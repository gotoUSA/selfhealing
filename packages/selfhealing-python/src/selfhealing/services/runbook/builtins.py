"""
빌트인 Runbook 정의 등록.

RecoveryCoordinator의 DEFAULT_RECOVERY_STEPS에 하드코딩된 복구 절차를
선언적 Runbook 정의로 전환한다.

5개 빌트인 런북:
    - emergency_recovery_level3 — LEVEL_3 복구 (6 Steps)
    - emergency_recovery_level2 — LEVEL_2 복구 (5 Steps)
    - emergency_recovery_level1 — LEVEL_1 복구 (4 Steps)
    - circuit_breaker_opened_response — CB Open 대응 (5 Steps)
    - error_budget_critical_response — Error Budget 소진 대응 (5 Steps)

Reference:
    docs/self_healing/middleware_system/296_BUILTIN_RUNBOOKS.md
    docs/self_healing/middleware_system/278_RUNBOOK_SERVICE.md §8.1
"""

from __future__ import annotations

import structlog

from selfhealing.services.runbook.models import (
    EventCondition,
    PatternCondition,
)
from selfhealing.services.runbook.runbook_registry import (
    RiskLevel,
    Runbook,
    RunbookStep,
    StepCondition,
)

logger = structlog.get_logger(__name__)


# =============================================================================
# Emergency Recovery — LEVEL_3
# =============================================================================


def _build_emergency_recovery_level3() -> Runbook:
    """LEVEL_3 복구 런북 — DEFAULT_RECOVERY_STEPS["LEVEL_3"] 대체."""
    return Runbook(
        id="emergency_recovery_level3",
        name="Emergency LEVEL_3 Recovery",
        description=(
            "Emergency LEVEL_3에서 정상으로 복구하는 4단계 절차. "
            "Budget Reset → Health Check → Canary Resume → Governance Normal."
        ),
        trigger_condition=PatternCondition(
            event_conditions=[
                EventCondition(
                    event_type="emergency_level_changed",
                    data_filter={"previous_level": 3, "direction": "deescalation"},
                ),
            ],
        ),
        steps=[
            RunbookStep(
                name="budget_reset",
                action="config.set",
                order=1,
                params={
                    "key": "crisis_multiplier.target_multiplier",
                    "value": 1.0,
                },
                on_failure_action="notify.send",
                on_failure_params={
                    "title": "[LEVEL_3 Recovery] Budget Reset Failed",
                    "message": "Crisis Multiplier 리셋 실패. 수동 확인 필요.",
                    "priority": "critical",
                },
                timeout_seconds=30,
                wait_after_seconds=0,
            ),
            RunbookStep(
                name="stabilization_wait",
                action="wait.stabilize",
                order=2,
                params={
                    "seconds": 300,
                    "assert_metric": "error_rate",
                    "threshold": 0.9,
                },
                timeout_seconds=360,
                wait_after_seconds=0,
            ),
            RunbookStep(
                name="health_check_gate",
                action="assert.metric",
                order=3,
                params={
                    "metric_name": "error_rate",
                    "operator": "lte",
                    "threshold": 0.1,
                },
                timeout_seconds=30,
                wait_after_seconds=0,
            ),
            RunbookStep(
                name="canary_resume",
                action="recovery.start",
                order=4,
                params={
                    "namespace": "${trigger.namespace}",
                    "trigger_level": "CANARY_RESUME",
                },
                timeout_seconds=120,
                wait_after_seconds=60,
            ),
            RunbookStep(
                name="governance_normal",
                action="config.set",
                order=5,
                params={
                    "key": "governance.mode",
                    "value": "NORMAL",
                },
                on_failure_action="notify.send",
                on_failure_params={
                    "title": "[LEVEL_3 Recovery] Governance Restore Failed",
                    "message": "Governance NORMAL 전환 실패. 수동 확인 필요.",
                    "priority": "high",
                },
                timeout_seconds=30,
                wait_after_seconds=300,
            ),
            RunbookStep(
                name="completion_notify",
                action="notify.send",
                order=6,
                params={
                    "title": "[LEVEL_3 Recovery] Completed",
                    "message": "Emergency LEVEL_3 복구 완료. 모든 서비스 정상 복원.",
                    "priority": "medium",
                },
                timeout_seconds=30,
            ),
        ],
        risk_level=RiskLevel.HIGH,
        priority=10,
        cooldown_seconds=600,
        tags=["emergency", "recovery", "level3"],
        global_timeout_seconds=1200,
    )


# =============================================================================
# Emergency Recovery — LEVEL_2
# =============================================================================


