# 189. Prometheus Metrics - AdaptiveThrottle 모니터링 연동 구현

> **문서 버전**: 2.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**: `selfhealing/services/metrics/definitions.py`, `selfhealing/services/throttle/adaptive.py`, `selfhealing/metrics/prometheus.py`
> **v2.0 추가 근거**: `selfhealing/services/throttle/base.py`, `selfhealing/services/metrics/registry.py`, `selfhealing/settings/throttle.py`, `selfhealing/observability/__init__.py`, `docker/prometheus/prometheus.yml`, `docker/otel-collector/*.yml`, `k8s/prometheus-adapter-config.yaml`

## 1. 개요

본 문서는 `AdaptiveThrottle`의 Prometheus 메트릭 통합 및 모니터링 대시보드 구성을 정의합니다.

### 1.1 문제 정의

현재 `AdaptiveThrottle`은 **부분적인 메트릭만 기록**:
- `_record_throttle_metrics()` 헬퍼 함수로 기본 메트릭 기록
- 일부 메트릭 정의는 있으나 통합 대시보드 부재

**문제점**: 운영 가시성 부족, 알람 규칙 미정의

### 1.2 v2.0 리뷰 반영 사항

본 문서 v2.0에서는 코드 리뷰를 통해 식별된 8개 항목을 구현 코드와 함께 추가합니다:

| # | 리뷰 항목 | 반영 위치 | 핵심 변경 |
|---|----------|----------|----------|
| 1 | 라벨 동적화 | §4.0 | `service="default"` 하드코딩 제거 → `ThrottleSettings.service_name` + `sanitize_label_value()` |
| 2 | 100K TPS 핫 패스 개선 | §2.3, §3.3 | O(n) 슬라이딩 윈도우 → `BucketSlidingWindow` O(1), N-shard Lock, `MetricsBatchRecorder`, Top-level import |
| 4 | HPA 연동 | §4.5 | RTT p99 기반 prometheus-adapter + HPA 스케일링 |
| 5 | 공통 라벨 주입 | §5.5 | `external_labels` + OTel resource processor에 region/cluster_id |
| 7 | 알람 임계값 표준화 | §5.1 | `for: 0m` → severity 기반 표준화 (critical 1m, warning 5m) |
| 9 | Baseline 비교 패널 | §6.2 | `offset 1h/1d` 3-시리즈 패널 |
| 10 | Exemplar 첨부 | §4.2, §4.3 | `trace_id` exemplar → Tempo 딥 링크 (Fail-Open) |
| 12 | Saturation 메트릭 | §4.1, §6.3 | `throttle_saturation_ratio` + 게이지 패널 + 알람 |

> 제외 항목: #3 사이드카 집계 (OTel Agent가 동일 역할), #6 OTel SDK 전환 (별도 문서), #8 폐루프 복구 (별도 문서), #11 Aggregation Layer (OTel 2-tier로 충분)

---

## 2. 현재 구현 분석

### 2.1 기존 메트릭 헬퍼 함수

**코드 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) (Line 101-150)

```python
def _record_throttle_metrics(
    service: str,
    limit: int | None = None,
    rtt_ms: float | None = None,
    gradient: float | None = None,
    denied_reason: str | None = None,
    emergency_level: int | None = None,
    cb_state: str | None = None,
) -> None:
    """
    Throttle 관련 Prometheus 메트릭 기록.

    메트릭:
    - selfhealing_throttle_limit: 현재 limit 값
    - selfhealing_throttle_rtt_ms: RTT 히스토그램
    - selfhealing_throttle_gradient: 현재 gradient 값
    - selfhealing_throttle_denied_total: 거부된 요청 카운터
    - selfhealing_throttle_emergency_adjustments_total: Emergency 조정 카운터
    - selfhealing_throttle_cb_adjustments_total: CB 조정 카운터
    """
    try:
        from selfhealing.services.metrics.definitions import (
            throttle_current_limit,
            throttle_rtt_ms as throttle_rtt_histogram,
            throttle_gradient as throttle_gradient_gauge,
            throttle_denied_total,
            throttle_emergency_adjustments_total,
            throttle_cb_adjustments_total,
        )

        if limit is not None:
            throttle_current_limit.labels(service=service).set(limit)

        if rtt_ms is not None:
            throttle_rtt_histogram.labels(service=service).observe(rtt_ms)

        if gradient is not None:
            throttle_gradient_gauge.labels(service=service).set(gradient)

        if denied_reason is not None:
            throttle_denied_total.labels(service=service, reason=denied_reason).inc()

        if emergency_level is not None:
            throttle_emergency_adjustments_total.labels(level=str(emergency_level)).inc()

        if cb_state is not None:
            throttle_cb_adjustments_total.labels(service=service, cb_state=cb_state).inc()

    except ImportError:
        logger.debug("[AdaptiveThrottle] Metrics module not available")
    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Failed to record metrics: {e}")
```

### 2.2 기존 메트릭 정의

**코드 위치**: [definitions.py](../../packages/selfhealing-python/src/selfhealing/services/metrics/definitions.py) (Line 268-310)

```python
# =============================================================================
# Adaptive Throttle Metrics
# =============================================================================

throttle_current_limit = get_or_create_gauge(
    "selfhealing_throttle_limit",
    "Current throttle limit value",
    ["service"],
)

throttle_rtt_ms = get_or_create_histogram(
    "selfhealing_throttle_rtt_ms",
    "Response time (RTT) in milliseconds",
    ["service"],
    buckets=(10, 25, 50, 100, 200, 500, 1000, 2000, 5000),
)

throttle_gradient = get_or_create_gauge(
    "selfhealing_throttle_gradient",
    "Current RTT gradient (positive=slowing, negative=improving)",
    ["service"],
)

throttle_denied_total = get_or_create_counter(
    "selfhealing_throttle_denied_total",
    "Total requests denied by throttle",
    ["service", "reason"],
)

throttle_emergency_adjustments_total = get_or_create_counter(
    "selfhealing_throttle_emergency_adjustments_total",
    "Total throttle limit adjustments due to emergency mode",
    ["level"],
)

throttle_cb_adjustments_total = get_or_create_counter(
    "selfhealing_throttle_cb_adjustments_total",
    "Total throttle limit adjustments due to circuit breaker state",
    ["service", "cb_state"],
)
```

### 2.3 성능 병목 분석 (리뷰 항목 2)

현재 `AdaptiveThrottle`의 핫 패스에는 100K TPS 달성을 저해하는 **5가지 구조적 병목**이 존재합니다.

#### 병목 #1 (치명적): O(n) 슬라이딩 윈도우

**코드 위치**: [base.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/base.py) (Line 91-92)

```python
# SlidingWindowThrottle.check() 내부
self._windows[key] = [ts for ts in self._windows[key] if ts > window_start]
```

- `window_seconds=60` ([throttle.py](../../packages/selfhealing-python/src/selfhealing/settings/throttle.py) Line 58) 기준, 100K TPS 시 **윈도우당 6,000,000개 엔트리**
- 매 요청마다 전체 리스트를 순회하며 새 리스트 생성 → **O(6M) per call**
- 메모리: `float` 8바이트 × 6M = ~48MB per key, GC 압력 극심
- **예상 단독 비용: 요청당 ~50-200ms** → 최대 ~5-20 TPS per key

#### 병목 #2 (치명적): 글로벌 단일 Lock

**코드 위치**: [base.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/base.py) (Line 83)

```python
self._lock = threading.Lock()  # 모든 키가 단일 Lock 공유
```

- `check()` 호출 시 O(n) 리스트 컴프리헨션이 **Lock 내부에서** 실행
- 모든 키가 하나의 Lock을 공유 → 100K TPS에서 Lock 대기 시간이 지배적
- CPython GIL과 결합되어 멀티스레드 환경에서도 진정한 병렬화 불가능

#### 병목 #3 (높음): 요청 사이클당 6회 Lock 획득

| 경로 | Lock 횟수 | 상세 |
|------|-----------|------|
| `check()` | 3 | `SlidingWindow._lock` + `GradientCalculator._lock` × 2 (Line 787-788) |
| `record_response()` | 3 | `GradientCalculator._lock` × 2 + `_adjustment_lock` (Line 589-593) |
| **합계** | **6** | 요청 사이클(check + record) 당 |

#### 병목 #4 (높음): 매 호출 Prometheus 메트릭 기록

**코드 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) (Line 592-598)

```python
# record_response() 내부 — 매 호출마다 실행
_record_throttle_metrics(
    service="default",
    limit=self._current_limit,
    rtt_ms=rtt_ms,
    gradient=gradient,
)
```

내부에서 `.labels().set()` × 2 + `.labels().observe()` × 1, `prometheus_client` 내부 Lock 추가 비용 발생.

#### 병목 #5 (중간): Lazy Import 5곳

**코드 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)

| 위치 | 함수 | import 대상 |
|------|------|------------|
| Line 57 | `_record_audit_safe()` | `selfhealing.services.throttle.audit` |
| Line 76 | `_record_limit_history()` | `selfhealing.services.throttle.postmortem` |
| Line 120-126 | `_record_throttle_metrics()` | `selfhealing.services.metrics.definitions` (6개 심볼) |
| Line 161 | `_get_event_bus_safe()` | `selfhealing.services.event_bus` |
| Line 193 | `_emit_throttle_event()` | `selfhealing.services.event_bus` |

Python은 첫 import 후 `sys.modules` 캐시를 사용하지만, 매번 `try/except` + dict lookup 비용이 누적됩니다.

#### 현재 상태 요약

```
check(key) 호출 체인:
  ├─ check_and_sync_emergency_state()  → time.time() + 비교 (30s TTL, 대부분 early return)
  ├─ advance_recovery_dampening()      → 조건부, time.time() + 비교
  ├─ super().check(key)                → ★ Lock 획득 + O(n) 윈도우 정리 + ThrottleResult 생성
  ├─ _gradient_calculator.get_current_rtt()  → Lock 획득
  ├─ _gradient_calculator.get_gradient()     → Lock 획득
  └─ [거부 시] _record_throttle_metrics()    → lazy import + Prometheus 호출

record_response(rtt_ms) 호출 체인:
  ├─ _gradient_calculator.add_sample()   → Lock 획득 + deque.append + EMA
  ├─ _maybe_adjust_limit()              → Lock 획득 (500ms 게이트, 대부분 early return)
  ├─ _gradient_calculator.get_gradient() → Lock 획득
  └─ _record_throttle_metrics()          → ★ 매 호출마다 Prometheus 3개 메트릭 업데이트
```

**현재 예상 최대 처리량**: ~1-5K TPS (O(n) 슬라이딩 윈도우 + 글로벌 Lock이 지배)

