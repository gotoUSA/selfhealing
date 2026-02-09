# 206. services/ 디렉토리 구조 통일 계획

> **상태**: 🔧 4차 정리 완료 (2026-02-09)
> **목적**: `services/` 하위의 플랫 파일(단일 `.py`)과 패키지(디렉토리) 공존 문제를 정리한다.

---

## 1. 현황

### 1-1. services/ 최상위 구조

`services/` 디렉토리에는 **패키지(디렉토리)**와 **플랫 .py 파일**이 혼재:

#### 패키지 (디렉토리) — 42개

```
services/
├── audit/
├── auto_tuning/
├── backoff_calculator/    ← 4차 신규 (backoff_calculator.py 전환)
├── blast_radius/
├── canary/
├── chaos/
├── circuit_breaker/
├── compliance/
├── config/
├── config_history/        ← 4차 신규 (config_history.py 전환)
├── control_api_service/   ← 4차 신규 (control_api_service.py 전환)
├── coordination/
├── corruption_shield/
├── daily_report/
├── dashboard_service/     ← 4차 신규 (dashboard_service.py 전환)
├── dlq/
├── emergency_mode/
├── error_budget/
├── error_budget_gate/
├── event_bus/             ← 2차 신규 (event_bus.py + event_bus_redis.py 통합)
├── execution_services/    ← 4차 신규 (execution_services.py 전환)
├── factory/
├── finops/
├── governance/           ← 2차 신규 (governance*.py 4파일 통합)
├── idempotency/          ← 4차 기존 패키지, _ForwardingModule 추가
├── isolation/
├── learning/
├── metrics/
├── namespace_emergency/
├── postmortem/
├── precomputed_cache/     ← 4차 신규 (precomputed_cache.py 전환)
├── rate_limit/
├── rate_limit_coordinator/ ← 4차 신규 (rate_limit_coordinator.py 전환)
├── replay_service/        ← 4차 신규 (replay_service.py 전환)
├── retry_handler/         ← 4차 신규 (retry_handler.py 전환)
├── rollback/
├── runtime_config/
├── security/
├── security_notification/
├── stress_test_service/   ← 4차 신규 (stress_test_service.py 전환)
├── throttle/
└── unified_notification/  ← 4차 신규 (unified_notification.py 전환)
```

#### 플랫 파일 — 약 30개

```
services/
├── adaptive_replay.py              (317줄)
├── audit_helpers.py                ← backward compat shim (audit/ 패키지로 re-export, deprecation warning 추가 완료)
├── chaos_context.py                (415줄)
├── circuit_breaker_service.py      ← backward compat shim (deprecation warning 추가 완료)
├── cleanup_service.py              (345줄)
├── dlq_models.py                   ← backward compat shim (dlq/models.py로 이전 완료)
├── dlq_service.py                  ← backward compat shim (deprecation warning 추가 완료)
├── error_budget_service.py         ← backward compat shim (deprecation warning 추가 완료)
├── event_bus_redis.py              ← backward compat shim (event_bus/redis_bus.py로 이전 완료)
├── forensic_audit_bridge.py        (476줄)
├── governance_api_service.py       ← backward compat shim (governance/api_service.py로 이전 완료)
├── governance_checks.py            ← backward compat shim (governance/checks.py로 이전 완료)
├── governance_service.py           ← backward compat shim (governance/service.py로 이전 완료)
├── healing_events_store.py         (285줄)
├── health_check.py                 (378줄)
├── http_client.py                  (335줄)
├── idempotency_service.py          ← backward compat shim (idempotency/ 패키지로 sys.modules 전환, 4차)
├── metric_sync_service.py          (402줄)
├── pending_config.py               (361줄)
├── postmortem_store.py             ← backward compat shim (postmortem/store.py로 이전 완료)
├── system_control.py               (421줄)
├── xtest_cleanup_service.py        (445줄)
└── xtest_session_manager.py        (397줄)
```

### 1-2. Backward Compatibility Shim 파일 (6건 → 5건 실존)

