# Phase 1: Import Crash Gate 제거

> **목표**: 쇼핑 앱/Django 없이도 `python -c "import selfhealing"` 성공
>
> **핵심**: 코어 패키지에서 `from shopping.*` 직접 import 제거

---

## 상태

| 항목 | 상태 |
|------|------|
| 전체 진행 | ✅ 완료 |
| 실제 소요 | 20분 |
| 위험도 | 낮음 |
| 완료일 | 2025-12-14 |

---

## 발견된 즉사 포인트

### 1-1. `packages/selfhealing-python` 내부 shopping import

| # | 파일 | 라인 | 문제 import | 즉사 여부 | 상태 |
|---|------|------|-------------|----------|------|
| 1 | `services/__init__.py` | 8 | `from shopping.services.self_healing import CircuitBreakerService` | ⚠️ docstring 예시 | ✅ 수정 불필요 |
| 2 | `services/circuit_breaker/manual_control.py` | 286 | `from shopping.models.failed_payment import CircuitBreakerState` | ⚠️ try 내부 | ✅ 기존 OK |
| 3 | `services/idempotency_service.py` | 348 | `from shopping.models.payment import Payment` | ⚠️ try 내부 | ✅ 이미 제거됨 |
| 4 | `services/idempotency_service.py` | 426 | `from shopping.models.payment import Payment` | ⚠️ try 내부 | ✅ 이미 제거됨 |
| 5 | `services/idempotency_service.py` | 496 | `from shopping.models.webhook_event import WebhookEvent` | ⚠️ try 내부 | ✅ 이미 제거됨 |
| 6 | `services/idempotency_service.py` | 566 | `from shopping.models.point import PointHistory` | ⚠️ try 내부 | ✅ 이미 제거됨 |
| 7 | `config.py` | 85-88 | `from shopping.services.self_healing.config import ...` | ⚠️ try-except 있음 | ✅ 기존 OK |
| 8 | `adapters/django_repos/failed_operation.py` | 33 | `from shopping.models.failed_operation import FailedOperation` | ❌ 즉사 | ✅ try-except 추가 |
| 9 | `adapters/django_repos/circuit_breaker.py` | 33 | `from shopping.models.failed_payment import CircuitBreakerState` | ❌ 즉사 | ✅ try-except 추가 |
| 10 | `adapters/django_repos/security_incident.py` | 34 | `from shopping.models.security_incident import SecurityIncident` | ❌ 즉사 | ✅ try-except 추가 |
| 11 | `adapters/django_repos/rate_limit.py` | 28 | `from shopping.models.rate_limit_state import RateLimitState` | ⚠️ try 내부 | ✅ 기존 OK |
| 12 | `adapters/django/repositories.py` | 43 | `from shopping.models.failed_operation import FailedOperation` | ⚠️ try 내부 | ✅ 기존 OK (fallback 있음) |
| 13 | `adapters/django/repositories.py` | 349 | `from shopping.models.failed_payment import CircuitBreakerState` | ⚠️ try 내부 | ✅ 기존 OK (fallback 있음) |
| 14 | `adapters/django/repositories.py` | 724 | `from shopping.models.security_incident import SecurityIncident` | ⚠️ try 내부 | ✅ 기존 OK (fallback 있음) |
| 15 | `api/django/stress_views.py` | 309 | `FROM shopping_product` SQL 쿼리 | ⚠️ 실행 시 에러 | ✅ 런타임만, 즉사 아님 |
| 16 | `api/django/views/dashboard.py` | 20 | `from shopping.models.failed_operation import FailedOperation` | ❌ 즉사 | ✅ lazy import 적용 |
| 17 | `api/django/views/health.py` | 23 | `from shopping.models import CircuitBreakerState` | ❌ 즉사 | ✅ lazy import 적용 |
| 18 | `api/django/views/circuit_breaker.py` | 36 | `from shopping.models import CircuitBreakerState` | ❌ 즉사 | ✅ lazy import 적용 |
| 19 | `api/django/views/circuit_breaker.py` | 37 | `from shopping.models.failed_operation import FailedOperation` | ❌ 즉사 | ✅ lazy import 적용 |
| 20 | `api/django/views/dlq.py` | 23 | `from shopping.models.failed_operation import FailedOperation` | ❌ 즉사 | ✅ lazy import 적용 |

