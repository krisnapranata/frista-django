"""Penghubung antara FristaClient (Python murni) dan konfigurasi Django."""

from django.conf import settings
from django.core.cache import cache

from .client import FristaClient, FristaConfig


class DjangoCacheTokenStore:
    """Simpan token FRISTA di cache Django supaya dipakai bersama semua worker."""

    def get(self, key):
        return cache.get(key)

    def set(self, key, value):
        cache.set(key, value, timeout=None)

    def delete(self, key):
        cache.delete(key)


_client = None


def get_client() -> FristaClient:
    """Satu instance klien per proses (koneksi HTTP dipakai ulang)."""
    global _client
    if _client is None:
        cfg = settings.FRISTA
        _client = FristaClient(
            FristaConfig(
                username=cfg["USERNAME"],
                password=cfg["PASSWORD"],
                base_url=cfg["BASE_URL"],
                version=cfg["VERSION"],
                verify_ssl=cfg["VERIFY_SSL"],
                timeout=cfg["TIMEOUT"],
            ),
            store=DjangoCacheTokenStore(),
        )
    return _client
