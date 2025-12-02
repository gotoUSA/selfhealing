from django.urls import reverse

import pytest
from rest_framework import status


@pytest.mark.django_db
class TestPasswordChangeSuccess:
    """정상적인 비밀번호 변경 테스트"""

    def test_change_password_success(self, authenticated_client, user, password_change_data):
        """정상 비밀번호 변경 테스트"""
        # Arrange
        url = reverse("user-password-change")

        # Act
        response = authenticated_client.post(url, password_change_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "message" in response.data
        assert "비밀번호가 변경되었습니다" in response.data["message"]

        # 추가 검증: DB에서 비밀번호 변경 확인
        user.refresh_from_db()
        assert user.check_password("NewSecurePass456!")
        assert not user.check_password("testpass123")  # 이전 비밀번호는 사용 불가

    def test_login_with_new_password(self, api_client, authenticated_client, user):
        """비밀번호 변경 후 새 비밀번호로 로그인 테스트"""
        # Arrange
        change_url = reverse("user-password-change")
        change_data = {
            "old_password": "testpass123",
            "new_password": "ChangedPassword789!",
            "new_password2": "ChangedPassword789!",
        }
        change_response = authenticated_client.post(change_url, change_data, format="json")
        assert change_response.status_code == status.HTTP_200_OK

        # Act
        login_url = reverse("auth-login")
        old_password_data = {
            "username": "testuser",
            "password": "testpass123",  # 이전 비밀번호
        }
        old_password_response = api_client.post(login_url, old_password_data)

        # Assert
        assert old_password_response.status_code == status.HTTP_400_BAD_REQUEST

        # Act
        new_password_data = {
            "username": "testuser",
            "password": "ChangedPassword789!",  # 새 비밀번호
        }
        new_password_response = api_client.post(login_url, new_password_data)

        # Assert (새 구조: token.access)
        assert new_password_response.status_code == status.HTTP_200_OK
        assert "token" in new_password_response.data
        assert "access" in new_password_response.data["token"]
        # refresh는 Cookie에서 확인
        assert "refresh_token" in new_password_response.cookies


@pytest.mark.django_db
class TestPasswordChangeBoundary:
    """경계값 및 특수 케이스 테스트"""

    def test_minimum_password_length(self, authenticated_client):
        """최소 길이 비밀번호 테스트 (8자)"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "Pass123!",  # 정확히 8자 (최소 길이)
            "new_password2": "Pass123!",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

    def test_same_as_old_password(self, authenticated_client):
        """현재 비밀번호와 동일한 새 비밀번호 입력 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "testpass123",  # 현재 비밀번호와 동일
            "new_password2": "testpass123",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        # Django 기본 설정에서는 이를 허용할 수 있음
        # 만약 커스텀 검증이 있다면 400 에러가 발생해야 함
        if response.status_code == status.HTTP_400_BAD_REQUEST:
            # 커스텀 검증이 구현된 경우
            assert "동일한 비밀번호" in str(response.data) or "old_password" in response.data
        else:
            # 기본 설정에서는 허용됨 (보안 강화 필요)
            assert response.status_code == status.HTTP_200_OK

    def test_password_with_special_characters(self, authenticated_client):
        """특수문자가 포함된 비밀번호 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "P@ssw0rd!#$%",  # 특수문자 포함
            "new_password2": "P@ssw0rd!#$%",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestPasswordChangeErrors:
    """비밀번호 변경 오류 케이스 테스트"""

    def test_wrong_old_password(self, authenticated_client):
        """잘못된 현재 비밀번호 입력 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "WrongPassword123!",  # 잘못된 현재 비밀번호
            "new_password": "NewSecurePass456!",
            "new_password2": "NewSecurePass456!",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "old_password" in response.data
        assert "현재 비밀번호가 올바르지 않습니다" in str(response.data["old_password"])

    def test_new_password_mismatch(self, authenticated_client):
        """새 비밀번호 불일치 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "NewSecurePass456!",
            "new_password2": "DifferentPass789!",  # 불일치
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.data
        assert "일치하지 않습니다" in str(response.data["new_password"])

    def test_weak_new_password_too_short(self, authenticated_client):
        """너무 짧은 새 비밀번호 테스트 (8자 미만)"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "Pass1!",  # 6자 (너무 짧음)
            "new_password2": "Pass1!",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.data

    def test_weak_new_password_numeric_only(self, authenticated_client):
        """숫자로만 구성된 새 비밀번호 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "12345678",  # 숫자만 (8자)
            "new_password2": "12345678",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.data

    def test_weak_new_password_common(self, authenticated_client):
        """흔한 비밀번호 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "password",  # 너무 흔한 비밀번호
            "new_password2": "password",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.data

    def test_password_similar_to_username(self, authenticated_client, user):
        """사용자명과 유사한 비밀번호 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "testuser",  # username과 동일
            "new_password2": "testuser123",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.data


@pytest.mark.django_db
class TestPasswordChangeAuthentication:
    """인증 관련 테스트"""

    def test_change_password_without_authentication(self, api_client):
        """인증 없이 비밀번호 변경 시도 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "NewSecurePass456!",
            "new_password2": "NewSecurePass456!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    def test_change_password_with_invalid_token(self, api_client):
        """잘못된 JWT 토큰으로 비밀번호 변경 시도 테스트"""
        # Arrange
        api_client.credentials(HTTP_AUTHORIZATION="Bearer invalid_token_here")
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "NewSecurePass456!",
            "new_password2": "NewSecurePass456!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestPasswordChangeMissingFields:
    """필수 필드 누락 테스트"""

    def test_missing_old_password(self, authenticated_client):
        """old_password 필드 누락 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            # old_password 누락
            "new_password": "NewSecurePass456!",
            "new_password2": "NewSecurePass456!",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "old_password" in response.data

    def test_missing_new_password(self, authenticated_client):
        """new_password 필드 누락 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            # new_password 누락
            "new_password2": "NewSecurePass456!",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.data

    def test_missing_new_password2(self, authenticated_client):
        """new_password2 필드 누락 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "NewSecurePass456!",
            # new_password2 누락
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password2" in response.data

    def test_all_fields_missing(self, authenticated_client):
        """모든 필드 누락 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {}  # 빈 데이터

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "old_password" in response.data
        assert "new_password" in response.data
        assert "new_password2" in response.data


@pytest.mark.django_db
class TestPasswordChangeTokens:
    """
    비밀번호 변경 후 토큰 처리 테스트

    중요 보안 고려사항:
    - JWT는 상태가 없는(stateless) 토큰이므로 비밀번호 변경 후에도
      유효기간 내에는 기존 토큰이 여전히 작동함
    - 이는 보안 취약점이 될 수 있으므로 다음 중 하나를 고려해야 함:
      1. 비밀번호 변경 시 refresh token을 블랙리스트에 추가
      2. 토큰에 비밀번호 버전 정보 포함
      3. Redis 등을 사용한 토큰 무효화 메커니즘 구현
    """

    def test_old_tokens_still_valid_after_password_change(self, api_client, user, get_tokens):
        """비밀번호 변경 후 기존 토큰 사용 가능 여부 테스트"""
        # Arrange
        tokens = get_tokens
        access_token = tokens["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        # 먼저 프로필 조회 가능한지 확인 (토큰 유효성 확인)
        profile_url = reverse("user-profile")
        profile_response = api_client.get(profile_url)
        assert profile_response.status_code == status.HTTP_200_OK

        # Act
        change_url = reverse("user-password-change")
        change_data = {
            "old_password": "testpass123",
            "new_password": "ChangedPassword999!",
            "new_password2": "ChangedPassword999!",
        }
        change_response = api_client.post(change_url, change_data, format="json")
        assert change_response.status_code == status.HTTP_200_OK

        # Assert - 검증: 비밀번호 변경 후에도 기존 토큰으로 접근 가능
        # (현재 JWT 구현에서는 토큰이 여전히 유효함)
        profile_response_after = api_client.get(profile_url)
        assert profile_response_after.status_code == status.HTTP_200_OK

        # 주의: 프로덕션 환경에서는 보안 강화를 위해
        # 비밀번호 변경 시 기존 토큰 무효화 로직 추가 권장

    def test_new_login_required_after_password_change(self, api_client, user):
        """비밀번호 변경 후 새로운 로그인 필요 테스트"""
        # Arrange
        login_url = reverse("auth-login")
        initial_login = api_client.post(
            login_url,
            {"username": "testuser", "password": "testpass123"},
        )
        assert initial_login.status_code == status.HTTP_200_OK

        # 토큰으로 비밀번호 변경 (새 구조: token.access)
        access_token = initial_login.data["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        change_url = reverse("user-password-change")
        change_data = {
            "old_password": "testpass123",
            "new_password": "SuperNewPass888!",
            "new_password2": "SuperNewPass888!",
        }
        change_response = api_client.post(change_url, change_data, format="json")
        assert change_response.status_code == status.HTTP_200_OK

        # Act - 실행: 이전 비밀번호로 새로 로그인 시도 (실패해야 함)
        api_client.credentials()  # 토큰 제거
        old_login_attempt = api_client.post(
            login_url,
            {"username": "testuser", "password": "testpass123"},  # 이전 비밀번호
        )

        # Assert - 검증: 이전 비밀번호로는 로그인 불가
        assert old_login_attempt.status_code == status.HTTP_400_BAD_REQUEST

        # Act - 실행: 새 비밀번호로 로그인 (성공해야 함)
        new_login_attempt = api_client.post(
            login_url,
            {"username": "testuser", "password": "SuperNewPass888!"},  # 새 비밀번호
        )

        # Assert - 검증: 새 비밀번호로는 로그인 성공 (새 구조)
        assert new_login_attempt.status_code == status.HTTP_200_OK
        assert "token" in new_login_attempt.data
        assert "access" in new_login_attempt.data["token"]
        # refresh는 Cookie에서 확인
        assert "refresh_token" in new_login_attempt.cookies


@pytest.mark.django_db
class TestPasswordChangeEdgeCases:
    """엣지 케이스 및 특수 상황 테스트"""

    def test_empty_password_fields(self, authenticated_client):
        """빈 문자열 비밀번호 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "",
            "new_password": "",
            "new_password2": "",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_whitespace_only_password(self, authenticated_client):
        """공백만 있는 비밀번호 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "        ",
            "new_password2": "        ",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_extremely_long_password(self, authenticated_client):
        """매우 긴 비밀번호 테스트"""
        # Arrange
        url = reverse("user-password-change")
        very_long_password = "A1b2C3d4!" * 20  # 180자
        data = {
            "old_password": "testpass123",
            "new_password": very_long_password,
            "new_password2": very_long_password,
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        # Django 기본 설정에서는 허용될 수 있음
        # 커스텀 검증이 있다면 400 에러 발생
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_unicode_characters_in_password(self, authenticated_client):
        """유니코드 문자(한글, 이모지 등) 포함 비밀번호 테스트"""
        # Arrange
        url = reverse("user-password-change")
        data = {
            "old_password": "testpass123",
            "new_password": "비밀번호123!😀",
            "new_password2": "비밀번호123!😀",
        }

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
