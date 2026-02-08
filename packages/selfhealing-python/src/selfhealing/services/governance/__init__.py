"""
Governance Package - Emergency Mode & Safety Checks.

비상 모드 추적, 거버넌스 체크, 만료 관리, API 서비스를 제공합니다.

Modules:
    - emergency: EmergencyModeTracker, OperationMode, 긴급 모드 추적
    - checks: GovernanceCheckMixin, 데코레이터, check_all_governance
    - service: GovernanceService (Celery Task용 만료 체크)
    - api_service: GovernanceApiService (API View용)

Usage:
    from selfhealing.services.governance import (
        get_emergency_tracker,
        EmergencyModeTracker,
        GovernanceCheckMixin,
    )

.. versionadded:: 2.1.0
    ``governance*.py`` 플랫 파일 4개에서 ``governance/`` 패키지로 전환.
"""

# --- emergency.py exports ---
from selfhealing.services.governance.emergency import (
    EmergencyModeTracker,
    GovernanceEmergencyState,
    OperationMode,
    get_current_operation_mode,
    get_emergency_tracker,
    is_emergency_mode_active,
)

# --- checks.py exports ---
from selfhealing.services.governance.checks import (
    BlockReason,
    GovernanceCheckMixin,
    GovernanceCheckResult,
    TTLCache,
    check_all_governance,
    invalidate_governance_cache,
    is_emergency_blocking,
    is_error_budget_blocking,
    is_system_enabled,
    require_error_budget,
    require_governance,
    require_not_emergency,
    require_system_enabled,
)

# --- service.py exports ---
from selfhealing.services.governance.service import (
    ExpiryCheckResult,
    GovernanceNotificationResult,
    GovernanceService,
    get_governance_service,
)

# --- api_service.py exports ---
from selfhealing.services.governance.api_service import (
    GovernanceApiService,
    get_governance_api_service,
    reset_governance_api_service,
)

__all__ = [
    # emergency
    "OperationMode",
    "GovernanceEmergencyState",
    "EmergencyModeTracker",
    "get_emergency_tracker",
    "is_emergency_mode_active",
    "get_current_operation_mode",
    # checks
    "BlockReason",
    "GovernanceCheckResult",
    "TTLCache",
    "invalidate_governance_cache",
    "is_system_enabled",
    "is_emergency_blocking",
    "is_error_budget_blocking",
    "check_all_governance",
    "require_system_enabled",
    "require_not_emergency",
    "require_error_budget",
    "require_governance",
    "GovernanceCheckMixin",
    # service
    "ExpiryCheckResult",
    "GovernanceNotificationResult",
    "GovernanceService",
    "get_governance_service",
    # api_service
    "GovernanceApiService",
    "get_governance_api_service",
    "reset_governance_api_service",
]