def _build_emergency_recovery_level2() -> Runbook:
    """LEVEL_2 복구 런북 — DEFAULT_RECOVERY_STEPS["LEVEL_2"] 대체."""
    return Runbook(
        id="emergency_recovery_level2",
        name="Emergency LEVEL_2 Recovery",
        description=(
            "Emergency LEVEL_2에서 정상으로 복구하는 3단계 절차. "
            "Budget Reset → Health Check → Canary Resume."
        ),
        trigger_condition=PatternCondition(
            event_conditions=[
                EventCondition(
                    event_type="emergency_level_changed",
                    data_filter={"previous_level": 2, "direction": "deescalation"},
                ),
            ],
        ),
        steps=[
            RunbookStep(
                name="budget_reset",
                action="config.set",
                order=1,
                params={
                    "key": "crisis_multiplier.target_multiplier",
                    "value": 1.0,
                },
                on_failure_action="notify.send",
                on_failure_params={
                    "title": "[LEVEL_2 Recovery] Budget Reset Failed",
                    "message": "Crisis Multiplier 리셋 실패.",
                    "priority": "high",
                },
                timeout_seconds=30,
            ),
            RunbookStep(
                name="stabilization_wait",
                action="wait.stabilize",
                order=2,
                params={
                    "seconds": 180,
                    "assert_metric": "error_rate",
                    "threshold": 0.85,
                },
                timeout_seconds=240,
            ),
            RunbookStep(
                name="health_check_gate",
                action="assert.metric",
                order=3,
                params={
                    "metric_name": "error_rate",
                    "operator": "lte",
                    "threshold": 0.15,
                },
                timeout_seconds=30,
            ),
            RunbookStep(
                name="canary_resume",
                action="recovery.start",
                order=4,
                params={
                    "namespace": "${trigger.namespace}",
                    "trigger_level": "CANARY_RESUME",
                },
                timeout_seconds=120,
                wait_after_seconds=30,
            ),
            RunbookStep(
                name="completion_notify",
                action="notify.send",
                order=5,
                params={
                    "title": "[LEVEL_2 Recovery] Completed",
                    "message": "Emergency LEVEL_2 복구 완료.",
                    "priority": "medium",
                },
                timeout_seconds=30,
            ),
        ],
        risk_level=RiskLevel.MEDIUM,
        priority=20,
        cooldown_seconds=300,
        tags=["emergency", "recovery", "level2"],
        global_timeout_seconds=900,
    )


# =============================================================================
# Emergency Recovery — LEVEL_1
# =============================================================================


def _build_emergency_recovery_level1() -> Runbook:
    """LEVEL_1 복구 런북 — DEFAULT_RECOVERY_STEPS["LEVEL_1"] 대체."""
    return Runbook(
        id="emergency_recovery_level1",
        name="Emergency LEVEL_1 Recovery",
        description=(
            "Emergency LEVEL_1에서 정상으로 복구하는 2단계 절차. "
            "Budget Reset → Health Check."
        ),
        trigger_condition=PatternCondition(
            event_conditions=[
                EventCondition(
                    event_type="emergency_level_changed",
                    data_filter={"previous_level": 1, "direction": "deescalation"},
                ),
            ],
        ),
        steps=[
            RunbookStep(
                name="budget_reset",
                action="config.set",
                order=1,
                params={
                    "key": "crisis_multiplier.target_multiplier",
                    "value": 1.0,
                },
                timeout_seconds=30,
            ),
            RunbookStep(
                name="stabilization_wait",
                action="wait.stabilize",
                order=2,
                params={
                    "seconds": 120,
                    "assert_metric": "error_rate",
                    "threshold": 0.8,
                },
                timeout_seconds=180,
            ),
            RunbookStep(
                name="health_check_gate",
                action="assert.metric",
                order=3,
                params={
                    "metric_name": "error_rate",
                    "operator": "lte",
                    "threshold": 0.2,
                },
                timeout_seconds=30,
            ),
            RunbookStep(
                name="completion_notify",
                action="notify.send",
                order=4,
                params={
                    "title": "[LEVEL_1 Recovery] Completed",
                    "message": "Emergency LEVEL_1 복구 완료.",
                    "priority": "low",
                },
                timeout_seconds=30,
            ),
        ],
        risk_level=RiskLevel.LOW,
        priority=30,
        cooldown_seconds=180,
        tags=["emergency", "recovery", "level1"],
        global_timeout_seconds=600,
    )


# =============================================================================
# Circuit Breaker Opened Response
# =============================================================================


