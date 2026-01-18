"""
FailSafePeriodTracker 테스트.
"""

import pytest


class TestFailSafePeriodTracker:
    """FailSafePeriodTracker 테스트."""

    def test_start_period(self, period_tracker):
        """Fail-Safe 기간 시작 테스트."""
        period = period_tracker.start_period(
            reason="Redis timeout",
            component="error_budget_gate",
        )
        
        assert period is not None
        assert period.is_active is True
        assert period.trigger_reason == "Redis timeout"
        
    def test_end_period(self, period_tracker):
        """Fail-Safe 기간 종료 테스트."""
        period_tracker.start_period(reason="Test")
        ended = period_tracker.end_period()
        
        assert ended is not None
        assert ended.is_active is False
        assert ended.ended_at is not None

    def test_end_period_when_no_active(self, period_tracker):
        """활성 기간 없을 때 종료 시도."""
        result = period_tracker.end_period()
        assert result is None

    def test_auto_end_previous_on_new_start(self, period_tracker):
        """새 기간 시작 시 기존 기간 자동 종료."""
        period1 = period_tracker.start_period(reason="First")
        period2 = period_tracker.start_period(reason="Second")
        
        assert period1.is_active is False
        assert period2.is_active is True

    def test_record_fail_open(self, period_tracker):
        """Fail-Open 카운트 기록."""
        period_tracker.start_period(reason="Test")
        period_tracker.record_fail_open()
        period_tracker.record_fail_open()
        period_tracker.record_fail_open()
        
        active = period_tracker.get_active_period()
        assert active.fail_open_count == 3

    def test_record_rate_limit_exceeded(self, period_tracker):
        """Rate Limit 초과 카운트 기록."""
        period_tracker.start_period(reason="Test")
        period_tracker.record_rate_limit_exceeded()
        period_tracker.record_rate_limit_exceeded()
        
        active = period_tracker.get_active_period()
        assert active.rate_limit_exceeded_count == 2

    def test_get_unreconciled_periods(self, period_tracker):
        """Reconciliation 대상 기간 조회."""
        # 3개 기간 생성 후 종료
        period_tracker.start_period(reason="First")
        period_tracker.end_period()
        period_tracker.start_period(reason="Second")
        period_tracker.end_period()
        period_tracker.start_period(reason="Third")  # 활성 상태
        
        unreconciled = period_tracker.get_unreconciled_periods()
        
        assert len(unreconciled) == 2  # 종료된 2개만

    def test_max_periods_limit(self, period_tracker):
        """최대 기간 수 제한 테스트."""
        # 15개 기간 생성 (max_periods=10)
        for i in range(15):
            period_tracker.start_period(reason=f"Period-{i}")
            period_tracker.end_period()
        
        all_periods = period_tracker.get_all_periods(limit=100)
        assert len(all_periods) == 10

    def test_get_status(self, period_tracker):
        """상태 조회 테스트."""
        period_tracker.start_period(reason="Test")
        status = period_tracker.get_status()
        
        assert "active_period" in status
        assert "total_periods" in status
        assert status["active_period"] is not None
