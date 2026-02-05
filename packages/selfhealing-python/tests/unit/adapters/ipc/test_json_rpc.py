"""
JSON-RPC 프로토콜 단위 테스트.

테스트 항목:
- JSONRPCRequest 생성 및 직렬화
- JSONRPCResponse 생성 및 직렬화
- JSONRPCError 생성 및 오류 코드
- JSONRPCErrorCode enum
- SidecarMethods 상수
- 메시지 파싱 및 검증
"""

from __future__ import annotations

import json

import pytest

from selfhealing.adapters.ipc.protocol.json_rpc import (
    JSONRPCError,
    JSONRPCErrorCode,
    JSONRPCParseError,
    JSONRPCRequest,
    JSONRPCResponse,
    SidecarMethods,
    create_error_response,
    parse_request,
)


class TestJSONRPCRequest:
    """JSONRPCRequest 테스트."""

    def test_create_request_with_all_fields(self):
        """모든 필드가 있는 요청 생성."""
        request = JSONRPCRequest(
            method="circuit_breaker.should_allow",
            params={"cb_id": "test_cb"},
            id="test-id-123",
        )

        assert request.method == "circuit_breaker.should_allow"
        assert request.params == {"cb_id": "test_cb"}
        assert request.id == "test-id-123"
        assert request.jsonrpc == "2.0"

    def test_create_request_without_params(self):
        """파라미터 없이 요청 생성 - 빈 딕셔너리가 기본값."""
        request = JSONRPCRequest(method="health.check")

        assert request.method == "health.check"
        assert request.params == {}  # 기본값은 빈 딕셔너리

    def test_create_request_without_id(self):
        """ID 없이 요청 생성 - notification으로 처리."""
        request = JSONRPCRequest(method="test")

        assert request.id is None
        assert request.is_notification is True

    def test_to_dict(self):
        """딕셔너리 변환."""
        request = JSONRPCRequest(
            method="test",
            params={"key": "value"},
            id="id-1",
        )

        result = request.to_dict()

        assert result["jsonrpc"] == "2.0"
        assert result["method"] == "test"
        assert result["params"] == {"key": "value"}
        assert result["id"] == "id-1"

    def test_to_dict_without_empty_params(self):
        """빈 params는 딕셔너리에 포함되지 않음."""
        request = JSONRPCRequest(method="test", id="id-1")

        result = request.to_dict()

        assert "params" not in result  # 빈 params는 제외

    def test_from_dict(self):
        """딕셔너리에서 생성."""
        data = {
            "jsonrpc": "2.0",
            "method": "test",
            "params": {"key": "value"},
            "id": "id-1",
        }

        request = JSONRPCRequest.from_dict(data)

        assert request.method == "test"
        assert request.params == {"key": "value"}
        assert request.id == "id-1"

    def test_from_dict_missing_method_returns_empty_string(self):
        """method 필드 누락 시 빈 문자열 반환."""
        data = {"jsonrpc": "2.0", "id": "id-1"}

        request = JSONRPCRequest.from_dict(data)
        # from_dict는 빈 문자열을 기본값으로 사용
        assert request.method == ""


class TestJSONRPCResponse:
    """JSONRPCResponse 테스트."""

    def test_create_success_response(self):
        """성공 응답 생성."""
        response = JSONRPCResponse(
            result={"allowed": True},
            id="test-id",
        )

        assert response.result == {"allowed": True}
        assert response.error is None
        assert response.id == "test-id"

    def test_create_error_response(self):
        """에러 응답 생성."""
        error = JSONRPCError(
            code=-32600,
            message="Invalid Request",
        )
        response = JSONRPCResponse(error=error, id="test-id")

        assert response.result is None
        assert response.error == error

    def test_is_success(self):
        """성공 여부 확인."""
        success_response = JSONRPCResponse(result={"data": 1}, id="1")
        error_response = JSONRPCResponse(
            error=JSONRPCError(-32600, "Error"),
            id="2",
        )

        assert success_response.is_success
        assert not error_response.is_success

    def test_to_dict_success(self):
        """성공 응답 딕셔너리 변환."""
        response = JSONRPCResponse(result={"key": "value"}, id="1")

        result = response.to_dict()

        assert "result" in result
        assert "error" not in result

    def test_to_dict_error(self):
        """에러 응답 딕셔너리 변환."""
        error = JSONRPCError(-32600, "Invalid Request", {"detail": "x"})
        response = JSONRPCResponse(error=error, id="1")

        result = response.to_dict()

        assert "error" in result
        assert "result" not in result
        assert result["error"]["code"] == -32600

    def test_to_json(self):
        """JSON 직렬화."""
        response = JSONRPCResponse(result={"allowed": True}, id="1")

        json_str = response.to_json()
        parsed = json.loads(json_str)

        assert parsed["result"] == {"allowed": True}
        assert parsed["id"] == "1"


class TestJSONRPCError:
    """JSONRPCError 테스트."""

    def test_create_error(self):
        """에러 생성."""
        error = JSONRPCError(
            code=-32600,
            message="Invalid Request",
            data={"detail": "missing field"},
        )

        assert error.code == -32600
        assert error.message == "Invalid Request"
        assert error.data == {"detail": "missing field"}

    def test_to_dict(self):
        """딕셔너리 변환."""
        error = JSONRPCError(-32600, "Invalid Request")

        result = error.to_dict()

        assert result["code"] == -32600
        assert result["message"] == "Invalid Request"
        assert "data" not in result

    def test_to_dict_with_data(self):
        """데이터 포함 딕셔너리 변환."""
        error = JSONRPCError(-32600, "Error", {"key": "value"})

        result = error.to_dict()

        assert result["data"] == {"key": "value"}


