# 206. services/ 디렉토리 구조 통일 계획

> **상태**: 🔧 1차 정리 완료 (2026-02-09)
> **목적**: `services/` 하위의 플랫 파일(단일 `.py`)과 패키지(디렉토리) 공존 문제를 정리한다.

---

## 1. 현황

### 1-1. services/ 최상위 구조

`services/` 디렉토리에는 **패키지(디렉토리)**와 **플랫 .py 파일**이 혼재:

#### 패키지 (디렉토리) — 28개

```
services/
├── audit/
├── auto_tuning/
├── blast_radius/
├── canary/
├── chaos/
├── circuit_breaker/
├── compliance/
├── config/
├── coordination/
├── corruption_shield/
├── daily_report/
├── dlq/
├── emergency_mode/
├── error_budget/
├── error_budget_gate/
├── factory/
├── finops/
├── isolation/
├── learning/
├── metrics/
├── namespace_emergency/
├── postmortem/
├── rate_limit/
├── rollback/
├── runtime_config/
├── security/
├── security_notification/
└── throttle/
```

#### 플랫 파일 — 약 30개

```
services/
├── adaptive_replay.py              (317줄)
├── audit_helpers.py                (121줄)
├── backoff_calculator.py           (822줄) ← 500줄+ 패키지 전환 대상
├── chaos_context.py                (415줄)
├── circuit_breaker_service.py      ← backward compat shim (deprecation warning 추가 완료)
├── cleanup_service.py              (345줄)
├── config_history.py               (512줄) ← 500줄+ 패키지 전환 대상
├── control_api_service.py          (871줄) ← 500줄+ 패키지 전환 대상
├── dashboard_service.py            (538줄) ← 500줄+ 패키지 전환 대상
├── dlq_models.py                   ← backward compat shim (dlq/models.py로 이전 완료)
├── dlq_service.py                  ← backward compat shim (deprecation warning 추가 완료)
├── error_budget_service.py         ← backward compat shim (deprecation warning 추가 완료)
├── event_bus.py                    (1,875줄) ← 500줄+ 패키지 전환 1순위
├── event_bus_redis.py              (377줄) ← event_bus.py와 동일 도메인
├── execution_services.py           (730줄) ← 500줄+ 패키지 전환 대상
├── forensic_audit_bridge.py        (476줄)
├── governance.py                   (547줄) ← governance 도메인 4파일 중 1
├── governance_api_service.py       (526줄) ← governance 도메인 4파일 중 2
├── governance_checks.py            (863줄) ← governance 도메인 4파일 중 3
├── governance_service.py           (435줄) ← governance 도메인 4파일 중 4
├── healing_events_store.py         (285줄)
├── health_check.py                 (378줄)
├── http_client.py                  (335줄)
├── idempotency_service.py          (1,160줄) ← 500줄+ 패키지 전환 대상
├── metric_sync_service.py          (402줄)
├── pending_config.py               (361줄)
├── postmortem_store.py             ← backward compat shim (postmortem/store.py로 이전 완료)
├── precomputed_cache.py            (690줄) ← 500줄+ 패키지 전환 대상
├── rate_limit_coordinator.py       (576줄) ← 500줄+ 패키지 전환 대상
├── replay_service.py               (940줄) ← 500줄+ 패키지 전환 대상, dlq/replay_operations.py와 기능 분산
├── retry_handler.py                (827줄) ← 500줄+ 패키지 전환 대상
├── stress_test_service.py          (842줄) ← 500줄+ 패키지 전환 대상
├── system_control.py               (421줄)
├── unified_notification.py         (945줄) ← 500줄+ 패키지 전환 대상
├── xtest_cleanup_service.py        (445줄)
└── xtest_session_manager.py        (397줄)
```

### 1-2. Backward Compatibility Shim 파일 (5건 → 4건 실존)

이미 패키지로 분리되었지만, 이전 import 경로 호환을 위해 남겨진 파일:

