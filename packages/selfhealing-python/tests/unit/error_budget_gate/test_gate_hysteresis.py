"""
ErrorBudgetGate 히스테리시스 로직 단위 테스트.

플래핑 방지를 위한 진입/복구 임계치 분리 동작 검증.
"""

from unittest.mock import patch

from selfhealing.services.error_budget_gate.config import (
    ErrorBudgetGateConfig,
    GateStatus,
)
from selfhealing.services.error_budget_gate.gate import ErrorBudgetGate


class TestGateHysteresisConstants:
    """히스테리시스 설정 상수 테스트."""

    def test_config_has_hysteresis_buffer(self):
        """설정에 threshold_hysteresis_buffer_percent 필드 존재."""
        config = ErrorBudgetGateConfig()
        assert hasattr(config, "threshold_hysteresis_buffer_percent")
        assert config.threshold_hysteresis_buffer_percent > 0

    def test_default_critical_threshold(self):
        """기본 critical 임계치 확인."""
        config = ErrorBudgetGateConfig()
        assert config.critical_threshold_percent == 10.0

    def test_default_warning_threshold(self):
        """기본 warning 임계치 확인."""
        config = ErrorBudgetGateConfig()
        assert config.warning_threshold_percent == 20.0


class TestGateHysteresisStateTransitions:
    """히스테리시스 상태 전이 테스트."""

    def setup_method(self):
        """각 테스트 전 Gate 초기화."""
        self.config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
            warning_threshold_percent=20.0,
            threshold_hysteresis_buffer_percent=2.0,
        )

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_open_to_warning_at_entry_threshold(self, mock_get_budget):
        """OPEN → WARNING: 진입 임계치(20%)에서 전이."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.OPEN

        mock_get_budget.return_value = 19.0  # < 20% → WARNING
        result = gate.check(force_refresh=True)

        assert result.status == GateStatus.WARNING
        assert gate._current_status == GateStatus.WARNING

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_warning_to_open_requires_recovery_threshold(self, mock_get_budget):
        """WARNING → OPEN: 복구 임계치(22%)가 필요."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.WARNING

        # 21%: 진입 임계치(20%)는 넘지만, 복구 임계치(22%)는 못 넘음
        mock_get_budget.return_value = 21.0
        result = gate.check(force_refresh=True)

        assert result.status == GateStatus.WARNING  # WARNING 유지
        assert gate._current_status == GateStatus.WARNING

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_warning_to_open_at_recovery_threshold(self, mock_get_budget):
        """WARNING → OPEN: 복구 임계치(22%) 도달 시 OPEN으로 복귀."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.WARNING

        # 22%: 복구 임계치 충족
        mock_get_budget.return_value = 22.0
        result = gate.check(force_refresh=True)

        assert result.status == GateStatus.OPEN
        assert gate._current_status == GateStatus.OPEN

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_warning_to_blocked_at_critical_entry(self, mock_get_budget):
        """WARNING → BLOCKED: critical 진입 임계치(10%)에서 전이."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.WARNING

        mock_get_budget.return_value = 9.0  # < 10%
        result = gate.check(force_refresh=True)

        assert result.status == GateStatus.BLOCKED
        assert result.allowed is False

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_blocked_to_warning_requires_recovery_threshold(self, mock_get_budget):
        """BLOCKED → WARNING: 복구 임계치(12%)가 필요."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.BLOCKED

        # 11%: 진입 임계치(10%)는 넘지만, 복구 임계치(12%)는 못 넘음
        mock_get_budget.return_value = 11.0
        result = gate.check(force_refresh=True)

        assert result.status == GateStatus.BLOCKED  # BLOCKED 유지
        assert result.allowed is False

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_blocked_to_warning_at_recovery_threshold(self, mock_get_budget):
        """BLOCKED → WARNING: 복구 임계치(12%) 도달 시 WARNING으로 복귀."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.BLOCKED

        # 12%: critical 복구 임계치 충족
        mock_get_budget.return_value = 12.0
        result = gate.check(force_refresh=True)

        assert result.status == GateStatus.WARNING
        assert result.allowed is True


