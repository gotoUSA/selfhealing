"""
Security Violation Types and Severity Enums.

Contains domain-neutral security violation type definitions
and severity level mappings.
"""

from __future__ import annotations

from enum import Enum


class ViolationType(str, Enum):
    """Types of security violations that never self-heal (domain-neutral)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # 기존 ViolationType
    # ═══════════════════════════════════════════════════════════════════════════
    SIGNATURE_INVALID = "signature_invalid"
    DATA_TAMPERED = "data_tampered"
    TOKEN_FORGED = "token_forged"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_ABUSE = "rate_limit_abuse"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    REPLAY_ATTACK = "replay_attack"
    INJECTION_ATTEMPT = "injection_attempt"

    # ═══════════════════════════════════════════════════════════════════════════
    # Self-Healing 루프 감지 관련
    # 복구 무한 루프, 상충 조정, 타임아웃, 플래핑 탐지
    # ═══════════════════════════════════════════════════════════════════════════
    RECOVERY_LOOP_DETECTED = "recovery_loop_detected"
    """복구/조정 무한 루프 감지 - 가장 심각."""

    CONFLICTING_ADJUSTMENT = "conflicting_adjustment"
    """상충하는 자율 조정 감지 (예: A→B→A 반복)."""

    HEALING_TIMEOUT = "healing_timeout"
    """Self-Healing 작업 시간 초과."""

    FLAPPING_DETECTED = "flapping_detected"
    """파라미터 플래핑 감지 (미세 조정 반복)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # CorruptionShield / 이상 감지 관련
    # 통계적 이상, 행위 이상, 스키마 위반, 비즈니스 규칙 위반 탐지
    # ═══════════════════════════════════════════════════════════════════════════
    ANOMALY_STATISTICAL = "anomaly_statistical"
    """L3 통계적 이상 감지 (Z-score 기반)."""

    ANOMALY_BEHAVIORAL = "anomaly_behavioral"
    """행위 이상 감지 (시퀀스 패턴 이탈)."""

    SCHEMA_VIOLATION = "schema_violation"
    """L1 스키마 위반 (필수 필드 누락, 타입 불일치)."""

    BUSINESS_RULE_VIOLATION = "business_rule_violation"
    """L2 비즈니스 규칙 위반."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Audit 무결성 관련
    # 감사 로그 조작 시도 및 해시 체인 무결성 위반 탐지
    # ═══════════════════════════════════════════════════════════════════════════
    AUDIT_TAMPERING = "audit_tampering"
    """Audit 로그 조작 시도 감지."""

    HASH_CHAIN_BROKEN = "hash_chain_broken"
    """ContinuousAuditRecorder 해시 체인 무결성 위반."""

    WAL_CORRUPTION = "wal_corruption"
    """WAL CRC32 체크섬 불일치."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Governance 위반 관련 (순위 1 - v2.3.0)
    # ═══════════════════════════════════════════════════════════════════════════
    UNAUTHORIZED_OVERRIDE = "unauthorized_override"
    """권한 없는 설정 변경 시도."""

    GOVERNANCE_BYPASS_ATTEMPT = "governance_bypass_attempt"
    """Kill Switch/Emergency Mode 우회 시도."""

    PRIVILEGE_ESCALATION = "privilege_escalation"
    """권한 상승 시도."""


class Severity(str, Enum):
    """Severity levels for security incidents."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


# Severity mapping for each violation type
SEVERITY_BY_VIOLATION_TYPE: dict[str, Severity] = {
    # 기존 ViolationType
    ViolationType.SIGNATURE_INVALID: Severity.CRITICAL,
    ViolationType.DATA_TAMPERED: Severity.CRITICAL,
    ViolationType.TOKEN_FORGED: Severity.CRITICAL,
    ViolationType.REPLAY_ATTACK: Severity.CRITICAL,
    ViolationType.UNAUTHORIZED_ACCESS: Severity.HIGH,
    ViolationType.INJECTION_ATTEMPT: Severity.HIGH,
    ViolationType.RATE_LIMIT_ABUSE: Severity.MEDIUM,
    ViolationType.SUSPICIOUS_ACTIVITY: Severity.MEDIUM,
    # Self-Healing 루프 감지 관련 (v2.0.0 - 순위 0.5)
    ViolationType.RECOVERY_LOOP_DETECTED: Severity.CRITICAL,
    ViolationType.CONFLICTING_ADJUSTMENT: Severity.HIGH,
    ViolationType.HEALING_TIMEOUT: Severity.MEDIUM,
    ViolationType.FLAPPING_DETECTED: Severity.HIGH,
    # ═══════════════════════════════════════════════════════════════════════════
    # 신규 ViolationType Severity (순위 2 - v2.3.0)
    # ═══════════════════════════════════════════════════════════════════════════
    # Audit 무결성 - 가장 심각 (즉시 차단)
    ViolationType.AUDIT_TAMPERING: Severity.CRITICAL,
    ViolationType.HASH_CHAIN_BROKEN: Severity.CRITICAL,
    ViolationType.WAL_CORRUPTION: Severity.CRITICAL,
    # Governance 위반 - 심각 (즉시 차단)
    ViolationType.GOVERNANCE_BYPASS_ATTEMPT: Severity.CRITICAL,
    ViolationType.PRIVILEGE_ESCALATION: Severity.CRITICAL,
    # CorruptionShield / 이상 감지 - HIGH (차단, DLQ 저장)
    ViolationType.ANOMALY_STATISTICAL: Severity.HIGH,
    ViolationType.ANOMALY_BEHAVIORAL: Severity.HIGH,
    ViolationType.UNAUTHORIZED_OVERRIDE: Severity.HIGH,
    ViolationType.BUSINESS_RULE_VIOLATION: Severity.HIGH,
    # 스키마 위반 - MEDIUM (로깅, 모니터링)
    ViolationType.SCHEMA_VIOLATION: Severity.MEDIUM,
}
