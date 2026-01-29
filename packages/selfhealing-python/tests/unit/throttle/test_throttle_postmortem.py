"""
Throttle Postmortem 연동 테스트.

테스트 대상:
1. ThrottleLimitChange 데이터클래스
2. ThrottlePostmortemData 데이터클래스
3. ThrottleLimitHistoryCollector 싱글톤
4. get_throttle_history_collector() 함수
5. collect_throttle_postmortem_data() 함수
6. _record_limit_history() 헬퍼 함수
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


class TestThrottleLimitChangeDataclass:
    """ThrottleLimitChange 데이터클래스 테스트."""

    def test_create_limit_change(self):
        """ThrottleLimitChange 생성 테스트."""
        from selfhealing.services.throttle.postmortem import ThrottleLimitChange

        now = datetime.now(timezone.utc)
        change = ThrottleLimitChange(
            timestamp=now,
            previous_limit=100,
            new_limit=80,
            reason="emergency_sync",
            trigger_source="emergency_mode",
        )

        assert change.previous_limit == 100
        assert change.new_limit == 80
        assert change.reason == "emergency_sync"
        assert change.trigger_source == "emergency_mode"

    def test_limit_change_optional_trigger_source(self):
        """ThrottleLimitChange trigger_source 옵션 테스트."""
        from selfhealing.services.throttle.postmortem import ThrottleLimitChange

        now = datetime.now(timezone.utc)
        change = ThrottleLimitChange(
            timestamp=now,
            previous_limit=100,
            new_limit=80,
            reason="gradient",
        )

        assert change.trigger_source is None


class TestThrottlePostmortemDataDataclass:
    """ThrottlePostmortemData 데이터클래스 테스트."""

    def test_create_postmortem_data(self):
        """ThrottlePostmortemData 생성 테스트."""
        from selfhealing.services.throttle.postmortem import ThrottlePostmortemData

        data = ThrottlePostmortemData(
            throttle_limit_history=[],
            throttle_min_limit=50,
            throttle_max_limit=100,
            throttle_adjustment_count=5,
            throttle_emergency_adjustments=1,
            throttle_cb_adjustments=2,
        )

        assert data.throttle_min_limit == 50
        assert data.throttle_max_limit == 100
        assert data.throttle_adjustment_count == 5
        assert data.throttle_emergency_adjustments == 1
        assert data.throttle_cb_adjustments == 2

    def test_postmortem_data_to_dict(self):
        """ThrottlePostmortemData to_dict() 테스트."""
        from selfhealing.services.throttle.postmortem import ThrottlePostmortemData

        data = ThrottlePostmortemData(
            throttle_limit_history=[{"test": "value"}],
            throttle_min_limit=80,
            throttle_max_limit=100,
            throttle_adjustment_count=1,
            throttle_emergency_adjustments=0,
            throttle_cb_adjustments=0,
        )

        result = data.to_dict()

        assert isinstance(result, dict)
        assert len(result["throttle_limit_history"]) == 1
        assert result["throttle_min_limit"] == 80

    def test_postmortem_data_default_values(self):
        """ThrottlePostmortemData 기본값 테스트."""
        from selfhealing.services.throttle.postmortem import ThrottlePostmortemData

        data = ThrottlePostmortemData()

        assert data.throttle_limit_history == []
        assert data.throttle_min_limit is None
        assert data.throttle_adjustment_count == 0


class TestThrottleLimitHistoryCollector:
    """ThrottleLimitHistoryCollector 싱글톤 테스트."""

    def test_get_throttle_history_collector_singleton(self):
        """싱글톤 인스턴스 테스트."""
        from selfhealing.services.throttle.postmortem import get_throttle_history_collector

        instance1 = get_throttle_history_collector()
        instance2 = get_throttle_history_collector()

        assert instance1 is instance2

    def test_record_limit_change(self):
        """한도 변경 기록 테스트."""
        from selfhealing.services.throttle.postmortem import get_throttle_history_collector

        collector = get_throttle_history_collector()
        collector.reset()  # 이전 테스트 데이터 정리

        collector.record_limit_change(
            previous_limit=100,
            new_limit=80,
            reason="test_reason",
            trigger_source="gradient",
        )

        history = collector.get_history_for_period()
        assert len(history) >= 1

    def test_get_history_for_period(self):
        """기간 지정 히스토리 조회 테스트."""
        from selfhealing.services.throttle.postmortem import get_throttle_history_collector

        collector = get_throttle_history_collector()
        collector.reset()

        start_time = datetime.now(timezone.utc)

        collector.record_limit_change(
            previous_limit=100,
            new_limit=80,
            reason="test",
        )

        history = collector.get_history_for_period(start_time=start_time)

        assert len(history) >= 1

    def test_reset_history(self):
        """히스토리 초기화 테스트."""
        from selfhealing.services.throttle.postmortem import get_throttle_history_collector

        collector = get_throttle_history_collector()

        collector.record_limit_change(
            previous_limit=100,
            new_limit=80,
            reason="test",
        )

        collector.reset()

        history = collector.get_history_for_period()
        assert len(history) == 0

    def test_emergency_adjustment_counter(self):
        """Emergency 조정 카운터 테스트."""
        from selfhealing.services.throttle.postmortem import get_throttle_history_collector

        collector = get_throttle_history_collector()
        collector.reset()

        collector.record_limit_change(
            previous_limit=100,
            new_limit=50,
            reason="emergency",
            trigger_source="emergency_mode",
        )
        collector.record_limit_change(
            previous_limit=50,
            new_limit=0,
            reason="cb",
            trigger_source="circuit_breaker",
        )

        data = collector.get_postmortem_data()

        assert data.throttle_emergency_adjustments >= 1
        assert data.throttle_cb_adjustments >= 1


class TestGetPostmortemData:
    """get_postmortem_data() 메서드 테스트."""

    def test_get_postmortem_data_empty(self):
        """빈 히스토리 데이터 수집 테스트."""
        from selfhealing.services.throttle.postmortem import get_throttle_history_collector

        collector = get_throttle_history_collector()
        collector.reset()

        data = collector.get_postmortem_data()

        assert data is not None
        assert len(data.throttle_limit_history) == 0

    def test_get_postmortem_data_with_history(self):
        """히스토리 데이터 수집 테스트."""
        from selfhealing.services.throttle.postmortem import get_throttle_history_collector

        collector = get_throttle_history_collector()
        collector.reset()

        collector.record_limit_change(100, 80, reason="gradient")
        collector.record_limit_change(80, 60, reason="emergency", trigger_source="emergency_mode")
        collector.record_limit_change(60, 0, reason="cb", trigger_source="circuit_breaker")

        data = collector.get_postmortem_data()

        assert len(data.throttle_limit_history) == 3
        assert data.throttle_adjustment_count == 3
        assert data.throttle_min_limit == 0
        assert data.throttle_max_limit == 80


class TestCollectThrottlePostmortemData:
    """collect_throttle_postmortem_data() 함수 테스트."""

    def test_collect_returns_dict(self):
        """딕셔너리 반환 테스트."""
        from selfhealing.services.throttle.postmortem import (
            collect_throttle_postmortem_data,
            get_throttle_history_collector,
        )

        collector = get_throttle_history_collector()
        collector.reset()

        result = collect_throttle_postmortem_data()

        assert isinstance(result, dict)

    def test_collect_with_time_range(self):
        """시간 범위 지정 테스트."""
        from selfhealing.services.throttle.postmortem import (
            collect_throttle_postmortem_data,
            get_throttle_history_collector,
        )

        collector = get_throttle_history_collector()
        collector.reset()

        start_time = datetime.now(timezone.utc)

        collector.record_limit_change(100, 80, reason="test")

        result = collect_throttle_postmortem_data(start_time=start_time)

        assert isinstance(result, dict)
        assert "throttle_limit_history" in result


class TestRecordLimitHistoryHelper:
    """_record_limit_history() 헬퍼 함수 테스트."""

    def test_record_limit_history_success(self):
        """정상적인 히스토리 기록 테스트."""
        from selfhealing.services.throttle.adaptive import _record_limit_history
        from selfhealing.services.throttle.postmortem import get_throttle_history_collector

        collector = get_throttle_history_collector()
        collector.reset()

        _record_limit_history(
            previous_limit=100,
            new_limit=80,
            reason="gradient",
        )

        history = collector.get_history_for_period()
        assert len(history) >= 1

    def test_record_limit_history_fail_open(self):
        """히스토리 기록 실패 시 Fail-Open 테스트."""
        from selfhealing.services.throttle.adaptive import _record_limit_history

        with patch(
            "selfhealing.services.throttle.postmortem.get_throttle_history_collector",
            side_effect=Exception("Test error"),
        ):
            # 예외가 전파되지 않음
            _record_limit_history(
                previous_limit=100,
                new_limit=80,
                reason="test",
            )
