"""
gRPC 기반 사이드카 서버.

타언어 애플리케이션(Go, Java 등)이 gRPC를 통해 Self-Healing API를
사용할 수 있도록 서버를 제공합니다.

특징:
- Protobuf 기반 효율적 직렬화
- HTTP/2 양방향 스트리밍
- 배치 요청 지원
- 이벤트 스트리밍 (Server-Side Streaming)

Usage:
    from selfhealing.adapters.ipc.grpc_server import SidecarGRPCServer

    server = SidecarGRPCServer(port=50051)
    server.start()

    # 종료
    server.stop()

Note:
    gRPC 서버 기능은 grpcio가 설치된 경우에만 사용 가능합니다.
    Proto 파일은 protocol/selfhealing.proto를 참조하세요.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent import futures
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

from selfhealing.adapters.ipc.auth import (
    SidecarAuthenticator,
    get_sidecar_authenticator,
)
from selfhealing.adapters.ipc.cb_state_cache import CBStateCache, get_cb_state_cache
from selfhealing.adapters.ipc.event_stream_proxy import (
    EventStreamProxy,
    get_event_stream_proxy,
)

logger = logging.getLogger(__name__)

# gRPC 가용성 확인
try:
    import grpc
    from grpc import ServicerContext

    GRPC_AVAILABLE = True
except ImportError:
    GRPC_AVAILABLE = False
    grpc = None  # type: ignore
    ServicerContext = None  # type: ignore


@dataclass
class GRPCServerStats:
    """gRPC 서버 통계."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    auth_failures: int = 0
    active_streams: int = 0