def _build_circuit_breaker_opened_response() -> Runbook:
    """CB Open 대응 런북 — 알림 + 에러율 확인 + 조건부 에스컬레이션."""
    return Runbook(
        id="circuit_breaker_opened_response",
        name="Circuit Breaker Opened Response",
        description=(
            "Circuit Breaker가 Open되면 알림 발송 후 에러율을 확인하고, "
            "임계값 초과 시 Emergency 모드를 활성화한다."
        ),
        trigger_condition=PatternCondition(
            event_conditions=[
                EventCondition(event_type="circuit_breaker_opened"),
            ],
        ),
        steps=[
            RunbookStep(
                name="alert_notify",
                action="notify.send",
                order=1,
                params={
                    "title": "[CB Open] ${trigger.source}",
                    "message": "Circuit Breaker가 Open되었습니다. 에러율 확인 중.",
                    "priority": "high",
                },
                timeout_seconds=30,
            ),
            RunbookStep(
                name="stabilization_wait",
                action="wait.stabilize",
                order=2,
                params={
                    "seconds": 60,
                    "assert_metric": "error_rate",
                    "threshold": 0.7,
                    "labels": {"service": "${trigger.source}"},
                },
                timeout_seconds=90,
            ),
            RunbookStep(
                name="error_rate_check",
                action="assert.metric",
                order=3,
                params={
                    "metric_name": "error_rate",
                    "operator": "lte",
                    "threshold": 0.3,
                    "labels": {"service": "${trigger.source}"},
                },
                continue_on_failure=True,
                timeout_seconds=30,
            ),
            RunbookStep(
                name="emergency_escalation",
                action="emergency.activate",
                order=4,
                params={
                    "level": 1,
                    "reason": "CB Open 후 에러율 미회복 — 자동 에스컬레이션",
                },
                condition=StepCondition(type="prev_failed"),
                timeout_seconds=30,
            ),
            RunbookStep(
                name="escalation_notify",
                action="notify.send",
                order=5,
                params={
                    "title": "[CB Open] Emergency LEVEL_1 Activated",
                    "message": "에러율 미회복으로 Emergency LEVEL_1이 활성화되었습니다.",
                    "priority": "critical",
                },
                condition=StepCondition(type="prev_succeeded"),
                timeout_seconds=30,
            ),
        ],
        risk_level=RiskLevel.MEDIUM,
        priority=50,
        cooldown_seconds=300,
        tags=["circuit_breaker", "escalation"],
        global_timeout_seconds=300,
    )


# =============================================================================
# Error Budget Critical Response
# =============================================================================


def _build_error_budget_critical_response() -> Runbook:
    """Error Budget 소진 대응 런북 — 알림 + 트래픽 제한 + 조건부 에스컬레이션."""
    return Runbook(
        id="error_budget_critical_response",
        name="Error Budget Critical Response",
        description=(
            "Error Budget이 소진되면 알림 발송 후 트래픽 제한을 적용하고, "
            "안정화 확인 후 에스컬레이션 여부를 판단한다."
        ),
        trigger_condition=PatternCondition(
            event_conditions=[
                EventCondition(event_type="error_budget_critical"),
            ],
        ),
        steps=[
            RunbookStep(
                name="alert_notify",
                action="notify.send",
                order=1,
                params={
                    "title": "[Error Budget] Critical — Budget Exhausted",
                    "message": "Error Budget이 소진되었습니다. 자동 대응 시작.",
                    "priority": "critical",
                },
                timeout_seconds=30,
            ),
            RunbookStep(
                name="increase_crisis_multiplier",
                action="config.set",
                order=2,
                params={
                    "key": "crisis_multiplier.target_multiplier",
                    "value_delta": 0.5,
                },
                on_failure_action="notify.send",
                on_failure_params={
                    "title": "[Error Budget] Crisis Multiplier Adjustment Failed",
                    "message": "Crisis Multiplier 조정 실패. 수동 확인 필요.",
                    "priority": "critical",
                },
                timeout_seconds=30,
            ),
            RunbookStep(
                name="stabilization_wait",
                action="wait.stabilize",
                order=3,
                params={
                    "seconds": 120,
                    "assert_metric": "error_rate",
                    "threshold": 0.8,
                },
                timeout_seconds=180,
            ),
            RunbookStep(
                name="error_rate_check",
                action="assert.metric",
                order=4,
                params={
                    "metric_name": "error_rate",
                    "operator": "lte",
                    "threshold": 0.15,
                    "labels": {"service": "${trigger.source}"},
                },
                continue_on_failure=True,
                timeout_seconds=30,
            ),
            RunbookStep(
                name="emergency_escalation",
                action="emergency.activate",
                order=5,
                params={
                    "level": 1,
                    "reason": "Error Budget 소진 후 에러율 미회복 — 자동 에스컬레이션",
                },
                condition=StepCondition(type="prev_failed"),
                timeout_seconds=30,
            ),
        ],
        risk_level=RiskLevel.MEDIUM,
        priority=40,
        cooldown_seconds=600,
        tags=["error_budget", "escalation"],
        global_timeout_seconds=600,
    )


# =============================================================================
# 등록 진입점
# =============================================================================


def register_builtin_runbooks() -> None:
    """빌트인 Runbook을 레지스트리에 등록.

    initialize_runbook_system() (service.py:637)에서 호출된다.
    RunbookService 싱글톤의 내부 RunbookRegistry에 등록한다.
    """
    from selfhealing.services.runbook.service import get_runbook_service

    service = get_runbook_service()
    registry = service._get_registry()

    builtin_runbooks = [
        _build_emergency_recovery_level3(),
        _build_emergency_recovery_level2(),
        _build_emergency_recovery_level1(),
        _build_circuit_breaker_opened_response(),
        _build_error_budget_critical_response(),
    ]

    registered = 0
    for runbook in builtin_runbooks:
        try:
            registry.register(runbook)
            registered += 1
        except ValueError as exc:
            logger.warning(
                "runbook_builtins.register_failed",
                runbook_id=runbook.id,
                error=str(exc),
            )

    logger.info(
        "runbook_builtins.registered",
        count=registered,
        total=len(builtin_runbooks),
    )
