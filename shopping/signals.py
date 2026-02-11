from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.signals import pre_social_login

from shopping.models.email_verification import EmailVerificationToken
from shopping.models.order import Order

if TYPE_CHECKING:
    from allauth.socialaccount.models import SocialLogin
    from django.http import HttpRequest


@receiver(pre_social_login)
def handle_social_login(sender: Any, request: HttpRequest, sociallogin: SocialLogin, **kwargs: Any) -> None:
    """
    소셜 로그인 시그널 핸들러

    소셜 로그인이 발생하면 자동으로:
    1. 사용자의 is_email_verified를 True로 설정
    2. 기존 이메일 인증 토큰 무효화

    Args:
        sender: 시그널을 보낸 객체
        request: HTTP 요청 객체
        sociallogin: SocialLogin 인스턴스
        **kwargs: 추가 매개변수
    """

    # 소셜 로그인으로 연결된 사용자 가져오기
    user = sociallogin.user

    # 신규 가입인 경우 (user.pk가 None이면 아직 DB에 저장 안 됨)
    if not user.pk:
        return

    # 이메일 자동 인증 처리
    if not user.is_email_verified:
        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])
        print(f"✅ 소셜 로그인: {user.email} 이메일 자동 인증 완료")

    # 기존 이메일 인증 토큰 무효화 (이미 소셜로 인증됨)
    updated_count = EmailVerificationToken.objects.filter(user=user, is_used=False).update(is_used=True)

    if updated_count > 0:
        print(f"🔐 소셜 로그인: {user.email} 기존 인증 토큰 무효화 완료")


@receiver(post_save, sender=SocialAccount)
def handle_new_social_account(sender: type[SocialAccount], instance: SocialAccount, created: bool, **kwargs: Any) -> None:
    """
    소셜 계정 생성 시그널 핸들러 (신규 가입자)

    신규 소셜 가입 시:
    1. 자동으로 이메일 인증 처리
    2. 기존 미사용 토큰 무효화

    Args:
        sender: SocialAccount 모델
        instance: 생성된 SocialAccount 인스턴스
        created: 신규 생성 여부
        **kwargs: 추가 매개변수
    """
    # 신규 생성된 소셜 계정만 처리
    if not created:
        return

    user = instance.user

    # 이메일이 없으면 스킵
    if not user.email:
        print(f"⚠️ 소셜 가입: {user.username} - 이메일 없음")
        return

    if user.is_email_verified:
        return

    # 이메일 자동 인증 처리
    user.is_email_verified = True
    user.save(update_fields=["is_email_verified"])
    print(f"✅ 소셜 가입 (신규): {user.email} 이메일 자동 인증 완료")

    # 혹시 있을 수 있는 기존 토큰 무효화
    updated_count = EmailVerificationToken.objects.filter(user=user, is_used=False).update(is_used=True)

    if updated_count > 0:
        print(f"🔐 소셜 가입: {user.email} 기존 인증 토큰 {updated_count}개 무효화 완료")


@receiver(post_save, sender=Order)
def generate_order_number(sender: type[Order], instance: Order, created: bool, **kwargs: Any) -> None:
    """
    주문 생성 시 주문번호 자동 생성

    주문이 생성되면 (created=True) order_number가 없는 경우에만
    YYYYMMDD + 6자리 ID 형식으로 주문번호를 생성합니다.

    예: 202401150000042

    Args:
        sender: Order 모델
        instance: 생성된 Order 인스턴스
        created: 신규 생성 여부
        **kwargs: 추가 매개변수
    """
    # 신규 생성이고 주문번호가 없는 경우에만 처리
    if created and not instance.order_number:
        # 날짜 + ID 형식으로 주문번호 생성
        date_str = timezone.now().strftime("%Y%m%d")
        order_number = f"{date_str}{instance.id:06d}"

        # update()를 사용하여 단일 쿼리로 업데이트 (signal 재귀 방지)
        Order.objects.filter(pk=instance.pk).update(order_number=order_number)

        # 인스턴스도 업데이트
        instance.order_number = order_number


# =============================================================================
# Session Registry Signal Handlers
# =============================================================================


@receiver(user_logged_in)
def on_user_login_register_session(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
    """
    로그인 시 UserSessionRegistry에 session_key 매핑 등록.

    Redis 세션 백엔드에서는 user_id → session_key 역방향 조회가 불가능하므로,
    로그인 시점에 매핑을 등록하여 세션 무효화 시 역방향 조회를 지원한다.
    """
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key

    if session_key and user and user.pk:
        try:
            from selfhealing.services.security.session_registry import (
                get_user_session_registry,
            )

            registry = get_user_session_registry()
            registry.register(user.pk, session_key)
        except ImportError:
            pass  # selfhealing 미설치 환경


@receiver(user_logged_out)
def on_user_logout_unregister_session(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
    """
    로그아웃 시 UserSessionRegistry에서 session_key 매핑 제거.

    로그인 시 등록한 session_key 매핑을 제거하여 불필요한 무효화 시도를 방지한다.
    """
    session_key = getattr(request.session, "session_key", None)
    if session_key and user and user.pk:
        try:
            from selfhealing.services.security.session_registry import (
                get_user_session_registry,
            )

            registry = get_user_session_registry()
            registry.unregister(user.pk, session_key)
        except ImportError:
            pass
