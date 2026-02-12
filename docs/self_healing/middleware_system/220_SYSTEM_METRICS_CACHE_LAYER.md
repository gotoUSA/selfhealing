# 220. 시스템 메트릭 캐시 레이어 (System Metrics Cache Layer)

> **상태**: ✅ 구현 완료
> **목적**: `psutil.cpu_percent(interval=0.1)`의 100ms 블로킹을 백그라운드 스레드로 격리하고, 모든 소비자(EventBus 핸들러, Celery Task, ResourceGuard)가 캐시된 값을 ~0ms에 읽을 수 있도록 한다.
> **기준일**: 2026-02-12
> **구현일**: 2026-02-12
> **선행 문서**: 213_EVENTBUS_HANDLER_CELERY_DELEGATION_PLAN.md

---

## 1. 배경: 현재 시스템의 psutil 호출 현황

### 1.1 `psutil.cpu_percent(interval=0.1)` 호출 위치 — 코드 근거

| # | 파일 | 라인 | 호출 방식 | 블로킹 | 실행 컨텍스트 |
|---|------|------|-----------|--------|--------------|
| 1 | `api/django/views/xtest/base.py` | L637 | `psutil.cpu_percent(interval=0.1)` | **100ms** | `collect_system_snapshot()` 함수 |
| 2 | `services/chaos/safety_guard/resource_guard.py` | L122 | `psutil.cpu_percent(interval=0.1)` | **100ms** | `ResourceGuard._get_cpu_percent()` |
| 3 | `services/circuit_breaker/service.py` | L606 | `psutil.cpu_percent(interval=None)` | **~0ms** | CB OPEN 스냅샷 |

**1번(`collect_system_snapshot`)의 호출 경로** (213 구현 완료 상태):

| 경로 | 실행 위치 | 코드 |
|------|-----------|------|
| CB OPEN 스냅샷 | Celery Worker | `adapters/celery/tasks/circuit_breaker.py` L479 |
| CB CLOSED 개별 Postmortem | Celery Worker | `adapters/celery/tasks/postmortem.py` L655 |
| Emergency Postmortem | Celery Worker | `adapters/celery/tasks/postmortem.py` L755 |
| CB CLOSED 개별 Postmortem (동기 fallback) | **Web Server** | `services/event_bus/bus.py` L925 |
| Emergency Postmortem (동기 fallback) | **Web Server** | `services/event_bus/bus.py` L1296 |

**2번(`ResourceGuard._get_cpu_percent`)의 호출 경로**:

```
ResourceGuard.is_safe_for_chaos()                     ← X-Test API 요청마다 호출
  └── self.get_resource_status()
        └── self._get_cpu_percent()
              └── psutil.cpu_percent(interval=0.1)     ← 100ms 블로킹
```

**파일**: `services/chaos/safety_guard/resource_guard.py` L165, L122

### 1.2 `psutil.cpu_percent(interval=None)` — 기존 문제

**파일**: `services/circuit_breaker/service.py` L603-606

```python
import psutil
snapshot["system_metrics"] = {
    "cpu_percent": psutil.cpu_percent(interval=None),
    "memory_percent": psutil.virtual_memory().percent,
}
```

`interval=None`은 **마지막 `cpu_percent()` 호출 이후**의 누적 평균을 반환한다.
문제: 이전 호출이 없으면 **부트 이후 전체 평균** 또는 **0.0**을 반환하여 의미없는 값이 된다.

### 1.3 213 구현 후 남은 한계

213 문서에서 EventBus 핸들러의 I/O 작업을 Celery로 위임하여 **Web Server 차단은 제거**했다.
그러나 다음 한계가 남아 있다:

1. **Celery Task 스냅샷의 CPU/Memory는 Worker 노드 값**
   - `adapters/celery/tasks/circuit_breaker.py` L487: `snapshot["snapshot_source"] = "celery_worker"`
   - `adapters/celery/tasks/circuit_breaker.py` L488: `snapshot["snapshot_note"] = "Worker 노드의 CPU/Memory. Web Server와 다를 수 있음."`
   - `adapters/celery/tasks/postmortem.py` L657: `snapshot["snapshot_source"] = "celery_worker"`
   - Postmortem 분석 시 **실제 장애 노드(Web Server)의 리소스 상태를 알 수 없음**

2. **ResourceGuard의 100ms 블로킹은 제거되지 않음**
   - `resource_guard.py` L122: X-Test 요청마다 `psutil.cpu_percent(interval=0.1)` 직접 호출
   - X-Test 빈도가 높으면 **매 요청 100ms 추가 레이턴시**

3. **Celery 미설치 환경의 동기 fallback 경로에서 100ms 블로킹 잔존**
   - `bus.py` L840: `_create_individual_postmortem(...)` → `collect_system_snapshot()` → 100ms
   - `bus.py` L1296: `_create_emergency_postmortem_sync(...)` → `collect_system_snapshot()` → 100ms

4. **`circuit_breaker/service.py` L606의 `interval=None` 부정확**
   - 마지막 `cpu_percent()` 호출과의 간격이 불규칙하여 값의 의미가 불안정

---

## 2. 해결 전략: 백그라운드 스레드 캐시

### 2.1 핵심 아이디어

```
백그라운드 데몬 스레드 (1초 주기):
  └── psutil.cpu_percent(interval=0.1)     ← 100ms sleep (데몬 스레드에서)
  └── psutil.virtual_memory()               ← ~0ms
  └── 캐시 갱신 (dict 교체)                  ← atomic (GIL 보호)

소비자들 (Web Server 스레드):
  └── cache.get_cpu_percent()               ← ~0ms (캐시 읽기)
  └── cache.get_memory_percent()            ← ~0ms
  └── cache.get_snapshot()                  ← ~0ms (cpu + memory 통합)
```

### 2.2 기존 패턴과의 일치 — 코드 근거

코드베이스에 동일한 "백그라운드 데몬 스레드 + 주기적 갱신" 패턴이 **이미 3개 존재**한다:

| 기존 컴포넌트 | 패턴 | daemon | 코드 위치 |
|--------------|------|--------|-----------|
| `PrecomputedCacheWorker` | `threading.Timer` + 주기적 갱신 | `daemon=True` | `services/precomputed_cache/worker.py` L77-78 |
| `BatchMetricRecorder` | `threading.Thread` + SimpleQueue + 100ms flush | `daemon=True` | `services/metrics/registry.py` L83-85 |
| `AsyncHealingLogger` | `threading.Thread` + Queue + worker loop | `daemon=True` | `utils/async_logger.py` L335 |

**`PrecomputedCacheWorker`가 가장 유사한 선례**:

```python
# services/precomputed_cache/worker.py L39-78
class PrecomputedCacheWorker:
    def __init__(self):
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()
        self._compute_functions: dict[str, Callable[[], dict[str, Any]]] = {}

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._schedule_refresh()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _schedule_refresh(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(_get_refresh_interval(), self._do_refresh)
        self._timer.daemon = True
        self._timer.start()
```

**`AppConfig.ready()`에서의 시작 패턴**:

```python
# adapters/django/apps.py L473-520
def _start_precomputed_cache_worker(self):
    # 설정에서 비활성화된 경우
    if not getattr(settings, "SELFHEALING_PRECOMPUTED_CACHE_ENABLED", True):
        return

    # 중복 실행 방지
    with self._cache_worker_lock:
        if self._cache_worker_started:
            return
        SelfHealingConfig._cache_worker_started = True

    try:
        from selfhealing.services.precomputed_cache import start_precomputed_cache
        start_precomputed_cache()
    except ImportError:
        logger.debug("[SelfHealing] precomputed_cache module not available")
    except Exception as e:
        logger.warning(f"[SelfHealing] Failed to start ... (non-fatal): {e}.")
```

---

## 3. 구현 설계

### 3.1 새 모듈 — `services/system_metrics_cache.py`

```python
"""
System Metrics Cache — Background psutil Cache Layer.

Threading.Timer 기반으로 1초마다 psutil CPU/Memory를 측정하여 캐시.
소비자는 캐시에서 ~0ms에 값을 읽어 블로킹 없이 시스템 메트릭을 조회할 수 있다.

Architecture:
- 패턴: PrecomputedCacheWorker (services/precomputed_cache/worker.py)와 동일
- 시작: AppConfig.ready()에서 호출 (adapters/django/apps.py)
- 소비자: collect_system_snapshot(), ResourceGuard._get_cpu_percent(),
          circuit_breaker/service.py L606

.. versionadded:: 6.x.0
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedMetrics:
    """
    캐시된 시스템 메트릭 스냅샷 (Immutable).

    frozen=True로 설정하여 읽기 시 동시성 문제를 원천 방지한다.
    백그라운드 스레드는 새 인스턴스를 생성하여 참조를 교체한다 (Copy-on-Write).
    Python GIL 하에서 참조 교체는 atomic이므로 Lock 불필요.
    """

    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_available_mb: float = 0.0
    measured_at: str = ""      # ISO format timestamp
    source: str = "cache"      # "cache" | "direct" (fallback)
    age_seconds: float = 0.0   # 마지막 갱신 후 경과 시간


class SystemMetricsCache:
    """
    시스템 메트릭 백그라운드 캐시.

    선례: PrecomputedCacheWorker (services/precomputed_cache/worker.py L39-78)와
    동일한 threading.Timer + daemon=True 패턴.

    동작:
    1. start() 호출 시 백그라운드 데몬 스레드 시작
    2. _refresh_interval 초마다 psutil.cpu_percent(interval=_sample_interval) + virtual_memory() 호출
    3. CachedMetrics 인스턴스를 생성하여 _cached 참조를 atomic 교체
    4. 소비자는 get_metrics() / get_cpu_percent() / get_memory_percent()로 ~0ms에 읽기

    스레드 안전성:
    - _cached 참조 교체는 GIL 하에서 atomic (Lock 불필요)
    - start()/stop()은 _lock으로 보호 (PrecomputedCacheWorker.start() L57-61과 동일)
    """

    def __init__(
        self,
        refresh_interval: float = 1.0,        # 갱신 주기 (초)
        sample_interval: float = 0.1,          # psutil.cpu_percent interval (초)
        max_age_seconds: float = 5.0,          # 캐시 유효 최대 시간 (초)
    ):
        self._refresh_interval = refresh_interval
        self._sample_interval = sample_interval
        self._max_age_seconds = max_age_seconds
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()
        self._cached = CachedMetrics()         # 초기값 (zero)
        self._last_refresh: float = 0.0        # time.monotonic() 기준

    # =========================================================================
    # Lifecycle (PrecomputedCacheWorker.start/stop과 동일 패턴)
    # =========================================================================

    def start(self) -> None:
        """백그라운드 갱신 시작.

        Cold Start 방지: 첫 번째 _do_refresh()를 동기로 1회 호출하여
        캐시 초기값을 즉시 확보한 후 Timer 스케줄링을 시작한다.

        동기 호출 비용: ~100ms (psutil.cpu_percent(interval=0.1))
        AppConfig.ready()는 서버 시작 시 1회만 실행되므로 무시 가능.

        이 설계가 없으면 start() 후 최대 refresh_interval + sample_interval
        (기본 1.1초) 동안 CachedMetrics(cpu_percent=0.0, measured_at="")이
        반환되어 ResourceGuard가 CPU 과부하를 감지하지 못하는 위험이 있다.

        참고: PrecomputedCacheWorker (worker.py L57-62)는 캐시 miss 시
        compute_fn을 직접 호출하는 fallback이 내장되어 있어 cold start
        문제가 없지만, SystemMetricsCache에는 그런 fallback이 없으므로
        start() 시 동기 1회 호출이 필수적이다.
        """
        with self._lock:
            if self._running:
                return
            self._running = True
            logger.info(
                f"[SystemMetricsCache] Starting "
                f"(refresh={self._refresh_interval}s, sample={self._sample_interval}s)"
            )

            # Cold Start 방지: 첫 측정을 동기로 수행 (~100ms)
            # 이후 소비자는 즉시 유효한 캐시 값을 읽을 수 있다.
            self._do_refresh()

            # 이후 주기적 갱신 스케줄링
            self._schedule_refresh()

    def stop(self) -> None:
        """백그라운드 갱신 중지."""
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
            logger.info("[SystemMetricsCache] Stopped")

    def is_running(self) -> bool:
        """캐시 워커 실행 여부."""
        return self._running

    # =========================================================================
    # 소비자 API (Read — Lock-free, ~0ms)
    # =========================================================================

    def get_metrics(self) -> CachedMetrics:
        """
        캐시된 메트릭 전체 반환.

        캐시가 max_age_seconds를 초과하면 source="stale"로 표시.
        """
        cached = self._cached  # 참조 복사 (atomic)
        age = time.monotonic() - self._last_refresh if self._last_refresh > 0 else float("inf")

        if age > self._max_age_seconds:
            # stale이지만 값은 반환 (Fail-Open)
            return CachedMetrics(
                cpu_percent=cached.cpu_percent,
                memory_percent=cached.memory_percent,
                memory_used_mb=cached.memory_used_mb,
                memory_available_mb=cached.memory_available_mb,
                measured_at=cached.measured_at,
                source="stale",
                age_seconds=round(age, 1),
            )
        return cached

    def get_cpu_percent(self) -> float:
        """캐시된 CPU 사용률."""
        return self._cached.cpu_percent

    def get_memory_percent(self) -> float:
        """캐시된 메모리 사용률."""
        return self._cached.memory_percent

    def get_snapshot_dict(self) -> dict[str, Any]:
        """
        collect_system_snapshot() 호환 딕셔너리 반환.

        기존 collect_system_snapshot() (base.py L620-699)의 cpu/memory 부분만
        캐시에서 읽어 반환. DB 연결수, Error Rate 등은 포함하지 않음
        (이 값들은 블로킹이 아니므로 캐시 불필요).
        """
        m = self._cached
        return {
            "cpu_percent": m.cpu_percent,
            "memory_percent": m.memory_percent,
            "memory_used_mb": m.memory_used_mb,
            "memory_available_mb": m.memory_available_mb,
            "metrics_source": m.source,
            "metrics_measured_at": m.measured_at,
        }

    # =========================================================================
    # Internal — Background Refresh
    # =========================================================================

    def _schedule_refresh(self) -> None:
        """다음 갱신 스케줄링 (PrecomputedCacheWorker._schedule_refresh와 동일)."""
        if not self._running:
            return
        self._timer = threading.Timer(self._refresh_interval, self._do_refresh)
        self._timer.daemon = True  # 메인 스레드 종료 시 함께 종료
        self._timer.start()

    def _do_refresh(self) -> None:
        """
        psutil로 CPU/Memory 측정 후 캐시 갱신.

        psutil.cpu_percent(interval=0.1)은 내부적으로 0.1초 sleep 후 측정하므로
        이 메서드는 ~100ms 소요된다. 데몬 스레드에서만 실행되므로 Web 스레드에 무영향.
        """
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=self._sample_interval)
            memory = psutil.virtual_memory()

            # 메트릭 정밀도 정책: round(val, 1)
            # 근거: ResourceCheckResult.to_response_dict() (resource_guard.py L86-87)
            #       round(self.cpu_percent, 1), round(self.memory_percent, 1)
            # 캐시 저장 시점에 통일하여 소비자 간 정밀도 일관성 보장.
            # round(round(x, 1), 1) == round(x, 1) 이므로 멱등, 중복 round 무해.
            self._cached = CachedMetrics(
                cpu_percent=round(cpu, 1),
                memory_percent=round(memory.percent, 1),
                memory_used_mb=round(memory.used / (1024 * 1024), 1),
                memory_available_mb=round(memory.available / (1024 * 1024), 1),
                measured_at=datetime.now(timezone.utc).isoformat(),
                source="cache",
                age_seconds=0.0,
            )
            self._last_refresh = time.monotonic()

        except Exception as e:
            logger.warning(f"[SystemMetricsCache] Refresh failed: {e}")
            # Fail-Open: 이전 캐시 값 유지, 다음 주기에 재시도

        # 다음 갱신 스케줄링
        self._schedule_refresh()

    def get_stats(self) -> dict[str, Any]:
        """디버깅/모니터링용 통계."""
        age = time.monotonic() - self._last_refresh if self._last_refresh > 0 else -1
        return {
            "running": self._running,
            "refresh_interval": self._refresh_interval,
            "sample_interval": self._sample_interval,
            "max_age_seconds": self._max_age_seconds,
            "cache_age_seconds": round(age, 1) if age >= 0 else None,
            "current_cpu_percent": self._cached.cpu_percent,
            "current_memory_percent": self._cached.memory_percent,
            "source": self._cached.source,
        }


# =============================================================================
# Global Singleton + Module-level API
# (PrecomputedCacheWorker 패턴: worker.py L130-146과 동일)
# =============================================================================

_cache = SystemMetricsCache()


def get_system_metrics_cache() -> SystemMetricsCache:
    """글로벌 SystemMetricsCache 인스턴스 반환."""
    return _cache


def start_system_metrics_cache() -> None:
    """캐시 워커 시작. AppConfig.ready()에서 호출."""
    _cache.start()


def stop_system_metrics_cache() -> None:
    """캐시 워커 중지."""
    _cache.stop()


def get_cached_cpu_percent() -> float:
    """편의 함수: 캐시된 CPU 사용률 반환 (~0ms)."""
    return _cache.get_cpu_percent()


def get_cached_memory_percent() -> float:
    """편의 함수: 캐시된 메모리 사용률 반환 (~0ms)."""
    return _cache.get_memory_percent()


def reset_system_metrics_cache() -> None:
    """테스트용: 글로벌 인스턴스 리셋."""
    global _cache
    _cache.stop()
    _cache = SystemMetricsCache()
```

### 3.2 새 설정 파일 — `settings/system_metrics_cache.py`

기존 `settings/precomputed_cache.py`와 동일한 Pydantic v2 BaseSettings 패턴.

```python
"""
System Metrics Cache Settings - Pydantic v2.

환경 변수:
    SELFHEALING_SYSTEM_METRICS_CACHE_ENABLED=true
    SELFHEALING_SYSTEM_METRICS_CACHE_REFRESH_INTERVAL=1.0
    SELFHEALING_SYSTEM_METRICS_CACHE_SAMPLE_INTERVAL=0.1
    SELFHEALING_SYSTEM_METRICS_CACHE_MAX_AGE_SECONDS=5.0
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SystemMetricsCacheSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SYSTEM_METRICS_CACHE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    enabled: bool = Field(
        default=True,
        description="시스템 메트릭 캐시 활성화 여부",
    )
    refresh_interval: float = Field(
        default=1.0,
        ge=0.5,
        le=10.0,
        description="캐시 갱신 주기 (초). 1.0 = 1초마다 psutil 측정.",
    )
    sample_interval: float = Field(
        default=0.1,
        ge=0.05,
        le=1.0,
        description="psutil.cpu_percent(interval=?) 값. 0.1 = 100ms 샘플링.",
    )
    max_age_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="캐시 유효 최대 시간. 초과 시 source='stale'로 표시.",
    )

    @field_validator("refresh_interval")
    @classmethod
    def refresh_must_be_greater_than_sample(cls, v, info):
        sample = info.data.get("sample_interval", 0.1)
        if v <= sample:
            raise ValueError(
                f"refresh_interval ({v}) must be > sample_interval ({sample})"
            )
        return v


_settings: SystemMetricsCacheSettings | None = None


def get_system_metrics_cache_settings() -> SystemMetricsCacheSettings:
    global _settings
    if _settings is None:
        _settings = SystemMetricsCacheSettings()
    return _settings
```

---

## 4. 소비자별 변경 — 상세 코드

### 4.1 `collect_system_snapshot()` 변경

**파일**: `api/django/views/xtest/base.py` L620-699

**현재 코드** (L637-641):
```python
def collect_system_snapshot() -> dict[str, Any]:
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)       # ← 100ms 블로킹
        memory = psutil.virtual_memory()

        snapshot = {
            "timestamp": timezone.now().isoformat(),
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_used_mb": memory.used / (1024 * 1024),
            "memory_available_mb": memory.available / (1024 * 1024),
        }
```

**변경 후**:
```python
def collect_system_snapshot() -> dict[str, Any]:
    try:
        # 캐시에서 CPU/Memory 조회 (~0ms)
        # 캐시 미가동 시 직접 측정으로 fallback (100ms)
        try:
            from selfhealing.services.system_metrics_cache import get_system_metrics_cache

            cache = get_system_metrics_cache()
            if cache.is_running():
                metrics = cache.get_metrics()
                snapshot = {
                    "timestamp": timezone.now().isoformat(),
                    "cpu_percent": metrics.cpu_percent,
                    "memory_percent": metrics.memory_percent,
                    "memory_used_mb": metrics.memory_used_mb,
                    "memory_available_mb": metrics.memory_available_mb,
                    "metrics_source": metrics.source,
                }
            else:
                raise RuntimeError("Cache not running")
        except Exception:
            # Fallback: 직접 측정 (기존 동작 유지)
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            snapshot = {
                "timestamp": timezone.now().isoformat(),
                # fallback에서도 캐시와 동일한 round(val, 1) 적용
                # 캐시 경로/직접 경로 간 정밀도 일관성 보장
                "cpu_percent": round(cpu_percent, 1),
                "memory_percent": round(memory.percent, 1),
                "memory_used_mb": round(memory.used / (1024 * 1024), 1),
                "memory_available_mb": round(memory.available / (1024 * 1024), 1),
                "metrics_source": "direct",
            }
```

**변경 근거**:
- 캐시 가동 중: `cache.get_metrics()` → ~0ms
- 캐시 미가동 (테스트 환경, Celery Worker 등): 기존 `psutil.cpu_percent(interval=0.1)` → 100ms (동일)
- `metrics_source` 필드로 데이터 출처를 명시
- fallback 경로에서도 `round(val, 1)` 적용하여 캐시 경로와 정밀도 일관성 유지
  - 근거: `ResourceCheckResult.to_response_dict()` (`resource_guard.py` L86-87)에서 `round(self.cpu_percent, 1)` 사용

### 4.2 `ResourceGuard._get_cpu_percent()` 변경

**파일**: `services/chaos/safety_guard/resource_guard.py` L111-124

**현재 코드**:
```python
def _get_cpu_percent(self) -> float:
    try:
        return psutil.cpu_percent(interval=0.1)    # ← 100ms 블로킹
    except Exception as e:
        logger.warning(f"[ResourceGuard] Failed to get CPU percent: {e}")
        return 0.0
```

**변경 후**:
```python
def _get_cpu_percent(self) -> float:
    try:
        from selfhealing.services.system_metrics_cache import get_system_metrics_cache

        cache = get_system_metrics_cache()
        if cache.is_running():
            return cache.get_cpu_percent()         # ← ~0ms
    except Exception:
        pass

    # Fallback: 직접 측정 (캐시 미가동 시)
    try:
        return psutil.cpu_percent(interval=0.1)
    except Exception as e:
        logger.warning(f"[ResourceGuard] Failed to get CPU percent: {e}")
        return 0.0
```

**변경 근거**:
- X-Test 요청마다 100ms → ~0ms로 개선
- 캐시 미가동 시 기존 동작 100% 유지 (직접 psutil 호출)

### 4.3 `circuit_breaker/service.py` L606 변경

**파일**: `services/circuit_breaker/service.py` L602-609

**현재 코드**:
```python
try:
    import psutil
    snapshot["system_metrics"] = {
        "cpu_percent": psutil.cpu_percent(interval=None),  # ← 부정확 가능
        "memory_percent": psutil.virtual_memory().percent,
    }
except Exception:
    pass
```

**변경 후**:
```python
try:
    from selfhealing.services.system_metrics_cache import get_system_metrics_cache

    cache = get_system_metrics_cache()
    if cache.is_running():
        snapshot["system_metrics"] = {
            "cpu_percent": cache.get_cpu_percent(),
            "memory_percent": cache.get_memory_percent(),
        }
    else:
        import psutil
        snapshot["system_metrics"] = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": psutil.virtual_memory().percent,
        }
except Exception:
    pass
```

**변경 근거**:
- 캐시가 1초마다 `cpu_percent(interval=0.1)`을 호출하므로, `interval=None`의 "마지막 호출 없음" 문제 해결
- 캐시의 값은 최대 1초 전 측정값이며, CB OPEN 스냅샷 용도로 충분

### 4.4 Celery Task 스냅샷 — `snapshot_source` 개선

213에서 Celery Worker의 스냅샷에 `snapshot_source: "celery_worker"`를 붙이고 있다.
캐시 레이어 도입 후, EventBus 핸들러에서 **Web Server의 캐시 값을 Celery Task에 파라미터로 전달**하는 것이 가능해진다.

#### 4.4.1 `_on_circuit_breaker_opened_snapshot` 변경

**파일**: `services/event_bus/bus.py` L604-621 (현재)

```python
def _on_circuit_breaker_opened_snapshot(event: SelfHealingEvent) -> None:
    service_name = event.data.get("service_name", "unknown")
    try:
        from selfhealing.adapters.celery.tasks import collect_cb_open_snapshot

        collect_cb_open_snapshot.delay(
            service_name=service_name,
            event_timestamp=event.timestamp.isoformat(),
        )
```

**변경 후**:
```python
def _on_circuit_breaker_opened_snapshot(event: SelfHealingEvent) -> None:
    service_name = event.data.get("service_name", "unknown")
    try:
        from selfhealing.adapters.celery.tasks import collect_cb_open_snapshot

        # Web Server의 캐시된 시스템 메트릭을 사전 수집 (~0ms)
        web_metrics = None
        try:
            from selfhealing.services.system_metrics_cache import get_system_metrics_cache

            cache = get_system_metrics_cache()
            if cache.is_running():
                web_metrics = cache.get_snapshot_dict()
        except Exception:
            pass

        collect_cb_open_snapshot.delay(
            service_name=service_name,
            event_timestamp=event.timestamp.isoformat(),
            web_server_metrics=web_metrics,   # ← 새 파라미터
        )
```

#### 4.4.2 `collect_cb_open_snapshot` Celery Task 변경 — Worker 메트릭 병기

**파일**: `adapters/celery/tasks/circuit_breaker.py` L448-510

**설계 원칙**: Web Server 메트릭으로 **주 필드를 교체**하되, Worker 원본 값을 `worker_*` 접두사 필드로 **별도 보존**한다. 이렇게 해야 장애 원인 분석 시 Web Server와 Celery Worker 양쪽 리소스 상태를 모두 확인할 수 있다.

**네이밍 근거**:
- `worker_cpu_percent`, `worker_memory_percent`, `worker_memory_used_mb`, `worker_memory_available_mb` — 코드베이스 전체 검색 결과 **0건** (충돌 없음)
- `web_server_cache+worker` — 기존 `snapshot_source` 값(`"celery_worker"`: `circuit_breaker.py` L483, `postmortem.py` L656, L756)과 구분되며, 양쪽 출처가 모두 포함됨을 명시

**시그니처 변경**:
```python
def collect_cb_open_snapshot(
    self,
    service_name: str,
    event_timestamp: str,
    web_server_metrics: dict | None = None,   # ← 새 파라미터
) -> dict:
```

**본체 변경**:
```python
        snapshot = collect_system_snapshot()

        if web_server_metrics:
            # 1) Worker 원본 값을 별도 필드로 보존
            #    장애 분석 시 Worker 과부하가 복구 지연 원인일 수 있으므로 유실 방지
            snapshot["worker_cpu_percent"] = snapshot.get("cpu_percent")
            snapshot["worker_memory_percent"] = snapshot.get("memory_percent")
            snapshot["worker_memory_used_mb"] = snapshot.get("memory_used_mb")
            snapshot["worker_memory_available_mb"] = snapshot.get("memory_available_mb")

            # 2) 주 필드를 Web Server 캐시 값으로 교체
            #    Postmortem 분석의 주 대상은 Web Server (실제 장애 노드)
            snapshot["cpu_percent"] = web_server_metrics.get("cpu_percent", snapshot["cpu_percent"])
            snapshot["memory_percent"] = web_server_metrics.get("memory_percent", snapshot["memory_percent"])
            snapshot["memory_used_mb"] = web_server_metrics.get("memory_used_mb", snapshot.get("memory_used_mb", 0))
            snapshot["memory_available_mb"] = web_server_metrics.get("memory_available_mb", snapshot.get("memory_available_mb", 0))

            # 3) 출처 표시: 양쪽 데이터가 모두 존재함을 명시
            snapshot["snapshot_source"] = "web_server_cache+worker"
            snapshot["snapshot_note"] = "주 CPU/Memory=Web Server 캐시, worker_*=Celery Worker 측정값."
        else:
            snapshot["snapshot_source"] = "celery_worker"
            snapshot["snapshot_note"] = "Worker 노드의 CPU/Memory. Web Server와 다를 수 있음."
```

**결과 snapshot 구조 예시** (`web_server_metrics` 전달 시):
```python
{
    # 주 필드 — Web Server 값 (실제 장애 노드)
    "cpu_percent": 78.5,
    "memory_percent": 72.3,
    "memory_used_mb": 1482.1,
    "memory_available_mb": 565.9,

    # Worker 보존 필드 — Celery Worker 값
    "worker_cpu_percent": 45.2,
    "worker_memory_percent": 58.7,
    "worker_memory_used_mb": 1203.4,
    "worker_memory_available_mb": 844.6,

    # 출처 메타데이터
    "snapshot_source": "web_server_cache+worker",
    "snapshot_note": "주 CPU/Memory=Web Server 캐시, worker_*=Celery Worker 측정값.",

    # 기존 필드 유지
    "timestamp": "...",
    "captured_at": "open",
    "service": "payment",
    ...
}
```

**호환성**: snapshot은 단순 dict이고 `generate_postmortem_data()` (`store.py` L828)에서 `"system_snapshot": snapshot`으로 통째로 저장하므로 추가 필드가 자동으로 보존된다. 기존 소비자(`load_tests/scenarios/chaos/stage51_observability.py` L194-195)는 `snapshot.get("cpu_percent")`로 주 필드만 읽으므로 영향 없음.

#### 4.4.3 Postmortem Task에도 동일 적용

`_on_circuit_breaker_closed_postmortem` fallback과 `_on_emergency_recovery_completed_postmortem`에서 `.delay()` 호출 전에 `web_server_metrics` 수집 후 전달.
`process_individual_postmortem` task, `_process_emergency_postmortem` 함수에 `web_server_metrics: dict | None = None` 파라미터 추가.

**본체 변경** (양쪽 Task 공통 — §4.4.2와 동일 패턴):
```python
    snapshot = collect_system_snapshot()

    if web_server_metrics:
        # Worker 원본 값 보존
        snapshot["worker_cpu_percent"] = snapshot.get("cpu_percent")
        snapshot["worker_memory_percent"] = snapshot.get("memory_percent")
        snapshot["worker_memory_used_mb"] = snapshot.get("memory_used_mb")
        snapshot["worker_memory_available_mb"] = snapshot.get("memory_available_mb")
        # 주 필드를 Web Server 값으로 교체
        snapshot["cpu_percent"] = web_server_metrics.get("cpu_percent", snapshot["cpu_percent"])
        snapshot["memory_percent"] = web_server_metrics.get("memory_percent", snapshot["memory_percent"])
        snapshot["memory_used_mb"] = web_server_metrics.get("memory_used_mb", snapshot.get("memory_used_mb", 0))
        snapshot["memory_available_mb"] = web_server_metrics.get("memory_available_mb", snapshot.get("memory_available_mb", 0))
        snapshot["snapshot_source"] = "web_server_cache+worker"
        snapshot["snapshot_note"] = "주 CPU/Memory=Web Server 캐시, worker_*=Celery Worker 측정값."
    else:
        snapshot["snapshot_source"] = "celery_worker"
        snapshot["snapshot_note"] = "Worker 노드의 CPU/Memory. Web Server와 다를 수 있음."
```

**적용 위치**:
- `adapters/celery/tasks/postmortem.py` → `_process_cb_closed_postmortem()` (현재 L655: `snapshot["snapshot_source"] = "celery_worker"`)
- `adapters/celery/tasks/postmortem.py` → `_process_emergency_postmortem()` (현재 L756: `snapshot["snapshot_source"] = "celery_worker"`)

---

## 5. AppConfig.ready() 시작 등록

### 5.1 `adapters/django/apps.py` 변경

**기존 패턴 복제** (`_start_precomputed_cache_worker` 바로 아래):

```python
class SelfHealingConfig(AppConfig):
    # System Metrics Cache - 중복 실행 방지
    _metrics_cache_started: bool = False
    _metrics_cache_lock: threading.Lock = threading.Lock()

    def ready(self):
        ...
        # V3: Start pre-computed cache worker for L3 observability endpoints
        self._start_precomputed_cache_worker()

        # Start System Metrics Cache for non-blocking psutil access
        self._start_system_metrics_cache()     # ← 추가

        # Start Meta-Watchdog
        self._start_meta_watchdog()
        ...

    def _start_system_metrics_cache(self):
        """
        시스템 메트릭 캐시 워커 시작.

        psutil CPU/Memory를 1초마다 백그라운드에서 캐시하여
        collect_system_snapshot(), ResourceGuard 등의 100ms 블로킹을 제거.

        패턴: _start_precomputed_cache_worker() (apps.py L473-520)와 동일.
        Graceful Degradation: 실패 시 모든 소비자가 직접 psutil 호출로 fallback.
        """
        from selfhealing.settings.system_metrics_cache import (
            get_system_metrics_cache_settings,
        )

        settings = get_system_metrics_cache_settings()
        if not settings.enabled:
            logger.debug("[SelfHealing] System metrics cache disabled by settings")
            return

        with self._metrics_cache_lock:
            if self._metrics_cache_started:
                return
            SelfHealingConfig._metrics_cache_started = True

        try:
            from selfhealing.services.system_metrics_cache import (
                get_system_metrics_cache,
                start_system_metrics_cache,
            )

            # 설정값으로 글로벌 인스턴스 구성
            cache = get_system_metrics_cache()
            cache._refresh_interval = settings.refresh_interval
            cache._sample_interval = settings.sample_interval
            cache._max_age_seconds = settings.max_age_seconds

            start_system_metrics_cache()

            logger.info(
                f"[SelfHealing] System metrics cache started "
                f"(refresh={settings.refresh_interval}s, "
                f"sample={settings.sample_interval}s)"
            )

        except ImportError:
            logger.debug("[SelfHealing] system_metrics_cache module not available")
        except Exception as e:
            logger.warning(
                f"[SelfHealing] Failed to start system metrics cache (non-fatal): {e}. "
                f"Consumers will use direct psutil calls."
            )
```

### 5.2 시작 순서

`AppConfig.ready()`의 호출 순서에서 `_start_system_metrics_cache`는 `_start_precomputed_cache_worker` 바로 다음에 위치한다.

```
ready()
  ├── _connect_session_signals()
  ├── _log_env_snapshot()
  ├── _sync_hash_chain_on_startup()
  ├── _validate_startup_config()
  ├── _schedule_gauge_hydration()
  ├── _start_precomputed_cache_worker()
  ├── _start_system_metrics_cache()     ← 여기
  ├── _start_meta_watchdog()
  ├── _validate_secrets()
  └── _register_jwt_blacklist_hook()
```

---

## 6. Gunicorn prefork 환경에서의 동작

### 6.1 프로세스별 독립 인스턴스

Gunicorn `prefork` 모델에서 각 Worker 프로세스마다 `SystemMetricsCache` 인스턴스가 생성된다.

```
Master Process (fork 전 AppConfig.ready() 실행 가능)
  ├── Worker 1: SystemMetricsCache + daemon thread
  ├── Worker 2: SystemMetricsCache + daemon thread
  ├── Worker 3: SystemMetricsCache + daemon thread
  └── Worker 4: SystemMetricsCache + daemon thread
```

**이것이 문제인가?**

1. `PrecomputedCacheWorker`가 **동일한 상황에서 정상 운영 중**
   - `apps.py` L154: `self._start_precomputed_cache_worker()` — 각 Worker마다 실행
   - 현재 아무 문제 없음

2. `psutil.cpu_percent(interval=0.1)`은 `/proc/stat` 읽기
   - 운영체제 수준의 전역 통계이므로 프로세스 간 충돌 없음
   - 각 Worker가 독립적으로 같은 CPU 사용률을 관측

3. 리소스 사용량:
   - Worker 4개 × 1 daemon thread = 4개 추가 스레드
   - 각 스레드: 1초마다 100ms sleep → **스레드 시간 10% 점유**
   - GIL release 중 sleep이므로 Python 스레드에 영향 없음

### 6.2 Master Process fork 고려

Django의 `AppConfig.ready()`는 Gunicorn의 `--preload` 옵션 사용 시 Master에서 1번 실행된다.
fork 후 자식 프로세스에서 Timer 스레드가 복제되지 않는 문제가 있을 수 있으나:

- `PrecomputedCacheWorker`도 동일한 상황
- Gunicorn의 `post_fork` 훅으로 재시작하거나, `--preload` 미사용 시 각 Worker에서 `ready()` 실행

---

## 7. 변경하지 않는 부분과 근거

### 7.1 `psutil.virtual_memory()` — 캐시 불필요하지만 포함

`psutil.virtual_memory()`는 블로킹 없이 ~0ms에 반환된다. 별도 캐시가 불필요하지만, `cpu_percent`와 함께 측정하여 **동일 시점의 CPU + Memory 쌍**을 보장하기 위해 캐시에 포함한다.

### 7.2 DB 연결 수, Error Rate, Request Rate — 캐시 미포함

`collect_system_snapshot()` (base.py L651-692)의 나머지 값들:

| 값 | 소스 | 블로킹 여부 |
|----|------|-------------|
| `db_active_connections` | `repo.get_active_connection_count()` | DB 쿼리 수ms |
| `error_rate` | `error_budget_service.get_status()` | 인메모리/Redis |
| `request_rate` | `adapter.get_counter_value(...)` | 인메모리 |

이 값들은 블로킹이 무시할 수준(수ms)이며, 호출 빈도도 낮으므로 캐시 대상에서 제외한다.

### 7.3 Celery Worker에서의 SystemMetricsCache

Celery Worker에서는 `AppConfig.ready()`가 실행되지 않으므로 캐시가 시작되지 않는다.
`collect_system_snapshot()`의 fallback으로 직접 `psutil.cpu_percent(interval=0.1)`을 호출한다.
→ **Celery Worker에서는 기존 동작 그대로** (블로킹이지만 Worker 스레드이므로 무관)

---

## 8. 파일 변경 목록

### 8.1 새로 생성하는 파일

| 파일 | 내용 |
|------|------|
| `services/system_metrics_cache.py` | `SystemMetricsCache`, `CachedMetrics`, 모듈 API |
| `settings/system_metrics_cache.py` | `SystemMetricsCacheSettings` Pydantic v2 설정 |

### 8.2 수정하는 파일

| 파일 | 변경 내용 | 영향 범위 |
|------|-----------|-----------|
| `api/django/views/xtest/base.py` L637-641 | `collect_system_snapshot()` 캐시 우선 조회 + fallback | 스냅샷 전체 |
| `services/chaos/safety_guard/resource_guard.py` L111-124 | `_get_cpu_percent()` 캐시 우선 + fallback | X-Test 안전성 체크 |
| `services/circuit_breaker/service.py` L602-609 | 캐시에서 CPU/Memory 조회 + fallback | CB OPEN 스냅샷 |
| `adapters/django/apps.py` | `_start_system_metrics_cache()` 추가 | 서버 시작 |
| `services/event_bus/bus.py` L604-621, L1260 | 핸들러에서 `web_server_metrics` 사전 수집 | Celery Task 파라미터 |
| `adapters/celery/tasks/circuit_breaker.py` L448 | `web_server_metrics` 파라미터 추가 | CB OPEN 스냅샷 Task |
| `adapters/celery/tasks/postmortem.py` L550 | `web_server_metrics` 파라미터 추가 | Postmortem Task |

### 8.3 테스트

| 테스트 파일 | 내용 | 상태 |
|-------------|------|------|
| 신규: `packages/selfhealing-python/tests/unit/services/test_system_metrics_cache.py` | `SystemMetricsCache` + `CachedMetrics` + `SystemMetricsCacheSettings` + 모듈 API + 소비자 연동 + 스레드 안전성 단위 테스트 (59개) | ✅ 완료 |
| 수정: `tests/self_healing/api/test_xtest_*.py` | `collect_system_snapshot` mock `return_value`에 `metrics_source: "cache"` 추가 (5개 파일: retry, rate_limit, idempotency, observability, integration) | ✅ 완료 |
| 수정: `packages/selfhealing-python/tests/unit/chaos/test_xtest_resource_guard.py` | `_get_cpu_percent()` 캐시 → psutil fallback 경로 테스트 7개 추가 (총 23개) | ✅ 완료 |

---

## 9. 구현 순서

| 단계 | 작업 | 의존성 |
|------|------|--------|
| 1 | `settings/system_metrics_cache.py` 생성 | 없음 |
| 2 | `services/system_metrics_cache.py` 생성 | 단계 1 |
| 3 | `SystemMetricsCache` 단위 테스트 작성 | 단계 2 |
| 4 | `adapters/django/apps.py` — `_start_system_metrics_cache()` 추가 | 단계 2 |
| 5 | `api/django/views/xtest/base.py` — `collect_system_snapshot()` 변경 | 단계 2 |
| 6 | `services/chaos/safety_guard/resource_guard.py` — `_get_cpu_percent()` 변경 | 단계 2 |
| 7 | `services/circuit_breaker/service.py` L602-609 변경 | 단계 2 |
| 8 | `services/event_bus/bus.py` — 핸들러에서 `web_server_metrics` 사전 수집 | 단계 2 |
| 9 | `adapters/celery/tasks/circuit_breaker.py` — `web_server_metrics` 파라미터 추가 | 단계 8 |
| 10 | `adapters/celery/tasks/postmortem.py` — `web_server_metrics` 파라미터 추가 | 단계 8 |
| 11 | 기존 테스트 수정 + 통합 테스트 | 단계 4-10 |

---

## 10. 예상 효과

### 10.1 레이턴시 개선

| 소비자 | Before | After |
|--------|--------|-------|
| `collect_system_snapshot()` (Web Server) | 100ms (fallback 경로) | ~0ms (캐시) |
| `ResourceGuard._get_cpu_percent()` (X-Test 요청) | 100ms | ~0ms (캐시) |
| `circuit_breaker/service.py` CB OPEN | ~0ms (`interval=None`, 부정확) | ~0ms (캐시, 정확) |
| Celery Task의 스냅샷 CPU/Memory | Worker 노드 값 | **Web Server 값** (핸들러에서 전달) |
| `collect_system_snapshot()` (Celery Worker) | 100ms (Worker 스레드) | 100ms (변경 없음) |

### 10.2 데이터 정확도 개선

| 항목 | Before (213 상태) | After |
|------|-------------------|-------|
| CB OPEN 스냅샷 CPU/Memory | `snapshot_source: "celery_worker"` | `snapshot_source: "web_server_cache+worker"` |
| Postmortem 스냅샷 CPU/Memory | `snapshot_source: "celery_worker"` | `snapshot_source: "web_server_cache+worker"` |
| Postmortem 분석 시 | "이 CPU가 Web? Worker?" 확인 필요 | **Web Server + Worker 양쪽 값 모두 제공** |
| CB OPEN 스냅샷의 `snapshot_note` | "Worker 노드의 CPU/Memory. Web Server와 다를 수 있음." | "주 CPU/Memory=Web Server 캐시, worker_*=Celery Worker 측정값." |
| Worker 리소스 상태 | 주 필드에 Worker 값만 존재 | `worker_cpu_percent`, `worker_memory_percent` 등으로 보존 |

### 10.3 리소스 비용

| 리소스 | 비용 |
|--------|------|
| 스레드 | Gunicorn Worker당 1개 daemon thread |
| CPU | 1초당 100ms sleep (10%) — GIL release 중이므로 Python 스레드 무영향 |
| 메모리 | `CachedMetrics` 인스턴스 ~200 bytes |

---

## 11. Fallback / 롤백 전략

### 11.1 Fail-Open 설계

모든 소비자는 다음 순서로 동작한다:

```
1. 캐시에서 값 읽기 시도
2. 캐시 미가동/예외 → 직접 psutil 호출 (기존 동작)
3. psutil도 실패 → 0.0 반환 또는 에러 필드 설정
```

**캐시가 완전히 제거되더라도 시스템 동작에 영향 없음** — 모든 변경에 fallback 경로가 포함.

### 11.2 설정으로 비활성화

```bash
# .env
SELFHEALING_SYSTEM_METRICS_CACHE_ENABLED=false
```

→ `AppConfig.ready()`에서 캐시가 시작되지 않음
→ 모든 소비자가 자동으로 직접 psutil 호출 fallback

### 11.3 코드 롤백

- `services/system_metrics_cache.py`, `settings/system_metrics_cache.py` 삭제
- 소비자의 캐시 조회 try/except 블록이 `ImportError` → fallback으로 진행
- **`apps.py`의 `_start_system_metrics_cache()` 호출만 제거**하면 사실상 비활성화

---

## 12. 참조 문서

| 문서 | 관련 내용 |
|------|-----------|
| 213_EVENTBUS_HANDLER_CELERY_DELEGATION_PLAN.md | EventBus 핸들러 Celery 위임 (선행), 리뷰 2: Worker CPU 한계 |
| 148_POSTMORTEM_TIMELINE_SNAPSHOT.md | Postmortem 스냅샷 설계 |
| 23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md | CB 알림 설계 |

---

## 13. 참조 코드 위치 요약

