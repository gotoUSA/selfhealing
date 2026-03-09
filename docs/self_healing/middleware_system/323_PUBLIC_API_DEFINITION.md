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
    from selfhealing import SelfHealingError, AdapterNotFoundError
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "0.1.0"
__author__ = "SelfHealing Contributors"

# === Core Types (Eager — lightweight, no heavy deps) ===
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateEnum as CircuitState,
    FailedOperationData as FailedOperationData,
)

# =========================================================================
# Lazy Import (PEP 562) — Heavy 객체는 실제 접근 시점에 로드
#
# adapters/__init__.py 의 검증된 패턴을 동일하게 적용한다.
# Consumer가 CircuitState Enum만 필요한 경우에도 ProviderRegistry →
# structlog → settings 등의 연쇄 로딩이 발생하는 것을 방지한다.
# =========================================================================
if TYPE_CHECKING:
    from selfhealing.core.exceptions import (
        AdapterNotFoundError as AdapterNotFoundError,
        CircuitBreakerError as CircuitBreakerError,
        ConfigurationError as ConfigurationError,
        DLQReplayError as DLQReplayError,
        RetryExhaustedError as RetryExhaustedError,
        SelfHealingError as SelfHealingError,
    )
    from selfhealing.factory import ProviderRegistry as ProviderRegistry
    from selfhealing.services import (
        get_circuit_breaker_service as get_circuit_breaker_service,
    )
    from selfhealing.services.replay_service import (
        ReplayRequest as ReplayRequest,
        ReplayService as ReplayService,
    )

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Service Access
    "ProviderRegistry": ("selfhealing.factory", "ProviderRegistry"),
    "get_circuit_breaker_service": (
        "selfhealing.services",
        "get_circuit_breaker_service",
    ),
    # Replay
    "ReplayService": ("selfhealing.services.replay_service", "ReplayService"),
    "ReplayRequest": ("selfhealing.services.replay_service", "ReplayRequest"),
    # Exceptions
    "SelfHealingError": ("selfhealing.core.exceptions", "SelfHealingError"),
    "AdapterNotFoundError": ("selfhealing.core.exceptions", "AdapterNotFoundError"),
    "CircuitBreakerError": ("selfhealing.core.exceptions", "CircuitBreakerError"),
    "RetryExhaustedError": ("selfhealing.core.exceptions", "RetryExhaustedError"),
    "DLQReplayError": ("selfhealing.core.exceptions", "DLQReplayError"),
    "ConfigurationError": ("selfhealing.core.exceptions", "ConfigurationError"),
}


def __getattr__(name: str) -> object:
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        value = getattr(module, attr_name)
        globals()[name] = value  # 이후 접근 시 __getattr__ 우회
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    # Exceptions
    "SelfHealingError",
    "AdapterNotFoundError",
    "CircuitBreakerError",
    "RetryExhaustedError",
    "DLQReplayError",
    "ConfigurationError",
]
```

> **참고**: structlog 초기화(`configure_structlog()`)는 이전 설계안에서 `__init__.py`
> 하단에 위치했으나, 320 문서의 "명시적 래퍼로 지연" 합의에 따라 제거되었다.
> 이동 위치와 멱등성 보장에 대해서는 §2.4를 참조한다.

### 2.3 설계 결정 사항

#### 2.3.1 Import Side Effect 제거 — `configure_structlog()` 이동

**문제**: 기존 설계안은 `__init__.py` 하단에서 `configure_structlog()`를 호출했다.
Python에서 모듈 import 시 해당 코드가 실행되므로, Consumer가 단순 타입 힌팅을 위해
`from selfhealing import CircuitState`만 호출해도 전역 로깅 파이프라인이 변경되는
Side Effect가 발생한다.

**결정**: 320 문서의 ADR 합의("마법 배제, 명시적 래퍼로 지연")에 따라 `__init__.py`에서
structlog 초기화 코드를 완전히 제거한다. 로깅 초기화는 다음 두 경로 중 하나에서 수행한다:

- **Django 앱**: `configure_selfhealing()` 래퍼 내부 (`adapters/django/auto_config.py`)
- **Non-Django 앱**: `AppConfig.ready()` 또는 Consumer의 명시적 호출

`configure_structlog()` 자체에는 멱등성 플래그를 추가하여 중복 호출에 안전하게 만든다:

```python
# observability/structlog_config.py
_configured = False

