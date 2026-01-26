"""
RequestAuditBuffer - 요청별 Audit 이벤트 버퍼

각 HTTP 요청의 전 생애주기에서 발생하는 Audit 이벤트를 수집합니다.
request.META에 저장되어 미들웨어 체인 전체에서 접근 가능하며,
AuditMiddleware에서 응답 직전에 일괄 기록됩니다.

핵심 설계 원칙:
- 모든 미들웨어와 서비스가 '직접 로깅' 대신 '버퍼에 적재'
- AuditMiddleware가 응답 직전에 버퍼를 '낚아채서' 단일 해시 체인으로 기록
- 이를 통해 "단 하나의 로그도 누락되거나 조작되지 않았다"를 증명

업계 사례:
- AWS CloudTrail: 이벤트 버퍼링 후 일괄 전송
- Datadog APM: Span 수집 후 Trace 완료 시 전송
- OpenTelemetry: SpanProcessor의 OnEnd에서 일괄 처리

Usage:
    # 미들웨어나 서비스에서 이벤트 적재
    from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType

    buffer = RequestAuditBuffer.get_or_create(request)
    buffer.add(
        event_type=AuditEventType.DLQ_STORE,
        source="DLQService",
        details={"dlq_id": 123, "domain": "payment"},
    )

    # AuditMiddleware에서 자동 수집 및 기록됨

Author: SelfHealing Team
Version: 1.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest


class AuditEventType(Enum):
    """
    Audit 이벤트 유형.

    각 유형은 AuditAction과 매핑되어 ContinuousAuditRecorder에 기록됩니다.
    """

    # DLQ 관련
    DLQ_STORE = "dlq_store"
    DLQ_REPLAY = "dlq_replay"
    DLQ_ESCALATE = "dlq_escalate"

    # Circuit Breaker 관련
    CB_STATE_CHANGE = "circuit_breaker_state_change"
    CB_REJECTION = "circuit_breaker_rejection"
    CB_RECOVERY = "circuit_breaker_recovery"

    # Governance 관련
    GOVERNANCE_BLOCKED = "governance_blocked"
    GOVERNANCE_KILL_SWITCH = "governance_kill_switch"

    # Rate Limit 관련
    RATE_LIMITED = "rate_limited"

    # Pool Circuit Breaker 관련
    POOL_CB_REJECTION = "pool_circuit_breaker_rejection"
    POOL_CB_STATE_CHANGE = "pool_circuit_breaker_state_change"

    # 에러 및 시스템 관련
    ERROR_DETECTED = "error_detected"
    CONFIG_CHANGE = "config_change"
    MANUAL_OVERRIDE = "manual_override"

    # API 예외 관련 - DRF 예외 핸들러에서 사용
    API_EXCEPTION = "api_exception"
    """API 요청 처리 중 예외 발생 (일반 예외)."""

    API_VALIDATION_ERROR = "api_validation_error"
    """입력값 검증 실패 (ValidationError, ValueError 등)."""

    API_AUTH_ERROR = "api_auth_error"
    """인증/인가 실패 (AuthenticationFailed, PermissionDenied 등)."""

    API_NOT_FOUND = "api_not_found"
    """리소스를 찾을 수 없음 (Http404, NotFound 예외)."""

    API_THROTTLED = "api_throttled"
    """요청 제한 초과 (Throttled 예외)."""

    # 복구 관련
    RECOVERY_EVENT = "recovery_event"
    RECOVERY_CHAIN_STARTED = "recovery_chain_started"
    RECOVERY_CHAIN_COMPLETED = "recovery_chain_completed"

    # 재시도 관련
    RETRY_ATTEMPTED = "retry_attempted"
    RETRY_EXHAUSTED = "retry_exhausted"

    # 시스템 제어 관련
    SYSTEM_CONTROL_CHANGED = "system_control_changed"

    # 롤백 관련
    ROLLBACK_PERFORMED = "rollback_performed"

    # ═══════════════════════════════════════════════════════════
    # Security Violation 관련 (85_AUDIT_INTEGRATION_OVERVIEW.md Phase 1)
    # ═══════════════════════════════════════════════════════════
    SECURITY_VIOLATION = "security_violation"
    """보안 위반 감지 및 처리."""

    SECURITY_IP_BLOCKED = "security_ip_blocked"
    """IP 차단 (임시 또는 영구)."""

    SECURITY_SESSION_INVALIDATED = "security_session_invalidated"
    """사용자 세션 무효화."""

    # ═══════════════════════════════════════════════════════════
    # Regional Isolation 관련 (85_AUDIT_INTEGRATION_OVERVIEW.md Phase 1)
    # ═══════════════════════════════════════════════════════════
    REGION_ISOLATED = "region_isolated"
    """리전 격리 활성화."""

    REGION_RESTORED = "region_restored"
    """리전 격리 해제."""

    # Chaos 실험 관련
    CHAOS_EXPERIMENT_STARTED = "chaos_experiment_started"
    CHAOS_EXPERIMENT_COMPLETED = "chaos_experiment_completed"
    CHAOS_INJECTION_APPLIED = "chaos_injection_applied"
    CHAOS_ROLLBACK_TRIGGERED = "chaos_rollback_triggered"

    # Emergency Mode 관련
    EMERGENCY_MODE_ACTIVATED = "emergency_mode_activated"
    EMERGENCY_MODE_DEACTIVATED = "emergency_mode_deactivated"

    # Error Budget 관련
    ERROR_BUDGET_DEPLETED = "error_budget_depleted"
    ERROR_BUDGET_BLOCKED = "error_budget_blocked"

    # Compliance 관련
    COMPLIANCE_VIOLATION = "compliance_violation"
    COMPLIANCE_CHECK_PASSED = "compliance_check_passed"

    # Blast Radius 관련
    BLAST_RADIUS_ISOLATION = "blast_radius_isolation"
    BLAST_RADIUS_VIOLATION = "blast_radius_violation"

    # FinOps 관련
    FINOPS_THRESHOLD_EXCEEDED = "finops_threshold_exceeded"
    FINOPS_BUDGET_EXCEEDED = "finops_budget_exceeded"

    # 데이터 접근 (ADR-002: 설정 기반)
    DATA_ACCESS = "data_access"

    # ═══════════════════════════════════════════════════════════
    # CorruptionShield 관련 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
    # ═══════════════════════════════════════════════════════════
    CORRUPTION_DETECTED = "corruption_detected"
    """데이터 무결성 위반 발견 (L1/L2/L3)."""

    CORRUPTION_BLOCKED = "corruption_blocked"
    """무결성 위반으로 요청 차단."""

    # ═══════════════════════════════════════════════════════════
    # ShadowLogger/L2 Sync 관련 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
    # ═══════════════════════════════════════════════════════════
    SHADOW_LOG_SYNC_FAILED = "shadow_log_sync_failed"
    """L2 동기화 실패 기록."""

    SHADOW_LOG_RECOVERED = "shadow_log_recovered"
    """L2 복구 후 재동기화 완료."""

    # ═══════════════════════════════════════════════════════════
    # WAL 관련 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
    # ═══════════════════════════════════════════════════════════
    WAL_CORRUPTION_DETECTED = "wal_corruption_detected"
    """WAL CRC32 체크섬 불일치 발견."""

    WAL_RECOVERED = "wal_recovered"
    """WAL 미처리 엔트리 복구 완료."""

    WAL_ROTATED = "wal_rotated"
    """WAL 파일 로테이션 발생."""

    # ═══════════════════════════════════════════════════════════
    # Forensic 관련 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
    # ═══════════════════════════════════════════════════════════
    FORENSIC_CAPTURE_STARTED = "forensic_capture_started"
    """Forensic 캡처 시작."""

    FORENSIC_CAPTURE_COMPLETED = "forensic_capture_completed"
    """Forensic 캡처 완료."""

    FORENSIC_ANOMALY_DETECTED = "forensic_anomaly_detected"
    """Forensic 분석 중 이상 패턴 발견."""

    # ═══════════════════════════════════════════════════════════
    # Reconciliation 관련 (30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md)
    # ═══════════════════════════════════════════════════════════
    FAILSAFE_PERIOD_STARTED = "failsafe_period_started"
    """Fail-Safe 기간 시작."""

    FAILSAFE_PERIOD_ENDED = "failsafe_period_ended"
    """Fail-Safe 기간 종료."""

    SHADOW_BUDGET_CALCULATED = "shadow_budget_calculated"
    """Shadow Budget 계산 완료."""

    RECONCILIATION_APPROVED = "reconciliation_approved"
    """Reconciliation 승인됨."""

    RECONCILIATION_REJECTED = "reconciliation_rejected"
    """Reconciliation 거부됨."""

    RECONCILIATION_ACCURACY_VERIFIED = "reconciliation_accuracy_verified"
    """Reconciliation 정확도 사후 검증 완료."""

    PENDING_RECONCILIATION_FREEZE = "pending_reconciliation_freeze"
    """대규모 조정으로 인한 배포 동결."""

    # 일반
    GENERIC = "generic"


@dataclass
class AuditEvent:
    """
    단일 Audit 이벤트.

    요청 처리 중 발생하는 각 이벤트를 캡처합니다.
    RequestAuditBuffer에 적재되어 AuditMiddleware에서 일괄 처리됩니다.
    """

    event_type: AuditEventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "unknown"
    details: Dict[str, Any] = field(default_factory=dict)
    actor_id: Optional[str] = None
    actor_type: str = "system"
    success: bool = True
    error_message: Optional[str] = None

    # 추가 메타데이터
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    domain: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """직렬화를 위한 딕셔너리 변환."""
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "details": self.details,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "success": self.success,
            "error_message": self.error_message,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "domain": self.domain,
            "reason": self.reason,
        }

    def __repr__(self) -> str:
        return f"AuditEvent(type={self.event_type.value}, " f"source={self.source}, success={self.success})"


class RequestAuditBuffer:
    """
    요청별 Audit 이벤트 버퍼.

    request.META에 저장되어 미들웨어 체인 전체에서 이벤트 수집.
    AuditMiddleware에서 최종 기록.

    설계 포인트:
    - 이 버퍼는 '영수증'과 같음 - 한 요청의 전 생애주기를 기록
    - Thread-safe: 단일 요청 내에서는 동기적으로 처리됨
    - 메모리 효율: 요청 완료 시 자동 정리

    사용 예시:
        # 1. 버퍼 가져오기/생성
        buffer = RequestAuditBuffer.get_or_create(request)

        # 2. 이벤트 추가
        buffer.add(
            event_type=AuditEventType.CB_STATE_CHANGE,
            source="SelfHealingMiddleware",
            details={"cb_name": "payment", "new_state": "open"},
        )

        # 3. AuditMiddleware에서 자동 처리
    """

    # request.META에 저장될 키
    META_KEY = "X-AUDIT-EVENTS"

    def __init__(self):
        self.events: List[AuditEvent] = []
        self.request_id: Optional[str] = None
        self.start_time: datetime = datetime.now(timezone.utc)

        # 요청 메타데이터
        self._path: Optional[str] = None
        self._method: Optional[str] = None
        self._user_id: Optional[str] = None

    def add_event(self, event: AuditEvent) -> None:
        """이벤트 직접 추가."""
        self.events.append(event)

    def add(
        self,
        event_type: AuditEventType,
        source: str,
        details: Optional[Dict[str, Any]] = None,
        actor_id: Optional[str] = None,
        actor_type: str = "system",
        success: bool = True,
        error_message: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        domain: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> AuditEvent:
        """
        편의 메서드: 이벤트 생성 및 추가.

        Args:
            event_type: 이벤트 유형
            source: 이벤트 발생 위치 (미들웨어명, 서비스명 등)
            details: 추가 상세 정보
            actor_id: 행위자 ID (없으면 ActorContext에서 자동 추출)
            actor_type: 행위자 유형 (system, user, scheduler 등)
            success: 성공 여부
            error_message: 실패 시 에러 메시지
            target_type: 대상 유형 (circuit_breaker, dlq_entry 등)
            target_id: 대상 ID
            domain: 비즈니스 도메인
            reason: 이벤트 사유

        Returns:
            생성된 AuditEvent
        """
        # ActorContext에서 actor 정보 자동 추출 시도
        if actor_id is None:
            try:
                from selfhealing.context.actor_context import ActorContext

                if ActorContext.is_set():
                    actor = ActorContext.get_current()
                    actor_id = actor.actor_id
                    actor_type = actor.actor_type
            except ImportError:
                pass

        event = AuditEvent(
            event_type=event_type,
            source=source,
            details=details or {},
            actor_id=actor_id,
            actor_type=actor_type,
            success=success,
            error_message=error_message,
            target_type=target_type,
            target_id=target_id,
            domain=domain,
            reason=reason,
        )
        self.events.append(event)
        return event

    def get_events(self) -> List[AuditEvent]:
        """모든 이벤트 반환 (복사본)."""
        return self.events.copy()

    def has_events(self) -> bool:
        """이벤트 존재 여부."""
        return len(self.events) > 0

    def event_count(self) -> int:
        """이벤트 개수."""
        return len(self.events)

    def get_events_by_type(self, event_type: AuditEventType) -> List[AuditEvent]:
        """특정 유형의 이벤트만 반환."""
        return [e for e in self.events if e.event_type == event_type]

    def get_failed_events(self) -> List[AuditEvent]:
        """실패 이벤트만 반환."""
        return [e for e in self.events if not e.success]

    def has_event_from_source(self, source: str) -> bool:
        """
        특정 source에서 기록한 이벤트가 존재하는지 확인.

        AuditMiddleware에서 중복 기록 방지에 사용됩니다.
        예: ExceptionHandler가 이미 예외를 기록했으면 ERROR_DETECTED 스킵.

        Args:
            source: 이벤트 발생 위치 (ExceptionHandler, AuditMiddleware 등)

        Returns:
            해당 source의 이벤트 존재 여부
        """
        return any(e.source == source for e in self.events)

    def set_request_metadata(
        self,
        path: Optional[str] = None,
        method: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """요청 메타데이터 설정."""
        if path is not None:
            self._path = path
        if method is not None:
            self._method = method
        if user_id is not None:
            self._user_id = user_id

    def get_elapsed_seconds(self) -> float:
        """요청 시작부터 경과 시간 (초)."""
        return (datetime.now(timezone.utc) - self.start_time).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        """버퍼 전체를 딕셔너리로 변환."""
        return {
            "request_id": self.request_id,
            "start_time": self.start_time.isoformat(),
            "elapsed_seconds": self.get_elapsed_seconds(),
            "path": self._path,
            "method": self._method,
            "user_id": self._user_id,
            "event_count": len(self.events),
            "events": [e.to_dict() for e in self.events],
        }

    def clear(self) -> None:
        """버퍼 초기화 (테스트용)."""
        self.events.clear()

    # =========================================================================
    # Class Methods - request에서 버퍼 관리
    # =========================================================================

    @classmethod
    def get_or_create(cls, request: "HttpRequest") -> "RequestAuditBuffer":
        """
        request에서 버퍼 가져오거나 새로 생성.

        Args:
            request: Django HttpRequest 객체

        Returns:
            RequestAuditBuffer 인스턴스

        Usage:
            buffer = RequestAuditBuffer.get_or_create(request)
            buffer.add(event_type=..., source=..., details=...)
        """
        if not hasattr(request, "META"):
            # request 객체가 이상한 경우 새 버퍼 반환
            return cls()

        if cls.META_KEY not in request.META:
            request.META[cls.META_KEY] = cls()

        return request.META[cls.META_KEY]

    @classmethod
    def get(cls, request: "HttpRequest") -> Optional["RequestAuditBuffer"]:
        """
        request에서 기존 버퍼 가져오기 (없으면 None).

        Args:
            request: Django HttpRequest 객체

        Returns:
            RequestAuditBuffer 또는 None
        """
        if not hasattr(request, "META"):
            return None
        return request.META.get(cls.META_KEY)

    @classmethod
    def exists(cls, request: "HttpRequest") -> bool:
        """request에 버퍼가 존재하는지 확인."""
        if not hasattr(request, "META"):
            return False
        return cls.META_KEY in request.META


# =============================================================================
# 편의 함수
# =============================================================================


def add_audit_event(
    request: "HttpRequest",
    event_type: AuditEventType,
    source: str,
    details: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Optional[AuditEvent]:
    """
    요청에 Audit 이벤트 추가 (편의 함수).

    request 객체가 유효하지 않으면 None 반환.

    Args:
        request: Django HttpRequest 객체
        event_type: 이벤트 유형
        source: 이벤트 발생 위치
        details: 추가 상세 정보
        **kwargs: AuditEvent 추가 파라미터

    Returns:
        생성된 AuditEvent 또는 None

    Usage:
        from selfhealing.audit.event_buffer import add_audit_event, AuditEventType

        add_audit_event(
            request,
            AuditEventType.CB_STATE_CHANGE,
            "SelfHealingMiddleware",
            details={"cb_name": "payment", "new_state": "open"},
        )
    """
    try:
        buffer = RequestAuditBuffer.get_or_create(request)
        return buffer.add(
            event_type=event_type,
            source=source,
            details=details,
            **kwargs,
        )
    except Exception:
        return None
