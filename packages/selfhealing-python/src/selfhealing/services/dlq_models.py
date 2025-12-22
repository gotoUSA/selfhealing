"""
DLQ Models and Data Classes

Data classes and configuration for DLQ operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from selfhealing.core.config import get_config


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
# Result Data Classes
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
