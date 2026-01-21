# 74. Canary Safety Interlock (Canary 자동 제동 장치)

> **Version**: 1.0.0  
> **Created**: 2026-01-21  
> **Status**: Draft  
> **Parent**: [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md)

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
    
    Reference:
    - docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
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
    ):
        """
        CanarySafetyInterlock 초기화.
        
        Args:
            policy: Emergency Level별 액션 정책 (None이면 기본값 사용)
        """
        self.policy = policy or self.DEFAULT_POLICY
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
    ) -> InterlockResult:
        """
        Safety Interlock 체크.
        
        Args:
            operation: 수행할 작업 (start, promote, resume)
            rollout_id: 롤아웃 ID (있는 경우)
            namespace: 대상 네임스페이스 (None이면 현재 인스턴스)
        
        Returns:
            InterlockResult: 체크 결과
        """
        tracker = self._get_emergency_tracker()
        state = tracker.get_effective_state(namespace=namespace)
        
        level = state.emergency_level
        ns = state.namespace
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

---

## 4. 설정

### 4.1 canary_interlock_policy.yaml

```yaml
# Canary Safety Interlock Policy Configuration
# Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md

version: "1.0"

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

---

## 6. 모니터링

### 6.1 메트릭

```python
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
```

### 6.2 알림 템플릿

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

---

## 7. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |
