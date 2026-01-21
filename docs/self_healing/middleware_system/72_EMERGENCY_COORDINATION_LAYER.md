# 72. Emergency Coordination Layer (긴급 상황 조율 레이어)

> **Version**: 1.0.0  
> **Created**: 2026-01-21  
> **Status**: Draft  
> **Related**: 70_MULTI_CLUSTER_ARCHITECTURE.md, 71_CANARY_CONFIG_ROLLOUT.md

## 1. 개요

### 1.1 배경

현재 시스템에는 두 개의 독립적인 Emergency 메커니즘이 존재합니다:

| 시스템 | 용도 | 상태 |
|--------|------|------|
| `GracefulDegradationManager` | 트래픽 단계별 제어 (LEVEL_1~3) | 구현됨 |
| `EmergencyModeTracker` | 자동화 STRICT/NORMAL 전환 | 구현됨 |

**문제점**:
1. 두 시스템 간 **연계 없음** - LEVEL_3가 되어도 STRICT 자동 활성화 안 됨
2. Canary Rollout이 Emergency 상태를 **무시**하고 진행
3. Error Budget이 Emergency 상황의 **심각성을 반영하지 않음**
4. 모든 클러스터가 **Global 상태 공유** - 리전 격리 불가

### 1.2 목표

**"지능형 연계 레이어"** 도입으로:
- 각 시스템의 전문성 유지
- 이벤트 기반 행동 통일
- 리전 격리 지원
- 인과관계 추적 가능

### 1.3 관련 문서

| 문서 | 내용 |
|------|------|
| [73_NAMESPACE_AWARE_EMERGENCY.md](73_NAMESPACE_AWARE_EMERGENCY.md) | 리전별 긴급 모드 격리 |
| [74_CANARY_SAFETY_INTERLOCK.md](74_CANARY_SAFETY_INTERLOCK.md) | Canary 자동 제동 장치 |
| [75_CRISIS_BUDGET_MULTIPLIER.md](75_CRISIS_BUDGET_MULTIPLIER.md) | 위기 가중치 버짓팅 |
| [76_CASCADE_EVENT_AUDIT.md](76_CASCADE_EVENT_AUDIT.md) | 연계 이벤트 감사 추적 |
| [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md) | 복구 조율자 |

---

## 2. 아키텍처 개요

### 2.1 현재 상태 (AS-IS)

```
┌─────────────────────────────────────────────────────────────────┐
│                      독립 동작 (연계 없음)                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐    ┌──────────────────┐                  │
│  │ GracefulDegradation │    │ EmergencyModeTracker │              │
│  │ Manager           │    │ (Governance)        │              │
│  │                   │    │                     │              │
│  │ LEVEL_1/2/3       │    │ STRICT/NORMAL       │              │
│  └────────┬──────────┘    └─────────┬───────────┘              │
│           │                         │                          │
│           │   ❌ 연동 없음          │                          │
│           ▼                         ▼                          │
│  ┌──────────────────┐    ┌──────────────────┐                  │
│  │  Traffic Control  │    │  Automation Control │              │
│  │  (Rate Limiting)  │    │  (Self-Healing)     │              │
│  └──────────────────┘    └──────────────────┘                  │
│                                                                 │
│  ┌──────────────────┐    ┌──────────────────┐                  │
│  │ CanaryRollout     │    │ ErrorBudget        │              │
│  │ Service           │    │ Calculator         │              │
│  │                   │    │                    │              │
│  │ ❌ Emergency 무시  │    │ ❌ 가중치 없음     │              │
│  └──────────────────┘    └──────────────────┘                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 목표 상태 (TO-BE)

```
┌─────────────────────────────────────────────────────────────────┐
│                Emergency Coordination Layer                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              EmergencyCoordinator (신규)                 │   │
│  │                                                          │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │   │
│  │  │ EventBroker │  │ PolicyEngine│  │ CascadeAudit│     │   │
│  │  │             │  │             │  │             │     │   │
│  │  │ • Subscribe │  │ • Rules     │  │ • HashChain │     │   │
│  │  │ • Dispatch  │  │ • Actions   │  │ • Causation │     │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘     │   │
│  │                                                          │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           │                                     │
│         ┌─────────────────┼─────────────────┐                  │
│         ▼                 ▼                 ▼                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Emergency    │  │ Governance   │  │ Canary       │         │
│  │ Manager      │  │ Tracker      │  │ Service      │         │
│  │              │  │              │  │              │         │
│  │ LEVEL_1/2/3  │──▶ STRICT/NORMAL│──▶ PAUSE/ROLLBACK│        │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
│         │                                    ▲                  │
│         │          ┌──────────────┐          │                  │
│         └──────────▶ ErrorBudget  │──────────┘                  │
│                    │ (Multiplier) │                             │
│                    └──────────────┘                             │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Namespace-Aware State Backend               │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐    │   │
│  │  │ Global  │  │ Seoul   │  │ Tokyo   │  │ Oregon  │    │   │
│  │  │ State   │  │ State   │  │ State   │  │ State   │    │   │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 핵심 컴포넌트

