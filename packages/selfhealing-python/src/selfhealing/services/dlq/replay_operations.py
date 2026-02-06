"""
DLQ Replay Operations Mixin.

Provides methods for replaying DLQ entries.
Includes throttle-aware replay for safe re-processing with adaptive throttle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import FailedOperationData
    from selfhealing.services.throttle.adaptive import AdaptiveThrottle

logger = logging.getLogger(__name__)


class ReplayOperationsMixin:
    """Mixin providing DLQ replay operations."""

    def _execute_replay(self, entry: FailedOperationData) -> bool:
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
            logger.warning(f"[DLQService] Replay not allowed for entry {entry.id}: {reason}")
            return False

        # Execute replay
        result = handler.replay(entry)
        return result.success

    def replay(
        self,
        domain: str | None = None,
        batch_size: int = 50,
        request: Any = None,
    ):
        """
        Execute batch replay of pending DLQ entries.

        하이브리드 로직:
        - request가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
        - request가 없으면 → 직접 adapter 호출 (Celery 등 비동기 컨텍스트)

        Args:
            domain: Filter by domain (optional)
            batch_size: Maximum number of entries to process (default 50)
            request: Django HttpRequest 객체 (있으면 버퍼에 적재)

        Returns:
            DLQBatchReplayStats with operation statistics
        """
        from selfhealing.services.dlq_models import DLQBatchReplayStats

        result = DLQBatchReplayStats()

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
                            f"[DLQService] Successfully replayed entry {entry.id}: " f"{entry.domain}/{entry.failure_type}"
                        )
                        # Audit 로깅: Replay 성공 (버퍼 패턴 지원)
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
                        # Audit 로깅: Replay 실패 (버퍼 패턴 지원)
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
                    logger.warning(f"[DLQService] Replay failed for entry {entry.id}: {e}")
                    # Audit 로깅: Replay 예외 (버퍼 패턴 지원)
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

    # =========================================================================
    # Throttle-Aware Replay (DLQ + AdaptiveThrottle 연동)
    # =========================================================================

    def replay_throttle_aware(
        self,
        entry_id: int,
        throttle: AdaptiveThrottle,
    ):
        """
        Throttle 상태를 고려한 안전한 단일 Replay.

        검증 순서:
        1. expires_at TTL 만료 검증 (FailedOperationData.expires_at)
        2. can_retry 재시도 한도 검증 (retry_count < max_retries)
        3. Throttle permit 획득 (store_rejection=False로 DLQ 재저장 방지)
        4. _execute_replay() 파이프라인 (can_replay → replay)

        Args:
            entry_id: 재처리할 DLQ entry ID
            throttle: AdaptiveThrottle 인스턴스

        Returns:
            DLQThrottleReplayResult
        """
        from selfhealing.services.dlq_models import DLQThrottleReplayResult

        entry = self.repository.get_by_id(entry_id)
        if entry is None:
            return DLQThrottleReplayResult(success=False, error="Entry not found")

        # 1. TTL 만료 검증 (FailedOperationData.expires_at, DLQConfig.expiry_hours=72)
        now_utc = datetime.now(timezone.utc)
        if entry.expires_at is not None:
            expires_at_aware = entry.expires_at
            if expires_at_aware.tzinfo is None:
                expires_at_aware = expires_at_aware.replace(tzinfo=timezone.utc)
            if expires_at_aware < now_utc:
                logger.info(f"[DLQService] Entry {entry_id} expired at {entry.expires_at}, " f"skipping replay")
                self.repository.update_status(
                    entry.id,
                    status="expired",
                    resolution_type="ttl_expired",
                    resolution_note=f"Expired at {entry.expires_at.isoformat()}",
                )
                # TTL 만료 메트릭 기록 (Fail-Open)
                try:
                    from selfhealing.services.metrics.definitions import (
                        throttle_replay_ttl_expired_total,
                    )

                    throttle_replay_ttl_expired_total.labels(
                        domain=entry.domain,
                    ).inc()
                except Exception:
                    pass
                return DLQThrottleReplayResult(
                    success=False,
                    error=f"Entry expired at {entry.expires_at.isoformat()}",
                )

        # 2. can_retry 재시도 한도 검증 (retry_count < max_retries)
        if not entry.can_retry:
            logger.warning(f"[DLQService] Entry {entry_id} exhausted retries " f"({entry.retry_count}/{entry.max_retries})")
            self.repository.update_status(
                entry.id,
                status="permanently_failed",
                resolution_type="max_retries_exhausted",
            )
            return DLQThrottleReplayResult(
                success=False,
                error=f"Max retries exhausted ({entry.retry_count}/{entry.max_retries})",
            )

        # 3. Throttle permit 획득 (store_rejection=False: Replay 거부 시 DLQ 재저장 방지)
        check_result = throttle.check(
            key=f"dlq_replay:{entry.domain}:{entry_id}",
            tier_id="standard",
        )

        if not check_result.allowed:
            self.repository.increment_retry_count(entry.id)
            return DLQThrottleReplayResult(
                success=False,
                error=f"Throttle rejected: {check_result.reason}",
                retry_after=check_result.reset_at,
            )

        # 4. _execute_replay() 파이프라인 (can_replay → replay 안전 검증)
        try:
            success = self._execute_replay(entry)

            if success:
                self.resolve_entry(entry.id, notes="throttle_aware_replay")
                return DLQThrottleReplayResult(success=True, entry_id=entry_id)
            else:
                self.repository.increment_retry_count(entry.id)
                return DLQThrottleReplayResult(
                    success=False,
                    entry_id=entry_id,
                    error="Replay handler returned failure",
                )
        except Exception as e:
            self.repository.increment_retry_count(entry.id)
            return DLQThrottleReplayResult(success=False, error=str(e))

    def replay_all_throttle_aware(
        self,
        throttle: AdaptiveThrottle,
        domain: str | None = None,
        batch_size: int = 10,
        max_entries: int = 100,
    ):
        """
        Throttle 상태를 고려한 배치 Replay.

        get_replayable_entries()로 retry_count < max_retries 엔트리만 조회.
        배치 단위로 Throttle 건강 상태를 확인하며 Emergency 시 중단.

        Args:
            throttle: AdaptiveThrottle 인스턴스
            domain: 도메인 필터 (optional)
            batch_size: Throttle 건강 확인 배치 크기
            max_entries: 최대 처리 엔트리 수

        Returns:
            DLQThrottleBatchReplayResult
        """
        from selfhealing.services.dlq_models import DLQThrottleBatchReplayResult

        entries = self.get_replayable_entries(
            domain=domain,
            limit=max_entries,
        )

        results = DLQThrottleBatchReplayResult(
            total=len(entries),
            succeeded=0,
            failed=0,
            skipped=0,
        )

        for i, entry in enumerate(entries):
            # 배치 단위 Throttle Emergency 상태 확인
            if i % batch_size == 0 and i > 0:
                stats = throttle.get_stats()
                if stats.get("emergency", {}).get("level", 0) > 0:
                    results.skipped = len(entries) - i
                    results.early_stop_reason = "emergency_mode_activated"
                    break

            result = self.replay_throttle_aware(
                entry_id=entry.id,
                throttle=throttle,
            )

            if result.success:
                results.succeeded += 1
            else:
                results.failed += 1

        return results
