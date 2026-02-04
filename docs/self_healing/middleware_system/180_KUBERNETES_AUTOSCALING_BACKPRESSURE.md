# 180. Kubernetes Auto-Scaling & Backpressure 통합 가이드

> **버전**: 1.0.0
> **작성일**: 2026-02-04
> **의존성**: [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md)
> **예상 소요**: 5-7일
> **예상 코드량**: ~1,200줄

---

## 0. 문서 목적

이 문서는 **Kubernetes Auto-Scaling과 Backpressure 통합** 구현 가이드입니다.

**핵심 목표**:
- "트래픽 폭증 시 자동으로 Pod 스케일 아웃"
- "과부하 시 Rate-aware Backpressure로 시스템 보호"

---

## 1. 현재 상황 분석 (코드 근거)

### 1.1 기존 Backpressure - RingBuffer 기반

**파일**: `packages/selfhealing-python/src/selfhealing/audit/ring_buffer.py`
**라인**: 90-120

```python
class RingBuffer:
    """
    Thread-safe Ring Buffer with backpressure.

    Backpressure 전략: DROP_OLDEST
    """

    def __init__(self, capacity: int = 10000):
        self._buffer = deque(maxlen=capacity)
```

**한계점**:
- DROP_OLDEST만 지원 (최신 데이터 우선)
- 외부 스케일링 트리거 없음
- Rate 인식 없음

### 1.2 기존 Load Shedding - Priority 기반

**파일**: `packages/selfhealing-python/src/selfhealing/services/cascade_load_shedding.py`
**라인**: 1-50

```python
class LoadSheddingConfig:
    """Load Shedding 설정."""

    priorities: list[Priority]
    thresholds: dict[str, float]
```

**한계점**:
- Priority 기반 Drop만 지원
- 동적 Rate 조절 없음
- HPA 연동 없음

### 1.3 누락된 기능

| 기능 | 현재 상태 | 필요 |
|------|----------|------|
| Kubernetes HPA | ❌ | 커스텀 메트릭 기반 스케일링 |
| Rate-aware Backpressure | ❌ | 동적 처리율 조절 |
| Backpressure 메트릭 노출 | ❌ | Prometheus 메트릭 |
| Graceful Degradation | 일부 | 단계별 기능 축소 |

---

## 2. 구현 목표

### 2.1 목표 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Kubernetes Cluster                                   │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    Prometheus + Prometheus Adapter                      │ │
│  │  ┌──────────────────────────────────────────────────────────────────┐  │ │
│  │  │  selfhealing_queue_depth{pod="*"}                                 │  │ │
│  │  │  selfhealing_processing_rate{pod="*"}                             │  │ │
│  │  │  selfhealing_backpressure_level{pod="*"}                          │  │ │
│  │  └──────────────────────────────────────────────────────────────────┘  │ │
│  └─────────────────────────────────────────────────────────────────┬──────┘ │
│                                                                    │        │
│  ┌────────────────────────────────────────────────────────────────┐│        │
│  │              HorizontalPodAutoscaler (HPA)                     ││        │
│  │  ┌──────────────────────────────────────────────────────────┐  ││        │
│  │  │  minReplicas: 2                                          │  ││        │
│  │  │  maxReplicas: 10                                         │  ││        │
│  │  │  metrics:                                                 │  ││        │
│  │  │    - type: Pods                                           │◄─┘│        │
│  │  │      pods:                                                │   │        │
│  │  │        metric: selfhealing_queue_depth                    │   │        │
│  │  │        target: averageValue: 100                          │   │        │
│  │  └──────────────────────────────────────────────────────────┘   │        │
│  └──────────────────────────────────────────────────────────────────┘        │
│                              │ Scale                                         │
│                              ▼                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         Deployment                                    │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐    │   │
│  │  │  Pod 1  │  │  Pod 2  │  │  Pod 3  │  │  Pod 4  │  │  Pod N  │    │   │
│  │  │┌───────┐│  │┌───────┐│  │┌───────┐│  │┌───────┐│  │┌───────┐│    │   │
│  │  ││Backpre││  ││Backpre││  ││Backpre││  ││Backpre││  ││Backpre││    │   │
│  │  ││ssure  ││  ││ssure  ││  ││ssure  ││  ││ssure  ││  ││ssure  ││    │   │
│  │  │└───────┘│  │└───────┘│  │└───────┘│  │└───────┘│  │└───────┘│    │   │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘  └─────────┘    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 핵심 컴포넌트

