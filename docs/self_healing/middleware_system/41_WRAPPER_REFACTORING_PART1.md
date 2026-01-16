# 41. Wrapper 리팩토링 PART 1: SafeGauge 모듈화

> **작성일**: 2026-01-16  
> **대상 파일**: `packages/selfhealing-python/src/selfhealing/metrics/safe_gauge.py`  
> **현재 줄 수**: 565줄  
> **우선순위**: 높음

---

## 1. 현재 코드 분석

### 1.1 파일 구조 (`safe_gauge.py` 565줄)

```python
# Lines 1-28: 모듈 docstring
"""
Safe Gauge Wrapper for Prometheus.
Prevents negative gauge values that can occur after server restarts.
...
"""

# Lines 29-45: Imports
from __future__ import annotations
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, TYPE_CHECKING

# Lines 47-56: SyncStatus Enum
class SyncStatus(Enum):
    """메트릭 동기화 상태."""
    SYNCED = "synced"
    STALE = "stale"
    UNKNOWN = "unknown"
    RECOVERING = "recovering"

# Lines 58-145: SyncInfo Dataclass (87줄)
@dataclass
class SyncInfo:
    """메트릭 동기화 정보."""
    status: SyncStatus = SyncStatus.UNKNOWN
    last_sync_time: Optional[float] = None
    last_sync_source: str = "none"
    staleness_threshold: float = 300.0
    stabilization_start: Optional[float] = None
    stabilization_duration: float = 60.0
    
    @property
    def age_seconds(self) -> Optional[float]: ...
    @property
    def is_synced(self) -> bool: ...
    @property
    def is_recovering(self) -> bool: ...
    @property
    def recovery_progress(self) -> float: ...
    def mark_synced(self, source: str = "push") -> None: ...
    def mark_stale(self, reason: str = "timeout") -> None: ...
    def check_staleness(self) -> bool: ...

# Lines 147-360: SafeGaugeChild 클래스 (213줄)
class SafeGaugeChild:
    """Safe wrapper for labeled Gauge child."""
    def __init__(self, gauge_child, label_values, staleness_threshold, stabilization_duration): ...
    @property
    def sync_info(self) -> SyncInfo: ...
    @property
    def is_synced(self) -> bool: ...
    @property
    def is_recovering(self) -> bool: ...
    @property
    def last_sync_time(self) -> Optional[float]: ...
    @property
    def sync_age_seconds(self) -> Optional[float]: ...
    def inc(self, amount: float = 1) -> None: ...
    def dec(self, amount: float = 1) -> None: ...
    def set(self, value: float, source: str = "manual") -> None: ...
    def get_shadow_value(self) -> float: ...
    def sync_from_source(self, actual_value: float, source: str = "reconciler") -> None: ...
    def mark_stale(self, reason: str = "external") -> None: ...
    def get_reliability_info(self) -> Dict[str, Any]: ...

# Lines 362-435: SafeGauge 클래스 (73줄)
class SafeGauge:
    """Safe wrapper for Prometheus Gauge."""
    def __init__(self, gauge: Optional["Gauge"]): ...
    def labels(self, **kwargs) -> SafeGaugeChild: ...
    def get_child(self, **kwargs) -> Optional[SafeGaugeChild]: ...
    @property
    def is_available(self) -> bool: ...

# Lines 437-470: _NoOpGaugeChild 클래스 (33줄)
class _NoOpGaugeChild:
    """No-op implementation for when gauge is not available."""
    def inc(self, amount: float = 1) -> None: pass
    def dec(self, amount: float = 1) -> None: pass
    def set(self, value: float) -> None: pass
    def get_shadow_value(self) -> float: return 0.0
    def sync_from_source(self, actual_value: float) -> None: pass

# Lines 472-565: 유틸리티 함수들 (93줄)
def clamp_non_negative(value: float, metric_name: str = "unknown") -> float: ...
def clamp_percentage(value: float, metric_name: str = "unknown") -> float: ...
def safe_set_gauge(gauge, value, clamp_type, metric_name, **labels) -> None: ...
```

### 1.2 책임 분석

| 구성 요소 | 줄 수 | 책임 |
|---|---|---|
| `SyncStatus` | 10 | 동기화 상태 Enum |
| `SyncInfo` | 87 | 동기화 정보 + 상태 전이 로직 |
| `SafeGaugeChild` | 213 | 라벨된 Gauge 래핑 + 음수 방지 + 동기화 추적 |
| `SafeGauge` | 73 | 메인 래퍼 + Child 관리 |
| `_NoOpGaugeChild` | 33 | Gauge 없을 때 No-op |
| 유틸리티 함수들 | 93 | clamping 헬퍼 |

**문제점**: `SafeGaugeChild`가 **3가지 책임**을 가짐

---

## 2. 리팩토링 목표

### 2.1 목표 구조

```
metrics/
├── safe_gauge/
│   ├── __init__.py          # Public API (re-export)
│   ├── core.py               # SafeGauge, SafeGaugeChild (핵심)
│   ├── sync.py               # SyncStatus, SyncInfo (동기화)
│   ├── clamping.py           # clamp_non_negative, clamp_percentage, safe_set_gauge
│   └── noop.py               # _NoOpGaugeChild
└── safe_gauge.py             # (deprecated) 하위 호환성용 re-export
```

### 2.2 모듈별 줄 수 목표

| 모듈 | 예상 줄 수 | 내용 |
|---|---|---|
| `__init__.py` | ~30 | Public API |
| `core.py` | ~150 | SafeGauge, SafeGaugeChild (핵심 로직만) |
| `sync.py` | ~100 | SyncStatus, SyncInfo |
| `clamping.py` | ~60 | 유틸리티 함수들 |
| `noop.py` | ~40 | No-op 구현 |

---

## 3. 상세 리팩토링 계획

### 3.1 Step 1: `sync.py` 분리

**분리할 코드** (현재 위치: Lines 47-145):

```python
# metrics/safe_gauge/sync.py

"""
Sync Status Tracking for SafeGauge.

Provides synchronization state management for metric reliability.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SyncStatus(Enum):
    """메트릭 동기화 상태."""
    SYNCED = "synced"           # 정상 동기화됨
    STALE = "stale"             # 동기화 지연
    UNKNOWN = "unknown"         # 초기 상태
    RECOVERING = "recovering"   # 복구 중


@dataclass
class SyncInfo:
    """메트릭 동기화 정보."""
    status: SyncStatus = SyncStatus.UNKNOWN
    last_sync_time: Optional[float] = None
    last_sync_source: str = "none"
    staleness_threshold: float = 300.0  # 5분
    stabilization_start: Optional[float] = None
    stabilization_duration: float = 60.0  # 1분
    
    # ... 현재 코드의 모든 property와 method 유지
```

### 3.2 Step 2: `clamping.py` 분리

**분리할 코드** (현재 위치: Lines 472-565):

```python
# metrics/safe_gauge/clamping.py

"""
Value Clamping Utilities for Metrics.

Prevents invalid metric values (negative counts, out-of-range percentages).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def clamp_non_negative(value: float, metric_name: str = "unknown") -> float:
    """값을 0 이상으로 클램핑."""
    if value < 0:
        logger.warning(
            f"[SafeGauge] Clamping negative value {value} to 0 "
            f"for metric '{metric_name}'"
        )
        return 0.0
    return float(value)


def clamp_percentage(value: float, metric_name: str = "unknown") -> float:
    """값을 0-100 범위로 클램핑."""
    if value < 0:
        logger.warning(
            f"[SafeGauge] Clamping negative percentage {value} to 0 "
            f"for metric '{metric_name}'"
        )
        return 0.0
    if value > 100:
        logger.warning(
            f"[SafeGauge] Clamping percentage {value} to 100 "
            f"for metric '{metric_name}'"
        )
        return 100.0
    return float(value)


def safe_set_gauge(
    gauge: Any,
    value: float,
    clamp_type: str = "non_negative",
    metric_name: str = "unknown",
    **labels,
) -> None:
    """게이지 값을 안전하게 설정."""
    # ... 현재 코드 유지
```

### 3.3 Step 3: `noop.py` 분리

**분리할 코드** (현재 위치: Lines 437-470):

