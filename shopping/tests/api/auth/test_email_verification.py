import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from shopping.models.email_verification import EmailVerificationToken


@pytest.mark.django_db
class TestEmailVerificationSend:
    def test_send_verification_email_success(self, api_client, unverified_user):
        """인증 이메일 발송 성공"""
        # Arrange - 비인증 사용자로 로그인
        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-send")

        # Act - 이메일 발송 요청
        response = api_client.post(url)

        # Assert - 응답 확인
        assert response.status_code == status.HTTP_200_OK
        assert "message" in response.data
        assert "인증 이메일" in response.data["message"]

        # Assert - 토큰 생성 확인
        token = EmailVerificationToken.objects.filter(user=unverified_user, is_used=False).first()
        assert token is not None
        assert len(token.verification_code) == 6

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_send_email_task_called(self, mock_task, api_client, unverified_user):
        """Celery 태스크 호출 확인"""
        # Arrange
        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-send")

        # Act
        response = api_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert mock_task.called


@pytest.mark.django_db
class TestEmailVerificationByToken:
    """UUID 토큰으로 인증 - 정상 케이스"""

    def test_verify_with_valid_token(self, api_client, unverified_user):
        """유효한 UUID 토큰으로 인증 성공"""
        # Arrange - 토큰 생성
        token = EmailVerificationToken.objects.create(user=unverified_user)
        url = reverse("email-verification-verify")

        # Act - GET 요청으로 인증
        response = api_client.get(url, {"token": str(token.token)})

        # Assert - 응답 확인
        assert response.status_code == status.HTTP_200_OK
        assert "이메일 인증이 완료되었습니다" in response.data["message"]

        # Assert - 사용자 인증 상태 확인
        unverified_user.refresh_from_db()
        assert unverified_user.is_email_verified is True

        # Assert - 토큰 사용 처리 확인
        token.refresh_from_db()
        assert token.is_used is True
        assert token.used_at is not None


@pytest.mark.django_db
class TestEmailVerificationByCode:
    """6자리 코드로 인증 - 정상 케이스"""

    def test_verify_with_valid_code(self, api_client, unverified_user):
        """유효한 6자리 코드로 인증 성공"""
        # Arrange - 토큰 생성 및 로그인
        token = EmailVerificationToken.objects.create(user=unverified_user)
        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-verify")

        # Act - POST 요청으로 코드 인증
        response = api_client.post(url, {"code": token.verification_code})

        # Assert - 응답 확인
        assert response.status_code == status.HTTP_200_OK
        assert "이메일 인증이 완료되었습니다" in response.data["message"]

        # Assert - 사용자 인증 상태 확인
        unverified_user.refresh_from_db()
        assert unverified_user.is_email_verified is True

        # Assert - 토큰 사용 처리 확인
        token.refresh_from_db()
        assert token.is_used is True

    def test_case_insensitive_code(self, api_client, unverified_user):
        """대소문자 구분 없이 인증 (코드는 대문자로 저장됨)"""
        # Arrange
        token = EmailVerificationToken.objects.create(user=unverified_user)
        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-verify")

        # Act - 소문자로 전송
        lowercase_code = token.verification_code.lower()
        response = api_client.post(url, {"code": lowercase_code})

        # Assert - 성공해야 함
        assert response.status_code == status.HTTP_200_OK
        unverified_user.refresh_from_db()
        assert unverified_user.is_email_verified is True


@pytest.mark.django_db
class TestEmailResend:
    """이메일 재발송 - 정상 케이스"""

    def test_resend_verification_email_success(self, api_client, unverified_user):
        """인증 이메일 재발송"""
        # Arrange - 기존 토큰을 1분 이상 전에 생성
        base_time = datetime(2025, 1, 28, 10, 0, 0, tzinfo=dt_timezone.utc)
        old_token = EmailVerificationToken.objects.create(user=unverified_user)
        old_token.created_at = base_time
        old_token.save()

        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-resend")

        # Act - 재발송 요청
        response = api_client.post(url)

        # Assert - 응답 확인
        assert response.status_code == status.HTTP_200_OK
        assert "재발송" in response.data["message"]

        # Assert - 기존 토큰 무효화 확인
        old_token.refresh_from_db()
        assert old_token.is_used is True

        # Assert - 새 토큰 생성 확인
        new_token = EmailVerificationToken.objects.filter(user=unverified_user, is_used=False).exclude(id=old_token.id).first()
        assert new_token is not None


