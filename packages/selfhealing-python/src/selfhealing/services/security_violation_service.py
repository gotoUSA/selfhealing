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
from typing import TYPE_CHECKING, Any, Optional

from selfhealing.core.timezone import now
from selfhealing.core.config import get_config

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        SecurityIncidentRepository,
    )
    from selfhealing.interfaces.cache_provider import CacheProviderInterface

logger = logging.getLogger(__name__)


# =============================================================================
# Constants and Configuration
# =============================================================================


class ViolationType(str, Enum):
    """Types of security violations that never self-heal (domain-neutral)."""

    SIGNATURE_INVALID = "signature_invalid"
    DATA_TAMPERED = "data_tampered"
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
    ViolationType.SIGNATURE_INVALID: Severity.CRITICAL,
    ViolationType.DATA_TAMPERED: Severity.CRITICAL,
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
        """Load configuration from settings."""
        config = get_config()
        security = config.security
        return cls(
            rate_limit_window_seconds=security.rate_limit_window_seconds,
            rate_limit_max_requests=security.rate_limit_max_requests,
            temporary_ban_hours=security.temporary_ban_hours,
            permanent_ban_threshold=security.permanent_ban_threshold,
            suspicious_ip_cache_timeout=security.suspicious_ip_cache_timeout,
            injection_ban_hours=security.injection_ban_hours,
            failed_login_threshold=security.failed_login_threshold,
            suspicious_ip_cache_prefix=security.suspicious_ip_cache_prefix,
            banned_ip_cache_prefix=security.banned_ip_cache_prefix,
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
            violation_type=ViolationType.SIGNATURE_INVALID,
            request_info={"ip": "1.2.3.4", "user_agent": "..."},
            description="Signature validation failed",
        )

    For testing with mock repository:
        mock_repo = Mock(spec=SecurityIncidentRepository)
        service = SecurityViolationService(repository=mock_repo)
    """

    def __init__(
        self,
        config: SecurityConfig | None = None,
        repository: "SecurityIncidentRepository | None" = None,
        cache: "CacheProviderInterface | None" = None,
    ):
        """
        Initialize the security violation service.

        Args:
            config: Optional configuration, loads from settings if None
            repository: Optional repository for DI, uses default adapter if None
            cache: Optional cache provider for DI, uses default if None
        """
        self.config = config or SecurityConfig.from_settings()
        self._repository = repository
        self._cache = cache

    @property
    def repository(self) -> "SecurityIncidentRepository":
        """Get the repository, creating default adapter if needed."""
        if self._repository is None:
            from selfhealing.factory import ProviderRegistry

            try:
                self._repository = ProviderRegistry.get_security_repo()
            except (ValueError, ImportError):
                from selfhealing.adapters.memory import InMemorySecurityIncidentRepository

                self._repository = InMemorySecurityIncidentRepository()
        return self._repository

    @property
    def cache(self) -> "CacheProviderInterface":
        """Get the cache provider, creating default if needed."""
        if self._cache is None:
            from selfhealing.factory import ProviderRegistry

            try:
                self._cache = ProviderRegistry.get_cache()
            except (ValueError, ImportError):
                from selfhealing.interfaces.cache_provider import InMemoryCacheAdapter

                self._cache = InMemoryCacheAdapter()
        return self._cache

    def handle_violation(
        self,
        violation_type: str | ViolationType,
        request_info: dict[str, Any] | None = None,
        user_id: Optional[int] = None,
        entity_refs: Optional[dict[str, int]] = None,
        description: str = "",
        raw_request_data: dict[str, Any] | None = None,
    ) -> SecurityViolationResult:
        """
        Handle a security violation.

        This method:
        1. Creates a SecurityIncident record via repository
        2. Takes immediate protective action based on violation type
        3. Triggers security team notification
        4. Returns result with action taken

        Args:
            violation_type: Type of security violation
            request_info: Request info dict with 'ip', 'user_agent' keys
            user_id: Associated user ID (if authenticated)
            entity_refs: Related entity references (e.g., {"order_id": 123})
            description: Detailed description of the violation
            raw_request_data: Sanitized request data for forensics

        Returns:
            SecurityViolationResult with incident ID and action taken
        """
        violation_type_str = violation_type.value if isinstance(violation_type, ViolationType) else violation_type

        try:
            # Extract request information
            source_ip = request_info.get("ip") if request_info else None
            user_agent = request_info.get("user_agent", "") if request_info else ""

            # Determine severity
            severity = SEVERITY_BY_VIOLATION_TYPE.get(violation_type_str, Severity.MEDIUM)

            # Create incident record via repository
            incident = self.repository.create(
                incident_type=violation_type_str,
                severity=severity.value,
                description=description,
                source_ip=source_ip,
                user_agent=user_agent,
                user_id=user_id,
                entity_refs=entity_refs or {},
                raw_payload=self._sanitize_request_data(raw_request_data),
            )

            # Take immediate protective action
            action_taken = self._take_protective_action(
                violation_type=violation_type_str,
                incident_id=incident.id,
                user_id=user_id,
                source_ip=source_ip,
            )

            # Log the violation
            logger.warning(
                f"[Security Violation] type={violation_type_str} severity={severity.value} "
                f"ip={source_ip} user_id={user_id} "
                f"incident_id={incident.id} action={action_taken}"
            )

            # Trigger notification (async if possible)
            # Notification failure should not affect incident creation
            try:
                self._send_security_notification(incident.id, violation_type_str, severity.value)
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

    def record_violation(
        self,
        violation_type: str,
        details: dict[str, Any] | None = None,
        request_info: dict[str, Any] | None = None,
        user_id: Optional[int] = None,
    ) -> SecurityViolationResult:
        """
        Simplified interface for recording a violation.
        
        This is a convenience method that wraps handle_violation for cases
        like CorruptionShield where simpler parameter passing is needed.
        
        Args:
            violation_type: Type of violation (e.g., "corruption_injection_attempt")
            details: Violation details dict (becomes description + raw_request_data)
            request_info: Optional request info with 'ip', 'user_agent'
            user_id: Optional associated user ID
            
        Returns:
            SecurityViolationResult with incident ID and action taken
        """
        # Build description from details
        description = ""
        if details:
            layer = details.get("layer", "unknown")
            message = details.get("message", "")
            field = details.get("field", "")
            description = f"[{layer}] {message}"
            if field:
                description += f" (field: {field})"
        
        return self.handle_violation(
            violation_type=violation_type,
            request_info=request_info,
            user_id=user_id,
            description=description,
            raw_request_data=details,
        )

    def _take_protective_action(
        self,
        violation_type: str,
        incident_id: int,
        user_id: Optional[int],
        source_ip: str | None,
    ) -> str:
        """
        Take immediate protective action based on violation type.

        Args:
            violation_type: Type of violation
            incident_id: The created incident ID
            user_id: Associated user ID
            source_ip: Source IP address

        Returns:
            Description of action taken
        """
        action_taken = ""

        if violation_type == ViolationType.TOKEN_FORGED.value:
            if user_id:
                action_taken = self._invalidate_user_sessions(user_id)
            else:
                action_taken = "Token forged but no user associated"

        elif violation_type == ViolationType.SIGNATURE_INVALID.value:
            if source_ip:
                action_taken = self._log_suspicious_ip(source_ip)
            else:
                action_taken = "Invalid signature logged"

        elif violation_type == ViolationType.RATE_LIMIT_ABUSE.value:
            if source_ip:
                action_taken = self._temporary_ip_ban(source_ip, hours=self.config.temporary_ban_hours)
            else:
                action_taken = "Rate limit abuse detected but no IP"

        elif violation_type == ViolationType.DATA_TAMPERED.value:
            action_taken = "Request blocked, entity frozen for investigation"

        elif violation_type == ViolationType.UNAUTHORIZED_ACCESS.value:
            if user_id:
                action_taken = f"Access blocked for user {user_id}"
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

    def _invalidate_user_sessions(self, user_id: int) -> str:
        """
        Invalidate all sessions for a user.

        Note: This is a placeholder. The actual implementation depends on
        the session/token management system being used.

        Args:
            user_id: ID of user whose sessions should be invalidated

        Returns:
            Description of action taken
        """
        try:
            # Clear any cached sessions
            cache_key = f"user_session:{user_id}"
            self.cache.delete(cache_key)

            logger.info(f"[Security] Invalidated sessions for user {user_id}")
            return f"User sessions cache cleared for user {user_id}"

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
        current_count = self.cache.get(cache_key) or 0
        new_count = current_count + 1
        self.cache.set(cache_key, new_count, ttl=timedelta(seconds=self.config.suspicious_ip_cache_timeout))

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
        self.cache.set(cache_key, {"banned": True, "type": "temporary"}, ttl=timedelta(hours=hours))

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
        self.cache.set(cache_key, {"banned": True, "type": "permanent"}, ttl=None)

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
        ban_info = self.cache.get(cache_key)
        return ban_info is not None and ban_info.get("banned", False)

    def _sanitize_request_data(self, raw_data: dict[str, Any] | None) -> dict[str, Any]:
        """
        Sanitize request data by removing sensitive fields and masking IPs/paths.

        FAIL-SECURE DESIGN:
        - If masking fails for any reason, return empty dict (not raw data)
        - This prevents accidental exposure of sensitive information
        - Better to lose debugging context than expose secrets

        Masks:
        - Sensitive field values (passwords, tokens, keys)
        - Internal IP addresses (10.x.x.x, 172.16-31.x.x, 192.168.x.x)
        - Server paths (home directories, config paths)

        Args:
            raw_data: Raw request data

        Returns:
            Sanitized data safe for storage and logging
        """
        import re

        # FAIL-SECURE: Return empty on None/empty input
        if not raw_data:
            return {}

        try:
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
                "private_key",
                "secret_key",
                "connection_string",
                "db_password",
                "redis_password",
            }

            # Internal IP patterns to mask
            internal_ip_patterns = [
                re.compile(r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"),           # 10.0.0.0/8
                re.compile(r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"),  # 172.16.0.0/12
                re.compile(r"192\.168\.\d{1,3}\.\d{1,3}"),              # 192.168.0.0/16
            ]

            # Server path patterns to mask
            server_path_patterns = [
                re.compile(r"/home/[^/\s]+"),                          # Unix home dirs
                re.compile(r"/var/[^/\s]+/[^/\s]+"),                   # Var subdirs
                re.compile(r"/etc/[^/\s]+"),                           # Config files
                re.compile(r"[A-Z]:\\Users\\[^\\\s]+", re.IGNORECASE), # Windows paths
                re.compile(r"/app/[^/\s]+/[^/\s]+"),                   # Container paths
            ]

            def mask_string(value: str) -> str:
                """Mask sensitive patterns in a string value."""
                result = value

                # Mask internal IPs
                for pattern in internal_ip_patterns:
                    result = pattern.sub("[INTERNAL_IP]", result)

                # Mask server paths
                for pattern in server_path_patterns:
                    result = pattern.sub("[SERVER_PATH]", result)

                return result

            def sanitize(data: Any) -> Any:
                if isinstance(data, dict):
                    return {
                        k: "[REDACTED]" if k.lower() in sensitive_fields else sanitize(v)
                        for k, v in data.items()
                    }
                elif isinstance(data, list):
                    return [sanitize(item) for item in data]
                elif isinstance(data, str):
                    return mask_string(data)
                return data

            return sanitize(raw_data)

        except Exception as e:
            # FAIL-SECURE: On any error, return fixed placeholder string
            # Never return raw data that might contain sensitive information
            # Using fixed string instead of dict for consistency and log parsing
            logger.error(f"[Security] Masking failed, returning placeholder: {e}")
            return "[MASKING_ERROR: SENSITIVE_DATA_HIDDEN]"

    def _send_security_notification(
        self,
        incident_id: int,
        incident_type: str,
        severity: str,
    ) -> None:
        """
        Send security notification for the incident.

        This method delegates to the security notification service.

        Args:
            incident_id: The security incident ID to notify about
            incident_type: Type of the incident
            severity: Severity level of the incident
        """
        try:
            from selfhealing.services.security_notification_service import (
                get_security_notification_service,
            )

            service = get_security_notification_service()
            service.notify_security_incident_by_id(incident_id, incident_type, severity)

        except Exception as e:
            # Don't fail the main flow if notification fails
            logger.error(f"[Security] Failed to send notification for incident {incident_id}: {e}")


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
    request_info: dict[str, Any] | None = None,
    user_id: Optional[int] = None,
    description: str = "",
    **kwargs: Any,
) -> SecurityViolationResult:
    """
    Convenience function to handle a security violation.

    This is the main entry point for security violation handling.

    Args:
        violation_type: Type of security violation
        request_info: Request info dict with 'ip', 'user_agent' keys
        user_id: Associated user ID
        description: Description of the violation
        **kwargs: Additional arguments passed to handle_violation

    Returns:
        SecurityViolationResult
    """
    service = get_security_violation_service()
    return service.handle_violation(
        violation_type=violation_type,
        request_info=request_info,
        user_id=user_id,
        description=description,
        **kwargs,
    )
