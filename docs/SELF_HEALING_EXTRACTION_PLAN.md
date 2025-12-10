# Self-Healing SaaS 분리 계획서

> **작성일**: 2025-12-10
> **목표**: L3 Self-Healing 시스템을 독립 SaaS 패키지로 분리
> **예상 기간**: 4-6주 (풀타임 기준)

---

## 📋 목차

1. [시스템 이해](#1-시스템-이해-l1-l2-l3-차이)
2. [전체 파일 목록](#2-전체-파일-목록-분리-대상)
3. [의존성 맵](#3-의존성-맵)
4. [Phase별 분리 계획](#4-phase별-분리-계획)
5. [체크리스트](#5-체크리스트)

---

## 1. 시스템 이해 (L1, L2, L3 차이)

### Reliability Layer 스택

```
┌─────────────────────────────────────────────────────────────┐
│  L3: Self-Healing Recovery  ← 🎯 SaaS 분리 대상             │
│  ├─ Circuit Breaker (서킷 브레이커)                          │
│  ├─ DLQ (Dead Letter Queue)                                 │
│  ├─ Retry/Backoff (재시도 정책)                              │
│  ├─ Replay Service (DLQ 재처리)                              │
│  ├─ Control API (관리 API)                                  │
│  └─ Metrics/Observability (관측성)                          │
├─────────────────────────────────────────────────────────────┤
│  L2: Transactional Safety  ← 쇼핑몰에 남음                   │
│  ├─ select_for_update (비관적 락)                            │
│  ├─ Crash recovery (중간 장애 복구)                          │
│  └─ FIFO 포인트, Idempotency                                │
├─────────────────────────────────────────────────────────────┤
│  L1: Code-Level Correctness  ← 쇼핑몰에 남음                 │
│  ├─ Unit tests / Integration tests                          │
│  ├─ Validation rules                                        │
│  └─ Business logic                                          │
└─────────────────────────────────────────────────────────────┘
```

### 각 레이어 설명

| Layer | 역할 | 예시 | 분리 대상 |
|-------|------|------|----------|
| **L1** | 코드 정확성 | 검증 로직, 비즈니스 룰 | ❌ 아님 |
| **L2** | 트랜잭션 안전 | DB 락, 원자성, 중복 방지 | ❌ 아님 |
| **L3** | 장애 복구 | 재시도, DLQ, 서킷 브레이커 | ✅ **분리 대상** |

### 핵심 포인트

- **L3는 L1, L2가 정상 작동한다고 가정**
- L3는 **기술적 실패** (네트워크 오류, 타임아웃 등) 복구
- L2 테스트 (`test_l2_*.py`)는 쇼핑몰에 남아야 함
- L3 테스트 (`test_l3_*.py`, `self_healing/`)는 분리 대상

---

## 2. 전체 파일 목록 (분리 대상)

### 📁 서비스 코드 (14개 파일)

```
shopping/services/self_healing/
├── __init__.py                     # 모듈 exports
├── backoff_calculator.py           # 지수 백오프 계산
├── circuit_breaker_service.py      # 서킷 브레이커 (1105줄)
├── config.py                       # 중앙 설정 (525줄)
├── control_api_service.py          # Control API 서비스
├── dlq_service.py                  # DLQ 관리 (506줄)
├── forensic_context.py             # 포렌식 컨텍스트
├── idempotency_service.py          # 멱등성 관리
├── metrics.py                      # Prometheus 메트릭
├── replay_service.py               # DLQ 재처리
├── retry_handler.py                # 재시도 핸들러
├── security_notification_service.py # 보안 알림
└── security_violation_service.py   # 보안 위반 감지
```

### 📁 모델 (4개 파일) ⚠️ 업데이트됨

```
shopping/models/
├── __init__.py                     # ⚠️ 부분 수정 필요 (4개 모델 export 제거)
│   # FailedPayment, FailedOperation, CircuitBreakerState, SecurityIncident
├── failed_operation.py             # ✅ 전체 분리 (DLQ 모델)
├── failed_payment.py               # ⚠️ CircuitBreakerState만 분리 (FailedPayment는 남음)
└── security_incident.py            # ✅ 전체 분리
```

### 📁 뷰 & 시리얼라이저 (2개 파일)

```
shopping/views/
└── self_healing_views.py           # Control API Views (650줄)

shopping/serializers/
└── self_healing_serializers.py     # API 시리얼라이저
```

### 📁 Celery 태스크 (4개 파일) ⚠️ 업데이트됨

```
shopping/tasks/
├── __init__.py                     # ⚠️ 부분 수정 필요 (17개 태스크 export 제거)
├── self_healing_tasks.py           # Self-Healing 전용 태스크 (7개 함수)
│   ├── conditional_replay_on_circuit_close
│   ├── check_circuit_breaker_recovery
│   ├── force_open_circuit_breaker
│   ├── force_close_circuit_breaker
│   ├── expire_manual_overrides
│   ├── collect_self_healing_metrics
│   └── check_and_report_sla_breaches
├── dlq_replay_tasks.py             # DLQ 재처리 태스크 (5개 함수)
│   ├── replay_single_dlq_entry
│   ├── replay_batch_by_failure_type
│   ├── replay_batch_by_domain
│   ├── replay_on_circuit_breaker_close
│   └── cleanup_resolved_dlq_entries
└── payment_recovery_tasks.py       # ✅ 추가됨! DLQ 관련 태스크 (5개 함수)
    ├── retry_failed_payment()      #    - 결제 재시도
    ├── check_sla_violations()      #    - SLA 위반 체크
    ├── cleanup_expired_dlq()       #    - 만료된 DLQ 정리
    ├── process_dlq_batch()         #    - DLQ 배치 처리
    └── reset_circuit_breaker()     #    - 서킷 브레이커 리셋
```

### 📁 결제 복구 서비스 (1개 파일) ⚠️ 새로 발견

```
shopping/services/
└── payment_recovery_service.py     # ✅ 추가됨! 467줄
    ├── PaymentRecoveryHandler (ABC)  # 추상 클래스 - 핵심 인터페이스
    ├── PaymentRecoveryError         # 복구 에러 기본 클래스
    ├── CircuitBreakerOpenError      # CB Open 상태 에러
    ├── SLATimeoutError              # SLA 타임아웃 에러
    └── CeleryPaymentRecovery        # Celery 기반 구현체
```

### 📁 Admin (3개 파일) ⚠️ 업데이트됨

```
shopping/admin/
├── __init__.py                     # ⚠️ 부분 수정 필요 (2개 Admin export 제거)
│   # CircuitBreakerStateAdmin, FailedOperationAdmin
├── circuit_breaker_admin.py        # CB 관리 화면
└── dlq_admin.py                    # DLQ 관리 화면
```

### 📁 URL 설정 (1개 파일 - 부분) ⚠️ 업데이트됨

```
shopping/urls.py                    # self-healing/* 경로 (27줄)
    ├── self-healing/control/       # ControlActionView
    ├── self-healing/status/        # ControlStatusView
    ├── self-healing/status/<service>/ # ServiceStatusView
    ├── self-healing/audit/         # ControlAuditView
    ├── self-healing/allow/<service>/ # QuickAllowView
    ├── self-healing/block/<service>/ # QuickBlockView
    ├── self-healing/reset/<service>/ # QuickResetView
    ├── self-healing/health/        # SelfHealingHealthView
    ├── self-healing/metrics/       # SelfHealingMetricsView
    └── self-healing/dlq/replay/    # DLQReplayView
```

### 📁 Management Commands (1개 파일)

```
shopping/management/commands/
└── generate_self_healing_alerts.py # Prometheus 알림 생성
```

### 📁 Unit Tests (14개 파일) ⚠️ 업데이트됨

```
shopping/tests/unit/self_healing/
├── __init__.py
├── test_audit_record_schema.py
├── test_backoff_jitter_distribution.py
├── test_backoff_policy.py
├── test_circuit_breaker_service.py  # (1260줄)
├── test_failure_classification.py
├── test_idempotency_enforcement.py
├── test_retry_decision_table.py
├── test_security_notification_service.py
├── test_security_violation_service.py
├── test_self_healing_metrics.py
├── test_self_healing_policy.py      # ✅ 추가됨! (누락되었던 파일)
└── test_sla_timer_policy.py

shopping/tests/unit/models/
└── test_self_healing_models.py      # 모델 테스트
```

### 📁 Integration Tests (22개 파일) ⚠️ 업데이트됨

```
shopping/tests/integration/self_healing/
├── __init__.py
├── conftest.py                      # ✅ 추가됨! (694줄, 중요 픽스처)
├── test_architectural_resilience_e2e.py
├── test_audit_accountability.py
├── test_cascading_failures.py
├── test_chaos_engineering.py
├── test_circuit_breaker.py
├── test_circuit_breaker_distributed.py
├── test_circuit_breaker_ttl.py
├── test_cold_start_recovery.py
├── test_control_api.py
├── test_cost_aware_recovery.py
├── test_dlq_retention.py
├── test_dlq_storage_and_replay.py
├── test_manual_override_policy.py
├── test_metrics_dlq_pending.py
├── test_multi_tenancy_isolation.py
├── test_notification_sla.py
├── test_observability_metrics.py
├── test_retry_persistence.py
└── test_security_dlq_separation.py

shopping/tests/integration/
└── test_l3_self_healing.py          # L3 통합 테스트
```

### 📁 Task Tests (1개 파일)

```
shopping/tests/tasks/
└── test_observability_tasks.py      # Self-Healing 태스크 테스트
```

### 📁 Chaos Engineering Tests (5개 파일) ✅ 새로 발견

```
shopping/tests/integration/chaos/
├── __init__.py
├── conftest.py                      # Chaos 테스트 픽스처
├── test_partial_failure_patterns.py # 부분 장애 패턴 테스트
├── test_recovery_during_chaos.py    # 카오스 중 복구 테스트
├── test_resource_exhaustion.py      # 리소스 고갈 테스트
└── test_slow_degradation.py         # 느린 성능 저하 테스트
```

### 📁 E2E Tests (4개 파일) ⚠️ 업데이트됨

```
shopping/tests/e2e/
├── test_failure_recovery_cycle.py      # 장애 복구 사이클 테스트
├── test_user_invisible_flows.py        # ✅ 추가됨! User-Invisible 흐름 테스트 (582줄)
│   ├── E2E-U001: Payment retry (user sees success)
│   ├── E2E-U002: Circuit breaker fallback PG
│   ├── E2E-U003: All retries fail graceful message
│   └── E2E-U004: Order status during DLQ processing
└── self_healing/
    ├── __init__.py
    └── test_forensic_replay.py         # 포렌식 리플레이 테스트
```

### 📁 문서 (28개 파일) ⚠️ 업데이트됨

```
docs/
├── L3_SELF_HEALING_SYSTEM.md        # 한글 요약 (908줄)
└── self_healing/
    ├── ACTIVE_SPEC_TEMPLATE.md
    ├── SELF_HEALING_LOAD_TEST_PLAN.md
    ├── 0_OVERVIEW/
    │   ├── SELF_HEALING_ARCHITECTURE.md    # (1012줄)
    │   ├── SELF_HEALING_OPERATIONS.md      # (1686줄)
    │   └── SELF_HEALING_TEST_README.md
    ├── 1_REQUIREMENTS/
    │   └── SELF_HEALING_TEST_REQUIREMENTS_SPECIFICATION.md
    ├── 2_STRATEGY/
    │   ├── SELF_HEALING_TEST_GAP_REPORT.md
    │   ├── SELF_HEALING_TEST_MATRICES.md
    │   └── SELF_HEALING_TEST_STRATEGY.md
    ├── 3_EXECUTION/
    │   ├── SELF_HEALING_PLATFORM_PORTABILITY.md
    │   ├── SELF_HEALING_TEST_EXECUTION_GUIDE.md
    │   ├── SELF_HEALING_TEST_ONBOARDING.md
    │   └── SELF_HEALING_TEST_SPECIFICATIONS.md
    ├── 4_REPORTING/
    │   └── SELF_HEALING_TEST_COVERAGE_ANALYSIS.md
    ├── 5_CONTROL_API/
    │   ├── README.md
    │   ├── CONTROL_API_REFERENCE.md
    │   ├── CONTROL_API_INTERFACE.md
    │   ├── CONTROL_API_SECURITY_GOVERNANCE.md
    │   ├── CONTROL_API_EXECUTION.md
    │   └── CONTROL_API_TEST_REQUIREMENTS.md
    └── _reserved_future/              # ✅ 상세 내용 추가
        ├── AI_REVIEWER_AUTOMATION_SPEC.md
        ├── CONTROL_API_CHAOS_STANDARD_SPEC.md
        ├── METRICS_PROVIDER_INTERFACE_SPEC.md
        ├── PROMPT_POLICY_SPEC.md
        ├── _annex_reserved_draft/
        └── _reserved_header/
```

### 📁 Settings 설정 (1개 파일) ✅ 새로 발견

```
myproject/settings/components/
└── payment.py                       # ⚠️ PAYMENT_RECOVERY 설정 (부분 수정 필요)
    ├── RETRY_MAX_ATTEMPTS           # 재시도 최대 횟수
    ├── RETRY_BACKOFF_BASE           # 백오프 기본값
    ├── RETRY_BACKOFF_MAX            # 백오프 최대값
    ├── RETRY_JITTER                 # 지터 사용 여부
    ├── SLA_TIMEOUT_SECONDS          # SLA 타임아웃
    ├── SLA_ABORT_ENABLED            # SLA 초과 시 중단 여부
    ├── CIRCUIT_BREAKER_ENABLED      # 서킷 브레이커 활성화
    ├── CIRCUIT_BREAKER_FAILURE_THRESHOLD  # 실패 임계값
    ├── CIRCUIT_BREAKER_RECOVERY_TIMEOUT   # 복구 타임아웃
    ├── CIRCUIT_BREAKER_SUCCESS_THRESHOLD  # 성공 임계값
    ├── DLQ_ENABLED                  # DLQ 활성화
    ├── DLQ_RETENTION_DAYS           # DLQ 보관 기간
    ├── NOTIFY_ON_DLQ                # DLQ 알림
    └── NOTIFY_ON_CIRCUIT_OPEN       # 서킷 오픈 알림
```

### 📁 인프라 설정 (2개 파일)

```
scripts/
├── prometheus/
│   └── self_healing_alerts.yml      # Prometheus 알림 규칙
└── grafana/
    └── dashboards/
        └── self_healing_dashboard.json  # Grafana 대시보드 (1500줄)
```

---

## 3. 의존성 맵

### Self-Healing → Django 모델 의존성

```python
# 분리해야 할 Django 모델 의존성

circuit_breaker_service.py:
  → shopping.models.failed_payment.CircuitBreakerState
  → shopping.models.user.User

dlq_service.py:
  → shopping.models.failed_operation.FailedOperation
  → shopping.models.order.Order
  → shopping.models.payment.Payment
  → shopping.models.user.User

replay_service.py:
  → shopping.models.failed_operation.FailedOperation

retry_handler.py:
  → shopping.models.failed_operation.FailedOperation

security_violation_service.py:
  → shopping.models.security_incident.SecurityIncident
  → shopping.models.order.Order
  → shopping.models.payment.Payment
  → shopping.models.user.User

security_notification_service.py:
  → shopping.models.security_incident.SecurityIncident

# ✅ 추가 발견된 의존성
payment_recovery_service.py:
  → shopping.services.self_healing.circuit_breaker_service
  → shopping.models.failed_payment.CircuitBreakerState

control_api_service.py:
  → shopping.services.self_healing.circuit_breaker_service
  → shopping.services.self_healing.dlq_service
  → shopping.services.self_healing.replay_service

# 테스트 픽스처 의존성
tests/integration/self_healing/conftest.py (694줄):
  → shopping.services.payment_recovery_service.CeleryPaymentRecovery
  → shopping.models.failed_payment.CircuitBreakerState
  → shopping.models.failed_payment.FailedPayment
  → shopping.tests.factories (OrderFactory, PaymentFactory, UserFactory)

tests/integration/chaos/conftest.py (315줄):
  → FailureInjector 클래스 (Chaos 테스트용)
  → LatencySimulator, ResourceTracker 등

tests/e2e/test_user_invisible_flows.py (582줄):
  → shopping.services.self_healing.circuit_breaker_service
  → shopping.models.failed_payment.CircuitBreakerState
  → UserFlowSimulator, InternalState 클래스
```

### Self-Healing ← Django Settings 의존성

```python
# 설정 파일에서 Self-Healing 설정을 읽어옴
# myproject/settings/components/payment.py

PAYMENT_RECOVERY = {
    "RETRY_MAX_ATTEMPTS": ...,
    "RETRY_BACKOFF_BASE": ...,
    "SLA_TIMEOUT_SECONDS": ...,
    "CIRCUIT_BREAKER_ENABLED": ...,
    "DLQ_ENABLED": ...,
    # ... 등
}

# 이 설정은 shopping/services/self_healing/config.py에서 참조
# 분리 후: selfhealing 패키지가 Django settings에서 자동으로 읽도록 설계
```

### 쇼핑몰 → Self-Healing 의존성

```python
# 쇼핑몰에서 Self-Healing을 호출하는 곳 (나중에 SDK 호출로 변경)

# 결제 서비스
from shopping.services.self_healing.circuit_breaker_service import (
    should_allow_request,
    force_open_circuit,
)
from shopping.services.self_healing.dlq_service import store_to_dlq
from shopping.services.self_healing.retry_handler import RetryHandler

# payment_recovery_service.py (467줄) - ✅ 추가됨
from shopping.services.self_healing.circuit_breaker_service import (
    get_circuit_breaker_service,
    CircuitBreakerService,
)

# Admin
from shopping.services.self_healing.replay_service import get_replay_service
from shopping.services.self_healing.circuit_breaker_service import (
    get_circuit_breaker_service,
)
```

---

## 4. Phase별 분리 계획

### Phase 0: 준비 (1-2일) ⬜

**목표**: 현재 상태 정리 및 분리 브랜치 생성

- [ ] 새 브랜치 생성: `feature/self-healing-extraction`
- [ ] 모든 테스트 통과 확인
- [ ] 현재 테스트 커버리지 기록
- [ ] 이 문서 검토 및 승인

### Phase 1: 인터페이스 추상화 (1주) ⬜

**목표**: Django 모델 직접 의존 제거, Repository 패턴 도입

#### 1.1 인터페이스 정의

```python
# 새로 생성: shopping/services/self_healing/interfaces/__init__.py
# 새로 생성: shopping/services/self_healing/interfaces/repositories.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class FailedOperationData:
    id: int
    domain: str
    failure_type: str
    status: str
    # ... 기타 필드

class FailedOperationRepository(ABC):
    @abstractmethod
    def create(self, **kwargs) -> FailedOperationData: ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[FailedOperationData]: ...

    @abstractmethod
    def get_pending_by_domain(self, domain: str) -> List[FailedOperationData]: ...

    @abstractmethod
    def update_status(self, id: int, status: str, **kwargs) -> bool: ...

class CircuitBreakerStateRepository(ABC):
    @abstractmethod
    def get_or_create(self, service_name: str) -> dict: ...

    @abstractmethod
    def update_state(self, service_name: str, state: str, **kwargs) -> bool: ...
```

#### 1.2 Django 어댑터 구현

```python
# 새로 생성: shopping/services/self_healing/adapters/__init__.py
# 새로 생성: shopping/services/self_healing/adapters/django_repositories.py

from shopping.models.failed_operation import FailedOperation
from ..interfaces.repositories import (
    FailedOperationRepository,
    FailedOperationData,
)

class DjangoFailedOperationRepository(FailedOperationRepository):
    def create(self, **kwargs) -> FailedOperationData:
        obj = FailedOperation.objects.create(**kwargs)
        return self._to_data(obj)

    def _to_data(self, obj) -> FailedOperationData:
        return FailedOperationData(
            id=obj.id,
            domain=obj.domain,
            failure_type=obj.failure_type,
            status=obj.status,
            # ... 매핑
        )
```

**작업 목록**:
- [ ] `interfaces/` 폴더 생성
- [ ] `FailedOperationRepository` 인터페이스 정의
- [ ] `CircuitBreakerStateRepository` 인터페이스 정의
- [ ] `SecurityIncidentRepository` 인터페이스 정의
- [ ] `adapters/` 폴더 생성
- [ ] Django 어댑터 구현
- [ ] 테스트 통과 확인

### Phase 2: 서비스 레이어 리팩토링 (1주) ⬜

**목표**: 서비스들이 Repository 인터페이스를 사용하도록 변경

#### 2.1 DI (Dependency Injection) 적용

```python
# 변경 전: dlq_service.py
from shopping.models.failed_operation import FailedOperation

class DLQService:
    def store(self, ...):
        FailedOperation.objects.create(...)  # ❌ 직접 의존

# 변경 후: dlq_service.py
from .interfaces.repositories import FailedOperationRepository

class DLQService:
    def __init__(self, repository: FailedOperationRepository):
        self._repository = repository

    def store(self, ...):
        self._repository.create(...)  # ✅ 추상화
```

#### 2.2 Service Factory 추가

```python
# 새로 생성: shopping/services/self_healing/factory.py

from .adapters.django_repositories import (
    DjangoFailedOperationRepository,
    DjangoCircuitBreakerStateRepository,
)
from .dlq_service import DLQService
from .circuit_breaker_service import CircuitBreakerService

def create_dlq_service() -> DLQService:
    """Django 환경에서 DLQService 생성"""
    return DLQService(
        repository=DjangoFailedOperationRepository()
    )

def create_circuit_breaker_service() -> CircuitBreakerService:
    """Django 환경에서 CircuitBreakerService 생성"""
    return CircuitBreakerService(
        repository=DjangoCircuitBreakerStateRepository()
    )
```

**작업 목록**:
- [ ] `dlq_service.py` 리팩토링
- [ ] `circuit_breaker_service.py` 리팩토링
- [ ] `replay_service.py` 리팩토링
- [ ] `retry_handler.py` 리팩토링
- [ ] `security_violation_service.py` 리팩토링
- [ ] `security_notification_service.py` 리팩토링
- [ ] `factory.py` 생성
- [ ] 기존 `get_*_service()` 함수들이 factory 사용하도록 변경
- [ ] 모든 테스트 통과 확인

### Phase 3: 독립 패키지 구조 설계 (3-4일) ⬜

**목표**: 독립 PyPI 패키지로 분리 가능한 구조 설계

#### 3.1 새 패키지 구조

```
selfhealing-python/                  # 새 저장소
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── selfhealing/
│       ├── __init__.py
│       ├── core/                    # 프레임워크 독립 핵심 로직
│       │   ├── __init__.py
│       │   ├── circuit_breaker.py
│       │   ├── backoff.py
│       │   ├── dlq.py
│       │   ├── retry.py
│       │   └── types.py             # 공통 타입 정의
│       ├── interfaces/              # 추상 인터페이스
│       │   ├── __init__.py
│       │   ├── repositories.py
│       │   └── task_queue.py
│       ├── adapters/                # 외부 시스템 어댑터
│       │   ├── __init__.py
│       │   ├── django/
│       │   │   ├── __init__.py
│       │   │   ├── repositories.py
│       │   │   ├── models.py        # Django 모델 정의
│       │   │   └── admin.py
│       │   ├── celery/
│       │   │   ├── __init__.py
│       │   │   └── tasks.py
│       │   └── sqlalchemy/          # 미래 확장
│       │       └── __init__.py
│       ├── api/                     # REST API
│       │   ├── __init__.py
│       │   └── django/
│       │       ├── views.py
│       │       ├── serializers.py
│       │       └── urls.py
│       ├── metrics/                 # 관측성
│       │   ├── __init__.py
│       │   ├── prometheus.py
│       │   └── opentelemetry.py
│       └── config/
│           ├── __init__.py
│           └── settings.py
├── tests/
│   ├── unit/
│   └── integration/
└── docs/
    └── (현재 docs/self_healing/ 이동)
```

#### 3.2 pyproject.toml

```toml
[project]
name = "selfhealing"
version = "0.1.0"
description = "Self-Healing Reliability Layer for Python Applications"
authors = [{name = "Your Name", email = "you@example.com"}]
license = {text = "MIT"}
readme = "README.md"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
django = ["django>=4.2"]
celery = ["celery>=5.0"]
prometheus = ["prometheus-client>=0.17"]
all = ["selfhealing[django,celery,prometheus]"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Phase 4: 코드 마이그레이션 (1주) ⬜

**목표**: 실제 코드를 새 패키지로 이동

**작업 목록**:
- [ ] 새 저장소 생성
- [ ] `core/` 모듈 구현 (순수 Python, 프레임워크 독립)
- [ ] `interfaces/` 이동
- [ ] `adapters/django/` 구현
- [ ] `adapters/celery/` 구현
- [ ] `api/django/` 구현
- [ ] `metrics/` 구현
- [ ] 테스트 마이그레이션
- [ ] 문서 마이그레이션

### Phase 5: 쇼핑몰 통합 (3-4일) ⬜

**목표**: 쇼핑몰이 새 패키지를 사용하도록 변경

```python
# requirements.txt 변경
# 기존 self_healing 폴더 대신 패키지 설치
selfhealing[django,celery,prometheus]

# 또는 로컬 개발 시
-e ../selfhealing-python[all]
```

**작업 목록**:
- [ ] 쇼핑몰 `requirements.txt` 업데이트
- [ ] import 경로 변경 (검색/치환)
  - `from shopping.services.self_healing` → `from selfhealing`
- [ ] 설정 마이그레이션 (`SELF_HEALING` → 패키지 설정)
- [ ] Admin 통합 확인
- [ ] 모든 테스트 통과 확인

### Phase 6: 정리 및 문서화 (2-3일) ⬜

**목표**: 최종 정리 및 SaaS 준비

**작업 목록**:
- [ ] 쇼핑몰에서 이전 `self_healing/` 폴더 삭제
- [ ] 새 패키지 README 작성
- [ ] API 문서 생성
- [ ] PyPI 배포 준비
- [ ] SaaS 대시보드 설계 (선택)

---

## 5. 체크리스트

### 분리 전 확인

- [ ] 모든 테스트 통과
- [ ] 테스트 커버리지 기록
- [ ] 의존성 분석 완료
- [ ] 팀 검토 완료

### 분리 중 확인

- [ ] 각 Phase 완료 후 테스트 통과
- [ ] 기존 기능 정상 작동
- [ ] 성능 저하 없음

### 분리 후 확인

- [ ] 새 패키지 독립 테스트 통과
- [ ] 쇼핑몰 통합 테스트 통과
- [ ] 문서 완비
- [ ] CI/CD 파이프라인 구성

---

## 📊 파일 통계 요약

| 카테고리 | 파일 수 | 예상 줄 수 | 비고 |
|----------|---------|-----------|------|
| 서비스 코드 | 15 | ~4,500 | self_healing/ + payment_recovery_service.py |
| 모델 | 4 | ~500 | __init__.py 수정 포함 |
| 뷰/시리얼라이저 | 2 | ~900 | |
| Celery 태스크 | 4 | ~800 | __init__.py 수정 포함 |
| Admin | 3 | ~500 | __init__.py 수정 포함 |
| URL 설정 | 1 | ~30 | urls.py 부분 수정 |
| Settings 설정 | 1 | ~40 | payment.py PAYMENT_RECOVERY 부분 |
| Management Commands | 1 | ~100 | |
| Unit 테스트 | 15 | ~4,000 | self_healing/ + models/ |
| Integration 테스트 | 22 | ~5,700 | conftest.py 694줄 포함 |
| Chaos 테스트 | 6 | ~1,800 | conftest.py 315줄 포함 |
| E2E 테스트 | 4 | ~1,200 | test_user_invisible_flows.py 582줄 추가 |
| Task 테스트 | 1 | ~200 | |
| 문서 | 28 | ~10,000 | _reserved_future/ 포함 |
| 인프라 설정 | 2 | ~1,600 | |
| **총계** | **~109** | **~31,870** | |

---

## 🎯 다음 단계

1. **이 계획서 검토** - 빠진 파일이나 의존성 있는지 확인
2. **Phase 0 시작** - 브랜치 생성 및 기준선 테스트
3. **점진적 진행** - 각 Phase 완료 후 테스트 확인

---

## 6. 분리 후 주의사항

### 6.1 테스트 전략

#### ✅ 정상 작동하는 테스트

| 테스트 유형 | 실행 위치 | 설명 |
|-------------|-----------|------|
| **Core 로직 Unit 테스트** | selfhealing 패키지 | Backoff 계산, Retry 정책 등 순수 Python 로직 |
| **서비스 Unit 테스트** | selfhealing 패키지 | Repository mock으로 독립 테스트 가능 |
| **Interface 테스트** | selfhealing 패키지 | ABC 구현 검증 |

#### ⚠️ 특별 주의가 필요한 테스트

| 테스트 유형 | 문제점 | 해결 방법 |
|-------------|--------|-----------|
| **Django Integration 테스트** | Django 모델/DB 필요 | 패키지에 `tests/integration/django/` 폴더 생성, `conftest.py`에서 Django 설정 |
| **Admin 테스트** | Django Admin 프레임워크 필요 | `adapters/django/` 하위에 테스트 포함 |
| **Celery 태스크 테스트** | Celery app 설정 필요 | `adapters/celery/` 하위에 테스트 포함, pytest-celery 사용 |
| **E2E 테스트** | 전체 쇼핑몰 시스템 필요 | **쇼핑몰 프로젝트에서만 실행** (분리 불가) |
| **Control API 테스트** | Django REST Framework 필요 | `api/django/` 하위에 테스트 포함 |

#### 테스트 실행 전략

```bash
# 1. selfhealing 패키지 내 테스트 (독립 실행)
cd selfhealing-python
pytest tests/unit/ -v                    # Unit 테스트
pytest tests/integration/django/ -v      # Django 어댑터 테스트

# 2. 쇼핑몰 프로젝트에서 통합 테스트
cd myproject
pytest shopping/tests/integration/self_healing/ -v  # E2E 테스트
```

#### conftest.py 설정 예시 (패키지 내 Django 테스트용)

```python
# selfhealing-python/tests/integration/django/conftest.py
import os
import django
from django.conf import settings

def pytest_configure():
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            DATABASES={
                'default': {
                    'ENGINE': 'django.db.backends.sqlite3',
                    'NAME': ':memory:',
                }
            },
            INSTALLED_APPS=[
                'django.contrib.contenttypes',
                'django.contrib.auth',
                'selfhealing.adapters.django',
            ],
            USE_TZ=True,
        )
        django.setup()
```

---

### 6.2 배포 시 주의사항

#### 버전 관리

| 항목 | 주의사항 | 권장 방법 |
|------|----------|-----------|
| **패키지 버전** | selfhealing과 쇼핑몰 버전 호환성 유지 | Semantic Versioning 사용, CHANGELOG 관리 |
| **Breaking Changes** | 인터페이스 변경 시 양쪽 동시 업데이트 필요 | Major 버전 bump, 마이그레이션 가이드 작성 |
| **의존성 충돌** | Django/Celery 버전 불일치 | `pyproject.toml`에 호환 버전 명시 |

#### 마이그레이션 주의사항

```python
# ⚠️ 모델이 2곳에 존재할 수 있음
# 1. selfhealing 패키지: selfhealing/adapters/django/models.py
# 2. 쇼핑몰: shopping/models/failed_operation.py (삭제됨)

# 마이그레이션 순서:
# 1. selfhealing 패키지 설치
# 2. 쇼핑몰 INSTALLED_APPS에 'selfhealing.adapters.django' 추가
# 3. python manage.py migrate selfhealing_django  (새 앱 마이그레이션)
# 4. 기존 데이터 마이그레이션 (필요시)
# 5. 이전 모델 삭제
```

#### 환경 변수 / 설정

```python
# settings.py - 분리 전
SELF_HEALING = {
    "RETRY": {"MAX_ATTEMPTS": 3},
    "CIRCUIT_BREAKER": {"ENABLED": False},
    "DLQ": {"RETENTION_DAYS": 30},
}

# settings.py - 분리 후 (동일하게 유지 가능)
SELFHEALING = {  # 또는 기존 SELF_HEALING 유지
    "RETRY": {"MAX_ATTEMPTS": 3},
    "CIRCUIT_BREAKER": {"ENABLED": False},
    "DLQ": {"RETENTION_DAYS": 30},
}

# selfhealing 패키지가 Django settings에서 자동으로 읽도록 설계
```

#### Celery Beat 스케줄 변경

```python
# 분리 전
CELERY_BEAT_SCHEDULE = {
    'collect-self-healing-metrics': {
        'task': 'shopping.tasks.self_healing_tasks.collect_self_healing_metrics',
        'schedule': 60.0,
    },
}

# 분리 후 (경로 변경)
CELERY_BEAT_SCHEDULE = {
    'collect-self-healing-metrics': {
        'task': 'selfhealing.adapters.celery.tasks.collect_metrics',  # 새 경로
        'schedule': 60.0,
    },
}
```

---

### 6.3 런타임 주의사항

#### 순환 Import 방지

```python
# ❌ 잘못된 예 - 순환 import 발생 가능
# selfhealing/core/dlq.py
from shopping.models import Order  # 쇼핑몰 모델 직접 import

# ✅ 올바른 예 - 인터페이스 사용
# selfhealing/core/dlq.py
from selfhealing.interfaces import FailedOperationRepository

class DLQService:
    def __init__(self, repository: FailedOperationRepository):
        self._repository = repository  # 의존성 주입
```

#### DB 연결 공유

```python
# 쇼핑몰과 selfhealing 패키지는 같은 DB를 사용해야 함
# Django settings.DATABASES를 공유

# selfhealing 패키지의 모델은 쇼핑몰의 Django 설정을 사용:
# - 별도 DB 설정 불필요
# - INSTALLED_APPS에 추가하면 자동으로 같은 DB 사용
```

#### 메트릭 엔드포인트 통합

```python
# 문제: selfhealing 패키지와 쇼핑몰 둘 다 메트릭 수집
# 해결: 단일 /metrics 엔드포인트에서 모두 노출

# urls.py
from prometheus_client import make_wsgi_app
from django.urls import path

# selfhealing 메트릭은 자동으로 prometheus_client 레지스트리에 등록
# 별도 설정 불필요
```

---

### 6.4 롤백 계획

분리 후 문제 발생 시 롤백 절차:

```bash
# 1. selfhealing 패키지 제거
pip uninstall selfhealing

# 2. 이전 코드 복원 (git)
git checkout HEAD~1 -- shopping/services/self_healing/
git checkout HEAD~1 -- shopping/models/failed_operation.py
git checkout HEAD~1 -- shopping/admin/dlq_admin.py
# ... 기타 파일들

# 3. 마이그레이션 롤백 (필요시)
python manage.py migrate shopping <previous_migration>

# 4. 재배포
```

---

### 6.5 체크리스트: 분리 완료 후 확인

#### 기능 테스트

- [ ] Circuit Breaker 정상 작동 (open/close/half-open)
- [ ] DLQ 저장 및 조회 정상
- [ ] Replay 기능 정상
- [ ] Control API 모든 엔드포인트 정상
- [ ] Admin 페이지 정상 표시
- [ ] Prometheus 메트릭 수집 정상
- [ ] Grafana 대시보드 정상 표시
- [ ] Celery 태스크 정상 실행

#### 성능 테스트

- [ ] 응답 시간 변화 없음 (±10% 이내)
- [ ] 메모리 사용량 변화 없음
- [ ] DB 쿼리 수 변화 없음

#### 운영 테스트

- [ ] 로그 정상 출력
- [ ] 에러 추적 (Sentry 등) 정상
- [ ] 알림 (Slack 등) 정상 발송

---

## 7. FAQ

### Q: 분리 후 테스트가 느려지나요?

**A**: 아니요. 오히려 Unit 테스트는 더 빨라질 수 있습니다.
- selfhealing 패키지의 Unit 테스트는 Django 없이 실행 가능
- Mock/Stub으로 빠르게 테스트

### Q: 두 저장소를 동시에 관리하기 어렵지 않나요?

**A**: 초기에는 약간의 오버헤드가 있지만:
- selfhealing은 안정화 후 변경 빈도 낮음
- 쇼핑몰 비즈니스 로직과 분리되어 관리 용이
- SaaS 판매 시 독립 배포 가능

### Q: 기존 데이터는 어떻게 되나요?

**A**: 마이그레이션으로 보존됩니다.
- 테이블 구조는 동일하게 유지
- Django 앱 레이블만 변경 (shopping → selfhealing_django)
- 데이터 마이그레이션 스크립트 제공

### Q: 롤백이 쉬운가요?

**A**: 네. Git으로 이전 상태 복원 가능.
- 각 Phase 완료 후 태그 생성 권장
- 문제 발생 시 이전 태그로 롤백

---

*이 문서는 Self-Healing 시스템 분리 프로젝트의 마스터 계획서입니다.*

---

## 8. 관련 문서 분석 (숨어있는 참조)

> ⚠️ 이 섹션은 분리 시 주의해야 할 문서들을 분류합니다.

### 8.1 분리 대상 문서 (함께 가져감)

| 문서 | 위치 | 설명 |
|------|------|------|
| L3_SELF_HEALING_SYSTEM.md | docs/ | 한글 요약 문서 (908줄) |
| self_healing/* | docs/self_healing/ | 전체 20+ 파일 |

### 8.2 관련되지만 분리하지 않는 문서

| 문서 | 위치 | 관계 | 조치 |
|------|------|------|------|
| **CELERY_RETRY_GUIDE.md** | docs/ | ⚠️ 관련되지만 별도 | Celery 레벨 설정 (L3 아님) |
| PAYMENT_SYSTEM_FIXES.md | docs/ | 결제 시스템 문서 | 쇼핑몰에 남음 |
| IMPLEMENTED_FEATURES.md | docs/ | 네비게이션 문서 | L3 섹션 링크 수정 |
| PRODUCTION_CHECKLIST.md | docs/ | 배포 체크리스트 | self-healing 관련 항목 분리 |
| INFRASTRUCTURE.md | docs/ | 인프라 설정 | 분리 불필요 (공통) |

### 8.3 CELERY_RETRY_GUIDE.md 상세 분석

이 문서는 **L3 Self-Healing과 관련이 있지만 별개 레이어**입니다:

\`\`\`
┌─────────────────────────────────────────┐
│  CELERY_RETRY_GUIDE.md 범위             │
│  = Celery Task 레벨 설정                │
│                                         │
│  • autoretry_for                        │
│  • retry_backoff (지수 백오프)           │
│  • retry_jitter (랜덤 지터)              │
│  • acks_late (늦은 ACK)                 │
│  • TOSS_RETRYABLE_ERRORS 상수           │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│  L3 Self-Healing 범위                   │
│  = 태스크 실패 후 복구                   │
│                                         │
│  • DLQ (Celery 최종 실패 후)             │
│  • Circuit Breaker (외부 서비스 보호)    │
│  • Replay Service (수동 재처리)          │
└─────────────────────────────────────────┘
\`\`\`

**결론**: CELERY_RETRY_GUIDE.md는 쇼핑몰에 남김. L3 Self-Healing은 "Celery가 포기한 후" 처리하는 레이어.

### 8.4 코드에서 문서와 불일치 확인 체크리스트

| 항목 | 문서 위치 | 코드 위치 | 일치 여부 |
|------|-----------|-----------|-----------|
| PaymentRecoveryHandler | L3_SELF_HEALING_SYSTEM.md:80 | payment_recovery_service.py | ✅ 일치 |
| CircuitBreakerService | SELF_HEALING_ARCHITECTURE.md | circuit_breaker_service.py | ✅ 일치 |
| DLQService | SELF_HEALING_OPERATIONS.md | dlq_service.py | ✅ 일치 |
| Prometheus 메트릭 | L3_SELF_HEALING_SYSTEM.md | metrics.py | ✅ 일치 |
| Control API | 5_CONTROL_API/*.md | self_healing_views.py | ✅ 일치 |

### 8.5 문서만으로 구현 가능한가?

**결론: 95% 가능, 5%는 코드 참조 필요**

✅ **문서만으로 가능한 것**:
- 아키텍처 이해
- API 스펙 (Control API)
- 설정 값 (backoff, threshold 등)
- 테스트 케이스 목록

⚠️ **코드 참조가 필요한 것**:
- 정확한 에러 핸들링 로직
- Django 모델 필드 상세
- Celery 태스크 데코레이터 옵션
- 실제 메트릭 이름 및 라벨

---

## 9. 추가 발견 파일 (업데이트)

### ��� 새로 발견된 분리 대상 파일

| 파일 | 설명 | 중요도 |
|------|------|--------|
| shopping/services/payment_recovery_service.py | PaymentRecoveryHandler ABC, Celery 구현 (467줄) | ��� 필수 |
| shopping/tasks/payment_recovery_tasks.py | DLQ 관련 Celery 태스크 | ��� 필수 |

### payment_recovery_service.py 핵심 내용

\`\`\`python
class PaymentRecoveryHandler(ABC):  # 추상 인터페이스
    @abstractmethod
    def handle_failure(...): ...
    @abstractmethod
    def schedule_retry(...): ...
    @abstractmethod
    def move_to_dlq(...): ...

class CeleryPaymentRecovery(PaymentRecoveryHandler):
    # Celery + Django ORM 기반 구현
\`\`\`

이 파일은 **L3 Self-Healing의 핵심 추상화 레이어**입니다.

### 업데이트된 파일 수

| 카테고리 | 이전 | 현재 |
|----------|------|------|
| 서비스 코드 | 14개 | **15개** (+1) |
| Celery 태스크 | 2개 | **3개** (+1) |
| **합계** | ~80개 | **~82개** (+2) |

---

## 10. Post-Plan Implementation Updates (2025-12-10)

> **Note**: This section documents changes made after the initial extraction plan was created.
> All additions below are required for complete SaaS package extraction.

### 10.1 New Configuration: SELF_HEALING Settings

**Files Modified**:
- `myproject/settings/local.py`
- `myproject/settings/production.py`
- `myproject/settings/test.py`

**Change**: Added centralized `SELF_HEALING` configuration dictionary.

```python
# myproject/settings/production.py (added)

SELF_HEALING = {
    "SLA": {
        "PAYMENT_HOURS": 1,
        "POINT_HOURS": 4,
        "INVENTORY_HOURS": 2,
        "WEBHOOK_HOURS": 8,
        "NOTIFICATION_HOURS": 24,
    },
    "RETRY": {
        "MAX_RETRIES": 5,
        "BACKOFF_BASE": 2,
        "BACKOFF_MAX": 300,
        "JITTER_PERCENT": 0.25,
    },
    "CIRCUIT_BREAKER": {
        "ENABLED": True,
        "FAILURE_THRESHOLD": 5,
        "SUCCESS_THRESHOLD": 3,
        "RECOVERY_TIMEOUT": 60,
    },
    "DLQ": {
        "ENABLED": True,
        "MAX_REPLAY_ATTEMPTS": 3,
        "REPLAY_DELAY_SECONDS": 60,
        "RETENTION_DAYS": 30,
    },
    "IDEMPOTENCY": {
        "DEFAULT_CACHE_TTL": 60,
        "PAYMENT_CACHE_TTL": 600,
        "WEBHOOK_CACHE_TTL": 120,
    },
}
```

**Impact on Extraction**: SaaS package must provide default `SELF_HEALING` settings template.

---

### 10.2 IdempotencyService: Redis Graceful Degradation

**File Modified**: `shopping/services/self_healing/idempotency_service.py` (+180 lines changed)

**Breaking Changes**:

| Method | Old Signature | New Signature |
|--------|--------------|---------------|
| `mark_as_processed()` | `-> None` | `-> bool` |
| `clear()` | `-> None` | `-> bool` |

**New Behavior**: All cache operations wrapped in try/except for graceful degradation.

```python
# Before (would crash on Redis failure)
cached_payment_id = cache.get(key.cache_key)

# After (gracefully degrades to DB-only)
try:
    cached_payment_id = cache.get(key.cache_key)
    if cached_payment_id:
        # ... cache hit logic
except Exception as e:
    logger.warning(f"[Idempotency] Cache unavailable, falling back to DB: {e}")
    # Continue to database check
```

**Affected Methods**:
- `check_payment()` - Cache-first, DB-fallback
- `check_payment_confirm()` - Cache-first, DB-fallback
- `check_webhook()` - Cache-first, DB-fallback
- `check_point_operation()` - Cache-first, DB-fallback
- `mark_as_processed()` - Returns `False` if cache fails
- `clear()` - Returns `False` if cache fails

**Impact on Extraction**: This is a **policy change** that makes the system Redis-independent. Must be included in SaaS package.

---

### 10.3 New Test Files

**Files Added**:

| File | Lines | Purpose |
|------|-------|---------|
| `test_celery_async_mode.py` | 317 | Tests Celery eager vs async mode differences |
| `test_redis_failure_scenarios.py` | 575 | Tests Redis graceful degradation (14 tests) |
| `test_transaction_task_timing.py` | 393 | Tests transaction/task timing issues |

**Test Classes in `test_redis_failure_scenarios.py`**:

```python
class TestCircuitBreakerRedisFailure:
    """3 tests - DB fallback verification"""

class TestIdempotencyServiceRedisFailure:
    """4 tests - Graceful degradation"""
    
class TestDLQServiceRedisFailure:
    """3 tests - DB-first design confirmation"""
    
class TestRateLimitTrackerCacheIndependence:
    """2 tests - In-memory operation"""
    
class TestCascadePreventionDuringRedisOutage:
    """2 tests - Full flow verification"""
```

**Impact on Extraction**: All tests must be included in SaaS package.

---

### 10.4 New Documentation

**Files Added**:

| File | Purpose |
|------|---------|
| `docs/self_healing/0_OVERVIEW/SAAS_READINESS.md` | SaaS readiness assessment (283 lines) |

**Updated Documentation**:

| File | Changes |
|------|---------|
| `SELF_HEALING_ARCHITECTURE.md` | §7 IdempotencyService architecture, §16 Infrastructure Resilience |
| `SELF_HEALING_TEST_GAP_ANALYSIS.md` | Marked Celery and Redis gaps as resolved |
| `SELF_HEALING_TEST_EXECUTION_GUIDE.md` | Added new test file references |

---

### 10.5 Infrastructure Resilience Policy

**New Policy**: All Self-Healing services must operate with PostgreSQL as the single point of truth.

**Service Resilience Matrix**:

| Service | PostgreSQL | Redis | Graceful Degradation |
|---------|-----------|-------|---------------------|
| DLQService | Required | Not used | ✅ DB-first by design |
| CircuitBreakerService | Required | Optional (fast lookup) | ✅ DB fallback |
| IdempotencyService | Required | Optional (fast lookup) | ✅ DB fallback (NEW) |
| RateLimitTracker | Not used | Not used | ✅ In-memory |
| BackoffCalculator | Not used | Not used | ✅ Stateless |

**Impact on Extraction**: SaaS package must document that Redis is **recommended but not required**.

---

### 10.6 Updated File Count Summary

| Category | Original Plan | Current | Delta |
|----------|--------------|---------|-------|
| Service Code | 14 | 15 | +1 |
| Celery Tasks | 2 | 3 | +1 |
| Unit Tests | 14 | 14 | 0 |
| Integration Tests | 22 | **25** | **+3** |
| Documentation | 28 | **29** | **+1** |
| Settings Files | 1 | **4** | **+3** |
| **Total** | ~81 | **~90** | **+9** |

---

### 10.7 Extraction Checklist Additions

Add these items to Phase 1 checklist:

- [ ] Include `SELF_HEALING` settings template in package
- [ ] Update `idempotency_service.py` with graceful degradation
- [ ] Include all 3 new test files
- [ ] Document Redis-optional policy
- [ ] Update return types for `mark_as_processed()` and `clear()`

Add these items to Phase 3 checklist:

- [ ] Create `SAAS_READINESS.md` in package docs
- [ ] Document infrastructure resilience policy
- [ ] Include `SELF_HEALING_ARCHITECTURE.md` §16

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2025-12-10 | 1.0 | Initial extraction plan created |
| 2025-12-10 | 1.1 | Added §10: Post-plan implementation updates |

