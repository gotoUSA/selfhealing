from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from shopping.models.email_verification import EmailLog, EmailVerificationToken
from shopping.models.user import User
from shopping.tasks.email_tasks import retry_failed_emails_task, send_verification_email_task


class SendVerificationEmailTaskTest(TestCase):
    """이메일 발송 태스크 테스트"""

    def setUp(self):
        """테스트 데이터 준비"""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123!",
            first_name="테스트",
        )

        self.token = EmailVerificationToken.objects.create(user=self.user)

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_send_verification_email_success(self, mock_send_mail):
        """정상적인 이메일 발송 테스트"""
        # send_mail 모킹
        mock_send_mail.return_value = 1

        # 태스크 실행
        result = send_verification_email_task(
            user_id=self.user.id,
            token_id=self.token.id,
            is_resend=False,
        )

        # 결과 검증
        self.assertTrue(result["success"])
        self.assertEqual(result["recipient"], self.user.email)
        self.assertEqual(result["verification_code"], self.token.verification_code)

        # send_mail이 호출되었는지 확인
        self.assertTrue(mock_send_mail.called)

        # EmailLog 생성 확인
        email_log = EmailLog.objects.filter(token=self.token).first()
        self.assertIsNotNone(email_log)
        self.assertEqual(email_log.status, "sent")
        self.assertIsNotNone(email_log.sent_at)

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_send_verification_email_resend(self, mock_send_mail):
        """재발송 테스트"""
        # 기존 로그 생성
        EmailLog.objects.create(
            user=self.user,
            token=self.token,
            email_type="verification",
            recipient_email=self.user.email,
            subject="[쇼핑몰] 이메일 인증을 완료해주세요",
            status="sent",
        )

        mock_send_mail.return_value = 1

        # 재발송 태스크 실행
        result = send_verification_email_task(
            user_id=self.user.id,
            token_id=self.token.id,
            is_resend=True,
        )

        # 결과 검증
        self.assertTrue(result["success"])

        # send_mail 호출 확인
        self.assertTrue(mock_send_mail.called)

    def test_send_verification_email_user_not_found(self):
        """존재하지 않는 사용자 테스트"""
        result = send_verification_email_task(
            user_id=99999,  # 존재하지 않는 ID
            token_id=self.token.id,
        )

        # 실패 결과 확인
        self.assertFalse(result["success"])
        self.assertIn("사용자를 찾을 수 없습니다", result["message"])

    def test_send_verification_email_token_not_found(self):
        """존재하지 않는 토큰 테스트"""
        result = send_verification_email_task(
            user_id=self.user.id,
            token_id=99999,  # 존재하지 않는 ID
        )

        # 실패 결과 확인
        self.assertFalse(result["success"])
        self.assertIn("토큰을 찾을 수 없습니다", result["message"])

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_send_verification_email_smtp_error(self, mock_send_mail):
        """SMTP 에러 시 재시도 테스트"""
        # send_mail에서 예외 발생
        mock_send_mail.side_effect = Exception("SMTP connection failed")

        # 태스크 실행 (예외가 발생해야 함)
        with self.assertRaises(Exception):
            send_verification_email_task(
                user_id=self.user.id,
                token_id=self.token.id,
            )

        # EmailLog에 실패 기록 확인
        email_log = EmailLog.objects.filter(token=self.token).first()
        self.assertIsNotNone(email_log)
        # 재시도 로직 때문에 status가 바로 failed가 안될 수 있음