class SidecarGRPCServer:
    """
    gRPC 기반 사이드카 서버.

    Self-Healing 서비스를 gRPC 인터페이스로 노출합니다:
    - CircuitBreakerService
    - DLQService
    - BufferService
    - EventService
    - HealthService
    """

    DEFAULT_PORT = 50051
    MAX_WORKERS = 10

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        host: str = "localhost",
        authenticator: SidecarAuthenticator | None = None,
        cb_cache: CBStateCache | None = None,
        event_proxy: EventStreamProxy | None = None,
        max_workers: int = MAX_WORKERS,
    ):
        """
        gRPC 서버 초기화.

        Args:
            port: 서버 포트
            host: 바인딩 호스트
            authenticator: 인증자
            cb_cache: CB 상태 캐시
            event_proxy: 이벤트 스트림 프록시
            max_workers: 최대 워커 스레드 수
        """
        if not GRPC_AVAILABLE:
            raise RuntimeError("gRPC is not available. Install grpcio: pip install grpcio")

        self._port = port
        self._host = host
        self._authenticator = authenticator or get_sidecar_authenticator()
        self._cb_cache = cb_cache or get_cb_state_cache()
        self._event_proxy = event_proxy or get_event_stream_proxy()
        self._max_workers = max_workers

        self._server: Any = None
        self._running = False
        self._stats = GRPCServerStats()

        # 서비스 인스턴스 (lazy loading)
        self._cb_service: Any = None
        self._dlq_service: Any = None

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
                logger.warning(f"[GRPCServer] CircuitBreakerService not available: {e}")
        return self._cb_service

    @property
    def dlq_service(self) -> Any:
        """DLQService 인스턴스."""
        if self._dlq_service is None:
            try:
                from selfhealing.services import get_dlq_service

                self._dlq_service = get_dlq_service()
            except ImportError as e:
                logger.warning(f"[GRPCServer] DLQService not available: {e}")
        return self._dlq_service

    # =========================================================================
    # Server Lifecycle
    # =========================================================================

    def start(self, blocking: bool = False) -> None:
        """
        서버 시작.

        Args:
            blocking: 블로킹 모드 여부
        """
        if self._running:
            logger.warning("[GRPCServer] Server already running")
            return

        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=self._max_workers))

        # 서비스 등록 (실제 Proto 생성 후 연결)
        self._register_services()

        # 포트 바인딩
        address = f"{self._host}:{self._port}"
        self._server.add_insecure_port(address)

        self._server.start()
        self._running = True
        logger.info(f"[GRPCServer] Started on {address}")

        if blocking:
            self._server.wait_for_termination()

    def stop(self, grace: float = 5.0) -> None:
        """
        서버 종료.

        Args:
            grace: 그레이스풀 종료 대기 시간 (초)
        """
        if self._server:
            self._server.stop(grace)
            self._running = False
            logger.info("[GRPCServer] Stopped")

    def _register_services(self) -> None:
        """gRPC 서비스 등록."""
        # Note: 실제 Proto 생성 후 서비스 구현체를 등록해야 함
        # 예: circuit_breaker_pb2_grpc.add_CircuitBreakerServiceServicer_to_server(
        #         CircuitBreakerServicer(self), self._server)
        logger.debug("[GRPCServer] Services registered (stub)")

    @property
    def is_running(self) -> bool:
        """서버 실행 중 여부."""
        return self._running

    @property
    def stats(self) -> GRPCServerStats:
        """서버 통계."""
        return self._stats

    def get_stats_dict(self) -> dict[str, Any]:
        """통계 딕셔너리."""
        return {
            "host": self._host,
            "port": self._port,
            "running": self._running,
            "total_requests": self._stats.total_requests,
            "successful_requests": self._stats.successful_requests,
            "failed_requests": self._stats.failed_requests,
            "auth_failures": self._stats.auth_failures,
            "active_streams": self._stats.active_streams,
        }

    # =========================================================================
    # Authentication Helper
    # =========================================================================

    def _authenticate(self, context: Any) -> bool:
        """
        gRPC 요청 인증.

        Args:
            context: gRPC ServicerContext

        Returns:
            인증 성공 여부
        """
        if not self._authenticator.is_enabled:
            return True

        metadata = dict(context.invocation_metadata())
        token = metadata.get("authorization", "")

        result = self._authenticator.validate(token)
        if not result.success:
            self._stats.auth_failures += 1
            context.abort(grpc.StatusCode.UNAUTHENTICATED, result.error or "Unauthorized")
            return False

        return True

    # =========================================================================
    # Circuit Breaker Service Implementation
    # =========================================================================

    def handle_should_allow(
        self,
        service_name: str,
        context: Any,
    ) -> dict[str, Any]:
        """ShouldAllow RPC 핸들러."""
        self._stats.total_requests += 1

        if not self._authenticate(context):
            self._stats.failed_requests += 1
            return {"allowed": True, "state": "unknown"}  # Fail-open

        # 캐시 확인
        cached, hit = self._cb_cache.get(service_name)
        if hit:
            self._stats.successful_requests += 1
            return cached

        # 서비스 호출
        if self.cb_service is None:
            return {"allowed": True, "state": "closed"}

        result = {
            "allowed": self.cb_service.should_allow(service_name),
            "state": self.cb_service.get_state(service_name),
        }

        # 캐시 저장
        self._cb_cache.set(service_name, result)
        self._stats.successful_requests += 1

        return result

    def handle_should_allow_batch(
        self,
        service_names: list[str],
        context: Any,
    ) -> dict[str, dict[str, Any]]:
        """ShouldAllowBatch RPC 핸들러."""
        self._stats.total_requests += 1

        if not self._authenticate(context):
            self._stats.failed_requests += 1
            return {name: {"allowed": True, "state": "unknown"} for name in service_names}

        results = {}
        for name in service_names[:100]:  # 최대 100개
            results[name] = self.handle_should_allow(name, context)

        return results

    def handle_force_open(
        self,
        service_name: str,
        reason: str,
        controlled_by: str,
        context: Any,
    ) -> dict[str, Any]:
        """ForceOpen RPC 핸들러."""
        self._stats.total_requests += 1

        if not self._authenticate(context):
            self._stats.failed_requests += 1
            return {"success": False, "error": "Unauthorized"}

        if self.cb_service is None:
            return {"success": False, "error": "Service unavailable"}

        result = self.cb_service.force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
        )

        self._cb_cache.invalidate(service_name)
        self._stats.successful_requests += 1

        return {
            "success": result.success,
            "service_name": service_name,
            "previous_state": result.previous_state,
            "new_state": result.new_state,
            "message": result.message,
        }

    def handle_force_close(
        self,
        service_name: str,
        reason: str,
        controlled_by: str,
        trigger_replay: bool,
        context: Any,
    ) -> dict[str, Any]:
        """ForceClose RPC 핸들러."""
        self._stats.total_requests += 1

        if not self._authenticate(context):
            self._stats.failed_requests += 1
            return {"success": False, "error": "Unauthorized"}

        if self.cb_service is None:
            return {"success": False, "error": "Service unavailable"}

        result = self.cb_service.force_close(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
            trigger_replay=trigger_replay,
        )

        self._cb_cache.invalidate(service_name)
        self._stats.successful_requests += 1

        return {
            "success": result.success,
            "service_name": service_name,
            "previous_state": result.previous_state,
            "new_state": result.new_state,
            "message": result.message,
        }

    # =========================================================================
    # Event Service Implementation
    # =========================================================================

    def handle_subscribe_events(
        self,
        event_types: list[str],
        client_id: str,
        context: Any,
    ) -> Iterator[dict[str, Any]]:
        """SubscribeEvents RPC 핸들러 (Server-Side Streaming)."""
        if not self._authenticate(context):
            return

        self._stats.active_streams += 1

        try:
            # 스트림 구독
            stream_id = f"grpc-{client_id}-{time.time()}"
            queue = self._event_proxy.subscribe(
                event_types=event_types if event_types else None,
                stream_id=stream_id,
                client_id=client_id,
            )

            # 이벤트 스트리밍
            for event in self._event_proxy.iter_events(stream_id):
                if context.is_active():
                    yield event
                else:
                    break

        finally:
            self._stats.active_streams -= 1
            self._event_proxy.unsubscribe(stream_id)

    # =========================================================================
    # Health Service Implementation
    # =========================================================================

    def handle_health_check(self, context: Any) -> dict[str, Any]:
        """Health.Check RPC 핸들러."""
        return {"status": "SERVING"}

    def handle_health_details(self, context: Any) -> dict[str, Any]:
        """Health.GetDetails RPC 핸들러."""
        return {
            "overall_status": "healthy",
            "components": {
                "grpc_server": {
                    "status": "healthy",
                    "details": self.get_stats_dict(),
                },
                "cb_service": {
                    "status": "healthy" if self.cb_service else "unavailable",
                },
                "event_proxy": {
                    "status": "healthy",
                    "details": self._event_proxy.get_stats_dict(),
                },
            },
            "version": "2.0.0",
        }


