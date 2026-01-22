# 75. Crisis Budget Multiplier (위기 가중치 버짓팅)

> **Version**: 2.4.0  
> **Created**: 2026-01-21  
> **Updated**: 2026-01-22  
> **Status**: Phase 2-A, 2-B Implemented  
> **Parent**: [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md)

## 0. 확장 기능 요약

### 0.1 전체 기능 목록 (총 15개)

| # | 기능명 | 카테고리 | 설명 | 파일 | 상태 |
|---|--------|----------|------|------|------|
| 1 | **EmergencyBackfillCalculator** | 리뷰①: 소급 적용 | 장애 선포 전 에러에 가중치 소급 적용 | `backfill.py` | Phase 2 |
| 2 | **MultiplierSmoother** | 리뷰②: 평활화 | 레벨 전환 시 가중치 점진적 변경 | `smoother.py` | Phase 2 |
| 3 | **DomainPropagationMultiplier** | 리뷰③: 도메인 전파 | 의존성 그래프 기반 가중치 감쇠 전파 | `propagation.py` | Phase 2 |
| 4 | **CanaryMultiplierRollout** | 리뷰④: 카나리 배포 | 가중치 설정값의 안전한 점진 배포 | `canary_multiplier.py` | Phase 2 |
| 5 | **CheckOnUseMultiplierProvider** | Q1: 컨텍스트 획득 | 매 사용 시점에 Emergency Level 조회 | `provider.py` | ✅ Phase 2-A |
| 6 | **@domain_tag 데코레이터** | Q2: 도메인 식별 | 에러 발생 시 도메인 자동 태깅 | `decorators/domain_tag.py` | ✅ Phase 2-A |
| 7 | **AtomicBudgetConsumer** | Q3: 원자성 보장 | Redis Lock 기반 버짓 소진 원자성 | `atomic_consumer.py` | ✅ Phase 2-B |
| 8 | **WeightedBudgetAuditEntry** | Q4: Hash chain 무결성 | 가중치 근거 포함 무결성 로그 스키마 | `weighted_audit.py` | ✅ Phase 2-B |
| 9 | **CRDTBudgetSynchronizer** | Q5: 글로벌 동기화 | 멀티 리전 버짓 상태 동기화 | `crdt_sync.py` | Phase 2 |
| 10 | **AdminOverrideInvalidator** | Q6: Override 충돌 | Admin Override 시 캐시 즉시 무효화 | `admin_invalidator.py` | Phase 2 |
| 11 | **통합 Cap 상수 (SSOT)** | Q7: Cap 통합 | 모든 가중치 Cap의 단일 진실 공급원 | `constants.py` | ✅ Phase 2-A |
| 12 | **BudgetRefundProposalService** | Q8: 오탐 환불 | 오탐 시 초과 소진 버짓 환불 제안 | `refund.py` | Phase 2 |
| 13 | **EscalationTriggeredInvalidation** | 추가: 격상 무효화 | Emergency 격상 시 캐시 푸시 무효화 | `escalation_invalidation.py` | Phase 2 |
| 14 | **MultiplierPrecedenceResolver** | 추가: 충돌 해결 | Level/Domain 가중치 결합 전략 | `precedence.py` | ✅ Phase 2-B |
| 15 | **DomainContext 컨텍스트 매니저** | Q2 보조 | with 문 기반 도메인 컨텍스트 | `decorators/domain_tag.py` | ✅ Phase 2-A |

### 0.2 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── services/
│   └── error_budget/
│       ├── __init__.py
│       ├── constants.py           # 11. 통합 Cap 상수 (SSOT)
│       ├── provider.py            # 5. CheckOnUseMultiplierProvider
│       ├── backfill.py            # 1. EmergencyBackfillCalculator
│       ├── smoother.py            # 2. MultiplierSmoother
│       ├── propagation.py         # 3. DomainPropagationMultiplier
│       ├── canary_multiplier.py   # 4. CanaryMultiplierRollout
│       ├── atomic_consumer.py     # 7. AtomicBudgetConsumer
│       ├── weighted_audit.py      # 8. WeightedBudgetAuditEntry
│       ├── crdt_sync.py           # 9. CRDTBudgetSynchronizer
│       ├── admin_invalidator.py   # 10. AdminOverrideInvalidator
│       ├── refund.py              # 12. BudgetRefundProposalService
│       ├── escalation_invalidation.py  # 13. EscalationTriggeredInvalidation
│       └── precedence.py          # 14. MultiplierPrecedenceResolver
└── decorators/
    └── domain_tag.py              # 6, 15. @domain_tag, DomainContext
```

### 0.3 구현 순서 (의존성 기반)

```
Phase 1: 핵심 기반 ✅ (2026-01-22 구현 완료)
────────────────────────────────────────────
  ┌─────────────────────────────────────────────────────────┐
  │ CrisisMultiplierConfig (§3.1)                           │ ◀── 가중치 설정 ✅
  └─────────────────────────────────────────────────────────┘
                            │
                            ▼
  ┌─────────────────────────────────────────────────────────┐
  │ CrisisMultiplierProvider (§3.2)                         │ ◀── 30초 캐시 + invalidate_cache() ✅
  └─────────────────────────────────────────────────────────┘
                            │
                            ▼
  ┌─────────────────────────────────────────────────────────┐
  │ ErrorBudgetCalculator 수정 (§3.3)                       │ ◀── 가중치 적용 로직 (Phase 2)
  └─────────────────────────────────────────────────────────┘
                            │
                            ▼
  ┌─────────────────────────────────────────────────────────┐
  │ ErrorRecord 모델 확장 (§3.4)                            │ ◀── 가중치 기록 (Phase 2)
  └─────────────────────────────────────────────────────────┘

  구현 파일: services/error_budget/multiplier.py
  테스트 파일: tests/unit/services/test_crisis_multiplier.py (30개 통과)

Phase 2-A: 기반 인프라 (Week 1) ✅ Implemented
────────────────────────────────
  ┌─────────────────────────────────────────────────────────┐
  │ 11. constants.py (SSOT)                                 │ ◀── 모든 컴포넌트가 참조
  └─────────────────────────────────────────────────────────┘
                            │
                            ▼
  ┌─────────────────────────────────────────────────────────┐
  │ 5. provider.py (CheckOnUseMultiplierProvider)           │ ◀── 가중치 조회 기반
  └─────────────────────────────────────────────────────────┘
                            │
                            ▼
  ┌─────────────────────────────────────────────────────────┐
  │ 6. decorators/domain_tag.py (@domain_tag)               │ ◀── 도메인 식별 기반
  └─────────────────────────────────────────────────────────┘

  구현 파일: services/error_budget/constants.py, provider.py, decorators/domain_tag.py
  테스트 파일: tests/unit/services/error_budget/test_constants.py (20개 통과)
              tests/unit/services/error_budget/test_provider.py (16개 통과)
              tests/unit/decorators/test_domain_tag.py (21개 통과)

Phase 2-B: 핵심 로직 (Week 2) ✅ Implemented
────────────────────────────
  ┌─────────────────────────┐   ┌─────────────────────────┐
  │ 14. precedence.py       │   │ 7. atomic_consumer.py   │
  │ (MultiplierPrecedence)  │   │ (AtomicBudgetConsumer)  │
  └─────────────────────────┘   └─────────────────────────┘
           │                              │
           └──────────────┬───────────────┘
                          ▼
  ┌─────────────────────────────────────────────────────────┐
  │ 8. weighted_audit.py (WeightedBudgetAuditEntry)         │ ◀── Hash Chain 통합
  └─────────────────────────────────────────────────────────┘

  구현 파일: services/error_budget/precedence.py, atomic_consumer.py, weighted_audit.py
  테스트 파일: tests/unit/services/error_budget/test_precedence.py (26개 통과)
              tests/unit/services/error_budget/test_atomic_consumer.py (14개 통과)
              tests/unit/services/error_budget/test_weighted_audit.py (18개 통과)

Phase 2-C: 고급 기능 (Week 3)
────────────────────────────
  ┌───────────────────────┐   ┌───────────────────────┐   ┌───────────────────────┐
  │ 1. backfill.py        │   │ 2. smoother.py        │   │ 3. propagation.py     │
  │ (Backfill)            │   │ (Smoother)            │   │ (Propagation)         │
  └───────────────────────┘   └───────────────────────┘   └───────────────────────┘
                                                                     │
                                                          ┌──────────┴──────────┐
                                                          │ max_hops=3 (순환방지)│
                                                          │ visited Set 사용    │
                                                          └─────────────────────┘

Phase 2-D: 운영 기능 (Week 4)
────────────────────────────
  ┌───────────────────────────────────────────────────────────────────────────┐
  │ 13. escalation_invalidation.py (EscalationTriggeredInvalidation)          │
  │     - CrisisMultiplierProvider.invalidate_cache() 연동                    │
  │     - 이벤트 버스: EMERGENCY_LEVEL_CHANGED 구독                            │
  │     - 격상(Escalation) 시에만 Push 무효화 (하강은 TTL 대기)               │
  └───────────────────────────────────────────────────────────────────────────┘
                          │
  ┌───────────────────────┼───────────────────────┐
  ▼                       ▼                       ▼
  ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
  │ 4. canary_mult    │   │ 10. admin_inval   │   │ 9. crdt_sync.py   │
  │ (CanaryRollout)   │   │ (AdminOverride)   │   │ (CRDTSync)        │
  └───────────────────┘   └───────────────────┘   └───────────────────┘
                          │
                          ▼
  ┌─────────────────────────────────────────────────────────┐
  │ 12. refund.py (BudgetRefundProposalService)             │ ◀── 최종 (Admin UI 연동)
  └─────────────────────────────────────────────────────────┘
```

### 0.4 리뷰 반영 현황

| 리뷰 | 내용 | 구현 상태 | 코드 근거 |
|------|------|----------|----------|
| §3.1 Push-based Invalidation | 격상 시 30초 캐시 대기 없이 즉시 무효화 | ✅ 구현됨 (§8.5) | `tracker.py#L446` invalidate_cache(), `EscalationTriggeredInvalidation` |
| §3.2.3 Depth Limit | 순환 참조 방지를 위한 깊이 제한 | ✅ 구현됨 (§8.3) | `propagation.py` max_hops=3, visited Set 사용 |

**추가 보완 사항:**
- §3.1: `CrisisMultiplierProvider`를 `EscalationTriggeredInvalidation`에 등록하는 연동 코드 추가 필요
- §3.2.3: 순환 참조 감지 시 메트릭 `domain_propagation_cycle_detected_total` 추가 권장

## 1. 개요

### 1.1 문제점 (AS-IS)

현재 Error Budget 계산은 Emergency Level을 **무시**합니다:

```python
# 현재 구현 - error_budget/calculator.py
def calculate_budget_status(self) -> BudgetStatus:
    # Emergency Level과 관계없이 동일한 가중치
    consumed = self._get_consumed_budget()
    remaining = self.total_budget - consumed
    ...
```

**문제**:
- LEVEL_3 상황의 에러와 평상시 에러가 **동일한 가치**로 취급됨
- 시스템이 한계에 도달한 시점의 에러는 비즈니스에 **훨씬 더 치명적**
- 버짓 소진율이 위기 상황을 제대로 반영하지 못함

### 1.2 해결책 (TO-BE)

**Crisis Multiplier** 도입:

| Emergency Level | Multiplier | 의미 |
|----------------|------------|------|
| NORMAL | 1.0x | 기본 소진율 |
| LEVEL_1 | 1.5x | 경미한 위기 |
| LEVEL_2 | 3.0x | 중간 위기 |
| LEVEL_3 | 5.0x | 심각한 위기 |

**예시**:
- 평상시 에러 1건 = 버짓 1분 소진
- LEVEL_3 에러 1건 = 버짓 **5분** 소진

---

## 2. 아키텍처

### 2.1 Multiplier 적용 흐름

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Crisis Multiplier Flow                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  Error Budget Calculator                      │  │
│  │                                                               │  │
│  │  record_error()  ──▶  get_multiplier()  ──▶  apply_weighted  │  │
│  │                                                               │  │
│  └───────────────────────────┬──────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              CrisisMultiplierProvider (신규)                  │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐ │  │
│  │  │ get_current_multiplier() -> float                       │ │  │
│  │  │                                                          │ │  │
│  │  │ 1. Get current EmergencyLevel                           │ │  │
│  │  │ 2. Lookup multiplier from config                        │ │  │
│  │  │ 3. Return multiplier value                              │ │  │
│  │  └─────────────────────────────────────────────────────────┘ │  │
│  │                                                               │  │
│  └───────────────────────────┬──────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Multiplier Configuration                         │  │
│  │                                                               │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │  │
│  │  │ NORMAL  │  │ LEVEL_1 │  │ LEVEL_2 │  │ LEVEL_3 │        │  │
│  │  │         │  │         │  │         │  │         │        │  │
│  │  │  1.0x   │  │  1.5x   │  │  3.0x   │  │  5.0x   │        │  │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │  │
│  │                                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 버짓 소진 계산

```
                    기존 방식                      새로운 방식
                    ─────────                      ──────────
                    
    에러 발생        ───▶  1분 소진               에러 발생 ─┬─▶ NORMAL:  1분 × 1.0 = 1분
                                                             ├─▶ LEVEL_1: 1분 × 1.5 = 1.5분
                                                             ├─▶ LEVEL_2: 1분 × 3.0 = 3분
                                                             └─▶ LEVEL_3: 1분 × 5.0 = 5분

    ┌─────────────────────────────────────────────────────────────────┐
    │                    Budget Consumption Timeline                   │
    ├─────────────────────────────────────────────────────────────────┤
    │                                                                  │
    │  100% ┤                                                         │
    │       │  ╭────╮ Normal consumption                              │
    │   75% ┤  │    ╲                                                 │
    │       │  │     ╲    ╭── LEVEL_3 spike (5x)                     │
    │   50% ┤  │      ╲   │                                           │
    │       │  │       ╲──╯                                           │
    │   25% ┤  │           ╲                                          │
    │       │  │            ╲                                         │
    │    0% ┼──┴─────────────┴────────────────────────────────────▶  │
    │       0h              4h              8h              12h       │
    │                                                                  │
    │  Legend: ─── Normal consumption                                 │
    │          ─── Crisis multiplied consumption                      │
    │                                                                  │
    └─────────────────────────────────────────────────────────────────┘
```

---

## 3. 구현 상세

### 3.1 CrisisMultiplierConfig

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/multiplier.py

from dataclasses import dataclass, field
from typing import Dict, Optional
from selfhealing.services.emergency_mode.enums import EmergencyLevel


@dataclass
class CrisisMultiplierConfig:
    """
    위기 가중치 설정.
    
    Emergency Level별 Error Budget 소진 가중치를 정의합니다.
    
    Attributes:
        multipliers: Level별 가중치 매핑
        enabled: 기능 활성화 여부
        max_multiplier: 최대 허용 가중치 (과도한 소진 방지)
    """
    
    multipliers: Dict[EmergencyLevel, float] = field(default_factory=lambda: {
        EmergencyLevel.NORMAL: 1.0,
        EmergencyLevel.LEVEL_1: 1.5,
        EmergencyLevel.LEVEL_2: 3.0,
        EmergencyLevel.LEVEL_3: 5.0,
    })
    
    enabled: bool = True
    """Crisis Multiplier 활성화 여부."""
    
    max_multiplier: float = 10.0
    """최대 허용 가중치 (안전 제한)."""
    
    def get_multiplier(self, level: EmergencyLevel) -> float:
        """
        레벨에 해당하는 가중치 반환.
        
        Args:
            level: Emergency 레벨
        
        Returns:
            가중치 값 (기본 1.0)
        """
        if not self.enabled:
            return 1.0
        
        multiplier = self.multipliers.get(level, 1.0)
        return min(multiplier, self.max_multiplier)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "CrisisMultiplierConfig":
        """딕셔너리에서 생성."""
        multipliers = {}
        for level_name, value in data.get("multipliers", {}).items():
            try:
                level = EmergencyLevel[level_name]
                multipliers[level] = float(value)
            except (KeyError, ValueError):
                continue
        
        return cls(
            multipliers=multipliers or cls().multipliers,
            enabled=data.get("enabled", True),
            max_multiplier=data.get("max_multiplier", 10.0),
        )
```

### 3.2 CrisisMultiplierProvider

```python
class CrisisMultiplierProvider:
    """
    위기 가중치 제공자.
    
    현재 Emergency Level에 따른 Error Budget 소진 가중치를 제공합니다.
    
    Features:
    - Emergency Level 기반 가중치 조회
    - 설정 가능한 가중치
    - TTL 캐시 (Check on Use 패턴)
    
    Reference:
    - docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md
    """
    
    def __init__(
        self,
        config: Optional[CrisisMultiplierConfig] = None,
    ):
        """
        CrisisMultiplierProvider 초기화.
        
        Args:
            config: 가중치 설정 (None이면 기본값 사용)
        """
        self.config = config or CrisisMultiplierConfig()
        self._emergency_tracker = None
        self._cache_ttl = 30.0  # 30초 캐시
        self._cached_multiplier: Optional[float] = None
        self._cache_timestamp: float = 0
    
    def _get_emergency_tracker(self):
        """EmergencyTracker 획득 (lazy)."""
        if self._emergency_tracker is None:
            from selfhealing.services.governance import get_namespaced_emergency_tracker
            self._emergency_tracker = get_namespaced_emergency_tracker()
        return self._emergency_tracker
    
    def get_current_multiplier(
        self,
        namespace: Optional[str] = None,
    ) -> float:
        """
        현재 Crisis Multiplier 조회.
        
        Args:
            namespace: 대상 네임스페이스 (None이면 현재 인스턴스)
        
        Returns:
            현재 가중치 값
        """
        import time
        
        # 캐시 확인
        now = time.time()
        if (
            self._cached_multiplier is not None
            and now - self._cache_timestamp < self._cache_ttl
        ):
            return self._cached_multiplier
        
        # Emergency Level 조회
        tracker = self._get_emergency_tracker()
        state = tracker.get_effective_state(namespace=namespace)
        level = state.emergency_level
        
        # 가중치 조회
        multiplier = self.config.get_multiplier(level)
        
        # 캐시 저장
        self._cached_multiplier = multiplier
        self._cache_timestamp = now
        
        return multiplier
    
    def invalidate_cache(self) -> None:
        """캐시 무효화 (이벤트 버스 연동용)."""
        self._cached_multiplier = None
        self._cache_timestamp = 0
    
    def set_multiplier_override(
        self,
        level: EmergencyLevel,
        multiplier: float,
    ) -> None:
        """
        특정 레벨의 가중치 오버라이드 (런타임 설정).
        
        Args:
            level: 대상 레벨
            multiplier: 새 가중치
        """
        self.config.multipliers[level] = min(
            multiplier, 
            self.config.max_multiplier
        )
        self.invalidate_cache()
        
        logger.info(
            f"[CrisisMultiplier] Override set: "
            f"level={level.name}, multiplier={multiplier}"
        )


# =============================================================================
# Singleton
# =============================================================================

_multiplier_provider: Optional[CrisisMultiplierProvider] = None


def get_crisis_multiplier_provider() -> CrisisMultiplierProvider:
    """CrisisMultiplierProvider 싱글톤 반환."""
    global _multiplier_provider
    if _multiplier_provider is None:
        _multiplier_provider = CrisisMultiplierProvider()
    return _multiplier_provider
```

### 3.3 ErrorBudgetCalculator 수정

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/calculator.py

class ErrorBudgetCalculator:
    """Error Budget 계산기."""
    
    def __init__(
        self,
        slo_config: Optional[SLOConfig] = None,
        crisis_multiplier_provider: Optional[CrisisMultiplierProvider] = None,
        enable_crisis_multiplier: bool = True,
    ):
        self.slo_config = slo_config or get_default_slo_config()
        self.crisis_multiplier_provider = (
            crisis_multiplier_provider or get_crisis_multiplier_provider()
        )
        self.enable_crisis_multiplier = enable_crisis_multiplier
    
    def record_error(
        self,
        error_type: str,
        service_name: str,
        duration_minutes: float = 1.0,
        namespace: Optional[str] = None,
    ) -> ErrorRecord:
        """
        에러 기록 및 버짓 소진.
        
        Crisis Multiplier가 적용된 가중치로 버짓을 소진합니다.
        
        Args:
            error_type: 에러 유형
            service_name: 서비스 이름
            duration_minutes: 에러 지속 시간 (분)
            namespace: 네임스페이스
        
        Returns:
            ErrorRecord: 기록된 에러 정보
        """
        # Crisis Multiplier 조회
        multiplier = 1.0
        if self.enable_crisis_multiplier:
            multiplier = self.crisis_multiplier_provider.get_current_multiplier(
                namespace=namespace
            )
        
        # 가중치 적용된 소진량 계산
        weighted_consumption = duration_minutes * multiplier
        
        # 에러 기록
        record = ErrorRecord(
            error_type=error_type,
            service_name=service_name,
            raw_duration_minutes=duration_minutes,
            weighted_duration_minutes=weighted_consumption,
            multiplier_applied=multiplier,
            recorded_at=utc_now(),
            namespace=namespace,
        )
        
        # 저장 및 버짓 소진
        self._save_error_record(record)
        self._consume_budget(weighted_consumption)
        
        # 로깅
        if multiplier > 1.0:
            logger.warning(
                f"[ErrorBudget] Crisis multiplier applied: "
                f"raw={duration_minutes}min, weighted={weighted_consumption}min, "
                f"multiplier={multiplier}x"
            )
        
        return record
    
    def calculate_budget_status(
        self,
        namespace: Optional[str] = None,
    ) -> BudgetStatus:
        """
        현재 버짓 상태 계산.
        
        현재 Crisis Multiplier도 포함하여 반환합니다.
        """
        consumed = self._get_consumed_budget()
        remaining = max(0, self.total_budget - consumed)
        remaining_percent = (remaining / self.total_budget) * 100
        
        # 현재 Crisis Multiplier
        current_multiplier = 1.0
        if self.enable_crisis_multiplier:
            current_multiplier = self.crisis_multiplier_provider.get_current_multiplier(
                namespace=namespace
            )
        
        return BudgetStatus(
            budget_total_minutes=self.total_budget,
            budget_consumed_minutes=consumed,
            budget_remaining_minutes=remaining,
            budget_remaining_percent=remaining_percent,
            current_crisis_multiplier=current_multiplier,
            verdict=self._get_verdict(remaining_percent),
        )
```

### 3.4 ErrorRecord 모델 확장

```python
@dataclass
class ErrorRecord:
    """에러 기록."""
    
    error_type: str
    """에러 유형."""
    
    service_name: str
    """서비스 이름."""
    
    raw_duration_minutes: float
    """원시 지속 시간 (분)."""
    
    weighted_duration_minutes: float
    """가중치 적용된 소진량 (분)."""
    
    multiplier_applied: float
    """적용된 Crisis Multiplier."""
    
    recorded_at: datetime
    """기록 시각."""
    
    namespace: Optional[str] = None
    """네임스페이스."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "error_type": self.error_type,
            "service_name": self.service_name,
            "raw_duration_minutes": self.raw_duration_minutes,
            "weighted_duration_minutes": self.weighted_duration_minutes,
            "multiplier_applied": self.multiplier_applied,
            "recorded_at": self.recorded_at.isoformat(),
            "namespace": self.namespace,
        }


@dataclass
class BudgetStatus:
    """버짓 상태."""
    
    budget_total_minutes: float
    """총 버짓 (분)."""
    
    budget_consumed_minutes: float
    """소진된 버짓 (분)."""
    
    budget_remaining_minutes: float
    """남은 버짓 (분)."""
    
    budget_remaining_percent: float
    """남은 버짓 비율 (%)."""
    
    current_crisis_multiplier: float = 1.0
    """현재 적용 중인 Crisis Multiplier."""
    
    verdict: str = "healthy"
    """상태 판정 (healthy, caution, warning, freeze)."""
```

---

## 4. 설정

### 4.1 crisis_multiplier.yaml

```yaml
# Crisis Multiplier Configuration
# Reference: docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md

version: "1.0"

# 기본 설정
defaults:
  enabled: true
  max_multiplier: 10.0  # 최대 10배까지 허용

# Emergency Level별 가중치
multipliers:
  NORMAL: 1.0      # 기본값
  LEVEL_1: 1.5     # 경미한 위기: 1.5배
  LEVEL_2: 3.0     # 중간 위기: 3배
  LEVEL_3: 5.0     # 심각한 위기: 5배

# 가중치 적용 옵션
options:
  # 가중치 적용 시 로깅
  log_on_multiplier_applied: true
  log_threshold: 1.5  # 이 값 이상일 때만 로깅
  
  # 알림 설정
  notification:
    enabled: true
    # 가중치가 이 값 이상이면 알림
    notify_threshold: 3.0
    channels: ["slack"]

# 버짓 소진율 경고 임계값 (가중치 적용 후)
budget_thresholds:
  warning: 50.0    # 50% 이하
  critical: 20.0   # 20% 이하
  freeze: 10.0     # 10% 이하 → 자동화 중단
```

---

## 5. 테스트

### 5.1 단위 테스트

```python
class TestCrisisMultiplierProvider:
    """CrisisMultiplierProvider 단위 테스트."""
    
    def test_normal_level_returns_1x(self):
        """NORMAL 레벨에서 1.0x 반환."""
        provider = CrisisMultiplierProvider()
        
        with patch.object(
            provider, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.NORMAL,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            multiplier = provider.get_current_multiplier()
            
            assert multiplier == 1.0
    
    def test_level_3_returns_5x(self):
        """LEVEL_3에서 5.0x 반환."""
        provider = CrisisMultiplierProvider()
        
        with patch.object(
            provider, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_3,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            multiplier = provider.get_current_multiplier()
            
            assert multiplier == 5.0
    
    def test_custom_multipliers(self):
        """커스텀 가중치 설정."""
        config = CrisisMultiplierConfig(
            multipliers={
                EmergencyLevel.NORMAL: 1.0,
                EmergencyLevel.LEVEL_3: 10.0,
            }
        )
        provider = CrisisMultiplierProvider(config=config)
        
        with patch.object(
            provider, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_3,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            multiplier = provider.get_current_multiplier()
            
            assert multiplier == 10.0
    
    def test_max_multiplier_cap(self):
        """최대 가중치 제한."""
        config = CrisisMultiplierConfig(
            multipliers={EmergencyLevel.LEVEL_3: 100.0},
            max_multiplier=10.0,
        )
        provider = CrisisMultiplierProvider(config=config)
        
        with patch.object(
            provider, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_3,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            multiplier = provider.get_current_multiplier()
            
            # 100.0 요청했지만 max 10.0으로 제한
            assert multiplier == 10.0


class TestErrorBudgetCalculatorWithMultiplier:
    """Crisis Multiplier 적용된 ErrorBudgetCalculator 테스트."""
    
    def test_record_error_with_multiplier(self):
        """가중치 적용된 에러 기록."""
        mock_provider = MagicMock()
        mock_provider.get_current_multiplier.return_value = 5.0
        
        calculator = ErrorBudgetCalculator(
            crisis_multiplier_provider=mock_provider,
        )
        
        record = calculator.record_error(
            error_type="timeout",
            service_name="api",
            duration_minutes=1.0,
        )
        
        assert record.raw_duration_minutes == 1.0
        assert record.weighted_duration_minutes == 5.0
        assert record.multiplier_applied == 5.0
    
    def test_budget_status_includes_multiplier(self):
        """버짓 상태에 현재 가중치 포함."""
        mock_provider = MagicMock()
        mock_provider.get_current_multiplier.return_value = 3.0
        
        calculator = ErrorBudgetCalculator(
            crisis_multiplier_provider=mock_provider,
        )
        
        status = calculator.calculate_budget_status()
        
        assert status.current_crisis_multiplier == 3.0
```

### 5.2 통합 테스트

```python
class TestCrisisMultiplierIntegration:
    """Crisis Multiplier 통합 테스트."""
    
    def test_level_3_accelerates_budget_consumption(
        self, 
        error_budget_service,
        emergency_manager,
    ):
        """LEVEL_3에서 버짓 소진 가속화."""
        # Given: 초기 버짓 상태
        initial_status = error_budget_service.calculate_budget_status()
        initial_remaining = initial_status.budget_remaining_minutes
        
        # When: LEVEL_3 상황에서 에러 발생
        emergency_manager.activate_manual(
            level=EmergencyLevel.LEVEL_3,
            reason="Test",
        )
        
        error_budget_service.record_error(
            error_type="timeout",
            service_name="api",
            duration_minutes=1.0,
        )
        
        # Then: 5분이 소진됨 (1분 × 5배)
        new_status = error_budget_service.calculate_budget_status()
        consumed = initial_remaining - new_status.budget_remaining_minutes
        
        assert consumed == 5.0  # 1분 × 5배
        assert new_status.current_crisis_multiplier == 5.0
```

### 5.3 Phase 2 테스트 목록

각 Phase 2 컴포넌트에 대한 테스트 파일 구조:

```
packages/selfhealing-python/tests/unit/services/error_budget/
├── __init__.py
├── test_constants.py              # 11. 통합 Cap 상수
├── test_provider.py               # 5. CheckOnUseMultiplierProvider
├── test_backfill.py               # 1. EmergencyBackfillCalculator
├── test_smoother.py               # 2. MultiplierSmoother
├── test_propagation.py            # 3. DomainPropagationMultiplier
├── test_canary_multiplier.py      # 4. CanaryMultiplierRollout
├── test_atomic_consumer.py        # 7. AtomicBudgetConsumer
├── test_weighted_audit.py         # 8. WeightedBudgetAuditEntry
├── test_crdt_sync.py              # 9. CRDTBudgetSynchronizer
├── test_admin_invalidator.py      # 10. AdminOverrideInvalidator
├── test_refund.py                 # 12. BudgetRefundProposalService
├── test_escalation_invalidation.py # 13. EscalationTriggeredInvalidation
└── test_precedence.py             # 14. MultiplierPrecedenceResolver

packages/selfhealing-python/tests/unit/decorators/
└── test_domain_tag.py             # 6, 15. @domain_tag, DomainContext
```

#### 5.3.1 Phase 2-A 테스트 (기반 인프라)

```python
# tests/unit/services/error_budget/test_constants.py
class TestBudgetConstants:
    """통합 Cap 상수 테스트."""
    
    def test_max_crisis_multiplier_cap_value(self):
        """MAX_CRISIS_MULTIPLIER_CAP = 10.0 확인."""
        from selfhealing.services.error_budget.constants import (
            MAX_CRISIS_MULTIPLIER_CAP,
        )
        assert MAX_CRISIS_MULTIPLIER_CAP == 10.0
    
    def test_max_domain_multiplier_value(self):
        """MAX_DOMAIN_MULTIPLIER = 24.0 확인."""
        from selfhealing.services.error_budget.constants import (
            MAX_DOMAIN_MULTIPLIER,
        )
        assert MAX_DOMAIN_MULTIPLIER == 24.0


# tests/unit/services/error_budget/test_provider.py
class TestCheckOnUseMultiplierProvider:
    """CheckOnUseMultiplierProvider 테스트."""
    
    def test_returns_multiplier_context(self):
        """MultiplierContext 반환 확인."""
        provider = CheckOnUseMultiplierProvider()
        ctx = provider.get_current_multiplier(namespace="test")
        
        assert isinstance(ctx, MultiplierContext)
        assert ctx.level_multiplier >= 1.0
    
    def test_realtime_level_lookup(self, mock_emergency_tracker):
        """매 호출마다 실시간 조회 확인."""
        mock_emergency_tracker.get_level.side_effect = [
            EmergencyLevel.NORMAL,
            EmergencyLevel.LEVEL_3,
        ]
        
        provider = CheckOnUseMultiplierProvider(
            emergency_tracker=mock_emergency_tracker
        )
        
        ctx1 = provider.get_current_multiplier()
        ctx2 = provider.get_current_multiplier()
        
        assert ctx1.level_multiplier == 1.0
        assert ctx2.level_multiplier == 5.0


# tests/unit/decorators/test_domain_tag.py
class TestDomainTag:
    """@domain_tag 데코레이터 테스트."""
    
    def test_sets_domain_context(self):
        """함수 실행 중 도메인 컨텍스트 설정 확인."""
        from selfhealing.decorators.domain_tag import (
            domain_tag, get_current_domain,
        )
        
        @domain_tag("payment")
        def process_payment():
            return get_current_domain()
        
        assert process_payment() == "payment"
    
    def test_context_cleared_after_function(self):
        """함수 종료 후 컨텍스트 해제 확인."""
        from selfhealing.decorators.domain_tag import (
            domain_tag, get_current_domain,
        )
        
        @domain_tag("payment")
        def process_payment():
            pass
        
        process_payment()
        assert get_current_domain() is None
    
    def test_domain_context_manager(self):
        """DomainContext with 문 테스트."""
        from selfhealing.decorators.domain_tag import (
            DomainContext, get_current_domain,
        )
        
        with DomainContext("order"):
            assert get_current_domain() == "order"
        
        assert get_current_domain() is None
```

#### 5.3.2 Phase 2-B 테스트 (핵심 로직)

```python
# tests/unit/services/error_budget/test_precedence.py
class TestMultiplierPrecedenceResolver:
    """MultiplierPrecedenceResolver 테스트."""
    
    def test_max_strategy(self):
        """MAX 전략: 더 큰 값 선택."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.MAX
            )
        )
        
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)
        assert result == 5.0
    
    def test_cap_applied(self):
        """Cap 초과 시 제한."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.MULTIPLY,
                max_combined_multiplier=10.0,
            )
        )
        
        # 3.0 * 5.0 = 15.0 → 10.0으로 제한
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)
        assert result == 10.0


# tests/unit/services/error_budget/test_atomic_consumer.py
class TestAtomicBudgetConsumer:
    """AtomicBudgetConsumer 테스트."""
    
    def test_acquire_and_release_lock(self, mock_redis):
        """Lock 획득 및 해제."""
        consumer = AtomicBudgetConsumer(redis_client=mock_redis)
        
        result = consumer.consume_atomic(
            namespace="test",
            raw_minutes=1.0,
            multiplier=5.0,
            budget_key="budget:test",
        )
        
        assert result.success is True
        assert result.lock_acquired is True
        assert result.consumed_minutes == 5.0
    
    def test_degraded_mode_without_lock(self, failing_redis):
        """Lock 실패 시 degraded mode."""
        consumer = AtomicBudgetConsumer(
            redis_client=failing_redis,
            allow_degraded_mode=True,
        )
        
        result = consumer.consume_atomic(
            namespace="test",
            raw_minutes=1.0,
            multiplier=5.0,
            budget_key="budget:test",
        )
        
        assert result.lock_acquired is False
        # degraded mode에서도 성공 가능


# tests/unit/services/error_budget/test_weighted_audit.py
class TestWeightedBudgetAuditEntry:
    """WeightedBudgetAuditEntry 테스트."""
    
    def test_to_dict(self):
        """딕셔너리 변환."""
        entry = WeightedBudgetAuditEntry(
            raw_consumption_minutes=1.0,
            weighted_consumption_minutes=5.0,
            level_multiplier=5.0,
            emergency_id="emg_123",
        )
        
        data = entry.to_dict()
        
        assert data["raw_consumption_minutes"] == 1.0
        assert data["weighted_consumption_minutes"] == 5.0
        assert data["emergency_id"] == "emg_123"
    
    def test_to_hash_chain_entry(self):
        """Hash Chain 엔트리 변환."""
        entry = WeightedBudgetAuditEntry(
            raw_consumption_minutes=1.0,
            weighted_consumption_minutes=5.0,
            level_multiplier=5.0,
        )
        
        chain_entry = entry.to_hash_chain_entry()
        
        assert chain_entry["type"] == "weighted_budget_consumption"
        assert "multipliers" in chain_entry
```

#### 5.3.3 Phase 2-C 테스트 (고급 기능)

```python
# tests/unit/services/error_budget/test_backfill.py
class TestEmergencyBackfillCalculator:
    """EmergencyBackfillCalculator 테스트."""
    
    def test_estimate_incident_start(self):
        """장애 시작 시점 추정."""
        calculator = EmergencyBackfillCalculator()
        
        declared_at = datetime(2026, 1, 22, 10, 0, 0)
        estimated = calculator.estimate_incident_start(declared_at)
        
        # 기본: 30분 전
        expected = declared_at - timedelta(minutes=30)
        assert estimated == expected
    
    def test_calculate_backfill(self, mock_error_records):
        """소급 계산."""
        calculator = EmergencyBackfillCalculator(
            get_error_records=lambda **kw: mock_error_records,
        )
        
        result = calculator.calculate_backfill(
            emergency_id="emg_123",
            declared_at=datetime(2026, 1, 22, 10, 0, 0),
            target_level=EmergencyLevel.LEVEL_3,
        )
        
        assert result.errors_affected > 0
        assert result.adjustment_delta_minutes > 0


# tests/unit/services/error_budget/test_smoother.py
class TestMultiplierSmoother:
    """MultiplierSmoother 테스트."""
    
    def test_initial_value(self):
        """초기값 1.0."""
        smoother = MultiplierSmoother()
        assert smoother.get_current_value() == 1.0
    
    def test_gradual_increase(self):
        """점진적 증가."""
        smoother = MultiplierSmoother(
            config=MultiplierSmootherConfig(smoothing_factor=0.5)
        )
        
        smoother.set_target(5.0)
        
        # 첫 번째 샘플링
        value1 = smoother.get_smoothed_value()
        assert 1.0 < value1 < 5.0
        
        # 목표값에 점진적으로 접근
        assert smoother.is_transitioning()
    
    def test_disabled_returns_target_immediately(self):
        """비활성화 시 즉시 목표값 반환."""
        smoother = MultiplierSmoother(
            config=MultiplierSmootherConfig(enabled=False)
        )
        
        smoother.set_target(5.0)
        assert smoother.get_smoothed_value() == 5.0


# tests/unit/services/error_budget/test_propagation.py
class TestDomainPropagationMultiplier:
    """DomainPropagationMultiplier 테스트."""
    
    def test_same_domain_full_multiplier(self):
        """동일 도메인: 전체 가중치."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={"order": ["payment"]}
        )
        
        multiplier = propagator.get_multiplier(
            crisis_domain="payment",
            error_domain="payment",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        assert multiplier == 5.0  # base_multiplier
    
    def test_1_hop_decayed_multiplier(self):
        """1-hop: 50% 감쇠."""
        propagator = DomainPropagationMultiplier(
            config=PropagationConfig(base_multiplier=5.0, decay_per_hop=0.5),
            dependency_graph={"order": ["payment"]}
        )
        
        multiplier = propagator.get_multiplier(
            crisis_domain="payment",
            error_domain="order",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        assert multiplier == 2.5  # 5.0 * 0.5
    
    def test_max_hops_limit(self):
        """max_hops 제한."""
        propagator = DomainPropagationMultiplier(
            config=PropagationConfig(max_hops=2),
            dependency_graph={
                "b": ["a"],
                "c": ["b"],
                "d": ["c"],  # 3-hop
            }
        )
        
        hop = propagator.get_hop_distance("a", "d")
        assert hop == -1  # max_hops=2 초과로 연결 없음
    
    def test_cycle_prevention(self):
        """순환 참조 방지 (visited Set)."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={
                "a": ["b"],
                "b": ["c"],
                "c": ["a"],  # 순환!
            }
        )
        
        # 무한 루프 없이 완료되어야 함
        hop = propagator.get_hop_distance("a", "x")
        assert hop == -1
```

#### 5.3.4 Phase 2-D 테스트 (운영 기능)

```python
# tests/unit/services/error_budget/test_escalation_invalidation.py
class TestEscalationTriggeredInvalidation:
    """EscalationTriggeredInvalidation 테스트."""
    
    def test_escalation_triggers_invalidation(self):
        """격상 시 무효화 호출."""
        invalidation = EscalationTriggeredInvalidation()
        mock_invalidate = MagicMock()
        invalidation.register_target(mock_invalidate)
        
        # Escalation 이벤트 시뮬레이션
        event = MagicMock()
        event.data = {"old_level": "LEVEL_1", "new_level": "LEVEL_3"}
        
        invalidation._on_level_changed(event)
        
        mock_invalidate.assert_called_once()
    
    def test_deescalation_does_not_trigger(self):
        """하강 시 무효화 미호출."""
        invalidation = EscalationTriggeredInvalidation()
        mock_invalidate = MagicMock()
        invalidation.register_target(mock_invalidate)
        
        # De-escalation 이벤트
        event = MagicMock()
        event.data = {"old_level": "LEVEL_3", "new_level": "LEVEL_1"}
        
        invalidation._on_level_changed(event)
        
        mock_invalidate.assert_not_called()


# tests/unit/services/error_budget/test_crdt_sync.py
class TestCRDTBudgetSynchronizer:
    """CRDTBudgetSynchronizer 테스트."""
    
    def test_local_consumption_recorded(self, mock_redis):
        """로컬 소진 기록."""
        sync = CRDTBudgetSynchronizer(
            redis_client=mock_redis,
            current_region="seoul",
        )
        
        total = sync.record_local_consumption("test", 10.0)
        
        assert total == 10.0
    
    def test_g_counter_merge(self):
        """G-Counter 병합."""
        state1 = GCounterState(counters={"seoul": 10.0})
        state2 = GCounterState(counters={"tokyo": 20.0})
        
        merged = state1.merge(state2)
        
        assert merged.get_total() == 30.0
        assert merged.counters["seoul"] == 10.0
        assert merged.counters["tokyo"] == 20.0
    
    def test_g_counter_merge_max(self):
        """G-Counter 병합 시 max 사용."""
        state1 = GCounterState(counters={"seoul": 10.0, "tokyo": 15.0})
        state2 = GCounterState(counters={"seoul": 12.0, "tokyo": 5.0})
        
        merged = state1.merge(state2)
        
        assert merged.counters["seoul"] == 12.0  # max(10, 12)
        assert merged.counters["tokyo"] == 15.0  # max(15, 5)


# tests/unit/services/error_budget/test_refund.py
class TestBudgetRefundProposalService:
    """BudgetRefundProposalService 테스트."""
    
    def test_create_proposal(self):
        """환불 제안 생성."""
        service = BudgetRefundProposalService()
        
        proposal = service.create_proposal(
            emergency_id="emg_123",
            false_positive_start=datetime(2026, 1, 22, 9, 0),
            false_positive_end=datetime(2026, 1, 22, 10, 0),
        )
        
        assert proposal.status == RefundStatus.PROPOSED
        assert proposal.refund_ratio == 0.5  # 기본 50%
    
    def test_approve_proposal(self):
        """환불 제안 승인."""
        service = BudgetRefundProposalService()
        proposal = service.create_proposal(
            emergency_id="emg_123",
            false_positive_start=datetime(2026, 1, 22, 9, 0),
            false_positive_end=datetime(2026, 1, 22, 10, 0),
        )
        
        approved = service.approve(
            proposal_id=proposal.proposal_id,
            approved_by="admin@example.com",
        )
        
        assert approved.status == RefundStatus.APPROVED
        assert approved.approved_by == "admin@example.com"
    
    def test_proposal_expires(self):
        """24시간 후 만료."""
        service = BudgetRefundProposalService()
        proposal = service.create_proposal(
            emergency_id="emg_123",
            false_positive_start=datetime(2026, 1, 22, 9, 0),
            false_positive_end=datetime(2026, 1, 22, 10, 0),
        )
        
        # 시간 조작: 25시간 후
        proposal.expires_at = datetime(2026, 1, 21, 10, 0)  # 과거
        
        pending = service.get_pending_proposals()
        
        assert proposal not in pending
        assert proposal.status == RefundStatus.EXPIRED
```

### 5.4 Phase 2 통합 테스트

```python
# tests/integration/services/error_budget/test_crisis_multiplier_integration.py

class TestPhase2Integration:
    """Phase 2 전체 통합 테스트."""
    
    def test_escalation_invalidates_and_recalculates(
        self,
        crisis_multiplier_provider,
        escalation_invalidation,
        emergency_manager,
    ):
        """
        격상 시 캐시 무효화 → 새 가중치 조회 통합 테스트.
        """
        # Given: NORMAL 상태에서 캐시된 값
        ctx1 = crisis_multiplier_provider.get_current_multiplier()
        assert ctx1.level_multiplier == 1.0
        
        # When: LEVEL_3로 격상
        emergency_manager.escalate_to(EmergencyLevel.LEVEL_3)
        
        # Then: 캐시 무효화되어 새 값 반환
        ctx2 = crisis_multiplier_provider.get_current_multiplier()
        assert ctx2.level_multiplier == 5.0
    
    def test_domain_propagation_with_precedence(
        self,
        domain_propagator,
        precedence_resolver,
    ):
        """
        도메인 전파 + 가중치 결합 통합 테스트.
        """
        # Level 가중치: 3.0, Domain 가중치: 2.5
        level_mult = 3.0
        domain_mult = domain_propagator.get_multiplier(
            crisis_domain="payment",
            error_domain="order",  # 1-hop
            crisis_level=EmergencyLevel.LEVEL_2,
        )
        
        final = precedence_resolver.resolve(level_mult, domain_mult)
        
        # MAX 전략: max(3.0, 2.5) = 3.0
        assert final == 3.0
    
    def test_full_budget_consumption_flow(
        self,
        check_on_use_provider,
        atomic_consumer,
        weighted_audit_recorder,
        mock_redis,
    ):
        """
        전체 버짓 소진 흐름 통합 테스트:
        1. 컨텍스트 조회 (Check on Use)
        2. 원자적 소진
        3. 감사 로그 기록
        """
        # 1. 컨텍스트 조회
        ctx = check_on_use_provider.get_current_multiplier(
            namespace="seoul",
            domain="payment",
        )
        
        # 2. 원자적 소진
        result = atomic_consumer.consume_atomic(
            namespace="seoul",
            raw_minutes=1.0,
            multiplier=ctx.final_multiplier,
            budget_key="budget:seoul",
        )
        
        assert result.success is True
        
        # 3. 감사 로그
        entry = WeightedBudgetAuditEntry(
            raw_consumption_minutes=1.0,
            weighted_consumption_minutes=result.consumed_minutes,
            level_multiplier=ctx.level_multiplier,
            domain_multiplier=ctx.domain_multiplier,
            final_multiplier=ctx.final_multiplier,
            emergency_id=ctx.emergency_id,
        )
        
        weighted_audit_recorder.record(entry)
        
        # 검증
        assert entry.weighted_consumption_minutes == ctx.final_multiplier
```

---

## 6. 모니터링

### 6.1 메트릭

```python
CRISIS_MULTIPLIER_GAUGE = Gauge(
    "selfhealing_crisis_multiplier_current",
    "Current crisis multiplier value",
    ["namespace"],
)

ERROR_BUDGET_WEIGHTED_CONSUMPTION = Counter(
    "selfhealing_error_budget_weighted_consumption_minutes_total",
    "Total weighted error budget consumption",
    ["namespace", "multiplier_range"],
)

CRISIS_MULTIPLIER_ACTIVATIONS = Counter(
    "selfhealing_crisis_multiplier_activations_total",
    "Number of times crisis multiplier was applied (>1.0)",
    ["emergency_level", "namespace"],
)
```

### 6.2 대시보드 쿼리

```promql
# 현재 Crisis Multiplier
selfhealing_crisis_multiplier_current

# 가중치 적용된 버짓 소진율 (최근 1시간)
rate(selfhealing_error_budget_weighted_consumption_minutes_total[1h])

# LEVEL_3 상황에서의 소진 비율
sum(selfhealing_error_budget_weighted_consumption_minutes_total{multiplier_range="5x"})
  /
sum(selfhealing_error_budget_weighted_consumption_minutes_total)
```

### 6.3 알림 템플릿

```
⚠️ Crisis Multiplier Activated

Current Level: LEVEL_3
Multiplier: 5.0x
Namespace: seoul

Impact:
• Error budget consumption is 5x faster
• Current budget: 45% remaining
• Projected exhaustion: 2 hours (at current rate)

Recommendation:
• Focus on resolving the emergency
• Consider pausing non-critical operations

View Dashboard: https://dashboard/error-budget
```

---

## 7. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |
| 2.0.0 | 2026-01-22 | Phase 2 확장 기능 8개 추가 | AI Assistant |
| 2.1.0 | 2026-01-22 | 누락된 7개 기능 추가 (총 15개 완성), 파일 분리 구조 및 구현 순서 추가 | AI Assistant |
| 2.2.0 | 2026-01-22 | 리뷰 반영: Phase 1 기존 컴포넌트 구현 순서에 추가, §3.1 Push-based Invalidation 연동 코드 추가, §3.2.3 깊이 제한 메트릭 추가 | AI Assistant |

---

## 8. Phase 2: 확장 기능 상세

### 8.0 CheckOnUseMultiplierProvider (Q1: 컨텍스트 획득)

#### 8.0.1 개요

"Check on Use" 패턴: 에러 발생 시점마다 현재 Emergency Level을 조회하여 가중치를 결정합니다.
캐시된 값이 아닌 실시간 상태를 사용하여 정확한 가중치 적용을 보장합니다.

**Code reference:**
- `namespace_emergency/tracker.py` (NamespacedEmergencyTracker.get_level)

#### 8.0.2 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/provider.py

from dataclasses import dataclass
from typing import Dict, Optional
import logging

from selfhealing.services.emergency_mode.enums import EmergencyLevel

logger = logging.getLogger(__name__)


# Level별 기본 가중치
DEFAULT_LEVEL_MULTIPLIERS: Dict[EmergencyLevel, float] = {
    EmergencyLevel.NORMAL: 1.0,
    EmergencyLevel.LEVEL_1: 1.5,
    EmergencyLevel.LEVEL_2: 3.0,
    EmergencyLevel.LEVEL_3: 5.0,
}


@dataclass
class MultiplierContext:
    """가중치 적용 컨텍스트."""
    
    level: EmergencyLevel
    """현재 Emergency Level."""
    
    level_multiplier: float
    """Level 기반 가중치."""
    
    domain: Optional[str] = None
    """에러 발생 도메인."""
    
    domain_multiplier: float = 1.0
    """도메인 기반 가중치."""
    
    final_multiplier: float = 1.0
    """최종 적용 가중치."""
    
    emergency_id: Optional[str] = None
    """관련 Emergency ID."""


class CheckOnUseMultiplierProvider:
    """
    Check-on-Use 패턴의 가중치 제공자.
    
    에러 발생 시점마다 현재 Emergency Level을 실시간으로 조회하여
    정확한 가중치를 반환합니다.
    
    Features:
    - 매 호출 시 Emergency Level 조회 (Check on Use)
    - 도메인 기반 추가 가중치 지원
    - MultiplierPrecedenceResolver 통합
    
    Code reference:
        namespace_emergency/tracker.py (get_level)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.0
    """
    
    def __init__(
        self,
        level_multipliers: Optional[Dict[EmergencyLevel, float]] = None,
        emergency_tracker: Optional[any] = None,
        precedence_resolver: Optional[any] = None,
    ):
        """
        CheckOnUseMultiplierProvider 초기화.
        
        Args:
            level_multipliers: Level별 가중치 맵
            emergency_tracker: Emergency 상태 추적기
            precedence_resolver: 가중치 충돌 해결기
        """
        self._multipliers = level_multipliers or DEFAULT_LEVEL_MULTIPLIERS
        self._emergency_tracker = emergency_tracker
        self._precedence_resolver = precedence_resolver
    
    def get_current_multiplier(
        self,
        namespace: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> MultiplierContext:
        """
        현재 가중치 컨텍스트 조회 (Check on Use).
        
        매 호출마다 실시간 Emergency Level을 조회합니다.
        
        Args:
            namespace: 네임스페이스
            domain: 에러 발생 도메인
        
        Returns:
            MultiplierContext: 가중치 컨텍스트
        """
        # Emergency Level 실시간 조회
        current_level = self._get_current_level(namespace)
        level_multiplier = self._multipliers.get(current_level, 1.0)
        
        # 도메인 가중치 조회 (있는 경우)
        domain_multiplier = 1.0
        if domain:
            domain_multiplier = self._get_domain_multiplier(domain)
        
        # 가중치 결합
        final_multiplier = self._combine_multipliers(
            level_multiplier, domain_multiplier
        )
        
        # Emergency ID 조회
        emergency_id = self._get_current_emergency_id(namespace)
        
        return MultiplierContext(
            level=current_level,
            level_multiplier=level_multiplier,
            domain=domain,
            domain_multiplier=domain_multiplier,
            final_multiplier=final_multiplier,
            emergency_id=emergency_id,
        )
    
    def _get_current_level(
        self,
        namespace: Optional[str] = None,
    ) -> EmergencyLevel:
        """현재 Emergency Level 조회."""
        if self._emergency_tracker:
            try:
                return self._emergency_tracker.get_level(namespace)
            except Exception as e:
                logger.warning(f"[Provider] Failed to get level: {e}")
        
        return EmergencyLevel.NORMAL
    
    def _get_domain_multiplier(self, domain: str) -> float:
        """도메인별 가중치 조회."""
        # DomainPropagationMultiplier 또는 SLA 기반 조회
        # 기본값 1.0
        return 1.0
    
    def _combine_multipliers(
        self,
        level_multiplier: float,
        domain_multiplier: float,
    ) -> float:
        """가중치 결합."""
        if self._precedence_resolver:
            return self._precedence_resolver.resolve(
                level_multiplier, domain_multiplier
            )
        
        # 기본: max 전략
        return max(level_multiplier, domain_multiplier)
    
    def _get_current_emergency_id(
        self,
        namespace: Optional[str] = None,
    ) -> Optional[str]:
        """현재 활성 Emergency ID 조회."""
        if self._emergency_tracker:
            try:
                return self._emergency_tracker.get_active_emergency_id(namespace)
            except Exception:
                pass
        return None
```

---

### 8.1 EmergencyBackfillCalculator (사후 가중치 소급 적용)

#### 8.1.1 개요

장애는 발생 시점과 감지 시점(LEVEL_3 선포 시점) 사이에 시차가 존재합니다.
LEVEL_3가 선포되면, 장애 시작 추정 시점부터 선포 시점 사이에 발생한 에러들에 대해서도 **소급하여 가중치를 적용**합니다.

**네이밍 선택 이유:**
- `EmergencyBackfillCalculator` 선택
- `RetroactiveBackfiller` 대안 검토했으나, selfhealing 프로젝트의 기존 패턴(`ShadowBudgetCalculator`, `ErrorBudgetCalculator`)과 일관성을 위해 `*Calculator` 접미사 채택
- "Backfill"은 데이터 엔지니어링에서 "누락된 과거 데이터 채우기"의 표준 용어

**Code reference:**
- `error_budget/reconciliation/shadow_calculator.py` (ShadowBudgetCalculator 패턴)

#### 8.1.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                  Emergency Backfill Flow                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Timeline:                                                           │
│  ────────────────────────────────────────────────────────────────▶  │
│                                                                      │
│  [T1: 장애 발생]      [T2: LEVEL_3 선포]      [T3: 현재]             │
│        │                     │                    │                  │
│        │◀───── Backfill ────▶│◀── 실시간 적용 ──▶│                  │
│        │      Zone           │     Zone           │                  │
│        │                     │                    │                  │
│  ┌─────▼─────────────────────▼────────────────────▼─────┐           │
│  │                                                       │           │
│  │  [T1~T2] 에러들: 1.0x → 5.0x 소급 적용               │           │
│  │  [T2~T3] 에러들: 5.0x 실시간 적용                    │           │
│  │                                                       │           │
│  └───────────────────────────────────────────────────────┘           │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

#### 8.1.3 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/backfill.py

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from selfhealing.core.timezone import now, utc_now
from selfhealing.services.emergency_mode.enums import EmergencyLevel


@dataclass
class BackfillPeriod:
    """소급 적용 대상 기간."""
    
    emergency_id: str
    """관련 Emergency ID."""
    
    detected_start_time: datetime
    """장애 시작 추정 시점."""
    
    declared_at: datetime
    """LEVEL_3 선포 시점."""
    
    target_level: EmergencyLevel
    """적용할 Emergency Level."""
    
    backfill_multiplier: float = 5.0
    """소급 적용할 가중치."""


@dataclass
class BackfillResult:
    """소급 적용 결과."""
    
    period: BackfillPeriod
    """대상 기간."""
    
    errors_affected: int
    """영향 받은 에러 수."""
    
    original_consumption_minutes: float
    """원래 소진량 (분)."""
    
    adjusted_consumption_minutes: float
    """조정된 소진량 (분)."""
    
    adjustment_delta_minutes: float
    """추가 소진량 (분)."""
    
    calculated_at: datetime = field(default_factory=utc_now)
    """계산 시각."""


class EmergencyBackfillCalculator:
    """
    사후 가중치 소급 계산기.
    
    Emergency 선포 전 발생한 에러에 대해 소급하여 가중치를 적용합니다.
    
    Features:
    - 장애 시작 추정 시점 계산 (메트릭 기반)
    - 소급 기간 내 에러 조회
    - 가중치 차이 계산 및 적용
    - Hash Chain에 소급 기록 추가
    
    Code reference:
        shadow_calculator.py (ShadowBudgetCalculator 패턴)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.1
    """
    
    # 소급 적용 최대 시간 (안전 제한)
    MAX_BACKFILL_HOURS: int = 2
    """최대 2시간 전까지만 소급 적용."""
    
    # 장애 시작 추정 방식
    DETECTION_METHODS = [
        "error_rate_spike",     # 에러율 급등 시점
        "latency_degradation",  # 지연 시간 악화 시점
        "first_alert",          # 첫 알림 발생 시점
    ]
    
    def __init__(
        self,
        get_error_records: Optional[callable] = None,
        get_metrics_history: Optional[callable] = None,
        hash_chain_manager: Optional[any] = None,
    ):
        """
        EmergencyBackfillCalculator 초기화.
        
        Args:
            get_error_records: 기간 내 에러 기록 조회 함수
            get_metrics_history: 메트릭 히스토리 조회 함수
            hash_chain_manager: 무결성 체인 관리자
        """
        self._get_error_records = get_error_records
        self._get_metrics_history = get_metrics_history
        self._hash_chain_manager = hash_chain_manager
    
    def estimate_incident_start(
        self,
        declared_at: datetime,
        namespace: Optional[str] = None,
    ) -> datetime:
        """
        장애 시작 시점 추정.
        
        메트릭 히스토리를 분석하여 에러율이 급등하기 시작한 시점을 찾습니다.
        
        Args:
            declared_at: LEVEL_3 선포 시점
            namespace: 네임스페이스
        
        Returns:
            장애 시작 추정 시점
        """
        # 최대 소급 시간 제한
        max_lookback = declared_at - timedelta(hours=self.MAX_BACKFILL_HOURS)
        
        if self._get_metrics_history:
            try:
                metrics = self._get_metrics_history(
                    start=max_lookback,
                    end=declared_at,
                    namespace=namespace,
                )
                
                # 에러율 급등 시점 찾기 (2-sigma 이상 증가)
                spike_time = self._find_error_rate_spike(metrics)
                if spike_time:
                    return max(spike_time, max_lookback)
                    
            except Exception as e:
                import logging
                logging.warning(f"[Backfill] Metrics lookup failed: {e}")
        
        # 폴백: 선포 시점 30분 전
        return declared_at - timedelta(minutes=30)
    
    def calculate_backfill(
        self,
        emergency_id: str,
        declared_at: datetime,
        target_level: EmergencyLevel,
        namespace: Optional[str] = None,
        detected_start_time: Optional[datetime] = None,
    ) -> BackfillResult:
        """
        소급 가중치 계산.
        
        Args:
            emergency_id: Emergency ID
            declared_at: LEVEL_3 선포 시점
            target_level: 적용할 레벨
            namespace: 네임스페이스
            detected_start_time: 장애 시작 시점 (None이면 자동 추정)
        
        Returns:
            BackfillResult: 소급 계산 결과
        """
        from selfhealing.services.coordination.crisis_multiplier import (
            DEFAULT_LEVEL_MULTIPLIERS,
        )
        
        # 장애 시작 시점 결정
        start_time = detected_start_time or self.estimate_incident_start(
            declared_at=declared_at,
            namespace=namespace,
        )
        
        # 소급 기간 정의
        backfill_multiplier = DEFAULT_LEVEL_MULTIPLIERS.get(target_level, 5.0)
        period = BackfillPeriod(
            emergency_id=emergency_id,
            detected_start_time=start_time,
            declared_at=declared_at,
            target_level=target_level,
            backfill_multiplier=backfill_multiplier,
        )
        
        # 기간 내 에러 조회
        errors_affected = 0
        original_consumption = 0.0
        
        if self._get_error_records:
            records = self._get_error_records(
                start=start_time,
                end=declared_at,
                namespace=namespace,
            )
            errors_affected = len(records)
            original_consumption = sum(r.raw_duration_minutes for r in records)
        
        # 조정된 소진량 계산
        adjusted_consumption = original_consumption * backfill_multiplier
        adjustment_delta = adjusted_consumption - original_consumption
        
        result = BackfillResult(
            period=period,
            errors_affected=errors_affected,
            original_consumption_minutes=original_consumption,
            adjusted_consumption_minutes=adjusted_consumption,
            adjustment_delta_minutes=adjustment_delta,
        )
        
        # Hash Chain에 기록 (무결성 보장)
        self._record_to_hash_chain(result)
        
        return result
    
    def _find_error_rate_spike(
        self,
        metrics: List[Dict],
    ) -> Optional[datetime]:
        """에러율 급등 시점 찾기 (2-sigma 기준)."""
        if not metrics or len(metrics) < 10:
            return None
        
        error_rates = [m.get("error_rate", 0) for m in metrics]
        
        import statistics
        mean = statistics.mean(error_rates)
        stdev = statistics.stdev(error_rates) if len(error_rates) > 1 else 0
        threshold = mean + (2 * stdev)
        
        for m in metrics:
            if m.get("error_rate", 0) > threshold:
                return m.get("timestamp")
        
        return None
    
    def _record_to_hash_chain(self, result: BackfillResult) -> None:
        """Hash Chain에 소급 기록 추가."""
        if self._hash_chain_manager:
            entry = {
                "type": "budget_backfill",
                "emergency_id": result.period.emergency_id,
                "detected_start": result.period.detected_start_time.isoformat(),
                "declared_at": result.period.declared_at.isoformat(),
                "multiplier_applied": result.period.backfill_multiplier,
                "errors_affected": result.errors_affected,
                "adjustment_delta_minutes": result.adjustment_delta_minutes,
                "calculated_at": result.calculated_at.isoformat(),
            }
            self._hash_chain_manager.add_entry(entry)
```

---

### 8.2 MultiplierSmoother (가중치 전환 평활화)

#### 8.2.1 개요

Emergency Level이 LEVEL_1에서 LEVEL_2로 급격히 변할 때 가중치가 계단식으로 튀는 현상을 방지합니다.
짧은 시간 동안 가중치를 점진적으로 변경하여 알람 폭주를 방지합니다.

**네이밍 선택 이유:**
- `MultiplierSmoother` 선택
- "Ramp-up"은 상승만 의미하지만, "Smoothing"은 상승/하강 모두 포함
- 기존 `RTTGradientCalculator`의 exponential smoothing 패턴과 일관성

**Code reference:**
- `throttle/adaptive.py` (RTTGradientCalculator의 exponential smoothing 패턴)

#### 8.2.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                  Multiplier Smoothing Flow                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Without Smoothing:                                                  │
│  ─────────────────                                                   │
│  5.0 ┤          ┌───────────────────                                │
│      │          │                                                    │
│  3.0 ┤     ┌────┘                                                   │
│      │     │                                                         │
│  1.5 ┤ ────┘                                                        │
│      │                                                               │
│  1.0 ┼─────┴────┴────┴────┴────┴────────────────────────────────▶  │
│      L1→L2    L2→L3                                                 │
│                                                                      │
│  With Smoothing (α=0.3):                                            │
│  ─────────────────────                                               │
│  5.0 ┤              ╭─────────────────                              │
│      │           ╭──╯                                                │
│  3.0 ┤       ╭───╯                                                  │
│      │    ╭──╯                                                       │
│  1.5 ┤ ───╯                                                         │
│      │                                                               │
│  1.0 ┼─────┴────┴────┴────┴────┴────────────────────────────────▶  │
│      L1→L2    L2→L3  (gradual transition)                           │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

#### 8.2.3 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/smoother.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import time

from selfhealing.core.timezone import utc_now


@dataclass
class MultiplierSmootherConfig:
    """가중치 평활화 설정."""
    
    smoothing_factor: float = 0.3
    """
    평활화 계수 (0.0 ~ 1.0).
    
    낮을수록 변화가 완만함:
    - 0.1: 매우 완만 (10번 샘플링 후 90% 도달)
    - 0.3: 적당 (5번 샘플링 후 83% 도달) ← 기본값
    - 0.5: 빠름 (3번 샘플링 후 87% 도달)
    - 1.0: 즉시 (평활화 없음)
    """
    
    min_transition_seconds: float = 10.0
    """최소 전환 시간 (초)."""
    
    max_transition_seconds: float = 60.0
    """최대 전환 시간 (초)."""
    
    sample_interval_seconds: float = 5.0
    """샘플링 간격 (초)."""
    
    enabled: bool = True
    """평활화 활성화 여부."""


class MultiplierSmoother:
    """
    가중치 전환 평활화기.
    
    Emergency Level 변경 시 가중치를 점진적으로 전환하여
    급격한 버짓 소진율 변화로 인한 알람 폭주를 방지합니다.
    
    Exponential smoothing 알고리즘 사용:
        smoothed = α × target + (1 - α) × current
    
    Code reference:
        throttle/adaptive.py (RTTGradientCalculator)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.2
    """
    
    def __init__(
        self,
        config: Optional[MultiplierSmootherConfig] = None,
    ):
        """
        MultiplierSmoother 초기화.
        
        Args:
            config: 평활화 설정
        """
        self.config = config or MultiplierSmootherConfig()
        self._current_value: float = 1.0
        self._target_value: float = 1.0
        self._last_sample_time: float = 0
        self._transition_start_time: Optional[float] = None
    
    def set_target(self, target_multiplier: float) -> None:
        """
        목표 가중치 설정.
        
        Args:
            target_multiplier: 목표 가중치
        """
        if target_multiplier != self._target_value:
            self._target_value = target_multiplier
            self._transition_start_time = time.time()
    
    def get_smoothed_value(self) -> float:
        """
        평활화된 가중치 반환.
        
        Returns:
            현재 평활화된 가중치
        """
        if not self.config.enabled:
            return self._target_value
        
        now = time.time()
        
        # 샘플링 간격 확인
        if now - self._last_sample_time < self.config.sample_interval_seconds:
            return self._current_value
        
        self._last_sample_time = now
        
        # 목표값에 도달했으면 조기 종료
        if abs(self._current_value - self._target_value) < 0.01:
            self._current_value = self._target_value
            return self._current_value
        
        # Exponential smoothing 적용
        alpha = self.config.smoothing_factor
        self._current_value = (
            alpha * self._target_value +
            (1 - alpha) * self._current_value
        )
        
        return self._current_value
    
    def get_current_value(self) -> float:
        """현재 가중치 (평활화 없이)."""
        return self._current_value
    
    def get_target_value(self) -> float:
        """목표 가중치."""
        return self._target_value
    
    def is_transitioning(self) -> bool:
        """전환 중 여부."""
        return abs(self._current_value - self._target_value) >= 0.01
    
    def get_transition_progress(self) -> float:
        """
        전환 진행률 (0.0 ~ 1.0).
        
        Returns:
            진행률 (1.0이면 완료)
        """
        if not self.is_transitioning():
            return 1.0
        
        if self._target_value == 1.0:
            # 하강 중
            start = self._current_value
            return 1.0 - ((self._current_value - 1.0) / (start - 1.0))
        else:
            # 상승 중
            return (self._current_value - 1.0) / (self._target_value - 1.0)
    
    def reset(self) -> None:
        """상태 초기화."""
        self._current_value = 1.0
        self._target_value = 1.0
        self._last_sample_time = 0
        self._transition_start_time = None
```

---

### 8.3 DomainPropagationMultiplier (도메인 의존성 기반 가중치 전파)

#### 8.3.1 개요

서울 리전의 '결제 도메인' 장애 시, 직접 장애 도메인은 5.0x, 인접 의존 도메인은 감쇠 적용합니다.
도메인 간 의존성 그래프(Dependency Graph)를 참조하여 가중치를 전파합니다.

**네이밍 선택 이유:**
- `DomainPropagationMultiplier` 선택
- 기존 `DomainAwareCrisisMultiplier`와 구분
- "Propagation"은 네트워크/그래프 이론에서 "인접 노드로 값 전파"의 표준 용어
- selfhealing 프로젝트의 DNA 모듈에 `DependencyGraph`가 이미 존재

**Code reference:**
- `coordination/crisis_multiplier.py` (DomainAwareCrisisMultiplier)
- `tests/unit/dna/test_dna_graph.py` (DependencyGraph 패턴)

#### 8.3.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                  Domain Propagation Flow                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Dependency Graph:                                                   │
│  ─────────────────                                                   │
│                                                                      │
│          ┌───────────┐                                              │
│          │  Payment  │◀── Crisis Domain (5.0x)                      │
│          └─────┬─────┘                                              │
│                │ depends_on                                          │
│        ┌───────┴───────┐                                            │
│        ▼               ▼                                             │
│  ┌───────────┐   ┌───────────┐                                      │
│  │   Order   │   │ Inventory │◀── 1-hop (2.5x)                      │
│  └─────┬─────┘   └───────────┘                                      │
│        │ depends_on                                                  │
│        ▼                                                             │
│  ┌───────────┐                                                      │
│  │   Cart    │◀── 2-hop (1.5x)                                      │
│  └───────────┘                                                      │
│                                                                      │
│  ┌───────────┐                                                      │
│  │ Analytics │◀── No dependency (1.0x)                              │
│  └───────────┘                                                      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

#### 8.3.3 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/propagation.py

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
import logging

from selfhealing.services.emergency_mode.enums import EmergencyLevel

logger = logging.getLogger(__name__)


@dataclass
class PropagationConfig:
    """도메인 전파 설정."""
    
    base_multiplier: float = 5.0
    """장애 도메인 기본 가중치."""
    
    decay_per_hop: float = 0.5
    """홉당 감쇠율 (1-hop: 50% 감쇠)."""
    
    min_multiplier: float = 1.0
    """최소 가중치 (감쇠 하한)."""
    
    max_hops: int = 3
    """최대 전파 홉 수."""
    
    enabled: bool = True
    """전파 활성화 여부."""


class DomainPropagationMultiplier:
    """
    도메인 의존성 기반 가중치 전파기.
    
    장애 도메인으로부터의 의존성 거리(hop)에 따라 가중치를 감쇠 적용합니다.
    
    Features:
    - 의존성 그래프 기반 홉 거리 계산
    - 홉 수에 따른 감쇠 가중치 적용
    - 최대 홉 수 제한 (무한 전파 방지)
    
    Code reference:
        coordination/crisis_multiplier.py (DomainAwareCrisisMultiplier)
        dna/graph.py (DependencyGraph)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.3
    """
    
    def __init__(
        self,
        config: Optional[PropagationConfig] = None,
        dependency_graph: Optional[Dict[str, List[str]]] = None,
    ):
        """
        DomainPropagationMultiplier 초기화.
        
        Args:
            config: 전파 설정
            dependency_graph: 도메인 의존성 그래프 {domain: [depends_on_domains]}
        """
        self.config = config or PropagationConfig()
        self._graph: Dict[str, Set[str]] = {}
        
        if dependency_graph:
            for domain, deps in dependency_graph.items():
                self._graph[domain.lower()] = set(d.lower() for d in deps)
    
    def set_dependency(self, domain: str, depends_on: List[str]) -> None:
        """
        도메인 의존성 설정.
        
        Args:
            domain: 도메인 이름
            depends_on: 의존하는 도메인 목록
        """
        self._graph[domain.lower()] = set(d.lower() for d in depends_on)
    
    def get_hop_distance(
        self,
        crisis_domain: str,
        error_domain: str,
    ) -> int:
        """
        장애 도메인으로부터의 홉 거리 계산 (BFS).
        
        순환 참조 방지:
        - visited Set으로 이미 방문한 노드 추적
        - max_hops 제한으로 무한 루프 방지
        - 리뷰 §3.2.3 반영: 깊이 제한(Depth Limit) 적용
        
        Args:
            crisis_domain: 장애 발생 도메인
            error_domain: 에러 발생 도메인
        
        Returns:
            홉 거리 (-1이면 연결 없음)
        """
        crisis = crisis_domain.lower()
        error = error_domain.lower()
        
        if crisis == error:
            return 0
        
        # BFS로 최단 거리 찾기
        visited: Set[str] = {crisis}
        queue: List[tuple] = [(crisis, 0)]  # (domain, distance)
        depth_limit_reached = False
        
        while queue:
            current, distance = queue.pop(0)
            
            # 깊이 제한 확인 (리뷰 §3.2.3)
            if distance >= self.config.max_hops:
                depth_limit_reached = True
                continue
            
            # 역방향 탐색: 현재 도메인에 의존하는 도메인들
            for domain, deps in self._graph.items():
                if current in deps and domain not in visited:
                    if domain == error:
                        return distance + 1
                    visited.add(domain)
                    queue.append((domain, distance + 1))
        
        # 깊이 제한으로 탐색 중단된 경우 메트릭 기록
        if depth_limit_reached:
            self._record_depth_limit_metric(crisis, error)
        
        return -1  # 연결 없음
    
    def _record_depth_limit_metric(
        self,
        crisis_domain: str,
        error_domain: str,
    ) -> None:
        """깊이 제한 도달 시 메트릭 기록."""
        try:
            from prometheus_client import Counter
            
            # 메트릭 정의 (lazy)
            if not hasattr(self, '_depth_limit_counter'):
                self._depth_limit_counter = Counter(
                    "selfhealing_domain_propagation_depth_limit_reached_total",
                    "Number of times domain propagation hit depth limit",
                    ["crisis_domain", "error_domain"],
                )
            
            self._depth_limit_counter.labels(
                crisis_domain=crisis_domain,
                error_domain=error_domain,
            ).inc()
            
            logger.debug(
                f"[DomainPropagation] Depth limit reached: "
                f"crisis={crisis_domain}, error={error_domain}, "
                f"max_hops={self.config.max_hops}"
            )
        except Exception:
            pass  # 메트릭 실패는 무시
        
        return -1  # 연결 없음
    
    def get_multiplier(
        self,
        crisis_domain: str,
        error_domain: str,
        crisis_level: EmergencyLevel,
    ) -> float:
        """
        도메인 전파 기반 가중치 계산.
        
        Args:
            crisis_domain: 장애 발생 도메인
            error_domain: 에러 발생 도메인
            crisis_level: Emergency 레벨
        
        Returns:
            적용할 가중치
        """
        if not self.config.enabled:
            return self.config.base_multiplier
        
        hop_distance = self.get_hop_distance(crisis_domain, error_domain)
        
        if hop_distance < 0:
            # 연결 없음: 기본 가중치
            return self.config.min_multiplier
        
        if hop_distance == 0:
            # 동일 도메인: 전체 가중치
            return self.config.base_multiplier
        
        # 감쇠 적용: base * (decay ^ hop)
        decayed = self.config.base_multiplier * (
            self.config.decay_per_hop ** hop_distance
        )
        
        final = max(decayed, self.config.min_multiplier)
        
        logger.debug(
            f"[DomainPropagation] crisis={crisis_domain}, error={error_domain}, "
            f"hop={hop_distance}, multiplier={final:.2f}"
        )
        
        return final
    
    def get_affected_domains(
        self,
        crisis_domain: str,
    ) -> Dict[str, float]:
        """
        장애 도메인의 영향을 받는 모든 도메인과 가중치 조회.
        
        Args:
            crisis_domain: 장애 발생 도메인
        
        Returns:
            {domain: multiplier} 딕셔너리
        """
        result = {crisis_domain.lower(): self.config.base_multiplier}
        
        for domain in self._graph.keys():
            if domain != crisis_domain.lower():
                hop = self.get_hop_distance(crisis_domain, domain)
                if hop >= 0:
                    result[domain] = self.get_multiplier(
                        crisis_domain, domain, EmergencyLevel.LEVEL_3
                    )
        
        return result
```

---

### 8.4 CanaryMultiplierRollout (가중치 설정의 카나리 배포)

#### 8.4.1 개요

LEVEL_3일 때 5.0x라는 값 자체가 적절한지는 운영 데이터가 쌓여야 알 수 있습니다.
가중치 값(multiplier_map) 자체를 Canary Rollout을 통해 안전하게 변경할 수 있도록 구성합니다.

**Code reference:**
- `canary/service.py` (CanaryRolloutService)

#### 8.4.2 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/canary_multiplier.py

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from selfhealing.services.canary.models import CanaryStage
from selfhealing.services.canary.service import CanaryRolloutService


@dataclass
class MultiplierCanaryConfig:
    """가중치 카나리 배포 설정."""
    
    config_type: str = "crisis_multiplier"
    """설정 유형."""
    
    canary_cluster: str = "seoul-canary"
    """카나리 클러스터."""
    
    observation_minutes: int = 30
    """관찰 시간 (분)."""
    
    auto_promote: bool = False
    """자동 프로모션 여부 (기본: 수동)."""
    
    rollback_on_budget_spike: bool = True
    """버짓 급락 시 자동 롤백."""
    
    budget_spike_threshold: float = 20.0
    """버짓 급락 임계값 (%)."""


class CanaryMultiplierRollout:
    """
    가중치 설정의 카나리 배포 관리자.
    
    가중치 값 변경을 안전하게 점진적으로 배포합니다.
    
    Features:
    - 카나리 클러스터에서 먼저 테스트
    - 메트릭 기반 자동 롤백
    - 단계별 프로모션
    
    Code reference:
        canary/service.py (CanaryRolloutService)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.4
        docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
    """
    
    def __init__(
        self,
        config: Optional[MultiplierCanaryConfig] = None,
        canary_service: Optional[CanaryRolloutService] = None,
    ):
        """
        CanaryMultiplierRollout 초기화.
        
        Args:
            config: 카나리 배포 설정
            canary_service: 기존 Canary 서비스 (없으면 자동 획득)
        """
        self.config = config or MultiplierCanaryConfig()
        self._canary_service = canary_service
    
    @property
    def canary_service(self) -> CanaryRolloutService:
        """CanaryRolloutService (Lazy loading)."""
        if self._canary_service is None:
            from selfhealing.services.canary import get_canary_rollout_service
            self._canary_service = get_canary_rollout_service()
        return self._canary_service
    
    def create_multiplier_rollout(
        self,
        new_multipliers: Dict[str, float],
        created_by: str,
        reason: str,
        target_clusters: Optional[List[str]] = None,
    ) -> Any:
        """
        가중치 변경 롤아웃 생성.
        
        Args:
            new_multipliers: 새 가중치 맵 {"LEVEL_3": 7.0, ...}
            created_by: 생성자
            reason: 변경 사유
            target_clusters: 대상 클러스터 (None이면 기본값)
        
        Returns:
            CanaryRollout
        """
        clusters = target_clusters or ["seoul", "tokyo"]
        
        stages = [
            CanaryStage(
                name="canary",
                clusters=[self.config.canary_cluster],
                percentage=10,
            ),
            CanaryStage(
                name="regional",
                clusters=clusters[:1],
                percentage=50,
            ),
            CanaryStage(
                name="full",
                clusters=clusters,
                percentage=100,
            ),
        ]
        
        rollout = self.canary_service.create_rollout(
            config_type=self.config.config_type,
            new_values={"multipliers": new_multipliers},
            stages=stages,
            created_by=created_by,
            reason=reason,
        )
        
        return rollout
    
    def start_rollout(self, rollout_id: str) -> None:
        """롤아웃 시작."""
        self.canary_service.start_rollout(rollout_id)
    
    def promote(self, rollout_id: str) -> None:
        """다음 단계로 프로모션."""
        self.canary_service.promote(rollout_id)
    
    def rollback(self, rollout_id: str, reason: str) -> None:
        """롤백."""
        self.canary_service.rollback(rollout_id, reason=reason)
```

---

### 8.5 EscalationTriggeredInvalidation (격상 시 캐시 즉시 무효화)

#### 8.5.1 개요

Emergency Level이 격상(Escalation)될 때 30초 캐시로 인한 '지연된 소진'을 방지하기 위해,
캐시를 즉시 무효화(Invalidate)하는 Push-based Invalidation 로직입니다.

**네이밍 선택 이유:**
- `EscalationTriggeredInvalidation` 선택
- selfhealing 프로젝트의 기존 `EscalationAuditTrail` 패턴과 일관성
- "Push-based Invalidation"보다 이벤트 트리거 기반임을 명확히 표현

**Code reference:**
- `namespace_emergency/tracker.py` (invalidate_cache 메서드)
- `namespace_emergency/escalation_audit.py` (Escalation 패턴)

#### 8.5.2 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/escalation_invalidation.py

from typing import Optional, List, Callable
import logging

from selfhealing.services.emergency_mode.enums import EmergencyLevel

logger = logging.getLogger(__name__)


class EscalationTriggeredInvalidation:
    """
    Emergency 격상 시 캐시 즉시 무효화.
    
    Emergency Level이 격상될 때 이벤트 버스를 통해 푸시 방식으로
    모든 관련 캐시를 즉시 무효화합니다.
    
    Features:
    - 이벤트 버스 구독 기반 푸시 무효화
    - 다중 캐시 대상 지원
    - 무효화 기록 (메트릭/로깅)
    
    Code reference:
        namespace_emergency/tracker.py (invalidate_cache)
        namespace_emergency/escalation_audit.py (Escalation 패턴)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.5
    """
    
    def __init__(self):
        """EscalationTriggeredInvalidation 초기화."""
        self._invalidation_targets: List[Callable[[], None]] = []
        self._registered = False
    
    def register_target(self, invalidate_fn: Callable[[], None]) -> None:
        """
        무효화 대상 등록.
        
        Args:
            invalidate_fn: 캐시 무효화 함수
        """
        self._invalidation_targets.append(invalidate_fn)
    
    def register_event_handler(self) -> None:
        """이벤트 버스에 핸들러 등록."""
        if self._registered:
            return
        
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType
            
            bus = get_event_bus()
            bus.subscribe(
                EventType.EMERGENCY_LEVEL_CHANGED,
                self._on_level_changed,
            )
            self._registered = True
            logger.info(
                "[EscalationInvalidation] Registered for EMERGENCY_LEVEL_CHANGED events"
            )
        except Exception as e:
            logger.warning(f"[EscalationInvalidation] Registration failed: {e}")
    
    def _on_level_changed(self, event) -> None:
        """Emergency Level 변경 이벤트 핸들러."""
        old_level = event.data.get("old_level")
        new_level = event.data.get("new_level")
        
        # 격상(Escalation)인 경우에만 즉시 무효화
        if self._is_escalation(old_level, new_level):
            self._invalidate_all_caches(
                reason=f"Escalation: {old_level} → {new_level}",
                namespace=event.data.get("namespace"),
            )
    
    def _is_escalation(
        self,
        old_level: Optional[str],
        new_level: Optional[str],
    ) -> bool:
        """격상 여부 확인."""
        level_order = {
            "NORMAL": 0,
            "LEVEL_1": 1,
            "LEVEL_2": 2,
            "LEVEL_3": 3,
        }
        old_order = level_order.get(old_level, 0)
        new_order = level_order.get(new_level, 0)
        
        return new_order > old_order
    
    def _invalidate_all_caches(
        self,
        reason: str,
        namespace: Optional[str] = None,
    ) -> None:
        """모든 등록된 캐시 무효화."""
        logger.warning(
            f"[EscalationInvalidation] Invalidating all caches: "
            f"reason={reason}, namespace={namespace}"
        )
        
        for invalidate_fn in self._invalidation_targets:
            try:
                invalidate_fn()
            except Exception as e:
                logger.error(f"[EscalationInvalidation] Invalidation failed: {e}")
        
        # 메트릭 기록
        try:
            from selfhealing.metrics.drift_metrics import record_config_cache_invalidated
            record_config_cache_invalidated("crisis_multiplier")
        except Exception:
            pass


# =============================================================================
# Singleton
# =============================================================================

_escalation_invalidation: Optional[EscalationTriggeredInvalidation] = None


def get_escalation_triggered_invalidation() -> EscalationTriggeredInvalidation:
    """EscalationTriggeredInvalidation 싱글톤 반환."""
    global _escalation_invalidation
    if _escalation_invalidation is None:
        _escalation_invalidation = EscalationTriggeredInvalidation()
        _escalation_invalidation.register_event_handler()
    return _escalation_invalidation


# =============================================================================
# CrisisMultiplierProvider 연동 (리뷰 §3.1 반영)
# =============================================================================

def setup_crisis_multiplier_invalidation() -> None:
    """
    CrisisMultiplierProvider를 EscalationTriggeredInvalidation에 등록.
    
    애플리케이션 시작 시 호출하여 Push-based Invalidation을 활성화합니다.
    
    Usage:
        # app startup
        from selfhealing.services.error_budget.escalation_invalidation import (
            setup_crisis_multiplier_invalidation,
        )
        setup_crisis_multiplier_invalidation()
    
    Reference:
        리뷰 §3.1: "Emergency Level이 격상될 때, 30초 캐시를 기다리지 않고 
        즉시 무효화하는 로직을 EmergencyModeTracker와 연동"
    """
    from selfhealing.services.error_budget.multiplier import (
        get_crisis_multiplier_provider,
    )
    
    invalidation = get_escalation_triggered_invalidation()
    provider = get_crisis_multiplier_provider()
    
    # CrisisMultiplierProvider의 캐시를 무효화 대상으로 등록
    invalidation.register_target(provider.invalidate_cache)
    
    logger.info(
        "[EscalationInvalidation] CrisisMultiplierProvider registered for "
        "push-based invalidation on escalation events"
    )
```

---

### 8.6 WeightedBudgetAuditEntry (가중치 근거 포함 무결성 로그)

#### 8.6.1 개요

버짓 소진 시 가중치 근거(Multiplier Value, Emergency ID)를 포함하는 무결성 로그 스키마입니다.
Hash Chain에 포함되어 "장애 은폐가 원천적으로 불가능한 시스템"을 구현합니다.

**네이밍 선택 이유:**
- `WeightedBudgetAuditEntry` 선택 (제안된 `WeightedAuditEntry`보다 목적이 명확)
- 기존 `EscalationAuditEntry`, `InterlockBypassAuditEntry` 패턴과 일관성

**Code reference:**
- `namespace_emergency/escalation_audit.py` (EscalationAuditEntry)
- `canary/bypass_audit.py` (InterlockBypassAuditEntry)

#### 8.6.2 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/weighted_audit.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
import uuid

from selfhealing.core.timezone import utc_now


@dataclass
class WeightedBudgetAuditEntry:
    """
    가중치 적용된 버짓 소진 감사 로그.
    
    버짓 소진 시 적용된 가중치와 그 근거를 기록하여
    Hash Chain에 포함시킵니다.
    
    Features:
    - 적용된 가중치 값 기록
    - 근거가 된 Emergency ID 기록
    - 도메인 가중치 정보 포함
    - Hash Chain 통합
    
    Code reference:
        namespace_emergency/escalation_audit.py (EscalationAuditEntry)
        canary/bypass_audit.py (InterlockBypassAuditEntry)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.6
    """
    
    # 식별자
    audit_id: str = field(default_factory=lambda: f"wba_{uuid.uuid4().hex[:12]}")
    """감사 로그 ID."""
    
    # 시간 정보
    recorded_at: datetime = field(default_factory=utc_now)
    """기록 시각."""
    
    # 버짓 정보
    raw_consumption_minutes: float = 0.0
    """원시 소진량 (분)."""
    
    weighted_consumption_minutes: float = 0.0
    """가중치 적용된 소진량 (분)."""
    
    # 가중치 근거
    level_multiplier: float = 1.0
    """Emergency Level 기반 가중치."""
    
    domain_multiplier: float = 1.0
    """도메인 기반 가중치."""
    
    final_multiplier: float = 1.0
    """최종 적용 가중치."""
    
    # Emergency 근거
    emergency_id: Optional[str] = None
    """관련 Emergency ID."""
    
    emergency_level: Optional[str] = None
    """당시 Emergency Level."""
    
    # 도메인 정보
    crisis_domain: Optional[str] = None
    """장애 발생 도메인."""
    
    error_domain: Optional[str] = None
    """에러 발생 도메인."""
    
    hop_distance: int = 0
    """도메인 간 홉 거리."""
    
    # 메타데이터
    namespace: Optional[str] = None
    """네임스페이스."""
    
    service_name: Optional[str] = None
    """서비스 이름."""
    
    error_type: Optional[str] = None
    """에러 유형."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환 (Hash Chain용)."""
        return {
            "audit_id": self.audit_id,
            "recorded_at": self.recorded_at.isoformat(),
            "raw_consumption_minutes": self.raw_consumption_minutes,
            "weighted_consumption_minutes": self.weighted_consumption_minutes,
            "level_multiplier": self.level_multiplier,
            "domain_multiplier": self.domain_multiplier,
            "final_multiplier": self.final_multiplier,
            "emergency_id": self.emergency_id,
            "emergency_level": self.emergency_level,
            "crisis_domain": self.crisis_domain,
            "error_domain": self.error_domain,
            "hop_distance": self.hop_distance,
            "namespace": self.namespace,
            "service_name": self.service_name,
            "error_type": self.error_type,
        }
    
    def to_hash_chain_entry(self) -> Dict[str, Any]:
        """Hash Chain 엔트리 변환."""
        return {
            "type": "weighted_budget_consumption",
            "audit_id": self.audit_id,
            "timestamp": self.recorded_at.isoformat(),
            "consumption": {
                "raw": self.raw_consumption_minutes,
                "weighted": self.weighted_consumption_minutes,
            },
            "multipliers": {
                "level": self.level_multiplier,
                "domain": self.domain_multiplier,
                "final": self.final_multiplier,
            },
            "evidence": {
                "emergency_id": self.emergency_id,
                "emergency_level": self.emergency_level,
                "crisis_domain": self.crisis_domain,
                "error_domain": self.error_domain,
            },
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WeightedBudgetAuditEntry":
        """딕셔너리에서 생성."""
        recorded_at = data.get("recorded_at")
        if isinstance(recorded_at, str):
            from datetime import datetime
            recorded_at = datetime.fromisoformat(recorded_at)
        
        return cls(
            audit_id=data.get("audit_id", ""),
            recorded_at=recorded_at or utc_now(),
            raw_consumption_minutes=data.get("raw_consumption_minutes", 0.0),
            weighted_consumption_minutes=data.get("weighted_consumption_minutes", 0.0),
            level_multiplier=data.get("level_multiplier", 1.0),
            domain_multiplier=data.get("domain_multiplier", 1.0),
            final_multiplier=data.get("final_multiplier", 1.0),
            emergency_id=data.get("emergency_id"),
            emergency_level=data.get("emergency_level"),
            crisis_domain=data.get("crisis_domain"),
            error_domain=data.get("error_domain"),
            hop_distance=data.get("hop_distance", 0),
            namespace=data.get("namespace"),
            service_name=data.get("service_name"),
            error_type=data.get("error_type"),
        )
```

---

### 8.7 MultiplierPrecedenceResolver (가중치 충돌 해결 로직)

#### 8.7.1 개요

도메인 가중치와 레벨 가중치가 충돌할 때 어떤 값을 우선할지 결정하는 로직입니다.
`max()` vs `sum()` 등 다양한 전략을 지원합니다.

**Code reference:**
- `namespace_emergency/atomic_query.py` (PRECEDENCE_LEVELS 패턴)

#### 8.7.2 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/precedence.py

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class MultiplierCombineStrategy(str, Enum):
    """가중치 결합 전략."""
    
    MAX = "max"
    """최대값 사용 (보수적)."""
    
    SUM = "sum"
    """합산 (위험)."""
    
    MULTIPLY = "multiply"
    """곱셈 (매우 위험, Cap 필수)."""
    
    LEVEL_PRIORITY = "level_priority"
    """Level 가중치 우선."""
    
    DOMAIN_PRIORITY = "domain_priority"
    """Domain 가중치 우선."""


@dataclass
class MultiplierPrecedenceConfig:
    """가중치 우선순위 설정."""
    
    combine_strategy: MultiplierCombineStrategy = MultiplierCombineStrategy.MAX
    """결합 전략."""
    
    max_combined_multiplier: float = 10.0
    """
    최대 결합 가중치 (통합 Cap).
    
    SSOT: 모든 가중치 결합 후 이 값을 초과할 수 없음.
    """
    
    level_weight: float = 1.0
    """Level 가중치 비중 (가중 평균 시 사용)."""
    
    domain_weight: float = 1.0
    """Domain 가중치 비중 (가중 평균 시 사용)."""


class MultiplierPrecedenceResolver:
    """
    가중치 충돌 해결기.
    
    Level 가중치와 Domain 가중치가 모두 적용될 때
    최종 가중치를 결정합니다.
    
    Features:
    - 다양한 결합 전략 지원 (max, sum, multiply)
    - 통합 Cap 적용 (MAX_CRISIS_MULTIPLIER_CAP)
    - 결정 로깅 및 감사
    
    Code reference:
        namespace_emergency/atomic_query.py (PRECEDENCE_LEVELS)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.7
    """
    
    def __init__(
        self,
        config: Optional[MultiplierPrecedenceConfig] = None,
    ):
        """
        MultiplierPrecedenceResolver 초기화.
        
        Args:
            config: 우선순위 설정
        """
        self.config = config or MultiplierPrecedenceConfig()
    
    def resolve(
        self,
        level_multiplier: float,
        domain_multiplier: float,
    ) -> float:
        """
        가중치 충돌 해결.
        
        Args:
            level_multiplier: Emergency Level 기반 가중치
            domain_multiplier: 도메인 기반 가중치
        
        Returns:
            최종 적용 가중치
        """
        strategy = self.config.combine_strategy
        
        if strategy == MultiplierCombineStrategy.MAX:
            result = max(level_multiplier, domain_multiplier)
        
        elif strategy == MultiplierCombineStrategy.SUM:
            result = level_multiplier + domain_multiplier - 1.0  # 기본값 1.0 중복 제거
        
        elif strategy == MultiplierCombineStrategy.MULTIPLY:
            result = level_multiplier * domain_multiplier
        
        elif strategy == MultiplierCombineStrategy.LEVEL_PRIORITY:
            result = level_multiplier if level_multiplier > 1.0 else domain_multiplier
        
        elif strategy == MultiplierCombineStrategy.DOMAIN_PRIORITY:
            result = domain_multiplier if domain_multiplier > 1.0 else level_multiplier
        
        else:
            result = max(level_multiplier, domain_multiplier)
        
        # Cap 적용
        final = min(result, self.config.max_combined_multiplier)
        
        if result != final:
            logger.warning(
                f"[PrecedenceResolver] Multiplier capped: "
                f"raw={result:.2f}, capped={final:.2f}, "
                f"strategy={strategy.value}"
            )
        
        return final
    
    def explain(
        self,
        level_multiplier: float,
        domain_multiplier: float,
    ) -> str:
        """
        결정 과정 설명.
        
        Returns:
            사람이 읽을 수 있는 설명
        """
        final = self.resolve(level_multiplier, domain_multiplier)
        strategy = self.config.combine_strategy
        
        return (
            f"Level={level_multiplier}x, Domain={domain_multiplier}x, "
            f"Strategy={strategy.value}, Final={final}x"
        )
```

---

### 8.8 BudgetRefundProposalService (오탐 시 버짓 복구 제안)

#### 8.8.1 개요

LEVEL_3가 오탐(False Positive)으로 판명되어 해제되었을 때,
그동안 초과 소진된 버짓의 복구 가능 금액을 계산하여 Admin에게 제안합니다.

**네이밍 선택 이유:**
- `BudgetRefundProposalService` 선택 (제안된 `RefundProposalEngine`보다 Service 컨벤션과 일치)
- selfhealing 프로젝트의 `*Service` 네이밍 패턴 준수

**Code reference:**
- `error_budget/recorder.py` (FreezeDecisionRecorder의 운영자 승인 패턴)

#### 8.8.2 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/refund.py

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from enum import Enum
import uuid
import logging

from selfhealing.core.timezone import utc_now

logger = logging.getLogger(__name__)


class RefundStatus(str, Enum):
    """환불 상태."""
    
    PROPOSED = "proposed"
    """제안됨 (승인 대기)."""
    
    APPROVED = "approved"
    """승인됨."""
    
    REJECTED = "rejected"
    """거부됨."""
    
    APPLIED = "applied"
    """적용됨."""
    
    EXPIRED = "expired"
    """만료됨 (미처리)."""


@dataclass
class RefundProposal:
    """버짓 환불 제안."""
    
    proposal_id: str = field(default_factory=lambda: f"refund_{uuid.uuid4().hex[:12]}")
    """제안 ID."""
    
    # 기간 정보
    false_positive_start: Optional[datetime] = None
    """오탐 시작 시점."""
    
    false_positive_end: Optional[datetime] = None
    """오탐 종료 시점 (해제 시점)."""
    
    emergency_id: Optional[str] = None
    """관련 Emergency ID."""
    
    # 환불 금액
    overconsumption_minutes: float = 0.0
    """초과 소진량 (분)."""
    
    proposed_refund_minutes: float = 0.0
    """제안 환불량 (분) - 보통 overconsumption의 50%."""
    
    refund_ratio: float = 0.5
    """환불 비율 (기본 50%)."""
    
    # 상태
    status: RefundStatus = RefundStatus.PROPOSED
    """현재 상태."""
    
    proposed_at: datetime = field(default_factory=utc_now)
    """제안 시각."""
    
    expires_at: Optional[datetime] = None
    """만료 시각 (24시간 후)."""
    
    # 승인 정보
    approved_by: Optional[str] = None
    """승인자."""
    
    approved_at: Optional[datetime] = None
    """승인 시각."""
    
    rejection_reason: Optional[str] = None
    """거부 사유."""
    
    # 메타데이터
    namespace: Optional[str] = None
    """네임스페이스."""
    
    def __post_init__(self):
        if self.expires_at is None:
            self.expires_at = self.proposed_at + timedelta(hours=24)
    
    def to_dict(self) -> Dict:
        """딕셔너리 변환."""
        return {
            "proposal_id": self.proposal_id,
            "false_positive_start": self.false_positive_start.isoformat() if self.false_positive_start else None,
            "false_positive_end": self.false_positive_end.isoformat() if self.false_positive_end else None,
            "emergency_id": self.emergency_id,
            "overconsumption_minutes": self.overconsumption_minutes,
            "proposed_refund_minutes": self.proposed_refund_minutes,
            "refund_ratio": self.refund_ratio,
            "status": self.status.value,
            "proposed_at": self.proposed_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
            "namespace": self.namespace,
        }


class BudgetRefundProposalService:
    """
    버짓 환불 제안 서비스.
    
    오탐(False Positive) 해제 시 초과 소진된 버짓의 환불을 
    계산하고 Admin 승인을 통해 적용합니다.
    
    Features:
    - 오탐 기간 동안 초과 소진량 계산
    - 환불 제안 생성 (50% 부분 환불 기본)
    - Admin 승인/거부 프로세스
    - 24시간 만료 정책
    
    Code reference:
        error_budget/recorder.py (FreezeDecisionRecorder)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.8
    """
    
    # 기본 환불 비율 (100% 환불은 시스템 요동 유발)
    DEFAULT_REFUND_RATIO: float = 0.5
    """기본 50% 환불."""
    
    # 제안 만료 시간
    PROPOSAL_EXPIRY_HOURS: int = 24
    """24시간 후 자동 만료."""
    
    def __init__(
        self,
        get_error_records: Optional[callable] = None,
        persist_proposal: Optional[callable] = None,
        emit_notification: Optional[callable] = None,
    ):
        """
        BudgetRefundProposalService 초기화.
        
        Args:
            get_error_records: 에러 기록 조회 함수
            persist_proposal: 제안 저장 함수
            emit_notification: 알림 발송 함수
        """
        self._get_error_records = get_error_records
        self._persist_proposal = persist_proposal
        self._emit_notification = emit_notification
        
        # In-memory 저장소 (영속화 함수 없을 때)
        self._proposals: Dict[str, RefundProposal] = {}
    
    def calculate_overconsumption(
        self,
        false_positive_start: datetime,
        false_positive_end: datetime,
        namespace: Optional[str] = None,
    ) -> float:
        """
        오탐 기간 동안 초과 소진량 계산.
        
        Args:
            false_positive_start: 오탐 시작 시점
            false_positive_end: 오탐 종료 시점
            namespace: 네임스페이스
        
        Returns:
            초과 소진량 (분)
        """
        if not self._get_error_records:
            return 0.0
        
        records = self._get_error_records(
            start=false_positive_start,
            end=false_positive_end,
            namespace=namespace,
        )
        
        # 가중치가 1.0x 초과로 적용된 부분만 계산
        overconsumption = 0.0
        for record in records:
            if record.multiplier_applied > 1.0:
                excess = record.weighted_duration_minutes - record.raw_duration_minutes
                overconsumption += excess
        
        return overconsumption
    
    def create_proposal(
        self,
        emergency_id: str,
        false_positive_start: datetime,
        false_positive_end: datetime,
        namespace: Optional[str] = None,
        refund_ratio: Optional[float] = None,
    ) -> RefundProposal:
        """
        환불 제안 생성.
        
        Args:
            emergency_id: 관련 Emergency ID
            false_positive_start: 오탐 시작 시점
            false_positive_end: 오탐 종료 시점
            namespace: 네임스페이스
            refund_ratio: 환불 비율 (기본 50%)
        
        Returns:
            RefundProposal
        """
        ratio = refund_ratio or self.DEFAULT_REFUND_RATIO
        
        overconsumption = self.calculate_overconsumption(
            false_positive_start=false_positive_start,
            false_positive_end=false_positive_end,
            namespace=namespace,
        )
        
        proposal = RefundProposal(
            false_positive_start=false_positive_start,
            false_positive_end=false_positive_end,
            emergency_id=emergency_id,
            overconsumption_minutes=overconsumption,
            proposed_refund_minutes=overconsumption * ratio,
            refund_ratio=ratio,
            namespace=namespace,
        )
        
        # 저장
        self._proposals[proposal.proposal_id] = proposal
        if self._persist_proposal:
            self._persist_proposal(proposal)
        
        # 알림 발송
        if self._emit_notification:
            self._emit_notification(
                event_type="budget_refund_proposed",
                data=proposal.to_dict(),
            )
        
        logger.info(
            f"[BudgetRefund] Proposal created: id={proposal.proposal_id}, "
            f"overconsumption={overconsumption:.2f}min, "
            f"proposed_refund={proposal.proposed_refund_minutes:.2f}min"
        )
        
        return proposal
    
    def approve(
        self,
        proposal_id: str,
        approved_by: str,
    ) -> Optional[RefundProposal]:
        """
        환불 제안 승인.
        
        Args:
            proposal_id: 제안 ID
            approved_by: 승인자
        
        Returns:
            승인된 제안 (없으면 None)
        """
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            logger.warning(f"[BudgetRefund] Proposal not found: {proposal_id}")
            return None
        
        if proposal.status != RefundStatus.PROPOSED:
            logger.warning(f"[BudgetRefund] Proposal not in PROPOSED state: {proposal_id}")
            return None
        
        # 만료 확인
        if proposal.expires_at and utc_now() > proposal.expires_at:
            proposal.status = RefundStatus.EXPIRED
            return None
        
        proposal.status = RefundStatus.APPROVED
        proposal.approved_by = approved_by
        proposal.approved_at = utc_now()
        
        logger.warning(
            f"[BudgetRefund] Proposal approved: id={proposal_id}, "
            f"approved_by={approved_by}, refund={proposal.proposed_refund_minutes:.2f}min"
        )
        
        return proposal
    
    def reject(
        self,
        proposal_id: str,
        rejected_by: str,
        reason: str,
    ) -> Optional[RefundProposal]:
        """환불 제안 거부."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return None
        
        proposal.status = RefundStatus.REJECTED
        proposal.approved_by = rejected_by
        proposal.rejection_reason = reason
        
        logger.info(
            f"[BudgetRefund] Proposal rejected: id={proposal_id}, "
            f"rejected_by={rejected_by}, reason={reason}"
        )
        
        return proposal
    
    def get_pending_proposals(
        self,
        namespace: Optional[str] = None,
    ) -> List[RefundProposal]:
        """승인 대기 중인 제안 목록."""
        now = utc_now()
        pending = []
        
        for proposal in self._proposals.values():
            if proposal.status == RefundStatus.PROPOSED:
                if proposal.expires_at and now > proposal.expires_at:
                    proposal.status = RefundStatus.EXPIRED
                    continue
                
                if namespace is None or proposal.namespace == namespace:
                    pending.append(proposal)
        
        return pending
```

---

### 8.9 AtomicBudgetConsumer (Q3: 버짓 소진 원자성)

#### 8.9.1 개요

버짓 소진(consume) 연산의 원자성을 보장합니다.
Redis Lock을 사용하여 동시 소진 시 race condition을 방지합니다.

**Code reference:**
- `audit/hash_chain_safety.py` (AtomicMergeSwap, ShardedDateLock)

#### 8.9.2 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/atomic_consumer.py

from dataclasses import dataclass
from typing import Any, Optional
import time
import uuid
import logging

logger = logging.getLogger(__name__)


@dataclass
class ConsumptionResult:
    """버짓 소진 결과."""
    
    success: bool
    """성공 여부."""
    
    consumed_minutes: float
    """소진량 (분)."""
    
    remaining_minutes: float
    """잔여량 (분)."""
    
    multiplier_applied: float
    """적용된 가중치."""
    
    lock_acquired: bool
    """Lock 획득 여부."""
    
    error: Optional[str] = None
    """오류 메시지."""


class AtomicBudgetConsumer:
    """
    원자적 버짓 소진기.
    
    Redis Distributed Lock을 사용하여 버짓 소진의 원자성을 보장합니다.
    동시에 여러 Pod에서 소진 시도 시 race condition을 방지합니다.
    
    Features:
    - Redis SET NX 기반 분산 Lock
    - Lock 획득 실패 시 재시도 (exponential backoff)
    - Lock 자동 만료 (deadlock 방지)
    - 실패 시 graceful degradation (Lock 없이 진행)
    
    Code reference:
        audit/hash_chain_safety.py (AtomicMergeSwap)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.9
    """
    
    LOCK_KEY_PREFIX = "budget:consume:lock:"
    DEFAULT_LOCK_TIMEOUT_SECONDS = 5
    DEFAULT_BLOCKING_TIMEOUT = 2.0
    
    def __init__(
        self,
        redis_client: Any,
        lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        blocking_timeout: float = DEFAULT_BLOCKING_TIMEOUT,
        allow_degraded_mode: bool = True,
    ):
        """
        AtomicBudgetConsumer 초기화.
        
        Args:
            redis_client: Redis 클라이언트
            lock_timeout_seconds: Lock 자동 만료 시간
            blocking_timeout: Lock 획득 대기 최대 시간
            allow_degraded_mode: Lock 실패 시 진행 허용 여부
        """
        self._redis = redis_client
        self._lock_timeout = lock_timeout_seconds
        self._blocking_timeout = blocking_timeout
        self._allow_degraded = allow_degraded_mode
    
    def consume_atomic(
        self,
        namespace: str,
        raw_minutes: float,
        multiplier: float,
        budget_key: str,
    ) -> ConsumptionResult:
        """
        원자적 버짓 소진.
        
        Args:
            namespace: 네임스페이스
            raw_minutes: 원시 소진량 (분)
            multiplier: 적용할 가중치
            budget_key: 버짓 저장 키
        
        Returns:
            ConsumptionResult: 소진 결과
        """
        weighted_minutes = raw_minutes * multiplier
        lock_key = f"{self.LOCK_KEY_PREFIX}{namespace}"
        lock_token = str(uuid.uuid4())
        lock_acquired = False
        
        try:
            # Lock 획득 시도
            lock_acquired = self._acquire_lock(lock_key, lock_token)
            
            if not lock_acquired and not self._allow_degraded:
                return ConsumptionResult(
                    success=False,
                    consumed_minutes=0,
                    remaining_minutes=0,
                    multiplier_applied=multiplier,
                    lock_acquired=False,
                    error="Failed to acquire lock",
                )
            
            if not lock_acquired:
                logger.warning(
                    f"[AtomicConsumer] Degraded mode: proceeding without lock "
                    f"for namespace={namespace}"
                )
            
            # Redis Transaction으로 소진 실행
            remaining = self._execute_consumption(
                budget_key, weighted_minutes
            )
            
            return ConsumptionResult(
                success=True,
                consumed_minutes=weighted_minutes,
                remaining_minutes=remaining,
                multiplier_applied=multiplier,
                lock_acquired=lock_acquired,
            )
            
        except Exception as e:
            logger.error(f"[AtomicConsumer] Consumption failed: {e}")
            return ConsumptionResult(
                success=False,
                consumed_minutes=0,
                remaining_minutes=0,
                multiplier_applied=multiplier,
                lock_acquired=lock_acquired,
                error=str(e),
            )
        finally:
            if lock_acquired:
                self._release_lock(lock_key, lock_token)
    
    def _acquire_lock(self, lock_key: str, lock_token: str) -> bool:
        """Lock 획득 (exponential backoff)."""
        start_time = time.monotonic()
        retry_delay = 0.01  # 10ms
        
        while time.monotonic() - start_time < self._blocking_timeout:
            result = self._redis.set(
                lock_key,
                lock_token,
                nx=True,
                ex=int(self._lock_timeout),
            )
            
            if result:
                return True
            
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 0.1)  # Max 100ms
        
        return False
    
    def _release_lock(self, lock_key: str, lock_token: str) -> None:
        """Lock 해제 (토큰 검증)."""
        # Lua script로 원자적 검증 및 삭제
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            self._redis.eval(lua_script, 1, lock_key, lock_token)
        except Exception as e:
            logger.warning(f"[AtomicConsumer] Lock release failed: {e}")
    
    def _execute_consumption(
        self,
        budget_key: str,
        weighted_minutes: float,
    ) -> float:
        """Redis에서 버짓 소진 실행."""
        # INCRBYFLOAT로 원자적 증가
        new_consumed = self._redis.incrbyfloat(
            f"{budget_key}:consumed",
            weighted_minutes,
        )
        
        # 총 버짓 조회
        total = float(self._redis.get(f"{budget_key}:total") or 0)
        
        return max(0, total - new_consumed)
```

---

### 8.10 CRDTBudgetSynchronizer (Q5: 글로벌 버짓 동기화)

#### 8.10.1 개요

멀티 리전 환경에서 Error Budget 상태를 동기화합니다.
CRDT (Conflict-free Replicated Data Type) 패턴을 사용하여 
최종 일관성(eventual consistency)을 보장합니다.

**Code reference:**
- 프로젝트 내 CRDT 구현 없음 → 새로 설계

#### 8.10.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                  CRDT Budget Synchronization                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Seoul Region                    Tokyo Region                        │
│  ┌───────────────┐               ┌───────────────┐                  │
│  │ Local Budget  │               │ Local Budget  │                  │
│  │ consumed: 30m │               │ consumed: 20m │                  │
│  └───────┬───────┘               └───────┬───────┘                  │
│          │                               │                           │
│          ▼                               ▼                           │
│  ┌───────────────┐               ┌───────────────┐                  │
│  │ G-Counter     │◀─────────────▶│ G-Counter     │                  │
│  │ {seoul: 30}   │   Sync        │ {tokyo: 20}   │                  │
│  └───────────────┘               └───────────────┘                  │
│          │                               │                           │
│          └───────────────┬───────────────┘                           │
│                          ▼                                           │
│                  ┌───────────────┐                                  │
│                  │ Merged State  │                                  │
│                  │ total: 50m    │                                  │
│                  └───────────────┘                                  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

#### 8.10.3 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/crdt_sync.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
import json
import logging

from selfhealing.core.timezone import utc_now

logger = logging.getLogger(__name__)


@dataclass
class GCounterState:
    """
    G-Counter (Grow-only Counter) 상태.
    
    각 리전별 소진량을 독립적으로 추적하고,
    merge 시 각 리전의 최대값을 사용합니다.
    """
    
    counters: Dict[str, float] = field(default_factory=dict)
    """리전별 소진량 {region: consumed_minutes}."""
    
    last_sync: Optional[datetime] = None
    """마지막 동기화 시각."""
    
    version: int = 0
    """버전 (동기화 횟수)."""
    
    def increment(self, region: str, amount: float) -> None:
        """리전별 카운터 증가."""
        self.counters[region] = self.counters.get(region, 0) + amount
    
    def get_total(self) -> float:
        """전체 소진량."""
        return sum(self.counters.values())
    
    def merge(self, other: "GCounterState") -> "GCounterState":
        """
        두 상태 병합 (CRDT merge).
        
        각 리전에 대해 더 큰 값을 취합니다.
        """
        merged_counters = dict(self.counters)
        
        for region, count in other.counters.items():
            if region in merged_counters:
                merged_counters[region] = max(merged_counters[region], count)
            else:
                merged_counters[region] = count
        
        return GCounterState(
            counters=merged_counters,
            last_sync=utc_now(),
            version=max(self.version, other.version) + 1,
        )
    
    def to_dict(self) -> Dict:
        """JSON 직렬화용."""
        return {
            "counters": self.counters,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "version": self.version,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "GCounterState":
        """JSON 역직렬화."""
        last_sync = None
        if data.get("last_sync"):
            last_sync = datetime.fromisoformat(data["last_sync"])
        
        return cls(
            counters=data.get("counters", {}),
            last_sync=last_sync,
            version=data.get("version", 0),
        )


class CRDTBudgetSynchronizer:
    """
    CRDT 기반 멀티 리전 버짓 동기화.
    
    G-Counter CRDT를 사용하여 멀티 리전 환경에서
    Error Budget 소진량을 최종 일관성으로 동기화합니다.
    
    Features:
    - 리전별 독립 소진 (local-first)
    - 비동기 동기화 (eventual consistency)
    - 충돌 없는 병합 (conflict-free merge)
    - 네트워크 분할 내성
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.10
    """
    
    SYNC_KEY_PREFIX = "budget:crdt:state:"
    SYNC_INTERVAL_SECONDS = 30
    
    def __init__(
        self,
        redis_client: Any,
        current_region: str,
        remote_regions: Optional[list] = None,
    ):
        """
        CRDTBudgetSynchronizer 초기화.
        
        Args:
            redis_client: Redis 클라이언트
            current_region: 현재 리전 이름
            remote_regions: 원격 리전 목록
        """
        self._redis = redis_client
        self._region = current_region
        self._remote_regions = remote_regions or []
        self._local_state: Dict[str, GCounterState] = {}
    
    def record_local_consumption(
        self,
        namespace: str,
        consumed_minutes: float,
    ) -> float:
        """
        로컬 소진 기록.
        
        Args:
            namespace: 네임스페이스
            consumed_minutes: 소진량
        
        Returns:
            전체 소진량 (추정치)
        """
        if namespace not in self._local_state:
            self._local_state[namespace] = GCounterState()
        
        state = self._local_state[namespace]
        state.increment(self._region, consumed_minutes)
        
        # 로컬 상태 저장
        self._persist_local_state(namespace, state)
        
        return state.get_total()
    
    def sync_with_remotes(self, namespace: str) -> GCounterState:
        """
        원격 리전과 동기화.
        
        Args:
            namespace: 네임스페이스
        
        Returns:
            병합된 상태
        """
        local_state = self._local_state.get(namespace, GCounterState())
        
        for remote_region in self._remote_regions:
            try:
                remote_state = self._fetch_remote_state(namespace, remote_region)
                if remote_state:
                    local_state = local_state.merge(remote_state)
            except Exception as e:
                logger.warning(
                    f"[CRDTSync] Failed to sync with {remote_region}: {e}"
                )
        
        # 병합된 상태 저장
        self._local_state[namespace] = local_state
        self._persist_local_state(namespace, local_state)
        
        return local_state
    
    def get_global_consumption(self, namespace: str) -> float:
        """
        글로벌 소진량 조회.
        
        마지막 동기화 시점의 값을 반환합니다.
        """
        state = self._local_state.get(namespace)
        if state:
            return state.get_total()
        
        # Redis에서 로드
        state = self._load_local_state(namespace)
        if state:
            self._local_state[namespace] = state
            return state.get_total()
        
        return 0.0
    
    def _persist_local_state(
        self,
        namespace: str,
        state: GCounterState,
    ) -> None:
        """로컬 상태 저장."""
        key = f"{self.SYNC_KEY_PREFIX}{self._region}:{namespace}"
        self._redis.set(key, json.dumps(state.to_dict()))
    
    def _load_local_state(self, namespace: str) -> Optional[GCounterState]:
        """로컬 상태 로드."""
        key = f"{self.SYNC_KEY_PREFIX}{self._region}:{namespace}"
        data = self._redis.get(key)
        if data:
            return GCounterState.from_dict(json.loads(data))
        return None
    
    def _fetch_remote_state(
        self,
        namespace: str,
        remote_region: str,
    ) -> Optional[GCounterState]:
        """원격 리전 상태 조회."""
        # 실제 구현에서는 Cross-region Redis 또는 API 호출
        key = f"{self.SYNC_KEY_PREFIX}{remote_region}:{namespace}"
        data = self._redis.get(key)
        if data:
            return GCounterState.from_dict(json.loads(data))
        return None
```

---

### 8.11 AdminOverrideInvalidator (Q6: Admin Override 충돌 해결)

#### 8.11.1 개요

Admin이 Emergency Level을 수동으로 Override할 때 
캐시된 Multiplier 값과 충돌이 발생할 수 있습니다.
Override 발생 시 모든 관련 캐시를 즉시 무효화합니다.

**Code reference:**
- `namespace_emergency/tracker.py` (invalidate_cache)
- `namespace_emergency/override.py` (AdminOverride 패턴)

#### 8.11.2 구현

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/admin_invalidator.py

from typing import Callable, List, Optional
import logging

logger = logging.getLogger(__name__)


class AdminOverrideInvalidator:
    """
    Admin Override 시 캐시 무효화기.
    
    Admin이 Emergency Level을 수동으로 Override할 때
    모든 관련 캐시를 즉시 무효화하여 일관성을 보장합니다.
    
    Features:
    - Override 이벤트 감지
    - 다중 캐시 대상 무효화
    - 무효화 로깅 및 감사
    
    Code reference:
        namespace_emergency/tracker.py (invalidate_cache)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.11
    """
    
    def __init__(self):
        """AdminOverrideInvalidator 초기화."""
        self._invalidation_targets: List[Callable[[], None]] = []
        self._registered = False
    
    def register_cache_target(self, invalidate_fn: Callable[[], None]) -> None:
        """
        무효화 대상 캐시 등록.
        
        Args:
            invalidate_fn: 캐시 무효화 함수
        """
        self._invalidation_targets.append(invalidate_fn)
    
    def register_event_handler(self) -> None:
        """이벤트 버스에 Override 핸들러 등록."""
        if self._registered:
            return
        
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType
            
            bus = get_event_bus()
            
            # Admin Override 이벤트 구독
            bus.subscribe(
                EventType.ADMIN_OVERRIDE_APPLIED,
                self._on_admin_override,
            )
            bus.subscribe(
                EventType.ADMIN_OVERRIDE_RELEASED,
                self._on_admin_override,
            )
            
            self._registered = True
            logger.info(
                "[AdminOverrideInvalidator] Registered for ADMIN_OVERRIDE events"
            )
        except Exception as e:
            logger.warning(f"[AdminOverrideInvalidator] Registration failed: {e}")
    
    def _on_admin_override(self, event) -> None:
        """Admin Override 이벤트 핸들러."""
        override_type = event.data.get("type", "unknown")
        namespace = event.data.get("namespace")
        admin_user = event.data.get("admin_user", "unknown")
        
        logger.warning(
            f"[AdminOverrideInvalidator] Override detected: "
            f"type={override_type}, namespace={namespace}, admin={admin_user}"
        )
        
        # 모든 캐시 무효화
        self._invalidate_all_caches(
            reason=f"Admin Override: {override_type}",
            namespace=namespace,
            admin_user=admin_user,
        )
    
    def _invalidate_all_caches(
        self,
        reason: str,
        namespace: Optional[str] = None,
        admin_user: Optional[str] = None,
    ) -> None:
        """모든 등록된 캐시 무효화."""
        invalidated_count = 0
        
        for invalidate_fn in self._invalidation_targets:
            try:
                invalidate_fn()
                invalidated_count += 1
            except Exception as e:
                logger.error(f"[AdminOverrideInvalidator] Invalidation failed: {e}")
        
        logger.info(
            f"[AdminOverrideInvalidator] Invalidated {invalidated_count} caches: "
            f"reason={reason}"
        )
        
        # 감사 로그 기록
        self._record_audit_log(
            reason=reason,
            namespace=namespace,
            admin_user=admin_user,
            invalidated_count=invalidated_count,
        )
    
    def _record_audit_log(
        self,
        reason: str,
        namespace: Optional[str],
        admin_user: Optional[str],
        invalidated_count: int,
    ) -> None:
        """감사 로그 기록."""
        try:
            from selfhealing.core.timezone import utc_now
            
            entry = {
                "type": "admin_override_cache_invalidation",
                "timestamp": utc_now().isoformat(),
                "reason": reason,
                "namespace": namespace,
                "admin_user": admin_user,
                "invalidated_cache_count": invalidated_count,
            }
            
            # Hash Chain에 기록 (선택)
            logger.info(f"[AdminOverrideInvalidator] Audit: {entry}")
            
        except Exception as e:
            logger.warning(f"[AdminOverrideInvalidator] Audit log failed: {e}")
    
    def force_invalidate(
        self,
        reason: str,
        admin_user: str,
    ) -> int:
        """
        강제 무효화 (수동 호출용).
        
        Args:
            reason: 무효화 사유
            admin_user: 실행자
        
        Returns:
            무효화된 캐시 수
        """
        count = 0
        for invalidate_fn in self._invalidation_targets:
            try:
                invalidate_fn()
                count += 1
            except Exception as e:
                logger.error(f"[AdminOverrideInvalidator] Force invalidate failed: {e}")
        
        self._record_audit_log(
            reason=f"Force: {reason}",
            namespace=None,
            admin_user=admin_user,
            invalidated_count=count,
        )
        
        return count


# =============================================================================
# Singleton
# =============================================================================

_admin_override_invalidator: Optional[AdminOverrideInvalidator] = None


def get_admin_override_invalidator() -> AdminOverrideInvalidator:
    """AdminOverrideInvalidator 싱글톤 반환."""
    global _admin_override_invalidator
    if _admin_override_invalidator is None:
        _admin_override_invalidator = AdminOverrideInvalidator()
        _admin_override_invalidator.register_event_handler()
    return _admin_override_invalidator
```

---

## 9. 통합 Cap 상수 정의

### 9.1 중앙 상수 (SSOT)

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/constants.py

"""
Error Budget 관련 글로벌 상수.

모든 가중치 관련 Cap은 이 파일에서 중앙 관리됩니다.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §9
"""

# =============================================================================
# 가중치 Cap (SSOT)
# =============================================================================

MAX_CRISIS_MULTIPLIER_CAP: float = 10.0
"""
Crisis Multiplier 최대 Cap.

모든 가중치 결합 후 이 값을 초과할 수 없습니다.
이 값은 shadow_calculator.py와 crisis_multiplier.py 모두에서 사용됩니다.
"""

MAX_DOMAIN_MULTIPLIER: float = 24.0
"""
Domain SLA 기반 최대 가중치.

SLA 1시간 도메인(payment)의 역수 가중치: 24h / 1h = 24x
"""

MAX_LEVEL_MULTIPLIER: float = 5.0
"""
Emergency Level 기반 최대 가중치.

LEVEL_3의 기본 가중치.
"""

# =============================================================================
# 특수 상황 Cap (관리자 승인 필요)
# =============================================================================

MAX_CRITICAL_MULTIPLIER_CAP: float = 50.0
"""
특수 상황용 확장 Cap.

데이터 유실 위기 등 극단적 상황에서만 사용.
반드시 Admin 승인 + PIR 리포트 의무.
"""
```

---

## 10. @domain_tag 데코레이터

### 10.1 구현

```python
# packages/selfhealing-python/src/selfhealing/decorators/domain_tag.py

"""
도메인 태깅 데코레이터.

에러 발생 시 도메인 정보를 자동으로 태깅합니다.
FinOps 도메인별 비용 분석에도 활용됩니다.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §10
"""

from functools import wraps
from typing import Optional
import threading


# Thread-local 도메인 컨텍스트
_domain_context = threading.local()


def get_current_domain() -> Optional[str]:
    """현재 스레드의 도메인 컨텍스트 조회."""
    return getattr(_domain_context, 'domain', None)


def set_current_domain(domain: str) -> None:
    """현재 스레드의 도메인 컨텍스트 설정."""
    _domain_context.domain = domain


def clear_current_domain() -> None:
    """현재 스레드의 도메인 컨텍스트 해제."""
    _domain_context.domain = None


def domain_tag(domain: str):
    """
    도메인 태깅 데코레이터.
    
    함수 실행 중 발생하는 모든 에러에 도메인 정보를 태깅합니다.
    
    Usage:
        @domain_tag("payment")
        def process_payment(order_id: str):
            # 이 함수에서 발생하는 에러는 "payment" 도메인으로 태깅됨
            ...
    
    Args:
        domain: 도메인 이름 (payment, order, inventory 등)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            previous_domain = get_current_domain()
            set_current_domain(domain)
            try:
                return func(*args, **kwargs)
            finally:
                if previous_domain:
                    set_current_domain(previous_domain)
                else:
                    clear_current_domain()
        return wrapper
    return decorator


class DomainContext:
    """
    도메인 컨텍스트 매니저.
    
    with 문과 함께 사용하여 블록 내 도메인을 설정합니다.
    
    Usage:
        with DomainContext("payment"):
            # 이 블록에서 발생하는 에러는 "payment" 도메인으로 태깅됨
            process_payment()
    """
    
    def __init__(self, domain: str):
        self.domain = domain
        self._previous_domain: Optional[str] = None
    
    def __enter__(self):
        self._previous_domain = get_current_domain()
        set_current_domain(self.domain)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._previous_domain:
            set_current_domain(self._previous_domain)
        else:
            clear_current_domain()
        return False
```
