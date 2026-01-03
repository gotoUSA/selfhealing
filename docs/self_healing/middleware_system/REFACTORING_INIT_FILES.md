# `__init__.py` 정리 상세 계획

> **Created**: 2026-01-03
> **Parent**: [REFACTORING_PLAN.md](REFACTORING_PLAN.md)

---

## 📋 목차

1. [현황](#1-현황)
2. [정리 대상](#2-정리-대상)
3. [유지할 Public API](#3-유지할-public-api)
4. [세부 작업](#4-세부-작업)

---

## 1. 현황

### 1.1 re-export가 많은 `__init__.py` 파일

| 파일 | 현재 라인 수 | re-export 수 |
|------|-------------|-------------|
| `services/__init__.py` | 512줄 | 180개 |
| `services/metrics/__init__.py` | 213줄 | 68개 |
| `tasks/__init__.py` | 129줄 | 36개 |
| `services/chaos/__init__.py` | 98줄 | 31개 |
| `api/django/views/xtest/__init__.py` | - | 23개 |
| `services/factory/__init__.py` | - | 19개 |
| `api/django/tiering/__init__.py` | 100줄 | 19개 |
| `api/django/views/error_budget/__init__.py` | 92줄 | 19개 |
| `services/circuit_breaker/__init__.py` | 87줄 | 16개 |
| `adapters/audit/__init__.py` | 154줄 | 12개 |

### 1.2 문제점

1. **과도한 re-export**: 하위 모듈의 거의 모든 클래스를 상위에서 re-export
2. **deprecation warning 혼재**: 레거시 호환성 코드와 실제 기능 코드 혼재
3. **순환 import 위험**: 많은 re-export가 순환 import 가능성 증가
4. **도구 혼란**: Vulture 등 분석 도구가 실제 사용 여부 파악 어려움

---

## 2. 정리 대상

### 2.1 완전 비움 (re-export 0개)

다음 `__init__.py`는 완전히 비웁니다:

| 패키지 | 이유 |
|--------|------|
| `services/metrics/__init__.py` | 내부 전용, 필요 시 직접 import |
| `services/chaos/__init__.py` | 내부 전용 |
| `services/circuit_breaker/__init__.py` | 내부 전용 |
| `services/factory/__init__.py` | 내부 전용 |
| `services/emergency_mode/__init__.py` | 내부 전용 |
| `services/error_budget/__init__.py` | 내부 전용 |
| `services/runtime_config/__init__.py` | 내부 전용 |
| `services/blast_radius/__init__.py` | 내부 전용 |
| `services/compliance/__init__.py` | 내부 전용 |
| `services/finops/__init__.py` | 내부 전용 |
| `services/learning/__init__.py` | 내부 전용 |
| `services/rollback/__init__.py` | 내부 전용 |
| `api/django/tiering/__init__.py` | 내부 전용 |
| `api/django/views/error_budget/__init__.py` | 내부 전용 |
| `api/django/views/xtest/__init__.py` | 테스트 전용 |
| `adapters/audit/__init__.py` | factory에서 직접 import |
| `adapters/alert/__init__.py` | factory에서 직접 import |
| `tasks/__init__.py` | Celery가 직접 태스크 파일 로드 |

**비움 형태:**
```python
"""
패키지 설명

직접 import 사용:
    from selfhealing.services.circuit_breaker.service import CircuitBreakerService
"""
# No re-exports - use direct imports
```

### 2.2 최소화 (핵심 API만)

다음 `__init__.py`는 핵심 API만 남깁니다:

| 패키지 | 현재 | 목표 |
|--------|------|------|
| `selfhealing/__init__.py` | ? | ~5개 |
| `services/__init__.py` | 180개 | ~15개 |
| `interfaces/__init__.py` | ? | ~10개 |

---

## 3. 유지할 Public API

### 3.1 `selfhealing/__init__.py`

외부 사용자가 가장 먼저 접근하는 최상위 패키지.
**가장 핵심적인 것만** 노출.

```python
"""
Self-Healing 패키지

사용법:
    from selfhealing import DLQService, ProviderRegistry
    
    # 또는 서비스 레이어에서:
    from selfhealing.services import get_circuit_breaker_service
"""

from selfhealing.services.dlq_service import DLQService
from selfhealing.factory import ProviderRegistry

__all__ = [
    "DLQService",
    "ProviderRegistry",
]
```

### 3.2 `services/__init__.py`

서비스 레이어 Public API.
**shopping 앱에서 실제로 사용하는 것** 기준으로 선정.

```python
"""
Self-Healing 서비스 레이어

사용법:
    from selfhealing.services import get_circuit_breaker_service
    
    cb = get_circuit_breaker_service("payment")
    cb.check()
"""

# === 핵심 서비스 getter ===
from selfhealing.services.dlq_service import DLQService, get_dlq_service
from selfhealing.services.replay_service import get_replay_service
from selfhealing.services.circuit_breaker_service import (
    get_circuit_breaker_service,
    CircuitBreakerService,
)
from selfhealing.services.error_budget_service import get_error_budget_service

# === 자주 사용되는 유틸 ===
from selfhealing.services.metrics.recorders import record_sla_breach
from selfhealing.services.sla_config import get_sla_thresholds
from selfhealing.services.forensic_context import ForensicContext
from selfhealing.services.security_notification_service import get_security_notification_service
from selfhealing.services.security_violation_service import SecurityViolationService

# === 자주 사용되는 상수 ===
from selfhealing.services.metrics.definitions import DOMAINS
from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

# === Runtime Config ===
from selfhealing.services.runtime_config.manager import RuntimeConfigManager

# === 메트릭 수집 ===
from selfhealing.services.metrics.collectors import collect_all_metrics

__all__ = [
    # 서비스
    "DLQService", "get_dlq_service",
    "get_replay_service",
    "get_circuit_breaker_service", "CircuitBreakerService",
    "get_error_budget_service",
    # 유틸
    "record_sla_breach",
    "get_sla_thresholds",
    "ForensicContext",
    "get_security_notification_service",
    "SecurityViolationService",
    # 상수
    "DOMAINS", "ALERTING_RULES",
    # Config
    "RuntimeConfigManager",
    # 메트릭
    "collect_all_metrics",
]
```

### 3.3 `interfaces/__init__.py`

인터페이스(ABC) 레이어.
외부에서 커스텀 구현체 만들 때 필요한 것만.

```python
"""
Self-Healing 인터페이스 (ABC)

사용법:
    from selfhealing.interfaces import CacheProviderInterface
    
    class MyCache(CacheProviderInterface):
        ...
"""

from selfhealing.interfaces.repositories import (
    FailedOperationRepository,
    FailedOperationData,
)
from selfhealing.interfaces.cache_provider import CacheProviderInterface
from selfhealing.interfaces.task_queue import TaskQueueInterface
from selfhealing.interfaces.audit_adapter import AuditLogAdapter
from selfhealing.interfaces.alert_adapter import AlertAdapter

__all__ = [
    "FailedOperationRepository",
    "FailedOperationData",
    "CacheProviderInterface",
    "TaskQueueInterface",
    "AuditLogAdapter",
    "AlertAdapter",
]
```

---

## 4. 세부 작업

### 4.1 작업 순서

```
1. Phase 1 완료 (내부 import 직접화) ← 먼저!
   ↓
2. services/metrics/__init__.py 비움
   ↓
3. services/circuit_breaker/__init__.py 비움
   ↓
4. services/emergency_mode/__init__.py 비움
   ↓
5. 기타 하위 패키지 비움
   ↓
6. services/__init__.py 최소화
   ↓
7. selfhealing/__init__.py 정리
   ↓
8. 테스트 실행 및 검증
```

### 4.2 각 파일별 작업

#### `services/metrics/__init__.py`

**현재:** 68개 re-export
**목표:** 비움

```python
# Before (현재)
from .definitions import DOMAINS, METRIC_NAMES, ...
from .recorders import record_sla_breach, emit_heartbeat, ...
from .updaters import update_dlq_pending_gauges, ...
from .registry import get_registered_domains, ...
from .alerting_rules import ALERTING_RULES, ...
# ... 68개

# After (목표)
"""
메트릭 모듈

직접 import 사용:
    from selfhealing.services.metrics.recorders import record_sla_breach
    from selfhealing.services.metrics.definitions import DOMAINS
"""
# No re-exports
```

#### `services/circuit_breaker/__init__.py`

**현재:** 16개 re-export
**목표:** 비움

```python
# Before (현재)
from .service import CircuitBreakerService
from .config import CircuitBreakerConfig, CircuitState, CircuitBreakerResult
from .protection import ProtectionHandler
from .rate_limit_tracker import RateLimitTracker, get_rate_limit_tracker
# ... 16개

# After (목표)
"""
Circuit Breaker 모듈

직접 import 사용:
    from selfhealing.services.circuit_breaker.service import CircuitBreakerService
    from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
"""
# No re-exports
```

#### `tasks/__init__.py`

**현재:** 36개 re-export
**목표:** 비움

```python
# Before (현재)
from .base import BaseTask, ...
from .chaos_scheduler import ...
from .config_apply import ...
# ... 36개

# After (목표)
"""
Celery 태스크 모듈

Celery가 직접 태스크 파일을 로드합니다.
tasks.chaos_scheduler, tasks.config_apply 등은
Celery Beat 설정에서 직접 참조됩니다.
"""
# No re-exports - Celery loads tasks directly
```

### 4.3 검증 체크리스트

#### 각 `__init__.py` 정리 후:

- [ ] `python -c "from selfhealing.services.xxx import ..."` 동작 확인
- [ ] shopping 앱 import 확인
- [ ] pytest 실행

#### 전체 완료 후:

- [ ] `python scripts/analyze_dependencies.py` 재실행
- [ ] re-export usage_count = 0 확인
- [ ] 전체 테스트 통과

---

## 📎 관련 문서

- [REFACTORING_PLAN.md](REFACTORING_PLAN.md) - 전체 리팩토링 계획
- [CODE_DEPENDENCY_ANALYSIS.md](../CODE_DEPENDENCY_ANALYSIS.md) - 의존성 분석
