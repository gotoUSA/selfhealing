"""
Tests for ML Bulkhead Priority Watermark — should_admit_by_priority().

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: ML_PRIORITY_WATERMARKS 상수값, STARVATION_RELIEF_SECONDS 값 검증
- Behavior: should_admit_by_priority()의 입장 결정 로직 검증

참조 소스:
- services/correlation_engine/service.py
  (ML_PRIORITY_WATERMARKS, STARVATION_RELIEF_SECONDS,
   CorrelationEngineService.should_admit_by_priority)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.correlation_engine.service import (
    ML_PRIORITY_WATERMARKS,
    STARVATION_RELIEF_SECONDS,
    CorrelationEngineService,
)


# =============================================================================
# 상수 계약 검증
# =============================================================================


class TestMLPriorityWatermarksContract:
    """ML_PRIORITY_WATERMARKS 설계 계약값 검증."""

    def test_critical_watermark_is_zero(self):
        """critical 우선순위는 watermark 0.0 (항상 처리)."""
        assert ML_PRIORITY_WATERMARKS["critical"] == 0.0

    def test_standard_watermark(self):
        """standard 우선순위는 watermark 0.4."""
        assert ML_PRIORITY_WATERMARKS["standard"] == 0.4

    def test_background_watermark(self):
        """background 우선순위는 watermark 0.7."""
        assert ML_PRIORITY_WATERMARKS["background"] == 0.7

    def test_three_priority_levels(self):
        """정확히 3단계 우선순위가 정의되어 있다."""
        assert len(ML_PRIORITY_WATERMARKS) == 3

    def test_starvation_relief_seconds(self):
        """기아 방지 타임아웃은 300.0초."""
        assert STARVATION_RELIEF_SECONDS == 300.0


# =============================================================================
# should_admit_by_priority 동작 검증
# =============================================================================


class TestShouldAdmitByPriorityBehavior:
    """should_admit_by_priority() 동작 검증."""

    def _make_service_with_mock_bulkhead(
        self,
        active_count: int,
        max_workers: int,
    ) -> CorrelationEngineService:
        """Mock bulkhead를 가진 CorrelationEngineService 생성."""
        mock_bulkhead = MagicMock()
        mock_bulkhead._active_count = active_count
        mock_bulkhead._max_workers = max_workers

        service = object.__new__(CorrelationEngineService)
        service._get_ml_bulkhead = MagicMock(return_value=mock_bulkhead)
        return service

    def test_critical_always_admitted(self):
        """critical 우선순위는 점유율 무관하게 항상 입장 허용."""
        service = self._make_service_with_mock_bulkhead(
            active_count=9,
            max_workers=10,
        )
        # utilization=0.9, watermark=0.0 → 1.0 - 0.0 = 1.0 → 0.9 < 1.0 → 허용
        assert service.should_admit_by_priority("critical") is True

    def test_standard_admitted_when_utilization_low(self):
        """standard 우선순위 — 점유율이 낮으면 입장 허용."""
        service = self._make_service_with_mock_bulkhead(
            active_count=3,
            max_workers=10,
        )
        # utilization=0.3, threshold=1.0-0.4=0.6 → 0.3 < 0.6 → 허용
        assert service.should_admit_by_priority("standard") is True

    def test_standard_denied_when_utilization_high(self):
        """standard 우선순위 — 점유율이 높으면 입장 거부."""
        service = self._make_service_with_mock_bulkhead(
            active_count=7,
            max_workers=10,
        )
        # utilization=0.7, threshold=1.0-0.4=0.6 → 0.7 >= 0.6 → 거부
        assert service.should_admit_by_priority("standard") is False

    def test_background_denied_at_moderate_utilization(self):
        """background 우선순위 — 중간 점유율에서도 거부."""
        service = self._make_service_with_mock_bulkhead(
            active_count=4,
            max_workers=10,
        )
        # utilization=0.4, threshold=1.0-0.7=0.3 → 0.4 >= 0.3 → 거부
        assert service.should_admit_by_priority("background") is False

    def test_background_admitted_when_utilization_very_low(self):
        """background 우선순위 — 매우 낮은 점유율에서 입장 허용."""
        service = self._make_service_with_mock_bulkhead(
            active_count=2,
            max_workers=10,
        )
        # utilization=0.2, threshold=1.0-0.7=0.3 → 0.2 < 0.3 → 허용
        assert service.should_admit_by_priority("background") is True

    def test_unknown_priority_uses_default_watermark(self):
        """알 수 없는 우선순위는 기본 watermark 0.5를 사용한다."""
        service = self._make_service_with_mock_bulkhead(
            active_count=6,
            max_workers=10,
        )
        # utilization=0.6, watermark=0.5 → threshold=1.0-0.5=0.5 → 0.6 >= 0.5 → 거부
        assert service.should_admit_by_priority("unknown_level") is False

    def test_default_priority_is_standard(self):
        """기본 호출 (인자 없음) 시 standard 우선순위 사용."""
        service = self._make_service_with_mock_bulkhead(
            active_count=3,
            max_workers=10,
        )
        # utilization=0.3, standard watermark 0.4 → threshold=0.6 → 0.3 < 0.6 → 허용
        assert service.should_admit_by_priority() is True

    def test_zero_max_workers_returns_false(self):
        """max_workers=0이면 utilization=1.0으로 계산 → 거부."""
        service = self._make_service_with_mock_bulkhead(
            active_count=0,
            max_workers=0,
        )
        assert service.should_admit_by_priority("standard") is False
