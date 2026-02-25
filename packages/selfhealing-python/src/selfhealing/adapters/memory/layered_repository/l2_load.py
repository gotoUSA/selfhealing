"""
L2 Load Operations Mixin.

Provides methods for loading data from L2 storage.
"""

from __future__ import annotations

import time
from concurrent.futures import TimeoutError as FuturesTimeoutError

import structlog

from selfhealing.adapters.memory.base import _now

logger = structlog.get_logger()


class L2LoadMixin:
    """Mixin providing L2 load operations."""

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
                "layered_repo.initial_load_completed_states",
                all_states_count=len(all_states),
                elapsed_ms=elapsed_ms,
            )

        except FuturesTimeoutError:
            self._handle_l2_timeout("initial_load", None)
            logger.warning(
                "layered_repo.initial_load_timeout_ms",
                timeout_ms=timeout * 1000,
            )
        except Exception as e:
            self._handle_l2_error("initial_load", None, e)
            logger.warning(
                "layered_repo.initial_load_failed_starting",
                error=e,
            )

    def _load_from_l2(self) -> None:
        """L2에서 L1으로 초기 데이터 로드 (레거시, 타임아웃 없음)."""
        self._load_from_l2_with_timeout()
