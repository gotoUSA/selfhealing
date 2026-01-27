"""
DLQ Store Operations Mixin.

Provides methods for storing failed operations in the DLQ.
Includes local file fallback for zero data loss guarantee.

Fallback Strategy:
    1. Primary: Repository (PostgreSQL/Redis)
    2. Fallback: Local JSON file (/tmp/selfhealing_dlq_fallback.jsonl)

Code reference:
    services/coordination/critical_path_fallback.py (CriticalPathFallback 패턴)
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# DLQ Fallback 경로 (selfhealing 소유, 상대 시스템 비침투)
DLQ_FALLBACK_PATH = Path("/tmp/selfhealing_dlq_fallback.jsonl")


class StoreOperationsMixin:
    """Mixin providing DLQ store operations."""

    def store_failure(
        self,
        domain: str,
        failure_type: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        user_id: int | None = None,
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

            logger.info(
                f"[DLQService] Created DLQ entry: id={failed_op.id}, "
                f"domain={domain}, failure_type={failure_type}"
            )

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

            # Local Fallback: 무손실 보장
            entry_data = {
                "domain": domain,
                "failure_type": failure_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "user_id": user_id,
                "error_code": error_code,
                "error_message": error_message,
                "snapshot_data": snapshot_data,
                "request_data": request_data,
                "response_data": response_data,
                "metadata": metadata,
                "next_action_hint": next_action_hint,
                "recommended_action": recommended_action,
            }

            fallback_path = self._write_to_local_fallback(entry_data, str(e))
            if fallback_path:
                logger.warning(f"[DLQService] Fallback to local file: {fallback_path}")
                return DLQEntryResult.fallback(str(e), fallback_path)

            return DLQEntryResult.failed(str(e))

    # =========================================================================
    # Local Fallback (Zero Data Loss)
    # =========================================================================

    _fallback_lock = threading.Lock()

    def _write_to_local_fallback(
        self,
        entry_data: dict[str, Any],
        original_error: str,
    ) -> str | None:
        """
        Local file fallback for zero data loss.

        Repository(DB) 실패 시 로컬 파일에 저장하여 데이터 유실 방지.
        나중에 reconciliation 프로세스가 복구 처리.

        Args:
            entry_data: DLQ 엔트리 데이터
            original_error: 원래 발생한 에러

        Returns:
            저장된 파일 경로 (성공 시), None (실패 시)

        Code reference:
            coordination/critical_path_fallback.py#L230-245
        """
        try:
            with self._fallback_lock:
                DLQ_FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)

                fallback_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "original_error": original_error,
                    "entry_data": entry_data,
                    "pending_reconciliation": True,
                }

                with open(DLQ_FALLBACK_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(fallback_entry, default=str) + "\n")

                logger.info(
                    f"[DLQService] Fallback entry saved: domain={entry_data.get('domain')}"
                )
                return str(DLQ_FALLBACK_PATH)

        except Exception as fallback_error:
            logger.critical(
                f"[DLQService] CRITICAL: Both DB and fallback failed! "
                f"DB error: {original_error}, Fallback error: {fallback_error}"
            )
            return None

    def store_with_forensic_context(
        self,
        domain: str,
        failure_type: str,
        forensic_context: Any,
        entity_type: str | None = None,
        entity_id: str | None = None,
        user_id: int | None = None,
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
