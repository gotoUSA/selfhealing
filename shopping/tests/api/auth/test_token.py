import base64
import json
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken



@pytest.mark.django_db
class TestTokenRefresh:
    """토큰 갱신 테스트"""

    def test_refresh_token_success(self, api_client, get_tokens):
        """정상적인 토큰 갱신"""
        # Arrange
        tokens = get_tokens
        refresh_token = tokens["refresh"]
        url = reverse("token-refresh")

        # Act
        response = api_client.post(url, {"refresh": refresh_token})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data
        # ROTATE_REFRESH_TOKENS=True이면 새로운 refresh는 Cookie로 전달됨
        if settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
            assert "refresh_token" in response.cookies

    def test_refresh_with_invalid_token(self, api_client):
        """잘못된 Refresh Token으로 갱신 실패"""
        # Arrange
        url = reverse("token-refresh")
        invalid_token = "invalid_token_string_12345"

        # Act
        response = api_client.post(url, {"refresh": invalid_token})

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_refresh_with_expired_token(self, api_client, user):
        """만료된 Refresh Token으로 갱신 실패"""
        # Arrange
        url = reverse("token-refresh")
        # 이미 만료된 refresh token 생성
        refresh = RefreshToken.for_user(user)
        refresh.set_exp(lifetime=timedelta(seconds=-1))  # 1초 전에 만료

        # Act
        response = api_client.post(url, {"refresh": str(refresh)})

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_old_token_after_refresh(self, api_client, get_tokens):
        """토큰 갱신 후 이전 토큰 사용 불가 (ROTATE + BLACKLIST 설정 필요)"""
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

        # ROTATE_REFRESH_TOKENS와 BLACKLIST_AFTER_ROTATION 둘 다 True인 경우만 테스트
        if not settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
            pytest.skip("ROTATE_REFRESH_TOKENS가 비활성화되어 있습니다")
        if not settings.SIMPLE_JWT.get("BLACKLIST_AFTER_ROTATION"):
            pytest.skip("BLACKLIST_AFTER_ROTATION이 비활성화되어 있습니다")

        # Arrange
        tokens = get_tokens
        old_refresh_token = tokens["refresh"]
        refresh_url = reverse("token-refresh")

        # 갱신 전 OutstandingToken 수 확인
        outstanding_before = OutstandingToken.objects.count()
        assert outstanding_before >= 1, f"로그인 시 OutstandingToken이 생성되어야 합니다. 현재: {outstanding_before}"

        # Act - 첫 번째 갱신
        first_refresh_response = api_client.post(refresh_url, {"refresh": old_refresh_token})
        assert first_refresh_response.status_code == status.HTTP_200_OK, f"첫 번째 갱신 실패: {first_refresh_response.data}"

        # 갱신 후 OutstandingToken과 BlacklistedToken 수 확인
        outstanding_after = OutstandingToken.objects.count()
        blacklisted_count = BlacklistedToken.objects.count()

        # ROTATE_REFRESH_TOKENS=True이면 새 토큰이 생성되어 OutstandingToken 수가 증가해야 함
        # BLACKLIST_AFTER_ROTATION=True이면 이전 토큰이 블랙리스트에 추가되어야 함
        assert (
            blacklisted_count >= 1
        ), f"블랙리스트에 토큰이 추가되어야 합니다. Outstanding: {outstanding_before}->{outstanding_after}, Blacklisted: {blacklisted_count}"

        # 중요: Cookie를 삭제하여 이전 토큰(body)만 사용하도록 함
        # api_client가 첫 번째 응답에서 받은 새 Cookie를 자동으로 사용하는 것을 방지
        api_client.cookies.clear()

        # Act - 이전 refresh token으로 다시 갱신 시도 (body로 전달)
        second_refresh_response = api_client.post(refresh_url, {"refresh": old_refresh_token})

        # Assert - 이전 토큰은 블랙리스트되어 사용 불가
        assert (
            second_refresh_response.status_code == status.HTTP_401_UNAUTHORIZED
        ), f"블랙리스트된 토큰은 401을 반환해야 합니다. 응답: {second_refresh_response.data}"

    def test_rotated_refresh_token_success(self, api_client, get_tokens):
        """Refresh Token 회전 후 새 토큰으로 갱신 성공"""
        # Arrange
        tokens = get_tokens
        refresh_url = reverse("token-refresh")

        # ROTATE_REFRESH_TOKENS=True인 경우만 테스트
        if not settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
            pytest.skip("ROTATE_REFRESH_TOKENS가 비활성화되어 있습니다")

        # Act - 첫 번째 갱신으로 새 refresh token 받기 (Cookie에서)
        first_refresh_response = api_client.post(refresh_url, {"refresh": tokens["refresh"]})
        assert first_refresh_response.status_code == status.HTTP_200_OK
        new_refresh_cookie = first_refresh_response.cookies.get("refresh_token")
        assert new_refresh_cookie is not None, "Cookie에 새 refresh token이 없습니다"
        new_refresh_token = new_refresh_cookie.value

        # Act - 새로운 refresh token으로 다시 갱신
        second_refresh_response = api_client.post(refresh_url, {"refresh": new_refresh_token})

        # Assert - 새 토큰으로는 성공해야함
        assert second_refresh_response.status_code == status.HTTP_200_OK
        assert "access" in second_refresh_response.data
        assert "refresh_token" in second_refresh_response.cookies