| 컴포넌트 | 역할 |
|----------|------|
| `BackpressureSettings` | Backpressure 설정 |
| `RateController` | 동적 처리율 조절 |
| `BackpressureMetrics` | Prometheus 메트릭 노출 |
| `HPAMetricsExporter` | 커스텀 메트릭 내보내기 |
| `GracefulDegradation` | 단계별 기능 축소 |

---

## 3. 상세 구현 명세

### 3.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/scaling/
├── __init__.py
├── config.py              # 설정
├── rate_controller.py     # Rate-aware Backpressure
├── metrics.py             # Prometheus 메트릭
├── graceful_degradation.py # 단계별 기능 축소
└── hpa_exporter.py        # HPA 메트릭 내보내기
```

### 3.2 설정 (config.py)

```python
# packages/selfhealing-python/src/selfhealing/scaling/config.py
"""
Auto-Scaling & Backpressure 설정.

기존 코드 참조:
- audit/ring_buffer.py: RingBuffer
- services/cascade_load_shedding.py: LoadShedding
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class BackpressureLevel(Enum):
    """Backpressure 레벨."""

    NONE = "none"           # 정상
    LOW = "low"             # 약간 과부하
    MEDIUM = "medium"       # 중간 과부하
    HIGH = "high"           # 높은 과부하
    CRITICAL = "critical"   # 위험 (긴급 조치)


class BackpressureStrategy(Enum):
    """Backpressure 전략."""

    DROP_OLDEST = "drop_oldest"     # 오래된 항목 삭제
    DROP_NEWEST = "drop_newest"     # 최신 항목 삭제
    REJECT = "reject"               # 거부 (HTTP 503)
    THROTTLE = "throttle"           # Rate Limit
    QUEUE = "queue"                 # 대기열에 추가


class ScalingSettings(BaseSettings):
    """
    Auto-Scaling & Backpressure 설정.

    환경변수:
    - SELFHEALING_SCALING_*
    """

    # Backpressure 활성화
    backpressure_enabled: bool = Field(
        default=True,
        description="Backpressure 활성화",
    )

    # 기본 전략
    default_strategy: BackpressureStrategy = Field(
        default=BackpressureStrategy.THROTTLE,
        description="기본 Backpressure 전략",
    )

    # 큐 임계치
    queue_low_threshold: int = Field(
        default=100,
        description="LOW 레벨 큐 크기",
    )
    queue_medium_threshold: int = Field(
        default=500,
        description="MEDIUM 레벨 큐 크기",
    )
    queue_high_threshold: int = Field(
        default=1000,
        description="HIGH 레벨 큐 크기",
    )
    queue_critical_threshold: int = Field(
        default=5000,
        description="CRITICAL 레벨 큐 크기",
    )

    # Rate Limit (처리/초)
    max_rate_per_second: float = Field(
        default=1000.0,
        description="최대 처리율 (항목/초)",
    )
    min_rate_per_second: float = Field(
        default=10.0,
        description="최소 처리율 (항목/초)",
    )

    # Rate 조절
    rate_decrease_factor: float = Field(
        default=0.8,
        description="Rate 감소 계수 (과부하 시)",
    )
    rate_increase_factor: float = Field(
        default=1.1,
        description="Rate 증가 계수 (정상화 시)",
    )
    rate_adjust_interval_seconds: float = Field(
        default=5.0,
        description="Rate 조절 주기 (초)",
    )

    # 메트릭 설정
    metrics_enabled: bool = Field(
        default=True,
        description="Prometheus 메트릭 활성화",
    )
    metrics_prefix: str = Field(
        default="selfhealing_",
        description="메트릭 이름 prefix",
    )

    # HPA 설정
    hpa_enabled: bool = Field(
        default=True,
        description="HPA 커스텀 메트릭 활성화",
    )
    hpa_target_queue_depth: int = Field(
        default=100,
        description="HPA 목표 큐 깊이",
    )

    # Graceful Degradation
    graceful_degradation_enabled: bool = Field(
        default=True,
        description="Graceful Degradation 활성화",
    )

    class Config:
        env_prefix = "SELFHEALING_SCALING_"
        env_file = ".env"

    def get_level_for_queue_size(self, queue_size: int) -> BackpressureLevel:
        """큐 크기에 따른 Backpressure 레벨 반환."""
        if queue_size >= self.queue_critical_threshold:
            return BackpressureLevel.CRITICAL
        elif queue_size >= self.queue_high_threshold:
            return BackpressureLevel.HIGH
        elif queue_size >= self.queue_medium_threshold:
            return BackpressureLevel.MEDIUM
        elif queue_size >= self.queue_low_threshold:
            return BackpressureLevel.LOW
        else:
            return BackpressureLevel.NONE


@lru_cache(maxsize=1)
def get_scaling_settings() -> ScalingSettings:
    """설정 싱글톤 반환."""
    return ScalingSettings()


def reset_scaling_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_scaling_settings.cache_clear()
```

### 3.3 Rate Controller (rate_controller.py)

```python
# packages/selfhealing-python/src/selfhealing/scaling/rate_controller.py
"""
Rate-aware Backpressure Controller.

