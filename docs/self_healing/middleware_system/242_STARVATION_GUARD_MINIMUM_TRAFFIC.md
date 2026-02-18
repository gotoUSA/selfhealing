# 242. Starvation Guard — 최소 트래픽 보장 + Per-Tier Drop Counter

| 항목 | 내용 |
|------|------|
| **문서번호** | 242 |
| **작성일** | 2026-02-18 |
| **상태** | 설계 완료 |
| **선행 문서** | 236, 239, 240, 241 |
| **후속 문서** | 243 |
| **작업 규모** | 설정 변경 + ~10줄 코드 추가 |

---

## 1. 배경 및 문제 정의

### 1.1 현재 Starvation 위험 지점

두 곳에서 `non_essential` 트래픽이 **완전 차단(0.0)**됩니다:

#### 지점 1: BACKPRESSURE_TIER_RULES

**파일**: `api/django/tiering/defaults.py` L181-187

```python
BACKPRESSURE_TIER_RULES: dict[BackpressureLevel, dict[str, float]] = {
    BackpressureLevel.NONE:     {"critical": 1.0, "standard": 1.0, "non_essential": 1.0},
    BackpressureLevel.LOW:      {"critical": 1.0, "standard": 1.0, "non_essential": 0.5},
    BackpressureLevel.MEDIUM:   {"critical": 1.0, "standard": 0.8, "non_essential": 0.2},
    BackpressureLevel.HIGH:     {"critical": 1.0, "standard": 0.5, "non_essential": 0.0},  # ← 완전 차단
    BackpressureLevel.CRITICAL: {"critical": 0.8, "standard": 0.1, "non_essential": 0.0},  # ← 완전 차단
}
```

`HIGH`, `CRITICAL` 레벨에서 `non_essential: 0.0` → 대시보드, 메트릭 조회가 **무기한 차단**됩니다.

#### 지점 2: ServiceConfig.min_traffic_percentage

**파일**: `services/circuit_breaker/models.py` L55

```python
min_traffic_percentage: float = 0.0
```

기본값이 `0.0`이므로, 서비스별로 명시적으로 설정하지 않는 한 최소 보장이 작동하지 않습니다.

LoadSheddingManager에서 이 값을 사용하는 코드 (`load_shedding/manager.py` L227):
```python
return max(applicable_level.traffic_limit, service_config.min_traffic_percentage)
```

`min_traffic_percentage=0.0`이면 `max(0.0, 0.0) = 0.0` → 보장 없음.

#### 지점 3: RateController._dropped_count 집계

**파일**: `rate_controller.py` L201

```python
self._dropped_count = 0
```

단일 카운터로 모든 tier의 거부를 합산합니다. tier별 거부 빈도를 알 수 없어 starvation 감지가 불가능합니다.

### 1.2 Starvation 시나리오

```
BackpressureLevel이 HIGH로 진입
    ↓
BACKPRESSURE_TIER_RULES[HIGH]["non_essential"] = 0.0
    ↓
대시보드/메트릭 API 완전 차단
    ↓
운영자가 시스템 상태 확인 불가
    ↓
장애 대응 지연 (관측 불가능)
    ↓
장애 확대
```

---

## 2. 설계

### 2.1 3가지 보완 작업

| # | 작업 | 유형 | 규모 |
|---|------|------|------|
| A | BACKPRESSURE_TIER_RULES 최소값 보장 | 설정 변경 | 2줄 |
| B | ServiceConfig.min_traffic_percentage 기본값 변경 | 설정 변경 | 1줄 |
| C | RateController per-tier dropped counter | 코드 추가 | ~10줄 |

---

## 3. 구현 상세

### 3.1 작업 A: BACKPRESSURE_TIER_RULES 최소값

**파일**: `api/django/tiering/defaults.py`

```python
# 변경 전
BackpressureLevel.HIGH:     {"critical": 1.0, "standard": 0.5, "non_essential": 0.0},
BackpressureLevel.CRITICAL: {"critical": 0.8, "standard": 0.1, "non_essential": 0.0},

# 변경 후
BackpressureLevel.HIGH:     {"critical": 1.0, "standard": 0.5, "non_essential": 0.05},
BackpressureLevel.CRITICAL: {"critical": 0.8, "standard": 0.1, "non_essential": 0.02},
```

