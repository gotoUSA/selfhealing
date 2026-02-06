"""
_maybe_adjust_limit() SLA Prometheus 메트릭 기록 단위 테스트.

SLA critical/warning RTT 발생 시 _record_throttle_metrics()가
sla_event, limit_change_direction, limit_change_trigger 파라미터와 함께
호출되는지 검증합니다.
"""

from __future__ import annotations

from unittest.mock import patch, call

import pytest

from selfhealing.services.throttle.config import ThrottleConfig
from selfhealing.services.throttle.adaptive import AdaptiveThrottle


# =============================================================================
# SLA Critical 경로 메트릭 테스트
# =============================================================================


class TestMaybeAdjustLimitSLACriticalMetrics:
    """_maybe_adjust_limit()에서 SLA critical RTT 시 Prometheus 메트릭 기록 테스트."""

    def _make_throttle(self, sla_critical_ms: int = 500, sla_warning_ms: int = 200) -> AdaptiveThrottle:
        """테스트용 AdaptiveThrottle 인스턴스 생성."""
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=1000,
            sla_critical_ms=sla_critical_ms,
            sla_warning_ms=sla_warning_ms,
            sample_interval_ms=0,  # 조정 간격 제거 (즉시 반응)
        )
        return AdaptiveThrottle(config=config)

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_sla_critical_records_sla_event_metric(self, mock_emit, mock_record):
        """SLA critical RTT 시 sla_event='critical' 메트릭이 기록되는지 확인."""
        throttle = self._make_throttle(sla_critical_ms=500)

        # 첫 호출로 _last_adjustment_time 초기화
        throttle._maybe_adjust_limit(600.0)

        # sla_event="critical"이 포함된 호출이 있는지 확인
        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "critical"]
        assert len(sla_calls) >= 1, (
            "_record_throttle_metrics(sla_event='critical')이 호출되지 않음. " f"실제 호출: {mock_record.call_args_list}"
        )

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_sla_critical_records_limit_change_direction_down(self, mock_emit, mock_record):
        """SLA critical RTT 시 limit_change_direction='down' 메트릭이 기록되는지 확인."""
        throttle = self._make_throttle(sla_critical_ms=500)

        throttle._maybe_adjust_limit(600.0)

        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "critical"]
        assert len(sla_calls) >= 1
        assert sla_calls[0].kwargs.get("limit_change_direction") == "down"

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_sla_critical_records_trigger_sla_critical(self, mock_emit, mock_record):
        """SLA critical RTT 시 limit_change_trigger='sla_critical' 메트릭이 기록되는지 확인."""
        throttle = self._make_throttle(sla_critical_ms=500)

        throttle._maybe_adjust_limit(600.0)

        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "critical"]
        assert len(sla_calls) >= 1
        assert sla_calls[0].kwargs.get("limit_change_trigger") == "sla_critical"

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_sla_critical_records_change_percent_30(self, mock_emit, mock_record):
        """SLA critical RTT 시 limit_change_percent=30 메트릭이 기록되는지 확인."""
        throttle = self._make_throttle(sla_critical_ms=500)

        throttle._maybe_adjust_limit(600.0)

        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "critical"]
        assert len(sla_calls) >= 1
        assert sla_calls[0].kwargs.get("limit_change_percent") == 30

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_sla_critical_uses_dynamic_service_name(self, mock_emit, mock_record):
        """SLA critical 메트릭이 self._service_name 동적 라벨을 사용하는지 확인."""
        throttle = self._make_throttle(sla_critical_ms=500)

        throttle._maybe_adjust_limit(600.0)

        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "critical"]
        assert len(sla_calls) >= 1
        assert sla_calls[0].kwargs.get("service") == throttle._service_name


# =============================================================================
# SLA Warning 경로 메트릭 테스트
# =============================================================================


