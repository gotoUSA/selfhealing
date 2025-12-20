# Self-Healing System 아키텍처

> 이 문서는 Self-Healing 시스템의 전체 아키텍처와 컴포넌트 간 관계를 설명합니다.

## 📋 목차

1. [시스템 계층 구조](#1-시스템-계층-구조)
2. [컴포넌트 다이어그램](#2-컴포넌트-다이어그램)
3. [데이터 흐름](#3-데이터-흐름)
4. [서비스 의존성](#4-서비스-의존성)
5. [Repository 패턴](#5-repository-패턴)
6. [패키지 아키텍처](#6-패키지-아키텍처)

---

## 1. 시스템 계층 구조

Self-Healing 시스템은 3개의 주요 계층으로 구성됩니다:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Presentation Layer                              │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│   │  REST API       │  │  Admin Panel    │  │  Celery Tasks          │ │
│   │  (Views)        │  │  (Django Admin) │  │  (Background Jobs)     │ │
│   └────────┬────────┘  └────────┬────────┘  └───────────┬─────────────┘ │
└────────────┼────────────────────┼───────────────────────┼───────────────┘
             │                    │                       │
             ▼                    ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Service Layer                                   │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                    ControlAPIService                             │   │
│   │   (통합 제어 인터페이스)                                          │   │
│   └─────────────────────────────┬───────────────────────────────────┘   │
│                                 │                                        │
│   ┌──────────────┬──────────────┼──────────────┬──────────────┐         │
│   ▼              ▼              ▼              ▼              ▼         │
│ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────┐ │
│ │ Circuit    │ │ DLQ        │ │ Replay     │ │ Retry      │ │Idempo- │ │
│ │ Breaker    │ │ Service    │ │ Service    │ │ Handler    │ │tency   │ │
│ │ Service    │ │            │ │            │ │            │ │Service │ │
│ └──────┬─────┘ └──────┬─────┘ └──────┬─────┘ └──────┬─────┘ └────┬───┘ │
│        │              │              │              │            │      │
│        │              │              │              │            │      │
│   ┌────┴──────────────┴──────────────┴──────────────┴────────────┴───┐  │
│   │                    Backoff Calculator                            │  │
│   └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
             │                    │                       │
             ▼                    ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Repository Layer                                │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │               Repository Interfaces (Abstraction)                │   │
│   │   ┌────────────────────────┐  ┌────────────────────────────┐    │   │
│   │   │ CircuitBreakerState    │  │ FailedOperation            │    │   │
│   │   │ Repository             │  │ Repository                 │    │   │
│   │   └────────────────────────┘  └────────────────────────────┘    │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                 │                                        │
│                                 ▼                                        │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │               Django Adapters (Implementation)                   │   │
│   │   ┌────────────────────────┐  ┌────────────────────────────┐    │   │
│   │   │ DjangoCircuitBreaker   │  │ DjangoFailedOperation      │    │   │
│   │   │ StateRepository        │  │ Repository                 │    │   │
│   │   └────────────────────────┘  └────────────────────────────┘    │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
             │                    │                       │
             ▼                    ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Persistence Layer                               │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                     Django ORM / PostgreSQL                      │   │
│   │   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │   │
│   │   │CircuitBreaker│  │FailedOper-  │  │ FailedExternal     │  │   │
│   │   │State         │  │ation        │  │ Request            │  │   │
│   │   └──────────────┘  └──────────────┘  └──────────────────────┘  │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 컴포넌트 다이어그램

### 2.1 핵심 서비스 컴포넌트

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Self-Healing Services                            │
│                                                                          │
│  ┌──────────────────────┐         ┌──────────────────────┐              │
│  │  CircuitBreakerService │◄───────│  ControlAPIService   │              │
│  │  ─────────────────────│        │  ─────────────────────│              │
│  │  • force_open()       │        │  • execute_control()  │              │
│  │  • force_close()      │        │  • get_status()       │              │
│  │  • should_allow()     │        │  • block_service()    │              │
│  │  • record_failure()   │        │  • allow_service()    │              │
│  │  • record_success()   │        │  • reset_service()    │              │
│  └──────────┬───────────┘         └──────────┬───────────┘              │
│             │                                 │                          │
│             │    ┌────────────────────────────┘                          │
│             │    │                                                       │
│             ▼    ▼                                                       │
│  ┌──────────────────────┐         ┌──────────────────────┐              │
│  │     DLQService       │◄────────│   ReplayService      │              │
│  │  ─────────────────────│        │  ─────────────────────│              │
│  │  • store_failure()    │        │  • replay_single()    │              │
│  │  • get_pending()      │        │  • batch_replay()     │              │
│  │  • update_status()    │        │  • replay_on_circuit_ │              │
│  │  • get_by_domain()    │        │    close()            │              │
│  └──────────────────────┘         └──────────────────────┘              │
│             ▲                                ▲                           │
│             │                                │                           │
│  ┌──────────┴───────────┐         ┌──────────┴───────────┐              │
│  │    RetryHandler      │         │   ReplayHandler      │              │
│  │  ─────────────────────│        │   (Abstract)         │              │
│  │  • execute()          │        │  ─────────────────────│              │
│  │  • should_retry()     │        │  • replay()          │              │
│  │  • get_next_delay()   │        │  • can_replay()      │              │
│  └──────────┬───────────┘         └──────────────────────┘              │
│             │                                ▲                           │
│             ▼                                │                           │
│  ┌──────────────────────┐         ┌──────────┴───────────┐              │
│  │  BackoffCalculator   │         │  PaymentReplayHandler│              │
│  │  ─────────────────────│        │  PointReplayHandler  │              │
│  │  • calculate()        │        │  InventoryReplay...  │              │
│  │  • get_delays_seq()   │        │  WebhookReplay...    │              │
│  └──────────────────────┘         └──────────────────────┘              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 모델 관계도

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            Model Relationships                           │
│                                                                          │
│  ┌──────────────────────┐                                               │
│  │   CircuitBreakerState │                                               │
│  │  ─────────────────────│                                               │
│  │  service_name (PK)    │                                               │
│  │  state: enum          │ ◄──── closed / open / half_open              │
│  │  failure_count: int   │                                               │
│  │  success_count: int   │                                               │
│  │  opened_at: datetime  │                                               │
│  │  manually_controlled  │                                               │
│  │  controlled_by: FK    │───────►  User                                 │
│  │  control_reason       │                                               │
│  └──────────────────────┘                                               │
│                                                                          │
│  ┌──────────────────────┐         ┌──────────────────────┐              │
│  │   FailedOperation    │         │ FailedExternalRequest│              │
│  │  ─────────────────────│        │  ─────────────────────│              │
│  │  id (PK)              │        │  id (PK)             │              │
│  │  domain: enum         │        │  payment: FK ────────│──► Payment   │
│  │  failure_type: str    │        │  order: FK ──────────│──► Order     │
│  │  status: enum         │        │  user: FK ───────────│──► User      │
│  │  entity_type: str     │        │  payment_key: str    │              │
│  │  entity_id: str       │        │  amount: decimal     │              │
│  │  user: FK ────────────│──► User│  failure_type: enum  │              │
│  │  snapshot_data: JSON  │        │  status: enum        │              │
│  │  error_code: str      │        │  request_data: JSON  │              │
│  │  error_message: text  │        │  response_data: JSON │              │
│  │  retry_count: int     │        │  metadata: JSON      │              │
│  │  request_data: JSON   │        │  expires_at: datetime│              │
│  │  response_data: JSON  │        └──────────────────────┘              │
│  │  metadata: JSON       │                                               │
│  │  resolved_at: datetime│                                               │
│  │  resolved_by: FK ─────│───────► User                                  │
│  └──────────────────────┘                                               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 데이터 흐름

### 3.1 실패 처리 흐름

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Failure Processing Flow                            │
│                                                                           │
│   [External API Call]                                                     │
│         │                                                                 │
│         ▼                                                                 │
│   ┌───────────────┐     Yes    ┌───────────────┐                         │
│   │ Circuit Open? │──────────► │ Return Error  │                         │
│   └───────┬───────┘            │ (Fast Fail)   │                         │
│           │ No                 └───────────────┘                         │
│           ▼                                                              │
│   ┌───────────────┐                                                      │
│   │ Execute Call  │                                                      │
│   └───────┬───────┘                                                      │
│           │                                                              │
│      ┌────┴────┐                                                         │
│   Success    Failure                                                     │
│      │          │                                                        │
│      ▼          ▼                                                        │
│ ┌──────────┐  ┌───────────────┐                                         │
│ │ Record   │  │ Retryable?    │                                         │
│ │ Success  │  └───────┬───────┘                                         │
│ └────┬─────┘      ┌───┴───┐                                             │
│      │         Yes│       │No                                            │
│      ▼            ▼       ▼                                              │
│ ┌──────────┐  ┌──────────┐  ┌──────────────────┐                        │
│ │ Circuit  │  │ Calculate│  │ Store to DLQ     │                        │
│ │ Half-Open│  │ Backoff  │  │ (Non-retryable)  │                        │
│ │ →Closed? │  └────┬─────┘  └──────────────────┘                        │
│ └──────────┘       │                                                     │
│                    ▼                                                     │
│            ┌───────────────┐     ┌──────────────────┐                   │
│            │ Max Retries?  │ Yes │ Store to DLQ     │                   │
│            └───────┬───────┘────►│ (Exhausted)      │                   │
│                    │ No          └──────────────────┘                   │
│                    ▼                                                     │
│            ┌───────────────┐                                            │
│            │ Schedule      │                                            │
│            │ Retry Task    │                                            │
│            └───────────────┘                                            │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Circuit Breaker 상태 전환

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Circuit Breaker State Machine                          │
│                                                                           │
│                              ┌──────────┐                                │
│                    ┌────────►│  CLOSED  │◄──────────┐                    │
│                    │         └────┬─────┘           │                    │
│                    │              │                 │                    │
│                    │         Failure recorded      │                    │
│                    │         (failure_count++)     │                    │
│                    │              │                 │                    │
│             success_count        │                 │                    │
│           >= success_threshold    │                 │                    │
│                    │              ▼                 │                    │
│                    │    ┌──────────────────┐       │                    │
│                    │    │ failure_count >= │       │                    │
│                    │    │ failure_threshold│       │                    │
│                    │    └────────┬─────────┘       │                    │
│                    │             │ Yes             │                    │
│                    │             ▼                 │                    │
│            ┌───────┴────┐    ┌──────────┐         │                    │
│            │ HALF_OPEN  │◄───│   OPEN   │         │                    │
│            └──────┬─────┘    └────┬─────┘         │                    │
│                   │               │               │                    │
│              Failure?        recovery_timeout     │                    │
│              (back to OPEN)      elapsed?         │                    │
│                   │               │               │                    │
│                   └──────────────►│               │                    │
│                                   │               │                    │
│                   Success in HALF_OPEN            │                    │
│                   (success_count++)               │                    │
│                          └────────────────────────┘                    │
│                                                                          │
│   Manual Control:                                                        │
│   • force_open()  → Any State → OPEN (manually_controlled=True)         │
│   • force_close() → Any State → CLOSED (manually_controlled=False)      │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 서비스 의존성

### 4.1 의존성 그래프

```python
# 서비스 초기화 순서
1. Configuration (config.py)
   └── All services depend on this

2. BackoffCalculator
   └── No dependencies

3. IdempotencyService
   └── Configuration

4. RetryHandler
   ├── Configuration
   └── BackoffCalculator

5. CircuitBreakerService
   ├── Configuration
   └── CircuitBreakerStateRepository

6. DLQService
   ├── Configuration
   └── FailedOperationRepository

7. ReplayService
   ├── DLQService
   ├── CircuitBreakerService
   └── ReplayHandlers (domain-specific)

8. ControlAPIService
   ├── CircuitBreakerService
   ├── DLQService
   └── ReplayService
```

### 4.2 Factory 패턴 사용

```python
# shopping/services/self_healing/factory.py

def get_circuit_breaker_service() -> CircuitBreakerService:
    """싱글톤 패턴으로 CircuitBreakerService 인스턴스 반환"""
    ...

def get_dlq_service() -> DLQService:
    """싱글톤 패턴으로 DLQService 인스턴스 반환"""
    ...

def get_replay_service() -> ReplayService:
    """싱글톤 패턴으로 ReplayService 인스턴스 반환"""
    ...

def get_control_api_service() -> ControlAPIService:
    """싱글톤 패턴으로 ControlAPIService 인스턴스 반환"""
    ...
```

---

## 5. Repository 패턴

### 5.1 추상 인터페이스

```python
# shopping/services/self_healing/interfaces/repositories.py

class CircuitBreakerStateRepository(ABC):
    """Circuit Breaker 상태 저장소 인터페이스"""
    
    @abstractmethod
    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """서비스별 상태 조회 또는 생성"""
        
    @abstractmethod
    def update_state(
        self, 
        service_name: str, 
        state: str,
        **kwargs
    ) -> CircuitBreakerStateData:
        """상태 업데이트"""
        
    @abstractmethod
    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """실패 기록"""
        
    @abstractmethod
    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """성공 기록"""


class FailedOperationRepository(ABC):
    """DLQ 저장소 인터페이스"""
    
    @abstractmethod
    def create(self, **kwargs) -> FailedOperationData:
        """DLQ 엔트리 생성"""
        
    @abstractmethod
    def get_by_id(self, dlq_id: int) -> Optional[FailedOperationData]:
        """ID로 조회"""
        
    @abstractmethod
    def get_pending_by_domain(
        self, 
        domain: str, 
        limit: int = 100
    ) -> List[FailedOperationData]:
        """도메인별 대기 중인 항목 조회"""
        
    @abstractmethod
    def update_status(
        self, 
        dlq_id: int, 
        status: str, 
        **kwargs
    ) -> Optional[FailedOperationData]:
        """상태 업데이트"""
```

### 5.2 Django 어댑터 구현

```python
# shopping/services/self_healing/adapters/django_repositories.py

class DjangoCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """Django ORM 기반 Circuit Breaker 저장소 구현"""
    
    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        obj, created = CircuitBreakerState.objects.get_or_create(
            service_name=service_name,
            defaults={
                'state': 'closed',
                'failure_count': 0,
                'success_count': 0,
            }
        )
        return self._to_data(obj)
    
    def _to_data(self, obj: CircuitBreakerState) -> CircuitBreakerStateData:
        """ORM 객체를 DTO로 변환"""
        return CircuitBreakerStateData(
            service_name=obj.service_name,
            state=obj.state,
            failure_count=obj.failure_count,
            ...
        )


class DjangoFailedOperationRepository(FailedOperationRepository):
    """Django ORM 기반 DLQ 저장소 구현"""
    
    def create(self, **kwargs) -> FailedOperationData:
        obj = FailedOperation.objects.create(**kwargs)
        return self._to_data(obj)
```

### 5.3 테스트용 In-Memory 어댑터

```python
# packages/selfhealing-python/src/selfhealing/adapters/memory/

class InMemoryCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """테스트용 인메모리 저장소"""
    
    def __init__(self):
        self._states: Dict[str, CircuitBreakerStateData] = {}
    
    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        if service_name not in self._states:
            self._states[service_name] = CircuitBreakerStateData(
                service_name=service_name,
                state='closed',
            )
        return self._states[service_name]
```

---

## 6. 패키지 아키텍처

### 6.1 레거시 vs 신규 패키지

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Package Architecture                              │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                    Application Layer                             │   │
│   │                 (shopping/views, tasks, etc.)                    │   │
│   └─────────────────────────────┬───────────────────────────────────┘   │
│                                 │                                        │
│              ┌──────────────────┴──────────────────┐                    │
│              │                                      │                    │
│              ▼                                      ▼                    │
│   ┌─────────────────────┐            ┌─────────────────────────┐        │
│   │  Legacy Package     │            │  New Package            │        │
│   │  (Django-coupled)   │  migrate   │  (Framework-agnostic)   │        │
│   │  ─────────────────  │ ─────────► │  ───────────────────    │        │
│   │  shopping/services/ │            │  packages/selfhealing-  │        │
│   │  self_healing/      │            │  python/                │        │
│   │                     │            │                         │        │
│   │  ⚠️ DEPRECATED      │            │  ✅ RECOMMENDED         │        │
│   └─────────────────────┘            └─────────────────────────┘        │
│                                                                          │
│   Migration Path:                                                        │
│   ──────────────                                                        │
│   # Before (deprecated)                                                  │
│   from shopping.services.self_healing import CircuitBreakerService      │
│                                                                          │
│   # After (recommended)                                                  │
│   from selfhealing.services import CircuitBreakerService                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.2 신규 패키지 구조

```
packages/selfhealing-python/
├── src/selfhealing/
│   ├── __init__.py           # 버전 및 공개 API
│   ├── core/                  # 핵심 도메인 로직
│   │   ├── types.py          # FailureType, CircuitState 등
│   │   ├── config.py         # 설정 데이터클래스
│   │   └── backoff.py        # 백오프 알고리즘
│   │
│   ├── services/              # 비즈니스 서비스
│   │   ├── circuit_breaker/  # Circuit Breaker 모듈
│   │   │   ├── service.py
│   │   │   ├── config.py
│   │   │   ├── protection.py
│   │   │   └── manual_control.py
│   │   ├── dlq_service.py
│   │   ├── replay_service.py
│   │   └── retry_handler.py
│   │
│   ├── interfaces/            # 추상 인터페이스
│   │   └── repositories.py
│   │
│   ├── adapters/              # 프레임워크별 구현
│   │   ├── django/           # Django ORM
│   │   ├── celery/           # Celery 태스크
│   │   └── memory/           # 테스트용 인메모리
│   │
│   ├── metrics/               # 관찰성
│   │   └── prometheus.py
│   │
│   └── factory.py             # 서비스 팩토리
│
├── tests/                     # 테스트
│   ├── unit/
│   ├── integration/
│   └── chaos/                 # 카오스 엔지니어링 테스트
│
└── pyproject.toml             # 패키지 설정
```

---

## 버전 정보

- **현재 버전**: 0.1.0
- **마지막 업데이트**: 2025-12-20
