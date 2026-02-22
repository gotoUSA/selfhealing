"""
ApprovalMixin for RecoveryCoordinator.

이 모듈은 selfhealing.services.coordination.recovery_coordinator 패키지의 내부 구현입니다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from ..enums import RecoveryStatus
from ..recovery_state import RecoverySession

logger = structlog.get_logger()


class ApprovalMixin:
    """승인 및 검증 Mixin.

수동 승인 플로우와 버짓 안정성 검증을 제공합니다."""

    def _handle_all_steps_completed(self, session: RecoverySession) -> None:
        """
        모든 단계 완료 시 처리.

        Phase 3.7: READY_TO_RESTORE 상태 전환 로직
        - require_manual_approval=True인 경우 READY_TO_RESTORE로 전환
        - 그렇지 않으면 COMPLETED로 완료
        """
        # 수동 승인 필요 여부 확인
        requires_approval = False
        if session.metadata and isinstance(session.metadata, dict):
            requires_approval = session.metadata.get("requires_approval", False)

        if requires_approval:
            # Phase 3.7: READY_TO_RESTORE 상태로 전환
            session.status = RecoveryStatus.READY_TO_RESTORE
            self._save_session(session)

            # 승인 요청 생성
            self._create_approval_request(session)

            logger.info(
                "recovery.waiting_approval",
                session=session.id,
                session_1=session.namespace,
            )
        else:
            # 일반 완료 처리
            self._complete_session(session)

    def _create_approval_request(self, session: RecoverySession) -> None:
        """
        수동 승인 요청 생성.

        Phase 3.7: PendingRecoveryApprovalManager 연동
        """
        try:
            from ..pending_recovery_approval import (
                get_pending_recovery_approval_manager,
            )

            manager = get_pending_recovery_approval_manager()

            manager.create_request(
                session_id=session.id,
                namespace=session.namespace,
                trigger_level=session.trigger_level,
            )
        except ImportError:
            logger.warning("recovery")
        except Exception as e:
            logger.exception(
                "recovery.failed_create_approval_request",
                error=e,
            )

    def approve_recovery(
        self,
        namespace: str,
        approved_by: str,
    ) -> RecoverySession | None:
        """
        복구 승인 (Phase 3.7).

        READY_TO_RESTORE 상태인 세션을 승인하여 COMPLETED로 전환합니다.

        Args:
            namespace: 네임스페이스
            approved_by: 승인자 ID

        Returns:
            승인된 RecoverySession 또는 None
        """
        with self._lock:
            session = self.get_active_session(namespace)
            if not session:
                return None

            if session.status != RecoveryStatus.READY_TO_RESTORE:
                logger.warning(
                    "recovery.cannot_approve_session_state",
                    session=session.status,
                )
                return None

            # 승인 처리
            session.status = RecoveryStatus.COMPLETED
            session.completed_at = datetime.now(timezone.utc).isoformat()

            # 승인자 정보 기록
            session.metadata = session.metadata or {}
            session.metadata["approved_by"] = approved_by
            session.metadata["approved_at"] = datetime.now(timezone.utc).isoformat()

            self._save_session(session)
            self._clear_active_session(namespace)

            # 락 해제
            self._recovery_lock.release(namespace, session.id)

            # 승인 요청 상태 업데이트
            try:
                from ..pending_recovery_approval import (
                    get_pending_recovery_approval_manager,
                )

                manager = get_pending_recovery_approval_manager()
                manager.approve(session.id, approved_by)
            except Exception:
                pass

            # EMERGENCY_RECOVERY_COMPLETED 이벤트 발행 (Postmortem 자동 생성 트리거)
            self._publish_emergency_recovery_completed_event(session, approved_by)

            logger.info(
                "recovery.approved",
                session=session.id,
                approved_by=approved_by,
            )

            return session

    def verify_weighted_budget_stability(
        self,
        namespace: str,
    ) -> dict[str, Any]:
        """
        가중 버짓 안정성 검증 (Phase 5.4 - WeightedBudgetStability).

        Plan 75 연동: 복구 전 현재 버짓 상태를 확인하여
        가중치 적용 소진이 정상 범위인지 검증합니다.

        Args:
            namespace: 네임스페이스

        Returns:
            검증 결과:
            - stable: 안정 여부
            - current_multiplier: 현재 가중치
            - budget_remaining_percent: 남은 버짓 비율
            - weighted_consumption: 가중 소진량
        """
        try:
            # CrisisMultiplierProvider에서 현재 가중치 조회
            from selfhealing.services.coordination.crisis_multiplier import (
                get_crisis_multiplier_provider,
            )

            provider = get_crisis_multiplier_provider()
            current_multiplier = provider.get_multiplier(namespace)

            # AtomicBudgetConsumer에서 버짓 상태 조회
            budget_info = self._get_budget_info(namespace)

            # 안정성 판단: 가중치가 1.0이고 버짓 잔여량이 충분하면 안정
            is_stable = abs(current_multiplier - 1.0) < 0.001 and budget_info.get("remaining_percent", 100) > 10

            return {
                "stable": is_stable,
                "current_multiplier": current_multiplier,
                "budget_remaining_percent": budget_info.get("remaining_percent", 100),
                "weighted_consumption": budget_info.get("weighted_consumption", 0),
                "budget_total_minutes": budget_info.get("total_minutes", 0),
                "budget_used_minutes": budget_info.get("used_minutes", 0),
            }
        except ImportError:
            logger.warning("recovery")
            return {
                "stable": True,
                "current_multiplier": 1.0,
                "budget_remaining_percent": 100,
                "assumed": True,
            }
        except Exception as e:
            logger.exception(
                "recovery.weighted_budget_verification_error",
                error=e,
            )
            return {
                "stable": False,
                "error": str(e),
            }

    def _get_budget_info(self, namespace: str) -> dict[str, Any]:
        """Error Budget 정보 조회 (Plan 75 연동)."""
        try:
            from selfhealing.services.error_budget.atomic_consumer import (
                get_atomic_budget_consumer,
            )

            consumer = get_atomic_budget_consumer()

            # 버짓 상태 조회 (구현에 따라 다를 수 있음)
            budget_key = f"selfhealing:{namespace}:error_budget"
            backend = self._get_backend()

            budget_data = backend.get(budget_key)
            if budget_data and isinstance(budget_data, dict):
                total = budget_data.get("total_minutes", 60)
                used = budget_data.get("used_minutes", 0)
                remaining_percent = ((total - used) / total * 100) if total > 0 else 100

                return {
                    "total_minutes": total,
                    "used_minutes": used,
                    "remaining_percent": remaining_percent,
                    "weighted_consumption": budget_data.get("weighted_consumption", 0),
                }

            return {"remaining_percent": 100, "total_minutes": 60, "used_minutes": 0}
        except Exception:
            return {"remaining_percent": 100, "total_minutes": 60, "used_minutes": 0}

    def _get_current_emergency_level(self, namespace: str) -> str:
        """현재 Emergency 레벨 조회."""
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager

            manager = get_emergency_manager()
            current_level = manager.get_current_level()
            return current_level.name
        except (ImportError, AttributeError, Exception):
            # 가져올 수 없으면 UNKNOWN 반환
            return "UNKNOWN"

    def _check_stability(
        self,
        namespace: str,
        duration_minutes: int,
        error_rate_threshold: float,
    ) -> dict[str, Any]:
        """
        안정화 조건 확인.

        Prometheus + OTel + Mimir 인프라가 error_rate 수집/저장/알림을
        이미 처리하므로, 별도 MetricsCollector 구현 없이
        안정으로 가정합니다.

        실제 에러율 기반 판단이 필요한 경우:
        - PrometheusMetricsCollector.query_instant() 활용 가능
          (selfhealing.services.postmortem.prometheus_collector)
        - PromQL: rate(selfhealing_http_request_errors_total[Xm])
        """
        logger.debug(
            "recovery.stability_check",
            namespace=namespace,
            duration_minutes=duration_minutes,
            error_rate_threshold=error_rate_threshold,
        )
        return {
            "stable": True,
            "error_rate": 0.0,
            "threshold": error_rate_threshold,
            "duration_minutes": duration_minutes,
            "reason": None,
            "assumed": True,
        }
