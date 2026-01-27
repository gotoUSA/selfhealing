"""
Security Violation Helper Functions.

Module-level helper functions for accessing security violation services.
"""

from __future__ import annotations

from typing import Any

from selfhealing.services.security.models import SecurityViolationResult
from selfhealing.services.security.service import SecurityViolationService
from selfhealing.services.security.types import ViolationType

_security_service: SecurityViolationService | None = None


def get_security_violation_service() -> SecurityViolationService:
    """Get or create the singleton security violation service."""
    global _security_service
    if _security_service is None:
        _security_service = SecurityViolationService()
    return _security_service


def reset_security_violation_service() -> None:
    """Reset the singleton security violation service (for testing)."""
    global _security_service
    _security_service = None


def handle_security_violation(
    violation_type: str | ViolationType,
    request_info: dict[str, Any] | None = None,
    user_id: int | None = None,
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
