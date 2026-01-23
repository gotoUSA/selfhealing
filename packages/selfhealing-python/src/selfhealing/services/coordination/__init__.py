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
- AntiFlappingGuard (히스테리시스 + recovery_hysteresis_factor)
- Dry-Run Mode

Phase 2 구현:
- CoordinationPolicyEngine: 정책 기반 액션 결정
- CoordinationPolicy: 연계 정책 정의
- CriticalPathFallback: Redis/Audit 장애 시 로컬 폴백
- AtomicLevelTransition: Lua 스크립트 원자적 상태 변경
- DomainAwareCrisisMultiplier: 도메인 인지형 가중치

Phase 4 구현:
- RecoveryAccountabilityConfig: 복구 책임 추적 설정
- OptimisticLocalActionExecutor: 낙관적 선조치 실행기

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
    RecoveryAccountabilityConfig,
)
from .anti_flapping import (
    AntiFlappingGuard,
    EMERGENCY_LEVEL_COOLDOWN_SECONDS,
)
from .coordinator import (
    EmergencyCoordinator,
    DryRunAuditLogger,
)
from .policy_engine import (
    CoordinationPolicy,
    CoordinationPolicyEngine,
)
from .critical_path_fallback import (
    CriticalPathFallback,
)
from .atomic_transition import (
    AtomicLevelTransition,
    ATOMIC_TRANSITION_SCRIPT,
)
from .crisis_multiplier import (
    DomainAwareCrisisMultiplier,
    CrisisMultiplierRegistry,
    MAX_CRISIS_MULTIPLIER,
)
from .optimistic_action import (
    OptimisticLocalActionExecutor,
    OptimisticActionResult,
    get_optimistic_action_executor,
    reset_optimistic_action_executor,
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
    # Phase 4: Recovery Accountability
    "RecoveryAccountabilityConfig",
    # Anti-Flapping
    "AntiFlappingGuard",
    "EMERGENCY_LEVEL_COOLDOWN_SECONDS",
    # Coordinator
    "EmergencyCoordinator",
    "DryRunAuditLogger",
    # Phase 2: Policy Engine
    "CoordinationPolicy",
    "CoordinationPolicyEngine",
    # Phase 2: Critical Path Fallback
    "CriticalPathFallback",
    # Phase 2: Atomic Transition
    "AtomicLevelTransition",
    "ATOMIC_TRANSITION_SCRIPT",
    # Phase 2: Crisis Multiplier
    "DomainAwareCrisisMultiplier",
    "CrisisMultiplierRegistry",
    "MAX_CRISIS_MULTIPLIER",
    # Phase 4: Optimistic Action
    "OptimisticLocalActionExecutor",
    "OptimisticActionResult",
    "get_optimistic_action_executor",
    "reset_optimistic_action_executor",
]