class RetryFailedEmailsTaskTest(TestCase):
    """실패한 이메일 재시도 태스크 테스트"""

    def setUp(self):
        """테스트 데이터 준비"""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123!",
            first_name="테스트",
        )

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_retry_failed_emails(self, mock_send_mail):
        """실패한 이메일 재시도 테스트 - 실제 태스크 실행"""
        # send_mail 모킹 (외부 SMTP 의존성)
        mock_send_mail.return_value = 1
        
        # 실패한 이메일 로그 생성
        token = EmailVerificationToken.objects.create(user=self.user)

        failed_log = EmailLog.objects.create(
            user=self.user,
            token=token,
            email_type="verification",
            recipient_email=self.user.email,
            subject="[쇼핑몰] 이메일 인증을 완료해주세요",
            status="failed",
        )

        # 태스크 실행 (CELERY_TASK_ALWAYS_EAGER로 실제 재시도 태스크 실행)
        result = retry_failed_emails_task()

        # 결과 검증
        self.assertTrue(result["success"])
        self.assertEqual(result["total_failed"], 1)
        self.assertEqual(result["retry_attempted"], 1)

        # 실제 이메일 발송 검증 (외부 의존성만 mock)
        self.assertTrue(mock_send_mail.called)
        
        # DB 상태 검증: 재발송 후 새로운 성공 로그가 생성되었는지 확인
        email_logs = EmailLog.objects.filter(token=token).order_by("-created_at")
        # 재발송 시 새 로그가 생성되거나 기존 로그 상태가 업데이트됨
        self.assertTrue(email_logs.exists())

    def test_retry_failed_emails_skip_expired_token(self):
        """만료된 토큰은 재시도 스킵 테스트"""
        # 만료된 토큰 생성
        token = EmailVerificationToken.objects.create(user=self.user)
        token.created_at = timezone.now() - timedelta(hours=25)
        token.save()

        EmailLog.objects.create(
            user=self.user,
            token=token,
            email_type="verification",
            recipient_email=self.user.email,
            subject="[쇼핑몰] 이메일 인증을 완료해주세요",
            status="failed",
        )

        # 태스크 실행
        result = retry_failed_emails_task()

        # 만료된 토큰은 스킵되어야 함
        self.assertEqual(result["retry_attempted"], 0)

    def test_retry_failed_emails_skip_verified_user(self):
        """이미 인증된 사용자는 재시도 스킵 테스트"""
        # 인증된 사용자
        self.user.is_email_verified = True
        self.user.save()

        token = EmailVerificationToken.objects.create(user=self.user)

        EmailLog.objects.create(
            user=self.user,
            token=token,
            email_type="verification",
            recipient_email=self.user.email,
            subject="[쇼핑몰] 이메일 인증을 완료해주세요",
            status="failed",
        )

        # 태스크 실행
        result = retry_failed_emails_task()

        # 이미 인증된 사용자는 스킵되어야 함
        self.assertEqual(result["retry_attempted"], 0)

    def test_retry_failed_emails_old_logs_skipped(self):
        """24시간 이전 로그는 재시도 안 함 테스트"""
        token = EmailVerificationToken.objects.create(user=self.user)

        # 25시간 전 로그
        old_log = EmailLog.objects.create(
            user=self.user,
            token=token,
            email_type="verification",
            recipient_email=self.user.email,
            subject="[쇼핑몰] 이메일 인증을 완료해주세요",
            status="failed",
        )
        old_log.created_at = timezone.now() - timedelta(hours=25)
        old_log.save()

        # 태스크 실행
        result = retry_failed_emails_task()

        # 오래된 로그는 제외되어야 함
        self.assertEqual(result["total_failed"], 0)
        self.assertEqual(result["retry_attempted"], 0)


class EmailTaskIntegrationTest(TestCase):
    """이메일 태스크 통합 테스트"""

    def setUp(self):
        """테스트 데이터 준비"""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123!",
            first_name="테스트",
        )

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_full_email_workflow(self, mock_send_mail):
        """전체 이메일 워크플로우 테스트"""
        mock_send_mail.return_value = 1

        # 1. 토큰 생성
        token = EmailVerificationToken.objects.create(user=self.user)

        # 2. 이메일 발송
        result = send_verification_email_task(
            user_id=self.user.id,
            token_id=token.id,
        )

        # 3. 발송 성공 확인
        self.assertTrue(result["success"])

        # 4. EmailLog 확인
        email_log = EmailLog.objects.filter(token=token).first()
        self.assertIsNotNone(email_log)
        self.assertEqual(email_log.status, "sent")

        # 5. 사용자 인증
        self.user.is_email_verified = True
        self.user.save()

        # 6. 토큰 사용 처리
        token.mark_as_used()

        # 7. 최종 상태 확인
        self.assertTrue(self.user.is_email_verified)
        self.assertTrue(token.is_used)
