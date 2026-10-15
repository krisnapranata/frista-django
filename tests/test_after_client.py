"""Uji klien sidik jari BPJS (After) tanpa Django dan tanpa koneksi ke BPJS.

Jalankan:  python -m unittest discover -s tests -v
"""

import base64
import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fingerprint.after_client import (  # noqa: E402
    TOKEN_DATE_KEY,
    TOKEN_KEY,
    AfterClient,
    AfterConfig,
    MemoryTokenStore,
    normalize_image,
    normalize_minutiae,
)
from fingerprint.status import StatusCode as S  # noqa: E402
from test_client import FakeSession  # noqa: E402

NIK = "3212345678901234"
NOKA = "0001234567890"
# Minutiae palsu dalam bentuk base64url seperti keluaran DigitalPersona
MINUTIAE_URL = base64.urlsafe_b64encode(bytes(range(250, 256)) * 20).decode().rstrip("=")
MINUTIAE = MINUTIAE_URL.replace("-", "+").replace("_", "/")
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"x" * 20 + b"\xff\xd9").decode()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 20).decode()

LOGIN = "/finger-rest/v2/user/login"
VERIFY = "/finger-rest/v1/finger/verify"
ADD = "/finger-rest/v1/finger/addpeserta"
LOGIN_OK = (200, {"response": {"tokenLogin": "TOKEN-1"}, "metaData": {"code": 200, "message": "OK"}})


def make_client(routes, **cfg):
    session = FakeSession(routes)
    client = AfterClient(AfterConfig(username="u", password="p", **cfg),
                         store=MemoryTokenStore(), session=session)
    return client, session


def meta(code, message="pesan BPJS"):
    return {"metaData": {"code": code, "message": message}}


class TestValidation(unittest.TestCase):
    def test_minutiae(self):
        self.assertEqual(normalize_minutiae(MINUTIAE_URL), MINUTIAE)   # base64url → base64
        self.assertEqual(normalize_minutiae(MINUTIAE), MINUTIAE)
        for bad in (None, "", "bukan base64!", "A", "a=b"):
            self.assertIsNone(normalize_minutiae(bad), bad)

    def test_image(self):
        self.assertEqual(normalize_image(JPEG), JPEG)
        self.assertEqual(normalize_image("data:image/png;base64," + PNG), PNG)
        self.assertIsNone(normalize_image(base64.b64encode(b"GIF89a....").decode()))
        self.assertIsNone(normalize_image("bukan base64!"))


