# 180. Kubernetes Auto-Scaling & Backpressure 통합 가이드

> **버전**: 1.1.0
> **작성일**: 2026-02-04
> **최종 수정**: 2026-02-05
> **의존성**: [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md)
> **예상 소요**: 5-7일
> **예상 코드량**: ~1,500줄

---

## 0. 문서 목적

이 문서는 **Kubernetes Auto-Scaling과 Backpressure 통합** 구현 가이드입니다.

**핵심 목표**:
- "트래픽 폭증 시 자동으로 Pod 스케일 아웃"
- "과부하 시 Rate-aware Backpressure로 시스템 보호"
- "HPA 스케일업 완료 전 Backpressure 보호 구간 운영"
- "기존 CascadeLoadShedding과의 통합 파이프라인 구축"

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
| `BackpressureSettings` | Backpressure 설정 (기존 ScaleSettings와 분리) |
| `RateController` | 동적 처리율 조절 (Token Bucket) |
| `TrafficGate` | RateController + LoadShedding 통합 파이프라인 |
| `BackpressureMetrics` | Prometheus 메트릭 노출 |
| `HPAMetricsExporter` | 커스텀 메트릭 내보내기 |
| `GracefulDegradation` | 단계별 기능 축소 |
| `CachedQueueSizeProvider` | 큐 크기 캐싱 (Redis 지연 방지) |

---

## 3. 상세 구현 명세

### 3.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/scaling/
├── __init__.py
├── config.py              # 설정 (BackpressureSettings)
├── rate_controller.py     # Rate-aware Backpressure
├── traffic_gate.py        # RateController + LoadShedding 통합
├── queue_provider.py      # 큐 크기 제공자 (캐싱 포함)
├── metrics.py             # Prometheus 메트릭
├── graceful_degradation.py # 단계별 기능 축소
└── hpa_exporter.py        # HPA 메트릭 내보내기
```

### 3.2 설정 (config.py)

```python
# packages/selfhealing-python/src/selfhealing/scaling/config.py
"""
Auto-Scaling & Backpressure 설정.

Note:
    클래스명을 BackpressureSettings로 지정하여 기존 ScaleSettings
    (settings/scale.py)와 역할 분리. ScaleSettings는 대규모 이벤트 처리용,
    BackpressureSettings는 트래픽 제어용.

기존 코드 참조:
- audit/ring_buffer.py: RingBuffer
- audit/cascade_load_shedding.py: CascadeLoadShedding
- services/error_budget_gate/gate.py: Gate 패턴
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


# =============================================================================
# 레벨별 Rate 감소 배율 (AIMD: Additive Increase, Multiplicative Decrease)
# 기존 adaptive.py의 EMERGENCY_LEVEL_LIMIT_MULTIPLIERS 패턴 참조
# =============================================================================

LEVEL_RATE_MULTIPLIERS: dict[BackpressureLevel, float] = {
    BackpressureLevel.NONE: 1.0,      # 정상: 100% 처리율
    BackpressureLevel.LOW: 1.0,       # 약간: 유지
    BackpressureLevel.MEDIUM: 0.9,    # 중간: 90%로 감소
    BackpressureLevel.HIGH: 0.8,      # 높음: 80%로 감소
    BackpressureLevel.CRITICAL: 0.5,  # 위험: 50%로 급감 (AIMD의 MD 부분)
}


class BackpressureSettings(BaseSettings):
    """
    Auto-Scaling & Backpressure 설정.

    Note:
        기존 ScaleSettings(settings/scale.py)와 역할 분리:
        - ScaleSettings: 대규모 이벤트 처리 (batch_size, flush_interval 등)
        - BackpressureSettings: 트래픽 제어 (rate limit, 큐 임계치 등)

    환경변수:
    - SELFHEALING_BACKPRESSURE_*
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_BACKPRESSURE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

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
        ge=1,
        description="LOW 레벨 큐 크기",
    )
    queue_medium_threshold: int = Field(
        default=500,
        ge=1,
        description="MEDIUM 레벨 큐 크기",
    )
    queue_high_threshold: int = Field(
        default=1000,
        ge=1,
        description="HIGH 레벨 큐 크기",
    )
    queue_critical_threshold: int = Field(
        default=5000,
        ge=1,
        description="CRITICAL 레벨 큐 크기",
    )

    # Rate Limit (처리/초)
    max_rate_per_second: float = Field(
        default=1000.0,
        ge=1.0,
        description="최대 처리율 (항목/초)",
    )
    min_rate_per_second: float = Field(
        default=10.0,
        ge=1.0,
        description="최소 처리율 (항목/초)",
    )

    # Rate 조절 (증가만 설정, 감소는 LEVEL_RATE_MULTIPLIERS 사용)
    rate_increase_factor: float = Field(
        default=1.1,
        ge=1.0,
        le=2.0,
        description="Rate 증가 계수 (정상화 시)",
    )
    rate_adjust_interval_seconds: float = Field(
        default=5.0,
        ge=1.0,
        description="Rate 조절 주기 (초)",
    )

    # 큐 크기 캐싱 (Redis 네트워크 지연 방지)
    queue_size_cache_ttl_seconds: float = Field(
        default=2.0,
        ge=0.5,
        le=10.0,
        description="큐 크기 캐시 TTL (초). Redis 조회 빈도 제한.",
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
        ge=1,
        description="HPA 목표 큐 깊이",
    )

    # Graceful Degradation
    graceful_degradation_enabled: bool = Field(
        default=True,
        description="Graceful Degradation 활성화",
    )

    # 503 응답 커스터마이징
    reject_message: str = Field(
        default="Service temporarily unavailable due to high load",
        description="503 거부 시 응답 메시지 (다국어/브랜딩 지원)",
    )
    reject_retry_after_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Retry-After 헤더 값 (초)",
    )

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

    def get_rate_multiplier(self, level: BackpressureLevel) -> float:
        """레벨별 Rate 감소 배율 반환 (AIMD 패턴)."""
        return LEVEL_RATE_MULTIPLIERS.get(level, 1.0)


