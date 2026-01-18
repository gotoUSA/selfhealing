"""
Security Action Policies.

Defines immediate response policies for security violations
and their priority ordering.
"""

from __future__ import annotations

from enum import Enum

from selfhealing.services.security.types import ViolationType


class ActionPolicy(str, Enum):
    """
    즉각적 대응 정책 (우선순위 높은 순).

    보호 조치의 원자성 보장 - "가장 강력한 정책 우선" 원칙.
    """

    # Priority 1: 시스템 전체 보호
    EMERGENCY_LEVEL_3 = "emergency_level_3"
    """전체 시스템 보호 모드."""

    EMERGENCY_LEVEL_2 = "emergency_level_2"
    """부분 시스템 보호 모드."""

    EMERGENCY_LEVEL_1 = "emergency_level_1"
    """경고 모드."""

    # Priority 2: 사용자/세션 격리
    ACCOUNT_FREEZE = "account_freeze"
    """계정 동결."""

    SESSION_INVALIDATE = "session_invalidate"
    """모든 세션 무효화."""

    # Priority 3: 네트워크 차단
    IP_PERMANENT_BAN = "ip_permanent_ban"
    """영구 IP 차단."""

    IP_TEMPORARY_BAN = "ip_temporary_ban"
    """임시 IP 차단."""

    # Priority 4: 로깅
    BLOCK_AND_LOG = "block_and_log"
    """차단 및 로깅."""


# 정책 우선순위 (숫자가 낮을수록 높은 우선순위)
ACTION_POLICY_PRIORITY: dict[ActionPolicy, int] = {
    ActionPolicy.EMERGENCY_LEVEL_3: 1,
    ActionPolicy.EMERGENCY_LEVEL_2: 2,
    ActionPolicy.EMERGENCY_LEVEL_1: 3,
    ActionPolicy.ACCOUNT_FREEZE: 4,
    ActionPolicy.SESSION_INVALIDATE: 5,
    ActionPolicy.IP_PERMANENT_BAN: 6,
    ActionPolicy.IP_TEMPORARY_BAN: 7,
    ActionPolicy.BLOCK_AND_LOG: 8,
}


# ViolationType → ActionPolicy 매핑
ACTION_POLICY_BY_VIOLATION_TYPE: dict[ViolationType, list[ActionPolicy]] = {
    # Self-Healing 루프 감지 - 가장 심각한 위반
    ViolationType.RECOVERY_LOOP_DETECTED: [
        ActionPolicy.EMERGENCY_LEVEL_3,  # 전체 보호!
    ],
    # 높은 심각도 보안 위반
    ViolationType.TOKEN_FORGED: [
        ActionPolicy.SESSION_INVALIDATE,
        ActionPolicy.IP_TEMPORARY_BAN,
    ],
    ViolationType.SIGNATURE_INVALID: [
        ActionPolicy.BLOCK_AND_LOG,
        ActionPolicy.IP_TEMPORARY_BAN,
    ],
    ViolationType.REPLAY_ATTACK: [
        ActionPolicy.BLOCK_AND_LOG,
        ActionPolicy.IP_TEMPORARY_BAN,
    ],
    ViolationType.DATA_TAMPERED: [
        ActionPolicy.SESSION_INVALIDATE,
        ActionPolicy.IP_PERMANENT_BAN,
    ],
    ViolationType.UNAUTHORIZED_ACCESS: [
        ActionPolicy.BLOCK_AND_LOG,
    ],
    ViolationType.INJECTION_ATTEMPT: [
        ActionPolicy.IP_TEMPORARY_BAN,
        ActionPolicy.BLOCK_AND_LOG,
    ],
    # 기본 정책
    ViolationType.RATE_LIMIT_ABUSE: [
        ActionPolicy.IP_TEMPORARY_BAN,
    ],
    ViolationType.SUSPICIOUS_ACTIVITY: [
        ActionPolicy.BLOCK_AND_LOG,
    ],
    # Self-Healing 관련
    ViolationType.FLAPPING_DETECTED: [
        ActionPolicy.BLOCK_AND_LOG,
    ],
    ViolationType.CONFLICTING_ADJUSTMENT: [
        ActionPolicy.BLOCK_AND_LOG,
    ],
    ViolationType.HEALING_TIMEOUT: [
        ActionPolicy.BLOCK_AND_LOG,
    ],
    # ═══════════════════════════════════════════════════════════════════════════
    # 신규 ViolationType ActionPolicy 매핑 (순위 2 - v2.3.0)
    # ═══════════════════════════════════════════════════════════════════════════
    # Governance 위반 - 가장 심각 (다중 정책)
    ViolationType.PRIVILEGE_ESCALATION: [
        ActionPolicy.EMERGENCY_LEVEL_2,
        ActionPolicy.SESSION_INVALIDATE,
        ActionPolicy.ACCOUNT_FREEZE,
    ],
    ViolationType.GOVERNANCE_BYPASS_ATTEMPT: [
        ActionPolicy.EMERGENCY_LEVEL_2,
        ActionPolicy.SESSION_INVALIDATE,
    ],
    # Audit 무결성 - 심각 (IP 영구 차단)
    ViolationType.AUDIT_TAMPERING: [
        ActionPolicy.EMERGENCY_LEVEL_2,
        ActionPolicy.IP_PERMANENT_BAN,
    ],
    ViolationType.HASH_CHAIN_BROKEN: [
        ActionPolicy.EMERGENCY_LEVEL_2,
        ActionPolicy.BLOCK_AND_LOG,
    ],
    ViolationType.WAL_CORRUPTION: [
        ActionPolicy.EMERGENCY_LEVEL_1,
        ActionPolicy.BLOCK_AND_LOG,
    ],
    # CorruptionShield / 이상 감지
    ViolationType.ANOMALY_STATISTICAL: [
        ActionPolicy.BLOCK_AND_LOG,
    ],
    ViolationType.ANOMALY_BEHAVIORAL: [
        ActionPolicy.BLOCK_AND_LOG,
        ActionPolicy.IP_TEMPORARY_BAN,
    ],
    ViolationType.UNAUTHORIZED_OVERRIDE: [
        ActionPolicy.SESSION_INVALIDATE,
        ActionPolicy.BLOCK_AND_LOG,
    ],
    ViolationType.BUSINESS_RULE_VIOLATION: [
        ActionPolicy.BLOCK_AND_LOG,
    ],
    ViolationType.SCHEMA_VIOLATION: [
        ActionPolicy.BLOCK_AND_LOG,
    ],
}
