"""
Dead Letter Queue (DLQ) Service

Provides centralized DLQ operations for the self-healing layer.
Handles storage, retrieval, and management of failed operations.

Features:
- Store failed operations with full forensic context
- Query and filter DLQ entries
- Manage DLQ lifecycle (pending → reviewing → resolved/rejected)
- Batch replay operations
- Cleanup, archive, and purge management
- Statistics and monitoring

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from selfhealing.core.timezone import now
from selfhealing.core.config import get_config

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        FailedOperationRepository,
        FailedOperationData,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class DLQConfig:
    """Configuration for DLQ operations."""

    enabled: bool = True
    retention_days: int = 30
    max_replay_attempts: int = 2

    @classmethod
    def from_settings(cls) -> "DLQConfig":
        """Load configuration from core config."""
        dlq_settings = get_config().dlq
        return cls(
            enabled=dlq_settings.enabled,
            retention_days=dlq_settings.retention_days,
            max_replay_attempts=dlq_settings.max_replay_attempts,
        )


# =============================================================================
# DLQ Entry Result
# =============================================================================


@dataclass
class DLQEntryResult:
    """Result of a DLQ operation."""

    success: bool
    dlq_id: int | None = None
    error: str | None = None

    @classmethod
    def created(cls, dlq_id: int) -> "DLQEntryResult":
        """Factory for successful creation."""
        return cls(success=True, dlq_id=dlq_id)

    @classmethod
    def failed(cls, error: str) -> "DLQEntryResult":
        """Factory for failed operation."""
        return cls(success=False, error=error)


@dataclass
class ReplayResult:
    """Result of a batch replay operation."""

    processed: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class CleanupStats:
    """Statistics for DLQ cleanup operations."""

    total: int = 0
    by_status: Dict[str, int] = field(default_factory=dict)
    resolved_older_than_30_days: int = 0
    archived_older_than_90_days: int = 0

    @property
    def can_archive(self) -> int:
        """Number of entries that can be archived."""
        return self.resolved_older_than_30_days

    @property
    def can_purge(self) -> int:
        """Number of entries that can be purged."""
        return self.archived_older_than_90_days


@dataclass
class PaginatedResult:
    """Paginated result for list operations."""

    results: List[Dict[str, Any]] = field(default_factory=list)
    page: int = 1
    page_size: int = 20
    total_pages: int = 0
    total_count: int = 0
    has_next: bool = False
    has_previous: bool = False


@dataclass
class RetryResult:
    """Result of a single entry retry operation."""

    success: bool
    id: int
    retry_count: int
    previous_retry_count: int
    message: str = ""
    error: str | None = None


@dataclass
class ResolveResult:
    """Result of a manual resolve operation."""

    success: bool
    id: int
    previous_status: str
    current_status: str
    resolved_at: str
    notes: str = ""
    error: str | None = None


# =============================================================================
# DLQ Service
# =============================================================================


class DLQService:
    """
    Dead Letter Queue Service.

    Provides centralized operations for managing failed operations.

    Usage:
        service = DLQService()
        result = service.store_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            order=order,
            error_message="Connection timed out",
        )
        if result.success:
            print(f"Stored as DLQ entry {result.dlq_id}")

    For testing with mock repository:
        mock_repo = Mock(spec=FailedOperationRepository)
        service = DLQService(repository=mock_repo)
    """

    def __init__(
        self,
        config: DLQConfig | None = None,
        repository: "FailedOperationRepository | None" = None,
    ):
        """
        Initialize the DLQ service.

        Args:
            config: Optional configuration, loads from settings if None
            repository: Optional repository for DI, uses Django adapter if None
        """
        self.config = config or DLQConfig.from_settings()
        self._repository = repository

    @property
    def repository(self) -> "FailedOperationRepository":
        """Get the repository, creating Django adapter if needed."""
        if self._repository is None:
            # Try to use ProviderRegistry from selfhealing package first
            try:
                from selfhealing.factory import ProviderRegistry

                self._repository = ProviderRegistry.get_failed_operation_repo()
            except (ImportError, ValueError):
                # Fallback to local Django adapter
                from .adapters.django_repositories import DjangoFailedOperationRepository

                self._repository = DjangoFailedOperationRepository()
        return self._repository

    @property
    def is_enabled(self) -> bool:
        """Check if DLQ is enabled."""
        return self.config.enabled

    # =========================================================================
    # Store Operations
    # =========================================================================

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
    ) -> DLQEntryResult:
        """
        Store a failed operation in the DLQ.

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

        Returns:
            DLQEntryResult with creation status
        """
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
    ) -> DLQEntryResult:
        """
        Store a failed operation with full forensic context.

        This is the preferred method when forensic context is available.

        Args:
            domain: Business domain
            failure_type: Specific failure type
            forensic_context: ForensicContext instance with full debug info
            entity_type: Type of related entity (e.g., "order", "payment")
            entity_id: ID of related entity
            user_id: Related User ID
            error_code: Error code
            error_message: Human-readable error message
            next_action_hint: Guidance for operators
            recommended_action: Suggested action

        Returns:
            DLQEntryResult with creation status
        """
        from .forensic_context import ForensicContext

        if not isinstance(forensic_context, ForensicContext):
            raise TypeError("forensic_context must be a ForensicContext instance")

        # Build snapshot data from forensic context
        snapshot_data = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "user_id": user_id,
        }

        return self.store_failure(
            domain=domain,
            failure_type=failure_type,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            error_code=error_code,
            error_message=error_message,
            snapshot_data=snapshot_data,
            request_data=forensic_context.extra.get("request_data", {}),
            response_data={
                "external_response_code": forensic_context.external_response_code,
                "external_response_body": forensic_context.external_response_body,
            },
            metadata=forensic_context.to_metadata(),
            next_action_hint=next_action_hint,
            recommended_action=recommended_action,
        )

    # =========================================================================
    # Query Operations
    # =========================================================================

    def get_pending_entries(
        self,
        domain: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> List["FailedOperationData"]:
        """
        Get pending DLQ entries.

        Args:
            domain: Filter by domain (optional)
            failure_type: Filter by failure type (optional)
            limit: Maximum number of entries to return

        Returns:
            List of pending FailedOperationData entries
        """
        return self.repository.find_by_status(
            status="pending",
            domain=domain,
            failure_type=failure_type,
            limit=limit,
        )

    def get_replayable_entries(
        self,
        domain: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> List["FailedOperationData"]:
        """
        Get entries that can be replayed.

        Entries are replayable if:
        - Status is PENDING
        - retry_count < max_retries

        Args:
            domain: Filter by domain (optional)
            failure_type: Filter by failure type (optional)
            limit: Maximum number of entries to return

        Returns:
            List of replayable FailedOperationData entries
        """
        return self.repository.find_replayable(
            max_retries=self.config.max_replay_attempts,
            domain=domain,
            failure_type=failure_type,
            limit=limit,
        )

    def get_sla_breached_entries(self) -> List["FailedOperationData"]:
        """
        Get entries that have breached their SLA.

        SLA thresholds are loaded from configuration.
        See config.SLAThresholds for default values.

        Returns:
            List of SLA-breached FailedOperationData entries
        """
        from selfhealing.core.config import get_config

        current_time = now()
        sla_config = get_config().sla

        return self.repository.find_sla_breached(
            current_time=current_time,
            sla_thresholds={
                "payment": sla_config.get_threshold("payment"),
                "point": sla_config.get_threshold("point"),
                "inventory": sla_config.get_threshold("inventory"),
                "webhook": sla_config.get_threshold("webhook"),
                "notification": sla_config.get_threshold("notification"),
            },
        )

    def get_expired_entries(self) -> List["FailedOperationData"]:
        """
        Get entries that have passed their retention period.

        Returns:
            List of expired FailedOperationData entries
        """
        current_time = now()
        return self.repository.find_expired(current_time=current_time)

    def get_entry_by_id(self, dlq_id: int) -> Optional["FailedOperationData"]:
        """
        Get a single DLQ entry by ID.

        Args:
            dlq_id: The DLQ entry ID

        Returns:
            FailedOperationData or None
        """
        return self.repository.get_by_id(dlq_id)

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict[str, Any]:
        """
        Get DLQ statistics.

        Returns:
            Dictionary with DLQ statistics
        """
        return self.repository.get_statistics()

    # =========================================================================
    # API Business Logic - Replay Operations
    # =========================================================================

    def replay(
        self,
        domain: Optional[str] = None,
        batch_size: int = 50,
    ) -> ReplayResult:
        """
        Execute batch replay of pending DLQ entries.

        Args:
            domain: Filter by domain (optional)
            batch_size: Maximum number of entries to process (default 50)

        Returns:
            ReplayResult with operation statistics
        """
        result = ReplayResult()

        try:
            entries = self.get_pending_entries(domain=domain, limit=batch_size)
            result.processed = len(entries)

            for entry in entries:
                try:
                    # TODO: Implement actual replay logic
                    # For now, just log the replay attempt
                    logger.info(
                        f"[DLQService] Would replay entry {entry.id}: "
                        f"{entry.domain}/{entry.failure_type}"
                    )
                    # In real implementation:
                    # replay_result = self._execute_replay(entry)
                    # if replay_result.success:
                    #     self.resolve_entry(entry.id, "auto_replay")
                    #     result.success += 1
                    # else:
                    #     result.failed += 1
                    #     result.errors.append(f"Entry {entry.id}: {replay_result.error}")
                except Exception as e:
                    result.failed += 1
                    result.errors.append(f"Entry {entry.id}: {str(e)}")

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
    # API Business Logic - Cleanup Operations
    # =========================================================================

    def get_cleanup_stats(self) -> CleanupStats:
        """
        Get statistics for cleanup operations.

        Returns:
            CleanupStats with counts by status and age
        """
        try:
            from selfhealing.adapters.django.models import FailedOperation
            from django.db.models import Count
            from django.utils import timezone

            current_time = timezone.now()
            day_30_ago = current_time - timedelta(days=30)
            day_90_ago = current_time - timedelta(days=90)

            # Count by status
            status_counts = dict(
                FailedOperation.objects.values("status")
                .annotate(count=Count("id"))
                .values_list("status", "count")
            )

            # Count resolved older than 30 days
            resolved_older_than_30_days = FailedOperation.objects.filter(
                status=FailedOperation.Status.RESOLVED,
                resolved_at__lt=day_30_ago,
            ).count()

            # Count archived older than 90 days
            archived_older_than_90_days = FailedOperation.objects.filter(
                status=FailedOperation.Status.ARCHIVED,
                updated_at__lt=day_90_ago,
            ).count()

            return CleanupStats(
                total=FailedOperation.objects.count(),
                by_status=status_counts,
                resolved_older_than_30_days=resolved_older_than_30_days,
                archived_older_than_90_days=archived_older_than_90_days,
            )

        except Exception as e:
            logger.error(f"[DLQService] Failed to get cleanup stats: {e}")
            return CleanupStats()

    def archive_old_entries(self, older_than_days: int = 30) -> int:
        """
        Archive resolved entries older than specified days.

        Args:
            older_than_days: Number of days (default 30)

        Returns:
            Number of entries archived

        Raises:
            ValueError: If older_than_days is less than 1
        """
        if older_than_days < 1:
            raise ValueError("older_than_days must be at least 1")

        try:
            from selfhealing.adapters.django.models import FailedOperation
            from django.utils import timezone

            cutoff = timezone.now() - timedelta(days=older_than_days)

            count = FailedOperation.objects.filter(
                status=FailedOperation.Status.RESOLVED,
                resolved_at__lt=cutoff,
            ).update(
                status=FailedOperation.Status.ARCHIVED,
                updated_at=timezone.now(),
            )

            logger.info(
                f"[DLQService] Archived {count} entries "
                f"(resolved > {older_than_days} days ago)"
            )

            return count

        except Exception as e:
            logger.error(f"[DLQService] Archive failed: {e}")
            raise

    def purge_archived(
        self,
        ids: Optional[List[int]] = None,
        older_than_days: Optional[int] = None,
    ) -> int:
        """
        Permanently delete archived entries.

        Args:
            ids: Specific entry IDs to purge (optional)
            older_than_days: Purge archived entries older than N days (optional)
            If neither specified, purges ALL archived entries.

        Returns:
            Number of entries purged

        Raises:
            ValueError: If both ids and older_than_days are specified,
                       or if older_than_days < 1,
                       or if specified ids contain non-archived entries
        """
        if ids is not None and older_than_days is not None:
            raise ValueError("Specify either ids or older_than_days, not both")

        try:
            from selfhealing.adapters.django.models import FailedOperation
            from django.utils import timezone

            archived_status = FailedOperation.Status.ARCHIVED

            if ids is not None:
                # Verify all are archived
                non_archived = (
                    FailedOperation.objects.filter(id__in=ids)
                    .exclude(status=archived_status)
                    .values_list("id", "status")
                )

                if non_archived.exists():
                    first_bad = list(non_archived)[0]
                    raise ValueError(
                        f"Entry {first_bad[0]} is not archived (status: {first_bad[1]}). "
                        "Only archived entries can be purged."
                    )

                result = FailedOperation.objects.filter(
                    id__in=ids,
                    status=archived_status,
                ).delete()
                count = result[0] if result else 0

            elif older_than_days is not None:
                if older_than_days < 1:
                    raise ValueError("older_than_days must be at least 1")

                cutoff = timezone.now() - timedelta(days=older_than_days)
                result = FailedOperation.objects.filter(
                    status=archived_status,
                    updated_at__lt=cutoff,
                ).delete()
                count = result[0] if result else 0

            else:
                # Purge all archived
                result = FailedOperation.objects.filter(
                    status=archived_status,
                ).delete()
                count = result[0] if result else 0

            logger.warning(f"[DLQService] PURGED {count} archived entries")

            return count

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"[DLQService] Purge failed: {e}")
            raise

    # =========================================================================
    # API Business Logic - List Operations
    # =========================================================================

    def list_entries(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult:
        """
        Get paginated list of DLQ entries.

        Args:
            filters: Dictionary with filter conditions
                - status: Filter by status
                - domain: Filter by domain
            page: Page number (default 1)
            page_size: Items per page (default 20, max 100)

        Returns:
            PaginatedResult with entries and pagination info
        """
        filters = filters or {}
        page_size = min(page_size, 100)

        try:
            from selfhealing.adapters.django.models import FailedOperation
            from django.core.paginator import Paginator

            queryset = FailedOperation.objects.all().order_by("-created_at")

            # Apply filters
            if filters.get("status"):
                queryset = queryset.filter(status=filters["status"])
            if filters.get("domain"):
                queryset = queryset.filter(domain=filters["domain"])

            paginator = Paginator(queryset, page_size)
            page_obj = paginator.get_page(page)

            entries = []
            for entry in page_obj:
                entries.append({
                    "id": entry.id,
                    "domain": entry.domain,
                    "failure_type": entry.failure_type,
                    "status": entry.status,
                    "retry_count": entry.retry_count,
                    "created_at": entry.created_at.isoformat() if entry.created_at else None,
                    "resolved_at": entry.resolved_at.isoformat() if entry.resolved_at else None,
                })

            return PaginatedResult(
                results=entries,
                page=page,
                page_size=page_size,
                total_pages=paginator.num_pages,
                total_count=paginator.count,
                has_next=page_obj.has_next(),
                has_previous=page_obj.has_previous(),
            )

        except Exception as e:
            logger.error(f"[DLQService] List failed: {e}")
            return PaginatedResult()

    def get_entry(self, pk: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed info for a single DLQ entry.

        Args:
            pk: Entry primary key

        Returns:
            Dictionary with entry details or None if not found
        """
        try:
            from selfhealing.adapters.django.models import FailedOperation

            entry = FailedOperation.objects.get(pk=pk)

            return {
                "id": entry.id,
                "domain": entry.domain,
                "failure_type": entry.failure_type,
                "status": entry.status,
                "retry_count": entry.retry_count,
                "max_retries": entry.max_retries,
                "context": entry.context,
                "error_message": entry.error_message,
                "stack_trace": entry.stack_trace,
                "resolution_notes": entry.resolution_notes,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
                "resolved_at": entry.resolved_at.isoformat() if entry.resolved_at else None,
            }

        except FailedOperation.DoesNotExist:
            return None
        except Exception as e:
            logger.error(f"[DLQService] Get entry failed for {pk}: {e}")
            raise

    # =========================================================================
    # API Business Logic - Retry/Resolve Operations
    # =========================================================================

    def retry_entry(self, pk: int) -> RetryResult:
        """
        Retry a single DLQ entry.

        Args:
            pk: Entry primary key

        Returns:
            RetryResult with operation details

        Raises:
            ValueError: If entry is already resolved or archived
        """
        try:
            from selfhealing.adapters.django.models import FailedOperation
            from django.utils import timezone

            entry = FailedOperation.objects.get(pk=pk)

            if entry.status == FailedOperation.Status.RESOLVED:
                raise ValueError("Cannot retry an already resolved entry")

            if entry.status == FailedOperation.Status.ARCHIVED:
                raise ValueError("Cannot retry an archived entry")

            old_count = entry.retry_count
            entry.retry_count += 1
            entry.updated_at = timezone.now()
            entry.save()

            logger.info(
                f"[DLQService] Retry triggered for entry {pk} "
                f"({entry.domain}/{entry.failure_type})"
            )

            return RetryResult(
                success=True,
                id=entry.id,
                retry_count=entry.retry_count,
                previous_retry_count=old_count,
                message=f"Retry triggered for entry {pk}",
            )

        except FailedOperation.DoesNotExist:
            raise ValueError(f"DLQ entry {pk} not found")
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"[DLQService] Retry failed for {pk}: {e}")
            raise

    def resolve_entry(self, pk: int, notes: str = "") -> ResolveResult:
        """
        Manually resolve a DLQ entry.

        Args:
            pk: Entry primary key
            notes: Resolution notes (optional)

        Returns:
            ResolveResult with operation details

        Raises:
            ValueError: If entry is already resolved or archived, or not found
        """
        try:
            from selfhealing.adapters.django.models import FailedOperation
            from django.utils import timezone

            entry = FailedOperation.objects.get(pk=pk)

            if entry.status == FailedOperation.Status.RESOLVED:
                raise ValueError("Entry is already resolved")

            if entry.status == FailedOperation.Status.ARCHIVED:
                raise ValueError("Cannot resolve an archived entry")

            old_status = entry.status
            entry.status = FailedOperation.Status.RESOLVED
            entry.resolved_at = timezone.now()
            entry.updated_at = timezone.now()
            entry.resolution_notes = notes
            entry.save()

            logger.info(f"[DLQService] Entry {pk} manually resolved: {notes}")

            return ResolveResult(
                success=True,
                id=entry.id,
                previous_status=old_status,
                current_status=entry.status,
                resolved_at=entry.resolved_at.isoformat(),
                notes=notes,
            )

        except FailedOperation.DoesNotExist:
            raise ValueError(f"DLQ entry {pk} not found")
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"[DLQService] Resolve failed for {pk}: {e}")
            raise

    # =========================================================================
    # API Business Logic - Test Operations
    # =========================================================================

    def create_test_entry(
        self,
        domain: str,
        failure_type: str,
        user_id: Optional[int] = None,
        order_id: Optional[str] = None,
        payment_id: Optional[str] = None,
        error_message: str = "Test failure for load testing",
        snapshot_data: Optional[Dict[str, Any]] = None,
        request_data: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        entity_type: str = "test",
        entity_id: str = "",
        created_by: str = "",
    ) -> Dict[str, Any]:
        """
        Create a test DLQ entry for load testing and verification.

        Only available in non-production environments (DEBUG=True or TESTING=True).

        Args:
            domain: Business domain (e.g., "payment", "point")
            failure_type: Failure type (e.g., "PG_TIMEOUT")
            user_id: User ID (optional)
            order_id: Order ID (optional)
            payment_id: Payment ID (optional)
            error_message: Error message (default: "Test failure for load testing")
            snapshot_data: Snapshot data (optional)
            request_data: Request data (optional)
            response_data: Response data (optional)
            metadata: Additional metadata (optional)
            entity_type: Entity type (default: "test")
            entity_id: Entity ID (optional)
            created_by: Creator identifier (optional)

        Returns:
            Dictionary with created entry details

        Raises:
            PermissionError: If not in DEBUG/TEST mode
            ValueError: If domain or failure_type is missing
        """
        from django.conf import settings

        # Only allow in non-production environments
        if not getattr(settings, "DEBUG", False) and not getattr(settings, "TESTING", False):
            raise PermissionError("DLQ test entries can only be created in DEBUG/TEST mode")

        if not domain or not failure_type:
            raise ValueError("domain and failure_type are required")

        try:
            from selfhealing.adapters.django.models import FailedOperation

            entry = FailedOperation.objects.create(
                domain=domain,
                failure_type=failure_type,
                order_id=order_id,
                payment_id=payment_id,
                user_id=user_id,
                error_code="TEST_ERROR",
                error_message=error_message,
                snapshot_data=snapshot_data or {},
                request_data=request_data or {},
                response_data=response_data or {},
                metadata={
                    "test": True,
                    "created_by": created_by,
                    "source": "DLQService.create_test_entry",
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    **(metadata or {}),
                },
                recommended_action=FailedOperation.RecommendedAction.REPLAY,
                status=FailedOperation.Status.PENDING,
            )

            logger.info(
                f"[DLQService] Test entry created: id={entry.id}, "
                f"domain={domain}, failure_type={failure_type}"
            )

            return {
                "status": "created",
                "dlq_id": entry.id,
                "domain": domain,
                "failure_type": failure_type,
            }

        except Exception as e:
            logger.error(f"[DLQService] Test entry creation failed: {e}")
            raise


# =============================================================================
# Module-level convenience functions
# =============================================================================


_dlq_service: DLQService | None = None


def get_dlq_service() -> DLQService:
    """Get the singleton DLQ service instance."""
    global _dlq_service
    if _dlq_service is None:
        _dlq_service = DLQService()
    return _dlq_service


def store_to_dlq(
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
) -> DLQEntryResult:
    """
    Convenience function to store a failure in the DLQ.

    This is a shortcut for get_dlq_service().store_failure(...).
    """
    return get_dlq_service().store_failure(
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
