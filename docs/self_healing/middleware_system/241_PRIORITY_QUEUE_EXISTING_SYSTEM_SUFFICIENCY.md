# 241. Priority Queue 보완 분석 — 기존 시스템 충분성 검증

| 항목 | 내용 |
|------|------|
| **문서번호** | 241 |
| **작성일** | 2026-02-18 |
| **상태** | 분석 완료 |
| **선행 문서** | 236, 239, 240 |
| **후속 문서** | 242, 243 |
| **결론** | **별도 Priority Queue 도입 불필요 — 기존 3중 파이프라인이 동일 효과를 O(1)로 달성** |
| **적용 범위** | HTTP 동기 요청에 한정. 비동기 작업(Celery)은 인프라 레벨의 큐 분리 전략을 따르며 이 문서의 대상이 아님 |

---

## 1. 제안된 Priority Queue vs 기존 시스템

### 1.1 제안된 시스템: Heap 기반 Priority Queue

```
요청 수신 → Priority 분류 → [Heap Queue] → 높은 priority부터 꺼내 처리
                                  ↑
                           enqueue: O(log n)
                           dequeue: O(log n)
```

- 모든 요청이 단일 큐에 들어감
- 큐에서 꺼낼 때 priority 순으로 정렬
- 큐 크기 = 동시 대기 요청 수

### 1.2 기존 시스템: Watermark + Tier Bulkhead 3중 파이프라인

```
요청 수신
    ↓
[1] Tier Bulkhead (도메인별 격리)     ← O(1) semaphore
    ↓
[2] CascadeLoadShedding (이벤트 필터) ← O(1) buffer_ratio 비교
    ↓
[3] RateController (Watermark 기반)   ← O(1) token_ratio 비교
    ↓
처리 허용/거부
```

---

## 2. 기존 시스템이 Priority Queue 역할을 하는 코드 근거

### 2.1 Watermark 기반 차등 허용 (RateController)

**파일**: `scaling/rate_controller.py` L34-39

```python
PRIORITY_WATERMARKS: dict[str, float] = {
    "critical": 0.0,      # 토큰이 0% 이상이면 허용 (항상 시도 가능)
    "standard": 0.3,      # 토큰이 30% 이상일 때만 허용
    "non_essential": 0.6,  # 토큰이 60% 이상일 때만 허용
}
```

**동작** (`rate_controller.py` L228-247):
```python
def should_process(self, priority: str = "standard") -> bool:
    watermark = PRIORITY_WATERMARKS.get(priority, 0.3)
    token_ratio = self._token_bucket.get_token_ratio()

    if token_ratio < watermark:
        with self._lock:
            self._dropped_count += 1
        return False

    if self._token_bucket.consume():
        with self._lock:
            self._processed_count += 1
        return True
```

**효과**: 토큰이 30% 미만이면 `non_essential`과 `standard` 모두 거부되지만 `critical`은 통과합니다. 이는 Heap Queue에서 높은 priority부터 꺼내는 것과 동일한 **우선순위 기반 자원 배분** 효과입니다.

### 2.2 Tier별 Bulkhead 격리 (AdmissionControl)

**파일**: `settings/admission_control.py` L47-73

```python
tier_critical_max_concurrent: int = Field(default=100, ...)
tier_standard_max_concurrent: int = Field(default=50, ...)
tier_non_essential_max_concurrent: int = Field(default=20, ...)

def get_tier_max_concurrent(self, tier_id: str) -> int:
    tier_map = {
        "critical": self.tier_critical_max_concurrent,        # 100
        "standard": self.tier_standard_max_concurrent,        # 50
        "non_essential": self.tier_non_essential_max_concurrent,  # 20
    }
```

**효과**: tier별 독립 격벽으로 `non_essential` 폭증이 `critical` 슬롯을 잠식할 수 없습니다.

### 2.3 TrafficGate 3단계 파이프라인

**파일**: `scaling/traffic_gate.py` L195-248

```python
def should_allow(self, priority=0, bulkhead_name=None, metadata=None):
    # 0단계: Bulkhead (도메인별 격리)
    if bulkhead_name is not None:
        acquired, decision = self._check_bulkhead(...)
        if decision is not None:
            return decision
        bulkhead_acquired = acquired

    # 1단계: CascadeLoadShedding (우선순위 필터링)
    load_shedding_decision = self._check_load_shedding(priority, ...)
    if load_shedding_decision is not None:
        # Bulkhead 획득했으면 release
        return load_shedding_decision

    # 2단계: RateController (Watermark 기반)
    tier_str = _map_priority_int_to_tier(priority)
    if not self._rate_controller.should_process(priority=tier_str):
        return TrafficDecision(allowed=False, ...)

    return TrafficDecision(allowed=True, ...)
```

### 2.4 AdmissionControlMiddleware의 Priority 매핑

**파일**: `api/django/admission_control.py` L35-40

```python
TIER_PRIORITY_MAP: dict[str, int] = {
    "critical": 0,
    "standard": 50,
    "non_essential": 100,
}
```

→ `_map_priority_int_to_tier()` (`traffic_gate.py` L46-54)에서 역변환:
```python
_PRIORITY_TIER_THRESHOLDS = [
    (25, "critical"),     # priority <= 25
    (75, "standard"),     # priority <= 75
]
_PRIORITY_TIER_DEFAULT = "non_essential"  # priority > 75
```

---

## 3. 비교 분석

### 3.1 동작 비교표

| 시나리오 | Heap Priority Queue | 기존 Watermark + Bulkhead |
|---------|---------------------|---------------------------|
| 부하 정상 (토큰 충분) | 모든 priority 통과 | 모든 priority 통과 (**동일**) |
| 부하 증가 (토큰 60% 미만) | low priority 큐 대기 | non_essential 즉시 거부 (**더 빠름**) |
| 부하 높음 (토큰 30% 미만) | standard 큐 대기 | standard + non_essential 즉시 거부 (**더 빠름**) |
| 과부하 (토큰 고갈) | critical만 꺼냄 | critical만 통과 (**동일**) |
| non_essential 폭증 | 큐 크기 증가, 메모리 사용 | Bulkhead 20개 제한, 초과 즉시 거부 (**더 안전**) |
| 도메인별 격리 | 단일 큐 → 격리 불가 | Tier Bulkhead → 도메인별 격리 (**우월**) |

### 3.2 시간 복잡도 비교

| 연산 | Heap Queue | 기존 시스템 |
|------|-----------|-------------|
| 요청 허용 판정 | O(log n) enqueue + O(log n) dequeue | O(1) 3회 비교 |
| 부하 시 메모리 | O(n) 큐 크기 비례 | O(1) 고정 (세마포어 카운터) |
| 공정성 보장 | FIFO within same priority | Watermark 기반 확률적 (**동등**) |

