"""
Security Violation Service Unit Tests

Tests for SecurityViolationService functionality including:
- Violation type handling
- Protective action execution
- Security incident creation
- IP management (logging, banning)

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §5 (Security Violation Handling)
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from shopping.models.security_incident import SecurityIncident
from shopping.services.self_healing.security_violation_service import (
    SecurityConfig,
    SecurityViolationResult,
    SecurityViolationService,
    Severity,
    ViolationType,
    SEVERITY_BY_VIOLATION_TYPE,
    get_security_violation_service,
    handle_security_violation,
)
from shopping.tests.factories import UserFactory, OrderFactory, PaymentFactory


# =============================================================================
# Configuration Tests
# =============================================================================


class TestSecurityConfig:
    """Tests for SecurityConfig dataclass."""

    def test_default_values(self):
        """
        Purpose:
            Verify default configuration values are correct.
        """
        config = SecurityConfig()

        assert config.rate_limit_window_seconds == 60
        assert config.rate_limit_max_requests == 100
        assert config.temporary_ban_hours == 1
        assert config.permanent_ban_threshold == 5
        assert config.failed_login_threshold == 5

    def test_custom_values(self):
        """
        Purpose:
            Verify custom configuration values are applied.
        """
        config = SecurityConfig(
            rate_limit_window_seconds=120,
            rate_limit_max_requests=50,
            temporary_ban_hours=2,
            permanent_ban_threshold=10,
        )

        assert config.rate_limit_window_seconds == 120
        assert config.rate_limit_max_requests == 50
        assert config.temporary_ban_hours == 2
        assert config.permanent_ban_threshold == 10


class TestSecurityViolationResult:
    """Tests for SecurityViolationResult dataclass."""

    def test_handled_factory(self):
        """
        Purpose:
            Verify handled factory creates correct result.
        """
        result = SecurityViolationResult.handled(
            incident_id=123,
            action="IP banned",
        )

        assert result.success is True
        assert result.incident_id == 123
        assert result.action_taken == "IP banned"
        assert result.error is None

    def test_failed_factory(self):
        """
        Purpose:
            Verify failed factory creates correct result.
        """
        result = SecurityViolationResult.failed("Database error")

        assert result.success is False
        assert result.incident_id is None
        assert result.error == "Database error"


# =============================================================================
# Severity Mapping Tests
# =============================================================================


class TestSeverityMapping:
    """Tests for violation type to severity mapping."""

    def test_critical_violations(self):
        """
        Purpose:
            Verify critical violation types are mapped correctly.
        """
        critical_types = [
            ViolationType.WEBHOOK_SIGNATURE_INVALID,
            ViolationType.PAYMENT_AMOUNT_TAMPERED,
            ViolationType.TOKEN_FORGED,
            ViolationType.REPLAY_ATTACK,
        ]

        for vtype in critical_types:
            assert SEVERITY_BY_VIOLATION_TYPE[vtype] == Severity.CRITICAL

    def test_high_violations(self):
        """
        Purpose:
            Verify high severity violation types are mapped correctly.
        """
        high_types = [
            ViolationType.UNAUTHORIZED_ACCESS,
            ViolationType.INJECTION_ATTEMPT,
        ]

        for vtype in high_types:
            assert SEVERITY_BY_VIOLATION_TYPE[vtype] == Severity.HIGH

    def test_medium_violations(self):
        """
        Purpose:
            Verify medium severity violation types are mapped correctly.
        """
        medium_types = [
            ViolationType.RATE_LIMIT_ABUSE,
            ViolationType.SUSPICIOUS_ACTIVITY,
        ]

        for vtype in medium_types:
            assert SEVERITY_BY_VIOLATION_TYPE[vtype] == Severity.MEDIUM


# =============================================================================
# Service Unit Tests
# =============================================================================


class TestSecurityViolationServiceUnit:
    """Unit tests for SecurityViolationService (no DB required)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = SecurityConfig()
        self.service = SecurityViolationService(config=self.config)
        self.factory = RequestFactory()

    def test_get_client_ip_direct(self):
        """
        Purpose:
            Verify client IP extraction from REMOTE_ADDR.
        """
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "192.168.1.100"

        ip = self.service._get_client_ip(request)

        assert ip == "192.168.1.100"

    def test_get_client_ip_from_x_forwarded_for(self):
        """
        Purpose:
            Verify client IP extraction from X-Forwarded-For header.
        """
        request = self.factory.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "10.0.0.1, 10.0.0.2, 10.0.0.3"

        ip = self.service._get_client_ip(request)

        # Should return the first IP (original client)
        assert ip == "10.0.0.1"

    def test_sanitize_request_data_removes_sensitive_fields(self):
        """
        Purpose:
            Verify sensitive fields are redacted from request data.
        """
        raw_data = {
            "username": "testuser",
            "password": "secret123",
            "token": "abc123",
            "data": {
                "nested_password": "hidden",
                "safe_field": "visible",
            },
        }

        sanitized = self.service._sanitize_request_data(raw_data)

        assert sanitized["username"] == "testuser"
        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["token"] == "[REDACTED]"
        assert sanitized["data"]["safe_field"] == "visible"

    def test_sanitize_request_data_handles_none(self):
        """
        Purpose:
            Verify None input returns empty dict.
        """
        result = self.service._sanitize_request_data(None)

        assert result == {}

    def test_sanitize_request_data_nested_list_with_dict(self):
        """
        Purpose:
            Verify nested list containing dicts with sensitive fields are sanitized.
            Critical for PCI-DSS compliance - card numbers in lists must be redacted.
        """
        raw_data = {
            "transactions": [
                {"id": 1, "card_number": "4111111111111111", "amount": 1000},
                {"id": 2, "cvv": "123", "token": "secret_token"},
            ],
            "metadata": {
                "items": [
                    {"name": "Product", "password": "hidden"},
                ]
            },
        }

        sanitized = self.service._sanitize_request_data(raw_data)

        # Verify nested list items are sanitized
        assert sanitized["transactions"][0]["id"] == 1
        assert sanitized["transactions"][0]["card_number"] == "[REDACTED]"
        assert sanitized["transactions"][1]["cvv"] == "[REDACTED]"
        assert sanitized["transactions"][1]["token"] == "[REDACTED]"
        assert sanitized["metadata"]["items"][0]["password"] == "[REDACTED]"
        assert sanitized["metadata"]["items"][0]["name"] == "Product"

    def test_sanitize_request_data_deeply_nested(self):
        """
        Purpose:
            Verify deeply nested structures are fully sanitized.
        """
        raw_data = {"level1": {"level2": {"level3": [{"api_key": "secret", "data": "visible"}]}}}

        sanitized = self.service._sanitize_request_data(raw_data)

        assert sanitized["level1"]["level2"]["level3"][0]["api_key"] == "[REDACTED]"
        assert sanitized["level1"]["level2"]["level3"][0]["data"] == "visible"


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.django_db
class TestSecurityViolationServiceIntegration:
    """Integration tests for SecurityViolationService (requires DB)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = SecurityConfig()
        self.service = SecurityViolationService(config=self.config)
        self.factory = RequestFactory()

    def test_handle_violation_creates_incident(self):
        """
        Purpose:
            Verify violation handling creates a SecurityIncident record.
        """
        user = UserFactory()
        request = self.factory.post("/api/payment/")
        request.META["REMOTE_ADDR"] = "192.168.1.100"
        request.META["HTTP_USER_AGENT"] = "TestAgent/1.0"

        with patch.object(self.service, "_send_security_notification") as mock_notify:
            result = self.service.handle_violation(
                violation_type=ViolationType.UNAUTHORIZED_ACCESS,
                request=request,
                user=user,
                description="Attempted access to admin endpoint",
            )

        assert result.success is True
        assert result.incident_id is not None

        # Verify incident was created
        incident = SecurityIncident.objects.get(id=result.incident_id)
        assert incident.incident_type == "unauthorized_access"
        assert incident.severity == "high"
        assert incident.source_ip == "192.168.1.100"
        assert incident.user == user
        assert "Attempted access" in incident.description

        # Verify notification was triggered
        mock_notify.assert_called_once()

    def test_handle_webhook_signature_violation(self):
        """
        Purpose:
            Verify webhook signature violation is handled correctly.
        """
        request = self.factory.post("/api/webhook/")
        request.META["REMOTE_ADDR"] = "203.0.113.50"

        with patch.object(self.service, "_send_security_notification"):
            result = self.service.handle_violation(
                violation_type=ViolationType.WEBHOOK_SIGNATURE_INVALID,
                request=request,
                description="HMAC signature mismatch",
            )

        assert result.success is True
        assert "logged" in result.action_taken.lower() or "monitoring" in result.action_taken.lower()

        incident = SecurityIncident.objects.get(id=result.incident_id)
        assert incident.severity == "critical"

    @patch("shopping.services.self_healing.security_violation_service.cache")
    def test_handle_rate_limit_abuse_bans_ip(self, mock_cache):
        """
        Purpose:
            Verify rate limit abuse triggers temporary IP ban.
        """
        mock_cache.get.return_value = None
        request = self.factory.post("/api/login/")
        request.META["REMOTE_ADDR"] = "10.0.0.99"

        with patch.object(self.service, "_send_security_notification"):
            result = self.service.handle_violation(
                violation_type=ViolationType.RATE_LIMIT_ABUSE,
                request=request,
                description="Excessive login attempts",
            )

        assert result.success is True
        assert "banned" in result.action_taken.lower()

        # Verify cache.set was called for banning
        mock_cache.set.assert_called()

    def test_handle_token_forged_invalidates_sessions(self):
        """
        Purpose:
            Verify forged token violation invalidates user sessions.
        """
        user = UserFactory()
        request = self.factory.get("/api/protected/")
        request.META["REMOTE_ADDR"] = "172.16.0.1"

        with (
            patch.object(self.service, "_send_security_notification"),
            patch.object(self.service, "_invalidate_user_sessions", return_value="Sessions invalidated") as mock_invalidate,
        ):
            result = self.service.handle_violation(
                violation_type=ViolationType.TOKEN_FORGED,
                request=request,
                user=user,
                description="JWT signature validation failed",
            )

        assert result.success is True
        mock_invalidate.assert_called_once_with(user)

    def test_handle_violation_with_order_and_payment(self):
        """
        Purpose:
            Verify violation can be linked to order and payment.
        """
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order)
        request = self.factory.post("/api/payment/confirm/")
        request.META["REMOTE_ADDR"] = "192.168.0.1"

        with patch.object(self.service, "_send_security_notification"):
            result = self.service.handle_violation(
                violation_type=ViolationType.PAYMENT_AMOUNT_TAMPERED,
                request=request,
                user=user,
                order=order,
                payment=payment,
                description="Amount in request differs from PG response",
            )

        assert result.success is True

        incident = SecurityIncident.objects.get(id=result.incident_id)
        assert incident.order == order
        assert incident.payment == payment
        assert incident.severity == "critical"


# =============================================================================
# IP Management Tests
# =============================================================================


@pytest.mark.django_db
class TestIPManagement:
    """Tests for IP banning and monitoring."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = SecurityViolationService()

    @patch("shopping.services.self_healing.security_violation_service.cache")
    def test_temporary_ip_ban(self, mock_cache):
        """
        Purpose:
            Verify temporary IP ban is set correctly.
        """
        mock_cache.get.return_value = {"banned": True, "type": "temporary"}

        result = self.service._temporary_ip_ban("192.168.1.1", hours=2)

        assert "banned" in result.lower()
        assert "2" in result
        mock_cache.set.assert_called_once()

    @patch("shopping.services.self_healing.security_violation_service.cache")
    def test_is_ip_banned_returns_false_for_unbanned(self, mock_cache):
        """
        Purpose:
            Verify is_ip_banned returns False for non-banned IPs.
        """
        mock_cache.get.return_value = None

        assert self.service.is_ip_banned("192.168.1.99") is False

    @patch("shopping.services.self_healing.security_violation_service.cache")
    def test_log_suspicious_ip_increments_count(self, mock_cache):
        """
        Purpose:
            Verify suspicious IP logging increments count.
        """
        # First call returns 0, second returns 1
        mock_cache.get.side_effect = [0, 1]

        result1 = self.service._log_suspicious_ip("10.0.0.1")
        result2 = self.service._log_suspicious_ip("10.0.0.1")

        assert "1" in result1
        assert "2" in result2

    @patch("shopping.services.self_healing.security_violation_service.cache")
    def test_suspicious_ip_escalates_to_ban(self, mock_cache):
        """
        Purpose:
            Verify reaching threshold escalates to permanent ban.
        """
        # Set low threshold for test
        self.service.config.permanent_ban_threshold = 3

        # Simulate count at threshold
        mock_cache.get.return_value = 2  # Next call will be 3rd

        result = self.service._log_suspicious_ip("10.0.0.2")

        assert "permanent" in result.lower()


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_get_security_violation_service_returns_singleton(self):
        """
        Purpose:
            Verify get_security_violation_service returns same instance.
        """
        # Reset singleton
        import shopping.services.self_healing.security_violation_service as svc_module

        svc_module._security_service = None

        service1 = get_security_violation_service()
        service2 = get_security_violation_service()

        assert service1 is service2

    @pytest.mark.django_db
    def test_handle_security_violation_convenience_function(self):
        """
        Purpose:
            Verify convenience function works correctly.
        """
        factory = RequestFactory()
        request = factory.get("/api/test/")
        request.META["REMOTE_ADDR"] = "127.0.0.1"

        with patch(
            "shopping.services.self_healing.security_violation_service." "SecurityViolationService._send_security_notification"
        ):
            result = handle_security_violation(
                violation_type=ViolationType.SUSPICIOUS_ACTIVITY,
                request=request,
                description="Test violation",
            )

        assert result.success is True
        assert result.incident_id is not None


