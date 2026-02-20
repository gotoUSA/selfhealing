"""
Unit tests for DLQ Entry Operations Mixin.

Tests for the Repository pattern implementation of:
- EntryOperationsMixin.retry_entry()
- EntryOperationsMixin.resolve_entry()
- EntryOperationsMixin.get_entry()

These methods use Repository pattern instead of direct Django ORM access
(domain-free architecture).

Refactored to use Factory Pattern (Phase 2):
- make_mock_entry → TestDataFactory.mock_failed_operation
"""

from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock

import pytest

from selfhealing.interfaces import FailedOperationData
from selfhealing.services.dlq import DLQService, DLQConfig

# Factory Pattern imports
from tests.factories import TestDataFactory


class TestRetryEntry:
    """Tests for EntryOperationsMixin.retry_entry()."""

    def test_retry_entry_success(self):
        """Test successful retry increments count."""
        mock_entry = TestDataFactory.mock_failed_operation(id=1, status="pending", retry_count=1)
        
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = mock_entry
        mock_repo.increment_retry_count.return_value = True
        
        config = DLQConfig(enabled=True)
        service = DLQService(config=config, repository=mock_repo)
        
        result = service.retry_entry(pk=1)
        
        assert result["success"] is True
        assert result["id"] == 1
        assert result["previous_retry_count"] == 1
        assert result["retry_count"] == 2
        mock_repo.increment_retry_count.assert_called_once_with(1)

    def test_retry_entry_not_found(self):
        """Test retry on non-existent entry raises ValueError."""
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = None
        
        service = DLQService(repository=mock_repo)
        
        with pytest.raises(ValueError, match="not found"):
            service.retry_entry(pk=999)

    def test_retry_entry_resolved_fails(self):
        """Test retry on already resolved entry raises ValueError."""
        mock_entry = TestDataFactory.mock_failed_operation(id=1, status="resolved")
        
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = mock_entry
        
        service = DLQService(repository=mock_repo)
        
        with pytest.raises(ValueError, match="already resolved"):
            service.retry_entry(pk=1)

    def test_retry_entry_archived_fails(self):
        """Test retry on archived entry raises ValueError."""
        mock_entry = TestDataFactory.mock_failed_operation(id=1, status="archived")
        
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = mock_entry
        
        service = DLQService(repository=mock_repo)
        
        with pytest.raises(ValueError, match="archived"):
            service.retry_entry(pk=1)


class TestResolveEntry:
    """Tests for EntryOperationsMixin.resolve_entry()."""

    def test_resolve_entry_success(self):
        """Test successful resolution marks entry as resolved."""
        mock_entry = TestDataFactory.mock_failed_operation(id=1, status="pending")
        
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = mock_entry
        mock_repo.mark_as_resolved.return_value = True
        
        service = DLQService(repository=mock_repo)
        
        result = service.resolve_entry(pk=1, notes="Manually resolved")
        
        assert result["success"] is True
        assert result["id"] == 1
        assert result["previous_status"] == "pending"
        assert result["current_status"] == "resolved"
        mock_repo.mark_as_resolved.assert_called_once_with(
            id=1, resolution_type="manual", resolution_note="Manually resolved"
        )

    def test_resolve_entry_not_found(self):
        """Test resolve on non-existent entry raises ValueError."""
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = None
        
        service = DLQService(repository=mock_repo)
        
        with pytest.raises(ValueError, match="not found"):
            service.resolve_entry(pk=999)

    def test_resolve_entry_already_resolved(self):
        """Test resolve on already resolved entry raises ValueError."""
        mock_entry = TestDataFactory.mock_failed_operation(id=1, status="resolved")
        
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = mock_entry
        
        service = DLQService(repository=mock_repo)
        
        with pytest.raises(ValueError, match="already resolved"):
            service.resolve_entry(pk=1)


class TestGetEntry:
    """Tests for EntryOperationsMixin.get_entry()."""

    def test_get_entry_success(self):
        """Test getting entry returns formatted dict."""
        mock_entry = TestDataFactory.mock_failed_operation(id=1, domain="payment")
        
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = mock_entry
        
        service = DLQService(repository=mock_repo)
        
        result = service.get_entry(pk=1)
        
        assert result is not None
        assert result["id"] == 1
        assert result["domain"] == "payment"
        assert result["failure_type"] == "PG_TIMEOUT"
        assert result["status"] == "pending"

    def test_get_entry_not_found(self):
        """Test getting non-existent entry returns None."""
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = None
        
        service = DLQService(repository=mock_repo)
        
        result = service.get_entry(pk=999)
        
        assert result is None

    def test_get_entry_with_all_fields(self):
        """Test get_entry returns all expected fields."""
        mock_entry = TestDataFactory.mock_failed_operation(id=5)
        mock_entry.snapshot_data = {"order_id": "test-123"}
        
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = mock_entry
        
        service = DLQService(repository=mock_repo)
        
        result = service.get_entry(pk=5)
        
        expected_fields = [
            "id", "domain", "failure_type", "status",
            "retry_count", "created_at", "resolved_at",
            "error_code", "error_message", "snapshot_data"
        ]
        for field in expected_fields:
            assert field in result, f"Missing field: {field}"
