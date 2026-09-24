"""소셜 로그인 OAuth 콜백 뷰 테스트 — state 대조, 토큰 전달 방식"""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

import pytest

from shopping.tests.factories import UserFactory

STATE = "google_0123456789abcdef0123456789abcdef"
EXCHANGE = "shopping.views.social_auth_views.SocialAuthService.process_oauth_callback"


def user_info(email):
    return {"email": email, "name": None, "provider_id": "g-9", "profile_image": None}


@pytest.mark.django_db
class TestSocialCallbackState:
    """로그인을 시작한 브라우저의 state 쿠키와 콜백의 state가 같아야 한다"""

    def test_state_mismatch_rejected_before_code_exchange(self, client):
        """다른 state 쿠키 → 인가 코드를 교환하지 않고 에러로 돌려보낸다"""
        # Arrange
        client.cookies["oauth_state"] = "google_someone_else"

        # Act
        with patch(EXCHANGE) as exchange:
            response = client.get(reverse("social-callback"), {"code": "c", "state": STATE})

        # Assert
        exchange.assert_not_called()
        assert parse_qs(urlsplit(response["Location"]).query)["error"] == ["oauth_error"]

    def test_missing_state_cookie_rejected(self, client):
        """state 쿠키가 없으면(이 브라우저가 시작한 로그인이 아니면) 거부한다"""
        # Act
        with patch(EXCHANGE) as exchange:
            response = client.get(reverse("social-callback"), {"code": "c", "state": STATE})

        # Assert
        exchange.assert_not_called()
        assert parse_qs(urlsplit(response["Location"]).query)["error"] == ["oauth_error"]


@pytest.mark.django_db
class TestSocialCallbackTokenDelivery:
    """토큰은 URL 쿼리로 넘기지 않는다 (접근 로그·Referer·브라우저 기록에 남음)"""

    def test_tokens_not_in_query(self, client):
        """refresh는 HttpOnly 쿠키, access는 URL 조각(#)으로 넘기고 state 쿠키는 지운다"""
        # Arrange
        user = UserFactory(email="callback@social.test")
        client.cookies["oauth_state"] = STATE

        # Act
        with patch(EXCHANGE, return_value=user_info(user.email)):
            response = client.get(reverse("social-callback"), {"code": "c", "state": STATE})

        # Assert
        location = urlsplit(response["Location"])
        query = parse_qs(location.query)
        assert "access_token" not in query
        assert "refresh_token" not in query
        assert parse_qs(location.fragment)["access_token"]
        assert response.cookies["refresh_token"]["httponly"]
        assert response.cookies["oauth_state"].value == ""
