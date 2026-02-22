"""
Unit tests for DLQService API business logic methods.

Tests for DLQService:
- replay()
- Data classes (ReplayResult, CleanupStats, DLQPaginatedResult, DlqReplayResult, ResolveResult)
"""

from unittest.mock import Mock

from selfhealing.services.dlq_models import (
    CleanupStats,
    DLQPaginatedResult,
    DlqReplayResult,
    ReplayResult,
    ResolveResult,
)
from selfhealing.services.dlq_service import (
    DLQConfig,
    DLQService,
    get_dlq_service,
)


class TestReplayResult:
    """Tests for ReplayResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = ReplayResult()
        assert result.processed == 0
        assert result.success == 0
        assert result.failed == 0
        assert result.skipped == 0
        assert result.errors == []

    def test_custom_values(self):
        """Test custom values."""
        result = ReplayResult(
            processed=10,
            success=8,
            failed=2,
            skipped=0,
            errors=["Error 1", "Error 2"],
        )
        assert result.processed == 10
        assert result.success == 8
        assert result.failed == 2
        assert len(result.errors) == 2


class TestCleanupStats:
    """Tests for CleanupStats dataclass."""

    def test_default_values(self):
        """Test default values."""
        stats = CleanupStats()
        assert stats.total == 0
        assert stats.by_status == {}
        assert stats.resolved_older_than_30_days == 0
        assert stats.archived_older_than_90_days == 0

    def test_can_archive_property(self):
        """Test can_archive property."""
        stats = CleanupStats(resolved_older_than_30_days=15)
        assert stats.can_archive == 15

    def test_can_purge_property(self):
        """Test can_purge property."""
        stats = CleanupStats(archived_older_than_90_days=25)
        assert stats.can_purge == 25


class TestDLQPaginatedResult:
    """Tests for DLQPaginatedResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = DLQPaginatedResult()
        assert result.results == []
        assert result.page == 1
        assert result.page_size == 20
        assert result.total_pages == 0
        assert result.total_count == 0
        assert result.has_next is False
        assert result.has_previous is False


class TestDlqReplayResult:
    """Tests for DlqReplayResult dataclass."""

    def test_success_result(self):
        """Test successful retry result."""
        result = DlqReplayResult(
            success=True,
            id=1,
            retry_count=3,
            previous_retry_count=2,
            message="Retry triggered",
        )
        assert result.success is True
        assert result.retry_count == 3
        assert result.previous_retry_count == 2


class TestResolveResult:
    """Tests for ResolveResult dataclass."""

    def test_success_result(self):
        """Test successful resolve result."""
        result = ResolveResult(
            success=True,
            id=1,
            previous_status="pending",
            current_status="resolved",
            resolved_at="2025-12-19T10:00:00",
            notes="Manually resolved",
        )
        assert result.success is True
        assert result.previous_status == "pending"
        assert result.current_status == "resolved"


class TestDLQServiceReplay:
    """Tests for DLQService.replay() method."""

    def test_replay_returns_replay_result(self):
        """Test replay returns ReplayResult."""
        mock_repo = Mock()
        mock_repo.find_by_status.return_value = []

        config = DLQConfig(enabled=True)
        service = DLQService(config=config, repository=mock_repo)

        result = service.replay(domain="payment", batch_size=10)

        assert isinstance(result, ReplayResult)
        assert result.processed == 0

    def test_replay_with_entries(self):
        """Test replay with pending entries."""
        mock_entry = Mock()
        mock_entry.id = 1
        mock_entry.domain = "payment"
        mock_entry.failure_type = "PG_TIMEOUT"

        mock_repo = Mock()
        mock_repo.find_by_status.return_value = [mock_entry]

        config = DLQConfig(enabled=True)
        service = DLQService(config=config, repository=mock_repo)

        result = service.replay(domain="payment", batch_size=10)

        assert result.processed == 1


class TestGetDLQService:
    """Tests for get_dlq_service() singleton."""

    def test_get_dlq_service_returns_instance(self):
        """Test get_dlq_service returns DLQService instance."""
        # Reset singleton
        import selfhealing.services.dlq_service as dlq_module

        dlq_module._dlq_service = None

        service = get_dlq_service()
        assert isinstance(service, DLQService)

    def test_get_dlq_service_returns_same_instance(self):
        """Test get_dlq_service returns singleton."""
        service1 = get_dlq_service()
        service2 = get_dlq_service()
        assert service1 is service2
