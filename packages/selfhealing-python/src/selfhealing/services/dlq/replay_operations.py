"""
DLQ Replay Operations Mixin.

Provides methods for replaying DLQ entries.

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §1
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import FailedOperationData

logger = logging.getLogger(__name__)


class ReplayOperationsMixin:
    """Mixin providing DLQ replay operations."""

    def _execute_replay(self, entry: "FailedOperationData") -> bool:
        """
        Execute replay for a single DLQ entry using registered handler.
        
        Args:
            entry: The failed operation entry to replay
            
        Returns:
            True if replay succeeded, False otherwise
        """
        from selfhealing.services.replay_service import get_replay_handler
        
        handler = get_replay_handler(entry.domain)
        
        # Check if replay is allowed
        can_replay, reason = handler.can_replay(entry)
        if not can_replay:
            logger.warning(
                f"[DLQService] Replay not allowed for entry {entry.id}: {reason}"
            )
            return False
        
        # Execute replay
        result = handler.replay(entry)
        return result.success

    def replay(
        self,
        domain: Optional[str] = None,
        batch_size: int = 50,
        request: Any = None,
    ):
        """
        Execute batch replay of pending DLQ entries.

        Phase 2 하이브리드 로직 (56_AUDIT_MIDDLEWARE_DESIGN.md):
        - request가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
        - request가 없으면 → 직접 adapter 호출 (Celery 등 비동기 컨텍스트)

        Args:
            domain: Filter by domain (optional)
            batch_size: Maximum number of entries to process (default 50)
            request: Django HttpRequest 객체 (있으면 버퍼에 적재)

        Returns:
            ReplayResult with operation statistics
        """
        from selfhealing.services.dlq_models import ReplayResult

        result = ReplayResult()

        try:
            entries = self.get_pending_entries(domain=domain, limit=batch_size)
            result.processed = len(entries)

            for entry in entries:
                try:
                    # Execute replay via registered handler
                    replay_success = self._execute_replay(entry)
                    
                    if replay_success:
                        self.resolve_entry(entry.id, "auto_replay")
                        result.success += 1
                        logger.info(
                            f"[DLQService] Successfully replayed entry {entry.id}: "
                            f"{entry.domain}/{entry.failure_type}"
                        )
                        # Audit 로깅: Replay 성공 (Phase 2: 버퍼 패턴 지원)
                        self._log_dlq_audit(
                            action="replay",
                            dlq_id=entry.id,
                            domain=entry.domain,
                            success=True,
                            request=request,
                        )
                    else:
                        result.failed += 1
                        result.errors.append(f"Entry {entry.id}: Replay handler returned failure")
                        # Audit 로깅: Replay 실패 (Phase 2: 버퍼 패턴 지원)
                        self._log_dlq_audit(
                            action="replay",
                            dlq_id=entry.id,
                            domain=entry.domain,
                            success=False,
                            error_message="Replay handler returned failure",
                            request=request,
                        )
                except Exception as e:
                    result.failed += 1
                    result.errors.append(f"Entry {entry.id}: {str(e)}")
                    logger.warning(
                        f"[DLQService] Replay failed for entry {entry.id}: {e}"
                    )
                    # Audit 로깅: Replay 예외 (Phase 2: 버퍼 패턴 지원)
                    self._log_dlq_audit(
                        action="replay",
                        dlq_id=entry.id,
                        domain=entry.domain,
                        success=False,
                        error_message=str(e),
                        request=request,
                    )

            logger.info(
                f"[DLQService] Replay completed: domain={domain}, "
                f"processed={result.processed}, success={result.success}, "
                f"failed={result.failed}"
            )

        except Exception as e:
            logger.error(f"[DLQService] Replay failed: {e}")
            result.errors.append(str(e))

        return result
