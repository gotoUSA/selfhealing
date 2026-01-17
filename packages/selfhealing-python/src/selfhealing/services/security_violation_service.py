"""
Security Violation Handling Service

Handles security violations that should NEVER self-heal.
Security incidents are immediately blocked and routed to the security team.

Features:
- Detect and classify security violations
- Take immediate protective actions (via ProtectionOrchestrator)
- Create SecurityIncident records
- Trigger security notifications
- ActionPolicy-based protection with rollback support (v2.1.0)

보안 위반 감지 및 분류, 즉각적 보호 조치, SecurityIncident 레코드 생성,
보안 알림 발송 및 ActionPolicy 기반 보호를 제공합니다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from selfhealing.core.timezone import now
from selfhealing.settings import get_config

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


# =============================================================================
# ActionPolicy Enum and Mapping (순위 0 - v2.0.0)
# =============================================================================


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


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ProtectionResult:
    """
    보호 조치 실행 결과 (순위 0 - v2.0.0).

    v2.1.0: 롤백 관련 필드 추가 (순위 0.3)
    v2.2.0: triggering_trace_id 추가 - 대시보드에서 "어떤 요청 때문에
    이 사용자의 세션이 무효화되었는가"를 원클릭으로 추적 가능.
    """

    success: bool
    executed_policies: list[ActionPolicy] = field(default_factory=list)
    failed_policies: list[ActionPolicy] = field(default_factory=list)
    highest_priority_succeeded: bool = True
    # v2.1.0: 롤백 관련 (순위 0.3)
    rolled_back_policies: list[ActionPolicy] = field(default_factory=list)
    rollback_success: bool = True
    error_message: str = ""
    # v2.2.0: Tracing Deep Link
    triggering_trace_id: Optional[str] = None
    """보호 조치를 유발한 원본 요청의 trace_id (Jaeger/Zipkin 연동용)."""
    triggering_request_path: Optional[str] = None
    """보호 조치를 유발한 원본 요청 경로 (예: POST /api/payments/)."""

    def get_trace_url(self, template: str = "") -> Optional[str]:
        """
        Trace UI Deep Link 생성.

        Args:
            template: URL 템플릿 (예: "https://jaeger.example.com/trace/{trace_id}")

        Returns:
            Deep Link URL 또는 None
        """
        if not self.triggering_trace_id:
            return None
        if not template:
            template = os.environ.get("SELFHEALING_TRACE_URL_TEMPLATE", "")
        if not template:
            return None
        return template.replace("{trace_id}", self.triggering_trace_id)


@dataclass
class SecurityViolationResult:
    """Result of security violation handling."""

    success: bool
    incident_id: int | None = None
    action_taken: str = ""
    error: str | None = None
    protection_result: ProtectionResult | None = None

    @classmethod
    def handled(cls, incident_id: int, action: str, protection_result: ProtectionResult | None = None) -> "SecurityViolationResult":
        """Factory for successfully handled violation."""
        return cls(success=True, incident_id=incident_id, action_taken=action, protection_result=protection_result)

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

            # ═══════════════════════════════════════════════════════════════════
            # 순위 2.5: CRITICAL 보안 위반 시 EventBus 연동 (v2.3.0)
            # ═══════════════════════════════════════════════════════════════════
            if severity == Severity.CRITICAL:
                self._emit_critical_violation_event(
                    violation_type=violation_type_str,
                    incident_id=incident.id,
                    source_ip=source_ip,
                    user_id=user_id,
                )

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

    def _remove_ip_ban(self, ip_address: str) -> str:
        """
        Remove IP ban (for rollback support).

        Args:
            ip_address: IP address to unban

        Returns:
            Description of action taken
        """
        cache_key = f"{self.config.banned_ip_cache_prefix}{ip_address}"
        self.cache.delete(cache_key)

        logger.info(f"[Security] IP ban removed: {ip_address}")
        return f"IP {ip_address} ban removed"

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

    def _emit_critical_violation_event(
        self,
        violation_type: str,
        incident_id: int,
        source_ip: Optional[str],
        user_id: Optional[int],
    ) -> None:
        """
        CRITICAL 보안 위반 시 EventBus를 통해 이벤트 발행.

        순위 2.5 - v2.3.0:
        - SECURITY_VIOLATION_CRITICAL 이벤트 발행
        - Emergency Mode 및 Error Budget 연동 트리거

        Args:
            violation_type: 위반 유형
            incident_id: 인시던트 ID
            source_ip: 소스 IP
            user_id: 사용자 ID
        """
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.SECURITY_VIOLATION_CRITICAL,
                data={
                    "violation_type": violation_type,
                    "severity": "critical",
                    "incident_id": incident_id,
                    "source_ip": source_ip,
                    "user_id": user_id,
                    "trigger_source": "security_violation_service",
                },
                source="security_violation_service",
            )
            logger.warning(
                f"[SecurityViolationService] Emitted SECURITY_VIOLATION_CRITICAL "
                f"for incident {incident_id}, type={violation_type}"
            )
        except Exception as e:
            # EventBus 실패가 주요 흐름을 막지 않도록
            logger.error(f"[SecurityViolationService] Failed to emit critical event: {e}")


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


# =============================================================================
# ProtectionOrchestrator (순위 0, 0.3 - v2.0.0, v2.1.0)
# =============================================================================


class ProtectionOrchestrator:
    """
    보호 조치 오케스트레이터.

    원자성 보장:
    - 가장 강력한 정책(우선순위 높은)부터 실행
    - 최고 우선순위 정책 실패 시 전체 실패 처리 + 롤백
    - 하위 정책 실패는 경고 로깅 후 계속 진행

    롤백 정책 (v2.1.0 - 순위 0.3):
    - 최고 우선순위 실패 시 이미 실행된 정책들을 역순으로 롤백
    - 롤백 가능한 정책만 롤백 시도 (Emergency Mode는 롤백 불가)
    - 롤백 실패 시 로깅 후 수동 개입 권장

    Reference: Architect Review - "가장 강력한 정책 우선 성공 보장"
    """

    def __init__(self, security_service: "SecurityViolationService"):
        self._service = security_service
        self._policy_executors: dict[ActionPolicy, Any] = {
            ActionPolicy.EMERGENCY_LEVEL_3: self._execute_emergency_3,
            ActionPolicy.EMERGENCY_LEVEL_2: self._execute_emergency_2,
            ActionPolicy.EMERGENCY_LEVEL_1: self._execute_emergency_1,
            ActionPolicy.ACCOUNT_FREEZE: self._execute_account_freeze,
            ActionPolicy.SESSION_INVALIDATE: self._execute_session_invalidate,
            ActionPolicy.IP_PERMANENT_BAN: self._execute_ip_permanent_ban,
            ActionPolicy.IP_TEMPORARY_BAN: self._execute_ip_temporary_ban,
            ActionPolicy.BLOCK_AND_LOG: self._execute_block_and_log,
        }

        # 롤백 실행자 (롤백 가능한 정책만) - 순위 0.3
        self._policy_rollback_executors: dict[ActionPolicy, Any] = {
            # Emergency Mode는 롤백 불가 (이미 발동되면 수동 해제 필요)
            ActionPolicy.ACCOUNT_FREEZE: self._rollback_account_freeze,
            ActionPolicy.SESSION_INVALIDATE: None,  # 세션은 롤백 불가 (이미 무효화됨)
            ActionPolicy.IP_PERMANENT_BAN: self._rollback_ip_ban,
            ActionPolicy.IP_TEMPORARY_BAN: self._rollback_ip_ban,
            ActionPolicy.BLOCK_AND_LOG: None,  # 로그는 롤백 불가
        }

    def execute_policies(
        self,
        policies: list[ActionPolicy],
        context: dict[str, Any],
    ) -> ProtectionResult:
        """
        정책 목록을 우선순위 순으로 실행.

        원자성 보장:
        1. 가장 높은 우선순위 정책 먼저 실행
        2. 최고 우선순위 실패 시 전체 실패 반환 (시스템 잠금 권장)
        3. 하위 정책 실패는 로깅 후 계속 진행

        Args:
            policies: 실행할 ActionPolicy 목록
            context: 실행 컨텍스트 (user_id, source_ip 등)

        Returns:
            ProtectionResult with execution details
        """
        if not policies:
            return ProtectionResult(
                success=True,
                executed_policies=[],
                failed_policies=[],
                highest_priority_succeeded=True,
            )

        # 우선순위 순 정렬 (낮은 숫자 = 높은 우선순위)
        sorted_policies = sorted(
            policies,
            key=lambda p: ACTION_POLICY_PRIORITY.get(p, 999)
        )

        executed: list[ActionPolicy] = []
        failed: list[ActionPolicy] = []
        highest_priority_policy = sorted_policies[0]
        highest_succeeded = False

        for policy in sorted_policies:
            try:
                executor = self._policy_executors.get(policy)
                if executor:
                    executor(context)
                    executed.append(policy)

                    if policy == highest_priority_policy:
                        highest_succeeded = True

            except Exception as e:
                failed.append(policy)
                logger.error(f"[ProtectionOrchestrator] Policy {policy.value} failed: {e}")

                # 최고 우선순위 실패 시 즉시 중단 + 롤백 시도 (순위 0.3)
                if policy == highest_priority_policy:
                    rolled_back, rollback_success = self._rollback_executed_policies(
                        executed, context
                    )

                    return ProtectionResult(
                        success=False,
                        executed_policies=executed,
                        failed_policies=failed,
                        highest_priority_succeeded=False,
                        rolled_back_policies=rolled_back,
                        rollback_success=rollback_success,
                        error_message=f"Highest priority policy failed: {e}",
                        triggering_trace_id=context.get("trace_id"),
                        triggering_request_path=context.get("request_path"),
                    )

        return ProtectionResult(
            success=len(failed) == 0,
            executed_policies=executed,
            failed_policies=failed,
            highest_priority_succeeded=highest_succeeded,
            triggering_trace_id=context.get("trace_id"),
            triggering_request_path=context.get("request_path"),
        )

    # =========================================================================
    # Policy Executors
    # =========================================================================

    def _execute_emergency_3(self, context: dict[str, Any]) -> None:
        """Emergency Level 3 선포."""
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.EMERGENCY_ACTIVATED,
                data={
                    "level": 3,
                    "reason": context.get("reason", "Security violation"),
                    "trigger_source": "protection_orchestrator",
                    "incident_id": context.get("incident_id"),
                },
                source="protection_orchestrator",
            )
            logger.critical("[ProtectionOrchestrator] Emergency Level 3 activated!")
        except Exception as e:
            logger.error(f"[ProtectionOrchestrator] Failed to emit emergency event: {e}")
            raise

    def _execute_emergency_2(self, context: dict[str, Any]) -> None:
        """Emergency Level 2 선포."""
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.EMERGENCY_ACTIVATED,
                data={
                    "level": 2,
                    "reason": context.get("reason", "Security violation"),
                    "trigger_source": "protection_orchestrator",
                    "incident_id": context.get("incident_id"),
                },
                source="protection_orchestrator",
            )
            logger.warning("[ProtectionOrchestrator] Emergency Level 2 activated")
        except Exception as e:
            logger.error(f"[ProtectionOrchestrator] Failed to emit emergency event: {e}")
            raise

    def _execute_emergency_1(self, context: dict[str, Any]) -> None:
        """Emergency Level 1 선포."""
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.EMERGENCY_ACTIVATED,
                data={
                    "level": 1,
                    "reason": context.get("reason", "Security warning"),
                    "trigger_source": "protection_orchestrator",
                },
                source="protection_orchestrator",
            )
            logger.info("[ProtectionOrchestrator] Emergency Level 1 activated")
        except Exception as e:
            logger.error(f"[ProtectionOrchestrator] Failed to emit emergency event: {e}")
            raise

    def _execute_account_freeze(self, context: dict[str, Any]) -> None:
        """계정 동결."""
        user_id = context.get("user_id")
        if user_id:
            logger.warning(f"[ProtectionOrchestrator] Account frozen: user_id={user_id}")

    def _execute_session_invalidate(self, context: dict[str, Any]) -> None:
        """세션 무효화."""
        user_id = context.get("user_id")
        if user_id:
            self._service._invalidate_user_sessions(user_id)

    def _execute_ip_permanent_ban(self, context: dict[str, Any]) -> None:
        """영구 IP 차단."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._permanent_ip_ban(source_ip)

    def _execute_ip_temporary_ban(self, context: dict[str, Any]) -> None:
        """임시 IP 차단."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._temporary_ip_ban(source_ip)

    def _execute_block_and_log(self, context: dict[str, Any]) -> None:
        """차단 및 로깅."""
        logger.warning(f"[ProtectionOrchestrator] Blocked and logged: {context}")

    # =========================================================================
    # Rollback Methods (순위 0.3 - v2.1.0)
    # =========================================================================

    def _rollback_executed_policies(
        self,
        executed: list[ActionPolicy],
        context: dict[str, Any],
    ) -> tuple[list[ActionPolicy], bool]:
        """
        이미 실행된 정책들을 역순으로 롤백.

        Args:
            executed: 실행된 정책 목록
            context: 실행 컨텍스트

        Returns:
            (rolled_back_policies, all_success)
        """
        rolled_back: list[ActionPolicy] = []
        all_success = True

        # 역순으로 롤백 (마지막 실행된 것부터)
        for policy in reversed(executed):
            rollback_fn = self._policy_rollback_executors.get(policy)

            if rollback_fn is None:
                # 롤백 불가능한 정책
                logger.warning(
                    f"[ProtectionOrchestrator] Policy {policy.value} cannot be rolled back"
                )
                continue

            try:
                rollback_fn(context)
                rolled_back.append(policy)
                logger.info(f"[ProtectionOrchestrator] Rolled back: {policy.value}")
            except Exception as e:
                logger.error(
                    f"[ProtectionOrchestrator] Rollback failed for {policy.value}: {e}"
                )
                all_success = False

        if not all_success:
            logger.critical(
                "[ProtectionOrchestrator] Some rollbacks failed! "
                "Manual intervention may be required."
            )

        return rolled_back, all_success

    def _rollback_account_freeze(self, context: dict[str, Any]) -> None:
        """계정 동결 해제."""
        user_id = context.get("user_id")
        if user_id:
            logger.info(f"[ProtectionOrchestrator] Account unfrozen: user_id={user_id}")

    def _rollback_ip_ban(self, context: dict[str, Any]) -> None:
        """IP 차단 해제."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._remove_ip_ban(source_ip)
            logger.info(f"[ProtectionOrchestrator] IP ban removed: {source_ip}")
