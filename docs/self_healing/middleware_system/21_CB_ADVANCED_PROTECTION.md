# 21. Circuit Breaker 고급 보호 시스템

> **Version**: 1.0.0  
> **Last Updated**: 2026-01-05  
> **Status**: Implementation Ready

## 1. 개요

Circuit Breaker의 자동화된 OPEN/CLOSE 결정을 안전하게 만들기 위한 고급 보호 메커니즘을 정의합니다.

### 1.1 설계 원칙

| 원칙 | 설명 |
|------|------|
| **도메인 프리** | 핵심/비핵심 판단은 SDK가 아닌 사용자가 설정 |
| **보수적 자동화** | 위기 상황일수록 더 느슨한 임계값 적용 |
| **Freeze over Action** | LOCKDOWN 시 현 상태 동결 |
| **Fail-Fast** | Delayed 차단 금지, Immediate가 기본 |

### 1.2 CB vs DLQ 안전장치 철학 차이

| 측면 | DLQ | Circuit Breaker |
|------|-----|-----------------|
| **목적** | 복구 (재시도) | 보호 (차단) |
| **잘못된 실행** | 재시도 실패 → 큐에 남음 | 잘못된 OPEN → 정상 트래픽 차단 |
| **안전장치 방향** | "실행 전 철저히 검증" | "차단 전 연쇄 영향 분석" |
| **Governance** | 실행 Gate | OPEN Gate |

---

## 2. 서비스 Criticality 설정

### 2.1 데이터 모델

```python
@dataclass
class ServiceConfig:
    """서비스 설정 - 사용자가 criticality를 직접 지정"""
    
    service_id: str
    
    # Criticality 레벨 (사용자 지정 필수)
    criticality: str  # "critical" | "high" | "medium" | "low"
    
    # Load Shedding 우선순위 (높을수록 먼저 차단, 0=절대 차단 안 함)
    shed_priority: int = 0
    
    # 최소 보장 트래픽 (0~100%)
    min_traffic_percentage: float = 0.0
    
    # 서비스별 Recovery 전략 오버라이드
    recovery_strategy: Optional[RecoveryStrategy] = None
    
    # 서비스별 CB 설정 오버라이드
    failure_threshold: Optional[int] = None
    window_seconds: Optional[int] = None
```

### 2.2 Criticality 가이드라인 (사용자 참고용)

| Level | 설명 | 예시 (도메인별 다름) |
|-------|------|---------------------|
| `critical` | 서비스 핵심 기능, 절대 차단 불가 | 결제, 인증, 주문 |
| `high` | 중요하지만 일시 차단 가능 | 재고, 배송 조회 |
| `medium` | 부가 기능, 차단 가능 | 알림, 이메일 |
| `low` | 비핵심, 우선 차단 대상 | 추천, 리뷰, 분석 |

> ⚠️ **주의**: 위 예시는 참고용입니다. 실제 criticality는 각 도메인의 비즈니스 요구사항에 따라 사용자가 결정해야 합니다.

---

## 3. Load Shedding (부분적 차단)

### 3.1 개념

핵심 서비스에 장애 조짐이 보이면, **비핵심 서비스 트래픽을 먼저 제한**하여 핵심 서비스에 리소스를 집중시킵니다.

```
┌─────────────────────────────────────────────────────────────┐
│                    정상 상태                                 │
│  payment-api: 100%  order-api: 100%  review-api: 100%       │
└─────────────────────────────────────────────────────────────┘
                          ↓ 
                  payment-api 에러율 35%
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                    Level 1 Shedding                          │
│  payment-api: 100%  order-api: 100%  review-api: 50%        │
│                                      ↑ 비핵심 먼저 제한      │
└─────────────────────────────────────────────────────────────┘
                          ↓
                  payment-api 에러율 55%
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                    Level 2 Shedding                          │
│  payment-api: 100%  order-api: 100%  review-api: 20%        │
└─────────────────────────────────────────────────────────────┘
                          ↓
                  payment-api 에러율 75%
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                    Level 3 Shedding (Max)                    │
│  payment-api: 100%  order-api: 100%  review-api: 0%         │
│                                      ↑ 완전 차단             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 데이터 모델

```python
@dataclass
class LoadSheddingPolicy:
    """Load Shedding 정책"""
    
    enabled: bool = True
    
    # 트리거 조건: critical 서비스의 에러율이 이 값 초과 시 shedding 시작
    trigger_threshold: float = 30.0
    
    # 단계별 차단 정책 (기본 3단계, 확장 가능)
    levels: List[SheddingLevel] = field(default_factory=lambda: [
        SheddingLevel(
            error_rate=30.0,
            shed_criticality=["low"],
            traffic_limit=50.0,
            description="Level 1: low criticality 50% 제한"
        ),
        SheddingLevel(
            error_rate=50.0,
            shed_criticality=["low", "medium"],
            traffic_limit=20.0,
            description="Level 2: low+medium 80% 제한"
        ),
        SheddingLevel(
            error_rate=70.0,
            shed_criticality=["low", "medium"],
            traffic_limit=0.0,
            description="Level 3: low+medium 완전 차단"
        ),
    ])


@dataclass
class SheddingLevel:
    """개별 Shedding 단계"""
    
    error_rate: float              # critical 서비스 에러율 임계값
    shed_criticality: List[str]    # 차단 대상 criticality 목록
    traffic_limit: float           # 허용 트래픽 % (0=완전 차단, 100=제한 없음)
    description: str = ""          # 단계 설명
```

### 3.3 설계 결정

| 항목 | 결정 | 이유 |
|------|------|------|
| **기본 단계 수** | 3단계 | 운영자 인지 부하 최소화, Flapping 방지 |
| **확장 가능성** | O | `List[SheddingLevel]`로 사용자 추가 가능 |
| **critical 차단** | 절대 불가 | `shed_criticality`에 "critical" 포함 금지 |

### 3.4 Shedding 알고리즘

```python
def evaluate_shedding(self, service_id: str) -> float:
    """
    서비스에 대한 현재 허용 트래픽 비율 계산
    
    Returns:
        float: 허용 트래픽 비율 (0.0 ~ 100.0)
    """
    if not self.policy.enabled:
        return 100.0
    
    service_config = self.get_service_config(service_id)
    
    # critical 서비스는 항상 100%
    if service_config.criticality == "critical":
        return 100.0
    
    # critical 서비스들의 평균 에러율 계산
    critical_error_rate = self._get_critical_services_error_rate()
    
    # 현재 적용할 shedding level 찾기
    applicable_level = None
    for level in sorted(self.policy.levels, key=lambda l: l.error_rate, reverse=True):
        if critical_error_rate >= level.error_rate:
            if service_config.criticality in level.shed_criticality:
                applicable_level = level
                break
    
    if applicable_level is None:
        return 100.0
    
    # 최소 보장 트래픽 적용
    return max(
        applicable_level.traffic_limit,
        service_config.min_traffic_percentage
    )
```

---

## 4. Canary Recovery (단계적 복구)

### 4.1 개념

HALF_OPEN 상태에서 즉시 100% 트래픽을 보내는 대신, **점진적으로 트래픽을 늘려** Thundering Herd를 방지합니다.

```
OPEN ──→ HALF_OPEN ──→ Canary Stage 1 (10%) ──→ Stage 2 (30%) ──→ Stage 3 (60%) ──→ CLOSED (100%)
                              │                      │                  │
                              │                      │                  │
                              └──────── 실패 시 OPEN으로 복귀 ───────────┘
```

### 4.2 데이터 모델

```python
@dataclass
class RecoveryStrategy:
    """HALF_OPEN → CLOSED 복구 전략"""
    
    # 전략 타입
    type: str = "canary"  # "immediate" | "canary"
    
    # Canary 단계 설정 (기본 4단계)
    canary_stages: List[CanaryStage] = field(default_factory=lambda: [
        CanaryStage(
            traffic_percent=10.0,
            duration_seconds=5,
            required_success_rate=95.0,
            description="Stage 1: 10% 트래픽으로 5초간 관찰"
        ),
        CanaryStage(
            traffic_percent=30.0,
            duration_seconds=5,
            required_success_rate=95.0,
            description="Stage 2: 30% 트래픽으로 5초간 관찰"
        ),
        CanaryStage(
            traffic_percent=60.0,
            duration_seconds=5,
            required_success_rate=90.0,
            description="Stage 3: 60% 트래픽으로 5초간 관찰"
        ),
        CanaryStage(
            traffic_percent=100.0,
            duration_seconds=0,
            required_success_rate=90.0,
            description="Stage 4: 완전 복구"
        ),
    ])
    
    # 단계 실패 시 동작
    on_stage_failure: str = "restart"  # "restart" | "abort"
    
    # 결제 등 핵심 서비스용 엄격 모드
    strict_mode: bool = False  # True면 모든 단계 100% 성공률 요구


@dataclass
class CanaryStage:
    """개별 Canary 단계"""
    
    traffic_percent: float        # 허용 트래픽 비율 (0~100)
    duration_seconds: int         # 이 단계 유지 시간 (0=즉시 다음 단계)
    required_success_rate: float  # 다음 단계로 가려면 필요한 성공률
    description: str = ""         # 단계 설명