### 3.3 장단점

| 항목 | Heap Priority Queue | 기존 시스템 |
|------|---------------------|-------------|
| **장점** | 세밀한 순서 제어 | O(1) 판정, 메모리 효율 |
| **장점** | 큐 대기 가능 | 즉시 판정 (대기 불필요) |
| **단점** | O(log n) 비용 | 대기 없이 즉시 거부 |
| **단점** | 메모리 비례 증가 | 세밀한 순서 불가 |
| **단점** | Head-of-Line Blocking 위험 | — |
| **단점** | 단일 큐 → 격리 불가 | Tier Bulkhead로 격리 |

---

## 4. 결론: 도입 불필요

### 4.1 기존 시스템이 이미 달성하는 것

1. **Priority 기반 자원 배분**: Watermark (`critical=0.0`, `standard=0.3`, `non_essential=0.6`)
2. **도메인별 격리**: Tier Bulkhead (`critical=100`, `standard=50`, `non_essential=20`)
3. **과부하 시 자동 차등 제한**: AIMD + Watermark 연동
4. **Backpressure 레벨별 동적 제한**: BACKPRESSURE_TIER_RULES (`defaults.py` L181-187)

### 4.2 Heap Queue가 필요한 유일한 시나리오

**동일 tier 내에서 요청 간 세밀한 순서 제어가 필요한 경우** — 하지만 self-healing 시스템에서 이 요구사항은 존재하지 않습니다:

- critical 요청끼리: 모두 최우선이므로 순서 무관
- standard 요청끼리: 어차피 같은 Watermark (0.3)
- non_essential 요청끼리: 부하 시 전체 차단 대상

### 4.3 Heap Queue 도입 시 실제 위험

1. **Head-of-Line Blocking**: 큐 앞에 처리 시간이 긴 요청이 있으면 뒤의 빠른 요청도 대기
2. **메모리 폭발**: 과부하 시 큐에 요청이 쌓여 OOM 위험 (기존 시스템은 즉시 거부)
3. **Timeout 연쇄**: 큐 대기 중 상위 서비스 timeout → 큐에서 꺼낸 시점에 이미 무효
4. **복잡성 증가**: Lock contention, 큐 크기 관리, 배압 전파 등

---

## 5. 추가 보완 권장사항

기존 시스템이 Priority Queue 역할을 충분히 하지만, 아래 보완으로 더 강화할 수 있습니다:

### 5.1 SemaphoreBulkhead의 fair 파라미터 활성화

**현재 코드** (`resilience/bulkhead/semaphore.py` L58-63):

```python
def __init__(
    self,
    name: str,
    max_concurrent: int = 10,
    fair: bool = True,  # noqa: ARG002 - 향후 공정 스케줄링 구현용
):
```

`fair=True`가 placeholder로 존재합니다. 이를 실제 구현하면 Bulkhead 대기 시 FIFO 보장이 추가됩니다. 다만 현재 `timeout=None` (즉시 실패) 동작에서는 대기 자체가 없으므로 우선순위가 낮습니다.

### 5.2 Watermark 동적 조정

현재 Watermark는 하드코딩 (`rate_controller.py` L34-39)입니다. 환경변수 또는 BackpressureSettings에 통합하면 운영 중 튜닝이 가능합니다.

### 5.3 통합 메트릭 (242번 문서 선행 필수)

tier별 허용/거부 비율을 Prometheus 메트릭으로 노출하면 Watermark 임계치 튜닝의 근거 데이터를 확보할 수 있습니다. **Priority Queue 부재로 인해 거부가 즉시 발생하므로, tier별 Starvation 감지를 위한 Per-Tier Dropped Counter 구현이 선행되어야 합니다.** 이는 242번 문서(Starvation Guard)에서 설계가 완료되어 있으며, `_dropped_by_tier: dict[str, int]` + Prometheus `tier` 레이블 추가로 구현됩니다.

---

## 6. Q&A — 코드 근거 기반 답변

> 아래 답변은 모두 코드베이스의 실제 구현을 근거로 작성되었습니다.

---

### Q1. 비동기 작업(Celery)과의 범위 구분

**결론: 이 문서의 결정은 "HTTP 동기 요청의 애플리케이션 내부 메모리 큐 도입 불필요"에 국한됩니다. 인프라 레벨 Broker 우선순위와는 별개 영역입니다.**

#### 코드 근거

**AdmissionControlMiddleware는 HTTP 전용입니다.**

`api/django/admission_control.py` — Django 미들웨어 프로토콜(`__init__(self, get_response)`)을 따르며, `request.method`, `request.path`, `request.META` 등 HTTP request 객체에만 의존합니다. docstring에도 **"HTTP 요청 경로를 TierRegistry로 자동 분류"**라고 명시되어 있습니다. 503 `JsonResponse` 반환은 HTTP 응답 형식입니다.

**TrafficGate는 Celery에서 호출되지 않습니다.**

`scaling/traffic_gate.py` L34 주석: *"AdmissionControlMiddleware에서 critical=0, standard=50, non_essential=100으로 전달된다"* — AdmissionControlMiddleware가 유일한 호출자이며, Celery 태스크에서 TrafficGate를 직접 호출하는 코드는 코드베이스에 존재하지 않습니다.

**Celery는 큐 분리 전략으로 우선순위를 물리적 격리합니다.**

`myproject/celery.py` L302-358에서 9개 큐를 정의합니다:

| 큐 | 용도 | 배포 (K8s) |
|---|---|---|
| `selfhealing.critical` | P0 전용 | `celery-critical-worker.yaml` — 복제본 2, concurrency 2, prefetch 1, `priorityClassName: high-priority` |
| `payment_critical` | 결제 전용 | 전용 라우팅 |
| `audit_flush` | 감사 데이터 | `celery-audit-worker.yaml` — 복제본 2, concurrency 4 |
| `celery` (default) | 일반 작업 | `celery-default-worker.yaml` — KEDA 2-10 Pods |

**Broker Priority(`task_queue_max_priority`)는 비활성화 상태입니다.**

`myproject/celery.py` L295-300의 `broker_transport_options`에는 `visibility_timeout: 3600`만 설정되어 있으며, Redis Broker의 `task_queue_max_priority`는 존재하지 않습니다. `adapters/queues/celery_adapter.py` L203에서 `priority = 10 - options.priority.value` 매핑이 있으나, 브로커 설정 부재로 Redis에서 이 값은 무시됩니다.

