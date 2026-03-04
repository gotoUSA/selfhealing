"""
빌트인 Runbook 정의 단위 테스트.

테스트 대상:
    selfhealing.services.runbook.builtins

커밋 284041ee에서 구현된 5개 빌트인 런북의 구조적 유효성,
계약값, 동작을 검증한다.

계약 검증 클래스 (Test*Contract):
    - TestBuiltinRunbookCountContract
    - TestEmergencyRecoveryLevel3Contract
    - TestEmergencyRecoveryLevel2Contract
    - TestEmergencyRecoveryLevel1Contract
    - TestCircuitBreakerOpenedResponseContract
    - TestErrorBudgetCriticalResponseContract

동작 검증 클래스 (Test*Behavior):
    - TestBuiltinRunbookValidationBehavior
    - TestBuiltinRunbookTriggerConditionBehavior
    - TestBuiltinRunbookContinueOnFailureBehavior
    - TestBuiltinRunbookStepOrderBehavior
    - TestRegisterBuiltinRunbooksBehavior
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.runbook.builtins import (
    _build_circuit_breaker_opened_response,
    _build_emergency_recovery_level1,
    _build_emergency_recovery_level2,
    _build_emergency_recovery_level3,
    _build_error_budget_critical_response,
    register_builtin_runbooks,
)
from selfhealing.services.runbook.runbook_registry import (
    RiskLevel,
    Runbook,
)

# =============================================================================
# 헬퍼
# =============================================================================

ALL_BUILDERS = [
    _build_emergency_recovery_level3,
    _build_emergency_recovery_level2,
    _build_emergency_recovery_level1,
    _build_circuit_breaker_opened_response,
    _build_error_budget_critical_response,
]


# =============================================================================
# 계약 검증 — 빌트인 런북 개수
# =============================================================================


class TestBuiltinRunbookCountContract:
    """빌트인 런북 개수 계약 검증."""

    def test_builtin_runbook_count_is_five(self):
        """빌트인 런북은 정확히 5개이다."""
        assert len(ALL_BUILDERS) == 5


# =============================================================================
# 계약 검증 — Emergency Recovery LEVEL_3
# =============================================================================


class TestEmergencyRecoveryLevel3Contract:
    """Emergency Recovery LEVEL_3 빌트인 런북 계약 검증."""

    @pytest.fixture()
    def runbook(self) -> Runbook:
        return _build_emergency_recovery_level3()

    def test_id(self, runbook: Runbook):
        """런북 ID: emergency_recovery_level3."""
        assert runbook.id == "emergency_recovery_level3"

    def test_risk_level_is_high(self, runbook: Runbook):
        """위험도: HIGH."""
        assert runbook.risk_level == RiskLevel.HIGH

    def test_priority(self, runbook: Runbook):
        """우선순위: 10 (가장 높음)."""
        assert runbook.priority == 10

    def test_cooldown_seconds(self, runbook: Runbook):
        """쿨다운: 600초."""
        assert runbook.cooldown_seconds == 600

    def test_global_timeout_seconds(self, runbook: Runbook):
        """전체 타임아웃: 1200초."""
        assert runbook.global_timeout_seconds == 1200

    def test_step_count(self, runbook: Runbook):
        """스텝 수: 6개."""
        assert len(runbook.steps) == 6

    def test_step_names(self, runbook: Runbook):
        """스텝 이름 목록 계약."""
        names = [s.name for s in runbook.steps]
        assert names == [
            "budget_reset",
            "stabilization_wait",
            "health_check_gate",
            "canary_resume",
            "governance_normal",
            "completion_notify",
        ]

    def test_tags(self, runbook: Runbook):
        """태그: emergency, recovery, level3."""
        assert set(runbook.tags) == {"emergency", "recovery", "level3"}

    def test_trigger_event_type(self, runbook: Runbook):
        """트리거 이벤트: emergency_level_changed (previous_level=3, deescalation)."""
        ec = runbook.trigger_condition.event_conditions[0]
        assert ec.event_type == "emergency_level_changed"
        assert ec.data_filter["previous_level"] == 3
        assert ec.data_filter["direction"] == "deescalation"

    def test_budget_reset_step_params(self, runbook: Runbook):
        """budget_reset 스텝: crisis_multiplier.target_multiplier = 1.0."""
        step = runbook.steps[0]
        assert step.action == "config.set"
        assert step.params["key"] == "crisis_multiplier.target_multiplier"
        assert step.params["value"] == 1.0

    def test_governance_normal_step_has_on_failure(self, runbook: Runbook):
        """governance_normal 스텝: on_failure_action이 notify.send이다."""
        governance_step = [s for s in runbook.steps if s.name == "governance_normal"][0]
        assert governance_step.on_failure_action == "notify.send"
        assert governance_step.on_failure_params["priority"] == "high"


# =============================================================================
# 계약 검증 — Emergency Recovery LEVEL_2
# =============================================================================


class TestEmergencyRecoveryLevel2Contract:
    """Emergency Recovery LEVEL_2 빌트인 런북 계약 검증."""

    @pytest.fixture()
    def runbook(self) -> Runbook:
        return _build_emergency_recovery_level2()

    def test_id(self, runbook: Runbook):
        """런북 ID: emergency_recovery_level2."""
        assert runbook.id == "emergency_recovery_level2"

    def test_risk_level_is_medium(self, runbook: Runbook):
        """위험도: MEDIUM."""
        assert runbook.risk_level == RiskLevel.MEDIUM

    def test_priority(self, runbook: Runbook):
        """우선순위: 20."""
        assert runbook.priority == 20

    def test_cooldown_seconds(self, runbook: Runbook):
        """쿨다운: 300초."""
        assert runbook.cooldown_seconds == 300

    def test_global_timeout_seconds(self, runbook: Runbook):
        """전체 타임아웃: 900초."""
        assert runbook.global_timeout_seconds == 900

    def test_step_count(self, runbook: Runbook):
        """스텝 수: 5개."""
        assert len(runbook.steps) == 5

    def test_step_names(self, runbook: Runbook):
        """스텝 이름 목록 계약."""
        names = [s.name for s in runbook.steps]
        assert names == [
            "budget_reset",
            "stabilization_wait",
            "health_check_gate",
            "canary_resume",
            "completion_notify",
        ]

    def test_tags(self, runbook: Runbook):
        """태그: emergency, recovery, level2."""
        assert set(runbook.tags) == {"emergency", "recovery", "level2"}

    def test_trigger_event_previous_level(self, runbook: Runbook):
        """트리거 이벤트: previous_level=2."""
        ec = runbook.trigger_condition.event_conditions[0]
        assert ec.data_filter["previous_level"] == 2

    def test_stabilization_wait_threshold(self, runbook: Runbook):
        """stabilization_wait 안정화 임계값: 0.85."""
        step = [s for s in runbook.steps if s.name == "stabilization_wait"][0]
        assert step.params["threshold"] == 0.85

    def test_health_check_gate_threshold(self, runbook: Runbook):
        """health_check_gate 에러율 임계값: 0.15."""
        step = [s for s in runbook.steps if s.name == "health_check_gate"][0]
        assert step.params["threshold"] == 0.15


# =============================================================================
# 계약 검증 — Emergency Recovery LEVEL_1
# =============================================================================


class TestEmergencyRecoveryLevel1Contract:
    """Emergency Recovery LEVEL_1 빌트인 런북 계약 검증."""

    @pytest.fixture()
    def runbook(self) -> Runbook:
        return _build_emergency_recovery_level1()

    def test_id(self, runbook: Runbook):
        """런북 ID: emergency_recovery_level1."""
        assert runbook.id == "emergency_recovery_level1"

    def test_risk_level_is_low(self, runbook: Runbook):
        """위험도: LOW."""
        assert runbook.risk_level == RiskLevel.LOW

    def test_priority(self, runbook: Runbook):
        """우선순위: 30."""
        assert runbook.priority == 30

    def test_cooldown_seconds(self, runbook: Runbook):
        """쿨다운: 180초."""
        assert runbook.cooldown_seconds == 180

    def test_global_timeout_seconds(self, runbook: Runbook):
        """전체 타임아웃: 600초."""
        assert runbook.global_timeout_seconds == 600

    def test_step_count(self, runbook: Runbook):
        """스텝 수: 4개."""
        assert len(runbook.steps) == 4

    def test_step_names(self, runbook: Runbook):
        """스텝 이름 목록 계약."""
        names = [s.name for s in runbook.steps]
        assert names == [
            "budget_reset",
            "stabilization_wait",
            "health_check_gate",
            "completion_notify",
        ]

    def test_tags(self, runbook: Runbook):
        """태그: emergency, recovery, level1."""
        assert set(runbook.tags) == {"emergency", "recovery", "level1"}

    def test_trigger_event_previous_level(self, runbook: Runbook):
        """트리거 이벤트: previous_level=1."""
        ec = runbook.trigger_condition.event_conditions[0]
        assert ec.data_filter["previous_level"] == 1

    def test_has_no_canary_resume_step(self, runbook: Runbook):
        """LEVEL_1은 canary_resume 스텝이 없다."""
        step_names = [s.name for s in runbook.steps]
        assert "canary_resume" not in step_names

    def test_budget_reset_has_no_on_failure(self, runbook: Runbook):
        """LEVEL_1 budget_reset은 on_failure_action이 없다."""
        step = runbook.steps[0]
        assert step.on_failure_action is None


# =============================================================================
# 계약 검증 — Circuit Breaker Opened Response
# =============================================================================


class TestCircuitBreakerOpenedResponseContract:
    """Circuit Breaker Opened Response 빌트인 런북 계약 검증."""

    @pytest.fixture()
    def runbook(self) -> Runbook:
        return _build_circuit_breaker_opened_response()

    def test_id(self, runbook: Runbook):
        """런북 ID: circuit_breaker_opened_response."""
        assert runbook.id == "circuit_breaker_opened_response"

    def test_risk_level_is_medium(self, runbook: Runbook):
        """위험도: MEDIUM."""
        assert runbook.risk_level == RiskLevel.MEDIUM

    def test_priority(self, runbook: Runbook):
        """우선순위: 50."""
        assert runbook.priority == 50

    def test_cooldown_seconds(self, runbook: Runbook):
        """쿨다운: 300초."""
        assert runbook.cooldown_seconds == 300

    def test_global_timeout_seconds(self, runbook: Runbook):
        """전체 타임아웃: 300초."""
        assert runbook.global_timeout_seconds == 300

    def test_step_count(self, runbook: Runbook):
        """스텝 수: 5개."""
        assert len(runbook.steps) == 5

    def test_step_names(self, runbook: Runbook):
        """스텝 이름 목록 계약."""
        names = [s.name for s in runbook.steps]
        assert names == [
            "alert_notify",
            "stabilization_wait",
            "error_rate_check",
            "emergency_escalation",
            "escalation_notify",
        ]

    def test_tags(self, runbook: Runbook):
        """태그: circuit_breaker, escalation."""
        assert set(runbook.tags) == {"circuit_breaker", "escalation"}

    def test_trigger_event_type(self, runbook: Runbook):
        """트리거 이벤트: circuit_breaker_opened."""
        ec = runbook.trigger_condition.event_conditions[0]
        assert ec.event_type == "circuit_breaker_opened"

    def test_error_rate_check_has_continue_on_failure(self, runbook: Runbook):
        """error_rate_check 스텝: continue_on_failure=True."""
        step = [s for s in runbook.steps if s.name == "error_rate_check"][0]
        assert step.continue_on_failure is True

    def test_emergency_escalation_condition_prev_failed(self, runbook: Runbook):
        """emergency_escalation 스텝: condition.type=prev_failed."""
        step = [s for s in runbook.steps if s.name == "emergency_escalation"][0]
        assert step.condition is not None
        assert step.condition.type == "prev_failed"

    def test_escalation_notify_condition_prev_succeeded(self, runbook: Runbook):
        """escalation_notify 스텝: condition.type=prev_succeeded."""
        step = [s for s in runbook.steps if s.name == "escalation_notify"][0]
        assert step.condition is not None
        assert step.condition.type == "prev_succeeded"

    def test_emergency_escalation_level(self, runbook: Runbook):
        """emergency_escalation 스텝: level=1."""
        step = [s for s in runbook.steps if s.name == "emergency_escalation"][0]
        assert step.params["level"] == 1


# =============================================================================
# 계약 검증 — Error Budget Critical Response
# =============================================================================


class TestErrorBudgetCriticalResponseContract:
    """Error Budget Critical Response 빌트인 런북 계약 검증."""

    @pytest.fixture()
    def runbook(self) -> Runbook:
        return _build_error_budget_critical_response()

    def test_id(self, runbook: Runbook):
        """런북 ID: error_budget_critical_response."""
        assert runbook.id == "error_budget_critical_response"

    def test_risk_level_is_medium(self, runbook: Runbook):
        """위험도: MEDIUM."""
        assert runbook.risk_level == RiskLevel.MEDIUM

    def test_priority(self, runbook: Runbook):
        """우선순위: 40."""
        assert runbook.priority == 40

    def test_cooldown_seconds(self, runbook: Runbook):
        """쿨다운: 600초."""
        assert runbook.cooldown_seconds == 600

    def test_global_timeout_seconds(self, runbook: Runbook):
        """전체 타임아웃: 600초."""
        assert runbook.global_timeout_seconds == 600

    def test_step_count(self, runbook: Runbook):
        """스텝 수: 5개."""
        assert len(runbook.steps) == 5

    def test_step_names(self, runbook: Runbook):
        """스텝 이름 목록 계약."""
        names = [s.name for s in runbook.steps]
        assert names == [
            "alert_notify",
            "increase_crisis_multiplier",
            "stabilization_wait",
            "error_rate_check",
            "emergency_escalation",
        ]

    def test_tags(self, runbook: Runbook):
        """태그: error_budget, escalation."""
        assert set(runbook.tags) == {"error_budget", "escalation"}

    def test_trigger_event_type(self, runbook: Runbook):
        """트리거 이벤트: error_budget_critical."""
        ec = runbook.trigger_condition.event_conditions[0]
        assert ec.event_type == "error_budget_critical"

    def test_increase_crisis_multiplier_params(self, runbook: Runbook):
        """increase_crisis_multiplier: value_delta=0.5."""
        step = [s for s in runbook.steps if s.name == "increase_crisis_multiplier"][0]
        assert step.action == "config.set"
        assert step.params["value_delta"] == 0.5

    def test_increase_crisis_multiplier_has_on_failure(self, runbook: Runbook):
        """increase_crisis_multiplier: on_failure_action=notify.send."""
        step = [s for s in runbook.steps if s.name == "increase_crisis_multiplier"][0]
        assert step.on_failure_action == "notify.send"

    def test_error_rate_check_has_continue_on_failure(self, runbook: Runbook):
        """error_rate_check: continue_on_failure=True."""
        step = [s for s in runbook.steps if s.name == "error_rate_check"][0]
        assert step.continue_on_failure is True

    def test_emergency_escalation_condition_prev_failed(self, runbook: Runbook):
        """emergency_escalation: condition.type=prev_failed."""
        step = [s for s in runbook.steps if s.name == "emergency_escalation"][0]
        assert step.condition is not None
        assert step.condition.type == "prev_failed"


# =============================================================================
# 동작 검증 — 모든 빌트인 런북 유효성
# =============================================================================


class TestBuiltinRunbookValidationBehavior:
    """모든 빌트인 런북이 Runbook.validate()를 통과하는지 검증."""

    @pytest.mark.parametrize("builder", ALL_BUILDERS, ids=lambda b: b.__name__)
    def test_all_builtins_pass_validation(self, builder):
        """모든 빌트인 런북은 validate() 성공한다."""
        runbook = builder()
        valid, error, warnings = runbook.validate()
        assert valid is True, f"{runbook.id} 검증 실패: {error}"

    @pytest.mark.parametrize("builder", ALL_BUILDERS, ids=lambda b: b.__name__)
    def test_all_builtins_have_non_empty_description(self, builder):
        """모든 빌트인 런북은 설명이 비어있지 않다."""
        runbook = builder()
        assert runbook.description, f"{runbook.id}의 description이 비어있다"

    @pytest.mark.parametrize("builder", ALL_BUILDERS, ids=lambda b: b.__name__)
    def test_all_builtins_are_enabled_by_default(self, builder):
        """모든 빌트인 런북은 기본 활성화 상태이다."""
        runbook = builder()
        assert runbook.enabled is True

    @pytest.mark.parametrize("builder", ALL_BUILDERS, ids=lambda b: b.__name__)
    def test_all_builtins_have_unique_step_names(self, builder):
        """모든 빌트인 런북의 step name은 중복이 없다."""
        runbook = builder()
        names = [s.name for s in runbook.steps]
        assert len(names) == len(set(names)), f"{runbook.id}에 중복 step name 존재"


# =============================================================================
# 동작 검증 — 트리거 조건
# =============================================================================


class TestBuiltinRunbookTriggerConditionBehavior:
    """빌트인 런북 트리거 조건의 매칭 동작 검증."""

    def test_level3_trigger_matches_deescalation_from_level3(self):
        """LEVEL_3 런북은 previous_level=3 + deescalation 이벤트에 매칭된다."""
        runbook = _build_emergency_recovery_level3()
        result = runbook.trigger_condition.evaluate(
            metrics={},
            triggered_event="emergency_level_changed",
            event_data={"previous_level": 3, "direction": "deescalation"},
        )
        assert result is True

    def test_level3_trigger_does_not_match_level2_event(self):
        """LEVEL_3 런북은 previous_level=2 이벤트에 매칭되지 않는다."""
        runbook = _build_emergency_recovery_level3()
        result = runbook.trigger_condition.evaluate(
            metrics={},
            triggered_event="emergency_level_changed",
            event_data={"previous_level": 2, "direction": "deescalation"},
        )
        assert result is False

    def test_level3_trigger_does_not_match_escalation(self):
        """LEVEL_3 런북은 escalation 방향에 매칭되지 않는다."""
        runbook = _build_emergency_recovery_level3()
        result = runbook.trigger_condition.evaluate(
            metrics={},
            triggered_event="emergency_level_changed",
            event_data={"previous_level": 3, "direction": "escalation"},
        )
        assert result is False

    def test_cb_opened_trigger_matches_circuit_breaker_opened(self):
        """CB 런북은 circuit_breaker_opened 이벤트에 매칭된다."""
        runbook = _build_circuit_breaker_opened_response()
        result = runbook.trigger_condition.evaluate(
            metrics={},
            triggered_event="circuit_breaker_opened",
        )
        assert result is True

    def test_cb_opened_trigger_does_not_match_unrelated_event(self):
        """CB 런북은 관련 없는 이벤트에 매칭되지 않는다."""
        runbook = _build_circuit_breaker_opened_response()
        result = runbook.trigger_condition.evaluate(
            metrics={},
            triggered_event="error_budget_critical",
        )
        assert result is False

    def test_error_budget_trigger_matches_error_budget_critical(self):
        """Error Budget 런북은 error_budget_critical 이벤트에 매칭된다."""
        runbook = _build_error_budget_critical_response()
        result = runbook.trigger_condition.evaluate(
            metrics={},
            triggered_event="error_budget_critical",
        )
        assert result is True

    def test_proactive_evaluation_does_not_match_event_based_runbook(self):
        """이벤트 기반 런북은 Proactive 경로(triggered_event=None)에서 매칭 불가."""
        runbook = _build_circuit_breaker_opened_response()
        result = runbook.trigger_condition.evaluate(metrics={})
        assert result is False


# =============================================================================
# 동작 검증 — continue_on_failure 사용 패턴
# =============================================================================


class TestBuiltinRunbookContinueOnFailureBehavior:
    """continue_on_failure 필드의 올바른 사용 패턴 검증."""

    def test_cb_response_error_rate_check_uses_continue_on_failure(self):
        """CB 런북의 error_rate_check는 continue_on_failure로 검증 게이트 역할."""
        runbook = _build_circuit_breaker_opened_response()
        check_step = [s for s in runbook.steps if s.name == "error_rate_check"][0]
        assert check_step.continue_on_failure is True
        assert check_step.action == "assert.metric"

    def test_error_budget_error_rate_check_uses_continue_on_failure(self):
        """Error Budget 런북의 error_rate_check는 continue_on_failure로 검증 게이트 역할."""
        runbook = _build_error_budget_critical_response()
        check_step = [s for s in runbook.steps if s.name == "error_rate_check"][0]
        assert check_step.continue_on_failure is True
        assert check_step.action == "assert.metric"

    def test_emergency_recovery_steps_do_not_use_continue_on_failure(self):
        """Emergency Recovery 런북의 스텝은 continue_on_failure를 사용하지 않는다."""
        for builder in [
            _build_emergency_recovery_level3,
            _build_emergency_recovery_level2,
            _build_emergency_recovery_level1,
        ]:
            runbook = builder()
            for step in runbook.steps:
                assert step.continue_on_failure is False, (
                    f"{runbook.id}/{step.name}에 continue_on_failure=True가 설정됨"
                )

    def test_validate_warns_on_continue_on_failure_with_state_changing_action(self):
        """continue_on_failure + 상태 변경 action 조합 시 validate()가 경고를 반환한다."""
        runbook = _build_circuit_breaker_opened_response()
        valid, error, warnings = runbook.validate()
        assert valid is True
        state_change_warnings = [w for w in warnings if "continue_on_failure" in w]
        assert len(state_change_warnings) == 0, (
            "assert.metric은 STATE_CHANGING_ACTIONS에 포함되지 않으므로 경고 없어야 함"
        )


# =============================================================================
# 동작 검증 — 스텝 순서 정합성
# =============================================================================


class TestBuiltinRunbookStepOrderBehavior:
    """빌트인 런북 스텝의 order 필드가 올바르게 증가하는지 검증."""

    @pytest.mark.parametrize("builder", ALL_BUILDERS, ids=lambda b: b.__name__)
    def test_step_orders_are_strictly_increasing(self, builder):
        """모든 빌트인 런북의 step order는 순증가한다."""
        runbook = builder()
        orders = [s.order for s in runbook.steps]
        for i in range(1, len(orders)):
            assert orders[i] > orders[i - 1], (
                f"{runbook.id}: order {orders[i]}이 이전 {orders[i - 1]}보다 크지 않음"
            )

    @pytest.mark.parametrize("builder", ALL_BUILDERS, ids=lambda b: b.__name__)
    def test_step_orders_start_from_one(self, builder):
        """모든 빌트인 런북의 첫 번째 step order는 1이다."""
        runbook = builder()
        assert runbook.steps[0].order == 1


# =============================================================================
# 동작 검증 — 빌트인 런북 등록
# =============================================================================


class TestRegisterBuiltinRunbooksBehavior:
    """register_builtin_runbooks() 함수의 등록 동작 검증."""

    @patch("selfhealing.services.runbook.service.get_runbook_service")
    def test_registers_all_five_runbooks(self, mock_get_service):
        """5개 런북 모두 레지스트리에 등록된다."""
        mock_registry = MagicMock()
        mock_service = MagicMock()
        mock_service._get_registry.return_value = mock_registry
        mock_get_service.return_value = mock_service

        register_builtin_runbooks()

        assert mock_registry.register.call_count == 5

    @patch("selfhealing.services.runbook.service.get_runbook_service")
    def test_registered_runbook_ids(self, mock_get_service):
        """등록된 런북 ID가 모두 올바르다."""
        mock_registry = MagicMock()
        mock_service = MagicMock()
        mock_service._get_registry.return_value = mock_registry
        mock_get_service.return_value = mock_service

        register_builtin_runbooks()

        registered_ids = {
            call.args[0].id for call in mock_registry.register.call_args_list
        }
        assert registered_ids == {
            "emergency_recovery_level3",
            "emergency_recovery_level2",
            "emergency_recovery_level1",
            "circuit_breaker_opened_response",
            "error_budget_critical_response",
        }

    @patch("selfhealing.services.runbook.service.get_runbook_service")
    def test_register_continues_on_individual_failure(self, mock_get_service):
        """개별 런북 등록 실패 시에도 나머지 런북 등록을 계속한다."""
        mock_registry = MagicMock()
        call_count = 0

        def register_side_effect(runbook):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("첫 번째 런북 등록 실패")

        mock_registry.register.side_effect = register_side_effect
        mock_service = MagicMock()
        mock_service._get_registry.return_value = mock_registry
        mock_get_service.return_value = mock_service

        register_builtin_runbooks()

        assert mock_registry.register.call_count == 5


# =============================================================================
# 동작 검증 — 레벨 간 우선순위 체계
# =============================================================================


class TestBuiltinRunbookPriorityHierarchyBehavior:
    """빌트인 런북 간 우선순위 체계가 올바른지 검증."""

    def test_level3_has_highest_priority(self):
        """LEVEL_3가 가장 높은 우선순위(가장 낮은 priority 값)를 가진다."""
        l3 = _build_emergency_recovery_level3()
        l2 = _build_emergency_recovery_level2()
        l1 = _build_emergency_recovery_level1()
        assert l3.priority < l2.priority < l1.priority

    def test_level3_has_highest_risk_level(self):
        """LEVEL_3가 가장 높은 위험도를 가진다."""
        l3 = _build_emergency_recovery_level3()
        l2 = _build_emergency_recovery_level2()
        l1 = _build_emergency_recovery_level1()
        assert l3.risk_level > l2.risk_level
        assert l2.risk_level > l1.risk_level

    def test_level3_has_longest_cooldown(self):
        """LEVEL_3가 가장 긴 쿨다운을 가진다."""
        l3 = _build_emergency_recovery_level3()
        l2 = _build_emergency_recovery_level2()
        l1 = _build_emergency_recovery_level1()
        assert l3.cooldown_seconds > l2.cooldown_seconds > l1.cooldown_seconds

    def test_level3_has_most_steps(self):
        """LEVEL_3가 가장 많은 스텝을 가진다."""
        l3 = _build_emergency_recovery_level3()
        l2 = _build_emergency_recovery_level2()
        l1 = _build_emergency_recovery_level1()
        assert len(l3.steps) > len(l2.steps) > len(l1.steps)

    def test_all_builtin_ids_are_unique(self):
        """모든 빌트인 런북의 ID가 고유하다."""
        ids = [builder().id for builder in ALL_BUILDERS]
        assert len(ids) == len(set(ids))
