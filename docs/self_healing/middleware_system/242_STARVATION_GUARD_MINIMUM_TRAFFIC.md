# 242. Starvation Guard — 최소 트래픽 보장 + Per-Tier Drop Counter

| 항목 | 내용 |
|------|------|
| **문서번호** | 242 |
| **작성일** | 2026-02-18 |
| **상태** | 설계 완료 |
| **선행 문서** | 236, 239, 240, 241 |
| **후속 문서** | 243 |
| **작업 규모** | 설정 변경 + ~30줄 코드 추가 |

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
| D | Degraded tier forced deadline (Heavy Query 방어) | 코드 추가 | ~10줄 |
| E | Per-tier processed counter + Alert 최소 볼륨 | 코드 추가 | ~8줄 |

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

### 3.4 작업 D: Degraded Tier Forced Deadline (Heavy Query 방어)

#### 문제

`non_essential` 트래픽이 5%/2%로 허용되더라도, 허용된 요청이 **Heavy Query** (예: 대시보드 full-scan)를 실행하면
제한된 DB 커넥션과 CPU를 장시간 점유하여 `critical`/`standard` tier에 2차 피해를 유발합니다.

#### 선택 근거: DeadlineContext 활용

기존 인프라 비교:

| 방식 | 장점 | 단점 | 선택 |
|------|------|------|------|
| `DeadlineContext.set_deadline()` | ContextVar 기반, DB `statement_timeout` 자동 전파 | 없음 | **✓** |
| Django signals + middleware timeout | 외부 라이브러리 필요 | 불안정, DB 쿼리 중단 불가 | ✗ |
| Gunicorn `--timeout` 헤더 | 프로세스 레벨 | 요청별 제어 불가 | ✗ |

`DeadlineContext`는 이미 `admission_control.py`의 0단계에서 사용 중이며,
`adapters/postgres/repository.py`의 `timeout_context()`가 `get_deadline_aware_statement_timeout()`을
호출하여 DB `SET statement_timeout`을 자동 조정합니다. 추가 의존성 없이 **E2E 타임아웃**이 보장됩니다.

#### 구현

**파일**: `api/django/admission_control.py` — `_process_request()` 내부, tier 분류(2단계) 직후

```python
# admission_control.py L181 이후 (tier 분류 완료 직후)

# 3단계(신규): Degraded tier에 강제 Deadline 주입
_DEGRADED_TIER_DEADLINE_MS = 1000  # 1초 상한

if tier_id == "non_essential":
    from selfhealing.scaling.deadline_context import (
        get_remaining_ms,
        set_deadline,
    )
    # 현재 backpressure 레벨 확인
    bp_level = self._traffic_gate.get_current_level()
    if bp_level >= BackpressureLevel.HIGH:
        remaining = get_remaining_ms()
        # 기존 deadline이 없거나, 기존 deadline > 강제 deadline이면 덮어쓰기
        if remaining is None or remaining > _DEGRADED_TIER_DEADLINE_MS:
            set_deadline(_DEGRADED_TIER_DEADLINE_MS)
            logger.info(
                "[AdmissionControlMiddleware] Forced deadline: "
                "tier=%s, bp_level=%s, deadline_ms=%d",
                tier_id, bp_level.name, _DEGRADED_TIER_DEADLINE_MS,
            )
```

#### 전파 경로

```
AdmissionControlMiddleware
  └─ set_deadline(1000ms)
       ├─ ContextVar(_request_deadline) 설정
       ├─ Django View 실행
       │    └─ repository.timeout_context()
       │         └─ get_deadline_aware_statement_timeout()
       │              └─ SET statement_timeout = '950ms'  (50ms 네트워크 버퍼 차감)
       └─ 1000ms 초과 시 is_expired() → True → Fast-Fail
```

#### 설정 상수

