"""Klien Python untuk API Sidik Jari BPJS Kesehatan ("After").

Porting dari PHP-After (https://github.com/banguncode/PHP-After, MIT). Seperti
frista/client.py, modul ini TIDAK bergantung pada Django.

Endpoint (tidak resmi, tanpa dokumentasi BPJS):
    POST   /finger-rest/v2/user/login                       login akun VClaim
    POST   /finger-rest/v1/finger/verify                    verifikasi sidik jari
    POST   /finger-rest/v1/finger/addpeserta                daftar 2 jari (BELUM TERUJI)
    DELETE /finger-rest/v1/finger/delpeserta/<noka>/<01|02> hapus sidik jari peserta

Minutiae dan gambar sidik jari adalah data biometrik: JANGAN pernah ditulis ke log.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import requests

from frista.client import ID_RE, FristaUnreachable, MemoryTokenStore, TokenStore

from .status import StatusCode as S

log = logging.getLogger("fingerprint")

DEFAULT_BASE_URL = "https://fp.bpjs-kesehatan.go.id"
NOKA_RE = re.compile(r"^\d{13}$")
NIK_RE = re.compile(r"^\d{16}$")
B64_RE = re.compile(r"^[A-Za-z0-9+/\-_]+={0,2}$")
DATA_URI_RE = re.compile(r"^data:image/\w+;base64,", re.I)
RESET_REASONS = {
    "01": "Enrollment (pembacaan) sidik jari",
    "02": "Fisik sidik jari rusak/cacat",
}
# Posisi jari saat pendaftaran, sesuai koleksi Postman PHP-After
INDEX_KANAN, INDEX_KIRI = 1, 6

TOKEN_KEY = "after:token"
TOKEN_DATE_KEY = "after:token_date"

JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}


@dataclass
class AfterConfig:
    username: str
    password: str
    base_url: str = DEFAULT_BASE_URL
    verify_ssl: bool = True
    timeout: float = 30.0
    # Batas waktu TOTAL satu aksi (login + request + login ulang). Harus di bawah
    # batas gunicorn/proxy (60 detik), sama seperti FRISTA.
    request_budget: float = 45.0


def _result(status: str, message: str, **extra: Any) -> dict:
    out = {"status": status, "message": message}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def normalize_minutiae(value: Any) -> Optional[str]:
    """Minutiae Base64 dari DigitalPersona (boleh base64url). None jika tidak valid.

    Seperti example/index.html PHP-After: '-' → '+', '_' → '/', padding tidak ditambah.
    TODO: belum diketahui apakah server BPJS butuh padding '='; ikuti contoh dulu.
    """
    s = str(value or "").strip()
    if not s or not B64_RE.match(s):
        return None
    s = s.replace("-", "+").replace("_", "/")
    try:
        raw = base64.b64decode(s + "=" * (-len(s) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None
    return s if raw else None


def normalize_image(value: Any) -> Optional[str]:
    """Gambar sidik jari Base64 JPEG/PNG, tanpa awalan data-URI. None jika tidak valid."""
    s = DATA_URI_RE.sub("", str(value or "").strip())
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None
    is_jpeg = raw[:2] == b"\xff\xd8" and raw[-2:] == b"\xff\xd9"
    is_png = raw[:8] == b"\x89PNG\r\n\x1a\n"
    return s if (is_jpeg or is_png) else None


class AfterClient:
    def __init__(
        self,
        config: AfterConfig,
        store: Optional[TokenStore] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.cfg = config
        self.base_url = config.base_url.rstrip("/")
        self.store = store or MemoryTokenStore()
        self.http = session or requests.Session()
        self._login_lock = threading.Lock()
        self._local = threading.local()

    @contextmanager
    def _budget(self):
        """Semua request BPJS di dalam blok ini berbagi satu batas waktu total."""
        outer = getattr(self._local, "deadline", None)
        if outer is None:
            self._local.deadline = time.monotonic() + self.cfg.request_budget
        try:
            yield
        finally:
            if outer is None:
                self._local.deadline = None

    # ------------------------------------------------------------------ HTTP
    def _request(self, method: str, path: str, *, token: Optional[str] = None,
                 json: Any = None) -> tuple[int, Any]:
        headers = dict(JSON_HEADERS)
        if token:
            headers["token"] = token
        timeout = self.cfg.timeout
        deadline = getattr(self._local, "deadline", None)
        if deadline is not None:
            left = deadline - time.monotonic()
            if left < 1:
                log.warning("After %s %s dilewati: batas waktu total habis", method, path)
                raise FristaUnreachable("batas waktu total habis")
            timeout = min(timeout, left)
        started = time.monotonic()
        try:
            resp = self.http.request(method, self.base_url + path, headers=headers, json=json,
                                     timeout=timeout, verify=self.cfg.verify_ssl)
        except requests.RequestException as exc:
            log.warning("After %s %s gagal setelah %.1f detik: %s",
                        method, self._safe_path(path), time.monotonic() - started, exc)
            raise FristaUnreachable(str(exc)) from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        log.info("After %s %s -> HTTP %s (%.1f detik)",
                 method, self._safe_path(path), resp.status_code, time.monotonic() - started)
        return resp.status_code, body

    @staticmethod
    def _safe_path(path: str) -> str:
        # Path reset memuat no. kartu: samarkan sebelum masuk log
        return re.sub(r"\d{9}(\d{4})", r"*********\1", path)

    # ------------------------------------------------------------- Token
    def login(self) -> Optional[str]:
        code, body = self._request("POST", "/finger-rest/v2/user/login",
                                   json={"username": self.cfg.username, "password": self.cfg.password})
        resp = body.get("response") if isinstance(body, dict) else None
        token = resp.get("tokenLogin") if code == 200 and isinstance(resp, dict) else None
        if not token:
            log.error("Login sidik jari BPJS gagal (HTTP %s)", code)
            self.store.delete(TOKEN_KEY)
            return None
        self.store.set(TOKEN_KEY, token)
        self.store.set(TOKEN_DATE_KEY, date.today().isoformat())
        return token

    def get_token(self, force: bool = False) -> Optional[str]:
        """Token dipakai ulang selama tanggalnya sama (seperti PHP-After)."""
        with self._login_lock:
            if not force:
                token = self.store.get(TOKEN_KEY)
                if token and self.store.get(TOKEN_DATE_KEY) == date.today().isoformat():
                    return token
            return self.login()

    def _authed(self, method: str, path: str, **kw) -> tuple[int, Any]:
        """Request dengan token; jika 401 login ulang sekali lalu coba lagi.

        TODO: PHP-After tidak menangani token kedaluwarsa. Diasumsikan server menjawab
        HTTP 401; kalau ternyata memakai kode lain, tambahkan di sini.
        """
        token = self.get_token()
        if not token:
            return 401, None
        code, body = self._request(method, path, token=token, **kw)
        if code == 401:
            token = self.get_token(force=True)
            if not token:
                return 401, None
            code, body = self._request(method, path, token=token, **kw)
        return code, body

    # ---------------------------------------------------------- Endpoint
    def verify(self, keyword: str, finger: Any) -> dict:
        """Verifikasi sidik jari (NIK 16 digit / no. kartu 13 digit + minutiae Base64)."""
        keyword = str(keyword or "").strip()
        if not ID_RE.match(keyword):
            return _result(S.INVALID_ID, "Nomor identitas harus 13 atau 16 digit angka")
        minutiae = normalize_minutiae(finger)
        if minutiae is None:
            return _result(S.INVALID_MINUTIAE, "Format sidik jari tidak valid")
        payload = {"keyword": keyword, "jnsFilter": "nik" if NIK_RE.match(keyword) else "psnoka",
                   "finger": minutiae}
        with self._budget():
            try:
                code, body = self._authed("POST", "/finger-rest/v1/finger/verify", json=payload)
            except FristaUnreachable:
                return _result(S.SERVER_UNREACHABLE, "Server BPJS tidak dapat dihubungi")
        return map_verify_response(code, body)

    def register(self, nokapst: str, finger_kanan: Any, img_kanan: Any,
                 finger_kiri: Any, img_kiri: Any) -> dict:
        """Daftarkan 2 sidik jari peserta. BELUM TERUJI ke server BPJS (sama seperti PHP-After)."""
        nokapst = str(nokapst or "").strip()
        if not NOKA_RE.match(nokapst):
            return _result(S.INVALID_PARTICIPANT, "Nomor peserta harus 13 digit angka")
        fingers = [normalize_minutiae(finger_kanan), normalize_minutiae(finger_kiri)]
        if None in fingers:
            return _result(S.INVALID_MINUTIAE, "Format sidik jari tidak valid")
        images = [normalize_image(img_kanan), normalize_image(img_kiri)]
        if None in images:
            return _result(S.INVALID_IMAGE, "Gambar sidik jari harus Base64 JPEG/PNG")
        payload = {"nokapst": nokapst, "finger": [
            {"finger": fingers[0], "index": INDEX_KANAN, "imgStr": images[0]},
            {"finger": fingers[1], "index": INDEX_KIRI, "imgStr": images[1]},
        ]}
        log.warning("Pendaftaran sidik jari dipanggil (fitur BELUM TERUJI ke server BPJS)")
        with self._budget():
            try:
                code, body = self._authed("POST", "/finger-rest/v1/finger/addpeserta", json=payload)
            except FristaUnreachable:
                return _result(S.SERVER_UNREACHABLE, "Server BPJS tidak dapat dihubungi")
        return map_simple_response(code, body, "Sidik jari berhasil didaftarkan",
                                   "Gagal mendaftarkan sidik jari")

    def reset(self, nokapst: str, alasan: str) -> dict:
        """Hapus sidik jari peserta. Alasan: 01 = enrollment ulang, 02 = fisik jari rusak/cacat."""
        nokapst = str(nokapst or "").strip()
        alasan = str(alasan or "").strip()
        if not NOKA_RE.match(nokapst):
            return _result(S.INVALID_PARTICIPANT, "Nomor peserta harus 13 digit angka")
        if alasan not in RESET_REASONS:
            return _result(S.INVALID_REASON, "Alasan harus 01 (enrollment ulang) atau 02 (fisik jari rusak)")
        with self._budget():
            try:
                code, body = self._authed("DELETE", f"/finger-rest/v1/finger/delpeserta/{nokapst}/{alasan}")
            except FristaUnreachable:
                return _result(S.SERVER_UNREACHABLE, "Server BPJS tidak dapat dihubungi")
        return map_simple_response(code, body, "Sidik jari berhasil dihapus", "Gagal menghapus sidik jari")


def _meta(body: Any) -> tuple[Optional[str], Optional[str]]:
    meta = body.get("metaData") if isinstance(body, dict) else None
    if not isinstance(meta, dict):
        return None, None
    code = meta.get("code")
    return (None if code is None else str(code)), (meta.get("message") or None)


def _data(body: Any) -> Optional[dict]:
    # TODO: isi body sukses belum diketahui; teruskan bagian "response" bila ada
    resp = body.get("response") if isinstance(body, dict) else None
    return resp if isinstance(resp, dict) else None


def map_verify_response(code: int, body: Any) -> dict:
    """Petakan respons /finger/verify ke StatusCode (sesuai PHP-After)."""
    bpjs_code, bpjs_message = _meta(body)
    extra = {"http": code, "bpjs_code": bpjs_code, "bpjs_message": bpjs_message}
    if code == 200:
        return _result(S.OK, "Sidik jari berhasil diverifikasi", data=_data(body), **extra)
    if code == 401:
        return _result(S.AUTH_FAILED, "Gagal autentikasi ke server BPJS", **extra)
    if bpjs_code is not None:
        mapped = {
            "1": (S.NOT_REGISTERED, "Nomor identitas tidak terdaftar sebagai peserta BPJS"),
            "2": (S.NOT_ENROLLED, "Peserta belum terdaftar sidik jari"),
            "3": (S.NOT_RECOGNIZED, "Sidik jari tidak dikenali"),
        }.get(bpjs_code.lstrip("0") or "0")
        if mapped:
            return _result(mapped[0], mapped[1], **extra)
        return _result(S.INTEGRATION_ERROR, bpjs_message or "Terjadi kesalahan pada integrasi BPJS", **extra)
    return _result(S.INTERNAL_SERVER_ERROR, "Respons dari server BPJS tidak dapat dibaca", **extra)


def map_simple_response(code: int, body: Any, ok_message: str, fail_message: str) -> dict:
    """Untuk register/reset: HTTP 200 = berhasil, selain itu pakai pesan metaData BPJS."""
    bpjs_code, bpjs_message = _meta(body)
    extra = {"http": code, "bpjs_code": bpjs_code, "bpjs_message": bpjs_message}
    if code == 200:
        return _result(S.OK, ok_message, data=_data(body), **extra)
    if code == 401:
        return _result(S.AUTH_FAILED, "Gagal autentikasi ke server BPJS", **extra)
    if bpjs_code is None and not isinstance(body, dict):
        return _result(S.INTERNAL_SERVER_ERROR, "Respons dari server BPJS tidak dapat dibaca", **extra)
    return _result(S.INTEGRATION_ERROR, bpjs_message or fail_message, **extra)
