"""Uji alur APM end-to-end dengan klien BPJS tiruan.

Jalankan:  python manage.py test frista
"""

from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from .status import StatusCode as S

ENC = [0.01] * 128
NIK = "3212345678901234"
APM = {"HTTP_X_APM_KEY": "apm-key"}
ADMIN = {"HTTP_X_ADMIN_KEY": "admin-key"}


@override_settings(
    APM_API_KEY="apm-key",
    ADMIN_API_KEY="admin-key",
    CAPTURE_SESSION_TTL=180,
    FRISTA_MAX_ATTEMPTS=3,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    ALLOWED_HOSTS=["testserver"],
)
class ApmFlowTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.client_mock = mock.Mock()
        patcher = mock.patch("frista.views.get_client", return_value=self.client_mock)
        patcher.start()
        self.addCleanup(patcher.stop)

    # helpers
    def post(self, name, data, **headers):
        return self.client.post(reverse(name), data, content_type="application/json", **headers)

    def new_session(self):
        r = self.post("frista:create_session", {"member_id": NIK}, **APM)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        token = body["capture_url"].split("?t=", 1)[1]
        return body["session_id"], token

    # tests
    def test_health(self):
        self.assertEqual(self.client.get(reverse("frista:health")).json(), {"status": "ok"})

    def test_api_key_required(self):
        r = self.post("frista:create_session", {"member_id": NIK})
        self.assertEqual(r.status_code, 401)
        r = self.post("frista:create_session", {"member_id": NIK}, HTTP_X_APM_KEY="salah")
        self.assertEqual(r.status_code, 401)
        # Kunci APM tidak berlaku untuk endpoint petugas
        r = self.post("frista:admin_nik", {"nik": NIK}, HTTP_X_ADMIN_KEY="apm-key")
        self.assertEqual(r.status_code, 401)

    def test_invalid_member_id(self):
        r = self.post("frista:create_session", {"member_id": "123"}, **APM)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["status"], S.INVALID_ID)

    def test_capture_page_renders(self):
        _, token = self.new_session()
        r = self.client.get(reverse("frista:capture"), {"t": token})
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("************1234", html)   # NIK disamarkan
        self.assertNotIn(NIK, html)                # NIK lengkap tidak bocor ke halaman
        self.assertIn("faceapi/face-api.min.js", html)
        self.assertIn("frista-cfg", html)

    def test_capture_page_bad_token(self):
        r = self.client.get(reverse("frista:capture"), {"t": "palsu"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("tidak valid", r.content.decode())

    def test_success_flow(self):
        sid, token = self.new_session()
        self.assertEqual(self.client.get(reverse("frista:session_result", args=[sid]), **APM).json()["status"], "PENDING")

        self.client_mock.verify.return_value = {
            "status": S.OK, "message": "ok",
            "data": {"nama": "BUDI", "nik": NIK, "nokartu": "0001234567890", "score": 0.6},
        }
        r = self.post("frista:capture_verify", {"t": token, "encoding": ENC})
        body = r.json()
        self.assertEqual(body["status"], S.OK)
        self.assertTrue(body["final"])
        self.assertEqual(body["data"]["nama"], "BUDI")
        self.assertEqual(body["data"]["nokartu"], "*********7890")   # disamarkan untuk layar
        self.client_mock.verify.assert_called_once_with(NIK, ENC)

        # APM mendapat hasil lengkap untuk membuat SEP
        full = self.client.get(reverse("frista:session_result", args=[sid]), **APM).json()
        self.assertEqual(full["data"]["nokartu"], "0001234567890")

        # Sesi yang sudah lolos tidak memanggil BPJS lagi
        self.post("frista:capture_verify", {"t": token, "encoding": ENC})
        self.assertEqual(self.client_mock.verify.call_count, 1)

    def test_retry_then_limit(self):
        sid, token = self.new_session()
        self.client_mock.verify.return_value = {"status": S.FACE_MISMATCH, "message": "tidak sesuai"}
        finals = []
        for _ in range(3):
            finals.append(self.post("frista:capture_verify", {"t": token, "encoding": ENC}).json()["final"])
        self.assertEqual(finals, [False, False, True])

        r = self.post("frista:capture_verify", {"t": token, "encoding": ENC})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.client_mock.verify.call_count, 3)
        self.assertEqual(self.client.get(reverse("frista:session_result", args=[sid]), **APM).json()["status"],
                         S.FACE_MISMATCH)

    def test_bpjs_timeout_can_be_retried(self):
        _, token = self.new_session()
        self.client_mock.verify.return_value = {"status": S.SERVER_UNREACHABLE, "message": "timeout"}
        body = self.post("frista:capture_verify", {"t": token, "encoding": ENC}).json()
        self.assertEqual(body["status"], S.SERVER_UNREACHABLE)
        self.assertFalse(body["final"])

    def test_result_refills_masked_nokartu(self):
        # nik2 lambat saat verifikasi: nokartu dilengkapi saat APM mengambil hasil
        sid, token = self.new_session()
        self.client_mock.verify.return_value = {
            "status": S.OK, "message": "ok", "data": {"nik": NIK, "nokartu": "000*******890"},
        }
        self.client_mock.fill_nokartu.side_effect = lambda mid, data: data.update(nokartu="0001234567890")
        self.post("frista:capture_verify", {"t": token, "encoding": ENC})
        url = reverse("frista:session_result", args=[sid])
        self.assertEqual(self.client.get(url, **APM).json()["data"]["nokartu"], "0001234567890")
        self.client.get(url, **APM)
        self.assertEqual(self.client_mock.fill_nokartu.call_count, 1)   # disimpan, tidak diulang

    def test_unregistered_is_final(self):
        sid, token = self.new_session()
        self.client_mock.verify.return_value = {"status": S.UNREGISTERED, "message": "belum rekam"}
        body = self.post("frista:capture_verify", {"t": token, "encoding": ENC}).json()
        self.assertTrue(body["final"])
        self.assertEqual(self.client.get(reverse("frista:session_result", args=[sid]), **APM).json()["status"],
                         S.UNREGISTERED)

    def test_verify_rejects_forged_token(self):
        r = self.post("frista:capture_verify", {"t": "palsu", "encoding": ENC})
        self.assertEqual(r.status_code, 403)
        self.client_mock.verify.assert_not_called()

    def test_expired_token(self):
        _, token = self.new_session()
        with override_settings(CAPTURE_SESSION_TTL=-1):
            r = self.post("frista:capture_verify", {"t": token, "encoding": ENC})
        self.assertEqual(r.status_code, 403)
        self.assertIn("kedaluwarsa", r.json()["message"])

    def test_admin_endpoints(self):
        self.client_mock.nik.return_value = {"status": S.OK, "message": "", "data": {"nik": NIK}}
        self.assertEqual(self.post("frista:admin_nik", {"nik": NIK}, **ADMIN).json()["status"], S.OK)

        self.client_mock.register_image.return_value = {"status": S.OK, "message": "ok"}
        r = self.post("frista:admin_register", {"member_id": NIK, "image_base64": "abc"}, **ADMIN)
        self.assertEqual(r.json()["status"], S.OK)
        self.client_mock.register_image.assert_called_once_with(NIK, "abc")

        self.client_mock.register_encoding.return_value = {"status": S.OK, "message": "ok"}
        self.post("frista:admin_register", {"member_id": NIK, "encoding": ENC, "kanal": "fktp"}, **ADMIN)
        self.client_mock.register_encoding.assert_called_once_with(NIK, ENC, "fktp")

        r = self.post("frista:admin_register", {"member_id": NIK}, **ADMIN)
        self.assertEqual(r.status_code, 400)

    def test_bad_json(self):
        r = self.client.post(reverse("frista:create_session"), "bukan json", content_type="application/json", **APM)
        self.assertEqual(r.status_code, 400)