# =============================================================================
# gRPC Servicer 구현 (Proto 생성 후 사용)
# =============================================================================

if GRPC_AVAILABLE:

    class CircuitBreakerServicer:
        """
        CircuitBreakerService gRPC Servicer.

        Note: 실제 사용을 위해서는 protoc으로 생성된
        코드와 함께 상속해야 합니다.
        """

        def __init__(self, server: SidecarGRPCServer):
            self._server = server

        def ShouldAllow(self, request: Any, context: Any) -> Any:
            """ShouldAllow RPC 구현."""
            result = self._server.handle_should_allow(
                request.service_name,
                context,
            )
            # Proto 메시지로 변환 필요
            return result

        def ShouldAllowBatch(self, request: Any, context: Any) -> Any:
            """ShouldAllowBatch RPC 구현."""
            result = self._server.handle_should_allow_batch(
                list(request.service_names),
                context,
            )
            return result

        def ForceOpen(self, request: Any, context: Any) -> Any:
            """ForceOpen RPC 구현."""
            result = self._server.handle_force_open(
                request.service_name,
                request.reason,
                request.controlled_by,
                context,
            )
            return result

        def ForceClose(self, request: Any, context: Any) -> Any:
            """ForceClose RPC 구현."""
            result = self._server.handle_force_close(
                request.service_name,
                request.reason,
                request.controlled_by,
                request.trigger_replay,
                context,
            )
            return result

    class EventServicer:
        """
        EventService gRPC Servicer.

        Server-Side Streaming으로 이벤트를 전달합니다.
        """

        def __init__(self, server: SidecarGRPCServer):
            self._server = server

        def SubscribeEvents(self, request: Any, context: Any) -> Iterator[Any]:
            """SubscribeEvents RPC 구현 (Server-Side Streaming)."""
            yield from self._server.handle_subscribe_events(
                list(request.event_types),
                request.client_id,
                context,
            )

    class HealthServicer:
        """
        HealthService gRPC Servicer.

        gRPC 표준 Health Check 프로토콜과 호환됩니다.
        """

        def __init__(self, server: SidecarGRPCServer):
            self._server = server

        def Check(self, request: Any, context: Any) -> Any:
            """Health Check RPC 구현."""
            return self._server.handle_health_check(context)

        def GetDetails(self, request: Any, context: Any) -> Any:
            """Health Details RPC 구현."""
            return self._server.handle_health_details(context)


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_server: SidecarGRPCServer | None = None


def get_grpc_server() -> SidecarGRPCServer:
    """싱글톤 서버 인스턴스 반환."""
    global _server
    if _server is None:
        _server = SidecarGRPCServer()
    return _server


def reset_grpc_server() -> None:
    """서버 인스턴스 리셋 (테스트용)."""
    global _server
    if _server is not None:
        _server.stop()
    _server = None
