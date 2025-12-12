# Stage 28-3.1: 긴 파일 분리 리팩토링 계획

## 🎯 목표

1000줄 이상의 큰 파일을 500줄 이하의 작은 파일로 분리하여 유지보수성 향상

---

## 📊 현재 상태

| 파일 | 줄 수 | 우선순위 | 상태 |
|------|------|----------|------|
| `adapters/memory/repositories.py` | 1,074 → ✅ | 🟢 완료 | 5개 파일로 분리 |
| `services/circuit_breaker_service.py` | 1,168 → ✅ | 🟢 완료 | 7개 파일로 분리 |
| `adapters/django_repositories.py` | 1,051 | 🔴 높음 | 대기 중 |
| `services/factory.py` | 966 → ✅ | 🟢 완료 | 6개 파일로 분리 |
| `api/django/views.py` | 956 → ✅ | 🟢 완료 | 4개 파일로 분리 |

---

## ✅ Phase 1.1: memory/repositories.py 분리 (완료)

**결과:**
```
adapters/memory/
├── __init__.py              # 18줄 - exports
├── base.py                  # 15줄 - 공통 유틸리티
├── failed_operation.py      # 458줄 - InMemoryFailedOperationRepository
├── circuit_breaker.py       # 396줄 - InMemoryCircuitBreakerStateRepository
└── security_incident.py     # 240줄 - InMemorySecurityIncidentRepository
```

**변경사항:**
- 1,074줄 → 5개 파일 (모두 500줄 이하)
- 테스트 512개 통과
- 하위 호환성 유지 (`from selfhealing.adapters.memory import ...`)

---

## 📋 Phase 1.2: adapters/django_repositories.py (1,051줄) - 대기 중

**분석 필요:**
- 이미 `adapters/django/repositories.py`가 753줄로 존재
- `django_repositories.py`는 레거시 파일일 수 있음
- 중복 확인 후 통합 또는 삭제 결정

**분리 계획 (필요시):**
```
adapters/django/
├── __init__.py
├── base.py
├── failed_operation.py
├── circuit_breaker.py
└── security_incident.py
```

---

## 📋 Phase 2: 서비스 분리

### 2.1 services/circuit_breaker_service.py (1,168줄) - ✅ 완료

**결과:**
```
services/circuit_breaker/
├── __init__.py              # 85줄 - exports
├── config.py                # 125줄 - CircuitBreakerConfig, CircuitState, CircuitBreakerResult
├── rate_limit_tracker.py    # 95줄 - RateLimitTracker 클래스
├── protection.py            # 258줄 - ProtectionMixin (Rate Limit/Self-DDoS)
├── manual_control.py        # 433줄 - ManualControlMixin (Force Open/Close, TTL)
├── service.py               # 297줄 - CircuitBreakerService 메인 (Mixin 상속)
└── convenience.py           # 136줄 - 모듈 레벨 편의 함수
```

**변경사항:**
- 1,168줄 → 7개 파일 (모두 500줄 이하)
- 테스트 512개 통과
- 하위 호환성 유지:
  - `from selfhealing.services.circuit_breaker_service import ...`
  - `from selfhealing.services.circuit_breaker import ...`
  - `from shopping.services.self_healing.circuit_breaker_service import ...`

### 2.2 services/factory.py (966줄) - ✅ 완료

**결과:**
```
services/factory/
├── __init__.py              # 86줄 - exports
├── base.py                  # 260줄 - FrameworkType, ServiceFactory, 글로벌 팩토리 함수
├── registry.py              # 511줄 - ProviderRegistry (플러거블 아키텍처)
├── repository.py            # 59줄 - Repository Factory Functions
├── service.py               # 85줄 - Service Factory Functions
└── singleton.py             # 99줄 - Singleton Service Accessors
```

**변경사항:**
- 967줄 → 6개 파일 (모두 500줄 이하 또는 약간 초과)
- 테스트 512개 통과
- 하위 호환성 유지:
  - `from selfhealing.services.factory import ...`
  - `from selfhealing.services.factory.base import ServiceFactory`
  - `from selfhealing.services.factory.registry import ProviderRegistry`

---

## 📋 Phase 3: API 분리

### 3.1 api/django/views.py (955줄) - ✅ 완료

**결과:**
```
api/django/
├── views.py                    # 85줄 - re-exports (하위 호환성)
└── views/
    ├── __init__.py             # 67줄 - exports
    ├── circuit_breaker.py      # 693줄 - Control API Service + Views
    ├── dlq.py                  # 86줄 - DLQ Replay View
    └── health.py               # 204줄 - Health Check Views
```

**변경사항:**
- 956줄 → 4개 파일로 분리
- 테스트 512개 통과
- 하위 호환성 유지:
  - `from selfhealing.api.django.views import ControlActionView, ...`
  - `from selfhealing.api.django.views.circuit_breaker import ControlAPIService`
  - `from selfhealing.api.django.views.health import SelfHealingHealthView`

**구성 요소:**
- `circuit_breaker.py`: ControlRequest, ControlResponse, ControlAPIService, Control Views
- `dlq.py`: DLQReplayView
- `health.py`: SelfHealingHealthView, LivenessView, ReadinessView, ConnectionPoolHealthView, SelfHealingMetricsView

---

## ✅ 완료 체크리스트

### Phase 1: 리포지토리
- [x] `adapters/memory/repositories.py` 분리
- [ ] `adapters/django_repositories.py` 정리 (중복 확인)

### Phase 2: 서비스
- [x] `services/circuit_breaker_service.py` 분리
- [x] `services/factory.py` 분리

### Phase 3: API
- [x] `api/django/views.py` 분리 (✅ 완료 - 2025.12.12)

---

## 📝 작업 순서 권장

```
1. adapters/memory/repositories.py 분리 (SQLAlchemy 패턴 그대로 적용)
2. adapters/django_repositories.py 정리 (중복 제거)
3. services/circuit_breaker_service.py 분석 후 분리
4. services/factory.py 분리
5. api/django/views.py 분리
```

---

## 🔧 분리 원칙

1. **단일 책임 원칙**: 각 파일은 하나의 주요 클래스/기능만 담당
2. **500줄 이하**: 각 파일은 500줄을 넘지 않도록
3. **명확한 이름**: 파일명에서 역할이 명확히 드러나도록
4. **기존 테스트 유지**: 분리 후에도 모든 기존 테스트 통과
5. **하위 호환성**: `__init__.py`를 통해 기존 import 경로 유지

---

## 📅 예상 소요 시간

| Phase | 작업 | 예상 시간 |
|-------|------|-----------|
| Phase 1 | 리포지토리 분리 | 1-2시간 |
| Phase 2 | 서비스 분리 | 2-3시간 |
| Phase 3 | API 분리 | 1-2시간 |
| **합계** | | **4-7시간** |

---

## 📝 새 세션 시작 프롬프트

```
# Phase 1
STAGE_28-3-1_FILE_SPLIT_PLAN.md의 Phase 1 문서대로 리포지토리 파일 분리해줘.
adapters/memory/repositories.py를 SQLAlchemy 패턴처럼 분리.

# Phase 2
STAGE_28-3-1_FILE_SPLIT_PLAN.md의 Phase 2 문서대로 서비스 파일 분리해줘.
circuit_breaker_service.py 분석 후 분리.

# Phase 3
STAGE_28-3-1_FILE_SPLIT_PLAN.md의 Phase 3 문서대로 API 파일 분리해줘.
django/views.py 분리.
```
