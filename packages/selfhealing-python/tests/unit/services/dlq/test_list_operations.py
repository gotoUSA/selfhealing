"""
Unit tests for DLQ List Operations Mixin.

Tests for the Repository pattern implementation of:
- ListOperationsMixin.list_entries()

Uses Repository pattern instead of direct Django ORM access
(domain-free architecture).

Refactored to use Factory Pattern (Phase 2):
- make_mock_entries → TestDataFactory.mock_failed_operation
"""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from selfhealing.services.dlq import DLQService, DLQConfig

# Factory Pattern imports
from tests.factories import TestDataFactory


def make_mock_entries(count: int, status: str = "pending") -> list:
    """Create a list of mock FailedOperationData for testing."""
    entries = []
    for i in range(count):
        entries.append(TestDataFactory.mock_failed_operation(
            id=i + 1,
            status=status,
        ))
    return entries


class TestListEntries:
    """Tests for ListOperationsMixin.list_entries()."""

    def test_list_entries_empty(self):
        """Test list_entries with no entries."""
        mock_repo = Mock()
        mock_repo.find_by_status.return_value = []
        
        service = DLQService(repository=mock_repo)
        
        result = service.list_entries()
        
        assert result["results"] == []
        assert result["total_count"] == 0
        assert result["page"] == 1
        assert result["has_next"] is False

    def test_list_entries_with_data(self):
        """Test list_entries returns paginated results."""
        mock_entries = make_mock_entries(5)
        
        mock_repo = Mock()
        mock_repo.find_by_status.return_value = mock_entries
        
        service = DLQService(repository=mock_repo)
        
        result = service.list_entries(filters={"status": "pending"})
        
        assert len(result["results"]) == 5
        assert result["total_count"] == 5
        assert result["page"] == 1

    def test_list_entries_pagination(self):
        """Test list_entries respects page_size."""
        mock_entries = make_mock_entries(25)
        
        mock_repo = Mock()
        mock_repo.find_by_status.return_value = mock_entries
        
        service = DLQService(repository=mock_repo)
        
        result = service.list_entries(
            filters={"status": "pending"},
            page=1,
            page_size=10
        )
        
        assert len(result["results"]) == 10
        assert result["total_count"] == 25
        assert result["total_pages"] == 3
        assert result["has_next"] is True
        assert result["has_previous"] is False

    def test_list_entries_page_2(self):
        """Test list_entries returns correct page."""
        mock_entries = make_mock_entries(25)
        
        mock_repo = Mock()
        mock_repo.find_by_status.return_value = mock_entries
        
        service = DLQService(repository=mock_repo)
        
        result = service.list_entries(
            filters={"status": "pending"},
            page=2,
            page_size=10
        )
        
        assert len(result["results"]) == 10
        assert result["page"] == 2
        assert result["has_next"] is True
        assert result["has_previous"] is True

    def test_list_entries_last_page(self):
        """Test list_entries on last page."""
        mock_entries = make_mock_entries(25)
        
        mock_repo = Mock()
        mock_repo.find_by_status.return_value = mock_entries
        
        service = DLQService(repository=mock_repo)
        
        result = service.list_entries(
            filters={"status": "pending"},
            page=3,
            page_size=10
        )
        
        assert len(result["results"]) == 5
        assert result["page"] == 3
        assert result["has_next"] is False
        assert result["has_previous"] is True

    def test_list_entries_max_page_size(self):
        """Test list_entries limits page_size to 100."""
        mock_entries = make_mock_entries(10)
        
        mock_repo = Mock()
        mock_repo.find_by_status.return_value = mock_entries
        
        service = DLQService(repository=mock_repo)
        
        result = service.list_entries(
            filters={"status": "pending"},
            page_size=200  # Should be capped at 100
        )
        
        # Page size should be capped at 100
        assert result["page_size"] == 100

    def test_list_entries_with_domain_filter(self):
        """Test list_entries with domain filter."""
        mock_entries = make_mock_entries(3)
        
        mock_repo = Mock()
        mock_repo.find_by_status.return_value = mock_entries
        
        service = DLQService(repository=mock_repo)
        
        result = service.list_entries(
            filters={"status": "pending", "domain": "payment"}
        )
        
        # Verify domain was passed to repository
        mock_repo.find_by_status.assert_called()
        call_args = mock_repo.find_by_status.call_args
        assert call_args.kwargs.get("domain") == "payment"

    def test_list_entries_error_handling(self):
        """Test list_entries handles errors gracefully."""
        mock_repo = Mock()
        mock_repo.find_by_status.side_effect = Exception("Database error")
        
        service = DLQService(repository=mock_repo)
        
        result = service.list_entries()
        
        # Should return empty result, not raise exception
        assert result["results"] == []
        assert result["total_count"] == 0

    def test_list_entries_entry_format(self):
        """Test list_entries returns correctly formatted entries."""
        mock_entry = Mock()
        mock_entry.id = 42
        mock_entry.domain = "payment"
        mock_entry.failure_type = "NETWORK_ERROR"
        mock_entry.status = "pending"
        mock_entry.retry_count = 2
        mock_entry.created_at = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        mock_entry.resolved_at = None
        
        mock_repo = Mock()
        mock_repo.find_by_status.return_value = [mock_entry]
        
        service = DLQService(repository=mock_repo)
        
        result = service.list_entries(filters={"status": "pending"})
        
        entry = result["results"][0]
        assert entry["id"] == 42
        assert entry["domain"] == "payment"
        assert entry["failure_type"] == "NETWORK_ERROR"
        assert entry["status"] == "pending"
        assert entry["retry_count"] == 2
        assert entry["created_at"] is not None