def configure_structlog() -> None:
    global _configured
    if _configured:
        return
    # ... 기존 초기화 로직 ...
    _configured = True

def reset_structlog_config() -> None:
    """테스트에서 structlog 설정을 리셋한다."""
    global _configured
    _configured = False
```

이 `reset_*` 함수는 프로젝트의 settings 싱글턴 패턴(`get_*/reset_*`)과 일관된다.

#### 2.3.2 순환 참조(Circular Dependency) 방어

**문제**: `__init__.py`에서 하위 모듈 객체를 한곳으로 끌어올리면, 내부 모듈들이 서로를
import할 때 최상위 `selfhealing`을 참조하다 순환 참조(`ImportError`)가 발생할 수 있다.

**방어 기법 (3중 레이어)**:

| 레이어 | 기법 | 적용 위치 |
|--------|------|-----------|
| 1 | **Lazy Import (`__getattr__` + PEP 562)** | `__init__.py` — Heavy 객체를 실제 접근 시 로드하여 import chain 자체를 끊음 |
| 2 | **`TYPE_CHECKING` guard** | `factory.py`, `__init__.py` — 런타임에서 타입 전용 import 제거 |
| 3 | **`import-linter` forbidden contract** | CI — 내부 모듈이 최상위 `selfhealing`을 참조하는 것을 정적으로 차단 |

`import-linter` 규칙 (`.importlinter` 설정에 추가):

```ini
[importlinter:contract:no-top-level-self-import]
name = Internal modules must not import from top-level selfhealing
type = forbidden
source_modules =
    selfhealing.core
    selfhealing.services
    selfhealing.adapters
    selfhealing.interfaces
    selfhealing.settings
