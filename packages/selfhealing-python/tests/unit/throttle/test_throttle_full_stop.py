"""
AdaptiveThrottle Full Stop 조건 테스트.

테스트 대상:
1. check_full_stop_conditions() - 3중 조건 확인 (LEVEL_3 + DB_CB_OPEN + BUDGET_EXHAUSTED)
2. activate_full_stop() - Full Stop 활성화 (min_limit=0)
3. deactivate_full_stop() - Full Stop 비활성화
4. is_full_stop_active() - Full Stop 상태 조회
5. KILL_SWITCH_ACTIVATED 이벤트 발행
"""

import pytest
from unittest.mock import MagicMock, patch

from selfhealing.services.throttle.adaptive import (
    AdaptiveThrottle,
    get_adaptive_throttle,
    reset_adaptive_throttle,
)
from selfhealing.services.throttle.config import ThrottleConfig


class TestCheckFullStopConditions:
    """check_full_stop_conditions() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_no_full_stop_at_normal_level(self):
        """NORMAL 레벨에서는 Full Stop 조건 미충족."""
        throttle = get_adaptive_throttle()

        is_full_stop, reason = throttle.check_full_stop_conditions()

        assert is_full_stop is False
        assert reason == "NORMAL"

    def test_level_3_alone_not_full_stop(self):
        """LEVEL_3만으로는 Full Stop 미충족."""
        throttle = get_adaptive_throttle()
        throttle._emergency_level = 3

        is_full_stop, reason = throttle.check_full_stop_conditions()

        assert is_full_stop is False
        assert "EMERGENCY_LEVEL_3" in reason

    @patch.object(AdaptiveThrottle, "_check_db_circuit_breaker_open")
    @patch.object(AdaptiveThrottle, "_check_error_budget_exhausted")
    def test_full_stop_when_all_three_conditions_met(self, mock_budget, mock_cb):
        """3중 조건 모두 충족 시 Full Stop."""
        mock_cb.return_value = True  # DB CB OPEN
        mock_budget.return_value = True  # Budget exhausted

        throttle = get_adaptive_throttle()
        throttle._emergency_level = 3  # LEVEL_3

        is_full_stop, reason = throttle.check_full_stop_conditions()

        assert is_full_stop is True
        assert "EMERGENCY_LEVEL_3" in reason
        assert "DB_CB_OPEN" in reason
        assert "BUDGET_EXHAUSTED" in reason

    @patch.object(AdaptiveThrottle, "_check_db_circuit_breaker_open")
    @patch.object(AdaptiveThrottle, "_check_error_budget_exhausted")
    def test_no_full_stop_without_db_cb_open(self, mock_budget, mock_cb):
        """DB CB OPEN 없으면 Full Stop 미충족."""
        mock_cb.return_value = False  # DB CB 정상
        mock_budget.return_value = True  # Budget exhausted

        throttle = get_adaptive_throttle()
        throttle._emergency_level = 3

        is_full_stop, reason = throttle.check_full_stop_conditions()

        assert is_full_stop is False

    @patch.object(AdaptiveThrottle, "_check_db_circuit_breaker_open")
    @patch.object(AdaptiveThrottle, "_check_error_budget_exhausted")
    def test_no_full_stop_without_budget_exhausted(self, mock_budget, mock_cb):
        """Budget 소진되지 않으면 Full Stop 미충족."""
        mock_cb.return_value = True  # DB CB OPEN
        mock_budget.return_value = False  # Budget 남음

        throttle = get_adaptive_throttle()
        throttle._emergency_level = 3

        is_full_stop, reason = throttle.check_full_stop_conditions()

        assert is_full_stop is False


class TestCheckDbCircuitBreakerOpen:
    """_check_db_circuit_breaker_open() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    @patch("selfhealing.services.circuit_breaker_service.get_circuit_breaker_service")
    def test_returns_true_when_db_cb_open(self, mock_get_cb):
        """DB Circuit Breaker OPEN 시 True 반환."""
        mock_cb_service = MagicMock()
        mock_cb_service.get_state.return_value = "open"
        mock_get_cb.return_value = mock_cb_service

        throttle = get_adaptive_throttle()
        result = throttle._check_db_circuit_breaker_open()

        assert result is True

    @patch("selfhealing.services.circuit_breaker_service.get_circuit_breaker_service")
    def test_returns_false_when_db_cb_closed(self, mock_get_cb):
        """DB Circuit Breaker CLOSED 시 False 반환."""
        mock_cb_service = MagicMock()
        mock_cb_service.get_state.return_value = "closed"
        mock_get_cb.return_value = mock_cb_service

        throttle = get_adaptive_throttle()
        result = throttle._check_db_circuit_breaker_open()

        assert result is False

    def test_returns_false_when_service_unavailable(self):
        """CB 서비스 사용 불가 시 False 반환 (Fail-Open)."""
        throttle = get_adaptive_throttle()

        # 실제 서비스 없이 호출 - 메서드가 예외 처리함
        result = throttle._check_db_circuit_breaker_open()

        # 서비스가 없거나 예외 발생 시 False 반환
        assert result is False


class TestCheckErrorBudgetExhausted:
    """_check_error_budget_exhausted() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    @patch("selfhealing.services.error_budget_service.get_error_budget_service")
    def test_returns_true_when_budget_exhausted(self, mock_get_budget):
        """Budget 소진 시 True 반환."""
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = 0.0
        mock_service = MagicMock()
        mock_service.get_budget_status.return_value = mock_status
        mock_get_budget.return_value = mock_service

        throttle = get_adaptive_throttle()
        result = throttle._check_error_budget_exhausted()

        assert result is True

    @patch("selfhealing.services.error_budget_service.get_error_budget_service")
    def test_returns_true_when_budget_negative(self, mock_get_budget):
        """Budget 음수 시 True 반환."""
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = -10.0
        mock_service = MagicMock()
        mock_service.get_budget_status.return_value = mock_status
        mock_get_budget.return_value = mock_service

        throttle = get_adaptive_throttle()
        result = throttle._check_error_budget_exhausted()

        assert result is True

    @patch("selfhealing.services.error_budget_service.get_error_budget_service")
    def test_returns_false_when_budget_remaining(self, mock_get_budget):
        """Budget 남아있으면 False 반환."""
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = 50.0
        mock_service = MagicMock()
        mock_service.get_budget_status.return_value = mock_status
        mock_get_budget.return_value = mock_service

        throttle = get_adaptive_throttle()
        result = throttle._check_error_budget_exhausted()

        assert result is False

    def test_returns_false_when_service_unavailable(self):
        """Budget 서비스 사용 불가 시 False 반환 (Fail-Open)."""
        throttle = get_adaptive_throttle()

        # 서비스가 없거나 예외 발생 시 False 반환 (Fail-Open 설계)
        result = throttle._check_error_budget_exhausted()

        assert result is False


class TestActivateFullStop:
    """activate_full_stop() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_sets_limit_to_zero(self):
        """Full Stop 시 limit을 0으로 설정."""
        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        throttle.activate_full_stop("LEVEL_3 + DB_CB_OPEN + BUDGET_EXHAUSTED")

        assert throttle.current_limit == 0

    def test_sets_full_stop_active_flag(self):
        """Full Stop 시 플래그 설정."""
        throttle = get_adaptive_throttle()

        throttle.activate_full_stop("test_reason")

        assert throttle.is_full_stop_active() is True

    def test_does_not_activate_twice(self):
        """이미 활성화된 경우 중복 활성화 안함."""
        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        throttle.activate_full_stop("first")
        throttle.current_limit = 50  # 임의로 변경

        throttle.activate_full_stop("second")

        # 두 번째 호출에서 limit 변경 안됨
        assert throttle.current_limit == 50

    @patch("selfhealing.services.throttle.adaptive._emit_throttle_event")
    def test_emits_throttle_limit_changed_event(self, mock_emit):
        """Full Stop 시 THROTTLE_LIMIT_CHANGED 이벤트 발행."""
        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        throttle.activate_full_stop("test_reason")

        mock_emit.assert_called()
        call_args = mock_emit.call_args_list[0]
        assert call_args[0][0] == "THROTTLE_LIMIT_CHANGED"
        assert call_args[0][1]["new_limit"] == 0
        assert call_args[0][1]["full_stop"] is True