@lru_cache(maxsize=1)
def get_backpressure_settings() -> BackpressureSettings:
    """설정 싱글톤 반환."""
    return BackpressureSettings()


def reset_backpressure_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_backpressure_settings.cache_clear()
```

### 3.3 Rate Controller (rate_controller.py)

> ⚠️ **Concurrency Warning**
>
> 이 모듈은 **동기(Threading) 환경**에서 사용하도록 설계되었습니다.
> Django/Celery 같은 동기 환경에서는 문제없이 동작합니다.
>
> **asyncio 환경에서 사용 시 주의:**
> - `time.sleep()`과 `threading.Lock`이 이벤트 루프를 블로킹할 수 있습니다.
> - asyncio 환경에서는 별도의 `AsyncRateController` 구현을 권장합니다.
> - 기존 `utils/jitter.py`의 `asyncio.iscoroutinefunction()` 분기 패턴 참조.

```python
# packages/selfhealing-python/src/selfhealing/scaling/rate_controller.py
"""
Rate-aware Backpressure Controller.

동적으로 처리율을 조절하여 과부하 방지.
AIMD (Additive Increase, Multiplicative Decrease) 패턴 적용.

Concurrency Model:
    이 모듈은 threading 기반 동기 환경용입니다.
    asyncio 환경에서는 AsyncRateController를 사용하세요.

기존 코드 참조:
- audit/ring_buffer.py: RingBuffer
- audit/cascade_load_shedding.py: CascadeLoadShedding
- services/throttle/adaptive.py: AdaptiveThrottle (AIMD 패턴)
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
    BackpressureSettings,
    get_backpressure_settings,
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

        Warning:
            이 메서드는 time.sleep()을 사용합니다.
            asyncio 환경에서는 이벤트 루프를 블로킹합니다.

        Args:
            timeout: 최대 대기 시간

        Returns:
            토큰 획득 성공 여부
        """
        start = time.time()
        while time.time() - start < timeout:
            if self.consume():
                return True
            time.sleep(0.01)  # 동기 환경에서만 사용
        return False


class RateController:
    """
    Rate-aware Backpressure Controller.

    기능:
    - 큐 크기 기반 Backpressure 레벨 계산
    - 동적 Rate 조절 (AIMD 패턴: 레벨별 차등 감소)
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
        settings: BackpressureSettings | None = None,
        queue_size_provider: Callable[[], int] | None = None,
    ):
        """
        초기화.

        Args:
            settings: 설정
            queue_size_provider: 큐 크기 제공 함수
        """
        self._settings = settings or get_backpressure_settings()
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
        """
        Rate 조절 (AIMD 패턴).

        - 과부하 시: 레벨별 차등 감소 (Multiplicative Decrease)
        - 정상화 시: 점진적 증가 (Additive Increase)
        """
        queue_size = self._queue_size_provider()
        new_level = self._settings.get_level_for_queue_size(queue_size)

        with self._lock:
            old_level = self._level
            self._level = new_level

        # AIMD 패턴: 레벨별 Rate 배율 적용
        if new_level == BackpressureLevel.NONE:
            # 정상: 점진적 증가 (AI)
            new_rate = self._current_rate * self._settings.rate_increase_factor
        else:
            # 과부하: 레벨별 차등 감소 (MD)
            multiplier = self._settings.get_rate_multiplier(new_level)
            new_rate = self._settings.max_rate_per_second * multiplier

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
                    f"(level={new_level.value}, queue={queue_size}, "
                    f"multiplier={self._settings.get_rate_multiplier(new_level)})"
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

### 3.4 큐 크기 캐싱 Provider (queue_provider.py)

```python
# packages/selfhealing-python/src/selfhealing/scaling/queue_provider.py
"""
큐 크기 제공자 (캐싱 포함).

Redis 큐 조회 시 네트워크 지연이 RateController 병목이 되는 것을 방지.
기존 adaptive.py의 _emergency_cache_ttl_seconds 패턴 참조.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from selfhealing.scaling.config import (
    BackpressureSettings,
    get_backpressure_settings,
)

logger = logging.getLogger(__name__)


class CachedQueueSizeProvider:
    """
    큐 크기 캐싱 Provider.

    Redis 등 외부 큐 조회 시 빈번한 네트워크 호출을 방지합니다.

    Usage:
        def get_redis_queue_size() -> int:
            return redis.llen("my_queue")

        provider = CachedQueueSizeProvider(get_redis_queue_size, cache_ttl=2.0)

        # 2초 내 재호출 시 캐시된 값 반환
        size = provider()
    """

    def __init__(
        self,
        provider: Callable[[], int],
        cache_ttl: float | None = None,
        settings: BackpressureSettings | None = None,
    ):
        """
        초기화.

        Args:
            provider: 실제 큐 크기 조회 함수
            cache_ttl: 캐시 TTL (초). None이면 설정에서 로드.
            settings: Backpressure 설정
        """
        self._provider = provider
        self._settings = settings or get_backpressure_settings()
        self._cache_ttl = cache_ttl or self._settings.queue_size_cache_ttl_seconds

        self._cached_value = 0
        self._last_fetch_time = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> int:
        """큐 크기 반환 (캐시 적용)."""
        now = time.time()

        with self._lock:
            if now - self._last_fetch_time > self._cache_ttl:
                try:
                    self._cached_value = self._provider()
                    self._last_fetch_time = now
                except Exception as e:
                    logger.warning(
                        f"[CachedQueueSizeProvider] Fetch failed, using cached: {e}"
                    )
                    # 실패 시 기존 캐시 값 유지

            return self._cached_value

    def invalidate(self) -> None:
        """캐시 무효화."""
        with self._lock:
            self._last_fetch_time = 0.0

    def get_cache_info(self) -> dict:
        """캐시 정보 반환."""
        with self._lock:
            return {
                "cached_value": self._cached_value,
                "last_fetch_time": self._last_fetch_time,
                "cache_ttl": self._cache_ttl,
                "age_seconds": time.time() - self._last_fetch_time,
            }
```

### 3.5 Traffic Gate (traffic_gate.py)

```python
# packages/selfhealing-python/src/selfhealing/scaling/traffic_gate.py
"""
Traffic Gate - RateController + CascadeLoadShedding 통합.

기존 시스템의 error_budget_gate 네이밍 패턴 따름.
RateController와 CascadeLoadShedding을 파이프라인 형태로 통합.

기존 코드 참조:
- audit/cascade_load_shedding.py: CascadeLoadShedding.should_accept()
- gate 네이밍 패턴: error_budget_gate 전역 사용 (20+ locations)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from selfhealing.scaling.config import (
    BackpressureLevel,
    BackpressureSettings,
    get_backpressure_settings,
)
from selfhealing.scaling.rate_controller import (
    RateController,
    get_rate_controller,
)

logger = logging.getLogger(__name__)


@dataclass
class TrafficDecision:
    """Traffic Gate 결정 결과."""

    allowed: bool
    """처리 허용 여부."""

    reason: str
    """결정 이유."""

    level: BackpressureLevel
    """현재 Backpressure 레벨."""

    gate: str
    """결정한 게이트 이름."""

    metadata: dict[str, Any] | None = None
    """추가 메타데이터."""


class TrafficGate:
    """
    Traffic Gate - 통합 트래픽 제어.

    처리 순서:
    1. CascadeLoadShedding.should_accept() - 우선순위 기반 필터링
    2. RateController.should_process() - Rate Limit 기반 스로틀링

    Usage:
        gate = TrafficGate()

        # 처리 전 확인
        decision = gate.should_allow(priority=5)
        if decision.allowed:
            process_item()
        else:
            logger.warning(f"Rejected: {decision.reason} by {decision.gate}")

    Integration with error_budget_gate:
        기존 error_budget_gate와 함께 사용 시:

        if error_budget_gate.should_allow() and traffic_gate.should_allow().allowed:
            process_request()
    """

    def __init__(
        self,
        settings: BackpressureSettings | None = None,
        rate_controller: RateController | None = None,
        load_shedding: Any | None = None,  # CascadeLoadShedding
    ):
        """
        초기화.

        Args:
            settings: Backpressure 설정
            rate_controller: RateController 인스턴스
            load_shedding: CascadeLoadShedding 인스턴스 (선택)
        """
        self._settings = settings or get_backpressure_settings()
        self._rate_controller = rate_controller or get_rate_controller()
        self._load_shedding = load_shedding

    def should_allow(
        self,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> TrafficDecision:
        """
        트래픽 허용 여부 결정.

        Args:
            priority: 요청 우선순위 (낮을수록 높은 우선순위)
            metadata: 결정에 사용할 추가 메타데이터

        Returns:
            TrafficDecision 결과
        """
        current_level = self._rate_controller.get_state().level

        # 1단계: CascadeLoadShedding 확인
        if self._load_shedding is not None:
            try:
                if not self._load_shedding.should_accept(priority=priority):
                    return TrafficDecision(
                        allowed=False,
                        reason=f"Load shedding rejected priority={priority}",
                        level=current_level,
                        gate="CascadeLoadShedding",
                        metadata=metadata,
                    )
            except Exception as e:
                logger.warning(f"[TrafficGate] LoadShedding error: {e}")

        # 2단계: RateController 확인
        if not self._rate_controller.should_process():
            return TrafficDecision(
                allowed=False,
                reason=f"Rate limit exceeded at level={current_level.value}",
                level=current_level,
                gate="RateController",
                metadata=metadata,
            )

        return TrafficDecision(
            allowed=True,
            reason="Allowed",
            level=current_level,
            gate="TrafficGate",
            metadata=metadata,
        )

    def get_level(self) -> BackpressureLevel:
        """현재 Backpressure 레벨 반환."""
        return self._rate_controller.get_state().level


# =============================================================================
# Singleton (error_budget_gate 패턴 따름)
# =============================================================================

_traffic_gate: TrafficGate | None = None


def get_traffic_gate() -> TrafficGate:
    """TrafficGate 싱글톤 반환."""
    global _traffic_gate
    if _traffic_gate is None:
        _traffic_gate = TrafficGate()
    return _traffic_gate


# 전역 인스턴스 (error_budget_gate 사용 패턴과 동일)
traffic_gate = get_traffic_gate()
```

### 3.6 Prometheus Metrics (metrics.py)

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

from selfhealing.scaling.config import BackpressureSettings, get_backpressure_settings

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
        settings: BackpressureSettings | None = None,
    ):
        """초기화."""
        self._settings = settings or get_backpressure_settings()
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

### 3.7 Graceful Degradation (graceful_degradation.py)

> **비활성화된 기능 가시성**
>
> Degradation으로 비활성화된 기능 목록은 `X-SelfHealing-Degraded-Features` 헤더를 통해
> 클라이언트와 모니터링 시스템에 전달됩니다.
> 기존 `x-selfhealing-*` 헤더 규약(`causation_context.py`)을 따릅니다.

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
    BackpressureSettings,
    get_backpressure_settings,
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
        settings: BackpressureSettings | None = None,
    ):
        """초기화."""
        self._settings = settings or get_backpressure_settings()
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

> ⚠️ **HPA-Backpressure 지연 Gap 주의**
>
> HPA Scale-Up과 Pod Ready 사이에는 필연적으로 지연이 발생합니다:
> - Container image pull: 10-60초
> - Pod initialization: 5-30초
> - Readiness probe 통과: 5-15초
>
> **이 지연 기간 동안 Backpressure가 부하를 제어해야 합니다.**
>
> 권장 설정:
> - Scale-Up은 `stabilizationWindowSeconds: 0`으로 즉시 반응
> - Scale-Down은 최소 300초 유지 (flapping 방지)
> - KEDA `cooldownPeriod: 300` 이상 권장

### 4.1 Prometheus Adapter 설정

> **메트릭 가중치 설정 권장**
>
> 복수 메트릭 사용 시, KEDA ScaledObject의 `advanced.horizontalPodAutoscalerConfig` 또는
> HPA의 `--horizontal-pod-autoscaler-tolerance` 설정을 통해 가중치를 조정할 수 있습니다.
>
> 참고: 기존 `keda-scaledobject-celery-critical.yaml`은 Redis 트리거만 사용합니다.

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
      # 300초 안정화 - flapping 방지
      # 기존 KEDA cooldownPeriod: 300과 일치
      stabilizationWindowSeconds: 300
      policies:
        - type: Percent
          value: 10
          periodSeconds: 60
    scaleUp:
      # 즉시 스케일 업 - 기존 keda-scaledobject-celery-critical.yaml 패턴
      # 부하 급증 시 Backpressure가 HPA 대기 시간을 커버
      stabilizationWindowSeconds: 0
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
"""
Backpressure Middleware for Django.

기존 헤더 규약 참조:
- causation_context.py: x-selfhealing-* 헤더 패턴
"""

from django.http import HttpResponse, HttpRequest

from selfhealing.scaling.config import get_backpressure_settings
from selfhealing.scaling.rate_controller import get_rate_controller
from selfhealing.scaling.graceful_degradation import get_graceful_degradation


class BackpressureMiddleware:
    """
    Backpressure 미들웨어.

    과부하 시 503 응답과 함께 커스텀 메시지 반환.
    비활성화된 기능 목록을 X-SelfHealing-Degraded-Features 헤더로 전달.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._controller = get_rate_controller()
        self._degradation = get_graceful_degradation()
        self._settings = get_backpressure_settings()

    def __call__(self, request: HttpRequest):
        # Backpressure 확인
        if not self._controller.should_process():
            return HttpResponse(
                self._settings.reject_message,  # 커스텀 메시지
                status=503,
                headers={
                    "Retry-After": str(self._settings.reject_retry_after_seconds),
                    # 기존 x-selfhealing-* 헤더 규약 따름
                    "X-SelfHealing-Backpressure-Level": str(
                        self._controller.get_state().level.value
                    ),
                },
            )

        response = self.get_response(request)

        # 비활성화된 기능 헤더 추가
        disabled_features = self._degradation.get_disabled_features()
        if disabled_features:
            response["X-SelfHealing-Degraded-Features"] = ",".join(disabled_features)

        return response
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

