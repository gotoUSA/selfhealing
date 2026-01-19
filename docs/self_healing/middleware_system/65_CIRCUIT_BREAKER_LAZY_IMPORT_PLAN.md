# 65. services/circuit_breaker/__init__.py Lazy Import 계획서

| 항목 | 내용 |
|-----|------|
| 버전 | 1.1 |
| 작성일 | 2026-01-19 |
| 완료일 | 2026-01-19 |
| 상태 | ✅ 완료 |
| 우선순위 | 🔴 즉시 |
| 예상 효과 | circuit_breaker 패키지 로딩 시간 ~95% 감소 |

---

## 1. 현황 분석

### 1.1 문제점

`selfhealing/services/circuit_breaker/__init__.py`가 **376줄, 126개 심볼**을 즉시 import.

**코드 근거:**
```python
# circuit_breaker/__init__.py:42-232
from .config import (CircuitBreakerConfig, CircuitBreakerResult, CircuitState, FallbackResult)
from .rate_limit_tracker import (RateLimitTracker, get_rate_limit_tracker)
from .protection import ProtectionMixin
from .manual_control import ManualControlMixin
from .service import CircuitBreakerService
from .convenience import (get_circuit_breaker_service, should_allow_request, ...)
from .models import (ServiceConfig, SheddingLevel, LoadSheddingPolicy, ...)
from .adaptive_threshold import (AdaptiveThresholdManager, AdjustedThreshold, ...)
from .freeze_mode import (FreezeModeManager, FreezeReason, ...)
from .panic_threshold import (PanicThresholdMonitor, PanicThresholdResult, ...)
from .tracing import (TracingConfig, TriggeringRequestInfo, ...)
from .service_config import (ServiceConfigManager, get_service_config_manager, ...)
from .blast_radius_integration import (BlastRadiusLevel, BlastRadiusAssessment, ...)
from .canary_recovery import (CanaryState, CanaryStageMetrics, ...)
from .stale_cache_integration import (CanaryWithStaleCacheConfig, ...)
from .recovery_strategy import (RecoveryStrategySelection, ...)
from .load_shedding import (SheddingState, SheddingDecision, ...)
```

### 1.2 영향받는 서브모듈 (15개)

| 서브모듈 | import 수 | 예상 크기 |
|---------|----------|----------|
| config | 4 | ~125줄 |
| rate_limit_tracker | 2 | ~95줄 |
| protection | 1 | ~250줄 |
| manual_control | 1 | ~350줄 |
| service | 1 | ~285줄 |
| convenience | 7 | ~135줄 |
| models | 11 | ~500줄 |
| adaptive_threshold | 5 | ~300줄 |
| freeze_mode | 5 | ~200줄 |
| panic_threshold | 5 | ~250줄 |
| tracing | 8 | ~400줄 |
| service_config | 9 | ~300줄 |
| blast_radius_integration | 11 | ~400줄 |
| canary_recovery | 16 | ~500줄 |
| stale_cache_integration | 12 | ~400줄 |
| recovery_strategy | 12 | ~350줄 |
| load_shedding | 17 | 233줄 (자체 __init__.py) |

**총합: 126개 심볼, ~4,500줄 즉시 로드**

### 1.3 외부 사용 패턴 (코드 근거)

```bash
# 실제 사용처 분석
$ grep -rn "from selfhealing.services.circuit_breaker import" packages/selfhealing-python/src/

# 결과 (8곳):
# 1. chaos/experiments/circuit_breaker.py:72 - 함수 내부 lazy import ✅
# 2. chaos/experiments/cascade.py:244 - 함수 내부 lazy import ✅
# 3. chaos/experiments/cascade.py:302 - 함수 내부 lazy import ✅
# 4. chaos/experiments/rate_limit.py:54 - 함수 내부 lazy import ✅
# 5. chaos/base/experiment.py:614 - 함수 내부 lazy import ✅
# 6. circuit_breaker/panic_threshold.py:120 - 내부 순환 방지 ✅
# 7. circuit_breaker/__init__.py:31 - docstring 예시
```

**핵심 발견:**
- 내부 사용처는 이미 **함수 내부 lazy import** 패턴 사용
- 외부에서 패키지 전체를 직접 import하는 경우는 **테스트 코드**뿐
- `__init__.py`의 대량 직접 import는 **낭비**

---

## 2. 구현 전략

### 2.1 Lazy Import 대상 분류

| 카테고리 | 심볼 수 | 전략 |
|---------|--------|------|
| 핵심 API (자주 사용) | 7개 | 직접 import 유지 |
| 확장 기능 (필요 시 사용) | 119개 | Lazy import |

