"""
IPC 예외 클래스 단위 테스트.

테스트 항목:
- 각 예외 클래스 생성 및 속성
- 에러 코드 확인
- 메시지 형식
"""

from __future__ import annotations

from selfhealing.adapters.ipc.exceptions import (
    IPCAuthenticationError,
    IPCAuthorizationError,
    IPCCircuitBreakerOpenError,
    IPCConnectionError,
    IPCError,
    IPCInternalError,
    IPCInvalidParamsError,
    IPCMethodNotFoundError,
    IPCParseError,
    IPCRateLimitedError,
    IPCServiceUnavailableError,
    IPCTimeoutError,
)


class TestIPCError:
    """IPCError 기본 예외 테스트."""

    def test_create_with_message(self):
        """메시지로 생성."""
        error = IPCError("Test error")

        assert str(error) == "Test error"
        assert error.message == "Test error"
        assert error.code is None

    def test_create_with_code(self):
        """코드 포함 생성."""
        error = IPCError("Test error", code=-32600)

        assert error.code == -32600

    def test_is_exception(self):
        """Exception 상속 확인."""
        error = IPCError("Test")

        assert isinstance(error, Exception)


class TestIPCConnectionError:
    """IPCConnectionError 테스트."""

    def test_default_message(self):
        """기본 메시지."""
        error = IPCConnectionError()

        assert "connect" in str(error).lower()
        assert error.code == -32003

    def test_custom_message(self):
        """커스텀 메시지."""
        error = IPCConnectionError("Custom connection error")

        assert "Custom connection error" in str(error)


class TestIPCTimeoutError:
    """IPCTimeoutError 테스트."""

    def test_default_message(self):
        """기본 메시지."""
        error = IPCTimeoutError()

        assert "timed out" in str(error).lower()
        assert error.code == -32003

    def test_with_timeout_value(self):
        """타임아웃 값 포함."""
        error = IPCTimeoutError("Timeout after 5s", timeout=5.0)

        assert error.timeout == 5.0


class TestIPCAuthenticationError:
    """IPCAuthenticationError 테스트."""

    def test_default_message(self):
        """기본 메시지."""
        error = IPCAuthenticationError()

        assert "authentication" in str(error).lower()
        assert error.code == -32001

    def test_custom_message(self):
        """커스텀 메시지."""
        error = IPCAuthenticationError("Invalid token")

        assert "Invalid token" in str(error)


class TestIPCAuthorizationError:
    """IPCAuthorizationError 테스트."""

    def test_default_message(self):
        """기본 메시지."""
        error = IPCAuthorizationError()

        assert "authorization" in str(error).lower()
        assert error.code == -32002


class TestIPCMethodNotFoundError:
    """IPCMethodNotFoundError 테스트."""

    def test_with_method_name(self):
        """메서드 이름 포함."""
        error = IPCMethodNotFoundError("unknown.method")

        assert "unknown.method" in str(error)
        assert error.method == "unknown.method"
        assert error.code == -32601


class TestIPCInvalidParamsError:
    """IPCInvalidParamsError 테스트."""

    def test_default_message(self):
        """기본 메시지."""
        error = IPCInvalidParamsError()

        assert error.code == -32602

    def test_with_param_name(self):
        """파라미터 이름 포함."""
        error = IPCInvalidParamsError("Missing param", param_name="service_name")

        assert error.param_name == "service_name"


class TestIPCParseError:
    """IPCParseError 테스트."""

    def test_default_message(self):
        """기본 메시지."""
        error = IPCParseError()

        assert "parse" in str(error).lower()
        assert error.code == -32700


class TestIPCInternalError:
    """IPCInternalError 테스트."""

    def test_default_message(self):
        """기본 메시지."""
        error = IPCInternalError()

        assert "internal" in str(error).lower()
        assert error.code == -32603

    def test_with_cause(self):
        """원인 예외 포함."""
        cause = ValueError("Original error")
        error = IPCInternalError("Wrapped error", cause=cause)

        assert error.cause is cause


class TestIPCRateLimitedError:
    """IPCRateLimitedError 테스트."""

    def test_default_message(self):
        """기본 메시지."""
        error = IPCRateLimitedError()

        assert "rate limit" in str(error).lower()
        assert error.code == -32004

    def test_with_retry_after(self):
        """재시도 시간 포함."""
        error = IPCRateLimitedError("Too many requests", retry_after=60.0)

        assert error.retry_after == 60.0


class TestIPCCircuitBreakerOpenError:
    """IPCCircuitBreakerOpenError 테스트."""

    def test_with_service_name(self):
        """서비스 이름 포함."""
        error = IPCCircuitBreakerOpenError("payment_service")

        assert "payment_service" in str(error)
        assert error.service_name == "payment_service"
        assert error.code == -32005

    def test_custom_message(self):
        """커스텀 메시지."""
        error = IPCCircuitBreakerOpenError("test", message="Custom CB error")

        assert "Custom CB error" in str(error)


class TestIPCServiceUnavailableError:
    """IPCServiceUnavailableError 테스트."""

    def test_default_message(self):
        """기본 메시지."""
        error = IPCServiceUnavailableError()

        assert "unavailable" in str(error).lower()
        assert error.code == -32003

    def test_with_service_name(self):
        """서비스 이름 포함."""
        error = IPCServiceUnavailableError(service_name="dlq")

        assert "dlq" in str(error)
        assert error.service_name == "dlq"


class TestExceptionHierarchy:
    """예외 계층 구조 테스트."""

    def test_all_inherit_from_ipc_error(self):
        """모든 예외가 IPCError를 상속."""
        exceptions = [
            IPCConnectionError(),
            IPCTimeoutError(),
            IPCAuthenticationError(),
            IPCAuthorizationError(),
            IPCMethodNotFoundError("test"),
            IPCInvalidParamsError(),
            IPCParseError(),
            IPCInternalError(),
            IPCRateLimitedError(),
            IPCCircuitBreakerOpenError("test"),
            IPCServiceUnavailableError(),
        ]

        for exc in exceptions:
            assert isinstance(exc, IPCError)
            assert isinstance(exc, Exception)

    def test_can_catch_by_base_class(self):
        """기본 클래스로 잡을 수 있음."""
        try:
            raise IPCMethodNotFoundError("test.method")
        except IPCError as e:
            assert "test.method" in str(e)


class TestJSONRPCErrorCodes:
    """JSON-RPC 에러 코드 테스트."""

    def test_standard_error_codes(self):
        """표준 에러 코드."""
        # Parse Error
        assert IPCParseError().code == -32700

        # Invalid Request (not directly mapped, using InvalidParams)
        assert IPCInvalidParamsError().code == -32602

        # Method Not Found
        assert IPCMethodNotFoundError("test").code == -32601

        # Internal Error
        assert IPCInternalError().code == -32603

    def test_custom_error_codes(self):
        """커스텀 에러 코드 (-32000 ~ -32099)."""
        # Authentication
        assert IPCAuthenticationError().code == -32001

        # Authorization
        assert IPCAuthorizationError().code == -32002

        # Service Unavailable
        assert IPCServiceUnavailableError().code == -32003

        # Rate Limited
        assert IPCRateLimitedError().code == -32004

        # Circuit Breaker Open
        assert IPCCircuitBreakerOpenError("test").code == -32005
