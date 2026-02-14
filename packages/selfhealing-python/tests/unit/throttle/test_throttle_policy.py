"""
ThrottlePolicy 단위 테스트.

테스트 대상: selfhealing.services.throttle.policy.ThrottlePolicy

검증 범위:
- ThrottlePolicy 초기화 및 속성 동작
- execute() 허용/거부/예외 시 PolicyResult 반환
- check() 순수 rate limit 검사
- record_response() RTT 기록 + gradient 기반 limit 조정
- _maybe_adjust_limit() SLA Critical/Warning/Gradient 분기
- _resolve_throttle_key() context 기반 키 결정
- current_limit setter min/max 바운드 적용
- gradient_frozen 시 limit 조정 스킵
- Dampening Cap 적용 동작
- reduce_limit_for_429() / adjust_limit_for_error_budget() / adjust_limit_for_shedding()
- start_recovery_dampening()
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from selfhealing.interfaces.resilience_policy import (
    PolicyContext,
    PolicyOutcome,
)
from selfhealing.services.throttle.config import ThrottleConfig
from selfhealing.services.throttle.policy import ThrottlePolicy


# =============================================================================
# ThrottlePolicy 초기화 동작 검증
# =============================================================================


class TestThrottlePolicyInitBehavior:
    """ThrottlePolicy 초기화 동작 검증."""

    def test_default_config_creates_valid_policy(self):
        """기본 ThrottleConfig로 생성 시 정상 초기화되어야 한다."""
        policy = ThrottlePolicy()
        config = ThrottleConfig()
        assert policy.current_limit == config.initial_limit
        assert policy.name == "throttle"
        assert policy.gradient_frozen is False

    def test_custom_config_applies_initial_limit(self):
        """사용자 지정 config의 initial_limit이 적용되어야 한다."""
        config = ThrottleConfig(initial_limit=200, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        assert policy.current_limit == config.initial_limit

    def test_custom_gradient_calculator_injected(self):
        """외부 GradientCalculator 주입 시 내부 기본 생성 대신 사용되어야 한다."""
        mock_gradient = MagicMock()
        mock_gradient.get_gradient.return_value = 0.0
        policy = ThrottlePolicy(gradient_calculator=mock_gradient)
        assert policy._gradient is mock_gradient

    def test_dampening_manager_none_by_default(self):
        """dampening_manager 미지정 시 None이어야 한다."""
        policy = ThrottlePolicy()
        assert policy._dampening_manager is None


# =============================================================================
# name 속성 계약 검증
# =============================================================================


class TestThrottlePolicyNameContract:
    """ThrottlePolicy.name 계약값 검증."""

    def test_name_is_throttle(self):
        """name 속성은 'throttle'이어야 한다."""
        policy = ThrottlePolicy()
        assert policy.name == "throttle"


# =============================================================================
# current_limit setter 동작 검증
# =============================================================================


class TestThrottlePolicyCurrentLimitBehavior:
    """current_limit setter의 min/max 바운드 동작 검증."""

    def test_set_limit_within_bounds(self):
        """min_limit~max_limit 범위 내 값은 그대로 설정되어야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        policy.current_limit = 250
        assert policy.current_limit == 250

    def test_set_limit_below_min_clamps_to_min(self):
        """min_limit 미만 값은 min_limit으로 클램핑되어야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        policy.current_limit = 1
        assert policy.current_limit == config.min_limit

    def test_set_limit_above_max_clamps_to_max(self):
        """max_limit 초과 값은 max_limit으로 클램핑되어야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        policy.current_limit = 9999
        assert policy.current_limit == config.max_limit

    def test_set_limit_syncs_engine(self):
        """current_limit 변경 시 내부 _engine.current_limit도 동기화되어야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        policy.current_limit = 200
        assert policy._engine.current_limit == 200


# =============================================================================
# gradient_frozen 속성 동작 검증
# =============================================================================


class TestThrottlePolicyGradientFrozenBehavior:
    """gradient_frozen 속성 동작 검증."""

    def test_gradient_frozen_default_false(self):
        """초기 상태에서 gradient_frozen은 False여야 한다."""
        policy = ThrottlePolicy()
        assert policy.gradient_frozen is False

    def test_gradient_frozen_settable(self):
        """gradient_frozen을 True로 설정할 수 있어야 한다."""
        policy = ThrottlePolicy()
        policy.gradient_frozen = True
        assert policy.gradient_frozen is True


# =============================================================================
# execute() 동작 검증
# =============================================================================


class TestThrottlePolicyExecuteBehavior:
    """ThrottlePolicy.execute() 동작 검증."""

    def test_execute_success_returns_value(self):
        """rate limit 내 요청은 함수 실행 결과를 반환해야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        result = policy.execute(lambda: 42)
        assert result.outcome == PolicyOutcome.SUCCESS
        assert result.value == 42

    def test_execute_success_includes_throttle_in_executed_policies(self):
        """성공 시 executed_policies에 'throttle'이 포함되어야 한다."""
        policy = ThrottlePolicy(config=ThrottleConfig(initial_limit=100))
        result = policy.execute(lambda: "ok")
        assert "throttle" in result.executed_policies

    def test_execute_success_records_duration(self):
        """성공 시 total_duration_ms가 0보다 크거나 같아야 한다."""
        policy = ThrottlePolicy(config=ThrottleConfig(initial_limit=100))
        result = policy.execute(lambda: "ok")
        assert result.total_duration_ms >= 0

    def test_execute_rejected_when_limit_exhausted(self):
        """rate limit 초과 시 REJECTED PolicyResult를 반환해야 한다."""
        config = ThrottleConfig(
            initial_limit=2,
            min_limit=1,
            max_limit=100,
            window_seconds=60,
        )
        policy = ThrottlePolicy(config=config)

        # 2번 허용
        r1 = policy.execute(lambda: "a")
        r2 = policy.execute(lambda: "b")
        assert r1.outcome == PolicyOutcome.SUCCESS
        assert r2.outcome == PolicyOutcome.SUCCESS

        # 3번째는 거부
        r3 = policy.execute(lambda: "c")
        assert r3.outcome == PolicyOutcome.REJECTED
        assert r3.value is None

    def test_execute_rejected_metadata_contains_policy_and_reason(self):
        """거부 시 metadata에 policy='throttle'과 reason이 있어야 한다."""
        config = ThrottleConfig(initial_limit=1, min_limit=1, max_limit=100)
        policy = ThrottlePolicy(config=config)
        policy.execute(lambda: "first")

        rejected = policy.execute(lambda: "second")
        assert rejected.metadata["policy"] == "throttle"
        assert "reason" in rejected.metadata

    def test_execute_function_exception_returns_failure(self):
        """함수가 예외를 발생시키면 FAILURE PolicyResult를 반환해야 한다."""
        policy = ThrottlePolicy(config=ThrottleConfig(initial_limit=100))

        def raise_error():
            raise ValueError("test error")

        result = policy.execute(raise_error)
        assert result.outcome == PolicyOutcome.FAILURE
        assert isinstance(result.error, ValueError)
        assert "throttle" in result.executed_policies

    def test_execute_with_context_throttle_key(self):
        """context.extra['throttle_key'] 지정 시 해당 키로 rate limit 적용."""
        config = ThrottleConfig(initial_limit=1, min_limit=1, max_limit=100)
        policy = ThrottlePolicy(config=config)

        ctx_a = PolicyContext(extra={"throttle_key": "key_a"})
        ctx_b = PolicyContext(extra={"throttle_key": "key_b"})

        # key_a는 1건 허용 후 거부
        r1 = policy.execute(lambda: "a", context=ctx_a)
        assert r1.outcome == PolicyOutcome.SUCCESS
        r2 = policy.execute(lambda: "a2", context=ctx_a)
        assert r2.outcome == PolicyOutcome.REJECTED

        # key_b는 별도 키이므로 1건 허용
        r3 = policy.execute(lambda: "b", context=ctx_b)
        assert r3.outcome == PolicyOutcome.SUCCESS

    def test_execute_passes_args_and_kwargs_to_func(self):
        """execute()가 *args, **kwargs를 함수에 올바르게 전달해야 한다."""
        policy = ThrottlePolicy(config=ThrottleConfig(initial_limit=100))

        def add(a, b, extra=0):
            return a + b + extra

        result = policy.execute(add, 3, 5, extra=10)
        assert result.value == 18


# =============================================================================
# check() 동작 검증
# =============================================================================


class TestThrottlePolicyCheckBehavior:
    """ThrottlePolicy.check() 순수 rate limit 검사 동작 검증."""

    def test_check_allowed(self):
        """limit 내 요청은 allowed=True인 ThrottleResult를 반환해야 한다."""
        policy = ThrottlePolicy(config=ThrottleConfig(initial_limit=100))
        result = policy.check("user_1")
        assert result.allowed is True
        assert result.remaining >= 0

    def test_check_rejected_when_limit_exceeded(self):
        """limit 초과 시 allowed=False인 ThrottleResult를 반환해야 한다."""
        config = ThrottleConfig(initial_limit=1, min_limit=1, max_limit=100)
        policy = ThrottlePolicy(config=config)
        policy.check("user_1")  # 1건 소비
        result = policy.check("user_1")
        assert result.allowed is False
        assert result.reason is not None


# =============================================================================
# record_response() 동작 검증
# =============================================================================


class TestThrottlePolicyRecordResponseBehavior:
    """ThrottlePolicy.record_response() 동작 검증."""

    def test_record_response_adds_gradient_sample(self):
        """record_response() 호출 시 gradient calculator에 샘플이 추가되어야 한다."""
        mock_gradient = MagicMock()
        mock_gradient.get_gradient.return_value = 0.0
        policy = ThrottlePolicy(gradient_calculator=mock_gradient)
        policy.record_response(50.0)
        mock_gradient.add_sample.assert_called_once_with(50.0)


# =============================================================================
# _resolve_throttle_key() 동작 검증
# =============================================================================


class TestThrottlePolicyResolveKeyBehavior:
    """_resolve_throttle_key() 키 결정 우선순위 검증."""

    def test_context_extra_throttle_key_takes_priority(self):
        """context.extra['throttle_key']가 최우선이어야 한다."""
        config = ThrottleConfig(service_name="svc_default")
        policy = ThrottlePolicy(config=config)
        ctx = PolicyContext(extra={"throttle_key": "custom_key"})
        assert policy._resolve_throttle_key(ctx) == "custom_key"

    def test_config_service_name_fallback(self):
        """context가 없으면 config.service_name을 사용해야 한다."""
        config = ThrottleConfig(service_name="my_service")
        policy = ThrottlePolicy(config=config)
        assert policy._resolve_throttle_key(None) == "my_service"

    def test_default_fallback(self):
        """service_name이 빈 문자열이면 'default'를 사용해야 한다."""
        config = ThrottleConfig(service_name="")
        policy = ThrottlePolicy(config=config)
        assert policy._resolve_throttle_key(None) == "default"

    def test_context_without_throttle_key_uses_service_name(self):
        """context.extra에 throttle_key가 없으면 service_name 사용."""
        config = ThrottleConfig(service_name="fallback_svc")
        policy = ThrottlePolicy(config=config)
        ctx = PolicyContext(extra={"other": "value"})
        assert policy._resolve_throttle_key(ctx) == "fallback_svc"


# =============================================================================
# _maybe_adjust_limit() SLA/Gradient 분기 동작 검증
# =============================================================================


class TestThrottlePolicyMaybeAdjustLimitBehavior:
    """_maybe_adjust_limit() SLA 임계값/Gradient 기반 limit 조정 동작 검증."""

    def test_sla_critical_reduces_limit_by_30_percent(self):
        """SLA Critical (rtt >= sla_critical_ms) 시 limit이 30% 감소해야 한다."""
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
            sla_warning_ms=200,
            sla_critical_ms=500,
        )
        policy = ThrottlePolicy(config=config)
        policy._maybe_adjust_limit(600.0)  # sla_critical_ms 이상
        # 100 * 0.7 = 70
        assert policy.current_limit == 70

    def test_sla_warning_reduces_limit_by_decrease_ratio(self):
        """SLA Warning (sla_warning_ms <= rtt < sla_critical_ms) 시 decrease_ratio 적용."""
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
            sla_warning_ms=200,
            sla_critical_ms=500,
            decrease_ratio=0.9,
        )
        policy = ThrottlePolicy(config=config)
        policy._maybe_adjust_limit(300.0)  # sla_warning_ms ~ sla_critical_ms
        # 100 * 0.9 = 90
        assert policy.current_limit == int(config.initial_limit * config.decrease_ratio)

    def test_gradient_positive_reduces_limit(self):
        """gradient > 0.1 (RTT 상승 추세) 시 limit이 감소해야 한다."""
        mock_gradient = MagicMock()
        mock_gradient.get_gradient.return_value = 0.2  # 상승 추세
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
            sla_warning_ms=200,
            sla_critical_ms=500,
            decrease_ratio=0.9,
        )
        policy = ThrottlePolicy(config=config, gradient_calculator=mock_gradient)
        policy._maybe_adjust_limit(50.0)  # SLA 임계값 미만 but gradient > 0.1
        assert policy.current_limit == int(config.initial_limit * config.decrease_ratio)

    def test_gradient_negative_increases_limit(self):
        """gradient < -0.05 (RTT 하강 추세) 시 limit이 증가해야 한다."""
        mock_gradient = MagicMock()
        mock_gradient.get_gradient.return_value = -0.1  # 하강 추세
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
            sla_warning_ms=200,
            sla_critical_ms=500,
            increase_step=1,
        )
        policy = ThrottlePolicy(config=config, gradient_calculator=mock_gradient)
        policy._maybe_adjust_limit(50.0)
        assert policy.current_limit == config.initial_limit + config.increase_step

    def test_gradient_frozen_skips_adjustment(self):
        """gradient_frozen=True 시 limit이 변경되지 않아야 한다."""
        mock_gradient = MagicMock()
        mock_gradient.get_gradient.return_value = 0.5
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
            sla_warning_ms=200,
            sla_critical_ms=500,
        )
        policy = ThrottlePolicy(config=config, gradient_calculator=mock_gradient)
        policy.gradient_frozen = True
        policy._maybe_adjust_limit(600.0)
        assert policy.current_limit == config.initial_limit

    def test_limit_cannot_go_below_min(self):
        """반복적 SLA Critical에도 limit이 min_limit 아래로 내려가지 않아야 한다."""
        config = ThrottleConfig(
            initial_limit=20,
            min_limit=10,
            max_limit=500,
            sla_critical_ms=100,
        )
        policy = ThrottlePolicy(config=config)
        for _ in range(50):
            policy._maybe_adjust_limit(200.0)
        assert policy.current_limit >= config.min_limit

    def test_limit_cannot_exceed_max(self):
        """반복적 증가에도 limit이 max_limit을 초과하지 않아야 한다."""
        mock_gradient = MagicMock()
        mock_gradient.get_gradient.return_value = -0.1
        config = ThrottleConfig(
            initial_limit=490,
            min_limit=10,
            max_limit=500,
            sla_warning_ms=200,
            sla_critical_ms=500,
            increase_step=5,
        )
        policy = ThrottlePolicy(config=config, gradient_calculator=mock_gradient)
        for _ in range(50):
            policy._maybe_adjust_limit(10.0)
        assert policy.current_limit <= config.max_limit

    def test_engine_limit_syncs_after_adjustment(self):
        """limit 조정 후 내부 엔진 limit도 동기화되어야 한다."""
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
            sla_critical_ms=200,
        )
        policy = ThrottlePolicy(config=config)
        policy._maybe_adjust_limit(300.0)
        assert policy._engine.current_limit == policy.current_limit


# =============================================================================
# Dampening Cap 동작 검증
# =============================================================================


class TestThrottlePolicyDampeningCapBehavior:
    """_apply_dampening_cap() Recovery Dampening Cap 동작 검증."""

    def test_no_dampening_manager_returns_original(self):
        """dampening_manager가 None이면 원래 limit을 반환해야 한다."""
        policy = ThrottlePolicy()
        assert policy._apply_dampening_cap(200) == 200

    def test_dampening_inactive_returns_original(self):
        """Dampening 비활성 시 원래 limit을 반환해야 한다."""
        mock_dampening = MagicMock()
        mock_dampening.is_recovery_active.return_value = False
        config = ThrottleConfig(service_name="test_svc")
        policy = ThrottlePolicy(config=config, dampening_manager=mock_dampening)
        assert policy._apply_dampening_cap(200) == 200

    def test_dampening_active_caps_limit(self):
        """Dampening 활성 시 dampened_limit 이하로 Cap이 적용되어야 한다."""
        mock_dampening = MagicMock()
        mock_dampening.is_recovery_active.return_value = True
        mock_dampening.get_recovery_state.return_value = {"target_limit": 100}
        mock_dampening.get_current_multiplier.return_value = 0.8  # 1단계

        config = ThrottleConfig(service_name="test_svc")
        policy = ThrottlePolicy(config=config, dampening_manager=mock_dampening)

        # dampened_limit = 100 * 0.8 = 80
        assert policy._apply_dampening_cap(120) == 80  # 120 > 80 → 80으로 cap

    def test_dampening_active_allows_below_cap(self):
        """new_limit이 dampened_limit 이하이면 원래 값을 반환해야 한다."""
        mock_dampening = MagicMock()
        mock_dampening.is_recovery_active.return_value = True
        mock_dampening.get_recovery_state.return_value = {"target_limit": 100}
        mock_dampening.get_current_multiplier.return_value = 0.9  # 2단계

        config = ThrottleConfig(service_name="test_svc")
        policy = ThrottlePolicy(config=config, dampening_manager=mock_dampening)

        # dampened_limit = 100 * 0.9 = 90
        assert policy._apply_dampening_cap(85) == 85  # 85 < 90 → 그대로

    def test_dampening_recovery_state_none_returns_original(self):
        """get_recovery_state()가 None이면 원래 limit을 반환해야 한다."""
        mock_dampening = MagicMock()
        mock_dampening.is_recovery_active.return_value = True
        mock_dampening.get_recovery_state.return_value = None

        config = ThrottleConfig(service_name="test_svc")
        policy = ThrottlePolicy(config=config, dampening_manager=mock_dampening)
        assert policy._apply_dampening_cap(200) == 200


# =============================================================================
# reduce_limit_for_429() 동작 검증
# =============================================================================


