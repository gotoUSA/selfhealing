"""
standard_pipeline / ha_pipeline 프리셋 단위 테스트 (#231, #234).

테스트 대상:
- resilience/policies/presets.py (standard_pipeline, ha_pipeline, _build_fallback_policy)

UNIT_TEST_GUIDELINES.md 준수:
- 동작 검증(Behavior): 소스 참조 (PolicyComposer, Guard/Hook/Sink 타입)
- conftest.py 배치: 1개 파일 전용 fixture → 파일 내부 (§5.1)

Note:
  presets.py 내부에서 RetryPolicy, BulkheadPolicy, HedgingPolicy 등을
  lazy import하므로, 해당 의존성이 사용 가능한 환경에서만 테스트한다.
  의존성 미설치 시 ImportError로 테스트 skip.
"""

from __future__ import annotations

from selfhealing.resilience.policies.composer import PolicyComposer
from selfhealing.resilience.policies.fallback import FallbackPolicy
from selfhealing.resilience.policies.guards.error_budget import ErrorBudgetGuard
from selfhealing.resilience.policies.guards.kill_switch import KillSwitchGuard
from selfhealing.resilience.policies.hooks.audit import AuditHook
from selfhealing.resilience.policies.hooks.metrics import MetricsHook
from selfhealing.resilience.policies.presets import (
    _build_fallback_policy,
    ha_pipeline,
    standard_pipeline,
)

# =============================================================================
# 동작 검증 — standard_pipeline
# =============================================================================


class TestStandardPipelineBehavior:
    """standard_pipeline() 동작 검증."""

    def test_returns_policy_composer(self):
        """standard_pipeline()은 PolicyComposer 인스턴스를 반환한다."""
        pipeline = standard_pipeline("test_service")
        assert isinstance(pipeline, PolicyComposer)

    def test_has_retry_policy(self):
        """RetryPolicy가 _policies에 포함되어 있다."""
        pipeline = standard_pipeline("test_service", max_retries=2)
        assert len(pipeline._policies) == 1
        assert pipeline._policies[0].name == "retry"

    def test_has_kill_switch_guard(self):
        """KillSwitchGuard가 _guards에 포함되어 있다."""
        pipeline = standard_pipeline("test_service")
        guard_types = [type(g) for g in pipeline._guards]
        assert KillSwitchGuard in guard_types

    def test_has_error_budget_guard(self):
        """ErrorBudgetGuard가 _guards에 포함되어 있다."""
        pipeline = standard_pipeline("test_service")
        guard_types = [type(g) for g in pipeline._guards]
        assert ErrorBudgetGuard in guard_types

    def test_has_audit_hook(self):
        """AuditHook이 _hooks에 포함되어 있다."""
        pipeline = standard_pipeline("test_service")
        hook_types = [type(h) for h in pipeline._hooks]
        assert AuditHook in hook_types

    def test_has_dlq_sink(self):
        """DLQSink가 _sinks에 포함되어 있다."""
        from selfhealing.services.retry_handler.sinks import DLQSink

        pipeline = standard_pipeline("test_service")
        sink_types = [type(s) for s in pipeline._sinks]
        assert DLQSink in sink_types

    def test_custom_max_retries(self):
        """max_retries 파라미터가 RetryPolicy에 전달된다."""
        pipeline = standard_pipeline("test_service", max_retries=5)
        retry_policy = pipeline._policies[0]
        assert retry_policy._config.max_attempts == 5

    def test_custom_domain(self):
        """domain 파라미터가 RetryPolicy config에 전달된다."""
        pipeline = standard_pipeline("test_service", domain="payment")
        retry_policy = pipeline._policies[0]
        assert retry_policy._config.domain == "payment"


# =============================================================================
# 동작 검증 — ha_pipeline
# =============================================================================


class TestHaPipelineBehavior:
    """ha_pipeline() 동작 검증."""

    def test_returns_policy_composer(self):
        """ha_pipeline()은 PolicyComposer 인스턴스를 반환한다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
        )
        assert isinstance(pipeline, PolicyComposer)

    def test_has_three_policies(self):
        """RetryPolicy + BulkheadPolicy + HedgingPolicy 총 3개 Policy가 포함된다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
        )
        assert len(pipeline._policies) == 3

    def test_policy_order(self):
        """Policy 순서: Retry(바깥) → Bulkhead → Hedging(안쪽)."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
        )
        policy_names = [p.name for p in pipeline._policies]
        assert policy_names[0] == "retry"
        assert policy_names[1] == "bulkhead"
        assert policy_names[2] == "hedging"

    def test_has_both_guards(self):
        """KillSwitchGuard, ErrorBudgetGuard가 모두 포함된다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
        )
        guard_types = [type(g) for g in pipeline._guards]
        assert KillSwitchGuard in guard_types
        assert ErrorBudgetGuard in guard_types

    def test_has_audit_and_metrics_hooks(self):
        """AuditHook과 MetricsHook이 포함된다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
        )
        hook_types = [type(h) for h in pipeline._hooks]
        assert AuditHook in hook_types
        assert MetricsHook in hook_types

    def test_has_dlq_sink(self):
        """DLQSink가 포함된다."""
        from selfhealing.services.retry_handler.sinks import DLQSink

        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
        )
        sink_types = [type(s) for s in pipeline._sinks]
        assert DLQSink in sink_types

    def test_custom_max_retries(self):
        """max_retries 파라미터가 RetryPolicy에 전달된다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
            max_retries=1,
        )
        retry_policy = pipeline._policies[0]
        assert retry_policy._config.max_attempts == 1


