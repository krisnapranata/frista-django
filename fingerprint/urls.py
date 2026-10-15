from django.urls import path

from . import views

app_name = "fingerprint"

urlpatterns = [
    # APM Android & PC loket (X-APM-Key atau X-Admin-Key)
    path("api/fingerprint/verify", views.verify, name="verify"),
    # Petugas / SIMRS (X-Admin-Key)
    path("api/fingerprint/register", views.register, name="register"),
    path("api/fingerprint/reset", views.reset, name="reset"),
    # Halaman capture DigitalPersona untuk PC loket (kunci diisi di halaman)
    path("fingerprint/", views.capture, name="capture"),
]
