"""
Tests for audit backends.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from selfhealing.audit.backends import (
    AuditBackend,
    BackendHealth,
    BackendStatus,
    CloudWatchBackend,
    CompositeBackend,
    DatadogBackend,
    LocalFileBackend,
    RemoteAuditBackend,
    S3WORMBackend,
    create_composite_backend,
    get_default_backend,
)


class TestLocalFileBackend:
    """Tests for LocalFileBackend."""

    def test_write_creates_file(self):
        """Test that write creates log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(
                log_dir=tmpdir,
                enable_hash_chain=False,
            )

            entry = {"event": "test", "timestamp": datetime.now(timezone.utc).isoformat()}
            result = backend.write(entry)

            assert result is True

            # Check file was created
            log_files = list(Path(tmpdir).glob("audit_*.jsonl"))
            assert len(log_files) > 0

            backend.close()

    def test_write_appends_json_line(self):
        """Test that entries are written as JSON lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(
                log_dir=tmpdir,
                enable_hash_chain=False,
            )

            entries = [{"event": f"event_{i}"} for i in range(3)]
            for entry in entries:
                backend.write(entry)

            backend.close()

            # Read back and verify
            log_files = list(Path(tmpdir).glob("audit_*.jsonl"))
            with open(log_files[0]) as f:
                lines = f.readlines()

            assert len(lines) == 3
            for i, line in enumerate(lines):
                data = json.loads(line)
                assert data["event"] == f"event_{i}"

    def test_write_with_hash_chain(self):
        """Test that hash chain is added to entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(
                log_dir=tmpdir,
                enable_hash_chain=True,
            )

            entry = {"event": "test"}
            backend.write(entry)
            backend.close()

            # Read back and verify hash chain
            log_files = list(Path(tmpdir).glob("audit_*.jsonl"))
            with open(log_files[0]) as f:
                data = json.loads(f.readline())

            assert "integrity" in data
            assert "sequence" in data["integrity"]
            assert "current_hash" in data["integrity"]

    def test_health_check_success(self):
        """Test health check when directory is writable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(log_dir=tmpdir)

            health = backend.health_check()

            assert health.status == BackendStatus.ACTIVE
            assert "operational" in health.message.lower()

    def test_query_by_config_type(self):
        """Test querying logs by config type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(
                log_dir=tmpdir,
                enable_hash_chain=False,
            )

            # Write entries with different config types
            entries = [
                {"change": {"config_type": "RETRY_CONFIG"}, "event": "1"},
                {"change": {"config_type": "CIRCUIT_BREAKER"}, "event": "2"},
                {"change": {"config_type": "RETRY_CONFIG"}, "event": "3"},
            ]
            for entry in entries:
                backend.write(entry)

            backend.close()

            # Query specific type
            results = backend.query(config_type="RETRY_CONFIG", limit=10)

            assert len(results) == 2
            for r in results:
                assert r["change"]["config_type"] == "RETRY_CONFIG"

    def test_verify_integrity(self):
        """Test integrity verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = LocalFileBackend(
                log_dir=tmpdir,
                enable_hash_chain=True,
            )

            # Write some entries
            for i in range(5):
                backend.write({"event": f"event_{i}"})

            backend.close()

            # Verify integrity
            is_valid, issues = backend.verify_integrity()

            assert is_valid
            assert len(issues) == 0


class TestCloudWatchBackend:
    """Tests for CloudWatchBackend (interface only)."""

    def test_initialization(self):
        """Test backend initialization."""
        backend = CloudWatchBackend(
            log_group="/test/audit",
            region="us-east-1",
        )

        assert backend.name == "CloudWatch"

    def test_write_returns_true_when_not_enabled(self):
        """Test that write returns True when not enabled (no-op)."""
        backend = CloudWatchBackend()

        result = backend.write({"event": "test"})

        assert result is True

    def test_health_check_not_configured(self):
        """Test health check shows not configured."""
        backend = CloudWatchBackend()

        health = backend.health_check()

        assert health.status == BackendStatus.NOT_CONFIGURED

    def test_get_configuration_template(self):
        """Test getting configuration template."""
        backend = CloudWatchBackend()

        template = backend.get_configuration_template()

        assert "required_packages" in template
        assert "boto3" in template["required_packages"]
        assert "environment_variables" in template
        assert "iam_policy" in template


class TestDatadogBackend:
    """Tests for DatadogBackend (interface only)."""

    def test_initialization(self):
        """Test backend initialization."""
        backend = DatadogBackend(
            service="my-service",
            site="datadoghq.eu",
        )

        assert backend.name == "Datadog"

    def test_write_returns_true_when_not_enabled(self):
        """Test that write returns True when not enabled."""
        backend = DatadogBackend()

        result = backend.write({"event": "test"})

        assert result is True

    def test_health_check_not_configured(self):
        """Test health check shows not configured."""
        backend = DatadogBackend()

        health = backend.health_check()

        assert health.status == BackendStatus.NOT_CONFIGURED


class TestS3WORMBackend:
    """Tests for S3WORMBackend (interface only)."""

    def test_initialization(self):
        """Test backend initialization."""
        backend = S3WORMBackend(
            bucket="my-audit-bucket",
            retention_days=730,
        )

        assert backend.name == "S3WORM"

    def test_write_returns_true_when_not_enabled(self):
        """Test that write returns True when not enabled."""
        backend = S3WORMBackend()

        result = backend.write({"event": "test"})

        assert result is True

    def test_get_configuration_template_includes_terraform(self):
        """Test that config template includes Terraform example."""
        backend = S3WORMBackend()

        template = backend.get_configuration_template()

        assert "terraform_example" in template
        assert "object_lock" in template["terraform_example"].lower()


class TestRemoteAuditBackend:
    """Tests for RemoteAuditBackend (interface only)."""

    def test_initialization(self):
        """Test backend initialization."""
        backend = RemoteAuditBackend(
            server_url="https://audit.example.com:8443",
        )

        assert backend.name == "RemoteAudit"

    def test_write_returns_true_when_not_enabled(self):
        """Test that write returns True when not enabled."""
        backend = RemoteAuditBackend()

        result = backend.write({"event": "test"})

        assert result is True

    def test_get_configuration_template_includes_security(self):
        """Test that config template includes security considerations."""
        backend = RemoteAuditBackend()

        template = backend.get_configuration_template()

        assert "security_considerations" in template
        assert len(template["security_considerations"]) > 0


class TestCompositeBackend:
    """Tests for CompositeBackend."""

    def test_write_to_multiple_backends(self):
        """Test writing to multiple backends."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                backend1 = LocalFileBackend(log_dir=tmpdir1, enable_hash_chain=False)
                backend2 = LocalFileBackend(log_dir=tmpdir2, enable_hash_chain=False)

                composite = CompositeBackend([backend1, backend2])

                result = composite.write({"event": "test"})

                assert result is True

                composite.close()

                # Verify both have the entry
                files1 = list(Path(tmpdir1).glob("audit_*.jsonl"))
                files2 = list(Path(tmpdir2).glob("audit_*.jsonl"))

                assert len(files1) > 0
                assert len(files2) > 0

    def test_write_continues_on_partial_failure(self):
        """Test that write continues even if one backend fails."""

        class FailingBackend(AuditBackend):
            @property
            def name(self):
                return "Failing"

            def write(self, entry):
                raise Exception("Intentional failure")

            def health_check(self):
                return BackendHealth(status=BackendStatus.UNAVAILABLE)

            def query(self, **kwargs):
                return []

        with tempfile.TemporaryDirectory() as tmpdir:
            working_backend = LocalFileBackend(log_dir=tmpdir, enable_hash_chain=False)
            failing_backend = FailingBackend()

            composite = CompositeBackend([failing_backend, working_backend])

            # Should still return True if at least one succeeds
            result = composite.write({"event": "test"})

            composite.close()

            # Working backend should have the entry
            files = list(Path(tmpdir).glob("audit_*.jsonl"))
            assert len(files) > 0


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_get_default_backend(self):
        """Test getting default backend."""
        backend = get_default_backend()

        assert isinstance(backend, LocalFileBackend)

    def test_create_composite_backend_local_only(self):
        """Test creating composite with local only."""
        backend = create_composite_backend(local=True)

        assert isinstance(backend, CompositeBackend)

    def test_create_composite_backend_with_all(self):
        """Test creating composite with all backends."""
        backend = create_composite_backend(
            local=True,
            cloudwatch=True,
            datadog=True,
            s3_worm=True,
            remote=True,
        )

        assert isinstance(backend, CompositeBackend)
        # Should have 5 backends
        assert len(backend._backends) == 5