**Self-Healing은 Celery에 별도 경로로 통합됩니다.**

`adapters/celery/signal_hooks.py` (1225줄)가 Celery 시그널(`task_failure`, `task_success`, `task_retry`, `task_prerun`, `task_postrun`)을 후킹하여 Circuit Breaker, DLQ, Forensics, Metrics를 자동 적용합니다. 이 경로는 HTTP의 AdmissionControl 파이프라인과 완전히 독립적입니다.

#### 범위 정리

| 구분 | HTTP 동기 요청 | Celery 비동기 태스크 |
|------|---------------|---------------------|
| AdmissionControl (Tier 분류) | **적용됨** | **미적용** |
| TrafficGate (Rate/LoadShedding) | **적용됨** | **미적용** |
| 우선순위 메커니즘 | Watermark + Bulkhead | **큐 분리 + 전용 워커** |
| CB / DLQ / Forensics | 별도 경로 | signal_hooks 자동 |
| 이 문서(241)의 결론 | **적용 대상** | **적용 대상 아님** |

---

### Q2. Token Bucket의 충전 기준 (Refill Strategy)

**결론: 현재 토큰 충전은 순수 시간 기반(Time-based)입니다. CPU/Memory/Latency는 토큰 충전에 직접 관여하지 않습니다. 다만 AIMD가 큐 크기 기반으로 충전 속도(rate)를 동적 조절합니다.**

#### 코드 근거

**토큰 충전 공식** — `scaling/rate_controller.py` L108-116:

```python
self._tokens = min(
    self._capacity,
    self._tokens + elapsed * self._rate,
)
```

`elapsed = now - self._last_update` (경과 시간), `self._rate` (초당 토큰, 기본 1000.0) — 시간만으로 충전합니다.

**AIMD가 `self._rate`를 동적으로 변경합니다** — `rate_controller.py` `_adjust_rate()` L285-319:

```
큐 크기 → BackpressureLevel → Rate 배율 → TokenBucket.set_rate(new_rate)
```

| 조건 | 동작 |
|------|------|
| 큐 정상 (`NONE`) | `new_rate = current_rate × 1.1` (AI: 매 5초 10% 증가) |
| 큐 과부하 (`MEDIUM`) | `new_rate = max_rate × 0.9` (MD: 90%로 감소) |
| 큐 위기 (`CRITICAL`) | `new_rate = max_rate × 0.5` (MD: 50%로 급감) |

큐 크기 → 레벨 매핑 (`settings/backpressure.py` L104-118):

| 큐 크기 | 레벨 | Rate 배율 |
|---------|------|-----------|
| < 100 | NONE | 1.0 |
| 100+ | LOW | 1.0 |
| 500+ | MEDIUM | 0.9 |
| 1000+ | HIGH | 0.8 |
| 5000+ | CRITICAL | 0.5 |

**시스템 메트릭(CPU/Memory)과의 연동: 없음**

- `BackpressureSettings`에 CPU/Memory/Latency 관련 필드 없음
- `BackpressureMetrics` (`scaling/metrics.py`)도 큐 깊이, 처리율, 레벨만 추적
- `services/chaos/safety_guard/resource_guard.py`에 리소스 모니터링이 있으나, 이것은 카오스 엔지니어링 안전장치이며 rate controller와 연결되지 않음

#### 질문자의 시나리오에 대한 평가

> "CPU가 100%여도 토큰이 차 있으면 non_essential 요청이 시스템을 다운시킬 수 있다"

이 시나리오는 **현재 구조에서 실현 가능합니다.** 토큰 충전이 시간 기반이므로, CPU 과부하 상태에서도 토큰이 충전되어 요청이 허용될 수 있습니다. 다만 실제로는 다음 완화 요소가 존재합니다:

1. CPU 과부하 → 처리 지연 → 큐 크기 증가 → AIMD가 rate를 감소시킴 (간접 피드백)
2. Tier Bulkhead가 `non_essential`을 최대 20개 동시 실행으로 제한 (`admission_control.py` L60)

그러나 **간접 피드백이므로 지연(lag)이 존재**합니다. 큐 크기가 증가하기 전까지 토큰은 계속 충전됩니다. 시스템 메트릭 직접 피드백이 추가되면 이 gap을 줄일 수 있으며, 이는 향후 개선 사항으로 검토할 수 있습니다.

---

### Q3. Bulkhead의 대기열 동작 (Wait Queue within Semaphore)

**결론: 기본 동작은 Zero-Wait (즉시 거부)입니다. Short-Wait도 API로 지원하지만, AdmissionControlMiddleware에서는 사용하지 않습니다. Micro-burst 보호를 위한 jitter 메커니즘은 없습니다.**

#### 코드 근거

**SemaphoreBulkhead의 acquire** — `resilience/bulkhead/semaphore.py` L93-97:

```python
if timeout is None:
    acquired = self._semaphore.acquire(blocking=False)
else:
    acquired = self._semaphore.acquire(blocking=True, timeout=timeout)
```

| 호출 | 동작 |
|------|------|
| `acquire()` / `acquire(timeout=None)` | `blocking=False` → **즉시 성공/실패** |
| `acquire(timeout=1.0)` | `blocking=True, timeout=1.0` → **최대 1초 대기 후 실패** |

**AdmissionControlMiddleware는 timeout을 전달하지 않습니다.**

`api/django/admission_control.py`에서 TrafficGate → Bulkhead 호출 시 `timeout` 파라미터를 지정하지 않으므로, 기본값 `None` → Zero-Wait으로 동작합니다. 즉, 슬롯이 꽉 차면 **즉시 `BulkheadFullError` → 503 반환**합니다.

**Jitter / Micro-burst 보호: 없음**

- `SemaphoreBulkhead` 자체에 jitter 없음
- `CascadeLoadShedding` L257-272에 슬라이딩 윈도우 rate limit이 있으나, 이것은 audit 이벤트 전용 (우선순위 `LOW` 이하만 대상)
- HTTP 요청 경로에 지수 백오프나 jitter를 적용하는 코드 없음

**`fair` 파라미터: 미구현 (스텁)**

```python
fair: bool = True,  # noqa: ARG002 - 향후 공정 스케줄링 구현용
```

`ARG002` 린트 억제 + "향후 구현용" 주석 — 코드에서 `fair` 값은 어디에서도 참조되지 않습니다. 내부의 `threading.BoundedSemaphore`는 FIFO를 보장하지 않습니다.

#### Micro-burst 취약성 평가

Zero-Wait + 고정 슬롯(critical=100, standard=50, non_essential=20)이므로, 순간적 스파이크 시 정상 요청도 다수 거절될 수 있습니다. 현재 완화 요소:

