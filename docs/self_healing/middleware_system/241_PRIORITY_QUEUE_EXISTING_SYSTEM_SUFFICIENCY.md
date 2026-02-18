# 241. Priority Queue 보완 분석 — 기존 시스템 충분성 검증

| 항목 | 내용 |
|------|------|
| **문서번호** | 241 |
| **작성일** | 2026-02-18 |
| **상태** | 분석 완료 |
| **선행 문서** | 236, 239, 240 |
| **후속 문서** | 242, 243 |
| **결론** | **별도 Priority Queue 도입 불필요 — 기존 3중 파이프라인이 동일 효과를 O(1)로 달성** |

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

### 5.3 통합 메트릭

tier별 허용/거부 비율을 Prometheus 메트릭으로 노출하면 Watermark 임계치 튜닝의 근거 데이터를 확보할 수 있습니다. 이는 242번 문서 (Starvation Guard)에서 `per-tier dropped counter` 구현 시 함께 추가됩니다.

---

## 6. 관련 문서

| 문서 | 관련성 |
|------|--------|
| 239 (Deadline Context) | 큐 대기 시간 문제를 원천 방지 — deadline이 있으면 큐 대기 자체가 무의미 |
| 240 (Priority Classifier) | tier 분류 정밀도 향상 → Watermark의 효과 극대화 |
| 242 (Starvation Guard) | non_essential=0.0 시 완전 차단 → 최소 보장 필요 |
| 243 (Cascading Timeout) | 큐 대기 중 timeout 문제 → Deadline Context + Fast-Fail로 해결 |