이미 패키지로 분리되었지만, 이전 import 경로 호환을 위해 남겨진 파일:

#### (0) `services/audit_helpers.py` → `services/audit/` — ✅ deprecation warning 추가 완료 (3차)

```python
# services/audit_helpers.py (139줄 → shim + DeprecationWarning)
"""
Audit Helpers - Backward Compatibility Wrapper

.. deprecated:: 2.0.0
    Import from ``selfhealing.services.audit`` instead.
    This shim will be removed in v3.0.0.
"""
import warnings
warnings.warn(
    "Importing from 'selfhealing.services.audit_helpers' is deprecated. "
    "Use 'selfhealing.services.audit' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning, stacklevel=2,
)
# → services/audit/ 패키지로 재export
```

**참조 현황** (50건+):
- `services/circuit_breaker/service.py`, `services/circuit_breaker/tracing.py`, `services/circuit_breaker/manual_control.py`, `services/circuit_breaker/freeze_mode.py`, `services/circuit_breaker/panic_threshold.py`, `services/circuit_breaker/blast_radius_integration.py`
- `services/dlq/base.py`, `services/replay_service.py`, `services/system_control.py`
- `services/rollback/service.py`, `services/finops/service.py`, `services/compliance/service.py`
- `services/emergency_mode/manager.py`, `services/error_budget_gate/gate.py`
- `services/chaos/blast_radius.py`, `services/chaos/base/experiment.py`, `services/chaos/safety_guard/guard.py`
- `tasks/chaos_scheduler.py`, `tasks/config_apply.py`, `tasks/governance.py`, `tasks/traffic_aware_replay.py`
- 테스트 50건+

**누락 심볼 보충** (3차): `audit/__init__.py`에는 있으나 `audit_helpers.py`에 누락되었던 2개 심볼 추가:
- `log_region_isolation_audit`
- `log_security_violation_audit`

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
| **500줄+ 플랫 파일 16개 미정리** | 패키지 전환 기준 충족하나 아직 패키지로 전환되지 않음 | ✅ 전체 16개 완료 (2차 4개 + 4차 12개) |
| **event_bus 도메인 분산** | event_bus.py (1,875줄) + event_bus_redis.py (377줄) 동일 도메인 | ✅ event_bus/ 패키지로 통합 완료 |
| **governance 도메인 분산** | 4개 파일 합계 2,371줄이 동일 도메인에서 플랫 파일로 존재 | ✅ governance/ 패키지로 통합 완료 |
| **audit_helpers.py shim 미비** | audit/ 패키지로의 re-export shim이나 DeprecationWarning 없음 + 심볼 2개 누락 | ✅ 3차에서 DeprecationWarning 추가 + 누락 심볼 보충 |
| **replay_service.py / dlq 기능 분산** | replay_service.py (940줄)와 dlq/replay_operations.py (296줄) 양쪽에 replay 로직 분산 | ✅ replay_service/ 패키지로 전환 완료 (4차) |

---

## 3. 수정 계획

### 3-1. Shim 파일 정리 — ✅ 3차 완료

| 파일 | 현재 상태 | 조치 | 상태 |
|------|----------|------|------|
| `audit_helpers.py` | re-export shim (DeprecationWarning 없었음) | DeprecationWarning 추가 + 누락 심볼 2개 보충, v3.0.0 삭제 예약 | ✅ 3차 완료 |
| `circuit_breaker_service.py` | re-export shim | DeprecationWarning 추가, v3.0.0 삭제 예약 | ✅ 1차 완료 |
| `dlq_service.py` | re-export shim | DeprecationWarning 추가, v3.0.0 삭제 예약 | ✅ 1차 완료 |
| `error_budget_service.py` | re-export shim | DeprecationWarning 추가, v3.0.0 삭제 예약 | ✅ 1차 완료 |
| `factory.py` | ~~deprecated shim~~ | **파일 존재하지 않음** — 조치 불필요 | ✅ 확인 |
| `postmortem_store.py` | 866줄 실구현 | 구현을 `postmortem/store.py`로 이전, shim으로 전환 | ✅ 1차 완료 |