#### (1) `services/circuit_breaker_service.py` → `services/circuit_breaker/` — ✅ deprecation warning 추가 완료

```python
# services/circuit_breaker_service.py (77줄 → shim + DeprecationWarning)
"""
Circuit Breaker Service - Compatibility Module

.. deprecated:: 2.0.0
    Import from ``selfhealing.services.circuit_breaker`` instead.
    This shim will be removed in v3.0.0.
"""
import warnings
warnings.warn(
    "Importing from 'selfhealing.services.circuit_breaker_service' is deprecated. "
    "Use 'selfhealing.services.circuit_breaker' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning, stacklevel=2,
)
# → services/circuit_breaker/ 패키지로 재export
```

**참조 현황** (20건+):
- `services/event_bus.py` (L672, L951)
- `services/throttle/adaptive.py` (L1808)
- `services/xtest_cleanup_service.py` (L218)
- `services/__init__.py` (L67-79)
- `tasks/traffic_aware_replay.py` (L74)
- 테스트 다수

#### (2) `services/dlq_service.py` → `services/dlq/` — ✅ deprecation warning 추가 완료

```python
# services/dlq_service.py (59줄 → shim + DeprecationWarning)
"""
Dead Letter Queue (DLQ) Service - Backward Compatibility Wrapper

.. deprecated:: 2.0.0
    Import from ``selfhealing.services.dlq`` instead.
    This shim will be removed in v3.0.0.
"""
```

**참조 현황** (20건+):
- `services/factory/service.py`, `services/factory/base.py`
- `services/cleanup_service.py` (L89, L253)
- `services/circuit_breaker/service.py` (L395)
- `services/throttle/dlq_integration.py` (L80)
- `services/__init__.py` (L108-109)
- 테스트 다수

#### (3) ~~`services/factory.py`~~ — ❌ 실제 존재하지 않음 (factory/ 디렉토리만 존재)

원래 문서에서 deprecated shim으로 기술했으나 `factory.py` 파일은 존재하지 않음.
`factory/` 디렉토리만 있으며 Python 모듈 해석에서 패키지가 우선 로드되므로 문제없음.
`from selfhealing.services.factory import ...` 참조는 모두 `factory/__init__.py` 경유.

#### (4) `services/error_budget_service.py` → `services/error_budget/` — ✅ deprecation warning 추가 완료

```python
# services/error_budget_service.py (80줄 → shim + DeprecationWarning)
"""
Error Budget Service (Re-export Module)

.. deprecated:: 2.0.0
    Import from ``selfhealing.services.error_budget`` instead.
    This shim will be removed in v3.0.0.
"""
```

**참조 현황** (20건+):
- `services/throttle/adaptive.py` (L1002, L1843)
- `services/error_budget_gate/gate.py` (L130)
- `services/chaos/stop_conditions.py`, `services/chaos/safety_guard/guard.py`, `services/chaos/reports.py`
- `api/django/views/` 다수
- 테스트 다수

#### (5) `services/postmortem_store.py` → `services/postmortem/store.py` — ✅ 이전 완료

866줄의 실제 구현을 `postmortem/store.py`로 이전 완료.
`postmortem_store.py`는 deprecation shim으로 전환.

**참조 현황** (20건+):
- `services/event_bus.py` (L945, L1297)
- `services/postmortem/revision.py` (L849) → `.store`로 변경 완료
- `services/postmortem/incident_group.py` (L584) → `.store`로 변경 완료
- 테스트 다수 (이전 경로로 import — shim 경유 동작)

### 1-3. Legacy 플랫 모델 파일 — ✅ 이전 완료

```python
# services/dlq_models.py → services/dlq/models.py 이전 완료
"""
DLQ Models and Data Classes - Backward Compatibility Shim

.. deprecated:: 2.0.0
    Import from ``selfhealing.services.dlq.models`` instead.
    This shim will be removed in v3.0.0.
"""
```

