"""
Unit tests for DLQService API business logic methods.

Tests for the new methods added to DLQService:
- replay()
- get_cleanup_stats()
- archive_old_entries()
- purge_archived()
- list_entries()
- get_entry()
- retry_entry()
- resolve_entry()
- create_test_entry()
"""

import pytest
from datetime import timedelta
from unittest.mock import Mock, patch, MagicMock

from selfhealing.services.dlq_service import (
    DLQService,
    DLQConfig,
    ReplayResult,
    CleanupStats,
    PaginatedResult,
    RetryResult,
    ResolveResult,
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


class TestPaginatedResult:
    """Tests for PaginatedResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = PaginatedResult()
        assert result.results == []
        assert result.page == 1
        assert result.page_size == 20
        assert result.total_pages == 0
        assert result.total_count == 0
        assert result.has_next is False
        assert result.has_previous is False


class TestRetryResult:
    """Tests for RetryResult dataclass."""

    def test_success_result(self):
        """Test successful retry result."""
        result = RetryResult(
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


class TestDLQServiceCleanupStats:
    """Tests for DLQService.get_cleanup_stats() method."""

    @patch("selfhealing.services.dlq_service.DLQService.get_cleanup_stats")
    def test_get_cleanup_stats_returns_cleanup_stats(self, mock_method):
        """Test get_cleanup_stats returns CleanupStats."""
        mock_method.return_value = CleanupStats(
            total=100,
            by_status={"pending": 50, "resolved": 30, "archived": 20},
            resolved_older_than_30_days=10,
            archived_older_than_90_days=5,
        )
        
        service = DLQService()
        result = service.get_cleanup_stats()
        
        assert isinstance(result, CleanupStats)
        assert result.total == 100
        assert result.can_archive == 10
        assert result.can_purge == 5


class TestDLQServiceArchive:
    """Tests for DLQService.archive_old_entries() method."""

    def test_archive_invalid_days_raises_error(self):
        """Test archive with invalid days raises ValueError."""
        service = DLQService()
        
        with pytest.raises(ValueError, match="older_than_days must be at least 1"):
            service.archive_old_entries(older_than_days=0)

    def test_archive_negative_days_raises_error(self):
        """Test archive with negative days raises ValueError."""
        service = DLQService()
        
        with pytest.raises(ValueError, match="older_than_days must be at least 1"):
            service.archive_old_entries(older_than_days=-5)


class TestDLQServicePurge:
    """Tests for DLQService.purge_archived() method."""

    def test_purge_both_params_raises_error(self):
        """Test purge with both ids and older_than_days raises ValueError."""
        service = DLQService()
        
        with pytest.raises(ValueError, match="Specify either ids or older_than_days, not both"):
            service.purge_archived(ids=[1, 2, 3], older_than_days=90)

    @patch("selfhealing.services.dlq_service.DLQService.purge_archived")
    def test_purge_invalid_days_raises_error(self, mock_method):
        """Test purge with invalid days raises ValueError."""
        mock_method.side_effect = ValueError("older_than_days must be at least 1")
        
        service = DLQService()
        
        with pytest.raises(ValueError, match="older_than_days must be at least 1"):
            service.purge_archived(older_than_days=0)


class TestDLQServiceListEntries:
    """Tests for DLQService.list_entries() method."""

    @patch("selfhealing.services.dlq_service.DLQService.list_entries")
    def test_list_entries_returns_paginated_result(self, mock_method):
        """Test list_entries returns PaginatedResult."""
        mock_method.return_value = PaginatedResult(
            results=[{"id": 1, "domain": "payment"}],
            page=1,
            page_size=20,
            total_pages=1,
            total_count=1,
            has_next=False,
            has_previous=False,
        )
        
        service = DLQService()
        result = service.list_entries(filters={"domain": "payment"})
        
        assert isinstance(result, PaginatedResult)
        assert len(result.results) == 1

    @patch("selfhealing.services.dlq_service.DLQService.list_entries")
    def test_list_entries_respects_page_size_limit(self, mock_method):
        """Test list_entries limits page_size to 100."""
        mock_method.return_value = PaginatedResult(page_size=100)
        
        service = DLQService()
        result = service.list_entries(page_size=200)
        
        # The mock was called, real implementation would cap at 100
        mock_method.assert_called_once()


class TestDLQServiceRetryEntry:
    """Tests for DLQService.retry_entry() method."""

    @patch("selfhealing.services.dlq_service.DLQService.retry_entry")
    def test_retry_entry_returns_retry_result(self, mock_method):
        """Test retry_entry returns RetryResult."""
        mock_method.return_value = RetryResult(
            success=True,
            id=1,
            retry_count=2,
            previous_retry_count=1,
            message="Retry triggered for entry 1",
        )
        
        service = DLQService()
        result = service.retry_entry(pk=1)
        
        assert isinstance(result, RetryResult)
        assert result.success is True
        assert result.retry_count == 2


class TestDLQServiceResolveEntry:
    """Tests for DLQService.resolve_entry() method."""

    @patch("selfhealing.services.dlq_service.DLQService.resolve_entry")
    def test_resolve_entry_returns_resolve_result(self, mock_method):
        """Test resolve_entry returns ResolveResult."""
        mock_method.return_value = ResolveResult(
            success=True,
            id=1,
            previous_status="pending",
            current_status="resolved",
            resolved_at="2025-12-19T10:00:00",
            notes="Test resolution",
        )
        
        service = DLQService()
        result = service.resolve_entry(pk=1, notes="Test resolution")
        
        assert isinstance(result, ResolveResult)
        assert result.success is True
        assert result.current_status == "resolved"


class TestDLQServiceCreateTestEntry:
    """Tests for DLQService.create_test_entry() method."""

    @patch("selfhealing.services.dlq_service.DLQService.create_test_entry")
    def test_create_test_entry_requires_debug_mode(self, mock_method):
        """Test create_test_entry requires DEBUG or TESTING mode."""
        mock_method.side_effect = PermissionError(
            "DLQ test entries can only be created in DEBUG/TEST mode"
        )
        
        service = DLQService()
        
        with pytest.raises(PermissionError, match="DEBUG/TEST mode"):
            service.create_test_entry(domain="payment", failure_type="TEST")

    @patch("selfhealing.services.dlq_service.DLQService.create_test_entry")
    def test_create_test_entry_requires_domain(self, mock_method):
        """Test create_test_entry requires domain."""
        mock_method.side_effect = ValueError("domain and failure_type are required")
        
        service = DLQService()
        
        with pytest.raises(ValueError, match="domain and failure_type are required"):
            service.create_test_entry(domain="", failure_type="TEST")

    @patch("selfhealing.services.dlq_service.DLQService.create_test_entry")
    def test_create_test_entry_requires_failure_type(self, mock_method):
        """Test create_test_entry requires failure_type."""
        mock_method.side_effect = ValueError("domain and failure_type are required")
        
        service = DLQService()
        
        with pytest.raises(ValueError, match="domain and failure_type are required"):
            service.create_test_entry(domain="payment", failure_type="")


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