```

### 4.3 설계 결정

| 항목 | 결정 | 이유 |
|------|------|------|
| **기본 단계 수** | 4단계 (10→30→60→100) | Thundering Herd 방지, 안정적 복구 |
| **10→50 대신 10→30→60** | 중간 완충 지대 | 트래픽 5배 급증 방지 |
| **실패 시 동작** | restart (처음부터) | 불안정한 서비스에 점진적 재시도 |
| **strict_mode** | 결제 등 핵심 서비스용 | 100% 성공률 보장 |

### 4.4 Canary 상태 머신

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Canary Recovery State Machine                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────┐     cooldown      ┌────────────┐                          │
│  │   OPEN   │ ─────경과────────▶│ HALF_OPEN  │                          │
│  └──────────┘                   └─────┬──────┘                          │
│       ▲                               │                                  │
│       │                               ▼                                  │
│       │                        ┌────────────┐                           │
│       │                        │  CANARY_1  │ 10% 트래픽                │
│       │                        │  (10%, 5s) │                           │
│       │                        └─────┬──────┘                           │
│       │                              │ 성공률 ≥ 95%                      │
│       │ 실패                         ▼                                   │
│       │                        ┌────────────┐                           │
│       │◀───────────────────────│  CANARY_2  │ 30% 트래픽                │
│       │    (on_failure:        │  (30%, 5s) │                           │
│       │     restart 시         └─────┬──────┘                           │
│       │     CANARY_1로)              │ 성공률 ≥ 95%                      │
│       │                              ▼                                   │
│       │                        ┌────────────┐                           │
│       │◀───────────────────────│  CANARY_3  │ 60% 트래픽                │
│       │                        │  (60%, 5s) │                           │
│       │                        └─────┬──────┘                           │
│       │                              │ 성공률 ≥ 90%                      │
│       │                              ▼                                   │
│       │                        ┌────────────┐                           │
│       │                        │  CANARY_4  │ 100% 트래픽               │
│       │                        │ (100%, 0s) │                           │
│       │                        └─────┬──────┘                           │
│       │                              │ 성공률 ≥ 90%                      │
│       │                              ▼                                   │
│       │                        ┌────────────┐                           │
│       └────────────────────────│   CLOSED   │                           │
│            (실패 감지 시)       └────────────┘                           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Adaptive Threshold (Emergency Level 연동)

### 5.1 개념

시스템 Emergency Level에 따라 CB 임계값을 **자동으로 조정**합니다. 위기 상황일수록 **더 보수적(느슨하게)** 설정하여 자가 유도 블랙아웃을 방지합니다.

### 5.2 데이터 모델

```python
@dataclass
class AdaptiveThresholdPolicy:
    """Emergency Level에 따른 CB 임계값 자동 조정"""
    
    enabled: bool = True
    
    # 기본값
    base_failure_threshold: int = 5      # 기본 실패 횟수 임계값
    base_window_seconds: int = 60        # 기본 관찰 윈도우
    
    # Emergency Level별 배율
    level_multipliers: Dict[str, ThresholdMultiplier] = field(default_factory=lambda: {
        "NORMAL": ThresholdMultiplier(
            failure=1.0, 
            window=1.0,
            description="정상: 5회/60초"
        ),
        "ELEVATED": ThresholdMultiplier(
            failure=1.5, 
            window=1.5,
            description="주의: 7.5회/90초"
        ),
        "HIGH": ThresholdMultiplier(
            failure=2.0, 
            window=2.0,
            description="경고: 10회/120초"
        ),
        "CRITICAL": ThresholdMultiplier(
            failure=3.0, 
            window=3.0,
            description="위험: 15회/180초"
        ),
        "LOCKDOWN": ThresholdMultiplier(
            failure=float('inf'),  # 사실상 OPEN 금지
            window=float('inf'),
            description="잠금: 자동 OPEN 금지"
        ),
    })


@dataclass
class ThresholdMultiplier:
    """임계값 배율"""
    
    failure: float   # 실패 횟수 배율
    window: float    # 관찰 윈도우 배율
    description: str = ""
```

### 5.3 설계 근거

| Emergency Level | 실패 임계값 | 윈도우 | 이유 |
|-----------------|------------|--------|------|
| **NORMAL** | 5회 | 60초 | 표준 감지 속도 |
| **ELEVATED** | 7.5회 | 90초 | 약간 보수적 |
| **HIGH** | 10회 | 120초 | 가짜 에러에 안 속음 |
| **CRITICAL** | 15회 | 180초 | 진짜 장애만 감지 |
| **LOCKDOWN** | ∞ | ∞ | 자동 OPEN 금지 (Freeze) |

> **핵심 통찰**: 위기 상황에서 "더 민감하게"가 아니라 "더 보수적으로" 가야 합니다. 네트워크 지터(jitter)로 인한 일시적 에러에 CB가 반응하면 **자가 유도 블랙아웃**이 발생합니다.

---

## 6. LOCKDOWN Freeze Mode

### 6.1 개념

LOCKDOWN 상태에서는 **현재 CB 상태를 그대로 동결**합니다.

```
┌─────────────────────────────────────────────────────────────┐
│                    LOCKDOWN = Freeze Mode                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  자동 OPEN   ──────────▶  ❌ 금지                            │
│  자동 CLOSE  ──────────▶  ❌ 금지                            │
│  Canary Recovery ─────▶  ❌ 금지                            │
│                                                              │
│  수동 OPEN   ──────────▶  ✅ 허용 (운영자 명시적 개입)        │
│  수동 CLOSE  ──────────▶  ✅ 허용 (운영자 명시적 개입)        │
│                                                              │
│  현재 OPEN인 것  ──────▶  OPEN 유지                          │
│  현재 CLOSED인 것 ─────▶  CLOSED 유지                        │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 설계 결정

| 선택지 | 장점 | 단점 | 채택 |
|--------|------|------|------|
| **완전 비활성화** | 시스템 지능 완전 중단 | CLOSE 안 하면 영원히 차단 | ❌ |
| **OPEN만 금지** | 추가 차단 방지 | 자동 복구가 부하 유발 가능 | ❌ |
| **Freeze Mode** | 현 상태 유지, 안정성 최대 | 수동 개입 필요 | ✅ |

### 6.3 Freeze Mode 로직

```python
def should_change_state(self, service_id: str, new_state: CBState) -> tuple[bool, str]:
    """
    상태 변경 허용 여부 판단
    
    Returns:
        tuple[bool, str]: (허용 여부, 거부 시 사유)
    """
    emergency_level = self.governance.get_emergency_level()
    
    if emergency_level == EmergencyLevel.LOCKDOWN:
        # Freeze Mode: 모든 자동 변경 금지
        return False, "LOCKDOWN: Freeze Mode active - all automatic state changes blocked"
    
    # 다른 레벨에서는 정상 처리
    return True, ""


def force_open(self, service_id: str, reason: str, operator: str) -> bool:
    """
    수동 OPEN (LOCKDOWN에서도 허용)
    """
    # LOCKDOWN에서도 수동 조작은 허용
    # 단, Audit에 명확히 기록
    self.audit.log_cb_state_change_audit(
        service_id=service_id,
        previous_state="CLOSED",
        new_state="OPEN",
        trigger="MANUAL_OVERRIDE",
        operator=operator,
        reason=f"[LOCKDOWN OVERRIDE] {reason}",
    )
    return self._set_state(service_id, CBState.OPEN)
```

---

## 7. Blast Radius 연동

### 7.1 개념

CB가 자동 OPEN되기 전에 **연쇄 장애 영향을 분석**하여, CRITICAL 수준이면 OPEN을 보류합니다.

### 7.2 통합 플로우

```
record_failure() 호출
        │
        ▼
┌───────────────────┐
│  실패 횟수 증가    │
└────────┬──────────┘
         │
         ▼
    threshold 초과?
         │
    Yes  │  No
         │   └──▶ 종료
         ▼
┌───────────────────┐
│  Blast Radius     │
│  assess_impact()  │
└────────┬──────────┘
         │
         ▼
    level == CRITICAL?
         │
    Yes  │  No
         │   └──▶ OPEN 진행
         ▼
┌───────────────────────────────────────┐
│  OPEN 보류                             │
│  - 운영팀 알림                         │
│  - Audit: GOVERNANCE_BLOCKED 기록     │
│  - 수동 승인 대기                      │
└───────────────────────────────────────┘
```

### 7.3 구현 예시

```python
def _should_auto_open(self, service_id: str) -> tuple[bool, Optional[str]]:
    """
    자동 OPEN 허용 여부 판단 (Blast Radius 연동)
    
    Returns:
        tuple[bool, Optional[str]]: (허용 여부, 거부 시 사유)
    """
    # 1. Blast Radius 영향 평가
    assessment = self.blast_radius.assess_impact(
        stage_name="cb_auto_open",
        trigger_event=f"CB auto-opening for {service_id}",
        failing_services=[service_id],
    )
    
    # 2. CRITICAL이면 OPEN 보류
    if assessment.level == BlastRadiusLevel.CRITICAL:
        reason = (
            f"Blast Radius CRITICAL: {len(assessment.affected_services)} services affected. "
            f"Cascading risk: {assessment.cascading_risk}"
        )
        
        # 3. Audit 기록 (GOVERNANCE_BLOCKED)
        self.audit.log_event(
            event_type=AuditEventType.GOVERNANCE_BLOCKED,
            target_type="circuit_breaker",
            target_id=service_id,
            details={
                "action": "auto_open",
                "blocked_reason": "blast_radius_critical",
                "affected_services": assessment.affected_services,
                "assessment_id": assessment.assessment_id,
            }
        )
        
        # 4. 운영팀 알림
        self.notifier.send_alert(
            level="critical",
            title=f"CB OPEN Blocked: {service_id}",
            message=reason,
            requires_manual_approval=True,
        )
        
        return False, reason
    
    # 5. EXTENSIVE면 경고만
    if assessment.level == BlastRadiusLevel.EXTENSIVE:
        logger.warning(
            f"CB auto-open proceeding with caution: {service_id}, "
            f"blast radius EXTENSIVE ({len(assessment.affected_services)} services)"
        )
    
    return True, None
```

---

## 8. 차단 전략 (Recovery Strategy)

### 8.1 Immediate vs Graceful

| 전략 | 설명 | 사용 시점 |
|------|------|----------|
| **Immediate** | OPEN 즉시 모든 요청 거부 | 기본값, 대부분의 서비스 |
| **Graceful** | 진행중 요청 완료 후 차단 | 결제, 주문 등 트랜잭션 |

