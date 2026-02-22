"""
JSON-RPC 2.0 프로토콜 - UDS 통신용.

Unix Domain Socket을 통한 사이드카 IPC 통신에 사용되는
JSON-RPC 2.0 요청/응답 프로토콜을 정의합니다.

Specification: https://www.jsonrpc.org/specification

Usage:
    from selfhealing.adapters.ipc.protocol.json_rpc import (
        JSONRPCRequest,
        JSONRPCResponse,
        parse_request,
        create_success_response,
        create_error_response,
    )

    # 요청 파싱
    request = parse_request(raw_json)

    # 성공 응답 생성
    response = create_success_response(request.id, {"allowed": True})

    # 에러 응답 생성
    response = create_error_response(
        request.id,
        JSONRPCErrorCode.METHOD_NOT_FOUND,
        "Method not found"
    )
"""

from __future__ import annotations

import json
import structlog
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

logger = structlog.get_logger()


class JSONRPCErrorCode(IntEnum):
    """JSON-RPC 2.0 표준 에러 코드."""

    # 표준 에러 코드
    PARSE_ERROR = -32700
    """잘못된 JSON."""

    INVALID_REQUEST = -32600
    """유효하지 않은 JSON-RPC 요청."""

    METHOD_NOT_FOUND = -32601
    """존재하지 않는 메서드."""

    INVALID_PARAMS = -32602
    """유효하지 않은 파라미터."""

    INTERNAL_ERROR = -32603
    """내부 서버 에러."""

    # Self-Healing 전용 에러 코드 (-32000 ~ -32099)
    AUTHENTICATION_ERROR = -32001
    """인증 실패."""

    AUTHORIZATION_ERROR = -32002
    """권한 없음."""

    SERVICE_UNAVAILABLE = -32003
    """서비스 불가."""

    RATE_LIMITED = -32004
    """요청 제한 초과."""

    CIRCUIT_BREAKER_OPEN = -32005
    """서킷 브레이커 열림."""


@dataclass
class JSONRPCError:
    """JSON-RPC 에러 객체."""

    code: int
    """에러 코드."""

    message: str
    """에러 메시지."""

    data: dict[str, Any] | None = None
    """추가 에러 정보 (선택)."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.data is not None:
            result["data"] = self.data
        return result


@dataclass
class JSONRPCRequest:
    """
    JSON-RPC 2.0 요청 객체.

    요청 형식:
        {
            "jsonrpc": "2.0",
            "method": "circuit_breaker.should_allow",
            "params": {"service_name": "payment_gateway"},
            "id": "req-001",
            "metadata": {
                "traceparent": "00-...",
                "tracestate": "..."
            },
            "auth": {
                "token": "sk-selfhealing-abc123"
            }
        }
    """

    method: str
    """호출할 메서드 이름."""

    id: str | int | None = None
    """요청 ID (notification이면 None)."""

    params: dict[str, Any] = field(default_factory=dict)
    """메서드 파라미터."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터 (traceparent 등)."""

    auth: dict[str, Any] = field(default_factory=dict)
    """인증 정보."""

    jsonrpc: str = "2.0"
    """JSON-RPC 버전."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JSONRPCRequest:
        """딕셔너리에서 요청 객체 생성."""
        return cls(
            method=data.get("method", ""),
            id=data.get("id"),
            params=data.get("params", {}),
            metadata=data.get("metadata", {}),
            auth=data.get("auth", {}),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        result: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
        }
        if self.params:
            result["params"] = self.params
        if self.id is not None:
            result["id"] = self.id
        if self.metadata:
            result["metadata"] = self.metadata
        if self.auth:
            result["auth"] = self.auth
        return result

    @property
    def is_notification(self) -> bool:
        """Notification 여부 (응답 불필요)."""
        return self.id is None

    @property
    def traceparent(self) -> str | None:
        """W3C traceparent 추출."""
        return self.metadata.get("traceparent")

    @property
    def tracestate(self) -> str | None:
        """W3C tracestate 추출."""
        return self.metadata.get("tracestate")


@dataclass
class JSONRPCResponse:
    """
    JSON-RPC 2.0 응답 객체.

    성공 응답:
        {
            "jsonrpc": "2.0",
            "result": {"allowed": true, "state": "closed"},
            "id": "req-001"
        }

    에러 응답:
        {
            "jsonrpc": "2.0",
            "error": {
                "code": -32601,
                "message": "Method not found",
                "data": {"method": "unknown.method"}
            },
            "id": "req-001"
        }
    """

    id: str | int | None
    """요청 ID."""

    result: Any = None
    """성공 결과 (에러 시 None)."""

    error: JSONRPCError | None = None
    """에러 정보 (성공 시 None)."""

    jsonrpc: str = "2.0"
    """JSON-RPC 버전."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        response: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }

        if self.error is not None:
            response["error"] = self.error.to_dict()
        else:
            response["result"] = self.result

        return response

    def to_json(self) -> str:
        """JSON 문자열로 변환."""
        return json.dumps(self.to_dict())

    @property
    def is_success(self) -> bool:
        """성공 응답 여부."""
        return self.error is None


