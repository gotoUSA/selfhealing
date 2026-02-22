"""
사이드카 Static Bearer Token 인증.

사이드카 IPC 통신에서 간단한 토큰 기반 인증을 제공합니다.
프로덕션에서는 mTLS를 권장하지만, 초기 단계에서 사용할 수 있습니다.

환경변수:
    SIDECAR_AUTH_TOKEN: 인증 토큰 (설정 시 인증 활성화)
    SIDECAR_AUTH_ENABLED: 인증 활성화 여부 (기본: 토큰 존재 시 true)

Usage:
    from selfhealing.adapters.ipc.auth import SidecarAuthenticator

    auth = SidecarAuthenticator()

    # 토큰 검증
    if auth.validate("sk-selfhealing-abc123"):
        # 인증 성공
        process_request(request)
    else:
        # 인증 실패
        return error_response("Authentication failed")

    # gRPC Metadata에서 토큰 추출
    token = auth.extract_from_metadata(context.invocation_metadata())
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class AuthResult:
    """인증 결과."""

    success: bool
    """인증 성공 여부."""

    error: str | None = None
    """에러 메시지 (실패 시)."""

    client_id: str | None = None
    """클라이언트 식별자 (성공 시)."""


class SidecarAuthenticator:
    """
    Static Bearer Token 기반 사이드카 인증.

    특징:
    - 환경변수에서 토큰 로드
    - Timing-safe 비교로 타이밍 공격 방지
    - 인증 비활성화 시 모든 요청 허용

    설정:
        # 환경변수
        SIDECAR_AUTH_TOKEN=sk-selfhealing-abc123xyz789

        # sidecar-config.yaml
        auth:
          enabled: true
          method: "bearer_token"
          token: "${SIDECAR_AUTH_TOKEN}"
    """

    TOKEN_ENV_KEY = "SIDECAR_AUTH_TOKEN"
    ENABLED_ENV_KEY = "SIDECAR_AUTH_ENABLED"
    TOKEN_PREFIX = "Bearer "

    def __init__(
        self,
        token: str | None = None,
        enabled: bool | None = None,
    ):
        """
        인증자 초기화.

        Args:
            token: 인증 토큰 (None이면 환경변수에서 로드)
            enabled: 인증 활성화 여부 (None이면 자동 판단)
        """
        self._token = token or os.environ.get(self.TOKEN_ENV_KEY, "")

        if enabled is not None:
            self._enabled = enabled
        else:
            # 환경변수 확인 또는 토큰 존재 여부로 판단
            enabled_env = os.environ.get(self.ENABLED_ENV_KEY, "").lower()
            if enabled_env in ("true", "1", "yes"):
                self._enabled = True
            elif enabled_env in ("false", "0", "no"):
                self._enabled = False
            else:
                # 토큰이 있으면 인증 활성화
                self._enabled = bool(self._token)

        if self._enabled and not self._token:
            logger.warning("sidecar_auth.authentication_enabled_no_token")
        elif self._enabled:
            logger.info("sidecar_auth.bearer_token_authentication_enabled")
        else:
            logger.debug("sidecar_auth.authentication_disabled")

    @property
    def is_enabled(self) -> bool:
        """인증 활성화 여부."""
        return self._enabled

    def validate(self, provided_token: str) -> AuthResult:
        """
        토큰 검증 (Timing-safe).

        Args:
            provided_token: 클라이언트가 제공한 토큰

        Returns:
            AuthResult 객체
        """
        if not self._enabled:
            return AuthResult(success=True, client_id="anonymous")

        if not self._token:
            return AuthResult(
                success=False,
                error="Server authentication token not configured",
            )

        if not provided_token:
            return AuthResult(
                success=False,
                error="No authentication token provided",
            )

        # Bearer 접두사 제거
        if provided_token.startswith(self.TOKEN_PREFIX):
            provided_token = provided_token[len(self.TOKEN_PREFIX) :]

        # Timing-safe 비교 (타이밍 공격 방지)
        if hmac.compare_digest(self._token, provided_token):
            return AuthResult(success=True, client_id="authenticated")

        return AuthResult(
            success=False,
            error="Invalid authentication token",
        )

    def validate_request(self, request_auth: dict[str, Any]) -> AuthResult:
        """
        JSON-RPC 요청의 auth 필드에서 토큰 추출 및 검증.

        Args:
            request_auth: 요청의 auth 딕셔너리 {"token": "..."}

        Returns:
            AuthResult 객체
        """
        if not self._enabled:
            return AuthResult(success=True, client_id="anonymous")

        token = request_auth.get("token", "")
        return self.validate(token)

    def extract_from_metadata(
        self,
        metadata: list[tuple[str, str]] | None,
    ) -> str:
        """
        gRPC Metadata에서 Authorization 헤더 추출.

        Args:
            metadata: gRPC invocation_metadata() 결과

        Returns:
            추출된 토큰 (없으면 빈 문자열)
        """
        if not metadata:
            return ""

        for key, value in metadata:
            if key.lower() == "authorization":
                return value

        return ""

    def validate_grpc_metadata(
        self,
        metadata: list[tuple[str, str]] | None,
    ) -> AuthResult:
        """
        gRPC Metadata의 Authorization 헤더 검증.

        Args:
            metadata: gRPC invocation_metadata() 결과

        Returns:
            AuthResult 객체
        """
        if not self._enabled:
            return AuthResult(success=True, client_id="anonymous")

        token = self.extract_from_metadata(metadata)
        return self.validate(token)

    def create_auth_header(self) -> dict[str, str]:
        """
        인증 헤더 생성 (클라이언트용).

        Returns:
            Authorization 헤더 딕셔너리
        """
        if not self._token:
            return {}
        return {"Authorization": f"{self.TOKEN_PREFIX}{self._token}"}

    def create_request_auth(self) -> dict[str, str]:
        """
        JSON-RPC 요청용 auth 필드 생성.

        Returns:
            auth 딕셔너리 {"token": "..."}
        """
        if not self._token:
            return {}
        return {"token": self._token}


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_authenticator: SidecarAuthenticator | None = None


def get_sidecar_authenticator() -> SidecarAuthenticator:
    """싱글톤 인증자 인스턴스 반환."""
    global _authenticator
    if _authenticator is None:
        _authenticator = SidecarAuthenticator()
    return _authenticator


def reset_sidecar_authenticator() -> None:
    """인증자 인스턴스 리셋 (테스트용)."""
    global _authenticator
    _authenticator = None
