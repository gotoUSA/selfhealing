"""
SQLAlchemy FailedOperation Repository Implementation.

Manages DLQ (Dead Letter Queue) entries using SQLAlchemy ORM.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from selfhealing.interfaces.repositories import (
    FailedOperationRepository,
    FailedOperationData,
    FailedOperationStatus,
)
from selfhealing.adapters.sqlalchemy.models import FailedOperationModel
from selfhealing.adapters.sqlalchemy.base import BaseRepository, _now


class SQLAlchemyFailedOperationRepository(BaseRepository, FailedOperationRepository):
    """
    SQLAlchemy implementation of FailedOperationRepository.

    Manages DLQ (Dead Letter Queue) entries using SQLAlchemy ORM.
    """

    def _model_to_data(self, model: FailedOperationModel) -> FailedOperationData:
        """Convert SQLAlchemy model to data class."""
        return FailedOperationData(
            id=model.id,
            domain=model.domain,
            failure_type=model.failure_type,
            status=model.status,
            entity_type=model.entity_type,
            entity_id=model.entity_id,
            entity_refs={},  # Not used in SQLAlchemy adapter
            user_id=model.user_id,
            snapshot_data=model.snapshot_data or {},
            error_code=model.error_code or "",
            error_message=model.error_message or "",
            retry_count=model.retry_count,
            max_retries=model.max_retries,
            last_retry_at=model.last_retry_at,
            request_data=model.request_data or {},
            response_data=model.response_data or {},
            metadata=model.extra_metadata or {},
            resolved_at=model.resolved_at,
            resolved_by_id=model.resolved_by_id,
            resolution_type=model.resolution_type or "",
            resolution_note=model.resolution_note or "",
            next_action_hint=model.next_action_hint or "",
            recommended_action=model.recommended_action or "",
            created_at=model.created_at,
            updated_at=model.updated_at,
            expires_at=model.expires_at,
        )

    def create(
        self,
        domain: str,
        failure_type: str,
        error_message: str = "",
        error_code: str = "",
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        user_id: Optional[int] = None,
        snapshot_data: Optional[dict[str, Any]] = None,
        request_data: Optional[dict[str, Any]] = None,
        response_data: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        retry_count: int = 0,
        max_retries: int = 2,
        next_action_hint: str = "",
        recommended_action: str = "",
    ) -> FailedOperationData:
        """Create a new failed operation record."""
        session = self._get_session()
        try:
            model = FailedOperationModel(
                domain=domain,
                failure_type=failure_type,
                status=FailedOperationStatus.PENDING.value,
                entity_type=entity_type,
                entity_id=entity_id,
                user_id=user_id,
                snapshot_data=snapshot_data or {},
                error_code=error_code,
                error_message=error_message,
                retry_count=retry_count,
                max_retries=max_retries,
                request_data=request_data or {},
                response_data=response_data or {},
                extra_metadata=metadata or {},
                next_action_hint=next_action_hint,
                recommended_action=recommended_action,
                created_at=_now(),
                updated_at=_now(),
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._model_to_data(model)
        finally:
            session.close()

    def get_by_id(self, id: int) -> Optional[FailedOperationData]:
        """Get a failed operation by ID."""
        session = self._get_session()
        try:
            model = session.query(FailedOperationModel).filter_by(id=id).first()
            if model is None:
                return None
            return self._model_to_data(model)
        finally:
            session.close()

    def get_pending_by_domain(
        self,
        domain: str,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get pending operations for a specific domain."""
        session = self._get_session()
        try:
            models = (
                session.query(FailedOperationModel)
                .filter_by(domain=domain, status=FailedOperationStatus.PENDING.value)
                .order_by(FailedOperationModel.created_at)
                .limit(limit)
                .all()
            )
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def get_pending_count_by_domain(self, domain: str) -> int:
        """Get count of pending operations for a domain."""
        session = self._get_session()
        try:
            count = (
                session.query(FailedOperationModel)
                .filter_by(domain=domain, status=FailedOperationStatus.PENDING.value)
                .count()
            )
            return count
        finally:
            session.close()

    def update_status(
        self,
        id: int,
        status: str,
        resolution_type: str = "",
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Update the status of a failed operation."""
        session = self._get_session()
        try:
            model = session.query(FailedOperationModel).filter_by(id=id).first()
            if model is None:
                return False

            model.status = status
            model.updated_at = _now()

            if resolution_type:
                model.resolution_type = resolution_type
            if resolution_note:
                model.resolution_note = resolution_note
            if resolved_by_id is not None:
                model.resolved_by_id = resolved_by_id
            if status == FailedOperationStatus.RESOLVED.value:
                model.resolved_at = _now()

            session.commit()
            return True
        finally:
            session.close()

    def increment_retry_count(self, id: int) -> bool:
        """Increment retry count and update last_retry_at."""
        session = self._get_session()
        try:
            model = session.query(FailedOperationModel).filter_by(id=id).first()
            if model is None:
                return False

            model.retry_count += 1
            model.last_retry_at = _now()
            model.updated_at = _now()
            session.commit()
            return True
        finally:
            session.close()

    def mark_as_resolved(
        self,
        id: int,
        resolution_type: str,
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
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
        session = self._get_session()
        try:
            models = (
                session.query(FailedOperationModel)
                .filter(FailedOperationModel.expires_at < before_date)
                .filter(FailedOperationModel.status != FailedOperationStatus.RESOLVED.value)
                .limit(limit)
                .all()
            )
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def bulk_update_status(
        self,
        ids: list[int],
        status: str,
    ) -> int:
        """Bulk update status for multiple operations."""
        if not ids:
            return 0

        session = self._get_session()
        try:
            count = (
                session.query(FailedOperationModel)
                .filter(FailedOperationModel.id.in_(ids))
                .update(
                    {
                        FailedOperationModel.status: status,
                        FailedOperationModel.updated_at: _now(),
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            return count
        finally:
            session.close()

    def find_by_status(
        self,
        status: str,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations by status with optional filters."""
        session = self._get_session()
        try:
            query = session.query(FailedOperationModel).filter_by(status=status)

            if domain:
                query = query.filter_by(domain=domain)
            if failure_type:
                query = query.filter_by(failure_type=failure_type)

            models = query.order_by(FailedOperationModel.created_at).limit(limit).all()
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def find_replayable(
        self,
        max_retries: int,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations that can be replayed."""
        session = self._get_session()
        try:
            query = (
                session.query(FailedOperationModel)
                .filter_by(status=FailedOperationStatus.PENDING.value)
                .filter(FailedOperationModel.retry_count < max_retries)
            )

            if domain:
                query = query.filter_by(domain=domain)
            if failure_type:
                query = query.filter_by(failure_type=failure_type)

            models = query.order_by(FailedOperationModel.created_at).limit(limit).all()
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def find_sla_breached(
        self,
        current_time: datetime,
        sla_thresholds: dict[str, timedelta],
    ) -> list[FailedOperationData]:
        """Find operations that have breached their SLA."""
        session = self._get_session()
        try:
            results = []
            for domain, threshold in sla_thresholds.items():
                cutoff_time = current_time - threshold
                models = (
                    session.query(FailedOperationModel)
                    .filter_by(domain=domain, status=FailedOperationStatus.PENDING.value)
                    .filter(FailedOperationModel.created_at < cutoff_time)
                    .all()
                )
                results.extend([self._model_to_data(m) for m in models])
            return results
        finally:
            session.close()

    def find_expired(
        self,
        current_time: datetime,
    ) -> list[FailedOperationData]:
        """Find operations past their retention period."""
        session = self._get_session()
        try:
            models = (
                session.query(FailedOperationModel)
                .filter(FailedOperationModel.expires_at.isnot(None))
                .filter(FailedOperationModel.expires_at < current_time)
                .all()
            )
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about failed operations."""
        session = self._get_session()
        try:
            total = session.query(FailedOperationModel).count()
            pending = session.query(FailedOperationModel).filter_by(status=FailedOperationStatus.PENDING.value).count()
            resolved = session.query(FailedOperationModel).filter_by(status=FailedOperationStatus.RESOLVED.value).count()

            return {
                "total": total,
                "pending": pending,
                "resolved": resolved,
                "pending_rate": pending / total if total > 0 else 0,
            }
        finally:
            session.close()

    def try_acquire_for_replay(
        self,
        id: int,
        max_retries: int,
    ) -> Optional[FailedOperationData]:
        """
        Atomically acquire a DLQ entry for replay.

        Uses SELECT FOR UPDATE to prevent race conditions.
        """
        session = self._get_session()
        try:
            # Use with_for_update() for row-level locking
            model = session.query(FailedOperationModel).filter_by(id=id).with_for_update().first()

            if model is None:
                return None

            # Check eligibility
            if model.status != FailedOperationStatus.PENDING.value or model.retry_count >= max_retries:
                return None

            # Update atomically
            model.status = "replaying"
            model.retry_count += 1
            model.last_retry_at = _now()
            model.updated_at = _now()
            session.commit()
            session.refresh(model)

            return self._model_to_data(model)
        except Exception:
            session.rollback()
            return None
        finally:
            session.close()

    def complete_replay(
        self,
        id: int,
        success: bool,
        resolution_type: str = "",
        note: str = "",
        resolved_by_id: Optional[int] = None,
        error_details: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Complete a replay operation by updating the final status."""
        session = self._get_session()
        try:
            model = session.query(FailedOperationModel).filter_by(id=id).first()
            if model is None:
                return False

            if success:
                model.status = FailedOperationStatus.RESOLVED.value
                model.resolution_type = resolution_type
                model.resolution_note = note
                model.resolved_by_id = resolved_by_id
                model.resolved_at = _now()
            else:
                # Revert to pending or escalate
                if model.retry_count >= model.max_retries:
                    model.status = FailedOperationStatus.REQUIRES_REVIEW.value
                else:
                    model.status = FailedOperationStatus.PENDING.value

                if note:
                    model.error_message = note
                if error_details:
                    model.extra_metadata = {**(model.extra_metadata or {}), "last_error": error_details}

            model.updated_at = _now()
            session.commit()
            return True
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    def release_stale_replaying(
        self,
        older_than_minutes: int = 30,
    ) -> int:
        """Release DLQ entries stuck in REPLAYING state."""
        session = self._get_session()
        try:
            cutoff = _now() - timedelta(minutes=older_than_minutes)
            count = (
                session.query(FailedOperationModel)
                .filter_by(status="replaying")
                .filter(FailedOperationModel.updated_at < cutoff)
                .update(
                    {
                        FailedOperationModel.status: FailedOperationStatus.PENDING.value,
                        FailedOperationModel.updated_at: _now(),
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            return count
        finally:
            session.close()

    # =========================================================================
    # DLQ Cleanup Methods
    # =========================================================================

    def archive_old_resolved(
        self,
        older_than_days: int = 30,
    ) -> int:
        """Archive resolved entries older than specified days."""
        session = self._get_session()
        try:
            cutoff = _now() - timedelta(days=older_than_days)
            count = (
                session.query(FailedOperationModel)
                .filter_by(status=FailedOperationStatus.RESOLVED.value)
                .filter(FailedOperationModel.resolved_at < cutoff)
                .update(
                    {
                        FailedOperationModel.status: FailedOperationStatus.ARCHIVED.value,
                        FailedOperationModel.updated_at: _now(),
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            return count
        finally:
            session.close()

    def purge_archived(
        self,
        older_than_days: int = 90,
        ids: Optional[list[int]] = None,
    ) -> int:
        """Permanently delete archived entries."""
        session = self._get_session()
        try:
            if ids is not None:
                # Delete specific archived entries by ID
                count = (
                    session.query(FailedOperationModel)
                    .filter(
                        FailedOperationModel.id.in_(ids),
                        FailedOperationModel.status == FailedOperationStatus.ARCHIVED.value,
                    )
                    .delete(synchronize_session=False)
                )
            else:
                # Delete archived older than days
                cutoff = _now() - timedelta(days=older_than_days)
                count = (
                    session.query(FailedOperationModel)
                    .filter_by(status=FailedOperationStatus.ARCHIVED.value)
                    .filter(FailedOperationModel.updated_at < cutoff)
                    .delete(synchronize_session=False)
                )
            session.commit()
            return count
        finally:
            session.close()

    def get_cleanup_stats(self) -> dict[str, Any]:
        """Get cleanup-related statistics."""
        session = self._get_session()
        try:
            from sqlalchemy import func

            now = _now()
            day_30_ago = now - timedelta(days=30)
            day_90_ago = now - timedelta(days=90)

            # Count by status
            status_counts_raw = (
                session.query(
                    FailedOperationModel.status,
                    func.count(FailedOperationModel.id),
                )
                .group_by(FailedOperationModel.status)
                .all()
            )
            status_counts = {s: c for s, c in status_counts_raw}

            total = sum(status_counts.values())

            # Count resolved older than 30 days
            resolved_older_than_30_days = (
                session.query(FailedOperationModel)
                .filter_by(status=FailedOperationStatus.RESOLVED.value)
                .filter(FailedOperationModel.resolved_at < day_30_ago)
                .count()
            )

            # Count archived older than 90 days
            archived_older_than_90_days = (
                session.query(FailedOperationModel)
                .filter_by(status=FailedOperationStatus.ARCHIVED.value)
                .filter(FailedOperationModel.updated_at < day_90_ago)
                .count()
            )

            return {
                "total": total,
                "by_status": status_counts,
                "resolved_older_than_30_days": resolved_older_than_30_days,
                "archived_older_than_90_days": archived_older_than_90_days,
                "recommendations": {
                    "can_archive": resolved_older_than_30_days,
                    "can_purge": archived_older_than_90_days,
                },
            }
        finally:
            session.close()
