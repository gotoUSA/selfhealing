# 224. Policy Composition 전환 마스터 플랜

## 1. 개요

현재 Self-Healing 시스템의 resilience 패턴들(Retry, Circuit Breaker, Bulkhead, Hedging, Fallback)은
각각 잘 구현되어 있으나, **패턴 간 조합이 하드코딩**되어 소비자가 선언적으로 조합을 변경할 수 없다.

이 문서는 **resilience4j Decorators / Polly PolicyWrap** 스타일의 선언적 Policy Composition 전환을 위한 마스터 플랜이다.

## 2. 핵심 질문: 모든 시스템을 Policy Composition으로 바꿔야 하는가?

### 결론: **아니오.** 6개 패턴만 전환 대상이며, 나머지는 인프라로 유지한다.

### 2.1 전환 대상 (POLICY_CANDIDATE) — 함수 호출을 래핑하는 패턴

| 패턴 | 현재 위치 | 전환 이유 |
|------|-----------|-----------|
| **Retry** | `services/retry_handler/handler.py` | `execute()` 내부에 6개 외부 패턴이 하드코딩 (Kill Switch, ErrorBudgetGate, RateLimit, Throttle, AdaptiveRetryBudget, DLQ). 소비자가 조합 변경 불가 |
| **Circuit Breaker** | `services/circuit_breaker/service.py` | 독립적이나, Retry/Fallback과 선언적 조합 불가 |
| **Bulkhead** | `resilience/bulkhead/` | 독립적이나, 다른 패턴과 조합 시 Hedging 내부 하드코딩으로만 가능 |
| **Fallback** | `core/fallback_strategy.py` | `SimpleFallback`, `PartitionAwareFallback` 존재하나, Retry/CB 실패 후 자동 전환 미지원 |
| **Hedging** | `core/hedging/` | Bulkhead/Backpressure가 내부 하드코딩. `FallbackStrategy` 상속 커플링 |
| **Timeout** | 현재 독립 구현 없음 | `DegradedModeHandler`의 `DEFAULT_TIMEOUT_MS` 상수만 존재. Policy로 신규 생성 필요 |

### 2.2 전환 비대상 (INFRASTRUCTURE) — 인프라로 유지

| 컴포넌트 | 현재 위치 | 유지 이유 | Policy와의 관계 |
|----------|-----------|-----------|-----------------|
| **Kill Switch** | `services/system_control.py` | 전체 시스템 on/off 제어 평면 | 모든 Policy 실행 전 전역 체크 |
| **DLQ** | `services/dlq/` | 실패 작업 저장소 | Policy Pipeline의 failure sink |
| **EventBus** | `services/event_bus/` | 이벤트 발행/구독 메시징 | Policy 상태 변경 옵저버 훅 |
| **Decision Engine** | `core/decision_engine.py` | 메트릭 기반 파라미터 자동 조정 | Policy 설정의 외부 피드백 루프 |
| **Audit** | `services/audit/` | 감사 로깅 | Policy 실행 이력 기록 훅 |
| **Rate Limit Channel** | `services/rate_limit/` | Kafka 분산 429 이벤트 전파 | 인프라 통신 레이어 |

### 2.3 가드(GUARD) — Policy Pipeline의 pre-check 훅으로 연결

| 컴포넌트 | 현재 위치 | 처리 방식 |
|----------|-----------|-----------|
| **ErrorBudgetGate** | `services/error_budget_gate/` | Guard hook으로 Policy Pipeline에 참조 (`add_guard()`) |
| **Backpressure Middleware** | `api/django/middleware/backpressure.py` | HTTP 미들웨어로 유지, Policy와 별도 레이어 |
| **Load Shedding** | `circuit_breaker/load_shedding/` | Guard hook 또는 인프라 유지 |
| **Traffic Gate** | `scaling/traffic_gate.py` | 복합 게이트, Bulkhead 부분만 Policy 후보 |

## 3. 현재 하드코딩 의존성 현황

### 3.1 RetryHandler — **가장 심각한 커플링 (12건)**

`services/retry_handler/handler.py`의 `execute()` 실행 흐름:

```
[고정 순서, 코드에 하드코딩됨]

Kill Switch 체크 (L419)
    ↓
ErrorBudgetGate 체크 (L428)
    ↓
┌─ while 루프 ──────────────────────┐
│  AdaptiveRetryBudget 확인 (L463)  │
│       ↓                           │
│  RateLimit 대기 (L468)            │
│       ↓                           │
│  함수 실행                         │
│       ↓ (실패 시)                  │
│  429 감지 → RateLimit 처리 (L505) │
│       ↓                           │
│  Throttle-aware backoff (L510)    │
│       ↓                           │
│  Full Stop → DLQ 이동 (L515)     │
└───────────────────────────────────┘
```

**참조 파일**: `handler.py` L419-L535

하드코딩 의존성 상세:

| 의존 대상 | 위치 | import 방식 | 제거 가능 |
|-----------|------|------------|----------|
| SystemControlManager (Kill Switch) | L29 lazy import | `_is_system_enabled()` | ✅ `Callable[[], bool]`로 추출 |
| ErrorBudgetGate | L157 lazy import | `_check_error_budget_gate()` | ✅ `Optional[Callable]`로 추출 |
| RateLimitCoordinator | L118 lazy import | property | ✅ 생성자 주입으로 변경 |
| ThrottleAwareBackoffCalculator | L89 lazy import | 조건부 생성 | ✅ BackoffStrategy 인터페이스로 |
| AdaptiveRetryBudget | L107 lazy import | 직접 생성 | ✅ 생성자 주입 |
| audit_helpers.log_retry_audit | L138 lazy import | Fail-Open | ✅ Observer 훅으로 추출 |
| Prometheus metrics | L290 lazy import | ImportError 무시 | ✅ 메트릭 인터페이스로 |
| dlq_service.store_to_dlq | L587 lazy import | 실패 시 호출 | ✅ DLQ Protocol 주입 |

### 3.2 HedgingStrategy — **Bulkhead/EventBus 하드코딩 (3건)**

`core/hedging/strategy.py`:

| 의존 대상 | 위치 | 제거 가능 |
|-----------|------|----------|
| FallbackStrategy (상속) | L13 정적 import | ⚠️ 상속 관계 → 공통 Result 타입 분리 필요 |
| BulkheadRegistry | L97 lazy import | ✅ optional 연동, 인터페이스 추출 가능 |
| EventBus | L108 lazy import | ✅ optional 구독, 훅으로 추출 가능 |

### 3.3 독립 패턴 (크로스-패턴 의존 0건)

| 패턴 | 위치 | 상태 |
|------|------|------|
| Circuit Breaker Service | `services/circuit_breaker/service.py` | **독립** — 자체 패키지 내부만 참조 |
| Circuit Breaker Protection | `services/circuit_breaker/protection.py` | **독립** |
| Bulkhead Base | `resilience/bulkhead/base.py` | **완전 독립** — 표준 라이브러리만 사용 |
| Hedging Executor | `core/hedging/executor.py` | **독립** — 자체 패키지 내부만 참조 |
| DegradedModeHandler | `core/degraded_mode_handler.py` | **완전 독립** |

## 4. 목표 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                  소비자(Shopping 등)                       │
│                                                          │
│  policy = compose(                                       │
│      timeout(seconds=5),                                 │
│      bulkhead("payment_db", max_concurrent=10),          │
│      circuit_breaker("pg_api", failure_threshold=5),     │
│      retry(max_attempts=3, backoff=exponential()),       │
│      fallback(cached_response),                          │
│  )                                                       │
│                                                          │
│  result = policy.execute(call_payment_api, order_id=123) │
└───────────────┬─────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────┐
│              Policy Composition Layer (NEW)               │
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │TimeoutPol│→│BulkheadPol│→│  CBPol   │→│RetryPol  │→… │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│                                                          │
│  Guards: [ErrorBudgetGate, LoadShedding]                 │
│  Hooks:  [Audit, Metrics, EventBus]                      │
│  Sinks:  [DLQ]                                           │
└───────────────┬─────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────┐
│              Infrastructure Layer (유지)                   │
│                                                          │
│  Kill Switch │ DLQ │ EventBus │ Decision Engine │ Audit  │
└─────────────────────────────────────────────────────────┘
```

## 5. 문서 구성

| 문서번호 | 제목 | 내용 |
|---------|------|------|
| **224** | Policy Composition 마스터 플랜 (본 문서) | 전환 범위, 대상/비대상 분류, 아키텍처 개요 |
| **225** | Policy 인터페이스 설계 | `ResiliencePolicy` Protocol, `PolicyResult`, Guard/Hook/Sink 인터페이스 |
| **226** | RetryPolicy 전환 | `handler.py` 하드코딩 12건 분리, 순수 Retry 로직 추출 |
| **227** | CircuitBreakerPolicy 전환 | toggle→state-based 래핑, `should_allow()` 기반 Policy 인터페이스 |
| **228** | BulkheadPolicy 전환 | `bulkhead/base.py` 기반, 리소스 획득/해제 Policy 래핑 |
| **229** | FallbackPolicy 전환 | `FallbackStrategy` ABC 리팩토링, 독립 Policy 추출 |
| **230** | HedgingPolicy 전환 | Bulkhead 하드코딩 분리, 커스텀 내부 Policy 주입 지원 |
| **231** | PolicyComposer 조합 엔진 | `compose()` 빌더, 실행 순서 제어, Guard/Hook/Sink 연결 |

## 6. 전환 원칙

1. **기존 코드 유지**: 개별 패턴 구현은 그대로 두고, Policy 래퍼를 위에 씌운다
2. **하위 호환성**: 기존 `@with_retry`, `@bulkhead`, `@hedged` 데코레이터는 내부적으로 Policy를 사용하도록 점진 전환
3. **인프라 분리**: Kill Switch, DLQ, EventBus, Audit는 Policy가 아닌 Hook/Guard/Sink로 참조
4. **소비자 자유도**: 소비자가 필요한 패턴만 선택적으로 조합 가능
5. **Hedging 커스터마이징**: Hedging 내부의 Bulkhead/Backpressure 연동은 내부 하위 정책 주입 방식으로 지원

## 7. 전환 순서 (권장)

```
Phase 1: 인터페이스 정의 (225)
    ↓
Phase 2: 독립 패턴 Policy 래핑 — 의존성 0건인 것부터
    - BulkheadPolicy (228) ← bulkhead/base.py가 완전 독립
    - FallbackPolicy (229) ← SimpleFallback이 독립적
    - CircuitBreakerPolicy (227) ← service.py가 독립적
    ↓
Phase 3: 복잡 패턴 Policy 래핑
    - RetryPolicy (226) ← 하드코딩 12건 분리 필요
    - HedgingPolicy (230) ← FallbackStrategy 상속 분리 필요
    ↓
Phase 4: 조합 엔진 구현
    - PolicyComposer (231)
    ↓
Phase 5: 기존 데코레이터 마이그레이션
    - @with_retry → RetryPolicy 내부 사용
    - @bulkhead → BulkheadPolicy 내부 사용
    - @hedged → HedgingPolicy 내부 사용
```
