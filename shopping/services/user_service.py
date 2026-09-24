"""사용자 서비스 레이어

사용자 관련 비즈니스 로직을 처리합니다.
- 회원가입 후처리 (토큰 생성 + 이메일 발송)
- 로그인 처리 (장바구니 병합 + 로그인 정보 업데이트)
- 회원 탈퇴 처리 (상태 변경 + 토큰 무효화)
- 소셜 로그인 처리 (OAuth + 사용자 생성)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from shopping.services.token_service import TokenService

if TYPE_CHECKING:
    from shopping.models.user import User

logger = logging.getLogger(__name__)


class UserServiceError(Exception):
    """사용자 서비스 관련 에러"""

    def __init__(self, message: str, code: str = "USER_SERVICE_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


@dataclass
class LoginResult:
    """로그인 처리 결과"""

    user: "User"
    tokens: dict[str, str]
    cart_merged: bool = False


@dataclass
class WithdrawResult:
    """회원 탈퇴 처리 결과"""

    success: bool
    message: str
    invalidated_tokens: int = 0


@dataclass
class SocialLoginResult:
    """소셜 로그인 처리 결과"""

    user: "User"
    tokens: dict[str, str]
    is_new_user: bool = False


class UserService:
    """사용자 관련 비즈니스 로직을 처리하는 서비스"""

    @staticmethod
    def send_verification_email(user, token) -> dict:
        """
        이메일 인증 발송 로직

        Args:
            user: 사용자 객체
            token: EmailVerificationToken 객체

        Returns:
            dict: 발송 결과 및 verification_code (DEBUG 모드에서만)
        """
        from shopping.tasks.email_tasks import send_verification_email_task

        logger.info(f"이메일 인증 발송 시작: user_id={user.id}, email={user.email}")

        # 비동기 이메일 발송 (Celery 태스크)
        send_verification_email_task.delay(
            user_id=user.id,
            token_id=token.id,
            is_resend=False,
        )

        result = {"message": "인증 이메일을 발송했습니다."}

        # DEBUG 모드에서만 verification_code 반환
        if settings.DEBUG:
            result["verification_code"] = token.verification_code
            logger.debug(f"DEBUG 모드: verification_code={token.verification_code}")

        logger.info(f"이메일 인증 발송 완료: user_id={user.id}")
        return result

    @staticmethod
    def create_tokens_for_user(user) -> dict:
        """
        사용자용 JWT 토큰 생성

        Args:
            user: 사용자 객체

        Returns:
            dict: access, refresh 토큰
        """
        refresh = RefreshToken.for_user(user)
        logger.info(f"JWT 토큰 생성: user_id={user.id}, username={user.username}")

        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

    @staticmethod
    def register_user(user) -> dict:
        """
        회원가입 후처리 로직 (토큰 생성 + 이메일 발송)

        Args:
            user: 생성된 사용자 객체

        Returns:
            dict: tokens, verification_result
        """
        from shopping.models.email_verification import EmailVerificationToken

        logger.info(f"회원가입 후처리 시작: user_id={user.id}, email={user.email}")

        # JWT 토큰 생성
        tokens = UserService.create_tokens_for_user(user)

        # 이메일 인증 토큰 생성
        verification_token = EmailVerificationToken.objects.create(user=user)
        logger.info(f"이메일 인증 토큰 생성: token_id={verification_token.id}")

        # 인증 이메일 발송
        verification_result = UserService.send_verification_email(user, verification_token)

        logger.info(f"회원가입 후처리 완료: user_id={user.id}")

        return {
            "tokens": tokens,
            "verification_result": verification_result,
        }

    @staticmethod
    def login_user(
        user: "User",
        session_key: str | None = None,
        request_meta: dict[str, Any] | None = None,
    ) -> LoginResult:
        """
        로그인 처리 로직

        비즈니스 로직:
        1. 비회원 장바구니 병합 (세션 키가 있는 경우)
        2. 마지막 로그인 시간 업데이트
        3. 로그인 IP 업데이트
        4. JWT 토큰 생성

        Args:
            user: 인증된 사용자 객체
            session_key: 비회원 세션 키 (장바구니 병합용)
            request_meta: request.META 딕셔너리 (IP 추출용)

        Returns:
            LoginResult: 로그인 처리 결과
        """
        logger.info(f"로그인 처리 시작: user_id={user.id}, username={user.username}")

        cart_merged = False

        # 1. 비회원 장바구니 병합
        if session_key:
            try:
                from shopping.models.cart import Cart

                Cart.merge_anonymous_cart(user, session_key)
                cart_merged = True
                logger.info(f"장바구니 병합 완료: user_id={user.id}, session_key={session_key}")
            except Exception as e:
                # 병합 실패해도 로그인은 진행 (에러 무시)
                logger.warning(f"장바구니 병합 실패: user_id={user.id}, error={str(e)}")

        # 2. 마지막 로그인 시간 업데이트
        user.last_login = timezone.now()

        # 3. 로그인 IP 업데이트
        if request_meta:
            ip = UserService._extract_client_ip(request_meta)
            user.last_login_ip = ip
            logger.debug(f"로그인 IP 업데이트: user_id={user.id}, ip={ip}")

        user.save(update_fields=["last_login", "last_login_ip"])

        # 4. JWT 토큰 생성
        tokens = UserService.create_tokens_for_user(user)

        logger.info(f"로그인 처리 완료: user_id={user.id}")

        return LoginResult(
            user=user,
            tokens=tokens,
            cart_merged=cart_merged,
        )

    @staticmethod
    def _extract_client_ip(request_meta: dict[str, Any]) -> str:
        """
        요청 메타데이터에서 클라이언트 IP 추출

        Args:
            request_meta: request.META 딕셔너리

        Returns:
            str: 클라이언트 IP 주소
        """
        x_forwarded_for = request_meta.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request_meta.get("REMOTE_ADDR", "")

    @staticmethod
    def withdraw_user(user: "User") -> WithdrawResult:
        """
        회원 탈퇴 처리 로직

        비즈니스 로직 (트랜잭션 보장):
        1. 사용자 탈퇴 상태 변경 (is_withdrawn, withdrawn_at, is_active)
        2. 모든 JWT 토큰 무효화 (블랙리스트 추가)

        Args:
            user: 탈퇴할 사용자 객체

        Returns:
            WithdrawResult: 탈퇴 처리 결과
        """
        logger.info(f"회원 탈퇴 처리 시작: user_id={user.id}, username={user.username}")

        with transaction.atomic():
            # 1. 사용자 탈퇴 처리
            user.is_withdrawn = True
            user.withdrawn_at = timezone.now()
            user.is_active = False
            user.save(update_fields=["is_withdrawn", "withdrawn_at", "is_active"])
            logger.info(f"사용자 탈퇴 상태 변경 완료: user_id={user.id}")

            # 2. 모든 JWT 토큰 무효화 (로그인·회전으로 발급한 살아 있는 refresh 토큰 전부)
            invalidated_count = TokenService.revoke_all_for_user(user)

            logger.info(f"JWT 토큰 무효화 완료: user_id={user.id}, invalidated={invalidated_count}")

        return WithdrawResult(
            success=True,
            message="회원 탈퇴가 완료되었습니다.",
            invalidated_tokens=invalidated_count,
        )

    @staticmethod
    def process_social_login(
        provider: str,
        user_info: dict[str, Any],
    ) -> SocialLoginResult:
        """
        소셜 로그인 처리 로직

        비즈니스 로직:
        1. 이메일로 기존 사용자 확인
           - 탈퇴·비활성 계정은 거부
           - 이메일 인증을 마쳤거나 소셜 계정이 연결된 계정에만 로그인 (어댑터와 같은 규칙)
        2. 없으면 새 사용자 생성
        3. JWT 토큰 발급

        Args:
            provider: OAuth 제공자 (google, kakao, naver)
            user_info: 정규화된 사용자 정보
                - email: 이메일 주소
                - name: 사용자 이름
                - provider_id: 제공자별 고유 ID
                - profile_image: 프로필 이미지 URL (선택)

        Returns:
            SocialLoginResult: 소셜 로그인 처리 결과
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()

        email = user_info.get("email")
        provider_id = user_info.get("provider_id")
        name = user_info.get("name")

        logger.info(f"소셜 로그인 처리 시작: provider={provider}, email={email}")

        # 1. 이메일이 없는 경우 대체 이메일 생성
        if not email:
            if provider_id:
                email = f"{provider}_{provider_id}@social.local"
                logger.info(f"{provider} 이메일 없음, 대체 이메일 생성: {email}")
            else:
                raise UserServiceError(
                    "이메일 정보가 없습니다. OAuth 제공자 설정에서 이메일 권한을 확인하세요.",
                    code="EMAIL_NOT_PROVIDED",
                )

        # 2. 기존 사용자 확인 또는 새 사용자 생성
        is_new_user = False
        try:
            user = User.objects.get(email=email)

            if not user.is_active:
                logger.warning(f"비활성 계정 소셜 로그인 거부: user_id={user.id} via {provider}")
                raise UserServiceError("탈퇴했거나 비활성화된 계정입니다.", code="ACCOUNT_INACTIVE")

            # 미인증 일반 계정에는 이메일이 같다는 이유만으로 로그인시키지 않는다
            # (CustomSocialAccountAdapter.pre_social_login과 같은 연결 규칙)
            if not (user.is_email_verified or user.socialaccount_set.exists()):
                logger.warning(f"미인증 계정 소셜 로그인 거부: user_id={user.id} via {provider}")
                raise UserServiceError(
                    "이 이메일로 가입된 계정이 있지만 이메일 인증이 되어 있지 않습니다. "
                    "기존 계정으로 로그인해 이메일 인증을 마친 뒤 다시 시도해주세요.",
                    code="EMAIL_NOT_VERIFIED",
                )

            # 기존 사용자 로그인 시간 업데이트
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])
            logger.info(f"기존 사용자 로그인: email={email} via {provider}")
        except User.DoesNotExist:
            # 새 사용자 생성
            username = UserService._generate_unique_username(email, provider, provider_id)
            user = User.objects.create_user(
                username=username,
                email=email,
                is_email_verified=True,  # 소셜 로그인은 이메일 인증 완료
            )

            # 이름 설정 (있으면)
            if name:
                user.first_name = name
                user.save(update_fields=["first_name"])

            is_new_user = True
            logger.info(f"새 사용자 생성: email={email} via {provider}")

        # 3. JWT 토큰 생성
        tokens = UserService.create_tokens_for_user(user)

        logger.info(f"소셜 로그인 처리 완료: user_id={user.id}, is_new={is_new_user}")

        return SocialLoginResult(
            user=user,
            tokens=tokens,
            is_new_user=is_new_user,
        )

    @staticmethod
    def _generate_unique_username(
        email: str,
        provider: str,
        provider_id: str | None,
    ) -> str:
        """
        고유한 username 생성

        Args:
            email: 이메일 주소
            provider: OAuth 제공자
            provider_id: 제공자별 고유 ID

        Returns:
            str: 고유한 username
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()

        base_username = email.split("@")[0]
        username = f"{base_username}_{provider}"

        # 중복 체크
        counter = 1
        original_username = username
        while User.objects.filter(username=username).exists():
            username = f"{original_username}_{counter}"
            counter += 1

        return username
