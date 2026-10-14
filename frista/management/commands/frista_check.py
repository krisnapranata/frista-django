"""Uji koneksi & login ke FRISTA:  python manage.py frista_check [--nik 16digit]"""

import json

from django.core.management.base import BaseCommand, CommandError

from frista.client import FristaUnreachable
from frista.services import get_client


class Command(BaseCommand):
    help = "Uji login ke server FRISTA BPJS dan (opsional) cari data biometrik per NIK"

    def add_arguments(self, parser):
        parser.add_argument("--nik", help="NIK 16 digit untuk dicek di FRISTA")

    def handle(self, *args, **opts):
        client = get_client()
        try:
            token = client.get_token(force=True)
        except FristaUnreachable as exc:
            raise CommandError(f"Server FRISTA tidak dapat dihubungi: {exc}")
        if not token:
            raise CommandError("Login gagal. Periksa FRISTA_USERNAME / FRISTA_PASSWORD (akun VClaim).")
        self.stdout.write(self.style.SUCCESS("Login FRISTA berhasil, token tersimpan di cache."))
        if opts.get("nik"):
            self.stdout.write(json.dumps(client.nik(opts["nik"]), indent=2, ensure_ascii=False))