| 상수 | 값 | 근거 |
|------|----|------|
| `_DEGRADED_TIER_DEADLINE_MS` | 1000 | 대시보드 API p99 < 500ms, 2× 마진 |
| `DEFAULT_NETWORK_LATENCY_BUFFER_MS` | 50 | 기존 값 유지 (Cross-AZ 2× 마진) |
| 적용 조건 | `bp_level >= HIGH` | HIGH 미만은 기존 watermark만으로 충분 |

---

## 4. Prometheus 메트릭

### 4.1 Per-Tier Dropped Counter

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

### 4.2 Per-Tier Processed Counter (신규)

기존 `_processed_count`는 단일 int이므로 tier별 처리량을 알 수 없습니다.
Alert Rule의 분모(전체 요청량)를 tier별로 구하려면 **processed 카운터도 tier 분할**이 필요합니다.

#### 메트릭 정의

```python
# scaling/metrics.py에 추가 (dropped_by_tier_total과 동일 패턴)

# Counter: Tier별 처리 항목 (Starvation Alert 분모용)
self.processed_by_tier_total = Counter(
    f"{self._prefix}rate_controller_processed_total",
    "Total processed items per tier for starvation monitoring",
    ["tier"],
)
```

#### Helper 메서드

```python
def inc_processed_by_tier(self, tier: str) -> None:
    """Tier별 처리 카운터 증가 (Starvation Alert 분모용)."""
    if HAS_PROMETHEUS and self._settings.metrics_enabled:
        self.processed_by_tier_total.labels(tier=tier).inc()
```

#### RateController 코드 변경

```python
# rate_controller.py — __init__에 추가
self._processed_by_tier: dict[str, int] = {
    "critical": 0,
    "standard": 0,
    "non_essential": 0,
}

# should_process() — 허용 경로에서 tier별 카운팅
if self._token_bucket.consume():
    with self._lock:
        self._processed_count += 1
        self._processed_by_tier[priority] = self._processed_by_tier.get(priority, 0) + 1
    return True
```

#### 카디널리티 영향

| 메트릭 | 레이블 | 카디널리티 |
|--------|--------|------------|
| `dropped_by_tier_total` | `tier` (3값) | 3 |
| `processed_by_tier_total` (신규) | `tier` (3값) | **3** |
| 합계 추가분 | | **+3 시계열** |

기존 `dropped_by_tier_total`과 동일한 `["tier"]` 레이블만 사용하므로 카디널리티 폭발 위험 없음.

### 4.3 Starvation 감지 Alerting Rule

```yaml
# Starvation Guard Alert
- alert: TierStarvation
  expr: |
    (
      rate(selfhealing_rate_controller_dropped_total{tier="non_essential"}[5m])
      /
      (rate(selfhealing_rate_controller_dropped_total{tier="non_essential"}[5m])
       + rate(selfhealing_rate_controller_processed_total{tier="non_essential"}[5m]))
      > 0.99
    )
    and
    (
      # 최소 트래픽 볼륨 보호: 샘플 부족 시 false-positive 방지
      (rate(selfhealing_rate_controller_dropped_total{tier="non_essential"}[5m])
       + rate(selfhealing_rate_controller_processed_total{tier="non_essential"}[5m]))
      > 10
    )
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "non_essential tier 99% 이상 거부 — starvation 의심"
    description: |
      5분간 non_essential 총 요청 > 10건 중 99% 이상이 거부됨.
      processed_by_tier_total이 0에 가까우면 완전 starvation 상태.
```

#### 최소 볼륨 조건 (`> 10`) 근거

| 조건 | 효과 |
|------|------|
| 볼륨 조건 없음 | 야간/유지보수 시 0/0 → NaN 또는 1/1 = 100% → **false-positive** |
| `> 10` (5분간) | 분당 2건 이상 → 실제 트래픽이 있는 상태에서만 Alert 발화 |
| `> 100` | 저트래픽 환경에서 starvation 감지 불가 위험 |

---

## 5. 선택적 확장: 시간 기반 Watermark 완화

### 5.1 문제

`non_essential`이 연속 N분 이상 100% 거부되면, 해당 tier의 watermark를 일시적으로 완화하여 최소한의 트래픽을 허용합니다.

