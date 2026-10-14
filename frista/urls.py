from django.urls import path

from . import views

app_name = "frista"

urlpatterns = [
    path("health", views.health, name="health"),
    # APM Android (header X-APM-Key)
    path("api/sessions", views.create_session, name="create_session"),
    path("api/sessions/<str:sid>", views.session_result, name="session_result"),
    # Halaman kamera di WebView (diamankan token sesi bertanda tangan)
    path("capture/", views.capture, name="capture"),
    path("capture/verify", views.capture_verify, name="capture_verify"),
    # Petugas / SIMRS (header X-Admin-Key)
    path("api/admin/verify", views.admin_verify, name="admin_verify"),
    path("api/admin/nik", views.admin_nik, name="admin_nik"),
    path("api/admin/register", views.admin_register, name="admin_register"),
]