class TestMaybeAdjustLimitSLAWarningMetrics:
    """_maybe_adjust_limit()에서 SLA warning RTT 시 Prometheus 메트릭 기록 테스트."""

    def _make_throttle(self, sla_critical_ms: int = 500, sla_warning_ms: int = 200) -> AdaptiveThrottle:
        """테스트용 AdaptiveThrottle 인스턴스 생성."""
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=1000,
            sla_critical_ms=sla_critical_ms,
            sla_warning_ms=sla_warning_ms,
            sample_interval_ms=0,
        )
        return AdaptiveThrottle(config=config)

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_sla_warning_records_sla_event_metric(self, mock_emit, mock_record):
        """SLA warning RTT 시 sla_event='warning' 메트릭이 기록되는지 확인."""
        throttle = self._make_throttle(sla_warning_ms=200, sla_critical_ms=500)

        # warning 범위: >= 200 and < 500
        throttle._maybe_adjust_limit(300.0)

        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "warning"]
        assert len(sla_calls) >= 1, (
            "_record_throttle_metrics(sla_event='warning')이 호출되지 않음. " f"실제 호출: {mock_record.call_args_list}"
        )

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_sla_warning_records_limit_change_direction_down(self, mock_emit, mock_record):
        """SLA warning RTT 시 limit_change_direction='down' 메트릭이 기록되는지 확인."""
        throttle = self._make_throttle(sla_warning_ms=200, sla_critical_ms=500)

        throttle._maybe_adjust_limit(300.0)

        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "warning"]
        assert len(sla_calls) >= 1
        assert sla_calls[0].kwargs.get("limit_change_direction") == "down"

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_sla_warning_records_trigger_sla_warning(self, mock_emit, mock_record):
        """SLA warning RTT 시 limit_change_trigger='sla_warning' 메트릭이 기록되는지 확인."""
        throttle = self._make_throttle(sla_warning_ms=200, sla_critical_ms=500)

        throttle._maybe_adjust_limit(300.0)

        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "warning"]
        assert len(sla_calls) >= 1
        assert sla_calls[0].kwargs.get("limit_change_trigger") == "sla_warning"

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_sla_warning_uses_dynamic_service_name(self, mock_emit, mock_record):
        """SLA warning 메트릭이 self._service_name 동적 라벨을 사용하는지 확인."""
        throttle = self._make_throttle(sla_warning_ms=200, sla_critical_ms=500)

        throttle._maybe_adjust_limit(300.0)

        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "warning"]
        assert len(sla_calls) >= 1
        assert sla_calls[0].kwargs.get("service") == throttle._service_name


# =============================================================================
# SLA 경계값 경로 분기 테스트
# =============================================================================


class TestMaybeAdjustLimitSLABranchRouting:
    """RTT 값에 따라 올바른 SLA 경로로 분기되는지 확인."""

    def _make_throttle(self) -> AdaptiveThrottle:
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=1000,
            sla_critical_ms=500,
            sla_warning_ms=200,
            sample_interval_ms=0,
        )
        return AdaptiveThrottle(config=config)

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_rtt_at_critical_threshold_triggers_critical(self, mock_emit, mock_record):
        """RTT == sla_critical_ms 시 critical 경로로 분기."""
        throttle = self._make_throttle()

        throttle._maybe_adjust_limit(500.0)

        sla_critical_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "critical"]
        sla_warning_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "warning"]
        assert len(sla_critical_calls) >= 1
        assert len(sla_warning_calls) == 0

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_rtt_between_warning_and_critical_triggers_warning(self, mock_emit, mock_record):
        """warning <= RTT < critical 시 warning 경로로 분기."""
        throttle = self._make_throttle()

        throttle._maybe_adjust_limit(300.0)

        sla_critical_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "critical"]
        sla_warning_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") == "warning"]
        assert len(sla_critical_calls) == 0
        assert len(sla_warning_calls) >= 1

    @patch("selfhealing.services.throttle.adaptive._record_throttle_metrics")
    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_rtt_below_warning_triggers_no_sla_event(self, mock_emit, mock_record):
        """RTT < warning 시 sla_event 메트릭이 기록되지 않음."""
        throttle = self._make_throttle()

        throttle._maybe_adjust_limit(50.0)

        sla_calls = [c for c in mock_record.call_args_list if c.kwargs.get("sla_event") is not None]
        assert len(sla_calls) == 0