### 8.2 Delayed 전략 (사용 금지)

> ⚠️ **안티패턴**: Delayed OPEN(N초 후 차단)은 사용하지 않습니다.

**이유:**
- N초간 장애 서버에 요청 지속 → Thread 점유, 커넥션 풀 고갈
- Cascading Failure의 주범
- SRE 세계에서 안티패턴으로 간주

### 8.3 데이터 모델

```python
@dataclass
class OpenStrategy:
    """CB OPEN 전략"""
    
    type: str = "immediate"  # "immediate" | "graceful"
    
    # Graceful 전용 설정
    drain_timeout_seconds: int = 30  # 진행중 요청 대기 최대 시간
    
    # Graceful 실패 시 fallback
    force_after_timeout: bool = True  # timeout 후 강제 OPEN
```

---

## 9. 전체 설정 스키마

### 9.1 통합 Configuration

```python
@dataclass
class CircuitBreakerAdvancedConfig:
    """Circuit Breaker 고급 보호 설정"""
    
    # 서비스 등록 (사용자 필수 설정)
    services: List[ServiceConfig] = field(default_factory=list)
    
    # Load Shedding 정책
    load_shedding: LoadSheddingPolicy = field(default_factory=LoadSheddingPolicy)
    
    # Adaptive Threshold 정책
    adaptive_threshold: AdaptiveThresholdPolicy = field(default_factory=AdaptiveThresholdPolicy)
    
    # 기본 Recovery 전략
    default_recovery: RecoveryStrategy = field(default_factory=RecoveryStrategy)
    
    # 기본 Open 전략
    default_open_strategy: OpenStrategy = field(default_factory=OpenStrategy)
    
    # Blast Radius 연동
    blast_radius_integration: bool = True
    blast_radius_block_on_critical: bool = True
    
    # Freeze Mode 설정
    freeze_on_lockdown: bool = True
    allow_manual_override_in_lockdown: bool = True
```

### 9.2 사용 예시

```python
from selfhealing import configure_circuit_breaker
from selfhealing.circuit_breaker import (
    ServiceConfig,
    LoadSheddingPolicy,
    RecoveryStrategy,
    OpenStrategy,
)

# 전체 설정
configure_circuit_breaker(
    # 서비스 등록 (criticality 직접 지정)
    services=[
        ServiceConfig(
            service_id="payment-api",
            criticality="critical",
            shed_priority=0,  # 절대 차단 안 함
            recovery_strategy=RecoveryStrategy(
                type="canary",
                strict_mode=True,  # 100% 성공률 요구
            ),
        ),
        ServiceConfig(
            service_id="order-api",
            criticality="high",
            shed_priority=1,
        ),
        ServiceConfig(
            service_id="review-api",
            criticality="low",
            shed_priority=10,  # 우선 차단 대상
            min_traffic_percentage=5.0,  # 최소 5%는 보장
        ),
        ServiceConfig(
            service_id="recommend-api",
            criticality="low",
            shed_priority=10,
            recovery_strategy=RecoveryStrategy(type="immediate"),
        ),
    ],
    
    # Load Shedding (기본 3단계)
    load_shedding=LoadSheddingPolicy(
        enabled=True,
        trigger_threshold=30.0,
    ),
    
    # Adaptive Threshold (Emergency Level 연동)
    adaptive_threshold=AdaptiveThresholdPolicy(
        enabled=True,
        base_failure_threshold=5,
        base_window_seconds=60,
    ),
    
    # 기본 Recovery (Canary 4단계)
    default_recovery=RecoveryStrategy(type="canary"),
    
    # 결제용 Graceful Open
    default_open_strategy=OpenStrategy(type="graceful", drain_timeout_seconds=30),
    
    # Blast Radius 연동
    blast_radius_integration=True,
    blast_radius_block_on_critical=True,
    
    # LOCKDOWN Freeze Mode
    freeze_on_lockdown=True,
)
```

---

## 10. Audit 기록

### 10.1 새로운 Audit 이벤트 타입

| 이벤트 | 설명 | 기록 시점 |
|--------|------|----------|
| `GOVERNANCE_BLOCKED` | 자동 OPEN이 거부됨 | Blast Radius CRITICAL |
| `SHEDDING_ACTIVATED` | Load Shedding 시작 | critical 에러율 임계값 초과 |
| `SHEDDING_LEVEL_CHANGED` | Shedding 단계 변경 | 에러율 변화 |
| `CANARY_STAGE_ADVANCED` | Canary 다음 단계 진행 | 성공률 충족 |
| `CANARY_STAGE_FAILED` | Canary 단계 실패 | 성공률 미달 |
| `FREEZE_MODE_ACTIVATED` | Freeze Mode 활성화 | LOCKDOWN 진입 |
| `MANUAL_OVERRIDE_IN_LOCKDOWN` | LOCKDOWN 중 수동 조작 | 운영자 개입 |
| `PANIC_THRESHOLD_TRIGGERED` | 70%+ CB OPEN 감지 | 시스템 전체 붕괴 판단 |
| `CB_STATE_CHANGE_WITH_TRACE` | CB 상태 변화 + trace_id | CLOSED→OPEN 전환 |
| `CB_SYSTEM_DEGRADED` | CB 시스템 Degraded 모드 진입 | Redis 장애 감지 |
| `CB_SYSTEM_RECOVERED` | CB 시스템 정상 복구 | Redis 재연결 |
| `KILL_SWITCH_OVERRIDE` | Kill Switch 무시하고 수동 제어 | 운영자 개입 |
| `CB_FAIL_OPEN_ACTIVATED` | Fail-Open 발동 | CB 조회 실패 |

### 10.2 GOVERNANCE_BLOCKED 기록 예시

```python
{
    "event_type": "GOVERNANCE_BLOCKED",
    "timestamp": "2026-01-05T14:30:00Z",
    "target_type": "circuit_breaker",
    "target_id": "payment-api",
    "details": {
        "action": "auto_open",
        "blocked_reason": "blast_radius_critical",
        "blast_radius_level": "CRITICAL",
        "affected_services": ["order-api", "cart-api", "inventory-api"],
        "affected_count": 3,
        "cascading_risk": true,
        "assessment_id": "abc123",
        "message": "CB가 열려야 했으나, 연쇄 장애 위험(Blast Radius: CRITICAL)으로 인해 시스템이 차단을 보류함"
    },
    "requires_manual_approval": true
}
```

---

## 11. 구현 우선순위

| 순서 | 기능 | 복잡도 | 가치 | 비고 |
|------|------|--------|------|------|
| 1 | **CB 시스템 장애 대응** | 낮음 | **매우 높음** | 이미 구현됨 (L1/L2/ResilientStorage) |
| 2 | Adaptive Threshold | 낮음 | 높음 | |
| 3 | Freeze Mode | 낮음 | 높음 | |
| 4 | Blast Radius 연동 | 중간 | 매우 높음 | |
| 5 | Canary Recovery | 중간 | 높음 | |
| 6 | Load Shedding | 높음 | 높음 | |
| 7 | GOVERNANCE_BLOCKED Audit | 낮음 | 중간 | |
| 8 | **Panic Threshold** | 낮음 | **매우 높음** | |
| 9 | **Distributed Tracing 연동** | 낮음 | **매우 높음** | 기존 TraceContext 활용 |
| 10 | **Canary + Stale Cache 결합** | 중간 | 높음 | 기존 should_allow_with_fallback() 확장 |
| 11 | **Kill Switch Override 수정** | 낮음 | 높음 | 버그 수정 필요 |

---

## 11.1 구체적 구현 순서 (Phase별)

### Phase 0: 사전 준비 (Day 0) ✅ COMPLETED

| 순서 | 작업 | 파일 | 예상 시간 | 상태 |
|------|------|------|----------|------|
| 0.1 | **데이터 모델 정의** | `selfhealing/services/circuit_breaker/models.py` | 2시간 | ✅ 완료 |
| 0.2 | **Config 스키마 추가** | `selfhealing/core/config.py` | 1시간 | ✅ 완료 |
| 0.3 | **테스트 기반 작성** | `tests/services/circuit_breaker/test_advanced_protection.py` | 2시간 | ✅ 완료 |

**체크포인트**: ✅ 모든 dataclass 정의 완료, mypy 통과, 65개 테스트 통과

### Phase 1: 핵심 안전장치 (Day 1-2) ✅ COMPLETED

> 🎯 **목표**: CB가 잘못 동작해도 시스템이 살아남게 만들기

| 순서 | 작업 | 의존성 | 파일 | 예상 시간 | 상태 |
|------|------|--------|------|----------|------|
| 1.1 | **Kill Switch Override 수정** | 없음 | `manual_control.py` | 1시간 | ✅ 완료 |
| 1.2 | **Adaptive Threshold 구현** | 0.1, 0.2 | `adaptive_threshold.py` (신규) | 3시간 | ✅ 완료 |
| 1.3 | **Freeze Mode 구현** | 0.1 | `freeze_mode.py` (신규) | 2시간 | ✅ 완료 |
| 1.4 | **Panic Threshold 구현** | 1.2, 1.3 | `panic_threshold.py` (신규) | 3시간 | ✅ 완료 |

**체크포인트**: ✅ Emergency Level 연동 테스트 통과, 32개 Phase 1 테스트 통과

**구현 상세:**
- **Kill Switch Override**: `override_kill_switch` 파라미터로 LOCKDOWN에서도 수동 제어 가능
- **Adaptive Threshold**: Emergency Level별 배율 (NORMAL 1x → LOCKDOWN ∞)
- **Freeze Mode**: LOCKDOWN 자동 활성화, 수동 조작만 허용
- **Panic Threshold**: 70% CB OPEN 감지 시 Emergency Level 3 자동 에스컬레이션

