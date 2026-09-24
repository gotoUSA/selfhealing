"""
소셜 로그인 OAuth 콜백 처리 뷰

각 OAuth 제공자로부터 authorization code를 받아
JWT 토큰을 발급합니다.

OAuth 인증 로직은 SocialAuthService에,
사용자 처리 로직은 UserService에 위임합니다.
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from django.views import View

from shopping.services.social_auth_service import SocialAuthError, SocialAuthService
from shopping.services.user_service import UserService, UserServiceError

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

# 로그인을 시작한 브라우저가 심어 두는 state 쿠키 (social_test.html의 generateState)
OAUTH_STATE_COOKIE = "oauth_state"
OAUTH_STATE_COOKIE_PATH = "/api/social/callback/"
REFRESH_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7일 (일반 로그인과 같음)


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

        # state 대조: 이 브라우저가 시작한 로그인인지 확인 (다른 사람이 시작한 로그인을 끝내 주지 않는다)
        expected_state = request.COOKIES.get(OAUTH_STATE_COOKIE, "")
        if not state or not expected_state or not hmac.compare_digest(state, expected_state):
            logger.warning("소셜 로그인 state 불일치")
            return self._redirect_with_error("로그인 요청 정보가 일치하지 않습니다. 다시 시도해주세요.")

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
        """토큰과 함께 프론트엔드로 리다이렉트

        refresh 토큰은 일반 로그인과 같이 HttpOnly 쿠키로, access 토큰은 URL 조각(#)으로 넘긴다.
        조각은 서버로 전송되지 않아 접근 로그·Referer에 남지 않는다.
        """
        # 테스트 페이지로 리다이렉트 (프론트엔드 URL로 변경 가능)
        redirect_url = "/api/social/test/"
        response = HttpResponseRedirect(f"{redirect_url}#{urlencode({'access_token': access_token})}")
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            max_age=REFRESH_COOKIE_MAX_AGE,
            httponly=True,
            secure=not settings.DEBUG,
            samesite="Lax",
        )
        response.delete_cookie(OAUTH_STATE_COOKIE, path=OAUTH_STATE_COOKIE_PATH)
        return response

    def _redirect_with_error(self, error_message: str) -> HttpResponseRedirect:
        """에러와 함께 프론트엔드로 리다이렉트"""
        redirect_url = "/api/social/test/"
        params = urlencode(
            {
                "error": "oauth_error",
                "error_description": error_message,
            }
        )
        response = HttpResponseRedirect(f"{redirect_url}?{params}")
        response.delete_cookie(OAUTH_STATE_COOKIE, path=OAUTH_STATE_COOKIE_PATH)
        return response