class TestGateHysteresisFlappingPrevention:
    """플래핑 방지 효과 테스트."""

    def setup_method(self):
        """각 테스트 전 Gate 초기화."""
        self.config = ErrorBudgetGateConfig(
            threshold_hysteresis_buffer_percent=2.0,
        )

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_oscillation_near_warning_threshold(self, mock_get_budget):
        """경고 임계치 근처 진동 시 상태 안정화."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.OPEN

        # 상태 전이 시퀀스: OPEN → WARNING → WARNING 유지
        # 19% → WARNING 진입
        mock_get_budget.return_value = 19.0
        result1 = gate.check(force_refresh=True)
        assert result1.status == GateStatus.WARNING

        # 20.5% → WARNING 유지 (복구 임계치 22% 미달)
        mock_get_budget.return_value = 20.5
        result2 = gate.check(force_refresh=True)
        assert result2.status == GateStatus.WARNING

        # 19.5% → WARNING 유지
        mock_get_budget.return_value = 19.5
        result3 = gate.check(force_refresh=True)
        assert result3.status == GateStatus.WARNING

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_oscillation_near_critical_threshold(self, mock_get_budget):
        """위험 임계치 근처 진동 시 상태 안정화."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.WARNING

        # 9% → BLOCKED 진입
        mock_get_budget.return_value = 9.0
        result1 = gate.check(force_refresh=True)
        assert result1.status == GateStatus.BLOCKED

        # 10.5% → BLOCKED 유지 (복구 임계치 12% 미달)
        mock_get_budget.return_value = 10.5
        result2 = gate.check(force_refresh=True)
        assert result2.status == GateStatus.BLOCKED

        # 11.5% → BLOCKED 유지
        mock_get_budget.return_value = 11.5
        result3 = gate.check(force_refresh=True)
        assert result3.status == GateStatus.BLOCKED


class TestGateHysteresisEventEmission:
    """히스테리시스 이벤트 발행 테스트."""

    def setup_method(self):
        """각 테스트 전 Gate 초기화."""
        self.config = ErrorBudgetGateConfig()

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._emit_error_budget_warning_event")
    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_warning_event_on_first_transition(self, mock_get_budget, mock_emit_warning):
        """OPEN → WARNING 첫 전이 시에만 이벤트 발행."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.OPEN

        mock_get_budget.return_value = 15.0  # WARNING 진입
        gate.check(force_refresh=True)

        mock_emit_warning.assert_called_once()

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._emit_error_budget_warning_event")
    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_no_warning_event_while_staying_in_warning(self, mock_get_budget, mock_emit_warning):
        """WARNING 상태 유지 시 중복 이벤트 발행하지 않음."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.WARNING

        mock_get_budget.return_value = 15.0  # WARNING 유지
        gate.check(force_refresh=True)

        mock_emit_warning.assert_not_called()

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._emit_error_budget_recovered_event")
    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_recovered_event_on_transition_to_open(self, mock_get_budget, mock_emit_recovered):
        """WARNING → OPEN 전이 시 recovered 이벤트 발행."""
        gate = ErrorBudgetGate(self.config)
        gate._current_status = GateStatus.WARNING

        # 복구 임계치(22%) 도달
        mock_get_budget.return_value = 25.0
        gate.check(force_refresh=True)

        mock_emit_recovered.assert_called_once()


class TestGateHysteresisConfigIntegration:
    """히스테리시스 설정 통합 테스트."""

    @patch("selfhealing.services.error_budget_gate.gate.ErrorBudgetGate._get_error_budget_percent")
    def test_custom_buffer_value(self, mock_get_budget):
        """커스텀 버퍼 값 적용 확인."""
        config = ErrorBudgetGateConfig(
            critical_threshold_percent=10.0,
            warning_threshold_percent=20.0,
            threshold_hysteresis_buffer_percent=5.0,  # 커스텀: 5%
        )
        gate = ErrorBudgetGate(config)
        gate._current_status = GateStatus.BLOCKED

        # 14%: 기본 버퍼(2%)면 복구, 커스텀 버퍼(5%)면 아직 BLOCKED
        mock_get_budget.return_value = 14.0
        result = gate.check(force_refresh=True)

        assert result.status == GateStatus.BLOCKED  # 15% 미달로 BLOCKED 유지

        # 15%: 커스텀 복구 임계치(10+5=15%) 도달
        mock_get_budget.return_value = 15.0
        result = gate.check(force_refresh=True)

        assert result.status == GateStatus.WARNING
