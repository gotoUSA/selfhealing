"""
Unix Domain Socket 기반 JSON-RPC 서버.

타언어 애플리케이션(Go, Java 등)이 Self-Healing API를 사용할 수 있도록
UDS 기반 JSON-RPC 서버를 제공합니다.

특징:
- Unix Domain Socket으로 낮은 오버헤드
- JSON-RPC 2.0 프로토콜
- 배치 요청 지원
- Static Bearer Token 인증
- CB 상태 로컬 캐싱

Usage:
    from selfhealing.adapters.ipc.uds_server import UDSServer

    server = UDSServer()
    server.start()

    # 종료
    server.stop()
"""

from __future__ import annotations

import json
import os
import select
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from selfhealing.adapters.ipc.auth import (
    SidecarAuthenticator,
    get_sidecar_authenticator,
)
from selfhealing.adapters.ipc.cb_state_cache import IPCStateCache, get_cb_state_cache
from selfhealing.adapters.ipc.protocol.json_rpc import (
    JSONRPCErrorCode,
    JSONRPCParseError,
    JSONRPCResponse,
    SidecarMethods,
    create_error_response,
    create_success_response,
    parse_request,
)

logger = structlog.get_logger()


@dataclass
class ServerStats:
    """서버 통계."""

    total_requests: int = 0
    """총 요청 수."""

    successful_requests: int = 0
    """성공 요청 수."""

    failed_requests: int = 0
    """실패 요청 수."""

    auth_failures: int = 0
    """인증 실패 수."""

    total_connections: int = 0
    """총 연결 수."""

    active_connections: int = 0
    """활성 연결 수."""


