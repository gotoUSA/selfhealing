"""
Emergency Mode Service - Graceful Degradation Manager.

비상 모드의 단계별 제어와 안전한 복구를 담당합니다.

Features:
- EmergencyLevel: 단계별 비상 모드 (NORMAL, LEVEL_1, LEVEL_2, LEVEL_3)
- GracefulDegradationManager: 비상 모드 진입/해제 관리
- RecoveryGate: 복구 안정화 및 점진적 복구

This package has been refactored from a single 1,043-line file into:
- enums.py: EmergencyLevel enum and EMERGENCY_LEVEL_RULES
- models.py: RecoveryGateConfig and EmergencyState dataclasses
- recovery_gate.py: RecoveryGate for safe recovery checks
- manager.py: GracefulDegradationManager main class

All exports are maintained for backward compatibility.

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1A.md (Section 4)

Usage:
    from selfhealing.services.emergency_mode import (
        get_emergency_manager,
        EmergencyLevel,
        is_emergency_active,
    )
    
    # Check emergency status
    if is_emergency_active():
        level = get_emergency_manager().get_current_level()
        ...
    
    # Activate emergency mode
    get_emergency_manager().activate_manual(
        level=EmergencyLevel.LEVEL_2,
        reason="High error rate detected",
        activated_by="admin",
        duration_minutes=30,
    )
    
    # Deactivate emergency mode
    get_emergency_manager().deactivate(deactivated_by="admin")
"""

from __future__ import annotations

from typing import Optional

# Enums
from .enums import EmergencyLevel, EMERGENCY_LEVEL_RULES

# Models
from .models import RecoveryGateConfig, EmergencyState

# Recovery Gate
from .recovery_gate import RecoveryGate

# Manager
from .manager import GracefulDegradationManager


# =============================================================================
# Singleton & Factory Functions
# =============================================================================


# Global instance
_emergency_manager: Optional[GracefulDegradationManager] = None


def get_emergency_manager() -> GracefulDegradationManager:
    """비상 모드 관리자 싱글톤 획득."""
    global _emergency_manager
    if _emergency_manager is None:
        _emergency_manager = GracefulDegradationManager()
    return _emergency_manager


def is_emergency_active() -> bool:
    """
    비상 모드 활성화 여부 확인 (간편 함수).
    
    Usage:
        if is_emergency_active():
            # 비상 모드 처리
            ...
    """
    return get_emergency_manager().is_active()


def get_emergency_level() -> EmergencyLevel:
    """
    현재 비상 모드 레벨 조회 (간편 함수).
    
    Usage:
        level = get_emergency_level()
        if level >= EmergencyLevel.LEVEL_2:
            # 심각한 비상 상황 처리
            ...
    """
    return get_emergency_manager().get_current_level()


def get_tier_multiplier(tier_id: str) -> float:
    """
    현재 비상 모드에 따른 티어 배율 조회 (간편 함수).
    
    Usage:
        multiplier = get_tier_multiplier("standard")
        if random.random() > multiplier:
            # Load shedding
            return Response(status=503)
    """
    return get_emergency_manager().get_tier_multiplier(tier_id)


__all__ = [
    # Enums
    "EmergencyLevel",
    "EMERGENCY_LEVEL_RULES",
    # Models
    "RecoveryGateConfig",
    "EmergencyState",
    # Classes
    "RecoveryGate",
    "GracefulDegradationManager",
    # Singleton functions
    "get_emergency_manager",
    "is_emergency_active",
    "get_emergency_level",
    "get_tier_multiplier",
]
