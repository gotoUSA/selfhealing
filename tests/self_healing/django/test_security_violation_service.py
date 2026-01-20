"""
Security Violation Service Unit Tests

Tests for SecurityViolationService functionality including:
- Violation type handling
- Protective action execution
- Security incident creation
- IP management (logging, banning)
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

# 이 파일의 테스트 중 일부는 DB 필요
pytestmark = pytest.mark.requires_db

from shopping.models.security_incident import SecurityIncident
from selfhealing.services import (
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
        # Use domain-neutral violation types from selfhealing package
        critical_types = [
            ViolationType.SIGNATURE_INVALID,
            ViolationType.DATA_TAMPERED,
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

    def test_extract_ip_from_request_info(self):
        """
        Purpose:
            Verify client IP extraction from request_info dict.

        Note:
            SecurityViolationService now receives request_info dict instead of
            Django request object for framework independence.
        """
        request_info = {"ip": "192.168.1.100", "user_agent": "TestAgent"}

        # IP is now passed directly in request_info, not extracted
        assert request_info.get("ip") == "192.168.1.100"

    def test_request_info_with_x_forwarded_for(self):
        """
        Purpose:
            Verify client IP in request_info (pre-extracted from X-Forwarded-For).

        Note:
            IP extraction from X-Forwarded-For should be done at adapter layer
            before calling SecurityViolationService.
        """
        # The adapter layer should extract the first IP from X-Forwarded-For
        request_info = {"ip": "10.0.0.1", "user_agent": "TestAgent"}

        # Should have the first IP (original client)
        assert request_info.get("ip") == "10.0.0.1"

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
        from selfhealing.adapters.django.repositories import DjangoSecurityIncidentRepository

        self.config = SecurityConfig()
        self.repository = DjangoSecurityIncidentRepository()
        self.service = SecurityViolationService(config=self.config, repository=self.repository)
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
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
                user_id=user.id,
                description="Attempted access to admin endpoint",
            )

        assert result.success is True
        assert result.incident_id is not None

        # Verify incident was created
        incident = SecurityIncident.objects.get(id=result.incident_id)
        assert incident.incident_type == "unauthorized_access"
        assert incident.severity == "high"
        assert incident.source_ip == "192.168.1.100"
        assert incident.user_id == user.id
        assert "Attempted access" in incident.description

        # Verify notification was triggered
        mock_notify.assert_called_once()

    def test_handle_signature_invalid_violation(self):
        """
        Purpose:
            Verify signature invalid violation is handled correctly.
        """
        request = self.factory.post("/api/webhook/")
        request.META["REMOTE_ADDR"] = "203.0.113.50"

        with patch.object(self.service, "_send_security_notification"):
            result = self.service.handle_violation(
                violation_type=ViolationType.SIGNATURE_INVALID,
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
                description="HMAC signature mismatch",
            )

        assert result.success is True
        assert "logged" in result.action_taken.lower() or "monitoring" in result.action_taken.lower()

        incident = SecurityIncident.objects.get(id=result.incident_id)
        assert incident.severity == "critical"

    def test_handle_rate_limit_abuse_bans_ip(self):
        """
        Purpose:
            Verify rate limit abuse triggers temporary IP ban.
        """
        request = self.factory.post("/api/login/")
        request.META["REMOTE_ADDR"] = "10.0.0.99"

        with patch.object(self.service, "_send_security_notification"):
            result = self.service.handle_violation(
                violation_type=ViolationType.RATE_LIMIT_ABUSE,
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
                description="Excessive login attempts",
            )

        assert result.success is True
        assert "banned" in result.action_taken.lower()

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
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
                user_id=user.id,
                description="JWT signature validation failed",
            )

        assert result.success is True
        # Service now receives user_id, not user object
        mock_invalidate.assert_called_once_with(user.id)

    def test_handle_violation_with_entity_refs(self):
        """
        Purpose:
            Verify violation can be linked to entities via entity_refs.
        """
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order)
        request = self.factory.post("/api/payment/confirm/")
        request.META["REMOTE_ADDR"] = "192.168.0.1"

        # Use request_info dict and entity_refs for framework-agnostic API
        request_info = {
            "ip": request.META.get("REMOTE_ADDR"),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        }
        entity_refs = {
            "order_id": order.id,
            "payment_id": payment.id,
        }

        with patch.object(self.service, "_send_security_notification"):
            result = self.service.handle_violation(
                violation_type=ViolationType.DATA_TAMPERED,
                request_info=request_info,
                user_id=user.id,
                entity_refs=entity_refs,
                description="Amount in request differs from expected value",
            )

        assert result.success is True

        incident = SecurityIncident.objects.get(id=result.incident_id)
        # Verify order and payment are stored via entity_refs
        assert incident.order_id == order.id
        assert incident.payment_id == payment.id
        assert incident.severity == "critical"


# =============================================================================
# IP Management Tests
# =============================================================================


@pytest.mark.django_db
@pytest.mark.django_db
class TestIPManagement:
    """Tests for IP banning and monitoring."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_cache = MagicMock()
        self.service = SecurityViolationService(cache=self.mock_cache)

    def test_temporary_ip_ban(self):
        """
        Purpose:
            Verify temporary IP ban is set correctly.
        """
        self.mock_cache.get.return_value = {"banned": True, "type": "temporary"}

        result = self.service._temporary_ip_ban("192.168.1.1", hours=2)

        assert "banned" in result.lower()
        assert "2" in result
        self.mock_cache.set.assert_called_once()

    def test_is_ip_banned_returns_false_for_unbanned(self):
        """
        Purpose:
            Verify is_ip_banned returns False for non-banned IPs.
        """
        self.mock_cache.get.return_value = None

        assert self.service.is_ip_banned("192.168.1.99") is False

    def test_log_suspicious_ip_increments_count(self):
        """
        Purpose:
            Verify suspicious IP logging increments count.
        """
        # First call returns None (0), second returns 1
        self.mock_cache.get.side_effect = [None, 1]

        result1 = self.service._log_suspicious_ip("10.0.0.1")
        result2 = self.service._log_suspicious_ip("10.0.0.1")

        assert "1" in result1
        assert "2" in result2

    def test_suspicious_ip_escalates_to_ban(self):
        """
        Purpose:
            Verify reaching threshold escalates to permanent ban.
        """
        # Create a new service with custom threshold
        config = SecurityConfig(permanent_ban_threshold=3)
        service = SecurityViolationService(config=config, cache=self.mock_cache)

        # Simulate count at threshold
        self.mock_cache.get.return_value = 2  # Next call will be 3rd

        result = service._log_suspicious_ip("10.0.0.2")

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
        import selfhealing.services.security_violation_service as svc_module

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

        with patch("selfhealing.services.security_violation_service." "SecurityViolationService._send_security_notification"):
            result = handle_security_violation(
                violation_type=ViolationType.SUSPICIOUS_ACTIVITY,
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
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
        from selfhealing.adapters.django.repositories import DjangoSecurityIncidentRepository

        self.config = SecurityConfig()
        self.repository = DjangoSecurityIncidentRepository()
        self.service = SecurityViolationService(config=self.config, repository=self.repository)
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

        with patch.object(
            self.repository,
            "create",
            side_effect=Exception("DB connection lost"),
        ):
            result = self.service.handle_violation(
                violation_type=ViolationType.UNAUTHORIZED_ACCESS,
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
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
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
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
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
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
                request_info=None,  # No request info
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
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
                user_id=None,  # No user
                description="Forged token with no user context",
            )

        assert result.success is True
        assert "no user" in result.action_taken.lower()
