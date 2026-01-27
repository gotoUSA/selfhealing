"""
Emergency Coordination Models.

긴급 상황 조율 레이어의 데이터 모델 정의.

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from selfhealing.services.emergency_mode.enums import EmergencyLevel

from .enums import ActionType, EmergencyScope


@dataclass
class CoordinationAction:
    """
    연계 액션 정의.

    Emergency 상황에서 실행되는 개별 액션을 나타냅니다.

    Code reference:
        circuit_breaker.py#L567 (ttl_minutes 패턴)
    """

    type: ActionType
    """액션 유형."""

    immediate: bool = False
    """즉시 실행 여부. True면 delay_seconds 무시."""

    delay_seconds: int = 0
    """지연 시간 (초). immediate=False일 때만 적용."""

    params: dict[str, Any] = field(default_factory=dict)
    """추가 파라미터 (예: multiplier, reason_prefix)."""

    # 신규: TTL 강제 (3가지 보완사항 ①)
    ttl_minutes: int | None = None
    """
    액션 유효 시간 (분).

    오버라이드 액션에 TTL을 강제하여 '좀비 오버라이드' 방지.
    None이면 OverrideTTLConfig.default_override_ttl_minutes 적용.

    Code reference:
        circuit_breaker.py#L567 (ttl_minutes: int = 90 패턴)
    """

    # Dry-Run 지원
    is_dry_run: bool = False
    """
    True면 실제 실행하지 않고 감사 로그에만 기록.

    용도:
    - 프로덕션 적용 초기 검증
    - 정책 변경 전 영향 분석

    Code reference:
        urls.py#L301-302 (dry-run 엔드포인트)
    """

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        result = asdict(self)
        result["type"] = self.type.value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoordinationAction:
        """딕셔너리에서 생성."""
        data = dict(data)
        if "type" in data and isinstance(data["type"], str):
            data["type"] = ActionType(data["type"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ScopedEmergencyState:
    """
    네임스페이스별 Emergency 상태.

    리전 격리를 지원하기 위해 각 네임스페이스마다 독립적인 상태를 유지합니다.
    """

    namespace: str
    """네임스페이스 (예: 'seoul', 'tokyo', 'oregon')."""

    emergency_level: EmergencyLevel = EmergencyLevel.NORMAL
    """현재 Emergency 레벨."""

    governance_mode: str = "NORMAL"
    """현재 Governance 모드 ('STRICT' 또는 'NORMAL')."""

    scope: EmergencyScope = EmergencyScope.REGIONAL
    """적용 범위."""

    activated_at: datetime | None = None
    """활성화 시각."""

    activated_by: str | None = None
    """활성화한 주체 (admin ID 또는 'system')."""

    reason: str | None = None
    """활성화 사유."""

    # TTL 관련
    expires_at: datetime | None = None
    """자동 만료 시각."""

    # 메타데이터
    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""

    def is_active(self) -> bool:
        """Emergency 상태가 활성화되어 있는지 확인."""
        return self.emergency_level != EmergencyLevel.NORMAL

    def is_expired(self) -> bool:
        """만료 여부 확인."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        result = {
            "namespace": self.namespace,
            "emergency_level": self.emergency_level.value,
            "governance_mode": self.governance_mode,
            "scope": self.scope.value,
            "activated_at": (
                self.activated_at.isoformat() if self.activated_at else None
            ),
            "activated_by": self.activated_by,
            "reason": self.reason,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": self.metadata,
        }
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScopedEmergencyState:
        """딕셔너리에서 생성."""
        data = dict(data)

        if "emergency_level" in data:
            data["emergency_level"] = EmergencyLevel(data["emergency_level"])
        if "scope" in data:
            data["scope"] = EmergencyScope(data["scope"])
        if "activated_at" in data and isinstance(data["activated_at"], str):
            data["activated_at"] = datetime.fromisoformat(data["activated_at"])
        if "expires_at" in data and isinstance(data["expires_at"], str):
            data["expires_at"] = datetime.fromisoformat(data["expires_at"])

        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class OverrideTTLConfig:
    """
    오버라이드 TTL 강제 설정.

    Admin이 수동 오버라이드 후 깜빡 잊었을 때 '좀비 오버라이드'를 방지합니다.

    Code reference:
        circuit_breaker.py#L567 (manual_override TTL 패턴)
        beat_schedule.py#L192-195 (expire_manual_overrides 태스크)
    """

    # 기본 오버라이드 TTL (분)
    default_override_ttl_minutes: int = 120  # 2시간
    """
    Admin이 TTL을 명시하지 않을 경우 적용되는 기본값.
    Circuit Breaker의 90분보다 길게 설정 (더 신중한 복구).
    """

    # 최대 허용 TTL (분)
    max_override_ttl_minutes: int = 480  # 8시간
    """아무리 길게 설정해도 8시간을 초과할 수 없음."""

    # TTL 없는 영구 오버라이드 허용 여부
    allow_permanent_override: bool = False
    """
    False (권장): 모든 오버라이드에 TTL 강제
    True: 특수 권한(super_admin)만 영구 오버라이드 가능
    """

    # 영구 오버라이드 허용 역할
    permanent_override_roles: list[str] = field(default_factory=lambda: ["super_admin"])

    def enforce_ttl(
        self,
        requested_ttl: int | None,
        role: str = "operator",
    ) -> int:
        """
        TTL 강제 적용.

        Args:
            requested_ttl: 요청된 TTL (분)
            role: 요청자 역할

        Returns:
            적용될 TTL (분)

        Raises:
            ValueError: 영구 오버라이드 불가 시
        """
        # 영구 오버라이드 요청 확인
        if requested_ttl is None or requested_ttl <= 0:
            if self.allow_permanent_override and role in self.permanent_override_roles:
                return 0  # 영구 오버라이드 허용
            # 기본 TTL 적용
            return self.default_override_ttl_minutes

        # 최대 TTL 제한
        if requested_ttl > self.max_override_ttl_minutes:
            return self.max_override_ttl_minutes

        return requested_ttl


