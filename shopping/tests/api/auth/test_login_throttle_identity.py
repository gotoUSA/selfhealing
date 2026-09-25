"""
로그인 속도 제한의 식별자 — nginx 뒤에서 X-Forwarded-For 값을 바꿔도 같은 클라이언트

API 테스트 설정은 DummyCache 에 비율 10000/분이라 스로틀이 꺼져 있다. 여기서만 LocMemCache 와
운영 비율(3/분)로 켠다. nginx 는 들어온 X-Forwarded-For 뒤에 자기가 본 주소를 이어 붙이므로
($proxy_add_x_forwarded_for) 믿을 수 있는 건 끝의 값뿐이다. 헤더 전체를 식별자로 쓰면
클라이언트가 앞부분을 바꿀 때마다 새 클라이언트가 되어 제한이 풀린다.
"""

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from shopping.throttles import LoginRateThrottle

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-login-throttle-identity",
    }
}

# nginx 가 본 클라이언트 주소 — nginx 가 헤더 끝에 붙인다
CLIENT_SEEN_BY_NGINX = "203.0.113.7"


@pytest.fixture(autouse=True)
def login_limit_on(settings, monkeypatch):
    settings.CACHES = TEST_CACHES
    monkeypatch.setattr(
        LoginRateThrottle,
        "THROTTLE_RATES",
        {**LoginRateThrottle.THROTTLE_RATES, "login": "3/min"},
    )
    cache.clear()
    yield
    cache.clear()


def _attempt(client, forwarded_for=None):
    extra = {"HTTP_X_FORWARDED_FOR": forwarded_for} if forwarded_for else {}
    return client.post(
        reverse("auth-login"),
        {"username": "nobody", "password": "wrong-password"},
        format="json",
        **extra,
    )


@pytest.mark.django_db
class TestLoginThrottleIdentity:
    """3번 틀리면 4번째는 429 — 헤더 앞부분을 바꿔도"""

    def test_rotating_forwarded_for_does_not_reset_the_limit(self):
        client = APIClient()

        codes = [_attempt(client, f"10.9.0.{i}, {CLIENT_SEEN_BY_NGINX}").status_code for i in range(4)]

        assert codes[:3] == [status.HTTP_400_BAD_REQUEST] * 3
        assert codes[3] == status.HTTP_429_TOO_MANY_REQUESTS

    def test_different_clients_behind_the_proxy_are_limited_separately(self):
        client = APIClient()
        for _ in range(3):
            _attempt(client, "198.51.100.1")

        assert _attempt(client, "198.51.100.1").status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert _attempt(client, "198.51.100.2").status_code == status.HTTP_400_BAD_REQUEST

    def test_request_without_forwarded_for_is_limited_by_socket_address(self):
        client = APIClient()

        codes = [_attempt(client).status_code for _ in range(4)]

        assert codes[3] == status.HTTP_429_TOO_MANY_REQUESTS
