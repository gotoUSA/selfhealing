from __future__ import annotations

import logging
from datetime import timedelta
from smtplib import SMTPException
from typing import Any

from celery import Task, shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone

from shopping.models.email_verification import EmailLog, EmailVerificationToken
from shopping.models.user import User

from .retry_policy import retry_with_backoff

logger = logging.getLogger(__name__)


def _handle_email_failure(email_log: EmailLog | None, error: Exception, recipient: str) -> None:
    """이메일 발송 실패 처리 헬퍼"""
    if email_log:
        email_log.mark_as_failed(str(error))
    logger.error(f"❌ 이메일 발송 실패: {recipient} - {str(error)}")


# 발송이 끝난 뒤의 상태들 — 이 중 하나면 같은 메일을 다시 보내지 않는다
SENT_EMAIL_STATUSES = ("sent", "opened", "clicked", "verified")


def _is_already_sent(email_log: EmailLog, recipient: str) -> bool:
    """이미 발송된 이메일인지 확인

    인증 메일 로그는 토큰마다 하나이고, 사용자가 재발송을 누르면 새 토큰이 만들어진다. 그래서 같은
    토큰의 로그가 이미 발송 상태라면 재발송 여부와 관계없이 재배달·재시도·스윕이 만든 중복이다.
    """
    if email_log.status in SENT_EMAIL_STATUSES:
        logger.info(f"이미 발송된 이메일입니다: {recipient}")
        return True
    return False


@shared_task(
    bind=True,
    max_retries=3,
    # 수동 self.retry 에도 적용되도록 retry_countdown 이 읽는다 (0~30, 0~60, 0~120초)
    retry_backoff=30,
    retry_backoff_max=180,
    retry_jitter=True,
    acks_late=True,
)
def send_verification_email_task(self: Task, user_id: int, token_id: int, is_resend: bool = False) -> dict[str, Any]:
    """
    이메일 인증 메일 발송 태스크 (비동기)

    Args:
        self: Celery task 인스턴스 (bind=True)
        user_id: 사용자 ID
        token_id: 인증 토큰 ID
        is_resend: 재발송 여부

    Returns:
        dict: 발송 결과 {'success': bool, 'message': str}
    """
    try:
        # 사용자 및 토큰 조회
        user = User.objects.get(id=user_id)
        token = EmailVerificationToken.objects.get(id=token_id)

        # 이메일 로그 조회 또는 생성
        email_log, created = EmailLog.objects.get_or_create(
            token=token,
            defaults={
                "user": user,
                "email_type": "verification",
                "recipient_email": user.email,
                "subject": "[쇼핑몰] 이메일 인증을 완료해주세요" + (" (재발송)" if is_resend else ""),
                "status": "pending",
            },
        )

        # 이미 발송 성공한 경우 중복 발송 방지 (재배달·재시도·재발송 스윕이 겹친 경우)
        if _is_already_sent(email_log, user.email):
            return {
                "success": True,
                "message": "이미 발송된 이메일입니다.",
            }

        # 인증 URL 생성
        verification_url = f"{settings.FRONTEND_URL}/verify-email?token={token.token}"

        # HTML 이메일 내용
        # HTML 이메일 내용
        html_message = render_to_string(
            "email/verification.html",
            {
                "user": user,
                "verification_url": verification_url,
                "verification_code": token.verification_code,
                "is_resend": is_resend,
            },
        )

        # 텍스트 버전
        plain_message = f"""
안녕하세요, {user.first_name}님!

{'요청하신 이메일 인증 메일을 다시 보내드립니다.' if is_resend else '회원가입을 환영합니다!'}

이메일 인증을 완료하려면 아래 링크를 클릭하거나 인증 코드를 입력해주세요.

인증 링크: {verification_url}
인증 코드: {token.verification_code}

이 링크와 코드는 24시간 동안 유효합니다.

{'이전에 받으신 인증 메일은 더 이상 유효하지 않습니다.' if is_resend else ''}

감사합니다.
쇼핑몰 팀 드림
"""

        # 이메일 발송
        send_mail(
            subject=email_log.subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )

        # 발송 성공 처리
        email_log.mark_as_sent()

        logger.info(f"✅ 이메일 발송 성공: {user.email} (토큰: {token.verification_code})")

        return {
            "success": True,
            "message": "이메일이 성공적으로 발송되었습니다.",
            "recipient": user.email,
            "verification_code": token.verification_code,
        }

    except (User.DoesNotExist, EmailVerificationToken.DoesNotExist) as e:
        # 재시도 불가 - 리소스가 없으면 재시도해도 동일
        error_type = "사용자" if isinstance(e, User.DoesNotExist) else "토큰"
        logger.error(f"❌ {error_type}을(를) 찾을 수 없습니다: user_id={user_id}, token_id={token_id}")
        return {"success": False, "message": f"{error_type}을(를) 찾을 수 없습니다."}

    except SMTPException as e:
        # SMTP 오류 - 네트워크/서버 문제, 재시도 가치 있음
        recipient = user.email if "user" in locals() else "unknown"
        _handle_email_failure(email_log if "email_log" in locals() else None, e, recipient)
        raise retry_with_backoff(self, e)

    except Exception as e:
        recipient = user.email if "user" in locals() else "unknown"
        # 이메일 로그 실패 처리
        if "email_log" in locals():
            email_log.mark_as_failed(str(e))

        # 알 수 없는 오류는 재시도 안 함 (Fail-Fast)
        raise