### 3.1 EmergencyCoordinator

중앙 조율자로서 모든 Emergency 관련 이벤트를 수신하고 정책에 따라 행동을 트리거합니다.

```python
class EmergencyCoordinator:
    """
    Emergency Coordination Layer의 중앙 조율자.
    
    역할:
    - Emergency 이벤트 수신 및 라우팅
    - 정책 기반 연계 액션 실행
    - 인과관계 추적 및 감사 로깅
    
    Reference:
    - docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
    """
    
    def __init__(
        self,
        policy_engine: CoordinationPolicyEngine,
        cascade_auditor: CascadeEventAuditor,
    ):
        self.policy_engine = policy_engine
        self.cascade_auditor = cascade_auditor
        self._event_handlers: Dict[str, List[Callable]] = {}
    
    def on_emergency_level_changed(
        self,
        old_level: EmergencyLevel,
        new_level: EmergencyLevel,
        namespace: str,
        trigger_event_id: str,
    ) -> CoordinationResult:
        """
        Emergency Level 변경 시 연계 액션 실행.
        
        Flow:
        1. 정책 엔진에서 적용할 액션 조회
        2. 각 액션 순차 실행
        3. 모든 결과를 CascadeEvent로 기록
        """
        # 1. 정책 조회
        actions = self.policy_engine.get_actions_for_level_change(
            old_level=old_level,
            new_level=new_level,
            namespace=namespace,
        )
        
        # 2. 연계 액션 실행
        results = []
        causation_chain = [trigger_event_id]
        
        for action in actions:
            result = self._execute_action(action, namespace)
            results.append(result)
            causation_chain.append(result.event_id)
        
        # 3. CascadeEvent 기록
        cascade_event = self.cascade_auditor.record(
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            trigger_details={
                "old_level": old_level.name,
                "new_level": new_level.name,
                "namespace": namespace,
            },
            effects=results,
            causation_chain=causation_chain,
        )
        
        return CoordinationResult(
            success=all(r.success for r in results),
            cascade_event_id=cascade_event.id,
            executed_actions=results,
        )
```

### 3.2 CoordinationPolicyEngine

정책 기반으로 Emergency 레벨 변경 시 어떤 액션을 실행할지 결정합니다.

```python
@dataclass
class CoordinationPolicy:
    """연계 정책 정의."""
    
    trigger_level: EmergencyLevel
    """트리거되는 Emergency 레벨."""
    
    scope: EmergencyScope
    """적용 범위 (REGIONAL or GLOBAL)."""
    
    actions: List[CoordinationAction]
    """실행할 액션 목록."""
    
    priority: int = 0
    """우선순위 (높을수록 먼저 실행)."""


class CoordinationPolicyEngine:
    """
    연계 정책 엔진.
    
    설정 가능한 정책 기반으로 Emergency 상황에서
    어떤 연계 액션을 실행할지 결정합니다.
    """
    
    # 기본 정책 (설정으로 오버라이드 가능)
    DEFAULT_POLICIES = [
        CoordinationPolicy(
            trigger_level=EmergencyLevel.LEVEL_2,
            scope=EmergencyScope.REGIONAL,
            actions=[
                CoordinationAction(
                    type=ActionType.CANARY_PAUSE,
                    delay_seconds=30,  # 30초 유예
                ),
            ],
            priority=10,
        ),
        CoordinationPolicy(
            trigger_level=EmergencyLevel.LEVEL_3,
            scope=EmergencyScope.REGIONAL,
            actions=[
                CoordinationAction(
                    type=ActionType.GOVERNANCE_STRICT,
                    immediate=True,
                ),
                CoordinationAction(
                    type=ActionType.CANARY_ROLLBACK,
                    immediate=True,
                ),
                CoordinationAction(
                    type=ActionType.BUDGET_MULTIPLIER,
                    params={"multiplier": 5.0},
                ),
            ],
            priority=100,
        ),
    ]
```

