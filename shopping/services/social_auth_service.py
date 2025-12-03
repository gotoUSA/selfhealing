"""소셜 인증 서비스 레이어

OAuth 제공자(Google, Kakao, Naver)와의 통신 및
인증 처리를 담당합니다.

- Authorization code → Access token 교환
- 사용자 정보 조회
- 사용자 정보 정규화
- 네트워크 재시도 로직 (tenacity)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class SocialAuthError(Exception):
    """소셜 인증 관련 에러"""

    def __init__(self, message: str, code: str = "SOCIAL_AUTH_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


@dataclass
class OAuthTokens:
    """OAuth 토큰 데이터"""

    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_in: int | None = None


@dataclass
class NormalizedUserInfo:
    """정규화된 사용자 정보"""

    email: str | None
    name: str | None
    provider_id: str | None
    profile_image: str | None = None


class SocialAuthService:
    """
    소셜 인증 서비스

    OAuth 제공자와의 통신 및 인증 처리를 담당합니다.
    View에서 분리된 인프라/비즈니스 로직을 캡슐화합니다.

    설정값:
        - settings.OAUTH_REQUEST_TIMEOUT: API 요청 타임아웃 (기본 10초)
        - settings.OAUTH_RETRY_ATTEMPTS: 재시도 횟수 (기본 3회)
        - settings.OAUTH_RETRY_WAIT_SECONDS: 재시도 대기 시간 (기본 1초)
        - settings.OAUTH_CALLBACK_URI: OAuth 콜백 URI
    """

    # OAuth 제공자별 설정
    PROVIDERS = {
        "google": {
            "token_url": "https://oauth2.googleapis.com/token",
            "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
            "client_id_env": "GOOGLE_CLIENT_ID",
            "client_secret_env": "GOOGLE_CLIENT_SECRET",
        },
        "kakao": {
            "token_url": "https://kauth.kakao.com/oauth/token",
            "userinfo_url": "https://kapi.kakao.com/v2/user/me",
            "client_id_env": "KAKAO_REST_API_KEY",
            "client_secret_env": "KAKAO_CLIENT_SECRET",
        },
        "naver": {
            "token_url": "https://nid.naver.com/oauth2.0/token",
            "userinfo_url": "https://openapi.naver.com/v1/nid/me",
            "client_id_env": "NAVER_CLIENT_ID",
            "client_secret_env": "NAVER_CLIENT_SECRET",
        },
    }

    @classmethod
    def _get_timeout(cls) -> int:
        """설정에서 타임아웃 값 조회"""
        return getattr(settings, "OAUTH_REQUEST_TIMEOUT", 10)

    @classmethod
    def _get_retry_attempts(cls) -> int:
        """설정에서 재시도 횟수 조회"""
        return getattr(settings, "OAUTH_RETRY_ATTEMPTS", 3)

    @classmethod
    def _get_retry_wait(cls) -> int:
        """설정에서 재시도 대기 시간 조회"""
        return getattr(settings, "OAUTH_RETRY_WAIT_SECONDS", 1)

    @classmethod
    def get_callback_uri(cls) -> str:
        """설정에서 OAuth 콜백 URI 조회"""
        return getattr(
            settings,
            "OAUTH_CALLBACK_URI",
            "http://localhost:8000/api/social/callback/",
        )

    @classmethod
    def get_supported_providers(cls) -> list[str]:
        """지원하는 OAuth 제공자 목록 반환"""
        return list(cls.PROVIDERS.keys())

    @classmethod
    def is_supported_provider(cls, provider: str | None) -> bool:
        """지원하는 OAuth 제공자인지 확인"""
        return provider in cls.PROVIDERS

    @classmethod
    def exchange_code_for_token(
        cls,
        provider: str,
        code: str,
        redirect_uri: str | None = None,
    ) -> OAuthTokens:
        """
        Authorization code를 access token으로 교환

        Args:
            provider: OAuth 제공자 (google, kakao, naver)
            code: Authorization code
            redirect_uri: OAuth 콜백 URI (None이면 설정에서 조회)

        Returns:
            OAuthTokens: 토큰 정보

        Raises:
            SocialAuthError: 토큰 교환 실패 시
        """
        if not cls.is_supported_provider(provider):
            raise SocialAuthError(
                f"지원하지 않는 OAuth 제공자: {provider}",
                code="UNSUPPORTED_PROVIDER",
            )

        # redirect_uri가 없으면 설정에서 가져옴
        if redirect_uri is None:
            redirect_uri = cls.get_callback_uri()

        config = cls.PROVIDERS[provider]
        client_id = os.getenv(config["client_id_env"])
        client_secret = os.getenv(config["client_secret_env"])

        logger.info(f"{provider} 토큰 교환 시도 - redirect_uri: {redirect_uri}")
        logger.debug(
            f"{provider} client_id 존재: {bool(client_id)}, "
            f"client_secret 존재: {bool(client_secret)}"
        )

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }

        token_data = cls._request_token_with_retry(provider, config["token_url"], data)

        return OAuthTokens(
            access_token=token_data["access_token"],
            token_type=token_data.get("token_type", "Bearer"),
            refresh_token=token_data.get("refresh_token"),
            expires_in=token_data.get("expires_in"),
        )

    @classmethod
    def _request_token_with_retry(
        cls,
        provider: str,
        token_url: str,
        data: dict,
    ) -> dict[str, Any]:
        """
        토큰 요청 (재시도 로직 포함)

        tenacity를 사용하여 네트워크 오류 시 자동 재시도합니다.
        """

        @retry(
            stop=stop_after_attempt(cls._get_retry_attempts()),
            wait=wait_exponential(
                multiplier=cls._get_retry_wait(),
                min=1,
                max=10,
            ),
            retry=retry_if_exception_type(requests.ConnectionError),
            reraise=True,
        )
        def _do_request() -> dict[str, Any]:
            response = requests.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=cls._get_timeout(),
            )

            logger.info(f"{provider} 토큰 응답: {response.status_code}")

            if response.status_code != 200:
                error_detail = response.text
                logger.error(
                    f"{provider} 토큰 교환 실패: {response.status_code} - {error_detail}"
                )
                raise SocialAuthError(
                    f"토큰 교환 실패: {error_detail}",
                    code="TOKEN_EXCHANGE_FAILED",
                )

            result = response.json()

            if "access_token" not in result:
                error_desc = result.get("error_description", "access_token 없음")
                raise SocialAuthError(
                    f"토큰 교환 실패: {error_desc}",
                    code="TOKEN_EXCHANGE_FAILED",
                )

            return result

        try:
            return _do_request()
        except requests.RequestException as e:
            logger.error(f"{provider} 토큰 요청 오류: {e}")
            raise SocialAuthError(
                f"토큰 요청 중 오류 발생: {str(e)}",
                code="TOKEN_REQUEST_ERROR",
            ) from e

    @classmethod
    def get_user_info(cls, provider: str, access_token: str) -> dict[str, Any]:
        """
        OAuth 제공자로부터 사용자 정보 조회

        Args:
            provider: OAuth 제공자
            access_token: 액세스 토큰

        Returns:
            dict: 제공자별 원본 사용자 정보

        Raises:
            SocialAuthError: 사용자 정보 조회 실패 시
        """
        if not cls.is_supported_provider(provider):
            raise SocialAuthError(
                f"지원하지 않는 OAuth 제공자: {provider}",
                code="UNSUPPORTED_PROVIDER",
            )

        config = cls.PROVIDERS[provider]
        headers = {"Authorization": f"Bearer {access_token}"}

        return cls._request_user_info_with_retry(
            provider,
            config["userinfo_url"],
            headers,
        )

    @classmethod
    def _request_user_info_with_retry(
        cls,
        provider: str,
        userinfo_url: str,
        headers: dict,
    ) -> dict[str, Any]:
        """
        사용자 정보 요청 (재시도 로직 포함)

        tenacity를 사용하여 네트워크 오류 시 자동 재시도합니다.
        """

        @retry(
            stop=stop_after_attempt(cls._get_retry_attempts()),
            wait=wait_exponential(
                multiplier=cls._get_retry_wait(),
                min=1,
                max=10,
            ),
            retry=retry_if_exception_type(requests.ConnectionError),
            reraise=True,
        )
        def _do_request() -> dict[str, Any]:
            response = requests.get(
                userinfo_url,
                headers=headers,
                timeout=cls._get_timeout(),
            )

            if response.status_code != 200:
                logger.error(
                    f"{provider} 사용자 정보 조회 실패: {response.status_code}"
                )
                raise SocialAuthError(
                    f"사용자 정보 조회 실패: HTTP {response.status_code}",
                    code="USER_INFO_FETCH_FAILED",
                )

            return response.json()

        try:
            return _do_request()
        except requests.RequestException as e:
            logger.error(f"{provider} 사용자 정보 요청 오류: {e}")
            raise SocialAuthError(
                f"사용자 정보 요청 중 오류 발생: {str(e)}",
                code="USER_INFO_REQUEST_ERROR",
            ) from e

    @classmethod
    def normalize_user_info(cls, provider: str, raw_data: dict) -> NormalizedUserInfo:
        """
        각 제공자별 응답을 통일된 형식으로 변환

        Args:
            provider: OAuth 제공자
            raw_data: 제공자별 원본 응답

        Returns:
            NormalizedUserInfo: 정규화된 사용자 정보
        """
        if provider == "google":
            return NormalizedUserInfo(
                email=raw_data.get("email"),
                name=raw_data.get("name"),
                provider_id=raw_data.get("id"),
                profile_image=raw_data.get("picture"),
            )
        elif provider == "kakao":
            kakao_account = raw_data.get("kakao_account", {})
            profile = kakao_account.get("profile", {})
            return NormalizedUserInfo(
                email=kakao_account.get("email"),
                name=profile.get("nickname"),
                provider_id=str(raw_data.get("id")),
                profile_image=profile.get("profile_image_url"),
            )
        elif provider == "naver":
            response = raw_data.get("response", {})
            return NormalizedUserInfo(
                email=response.get("email"),
                name=response.get("name") or response.get("nickname"),
                provider_id=response.get("id"),
                profile_image=response.get("profile_image"),
            )

        # 알 수 없는 제공자 (방어적 처리)
        logger.warning(f"알 수 없는 제공자 데이터 정규화: {provider}")
        return NormalizedUserInfo(
            email=None,
            name=None,
            provider_id=None,
        )

    @classmethod
    def process_oauth_callback(
        cls,
        provider: str,
        code: str,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """
        OAuth 콜백 전체 처리

        Authorization code → Access token → User info 조회까지
        한 번에 처리합니다.

        Args:
            provider: OAuth 제공자
            code: Authorization code
            redirect_uri: OAuth 콜백 URI (None이면 설정에서 조회)

        Returns:
            dict: 정규화된 사용자 정보 (UserService에 전달할 형식)

        Raises:
            SocialAuthError: OAuth 처리 실패 시
        """
        logger.info(f"OAuth 콜백 처리 시작: provider={provider}")

        # redirect_uri가 없으면 설정에서 가져옴
        if redirect_uri is None:
            redirect_uri = cls.get_callback_uri()

        # 1. Authorization code → Access token
        oauth_tokens = cls.exchange_code_for_token(provider, code, redirect_uri)

        # 2. Access token → User info
        raw_user_info = cls.get_user_info(provider, oauth_tokens.access_token)

        # 3. User info 정규화
        normalized = cls.normalize_user_info(provider, raw_user_info)

        logger.info(f"OAuth 콜백 처리 완료: provider={provider}, email={normalized.email}")

        return {
            "email": normalized.email,
            "name": normalized.name,
            "provider_id": normalized.provider_id,
            "profile_image": normalized.profile_image,
        }