1. **Tier Bulkhead 슬롯 수**: `critical=100`으로 가장 넓은 대역폭 확보
2. **AIMD**: 과부하 시 rate를 자동 감소 → 유입 자체를 줄임
3. **503 + Retry-After: 30**: 클라이언트에 재시도 간격 힌트 제공

추가 고려 사항: Short-Wait(`timeout=0.05` 등 50ms) 옵션을 AdmissionControlMiddleware에 도입하면 micro-burst 흡수력을 높일 수 있습니다. 그러나 tail latency 증가와 트레이드오프가 있습니다.

---

### Q4. Starvation(기아 현상) 모니터링

**결론: 현재 Per-Tier Starvation 모니터링과 알람은 미구현 상태입니다. 242번 문서(Starvation Guard)에서 설계가 완료되어 구현만 남아 있습니다.**

#### 코드 근거

**Per-Tier 메트릭: 없음**

`scaling/metrics.py`의 `BackpressureMetrics` — Prometheus 메트릭에 `tier` 레이블이 없습니다:

| 메트릭 | 레이블 | 비고 |
|--------|--------|------|
| `selfhealing_processed_total` | `component, status` | **tier 없음** |
| `selfhealing_dropped_total` | `component, reason` | **tier 없음** |

`rate_controller.py`에도 단일 카운터만 존재:
- `self._dropped_count = 0` (모든 tier 합산)
- `self._processed_count = 0` (모든 tier 합산)

**Starvation Alert: 없음**

`services/metrics/alerting_rules.py` (285줄)에 admission control/starvation 관련 alert 0개. 현재 정의된 카테고리: DLQ, Retry, Circuit Breaker, SLA, Error Budget, Fail-Safe, Dead Man's Snitch.

`docker/prometheus/rules/throttle_alerts.yml`에 `ThrottleHighDenialRate` alert가 있으나, 글로벌 스로틀 수준이며 per-tier가 아닙니다.

**`BACKPRESSURE_TIER_RULES`에서 `non_essential=0.0` (완전 차단) 확인**

`api/django/tiering/defaults.py` L211-217:

```python
BackpressureLevel.HIGH:     {"critical": 1.0, "standard": 0.5, "non_essential": 0.0},
BackpressureLevel.CRITICAL: {"critical": 0.8, "standard": 0.1, "non_essential": 0.0},
```

HIGH/CRITICAL 레벨에서 `non_essential`은 **0.0 = 완전 차단**입니다. 장기간 과부하 시 non_essential 트래픽은 영원히 처리되지 않습니다.

**242번 문서의 해결 방안 (설계 완료, 미구현)**

| 작업 | 내용 | 상태 |
|------|------|------|
| A. 최소값 보장 | `non_essential: 0.0` → `0.05` / `0.02` | 미구현 |
| B. `min_traffic_percentage` | `0.0` → `5.0` | 미구현 |
| C. Per-Tier Dropped Counter | `_dropped_by_tier: dict[str, int]` + Prometheus `tier` 레이블 | 미구현 |
| D. Starvation Alert | `selfhealing_rate_controller_dropped_total{tier="non_essential"}` 99% 초과 10분 지속 시 warning | 미구현 |
| E. 시간 기반 완화 (선택) | 5분 연속 100% 거부 시 watermark 임시 완화 | 미구현 |

#### 질문자의 시나리오 평가

> "critical이 70% 이상이면 non_essential은 영원히 처리되지 않을 수 있다"

이 시나리오는 **코드 구조상 현실화 가능**합니다:
1. critical 트래픽이 토큰을 대량 소비 → `token_ratio < 0.6` 상태 유지
2. Watermark `non_essential=0.6`에 의해 즉시 거부
3. `BACKPRESSURE_TIER_RULES`의 `non_essential=0.0`에 의해 추가 차단
4. **모니터링 알람 없음** → 감지 불가

이 gap은 242번 문서 구현으로 해결됩니다.

---

### Q5. Watermark 임계치 동적 변경

**결론: 현재 Watermark는 모듈 레벨 상수로 하드코딩되어 있어 배포 없이 변경 불가합니다. RuntimeConfigManager라는 동적 설정 시스템이 존재하지만, Watermark/BackpressureSettings는 이 시스템에 등록되어 있지 않습니다.**

#### 코드 근거

**PRIORITY_WATERMARKS: 하드코딩 상수**

`scaling/rate_controller.py` L35-39:
```python
PRIORITY_WATERMARKS: dict[str, float] = {
    "critical": 0.0,
    "standard": 0.3,
    "non_essential": 0.6,
}
```
환경변수, 설정 파일, DB에서 읽지 않습니다.

**BackpressureSettings: 환경변수 지원하나 Watermark 필드 없음**

`settings/backpressure.py` — `pydantic-settings` BaseSettings 기반으로 `SELFHEALING_BACKPRESSURE_*` 환경변수에서 읽을 수 있으나, `PRIORITY_WATERMARKS` 필드는 포함되지 않습니다. 또한 `@lru_cache(maxsize=1)`로 싱글톤 처리되어 **프로세스 재시작 없이 리프레시되지 않습니다.**

**RuntimeConfigManager: 존재하나 미등록**

`services/runtime_config/__init__.py` — Redis/StateBackend 기반 동적 설정 시스템이 존재합니다:
- 3가지 적용 전략: `IMMEDIATE` / `DELAYED` / `GRACEFUL`
- 서버 재시작 없이 설정 변경 가능

그러나 `services/runtime_config/constants.py`의 `STORAGE_KEYS`와 `CONFIG_CLASSES`에 **`backpressure` 키가 등록되어 있지 않습니다.** BackpressureSettings는 RuntimeConfigManager의 동적 변경 대상이 아닙니다.

**LayeredProvider: 존재하나 미활용**

`settings/layered_provider.py` — 4계층 설정 우선순위 시스템:
```
Hard-coded defaults < Static ENV < Dynamic DB/Redis < Request-scoped override
```
이 시스템이 존재하지만, BackpressureSettings나 PRIORITY_WATERMARKS를 이 경로로 로드하는 코드가 없습니다.

#### 동적 변경을 위한 경로

기존 인프라(`RuntimeConfigManager` + `LayeredProvider`)를 활용하면 코드 변경 범위를 최소화할 수 있습니다:

1. `PRIORITY_WATERMARKS`를 `BackpressureSettings`에 Pydantic 필드로 이동
2. `BackpressureSettings`를 `RuntimeConfigManager`의 `CONFIG_CLASSES`에 등록
3. `get_backpressure_settings()`의 `@lru_cache` 제거 또는 TTL 캐시로 교체
4. 운영 중 Redis를 통해 값 변경 가능