### 2.2 직접 import 유지 대상 (7개)

```python
# 가장 자주 사용되는 핵심 API만 유지
from .config import CircuitBreakerConfig, CircuitBreakerResult, CircuitState
from .service import CircuitBreakerService
from .convenience import get_circuit_breaker_service, should_allow_request, force_open_circuit
```

**선정 근거 (코드 분석):**
- `CircuitBreakerService`: 메인 서비스 클래스
- `get_circuit_breaker_service`: 외부 진입점 함수
- `should_allow_request`: 가장 빈번하게 호출되는 함수
- `CircuitBreakerConfig`, `CircuitBreakerResult`, `CircuitState`: 기본 타입

### 2.3 Lazy import 대상 (119개)

| 서브모듈 | 심볼 | 모듈 경로 |
|---------|------|----------|
| rate_limit_tracker | RateLimitTracker, get_rate_limit_tracker | `.rate_limit_tracker` |
| protection | ProtectionMixin | `.protection` |
| manual_control | ManualControlMixin | `.manual_control` |
| models | ServiceConfig, SheddingLevel, ... (11개) | `.models` |
| adaptive_threshold | AdaptiveThresholdManager, ... (5개) | `.adaptive_threshold` |
| freeze_mode | FreezeModeManager, ... (5개) | `.freeze_mode` |
| panic_threshold | PanicThresholdMonitor, ... (5개) | `.panic_threshold` |
| tracing | TracingConfig, ... (8개) | `.tracing` |
| service_config | ServiceConfigManager, ... (9개) | `.service_config` |
| blast_radius_integration | BlastRadiusLevel, ... (11개) | `.blast_radius_integration` |
| canary_recovery | CanaryState, ... (16개) | `.canary_recovery` |
| stale_cache_integration | CanaryWithStaleCacheConfig, ... (12개) | `.stale_cache_integration` |
| recovery_strategy | RecoveryStrategySelection, ... (12개) | `.recovery_strategy` |
| load_shedding | SheddingState, ... (17개) | `.load_shedding` |
| convenience (추가) | force_close_circuit, record_rate_limit, ... (4개) | `.convenience` |

---

## 3. 구현 코드

### 3.1 새로운 `__init__.py` 구조

