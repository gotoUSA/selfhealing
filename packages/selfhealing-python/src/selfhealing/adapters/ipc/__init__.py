"""
IPC 어댑터 - 사이드카 통신 레이어.

타언어 애플리케이션(Go, Java 등)이 Self-Healing 시스템을 사용할 수 있도록
Unix Domain Socket 및 gRPC 기반 통신 레이어를 제공합니다.

Components:
    - UDSServer: Unix Domain Socket 기반 JSON-RPC 서버
    - UDSClient: 테스트용 UDS 클라이언트
    - SidecarGRPCServer: gRPC 기반 사이드카 서버
    - SidecarAuthenticator: Static Bearer Token 인증
    - CBStateCache: 서킷 브레이커 상태 로컬 캐시
    - CBStateSnapshot: Shared Memory 기반 CB 상태 공유
    - EventStreamProxy: EventBus → gRPC 스트림 중계
    - SidecarIPCProbe: 메타워치독 헬스 프로브
    - sidecar_metrics: Prometheus 메트릭

Usage:
    from selfhealing.adapters.ipc import (
        UDSServer,
        UDSClient,
        SidecarGRPCServer,
        SidecarAuthenticator,
        IPCStateCache,
        EventStreamProxy,
        CBStateSnapshot,
        get_cb_state_snapshot,
    )

    # UDS 서버 시작
    server = UDSServer()
    server.start()

    # gRPC 서버 시작
    grpc_server = SidecarGRPCServer()
    grpc_server.serve()

    # Shared Memory CB 상태 조회 (~10μs)
    snapshot = get_cb_state_snapshot()
    state = snapshot.get_state("payment_service")
"""

from selfhealing.adapters.ipc.auth import SidecarAuthenticator
from selfhealing.adapters.ipc.cb_state_cache import (
    IPCStateCache,
    CBStateCache,
    get_cb_state_cache,
    reset_cb_state_cache,
)
from selfhealing.adapters.ipc.cb_state_snapshot import (
    CBStateSnapshot,
    get_cb_state_snapshot,
    reset_cb_state_snapshot,
)
from selfhealing.adapters.ipc.event_stream_proxy import (
    EventStreamProxy,
    get_event_stream_proxy,
    reset_event_stream_proxy,
)
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
from selfhealing.adapters.ipc.grpc_server import (
    SidecarGRPCServer,
    get_grpc_server,
    reset_grpc_server,
)
from selfhealing.adapters.ipc.request_handler import (
    RequestHandler,
    get_request_handler,
    reset_request_handler,
)
from selfhealing.adapters.ipc.sidecar_ipc_probe import (
    SidecarIPCProbe,
    get_sidecar_ipc_probe,
    reset_sidecar_ipc_probe,
)
from selfhealing.adapters.ipc.sidecar_metrics import (
    record_ipc_request,
    sidecar_metrics,
)
from selfhealing.adapters.ipc.uds_client import (
    FailOpenUDSClient,
    UDSClient,
)
from selfhealing.adapters.ipc.uds_server import UDSServer, get_uds_server, reset_uds_server

__all__ = [
    # Server/Client
    "UDSServer",
    "UDSClient",
    "FailOpenUDSClient",
    "SidecarGRPCServer",
    # Auth
    "SidecarAuthenticator",
    # Cache
    "IPCStateCache",
    "CBStateCache",  # deprecated alias
    "get_cb_state_cache",
    "reset_cb_state_cache",
    # Event Proxy
    "EventStreamProxy",
    "get_event_stream_proxy",
    "reset_event_stream_proxy",
    # CB State Snapshot
    "CBStateSnapshot",
    "get_cb_state_snapshot",
    "reset_cb_state_snapshot",
    # Request Handler
    "RequestHandler",
    "get_request_handler",
    "reset_request_handler",
    # Exceptions
    "IPCError",
    "IPCConnectionError",
    "IPCTimeoutError",
    "IPCAuthenticationError",
    "IPCAuthorizationError",
    "IPCMethodNotFoundError",
    "IPCInvalidParamsError",
    "IPCParseError",
    "IPCInternalError",
    "IPCRateLimitedError",
    "IPCCircuitBreakerOpenError",
    "IPCServiceUnavailableError",
    # Metrics
    "sidecar_metrics",
    "record_ipc_request",
    # Health Probe
    "SidecarIPCProbe",
    "get_sidecar_ipc_probe",
    "reset_sidecar_ipc_probe",
    # Singleton getters
    "get_uds_server",
    "reset_uds_server",
    "get_grpc_server",
    "reset_grpc_server",
]
