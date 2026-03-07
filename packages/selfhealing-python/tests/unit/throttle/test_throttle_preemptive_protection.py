"""
AdaptiveThrottle 선제적 보호(Preemptive Protection) 단위 테스트.

Burn Rate 기반 예산 소진 예측으로 선제적 limit 감소 동작 검증.
"""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from selfhealing.services.throttle.adaptive import (
    AdaptiveThrottle,
    reset_adaptive_throttle,
)
from selfhealing.services.throttle.config import ThrottleConfig


@dataclass
class MockDepletionForecast:
    """테스트용 DepletionForecast Mock."""

    estimated_depletion_hours: float | None
    burn_rate_1h: float
    burn_rate_6h: float
    is_accelerating: bool
    risk_level: str
    recommended_action: str


@dataclass
class MockErrorBudgetStatus:
    """테스트용 ErrorBudgetStatus Mock."""

    budget_remaining_percent: float
    burn_rate_1h: float
    burn_rate_6h: float


class TestPreemptiveProtectionTrigger:
    """선제적 보호 트리거 조건 테스트."""

    def setup_method(self):
        """각 테스트 전 throttle 초기화."""
        reset_adaptive_throttle()

    def teardown_method(self):
        """각 테스트 후 throttle 초기화."""
        reset_adaptive_throttle()

    @patch.object(AdaptiveThrottle, "_subscribe_error_budget_events")
    @patch.object(AdaptiveThrottle, "_subscribe_rate_limit_events")
    def test_critical_risk_triggers_reduction(self, mock_sub_rate, mock_sub_budget):
        """critical 위험 수준 시 즉시 limit 감소 - _apply_preemptive_reduction 직접 테스트."""
        config = ThrottleConfig(initial_limit=1000, min_limit=10, max_limit=2000)
        throttle = AdaptiveThrottle(config)
        throttle._error_budget_limit_reduction_active = False

        # Mock forecast 객체 생성
        mock_forecast = MockDepletionForecast(
            estimated_depletion_hours=0.5,
            burn_rate_1h=50.0,
            burn_rate_6h=30.0,
            is_accelerating=True,
            risk_level="critical",
            recommended_action="즉시 Throttle 강화",
        )

        # 직접 _apply_preemptive_reduction 호출
        throttle._apply_preemptive_reduction(mock_forecast)

        # 검증: critical이므로 50% 감소
        assert throttle._error_budget_limit_reduction_active is True
        assert throttle._error_budget_multiplier == 0.5
        assert throttle._current_limit == 500

    @patch.object(AdaptiveThrottle, "_subscribe_error_budget_events")
    @patch.object(AdaptiveThrottle, "_subscribe_rate_limit_events")
    def test_high_risk_triggers_moderate_reduction(self, mock_sub_rate, mock_sub_budget):
        """high 위험 수준 시 20% 감소 - _apply_preemptive_reduction 직접 테스트."""
        config = ThrottleConfig(initial_limit=1000, min_limit=10, max_limit=2000)
        throttle = AdaptiveThrottle(config)
        throttle._error_budget_limit_reduction_active = False

        # Mock forecast 객체 생성
        mock_forecast = MockDepletionForecast(
            estimated_depletion_hours=3.0,
            burn_rate_1h=20.0,
            burn_rate_6h=10.0,
            is_accelerating=True,
            risk_level="high",
            recommended_action="Throttle WARNING 레벨로 사전 조정",
        )

        # 직접 _apply_preemptive_reduction 호출
        throttle._apply_preemptive_reduction(mock_forecast)

        # 검증: high이므로 20% 감소 (×0.8)
        assert throttle._error_budget_limit_reduction_active is True
        assert throttle._error_budget_multiplier == 0.8
        assert throttle._current_limit == 800