@dataclass
class ActionResult:
    """
    액션 실행 결과.
    """

    success: bool
    """실행 성공 여부."""

    action_type: ActionType
    """실행된 액션 유형."""

    event_id: str
    """생성된 이벤트 ID."""

    namespace: str
    """대상 네임스페이스."""

    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """실행 시각."""

    was_dry_run: bool = False
    """Dry-Run 모드 여부."""

    error: str | None = None
    """에러 메시지 (실패 시)."""

    details: dict[str, Any] = field(default_factory=dict)
    """추가 세부사항."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "success": self.success,
            "action_type": self.action_type.value,
            "event_id": self.event_id,
            "namespace": self.namespace,
            "executed_at": self.executed_at.isoformat(),
            "was_dry_run": self.was_dry_run,
            "error": self.error,
            "details": self.details,
        }


@dataclass
class CoordinationResult:
    """
    전체 조율 결과.

    Emergency 이벤트에 대한 모든 연계 액션의 결과를 포함합니다.
    """

    success: bool
    """전체 성공 여부."""

    cascade_event_id: str
    """Cascade Event ID (감사 추적용)."""

    executed_actions: list[ActionResult] = field(default_factory=list)
    """실행된 액션 결과 목록."""

    trigger_type: str = ""
    """트리거 유형."""

    namespace: str = ""
    """대상 네임스페이스."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "success": self.success,
            "cascade_event_id": self.cascade_event_id,
            "executed_actions": [a.to_dict() for a in self.executed_actions],
            "trigger_type": self.trigger_type,
            "namespace": self.namespace,
        }


# =============================================================================
# Phase 4: Recovery Accountability (72번 문서 §5.4.1)
# =============================================================================


@dataclass
class RecoveryAccountabilityConfig:
    """
    복구 책임 추적 설정.

    대규모 장애 후 자동 복구 시 "왜 사람이 확인하지 않았는가"에 대한
    책임 추적성을 보장합니다.

    Code reference:
        governance.py#L404 (acknowledge_warning 패턴)

    Reference:
        72_EMERGENCY_COORDINATION_LAYER.md#§5.4.1
    """

    requires_manual_acknowledgement: bool = False
    """
    수동 승인 필수 여부.

    True일 때:
    - 8시간 경과해도 자동 NORMAL 복구하지 않음
    - READY_TO_RESTORE 상태로 전환
    - Admin의 acknowledge() 호출 시에만 복구 완료
    """

    ready_to_restore_timeout_hours: int = 24
    """READY_TO_RESTORE 상태 최대 유지 시간. 초과 시 알림 에스컬레이션."""

    acknowledgement_required_roles: list[str] = field(
        default_factory=lambda: ["admin", "sre_lead"]
    )
    """복구 승인 가능 역할."""

    auto_restore_after_hours: float = 8.0
    """자동 복구까지 대기 시간 (시간). requires_manual_acknowledgement=False일 때만 적용."""

    escalation_channels: list[str] = field(
        default_factory=lambda: ["slack", "pagerduty"]
    )
    """에스컬레이션 알림 채널."""

    def can_acknowledge(self, role: str) -> bool:
        """
        역할이 복구 승인 가능한지 확인.

        Args:
            role: 사용자 역할

        Returns:
            승인 가능 여부
        """
        return role in self.acknowledgement_required_roles

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "requires_manual_acknowledgement": self.requires_manual_acknowledgement,
            "ready_to_restore_timeout_hours": self.ready_to_restore_timeout_hours,
            "acknowledgement_required_roles": self.acknowledgement_required_roles,
            "auto_restore_after_hours": self.auto_restore_after_hours,
            "escalation_channels": self.escalation_channels,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryAccountabilityConfig:
        """딕셔너리에서 생성."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
