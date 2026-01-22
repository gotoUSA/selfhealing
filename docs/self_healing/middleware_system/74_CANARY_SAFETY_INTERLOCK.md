# 74. Canary Safety Interlock (Canary 자동 제동 장치)

> **Version**: 1.1.0  
> **Created**: 2026-01-21  
> **Updated**: 2026-01-22  
> **Status**: Draft  
> **Parent**: [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md)

---

## 구현 로드맵

### Phase 1: 핵심 인터락 (P0 - 필수)

| 순서 | 컴포넌트 | 설명 | 우선순위 | 상태 |
|------|----------|------|----------|------|
| 1 | `CanarySafetyInterlock` | Emergency Level 기반 자동 제동 | P0 | ⬜ TODO |
| 2 | `FailClosedPolicy` | Redis 장애 시 안전 모드 (§3.7) | P0 | ⬜ TODO |
| 3 | `InterlockAction` / `InterlockResult` | 액션 및 결과 모델 | P0 | ⬜ TODO |

### Phase 2: 안전 강화 (P1)

| 순서 | 컴포넌트 | 설명 | 우선순위 | 상태 |
|------|----------|------|----------|------|
| 4 | `EmergencyOverridePolicy` | Break Glass 긴급 우회 (§3.8) | P1 | ⬜ TODO |
| 5 | `RegionalInterlockPolicy` | 리전별 격리 정책 (§3.9) | P1 | ⬜ TODO |
| 6 | `PauseReasonTracker` | PAUSED 상태 사유 추적 (§3.10) | P1 | ⬜ TODO |

### Phase 3: 신뢰성 및 거버넌스 (P2)

| 순서 | 컴포넌트 | 설명 | 우선순위 | 상태 |
|------|----------|------|----------|------|
| 7 | `EmergencyStateRefresher` | 주기적 상태 동기화 (§3.11) | P2 | ⬜ TODO |
| 8 | `MidApplyInterlockCheck` | 적용 중 인터락 재체크 (§3.12) | P2 | ⬜ TODO |
| 9 | `RollbackValueResolver` | 다단계 롤백 폴백 (§3.13) | P2 | ⬜ TODO |
| 10 | `InterlockBypassAudit` | 우회 시 PIR 강제 마킹 (§3.14) | P2 | ⬜ TODO |

---

## 1. 개요

### 1.1 문제점 (AS-IS)

현재 `CanaryRolloutService`는 Emergency 상태를 **무시**합니다:

```python
# 현재 구현 - canary/service.py
def promote(self, rollout_id: str, force: bool = False) -> bool:
    # ...
    # 메트릭 검증만 수행 (Emergency 체크 없음)
    if not force:
        metrics = self._collect_stage_metrics(rollout)
        is_healthy, failure_reason = self._is_stage_healthy(...)
        if not is_healthy:
            return False
    # ...
```

**문제**:
- LEVEL_3 상황에서도 Canary 프로모션 진행
- 설정 변경이 장애를 **가속화**할 수 있음
- Kill Switch 역할 부재

### 1.2 해결책 (TO-BE)

Emergency Level에 따른 **자동 제동 장치** 추가:

| Emergency Level | Canary 행동 |
|----------------|-------------|
| NORMAL | 정상 진행 |
| LEVEL_1 | 정상 진행 (경고 로깅) |
| LEVEL_2 | **PAUSE** (일시 중지) |
| LEVEL_3 | **ROLLBACK** (즉시 롤백) |

---

## 2. 아키텍처

### 2.1 Safety Interlock 흐름

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Canary Safety Interlock Flow                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  CanaryRolloutService                         │  │
│  │                                                               │  │
│  │  promote()  ──▶  SafetyInterlock.check()  ──▶  proceed/block  │  │
│  │  start()    ──▶  SafetyInterlock.check()  ──▶  proceed/block  │  │
│  │                                                               │  │
│  └───────────────────────────┬──────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              CanarySafetyInterlock (신규)                     │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐ │  │
│  │  │ check(operation: str) -> InterlockResult                 │ │  │
│  │  │                                                          │ │  │
│  │  │ 1. Get current EmergencyLevel                           │ │  │
│  │  │ 2. Lookup policy for level                              │ │  │
│  │  │ 3. Return action (ALLOW/PAUSE/ROLLBACK)                 │ │  │
│  │  └─────────────────────────────────────────────────────────┘ │  │
│  │                                                               │  │
│  └───────────────────────────┬──────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Emergency Level Check                            │  │
│  │                                                               │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │  │
│  │  │ NORMAL  │  │ LEVEL_1 │  │ LEVEL_2 │  │ LEVEL_3 │        │  │
│  │  │         │  │         │  │         │  │         │        │  │
│  │  │ ALLOW   │  │ ALLOW   │  │ PAUSE   │  │ ROLLBACK│        │  │
│  │  │         │  │ +WARN   │  │         │  │         │        │  │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │  │
│  │                                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 이벤트 기반 자동 제동

Emergency Level 변경 시 **진행 중인 모든 Canary에 자동 적용**:

```
┌─────────────────────────────────────────────────────────────────────┐
│               Event-Driven Automatic Interlock                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  EmergencyCoordinator          CanaryInterlockHandler               │
│        │                              │                              │
│        │  LEVEL_3 Detected           │                              │
│        ├──────────────────────────────▶                              │
│        │  event: EmergencyLevelChanged│                              │
│        │                              │                              │
│        │                   ┌──────────┴──────────┐                  │
│        │                   │ Get active rollouts │                  │
│        │                   │ [rollout-1, rollout-2]                 │
│        │                   └──────────┬──────────┘                  │
│        │                              │                              │
│        │                              │  For each rollout:          │
│        │                              │                              │
│        │                   ┌──────────▼──────────┐                  │
│        │                   │ CanaryService       │                  │
│        │                   │ .panic_rollback()   │                  │
│        │                   │                     │                  │
│        │                   │ rollout-1: ROLLED_BACK                 │
│        │                   │ rollout-2: ROLLED_BACK                 │
│        │                   └──────────┬──────────┘                  │
│        │                              │                              │
│        │                   ┌──────────▼──────────┐                  │
│        │                   │ CascadeAudit        │                  │
│        │                   │ .record(...)        │                  │
│        │                   └─────────────────────┘                  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 구현 상세

### 3.1 InterlockAction Enum

```python
# packages/selfhealing-python/src/selfhealing/services/canary/interlock.py

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class InterlockAction(str, Enum):
    """Safety Interlock 액션."""
    
    ALLOW = "allow"
    """정상 진행 허용."""
    
    ALLOW_WITH_WARNING = "allow_with_warning"
    """경고와 함께 진행 허용."""
    
    PAUSE = "pause"
    """일시 중지 (나중에 재개 가능)."""
    
    ROLLBACK = "rollback"
    """즉시 롤백."""
    
    BLOCK = "block"
    """작업 차단 (시작 불가)."""


class InterlockCheckFailure(str, Enum):
    """인터락 체크 실패 유형 (Fail-Safe용)."""
    
    BACKEND_UNAVAILABLE = "backend_unavailable"
    """Redis/백엔드 연결 불가."""
    
    TRACKER_ERROR = "tracker_error"
    """EmergencyModeTracker 오류."""
    
    TIMEOUT = "timeout"
    """체크 타임아웃."""