# =============================================================================
# Transaction Rollback Tests
# =============================================================================


@pytest.mark.django_db
class TestTransactionRollback:
    """Tests for database transaction integrity."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = SecurityConfig()
        self.service = SecurityViolationService(config=self.config)
        self.factory = RequestFactory()

    def test_db_error_rolls_back_transaction(self):
        """
        Purpose:
            Verify that DB errors during incident creation cause rollback.
            No partial records should exist after failure.
        """
        initial_count = SecurityIncident.objects.count()
        request = self.factory.post("/api/test/")
        request.META["REMOTE_ADDR"] = "1.2.3.4"

        with patch(
            "shopping.models.security_incident.SecurityIncident.create_incident",
            side_effect=Exception("DB connection lost"),
        ):
            result = self.service.handle_violation(
                violation_type=ViolationType.UNAUTHORIZED_ACCESS,
                request=request,
                description="Test violation",
            )

        assert result.success is False
        assert "DB connection lost" in result.error
        # Verify no incident was created
        assert SecurityIncident.objects.count() == initial_count

    def test_notification_failure_does_not_rollback_incident(self):
        """
        Purpose:
            Verify that notification failure does not rollback the incident.
            Incident creation should succeed even if notification fails.
        """
        request = self.factory.post("/api/test/")
        request.META["REMOTE_ADDR"] = "5.6.7.8"

        with patch.object(
            self.service,
            "_send_security_notification",
            side_effect=Exception("Notification service down"),
        ):
            result = self.service.handle_violation(
                violation_type=ViolationType.SUSPICIOUS_ACTIVITY,
                request=request,
                description="Test - notification should fail",
            )

        # Incident should still be created successfully
        assert result.success is True
        assert result.incident_id is not None

        # Verify incident exists
        incident = SecurityIncident.objects.get(id=result.incident_id)
        assert incident is not None


# =============================================================================
# Unknown Violation Type Tests
# =============================================================================


@pytest.mark.django_db
class TestUnknownViolationType:
    """Tests for handling unknown violation types."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = SecurityViolationService()
        self.factory = RequestFactory()

    def test_unknown_violation_type_handled_gracefully(self):
        """
        Purpose:
            Verify unknown violation types fall through to default handling.
        """
        request = self.factory.post("/api/test/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"

        with patch.object(self.service, "_send_security_notification"):
            result = self.service.handle_violation(
                violation_type="unknown_custom_violation",
                request=request,
                description="Some unknown violation type",
            )

        assert result.success is True
        assert "logged for review" in result.action_taken.lower()

        incident = SecurityIncident.objects.get(id=result.incident_id)
        # Unknown types default to medium severity
        assert incident.severity == "medium"

    def test_replay_attack_without_ip(self):
        """
        Purpose:
            Verify replay attack handling when no IP is available.
        """
        with patch.object(self.service, "_send_security_notification"):
            result = self.service.handle_violation(
                violation_type=ViolationType.REPLAY_ATTACK,
                request=None,  # No request
                description="Replay attack detected via other means",
            )

        assert result.success is True
        assert "blocked" in result.action_taken.lower()

    def test_token_forged_without_user(self):
        """
        Purpose:
            Verify token forged handling when no user is associated.
        """
        request = self.factory.get("/api/test/")
        request.META["REMOTE_ADDR"] = "192.168.1.1"

        with patch.object(self.service, "_send_security_notification"):
            result = self.service.handle_violation(
                violation_type=ViolationType.TOKEN_FORGED,
                request=request,
                user=None,  # No user
                description="Forged token with no user context",
            )

        assert result.success is True
        assert "no user" in result.action_taken.lower()
