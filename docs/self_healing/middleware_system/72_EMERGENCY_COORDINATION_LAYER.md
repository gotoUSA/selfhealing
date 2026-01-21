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

### Phase 1: 기반 구조 (Week 1-2) ✅ 완료

| 태스크 | 설명 | 우선순위 | 상태 |
|--------|------|----------|------|
| EmergencyScope enum | REGIONAL/GLOBAL 범위 정의 | P0 | ✅ |
| ScopedEmergencyState | 네임스페이스별 상태 모델 | P0 | ✅ |
| EmergencyCoordinator 골격 | 기본 구조 및 이벤트 핸들링 | P0 | ✅ |
| **AntiFlappingGuard** | 히스테리시스 로직 (플래핑 방지) | P0 | ✅ |
| **EMERGENCY_LEVEL_COOLDOWN_SECONDS** | SSOT 상수 정의 (300초) | P0 | ✅ |
| **CoordinationAction.ttl_minutes** | TTL 강제 필드 추가 | P0 | ✅ |
| **Dry-Run Mode** | 섀도우 모드 지원 | P1 | ✅ |

**구현 파일**:
- `selfhealing/services/coordination/enums.py` - EmergencyScope, ActionType, CommandPrecedence, RecoveryStatus
- `selfhealing/services/coordination/models.py` - CoordinationAction, ScopedEmergencyState, OverrideTTLConfig
- `selfhealing/services/coordination/anti_flapping.py` - AntiFlappingGuard, EMERGENCY_LEVEL_COOLDOWN_SECONDS
- `selfhealing/services/coordination/coordinator.py` - EmergencyCoordinator, DryRunAuditLogger

#### 5.1.1 AntiFlappingGuard (히스테리시스)

레벨이 빈번하게 변하며 시스템이 요동치는 '플래핑(Flapping)' 현상을 방지합니다.