class UDSServer:
    """
    Unix Domain Socket 기반 Self-Healing API 서버.

    기존 서비스를 감싸서 IPC로 노출:
    - CircuitBreakerService
    - DLQService
    - LearningService
    """

    # 기본 소켓 경로 (플랫폼별)
    if sys.platform == "win32":
        # Windows Named Pipe 에뮬레이션 (WSL용)
        DEFAULT_SOCKET_PATH = r"\\.\pipe\selfhealing_ipc"
    else:
        DEFAULT_SOCKET_PATH = "/tmp/selfhealing.sock"

    MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB
    BACKLOG = 128

    def __init__(
        self,
        socket_path: str | None = None,
        authenticator: SidecarAuthenticator | None = None,
        cb_cache: IPCStateCache | None = None,
    ):
        """
        UDS 서버 초기화.

        Args:
            socket_path: 소켓 파일 경로 (None = 기본값)
            authenticator: 인증자 (None = 싱글톤 사용)
            cb_cache: CB 상태 캐시 (None = 싱글톤 사용)
        """
        self._socket_path = socket_path or self.DEFAULT_SOCKET_PATH
        self._authenticator = authenticator or get_sidecar_authenticator()
        self._cb_cache = cb_cache or get_cb_state_cache()

        self._server_socket: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._stats = ServerStats()

        # 서비스 인스턴스 (lazy loading)
        self._cb_service: Any = None
        self._dlq_service: Any = None
        self._learning_service: Any = None

        # 메서드 핸들러 매핑
        self._handlers: dict[str, Callable[[dict], dict]] = {
            SidecarMethods.CB_SHOULD_ALLOW: self._handle_cb_should_allow,
            SidecarMethods.CB_SHOULD_ALLOW_BATCH: self._handle_cb_should_allow_batch,
            SidecarMethods.CB_GET_STATE: self._handle_cb_get_state,
            SidecarMethods.CB_GET_ALL_STATES: self._handle_cb_get_all_states,
            SidecarMethods.CB_FORCE_OPEN: self._handle_cb_force_open,
            SidecarMethods.CB_FORCE_CLOSE: self._handle_cb_force_close,
            SidecarMethods.DLQ_STORE: self._handle_dlq_store,
            SidecarMethods.DLQ_REPLAY: self._handle_dlq_replay,
            SidecarMethods.DLQ_GET_ENTRY: self._handle_dlq_get_entry,
            SidecarMethods.DLQ_LIST: self._handle_dlq_list,
            SidecarMethods.BUFFER_STORE: self._handle_buffer_store,
            SidecarMethods.BUFFER_FLUSH: self._handle_buffer_flush,
            SidecarMethods.BUFFER_GET_STATS: self._handle_buffer_get_stats,
            SidecarMethods.LEARNING_GET_SUGGESTIONS: self._handle_learning_get_suggestions,
            SidecarMethods.HEALTH_CHECK: self._handle_health_check,
            SidecarMethods.HEALTH_GET_DETAILS: self._handle_health_get_details,
        }

    # =========================================================================
    # Service Lazy Loading
    # =========================================================================

    @property
    def cb_service(self) -> Any:
        """CircuitBreakerService 인스턴스."""
        if self._cb_service is None:
            try:
                from selfhealing.services import get_circuit_breaker_service

                self._cb_service = get_circuit_breaker_service()
            except ImportError as e:
                logger.warning(
                    "uds_server.circuitbreakerservice_available",
                    error=e,
                )
        return self._cb_service

    @property
    def dlq_service(self) -> Any:
        """DLQService 인스턴스."""
        if self._dlq_service is None:
            try:
                from selfhealing.services import get_dlq_service

                self._dlq_service = get_dlq_service()
            except ImportError as e:
                logger.warning(
                    "uds_server.dlqservice_available",
                    error=e,
                )
        return self._dlq_service

    @property
    def learning_service(self) -> Any:
        """LearningService 인스턴스."""
        if self._learning_service is None:
            try:
                from selfhealing.services.learning import LearningService

                self._learning_service = LearningService()
            except ImportError as e:
                logger.warning(
                    "uds_server.learningservice_available",
                    error=e,
                )
        return self._learning_service

    # =========================================================================
    # Server Lifecycle
    # =========================================================================

    def start(self, background: bool = True) -> None:
        """
        서버 시작.

        Args:
            background: 백그라운드 스레드로 실행 여부
        """
        if self._running:
            logger.warning("uds_server.server_already_running")
            return

        # 기존 소켓 파일 제거
        self._cleanup_socket_file()

        # 소켓 생성
        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(self._socket_path)
        self._server_socket.listen(self.BACKLOG)
        self._server_socket.setblocking(False)

        # 소켓 파일 권한 설정 (660)
        if sys.platform != "win32":
            os.chmod(self._socket_path, 0o660)

        self._running = True
        logger.info(
            "uds_server.started",
            socket_path=self._socket_path,
        )

        if background:
            self._thread = threading.Thread(
                target=self._accept_loop,
                daemon=True,
                name="UDSServer-AcceptLoop",
            )
            self._thread.start()
        else:
            self._accept_loop()

    def stop(self, timeout: float = 5.0) -> None:
        """
        서버 종료.

        Args:
            timeout: 종료 대기 시간 (초)
        """
        self._running = False

        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

        self._cleanup_socket_file()
        logger.info("uds_server.stopped")

    def _cleanup_socket_file(self) -> None:
        """소켓 파일 정리."""
        if sys.platform != "win32" and os.path.exists(self._socket_path):
            try:
                os.unlink(self._socket_path)
            except OSError:
                pass

    def _accept_loop(self) -> None:
        """연결 수락 루프."""
        while self._running and self._server_socket:
            try:
                # select로 비차단 수락
                ready, _, _ = select.select([self._server_socket], [], [], 0.5)
                if not ready:
                    continue

                conn, _ = self._server_socket.accept()
                self._stats.total_connections += 1
                self._stats.active_connections += 1

                # 연결 처리 스레드 시작
                thread = threading.Thread(
                    target=self._handle_connection,
                    args=(conn,),
                    daemon=True,
                    name=f"UDSServer-Conn-{self._stats.total_connections}",
                )
                thread.start()

            except OSError:
                # 소켓 종료됨
                break
            except Exception as e:
                logger.exception(
                    "uds_server.accept_error",
                    error=e,
                )

    def _handle_connection(self, conn: socket.socket) -> None:
        """개별 연결 처리."""
        try:
            conn.settimeout(30.0)

            while self._running:
                # 메시지 수신
                data = self._recv_message(conn)
                if not data:
                    break

                # 요청 처리
                response = self._process_request(data)

                # 응답 전송
                self._send_message(conn, response)

        except TimeoutError:
            pass
        except Exception as e:
            logger.debug(
                "uds_server.connection_error",
                error=e,
            )
        finally:
            self._stats.active_connections -= 1
            try:
                conn.close()
            except Exception:
                pass

    def _recv_message(self, conn: socket.socket) -> bytes | None:
        """메시지 수신 (프레이밍)."""
        # 간단한 줄바꿈 구분 프로토콜
        data = b""
        while True:
            try:
                chunk = conn.recv(4096)
                if not chunk:
                    return None
                data += chunk
                if b"\n" in data:
                    return data.split(b"\n")[0]
                if len(data) > self.MAX_MESSAGE_SIZE:
                    raise ValueError("Message too large")
            except TimeoutError:
                if data:
                    return data
                raise

    def _send_message(self, conn: socket.socket, response: JSONRPCResponse) -> None:
        """응답 전송."""
        data = response.to_json().encode("utf-8") + b"\n"
        conn.sendall(data)

    # =========================================================================
    # Request Processing
    # =========================================================================

    def _process_request(self, data: bytes) -> JSONRPCResponse:
        """요청 처리 및 라우팅."""
        self._stats.total_requests += 1
        start_time = time.time()

        try:
            # 요청 파싱
            request = parse_request(data)

            # 인증 검증
            auth_result = self._authenticator.validate_request(request.auth)
            if not auth_result.success:
                self._stats.auth_failures += 1
                return create_error_response(
                    request.id,
                    JSONRPCErrorCode.AUTHENTICATION_ERROR,
                    auth_result.error or "Authentication failed",
                )

            # 핸들러 조회
            handler = self._handlers.get(request.method)
            if handler is None:
                self._stats.failed_requests += 1
                return create_error_response(
                    request.id,
                    JSONRPCErrorCode.METHOD_NOT_FOUND,
                    f"Method not found: {request.method}",
                    {"method": request.method},
                )

            # 핸들러 실행
            result = handler(request.params)
            self._stats.successful_requests += 1

            return create_success_response(request.id, result)

        except JSONRPCParseError as e:
            self._stats.failed_requests += 1
            return create_error_response(
                None,
                JSONRPCErrorCode.PARSE_ERROR,
                str(e),
            )
        except Exception as e:
            self._stats.failed_requests += 1
            logger.exception(
                "uds_server.request_error",
                error=e,
            )
            return create_error_response(
                None,
                JSONRPCErrorCode.INTERNAL_ERROR,
                str(e),
            )

    # =========================================================================
    # Circuit Breaker Handlers
    # =========================================================================

    def _handle_cb_should_allow(self, params: dict) -> dict:
        """circuit_breaker.should_allow 핸들러."""
        service_name = params.get("service_name")
        if not service_name:
            raise ValueError("service_name required")

        # 캐시 확인
        cached, hit = self._cb_cache.get(service_name)
        if hit:
            return cached

        # 서비스 호출
        if self.cb_service is None:
            return {"allowed": True, "state": "closed", "cached": False}

        result = {
            "allowed": self.cb_service.should_allow(service_name),
            "state": self.cb_service.get_state(service_name),
        }

        # 캐시 저장
        self._cb_cache.set(service_name, result)
        return result

    def _handle_cb_should_allow_batch(self, params: dict) -> dict:
        """circuit_breaker.should_allow_batch 핸들러."""
        service_names = params.get("service_names", [])
        if not service_names:
            return {}

        # 최대 100개 제한
        service_names = service_names[:100]
        results = {}

        for name in service_names:
            results[name] = self._handle_cb_should_allow({"service_name": name})

        return results

    def _handle_cb_get_state(self, params: dict) -> dict:
        """circuit_breaker.get_state 핸들러."""
        service_name = params.get("service_name")
        if not service_name:
            raise ValueError("service_name required")

        if self.cb_service is None:
            return {"service_name": service_name, "state": "closed"}

        state_data = self.cb_service.get_or_create_state(service_name)
        return {
            "service_name": service_name,
            "state": state_data.state,
            "failure_count": state_data.failure_count,
            "success_count": state_data.success_count,
            "opened_at": (state_data.opened_at.isoformat() if state_data.opened_at else None),
            "controlled_by": state_data.controlled_by,
            "reason": state_data.reason,
        }

    def _handle_cb_get_all_states(self, params: dict) -> dict:
        """circuit_breaker.get_all_states 핸들러."""
        if self.cb_service is None:
            return {"states": []}

        states = self.cb_service.get_all_states()
        return {"states": states}

    def _handle_cb_force_open(self, params: dict) -> dict:
        """circuit_breaker.force_open 핸들러."""
        service_name = params.get("service_name")
        reason = params.get("reason", "IPC request")
        controlled_by = params.get("controlled_by", "sidecar")

        if not service_name:
            raise ValueError("service_name required")

        if self.cb_service is None:
            return {"success": False, "error": "Service unavailable"}

        result = self.cb_service.force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
        )

        # 캐시 무효화
        self._cb_cache.invalidate(service_name)

        return {
            "success": result.success,
            "service_name": service_name,
            "previous_state": result.previous_state,
            "new_state": result.new_state,
            "message": result.message,
        }

    def _handle_cb_force_close(self, params: dict) -> dict:
        """circuit_breaker.force_close 핸들러."""
        service_name = params.get("service_name")
        reason = params.get("reason", "IPC request")
        controlled_by = params.get("controlled_by", "sidecar")
        trigger_replay = params.get("trigger_replay", False)

        if not service_name:
            raise ValueError("service_name required")

        if self.cb_service is None:
            return {"success": False, "error": "Service unavailable"}

        result = self.cb_service.force_close(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
            trigger_replay=trigger_replay,
        )

        # 캐시 무효화
        self._cb_cache.invalidate(service_name)

        return {
            "success": result.success,
            "service_name": service_name,
            "previous_state": result.previous_state,
            "new_state": result.new_state,
            "message": result.message,
        }

    # =========================================================================
    # DLQ Handlers
    # =========================================================================

    def _handle_dlq_store(self, params: dict) -> dict:
        """dlq.store 핸들러."""
        if self.dlq_service is None:
            return {"success": False, "error": "Service unavailable"}

        domain = params.get("domain")
        failure_type = params.get("failure_type")
        error_message = params.get("error_message", "")

        if not domain or not failure_type:
            raise ValueError("domain and failure_type required")

        # JSON 바이트 디코딩
        snapshot_data = params.get("snapshot_data", {})
        if isinstance(snapshot_data, bytes):
            snapshot_data = json.loads(snapshot_data.decode("utf-8"))

        request_data = params.get("request_data")
        if isinstance(request_data, bytes):
            request_data = json.loads(request_data.decode("utf-8"))

        result = self.dlq_service.store_with_snapshot(
            domain=domain,
            failure_type=failure_type,
            error_code=params.get("error_code", ""),
            error_message=error_message,
            snapshot_data=snapshot_data,
            request_data=request_data,
            entity_type=params.get("entity_type"),
            entity_id=params.get("entity_id"),
            user_id=params.get("user_id"),
            max_retries=params.get("max_retries"),
        )

        return {
            "success": result.success,
            "entry_id": result.entry_id,
            "message": result.message,
        }

    def _handle_dlq_replay(self, params: dict) -> dict:
        """dlq.replay 핸들러."""
        if self.dlq_service is None:
            return {"success": False, "error": "Service unavailable"}

        entry_id = params.get("entry_id")
        if not entry_id:
            raise ValueError("entry_id required")

        result = self.dlq_service.replay(
            entry_id=entry_id,
            actor_id=params.get("actor_id", "sidecar"),
            force=params.get("force", False),
        )

        return {
            "success": result.success,
            "entry_id": entry_id,
            "status": result.status,
            "message": result.message,
        }

    def _handle_dlq_get_entry(self, params: dict) -> dict:
        """dlq.get_entry 핸들러."""
        if self.dlq_service is None:
            return {"error": "Service unavailable"}

        entry_id = params.get("entry_id")
        if not entry_id:
            raise ValueError("entry_id required")

        entry = self.dlq_service.get_entry(entry_id)
        if entry is None:
            return {"error": "Entry not found"}

        return entry.to_dict() if hasattr(entry, "to_dict") else {"id": entry_id}

    def _handle_dlq_list(self, params: dict) -> dict:
        """dlq.list 핸들러."""
        if self.dlq_service is None:
            return {"entries": [], "total_count": 0}

        entries = self.dlq_service.list_entries(
            domain=params.get("domain"),
            status=params.get("status"),
            limit=params.get("limit", 100),
            offset=params.get("offset", 0),
        )

        return {
            "entries": [e.to_dict() if hasattr(e, "to_dict") else {"id": e.id} for e in entries],
            "total_count": len(entries),
        }

    # =========================================================================
    # Buffer Handlers
    # =========================================================================

    def _handle_buffer_store(self, params: dict) -> dict:
        """buffer.store 핸들러."""
        try:
            from selfhealing.audit.persistence import get_disk_buffer

            buffer = get_disk_buffer()

            entry_type = params.get("entry_type", "general")
            data = params.get("data", {})
            if isinstance(data, bytes):
                data = json.loads(data.decode("utf-8"))

            data["_entry_type"] = entry_type
            data["_dedup_key"] = params.get("dedup_key")

            success = buffer.put(data)
            return {
                "success": success,
                "sequence": buffer.stats.get("sequence", 0),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_buffer_flush(self, params: dict) -> dict:
        """buffer.flush 핸들러."""
        try:
            from selfhealing.audit.persistence import get_disk_buffer

            buffer = get_disk_buffer()

            def flush_callback(entries: list) -> bool:
                # 실제 플러시 로직 (Kafka 등으로 전송)
                return True

            flushed = buffer.flush_to(flush_callback)
            return {"flushed_count": flushed, "failed_count": 0}
        except Exception as e:
            return {"flushed_count": 0, "failed_count": 0, "error": str(e)}

    def _handle_buffer_get_stats(self, params: dict) -> dict:
        """buffer.get_stats 핸들러."""
        try:
            from selfhealing.audit.persistence import get_disk_buffer

            buffer = get_disk_buffer()
            stats = buffer.stats
            return {
                "total_entries": stats.get("entry_count", 0),
                "pending_flush": stats.get("pending", 0),
                "state": str(buffer.state.value) if hasattr(buffer, "state") else "active",
                "bytes_used": stats.get("bytes_used", 0),
                "bytes_limit": stats.get("bytes_limit", 0),
            }
        except Exception as e:
            return {"state": "unavailable", "error": str(e)}

    # =========================================================================
    # Learning Handlers
    # =========================================================================

    def _handle_learning_get_suggestions(self, params: dict) -> dict:
        """learning.get_suggestions 핸들러."""
        if self.learning_service is None:
            return {"suggestions": []}

        limit = params.get("limit", 10)
        suggestions = self.learning_service.get_suggestions(limit=limit)

        return {
            "suggestions": [
                {
                    "suggestion_type": s.suggestion_type,
                    "description": s.description,
                    "priority": s.priority.value if hasattr(s.priority, "value") else s.priority,
                }
                for s in suggestions
            ]
        }

    # =========================================================================
    # Health Handlers
    # =========================================================================

    def _handle_health_check(self, params: dict) -> dict:
        """health.check 핸들러."""
        return {"status": "SERVING"}

    def _handle_health_get_details(self, params: dict) -> dict:
        """health.get_details 핸들러."""
        return {
            "overall_status": "healthy",
            "components": {
                "uds_server": {
                    "status": "healthy",
                    "details": {
                        "active_connections": str(self._stats.active_connections),
                        "total_requests": str(self._stats.total_requests),
                    },
                },
                "cb_service": {
                    "status": "healthy" if self.cb_service else "unavailable",
                },
                "dlq_service": {
                    "status": "healthy" if self.dlq_service else "unavailable",
                },
            },
            "version": "2.0.0",
        }

    # =========================================================================
    # Stats
    # =========================================================================

    @property
    def stats(self) -> ServerStats:
        """서버 통계."""
        return self._stats

    def get_stats_dict(self) -> dict[str, Any]:
        """통계 딕셔너리."""
        return {
            "socket_path": self._socket_path,
            "running": self._running,
            "total_requests": self._stats.total_requests,
            "successful_requests": self._stats.successful_requests,
            "failed_requests": self._stats.failed_requests,
            "auth_failures": self._stats.auth_failures,
            "total_connections": self._stats.total_connections,
            "active_connections": self._stats.active_connections,
            "cache_stats": self._cb_cache.get_stats_dict(),
        }

    @property
    def is_running(self) -> bool:
        """서버 실행 중 여부."""
        return self._running


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_server: UDSServer | None = None


def get_uds_server() -> UDSServer:
    """싱글톤 서버 인스턴스 반환."""
    global _server
    if _server is None:
        _server = UDSServer()
    return _server


def reset_uds_server() -> None:
    """서버 인스턴스 리셋 (테스트용)."""
    global _server
    if _server is not None:
        _server.stop()
    _server = None