class TestPreemptiveProtectionSkipConditions:
    """선제적 보호 스킵 조건 테스트."""

    def setup_method(self):
        """각 테스트 전 throttle 초기화."""
        reset_adaptive_throttle()

    def teardown_method(self):
        """각 테스트 후 throttle 초기화."""
        reset_adaptive_throttle()

    @patch.object(AdaptiveThrottle, "_subscribe_error_budget_events")
    @patch.object(AdaptiveThrottle, "_subscribe_rate_limit_events")
    @patch("selfhealing.services.error_budget.forecaster.BudgetDepletionForecaster")
    @patch("selfhealing.services.error_budget.get_error_budget_service")
    def test_skips_if_already_in_reduction_mode(self, mock_get_service, mock_forecaster_class, mock_sub_rate, mock_sub_budget):
        """이미 감소 모드면 추가 감소 없음."""
        config = ThrottleConfig(initial_limit=1000, min_limit=10, max_limit=2000)
        throttle = AdaptiveThrottle(config)

        # 이미 감소 상태
        throttle._error_budget_limit_reduction_active = True
        throttle._error_budget_multiplier = 0.5
        throttle._current_limit = 500

        # 선제적 보호 실행 (아무 동작 없어야 함)
        throttle._check_preemptive_protection()

        # 검증: 상태 변화 없음
        assert throttle._current_limit == 500
        mock_forecaster_class.assert_not_called()

    @patch.object(AdaptiveThrottle, "_subscribe_error_budget_events")
    @patch.object(AdaptiveThrottle, "_subscribe_rate_limit_events")
    @patch("selfhealing.services.error_budget.forecaster.BudgetDepletionForecaster")
    @patch("selfhealing.services.error_budget.get_error_budget_service")
    def test_skips_if_forecaster_says_no(self, mock_get_service, mock_forecaster_class, mock_sub_rate, mock_sub_budget):
        """Forecaster가 False 반환 시 감소 없음."""
        config = ThrottleConfig(initial_limit=1000, min_limit=10, max_limit=2000)
        throttle = AdaptiveThrottle(config)
        throttle._error_budget_limit_reduction_active = False

        # Mock 설정: 선제적 보호 불필요
        mock_status = MockErrorBudgetStatus(
            budget_remaining_percent=80.0,
            burn_rate_1h=1.0,
            burn_rate_6h=1.0,
        )
        mock_service = MagicMock()
        mock_service.get_budget_status.return_value = mock_status
        mock_get_service.return_value = mock_service

        mock_forecaster = MagicMock()
        mock_forecaster.should_preemptive_throttle.return_value = False
        mock_forecaster_class.return_value = mock_forecaster

        # 선제적 보호 실행
        throttle._check_preemptive_protection()

        # 검증: 상태 변화 없음
        assert throttle._error_budget_limit_reduction_active is False
        assert throttle._current_limit == 1000


class TestPreemptiveProtectionMinLimit:
    """선제적 보호 min_limit 적용 테스트."""

    def setup_method(self):
        """각 테스트 전 throttle 초기화."""
        reset_adaptive_throttle()

    def teardown_method(self):
        """각 테스트 후 throttle 초기화."""
        reset_adaptive_throttle()

    @patch.object(AdaptiveThrottle, "_subscribe_error_budget_events")
    @patch.object(AdaptiveThrottle, "_subscribe_rate_limit_events")
    @patch("selfhealing.services.error_budget.forecaster.BudgetDepletionForecaster")
    @patch("selfhealing.services.error_budget.get_error_budget_service")
    def test_respects_min_limit(self, mock_get_service, mock_forecaster_class, mock_sub_rate, mock_sub_budget):
        """선제적 감소 시 min_limit 미만으로 내려가지 않음."""
        config = ThrottleConfig(initial_limit=100, min_limit=60)
        throttle = AdaptiveThrottle(config)
        throttle._error_budget_limit_reduction_active = False

        # Mock 설정: critical (50% 감소 → 50, 그러나 min_limit=60)
        mock_status = MockErrorBudgetStatus(
            budget_remaining_percent=5.0,
            burn_rate_1h=50.0,
            burn_rate_6h=30.0,
        )
        mock_service = MagicMock()
        mock_service.get_budget_status.return_value = mock_status
        mock_get_service.return_value = mock_service

        mock_forecast = MockDepletionForecast(
            estimated_depletion_hours=0.5,
            burn_rate_1h=50.0,
            burn_rate_6h=30.0,
            is_accelerating=True,
            risk_level="critical",
            recommended_action="즉시 Throttle 강화",
        )
        mock_forecaster = MagicMock()
        mock_forecaster.should_preemptive_throttle.return_value = True
        mock_forecaster.forecast.return_value = mock_forecast
        mock_forecaster_class.return_value = mock_forecaster

        # 선제적 보호 실행
        throttle._check_preemptive_protection()

        # 검증: min_limit 적용
        assert throttle._current_limit == 60  # min_limit 이상으로 제한


