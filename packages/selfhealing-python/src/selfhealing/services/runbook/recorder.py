"""
RunbookPlaybackRecorder — Runbook 실행 결과를 3개 채널에 기록하는 Recorder.

채널:
    1. CascadeEventAuditor — 인과 관계 감사 추적
    2. LearningService — 성공/실패 패턴 학습 피드백
    3. PostmortemStore — 실패 시 자동 인시던트 생성

+ EventBus 이벤트 발행 (RUNBOOK_EXECUTION_COMPLETED / RUNBOOK_EXECUTION_FAILED)

데이터 보호:
    - 모든 채널 전송 전 PII 자동 마스킹 (audit/masking.py)
    - 이벤트 페이로드 에러 메시지 크기 제한
    - 기록 실패 시 Prometheus 메트릭 노출

Reference:
    docs/self_healing/middleware_system/277_RUNBOOK_PLAYBACK_RECORDER.md
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.audit.masking import mask_sensitive_fields
from selfhealing.services.runbook.execution_models import (
    RecordingSummary,
    RunbookExecutionStatus,
    RunbookStepResult,
)
from selfhealing.services.runbook.recorder_metrics import RECORDER_ERROR, RECORDER_SUCCESS

if TYPE_CHECKING:
    from selfhealing.audit.cascade_auditor import CascadeEventAuditor
    from selfhealing.services.event_bus.bus import SelfHealingEventBus
    from selfhealing.services.learning.service import LearningService
    from selfhealing.services.runbook.execution_models import (
        CompensationSummary,
        RunbookExecutionContext,
    )
    from selfhealing.services.runbook.runbook_registry import Runbook

logger = structlog.get_logger()

# =============================================================================
# 민감 데이터 마스킹 확장 키
# =============================================================================
# audit/masking.py의 기본 키(password, secret, token 등)에 추가하여,
# Runbook 실행 컨텍스트에서 노출 가능한 인프라 자격 증명 키를 확장한다.

RUNBOOK_SENSITIVE_KEYS: list[str] = [
    # audit/masking.py 기본 키와 동일
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "credential",
    "private_key",
    "credit_card",
    "ssn",
    "social_security",
    # Runbook 도메인 확장 — 인프라 접속 문자열
    "connection_string",
    "dsn",
    "database_url",
    "db_password",
    "redis_url",
    "broker_url",
    "smtp_password",
]

# =============================================================================
# 이벤트 페이로드 크기 제한
# =============================================================================
# EventBus 이벤트의 abort_reason에 긴 Stacktrace가 포함될 수 있으므로
# Kafka 기본 max_request_size(1MB) 초과를 방지하기 위해 Truncate한다.
# 상세 로그는 execution_id 기반 DB 조회로 유도한다 (Claim Check 패턴).

MAX_ERROR_MESSAGE_LENGTH: int = 200
TRUNCATION_MARKER: str = "... [TRUNCATED. Full log: DB lookup by execution_id]"

# =============================================================================
# 성능 패턴 학습 SLA 비율 임계값
# =============================================================================
# 성공했지만 전체 실행 시간이 global_timeout_seconds의 이 비율을 초과하면
# PatternType.PERFORMANCE로 "느린 복구" 패턴을 추가 학습한다.

SLOW_RECOVERY_SLA_RATIO: float = 0.8


class RunbookPlaybackRecorder:
    """Runbook 실행 결과를 4개 채널에 기록하는 Recorder.

    각 채널은 독립적으로 Fail-Open 처리하여 한 채널의 실패가
    다른 채널의 기록을 차단하지 않는다.
    """

    def __init__(
        self,
        cascade_auditor: CascadeEventAuditor | None = None,
        learning_service: LearningService | None = None,
        event_bus: SelfHealingEventBus | None = None,
    ) -> None:
        self._cascade_auditor = cascade_auditor
        self._learning_service = learning_service
        self._event_bus = event_bus

    # =========================================================================
    # Public API
    # =========================================================================

    def record(
        self,
        ctx: RunbookExecutionContext,
        runbook: Runbook,
        compensation: CompensationSummary | None = None,
    ) -> RecordingSummary:
        """Runbook 실행 결과를 모든 채널에 기록한다.

        채널 전송 전에 PII 마스킹을 일괄 적용한 뒤,
        각 채널에 마스킹된 데이터만 전달한다.

        Args:
            ctx: 실행 컨텍스트 (step_results, status, trigger_event 등).
            runbook: 실행된 Runbook 정의.
            compensation: 보상 실행 결과 (실패 시에만 존재).

        Returns:
            각 채널별 기록 성공 여부를 담은 RecordingSummary.
        """
        summary = RecordingSummary(execution_id=ctx.execution_id)

        # PII 마스킹 — 모든 채널에 전달되기 전에 민감 데이터를 일괄 마스킹한다.
        sanitized_step_results = self._sanitize_step_results(ctx.step_results)
        sanitized_trigger_event = mask_sensitive_fields(
            ctx.trigger_event,
            RUNBOOK_SENSITIVE_KEYS,
        )

        # 1. Cascade Event (인과 추적)
        summary.cascade_recorded = self._record_cascade_event(
            ctx,
            runbook,
            sanitized_step_results,
            sanitized_trigger_event,
        )

        # 2. Learning Feedback (패턴 학습)
        summary.pattern_recorded = self._record_learning_feedback(ctx, runbook)

        # 3. Postmortem (실패 시 인시던트)
        if ctx.status == RunbookExecutionStatus.FAILED:
            summary.postmortem_recorded = self._record_postmortem(
                ctx,
                runbook,
                compensation,
                sanitized_step_results,
                sanitized_trigger_event,
            )

        # 4. EventBus (이벤트 발행)
        summary.event_emitted = self._emit_event(ctx, runbook)

        return summary

    # =========================================================================
    # 채널 1: Cascade Event (인과 관계 감사 추적)
    # =========================================================================

    def _record_cascade_event(
        self,
        ctx: RunbookExecutionContext,
        runbook: Runbook,
        sanitized_step_results: dict[str, RunbookStepResult],
        sanitized_trigger_event: dict[str, Any],
    ) -> bool:
        """CascadeEventAuditor.record()를 호출하여 Step별 인과 체인을 구성한다.

        정방향 Step은 순차 체인(caused_by 자동)으로,
        보상 Step은 역순 배치하여 실패 Step → 보상 Step 연결을 만든다.
        """
        if self._cascade_auditor is None:
            return False

        try:
            trigger_details = {
                "execution_id": ctx.execution_id,
                "runbook_id": ctx.runbook_id,
                "status": ctx.status.value,
                "started_at": ctx.started_at,
                "completed_at": ctx.completed_at,
                "trigger_event": sanitized_trigger_event,
            }

            effects: list[dict[str, Any]] = []

            # 정방향 Step 기록 (순차 체인 — caused_by 자동)
            for step_name, step_result in sanitized_step_results.items():
                if step_result.compensation_status == "not_needed" or step_result.executed:
                    effects.append(
                        {
                            "action_type": f"RUNBOOK_STEP:{step_result.action_name}",
                            "success": step_result.success,
                            "target": step_name,
                            "details": {
                                "executed": step_result.executed,
                                "idempotent": step_result.idempotent,
                                "result_data": step_result.result_data,
                                "compensation_status": step_result.compensation_status,
                                "partial_execution": step_result.partial_execution,
                            },
                            "error_message": step_result.error,
                        }
                    )

            # 보상 Step 기록 (역순 배치 — 실패 Step 뒤에 배치하여 자동 체인 활용)
            for step_name, step_result in sanitized_step_results.items():
                if step_result.compensation_status in (
                    "compensated",
                    "compensate_failed",
                ):
                    effects.append(
                        {
                            "action_type": f"RUNBOOK_COMPENSATE:{step_result.action_name}",
                            "success": step_result.compensation_status == "compensated",
                            "target": f"compensate:{step_name}",
                            "details": {
                                "original_step": step_name,
                                "compensation_status": step_result.compensation_status,
                                "partial_execution": step_result.partial_execution,
                            },
                            "error_message": (
                                step_result.error if step_result.compensation_status == "compensate_failed" else None
                            ),
                        }
                    )

            self._cascade_auditor.record(
                trigger_type="RUNBOOK_EXECUTION",
                trigger_details=trigger_details,
                effects=effects,
                namespace=ctx.namespace,
                triggered_by="system:runbook_executor",
            )

            RECORDER_SUCCESS.labels(channel="cascade").inc()
            return True

        except Exception as exc:
            RECORDER_ERROR.labels(
                channel="cascade",
                error_type=type(exc).__name__,
            ).inc()
            logger.warning("runbook_recorder.cascade_failed", error=str(exc))
            return False

    # =========================================================================
    # 채널 2: Learning Service (패턴 학습 피드백)
    # =========================================================================

    def _record_learning_feedback(
        self,
        ctx: RunbookExecutionContext,
        runbook: Runbook,
    ) -> bool:
        """LearningService.learn_pattern()을 호출하여 성공/실패 패턴을 학습시킨다.

        패턴 이름에 버전을 포함(v{version})하여 버전별 독립 패턴을 관리하고,
        metadata의 previous_pattern_name으로 계보(Lineage)를 추적한다.

        성공했지만 SLA의 80%를 초과한 느린 복구는
        PatternType.PERFORMANCE로 추가 학습한다.
        """
        if self._learning_service is None:
            return False

        try:
            from selfhealing.services.learning.models import PatternType

            is_success = ctx.status == RunbookExecutionStatus.COMPLETED

            pattern_type = PatternType.RECOVERY if is_success else PatternType.FAILURE
            outcome_label = "success" if is_success else "failure"
            pattern_name = f"runbook:{runbook.id}:v{runbook.version}:{outcome_label}"

            # 실행 시간 계산
            duration_seconds = self._calculate_duration(ctx)

            features: dict[str, Any] = {
                "runbook_id": runbook.id,
                "namespace": ctx.namespace,
                "step_count": len(runbook.steps),
                "executed_step_count": len(ctx.step_results),
                "duration_seconds": duration_seconds,
                "risk_level": runbook.risk_level.value,
            }

            if not is_success:
                failed_steps = [name for name, result in ctx.step_results.items() if not result.success]
                features["failed_steps"] = failed_steps
                features["abort_reason"] = ctx.abort_reason

            confidence = 0.9 if is_success else 0.85

            # 계보 추적 — 이전 버전 패턴 이름을 Linked List로 연결
            previous_version = runbook.version - 1
            previous_pattern_name = (
                f"runbook:{runbook.id}:v{previous_version}:{outcome_label}" if previous_version >= 1 else None
            )

            self._learning_service.learn_pattern(
                pattern_type=pattern_type,
                name=pattern_name,
                description=(
                    f"Runbook '{runbook.name}' v{runbook.version} "
                    f"{'succeeded' if is_success else 'failed'} "
                    f"in namespace '{ctx.namespace}'"
                ),
                features=features,
                confidence=confidence,
                metadata={
                    "execution_id": ctx.execution_id,
                    "runbook_version": runbook.version,
                    "previous_pattern_name": previous_pattern_name,
                },
            )

            # 성능 저하 패턴 추가 학습
            # 성공했지만 전체 타임아웃 대비 80% 초과 시 "느린 복구"로 학습
            if is_success and duration_seconds is not None:
                runbook_timeout = runbook.global_timeout_seconds
                if runbook_timeout and duration_seconds > runbook_timeout * SLOW_RECOVERY_SLA_RATIO:
                    sla_ratio = duration_seconds / runbook_timeout
                    self._learning_service.learn_pattern(
                        pattern_type=PatternType.PERFORMANCE,
                        name=f"runbook:{runbook.id}:v{runbook.version}:slow_recovery",
                        description=(
                            f"Runbook '{runbook.name}' v{runbook.version} succeeded "
                            f"but took {duration_seconds:.1f}s "
                            f"(SLA: {runbook_timeout}s, {sla_ratio * 100:.0f}%)"
                        ),
                        features={
                            "runbook_id": runbook.id,
                            "namespace": ctx.namespace,
                            "duration_seconds": duration_seconds,
                            "timeout_seconds": runbook_timeout,
                            "sla_ratio": sla_ratio,
                            "step_count": len(runbook.steps),
                        },
                        confidence=0.7,
                        metadata={
                            "execution_id": ctx.execution_id,
                            "runbook_version": runbook.version,
                        },
                    )

            RECORDER_SUCCESS.labels(channel="learning").inc()
            return True

        except Exception as exc:
            RECORDER_ERROR.labels(
                channel="learning",
                error_type=type(exc).__name__,
            ).inc()
            logger.warning("runbook_recorder.learning_failed", error=str(exc))
            return False

    # =========================================================================
    # 채널 3: Postmortem (실패 시 인시던트 생성)
    # =========================================================================

    def _record_postmortem(
        self,
        ctx: RunbookExecutionContext,
        runbook: Runbook,
        compensation: CompensationSummary | None,
        sanitized_step_results: dict[str, RunbookStepResult],
        sanitized_trigger_event: dict[str, Any],
    ) -> bool:
        """실패 시 자동 postmortem incident를 생성한다.

        add_healing_incident()를 호출하여 PostgreSQL(실패 시 In-Memory)에 저장한다.
        trigger_event에 trigger_context가 포함되어 있으면 Root Cause Link로 활용하여
        오진(False Positive)과 런북 로직 오류를 구분할 수 있는 증거를 포함한다.
        """
        try:
            from selfhealing.services.postmortem.store import add_healing_incident

            failed_steps = [
                {
                    "step_name": name,
                    "error": result.error,
                    "action": result.action_name,
                }
                for name, result in sanitized_step_results.items()
                if not result.success
            ]

            compensation_info = None
            if compensation:
                compensation_info = {
                    "compensated": compensation.compensated,
                    "failed": compensation.failed,
                    "skipped": compensation.skipped,
                    "all_compensated": compensation.all_compensated,
                }

            # Root Cause Link — trigger_event에 포함된 trigger_context 추출
            trigger_context = sanitized_trigger_event.get("trigger_context", {})

            incident: dict[str, Any] = {
                "incident_type": "runbook_execution_failed",
                "service_name": ctx.namespace,
                "action_taken": f"runbook:{runbook.id}",
                "resolved": False,
                "details": {
                    "execution_id": ctx.execution_id,
                    "runbook_id": runbook.id,
                    "runbook_name": runbook.name,
                    "risk_level": runbook.risk_level.value,
                    "namespace": ctx.namespace,
                    "trigger_event": sanitized_trigger_event,
                    "failed_steps": failed_steps,
                    "compensation": compensation_info,
                    "abort_reason": ctx.abort_reason,
                    "started_at": ctx.started_at,
                    "completed_at": ctx.completed_at,
                    "trigger_context": {
                        "triggered_by_event": trigger_context.get("triggered_by_event"),
                        "metric_snapshot": trigger_context.get("metric_snapshot"),
                        "match_confidence": trigger_context.get("match_confidence"),
                        "matched_conditions": trigger_context.get("matched_conditions"),
                    },
                },
            }

            add_healing_incident(incident)

            RECORDER_SUCCESS.labels(channel="postmortem").inc()
            return True

        except Exception as exc:
            RECORDER_ERROR.labels(
                channel="postmortem",
                error_type=type(exc).__name__,
            ).inc()
            logger.warning("runbook_recorder.postmortem_failed", error=str(exc))
            return False

    # =========================================================================
    # 채널 4: EventBus (이벤트 발행)
    # =========================================================================

    def _emit_event(
        self,
        ctx: RunbookExecutionContext,
        runbook: Runbook,
    ) -> bool:
        """EventBus에 실행 결과 이벤트를 발행한다.

        에러 메시지는 MAX_ERROR_MESSAGE_LENGTH로 Truncate하고,
        상세 로그는 execution_id 기반 DB 조회로 유도한다 (Claim Check 패턴).
        """
        if self._event_bus is None:
            return False

        try:
            from selfhealing.services.event_bus.bus import EventType

            is_success = ctx.status == RunbookExecutionStatus.COMPLETED

            event_type = EventType.RUNBOOK_EXECUTION_COMPLETED if is_success else EventType.RUNBOOK_EXECUTION_FAILED

            event_data: dict[str, Any] = {
                "execution_id": ctx.execution_id,
                "runbook_id": ctx.runbook_id,
                "runbook_name": runbook.name,
                "namespace": ctx.namespace,
                "status": ctx.status.value,
                "risk_level": runbook.risk_level.value,
                "step_count": len(runbook.steps),
                "executed_step_count": len(ctx.step_results),
                "started_at": ctx.started_at,
                "completed_at": ctx.completed_at,
            }

            if not is_success:
                abort_reason = ctx.abort_reason or ""
                if len(abort_reason) > MAX_ERROR_MESSAGE_LENGTH:
                    abort_reason = abort_reason[:MAX_ERROR_MESSAGE_LENGTH] + TRUNCATION_MARKER
                event_data["abort_reason"] = abort_reason
                event_data["failed_steps"] = [name for name, result in ctx.step_results.items() if not result.success]

            self._event_bus.emit(event_type, event_data)

            RECORDER_SUCCESS.labels(channel="event_bus").inc()
            return True

        except Exception as exc:
            RECORDER_ERROR.labels(
                channel="event_bus",
                error_type=type(exc).__name__,
            ).inc()
            logger.warning("runbook_recorder.event_emit_failed", error=str(exc))
            return False

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    @staticmethod
    def _sanitize_step_results(
        step_results: dict[str, RunbookStepResult],
    ) -> dict[str, RunbookStepResult]:
        """Step result_data에 포함된 민감 정보를 마스킹한 복사본을 반환한다."""
        sanitized: dict[str, RunbookStepResult] = {}
        for step_name, step_result in step_results.items():
            sanitized_result_data = mask_sensitive_fields(
                step_result.result_data,
                RUNBOOK_SENSITIVE_KEYS,
            )
            sanitized[step_name] = RunbookStepResult(
                step_name=step_result.step_name,
                action_name=step_result.action_name,
                success=step_result.success,
                executed=step_result.executed,
                result_data=sanitized_result_data,
                error=step_result.error,
                started_at=step_result.started_at,
                completed_at=step_result.completed_at,
                idempotent=step_result.idempotent,
                partial_execution=step_result.partial_execution,
                compensation_status=step_result.compensation_status,
            )
        return sanitized

    @staticmethod
    def _calculate_duration(ctx: RunbookExecutionContext) -> float | None:
        """started_at, completed_at ISO 8601 문자열로부터 실행 시간(초)을 계산한다."""
        if not ctx.started_at or not ctx.completed_at:
            return None
        try:
            start = datetime.fromisoformat(ctx.started_at)
            end = datetime.fromisoformat(ctx.completed_at)
            return (end - start).total_seconds()
        except (ValueError, TypeError):
            return None
