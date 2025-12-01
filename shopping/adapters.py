"""
소셜 로그인 커스텀 어댑터

django-allauth의 기본 동작을 오버라이드하여
API 방식 소셜 로그인에 최적화

목적:
- 웹 기반 signup 페이지 리다이렉트 방지
- 자동 가입 처리 (API 응답으로 JWT 반환)
- 이메일 자동 인증 처리
- 기존 이메일 계정에 소셜 계정 자동 연결
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model

if TYPE_CHECKING:
    from allauth.socialaccount.models import SocialLogin
    from django.contrib.auth.models import AbstractBaseUser
    from django.http import HttpRequest

User = get_user_model()


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    API 전용 소셜 계정 어댑터

    django-allauth가 웹 페이지로 리다이렉트하는 것을 방지하고
    API 응답으로 직접 처리
    """

    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin) -> None:
        """
        소셜 로그인 전 처리 - 기존 이메일 계정에 자동 연결

        같은 이메일로 가입된 계정이 있으면 해당 계정에 소셜 계정을 연결합니다.
        이를 통해 구글로 가입한 사용자가 네이버로도 로그인할 수 있습니다.

        보안 정책:
        - 이메일 인증이 완료된 계정에만 자동 연결 (계정 탈취 방지)
        - 또는 기존에 소셜 로그인으로 가입한 계정에 연결

        업계 표준:
        - GitHub, GitLab, Notion, Slack 등에서 사용하는 방식
        - 이메일 기반 계정 통합으로 UX 향상

        Args:
            request: HTTP 요청 객체
            sociallogin: SocialLogin 인스턴스
        """
        # 이미 연결된 소셜 계정이면 패스
        if sociallogin.is_existing:
            return

        # 소셜 계정의 이메일 가져오기
        email = sociallogin.account.extra_data.get("email")
        if not email:
            # 카카오 등은 다른 위치에 이메일이 있을 수 있음
            if sociallogin.account.provider == "kakao":
                kakao_account = sociallogin.account.extra_data.get("kakao_account", {})
                email = kakao_account.get("email")
            elif sociallogin.account.provider == "naver":
                response = sociallogin.account.extra_data.get("response", {})
                email = response.get("email")

        if not email:
            return

        # 해당 이메일로 기존 사용자가 있는지 확인
        try:
            existing_user = User.objects.get(email=email)

            # 보안 체크: 이메일 인증된 계정이거나 이미 소셜 계정이 연결된 경우만 자동 연결
            # (미인증 일반 계정에 소셜 연결 시 계정 탈취 가능성 있음)
            has_social_account = existing_user.socialaccount_set.exists()
            is_verified = getattr(existing_user, "is_email_verified", True)

            if is_verified or has_social_account:
                # 기존 사용자에게 이 소셜 계정을 연결
                sociallogin.connect(request, existing_user)
            # else: 미인증 일반 계정 → 새 계정으로 가입 시도 (에러 발생하게 둠)

        except User.DoesNotExist:
            # 새 사용자 - 정상적으로 가입 진행
            pass

    def is_auto_signup_allowed(self, request: HttpRequest, sociallogin: SocialLogin) -> bool:
        """
        자동 가입 허용 여부

        API 방식에서는 항상 자동 가입을 허용하여
        signup 페이지로 리다이렉트하지 않음

        Args:
            request: HTTP 요청 객체
            sociallogin: SocialLogin 인스턴스

        Returns:
            bool: 항상 True (자동 가입 허용)
        """
        return True

    def populate_user(self, request: HttpRequest, sociallogin: SocialLogin, data: dict[str, Any]) -> AbstractBaseUser:
        """
        소셜 로그인 데이터로 User 객체 생성/업데이트

        OAuth 제공자로부터 받은 데이터를 User 모델에 매핑
        이메일 자동 인증 처리

        Args:
            request: HTTP 요청 객체
            sociallogin: SocialLogin 인스턴스
            data: OAuth 제공자로부터 받은 사용자 데이터

        Returns:
            User: 생성/업데이트된 User 인스턴스
        """
        user = super().populate_user(request, sociallogin, data)

        # 소셜 로그인은 OAuth 제공자가 이미 이메일을 인증했으므로
        # 별도의 이메일 인증 절차 불필요
        user.is_email_verified = True

        return user

    def save_user(self, request: HttpRequest, sociallogin: SocialLogin, form: Any = None) -> AbstractBaseUser:
        """
        User 저장 시 추가 처리

        Args:
            request: HTTP 요청 객체
            sociallogin: SocialLogin 인스턴스
            form: 회원가입 폼 (API에서는 None)

        Returns:
            User: 저장된 User 인스턴스
        """
        user = super().save_user(request, sociallogin, form)

        # 이메일 자동 인증 확인 (populate_user에서 설정했지만 재확인)
        if not user.is_email_verified:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified"])

        return user
