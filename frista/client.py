"""Klien Python untuk API FRISTA (Face Recognition BPJS Kesehatan).

Modul ini sengaja TIDAK bergantung pada Django, supaya bisa dipakai ulang
(Flask, FastAPI, skrip) dan diuji tanpa framework.

Logika endpoint dan pemetaan respons diporting dari library PHP-Frista
(https://github.com/banguncode/PHP-Frista, lisensi MIT).

PERINGATAN: API ini bukan API publik resmi BPJS Kesehatan. Endpoint, format,
dan kebijakannya bisa berubah sewaktu-waktu. Konfirmasikan penggunaannya ke
BPJS Kesehatan cabang sebelum dipakai untuk layanan pasien.

Endpoint yang dipakai:
    POST /frista-api/user/login/rs      login, mendapat bearer token
    GET  /frista-api/user/session       cek token masih aktif
    POST /frista-api/face/recognition2  identifikasi dari encoding saja
    POST /frista-api/face/nik2          cari data biometrik berdasarkan NIK
    POST /frista-api/face/match2        verifikasi ID + encoding
    POST /frista-api/face/upload        registrasi wajah dari JPEG
    POST /frista-api/face/upload2       registrasi wajah dari encoding
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence

import requests

from .status import StatusCode as S

log = logging.getLogger("frista")

DEFAULT_BASE_URL = "https://frista.bpjs-kesehatan.go.id"
DEFAULT_VERSION = "3.0.2"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
ID_RE = re.compile(r"^(\d{13}|\d{16})$")
NIK_RE = re.compile(r"^\d{16}$")
JPEG_MAGIC = b"\xff\xd8\xff"

TOKEN_KEY = "frista:token"
CHECKED_AT_KEY = "frista:token_checked_at"


class TokenStore(Protocol):
    """Penyimpanan token bersama. Di Django dipakai cache framework."""

    def get(self, key: str) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def delete(self, key: str) -> None: ...


class MemoryTokenStore:
    """Penyimpanan token di memori proses (cocok untuk skrip/uji)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class FristaUnreachable(Exception):
    """Server BPJS tidak bisa dihubungi (timeout, DNS, koneksi putus)."""


@dataclass
class FristaConfig:
    username: str
    password: str
    base_url: str = DEFAULT_BASE_URL
    version: str = DEFAULT_VERSION
    verify_ssl: bool = True
    timeout: float = 30.0
    # Token dicek ulang ke server paling sering tiap N detik
    session_check_interval: float = 60.0
    # Batas waktu TOTAL satu verifikasi (cek sesi + login + match2 + nik2). Harus di
    # bawah batas gunicorn/proxy (60 detik), supaya APM tetap menerima jawaban JSON
    # dan bukan halaman error 502/504.
    request_budget: float = 45.0
    extra_headers: dict[str, str] = field(default_factory=dict)


def _result(status: str, message: str, data: Any = None) -> dict:
    out = {"status": status, "message": message}
    if data is not None:
        out["data"] = data
    return out


def validate_id(value: Any) -> bool:
    return bool(ID_RE.match(str(value or "")))


def validate_encoding(encoding: Any) -> Optional[list[float]]:
    """Kembalikan list 128 float, atau None jika tidak valid."""
    if not isinstance(encoding, (list, tuple)) or len(encoding) != 128:
        return None
    out = []
    for v in encoding:
        if isinstance(v, bool):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
            return None
        out.append(f)
    return out


def decode_jpeg(data: Any) -> Optional[bytes]:
    """Terima bytes JPEG atau string base64 (boleh dengan prefix data URI)."""
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    elif isinstance(data, str):
        b64 = re.sub(r"^data:image/[\w.+-]+;base64,", "", data.strip(), flags=re.I)
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            return None
    else:
        return None
    return raw if raw.startswith(JPEG_MAGIC) else None


