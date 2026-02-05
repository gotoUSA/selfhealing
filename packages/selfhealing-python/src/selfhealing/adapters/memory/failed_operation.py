"""
In-Memory Failed Operation Repository Implementation.

Thread-safe in-memory storage for DLQ (Dead Letter Queue) entries.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from selfhealing.adapters.memory.base import _now
from selfhealing.interfaces.repositories import (
    FailedOperationData,
    FailedOperationRepository,
    FailedOperationStatus,
)


class InMemoryFailedOperationRepository(FailedOperationRepository):
    """
    In-memory implementation of FailedOperationRepository.

    Thread-safe storage for DLQ entries in memory.
    Data is lost when the process exits.

    성능1 개선: status/domain별 인덱스를 유지하여 O(n) 순회를 O(1) 조회로 개선
    """

    def __init__(self):
        self._storage: dict[int, FailedOperationData] = {}
        self._next_id = 1
        self._lock = threading.RLock()  # RLock for reentrant calls

        # 성능1: 인덱스 추가 - status별, domain별 ID 집합
        self._index_by_status: dict[str, set[int]] = {}
        self._index_by_domain: dict[str, set[int]] = {}
        self._index_by_status_domain: dict[tuple[str, str], set[int]] = {}

    def create(
        self,
        domain: str,
        failure_type: str,
        error_message: str = "",
        error_code: str = "",
        entity_type: str | None = None,
        entity_id: str | None = None,
        entity_refs: dict[str, Any] | None = None,
        user_id: int | None = None,
        snapshot_data: dict[str, Any] | None = None,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        retry_count: int = 0,
        max_retries: int = 2,
        next_action_hint: str = "",
        recommended_action: str = "",
    ) -> FailedOperationData:
        """Create a new failed operation record (domain-neutral)."""
        refs = entity_refs or {}

        with self._lock:
            status = FailedOperationStatus.PENDING.value
            entry = FailedOperationData(
                id=self._next_id,
                domain=domain,
                failure_type=failure_type,
                status=status,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_refs=refs,
                user_id=user_id,
                snapshot_data=snapshot_data or {},
                error_code=error_code,
                error_message=error_message,
                retry_count=retry_count,
                max_retries=max_retries,
                request_data=request_data or {},
                response_data=response_data or {},
                metadata=metadata or {},
                next_action_hint=next_action_hint,
                recommended_action=recommended_action,
                created_at=_now(),
                updated_at=_now(),
            )
            self._storage[self._next_id] = entry

            # 성능1: 인덱스 업데이트
            self._add_to_index(self._next_id, status, domain)

            self._next_id += 1
            return entry

    def _add_to_index(self, entry_id: int, status: str, domain: str) -> None:
        """인덱스에 엔트리 추가 (Lock 내부에서 호출)."""
        if status not in self._index_by_status:
            self._index_by_status[status] = set()
        self._index_by_status[status].add(entry_id)

        if domain not in self._index_by_domain:
            self._index_by_domain[domain] = set()
        self._index_by_domain[domain].add(entry_id)

        key = (status, domain)
        if key not in self._index_by_status_domain:
            self._index_by_status_domain[key] = set()
        self._index_by_status_domain[key].add(entry_id)

    def _remove_from_index(self, entry_id: int, status: str, domain: str) -> None:
        """인덱스에서 엔트리 제거 (Lock 내부에서 호출)."""
        if status in self._index_by_status:
            self._index_by_status[status].discard(entry_id)
        if domain in self._index_by_domain:
            self._index_by_domain[domain].discard(entry_id)
        key = (status, domain)
        if key in self._index_by_status_domain:
            self._index_by_status_domain[key].discard(entry_id)

    def _update_index_status(self, entry_id: int, old_status: str, new_status: str, domain: str) -> None:
        """상태 변경 시 인덱스 업데이트 (Lock 내부에서 호출)."""
        # 이전 인덱스에서 제거
        self._remove_from_index(entry_id, old_status, domain)
        # 새 인덱스에 추가
        self._add_to_index(entry_id, new_status, domain)

    def _copy_with_updates(self, entry: FailedOperationData, **updates) -> FailedOperationData:
        """Create a copy of entry with specified field updates."""
        return FailedOperationData(
            id=updates.get("id", entry.id),
            domain=updates.get("domain", entry.domain),
            failure_type=updates.get("failure_type", entry.failure_type),
            status=updates.get("status", entry.status),
            entity_type=updates.get("entity_type", entry.entity_type),
            entity_id=updates.get("entity_id", entry.entity_id),
            entity_refs=updates.get("entity_refs", entry.entity_refs),
            user_id=updates.get("user_id", entry.user_id),
            snapshot_data=updates.get("snapshot_data", entry.snapshot_data),
            error_code=updates.get("error_code", entry.error_code),
            error_message=updates.get("error_message", entry.error_message),
            retry_count=updates.get("retry_count", entry.retry_count),
            max_retries=updates.get("max_retries", entry.max_retries),
            last_retry_at=updates.get("last_retry_at", entry.last_retry_at),
            request_data=updates.get("request_data", entry.request_data),
            response_data=updates.get("response_data", entry.response_data),
            metadata=updates.get("metadata", entry.metadata),
            resolved_at=updates.get("resolved_at", entry.resolved_at),
            resolved_by_id=updates.get("resolved_by_id", entry.resolved_by_id),
            resolution_type=updates.get("resolution_type", entry.resolution_type),
            resolution_note=updates.get("resolution_note", entry.resolution_note),
            next_action_hint=updates.get("next_action_hint", entry.next_action_hint),
            recommended_action=updates.get("recommended_action", entry.recommended_action),
            created_at=updates.get("created_at", entry.created_at),
            updated_at=updates.get("updated_at", _now()),
            expires_at=updates.get("expires_at", entry.expires_at),
        )

    def get_by_id(self, id: int) -> FailedOperationData | None:
        """Get a failed operation by ID."""
        with self._lock:
            return self._storage.get(id)

    def get_pending_by_domain(
        self,
        domain: str,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get pending operations for a specific domain (인덱스 활용)."""
        with self._lock:
            # 성능1: 인덱스 기반 O(k) 조회 (k = 결과 수)
            key = (FailedOperationStatus.PENDING.value, domain)
            entry_ids = self._index_by_status_domain.get(key, set())
            results = []
            for entry_id in entry_ids:
                if len(results) >= limit:
                    break
                entry = self._storage.get(entry_id)
                if entry:
                    results.append(entry)
            return results

    def get_pending_count_by_domain(self, domain: str) -> int:
        """Get count of pending operations for a domain (인덱스 활용)."""
        with self._lock:
            # 성능1: 인덱스 기반 O(1) 카운트
            key = (FailedOperationStatus.PENDING.value, domain)
            return len(self._index_by_status_domain.get(key, set()))

    def update_status(
        self,
        id: int,
        status: str,
        resolution_type: str = "",
        resolution_note: str = "",
        resolved_by_id: int | None = None,
    ) -> bool:
        """Update the status of a failed operation."""
        with self._lock:
            entry = self._storage.get(id)
            if entry is None:
                return False

            old_status = entry.status
            updated = self._copy_with_updates(
                entry,
                status=status,
                resolved_at=(_now() if status == FailedOperationStatus.RESOLVED.value else entry.resolved_at),
                resolved_by_id=resolved_by_id or entry.resolved_by_id,
                resolution_type=resolution_type or entry.resolution_type,
                resolution_note=resolution_note or entry.resolution_note,
            )
            self._storage[id] = updated

            # 성능1: 상태 변경 시 인덱스 업데이트
            if old_status != status:
                self._update_index_status(id, old_status, status, entry.domain)

            return True

    def increment_retry_count(self, id: int) -> bool:
        """Increment retry count and update last_retry_at."""
        with self._lock:
            entry = self._storage.get(id)
            if entry is None:
                return False

            updated = self._copy_with_updates(
                entry,
                retry_count=entry.retry_count + 1,
                last_retry_at=_now(),
            )
            self._storage[id] = updated
            return True

    def mark_as_resolved(
        self,
        id: int,
        resolution_type: str,
        resolution_note: str = "",
        resolved_by_id: int | None = None,
    ) -> bool:
        """Mark a failed operation as resolved."""
        return self.update_status(
            id=id,
            status=FailedOperationStatus.RESOLVED.value,
            resolution_type=resolution_type,
            resolution_note=resolution_note,
            resolved_by_id=resolved_by_id,
        )

    def get_expired_operations(
        self,
        before_date: datetime,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get operations that have expired."""
        with self._lock:
            results = [entry for entry in self._storage.values() if entry.expires_at and entry.expires_at < before_date]
            return results[:limit]

    def bulk_update_status(
        self,
        ids: list[int],
        status: str,
    ) -> int:
        """Bulk update status for multiple operations."""
        count = 0
        for id in ids:
            if self.update_status(id, status):
                count += 1
        return count

    def find_by_status(
        self,
        status: str,
        domain: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations by status with optional filters (인덱스 활용)."""
        with self._lock:
            # 성능1: 인덱스 기반 조회
            if domain:
                key = (status, domain)
                entry_ids = self._index_by_status_domain.get(key, set())
            else:
                entry_ids = self._index_by_status.get(status, set())

            results = []
            for entry_id in entry_ids:
                if len(results) >= limit:
                    break
                entry = self._storage.get(entry_id)
                if entry is None:
                    continue
                if failure_type and entry.failure_type != failure_type:
                    continue
                results.append(entry)
            return results

    def find_replayable(
        self,
        max_retries: int,
        domain: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations that can be replayed (인덱스 활용)."""
        with self._lock:
            # 성능1: PENDING 상태 인덱스 기반 조회
            pending_status = FailedOperationStatus.PENDING.value
            if domain:
                key = (pending_status, domain)
                entry_ids = self._index_by_status_domain.get(key, set())
            else:
                entry_ids = self._index_by_status.get(pending_status, set())

            results = []
            for entry_id in entry_ids:
                if len(results) >= limit:
                    break
                entry = self._storage.get(entry_id)
                if entry is None:
                    continue
                if entry.retry_count >= max_retries:
                    continue
                if failure_type and entry.failure_type != failure_type:
                    continue
                results.append(entry)
            return results

    def find_sla_breached(
        self,
        current_time: datetime,
        sla_thresholds: dict[str, timedelta],
    ) -> list[FailedOperationData]:
        """Find operations that have breached their SLA (인덱스 활용)."""
        with self._lock:
            # 성능1: PENDING 인덱스 기반 조회
            pending_ids = self._index_by_status.get(FailedOperationStatus.PENDING.value, set())
            results = []
            for entry_id in pending_ids:
                entry = self._storage.get(entry_id)
                if entry is None:
                    continue
                threshold = sla_thresholds.get(entry.domain, timedelta(hours=24))
                if entry.created_at and current_time - entry.created_at > threshold:
                    results.append(entry)
            return results

    def find_expired(
        self,
        current_time: datetime,
    ) -> list[FailedOperationData]:
        """Find operations past their retention period."""
        with self._lock:
            return [entry for entry in self._storage.values() if entry.expires_at and entry.expires_at < current_time]

    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about failed operations (인덱스 활용)."""
        with self._lock:
            # 성능1: 인덱스 기반 O(k) 통계 (k = 상태/도메인 종류 수)
            stats = {
                "total": len(self._storage),
                "by_status": {status: len(ids) for status, ids in self._index_by_status.items()},
                "by_domain": {domain: len(ids) for domain, ids in self._index_by_domain.items()},
            }
            return stats

    def try_acquire_for_replay(
        self,
        id: int,
        max_retries: int,
    ) -> FailedOperationData | None:
        """Atomically acquire a DLQ entry for replay."""
        with self._lock:
            entry = self._storage.get(id)
            if entry is None:
                return None
            if entry.status != FailedOperationStatus.PENDING.value:
                return None
            if entry.retry_count >= max_retries:
                return None

            old_status = entry.status
            new_status = "replaying"

            # Atomically update
            updated = self._copy_with_updates(
                entry,
                status=new_status,
                retry_count=entry.retry_count + 1,
                last_retry_at=_now(),
            )
            self._storage[id] = updated

            # 성능1: 인덱스 업데이트
            self._update_index_status(id, old_status, new_status, entry.domain)

            return updated

    def complete_replay(
        self,
        id: int,
        success: bool,
        resolution_type: str = "",
        note: str = "",
        resolved_by_id: int | None = None,
        error_details: dict[str, Any] | None = None,
    ) -> bool:
        """Complete a replay operation by updating the final status."""
        with self._lock:
            entry = self._storage.get(id)
            if entry is None:
                return False

            if success:
                new_status = FailedOperationStatus.RESOLVED.value
                resolved_at = _now()
            else:
                # Revert to pending for retry
                new_status = FailedOperationStatus.PENDING.value
                resolved_at = entry.resolved_at

            updated = self._copy_with_updates(
                entry,
                status=new_status,
                error_message=note or entry.error_message,
                metadata={**(entry.metadata or {}), **(error_details or {})},
                resolved_at=resolved_at,
                resolved_by_id=resolved_by_id or entry.resolved_by_id,
                resolution_type=resolution_type or entry.resolution_type,
                resolution_note=note or entry.resolution_note,
            )
            self._storage[id] = updated
            return True

    def release_stale_replaying(
        self,
        older_than_minutes: int = 30,
    ) -> int:
        """Release DLQ entries stuck in REPLAYING state."""
        cutoff = _now() - timedelta(minutes=older_than_minutes)
        released = 0

        with self._lock:
            for id, entry in list(self._storage.items()):
                if entry.status == "replaying" and entry.last_retry_at and entry.last_retry_at < cutoff:
                    updated = self._copy_with_updates(
                        entry,
                        status=FailedOperationStatus.PENDING.value,
                    )
                    self._storage[id] = updated
                    released += 1

        return released

    def clear(self) -> None:
        """Clear all entries (for testing)."""
        with self._lock:
            self._storage.clear()
            self._next_id = 1

    # =========================================================================
    # Cleanup Operations
    # =========================================================================

    def archive_old_resolved(
        self,
        older_than_days: int = 30,
    ) -> int:
        """Archive resolved entries older than N days."""
        cutoff = _now() - timedelta(days=older_than_days)
        archived_count = 0

        with self._lock:
            for id, entry in list(self._storage.items()):
                if entry.status == FailedOperationStatus.RESOLVED.value and entry.resolved_at and entry.resolved_at < cutoff:
                    updated = self._copy_with_updates(
                        entry,
                        status=FailedOperationStatus.ARCHIVED.value,
                    )
                    self._storage[id] = updated
                    archived_count += 1

        return archived_count

    def _purge_by_ids(self, ids: list[int]) -> int:
        """Purge specific archived entries by ID. Must be called with lock held."""
        purged_count = 0
        for id in ids:
            entry = self._storage.get(id)
            if entry and entry.status == FailedOperationStatus.ARCHIVED.value:
                del self._storage[id]
                purged_count += 1
            elif entry:
                raise ValueError(
                    f"Entry {id} is not archived (status: {entry.status}). " "Only archived entries can be purged."
                )
        return purged_count

    def _purge_older_than(self, older_than_days: int) -> int:
        """Purge archived entries older than N days. Must be called with lock held."""
        cutoff = _now() - timedelta(days=older_than_days)
        to_delete = [
            id
            for id, entry in self._storage.items()
            if entry.status == FailedOperationStatus.ARCHIVED.value and entry.updated_at and entry.updated_at < cutoff
        ]
        for id in to_delete:
            del self._storage[id]
        return len(to_delete)

    def _purge_all_archived(self) -> int:
        """Purge all archived entries. Must be called with lock held."""
        to_delete = [id for id, entry in self._storage.items() if entry.status == FailedOperationStatus.ARCHIVED.value]
        for id in to_delete:
            del self._storage[id]
        return len(to_delete)

    def purge_archived(
        self,
        ids: list[int] | None = None,
        older_than_days: int | None = None,
    ) -> int:
        """Permanently delete archived entries."""
        if ids is not None and older_than_days is not None:
            raise ValueError("Specify either ids or older_than_days, not both")

        with self._lock:
            if ids is not None:
                return self._purge_by_ids(ids)
            elif older_than_days is not None:
                return self._purge_older_than(older_than_days)
            else:
                return self._purge_all_archived()

    def get_cleanup_stats(self) -> dict[str, Any]:
        """Get statistics for cleanup operations."""
        now = _now()
        day_30_ago = now - timedelta(days=30)
        day_90_ago = now - timedelta(days=90)

        with self._lock:
            total = len(self._storage)
            by_status: dict[str, int] = {}
            resolved_older_than_30_days = 0
            archived_older_than_90_days = 0

            for entry in self._storage.values():
                # Count by status
                by_status[entry.status] = by_status.get(entry.status, 0) + 1

                # Count resolved older than 30 days
                if (
                    entry.status == FailedOperationStatus.RESOLVED.value
                    and entry.resolved_at
                    and entry.resolved_at < day_30_ago
                ):
                    resolved_older_than_30_days += 1

                # Count archived older than 90 days
                if entry.status == FailedOperationStatus.ARCHIVED.value and entry.updated_at and entry.updated_at < day_90_ago:
                    archived_older_than_90_days += 1

            return {
                "total": total,
                "by_status": by_status,
                "resolved_older_than_30_days": resolved_older_than_30_days,
                "archived_older_than_90_days": archived_older_than_90_days,
            }