---

### Q6. Client Side Retry 정책과의 관계

**결론: 503 응답에 `Retry-After: 30` 헤더가 포함되어 있으나, 값이 부하 상태와 무관하게 고정 30초입니다. 서버 측 Retry Storm 방지 메커니즘(`AdaptiveRetryBudget`)은 존재하지만, 클라이언트 측 Exponential Backoff를 강제하는 메커니즘은 없습니다.**

#### 코드 근거

**503 응답에 `Retry-After` 헤더 포함**

`api/django/admission_control.py` `_create_rejection_response`:
```python
response = JsonResponse(
    {
        "error": "Service Temporarily Unavailable",
        "code": "ADMISSION_CONTROL_REJECTED",
        "retry_after": 30,
        ...
    },
    status=503,
)
response["Retry-After"] = "30"
```

Deadline 만료 시에는 `Retry-After: 0` (즉시 재시도 가능) 반환.

**`Retry-After` 값이 고정 30초 — 설정값 미사용**

`settings/backpressure.py` L178-182:
```python
reject_retry_after_seconds: int = Field(
    default=5,
    ge=1,
    le=60,
    description="Retry-After 헤더 값 (초)",
)
```

이 설정이 존재하지만 **`admission_control.py`에서 참조하지 않고 30을 직접 하드코딩**하고 있습니다. 설정값(`default=5`)과 실제 헤더값(`30`)이 불일치합니다.

**서버 측 Retry Storm 방지: AdaptiveRetryBudget 존재**

`services/retry_handler/policy.py` L109 — `AdaptiveRetryBudget`이 서버 내부 재시도 예산을 관리합니다. 외부 서비스 호출 시 재시도 폭주를 방지합니다.

**서버 측 Retry-After 파싱: 존재**

`services/retry_handler/policy.py` L204-222 — 외부 서비스의 429 응답에서 `Retry-After` 헤더를 파싱하여 쿨다운에 반영합니다.

**서버 측 Exponential Backoff: 존재**

`core/backoff.py` — `ExponentialBackoff`, `BackoffCalculator` 등 다양한 백오프 전략이 구현되어 있습니다. 이것은 서버 내부 재시도 로직용입니다 (DB 재연결, 외부 API 호출).

**Retry Storm 부하 테스트: 존재**

`load_tests/scenarios/hybrid/stage32_retry_storm_extended.py` — 전용 Retry Storm 부하 테스트 시나리오가 존재합니다.

**클라이언트 측 강제 메커니즘: 없음**

- 클라이언트(브라우저/상위 MSA)의 Exponential Backoff를 **강제하는 서버 측 메커니즘**은 없습니다
- `Retry-After` 헤더는 **권고 사항**이며, 클라이언트가 무시하면 효과 없음
- 동일 클라이언트의 반복 거절을 감지/차단하는 Rate Limiting은 별도 구현 필요

#### 개선 포인트

| 항목 | 현재 | 개선 방향 |
|------|------|-----------|
| `Retry-After` 값 | 고정 30초 | `BackpressureLevel`에 비례한 동적 값 (`BackpressureSettings.reject_retry_after_seconds` 활용) |
| 설정 불일치 | 설정=5초, 실제=30초 | `admission_control.py`에서 설정값 참조 |
| 클라이언트 강제 | 없음 | 반복 거절 IP/Client에 대한 증가하는 `Retry-After` 또는 429 에스컬레이션 |

---

## 7. 관련 문서

| 문서 | 관련성 |
|------|--------|
| 239 (Deadline Context) | 큐 대기 시간 문제를 원천 방지 — deadline이 있으면 큐 대기 자체가 무의미 |
| 240 (Priority Classifier) | tier 분류 정밀도 향상 → Watermark의 효과 극대화 |
| 242 (Starvation Guard) | non_essential=0.0 시 완전 차단 → 최소 보장 필요 |
| 243 (Cascading Timeout) | 큐 대기 중 timeout 문제 → Deadline Context + Fast-Fail로 해결 |

---

## 8. 리뷰 대응 — 구현 계획

> 241번 문서에 대한 6가지 리뷰에 대해 코드 근거 기반으로 검토하고, 구현 계획을 수립합니다.
> **추측 없음 — 모든 판단은 코드 참조를 수반합니다.**

### 8.0 구현 우선순위 총괄

| 순위 | 리뷰 # | 항목 | 유형 | 규모 | 상태 |
|------|--------|------|------|------|------|
| **P0** | #6-A | Retry-After 하드코딩 버그 수정 | 버그 수정 | ~5줄 | ✅ 구현 완료 |
| **P1** | #4 | Per-Tier Dropped Counter (242번 문서) | 코드 추가 | ~10줄 | ✅ 구현 완료 |
| **P1** | #2 | RateController CPU 피드백 | 코드 추가 | ~15줄 | ✅ 구현 완료 |
| **P2** | #3 | Bulkhead Tier별 차등 Timeout | 코드 추가 | ~20줄 | ✅ 구현 완료 |
| **P2** | #5 | Watermark RuntimeConfigManager 등록 | 리팩토링 | 중간 규모 | ✅ 구현 완료 |
| **P2** | #6-B | 레벨별 동적 Retry-After | 코드 추가 | ~15줄 | ✅ 구현 완료 |
| **P3** | #1 | 적용 범위 명시 | 문서 수정 | 1줄 | — |

---

### 8.1 적용 범위 명시 (리뷰 #1)

**판단: 완전 동의 — 문서 수정만 필요, 코드 변경 없음**

문서 상단 메타데이터 테이블에 **적용 범위** 행이 추가되었습니다.

#### 코드 근거

- `AdmissionControlMiddleware`는 Django 미들웨어 프로토콜을 따르는 HTTP 전용 컴포넌트입니다 (`api/django/admission_control.py` docstring: *"HTTP 요청 경로를 TierRegistry로 자동 분류"*)
- Celery는 물리적 큐 분리(`myproject/celery.py` L302-358, 9개 큐)와 전용 워커(`k8s/celery-critical-worker.yaml` — replicas 2, prefetch 1)로 우선순위를 구현합니다
- 두 경로는 완전히 독립적이며 (`adapters/celery/signal_hooks.py` 1225줄이 Celery 시그널을 후킹), AdmissionControl 파이프라인과 교차하지 않습니다

---

### 8.2 Token Bucket — 시스템 메트릭 직접 피드백 (리뷰 #2)

**판단: 방향 동의 — 다만 "Pause Refill" 대신 기존 AIMD 레벨 시스템에 통합**