`dlq/__init__.py` 및 `dlq/replay_operations.py`, `dlq/store_operations.py` 내부 import도
`selfhealing.services.dlq.models`로 변경 완료.

**dlq_models.py 참조 현황** (20건+, 외부에서의 import — shim 경유 동작):
- `dlq/replay_operations.py` (L69, L159, L262) → `.models`로 변경 완료
- `dlq/store_operations.py` (L77) → `.models`로 변경 완료
- `dlq/__init__.py` (L22) → `.models`로 변경 완료
- 테스트 다수 (이전 경로로 import — shim 경유 동작)

---

## 2. 문제점

| 항목 | 설명 | 상태 |
|------|------|------|
| **import 경로 혼란** | `from services.dlq_service import DLQService` vs `from services.dlq import DLQService` — 어느 것이 정식인지 불분명 | ✅ deprecation warning으로 명시 |
| **Shim 파일 유지 비용** | 4개 shim 파일이 패키지와 동일 이름으로 존재하여 모듈 해석 혼란 가능 | ✅ v3.0.0 삭제 예약 |
| **postmortem_store.py 이중 역할** | 866줄의 실제 구현 + backward compat을 겸하여 역할이 불명확 | ✅ postmortem/store.py로 이전 |
| **dlq_models.py 고아 파일** | 패키지 내 모델과 플랫 파일 모델이 이중 존재 | ✅ dlq/models.py로 이전 |
| **500줄+ 플랫 파일 16개 미정리** | 패키지 전환 기준 충족하나 아직 패키지로 전환되지 않음 | 🔜 2차 계획 |
| **event_bus 도메인 분산** | event_bus.py (1,875줄) + event_bus_redis.py (377줄) 동일 도메인 | 🔜 2차 계획 |
| **governance 도메인 분산** | 4개 파일 합계 2,371줄이 동일 도메인에서 플랫 파일로 존재 | 🔜 2차 계획 |
| **replay_service.py / dlq 기능 분산** | replay_service.py (940줄)와 dlq/replay_operations.py (296줄) 양쪽에 replay 로직 분산 | 🔜 2차 계획 |

---

## 3. 수정 계획

### 3-1. Shim 파일 정리 — ✅ 1차 완료

| 파일 | 현재 상태 | 조치 | 상태 |
|------|----------|------|------|
| `circuit_breaker_service.py` | re-export shim | DeprecationWarning 추가, v3.0.0 삭제 예약 | ✅ 완료 |
| `dlq_service.py` | re-export shim | DeprecationWarning 추가, v3.0.0 삭제 예약 | ✅ 완료 |
| `error_budget_service.py` | re-export shim | DeprecationWarning 추가, v3.0.0 삭제 예약 | ✅ 완료 |
| `factory.py` | ~~deprecated shim~~ | **파일 존재하지 않음** — 조치 불필요 | ✅ 확인 |
| `postmortem_store.py` | 866줄 실구현 | 구현을 `postmortem/store.py`로 이전, shim으로 전환 | ✅ 완료 |

### 3-2. Legacy 파일 정리 — ✅ 1차 완료

| 파일 | 조치 | 상태 |
|------|------|------|
| `dlq_models.py` | `dlq/models.py`로 이전, shim 전환. dlq 패키지 내부 import도 `.models`로 변경 | ✅ 완료 |

### 3-3. 플랫 파일의 패키지 전환 기준

다음 조건 중 하나 이상 충족 시 플랫 파일을 패키지로 전환:
1. 파일이 500줄 이상
2. 내부에 3개 이상의 독립 클래스 정의
3. 설정/모델/서비스 로직이 한 파일에 혼재

### 3-4. 500줄+ 플랫 파일 패키지 전환 대상 (2차 계획)

코드에서 확인된 500줄 이상 플랫 파일 16개:

