"""Uji klien FRISTA tanpa Django dan tanpa koneksi ke BPJS.

Jalankan:  python -m unittest discover -s tests -v
"""

import base64
import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frista.client import (  # noqa: E402
    CHECKED_AT_KEY,
    TOKEN_KEY,
    FristaClient,
    FristaConfig,
    MemoryTokenStore,
    decode_jpeg,
    map_match_response,
    validate_encoding,
)
from frista.status import StatusCode as S  # noqa: E402

ENC = [0.01 * i for i in range(128)]
NIK = "3212345678901234"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Pengganti requests.Session: balas sesuai antrean per path."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.headers = {}
        self.calls = []

    def request(self, method, url, headers=None, json=None, data=None, files=None, timeout=None, verify=None):
        path = url.split(".go.id", 1)[1]
        self.calls.append({"method": method, "path": path, "headers": headers or {},
                           "json": json, "data": data, "files": files, "verify": verify,
                           "timeout": timeout})
        queue = self.routes.get(path)
        if not queue:
            raise AssertionError(f"Request tak terduga: {method} {path}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return FakeResponse(*item)


def make_client(routes, **cfg):
    session = FakeSession(routes)
    client = FristaClient(FristaConfig(username="u", password="p", **cfg),
                          store=MemoryTokenStore(), session=session)
    return client, session


LOGIN_OK = (200, {"token": "TOKEN-1"})


class TestValidation(unittest.TestCase):
    def test_encoding(self):
        self.assertEqual(len(validate_encoding(ENC)), 128)
        self.assertIsNone(validate_encoding(ENC[:127]))
        self.assertIsNone(validate_encoding(ENC[:127] + ["x"]))
        self.assertIsNone(validate_encoding(ENC[:127] + [float("nan")]))
        self.assertIsNone(validate_encoding(ENC[:127] + [True]))
        self.assertIsNone(validate_encoding("bukan list"))

    def test_jpeg(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"0" * 20
        b64 = base64.b64encode(jpeg).decode()
        self.assertEqual(decode_jpeg(jpeg), jpeg)
        self.assertEqual(decode_jpeg(b64), jpeg)
        self.assertEqual(decode_jpeg("data:image/jpeg;base64," + b64), jpeg)
        self.assertIsNone(decode_jpeg(base64.b64encode(b"\x89PNG....").decode()))
        self.assertIsNone(decode_jpeg("%%%bukan-base64"))


class TestMapping(unittest.TestCase):
    def test_all_documented_responses(self):
        cases = [
            ({"status": True, "data": {"nama": "A"}, "message": "Data ditemukan"}, S.OK),
            ({"status": False, "message": "Peserta telah terdaftar hari ini"}, S.ALREADY_REGISTERED),
            ({"status": False, "message": "Wajah tidak dapat diproses"}, S.FACE_UNPROCESSABLE),
            ({"status": False, "message": "Anda pernah melakukan upload dengan foto yang identik"}, S.DUPLICATE_PHOTO),
            ({"status": False, "message": "Verifikasi wajah gagal. Foto tidak sesuai", "data": {"distance": 99}}, S.FACE_MISMATCH),
            ({"status": False, "code": "0", "message": "Peserta belum melakukan perekaman wajah."}, S.UNREGISTERED),
            ({"status": False, "message": "Error lain"}, S.INTEGRATION_ERROR),
            ({"tanpa": "status"}, S.INTERNAL_SERVER_ERROR),
            (None, S.INTERNAL_SERVER_ERROR),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(map_match_response(body)["status"], expected)

    def test_mismatch_keeps_distance(self):
        r = map_match_response({"status": False, "message": "Foto tidak sesuai", "data": {"distance": 99.3}})
        self.assertEqual(r["data"]["distance"], 99.3)


class TestClient(unittest.TestCase):
    def test_verify_success_sends_correct_request(self):
        client, http = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [(200, {"status": True, "message": "Data ditemukan",
                                               "data": {"nama": "BUDI", "nokartu": "0001234567890"}})],
        })
        r = client.verify(NIK, ENC)
        self.assertEqual(r["status"], S.OK)
        self.assertEqual(r["data"]["nama"], "BUDI")

        login, match = http.calls
        self.assertEqual(login["json"], {"username": "u", "password": "p", "version": "3.0.2"})
        self.assertEqual(match["headers"]["Authorization"], "Bearer TOKEN-1")
        self.assertEqual(match["json"]["id"], NIK)
        self.assertEqual(len(match["json"]["encoding"]), 128)
        self.assertTrue(match["verify"])

    def test_invalid_input_makes_no_request(self):
        client, http = make_client({})
        self.assertEqual(client.verify("123", ENC)["status"], S.INVALID_ID)
        self.assertEqual(client.verify(NIK, ENC[:10])["status"], S.INVALID_ENCODING)
        self.assertEqual(http.calls, [])

    def test_login_failure(self):
        client, _ = make_client({"/frista-api/user/login/rs": [(401, {"message": "salah"})]})
        self.assertEqual(client.verify(NIK, ENC)["status"], S.AUTH_FAILED)

    def test_token_reused_within_interval(self):
        client, http = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [(200, {"status": True, "data": {}})],
        })
        client.verify(NIK, ENC)
        client.verify(NIK, ENC)
        paths = [c["path"] for c in http.calls]
        self.assertEqual(paths.count("/frista-api/user/login/rs"), 1)

    def test_stale_token_is_checked_and_refreshed(self):
        client, http = make_client({
            "/frista-api/user/session": [(200, {"status": False})],
            "/frista-api/user/login/rs": [(200, {"token": "TOKEN-2"})],
            "/frista-api/face/match2": [(200, {"status": True, "data": {}})],
        })
        client.store.set(TOKEN_KEY, "OLD")
        client.store.set(CHECKED_AT_KEY, 0)  # sudah lama tidak dicek
        client.verify(NIK, ENC)
        self.assertEqual([c["path"] for c in http.calls],
                         ["/frista-api/user/session", "/frista-api/user/login/rs", "/frista-api/face/match2"])
        self.assertEqual(http.calls[-1]["headers"]["Authorization"], "Bearer TOKEN-2")

    def test_401_triggers_single_relogin(self):
        client, http = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK, (200, {"token": "TOKEN-2"})],
            "/frista-api/face/match2": [(401, None), (200, {"status": True, "data": {}})],
        })
        self.assertEqual(client.verify(NIK, ENC)["status"], S.OK)
        self.assertEqual(http.calls[-1]["headers"]["Authorization"], "Bearer TOKEN-2")

    def test_http_400(self):
        client, _ = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [(400, None)],
        })
        self.assertEqual(client.verify(NIK, ENC)["status"], S.INTEGRATION_ERROR)

    def test_server_unreachable(self):
        client, _ = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [requests.ConnectionError("timeout")],
        })
        self.assertEqual(client.verify(NIK, ENC)["status"], S.SERVER_UNREACHABLE)

    def test_register_image_multipart(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"1" * 50
        client, http = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/upload": [(200, {"status": True})],
        })
        r = client.register_image(NIK, base64.b64encode(jpeg).decode())
        self.assertEqual(r["status"], S.OK)
        call = http.calls[-1]
        self.assertEqual(call["data"], {"id": NIK})
        self.assertEqual(call["files"]["file"], ("photo.jpg", jpeg, "image/jpeg"))

    def test_register_image_rejects_png(self):
        client, http = make_client({})
        r = client.register_image(NIK, base64.b64encode(b"\x89PNG\r\n").decode())
        self.assertEqual(r["status"], S.INVALID_IMAGE)
        self.assertEqual(http.calls, [])

    def test_register_encoding_and_nik(self):
        client, http = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/upload2": [(200, {"status": True, "message": "ok"})],
            "/frista-api/face/nik2": [(200, {"status": True, "data": {"nik": NIK}})],
        })
        self.assertEqual(client.register_encoding(NIK, ENC, "fktp")["status"], S.OK)
        self.assertEqual(http.calls[-1]["json"]["kanal"], "fktp")
        r = client.nik(NIK)
        self.assertEqual(r["data"]["nik"], NIK)
        self.assertEqual(client.nik("0001234567890")["status"], S.INVALID_ID)

    def test_verify_fills_masked_nokartu_from_nik(self):
        client, http = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [(200, {"status": True, "data": {"nik": NIK, "nokartu": "000*******890"}})],
            "/frista-api/face/nik2": [(200, {"status": True, "nik": NIK, "nokartu": "0001234567890"})],
        })
        r = client.verify(NIK, ENC)
        self.assertEqual(r["status"], S.OK)
        self.assertEqual(r["data"]["nokartu"], "0001234567890")
        self.assertEqual(http.calls[-1]["json"], {"nik": NIK})

    def test_verify_masked_nokartu_by_card_number(self):
        client, http = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [(200, {"status": True, "data": {"nokartu": "000*******890"}})],
        })
        r = client.verify("0001234567890", ENC)
        self.assertEqual(r["data"]["nokartu"], "0001234567890")
        self.assertEqual(len(http.calls), 2)   # tanpa nik2

    def test_already_registered_gets_data_from_nik(self):
        client, _ = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [(200, {"status": False, "message": "Peserta telah terdaftar hari ini"})],
            "/frista-api/face/nik2": [(200, {"status": True, "nama": "BUDI", "nik": NIK, "nokartu": "0001234567890"})],
        })
        r = client.verify(NIK, ENC)
        self.assertEqual(r["status"], S.ALREADY_REGISTERED)
        self.assertEqual(r["data"], {"nokartu": "0001234567890", "nik": NIK, "nama": "BUDI"})

    def test_already_registered_nik_lookup_fails(self):
        client, _ = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [(200, {"status": False, "message": "Peserta telah terdaftar hari ini"})],
            "/frista-api/face/nik2": [requests.ConnectionError("timeout")],
        })
        r = client.verify(NIK, ENC)
        self.assertEqual(r["status"], S.ALREADY_REGISTERED)
        self.assertNotIn("data", r)

    def test_verify_ok_even_if_nik_lookup_fails(self):
        client, _ = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [(200, {"status": True, "data": {"nik": NIK, "nokartu": "000*******890"}})],
            "/frista-api/face/nik2": [requests.ConnectionError("timeout")],
        })
        r = client.verify(NIK, ENC)
        self.assertEqual(r["status"], S.OK)
        self.assertEqual(r["data"]["nokartu"], "000*******890")

    def test_nik_flat_response(self):
        # Bentuk respons nik2 yang sebenarnya dari BPJS: tanpa field "data"
        client, _ = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/nik2": [(200, {"status": True, "nama": "X", "nik": NIK, "nokartu": "0001234567890"})],
        })
        r = client.nik(NIK)
        self.assertEqual(r["status"], S.OK)
        self.assertEqual(r["data"], {"nama": "X", "nik": NIK, "nokartu": "0001234567890"})

    def test_budget_caps_request_timeout(self):
        client, http = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/match2": [(200, {"status": True, "data": {"nokartu": "0001234567890"}})],
        }, timeout=30, request_budget=10)
        client.verify(NIK, ENC)
        self.assertTrue(all(c["timeout"] <= 10 for c in http.calls), http.calls)

    def test_budget_exhausted_is_unreachable(self):
        # Sisa waktu < 1 detik: request BPJS tidak dikirim, APM tetap dapat jawaban
        client, http = make_client({}, request_budget=0.5)
        r = client.verify(NIK, ENC)
        self.assertEqual(r["status"], S.SERVER_UNREACHABLE)
        self.assertEqual(http.calls, [])

    def test_fill_nokartu_public(self):
        client, _ = make_client({
            "/frista-api/user/login/rs": [LOGIN_OK],
            "/frista-api/face/nik2": [(200, {"status": True, "nama": "X", "nik": NIK, "nokartu": "0001234567890"})],
        })
        data = {"nokartu": "000*******890"}
        client.fill_nokartu(NIK, data)
        self.assertEqual(data["nokartu"], "0001234567890")


if __name__ == "__main__":
    unittest.main()
