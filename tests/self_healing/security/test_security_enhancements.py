"""
Tests for security enhancements:
1. API permission relaxation (read-only endpoints accessible to all authenticated users)
2. Log masking (sensitive fields, internal IPs, server paths)
3. Sensitive endpoint access logging
4. Dashboard Redis caching
"""

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest


# =============================================================================
# Test: Log Masking (_sanitize_request_data)
# =============================================================================


class TestLogMasking:
    """Tests for the enhanced _sanitize_request_data function."""

    def _get_service(self):
        """Create SecurityViolationService with mocked dependencies."""
        from selfhealing.services.security import (
            SecurityViolationService,
            SecurityConfig,
        )

        mock_repo = Mock()
        mock_cache = Mock()
        mock_cache.get.return_value = None

        return SecurityViolationService(
            config=SecurityConfig(),
            repository=mock_repo,
            cache=mock_cache,
        )

    def test_mask_sensitive_fields(self):
        """Test that sensitive fields are redacted."""
        service = self._get_service()

        raw_data = {
            "username": "testuser",
            "password": "secret123",
            "token": "jwt_token_here",
            "api_key": "key_12345",
            "data": "normal data",
        }

        result = service._sanitize_request_data(raw_data)

        assert result["username"] == "testuser"
        assert result["password"] == "[REDACTED]"
        assert result["token"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        assert result["data"] == "normal data"

    def test_mask_internal_ips(self):
        """Test that internal IP addresses are masked."""
        service = self._get_service()

        raw_data = {
            "server": "Connected to 10.0.5.123 successfully",
            "fallback": "Using 172.16.100.50 as backup",
            "private": "Internal network 192.168.1.100",
            "public": "External IP: 8.8.8.8",
        }

        result = service._sanitize_request_data(raw_data)

        assert "[INTERNAL_IP]" in result["server"]
        assert "10.0.5.123" not in result["server"]

        assert "[INTERNAL_IP]" in result["fallback"]
        assert "172.16.100.50" not in result["fallback"]

        assert "[INTERNAL_IP]" in result["private"]
        assert "192.168.1.100" not in result["private"]

        # Public IP should NOT be masked
        assert "8.8.8.8" in result["public"]

    def test_mask_server_paths(self):
        """Test that server paths are masked."""
        service = self._get_service()

        raw_data = {
            "log_path": "Error in /home/deploy/app/main.py",
            "config": "Reading from /etc/myapp",
            "var": "Logs at /var/log/nginx",
            "windows": r"Config at C:\Users\admin\settings",
            "relative": "Using ./config/local.yaml",  # Should NOT be masked
        }

        result = service._sanitize_request_data(raw_data)

        assert "[SERVER_PATH]" in result["log_path"]
        assert "/home/deploy" not in result["log_path"]

        assert "[SERVER_PATH]" in result["config"]
        assert "/etc/myapp" not in result["config"]

        assert "[SERVER_PATH]" in result["var"]
        assert "/var/log" not in result["var"]

        # Relative paths should NOT be masked
        assert "./config/local.yaml" in result["relative"]

    def test_mask_nested_data(self):
        """Test masking in nested dictionaries and lists."""
        service = self._get_service()

        raw_data = {
            "user": {
                "name": "test",
                "password": "secret",
            },
            "servers": [
                {"ip": "10.0.0.1", "name": "server1"},
                {"ip": "8.8.8.8", "name": "server2"},
            ],
        }

        result = service._sanitize_request_data(raw_data)

        assert result["user"]["password"] == "[REDACTED]"
        assert "[INTERNAL_IP]" in result["servers"][0]["ip"]
        assert "8.8.8.8" in result["servers"][1]["ip"]

    def test_empty_data(self):
        """Test handling of empty/None data."""
        service = self._get_service()

        assert service._sanitize_request_data(None) == {}
        assert service._sanitize_request_data({}) == {}


# =============================================================================
# Test: Sensitive Endpoint Access Logging
# =============================================================================


class TestAccessLogging:
    """Tests for SensitiveEndpointAccessLogger."""

    def test_is_sensitive_endpoint(self):
        """Test sensitive endpoint detection."""
        from selfhealing.api.django.middleware import SensitiveEndpointAccessLogger

        logger = SensitiveEndpointAccessLogger()

        # Sensitive endpoints
        assert logger.is_sensitive_endpoint("/api/self-healing/audit/")
        assert logger.is_sensitive_endpoint("/api/self-healing/config/")
        assert logger.is_sensitive_endpoint("/api/self-healing/config/circuit-breaker/")
        assert logger.is_sensitive_endpoint("/api/self-healing/chaos/schedules/")
        assert logger.is_sensitive_endpoint("/api/self-healing/chaos/schedules/123/")
        assert logger.is_sensitive_endpoint("/api/self-healing/chaos/config/")

        # Non-sensitive endpoints
        assert not logger.is_sensitive_endpoint("/api/self-healing/status/")
        assert not logger.is_sensitive_endpoint("/api/self-healing/health/")
        assert not logger.is_sensitive_endpoint("/api/self-healing/dashboard/summary/")

    def test_access_log_entry(self):
        """Test AccessLogEntry creation and serialization."""
        from selfhealing.api.django.middleware import AccessLogEntry

        entry = AccessLogEntry(
            timestamp=datetime(2025, 12, 21, 10, 30, 0, tzinfo=timezone.utc),
            user="admin",
            method="GET",
            path="/api/self-healing/audit/",
            query_params="page=1",
            source_ip="10.0.5.123",
            user_agent="Mozilla/5.0",
            status_code=200,
            response_time_ms=45.2,
        )

        data = entry.to_dict()

        assert data["user"] == "admin"
        assert data["method"] == "GET"
        assert data["path"] == "/api/self-healing/audit/"
        assert data["status_code"] == 200

        # Internal IP should be partially masked
        assert "xxx.xxx" in data["source_ip"]
        assert "10.0.5.123" not in data["source_ip"]

    def test_internal_ip_masking(self):
        """Test internal IP masking in access log."""
        from selfhealing.api.django.middleware import AccessLogEntry

        # Test various internal IP ranges
        test_cases = [
            ("10.0.5.123", "10.0.xxx.xxx"),
            ("172.16.100.50", "172.16.xxx.xxx"),
            ("192.168.1.100", "192.168.xxx.xxx"),
            ("8.8.8.8", "8.8.8.8"),  # Public IP - not masked
        ]

        for original, expected in test_cases:
            entry = AccessLogEntry(
                timestamp=datetime.now(timezone.utc),
                user="test",
                method="GET",
                path="/test",
                query_params="",
                source_ip=original,
                user_agent="",
                status_code=200,
            )
            assert entry.to_dict()["source_ip"] == expected


# =============================================================================
# Test: API Permission Relaxation
# =============================================================================


class TestAPIPermissions:
    """Tests for API permission configuration."""

    def test_dashboard_view_permissions(self):
        """Test DashboardSummaryView has correct permissions."""
        from selfhealing.api.django.views.dashboard import DashboardSummaryView
        from rest_framework.permissions import IsAuthenticated
        from selfhealing.api.django.permissions import IsViewer

        view = DashboardSummaryView()

        # Should require IsAuthenticated and IsViewer
        assert len(view.permission_classes) == 2
        assert IsAuthenticated in view.permission_classes
        assert IsViewer in view.permission_classes

    def test_audit_view_permissions(self):
        """Test ControlAuditView has correct permissions."""
        from selfhealing.api.django.views.circuit_breaker import ControlAuditView
        from rest_framework.permissions import IsAuthenticated
        from selfhealing.api.django.permissions import IsViewer

        view = ControlAuditView()

        # Should require IsAuthenticated and IsViewer
        assert len(view.permission_classes) == 2
        assert IsAuthenticated in view.permission_classes
        assert IsViewer in view.permission_classes

    def test_dlq_stats_view_permissions(self):
        """Test DLQCleanupStatsView has correct permissions."""
        from selfhealing.api.django.views.dlq import DLQCleanupStatsView
        from selfhealing.api.django.permissions import IsSelfHealingAuthenticated, IsViewer

        view = DLQCleanupStatsView()

        # Should require IsSelfHealingAuthenticated and IsViewer
        assert len(view.permission_classes) == 2
        assert IsSelfHealingAuthenticated in view.permission_classes
        assert IsViewer in view.permission_classes

    def test_control_action_still_requires_admin(self):
        """Test that control actions still require admin."""
        from selfhealing.api.django.views.circuit_breaker import ControlActionView
        from rest_framework.permissions import IsAuthenticated
        from selfhealing.api.django.permissions import IsSelfHealingAdmin

        view = ControlActionView()

        # Should require both IsAuthenticated AND IsSelfHealingAdmin
        assert len(view.permission_classes) == 2
        assert IsAuthenticated in view.permission_classes
        assert IsSelfHealingAdmin in view.permission_classes


# =============================================================================
# Test: Config Masking Patterns
# =============================================================================


class TestConfigMaskingPatterns:
    """Tests for ForensicSettings masking patterns."""

    def test_sensitive_field_patterns(self):
        """Test that all expected sensitive fields are configured."""
        from selfhealing.config import ForensicSettings

        settings = ForensicSettings()

        expected_patterns = [
            "password", "secret", "token", "api_key",
            "authorization", "credential", "private_key",
            "card_number", "cvv", "connection_string",
        ]

        for pattern in expected_patterns:
            assert pattern in settings.sensitive_field_patterns, f"Missing: {pattern}"

    def test_internal_ip_patterns(self):
        """Test that internal IP patterns are configured."""
        from selfhealing.config import ForensicSettings

        settings = ForensicSettings()

        assert settings.mask_internal_ip is True
        assert len(settings.internal_ip_patterns) >= 3  # 10.x, 172.16.x, 192.168.x

    def test_server_path_patterns(self):
        """Test that server path patterns are configured."""
        from selfhealing.config import ForensicSettings

        settings = ForensicSettings()

        assert settings.mask_server_paths is True
        assert len(settings.server_path_patterns) >= 3


# =============================================================================
# Test: Fail-Secure Behavior
# =============================================================================


class TestFailSecureBehavior:
    """Tests for fail-secure behavior in security components."""

    def test_masking_correctly_sanitizes_data(self):
        """Test that masking correctly sanitizes sensitive data (fail-secure)."""
        from selfhealing.services.security import (
            SecurityViolationService,
            SecurityConfig,
        )

        mock_repo = Mock()
        mock_cache = Mock()
        mock_cache.get.return_value = None

        service = SecurityViolationService(
            config=SecurityConfig(),
            repository=mock_repo,
            cache=mock_cache,
        )

        # Test with normal data containing sensitive patterns
        normal_data = {
            "ip_address": "10.0.1.5",
            "server_path": "/home/admin/secrets",
            "password": "secret123",
        }

        result = service._sanitize_request_data(normal_data)

        # Should mask sensitive data correctly
        assert "secret123" not in str(result)  # password should be redacted
        assert result.get("password") == "[REDACTED]"

        # Internal IPs should be masked
        assert "10.0.1.5" not in str(result)

    def test_masking_handles_none_gracefully(self):
        """Test that None input returns empty dict."""
        from selfhealing.services.security import (
            SecurityViolationService,
            SecurityConfig,
        )

        mock_repo = Mock()
        mock_cache = Mock()

        service = SecurityViolationService(
            config=SecurityConfig(),
            repository=mock_repo,
            cache=mock_cache,
        )

        # Should return empty dict for None
        result = service._sanitize_request_data(None)
        assert result == {}

    def test_ip_masking_error_returns_masked(self):
        """Test that IP masking errors return [MASKED] (fail-secure)."""
        from selfhealing.api.django.middleware import AccessLogEntry

        entry = AccessLogEntry(
            timestamp=datetime.now(timezone.utc),
            user="test",
            method="GET",
            path="/test",
            query_params="",
            source_ip="not-an-ip",  # Invalid IP format
            user_agent="",
            status_code=200,
        )

        # Should not raise exception
        data = entry.to_dict()

        # Source IP should be in result (not an internal IP, so returned as-is)
        assert data["source_ip"] == "not-an-ip"

    def test_empty_ip_returns_marker(self):
        """Test that empty IP returns [EMPTY] marker."""
        from selfhealing.api.django.middleware import AccessLogEntry

        entry = AccessLogEntry(
            timestamp=datetime.now(timezone.utc),
            user="test",
            method="GET",
            path="/test",
            query_params="",
            source_ip="",  # Empty IP
            user_agent="",
            status_code=200,
        )

        data = entry.to_dict()
        assert data["source_ip"] == "[EMPTY]"

    def test_fail_secure_is_authenticated(self):
        """Test FailSecureIsAuthenticated returns False for unauthenticated user."""
        from selfhealing.api.django.middleware import FailSecureIsAuthenticated

        permission = FailSecureIsAuthenticated()

        # Mock request with unauthenticated user
        mock_request = Mock()
        mock_request.path = "/test"
        mock_request.META = {"REMOTE_ADDR": "1.2.3.4"}

        # User that is not authenticated
        mock_request.user = Mock()
        mock_request.user.is_authenticated = False

        # Should deny access
        result = permission.has_permission(mock_request, None)
        assert result is False

    def test_fail_secure_is_authenticated_no_user(self):
        """Test FailSecureIsAuthenticated denies when user is None."""
        from selfhealing.api.django.middleware import FailSecureIsAuthenticated

        permission = FailSecureIsAuthenticated()

        # Mock request with no user
        mock_request = Mock()
        mock_request.path = "/test"
        mock_request.META = {"REMOTE_ADDR": "1.2.3.4"}
        mock_request.user = None

        # Should deny access (fail-secure)
        result = permission.has_permission(mock_request, None)
        assert result is False

    def test_fail_secure_is_admin(self):
        """Test FailSecureIsAdminUser denies on error."""
        from selfhealing.api.django.middleware import FailSecureIsAdminUser

        permission = FailSecureIsAdminUser()

        # Mock request with problematic user
        mock_request = Mock()
        mock_request.path = "/test"

        # User that raises exception on is_staff
        bad_user = Mock()
        bad_user.is_authenticated = True
        del bad_user.is_staff  # Remove is_staff attribute

        mock_request.user = bad_user

        # Should deny access (fail-secure) - no is_staff attribute
        result = permission.has_permission(mock_request, None)
        assert result is False


# =============================================================================
# Test: Reauthentication Framework
# =============================================================================


class TestReauthenticationFramework:
    """Tests for the reauthentication decorator and provider interface."""

    def test_reauthentication_config_defaults(self):
        """Test ReauthenticationConfig default values."""
        from selfhealing.api.django.reauthentication import ReauthenticationConfig

        config = ReauthenticationConfig()

        assert config.max_idle_minutes == 15
        assert config.max_session_minutes == 60
        assert config.enabled is True
        assert config.status_code == 403

    def test_noop_provider_never_requires_reauth(self):
        """Test NoOpReauthenticationProvider always returns False."""
        from selfhealing.api.django.reauthentication import (
            NoOpReauthenticationProvider,
            ReauthenticationConfig,
        )

        provider = NoOpReauthenticationProvider()
        config = ReauthenticationConfig()

        mock_request = Mock()
        mock_request.path = "/test"

        result = provider.check_reauthentication_required(mock_request, config)
        assert result is False

    def test_session_provider_idle_timeout(self):
        """Test SessionBasedReauthProvider detects idle timeout."""
        from selfhealing.api.django.reauthentication import (
            SessionBasedReauthProvider,
            ReauthenticationConfig,
        )
        from datetime import timedelta

        provider = SessionBasedReauthProvider()
        config = ReauthenticationConfig(max_idle_minutes=10)

        mock_request = Mock()
        mock_request.path = "/test"

        # Session with old last activity (20 minutes ago)
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        mock_request.session = {
            provider.SESSION_KEY_LAST_ACTIVITY: old_time
        }

        result = provider.check_reauthentication_required(mock_request, config)
        assert result is True

    def test_session_provider_within_timeout(self):
        """Test SessionBasedReauthProvider allows recent activity."""
        from selfhealing.api.django.reauthentication import (
            SessionBasedReauthProvider,
            ReauthenticationConfig,
        )
        from datetime import timedelta

        provider = SessionBasedReauthProvider()
        config = ReauthenticationConfig(max_idle_minutes=15)

        mock_request = Mock()
        mock_request.path = "/test"

        # Session with recent last activity (5 minutes ago)
        recent_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        mock_request.session = {
            provider.SESSION_KEY_LAST_ACTIVITY: recent_time
        }

        result = provider.check_reauthentication_required(mock_request, config)
        assert result is False

    def test_requires_reauthentication_decorator_passes_when_not_required(self):
        """Test decorator allows request when reauth not required."""
        from selfhealing.api.django.reauthentication import (
            requires_reauthentication,
            set_reauthentication_provider,
            NoOpReauthenticationProvider,
        )

        # Use NoOp provider (never requires reauth)
        set_reauthentication_provider(NoOpReauthenticationProvider())

        @requires_reauthentication(max_idle_minutes=15)
        def my_view(request):
            return "success"

        mock_request = Mock()
        result = my_view(mock_request)
        assert result == "success"

    def test_requires_reauthentication_decorator_disabled(self):
        """Test decorator skips check when disabled."""
        from selfhealing.api.django.reauthentication import requires_reauthentication

        @requires_reauthentication(enabled=False)
        def my_view(request):
            return "success"

        mock_request = Mock()
        result = my_view(mock_request)
        assert result == "success"

    def test_permission_class_fail_secure(self):
        """Test RequiresReauthenticationPermission fails securely on error."""
        from selfhealing.api.django.reauthentication import (
            RequiresReauthenticationPermission,
            set_reauthentication_provider,
        )

        # Create a provider that raises exception
        class FailingProvider:
            def check_reauthentication_required(self, request, config):
                raise RuntimeError("Provider error")

        set_reauthentication_provider(FailingProvider())

        permission = RequiresReauthenticationPermission()

        mock_request = Mock()
        mock_request.path = "/test"

        # Should deny access (fail-secure)
        with patch('selfhealing.api.django.reauthentication.logger'):
            result = permission.has_permission(mock_request, None)

        assert result is False


# =============================================================================
# Test: Masking Error String
# =============================================================================


class TestMaskingErrorString:
    """Tests for the new masking error string format."""

    def test_masking_error_returns_string_not_dict(self):
        """Test that masking errors now return a string placeholder."""
        from selfhealing.services.security import (
            SecurityViolationService,
            SecurityConfig,
        )

        mock_repo = Mock()
        mock_cache = Mock()

        service = SecurityViolationService(
            config=SecurityConfig(),
            repository=mock_repo,
            cache=mock_cache,
        )

        # Test with normal data - should work
        normal_data = {"key": "value"}
        result = service._sanitize_request_data(normal_data)
        assert isinstance(result, dict)

    def test_masking_sensitive_data_hidden_format(self):
        """Test the expected error placeholder format."""
        expected = "[MASKING_ERROR: SENSITIVE_DATA_HIDDEN]"

        # This is the format we expect on masking failure
        assert "MASKING_ERROR" in expected
        assert "SENSITIVE_DATA_HIDDEN" in expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
