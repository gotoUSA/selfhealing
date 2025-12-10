"""
Security Violation Handling Service

Handles security violations that should NEVER self-heal.
Security incidents are immediately blocked and routed to the security team.

Features:
- Detect and classify security violations
- Take immediate protective actions
- Create SecurityIncident records
- Trigger security notifications

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §5 (Security Violation Handling)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from django.http import HttpRequest

    from shopping.models.order import Order
    from shopping.models.payment import Payment
    from shopping.models.security_incident import SecurityIncident
    from shopping.models.user import User
    from selfhealing.interfaces.repositories import (
        SecurityIncidentRepository,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Constants and Configuration
# =============================================================================


class ViolationType(str, Enum):
    """Types of security violations that never self-heal."""

    WEBHOOK_SIGNATURE_INVALID = "webhook_signature_invalid"
    PAYMENT_AMOUNT_TAMPERED = "payment_amount_tampered"
    TOKEN_FORGED = "token_forged"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_ABUSE = "rate_limit_abuse"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    REPLAY_ATTACK = "replay_attack"
    INJECTION_ATTEMPT = "injection_attempt"


class Severity(str, Enum):
    """Severity levels for security incidents."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


# Severity mapping for each violation type
SEVERITY_BY_VIOLATION_TYPE: dict[str, Severity] = {
    ViolationType.WEBHOOK_SIGNATURE_INVALID: Severity.CRITICAL,
    ViolationType.PAYMENT_AMOUNT_TAMPERED: Severity.CRITICAL,
    ViolationType.TOKEN_FORGED: Severity.CRITICAL,
    ViolationType.REPLAY_ATTACK: Severity.CRITICAL,
    ViolationType.UNAUTHORIZED_ACCESS: Severity.HIGH,
    ViolationType.INJECTION_ATTEMPT: Severity.HIGH,
    ViolationType.RATE_LIMIT_ABUSE: Severity.MEDIUM,
    ViolationType.SUSPICIOUS_ACTIVITY: Severity.MEDIUM,
}


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SecurityViolationResult:
    """Result of security violation handling."""

    success: bool
    incident_id: int | None = None
    action_taken: str = ""
    error: str | None = None

    @classmethod
    def handled(cls, incident_id: int, action: str) -> "SecurityViolationResult":
        """Factory for successfully handled violation."""
        return cls(success=True, incident_id=incident_id, action_taken=action)

    @classmethod
    def failed(cls, error: str) -> "SecurityViolationResult":
        """Factory for failed handling."""
        return cls(success=False, error=error)


@dataclass
class SecurityConfig:
    """Configuration for security violation handling."""

    # Rate limit abuse detection
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 100

    # IP ban settings
    temporary_ban_hours: int = 1
    permanent_ban_threshold: int = 5  # violations before permanent ban

    # Suspicious IP tracking cache timeout (seconds)
    suspicious_ip_cache_timeout: int = 86400  # 24 hours

    # Injection attempt ban duration (hours)
    injection_ban_hours: int = 24

    # Suspicious activity detection
    failed_login_threshold: int = 5
    suspicious_ip_cache_prefix: str = "security:suspicious_ip:"
    banned_ip_cache_prefix: str = "security:banned_ip:"

    @classmethod
    def from_settings(cls) -> "SecurityConfig":
        """Load configuration from Django settings."""
        from selfhealing.config import get_security_thresholds

        thresholds = get_security_thresholds()
        return cls(
            rate_limit_window_seconds=thresholds.rate_limit_window_seconds,
            rate_limit_max_requests=thresholds.rate_limit_max_requests,
            temporary_ban_hours=thresholds.temporary_ban_hours,
            permanent_ban_threshold=thresholds.permanent_ban_threshold,
            suspicious_ip_cache_timeout=thresholds.suspicious_ip_cache_timeout,
            injection_ban_hours=thresholds.injection_ban_hours,
            failed_login_threshold=thresholds.failed_login_threshold,
            suspicious_ip_cache_prefix=thresholds.suspicious_ip_cache_prefix,
            banned_ip_cache_prefix=thresholds.banned_ip_cache_prefix,
        )


# =============================================================================
# Security Violation Service
# =============================================================================


class SecurityViolationService:
    """
    Service for handling security violations.

    Security violations are NEVER auto-recovered. They are:
    1. Immediately blocked
    2. Logged with full forensic context
    3. Routed to security team for investigation

    Usage:
        service = SecurityViolationService()
        result = service.handle_violation(
            violation_type=ViolationType.WEBHOOK_SIGNATURE_INVALID,
            request=request,
            description="HMAC signature mismatch",
        )

    For testing with mock repository:
        mock_repo = Mock(spec=SecurityIncidentRepository)
        service = SecurityViolationService(repository=mock_repo)
    """

    def __init__(
        self,
        config: SecurityConfig | None = None,
        repository: "SecurityIncidentRepository | None" = None,
    ):
        """
        Initialize the security violation service.

        Args:
            config: Optional configuration, loads from settings if None
            repository: Optional repository for DI, uses Django adapter if None
        """
        self.config = config or SecurityConfig.from_settings()
        self._repository = repository

    @property
    def repository(self) -> "SecurityIncidentRepository":
        """Get the repository, creating Django adapter if needed."""
        if self._repository is None:
            from .adapters.django_repositories import DjangoSecurityIncidentRepository

            self._repository = DjangoSecurityIncidentRepository()
        return self._repository

    def handle_violation(
        self,
        violation_type: str | ViolationType,
        request: "HttpRequest | None" = None,
        user: "User | None" = None,
        order: "Order | None" = None,
        payment: "Payment | None" = None,
        description: str = "",
        raw_request_data: dict[str, Any] | None = None,
    ) -> SecurityViolationResult:
        """
        Handle a security violation.

        This method:
        1. Creates a SecurityIncident record
        2. Takes immediate protective action based on violation type
        3. Triggers security team notification
        4. Returns result with action taken

        Args:
            violation_type: Type of security violation
            request: Django HTTP request (for extracting IP, user agent)
            user: Associated user (if authenticated)
            order: Related order (if applicable)
            payment: Related payment (if applicable)
            description: Detailed description of the violation
            raw_request_data: Sanitized request data for forensics

        Returns:
            SecurityViolationResult with incident ID and action taken
        """
        from shopping.models.security_incident import SecurityIncident

        violation_type_str = violation_type.value if isinstance(violation_type, ViolationType) else violation_type

        try:
            # Extract request information
            source_ip = self._get_client_ip(request) if request else None
            user_agent = request.META.get("HTTP_USER_AGENT", "") if request else ""

            # Determine severity
            severity = SEVERITY_BY_VIOLATION_TYPE.get(violation_type_str, Severity.MEDIUM)

            # Create incident record
            with transaction.atomic():
                incident = SecurityIncident.create_incident(
                    incident_type=violation_type_str,
                    description=description,
                    source_ip=source_ip,
                    user_agent=user_agent,
                    user=user,
                    order=order,
                    payment=payment,
                    raw_request=self._sanitize_request_data(raw_request_data),
                )

                # Take immediate protective action
                action_taken = self._take_protective_action(
                    violation_type=violation_type_str,
                    incident=incident,
                    user=user,
                    source_ip=source_ip,
                )

                # Record action taken
                if action_taken:
                    incident.add_action_taken(action_taken)

            # Log the violation
            logger.warning(
                f"[Security Violation] type={violation_type_str} severity={severity.value} "
                f"ip={source_ip} user={user.id if user else None} "
                f"incident_id={incident.id} action={action_taken}"
            )

            # Trigger notification (async if possible)
            # Notification failure should not affect incident creation
            try:
                self._send_security_notification(incident)
            except Exception as e:
                logger.error(f"[Security Violation] Notification failed but incident saved: {e}")

            return SecurityViolationResult.handled(
                incident_id=incident.id,
                action=action_taken,
            )

        except Exception as e:
            logger.error(
                f"[Security Violation] Failed to handle violation: {e}",
                exc_info=True,
            )
            return SecurityViolationResult.failed(str(e))

    def _take_protective_action(
        self,
        violation_type: str,
        incident: "SecurityIncident",
        user: "User | None",
        source_ip: str | None,
    ) -> str:
        """
        Take immediate protective action based on violation type.

        Args:
            violation_type: Type of violation
            incident: The created incident record
            user: Associated user
            source_ip: Source IP address

        Returns:
            Description of action taken
        """
        action_taken = ""

        if violation_type == ViolationType.TOKEN_FORGED.value:
            if user:
                action_taken = self._invalidate_user_sessions(user)
            else:
                action_taken = "Token forged but no user associated"

        elif violation_type == ViolationType.WEBHOOK_SIGNATURE_INVALID.value:
            if source_ip:
                action_taken = self._log_suspicious_ip(source_ip)
            else:
                action_taken = "Invalid webhook signature logged"

        elif violation_type == ViolationType.RATE_LIMIT_ABUSE.value:
            if source_ip:
                action_taken = self._temporary_ip_ban(source_ip, hours=self.config.temporary_ban_hours)
            else:
                action_taken = "Rate limit abuse detected but no IP"

        elif violation_type == ViolationType.PAYMENT_AMOUNT_TAMPERED.value:
            action_taken = "Payment blocked, order frozen for investigation"

        elif violation_type == ViolationType.UNAUTHORIZED_ACCESS.value:
            if user:
                action_taken = f"Access blocked for user {user.id}"
            else:
                action_taken = "Unauthorized access attempt logged"

        elif violation_type == ViolationType.REPLAY_ATTACK.value:
            action_taken = "Replay attack blocked, request discarded"
            if source_ip:
                self._log_suspicious_ip(source_ip)

        elif violation_type == ViolationType.INJECTION_ATTEMPT.value:
            action_taken = "Injection attempt blocked"
            if source_ip:
                self._temporary_ip_ban(source_ip, hours=self.config.injection_ban_hours)

        else:
            action_taken = f"Violation logged for review: {violation_type}"

        return action_taken

    def _invalidate_user_sessions(self, user: "User") -> str:
        """
        Invalidate all sessions for a user.

        Args:
            user: User whose sessions should be invalidated

        Returns:
            Description of action taken
        """
        try:
            # Delete all refresh tokens for the user
            from shopping.models.user import OutstandingToken

            token_count = OutstandingToken.objects.filter(user=user).count()
            OutstandingToken.objects.filter(user=user).delete()

            # Clear any cached sessions
            cache_key = f"user_session:{user.id}"
            cache.delete(cache_key)

            logger.info(f"[Security] Invalidated {token_count} sessions for user {user.id}")
            return f"All user sessions invalidated ({token_count} tokens revoked)"

        except Exception as e:
            logger.error(f"[Security] Failed to invalidate sessions: {e}")
            return f"Session invalidation attempted but failed: {e}"

    def _log_suspicious_ip(self, ip_address: str) -> str:
        """
        Log an IP address as suspicious for monitoring.

        Args:
            ip_address: IP address to log

        Returns:
            Description of action taken
        """
        cache_key = f"{self.config.suspicious_ip_cache_prefix}{ip_address}"

        # Increment suspicious activity count
        current_count = cache.get(cache_key, 0)
        new_count = current_count + 1
        cache.set(cache_key, new_count, timeout=self.config.suspicious_ip_cache_timeout)

        logger.info(f"[Security] Suspicious IP logged: {ip_address} (count: {new_count})")

        # Check if should escalate to ban
        if new_count >= self.config.permanent_ban_threshold:
            self._permanent_ip_ban(ip_address)
            return f"IP {ip_address} marked for permanent ban (violations: {new_count})"

        return f"IP {ip_address} logged for monitoring (violations: {new_count})"

    def _temporary_ip_ban(self, ip_address: str, hours: int = 1) -> str:
        """
        Temporarily ban an IP address.

        Args:
            ip_address: IP address to ban
            hours: Duration of ban in hours

        Returns:
            Description of action taken
        """
        cache_key = f"{self.config.banned_ip_cache_prefix}{ip_address}"
        cache.set(cache_key, {"banned": True, "type": "temporary"}, timeout=hours * 3600)

        logger.info(f"[Security] IP temporarily banned: {ip_address} for {hours} hours")
        return f"IP {ip_address} temporarily banned for {hours} hour(s)"

    def _permanent_ip_ban(self, ip_address: str) -> str:
        """
        Permanently ban an IP address.

        Args:
            ip_address: IP address to ban

        Returns:
            Description of action taken
        """
        cache_key = f"{self.config.banned_ip_cache_prefix}{ip_address}"
        cache.set(cache_key, {"banned": True, "type": "permanent"}, timeout=None)

        logger.warning(f"[Security] IP permanently banned: {ip_address}")
        return f"IP {ip_address} permanently banned"

    def is_ip_banned(self, ip_address: str) -> bool:
        """
        Check if an IP address is banned.

        Args:
            ip_address: IP address to check

        Returns:
            True if banned, False otherwise
        """
        cache_key = f"{self.config.banned_ip_cache_prefix}{ip_address}"
        ban_info = cache.get(cache_key)
        return ban_info is not None and ban_info.get("banned", False)

    def _get_client_ip(self, request: "HttpRequest") -> str | None:
        """
        Extract client IP from request.

        Handles X-Forwarded-For header for proxied requests.

        Args:
            request: Django HTTP request

        Returns:
            Client IP address or None
        """
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            # Take the first IP in the chain (original client)
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def _sanitize_request_data(self, raw_data: dict[str, Any] | None) -> dict[str, Any]:
        """
        Sanitize request data by removing sensitive fields.

        Args:
            raw_data: Raw request data

        Returns:
            Sanitized data safe for storage
        """
        if not raw_data:
            return {}

        sensitive_fields = {
            "password",
            "new_password",
            "old_password",
            "token",
            "access_token",
            "refresh_token",
            "api_key",
            "secret",
            "card_number",
            "cvv",
            "cvc",
            "credit_card",
        }

        def sanitize(data: Any) -> Any:
            if isinstance(data, dict):
                return {k: "[REDACTED]" if k.lower() in sensitive_fields else sanitize(v) for k, v in data.items()}
            elif isinstance(data, list):
                return [sanitize(item) for item in data]
            return data

        return sanitize(raw_data)

    def _send_security_notification(self, incident: "SecurityIncident") -> None:
        """
        Send security notification for the incident.

        This method delegates to the security notification service.

        Args:
            incident: The security incident to notify about
        """
        try:
            from selfhealing.security_notification_service import (
                get_security_notification_service,
            )

            service = get_security_notification_service()
            service.notify_security_incident(incident)

        except Exception as e:
            # Don't fail the main flow if notification fails
            logger.error(f"[Security] Failed to send notification for incident {incident.id}: {e}")


# =============================================================================
# Module-level Helper Functions
# =============================================================================


_security_service: SecurityViolationService | None = None


def get_security_violation_service() -> SecurityViolationService:
    """Get or create the singleton security violation service."""
    global _security_service
    if _security_service is None:
        _security_service = SecurityViolationService()
    return _security_service


def handle_security_violation(
    violation_type: str | ViolationType,
    request: "HttpRequest | None" = None,
    user: "User | None" = None,
    description: str = "",
    **kwargs: Any,
) -> SecurityViolationResult:
    """
    Convenience function to handle a security violation.

    This is the main entry point for security violation handling.

    Args:
        violation_type: Type of security violation
        request: Django HTTP request
        user: Associated user
        description: Description of the violation
        **kwargs: Additional arguments passed to handle_violation

    Returns:
        SecurityViolationResult
    """
    service = get_security_violation_service()
    return service.handle_violation(
        violation_type=violation_type,
        request=request,
        user=user,
        description=description,
        **kwargs,
    )
