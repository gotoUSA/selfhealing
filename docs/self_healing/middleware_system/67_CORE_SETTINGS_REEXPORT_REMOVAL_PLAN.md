# 67. core/__init__.py 리팩토링 계획서 (Settings Re-export 제거)

| 항목 | 내용 |
|-----|------|
| 버전 | 1.0 |
| 작성일 | 2026-01-19 |
| 우선순위 | 🟡 단기 |
| 예상 효과 | API 혼란 제거, 순환 의존성 위험 제거 |

---

## 1. 현황 분석

### 1.1 문제점

`selfhealing/core/__init__.py`가 `selfhealing/settings/`의 심볼을 **중복 re-export**.

**코드 근거:**
```python
# core/__init__.py:37-65 (28줄에 걸쳐 26개 심볼 re-export)
from selfhealing.settings import (
    SelfHealingSettings as SelfHealingConfig,
    CircuitBreakerSettings as CircuitBreakerConfig,
    DLQSettings as DLQConfig,
    RetrySettings as RetryConfig,
    SLASettings as SLAConfig,
    SLASettings as SLAThresholds,  # Legacy alias
    IdempotencySettings as IdempotencyConfig,
    SecuritySettings as SecurityConfig,
    SecuritySettings as SecurityThresholds,  # Legacy alias
    ForensicSettings as ForensicConfig,
    MetricsSettings as MetricsConfig,
    NotificationSettings as NotificationConfig,
    NotificationSettings as NotificationLimits,  # Legacy alias
    get_config,
    set_config,
    configure,
    reload_config,
    get_circuit_breaker_config as get_circuit_breaker_settings,
    get_dlq_settings,
    get_retry_settings,
    get_sla_thresholds,
    get_security_thresholds,
    get_forensic_settings,
    get_notification_settings,
)
```

### 1.2 문제점 상세

| 문제 | 설명 | 영향 |
|------|------|------|
| **API 중복** | 동일 심볼이 2개 경로로 접근 가능 | 사용자 혼란 |
| **Legacy alias 중복** | `SLAThresholds`, `SecurityThresholds`, `NotificationLimits` | 3개 alias 불필요 |
| **순환 의존성 위험** | `settings` → `core` 간 상호 참조 가능성 | 런타임 오류 |
| **유지보수 어려움** | settings 변경 시 core도 수정 필요 | 이중 작업 |

### 1.3 동일 심볼 2경로 문제 (코드 근거)

```python
# 경로 1: 올바른 경로
from selfhealing.settings import get_config, CircuitBreakerSettings

# 경로 2: 중복 경로 (core를 통해)
from selfhealing.core import get_config, CircuitBreakerConfig
```

**실제 사용 패턴 분석:**
```bash
# settings 직접 import (올바른 패턴) - 20+ 곳
$ grep -rn "from selfhealing.settings import" packages/selfhealing-python/src/ | wc -l
# 결과: 27

# core 통해 settings import (비권장 패턴) - 테스트 코드
$ grep -rn "from selfhealing.core import.*Config" tests/ | wc -l  
# 결과: 12 (대부분 테스트 코드)
```

---

## 2. 해결 방안

### 2.1 선택지 비교

| 선택지 | 장점 | 단점 | 권장 |
|--------|------|------|------|
| A. 즉시 제거 | 깔끔한 API | 하위 호환성 파괴 | ❌ |
| B. Deprecation Warning + 점진적 제거 | 마이그레이션 시간 확보 | 시간 소요 | ⭐⭐⭐ |
| C. `__getattr__` 로 lazy warning | 실제 사용 시에만 경고 | 구현 복잡 | ⭐⭐ |

### 2.2 권장안: B. Deprecation Warning + 점진적 제거

**이유:**
1. 테스트 코드에서 `from selfhealing.core import SLAThresholds` 등 사용 중
2. 외부 사용자도 core 경로 사용 가능성
3. 충분한 마이그레이션 시간 필요

---

## 3. 구현 전략

### 3.1 Phase 1: Deprecation Warning 추가 (즉시)

