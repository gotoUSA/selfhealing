"""
Fail-Safe Period Tracker.

Tracks fail-safe activation periods for reconciliation.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Any

from selfhealing.core.timezone import now

from .models import FailSafePeriod

logger = logging.getLogger(__name__)


class FailSafePeriodTracker:
    """
    Fail-Safe 발동 기간 추적기.

    Error Budget Gate가 Fail-Open 상태가 되면 기간을 기록합니다.
    복구 후 이 기간들을 Reconciliation에 사용합니다.
    """

    def __init__(self, max_periods: int = 100):
        """
        초기화.

        Args:
            max_periods: 보관할 최대 기간 수
        """
        self._max_periods = max_periods
        self._periods: list[FailSafePeriod] = []
        self._active_period: FailSafePeriod | None = None
        self._lock = threading.RLock()

    def start_period(
        self,
        reason: str,
        component: str = "error_budget_gate",
        service_name: str = "",
    ) -> FailSafePeriod:
        """
        Fail-Safe 기간 시작.

        Args:
            reason: 발동 사유
            component: 발동 컴포넌트
            service_name: 대상 서비스 이름 (도메인 프리 설계, 선택적)

        Returns:
            생성된 FailSafePeriod
        """
        with self._lock:
            # 이미 활성 기간이 있으면 종료
            if self._active_period:
                self._end_current_period()

            period = FailSafePeriod(
                period_id=str(uuid.uuid4()),
                started_at=now(),
                service_name=service_name,
                trigger_reason=reason,
                trigger_component=component,
            )

            self._active_period = period
            self._periods.append(period)

            # 최대 개수 유지
            if len(self._periods) > self._max_periods:
                self._periods = self._periods[-self._max_periods :]

            logger.info(
                f"[FailSafeTracker] Period started: {period.period_id}, "
                f"reason: {reason}"
            )

            return period

    def end_period(self) -> FailSafePeriod | None:
        """
        현재 Fail-Safe 기간 종료.

        Returns:
            종료된 FailSafePeriod (없으면 None)
        """
        with self._lock:
            return self._end_current_period()

    def _end_current_period(self) -> FailSafePeriod | None:
        """내부: 현재 기간 종료."""
        if not self._active_period:
            return None

        period = self._active_period
        period.ended_at = now()
        period.is_active = False
        self._active_period = None

        logger.info(
            f"[FailSafeTracker] Period ended: {period.period_id}, "
            f"duration: {period.duration_minutes:.1f} min"
        )

        return period

    def record_fail_open(self) -> None:
        """Fail-Open 발생 기록."""
        with self._lock:
            if self._active_period:
                self._active_period.fail_open_count += 1

    def record_rate_limit_exceeded(self) -> None:
        """Rate Limit 초과 기록."""
        with self._lock:
            if self._active_period:
                self._active_period.rate_limit_exceeded_count += 1

    def get_active_period(self) -> FailSafePeriod | None:
        """현재 활성 기간 조회."""
        with self._lock:
            return self._active_period

    def get_period(self, period_id: str) -> FailSafePeriod | None:
        """ID로 기간 조회."""
        with self._lock:
            for period in self._periods:
                if period.period_id == period_id:
                    return period
            return None

    def get_unreconciled_periods(self) -> list[FailSafePeriod]:
        """Reconciliation 대상 기간 조회 (종료된 기간 중 미처리)."""
        with self._lock:
            return [p for p in self._periods if not p.is_active]

    def get_periods_in_range(
        self,
        start: datetime,
        end: datetime,
    ) -> list[FailSafePeriod]:
        """특정 기간 내 Fail-Safe 기간 조회."""
        with self._lock:
            result = []
            for period in self._periods:
                # 기간이 겹치는지 확인
                period_end = period.ended_at or now()
                if period.started_at <= end and period_end >= start:
                    result.append(period)
            return result

    def get_all_periods(self, limit: int = 50) -> list[FailSafePeriod]:
        """모든 기간 조회."""
        with self._lock:
            return list(reversed(self._periods[-limit:]))

    def get_status(self) -> dict[str, Any]:
        """현재 상태 조회."""
        with self._lock:
            return {
                "active_period": (
                    self._active_period.to_dict() if self._active_period else None
                ),
                "total_periods": len(self._periods),
                "unreconciled_count": len(
                    [p for p in self._periods if not p.is_active]
                ),
            }