```
구현 순서 다이어그램:

0.1 데이터 모델 ─────────────────────────────────┐
                                                 │
0.2 Config 스키마 ─────┬─────────────────────────┤
                       │                         │
                       ▼                         ▼
                   1.1 Kill Switch     1.2 Adaptive Threshold
                   Override 수정 ✅            ✅ │
                                               │
                       ┌───────────────────────┘
                       │
                       ▼
                   1.3 Freeze Mode ✅───▶ 1.4 Panic Threshold ✅
```

### Phase 2: Audit 강화 (Day 3) ✅ COMPLETED

> 🎯 **목표**: 모든 상태 변화를 추적 가능하게 만들기

| 순서 | 작업 | 의존성 | 파일 | 예상 시간 | 상태 |
|------|------|--------|------|----------|------|
| 2.1 | **GOVERNANCE_BLOCKED Audit 추가** | 1.2 | `audit_helpers.py` | 1시간 | ✅ 완료 |
| 2.2 | **Distributed Tracing 연동** | 없음 | `tracing.py` (신규) | 3시간 | ✅ 완료 |
| 2.3 | **CB 상태 변화 Audit 확장** | 2.2 | `audit_helpers.py` | 2시간 | ✅ 완료 |

**체크포인트**: ✅ trace_id가 모든 CB 상태 변화 로그에 포함됨, 28개 Phase 2 테스트 통과

**구현 상세:**
- **tracing.py**: TracingConfig, TriggeringRequestInfo, TraceContextProvider, CircuitBreakerTracingManager (약 500줄)
- **audit_helpers.py**: log_cb_state_change_with_trace_audit, log_governance_blocked_cb_audit 추가
- **__init__.py**: Phase 2 exports 추가

```
구현 순서 다이어그램:

                   2.1 GOVERNANCE_BLOCKED Audit
                           ✅
                           │
                           ▼
2.2 Distributed Tracing ───┼───▶ 2.3 CB Audit 확장
        ✅                 │            ✅
                           │
          ┌────────────────┘
          ▼
    모든 상태 변화에 trace_id 포함
```

### Phase 3: 연쇄 장애 방지 (Day 4-5) ✅ COMPLETED

> 🎯 **목표**: CB OPEN이 다른 서비스에 영향 안 주게 만들기

| 순서 | 작업 | 의존성 | 파일 | 예상 시간 | 상태 |
|------|------|--------|------|----------|------|
| 3.1 | **ServiceConfig 모델 구현** | 0.1 | `service_config.py` (신규) | 2시간 | ✅ 완료 |
| 3.2 | **Blast Radius 연동** | 3.1, 1.4 | `blast_radius_integration.py` (신규) | 4시간 | ✅ 완료 |
| 3.3 | **Blast Radius 테스트** | 3.2 | `test_phase3_advanced_protection.py` | 2시간 | ✅ 완료 |

**체크포인트**: ✅ Blast Radius CRITICAL 시 자동 OPEN 차단 확인, 39개 Phase 3 테스트 통과

**구현 상세:**
- **service_config.py**: ServiceConfigManager (싱글톤), criticality 조회, Load Shedding 대상 선택 (약 500줄)
- **blast_radius_integration.py**: BlastRadiusIntegration, ServiceDependencyGraph, BlastRadiusAssessment (약 650줄)
- **__init__.py**: Phase 3 exports 추가

```
구현 순서 다이어그램:

3.1 ServiceConfig 모델 ─────────┐
    (criticality 관리)         │
           │                   │
           ▼                   │
3.2 Blast Radius 연동 ─────────┼───▶ 3.3 테스트
    (assess_impact,            │         39개 통과
     should_auto_open)         │
           │                   │
           ▼                   │
    CRITICAL 시 자동 OPEN 차단  ◀───────┘
    + GOVERNANCE_BLOCKED Audit
```

### Phase 4: 복구 전략 (Day 6-7) ✅ COMPLETED

> 🎯 **목표**: HALF_OPEN에서 안전하게 복구하기

| 순서 | 작업 | 의존성 | 파일 | 예상 시간 | 상태 |
|------|------|--------|------|----------|------|
| 4.1 | **Canary Stage 상태 머신** | 0.1 | `canary_recovery.py` (신규) | 4시간 | ✅ 완료 |
| 4.2 | **Canary + Stale Cache 결합** | 4.1 | `stale_cache_integration.py` (신규) | 4시간 | ✅ 완료 |
| 4.3 | **Recovery 전략 선택자** | 4.1 | `recovery_strategy.py` (신규) | 2시간 | ✅ 완료 |

**체크포인트**: ✅ Canary 4단계 (10→30→60→100%) 정상 동작 확인, 45개 Phase 4 테스트 통과

**구현 상세:**
- **canary_recovery.py**: CanaryRecoveryManager (싱글톤), 단계별 성공률 추적, 자동 단계 전이 (약 650줄)
- **stale_cache_integration.py**: CanaryWithStaleCacheService, StaleCacheStore, non-canary 요청 캐시 처리 (약 550줄)
- **recovery_strategy.py**: RecoveryStrategySelector, criticality 기반 전략 선택 (약 450줄)
- **__init__.py**: Phase 4 exports 추가

```
복구 전략 구현 흐름:

4.1 Canary Stage ───────┬──────▶ 4.3 Recovery 전략 선택자
    상태 머신 ✅         │           (immediate/canary 선택) ✅
                        ▼
              4.2 Canary + Stale Cache ✅
                  (non-canary → stale cache)
```

### Phase 5: Load Shedding (Day 8-9) ✅ COMPLETED

> 🎯 **목표**: 핵심 서비스 보호를 위해 비핵심 트래픽 제한

| 순서 | 작업 | 의존성 | 파일 | 예상 시간 | 상태 |
|------|------|--------|------|----------|------|
| 5.1 | **Shedding Level 정책** | 3.1 | `load_shedding.py` (신규) | 3시간 | ✅ 완료 |
| 5.2 | **Shedding 알고리즘 구현** | 5.1 | `load_shedding.py` | 4시간 | ✅ 완료 |
| 5.3 | **Shedding Middleware 연동** | 5.2 | `load_shedding.py` | 3시간 | ✅ 완료 |
| 5.4 | **Shedding 대시보드** | 5.2 | `load_shedding.py` | 2시간 | ✅ 완료 |

**체크포인트**: ✅ critical 서비스 에러율 30% → low criticality 50% 제한 확인, 83개 Phase 5 테스트 통과

**구현 상세:**
- **load_shedding.py**: LoadSheddingManager (싱글톤), evaluate_shedding 알고리즘, LoadSheddingMiddleware, LoadSheddingDashboard (약 850줄)
- **데이터 모델**: SheddingState, SheddingDecision, SheddingStatus, SheddingAuditEntry, ErrorRateProvider
- **Convenience 함수**: get_load_shedding_manager, evaluate_shedding, should_allow_shedding_request 등
- **__init__.py**: Phase 5 exports 추가

```
Load Shedding 구현 흐름:

5.1 Shedding Level ─────┬──────▶ 5.2 알고리즘
    정책 정의 ✅         │           evaluate_shedding() ✅
                        │               │
                        ▼               ▼
              5.3 Middleware ✅ ◀──── 5.4 Dashboard ✅
                  (요청 필터링)         (운영자 API)
```

### Phase 6: 통합 및 문서화 (Day 10)

| 순서 | 작업 | 의존성 | 파일 | 예상 시간 |
|------|------|--------|------|----------|
| 6.1 | **통합 테스트 작성** | 전체 | `test_integration.py` | 4시간 |
| 6.2 | **설정 가이드 문서화** | 전체 | `docs/` | 2시간 |
| 6.3 | **모니터링 대시보드 설정** | 5.4 | Grafana JSON | 2시간 |

---

### 11.2 파일 구조 (최종)

```
packages/selfhealing-python/src/selfhealing/services/circuit_breaker/
├── __init__.py
├── service.py                    # 기존 (수정)
├── manual_control.py             # 1.1 ✅ 완료 - Kill Switch Override 수정
├── protection.py                 # 기존
├── models.py                     # 0.1 ✅ 완료 - 데이터 모델
├── adaptive_threshold.py         # 1.2 ✅ 완료 - Emergency Level 연동 (337줄)
├── freeze_mode.py                # 1.3 ✅ 완료 - LOCKDOWN Freeze (400줄)
├── panic_threshold.py            # 1.4 ✅ 완료 - 70% OPEN 감지 (449줄)
├── tracing.py                    # 2.2 ✅ 완료 - Distributed Tracing (약 500줄)
├── service_config.py             # 3.1 ✅ 완료 - Criticality 설정 (약 500줄)
├── blast_radius_integration.py   # 3.2 ✅ 완료 - 연쇄 장애 분석 (약 650줄)
├── canary_recovery.py            # 4.1 ✅ 완료 - 단계적 복구 (약 650줄)
├── stale_cache_integration.py    # 4.2 ✅ 완료 - Canary + Cache (약 550줄)
├── recovery_strategy.py          # 4.3 ✅ 완료 - 전략 선택자 (약 450줄)
└── load_shedding.py              # 5.1-5.4 ✅ 완료 - 부분적 차단 (약 850줄)

packages/selfhealing-python/src/selfhealing/core/
├── config.py                     # 0.2 ✅ 완료 - CircuitBreakerAdvancedConfig 추가

packages/selfhealing-python/src/selfhealing/services/
├── audit_helpers.py              # 1.1-2.3 ✅ 완료 - Phase 1+2 Audit 함수 추가

tests/services/circuit_breaker/
├── test_advanced_protection.py   # 0.3 ✅ 완료 - 65개 테스트
├── test_phase1_advanced_protection.py  # 1.1-1.4 ✅ 완료 - 32개 테스트 (611줄)
├── test_phase2_advanced_protection.py  # 2.1-2.3 ✅ 완료 - 28개 테스트
├── test_phase3_advanced_protection.py  # 3.1-3.3 ✅ 완료 - 39개 테스트
├── test_phase4_advanced_protection.py  # 4.1-4.3 ✅ 완료 - 45개 테스트
├── test_phase5_advanced_protection.py  # 5.1-5.4 ✅ 완료 - 83개 테스트
├── test_canary_recovery.py       # 4.1 테스트
├── test_load_shedding.py         # 5.1 테스트
└── test_integration.py           # 6.1 신규
```

