"""이메일 인증 서비스 레이어

이메일 인증 관련 비즈니스 로직을 처리합니다.

현업에서 널리 사용되는 서비스 레이어 패턴 적용:
1. 단일 책임 원칙 (SRP): 이메일 인증 관련 로직만 담당
2. 트랜잭션 경계 명확화: @transaction.atomic 데코레이터로 트랜잭션 관리
3. 예외 처리 표준화: EmailVerificationServiceError로 비즈니스 로직 예외 통합
4. 로깅 표준화: 구조화된 로깅으로 디버깅 및 모니터링 용이

사용 예시:
    # 인증 이메일 발송
    result = EmailVerificationService.send_verification_email(user)

    # 토큰으로 인증
    EmailVerificationService.verify_by_token(token_str)

    # 코드로 인증
    EmailVerificationService.verify_by_code(user, code)

    # 인증 상태 확인
    status = EmailVerificationService.get_verification_status(user)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from ..models.user import User

from ..models.email_verification import EmailVerificationToken
from ..tasks.email_tasks import send_verification_email_task
from .base import ServiceError, log_service_call

logger = logging.getLogger(__name__)


class EmailVerificationServiceError(ServiceError):
    """이메일 인증 서비스 관련 에러"""

    def __init__(self, message: str, code: str = "EMAIL_VERIFICATION_ERROR", details: dict | None = None):
        super().__init__(message, code, details)


# ===== Data Transfer Objects (DTO) =====


@dataclass
class SendEmailResult:
    """이메일 발송 결과"""

    success: bool
    message: str
    token_id: int | None = None


@dataclass
class VerificationStatus:
    """인증 상태 정보"""

    is_verified: bool
    email: str
    pending_verification: bool = False
    token_expired: bool = False
    can_resend: bool = True


class EmailVerificationService:
    """
    이메일 인증 관련 비즈니스 로직 서비스

    책임:
    - 인증 이메일 발송/재발송
    - 토큰/코드 인증 처리
    - 인증 상태 조회
    - 토큰 관리 (생성, 무효화)

    Note:
        모든 메서드는 stateless하게 설계되어 있으며,
        필요한 상태는 인자로 전달받습니다.
    """

    # ===== 정책 상수 =====
    RESEND_COOLDOWN_SECONDS = 60  # 재발송 대기 시간 (초)
    TOKEN_EXPIRY_HOURS = 24  # 토큰 만료 시간 (시간)

    # ===== 이메일 발송 =====

    @staticmethod
    @log_service_call
    @transaction.atomic
    def send_verification_email(user: User, is_resend: bool = False) -> SendEmailResult:
        """
        인증 이메일 발송

        Args:
            user: 사용자
            is_resend: 재발송 여부

        Returns:
            SendEmailResult: 발송 결과

        Raises:
            EmailVerificationServiceError: 이미 인증된 경우, 재발송 제한 등
        """
        # 이미 인증된 사용자 체크
        if user.is_email_verified:
            raise EmailVerificationServiceError(
                "이미 이메일 인증이 완료되었습니다.",
                code="ALREADY_VERIFIED",
            )

        # 재발송 제한 체크
        if is_resend:
            EmailVerificationService._check_resend_cooldown(user)

        # 기존 미사용 토큰 무효화
        invalidated_count = EmailVerificationToken.objects.filter(
            user=user,
            is_used=False,
        ).update(is_used=True)

        if invalidated_count > 0:
            logger.debug(
                "[EmailVerification] 기존 토큰 무효화 | user_id=%d, count=%d",
                user.id,
                invalidated_count,
            )

        # 새 토큰 생성
        token = EmailVerificationToken.objects.create(user=user)

        # 비동기 이메일 발송 (Celery 태스크)
        send_verification_email_task.delay(
            user_id=user.id,
            token_id=token.id,
            is_resend=is_resend,
        )

        action = "재발송" if is_resend else "발송"
        logger.info(
            "[EmailVerification] 인증 이메일 %s | user_id=%d, token_id=%d",
            action,
            user.id,
            token.id,
        )

        return SendEmailResult(
            success=True,
            message=f"인증 이메일이 {action} 중입니다. 잠시 후 이메일을 확인해주세요.",
            token_id=token.id,
        )

    # ===== 인증 처리 =====

    @staticmethod
    @log_service_call
    @transaction.atomic
    def verify_by_token(token_str: str) -> User:
        """
        UUID 토큰으로 이메일 인증

        Args:
            token_str: UUID 토큰 문자열

        Returns:
            User: 인증된 사용자

        Raises:
            EmailVerificationServiceError: 토큰 없음, 만료, 이미 사용 등
        """
        if not token_str:
            raise EmailVerificationServiceError(
                "토큰이 제공되지 않았습니다.",
                code="TOKEN_MISSING",
            )

        # UUID 형식 검증
        import uuid
        try:
            uuid.UUID(str(token_str))
        except (ValueError, AttributeError):
            raise EmailVerificationServiceError(
                "Must be a valid UUID.",
                code="TOKEN_INVALID",
            )

        # 토큰 조회
        try:
            token = EmailVerificationToken.objects.select_related("user").get(token=token_str)
        except EmailVerificationToken.DoesNotExist:
            raise EmailVerificationServiceError(
                "유효하지 않은 토큰입니다.",
                code="TOKEN_INVALID",
            )

        # 토큰 검증
        EmailVerificationService._validate_token(token)

        # 인증 처리
        user = token.user
        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])

        # 토큰 사용 완료 처리 (mark_as_used 메서드 사용하여 used_at 설정)
        token.mark_as_used()

        logger.info(
            "[EmailVerification] 토큰 인증 완료 | user_id=%d, token_id=%d",
            user.id,
            token.id,
        )

        return user

    @staticmethod
    @log_service_call
    @transaction.atomic
    def verify_by_code(user: User, code: str) -> User:
        """
        6자리 코드로 이메일 인증

        Args:
            user: 사용자
            code: 6자리 인증 코드

        Returns:
            User: 인증된 사용자

        Raises:
            EmailVerificationServiceError: 코드 없음, 만료, 불일치 등
        """
        if not code:
            raise EmailVerificationServiceError(
                "인증 코드가 제공되지 않았습니다.",
                code="CODE_MISSING",
            )

        # 이미 인증된 사용자 체크
        if user.is_email_verified:
            raise EmailVerificationServiceError(
                "이미 이메일 인증이 완료되었습니다.",
                code="ALREADY_VERIFIED",
            )

        # 최신 미사용 토큰 조회
        token = (
            EmailVerificationToken.objects.filter(user=user, is_used=False)
            .order_by("-created_at")
            .first()
        )

        if not token:
            raise EmailVerificationServiceError(
                "유효한 인증 토큰이 없습니다. 인증 이메일을 다시 요청해주세요.",
                code="NO_VALID_TOKEN",
            )

        # 토큰 만료 체크
        if token.is_expired():
            raise EmailVerificationServiceError(
                "인증 코드가 만료되었습니다. 인증 이메일을 다시 요청해주세요.",
                code="CODE_EXPIRED",
            )

        # 코드 일치 확인 (대소문자 무시)
        if token.verification_code.upper() != code.upper():
            raise EmailVerificationServiceError(
                "인증 코드가 일치하지 않습니다.",
                code="CODE_MISMATCH",
            )

        # 인증 처리
        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])

        # 토큰 사용 완료 처리 (mark_as_used 메서드 사용하여 used_at 설정)
        token.mark_as_used()

        logger.info(
            "[EmailVerification] 코드 인증 완료 | user_id=%d, token_id=%d",
            user.id,
            token.id,
        )

        return user

    # ===== 상태 조회 =====

    @staticmethod
    @log_service_call
    def get_verification_status(user: User) -> VerificationStatus:
        """
        이메일 인증 상태 조회

        Args:
            user: 사용자

        Returns:
            VerificationStatus: 인증 상태 정보
        """
        status = VerificationStatus(
            is_verified=user.is_email_verified,
            email=user.email,
        )

        # 인증되지 않은 경우에만 토큰 정보 조회 (성능 최적화)
        if not user.is_email_verified:
            latest_token = (
                EmailVerificationToken.objects.filter(user=user)
                .only("id", "created_at", "is_used")
                .order_by("-created_at")
                .first()
            )

            if latest_token:
                status.pending_verification = True
                status.token_expired = latest_token.is_expired()
                status.can_resend = latest_token.can_resend() if not latest_token.is_used else True

        return status

    # ===== 토큰 관리 =====

    @staticmethod
    @log_service_call
    @transaction.atomic
    def invalidate_tokens(user: User) -> int:
        """
        사용자의 모든 미사용 토큰 무효화

        Args:
            user: 사용자

        Returns:
            int: 무효화된 토큰 수
        """
        count = EmailVerificationToken.objects.filter(
            user=user,
            is_used=False,
        ).update(is_used=True)

        if count > 0:
            logger.info(
                "[EmailVerification] 토큰 무효화 | user_id=%d, count=%d",
                user.id,
                count,
            )

        return count

    @staticmethod
    @log_service_call
    def get_active_token(user: User) -> EmailVerificationToken | None:
        """
        사용자의 활성 토큰 조회

        Args:
            user: 사용자

        Returns:
            EmailVerificationToken | None: 활성 토큰 (없으면 None)
        """
        return (
            EmailVerificationToken.objects.filter(user=user, is_used=False)
            .order_by("-created_at")
            .first()
        )

    # ===== Private Helper Methods =====

    @staticmethod
    def _check_resend_cooldown(user: User) -> None:
        """재발송 대기 시간 체크"""
        latest_token = (
            EmailVerificationToken.objects.filter(user=user)
            .order_by("-created_at")
            .first()
        )

        if latest_token and not latest_token.can_resend():
            cooldown = EmailVerificationService.RESEND_COOLDOWN_SECONDS
            raise EmailVerificationServiceError(
                f"{cooldown}초 후에 다시 시도해주세요.",
                code="RESEND_COOLDOWN",
                details={"cooldown_seconds": cooldown},
            )

    @staticmethod
    def _validate_token(token: EmailVerificationToken) -> None:
        """토큰 유효성 검증"""
        if token.is_used:
            raise EmailVerificationServiceError(
                "이미 사용된 토큰입니다.",
                code="TOKEN_USED",
            )

        if token.is_expired():
            raise EmailVerificationServiceError(
                "토큰이 만료되었습니다. 인증 이메일을 다시 요청해주세요.",
                code="TOKEN_EXPIRED",
            )

        if token.user.is_email_verified:
            raise EmailVerificationServiceError(
                "이미 이메일 인증이 완료되었습니다.",
                code="ALREADY_VERIFIED",
            )