class TestJSONRPCErrorCode:
    """JSONRPCErrorCode enum 테스트."""

    def test_standard_error_codes(self):
        """JSON-RPC 2.0 표준 에러 코드."""
        assert JSONRPCErrorCode.PARSE_ERROR == -32700
        assert JSONRPCErrorCode.INVALID_REQUEST == -32600
        assert JSONRPCErrorCode.METHOD_NOT_FOUND == -32601
        assert JSONRPCErrorCode.INVALID_PARAMS == -32602
        assert JSONRPCErrorCode.INTERNAL_ERROR == -32603

    def test_selfhealing_custom_error_codes(self):
        """Self-Healing 전용 에러 코드."""
        assert JSONRPCErrorCode.AUTHENTICATION_ERROR == -32001
        assert JSONRPCErrorCode.AUTHORIZATION_ERROR == -32002
        assert JSONRPCErrorCode.SERVICE_UNAVAILABLE == -32003
        assert JSONRPCErrorCode.RATE_LIMITED == -32004
        assert JSONRPCErrorCode.CIRCUIT_BREAKER_OPEN == -32005


class TestSidecarMethods:
    """SidecarMethods 상수 테스트."""

    def test_circuit_breaker_methods(self):
        """Circuit Breaker 메서드."""
        assert SidecarMethods.CB_SHOULD_ALLOW == "circuit_breaker.should_allow"
        assert SidecarMethods.CB_SHOULD_ALLOW_BATCH == "circuit_breaker.should_allow_batch"
        assert SidecarMethods.CB_GET_STATE == "circuit_breaker.get_state"
        assert SidecarMethods.CB_GET_ALL_STATES == "circuit_breaker.get_all_states"
        assert SidecarMethods.CB_FORCE_OPEN == "circuit_breaker.force_open"
        assert SidecarMethods.CB_FORCE_CLOSE == "circuit_breaker.force_close"

    def test_dlq_methods(self):
        """DLQ 메서드."""
        assert SidecarMethods.DLQ_STORE == "dlq.store"
        assert SidecarMethods.DLQ_REPLAY == "dlq.replay"
        assert SidecarMethods.DLQ_GET_ENTRY == "dlq.get_entry"
        assert SidecarMethods.DLQ_LIST == "dlq.list"

    def test_buffer_methods(self):
        """Buffer 메서드."""
        assert SidecarMethods.BUFFER_STORE == "buffer.store"
        assert SidecarMethods.BUFFER_FLUSH == "buffer.flush"
        assert SidecarMethods.BUFFER_GET_STATS == "buffer.get_stats"

    def test_learning_methods(self):
        """Learning 메서드."""
        assert SidecarMethods.LEARNING_RECORD_SUCCESS == "learning.record_success"
        assert SidecarMethods.LEARNING_RECORD_FAILURE == "learning.record_failure"
        assert SidecarMethods.LEARNING_GET_SUGGESTIONS == "learning.get_suggestions"
        assert SidecarMethods.LEARNING_ANALYZE_PATTERNS == "learning.analyze_patterns"

    def test_health_methods(self):
        """Health 메서드."""
        assert SidecarMethods.HEALTH_CHECK == "health.check"
        assert SidecarMethods.HEALTH_GET_DETAILS == "health.get_details"

    def test_all_methods_returns_list(self):
        """all_methods() 메서드 테스트."""
        methods = SidecarMethods.all_methods()

        assert isinstance(methods, list)
        assert "circuit_breaker.should_allow" in methods
        assert "dlq.store" in methods
        assert "health.check" in methods


class TestParseRequest:
    """parse_request 함수 테스트."""

    def test_parse_valid_json(self):
        """유효한 JSON 파싱."""
        json_str = '{"jsonrpc": "2.0", "method": "test", "id": "1"}'

        request = parse_request(json_str)

        assert request.method == "test"

    def test_parse_invalid_json(self):
        """잘못된 JSON 파싱 - JSONRPCParseError 발생."""
        with pytest.raises(JSONRPCParseError, match="Invalid JSON"):
            parse_request("not valid json")

    def test_parse_bytes(self):
        """바이트 문자열 파싱."""
        json_bytes = b'{"jsonrpc": "2.0", "method": "test", "id": "1"}'

        request = parse_request(json_bytes)

        assert request.method == "test"

    def test_parse_missing_jsonrpc_version(self):
        """jsonrpc 버전 누락 시 에러."""
        with pytest.raises(JSONRPCParseError, match="Invalid or missing jsonrpc"):
            parse_request('{"method": "test", "id": "1"}')

    def test_parse_missing_method(self):
        """method 누락 시 에러."""
        with pytest.raises(JSONRPCParseError, match="Missing or invalid method"):
            parse_request('{"jsonrpc": "2.0", "id": "1"}')


class TestCreateErrorResponse:
    """create_error_response 함수 테스트."""

    def test_create_error_response(self):
        """에러 응답 생성."""
        response = create_error_response(
            request_id="test-id",
            code=-32600,
            message="Invalid Request",
        )

        assert response.error.code == -32600
        assert response.error.message == "Invalid Request"
        assert response.id == "test-id"

    def test_create_error_response_with_data(self):
        """데이터 포함 에러 응답 생성."""
        response = create_error_response(
            request_id="1",
            code=-32600,
            message="Error",
            data={"detail": "info"},
        )

        assert response.error.data == {"detail": "info"}

    def test_create_error_response_with_error_code_enum(self):
        """JSONRPCErrorCode enum 사용."""
        response = create_error_response(
            request_id="1",
            code=JSONRPCErrorCode.METHOD_NOT_FOUND,
            message="Method not found",
        )

        assert response.error.code == -32601
