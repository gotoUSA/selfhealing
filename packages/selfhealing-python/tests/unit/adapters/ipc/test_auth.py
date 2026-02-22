"""
SidecarAuthenticator 단위 테스트.

테스트 항목:
- 정적 Bearer Token 인증
- 환경 변수 토큰 로딩
- 타이밍 안전 비교
- gRPC 메타데이터 추출
- JSON-RPC 요청 인증
"""

from __future__ import annotations

import os
from unittest.mock import patch

from selfhealing.adapters.ipc.auth import (
    AuthResult,
    SidecarAuthenticator,
    get_sidecar_authenticator,
    reset_sidecar_authenticator,
)


class TestSidecarAuthenticator:
    """SidecarAuthenticator 테스트."""

    def test_init_with_token(self):
        """토큰으로 초기화."""
        auth = SidecarAuthenticator(token="my-secret-token")

        assert auth._token == "my-secret-token"
        assert auth.is_enabled

    def test_init_without_token_disabled(self):
        """토큰 없이 초기화 시 비활성화."""
        with patch.dict(os.environ, {}, clear=True):
            auth = SidecarAuthenticator(token="", enabled=False)

            assert not auth.is_enabled

    def test_init_from_env_var(self):
        """환경 변수에서 토큰 로딩."""
        with patch.dict(os.environ, {"SIDECAR_AUTH_TOKEN": "env-token"}, clear=True):
            auth = SidecarAuthenticator()

            assert auth._token == "env-token"
            assert auth.is_enabled

    def test_validate_valid_token_returns_auth_result(self):
        """유효한 토큰 검증 - AuthResult 반환."""
        auth = SidecarAuthenticator(token="secret-token")

        result = auth.validate("secret-token")

        assert isinstance(result, AuthResult)
        assert result.success is True
        assert result.client_id == "authenticated"
        assert result.error is None

    def test_validate_invalid_token_returns_auth_result(self):
        """잘못된 토큰 검증 - AuthResult 반환."""
        auth = SidecarAuthenticator(token="secret-token")

        result = auth.validate("wrong-token")

        assert isinstance(result, AuthResult)
        assert result.success is False
        assert result.error == "Invalid authentication token"

    def test_validate_disabled_auth_returns_success(self):
        """인증 비활성화 시 항상 성공."""
        auth = SidecarAuthenticator(token="", enabled=False)

        result = auth.validate("any-token")

        assert result.success is True
        assert result.client_id == "anonymous"

    def test_validate_bearer_prefix_removed(self):
        """Bearer 접두사 제거 후 검증."""
        auth = SidecarAuthenticator(token="my-token")

        # Bearer 접두사 있는 경우 제거 후 검증
        result = auth.validate("Bearer my-token")

        assert result.success is True

    def test_validate_empty_token_returns_error(self):
        """빈 토큰 검증 - 에러."""
        auth = SidecarAuthenticator(token="secret")

        result = auth.validate("")

        assert result.success is False
        assert result.error == "No authentication token provided"

    def test_validate_timing_safe(self):
        """타이밍 안전 비교 사용 확인."""
        auth = SidecarAuthenticator(token="secret-token")

        # hmac.compare_digest 호출 확인
        with patch("selfhealing.adapters.ipc.auth.hmac.compare_digest") as mock_compare:
            mock_compare.return_value = False

            auth.validate("wrong")

            mock_compare.assert_called_once()

    def test_extract_from_metadata_authorization(self):
        """gRPC 메타데이터에서 Authorization 헤더 추출."""
        auth = SidecarAuthenticator(token="secret")

        # gRPC 메타데이터는 튜플 리스트
        metadata = [
            ("authorization", "Bearer secret"),
            ("content-type", "application/grpc"),
        ]

        token = auth.extract_from_metadata(metadata)

        assert token == "Bearer secret"

    def test_extract_from_metadata_no_auth(self):
        """인증 헤더 없음."""
        auth = SidecarAuthenticator(token="secret")

        metadata = [("content-type", "application/grpc")]

        token = auth.extract_from_metadata(metadata)

        assert token == ""

    def test_extract_from_metadata_none(self):
        """metadata가 None인 경우."""
        auth = SidecarAuthenticator(token="secret")

        token = auth.extract_from_metadata(None)

        assert token == ""

    def test_validate_request_auth_dict(self):
        """JSON-RPC 요청 auth 딕셔너리 검증."""
        auth = SidecarAuthenticator(token="secret-token")

        request_auth = {"token": "secret-token"}

        result = auth.validate_request(request_auth)

        assert result.success is True

    def test_validate_request_invalid_token(self):
        """JSON-RPC 요청 잘못된 토큰."""
        auth = SidecarAuthenticator(token="secret-token")

        request_auth = {"token": "wrong-token"}

        result = auth.validate_request(request_auth)

        assert result.success is False

    def test_validate_request_missing_token(self):
        """JSON-RPC 요청 토큰 없음."""
        auth = SidecarAuthenticator(token="secret-token")

        request_auth = {}

        result = auth.validate_request(request_auth)

        assert result.success is False
        assert result.error == "No authentication token provided"

    def test_validate_grpc_metadata(self):
        """gRPC 메타데이터 검증."""
        auth = SidecarAuthenticator(token="grpc-token")

        metadata = [("authorization", "Bearer grpc-token")]

        result = auth.validate_grpc_metadata(metadata)

        assert result.success is True

    def test_validate_grpc_metadata_disabled(self):
        """인증 비활성화 시 gRPC 메타데이터 검증."""
        auth = SidecarAuthenticator(token="", enabled=False)

        result = auth.validate_grpc_metadata(None)

        assert result.success is True
        assert result.client_id == "anonymous"

    def test_create_auth_header(self):
        """인증 헤더 생성."""
        auth = SidecarAuthenticator(token="my-token")

        header = auth.create_auth_header()

        assert header == {"Authorization": "Bearer my-token"}

    def test_create_auth_header_no_token(self):
        """토큰 없을 때 빈 헤더."""
        auth = SidecarAuthenticator(token="", enabled=False)

        header = auth.create_auth_header()

        assert header == {}

    def test_create_request_auth(self):
        """요청용 auth 필드 생성."""
        auth = SidecarAuthenticator(token="my-token")

        auth_field = auth.create_request_auth()

        assert auth_field == {"token": "my-token"}

    def test_create_request_auth_no_token(self):
        """토큰 없을 때 빈 auth."""
        auth = SidecarAuthenticator(token="", enabled=False)

        auth_field = auth.create_request_auth()

        assert auth_field == {}


