"""
Emergency Coordination Layer.

긴급 상황 조율 레이어 - 독립적인 Emergency 시스템들 간의 연계를 조율합니다.

주요 컴포넌트:
- EmergencyCoordinator: 중앙 조율자
- AntiFlappingGuard: 플래핑 방지 히스테리시스 가드
- ScopedEmergencyState: 네임스페이스별 상태
- CoordinationAction: 연계 액션 정의

Phase 1 구현:
- EmergencyScope enum
- ScopedEmergencyState 모델
- EmergencyCoordinator 골격
- AntiFlappingGuard (히스테리시스)
- Dry-Run Mode

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

from __future__ import annotations

from .enums import (
    EmergencyScope,
    ActionType,
    CommandPrecedence,
    RecoveryStatus,
)
from .models import (
    CoordinationAction,
    ScopedEmergencyState,
    OverrideTTLConfig,
    ActionResult,
    CoordinationResult,
)
from .anti_flapping import (
    AntiFlappingGuard,
    EMERGENCY_LEVEL_COOLDOWN_SECONDS,
)
from .coordinator import (
    EmergencyCoordinator,
    DryRunAuditLogger,
)


__all__ = [
    # Enums
    "EmergencyScope",
    "ActionType",
    "CommandPrecedence",
    "RecoveryStatus",
    # Models
    "CoordinationAction",
    "ScopedEmergencyState",
    "OverrideTTLConfig",
    "ActionResult",
    "CoordinationResult",
    # Anti-Flapping
    "AntiFlappingGuard",
    "EMERGENCY_LEVEL_COOLDOWN_SECONDS",
    # Coordinator
    "EmergencyCoordinator",
    "DryRunAuditLogger",
]
