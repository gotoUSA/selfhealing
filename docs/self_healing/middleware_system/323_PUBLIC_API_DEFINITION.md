# 323. Public API Definition — 안정적 Public API 명세

> **Status**: Planning
> **Severity**: P2 (MEDIUM) — repo 분리 선행 조건
> **Target**: `selfhealing/__init__.py`
> **References**:
> - 319 — Repo Separation Overview (커플링 해소, Step 4)

---

## 1. 현황 및 문제

### 1.1 현재 `__init__.py`

```python
# selfhealing/__init__.py — 현재
__version__ = "0.1.0"
from selfhealing.interfaces.repositories import CircuitBreakerStateEnum as CircuitState

__all__ = ["__version__", "CircuitState"]
```

### 1.2 Consumer의 실제 import 패턴

```python
# shopping/services/payment_recovery_service.py
from selfhealing.factory import ProviderRegistry

# shopping/tasks/payment_recovery_tasks.py
from selfhealing.services import get_circuit_breaker_service

# shopping/handlers/replay_handlers.py
from selfhealing.services.replay_service import ReplayService, ReplayRequest
from selfhealing.interfaces.repositories import FailedOperationData
```

**문제**: Consumer가 내부 모듈 경로(`services.replay_service`, `interfaces.repositories`)에 직접 의존.
라이브러리 내부 구조 변경 시 consumer가 깨진다.

---

## 2. 설계

### 2.1 Public API 정의 원칙

1. **Consumer가 import하는 것만 public** — 내부 구현 경로는 private
2. **`selfhealing.*` 또는 `selfhealing.api.*`에서만 import** — 깊은 경로 사용 금지
3. **Semantic Versioning** — public API 변경 시 major 버전 범프

### 2.2 `selfhealing/__init__.py` 확장

```python
# selfhealing/__init__.py — 분리 후
"""
Self-Healing Reliability Layer for Python Applications.

Public API:
    from selfhealing import ProviderRegistry
    from selfhealing import get_circuit_breaker_service
    from selfhealing import CircuitState
    from selfhealing import FailedOperationData
    from selfhealing import ReplayService, ReplayRequest
"""

__version__ = "0.1.0"
__author__ = "SelfHealing Contributors"

# === Core Types ===
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateEnum as CircuitState,
    FailedOperationData,
)

# === Service Access ===
from selfhealing.factory import ProviderRegistry
from selfhealing.services import get_circuit_breaker_service

# === Replay ===
from selfhealing.services.replay_service import (
    ReplayService,
    ReplayRequest,
)

# structlog 초기화
try:
    from selfhealing.observability.structlog_config import configure_structlog
    configure_structlog()
except Exception:
    pass

__all__ = [
    "__version__",
    # Core Types
    "CircuitState",
    "FailedOperationData",
    # Service Access
    "ProviderRegistry",
    "get_circuit_breaker_service",
    # Replay
    "ReplayService",
    "ReplayRequest",
]
```

### 2.3 Consumer 마이그레이션

| 현재 | 변경 후 |
|------|---------|
| `from selfhealing.factory import ProviderRegistry` | `from selfhealing import ProviderRegistry` |
| `from selfhealing.services import get_circuit_breaker_service` | `from selfhealing import get_circuit_breaker_service` |
| `from selfhealing.services.replay_service import ReplayService` | `from selfhealing import ReplayService` |
| `from selfhealing.interfaces.repositories import FailedOperationData` | `from selfhealing import FailedOperationData` |

**하위 호환성**: 기존 깊은 경로 import도 계속 동작 (Python 모듈 시스템 표준 동작)

---

## 3. 향후 확장

추가 Public API 후보 (필요 시):

```python
# === Celery Integration ===
from selfhealing.celery_app import SELFHEALING_BEAT_SCHEDULE  # 321

# === Server Integration ===
from selfhealing.server import post_fork_reset  # 322

# === Configuration ===
from selfhealing.settings import get_config
```

---

## 4. 테스트 계획

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | `from selfhealing import ProviderRegistry` | import 성공 확인 |
| 2 | `from selfhealing import get_circuit_breaker_service` | import 성공 확인 |
| 3 | `from selfhealing import ReplayService, ReplayRequest` | import 성공 확인 |
| 4 | `from selfhealing import FailedOperationData` | import 성공 확인 |
| 5 | 기존 깊은 경로 import 하위 호환성 | `from selfhealing.factory import ProviderRegistry` 동작 확인 |
| 6 | `__all__` 완전성 | 모든 public API가 `__all__`에 포함 확인 |
