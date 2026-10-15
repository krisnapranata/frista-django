"""Penghubung antara AfterClient (Python murni) dan konfigurasi Django."""

from django.conf import settings

from frista.services import DjangoCacheTokenStore

from .after_client import AfterClient, AfterConfig

_client = None


def get_client() -> AfterClient:
    """Satu instance klien per proses; token disimpan di cache bersama semua worker."""
    global _client
    if _client is None:
        cfg = settings.FINGERPRINT
        _client = AfterClient(
            AfterConfig(
                username=cfg["USERNAME"],
                password=cfg["PASSWORD"],
                base_url=cfg["BASE_URL"],
                verify_ssl=cfg["VERIFY_SSL"],
                timeout=cfg["TIMEOUT"],
                request_budget=cfg["REQUEST_BUDGET"],
            ),
            store=DjangoCacheTokenStore(),
        )
    return _client