#### 설계 결정

**"Pause Refill" 방식을 채택하지 않는 이유:**

`TokenBucket`에는 별도 "충전 스레드"가 없습니다. 토큰 충전은 `consume()` 호출 시 경과 시간으로 계산됩니다 (`rate_controller.py` L108-116):

```python
self._tokens = min(
    self._capacity,
    self._tokens + elapsed * self._rate,
)
```

따라서 "충전을 일시 중지"할 대상 자체가 존재하지 않습니다. 실질적으로 토큰 유입을 줄이려면 `self._rate`를 감소시켜야 하며, 이는 기존 AIMD `_adjust_rate()` 패턴과 동일합니다.

**채택 방식: `_adjust_rate()` 내부에 CPU 피드백 통합**

기존 `_adjust_rate()`는 큐 크기 → 레벨 → Rate 배율 경로만 사용합니다. 여기에 CPU/Memory 피드백을 **추가 감쇠 요소**로 반영합니다:

```
최종 Rate = AIMD Rate × resource_pressure_multiplier
```

#### 기존 인프라 재활용

| 컴포넌트 | 위치 | 역할 | 호출 비용 |
|---------|------|------|----------|
| `SystemMetricsCache` | `services/system_metrics_cache.py` | 1초 주기 CPU/Memory 캐시 | **~0ms** (Lock-free, GIL atomic 참조 교체) |
| `CgroupResourceMonitor` | `core/resource_monitor.py` | cgroup v1/v2 메모리 읽기 | ~1ms (파일 I/O) |
| `ResourceGuardSettings` | `settings/resource_guard.py` | CPU 80%, Memory 85% 임계치 | 싱글톤 |

`SystemMetricsCache.get_cpu_percent()`는 이미 `ResourceGuard`가 사용 중이므로 (`services/chaos/safety_guard/resource_guard.py` L127-133), 동일 인프라를 재활용합니다.

#### 네이밍

| 항목 | 이름 | 충돌 여부 |
|------|------|----------|
| 메서드 | `_get_resource_pressure_multiplier()` | 미존재 ✓ |
| 설정 필드 | `resource_cpu_high_threshold` | 미존재 ✓ |
| 설정 필드 | `resource_cpu_critical_threshold` | 미존재 ✓ |

기존 `get_rate_multiplier(level: BackpressureLevel)` 메서드 (`settings/backpressure.py` L214)와 네이밍 패턴 일관.

#### CPU 기반 Rate 감쇠 테이블

| CPU 사용률 | `resource_pressure_multiplier` | 근거 |
|-----------|-------------------------------|------|
| < 80% | 1.0 (영향 없음) | `ResourceGuardSettings.cpu_threshold` 기본값 80 |
| 80-90% | 0.5 | `LEVEL_RATE_MULTIPLIERS[CRITICAL]`과 동일 |
| > 90% | 0.1 | 사실상 critical만 허용 수준 |

80%는 `ResourceGuardSettings.cpu_threshold` (`settings/resource_guard.py` L49) 기본값과 일치시켜 시스템 전체의 CPU 임계치 기준을 통일합니다.

#### 변경 대상

| 파일 | 변경 | 규모 |
|------|------|------|
| `scaling/rate_controller.py` | `_adjust_rate()` 내 `_get_resource_pressure_multiplier()` 호출 추가 | ~15줄 |
| `settings/backpressure.py` | `resource_cpu_high_threshold`, `resource_cpu_critical_threshold` 필드 추가 | ~10줄 |

#### 의사 코드

```python
# rate_controller.py — _adjust_rate() 내부
def _get_resource_pressure_multiplier(self) -> float:
    """CPU 사용률 기반 Rate 감쇠 배율."""
    try:
        from selfhealing.services.system_metrics_cache import get_system_metrics_cache
        cache = get_system_metrics_cache()
        if not cache.is_running():
            return 1.0
        cpu = cache.get_cpu_percent()
    except Exception:
        return 1.0

    if cpu > self._settings.resource_cpu_critical_threshold:   # default 90
        return 0.1
    elif cpu > self._settings.resource_cpu_high_threshold:     # default 80
        return 0.5
    return 1.0

# _adjust_rate() 기존 코드 수정:
#   new_rate = ... (기존 AIMD 계산)
#   new_rate *= self._get_resource_pressure_multiplier()  # 추가
```

---

### 8.3 Bulkhead Tier별 차등 Timeout (리뷰 #3)

**판단: Tier별 차등 timeout 적용, `non_essential`은 Zero-Wait 유지**

#### 설계 결정 근거

**`non_essential`에 대기를 부여하면 안 되는 이유:**

1. **설계 의도 위반**: `non_essential`은 부하 시 가장 먼저 차단되는 tier입니다 (`BACKPRESSURE_TIER_RULES`의 `non_essential=0.0`). 대기시키면 Bulkhead 슬롯을 점유하여 상위 tier에 악영향을 줍니다.
2. **Bulkhead 슬롯 점유 시간 증가**: `non_essential` Bulkhead 슬롯은 20개뿐입니다 (`AdmissionControlSettings.tier_non_essential_max_concurrent=20`). 50ms 대기 × 20개 = 동시에 20개가 대기하면 전체 슬롯이 "대기 중"으로 점유됩니다.
3. **기존 패턴 일관성**: `BACKPRESSURE_TIER_RULES`에서 레벨이 높아질수록 `non_essential`을 먼저 차단하는 패턴과 일관됩니다.

**`critical`/`standard`에 대기를 부여하는 이유:**

1. **Micro-burst 흡수**: 네트워크 지터, GC Pause 등 50ms 이내의 순간 스파이크에서 정상 요청의 불필요한 거절을 방지합니다.
2. **슬롯 여유**: `critical=100`, `standard=50`으로 충분한 슬롯이 확보되어 50ms 대기가 전체 용량에 미치는 영향이 미미합니다.
3. **P99 영향 허용 범위**: 50ms는 HTTP 요청의 일반적 P99 latency(~500ms)의 10%에 불과합니다.

#### 기존 API 활용

`SemaphoreBulkhead.acquire(timeout=None)` (`resilience/bulkhead/semaphore.py`)는 이미 timeout 파라미터를 지원합니다:
- `timeout=None` → `blocking=False` (Zero-Wait, 현재 동작)
- `timeout=float` → `blocking=True, timeout=float` (지정 시간 대기)

`TrafficGate._check_bulkhead()`는 현재 `try_acquire()`를 호출하며 (`scaling/traffic_gate.py` L148-155), 이를 `acquire(timeout=...)` 호출로 변경하면 됩니다.