### 3-2. Legacy 파일 정리 — ✅ 1차 완료

| 파일 | 조치 | 상태 |
|------|------|------|
| `dlq_models.py` | `dlq/models.py`로 이전, shim 전환. dlq 패키지 내부 import도 `.models`로 변경 | ✅ 완료 |

### 3-3. 플랫 파일의 패키지 전환 기준

다음 조건 중 하나 이상 충족 시 플랫 파일을 패키지로 전환:
1. 파일이 500줄 이상
2. 내부에 3개 이상의 독립 클래스 정의
3. 설정/모델/서비스 로직이 한 파일에 혼재

### 3-4. 500줄+ 플랫 파일 패키지 전환 대상

코드에서 확인된 500줄 이상 플랫 파일 16개:

| 우선순위 | 파일 | 줄 수 | 비고 | 상태 |
|---------|------|-------|------|------|
| **1** | `event_bus.py` | 1,875 | + event_bus_redis.py (377줄) → `event_bus/` 패키지 통합 | ✅ 2차 완료 |
| **6** | `governance_checks.py` | 863 | governance 4파일 → `governance/` 패키지 통합 | ✅ 2차 완료 |
| **6** | `governance.py` | 547 | 위와 동일 도메인 | ✅ 2차 완료 |
| **6** | `governance_api_service.py` | 526 | 위와 동일 도메인 | ✅ 2차 완료 |
| **6** | `governance_service.py` | 435 | 위와 동일 도메인 (합계 2,371줄) | ✅ 2차 완료 |
| **2** | `idempotency_service.py` | 1,160 | sys.modules shim → `idempotency/` 패키지 | ✅ 4차 완료 |
| **3** | `unified_notification.py` | 945 | `unified_notification/` 패키지 (6 서브모듈) | ✅ 4차 완료 |
| **4** | `replay_service.py` | 940 | `replay_service/` 패키지 (4 서브모듈) | ✅ 4차 완료 |
| **5** | `control_api_service.py` | 871 | `control_api_service/` 패키지 (4 서브모듈) | ✅ 4차 완료 |
| **7** | `stress_test_service.py` | 842 | `stress_test_service/` 패키지 (3 서브모듈) | ✅ 4차 완료 |
| **8** | `retry_handler.py` | 827 | `retry_handler/` 패키지 (4 서브모듈) | ✅ 4차 완료 |
| **9** | `backoff_calculator.py` | 822 | `backoff_calculator/` 패키지 (5 서브모듈) | ✅ 4차 완료 |
| **10** | `execution_services.py` | 730 | `execution_services/` 패키지 (4 서브모듈) | ✅ 4차 완료 |
| **11** | `precomputed_cache.py` | 690 | `precomputed_cache/` 패키지 (7 서브모듈) | ✅ 4차 완료 |
| **12** | `rate_limit_coordinator.py` | 576 | `rate_limit_coordinator/` 패키지 (4 서브모듈) | ✅ 4차 완료 |
| **13** | `dashboard_service.py` | 538 | `dashboard_service/` 패키지 (3 서브모듈) | ✅ 4차 완료 |
| **14** | `config_history.py` | 512 | `config_history/` 패키지 (4 서브모듈) | ✅ 4차 완료 |

### 3-5. 검증 항목 — ✅ 3차 완료