```python
# core/__init__.py - 변경

import warnings
from typing import TYPE_CHECKING

# =============================================================================
# DEPRECATED: Settings re-exports
# Use 'from selfhealing.settings import ...' instead
# Will be removed in v3.0.0
# =============================================================================

def _deprecated_settings_import(name: str):
    """Helper to emit deprecation warning for settings imports."""
    warnings.warn(
        f"Importing '{name}' from 'selfhealing.core' is deprecated. "
        f"Use 'from selfhealing.settings import {name}' instead. "
        "This will be removed in v3.0.0.",
        DeprecationWarning,
        stacklevel=3,
    )

# Lazy import with deprecation warning
_DEPRECATED_SETTINGS_IMPORTS = {
    # Settings Classes (re-exported with alias)
    "SelfHealingConfig": ("selfhealing.settings", "SelfHealingSettings"),
    "CircuitBreakerConfig": ("selfhealing.settings", "CircuitBreakerSettings"),
    "DLQConfig": ("selfhealing.settings", "DLQSettings"),
    "RetryConfig": ("selfhealing.settings", "RetrySettings"),
    "SLAConfig": ("selfhealing.settings", "SLASettings"),
    "SLAThresholds": ("selfhealing.settings", "SLASettings"),  # Extra alias
    "IdempotencyConfig": ("selfhealing.settings", "IdempotencySettings"),
    "SecurityConfig": ("selfhealing.settings", "SecuritySettings"),
    "SecurityThresholds": ("selfhealing.settings", "SecuritySettings"),  # Extra alias
    "ForensicConfig": ("selfhealing.settings", "ForensicSettings"),
    "MetricsConfig": ("selfhealing.settings", "MetricsSettings"),
    "NotificationConfig": ("selfhealing.settings", "NotificationSettings"),
    "NotificationLimits": ("selfhealing.settings", "NotificationSettings"),  # Extra alias
    # Settings Functions
    "get_config": ("selfhealing.settings", "get_config"),
    "set_config": ("selfhealing.settings", "set_config"),
    "configure": ("selfhealing.settings", "configure"),
    "reload_config": ("selfhealing.settings", "reload_config"),
    "get_circuit_breaker_settings": ("selfhealing.settings", "get_circuit_breaker_config"),
    "get_dlq_settings": ("selfhealing.settings", "get_dlq_settings"),
    "get_retry_settings": ("selfhealing.settings", "get_retry_settings"),
    "get_sla_thresholds": ("selfhealing.settings", "get_sla_thresholds"),
    "get_security_thresholds": ("selfhealing.settings", "get_security_thresholds"),
    "get_forensic_settings": ("selfhealing.settings", "get_forensic_settings"),
    "get_notification_settings": ("selfhealing.settings", "get_notification_settings"),
}

_deprecated_cache: dict[str, object] = {}

def __getattr__(name: str) -> object:
    """Lazy import with deprecation warning for settings symbols."""
    if name in _deprecated_cache:
        return _deprecated_cache[name]
    
    if name in _DEPRECATED_SETTINGS_IMPORTS:
        _deprecated_settings_import(name)
        module_path, attr_name = _DEPRECATED_SETTINGS_IMPORTS[name]
        import importlib
        module = importlib.import_module(module_path)
        symbol = getattr(module, attr_name)
        _deprecated_cache[name] = symbol
        return symbol
    
    raise AttributeError(f"module 'selfhealing.core' has no attribute '{name}'")
```

### 3.2 Phase 2: 테스트 코드 마이그레이션 (1주일)

| 파일 | 변경 전 | 변경 후 |
|------|--------|--------|
| `test_time_based_behaviors.py` | `from selfhealing.core import SLAThresholds` | `from selfhealing.settings import SLASettings` |
| `test_self_healing_policy.py` | `from selfhealing.core import CircuitState` | 유지 (core 고유 심볼) |
| 기타 테스트 | `from selfhealing.core import get_config` | `from selfhealing.settings import get_config` |

### 3.3 Phase 3: `__all__`에서 제거 (2주 후)

```python
# core/__init__.py - __all__ 정리

__all__ = [
    # Types (core 고유)
    "FailureType",
    "OperationStatus",
    "CircuitState",
    "DomainType",
    "FailedOperationData",
    ...
    
    # Backoff (core 고유)
    "ExponentialBackoff",
    "LinearBackoff",
    ...
    
    # ❌ REMOVED: Settings re-exports
    # "SelfHealingConfig",  # Use settings.SelfHealingSettings
    # "CircuitBreakerConfig",  # Use settings.CircuitBreakerSettings
    # ...
]
```

### 3.4 Phase 4: 완전 제거 (v3.0.0)

- `_DEPRECATED_SETTINGS_IMPORTS` 삭제
- `__getattr__` 에서 settings 관련 로직 제거
- 직접 import 문 완전 제거

