"""
AuditRecordingMixin for RecoveryCoordinator.

이 모듈은 selfhealing.services.coordination.recovery_coordinator 패키지의 내부 구현입니다.
"""

from __future__ import annotations

import logging
from typing import Any
from ..recovery_audit import RecoveryAuditEventType
from ..recovery_state import RecoverySession, RecoveryStep

logger = logging.getLogger(__name__)


class AuditRecordingMixin:
    """감사 기록 Mixin (Phase 5.3).

복구 프로세스의 감사 이벤트를 기록합니다."""

    def _record_recovery_started(self, session: RecoverySession) -> None:
        """
        복구 시작 감사 기록 (Phase 5.3).

        Args:
            session: RecoverySession 인스턴스
        """
        # 1. RecoveryAuditRecorder에 기록
        audit_recorder = self._get_audit_recorder()
        audit_recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_STARTED,
            session_id=session.id,
            namespace=session.namespace,
            executed_by=session.initiated_by,
            metadata={
                "trigger_level": session.trigger_level,
                "total_steps": len(session.steps),
                "step_types": [s.step_type.value for s in session.steps],
            },
        )

        # 2. CascadeEvent 기록
        effects = [
            {
                "action_type": "RECOVERY_INITIATED",
                "success": True,
                "target": session.id,
                "details": {
                    "trigger_level": session.trigger_level,
                    "total_steps": len(session.steps),
                },
            }
        ]
        self._record_cascade_event(
            session=session,
            trigger_type="RECOVERY_STARTED",
            effects=effects,
        )

    def _record_step_executed(
        self,
        session: RecoverySession,
        step: RecoveryStep,
        success: bool,
        error_message: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """
        복구 단계 실행 감사 기록 (Phase 5.3).

        Args:
            session: RecoverySession 인스턴스
            step: 실행된 RecoveryStep
            success: 성공 여부
            error_message: 에러 메시지 (실패 시)
            result: 핸들러 결과
        """
        # 1. RecoveryAuditRecorder에 기록
        audit_recorder = self._get_audit_recorder()
        event_type = RecoveryAuditEventType.RECOVERY_STEP_EXECUTED if success else RecoveryAuditEventType.RECOVERY_STEP_FAILED

        metadata = {
            "trigger_level": session.trigger_level,
            "step_params": step.params,
        }
        if result:
            metadata["idempotent"] = result.get("idempotent", False)
            metadata["already_applied"] = result.get("already_applied", False)

        audit_recorder.record_recovery_event(
            event_type=event_type,
            session_id=session.id,
            namespace=session.namespace,
            step_type=step.step_type.value,
            step_order=step.order,
            executed_by=session.initiated_by,
            success=success,
            error_message=error_message,
            metadata=metadata,
        )

        # 2. CascadeEvent 기록
        effects = [
            {
                "action_type": f"RECOVERY_STEP_{step.step_type.value.upper()}",
                "success": success,
                "target": step.step_type.value,
                "details": {
                    "step_order": step.order,
                    "error_message": error_message,
                },
            }
        ]
        trigger_type = "RECOVERY_STEP_EXECUTED" if success else "RECOVERY_STEP_FAILED"
        self._record_cascade_event(
            session=session,
            trigger_type=trigger_type,
            effects=effects,
        )

    def _record_recovery_completed(self, session: RecoverySession) -> None:
        """
        복구 완료 감사 기록 (Phase 5.3).

        Args:
            session: RecoverySession 인스턴스
        """
        # 1. RecoveryAuditRecorder에 기록
        audit_recorder = self._get_audit_recorder()
        audit_recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_COMPLETED,
            session_id=session.id,
            namespace=session.namespace,
            executed_by=session.initiated_by,
            metadata={
                "trigger_level": session.trigger_level,
                "completed_steps": session.current_step_index,
                "total_steps": len(session.steps),
            },
        )

        # 2. CascadeEvent 기록
        effects = [
            {
                "action_type": "RECOVERY_COMPLETED",
                "success": True,
                "target": session.id,
                "details": {
                    "trigger_level": session.trigger_level,
                    "completed_steps": session.current_step_index,
                },
            }
        ]
        self._record_cascade_event(
            session=session,
            trigger_type="RECOVERY_COMPLETED",
            effects=effects,
        )

    def _record_recovery_aborted(
        self,
        session: RecoverySession,
        reason: str,
    ) -> None:
        """
        복구 중단 감사 기록 (Phase 5.3).

        Args:
            session: RecoverySession 인스턴스
            reason: 중단 사유
        """
        # 1. RecoveryAuditRecorder에 기록
        audit_recorder = self._get_audit_recorder()
        audit_recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_ABORTED,
            session_id=session.id,
            namespace=session.namespace,
            executed_by=session.initiated_by,
            success=False,
            error_message=reason,
            metadata={
                "trigger_level": session.trigger_level,
                "aborted_at_step": session.current_step_index,
                "total_steps": len(session.steps),
            },
        )

        # 2. CascadeEvent 기록
        effects = [
            {
                "action_type": "RECOVERY_ABORTED",
                "success": False,
                "target": session.id,
                "details": {
                    "trigger_level": session.trigger_level,
                    "abort_reason": reason,
                    "aborted_at_step": session.current_step_index,
                },
            }
        ]
        self._record_cascade_event(
            session=session,
            trigger_type="RECOVERY_ABORTED",
            effects=effects,
        )

    def _publish_emergency_recovery_completed_event(
        self,
        session: RecoverySession,
        approved_by: str | None = None,
    ) -> None:
        """
        EMERGENCY_RECOVERY_COMPLETED 이벤트 발행.

        EventBus를 통해 Emergency 복구 완료 이벤트를 발행합니다.
        이 이벤트는 Emergency Postmortem 자동 생성을 트리거합니다.

        Args:
            session: 완료된 RecoverySession 인스턴스
            approved_by: 승인자 ID (수동 승인인 경우)
        """
        try:
            # 지연 import (순환 import 방지)
            from selfhealing.services.event_bus import EventType, get_event_bus

            # duration 계산
            duration_seconds = None
            if session.started_at and session.completed_at:
                try:
                    from datetime import datetime

                    started = datetime.fromisoformat(session.started_at.replace("Z", "+00:00"))
                    completed = datetime.fromisoformat(session.completed_at.replace("Z", "+00:00"))
                    duration_seconds = (completed - started).total_seconds()
                except (ValueError, TypeError):
                    pass

            event_bus = get_event_bus()
            event_bus.emit(
                event_type=EventType.EMERGENCY_RECOVERY_COMPLETED,
                data={
                    "session_id": session.id,
                    "namespace": session.namespace,
                    "trigger_level": session.trigger_level,
                    "started_at": session.started_at,
                    "completed_at": session.completed_at,
                    "duration_seconds": duration_seconds,
                    "steps_executed": session.current_step_index,
                    "total_steps": len(session.steps),
                    "requires_approval": bool(session.metadata and session.metadata.get("requires_approval")),
                    "approved_by": approved_by,
                },
                source="recovery_coordinator",
            )

            logger.info(f"[Recovery] Published EMERGENCY_RECOVERY_COMPLETED: {session.id}")

        except Exception as e:
            # 이벤트 발행 실패가 복구 완료에 영향을 주지 않도록 함
            logger.warning(f"[Recovery] Failed to publish EMERGENCY_RECOVERY_COMPLETED: {e}")
