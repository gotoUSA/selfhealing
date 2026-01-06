"""
Layered Repository Base Class.

Provides the base class with initialization and configuration.

Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from selfhealing.adapters.memory.base import _now
from selfhealing.adapters.memory.drift_reconciliation import (
    DriftReconciler,
    get_drift_reconciler,
)
from selfhealing.adapters.memory.shadow_logger import get_shadow_logger, ShadowLogger
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateRepository,
    CircuitBreakerStateData,
)

if TYPE_CHECKING:
    from selfhealing.adapters.memory.circuit_breaker import InMemoryCircuitBreakerStateRepository

logger = logging.getLogger(__name__)


class LayeredRepositoryBase:
    """
    Base class for Layered Repository.

    Provides initialization, configuration, and executor management.
    """

    # ThreadPoolExecutor for async L2 operations with timeout
    _executor: Optional[ThreadPoolExecutor] = None
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
        l2_repo: Optional[CircuitBreakerStateRepository] = None,
        sync_interval_seconds: float = 5.0,
        adapter_type: str = "unknown",
        drift_reconciler: Optional[DriftReconciler] = None,
    ):
        """
        Args:
            l2_repo: L2 저장소 (Redis, Django DB 등). None이면 L1만 사용.
            sync_interval_seconds: L2 동기화 주기 (초)
            adapter_type: L2 어댑터 타입 (redis, django 등) - 타임아웃 결정에 사용
            drift_reconciler: 드리프트 복구 인스턴스. None이면 기본 인스턴스 사용.
        """
        # Lazy import to avoid circular dependency
        from selfhealing.adapters.memory.circuit_breaker import InMemoryCircuitBreakerStateRepository

        self._l1 = InMemoryCircuitBreakerStateRepository()
        self._l2 = l2_repo
        self._sync_interval = sync_interval_seconds
        self._adapter_type = adapter_type
        self._last_sync_time: Optional[datetime] = None
        self._lock = threading.RLock()
        self._shadow_logger = get_shadow_logger()
        self._drift_reconciler = drift_reconciler or get_drift_reconciler()

        # L2 연결 상태 추적
        self._l2_healthy = True
        self._l2_last_error_time: Optional[datetime] = None
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
        }

        # L2가 있으면 초기 로드
        if self._l2:
            self._load_from_l2_with_timeout()

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
