"""
Freeze Decision Recorder (Audit Trail)

배포 동결 확정, Override 승인 등의 결정을 Audit Trail에 기록합니다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import structlog

from selfhealing.core.timezone import now
from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
from selfhealing.services.error_budget.enums import (
    OverrideType,
    _get_error_budget_config,
)
from selfhealing.services.error_budget.models import FreezeDecisionRecord

logger = structlog.get_logger()


class FreezeDecisionRecorder:
    """
    배포 동결 결정 기록기.

    배포 동결 확정, Override 승인 등의 결정을 Audit Trail에 기록합니다.
    """

    def __init__(
        self,
        advisor: DeploymentPolicyAdvisor | None = None,
        persist_record: Callable[[FreezeDecisionRecord], None] | None = None,
        emit_metric: Callable[[str, dict], None] | None = None,
        emit_otel_event: Callable[[str, dict], None] | None = None,
        alert_adapter: Any | None = None,  # AlertAdapter for escalations
    ):
        """
        초기화.

        Args:
            advisor: 배포 정책 어드바이저
            persist_record: 기록 저장 함수
            emit_metric: 메트릭 발행 함수
            emit_otel_event: OpenTelemetry 이벤트 발행 함수
            alert_adapter: 알림 어댑터 (에스컬레이션용)
        """
        self.advisor = advisor or DeploymentPolicyAdvisor()
        self._persist_record = persist_record
        self._emit_metric = emit_metric
        self._emit_otel_event = emit_otel_event
        self._alert_adapter = alert_adapter

        # In-memory 기록 (영속화 함수가 없는 경우)
        self._records: list[FreezeDecisionRecord] = []

    def record_freeze_acknowledged(
        self,
        decided_by: str,
        justification: str,
    ) -> FreezeDecisionRecord:
        """
        배포 동결 확정 기록.

        운영자가 동결 권고를 확인하고 동결을 확정할 때 호출.

        Args:
            decided_by: 결정자 (사용자명 또는 ID)
            justification: 결정 사유

        Returns:
            FreezeDecisionRecord
        """
        verdict = self.advisor.get_deployment_verdict()

        record = FreezeDecisionRecord(
            decision_id=f"freeze_{now().strftime('%Y%m%d%H%M%S')}",
            decision_type="freeze_acknowledged",
            decided_by=decided_by,
            decided_at=now(),
            budget_remaining_percent=verdict.budget_status.budget_remaining_percent,
            freeze_status=verdict.status,
            justification=justification,
        )

        self._save_and_emit(record)

        logger.info(
            "freeze_decision.freeze_acknowledged",
            decided_by=decided_by,
            verdict=verdict.budget_status.budget_remaining_percent,
        )

        return record

    def record_override_approved(
        self,
        decided_by: str,
        justification: str,
        override_type: OverrideType,
        deployment_id: str | None = None,
        deployment_name: str | None = None,
        expires_hours: int = 4,
    ) -> FreezeDecisionRecord:
        """
        배포 동결 무시(Override) 승인 기록.

        운영자가 동결 권고를 무시하고 배포를 강행할 때 호출.
        에스컬레이션이 활성화된 경우, 상위 채널에 알림을 발송합니다.

        Args:
            decided_by: 결정자
            justification: 결정 사유
            override_type: Override 유형
            deployment_id: 배포 ID
            deployment_name: 배포 이름
            expires_hours: Override 유효 시간

        Returns:
            FreezeDecisionRecord
        """
        verdict = self.advisor.get_deployment_verdict()

        record = FreezeDecisionRecord(
            decision_id=f"override_{now().strftime('%Y%m%d%H%M%S')}",
            decision_type="override_approved",
            decided_by=decided_by,
            decided_at=now(),
            budget_remaining_percent=verdict.budget_status.budget_remaining_percent,
            freeze_status=verdict.status,
            justification=justification,
            override_type=override_type,
            expires_at=now() + timedelta(hours=expires_hours),
            deployment_id=deployment_id,
            deployment_name=deployment_name,
        )

        # Advisor에 활성 Override 등록
        self.advisor._active_overrides[record.decision_id] = record

        self._save_and_emit(record)

        # 에스컬레이션 알림 발송
        self._send_override_escalation(
            override_type=override_type,
            requester=decided_by,
            reason=justification,
            service_name=deployment_name,
        )

        logger.warning(
            "freeze_decision.override_approved",
            decided_by=decided_by,
            override_type=override_type.value,
            deployment_name=deployment_name,
            verdict=verdict.budget_status.budget_remaining_percent,
        )

        return record

    def _send_override_escalation(
        self,
        override_type: OverrideType,
        requester: str,
        reason: str,
        service_name: str | None = None,
    ) -> None:
        """
        Override 에스컬레이션 알림 발송.

        RuntimeConfig의 escalation_enabled가 True일 때만 발송합니다.
        """
        try:
            # 설정 확인
            config = _get_error_budget_config()
            if not config.get("escalation_enabled", True):
                logger.debug("freeze_decision.escalation_disabled_skipping")
                return

            escalation_channel = config.get("escalation_channel", "#governance")
            escalation_mention = config.get("escalation_mention", "@cto @security")

            # 메트릭 기록
            from selfhealing.services.metrics.recorders import (
                record_override_escalation,
            )

            record_override_escalation(override_type.value)

            # AlertAdapter가 있으면 에스컬레이션 알림 발송
            if self._alert_adapter:
                self._alert_adapter.alert_override_escalation(
                    override_type=override_type.value,
                    requester=requester,
                    reason=reason,
                    service_name=service_name,
                    escalation_channel=escalation_channel,
                    escalation_mention=escalation_mention,
                )
                logger.info(
                    "freeze_decision.escalation_alert_sent",
                    override_type=override_type.value,
                    escalation_channel=escalation_channel,
                )
            else:
                logger.warning(
                    "freeze_decision.no_alert_adapter_configured",
                    override_type=override_type.value,
                    requester=requester,
                    reason=reason,
                )
        except Exception as e:
            # 에스컬레이션 실패는 Override 자체를 막지 않음
            logger.exception(
                "freeze_decision.failed_send_escalation",
                error=e,
            )

    def record_freeze_lifted(
        self,
        decided_by: str,
        justification: str,
    ) -> FreezeDecisionRecord:
        """
        배포 동결 해제 기록.

        Error Budget이 회복되거나 운영자가 동결을 해제할 때 호출.

        Args:
            decided_by: 결정자
            justification: 해제 사유

        Returns:
            FreezeDecisionRecord
        """
        verdict = self.advisor.get_deployment_verdict()

        record = FreezeDecisionRecord(
            decision_id=f"lift_{now().strftime('%Y%m%d%H%M%S')}",
            decision_type="freeze_lifted",
            decided_by=decided_by,
            decided_at=now(),
            budget_remaining_percent=verdict.budget_status.budget_remaining_percent,
            freeze_status=verdict.status,
            justification=justification,
        )

        # 활성 Override 모두 해제
        self.advisor._active_overrides.clear()

        self._save_and_emit(record)

        logger.info(
            "freeze_decision.freeze_lifted",
            decided_by=decided_by,
            verdict=verdict.budget_status.budget_remaining_percent,
        )

        return record

    def get_decision_history(
        self,
        limit: int = 50,
        decision_type: str | None = None,
    ) -> list[FreezeDecisionRecord]:
        """
        결정 이력 조회.

        Args:
            limit: 최대 조회 건수
            decision_type: 결정 유형 필터

        Returns:
            결정 기록 목록
        """
        records = self._records

        if decision_type:
            records = [r for r in records if r.decision_type == decision_type]

        return sorted(records, key=lambda r: r.decided_at, reverse=True)[:limit]

    def _save_and_emit(self, record: FreezeDecisionRecord) -> None:
        """기록 저장 및 이벤트 발행."""
        # In-memory 저장
        self._records.append(record)

        # 영속화
        if self._persist_record:
            try:
                self._persist_record(record)
            except Exception as e:
                logger.exception(
                    "freeze_decision.failed_persist_record",
                    error=e,
                )

        # 메트릭 발행
        if self._emit_metric:
            try:
                self._emit_metric(
                    "freeze_decision",
                    {
                        "decision_type": record.decision_type,
                        "freeze_status": record.freeze_status.value,
                        "budget_remaining": record.budget_remaining_percent,
                    },
                )
            except Exception as e:
                logger.warning(
                    "freeze_decision.failed_emit_metric",
                    error=e,
                )

        # OpenTelemetry 이벤트 발행
        if self._emit_otel_event:
            try:
                self._emit_otel_event(
                    f"selfhealing.deployment.{record.decision_type}",
                    record.to_dict(),
                )
            except Exception as e:
                logger.warning(
                    "freeze_decision.failed_emit_otel_event",
                    error=e,
                )
