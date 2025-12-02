from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from shopping.models.password_reset import PasswordResetToken


@pytest.mark.django_db
class TestPasswordResetRequest:
    """비밀번호 재설정 요청 - 정상 케이스"""

    def test_request_password_reset_success(self, api_client, user):
        """유효한 이메일로 재설정 요청 성공"""
        # Arrange
        url = reverse("password-reset-request")
        data = {"email": user.email}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 응답 확인
        assert response.status_code == status.HTTP_200_OK
        assert "비밀번호 재설정 링크가 발송되었습니다" in response.data["message"]

        # Assert - 토큰 생성 확인
        token = PasswordResetToken.objects.filter(user=user, is_used=False).first()
        assert token is not None

        # Assert - 이메일 발송 확인 (Celery task가 호출되었는지)
        # 실제 환경에서는 Celery가 비동기로 처리
        # 테스트 환경에서는 CELERY_TASK_ALWAYS_EAGER=True 설정으로 동기 실행

    def test_request_password_reset_case_insensitive_email(self, api_client, user):
        """대소문자 구분 없이 이메일로 요청"""
        # Arrange
        url = reverse("password-reset-request")
        # 대문자로 이메일 전송
        data = {"email": user.email.upper()}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 성공해야 함 (이메일은 소문자로 변환됨)
        assert response.status_code == status.HTTP_200_OK

        # Assert - 토큰 생성 확인
        token = PasswordResetToken.objects.filter(user=user, is_used=False).first()
        assert token is not None


@pytest.mark.django_db
class TestPasswordResetRequestSecurity:
    """비밀번호 재설정 요청 - 보안 테스트"""

    def test_request_with_nonexistent_email(self, api_client):
        """존재하지 않는 이메일로 요청 (계정 존재 여부 노출 방지)"""
        # Arrange
        url = reverse("password-reset-request")
        data = {"email": "nonexistent@example.com"}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 같은 메시지 반환 (보안)
        assert response.status_code == status.HTTP_200_OK
        assert "비밀번호 재설정 링크가 발송되었습니다" in response.data["message"]

        # Assert - 실제로는 토큰이 생성되지 않음
        token_count = PasswordResetToken.objects.count()
        assert token_count == 0

    def test_request_for_withdrawn_user(self, api_client, withdrawn_user):
        """탈퇴한 사용자는 재설정 불가"""
        # Arrange
        url = reverse("password-reset-request")
        data = {"email": withdrawn_user.email}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "탈퇴한 회원" in str(response.data)

    def test_request_for_inactive_user(self, api_client, inactive_user):
        """비활성화된 사용자는 재설정 불가"""
        # Arrange
        url = reverse("password-reset-request")
        data = {"email": inactive_user.email}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "비활성화된 계정" in str(response.data)


@pytest.mark.django_db
class TestPasswordResetRequestValidation:
    """비밀번호 재설정 요청 - 입력 검증"""

    def test_request_without_email(self, api_client):
        """이메일 없이 요청"""
        # Arrange
        url = reverse("password-reset-request")
        data = {}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_request_with_invalid_email_format(self, api_client):
        """잘못된 이메일 형식"""
        # Arrange
        url = reverse("password-reset-request")
        data = {"email": "not-an-email"}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data


