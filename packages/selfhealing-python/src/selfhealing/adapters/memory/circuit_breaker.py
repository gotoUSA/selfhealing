"""
In-Memory Circuit Breaker State Repository Implementation.

Thread-safe in-memory storage for circuit breaker states.
Includes L1+L2 Layered Storage with Drift Reconciliation support.

Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from selfhealing.adapters.memory.base import _now
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateRepository,
    CircuitBreakerStateData,
    CircuitBreakerStateEnum,
)


logger = logging.getLogger(__name__)


class InMemoryCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    In-memory implementation of CircuitBreakerStateRepository.

    Thread-safe storage for circuit breaker states in memory.
    """

    def __init__(self):
        self._storage: Dict[str, CircuitBreakerStateData] = {}
        self._next_id = 1
        self._lock = threading.RLock()  # RLock for reentrant calls

    def get_by_service_name(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """Get circuit breaker state by service name."""
        with self._lock:
            return self._storage.get(service_name)

    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """Get or create a circuit breaker state."""
        with self._lock:
            if service_name not in self._storage:
                state = CircuitBreakerStateData(
                    id=self._next_id,
                    service_name=service_name,
                    state=CircuitBreakerStateEnum.CLOSED.value,
                    created_at=_now(),
                    updated_at=_now(),
                )
                self._storage[service_name] = state
                self._next_id += 1
            return self._storage[service_name]

    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        opened_at: Optional[datetime] = None,
    ) -> bool:
        """Update circuit breaker state."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=state,
                failure_count=failure_count if failure_count is not None else entry.failure_count,
                success_count=success_count if success_count is not None else entry.success_count,
                last_failure_at=entry.last_failure_at,
                opened_at=opened_at if opened_at is not None else entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def increment_failure_count(
        self,
        service_name: str,
        last_failure_at: Optional[datetime] = None,
    ) -> int:
        """Increment failure count."""
        with self._lock:
            entry = self.get_or_create(service_name)
            new_count = entry.failure_count + 1

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=new_count,
                success_count=entry.success_count,
                last_failure_at=last_failure_at or _now(),
                opened_at=entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return new_count

    def reset_counts(self, service_name: str) -> bool:
        """Reset failure and success counts."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=0,
                success_count=0,
                last_failure_at=entry.last_failure_at,
                opened_at=entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def set_manual_control(
        self,
        service_name: str,
        state: str,
        controlled_by_id: int,
        reason: str = "",
        expires_at: Optional[datetime] = None,
    ) -> bool:
        """Set manual control override."""
        with self._lock:
            entry = self.get_or_create(service_name)

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=state,
                failure_count=entry.failure_count,
                success_count=entry.success_count,
                last_failure_at=entry.last_failure_at,
                opened_at=_now() if state == CircuitBreakerStateEnum.OPEN.value else entry.opened_at,
                manually_controlled=True,
                controlled_by_id=controlled_by_id,
                control_reason=reason,
                manual_override_expires_at=expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def clear_manual_control(self, service_name: str, preserve_reason: bool = False) -> bool:
        """Clear manual control override."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.CLOSED.value,
                failure_count=0,
                success_count=0,
                last_failure_at=entry.last_failure_at,
                opened_at=None,
                manually_controlled=False,
                controlled_by_id=None,
                control_reason=entry.control_reason if preserve_reason else "",
                manual_override_expires_at=None,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """Record a failure and return updated state."""
        with self._lock:
            entry = self.get_or_create(service_name)
            new_count = entry.failure_count + 1

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=new_count,
                success_count=entry.success_count,
                last_failure_at=_now(),
                opened_at=entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return updated

    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """Record a success and return updated state."""
        with self._lock:
            entry = self.get_or_create(service_name)
            new_count = entry.success_count + 1

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=entry.failure_count,
                success_count=new_count,
                last_failure_at=entry.last_failure_at,
                opened_at=entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return updated

    def get_all_states(self) -> List[CircuitBreakerStateData]:
        """Get all circuit breaker states (alias for get_all)."""
        return self.get_all()

    def reset(self, service_name: str) -> bool:
        """Reset circuit breaker to initial closed state."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.CLOSED.value,
                failure_count=0,
                success_count=0,
                last_failure_at=None,
                opened_at=None,
                manually_controlled=False,
                controlled_by_id=None,
                control_reason="",
                manual_override_expires_at=None,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def atomic_force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
        ttl_minutes: int = 90,
    ) -> tuple[bool, str, str]:
        """Atomically force open a circuit breaker."""
        with self._lock:
            entry = self.get_or_create(service_name)
            previous_state = entry.state

            expires_at = _now() + timedelta(minutes=ttl_minutes) if ttl_minutes > 0 else None

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.OPEN.value,
                failure_count=entry.failure_count,
                success_count=entry.success_count,
                last_failure_at=entry.last_failure_at,
                opened_at=_now(),
                manually_controlled=True,
                controlled_by_id=controlled_by_id,
                control_reason=reason,
                manual_override_expires_at=expires_at,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return (True, previous_state, CircuitBreakerStateEnum.OPEN.value)

    def atomic_force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """Atomically force close a circuit breaker."""
        with self._lock:
            entry = self.get_or_create(service_name)
            previous_state = entry.state

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.CLOSED.value,
                failure_count=0,
                success_count=0,
                last_failure_at=entry.last_failure_at,
                opened_at=None,
                manually_controlled=True,
                controlled_by_id=controlled_by_id,
                control_reason=reason,
                manual_override_expires_at=None,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)

    def atomic_reset(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """Atomically reset a circuit breaker to initial state."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return (False, "", "")

            previous_state = entry.state

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.CLOSED.value,
                failure_count=0,
                success_count=0,
                last_failure_at=None,
                opened_at=None,
                manually_controlled=False,
                controlled_by_id=None,
                control_reason=reason,
                manual_override_expires_at=None,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)

    def get_all_open(self) -> List[CircuitBreakerStateData]:
        """Get all open circuit breakers."""
        with self._lock:
            return [entry for entry in self._storage.values() if entry.state == CircuitBreakerStateEnum.OPEN.value]

    def get_all(self) -> List[CircuitBreakerStateData]:
        """Get all circuit breaker states."""
        with self._lock:
            return list(self._storage.values())

    def delete(self, service_name: str) -> bool:
        """Delete a circuit breaker state."""
        with self._lock:
            if service_name in self._storage:
                del self._storage[service_name]
                return True
            return False

    def clear(self) -> None:
        """Clear all entries (for testing)."""
        with self._lock:
            self._storage.clear()
            self._next_id = 1


# =============================================================================
# L2 Sync Failure Record & Shadow Logger
# =============================================================================


class DriftReconciliationResult(Enum):
    """드리프트 복구 결과."""
    L1_WINS = "l1_wins"       # L1 상태가 더 제한적 → L2에 전파
    L2_WINS = "l2_wins"       # L2 상태가 더 제한적 → L1에 전파
    TIMESTAMP_L1 = "timestamp_l1"  # 같은 상태, L1이 더 최신
    TIMESTAMP_L2 = "timestamp_l2"  # 같은 상태, L2가 더 최신
    NO_DRIFT = "no_drift"     # 드리프트 없음 (동일 상태)
    SKIPPED = "skipped"       # 건너뜀 (데이터 없음 등)


@dataclass
class DriftReconciliationRecord:
    """
    드리프트 복구 기록.
    
    L2 복구 후 L1과 L2 간 상태 불일치 해결 기록.
    
    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §6
    """
    service_name: str
    l1_state: str
    l2_state: str
    l1_updated_at: Optional[datetime]
    l2_updated_at: Optional[datetime]
    winner: str  # "l1", "l2", "both" (동일)
    result: DriftReconciliationResult
    reconciled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    jitter_seconds: float = 0.0


class DriftReconciler:
    """
    L2 복구 시 상태 드리프트 해결.
    
    L2 장애 동안 L1만 업데이트되면, L2 복구 후 L1과 L2의 상태가 불일치합니다.
    이 클래스는 "Most Restrictive Wins" 전략으로 드리프트를 해결합니다.
    
    우선순위: OPEN (3) > HALF_OPEN (2) > CLOSED (1)
    - 더 제한적인 상태가 우선 (안전 우선)
    - 같은 상태면 더 최신 타임스탬프가 우선
    
    Thundering Herd 방지:
    - L2 복구 시 모든 Pod가 동시에 쓰기 요청을 보내면 L2 과부하 발생
    - Jitter를 적용하여 순차적으로 동기화
    
    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §6
    """
    
    # 상태 우선순위: 높을수록 더 제한적
    STATE_PRIORITY: Dict[str, int] = {
        "open": 3,       # 가장 제한적 (우선)
        "half_open": 2,
        "closed": 1,     # 가장 허용적
    }
    
    def __init__(
        self,
        min_jitter_seconds: float = 0.0,
        max_jitter_seconds: float = 5.0,
        on_reconciled: Optional[Callable[[DriftReconciliationRecord], None]] = None,
    ):
        """
        Args:
            min_jitter_seconds: 최소 Jitter (초)
            max_jitter_seconds: 최대 Jitter (초)
            on_reconciled: 복구 완료 시 콜백 (메트릭, 로깅 등)
        """
        self._min_jitter = min_jitter_seconds
        self._max_jitter = max_jitter_seconds
        self._on_reconciled = on_reconciled
        self._reconciliation_history: List[DriftReconciliationRecord] = []
        self._lock = threading.RLock()
        self._max_history = 1000
    
    def get_jitter(self) -> float:
        """Jitter 값 생성 (0~max 사이 무작위)."""
        return random.uniform(self._min_jitter, self._max_jitter)
    
    def reconcile(
        self,
        service_name: str,
        l1_state: str,
        l2_state: str,
        l1_updated_at: Optional[datetime] = None,
        l2_updated_at: Optional[datetime] = None,
    ) -> Tuple[str, DriftReconciliationResult]:
        """
        드리프트 해결 전략:
        1. 더 제한적인 상태가 우선 (Most Restrictive Wins)
        2. 같은 레벨이면 더 최신 타임스탬프가 우선
        
        Args:
            service_name: 서비스 이름
            l1_state: L1 상태 (closed, half_open, open)
            l2_state: L2 상태
            l1_updated_at: L1 마지막 업데이트 시간
            l2_updated_at: L2 마지막 업데이트 시간
            
        Returns:
            (승리 상태, 복구 결과)
        """
        l1_priority = self.STATE_PRIORITY.get(l1_state.lower(), 0)
        l2_priority = self.STATE_PRIORITY.get(l2_state.lower(), 0)
        
        # 상태가 같으면 드리프트 없음
        if l1_state.lower() == l2_state.lower():
            winner_state = l1_state
            result = DriftReconciliationResult.NO_DRIFT
            winner = "both"
        elif l1_priority > l2_priority:
            # L1이 더 제한적 → L2에 전파
            winner_state = l1_state
            result = DriftReconciliationResult.L1_WINS
            winner = "l1"
            logger.info(
                f"[DriftReconciler] Reconciled {service_name}: "
                f"{l1_state.upper()} wins over {l2_state.upper()} (L1 more restrictive)"
            )
        elif l2_priority > l1_priority:
            # L2가 더 제한적 → L1에 전파
            winner_state = l2_state
            result = DriftReconciliationResult.L2_WINS
            winner = "l2"
            logger.info(
                f"[DriftReconciler] Reconciled {service_name}: "
                f"{l2_state.upper()} wins over {l1_state.upper()} (L2 more restrictive)"
            )
        else:
            # 같은 레벨: 타임스탬프 비교
            if l1_updated_at and l2_updated_at:
                if l1_updated_at > l2_updated_at:
                    winner_state = l1_state
                    result = DriftReconciliationResult.TIMESTAMP_L1
                    winner = "l1"
                else:
                    winner_state = l2_state
                    result = DriftReconciliationResult.TIMESTAMP_L2
                    winner = "l2"
            elif l1_updated_at:
                winner_state = l1_state
                result = DriftReconciliationResult.TIMESTAMP_L1
                winner = "l1"
            elif l2_updated_at:
                winner_state = l2_state
                result = DriftReconciliationResult.TIMESTAMP_L2
                winner = "l2"
            else:
                # 타임스탬프 없으면 L1 우선 (로컬 데이터 신뢰)
                winner_state = l1_state
                result = DriftReconciliationResult.TIMESTAMP_L1
                winner = "l1"
        
        # 기록 저장
        record = DriftReconciliationRecord(
            service_name=service_name,
            l1_state=l1_state,
            l2_state=l2_state,
            l1_updated_at=l1_updated_at,
            l2_updated_at=l2_updated_at,
            winner=winner,
            result=result,
        )
        
        with self._lock:
            self._reconciliation_history.append(record)
            if len(self._reconciliation_history) > self._max_history:
                self._reconciliation_history = self._reconciliation_history[-self._max_history:]
        
        # 콜백 실행
        if self._on_reconciled:
            try:
                self._on_reconciled(record)
            except Exception as e:
                logger.warning(f"[DriftReconciler] Callback error: {e}")
        
        return winner_state, result
    
    def schedule_reconciliation_sync(
        self,
        service_name: str,
        do_reconcile: Callable[[], None],
    ) -> float:
        """
        Jitter를 적용하여 동기적으로 지연 후 동기화.
        
        Args:
            service_name: 서비스 이름
            do_reconcile: 실제 동기화 실행 함수
            
        Returns:
            적용된 Jitter 시간 (초)
        """
        jitter = self.get_jitter()
        
        if jitter > 0:
            logger.debug(
                f"[DriftReconciler] Scheduling reconciliation for {service_name} "
                f"in {jitter:.2f}s (jitter applied)"
            )
            time.sleep(jitter)
        
        do_reconcile()
        return jitter
    
    async def schedule_reconciliation_async(
        self,
        service_name: str,
        do_reconcile: Callable[[], None],
    ) -> float:
        """
        Jitter를 적용하여 비동기적으로 지연 후 동기화.
        
        Args:
            service_name: 서비스 이름
            do_reconcile: 실제 동기화 실행 함수
            
        Returns:
            적용된 Jitter 시간 (초)
        """
        jitter = self.get_jitter()
        
        if jitter > 0:
            logger.info(
                f"[DriftReconciler] Scheduling reconciliation for {service_name} "
                f"in {jitter:.2f}s (jitter applied)"
            )
            await asyncio.sleep(jitter)
        
        do_reconcile()
        return jitter
    
    def get_history(self) -> List[DriftReconciliationRecord]:
        """복구 기록 조회."""
        with self._lock:
            return list(self._reconciliation_history)
    
    def get_stats(self) -> Dict[str, Any]:
        """복구 통계 조회."""
        with self._lock:
            history = list(self._reconciliation_history)
        
        if not history:
            return {
                "total_reconciliations": 0,
                "by_result": {},
                "by_winner": {},
                "affected_services": [],
            }
        
        by_result: Dict[str, int] = {}
        by_winner: Dict[str, int] = {}
        services = set()
        
        for record in history:
            result_name = record.result.value
            by_result[result_name] = by_result.get(result_name, 0) + 1
            by_winner[record.winner] = by_winner.get(record.winner, 0) + 1
            services.add(record.service_name)
        
        return {
            "total_reconciliations": len(history),
            "by_result": by_result,
            "by_winner": by_winner,
            "affected_services": list(services),
            "last_reconciliation": history[-1].reconciled_at.isoformat() if history else None,
        }
    
    def clear_history(self) -> None:
        """기록 초기화 (테스트용)."""
        with self._lock:
            self._reconciliation_history.clear()


# 모듈 레벨 싱글톤 인스턴스
_drift_reconciler: Optional[DriftReconciler] = None
_drift_reconciler_lock = threading.Lock()


def get_drift_reconciler() -> DriftReconciler:
    """Get the singleton DriftReconciler instance."""
    global _drift_reconciler
    if _drift_reconciler is None:
        with _drift_reconciler_lock:
            if _drift_reconciler is None:
                _drift_reconciler = DriftReconciler()
    return _drift_reconciler


@dataclass
class L2SyncFailureRecord:
    """
    L2 동기화 실패 기록.

    L2 장애 동안 발생한 상태 변화를 기록하여
    사후 분석(Forensic) 및 복구 후 재동기화에 활용합니다.

    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §7
    """

    service_name: str
    intended_state: str
    failure_time: datetime
    error_message: str
    l1_state_at_failure: str
    adapter_type: str = "unknown"
    operation: str = "sync"  # sync, update, delete
    synced_after_recovery: bool = False
    recovery_time: Optional[datetime] = None


class ShadowLogger:
    """
    L2 장애 동안의 상태 변화를 로컬에 기록.

    Shadow Log는 L2가 장애 상태일 때 발생한 모든 상태 변경을
    메모리에 기록하여, L2 복구 후 재동기화 및 Forensic 분석에 활용됩니다.

    Thread-safe 구현으로 동시 접근에 안전합니다.

    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §7
    """

    _instance: Optional["ShadowLogger"] = None
    _lock_class = None

    def __new__(cls) -> "ShadowLogger":
        """Singleton pattern."""
        if cls._instance is None:
            cls._lock_class = threading.Lock()
            with cls._lock_class:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance

    def _init(self) -> None:
        """Initialize shadow logger."""
        self._failure_log: List[L2SyncFailureRecord] = []
        self._lock = threading.RLock()
        self._max_entries = 1000  # 기본값, 런타임에 변경 가능

    def set_max_entries(self, max_entries: int) -> None:
        """Set maximum entries to keep."""
        with self._lock:
            self._max_entries = max_entries
            # Trim if over limit
            if len(self._failure_log) > max_entries:
                self._failure_log = self._failure_log[-max_entries:]

    def record_sync_failure(
        self,
        service_name: str,
        intended_state: str,
        error: Exception,
        adapter_type: str = "unknown",
        operation: str = "sync",
    ) -> None:
        """
        L2 동기화 실패 기록.

        Args:
            service_name: 서비스 이름
            intended_state: 동기화하려던 상태
            error: 발생한 예외
            adapter_type: L2 어댑터 타입 (redis, django 등)
            operation: 작업 유형 (sync, update, delete)
        """
        with self._lock:
            record = L2SyncFailureRecord(
                service_name=service_name,
                intended_state=intended_state,
                failure_time=datetime.now(timezone.utc),
                error_message=str(error),
                l1_state_at_failure=intended_state,
                adapter_type=adapter_type,
                operation=operation,
            )
            self._failure_log.append(record)

            # Trim old entries if over limit
            if len(self._failure_log) > self._max_entries:
                self._failure_log = self._failure_log[-self._max_entries:]

            logger.warning(
                f"[ShadowLog] L2 sync failed: service={service_name} "
                f"state={intended_state} adapter={adapter_type} error={error}"
            )

    def get_unsynced_records(self) -> List[L2SyncFailureRecord]:
        """아직 동기화되지 않은 기록 조회."""
        with self._lock:
            return [r for r in self._failure_log if not r.synced_after_recovery]

    def get_all_records(self) -> List[L2SyncFailureRecord]:
        """모든 기록 조회."""
        with self._lock:
            return list(self._failure_log)

    def mark_as_synced(self, service_name: str) -> int:
        """
        복구 후 동기화 완료 마킹.

        Args:
            service_name: 서비스 이름

        Returns:
            마킹된 레코드 수
        """
        count = 0
        with self._lock:
            for record in self._failure_log:
                if record.service_name == service_name and not record.synced_after_recovery:
                    record.synced_after_recovery = True
                    record.recovery_time = datetime.now(timezone.utc)
                    count += 1
        if count > 0:
            logger.info(f"[ShadowLog] Marked {count} records as synced for {service_name}")
        return count

    def mark_all_as_synced(self) -> int:
        """모든 미동기화 레코드를 동기화 완료로 마킹."""
        count = 0
        with self._lock:
            now = datetime.now(timezone.utc)
            for record in self._failure_log:
                if not record.synced_after_recovery:
                    record.synced_after_recovery = True
                    record.recovery_time = now
                    count += 1
        if count > 0:
            logger.info(f"[ShadowLog] Marked all {count} records as synced")
        return count

    def get_stats(self) -> Dict:
        """Shadow Log 통계 조회."""
        with self._lock:
            unsynced = [r for r in self._failure_log if not r.synced_after_recovery]
            services = set(r.service_name for r in self._failure_log)
            return {
                "total_records": len(self._failure_log),
                "unsynced_count": len(unsynced),
                "affected_services": list(services),
                "max_entries": self._max_entries,
                "oldest_record": (
                    self._failure_log[0].failure_time.isoformat()
                    if self._failure_log else None
                ),
                "newest_record": (
                    self._failure_log[-1].failure_time.isoformat()
                    if self._failure_log else None
                ),
            }

    def clear(self) -> None:
        """Clear all records (for testing)."""
        with self._lock:
            self._failure_log.clear()

    def analyze_l2_failures(self) -> Dict[str, Any]:
        """
        L2 장애 기간 동안의 상태 변화 분석.

        Forensic Advisor와 연동하여 L2 장애 시 발생한
        상태 변화를 타임라인 형태로 분석합니다.

        Returns:
            분석 결과 딕셔너리:
            - unsynced_count: 미동기화 레코드 수
            - affected_services: 영향 받은 서비스 목록
            - failure_timeline: 시간순 실패 이력
            - by_adapter: 어댑터별 통계
            - by_operation: 작업별 통계
            - time_range: 장애 시간 범위
            - recommendations: 권장 조치

        Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §7.3
        """
        with self._lock:
            unsynced = [r for r in self._failure_log if not r.synced_after_recovery]
            all_records = list(self._failure_log)

        if not all_records:
            return {
                "unsynced_count": 0,
                "affected_services": [],
                "failure_timeline": [],
                "by_adapter": {},
                "by_operation": {},
                "time_range": None,
                "recommendations": ["No L2 failures recorded."],
            }

        # 서비스별 집계
        affected_services = list(set(r.service_name for r in unsynced))

        # 타임라인 생성
        sorted_records = sorted(all_records, key=lambda x: x.failure_time)
        failure_timeline = [
            {
                "service": r.service_name,
                "state": r.intended_state,
                "time": r.failure_time.isoformat(),
                "error": r.error_message,
                "adapter": r.adapter_type,
                "operation": r.operation,
                "synced": r.synced_after_recovery,
            }
            for r in sorted_records
        ]

        # 어댑터별 통계
        by_adapter: Dict[str, int] = {}
        for r in all_records:
            by_adapter[r.adapter_type] = by_adapter.get(r.adapter_type, 0) + 1

        # 작업별 통계
        by_operation: Dict[str, int] = {}
        for r in all_records:
            by_operation[r.operation] = by_operation.get(r.operation, 0) + 1

        # 시간 범위
        time_range = None
        if sorted_records:
            time_range = {
                "start": sorted_records[0].failure_time.isoformat(),
                "end": sorted_records[-1].failure_time.isoformat(),
                "duration_seconds": (
                    sorted_records[-1].failure_time - sorted_records[0].failure_time
                ).total_seconds(),
            }

        # 권장 조치 생성
        recommendations = self._generate_recommendations(
            unsynced_count=len(unsynced),
            affected_services=affected_services,
            by_adapter=by_adapter,
            total_records=len(all_records),
        )

        return {
            "unsynced_count": len(unsynced),
            "affected_services": affected_services,
            "failure_timeline": failure_timeline,
            "by_adapter": by_adapter,
            "by_operation": by_operation,
            "time_range": time_range,
            "recommendations": recommendations,
        }

    def _generate_recommendations(
        self,
        unsynced_count: int,
        affected_services: List[str],
        by_adapter: Dict[str, int],
        total_records: int,
    ) -> List[str]:
        """권장 조치 생성."""
        recommendations = []

        if unsynced_count > 0:
            recommendations.append(
                f"Sync {unsynced_count} unsynced records to L2 using "
                f"POST /api/self-healing/l2-storage/sync/to-l2"
            )

        if len(affected_services) > 3:
            recommendations.append(
                f"Multiple services affected ({len(affected_services)}). "
                f"Consider checking L2 infrastructure health."
            )

        if total_records > 100:
            recommendations.append(
                "High failure count detected. Consider increasing L2 timeout "
                "or optimizing L2 storage performance."
            )

        # 어댑터별 권장사항
        for adapter, count in by_adapter.items():
            if count > 50:
                recommendations.append(
                    f"Adapter '{adapter}' has {count} failures. "
                    f"Check {adapter} connectivity and performance."
                )

        if not recommendations:
            recommendations.append("No critical issues detected.")

        return recommendations

    def get_records_by_service(self, service_name: str) -> List[L2SyncFailureRecord]:
        """특정 서비스의 실패 기록 조회."""
        with self._lock:
            return [r for r in self._failure_log if r.service_name == service_name]

    def get_records_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> List[L2SyncFailureRecord]:
        """시간 범위 내 실패 기록 조회."""
        with self._lock:
            return [
                r for r in self._failure_log
                if start_time <= r.failure_time <= end_time
            ]


def get_shadow_logger() -> ShadowLogger:
    """Get the singleton ShadowLogger instance."""
    return ShadowLogger()


# =============================================================================
# Layered Circuit Breaker State Repository
# =============================================================================


class LayeredCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    하이브리드 레이어드 저장소 (L1 Memory + L2 Shared Storage).
    
    설계 원칙:
    - L1 (Local Memory): 모든 판정은 1차적으로 메모리에서 즉시 수행 (0.01ms)
    - L2 (Shared Storage): Redis나 DB는 백그라운드에서 비동기적으로 동기화
    - 타임아웃 적용: L2 응답이 늦으면 즉시 포기하고 L1만으로 동작 (Fail-Fast)
    - Shadow Logging: L2 장애 시 발생한 변경사항을 로컬에 기록
    
    장점:
    - 외부 의존성(Redis/DB)이 잠시 죽어도 시스템은 L1만으로 계속 동작
    - 분산 환경에서도 최종적으로 일관성 유지 (Eventual Consistency)
    - 호스트 DB에 침투하지 않음 (L2는 opt-in)
    
    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md
    
    Usage:
        # 메모리만 사용 (기본, 단일 서버)
        repo = LayeredCircuitBreakerStateRepository()
        repo = LayeredCircuitBreakerStateRepository()
        
        # L2로 Redis 추가 (분산 환경)
        from selfhealing.adapters.redis import RedisCircuitBreakerStateRepository
        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=RedisCircuitBreakerStateRepository(),
            sync_interval_seconds=5,
        )
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
                "redis": 0.05,    # 50ms
                "database": 0.2,  # 200ms
                "django": 0.2,    # 200ms
            }
            return timeouts.get(self._adapter_type.lower(), 0.1)
    
    def _load_from_l2_with_timeout(self) -> None:
        """L2에서 L1으로 초기 데이터 로드 (타임아웃 적용)."""
        if not self._l2:
            return
        
        timeout = self._get_timeout_seconds() * 2  # 초기 로드는 2배 타임아웃
        start_time = time.perf_counter()
        
        try:
            executor = self._get_executor()
            future = executor.submit(self._l2.get_all)
            all_states = future.result(timeout=timeout)
            
            for state in all_states:
                self._l1.get_or_create(state.service_name)
                self._l1.update_state(
                    service_name=state.service_name,
                    state=state.state,
                    failure_count=state.failure_count,
                    success_count=state.success_count,
                    opened_at=state.opened_at,
                )
            
            self._last_sync_time = _now()
            self._l2_healthy = True
            self._l2_consecutive_failures = 0
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._metrics["l2_latency_total_ms"] += elapsed_ms
            self._metrics["l2_latency_count"] += 1
            
            logger.info(
                f"[LayeredRepo] L2 initial load completed: "
                f"{len(all_states)} states loaded in {elapsed_ms:.1f}ms"
            )
            
        except FuturesTimeoutError:
            self._handle_l2_timeout("initial_load", None)
            logger.warning(
                f"[LayeredRepo] L2 initial load timeout ({timeout*1000:.0f}ms). "
                f"Starting with empty L1."
            )
        except Exception as e:
            self._handle_l2_error("initial_load", None, e)
            logger.warning(
                f"[LayeredRepo] L2 initial load failed: {e}. "
                f"Starting with empty L1."
            )
    
    def _load_from_l2(self) -> None:
        """L2에서 L1으로 초기 데이터 로드 (레거시, 타임아웃 없음)."""
        # 레거시 호환성 유지, 새 메서드로 위임
        self._load_from_l2_with_timeout()
    
    def _handle_l2_timeout(self, operation: str, service_name: Optional[str]) -> None:
        """L2 타임아웃 처리."""
        self._metrics["l2_timeout_count"] += 1
        self._l2_consecutive_failures += 1
        self._l2_last_error_time = datetime.now(timezone.utc)
        
        if self._l2_consecutive_failures >= 3:
            self._l2_healthy = False
            self._l2_was_unhealthy = True  # 복구 감지용 플래그 설정
        
        # Prometheus 메트릭 업데이트 (가능한 경우)
        try:
            from selfhealing.services.metrics import record_l2_timeout
            record_l2_timeout(self._adapter_type, operation)
        except ImportError:
            pass
    
    def _handle_l2_error(
        self,
        operation: str,
        service_name: Optional[str],
        error: Exception,
        intended_state: str = "",
    ) -> None:
        """L2 오류 처리 및 Shadow Log 기록."""
        self._metrics["l2_sync_failure_count"] += 1
        self._l2_consecutive_failures += 1
        self._l2_last_error_time = datetime.now(timezone.utc)
        
        if self._l2_consecutive_failures >= 3:
            self._l2_healthy = False
            self._l2_was_unhealthy = True  # 복구 감지용 플래그 설정
        
        # Shadow Log에 기록
        if service_name and intended_state:
            self._shadow_logger.record_sync_failure(
                service_name=service_name,
                intended_state=intended_state,
                error=error,
                adapter_type=self._adapter_type,
                operation=operation,
            )
        
        # Prometheus 메트릭 업데이트 (가능한 경우)
        try:
            from selfhealing.services.metrics import record_l2_sync_failure
            record_l2_sync_failure(self._adapter_type, operation)
        except ImportError:
            pass
    
    def _handle_l2_success(self, elapsed_ms: float) -> None:
        """L2 성공 처리 및 복구 감지."""
        was_unhealthy = not self._l2_healthy or self._l2_was_unhealthy
        
        self._metrics["l2_sync_success_count"] += 1
        self._metrics["l2_latency_total_ms"] += elapsed_ms
        self._metrics["l2_latency_count"] += 1
        self._l2_consecutive_failures = 0
        self._l2_healthy = True
        
        # L2 복구 감지: unhealthy → healthy 전환 시
        if was_unhealthy:
            self._l2_was_unhealthy = False
            logger.info(
                f"[LayeredRepo] L2 recovery detected after "
                f"{self._metrics.get('l2_sync_failure_count', 0)} failures. "
                f"Initiating drift reconciliation."
            )
            # 백그라운드에서 드리프트 복구 실행
            self._schedule_drift_reconciliation()
        
        # Prometheus 메트릭 업데이트 (가능한 경우)
        try:
            from selfhealing.services.metrics import record_l2_latency
            record_l2_latency(self._adapter_type, elapsed_ms / 1000.0)
        except ImportError:
            pass
    
    def _schedule_drift_reconciliation(self) -> None:
        """드리프트 복구를 백그라운드에서 스케줄."""
        def _run_reconciliation():
            try:
                jitter = self._drift_reconciler.get_jitter()
                if jitter > 0:
                    logger.debug(
                        f"[LayeredRepo] Drift reconciliation scheduled with "
                        f"{jitter:.2f}s jitter (Thundering Herd prevention)"
                    )
                    time.sleep(jitter)
                
                self._reconcile_all_drift()
            except Exception as e:
                logger.error(f"[LayeredRepo] Drift reconciliation error: {e}")
        
        try:
            executor = self._get_executor()
            executor.submit(_run_reconciliation)
        except Exception as e:
            logger.warning(f"[LayeredRepo] Failed to schedule drift reconciliation: {e}")
    
    def _reconcile_all_drift(self) -> Dict[str, Any]:
        """
        모든 서비스의 L1/L2 드리프트 해결.
        
        Returns:
            복구 결과 요약
        """
        if not self._l2:
            return {"success": False, "reason": "L2 not configured"}
        
        reconciled_count = 0
        l1_wins_count = 0
        l2_wins_count = 0
        errors = []
        
        # L1의 모든 상태 가져오기
        l1_states = self._l1.get_all()
        
        for l1_state in l1_states:
            try:
                # L2에서 해당 서비스 상태 가져오기
                timeout = self._get_timeout_seconds()
                executor = self._get_executor()
                future = executor.submit(
                    self._l2.get_by_service_name, l1_state.service_name
                )
                
                try:
                    l2_state = future.result(timeout=timeout)
                except FuturesTimeoutError:
                    logger.warning(
                        f"[LayeredRepo] Drift reconciliation timeout for "
                        f"{l1_state.service_name}, skipping"
                    )
                    continue
                
                if l2_state is None:
                    # L2에 없으면 L1 상태를 L2로 동기화
                    self._sync_to_l2_with_timeout(l1_state.service_name, l1_state)
                    l1_wins_count += 1
                    reconciled_count += 1
                    continue
                
                # 드리프트 해결
                winner_state, result = self._drift_reconciler.reconcile(
                    service_name=l1_state.service_name,
                    l1_state=l1_state.state,
                    l2_state=l2_state.state,
                    l1_updated_at=l1_state.updated_at,
                    l2_updated_at=l2_state.updated_at,
                )
                
                if result == DriftReconciliationResult.NO_DRIFT:
                    continue
                
                reconciled_count += 1
                
                if result in (
                    DriftReconciliationResult.L1_WINS,
                    DriftReconciliationResult.TIMESTAMP_L1,
                ):
                    # L1 → L2 동기화
                    self._sync_to_l2_with_timeout(l1_state.service_name, l1_state)
                    l1_wins_count += 1
                else:
                    # L2 → L1 동기화
                    self._l1.update_state(
                        service_name=l2_state.service_name,
                        state=l2_state.state,
                        failure_count=l2_state.failure_count,
                        success_count=l2_state.success_count,
                        opened_at=l2_state.opened_at,
                    )
                    l2_wins_count += 1
                
            except Exception as e:
                errors.append({
                    "service": l1_state.service_name,
                    "error": str(e),
                })
                logger.warning(
                    f"[LayeredRepo] Drift reconciliation error for "
                    f"{l1_state.service_name}: {e}"
                )
        
        self._metrics["drift_reconciliation_count"] += reconciled_count
        
        # Shadow Log 정리
        if reconciled_count > 0:
            self._shadow_logger.mark_all_as_synced()
        
        result = {
            "success": len(errors) == 0,
            "total_checked": len(l1_states),
            "reconciled": reconciled_count,
            "l1_wins": l1_wins_count,
            "l2_wins": l2_wins_count,
            "errors": errors,
        }
        
        logger.info(
            f"[LayeredRepo] Drift reconciliation completed: "
            f"{reconciled_count} reconciled, L1 wins={l1_wins_count}, L2 wins={l2_wins_count}"
        )
        
        return result
    
    def _sync_to_l2_with_timeout(
        self,
        service_name: str,
        state: CircuitBreakerStateData,
    ) -> bool:
        """
        L2로 동기화 (타임아웃 적용).
        
        Args:
            service_name: 서비스 이름
            state: 동기화할 상태
            
        Returns:
            성공 여부
        """
        if not self._l2:
            return False
        
        timeout = self._get_timeout_seconds()
        start_time = time.perf_counter()
        
        def _do_sync():
            self._l2.get_or_create(service_name)
            self._l2.update_state(
                service_name=service_name,
                state=state.state,
                failure_count=state.failure_count,
                success_count=state.success_count,
                opened_at=state.opened_at,
            )
        
        try:
            executor = self._get_executor()
            future = executor.submit(_do_sync)
            future.result(timeout=timeout)
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._handle_l2_success(elapsed_ms)
            return True
            
        except FuturesTimeoutError:
            self._handle_l2_timeout("sync", service_name)
            logger.warning(
                f"[LayeredRepo] L2 sync timeout for {service_name} "
                f"({timeout*1000:.0f}ms). L1 isolated."
            )
            return False
            
        except Exception as e:
            self._handle_l2_error("sync", service_name, e, state.state)
            return False
    
    def _sync_to_l2_async(self, service_name: str, state: CircuitBreakerStateData) -> None:
        """L2로 비동기 동기화 (백그라운드, 타임아웃 적용)."""
        if not self._l2:
            return
        
        # ThreadPoolExecutor로 비동기 실행
        def _sync():
            self._sync_to_l2_with_timeout(service_name, state)
        
        try:
            executor = self._get_executor()
            executor.submit(_sync)
        except Exception as e:
            logger.warning(f"[LayeredRepo] Failed to submit L2 sync task: {e}")
    
    # =========================================================================
    # CircuitBreakerStateRepository 인터페이스 구현 (L1 우선)
    # =========================================================================
    
    def get_by_service_name(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """L1에서 조회. L1에 없으면 L2 확인 후 L1에 캐시."""
        result = self._l1.get_by_service_name(service_name)
        
        if result is None and self._l2 and self._l2_healthy:
            timeout = self._get_timeout_seconds()
            start_time = time.perf_counter()
            
            try:
                executor = self._get_executor()
                future = executor.submit(self._l2.get_by_service_name, service_name)
                l2_result = future.result(timeout=timeout)
                
                if l2_result:
                    # L1에 캐시
                    self._l1.get_or_create(service_name)
                    self._l1.update_state(
                        service_name=service_name,
                        state=l2_result.state,
                        failure_count=l2_result.failure_count,
                        success_count=l2_result.success_count,
                        opened_at=l2_result.opened_at,
                    )
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    self._handle_l2_success(elapsed_ms)
                    return self._l1.get_by_service_name(service_name)
                    
            except FuturesTimeoutError:
                self._handle_l2_timeout("get", service_name)
            except Exception as e:
                self._handle_l2_error("get", service_name, e)
        
        return result
    
    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """L1에서 조회/생성. L2도 동기화."""
        result = self._l1.get_or_create(service_name)
        self._sync_to_l2_async(service_name, result)
        return result
    
    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        opened_at: Optional[datetime] = None,
    ) -> bool:
        """L1 업데이트 후 L2 비동기 동기화."""
        result = self._l1.update_state(
            service_name=service_name,
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            opened_at=opened_at,
        )
        
        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def increment_failure_count(
        self,
        service_name: str,
        last_failure_at: Optional[datetime] = None,
    ) -> int:
        """L1에서 카운트 증가 후 L2 동기화."""
        result = self._l1.increment_failure_count(service_name, last_failure_at)
        
        updated = self._l1.get_by_service_name(service_name)
        if updated:
            self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def reset_failure_count(self, service_name: str) -> bool:
        """L1에서 리셋 후 L2 동기화."""
        result = self._l1.reset_failure_count(service_name)
        
        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def set_half_open(self, service_name: str) -> bool:
        """L1에서 half-open 설정 후 L2 동기화."""
        result = self._l1.set_half_open(service_name)
        
        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def set_open(
        self,
        service_name: str,
        opened_at: Optional[datetime] = None,
    ) -> bool:
        """L1에서 open 설정 후 L2 동기화."""
        result = self._l1.set_open(service_name, opened_at)
        
        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def set_closed(self, service_name: str, reason: Optional[str] = None) -> tuple:
        """L1에서 closed 설정 후 L2 동기화."""
        result = self._l1.set_closed(service_name, reason)
        
        if result[0]:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def get_all_open(self) -> List[CircuitBreakerStateData]:
        """L1에서 open 상태 조회."""
        return self._l1.get_all_open()
    
    def get_all(self) -> List[CircuitBreakerStateData]:
        """L1에서 전체 조회."""
        return self._l1.get_all()
    
    def delete(self, service_name: str) -> bool:
        """L1에서 삭제. L2도 동기화."""
        result = self._l1.delete(service_name)
        
        if result and self._l2:
            try:
                self._l2.delete(service_name)
            except Exception:
                pass
        
        return result
    
    def clear(self) -> None:
        """L1 클리어. L2는 건드리지 않음 (테스트용)."""
        self._l1.clear()
    
    # =========================================================================
    # 추가 추상 메서드 구현 (L1 위임)
    # =========================================================================
    
    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """L1에서 실패 기록 후 L2 동기화."""
        result = self._l1.record_failure(service_name)
        self._sync_to_l2_async(service_name, result)
        return result
    
    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """L1에서 성공 기록 후 L2 동기화."""
        result = self._l1.record_success(service_name)
        self._sync_to_l2_async(service_name, result)
        return result
    
    def get_all_states(self) -> List[CircuitBreakerStateData]:
        """L1에서 전체 상태 조회."""
        return self._l1.get_all_states()
    
    def reset(self, service_name: str) -> bool:
        """L1에서 리셋 후 L2 동기화."""
        result = self._l1.reset(service_name)
        
        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def atomic_force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
        ttl_minutes: int = 90,
    ) -> tuple:
        """L1에서 강제 open 후 L2 동기화."""
        result = self._l1.atomic_force_open(service_name, reason, controlled_by_id, ttl_minutes)
        
        if result[0]:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def atomic_force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple:
        """L1에서 강제 close 후 L2 동기화."""
        result = self._l1.atomic_force_close(service_name, reason, controlled_by_id)
        
        if result[0]:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def atomic_reset(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple:
        """L1에서 리셋 후 L2 동기화."""
        result = self._l1.atomic_reset(service_name, reason, controlled_by_id)
        
        if result[0]:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def set_manual_control(
        self,
        service_name: str,
        controlled_by_id: Optional[int] = None,
        reason: str = "",
        ttl_minutes: int = 90,
    ) -> bool:
        """L1에서 수동 제어 설정 후 L2 동기화."""
        result = self._l1.set_manual_control(service_name, controlled_by_id, reason, ttl_minutes)
        
        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    def clear_manual_control(self, service_name: str, reason: str = "") -> bool:
        """L1에서 수동 제어 해제 후 L2 동기화."""
        result = self._l1.clear_manual_control(service_name, reason)
        
        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)
        
        return result
    
    # =========================================================================
    # 추가 메서드
    # =========================================================================
    
    def get_storage_info(self) -> Dict:
        """저장소 정보 조회 (L2 상태 및 메트릭 포함)."""
        avg_latency_ms = 0.0
        if self._metrics["l2_latency_count"] > 0:
            avg_latency_ms = (
                self._metrics["l2_latency_total_ms"] / 
                self._metrics["l2_latency_count"]
            )
        
        return {
            "l1_type": "memory",
            "l1_count": len(self._l1.get_all()),
            "l2_enabled": self._l2 is not None,
            "l2_type": type(self._l2).__name__ if self._l2 else None,
            "l2_adapter_type": self._adapter_type,
            "l2_healthy": self._l2_healthy,
            "l2_was_unhealthy": self._l2_was_unhealthy,
            "l2_consecutive_failures": self._l2_consecutive_failures,
            "l2_last_error_time": (
                self._l2_last_error_time.isoformat() 
                if self._l2_last_error_time else None
            ),
            "sync_interval_seconds": self._sync_interval,
            "last_sync_time": (
                self._last_sync_time.isoformat() 
                if self._last_sync_time else None
            ),
            "timeout_ms": self._get_timeout_seconds() * 1000,
            "metrics": {
                "timeout_count": self._metrics["l2_timeout_count"],
                "sync_failure_count": self._metrics["l2_sync_failure_count"],
                "sync_success_count": self._metrics["l2_sync_success_count"],
                "drift_reconciliation_count": self._metrics["drift_reconciliation_count"],
                "avg_latency_ms": round(avg_latency_ms, 2),
            },
            "shadow_log": self._shadow_logger.get_stats(),
            "drift_reconciler": self._drift_reconciler.get_stats(),
        }
    
    def get_l2_health(self) -> Dict:
        """L2 헬스 상태 조회."""
        return {
            "healthy": self._l2_healthy,
            "was_unhealthy": self._l2_was_unhealthy,
            "consecutive_failures": self._l2_consecutive_failures,
            "last_error_time": (
                self._l2_last_error_time.isoformat() 
                if self._l2_last_error_time else None
            ),
            "adapter_type": self._adapter_type,
            "timeout_ms": self._get_timeout_seconds() * 1000,
        }
    
    def reset_l2_health(self) -> None:
        """L2 헬스 상태 리셋 (수동 복구 시)."""
        self._l2_healthy = True
        self._l2_was_unhealthy = False
        self._l2_consecutive_failures = 0
        self._l2_last_error_time = None
        logger.info("[LayeredRepo] L2 health status reset manually")
    
    def get_metrics(self) -> Dict:
        """내부 메트릭 조회."""
        return dict(self._metrics)
    
    def reset_metrics(self) -> None:
        """메트릭 리셋 (테스트용)."""
        self._metrics = {
            "l2_timeout_count": 0,
            "l2_sync_failure_count": 0,
            "l2_sync_success_count": 0,
            "l2_latency_total_ms": 0.0,
            "l2_latency_count": 0,
            "drift_reconciliation_count": 0,
        }
    
    def force_sync_from_l2(self) -> bool:
        """L2에서 강제 동기화 (관리 목적)."""
        if not self._l2:
            return False
        
        try:
            self._load_from_l2_with_timeout()
            return True
        except Exception as e:
            logger.error(f"[LayeredRepo] Force sync from L2 failed: {e}")
            return False
    
    def force_sync_to_l2(self) -> Dict:
        """L1의 모든 상태를 L2로 강제 동기화."""
        if not self._l2:
            return {"success": False, "reason": "L2 not configured"}
        
        all_states = self._l1.get_all()
        success_count = 0
        failure_count = 0
        
        for state in all_states:
            if self._sync_to_l2_with_timeout(state.service_name, state):
                success_count += 1
            else:
                failure_count += 1
        
        # Shadow Log 정리
        if success_count > 0:
            self._shadow_logger.mark_all_as_synced()
        
        return {
            "success": failure_count == 0,
            "total": len(all_states),
            "synced": success_count,
            "failed": failure_count,
        }
    
    def force_drift_reconciliation(self) -> Dict[str, Any]:
        """
        수동으로 드리프트 복구 트리거.
        
        L2 복구 후 자동 복구가 실행되지 않았거나,
        관리자가 수동으로 드리프트를 해결하고자 할 때 사용.
        
        Returns:
            복구 결과 요약 딕셔너리
        """
        if not self._l2:
            return {"success": False, "reason": "L2 not configured"}
        
        logger.info("[LayeredRepo] Manual drift reconciliation triggered")
        return self._reconcile_all_drift()
    
    def get_drift_reconciler_stats(self) -> Dict[str, Any]:
        """드리프트 복구 통계 조회."""
        return self._drift_reconciler.get_stats()
    
    def get_drift_reconciliation_history(self) -> List[Dict[str, Any]]:
        """드리프트 복구 기록 조회."""
        history = self._drift_reconciler.get_history()
        return [
            {
                "service_name": r.service_name,
                "l1_state": r.l1_state,
                "l2_state": r.l2_state,
                "l1_updated_at": r.l1_updated_at.isoformat() if r.l1_updated_at else None,
                "l2_updated_at": r.l2_updated_at.isoformat() if r.l2_updated_at else None,
                "winner": r.winner,
                "result": r.result.value,
                "reconciled_at": r.reconciled_at.isoformat(),
                "jitter_seconds": r.jitter_seconds,
            }
            for r in history
        ]
    
    def reconcile_single_service(self, service_name: str) -> Dict[str, Any]:
        """
        특정 서비스의 드리프트만 복구.
        
        Args:
            service_name: 복구할 서비스 이름
            
        Returns:
            복구 결과
        """
        if not self._l2:
            return {"success": False, "reason": "L2 not configured"}
        
        l1_state = self._l1.get_by_service_name(service_name)
        if l1_state is None:
            return {"success": False, "reason": "Service not found in L1"}
        
        try:
            timeout = self._get_timeout_seconds()
            executor = self._get_executor()
            future = executor.submit(self._l2.get_by_service_name, service_name)
            l2_state = future.result(timeout=timeout)
        except FuturesTimeoutError:
            return {"success": False, "reason": "L2 timeout"}
        except Exception as e:
            return {"success": False, "reason": str(e)}
        
        if l2_state is None:
            # L2에 없으면 L1 상태를 L2로 동기화
            self._sync_to_l2_with_timeout(service_name, l1_state)
            return {
                "success": True,
                "action": "l1_to_l2",
                "reason": "L2 had no state, synced from L1",
            }
        
        # 드리프트 해결
        winner_state, result = self._drift_reconciler.reconcile(
            service_name=service_name,
            l1_state=l1_state.state,
            l2_state=l2_state.state,
            l1_updated_at=l1_state.updated_at,
            l2_updated_at=l2_state.updated_at,
        )
        
        if result == DriftReconciliationResult.NO_DRIFT:
            return {
                "success": True,
                "action": "none",
                "reason": "No drift detected",
            }
        
        self._metrics["drift_reconciliation_count"] += 1
        
        if result in (
            DriftReconciliationResult.L1_WINS,
            DriftReconciliationResult.TIMESTAMP_L1,
        ):
            self._sync_to_l2_with_timeout(service_name, l1_state)
            return {
                "success": True,
                "action": "l1_to_l2",
                "winner": "l1",
                "result": result.value,
                "winner_state": winner_state,
            }
        else:
            self._l1.update_state(
                service_name=l2_state.service_name,
                state=l2_state.state,
                failure_count=l2_state.failure_count,
                success_count=l2_state.success_count,
                opened_at=l2_state.opened_at,
            )
            return {
                "success": True,
                "action": "l2_to_l1",
                "winner": "l2",
                "result": result.value,
                "winner_state": winner_state,
            }