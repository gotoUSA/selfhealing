"""
ThrottleLimitAdjuster 단위 테스트.

테스트 대상: selfhealing.services.throttle.limit_adjuster.ThrottleLimitAdjuster

검증 범위:
- 초기 상태 및 register()/start() 수명주기
- 429, Cooldown, Error Budget, Load Shedding, Kill Switch 핸들러 동작
- 다중 Policy 등록 시 모든 Policy에 전파
- 개별 핸들러 실패 시 Fail-Open (다른 Policy에 전파하지 않음)
- start() 중복 호출 방지
- EventBus 미설치 시 Fail-Open
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.services.throttle.limit_adjuster import ThrottleLimitAdjuster

# =============================================================================
# 초기화 및 수명주기 동작 검증
# =============================================================================


class TestThrottleLimitAdjusterLifecycleBehavior:
    """ThrottleLimitAdjuster 초기화 및 수명주기 동작 검증."""

    def test_initial_state_empty_and_not_started(self):
        """초기 상태에서 policies는 비어있고 started는 False여야 한다."""
        adjuster = ThrottleLimitAdjuster()
        assert adjuster._policies == []
        assert adjuster._started is False

    def test_register_adds_policy(self):
        """register() 호출 시 policy가 _policies에 추가되어야 한다."""
        adjuster = ThrottleLimitAdjuster()
        mock_policy = MagicMock()
        adjuster.register(mock_policy)
        assert mock_policy in adjuster._policies

    def test_register_multiple_policies(self):
        """여러 policy를 register할 수 있어야 한다."""
        adjuster = ThrottleLimitAdjuster()
        p1, p2 = MagicMock(), MagicMock()
        adjuster.register(p1)
        adjuster.register(p2)
        assert len(adjuster._policies) == 2

    def test_start_without_eventbus_failopen(self):
        """EventBus import 실패 시 _started가 False로 유지 (Fail-Open)."""
        adjuster = ThrottleLimitAdjuster()
        with patch(
            "selfhealing.services.throttle.limit_adjuster.ThrottleLimitAdjuster.start",
            side_effect=ImportError("no eventbus"),
        ):
            try:
                adjuster.start()
            except ImportError:
                pass
        # 원본 start()는 ImportError를 내부에서 처리
        adjuster_real = ThrottleLimitAdjuster()
        with patch(
            "selfhealing.services.event_bus.get_event_bus",
            side_effect=ImportError,
        ):
            adjuster_real.start()
        assert adjuster_real._started is False

    def test_start_duplicate_call_ignored(self):
        """start()를 두 번 호출하면 두 번째는 무시되어야 한다."""
        adjuster = ThrottleLimitAdjuster()
        adjuster._started = True  # 이미 시작된 상태 시뮬레이션
        adjuster.start()  # 두 번째 호출 — 무시됨
        assert adjuster._started is True


# =============================================================================
# 429 Rate Limit 핸들러 동작 검증
# =============================================================================


class TestThrottleLimitAdjuster429HandlerBehavior:
    """_handle_rate_limit_429() 동작 검증."""

    def test_429_handler_calls_reduce_limit_on_all_policies(self):
        """429 이벤트 수신 시 모든 등록된 policy의 reduce_limit_for_429()를 호출."""
        adjuster = ThrottleLimitAdjuster()
        p1, p2 = MagicMock(), MagicMock()
        adjuster.register(p1)
        adjuster.register(p2)

        event = MagicMock()
        adjuster._handle_rate_limit_429(event)

        p1.reduce_limit_for_429.assert_called_once_with(event)
        p2.reduce_limit_for_429.assert_called_once_with(event)

    def test_429_handler_failopen_on_policy_error(self):
        """한 policy에서 예외 발생 시 다른 policy에는 영향 없어야 한다."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        p1.reduce_limit_for_429.side_effect = RuntimeError("fail")
        p2 = MagicMock()
        adjuster.register(p1)
        adjuster.register(p2)

        event = MagicMock()
        adjuster._handle_rate_limit_429(event)

        # p1 실패했지만 p2는 호출됨
        p2.reduce_limit_for_429.assert_called_once_with(event)


# =============================================================================
# Cooldown End 핸들러 동작 검증
# =============================================================================