### 3.3 EmergencyScope

긴급 모드의 적용 범위를 정의합니다.

```python
class EmergencyScope(str, Enum):
    """긴급 모드 적용 범위."""
    
    REGIONAL = "regional"
    """특정 리전/네임스페이스에만 적용."""
    
    GLOBAL = "global"
    """모든 클러스터에 적용."""


@dataclass
class ScopedEmergencyState:
    """네임스페이스별 Emergency 상태."""
    
    namespace: str
    """네임스페이스 (예: 'seoul', 'tokyo')."""
    
    emergency_level: EmergencyLevel
    """현재 Emergency 레벨."""
    
    governance_mode: GovernanceMode
    """현재 Governance 모드."""
    
    activated_at: datetime
    """활성화 시각."""
    
    scope: EmergencyScope
    """적용 범위."""
```

---

## 4. 연계 흐름

### 4.1 LEVEL_3 발생 시 연계 시퀀스

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    LEVEL_3 Emergency Cascade Flow                         │
└──────────────────────────────────────────────────────────────────────────┘

  [1] 장애 감지                [2] 연계 시작              [3] 액션 실행
  ─────────────────────────────────────────────────────────────────────────

  Metrics Collector            EmergencyCoordinator       Target Systems
        │                              │                         │
        │  error_rate > 50%            │                         │
        ├──────────────────────────────▶                         │
        │  LEVEL_3 Detected            │                         │
        │                              │                         │
        │                   ┌──────────┴──────────┐              │
        │                   │ PolicyEngine        │              │
        │                   │ lookup(LEVEL_3)     │              │
        │                   └──────────┬──────────┘              │
        │                              │                         │
        │                   ┌──────────▼──────────┐              │
        │                   │ Actions:            │              │
        │                   │ 1. GOVERNANCE_STRICT│              │
        │                   │ 2. CANARY_ROLLBACK  │              │
        │                   │ 3. BUDGET_MULTIPLIER│              │
        │                   └──────────┬──────────┘              │
        │                              │                         │
        │                              │  [Action 1]             │
        │                              ├─────────────────────────▶
        │                              │  GovernanceTracker      │
        │                              │  .activate_strict()     │
        │                              │                         │
        │                              │  [Action 2]             │
        │                              ├─────────────────────────▶
        │                              │  CanaryService          │
        │                              │  .panic_rollback()      │
        │                              │                         │
        │                              │  [Action 3]             │
        │                              ├─────────────────────────▶
        │                              │  ErrorBudgetService     │
        │                              │  .set_multiplier(5.0)   │
        │                              │                         │
        │                   ┌──────────▼──────────┐              │
        │                   │ CascadeAuditor      │              │
        │                   │ .record(            │              │
        │                   │   trigger=LEVEL_3,  │              │
        │                   │   effects=[...],    │              │
        │                   │   chain=[evt1,evt2] │              │
        │                   │ )                   │              │
        │                   └─────────────────────┘              │
        │                                                        │
```

### 4.2 복구 시 역순 연계

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Recovery Cascade Flow (역순)                           │
└──────────────────────────────────────────────────────────────────────────┘

  [1] 복구 감지                [2] 안전 검증              [3] 역순 복구
  ─────────────────────────────────────────────────────────────────────────

  RecoveryGate                 EmergencyCoordinator       Target Systems
        │                              │                         │
        │  error_rate < 10%            │                         │
        │  for 10 minutes              │                         │
        ├──────────────────────────────▶                         │
        │  LEVEL_1 Recovery OK         │                         │
        │                              │                         │
        │                   ┌──────────┴──────────┐              │
        │                   │ RecoveryCoordinator │              │
        │                   │ (신규 컴포넌트)      │              │
        │                   └──────────┬──────────┘              │
        │                              │                         │
        │                              │  [Step 1] Budget 복구   │
        │                              ├─────────────────────────▶
        │                              │  ErrorBudgetService     │
        │                              │  .reset_multiplier()    │
        │                              │                         │
        │                              │  [Step 2] Health Check  │
        │                              ├─────────────────────────▶
        │                              │  Wait 5 min + verify    │
        │                              │                         │
        │                              │  [Step 3] Canary Resume │
        │                              ├─────────────────────────▶
        │                              │  CanaryService          │
        │                              │  .resume_paused()       │
        │                              │                         │
        │                              │  [Step 4] NORMAL 복구   │
        │                              ├─────────────────────────▶
        │                              │  GovernanceTracker      │
        │                              │  .restore_normal()      │
        │                              │                         │
```