forbidden_modules = selfhealing
```

`pre-commit` hook에도 추가하여 커밋 전 로컬에서 감지한다.

#### 2.3.3 도메인 예외(Exception) Public API 노출

**문제**: Consumer가 `get_circuit_breaker_service()`나 `ReplayService`를 사용할 때
`try-except`를 작성하게 되는데, 핵심 예외가 Public API에 없으면 깊은 내부 경로
(`from selfhealing.core.exceptions import ...`)를 파고들어야 한다.

**결정**: `core/exceptions.py`의 15개 예외 중 Consumer가 `except`로 실제 분기할
가능성이 있는 6개를 선별하여 Public API에 노출한다:

| 예외 클래스 | 노출 사유 |
|---|---|
| `SelfHealingError` | Base — `except SelfHealingError`로 라이브러리 전체 에러 catch-all |
| `AdapterNotFoundError` | `ProviderRegistry` 사용 시 등록되지 않은 어댑터 접근 |
| `CircuitBreakerError` | Circuit Breaker 서비스 사용 시 상태 관련 에러 |
| `RetryExhaustedError` | 재시도 전략 사용 시 모든 시도 소진 |
| `DLQReplayError` | `ReplayService` 사용 시 재처리 실패 |
| `ConfigurationError` | 설정 오류 진단 |

**선별 기준**: Consumer가 해당 예외를 잡아서 비즈니스 로직을 분기할 현실적 필요가 있는가?
나머지 9개(`AdapterError`, `AdapterInitializationError`, `AdapterConnectionError`,
`CircuitBreakerTransitionError`, `DLQError`, `DLQEntryNotFoundError`, `ResilienceError`,
`RunbookError`, `SettingsValidationError`)는 상위 base 클래스로 catch 가능하므로
Public API에 노출하지 않는다.

#### 2.3.4 Lazy Import (PEP 562) 적용

**문제**: `ProviderRegistry`를 Eager import하면 내부적으로 `structlog`, `core.exceptions`
등이 연쇄 로드된다. Consumer가 `CircuitState` Enum 하나만 필요한 상황에서도 전체
의존성이 로드되는 오버헤드가 발생한다.

**결정**: `adapters/__init__.py`에서 이미 검증된 PEP 562 패턴을 `__init__.py`에도 적용한다.

- **Eager (경량, 의존성 없음)**: `CircuitState`, `FailedOperationData` — Enum/DTO
- **Lazy (`__getattr__`)**: `ProviderRegistry`, `get_circuit_breaker_service`,
  `ReplayService`, `ReplayRequest`, 예외 클래스 6개

3종 세트 패턴:

```
TYPE_CHECKING 블록  → IDE 자동완성 + 타입 체커 지원
_LAZY_IMPORTS dict  → 런타임 지연 로딩
__all__ 선언        → 공개 API 명세
```

`globals()[name] = value` 캐싱으로 최초 접근 이후에는 `__getattr__`를 우회한다.

#### 2.3.5 타입 체커 호환성 — PEP 561 + 명시적 재수출

**문제**: Mypy `--no-implicit-reexport`(strict 기본값) 또는 Pyright strict mode에서
`from .module import X` 형태는 내부 구현용 import로 간주되어 외부 Consumer 사용 시
경고가 발생할 수 있다.

**결정**: `.pyi` 스텁 파일 대신 `py.typed` + `TYPE_CHECKING` 내 `as X` 명시적 재수출
패턴을 채택한다.

**`.pyi` vs `TYPE_CHECKING` + `py.typed` 비교**:

| 기준 | `.pyi` 스텁 | `TYPE_CHECKING` + `py.typed` |
|------|------------|------------------------------|
| 동기화 안전성 | `.py`↔`.pyi` 2파일 동기화 필요 | 단일 파일 내 관리 |
| 프로젝트 전례 | 없음 (`.pyi` 0개) | `adapters/__init__.py`에서 검증 |
| IDE Go-to-Definition | `.pyi`로 이동 (우회 필요) | 실제 구현으로 직접 이동 |
| CI 부담 | `stubtest` 추가 필요 | 기존 mypy/ruff로 충분 |
| PEP 561 호환 | 즉시 | `py.typed` 마커 추가로 즉시 |
| Mypy/Pyright strict | 완벽 | `as X` 재수출로 완벽 |
| 업계 사례 | Pydantic (메타프로그래밍 특수) | Sentry, httpx, structlog, redis-py |

**PyPI 독립 배포 시 필요 작업**:

1. `selfhealing/py.typed` 빈 마커 파일 추가 (PEP 561)
2. `TYPE_CHECKING` 블록의 모든 import에 `as X` 명시적 재수출 구문 적용 (§2.2에 반영 완료)
3. 향후 `.pyi`가 필요해지면 `TYPE_CHECKING` 블록에서 자동 생성 가능 — 전환 비용 무시 수준

> **`.pyi` 도입 트리거 조건** (현재 해당 없음):
> - 런타임 동작과 타입 시그니처가 근본적으로 다른 메타프로그래밍 API 등장
> - `@overload`가 필요한 복잡한 시그니처 API 등장

### 2.4 Consumer 마이그레이션

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
| 5 | `from selfhealing import SelfHealingError, AdapterNotFoundError` | 예외 클래스 import 확인 |
| 6 | `from selfhealing import CircuitBreakerError, RetryExhaustedError` | 예외 클래스 import 확인 |
| 7 | `from selfhealing import DLQReplayError, ConfigurationError` | 예외 클래스 import 확인 |
| 8 | 기존 깊은 경로 import 하위 호환성 | `from selfhealing.factory import ProviderRegistry` 동작 확인 |
| 9 | `__all__` 완전성 | 모든 public API가 `__all__`에 포함 확인 |
| 10 | Lazy Import 검증 | `CircuitState`만 import 시 `factory`, `services` 모듈이 로드되지 않음 확인 |
| 11 | Side Effect 부재 확인 | `from selfhealing import CircuitState` 시 structlog 전역 설정 미변경 확인 |
| 12 | `py.typed` 마커 존재 | `selfhealing/py.typed` 파일 존재 및 wheel 배포 시 포함 확인 |
