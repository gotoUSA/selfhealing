# Self-Healing 미들웨어 시스템 문서 인덱스

> **Version**: 2.2.0
> **Updated**: 2026-01-02
> **Based on**: MIDDLEWARE_REFERENCE.md v2.2.0

---

## 📚 문서 구조

Self-Healing 시스템의 복잡성으로 인해 단일 문서를 4개의 주제별 문서로 분리하였습니다.

```
middleware_system/
├── 00_INDEX.md              ← 현재 문서 (개요 및 네비게이션)
├── 01_MIDDLEWARE_GATEWAY.md   ← 미들웨어/게이트웨이 파이프라인
├── 02_LOGIC_ENGINE.md         ← 비즈니스 로직 엔진
├── 03_INFRA_ADAPTER.md        ← 인프라/저장소 어댑터
└── 04_AUTONOMOUS_OPS.md       ← 자율 운영 시스템
```

---

## 🗺️ 문서 간 관계도

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         요청 흐름 (Request Flow)                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  01_MIDDLEWARE_GATEWAY.md                                                │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Django/FastAPI 미들웨어 → Rate Limiting → Throttling → Routing   │  │
│  │  - 등록된/미등록 미들웨어                                          │  │
│  │  - Tiering 시스템                                                  │  │
│  │  - DRF/FastAPI 어댑터                                              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  02_LOGIC_ENGINE.md                                                      │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Circuit Breaker → DLQ → Replay → SLO/SLI → Error Budget          │  │
│  │  - 인터페이스 정의 (Repository, Cache, TaskQueue)                  │  │
│  │  - Resilience 패턴                                                 │  │
│  │  - Core 컴포넌트                                                   │  │
│  │  - Provider Registry                                               │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌────────────────────────────────────┐ ┌────────────────────────────────────┐
│  03_INFRA_ADAPTER.md               │ │  04_AUTONOMOUS_OPS.md              │
│  ┌──────────────────────────────┐ │ │  ┌──────────────────────────────┐ │
│  │  Django/SQLAlchemy 어댑터    │ │ │  │  Celery Tasks                │ │
│  │  - 저장소 (Repository)        │ │ │  │  - Chaos Engineering         │ │
│  │  - 캐시 (Redis/Memory)        │ │ │  │  - 메트릭 수집               │ │
│  │  - 알림 (Notification)        │ │ │  │  - Drift Detection           │ │
│  │  - 감사 (Audit)               │ │ │  │  - Governance 자동화         │ │
│  │  - 외부 연동                  │ │ │  │  - 학습 시스템               │ │
│  └──────────────────────────────┘ │ │  └──────────────────────────────┘ │
└────────────────────────────────────┘ └────────────────────────────────────┘
```

---

## 📖 문서별 개요

### [01_MIDDLEWARE_GATEWAY.md](01_MIDDLEWARE_GATEWAY.md)

**목적**: 요청/응답 파이프라인의 게이트웨이 레이어

| 섹션 | 내용 |
|------|------|
| 등록된 미들웨어 | HealthBridge, SelfHealing, HybridRateLimit, Chaos 등 |
| 미등록 미들웨어 | PoolCircuitBreaker, Audit, Tiering 등 |
| FastAPI 미들웨어 | SelfHealingMiddleware, ShutdownMiddleware |
| Tiering 시스템 | 계층별 요청 라우팅 |
| DRF 컴포넌트 | Permissions, Throttle, Reauthentication |
| Throttle 서비스 | Netflix Gradient 적응형 쓰로틀링 |

**주요 패키지**:
- `myproject.middleware.*`
- `selfhealing.adapters.fastapi.middleware`
- `selfhealing.api.django.*`
- `selfhealing.services.throttle.*`

---

### [02_LOGIC_ENGINE.md](02_LOGIC_ENGINE.md)

**목적**: 비즈니스 로직 및 복원력 엔진

| 섹션 | 내용 |
|------|------|
| 연결 서비스 | CircuitBreaker, DLQService, ReplayService 등 |
| 유틸리티 | Decorators, Context Managers |
| 서비스 컴포넌트 | EmergencyService, ErrorBudget, HealthCheck 등 |
| Resilience 패턴 | Fallback, Retry, Bulkhead, Timeout |
| Core 컴포넌트 | TLS, Certificate, Pool 관리 |
| 인터페이스 | Repository, Cache, TaskQueue, WebFramework |
| SLO/SLI | Error Budget 계산 |
| Provider Registry | 플러그인 팩토리 |

**주요 패키지**:
- `selfhealing.core.*`
- `selfhealing.services.*`
- `selfhealing.interfaces.*`
- `selfhealing.resilience.*`
- `selfhealing.slo`
- `selfhealing.factory`

---

### [03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md)

**목적**: 인프라/저장소/외부 시스템 연동

| 섹션 | 내용 |
|------|------|
| Adapters 모듈 | Cache, Task Queue, Config 어댑터 |
| Audit Backends | 감사 로그 백엔드 구현 |
| Audit 컴포넌트 | WAL, RingBuffer, Manifest 등 |
| Notification | 알림 인터페이스 및 어댑터 |
| SQLAlchemy 어댑터 | SQLAlchemy ORM 기반 구현 |
| Django 어댑터 | Django ORM 기반 구현 |

**주요 패키지**:
- `selfhealing.adapters.cache.*`
- `selfhealing.adapters.queue.*`
- `selfhealing.adapters.django.*`
- `selfhealing.adapters.sqlalchemy.*`
- `selfhealing.audit.*`
- `selfhealing.interfaces.notification`

---

### [04_AUTONOMOUS_OPS.md](04_AUTONOMOUS_OPS.md)

**목적**: 자율 운영 및 백그라운드 자동화

| 섹션 | 내용 |
|------|------|
| 서비스 (자동화) | FinOps, AdaptiveLearning, AnomalyDetector, PatternRecognizer 등 |
| Celery 어댑터 | 태스크 & 시그널 훅 |
| Metrics 모듈 | Prometheus, Event Handlers, Reliability |
| Utils 모듈 | Time, AsyncHealingLogger |
| Tasks 모듈 | Chaos Scheduler, Drift Detection, Governance |

**주요 패키지**:
- `selfhealing.services.finops`
- `selfhealing.services.learning`
- `selfhealing.adapters.celery.*`
- `selfhealing.metrics.*`
- `selfhealing.utils.*`
- `selfhealing.tasks.*`

---

## 🔗 크로스 레퍼런스

### 인터페이스 → 구현체 매핑

| 인터페이스 (02_LOGIC_ENGINE) | 구현체 (03_INFRA_ADAPTER) |
|------------------------------|---------------------------|
| `RepositoryInterface` | `DjangoFailedOperationRepository`, `SQLAlchemyFailedOperationRepository` |
| `CacheInterface` | `RedisCacheAdapter`, `MemoryCacheAdapter` |
| `TaskQueueInterface` | `CeleryTaskQueue`, `SyncTaskQueue` |
| `AuditInterface` | `PostgresAuditBackend`, `CloudWatchAuditBackend`, `FileAuditBackend` |
| `NotificationAdapter` | `SlackAdapter`, `TeamsAdapter`, `PagerDutyAdapter` |

### 미들웨어 → 서비스 의존성

| 미들웨어 (01_GATEWAY) | 사용 서비스 (02_LOGIC_ENGINE) |
|-----------------------|-------------------------------|
| `SelfHealingMiddleware` | `CircuitBreakerService`, `DLQService` |
| `HybridRateLimitMiddleware` | `RateLimitService`, `AdaptiveThrottle` |
| `ChaosMiddleware` | `ChaosController` |
| `PoolCircuitBreakerMiddleware` | `CircuitBreakerService`, `PoolWatchdog` |

### 태스크 → 서비스 의존성

| 태스크 (04_AUTONOMOUS_OPS) | 사용 서비스 (02_LOGIC_ENGINE) |
|----------------------------|-------------------------------|
| `run_scheduled_experiments` | `ChaosController` |
| `check_emergency_mode_expiry` | `SystemControlService` |
| `apply_pending_config_changes` | `ConfigManager` |
| `drift_detection` | `SLADriftDetector`, `MetricReconciler` |

---

## 📁 패키지 구조 총괄

```
selfhealing/
├── __init__.py
├── factory.py                    ← Provider Registry (02)
├── config.py                     ← Configuration (02)
├── slo.py                        ← SLO/SLI 정의 (02)
│
├── adapters/                     ← 03_INFRA_ADAPTER
│   ├── cache/                    ← 캐시 어댑터
│   ├── celery/                   ← Celery 어댑터 (04)
│   ├── config/                   ← 설정 프로바이더
│   ├── django/                   ← Django ORM 어댑터
│   ├── fastapi/                  ← FastAPI 미들웨어 (01)
│   ├── frameworks/               ← 프레임워크 어댑터 (01)
│   ├── queue/                    ← 태스크 큐 어댑터
│   └── sqlalchemy/               ← SQLAlchemy 어댑터
│
├── api/                          ← 01_MIDDLEWARE_GATEWAY
│   └── django/                   ← DRF 컴포넌트
│       ├── permissions.py
│       ├── throttle_adapter.py
│       └── reauthentication.py
│
├── audit/                        ← 03_INFRA_ADAPTER
│   ├── backends/                 ← 감사 백엔드
│   └── components/               ← WAL, RingBuffer 등
│
├── core/                         ← 02_LOGIC_ENGINE
│   ├── tls_handler.py
│   ├── certificate_monitor.py
│   ├── connection_health.py
│   └── pool_watchdog.py
│
├── interfaces/                   ← 02_LOGIC_ENGINE
│   ├── repository.py
│   ├── cache.py
│   ├── task_queue.py
│   ├── web_framework.py
│   └── notification.py           ← 03_INFRA_ADAPTER
│
├── metrics/                      ← 04_AUTONOMOUS_OPS
│   ├── prometheus.py
│   ├── event_handlers.py
│   ├── reliability.py
│   └── reconciler.py
│
├── resilience/                   ← 02_LOGIC_ENGINE
│   ├── fallback.py
│   ├── retry.py
│   └── bulkhead.py
│
├── services/                     ← 02_LOGIC_ENGINE & 04_AUTONOMOUS_OPS
│   ├── circuit_breaker.py        ← 02
│   ├── dlq_service.py            ← 02
│   ├── replay.py                 ← 02
│   ├── emergency.py              ← 02
│   ├── error_budget.py           ← 02
│   ├── finops.py                 ← 04
│   ├── learning.py               ← 04
│   └── throttle/                 ← 01
│
├── tasks/                        ← 04_AUTONOMOUS_OPS
│   ├── chaos_scheduler.py
│   ├── drift_detection.py
│   └── governance.py
│
└── utils/                        ← 04_AUTONOMOUS_OPS
    ├── time.py
    └── async_logger.py
```

---

## 🔄 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| 2.2.0 | 2026-01-02 | MIDDLEWARE_REFERENCE.md → 4개 문서 분리 |
| 2.1.0 | 2026-01-01 | 초기 통합 문서 작성 |

---

## 📎 관련 문서

- [../01_OVERVIEW.md](../01_OVERVIEW.md) - 시스템 개요
- [../02_ARCHITECTURE.md](../02_ARCHITECTURE.md) - 아키텍처 설계
- [../03_CIRCUIT_BREAKER.md](../03_CIRCUIT_BREAKER.md) - 서킷브레이커 상세
- [../04_DEAD_LETTER_QUEUE.md](../04_DEAD_LETTER_QUEUE.md) - DLQ 상세
- [../07_CONTROL_API.md](../07_CONTROL_API.md) - API 엔드포인트
- [../56_AUDIT_MIDDLEWARE_DESIGN.md](../56_AUDIT_MIDDLEWARE_DESIGN.md) - Audit 미들웨어 설계