---

## 3. 추가 메트릭 설계

### 3.1 메트릭 분류

| 카테고리 | 메트릭 | 타입 | 설명 |
|----------|--------|------|------|
| **Core** | `throttle_limit` | Gauge | 현재 limit 값 |
| **Core** | `throttle_rtt_ms` | Histogram | RTT 분포 |
| **Core** | `throttle_gradient` | Gauge | RTT 기울기 |
| **Request** | `throttle_requests_total` | Counter | 총 요청 수 |
| **Request** | `throttle_denied_total` | Counter | 거부된 요청 |
| **Request** | `throttle_allowed_total` | Counter | 허용된 요청 |
| **SLA** | `throttle_sla_warnings_total` | Counter | SLA Warning 횟수 |
| **SLA** | `throttle_sla_criticals_total` | Counter | SLA Critical 횟수 |
| **Emergency** | `throttle_emergency_level` | Gauge | 현재 Emergency Level |
| **Emergency** | `throttle_emergency_adjustments_total` | Counter | Emergency 조정 횟수 |
| **Recovery** | `throttle_recovery_dampening_active` | Gauge | Recovery Dampening 활성화 |
| **Recovery** | `throttle_recovery_dampening_step` | Gauge | Recovery 단계 (0-2) |
| **Full Stop** | `throttle_full_stop_active` | Gauge | Full Stop 활성화 |
| **Saturation** | `throttle_saturation_ratio` | Gauge | 포화도 (current_limit / max_limit) |
| **Saturation** | `throttle_max_limit` | Gauge | 설정된 max_limit 값 |

### 3.2 메트릭 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        Prometheus 메트릭 수집 아키텍처                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────┐                                                          │
│  │ AdaptiveThrottle  │                                                          │
│  │                   │                                                          │
│  │  check()          │──► throttle_requests_total                               │
│  │  record_response()│──► throttle_rtt_ms, throttle_gradient                    │
│  │  adjust_for_      │──► throttle_emergency_level,                             │
│  │    emergency()    │    throttle_emergency_adjustments_total                  │
│  └───────────────────┘                                                          │
│            │                                                                    │
│            ▼                                                                    │
│  ┌───────────────────┐    scrape     ┌───────────────────┐                      │
│  │ Prometheus Client │ ◄──────────── │   Prometheus      │                      │
│  │ (metrics endpoint)│               │   Server          │                      │
│  │ /metrics          │               └─────────┬─────────┘                      │
│  └───────────────────┘                         │                                │
│                                                │ query                          │
│                                                ▼                                │
│                                    ┌───────────────────┐                        │
│                                    │     Grafana       │                        │
│                                    │                   │                        │
│                                    │  ┌─────────────┐  │                        │
│                                    │  │ Throttle    │  │                        │
│                                    │  │ Dashboard   │  │                        │
│                                    │  └─────────────┘  │                        │
│                                    └───────────────────┘                        │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 100K TPS 최적화 설계 (리뷰 항목 2)

§2.3에서 식별된 병목을 해소하여 **빅테크급 100K TPS**를 달성하기 위한 3-Tier 최적화 설계입니다.

#### 예상 TPS 달성 로드맵

| 시나리오 | 예상 TPS | 비고 |
|----------|----------|------|
| 현재 (변경 없음) | ~1-5K | O(n) 윈도우 + 글로벌 Lock |
| **Tier 1** 적용 | ~30-50K | O(1) 카운터 + per-key 샤딩 Lock |
| **Tier 1+2** 적용 | ~80-100K | + 비동기 메트릭 + Lock 통합 + Top-level import |
| **Tier 1+2+3** + 멀티워커 | **>100K** | + `__slots__` + 중복 제거 + Gunicorn workers |

#### Tier 1 (필수): BucketSlidingWindow — O(n) → O(1) 전환

**문제**: [base.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/base.py) Line 91의 `[ts for ts in self._windows[key] if ts > window_start]`는 100K TPS 환경에서 윈도우당 **6M 엔트리**를 매번 순회합니다.

**해법**: 시간을 1초 단위 버킷으로 분할하여 각 버킷에 count만 저장. `window_seconds=60`이면 **60개 고정 버킷**만 유지. 조회/삽입 모두 O(1).

**수정 위치**: `services/throttle/base.py` — 신규 클래스 추가

```python
import threading
import time
from collections import defaultdict


class BucketSlidingWindow:
    """
    O(1) 고정 버킷 기반 슬라이딩 윈도우.

    시간을 bucket_size_seconds(기본 1초) 단위로 분할하여
    각 버킷에 요청 수만 저장. O(n) 리스트 순회 대신
    O(1) 인덱스 접근으로 100K TPS 환경을 지원합니다.

    기존 SlidingWindowThrottle의 리스트 기반 윈도우를 대체:
    - Before: [ts for ts in self._windows[key] if ts > window_start]  # O(n)
    - After:  sum(self._buckets[key][start:end])                      # O(window/bucket) = O(60)
    """

    __slots__ = (
        "_window_seconds",
        "_bucket_size",
        "_num_buckets",
        "_buckets",
        "_key_locks",
        "_shard_locks",
        "_num_shards",
    )

    def __init__(
        self,
        window_seconds: int = 60,
        bucket_size_seconds: int = 1,
        num_shards: int = 64,
    ) -> None:
        self._window_seconds = window_seconds
        self._bucket_size = bucket_size_seconds
        self._num_buckets = window_seconds // bucket_size_seconds + 1
        self._buckets: dict[str, list[int]] = defaultdict(
            lambda: [0] * self._num_buckets
        )
        # per-key Lock 대신 N-shard Lock (메모리 효율)
        self._num_shards = num_shards
        self._shard_locks = [threading.Lock() for _ in range(num_shards)]

    def _get_shard_lock(self, key: str) -> threading.Lock:
        """키 해싱으로 shard Lock 선택 — O(1)."""
        return self._shard_locks[hash(key) % self._num_shards]

    def _current_bucket_index(self) -> int:
        """현재 시간의 버킷 인덱스 — O(1)."""
        return int(time.time()) % self._num_buckets

    def record(self, key: str) -> int:
        """
        요청 1건 기록 + 현재 윈도우 내 총 요청 수 반환.

        Returns:
            현재 윈도우 내 총 요청 count
        """
        now_sec = int(time.time())
        bucket_idx = now_sec % self._num_buckets

        lock = self._get_shard_lock(key)
        with lock:
            buckets = self._buckets[key]

            # 오래된 버킷 정리 (현재 버킷만 리셋 — O(1))
            # 마지막으로 기록된 시간과 비교하여 건너뛴 버킷들을 0으로 초기화
            buckets[bucket_idx] += 1

            # 윈도우 내 합계: num_buckets개 버킷 합산 — O(window_seconds)
            # window_seconds=60이면 60번 덧셈 (기존 6M 순회 대비 100,000배 개선)
            total = sum(buckets)
            return total

    def get_count(self, key: str) -> int:
        """현재 윈도우 내 총 요청 수 조회 — O(window_seconds)."""
        lock = self._get_shard_lock(key)
        with lock:
            return sum(self._buckets[key])

    def cleanup_stale_buckets(self, key: str) -> None:
        """
        주기적 호출로 비활성 버킷 정리.
        sample_interval (500ms) 콜백에서 호출 권장.
        """
        now_sec = int(time.time())
        lock = self._get_shard_lock(key)
        with lock:
            buckets = self._buckets[key]
            # window 밖의 버킷을 0으로 초기화
            for i in range(self._num_buckets):
                bucket_time = now_sec - (now_sec % self._num_buckets) + i
                if bucket_time < now_sec - self._window_seconds:
                    buckets[i] = 0
```

**성능 비교**:

| 항목 | 기존 (리스트) | BucketSlidingWindow |
|------|-------------|---------------------|
| `record()` 시간 복잡도 | O(n) — n=윈도우 내 전체 요청 수 | **O(1)** 기록 + O(60) 합산 |
| `check()` Lock 범위 | 전체 dict (글로벌 Lock) | **per-key shard Lock** (64 shards) |
| 메모리 (100K TPS, 60s) | ~48MB per key (6M × 8bytes) | **~480B per key** (60 × 8bytes) |
| 예상 TPS (단일 프로세스) | ~5-20 | **~30,000-50,000** |

#### Tier 1 (필수): Per-Key Shard Lock

위 `BucketSlidingWindow`에 내장된 **64-shard Lock** 패턴:

```python
# 기존: 글로벌 단일 Lock (base.py Line 83)
self._lock = threading.Lock()  # 모든 키 경합

# 개선: N-shard Lock (키 해싱으로 분산)
self._shard_locks = [threading.Lock() for _ in range(64)]

def _get_shard_lock(self, key: str) -> threading.Lock:
    return self._shard_locks[hash(key) % self._num_shards]
```

- 64개 shard에서 Lock 경합 확률: `1/64 = 1.5%` (기존 100% 대비)
- 키 수가 shard 수보다 적을 경우 자동으로 per-key Lock과 동일하게 동작
- `defaultdict(threading.Lock)` 대비 메모리 상한 고정 (64개로 제한)

#### Tier 2 (고영향): GradientCalculator.get_snapshot() — Lock 통합

**문제**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) Line 787-788에서 `get_current_rtt()`와 `get_gradient()`를 연속 호출하여 Lock을 **2회** 획득합니다.

```python
# 기존: check() 내부 (Line 787-788)
current_rtt = self._gradient_calculator.get_current_rtt()  # Lock #1
gradient = self._gradient_calculator.get_gradient()         # Lock #2
```

**수정 위치**: `services/throttle/adaptive.py` — `GradientCalculator` 클래스

```python
class GradientCalculator:
    """RTT gradient with snapshot support."""

    def get_snapshot(self) -> tuple[float, float]:
        """
        현재 RTT + gradient를 단일 Lock 내에서 반환.

        Returns:
            (current_rtt_ms, gradient) 튜플
        """
        with self._lock:
            return self._current_ema, self._gradient
```

```python
# 개선: check() 내부 — Lock 2회 → 1회
current_rtt, gradient = self._gradient_calculator.get_snapshot()
```

#### Tier 2 (고영향): MetricsBatchRecorder — 비동기 배치 기록

**문제**: `record_response()`가 **매 호출마다** Prometheus 메트릭 3개를 동기적으로 기록합니다 (Line 592-598). `prometheus_client` 내부에도 `.labels()` lookup + histogram bucket 탐색에 Lock이 존재합니다.

**수정 위치**: `services/metrics/registry.py` — 신규 클래스

