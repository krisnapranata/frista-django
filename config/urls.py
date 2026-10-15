from django.urls import include, path

urlpatterns = [
    path("", include("frista.urls")),
    path("", include("fingerprint.urls")),
]