```python
"""
Circuit Breaker Module

Provides circuit breaker functionality for external service protection.

Usage:
    from selfhealing.services.circuit_breaker import (
        CircuitBreakerService,
        get_circuit_breaker_service,
        should_allow_request,
    )
    
    # 확장 기능 (필요 시 로드)
    from selfhealing.services.circuit_breaker import AdaptiveThresholdManager
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# =============================================================================
# CORE API - 직접 import (7개)
# =============================================================================
from .config import CircuitBreakerConfig, CircuitBreakerResult, CircuitState
from .service import CircuitBreakerService
from .convenience import get_circuit_breaker_service, should_allow_request, force_open_circuit

# =============================================================================
# LAZY IMPORTS - 119개 심볼
# =============================================================================
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # rate_limit_tracker
    "RateLimitTracker": (".rate_limit_tracker", "RateLimitTracker"),
    "get_rate_limit_tracker": (".rate_limit_tracker", "get_rate_limit_tracker"),
    
    # protection
    "ProtectionMixin": (".protection", "ProtectionMixin"),
    
    # manual_control
    "ManualControlMixin": (".manual_control", "ManualControlMixin"),
    
    # convenience (추가 함수)
    "force_close_circuit": (".convenience", "force_close_circuit"),
    "record_rate_limit": (".convenience", "record_rate_limit"),
    "should_allow_with_protection": (".convenience", "should_allow_with_protection"),
    "get_protection_status": (".convenience", "get_protection_status"),
    
    # config (추가 타입)
    "FallbackResult": (".config", "FallbackResult"),
    
    # models
    "ServiceConfig": (".models", "ServiceConfig"),
    "SheddingLevel": (".models", "SheddingLevel"),
    "LoadSheddingPolicy": (".models", "LoadSheddingPolicy"),
    "CanaryStage": (".models", "CanaryStage"),
    "RecoveryStrategy": (".models", "RecoveryStrategy"),
    "ThresholdMultiplier": (".models", "ThresholdMultiplier"),
    "AdaptiveThresholdPolicy": (".models", "AdaptiveThresholdPolicy"),
    "OpenStrategy": (".models", "OpenStrategy"),
    "CircuitBreakerAdvancedConfig": (".models", "CircuitBreakerAdvancedConfig"),
    "PanicThresholdConfig": (".models", "PanicThresholdConfig"),
    "FreezeModeState": (".models", "FreezeModeState"),
    
    # adaptive_threshold
    "AdaptiveThresholdManager": (".adaptive_threshold", "AdaptiveThresholdManager"),
    "AdjustedThreshold": (".adaptive_threshold", "AdjustedThreshold"),
    "get_adaptive_threshold_manager": (".adaptive_threshold", "get_adaptive_threshold_manager"),
    "get_adjusted_cb_threshold": (".adaptive_threshold", "get_adjusted_cb_threshold"),
    "should_allow_cb_auto_open": (".adaptive_threshold", "should_allow_cb_auto_open"),
    
    # freeze_mode
    "FreezeModeManager": (".freeze_mode", "FreezeModeManager"),
    "FreezeReason": (".freeze_mode", "FreezeReason"),
    "get_freeze_mode_manager": (".freeze_mode", "get_freeze_mode_manager"),
    "is_freeze_mode_active": (".freeze_mode", "is_freeze_mode_active"),
    "should_allow_cb_state_change": (".freeze_mode", "should_allow_cb_state_change"),
    
    # panic_threshold
    "PanicThresholdMonitor": (".panic_threshold", "PanicThresholdMonitor"),
    "PanicThresholdResult": (".panic_threshold", "PanicThresholdResult"),
    "get_panic_threshold_monitor": (".panic_threshold", "get_panic_threshold_monitor"),
    "check_panic_threshold": (".panic_threshold", "check_panic_threshold"),
    "is_panic_threshold_triggered": (".panic_threshold", "is_panic_threshold_triggered"),
    
    # tracing
    "TracingConfig": (".tracing", "TracingConfig"),
    "TriggeringRequestInfo": (".tracing", "TriggeringRequestInfo"),
    "TraceContextProvider": (".tracing", "TraceContextProvider"),
    "CircuitBreakerTracingManager": (".tracing", "CircuitBreakerTracingManager"),
    "get_tracing_manager": (".tracing", "get_tracing_manager"),
    "record_failure_with_trace": (".tracing", "record_failure_with_trace"),
    "get_triggering_request": (".tracing", "get_triggering_request"),
    "log_state_change_with_trace": (".tracing", "log_state_change_with_trace"),
    
    # service_config
    "ServiceConfigManager": (".service_config", "ServiceConfigManager"),
    "get_service_config_manager": (".service_config", "get_service_config_manager"),
    "reset_service_config_manager": (".service_config", "reset_service_config_manager"),
    "register_service": (".service_config", "register_service"),
    "get_service_config": (".service_config", "get_service_config"),
    "get_services_by_criticality": (".service_config", "get_services_by_criticality"),
    "get_shedding_targets": (".service_config", "get_shedding_targets"),
    "is_critical_service": (".service_config", "is_critical_service"),
    
    # blast_radius_integration
    "BlastRadiusLevel": (".blast_radius_integration", "BlastRadiusLevel"),
    "BlastRadiusAssessment": (".blast_radius_integration", "BlastRadiusAssessment"),
    "ServiceDependency": (".blast_radius_integration", "ServiceDependency"),
    "ServiceDependencyGraph": (".blast_radius_integration", "ServiceDependencyGraph"),
    "BlastRadiusIntegration": (".blast_radius_integration", "BlastRadiusIntegration"),
    "BlastRadiusConfig": (".blast_radius_integration", "BlastRadiusConfig"),
    "get_blast_radius_integration": (".blast_radius_integration", "get_blast_radius_integration"),
    "reset_blast_radius_integration": (".blast_radius_integration", "reset_blast_radius_integration"),
    "assess_cb_open_impact": (".blast_radius_integration", "assess_cb_open_impact"),
    "should_allow_cb_auto_open_blast": (".blast_radius_integration", "should_allow_cb_auto_open"),
    "register_service_dependency": (".blast_radius_integration", "register_service_dependency"),
    
    # canary_recovery (16개)
    "CanaryState": (".canary_recovery", "CanaryState"),
    "CanaryStageMetrics": (".canary_recovery", "CanaryStageMetrics"),
    "CanaryRecoveryState": (".canary_recovery", "CanaryRecoveryState"),
    "CanaryDecision": (".canary_recovery", "CanaryDecision"),
    "CanaryStageTransitionResult": (".canary_recovery", "CanaryStageTransitionResult"),
    "CanaryRecoveryManager": (".canary_recovery", "CanaryRecoveryManager"),
    "get_canary_recovery_manager": (".canary_recovery", "get_canary_recovery_manager"),
    "reset_canary_recovery_manager": (".canary_recovery", "reset_canary_recovery_manager"),
    "start_canary_recovery": (".canary_recovery", "start_canary_recovery"),
    "stop_canary_recovery": (".canary_recovery", "stop_canary_recovery"),
    "is_in_canary_recovery": (".canary_recovery", "is_in_canary_recovery"),
    "canary_should_allow_request": (".canary_recovery", "canary_should_allow_request"),
    "canary_record_success": (".canary_recovery", "canary_record_success"),
    "canary_record_failure": (".canary_recovery", "canary_record_failure"),
    "get_canary_recovery_state": (".canary_recovery", "get_canary_recovery_state"),
    
    # stale_cache_integration (12개)
    "CanaryWithStaleCacheConfig": (".stale_cache_integration", "CanaryWithStaleCacheConfig"),
    "StaleCacheEntry": (".stale_cache_integration", "StaleCacheEntry"),
    "CanaryWithStaleDecision": (".stale_cache_integration", "CanaryWithStaleDecision"),
    "StaleCacheStore": (".stale_cache_integration", "StaleCacheStore"),
    "CanaryWithStaleCacheService": (".stale_cache_integration", "CanaryWithStaleCacheService"),
    "get_canary_stale_cache_service": (".stale_cache_integration", "get_canary_stale_cache_service"),
    "reset_canary_stale_cache_service": (".stale_cache_integration", "reset_canary_stale_cache_service"),
    "canary_should_allow_with_fallback": (".stale_cache_integration", "should_allow_with_fallback"),
    "update_stale_cache": (".stale_cache_integration", "update_stale_cache"),
    "record_canary_success": (".stale_cache_integration", "record_canary_success"),
    "record_canary_failure": (".stale_cache_integration", "record_canary_failure"),
    
    # recovery_strategy (12개)
    "RecoveryStrategySelection": (".recovery_strategy", "RecoveryStrategySelection"),
    "RecoveryDecision": (".recovery_strategy", "RecoveryDecision"),
    "RecoveryStrategySelector": (".recovery_strategy", "RecoveryStrategySelector"),
    "get_recovery_strategy_selector": (".recovery_strategy", "get_recovery_strategy_selector"),
    "reset_recovery_strategy_selector": (".recovery_strategy", "reset_recovery_strategy_selector"),
    "select_recovery_strategy": (".recovery_strategy", "select_recovery_strategy"),
    "start_service_recovery": (".recovery_strategy", "start_service_recovery"),
    "stop_service_recovery": (".recovery_strategy", "stop_service_recovery"),
    "handle_half_open": (".recovery_strategy", "handle_half_open"),
    "record_recovery_success": (".recovery_strategy", "record_recovery_success"),
    "record_recovery_failure": (".recovery_strategy", "record_recovery_failure"),
    
    # load_shedding (17개)
    "SheddingState": (".load_shedding", "SheddingState"),
    "SheddingDecision": (".load_shedding", "SheddingDecision"),
    "SheddingStatus": (".load_shedding", "SheddingStatus"),
    "SheddingAuditEntry": (".load_shedding", "SheddingAuditEntry"),
    "ErrorRateProvider": (".load_shedding", "ErrorRateProvider"),
    "LoadSheddingManager": (".load_shedding", "LoadSheddingManager"),
    "LoadSheddingMiddleware": (".load_shedding", "LoadSheddingMiddleware"),
    "LoadSheddingDashboard": (".load_shedding", "LoadSheddingDashboard"),
    "get_load_shedding_manager": (".load_shedding", "get_load_shedding_manager"),
    "reset_load_shedding_manager": (".load_shedding", "reset_load_shedding_manager"),
    "get_load_shedding_middleware": (".load_shedding", "get_load_shedding_middleware"),
    "get_load_shedding_dashboard": (".load_shedding", "get_load_shedding_dashboard"),
    "register_load_shedding_service": (".load_shedding", "register_load_shedding_service"),
    "evaluate_shedding": (".load_shedding", "evaluate_shedding"),
    "should_allow_shedding_request": (".load_shedding", "should_allow_shedding_request"),
    "is_shedding_active": (".load_shedding", "is_shedding_active"),
    "get_shedding_status": (".load_shedding", "get_shedding_status"),
    "set_service_error_rate": (".load_shedding", "set_service_error_rate"),
    "update_shedding_state": (".load_shedding", "update_shedding_state"),
}

# Cache for loaded symbols
_loaded_symbols: dict[str, object] = {}


def __getattr__(name: str) -> object:
    """Lazy import for circuit breaker symbols."""
    if name in _loaded_symbols:
        return _loaded_symbols[name]
    
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path, __package__)
        symbol = getattr(module, attr_name)
        _loaded_symbols[name] = symbol
        return symbol
    
    raise AttributeError(f"module 'selfhealing.services.circuit_breaker' has no attribute '{name}'")


def __dir__() -> list[str]:
    """List available symbols for IDE autocompletion."""
    return list(__all__)


# TYPE_CHECKING block for IDE support
if TYPE_CHECKING:
    from .rate_limit_tracker import RateLimitTracker, get_rate_limit_tracker
    from .protection import ProtectionMixin
    from .manual_control import ManualControlMixin
    # ... (모든 lazy import 심볼에 대한 타입 힌트)


__all__ = [
    # Core API (직접 import)
    "CircuitBreakerConfig",
    "CircuitBreakerResult", 
    "CircuitState",
    "CircuitBreakerService",
    "get_circuit_breaker_service",
    "should_allow_request",
    "force_open_circuit",
    # ... (전체 126개 __all__ 유지)
]
```