| 우선순위 | 파일 | 줄 수 | 비고 |
|---------|------|-------|------|
| **1** | `event_bus.py` | 1,875 | + event_bus_redis.py (377줄) → `event_bus/` 패키지 통합 |
| **2** | `idempotency_service.py` | 1,160 | 단독 대형 파일 |
| **3** | `unified_notification.py` | 945 | 단독 대형 파일 |
| **4** | `replay_service.py` | 940 | `dlq/replay_operations.py`와 기능 분산 정리 필요 |
| **5** | `control_api_service.py` | 871 | 단독 대형 파일 |
| **6** | `governance_checks.py` | 863 | governance 4파일 → `governance/` 패키지 통합 |
| **6** | `governance.py` | 547 | 위와 동일 도메인 |
| **6** | `governance_api_service.py` | 526 | 위와 동일 도메인 |
| **6** | `governance_service.py` | 435 | 위와 동일 도메인 (합계 2,371줄) |
| **7** | `stress_test_service.py` | 842 | 단독 대형 파일 |
| **8** | `retry_handler.py` | 827 | 단독 대형 파일 |
| **9** | `backoff_calculator.py` | 822 | 단독 대형 파일 |
| **10** | `execution_services.py` | 730 | 단독 대형 파일 |
| **11** | `precomputed_cache.py` | 690 | 단독 대형 파일 |
| **12** | `rate_limit_coordinator.py` | 576 | 단독 대형 파일 |
| **13** | `dashboard_service.py` | 538 | 단독 대형 파일 |
| **14** | `config_history.py` | 512 | 단독 대형 파일 |

### 3-5. 검증 항목 — ✅ 1차 완료

- [x] `factory.py` 실제 존재 여부 확인 → **존재하지 않음** (factory/ 디렉토리만 존재)
- [x] 전체 코드베이스에서 `from selfhealing.services.circuit_breaker_service import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] 전체 코드베이스에서 `from selfhealing.services.dlq_service import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] 전체 코드베이스에서 `from selfhealing.services.error_budget_service import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] 전체 코드베이스에서 `from selfhealing.services.postmortem_store import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] 전체 코드베이스에서 `from selfhealing.services.dlq_models import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] `postmortem_store.py` → `postmortem/store.py` 이전 후 public API 변경 없음 확인 → **테스트 90건 전체 통과**
- [x] `dlq_models.py` → `dlq/models.py` 이전 후 호환성 확인 → **테스트 90건 전체 통과**

---

## 4. 실행 기록

### 4-1. 1차 정리 (2026-02-09)

#### 변경 파일 목록

**신규 생성:**
- `services/dlq/models.py` — dlq_models.py 구현 이전 (정식 위치)
- `services/postmortem/store.py` — postmortem_store.py 구현 이전 (정식 위치)

**수정 (deprecation warning 추가):**
- `services/circuit_breaker_service.py` — DeprecationWarning 추가
- `services/dlq_service.py` — DeprecationWarning 추가
- `services/error_budget_service.py` — DeprecationWarning 추가

**수정 (shim 전환):**
- `services/dlq_models.py` — 202줄 실구현 → shim (re-export from dlq.models)
- `services/postmortem_store.py` — 866줄 실구현 → shim (re-export from postmortem.store)

**수정 (내부 import 경로 변경):**
- `services/dlq/__init__.py` — `dlq_models` → `.models`
- `services/dlq/replay_operations.py` — `dlq_models` → `dlq.models` (3건)
- `services/dlq/store_operations.py` — `dlq_models` → `dlq.models` (1건)
- `services/postmortem/__init__.py` — store 모듈 export 추가
- `services/postmortem/revision.py` — `postmortem_store` → `postmortem.store`
- `services/postmortem/incident_group.py` — `postmortem_store` → `postmortem.store`

#### 테스트 결과

```
90 passed in 0.90s
```

대상: `tests/unit/dlq/`, `tests/unit/replay/test_dlq_service.py`,
`tests/services/test_postmortem_store.py`, `tests/services/test_error_budget_service.py`
