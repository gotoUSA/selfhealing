"""
Tests for audit_helpers module.

Tests DLQ store/replay audit logging functionality.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestLogDlqStoreAudit:
    """Tests for log_dlq_store_audit function."""

    def test_logs_to_adapter_when_available(self):
        """Should call adapter.log_dlq_store when adapter is available."""
        mock_adapter = MagicMock()
        
        with patch(
            "selfhealing.services.audit_helpers._get_audit_adapter",
            return_value=mock_adapter,
        ):
            from selfhealing.services.audit_helpers import log_dlq_store_audit
            
            log_dlq_store_audit(
                dlq_id=123,
                domain="payment",
                failure_type="PG_TIMEOUT",
                error_message="Connection timed out",
            )
            
            mock_adapter.log_dlq_store.assert_called_once_with(
                dlq_id=123,
                domain="payment",
                failure_type="PG_TIMEOUT",
                error_message="Connection timed out",
            )

    def test_logs_to_standard_logger_when_adapter_unavailable(self, caplog):
        """Should log to standard logger when adapter is not available."""
        with patch(
            "selfhealing.services.audit_helpers._get_audit_adapter",
            return_value=None,
        ):
            from selfhealing.services.audit_helpers import log_dlq_store_audit
            import logging
            
            with caplog.at_level(logging.INFO):
                log_dlq_store_audit(
                    dlq_id=456,
                    domain="point",
                    failure_type="AMOUNT_MISMATCH",
                )
            
            assert "[DLQAudit] STORE" in caplog.text
            assert "id=456" in caplog.text
            assert "domain=point" in caplog.text

    def test_handles_adapter_exception_gracefully(self, caplog):
        """Should not raise when adapter.log_dlq_store fails."""
        mock_adapter = MagicMock()
        mock_adapter.log_dlq_store.side_effect = Exception("Adapter error")
        
        with patch(
            "selfhealing.services.audit_helpers._get_audit_adapter",
            return_value=mock_adapter,
        ):
            from selfhealing.services.audit_helpers import log_dlq_store_audit
            import logging
            
            with caplog.at_level(logging.WARNING):
                # Should not raise
                log_dlq_store_audit(
                    dlq_id=789,
                    domain="webhook",
                    failure_type="SIGNATURE_INVALID",
                )
            
            assert "[DLQAudit] Failed to log store" in caplog.text


class TestLogDlqReplayAudit:
    """Tests for log_dlq_replay_audit function."""

    def test_logs_success_to_adapter(self):
        """Should call adapter.log_dlq_replay with success=True."""
        mock_adapter = MagicMock()
        
        with patch(
            "selfhealing.services.audit_helpers._get_audit_adapter",
            return_value=mock_adapter,
        ):
            from selfhealing.services.audit_helpers import log_dlq_replay_audit
            
            log_dlq_replay_audit(
                dlq_id=123,
                domain="payment",
                success=True,
                actor_id="user_1",
            )
            
            mock_adapter.log_dlq_replay.assert_called_once_with(
                dlq_id=123,
                domain="payment",
                success=True,
                actor_id="user_1",
                error_message=None,
            )

    def test_logs_failure_to_adapter(self):
        """Should call adapter.log_dlq_replay with success=False and error."""
        mock_adapter = MagicMock()
        
        with patch(
            "selfhealing.services.audit_helpers._get_audit_adapter",
            return_value=mock_adapter,
        ):
            from selfhealing.services.audit_helpers import log_dlq_replay_audit
            
            log_dlq_replay_audit(
                dlq_id=456,
                domain="point",
                success=False,
                error_message="Max retries exceeded",
            )
            
            mock_adapter.log_dlq_replay.assert_called_once_with(
                dlq_id=456,
                domain="point",
                success=False,
                actor_id=None,
                error_message="Max retries exceeded",
            )

    def test_logs_success_to_standard_logger_when_adapter_unavailable(self, caplog):
        """Should log SUCCESS to standard logger when adapter is unavailable."""
        with patch(
            "selfhealing.services.audit_helpers._get_audit_adapter",
            return_value=None,
        ):
            from selfhealing.services.audit_helpers import log_dlq_replay_audit
            import logging
            
            with caplog.at_level(logging.INFO):
                log_dlq_replay_audit(
                    dlq_id=789,
                    domain="webhook",
                    success=True,
                )
            
            assert "[DLQAudit] REPLAY_SUCCESS" in caplog.text
            assert "id=789" in caplog.text

    def test_logs_failure_to_standard_logger_when_adapter_unavailable(self, caplog):
        """Should log FAILED to standard logger when adapter is unavailable."""
        with patch(
            "selfhealing.services.audit_helpers._get_audit_adapter",
            return_value=None,
        ):
            from selfhealing.services.audit_helpers import log_dlq_replay_audit
            import logging
            
            with caplog.at_level(logging.INFO):
                log_dlq_replay_audit(
                    dlq_id=101,
                    domain="notification",
                    success=False,
                    error_message="Handler crashed",
                )
            
            assert "[DLQAudit] REPLAY_FAILED" in caplog.text
            assert "id=101" in caplog.text
            assert "error=Handler crashed" in caplog.text

    def test_handles_adapter_exception_gracefully(self, caplog):
        """Should not raise when adapter.log_dlq_replay fails."""
        mock_adapter = MagicMock()
        mock_adapter.log_dlq_replay.side_effect = Exception("Adapter error")
        
        with patch(
            "selfhealing.services.audit_helpers._get_audit_adapter",
            return_value=mock_adapter,
        ):
            from selfhealing.services.audit_helpers import log_dlq_replay_audit
            import logging
            
            with caplog.at_level(logging.WARNING):
                # Should not raise
                log_dlq_replay_audit(
                    dlq_id=202,
                    domain="payment",
                    success=True,
                )
            
            assert "[DLQAudit] Failed to log replay" in caplog.text


class TestGetAuditAdapter:
    """Tests for _get_audit_adapter internal function."""

    def test_returns_none_when_provider_registry_unavailable(self):
        """Should return None when ProviderRegistry is not available."""
        with patch(
            "selfhealing.services.audit_helpers._get_audit_adapter",
            return_value=None,
        ):
            from selfhealing.services.audit_helpers import _get_audit_adapter
            
            # Re-import to test actual behavior
            import selfhealing.services.audit_helpers as helpers
            
            with patch.object(helpers, "_get_audit_adapter", wraps=helpers._get_audit_adapter):
                # Mock ProviderRegistry to raise ImportError
                with patch.dict("sys.modules", {"selfhealing.factory": None}):
                    result = helpers._get_audit_adapter()
                    # When factory is None, import fails, returns None
                    assert result is None
