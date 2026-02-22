"""
Emergency Backfill Calculator.

장애 선포 전 발생한 에러에 대해 소급하여 가중치를 적용합니다.
장애 발생 시점과 감지 시점(LEVEL_3 선포) 사이의 시차를 보정합니다.

Features:
- 장애 시작 시점 추정 (메트릭 기반)
- 소급 기간 내 에러 조회 및 재계산
- Hash Chain에 소급 기록 추가

Usage:
    from selfhealing.services.error_budget.backfill import (
        EmergencyBackfillCalculator,
        BackfillResult,
    )

    calculator = EmergencyBackfillCalculator()
    result = calculator.calculate_backfill(
        emergency_id="emg_123",
        declared_at=datetime(2026, 1, 22, 10, 0, 0),
        target_level=EmergencyLevel.LEVEL_3,
    )

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.1
"""

from __future__ import annotations

import structlog
import statistics
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from selfhealing.core.timezone import now as utc_now
from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.error_budget.constants import DEFAULT_LEVEL_MULTIPLIERS

logger = structlog.get_logger()


# =============================================================================
# Protocols
# =============================================================================


class ErrorRecordProtocol(Protocol):
    """에러 기록 프로토콜 (duck typing)."""

    raw_duration_minutes: float
    recorded_at: datetime


# =============================================================================
# Backfill Period
# =============================================================================


@dataclass
class BackfillPeriod:
    """
    소급 적용 대상 기간.

    장애 시작 추정 시점부터 Emergency 선포 시점까지의 기간을 정의합니다.

    Attributes:
        emergency_id: 관련 Emergency ID
        detected_start_time: 장애 시작 추정 시점
        declared_at: LEVEL_3 선포 시점
        target_level: 적용할 Emergency Level
        backfill_multiplier: 소급 적용할 가중치
    """

    emergency_id: str
    """관련 Emergency ID."""

    detected_start_time: datetime
    """장애 시작 추정 시점."""

    declared_at: datetime
    """LEVEL_3 선포 시점."""

    target_level: EmergencyLevel
    """적용할 Emergency Level."""

    backfill_multiplier: float = 5.0
    """소급 적용할 가중치."""

    def get_duration_minutes(self) -> float:
        """소급 기간 (분)."""
        delta = self.declared_at - self.detected_start_time
        return delta.total_seconds() / 60.0

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "emergency_id": self.emergency_id,
            "detected_start_time": self.detected_start_time.isoformat(),
            "declared_at": self.declared_at.isoformat(),
            "target_level": self.target_level.name,
            "backfill_multiplier": self.backfill_multiplier,
            "duration_minutes": self.get_duration_minutes(),
        }


# =============================================================================
# Backfill Result
# =============================================================================