class TestAuthResult:
    """AuthResult 테스트."""

    def test_create_success_result(self):
        """성공 결과 생성."""
        result = AuthResult(success=True, client_id="test-client")

        assert result.success
        assert result.client_id == "test-client"
        assert result.error is None

    def test_create_failure_result(self):
        """실패 결과 생성."""
        result = AuthResult(success=False, error="Token invalid")

        assert not result.success
        assert result.error == "Token invalid"
        assert result.client_id is None


class TestSidecarAuthenticatorSingleton:
    """싱글톤 인스턴스 테스트."""

    def teardown_method(self):
        """테스트 후 싱글톤 리셋."""
        reset_sidecar_authenticator()

    def test_get_sidecar_authenticator_singleton(self):
        """싱글톤 인스턴스 반환."""
        auth1 = get_sidecar_authenticator()
        auth2 = get_sidecar_authenticator()

        assert auth1 is auth2

    def test_reset_sidecar_authenticator(self):
        """싱글톤 리셋."""
        auth1 = get_sidecar_authenticator()

        reset_sidecar_authenticator()

        auth2 = get_sidecar_authenticator()

        assert auth1 is not auth2


class TestSidecarAuthenticatorIntegration:
    """SidecarAuthenticator 통합 테스트."""

    def test_full_flow_json_rpc(self):
        """JSON-RPC 인증 전체 흐름."""
        auth = SidecarAuthenticator(token="json-rpc-token")

        # 클라이언트가 auth 필드 생성
        client_auth = auth.create_request_auth()
        assert client_auth == {"token": "json-rpc-token"}

        # 서버가 auth 필드 검증
        result = auth.validate_request(client_auth)
        assert result.success is True

    def test_full_flow_grpc(self):
        """gRPC 인증 전체 흐름."""
        auth = SidecarAuthenticator(token="grpc-secret-token")

        # gRPC 클라이언트 메타데이터
        metadata = [
            ("authorization", "Bearer grpc-secret-token"),
            ("grpc-accept-encoding", "gzip"),
        ]

        # 토큰 추출 및 검증
        result = auth.validate_grpc_metadata(metadata)
        assert result.success is True

    def test_disabled_auth_allows_all(self):
        """인증 비활성화 시 모든 요청 허용."""
        auth = SidecarAuthenticator(token="", enabled=False)

        # 모든 요청 허용
        assert auth.validate_request({}).success is True
        assert auth.validate_grpc_metadata(None).success is True
        assert auth.validate("anything").success is True