---

## 4. 예상 효과

### 4.1 정량적 효과

| 지표 | Before | After | 개선율 |
|------|--------|-------|-------|
| 즉시 로드 모듈 | 15개 | 3개 | -80% |
| 즉시 로드 심볼 | 126개 | 7개 | -94% |
| 패키지 로드 시간 | ~100ms (추정) | ~10ms (추정) | -90% |

### 4.2 하위 호환성

| 기존 코드 | 변경 후 동작 |
|----------|------------|
| `from circuit_breaker import get_circuit_breaker_service` | ✅ 동일 (직접 import) |
| `from circuit_breaker import LoadSheddingManager` | ✅ 동일 (`__getattr__` 해결) |
| `from circuit_breaker import *` | ✅ `__all__` 기반 동작 |

---

## 5. 구현 체크리스트

- [x] `_LAZY_IMPORTS` 딕셔너리 정의 (119개 심볼)
- [x] 핵심 API 7개만 직접 import 유지
- [x] `__getattr__` 함수 구현
- [x] `__dir__` 함수 구현
- [x] `TYPE_CHECKING` 블록 추가
- [x] `__all__` 유지 (125개 전체)
- [x] 단위 테스트 통과 확인 (7개 기본 테스트 + 28개 기존 테스트)
- [ ] Docker 테스트 통과 확인
- [x] Git 커밋