@pytest.mark.django_db
class TestEmailVerificationStatus:
    """인증 상태 확인 - 정상 케이스"""

    def test_check_status_verified_user(self, api_client, user):
        """인증 완료된 사용자 상태 확인"""
        # Arrange
        api_client.force_authenticate(user=user)
        url = reverse("email-verification-status")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_verified"] is True
        assert response.data["email"] == user.email

    def test_check_status_unverified_user_with_token(self, api_client, unverified_user):
        """미인증 사용자 상태 확인 (토큰 있음)"""
        # Arrange
        token = EmailVerificationToken.objects.create(user=unverified_user)
        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-status")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_verified"] is False
        assert response.data["pending_verification"] is True
        assert response.data["token_expired"] is False
        assert response.data["can_resend"] is False  # 방금 생성해서 1분 안됨


@pytest.mark.django_db
class TestEmailVerificationOnSignup:
    """회원가입 시 자동 발송 - 정상 케이스"""

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_email_sent_on_signup(self, mock_task, api_client, db, registration_data_factory):
        """회원가입 시 자동으로 인증 이메일 발송됨"""
        # Arrange
        url = reverse("auth-register")
        data = registration_data_factory(
            username="newuser", email="newuser@example.com", password="NewPass123!", phone_number="010-9999-9999"
        )

        # Act - 회원가입
        response = api_client.post(url, data)

        # Assert - 회원가입 성공
        assert response.status_code == status.HTTP_201_CREATED

        # Assert - Celery 태스크 호출 확인
        assert mock_task.called

    def test_token_created_on_signup(self, api_client, db, registration_data_factory):
        """회원가입 시 EmailVerificationToken 자동 생성"""
        # Arrange
        url = reverse("auth-register")
        data = registration_data_factory(
            username="newuser2", email="newuser2@example.com", password="NewPass123!", phone_number="010-8888-8888"
        )

        # Act - 회원가입
        response = api_client.post(url, data)

        # Assert - 회원가입 성공
        assert response.status_code == status.HTTP_201_CREATED

        # Assert - 토큰 생성 확인
        from shopping.models.user import User

        user = User.objects.get(username="newuser2")
        token = EmailVerificationToken.objects.filter(user=user).first()
        assert token is not None
        assert token.is_used is False


@pytest.mark.django_db
class TestEmailVerificationBoundary:
    """경계값 테스트"""

    def test_token_expiry_exactly_24_hours(self, api_client, unverified_user, mock_time):
        """토큰이 정확히 24시간 후에 만료됨"""
        # Arrange - 정확히 24시간 전 토큰 생성
        base_time = datetime(2025, 1, 28, 10, 0, 0, tzinfo=dt_timezone.utc)

        with mock_time(base_time):
            token = EmailVerificationToken.objects.create(user=unverified_user)

        url = reverse("email-verification-verify")

        # Act - 24시간 1초 후 시점으로 시간 고정하여 요청
        with mock_time(base_time + timedelta(hours=24, seconds=1)):
            response = api_client.get(url, {"token": str(token.token)})

        # Assert - 만료되어야 함
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "만료" in (response.data["token"][0])

    def test_token_valid_before_24_hours(self, api_client, unverified_user, mock_time):
        """24시간 전이면 아직 유효함"""
        # Arrange
        base_time = datetime(2025, 1, 28, 10, 0, 0, tzinfo=dt_timezone.utc)

        with mock_time(base_time):
            token = EmailVerificationToken.objects.create(user=unverified_user)

        url = reverse("email-verification-verify")

        # Act - 23시간 59분 59초 후 시점으로 시간 고정하여 요청
        with mock_time(base_time + timedelta(hours=23, minutes=59, seconds=59)):
            response = api_client.get(url, {"token": str(token.token)})

        # Assert - 성공해야 함
        assert response.status_code == status.HTTP_200_OK

    def test_resend_blocked_within_1_minute(self, api_client, unverified_user):
        """1분 미만일 때는 재발송 차단"""
        # Arrange - 30초 전 토큰 (1분 미만)
        recent_token = EmailVerificationToken.objects.create(user=unverified_user)
        recent_token.created_at = timezone.now() - timedelta(seconds=30)
        recent_token.save()

        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-resend")

        # Act
        response = api_client.post(url)

        # Assert - 차단되어야 함
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "1분" in str(response.data)

    def test_resend_rate_limit_exactly_1_minute(self, api_client, unverified_user):
        """1분 경과 후에는 재발송 가능"""
        # Arrange - 1분 이상 지난 토큰 (충분한 여유를 두어 타이밍 이슈 방지)
        old_token = EmailVerificationToken.objects.create(user=unverified_user)
        old_token.created_at = timezone.now() - timedelta(minutes=1, seconds=1)
        old_token.save()

        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-resend")

        # Act
        response = api_client.post(url)

        # Assert - 성공해야 함
        assert response.status_code == status.HTTP_200_OK

    def test_code_length_exactly_6_hours(self, api_client, unverified_user):
        """코드는 정확히 6자리여야함"""
        # Arrange
        token = EmailVerificationToken.objects.create(user=unverified_user)

        # Assert - 6자리 확인
        assert len(token.verification_code) == 6


