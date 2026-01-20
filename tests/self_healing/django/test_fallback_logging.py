"""
Fallback Logging Tests.

These tests require Django settings for middleware imports.
Run with: docker-compose exec web pytest tests/self_healing/django/test_fallback_logging.py
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, Mock


@pytest.mark.django_db
class TestFallbackLogging:
    """Tests for fallback logging mechanism."""

    def test_fallback_log_called_on_primary_failure(self, capsys):
        """Test that fallback logging is used when primary fails."""
        from selfhealing.api.django.middleware import (
            SensitiveEndpointAccessLogger,
            AccessLogEntry,
        )

        logger_service = SensitiveEndpointAccessLogger()

        entry = AccessLogEntry(
            timestamp=datetime.now(timezone.utc),
            user="testuser",
            method="GET",
            path="/api/self-healing/audit/",
            query_params="",
            source_ip="1.2.3.4",
            user_agent="test",
            status_code=200,
        )

        # Force primary_success to False by patching
        with patch.object(logger_service, '_append_to_file', side_effect=Exception("File error")):
            with patch('selfhealing.api.django.middleware.access_logging.logger') as mock_logger:
                # Also make logger.info fail
                mock_logger.info.side_effect = Exception("Logger error")
                mock_logger.warning = Mock()  # Keep warning working

                logger_service._write_log(entry)

        # Check stdout for fallback log
        captured = capsys.readouterr()
        assert "[FALLBACK_AUDIT_LOG]" in captured.out
        assert "testuser" in captured.out

    def test_fallback_log_format(self, capsys):
        """Test fallback log output format."""
        from selfhealing.api.django.middleware import (
            SensitiveEndpointAccessLogger,
            AccessLogEntry,
        )

        logger_service = SensitiveEndpointAccessLogger()

        entry = AccessLogEntry(
            timestamp=datetime.now(timezone.utc),
            user="admin",
            method="POST",
            path="/api/self-healing/config/update/",
            query_params="",
            source_ip="10.0.0.1",
            user_agent="",
            status_code=403,
        )

        # Call fallback directly
        logger_service._fallback_log(entry)

        captured = capsys.readouterr()
        assert "[FALLBACK_AUDIT_LOG]" in captured.out
        assert "_fallback" in captured.out
        assert "primary_logging_failed" in captured.out