---

### 11.3 의존성 그래프 (전체)

```
                        ┌─────────────────────────────────────────────────────┐
                        │               Phase 0: 사전 준비 ✅                  │
                        │  0.1 데이터 모델 ──▶ 0.2 Config ──▶ 0.3 테스트 기반  │
                        └──────────────────────────┬──────────────────────────┘
                                                   │
           ┌───────────────────────────────────────┼───────────────────────────────────────┐
           │                                       │                                       │
           ▼                                       ▼                                       ▼
┌─────────────────────┐              ┌─────────────────────┐              ┌─────────────────────┐
│  Phase 1: 안전장치 ✅│              │  Phase 2: Audit  ✅ │              │  Phase 3: 연쇄방지 ✅│
│                     │              │                     │              │                     │
│  1.1 Kill Switch ✅ │              │  2.1 GOVERNANCE_ ✅ │              │  3.1 ServiceConfig ✅│
│      Override       │              │      BLOCKED        │◀─────────────│                     │
│         │           │              │         │           │              │         │           │
│         ▼           │              │         ▼           │              │         ▼           │
│  1.2 Adaptive    ✅ │──────────────│▶ 2.2 Tracing     ✅ │              │  3.2 Blast Radius ✅│
│      Threshold      │              │         │           │              │      연동           │
│         │           │              │         ▼           │              │         │           │
│         ▼           │              │  2.3 CB Audit    ✅ │              │         ▼           │
│  1.3 Freeze Mode ✅ │              │       확장          │              │  3.3 테스트       ✅│
│         │           │              └─────────────────────┘              └─────────────────────┘
│         ▼           │                                                              │
│  1.4 Panic       ✅ │──────────────────────────────────────────────────────────────┘
│      Threshold      │
└─────────────────────┘
           │
           ▼
┌─────────────────────┐              ┌─────────────────────┐
│  Phase 4: 복구 전략 ✅│              │  Phase 5: Shedding ✅│
│                     │              │                     │
│  4.1 Canary Stage ✅ │              │  5.1 Shedding Level✅│◀─── Phase 3.1
│         │           │              │         │           │
│    ┌────┴────┐      │              │         ▼           │
│    ▼         ▼      │              │  5.2 알고리즘     ✅ │
│  4.2 ✅    4.3 ✅    │              │         │           │
│  Stale    Recovery  │              │         ▼           │
│  Cache    전략      │              │  5.3 Middleware  ✅ │
└─────────────────────┘              │         │           │
           │                         │         ▼           │
           │                         │  5.4 대시보드    ✅ │
           │                         └─────────────────────┘
           │                                   │
           └───────────────────┬───────────────┘
                               ▼
                    ┌─────────────────────┐
                    │  Phase 6: 통합      │
                    │                     │
                    │  6.1 통합 테스트    │
                    │  6.2 문서화         │
                    │  6.3 모니터링       │
                    └─────────────────────┘
```

---

### 11.4 예상 총 소요 시간

| Phase | 작업 수 | 예상 시간 | 누적 | 상태 |
|-------|--------|----------|------|------|
| Phase 0 | 3 | 5시간 | 5시간 | ✅ 완료 |
| Phase 1 | 4 | 9시간 | 14시간 | ✅ 완료 |
| Phase 2 | 3 | 6시간 | 20시간 | ✅ 완료 |
| Phase 3 | 3 | 8시간 | 28시간 | ✅ 완료 |
| Phase 4 | 3 | 10시간 | 38시간 | ✅ 완료 |
| Phase 5 | 4 | 12시간 | 50시간 | ✅ 완료 |
| Phase 6 | 3 | 8시간 | **58시간** | 🔲 대기 |

> 💡 **현재 진행 상황**: Phase 0-5 완료 (50시간) - Load Shedding 완료
> 
> **테스트 현황**: 385개 테스트 통과 (65 Phase0 + 32 Phase1 + 28 Phase2 + 39 Phase3 + 45 Phase4 + 83 Phase5 + 93 existing)

---

## 12. 참고: 업계 관행

| 회사 | 접근 방식 |
|------|----------|
| **Netflix** | Cell-based architecture + Hystrix → Resilience4j |
| **Amazon** | Shuffle Sharding + Cell isolation |
| **Google** | Overload Protection + Graceful degradation |
| **Uber** | Tenancy isolation + Adaptive retry |
| **LinkedIn** | Graduated Degradation |

---

## 13. CB 시스템 자체 장애 대응

### 13.1 문제 정의

Circuit Breaker 시스템 자체가 장애나면 어떻게 되는가?

| CB 장애 시나리오 | 영향 | 대응 필요 |
|-----------------|------|----------|
| **Redis 장애** | CB 상태 조회 불가 | ✅ |
| **상태 저장 실패** | 상태 불일치 | ✅ |
| **Kill Switch 활성화** | 수동 제어 차단 | ✅ |
| **설정 조회 실패** | 기본값 누락 | ✅ |

### 13.2 기존 구현 활용 (이미 구현됨)

#### 13.2.1 L1/L2 Cache 계층 (precomputed_cache.py)

```python
# 이미 구현된 캐시 계층 구조
# packages/selfhealing-python/src/selfhealing/services/precomputed_cache.py

L1_TTL_SECONDS = 2.0       # In-process cache TTL (0ms 오버헤드)
L2_TTL_SECONDS = 15.0      # Redis cache TTL (1-5ms 오버헤드)

class L1Cache:
    """
    L1 In-Process Cache using cachetools.TTLCache.
    
    Zero network overhead - immediate response.
    Falls back to simple dict if cachetools not installed.
    """
```

**CB 상태 캐싱 전략:**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CB 상태 조회 흐름                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  CB 상태 요청 ──▶ L1 Cache 조회 (2초 TTL)                               │
│                        │                                                 │
│                   Hit? │ Miss                                            │
│                        ▼                                                 │
│                   L2 Redis 조회 (15초 TTL)                              │
│                        │                                                 │
│                   Hit? │ Miss/Fail                                       │
│                        ▼                                                 │
│                   ResilientStorage 조회                                  │
│                        │                                                 │
│                   Success? │ Fail                                        │
│                        │     └──▶ DegradedModeHandler.get() ────────────┤
│                        ▼                                                 │
│                   Return State                                           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

#### 13.2.2 ResilientStorageBackend (adapters/resilient/backend.py)

```python
# 이미 구현된 Resilient Storage
# packages/selfhealing-python/src/selfhealing/adapters/resilient/backend.py

class StorageMode(Enum):
    REDIS = "redis"           # Normal mode - Redis only
    DEGRADED = "degraded"     # Fallback mode - Memory + WAL
    RECOVERING = "recovering"  # Transitioning back to Redis

class ResilientStorageBackend:
    """
    Redis-First + Graceful Degradation + WAL.
    
    Zero data loss storage backend that:
    - Uses Redis in normal mode
    - Falls back to Memory + WAL on Redis failure
    - Recovers from WAL on server restart
    - Syncs WAL to Redis on recovery
    
    Core Invariant:
        WAL-First Write Protocol in degraded mode:
        1. WAL.write() + fsync() - disk persisted first
        2. Memory[key] = value  - then memory
    """
```

**CB 상태 저장 시 활용:**

| 모드 | 저장 위치 | 복구 방법 |
|------|----------|----------|
| **REDIS** | Redis only | - |
| **DEGRADED** | WAL → Memory | 서버 재시작 시 WAL 복구 |
| **RECOVERING** | Redis + Memory sync | WAL → Redis 동기화 |

#### 13.2.3 DegradedModeHandler (core/degraded_mode_handler.py)

```python
# 이미 구현된 Degraded Mode Handler
# packages/selfhealing-python/src/selfhealing/core/degraded_mode_handler.py

class DegradedModeHandler:
    """
    설정 조회 실패 시 안전한 기본값 반환.
    
    Usage:
        # 설정 조회 실패 시
        cb_threshold = DegradedModeHandler.get('CB_FAILURE_THRESHOLD')  # Returns 5
    """
    
    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """Get safe default value for given configuration key."""
        
    @classmethod
    def is_degraded(cls) -> bool:
        """Check if system is in degraded mode."""
```

### 13.3 Kill Switch Override 문제 (버그)

#### 13.3.1 문제 상황

```python
# packages/selfhealing-python/src/selfhealing/services/circuit_breaker/manual_control.py#L81

def force_open(self, service_name: str, reason: str, ...) -> CircuitBreakerResult:
    # Kill Switch 체크: 시스템이 비활성화되면 모든 self-healing 작업 중단
    if not _is_system_enabled():  # ⚠️ 문제: LOCKDOWN에서 수동 제어도 막힘!
        return CircuitBreakerResult.failed(
            service_name=service_name,
            error="Kill Switch is active: self-healing system is disabled",
        )
```

#### 13.3.2 문제점

| 상황 | 기대 동작 | 현재 동작 | 문제 |
|------|----------|----------|------|
| **LOCKDOWN + 수동 OPEN** | 허용 | 차단 | 운영자 개입 불가 |
| **LOCKDOWN + 수동 CLOSE** | 허용 | 차단 | 서비스 복구 불가 |
| **Kill Switch + 긴급 복구** | 허용 | 차단 | 완전히 막힘 |

