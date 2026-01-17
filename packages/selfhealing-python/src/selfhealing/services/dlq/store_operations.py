"""
DLQ Store Operations Mixin.

Provides methods for storing failed operations in the DLQ.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class StoreOperationsMixin:
    """Mixin providing DLQ store operations."""

    def store_failure(
        self,
        domain: str,
        failure_type: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        user_id: Optional[int] = None,
        error_code: str = "",
        error_message: str = "",
        snapshot_data: dict[str, Any] | None = None,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        next_action_hint: str = "",
        recommended_action: str = "",
        request: Any = None,
    ):
        """
        Store a failed operation in the DLQ.

        하이브리드 로직:
        - request가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
        - request가 없으면 → 직접 adapter 호출 (Celery 등 비동기 컨텍스트)

        Args:
            domain: Business domain (payment, point, inventory, webhook, notification)
            failure_type: Specific failure type (e.g., PG_TIMEOUT, AMOUNT_MISMATCH)
            entity_type: Type of related entity (e.g., "order", "payment", "product")
            entity_id: ID of related entity
            user_id: Related User ID
            error_code: Error code from external system
            error_message: Human-readable error message
            snapshot_data: State snapshot for recovery
            request_data: Original request payload
            response_data: External system response
            metadata: Additional debug context
            next_action_hint: Guidance for operators
            recommended_action: Suggested action (replay, manual_check, etc.)
            request: Django HttpRequest 객체 (있으면 버퍼에 적재)

        Returns:
            DLQEntryResult with creation status
        """
        from selfhealing.services.dlq_models import DLQEntryResult

        if not self.is_enabled:
            logger.debug("[DLQService] DLQ is disabled, skipping storage")
            return DLQEntryResult.failed("DLQ is disabled")

        try:
            failed_op = self.repository.create(
                domain=domain,
                failure_type=failure_type,
                entity_type=entity_type,
                entity_id=entity_id,
                user_id=user_id,
                error_code=error_code,
                error_message=error_message,
                snapshot_data=snapshot_data,
                request_data=request_data,
                response_data=response_data,
                metadata=metadata,
                next_action_hint=next_action_hint,
                recommended_action=recommended_action,
            )

            logger.info(f"[DLQService] Created DLQ entry: id={failed_op.id}, " f"domain={domain}, failure_type={failure_type}")

            # Push 이벤트 - Gauge 증가 (SafeGauge 사용)
            try:
                from selfhealing.metrics.event_handlers import DLQMetricEventHandler
                DLQMetricEventHandler.on_item_created(domain, failure_type)
            except ImportError:
                pass  # Metrics not available

            # Audit 로깅: DLQ 저장 기록 (버퍼 패턴 지원)
            self._log_dlq_audit(
                action="store",
                dlq_id=failed_op.id,
                domain=domain,
                failure_type=failure_type,
                error_message=error_message,
                success=True,
                request=request,
            )

            return DLQEntryResult.created(failed_op.id)

        except Exception as e:
            logger.error(f"[DLQService] Failed to store in DLQ: {e}")
            return DLQEntryResult.failed(str(e))

    def store_with_forensic_context(
        self,
        domain: str,
        failure_type: str,
        forensic_context: Any,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        user_id: Optional[int] = None,
        error_code: str = "",
        error_message: str = "",
        next_action_hint: str = "",
        recommended_action: str = "",
        request: Any = None,
    ):
        """
        DEPRECATED: ForensicContext has been removed from the system.
        
        Use store_failure() directly instead with explicit parameters.
        This method is kept for backward compatibility but will raise an error.
        
        Args:
            domain: Business domain
            failure_type: Specific failure type
            forensic_context: No longer supported
            ...

        Raises:
            NotImplementedError: ForensicContext is no longer available
        """
        raise NotImplementedError(
            "store_with_forensic_context() is deprecated. "
            "ForensicContext has been removed from the system. "
            "Use store_failure() directly with explicit parameters instead."
        )