class TestPreemptiveProtectionFailOpen:
    """선제적 보호 Fail-Open 테스트."""

    def setup_method(self):
        """각 테스트 전 throttle 초기화."""
        reset_adaptive_throttle()

    def teardown_method(self):
        """각 테스트 후 throttle 초기화."""
        reset_adaptive_throttle()

    @patch.object(AdaptiveThrottle, "_subscribe_error_budget_events")
    @patch.object(AdaptiveThrottle, "_subscribe_rate_limit_events")
    def test_handles_import_error_gracefully(self, mock_sub_rate, mock_sub_budget):
        """Forecaster Import 실패 시 정상 동작."""
        config = ThrottleConfig(initial_limit=1000, min_limit=10, max_limit=2000)
        throttle = AdaptiveThrottle(config)
        throttle._error_budget_limit_reduction_active = False

        # ImportError 발생 시 예외 없이 진행
        with patch(
            "selfhealing.services.error_budget.get_error_budget_service",
            side_effect=ImportError("Module not found"),
        ):
            throttle._check_preemptive_protection()

        # 검증: 상태 변화 없음 (Fail-Open)
        assert throttle._error_budget_limit_reduction_active is False
        assert throttle._current_limit == 1000

    @patch.object(AdaptiveThrottle, "_subscribe_error_budget_events")
    @patch.object(AdaptiveThrottle, "_subscribe_rate_limit_events")
    @patch("selfhealing.services.error_budget.get_error_budget_service")
    def test_handles_service_exception_gracefully(self, mock_get_service, mock_sub_rate, mock_sub_budget):
        """서비스 예외 발생 시 정상 동작."""
        config = ThrottleConfig(initial_limit=1000, min_limit=10, max_limit=2000)
        throttle = AdaptiveThrottle(config)
        throttle._error_budget_limit_reduction_active = False

        mock_get_service.side_effect = Exception("Service unavailable")

        # 예외 발생해도 정상 진행
        throttle._check_preemptive_protection()

        # 검증: 상태 변화 없음 (Fail-Open)
        assert throttle._error_budget_limit_reduction_active is False
        assert throttle._current_limit == 1000


class TestPreemptiveProtectionIntegration:
    """선제적 보호 통합 테스트 (check_and_sync_emergency_state 호출)."""

    def setup_method(self):
        """각 테스트 전 throttle 초기화."""
        reset_adaptive_throttle()

    def teardown_method(self):
        """각 테스트 후 throttle 초기화."""
        reset_adaptive_throttle()

    @patch.object(AdaptiveThrottle, "_subscribe_error_budget_events")
    @patch.object(AdaptiveThrottle, "_subscribe_rate_limit_events")
    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    @patch.object(AdaptiveThrottle, "_check_preemptive_protection")
    def test_calls_preemptive_protection_in_emergency_sync(
        self, mock_preemptive, mock_get_manager, mock_sub_rate, mock_sub_budget
    ):
        """_sync_governance_state에서 선제적 보호 호출."""
        config = ThrottleConfig(initial_limit=1000, max_limit=2000)
        throttle = AdaptiveThrottle(config)

        # Emergency check 강제 트리거 (TTL 만료)
        throttle._last_emergency_check_time = 0
        throttle._emergency_cache_ttl_seconds = 1

        # Mock: Emergency level 정상
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value.value = 0
        mock_get_manager.return_value = mock_manager

        # _sync_governance_state 호출 (check_and_sync_emergency_state는 래퍼)
        throttle.check_and_sync_emergency_state()

        # 검증: 선제적 보호 호출됨
        mock_preemptive.assert_called_once()
