"""
Drift Reconciliation Module

L2 복구 시 L1과 L2 간 상태 불일치(드리프트)를 해결합니다.
"Most Restrictive Wins" 전략으로 안전 우선 복구를 수행합니다.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class DriftReconciliationResult(Enum):
    """드리프트 복구 결과."""

    L1_WINS = "l1_wins"  # L1 상태가 더 제한적 → L2에 전파
    L2_WINS = "l2_wins"  # L2 상태가 더 제한적 → L1에 전파
    TIMESTAMP_L1 = "timestamp_l1"  # 같은 상태, L1이 더 최신
    TIMESTAMP_L2 = "timestamp_l2"  # 같은 상태, L2가 더 최신
    NO_DRIFT = "no_drift"  # 드리프트 없음 (동일 상태)
    SKIPPED = "skipped"  # 건너뜀 (데이터 없음 등)


@dataclass
class DriftReconciliationRecord:
    """
    드리프트 복구 기록.

    L2 복구 후 L1과 L2 간 상태 불일치 해결 기록.
    """

    service_name: str
    l1_state: str
    l2_state: str
    l1_updated_at: datetime | None
    l2_updated_at: datetime | None
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
    """

    # 상태 우선순위: 높을수록 더 제한적
    STATE_PRIORITY: dict[str, int] = {
        "open": 3,  # 가장 제한적 (우선)
        "half_open": 2,
        "closed": 1,  # 가장 허용적
    }

    def __init__(
        self,
        min_jitter_seconds: float = 0.0,
        max_jitter_seconds: float = 5.0,
        on_reconciled: Callable[[DriftReconciliationRecord], None] | None = None,
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
        self._reconciliation_history: list[DriftReconciliationRecord] = []
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
        l1_updated_at: datetime | None = None,
        l2_updated_at: datetime | None = None,
    ) -> tuple[str, DriftReconciliationResult]:
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
            winner_state, result, winner = self._resolve_by_timestamp(
                l1_state, l2_state, l1_updated_at, l2_updated_at
            )

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
                self._reconciliation_history = self._reconciliation_history[
                    -self._max_history :
                ]

        # 콜백 실행
        if self._on_reconciled:
            try:
                self._on_reconciled(record)
            except Exception as e:
                logger.warning(f"[DriftReconciler] Callback error: {e}")

        return winner_state, result

    def _resolve_by_timestamp(
        self,
        l1_state: str,
        l2_state: str,
        l1_updated_at: datetime | None,
        l2_updated_at: datetime | None,
    ) -> tuple[str, DriftReconciliationResult, str]:
        """타임스탬프 기반으로 승자 결정."""
        if l1_updated_at and l2_updated_at:
            if l1_updated_at > l2_updated_at:
                return l1_state, DriftReconciliationResult.TIMESTAMP_L1, "l1"
            else:
                return l2_state, DriftReconciliationResult.TIMESTAMP_L2, "l2"
        elif l1_updated_at:
            return l1_state, DriftReconciliationResult.TIMESTAMP_L1, "l1"
        elif l2_updated_at:
            return l2_state, DriftReconciliationResult.TIMESTAMP_L2, "l2"
        else:
            # 타임스탬프 없으면 L1 우선 (로컬 데이터 신뢰)
            return l1_state, DriftReconciliationResult.TIMESTAMP_L1, "l1"

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

    def get_history(self) -> list[DriftReconciliationRecord]:
        """복구 기록 조회."""
        with self._lock:
            return list(self._reconciliation_history)

    def get_stats(self) -> dict[str, Any]:
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

        by_result: dict[str, int] = {}
        by_winner: dict[str, int] = {}
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
            "last_reconciliation": (
                history[-1].reconciled_at.isoformat() if history else None
            ),
        }

    def clear_history(self) -> None:
        """기록 초기화 (테스트용)."""
        with self._lock:
            self._reconciliation_history.clear()


# =============================================================================
# Module-level Singleton
# =============================================================================

_drift_reconciler: DriftReconciler | None = None
_drift_reconciler_lock = threading.Lock()


def get_drift_reconciler() -> DriftReconciler:
    """Get the singleton DriftReconciler instance."""
    global _drift_reconciler
    if _drift_reconciler is None:
        with _drift_reconciler_lock:
            if _drift_reconciler is None:
                _drift_reconciler = DriftReconciler()
    return _drift_reconciler
