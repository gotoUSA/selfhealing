"""
DLQ Replay Service

Provides replay functionality for failed operations in the DLQ.
Supports manual replay, batch replay, and conditional replay on circuit breaker recovery.

Replay Types:
- Manual Replay: Operator selects individual items
- Batch Replay: Operator selects multiple items by filter
- Conditional Replay: Auto-replay when external system recovers

Thin Task, Fat Service Architecture:
    - 모든 거버넌스 체크는 이 서비스에서 수행
    - Celery Task는 단순 위임자 역할만 수행
    - Audit 로깅은 check_all_governance를 통해 자동 수행

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §2
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Callable

from selfhealing.core.timezone import now
from selfhealing.core.config import get_config
from selfhealing.services.governance_checks import (
    check_all_governance,
    GovernanceCheckResult,
)

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        FailedOperationRepository,
        FailedOperationData,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Replay Result
# =============================================================================


@dataclass
class ReplayResult:
    """Result of a replay operation."""

    success: bool
    dlq_id: int
    message: str = ""
    error: str | None = None
    data: dict[str, Any] | None = None

    @classmethod
    def succeeded(cls, dlq_id: int, message: str = "", data: dict | None = None) -> "ReplayResult":
        """Factory for successful replay."""
        return cls(success=True, dlq_id=dlq_id, message=message, data=data)

    @classmethod
    def failed(cls, dlq_id: int, error: str) -> "ReplayResult":
        """Factory for failed replay."""
        return cls(success=False, dlq_id=dlq_id, error=error)
    
    @classmethod
    def blocked(cls, dlq_id: int, governance_result: GovernanceCheckResult) -> "ReplayResult":
        """Factory for governance-blocked replay."""
        return cls(
            success=False,
            dlq_id=dlq_id,
            error=governance_result.block_message,
            data={
                "blocked": True,
                "block_reason": governance_result.block_reason.value if governance_result.block_reason else None,
            },
        )


@dataclass
class BatchReplayResult:
    """Result of a batch replay operation."""

    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    results: list[ReplayResult] | None = None
    governance_blocked: bool = False
    governance_block_reason: str = ""


# =============================================================================
# Replay Handlers (Domain-specific)
# =============================================================================


class ReplayHandler(ABC):
    """
    Abstract base class for domain-specific replay handlers.

    Each domain (payment, point, inventory, etc.) should implement
    its own replay logic by subclassing this.

    Note: Handlers should work with FailedOperationData (a simple dataclass)
    not Django models. The handler receives operation data and should
    use injected services for actual operations.
    """

    @property
    @abstractmethod
    def domain(self) -> str:
        """Return the domain this handler handles."""
        pass

    @abstractmethod
    def replay(self, failed_op: "FailedOperationData") -> ReplayResult:
        """
        Execute replay for a single failed operation.

        Args:
            failed_op: The FailedOperationData to replay

        Returns:
            ReplayResult indicating success or failure
        """
        pass

    @abstractmethod
    def can_replay(self, failed_op: "FailedOperationData") -> tuple[bool, str]:
        """
        Check if the operation can be replayed.

        Args:
            failed_op: The FailedOperationData to check

        Returns:
            Tuple of (can_replay: bool, reason: str)
        """
        pass


class DefaultReplayHandler(ReplayHandler):
    """
    Default replay handler that returns an error.

    This handler is used when no specific handler is registered for a domain.
    Users should register their own handlers for each domain they need.
    """

    def __init__(self, domain_name: str):
        self._domain = domain_name

    @property
    def domain(self) -> str:
        return self._domain

    def can_replay(self, failed_op: "FailedOperationData") -> tuple[bool, str]:
        return False, f"No replay handler registered for domain '{self._domain}'"

    def replay(self, failed_op: "FailedOperationData") -> ReplayResult:
        return ReplayResult.failed(
            failed_op.id,
            f"No replay handler registered for domain '{self._domain}'. "
            "Please register a handler using register_replay_handler().",
        )


# =============================================================================
# Domain-Specific Handlers (MOVED TO ADAPTER LAYER)
# =============================================================================
# NOTE: Domain-specific handlers have been moved to the adapter layer.
#
# For Django projects, create your own handlers:
#   from myapp.services.self_healing.replay_handlers import (
#       OrderReplayHandler,
#       NotificationReplayHandler,
#   )
#
# Or register your own handlers:
#   from selfhealing.services.replay_service import register_replay_handler, ReplayHandler
#
#   class MyDomainHandler(ReplayHandler):
#       @property
#       def domain(self) -> str:
#           return "my_domain"
#
#       def can_replay(self, failed_op) -> tuple[bool, str]:
#           # Your validation logic
#           return True, ""
#
#       def replay(self, failed_op) -> ReplayResult:
#           # Your replay logic
#           return ReplayResult.succeeded(failed_op.id, "Done")
#
#   register_replay_handler(MyDomainHandler())
# =============================================================================


# =============================================================================
# Replay Handler Registry
# =============================================================================


_replay_handlers: dict[str, ReplayHandler] = {}


def register_replay_handler(handler: ReplayHandler) -> None:
    """Register a replay handler for a domain."""
    _replay_handlers[handler.domain] = handler


def get_replay_handler(domain: str) -> ReplayHandler:
    """
    Get the replay handler for a domain.

    Args:
        domain: The domain name

    Returns:
        ReplayHandler instance for the domain
    """
    if domain in _replay_handlers:
        return _replay_handlers[domain]

    # Return default handler if no specific handler exists
    return DefaultReplayHandler(domain)


# NOTE: No default handlers registered in core package.
# Domain-specific handlers should be registered by the adapter layer.
# Example (in your adapter):
#   from selfhealing.services.replay_service import register_replay_handler
#   from myapp.services.replay_handlers import OrderReplayHandler
#   register_replay_handler(OrderReplayHandler())


# =============================================================================
# Replay Service
# =============================================================================


class ReplayService:
    """
    DLQ Replay Service.

    Orchestrates replay operations for failed operations.

    Usage:
        service = ReplayService()

        # Single replay
        result = service.replay_single(dlq_id=123)

        # Batch replay
        batch_result = service.replay_batch(
            failure_type="PG_TIMEOUT",
            max_items=50
        )

    For testing with mock repository:
        mock_repo = Mock(spec=FailedOperationRepository)
        service = ReplayService(repository=mock_repo)
    """

    def __init__(self, repository: "FailedOperationRepository | None" = None):
        """
        Initialize the replay service.

        Args:
            repository: Optional repository for DI, uses Django adapter if None
        """
        self.config = self._load_config()
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

    def _load_config(self) -> dict[str, Any]:
        """Load replay configuration from config system."""
        config = get_config()
        return {
            "max_replay_attempts": config.dlq.max_replay_attempts,
        }

    # =========================================================================
    # Single Replay
    # =========================================================================

    def replay_single(self, dlq_id: int) -> ReplayResult:
        """
        Replay a single DLQ entry.

        This method uses atomic acquisition to prevent race conditions when
        multiple workers try to replay the same entry simultaneously.

        Safety Checks (via check_all_governance):
        1. Kill Switch - 시스템 전역 비활성화 체크
        2. Emergency Level - LEVEL_2+ 시 자원 보호를 위해 차단
        3. ErrorBudgetGate - 에러 예산 고갈 시 자동화 차단

        Audit Logging:
        - 차단 발생 시 자동으로 AuditLog에 기록됨

        Args:
            dlq_id: ID of the FailedOperation to replay

        Returns:
            ReplayResult indicating success or failure
        """
        # 거버넌스 체크 (Kill Switch, Emergency Mode, Error Budget)
        # check_all_governance가 차단 시 자동으로 Audit 로깅 수행
        governance = check_all_governance(
            check_kill_switch=True,
            check_emergency=True,
            emergency_min_level=2,
            check_error_budget=True,
            operation_name="replay_single",
            service_name="ReplayService",
            domain="dlq",
            audit_on_block=True,
        )

        if not governance.allowed:
            logger.warning(
                f"[ReplayService] replay_single blocked: {governance.block_message}. "
                f"dlq_id={dlq_id}"
            )
            return ReplayResult.blocked(dlq_id, governance)

        # Atomically try to acquire the entry for replay
        # This prevents race conditions where two workers process the same entry
        config_max = self.config["max_replay_attempts"]

        failed_op_data = self.repository.try_acquire_for_replay(dlq_id, config_max)

        if failed_op_data is None:
            # Entry not found, not eligible, or already being processed
            # Check if it exists to provide appropriate error message
            existing = self.repository.get_by_id(dlq_id)
            if existing is None:
                return ReplayResult.failed(dlq_id, "DLQ entry not found")
            elif existing.status != "pending":
                return ReplayResult.failed(dlq_id, f"Cannot replay: status is '{existing.status}'")
            else:
                return ReplayResult.failed(dlq_id, "max_replays_exceeded")

        # Get appropriate handler and execute replay
        handler = get_replay_handler(failed_op_data.domain)

        try:
            result = handler.replay(failed_op_data)
        except Exception as e:
            # Handler raised an unexpected exception - escalate to REQUIRES_REVIEW
            logger.error(f"[ReplayService] Handler exception for DLQ {dlq_id}: {e}", exc_info=True)
            self.repository.complete_replay(
                id=dlq_id,
                success=False,
                note=f"Handler crash: {type(e).__name__}: {str(e)[:200]}",
                error_details={
                    "type": type(e).__name__,
                    "message": str(e)[:500],
                    "occurred_at": now().isoformat(),
                    "escalated_to": "requires_review",
                },
            )
            return ReplayResult.failed(dlq_id, f"internal_error: {type(e).__name__}")

        # Complete the replay operation with final status
        self.repository.complete_replay(
            id=dlq_id,
            success=result.success,
            resolution_type="auto_replay" if result.success else "",
            note=result.message if result.success else (result.error or "Replay failed"),
        )

        if result.success:
            logger.info(f"[ReplayService] DLQ entry {dlq_id} replayed successfully")
        else:
            logger.warning(f"[ReplayService] DLQ entry {dlq_id} replay failed: {result.error}")

        return result

    # =========================================================================
    # Batch Replay
    # =========================================================================

    def replay_batch(
        self,
        domain: str | None = None,
        failure_type: str | None = None,
        max_items: int = 100,
    ) -> BatchReplayResult:
        """
        Replay multiple DLQ entries matching criteria.

        Safety Checks (via check_all_governance):
        1. Kill Switch - 시스템 전역 비활성화 체크
        2. Emergency Level - LEVEL_2+ 시 자원 보호를 위해 차단
        3. ErrorBudgetGate - 에러 예산 고갈 시 자동화 차단

        Audit Logging:
        - 차단 발생 시 자동으로 AuditLog에 기록됨

        Args:
            domain: Filter by domain (optional)
            failure_type: Filter by failure type (optional)
            max_items: Maximum number of items to replay

        Returns:
            BatchReplayResult with summary and individual results
        """
        # 거버넌스 체크 (Kill Switch, Emergency Mode, Error Budget)
        # check_all_governance가 차단 시 자동으로 Audit 로깅 수행
        governance = check_all_governance(
            check_kill_switch=True,
            check_emergency=True,
            emergency_min_level=2,
            check_error_budget=True,
            operation_name="replay_batch",
            service_name="ReplayService",
            domain=domain or "dlq",
            audit_on_block=True,
        )

        if not governance.allowed:
            logger.warning(
                f"[ReplayService] replay_batch blocked: {governance.block_message}. "
                f"domain={domain}, failure_type={failure_type}"
            )
            return BatchReplayResult(
                total=0,
                success_count=0,
                failed_count=0,
                skipped_count=0,
                results=[],
                governance_blocked=True,
                governance_block_reason=governance.block_message,
            )

        max_replays = self.config["max_replay_attempts"]

        # Get eligible entries using repository
        entries = self.repository.get_pending_entries(
            domain=domain,
            failure_type=failure_type,
            max_retry_count=max_replays,
            limit=max_items,
        )

        batch_result = BatchReplayResult(
            total=len(entries),
            results=[],
        )

        for entry in entries:
            # 개별 replay는 거버넌스 체크 스킵 (배치에서 이미 체크함)
            result = self._replay_single_internal(entry.id)
            batch_result.results.append(result)

            if result.success:
                batch_result.success_count += 1
            else:
                batch_result.failed_count += 1

        logger.info(
            f"[ReplayService] Batch replay completed: "
            f"total={batch_result.total}, success={batch_result.success_count}, "
            f"failed={batch_result.failed_count}"
        )

        return batch_result

    def _replay_single_internal(self, dlq_id: int) -> ReplayResult:
        """
        Internal replay without governance check.
        
        Used by replay_batch to avoid redundant governance checks.
        """
        config_max = self.config["max_replay_attempts"]

        failed_op_data = self.repository.try_acquire_for_replay(dlq_id, config_max)

        if failed_op_data is None:
            existing = self.repository.get_by_id(dlq_id)
            if existing is None:
                return ReplayResult.failed(dlq_id, "DLQ entry not found")
            elif existing.status != "pending":
                return ReplayResult.failed(dlq_id, f"Cannot replay: status is '{existing.status}'")
            else:
                return ReplayResult.failed(dlq_id, "max_replays_exceeded")

        handler = get_replay_handler(failed_op_data.domain)

        try:
            result = handler.replay(failed_op_data)
        except Exception as e:
            logger.error(f"[ReplayService] Handler exception for DLQ {dlq_id}: {e}", exc_info=True)
            self.repository.complete_replay(
                id=dlq_id,
                success=False,
                note=f"Handler crash: {type(e).__name__}: {str(e)[:200]}",
                error_details={
                    "type": type(e).__name__,
                    "message": str(e)[:500],
                    "occurred_at": now().isoformat(),
                    "escalated_to": "requires_review",
                },
            )
            return ReplayResult.failed(dlq_id, f"internal_error: {type(e).__name__}")

        self.repository.complete_replay(
            id=dlq_id,
            success=result.success,
            resolution_type="auto_replay" if result.success else "",
            note=result.message if result.success else (result.error or "Replay failed"),
        )

        if result.success:
            logger.info(f"[ReplayService] DLQ entry {dlq_id} replayed successfully")
        else:
            logger.warning(f"[ReplayService] DLQ entry {dlq_id} replay failed: {result.error}")

        return result

    # =========================================================================
    # Conditional Replay (Circuit Breaker Recovery)
    # =========================================================================

    def replay_on_circuit_close(
        self,
        service_name: str,
        max_items: int = 50,
        escalate_failures: bool = True,
        service_failure_type_map: dict[str, list[str]] | None = None,
    ) -> BatchReplayResult:
        """
        Replay entries when circuit breaker closes.

        This is triggered when an external service recovers.
        Only replays entries related to the recovered service.

        IMPORTANT: When triggered by force_close with trigger_replay=True,
        any replay failures are escalated to REQUIRES_REVIEW status.
        This is because operator-initiated recovery implies the operator
        intended to resolve these items, so failures need explicit attention.

        Args:
            service_name: Name of the service that recovered
            max_items: Maximum number of items to replay
            escalate_failures: If True, mark failed replays as REQUIRES_REVIEW
            service_failure_type_map: Custom mapping of service names to failure types.
                                      If None, uses empty mapping (no auto-mapping).
                                      Example: {"my_service": ["TIMEOUT", "CONNECTION_ERROR"]}

        Returns:
            BatchReplayResult with summary
        """
        # Use provided mapping or empty (no default domain-specific mappings in core)
        failure_type_map = service_failure_type_map or {}

        failure_types = failure_type_map.get(service_name, [])
        if not failure_types:
            logger.info(f"[ReplayService] No failure types mapped for service '{service_name}'")
            return BatchReplayResult()

        # Replay entries with matching failure types using repository
        max_replays = self.config["max_replay_attempts"]

        entries = self.repository.get_pending_by_failure_types(
            failure_types=failure_types,
            max_retry_count=max_replays,
            limit=max_items,
        )

        batch_result = BatchReplayResult(
            total=len(entries),
            results=[],
        )

        for entry in entries:
            result = self.replay_single(entry.id)
            batch_result.results.append(result)

            if result.success:
                batch_result.success_count += 1
            else:
                batch_result.failed_count += 1

                # Escalate failures to REQUIRES_REVIEW when triggered by forced close
                # This ensures operator attention for operator-initiated recoveries
                if escalate_failures:
                    # Check if still in pending status before escalating
                    current_entry = self.repository.get_by_id(entry.id)
                    if current_entry and current_entry.status == "pending":
                        self.repository.mark_as_requires_review(
                            entry.id, note=f"Conditional replay failed after circuit close for {service_name}: {result.error}"
                        )
                        logger.warning(
                            f"[ReplayService] Escalated DLQ {entry.id} to REQUIRES_REVIEW " f"after conditional replay failure"
                        )

        logger.info(
            f"[ReplayService] Circuit close replay for {service_name}: "
            f"total={batch_result.total}, success={batch_result.success_count}, "
            f"failed={batch_result.failed_count} (escalated={batch_result.failed_count if escalate_failures else 0})"
        )

        return batch_result


# =============================================================================
# Module-level convenience functions
# =============================================================================


_replay_service: ReplayService | None = None


def get_replay_service() -> ReplayService:
    """Get the singleton replay service instance."""
    global _replay_service
    if _replay_service is None:
        _replay_service = ReplayService()
    return _replay_service


def replay_failed_operation(dlq_id: int) -> ReplayResult:
    """
    Convenience function to replay a single DLQ entry.

    This is a shortcut for get_replay_service().replay_single(dlq_id).
    """
    return get_replay_service().replay_single(dlq_id)


def batch_replay_by_failure_type(
    failure_type: str,
    max_items: int = 100,
) -> BatchReplayResult:
    """
    Convenience function to replay entries by failure type.

    This is a shortcut for get_replay_service().replay_batch(...).
    """
    return get_replay_service().replay_batch(
        failure_type=failure_type,
        max_items=max_items,
    )
