"""
Layered Repository Base Class.

Provides the base class with initialization and configuration.
L2 저장소 동기화 시 Bulkhead 패턴을 사용하여 리소스 격리를 제공합니다.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING

from selfhealing.adapters.memory.drift_reconciliation import (
    DriftReconciler,
    get_drift_reconciler,
)
from selfhealing.adapters.memory.shadow_logger import get_shadow_logger
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateRepository,
)

if TYPE_CHECKING:
    from selfhealing.resilience.bulkhead.base import Bulkhead

logger = logging.getLogger(__name__)


class LayeredRepositoryBase:
    """
    Base class for Layered Repository.

    Provides initialization, configuration, and executor management.
    L2 저장소 작업 시 Bulkhead 패턴으로 리소스 격리를 제공합니다.
    """

    # ThreadPoolExecutor for async L2 operations with timeout
    _executor: ThreadPoolExecutor | None = None
    _executor_lock = threading.Lock()

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        """Get or create shared ThreadPoolExecutor."""
        if cls._executor is None:
            with cls._executor_lock:
                if cls._executor is None:
                    cls._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="l2_sync")
        return cls._executor

    def __init__(
        self,
        l2_repo: CircuitBreakerStateRepository | None = None,
        sync_interval_seconds: float = 5.0,
        adapter_type: str = "unknown",
        drift_reconciler: DriftReconciler | None = None,
        use_bulkhead: bool = True,
    ):
        """
        Args:
            l2_repo: L2 저장소 (Redis, Django DB 등). None이면 L1만 사용.
            sync_interval_seconds: L2 동기화 주기 (초)
            adapter_type: L2 어댑터 타입 (redis, django 등) - 타임아웃 결정에 사용
            drift_reconciler: 드리프트 복구 인스턴스. None이면 기본 인스턴스 사용.
            use_bulkhead: Bulkhead 패턴 사용 여부 (기본 True)
        """
        # Lazy import to avoid circular dependency
        from selfhealing.adapters.memory.circuit_breaker import (
            InMemoryCircuitBreakerStateRepository,
        )

        self._l1 = InMemoryCircuitBreakerStateRepository()
        self._l2 = l2_repo
        self._sync_interval = sync_interval_seconds
        self._adapter_type = adapter_type
        self._last_sync_time: datetime | None = None
        self._lock = threading.RLock()
        self._shadow_logger = get_shadow_logger()
        self._drift_reconciler = drift_reconciler or get_drift_reconciler()

        # Bulkhead 설정
        self._use_bulkhead = use_bulkhead
        self._bulkhead: Bulkhead | None = None
        if use_bulkhead:
            self._init_bulkhead()

        # L2 연결 상태 추적
        self._l2_healthy = True
        self._l2_last_error_time: datetime | None = None
        self._l2_consecutive_failures = 0
        self._l2_was_unhealthy = False  # L2 복구 감지용

        # 메트릭 카운터 (Prometheus 연동 전 로컬 추적용)
        self._metrics = {
            "l2_timeout_count": 0,
            "l2_sync_failure_count": 0,
            "l2_sync_success_count": 0,
            "l2_latency_total_ms": 0.0,
            "l2_latency_count": 0,
            "drift_reconciliation_count": 0,
            "bulkhead_rejected_count": 0,
        }

        # L2가 있으면 초기 로드
        if self._l2:
            self._load_from_l2_with_timeout()

    def _init_bulkhead(self) -> None:
        """어댑터 타입에 맞는 Bulkhead 초기화."""
        try:
            from selfhealing.core.connection_health import ConnectionType
            from selfhealing.resilience.bulkhead import get_bulkhead_registry

            registry = get_bulkhead_registry()

            # 어댑터 타입에 따라 적절한 격벽 선택
            bulkhead_mapping = {
                "redis": ConnectionType.CACHE,
                "memcached": ConnectionType.CACHE,
                "database": ConnectionType.DATABASE,
                "django": ConnectionType.DATABASE,
            }

            conn_type = bulkhead_mapping.get(
                self._adapter_type.lower(),
                ConnectionType.CACHE,  # 기본값
            )
            self._bulkhead = registry.get(conn_type)
            logger.debug(
                f"[LayeredRepositoryBase] Bulkhead initialized: "
                f"adapter={self._adapter_type}, bulkhead={self._bulkhead.name}"
            )
        except Exception as e:
            logger.warning(f"[LayeredRepositoryBase] Bulkhead init failed, " f"continuing without bulkhead: {e}")
            self._bulkhead = None
            self._use_bulkhead = False

    def _execute_with_bulkhead(self, operation_name: str, func, *args, **kwargs):
        """
        Bulkhead로 보호된 작업 실행.

        Bulkhead 획득 실패 시 작업을 건너뛰고 None 반환.

        Args:
            operation_name: 작업 이름 (로깅용)
            func: 실행할 함수
            *args: 함수 위치 인자
            **kwargs: 함수 키워드 인자

        Returns:
            함수 실행 결과 또는 None (Bulkhead 거부 시)
        """
        if not self._use_bulkhead or self._bulkhead is None:
            return func(*args, **kwargs)

        try:
            from selfhealing.resilience.bulkhead.exceptions import BulkheadFullError

            with self._bulkhead.acquire(timeout=self._get_timeout_seconds()):
                return func(*args, **kwargs)
        except BulkheadFullError:
            self._metrics["bulkhead_rejected_count"] += 1
            logger.warning(f"[LayeredRepositoryBase] Bulkhead rejected {operation_name}, " f"bulkhead={self._bulkhead.name}")
            return None
        except Exception as e:
            logger.warning(f"[LayeredRepositoryBase] Bulkhead error in {operation_name}: {e}")
            return func(*args, **kwargs)

    def _get_timeout_seconds(self) -> float:
        """어댑터 타입에 따른 타임아웃 반환 (초 단위)."""
        try:
            from selfhealing.config import get_l2_storage_runtime_config

            config = get_l2_storage_runtime_config()
            return config.get_timeout_for_adapter(self._adapter_type)
        except ImportError:
            # Config not available, use defaults
            timeouts = {
                "redis": 0.05,  # 50ms
                "database": 0.2,  # 200ms
                "django": 0.2,  # 200ms
            }
            return timeouts.get(self._adapter_type.lower(), 0.1)