---

## 처리 방안

### 방안 A: Lazy Import + try-except (권장)

```python
# Before (즉사)
from shopping.models.failed_operation import FailedOperation

# After (안전)
def get_model(self):
    try:
        from shopping.models.failed_operation import FailedOperation
        return FailedOperation
    except ImportError:
        raise RuntimeError(
            "Django adapter requires shopping app. "
            "Install shopping app or use a different adapter."
        )
```

### 방안 B: 어댑터 레이어 분리

```
packages/selfhealing-python/
└── src/selfhealing/
    ├── adapters/
    │   ├── django/          # Django 공용 (shopping 없이 동작)
    │   ├── django_repos/    # ⚠️ shopping 의존 → 별도 패키지로 분리
    │   └── memory/          # 인메모리 (테스트용)
```

**권장**: `django_repos/`를 별도 패키지(`selfhealing-shopping`)로 분리하거나, lazy import로 변경

---

## 작업 체크리스트

### 1-1: shopping import 제거

- [x] `services/__init__.py#L8`: docstring 예시로 확인, 실제 import 아님
- [x] `manual_control.py#L286`: 이미 try-except 내부 (기존 OK)
- [x] `idempotency_service.py`: shopping import 이미 제거됨 (확인 완료)
- [x] `config.py#L85-88`: try-except 유지 (기존 OK)

### 1-2: Django 어댑터 레이어 정리

- [x] `adapters/django_repos/failed_operation.py`: try-except + 명확한 에러 메시지 추가
- [x] `adapters/django_repos/circuit_breaker.py`: try-except + 명확한 에러 메시지 추가
- [x] `adapters/django_repos/security_incident.py`: try-except + 명확한 에러 메시지 추가
- [x] `adapters/django_repos/rate_limit.py`: 이미 try-except 있음 (기존 OK)
- [x] `adapters/django/repositories.py`: 이미 try-except + fallback 있음 (기존 OK)
- [x] `api/django/stress_views.py`: SQL 쿼리는 런타임 에러, 즉사 아님

### 1-3: Django Views 정리 (추가 발견)

- [x] `api/django/views/dashboard.py#L20`: lazy import 헬퍼 함수 적용
- [x] `api/django/views/health.py#L23`: lazy import 헬퍼 함수 적용
- [x] `api/django/views/circuit_breaker.py#L36-37`: lazy import 헬퍼 함수 적용
- [x] `api/django/views/dlq.py#L23`: lazy import 헬퍼 함수 적용

### 1-4: 코어→어댑터 역방향 import 확인

- [x] 코어 패키지가 `adapters/django_repos/`를 import하지 않음 확인
- [x] 어댑터 초기화는 entry point에서만 수행

---

## 검증 결과

```bash
# Empty Host Import Test (Django 없는 환경에서 실행)
cd packages/selfhealing-python
python -c "import selfhealing; print('OK')"
# 결과: SUCCESS: selfhealing imported without Django/shopping ✅

# shopping import 검색 결과
grep -rn "from shopping\|import shopping" src/selfhealing/
# 결과: 모든 shopping import가 try-except 블록 또는 함수 내부에 위치 ✅
```

---

## 완료 조건

- [x] `python -c "import selfhealing"` Django 없이 성공 ✅
- [x] `packages/selfhealing-python/` 내 즉사 import 0건 ✅
- [x] shopping import는 try-except 또는 lazy import로만 존재 ✅

---

## 수정된 파일 목록

1. `adapters/django_repos/failed_operation.py` - _get_model()에 try-except 추가
2. `adapters/django_repos/circuit_breaker.py` - _get_model()에 try-except 추가
3. `adapters/django_repos/security_incident.py` - _get_model()에 try-except 추가
4. `api/django/views/dashboard.py` - lazy import 헬퍼 함수 추가
5. `api/django/views/health.py` - lazy import 헬퍼 함수 추가
6. `api/django/views/circuit_breaker.py` - lazy import 헬퍼 함수 추가
7. `api/django/views/dlq.py` - lazy import 헬퍼 함수 추가

---

*문서 생성일: 2025-12-14*
*완료일: 2025-12-14*
