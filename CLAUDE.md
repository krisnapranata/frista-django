# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Django service that connects hospital APM kiosks (Android tablets) to FRISTA, the
BPJS Kesehatan face-recognition API, so the VClaim account never lives in the APK.
The FRISTA endpoints are unofficial and undocumented. The client logic is ported from
[PHP-Frista](https://github.com/banguncode/PHP-Frista). Code comments, user-facing
messages and the README are in Indonesian; keep it that way.

## Commands

```bash
source venv/bin/activate
pip install -r requirements.txt

python manage.py test frista                         # Django/APM flow tests (fake client)
python manage.py test frista.tests.ApmFlowTests.<test_name>
python -m unittest discover -s tests                 # pure client tests, no Django
python -m unittest discover -s tests -k <test_name>

python manage.py frista_check [--nik 16digit]        # real login (and NIK lookup) against BPJS
python manage.py runserver localhost:8000            # camera works on http only for localhost

docker compose up -d --build                         # gunicorn on port 8010, state in ./data
docker compose exec frista python manage.py frista_check
```

No linter or formatter is configured.

The two test suites fake BPJS at different layers:
- `tests/test_client.py` swaps `requests.Session` for `FakeSession`, which has per-path
  response queues. An unexpected request path raises `AssertionError`, so if a client
  method starts making a new BPJS call (as `_fill_nokartu` calls `nik2`), every existing
  test that reaches it needs a route for that path.
- `frista/tests.py` patches `frista.views.get_client` with a `Mock`, so it tests only the
  view and session logic. It still writes to the real `frista.log` (masked id `…1234`).

## Architecture

- `frista/client.py` is a **Django-free** FRISTA client (`FristaClient`). It handles login
  and token reuse through a pluggable `TokenStore`. `_authed()` retries once after
  re-login on HTTP 401. It maps BPJS responses to `StatusCode` values (`frista/status.py`).
  `map_match_response()` maps on BPJS message text ("terdaftar hari ini", "tidak sesuai", …),
  so any change to BPJS wording breaks status mapping.
- `frista/services.py` builds one client per process from `settings.FRISTA` and stores the
  token in the Django cache, so all gunicorn workers share it.
- `frista/views.py` contains three kinds of endpoints:
  - APM (`X-APM-Key`): `POST /api/sessions` creates a cache-backed session and returns a
    signed `capture_url`. `GET /api/sessions/<id>` returns the **full** result,
    including the `nokartu` the APM needs to create a SEP.
  - Camera page (signed token `t`, no key): `/capture/` renders
    `templates/frista/capture.html`. That page runs face-api.js locally from
    `static/faceapi` and posts a 128-float encoding to `/capture/verify`. Responses here
    go through `_public_result()`, which masks NIK and nokartu for the kiosk screen.
  - Admin/SIMRS (`X-Admin-Key`, never in the APK): `verify`, `nik`, `register`.
- Session state lives only in the Django cache (file-based by default at `.cache/` or
  `CACHE_LOCATION`; Redis for multiple servers). There is no database. Sessions enforce
  `FRISTA_MAX_ATTEMPTS`. `StatusCode.PASSED` and `RETRYABLE` decide when a result is final.
- The result goes back to the APK via the `AndroidFrista.onResult` JS bridge (see
  `android-example/FristaActivity.kt`), or via `postMessage` when the page is in an iframe.

## BPJS response quirks (verified against the real server)

- `face/nik2` returns `nama`/`nik`/`nokartu` at the top level, not under `data`.
  `_raw()` handles both shapes.
- `face/match2` returns a masked `nokartu` (`000*******322`). `ALREADY_REGISTERED` (one
  verification per participant per day) returns no data at all. In both cases,
  `FristaClient._fill_nokartu()` fills in the full data from `nik2`. A failed lookup
  must never turn a passed verification into a failure.

## Deployment notes

- Browsers allow the camera only over HTTPS (except `localhost`). In production nginx
  terminates TLS in front of port 8010 and **must** send `X-Forwarded-Proto https`.
  Otherwise `capture_url` is built as `http://` (`SECURE_PROXY_SSL_HEADER`).
- `DJANGO_ALLOWED_HOSTS` does not include `127.0.0.1`. Use `localhost` or set the
  `Host` header, as the Docker healthcheck does.
- Config comes from `.env` (see `.env.example`). `LOG_FILE` and `CACHE_LOCATION` are
  overridable; the Docker image points both into `/app/data`.
- The GitHub repo is public. Never commit `.env`, logs, or real NIK/nokartu values.