| 항목 | 파일 | 라인 |
|------|------|------|
| `collect_system_snapshot()` | `api/django/views/xtest/base.py` | L620-699 |
| `psutil.cpu_percent(interval=0.1)` 호출 | `api/django/views/xtest/base.py` | L637 |
| `ResourceGuard._get_cpu_percent()` | `services/chaos/safety_guard/resource_guard.py` | L111-124 |
| `psutil.cpu_percent(interval=None)` | `services/circuit_breaker/service.py` | L606 |
| `PrecomputedCacheWorker` (참조 패턴) | `services/precomputed_cache/worker.py` | L39-120 |
| `PrecomputedCacheWorker.start()` 시작 | `adapters/django/apps.py` | L473-520 |
| `_start_precomputed_cache_worker()` | `adapters/django/apps.py` | L154 |
| `BatchMetricRecorder` (참조 패턴) | `services/metrics/registry.py` | L60-120 |
| `AsyncHealingLogger` (참조 패턴) | `utils/async_logger.py` | L320-355 |
| `collect_cb_open_snapshot` Task | `adapters/celery/tasks/circuit_breaker.py` | L448-510 |
| `process_individual_postmortem` Task | `adapters/celery/tasks/postmortem.py` | L550-620 |
| `_on_circuit_breaker_opened_snapshot` 핸들러 | `services/event_bus/bus.py` | L604-621 |
| `_on_circuit_breaker_closed_postmortem` 핸들러 | `services/event_bus/bus.py` | L800-843 |
| `_on_emergency_recovery_completed_postmortem` 핸들러 | `services/event_bus/bus.py` | L1220-1270 |
| `_create_individual_postmortem` (동기 fallback) | `services/event_bus/bus.py` | L895-960 |
| `_create_emergency_postmortem_sync` (동기 fallback) | `services/event_bus/bus.py` | L1280-1370 |
| `PrecomputedCacheSettings` (참조 패턴) | `settings/precomputed_cache.py` | L24-60 |
| `ResourceGuardSettings` (참조 패턴) | `settings/resource_guard.py` | L22-60 |
| `CgroupResourceMonitor` | `core/resource_monitor.py` | L23-60 |
| `ResourceCheckResult.to_response_dict()` (round 선례) | `services/chaos/safety_guard/resource_guard.py` | L83-87 |
| `generate_postmortem_data()` snapshot 저장 | `services/postmortem/store.py` | L730-828 |
| 스냅샷 소비자 (load test) | `load_tests/scenarios/chaos/stage51_observability.py` | L194-195 |

---

## 14. 리뷰 반영 이력

### 14.1 리뷰 1: Startup Race Condition (Cold Start 방지) — §3.1 `start()` 수정

**상태**: 🔧 수정 필수 → 반영 완료

**문제**:
- 기존 `start()`는 `_schedule_refresh()`만 호출하여 Timer를 스케줄링
- 첫 `_do_refresh()` 실행 전까지 최대 `refresh_interval + sample_interval` (기본 1.1초) 동안 `CachedMetrics(cpu_percent=0.0, measured_at="")`이 반환
- `ResourceGuard._get_cpu_percent()` → `cache.get_cpu_percent()` → `0.0` → **CPU 과부하 상태에서도 X-Test 통과**

**조치**:
- `start()` 내에서 `_schedule_refresh()` 호출 전에 `self._do_refresh()`를 **동기로 1회 호출**
- 100ms 비용은 `AppConfig.ready()` 시작 시 1회만 발생하므로 무시 가능

**PrecomputedCacheWorker와의 차이점**:
- `PrecomputedCacheWorker` (`worker.py` L57-62)는 캐시 miss 시 `compute_fn`을 직접 호출하는 fallback이 내장되어 있어 cold start 문제 없음
- `SystemMetricsCache`에는 소비자가 직접 데이터를 생성하는 fallback이 없으므로 **`start()` 시 동기 초기화 필수**

**소비자 측 `measured_at == ""` 가드 불필요**:
- `start()`에서 동기 호출하면 `measured_at`가 항상 채워진 상태에서 소비자가 접근
- 소비자 코드에 추가 분기를 넣으면 과잉 방어가 되어 코드 복잡도만 증가

### 14.2 리뷰 2: 메트릭 정밀도 통일 — §3.1 `_do_refresh()` + §4.1 fallback 수정

**상태**: 🔧 수정 → 반영 완료

**문제**:
- 기존 설계는 raw float를 그대로 캐시 저장 (`cpu=45.23456789`, `memory_used_mb=1482.567890625`)
- 소비자마다 round 시점이 다르면 동일 시점 메트릭이 다른 정밀도로 저장될 수 있음

**코드베이스 round 관행 조사**:

| 위치 | 코드 | 자릿수 |
|------|------|--------|
| `resource_guard.py` L86-87 | `round(self.cpu_percent, 1)`, `round(self.memory_percent, 1)` | **1** |
| `memory_test_views.py` L333 | `round(cls.get_memory_percent() * 100, 2)` | 2 |
| `error_budget/models.py` L88 | `round(self.budget_remaining_percent, 2)` | 2 |

- `ResourceGuard`가 `SystemMetricsCache`의 **직접적인 소비자**이므로 `round(val, 1)` 채택
- `round(round(x, 1), 1) == round(x, 1)` (멱등) → ResourceGuard에서 중복 round 무해

**조치**:
1. `_do_refresh()`에서 `CachedMetrics` 생성 시 `round(cpu, 1)`, `round(memory.percent, 1)`, `round(memory_mb, 1)` 적용
2. `collect_system_snapshot()` fallback 경로(직접 psutil 호출)에서도 **동일하게 `round(val, 1)` 적용** → 캐시/fallback 간 정밀도 일관성 보장

### 14.3 리뷰 3: Celery Worker 메트릭 병기 — §4.4.2, §4.4.3, §10.2 수정

**상태**: 🔧 수정 필수 → 반영 완료

**문제**:
- 기존 §4.4.2 설계는 `web_server_metrics`로 `cpu_percent` 등 주 필드를 **덮어쓰기**
- Worker의 원본 CPU/Memory 완전 유실 → 장애 분석 시 Worker 과부하 여부 확인 불가

**조치**:
1. Worker 원본 값을 `worker_cpu_percent`, `worker_memory_percent`, `worker_memory_used_mb`, `worker_memory_available_mb`로 **별도 보존**
2. 주 필드(`cpu_percent` 등)를 Web Server 값으로 교체 (Postmortem 분석의 주 대상은 Web Server)
3. `snapshot_source`를 `"web_server_cache+worker"`로 변경 (양쪽 데이터 존재를 명시)

**네이밍 충돌 검증**:
- `worker_cpu_percent` — 코드베이스 전체 검색 **0건** (충돌 없음)
- `worker_memory_percent` — 코드베이스 전체 검색 **0건** (충돌 없음)
- `worker_memory_used_mb` — 코드베이스 전체 검색 **0건** (충돌 없음)
- `worker_memory_available_mb` — 코드베이스 전체 검색 **0건** (충돌 없음)
- `web_server_cache+worker` — 기존 `snapshot_source` 값(`"celery_worker"`)과 구분됨

**호환성**:
- snapshot은 단순 dict → 필드 추가에 제약 없음
- `generate_postmortem_data()` (`store.py` L828): `"system_snapshot": snapshot` 통째로 저장 → 추가 필드 자동 보존
- 기존 소비자(`stage51_observability.py` L194-195): `snapshot.get("cpu_percent")` → 주 필드만 읽음 → **영향 없음**
- `Postmortem Task` (`circuit_breaker.py`, `postmortem.py`)에도 동일 패턴 적용