```

### 3.2 InterlockResult 모델

```python
@dataclass
class InterlockResult:
    """Safety Interlock 체크 결과."""
    
    action: InterlockAction
    """수행할 액션."""
    
    allowed: bool
    """진행 허용 여부."""
    
    reason: str
    """액션 사유."""
    
    emergency_level: EmergencyLevel
    """현재 Emergency 레벨."""
    
    namespace: str
    """적용된 네임스페이스."""
    
    delay_seconds: int = 0
    """지연 시간 (PAUSE의 경우)."""
    
    auto_resume_at: Optional[str] = None
    """자동 재개 시각 (있는 경우)."""
    
    # 신규: Fail-Safe 관련 필드
    check_failure: Optional[InterlockCheckFailure] = None
    """체크 실패 유형 (정상이면 None)."""
    
    is_fail_closed: bool = False
    """Fail-Closed 정책으로 인한 차단인지 여부."""
    
    # 신규: 우회 관련 필드
    was_bypassed: bool = False
    """Emergency Override로 우회되었는지 여부."""
    
    bypass_reason: Optional[str] = None
    """우회 사유 (was_bypassed=True일 때)."""
    
    @classmethod
    def allow(cls, level: EmergencyLevel, namespace: str) -> "InterlockResult":
        """허용 결과 팩토리."""
        return cls(
            action=InterlockAction.ALLOW,
            allowed=True,
            reason="Emergency level allows operation",
            emergency_level=level,
            namespace=namespace,
        )
    
    @classmethod
    def pause(
        cls,
        level: EmergencyLevel,
        namespace: str,
        reason: str,
    ) -> "InterlockResult":
        """일시 중지 결과 팩토리."""
        return cls(
            action=InterlockAction.PAUSE,
            allowed=False,
            reason=reason,
            emergency_level=level,
            namespace=namespace,
        )
    
    @classmethod
    def rollback(
        cls,
        level: EmergencyLevel,
        namespace: str,
        reason: str,
    ) -> "InterlockResult":
        """롤백 결과 팩토리."""
        return cls(
            action=InterlockAction.ROLLBACK,
            allowed=False,
            reason=reason,
            emergency_level=level,
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
        
        Reference:
            critical_path_fallback.py#L158 (_get_default_state 패턴)
        """
        return cls(
            action=InterlockAction.ROLLBACK,
            allowed=False,
            reason=f"Fail-Closed: {error_message}",
            emergency_level=EmergencyLevel.LEVEL_3,  # 최악 가정
            namespace=namespace,
            check_failure=failure,
            is_fail_closed=True,
        )
    
    @classmethod
    def bypassed(
        cls,
        level: EmergencyLevel,
        namespace: str,
        bypass_reason: str,
        bypassed_by: str,
    ) -> "InterlockResult":
        """
        Emergency Override 우회 결과 팩토리.
        
        Reference:
            circuit_breaker.py#L567 (manual_override 패턴)
        """
        return cls(
            action=InterlockAction.ALLOW,
            allowed=True,
            reason=f"Emergency Override by {bypassed_by}: {bypass_reason}",
            emergency_level=level,
            namespace=namespace,
            was_bypassed=True,
            bypass_reason=bypass_reason,
        )
```

### 3.3 CanarySafetyInterlock

```python
class CanarySafetyInterlock:
    """
    Canary 롤아웃 Safety Interlock.
    
    Emergency Level에 따라 Canary 작업을 제어합니다.
    
    Features:
    - Emergency Level 기반 자동 제동
    - 설정 가능한 정책
    - Audit 로깅 연동
    - Fail-Closed 정책 (백엔드 장애 시 안전 모드)
    - Emergency Override (Break Glass) 지원
    - Regional/Global 격리 정책
    
    Reference:
    - docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
    - critical_path_fallback.py#L158 (Fail-Safe 패턴)
    - circuit_breaker.py#L567 (manual_override 패턴)
    """
    
    # 기본 정책 (설정으로 오버라이드 가능)
    DEFAULT_POLICY: Dict[EmergencyLevel, InterlockAction] = {
        EmergencyLevel.NORMAL: InterlockAction.ALLOW,
        EmergencyLevel.LEVEL_1: InterlockAction.ALLOW_WITH_WARNING,
        EmergencyLevel.LEVEL_2: InterlockAction.PAUSE,
        EmergencyLevel.LEVEL_3: InterlockAction.ROLLBACK,
    }
    
    def __init__(
        self,
        policy: Optional[Dict[EmergencyLevel, InterlockAction]] = None,
        fail_closed: bool = True,
        regional_policy: Optional["RegionalInterlockPolicy"] = None,
    ):
        """
        CanarySafetyInterlock 초기화.
        
        Args:
            policy: Emergency Level별 액션 정책 (None이면 기본값 사용)
            fail_closed: True면 백엔드 장애 시 LEVEL_3로 간주 (기본값: True)
            regional_policy: 리전별 격리 정책 (None이면 기본 정책)
        """
        self.policy = policy or self.DEFAULT_POLICY
        self.fail_closed = fail_closed
        self.regional_policy = regional_policy or RegionalInterlockPolicy()
        self._emergency_tracker = None
    
    def _get_emergency_tracker(self):
        """EmergencyModeTracker 획득 (lazy)."""
        if self._emergency_tracker is None:
            from selfhealing.services.governance import get_namespaced_emergency_tracker
            self._emergency_tracker = get_namespaced_emergency_tracker()
        return self._emergency_tracker
    
    def check(
        self,
        operation: str,
        rollout_id: Optional[str] = None,
        namespace: Optional[str] = None,
        emergency_override: Optional["EmergencyOverrideRequest"] = None,
    ) -> InterlockResult:
        """
        Safety Interlock 체크.
        
        Args:
            operation: 수행할 작업 (start, promote, resume)
            rollout_id: 롤아웃 ID (있는 경우)
            namespace: 대상 네임스페이스 (None이면 현재 인스턴스)
            emergency_override: Emergency Override 요청 (Break Glass)
        
        Returns:
            InterlockResult: 체크 결과
        """
        # === Emergency Override 체크 (Break Glass) ===
        if emergency_override and emergency_override.is_valid():
            return self._handle_emergency_override(
                emergency_override, operation, rollout_id, namespace
            )
        
        # === 백엔드 상태 조회 (Fail-Safe 적용) ===
        try:
            tracker = self._get_emergency_tracker()
            state = tracker.get_effective_state(namespace=namespace)
            level = state.emergency_level
            ns = state.namespace
        except Exception as e:
            # Fail-Closed: 백엔드 장애 시 LEVEL_3로 간주
            if self.fail_closed:
                logger.critical(
                    f"[CanaryInterlock] Backend unavailable, applying Fail-Closed: {e}"
                )
                return InterlockResult.fail_closed(
                    failure=InterlockCheckFailure.BACKEND_UNAVAILABLE,
                    namespace=namespace or "unknown",
                    error_message=str(e),
                )
            else:
                # Fail-Open (위험! 프로덕션 비권장)
                logger.error(
                    f"[CanaryInterlock] Backend unavailable, Fail-Open mode: {e}"
                )
                return InterlockResult.allow(EmergencyLevel.NORMAL, namespace or "unknown")
        
        action = self.policy.get(level, InterlockAction.ALLOW)
        
        # 액션별 결과 생성
        if action == InterlockAction.ALLOW:
            result = InterlockResult.allow(level, ns)
        
        elif action == InterlockAction.ALLOW_WITH_WARNING:
            result = InterlockResult(
                action=InterlockAction.ALLOW_WITH_WARNING,
                allowed=True,
                reason=f"Emergency {level.name} active - proceeding with caution",
                emergency_level=level,
                namespace=ns,
            )
            logger.warning(
                f"[CanaryInterlock] Allowing {operation} with warning: "
                f"rollout={rollout_id}, level={level.name}"
            )
        
        elif action == InterlockAction.PAUSE:
            result = InterlockResult.pause(
                level=level,
                namespace=ns,
                reason=f"Emergency {level.name} requires Canary pause",
            )
            logger.warning(
                f"[CanaryInterlock] PAUSE required: "
                f"rollout={rollout_id}, level={level.name}"
            )
        
        elif action == InterlockAction.ROLLBACK:
            result = InterlockResult.rollback(
                level=level,
                namespace=ns,
                reason=f"Emergency {level.name} requires immediate rollback",
            )
            logger.warning(
                f"[CanaryInterlock] ROLLBACK required: "
                f"rollout={rollout_id}, level={level.name}"
            )
        
        else:
            # BLOCK (기본)
            result = InterlockResult(
                action=InterlockAction.BLOCK,
                allowed=False,
                reason=f"Operation blocked by Emergency {level.name}",
                emergency_level=level,
                namespace=ns,
            )
        
        return result
    
    def _handle_emergency_override(
        self,
        override: "EmergencyOverrideRequest",
        operation: str,
        rollout_id: Optional[str],
        namespace: Optional[str],
    ) -> InterlockResult:
        """
        Emergency Override (Break Glass) 처리.
        
        위험 배포로 마킹하고 PIR 필수 플래그를 설정합니다.
        """
        # 감사 로그에 위험 배포 기록
        log_interlock_bypass(
            rollout_id=rollout_id,
            operation=operation,
            override=override,
            namespace=namespace,
        )
        
        # 현재 레벨 조회 (Fail-Safe 없이 - Override는 항상 허용)
        try:
            tracker = self._get_emergency_tracker()
            state = tracker.get_effective_state(namespace=namespace)
            level = state.emergency_level
            ns = state.namespace
        except Exception:
            level = EmergencyLevel.LEVEL_3  # 최악 가정
            ns = namespace or "unknown"
        
        logger.warning(
            f"[CanaryInterlock] EMERGENCY OVERRIDE: "
            f"rollout={rollout_id}, level={level.name}, "
            f"reason={override.reason}, by={override.requested_by}"
        )
        
        return InterlockResult.bypassed(
            level=level,
            namespace=ns,
            bypass_reason=override.reason,
            bypassed_by=override.requested_by,
        )
    
    def check_and_apply(
        self,
        canary_service: "CanaryRolloutService",
        rollout_id: str,
        operation: str,
        namespace: Optional[str] = None,
    ) -> InterlockResult:
        """
        Safety Interlock 체크 및 자동 적용.
        
        PAUSE나 ROLLBACK 액션이면 자동으로 적용합니다.
        
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
            success = canary_service.pause(
                rollout_id,
                reason=f"[AUTO-INTERLOCK] {result.reason}",
            )
            logger.warning(
                f"[CanaryInterlock] Auto-paused: "
                f"rollout={rollout_id}, success={success}"
            )
        
        elif result.action == InterlockAction.ROLLBACK:
            success = canary_service.rollback(
                rollout_id,
                reason=f"[AUTO-INTERLOCK] {result.reason}",
            )
            logger.warning(
                f"[CanaryInterlock] Auto-rolled back: "
                f"rollout={rollout_id}, success={success}"
            )
        
        return result


# =============================================================================
# Singleton
# =============================================================================

_safety_interlock: Optional[CanarySafetyInterlock] = None


def get_canary_safety_interlock() -> CanarySafetyInterlock:
    """CanarySafetyInterlock 싱글톤 반환."""
    global _safety_interlock
    if _safety_interlock is None:
        _safety_interlock = CanarySafetyInterlock()
    return _safety_interlock
```

### 3.4 CanaryRolloutService 수정

```python
# packages/selfhealing-python/src/selfhealing/services/canary/service.py

class CanaryRolloutService:
    """Canary Rollout 관리 서비스."""
    
    def __init__(
        self,
        # ... 기존 파라미터
        safety_interlock: Optional[CanarySafetyInterlock] = None,
        enable_safety_interlock: bool = True,
    ):
        # ... 기존 초기화
        self.safety_interlock = safety_interlock or get_canary_safety_interlock()
        self.enable_safety_interlock = enable_safety_interlock
    
    def promote(
        self,
        rollout_id: str,
        force: bool = False,
    ) -> bool:
        """
        다음 단계로 프로모션.
        
        Safety Interlock 체크 후 진행합니다.
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
            return False
        
        # === Safety Interlock 체크 (신규) ===
        if self.enable_safety_interlock and not force:
            interlock_result = self.safety_interlock.check(
                operation="promote",
                rollout_id=rollout_id,
            )
            
            if not interlock_result.allowed:
                logger.warning(
                    f"[CanaryRollout] Promotion blocked by Safety Interlock: "
                    f"action={interlock_result.action.value}, "
                    f"reason={interlock_result.reason}"
                )
                
                # PAUSE/ROLLBACK 자동 적용
                if interlock_result.action == InterlockAction.PAUSE:
                    self.pause(rollout_id, reason=interlock_result.reason)
                elif interlock_result.action == InterlockAction.ROLLBACK:
                    self.rollback(rollout_id, reason=interlock_result.reason)
                
                return False
        
        # === 기존 로직 ===
        if rollout.state not in (CanaryState.CANARY, CanaryState.PAUSED):
            logger.warning(
                f"[CanaryRollout] Cannot promote: state={rollout.state}"
            )
            return False
        
        # 메트릭 검증 (force가 아니면)
        if not force:
            metrics = self._collect_stage_metrics(rollout)
            is_healthy, failure_reason = self._is_stage_healthy(
                rollout.current_stage,
                metrics,
            )
            
            if not is_healthy:
                logger.warning(
                    f"[CanaryRollout] Promotion blocked: {failure_reason}"
                )
                return False
        
        # ... 나머지 기존 로직
```

### 3.5 Event Handler (자동 적용)

```python
# packages/selfhealing-python/src/selfhealing/services/canary/event_handlers.py

class CanaryInterlockEventHandler:
    """
    Emergency 이벤트에 대응하는 Canary Interlock 핸들러.
    
    EmergencyCoordinator에서 이벤트를 수신하여
    진행 중인 모든 Canary에 Safety Interlock을 적용합니다.
    """
    
    def __init__(
        self,
        canary_service: CanaryRolloutService,
        safety_interlock: CanarySafetyInterlock,
    ):
        self.canary_service = canary_service
        self.safety_interlock = safety_interlock
    
    def on_emergency_level_changed(
        self,
        old_level: EmergencyLevel,
        new_level: EmergencyLevel,
        namespace: str,
        trigger_event_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Emergency Level 변경 시 모든 활성 Canary에 Interlock 적용.
        
        Args:
            old_level: 이전 레벨
            new_level: 새 레벨
            namespace: 네임스페이스
            trigger_event_id: 트리거 이벤트 ID
        
        Returns:
            적용 결과 목록
        """
        # 레벨이 하락하면 (복구 중) 아무것도 안 함
        if new_level.value <= old_level.value:
            logger.info(
                f"[CanaryInterlock] Emergency level decreased: "
                f"{old_level.name} -> {new_level.name}, no action needed"
            )
            return []
        
        # 활성 롤아웃 조회
        active_rollouts = self.canary_service.get_active_rollouts()
        
        if not active_rollouts:
            logger.info("[CanaryInterlock] No active rollouts to interlock")
            return []
        
        # 각 롤아웃에 Interlock 적용
        results = []
        for rollout in active_rollouts:
            interlock_result = self.safety_interlock.check_and_apply(
                canary_service=self.canary_service,
                rollout_id=rollout.id,
                operation="emergency_response",
                namespace=namespace,
            )
            
            results.append({
                "rollout_id": rollout.id,
                "config_type": rollout.config_type,
                "action": interlock_result.action.value,
                "success": True,
                "reason": interlock_result.reason,
            })
        
        logger.warning(
            f"[CanaryInterlock] Applied interlock to {len(results)} rollouts: "
            f"level={new_level.name}, namespace={namespace}"
        )
        
        return results


def register_canary_interlock_handlers(event_bus):
    """이벤트 버스에 Canary Interlock 핸들러 등록."""
    from selfhealing.services.canary import get_canary_rollout_service
    
    handler = CanaryInterlockEventHandler(
        canary_service=get_canary_rollout_service(),
        safety_interlock=get_canary_safety_interlock(),
    )
    
    event_bus.subscribe(
        "EmergencyLevelChanged",
        handler.on_emergency_level_changed,
    )
    
    logger.info("[CanaryInterlock] Event handlers registered")
```

### 3.6 Pause 사유 추적 확장

```python
# packages/selfhealing-python/src/selfhealing/services/canary/models.py (수정)

@dataclass
class CanaryRollout:
    """Canary 롤아웃 정보 (확장)."""
    
    # ... 기존 필드 ...
    
    # 신규: PAUSED 상태 사유 추적
    pause_reason: Optional[str] = None
    """일시 중지 사유."""
    
    pause_triggered_by: Optional[str] = None
    """
    일시 중지 트리거 유형.
    
    Values:
    - "interlock": Safety Interlock에 의해 자동 중지
    - "manual": 운영자 수동 중지
    - "chaos_guard": Chaos Guard에 의해 중지
    - "metrics": 메트릭 악화로 인한 중지
    """
    
    pause_causation_chain_id: Optional[str] = None
    """
    CausationChain 연동 ID.
    
    "왜 배포가 멈췄는지" 인과관계 추적을 위해 CascadeEventAuditor와 연동.
    Reference: 76_CASCADE_EVENT_AUDIT.md
    """
    
    paused_at: Optional[datetime] = None
    """일시 중지 시각."""


# packages/selfhealing-python/src/selfhealing/services/canary/service.py (수정)

def pause(
    self,
    rollout_id: str,
    reason: str = "",
    triggered_by: str = "manual",
    causation_chain_id: Optional[str] = None,
) -> bool:
    """
    롤아웃 일시 중지 (사유 추적 확장).
    
    Args:
        rollout_id: 롤아웃 ID
        reason: 일시 중지 사유
        triggered_by: 트리거 유형 (interlock, manual, chaos_guard, metrics)
        causation_chain_id: CausationChain ID (있는 경우)
    
    Returns:
        성공 여부
    """
    rollout = self.get_rollout(rollout_id)
    if not rollout or rollout.state != CanaryState.CANARY:
        return False
    
    rollout.state = CanaryState.PAUSED
    rollout.pause_reason = reason
    rollout.pause_triggered_by = triggered_by
    rollout.pause_causation_chain_id = causation_chain_id
    rollout.paused_at = utc_now()
    
    self._save_rollout(rollout)
    
    log_canary_action(
        action="pause",
        rollout=rollout,
        additional_context={
            "pause_reason": reason,
            "triggered_by": triggered_by,
            "causation_chain_id": causation_chain_id,
        },
    )
    
    logger.info(
        f"[CanaryRollout] Paused: id={rollout_id}, "
        f"triggered_by={triggered_by}, reason={reason}"
    )
    return True
```

### 3.7 Fail-Closed 정책 (Fail-Safe)

> **리뷰 반영**: 인터락 자체의 보안 장치 (§리뷰 ④)

```python
# packages/selfhealing-python/src/selfhealing/services/canary/interlock.py

@dataclass
class FailClosedConfig:
    """
    Fail-Closed 정책 설정.
    
    Redis/백엔드 장애 시 시스템을 안전한 상태로 유지하기 위한 설정.
    
    Reference:
        critical_path_fallback.py#L158 (_get_default_state 패턴)
        cluster_identity.py#L70 (Fail-Fast 패턴)
    """
    
    enabled: bool = True
    """
    True (기본값): 백엔드 장애 시 LEVEL_3로 간주 → 모든 배포 차단
    False: 백엔드 장애 시 NORMAL로 간주 → 배포 허용 (위험!)
    
    프로덕션에서는 반드시 True 사용.
    """
    
    assumed_level_on_failure: EmergencyLevel = EmergencyLevel.LEVEL_3
    """백엔드 장애 시 가정할 Emergency 레벨."""
    
    retry_count: int = 2
    """백엔드 조회 재시도 횟수."""
    
    retry_delay_ms: int = 100
    """재시도 간 대기 시간 (밀리초)."""
    
    timeout_ms: int = 1000
    """백엔드 조회 타임아웃 (밀리초)."""
    
    log_level_on_failure: str = "critical"
    """장애 발생 시 로그 레벨."""


def check_with_fail_safe(
    interlock: CanarySafetyInterlock,
    operation: str,
    rollout_id: str,
    namespace: Optional[str] = None,
    fail_closed_config: Optional[FailClosedConfig] = None,
) -> InterlockResult:
    """
    Fail-Safe가 적용된 인터락 체크.
    
    백엔드 장애 시 Fail-Closed 정책에 따라 안전한 상태 반환.
    """
    config = fail_closed_config or FailClosedConfig()
    
    for attempt in range(config.retry_count + 1):
        try:
            return interlock.check(operation, rollout_id, namespace)
        except Exception as e:
            if attempt < config.retry_count:
                import time
                time.sleep(config.retry_delay_ms / 1000)
                continue
            
            # 모든 재시도 실패
            if config.enabled:
                logger.log(
                    getattr(logging, config.log_level_on_failure.upper()),
                    f"[CanaryInterlock] Backend unavailable after {config.retry_count} retries, "
                    f"applying Fail-Closed (assumed LEVEL_3): {e}"
                )
                return InterlockResult.fail_closed(
                    failure=InterlockCheckFailure.BACKEND_UNAVAILABLE,
                    namespace=namespace or "unknown",
                    error_message=str(e),
                )
            else:
                logger.error(
                    f"[CanaryInterlock] Backend unavailable, Fail-Open mode (DANGEROUS): {e}"
                )
                return InterlockResult.allow(
                    EmergencyLevel.NORMAL,
                    namespace or "unknown",
                )
```

### 3.8 Emergency Override 정책 (Break Glass)

> **리뷰 반영**: 긴급 수정 시나리오 대응 (§리뷰 ①)
> 
> **네이밍 선택**: `EmergencyOverride`
> - 기존 `manual_override` 패턴([circuit_breaker.py#L567](packages/selfhealing-python/src/selfhealing/adapters/redis/circuit_breaker.py#L567))과 일관성
> - `bypass`보다 `override`가 기존 코드베이스에서 더 많이 사용됨
> - "Break Glass"는 문서용 개념명, 코드에서는 `EmergencyOverride` 사용

```python
# packages/selfhealing-python/src/selfhealing/services/canary/interlock.py

@dataclass
class EmergencyOverrideRequest:
    """
    Emergency Override (Break Glass) 요청.
    
    장애 복구를 위한 긴급 설정 변경 시 인터락을 우회합니다.
    
    Reference:
        circuit_breaker.py#L567 (manual_override 패턴)
        governance.py#L404 (acknowledge_warning 패턴)
    
    Warning:
        이 기능은 장애 복구용으로만 사용해야 합니다.
        사용 시 PIR(Post-Incident Review)가 필수입니다.
    """
    
    reason: str
    """우회 사유 (필수, 최소 10자 이상)."""
    
    requested_by: str
    """요청자 (이메일 또는 사용자명)."""
    
    ticket_id: Optional[str] = None
    """관련 티켓/인시던트 ID (강력 권장)."""
    
    expires_at: Optional[datetime] = None
    """
    우회 만료 시각.
    
    None이면 기본값 1시간 적용.
    Reference: OverrideTTLConfig.default_override_ttl_minutes
    """
    
    approval_token: Optional[str] = None
    """
    사전 승인 토큰 (LEVEL_3에서는 필수).
    
    LEVEL_3 상황에서는 2인 승인(dual approval)이 필요하며,
    승인된 토큰이 있어야 우회 가능.
    """
    
    acknowledged_risks: List[str] = field(default_factory=list)
    """
    인지한 위험 목록.
    
    예: ["may_cause_data_inconsistency", "may_extend_outage"]
    """
    
    def is_valid(self) -> bool:
        """요청 유효성 검증."""
        if not self.reason or len(self.reason) < 10:
            return False
        if not self.requested_by:
            return False
        return True
    
    def requires_approval_token(self, level: EmergencyLevel) -> bool:
        """현재 레벨에서 승인 토큰이 필요한지 확인."""
        return level == EmergencyLevel.LEVEL_3


@dataclass
class EmergencyOverridePolicy:
    """
    Emergency Override 정책.
    
    어떤 조건에서 우회를 허용할지 정의합니다.
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
    
    allowed_roles: List[str] = field(
        default_factory=lambda: ["admin", "sre", "on_call"]
    )
    """우회 가능 역할."""
    
    pir_required: bool = True
    """Post-Incident Review 필수 여부."""


def log_interlock_bypass(
    rollout_id: Optional[str],
    operation: str,
    override: EmergencyOverrideRequest,
    namespace: Optional[str],
) -> str:
    """
    인터락 우회를 감사 로그에 기록.
    
    DANGEROUS_BYPASS_INTERLOCK 마킹과 함께 PIR 필수 플래그 설정.
    """
    from selfhealing.services.audit_helpers import log_config_change
    
    audit_entry = {
        "event_type": "DANGEROUS_BYPASS_INTERLOCK",
        "severity": "CRITICAL",
        "rollout_id": rollout_id,
        "operation": operation,
        "namespace": namespace,
        "override_reason": override.reason,
        "requested_by": override.requested_by,
        "ticket_id": override.ticket_id,
        "acknowledged_risks": override.acknowledged_risks,
        "requires_incident_review": True,  # PIR 필수 플래그
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    logger.critical(
        f"[CanaryInterlock] DANGEROUS BYPASS: "
        f"rollout={rollout_id}, operation={operation}, "
        f"by={override.requested_by}, reason={override.reason}"
    )
    
    return log_config_change(
        config_type="canary_interlock_bypass",
        action="EMERGENCY_OVERRIDE",
        details=audit_entry,
    )
```

### 3.9 Regional Interlock 정책 (리전 격리)

> **리뷰 반영**: 리전 격리 기반 롤백 (§리뷰 ②)
> 
> **네이밍 선택**: `RegionalInterlockPolicy` + `RegionalInterlockBehavior`
> - 기존 `EmergencyScope.REGIONAL` 패턴([72문서#L267](72_EMERGENCY_COORDINATION_LAYER.md#L267))과 일관성
> - `regional_policy`는 기존 네이밍 패턴과 일치

```python
# packages/selfhealing-python/src/selfhealing/services/canary/interlock.py

class RegionalInterlockBehavior(str, Enum):
    """
    리전별 인터락 행동 정의.
    
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
    해당 리전의 previous_values로 복원하고, 다른 리전은 계속 진행.
    
    주의: 리전 간 설정 불일치가 발생할 수 있음.
    """
    
    HYBRID = "hybrid"
    """
    하이브리드 전략.
    
    - 영향 리전: 즉시 롤백
    - 나머지 리전: 일시 중지 (PAUSE)
    
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
    리전별 인터락 정책.
    
    Reference:
        73_NAMESPACE_AWARE_EMERGENCY.md (리전별 긴급 모드 격리)
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
    """
    리전 롤백 후 수동 재개 필수 여부.
    
    True면 리전 롤백 후 운영자가 명시적으로 resume() 호출 필요.
    """
    
    max_affected_regions_for_isolated_rollback: int = 1
    """
    격리 롤백 허용 최대 영향 리전 수.
    
    2개 이상 리전이 영향받으면 무조건 PAUSE_ALL.
    """
    
    def get_behavior_for_situation(
        self,
        affected_regions: List[str],
        total_regions: List[str],
        is_region_isolated_deployment: bool,
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
        # 전체 리전이 영향받으면 무조건 PAUSE_ALL
        if len(affected_regions) >= len(total_regions):
            return RegionalInterlockBehavior.PAUSE_ALL
        
        # 격리 배포가 아니면 PAUSE_ALL 또는 HYBRID만
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
```

### 3.10 PAUSED 상태 확장 구현

> **리뷰 반영**: 상태 머신의 확장 (§리뷰 ③)

기존 `CanaryState.PAUSED`를 활용하되, 사유와 인과관계를 추적합니다.

```python
# packages/selfhealing-python/src/selfhealing/services/canary/pause_tracker.py

@dataclass
class PauseContext:
    """
    PAUSED 상태의 컨텍스트 정보.
    
    "왜 배포가 멈췄는지"를 운영자에게 명확히 설명합니다.
    """
    
    reason: str
    """일시 중지 사유."""
    
    triggered_by: str
    """트리거 유형 (interlock, manual, chaos_guard, metrics)."""
    
    emergency_level: Optional[EmergencyLevel] = None
    """인터락 발동 시의 Emergency 레벨."""
    
    namespace: Optional[str] = None
    """영향받은 네임스페이스."""
    
    causation_chain_id: Optional[str] = None
    """CausationChain ID (인과관계 추적)."""
    
    paused_at: datetime = field(default_factory=utc_now)
    """일시 중지 시각."""
    
    auto_resume_condition: Optional[str] = None
    """자동 재개 조건 (있는 경우)."""
    
    estimated_resume_at: Optional[datetime] = None
    """예상 재개 시각 (있는 경우)."""
    
    def explain(self) -> str:
        """
        운영자를 위한 설명 생성.
        
        Returns:
            사람이 읽을 수 있는 설명 문자열
        """
        parts = [f"배포가 일시 중지되었습니다."]
        
        if self.triggered_by == "interlock":
            parts.append(
                f"원인: Emergency {self.emergency_level.name if self.emergency_level else 'Unknown'} 발생"
            )
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


class PauseReasonTracker:
    """
    PAUSED 상태 사유 추적기.
    
    CausationChain과 연동하여 인과관계를 박제합니다.
    """
    
    def __init__(self, cascade_auditor: Optional["CascadeEventAuditor"] = None):
        self._cascade_auditor = cascade_auditor
    
    def record_pause(
        self,
        rollout_id: str,
        context: PauseContext,
    ) -> str:
        """
        PAUSE 이벤트 기록.
        
        Returns:
            생성된 causation_chain_id
        """
        if self._cascade_auditor:
            # CascadeEventAuditor와 연동
            chain_id = self._cascade_auditor.record(
                trigger_type="CANARY_PAUSED",
                trigger_details={
                    "rollout_id": rollout_id,
                    "triggered_by": context.triggered_by,
                    "emergency_level": (
                        context.emergency_level.name 
                        if context.emergency_level else None
                    ),
                    "namespace": context.namespace,
                },
                effects=[{"action": "pause", "rollout_id": rollout_id}],
            )
            context.causation_chain_id = chain_id
            return chain_id
        
        # CascadeEventAuditor 없으면 UUID 생성
        import uuid
        return str(uuid.uuid4())
```

### 3.11 Emergency State Refresher (주기적 동기화)

> **리뷰 반영**: Pub/Sub 유실 대비 주기적 동기화 (신뢰성 보강)
> 
> **네이밍 선택**: `EmergencyStateRefresher`
> - 기존 `SELFHEALING_SYNC_ON_STARTUP` 패턴([startup_hydration.py](tests/self_healing/django/test_startup_hydration.py#L42))과 일관성
> - `Synchronizer`보다 `Refresher`가 "주기적 갱신" 의미를 더 명확히 전달
> - `refresh_` 패턴은 캐시 갱신에서 흔히 사용

```python
# packages/selfhealing-python/src/selfhealing/services/canary/state_refresher.py

import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any


@dataclass
class StateRefresherConfig:
    """
    Emergency State Refresher 설정.
    
    Reference:
        startup_hydration.py#L42 (SELFHEALING_SYNC_ON_STARTUP 패턴)
    """
    
    refresh_interval_seconds: int = 30
    """상태 갱신 주기 (기본 30초)."""
    
    jitter_max_seconds: int = 5
    """
    갱신 주기 지터 (0 ~ jitter_max_seconds 랜덤 추가).
    
    모든 인스턴스가 동시에 갱신하는 것을 방지.
    """
    
    enabled: bool = True
    """Refresher 활성화 여부."""
    
    on_refresh_failure_action: str = "log_and_continue"
    """
    갱신 실패 시 행동.
    
    Values:
    - "log_and_continue": 로깅 후 계속 (기본값)
    - "fail_closed": 모든 배포 차단
    """


class EmergencyStateRefresher:
    """
    Emergency 상태 주기적 갱신기.
    
    Redis Pub/Sub 메시지가 유실되더라도 최대 refresh_interval_seconds 내에
    시스템이 안전 상태로 수렴(Convergence)하도록 보장합니다.
    
    Features:
    - 주기적 상태 폴링 (30초 기본)
    - 지터를 통한 Thundering Herd 방지
    - 상태 변경 시 인터락 자동 적용
    
    Reference:
        partition_reconciliation.py#L419 (상태 동기화 패턴)
    """
    
    def __init__(
        self,
        config: Optional[StateRefresherConfig] = None,
        safety_interlock: Optional[CanarySafetyInterlock] = None,
        canary_service: Optional["CanaryRolloutService"] = None,
    ):
        self._config = config or StateRefresherConfig()
        self._safety_interlock = safety_interlock
        self._canary_service = canary_service
        self._last_known_level: Dict[str, EmergencyLevel] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
    
    def start(self) -> None:
        """백그라운드 갱신 시작."""
        if not self._config.enabled:
            logger.info("[StateRefresher] Disabled, not starting")
            return
        
        with self._lock:
            if self._running:
                return
            
            self._running = True
            self._thread = threading.Thread(
                target=self._refresh_loop,
                daemon=True,
                name="EmergencyStateRefresher",
            )
            self._thread.start()
            logger.info(
                f"[StateRefresher] Started with interval={self._config.refresh_interval_seconds}s"
            )
    
    def stop(self) -> None:
        """백그라운드 갱신 중지."""
        with self._lock:
            self._running = False
            if self._thread:
                self._thread.join(timeout=5)
                self._thread = None
        logger.info("[StateRefresher] Stopped")
    
    def _refresh_loop(self) -> None:
        """갱신 루프."""
        import random
        import time
        
        while self._running:
            try:
                self._do_refresh()
            except Exception as e:
                logger.error(f"[StateRefresher] Refresh failed: {e}")
            
            # 다음 갱신까지 대기 (지터 포함)
            jitter = random.uniform(0, self._config.jitter_max_seconds)
            time.sleep(self._config.refresh_interval_seconds + jitter)
    
    def _do_refresh(self) -> None:
        """실제 갱신 수행."""
        if not self._safety_interlock:
            return
        
        try:
            tracker = self._safety_interlock._get_emergency_tracker()
            
            # 현재 네임스페이스 상태 조회
            current_state = tracker.get_effective_state()
            current_level = current_state.emergency_level
            namespace = current_state.namespace
            
            # 이전 상태와 비교
            last_level = self._last_known_level.get(namespace)
            
            if last_level is not None and current_level != last_level:
                # 레벨 변경 감지 → 인터락 적용
                logger.warning(
                    f"[StateRefresher] Level change detected via polling: "
                    f"{last_level.name} -> {current_level.name}"
                )
                
                if current_level.value > last_level.value and self._canary_service:
                    # 레벨 상승 → 활성 롤아웃에 인터락 적용
                    self._apply_interlock_to_active_rollouts(namespace)
            
            self._last_known_level[namespace] = current_level
            
        except Exception as e:
            logger.error(f"[StateRefresher] Failed to refresh state: {e}")
            if self._config.on_refresh_failure_action == "fail_closed":
                # 모든 활성 롤아웃 일시 중지
                self._pause_all_active_rollouts()
    
    def _apply_interlock_to_active_rollouts(self, namespace: str) -> None:
        """활성 롤아웃에 인터락 적용."""
        if not self._canary_service or not self._safety_interlock:
            return
        
        active_rollouts = self._canary_service.get_active_rollouts()
        for rollout in active_rollouts:
            self._safety_interlock.check_and_apply(
                canary_service=self._canary_service,
                rollout_id=rollout.id,
                operation="state_refresh",
                namespace=namespace,
            )
    
    def _pause_all_active_rollouts(self) -> None:
        """모든 활성 롤아웃 일시 중지 (Fail-Closed)."""
        if not self._canary_service:
            return
        
        active_rollouts = self._canary_service.get_active_rollouts()
        for rollout in active_rollouts:
            self._canary_service.pause(
                rollout.id,
                reason="[StateRefresher] Backend unavailable - Fail-Closed",
                triggered_by="interlock",
            )
```

### 3.12 Mid-Apply Interlock Check (적용 중 인터락 재체크)

> **리뷰 반영**: 런타임 세이프티 정교화 (Race Condition 방어)

```python
# packages/selfhealing-python/src/selfhealing/services/canary/mid_apply_check.py

@dataclass
class MidApplyCheckResult:
    """
    적용 중 인터락 체크 결과.
    
    다중 클러스터 배포 루프 중간에 Emergency가 발생했을 때
    어떻게 대응할지 결정합니다.
    """
    
    should_continue: bool
    """배포 계속 진행 여부."""
    
    interlock_triggered: bool
    """인터락이 발동되었는지 여부."""
    
    applied_clusters: List[str]
    """이미 적용된 클러스터 목록 (부분 롤백 대상)."""
    
    remaining_clusters: List[str]
    """아직 적용되지 않은 클러스터 목록."""
    
    rollback_required: bool
    """부분 롤백이 필요한지 여부."""
    
    interlock_result: Optional[InterlockResult] = None
    """인터락 체크 결과 (발동된 경우)."""


def apply_with_mid_check(
    canary_service: "CanaryRolloutService",
    rollout: "CanaryRollout",
    target_clusters: List[str],
    safety_interlock: CanarySafetyInterlock,
    check_interval: int = 1,
) -> MidApplyCheckResult:
    """
    인터락 재체크를 포함한 클러스터 적용.
    
    각 클러스터에 설정을 적용하기 전에 인터락 상태를 재확인하고,
    Emergency 발생 시 즉시 중단하고 부분 롤백을 수행합니다.
    
    Args:
        canary_service: CanaryRolloutService 인스턴스
        rollout: 롤아웃 정보
        target_clusters: 적용 대상 클러스터 목록
        safety_interlock: Safety Interlock 인스턴스
        check_interval: N개 클러스터마다 인터락 체크 (기본 1 = 매번)
    
    Returns:
        MidApplyCheckResult: 적용 결과
    
    Reference:
        cross_cluster.py (notify_config_change_partial_failure 패턴)
    """
    applied_clusters: List[str] = []
    remaining_clusters = target_clusters.copy()
    
    for i, cluster in enumerate(target_clusters):
        # 인터락 재체크 (check_interval마다)
        if i % check_interval == 0:
            interlock_result = safety_interlock.check(
                operation="mid_apply",
                rollout_id=rollout.id,
            )
            
            if not interlock_result.allowed:
                logger.warning(
                    f"[MidApplyCheck] Interlock triggered during apply: "
                    f"cluster={cluster}, applied={len(applied_clusters)}/{len(target_clusters)}"
                )
                
                # 부분 롤백 수행
                if applied_clusters:
                    _rollback_applied_clusters(
                        canary_service,
                        rollout,
                        applied_clusters,
                        reason=f"[MID-APPLY-INTERLOCK] {interlock_result.reason}",
                    )
                
                return MidApplyCheckResult(
                    should_continue=False,
                    interlock_triggered=True,
                    applied_clusters=applied_clusters,
                    remaining_clusters=remaining_clusters,
                    rollback_required=len(applied_clusters) > 0,
                    interlock_result=interlock_result,
                )
        
        # 클러스터에 설정 적용
        try:
            canary_service._apply_config_to_cluster(
                cluster,
                rollout.config_type,
                rollout.new_values,
            )
            applied_clusters.append(cluster)
            remaining_clusters.remove(cluster)
        except Exception as e:
            logger.error(f"[MidApplyCheck] Failed to apply to {cluster}: {e}")
            # 적용 실패 시에도 부분 롤백 고려
    
    return MidApplyCheckResult(
        should_continue=True,
        interlock_triggered=False,
        applied_clusters=applied_clusters,
        remaining_clusters=[],
        rollback_required=False,
    )


def _rollback_applied_clusters(
    canary_service: "CanaryRolloutService",
    rollout: "CanaryRollout",
    applied_clusters: List[str],
    reason: str,
) -> None:
    """
    이미 적용된 클러스터들만 롤백.
    
    Args:
        canary_service: CanaryRolloutService 인스턴스
        rollout: 롤아웃 정보
        applied_clusters: 롤백 대상 클러스터 목록
        reason: 롤백 사유
    """
    logger.warning(
        f"[MidApplyCheck] Partial rollback: "
        f"clusters={applied_clusters}, reason={reason}"
    )
    
    for cluster in applied_clusters:
        try:
            canary_service._apply_config_to_cluster(
                cluster,
                rollout.config_type,
                rollout.previous_values,  # 이전 값으로 복원
            )
        except Exception as e:
            logger.error(
                f"[MidApplyCheck] Failed to rollback {cluster}: {e}"
            )
    
    # Audit 로그
    from selfhealing.services.canary.audit import log_canary_action
    log_canary_action(
        action="partial_rollback",
        rollout=rollout,
        additional_context={
            "rolled_back_clusters": applied_clusters,
            "reason": reason,
        },
    )
```

### 3.13 Rollback Value Resolver (다단계 롤백 폴백)

> **리뷰 반영**: 데이터 무결성 강화 (previous_values 유실 대비)

```python
# packages/selfhealing-python/src/selfhealing/services/canary/rollback_resolver.py

from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict, Optional


class RollbackValueSource(str, Enum):
    """롤백 값의 출처."""
    
    PREVIOUS_VALUES = "previous_values"
    """Tier 1: 롤아웃에 저장된 이전 값."""
    
    CONFIG_HISTORY = "config_history"
    """Tier 2: ConfigHistory 저장소에서 조회."""
    
    DEFAULT_CONFIG = "default_config"
    """Tier 3: 시스템 정의 기본값."""
    
    UNKNOWN = "unknown"
    """출처 불명 (오류)."""


@dataclass
class ResolvedRollbackValue:
    """
    해결된 롤백 값.
    
    어떤 출처에서 값을 가져왔는지 명시합니다.
    """
    
    values: Dict[str, Any]
    """롤백할 설정값."""
    
    source: RollbackValueSource
    """값의 출처."""
    
    is_fallback: bool
    """폴백을 사용했는지 여부."""
    
    fallback_reason: Optional[str] = None
    """폴백 사용 이유 (is_fallback=True일 때)."""
    
    warning: Optional[str] = None
    """경고 메시지 (있는 경우)."""


class RollbackValueResolver:
    """
    롤백 값 해결기.
    
    Multi-Stage Fallback 스키마:
    - Tier 1: previous_values (메모리/DB 내 저장된 이전 값)
    - Tier 2: ConfigHistory.get_version_before() (설정 이력 저장소 조회)
    - Tier 3: DefaultConfig (시스템 정의 기본값)
    
    Reference:
        critical_path_fallback.py#L291 (_get_default_state 패턴)
        runtime_config/base.py#L329 (default_config 패턴)
    """
    
    def __init__(
        self,
        config_history: Optional["ConfigHistoryService"] = None,
        default_configs: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """
        RollbackValueResolver 초기화.
        
        Args:
            config_history: ConfigHistoryService 인스턴스
            default_configs: config_type별 기본값 딕셔너리
        """
        self._config_history = config_history
        self._default_configs = default_configs or self._load_default_configs()
    
    def _load_default_configs(self) -> Dict[str, Dict[str, Any]]:
        """시스템 기본값 로드."""
        return {
            "circuit_breaker": {
                "failure_threshold": 5,
                "recovery_timeout_seconds": 60,
                "half_open_max_calls": 3,
            },
            "dlq": {
                "max_retry_count": 3,
                "replay_batch_size": 10,
            },
            "rate_limiter": {
                "requests_per_second": 100,
                "burst_size": 150,
            },
            "retry": {
                "max_attempts": 3,
                "initial_delay_ms": 100,
                "max_delay_ms": 5000,
                "exponential_base": 2.0,
            },
        }
    
    def resolve(
        self,
        rollout: "CanaryRollout",
    ) -> ResolvedRollbackValue:
        """
        롤백 값 해결.
        
        3단계 폴백을 통해 롤백할 값을 결정합니다.
        
        Args:
            rollout: 롤아웃 정보
        
        Returns:
            ResolvedRollbackValue: 해결된 롤백 값
        """
        # Tier 1: previous_values
        if rollout.previous_values:
            return ResolvedRollbackValue(
                values=rollout.previous_values,
                source=RollbackValueSource.PREVIOUS_VALUES,
                is_fallback=False,
            )
        
        logger.warning(
            f"[RollbackResolver] previous_values empty for rollout={rollout.id}, "
            f"falling back to ConfigHistory"
        )
        
        # Tier 2: ConfigHistory
        if self._config_history:
            try:
                version = self._config_history.get_version_before(
                    config_type=rollout.config_type,
                    before_time=rollout.created_at,
                )
                if version and version.values:
                    return ResolvedRollbackValue(
                        values=version.values,
                        source=RollbackValueSource.CONFIG_HISTORY,
                        is_fallback=True,
                        fallback_reason="previous_values was empty",
                    )
            except Exception as e:
                logger.error(
                    f"[RollbackResolver] ConfigHistory lookup failed: {e}"
                )
        
        logger.warning(
            f"[RollbackResolver] ConfigHistory unavailable for rollout={rollout.id}, "
            f"falling back to DefaultConfig"
        )
        
        # Tier 3: DefaultConfig
        default = self._default_configs.get(rollout.config_type)
        if default:
            return ResolvedRollbackValue(
                values=default,
                source=RollbackValueSource.DEFAULT_CONFIG,
                is_fallback=True,
                fallback_reason="previous_values and ConfigHistory both unavailable",
                warning=(
                    "Using system default config. "
                    "This may not reflect the actual previous state."
                ),
            )
        
        # 최악의 경우: 빈 값 반환 (위험!)
        logger.critical(
            f"[RollbackResolver] No rollback value found for rollout={rollout.id}, "
            f"config_type={rollout.config_type}. Returning empty dict (DANGEROUS!)"
        )
        
        return ResolvedRollbackValue(
            values={},
            source=RollbackValueSource.UNKNOWN,
            is_fallback=True,
            fallback_reason="All fallback tiers exhausted",
            warning="CRITICAL: No rollback value available. Manual intervention required.",
        )


def rollback_with_resolver(
    canary_service: "CanaryRolloutService",
    rollout_id: str,
    reason: str,
    resolver: Optional[RollbackValueResolver] = None,
) -> bool:
    """
    RollbackValueResolver를 사용한 안전한 롤백.
    
    Args:
        canary_service: CanaryRolloutService 인스턴스
        rollout_id: 롤아웃 ID
        reason: 롤백 사유
        resolver: RollbackValueResolver 인스턴스 (None이면 기본값 생성)
    
    Returns:
        성공 여부
    """
    rollout = canary_service.get_rollout(rollout_id)
    if not rollout:
        return False
    
    resolver = resolver or RollbackValueResolver(
        config_history=canary_service.config_history,
    )
    
    resolved = resolver.resolve(rollout)
    
    # 경고 로깅
    if resolved.warning:
        logger.warning(
            f"[RollbackResolver] {resolved.warning}, "
            f"rollout={rollout_id}, source={resolved.source.value}"
        )
    
    # 해결된 값으로 롤백 수행
    for cluster in rollout.affected_clusters:
        canary_service._apply_config_to_cluster(
            cluster,
            rollout.config_type,
            resolved.values,
        )
    
    # 상태 업데이트
    rollout.state = CanaryState.ROLLED_BACK
    rollout.rollback_reason = (
        f"{reason} (source: {resolved.source.value})"
        if resolved.is_fallback
        else reason
    )
    rollout.completed_at = utc_now()
    canary_service._save_rollout(rollout)
    canary_service._remove_from_active(rollout.id)
    
    # Audit 로그
    from selfhealing.services.canary.audit import log_canary_action
    log_canary_action(
        action="rollback",
        rollout=rollout,
        additional_context={
            "rollback_value_source": resolved.source.value,
            "is_fallback": resolved.is_fallback,
            "fallback_reason": resolved.fallback_reason,
        },
    )
    
    return True
```

### 3.14 Interlock Bypass Audit (우회 시 PIR 강제)

> **리뷰 반영**: 거버넌스 강화 (PIR 필수 마킹)

```python
# packages/selfhealing-python/src/selfhealing/services/canary/bypass_audit.py

@dataclass
class InterlockBypassAuditEntry:
    """
    인터락 우회 감사 로그 항목.
    
    Post-Incident Review (PIR) 필수 마킹을 포함합니다.
    """
    
    audit_id: str
    """감사 로그 ID."""
    
    timestamp: datetime
    """발생 시각."""
    
    # 롤아웃 정보
    rollout_id: str
    """롤아웃 ID."""
    
    config_type: str
    """설정 유형."""
    
    operation: str
    """수행된 작업 (start, promote, resume)."""
    
    # Emergency 정보
    emergency_level: EmergencyLevel
    """우회 당시 Emergency 레벨."""
    
    namespace: str
    """네임스페이스."""
    
    # 우회 정보
    bypassed_by: str
    """우회 요청자."""
    
    bypass_reason: str
    """우회 사유."""
    
    ticket_id: Optional[str]
    """관련 티켓/인시던트 ID."""
    
    acknowledged_risks: List[str]
    """인지한 위험 목록."""
    
    # 거버넌스 마킹
    severity: str = "CRITICAL"
    """심각도."""
    
    requires_incident_review: bool = True
    """PIR 필수 여부."""
    
    incident_review_due_hours: int = 48
    """PIR 마감 시한 (시간)."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "audit_id": self.audit_id,
            "event_type": "DANGEROUS_BYPASS_INTERLOCK",
            "timestamp": self.timestamp.isoformat(),
            "rollout_id": self.rollout_id,
            "config_type": self.config_type,
            "operation": self.operation,
            "emergency_level": self.emergency_level.name,
            "namespace": self.namespace,
            "bypassed_by": self.bypassed_by,
            "bypass_reason": self.bypass_reason,
            "ticket_id": self.ticket_id,
            "acknowledged_risks": self.acknowledged_risks,
            "severity": self.severity,
            "governance": {
                "requires_incident_review": self.requires_incident_review,
                "incident_review_due_hours": self.incident_review_due_hours,
                "review_due_at": (
                    self.timestamp + timedelta(hours=self.incident_review_due_hours)
                ).isoformat(),
            },
        }


class InterlockBypassAuditor:
    """
    인터락 우회 감사기.
    
    우회 발생 시 CRITICAL 레벨로 기록하고 PIR 필수 플래그를 설정합니다.
    """
    
    def record_bypass(
        self,
        rollout: "CanaryRollout",
        operation: str,
        override: "EmergencyOverrideRequest",
        emergency_level: EmergencyLevel,
        namespace: str,
    ) -> InterlockBypassAuditEntry:
        """
        우회 이벤트 기록.
        
        Returns:
            생성된 감사 로그 항목
        """
        import uuid
        
        entry = InterlockBypassAuditEntry(
            audit_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            rollout_id=rollout.id,
            config_type=rollout.config_type,
            operation=operation,
            emergency_level=emergency_level,
            namespace=namespace,
            bypassed_by=override.requested_by,
            bypass_reason=override.reason,
            ticket_id=override.ticket_id,
            acknowledged_risks=override.acknowledged_risks,
        )
        
        # 감사 로그 저장
        self._persist_entry(entry)
        
        # 알림 발송
        self._send_notifications(entry)
        
        # PIR 티켓 생성 (자동화된 경우)
        if entry.requires_incident_review:
            self._create_pir_reminder(entry)
        
        return entry
    
    def _persist_entry(self, entry: InterlockBypassAuditEntry) -> None:
        """감사 로그 영구 저장."""
        from selfhealing.services.audit_helpers import log_config_change
        
        log_config_change(
            config_type="canary_interlock_bypass",
            action="DANGEROUS_BYPASS_INTERLOCK",
            details=entry.to_dict(),
        )
    
    def _send_notifications(self, entry: InterlockBypassAuditEntry) -> None:
        """알림 발송."""
        # Slack, PagerDuty 등 알림 채널로 전송
        logger.critical(
            f"🚨 DANGEROUS BYPASS INTERLOCK 🚨\n"
            f"Rollout: {entry.rollout_id}\n"
            f"Level: {entry.emergency_level.name}\n"
            f"By: {entry.bypassed_by}\n"
            f"Reason: {entry.bypass_reason}\n"
            f"Ticket: {entry.ticket_id or 'NOT PROVIDED'}\n"
            f"⚠️ PIR REQUIRED within {entry.incident_review_due_hours}h"
        )
    
    def _create_pir_reminder(self, entry: InterlockBypassAuditEntry) -> None:
        """PIR 리마인더 생성."""
        # TODO: Jira, PagerDuty 등과 연동하여 자동 티켓 생성
        pass
```

---

## 4. 설정

### 4.1 canary_interlock_policy.yaml

```yaml
# Canary Safety Interlock Policy Configuration
# Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
# Version: 1.1.0 (확장판 - Break Glass, Regional, Fail-Safe 포함)

version: "1.1"

# 기본 설정
defaults:
  enable_safety_interlock: true
  auto_resume_on_recovery: true
  resume_delay_minutes: 5

# Emergency Level별 정책
level_policies:
  NORMAL:
    action: allow
    
  LEVEL_1:
    action: allow_with_warning
    log_level: warning
    notification:
      enabled: true
      channels: ["slack"]
      
  LEVEL_2:
    action: pause
    auto_resume: true
    resume_conditions:
      - level_drops_below: LEVEL_2
      - health_check_passes: true
    notification:
      enabled: true
      channels: ["slack", "email"]
      
  LEVEL_3:
    action: rollback
    immediate: true
    notification:
      enabled: true
      channels: ["slack", "email", "pagerduty"]
      priority: critical

# 복구 정책
recovery:
  # Emergency 해제 시 Canary 자동 재개
  auto_resume:
    enabled: true
    delay_after_normal_minutes: 5
    health_check_required: true
    
  # 롤백된 Canary는 수동 재시작 필요
  rolled_back_handling:
    require_manual_restart: true
    notify_owner: true
```

### 4.2 fail_closed_config.yaml

> **§3.7 Fail-Closed 정책** 설정

```yaml
# Fail-Closed Policy Configuration
# 백엔드 장애 시 안전한 상태 유지

fail_closed:
  enabled: true                          # Fail-Closed 활성화 (프로덕션: true 필수)
  assumed_level_on_failure: LEVEL_3      # 장애 시 가정할 Emergency 레벨
  
  retry:
    count: 2                             # 재시도 횟수
    delay_ms: 100                        # 재시도 간 대기 시간
    timeout_ms: 1000                     # 백엔드 조회 타임아웃
  
  logging:
    level_on_failure: critical           # 장애 발생 시 로그 레벨
    include_stack_trace: true            # 스택 트레이스 포함 여부
  
  alerts:
    notify_on_fail_closed: true          # Fail-Closed 발동 시 알림
    channels: ["slack", "pagerduty"]
```

### 4.3 emergency_override_policy.yaml

> **§3.8 Emergency Override (Break Glass)** 설정

```yaml
# Emergency Override (Break Glass) Policy Configuration
# 장애 복구를 위한 긴급 인터락 우회 정책

emergency_override:
  enabled: true                          # Emergency Override 활성화
  
  # 유효성 검증
  validation:
    min_reason_length: 10                # 최소 사유 길이 (자)
    require_ticket_id: true              # 티켓/인시던트 ID 필수
    require_approval_on_level_3: true    # LEVEL_3에서 사전 승인 필수
  
  # TTL 설정
  ttl:
    default_minutes: 60                  # 기본 우회 만료 시간
    max_minutes: 240                     # 최대 우회 만료 시간 (4시간)
  
  # 접근 제어
  access:
    allowed_roles:                       # 우회 가능 역할
      - admin
      - sre
      - on_call
    
    approval_roles:                      # LEVEL_3 승인 가능 역할
      - admin
      - sre_lead
  
  # 거버넌스
  governance:
    pir_required: true                   # Post-Incident Review 필수
    pir_due_hours: 48                    # PIR 마감 시한
    auto_create_pir_ticket: true         # PIR 티켓 자동 생성
    
  # 감사 로깅
  audit:
    log_level: critical                  # 로그 레벨
    include_acknowledged_risks: true     # 인지한 위험 기록
    notify_security_team: true           # 보안팀 알림
```

### 4.4 regional_interlock_policy.yaml

> **§3.9 Regional Interlock** 설정

```yaml
# Regional Interlock Policy Configuration
# 리전별 인터락 행동 정의

regional_interlock:
  # 기본 행동
  default_behavior: PAUSE_ALL            # pause_all | rollback_affected_only | hybrid | continue_healthy
  
  # 격리 정책
  isolation:
    allow_isolated_rollback: false       # 격리 롤백 허용 (위험한 기능)
    require_manual_resume: true          # 리전 롤백 후 수동 재개 필수
    max_affected_regions: 1              # 격리 롤백 허용 최대 영향 리전 수
  
  # 행동 설명
  # PAUSE_ALL: 어느 리전이든 Emergency면 전체 롤아웃 일시 중지 (가장 안전)
  # ROLLBACK_AFFECTED_ONLY: 영향 리전만 롤백, 다른 리전 계속 (격리 배포 시만)
  # HYBRID: 영향 리전 롤백 + 나머지 일시 중지
  # CONTINUE_HEALTHY: 건강한 리전만 프로모션 계속 (위험, 설정 드리프트 발생 가능)
  
  # 리전별 오버라이드 (선택적)
  region_overrides:
    us-east-1:
      behavior: PAUSE_ALL                # 핵심 리전은 보수적으로
      
    staging-kr:
      behavior: ROLLBACK_AFFECTED_ONLY   # 스테이징은 격리 롤백 허용
```

### 4.5 state_refresher_config.yaml

> **§3.11 Emergency State Refresher** 설정

```yaml
# Emergency State Refresher Configuration
# Pub/Sub 유실 대비 주기적 동기화

state_refresher:
  enabled: true                          # Refresher 활성화
  
  # 갱신 주기
  timing:
    refresh_interval_seconds: 30         # 상태 갱신 주기 (기본 30초)
    jitter_max_seconds: 5                # 랜덤 지터 (Thundering Herd 방지)
  
  # 장애 처리
  on_failure:
    action: log_and_continue             # log_and_continue | fail_closed
    max_consecutive_failures: 3          # 연속 실패 시 fail_closed 전환
  
  # 로깅
  logging:
    log_every_refresh: false             # 매 갱신마다 로그 (디버그용)
    log_on_level_change: true            # 레벨 변경 시 로그
```

### 4.6 rollback_resolver_config.yaml

> **§3.13 Rollback Value Resolver** 설정

```yaml
# Rollback Value Resolver Configuration
# 3단계 롤백 값 폴백 정책

rollback_resolver:
  # Tier 1: previous_values (롤아웃에 저장된 이전 값)
  tier_1:
    source: previous_values
    enabled: true
  
  # Tier 2: ConfigHistory (설정 이력 저장소)
  tier_2:
    source: config_history
    enabled: true
    lookup_strategy: get_version_before  # 롤아웃 생성 시점 이전 버전 조회
  
  # Tier 3: DefaultConfig (시스템 기본값)
  tier_3:
    source: default_config
    enabled: true
    warn_on_use: true                    # 사용 시 경고 로그
    
  # 최악의 경우 (모든 tier 실패)
  fallback_exhausted:
    log_level: critical
    action: manual_intervention          # 수동 개입 필요
    notify_on_call: true
```

### 4.7 mid_apply_check_config.yaml

> **§3.12 Mid-Apply Interlock Check** 설정

```yaml
# Mid-Apply Interlock Check Configuration
# 적용 중 인터락 재체크 정책

mid_apply_check:
  enabled: true                          # Mid-Apply 체크 활성화
  
  # 체크 주기
  check_interval: 1                      # N개 클러스터마다 체크 (1 = 매번)
  
  # 부분 롤백 정책
  partial_rollback:
    enabled: true                        # 부분 적용 시 자동 롤백
    log_level: warning
    
  # 타임아웃
  interlock_check_timeout_ms: 500        # 인터락 체크 타임아웃
```

---

## 5. 테스트

### 5.1 단위 테스트

```python
class TestCanarySafetyInterlock:
    """CanarySafetyInterlock 단위 테스트."""
    
    def test_allow_on_normal(self):
        """NORMAL 레벨에서 허용."""
        interlock = CanarySafetyInterlock()
        
        with patch.object(
            interlock, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.NORMAL,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            result = interlock.check("promote", "rollout-123")
            
            assert result.allowed is True
            assert result.action == InterlockAction.ALLOW
    
    def test_pause_on_level_2(self):
        """LEVEL_2에서 PAUSE."""
        interlock = CanarySafetyInterlock()
        
        with patch.object(
            interlock, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_2,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            result = interlock.check("promote", "rollout-123")
            
            assert result.allowed is False
            assert result.action == InterlockAction.PAUSE
    
    def test_rollback_on_level_3(self):
        """LEVEL_3에서 ROLLBACK."""
        interlock = CanarySafetyInterlock()
        
        with patch.object(
            interlock, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_3,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            result = interlock.check("promote", "rollout-123")
            
            assert result.allowed is False
            assert result.action == InterlockAction.ROLLBACK
    
    def test_check_and_apply_pause(self):
        """check_and_apply가 PAUSE를 자동 적용."""
        interlock = CanarySafetyInterlock()
        mock_service = MagicMock()
        
        with patch.object(
            interlock, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_2,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            result = interlock.check_and_apply(
                canary_service=mock_service,
                rollout_id="rollout-123",
                operation="promote",
            )
            
            assert result.action == InterlockAction.PAUSE
            mock_service.pause.assert_called_once()
```

### 5.2 통합 테스트

```python
class TestCanarySafetyInterlockIntegration:
    """Canary Safety Interlock 통합 테스트."""
    
    def test_promote_blocked_during_level_3(self, canary_service, emergency_manager):
        """LEVEL_3 중 프로모션 차단."""
        # Given: 활성 Canary 롤아웃
        rollout = canary_service.create_rollout(
            config_type="circuit_breaker",
            new_values={"threshold": 5},
            stages=[...],
        )
        canary_service.start_rollout(rollout.id)
        
        # When: LEVEL_3 발생
        emergency_manager.activate_manual(
            level=EmergencyLevel.LEVEL_3,
            reason="Test emergency",
        )
        
        # Then: 프로모션 차단되고 자동 롤백
        success = canary_service.promote(rollout.id)
        
        assert success is False
        
        updated_rollout = canary_service.get_rollout(rollout.id)
        assert updated_rollout.state == CanaryState.ROLLED_BACK
    
    def test_event_driven_rollback(self, canary_service, event_bus):
        """이벤트 기반 자동 롤백."""
        # Given: 활성 Canary 롤아웃
        rollout = canary_service.create_rollout(...)
        canary_service.start_rollout(rollout.id)
        
        # When: EmergencyLevelChanged 이벤트 발생
        event_bus.publish(
            "EmergencyLevelChanged",
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="test",
            trigger_event_id="evt-123",
        )
        
        # Then: 자동 롤백
        updated_rollout = canary_service.get_rollout(rollout.id)
        assert updated_rollout.state == CanaryState.ROLLED_BACK
        assert "[AUTO-INTERLOCK]" in updated_rollout.rollback_reason
```

### 5.3 Fail-Closed 테스트

> **§3.7 Fail-Safe** 검증

```python
class TestFailClosedPolicy:
    """Fail-Closed 정책 테스트."""
    
    def test_fail_closed_on_backend_unavailable(self):
        """백엔드 장애 시 Fail-Closed 동작."""
        interlock = CanarySafetyInterlock(fail_closed=True)
        
        with patch.object(
            interlock, '_get_emergency_tracker'
        ) as mock_tracker:
            # Redis/백엔드 장애 시뮬레이션
            mock_tracker.side_effect = ConnectionError("Redis unavailable")
            
            result = interlock.check("promote", "rollout-123")
            
            # Fail-Closed: 모든 배포 차단
            assert result.allowed is False
            assert result.is_fail_closed is True
            assert result.check_failure == InterlockCheckFailure.BACKEND_UNAVAILABLE
            assert result.action == InterlockAction.ROLLBACK
    
    def test_fail_closed_with_retry(self):
        """Fail-Closed 재시도 후 성공."""
        interlock = CanarySafetyInterlock(fail_closed=True)
        config = FailClosedConfig(retry_count=2)
        
        call_count = 0
        
        def mock_get_tracker():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Transient failure")
            return MagicMock(
                get_effective_state=MagicMock(return_value=ScopedEmergencyState(
                    namespace="test",
                    scope=EmergencyScope.REGIONAL,
                    emergency_level=EmergencyLevel.NORMAL,
                ))
            )
        
        with patch.object(interlock, '_get_emergency_tracker', mock_get_tracker):
            result = check_with_fail_safe(
                interlock, "promote", "rollout-123",
                fail_closed_config=config,
            )
            
            # 재시도 후 성공
            assert result.allowed is True
            assert result.is_fail_closed is False
    
    def test_fail_open_mode_dangerous(self):
        """Fail-Open 모드 (위험)."""
        interlock = CanarySafetyInterlock(fail_closed=False)
        
        with patch.object(
            interlock, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_tracker.side_effect = ConnectionError("Redis unavailable")
            
            # 위험: Fail-Open은 백엔드 장애 시에도 허용
            result = interlock.check("promote", "rollout-123")
            
            # Fail-Open: 배포 허용 (위험!)
            assert result.allowed is True
            assert result.is_fail_closed is False
```

### 5.4 Emergency Override (Break Glass) 테스트

> **§3.8 긴급 우회** 검증

```python
class TestEmergencyOverride:
    """Emergency Override (Break Glass) 테스트."""
    
    def test_override_allows_during_emergency(self):
        """긴급 우회로 Emergency 중 배포 허용."""
        interlock = CanarySafetyInterlock()
        
        override = EmergencyOverrideRequest(
            reason="Critical hotfix for production outage",
            requested_by="oncall@example.com",
            ticket_id="INC-12345",
            acknowledged_risks=["may_cause_data_inconsistency"],
        )
        
        with patch.object(
            interlock, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_3,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            result = interlock.check(
                "promote",
                "rollout-123",
                emergency_override=override,
            )
            
            # 우회로 허용됨
            assert result.allowed is True
            assert result.was_bypassed is True
            assert result.bypass_reason == override.reason
    
    def test_override_requires_valid_reason(self):
        """우회에는 유효한 사유 필수."""
        override = EmergencyOverrideRequest(
            reason="short",  # 너무 짧음 (10자 미만)
            requested_by="oncall@example.com",
        )
        
        assert override.is_valid() is False
    
    def test_override_requires_approval_on_level_3(self):
        """LEVEL_3에서는 사전 승인 필요."""
        override = EmergencyOverrideRequest(
            reason="Critical hotfix for production outage",
            requested_by="oncall@example.com",
            ticket_id="INC-12345",
            # approval_token 없음
        )
        
        assert override.requires_approval_token(EmergencyLevel.LEVEL_3) is True
        assert override.requires_approval_token(EmergencyLevel.LEVEL_2) is False
    
    def test_override_audit_log_created(self):
        """우회 시 감사 로그 생성."""
        auditor = InterlockBypassAuditor()
        
        rollout = MagicMock(id="rollout-123", config_type="circuit_breaker")
        override = EmergencyOverrideRequest(
            reason="Critical hotfix for production outage",
            requested_by="oncall@example.com",
            ticket_id="INC-12345",
        )
        
        with patch.object(auditor, '_persist_entry') as mock_persist:
            entry = auditor.record_bypass(
                rollout=rollout,
                operation="promote",
                override=override,
                emergency_level=EmergencyLevel.LEVEL_3,
                namespace="production",
            )
            
            mock_persist.assert_called_once()
            assert entry.requires_incident_review is True
            assert entry.incident_review_due_hours == 48
```

### 5.5 Regional Interlock 테스트

> **§3.9 리전 격리** 검증

```python
class TestRegionalInterlockPolicy:
    """Regional Interlock 정책 테스트."""
    
    def test_pause_all_by_default(self):
        """기본값: 어느 리전이든 Emergency면 전체 PAUSE."""
        policy = RegionalInterlockPolicy()
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["us-east-1"],
            total_regions=["us-east-1", "eu-west-1", "ap-northeast-1"],
            is_region_isolated_deployment=False,
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL
    
    def test_rollback_affected_only_when_allowed(self):
        """격리 배포 시 영향 리전만 롤백 가능."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=True,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["us-east-1"],
            total_regions=["us-east-1", "eu-west-1"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY
    
    def test_fallback_to_pause_all_on_non_isolated(self):
        """비격리 배포에서는 항상 PAUSE_ALL로 폴백."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=True,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["us-east-1"],
            total_regions=["us-east-1", "eu-west-1"],
            is_region_isolated_deployment=False,  # 비격리 배포
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL
    
    def test_pause_all_when_too_many_affected(self):
        """영향 리전이 너무 많으면 PAUSE_ALL."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=True,
            max_affected_regions_for_isolated_rollback=1,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["us-east-1", "eu-west-1"],  # 2개 영향
            total_regions=["us-east-1", "eu-west-1", "ap-northeast-1"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL
```

### 5.6 State Refresher 테스트

> **§3.11 주기적 동기화** 검증

```python
class TestEmergencyStateRefresher:
    """Emergency State Refresher 테스트."""
    
    def test_detects_level_change_via_polling(self):
        """폴링으로 레벨 변경 감지."""
        mock_interlock = MagicMock()
        mock_service = MagicMock()
        
        refresher = EmergencyStateRefresher(
            config=StateRefresherConfig(refresh_interval_seconds=1),
            safety_interlock=mock_interlock,
            canary_service=mock_service,
        )
        
        # 초기 상태: NORMAL
        refresher._last_known_level["test"] = EmergencyLevel.NORMAL
        
        # 폴링 시 LEVEL_2로 변경됨
        mock_state = ScopedEmergencyState(
            namespace="test",
            scope=EmergencyScope.REGIONAL,
            emergency_level=EmergencyLevel.LEVEL_2,
        )
        mock_interlock._get_emergency_tracker.return_value.get_effective_state.return_value = mock_state
        
        # 갱신 수행
        refresher._do_refresh()
        
        # 인터락 적용 확인
        mock_service.get_active_rollouts.assert_called()
    
    def test_fail_closed_on_refresh_failure(self):
        """갱신 실패 시 Fail-Closed 적용."""
        mock_interlock = MagicMock()
        mock_service = MagicMock()
        mock_service.get_active_rollouts.return_value = [
            MagicMock(id="rollout-1"),
            MagicMock(id="rollout-2"),
        ]
        
        refresher = EmergencyStateRefresher(
            config=StateRefresherConfig(on_refresh_failure_action="fail_closed"),
            safety_interlock=mock_interlock,
            canary_service=mock_service,
        )
        
        # 갱신 실패 시뮬레이션
        mock_interlock._get_emergency_tracker.side_effect = ConnectionError()
        
        refresher._do_refresh()
        
        # 모든 활성 롤아웃 일시 중지 확인
        assert mock_service.pause.call_count == 2
```

### 5.7 Mid-Apply Check 테스트

> **§3.12 적용 중 인터락 재체크** 검증

```python
class TestMidApplyCheck:
    """Mid-Apply Interlock Check 테스트."""
    
    def test_stops_on_interlock_during_apply(self):
        """적용 중 인터락 발동 시 중단."""
        mock_service = MagicMock()
        mock_interlock = MagicMock()
        
        # 첫 번째 클러스터는 OK, 두 번째에서 Emergency 발생
        check_count = 0
        def mock_check(*args, **kwargs):
            nonlocal check_count
            check_count += 1
            if check_count == 1:
                return InterlockResult.allow(EmergencyLevel.NORMAL, "test")
            return InterlockResult.block(
                EmergencyLevel.LEVEL_3, "test", InterlockAction.ROLLBACK, "Emergency!"
            )
        
        mock_interlock.check = mock_check
        
        rollout = MagicMock(
            id="rollout-1",
            config_type="circuit_breaker",
            previous_values={"threshold": 3},
            new_values={"threshold": 5},
        )
        
        result = apply_with_mid_check(
            canary_service=mock_service,
            rollout=rollout,
            target_clusters=["cluster-1", "cluster-2", "cluster-3"],
            safety_interlock=mock_interlock,
        )
        
        # 중단됨
        assert result.should_continue is False
        assert result.interlock_triggered is True
        assert result.applied_clusters == ["cluster-1"]
        assert result.remaining_clusters == ["cluster-2", "cluster-3"]
        assert result.rollback_required is True
```

### 5.8 Rollback Value Resolver 테스트

> **§3.13 다단계 롤백 폴백** 검증

```python
class TestRollbackValueResolver:
    """Rollback Value Resolver 테스트."""
    
    def test_tier_1_previous_values(self):
        """Tier 1: previous_values 사용."""
        resolver = RollbackValueResolver()
        
        rollout = MagicMock(
            id="rollout-1",
            config_type="circuit_breaker",
            previous_values={"threshold": 3},
            created_at=datetime.now(timezone.utc),
        )
        
        resolved = resolver.resolve(rollout)
        
        assert resolved.source == RollbackValueSource.PREVIOUS_VALUES
        assert resolved.values == {"threshold": 3}
        assert resolved.is_fallback is False
    
    def test_tier_2_config_history(self):
        """Tier 2: previous_values 없으면 ConfigHistory 조회."""
        mock_history = MagicMock()
        mock_history.get_version_before.return_value = MagicMock(
            values={"threshold": 4}
        )
        
        resolver = RollbackValueResolver(config_history=mock_history)
        
        rollout = MagicMock(
            id="rollout-1",
            config_type="circuit_breaker",
            previous_values=None,  # 없음
            created_at=datetime.now(timezone.utc),
        )
        
        resolved = resolver.resolve(rollout)
        
        assert resolved.source == RollbackValueSource.CONFIG_HISTORY
        assert resolved.values == {"threshold": 4}
        assert resolved.is_fallback is True
        assert resolved.fallback_reason == "previous_values was empty"
    
    def test_tier_3_default_config(self):
        """Tier 3: 모든 값 없으면 DefaultConfig 사용."""
        mock_history = MagicMock()
        mock_history.get_version_before.return_value = None
        
        resolver = RollbackValueResolver(config_history=mock_history)
        
        rollout = MagicMock(
            id="rollout-1",
            config_type="circuit_breaker",
            previous_values=None,
            created_at=datetime.now(timezone.utc),
        )
        
        resolved = resolver.resolve(rollout)
        
        assert resolved.source == RollbackValueSource.DEFAULT_CONFIG
        assert "failure_threshold" in resolved.values
        assert resolved.is_fallback is True
        assert resolved.warning is not None
    
    def test_all_tiers_exhausted(self):
        """모든 tier 실패 시 빈 값 반환 (위험!)."""
        resolver = RollbackValueResolver(
            config_history=None,
            default_configs={},  # 빈 기본값
        )
        
        rollout = MagicMock(
            id="rollout-1",
            config_type="unknown_config",  # 알 수 없는 타입
            previous_values=None,
            created_at=datetime.now(timezone.utc),
        )
        
        resolved = resolver.resolve(rollout)
        
        assert resolved.source == RollbackValueSource.UNKNOWN
        assert resolved.values == {}
        assert "CRITICAL" in resolved.warning
```

---

## 6. 모니터링

### 6.1 메트릭

```python
from prometheus_client import Counter, Gauge, Histogram

# 기본 인터락 메트릭
INTERLOCK_CHECKS_TOTAL = Counter(
    "selfhealing_canary_interlock_checks_total",
    "Total Safety Interlock checks",
    ["operation", "action", "namespace"],
)

INTERLOCK_ACTIONS_TOTAL = Counter(
    "selfhealing_canary_interlock_actions_total",
    "Total Safety Interlock actions applied",
    ["action", "namespace"],
)

INTERLOCK_BLOCKED_PROMOTIONS = Counter(
    "selfhealing_canary_interlock_blocked_promotions_total",
    "Total promotions blocked by Safety Interlock",
    ["emergency_level", "namespace"],
)

# §3.7 Fail-Closed 메트릭
INTERLOCK_FAIL_CLOSED_TOTAL = Counter(
    "selfhealing_canary_interlock_fail_closed_total",
    "Total Fail-Closed activations due to backend failure",
    ["failure_type", "namespace"],
)

INTERLOCK_BACKEND_LATENCY = Histogram(
    "selfhealing_canary_interlock_backend_latency_seconds",
    "Interlock backend check latency",
    ["operation"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
)

# §3.8 Emergency Override 메트릭
INTERLOCK_BYPASS_TOTAL = Counter(
    "selfhealing_canary_interlock_bypass_total",
    "Total Emergency Override (Break Glass) activations",
    ["emergency_level", "namespace", "requested_by"],
)

INTERLOCK_BYPASS_WITHOUT_TICKET = Counter(
    "selfhealing_canary_interlock_bypass_without_ticket_total",
    "Bypass attempts without ticket ID (compliance risk)",
    ["emergency_level", "namespace"],
)

# §3.9 Regional Interlock 메트릭
REGIONAL_INTERLOCK_BEHAVIOR = Counter(
    "selfhealing_canary_regional_interlock_behavior_total",
    "Regional interlock behavior selections",
    ["behavior", "affected_regions_count"],
)

# §3.11 State Refresher 메트릭
STATE_REFRESHER_CYCLES = Counter(
    "selfhealing_canary_state_refresher_cycles_total",
    "Total state refresher cycles",
    ["result"],  # success, failure
)

STATE_REFRESHER_LEVEL_CHANGES = Counter(
    "selfhealing_canary_state_refresher_level_changes_total",
    "Level changes detected via polling (not event)",
    ["from_level", "to_level", "namespace"],
)

# §3.12 Mid-Apply Check 메트릭
MID_APPLY_INTERLOCK_TRIGGERED = Counter(
    "selfhealing_canary_mid_apply_interlock_triggered_total",
    "Interlocks triggered during apply loop",
    ["namespace"],
)

PARTIAL_ROLLBACK_TOTAL = Counter(
    "selfhealing_canary_partial_rollback_total",
    "Partial rollbacks due to mid-apply interlock",
    ["clusters_rolled_back"],
)

# §3.13 Rollback Value Resolver 메트릭
ROLLBACK_VALUE_SOURCE = Counter(
    "selfhealing_canary_rollback_value_source_total",
    "Rollback value sources used",
    ["source", "config_type"],  # previous_values, config_history, default_config, unknown
)

ROLLBACK_FALLBACK_USED = Counter(
    "selfhealing_canary_rollback_fallback_used_total",
    "Fallback used for rollback value resolution",
    ["tier", "config_type"],  # tier_2, tier_3
)
```

### 6.2 알림 템플릿

#### 기본 인터락 발동 알림

```
🛑 Canary Safety Interlock Triggered

Action: ROLLBACK
Emergency Level: LEVEL_3
Namespace: seoul

Affected Rollouts:
• rollout-abc123 (circuit_breaker) → ROLLED_BACK
• rollout-def456 (rate_limiter) → ROLLED_BACK

Trigger Event: evt-789
Reason: Emergency LEVEL_3 requires immediate rollback

View Details: https://dashboard/canary/interlock/evt-789
```

#### Fail-Closed 발동 알림 (§3.7)

```
⚠️ Canary Interlock FAIL-CLOSED Activated

Backend Status: UNAVAILABLE
Assumed Level: LEVEL_3
Namespace: production

Action Taken: All deployments BLOCKED

Failure Details:
• Type: BACKEND_UNAVAILABLE
• Retry Count: 2/2
• Error: ConnectionError: Redis connection timeout

⚠️ This is a safety measure. Investigate backend health immediately.

View Details: https://dashboard/canary/interlock/fail-closed/evt-123
```

#### Emergency Override (Break Glass) 알림 (§3.8)

```
🚨 DANGEROUS: Interlock BYPASSED 🚨

Bypassed By: oncall@example.com
Emergency Level: LEVEL_3
Namespace: production

Rollout: rollout-abc123 (circuit_breaker)
Operation: promote

Override Details:
• Reason: Critical hotfix for production payment outage
• Ticket: INC-12345
• Acknowledged Risks:
  - may_cause_data_inconsistency
  - may_extend_outage

⚠️ POST-INCIDENT REVIEW REQUIRED within 48 hours

PIR Deadline: 2026-01-23 14:30 UTC

View Audit Log: https://dashboard/canary/audit/bypass/audit-789
```

#### Partial Rollback 알림 (§3.12)

```
⚡ Mid-Apply Interlock: PARTIAL ROLLBACK

Rollout: rollout-abc123 (circuit_breaker)
Namespace: production

Apply Progress (Interrupted):
✅ cluster-1: Applied → Rolled Back
✅ cluster-2: Applied → Rolled Back
❌ cluster-3: Skipped (Interlock triggered)
❌ cluster-4: Skipped

Interlock Reason: Emergency LEVEL_2 detected mid-apply
Action Taken: Rolled back applied clusters, paused remaining

⚠️ Manual intervention may be required to resume.

View Details: https://dashboard/canary/rollout/rollout-abc123
```

### 6.3 대시보드 패널

```yaml
# Grafana Dashboard Configuration
dashboard:
  title: "Canary Safety Interlock Monitoring"
  
  rows:
    - title: "Interlock Overview"
      panels:
        - type: stat
          title: "Fail-Closed Activations (24h)"
          query: sum(increase(selfhealing_canary_interlock_fail_closed_total[24h]))
          thresholds:
            - value: 0
              color: green
            - value: 1
              color: yellow
            - value: 5
              color: red
        
        - type: stat
          title: "Emergency Bypasses (24h)"
          query: sum(increase(selfhealing_canary_interlock_bypass_total[24h]))
          thresholds:
            - value: 0
              color: green
            - value: 1
              color: orange
            - value: 3
              color: red
        
        - type: stat
          title: "Partial Rollbacks (24h)"
          query: sum(increase(selfhealing_canary_partial_rollback_total[24h]))
          
    - title: "Rollback Value Sources"
      panels:
        - type: piechart
          title: "Rollback Value Source Distribution"
          query: sum(selfhealing_canary_rollback_value_source_total) by (source)
          
    - title: "State Refresher Health"
      panels:
        - type: graph
          title: "State Refresher Cycles"
          queries:
            - expr: rate(selfhealing_canary_state_refresher_cycles_total{result="success"}[5m])
              legend: "Success"
            - expr: rate(selfhealing_canary_state_refresher_cycles_total{result="failure"}[5m])
              legend: "Failure"
```

---

## 7. 리뷰 반영 요약

> **구현된 리뷰 항목들**

| 리뷰 항목 | 섹션 | 주요 구현 내용 |
|-----------|------|---------------|
| ① Break Glass (긴급 수정) | §3.8 | `EmergencyOverrideRequest`, `EmergencyOverridePolicy`, 48시간 PIR 필수 |
| ② 리전 격리 롤백 | §3.9 | `RegionalInterlockPolicy`, `RegionalInterlockBehavior` enum (4가지 행동) |
| ③ PAUSED 상태 확장 | §3.6, §3.10 | `pause_reason`, `pause_triggered_by`, `PauseContext`, CausationChain 연동 |
| ④ Fail-Safe 보안 장치 | §3.7 | `FailClosedConfig`, `check_with_fail_safe()`, 재시도 로직 |
| 신뢰성: 상태 동기화 | §3.11 | `EmergencyStateRefresher` (30초 폴링, 지터, Fail-Closed 폴백) |
| Race Condition 방어 | §3.12 | `apply_with_mid_check()`, `_rollback_applied_clusters()` 부분 롤백 |
| 데이터 무결성: 롤백 폴백 | §3.13 | `RollbackValueResolver` (3-tier: previous → history → default) |
| 거버넌스: PIR 강제 | §3.14 | `InterlockBypassAuditEntry.requires_incident_review`, 48시간 마감 |

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |
| 1.1.0 | 2026-01-21 | 리뷰 반영: Break Glass, 리전 격리, PAUSED 확장, Fail-Safe, 상태 동기화, Mid-Apply Check, Rollback Resolver, PIR 거버넌스 추가 | AI Assistant |