### 5.2 안전 전제조건: RecoveryGate 연동

Relief가 watermark를 완화하면 **시스템이 아직 과부하인 상태에서 트래픽이 증가**할 위험이 있습니다.
이를 방지하기 위해 기존 `RecoveryGate.check_recovery_allowed()`를 **필수 전제조건**으로 삽입합니다.

#### 기존 RecoveryGate 사양

**파일**: `services/emergency_mode/recovery_gate.py`

```python
def check_recovery_allowed(self) -> tuple[bool, str]:
    metrics = self._metrics_checker()
    cpu = metrics.get("cpu_percent", 0.0)
    error_rate = metrics.get("error_rate", 0.0)

    if cpu > self.config.cpu_threshold_percent:      # 기본 80%
        return (False, f"CPU usage too high: {cpu:.1f}%")
    if error_rate > self.config.error_rate_threshold: # 기본 5%
        return (False, f"Error rate too high: {error_rate:.3f}")

    return (True, "All metrics within thresholds")
```

RecoveryGateConfig 기본값: `cpu_threshold_percent=80.0`, `error_rate_threshold=0.05`

#### 연동 이유

| 비교 | 별도 구현 | RecoveryGate 재사용 |
|------|-----------|--------------------|
| 판단 기준 | CPU + error_rate 직접 구현 | 이미 검증된 동일 기준 |
| 임계값 일관성 | 별도 상수 관리 → Drift 위험 | 단일 `RecoveryGateConfig` |
| 테스트 커버리지 | 신규 작성 필요 | 기존 테스트 활용 |
| **선택** | | **✓ 재사용** |

### 5.3 구현 스케치

```python
# rate_controller.py에 추가 (~20줄)

from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

# 연속 거부 시간 추적
_tier_last_allowed: dict[str, float]  # tier → 마지막 허용 시각 (monotonic)
_STARVATION_RELIEF_SECONDS = 300.0    # 5분 연속 거부 시 완화
_STARVATION_RELIEF_WATERMARK = 0.3    # 완화 시 watermark (standard와 동일)
_recovery_gate = RecoveryGate()       # 싱글톤 또는 DI로 주입

def _check_relief_allowed(self) -> bool:
    """Starvation Relief 활성화 전 시스템 안정성 확인.

    RecoveryGate와 동일한 기준(CPU < 80%, error_rate < 5%)을 사용하여
    과부하 상태에서 Relief가 트래픽을 증가시키는 것을 방지한다.
    """
    allowed, reason = self._recovery_gate.check_recovery_allowed()
    if not allowed:
        logger.info(
            "[RateController] Starvation relief blocked: %s", reason
        )
    return allowed

def should_process(self, priority: str = "standard") -> bool:
    # ... 기존 watermark 확인 ...

    # Starvation Relief: N분간 한 번도 허용 안 되었으면 watermark 임시 완화
    if token_ratio < watermark and priority in self._tier_last_allowed:
        elapsed = time.time() - self._tier_last_allowed[priority]
        if elapsed > self._STARVATION_RELIEF_SECONDS:
            # ★ 안전 전제조건: RecoveryGate 통과 시에만 Relief 활성화
            if not self._check_relief_allowed():
                # 시스템 과부하 → Relief 차단, 기존 watermark 유지
                return False

            watermark = min(watermark, _STARVATION_RELIEF_WATERMARK)
            logger.warning(
                "[RateController] Starvation relief: tier=%s, "
                "elapsed=%.0fs, relaxed_watermark=%.2f",
                priority, elapsed, watermark,
            )
```

#### Relief 활성화 판정 흐름