def parse_request(raw_data: str | bytes) -> JSONRPCRequest:
    """
    JSON 문자열에서 요청 객체 파싱.

    Args:
        raw_data: JSON 문자열 또는 바이트

    Returns:
        파싱된 JSONRPCRequest 객체

    Raises:
        JSONRPCParseError: 파싱 실패 시
    """
    if isinstance(raw_data, bytes):
        raw_data = raw_data.decode("utf-8")

    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as e:
        raise JSONRPCParseError(f"Invalid JSON: {e}") from e

    # 필수 필드 검증
    if not isinstance(data, dict):
        raise JSONRPCParseError("Request must be an object")

    if data.get("jsonrpc") != "2.0":
        raise JSONRPCParseError("Invalid or missing jsonrpc version")

    if "method" not in data or not isinstance(data["method"], str):
        raise JSONRPCParseError("Missing or invalid method")

    return JSONRPCRequest.from_dict(data)


def parse_batch_request(raw_data: str | bytes) -> list[JSONRPCRequest]:
    """
    배치 JSON-RPC 요청 파싱.

    Args:
        raw_data: JSON 배열 문자열

    Returns:
        JSONRPCRequest 객체 리스트
    """
    if isinstance(raw_data, bytes):
        raw_data = raw_data.decode("utf-8")

    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as e:
        raise JSONRPCParseError(f"Invalid JSON: {e}") from e

    if not isinstance(data, list):
        raise JSONRPCParseError("Batch request must be an array")

    if not data:
        raise JSONRPCParseError("Empty batch request")

    requests = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise JSONRPCParseError(f"Invalid request at index {i}")
        requests.append(JSONRPCRequest.from_dict(item))

    return requests


def create_success_response(
    request_id: str | int | None,
    result: Any,
) -> JSONRPCResponse:
    """
    성공 응답 생성.

    Args:
        request_id: 요청 ID
        result: 결과 데이터

    Returns:
        JSONRPCResponse 객체
    """
    return JSONRPCResponse(id=request_id, result=result)


def create_error_response(
    request_id: str | int | None,
    code: int | JSONRPCErrorCode,
    message: str,
    data: dict[str, Any] | None = None,
) -> JSONRPCResponse:
    """
    에러 응답 생성.

    Args:
        request_id: 요청 ID
        code: 에러 코드
        message: 에러 메시지
        data: 추가 에러 정보

    Returns:
        JSONRPCResponse 객체
    """
    if isinstance(code, JSONRPCErrorCode):
        code = code.value

    return JSONRPCResponse(
        id=request_id,
        error=JSONRPCError(code=code, message=message, data=data),
    )


class JSONRPCParseError(Exception):
    """JSON-RPC 파싱 에러."""

    pass


# =============================================================================
# Self-Healing 전용 메서드 네임스페이스
# =============================================================================


class SidecarMethods:
    """
    사이드카 IPC 메서드 상수.

    네이밍 규칙: {서비스}.{동작}
    """

    # Circuit Breaker
    CB_SHOULD_ALLOW = "circuit_breaker.should_allow"
    CB_SHOULD_ALLOW_BATCH = "circuit_breaker.should_allow_batch"
    CB_GET_STATE = "circuit_breaker.get_state"
    CB_GET_ALL_STATES = "circuit_breaker.get_all_states"
    CB_FORCE_OPEN = "circuit_breaker.force_open"
    CB_FORCE_CLOSE = "circuit_breaker.force_close"

    # DLQ
    DLQ_STORE = "dlq.store"
    DLQ_REPLAY = "dlq.replay"
    DLQ_GET_ENTRY = "dlq.get_entry"
    DLQ_LIST = "dlq.list"

    # Buffer
    BUFFER_STORE = "buffer.store"
    BUFFER_FLUSH = "buffer.flush"
    BUFFER_GET_STATS = "buffer.get_stats"

    # Learning
    LEARNING_RECORD_SUCCESS = "learning.record_success"
    LEARNING_RECORD_FAILURE = "learning.record_failure"
    LEARNING_GET_SUGGESTIONS = "learning.get_suggestions"
    LEARNING_ANALYZE_PATTERNS = "learning.analyze_patterns"

    # Health
    HEALTH_CHECK = "health.check"
    HEALTH_GET_DETAILS = "health.get_details"

    @classmethod
    def all_methods(cls) -> list[str]:
        """모든 지원 메서드 목록."""
        return [value for name, value in vars(cls).items() if isinstance(value, str) and not name.startswith("_")]