동적으로 처리율을 조절하여 과부하 방지.

기존 코드 참조:
- audit/ring_buffer.py: RingBuffer
- services/cascade_load_shedding.py: LoadShedding
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from selfhealing.scaling.config import (
    BackpressureLevel,
    BackpressureStrategy,
    ScalingSettings,
    get_scaling_settings,
)

logger = logging.getLogger(__name__)


@dataclass
class RateControllerState:
    """Rate Controller 상태."""

    current_rate: float
    """현재 처리율 (항목/초)."""

    target_rate: float
    """목표 처리율."""

    level: BackpressureLevel
    """Backpressure 레벨."""

    queue_size: int
    """현재 큐 크기."""

    processed_count: int
    """처리된 항목 수."""

    dropped_count: int
    """버려진 항목 수."""


class TokenBucket:
    """
    Token Bucket 알고리즘.

    Rate Limit 구현에 사용.
    """

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
    ):
        """
        초기화.

        Args:
            rate: 초당 토큰 생성율
            capacity: 최대 토큰 수 (None이면 rate와 동일)
        """
        self._rate = rate
        self._capacity = capacity or rate
        self._tokens = self._capacity
        self._last_update = time.time()
        self._lock = threading.Lock()

    def set_rate(self, rate: float) -> None:
        """Rate 변경."""
        with self._lock:
            self._rate = rate

    def consume(self, tokens: int = 1) -> bool:
        """
        토큰 소비.

        Args:
            tokens: 소비할 토큰 수

        Returns:
            소비 성공 여부
        """
        with self._lock:
            now = time.time()
            elapsed = now - self._last_update
            self._last_update = now

            # 토큰 충전
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._rate,
            )

            # 소비
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def wait_for_token(self, timeout: float = 1.0) -> bool:
        """
        토큰 대기.

        Args:
            timeout: 최대 대기 시간

        Returns:
            토큰 획득 성공 여부
        """
        start = time.time()
        while time.time() - start < timeout:
            if self.consume():
                return True
            time.sleep(0.01)
        return False


