# Phase 1: Import Crash Gate 제거

> **목표**: 쇼핑 앱/Django 없이도 `python -c "import selfhealing"` 성공
>
> **핵심**: 코어 패키지에서 `from shopping.*` 직접 import 제거

---

## 상태

| 항목 | 상태 |
|------|------|
| 전체 진행 | ⬜ 미시작 |
| 예상 소요 | 30분 |
| 위험도 | 낮음 |

---

## 발견된 즉사 포인트

### 1-1. `packages/selfhealing-python` 내부 shopping import

| # | 파일 | 라인 | 문제 import | 즉사 여부 | 상태 |
|---|------|------|-------------|----------|------|
| 1 | `services/__init__.py` | 8 | `from shopping.services.self_healing import CircuitBreakerService` | ❌ 즉사 | ⬜ |
| 2 | `services/circuit_breaker/manual_control.py` | 286 | `from shopping.models.failed_payment import CircuitBreakerState` | ❌ 즉사 | ⬜ |
| 3 | `services/idempotency_service.py` | 348 | `from shopping.models.payment import Payment` | ⚠️ try 내부 | ⬜ |
| 4 | `services/idempotency_service.py` | 426 | `from shopping.models.payment import Payment` | ⚠️ try 내부 | ⬜ |
| 5 | `services/idempotency_service.py` | 496 | `from shopping.models.webhook_event import WebhookEvent` | ⚠️ try 내부 | ⬜ |
| 6 | `services/idempotency_service.py` | 566 | `from shopping.models.point import PointHistory` | ⚠️ try 내부 | ⬜ |
| 7 | `config.py` | 85-88 | `from shopping.services.self_healing.config import ...` | ⚠️ try-except 있음 | ⬜ |
| 8 | `adapters/django_repos/failed_operation.py` | 33 | `from shopping.models.failed_operation import FailedOperation` | ❌ 즉사 | ⬜ |
| 9 | `adapters/django_repos/circuit_breaker.py` | 33 | `from shopping.models.failed_payment import CircuitBreakerState` | ❌ 즉사 | ⬜ |
| 10 | `adapters/django_repos/security_incident.py` | 34 | `from shopping.models.security_incident import SecurityIncident` | ❌ 즉사 | ⬜ |
| 11 | `adapters/django_repos/rate_limit.py` | 28 | `from shopping.models.rate_limit_state import RateLimitState` | ❌ 즉사 | ⬜ |
| 12 | `adapters/django/repositories.py` | 43 | `from shopping.models.failed_operation import FailedOperation` | ❌ 즉사 | ⬜ |
| 13 | `adapters/django/repositories.py` | 349 | `from shopping.models.failed_payment import CircuitBreakerState` | ❌ 즉사 | ⬜ |
| 14 | `adapters/django/repositories.py` | 724 | `from shopping.models.security_incident import SecurityIncident` | ❌ 즉사 | ⬜ |
| 15 | `api/django/stress_views.py` | 309 | `FROM shopping_product` SQL 쿼리 | ⚠️ 실행 시 에러 | ⬜ |
| 16 | `api/django/views/dashboard.py` | 20 | `from shopping.models.failed_operation import FailedOperation` | ❌ 즉사 | ⬜ |
| 17 | `api/django/views/health.py` | 23 | `from shopping.models import CircuitBreakerState` | ❌ 즉사 | ⬜ |
| 18 | `api/django/views/circuit_breaker.py` | 36 | `from shopping.models import CircuitBreakerState` | ❌ 즉사 | ⬜ |
| 19 | `api/django/views/circuit_breaker.py` | 37 | `from shopping.models.failed_operation import FailedOperation` | ❌ 즉사 | ⬜ |
| 20 | `api/django/views/dlq.py` | 23 | `from shopping.models.failed_operation import FailedOperation` | ❌ 즉사 | ⬜ |

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

- [ ] `services/__init__.py#L8`: fallback import 제거 → 내부 CircuitBreakerService 사용
- [ ] `manual_control.py#L286`: lazy import + try-except 적용
- [ ] `idempotency_service.py`: 이미 try-except 있으나 완전 제거 권장 → 콜백 주입 패턴으로 변경
- [ ] `config.py#L85-88`: try-except 유지하되 fallback 기본값 강화

### 1-2: Django 어댑터 레이어 정리

- [ ] `adapters/django_repos/` 전체: lazy import로 변경
- [ ] `adapters/django/repositories.py`: lazy import로 변경
- [ ] `api/django/stress_views.py`: SQL 쿼리에서 테이블명 동적 처리

### 1-3: Django Views 정리 (추가 발견)

- [ ] `api/django/views/dashboard.py#L20`: lazy import로 변경
- [ ] `api/django/views/health.py#L23`: lazy import로 변경
- [ ] `api/django/views/circuit_breaker.py#L36-37`: lazy import로 변경
- [ ] `api/django/views/dlq.py#L23`: lazy import로 변경

### 1-4: 코어→어댑터 역방향 import 확인

- [ ] 코어 패키지가 `adapters/django_repos/`를 import하지 않음 확인
- [ ] 어댑터 초기화는 entry point에서만 수행

---

## 검증

```bash
# Empty Host Import Test (Django 없는 환경에서 실행)
cd packages/selfhealing-python
python -c "import selfhealing; print('OK')"

# 성공: 아무 에러 없이 종료
# 실패: ImportError 발생

# shopping import 0건 확인
grep -rn --include="*.py" "from shopping\|import shopping" src/selfhealing/
# 목표: 0건 또는 try-except 내부만 허용
```

---

## 완료 조건

- [ ] `python -c "import selfhealing"` Django 없이 성공
- [ ] `packages/selfhealing-python/` 내 즉사 import 0건
- [ ] shopping import는 try-except 또는 lazy import로만 존재

---

*문서 생성일: 2025-12-14*
