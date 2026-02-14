"""
standard_pipeline / ha_pipeline 프리셋 단위 테스트 (#231).

테스트 대상:
- resilience/policies/presets.py (standard_pipeline, ha_pipeline)

UNIT_TEST_GUIDELINES.md 준수:
- 동작 검증(Behavior): 소스 참조 (PolicyComposer, Guard/Hook/Sink 타입)
- conftest.py 배치: 1개 파일 전용 fixture → 파일 내부 (§5.1)

Note:
  presets.py 내부에서 RetryPolicy, BulkheadPolicy, HedgingPolicy 등을
  lazy import하므로, 해당 의존성이 사용 가능한 환경에서만 테스트한다.
  의존성 미설치 시 ImportError로 테스트 skip.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.resilience.policies.composer import PolicyComposer
from selfhealing.resilience.policies.guards.error_budget import ErrorBudgetGuard
from selfhealing.resilience.policies.guards.kill_switch import KillSwitchGuard
from selfhealing.resilience.policies.hooks.audit import AuditHook
from selfhealing.resilience.policies.hooks.metrics import MetricsHook
from selfhealing.resilience.policies.presets import ha_pipeline, standard_pipeline


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
