# X-Test 컨텍스트 기반 글로벌 태깅

**문서 번호:** 137  
**작성일:** 2026-01-27  
**상태:** 설계 완료  
**선행 문서:** 116-123 (X-Test-Mode 시리즈)

---

## 1. 목적

X-Test-Mode에서 발생한 메트릭과 에러가 운영 지표를 오염시키지 않도록 글로벌 컨텍스트 기반 태깅 시스템을 구축한다.

### 1.1 해결 대상 문제

| 문제 | 현재 상태 | 목표 |
|------|----------|------|
| 메트릭 오염 | Prometheus 메트릭에 `is_test` 레이블 없음 | 모든 메트릭에 `is_test` 레이블 추가 |
| 에러 버짓 오염 | X-Test 에러가 실제 버짓 소진 | 테스트 에러 자동 제외 |
| 데이터 혼재 | 동일 Redis 키 공간 사용 | 동적 Namespace 분리 |

---

## 2. 현재 상태 분석

### 2.1 기존 컨텍스트 전파 패턴

| 파일 | ContextVar | 용도 |
|------|------------|------|
| `adapters/http_client.py` | `_is_chaos_request` | Chaos 실험 플래그 |
| `core/causation_context.py` | `_causation_info` | 인과관계 추적 |
| `core/actor_context.py` | `_current_actor` | 현재 Actor 추적 |

### 2.2 기존 메트릭 구조

| 파일 | 역할 |
|------|------|
| `services/metrics/definitions.py` | Counter, Gauge, Histogram 정의 |
| `services/metrics/recorders.py` | `record_*` 함수들 |
| `services/metrics/registry.py` | `get_or_create_counter` 헬퍼 |

### 2.3 기존 에러 버짓 제외 패턴

| 파일 | 메서드 | 파라미터 |
|------|--------|----------|
| `services/error_budget/calculator.py` | `calculate_budget_status()` | `exclude_chaos: bool = True` |

---

## 3. 설계

### 3.1 TestModeContext 설계

**위치:** `core/test_mode_context.py` (신규)

**네이밍 선택: `exclude_synthetic`**

| 후보 | 선택 | 이유 |
|------|------|------|
| `exclude_test` | ❌ | 단위 테스트와 혼동 가능 |
| `exclude_xtest` | ❌ | X-Test 전용, 확장성 제한 |
| `exclude_synthetic` | ✅ | Chaos/X-Test/합성 트래픽 모두 포괄, 기존 `X-Self-Healing-Synthetic` 헤더와 일관성 |

**핵심 구성요소:**

| 구성요소 | 역할 |
|---------|------|
| `_is_synthetic_request: ContextVar[bool]` | 현재 요청이 합성(테스트) 요청인지 |
| `_synthetic_session_id: ContextVar[str]` | 테스트 세션 식별자 |
| `TestModeContext.start()` | 컨텍스트 시작 (context manager) |
| `TestModeContext.is_synthetic()` | 현재 합성 요청 여부 조회 |

### 3.2 메트릭 레이블 확장

**수정 대상:** `services/metrics/definitions.py`

| 메트릭 | 추가 레이블 |
|--------|------------|
| `dlq_items_total` | `is_synthetic` |
| `retry_attempts_total` | `is_synthetic` |
| `circuit_breaker_state_changes` | `is_synthetic` |
| `replay_attempts_total` | `is_synthetic` |
| `error_budget_consumed` | `is_synthetic` |

### 3.3 ErrorBudgetCalculator 확장

**수정 대상:** `services/error_budget/calculator.py`

| 기존 파라미터 | 확장 |
|--------------|------|
| `exclude_chaos: bool` | `exclude_synthetic: bool` (Chaos + X-Test 통합) |

**통합 로직:**
- `exclude_synthetic=True` 시 `is_chaos_experiment=True` 또는 `source="x-test-mode"` 모두 제외
- 기존 `exclude_chaos`는 deprecated 처리 후 내부적으로 `exclude_synthetic` 호출

### 3.4 동적 Redis Namespace 분리

**수정 대상:** `repositories/resilient_backend.py`, `settings/namespace.py`

