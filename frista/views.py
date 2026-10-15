"""Endpoint HTTP untuk APM Android dan SIMRS.

Alur APM (tanpa kredensial BPJS di dalam APK):
  1. APM  -> POST /api/sessions {member_id}          (header X-APM-Key)
           <- {session_id, capture_url}
  2. APM membuka capture_url di WebView. Halaman kamera membuat encoding
     wajah (face-api.js) dan mengirimnya ke POST /capture/verify.
  3. Hasil dikirim ke APK lewat JavaScript bridge `AndroidFrista.onResult`,
     dan juga bisa diambil APM via GET /api/sessions/<session_id>.
"""

import functools
import hmac
import json
import logging
import secrets
import time

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .client import validate_id
from .services import get_client
from .status import StatusCode as S

log = logging.getLogger("frista")

SIGN_SALT = "frista.capture.session"


# ------------------------------------------------------------------ helpers
def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _err(status, message, http=400):
    return JsonResponse({"status": status, "message": message}, status=http)


def _key_required(setting_name):
    """Wajibkan header kunci API. Header: X-APM-Key atau X-Admin-Key."""
    header = "HTTP_X_APM_KEY" if setting_name == "APM_API_KEY" else "HTTP_X_ADMIN_KEY"

    def deco(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            expected = getattr(settings, setting_name, "")
            if not expected:
                return _err("CONFIG_ERROR", f"{setting_name} belum diatur di server", 500)
            given = request.META.get(header, "")
            if not hmac.compare_digest(given.encode(), expected.encode()):
                return _err("UNAUTHORIZED", "Kunci API tidak valid", 401)
            return view(request, *args, **kwargs)

        return wrapper

    return deco


def _sess_key(sid):
    return f"frista:sess:{sid}"


def _ttl():
    return int(settings.CAPTURE_SESSION_TTL)


def _mask(value, keep=4):
    s = str(value or "")
    return s if len(s) <= keep else "*" * (len(s) - keep) + s[-keep:]


def _full_nokartu(value):
    value = str(value or "")
    return len(value) == 13 and value.isdigit()


def _public_result(result):
    """Versi hasil untuk ditampilkan di layar kiosk (NIK & no. kartu disamarkan)."""
    out = {"status": result["status"], "message": result.get("message", "")}
    data = result.get("data")
    if result["status"] == S.OK and isinstance(data, dict):
        out["data"] = {
            "nama": data.get("nama"),
            "nik": _mask(data.get("nik")),
            "nokartu": _mask(data.get("nokartu")),
        }
    return out


# ------------------------------------------------------------------ health
@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


# ------------------------------------------------------- sesi untuk APM
@csrf_exempt
@require_POST
@_key_required("APM_API_KEY")
def create_session(request):
    body = _body(request)
    if body is None:
        return _err("BAD_REQUEST", "Body harus JSON")
    member_id = str(body.get("member_id", "")).strip()
    if not validate_id(member_id):
        return _err(S.INVALID_ID, "member_id harus NIK 16 digit atau no. kartu 13 digit")

    sid = secrets.token_urlsafe(16)
    cache.set(
        _sess_key(sid),
        {"member_id": member_id, "attempts": 0, "result": None},
        timeout=_ttl() + 600,  # hasil tetap bisa diambil 10 menit setelah sesi habis
    )
    token = signing.dumps({"sid": sid}, salt=SIGN_SALT)
    capture_url = request.build_absolute_uri(reverse("frista:capture")) + "?t=" + token
    log.info("Sesi FRISTA dibuat sid=%s id=%s", sid, _mask(member_id))
    return JsonResponse(
        {"status": "OK", "session_id": sid, "capture_url": capture_url, "expires_in": _ttl()}
    )


@require_GET
@_key_required("APM_API_KEY")
def session_result(request, sid):
    sess = cache.get(_sess_key(sid))
    if not sess:
        return _err("NOT_FOUND", "Sesi tidak ditemukan atau sudah kedaluwarsa", 404)
    if sess["result"] is None:
        return JsonResponse({"status": "PENDING", "attempts": sess["attempts"]})
    result = sess["result"]
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if result["status"] in S.PASSED and not _full_nokartu(data.get("nokartu")):
        # nik2 tadi lambat/gagal: coba lagi sekarang, APM butuh nokartu lengkap untuk SEP
        get_client().fill_nokartu(sess["member_id"], data)
        if _full_nokartu(data.get("nokartu")):
            result["data"] = data
            cache.set(_sess_key(sid), sess, timeout=_ttl() + 600)
    # Hasil lengkap (termasuk nokartu) untuk dipakai APM membuat SEP
    return JsonResponse(sess["result"])


# ------------------------------------------------------- halaman kamera
def _load_session(token):
    try:
        payload = signing.loads(token or "", salt=SIGN_SALT, max_age=_ttl())
    except signing.SignatureExpired:
        return None, None, "Sesi sudah kedaluwarsa, silakan ulangi dari awal."
    except signing.BadSignature:
        return None, None, "Tautan verifikasi tidak valid."
    sid = payload.get("sid")
    sess = cache.get(_sess_key(sid))
    if not sess:
        return None, None, "Sesi tidak ditemukan, silakan ulangi dari awal."
    return sid, sess, None


@never_cache
@require_GET
def capture(request):
    token = request.GET.get("t", "")
    sid, sess, error = _load_session(token)
    ctx = {
        "error": error,
        "token": token,
        "member_id_masked": _mask(sess["member_id"]) if sess else "",
        "already_done": bool(sess and sess["result"] and sess["result"]["status"] in S.PASSED),
        "cfg": {
            "verifyUrl": reverse("frista:capture_verify"),
            "modelUrl": settings.FACEAPI_MODEL_URL,
            "maxAttempts": settings.FRISTA_MAX_ATTEMPTS,
            "minScore": settings.FACEAPI_MIN_SCORE,
            "detector": settings.FACEAPI_DETECTOR,
            "stableFrames": settings.FACEAPI_STABLE_FRAMES,
            "mode": settings.CAPTURE_MODE,
            "camera": settings.CAPTURE_CAMERA,
        },
        "faceapi_script": settings.FACEAPI_SCRIPT_URL,
    }
    return render(request, "frista/capture.html", ctx, status=400 if error else 200)


@csrf_exempt
@require_POST
def capture_verify(request):
    body = _body(request)
    if body is None:
        return _err("BAD_REQUEST", "Body harus JSON")
    sid, sess, error = _load_session(body.get("t"))
    if error:
        return _err("SESSION_INVALID", error, 403)

    # Sesi yang sudah lolos tidak boleh dipakai ulang
    if sess["result"] and sess["result"]["status"] in S.PASSED:
        return JsonResponse(_public_result(sess["result"]))

    max_attempts = int(settings.FRISTA_MAX_ATTEMPTS)
    if sess["attempts"] >= max_attempts:
        return _err("TOO_MANY_ATTEMPTS", "Batas percobaan habis, silakan hubungi petugas", 429)

    sess["attempts"] += 1
    started = time.monotonic()
    result = get_client().verify(sess["member_id"], body.get("encoding"))
    elapsed = time.monotonic() - started
    result["attempt"] = sess["attempts"]
    result["max_attempts"] = max_attempts

    final = result["status"] in S.PASSED or result["status"] not in S.RETRYABLE \
        or sess["attempts"] >= max_attempts
    if final:
        sess["result"] = result
    cache.set(_sess_key(sid), sess, timeout=_ttl() + 600)

    log.info("Verifikasi sid=%s percobaan=%s status=%s (%.1f detik)",
             sid, sess["attempts"], result["status"], elapsed)
    public = _public_result(result)
    public.update(attempt=result["attempt"], max_attempts=max_attempts, final=final,
                  session_id=sid)
    return JsonResponse(public)


# ------------------------------------------------------- endpoint petugas
@csrf_exempt
@require_POST
@_key_required("ADMIN_API_KEY")
def admin_verify(request):
    body = _body(request)
    if body is None:
        return _err("BAD_REQUEST", "Body harus JSON")
    return JsonResponse(get_client().verify(str(body.get("member_id", "")), body.get("encoding")))


@csrf_exempt
@require_POST
@_key_required("ADMIN_API_KEY")
def admin_nik(request):
    body = _body(request)
    if body is None:
        return _err("BAD_REQUEST", "Body harus JSON")
    return JsonResponse(get_client().nik(str(body.get("nik", ""))))


@csrf_exempt
@require_POST
@_key_required("ADMIN_API_KEY")
def admin_register(request):
    """Registrasi wajah pertama kali. Kirim salah satu: image_base64 atau encoding."""
    body = _body(request)
    if body is None:
        return _err("BAD_REQUEST", "Body harus JSON")
    member_id = str(body.get("member_id", ""))
    client = get_client()
    if body.get("image_base64"):
        result = client.register_image(member_id, body["image_base64"])
    elif body.get("encoding") is not None:
        result = client.register_encoding(member_id, body["encoding"], body.get("kanal", "fktl"))
    else:
        return _err("BAD_REQUEST", "Kirim image_base64 atau encoding")
    log.info("Registrasi wajah id=%s status=%s", _mask(member_id), result["status"])
    return JsonResponse(result)