class TestDeactivateFullStop:
    """deactivate_full_stop() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_clears_full_stop_active_flag(self):
        """Full Stop 비활성화 시 플래그 해제."""
        throttle = get_adaptive_throttle()
        throttle.activate_full_stop("test")

        throttle.deactivate_full_stop()

        assert throttle.is_full_stop_active() is False

    def test_starts_recovery_dampening(self):
        """Full Stop 비활성화 시 Recovery Dampening 시작."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100
        throttle.activate_full_stop("test")

        throttle.deactivate_full_stop()

        assert throttle.is_recovery_dampening_active() is True

    def test_does_not_deactivate_if_not_active(self):
        """Full Stop 비활성 상태에서 deactivate 호출 무시."""
        throttle = get_adaptive_throttle()

        throttle.deactivate_full_stop()  # 이미 비활성

        assert throttle.is_full_stop_active() is False


class TestIsFullStopActive:
    """is_full_stop_active() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_returns_false_initially(self):
        """초기 상태에서 False 반환."""
        throttle = get_adaptive_throttle()

        assert throttle.is_full_stop_active() is False

    def test_returns_true_after_activation(self):
        """활성화 후 True 반환."""
        throttle = get_adaptive_throttle()
        throttle.activate_full_stop("test")

        assert throttle.is_full_stop_active() is True


class TestFullStopInStats:
    """get_stats()에 Full Stop 정보 포함 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_stats_include_full_stop_active(self):
        """stats에 full_stop_active 필드 포함."""
        throttle = get_adaptive_throttle()
        stats = throttle.get_stats()

        assert "emergency" in stats
        assert "full_stop_active" in stats["emergency"]

    def test_stats_reflect_full_stop_state(self):
        """stats가 Full Stop 상태 반영."""
        throttle = get_adaptive_throttle()

        # 비활성 상태
        stats1 = throttle.get_stats()
        assert stats1["emergency"]["full_stop_active"] is False

        # 활성화
        throttle.activate_full_stop("test")
        stats2 = throttle.get_stats()
        assert stats2["emergency"]["full_stop_active"] is True


class TestFullStopIntegrationWithAdjustForEmergency:
    """adjust_for_emergency()와 Full Stop 연동 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    @patch.object(AdaptiveThrottle, "_check_db_circuit_breaker_open")
    @patch.object(AdaptiveThrottle, "_check_error_budget_exhausted")
    def test_level_3_triggers_full_stop_when_conditions_met(self, mock_budget, mock_cb):
        """LEVEL_3 시 3중 조건 충족하면 Full Stop 활성화."""
        mock_cb.return_value = True
        mock_budget.return_value = True

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        throttle.adjust_for_emergency(3)

        assert throttle.is_full_stop_active() is True
        assert throttle.current_limit == 0

    @patch.object(AdaptiveThrottle, "_check_db_circuit_breaker_open")
    @patch.object(AdaptiveThrottle, "_check_error_budget_exhausted")
    def test_level_3_no_full_stop_when_conditions_not_met(self, mock_budget, mock_cb):
        """LEVEL_3 시 조건 미충족하면 Full Stop 미활성화."""
        mock_cb.return_value = False  # DB CB 정상
        mock_budget.return_value = False  # Budget 남음

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        throttle.adjust_for_emergency(3)

        assert throttle.is_full_stop_active() is False
        assert throttle.current_limit == throttle.config.min_limit  # min_limit 적용

    def test_level_0_deactivates_full_stop(self):
        """Level 0으로 복구 시 Full Stop 비활성화."""
        throttle = get_adaptive_throttle()
        throttle._full_stop_active = True
        throttle._emergency_mode_active = True
        throttle._emergency_level = 3
        throttle._base_limit_before_emergency = 100

        throttle.adjust_for_emergency(0)

        assert throttle.is_full_stop_active() is False


class TestResetClearsFullStop:
    """reset_all()이 Full Stop 상태도 초기화하는지 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_reset_clears_full_stop(self):
        """reset_all()이 Full Stop 상태 초기화."""
        throttle = get_adaptive_throttle()
        throttle.activate_full_stop("test")

        assert throttle.is_full_stop_active() is True

        throttle.reset_all()

        assert throttle.is_full_stop_active() is False