class RateController:
    """
    Rate-aware Backpressure Controller.

    기능:
    - 큐 크기 기반 Backpressure 레벨 계산
    - 동적 Rate 조절
    - 전략 기반 처리 (Throttle, Drop, Reject)

    Usage:
        controller = RateController()
        controller.start()

        # 처리 전 확인
        if controller.should_process():
            process_item()
        else:
            # Backpressure 활성화됨
            pass

        controller.stop()
    """

    def __init__(
        self,
        settings: ScalingSettings | None = None,
        queue_size_provider: Callable[[], int] | None = None,
    ):
        """
        초기화.

        Args:
            settings: 설정
            queue_size_provider: 큐 크기 제공 함수
        """
        self._settings = settings or get_scaling_settings()
        self._queue_size_provider = queue_size_provider or (lambda: 0)

        self._lock = threading.RLock()
        self._current_rate = self._settings.max_rate_per_second
        self._level = BackpressureLevel.NONE
        self._token_bucket = TokenBucket(self._current_rate)

        # 통계
        self._processed_count = 0
        self._dropped_count = 0

        # 백그라운드 조절
        self._running = False
        self._worker: threading.Thread | None = None

    def get_state(self) -> RateControllerState:
        """현재 상태 반환."""
        with self._lock:
            return RateControllerState(
                current_rate=self._current_rate,
                target_rate=self._settings.max_rate_per_second,
                level=self._level,
                queue_size=self._queue_size_provider(),
                processed_count=self._processed_count,
                dropped_count=self._dropped_count,
            )

    def should_process(self) -> bool:
        """
        처리 여부 결정.

        Returns:
            True면 처리, False면 Backpressure
        """
        if not self._settings.backpressure_enabled:
            return True

        # Token Bucket 확인
        if self._token_bucket.consume():
            with self._lock:
                self._processed_count += 1
            return True

        # 전략에 따른 처리
        strategy = self._settings.default_strategy

        if strategy == BackpressureStrategy.REJECT:
            with self._lock:
                self._dropped_count += 1
            return False

        if strategy == BackpressureStrategy.THROTTLE:
            # 잠시 대기 후 재시도
            if self._token_bucket.wait_for_token(timeout=0.1):
                with self._lock:
                    self._processed_count += 1
                return True
            with self._lock:
                self._dropped_count += 1
            return False

        if strategy == BackpressureStrategy.DROP_OLDEST:
            # DROP_OLDEST는 호출자가 처리
            return True

        if strategy == BackpressureStrategy.QUEUE:
            # QUEUE는 호출자가 처리
            return True

        return True

    def _adjust_rate(self) -> None:
        """Rate 조절."""
        queue_size = self._queue_size_provider()
        new_level = self._settings.get_level_for_queue_size(queue_size)

        with self._lock:
            old_level = self._level
            self._level = new_level

        # 레벨에 따른 Rate 조절
        if new_level == BackpressureLevel.CRITICAL:
            new_rate = self._settings.min_rate_per_second
        elif new_level == BackpressureLevel.HIGH:
            new_rate = self._current_rate * self._settings.rate_decrease_factor
        elif new_level == BackpressureLevel.MEDIUM:
            new_rate = self._current_rate * self._settings.rate_decrease_factor
        elif new_level == BackpressureLevel.LOW:
            new_rate = self._current_rate  # 유지
        else:
            new_rate = self._current_rate * self._settings.rate_increase_factor

        # 범위 제한
        new_rate = max(
            self._settings.min_rate_per_second,
            min(self._settings.max_rate_per_second, new_rate),
        )

        with self._lock:
            if new_rate != self._current_rate:
                self._current_rate = new_rate
                self._token_bucket.set_rate(new_rate)
                logger.info(
                    f"[RateController] Rate adjusted: {new_rate:.1f}/s "
                    f"(level={new_level.value}, queue={queue_size})"
                )

    def _run_loop(self) -> None:
        """조절 루프."""
        while self._running:
            try:
                self._adjust_rate()
            except Exception as e:
                logger.error(f"[RateController] Adjust error: {e}")

            time.sleep(self._settings.rate_adjust_interval_seconds)

    def start(self) -> None:
        """시작."""
        if not self._settings.backpressure_enabled:
            logger.info("[RateController] Disabled")
            return

        if self._running:
            return

        self._running = True
        self._worker = threading.Thread(
            target=self._run_loop,
            name="RateController",
            daemon=True,
        )
        self._worker.start()
        logger.info("[RateController] Started")

    def stop(self) -> None:
        """중지."""
        self._running = False
        if self._worker:
            self._worker.join(timeout=5.0)
        logger.info("[RateController] Stopped")


