"""
L2 Sync Operations Mixin.

Provides methods for syncing data to/from L2 storage.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from selfhealing.interfaces.repositories import CircuitBreakerStateData

logger = logging.getLogger(__name__)


class L2SyncMixin:
    """Mixin providing L2 sync operations."""

    def _sync_to_l2_with_timeout(
        self,
        service_name: str,
        state: CircuitBreakerStateData,
    ) -> bool:
        """L2로 동기화 (타임아웃 적용)."""
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

    def _sync_to_l2_async(
        self, service_name: str, state: CircuitBreakerStateData
    ) -> None:
        """L2로 비동기 동기화 (백그라운드, 타임아웃 적용)."""
        if not self._l2:
            return

        def _sync():
            self._sync_to_l2_with_timeout(service_name, state)

        try:
            executor = self._get_executor()
            executor.submit(_sync)
        except Exception as e:
            logger.warning(f"[LayeredRepo] Failed to submit L2 sync task: {e}")

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

    def force_sync_to_l2(self) -> dict[str, Any]:
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

        if success_count > 0:
            self._shadow_logger.mark_all_as_synced()

        return {
            "success": failure_count == 0,
            "total": len(all_states),
            "synced": success_count,
            "failed": failure_count,
        }
