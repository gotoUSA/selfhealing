"""
DLQ Models and Data Classes

Data classes and configuration for DLQ operations.

This is the canonical location for DLQ models.
Previously located at ``selfhealing.services.dlq.models``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from selfhealing.settings import get_dlq_settings

# =============================================================================
# Configuration
# =============================================================================


@dataclass
class DLQConfig:
    """Configuration for DLQ operations."""

    enabled: bool = True
    retention_days: int = 30
    max_replay_attempts: int = 2
    max_retries: int = 3
    retry_delay: int = 60
    expiry_hours: int = 72
    batch_size: int = 10

    @classmethod
    def from_settings(cls) -> DLQConfig:
        """Load configuration from RuntimeConfigManager (preferred) or DLQSettings."""
        # Try RuntimeConfigManager first (runtime-configurable)
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            runtime_config = manager.get_dlq_config()

            return cls(
                enabled=runtime_config.get("enabled", True),
                retention_days=runtime_config.get("retention_days", 30),
                max_replay_attempts=runtime_config.get("max_replay_attempts", 2),
                max_retries=runtime_config.get("max_retries", 3),
                retry_delay=runtime_config.get("retry_delay", 60),
                expiry_hours=runtime_config.get("expiry_hours", 72),
                batch_size=runtime_config.get("batch_size", 10),
            )
        except Exception:
            pass  # Fall through to static config

        # Fallback to DLQSettings (Pydantic Settings)
        dlq_settings = get_dlq_settings()
        return cls(
            enabled=dlq_settings.enabled,
            retention_days=dlq_settings.retention_days,
            max_replay_attempts=dlq_settings.max_replay_attempts,
            max_retries=dlq_settings.max_retries,
            retry_delay=dlq_settings.retry_delay,
            expiry_hours=dlq_settings.expiry_hours,
            batch_size=dlq_settings.batch_size,
        )


# =============================================================================
# Result Data Classes
# =============================================================================


@dataclass
class DLQEntryResult:
    """Result of a DLQ operation."""

    success: bool
    dlq_id: int | None = None
    error: str | None = None
    fallback_path: str | None = None  # Local fallback 경로 (DB 실패 시)

    @classmethod
    def created(cls, dlq_id: int) -> DLQEntryResult:
        """Factory for successful creation."""
        return cls(success=True, dlq_id=dlq_id)

    @classmethod
    def failed(cls, error: str) -> DLQEntryResult:
        """Factory for failed operation (no fallback)."""
        return cls(success=False, error=error)

    @classmethod
    def fallback(cls, error: str, fallback_path: str) -> DLQEntryResult:
        """Factory for fallback to local file (data preserved)."""
        return cls(success=False, error=error, fallback_path=fallback_path)

    @property
    def is_fallback(self) -> bool:
        """데이터가 local fallback에 저장되었는지 여부."""
        return self.fallback_path is not None


@dataclass
class DLQBatchReplayStats:
    """Result of a batch replay operation (statistics).

    Note: This is for batch DLQ replay statistics, not to be confused with
    ReplayResult in replay_service.py which is for single replay outcomes.
    """

    processed: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# Backward compatibility alias
ReplayResult = DLQBatchReplayStats


@dataclass
class CleanupStats:
    """Statistics for DLQ cleanup operations."""

    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
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
class DLQPaginatedResult:
    """Paginated result for DLQ list operations."""

    results: list[dict[str, Any]] = field(default_factory=list)
    page: int = 1
    page_size: int = 20
    total_pages: int = 0
    total_count: int = 0
    has_next: bool = False
    has_previous: bool = False


@dataclass
class DlqReplayResult:
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
# Throttle-aware Replay Result Data Classes
# =============================================================================


@dataclass
class DLQThrottleReplayResult:
    """Result of a single throttle-aware DLQ replay operation."""

    success: bool
    entry_id: int | None = None
    error: str | None = None
    retry_after: float | None = None  # Throttle 거부 시 재시도 대기 시간(초)


@dataclass
class DLQThrottleBatchReplayResult:
    """Result of a batch throttle-aware DLQ replay operation."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    early_stop_reason: str | None = None