# =============================================================================
# Singleton
# =============================================================================

_controller: RateController | None = None
_controller_lock = threading.Lock()


def get_rate_controller() -> RateController:
    """RateController 싱글톤 반환."""
    global _controller
    if _controller is None:
        with _controller_lock:
            if _controller is None:
                _controller = RateController()
    return _controller


def reset_rate_controller() -> None:
    """리셋 (테스트용)."""
    global _controller
    with _controller_lock:
        if _controller is not None:
            _controller.stop()
            _controller = None
```

### 3.4 Prometheus Metrics (metrics.py)

```python
# packages/selfhealing-python/src/selfhealing/scaling/metrics.py
"""
Prometheus 메트릭 노출.

기존 코드 참조:
- audit/metrics/registry.py: MetricsRegistry
- audit/metrics/recorders.py: Counter, Gauge
"""

from __future__ import annotations

import logging
from typing import Any

from selfhealing.scaling.config import ScalingSettings, get_scaling_settings

logger = logging.getLogger(__name__)

# prometheus_client가 있으면 사용
try:
    from prometheus_client import Counter, Gauge, Histogram, Info
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False


class BackpressureMetrics:
    """
    Backpressure Prometheus 메트릭.

    메트릭:
    - selfhealing_queue_depth: 현재 큐 깊이
    - selfhealing_processing_rate: 처리율 (항목/초)
    - selfhealing_backpressure_level: Backpressure 레벨
    - selfhealing_processed_total: 처리된 총 항목 수
    - selfhealing_dropped_total: 버려진 총 항목 수
    """

    def __init__(
        self,
        settings: ScalingSettings | None = None,
    ):
        """초기화."""
        self._settings = settings or get_scaling_settings()
        self._prefix = self._settings.metrics_prefix

        if not HAS_PROMETHEUS:
            logger.warning("[BackpressureMetrics] prometheus_client not installed")
            return

        if not self._settings.metrics_enabled:
            return

        # Gauge: 현재 큐 깊이
        self.queue_depth = Gauge(
            f"{self._prefix}queue_depth",
            "Current queue depth",
            ["queue_name"],
        )

        # Gauge: 처리율
        self.processing_rate = Gauge(
            f"{self._prefix}processing_rate",
            "Current processing rate (items/second)",
            ["component"],
        )

        # Gauge: Backpressure 레벨 (0=NONE, 1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL)
        self.backpressure_level = Gauge(
            f"{self._prefix}backpressure_level",
            "Current backpressure level",
            ["component"],
        )

        # Counter: 처리된 항목
        self.processed_total = Counter(
            f"{self._prefix}processed_total",
            "Total processed items",
            ["component", "status"],
        )

        # Counter: 버려진 항목
        self.dropped_total = Counter(
            f"{self._prefix}dropped_total",
            "Total dropped items",
            ["component", "reason"],
        )

        # Histogram: 처리 시간
        self.processing_duration = Histogram(
            f"{self._prefix}processing_duration_seconds",
            "Processing duration in seconds",
            ["component", "operation"],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
        )

    def set_queue_depth(self, queue_name: str, depth: int) -> None:
        """큐 깊이 설정."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.queue_depth.labels(queue_name=queue_name).set(depth)

    def set_processing_rate(self, component: str, rate: float) -> None:
        """처리율 설정."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.processing_rate.labels(component=component).set(rate)

    def set_backpressure_level(self, component: str, level: int) -> None:
        """Backpressure 레벨 설정."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.backpressure_level.labels(component=component).set(level)

    def inc_processed(self, component: str, status: str = "success") -> None:
        """처리 카운터 증가."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.processed_total.labels(component=component, status=status).inc()

    def inc_dropped(self, component: str, reason: str = "backpressure") -> None:
        """드롭 카운터 증가."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.dropped_total.labels(component=component, reason=reason).inc()

    def observe_duration(
        self,
        component: str,
        operation: str,
        duration: float,
    ) -> None:
        """처리 시간 기록."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.processing_duration.labels(
                component=component,
                operation=operation,
            ).observe(duration)


