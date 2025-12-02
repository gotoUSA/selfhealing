from unittest.mock import patch

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.email_verification import EmailVerificationToken
from shopping.models.user import User


@pytest.mark.django_db
class TestRegistrationSuccess:
    """정상 회원가입 테스트"""

    def test_register_with_valid_data(self, api_client, valid_registration_data):
        """올바른 데이터로 회원가입 성공"""
        # Arrange
        url = reverse("auth-register")

        # Act
        response = api_client.post(url, valid_registration_data, format="json")

        # Assert - 201 Created 응답 확인
        assert response.status_code == status.HTTP_201_CREATED

        # 응답 데이터 구조 확인 (새 구조)
        assert "token" in response.data
        assert "access" in response.data["token"]
        # refresh는 HTTP Only Cookie로 전달됨
        assert "refresh_token" in response.cookies
        assert "user" in response.data
        assert "message" in response.data

        # 사용자 정보 확인 (최소 정보만 반환)
        assert response.data["user"]["username"] == "newuser"
        assert response.data["user"]["email"] == "newuser@example.com"

    def test_jwt_tokens_issued(self, api_client, registration_data_factory):
        """회원가입 시 JWT 토큰 발급 확인"""
        # Arrange
        url = reverse("auth-register")
        data = registration_data_factory(username="tokenuser", email="token@example.com")

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - Access 토큰이 문자열이고 비어있지 않은지 확인 (새 구조)
        assert isinstance(response.data["token"]["access"], str)
        assert len(response.data["token"]["access"]) > 0

        # Refresh 토큰은 Cookie에서 확인
        refresh_cookie = response.cookies.get("refresh_token")
        assert refresh_cookie is not None
        assert len(refresh_cookie.value) > 0

    def test_user_created_in_database(self, api_client, registration_data_factory):
        """DB에 사용자 실제 생성 확인"""
        # Arrange
        url = reverse("auth-register")
        data = registration_data_factory(username="dbuser", email="dbuser@example.com")

        # 회원가입 전 사용자 수
        user_count_before = User.objects.count()

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 회원가입 후 사용자 수 증가 확인
        assert User.objects.count() == user_count_before + 1

        # DB에서 사용자 조회
        user = User.objects.get(username="dbuser")
        assert user.email == "dbuser@example.com"
        assert user.check_password("testpass123!")  # 비밀번호 해시 확인

    def test_initial_email_verified_false(self, api_client, registration_data_factory):
        """회원가입 직후 is_email_verified=False"""
        # Arrange
        url = reverse("auth-register")
        data = registration_data_factory(username="unverifuser", email="unverif@example.com")

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - DB에서 사용자 조회
        user = User.objects.get(username="unverifuser")
        assert user.is_email_verified is False

    def test_initial_points_zero(self, api_client, registration_data_factory):
        """초기 포인트 0 확인"""
        # Arrange
        url = reverse("auth-register")
        data = registration_data_factory(username="pointuser", email="point@example.com")

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        user = User.objects.get(username="pointuser")
        assert user.points == 0

    def test_initial_membership_level_bronze(self, api_client, registration_data_factory):
        """초기 등급 bronze 확인"""
        # Arrange
        url = reverse("auth-register")
        data = registration_data_factory(username="leveluser", email="level@example.com")

        # Act
        _ = api_client.post(url, data, format="json")

        # Assert
        user = User.objects.get(username="leveluser")
        assert user.membership_level == "bronze"

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_verification_email_sent(self, mock_email_task, api_client):
        """회원가입 시 인증 이메일 자동 발송"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "emailuser",
            "email": "emailuser@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Arrange - Celery 태스크가 호출되었는지 확인
        assert mock_email_task.called
        assert mock_email_task.call_count == 1

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_email_verification_token_created(self, mock_email_task, api_client):
        """회원가입 시 EmailVerificationToken 자동 생성"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "tokengenuser",
            "email": "tokengen@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        user = User.objects.get(username="tokengenuser")

        # 이메일 인증 토큰 생성 확인
        token = EmailVerificationToken.objects.filter(user=user, is_used=False).first()
        assert token is not None
        assert token.user == user

    def test_optional_fields_included(self, api_client):
        """선택 필드 포함 회원가입"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "fulluser",
            "email": "fulluser@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
            "first_name": "길동",
            "last_name": "홍",
            "phone_number": "010-1234-5678",
            "agree_marketing_email": True,
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_201_CREATED

        user = User.objects.get(username="fulluser")
        assert user.first_name == "길동"
        assert user.last_name == "홍"
        assert user.phone_number == "010-1234-5678"
        assert user.agree_marketing_email is True


@pytest.mark.django_db
class TestBoundaryValues:
    """경계값 테스트"""

    def test_username_max_length_150(self, api_client):
        """username 정확히 150자 (허용)"""
        # Arrange
        url = reverse("auth-register")
        long_username = "a" * 150
        data = {
            "username": long_username,
            "email": "maxlength@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 150자는 허용되어야 함
        assert response.status_code == status.HTTP_201_CREATED

    def test_username_exceeds_150(self, api_client):
        """username 151자 초과 (거부)"""
        # Arrange
        url = reverse("auth-register")
        too_long_username = "a" * 151
        data = {
            "username": too_long_username,
            "email": "toolong@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 151자는 거부되어야 함
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "username" in response.data

    def test_email_max_length_254(self, api_client):
        """email 정확히 254자 (허용)"""
        # Arrange
        url = reverse("auth-register")
        # 254자 이메일 생성: "a"*240 + "@example.com" = 254자
        long_email = "a" * 240 + "@example.com"
        data = {
            "username": "emailmax",
            "email": long_email,
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_201_CREATED

    def test_email_exceeds_254(self, api_client):
        """email 255자 초과 (거부)"""
        # Arrange
        url = reverse("auth-register")
        # 255자 이메일 생성: "a" * 243 + "@example.com" = 243 + 12 = 255자
        # (참고: "a" * 241 + "@example.com" = 253자로 254 미만이라 통과됨)
        too_long_email = "a" * 243 + "@example.com"
        data = {
            "username": "emailtoolong",
            "email": too_long_email,
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 255자 초과 이메일은 거부되어야 함
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data


@pytest.mark.django_db
class TestDuplicateValidation:
    """중복 검증 테스트"""

    def test_duplicate_username(self, api_client, user):
        """중복 username으로 가입 실패"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": user.username,  # 기존 사용자의 username
            "email": "newemail@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "username" in response.data
        assert "이미 존재합니다" in str(response.data["username"]) or "이미 사용중인" in str(response.data["username"])

    def test_duplicate_email(self, api_client, user):
        """중복 email으로 가입 실패"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "newusername",
            "email": user.email,  # 기존 사용자의 email
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data
        assert "이미 사용중인" in str(response.data["email"])


@pytest.mark.django_db
class TestEmailValidation:
    """이메일 형식 검증"""

    def test_invalid_email_format_no_at(self, api_client):
        """@ 없는 이메일"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "noatuser",
            "email": "invalid.email.com",  # @ 없음
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_invalid_email_format_no_domain(self, api_client):
        """도메인 없는 이메일"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "nodomainuser",
            "email": "invalid@",  # 도메인 없음
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_invalid_email_consecutive_dots(self, api_client):
        """연속된 점"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "nodomainuser",
            "email": "invalid@",  # 도메인 없음
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data


