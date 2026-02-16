"""
Repository Operations Mixin.

Provides CircuitBreakerStateRepository interface implementation with L1 priority.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime

from selfhealing.interfaces.repositories import CircuitBreakerStateData

logger = logging.getLogger(__name__)


class RepositoryOperationsMixin:
    """Mixin providing repository interface operations."""

    def get_by_service_name(self, service_name: str) -> CircuitBreakerStateData | None:
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
        failure_count: int | None = None,
        success_count: int | None = None,
        opened_at: datetime | None = None,
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
        last_failure_at: datetime | None = None,
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
        opened_at: datetime | None = None,
    ) -> bool:
        """L1에서 open 설정 후 L2 동기화."""
        result = self._l1.set_open(service_name, opened_at)

        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)

        return result

    def set_closed(self, service_name: str, reason: str | None = None) -> tuple:
        """L1에서 closed 설정 후 L2 동기화."""
        result = self._l1.set_closed(service_name, reason)

        if result[0]:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)

        return result

    def get_all_open(self) -> list[CircuitBreakerStateData]:
        """L1에서 open 상태 조회."""
        return self._l1.get_all_open()

    def get_all(self) -> list[CircuitBreakerStateData]:
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

    def get_all_states(self) -> list[CircuitBreakerStateData]:
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
        controlled_by_id: int | None = None,
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
        controlled_by_id: int | None = None,
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
        controlled_by_id: int | None = None,
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
        state: str,
        controlled_by_id: int | None = None,
        reason: str = "",
        expires_at: datetime | None = None,
    ) -> bool:
        """L1에서 수동 제어 설정 후 L2 동기화."""
        result = self._l1.set_manual_control(service_name, state, controlled_by_id, reason, expires_at)

        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)

        return result

    def clear_manual_control(self, service_name: str, preserve_reason: bool = False) -> bool:
        """L1에서 수동 제어 해제 후 L2 동기화."""
        result = self._l1.clear_manual_control(service_name, preserve_reason)

        if result:
            updated = self._l1.get_by_service_name(service_name)
            if updated:
                self._sync_to_l2_async(service_name, updated)

        return result