class TestThrottlePolicyReduceLimitFor429Behavior:
    """reduce_limit_for_429() 동작 검증."""

    def test_429_with_default_50_percent_reduction(self):
        """기본 50% 감소가 적용되어야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)

        event = MagicMock()
        event.data = {"reduction_percent": 50.0}
        policy.reduce_limit_for_429(event)
        assert policy.current_limit == 50

    def test_429_with_custom_reduction_percent(self):
        """사용자 지정 감소율이 적용되어야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)

        event = MagicMock()
        event.data = {"reduction_percent": 30.0}
        policy.reduce_limit_for_429(event)
        assert policy.current_limit == 70  # 100 * 0.7

    def test_429_clamps_to_min_limit(self):
        """감소 후 min_limit 미만이면 min_limit으로 클램핑되어야 한다."""
        config = ThrottleConfig(initial_limit=20, min_limit=15, max_limit=500)
        policy = ThrottlePolicy(config=config)

        event = MagicMock()
        event.data = {"reduction_percent": 50.0}
        policy.reduce_limit_for_429(event)
        assert policy.current_limit == config.min_limit

    def test_429_dict_event_without_data_attribute(self):
        """event가 dict일 때도 동작해야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)

        event = {"reduction_percent": 40.0}
        policy.reduce_limit_for_429(event)
        assert policy.current_limit == 60  # 100 * 0.6


# =============================================================================
# adjust_limit_for_error_budget() 동작 검증
# =============================================================================


class TestThrottlePolicyAdjustErrorBudgetBehavior:
    """adjust_limit_for_error_budget() 동작 검증."""

    def test_warning_reduces_by_20_percent(self):
        """multiplier=0.8 (Warning) 시 initial_limit의 80% 적용."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        policy.adjust_limit_for_error_budget(multiplier=0.8)
        assert policy.current_limit == 80

    def test_critical_reduces_by_50_percent(self):
        """multiplier=0.5 (Critical) 시 initial_limit의 50% 적용."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        policy.adjust_limit_for_error_budget(multiplier=0.5)
        assert policy.current_limit == 50

    def test_clamps_to_min_limit(self):
        """감소 후 min_limit 미만이면 min_limit으로 클램핑."""
        config = ThrottleConfig(initial_limit=20, min_limit=15, max_limit=500)
        policy = ThrottlePolicy(config=config)
        policy.adjust_limit_for_error_budget(multiplier=0.5)
        assert policy.current_limit == config.min_limit


# =============================================================================
# adjust_limit_for_shedding() 동작 검증
# =============================================================================


class TestThrottlePolicyAdjustSheddingBehavior:
    """adjust_limit_for_shedding() 동작 검증."""

    def test_suggested_lower_than_current_applies(self):
        """suggested_limit < current_limit 시 적용되어야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        policy.adjust_limit_for_shedding(suggested_limit=60)
        assert policy.current_limit == 60

    def test_suggested_higher_than_current_no_change(self):
        """suggested_limit >= current_limit 시 변경하지 않아야 한다."""
        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=500)
        policy = ThrottlePolicy(config=config)
        policy.adjust_limit_for_shedding(suggested_limit=200)
        assert policy.current_limit == config.initial_limit


# =============================================================================
# start_recovery_dampening() 동작 검증
# =============================================================================


class TestThrottlePolicyRecoveryDampeningBehavior:
    """start_recovery_dampening() 동작 검증."""

    def test_no_dampening_manager_noop(self):
        """dampening_manager가 None이면 아무 동작도 하지 않아야 한다."""
        policy = ThrottlePolicy()
        original = policy.current_limit
        policy.start_recovery_dampening()
        assert policy.current_limit == original

    def test_dampening_start_sets_phase_1_limit(self):
        """dampening_manager가 있으면 phase_1 limit으로 설정되어야 한다."""
        mock_dampening = MagicMock()
        mock_dampening.start_recovery.return_value = 80

        config = ThrottleConfig(initial_limit=100, service_name="test_svc")
        policy = ThrottlePolicy(config=config, dampening_manager=mock_dampening)
        policy.start_recovery_dampening()

        mock_dampening.start_recovery.assert_called_once_with(
            service_name="test_svc",
            target_limit=config.initial_limit,
            current_limit=config.initial_limit,
        )
        assert policy.current_limit == 80

    def test_dampening_start_with_custom_target(self):
        """target_limit 지정 시 해당 값으로 복구 목표 설정."""
        mock_dampening = MagicMock()
        mock_dampening.start_recovery.return_value = 120

        config = ThrottleConfig(initial_limit=100, service_name="test_svc", max_limit=500)
        policy = ThrottlePolicy(config=config, dampening_manager=mock_dampening)
        policy.start_recovery_dampening(target_limit=150)

        mock_dampening.start_recovery.assert_called_once_with(
            service_name="test_svc",
            target_limit=150,
            current_limit=config.initial_limit,
        )