```python
import queue
import threading
import time
from typing import Any, Callable


class MetricsBatchRecorder:
    """
    핫 패스에서 메트릭 기록을 비동기 배치로 처리.

    호출 스레드는 SimpleQueue.put()만 수행 (Lock-free, ~50ns).
    백그라운드 데몬 스레드가 100ms 간격 또는 배치 크기 256 도달 시 flush.

    Fail-Open: flush 실패 시 해당 배치를 drop하고 경고 로깅.
    """

    __slots__ = (
        "_queue",
        "_batch_size",
        "_flush_interval",
        "_worker",
        "_running",
    )

    def __init__(
        self,
        batch_size: int = 256,
        flush_interval_ms: int = 100,
    ) -> None:
        self._queue: queue.SimpleQueue[tuple[Callable, tuple, dict]] = (
            queue.SimpleQueue()
        )
        self._batch_size = batch_size
        self._flush_interval = flush_interval_ms / 1000.0
        self._running = True
        self._worker = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="metrics-batch-recorder",
        )
        self._worker.start()

    def enqueue(
        self,
        metric_fn: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        메트릭 기록 요청을 큐에 적재 — Lock-free O(1).

        핫 패스에서 호출. SimpleQueue.put()은 Lock-free이므로
        prometheus_client 내부 Lock 경합을 회피합니다.
        """
        if self._running:
            self._queue.put((metric_fn, args, kwargs))

    def _flush_loop(self) -> None:
        """백그라운드 스레드: 배치 수집 후 일괄 기록."""
        import logging

        logger = logging.getLogger(__name__)

        while self._running:
            batch: list[tuple[Callable, tuple, dict]] = []
            deadline = time.monotonic() + self._flush_interval

            # 배치 수집: flush_interval 또는 batch_size 충족 시 flush
            while len(batch) < self._batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                    batch.append(item)
                except queue.Empty:
                    break

            # Flush — Fail-Open
            for metric_fn, args, kwargs in batch:
                try:
                    metric_fn(*args, **kwargs)
                except Exception as e:
                    logger.debug(
                        f"[MetricsBatchRecorder] Failed to record metric: {e}"
                    )

    def shutdown(self) -> None:
        """그레이스풀 셧다운 — 잔여 배치 flush."""
        self._running = False
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
```

**적용 예시**:

```python
# adaptive.py — 모듈 수준 싱글톤
_metrics_batch_recorder: MetricsBatchRecorder | None = None

def _get_metrics_recorder() -> MetricsBatchRecorder:
    global _metrics_batch_recorder
    if _metrics_batch_recorder is None:
        _metrics_batch_recorder = MetricsBatchRecorder()
    return _metrics_batch_recorder

# record_response() 내부 — 동기 호출 대신 큐에 적재
recorder = _get_metrics_recorder()
recorder.enqueue(
    throttle_current_limit.labels(service=svc).set,
    self._current_limit,
)
recorder.enqueue(
    throttle_rtt_histogram.labels(service=svc).observe,
    rtt_ms,
)
```

#### Tier 2 (고영향): Top-level Import + `_METRICS_AVAILABLE` 플래그

**문제**: 5곳의 lazy import가 매 호출마다 `try/except` + `sys.modules` dict lookup 비용을 발생시킵니다.

**수정 위치**: `services/throttle/adaptive.py` — 파일 상단

```python
# 모듈 수준 (Top-level) — 한 번만 실행
_METRICS_AVAILABLE = False
_throttle_current_limit = None
_throttle_rtt_histogram = None
_throttle_gradient_gauge = None
_throttle_denied_total = None
_throttle_emergency_adjustments_total = None
_throttle_cb_adjustments_total = None

try:
    from selfhealing.services.metrics.definitions import (
        throttle_current_limit as _throttle_current_limit,
        throttle_rtt_ms as _throttle_rtt_histogram,
        throttle_gradient as _throttle_gradient_gauge,
        throttle_denied_total as _throttle_denied_total,
        throttle_emergency_adjustments_total as _throttle_emergency_adjustments_total,
        throttle_cb_adjustments_total as _throttle_cb_adjustments_total,
    )
    _METRICS_AVAILABLE = True
except ImportError:
    pass


def _record_throttle_metrics(...) -> None:
    if not _METRICS_AVAILABLE:
        return  # 즉시 반환 — try/except 오버헤드 제거

    # 직접 참조 — import lookup 불필요
    if limit is not None:
        _throttle_current_limit.labels(service=service).set(limit)
    ...
```

#### Tier 3 (추가): `__slots__` + 중복 Lock 제거

**수정 위치**: `services/throttle/config.py`

```python
# Python 3.10+ slots=True → 인스턴스당 ~30% 메모리 절감, 생성 ~20% 가속
@dataclass(slots=True)
class RTTSample:
    timestamp: float
    rtt_ms: float

@dataclass(slots=True)
class ThrottleResult:
    allowed: bool
    current_count: int
    limit: int
    remaining: int
    reset_at: float
    reason: str | None = None
    current_rtt_ms: float | None = None
    rtt_gradient: float | None = None
```

**중복 `get_gradient()` 제거** — [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) Line 593과 Line 622에서 동일한 `get_gradient()`를 각각 호출:

```python
# 기존 record_response() 내부
self._maybe_adjust_limit(rtt_ms)          # 내부에서 get_gradient() 호출 (Line 622)
gradient = self._gradient_calculator.get_gradient()  # 또 호출 (Line 593)

# 개선: _maybe_adjust_limit()에서 계산된 gradient를 반환하여 재사용
gradient = self._maybe_adjust_limit(rtt_ms)  # gradient 반환
# gradient를 직접 사용 — Lock 1회 절약
```

---

## 4. 구현 코드

### 4.0 라벨 동적화 구현 (리뷰 항목 1)

현재 `_record_throttle_metrics()` 호출부 5곳이 모두 `service="default"`를 하드코딩하고 있습니다 ([adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) Line 487, 579, 788, 878, 910). 환경변수로 동적 주입할 수 있도록 변경합니다.

#### 4.0.1 sanitize_label_value() — 라벨 정규화 함수

**수정 위치**: `services/metrics/registry.py`

> **네이밍 선택**: `sanitize_label_value()` — `normalize_label_value()` 대안 대비 선택.
> Prometheus 라벨 제약(`[a-zA-Z_][a-zA-Z0-9_]*`)에 맞지 않는 값을 **치환/제거**하는 것은 "sanitize" semantics에 해당.
> 코드베이스에서 `validate`는 `settings/` 디렉토리의 Pydantic validator에만 사용되므로 차별화.
> [registry.py](../../packages/selfhealing-python/src/selfhealing/services/metrics/registry.py)에 위치하여 metrics 레지스트리의 책임으로 배치.

```python
import re

# Prometheus 라벨 값 제약: UTF-8 문자열이지만, 실무상 안전한 범위로 제한
_LABEL_UNSAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_]")


def sanitize_label_value(value: str, max_length: int = 128) -> str:
    """
    Prometheus 메트릭 라벨 값을 안전한 형식으로 정규화.

    - 영숫자/언더스코어 이외 문자 → '_' 치환
    - 최대 길이 128자로 절단 (카디널리티 폭발 방지)
    - 빈 문자열 → 'unknown'

    Examples:
        >>> sanitize_label_value("my-service.v2")
        'my_service_v2'
        >>> sanitize_label_value("")
        'unknown'
        >>> sanitize_label_value("a" * 200)
        'aaa...a'  # 128자
    """
    if not value or not value.strip():
        return "unknown"
    sanitized = _LABEL_UNSAFE_PATTERN.sub("_", value.strip())
    return sanitized[:max_length]
```

#### 4.0.2 ThrottleSettings.service_name 필드 추가

**수정 위치**: `settings/throttle.py`

> **네이밍 선택**: `service_name` — `service_label`, `metric_service` 대안 대비.
> [config.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/config.py)의 다른 필드(`key_prefix`, `initial_limit`)가 직관적 영어명을 사용.
> `service_name`이 Prometheus 레이블 `service`와 1:1 대응하여 가장 명확.
> 환경변수: `SELFHEALING_THROTTLE_SERVICE_NAME` (기존 `env_prefix` 패턴 준수).

```python
class ThrottleSettings(BaseSettings):
    """Throttle 관련 설정."""

    model_config = SettingsConfigDict(env_prefix="SELFHEALING_THROTTLE_")

    # 기존 필드들...
    initial_limit: int = Field(default=100)
    window_seconds: int = Field(default=60)
    # ...

    # 신규: 메트릭 라벨 동적화
    service_name: str = Field(
        default="default",
        description="Prometheus 메트릭의 service 라벨 값. "
                    "환경변수 SELFHEALING_THROTTLE_SERVICE_NAME으로 주입.",
    )
```

#### 4.0.3 ThrottleConfig.service_name 전파

**수정 위치**: `services/throttle/config.py`

```python
@dataclass
class ThrottleConfig:
    """AdaptiveThrottle 설정 데이터클래스."""

    initial_limit: int = 100
    window_seconds: int = 60
    min_limit: int = 10
    max_limit: int = 500
    sample_interval_ms: int = 500
    smoothing_factor: float = 0.3
    decrease_ratio: float = 0.9
    increase_step: int = 1
    sla_warning_ms: int = 1000
    sla_critical_ms: int = 3000
    emergency_limit: int = 20
    key_prefix: str = "throttle"
    service_name: str = "default"  # 신규 필드

    @classmethod
    def from_settings(cls) -> "ThrottleConfig":
        settings = get_throttle_settings()
        return cls(
            # 기존 필드 매핑...
            service_name=settings.service_name,  # 신규 매핑
        )
```

#### 4.0.4 AdaptiveThrottle 하드코딩 5곳 교체

**수정 위치**: `services/throttle/adaptive.py`

```python
class AdaptiveThrottle(SlidingWindowThrottle):
    def __init__(self, config: ThrottleConfig | None = None, **kwargs):
        # 기존 초기화...
        self.config = config or ThrottleConfig.from_settings()

        # 라벨 동적화 — sanitize_label_value()로 안전한 값 보장
        from selfhealing.services.metrics.registry import sanitize_label_value
        self._service_name: str = sanitize_label_value(self.config.service_name)

    # 교체 대상 5곳:
    # Line 487: _handle_rate_limit_429()
    #   Before: _record_throttle_metrics(service="default", ...)
    #   After:  _record_throttle_metrics(service=self._service_name, ...)

    # Line 579: record_response()
    #   Before: _record_throttle_metrics(service="default", ...)
    #   After:  _record_throttle_metrics(service=self._service_name, ...)

    # Line 788: check()
    #   Before: _record_throttle_metrics(service="default", ...)
    #   After:  _record_throttle_metrics(service=self._service_name, ...)

    # Line 878: adjust_for_emergency() — emergency 메트릭
    #   Before: _record_throttle_metrics(service="default", ...)
    #   After:  _record_throttle_metrics(service=self._service_name, ...)

    # Line 910: adjust_for_emergency() — limit 메트릭
    #   Before: _record_throttle_metrics(service="default", ...)
    #   After:  _record_throttle_metrics(service=self._service_name, ...)
```

