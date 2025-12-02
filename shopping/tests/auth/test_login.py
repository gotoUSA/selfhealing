from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status


@pytest.mark.django_db
class TestLoginSuccess:
    """로그인 성공 시나리오 테스트"""

    def test_login_with_valid_credentials(self, api_client, user, valid_login_data):
        """정상 로그인"""
        # Arrange
        login_url = reverse("auth-login")

        # Act
        response = api_client.post(login_url, valid_login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        response_data = response.json()

        # JWT 토큰 발급 확인 (새 구조: token.access)
        assert "token" in response_data
        assert "access" in response_data["token"]
        assert response_data["token"]["access"]  # 토큰이 비어있지 않은지

        # Refresh 토큰은 HTTP Only Cookie로 전달됨
        assert "refresh_token" in response.cookies

        # 사용자 정보 확인
        assert "user" in response_data
        assert response_data["user"]["username"] == "testuser"

    def test_login_response_structure(self, api_client, user, valid_login_data):
        """로그인 응답 구조 검증"""
        # Arrange
        login_url = reverse("auth-login")

        # Act
        response = api_client.post(login_url, valid_login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        response_data = response.json()

        # 최상위 필수 키 확인 (새 구조)
        required_keys = ["token", "user", "message"]
        for key in required_keys:
            assert key in response_data, f"응답에 '{key}' 키가 없습니다"

        # token 객체 내 access 확인
        assert "access" in response_data["token"], "token 객체에 'access' 키가 없습니다"

        # 성공 메세지 확인
        assert response_data["message"] == "로그인 되었습니다."

        # user 객체 필드 확인 (최소 정보만 반환)
        user_data = response_data["user"]
        user_required_fields = ["username", "email"]
        for field in user_required_fields:
            assert field in user_data, f"user 객체에 '{field}' 필드가 없습니다"

        # 민감 정보 제외 확인
        assert "password" not in user_data, "응답에 비밀번호가 포함되어서는 안됩니다"

    def test_login_jwt_tokens_format(self, api_client, user, valid_login_data):
        """JWT 토큰 형식 검증"""
        # Arrange
        login_url = reverse("auth-login")

        # Act
        response = api_client.post(login_url, valid_login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        response_data = response.json()

        # Access 토큰 타입 확인 (새 구조: token.access)
        assert isinstance(response_data["token"]["access"], str)

        # JWT 형식 확인 (header.payload.signature)
        access_parts = response_data["token"]["access"].split(".")
        assert len(access_parts) == 3, "Access 토큰이 JWT 형식이 아닙니다"

        # Refresh 토큰은 Cookie에서 확인
        refresh_cookie = response.cookies.get("refresh_token")
        assert refresh_cookie is not None, "Refresh 토큰이 Cookie에 없습니다"
        refresh_parts = refresh_cookie.value.split(".")
        assert len(refresh_parts) == 3, "Refresh 토큰이 JWT 형식이 아닙니다"


@pytest.mark.django_db
class TestLoginFailure:
    """로그인 실패 시나리오 테스트"""

    def test_login_wrong_password(self, api_client, user):
        """잘못된 비밀번호"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {
            "username": "testuser",
            "password": "wrongpassword123",  # 잘못된 비밀번호
        }

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response_data = response.json()

        # 에러 메시지 확인 (non_field_errors 또는 detail에 포함)
        error_message = str(response_data)
        assert "아이디 또는 비밀번호가 올바르지 않습니다" in error_message

    def test_login_nonexistent_user(self, api_client):
        """존재하지 않는 사용자"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {
            "username": "nonexistent_user",  # 존재하지 않는 사용자
            "password": "somepassword123",
        }

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response_data = response.json()
        error_message = str(response_data)
        assert "아이디 또는 비밀번호가 올바르지 않습니다" in error_message

    def test_login_withdrawn_user(self, api_client, withdrawn_user):
        """탈퇴한 사용자"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {
            "username": "withdrawn_user",  # withdrawn_user fixture 사용
            "password": "testpass123",
        }

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response_data = response.json()
        error_message = str(response_data)
        # 탈퇴 회원은 is_active=False이므로 authenticate가 실패
        assert "아이디 또는 비밀번호가 올바르지 않습니다" in error_message

    def test_login_inactive_user(self, api_client, inactive_user):
        """비활성화된 사용자"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {
            "username": "inactive_user",  # inactive_user fixture 사용
            "password": "testpass123",
        }

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response_data = response.json()
        error_message = str(response_data)
        # 비활성 계정은 is_active=False이므로 authenticate가 실패
        assert "아이디 또는 비밀번호가 올바르지 않습니다" in error_message


@pytest.mark.django_db
class TestLoginValidation:
    """로그인 입력값 검증 테스트"""

    def test_empty_username(self, api_client):
        """빈 username"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {
            "username": "",  # 빈 문자열
            "password": "testpass123",
        }

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response_data = response.json()
        # DRF가 필드 레벨에서 검증
        assert "username" in response_data
        error_message = str(response_data["username"])
        assert "blank" in error_message or "필수" in error_message

    def test_empty_password(self, api_client):
        """빈 password"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {
            "username": "testuser",
            "password": "",  # 빈 문자열
        }

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response_data = response.json()
        # DRF가 필드 레벨에서 검증
        assert "password" in response_data
        error_message = str(response_data["password"])
        assert "blank" in error_message or "필수" in error_message

    def test_missing_username_field(self, api_client):
        """username 필드 자체가 없을 때 로그인 실패"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {
            # username 필드 누락
            "password": "testpass123",
        }

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response_data = response.json()
        # DRF는 필수 필드 누락 시 해당 필드 이름으로 에러 반환
        assert "username" in response_data or "non_field_errors" in response_data

    def test_missing_password_field(self, api_client):
        """password 필드 자체가 없을 때 로그인 실패"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {
            "username": "testuser",
            # password 필드 누락
        }

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response_data = response.json()
        assert "password" in response_data or "non_field_errors" in response_data

    def test_case_sensitive_username(self, api_client, user):
        """username 대소문자 구분 확인"""
        # Arrange
        login_url = reverse("auth-login")

        # Act & Assert 1 - 올바른 대소문자 (성공)
        correct_login_data = {
            "username": "testuser",  # 정확한 대소문자
            "password": "testpass123",
        }
        response = api_client.post(login_url, correct_login_data, format="json")
        assert response.status_code == status.HTTP_200_OK

        # Act & Assert 2 - 잘못된 대소문자 (실패)
        wrong_case_login_data = {
            "username": "TestUser",  # 대소문자 다름
            "password": "testpass123",
        }
        response = api_client.post(login_url, wrong_case_login_data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestLoginMetadata:
    """로그인 시 메타데이터 업데이트 테스트"""

    def test_last_login_updated(self, api_client, user):
        """로그인 시 last_login 시간이 업데이트되는지 확인"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {"username": "testuser", "password": "testpass123"}

        # 로그인 전 시간 저장
        old_last_login = user.last_login

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # DB에서 최신 정보 가져오기
        user.refresh_from_db()

        # last_login이 업데이트되었는지 확인
        assert user.last_login is not None

        # 이전 로그인 시간이 있었다면, 새 시간이 더 최근인지 확인
        if old_last_login:
            assert user.last_login > old_last_login

        # 현재 시간과 비교 (로그인 시간이 현재 시간보다 크게 과거가 아닌지)
        now = timezone.now()
        time_diff = (now - user.last_login).total_seconds()
        assert time_diff < 5, "로그인 시간이 너무 과거입니다"

    def test_last_login_ip_recorded(self, api_client, user):
        """로그인 시 IP 주소가 기록되는지 확인"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {"username": "testuser", "password": "testpass123"}

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # DB에서 최신 정보 가져오기
        user.refresh_from_db()

        # IP가 기록되었는지 확인
        assert user.last_login_ip is not None

        # IP 형식 간단 검증 (빈 문자열이 아닌지)
        assert len(user.last_login_ip) > 0

    def test_last_login_ip_with_forwarded_header(self, api_client, user):
        """X-Forwarded-For 헤더가 있을 때 IP 주소 기록 확인"""
        # Arrange
        login_url = reverse("auth-login")
        login_data = {"username": "testuser", "password": "testpass123"}

        # X-Forwarded-For 헤더 추가
        # 형식: "client_ip, proxy1_ip, proxy2_ip"
        client_ip = "203.0.113.1"

        # Act
        response = api_client.post(
            login_url, login_data, format="json", HTTP_X_FORWARDED_FOR=f"{client_ip}, 10.0.0.1, 10.0.0.2"
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # DB에서 최신 정보 가져오기
        user.refresh_from_db()

        # 첫 번째 IP(실제 클라이언트 IP)가 기록되었는지 확인
        assert user.last_login_ip == client_ip