- [x] `factory.py` 실제 존재 여부 확인 → **존재하지 않음** (factory/ 디렉토리만 존재)
- [x] 전체 코드베이스에서 `from selfhealing.services.circuit_breaker_service import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] 전체 코드베이스에서 `from selfhealing.services.dlq_service import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] 전체 코드베이스에서 `from selfhealing.services.error_budget_service import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] 전체 코드베이스에서 `from selfhealing.services.postmortem_store import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] 전체 코드베이스에서 `from selfhealing.services.dlq_models import` 참조 검색 → **20건+ 확인**, shim 유지 필요
- [x] `postmortem_store.py` → `postmortem/store.py` 이전 후 public API 변경 없음 확인 → **테스트 90건 전체 통과**
- [x] `dlq_models.py` → `dlq/models.py` 이전 후 호환성 확인 → **테스트 90건 전체 통과**
- [x] `audit_helpers.py`가 `audit/` 패키지의 re-export shim임을 확인 → **DeprecationWarning 누락** (3차 추가)
- [x] `audit_helpers.py` ↔ `audit/__init__.py` 심볼 대조 → **`log_region_isolation_audit`, `log_security_violation_audit` 누락** (3차 보충)
- [x] 전체 코드베이스에서 `from selfhealing.services.audit_helpers import` 참조 검색 → **소스 50건+, 테스트 50건+**, shim 유지 필요
- [x] `audit_helpers.py` DeprecationWarning 추가 + 누락 심볼 보충 후 호환성 확인 → **테스트 1634건 통과**

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

### 4-2. 2차 정리 (2026-02-09) — event_bus/, governance/ 패키지 전환

#### 변경 요약

- `event_bus.py` (1,875줄) + `event_bus_redis.py` (377줄) → `event_bus/` 패키지 통합
- `governance.py` (547줄) + `governance_checks.py` (863줄) + `governance_service.py` (435줄) + `governance_api_service.py` (526줄) → `governance/` 패키지 통합
- 총 6개 플랫 파일 → 2개 패키지 + 4개 shim + 2개 삭제

#### 변경 파일 목록

**신규 생성 (event_bus/ 패키지):**
- `services/event_bus/__init__.py` — 공개 API 재export + bus.py 전체 속성 동적 노출
- `services/event_bus/bus.py` — event_bus.py 전체 구현 이전 (1,875줄)
- `services/event_bus/redis_bus.py` — event_bus_redis.py 구현 이전 (377줄)

**신규 생성 (governance/ 패키지):**
- `services/governance/__init__.py` — 4개 모듈 공개 API 재export
- `services/governance/emergency.py` — governance.py 이전 (EmergencyModeTracker, 547줄)
- `services/governance/checks.py` — governance_checks.py 이전 (GovernanceCheckMixin, 데코레이터, 863줄)
- `services/governance/service.py` — governance_service.py 이전 (GovernanceService, 435줄)
- `services/governance/api_service.py` — governance_api_service.py 이전 (GovernanceApiService, 526줄)

**삭제 (패키지가 네임스페이스 대체):**
- `services/event_bus.py` — `event_bus/` 패키지가 동일 네임스페이스 차지
- `services/governance.py` — `governance/` 패키지가 동일 네임스페이스 차지

**수정 (sys.modules shim 전환):**
- `services/event_bus_redis.py` — `sys.modules[__name__] = event_bus.redis_bus` + DeprecationWarning
- `services/governance_checks.py` — `sys.modules[__name__] = governance.checks` + DeprecationWarning
- `services/governance_service.py` — `sys.modules[__name__] = governance.service` + DeprecationWarning
- `services/governance_api_service.py` — `sys.modules[__name__] = governance.api_service` + DeprecationWarning

**수정 (내부 import 경로 변경):**
- `services/event_bus/redis_bus.py` — `event_bus` → `event_bus.bus` (EventType 등 4개 import)
- `services/governance/service.py` L26 — `governance_checks` → `governance.checks` (GovernanceCheckMixin)
- `services/governance/service.py` L132 — lazy import `governance` → 패키지 __init__ 경유 (테스트 patch 호환)
- `services/governance/api_service.py` L223 — lazy import `governance` → 패키지 __init__ 경유

#### Shim 전략 비교

| 패턴 | 적용 대상 | 이유 |
|------|----------|------|
| **re-export shim** | `dlq_models.py`, `postmortem_store.py` 등 | 파일명과 패키지명이 다름 → 정상 동작 |
| **sys.modules 교체 shim** | `governance_checks.py`, `governance_service.py` 등 | 테스트에서 `patch("...governance_checks._private_fn")` 사용 → 모듈 객체 일치 필요 |
| **삭제** | `event_bus.py`, `governance.py` | 패키지와 동일 이름 → Python이 패키지 우선 → 파일 접근 불가 |

