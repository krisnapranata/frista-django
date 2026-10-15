"""Uji endpoint sidik jari dengan klien BPJS tiruan.

Jalankan:  python manage.py test fingerprint
"""

import json
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from .status import StatusCode as S

NIK = "3212345678901234"
NOKA = "0001234567890"
MINUTIAE = "AOhPAcgp43NcwEE381mK69RAHASIAMINUTIAE"
IMG = "/9j/RAHASIAGAMBAR"
APM = {"HTTP_X_APM_KEY": "apm-key"}
ADMIN = {"HTTP_X_ADMIN_KEY": "admin-key"}


@override_settings(APM_API_KEY="apm-key", ADMIN_API_KEY="admin-key", ALLOWED_HOSTS=["testserver"])
class FingerprintApiTests(SimpleTestCase):
    def setUp(self):
        self.fp = mock.Mock()
        patcher = mock.patch("fingerprint.views.get_client", return_value=self.fp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, name, data, **headers):
        body = data if isinstance(data, str) else json.dumps(data)
        return self.client.post(reverse(name), body, content_type="application/json", **headers)

    def test_verify_accepts_apm_or_admin_key(self):
        self.fp.verify.return_value = {"status": S.OK, "message": "ok", "http": 200}
        for headers in (APM, ADMIN):
            r = self.post("fingerprint:verify", {"id": NIK, "finger": MINUTIAE}, **headers)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], S.OK)
        self.fp.verify.assert_called_with(NIK, MINUTIAE)

    def test_verify_rejects_missing_or_wrong_key(self):
        self.assertEqual(self.post("fingerprint:verify", {"id": NIK}).status_code, 401)
        r = self.post("fingerprint:verify", {"id": NIK}, HTTP_X_APM_KEY="salah", HTTP_X_ADMIN_KEY="")
        self.assertEqual(r.status_code, 401)
        self.fp.verify.assert_not_called()

    def test_register_and_reset_need_admin_key(self):
        for name in ("fingerprint:register", "fingerprint:reset"):
            self.assertEqual(self.post(name, {"nokartu": NOKA}, **APM).status_code, 401, name)
        self.fp.register.assert_not_called()
        self.fp.reset.assert_not_called()

    def test_register_and_reset_pass_fields(self):
        self.fp.register.return_value = {"status": S.OK, "message": "ok"}
        self.fp.reset.return_value = {"status": S.OK, "message": "ok"}
        self.post("fingerprint:register", {"nokartu": NOKA, "finger_kanan": "a", "img_kanan": "b",
                                           "finger_kiri": "c", "img_kiri": "d"}, **ADMIN)
        self.fp.register.assert_called_once_with(NOKA, "a", "b", "c", "d")
        self.post("fingerprint:reset", {"nokartu": NOKA, "alasan": "02"}, **ADMIN)
        self.fp.reset.assert_called_once_with(NOKA, "02")

    def test_bad_json(self):
        r = self.post("fingerprint:verify", "{bukan json", **APM)
        self.assertEqual((r.status_code, r.json()["status"]), (400, "BAD_REQUEST"))

    def test_transaction_log_has_no_biometrics(self):
        self.fp.register.return_value = {"status": S.INTEGRATION_ERROR, "message": "x", "http": 400,
                                         "bpjs_code": "201", "bpjs_message": "Gagal"}
        with self.assertLogs("fingerprint.transaksi", "INFO") as logs:
            self.post("fingerprint:register", {"nokartu": NOKA, "finger_kanan": MINUTIAE, "img_kanan": IMG,
                                               "finger_kiri": MINUTIAE, "img_kiri": IMG,
                                               "petugas": "  Budi   Loket 1 "}, **ADMIN)
        line = logs.records[0].getMessage()
        entry = json.loads(line)
        self.assertEqual(entry["aksi"], "register")
        self.assertEqual(entry["id"], "*********7890")
        self.assertEqual(entry["petugas"], "Budi Loket 1")
        self.assertEqual((entry["status"], entry["bpjs_code"]), (S.INTEGRATION_ERROR, "201"))
        for secret in (MINUTIAE, IMG, "RAHASIA", NOKA):
            self.assertNotIn(secret, line)

    def test_capture_page_renders(self):
        r = self.client.get(reverse("fingerprint:capture"))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("fp-cfg", html)
        self.assertIn("@digitalpersona/fingerprint@1.0.0", html)
        self.assertIn(reverse("fingerprint:verify"), html)