> ⚠️ **철학 위반**: "운영자는 신이다" - Kill Switch가 수동 제어까지 막으면 안 됨

#### 13.3.3 수정 방안

```python
def force_open(
    self, 
    service_name: str, 
    reason: str,
    override_kill_switch: bool = False,  # 신규 파라미터
    **kwargs
) -> CircuitBreakerResult:
    """
    수동 OPEN (LOCKDOWN/Kill Switch에서도 허용 가능)
    
    Args:
        override_kill_switch: True면 Kill Switch 무시 (운영자 권한)
    """
    # Kill Switch 체크 (수동 override 허용)
    if not _is_system_enabled() and not override_kill_switch:
        return CircuitBreakerResult.failed(
            service_name=service_name,
            error="Kill Switch is active: use override_kill_switch=True for manual control",
        )
    
    # Kill Switch override 시 Audit 기록
    if override_kill_switch and not _is_system_enabled():
        self.audit.log_event(
            event_type=AuditEventType.KILL_SWITCH_OVERRIDE,
            target_type="circuit_breaker",
            target_id=service_name,
            details={
                "action": "force_open",
                "reason": reason,
                "override_by": kwargs.get("controlled_by_id"),
            },
            severity="WARNING",
        )
    
    # 기존 로직 계속...
```

### 13.4 CB Fail-Open 정책

CB 시스템 자체가 장애나면 **Fail-Open** (모든 요청 허용):

```python
# 이미 구현된 패턴 - service.py
def should_allow(self, service_name: str) -> bool:
    """
    Request permission check with Fail-Open.
    
    If CB system fails, allow all requests (fail-open).
    """
    try:
        state = self.get_or_create_state(service_name)
        return state.state != CircuitState.OPEN
    except Exception as e:
        # Fail-Open: CB 장애 시 모든 요청 허용
        logger.warning(f"[CircuitBreaker] System error, fail-open: {e}")
        return True
```

| CB 컴포넌트 장애 | Fail-Open 동작 | 이유 |
|-----------------|----------------|------|
| Redis 조회 실패 | 모든 요청 허용 | 차단이 더 위험 |
| 상태 저장 실패 | 요청은 허용, 로깅만 | 비즈니스 우선 |
| Audit 실패 | 요청은 허용, Shadow Log | 데이터 손실 방지 |

### 13.5 Audit 이벤트

| 이벤트 | 설명 | 기록 시점 |
|--------|------|----------|
| `CB_SYSTEM_DEGRADED` | CB 시스템 Degraded 모드 진입 | Redis 장애 감지 |
| `CB_SYSTEM_RECOVERED` | CB 시스템 정상 복구 | Redis 재연결 |
| `KILL_SWITCH_OVERRIDE` | Kill Switch 무시하고 수동 제어 | 운영자 개입 |
| `CB_FAIL_OPEN_ACTIVATED` | Fail-Open 발동 | CB 조회 실패 |

### 13.6 통합 흐름

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CB 시스템 장애 대응 통합 흐름                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  CB 상태 조회 요청                                                       │
│        │                                                                 │
│        ▼                                                                 │
│  ┌─────────────┐                                                        │
│  │ L1 Cache    │ ◀── 2초 TTL, In-Process                               │
│  └──────┬──────┘                                                        │
│         │ Miss                                                           │
│         ▼                                                                │
│  ┌─────────────┐                                                        │
│  │ L2 Redis    │ ◀── 15초 TTL                                          │
│  └──────┬──────┘                                                        │
│         │ Miss/Fail                                                      │
│         ▼                                                                │
│  ┌─────────────────────────┐                                            │
│  │ ResilientStorageBackend │                                            │
│  │  ├─ REDIS mode         │ ◀── 정상                                   │
│  │  ├─ DEGRADED mode      │ ◀── Memory + WAL                           │
│  │  └─ RECOVERING mode    │ ◀── WAL → Redis 동기화                     │
│  └──────┬─────────────────┘                                             │
│         │ Complete Fail                                                  │
│         ▼                                                                │
│  ┌─────────────────────────┐                                            │
│  │ DegradedModeHandler     │ ◀── 안전한 기본값 반환                     │
│  │   CB_FAILURE_THRESHOLD=5│                                            │
│  └──────┬─────────────────┘                                             │
│         │                                                                │
│         ▼                                                                │
│  ┌─────────────────────────┐                                            │
│  │ Fail-Open 정책          │ ◀── 모든 요청 허용                         │
│  │   (차단보다 허용이 안전) │                                            │
│  └─────────────────────────┘                                            │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 14. Panic Threshold (시스템 전체 자폭 방지)

### 14.1 개념

**70% 이상의 Circuit Breaker가 동시에 OPEN**되면, 개별 서비스 문제가 아니라 **인프라 전체 붕괴**로 판단합니다. 이때 자율 운영 엔진이 스스로 **Emergency Level 3를 선포**하고 모든 자동 복구를 중단합니다.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     Panic Threshold 감지 흐름                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  get_open_circuits() ──▶ OPEN 비율 계산 ──▶ 70% 초과?                   │
│                                                │                         │
│                                           No   │   Yes                   │
│                                            └───┘    │                    │
│                                                     ▼                    │
│                                          ┌─────────────────────┐        │
│                                          │  PANIC THRESHOLD    │        │
│                                          │  TRIGGERED!         │        │
│                                          └──────────┬──────────┘        │
│                                                     │                    │
│                                                     ▼                    │
│                                          ┌─────────────────────┐        │
│                                          │  Emergency Level 3  │        │
│                                          │  자동 선포           │        │
│                                          └──────────┬──────────┘        │
│                                                     │                    │
│                                                     ▼                    │
│                                          ┌─────────────────────┐        │
│                                          │  Global Lockdown    │        │
│                                          │  (Freeze Mode)      │        │
│                                          └──────────┬──────────┘        │
│                                                     │                    │
│                                                     ▼                    │
│                            ┌────────────────────────────────────────┐   │
│                            │  모든 자동 복구 중단:                    │   │
│                            │  - Replay 중지                         │   │
│                            │  - Canary Recovery 중지                │   │
│                            │  - Auto OPEN/CLOSE 금지               │   │
│                            │  - 수동 개입 대기                       │   │
│                            └────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 14.2 데이터 모델

```python
@dataclass
class PanicThresholdPolicy:
    """Panic Threshold (시스템 전체 붕괴 감지) 정책"""
    
    enabled: bool = True
    
    # OPEN 비율 임계값 (기본 70%)
    open_rate_threshold: float = 70.0
    
    # 최소 등록 서비스 수 (너무 적으면 오탐 가능)
    min_registered_services: int = 3
    
    # 감지 시 자동 Emergency Level 3 선포
    auto_escalate_to_level3: bool = True
    
    # 자동 복구 대상 지정 (전부 중단)
    halt_on_panic: List[str] = field(default_factory=lambda: [
        "replay",           # DLQ Replay 중단
        "canary_recovery",  # Canary Recovery 중단
        "auto_open",        # 자동 OPEN 금지
        "auto_close",       # 자동 CLOSE 금지
    ])
    
    # 체크 주기 (초)
    check_interval_seconds: int = 5
    
    # 연속 감지 횟수 (Flapping 방지)
    consecutive_triggers_required: int = 2
```

### 14.3 구현

```python
class PanicThresholdMonitor:
    """시스템 전체 OPEN 비율을 모니터링하고 Panic Threshold 발동"""
    
    def __init__(
        self,
        circuit_breaker_service: CircuitBreakerService,
        emergency_manager: EmergencyModeManager,
        policy: PanicThresholdPolicy,
    ):
        self.cb_service = circuit_breaker_service
        self.emergency_manager = emergency_manager
        self.policy = policy
        self._consecutive_triggers = 0
    
    def check_panic_threshold(self) -> PanicThresholdResult:
        """
        시스템 전체 OPEN 비율 확인 및 Panic Threshold 발동
        
        Returns:
            PanicThresholdResult: 감지 결과 및 취한 조치
        """
        if not self.policy.enabled:
            return PanicThresholdResult(triggered=False)
        
        # 1. 모든 Circuit 상태 수집 (기존 get_open_circuits() 활용)
        open_circuits = self.cb_service.get_open_circuits()
        total_circuits = self.cb_service.get_all_registered_circuits()
        
        # 2. 최소 서비스 수 미달 시 무시 (오탐 방지)
        if len(total_circuits) < self.policy.min_registered_services:
            return PanicThresholdResult(
                triggered=False,
                reason=f"Insufficient services ({len(total_circuits)} < {self.policy.min_registered_services})"
            )
        
        # 3. OPEN 비율 계산
        open_rate = (len(open_circuits) / len(total_circuits)) * 100
        
        # 4. 임계값 초과 확인
        if open_rate >= self.policy.open_rate_threshold:
            self._consecutive_triggers += 1
            
            # 연속 감지 횟수 충족 시 Panic 발동
            if self._consecutive_triggers >= self.policy.consecutive_triggers_required:
                return self._trigger_panic(open_rate, open_circuits, total_circuits)
        else:
            self._consecutive_triggers = 0
        
        return PanicThresholdResult(
            triggered=False,
            open_rate=open_rate,
            open_count=len(open_circuits),
            total_count=len(total_circuits),
        )
    
    def _trigger_panic(
        self, 
        open_rate: float, 
        open_circuits: List[str],
        total_circuits: List[str],
    ) -> PanicThresholdResult:
        """Panic Threshold 발동 및 Emergency Level 3 선포"""
        
        # 1. Audit 기록 (실사 시 높은 평가 획득용)
        audit_entry = {
            "event_type": "PANIC_THRESHOLD_TRIGGERED",
            "timestamp": datetime.utcnow().isoformat(),
            "open_rate": open_rate,
            "open_circuits": open_circuits,
            "total_circuits": len(total_circuits),
            "message": f"Panic Threshold triggered (Open Rate: {open_rate:.1f}%) - "
                       f"Escalating to Emergency Level 3",
            "action_taken": "emergency_level_3_escalation",
        }
        self.audit.log_event(
            event_type=AuditEventType.PANIC_THRESHOLD_TRIGGERED,
            target_type="system",
            target_id="global",
            details=audit_entry,
            severity="CRITICAL",
        )
        
        logger.critical(
            f"🚨 PANIC THRESHOLD TRIGGERED: {len(open_circuits)}/{len(total_circuits)} "
            f"circuits OPEN ({open_rate:.1f}%) - Escalating to Emergency Level 3"
        )
        
        # 2. Emergency Level 3 자동 선포
        if self.policy.auto_escalate_to_level3:
            self.emergency_manager.escalate_to_level(
                level=EmergencyLevel.LEVEL_3,
                reason=f"Panic Threshold: {open_rate:.1f}% of circuits are OPEN",
                triggered_by="PanicThresholdMonitor",
            )
        
        # 3. 모든 자동 복구 중단
        for system in self.policy.halt_on_panic:
            self._halt_system(system)
        
        # 4. 운영팀 즉시 알림
        self.notifier.send_critical_alert(
            title="🚨 PANIC THRESHOLD - Emergency Level 3 Activated",
            message=(
                f"시스템 전체 붕괴 감지: {len(open_circuits)}/{len(total_circuits)} "
                f"서비스 OPEN ({open_rate:.1f}%)\n\n"
                f"자동 조치:\n"
                f"- Emergency Level 3 선포\n"
                f"- 모든 자동 복구 중단 (Replay, Canary 등)\n"
                f"- Global Lockdown (Freeze Mode) 활성화\n\n"
                f"즉각적인 운영자 개입이 필요합니다."
            ),
            requires_immediate_action=True,
        )
        
        return PanicThresholdResult(
            triggered=True,
            open_rate=open_rate,
            open_count=len(open_circuits),
            total_count=len(total_circuits),
            action_taken="emergency_level_3_escalation",
            halted_systems=self.policy.halt_on_panic,
        )
```

### 14.4 Audit 기록 형식 (실사 대비)

```python
# 실사 시 높은 평가를 받기 위한 Audit 기록 형식
{
    "event_type": "PANIC_THRESHOLD_TRIGGERED",
    "timestamp": "2026-01-05T14:30:00Z",
    "severity": "CRITICAL",
    "target_type": "system",
    "target_id": "global",
    "details": {
        "open_rate": 72.0,
        "threshold": 70.0,
        "open_circuits": ["payment-api", "order-api", "cart-api", "inventory-api"],
        "open_count": 4,
        "total_count": 6,
        "message": "Panic Threshold triggered (Open Rate: 72%) - Escalating to Emergency Level 3",
        "action_taken": "emergency_level_3_escalation",
        "halted_systems": ["replay", "canary_recovery", "auto_open", "auto_close"],
        "triggered_by": "PanicThresholdMonitor",
        "consecutive_triggers": 2,
        "root_cause_hypothesis": "인프라 전체 붕괴 감지 - 개별 서비스 장애 아님"
    }
}
```

---

## 15. Distributed Tracing 연동 (장애 전파 가시성)

### 15.1 개념

CB 상태 변화 시 **해당 상태 변화를 유발한 '마지막 요청'의 trace_id**를 감사 로그에 기록합니다. 운영자가 "서킷이 왜 열렸지?"라고 물었을 때, 로그의 trace_id 하나로 **전체 서비스 호출 흐름을 1초 만에 시각화**할 수 있습니다.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Tracing 연동 흐름                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  요청 진입 ──▶ TraceContext 추출 ──▶ CB record_failure() 호출           │
│                     │                        │                           │
│                     ▼                        ▼                           │
│            ┌─────────────┐          ┌────────────────┐                  │
│            │ trace_id    │          │ 실패 횟수 증가  │                  │
│            │ span_id     │          │ threshold 확인  │                  │
│            │ request_id  │          └───────┬────────┘                  │
│            └─────────────┘                  │                            │
│                     │                       ▼                            │
│                     │              threshold 초과?                       │
│                     │                   │                                │
│                     │              Yes  │                                │
│                     │                   ▼                                │
│                     │          ┌────────────────────────┐               │
│                     └─────────▶│  CB OPEN + Audit 기록   │               │
│                                │  (triggering_trace_id   │               │
│                                │   포함)                 │               │
│                                └────────────────────────┘               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 15.2 데이터 모델

```python
@dataclass
class TracingConfig:
    """CB Audit Tracing 설정"""
    
    enabled: bool = True
    
    # 기록할 trace 헤더들 (기존 TraceContext 활용)
    captured_headers: List[str] = field(default_factory=lambda: [
        "X-Trace-ID",
        "X-Request-ID", 
        "X-Correlation-ID",
        "traceparent",      # W3C Trace Context
        "X-Amzn-Trace-Id",  # AWS X-Ray
    ])
    
    # 상태 변화 시 마지막 요청 정보 기록
    record_triggering_request: bool = True
    
    # Span 생성 여부 (OpenTelemetry 연동)
    create_spans: bool = True


@dataclass
class TriggeringRequestInfo:
    """CB 상태 변화를 유발한 마지막 요청 정보"""
    
    trace_id: str
    span_id: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    
    # 요청 메타데이터
    timestamp: datetime = field(default_factory=datetime.utcnow)
    endpoint: Optional[str] = None
    method: Optional[str] = None
    error_message: Optional[str] = None
    
    # 전체 추적 가능한 링크
    trace_url: Optional[str] = None  # Jaeger/Zipkin URL
```

### 15.3 구현

```python
class CircuitBreakerWithTracing:
    """Tracing이 연동된 Circuit Breaker"""
    
    def __init__(self, trace_context_provider: TraceContextProvider):
        self.trace_provider = trace_context_provider
        self._last_triggering_request: Dict[str, TriggeringRequestInfo] = {}
    
    def record_failure(
        self, 
        service_id: str, 
        error: Exception,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        실패 기록 + Triggering Request 정보 저장
        """
        # 1. 현재 TraceContext에서 trace_id 추출
        trace_ctx = self.trace_provider.get_current_context()
        
        # 2. Triggering Request 정보 저장 (마지막 실패 요청)
        self._last_triggering_request[service_id] = TriggeringRequestInfo(
            trace_id=trace_ctx.trace_id,
            span_id=trace_ctx.span_id,
            request_id=trace_ctx.request_id,
            correlation_id=trace_ctx.correlation_id,
            endpoint=request_context.get("endpoint") if request_context else None,
            method=request_context.get("method") if request_context else None,
            error_message=str(error),
            trace_url=self._build_trace_url(trace_ctx.trace_id),
        )
        
        # 3. 실패 카운트 증가
        self._increment_failure_count(service_id)
        
        # 4. Threshold 체크 및 OPEN
        if self._should_open(service_id):
            self._open_with_tracing(service_id)
    
    def _open_with_tracing(self, service_id: str) -> None:
        """OPEN 전환 + trace_id 포함 Audit 기록"""
        
        triggering_request = self._last_triggering_request.get(service_id)
        
        # Audit 기록 (trace_id 포함)
        self.audit.log_cb_state_change_audit(
            service_id=service_id,
            previous_state="CLOSED",
            new_state="OPEN",
            trigger="AUTO_THRESHOLD",
            # 핵심: 상태 변화를 유발한 마지막 요청의 trace_id
            triggering_trace_id=triggering_request.trace_id if triggering_request else None,
            triggering_request_info={
                "trace_id": triggering_request.trace_id,
                "span_id": triggering_request.span_id,
                "request_id": triggering_request.request_id,
                "endpoint": triggering_request.endpoint,
                "method": triggering_request.method,
                "error_message": triggering_request.error_message,
                "trace_url": triggering_request.trace_url,
            } if triggering_request else None,
        )
        
        # OpenTelemetry Span 생성 (연동 시)
        if self.config.create_spans:
            with self.tracer.start_as_current_span(
                "circuit_breaker.state_change",
                attributes={
                    "cb.service_id": service_id,
                    "cb.previous_state": "CLOSED",
                    "cb.new_state": "OPEN",
                    "cb.triggering_trace_id": triggering_request.trace_id,
                }
            ):
                pass  # Span 기록만
        
        self._set_state(service_id, CBState.OPEN)
```

### 15.4 Audit 기록 형식 (trace_id 포함)

```python
# "서킷이 왜 열렸지?" → trace_id로 1초 만에 Root Cause 추적
{
    "event_type": "CB_STATE_CHANGE",
    "timestamp": "2026-01-05T14:30:00Z",
    "service_id": "payment-api",
    "previous_state": "CLOSED",
    "new_state": "OPEN",
    "trigger": "AUTO_THRESHOLD",
    
    # 핵심: 상태 변화를 유발한 마지막 요청 정보
    "triggering_request": {
        "trace_id": "abc123def456",
        "span_id": "span789",
        "request_id": "req-001",
        "endpoint": "/api/v1/payments",
        "method": "POST",
        "error_message": "Connection timeout to payment gateway",
        "trace_url": "https://jaeger.internal/trace/abc123def456",
        "timestamp": "2026-01-05T14:29:58Z"
    },
    
    # 운영자 안내
    "debug_hint": "위 trace_id로 Jaeger/Zipkin에서 전체 호출 흐름 확인 가능"
}
```

### 15.5 운영 가치

| 상황 | 기존 방식 | Tracing 연동 후 |
|------|----------|----------------|
| "서킷이 왜 열렸지?" | 로그 뒤져서 추측 | trace_id 클릭 → 1초 |
| Root Cause 분석 | 30분~1시간 | 1분 이내 |
| 서비스 간 연쇄 장애 추적 | 불가능 | 전체 호출 그래프 시각화 |
| 실사 대응 | "조사 중입니다" | trace_id로 즉시 증거 제시 |

---

## 16. Canary Recovery + Stale Cache 결합

### 16.1 개념

기존 `should_allow_with_fallback()`에 Canary Recovery를 결합합니다. **HALF_OPEN 상태에서 Canary 비율(10%→30%→60%)의 요청만 백엔드로 보내고**, 나머지 요청은 즉시 **Stale Cache를 반환**합니다.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    HALF_OPEN + Canary + Stale Cache                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  HALF_OPEN (Stage 1: 10%)                                               │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                                                                  │    │
│  │   요청 100개                                                     │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │   ┌──────────┐                                                  │    │
│  │   │ Canary   │                                                  │    │
│  │   │ Selector │                                                  │    │
│  │   └────┬─────┘                                                  │    │
│  │        │                                                         │    │
│  │   ┌────┴────┐                                                   │    │
│  │   │         │                                                   │    │
│  │   ▼         ▼                                                   │    │
│  │ 10개      90개                                                   │    │
│  │ (10%)     (90%)                                                 │    │
│  │   │         │                                                   │    │
│  │   ▼         ▼                                                   │    │
│  │ Backend  Stale Cache                                            │    │
│  │ (실제     (캐시된                                                │    │
│  │  요청)    데이터)                                               │    │
│  │   │         │                                                   │    │
│  │   ▼         ▼                                                   │    │
│  │ 성공/실패  즉시 응답                                             │    │
│  │ 기록       (약간 오래된                                          │    │
│  │            데이터)                                              │    │
│  │                                                                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  결과: 90%의 사용자는 에러 없이 서비스 이용                             │
│        10%의 요청으로 백엔드 안정성 검증                                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 16.2 데이터 모델 확장

```python
@dataclass
class CanaryWithStalePolicy:
    """Canary Recovery + Stale Cache 결합 정책"""
    
    enabled: bool = True
    
    # Stale Cache 설정
    stale_cache_max_age_seconds: int = 300  # 5분까지 허용
    
    # Canary 비율에서 제외된 요청 처리
    non_canary_action: str = "stale_cache"  # "stale_cache" | "reject" | "queue"
    
    # Stale Cache 없을 때 fallback
    stale_cache_miss_action: str = "reject"  # "reject" | "default_value"
    
    # 응답에 Stale 여부 표시
    add_stale_indicator: bool = True
    stale_header_name: str = "X-Stale-Response"


@dataclass
class CanaryStageEnhanced(CanaryStage):
    """Stale Cache 지원이 추가된 Canary Stage"""
    
    # 기존 필드 상속
    traffic_percent: float
    duration_seconds: int
    required_success_rate: float
    description: str = ""
    
    # Stale Cache 연동
    use_stale_for_non_canary: bool = True  # Canary 외 요청은 Stale 사용
    stale_max_age_override: Optional[int] = None  # Stage별 Stale 허용 시간
```

### 16.3 구현

```python
class CanaryRecoveryWithStaleCache:
    """Canary Recovery + Stale-While-Revalidate 결합"""
    
    def __init__(
        self,
        cb_service: CircuitBreakerService,
        cache_service: StaleCacheService,
        policy: CanaryWithStalePolicy,
    ):
        self.cb_service = cb_service
        self.cache = cache_service
        self.policy = policy
    
    def should_allow_with_fallback(
        self, 
        service_id: str,
        cache_key: str,
    ) -> CanaryDecision:
        """
        Canary + Stale Cache 결합 결정
        
        Returns:
            CanaryDecision: 
                - allow_backend: True면 백엔드 호출
                - use_stale: True면 Stale Cache 반환
                - stale_data: 캐시된 데이터 (있으면)
        """
        cb_state = self.cb_service.get_state(service_id)
        
        # 1. CLOSED: 정상 허용
        if cb_state == CBState.CLOSED:
            return CanaryDecision(allow_backend=True, use_stale=False)
        
        # 2. OPEN: 무조건 Stale Cache (기존 로직)
        if cb_state == CBState.OPEN:
            stale_data = self._get_stale_cache(service_id, cache_key)
            return CanaryDecision(
                allow_backend=False, 
                use_stale=True, 
                stale_data=stale_data,
                reason="CB is OPEN - returning stale cache"
            )
        
        # 3. HALF_OPEN (Canary): 비율에 따라 분기
        if cb_state == CBState.HALF_OPEN:
            return self._handle_canary_request(service_id, cache_key)
        
        return CanaryDecision(allow_backend=False, use_stale=False, reject=True)
    
    def _handle_canary_request(
        self, 
        service_id: str, 
        cache_key: str,
    ) -> CanaryDecision:
        """HALF_OPEN 상태에서 Canary 비율 적용"""
        
        current_stage = self.cb_service.get_canary_stage(service_id)
        traffic_percent = current_stage.traffic_percent
        
        # Canary 선택 여부 결정 (예: 10% 확률)
        is_canary = self._select_canary(service_id, traffic_percent)
        
        if is_canary:
            # 운이 좋은 요청: 백엔드로 보내서 상태 검증
            return CanaryDecision(
                allow_backend=True,
                use_stale=False,
                is_canary=True,
                canary_stage=current_stage.description,
            )
        else:
            # Canary 외 요청: Stale Cache 반환 (에러 없음!)
            stale_data = self._get_stale_cache(service_id, cache_key)
            
            return CanaryDecision(
                allow_backend=False,
                use_stale=True,
                stale_data=stale_data,
                is_canary=False,
                reason=f"Canary stage {current_stage.description}: "
                       f"non-canary request served from stale cache",
            )
    
    def _select_canary(self, service_id: str, traffic_percent: float) -> bool:
        """Canary 요청 선택 (확률 기반)"""
        import random
        return random.random() * 100 < traffic_percent
    
    def _get_stale_cache(
        self, 
        service_id: str, 
        cache_key: str,
    ) -> Optional[Any]:
        """Stale Cache 조회"""
        return self.cache.get_stale(
            key=cache_key,
            max_age=self.policy.stale_cache_max_age_seconds,
        )
    
    def wrap_response(
        self, 
        response: Any, 
        decision: CanaryDecision,
    ) -> Any:
        """응답에 Stale 표시 추가"""
        if self.policy.add_stale_indicator and decision.use_stale:
            # HTTP 응답의 경우 헤더 추가
            if hasattr(response, 'headers'):
                response.headers[self.policy.stale_header_name] = "true"
                response.headers["X-Stale-Age"] = str(decision.stale_age_seconds)
        
        return response


@dataclass
class CanaryDecision:
    """Canary + Stale Cache 결정 결과"""
    
    allow_backend: bool = False      # 백엔드 호출 허용
    use_stale: bool = False          # Stale Cache 사용
    stale_data: Optional[Any] = None # 캐시된 데이터
    is_canary: bool = False          # Canary 요청 여부
    canary_stage: Optional[str] = None  # 현재 Canary 단계
    reason: Optional[str] = None     # 결정 사유
    reject: bool = False             # 거부 (Stale도 없음)
    stale_age_seconds: int = 0       # Stale 데이터 나이
```

### 16.4 사용자 경험 비교

| 상황 | 기존 방식 | Canary + Stale 결합 |
|------|----------|---------------------|
| **HALF_OPEN 진입** | 일부 요청 실패 가능 | 90%는 Stale Cache로 정상 응답 |
| **Stage 1 (10%)** | 10% 성공, 90% 불확실 | 10% 검증, 90%는 캐시 데이터 |
| **Stage 2 (30%)** | 30% 성공, 70% 불확실 | 30% 검증, 70%는 캐시 데이터 |
| **사용자 체감 에러** | 최대 90% | 거의 0% (약간 오래된 데이터) |
| **복구 안정성** | 중간 | 높음 (점진적 검증) |

### 16.5 흐름 요약

```
HALF_OPEN (10%) 진입
    │
    ├── 10% Canary 선택됨 ──▶ 백엔드 호출 ──▶ 성공/실패 기록
    │                                            │
    │                                    성공률 95% 이상?
    │                                       Yes │ No
    │                                           │  │
    │                                           │  └─▶ OPEN 복귀
    │                                           ▼
    │                                    Stage 2 (30%) 진행
    │
    └── 90% Non-Canary ──▶ Stale Cache 반환 ──▶ 사용자는 정상 응답 수신
                                                 (약간 오래된 데이터)
```

---

## 17. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-01-05 | 초안 작성 - 전체 설계 완료 |
| 1.1.0 | 2026-01-05 | Section 14-16 추가: Panic Threshold, Distributed Tracing, Canary+Stale 결합 |
| 1.2.0 | 2026-01-05 | Section 13 추가: CB 시스템 자체 장애 대응 (L1/L2 Cache, ResilientStorage, Kill Switch Override) |
| 1.3.0 | 2026-01-05 | **Phase 0 구현 완료**: 데이터 모델 정의, Config 스키마 추가, 테스트 기반 작성 (65개 테스트 통과) |
| 1.4.0 | 2026-01-05 | **Phase 1 구현 완료**: Kill Switch Override 수정, Adaptive Threshold, Freeze Mode, Panic Threshold (32개 테스트 추가, 총 97개 테스트 통과) |
| 1.5.0 | 2026-01-06 | **Phase 4 구현 완료**: Canary Recovery (canary_recovery.py), Stale Cache Integration (stale_cache_integration.py), Recovery Strategy Selector (recovery_strategy.py) (45개 테스트 추가, 총 295개 테스트 통과) |
| 1.6.0 | 2026-01-06 | **Phase 5 구현 완료**: Load Shedding (load_shedding.py) - LoadSheddingManager, Middleware, Dashboard (83개 테스트 추가, 총 385개 테스트 통과) |
