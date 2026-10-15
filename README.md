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

## Instalasi dengan Docker (server production)

Container menjalankan gunicorn di port **8010** sebagai user **uid 1000**. Cache sesi/token
dan `frista.log` disimpan di folder `./data` (di-mount ke `/app/data`). Port 8010 hanya HTTP,
jadi tetap butuh reverse proxy HTTPS di depannya.

### Ringkasan perintah (server dengan Nginx Proxy Manager)

Salin per blok. Penjelasan tiap langkah ada di bawahnya.

**A. Pasang pertama kali**

```bash
# 1. Ambil kode
sudo mkdir -p /srv && cd /srv
sudo git clone https://github.com/krisnapranata/frista-django.git
sudo chown -R $USER: frista-django
cd /srv/frista-django

# 2. Folder data milik uid 1000 (tanpa ini container terus "Restarting")
mkdir -p data
sudo chown -R 1000:1000 data

# 3. Buat .env, lalu buat 3 kunci acak untuk SECRET_KEY, APM_API_KEY, ADMIN_API_KEY
cp .env.example .env
for i in 1 2 3; do python3 -c "import secrets; print(secrets.token_urlsafe(40))"; done
nano .env      # isi kunci, akun VClaim, DJANGO_DEBUG=false, domain (lihat langkah 4)

# 4. Cari nama jaringan Nginx Proxy Manager (biasanya proxy_default)
docker ps --format '{{.Names}}  {{.Image}}' | grep -i proxy-manager
docker inspect proxy-npm-1 -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'

# 5. Aktifkan docker-compose.npm.yml lewat .env (ganti proxy_default bila berbeda)
grep -q '^COMPOSE_FILE=' .env || cat >> .env <<'EOF'

COMPOSE_FILE=docker-compose.yml:docker-compose.npm.yml
NPM_NETWORK=proxy_default
EOF

# 6. Jalankan dan cek
docker compose up -d --build
docker compose ps                  # tunggu "Up ... (healthy)", kolom PORTS kosong
docker compose exec frista python manage.py frista_check
```

Lalu buat Proxy Host di panel NPM (`http://<ip-server>:81`): Forward Hostname `frista-django`,
Port `8010`, SSL Let's Encrypt + Force SSL (detail di langkah 7).

**B. Update ke versi terbaru**

```bash
cd /srv/frista-django
git pull
docker compose up -d --build
docker compose ps
```

**C. Kalau container bermasalah**

```bash
cd /srv/frista-django
docker compose ps
docker compose logs --tail 60 frista
sudo chown -R 1000:1000 data && docker compose up -d     # bila log berisi PermissionError
```

### 1. Prasyarat

- Docker dan Docker Compose **2.24 atau lebih baru** (`docker compose version`)
- Server bisa mengakses `https://frista.bpjs-kesehatan.go.id` (cek: `getent hosts frista.bpjs-kesehatan.go.id`)
- Domain yang mengarah ke server, dan reverse proxy HTTPS (Nginx Proxy Manager, nginx, atau Caddy)
- Akun VClaim faskes

### 2. Ambil kode

```bash
sudo mkdir -p /srv && cd /srv
sudo git clone https://github.com/krisnapranata/frista-django.git
sudo chown -R $USER: frista-django
cd frista-django
```

### 3. Folder data (WAJIB)

```bash
mkdir -p data
sudo chown -R 1000:1000 data
```

> Container berjalan sebagai uid 1000. Kalau folder `data` milik user lain, `frista.log`
> tidak bisa ditulis, gunicorn langsung berhenti, dan container terus `Restarting (1)`.

### 4. Isi `.env`

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(40))"   # jalankan 3x untuk 3 kunci
nano .env
```

Yang wajib diisi/diubah (ganti `frista.domain-anda.id` dengan domain Anda):

```ini
DJANGO_SECRET_KEY=<kunci acak 1>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=frista.domain-anda.id,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://frista.domain-anda.id

FRISTA_USERNAME=<username VClaim>
FRISTA_PASSWORD=<password VClaim>

APM_API_KEY=<kunci acak 2>       # diisi di Pengaturan aplikasi APM
ADMIN_API_KEY=<kunci acak 3>     # hanya untuk petugas/SIMRS, jangan di APK
```

`localhost` di `DJANGO_ALLOWED_HOSTS` dipakai oleh healthcheck container, jangan dihapus.

### 5. Hubungkan ke reverse proxy

**Nginx Proxy Manager (NPM) yang berjalan di Docker, di server yang sama.** Container
FRISTA digabung ke jaringan Docker NPM, dan port 8010 tidak dibuka ke luar. Cari nama
jaringan NPM terlebih dahulu:

```bash
docker ps --format '{{.Names}}  {{.Image}}' | grep -i proxy-manager
docker inspect <nama_container_npm> -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

Lalu tambahkan dua baris ini di `.env` (ganti `proxy_default` dengan nama jaringan dari
perintah di atas). Repo sudah berisi `docker-compose.npm.yml`; baris `COMPOSE_FILE` membuat
`docker compose` memakainya otomatis, jadi tidak perlu `-f` di setiap perintah:

```ini
COMPOSE_FILE=docker-compose.yml:docker-compose.npm.yml
NPM_NETWORK=proxy_default
```

**nginx/Caddy langsung di host (bukan Docker).** Buka port hanya untuk localhost dengan
membuat `docker-compose.override.yml` (khusus server, tidak ikut ke git):

```yaml
services:
  frista:
    ports: !override ["127.0.0.1:8010:8010"]
```

### 6. Jalankan

```bash
docker compose up -d --build
docker compose ps                     # tunggu sampai STATUS "Up ... (healthy)", sekitar 30 detik
docker compose exec frista python manage.py frista_check
```

`frista_check` harus menampilkan `Login FRISTA berhasil`. Tambahkan `--nik <16 digit>` untuk
sekaligus mencoba mencari data peserta.

### 7. Buat proxy host

**Nginx Proxy Manager** (panel di `http://<ip-server>:81`) → Hosts → Proxy Hosts → Add:

| Tab | Isian |
|---|---|
| Details | Domain: `frista.domain-anda.id`; Scheme `http`; Forward Hostname `frista-django`; Port `8010`; aktifkan Block Common Exploits |
| SSL | Request a new SSL Certificate (Let's Encrypt); aktifkan Force SSL dan HTTP/2 |
| Advanced (disarankan) | `location /api/admin/ { deny all; }` supaya endpoint petugas tidak bisa diakses dari internet |

NPM sudah mengirim header `Host` dan `X-Forwarded-Proto`. Untuk nginx biasa, lihat contoh di
bagian [WAJIB HTTPS](#wajib-https) dengan `proxy_pass http://127.0.0.1:8010;`.

### 8. Uji

```bash
curl https://frista.domain-anda.id/health            # {"status": "ok"}

curl -X POST https://frista.domain-anda.id/api/sessions \
  -H "X-APM-Key: <APM_API_KEY>" -H "Content-Type: application/json" \
  -d '{"member_id":"<NIK 16 digit>"}'
```

`capture_url` di respons harus diawali `https://`. Buka link itu di HP/tablet dan izinkan kamera.
Kamera **belakang** langsung terbuka. Arahkan ke wajah peserta lalu tekan **Ambil Gambar**,
seperti di aplikasi FRISTA Windows. Kalau perangkat punya lebih dari satu kamera, ada tombol **Ganti Kamera**.

Perilaku ini bisa diubah lewat `.env`:

| Variabel | Default | Pilihan |
|---|---|---|
| `CAPTURE_MODE` | `manual` | `manual` = tombol Ambil Gambar, `auto` = foto diambil otomatis saat wajah stabil |
| `CAPTURE_CAMERA` | `environment` | `environment` = kamera belakang, `user` = kamera depan |
| `FACEAPI_DETECTOR` | `tiny` | `tiny` = detektor ringan 190 KB (cepat di tablet), `ssd` = detektor lama 5,6 MB |

Untuk satu perangkat saja, tambahkan `&mode=auto` atau `&cam=user` di belakang `capture_url`.

### 9. Update ke versi terbaru

```bash
cd /srv/frista-django
git pull
docker compose up -d --build
```

`.env`, `data/`, dan `docker-compose.override.yml` (kalau ada) tidak tersentuh oleh `git pull`.

### Troubleshooting

Lihat log dulu: `docker compose logs --tail 60 frista`

| Gejala | Penyebab | Perbaikan |
|---|---|---|
| `Restarting (1)`, log berisi `PermissionError` atau `Unable to configure handler 'file'` | Folder `data` tidak ada atau bukan milik uid 1000 | `mkdir -p data && sudo chown -R 1000:1000 data && docker compose up -d` |
| `Restarting (1)`, log berisi `SECRET_KEY setting must not be empty` | `.env` tidak ada atau `DJANGO_SECRET_KEY` kosong | Isi `.env` (langkah 4), lalu `docker compose up -d` |
| Status `(unhealthy)`, log berisi `DisallowedHost` | `localhost` atau domain tidak ada di `DJANGO_ALLOWED_HOSTS` | Tambahkan, lalu `docker compose up -d` |
| Proxy menampilkan 502 Bad Gateway | Container belum jalan, atau tidak satu jaringan dengan NPM | Cek `docker compose ps` dan `NPM_NETWORK` di `.env` (harus sama dengan jaringan NPM) |
| `capture_url` diawali `http://`, kamera ditolak di tablet | Proxy tidak mengirim `X-Forwarded-Proto https` | Aktifkan SSL/Force SSL di NPM, atau tambahkan header di nginx |
| `frista_check`: tidak dapat menghubungi server FRISTA | DNS/firewall server tidak bisa ke BPJS | Cek `getent hosts frista.bpjs-kesehatan.go.id` dan akses keluar port 443 |
| `frista_check`: login gagal | Username/password VClaim salah | Perbaiki `.env`, lalu `docker compose up -d` |

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