# =============================================================================
# Backpressure 설정 (SELFHEALING_BACKPRESSURE_ 접두사)
# 기존 SELFHEALING_SCALE_ (ScaleSettings)와 구분
# =============================================================================

# 기본 설정
SELFHEALING_BACKPRESSURE_ENABLED=true
SELFHEALING_BACKPRESSURE_DEFAULT_STRATEGY=throttle

# 큐 임계치 (KEDA pollingInterval과 연동 고려)
SELFHEALING_BACKPRESSURE_QUEUE_LOW_THRESHOLD=100
SELFHEALING_BACKPRESSURE_QUEUE_MEDIUM_THRESHOLD=500
SELFHEALING_BACKPRESSURE_QUEUE_HIGH_THRESHOLD=1000
SELFHEALING_BACKPRESSURE_QUEUE_CRITICAL_THRESHOLD=5000

# Rate Limit
SELFHEALING_BACKPRESSURE_MAX_RATE_PER_SECOND=1000
SELFHEALING_BACKPRESSURE_MIN_RATE_PER_SECOND=10

# AIMD Rate 조절 (기존 adaptive.py EMERGENCY_LEVEL_LIMIT_MULTIPLIERS 패턴)
SELFHEALING_BACKPRESSURE_RATE_INCREASE_FACTOR=1.1
SELFHEALING_BACKPRESSURE_RATE_DECREASE_FACTOR=0.5

# 큐 크기 캐싱 (네트워크 지연 방지)
SELFHEALING_BACKPRESSURE_QUEUE_SIZE_CACHE_TTL_SECONDS=2.0

# 503 응답 커스터마이징
SELFHEALING_BACKPRESSURE_REJECT_MESSAGE="Service temporarily unavailable. Please retry."
SELFHEALING_BACKPRESSURE_REJECT_RETRY_AFTER_SECONDS=5

# Metrics
SELFHEALING_BACKPRESSURE_METRICS_ENABLED=true
SELFHEALING_BACKPRESSURE_METRICS_PREFIX=selfhealing_

# HPA
SELFHEALING_BACKPRESSURE_HPA_ENABLED=true
SELFHEALING_BACKPRESSURE_HPA_TARGET_QUEUE_DEPTH=100

# Graceful Degradation
SELFHEALING_BACKPRESSURE_GRACEFUL_DEGRADATION_ENABLED=true
```

---

## 7. Prerequisites (사전 점검)

구현 전 다음 사항을 확인하세요:

### 7.1 Kubernetes 환경 점검

```bash
# metrics-server 확인 (CPU/Memory 기반 HPA 필수)
kubectl get deployment metrics-server -n kube-system

# Prometheus Adapter 확인 (Custom Metrics HPA)
kubectl get deployment prometheus-adapter -n monitoring

# KEDA 확인 (Redis 트리거 사용 시)
kubectl get deployment keda-operator -n keda

# Custom Metrics API 확인
kubectl get --raw /apis/custom.metrics.k8s.io/v1beta1 | jq '.resources[].name'
```

### 7.2 기존 HPA 설정 충돌 점검

```bash
# 기존 HPA 확인
kubectl get hpa -A

# 기존 django-api-hpa stabilizationWindowSeconds 확인
# 주의: 기존 설정과 본 문서 권장값(0)이 다를 수 있음
kubectl get hpa django-api-hpa -o yaml | grep -A10 behavior
```

### 7.3 Redis 연결 확인 (KEDA 사용 시)

```bash
# Redis 연결 Secret 확인
kubectl get secret redis-credentials -n selfhealing

