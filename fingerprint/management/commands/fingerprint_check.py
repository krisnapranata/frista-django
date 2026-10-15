"""Uji koneksi & login ke server sidik jari BPJS:  python manage.py fingerprint_check"""

from django.core.management.base import BaseCommand, CommandError

from frista.client import FristaUnreachable
from fingerprint.services import get_client


class Command(BaseCommand):
    help = "Uji login ke server sidik jari BPJS (After) dengan akun VClaim"

    def handle(self, *args, **opts):
        client = get_client()
        try:
            token = client.get_token(force=True)
        except FristaUnreachable as exc:
            raise CommandError(f"Server sidik jari BPJS tidak dapat dihubungi: {exc}")
        if not token:
            raise CommandError("Login gagal. Periksa FINGERPRINT_USERNAME/PASSWORD "
                               "(default: FRISTA_USERNAME/PASSWORD, akun VClaim).")
        self.stdout.write(self.style.SUCCESS("Login sidik jari BPJS berhasil, token tersimpan di cache."))