---

## 5. 구현 로드맵

### Phase 1: 기반 구조 (Week 1-2)

| 태스크 | 설명 | 우선순위 |
|--------|------|----------|
| EmergencyScope enum | REGIONAL/GLOBAL 범위 정의 | P0 |
| ScopedEmergencyState | 네임스페이스별 상태 모델 | P0 |
| EmergencyCoordinator 골격 | 기본 구조 및 이벤트 핸들링 | P0 |

### Phase 2: 정책 엔진 (Week 3-4)

| 태스크 | 설명 | 우선순위 |
|--------|------|----------|
| CoordinationPolicy 모델 | 정책 데이터 구조 | P0 |
| CoordinationPolicyEngine | 정책 조회 및 매칭 | P0 |
| DEFAULT_POLICIES | 기본 연계 정책 정의 | P1 |

### Phase 3: 연계 구현 (Week 5-8)

| 태스크 | 설명 | 문서 |
|--------|------|------|
| Namespace-Aware Emergency | 리전별 상태 분리 | [73_NAMESPACE_AWARE_EMERGENCY.md](73_NAMESPACE_AWARE_EMERGENCY.md) |
| Canary Safety Interlock | 자동 제동 장치 | [74_CANARY_SAFETY_INTERLOCK.md](74_CANARY_SAFETY_INTERLOCK.md) |
| Crisis Budget Multiplier | 위기 가중치 | [75_CRISIS_BUDGET_MULTIPLIER.md](75_CRISIS_BUDGET_MULTIPLIER.md) |
| Cascade Event Audit | 연계 감사 추적 | [76_CASCADE_EVENT_AUDIT.md](76_CASCADE_EVENT_AUDIT.md) |

### Phase 4: 복구 조율 (Week 9-10)

| 태스크 | 설명 | 문서 |
|--------|------|------|
| RecoveryCoordinator | 역순 복구 조율 | [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md) |
| Recovery Health Checks | 단계별 검증 | [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md) |
| Recovery Policy | 복구 정책 정의 | [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md) |

---

## 6. 테스트 전략

### 6.1 단위 테스트

```python
class TestEmergencyCoordinator:
    """EmergencyCoordinator 단위 테스트."""
    
    def test_level_3_triggers_strict_mode(self):
        """LEVEL_3 발생 시 STRICT 모드 자동 활성화."""
        coordinator = EmergencyCoordinator(...)
        
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="evt-123",
        )
        
        assert result.success
        assert ActionType.GOVERNANCE_STRICT in [
            a.type for a in result.executed_actions
        ]
    
    def test_regional_isolation(self):
        """서울 LEVEL_3가 도쿄에 영향 없음."""
        coordinator = EmergencyCoordinator(...)
        
        # 서울에 LEVEL_3 발생
        coordinator.on_emergency_level_changed(
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            ...
        )
        
        # 도쿄는 여전히 NORMAL
        tokyo_state = coordinator.get_state("tokyo")
        assert tokyo_state.governance_mode == GovernanceMode.NORMAL
```

### 6.2 통합 테스트

```python
class TestEmergencyCascadeIntegration:
    """Emergency Cascade 통합 테스트."""
    
    def test_full_cascade_flow(self):
        """LEVEL_3 → STRICT → Canary Rollback 전체 흐름."""
        # Given: 활성 Canary 롤아웃 존재
        rollout = canary_service.create_rollout(...)
        canary_service.start_rollout(rollout.id)
        
        # When: LEVEL_3 발생
        emergency_manager.activate_manual(
            level=EmergencyLevel.LEVEL_3,
            reason="Test cascade",
        )
        
        # Then: 연계 액션 모두 실행됨
        assert governance_tracker.get_mode() == "STRICT"
        assert rollout.state == CanaryState.ROLLED_BACK
        assert error_budget.current_multiplier == 5.0
        
        # And: CascadeEvent 기록됨
        events = cascade_auditor.get_recent_events()
        assert len(events) == 1
        assert events[0].trigger_type == "EMERGENCY_LEVEL_CHANGED"
        assert len(events[0].effects) == 3
```

