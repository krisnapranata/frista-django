"""Endpoint HTTP sidik jari BPJS untuk PC loket (web) dan APM Android.

    POST /api/fingerprint/verify    X-APM-Key atau X-Admin-Key
    POST /api/fingerprint/register  X-Admin-Key (BELUM TERUJI ke server BPJS)
    POST /api/fingerprint/reset     X-Admin-Key
    GET  /fingerprint/              halaman capture DigitalPersona untuk petugas loket

Setiap aksi dicatat di log transaksi (logger "fingerprint.transaksi") TANPA
minutiae maupun gambar sidik jari.
"""

import functools
import hmac
import json
import logging
import time

from django.conf import settings
from django.shortcuts import render
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from frista.views import _body, _err, _key_required, _mask

from .services import get_client

trx_log = logging.getLogger("fingerprint.transaksi")

PETUGAS_MAX = 60


def _any_key_required(view):
    """Terima X-APM-Key ATAU X-Admin-Key (verify dipakai APM dan PC loket)."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        pairs = [(settings.APM_API_KEY, request.META.get("HTTP_X_APM_KEY", "")),
                 (settings.ADMIN_API_KEY, request.META.get("HTTP_X_ADMIN_KEY", ""))]
        if not any(expected for expected, _ in pairs):
            return _err("CONFIG_ERROR", "APM_API_KEY / ADMIN_API_KEY belum diatur di server", 500)
        if any(expected and given and hmac.compare_digest(given.encode(), expected.encode())
               for expected, given in pairs):
            return view(request, *args, **kwargs)
        return _err("UNAUTHORIZED", "Kunci API tidak valid", 401)

    return wrapper


def _petugas(body):
    return " ".join(str(body.get("petugas") or "").split())[:PETUGAS_MAX] or None


def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")


def _catat(request, aksi, ident, result, petugas, started):
    """Satu baris JSON per transaksi. Field disusun eksplisit: biometrik tidak ikut."""
    trx_log.info(json.dumps({
        "waktu": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "aksi": aksi,
        "id": _mask(ident),
        "status": result.get("status"),
        "http": result.get("http"),
        "bpjs_code": result.get("bpjs_code"),
        "bpjs_message": result.get("bpjs_message"),
        "petugas": petugas,
        "ip": _client_ip(request),
        "durasi": round(time.monotonic() - started, 2),
    }, ensure_ascii=False))


def _respond(request, aksi, ident, body, call):
    started = time.monotonic()
    result = call()
    _catat(request, aksi, ident, result, _petugas(body), started)
    return JsonResponse(result)


@csrf_exempt
@require_POST
@_any_key_required
def verify(request):
    body = _body(request)
    if body is None:
        return _err("BAD_REQUEST", "Body harus JSON")
    ident = str(body.get("id", "")).strip()
    return _respond(request, "verify", ident, body,
                    lambda: get_client().verify(ident, body.get("finger")))


@csrf_exempt
@require_POST
@_key_required("ADMIN_API_KEY")
def register(request):
    body = _body(request)
    if body is None:
        return _err("BAD_REQUEST", "Body harus JSON")
    noka = str(body.get("nokartu", "")).strip()
    return _respond(request, "register", noka, body, lambda: get_client().register(
        noka, body.get("finger_kanan"), body.get("img_kanan"),
        body.get("finger_kiri"), body.get("img_kiri")))


@csrf_exempt
@require_POST
@_key_required("ADMIN_API_KEY")
def reset(request):
    body = _body(request)
    if body is None:
        return _err("BAD_REQUEST", "Body harus JSON")
    noka = str(body.get("nokartu", "")).strip()
    return _respond(request, "reset", noka, body,
                    lambda: get_client().reset(noka, body.get("alasan")))


@never_cache
@require_GET
def capture(request):
    return render(request, "fingerprint/capture.html", {
        "cfg": {"verifyUrl": reverse("fingerprint:verify")},
    })