@pytest.mark.django_db
class TestEmailVerificationSendException:
    """발송 예외 케이스"""

    def test_send_unauthenticated(self, api_client):
        """비인증 사용자는 발송 불가"""
        # Arrange
        url = reverse("email-verification-send")

        # Act
        response = api_client.post(url)

        # Assert
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    def test_send_already_verified_user(self, api_client, user):
        """이미 인증된 사용자는 발송 불가"""
        # Arrange - user fixture는 이미 인증됨
        api_client.force_authenticate(user=user)
        url = reverse("email-verification-send")

        # Act
        response = api_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "이미 이메일 인증이 완료되었습니다" in str(response.data)

    def test_send_rate_limit_within_1_minute(self, api_client, unverified_user):
        """1분 이내 재발송 시도 시 제한"""
        # Arrange - 방금 토큰 생성 (1분 안됨)
        EmailVerificationToken.objects.create(user=unverified_user)
        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-send")

        # Act
        response = api_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "1분" in str(response.data)


@pytest.mark.django_db
class TestEmailVerificationByTokenException:
    """UUID 토큰 인증 예외 케이스"""

    def test_verify_with_expired_token(self, api_client, unverified_user):
        """만료된 토큰으로 인증 시도"""
        # Arrange - 만료된 토큰 (24시간 1초전)
        token = EmailVerificationToken.objects.create(user=unverified_user)
        token.created_at = timezone.now() - timedelta(hours=24, seconds=1)
        token.save()
        url = reverse("email-verification-verify")

        # Act
        response = api_client.get(url, {"token": str(token.token)})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "만료" in (response.data["token"][0])

    def test_verify_with_used_token(self, api_client, unverified_user):
        """이미 사용된 토큰으로 인증 시도"""
        # Arrange - 사용된 토큰
        token = EmailVerificationToken.objects.create(user=unverified_user)
        token.is_used = True
        token.save()
        url = reverse("email-verification-verify")

        # Act
        response = api_client.get(url, {"token": str(token.token)})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "이미 사용된 토큰입니다" in str(response.data["token"][0])

    def test_verify_with_invalid_uuid_format(self, api_client):
        """잘못된 UUID 형식"""
        # Arrange
        url = reverse("email-verification-verify")

        # Act - 유효하지 않은 UUID
        response = api_client.get(url, {"token": "invalid-token-123"})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Must be a valid UUID" in str(response.data["token"][0])

    def test_verify_with_nonexistent_token(self, api_client):
        """존재하지 않는 UUID 토큰"""
        # Arrange - 유효한 UUID 형식이지만 DB에 없음
        url = reverse("email-verification-verify")
        fake_uuid = str(uuid.uuid4())

        # Act
        response = api_client.get(url, {"token": fake_uuid})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "유효하지 않은 토큰입니다" in str(response.data["token"][0])

    def test_verify_without_token_parameter(self, api_client):
        """토큰 파라미터 누락"""
        # Arrange
        url = reverse("email-verification-verify")

        # Act - 토큰 없이 GET 요청
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "토큰이 제공되지 않았습니다" in response.data["token"][0]


@pytest.mark.django_db
class TestEmailVerificationByCodeException:
    """6자리 코드 인증 예외 케이스"""

    def test_verify_code_unauthenticated(self, api_client):
        """비인증 사용자는 코드 인증 불가"""
        # Arrange
        url = reverse("email-verification-verify")

        # Act
        response = api_client.post(url, {"code": "ABC123"})

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_verify_with_wrong_code(self, api_client, unverified_user):
        """잘못된 코드로 인증 시도"""
        # Arrange
        EmailVerificationToken.objects.create(user=unverified_user)
        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-verify")

        # Act - 존재하지 않는 코드
        response = api_client.post(url, {"code": "WRONG1"})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "인증 코드가 일치하지 않습니다" in response.data["code"][0]

    def test_verify_with_expired_code(self, api_client, unverified_user):
        """만료된 코드로 인증 시도"""
        # Arrange - 만료된 토큰
        token = EmailVerificationToken.objects.create(user=unverified_user)
        token.created_at = timezone.now() - timedelta(hours=24, seconds=1)
        token.save()

        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-verify")

        # Act
        response = api_client.post(url, {"code": token.verification_code})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "만료" in response.data["code"][0]

    def test_verify_without_code_parameter(self, api_client, unverified_user):
        """코드 파라미터 누락"""
        # Arrange
        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-verify")

        # Act
        response = api_client.post(url, {})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "인증 코드가 제공되지 않았습니다" in response.data["code"][0]