---

## 6. 구현 결과

### 6.1 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/__init__.py` | Lazy Import 패턴 적용 |
| `tests/self_healing/services/circuit_breaker/test_lazy_import.py` | 테스트 코드 추가 |

### 6.2 구현 상세

**직접 import (7개 핵심 API):**
- `CircuitBreakerConfig`, `CircuitBreakerResult`, `CircuitState`
- `CircuitBreakerService`
- `get_circuit_breaker_service`, `should_allow_request`, `force_open_circuit`

**Lazy import (118개 확장 기능):**
- `_LAZY_IMPORTS` 딕셔너리로 모듈 경로와 심볼 이름 매핑
- `__getattr__` 함수로 최초 접근 시 로딩
- `_loaded_symbols` 딕셔너리로 캐싱

### 6.3 테스트 결과

| 테스트 카테고리 | 결과 |
|----------------|------|
| Core API 직접 import | ✅ PASS |
| Lazy loaded symbols | ✅ PASS |
| `__all__` 125개 심볼 | ✅ PASS |
| `__dir__` 반환 확인 | ✅ PASS |
| Invalid attribute 에러 | ✅ PASS |
| Lazy import 캐싱 | ✅ PASS |
| 모든 카테고리 동작 | ✅ PASS |
| 기존 테스트 호환성 | ✅ 28개 PASS |

### 6.4 효과 측정

| 지표 | Before | After | 개선율 |
|------|--------|-------|-------|
| 즉시 로드 모듈 | 15개 | 3개 | -80% |
| 즉시 로드 심볼 | 126개 | 7개 | -94% |
| 하위 호환성 | - | 100% 유지 | ✅ |

---

## 7. 참고 자료

| 문서 | 경로 |
|------|------|
| 현재 파일 | `services/circuit_breaker/__init__.py` (376줄) |
| Lazy Import 참조 | `api/django/views/chaos/__init__.py` |
| 패턴 효율성 분석 | `64_PATTERN_EFFICIENCY_ANALYSIS.md` |
