"""Pengaturan Django untuk layanan FRISTA.

Semua nilai rahasia dibaca dari environment / file .env (lihat .env.example).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [x.strip() for x in str(env(name, default)).split(",") if x.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env_bool("DJANGO_DEBUG", False)
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY wajib diisi di .env")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "frista",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

# Tidak butuh database: token & sesi disimpan di cache.
DATABASES = {}

# Cache dipakai bersama oleh semua worker gunicorn. Untuk beberapa server,
# ganti ke Redis: CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
# dan CACHE_LOCATION=redis://127.0.0.1:6379/1
CACHES = {
    "default": {
        "BACKEND": env("CACHE_BACKEND", "django.core.cache.backends.filebased.FileBasedCache"),
        "LOCATION": env("CACHE_LOCATION", str(BASE_DIR / ".cache")),
    }
}

LANGUAGE_CODE = "id"
TIME_ZONE = env("TIME_ZONE", "Asia/Makassar")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Di belakang reverse proxy HTTPS (nginx/caddy)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("USE_X_FORWARDED_HOST", True)

# Halaman kamera boleh di-embed dalam iframe hanya dari origin yang sama
X_FRAME_OPTIONS = "SAMEORIGIN"

# ---------------------------------------------------------------- FRISTA
FRISTA = {
    "USERNAME": env("FRISTA_USERNAME", ""),       # username VClaim
    "PASSWORD": env("FRISTA_PASSWORD", ""),       # password VClaim
    "BASE_URL": env("FRISTA_BASE_URL", "https://frista.bpjs-kesehatan.go.id"),
    "VERSION": env("FRISTA_VERSION", "3.0.2"),
    "VERIFY_SSL": env_bool("FRISTA_VERIFY_SSL", True),
    "TIMEOUT": float(env("FRISTA_TIMEOUT", "30")),
}

# Kunci untuk APM (boleh ada di APK; hanya bisa membuat sesi & membaca hasil)
APM_API_KEY = env("APM_API_KEY", "")
# Kunci petugas/SIMRS (JANGAN dimasukkan ke APK; bisa registrasi wajah)
ADMIN_API_KEY = env("ADMIN_API_KEY", "")

CAPTURE_SESSION_TTL = int(env("CAPTURE_SESSION_TTL", "180"))   # detik
FRISTA_MAX_ATTEMPTS = int(env("FRISTA_MAX_ATTEMPTS", "3"))
# Halaman kamera: "manual" = tombol Ambil Gambar (seperti aplikasi FRISTA Windows),
# "auto" = foto diambil otomatis saat wajah terdeteksi stabil.
CAPTURE_MODE = env("CAPTURE_MODE", "manual")
# Kamera yang dibuka pertama kali: "environment" (belakang) atau "user" (depan)
CAPTURE_CAMERA = env("CAPTURE_CAMERA", "environment")

# face-api.js 0.22.2 (MIT) dan model wajahnya sudah disertakan di static/faceapi,
# jadi APM tidak bergantung pada CDN/internet untuk deteksi wajah.
FACEAPI_SCRIPT_URL = env("FACEAPI_SCRIPT_URL", STATIC_URL + "faceapi/face-api.min.js")
FACEAPI_MODEL_URL = env("FACEAPI_MODEL_URL", STATIC_URL + "faceapi/models")
FACEAPI_MIN_SCORE = float(env("FACEAPI_MIN_SCORE", "0.85"))
# Detektor wajah: "tiny" (model 190 KB, cepat di tablet) atau "ssd" (5,6 MB, lebih berat)
FACEAPI_DETECTOR = env("FACEAPI_DETECTOR", "tiny")
FACEAPI_STABLE_FRAMES = int(env("FACEAPI_STABLE_FRAMES", "3"))


def _static_cache_headers(headers, path, url):
    # Model face-api (~12 MB) tidak pernah berubah untuk versi yang sama: simpan lama
    # di cache tablet supaya halaman kamera tidak memuat/menanyakan ulang ke server.
    if url.startswith(STATIC_URL + "faceapi/"):
        headers["Cache-Control"] = "public, max-age=31536000, immutable"


WHITENOISE_ADD_HEADERS_FUNCTION = _static_cache_headers

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "std"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": env("LOG_FILE", str(BASE_DIR / "frista.log")),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "std",
        },
    },
    "loggers": {"frista": {"handlers": ["console", "file"], "level": env("LOG_LEVEL", "INFO")}},
}
