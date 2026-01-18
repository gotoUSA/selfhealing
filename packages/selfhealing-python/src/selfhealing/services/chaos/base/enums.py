"""
Chaos Experiment Enums.

Contains status, type, and traffic type enumerations.
"""

from __future__ import annotations

from enum import Enum


class ExperimentStatus(str, Enum):
    """Experiment lifecycle status."""

    PENDING = "pending"
    """Experiment is scheduled but not yet started."""

    AWAITING_APPROVAL = "awaiting_approval"
    """High-risk experiment awaiting manual approval."""

    RUNNING = "running"
    """Experiment is currently active."""

    COMPLETED = "completed"
    """Experiment finished successfully."""

    FAILED = "failed"
    """Experiment encountered an error."""

    ABORTED = "aborted"
    """Experiment was manually stopped via Kill Switch."""

    SKIPPED = "skipped"
    """Experiment was skipped (e.g., low error budget)."""

    ROLLED_BACK = "rolled_back"
    """Experiment was rolled back due to issues."""

    # 비동기 복구 모니터링: 실험 완료 후 시스템이 정상으로 복구되는지 추적
    RECOVERY_MONITORING = "recovery_monitoring"
    """실험 완료 후 Canary 복구 모니터링 중."""


class ExperimentType(str, Enum):
    """Core experiment types."""

    # 기본 장애 주입 유형
    LATENCY_INJECTION = "latency_injection"
    ERROR_5XX = "error_5xx"
    PACKET_LOSS = "packet_loss"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTION = "resource_exhaustion"

    # 확장 장애 유형: 다양한 네트워크/서비스 장애 시뮬레이션
    ERROR_4XX = "error_4xx"
    CONNECTION_RESET = "connection_reset"
    RATE_LIMIT = "rate_limit"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    PARTIAL_FAILURE = "partial_failure"
    CASCADING_FAILURE = "cascading_failure"

    # 커넥션 풀/네트워크 시뮬레이션 실험
    POOL_EXHAUSTION = "pool_exhaustion"
    """Connection Pool 고갈 시뮬레이션 실험."""

    CONNECTION_PARTITION = "connection_partition"
    """네트워크 파티션 시뮬레이션 실험."""

    # 업계 표준 실험: Netflix ChAP, Gremlin, AWS FIS 패턴 기반
    CERTIFICATE_EXPIRY = "certificate_expiry"
    """인증서 만료 시뮬레이션 실험."""

    DNS_FAILURE = "dns_failure"
    """DNS 장애 시뮬레이션 실험."""

    CLOCK_SKEW = "clock_skew"
    """시스템 시간 불일치 시뮬레이션 실험."""

    # 추가 인프라 장애 시뮬레이션
    NETWORK_BLACKHOLE = "network_blackhole"
    """네트워크 블랙홀 시뮬레이션 실험."""

    SIMULATED_DISK_IO = "simulated_disk_io"
    """디스크 I/O 지연/실패 시뮬레이션 실험."""

    SIMULATED_TLS_FAILURE = "simulated_tls_failure"
    """TLS 핸드셰이크 실패 시뮬레이션 실험."""

    # Self-Healing 시스템 고유 실험
    AUDIT_STORAGE_FAILURE = "audit_storage_failure"
    """Audit 저장소 계층 장애 시뮬레이션 실험."""

    REPLAY_FLOOD = "replay_flood"
    """DLQ Replay 폭풍 시뮬레이션 실험."""


class TrafficType(str, Enum):
    """Traffic type for experiment targeting."""

    SYNTHETIC = "synthetic"
    """Synthetic/test traffic only."""

    SHADOW = "shadow"
    """Shadow/mirrored production traffic."""

    CANARY = "canary"
    """Small percentage of real traffic."""

    PRODUCTION = "production"
    """Full production traffic."""
