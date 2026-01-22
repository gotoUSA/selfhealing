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
