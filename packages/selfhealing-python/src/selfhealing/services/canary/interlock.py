"""
Canary Safety Interlock.

Emergency Level에 따라 Canary 롤아웃 작업을 자동 제동합니다.

주요 기능:
- Emergency Level 기반 자동 제동 (NORMAL, LEVEL_1, LEVEL_2, LEVEL_3)
- Fail-Closed 정책: Redis 장애 시 LEVEL_3로 간주하여 ROLLBACK
- InterlockAction: ALLOW, ALLOW_WITH_WARNING, PAUSE, ROLLBACK, BLOCK
- InterlockResult: 체크 결과 및 메타데이터

정책 매핑:
- NORMAL: ALLOW (정상 진행)
- LEVEL_1: ALLOW_WITH_WARNING (경고와 함께 진행)
- LEVEL_2: PAUSE (일시 중지)
- LEVEL_3: ROLLBACK (즉시 롤백)

Reference:
    docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md

Usage:
    from selfhealing.services.canary.interlock import (
        get_canary_safety_interlock,
        CanarySafetyInterlock,
        InterlockAction,
        InterlockResult,
    )
    
    interlock = get_canary_safety_interlock()
    
    # 작업 전 체크
    result = interlock.check(operation="promote", rollout_id="abc123")
    
    if not result.allowed:
        # PAUSE 또는 ROLLBACK 필요
        if result.action == InterlockAction.PAUSE:
            canary_service.pause(rollout_id)
        elif result.action == InterlockAction.ROLLBACK:
            canary_service.rollback(rollout_id)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.services.canary.service import CanaryRolloutService

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class InterlockAction(str, Enum):
    """
    Safety Interlock 액션.
    
    Canary 롤아웃 작업에 대해 Interlock이 결정하는 행동 유형.
    """
    
    ALLOW = "allow"
    """정상 진행 허용."""
    
    ALLOW_WITH_WARNING = "allow_with_warning"
    """경고 로그와 함께 진행 허용. LEVEL_1에서 사용."""
    
    PAUSE = "pause"
    """일시 중지. LEVEL_2에서 사용. 상황 해소 후 재개 가능."""
    
    ROLLBACK = "rollback"
    """즉시 롤백. LEVEL_3 또는 Fail-Closed에서 사용."""
    
    BLOCK = "block"
    """작업 차단. 새 롤아웃 시작 불가."""


class InterlockCheckFailure(str, Enum):
    """
    Interlock 체크 실패 유형.
    
    Fail-Closed 정책 적용 시 실패 원인을 기록합니다.
    """
    
    BACKEND_UNAVAILABLE = "backend_unavailable"
    """Redis/백엔드 연결 불가."""
    
    TRACKER_ERROR = "tracker_error"
    """EmergencyModeTracker 오류."""
    
    TIMEOUT = "timeout"
    """체크 타임아웃."""


# =============================================================================
# InterlockResult Dataclass
# =============================================================================


@dataclass
class InterlockResult:
    """
    Safety Interlock 체크 결과.
    
    Canary 작업에 대한 Interlock 검사 결과를 담습니다.
    
    Attributes:
        action: 수행할 액션 (ALLOW, PAUSE, ROLLBACK 등)
        allowed: 진행 허용 여부 (action이 ALLOW* 계열이면 True)
        reason: 액션 결정 사유
        emergency_level: 현재 Emergency 레벨
        namespace: 적용된 네임스페이스
        check_failure: 체크 실패 유형 (정상이면 None)
        is_fail_closed: Fail-Closed 정책으로 인한 결정인지 여부
    """
    
    action: InterlockAction
    """수행할 액션."""
    
    allowed: bool
    """진행 허용 여부."""
    
    reason: str
    """액션 사유."""
    
    emergency_level: int = 0
    """현재 Emergency 레벨 값 (0=NORMAL, 1=LEVEL_1, 2=LEVEL_2, 3=LEVEL_3)."""
    
    emergency_level_name: str = "NORMAL"
    """Emergency 레벨 이름."""
    
    namespace: str = ""
    """적용된 네임스페이스."""
    
    delay_seconds: int = 0
    """지연 시간 (PAUSE의 경우)."""
    
    auto_resume_at: Optional[str] = None
    """자동 재개 시각 (있는 경우)."""
    
    # Fail-Safe 관련 필드
    check_failure: Optional[InterlockCheckFailure] = None
    """체크 실패 유형 (정상이면 None)."""
    
    is_fail_closed: bool = False
    """Fail-Closed 정책으로 인한 차단인지 여부."""
    
    # 메타데이터
    metadata: Dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""
    
    # =========================================================================
    # Factory Methods
    # =========================================================================
    
    @classmethod
    def allow(
        cls,
        emergency_level: int,
        emergency_level_name: str,
        namespace: str,
    ) -> "InterlockResult":
        """
        허용 결과 팩토리.
        
        Args:
            emergency_level: Emergency 레벨 값
            emergency_level_name: Emergency 레벨 이름
            namespace: 네임스페이스
        
        Returns:
            ALLOW 액션의 InterlockResult
        """
        return cls(
            action=InterlockAction.ALLOW,
            allowed=True,
            reason="Emergency level allows operation",
            emergency_level=emergency_level,
            emergency_level_name=emergency_level_name,
            namespace=namespace,
        )
    
    @classmethod
    def allow_with_warning(
        cls,
        emergency_level: int,
        emergency_level_name: str,
        namespace: str,
    ) -> "InterlockResult":
        """
        경고와 함께 허용 결과 팩토리.
        
        Args:
            emergency_level: Emergency 레벨 값
            emergency_level_name: Emergency 레벨 이름
            namespace: 네임스페이스
        
        Returns:
            ALLOW_WITH_WARNING 액션의 InterlockResult
        """
        return cls(
            action=InterlockAction.ALLOW_WITH_WARNING,
            allowed=True,
            reason=f"Emergency {emergency_level_name} active - proceeding with caution",
            emergency_level=emergency_level,
            emergency_level_name=emergency_level_name,
            namespace=namespace,
        )
    
    @classmethod
    def pause(
        cls,
        emergency_level: int,
        emergency_level_name: str,
        namespace: str,
        reason: Optional[str] = None,
    ) -> "InterlockResult":
        """
        일시 중지 결과 팩토리.
        
        Args:
            emergency_level: Emergency 레벨 값
            emergency_level_name: Emergency 레벨 이름
            namespace: 네임스페이스
            reason: 추가 사유 (없으면 기본 메시지)
        
        Returns:
            PAUSE 액션의 InterlockResult
        """
        return cls(
            action=InterlockAction.PAUSE,
            allowed=False,
            reason=reason or f"Emergency {emergency_level_name} requires Canary pause",
            emergency_level=emergency_level,
            emergency_level_name=emergency_level_name,
            namespace=namespace,
        )
    
    @classmethod
    def rollback(
        cls,
        emergency_level: int,
        emergency_level_name: str,
        namespace: str,
        reason: Optional[str] = None,
    ) -> "InterlockResult":
        """
        롤백 결과 팩토리.
        
        Args:
            emergency_level: Emergency 레벨 값
            emergency_level_name: Emergency 레벨 이름
            namespace: 네임스페이스
            reason: 추가 사유 (없으면 기본 메시지)
        
        Returns:
            ROLLBACK 액션의 InterlockResult
        """
        return cls(
            action=InterlockAction.ROLLBACK,
            allowed=False,
            reason=reason or f"Emergency {emergency_level_name} requires immediate rollback",
            emergency_level=emergency_level,
            emergency_level_name=emergency_level_name,
            namespace=namespace,
        )
    
    @classmethod
    def fail_closed(
        cls,
        failure: InterlockCheckFailure,
        namespace: str,
        error_message: str,
    ) -> "InterlockResult":
        """
        Fail-Closed 결과 팩토리.
        
        백엔드 장애 시 LEVEL_3로 간주하여 ROLLBACK 액션 반환.
        
        Args:
            failure: 실패 유형
            namespace: 네임스페이스
            error_message: 에러 메시지
        
        Returns:
            Fail-Closed로 인한 ROLLBACK 액션의 InterlockResult
        """
        return cls(
            action=InterlockAction.ROLLBACK,
            allowed=False,
            reason=f"Fail-Closed: {error_message}",
            emergency_level=3,  # LEVEL_3 값
            emergency_level_name="LEVEL_3",
            namespace=namespace,
            check_failure=failure,
            is_fail_closed=True,
        )
    
    @classmethod
    def block(
        cls,
        emergency_level: int,
        emergency_level_name: str,
        namespace: str,
        reason: Optional[str] = None,
    ) -> "InterlockResult":
        """
        차단 결과 팩토리.
        
        Args:
            emergency_level: Emergency 레벨 값
            emergency_level_name: Emergency 레벨 이름
            namespace: 네임스페이스
            reason: 추가 사유 (없으면 기본 메시지)
        
        Returns:
            BLOCK 액션의 InterlockResult
        """
        return cls(
            action=InterlockAction.BLOCK,
            allowed=False,
            reason=reason or f"Operation blocked by Emergency {emergency_level_name}",
            emergency_level=emergency_level,
            emergency_level_name=emergency_level_name,
            namespace=namespace,
        )


# =============================================================================
# CanarySafetyInterlock
# =============================================================================


class CanarySafetyInterlock:
    """
    Canary 롤아웃 Safety Interlock.
    
    Emergency Level에 따라 Canary 작업을 제어합니다.
    
    기능:
    - Emergency Level 기반 자동 제동
    - 설정 가능한 정책 (레벨별 액션 매핑)
    - Fail-Closed 정책 (백엔드 장애 시 안전 모드)
    
    기본 정책:
    - NORMAL (0): ALLOW
    - LEVEL_1 (1): ALLOW_WITH_WARNING
    - LEVEL_2 (2): PAUSE
    - LEVEL_3 (3): ROLLBACK
    
    Usage:
        interlock = CanarySafetyInterlock()
        
        # 작업 전 체크
        result = interlock.check(operation="promote", rollout_id="abc123")
        
        if not result.allowed:
            if result.action == InterlockAction.PAUSE:
                canary_service.pause(rollout_id)
            elif result.action == InterlockAction.ROLLBACK:
                canary_service.rollback(rollout_id)
    """
    
    # Emergency Level 값 → InterlockAction 기본 매핑
    # EmergencyLevel enum의 값 (0, 1, 2, 3)을 키로 사용
    DEFAULT_POLICY: Dict[int, InterlockAction] = {
        0: InterlockAction.ALLOW,             # NORMAL
        1: InterlockAction.ALLOW_WITH_WARNING,  # LEVEL_1
        2: InterlockAction.PAUSE,             # LEVEL_2
        3: InterlockAction.ROLLBACK,          # LEVEL_3
    }
    
    def __init__(
        self,
        policy: Optional[Dict[int, InterlockAction]] = None,
        fail_closed: bool = True,
        emergency_tracker_factory: Optional[Callable] = None,
    ):
        """
        CanarySafetyInterlock 초기화.
        
        Args:
            policy: Emergency Level별 액션 정책
                    (None이면 DEFAULT_POLICY 사용)
            fail_closed: True면 백엔드 장애 시 LEVEL_3로 간주 (기본값: True)
            emergency_tracker_factory: EmergencyTracker 팩토리 함수 (테스트용)
        """
        self.policy = policy if policy is not None else self.DEFAULT_POLICY.copy()
        self.fail_closed = fail_closed
        self._emergency_tracker = None
        self._emergency_tracker_factory = emergency_tracker_factory
    
    def _get_emergency_tracker(self):
        """
        EmergencyModeTracker 획득 (lazy loading).
        
        Returns:
            NamespacedEmergencyTracker 인스턴스
        """
        if self._emergency_tracker is None:
            if self._emergency_tracker_factory is not None:
                self._emergency_tracker = self._emergency_tracker_factory()
            else:
                from selfhealing.services.namespace_emergency import (
                    get_namespaced_emergency_tracker,
                )
                self._emergency_tracker = get_namespaced_emergency_tracker()
        return self._emergency_tracker
    
    def check(
        self,
        operation: str,
        rollout_id: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> InterlockResult:
        """
        Safety Interlock 체크.
        
        현재 Emergency Level을 조회하고, 정책에 따른 액션을 결정합니다.
        
        Args:
            operation: 수행할 작업 (start, promote, resume 등)
            rollout_id: 롤아웃 ID (있는 경우)
            namespace: 대상 네임스페이스 (None이면 현재 인스턴스)
        
        Returns:
            InterlockResult: 체크 결과
        """
        # 백엔드 상태 조회 (Fail-Safe 적용)
        try:
            tracker = self._get_emergency_tracker()
            state = tracker.get_effective_state(namespace=namespace)
            level = state.emergency_level
            level_value = level.value
            level_name = level.name
            ns = state.namespace
        except Exception as e:
            # Fail-Closed: 백엔드 장애 시 LEVEL_3로 간주
            if self.fail_closed:
                logger.critical(
                    f"[CanaryInterlock] Backend unavailable, "
                    f"applying Fail-Closed policy: {e}"
                )
                return InterlockResult.fail_closed(
                    failure=InterlockCheckFailure.BACKEND_UNAVAILABLE,
                    namespace=namespace or "unknown",
                    error_message=str(e),
                )
            else:
                # Fail-Open (위험! 프로덕션 비권장)
                logger.error(
                    f"[CanaryInterlock] Backend unavailable, "
                    f"Fail-Open mode (dangerous): {e}"
                )
                return InterlockResult.allow(
                    emergency_level=0,
                    emergency_level_name="NORMAL",
                    namespace=namespace or "unknown",
                )
        
        # 정책에서 액션 조회
        action = self.policy.get(level_value, InterlockAction.ALLOW)
        
        # 액션별 결과 생성
        if action == InterlockAction.ALLOW:
            result = InterlockResult.allow(
                emergency_level=level_value,
                emergency_level_name=level_name,
                namespace=ns,
            )
        
        elif action == InterlockAction.ALLOW_WITH_WARNING:
            result = InterlockResult.allow_with_warning(
                emergency_level=level_value,
                emergency_level_name=level_name,
                namespace=ns,
            )
            logger.warning(
                f"[CanaryInterlock] Allowing {operation} with warning: "
                f"rollout={rollout_id}, level={level_name}"
            )
        
        elif action == InterlockAction.PAUSE:
            result = InterlockResult.pause(
                emergency_level=level_value,
                emergency_level_name=level_name,
                namespace=ns,
            )
            logger.warning(
                f"[CanaryInterlock] PAUSE required: "
                f"rollout={rollout_id}, level={level_name}, operation={operation}"
            )
        
        elif action == InterlockAction.ROLLBACK:
            result = InterlockResult.rollback(
                emergency_level=level_value,
                emergency_level_name=level_name,
                namespace=ns,
            )
            logger.warning(
                f"[CanaryInterlock] ROLLBACK required: "
                f"rollout={rollout_id}, level={level_name}, operation={operation}"
            )
        
        else:  # BLOCK (기본)
            result = InterlockResult.block(
                emergency_level=level_value,
                emergency_level_name=level_name,
                namespace=ns,
            )
            logger.warning(
                f"[CanaryInterlock] BLOCK: "
                f"rollout={rollout_id}, level={level_name}, operation={operation}"
            )
        
        return result
    
    def check_and_apply(
        self,
        canary_service: "CanaryRolloutService",
        rollout_id: str,
        operation: str,
        namespace: Optional[str] = None,
    ) -> InterlockResult:
        """
        Safety Interlock 체크 및 자동 적용.
        
        PAUSE나 ROLLBACK 액션이면 canary_service를 통해 자동 적용합니다.
        
        Args:
            canary_service: CanaryRolloutService 인스턴스
            rollout_id: 롤아웃 ID
            operation: 수행할 작업
            namespace: 대상 네임스페이스
        
        Returns:
            InterlockResult: 체크 및 적용 결과
        """
        result = self.check(operation, rollout_id, namespace)
        
        if result.action == InterlockAction.PAUSE:
            reason = f"[AUTO-INTERLOCK] {result.reason}"
            try:
                success = canary_service.pause(rollout_id)
                logger.warning(
                    f"[CanaryInterlock] Auto-paused: "
                    f"rollout={rollout_id}, success={success}"
                )
                result.metadata["auto_applied"] = True
                result.metadata["apply_success"] = success
            except Exception as e:
                logger.error(
                    f"[CanaryInterlock] Failed to auto-pause: "
                    f"rollout={rollout_id}, error={e}"
                )
                result.metadata["auto_applied"] = True
                result.metadata["apply_success"] = False
                result.metadata["apply_error"] = str(e)
        
        elif result.action == InterlockAction.ROLLBACK:
            reason = f"[AUTO-INTERLOCK] {result.reason}"
            try:
                success = canary_service.rollback(rollout_id, reason=reason)
                logger.warning(
                    f"[CanaryInterlock] Auto-rolled back: "
                    f"rollout={rollout_id}, success={success}"
                )
                result.metadata["auto_applied"] = True
                result.metadata["apply_success"] = success
            except Exception as e:
                logger.error(
                    f"[CanaryInterlock] Failed to auto-rollback: "
                    f"rollout={rollout_id}, error={e}"
                )
                result.metadata["auto_applied"] = True
                result.metadata["apply_success"] = False
                result.metadata["apply_error"] = str(e)
        
        return result


# =============================================================================
# Singleton
# =============================================================================

_safety_interlock: Optional[CanarySafetyInterlock] = None
_singleton_lock = threading.Lock()


def get_canary_safety_interlock() -> CanarySafetyInterlock:
    """
    CanarySafetyInterlock 싱글톤 반환.
    
    Returns:
        CanarySafetyInterlock 인스턴스
    """
    global _safety_interlock
    
    if _safety_interlock is None:
        with _singleton_lock:
            if _safety_interlock is None:
                _safety_interlock = CanarySafetyInterlock()
    
    return _safety_interlock


def reset_canary_safety_interlock() -> None:
    """
    싱글톤 초기화 (테스트용).
    """
    global _safety_interlock
    with _singleton_lock:
        _safety_interlock = None


# =============================================================================
# Phase 2: EmergencyOverrideRequest (§3.8 Break Glass)
# =============================================================================


@dataclass
class EmergencyOverrideRequest:
    """
    Emergency Override (Break Glass) 요청.
    
    장애 복구를 위한 긴급 설정 변경 시 Safety Interlock을 우회합니다.
    
    주의사항:
    - 장애 복구용으로만 사용해야 합니다.
    - 사용 시 PIR(Post-Incident Review)가 필수입니다.
    - LEVEL_3에서는 사전 승인 토큰이 필요합니다.
    
    Attributes:
        reason: 우회 사유 (최소 10자 이상)
        requested_by: 요청자 (이메일 또는 사용자명)
        ticket_id: 관련 티켓/인시던트 ID (강력 권장)
        expires_at: 우회 만료 시각 (기본 1시간)
        approval_token: 사전 승인 토큰 (LEVEL_3에서 필수)
        acknowledged_risks: 인지한 위험 목록
    """
    
    reason: str
    """우회 사유 (필수, 최소 10자 이상)."""
    
    requested_by: str
    """요청자 (이메일 또는 사용자명)."""
    
    ticket_id: Optional[str] = None
    """관련 티켓/인시던트 ID (강력 권장)."""
    
    expires_at: Optional[str] = None
    """우회 만료 시각 ISO 형식 (None이면 기본값 1시간)."""
    
    approval_token: Optional[str] = None
    """사전 승인 토큰 (LEVEL_3에서 필수)."""
    
    acknowledged_risks: list = field(default_factory=list)
    """인지한 위험 목록."""
    
    def is_valid(self) -> bool:
        """
        요청 유효성 검증.
        
        Returns:
            True: 필수 필드가 올바르게 설정됨
            False: 필수 필드 누락 또는 유효하지 않음
        """
        if not self.reason or len(self.reason.strip()) < 10:
            return False
        if not self.requested_by or len(self.requested_by.strip()) < 1:
            return False
        return True
    
    def requires_approval_token(self, level_value: int) -> bool:
        """
        현재 레벨에서 승인 토큰이 필요한지 확인.
        
        Args:
            level_value: Emergency 레벨 값 (3 = LEVEL_3)
        
        Returns:
            True: LEVEL_3에서는 승인 토큰 필요
        """
        return level_value >= 3


@dataclass
class EmergencyOverridePolicy:
    """
    Emergency Override 정책.
    
    어떤 조건에서 Break Glass 우회를 허용할지 정의합니다.
    
    Attributes:
        enabled: Emergency Override 기능 활성화 여부
        min_reason_length: 최소 사유 길이
        require_ticket_id: 티켓/인시던트 ID 필수 여부
        require_approval_on_level_3: LEVEL_3에서 사전 승인 필수 여부
        default_ttl_minutes: 기본 우회 만료 시간 (분)
        max_ttl_minutes: 최대 우회 만료 시간 (분)
        allowed_roles: 우회 가능 역할 목록
        pir_required: Post-Incident Review 필수 여부
    """
    
    enabled: bool = True
    """Emergency Override 기능 활성화 여부."""
    
    min_reason_length: int = 10
    """최소 사유 길이."""
    
    require_ticket_id: bool = True
    """티켓/인시던트 ID 필수 여부."""
    
    require_approval_on_level_3: bool = True
    """LEVEL_3에서 사전 승인 필수 여부."""
    
    default_ttl_minutes: int = 60
    """기본 우회 만료 시간 (분)."""
    
    max_ttl_minutes: int = 240
    """최대 우회 만료 시간 (분) - 4시간."""
    
    allowed_roles: list = field(default_factory=lambda: ["admin", "sre", "on_call"])
    """우회 가능 역할 목록."""
    
    pir_required: bool = True
    """Post-Incident Review 필수 여부."""
    
    def validate_request(
        self,
        override: EmergencyOverrideRequest,
        level_value: int,
    ) -> tuple:
        """
        Override 요청 유효성 검증.
        
        Args:
            override: Override 요청
            level_value: 현재 Emergency 레벨 값
        
        Returns:
            (is_valid: bool, error_message: Optional[str])
        """
        if not self.enabled:
            return False, "Emergency Override is disabled"
        
        if not override.is_valid():
            return False, "Invalid override request: reason and requested_by are required"
        
        if len(override.reason) < self.min_reason_length:
            return False, f"Reason must be at least {self.min_reason_length} characters"
        
        if self.require_ticket_id and not override.ticket_id:
            return False, "Ticket ID is required for emergency override"
        
        if (
            self.require_approval_on_level_3
            and level_value >= 3
            and not override.approval_token
        ):
            return False, "Approval token is required for LEVEL_3 override"
        
        return True, None


# =============================================================================
# Phase 2: RegionalInterlockPolicy (§3.9 리전별 격리)
# =============================================================================


class RegionalInterlockBehavior(str, Enum):
    """
    리전별 Interlock 행동 유형.
    
    글로벌 카나리 진행 중 특정 리전에 Emergency가 발생했을 때
    어떻게 대응할지 정의합니다.
    """
    
    PAUSE_ALL = "pause_all"
    """
    기본값 (안전 우선).
    
    한 리전이라도 Emergency면 전체 롤아웃 일시 중지.
    가장 보수적이지만 가장 안전한 옵션.
    """
    
    ROLLBACK_AFFECTED_ONLY = "rollback_affected_only"
    """
    영향 리전만 롤백.
    
    리전별 격리 배포일 때만 사용 가능.
    해당 리전의 previous_values로 복원, 다른 리전은 계속 진행.
    
    주의: 리전 간 설정 불일치가 발생할 수 있음.
    """
    
    HYBRID = "hybrid"
    """
    하이브리드 전략.
    
    영향 리전: 즉시 롤백
    나머지 리전: 일시 중지 (PAUSE)
    
    영향 리전의 복구를 기다린 후 수동으로 재개.
    """
    
    CONTINUE_HEALTHY = "continue_healthy"
    """
    건강한 리전만 계속 진행 (위험!).
    
    영향 리전을 롤백하고 나머지 리전은 프로모션 계속.
    설정 불일치 리스크가 있으므로 신중하게 사용.
    
    Warning: 리전 간 설정 드리프트 발생 가능.
    """


@dataclass
class RegionalInterlockPolicy:
    """
    리전별 Interlock 정책.
    
    멀티 리전 환경에서 특정 리전에만 Emergency가 발생했을 때
    롤아웃을 어떻게 처리할지 결정합니다.
    
    Attributes:
        default_behavior: 기본 행동 (안전 우선: PAUSE_ALL)
        allow_isolated_rollback: 격리 롤백 허용 여부
        require_manual_resume_after_regional_rollback: 리전 롤백 후 수동 재개 필수
        max_affected_regions_for_isolated_rollback: 격리 롤백 허용 최대 영향 리전 수
    """
    
    default_behavior: RegionalInterlockBehavior = RegionalInterlockBehavior.PAUSE_ALL
    """기본 행동 (안전 우선: PAUSE_ALL)."""
    
    allow_isolated_rollback: bool = False
    """
    격리 롤백 허용 여부.
    
    True면 ROLLBACK_AFFECTED_ONLY와 CONTINUE_HEALTHY 사용 가능.
    False면 항상 PAUSE_ALL 또는 HYBRID만 사용.
    """
    
    require_manual_resume_after_regional_rollback: bool = True
    """리전 롤백 후 수동 재개 필수 여부."""
    
    max_affected_regions_for_isolated_rollback: int = 1
    """격리 롤백 허용 최대 영향 리전 수."""
    
    def get_behavior_for_situation(
        self,
        affected_regions: list,
        total_regions: list,
        is_region_isolated_deployment: bool = False,
    ) -> RegionalInterlockBehavior:
        """
        상황에 맞는 행동 결정.
        
        Args:
            affected_regions: Emergency 상태인 리전 목록
            total_regions: 전체 배포 대상 리전 목록
            is_region_isolated_deployment: 리전별 격리 배포인지 여부
        
        Returns:
            적용할 행동
        """
        # 모든 리전이 영향받으면 무조건 PAUSE_ALL
        if len(affected_regions) >= len(total_regions):
            return RegionalInterlockBehavior.PAUSE_ALL
        
        # 영향 리전 없으면 변경 없음 (호출 안 되어야 하지만 방어코드)
        if not affected_regions:
            return RegionalInterlockBehavior.PAUSE_ALL
        
        # 격리 배포가 아니면 ROLLBACK_AFFECTED_ONLY, CONTINUE_HEALTHY 사용 불가
        if not is_region_isolated_deployment:
            if self.default_behavior in (
                RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
                RegionalInterlockBehavior.CONTINUE_HEALTHY,
            ):
                return RegionalInterlockBehavior.PAUSE_ALL
        
        # 영향 리전이 너무 많으면 PAUSE_ALL
        if len(affected_regions) > self.max_affected_regions_for_isolated_rollback:
            return RegionalInterlockBehavior.PAUSE_ALL
        
        # 격리 롤백이 허용되지 않으면 PAUSE_ALL
        if not self.allow_isolated_rollback:
            if self.default_behavior in (
                RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
                RegionalInterlockBehavior.CONTINUE_HEALTHY,
            ):
                return RegionalInterlockBehavior.PAUSE_ALL
        
        return self.default_behavior


# =============================================================================
# Phase 2: PauseReasonTracker (§3.10 PAUSED 상태 사유 추적)
# =============================================================================


@dataclass
class PauseContext:
    """
    PAUSED 상태의 컨텍스트 정보.
    
    왜 배포가 멈췄는지를 운영자에게 명확히 설명합니다.
    
    Attributes:
        reason: 일시 중지 사유
        triggered_by: 트리거 유형 (interlock, manual, chaos_guard, metrics)
        emergency_level: 인터락 발동 시의 Emergency 레벨 값
        emergency_level_name: Emergency 레벨 이름
        namespace: 영향받은 네임스페이스
        causation_chain_id: CausationChain ID (인과관계 추적)
        paused_at: 일시 중지 시각 ISO 형식
        auto_resume_condition: 자동 재개 조건
        estimated_resume_at: 예상 재개 시각 ISO 형식
    """
    
    reason: str
    """일시 중지 사유."""
    
    triggered_by: str
    """트리거 유형 (interlock, manual, chaos_guard, metrics)."""
    
    emergency_level: Optional[int] = None
    """인터락 발동 시의 Emergency 레벨 값."""
    
    emergency_level_name: Optional[str] = None
    """Emergency 레벨 이름."""
    
    namespace: Optional[str] = None
    """영향받은 네임스페이스."""
    
    causation_chain_id: Optional[str] = None
    """CausationChain ID (인과관계 추적)."""
    
    paused_at: Optional[str] = None
    """일시 중지 시각 ISO 형식."""
    
    auto_resume_condition: Optional[str] = None
    """자동 재개 조건 (있는 경우)."""
    
    estimated_resume_at: Optional[str] = None
    """예상 재개 시각 ISO 형식 (있는 경우)."""
    
    def explain(self) -> str:
        """
        운영자를 위한 설명 생성.
        
        Returns:
            사람이 읽을 수 있는 설명 문자열
        """
        parts = ["배포가 일시 중지되었습니다."]
        
        if self.triggered_by == "interlock":
            level_name = self.emergency_level_name or "Unknown"
            parts.append(f"원인: Emergency {level_name} 발생")
            if self.namespace:
                parts.append(f"영향 리전: {self.namespace}")
        elif self.triggered_by == "chaos_guard":
            parts.append("원인: Chaos 실험 충돌 감지")
        elif self.triggered_by == "metrics":
            parts.append("원인: 메트릭 악화 감지")
        else:
            parts.append("원인: 운영자 수동 중지")
        
        parts.append(f"사유: {self.reason}")
        
        if self.causation_chain_id:
            parts.append(f"인과관계 추적: {self.causation_chain_id}")
        
        if self.auto_resume_condition:
            parts.append(f"자동 재개 조건: {self.auto_resume_condition}")
        
        return "\n".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "reason": self.reason,
            "triggered_by": self.triggered_by,
            "emergency_level": self.emergency_level,
            "emergency_level_name": self.emergency_level_name,
            "namespace": self.namespace,
            "causation_chain_id": self.causation_chain_id,
            "paused_at": self.paused_at,
            "auto_resume_condition": self.auto_resume_condition,
            "estimated_resume_at": self.estimated_resume_at,
        }


class PauseReasonTracker:
    """
    PAUSED 상태 사유 추적기.
    
    롤아웃이 왜 멈췄는지 기록하고 조회합니다.
    CausationChain과 연동하여 인과관계를 추적합니다.
    
    Features:
    - PAUSE 이벤트 기록
    - 롤아웃별 PAUSE 이력 조회
    - CausationChain ID 생성 및 연동
    """
    
    def __init__(self):
        """PauseReasonTracker 초기화."""
        self._pause_records: Dict[str, list] = {}  # rollout_id -> List[PauseContext]
        self._lock = threading.Lock()
    
    def record_pause(
        self,
        rollout_id: str,
        context: PauseContext,
    ) -> str:
        """
        PAUSE 이벤트 기록.
        
        Args:
            rollout_id: 롤아웃 ID
            context: PAUSE 컨텍스트
        
        Returns:
            생성된 causation_chain_id
        """
        import uuid
        from datetime import datetime, timezone
        
        # causation_chain_id 생성
        if not context.causation_chain_id:
            context.causation_chain_id = str(uuid.uuid4())
        
        # paused_at 설정
        if not context.paused_at:
            context.paused_at = datetime.now(timezone.utc).isoformat()
        
        # 기록 저장
        with self._lock:
            if rollout_id not in self._pause_records:
                self._pause_records[rollout_id] = []
            self._pause_records[rollout_id].append(context)
        
        # 로깅
        logger.info(
            f"[PauseReasonTracker] Recorded pause: "
            f"rollout={rollout_id}, triggered_by={context.triggered_by}, "
            f"chain_id={context.causation_chain_id}"
        )
        
        return context.causation_chain_id
    
    def get_pause_history(self, rollout_id: str) -> list:
        """
        롤아웃의 PAUSE 이력 조회.
        
        Args:
            rollout_id: 롤아웃 ID
        
        Returns:
            PauseContext 목록
        """
        with self._lock:
            return self._pause_records.get(rollout_id, []).copy()
    
    def get_latest_pause(self, rollout_id: str) -> Optional[PauseContext]:
        """
        롤아웃의 최신 PAUSE 컨텍스트 조회.
        
        Args:
            rollout_id: 롤아웃 ID
        
        Returns:
            최신 PauseContext (없으면 None)
        """
        history = self.get_pause_history(rollout_id)
        return history[-1] if history else None
    
    def clear(self, rollout_id: str) -> None:
        """
        롤아웃의 PAUSE 이력 삭제.
        
        Args:
            rollout_id: 롤아웃 ID
        """
        with self._lock:
            self._pause_records.pop(rollout_id, None)
    
    def clear_all(self) -> None:
        """모든 PAUSE 이력 삭제 (테스트용)."""
        with self._lock:
            self._pause_records.clear()


# =============================================================================
# Phase 2 Singleton
# =============================================================================

_pause_reason_tracker: Optional[PauseReasonTracker] = None
_pause_tracker_lock = threading.Lock()


def get_pause_reason_tracker() -> PauseReasonTracker:
    """
    PauseReasonTracker 싱글톤 반환.
    
    Returns:
        PauseReasonTracker 인스턴스
    """
    global _pause_reason_tracker
    
    if _pause_reason_tracker is None:
        with _pause_tracker_lock:
            if _pause_reason_tracker is None:
                _pause_reason_tracker = PauseReasonTracker()
    
    return _pause_reason_tracker


def reset_pause_reason_tracker() -> None:
    """
    PauseReasonTracker 싱글톤 초기화 (테스트용).
    """
    global _pause_reason_tracker
    with _pause_tracker_lock:
        if _pause_reason_tracker is not None:
            _pause_reason_tracker.clear_all()
        _pause_reason_tracker = None
