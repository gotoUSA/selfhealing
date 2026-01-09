"""
Integration tests for AuditMiddleware data access recording (ADR-002).

These tests require Django settings to be configured.
Moved from packages/selfhealing-python/tests for proper test isolation.
"""

import pytest
from unittest.mock import MagicMock


@pytest.mark.django_db
class TestAuditMiddlewareDataAccess:
    """Tests for AuditMiddleware data access recording (ADR-002)."""

    def test_default_read_audit_paths(self):
        """Should have default read audit paths."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        
        assert "/api/admin/" in AuditMiddleware.DEFAULT_READ_AUDIT_PATHS
        assert "/api/payments/" in AuditMiddleware.DEFAULT_READ_AUDIT_PATHS

    def test_should_audit_read_matches_path(self):
        """Should match paths for read audit."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        
        middleware = AuditMiddleware(lambda r: MagicMock())
        middleware._read_audit_paths = ["/api/admin/", "/api/payments/"]
        
        assert middleware._should_audit_read("/api/admin/users/")
        assert middleware._should_audit_read("/api/payments/transactions/")
        assert not middleware._should_audit_read("/api/products/")

    def test_capture_read_access_adds_event_for_get(self):
        """Should add DATA_ACCESS event for GET requests on configured paths."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        middleware = AuditMiddleware(lambda r: MagicMock())
        middleware._read_audit_paths = ["/api/admin/"]
        
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.path = "/api/admin/users/"
        mock_request.META = {}
        
        buffer = RequestAuditBuffer()
        
        middleware._capture_read_access(mock_request, buffer)
        
        # Should have added DATA_ACCESS event
        assert buffer.event_count() == 1
        event = buffer.get_events()[0]
        assert event.event_type == AuditEventType.DATA_ACCESS

    def test_capture_read_access_skips_non_get(self):
        """Should skip non-GET requests."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        middleware = AuditMiddleware(lambda r: MagicMock())
        middleware._read_audit_paths = ["/api/admin/"]
        
        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.path = "/api/admin/users/"
        
        buffer = RequestAuditBuffer()
        
        middleware._capture_read_access(mock_request, buffer)
        
        # Should not have added any event
        assert buffer.event_count() == 0

    def test_capture_read_access_skips_unconfigured_paths(self):
        """Should skip paths not in read_paths config."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        middleware = AuditMiddleware(lambda r: MagicMock())
        middleware._read_audit_paths = ["/api/admin/"]
        
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.path = "/api/products/"
        
        buffer = RequestAuditBuffer()
        
        middleware._capture_read_access(mock_request, buffer)
        
        # Should not have added any event
        assert buffer.event_count() == 0
