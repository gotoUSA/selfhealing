"""
URL configuration for myproject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from django.http import JsonResponse
from django.shortcuts import redirect
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


def root_view(request):
    """루트 경로 - API 문서로 리다이렉트"""
    return redirect("swagger-ui")


urlpatterns = [
    # 루트 경로 (API 문서로 리다이렉트)
    path("", root_view, name="root"),
    # 관리자 페이지
    path("admin/", admin.site.urls),
    # shopping 앱 URLs 포함
    path("api/", include("shopping.urls")),
    # DRF 인증 URLs (로그인/로그아웃 페이지)
    path("api-auth/", include("rest_framework.urls")),
    # allauth URLs (dj-rest-auth 소셜 로그인에 필요)
    path("accounts/", include("allauth.urls")),
    # OpenAPI Schema
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    # Swagger UI
    path("api/docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # ReDoc UI
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]

# 개발 환경에서 미디어 파일 서빙
if settings.DEBUG:

    import debug_toolbar

    # Debug Toolbar
    urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
    ] + urlpatterns

    # 미디어/ 정적 파일 서빙
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
