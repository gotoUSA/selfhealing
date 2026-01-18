"""
Security Violation Handling Package.

Handles security violations that should NEVER self-heal.
Security incidents are immediately blocked and routed to the security team.

Features:
- Detect and classify security violations
- Take immediate protective actions (via ProtectionOrchestrator)
- Create SecurityIncident records
- Trigger security notifications
- ActionPolicy-based protection with rollback support

Usage:
    from selfhealing.services.security import (
        SecurityViolationService,
        ViolationType,
        Severity,
        handle_security_violation,
    )

    # Simple usage
    result = handle_security_violation(
        violation_type=ViolationType.SIGNATURE_INVALID,
        request_info={"ip": "1.2.3.4", "user_agent": "..."},
        description="Signature validation failed",
    )

    # With service instance
    service = SecurityViolationService()
    result = service.handle_violation(...)
"""

from .types import (
    ViolationType,
    Severity,
    SEVERITY_BY_VIOLATION_TYPE,
)
from .policies import (
    ActionPolicy,
    ACTION_POLICY_PRIORITY,
    ACTION_POLICY_BY_VIOLATION_TYPE,
)
from .models import (
    ProtectionResult,
    SecurityViolationResult,
    SecurityConfig,
)
from .service import SecurityViolationService
from .orchestrator import ProtectionOrchestrator
from .helpers import (
    get_security_violation_service,
    reset_security_violation_service,
    handle_security_violation,
)


__all__ = [
    # Types
    "ViolationType",
    "Severity",
    "SEVERITY_BY_VIOLATION_TYPE",
    # Policies
    "ActionPolicy",
    "ACTION_POLICY_PRIORITY",
    "ACTION_POLICY_BY_VIOLATION_TYPE",
    # Models
    "ProtectionResult",
    "SecurityViolationResult",
    "SecurityConfig",
    # Service
    "SecurityViolationService",
    # Orchestrator
    "ProtectionOrchestrator",
    # Helpers
    "get_security_violation_service",
    "reset_security_violation_service",
    "handle_security_violation",
]
