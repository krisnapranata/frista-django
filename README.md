# FRISTA Django: Verifikasi Wajah untuk APM Android

Layanan Django yang menghubungkan **APM Android** ke **FRISTA (Face Recognition BPJS
Kesehatan)**, tanpa perlu aplikasi FRISTA desktop Windows dan tanpa menyimpan akun
VClaim di dalam APK.

> **Penting: ini bukan aplikasi resmi BPJS Kesehatan.** Proyek ini tidak membongkar
> atau menyalin aplikasi FRISTA resmi. Ini adalah klien API buatan sendiri yang
> diporting dari library komunitas [PHP-Frista](https://github.com/banguncode/PHP-Frista)
> (MIT). Endpoint FRISTA tidak didokumentasikan untuk publik dan bisa berubah
> sewaktu-waktu. **Konfirmasikan ke BPJS Kesehatan cabang sebelum dipakai untuk
> layanan pasien.**

---

## Cara kerja

```
 APM Android ──(1) POST /api/sessions {member_id}  [X-APM-Key]──► Django
             ◄── {session_id, capture_url} ──────────────────────
 APM membuka capture_url di WebView
 WebView ──(2) kamera + face-api.js → encoding 128 angka
         ──(3) POST /capture/verify {t, encoding} ─────────────► Django ──► FRISTA BPJS
         ◄── hasil ──────────────────────────────────────────────        (akun VClaim
 WebView ──(4) AndroidFrista.onResult(json) ──► APM                      hanya di server)
 APM ─────(5) GET /api/sessions/<id> [X-APM-Key] → hasil lengkap (nokartu) → buat SEP
```

- **Akun VClaim hanya ada di server**, tidak pernah dikirim ke APK.
- Halaman kamera diamankan **token sesi bertanda tangan** yang berlaku 3 menit
  (bisa diatur) dan hanya untuk satu peserta.
- Maksimal **3 percobaan** per sesi (bisa diatur). Sesi yang sudah lolos tidak bisa
  dipakai ulang.
- Di layar kiosk, NIK dan nomor kartu **disamarkan**. Data lengkap hanya bisa diambil
  APM lewat API berkunci.
- face-api.js dan model wajahnya **sudah disertakan** di `static/faceapi`, jadi
  deteksi wajah tidak butuh internet/CDN. Yang butuh internet hanya server Django
  ke server BPJS.

## Struktur

```
frista_django/
├── config/                  pengaturan Django
├── frista/
│   ├── client.py            klien FRISTA (Python murni, bisa dipakai tanpa Django)
│   ├── status.py            kode status hasil
│   ├── services.py          penghubung klien ↔ cache Django
│   ├── views.py             endpoint API + halaman kamera
│   ├── templates/frista/capture.html   halaman kamera untuk WebView
│   ├── management/commands/frista_check.py   uji login ke BPJS
│   └── tests.py             uji alur APM (Django)
├── tests/test_client.py     uji klien (tanpa Django)
├── static/faceapi/          face-api.js 0.22.2 + model (MIT)
├── android-example/FristaActivity.kt   contoh integrasi APK
└── .env.example
```

## Instalasi

Butuh Python 3.10+ di server Linux/Windows yang bisa mengakses internet (ke
`frista.bpjs-kesehatan.go.id`) dan bisa diakses tablet APM lewat jaringan lokal.

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # lalu isi .env
python manage.py collectstatic --noinput

# Uji login ke BPJS (pakai akun VClaim)
python manage.py frista_check
python manage.py frista_check --nik 3212xxxxxxxxxxxx

# Uji otomatis
python manage.py test frista
python -m unittest discover -s tests

# Jalankan
gunicorn config.wsgi -b 127.0.0.1:8000 -w 3
```

Buat `DJANGO_SECRET_KEY`, `APM_API_KEY`, dan `ADMIN_API_KEY` dengan string acak, misalnya:
`python -c "import secrets; print(secrets.token_urlsafe(40))"`.

## Docker

Container berjalan di port **8010** dengan gunicorn. Cache sesi/token dan `frista.log`
disimpan di folder `./data`.

```bash
cp .env.example .env              # lalu isi .env
docker compose up -d --build
docker compose exec frista python manage.py frista_check --nik 3212xxxxxxxxxxxx
docker compose logs -f
```

Port 8010 hanya HTTP. Untuk APM tetap pasang nginx/Caddy dengan HTTPS di depannya
(lihat bagian berikut), dengan `proxy_pass http://127.0.0.1:8010;`.

## WAJIB HTTPS

Browser (termasuk Android WebView) **hanya mengizinkan kamera di halaman HTTPS**
(kecuali `localhost`). Jika APM membuka `http://192.168.x.x`, kamera akan ditolak.

Pilihan yang disarankan:

1. Pasang nginx/Caddy di depan gunicorn dengan sertifikat untuk domain internal,
   misalnya `frista.rs.local`.
2. Sertifikat dari CA internal rumah sakit (atau `mkcert`) harus **dipasang sebagai
   CA tepercaya di tablet APM**. Jangan mengabaikan error SSL di WebView.

Contoh nginx:

```nginx
server {
    listen 443 ssl;
    server_name frista.rs.local;
    ssl_certificate     /etc/ssl/frista.rs.local.crt;
    ssl_certificate_key /etc/ssl/frista.rs.local.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

## Referensi API

Semua body dan respons dalam JSON.

### Untuk APM (header `X-APM-Key`)

| Method | Path | Body | Keterangan |
|---|---|---|---|
| POST | `/api/sessions` | `{"member_id": "NIK 16 digit / no. kartu 13 digit"}` | Membuat sesi, mengembalikan `session_id` dan `capture_url` |
| GET | `/api/sessions/<session_id>` | - | `PENDING` selama belum selesai, lalu hasil lengkap (termasuk `nokartu`) |

### Untuk halaman kamera (tanpa kunci, memakai token sesi)

| Method | Path | Keterangan |
|---|---|---|
| GET | `/capture/?t=<token>` | Halaman kamera |
| POST | `/capture/verify` | `{"t": token, "encoding": [128 angka]}` |

### Untuk petugas / SIMRS (header `X-Admin-Key`, JANGAN di APK)

| Method | Path | Body |
|---|---|---|
| POST | `/api/admin/verify` | `{"member_id", "encoding"}` |
| POST | `/api/admin/nik` | `{"nik"}` |
| POST | `/api/admin/register` | `{"member_id", "image_base64"}` atau `{"member_id", "encoding", "kanal": "fktl"/"fktp"}` |

Registrasi wajah pertama kali sebaiknya dilakukan di loket oleh petugas, bukan di APM
tanpa pengawasan.

### Kode status

| Status | Arti | Tindakan APM |
|---|---|---|
| `OK` | Wajah cocok | Lanjut buat SEP |
| `ALREADY_REGISTERED` | Sudah verifikasi hari ini | Lanjut buat SEP |
| `UNREGISTERED` | Peserta belum rekam wajah | Arahkan ke loket petugas |
| `FACE_MISMATCH` | Wajah tidak cocok | Otomatis coba ulang, lalu ke loket |
| `FACE_UNPROCESSABLE` | Kualitas wajah buruk | Otomatis coba ulang |
| `DUPLICATE_PHOTO` | Foto identik pernah dikirim | Otomatis coba ulang |
| `AUTH_FAILED` | Login akun VClaim gagal | Hubungi IT |
| `SERVER_UNREACHABLE` | Server BPJS tidak bisa dihubungi | Coba lagi / layanan manual |
| `INTEGRATION_ERROR`, `INTERNAL_SERVER_ERROR` | Error dari BPJS | Hubungi IT |
| `TOO_MANY_ATTEMPTS` | Batas percobaan habis (HTTP 429) | Ke loket petugas |

## Integrasi di APK Android

Lihat `android-example/FristaActivity.kt`. Intinya:

1. APK meminta izin `CAMERA`.
2. APK memanggil `POST /api/sessions` dengan `X-APM-Key`.
3. APK membuka `capture_url` di WebView dengan `javaScriptEnabled`, mengizinkan
   `RESOURCE_VIDEO_CAPTURE` hanya untuk server sendiri, dan mendaftarkan bridge
   `AndroidFrista`.
4. Halaman memanggil `AndroidFrista.onResult(json)` saat selesai.
5. Jika status `OK`/`ALREADY_REGISTERED`, APK mengambil `GET /api/sessions/<id>` untuk
   data lengkap (nokartu), lalu membuat SEP lewat bridging VClaim seperti biasa.

Halaman yang sama juga bisa disematkan di `iframe` (APM berbasis web). Hasilnya dikirim
lewat `window.parent.postMessage({source: "frista", result})`.

## Hal yang perlu diketahui

- **Tidak ada deteksi liveness.** face-api.js tidak bisa membedakan wajah asli dengan
  foto/layar. Aplikasi FRISTA resmi kemungkinan punya pengaman tambahan. Pertimbangkan
  pengawasan petugas di area APM.
- **Kompatibilitas encoding.** Format 128 angka dari face-api.js mengikuti yang dipakai
  PHP-Frista. Uji dulu dengan beberapa peserta nyata sebelum dipakai luas.
- **Satu server, beberapa worker:** cache default berbasis file sudah cukup. Untuk
  beberapa server, gunakan Redis (lihat `.env.example`).
- Log ada di `frista.log`. NIK di log disamarkan.

## Lisensi pihak ketiga

- Logika endpoint diporting dari [PHP-Frista](https://github.com/banguncode/PHP-Frista), MIT (lihat folder `licenses/`).
- [face-api.js](https://github.com/justadudewhohacks/face-api.js) 0.22.2 dan modelnya, MIT
  (lihat folder `licenses/`).