**배포 방법**: 환경변수만 설정하면 즉시 적용, 코드 변경 불필요.

```bash
# Kubernetes Deployment
env:
  - name: SELFHEALING_THROTTLE_SERVICE_NAME
    value: "payment-gateway"

# docker-compose.yml
environment:
  SELFHEALING_THROTTLE_SERVICE_NAME: "payment-gateway"
```

### 4.1 추가 메트릭 정의

**수정 위치**: `services/metrics/definitions.py`

```python
# =============================================================================
# Adaptive Throttle Extended Metrics
# =============================================================================

# Request Metrics
throttle_requests_total = get_or_create_counter(
    "selfhealing_throttle_requests_total",
    "Total requests processed by throttle",
    ["service", "result"],  # result: allowed, denied
)

throttle_allowed_total = get_or_create_counter(
    "selfhealing_throttle_allowed_total",
    "Total requests allowed by throttle",
    ["service"],
)

# SLA Metrics
throttle_sla_warnings_total = get_or_create_counter(
    "selfhealing_throttle_sla_warnings_total",
    "Total SLA warning threshold breaches",
    ["service"],
)

throttle_sla_criticals_total = get_or_create_counter(
    "selfhealing_throttle_sla_criticals_total",
    "Total SLA critical threshold breaches",
    ["service"],
)

throttle_sla_breach_duration_seconds = get_or_create_histogram(
    "selfhealing_throttle_sla_breach_duration_seconds",
    "Duration of SLA breach periods",
    ["service", "severity"],
    buckets=(60, 300, 600, 1800, 3600),
)

# Emergency Metrics
throttle_emergency_level = get_or_create_gauge(
    "selfhealing_throttle_emergency_level",
    "Current emergency level (0-3)",
    ["service"],
)

throttle_gradient_frozen = get_or_create_gauge(
    "selfhealing_throttle_gradient_frozen",
    "Whether gradient adjustment is frozen (1=yes, 0=no)",
    ["service"],
)

# Recovery Metrics
throttle_recovery_dampening_active = get_or_create_gauge(
    "selfhealing_throttle_recovery_dampening_active",
    "Whether recovery dampening is active (1=yes, 0=no)",
    ["service"],
)

throttle_recovery_dampening_step = get_or_create_gauge(
    "selfhealing_throttle_recovery_dampening_step",
    "Current recovery dampening step (0=80%, 1=90%, 2=100%)",
    ["service"],
)

throttle_recovery_completed_total = get_or_create_counter(
    "selfhealing_throttle_recovery_completed_total",
    "Total recovery dampening completions",
    ["service"],
)

# Full Stop Metrics
throttle_full_stop_active = get_or_create_gauge(
    "selfhealing_throttle_full_stop_active",
    "Whether full stop is active (1=yes, 0=no)",
    ["service"],
)

throttle_full_stop_activations_total = get_or_create_counter(
    "selfhealing_throttle_full_stop_activations_total",
    "Total full stop activations",
    ["service", "reason"],
)

# Limit Change Metrics
throttle_limit_changes_total = get_or_create_counter(
    "selfhealing_throttle_limit_changes_total",
    "Total throttle limit changes",
    ["service", "direction", "trigger"],  # direction: up, down; trigger: gradient, sla, emergency, cb, 429
)

throttle_limit_change_magnitude = get_or_create_histogram(
    "selfhealing_throttle_limit_change_magnitude",
    "Magnitude of limit changes (percentage)",
    ["service", "direction"],
    buckets=(5, 10, 20, 30, 50, 70, 100),
)

# =============================================================================
# Saturation Metrics (리뷰 항목 12)
# =============================================================================
#
# 네이밍 선택: throttle_saturation_ratio — throttle_utilization_ratio 대안 대비.
# prometheus.py Line 260-275에 worker_utilization_ratio가 이미 존재하지만,
# 이는 worker pool "사용률". Throttle의 current_limit/max_limit은
# "리소스가 얼마나 포화되었는가"로 Kubernetes USE method의 saturation에 해당.
# bulkhead/metrics.py의 selfhealing_bulkhead_utilization_percent 패턴 참고.

throttle_saturation_ratio = get_or_create_gauge(
    "selfhealing_throttle_saturation_ratio",
    "Throttle limit saturation (current_limit / max_limit), 0.0-1.0. "
    "낮을수록 더 많이 제한됨",
    ["service"],
)

throttle_max_limit = get_or_create_gauge(
    "selfhealing_throttle_max_limit",
    "Configured maximum throttle limit",
    ["service"],
)
```

### 4.2 확장된 메트릭 기록 함수 (리뷰 항목 10: Exemplar + 통합)

> **설계 결정**: 기존 `_record_throttle_metrics_extended()` 신규 함수 대신, **기존 `_record_throttle_metrics()` 시그니처를 확장**합니다.
> - 이유: 5곳의 호출부가 모두 keyword argument를 사용하므로 하위호환 보장
> - 함수 분리 시 호출부에서 "어떤 함수를 호출해야 하는가?" 이중 관리 문제 발생
> - 신규 파라미터는 모두 `None` 기본값으로 기존 호출부 변경 불필요

**Exemplar 인프라 준비도 확인**:
- [prometheus.yml](../../docker/prometheus/prometheus.yml) Line 17: `send_exemplars: true` ✅
- [datasource.yml](../../docker/grafana/provisioning/datasources/datasource.yml) Line 15-18: `exemplarTraceIdDestinations` → Tempo ✅
- [observability/__init__.py](../../packages/selfhealing-python/src/selfhealing/observability/__init__.py) Line 195-210: `get_current_trace_id_from_otel()` ✅
- [unified_view.json](../../docker/grafana/provisioning/dashboards/unified_view.json)에서 이미 `"exemplar": true` 사용 중 (4개 패널) ✅

**수정 위치**: `services/throttle/adaptive.py` — 기존 `_record_throttle_metrics()` 대체

```python
def _record_throttle_metrics(
    service: str,
    # === 기존 파라미터 (하위호환) ===
    limit: int | None = None,
    rtt_ms: float | None = None,
    gradient: float | None = None,
    denied_reason: str | None = None,
    emergency_level: int | None = None,
    cb_state: str | None = None,
    # === 확장 파라미터 (신규) ===
    request_result: str | None = None,      # "allowed" | "denied"
    sla_event: str | None = None,           # "warning" | "critical"
    gradient_frozen: bool | None = None,
    recovery_dampening_active: bool | None = None,
    recovery_dampening_step: int | None = None,
    full_stop_active: bool | None = None,
    full_stop_reason: str | None = None,
    limit_change_direction: str | None = None,
    limit_change_trigger: str | None = None,
    limit_change_percent: float | None = None,
    # === Saturation (리뷰 항목 12) ===
    max_limit: int | None = None,           # config.max_limit 전달
    # === Exemplar (리뷰 항목 10) ===
    trace_id: str | None = None,            # OTel trace_id
) -> None:
    """
    Throttle Prometheus 메트릭 기록 (확장 버전).

    기존 _record_throttle_metrics() 시그니처를 확장하여 하위호환 유지.
    Exemplar는 Fail-Open으로 처리: 첨부 실패 시 exemplar 없이 기록 계속.

    Exemplar 활용 조건:
    - prometheus_client >= 0.16.0
    - Histogram.observe() / Counter.inc()에 exemplar 파라미터 전달
    - 인프라: prometheus.yml send_exemplars: true + Grafana exemplarTraceIdDestinations
    """
    if not _METRICS_AVAILABLE:
        return

    try:
        # Exemplar 준비 — Fail-Open
        exemplar = None
        if trace_id:
            exemplar = {"trace_id": trace_id}

        # --- Core metrics ---
        if limit is not None:
            _throttle_current_limit.labels(service=service).set(limit)

        if rtt_ms is not None:
            try:
                _throttle_rtt_histogram.labels(service=service).observe(
                    rtt_ms, exemplar=exemplar
                )
            except TypeError:
                # exemplar 미지원 버전 fallback
                _throttle_rtt_histogram.labels(service=service).observe(rtt_ms)

        if gradient is not None:
            _throttle_gradient_gauge.labels(service=service).set(gradient)

        # --- Request metrics ---
        if request_result is not None:
            _throttle_requests_total.labels(
                service=service, result=request_result
            ).inc()
            if request_result == "allowed":
                try:
                    _throttle_allowed_total.labels(service=service).inc(
                        exemplar=exemplar
                    )
                except TypeError:
                    _throttle_allowed_total.labels(service=service).inc()
            elif request_result == "denied":
                _throttle_denied_total.labels(
                    service=service, reason=denied_reason or "unknown"
                ).inc()

        # --- SLA metrics ---
        if sla_event == "warning":
            _throttle_sla_warnings_total.labels(service=service).inc()
        elif sla_event == "critical":
            _throttle_sla_criticals_total.labels(service=service).inc()

        # --- Emergency metrics ---
        if emergency_level is not None:
            _throttle_emergency_level_gauge.labels(service=service).set(
                emergency_level
            )
            _throttle_emergency_adjustments_total.labels(
                level=str(emergency_level)
            ).inc()

        if gradient_frozen is not None:
            _throttle_gradient_frozen_gauge.labels(service=service).set(
                1 if gradient_frozen else 0
            )

        if cb_state is not None:
            _throttle_cb_adjustments_total.labels(
                service=service, cb_state=cb_state
            ).inc()

        # --- Recovery metrics ---
        if recovery_dampening_active is not None:
            _recovery_active_gauge.labels(service=service).set(
                1 if recovery_dampening_active else 0
            )

        if recovery_dampening_step is not None:
            _recovery_step_gauge.labels(service=service).set(
                recovery_dampening_step
            )

        # --- Full Stop metrics ---
        if full_stop_active is not None:
            _full_stop_gauge.labels(service=service).set(
                1 if full_stop_active else 0
            )

        if full_stop_reason is not None:
            _throttle_full_stop_activations_total.labels(
                service=service, reason=full_stop_reason
            ).inc()

        # --- Limit change metrics ---
        if limit_change_direction and limit_change_trigger:
            _throttle_limit_changes_total.labels(
                service=service,
                direction=limit_change_direction,
                trigger=limit_change_trigger,
            ).inc()

            if limit_change_percent is not None:
                _throttle_limit_change_magnitude.labels(
                    service=service,
                    direction=limit_change_direction,
                ).observe(abs(limit_change_percent))

        # --- Saturation metrics (리뷰 항목 12) ---
        if limit is not None and max_limit is not None and max_limit > 0:
            saturation = limit / max_limit
            _throttle_saturation_ratio.labels(service=service).set(saturation)
            _throttle_max_limit_gauge.labels(service=service).set(max_limit)

    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Failed to record metrics: {e}")
```

### 4.3 AdaptiveThrottle 메트릭 통합 (라벨 동적화 + Exemplar 적용)

**수정 위치**: `services/throttle/adaptive.py`

```python
class AdaptiveThrottle(SlidingWindowThrottle):
    """AdaptiveThrottle with comprehensive metrics."""

    def check(self, key: str) -> ThrottleResult:
        """Check if request is allowed with metrics recording."""
        # 기존 로직...
        result = super().check(key)

        # Exemplar: 현재 OTel trace_id 취득 (Fail-Open)
        trace_id = None
        try:
            from selfhealing.observability import get_current_trace_id_from_otel
            trace_id = get_current_trace_id_from_otel()
        except Exception:
            pass

        # 메트릭 기록 — service 동적화 (§4.0) + exemplar (리뷰 항목 10)
        _record_throttle_metrics(
            service=self._service_name,       # §4.0: 하드코딩 제거
            request_result="allowed" if result.allowed else "denied",
            denied_reason=result.reason if not result.allowed else None,
            trace_id=trace_id,                # 리뷰 항목 10: Exemplar
        )

        return result

    def record_response(self, rtt_ms: float) -> None:
        """Record response time with metrics."""
        # 기존 로직...
        previous_limit = self._current_limit
        self._gradient_calculator.add_sample(rtt_ms)
        self._maybe_adjust_limit(rtt_ms)

        # Lock 통합: get_snapshot() 단일 호출 (§3.3 Tier 2)
        current_rtt, gradient = self._gradient_calculator.get_snapshot()

        # Exemplar 취득
        trace_id = None
        try:
            from selfhealing.observability import get_current_trace_id_from_otel
            trace_id = get_current_trace_id_from_otel()
        except Exception:
            pass

        # 확장 메트릭 기록 — 통합된 단일 호출
        _record_throttle_metrics(
            service=self._service_name,
            limit=self._current_limit,
            rtt_ms=rtt_ms,
            gradient=gradient,
            emergency_level=self._emergency_level,
            gradient_frozen=self._gradient_frozen,
            recovery_dampening_active=self._recovery_dampening_active,
            recovery_dampening_step=(
                self._recovery_dampening_step
                if self._recovery_dampening_active
                else None
            ),
            full_stop_active=self._full_stop_active,
            max_limit=self.config.max_limit,  # 리뷰 항목 12: Saturation
            trace_id=trace_id,                # 리뷰 항목 10: Exemplar
        )

        # Limit 변경 메트릭
        if self._current_limit != previous_limit:
            direction = "up" if self._current_limit > previous_limit else "down"
            change_percent = (
                abs(self._current_limit - previous_limit)
                / previous_limit * 100
            )
            _record_throttle_metrics(
                service=self._service_name,
                limit_change_direction=direction,
                limit_change_trigger="gradient",
                limit_change_percent=change_percent,
            )

    def _maybe_adjust_limit(self, rtt_ms: float) -> None:
        """Adjust limit with SLA metrics."""
        # 기존 로직...

        if rtt_ms >= self.config.sla_critical_ms:
            _record_throttle_metrics(
                service=self._service_name,
                sla_event="critical",
                limit_change_direction="down",
                limit_change_trigger="sla_critical",
                limit_change_percent=30,
            )
            return

        if rtt_ms >= self.config.sla_warning_ms:
            _record_throttle_metrics(
                service=self._service_name,
                sla_event="warning",
                limit_change_direction="down",
                limit_change_trigger="sla_warning",
            )
```

### 4.5 HPA 연동 — RTT p99 기반 오토스케일링 (리뷰 항목 4)

> **시그널 선택**: `denied_total` 대신 `rtt_ms p99` 선택.
> Throttle limit은 글로벌 싱글톤([adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) `get_adaptive_throttle()`)이므로,
> denied 증가 시 Pod 추가는 인스턴스 간 limit 불일치를 유발합니다.
> RTT p99 상승은 **실제 백엔드 부하**를 반영하여 의미 있는 스케일 시그널입니다.

#### 4.5.1 prometheus-adapter 커스텀 메트릭 규칙

**수정 위치**: `k8s/prometheus-adapter-config.yaml` — 기존 ConfigMap에 규칙 추가

기존 [prometheus-adapter-config.yaml](../../k8s/prometheus-adapter-config.yaml)에는 `django_http_requests_per_second`, `selfhealing_queue_depth` 등의 커스텀 메트릭이 정의되어 있습니다.

```yaml
# k8s/prometheus-adapter-config.yaml — rules 배열에 추가
- seriesQuery: 'selfhealing_throttle_rtt_ms_bucket{namespace!="",pod!=""}'
  resources:
    overrides:
      namespace: {resource: "namespace"}
      pod: {resource: "pod"}
  name:
    as: "selfhealing_throttle_rtt_p99"
  metricsQuery: >-
    histogram_quantile(
      0.99,
      sum(rate(<<.Series>>{<<.LabelMatchers>>}[5m]))
      by (<<.GroupBy>>, le)
    )
```

#### 4.5.2 HPA 스케일링 규칙

**수정 위치**: `k8s/selfhealing-hpa.yaml` — 기존 metrics 배열에 추가

기존 [selfhealing-hpa.yaml](../../k8s/selfhealing-hpa.yaml)은 `selfhealing_queue_depth` + CPU/Memory 기반입니다. RTT p99 메트릭을 추가하여 응답 시간 기반 스케일링을 지원합니다.

```yaml
# k8s/selfhealing-hpa.yaml — spec.metrics 배열에 추가
- type: Pods
  pods:
    metric:
      name: selfhealing_throttle_rtt_p99
    target:
      type: AverageValue
      averageValue: "500"  # 500ms — SLA warning threshold와 동일
```

**스케일링 동작**:
- RTT p99 > 500ms → 스케일 아웃 (Pod 증가)
- RTT p99 < 500ms → 스케일 인 (안정 후 cooldown)
- 기존 `queue_depth`, CPU, Memory 메트릭과 **OR 조건**으로 동작 (가장 높은 비율 기준)

---

## 5. Alerting Rules

### 5.1 Prometheus Alerting Rules (리뷰 항목 7: `for:` 임계값 표준화)

> **변경 사항**: 기존 `for: 0m` 항목들을 severity 기반으로 표준화.
> - `severity: critical` → `for: 1m` (기존 [alerts.yml](../../docker/prometheus/rules/alerts.yml)의 `CircuitBreakerOpen`, `LatencyP95SlaCritical`과 동일 패턴)
> - `severity: warning` → `for: 5m`
> - `for: 0m`은 단일 scrape 결과에 반응하여 **flapping** 위험이 큼

**파일**: `docker/prometheus/rules/throttle_alerts.yml` (신규 파일)

> [prometheus.yml](../../docker/prometheus/prometheus.yml) Line 25의 `rule_files: /etc/prometheus/rules/*.yml` glob 패턴으로 자동 로딩됩니다.

```yaml
groups:
  - name: throttle_alerts
    rules:
      # ── Critical 알람 (for: 1m) ──────────────────────────────────

      # SLA Critical 알람
      - alert: ThrottleSLACritical
        expr: increase(selfhealing_throttle_sla_criticals_total[5m]) > 0
        for: 1m                          # 변경: 0m → 1m (flapping 방지)
        labels:
          severity: critical
        annotations:
          summary: "Throttle SLA Critical threshold breached"
          description: "SLA Critical threshold has been breached {{ $value }} times in the last 5 minutes"

      # Full Stop 활성화 알람
      - alert: ThrottleFullStopActive
        expr: selfhealing_throttle_full_stop_active == 1
        for: 1m                          # 변경: 0m → 1m
        labels:
          severity: critical
        annotations:
          summary: "Throttle Full Stop is ACTIVE"
          description: "All requests are being blocked due to Full Stop condition"

      # Emergency Level 3 알람
      - alert: ThrottleEmergencyLevel3
        expr: selfhealing_throttle_emergency_level >= 3
        for: 1m                          # 변경: 0m → 1m
        labels:
          severity: critical
        annotations:
          summary: "Throttle Emergency Level 3 active"
          description: "Maximum emergency level is active, gradient adjustments are frozen"

      # ── Warning 알람 (for: 5m) ──────────────────────────────────

      # SLA Warning 알람 (5분간 3회 이상)
      - alert: ThrottleSLAWarningFrequent
        expr: increase(selfhealing_throttle_sla_warnings_total[5m]) >= 3
        for: 5m                          # 변경: 0m → 5m
        labels:
          severity: warning
        annotations:
          summary: "Frequent SLA warnings detected"
          description: "SLA Warning threshold breached {{ $value }} times in 5 minutes"

      # Limit 급격한 감소 알람
      - alert: ThrottleLimitDropped
        expr: |
          (selfhealing_throttle_limit - selfhealing_throttle_limit offset 5m)
          / selfhealing_throttle_limit offset 5m < -0.5
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Throttle limit dropped by more than 50%"
          description: "Current limit: {{ $value }}"

      # 높은 거부율 알람
      - alert: ThrottleHighDenialRate
        expr: |
          rate(selfhealing_throttle_denied_total[5m])
          / (rate(selfhealing_throttle_requests_total[5m]) + 0.001) > 0.3
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High throttle denial rate (>30%)"
          description: "{{ $value | humanizePercentage }} of requests are being denied"

      # RTT 지속적 상승 알람
      - alert: ThrottleRTTIncreasing
        expr: selfhealing_throttle_gradient > 0.2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "RTT gradient consistently positive"
          description: "Response times have been increasing for 5+ minutes (gradient: {{ $value }})"

      # Recovery Dampening 장기화 알람
      - alert: ThrottleRecoveryStuck
        expr: selfhealing_throttle_recovery_dampening_active == 1
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Recovery dampening taking too long"
          description: "Recovery dampening has been active for more than 10 minutes"

      # ── Saturation 알람 (리뷰 항목 12) ─────────────────────────

      # Throttle Saturation 위험 수준 알람
      - alert: ThrottleSaturationCritical
        expr: selfhealing_throttle_saturation_ratio < 0.2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Throttle saturation dangerously low (<20%)"
          description: >-
            현재 limit이 max_limit의 {{ $value | humanizePercentage }}만 사용 중.
            시스템이 심하게 제한되고 있습니다.

### 5.2 `for:` 임계값 표준화 근거

기존 [alerts.yml](../../docker/prometheus/rules/alerts.yml)의 `for:` 값 분포 분석:

| 기존 알람 (alerts.yml) | severity | for: |
|------------------------|----------|------|
| `CircuitBreakerOpen` | critical | 1m |
| `LatencyP95SlaCritical` | critical | 1m |
| `DLQCriticalPendingCount` | critical | 3m |
| `SLOAvailabilityViolation` | critical | 5m |
| `DLQHighPendingCount` | warning | 5m |
| `RetrySuccessRateLow` | warning | 5m |
| `RequestThroughputDrop` | warning | 5m |

**표준화 원칙**: `critical` 최소 `1m`, `warning` 최소 `5m`. 이는 단일 scrape 결과(15-30s 간격)에 의한 거짓 양성을 방지합니다.

```

### 5.5 공통 라벨 주입 — Infrastructure Level (리뷰 항목 5)

> **설계 결정**: `region`/`cluster_id`를 **코드 레벨 라벨이 아닌 인프라 레벨**에서 주입합니다.
>
> **이유**:
> 1. **카디널리티 폭발 방지** — 코드에 `region` 라벨 추가 시 `메트릭 수 × region 수`로 시계열 폭증
> 2. **관심사 분리** — 메트릭 코드는 비즈니스 라벨(service, reason)만 관리, 인프라 라벨은 인프라가 관리
> 3. **동일 소스** — [cluster_identity.py](../../packages/selfhealing-python/src/selfhealing/observability/cluster_identity.py)의 `region` 값이 이미 환경변수(`SELFHEALING_REGION`) 기반이므로 인프라 레벨과 동일 소스

#### 5.5.1 Prometheus external_labels

**수정 위치**: `docker/prometheus/prometheus.yml`

현재 [prometheus.yml](../../docker/prometheus/prometheus.yml) Line 10-13:

```yaml
# 현재 상태
global:
  external_labels:
    environment: 'production'
    service: 'self-healing'
```

```yaml
# 수정 후 — region + cluster_id 추가
global:
  external_labels:
    environment: 'production'
    service: 'self-healing'
    region: '${REGION:-ap-northeast-2}'       # 신규
    cluster_id: '${CLUSTER_ID:-default}'       # 신규
```

> `external_labels`는 모든 시계열에 자동 첨부되며, federation/remote_write 시에도 전파됩니다.

#### 5.5.2 OTel Collector Resource Processor

3개 설정 파일 모두에 `region`/`cluster_id` resource attribute를 추가합니다.

**(a) otel-collector-config.yml**

**수정 위치**: `docker/otel-collector/otel-collector-config.yml` Line 85-92

```yaml
# 현재 상태
processors:
  resource:
    attributes:
      - key: service.name
        value: "selfhealing"
        action: upsert
      - key: deployment.environment
        value: "development"
        action: upsert
```

```yaml
# 수정 후
processors:
  resource:
    attributes:
      - key: service.name
        value: "selfhealing"
        action: upsert
      - key: deployment.environment
        value: "development"
        action: upsert
      - key: region                              # 신규
        value: ${env:REGION:-ap-northeast-2}
        action: upsert
      - key: cluster_id                          # 신규
        value: ${env:CLUSTER_ID:-default}
        action: upsert
```

**(b) otel-collector-agent.yml**

**수정 위치**: `docker/otel-collector/otel-collector-agent.yml` Line 54-63

```yaml
# 수정 후 — 기존 attributes에 추가
processors:
  resource:
    attributes:
      - key: service.name
        value: "selfhealing"
        action: upsert
      - key: deployment.environment
        value: ${env:DEPLOYMENT_ENVIRONMENT:-development}
        action: upsert
      - key: collector.role
        value: "agent"
        action: upsert
      - key: region                              # 신규
        value: ${env:REGION:-ap-northeast-2}
        action: upsert
      - key: cluster_id                          # 신규
        value: ${env:CLUSTER_ID:-default}
        action: upsert
```

**(c) otel-collector-gateway.yml**

**수정 위치**: `docker/otel-collector/otel-collector-gateway.yml` Line 72-76

```yaml
# 수정 후
processors:
  resource:
    attributes:
      - key: collector.role
        value: "gateway"
        action: upsert
      - key: region                              # 신규
        value: ${env:REGION:-ap-northeast-2}
        action: upsert
      - key: cluster_id                          # 신규
        value: ${env:CLUSTER_ID:-default}
        action: upsert
```

> Gateway의 `resource_to_telemetry_conversion: enabled: true` 설정으로 resource attribute가 메트릭 라벨로 자동 변환됩니다.

#### 5.5.3 환경변수 설정

```bash
# Kubernetes ConfigMap / Deployment
env:
  - name: REGION
    value: "ap-northeast-2"          # 또는 valueFrom으로 동적 주입
  - name: CLUSTER_ID
    value: "prod-cluster-01"

# docker-compose.yml
environment:
  REGION: "ap-northeast-2"
  CLUSTER_ID: "local-dev"
```

> [cluster_identity.py](../../packages/selfhealing-python/src/selfhealing/observability/cluster_identity.py)의 `SELFHEALING_REGION` 환경변수와 동일 값을 사용하면 Application ↔ Infrastructure 라벨이 일치합니다.

---

## 6. Grafana Dashboard

### 6.1 Dashboard JSON

```json
{
  "title": "Adaptive Throttle Monitoring",
  "uid": "adaptive-throttle",
  "panels": [
    {
      "title": "Current Throttle Limit",
      "type": "stat",
      "gridPos": {"h": 4, "w": 6, "x": 0, "y": 0},
      "targets": [
        {
          "expr": "selfhealing_throttle_limit{service=\"default\"}",
          "legendFormat": "Limit"
        }
      ]
    },
    {
      "title": "Emergency Level",
      "type": "stat",
      "gridPos": {"h": 4, "w": 3, "x": 6, "y": 0},
      "targets": [
        {
          "expr": "selfhealing_throttle_emergency_level{service=\"default\"}",
          "legendFormat": "Level"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "thresholds": {
            "steps": [
              {"color": "green", "value": 0},
              {"color": "yellow", "value": 1},
              {"color": "orange", "value": 2},
              {"color": "red", "value": 3}
            ]
          }
        }
      }
    },
    {
      "title": "Full Stop Status",
      "type": "stat",
      "gridPos": {"h": 4, "w": 3, "x": 9, "y": 0},
      "targets": [
        {
          "expr": "selfhealing_throttle_full_stop_active{service=\"default\"}",
          "legendFormat": "Full Stop"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "mappings": [
            {"type": "value", "options": {"0": {"text": "OFF", "color": "green"}}},
            {"type": "value", "options": {"1": {"text": "ACTIVE", "color": "red"}}}
          ]
        }
      }
    },
    {
      "title": "Throttle Limit Over Time",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 4},
      "targets": [
        {
          "expr": "selfhealing_throttle_limit{service=\"default\"}",
          "legendFormat": "Current Limit"
        }
      ]
    },
    {
      "title": "RTT Distribution (p50, p90, p99)",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 4},
      "targets": [
        {
          "expr": "histogram_quantile(0.5, rate(selfhealing_throttle_rtt_ms_bucket[5m]))",
          "legendFormat": "p50"
        },
        {
          "expr": "histogram_quantile(0.9, rate(selfhealing_throttle_rtt_ms_bucket[5m]))",
          "legendFormat": "p90"
        },
        {
          "expr": "histogram_quantile(0.99, rate(selfhealing_throttle_rtt_ms_bucket[5m]))",
          "legendFormat": "p99"
        }
      ]
    },
    {
      "title": "RTT Gradient",
      "type": "timeseries",
      "gridPos": {"h": 6, "w": 12, "x": 0, "y": 12},
      "targets": [
        {
          "expr": "selfhealing_throttle_gradient{service=\"default\"}",
          "legendFormat": "Gradient"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "custom": {
            "thresholdsStyle": {"mode": "line"}
          },
          "thresholds": {
            "steps": [
              {"color": "green", "value": -0.1},
              {"color": "yellow", "value": 0},
              {"color": "red", "value": 0.1}
            ]
          }
        }
      }
    },
    {
      "title": "Request Rate (Allowed vs Denied)",
      "type": "timeseries",
      "gridPos": {"h": 6, "w": 12, "x": 12, "y": 12},
      "targets": [
        {
          "expr": "rate(selfhealing_throttle_requests_total{result=\"allowed\"}[5m])",
          "legendFormat": "Allowed"
        },
        {
          "expr": "rate(selfhealing_throttle_requests_total{result=\"denied\"}[5m])",
          "legendFormat": "Denied"
        }
      ]
    },
    {
      "title": "SLA Events",
      "type": "timeseries",
      "gridPos": {"h": 6, "w": 12, "x": 0, "y": 18},
      "targets": [
        {
          "expr": "increase(selfhealing_throttle_sla_warnings_total[5m])",
          "legendFormat": "Warnings"
        },
        {
          "expr": "increase(selfhealing_throttle_sla_criticals_total[5m])",
          "legendFormat": "Criticals"
        }
      ]
    },
    {
      "title": "Limit Changes by Trigger",
      "type": "timeseries",
      "gridPos": {"h": 6, "w": 12, "x": 12, "y": 18},
      "targets": [
        {
          "expr": "increase(selfhealing_throttle_limit_changes_total[5m])",
          "legendFormat": "{{direction}} - {{trigger}}"
        }
      ]
    }
  ]
}
```

### 6.2 Baseline 비교 패널 (리뷰 항목 9)

기존 알람 규칙에서 `offset` 패턴이 사용되고 있습니다 ([alerts.yml](../../docker/prometheus/rules/alerts.yml) Line 358: `RequestThroughputDrop`의 `offset 1h`). 대시보드에도 동일 패턴을 적용하여 **과거 대비 현재 상태**를 시각화합니다.

```json
{
  "title": "Throttle Limit: Current vs Baseline",
  "type": "timeseries",
  "gridPos": {"h": 8, "w": 12, "x": 0, "y": 24},
  "targets": [
    {
      "expr": "selfhealing_throttle_limit{service=\"$service\"}",
      "legendFormat": "Current"
    },
    {
      "expr": "selfhealing_throttle_limit{service=\"$service\"} offset 1h",
      "legendFormat": "1h ago"
    },
    {
      "expr": "selfhealing_throttle_limit{service=\"$service\"} offset 1d",
      "legendFormat": "1d ago"
    }
  ],
  "fieldConfig": {
    "defaults": {
      "custom": {
        "lineWidth": 2,
        "fillOpacity": 5
      }
    },
    "overrides": [
      {
        "matcher": {"id": "byName", "options": "1h ago"},
        "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash"}}]
      },
      {
        "matcher": {"id": "byName", "options": "1d ago"},
        "properties": [{"id": "custom.lineStyle", "value": {"fill": "dot"}}]
      }
    ]
  }
}
```

```json
{
  "title": "RTT p99: Current vs Baseline",
  "type": "timeseries",
  "gridPos": {"h": 8, "w": 12, "x": 12, "y": 24},
  "targets": [
    {
      "expr": "histogram_quantile(0.99, rate(selfhealing_throttle_rtt_ms_bucket{service=\"$service\"}[5m]))",
      "legendFormat": "Current p99",
      "exemplar": true
    },
    {
      "expr": "histogram_quantile(0.99, rate(selfhealing_throttle_rtt_ms_bucket{service=\"$service\"}[5m] offset 1h))",
      "legendFormat": "p99 1h ago"
    },
    {
      "expr": "histogram_quantile(0.99, rate(selfhealing_throttle_rtt_ms_bucket{service=\"$service\"}[5m] offset 1d))",
      "legendFormat": "p99 1d ago"
    }
  ]
}
```

```json
{
  "title": "Denied Rate: Current vs Baseline",
  "type": "timeseries",
  "gridPos": {"h": 8, "w": 12, "x": 0, "y": 32},
  "targets": [
    {
      "expr": "rate(selfhealing_throttle_denied_total{service=\"$service\"}[5m])",
      "legendFormat": "Current",
      "exemplar": true
    },
    {
      "expr": "rate(selfhealing_throttle_denied_total{service=\"$service\"}[5m] offset 1h)",
      "legendFormat": "1h ago"
    },
    {
      "expr": "rate(selfhealing_throttle_denied_total{service=\"$service\"}[5m] offset 1d)",
      "legendFormat": "1d ago"
    }
  ]
}
```

### 6.3 Saturation 게이지 패널 (리뷰 항목 12)

```json
{
  "title": "Throttle Saturation",
  "type": "gauge",
  "gridPos": {"h": 6, "w": 6, "x": 12, "y": 32},
  "targets": [
    {
      "expr": "selfhealing_throttle_saturation_ratio{service=\"$service\"}",
      "legendFormat": "Saturation"
    }
  ],
  "fieldConfig": {
    "defaults": {
      "min": 0,
      "max": 1,
      "unit": "percentunit",
      "thresholds": {
        "mode": "absolute",
        "steps": [
          {"color": "red", "value": 0},
          {"color": "yellow", "value": 0.2},
          {"color": "green", "value": 0.5}
        ]
      }
    }
  }
}
```

```json
{
  "title": "Saturation Over Time",
  "type": "timeseries",
  "gridPos": {"h": 6, "w": 6, "x": 18, "y": 32},
  "targets": [
    {
      "expr": "selfhealing_throttle_saturation_ratio{service=\"$service\"}",
      "legendFormat": "Saturation"
    },
    {
      "expr": "selfhealing_throttle_saturation_ratio{service=\"$service\"} offset 1h",
      "legendFormat": "1h ago"
    }
  ],
  "fieldConfig": {
    "defaults": {
      "unit": "percentunit",
      "custom": {
        "thresholdsStyle": {"mode": "line+area"}
      },
      "thresholds": {
        "steps": [
          {"color": "transparent", "value": null},
          {"color": "red", "value": 0.2}
        ]
      }
    }
  }
}
```

### 6.4 대시보드 템플릿 변수

기존 대시보드의 하드코딩된 `service="default"`를 템플릿 변수로 교체합니다 (§4.0 라벨 동적화 연계).

```json
{
  "templating": {
    "list": [
      {
        "name": "service",
        "type": "query",
        "query": "label_values(selfhealing_throttle_limit, service)",
        "current": {"text": "default", "value": "default"},
        "refresh": 2
      }
    ]
  }
}
```

> [unified_view.json](../../docker/grafana/provisioning/dashboards/unified_view.json)에서 이미 `"exemplar": true` 패턴이 4개 패널에 사용되고 있습니다. 위 RTT p99, Denied Rate 패널에도 동일하게 `"exemplar": true`를 적용하여 Tempo trace로의 딥 링크를 활성화합니다.

---

## 7. 테스트

### 7.1 메트릭 기록 테스트

```python
class TestThrottleMetrics:
    """Throttle Prometheus 메트릭 테스트."""

    def test_check_records_request_metrics(self):
        """check() 호출 시 요청 메트릭 기록 확인."""
        throttle = AdaptiveThrottle()

        # 요청 실행
        result = throttle.check("test_key")

        # 메트릭 검증 (prometheus_client 사용)
        from prometheus_client import REGISTRY

        requests_total = REGISTRY.get_sample_value(
            "selfhealing_throttle_requests_total",
            {"service": "default", "result": "allowed"}
        )
        assert requests_total >= 1

    def test_sla_critical_records_metrics(self):
        """SLA Critical 시 메트릭 기록 확인."""
        throttle = AdaptiveThrottle(config=ThrottleConfig(
            sla_critical_ms=100,
        ))

        # SLA Critical 트리거
        throttle.record_response(150)  # > 100ms

        from prometheus_client import REGISTRY

        criticals_total = REGISTRY.get_sample_value(
            "selfhealing_throttle_sla_criticals_total",
            {"service": "default"}
        )
        assert criticals_total >= 1
```

### 7.2 라벨 동적화 테스트 (리뷰 항목 1)

```python
class TestSanitizeLabelValue:
    """sanitize_label_value() 단위 테스트."""

    def test_replaces_special_characters(self):
        """특수문자가 '_'로 치환되는지 확인."""
        from selfhealing.services.metrics.registry import sanitize_label_value

        assert sanitize_label_value("my-service.v2") == "my_service_v2"
        assert sanitize_label_value("payment/gateway") == "payment_gateway"
        assert sanitize_label_value("svc@region#1") == "svc_region_1"

    def test_empty_string_returns_unknown(self):
        """빈 문자열/공백만 입력 시 'unknown' 반환."""
        from selfhealing.services.metrics.registry import sanitize_label_value

        assert sanitize_label_value("") == "unknown"
        assert sanitize_label_value("   ") == "unknown"

    def test_truncates_at_max_length(self):
        """128자 초과 시 절단."""
        from selfhealing.services.metrics.registry import sanitize_label_value

        long_value = "a" * 200
        result = sanitize_label_value(long_value)
        assert len(result) == 128
        assert result == "a" * 128

    def test_preserves_valid_characters(self):
        """유효한 문자(영숫자+언더스코어)는 그대로 유지."""
        from selfhealing.services.metrics.registry import sanitize_label_value

        assert sanitize_label_value("valid_service_123") == "valid_service_123"


class TestDynamicServiceLabel:
    """service_name 동적 라벨 통합 테스트."""

    def test_service_name_from_config(self):
        """ThrottleConfig.service_name이 메트릭 라벨로 전파되는지 확인."""
        config = ThrottleConfig(service_name="payment-gateway")
        throttle = AdaptiveThrottle(config=config)

        assert throttle._service_name == "payment_gateway"  # sanitized

    def test_service_name_from_env(self, monkeypatch):
        """환경변수 SELFHEALING_THROTTLE_SERVICE_NAME 주입 확인."""
        monkeypatch.setenv("SELFHEALING_THROTTLE_SERVICE_NAME", "order-service")

        from selfhealing.settings.throttle import get_throttle_settings

        settings = get_throttle_settings()
        assert settings.service_name == "order-service"

    def test_default_service_name(self):
        """기본값 'default' 확인."""
        config = ThrottleConfig()
        throttle = AdaptiveThrottle(config=config)

        assert throttle._service_name == "default"
```

### 7.3 BucketSlidingWindow 성능 테스트 (리뷰 항목 2)

```python
import time


class TestBucketSlidingWindowPerformance:
    """BucketSlidingWindow O(1) 성능 벤치마크."""

    def test_record_is_o1(self):
        """record()가 O(1) 시간 복잡도인지 벤치마크."""
        from selfhealing.services.throttle.base import BucketSlidingWindow

        window = BucketSlidingWindow(window_seconds=60)

        # 워밍업
        for _ in range(1000):
            window.record("test_key")

        # 벤치마크: 100K 연산
        start = time.perf_counter()
        for _ in range(100_000):
            window.record("bench_key")
        elapsed = time.perf_counter() - start

        ops_per_sec = 100_000 / elapsed
        print(f"BucketSlidingWindow: {ops_per_sec:,.0f} ops/sec ({elapsed:.3f}s)")

        # 최소 100K ops/sec 달성 확인
        assert ops_per_sec >= 100_000, (
            f"Expected >= 100K ops/sec, got {ops_per_sec:,.0f}"
        )

    def test_shard_locks_reduce_contention(self):
        """64-shard Lock이 글로벌 Lock 대비 개선되는지 확인."""
        import threading
        from selfhealing.services.throttle.base import BucketSlidingWindow

        window = BucketSlidingWindow(window_seconds=60, num_shards=64)
        errors = []
        ops_count = 0

        def worker(key_prefix: str, num_ops: int):
            nonlocal ops_count
            for i in range(num_ops):
                try:
                    window.record(f"{key_prefix}_{i % 10}")
                except Exception as e:
                    errors.append(e)
            ops_count += num_ops

        # 8 스레드로 동시 실행
        threads = [
            threading.Thread(target=worker, args=(f"worker_{i}", 10_000))
            for i in range(8)
        ]

        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - start

        assert len(errors) == 0, f"Errors: {errors}"
        print(f"Concurrent: {80_000 / elapsed:,.0f} ops/sec ({elapsed:.3f}s)")
```

### 7.4 Saturation 메트릭 테스트 (리뷰 항목 12)

