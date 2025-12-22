"""
In-Memory Circuit Breaker State Repository Implementation.

Thread-safe in-memory storage for circuit breaker states.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from selfhealing.adapters.memory.base import _now
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateRepository,
    CircuitBreakerStateData,
    CircuitBreakerStateEnum,
)


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


class LayeredCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    하이브리드 레이어드 저장소 (L1 Memory + L2 Shared Storage).
    
    설계 원칙:
    - L1 (Local Memory): 모든 판정은 1차적으로 메모리에서 즉시 수행 (0.01ms)
    - L2 (Shared Storage): Redis나 DB는 백그라운드에서 비동기적으로 동기화
    
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
    
    def __init__(
        self,
        l2_repo: Optional[CircuitBreakerStateRepository] = None,
        sync_interval_seconds: float = 5.0,
    ):
        """
        Args:
            l2_repo: L2 저장소 (Redis, Django DB 등). None이면 L1만 사용.
            sync_interval_seconds: L2 동기화 주기 (초)
        """
        self._l1 = InMemoryCircuitBreakerStateRepository()
        self._l2 = l2_repo
        self._sync_interval = sync_interval_seconds
        self._last_sync_time: Optional[datetime] = None
        self._lock = threading.RLock()
        
        # L2가 있으면 초기 로드
        if self._l2:
            self._load_from_l2()
    
    def _load_from_l2(self) -> None:
        """L2에서 L1으로 초기 데이터 로드."""
        if not self._l2:
            return
        
        try:
            all_states = self._l2.get_all()
            for state in all_states:
                # L1에 복사
                self._l1.get_or_create(state.service_name)
                self._l1.update_state(
                    service_name=state.service_name,
                    state=state.state,
                    failure_count=state.failure_count,
                    success_count=state.success_count,
                    opened_at=state.opened_at,
                )
            self._last_sync_time = _now()
        except Exception:
            # L2 장애 시 무시 - L1만으로 동작
            pass
    
    def _sync_to_l2_async(self, service_name: str, state: CircuitBreakerStateData) -> None:
        """L2로 비동기 동기화 (백그라운드)."""
        if not self._l2:
            return
        
        # 간단한 비동기 처리 (실제 프로덕션에서는 ThreadPoolExecutor 사용 권장)
        def _sync():
            try:
                self._l2.get_or_create(service_name)
                self._l2.update_state(
                    service_name=service_name,
                    state=state.state,
                    failure_count=state.failure_count,
                    success_count=state.success_count,
                    opened_at=state.opened_at,
                )
            except Exception:
                # L2 장애 시 무시 - 다음 기회에 재시도
                pass
        
        # 백그라운드 스레드로 실행
        thread = threading.Thread(target=_sync, daemon=True)
        thread.start()
    
    # =========================================================================
    # CircuitBreakerStateRepository 인터페이스 구현 (L1 우선)
    # =========================================================================
    
    def get_by_service_name(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """L1에서 조회. L1에 없으면 L2 확인 후 L1에 캐시."""
        result = self._l1.get_by_service_name(service_name)
        
        if result is None and self._l2:
            try:
                l2_result = self._l2.get_by_service_name(service_name)
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
                    return self._l1.get_by_service_name(service_name)
            except Exception:
                pass  # L2 장애 시 무시
        
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
        """저장소 정보 조회."""
        return {
            "l1_type": "memory",
            "l1_count": len(self._l1.get_all()),
            "l2_enabled": self._l2 is not None,
            "l2_type": type(self._l2).__name__ if self._l2 else None,
            "sync_interval_seconds": self._sync_interval,
            "last_sync_time": self._last_sync_time.isoformat() if self._last_sync_time else None,
        }
    
    def force_sync_from_l2(self) -> bool:
        """L2에서 강제 동기화 (관리 목적)."""
        if not self._l2:
            return False
        
        try:
            self._load_from_l2()
            return True
        except Exception:
            return False