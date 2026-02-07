"""
Layered Circuit Breaker State Repository Package.

하이브리드 레이어드 저장소 (L1 Memory + L2 Shared Storage).

설계 원칙:
- L1 (Local Memory): 모든 판정은 1차적으로 메모리에서 즉시 수행 (0.01ms)
- L2 (Shared Storage): Redis나 DB는 백그라운드에서 비동기적으로 동기화
- 타임아웃 적용: L2 응답이 늦으면 즉시 포기하고 L1만으로 동작 (Fail-Fast)
- Shadow Logging: L2 장애 시 발생한 변경사항을 로컬에 기록
"""

from __future__ import annotations

from selfhealing.adapters.memory.drift_reconciliation import DriftReconciler
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateRepository,
)

from .audit_helpers import AuditHelpersMixin

# Import base and mixins
from .base import LayeredRepositoryBase
from .drift_operations import DriftOperationsMixin
from .error_handling import ErrorHandlingMixin
from .l2_load import L2LoadMixin
from .l2_sync import L2SyncMixin
from .monitoring import MonitoringMixin
from .repository_operations import RepositoryOperationsMixin


class LayeredCircuitBreakerStateRepository(
    L2LoadMixin,
    ErrorHandlingMixin,
    DriftOperationsMixin,
    L2SyncMixin,
    RepositoryOperationsMixin,
    MonitoringMixin,
    AuditHelpersMixin,
    LayeredRepositoryBase,
    CircuitBreakerStateRepository,
):
    """
    하이브리드 레이어드 저장소 (L1 Memory + L2 Shared Storage).

    장점:
    - 외부 의존성(Redis/DB)이 잠시 죽어도 시스템은 L1만으로 계속 동작
    - 분산 환경에서도 최종적으로 일관성 유지 (Eventual Consistency)
    - 호스트 DB에 침투하지 않음 (L2는 opt-in)

    Usage:
        # 메모리만 사용 (기본, 단일 서버)
        repo = LayeredCircuitBreakerStateRepository()

        # L2로 Redis 추가 (분산 환경)
        from selfhealing.adapters.redis import RedisCircuitBreakerStateRepository
        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=RedisCircuitBreakerStateRepository(),
            sync_interval_seconds=5,
        )
    """

    pass


__all__ = [
    "LayeredCircuitBreakerStateRepository",
    # Base and mixins for extension
    "LayeredRepositoryBase",
    "L2LoadMixin",
    "ErrorHandlingMixin",
    "DriftOperationsMixin",
    "L2SyncMixin",
    "RepositoryOperationsMixin",
    "MonitoringMixin",
    "AuditHelpersMixin",
]
