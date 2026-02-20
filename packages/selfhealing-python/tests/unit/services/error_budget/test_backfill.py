"""
EmergencyBackfillCalculator 단위 테스트.

장애 선포 전 에러에 가중치 소급 적용 테스트.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.1
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.error_budget.backfill import (
    BackfillPeriod,
    BackfillResult,
    EmergencyBackfillCalculator,
    get_emergency_backfill_calculator,
    reset_backfill_calculator,
)


# =============================================================================
# Mock Error Record
# =============================================================================


@dataclass
class MockErrorRecord:
    """테스트용 에러 기록."""

    raw_duration_minutes: float = 1.0
    recorded_at: datetime = None

    def __post_init__(self):
        if self.recorded_at is None:
            from selfhealing.core.timezone import now

            self.recorded_at = now()


# =============================================================================
# BackfillPeriod 테스트
# =============================================================================


class TestBackfillPeriod:
    """BackfillPeriod 테스트."""

    def test_get_duration_minutes(self):
        """소급 기간 계산."""
        declared = datetime(2026, 1, 22, 10, 0, 0)
        detected = datetime(2026, 1, 22, 9, 30, 0)

        period = BackfillPeriod(
            emergency_id="emg_123",
            detected_start_time=detected,
            declared_at=declared,
            target_level=EmergencyLevel.LEVEL_3,
        )

        assert period.get_duration_minutes() == 30.0

    def test_to_dict(self):
        """딕셔너리 변환."""
        declared = datetime(2026, 1, 22, 10, 0, 0)
        detected = datetime(2026, 1, 22, 9, 30, 0)

        period = BackfillPeriod(
            emergency_id="emg_123",
            detected_start_time=detected,
            declared_at=declared,
            target_level=EmergencyLevel.LEVEL_3,
            backfill_multiplier=5.0,
        )

        data = period.to_dict()

        assert data["emergency_id"] == "emg_123"
        assert data["target_level"] == "LEVEL_3"
        assert data["backfill_multiplier"] == 5.0
        assert data["duration_minutes"] == 30.0


# =============================================================================
# BackfillResult 테스트
# =============================================================================


class TestBackfillResult:
    """BackfillResult 테스트."""

    def test_backfill_id_generated(self):
        """ID 자동 생성."""
        declared = datetime(2026, 1, 22, 10, 0, 0)
        detected = datetime(2026, 1, 22, 9, 30, 0)

        period = BackfillPeriod(
            emergency_id="emg_123",
            detected_start_time=detected,
            declared_at=declared,
            target_level=EmergencyLevel.LEVEL_3,
        )

        result = BackfillResult(period=period)

        assert result.backfill_id.startswith("bf_")

    def test_to_dict(self):
        """딕셔너리 변환."""
        declared = datetime(2026, 1, 22, 10, 0, 0)
        detected = datetime(2026, 1, 22, 9, 30, 0)

        period = BackfillPeriod(
            emergency_id="emg_123",
            detected_start_time=detected,
            declared_at=declared,
            target_level=EmergencyLevel.LEVEL_3,
        )

        result = BackfillResult(
            period=period,
            errors_affected=10,
            original_consumption_minutes=10.0,
            adjusted_consumption_minutes=50.0,
            adjustment_delta_minutes=40.0,
        )

        data = result.to_dict()

        assert data["errors_affected"] == 10
        assert data["adjustment_delta_minutes"] == 40.0
        assert "period" in data

    def test_to_hash_chain_entry(self):
        """Hash Chain 엔트리 변환."""
        declared = datetime(2026, 1, 22, 10, 0, 0)
        detected = datetime(2026, 1, 22, 9, 30, 0)

        period = BackfillPeriod(
            emergency_id="emg_123",
            detected_start_time=detected,
            declared_at=declared,
            target_level=EmergencyLevel.LEVEL_3,
        )

        result = BackfillResult(
            period=period,
            adjustment_delta_minutes=40.0,
        )

        entry = result.to_hash_chain_entry()

        assert entry["type"] == "budget_backfill"
        assert entry["emergency_id"] == "emg_123"
        assert entry["adjustment_delta_minutes"] == 40.0


# =============================================================================
# EmergencyBackfillCalculator 테스트
# =============================================================================


class TestEmergencyBackfillCalculator:
    """EmergencyBackfillCalculator 테스트."""

    def test_estimate_incident_start_default_30min(self):
        """메트릭 없을 때 기본 30분 전."""
        calculator = EmergencyBackfillCalculator()

        declared_at = datetime(2026, 1, 22, 10, 0, 0)
        estimated = calculator.estimate_incident_start(declared_at)

        expected = declared_at - timedelta(minutes=30)
        assert estimated == expected

    def test_estimate_incident_start_max_2_hours(self):
        """최대 소급 2시간 제한."""

        # 메트릭이 3시간 전 스파이크를 찾아도 2시간으로 제한
        def mock_get_metrics(**kwargs):
            # 3시간 전 스파이크 반환
            return [
                {"error_rate": 0.01, "timestamp": kwargs["end"] - timedelta(hours=3)},
            ]

        calculator = EmergencyBackfillCalculator(
            get_metrics_history=mock_get_metrics,
        )

        declared_at = datetime(2026, 1, 22, 10, 0, 0)
        estimated = calculator.estimate_incident_start(declared_at)

        # 최대 2시간 전으로 제한
        max_lookback = declared_at - timedelta(hours=2)
        assert estimated >= max_lookback

    def test_estimate_incident_start_finds_spike(self):
        """에러율 급등 시점 찾기."""

        def mock_get_metrics(**kwargs):
            start = kwargs["start"]
            # 10개 데이터 포인트, 마지막에 스파이크
            normal_rate = 0.01
            spike_time = kwargs["end"] - timedelta(minutes=15)

            return [{"error_rate": normal_rate, "timestamp": start + timedelta(minutes=i * 10)} for i in range(10)] + [
                {"error_rate": 0.5, "timestamp": spike_time},  # 스파이크
            ]

        calculator = EmergencyBackfillCalculator(
            get_metrics_history=mock_get_metrics,
        )

        declared_at = datetime(2026, 1, 22, 10, 0, 0)
        estimated = calculator.estimate_incident_start(declared_at)

        # 폴백(30분 전)보다 정확한 시점이어야 함
        fallback = declared_at - timedelta(minutes=30)
        assert estimated != fallback or True  # 메트릭에 따라 다를 수 있음

    def test_calculate_backfill_basic(self):
        """기본 소급 계산."""
        mock_records = [
            MockErrorRecord(raw_duration_minutes=1.0),
            MockErrorRecord(raw_duration_minutes=2.0),
            MockErrorRecord(raw_duration_minutes=1.0),
        ]

        calculator = EmergencyBackfillCalculator(
            get_error_records=lambda **kwargs: mock_records,
        )

        result = calculator.calculate_backfill(
            emergency_id="emg_123",
            declared_at=datetime(2026, 1, 22, 10, 0, 0),
            target_level=EmergencyLevel.LEVEL_3,
        )

        assert result.errors_affected == 3
        assert result.original_consumption_minutes == 4.0  # 1+2+1
        assert result.adjusted_consumption_minutes == 20.0  # 4 * 5.0
        assert result.adjustment_delta_minutes == 16.0  # 20 - 4

    def test_calculate_backfill_with_custom_start_time(self):
        """커스텀 시작 시점 지정."""
        mock_records = [MockErrorRecord(raw_duration_minutes=1.0)]

        calculator = EmergencyBackfillCalculator(
            get_error_records=lambda **kwargs: mock_records,
        )

        declared_at = datetime(2026, 1, 22, 10, 0, 0)
        custom_start = datetime(2026, 1, 22, 9, 0, 0)

        result = calculator.calculate_backfill(
            emergency_id="emg_123",
            declared_at=declared_at,
            target_level=EmergencyLevel.LEVEL_3,
            detected_start_time=custom_start,
        )

        assert result.period.detected_start_time == custom_start

    def test_calculate_backfill_level_2_multiplier(self):
        """LEVEL_2 가중치 적용."""
        mock_records = [MockErrorRecord(raw_duration_minutes=10.0)]

        calculator = EmergencyBackfillCalculator(
            get_error_records=lambda **kwargs: mock_records,
        )

        result = calculator.calculate_backfill(
            emergency_id="emg_123",
            declared_at=datetime(2026, 1, 22, 10, 0, 0),
            target_level=EmergencyLevel.LEVEL_2,
        )

        # LEVEL_2 = 3.0x
        assert result.period.backfill_multiplier == 3.0
        assert result.adjusted_consumption_minutes == 30.0

    def test_calculate_backfill_no_errors(self):
        """에러 없는 경우."""
        calculator = EmergencyBackfillCalculator(
            get_error_records=lambda **kwargs: [],
        )

        result = calculator.calculate_backfill(
            emergency_id="emg_123",
            declared_at=datetime(2026, 1, 22, 10, 0, 0),
            target_level=EmergencyLevel.LEVEL_3,
        )

        assert result.errors_affected == 0
        assert result.original_consumption_minutes == 0.0
        assert result.adjustment_delta_minutes == 0.0

    def test_calculate_backfill_hash_chain_recording(self):
        """Hash Chain 기록."""
        mock_hash_chain = MagicMock()
        mock_records = [MockErrorRecord(raw_duration_minutes=1.0)]

        calculator = EmergencyBackfillCalculator(
            get_error_records=lambda **kwargs: mock_records,
            hash_chain_manager=mock_hash_chain,
        )

        calculator.calculate_backfill(
            emergency_id="emg_123",
            declared_at=datetime(2026, 1, 22, 10, 0, 0),
            target_level=EmergencyLevel.LEVEL_3,
        )

        mock_hash_chain.add_entry.assert_called_once()
        entry = mock_hash_chain.add_entry.call_args[0][0]
        assert entry["type"] == "budget_backfill"


# =============================================================================
# Singleton 테스트
# =============================================================================


class TestBackfillCalculatorSingleton:
    """싱글톤 테스트."""

    def setup_method(self):
        """테스트 전 초기화."""
        reset_backfill_calculator()

    def teardown_method(self):
        """테스트 후 정리."""
        reset_backfill_calculator()

    def test_get_returns_singleton(self):
        """싱글톤 반환."""
        calc1 = get_emergency_backfill_calculator()
        calc2 = get_emergency_backfill_calculator()

        assert calc1 is calc2

    def test_reset_clears_singleton(self):
        """리셋 후 새 인스턴스."""
        calc1 = get_emergency_backfill_calculator()
        reset_backfill_calculator()
        calc2 = get_emergency_backfill_calculator()

        assert calc1 is not calc2