```python
# metrics/safe_gauge/noop.py

"""
No-op Gauge Implementation.

Used when Prometheus gauge is not available (e.g., metrics disabled).
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class NoOpGaugeChild:
    """No-op implementation for when gauge is not available."""

    def inc(self, amount: float = 1) -> None:
        """No-op increment."""
        pass

    def dec(self, amount: float = 1) -> None:
        """No-op decrement."""
        pass

    def set(self, value: float) -> None:
        """No-op set."""
        pass

    def get_shadow_value(self) -> float:
        """Return 0 for no-op."""
        return 0.0

    def sync_from_source(self, actual_value: float, source: str = "noop") -> None:
        """No-op sync."""
        pass
    
    @property
    def is_synced(self) -> bool:
        """Always False for no-op."""
        return False
    
    def get_reliability_info(self) -> Dict[str, Any]:
        """Return empty reliability info."""
        return {"is_synced": False, "status": "noop"}
```

### 3.4 Step 4: `core.py` (핵심 로직만)

```python
# metrics/safe_gauge/core.py

"""
Core SafeGauge Implementation.

Thread-safe gauge wrapper that prevents negative values.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, TYPE_CHECKING

from .sync import SyncInfo, SyncStatus
from .noop import NoOpGaugeChild
from .clamping import clamp_non_negative

if TYPE_CHECKING:
    from prometheus_client import Gauge

logger = logging.getLogger(__name__)


class SafeGaugeChild:
    """
    Safe wrapper for labeled Gauge child.
    
    핵심 책임:
    - 음수 값 방지 (shadow counter + clamping)
    - Thread-safe 연산
    - 동기화 상태 추적 (SyncInfo에 위임)
    """

    def __init__(
        self, 
        gauge_child: Any, 
        label_values: Dict[str, str],
        staleness_threshold: float = 300.0,
        stabilization_duration: float = 60.0,
    ):
        self._gauge_child = gauge_child
        self._label_values = label_values
        self._lock = threading.Lock()
        self._shadow_value: float = 0.0
        self._initialized = False
        self._sync_info = SyncInfo(
            staleness_threshold=staleness_threshold,
            stabilization_duration=stabilization_duration,
        )

    # ... inc, dec, set, get_shadow_value 메서드들 (현재 코드 유지)
    # ... sync_from_source, mark_stale, get_reliability_info (현재 코드 유지)


class SafeGauge:
    """
    Safe wrapper for Prometheus Gauge.
    
    핵심 책임:
    - SafeGaugeChild 인스턴스 관리
    - Label 기반 child 생성 및 캐싱
    """

    def __init__(self, gauge: Optional["Gauge"]):
        self._gauge = gauge
        self._children: Dict[tuple, SafeGaugeChild] = {}
        self._lock = threading.Lock()

    def labels(self, **kwargs) -> SafeGaugeChild:
        if self._gauge is None:
            return NoOpGaugeChild()
        # ... 현재 코드 유지

    def get_child(self, **kwargs) -> Optional[SafeGaugeChild]:
        # ... 현재 코드 유지

    @property
    def is_available(self) -> bool:
        return self._gauge is not None
```

### 3.5 Step 5: `__init__.py` (Public API)

```python
# metrics/safe_gauge/__init__.py

"""
SafeGauge - Thread-safe Prometheus Gauge Wrapper.

Prevents negative gauge values after server restarts.

Usage:
    >>> from selfhealing.metrics.safe_gauge import SafeGauge
    >>> from prometheus_client import Gauge
    >>> 
    >>> raw = Gauge("dlq_pending", "Pending DLQ items", ["domain"])
    >>> safe = SafeGauge(raw)
    >>> safe.labels(domain="payment").inc()
    >>> safe.labels(domain="payment").dec()  # Won't go below 0
"""

from .core import SafeGauge, SafeGaugeChild
from .sync import SyncStatus, SyncInfo
from .clamping import clamp_non_negative, clamp_percentage, safe_set_gauge
from .noop import NoOpGaugeChild

__all__ = [
    # Core
    "SafeGauge",
    "SafeGaugeChild",
    # Sync
    "SyncStatus",
    "SyncInfo",
    # Clamping
    "clamp_non_negative",
    "clamp_percentage",
    "safe_set_gauge",
    # NoOp
    "NoOpGaugeChild",
]
```

