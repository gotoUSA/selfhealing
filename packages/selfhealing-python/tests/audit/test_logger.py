"""
Tests for the main AuditLogger class.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.audit import (
    AuditAction,
    AuditLogger,
    ConfigChangeEvent,
    LocalFileBackend,
    get_audit_logger,
    log_config_change,
)


class TestConfigChangeEvent:
    """Tests for ConfigChangeEvent dataclass."""

    def test_create_event(self):
        """Test creating a config change event."""
        event = ConfigChangeEvent(
            config_type="RETRY_CONFIG",
            config_key="max_retries",
            action=AuditAction.UPDATE,
            old_value=3,
            new_value=5,
            user="admin",
            reason="Increasing reliability",
        )

        assert event.config_type == "RETRY_CONFIG"
        assert event.config_key == "max_retries"
        assert event.action == AuditAction.UPDATE
        assert event.old_value == 3
        assert event.new_value == 5

    def test_to_dict(self):
        """Test converting event to dict."""
        event = ConfigChangeEvent(
            config_type="CIRCUIT_BREAKER",
            config_key="failure_threshold",
            action=AuditAction.UPDATE,
            old_value=5,
            new_value=10,
        )

        result = event.to_dict()

        assert isinstance(result, dict)
        assert result["config_type"] == "CIRCUIT_BREAKER"
        assert result["action"] == "update"

    def test_string_action(self):
        """Test using string action instead of enum."""
        event = ConfigChangeEvent(
            config_type="TEST",
            config_key="key",
            action="custom_action",
        )

        assert event.action == "custom_action"


class TestAuditLogger:
    """Tests for AuditLogger class."""

    def test_log_change_basic(self):
        """Test basic change logging."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            logger = AuditLogger(backend=backend, enable_console_log=False)

            event = ConfigChangeEvent(
                config_type="RETRY_CONFIG",
                config_key="max_retries",
                action=AuditAction.UPDATE,
                old_value={"value": 3},  # Use dict instead of int
                new_value={"value": 5},
                user="admin",
            )

            result = logger.log_change(event)

            assert result is True

            logger.close()

            # Verify log was written
            files = list(Path(tmpdir).glob("audit_*.jsonl"))
            with open(files[0]) as f:
                entry = json.loads(f.readline())

            assert entry["change"]["config_type"] == "RETRY_CONFIG"
            assert entry["change"]["old_value"]["value"] == 3
            assert entry["change"]["new_value"]["value"] == 5
            assert entry["actor"]["user"] == "admin"

    def test_log_change_with_dict(self):
        """Test logging change from dict instead of event object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            logger = AuditLogger(backend=backend, enable_console_log=False)

            event_dict = {
                "config_type": "TIMEOUT_CONFIG",
                "config_key": "connect_timeout",
                "action": "update",
                "old_value": {"timeout": 30},
                "new_value": {"timeout": 60},
                "user": "system",
            }

            result = logger.log_change(event_dict)

            assert result is True
            logger.close()

    def test_log_change_with_request(self):
        """Test logging with Django request object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            logger = AuditLogger(backend=backend, enable_console_log=False)

            request = MagicMock()
            request.META = {
                "REMOTE_ADDR": "192.168.1.100",
                "HTTP_USER_AGENT": "Mozilla/5.0",
            }

            event = ConfigChangeEvent(
                config_type="TEST",
                config_key="key",
                action=AuditAction.UPDATE,
                old_value={"v": 1},
                new_value={"v": 2},
            )

            result = logger.log_change(event, request=request)
            assert result is True
            logger.close()

            # Verify IP was extracted and masked
            files = list(Path(tmpdir).glob("audit_*.jsonl"))
            with open(files[0]) as f:
                entry = json.loads(f.readline())

            assert "192.168" in entry["actor"]["ip_address"]
            assert "***" in entry["actor"]["ip_address"]  # Should be masked
            assert "Mozilla" in entry["actor"]["user_agent"]

    def test_log_change_ip_masking(self):
        """Test that IP addresses are masked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            logger = AuditLogger(
                backend=backend,
                mask_ip_addresses=True,
                enable_console_log=False,
            )

            event = ConfigChangeEvent(
                config_type="TEST",
                config_key="key",
                action=AuditAction.UPDATE,
                ip_address="10.20.30.40",
            )

            logger.log_change(event)
            logger.close()

            files = list(Path(tmpdir).glob("audit_*.jsonl"))
            with open(files[0]) as f:
                entry = json.loads(f.readline())

            # Last two octets should be masked
            assert entry["actor"]["ip_address"] == "10.20.***.***"

    def test_log_change_sensitive_field_masking(self):
        """Test that sensitive fields are masked in values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            logger = AuditLogger(
                backend=backend,
                sensitive_fields=["password", "api_key"],
                enable_console_log=False,
            )

            event = ConfigChangeEvent(
                config_type="AUTH_CONFIG",
                config_key="settings",
                action=AuditAction.UPDATE,
                old_value={"username": "admin", "password": "old_secret"},
                new_value={"username": "admin", "password": "new_secret"},
            )

            logger.log_change(event)
            logger.close()

            files = list(Path(tmpdir).glob("audit_*.jsonl"))
            with open(files[0]) as f:
                entry = json.loads(f.readline())

            assert entry["change"]["old_value"]["password"] == "***REDACTED***"
            assert entry["change"]["new_value"]["password"] == "***REDACTED***"
            assert entry["change"]["old_value"]["username"] == "admin"

    def test_log_config_update_convenience(self):
        """Test log_config_update convenience method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            logger = AuditLogger(backend=backend, enable_console_log=False)

            result = logger.log_config_update(
                config_type="RATE_LIMIT",
                config_key="requests_per_minute",
                old_value={"limit": 100},
                new_value={"limit": 200},
                user="admin",
                reason="Scaling up",
            )

            assert result is True
            logger.close()

    def test_log_batch_update(self):
        """Test batch update logging."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            logger = AuditLogger(backend=backend, enable_console_log=False)

            changes = [
                {"key": "setting1", "old_value": {"v": 1}, "new_value": {"v": 2}},
                {"key": "setting2", "old_value": {"v": "a"}, "new_value": {"v": "b"}},
                {"key": "setting3", "old_value": {"v": True}, "new_value": {"v": False}},
            ]

            result = logger.log_batch_update(
                config_type="BULK_CONFIG",
                changes=changes,
                user="admin",
            )

            assert result is True
            logger.close()

            # Should have 3 log entries
            files = list(Path(tmpdir).glob("audit_*.jsonl"))
            with open(files[0]) as f:
                lines = f.readlines()

            assert len(lines) == 3

            # All should have same batch_id
            entries = [json.loads(line) for line in lines]
            batch_ids = [e["metadata"]["batch_id"] for e in entries]
            assert len(set(batch_ids)) == 1  # All same batch ID

    def test_query_logs(self):
        """Test querying audit logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            logger = AuditLogger(backend=backend, enable_console_log=False)

            # Write some entries
            for i in range(5):
                logger.log_config_update(
                    config_type="TEST_CONFIG",
                    config_key=f"key_{i}",
                    old_value={"v": i},
                    new_value={"v": i + 1},
                )

            # Query
            results = logger.query(config_type="TEST_CONFIG", limit=10)

            assert len(results) == 5
            logger.close()

    def test_verify_integrity(self):
        """Test integrity verification through logger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=True)
            logger = AuditLogger(backend=backend, enable_console_log=False)

            for i in range(5):
                logger.log_config_update(
                    config_type="TEST",
                    config_key=f"key_{i}",
                    old_value=i,
                    new_value=i + 1,
                )

            is_valid, issues = logger.verify_integrity()

            assert is_valid
            assert len(issues) == 0
            logger.close()

    def test_get_backend_health(self):
        """Test getting backend health."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir)
            logger = AuditLogger(backend=backend)

            health = logger.get_backend_health()

            assert "name" in health or "composite" in health
            logger.close()


class TestGlobalLogger:
    """Tests for global logger functions."""

    def test_get_audit_logger_singleton(self):
        """Test that get_audit_logger returns singleton."""
        # Reset singleton
        AuditLogger._instance = None

        logger1 = get_audit_logger()
        logger2 = get_audit_logger()

        assert logger1 is logger2

        # Cleanup
        AuditLogger._instance = None

    def test_log_config_change_function(self):
        """Test global log_config_change function."""
        # Reset and configure with temp backend
        AuditLogger._instance = None

        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            AuditLogger.configure(backend=backend, enable_console_log=False)

            result = log_config_change(
                config_type="GLOBAL_TEST",
                config_key="setting",
                old_value={"v": 1},
                new_value={"v": 2},
            )

            assert result is True

            get_audit_logger().close()
            AuditLogger._instance = None

    def test_configure_logger(self):
        """Test configuring global logger."""
        AuditLogger._instance = None

        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir)
            logger = AuditLogger.configure(
                backend=backend,
                mask_ip_addresses=False,
                sensitive_fields=["secret"],
            )

            assert logger is AuditLogger.get_instance()
            assert logger._mask_ip is False
            assert "secret" in logger._sensitive_fields

            logger.close()
            AuditLogger._instance = None


class TestAuditAction:
    """Tests for AuditAction enum."""

    def test_action_values(self):
        """Test action enum values."""
        assert AuditAction.CREATE.value == "create"
        assert AuditAction.UPDATE.value == "update"
        assert AuditAction.DELETE.value == "delete"
        assert AuditAction.READ.value == "read"
        assert AuditAction.APPLY.value == "apply"
        assert AuditAction.ROLLBACK.value == "rollback"
