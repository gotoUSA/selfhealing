"""
Redis-based DLQ (Dead Letter Queue) Repository.

Implements DLQ storage using ResilientStorageBackend.
Provides zero data loss guarantees through WAL-First protocol.

Redis Key Structure:
- dlq:{id} → Hash (item data)
- dlq:pending → Sorted Set (pending queue, score=timestamp)
- dlq:id_seq → String (ID sequence counter)
- dlq:by_domain:{domain} → Set (items by domain for filtering)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from selfhealing.interfaces.repositories import (
    FailedOperationRepository,
    FailedOperationData,
    FailedOperationStatus,
)

if TYPE_CHECKING:
    from selfhealing.adapters.resilient.backend import ResilientStorageBackend

logger = logging.getLogger(__name__)


class RedisDLQRepository(FailedOperationRepository):
    """
    Redis-based Dead Letter Queue Repository.
    
    Uses ResilientStorageBackend for:
    - Normal mode: Redis storage with sorted sets for efficient querying
    - Degraded mode: Memory + WAL (zero data loss)
    
    Key Structure:
    - dlq:{id} → Hash with entry data
    - dlq:pending → Sorted Set for pending entries (score = timestamp)
    - dlq:id_seq → Counter for ID generation
    """
    
    KEY_PREFIX = "dlq:"
    PENDING_KEY = "dlq:pending"
    ID_SEQ_KEY = "dlq:id_seq"
    BY_DOMAIN_PREFIX = "dlq:by_domain:"
    
    def __init__(self, backend: "ResilientStorageBackend"):
        """
        Initialize Redis DLQ Repository.
        
        Args:
            backend: ResilientStorageBackend instance
        """
        self._backend = backend
    
    def _make_key(self, entry_id: int) -> str:
        """Generate storage key for entry."""
        return f"{self.KEY_PREFIX}{entry_id}"
    
    # =========================================================================
    # Interface Implementation
    # =========================================================================
    
    def create(
        self,
        domain: str,
        failure_type: str,
        error_message: str = "",
        error_code: str = "",
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        entity_refs: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
        snapshot_data: Optional[Dict[str, Any]] = None,
        request_data: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        retry_count: int = 0,
        max_retries: int = 2,
        next_action_hint: str = "",
        recommended_action: str = "",
        expires_at: Optional[datetime] = None,
    ) -> FailedOperationData:
        """
        Create a new DLQ entry.
        
        Args:
            domain: Domain classification (e.g., 'payment', 'order')
            failure_type: Type of failure
            error_message: Error message
            error_code: Error code
            entity_type: Type of entity (e.g., 'order', 'payment')
            entity_id: Entity identifier
            entity_refs: Additional entity references
            user_id: User ID if applicable
            snapshot_data: Snapshot of data at failure time
            request_data: Original request data
            response_data: Response data if available
            metadata: Additional metadata
            retry_count: Initial retry count
            max_retries: Maximum retry attempts
            next_action_hint: Hint for next action
            recommended_action: Recommended resolution action
            expires_at: Expiration time
            
        Returns:
            Created FailedOperationData
        """
        import json
        
        # Generate ID
        entry_id = self._backend.incr(self.ID_SEQ_KEY)
        
        now = datetime.now(timezone.utc)
        
        # Build entry data
        data = {
            "id": str(entry_id),
            "domain": domain,
            "failure_type": failure_type,
            "error_message": error_message,
            "error_code": error_code,
            "status": FailedOperationStatus.PENDING.value,
            "entity_type": entity_type or "",
            "entity_id": entity_id or "",
            "entity_refs": json.dumps(entity_refs or {}),
            "user_id": str(user_id) if user_id is not None else "",
            "snapshot_data": json.dumps(snapshot_data or {}),
            "request_data": json.dumps(request_data or {}),
            "response_data": json.dumps(response_data or {}),
            "metadata": json.dumps(metadata or {}),
            "retry_count": str(retry_count),
            "max_retries": str(max_retries),
            "next_action_hint": next_action_hint,
            "recommended_action": recommended_action,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else "",
        }
        
        # Store entry
        self._backend.hset(self._make_key(entry_id), data)
        
        # Add to pending queue (sorted by timestamp)
        self._backend.zadd(self.PENDING_KEY, {str(entry_id): time.time()})
        
        # Add to domain index
        domain_key = f"{self.BY_DOMAIN_PREFIX}{domain}"
        self._backend.zadd(domain_key, {str(entry_id): time.time()})
        
        logger.info(
            f"[RedisDLQ] Created entry {entry_id} for domain={domain} "
            f"failure_type={failure_type}"
        )
        
        # Return the created entry as FailedOperationData
        return self._to_data(data)
    
    def get(self, entry_id: int) -> Optional[FailedOperationData]:
        """
        Get DLQ entry by ID.
        
        Args:
            entry_id: Entry ID
            
        Returns:
            FailedOperationData if exists, None otherwise
        """
        data = self._backend.hgetall(self._make_key(entry_id))
        if not data:
            return None
        return self._to_data(data)
    
    def update(
        self,
        entry_id: int,
        status: Optional[str] = None,
        retry_count: Optional[int] = None,
        last_retry_at: Optional[datetime] = None,
        resolution_type: Optional[str] = None,
        resolution_note: Optional[str] = None,
        resolved_at: Optional[datetime] = None,
        resolved_by_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Update DLQ entry.
        
        Args:
            entry_id: Entry ID
            status: New status
            retry_count: Updated retry count
            last_retry_at: Last retry timestamp
            resolution_type: Type of resolution
            resolution_note: Resolution notes
            resolved_at: Resolution timestamp
            resolved_by_id: User who resolved
            metadata: Additional metadata to merge
            
        Returns:
            True on success
        """
        import json
        
        now = datetime.now(timezone.utc)
        updates = {"updated_at": now.isoformat()}
        
        if status is not None:
            updates["status"] = status
            
            # Remove from pending if resolved
            if status in [
                FailedOperationStatus.RESOLVED.value,
                FailedOperationStatus.REJECTED.value,
                FailedOperationStatus.ARCHIVED.value,
            ]:
                self._backend.zrem(self.PENDING_KEY, str(entry_id))
        
        if retry_count is not None:
            updates["retry_count"] = str(retry_count)
        if last_retry_at is not None:
            updates["last_retry_at"] = last_retry_at.isoformat()
        if resolution_type is not None:
            updates["resolution_type"] = resolution_type
        if resolution_note is not None:
            updates["resolution_note"] = resolution_note
        if resolved_at is not None:
            updates["resolved_at"] = resolved_at.isoformat()
        if resolved_by_id is not None:
            updates["resolved_by_id"] = str(resolved_by_id)
        if metadata is not None:
            # Merge with existing metadata
            existing = self._backend.hget(self._make_key(entry_id), "metadata")
            if existing:
                try:
                    existing_meta = json.loads(existing)
                    existing_meta.update(metadata)
                    metadata = existing_meta
                except (json.JSONDecodeError, TypeError):
                    pass
            updates["metadata"] = json.dumps(metadata)
        
        return self._backend.hset(self._make_key(entry_id), updates)
    
    def delete(self, entry_id: int) -> bool:
        """
        Delete DLQ entry.
        
        Args:
            entry_id: Entry ID
            
        Returns:
            True on success
        """
        # Get domain for index cleanup
        data = self._backend.hgetall(self._make_key(entry_id))
        if data:
            domain = data.get("domain", "")
            if domain:
                domain_key = f"{self.BY_DOMAIN_PREFIX}{domain}"
                self._backend.zrem(domain_key, str(entry_id))
        
        # Remove from pending
        self._backend.zrem(self.PENDING_KEY, str(entry_id))
        
        # Delete entry
        return self._backend.delete(self._make_key(entry_id))
    
    # =========================================================================
    # Query Methods
    # =========================================================================
    
    def get_pending(self, limit: int = 100) -> List[FailedOperationData]:
        """
        Get pending entries.
        
        Args:
            limit: Maximum entries to return
            
        Returns:
            List of pending FailedOperationData
        """
        # Get IDs from pending sorted set
        pending_ids = self._backend.zrange(self.PENDING_KEY, 0, limit - 1)
        
        results = []
        for entry_id in pending_ids:
            data = self._backend.hgetall(self._make_key(int(entry_id)))
            if data and data.get("status") == FailedOperationStatus.PENDING.value:
                results.append(self._to_data(data))
        
        return results
    
    def get_by_domain(
        self,
        domain: str,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[FailedOperationData]:
        """
        Get entries by domain.
        
        Args:
            domain: Domain to filter by
            status: Optional status filter
            limit: Maximum entries to return
            
        Returns:
            List of FailedOperationData
        """
        domain_key = f"{self.BY_DOMAIN_PREFIX}{domain}"
        entry_ids = self._backend.zrange(domain_key, 0, limit - 1)
        
        results = []
        for entry_id in entry_ids:
            data = self._backend.hgetall(self._make_key(int(entry_id)))
            if data:
                if status is None or data.get("status") == status:
                    results.append(self._to_data(data))
        
        return results
    
    def get_by_status(
        self,
        status: str,
        limit: int = 100,
    ) -> List[FailedOperationData]:
        """
        Get entries by status.
        
        Args:
            status: Status to filter by
            limit: Maximum entries to return
            
        Returns:
            List of FailedOperationData
        """
        # For pending status, use the pending sorted set
        if status == FailedOperationStatus.PENDING.value:
            return self.get_pending(limit)
        
        # In degraded mode, scan memory
        if self._backend.is_degraded:
            return self._get_by_status_from_memory(status, limit)
        
        # In Redis mode, scan keys
        return self._get_by_status_from_redis(status, limit)
    
    def _is_valid_entry_key(self, key: str) -> bool:
        """Check if key is a valid entry key (not a special key)."""
        if not key.startswith(self.KEY_PREFIX):
            return False
        special_prefixes = ["dlq:pending", "dlq:by_domain", "dlq:id_seq"]
        return not any(key.startswith(prefix) for prefix in special_prefixes)
    
    def _get_by_status_from_memory(
        self,
        status: str,
        limit: int,
    ) -> List[FailedOperationData]:
        """Get entries by status from in-memory storage (degraded mode)."""
        results = []
        for key, value in self._backend._memory.items():
            if not self._is_valid_entry_key(key):
                continue
            if isinstance(value, dict) and value.get("status") == status:
                results.append(self._to_data(value))
                if len(results) >= limit:
                    break
        return results
    
    def _decode_redis_data(self, data: dict) -> dict:
        """Decode bytes to strings in Redis hash data."""
        return {
            (k.decode() if isinstance(k, bytes) else k): 
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in data.items()
        }
    
    def _get_by_status_from_redis(
        self,
        status: str,
        limit: int,
    ) -> List[FailedOperationData]:
        """Get entries by status from Redis (normal mode)."""
        results = []
        try:
            pattern = f"{self._backend.config.key_prefix}{self.KEY_PREFIX}[0-9]*"
            cursor = 0
            special_keys = ["pending", "by_domain", "id_seq"]
            
            while len(results) < limit:
                cursor, keys = self._backend._redis._redis.scan(
                    cursor, match=pattern, count=100
                )
                
                for key in keys:
                    if isinstance(key, bytes):
                        key = key.decode()
                    
                    if any(special in key for special in special_keys):
                        continue
                    
                    data = self._backend._redis._redis.hgetall(key)
                    if data:
                        decoded = self._decode_redis_data(data)
                        if decoded.get("status") == status:
                            results.append(self._to_data(decoded))
                            if len(results) >= limit:
                                break
                
                if cursor == 0:
                    break
                    
        except Exception as e:
            logger.error(f"[RedisDLQ] get_by_status error: {e}")
        
        return results
    
    def get_retry_candidates(self, limit: int = 50) -> List[FailedOperationData]:
        """
        Get entries that can be retried.
        
        Returns entries that are PENDING and have retries remaining.
        
        Args:
            limit: Maximum entries to return
            
        Returns:
            List of FailedOperationData that can be retried
        """
        pending = self.get_pending(limit * 2)  # Get extra for filtering
        
        candidates = []
        for entry in pending:
            if entry.retry_count < entry.max_retries:
                candidates.append(entry)
                if len(candidates) >= limit:
                    break
        
        return candidates
    
    def count_by_status(self, status: str) -> int:
        """
        Count entries by status.
        
        Args:
            status: Status to count
            
        Returns:
            Count of entries with given status
        """
        if status == FailedOperationStatus.PENDING.value:
            return self._backend.zcard(self.PENDING_KEY)
        
        # For other statuses, count via get_by_status
        # This is inefficient - consider adding counters
        return len(self.get_by_status(status, limit=10000))
    
    def count_pending(self) -> int:
        """Get count of pending entries."""
        return self._backend.zcard(self.PENDING_KEY)
    
    # =========================================================================
    # Resolution Methods
    # =========================================================================
    
    def mark_resolved(
        self,
        entry_id: int,
        resolution_type: str,
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """
        Mark entry as resolved.
        
        Args:
            entry_id: Entry ID
            resolution_type: Type of resolution
            resolution_note: Resolution notes
            resolved_by_id: User who resolved
            
        Returns:
            True on success
        """
        return self.update(
            entry_id=entry_id,
            status=FailedOperationStatus.RESOLVED.value,
            resolution_type=resolution_type,
            resolution_note=resolution_note,
            resolved_at=datetime.now(timezone.utc),
            resolved_by_id=resolved_by_id,
        )
    
    def mark_rejected(
        self,
        entry_id: int,
        reason: str = "",
        rejected_by_id: Optional[int] = None,
    ) -> bool:
        """
        Mark entry as rejected.
        
        Args:
            entry_id: Entry ID
            reason: Rejection reason
            rejected_by_id: User who rejected
            
        Returns:
            True on success
        """
        return self.update(
            entry_id=entry_id,
            status=FailedOperationStatus.REJECTED.value,
            resolution_type="rejected",
            resolution_note=reason,
            resolved_at=datetime.now(timezone.utc),
            resolved_by_id=rejected_by_id,
        )
    
    def increment_retry(self, entry_id: int) -> int:
        """
        Increment retry count.
        
        Args:
            entry_id: Entry ID
            
        Returns:
            New retry count
        """
        data = self._backend.hgetall(self._make_key(entry_id))
        if not data:
            return 0
        
        current_count = int(data.get("retry_count", 0))
        new_count = current_count + 1
        
        self.update(
            entry_id=entry_id,
            retry_count=new_count,
            last_retry_at=datetime.now(timezone.utc),
        )
        
        # Check if max retries exceeded
        max_retries = int(data.get("max_retries", 2))
        if new_count >= max_retries:
            self.update(
                entry_id=entry_id,
                status=FailedOperationStatus.REQUIRES_REVIEW.value,
            )
        
        return new_count
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _to_data(self, data: Dict[str, Any]) -> FailedOperationData:
        """Convert dict to FailedOperationData."""
        import json
        
        def parse_json(value: Any) -> Dict[str, Any]:
            if not value or value == "":
                return {}
            try:
                if isinstance(value, str):
                    return json.loads(value)
                return value
            except (json.JSONDecodeError, TypeError):
                return {}
        
        def parse_int(value: Any) -> int:
            if value is None or value == "":
                return 0
            try:
                return int(value)
            except (ValueError, TypeError):
                return 0
        
        def parse_datetime(value: Optional[str]) -> Optional[datetime]:
            if not value or value == "":
                return None
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return None
        
        return FailedOperationData(
            id=parse_int(data.get("id")),
            domain=data.get("domain", ""),
            failure_type=data.get("failure_type", ""),
            status=data.get("status", FailedOperationStatus.PENDING.value),
            entity_type=data.get("entity_type") or None,
            entity_id=data.get("entity_id") or None,
            entity_refs=parse_json(data.get("entity_refs")),
            user_id=parse_int(data.get("user_id")) or None,
            snapshot_data=parse_json(data.get("snapshot_data")),
            error_code=data.get("error_code", ""),
            error_message=data.get("error_message", ""),
            retry_count=parse_int(data.get("retry_count")),
            max_retries=parse_int(data.get("max_retries")) or 2,
            last_retry_at=parse_datetime(data.get("last_retry_at")),
            request_data=parse_json(data.get("request_data")),
            response_data=parse_json(data.get("response_data")),
            metadata=parse_json(data.get("metadata")),
            resolved_at=parse_datetime(data.get("resolved_at")),
            resolved_by_id=parse_int(data.get("resolved_by_id")) or None,
            resolution_type=data.get("resolution_type", ""),
            resolution_note=data.get("resolution_note", ""),
            next_action_hint=data.get("next_action_hint", ""),
            recommended_action=data.get("recommended_action", ""),
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("updated_at")),
            expires_at=parse_datetime(data.get("expires_at")),
        )
    
    # =========================================================================
    # Interface Required Methods (Aliases and Additional Implementations)
    # =========================================================================
    
    def get_by_id(self, id: int) -> Optional[FailedOperationData]:
        """Get a failed operation by ID (alias for get)."""
        return self.get(id)
    
    def get_pending_by_domain(
        self,
        domain: str,
        limit: int = 100,
    ) -> List[FailedOperationData]:
        """Get pending operations for a specific domain."""
        return self.get_by_domain(
            domain=domain,
            status=FailedOperationStatus.PENDING.value,
            limit=limit,
        )
    
    def get_pending_count_by_domain(self, domain: str) -> int:
        """Get count of pending operations for a domain."""
        return len(self.get_pending_by_domain(domain, limit=10000))
    
    def update_status(
        self,
        id: int,
        status: str,
        resolution_type: str = "",
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Update the status of a failed operation."""
        return self.update(
            entry_id=id,
            status=status,
            resolution_type=resolution_type if resolution_type else None,
            resolution_note=resolution_note if resolution_note else None,
            resolved_by_id=resolved_by_id,
            resolved_at=datetime.now(timezone.utc) if resolved_by_id else None,
        )
    
    def increment_retry_count(self, id: int) -> bool:
        """Increment retry count and update last_retry_at."""
        new_count = self.increment_retry(id)
        return new_count > 0
    
    def mark_as_resolved(
        self,
        id: int,
        resolution_type: str,
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Mark a failed operation as resolved."""
        return self.mark_resolved(
            entry_id=id,
            resolution_type=resolution_type,
            resolution_note=resolution_note,
            resolved_by_id=resolved_by_id,
        )
    
    def get_expired_operations(
        self,
        before_date: datetime,
        limit: int = 100,
    ) -> List[FailedOperationData]:
        """Get operations that have expired."""
        results = []
        
        # Scan for entries with expires_at before the given date
        pending = self.get_pending(limit=limit * 2)
        
        for entry in pending:
            if entry.expires_at and entry.expires_at < before_date:
                results.append(entry)
                if len(results) >= limit:
                    break
        
        return results
    
    def bulk_update_status(
        self,
        ids: List[int],
        status: str,
    ) -> int:
        """Bulk update status for multiple operations."""
        updated_count = 0
        for id in ids:
            if self.update_status(id, status):
                updated_count += 1
        return updated_count
    
    def find_by_status(
        self,
        status: str,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[FailedOperationData]:
        """Find operations by status with optional filters."""
        # Get by status first
        entries = self.get_by_status(status, limit=limit * 2)
        
        # Apply filters
        results = []
        for entry in entries:
            if domain and entry.domain != domain:
                continue
            if failure_type and entry.failure_type != failure_type:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        
        return results
    
    def find_replayable(
        self,
        max_retries: int,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[FailedOperationData]:
        """Find operations that can be replayed."""
        pending = self.find_by_status(
            status=FailedOperationStatus.PENDING.value,
            domain=domain,
            failure_type=failure_type,
            limit=limit * 2,
        )
        
        results = []
        for entry in pending:
            if entry.retry_count < max_retries:
                results.append(entry)
                if len(results) >= limit:
                    break
        
        return results
    
    def find_sla_breached(
        self,
        current_time: datetime,
        sla_thresholds: Dict[str, "timedelta"],
    ) -> List[FailedOperationData]:
        """Find operations that have breached their SLA."""
        pending = self.get_pending(limit=1000)
        
        results = []
        default_sla = sla_thresholds.get("default", timedelta(hours=24))
        
        for entry in pending:
            if entry.created_at:
                threshold = sla_thresholds.get(entry.domain, default_sla)
                deadline = entry.created_at + threshold
                if current_time > deadline:
                    results.append(entry)
        
        return results
    
    def find_expired(
        self,
        current_time: datetime,
    ) -> List[FailedOperationData]:
        """Find operations past their retention period."""
        return self.get_expired_operations(current_time)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about failed operations."""
        pending_count = self.count_pending()
        resolved_count = len(self.get_by_status(FailedOperationStatus.RESOLVED.value, limit=10000))
        requires_review_count = len(self.get_by_status(FailedOperationStatus.REQUIRES_REVIEW.value, limit=10000))
        rejected_count = len(self.get_by_status(FailedOperationStatus.REJECTED.value, limit=10000))
        archived_count = len(self.get_by_status(FailedOperationStatus.ARCHIVED.value, limit=10000))
        
        return {
            "total": pending_count + resolved_count + requires_review_count + rejected_count + archived_count,
            "pending": pending_count,
            "resolved": resolved_count,
            "requires_review": requires_review_count,
            "rejected": rejected_count,
            "archived": archived_count,
        }
    
    # =========================================================================
    # Atomic Operations for Concurrency Safety
    # =========================================================================
    
    def try_acquire_for_replay(
        self,
        id: int,
        max_retries: int,
    ) -> Optional[FailedOperationData]:
        """
        Atomically acquire a DLQ entry for replay.
        
        Note: Redis doesn't provide true row-level locking.
        This implementation uses optimistic locking via status check.
        """
        entry = self.get(id)
        if not entry:
            return None
        
        # Check eligibility
        if entry.status != FailedOperationStatus.PENDING.value:
            return None
        if entry.retry_count >= max_retries:
            return None
        
        # Atomically update status to REPLAYING
        now = datetime.now(timezone.utc)
        success = self.update(
            entry_id=id,
            status="replaying",
            retry_count=entry.retry_count + 1,
            last_retry_at=now,
        )
        
        if success:
            # Return updated entry
            return self.get(id)
        
        return None
    
    def complete_replay(
        self,
        id: int,
        success: bool,
        resolution_type: str = "",
        note: str = "",
        resolved_by_id: Optional[int] = None,
        error_details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Complete a replay operation by updating the final status."""
        if success:
            return self.mark_resolved(
                entry_id=id,
                resolution_type=resolution_type or "auto_replay",
                resolution_note=note,
                resolved_by_id=resolved_by_id,
            )
        else:
            # Revert to PENDING or escalate to REQUIRES_REVIEW
            entry = self.get(id)
            if entry and entry.retry_count >= entry.max_retries:
                return self.update(
                    entry_id=id,
                    status=FailedOperationStatus.REQUIRES_REVIEW.value,
                    resolution_note=note,
                    metadata=error_details,
                )
            else:
                return self.update(
                    entry_id=id,
                    status=FailedOperationStatus.PENDING.value,
                    resolution_note=note,
                    metadata=error_details,
                )
    
    def release_stale_replaying(
        self,
        older_than_minutes: int = 30,
    ) -> int:
        """Release DLQ entries stuck in REPLAYING state."""
        # Get replaying entries
        replaying = self.get_by_status("replaying", limit=1000)
        
        released = 0
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        
        for entry in replaying:
            if entry.updated_at and entry.updated_at < cutoff:
                self.update(
                    entry_id=entry.id,
                    status=FailedOperationStatus.PENDING.value,
                    metadata={"released_from_stale": True},
                )
                released += 1
        
        return released
    
    # =========================================================================
    # Cleanup Operations
    # =========================================================================
    
    def archive_old_resolved(
        self,
        older_than_days: int = 30,
    ) -> int:
        """Archive resolved entries older than N days."""
        resolved = self.get_by_status(FailedOperationStatus.RESOLVED.value, limit=10000)
        
        archived = 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        
        for entry in resolved:
            if entry.resolved_at and entry.resolved_at < cutoff:
                self.update(
                    entry_id=entry.id,
                    status=FailedOperationStatus.ARCHIVED.value,
                )
                archived += 1
        
        return archived
    
    def purge_archived(
        self,
        ids: Optional[List[int]] = None,
        older_than_days: Optional[int] = None,
    ) -> int:
        """Permanently delete archived entries."""
        if ids and older_than_days:
            raise ValueError("Specify either ids or older_than_days, not both")
        
        purged = 0
        
        if ids:
            for id in ids:
                entry = self.get(id)
                if entry and entry.status == FailedOperationStatus.ARCHIVED.value:
                    self.delete(id)
                    purged += 1
        elif older_than_days:
            archived = self.get_by_status(FailedOperationStatus.ARCHIVED.value, limit=10000)
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            
            for entry in archived:
                if entry.resolved_at and entry.resolved_at < cutoff:
                    self.delete(entry.id)
                    purged += 1
        
        return purged
    
    def get_cleanup_stats(self) -> Dict[str, Any]:
        """Get statistics for cleanup operations."""
        stats = self.get_statistics()
        
        # Calculate age-based stats
        resolved = self.get_by_status(FailedOperationStatus.RESOLVED.value, limit=10000)
        archived = self.get_by_status(FailedOperationStatus.ARCHIVED.value, limit=10000)
        
        now = datetime.now(timezone.utc)
        resolved_30_days = sum(
            1 for e in resolved
            if e.resolved_at and (now - e.resolved_at).days > 30
        )
        archived_90_days = sum(
            1 for e in archived
            if e.resolved_at and (now - e.resolved_at).days > 90
        )
        
        return {
            "total": stats["total"],
            "by_status": {
                "pending": stats["pending"],
                "resolved": stats["resolved"],
                "requires_review": stats["requires_review"],
                "rejected": stats["rejected"],
                "archived": stats["archived"],
            },
            "resolved_older_than_30_days": resolved_30_days,
            "archived_older_than_90_days": archived_90_days,
        }


# Singleton
_redis_dlq_repo: Optional[RedisDLQRepository] = None


def get_redis_dlq_repo(
    backend: Optional["ResilientStorageBackend"] = None,
) -> RedisDLQRepository:
    """
    Get singleton Redis DLQ Repository.
    
    Args:
        backend: ResilientStorageBackend (uses default if not provided)
        
    Returns:
        RedisDLQRepository instance
    """
    global _redis_dlq_repo
    
    if _redis_dlq_repo is None:
        if backend is None:
            from selfhealing.adapters.resilient.backend import get_storage_backend
            backend = get_storage_backend()
        _redis_dlq_repo = RedisDLQRepository(backend)
    
    return _redis_dlq_repo