@pytest.mark.django_db
class TestTokenExpiry:
    """토큰 만료 테스트"""

    def test_valid_access_token_success(self, api_client, get_tokens):
        """정상적인 Access Toekn으로 API 접근 성공"""
        # Arrange
        tokens = get_tokens
        access_token = tokens["access"]
        url = reverse("user-profile")

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        response = api_client.get(url)

        # Assert - 정상적으로 프로필 조회 성공
        assert response.status_code == status.HTTP_200_OK
        assert "username" in response.data
        assert "email" in response.data

    def test_expired_access_token(self, api_client, user):
        """만료된 Access Token으로 API 접근 실패"""
        # Arrange
        url = reverse("user-profile")
        # 이미 만료된 access token 생성
        token = AccessToken.for_user(user)
        token.set_exp(lifetime=timedelta(seconds=-1))  # 1초 전에 만료

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(token)}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_expired_refresh_token(self, api_client, user):
        """만료된 Refresh Token으로 갱신 실패"""
        # Arrange
        url = reverse("token-refresh")
        refresh = RefreshToken.for_user(user)
        refresh.set_exp(lifetime=timedelta(seconds=-1))

        # Act
        response = api_client.post(url, {"refresh": str(refresh)})

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_inactive_user_token(self, api_client, user):
        """비활성화된 사용자의 토큰으로 접근 실패"""
        # Arrange
        url = reverse("user-profile")
        # 토큰 먼저 발급
        token = AccessToken.for_user(user)

        # 사용자 비활성화
        user.is_active = False
        user.save()

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(token)}")
        response = api_client.get(url)

        # Assert - 비활성화된 사용자는 접근 불가
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_with_drawn_user_token(self, api_client, user):
        """탈퇴한 사용자의 토큰으로 접근 실패"""
        # Arrange
        url = reverse("user-profile")
        # 토큰 먼저 발급
        token = AccessToken.for_user(user)

        # 사용자 탈퇴 처리
        user.is_withdrawn = True
        user.withdrawn_at = timezone.now()
        user.is_active = False
        user.save()

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(token)}")
        response = api_client.get(url)

        # Assert - 탈퇴한 사용자는 접근 불가
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestTokenTampering:
    """토큰 변조 테스트 (보안)"""

    def test_tampered_payload(self, api_client, user):
        """변조된 payload (user_id 변경)"""
        # Arrange
        url = reverse("user-profile")
        token = AccessToken.for_user(user)
        token_str = str(token)

        # JWT 구조: header.payload.signature
        parts = token_str.split(".")

        # payload 디코딩 후 user_id 변조
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        payload["user_id"] = 99999  # 존재하지 않는 user_id로 변조

        # 변조된 payload를 다시 인코딩
        tampered_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

        # 변조된 토큰 생성 (signature는 그대로)
        tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tampered_token}")
        response = api_client.get(url)

        # Assert - signature가 맞지 않아 실패해야 함
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_signature(self, api_client, user):
        """잘못된 signature"""
        # Arrange
        url = reverse("user-profile")
        token = AccessToken.for_user(user)
        token_str = str(token)

        # signature 부분을 무작위 문자열로 변조
        parts = token_str.split(".")
        tampered_token = f"{parts[0]}.{parts[1]}.invalidsignature123"

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tampered_token}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_different_user_token(self, api_client, user, second_user):
        """다른 사용자의 토큰으로 접근"""
        # Arrange
        # second_user의 토큰 발급
        url = reverse("auth-login")
        response = api_client.post(url, {"username": "seconduser", "password": "testpass123"})
        user2_token = response.data["token"]["access"]

        # Act - second_user의 토큰으로 user의 프로필 접근 시도
        # (프로필은 자기 자신만 조회 가능)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {user2_token}")
        profile_response = api_client.get(reverse("user-profile"))

        # Assert - second_user의 정보가 나와야 함 (user 정보 아님)
        assert profile_response.status_code == status.HTTP_200_OK
        assert profile_response.data["username"] == "seconduser"
        assert profile_response.data["username"] != user.username