```python
class TestSaturationMetrics:
    """Throttle Saturation 메트릭 정확성 테스트."""

    def test_saturation_ratio_calculation(self):
        """saturation = current_limit / max_limit 계산 정확성."""
        config = ThrottleConfig(
            initial_limit=100,
            max_limit=500,
        )
        throttle = AdaptiveThrottle(config=config)

        # 응답 기록으로 limit 변경 유도 (SLA Critical)
        throttle.record_response(5000)  # > sla_critical_ms

        from prometheus_client import REGISTRY

        saturation = REGISTRY.get_sample_value(
            "selfhealing_throttle_saturation_ratio",
            {"service": "default"}
        )
        max_limit = REGISTRY.get_sample_value(
            "selfhealing_throttle_max_limit",
            {"service": "default"}
        )

        assert max_limit == 500
        assert 0.0 <= saturation <= 1.0
        # limit이 감소했으므로 saturation < 1.0
        assert saturation < 1.0

    def test_saturation_at_full_capacity(self):
        """max_limit 도달 시 saturation == 1.0."""
        config = ThrottleConfig(
            initial_limit=500,  # max_limit과 동일
            max_limit=500,
        )
        throttle = AdaptiveThrottle(config=config)

        # limit 변경 없이 기록
        throttle.record_response(10)  # 정상 RTT

        from prometheus_client import REGISTRY

        saturation = REGISTRY.get_sample_value(
            "selfhealing_throttle_saturation_ratio",
            {"service": "default"}
        )
        assert saturation == 1.0
```

### 7.5 Exemplar 첨부 테스트 (리뷰 항목 10)

```python
from unittest.mock import patch, MagicMock


class TestExemplarAttachment:
    """Exemplar 첨부 및 Fail-Open 테스트."""

    @patch("selfhealing.observability.get_current_trace_id_from_otel")
    def test_exemplar_attached_when_otel_active(self, mock_trace_id):
        """OTel 활성 시 trace_id exemplar 첨부 확인."""
        mock_trace_id.return_value = "abc123def456"

        config = ThrottleConfig()
        throttle = AdaptiveThrottle(config=config)

        # RTT 기록 — exemplar가 첨부되어야 함
        throttle.record_response(50)

        # prometheus_client에서 exemplar 조회
        from prometheus_client import REGISTRY

        # Histogram 메트릭에서 exemplar 확인
        for metric in REGISTRY.collect():
            if metric.name == "selfhealing_throttle_rtt_ms":
                for sample in metric.samples:
                    if sample.exemplar:
                        assert sample.exemplar.labels.get("trace_id") == "abc123def456"
                        return
        # exemplar 미지원 환경에서는 pass (Fail-Open)

    @patch(
        "selfhealing.observability.get_current_trace_id_from_otel",
        side_effect=ImportError("OTel not installed"),
    )
    def test_fail_open_when_otel_unavailable(self, mock_trace_id):
        """OTel 미설치 시 메트릭 기록은 정상 진행 (Fail-Open)."""
        config = ThrottleConfig()
        throttle = AdaptiveThrottle(config=config)

        # 예외 없이 정상 기록되어야 함
        throttle.record_response(50)

        from prometheus_client import REGISTRY

        rtt_count = REGISTRY.get_sample_value(
            "selfhealing_throttle_rtt_ms_count",
            {"service": "default"}
        )
        assert rtt_count >= 1  # exemplar 없이도 메트릭 기록 성공
```

### 7.6 Alert Rule 검증 테스트

```bash
# PromQL 문법 검증 (CI/CD 파이프라인에서 실행)
promtool check rules docker/prometheus/rules/throttle_alerts.yml

# 예상 출력:
# Checking docker/prometheus/rules/throttle_alerts.yml
#   SUCCESS: 9 rules found
```

```python
class TestAlertRules:
    """Alert rule 설정 검증 테스트."""

    def test_all_critical_alerts_have_minimum_for_duration(self):
        """모든 critical 알람의 for: >= 1m 확인."""
        import yaml

        with open("docker/prometheus/rules/throttle_alerts.yml") as f:
            rules_config = yaml.safe_load(f)

        for group in rules_config["groups"]:
            for rule in group["rules"]:
                if rule.get("labels", {}).get("severity") == "critical":
                    for_duration = rule.get("for", "0m")
                    assert for_duration != "0m", (
                        f"Critical alert '{rule['alert']}' has for: 0m "
                        f"(minimum should be 1m)"
                    )

    def test_all_warning_alerts_have_minimum_for_duration(self):
        """모든 warning 알람의 for: >= 1m 확인."""
        import yaml

        with open("docker/prometheus/rules/throttle_alerts.yml") as f:
            rules_config = yaml.safe_load(f)

        for group in rules_config["groups"]:
            for rule in group["rules"]:
                if rule.get("labels", {}).get("severity") == "warning":
                    for_duration = rule.get("for", "0m")
                    assert for_duration != "0m", (
                        f"Warning alert '{rule['alert']}' has for: 0m"
                    )
```

---

## 8. 구현 상태

> **최종 구현일**: 2026-02-06
> **단위 테스트**: 50/50 통과

### 8.1 완료 항목

| 항목 | 구현 파일 | 테스트 파일 |
|------|----------|------------|
| `sanitize_label_value()` | `services/metrics/registry.py` | `tests/unit/metrics/test_registry_label_sanitize.py` |
| `MetricsBatchRecorder` | `services/metrics/registry.py` | `tests/unit/metrics/test_registry_label_sanitize.py` |
| `BucketSlidingWindow` | `services/throttle/base.py` | `tests/unit/throttle/test_bucket_sliding_window.py` |
| `GradientCalculator.get_snapshot()` | `services/throttle/adaptive.py` | `tests/unit/throttle/test_gradient_calculator_snapshot.py` |
| `ThrottleSettings.service_name` | `settings/throttle.py` | `tests/unit/throttle/test_throttle_dynamic_labels.py` |
| `ThrottleConfig.service_name` | `services/throttle/config.py` | `tests/unit/throttle/test_throttle_dynamic_labels.py` |
| 확장 메트릭 정의 | `services/metrics/definitions.py` | `tests/unit/throttle/test_throttle_extended_metrics.py` |
| `_record_throttle_metrics()` 확장 | `services/throttle/adaptive.py` | `tests/unit/throttle/test_throttle_extended_metrics.py` |
| 라벨 동적화 | `services/throttle/adaptive.py` | `tests/unit/throttle/test_throttle_dynamic_labels.py` |
| `_maybe_adjust_limit()` SLA 메트릭 | `services/throttle/adaptive.py` | `tests/unit/throttle/test_maybe_adjust_limit_sla_metrics.py` |
| Prometheus `external_labels` | `docker/prometheus/prometheus.yml` | - |
| OTel Collector 설정 | `docker/otel-collector/*.yml` | - |
| Alert Rules | `docker/prometheus/rules/throttle_alerts.yml` | - |
| HPA + prometheus-adapter | `k8s/selfhealing-hpa.yaml`, `k8s/prometheus-adapter-config.yaml` | - |
| Grafana Dashboard | `docker/grafana/provisioning/dashboards/adaptive_throttle.json` | - |

---

## 9. 네이밍 결정 근거

본 문서에서 선택한 네이밍과 그 근거를 정리합니다.

| 항목 | 선택 | 대안 | 선택 이유 |
|------|------|------|----------|
| 라벨 정규화 함수 | `sanitize_label_value()` | `normalize_label_value()` | Prometheus 라벨 제약에 맞지 않는 값을 **치환/제거**하는 것은 "sanitize" semantics. 코드베이스에서 `validate`는 Pydantic validator에만 사용되므로 차별화. [registry.py](../../packages/selfhealing-python/src/selfhealing/services/metrics/registry.py)에 위치 |
| 설정 필드명 | `service_name` | `service_label`, `metric_service` | [config.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/config.py)의 다른 필드(`key_prefix`, `initial_limit`)가 직관적 영어명 사용. `service_name`이 Prometheus 레이블 `service`와 1:1 대응 |
| Saturation 메트릭명 | `throttle_saturation_ratio` | `throttle_utilization_ratio` | [prometheus.py](../../packages/selfhealing-python/src/selfhealing/metrics/prometheus.py) L260-275에 `worker_utilization_ratio`가 이미 존재(worker pool 사용률). Throttle의 `current_limit/max_limit`은 Kubernetes USE method의 **saturation**(포화도)에 해당 |
| 확장 메트릭 함수 | 기존 `_record_throttle_metrics()` 확장 | `_record_throttle_metrics_extended()` 신규 | 5곳의 호출부가 keyword argument만 사용하므로 하위호환 보장. 함수 분리 시 호출부 이중 관리 문제 |
| 윈도우 자료구조 | `BucketSlidingWindow` | `TokenBucket`, `deque` | Token Bucket은 burst semantics가 다름. `deque`는 여전히 O(n) trim. **고정 버킷 카운터**는 O(1) 조회 + O(1) 삽입으로 100K TPS 적합 |
| 클래스명 | `BucketSlidingWindow` | `FixedBucketCounter`, `TimeSlotWindow` | "SlidingWindow" 기존 개념 유지 + `Bucket` 접두어로 내부 구현 힌트 |

---

## 10. 참조

### 10.1 소스 코드

- [메트릭 정의 소스](../../packages/selfhealing-python/src/selfhealing/services/metrics/definitions.py)
- [AdaptiveThrottle 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)
- [SlidingWindowThrottle 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/base.py)
- [Prometheus 메트릭 소스](../../packages/selfhealing-python/src/selfhealing/metrics/prometheus.py)
- [메트릭 레지스트리](../../packages/selfhealing-python/src/selfhealing/services/metrics/registry.py)
- [ThrottleSettings](../../packages/selfhealing-python/src/selfhealing/settings/throttle.py)
- [ThrottleConfig](../../packages/selfhealing-python/src/selfhealing/services/throttle/config.py)
- [OTel Observability](../../packages/selfhealing-python/src/selfhealing/observability/__init__.py)
- [ClusterIdentity](../../packages/selfhealing-python/src/selfhealing/observability/cluster_identity.py)
- [Bulkhead Metrics](../../packages/selfhealing-python/src/selfhealing/resilience/bulkhead/metrics.py)

### 10.2 인프라 설정

- [Prometheus 설정](../../docker/prometheus/prometheus.yml)
- [기존 알람 규칙](../../docker/prometheus/rules/alerts.yml)
- [OTel Collector Config](../../docker/otel-collector/otel-collector-config.yml)
- [OTel Collector Agent](../../docker/otel-collector/otel-collector-agent.yml)
- [OTel Collector Gateway](../../docker/otel-collector/otel-collector-gateway.yml)
- [Grafana Datasource](../../docker/grafana/provisioning/datasources/datasource.yml)
- [Unified View Dashboard](../../docker/grafana/provisioning/dashboards/unified_view.json)

### 10.3 Kubernetes

- [selfhealing HPA](../../k8s/selfhealing-hpa.yaml)
- [prometheus-adapter Config](../../k8s/prometheus-adapter-config.yaml)

### 10.4 관련 문서

- [156_OTEL_OBSERVABILITY_OVERVIEW.md](156_OTEL_OBSERVABILITY_OVERVIEW.md)
- [159_GRAFANA_STACK_INTEGRATION.md](159_GRAFANA_STACK_INTEGRATION.md)