---

## 4. Core 고유 심볼 유지 목록

**코드 근거 (`core/__init__.py` 분석):**

| 카테고리 | 심볼 | 상태 |
|---------|------|------|
| Types | `FailureType`, `OperationStatus`, `CircuitState`, `DomainType` | ✅ 유지 |
| Data Classes | `FailedOperationData`, `CircuitBreakerStateData`, `SecurityIncidentData` | ✅ 유지 |
| Backoff | `ExponentialBackoff`, `LinearBackoff`, `ConstantBackoff`, `DecorrelatedJitterBackoff` | ✅ 유지 |
| Pool Monitor | `PoolHealthStatus`, `PoolStats`, `ConnectionPoolMonitor` | ✅ 유지 |
| Shutdown | `GracefulShutdownCoordinator`, `RequestTracker` | ✅ 유지 |
| Time Provider | `TimeProvider`, `SystemTimeProvider`, `MockTimeProvider` | ✅ 유지 |
| Connection Health | `ConnectionHealthMonitor`, `PartitionState` | ✅ 유지 |
| TLS Handler | `TLSResilientClient`, `CertificateExpiryMonitor` | ✅ 유지 |
| Decision Logger | `DecisionLogger`, `ReasonCode` | ✅ 유지 |
| Execution Mode | `ExecutionMode`, `get_execution_mode` | ✅ 유지 |
| Action Executor | `ActionExecutor`, `execute_action` | ✅ 유지 |

**총 core 고유 심볼: ~70개 (유지)**
**settings re-export 심볼: 26개 (제거 대상)**

---

## 5. 마이그레이션 가이드

### 5.1 변경 전 → 변경 후

| 변경 전 | 변경 후 |
|--------|--------|
| `from selfhealing.core import SelfHealingConfig` | `from selfhealing.settings import SelfHealingSettings` |
| `from selfhealing.core import CircuitBreakerConfig` | `from selfhealing.settings import CircuitBreakerSettings` |
| `from selfhealing.core import get_config` | `from selfhealing.settings import get_config` |
| `from selfhealing.core import SLAThresholds` | `from selfhealing.settings import SLASettings` |
| `from selfhealing.core import SecurityThresholds` | `from selfhealing.settings import SecuritySettings` |
| `from selfhealing.core import NotificationLimits` | `from selfhealing.settings import NotificationSettings` |

### 5.2 Deprecation Warning 예시

```
DeprecationWarning: Importing 'SLAThresholds' from 'selfhealing.core' is deprecated.
Use 'from selfhealing.settings import SLASettings' instead.
This will be removed in v3.0.0.
```

---

## 6. 예상 효과

### 6.1 정량적 효과

| 지표 | Before | After | 개선율 |
|------|--------|-------|-------|
| core re-export 심볼 | 26개 | 0개 | -100% |
| API 경로 중복 | 26개 | 0개 | -100% |
| Legacy alias | 3개 | 0개 | -100% |

### 6.2 정성적 효과

| 효과 | 설명 |
|------|------|
| **API 명확성** | settings는 settings에서, core는 core에서 |
| **순환 의존성 제거** | settings ↔ core 간 참조 제거 |
| **유지보수 간소화** | settings 변경 시 core 수정 불필요 |
| **문서화 간소화** | 단일 경로만 문서화 |

---

## 7. 구현 체크리스트

### Phase 1 (즉시)
- [ ] `_DEPRECATED_SETTINGS_IMPORTS` 딕셔너리 추가
- [ ] `__getattr__` 함수 구현 (deprecation warning 포함)
- [ ] 기존 직접 import 문 제거
- [ ] 단위 테스트 통과 확인

### Phase 2 (1주일 내)
- [ ] 테스트 코드 마이그레이션 (12곳)
- [ ] CI 경고 무시 설정 확인

### Phase 3 (2주 후)
- [ ] `__all__`에서 settings 심볼 제거
- [ ] 문서 업데이트

### Phase 4 (v3.0.0)
- [ ] `_DEPRECATED_SETTINGS_IMPORTS` 완전 제거
- [ ] `__getattr__` 정리

---

## 8. 참고 자료

| 문서 | 경로 |
|------|------|
| 현재 파일 | `core/__init__.py` (286줄) |
| settings 정의 | `settings/__init__.py` (314줄) |
| 패턴 효율성 분석 | `64_PATTERN_EFFICIENCY_ANALYSIS.md` |