@pytest.mark.django_db
class TestPasswordResetConfirm:
    """비밀번호 재설정 확인 - 정상 케이스"""

    def test_confirm_password_reset_success(self, api_client, user, password_reset_confirm_data_factory):
        """유효한 토큰으로 비밀번호 재설정 성공"""
        # Arrange
        raw_token = PasswordResetToken.generate_token(user=user)
        url = reverse("password-reset-confirm")
        data = password_reset_confirm_data_factory(token=raw_token)

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 응답 확인
        assert response.status_code == status.HTTP_200_OK
        assert "비밀번호가 성공적으로 변경되었습니다" in response.data["message"]
        assert "username" in response.data  # 로그인 시 사용

        # Assert - 비밀번호 변경 확인
        user.refresh_from_db()
        assert user.check_password(data["new_password"])
        assert not user.check_password("testpass123")  # 이전 비밀번호는 안됨

        # Assert - 토큰 사용 처리 확인
        token_obj = PasswordResetToken.objects.get(user=user, is_used=True)
        assert token_obj is not None
        assert token_obj.used_at is not None

    def test_login_with_new_password_after_reset(
        self, api_client, user, password_reset_confirm_data_factory, login_data_factory
    ):
        """재설정 후 새 비밀번호로 로그인"""
        # Arrange - 비밀번호 재설정
        raw_token = PasswordResetToken.generate_token(user=user)
        confirm_url = reverse("password-reset-confirm")
        new_password = "ResetPassword456!"
        confirm_data = password_reset_confirm_data_factory(token=raw_token, new_password=new_password)
        confirm_response = api_client.post(confirm_url, confirm_data, format="json")
        assert confirm_response.status_code == status.HTTP_200_OK

        # Act - 새 비밀번호로 로그인
        login_url = reverse("auth-login")
        login_data = login_data_factory(username=user.username, password=new_password)
        login_response = api_client.post(login_url, login_data, format="json")

        # Assert (새 구조: token.access)
        assert login_response.status_code == status.HTTP_200_OK
        assert "token" in login_response.data
        assert "access" in login_response.data["token"]
        # refresh는 Cookie에서 확인
        assert "refresh_token" in login_response.cookies

    def test_old_password_invalid_after_reset(self, api_client, user, password_reset_confirm_data_factory, login_data_factory):
        """재설정 후 이전 비밀번호는 사용 불가"""
        # Arrange - 비밀번호 재설정
        old_password = "testpass123"  # user fixture의 기본 비밀번호
        raw_token = PasswordResetToken.generate_token(user=user)
        confirm_url = reverse("password-reset-confirm")
        confirm_data = password_reset_confirm_data_factory(token=raw_token, new_password="NewPassword789!")
        api_client.post(confirm_url, confirm_data, format="json")

        # Act - 이전 비밀번호로 로그인 시도
        login_url = reverse("auth-login")
        login_data = login_data_factory(username=user.username, password=old_password)
        login_response = api_client.post(login_url, login_data, format="json")

        # Assert - 실패해야 함
        assert login_response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestPasswordResetConfirmValidation:
    """비밀번호 재설정 확인 - 입력 검증"""

    def test_confirm_with_invalid_token(self, api_client):
        """잘못된 토큰으로 재설정 시도"""
        # Arrange
        url = reverse("password-reset-confirm")
        data = {
            "email": "test@example.com",  # 이메일 필드 추가
            "token": "00000000-0000-0000-0000-000000000000",  # 존재하지 않는 UUID
            "new_password": "NewPass123!",
            "new_password2": "NewPass123!",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # 토큰 검증 에러는 non_field_errors로 반환됨
        assert "유효하지 않은 토큰" in str(response.data)

    def test_confirm_with_expired_token(self, api_client, user, password_reset_confirm_data_factory):
        """만료된 토큰으로 재설정 시도"""
        # Arrange - 24시간 이상 경과한 토큰
        raw_token = PasswordResetToken.generate_token(user=user)
        token_obj = PasswordResetToken.objects.get(user=user, is_used=False)
        token_obj.created_at = datetime.now(dt_timezone.utc) - timedelta(hours=24, seconds=1)
        token_obj.save()

        url = reverse("password-reset-confirm")
        data = password_reset_confirm_data_factory(token=raw_token, new_password="NewPass123!")

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # 보안상 만료된 토큰도 "유효하지 않은 토큰"으로 반환
        assert "유효하지 않은 토큰" in str(response.data)

    def test_confirm_with_used_token(self, api_client, user, password_reset_confirm_data_factory):
        """이미 사용된 토큰으로 재설정 시도 (재사용 방지)"""
        # Arrange
        raw_token = PasswordResetToken.generate_token(user=user)
        token_obj = PasswordResetToken.objects.get(user=user, is_used=False)
        token_obj.mark_as_used()  # 사용 처리

        url = reverse("password-reset-confirm")
        data = password_reset_confirm_data_factory(token=raw_token, new_password="NewPass123!")

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "유효하지 않은 토큰" in str(response.data)

    def test_confirm_password_mismatch(self, api_client, user, password_reset_confirm_data_factory):
        """비밀번호 불일치"""
        # Arrange
        raw_token = PasswordResetToken.generate_token(user=user)
        url = reverse("password-reset-confirm")
        # Factory 사용: new_password2만 다르게 설정
        data = password_reset_confirm_data_factory(
            token=raw_token, new_password="NewPass123!", new_password2="DifferentPass456!"  # 다른 비밀번호
        )

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "일치하지 않습니다" in str(response.data)

    def test_confirm_weak_password(self, api_client, user, password_reset_confirm_data_factory):
        """약한 비밀번호 (Django 정책 위반)"""
        # Arrange
        raw_token = PasswordResetToken.generate_token(user=user)
        url = reverse("password-reset-confirm")
        # Factory 사용: 너무 짧은 비밀번호
        data = password_reset_confirm_data_factory(token=raw_token, new_password="1234")  # 너무 짧음

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.data

    def test_confirm_common_password(self, api_client, user, password_reset_confirm_data_factory):
        """흔한 비밀번호 (Django 정책 위반)"""
        # Arrange
        raw_token = PasswordResetToken.generate_token(user=user)
        url = reverse("password-reset-confirm")
        # Factory 사용: 흔한 비밀번호
        data = password_reset_confirm_data_factory(token=raw_token, new_password="password123")  # 흔한 비밀번호

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.data

    def test_confirm_missing_fields(self, api_client):
        """필수 필드 누락"""
        # Arrange
        url = reverse("password-reset-confirm")
        # 이 테스트는 빈 dict로 필수 필드 검증을 테스트하므로 factory 사용 안 함
        data = {}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data
        assert "token" in response.data
        assert "new_password" in response.data
        assert "new_password2" in response.data


@pytest.mark.django_db
class TestPasswordResetTokenModel:
    """PasswordResetToken 모델 테스트"""

    def test_token_creation(self, user):
        """토큰 생성 시 UUID 자동 생성"""
        # Arrange & Act
        raw_token = PasswordResetToken.generate_token(user=user)

        # Assert
        assert raw_token is not None
        assert len(str(raw_token)) == 36  # UUID 형식
        token_obj = PasswordResetToken.objects.get(user=user)
        assert token_obj.is_used is False
        assert token_obj.used_at is None

    def test_is_expired_method(self, user):
        """is_expired() 메서드 테스트"""
        # Arrange
        base_time = datetime(2025, 1, 28, 10, 0, 0, tzinfo=dt_timezone.utc)
        raw_token = PasswordResetToken.generate_token(user=user)
        token_obj = PasswordResetToken.objects.get(user=user)
        token_obj.created_at = base_time
        token_obj.save()

        # Assert - 정확히 24시간 후 (만료되지 않음)
        assert token_obj.is_expired(now=base_time + timedelta(hours=24)) is False

        # Assert - 24시간 1초 후 (만료됨)
        assert token_obj.is_expired(now=base_time + timedelta(hours=24, seconds=1)) is True

        # Assert - 23시간 59분 59초 후 (유효)
        assert token_obj.is_expired(now=base_time + timedelta(hours=23, minutes=59, seconds=59)) is False

    def test_mark_as_used_method(self, user):
        """mark_as_used() 메서드 테스트"""
        # Arrange
        raw_token = PasswordResetToken.generate_token(user=user)
        token_obj = PasswordResetToken.objects.get(user=user)
        assert token_obj.is_used is False
        assert token_obj.used_at is None

        # Act
        token_obj.mark_as_used()

        # Assert
        assert token_obj.is_used is True
        assert token_obj.used_at is not None

    def test_token_uniqueness(self, user):
        """토큰 고유성 확인"""
        # Arrange & Act
        raw_token1 = PasswordResetToken.generate_token(user=user, invalidate_previous=False)
        raw_token2 = PasswordResetToken.generate_token(user=user, invalidate_previous=False)

        # Assert - UUID는 항상 다름
        assert raw_token1 != raw_token2

    def test_multiple_tokens_per_user(self, user):
        """한 사용자가 여러 토큰을 가질 수 있음"""
        # Arrange & Act
        raw_token1 = PasswordResetToken.generate_token(user=user, invalidate_previous=False)
        raw_token2 = PasswordResetToken.generate_token(user=user, invalidate_previous=False)
        raw_token3 = PasswordResetToken.generate_token(user=user, invalidate_previous=False)

        # Assert
        tokens = PasswordResetToken.objects.filter(user=user)
        assert tokens.count() == 3


@pytest.mark.django_db
class TestPasswordResetBoundary:
    """비밀번호 재설정 경계값 테스트"""

    def test_token_expiry_exactly_24_hours(self, api_client, user, password_reset_confirm_data_factory):
        """토큰이 정확히 24시간 후에 만료됨"""
        # Arrange - 정확히 24시간 1초 전 토큰
        raw_token = PasswordResetToken.generate_token(user=user)
        token_obj = PasswordResetToken.objects.get(user=user, is_used=False)
        token_obj.created_at = timezone.now() - timedelta(hours=24, seconds=1)
        token_obj.save()

        url = reverse("password-reset-confirm")
        data = password_reset_confirm_data_factory(token=raw_token, new_password="NewPass123!")

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 만료되어야 함
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # 보안상 만료된 토큰도 "유효하지 않은 토큰"으로 반환
        assert "유효하지 않은 토큰" in str(response.data)

    def test_token_valid_before_24_hours(self, api_client, user, password_reset_confirm_data_factory):
        """24시간 전이면 아직 유효함"""
        # Arrange - 23시간 전 토큰 (여유 있게 설정)
        raw_token = PasswordResetToken.generate_token(user=user)
        token_obj = PasswordResetToken.objects.get(user=user, is_used=False)
        token_obj.created_at = timezone.now() - timedelta(hours=23)
        token_obj.save()

        url = reverse("password-reset-confirm")
        data = password_reset_confirm_data_factory(token=raw_token, new_password="NewPass123!")

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 성공해야 함
        assert response.status_code == status.HTTP_200_OK

    def test_minimum_password_length(self, api_client, user, password_reset_confirm_data_factory):
        """최소 비밀번호 길이 (8자)"""
        # Arrange - 7자 (너무 짧음)
        raw_token = PasswordResetToken.generate_token(user=user)
        url = reverse("password-reset-confirm")
        data = password_reset_confirm_data_factory(token=raw_token, new_password="Pass12!")  # 7자

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Arrange - 정확히 8자
        data = password_reset_confirm_data_factory(token=raw_token, new_password="Pass123!")  # 8자

        # Act
        response = api_client.post(url, data, format="json")

        # Assert - 성공해야 함
        assert response.status_code == status.HTTP_200_OK