| 모드 | Key Prefix | 예시 |
|------|-----------|------|
| 운영 | `selfhealing:` | `selfhealing:dlq:pending` |
| 테스트 | `xtest:selfhealing:` | `xtest:selfhealing:dlq:pending` |

**동적 선택 로직:**

| 파일 | 메서드 | 역할 |
|------|--------|------|
| `settings/namespace.py` | `get_effective_key_prefix()` | `TestModeContext.is_synthetic()` 확인 후 prefix 반환 |
| `repositories/resilient_backend.py` | `_get_key()` | 동적 prefix 적용 |

---

## 4. 구현 순서

### Step 1: TestModeContext 생성

**파일:** `core/test_mode_context.py`

| 순서 | 항목 |
|------|------|
| 1-1 | `_is_synthetic_request: ContextVar[bool]` 정의 |
| 1-2 | `_synthetic_session_id: ContextVar[Optional[str]]` 정의 |
| 1-3 | `TestModeContext` 클래스 구현 |
| 1-4 | `start()`, `is_synthetic()`, `get_session_id()` 메서드 |

### Step 2: XTestModeMixin 연동

**파일:** `api/django/views/xtest/base.py`

| 순서 | 항목 |
|------|------|
| 2-1 | 요청 진입 시 `TestModeContext.start()` 호출 |
| 2-2 | `X-Test-Session` 헤더를 세션 ID로 전달 |

### Step 3: 메트릭 레이블 추가

**파일:** `services/metrics/definitions.py`, `services/metrics/recorders.py`

| 순서 | 항목 |
|------|------|
| 3-1 | 주요 메트릭에 `is_synthetic` 레이블 추가 |
| 3-2 | `record_*` 함수에서 `TestModeContext.is_synthetic()` 자동 감지 |

### Step 4: ErrorBudgetCalculator 확장

**파일:** `services/error_budget/calculator.py`

| 순서 | 항목 |
|------|------|
| 4-1 | `exclude_synthetic` 파라미터 추가 |
| 4-2 | `exclude_chaos` deprecated 처리 |
| 4-3 | 필터링 로직에 `source="x-test-mode"` 조건 추가 |

### Step 5: 동적 Redis Namespace

**파일:** `settings/namespace.py`, `repositories/resilient_backend.py`

| 순서 | 항목 |
|------|------|
| 5-1 | `get_effective_key_prefix()` 함수 추가 |
| 5-2 | `ResilientStorageConfig`에서 동적 prefix 지원 |
| 5-3 | DLQ, CB 등 Repository에 적용 |

### Step 6: __init__.py 업데이트

**파일:** `core/__init__.py`

| 순서 | 항목 |
|------|------|
| 6-1 | `TestModeContext` export 추가 |

---

## 5. 테스트 계획

### 5.1 단위 테스트

| 테스트 케이스 | 검증 항목 |
|--------------|----------|
| `test_synthetic_context_propagation` | ContextVar 전파 확인 |
| `test_metric_is_synthetic_label` | 메트릭 레이블 자동 설정 |
| `test_error_budget_exclude_synthetic` | 합성 에러 버짓 제외 |
| `test_dynamic_key_prefix` | 동적 prefix 선택 |

### 5.2 통합 테스트

| 테스트 시나리오 |
|----------------|
| X-Test API 호출 → 메트릭에 `is_synthetic="true"` 확인 |
| X-Test 에러 주입 → 에러 버짓 미소진 확인 |
| X-Test DLQ 주입 → `xtest:selfhealing:dlq:*` 키 생성 확인 |

---

## 6. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `adapters/http_client.py` | `_is_chaos_request` ContextVar 패턴 |
| `services/error_budget/calculator.py` | `exclude_chaos` 파라미터 |
| `settings/namespace.py` | `get_effective_namespace()` 동적 결정 패턴 |
| `repositories/resilient_backend.py` | `ResilientStorageConfig.key_prefix` |
| `api/django/views/xtest/base.py` | `XTestModeMixin` 클래스 |

---

**다음 문서:** 138_XTEST_PERMISSION_DUAL_LOCK.md
