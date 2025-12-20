"""
Forensic Advisor - DEPRECATED

.. deprecated:: 0.1.0
    This module has moved to the selfhealing package.

    Before (deprecated):
        from shopping.services.self_healing.forensic_advisor import ForensicAdvisorService

    After:
        from selfhealing.services.forensic_advisor import ForensicAdvisorService

This is a compatibility shim. All functionality has been moved to:
    packages/selfhealing-python/src/selfhealing/services/forensic_advisor.py

Reference: docs/self_healing/11_FORENSIC_ADVISOR.md
"""

import warnings

warnings.warn(
    "Importing from shopping.services.self_healing.forensic_advisor is deprecated. "
    "Please migrate to 'from selfhealing.services.forensic_advisor import ...' "
    "See packages/selfhealing-python/docs/MIGRATION.md for details.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new location for backwards compatibility
from selfhealing.services.forensic_advisor import (
    AdvisoryLevel,
    RecommendedAction,
    FailurePattern,
    ForensicAdvisory,
    ForensicAdvisorService,
    KNOWN_PATTERNS,
    get_forensic_advisor,
    analyze_failed_operation,
    analyze_and_update_operation,
)

__all__ = [
    "AdvisoryLevel",
    "RecommendedAction",
    "FailurePattern",
    "ForensicAdvisory",
    "ForensicAdvisorService",
    "KNOWN_PATTERNS",
    "get_forensic_advisor",
    "analyze_failed_operation",
    "analyze_and_update_operation",
]