@shared_task(bind=True)
def retry_failed_emails_task(self: Task) -> dict[str, Any]:
    """
    실패한 이메일 재발송 태스크 (주기적 실행)

    최근 24시간 이내 실패한 이메일 중, 재발송 횟수가 settings.EMAIL_MAX_RESENDS 미만이고
    마지막 실패 뒤 settings.EMAIL_RESEND_AFTER_MINUTES 가 지난 것만 재발송한다.

    마지막 실패 뒤 대기: 발송 태스크는 스스로 재시도(최대 3회, 백오프)하므로, 그 재시도가 아직 살아 있는
    로그를 여기서 또 보내면 복구 순간 같은 메일이 여러 통 간다. 재발송 전에 로그를 pending 으로 바꿔
    (조건부 UPDATE) 다음 주기와 다른 스윕이 같은 로그를 다시 집지 않게 한다.

    Returns:
        dict: 재시도 결과 통계
    """
    try:
        # 24시간 이내 실패한 이메일 로그 조회
        now = timezone.now()
        failed_logs = (
            EmailLog.objects.filter(
                status="failed",
                created_at__gte=now - timedelta(hours=24),
                email_type="verification",
                resend_count__lt=settings.EMAIL_MAX_RESENDS,
            )
            .exclude(failed_at__gt=now - timedelta(minutes=settings.EMAIL_RESEND_AFTER_MINUTES))
            .select_related("token", "user")
        )
        # 루프에서 집은 로그는 pending 으로 바뀌므로 대상은 먼저 확정해 둔다
        failed_logs = list(failed_logs)

        retry_count = 0
        success_count = 0

        for email_log in failed_logs:
            # 토큰이 없거나 만료된 경우 스킵
            if not email_log.token or email_log.token.is_expired():
                logger.info(f"⏭️ 만료된 토큰 스킵: {email_log.recipient_email}")
                continue

            # 이미 인증된 경우 스킵
            if email_log.user and email_log.user.is_email_verified:
                logger.info(f"⏭️ 이미 인증됨 스킵: {email_log.recipient_email}")
                continue

            # 재발송 시도
            try:
                # 먼저 집는다 — 이 UPDATE 가 성공한 스윕만 발행한다 (그 사이 발송 성공·다른 스윕이 집은 경우 건너뜀)
                claimed = EmailLog.objects.filter(pk=email_log.pk, status="failed").update(
                    status="pending", resend_count=F("resend_count") + 1
                )
                if not claimed:
                    continue
                retry_count += 1

                # 비동기 태스크 호출
                result = send_verification_email_task.delay(
                    user_id=email_log.user.id,
                    token_id=email_log.token.id,
                    is_resend=True,
                )

                success_count += 1
                logger.info(f"🔄 재발송 예약 성공: {email_log.recipient_email}")

            except Exception as e:
                logger.error(f"❌ 재발송 예약 실패: {email_log.recipient_email} - {str(e)}")

        result = {
            "success": True,
            "total_failed": len(failed_logs),
            "retry_attempted": retry_count,
            "retry_success": success_count,
        }

        logger.info(f"📊 실패 이메일 재시도 완료: {result}")
        return result

    except Exception as e:
        logger.error(f"❌ 실패 이메일 재시도 작업 실패: {str(e)}")
        return {
            "success": False,
            "message": str(e),
        }