**5%/2% 근거**:
- `non_essential`의 기본 Bulkhead 슬롯: 20개 (`admission_control.py` L68)
- 5%일 때 허용 슬롯: 20 × 0.05 = 1개 (확률적으로 20회 중 1회 허용)
- 대시보드 새로고침이 ~10초 간격이면, 200초(~3분)에 1회는 성공
- 운영자가 시스템 상태를 **완전히 확인 불가**한 상태는 방지

### 3.2 작업 B: min_traffic_percentage 기본값

**파일**: `services/circuit_breaker/models.py`

```python
# 변경 전
min_traffic_percentage: float = 0.0

# 변경 후
min_traffic_percentage: float = 5.0
```

**영향 범위**:
- `LoadSheddingManager.evaluate_shedding()` (`manager.py` L227)에서:
  ```python
  return max(applicable_level.traffic_limit, service_config.min_traffic_percentage)
  # 변경 후: max(0.0, 5.0) = 5.0 → 최소 5% 보장
  ```
- `shed_priority=0` (critical 서비스)는 `evaluate_shedding()`에서 `100.0`을 반환하므로 영향 없음
- 기존에 `min_traffic_percentage`를 명시적으로 설정한 서비스는 영향 없음

### 3.3 작업 C: Per-Tier Dropped Counter

**파일**: `scaling/rate_controller.py`

#### 변경 1: 카운터 초기화

```python
class RateController:
    def __init__(self, ...):
        # 기존
        self._processed_count = 0
        self._dropped_count = 0

        # 신규: tier별 dropped counter
        self._dropped_by_tier: dict[str, int] = {
            "critical": 0,
            "standard": 0,
            "non_essential": 0,
        }
```

#### 변경 2: should_process()에서 tier별 카운팅

```python
def should_process(self, priority: str = "standard") -> bool:
    if not self._settings.backpressure_enabled:
        return True

    watermark = PRIORITY_WATERMARKS.get(priority, 0.3)
    token_ratio = self._token_bucket.get_token_ratio()

    if token_ratio < watermark:
        with self._lock:
            self._dropped_count += 1
            # 신규: tier별 카운팅 (1줄 추가)
            self._dropped_by_tier[priority] = self._dropped_by_tier.get(priority, 0) + 1
        logger.info(...)
        return False

    if self._token_bucket.consume():
        with self._lock:
            self._processed_count += 1
        return True

    # 토큰 부족 시 (기존 전략 분기 내)
    # ... 거부 경로에서도 동일하게 tier별 카운팅 추가:
    with self._lock:
        self._dropped_count += 1
        self._dropped_by_tier[priority] = self._dropped_by_tier.get(priority, 0) + 1
```

#### 변경 3: RateControllerState에 tier별 카운터 노출

```python
@dataclass
class RateControllerState:
    current_rate: float
    target_rate: float
    level: BackpressureLevel
    queue_size: int
    processed_count: int
    dropped_count: int
    dropped_by_tier: dict[str, int] = field(default_factory=dict)  # 신규

def get_state(self) -> RateControllerState:
    with self._lock:
        return RateControllerState(
            current_rate=self._current_rate,
            target_rate=self._settings.max_rate_per_second,
            level=self._level,
            queue_size=self._queue_size_provider(),
            processed_count=self._processed_count,
            dropped_count=self._dropped_count,
            dropped_by_tier=dict(self._dropped_by_tier),  # 신규: 복사본 반환
        )
```

---

## 4. Prometheus 메트릭

Per-tier dropped counter를 활용한 메트릭:

```python
# scaling/metrics.py에 추가

from prometheus_client import Counter

rate_controller_dropped_total = Counter(
    "selfhealing_rate_controller_dropped_total",
    "RateController tier별 거부 횟수",
    ["tier"],  # "critical", "standard", "non_essential"
)
```

`should_process()` 거부 시:
```python
rate_controller_dropped_total.labels(tier=priority).inc()
```

### 4.1 Starvation 감지 Alerting Rule

```yaml
# Starvation Guard Alert
- alert: TierStarvation
  expr: |
    rate(selfhealing_rate_controller_dropped_total{tier="non_essential"}[5m])
    /
    (rate(selfhealing_rate_controller_dropped_total{tier="non_essential"}[5m])
     + rate(selfhealing_rate_controller_processed_total{tier="non_essential"}[5m]))
    > 0.99
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "non_essential tier 99% 이상 거부 — starvation 의심"
```

