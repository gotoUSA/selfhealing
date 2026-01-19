"""
Canary Audit 단위 테스트.

테스트 대상:
1. log_canary_action() - 카나리 액션 로깅
2. log_canary_error() - 카나리 오류 로깅

Note: 실제 audit 구현은 내부적으로 로거를 사용하며,
      이 테스트는 함수 호출이 오류 없이 수행되는지 확인합니다.

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
"""

import pytest
from unittest.mock import MagicMock, patch

from selfhealing.services.canary.models import (
    CanaryState,
    CanaryStage,
    CanaryRollout,
    CanaryMetrics,
    PassCriteria,
)
from selfhealing.services.canary.audit import (
    log_canary_action,
    CANARY_ACTIONS,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_rollout():
    """샘플 롤아웃."""
    return CanaryRollout(
        id="canary-123",
        config_type="circuit_breaker",
        previous_values={"failure_threshold": 5},
        new_values={"failure_threshold": 3},
        state=CanaryState.CANARY,
        current_stage_index=0,
        stages=[
            CanaryStage(name="canary", clusters=["seoul-canary"], percentage=10.0),
            CanaryStage(name="full", clusters=["tokyo"], percentage=100.0),
        ],
        created_by="admin@example.com",
    )


@pytest.fixture
def sample_metrics():
    """샘플 메트릭."""
    return CanaryMetrics(
        cluster="seoul-canary",
        stage_name="canary",
        error_rate_before=0.01,
        error_rate_after=0.02,
        latency_p99_before=100.0,
        latency_p99_after=150.0,
        requests_total=1000,
        errors_total=20,
    )


@pytest.fixture
def sample_criteria():
    """샘플 기준."""
    return PassCriteria(
        error_rate_absolute_max=0.05,
        latency_p99_delta_pct=0.2,
        min_requests_required=100,
    )


# =============================================================================
# Test: log_canary_action
# =============================================================================


class TestLogCanaryAction:
    """log_canary_action 테스트."""

    def test_log_canary_action_no_error(self, sample_rollout):
        """오류 없이 실행되는지 확인."""
        # 함수가 예외 없이 실행되면 성공
        log_canary_action(
            action="start",
            rollout=sample_rollout,
        )

    def test_log_canary_action_with_safety_check(self, sample_rollout):
        """안전 검사 결과 포함."""
        log_canary_action(
            action="start",
            rollout=sample_rollout,
            safety_check_result={
                "chaos_guard": "passed",
                "config_lock": "acquired",
            },
        )

    def test_log_canary_action_with_additional_context(self, sample_rollout):
        """추가 컨텍스트 포함."""
        log_canary_action(
            action="rollback",
            rollout=sample_rollout,
            additional_context={
                "reason": "High error rate",
                "triggerd_by": "auto_rollback",
            },
        )

    def test_log_canary_action_all_actions(self, sample_rollout):
        """모든 액션 타입이 처리 가능한지 확인."""
        for action in CANARY_ACTIONS:
            log_canary_action(
                action=action,
                rollout=sample_rollout,
            )


# =============================================================================
# Test: CANARY_ACTIONS
# =============================================================================


class TestCanaryActions:
    """CANARY_ACTIONS 상수 테스트."""

    def test_contains_core_actions(self):
        """핵심 액션이 포함되어 있는지 확인."""
        assert "create" in CANARY_ACTIONS
        assert "start" in CANARY_ACTIONS
        assert "promote" in CANARY_ACTIONS
        assert "rollback" in CANARY_ACTIONS
        assert "pause" in CANARY_ACTIONS
        assert "resume" in CANARY_ACTIONS
        assert "complete" in CANARY_ACTIONS

    def test_contains_emergency_actions(self):
        """긴급 액션이 포함되어 있는지 확인."""
        assert "panic_rollback" in CANARY_ACTIONS
        assert "force_promote" in CANARY_ACTIONS

    def test_contains_cancel_action(self):
        """취소 액션이 포함되어 있는지 확인."""
        assert "cancel" in CANARY_ACTIONS