**코드 근거**: 기존 `RecoveryGateConfig`([models.py#L16-48](packages/selfhealing-python/src/selfhealing/services/emergency_mode/models.py#L16))의 `stabilization_period_seconds: int = 300` 패턴 확장

```python
# SSOT: Emergency Level 쿨다운 상수 (3가지 보완사항 ③)
EMERGENCY_LEVEL_COOLDOWN_SECONDS: int = 300
"""
Emergency Level 전환 간 최소 대기 시간 (초).

장애 상황에서 지표가 경계선에 걸쳐 있어 레벨이 초 단위로 출렁거릴 때,
시스템이 불필요하게 롤백과 복구를 반복하는 현상을 방지.

Code reference:
    models.py#L24 (RecoveryGateConfig.stabilization_period_seconds = 300)
"""


@dataclass
class AntiFlappingGuard:
    """
    플래핑 방지를 위한 히스테리시스 가드.
    
    Recovery 시 안정성을 충분히 확인한 뒤에 자동화를 재개하는
    신중한 복구 로직을 구현합니다.
    
    Reference:
    - models.py#RecoveryGateConfig.stabilization_period_seconds
    """
    
    # 레벨 전환 간 최소 대기 시간 (초) - SSOT 사용
    level_cooldown_seconds: int = EMERGENCY_LEVEL_COOLDOWN_SECONDS
    """레벨 전환 후 다음 전환까지의 최소 대기 시간."""
    
    # 복구 후 대기 시간 (재활성화 제한)
    cooldown_after_recovery_seconds: int = 600  # 10분
    """복구 완료 후 일정 시간 동안 재활성화 제한."""
    
    # 복구 전 최소 안정 유지 시간
    min_stable_duration_before_recovery_seconds: int = 600  # 10분
    """10분간 안정 상태 유지 후에만 복구 가능."""
    
    # 시간당 최대 전환 횟수
    max_level_transitions_per_hour: int = 3
    """플래핑 감지 임계값: 시간당 3회 초과 시 경고."""
    
    # 플래핑 감지 시 강제 쿨다운
    flapping_lockout_minutes: int = 30
    """플래핑 감지 시 30분간 레벨 변경 잠금."""
    
    def check_transition_allowed(
        self,
        transition_history: List[datetime],
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        레벨 전환 허용 여부 확인.
        
        Returns:
            (is_allowed, reason): 전환 가능 여부와 사유
        """
        now = now or datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        
        recent_transitions = [
            t for t in transition_history if t > one_hour_ago
        ]
        
        if len(recent_transitions) >= self.max_level_transitions_per_hour:
            return (
                False,
                f"Flapping detected: {len(recent_transitions)} transitions "
                f"in last hour (max: {self.max_level_transitions_per_hour}). "
                f"Lockout for {self.flapping_lockout_minutes} minutes."
            )
        
        return (True, "Transition allowed")
```

#### 5.1.2 Dry-Run / Shadow Mode

프로덕션 적용 초기 단계에서 시스템의 '지능'을 안전하게 검증하기 위한 필수 장치입니다.

**코드 근거**: 기존 `dry_run` 패턴 ([urls.py#L301-302](packages/selfhealing-python/src/selfhealing/api/django/urls.py#L301), [builders.py#L874-876](tests/factories/builders.py#L874))

```python
@dataclass
class CoordinationAction:
    """
    연계 액션 정의.
    
    is_dry_run 플래그로 실제 동작 없이 Audit 로그에만 기록하는
    섀도우 모드를 지원합니다.
    
    Code reference:
        circuit_breaker.py#L567 (ttl_minutes 패턴)
    """
    
    type: ActionType
    """액션 유형."""
    
    immediate: bool = False
    """즉시 실행 여부."""
    
    delay_seconds: int = 0
    """지연 시간 (초)."""
    
    params: Dict[str, Any] = field(default_factory=dict)
    """추가 파라미터."""
    
    # 신규: TTL 강제 (3가지 보완사항 ①)
    ttl_minutes: Optional[int] = None
    """
    액션 유효 시간 (분).
    
    오버라이드 액션에 TTL을 강제하여 '좀비 오버라이드' 방지.
    None이면 OverrideTTLConfig.default_override_ttl_minutes 적용.
    
    Code reference:
        circuit_breaker.py#L567 (ttl_minutes: int = 90 패턴)
    """
    
    # 신규: Dry-Run 지원
    is_dry_run: bool = False
    """
    True면 실행하지 않고 감사 로그에만 "수행되었을 액션" 기록.
    
    용도:
    - 프로덕션 적용 초기 검증
    - 정책 변경 전 영향 분석
    - 버그 있는 자동화 레이어의 위험 방지
    """


class DryRunAuditLogger:
    """
    Dry-Run 모드에서 "수행되었을 액션"을 감사 로그에 기록.
    
    Reference:
    - api/django/urls.py#L301-302 (기존 dry-run 엔드포인트)
    """
    
    def log_would_execute(
        self,
        action: CoordinationAction,
        namespace: str,
        context: Dict[str, Any],
    ) -> str:
        """수행되었을 액션을 Audit 로그에 기록."""
        entry = {
            "event_type": "DRY_RUN_ACTION",
            "action_type": action.type.value,
            "namespace": namespace,
            "params": action.params,
            "would_execute": True,
            "actual_execution": False,
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # 기존 audit 시스템 활용
        from selfhealing.services.audit_helpers import log_config_change
        return log_config_change(
            config_type="coordination_dry_run",
            action="WOULD_EXECUTE",
            details=entry,
        )
```

### Phase 2: 정책 엔진 (Week 3-4)

| 태스크 | 설명 | 우선순위 |
|--------|------|----------|
| CoordinationPolicy 모델 | 정책 데이터 구조 | P0 |
| CoordinationPolicyEngine | 정책 조회 및 매칭 | P0 |
| DEFAULT_POLICIES | 기본 연계 정책 정의 | P1 |
| **CriticalPathFallback** | 순환 의존성 방어 (로컬 폴백) | P0 |
| **AtomicLevelTransition** | Lua 스크립트 원자적 상태 변경 (3가지 보완사항 ②) | P0 |
| **DomainAwareCrisisMultiplier** | 도메인 인지형 가중치 | P1 |

#### 5.2.1 CriticalPathFallback (순환 의존성 방어)

CoordinationManager가 의존하는 서비스(Redis, Audit API)가 Emergency 상태로 인해 차단/지연될 경우 교착 상태를 방지합니다.

**네이밍 선택**: `CriticalPathFallback`
- **선택 이유**: 기존 코드베이스에서 `fallback` 패턴이 광범위하게 사용됨
  - [fallback.py#L24-45](packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/fallback.py#L24) - `HashChainFallbackChain`
  - [manager.py#L61](packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/manager.py#L61) - `local_fallback_path`
  - [event_bus_redis.py#L139](packages/selfhealing-python/src/selfhealing/services/event_bus_redis.py#L139) - `fallback_to_local`
- **대안 `CRITICAL_PATH_EXEMPT`**: Whitelist/Exempt 패턴보다 Fallback 패턴이 시스템에 더 일관됨

```python
class CriticalPathFallback:
    """
    연계 레이어 핵심 경로의 로컬 폴백.
    
    Redis/Audit API 장애 시에도 Emergency 상태 변경이 가능하도록
    로컬 폴백 경로를 제공합니다.
    
    Pattern source:
        audit/graceful_degradation/fallback.py#HashChainFallbackChain
    """
    
    def __init__(
        self,
        local_state_path: Path = Path("/tmp/emergency_state.json"),
        local_audit_path: Path = Path("/tmp/emergency_audit.jsonl"),
    ):
        self._local_state_path = local_state_path
        self._local_audit_path = local_audit_path
        self._memory_state: Optional[Dict[str, Any]] = None
    
    def load_state_with_fallback(self) -> Dict[str, Any]:
        """
        상태 로드 (Redis → Local File → Memory 순 폴백).
        
        Reference:
            fallback.py#L84-130 (add_integrity 폴백 체인)
        """
        # 1. Redis Primary 시도
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            data = backend.get("emergency_mode")
            if data:
                return data
        except Exception as e:
            logger.warning(f"[CriticalPathFallback] Redis failed: {e}")
        
        # 2. Local File 시도
        try:
            if self._local_state_path.exists():
                with open(self._local_state_path) as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"[CriticalPathFallback] Local file failed: {e}")
        
        # 3. Memory 폴백
        if self._memory_state:
            return self._memory_state
        
        # 4. 기본 상태 반환 (최후 수단)
        return {"level": "NORMAL", "is_active": False}
    
    def save_state_with_fallback(self, state: Dict[str, Any]) -> str:
        """
        상태 저장 (Redis + Local File 동시 저장).
        
        Returns:
            저장된 tier ('redis', 'local', 'memory')
        """
        tier = "memory"
        self._memory_state = state  # 항상 메모리에 유지
        
        # Local File 저장 (항상 시도)
        try:
            with open(self._local_state_path, "w") as f:
                json.dump(state, f)
            tier = "local"
        except Exception as e:
            logger.warning(f"[CriticalPathFallback] Local save failed: {e}")
        
        # Redis 저장 시도
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            backend.set("emergency_mode", state)
            tier = "redis"
        except Exception as e:
            logger.warning(f"[CriticalPathFallback] Redis save failed: {e}")
        
        return tier
```

#### 5.2.2 AtomicLevelTransition (Lua 스크립트 원자적 상태 변경)

Redis 상태 변경 시 `[현재 레벨 확인 + 모드 변경 + 인과관계 ID 기록]`을 Lua 스크립트로 단일 트랜잭션 처리하여 Race Condition을 방지합니다.

**코드 근거**: 기존 Lua 스크립트 패턴 ([locking.py#L177-187](packages/selfhealing-python/src/selfhealing/services/canary/locking.py#L177))

```python
class AtomicLevelTransition:
    """
    원자적 Emergency Level 전환.
    
    분산 환경에서 여러 노드가 동시에 상태를 바꿀 때 발생할 수 있는
    데이터 경합(Race Condition)을 Lua 스크립트로 원천 차단합니다.
    
    Code reference:
        locking.py#L177-187 (Lua 스크립트 원자적 처리)
        locking.py#L279-289 (TTL 연장 Lua 스크립트)
    """
    
    # Lua 스크립트: 레벨 확인 + 모드 변경 + 인과관계 ID 기록 (원자적)
    ATOMIC_TRANSITION_SCRIPT = """
    -- KEYS[1]: emergency state key (예: selfhealing:emergency:seoul)
    -- ARGV[1]: expected current level (검증용)
    -- ARGV[2]: new level
    -- ARGV[3]: new governance mode
    -- ARGV[4]: causation event id
    -- ARGV[5]: updated_at timestamp
    
    local current_level = redis.call("HGET", KEYS[1], "level")
    
    -- Optimistic Lock: 현재 레벨이 예상과 다르면 실패
    if current_level and current_level ~= ARGV[1] then
        return {0, "level_mismatch", current_level}
    end
    
    -- 원자적 상태 업데이트
    redis.call("HMSET", KEYS[1], 
        "level", ARGV[2],
        "governance_mode", ARGV[3],
        "causation_id", ARGV[4],
        "updated_at", ARGV[5]
    )
    
    return {1, "success", ARGV[2]}
    """
    
    def __init__(self, redis_client):
        self._redis = redis_client
        self._script_sha: Optional[str] = None
    
    def transition(
        self,
        namespace: str,
        expected_level: str,
        new_level: str,
        new_mode: str,
        causation_id: str,
    ) -> Tuple[bool, str, str]:
        """
        원자적 레벨 전환 실행.
        
        Args:
            namespace: 대상 네임스페이스
            expected_level: 예상 현재 레벨 (Optimistic Lock)
            new_level: 새 레벨
            new_mode: 새 Governance 모드
            causation_id: 인과관계 이벤트 ID
        
        Returns:
            (success, message, resulting_level)
        """
        from selfhealing.settings.namespace import get_key_prefix
        
        key = f"{get_key_prefix()}:emergency:{namespace}"
        now = datetime.now(timezone.utc).isoformat()
        
        try:
            result = self._redis.eval(
                self.ATOMIC_TRANSITION_SCRIPT,
                1,  # KEYS count
                key,
                expected_level,
                new_level,
                new_mode,
                causation_id,
                now,
            )
            
            success = result[0] == 1
            message = result[1]
            level = result[2]
            
            if success:
                logger.info(
                    f"[AtomicTransition] Success: {namespace} -> {new_level}"
                )
            else:
                logger.warning(
                    f"[AtomicTransition] Failed: {message}, "
                    f"current={level}, expected={expected_level}"
                )
            
            return (success, message, level)
            
        except Exception as e:
            logger.error(f"[AtomicTransition] Error: {e}")
            return (False, str(e), expected_level)
```

#### 5.2.3 DomainAwareCrisisMultiplier (도메인 인지형 가중치)

장애가 발생한 도메인과 연관된 에러에 대해서만 높은 가중치를 주고, 관련 없는 도메인의 에러는 일반 가중치를 유지합니다.

**네이밍 선택**: `DomainAwareCrisisMultiplier`
- **선택 이유**: 기존 코드베이스의 도메인 가중치 패턴과 일관성 유지
  - [shadow_calculator.py#L222](packages/selfhealing-python/src/selfhealing/services/error_budget/reconciliation/shadow_calculator.py#L222) - `domain_multiplier = self._get_domain_weight(domain)`
  - [30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md#L357](docs/self_healing/middleware_system/30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md#L357) - 동일 패턴
- **대안 `ContextualBudgetMultiplier`**: 도메인 개념이 이미 코드에 정착되어 있어 Domain-Aware가 더 직관적

```python
@dataclass
class DomainAwareCrisisMultiplier:
    """
    도메인 인지형 위기 가중치.
    
    장애 도메인과 에러 도메인이 일치할 때만 높은 가중치를 적용하여,
    관련 없는 도메인의 에러가 불필요하게 증폭되지 않도록 합니다.
    
    Reference:
        shadow_calculator.py#L222 (_get_domain_weight 패턴)
    """
    
    # 기본 위기 가중치
    base_crisis_multiplier: float = 5.0
    
    # 도메인 인지 기능 활성화 여부
    domain_aware_enabled: bool = True
    
    # 도메인별 민감도 가중치
    domain_sensitivity: Dict[str, float] = field(default_factory=lambda: {
        "payment": 10.0,      # 결제 도메인: 최고 민감도
        "order": 5.0,         # 주문 도메인: 높은 민감도
        "inventory": 3.0,     # 재고 도메인: 중간 민감도
        "notification": 1.5,  # 알림 도메인: 낮은 민감도
        "analytics": 1.0,     # 분석 도메인: 기본 민감도
    })
    
    def get_multiplier(
        self,
        crisis_domain: str,
        error_domain: str,
        crisis_level: EmergencyLevel,
    ) -> float:
        """
        도메인 기반 가중치 계산.
        
        Args:
            crisis_domain: 현재 위기가 발생한 도메인
            error_domain: 에러가 발생한 도메인
            crisis_level: 현재 Emergency 레벨
        
        Returns:
            적용할 가중치 (1.0 ~ 10.0)
        """
        if not self.domain_aware_enabled:
            # 도메인 인지 비활성화 시 레벨 기반 일괄 적용
            return self._get_level_multiplier(crisis_level)
        
        # 동일 도메인: 전체 가중치 적용
        if crisis_domain == error_domain:
            domain_weight = self.domain_sensitivity.get(
                error_domain, 1.0
            )
            return min(
                self._get_level_multiplier(crisis_level) * domain_weight,
                10.0,  # MAX_CRISIS_MULTIPLIER cap
            )
        
        # 다른 도메인: 기본 가중치 유지
        return 1.0
    
    def _get_level_multiplier(self, level: EmergencyLevel) -> float:
        """레벨 기반 기본 가중치."""
        return {
            EmergencyLevel.NORMAL: 1.0,
            EmergencyLevel.LEVEL_1: 1.5,
            EmergencyLevel.LEVEL_2: 3.0,
            EmergencyLevel.LEVEL_3: self.base_crisis_multiplier,
        }.get(level, 1.0)
```

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
| **RecoveryAccountability** | 복구 책임 추적성 | 본 문서 §5.4.1 |
| **OptimisticLocalAction** | 낙관적 선조치 패턴 | 본 문서 §5.4.2 |
| **OverrideTTLEnforcement** | 오버라이드 TTL 강제 | 본 문서 §5.4.3 |

#### 5.4.1 RecoveryAccountability (복구 책임 추적성)

대규모 장애 후 자동 복구 시 "왜 사람이 확인하지 않았는가"에 대한 책임 추적성을 보장합니다.

**코드 근거**: 기존 `acknowledge_warning()` 패턴 ([governance.py#L404-420](packages/selfhealing-python/src/selfhealing/services/governance.py#L404))

```python
class RecoveryStatus(str, Enum):
    """복구 상태 (확장)."""
    
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    HEALTH_CHECK = "health_check"
    
    # 신규: 수동 승인 대기 상태
    READY_TO_RESTORE = "ready_to_restore"
    """
    8시간 자동 만료 조건 충족 + requires_manual_acknowledgement=True일 때,
    즉시 NORMAL 복구 대신 이 상태로 전환.
    
    사람이 마지막 Acknowledge 버튼을 누른 시점을 Audit 로그에 박제하여
    "복구 결정의 최종 책임은 사람에게 있었다"는 점을 명확히 함.
    """
    
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class RecoveryAccountabilityConfig:
    """
    복구 책임 추적 설정.
    
    Reference:
        governance.py#L404 (acknowledge_warning 패턴)
    """
    
    # 수동 승인 필수 여부
    requires_manual_acknowledgement: bool = False
    """
    True일 때:
    - 8시간 경과해도 자동 NORMAL 복구하지 않음
    - READY_TO_RESTORE 상태로 전환
    - Admin의 acknowledge() 호출 시에만 복구 완료
    """
    
    # READY_TO_RESTORE 상태 최대 유지 시간
    ready_to_restore_timeout_hours: int = 24
    """24시간 내 승인 없으면 알림 에스컬레이션."""
    
    # 승인 가능 역할
    acknowledgement_required_roles: List[str] = field(
        default_factory=lambda: ["admin", "sre_lead"]
    )


class RecoveryCoordinatorWithAccountability:
    """
    책임 추적성이 포함된 복구 조율자.
    """
    
    def check_auto_restore_eligibility(self) -> Tuple[bool, str, RecoveryStatus]:
        """
        자동 복구 가능 여부 확인.
        
        Returns:
            (can_auto_restore, reason, next_status)
        """
        expiry_status = self._tracker.check_expiry_status()
        
        if not expiry_status["should_auto_restore"]:
            return (False, "Not yet eligible", RecoveryStatus.IN_PROGRESS)
        
        if self._config.requires_manual_acknowledgement:
            return (
                False,
                "Manual acknowledgement required",
                RecoveryStatus.READY_TO_RESTORE,  # 대기 상태로 전환
            )
        
        return (True, "Auto restore allowed", RecoveryStatus.COMPLETED)
    
    def acknowledge_recovery(
        self,
        acknowledged_by: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        수동 복구 승인 (READY_TO_RESTORE → COMPLETED).
        
        Audit 로그에 승인자 정보를 박제합니다.
        """
        if self._current_status != RecoveryStatus.READY_TO_RESTORE:
            raise ValueError(
                f"Cannot acknowledge: current status is {self._current_status}"
            )
        
        # Audit 로그에 "최종 복구 결정" 기록
        self._audit_recovery_decision(
            decision="MANUAL_ACKNOWLEDGE",
            acknowledged_by=acknowledged_by,
            reason=reason,
            auto_restore_was_eligible=True,
        )
        
        # 실제 복구 수행
        return self._execute_recovery_steps()
```

#### 5.4.2 OptimisticLocalAction (낙관적 선조치 패턴)

LEVEL_3 발생 시 3초 이내 연계 완료를 위해, 중앙 DB 상태 업데이트를 기다리지 않고 로컬에서 즉시 행동합니다.

**코드 근거**: 
- [73_NAMESPACE_AWARE_EMERGENCY.md#L216-220](docs/self_healing/middleware_system/73_NAMESPACE_AWARE_EMERGENCY.md#L216) - `_local_cache` 패턴
- [versioning.py#L74](packages/selfhealing-python/src/selfhealing/services/canary/versioning.py#L74) - Optimistic Locking 패턴

```python
class OptimisticLocalActionExecutor:
    """
    낙관적 선조치 실행기.
    
    이벤트 수신 시 로컬 캐시를 즉시 업데이트하고,
    사후에 중앙 상태와 동기화합니다.
    
    Pattern source:
        canary/versioning.py#L74 (Optimistic Locking)
        73_NAMESPACE_AWARE_EMERGENCY.md#L216 (_local_cache)
    
    가치: 
        네트워크 지연에 상관없이 각 리전이 1초 내에
        Safety-Shut 할 수 있는 속도 보장.
    """
    
    def __init__(self, redis_client: Optional[Any] = None):
        self._redis = redis_client
        self._local_cache: Dict[str, Any] = {}
        self._pending_sync: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
    
    def execute_with_optimistic_action(
        self,
        action: CoordinationAction,
        namespace: str,
    ) -> ActionResult:
        """
        낙관적 선조치 실행.
        
        Flow:
        1. 로컬 캐시 즉시 업데이트 (< 10ms)
        2. 액션 실행 (동기)
        3. 중앙 상태 비동기 동기화 (백그라운드)
        """
        # Step 1: 로컬 캐시 즉시 업데이트
        with self._lock:
            self._local_cache[f"{namespace}:governance_mode"] = "STRICT"
            self._local_cache[f"{namespace}:updated_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
        
        # Step 2: 액션 실행
        result = self._execute_action_locally(action, namespace)
        
        # Step 3: 비동기 동기화 예약
        self._schedule_central_sync(action, namespace, result)
        
        return result
    
    def _schedule_central_sync(
        self,
        action: CoordinationAction,
        namespace: str,
        result: ActionResult,
    ) -> None:
        """중앙 상태 비동기 동기화 예약."""
        sync_entry = {
            "action": action,
            "namespace": namespace,
            "result": result,
            "local_timestamp": datetime.now(timezone.utc).isoformat(),
            "sync_status": "pending",
        }
        
        with self._lock:
            self._pending_sync.append(sync_entry)
        
        # 백그라운드 태스크로 동기화 (Celery 또는 threading)
        try:
            from selfhealing.tasks.coordination import sync_to_central
            sync_to_central.delay(sync_entry)
        except Exception as e:
            logger.warning(f"[OptimisticAction] Async sync failed: {e}")
            # 폴백: 동기 재시도는 다음 주기에
```

#### 5.4.3 OverrideTTLEnforcement (오버라이드 TTL 강제)

Admin이 force_normal=True 설정 후 깜빡 잊고 퇴근했을 때, 시스템이 영원히 장애를 방치하는 '좀비 오버라이드'를 방지합니다.

**코드 근거**: 기존 `manual_override_expires_at` 패턴 ([circuit_breaker.py#L567-598](packages/selfhealing-python/src/selfhealing/adapters/redis/circuit_breaker.py#L567))

```python
@dataclass
class OverrideTTLConfig:
    """
    오버라이드 TTL 강제 설정.
    
    Reference:
        circuit_breaker.py#L567 (manual_override TTL 패턴)
        test_time_based_behaviors.py#L478 (TTL 테스트)
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
    permanent_override_roles: List[str] = field(
        default_factory=lambda: ["super_admin"]
    )


class CommandPrecedence(IntEnum):
    """
    명령 우선순위.
    
    숫자가 높을수록 우선.
    """
    
    SYSTEM_AUTO = 1
    """시스템 자동 감지 (기본)."""
    
    OPERATOR_COMMAND = 2
    """운영자 수동 명령."""
    
    ADMIN_OVERRIDE = 3
    """Admin 강제 오버라이드 (TTL 필수)."""
    
    MAINTENANCE_MODE = 4
    """
    유지보수 모드 (신규).
    
    운영자가 긴급 점검을 위해 의도적으로 에러를 발생시키는
    상황에서 시스템 자동 대응을 일시 중지.
    
    반드시 TTL이 설정되어야 하며, 만료 시 자동 해제.
    """
    
    KILL_SWITCH = 5
    """최우선: Kill Switch (모든 자동화 중지)."""


def resolve_precedence(
    manual_command: Dict[str, Any],
    system_detected: Dict[str, Any],
    ttl_config: OverrideTTLConfig,
) -> Dict[str, Any]:
    """
    운영자 명령 vs 시스템 감지 우선순위 해결.
    
    기본 원칙: 안전 우선 (시스템 감지 우선)
    예외: Admin + force_override + TTL 설정 시 수동 명령 우선
    
    Args:
        manual_command: 운영자 수동 명령
        system_detected: 시스템 자동 감지 결과
        ttl_config: TTL 설정
    
    Returns:
        최종 적용할 명령
    """
    # Kill Switch는 항상 최우선
    if system_detected.get("is_kill_switch"):
        return system_detected
    
    # 유지보수 모드 확인
    if manual_command.get("maintenance_mode"):
        ttl = manual_command.get("ttl_minutes")
        if not ttl:
            # TTL 없는 유지보수 모드 거부
            raise ValueError(
                "MAINTENANCE_MODE requires TTL to prevent zombie override"
            )
        if ttl > ttl_config.max_override_ttl_minutes:
            ttl = ttl_config.max_override_ttl_minutes
            logger.warning(
                f"[Precedence] TTL capped to {ttl} minutes"
            )
        return {**manual_command, "ttl_minutes": ttl}
    
    # Admin 강제 오버라이드
    if manual_command.get("force_override") and manual_command.get("role") == "admin":
        ttl = manual_command.get("ttl_minutes", ttl_config.default_override_ttl_minutes)
        if ttl > ttl_config.max_override_ttl_minutes:
            ttl = ttl_config.max_override_ttl_minutes
        return {**manual_command, "ttl_minutes": ttl, "ttl_enforced": True}
    
    # 기본: 시스템 감지 우선 (안전 우선 원칙)
    return system_detected
```

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
| **AntiFlappingGuard** | 레벨 변경이 빈번하게 발생하는 플래핑 현상을 방지하는 히스테리시스 가드 |
| **Dry-Run Mode** | 실제 동작 없이 "수행되었을 액션"을 Audit 로그에만 기록하는 검증 모드 |
| **CriticalPathFallback** | Redis/Audit 장애 시에도 Emergency 상태 변경이 가능한 로컬 폴백 경로 |
| **DomainAwareCrisisMultiplier** | 장애 도메인과 에러 도메인의 연관성을 고려한 가중치 계산 |
| **READY_TO_RESTORE** | 자동 복구 조건 충족 후 수동 승인 대기 상태 (책임 추적성 보장) |
| **OptimisticLocalAction** | 중앙 동기화 전 로컬에서 즉시 행동하는 낙관적 선조치 패턴 |
| **OverrideTTLEnforcement** | 수동 오버라이드에 TTL을 강제하여 좀비 상태 방지 |
| **MAINTENANCE_MODE** | 의도적 장애 유발 시 시스템 자동 대응을 일시 중지하는 유지보수 모드 |

---

## 10. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |
| 1.1.0 | 2026-01-21 | Phase 1: AntiFlappingGuard(히스테리시스), Dry-Run Mode 추가 | AI Assistant |
| 1.2.0 | 2026-01-21 | Phase 2: CriticalPathFallback(순환 의존성 방어), DomainAwareCrisisMultiplier(도메인 인지형 가중치) 추가 | AI Assistant |
| 1.3.0 | 2026-01-21 | Phase 4: RecoveryAccountability(READY_TO_RESTORE), OptimisticLocalAction, OverrideTTLEnforcement 추가 | AI Assistant |
| 1.4.0 | 2026-01-21 | 3가지 보완사항 반영 (①TTL 강제, ②Lua 원자성, ③COOLDOWN SSOT) 및 Phase 1 구현 완료 | AI Assistant |