# =============================================================================
# 동작 검증 — _build_fallback_policy (#234)
# =============================================================================


class TestBuildFallbackPolicyBehavior:
    """_build_fallback_policy() 동작 검증."""

    def test_all_none_returns_none(self):
        """모든 파라미터가 None이면 None을 반환한다."""
        result = _build_fallback_policy(
            fallback_chain=None,
            fallback_fn=None,
            fallback_default=None,
        )
        assert result is None

    def test_fallback_default_only(self):
        """fallback_default만 전달하면 FallbackPolicy를 반환한다."""
        result = _build_fallback_policy(
            fallback_chain=None,
            fallback_fn=None,
            fallback_default={"status": "degraded"},
        )
        assert isinstance(result, FallbackPolicy)
        assert result._default_value == {"status": "degraded"}

    def test_fallback_fn_only(self):
        """fallback_fn만 전달하면 FallbackPolicy를 반환한다."""
        fn = lambda: "fallback_value"
        result = _build_fallback_policy(
            fallback_chain=None,
            fallback_fn=fn,
            fallback_default=None,
        )
        assert isinstance(result, FallbackPolicy)
        assert result._fallback_fn is fn

    def test_fallback_chain_only(self):
        """fallback_chain만 전달하면 FallbackPolicy를 반환한다."""
        chain = [lambda: "first", lambda: "second"]
        result = _build_fallback_policy(
            fallback_chain=chain,
            fallback_fn=None,
            fallback_default=None,
        )
        assert isinstance(result, FallbackPolicy)
        assert result._fallback_chain == chain

    def test_all_three_params(self):
        """3단계 파라미터 모두 전달 시 FallbackPolicy에 모두 적용된다."""
        chain = [lambda: "chain"]
        fn = lambda: "fn"
        default = {"status": "degraded"}

        result = _build_fallback_policy(
            fallback_chain=chain,
            fallback_fn=fn,
            fallback_default=default,
        )
        assert isinstance(result, FallbackPolicy)
        assert result._fallback_chain == chain
        assert result._fallback_fn is fn
        assert result._default_value == default

    def test_returned_policy_name_is_fallback(self):
        """반환된 FallbackPolicy의 name은 'fallback'이다."""
        result = _build_fallback_policy(
            fallback_chain=None,
            fallback_fn=None,
            fallback_default="default",
        )
        assert result.name == "fallback"


# =============================================================================
# 동작 검증 — standard_pipeline Fallback 통합 (#234)
# =============================================================================