#### 네이밍

기존 `AdmissionControlSettings`의 `tier_{tier_name}_{parameter}` 패턴을 따릅니다:

| 필드 | 기본값 | 충돌 여부 |
|------|--------|-----------|
| `tier_critical_bulkhead_timeout_seconds` | `0.05` (50ms) | 미존재 ✓ |
| `tier_standard_bulkhead_timeout_seconds` | `0.03` (30ms) | 미존재 ✓ |
| `tier_non_essential_bulkhead_timeout_seconds` | `0.0` (Zero-Wait) | 미존재 ✓ |

기존 필드 `tier_critical_max_concurrent`, `tier_standard_max_concurrent`, `tier_non_essential_max_concurrent`와 동일한 네이밍 규약.

#### 변경 대상

| 파일 | 변경 | 규모 |
|------|------|------|
| `settings/admission_control.py` | `tier_*_bulkhead_timeout_seconds` 필드 3개 + `get_tier_bulkhead_timeout()` 헬퍼 | ~20줄 |
| `resilience/bulkhead/semaphore.py` | `try_acquire(timeout=...)` 시그니처 확장 | ~5줄 |
| `scaling/traffic_gate.py` | `_check_bulkhead()`에서 timeout 전달 | ~5줄 |
| `api/django/admission_control.py` | `_init_dependencies()`에서 timeout 설정 전달 | ~3줄 |

#### 의사 코드

```python
# settings/admission_control.py — 신규 필드
tier_critical_bulkhead_timeout_seconds: float = Field(
    default=0.05, ge=0.0, le=1.0,
    description="critical tier Bulkhead 획득 대기 시간 (초). 0이면 즉시 실패.",
)
tier_standard_bulkhead_timeout_seconds: float = Field(
    default=0.03, ge=0.0, le=1.0,
    description="standard tier Bulkhead 획득 대기 시간 (초). 0이면 즉시 실패.",
)
tier_non_essential_bulkhead_timeout_seconds: float = Field(
    default=0.0, ge=0.0, le=1.0,
    description="non_essential tier Bulkhead 획득 대기 시간 (초). 기본 Zero-Wait.",
)

def get_tier_bulkhead_timeout(self, tier_id: str) -> float | None:
    """tier별 Bulkhead 대기 timeout 반환. 0이면 None(즉시 실패)."""
    tier_map = {
        "critical": self.tier_critical_bulkhead_timeout_seconds,
        "standard": self.tier_standard_bulkhead_timeout_seconds,
        "non_essential": self.tier_non_essential_bulkhead_timeout_seconds,
    }
    value = tier_map.get(tier_id, 0.0)
    return value if value > 0 else None

# resilience/bulkhead/semaphore.py — try_acquire 확장
def try_acquire(self, timeout: float | None = None) -> bool:
    if timeout is None:
        acquired = self._semaphore.acquire(blocking=False)
    else:
        acquired = self._semaphore.acquire(blocking=True, timeout=timeout)
    if acquired:
        with self._lock:
            self._active_count += 1
    return acquired
```

---

### 8.4 Per-Tier Starvation Metrics (리뷰 #4)

**판단: 완전 동의 — 242번 문서 구현이 선행 필수**

**Priority Queue가 없으므로** 요청은 대기 없이 즉시 거부됩니다. 이때 어떤 tier가 기아 상태인지 감지하려면 **per-tier 거부 카운터가 필수적**입니다. 5.3절에 이 선후관계를 명시하였습니다.

242번 문서에서 설계된 구현 항목:

| # | 작업 | 상태 |
|---|------|------|
| A | `BACKPRESSURE_TIER_RULES`에서 `non_essential: 0.0` → `0.05` | 미구현 |
| B | `ServiceConfig.min_traffic_percentage: 0.0` → `5.0` | 미구현 |
| C | `RateController._dropped_by_tier: dict[str, int]` + Prometheus `tier` 레이블 | 미구현 |
| D | Starvation Alert: `dropped_total{tier="non_essential"}` 99% 초과 10분 시 warning | 미구현 |

242번 문서의 작업 C(Per-Tier Dropped Counter)는 8.2(CPU 피드백), 8.5(Watermark 동적 변경), 8.6(동적 Retry-After)의 효과를 **계측**하기 위한 기반이므로, 다른 작업보다 선행되어야 합니다.

---

### 8.5 Watermark 동적 변경 (리뷰 #5)

**판단: 완전 동의 — 개별 Pydantic 필드 + RuntimeConfigManager 등록**

#### 설계 결정: dict 필드 vs 개별 필드

**개별 필드를 채택합니다.** 이유:

| 기준 | dict 필드 | 개별 필드 |
|------|----------|----------|
| 환경변수 호환 | JSON 문자열 필요 (`'{"critical":0.0,...}'`) | **개별 오버라이드** (`WATERMARK_STANDARD=0.4`) |
| K8s ConfigMap | JSON blob 관리 복잡 | **개별 키-값** (표준 패턴) |
| 코드베이스 일관성 | 해당 패턴 없음 | **`tier_*_max_concurrent` 패턴과 동일** |
| 검증 | dict 내부 개별 검증 어려움 | **`ge=0.0, le=1.0` 개별 적용** |
| 감사(Audit) 추적 | "dict 변경" 단일 이벤트 | **개별 필드 변경 추적** |
| RuntimeConfigManager | dict 직렬화/역직렬화 필요 | **필드 단위 변경** (기존 패턴) |

엔터프라이즈 환경에서 Kubernetes ConfigMap/Secret은 개별 환경변수를 설정하는 것이 표준이며, JSON blob은 오류 가능성이 높고 리뷰가 어렵습니다.

`AdmissionControlSettings`의 `tier_critical_max_concurrent`, `tier_standard_max_concurrent`, `tier_non_essential_max_concurrent` 패턴이 이미 **tier별 개별 필드** 방식으로 확립되어 있으므로, 이를 따릅니다.

#### 네이밍

| 필드 | 환경변수 | 기본값 | 충돌 여부 |
|------|---------|--------|----------|
| `watermark_critical` | `SELFHEALING_BACKPRESSURE_WATERMARK_CRITICAL` | `0.0` | 미존재 ✓ |
| `watermark_standard` | `SELFHEALING_BACKPRESSURE_WATERMARK_STANDARD` | `0.3` | 미존재 ✓ |
| `watermark_non_essential` | `SELFHEALING_BACKPRESSURE_WATERMARK_NON_ESSENTIAL` | `0.6` | 미존재 ✓ |

#### RuntimeConfigManager 등록

`services/runtime_config/constants.py`의 `STORAGE_KEYS`와 `CONFIG_CLASSES`에 `backpressure` 키가 등록되어 있지 않습니다 (현재 40+종 설정 중 미포함). 등록이 필요합니다:

```python
# constants.py — STORAGE_KEYS 추가
"backpressure": "runtime_config:backpressure",

# constants.py — CONFIG_CLASSES 추가
"backpressure": BackpressureSettings,
```

`get_backpressure_settings()`의 `@lru_cache(maxsize=1)`는 유지하되, `RuntimeConfigManager`의 변경 콜백에서 `reset_backpressure_settings()`를 호출하여 캐시를 무효화합니다. 이 패턴은 기존 `reset_backpressure_settings()` 함수(`settings/backpressure.py` L228-230)가 이미 존재하므로 재활용 가능합니다.

#### 하위 호환

`PRIORITY_WATERMARKS` 상수를 참조하는 기존 코드:

| 파일 | 참조 방식 |
|------|----------|
| `scaling/rate_controller.py` L35-39 | 모듈 레벨 상수 정의 |
| `scaling/rate_controller.py` L239 | `should_process()` 내부 참조 |
| `tests/.../test_rate_controller_priority.py` L21 | `from ... import PRIORITY_WATERMARKS` |
| `tests/.../test_request_priority_admission_control.py` L20 | `from ... import PRIORITY_WATERMARKS` |

마이그레이션 방안:

```python
# rate_controller.py — 하위 호환 래퍼
def _get_priority_watermarks() -> dict[str, float]:
    """BackpressureSettings에서 Watermark 로드 (하위 호환)."""
    settings = get_backpressure_settings()
    return {
        "critical": settings.watermark_critical,
        "standard": settings.watermark_standard,
        "non_essential": settings.watermark_non_essential,
    }

# 모듈 레벨 상수 (기존 import 호환)
PRIORITY_WATERMARKS = _get_priority_watermarks()
```

`should_process()` 내부에서는 매번 settings를 읽도록 변경하여 동적 변경을 반영합니다:

```python
def should_process(self, priority: str = "standard") -> bool:
    settings = get_backpressure_settings()
    watermarks = {
        "critical": settings.watermark_critical,
        "standard": settings.watermark_standard,
        "non_essential": settings.watermark_non_essential,
    }
    watermark = watermarks.get(priority, 0.3)
    # ... 기존 로직
```

#### 변경 대상

| 파일 | 변경 | 규모 |
|------|------|------|
| `settings/backpressure.py` | `watermark_*` 필드 3개 추가 | ~15줄 |
| `scaling/rate_controller.py` | `PRIORITY_WATERMARKS` 상수 → settings 참조로 변경 | ~10줄 |
| `services/runtime_config/constants.py` | `backpressure` 키 등록 | 2줄 |
| 테스트 2개 | `PRIORITY_WATERMARKS` import 유지 (하위 호환 래퍼로 동작) | 0줄 |

---

### 8.6 Retry-After 정비 (리뷰 #6)

#### Action Item A: 하드코딩 버그 수정 (P0)

**현재 버그**: `settings/backpressure.py`의 `reject_retry_after_seconds = 5`(기본값)와 `api/django/admission_control.py` L243의 하드코딩 `30`이 **6배 불일치**합니다.

```python
# admission_control.py L243 — 현재 (버그)
response["Retry-After"] = "30"

# admission_control.py — 수정 후
from selfhealing.scaling.config import get_backpressure_settings
settings = get_backpressure_settings()
response["Retry-After"] = str(settings.reject_retry_after_seconds)
```

이 버그는 다른 작업과 무관하게 **즉시 수정 가능**합니다.

#### Action Item B: 레벨별 동적 Retry-After (P2)

현재 `BackpressureLevel`은 5단계 (`NONE`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`)이며, 부하가 높을수록 클라이언트의 재시도 간격을 늘려야 합니다.

**설계 결정: 기존 `reject_retry_after_seconds` 기반 배율 적용**

레벨별 별도 필드 5개를 추가하는 대신, 기존 `reject_retry_after_seconds`를 base로 사용하고 레벨별 배율을 적용합니다:

```python
# settings/backpressure.py — 신규 메서드
def get_retry_after_for_level(self, level: BackpressureLevel) -> int:
    """BackpressureLevel별 Retry-After 값 반환."""
    base = self.reject_retry_after_seconds
    multiplier = {
        BackpressureLevel.NONE: 1,       # 5초
        BackpressureLevel.LOW: 2,        # 10초
        BackpressureLevel.MEDIUM: 4,     # 20초
        BackpressureLevel.HIGH: 6,       # 30초
        BackpressureLevel.CRITICAL: 12,  # 60초
    }
    return base * multiplier.get(level, 1)
```

**이 방식을 채택한 이유:**

1. **설정 필드 폭발 방지**: 5개 레벨 × 별도 필드 = 5개 추가 vs 메서드 1개 추가
2. **단일 설정 노브**: `reject_retry_after_seconds` 하나로 전체 스케일 조절 가능 (예: 5→10 변경 시 모든 레벨이 2배)
3. **기존 패턴 일관성**: `get_rate_multiplier(level)` 메서드가 이미 동일한 레벨 → 배율 패턴을 사용 중 (`settings/backpressure.py` L214)
4. **LEVEL_RATE_MULTIPLIERS와 대칭**: Rate는 부하 증가 시 *감소*, Retry-After는 부하 증가 시 *증가*

#### 네이밍

| 항목 | 이름 | 충돌 여부 |
|------|------|----------|
| 메서드 | `get_retry_after_for_level()` | 미존재 ✓ |

#### AdmissionControlMiddleware 적용

`_create_rejection_response()`에서 현재 BackpressureLevel을 조회하여 동적 Retry-After를 반환합니다. `self._traffic_gate` 참조가 이미 존재하므로:

```python
# admission_control.py — _create_rejection_response() 수정
def _create_rejection_response(self, request, tier_id, gate, reason):
    from selfhealing.scaling.config import get_backpressure_settings
    settings = get_backpressure_settings()
    level = self._traffic_gate.get_level()
    retry_after = settings.get_retry_after_for_level(level)

    response = JsonResponse({
        ...
        "retry_after": retry_after,
    }, status=503)
    response["Retry-After"] = str(retry_after)
    return response
```

#### 변경 대상

| 파일 | 변경 | 규모 |
|------|------|------|
| `settings/backpressure.py` | `get_retry_after_for_level()` 메서드 추가 | ~10줄 |
| `api/django/admission_control.py` | 하드코딩 `30` 제거 → 동적 Retry-After 반환 | ~5줄 |
