# 206. services/ 디렉토리 구조 통일 계획

> **상태**: 📋 계획
> **목적**: `services/` 하위의 플랫 파일(단일 `.py`)과 패키지(디렉토리) 공존 문제를 정리한다.

---

## 1. 현황

### 1-1. services/ 최상위 구조

`services/` 디렉토리에는 **패키지(디렉토리)**와 **플랫 .py 파일**이 혼재:

#### 패키지 (디렉토리) — 20개

```
services/
├── audit/
├── auto_tuning/
├── blast_radius/
├── canary/
├── chaos/
├── circuit_breaker/
├── cleanup_service.py         ← 단독 파일 (패키지 아님)
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
├── adaptive_replay.py
├── audit_helpers.py
├── backoff_calculator.py
├── chaos_context.py
├── circuit_breaker_service.py  ← backward compat shim
├── cleanup_service.py
├── config_history.py
├── control_api_service.py
├── dashboard_service.py
├── dlq_models.py               ← legacy flat model
├── dlq_service.py              ← backward compat shim
├── error_budget_service.py     ← backward compat shim
├── event_bus.py
├── event_bus_redis.py
├── execution_services.py
├── factory.py                  ← backward compat shim (deprecated)
├── forensic_audit_bridge.py
├── governance.py
├── governance_api_service.py
├── governance_checks.py
├── governance_service.py
├── healing_events_store.py
├── health_check.py
├── http_client.py
├── idempotency_service.py
├── metric_sync_service.py
├── pending_config.py
├── postmortem_store.py         ← backward compat shim
├── precomputed_cache.py
├── rate_limit_coordinator.py
├── replay_service.py
├── retry_handler.py
├── stress_test_service.py
├── system_control.py
├── unified_notification.py
├── xtest_cleanup_service.py
└── xtest_session_manager.py
```

### 1-2. Backward Compatibility Shim 파일 (5건)

이미 패키지로 분리되었지만, 이전 import 경로 호환을 위해 남겨진 파일:

#### (1) `services/circuit_breaker_service.py` → `services/circuit_breaker/`

```python
# services/circuit_breaker_service.py L1-30
"""
Circuit Breaker Service - Compatibility Module

This module re-exports all symbols from the circuit_breaker package
for backward compatibility with existing imports.
"""
# → services/circuit_breaker/ 패키지로 재export
```

#### (2) `services/dlq_service.py` → `services/dlq/`

```python
# services/dlq_service.py L1-17
"""
Dead Letter Queue (DLQ) Service - Backward Compatibility Wrapper

이 모듈은 하위 호환성을 위한 re-export wrapper입니다.
실제 구현은 selfhealing.services.dlq 패키지에 있습니다.
"""
from selfhealing.services.dlq import (
    DLQService, DLQConfig, ...
)
```

#### (3) `services/factory.py` → `services/factory/`

```python
# services/factory.py L1-35
"""
Service Factory for Self-Healing Components.

.. deprecated:: 2.0.0
    This module is kept for backward compatibility only.
    Import directly from selfhealing.services.factory package instead.
    Will be removed in version 3.0.0.
"""
import warnings
warnings.warn(
    "Importing from 'selfhealing.services.factory' (single module) is deprecated. "
    "Import from 'selfhealing.services.factory' package directly. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)
```

#### (4) `services/error_budget_service.py` → `services/error_budget/`

```python
# services/error_budget_service.py L1-25
"""
Error Budget Service (Re-export Module)

SRE Error Budget 계산기 및 배포 정책 어드바이저.

⚠️ BACKWARD COMPATIBILITY:
이 파일은 기존 import 경로와의 호환성을 위해 유지됩니다.
"""
```

#### (5) `services/postmortem_store.py`

876줄의 실제 구현을 포함하면서도 backward compat 역할을 겸함. `services/postmortem/` 패키지가 별도 존재.

### 1-3. Legacy 플랫 모델 파일

```python
# services/dlq_models.py L1-6
"""
DLQ Models and Data Classes

Data classes and configuration for DLQ operations.
"""
```

`services/dlq/` 패키지가 존재함에도 `services/dlq_models.py`가 별도로 남아있음.

---

## 2. 문제점

| 항목 | 설명 |
|------|------|
| **import 경로 혼란** | `from services.dlq_service import DLQService` vs `from services.dlq import DLQService` — 어느 것이 정식인지 불분명 |
| **Shim 파일 유지 비용** | 5개 shim 파일이 패키지와 동일 이름으로 존재하여 모듈 해석 혼란 가능 |
| **factory.py vs factory/의 모호성** | Python은 `factory.py`와 `factory/` 디렉토리가 같은 레벨에 있을 때 `factory/__init__.py`를 우선 로드하므로 `factory.py`의 deprecation warning은 **실행되지 않을 수 있음** |
| **postmortem_store.py 이중 역할** | 876줄의 실제 구현 + backward compat을 겸하여 역할이 불명확 |
| **dlq_models.py 고아 파일** | 패키지 내 모델과 플랫 파일 모델이 이중 존재 |

---

## 3. 수정 계획

### 3-1. Shim 파일 정리 방향

| 파일 | 현재 상태 | 조치 |
|------|----------|------|
| `circuit_breaker_service.py` | re-export shim | `__init__.py`에 deprecation re-export 이전 후 삭제 예약 (v3.0.0) |
| `dlq_service.py` | re-export shim | 동일 |
| `error_budget_service.py` | re-export shim | 동일 |
| `factory.py` | deprecated shim (이미 warning) | **즉시 삭제 가능** — `factory/`가 우선 로드되므로 이 파일 도달 불가 |
| `postmortem_store.py` | 876줄 실구현 | 구현을 `postmortem/` 패키지로 이전 후 shim으로 전환 |

### 3-2. Legacy 파일 정리

| 파일 | 조치 |
|------|------|
| `dlq_models.py` | `services/dlq/models.py`와 통합 후 삭제. re-export alias 제공 |

### 3-3. 플랫 파일의 패키지 전환 기준

다음 조건 중 하나 이상 충족 시 플랫 파일을 패키지로 전환:
1. 파일이 500줄 이상
2. 내부에 3개 이상의 독립 클래스 정의
3. 설정/모델/서비스 로직이 한 파일에 혼재

### 3-4. 검증 항목

- [ ] `factory.py` 삭제 전 실제 도달 여부 테스트 (Python 모듈 해석 우선순위 확인)
- [ ] 전체 코드베이스에서 `from selfhealing.services.circuit_breaker_service import` 참조 검색
- [ ] 전체 코드베이스에서 `from selfhealing.services.dlq_service import` 참조 검색
- [ ] `postmortem_store.py` 구현 이전 시 public API 변경 없음 확인
- [ ] `dlq_models.py` 참조 검색 및 호환성 alias 테스트