class FristaClient:
    def __init__(
        self,
        config: FristaConfig,
        store: Optional[TokenStore] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.cfg = config
        self.base_url = config.base_url.rstrip("/")
        self.store = store or MemoryTokenStore()
        self.http = session or requests.Session()
        self.http.headers.update(
            {"Accept": "*/*", "User-Agent": USER_AGENT, **config.extra_headers}
        )
        self._login_lock = threading.Lock()
        self._local = threading.local()   # batas waktu per thread (lihat _budget)

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
    def _request(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        json: Any = None,
        data: Any = None,
        files: Any = None,
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        timeout = self.cfg.timeout
        deadline = getattr(self._local, "deadline", None)
        if deadline is not None:
            left = deadline - time.monotonic()
            if left < 1:
                log.warning("FRISTA %s %s dilewati: batas waktu total habis", method, path)
                raise FristaUnreachable("batas waktu total habis")
            timeout = min(timeout, left)
        started = time.monotonic()
        try:
            resp = self.http.request(
                method,
                self.base_url + path,
                headers=headers,
                json=json,
                data=data,
                files=files,
                timeout=timeout,
                verify=self.cfg.verify_ssl,
            )
        except requests.RequestException as exc:
            log.warning("FRISTA %s %s gagal setelah %.1f detik: %s",
                        method, path, time.monotonic() - started, exc)
            raise FristaUnreachable(str(exc)) from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        log.info("FRISTA %s %s -> HTTP %s (%.1f detik)",
                 method, path, resp.status_code, time.monotonic() - started)
        return resp.status_code, body

    # ------------------------------------------------------------- Token
    def login(self) -> Optional[str]:
        code, body = self._request(
            "POST",
            "/frista-api/user/login/rs",
            json={
                "username": self.cfg.username,
                "password": self.cfg.password,
                "version": self.cfg.version,
            },
        )
        token = body.get("token") if code == 200 and isinstance(body, dict) else None
        if not token:
            log.error("Login FRISTA gagal (HTTP %s)", code)
            self.store.delete(TOKEN_KEY)
            return None
        self.store.set(TOKEN_KEY, token)
        self.store.set(CHECKED_AT_KEY, time.time())
        return token

    def check_session(self, token: Optional[str] = None) -> Optional[bool]:
        token = token or self.store.get(TOKEN_KEY)
        if not token:
            return None
        code, body = self._request("GET", "/frista-api/user/session", token=token)
        return code == 200 and isinstance(body, dict) and body.get("status") is True

    def get_token(self, force: bool = False) -> Optional[str]:
        """Ambil token dari cache; login ulang jika kosong/kedaluwarsa."""
        with self._login_lock:
            token = None if force else self.store.get(TOKEN_KEY)
            if token:
                last = self.store.get(CHECKED_AT_KEY) or 0
                if time.time() - float(last) < self.cfg.session_check_interval:
                    return token
                if self.check_session(token):
                    self.store.set(CHECKED_AT_KEY, time.time())
                    return token
                self.store.delete(TOKEN_KEY)
            return self.login()

    def _authed(self, method: str, path: str, **kw) -> tuple[int, Any]:
        """Request dengan token; jika 401 login ulang sekali lalu coba lagi."""
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
    def verify(self, member_id: str, encoding: Sequence[float]) -> dict:
        """Verifikasi wajah peserta (NIK 16 digit / no. kartu 13 digit)."""
        with self._budget():
            return self._verify(member_id, encoding)

    def _verify(self, member_id: str, encoding: Sequence[float]) -> dict:
        if not validate_id(member_id):
            return _result(S.INVALID_ID, "Nomor identitas harus 13 atau 16 digit angka")
        enc = validate_encoding(encoding)
        if enc is None:
            return _result(S.INVALID_ENCODING, "Encoding harus berupa array berisi 128 angka desimal")

        try:
            code, body = self._authed(
                "POST", "/frista-api/face/match2", json={"id": str(member_id), "encoding": enc}
            )
        except FristaUnreachable:
            return _result(S.SERVER_UNREACHABLE, "Server BPJS tidak dapat dihubungi")

        if code == 401:
            return _result(S.AUTH_FAILED, "Gagal autentikasi ke server BPJS")
        if code == 400:
            return _result(S.INTEGRATION_ERROR, "Permintaan tidak valid, periksa payload yang dikirim")
        result = map_match_response(body)
        if result["status"] == S.OK and isinstance(result.get("data"), dict):
            self._fill_nokartu(member_id, result["data"])
        elif result["status"] == S.ALREADY_REGISTERED:
            # BPJS tidak mengirim data apa pun; APM tetap butuh nokartu untuk SEP
            data = {}
            self._fill_nokartu(member_id, data, force=True)
            if data:
                result["data"] = data
        return result

    def fill_nokartu(self, member_id: str, data: dict) -> None:
        """Lengkapi nokartu yang tersamar/kosong lewat nik2 (dipakai ulang oleh APM)."""
        with self._budget():
            self._fill_nokartu(member_id, data, force=True)

    def _fill_nokartu(self, member_id: str, data: dict, force: bool = False) -> None:
        """match2 mengembalikan nokartu tersamar (000*******322); lengkapi untuk SEP."""
        if not force and "*" not in str(data.get("nokartu") or ""):
            return
        member_id = str(member_id)
        if len(member_id) == 13:
            data["nokartu"] = member_id
            return
        nik = str(data.get("nik") or member_id)
        if not NIK_RE.match(nik):
            return
        found = self.nik(nik)
        info = found.get("data") or {}
        nokartu = str(info.get("nokartu") or "")
        if found["status"] == S.OK and len(nokartu) == 13 and nokartu.isdigit():
            data["nokartu"] = nokartu
            data.setdefault("nik", nik)
            if info.get("nama"):
                data.setdefault("nama", info["nama"])
        else:
            log.warning("Nokartu lengkap tidak didapat dari nik2: status=%s", found["status"])

    def nik(self, nik: str) -> dict:
        if not NIK_RE.match(str(nik or "")):
            return _result(S.INVALID_ID, "NIK harus 16 digit angka")
        return self._raw("POST", "/frista-api/face/nik2", json={"nik": str(nik)})

    def recognition(self, encoding: Sequence[float]) -> dict:
        enc = validate_encoding(encoding)
        if enc is None:
            return _result(S.INVALID_ENCODING, "Encoding harus berupa array berisi 128 angka desimal")
        return self._raw("POST", "/frista-api/face/recognition2", json={"encoding": enc})

    def register_image(self, member_id: str, image: Any) -> dict:
        """Registrasi wajah pertama kali dari JPEG (bytes atau base64).

        Panggil hanya jika verify() mengembalikan UNREGISTERED, dan sebaiknya
        dilakukan oleh petugas, bukan dari APM tanpa pengawasan.
        """
        if not validate_id(member_id):
            return _result(S.INVALID_ID, "Nomor identitas harus 13 atau 16 digit angka")
        jpeg = decode_jpeg(image)
        if jpeg is None:
            return _result(S.INVALID_IMAGE, "Gambar harus berupa JPEG yang valid")
        try:
            code, body = self._authed(
                "POST",
                "/frista-api/face/upload",
                data={"id": str(member_id)},
                files={"file": ("photo.jpg", jpeg, "image/jpeg")},
            )
        except FristaUnreachable:
            return _result(S.SERVER_UNREACHABLE, "Server BPJS tidak dapat dihubungi")
        if code == 401:
            return _result(S.AUTH_FAILED, "Gagal autentikasi ke server BPJS")
        if not isinstance(body, dict) or "status" not in body:
            return _result(S.INTERNAL_SERVER_ERROR, "Respons dari server BPJS tidak dapat dibaca")
        if body.get("status"):
            return _result(S.OK, "Pendaftaran biometrik berhasil")
        return _result(S.INTEGRATION_ERROR, body.get("message") or "Gagal terhubung ke server BPJS")

    def register_encoding(self, member_id: str, encoding: Sequence[float], kanal: str = "fktl") -> dict:
        """Registrasi wajah dari encoding. kanal: 'fktl' (RS) atau 'fktp' (klinik/puskesmas)."""
        if not validate_id(member_id):
            return _result(S.INVALID_ID, "Nomor identitas harus 13 atau 16 digit angka")
        enc = validate_encoding(encoding)
        if enc is None:
            return _result(S.INVALID_ENCODING, "Encoding harus berupa array berisi 128 angka desimal")
        if kanal not in ("fktl", "fktp"):
            return _result(S.INTEGRATION_ERROR, "kanal harus 'fktl' atau 'fktp'")
        return self._raw(
            "POST", "/frista-api/face/upload2",
            json={"id": str(member_id), "kanal": kanal, "encoding": enc},
        )

    def _raw(self, method: str, path: str, **kw) -> dict:
        """Teruskan respons mentah BPJS, dibungkus status standar."""
        try:
            code, body = self._authed(method, path, **kw)
        except FristaUnreachable:
            return _result(S.SERVER_UNREACHABLE, "Server BPJS tidak dapat dihubungi")
        if code == 401:
            return _result(S.AUTH_FAILED, "Gagal autentikasi ke server BPJS")
        if not isinstance(body, dict):
            return _result(S.INTERNAL_SERVER_ERROR, "Respons dari server BPJS tidak dapat dibaca")
        status = S.OK if body.get("status") else S.INTEGRATION_ERROR
        data = body.get("data")
        if data is None:
            # nik2 mengirim nama/nik/nokartu di level atas, bukan di "data"
            extra = {k: v for k, v in body.items() if k not in ("status", "message", "code")}
            data = extra or None
        return _result(status, body.get("message") or "", data)


def map_match_response(body: Any) -> dict:
    """Petakan respons /face/match2 ke StatusCode (sesuai PHP-Frista)."""
    if not isinstance(body, dict) or "status" not in body:
        return _result(S.INTERNAL_SERVER_ERROR, "Respons dari server BPJS tidak dapat dibaca")

    message = str(body.get("message") or "")
    msg = message.lower()
    code = body.get("code")
    data = body.get("data")

    if body.get("status"):
        return _result(S.OK, "Pengenalan wajah berhasil", data)
    if "terdaftar hari ini" in msg:
        return _result(S.ALREADY_REGISTERED, "Peserta telah terdaftar hari ini")
    if "tidak dapat diproses" in msg:
        return _result(S.FACE_UNPROCESSABLE, "Wajah tidak dapat diproses, pastikan kualitas gambar memadai")
    if "foto yang identik" in msg:
        return _result(S.DUPLICATE_PHOTO, message)
    if "tidak sesuai" in msg or "verifikasi wajah gagal" in msg:
        return _result(S.FACE_MISMATCH, message, data)
    if code is not None and str(code) == "0":
        return _result(S.UNREGISTERED, "Peserta belum terdaftar biometrik, silakan hubungi petugas")
    return _result(S.INTEGRATION_ERROR, message or "Terjadi kesalahan pada integrasi BPJS")