@pytest.mark.django_db
class TestTokenFormat:
    """Authorization 헤더 형식 테스트"""

    def test_missing_bearer(self, api_client, get_tokens):
        """Bearer 문자열 없이 토큰만 전송"""
        # Arrange
        tokens = get_tokens
        url = reverse("user-profile")

        # Act - "Bearer" 없이 토큰만 전송
        api_client.credentials(HTTP_AUTHORIZATION=tokens["access"])
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_bearer_typo(self, api_client, get_tokens):
        """Bearer 철자 오류"""
        # Arrange
        tokens = get_tokens
        url = reverse("user-profile")

        # Act - "Beare" 오타
        api_client.credentials(HTTP_AUTHORIZATION=f"Beare {tokens['access']}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_no_space(self, api_client, get_tokens):
        """Bearer와 토큰 사이 공백 없음"""
        # Arrange
        tokens = get_tokens
        url = reverse("user-profile")

        # Act - 공백 없음
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer{tokens['access']}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_lowercase_bearer(self, api_client, get_tokens):
        """소문자 bearer"""
        # Arrange
        tokens = get_tokens
        url = reverse("user-profile")

        # Act - 소문자 "bearer"
        api_client.credentials(HTTP_AUTHORIZATION=f"bearer {tokens['access']}")
        response = api_client.get(url)

        # Assert
        # DRF는 대소문자 구분할 수 있음
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_multiple_bearer_tokens(self, api_client, get_tokens):
        """여러 개의 Bearer 토큰"""
        # Arrange
        tokens = get_tokens
        url = reverse("user-profile")

        # Act - Bearer 토큰 2개 전송
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']} Bearer {tokens['access']}")
        response = api_client.get(url)

        # Assert - 형식이 잘못되어 실패해야 함
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestTokenBlacklist:
    """토큰 블랙리스트 테스트"""

    def test_logout_blacklists_token(self, api_client, get_tokens):
        """로그아웃 시 토큰 블랙리스트 등록"""
        # Arrange
        tokens = get_tokens
        access_token = tokens["access"]
        refresh_token = tokens["refresh"]

        # 로그인 상태 설정
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        # Act - 로그아웃
        logout_url = reverse("auth-logout")
        logout_response = api_client.post(logout_url, {"refresh": refresh_token})

        # Assert - 로그아웃 성공
        assert logout_response.status_code == status.HTTP_200_OK

        # Act - 로그아웃한 refresh token으로 갱신 시도
        refresh_url = reverse("token-refresh")
        refresh_response = api_client.post(refresh_url, {"refresh": refresh_token})

        # Assert - 블랙리스트된 토큰이므로 실패해야 함
        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_refresh_after_logout(self, api_client, get_tokens):
        """로그아웃 후 토큰 갱신 실패"""
        # Arrange
        tokens = get_tokens
        access_token = tokens["access"]
        refresh_token = tokens["refresh"]

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        # Act - 로그아웃
        logout_url = reverse("auth-logout")
        api_client.post(logout_url, {"refresh": refresh_token})

        # Act - 블랙리스트된 토큰으로 갱신 시도
        refresh_url = reverse("token-refresh")
        response = api_client.post(refresh_url, {"refresh": refresh_token})

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestTokenWithoutAuth:
    """토큰 없이 보호된 엔드포인트 접근"""

    def test_access_profile_without_token(self, api_client):
        """토큰 없이 프로필 접근"""
        # Arrange
        url = reverse("user-profile")

        # Act - Authorization 헤더 없이 요청
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_access_password_change_without_token(self, api_client):
        """토큰 없이 비밀번호 변경 시도"""
        # Arrange
        url = reverse("user-password-change")
        data = {"old_password": "oldpass", "new_password": "newpass123!", "new_password2": "newpass123!"}

        # Act
        response = api_client.post(url, data)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestInvalidTokenFormat:
    """잘못된 형식의 토큰"""

    def test_malformed_token(self, api_client):
        """형식이 완전히 잘못된 토큰"""
        # Arrange
        url = reverse("user-profile")
        malformed_token = "this_is_not_a_valid_jwt_token"

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {malformed_token}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_incomplete_token(self, api_client):
        """불완전한 JWT 토큰 (. 구분자 부족)"""
        # Arrange
        url = reverse("user-profile")
        # JWT는 header.payload.signature 3부분이어야 하는데 2부분만 있음
        incomplete_token = "header.payload"

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {incomplete_token}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_non_json_payload(self, api_client, user):
        """JSON이 아닌 payload"""
        # Arrange
        url = reverse("user-profile")
        token = AccessToken.for_user(user)
        token_str = str(token)
        parts = token_str.split(".")

        # payload를 JSON이 아닌 일반 문자열로 변조
        non_json_payload = base64.urlsafe_b64encode(b"this is not json").decode().rstrip("=")
        tampered_token = f"{parts[0]}.{non_json_payload}.{parts[2]}"

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tampered_token}")
        response = api_client.get(url)

        # Assert - JSON 파싱 실패로 인증 실패
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_null_token(self, api_client):
        """NULL 토큰"""
        # Arrange
        url = reverse("user-profile")

        # Act - None을 전달 (실제로는 빈 헤더)
        api_client.credentials(HTTP_AUTHORIZATION="Bearer ")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_empty_token(self, api_client):
        """빈 문자열 토큰"""
        # Arrange
        url = reverse("user-profile")

        # Act
        api_client.credentials(HTTP_AUTHORIZATION="Bearer ")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_whitespace_only_token(self, api_client):
        """공백만 있는 토큰"""
        # Arrange
        url = reverse("user-profile")

        # Act
        api_client.credentials(HTTP_AUTHORIZATION="Bearer     ")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_very_long_token(self, api_client):
        """매우 긴 토큰 (버퍼 오버플로우 테스트)"""
        # Arrange
        url = reverse("user-profile")
        # 10KB 길이의 토큰
        very_long_token = "a" * 10000

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {very_long_token}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_special_characters_token(self, api_client):
        """특수문자만으로 구성된 토큰"""
        # Arrange
        url = reverse("user-profile")
        special_token = "!@#$%^&*()_+-=[]{}|;:',.<>?/~`"

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {special_token}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestTokenSecurity:
    """토큰 보안 테스트"""

    def test_future_iat_token(self, api_client, user):
        """미래 시간의 iat (issued at)"""
        # Arrange
        url = reverse("user-profile")
        token = AccessToken.for_user(user)

        # iat를 미래 시간으로 변조
        token_str = str(token)
        parts = token_str.split(".")

        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        # 1시간 후의 시간으로 변조
        payload["iat"] = int((timezone.now() + timedelta(hours=1)).timestamp())

        # 변조된 payload를 다시 인코딩
        tampered_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tampered_token}")
        response = api_client.get(url)

        # Assert - signature가 맞지 않아 실패해야 함
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestTokenVerify:
    """토큰 검증 엔드포인트 테스트"""

    def test_verify_valid_token(self, api_client, get_tokens):
        """유효한 토큰 검증"""
        # Arrange
        tokens = get_tokens
        access_token = tokens["access"]

        # Note: token-verify 엔드포인트가 있는 경우에만 테스트
        # 없다면 이 테스트는 skip
        try:
            url = reverse("token-verify")
        except:
            pytest.skip("token-verify 엔드포인트가 없습니다")

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK

    def test_verify_invalid_token(self, api_client):
        """잘못된 토큰 검증"""
        # Arrange
        try:
            url = reverse("token-verify")
        except:
            pytest.skip("token-verify 엔드포인트가 없습니다")

        invalid_token = "invalid_token_123"

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {invalid_token}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_verify_expired_token(self, api_client, user):
        """만료된 토큰 검증"""
        # Arrange
        try:
            url = reverse("token-verify")
        except:
            pytest.skip("token-verify 엔드포인트가 없습니다")

        # 만료된 토큰 생성
        token = AccessToken.for_user(user)
        token.set_exp(lifetime=timedelta(seconds=-1))

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(token)}")
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
