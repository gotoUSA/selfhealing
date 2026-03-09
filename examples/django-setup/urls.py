from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health_view(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health_view, name="health"),
    path("admin/", admin.site.urls),
    # Self-Healing API
    path("api/self-healing/", include("selfhealing.api.django.urls")),
    # Your app URLs
    path("api/", include("myapp.urls")),
]