@pytest.mark.django_db
class TestEmailResendException:
    """재발송 예외 케이스"""

    def test_resend_unauthenticated(self, api_client):
        """비인증 사용자는 재발송 불가"""
        # Arrange
        url = reverse("email-verification-resend")

        # Act
        response = api_client.post(url)

        # Assert
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    def test_resend_already_verified_user(self, api_client, user):
        """이미 인증된 사용자는 재발송 불가"""
        # Arrange
        api_client.force_authenticate(user=user)
        url = reverse("email-verification-resend")

        # Act
        response = api_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "이미 이메일 인증이 완료되었습니다" in str(response.data)

    def test_resend_rate_limit_within_1_minute(self, api_client, unverified_user):
        """1분 이내 재발송 시도"""
        # Arrange - 방금 토큰 생성
        EmailVerificationToken.objects.create(user=unverified_user)
        api_client.force_authenticate(user=unverified_user)
        url = reverse("email-verification-resend")

        # Act
        response = api_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "1분" in str(response.data)


@pytest.mark.django_db
class TestEmailVerificationStatusException:
    """상태 확인 예외 케이스"""

    def test_check_status_unauthenticated(self, api_client):
        """비인증 사용자는 상태 확인 불가"""
        # Arrange
        url = reverse("email-verification-status")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestEmailVerificationTokenModel:
    """EmailVerificationToken 모델 자체 테스트"""

    def test_token_creation_generates_uuid_and_code(self, unverified_user):
        """토큰 생성 시 UUID와 6자리 코드 자동 생성"""
        # Arrange & Act
        token = EmailVerificationToken.objects.create(user=unverified_user)

        # Assert - UUID 생성 확인
        assert token.token is not None
        assert len(str(token.token)) == 36  # UUID 형식

        # Assert - 6자리 코드 생성 확인
        assert token.verification_code is not None
        assert len(token.verification_code) == 6
        assert token.verification_code.isalnum()  # 영문+숫자
        assert token.verification_code.isupper()  # 대문자

    def test_is_expired_method(self, unverified_user):
        """is_expired() 메서드 테스트"""
        # Arrange - 만료된 토큰
        base_time = datetime(2025, 1, 28, 10, 0, 0, tzinfo=dt_timezone.utc)
        token = EmailVerificationToken.objects.create(user=unverified_user)
        token.created_at = base_time
        token.save()

        # Assert - 정확히 24시간 후 (만료되지 않음)
        assert token.is_expired(now=base_time + timedelta(hours=24)) is False

        # Assert - 24시간 1초 후 (만료됨)
        assert token.is_expired(now=base_time + timedelta(hours=24, seconds=1)) is True

        # Assert - 23시간 59분 59초 후 (유효)
        assert token.is_expired(now=base_time + timedelta(hours=23, minutes=59, seconds=59)) is False

    def test_can_resend_method(self, unverified_user):
        """can_resend() 메서드 테스트 (1분 제한)"""
        # Arrange
        base_time = datetime(2025, 1, 28, 10, 0, 0, tzinfo=dt_timezone.utc)
        token = EmailVerificationToken.objects.create(user=unverified_user)
        token.created_at = base_time
        token.save()

        # Assert - 정확히 1분 후 (재발송 불가)
        assert token.can_resend(now=base_time + timedelta(minutes=1)) is False

        # Assert - 1분 1초 후 (재발송 가능)
        assert token.can_resend(now=base_time + timedelta(minutes=1, seconds=1)) is True

        # Assert - 30초 후 (재발송 불가)
        assert token.can_resend(now=base_time + timedelta(seconds=30)) is False

    def test_mark_as_used_method(self, unverified_user):
        """mark_as_used() 메서드 테스트"""
        # Arrange
        token = EmailVerificationToken.objects.create(user=unverified_user)
        assert token.is_used is False
        assert token.used_at is None

        # Act
        token.mark_as_used()

        # Assert
        assert token.is_used is True
        assert token.used_at is not None

    def test_token_uniqueness(self, unverified_user):
        """토큰 고유성 확인"""
        # Arrange & Act
        token1 = EmailVerificationToken.objects.create(user=unverified_user)
        token2 = EmailVerificationToken.objects.create(user=unverified_user)

        # Assert - UUID는 항상 다름
        assert token1.token != token2.token

        # Assert - 코드도 대부분 다름 (같을 확률은 매우 낮음)
        # 36^6 = 2,176,782,336 가지의 경우의 수