class TestStandardPipelineFallbackBehavior:
    """standard_pipeline() Fallback 파라미터 동작 검증 (#234)."""

    def test_no_fallback_params_excludes_fallback_policy(self):
        """Fallback 파라미터 없으면 FallbackPolicy가 _policies에 포함되지 않는다."""
        pipeline = standard_pipeline("test_service")
        policy_names = [p.name for p in pipeline._policies]
        assert "fallback" not in policy_names

    def test_fallback_default_adds_fallback_policy(self):
        """fallback_default 전달 시 FallbackPolicy가 _policies에 추가된다."""
        pipeline = standard_pipeline(
            "test_service",
            fallback_default={"status": "degraded"},
        )
        policy_names = [p.name for p in pipeline._policies]
        assert "fallback" in policy_names

    def test_fallback_fn_adds_fallback_policy(self):
        """fallback_fn 전달 시 FallbackPolicy가 _policies에 추가된다."""
        pipeline = standard_pipeline(
            "test_service",
            fallback_fn=lambda: "backup",
        )
        policy_names = [p.name for p in pipeline._policies]
        assert "fallback" in policy_names

    def test_fallback_chain_adds_fallback_policy(self):
        """fallback_chain 전달 시 FallbackPolicy가 _policies에 추가된다."""
        pipeline = standard_pipeline(
            "test_service",
            fallback_chain=[lambda: "first", lambda: "second"],
        )
        policy_names = [p.name for p in pipeline._policies]
        assert "fallback" in policy_names

    def test_fallback_policy_is_last_in_policies(self):
        """FallbackPolicy는 _policies 리스트의 마지막(가장 바깥쪽)에 배치된다."""
        pipeline = standard_pipeline(
            "test_service",
            fallback_default={"status": "degraded"},
        )
        last_policy = pipeline._policies[-1]
        assert last_policy.name == "fallback"

    def test_fallback_preserves_retry_policy(self):
        """Fallback 추가 시 RetryPolicy는 여전히 첫 번째 Policy이다."""
        pipeline = standard_pipeline(
            "test_service",
            max_retries=3,
            fallback_default={"status": "degraded"},
        )
        assert pipeline._policies[0].name == "retry"

    def test_fallback_default_value_propagated(self):
        """fallback_default 값이 FallbackPolicy._default_value에 전달된다."""
        expected_default = {"status": "degraded"}
        pipeline = standard_pipeline(
            "test_service",
            fallback_default=expected_default,
        )
        fallback = pipeline._policies[-1]
        assert fallback._default_value == expected_default

    def test_fallback_with_all_three_params(self):
        """3단계 Fallback 파라미터 모두 전달 시 FallbackPolicy에 반영된다."""
        chain = [lambda: "chain_value"]
        fn = lambda: "fn_value"
        default = {"status": "degraded"}

        pipeline = standard_pipeline(
            "test_service",
            fallback_chain=chain,
            fallback_fn=fn,
            fallback_default=default,
        )
        fallback = pipeline._policies[-1]
        assert fallback._fallback_chain == chain
        assert fallback._fallback_fn is fn
        assert fallback._default_value == default

    def test_guards_preserved_with_fallback(self):
        """Fallback 추가 시 Guard(KillSwitch, ErrorBudget)는 유지된다."""
        pipeline = standard_pipeline(
            "test_service",
            fallback_default={"status": "degraded"},
        )
        guard_types = [type(g) for g in pipeline._guards]
        assert KillSwitchGuard in guard_types
        assert ErrorBudgetGuard in guard_types

    def test_policy_count_with_fallback(self):
        """Fallback 추가 시 _policies 개수는 2 (Retry + Fallback)이다."""
        pipeline = standard_pipeline(
            "test_service",
            fallback_default={"status": "degraded"},
        )
        assert len(pipeline._policies) == 2


# =============================================================================
# 동작 검증 — ha_pipeline Fallback 통합 (#234)
# =============================================================================


class TestHaPipelineFallbackBehavior:
    """ha_pipeline() Fallback 파라미터 동작 검증 (#234)."""

    def test_no_fallback_params_excludes_fallback_policy(self):
        """Fallback 파라미터 없으면 FallbackPolicy가 _policies에 포함되지 않는다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
        )
        policy_names = [p.name for p in pipeline._policies]
        assert "fallback" not in policy_names

    def test_fallback_default_adds_fallback_policy(self):
        """fallback_default 전달 시 FallbackPolicy가 _policies에 추가된다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
            fallback_default={"status": "degraded"},
        )
        policy_names = [p.name for p in pipeline._policies]
        assert "fallback" in policy_names

    def test_fallback_policy_is_last_in_policies(self):
        """FallbackPolicy는 _policies 리스트의 마지막(가장 바깥쪽)에 배치된다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
            fallback_default={"status": "degraded"},
        )
        last_policy = pipeline._policies[-1]
        assert last_policy.name == "fallback"

    def test_original_three_policies_preserved_with_fallback(self):
        """Fallback 추가 시 기존 3개 Policy(Retry, Bulkhead, Hedging) 순서 유지."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
            fallback_default={"status": "degraded"},
        )
        policy_names = [p.name for p in pipeline._policies]
        assert policy_names[0] == "retry"
        assert policy_names[1] == "bulkhead"
        assert policy_names[2] == "hedging"
        assert policy_names[3] == "fallback"

    def test_policy_count_with_fallback(self):
        """Fallback 추가 시 _policies 개수는 4 (Retry + Bulkhead + Hedging + Fallback)이다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
            fallback_default={"status": "degraded"},
        )
        assert len(pipeline._policies) == 4

    def test_fallback_chain_propagated(self):
        """fallback_chain이 FallbackPolicy._fallback_chain에 전달된다."""
        chain = [lambda: "first", lambda: "second"]
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
            fallback_chain=chain,
        )
        fallback = pipeline._policies[-1]
        assert fallback._fallback_chain == chain

    def test_guards_preserved_with_fallback(self):
        """Fallback 추가 시 Guard(KillSwitch, ErrorBudget)는 유지된다."""
        pipeline = ha_pipeline(
            "test_service",
            candidates=[lambda: "alt"],
            fallback_default={"status": "degraded"},
        )
        guard_types = [type(g) for g in pipeline._guards]
        assert KillSwitchGuard in guard_types
        assert ErrorBudgetGuard in guard_types