---

## 5. 선택적 확장: 시간 기반 Watermark 완화

### 5.1 문제

`non_essential`이 연속 N분 이상 100% 거부되면, 해당 tier의 watermark를 일시적으로 완화하여 최소한의 트래픽을 허용합니다.

### 5.2 구현 스케치

```python
# rate_controller.py에 추가 (~15줄)

# 연속 거부 시간 추적
_tier_last_allowed: dict[str, float]  # tier → 마지막 허용 시각 (monotonic)
_STARVATION_RELIEF_SECONDS = 300.0    # 5분 연속 거부 시 완화
_STARVATION_RELIEF_WATERMARK = 0.3    # 완화 시 watermark (standard와 동일)

def should_process(self, priority: str = "standard") -> bool:
    # ... 기존 watermark 확인 ...

    # Starvation Relief: N분간 한 번도 허용 안 되었으면 watermark 임시 완화
    if token_ratio < watermark and priority in self._tier_last_allowed:
        elapsed = time.time() - self._tier_last_allowed[priority]
        if elapsed > self._STARVATION_RELIEF_SECONDS:
            watermark = min(watermark, _STARVATION_RELIEF_WATERMARK)
            logger.warning(
                "[RateController] Starvation relief: tier=%s, "
                "elapsed=%.0fs, relaxed_watermark=%.2f",
                priority, elapsed, watermark,
            )
```

이 확장은 선택 사항이며, 작업 A+B의 설정 변경만으로도 기본적인 starvation 방지가 충분합니다.

---

## 6. 테스트 전략

### 6.1 단위 테스트

```
tests/unit/scaling/test_starvation_guard.py
├── TestPerTierDroppedCounter
│   ├── test_dropped_by_tier_increments      # non_essential 거부 시 카운터 증가
│   ├── test_dropped_by_tier_isolation       # critical 거부 시 non_essential 카운터 불변
│   ├── test_get_state_includes_tier_counts  # RateControllerState에 포함 확인
│   └── test_counter_thread_safety           # 멀티스레드 동시 접근
├── TestBackpressureTierRules
│   ├── test_high_level_non_essential_minimum # HIGH에서 non_essential ≥ 0.05
│   ├── test_critical_level_all_tiers        # CRITICAL에서 각 tier 최소값 확인
│   └── test_none_level_all_allowed          # NONE에서 모든 tier 1.0
├── TestMinTrafficPercentage
│   ├── test_default_value_5_percent         # 기본값 5.0 확인
│   ├── test_evaluate_shedding_minimum       # traffic_limit=0 + min=5 → 5
│   └── test_critical_services_unaffected    # critical 서비스는 100% 유지
```

### 6.2 회귀 테스트

- 기존 `BACKPRESSURE_TIER_RULES` 참조 테스트: `0.0` → `0.05`로 변경되므로 관련 assertion 수정 필요
- 기존 `ServiceConfig` 테스트: `min_traffic_percentage` 기본값이 0.0에서 5.0으로 변경됨

---

## 7. 영향 분석

### 7.1 성능 영향

| 변경 | 영향 |
|------|------|
| BACKPRESSURE_TIER_RULES 값 변경 | 없음 (비교 임계치만 변경) |
| min_traffic_percentage 기본값 변경 | 없음 (비교 임계치만 변경) |
| Per-tier dropped counter | 무시: dict 키 lookup + int increment (O(1)) |

### 7.2 동작 변경

| 시나리오 | 변경 전 | 변경 후 |
|---------|--------|--------|
| HIGH + non_essential 요청 | 100% 거부 | **95% 거부** (5% 허용) |
| CRITICAL + non_essential 요청 | 100% 거부 | **98% 거부** (2% 허용) |
| LoadShedding + min_traffic 미지정 | 0% 보장 | **5% 보장** |

### 7.3 Fail-Open 안전성

모든 변경이 **기존보다 더 많이 허용**하는 방향입니다:
- `0.0 → 0.05`: 더 많은 트래픽 허용
- `0.0 → 5.0`: 최소 보장 트래픽 증가
- Per-tier counter: 읽기 전용 추가 → 기존 동작 변경 없음