# 싱글톤
_metrics: BackpressureMetrics | None = None


def get_backpressure_metrics() -> BackpressureMetrics:
    """BackpressureMetrics 싱글톤 반환."""
    global _metrics
    if _metrics is None:
        _metrics = BackpressureMetrics()
    return _metrics
```

### 3.5 Graceful Degradation (graceful_degradation.py)

```python
# packages/selfhealing-python/src/selfhealing/scaling/graceful_degradation.py
"""
Graceful Degradation - 단계별 기능 축소.

과부하 시 비필수 기능부터 순차적으로 비활성화.

기존 코드 참조:
- audit/graceful_degradation/fallback.py: Fallback Chain
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from selfhealing.scaling.config import (
    BackpressureLevel,
    ScalingSettings,
    get_scaling_settings,
)

logger = logging.getLogger(__name__)


class FeaturePriority(Enum):
    """기능 우선순위."""

    CRITICAL = 0    # 항상 유지 (핵심 Self-Healing)
    HIGH = 1        # 높음 (DLQ 처리)
    MEDIUM = 2      # 중간 (알림)
    LOW = 3         # 낮음 (로깅, 통계)
    OPTIONAL = 4    # 선택 (디버그, 추적)


@dataclass
class Feature:
    """기능 정의."""

    name: str
    """기능 이름."""

    priority: FeaturePriority
    """우선순위."""

    enabled: bool = True
    """현재 활성화 상태."""

    on_disable: Callable[[], None] | None = None
    """비활성화 시 콜백."""

    on_enable: Callable[[], None] | None = None
    """활성화 시 콜백."""


class GracefulDegradation:
    """
    Graceful Degradation Manager.

    Backpressure 레벨에 따라 기능 활성화/비활성화.

    레벨별 동작:
    - NONE: 모든 기능 활성화
    - LOW: OPTIONAL 비활성화
    - MEDIUM: LOW 이하 비활성화
    - HIGH: MEDIUM 이하 비활성화
    - CRITICAL: CRITICAL만 유지

    Usage:
        degradation = GracefulDegradation()

        # 기능 등록
        degradation.register_feature(Feature(
            name="detailed_logging",
            priority=FeaturePriority.OPTIONAL,
        ))

        # 레벨 업데이트
        degradation.update_level(BackpressureLevel.HIGH)

        # 기능 사용 가능 여부 확인
        if degradation.is_enabled("detailed_logging"):
            log_details()
    """

    # 레벨별 활성화 우선순위 임계치
    LEVEL_THRESHOLDS = {
        BackpressureLevel.NONE: FeaturePriority.OPTIONAL,
        BackpressureLevel.LOW: FeaturePriority.LOW,
        BackpressureLevel.MEDIUM: FeaturePriority.MEDIUM,
        BackpressureLevel.HIGH: FeaturePriority.HIGH,
        BackpressureLevel.CRITICAL: FeaturePriority.CRITICAL,
    }

    def __init__(
        self,
        settings: ScalingSettings | None = None,
    ):
        """초기화."""
        self._settings = settings or get_scaling_settings()
        self._features: dict[str, Feature] = {}
        self._current_level = BackpressureLevel.NONE

    def register_feature(self, feature: Feature) -> None:
        """기능 등록."""
        self._features[feature.name] = feature
        logger.debug(f"[GracefulDegradation] Registered: {feature.name}")

    def unregister_feature(self, name: str) -> None:
        """기능 등록 해제."""
        if name in self._features:
            del self._features[name]

    def is_enabled(self, name: str) -> bool:
        """기능 활성화 여부 확인."""
        if not self._settings.graceful_degradation_enabled:
            return True

        feature = self._features.get(name)
        if feature is None:
            return True

        return feature.enabled

    def update_level(self, level: BackpressureLevel) -> None:
        """
        Backpressure 레벨 업데이트.

        레벨에 따라 기능 활성화/비활성화.
        """
        if not self._settings.graceful_degradation_enabled:
            return

        if level == self._current_level:
            return

        old_level = self._current_level
        self._current_level = level

        threshold = self.LEVEL_THRESHOLDS.get(level, FeaturePriority.OPTIONAL)

        for feature in self._features.values():
            should_enable = feature.priority.value <= threshold.value

            if should_enable and not feature.enabled:
                feature.enabled = True
                if feature.on_enable:
                    try:
                        feature.on_enable()
                    except Exception as e:
                        logger.error(f"[GracefulDegradation] on_enable error: {e}")
                logger.info(f"[GracefulDegradation] Enabled: {feature.name}")

            elif not should_enable and feature.enabled:
                feature.enabled = False
                if feature.on_disable:
                    try:
                        feature.on_disable()
                    except Exception as e:
                        logger.error(f"[GracefulDegradation] on_disable error: {e}")
                logger.info(f"[GracefulDegradation] Disabled: {feature.name}")

        logger.info(
            f"[GracefulDegradation] Level changed: {old_level.value} → {level.value}"
        )

    def get_enabled_features(self) -> list[str]:
        """활성화된 기능 목록 반환."""
        return [
            name for name, feature in self._features.items()
            if feature.enabled
        ]

    def get_disabled_features(self) -> list[str]:
        """비활성화된 기능 목록 반환."""
        return [
            name for name, feature in self._features.items()
            if not feature.enabled
        ]


# 싱글톤
_degradation: GracefulDegradation | None = None


def get_graceful_degradation() -> GracefulDegradation:
    """GracefulDegradation 싱글톤 반환."""
    global _degradation
    if _degradation is None:
        _degradation = GracefulDegradation()
    return _degradation
```

---

## 4. Kubernetes HPA 설정

### 4.1 Prometheus Adapter 설정

```yaml
# k8s/prometheus-adapter-config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-adapter-config
  namespace: monitoring
data:
  config.yaml: |
    rules:
      - seriesQuery: 'selfhealing_queue_depth{namespace!="",pod!=""}'
        resources:
          overrides:
            namespace:
              resource: namespace
            pod:
              resource: pod
        name:
          matches: "^(.*)$"
          as: "selfhealing_queue_depth"
        metricsQuery: 'sum(<<.Series>>{<<.LabelMatchers>>}) by (<<.GroupBy>>)'

      - seriesQuery: 'selfhealing_processing_rate{namespace!="",pod!=""}'
        resources:
          overrides:
            namespace:
              resource: namespace
            pod:
              resource: pod
        name:
          matches: "^(.*)$"
          as: "selfhealing_processing_rate"
        metricsQuery: 'avg(<<.Series>>{<<.LabelMatchers>>}) by (<<.GroupBy>>)'

      - seriesQuery: 'selfhealing_backpressure_level{namespace!="",pod!=""}'
        resources:
          overrides:
            namespace:
              resource: namespace
            pod:
              resource: pod
        name:
          matches: "^(.*)$"
          as: "selfhealing_backpressure_level"
        metricsQuery: 'max(<<.Series>>{<<.LabelMatchers>>}) by (<<.GroupBy>>)'
```

### 4.2 HorizontalPodAutoscaler

```yaml
# k8s/selfhealing-hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: selfhealing-worker-hpa
  namespace: selfhealing
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: selfhealing-worker

  minReplicas: 2
  maxReplicas: 20

  metrics:
    # 1. 큐 깊이 기반 (주요 지표)
    - type: Pods
      pods:
        metric:
          name: selfhealing_queue_depth
        target:
          type: AverageValue
          averageValue: "100"

    # 2. CPU 기반 (보조 지표)
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70

    # 3. 메모리 기반 (보조 지표)
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80

  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300  # 5분 안정화
      policies:
        - type: Percent
          value: 10
          periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 0  # 즉시 스케일 업
      policies:
        - type: Percent
          value: 100
          periodSeconds: 15
        - type: Pods
          value: 4
          periodSeconds: 15
      selectPolicy: Max
```

### 4.3 ServiceMonitor (Prometheus)

```yaml
# k8s/selfhealing-servicemonitor.yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: selfhealing
  namespace: selfhealing
  labels:
    app: selfhealing
spec:
  selector:
    matchLabels:
      app: selfhealing
  endpoints:
    - port: metrics
      path: /metrics
      interval: 15s
      scrapeTimeout: 10s
```

---

## 5. 사용 예시

### 5.1 Django 미들웨어 통합

```python
# selfhealing/middleware/backpressure.py

from django.http import HttpResponse

from selfhealing.scaling.rate_controller import get_rate_controller


class BackpressureMiddleware:
    """Backpressure 미들웨어."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._controller = get_rate_controller()

    def __call__(self, request):
        # Backpressure 확인
        if not self._controller.should_process():
            return HttpResponse(
                "Service temporarily unavailable due to high load",
                status=503,
                headers={"Retry-After": "5"},
            )

        return self.get_response(request)
```

### 5.2 Celery Worker 통합

```python
# tasks/base.py

from celery import Task

from selfhealing.scaling.rate_controller import get_rate_controller
from selfhealing.scaling.graceful_degradation import (
    Feature,
    FeaturePriority,
    get_graceful_degradation,
)


class SelfHealingTask(Task):
    """Self-Healing Celery Task 기본 클래스."""

    def __init__(self):
        super().__init__()
        self._controller = get_rate_controller()
        self._degradation = get_graceful_degradation()

        # 기능 등록
        self._degradation.register_feature(Feature(
            name="detailed_logging",
            priority=FeaturePriority.OPTIONAL,
        ))

    def __call__(self, *args, **kwargs):
        # Backpressure 확인
        if not self._controller.should_process():
            # 재시도 스케줄
            self.retry(countdown=5, max_retries=3)

        return super().__call__(*args, **kwargs)
```

---

## 6. 설정 예시

```bash
# .env

# Backpressure 설정
SELFHEALING_SCALING_BACKPRESSURE_ENABLED=true
SELFHEALING_SCALING_DEFAULT_STRATEGY=throttle

# 큐 임계치
SELFHEALING_SCALING_QUEUE_LOW_THRESHOLD=100
SELFHEALING_SCALING_QUEUE_MEDIUM_THRESHOLD=500
SELFHEALING_SCALING_QUEUE_HIGH_THRESHOLD=1000
SELFHEALING_SCALING_QUEUE_CRITICAL_THRESHOLD=5000

# Rate Limit
SELFHEALING_SCALING_MAX_RATE_PER_SECOND=1000
SELFHEALING_SCALING_MIN_RATE_PER_SECOND=10

# Metrics
SELFHEALING_SCALING_METRICS_ENABLED=true
SELFHEALING_SCALING_METRICS_PREFIX=selfhealing_

# HPA
SELFHEALING_SCALING_HPA_ENABLED=true
SELFHEALING_SCALING_HPA_TARGET_QUEUE_DEPTH=100

# Graceful Degradation
SELFHEALING_SCALING_GRACEFUL_DEGRADATION_ENABLED=true
```

---

## 7. 구현 체크리스트

- [ ] `scaling/__init__.py` 생성
- [ ] `scaling/config.py` 구현
- [ ] `scaling/rate_controller.py` 구현
- [ ] `scaling/metrics.py` 구현
- [ ] `scaling/graceful_degradation.py` 구현
- [ ] `scaling/hpa_exporter.py` 구현
- [ ] Django 미들웨어 통합
- [ ] Celery Worker 통합
- [ ] Kubernetes HPA 설정
- [ ] Prometheus Adapter 설정
- [ ] 단위 테스트 작성
- [ ] 부하 테스트 수행

---

## 8. 관련 문서

- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜
- [ring_buffer.py](../../packages/selfhealing-python/src/selfhealing/audit/ring_buffer.py) - 기존 RingBuffer
- [cascade_load_shedding.py](../../packages/selfhealing-python/src/selfhealing/services/cascade_load_shedding.py) - 기존 Load Shedding

---

## 9. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