---

## 7. 설정 예시

### 7.1 coordination_policy.yaml

```yaml
# Emergency Coordination Policy Configuration
# Reference: docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md

version: "1.0"

# 기본 설정
defaults:
  scope: regional  # regional or global
  recovery_delay_minutes: 10
  health_check_interval_seconds: 30

# Level별 연계 정책
policies:
  level_2:
    description: "중간 장애 - Canary 일시 중지"
    scope: regional
    actions:
      - type: canary_pause
        delay_seconds: 30
        filter:
          states: ["CANARY", "STARTING"]
      - type: budget_multiplier
        params:
          multiplier: 3.0
    
  level_3:
    description: "심각한 장애 - 전체 연계 대응"
    scope: regional
    actions:
      - type: governance_strict
        immediate: true
        params:
          reason_prefix: "[AUTO-CASCADE]"
      - type: canary_rollback
        immediate: true
        filter:
          states: ["CANARY", "PAUSED", "STARTING"]
      - type: budget_multiplier
        params:
          multiplier: 5.0
      - type: notification
        params:
          channels: ["slack", "pagerduty"]
          severity: critical

# 복구 정책
recovery:
  # LEVEL_3 → NORMAL 복구 순서
  level_3_to_normal:
    steps:
      - action: budget_reset_multiplier
        wait_after_seconds: 0
      - action: health_check
        params:
          duration_minutes: 5
          success_threshold: 0.95
      - action: canary_resume
        wait_after_seconds: 60
      - action: governance_normal
        wait_after_seconds: 300  # 5분 안정화 후

# Crisis Multiplier 설정
crisis_multipliers:
  NORMAL: 1.0
  LEVEL_1: 1.5
  LEVEL_2: 3.0
  LEVEL_3: 5.0  # 기본값, policies에서 오버라이드 가능
```

---

## 8. 모니터링 및 알림

### 8.1 메트릭

```python
# Prometheus 메트릭
COORDINATION_ACTIONS_TOTAL = Counter(
    "selfhealing_coordination_actions_total",
    "Total coordination actions executed",
    ["action_type", "namespace", "success"],
)

CASCADE_EVENTS_TOTAL = Counter(
    "selfhealing_cascade_events_total",
    "Total cascade events recorded",
    ["trigger_type", "namespace"],
)

COORDINATION_LATENCY = Histogram(
    "selfhealing_coordination_latency_seconds",
    "Coordination action execution latency",
    ["action_type"],
)
```

### 8.2 알림 템플릿

```
🚨 Emergency Cascade Triggered

Trigger: LEVEL_3 Detected
Namespace: seoul
Time: 2026-01-21T15:30:00Z

Executed Actions:
✅ GOVERNANCE_STRICT - Automation blocked
✅ CANARY_ROLLBACK - 3 rollouts rolled back
✅ BUDGET_MULTIPLIER - Set to 5.0x

Cascade Event ID: cascade-evt-abc123
Causation Chain: [trigger-001] → [strict-002] → [rollback-003]

View Details: https://dashboard/cascade/cascade-evt-abc123
```

---

## 9. 용어 정리

| 용어 | 설명 |
|------|------|
| **Coordination Layer** | Emergency 이벤트를 수신하고 연계 액션을 조율하는 레이어 |
| **Cascade Event** | 하나의 트리거로 인해 연쇄적으로 발생한 액션들의 묶음 |
| **Causation Chain** | 인과관계를 추적하기 위한 이벤트 ID 체인 |
| **Emergency Scope** | 긴급 모드의 적용 범위 (REGIONAL/GLOBAL) |
| **Safety Interlock** | 위험 상황에서 자동으로 작동하는 안전 장치 |
| **Crisis Multiplier** | 위기 상황에서 에러 버짓 소진율을 증가시키는 계수 |
| **Recovery Coordinator** | 복구 시 역순으로 안전하게 시스템을 정상화하는 컴포넌트 |

---

## 10. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |
