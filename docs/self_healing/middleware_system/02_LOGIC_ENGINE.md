# Self-Healing 로직 엔진

> **Version**: 2.2.0
> **Updated**: 2026-01-02
> **Category**: 비즈니스 로직 및 복원력 엔진

---

## 📋 목차

1. [개요](#1-개요)
2. [연결 서비스](#2-연결-서비스)
3. [유틸리티](#3-유틸리티)
4. [서비스 컴포넌트](#4-서비스-컴포넌트)
5. [Resilience 패턴](#5-resilience-패턴)
6. [Core 컴포넌트](#6-core-컴포넌트)
7. [인터페이스 모듈](#7-인터페이스-모듈)
8. [SLO/SLI 모듈](#8-slosli-모듈)
9. [Provider Registry](#9-provider-registry)

---

## 1. 개요

이 문서는 Self-Healing 시스템의 **비즈니스 로직 및 복원력 엔진**을 다룹니다.

### 1.1 범위

- 핵심 서비스 (CircuitBreaker, DLQ, Replay, RetryHandler 등)
- 자동화 제어 (ErrorBudgetGate, RateLimitCoordinator)
- 인터페이스 정의 (Repository, Cache, TaskQueue)
- Resilience 패턴 (Fallback, Retry, Bulkhead, Backoff)
- Core 컴포넌트 (TLS, Certificate, Pool 관리)
- SLO/SLI 및 Error Budget
- Provider Registry (플러그인 팩토리)

### 1.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       02_LOGIC_ENGINE 범위                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │CircuitBreaker│ │ DLQService   │ │ReplayService │ │ RetryHandler │   │
│  │   Service    │─│(Dead Letter Q)│─│  (재처리)    │─│(지수 백오프)  │   │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘   │
│         │                │                │                │            │
│         └────────────────┼────────────────┼────────────────┘            │
│                          ▼                ▼                              │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                   자동화 제어 Layer                              │    │
│  │  ┌─────────────────────┐  ┌─────────────────────────────────┐ │    │
│  │  │  ErrorBudgetGate    │  │  RateLimitCoordinator           │ │    │
│  │  │  (에러예산 기반 차단) │  │  (Self-DDoS 방지)               │ │    │
│  │  └─────────────────────┘  └─────────────────────────────────┘ │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                          │                                               │
│                          ▼                                               │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                    Interfaces Layer                              │    │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌───────────┐ │    │
│  │  │ Repository  │ │ Cache       │ │ TaskQueue   │ │ WebFrame  │ │    │
│  │  │ Interface   │ │ Interface   │ │ Interface   │ │ Interface │ │    │
│  │  └─────────────┘ └─────────────┘ └─────────────┘ └───────────┘ │    │
│  └────────────────────────────────────────────────────────────────┘    │
│           │                                                              │
│           ▼                                                              │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │              Provider Registry (Factory)                        │    │
│  │              - 플러그인 등록 및 조회                              │    │
│  │              - 기본값 관리                                        │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    03_INFRA_ADAPTER.md (구현체)
```

---

## 2. 연결 서비스

> 미들웨어와 연결된 핵심 서비스

### 2.1 CircuitBreakerService

**경로**: `selfhealing.services.circuit_breaker`

**역할**: 장애 격리 및 빠른 실패

| 상태 | 설명 |
|------|------|
| `CLOSED` | 정상 - 요청 통과 |
| `OPEN` | 차단 - 즉시 실패 반환 |
| `HALF_OPEN` | 테스트 - 일부 요청만 허용 |

**설정**:
- `failure_threshold`: 연속 실패 횟수
- `success_threshold`: 복구에 필요한 성공 횟수
- `timeout`: Open 상태 유지 시간
- `half_open_max_calls`: Half-Open 시 허용 요청 수

**사용법**:
```python
from selfhealing.services import get_circuit_breaker

cb = get_circuit_breaker("payment-service")

if cb.is_available():
    try:
        result = call_payment_service()
        cb.record_success()
    except Exception as e:
        cb.record_failure(e)
        raise
else:
    raise CircuitBreakerOpenException()
```

### 2.2 DLQService

**경로**: `selfhealing.services.dlq_service`

**역할**: 실패한 작업의 안전한 저장 및 관리

| 메서드 | 설명 |
|--------|------|
| `enqueue` | 실패 작업 저장 |
| `dequeue` | 재처리할 작업 조회 |
| `mark_resolved` | 해결 완료 표시 |
| `mark_reviewing` | 검토 중 표시 |
| `get_pending` | 대기 중 작업 조회 |
| `get_stats` | 통계 조회 |

**FailedOperation 필드**:
- `domain`: 도메인 (payment, point, inventory 등)
- `failure_type`: 실패 유형
- `status`: 상태 (pending, reviewing, resolved 등)
- `entity_type`, `entity_id`: 제네릭 엔티티 참조
- `snapshot_data`: JSON 스냅샷
- `retry_count`, `max_retries`: 재시도 정보

### 2.3 ReplayService

**경로**: `selfhealing.services.replay`

**역할**: DLQ 항목 재처리

| 메서드 | 설명 |
|--------|------|
| `replay_single` | 단일 항목 재처리 |
| `replay_batch` | 배치 재처리 |
| `replay_by_domain` | 도메인별 재처리 |
| `schedule_replay` | 예약 재처리 |

**재처리 전략**:
- `immediate`: 즉시 실행
- `scheduled`: 예약 실행
- `manual`: 수동 승인 후 실행

### 2.4 SystemControlService

**경로**: `selfhealing.services.system_control`

**역할**: 시스템 운영 모드 관리

| 모드 | 설명 |
|------|------|
| `NORMAL` | 정상 운영 |
| `EMERGENCY` | 긴급 모드 (보수적 처리) |
| `MAINTENANCE` | 유지보수 모드 |
| `READONLY` | 읽기 전용 모드 |

### 2.5 ChaosController

**경로**: `selfhealing.services.chaos`

**역할**: Chaos Engineering 실험 관리

| 메서드 | 설명 |
|--------|------|
| `create_experiment` | 실험 생성 |
| `approve_experiment` | 실험 승인 |
| `start_experiment` | 실험 시작 |
| `stop_experiment` | 실험 중지 |
| `get_results` | 결과 조회 |

### 2.6 RetryHandler

**경로**: `selfhealing.services.retry_handler`

**역할**: 지수 백오프 기반 재시도 + Self-DDoS 방지 + DLQ 연동

| 컴포넌트 | 설명 |
|----------|------|
| `RetryHandler` | 재시도 로직 실행기 (Exponential Backoff, Jitter, Rate Limit Awareness) |
| `RetryConfig` | max_attempts, backoff_base/max, jitter_percent, 도메인별 설정 |
| `RetryResult` | 재시도 결과 DTO (success, action, attempt, dlq_id) |
| `RetryAction` | 재시도 액션 Enum (RETRY, DLQ, ABORT, SUCCESS) |
| `MaxRetriesExceededError` | 최대 재시도 초과 예외 |
| `@with_retry` | 재시도 로직 데코레이터 |

**핵심 기능**:
- Kill Switch 연동: 시스템 비활성화 시 즉시 실패 반환
- ErrorBudgetGate 연동: 에러 예산 임계치 이하 시 재시도 차단
- Rate Limit Awareness: 429 에러 감지 시 글로벌 쿨다운 설정
- DLQ 자동 이동: max_retries 초과 시 자동으로 DLQ 저장

### 2.7 BackoffCalculator

**경로**: `selfhealing.services.backoff_calculator`

**역할**: 지수 백오프 지연 시간 계산

| 컴포넌트 | 설명 |
|----------|------|
| `BackoffCalculator` | 지수 백오프 계산기 (base^attempt, max cap, jitter) |
| `BackoffConfig` | base, max_delay, jitter_percent, min_delay 설정 |
| `calculate_backoff` | 단일 시도에 대한 지연 시간 계산 함수 |
| `get_calculator_for_domain` | 도메인별 계산기 인스턴스 캐싱 |

**지연 시간 예시** (base=4, max=180s, jitter=25%):
- Attempt 1: ~4s
- Attempt 2: ~16s
- Attempt 3: ~64s
- Attempt 4+: 180s (cap)

### 2.8 RateLimitCoordinator

**경로**: `selfhealing.services.rate_limit_coordinator`

**역할**: 분산 환경 Self-DDoS 방지 코디네이터

| 컴포넌트 | 설명 |
|----------|------|
| `RateLimitCoordinator` | 분산 레이트 리밋 조율기 |
| `RateLimitCoordinatorConfig` | base_delay, max_delay, jitter_percent, backoff_multiplier |
| `RateLimitResult` | 대기 결과 DTO (waited, wait_time, consecutive_429s) |

**핵심 기능**:
- 글로벌 쿨다운: 429 응답 시 모든 워커가 대기
- 분산 상태 공유: Redis/DB/InMemory 자동 선택
- 100% 호환: Redis 없어도 DB 폴백으로 동작

### 2.9 ErrorBudgetGate

**경로**: `selfhealing.services.error_budget_gate`

**역할**: 에러 예산 기반 자동화 제어 게이트 ("위기 상황일수록 인간의 개입을 강제")

| 컴포넌트 | 설명 |
|----------|------|
| `ErrorBudgetGate` | 메인 게이트 - 에러 예산 미달 시 자동화 차단 |
| `GateCheckResult` | 게이트 체크 결과 (allowed, error_budget_percent, threshold_percent) |
| `GateStatus` | 게이트 상태 Enum |
| `AutomationBlockedError` | 자동화 차단 예외 |
| `check_automation_allowed` | 자동화 허용 여부 조건 체크 |
| `require_automation_allowed` | 자동화 허용 여부 체크 (불허 시 예외 발생) |
| `@automation_gate` | 자동화 게이트 데코레이터 |

**설계 철학**: "보고는 자동, 결정은 수동" - 시스템이 대신 하는 것이 아니라 위험할 때 멈추는 설계

---

## 3. 유틸리티

> 서비스에서 사용하는 데코레이터 및 컨텍스트 매니저

### 3.1 Decorators

**경로**: `selfhealing.core.decorators`

| 데코레이터 | 용도 |
|------------|------|
| `@with_circuit_breaker` | CB 자동 적용 |
| `@with_retry` | 재시도 로직 |
| `@with_dlq` | 실패 시 DLQ 저장 |
| `@with_timeout` | 타임아웃 설정 |
| `@with_bulkhead` | 동시성 제한 |

**조합 예시**:
```python
from selfhealing.core.decorators import with_circuit_breaker, with_retry, with_dlq

@with_circuit_breaker(name="payment")
@with_retry(max_attempts=3, backoff=exponential_backoff)
@with_dlq(domain="payment", entity_type="order")
def process_payment(order_id: int):
    ...
```

### 3.2 Context Managers

**경로**: `selfhealing.core.context_managers`

| 컨텍스트 매니저 | 용도 |
|-----------------|------|
| `circuit_breaker_context` | CB 스코프 |
| `retry_context` | 재시도 스코프 |
| `timeout_context` | 타임아웃 스코프 |
| `forensic_context` | 포렌식 데이터 수집 |

### 3.3 Backoff Strategies

**경로**: `selfhealing.core.backoff`

| 컴포넌트 | 설명 |
|----------|------|
| `ExponentialBackoff` | 지수 백오프 (base_delay × multiplier^attempt, 선택적 jitter) |
| `LinearBackoff` | 선형 백오프 (base_delay + increment × attempt) |
| `ConstantBackoff` | 고정 간격 백오프 |
| `DecorrelatedJitterBackoff` | AWS 스타일 비상관 지터 백오프 (이전 지연의 1~3배 랜덤) |
| `get_backoff_calculator` | 전략별 백오프 계산기 팩토리 함수 |

**추가 경로**: `selfhealing.services.backoff_calculator`

| 컴포넌트 | 설명 |
|----------|------|
| `BackoffCalculator` | 도메인별 설정 지원 백오프 계산기 |
| `BackoffConfig` | base, max_delay, jitter_percent, min_delay 설정 |
| `calculate_backoff` | 단일 시도에 대한 지연 시간 계산 함수 |
| `get_calculator_for_domain` | 도메인별 계산기 인스턴스 캐싱 |

---

## 4. 서비스 컴포넌트

> 독립적으로 동작하는 서비스 컴포넌트

### 4.1 EmergencyService

**경로**: `selfhealing.services.emergency`

**역할**: 긴급 상황 대응

| 메서드 | 설명 |
|--------|------|
| `trigger_emergency` | 긴급 모드 활성화 |
| `resolve_emergency` | 긴급 모드 해제 |
| `get_status` | 긴급 상태 조회 |
| `escalate` | 에스컬레이션 |

### 4.2 ErrorBudgetService

**경로**: `selfhealing.services.error_budget`

**역할**: SLO 기반 Error Budget 관리

| 메서드 | 설명 |
|--------|------|
| `get_remaining_budget` | 남은 예산 조회 |
| `consume_budget` | 예산 소비 기록 |
| `reset_budget` | 예산 초기화 (월간) |
| `get_burn_rate` | 소비율 조회 |

**계산 공식**:
```
Error Budget = 1 - SLO
Remaining = Error Budget - (Errors / Total Requests)
Burn Rate = (Consumed Budget / Time Elapsed) × (Total Period / Total Budget)
```

### 4.3 HealthCheckService

**경로**: `selfhealing.services.health`

**역할**: 시스템 헬스 체크

| 메서드 | 설명 |
|--------|------|
| `check_all` | 전체 컴포넌트 체크 |
| `check_database` | DB 연결 체크 |
| `check_cache` | 캐시 연결 체크 |
| `check_queue` | 큐 연결 체크 |
| `get_status` | 상태 요약 조회 |

### 4.4 IdempotencyService

**경로**: `selfhealing.services.idempotency`

**역할**: 멱등성 보장

| 메서드 | 설명 |
|--------|------|
| `check_key` | 키 존재 여부 확인 |
| `store_result` | 결과 저장 |
| `get_result` | 저장된 결과 조회 |
| `cleanup_expired` | 만료 키 정리 |

### 4.5 ForensicContextService

**경로**: `selfhealing.services.forensic`

**역할**: 장애 분석용 컨텍스트 수집

| 수집 데이터 | 설명 |
|-------------|------|
| Request 정보 | 헤더, 바디, 파라미터 |
| Actor 정보 | 사용자, 서비스 |
| 시스템 상태 | 메모리, CPU, 커넥션 |
| 스택 트레이스 | 예외 정보 |

### 4.6 RateLimitService

**경로**: `selfhealing.services.rate_limit`

**역할**: 레이트 리밋 로직

| 알고리즘 | 설명 |
|----------|------|
| `token_bucket` | 토큰 버킷 |
| `sliding_window` | 슬라이딩 윈도우 |
| `leaky_bucket` | 리키 버킷 |

---

## 5. Resilience 패턴

> 복원력 패턴 구현

**경로**: `selfhealing.resilience/`

### 5.1 Fallback

**경로**: `selfhealing.resilience.fallback`

| 컴포넌트 | 용도 |
|---------|------|
| `FallbackChain` | 폴백 체인 (순차 시도) |
| `CachedFallback` | 캐시 기반 폴백 |
| `StaticFallback` | 정적 값 폴백 |
| `GracefulDegradation` | 우아한 기능 저하 |

**사용법**:
```python
from selfhealing.resilience.fallback import FallbackChain

chain = FallbackChain([
    lambda: call_primary_service(),
    lambda: call_secondary_service(),
    lambda: get_cached_value(),
    lambda: DEFAULT_VALUE,
])

result = chain.execute()
```

### 5.2 Retry

**경로**: `selfhealing.resilience.retry`

| 컴포넌트 | 용도 |
|---------|------|
| `RetryPolicy` | 재시도 정책 정의 |
| `RetryExecutor` | 재시도 실행기 |
| `RetryableException` | 재시도 가능 예외 마커 |

**정책 옵션**:
- `max_attempts`: 최대 시도 횟수
- `backoff`: 백오프 전략
- `retry_on`: 재시도할 예외 타입
- `retry_if`: 재시도 조건 함수

### 5.3 Bulkhead

**경로**: `selfhealing.resilience.bulkhead`

| 컴포넌트 | 용도 |
|---------|------|
| `ThreadPoolBulkhead` | 스레드 풀 격리 |
| `SemaphoreBulkhead` | 세마포어 격리 |
| `BulkheadFull` | 격벽 가득 참 예외 |

**설정**:
```python
from selfhealing.resilience.bulkhead import SemaphoreBulkhead

bulkhead = SemaphoreBulkhead(
    name="payment",
    max_concurrent=10,
    max_wait_ms=100,
)

with bulkhead:
    process_payment()
```

### 5.4 Timeout

**경로**: `selfhealing.resilience.timeout`

| 컴포넌트 | 용도 |
|---------|------|
| `TimeoutPolicy` | 타임아웃 정책 |
| `TimeoutExecutor` | 타임아웃 실행기 |
| `TimeoutException` | 타임아웃 예외 |

### 5.5 Bypass Hooks

**경로**: `selfhealing.resilience.bypass`

| 컴포넌트 | 용도 |
|---------|------|
| `BypassCondition` | 바이패스 조건 정의 |
| `bypass_circuit_breaker` | CB 바이패스 |
| `bypass_rate_limit` | 레이트 리밋 바이패스 |

**사용 사례**:
- 헬스 체크 요청
- 내부 모니터링 요청
- 긴급 복구 작업

---

## 6. Core 컴포넌트

> 저수준 핵심 컴포넌트

**경로**: `selfhealing.core/`

### 6.1 TLS Handler

**경로**: `selfhealing.core.tls_handler`

| 컴포넌트 | 용도 |
|---------|------|
| `TLSHandler` | TLS 연결 관리 |
| `TLSConfig` | TLS 설정 |
| `CipherSuiteValidator` | 암호화 스위트 검증 |

### 6.2 Certificate Monitor

**경로**: `selfhealing.core.certificate_monitor`

| 컴포넌트 | 용도 |
|---------|------|
| `CertificateMonitor` | 인증서 만료 모니터링 |
| `CertificateInfo` | 인증서 정보 DTO |
| `ExpiryAlert` | 만료 알림 |

**알림 임계값**:
- 30일 전: INFO
- 14일 전: WARNING
- 7일 전: CRITICAL

### 6.3 Connection Health

**경로**: `selfhealing.core.connection_health`

| 컴포넌트 | 용도 |
|---------|------|
| `ConnectionHealthChecker` | 연결 상태 체크 |
| `ConnectionPool` | 연결 풀 추상화 |
| `HealthStatus` | 상태 Enum |

### 6.4 Pool Watchdog

**경로**: `selfhealing.core.pool_watchdog`

| 컴포넌트 | 용도 |
|---------|------|
| `PoolWatchdog` | 풀 상태 감시 |
| `PoolMetrics` | 풀 메트릭 DTO |
| `PoolAlert` | 풀 알림 |

**감시 메트릭**:
- 사용 중 커넥션 수
- 대기 중 요청 수
- 평균 대기 시간
- 커넥션 생성/반환 비율

---

## 7. 인터페이스 모듈

> 플러그인 가능한 추상 인터페이스

**경로**: `selfhealing.interfaces/`

### 7.1 Repository Interface

**경로**: `selfhealing.interfaces.repository`

```python
class RepositoryInterface(ABC):
    @abstractmethod
    def save(self, entity: T) -> T: ...

    @abstractmethod
    def find_by_id(self, id: str) -> Optional[T]: ...

    @abstractmethod
    def find_all(self, criteria: Dict) -> List[T]: ...

    @abstractmethod
    def delete(self, id: str) -> bool: ...
```

**구현체** ([03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md) 참조):
- `DjangoFailedOperationRepository`
- `SQLAlchemyFailedOperationRepository`

### 7.2 Cache Interface

**경로**: `selfhealing.interfaces.cache`

```python
class CacheInterface(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[Any]: ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int = None) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> bool: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...
```

**구현체**:
- `RedisCacheAdapter`
- `MemoryCacheAdapter`

### 7.3 TaskQueue Interface

**경로**: `selfhealing.interfaces.task_queue`

```python
class TaskQueueInterface(ABC):
    @abstractmethod
    def enqueue(self, task: Callable, *args, **kwargs) -> str: ...

    @abstractmethod
    def get_status(self, task_id: str) -> TaskStatus: ...

    @abstractmethod
    def cancel(self, task_id: str) -> bool: ...

    @abstractmethod
    def schedule(self, task: Callable, delay: int, *args, **kwargs) -> str: ...
```

**구현체**:
- `CeleryTaskQueue`
- `SyncTaskQueue`

### 7.4 WebFramework Interface

**경로**: `selfhealing.interfaces.web_framework`

```python
class WebFrameworkInterface(ABC):
    @abstractmethod
    def create_router(self, prefix: str, **kwargs) -> Any: ...

    @abstractmethod
    def add_route(self, router, path: str, method: HttpMethod, handler: Callable): ...

    @abstractmethod
    def get_request_context(self, request) -> RequestContext: ...
```

**구현체**:
- `FastAPIAdapter`
- `DjangoAdapter`

### 7.5 Config Interface

**경로**: `selfhealing.interfaces.config`

```python
class ConfigInterface(ABC):
    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any: ...

    @abstractmethod
    def set(self, key: str, value: Any) -> bool: ...

    @abstractmethod
    def reload(self) -> bool: ...
```

### 7.6 RateLimit Interface

**경로**: `selfhealing.interfaces.rate_limit`

```python
class RateLimitInterface(ABC):
    @abstractmethod
    def is_allowed(self, key: str) -> bool: ...

    @abstractmethod
    def get_remaining(self, key: str) -> int: ...

    @abstractmethod
    def reset(self, key: str) -> bool: ...
```

### 7.7 Audit Interface

**경로**: `selfhealing.interfaces.audit`

```python
class AuditInterface(ABC):
    @abstractmethod
    def log(self, entry: AuditEntry) -> bool: ...

    @abstractmethod
    def query(self, criteria: AuditCriteria) -> List[AuditEntry]: ...
```

### 7.8 Alert Interface

**경로**: `selfhealing.interfaces.alert`

| 컴포넌트 | 용도 |
|---------|------|
| `AlertSeverity` | 알림 심각도 Enum |
| `AlertCategory` | 알림 카테고리 Enum |
| `Alert` | 알림 데이터 클래스 |
| `AlertAdapter` | 알림 어댑터 ABC |

---

## 8. SLO/SLI 모듈

> Service Level Objectives/Indicators 정의

**경로**: `selfhealing.slo`

### 8.1 SLI 타입

```python
class SLI(Enum):
    AVAILABILITY = "availability"       # 가용성 (성공 비율)
    LATENCY_P50 = "latency_p50"        # 레이턴시 50분위
    LATENCY_P90 = "latency_p90"        # 레이턴시 90분위
    LATENCY_P99 = "latency_p99"        # 레이턴시 99분위
    ERROR_RATE = "error_rate"          # 에러율
    THROUGHPUT = "throughput"          # 처리량
    CUSTOM = "custom"                   # 사용자 정의
```

### 8.2 SLO 정의

```python
@dataclass
class SLO:
    name: str
    sli: SLI
    target: float           # 목표값 (예: 0.999)
    window: timedelta       # 측정 윈도우 (예: 30일)
    warning_threshold: float  # 경고 임계값
```

### 8.3 Error Budget

```python
@dataclass
class ErrorBudget:
    slo: SLO
    total_requests: int
    failed_requests: int

    @property
    def remaining(self) -> float:
        budget = 1 - self.slo.target  # 예: 0.001 for 99.9%
        consumed = self.failed_requests / self.total_requests
        return budget - consumed

    @property
    def burn_rate(self) -> float:
        # 현재 소비율 대비 허용 소비율
        ...
```

### 8.4 Drift Config

**경로**: `selfhealing.models.drift_config`

| 임계값 | 값 | 동작 |
|--------|-----|------|
| `warning_threshold` | 5% | 경고, 로그만 기록 |
| `critical_threshold` | 20% | 심각, 알림 발송 |
| `incident_threshold` | 50% | 인시던트, 이벤트 유실 의심 |

---

## 9. Provider Registry

> 플러그인 컴포넌트 중앙 레지스트리

**경로**: `selfhealing.factory`

### 9.1 레지스트리 메서드

| 메서드 | 용도 |
|--------|------|
| `register_cache` | 캐시 프로바이더 등록 |
| `register_queue` | 태스크 큐 등록 |
| `register_failed_operation_repo` | DLQ 저장소 등록 |
| `register_circuit_breaker_repo` | CB 상태 저장소 등록 |
| `register_security_repo` | 보안 인시던트 저장소 등록 |
| `register_audit_adapter` | 감사 로그 어댑터 등록 |
| `get_cache` | 캐시 프로바이더 조회 |
| `get_queue` | 태스크 큐 조회 |
| `get_failed_operation_repo` | DLQ 저장소 조회 |
| `get_circuit_breaker_repo` | CB 상태 저장소 조회 |
| `get_security_repo` | 보안 인시던트 저장소 조회 |
| `get_audit_adapter` | 감사 로그 어댑터 조회 |
| `set_default_cache` | 기본 캐시 설정 |
| `set_default_queue` | 기본 큐 설정 |
| `set_default_repo` | 기본 저장소 설정 |
| `set_default_audit` | 기본 감사 어댑터 설정 |

### 9.2 기본값

| 타입 | 기본 프로바이더 |
|------|----------------|
| Cache | `memory` |
| Queue | `sync` |
| Repository | `django` |
| Audit | `file` |

### 9.3 사용법

```python
from selfhealing.factory import ProviderRegistry

# 기본 프로바이더 조회
cache = ProviderRegistry.get_cache()
queue = ProviderRegistry.get_queue()

# 특정 프로바이더 조회
cache = ProviderRegistry.get_cache("redis")
queue = ProviderRegistry.get_queue("celery")

# 커스텀 프로바이더 등록
class CustomCacheAdapter(CacheInterface):
    ...

ProviderRegistry.register_cache("custom", CustomCacheAdapter)

# 기본값 변경
ProviderRegistry.set_default_cache("redis")
```

---

## 📎 관련 문서

- [00_INDEX.md](00_INDEX.md) - 문서 인덱스
- [01_MIDDLEWARE_GATEWAY.md](01_MIDDLEWARE_GATEWAY.md) - 미들웨어 게이트웨이
- [03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md) - 인프라 어댑터
- [04_AUTONOMOUS_OPS.md](04_AUTONOMOUS_OPS.md) - 자율 운영 시스템