```
tier가 5분간 100% 거부됨
    ↓
_check_relief_allowed() 호출
    ↓
RecoveryGate.check_recovery_allowed()
    ├─ CPU > 80% → Relief 차단 (기존 watermark 유지)
    ├─ error_rate > 5% → Relief 차단
    └─ 둘 다 정상 → Relief 활성화 (watermark 0.6 → 0.3)
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
├── TestDegradedTierDeadline                 # 신규 (리뷰 1)
│   ├── test_forced_deadline_on_high_level   # HIGH + non_essential → 1000ms deadline
│   ├── test_no_deadline_below_high          # MEDIUM + non_essential → deadline 미설정
│   ├── test_existing_shorter_deadline_kept  # 기존 500ms < 1000ms → 기존 유지
│   └── test_db_statement_timeout_propagated # set_deadline → statement_timeout 자동 전파
├── TestPerTierProcessedCounter              # 신규 (리뷰 4)
│   ├── test_processed_by_tier_increments    # 허용 시 tier별 카운터 증가
│   └── test_processed_by_tier_isolation     # critical 허용 시 non_essential 카운터 불변
├── TestStarvationReliefSafety               # 신규 (리뷰 5)
│   ├── test_relief_blocked_on_high_cpu      # CPU > 80% → Relief 차단
│   ├── test_relief_blocked_on_high_error    # error_rate > 5% → Relief 차단
│   └── test_relief_allowed_on_stable_system # CPU < 80% + error_rate < 5% → Relief 허용
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
| Per-tier processed counter (신규) | 무시: dropped counter와 동일 O(1) |
| Degraded tier forced deadline (신규) | 무시: ContextVar 설정 1회 (O(1)) |
| Relief RecoveryGate 체크 (신규) | 무시: 5분 주기, metrics_checker() 1회 호출 |

### 7.2 동작 변경

| 시나리오 | 변경 전 | 변경 후 |
|---------|--------|--------|
| HIGH + non_essential 요청 | 100% 거부 | **95% 거부** (5% 허용) |
| CRITICAL + non_essential 요청 | 100% 거부 | **98% 거부** (2% 허용) |
| LoadShedding + min_traffic 미지정 | 0% 보장 | **5% 보장** |
| HIGH + non_essential + Heavy Query | 무제한 실행 | **1초 Deadline 강제** (신규) |
| Starvation Relief 활성화 | 무조건 완화 | **RecoveryGate 통과 시만 완화** (신규) |

### 7.4 배치/통계 서비스 배포 체크리스트

`min_traffic_percentage` 기본값이 `0.0 → 5.0`으로 변경되므로,
**의도적으로 0%가 필요한 서비스**는 명시적으로 `0.0`을 설정해야 합니다.

#### 대상 서비스 식별 기준

| 서비스 유형 | `min_traffic_percentage` | 설정 이유 |
|-------------|-------------------------|----------|
| API 서비스 (기본) | `5.0` (기본값) | 최소 관측성 보장 |
| 배치/ETL 서비스 | **`0.0` (명시적)** | 장애 시 완전 중단 허용, 재시도 보장됨 |
| 통계 집계 서비스 | **`0.0` (명시적)** | 지연 허용, 과부하 시 자원 반환 우선 |
| 내부 크론 서비스 | **`0.0` (명시적)** | 스케줄 기반 재실행 보장됨 |

#### 코드 예시

```python
# service_config.py — 배치 서비스 등록 시
from selfhealing.services.circuit_breaker.service_config import register_service

register_service(
    service_name="batch-etl-worker",
    min_traffic_percentage=0.0,  # ← 명시적 0.0: 과부하 시 완전 중단 허용
    # ... 기타 설정
)
```

#### 배포 전 확인 항목

- [ ] 기존 `min_traffic_percentage`를 명시적으로 설정하지 않은 서비스 목록 감사
- [ ] 배치/통계/크론 서비스에 `min_traffic_percentage=0.0` 명시적 설정
- [ ] API 서비스는 기본값 `5.0` 유지 확인
- [ ] 변경 후 `LoadSheddingManager.evaluate_shedding()` 동작 확인 테스트 실행

### 7.3 Fail-Open 안전성

모든 변경이 **기존보다 더 많이 허용**하는 방향입니다:
- `0.0 → 0.05`: 더 많은 트래픽 허용
- `0.0 → 5.0`: 최소 보장 트래픽 증가
- Per-tier counter: 읽기 전용 추가 → 기존 동작 변경 없음