@shared_task(
    bind=True,
    max_retries=3,
    # 수동 self.retry 에도 적용되도록 retry_countdown 이 읽는다 (0~30, 0~60, 0~120초)
    retry_backoff=30,
    retry_backoff_max=180,
    retry_jitter=True,
    acks_late=True,
)
def send_email_task(
    self: Task,
    subject: str,
    message: str,
    recipient_list: list[str],
    user_id: int | None = None,
    email_type: str = "general",
    html_message: str | None = None,
) -> dict[str, Any]:
    """
    범용 이메일 발송 태스크 (비동기)

    비밀번호 재설정, 일반 알림 등 모든 이메일 발송에 사용 가능

    Args:
        self: Celery task 인스턴스 (bind=True)
        subject: 이메일 제목
        message: 이메일 본문 (텍스트)
        recipient_list: 수신자 이메일 리스트
        user_id: 사용자 ID (선택, 로그용)
        email_type: 이메일 유형 (선택, 기본값: "general")
        token: 관련 토큰 객체 (선택, 로그용)
        html_message: HTML 본문 (선택)

    Returns:
        dict: 발송 결과 {'success': bool, 'message': str}
    """
    try:
        # 사용자 조회 (있는 경우)
        user = None
        if user_id:
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                logger.warning(f"사용자를 찾을 수 없습니다: user_id={user_id}")

        # EmailLog 조회 또는 생성 — 이 메시지(task id)의 로그. 같은 메시지가 재배달·재시도로 다시 오면
        # 같은 로그를 찾는다. 인자(제목·수신자)로 찾으면 사용자가 정당하게 다시 요청한 메일(비밀번호 재설정
        # 두 번)까지 중복으로 막게 되므로 인자로는 판단하지 않는다.
        if user and email_type:
            log_fields = {
                "user": user,
                "email_type": email_type,
                "recipient_email": recipient_list[0] if recipient_list else "",
                "subject": subject,
                "status": "pending",
            }
            task_id = self.request.id
            if task_id:
                email_log, created = EmailLog.objects.get_or_create(task_id=task_id, defaults=log_fields)
            else:
                email_log, created = EmailLog.objects.create(**log_fields), True

            # 이미 발송 성공한 경우 중복 발송 방지
            if not created and email_log.status in SENT_EMAIL_STATUSES:
                logger.info(f"이미 발송된 이메일입니다: {recipient_list[0]}")
                return {
                    "success": True,
                    "message": "이미 발송된 이메일입니다.",
                }
        else:
            email_log = None

        # 이메일 발송
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipient_list,
            html_message=html_message,
            fail_silently=False,
        )

        # 발송 성공 처리
        if email_log:
            email_log.mark_as_sent()

        logger.info(f"✅ 이메일 발송 성공: {recipient_list}")

        return {
            "success": True,
            "message": "이메일이 성공적으로 발송되었습니다.",
            "recipients": recipient_list,
        }

    except SMTPException as e:
        # SMTP 오류는 재시도 가치 있음
        _handle_email_failure(email_log if "email_log" in locals() else None, e, str(recipient_list))
        raise retry_with_backoff(self, e)

    except Exception as e:
        # 그 외 오류는 재시도 안 함 (Fail-Fast)
        _handle_email_failure(email_log if "email_log" in locals() else None, e, str(recipient_list))
        raise
