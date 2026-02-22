"""
Unix Domain Socket 테스트용 클라이언트.

UDS 서버와의 통신을 테스트하기 위한 Python 클라이언트입니다.
타언어 SDK 구현의 참조 구현으로도 사용됩니다.

Usage:
    from selfhealing.adapters.ipc.uds_client import UDSClient

    client = UDSClient()
    client.connect()

    # Circuit Breaker 상태 확인
    result = client.should_allow("payment_gateway")
    print(f"Allowed: {result['allowed']}")

    # 배치 확인
    results = client.should_allow_batch(["service1", "service2"])

    client.close()
"""

from __future__ import annotations

import json
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any

import structlog

from selfhealing.adapters.ipc.protocol.json_rpc import (
    JSONRPCErrorCode,
    JSONRPCRequest,
    JSONRPCResponse,
    SidecarMethods,
)

logger = structlog.get_logger()


@dataclass
class ClientStats:
    """클라이언트 통계."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        """평균 지연 시간."""
        if self.total_requests == 0:
            return 0.0
        return self.total_latency_ms / self.total_requests


class UDSClientError(Exception):
    """UDS 클라이언트 에러."""

    pass


class UDSClient:
    """
    Unix Domain Socket 클라이언트.

    UDS 서버에 JSON-RPC 요청을 보내고 응답을 받습니다.
    Fail-Open 정책을 지원하여 연결 실패 시에도 기본값을 반환합니다.
    """

    # 기본 소켓 경로 (플랫폼별)
    if sys.platform == "win32":
        DEFAULT_SOCKET_PATH = r"\\.\pipe\selfhealing_ipc"
    else:
        DEFAULT_SOCKET_PATH = "/tmp/selfhealing.sock"

    DEFAULT_TIMEOUT = 5.0  # 초

    def __init__(
        self,
        socket_path: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        auth_token: str | None = None,
        fail_open: bool = True,
    ):
        """
        클라이언트 초기화.

        Args:
            socket_path: 소켓 파일 경로
            timeout: 요청 타임아웃 (초)
            auth_token: 인증 토큰
            fail_open: 연결 실패 시 기본값 반환 여부
        """
        self._socket_path = socket_path or self.DEFAULT_SOCKET_PATH
        self._timeout = timeout
        self._auth_token = auth_token
        self._fail_open = fail_open
        self._socket: socket.socket | None = None
        self._connected = False
        self._request_id = 0
        self._stats = ClientStats()

    def connect(self) -> bool:
        """
        서버 연결.

        Returns:
            연결 성공 여부
        """
        if self._connected:
            return True

        try:
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.settimeout(self._timeout)
            self._socket.connect(self._socket_path)
            self._connected = True
            logger.info(
                "uds_client.connected",
                self=self._socket_path,
            )
            return True
        except Exception as e:
            logger.warning(
                "uds_client.connection_failed",
                error=e,
            )
            self._socket = None
            self._connected = False
            return False

    def close(self) -> None:
        """연결 종료."""
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        self._connected = False
        logger.debug("uds_client.closed")

    def is_connected(self) -> bool:
        """연결 상태 확인."""
        return self._connected and self._socket is not None

    def _next_request_id(self) -> str:
        """다음 요청 ID 생성."""
        self._request_id += 1
        return f"req-{self._request_id}"

    def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        JSON-RPC 요청 전송.

        Args:
            method: 메서드 이름
            params: 파라미터
            metadata: 메타데이터 (traceparent 등)

        Returns:
            응답 결과

        Raises:
            UDSClientError: 통신 오류 시
        """
        if not self._connected:
            if not self.connect():
                raise UDSClientError("Not connected")

        request_id = self._next_request_id()
        request_dict: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": request_id,
        }

        if params:
            request_dict["params"] = params
        if metadata:
            request_dict["metadata"] = metadata
        if self._auth_token:
            request_dict["auth"] = {"token": self._auth_token}

        start_time = time.time()
        self._stats.total_requests += 1

        try:
            # 요청 전송
            data = json.dumps(request_dict).encode("utf-8") + b"\n"
            self._socket.sendall(data)

            # 응답 수신
            response_data = self._recv_response()
            response = json.loads(response_data.decode("utf-8"))

            # 통계 업데이트
            latency = (time.time() - start_time) * 1000
            self._stats.total_latency_ms += latency

            # 에러 확인
            if "error" in response:
                self._stats.failed_requests += 1
                error = response["error"]
                raise UDSClientError(f"RPC Error [{error.get('code')}]: {error.get('message')}")

            self._stats.successful_requests += 1
            return response.get("result", {})

        except socket.timeout:
            self._stats.failed_requests += 1
            self._connected = False
            raise UDSClientError("Request timeout")
        except (OSError, BrokenPipeError) as e:
            self._stats.failed_requests += 1
            self._connected = False
            raise UDSClientError(f"Socket error: {e}")

    def _recv_response(self) -> bytes:
        """응답 수신."""
        data = b""
        while True:
            chunk = self._socket.recv(4096)
            if not chunk:
                raise UDSClientError("Connection closed")
            data += chunk
            if b"\n" in data:
                return data.split(b"\n")[0]

    # =========================================================================
    # Circuit Breaker API
    # =========================================================================

    def should_allow(
        self,
        service_name: str,
        traceparent: str | None = None,
    ) -> dict[str, Any]:
        """
        서킷 브레이커 요청 허용 여부 확인.

        Args:
            service_name: 서비스 이름
            traceparent: W3C traceparent (분산 트레이싱)

        Returns:
            {"allowed": bool, "state": str}
        """
        metadata = {"traceparent": traceparent} if traceparent else None

        try:
            return self._send_request(
                SidecarMethods.CB_SHOULD_ALLOW,
                params={"service_name": service_name},
                metadata=metadata,
            )
        except UDSClientError as e:
            if self._fail_open:
                logger.warning(
                    "uds_client.fail_open",
                    error=e,
                )
                return {"allowed": True, "state": "unknown", "fail_open": True}
            raise

    def should_allow_batch(
        self,
        service_names: list[str],
        traceparent: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        배치 요청 허용 여부 확인.

        Args:
            service_names: 서비스 이름 목록
            traceparent: W3C traceparent

        Returns:
            {service_name: {"allowed": bool, "state": str}}
        """
        metadata = {"traceparent": traceparent} if traceparent else None

        try:
            return self._send_request(
                SidecarMethods.CB_SHOULD_ALLOW_BATCH,
                params={"service_names": service_names},
                metadata=metadata,
            )
        except UDSClientError as e:
            if self._fail_open:
                logger.warning(
                    "uds_client.fail_open_batch",
                    error=e,
                )
                return {name: {"allowed": True, "state": "unknown", "fail_open": True} for name in service_names}
            raise

    def get_state(self, service_name: str) -> dict[str, Any]:
        """서킷 브레이커 상태 조회."""
        try:
            return self._send_request(
                SidecarMethods.CB_GET_STATE,
                params={"service_name": service_name},
            )
        except UDSClientError as e:
            if self._fail_open:
                return {"service_name": service_name, "state": "unknown"}
            raise

    def force_open(
        self,
        service_name: str,
        reason: str = "Client request",
        controlled_by: str = "uds_client",
    ) -> dict[str, Any]:
        """서킷 브레이커 강제 열기."""
        return self._send_request(
            SidecarMethods.CB_FORCE_OPEN,
            params={
                "service_name": service_name,
                "reason": reason,
                "controlled_by": controlled_by,
            },
        )

    def force_close(
        self,
        service_name: str,
        reason: str = "Client request",
        controlled_by: str = "uds_client",
        trigger_replay: bool = False,
    ) -> dict[str, Any]:
        """서킷 브레이커 강제 닫기."""
        return self._send_request(
            SidecarMethods.CB_FORCE_CLOSE,
            params={
                "service_name": service_name,
                "reason": reason,
                "controlled_by": controlled_by,
                "trigger_replay": trigger_replay,
            },
        )

    # =========================================================================
    # DLQ API
    # =========================================================================

    def dlq_store(
        self,
        domain: str,
        failure_type: str,
        error_message: str,
        snapshot_data: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """DLQ에 실패 작업 저장."""
        params = {
            "domain": domain,
            "failure_type": failure_type,
            "error_message": error_message,
            "snapshot_data": snapshot_data,
            **kwargs,
        }
        return self._send_request(SidecarMethods.DLQ_STORE, params=params)

    def dlq_replay(
        self,
        entry_id: int,
        actor_id: str = "uds_client",
        force: bool = False,
    ) -> dict[str, Any]:
        """DLQ 엔트리 재처리."""
        return self._send_request(
            SidecarMethods.DLQ_REPLAY,
            params={
                "entry_id": entry_id,
                "actor_id": actor_id,
                "force": force,
            },
        )

    # =========================================================================
    # Buffer API
    # =========================================================================

    def buffer_store(
        self,
        entry_type: str,
        data: dict[str, Any],
        dedup_key: str | None = None,
    ) -> dict[str, Any]:
        """버퍼에 데이터 저장."""
        return self._send_request(
            SidecarMethods.BUFFER_STORE,
            params={
                "entry_type": entry_type,
                "data": data,
                "dedup_key": dedup_key,
            },
        )

    def buffer_get_stats(self) -> dict[str, Any]:
        """버퍼 상태 조회."""
        try:
            return self._send_request(SidecarMethods.BUFFER_GET_STATS)
        except UDSClientError:
            return {"state": "unavailable"}

    # =========================================================================
    # Health API
    # =========================================================================

    def health_check(self) -> dict[str, Any]:
        """건강 상태 확인."""
        try:
            return self._send_request(SidecarMethods.HEALTH_CHECK)
        except UDSClientError:
            return {"status": "UNAVAILABLE"}

    def health_details(self) -> dict[str, Any]:
        """상세 건강 정보."""
        try:
            return self._send_request(SidecarMethods.HEALTH_GET_DETAILS)
        except UDSClientError:
            return {"overall_status": "unavailable"}

    # =========================================================================
    # Stats
    # =========================================================================

    @property
    def stats(self) -> ClientStats:
        """클라이언트 통계."""
        return self._stats

    def get_stats_dict(self) -> dict[str, Any]:
        """통계 딕셔너리."""
        return {
            "socket_path": self._socket_path,
            "connected": self._connected,
            "total_requests": self._stats.total_requests,
            "successful_requests": self._stats.successful_requests,
            "failed_requests": self._stats.failed_requests,
            "avg_latency_ms": round(self._stats.avg_latency_ms, 3),
        }

    def __enter__(self) -> "UDSClient":
        """컨텍스트 매니저 진입."""
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """컨텍스트 매니저 종료."""
        self.close()


# =============================================================================
# Fail-Open 래퍼 클라이언트
# =============================================================================


class FailOpenUDSClient(UDSClient):
    """
    Fail-Open 정책을 강제하는 UDS 클라이언트.

    사이드카 장애 시에도 애플리케이션이 계속 동작하도록 보장합니다.
    """

    def __init__(
        self,
        socket_path: str | None = None,
        timeout: float = 1.0,  # 더 짧은 타임아웃
        auth_token: str | None = None,
    ):
        super().__init__(
            socket_path=socket_path,
            timeout=timeout,
            auth_token=auth_token,
            fail_open=True,  # 항상 fail-open
        )

    def should_allow(
        self,
        service_name: str,
        traceparent: str | None = None,
    ) -> bool:
        """
        간소화된 요청 허용 여부 확인.

        Returns:
            bool: 허용 여부 (장애 시 True)
        """
        result = super().should_allow(service_name, traceparent)
        return result.get("allowed", True)