@pytest.mark.django_db
class TestPasswordValidation:
    """비밀번호 검증"""

    def test_short_password(self, api_client):
        """짧은 비밀번호 (8자 미만)"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "shortpw",
            "email": "shortpw@example.com",
            "password": "short1",  # 6자
            "password2": "short1",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in response.data

    def test_numeric_only_password(self, api_client):
        """숫자만 있는 비밀번호"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "numericpw",
            "email": "numeric@example.com",
            "password": "12345678",  # 숫자만
            "password2": "12345678",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Arrange - Django의 NumericPasswordValidator가 거부
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in response.data

    def test_password_mismatch(self, api_client):
        """password와 password2 불일치"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "mismatchpw",
            "email": "mismatch@example.com",
            "password": "testpass123!",
            "password2": "different123!",  # 다른 비밀번호
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in response.data or "non_field_errors" in response.data


@pytest.mark.django_db
class TestRequiredFields:
    """필수 필드 검증"""

    def test_missing_username(self, api_client):
        """username 누락"""
        # Arrange
        url = reverse("auth-register")
        data = {
            # username 없음
            "email": "nouser@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "username" in response.data

    def test_missing_email(self, api_client):
        """email 누락"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "noemail",
            # email 없음
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_missing_password(self, api_client):
        """password 누락"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "nopassword",
            "email": "nopw@example.com",
            # password 없음
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in response.data


@pytest.mark.django_db
class TestEmptyInputs:
    """빈 문자열 검증"""

    def test_empty_username(self, api_client):
        """빈 username"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "",  # 빈 문자열
            "email": "empty@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "username" in response.data

    def test_empty_email(self, api_client):
        """빈 email"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "emptyemail",
            "email": "",  # 빈 문자열
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_whitespace_only(self, api_client):
        """공백만 있는 username"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "   ",  # 공백만
            "email": "space@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestPhoneValidation:
    """전화번호 형식 검증"""

    def test_invalid_phone_format_no_hyphens(self, api_client):
        """하이픈 없는 전화번호 (잘못된 형식)"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "phonenohyphen",
            "email": "phonenohyphen@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
            "phone_number": "01012345678",  # 하이픈 없음
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "phone_number" in response.data

    def test_invalid_phone_format_wrong_length(self, api_client):
        """자릿수가 틀린 전화번호"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "phonewrong",
            "email": "phonewrong@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
            "phone_number": "010-1234-567",  # 마지막 자리 부족
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "phone_number" in response.data

    def test_phone_with_letters(self, api_client):
        """문자가 포함된 전화번호"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "phoneletters",
            "email": "phoneletters@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
            "phone_number": "010-abcd-5678",  # 문자 포함
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "phone_number" in response.data

    def test_valid_phone_format(self, api_client):
        """올바른 전화번호 형식"""
        # Arrange
        url = reverse("auth-register")
        data = {
            "username": "phonevalid",
            "email": "phonevalid@example.com",
            "password": "testpass123!",
            "password2": "testpass123!",
            "phone_number": "010-1234-5678",  # 올바른 형식
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        user = User.objects.get(username="phonevalid")
        assert user.phone_number == "010-1234-5678"



