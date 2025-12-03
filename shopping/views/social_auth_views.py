"""
소셜 로그인 OAuth 콜백 처리 뷰

각 OAuth 제공자로부터 authorization code를 받아
JWT 토큰을 발급합니다.

OAuth 인증 로직은 SocialAuthService에,
사용자 처리 로직은 UserService에 위임합니다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from django.http import HttpResponseRedirect
from django.views import View

from shopping.services.social_auth_service import SocialAuthError, SocialAuthService
from shopping.services.user_service import UserService, UserServiceError

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


class SocialCallbackView(View):
    """
    OAuth 콜백 통합 처리 뷰

    Google, Kakao, Naver의 OAuth 콜백을 처리하고
    JWT 토큰을 발급하여 프론트엔드로 리다이렉트합니다.

    OAuth 인증: SocialAuthService
    사용자 처리: UserService
    """

    def get(self, request: "HttpRequest"):
        """OAuth 콜백 처리"""
        code = request.GET.get("code")
        state = request.GET.get("state", "")
        error = request.GET.get("error")

        # 에러 체크
        if error:
            error_desc = request.GET.get("error_description", error)
            return self._redirect_with_error(error_desc)

        if not code:
            return self._redirect_with_error("Authorization code가 없습니다.")

        # state에서 provider 추출 (format: provider_randomstring)
        provider = state.split("_")[0] if "_" in state else None

        if not SocialAuthService.is_supported_provider(provider):
            return self._redirect_with_error(f"지원하지 않는 OAuth 제공자: {provider}")

        try:
            # 1. OAuth 인증 처리 (SocialAuthService)
            # redirect_uri는 서비스에서 설정 값 사용
            user_info = SocialAuthService.process_oauth_callback(
                provider=provider,
                code=code,
            )

            # 2. 소셜 로그인 처리 (UserService)
            result = UserService.process_social_login(
                provider=provider,
                user_info=user_info,
            )

            # 3. 프론트엔드로 리다이렉트 (토큰 포함)
            return self._redirect_with_tokens(
                result.tokens["access"],
                result.tokens["refresh"],
            )

        except SocialAuthError as e:
            logger.error(f"소셜 인증 에러: {e.message}")
            return self._redirect_with_error(e.message)

        except UserServiceError as e:
            logger.error(f"소셜 로그인 서비스 에러: {e.message}")
            return self._redirect_with_error(e.message)

        except Exception as e:
            logger.exception(f"소셜 로그인 처리 중 오류: {e}")
            return self._redirect_with_error(str(e))

    def _redirect_with_tokens(self, access_token: str, refresh_token: str) -> HttpResponseRedirect:
        """토큰과 함께 프론트엔드로 리다이렉트"""
        # 테스트 페이지로 리다이렉트 (프론트엔드 URL로 변경 가능)
        redirect_url = "/api/social/test/"
        params = urlencode(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
            }
        )
        return HttpResponseRedirect(f"{redirect_url}?{params}")

    def _redirect_with_error(self, error_message: str) -> HttpResponseRedirect:
        """에러와 함께 프론트엔드로 리다이렉트"""
        redirect_url = "/api/social/test/"
        params = urlencode(
            {
                "error": "oauth_error",
                "error_description": error_message,
            }
        )
        return HttpResponseRedirect(f"{redirect_url}?{params}")
