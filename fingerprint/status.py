"""Kode status standar untuk modul sidik jari (sesuai PHP-After)."""


class StatusCode:
    OK = "OK"                                      # Sidik jari cocok / aksi berhasil
    INVALID_ID = "INVALID_ID"                      # Bukan NIK 16 digit / no. kartu 13 digit
    INVALID_PARTICIPANT = "INVALID_PARTICIPANT"    # No. kartu bukan 13 digit (register/reset)
    INVALID_MINUTIAE = "INVALID_MINUTIAE"          # Minutiae bukan Base64 valid
    INVALID_IMAGE = "INVALID_IMAGE"                # Gambar bukan Base64 JPEG/PNG
    INVALID_REASON = "INVALID_REASON"              # Alasan reset bukan 01/02
    AUTH_FAILED = "AUTH_FAILED"                    # Login ke BPJS gagal
    SERVER_UNREACHABLE = "SERVER_UNREACHABLE"      # Timeout / server BPJS down
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"  # Respons BPJS tidak terbaca
    INTEGRATION_ERROR = "INTEGRATION_ERROR"        # Error lain dari BPJS (lihat bpjs_message)
    NOT_REGISTERED = "NOT_REGISTERED"              # metaData.code 1: bukan peserta BPJS
    NOT_ENROLLED = "NOT_ENROLLED"                  # metaData.code 2: belum rekam sidik jari
    NOT_RECOGNIZED = "NOT_RECOGNIZED"              # metaData.code 3: sidik jari tidak cocok