# KEDA TriggerAuthentication 확인
kubectl get triggerauthentication -n selfhealing
```

---

## 8. Capacity Planning (용량 계획)

### 8.1 Backpressure 임계치 계산 공식

```
Queue Threshold = (Pod Count × Processing Rate × Target Latency)

예시:
- Pod Count: 4
- Processing Rate: 100 items/sec per pod
- Target Latency: 5초

LOW Threshold = 4 × 100 × 1 = 400
MEDIUM Threshold = 4 × 100 × 3 = 1,200
HIGH Threshold = 4 × 100 × 5 = 2,000
CRITICAL Threshold = 4 × 100 × 10 = 4,000
```

### 8.2 HPA 스케일 계산

```
Required Replicas = ceil(Current Queue Depth / Target Queue Depth per Pod)

예시:
- Current Queue Depth: 800
- Target Queue Depth per Pod: 100

Required Replicas = ceil(800 / 100) = 8
```

### 8.3 KEDA 폴링 간격 고려

```
KEDA 권장 설정 (기존 YAML 참조):
- critical 큐: pollingInterval: 5s
- audit 큐: pollingInterval: 10s

Rate Controller 조절 간격:
- rate_adjust_interval_seconds >= KEDA pollingInterval
- 권장: 5초 (KEDA와 동기화)
```

---

## 9. 구현 체크리스트

### 9.1 Core 모듈

- [ ] `scaling/__init__.py` 생성
- [ ] `scaling/config.py` 구현 (BackpressureSettings)
- [ ] `scaling/rate_controller.py` 구현 (AIMD 패턴)
- [ ] `scaling/queue_provider.py` 구현 (CachedQueueSizeProvider)
- [ ] `scaling/traffic_gate.py` 구현 (TrafficGate)
- [ ] `scaling/metrics.py` 구현
- [ ] `scaling/graceful_degradation.py` 구현

### 9.2 통합

- [ ] Django 미들웨어 통합 (X-SelfHealing-* 헤더)
- [ ] Celery Worker 통합
- [ ] 기존 CascadeLoadShedding 연동

### 9.3 Kubernetes

- [ ] Kubernetes HPA 설정 (stabilizationWindowSeconds 확인)
- [ ] Prometheus Adapter 설정
- [ ] KEDA ScaledObject 검토 (기존과 충돌 여부)
- [ ] ServiceMonitor 설정

### 9.4 검증

- [ ] 단위 테스트 작성
- [ ] 부하 테스트 수행
- [ ] HPA-Backpressure 지연 시나리오 테스트

---

## 10. Service Mesh 호환성 주의

> ⚠️ **Istio/Linkerd 사용 시 주의**
>
> Service Mesh의 자체 Rate Limit 기능과 본 Backpressure가 충돌할 수 있습니다.
>
> **권장 설정:**
> - Istio: `EnvoyFilter` 또는 `AuthorizationPolicy`의 rate limit 비활성화
> - Linkerd: 별도 rate limit 설정 없음 (Backpressure 사용)
>
> **모니터링 연동:**
> - Istio: `istio_request_duration_milliseconds` 메트릭과 별도 관리
> - Prometheus Adapter: selfhealing_* 메트릭만 사용

---

## 11. 관련 문서

- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜
- [ring_buffer.py](../../packages/selfhealing-python/src/selfhealing/audit/ring_buffer.py) - 기존 RingBuffer
- [cascade_load_shedding.py](../../packages/selfhealing-python/src/selfhealing/audit/cascade_load_shedding.py) - 기존 Load Shedding
- [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) - AIMD 패턴 참조
- [django-api-hpa.yaml](../../k8s/django-api-hpa.yaml) - 기존 HPA 설정
- [keda-scaledobject-celery-critical.yaml](../../k8s/keda-scaledobject-celery-critical.yaml) - KEDA 설정

---

## 12. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
| 1.1.0 | 2026-02-04 | 리뷰 반영: AIMD 패턴, TrafficGate, CachedQueueSizeProvider, Concurrency Warning, Prerequisites, Capacity Planning, Service Mesh 주의사항 추가 |