### 3.6 Step 6: 하위 호환성 유지

```python
# metrics/safe_gauge.py (기존 파일 - deprecated)

"""
DEPRECATED: Import from selfhealing.metrics.safe_gauge package instead.

This file is kept for backward compatibility.
"""

import warnings

warnings.warn(
    "Importing from selfhealing.metrics.safe_gauge module is deprecated. "
    "Use selfhealing.metrics.safe_gauge package instead.",
    DeprecationWarning,
    stacklevel=2,
)

from selfhealing.metrics.safe_gauge import (
    SafeGauge,
    SafeGaugeChild,
    SyncStatus,
    SyncInfo,
    clamp_non_negative,
    clamp_percentage,
    safe_set_gauge,
)

__all__ = [
    "SafeGauge",
    "SafeGaugeChild", 
    "SyncStatus",
    "SyncInfo",
    "clamp_non_negative",
    "clamp_percentage",
    "safe_set_gauge",
]
```

---

## 4. 테스트 계획

### 4.1 단위 테스트 구조

```
tests/unit/metrics/safe_gauge/
├── test_core.py          # SafeGauge, SafeGaugeChild 테스트
├── test_sync.py          # SyncStatus, SyncInfo 테스트
├── test_clamping.py      # 유틸리티 함수 테스트
└── test_noop.py          # NoOpGaugeChild 테스트
```

### 4.2 테스트 케이스

**test_sync.py**:
```python
def test_sync_info_mark_synced():
    """mark_synced 호출 시 상태 전이 확인."""
    info = SyncInfo()
    assert info.status == SyncStatus.UNKNOWN
    
    info.mark_synced("push")
    assert info.status == SyncStatus.RECOVERING  # UNKNOWN → RECOVERING
    
def test_sync_info_staleness_detection():
    """staleness_threshold 초과 시 stale 감지."""
    info = SyncInfo(staleness_threshold=0.1)
    info.mark_synced("push")
    time.sleep(0.2)
    
    assert info.check_staleness() is True
    assert info.status == SyncStatus.STALE
```

**test_clamping.py**:
```python
def test_clamp_non_negative():
    """음수 값 클램핑 확인."""
    assert clamp_non_negative(-5) == 0.0
    assert clamp_non_negative(10) == 10.0
    
def test_clamp_percentage():
    """백분율 범위 클램핑 확인."""
    assert clamp_percentage(-10) == 0.0
    assert clamp_percentage(150) == 100.0
    assert clamp_percentage(50) == 50.0
```

---

## 5. 마이그레이션 가이드

### 5.1 기존 코드 (변경 불필요)

```python
# 기존 import 유지 (하위 호환성)
from selfhealing.metrics.safe_gauge import SafeGauge

# DeprecationWarning 발생하지만 동작함
```

### 5.2 권장 코드 (새 프로젝트)

```python
# 패키지에서 직접 import
from selfhealing.metrics.safe_gauge import (
    SafeGauge,
    SyncStatus,
    clamp_non_negative,
)
```

### 5.3 동기화 로직만 사용

```python
# 동기화 로직만 필요한 경우
from selfhealing.metrics.safe_gauge.sync import SyncInfo, SyncStatus

info = SyncInfo(staleness_threshold=60.0)
info.mark_synced("hydration")
print(f"Is synced: {info.is_synced}")
```

---

## 6. 검증 체크리스트

- [ ] 모든 기존 테스트 통과
- [ ] 새 모듈별 단위 테스트 추가
- [ ] 기존 import 경로 하위 호환성 확인
- [ ] DeprecationWarning 발생 확인
- [ ] 메트릭 수집 정상 동작 확인
- [ ] 문서 업데이트

---

## 7. 예상 효과

| 지표 | Before | After |
|---|---|---|
| 단일 파일 줄 수 | 565 | 최대 150 (core.py) |
| 테스트 대상 분리 | 1 파일 | 4 파일 |
| 동기화 로직 독립 수정 | 불가 | 가능 (sync.py만) |
| 유틸리티 재사용 | 어려움 | 쉬움 (clamping.py import) |
