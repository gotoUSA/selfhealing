"""비밀번호 재설정 서비스 레이어

비밀번호 재설정 관련 비즈니스 로직을 처리합니다.
- 재설정 요청 (토큰 생성 + 이메일 발송)
- 재설정 확인 (비밀번호 변경 + 토큰 무효화)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from shopping.models.password_reset import PasswordResetToken
    from shopping.models.user import User

logger = logging.getLogger(__name__)


class PasswordResetServiceError(Exception):
    """비밀번호 재설정 서비스 관련 에러"""

    def __init__(self, message: str, code: str = "PASSWORD_RESET_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


@dataclass
class PasswordResetRequestResult:
    """비밀번호 재설정 요청 결과"""

    success: bool
    message: str


@dataclass
class PasswordResetConfirmResult:
    """비밀번호 재설정 확인 결과"""

    success: bool
    message: str
    user: "User | None" = None


class PasswordResetService:
    """비밀번호 재설정 관련 비즈니스 로직을 처리하는 서비스"""

    @staticmethod
    def request_password_reset(user: "User") -> PasswordResetRequestResult:
        """
        비밀번호 재설정 요청 처리

        비즈니스 로직:
        1. 재설정 토큰 생성 (이전 토큰 무효화)
        2. 재설정 링크 생성
        3. 이메일 발송 (비동기)
        4. 이메일 로그 생성

        Args:
            user: 비밀번호 재설정을 요청한 사용자

        Returns:
            PasswordResetRequestResult: 요청 처리 결과
        """
        from shopping.models.email_verification import EmailLog
        from shopping.models.password_reset import PasswordResetToken
        from shopping.tasks.email_tasks import send_email_task

        logger.info(f"비밀번호 재설정 요청 시작: user_id={user.id}, email={user.email}")

        # 1. 토큰 생성 (이전 미사용 토큰 자동 무효화)
        raw_token = PasswordResetToken.generate_token(user, invalidate_previous=True)
        logger.debug(f"비밀번호 재설정 토큰 생성 완료: user_id={user.id}")

        # 2. 재설정 링크 생성
        frontend_url = settings.FRONTEND_URL
        reset_link = f"{frontend_url}/password-reset?token={raw_token}&email={user.email}"

        # 3. 이메일 발송 (비동기)
        email_subject = "[Django 쇼핑몰] 비밀번호 재설정 안내"
        email_message = f"""
안녕하세요 {user.username}님,

비밀번호 재설정을 요청하셨습니다.
아래 링크를 클릭하여 새로운 비밀번호를 설정해주세요.

재설정 링크: {reset_link}

※ 이 링크는 24시간 동안 유효합니다.
※ 본인이 요청하지 않았다면 이 이메일을 무시하세요.

감사합니다.
        """.strip()

        send_email_task.delay(
            subject=email_subject,
            message=email_message,
            recipient_list=[user.email],
            user_id=user.id,
            email_type="password_reset",
        )
        logger.info(f"비밀번호 재설정 이메일 발송 요청: user_id={user.id}")

        # 4. 이메일 로그 생성
        EmailLog.objects.create(
            user=user,
            email_type="password_reset",
            recipient_email=user.email,
            subject=email_subject,
            status="pending",
        )

        logger.info(f"비밀번호 재설정 요청 완료: user_id={user.id}")

        return PasswordResetRequestResult(
            success=True,
            message="해당 이메일로 비밀번호 재설정 링크가 발송되었습니다. 이메일을 확인해주세요.",
        )

    @staticmethod
    def confirm_password_reset(
        user: "User",
        token_obj: "PasswordResetToken",
        new_password: str,
    ) -> PasswordResetConfirmResult:
        """
        비밀번호 재설정 확인 처리

        비즈니스 로직 (트랜잭션 보장):
        1. 비밀번호 변경
        2. 토큰 사용 처리 (is_used=True, used_at 설정)
        3. EmailLog 상태 업데이트

        Args:
            user: 비밀번호를 변경할 사용자
            token_obj: 검증된 PasswordResetToken 객체
            new_password: 새 비밀번호

        Returns:
            PasswordResetConfirmResult: 처리 결과
        """
        from shopping.models.email_verification import EmailLog

        logger.info(f"비밀번호 재설정 확인 시작: user_id={user.id}")

        with transaction.atomic():
            # 1. 비밀번호 변경
            user.set_password(new_password)
            user.save(update_fields=["password"])
            logger.info(f"비밀번호 변경 완료: user_id={user.id}")

            # 2. 토큰 사용 처리
            token_obj.mark_as_used()
            logger.debug(f"토큰 사용 처리 완료: token_id={token_obj.id}")

            # 3. EmailLog 업데이트 (있는 경우)
            updated_count = EmailLog.objects.filter(
                user=user,
                email_type="password_reset",
                status="sent",
                sent_at__gte=token_obj.created_at,
            ).update(status="verified", verified_at=timezone.now())

            if updated_count > 0:
                logger.debug(f"EmailLog 업데이트 완료: count={updated_count}")

        logger.info(f"비밀번호 재설정 확인 완료: user_id={user.id}")

        return PasswordResetConfirmResult(
            success=True,
            message="비밀번호가 성공적으로 변경되었습니다. 새 비밀번호로 로그인해주세요.",
            user=user,
        )
