"""Kode status hasil integrasi FRISTA.

Dipetakan dari respons server BPJS agar aplikasi pemanggil (APM Android,
SIMRS) tidak perlu mem-parsing pesan teks dari BPJS.
"""


class StatusCode:
    OK = "OK"                                      # Wajah cocok
    ALREADY_REGISTERED = "ALREADY_REGISTERED"      # Sudah verifikasi hari ini (dianggap lolos)
    UNREGISTERED = "UNREGISTERED"                  # Peserta belum rekam wajah, perlu registrasi oleh petugas
    FACE_MISMATCH = "FACE_MISMATCH"                # Wajah tidak cocok
    FACE_UNPROCESSABLE = "FACE_UNPROCESSABLE"      # Kualitas wajah tidak bisa diproses
    DUPLICATE_PHOTO = "DUPLICATE_PHOTO"            # Foto identik pernah dikirim
    AUTH_FAILED = "AUTH_FAILED"                    # Login ke server BPJS gagal
    INVALID_ID = "INVALID_ID"                      # NIK/No. kartu bukan 13/16 digit
    INVALID_ENCODING = "INVALID_ENCODING"          # Encoding bukan 128 angka
    INVALID_IMAGE = "INVALID_IMAGE"                # Gambar bukan JPEG valid
    INTEGRATION_ERROR = "INTEGRATION_ERROR"        # Error tak terduga dari BPJS
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"  # Respons tidak terbaca
    SERVER_UNREACHABLE = "SERVER_UNREACHABLE"      # Timeout / server BPJS down

    # Status yang berarti peserta lolos verifikasi
    PASSED = (OK, ALREADY_REGISTERED)
    # Status yang boleh dicoba ulang dengan foto baru
    RETRYABLE = (FACE_MISMATCH, FACE_UNPROCESSABLE, DUPLICATE_PHOTO)