class TestClient(unittest.TestCase):
    def test_verify_success_by_nik(self):
        client, http = make_client({LOGIN: [LOGIN_OK], VERIFY: [(200, meta(200, "OK"))]})
        r = client.verify(NIK, MINUTIAE_URL)
        self.assertEqual(r["status"], S.OK)
        self.assertEqual(http.calls[0]["json"], {"username": "u", "password": "p"})
        call = http.calls[1]
        self.assertEqual(call["headers"]["token"], "TOKEN-1")
        self.assertEqual(call["json"], {"keyword": NIK, "jnsFilter": "nik", "finger": MINUTIAE})

    def test_verify_by_card_number(self):
        client, http = make_client({LOGIN: [LOGIN_OK], VERIFY: [(200, meta(200))]})
        client.verify(NOKA, MINUTIAE)
        self.assertEqual(http.calls[1]["json"]["jnsFilter"], "psnoka")

    def test_verify_bpjs_codes(self):
        cases = {1: S.NOT_REGISTERED, 2: S.NOT_ENROLLED, 3: S.NOT_RECOGNIZED, 99: S.INTEGRATION_ERROR}
        for code, status in cases.items():
            client, _ = make_client({LOGIN: [LOGIN_OK], VERIFY: [(201, meta(code, "teks asli"))]})
            r = client.verify(NIK, MINUTIAE)
            self.assertEqual(r["status"], status, code)
            self.assertEqual((r["bpjs_code"], r["bpjs_message"]), (str(code), "teks asli"))

    def test_token_reused_same_day(self):
        client, http = make_client({LOGIN: [LOGIN_OK], VERIFY: [(200, meta(200))]})
        client.verify(NIK, MINUTIAE)
        client.verify(NIK, MINUTIAE)
        self.assertEqual([c["path"] for c in http.calls], [LOGIN, VERIFY, VERIFY])

    def test_token_from_yesterday_relogin(self):
        client, http = make_client({LOGIN: [LOGIN_OK], VERIFY: [(200, meta(200))]})
        client.store.set(TOKEN_KEY, "LAMA")
        client.store.set(TOKEN_DATE_KEY, "2000-01-01")
        client.verify(NIK, MINUTIAE)
        self.assertEqual([c["path"] for c in http.calls], [LOGIN, VERIFY])

    def test_expired_token_triggers_single_relogin(self):
        client, http = make_client({
            LOGIN: [LOGIN_OK, (200, {"response": {"tokenLogin": "TOKEN-2"}})],
            VERIFY: [(401, None), (200, meta(200))],
        })
        r = client.verify(NIK, MINUTIAE)
        self.assertEqual(r["status"], S.OK)
        self.assertEqual([c["path"] for c in http.calls], [LOGIN, VERIFY, LOGIN, VERIFY])
        self.assertEqual(http.calls[-1]["headers"]["token"], "TOKEN-2")

    def test_login_failure(self):
        client, http = make_client({LOGIN: [(401, meta(401, "user salah"))]})
        r = client.verify(NIK, MINUTIAE)
        self.assertEqual(r["status"], S.AUTH_FAILED)
        self.assertEqual(len(http.calls), 1)

    def test_timeout(self):
        client, _ = make_client({LOGIN: [LOGIN_OK], VERIFY: [requests.Timeout("read timed out")]})
        self.assertEqual(client.verify(NIK, MINUTIAE)["status"], S.SERVER_UNREACHABLE)

    def test_bpjs_server_error(self):
        client, _ = make_client({LOGIN: [LOGIN_OK], VERIFY: [(500, None)]})
        r = client.verify(NIK, MINUTIAE)
        self.assertEqual(r["status"], S.INTERNAL_SERVER_ERROR)
        self.assertEqual(r["http"], 500)

    def test_budget_exhausted(self):
        client, http = make_client({}, request_budget=0.5)
        self.assertEqual(client.verify(NIK, MINUTIAE)["status"], S.SERVER_UNREACHABLE)
        self.assertEqual(http.calls, [])

    def test_invalid_input_makes_no_request(self):
        client, http = make_client({})
        self.assertEqual(client.verify("123", MINUTIAE)["status"], S.INVALID_ID)
        self.assertEqual(client.verify(NIK, "bukan base64!")["status"], S.INVALID_MINUTIAE)
        self.assertEqual(client.register(NIK, MINUTIAE, JPEG, MINUTIAE, JPEG)["status"], S.INVALID_PARTICIPANT)
        self.assertEqual(client.register(NOKA, "x!", JPEG, MINUTIAE, JPEG)["status"], S.INVALID_MINUTIAE)
        self.assertEqual(client.register(NOKA, MINUTIAE, JPEG, MINUTIAE, "x")["status"], S.INVALID_IMAGE)
        self.assertEqual(client.reset(NOKA, "03")["status"], S.INVALID_REASON)
        self.assertEqual(client.reset(NIK, "01")["status"], S.INVALID_PARTICIPANT)
        self.assertEqual(http.calls, [])

    def test_register_payload(self):
        client, http = make_client({LOGIN: [LOGIN_OK], ADD: [(200, meta(200))]})
        r = client.register(NOKA, MINUTIAE_URL, "data:image/jpeg;base64," + JPEG, MINUTIAE, PNG)
        self.assertEqual(r["status"], S.OK)
        self.assertEqual(http.calls[1]["json"], {"nokapst": NOKA, "finger": [
            {"finger": MINUTIAE, "index": 1, "imgStr": JPEG},
            {"finger": MINUTIAE, "index": 6, "imgStr": PNG},
        ]})

    def test_reset(self):
        path = f"/finger-rest/v1/finger/delpeserta/{NOKA}/02"
        client, http = make_client({LOGIN: [LOGIN_OK], path: [(200, meta(200))]})
        self.assertEqual(client.reset(NOKA, "02")["status"], S.OK)
        self.assertEqual(http.calls[1]["method"], "DELETE")

    def test_reset_failure_keeps_bpjs_message(self):
        path = f"/finger-rest/v1/finger/delpeserta/{NOKA}/01"
        client, _ = make_client({LOGIN: [LOGIN_OK], path: [(400, meta(201, "Data tidak ditemukan"))]})
        r = client.reset(NOKA, "01")
        self.assertEqual((r["status"], r["message"]), (S.INTEGRATION_ERROR, "Data tidak ditemukan"))


if __name__ == "__main__":
    unittest.main()
