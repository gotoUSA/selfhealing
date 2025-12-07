"""
이메일 발송 태스크 테스트

Celery 비동기 작업의 이메일 발송 기능 검증:
- send_verification_email_task: 인증 이메일 발송
- retry_failed_emails_task: 실패한 이메일 재시도
"""

import pytest

from shopping.models.email_verification import EmailLog, EmailVerificationToken
from shopping.tasks.email_tasks import retry_failed_emails_task, send_verification_email_task
from shopping.tests.factories import (
    EmailLogFactory,
    EmailVerificationTokenFactory,
    UserFactory,
)


# ==========================================
# 인증 이메일 발송 태스크 테스트
# ==========================================


@pytest.mark.django_db
class TestSendVerificationEmailTask:
    """이메일 발송 태스크 테스트"""

    def test_send_verification_email_success(self, mocker):
        """정상적인 이메일 발송 테스트"""
        # Arrange - Factory 사용
        mock_send_mail = mocker.patch("shopping.tasks.email_tasks.send_mail", return_value=1)
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        result = send_verification_email_task(
            user_id=user.id,
            token_id=token.id,
            is_resend=False,
        )

        # Assert - 결과 검증
        assert result["success"] is True
        assert result["recipient"] == user.email
        assert result["verification_code"] == token.verification_code

        # Assert - send_mail 호출 확인
        assert mock_send_mail.called

        # Assert - EmailLog 생성 확인
        email_log = EmailLog.objects.filter(token=token).first()
        assert email_log is not None
        assert email_log.status == "sent"
        assert email_log.sent_at is not None

    def test_send_verification_email_resend(self, mocker):
        """재발송 테스트"""
        # Arrange - Factory 사용
        mock_send_mail = mocker.patch("shopping.tasks.email_tasks.send_mail", return_value=1)
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)
        EmailLogFactory.sent(user=user, token=token)

        # Act
        result = send_verification_email_task(
            user_id=user.id,
            token_id=token.id,
            is_resend=True,
        )

        # Assert
        assert result["success"] is True
        assert mock_send_mail.called

    def test_send_verification_email_user_not_found(self):
        """존재하지 않는 사용자 테스트"""
        # Arrange
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        result = send_verification_email_task(
            user_id=99999,  # 존재하지 않는 ID
            token_id=token.id,
        )

        # Assert
        assert result["success"] is False
        assert "사용자" in result["message"] and "찾을 수 없습니다" in result["message"]

    def test_send_verification_email_token_not_found(self):
        """존재하지 않는 토큰 테스트"""
        # Arrange
        user = UserFactory.unverified()

        # Act
        result = send_verification_email_task(
            user_id=user.id,
            token_id=99999,  # 존재하지 않는 ID
        )

        # Assert
        assert result["success"] is False
        assert "토큰" in result["message"] and "찾을 수 없습니다" in result["message"]

    def test_send_verification_email_smtp_error(self, mocker):
        """SMTP 에러 시 재시도 테스트"""
        # Arrange - 외부 의존성만 mock
        mocker.patch(
            "shopping.tasks.email_tasks.send_mail",
            side_effect=Exception("SMTP connection failed"),
        )
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act & Assert
        with pytest.raises(Exception):
            send_verification_email_task(
                user_id=user.id,
                token_id=token.id,
            )


# ==========================================
# 실패한 이메일 재시도 태스크 테스트
# ==========================================


@pytest.mark.django_db
class TestRetryFailedEmailsTask:
    """실패한 이메일 재시도 태스크 테스트"""

    def test_retry_failed_emails(self, mocker):
        """실패한 이메일 재시도 테스트 - 실제 태스크 실행"""
        # Arrange - 외부 의존성만 mock
        mock_send_mail = mocker.patch("shopping.tasks.email_tasks.send_mail", return_value=1)
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)
        EmailLogFactory.failed(user=user, token=token)

        # Act
        result = retry_failed_emails_task()

        # Assert
        assert result["success"] is True
        assert result["total_failed"] == 1
        assert result["retry_attempted"] == 1
        assert mock_send_mail.called

    def test_retry_failed_emails_skip_expired_token(self):
        """만료된 토큰은 재시도 스킵 테스트"""
        # Arrange - Factory 사용
        user = UserFactory.unverified()
        expired_token = EmailVerificationTokenFactory.expired(user=user, hours_ago=25)
        EmailLogFactory.failed(user=user, token=expired_token)

        # Act
        result = retry_failed_emails_task()

        # Assert - 만료된 토큰은 스킵
        assert result["retry_attempted"] == 0

    def test_retry_failed_emails_skip_verified_user(self):
        """이미 인증된 사용자는 재시도 스킵 테스트"""
        # Arrange - 인증된 사용자 (기본값)
        user = UserFactory()  # is_email_verified=True
        token = EmailVerificationTokenFactory(user=user)
        EmailLogFactory.failed(user=user, token=token)

        # Act
        result = retry_failed_emails_task()

        # Assert - 이미 인증된 사용자는 스킵
        assert result["retry_attempted"] == 0

    def test_retry_failed_emails_old_logs_skipped(self):
        """24시간 이전 로그는 재시도 안 함 테스트"""
        # Arrange - Factory 사용 (25시간 전 실패 로그)
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)
        old_failed_log = EmailLogFactory.failed(user=user, token=token)

        # 수동으로 created_at 조작 (25시간 전)
        from datetime import timedelta

        from django.utils import timezone

        EmailLog.objects.filter(id=old_failed_log.id).update(created_at=timezone.now() - timedelta(hours=25))

        # Act
        result = retry_failed_emails_task()

        # Assert - 오래된 로그는 제외
        assert result["total_failed"] == 0
        assert result["retry_attempted"] == 0


# ==========================================
# 이메일 태스크 통합 테스트
# ==========================================


@pytest.mark.django_db
class TestEmailTaskIntegration:
    """이메일 태스크 통합 테스트"""

    def test_full_email_workflow(self, mocker):
        """전체 이메일 워크플로우 테스트"""
        # Arrange - 외부 의존성만 mock
        mocker.patch("shopping.tasks.email_tasks.send_mail", return_value=1)
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act 1 - 이메일 발송
        result = send_verification_email_task(
            user_id=user.id,
            token_id=token.id,
        )

        # Assert 1 - 발송 성공
        assert result["success"] is True

        # Assert 2 - EmailLog 확인
        email_log = EmailLog.objects.filter(token=token).first()
        assert email_log is not None
        assert email_log.status == "sent"

        # Act 2 - 사용자 인증 처리
        user.is_email_verified = True
        user.save()
        token.mark_as_used()

        # Assert 3 - 최종 상태 확인
        user.refresh_from_db()
        token.refresh_from_db()
        assert user.is_email_verified is True
        assert token.is_used is True