class TestThrottleLimitAdjusterCooldownEndBehavior:
    """_handle_cooldown_end() 동작 검증."""

    def test_cooldown_end_starts_recovery(self):
        """Cooldown 종료 시 모든 policy의 start_recovery_dampening()을 호출."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        adjuster.register(p1)

        adjuster._handle_cooldown_end(MagicMock())
        p1.start_recovery_dampening.assert_called_once()


# =============================================================================
# Error Budget 핸들러 동작 검증
# =============================================================================


class TestThrottleLimitAdjusterErrorBudgetHandlerBehavior:
    """Error Budget Warning/Critical/Recovered 핸들러 동작 검증."""

    def test_warning_applies_0_8_multiplier(self):
        """Error Budget Warning 시 multiplier=0.8로 호출."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        adjuster.register(p1)

        adjuster._handle_error_budget_warning(MagicMock())
        p1.adjust_limit_for_error_budget.assert_called_once_with(multiplier=0.8)

    def test_critical_applies_0_5_multiplier(self):
        """Error Budget Critical 시 multiplier=0.5로 호출."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        adjuster.register(p1)

        adjuster._handle_error_budget_critical(MagicMock())
        p1.adjust_limit_for_error_budget.assert_called_once_with(multiplier=0.5)

    def test_recovered_starts_dampening(self):
        """Error Budget 복구 시 start_recovery_dampening()을 호출."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        adjuster.register(p1)

        adjuster._handle_error_budget_recovered(MagicMock())
        p1.start_recovery_dampening.assert_called_once()

    def test_error_budget_handler_failopen(self):
        """핸들러 실패 시 다른 policy에 전파하지 않아야 한다."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        p1.adjust_limit_for_error_budget.side_effect = RuntimeError("fail")
        p2 = MagicMock()
        adjuster.register(p1)
        adjuster.register(p2)

        adjuster._handle_error_budget_warning(MagicMock())
        p2.adjust_limit_for_error_budget.assert_called_once_with(multiplier=0.8)


# =============================================================================
# Load Shedding 핸들러 동작 검증
# =============================================================================


class TestThrottleLimitAdjusterSheddingHandlerBehavior:
    """_handle_shedding_changed() 동작 검증."""

    def test_shedding_level_positive_reduces_limit(self):
        """new_level >= 0 시 traffic_limit 비율로 limit 조정."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        p1._config = MagicMock()
        p1._config.initial_limit = 100
        p1._config.min_limit = 10
        adjuster.register(p1)

        event = MagicMock()
        event.data = {"traffic_limit": 70.0, "new_level": 1}
        adjuster._handle_shedding_changed(event)

        p1.adjust_limit_for_shedding.assert_called_once()
        call_args = p1.adjust_limit_for_shedding.call_args
        suggested = call_args.kwargs.get("suggested_limit") or call_args[1].get("suggested_limit")
        assert suggested == 70  # 100 * 0.7

    def test_shedding_level_negative_restores_max(self):
        """new_level < 0 시 max_limit으로 복원."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        p1._config = MagicMock()
        p1._config.max_limit = 500
        adjuster.register(p1)

        event = MagicMock()
        event.data = {"traffic_limit": 100.0, "new_level": -1}
        adjuster._handle_shedding_changed(event)

        p1.adjust_limit_for_shedding.assert_called_once_with(
            suggested_limit=500,
        )

    def test_shedding_clamps_to_min(self):
        """traffic_limit 비율 적용 후 min_limit 이상이어야 한다."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        p1._config = MagicMock()
        p1._config.initial_limit = 100
        p1._config.min_limit = 20
        adjuster.register(p1)

        event = MagicMock()
        event.data = {"traffic_limit": 10.0, "new_level": 3}
        adjuster._handle_shedding_changed(event)

        call_args = p1.adjust_limit_for_shedding.call_args
        suggested = call_args.kwargs.get("suggested_limit") or call_args[1].get("suggested_limit")
        assert suggested >= 20  # min_limit 이상


# =============================================================================
# Kill Switch 핸들러 동작 검증
# =============================================================================


class TestThrottleLimitAdjusterKillSwitchHandlerBehavior:
    """Kill Switch 활성화/비활성화 핸들러 동작 검증."""

    def test_kill_switch_activated_freezes_gradient(self):
        """Kill Switch 활성화 시 gradient_frozen=True로 설정."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        adjuster.register(p1)

        adjuster._handle_kill_switch_activated(MagicMock())
        assert p1.gradient_frozen is True

    def test_kill_switch_deactivated_unfreezes_and_starts_recovery(self):
        """Kill Switch 비활성화 시 gradient_frozen=False + recovery 시작."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()
        adjuster.register(p1)

        adjuster._handle_kill_switch_deactivated(MagicMock())
        assert p1.gradient_frozen is False
        p1.start_recovery_dampening.assert_called_once()

    def test_kill_switch_handler_failopen(self):
        """Kill Switch 핸들러 실패 시 다른 policy에 전파하지 않아야 한다."""
        adjuster = ThrottleLimitAdjuster()
        p1 = MagicMock()

        # gradient_frozen setter에서 에러 발생 시뮬레이션
        type(p1).gradient_frozen = property(
            fget=lambda self: False,
            fset=MagicMock(side_effect=RuntimeError("fail")),
        )
        p2 = MagicMock()
        adjuster.register(p1)
        adjuster.register(p2)

        adjuster._handle_kill_switch_activated(MagicMock())
        # p2는 정상 처리됨
        assert p2.gradient_frozen is True