@dataclass
class BackfillResult:
    """
    소급 적용 결과.

    소급 계산의 결과를 담습니다.

    Attributes:
        period: 대상 기간
        errors_affected: 영향 받은 에러 수
        original_consumption_minutes: 원래 소진량 (분)
        adjusted_consumption_minutes: 조정된 소진량 (분)
        adjustment_delta_minutes: 추가 소진량 (분)
        calculated_at: 계산 시각
        backfill_id: 소급 작업 ID
    """

    period: BackfillPeriod
    """대상 기간."""

    errors_affected: int = 0
    """영향 받은 에러 수."""

    original_consumption_minutes: float = 0.0
    """원래 소진량 (분)."""

    adjusted_consumption_minutes: float = 0.0
    """조정된 소진량 (분)."""

    adjustment_delta_minutes: float = 0.0
    """추가 소진량 (분)."""

    calculated_at: datetime = field(default_factory=utc_now)
    """계산 시각."""

    backfill_id: str = field(default_factory=lambda: f"bf_{uuid.uuid4().hex[:12]}")
    """소급 작업 ID."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "backfill_id": self.backfill_id,
            "period": self.period.to_dict(),
            "errors_affected": self.errors_affected,
            "original_consumption_minutes": self.original_consumption_minutes,
            "adjusted_consumption_minutes": self.adjusted_consumption_minutes,
            "adjustment_delta_minutes": self.adjustment_delta_minutes,
            "calculated_at": self.calculated_at.isoformat(),
        }

    def to_hash_chain_entry(self) -> dict[str, Any]:
        """Hash Chain 엔트리 변환."""
        return {
            "type": "budget_backfill",
            "backfill_id": self.backfill_id,
            "emergency_id": self.period.emergency_id,
            "detected_start": self.period.detected_start_time.isoformat(),
            "declared_at": self.period.declared_at.isoformat(),
            "multiplier_applied": self.period.backfill_multiplier,
            "errors_affected": self.errors_affected,
            "adjustment_delta_minutes": self.adjustment_delta_minutes,
            "calculated_at": self.calculated_at.isoformat(),
        }


# =============================================================================
# Emergency Backfill Calculator
# =============================================================================


class EmergencyBackfillCalculator:
    """
    사후 가중치 소급 계산기.

    Emergency 선포 전 발생한 에러에 대해 소급하여 가중치를 적용합니다.
    장애 발생 시점과 감지 시점 사이의 시차를 보정합니다.

    Features:
    - 장애 시작 시점 추정 (메트릭 기반 2-sigma)
    - 소급 기간 내 에러 조회 및 재계산
    - Hash Chain에 소급 기록 추가
    - 최대 소급 시간 제한 (안전)

    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.1
    """

    MAX_BACKFILL_HOURS: int = 2
    """최대 소급 시간 (2시간)."""

    DEFAULT_LOOKBACK_MINUTES: int = 30
    """기본 소급 시간 (메트릭 없을 때)."""

    def __init__(
        self,
        get_error_records: Callable[..., list[Any]] | None = None,
        get_metrics_history: Callable[..., list[dict[str, Any]]] | None = None,
        hash_chain_manager: Any | None = None,
        level_multipliers: dict[EmergencyLevel, float] | None = None,
    ):
        """
        EmergencyBackfillCalculator 초기화.

        Args:
            get_error_records: 기간 내 에러 기록 조회 함수
            get_metrics_history: 메트릭 히스토리 조회 함수
            hash_chain_manager: 무결성 체인 관리자
            level_multipliers: 레벨별 가중치 맵
        """
        self._get_error_records = get_error_records
        self._get_metrics_history = get_metrics_history
        self._hash_chain_manager = hash_chain_manager
        self._level_multipliers = level_multipliers or dict(DEFAULT_LEVEL_MULTIPLIERS)

    def estimate_incident_start(
        self,
        declared_at: datetime,
        namespace: str | None = None,
    ) -> datetime:
        """
        장애 시작 시점 추정.

        메트릭 히스토리를 분석하여 에러율이 급등하기 시작한 시점을 찾습니다.
        2-sigma 이상 증가한 첫 시점을 장애 시작으로 추정합니다.

        Args:
            declared_at: LEVEL_3 선포 시점
            namespace: 네임스페이스

        Returns:
            장애 시작 추정 시점
        """
        # 최대 소급 시간 제한
        max_lookback = declared_at - timedelta(hours=self.MAX_BACKFILL_HOURS)

        if self._get_metrics_history:
            try:
                metrics = self._get_metrics_history(
                    start=max_lookback,
                    end=declared_at,
                    namespace=namespace,
                )

                spike_time = self._find_error_rate_spike(metrics)
                if spike_time:
                    return max(spike_time, max_lookback)

            except Exception as e:
                logger.warning(
                    "backfill.metrics_lookup_failed",
                    error=e,
                )

        # 폴백: 기본 소급 시간
        return declared_at - timedelta(minutes=self.DEFAULT_LOOKBACK_MINUTES)

    def calculate_backfill(
        self,
        emergency_id: str,
        declared_at: datetime,
        target_level: EmergencyLevel,
        namespace: str | None = None,
        detected_start_time: datetime | None = None,
    ) -> BackfillResult:
        """
        소급 가중치 계산.

        Args:
            emergency_id: Emergency ID
            declared_at: LEVEL_3 선포 시점
            target_level: 적용할 레벨
            namespace: 네임스페이스
            detected_start_time: 장애 시작 시점 (None이면 자동 추정)

        Returns:
            BackfillResult: 소급 계산 결과
        """
        # 장애 시작 시점 결정
        start_time = detected_start_time or self.estimate_incident_start(
            declared_at=declared_at,
            namespace=namespace,
        )

        # 가중치 결정
        backfill_multiplier = self._level_multipliers.get(target_level, 5.0)

        # 소급 기간 정의
        period = BackfillPeriod(
            emergency_id=emergency_id,
            detected_start_time=start_time,
            declared_at=declared_at,
            target_level=target_level,
            backfill_multiplier=backfill_multiplier,
        )

        # 기간 내 에러 조회
        errors_affected = 0
        original_consumption = 0.0

        if self._get_error_records:
            try:
                records = self._get_error_records(
                    start=start_time,
                    end=declared_at,
                    namespace=namespace,
                )
                errors_affected = len(records) if records else 0
                original_consumption = sum(
                    getattr(r, "raw_duration_minutes", 1.0) for r in (records or [])
                )
            except Exception as e:
                logger.warning(
                    "backfill.error_records_lookup_failed",
                    error=e,
                )

        # 조정된 소진량 계산
        adjusted_consumption = original_consumption * backfill_multiplier
        adjustment_delta = adjusted_consumption - original_consumption

        result = BackfillResult(
            period=period,
            errors_affected=errors_affected,
            original_consumption_minutes=original_consumption,
            adjusted_consumption_minutes=adjusted_consumption,
            adjustment_delta_minutes=adjustment_delta,
        )

        # Hash Chain에 기록
        self._record_to_hash_chain(result)

        logger.info(
            f"[Backfill] Calculated: emergency_id={emergency_id}, "
            f"errors={errors_affected}, delta={adjustment_delta:.2f}min, "
            f"multiplier={backfill_multiplier}x"
        )

        return result

    def _find_error_rate_spike(
        self,
        metrics: list[dict[str, Any]],
    ) -> datetime | None:
        """
        에러율 급등 시점 찾기 (2-sigma 기준).

        Args:
            metrics: 메트릭 목록 (error_rate, timestamp 포함)

        Returns:
            급등 시점 (없으면 None)
        """
        if not metrics or len(metrics) < 10:
            return None

        error_rates = [m.get("error_rate", 0) for m in metrics]

        try:
            mean = statistics.mean(error_rates)
            stdev = statistics.stdev(error_rates) if len(error_rates) > 1 else 0
            threshold = mean + (2 * stdev)

            for m in metrics:
                if m.get("error_rate", 0) > threshold:
                    timestamp = m.get("timestamp")
                    if isinstance(timestamp, datetime):
                        return timestamp
                    elif isinstance(timestamp, str):
                        return datetime.fromisoformat(timestamp)
        except Exception:
            pass

        return None

    def _record_to_hash_chain(self, result: BackfillResult) -> None:
        """Hash Chain에 소급 기록 추가."""
        if self._hash_chain_manager:
            try:
                entry = result.to_hash_chain_entry()
                self._hash_chain_manager.add_entry(entry)
            except Exception as e:
                logger.warning(
                    "backfill.hash_chain_recording_failed",
                    error=e,
                )


# =============================================================================
# Singleton
# =============================================================================

_backfill_calculator: EmergencyBackfillCalculator | None = None


def get_emergency_backfill_calculator() -> EmergencyBackfillCalculator:
    """EmergencyBackfillCalculator 싱글톤 반환."""
    global _backfill_calculator
    if _backfill_calculator is None:
        _backfill_calculator = EmergencyBackfillCalculator()
    return _backfill_calculator


def reset_backfill_calculator() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _backfill_calculator
    _backfill_calculator = None