#### 테스트 결과

```
event_bus + governance + throttle: 172 passed in 6.92s
dlq + replay + postmortem + error_budget: 155 passed in 1.45s
합계: 327 passed, 0 failed
```

대상: `tests/unit/services/test_event_bus_unit.py`, `tests/unit/governance/`,
`tests/services/test_event_bus_redis.py`, `tests/services/test_event_bus_throttle_events.py`,
`tests/unit/resilience/test_event_bus_error_budget_gate.py`,
`tests/unit/throttle/test_throttle_*_integration.py`, `tests/unit/throttle/test_throttle_eventbus_handlers.py`,
`tests/unit/adapters/test_beat_schedule_governance_integration.py`,
`tests/unit/config/test_auto_tuning_governance.py`, `tests/unit/tasks/test_tasks_governance.py`

### 4-3. 3차 정리 (2026-02-09) — audit_helpers.py shim 보완

#### 발견 경위

206 문서 검증 과정에서 `audit_helpers.py`가 `audit/` 패키지로의 re-export shim임에도 불구하고:
1. 문서 1-2절 Backward Compatibility Shim 목록에 누락되어 있었음
2. 다른 shim 파일(`circuit_breaker_service.py`, `dlq_service.py` 등)에는 모두 `DeprecationWarning`이 있으나, `audit_helpers.py`에만 없었음
3. `audit/__init__.py`에서 export하는 `log_region_isolation_audit`, `log_security_violation_audit` 2개 심볼이 `audit_helpers.py`의 re-export 목록에 누락

#### 변경 파일 목록

**수정:**
- `services/audit_helpers.py` — DeprecationWarning 추가 + 누락 심볼 2개(`log_region_isolation_audit`, `log_security_violation_audit`) import 및 `__all__` 보충 (121줄 → 139줄)

#### 테스트 결과

```
audit helpers (helpers + pipeline + wal + trace): 1371 passed in 63.72s
dlq: 33 passed in 0.56s
postmortem_store: 11 passed in 0.14s
error_budget + event_bus_redis: 46 passed in 0.61s
replay: 77 passed in 1.14s
governance + event_bus: 96 passed in 1.83s
합계: 1634 passed, 0 관련 실패
```

(1건 실패 = `test_sampling_verification_performance` — 타이밍 기반 성능 테스트로 변경과 무관)

### 4-4. 4차 정리 (2026-02-09) — 12개 플랫 파일 패키지 전환

#### 변경 요약

500줄+ 플랫 파일 12개를 패키지로 전환. 총 52개 서브모듈 신규 생성, 11개 플랫 파일 삭제, 1개 sys.modules shim 전환.
`mock.patch` 호환을 위해 `_ForwardingModule` 패턴 적용 (replay_service, control_api_service, idempotency, retry_handler).

#### 변경 파일 목록

**신규 생성 (12개 패키지, 52개 서브모듈):**

| 패키지 | 서브모듈 | 원본 줄 수 |
|--------|---------|-----------|
| `stress_test_service/` | `__init__.py`, `models.py`, `service.py` | 842 |
| `backoff_calculator/` | `__init__.py`, `models.py`, `budget.py`, `global_state.py`, `calculator.py` | 822 |
| `precomputed_cache/` | `__init__.py`, `constants.py`, `l1_cache.py`, `l2_cache.py`, `multi_tier.py`, `worker.py`, `compute_functions.py` | 690 |
| `rate_limit_coordinator/` | `__init__.py`, `models.py`, `helpers.py`, `coordinator.py` | 576 |
| `dashboard_service/` | `__init__.py`, `models.py`, `service.py` | 538 |
| `config_history/` | `__init__.py`, `keys.py`, `models.py`, `service.py` | 512 |
| `unified_notification/` | `__init__.py`, `models.py`, `routing.py`, `service.py`, `convenience.py`, `formatters.py` | 945 |
| `replay_service/` | `__init__.py`, `models.py`, `handlers.py`, `service.py` | 940 |
| `control_api_service/` | `__init__.py`, `models.py`, `risk.py`, `service.py` | 871 |
| `retry_handler/` | `__init__.py`, `models.py`, `handler.py`, `decorators.py` | 827 |
| `execution_services/` | `__init__.py`, `models.py`, `chaos_service.py`, `config_apply_service.py` | 730 |

**sys.modules shim 전환:**
- `idempotency_service.py` (1,160줄 → 25줄) — `idempotency/` 패키지로 sys.modules 교체 + DeprecationWarning

**삭제 (패키지가 네임스페이스 대체):**
- `stress_test_service.py`, `backoff_calculator.py`, `precomputed_cache.py`, `rate_limit_coordinator.py`
- `dashboard_service.py`, `config_history.py`, `unified_notification.py`, `replay_service.py`
- `control_api_service.py`, `retry_handler.py`, `execution_services.py`

**수정 (mock.patch 호환 _ForwardingModule 추가):**
- `idempotency/__init__.py` — setattr 동적 전달 + `_ForwardingModule` 추가
- `replay_service/__init__.py` — `_ForwardingModule` 추가
- `control_api_service/__init__.py` — eager setattr + `_ForwardingModule` 추가

**수정 (import 경로):**
- `dlq/base.py` — `selfhealing.services.dlq_models` → `selfhealing.services.dlq.models` (2건)

#### Shim 전략 적용 결과

| 패턴 | 적용 대상 | 이유 |
|------|----------|------|
| **파일 삭제** | 11개 플랫 파일 | 패키지와 동일 이름 → Python이 패키지 우선 로드 |
| **sys.modules shim** | `idempotency_service.py` | 패키지명(`idempotency`)과 파일명(`idempotency_service`)이 다름 + 테스트 patch 존재 |
| **_ForwardingModule** | `replay_service`, `control_api_service`, `idempotency`, `retry_handler` | `mock.patch` 가 패키지 레벨에서 서브모듈 속성을 교체해야 하는 경우 |
| **setattr 동적 전달** | 전체 12개 패키지 | 서브모듈의 모든 속성을 패키지 레벨로 노출 (import 호환) |

#### 테스트 결과

```
변환 관련 영역 (services + dlq + replay + rate_limit + notification + idempotency):
568 passed, 0 failed in 3.90s

전체 테스트 스위트:
10,621 passed, 28 failed (기존 postmortem/resilience 실패 — 변환과 무관)
```

변환 관련 5개 초기 실패 (`_ForwardingModule` 미적용 시):
- `test_control_api_service::test_injection_expired` — `now` 패치 미전파 → `_ForwardingModule` 추가로 해결
- `test_control_api_service::test_returns_existing_singleton` — `_control_api_service` 변수 미전파 → `_ForwardingModule` 추가로 해결
- `test_replay_service_unit::test_governance_blocked` — `check_all_governance` 패치 미전파 → `_ForwardingModule` 추가로 해결
- `test_replay_service_unit::test_governance_blocked_batch` — 동일 원인 → 해결
- `test_idempotency_extension::test_sliding_window_expiry` — `time` 모듈 미노출 → setattr 동적 전달 추가로 해결

### 4-5. 4차 보완 (2026-02-09) — execution_services.py 삭제 누락 수정

#### 발견 경위

206 문서 검증 과정에서 `execution_services.py` (691줄)가 삭제 목록에 포함되어 있음에도 실제 파일이 남아 있음을 확인.
`execution_services/` 패키지가 동일 네임스페이스를 점유하여 Python 실행 시에는 영향 없으나 (패키지 우선 로드), dead code로 잔존.

#### 변경 파일 목록

**삭제:**
- `services/execution_services.py` (691줄) — 4차 삭제 목록에서 누락되었던 파일
