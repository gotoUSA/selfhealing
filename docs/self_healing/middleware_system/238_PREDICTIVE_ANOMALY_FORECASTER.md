# 238. Predictive Anomaly Forecaster — 예측적 이상 탐지 엔진

> **문서 번호**: 238
> **작성일**: 2026-02-18
> **상태**: 설계 확정 (구현 대기)
> **관련 문서**: 36_RUNTIME_FEEDBACK_IMPLEMENTATION.md, 38_AUTO_TUNING_API.md, 12_ERROR_BUDGET.md

---

## 1. 개요

### 1.1 문제 정의

현재 셀프힐링 시스템의 DecisionEngine은 **완전한 반응형(reactive)** 으로, 메트릭이 임계치를 넘은 **후에야** 조정을 수행한다.

```python
# core/decision_engine.py L88-119 — 모든 rule이 현재 스냅샷 비교
DEFAULT_RULES = [
    AdjustmentRule(
        parameter="timeout_ms",
        metric="p99_latency_ms",
        condition=lambda current, metric: metric > current * 0.8,  # 이미 80% 넘은 후
        ...
    ),
    AdjustmentRule(
        parameter="retry_count",
        metric="retry_exhausted_rate",
        condition=lambda current, metric: metric > 0.1,  # 이미 10% 소진된 후
        ...
    ),
    AdjustmentRule(
        parameter="circuit_breaker_threshold",
        metric="error_rate",
        condition=lambda current, metric: metric > current * 0.9,  # 이미 90% 근접 후
        ...
    ),
]
```

빅테크/엔터프라이즈급 시스템에서 이 반응형 구조의 한계:

- `RuntimeFeedbackLoop` 관찰 간격: **60초** (`core/runtime_feedback.py L105`)
- 감지 → 조정 적용 → `POST_ADJUSTMENT_WAIT` → 헬스체크까지 **최소 2분 소요**
- `AutoRollbackGuard` 헬스체크 간격: **30초** (`core/auto_rollback_guard.py L181`)
- 초당 수백만 TPS 환경에서는 2분의 지연이 **수십만 건의 실패 요청**을 발생시킬 수 있음

### 1.2 제안 솔루션

시계열 예측 모델(EWMA/Holt-Winters)로 **5~15분 후 메트릭을 예측**하여 사전 조치:

```
services/
  predictive_forecaster/
    time_series.py       # EWMA/Holt-Winters 경량 예측
    anomaly_detector.py  # Z-Score/IQR 이상 탐지
    proactive_action.py  # 예측 기반 사전 조치 트리거
    service.py           # 통합 서비스
```

### 1.3 빅테크 사례

- **Netflix AIOps**: 자동 이상 탐지 + 예측 기반 스케일링
- **Google Predictive Autoscaling**: Borg/Autopilot의 시계열 예측 기반 리소스 할당
- **AWS DevOps Guru**: ML 기반 운영 이상 탐지

---

## 2. 현재 시스템 분석 (코드 근거)

### 2.1 현재 아키텍처의 반응형 흐름

```
메트릭 수집 (MetricsAdapter.fetch_current_metrics)
     │
     ▼
현재 스냅샷 비교 (DecisionEngine.analyze → _evaluate_rule)
     │  condition=lambda current, metric: metric > threshold
     ▼
안전 한계 검증 (SafetyBounds.is_within_bounds)
     │
     ▼
설정 적용 (ConfigApplier.apply)
     │
     ▼
헬스체크 대기 (POST_ADJUSTMENT_WAIT)
     │
     ▼
메트릭 재수집 → 악화 시 롤백 (_detect_degradation)
```

**근거 코드**: `core/runtime_feedback.py` `observe_and_adjust()` (L232-L270)

### 2.2 기존 예측 컴포넌트 분석

#### 2.2.1 BudgetDepletionForecaster (유일한 예측적 요소)

```python
# services/error_budget/forecaster.py L72-120
class BudgetDepletionForecaster:
    RISK_THRESHOLDS = {
        "critical": 1.0,   # 1시간 이내 소진 예상
        "high": 6.0,       # 6시간 이내 소진 예상
        "medium": 24.0,    # 24시간 이내 소진 예상
    }

    def forecast(self, status: ErrorBudgetStatus) -> DepletionForecast:
        # Burn Rate 기반 선형 외삽
        hourly_depletion = (burn_rate_1h - 1.0) * self.DEFAULT_SLO_ERROR_BUDGET_PERCENT
        estimated_hours = remaining_percent / hourly_depletion
```

- **입력**: `ErrorBudgetStatus.burn_rate_1h`, `burn_rate_6h`
- **수법**: Burn Rate 기반 **선형 외삽** (EWMA/Holt-Winters 아님)
- **소비자**: `AdaptiveThrottle._check_preemptive_protection()` **throttle 전용**

#### 2.2.2 ConnectionPoolMonitor.get_trend() (원시적 트렌드)

```python
# core/pool_monitor.py L356-375
def get_trend(self) -> dict[str, Any]:
    recent = self._stats_history[-10:]          # 최근 10개만
    avg_usage = sum(s.usage_percent for s in recent) / len(recent)

    if len(self._stats_history) > 20:
        older = self._stats_history[-20:-10]    # 이전 10개
        older_avg = sum(s.usage_percent for s in older) / len(older)

        if avg_usage > older_avg + 10:          # 단순 평균 차이 ±10%
            return {"trend": "increasing", ...}
```

- **분석 윈도우**: 최근 10개 vs 이전 10개 = **총 20분**만 분석
- **기울기/가속도**: 없음. "increasing/decreasing/stable" 3가지 문자열만 반환
- **통계적 유의성**: 없음. 단순 평균 차이만 비교

#### 2.2.3 CgroupResourceMonitor (트렌드 없음)

```python
# core/resource_monitor.py L100-148
@classmethod
def get_available_memory_bytes(cls, safety_margin=None) -> int | None:
    max_bytes = cls.get_memory_max_bytes()
    current_bytes = cls.get_memory_current_bytes()
    # 현재 스냅샷만 — 추세 분석 없음
    safe_available = int(available * (1.0 - safety_margin))
```

- **트렌드 분석**: 없음
- **OOM 예측**: 불가능

### 2.3 예측 격차 요약

| 메트릭 | 현재 상태 | 격차 |
|--------|----------|------|
| P99 Latency | 임계치 80% 넘은 후 감지 | 트렌드 기반 사전 예측 없음 |
| Error Rate | CB 임계값 90% 근접 후 감지 | 증가 추세 기반 사전 경고 없음 |
| Pool Usage | 70%/90% 임계치 기반 (10개 평균) | 고갈 시점 예측 없음 |
| Memory | 현재 스냅샷만 | OOM 시점 예측 불가 |
| Error Budget | ✅ Burn Rate 예측 존재 | throttle 전용, 범용 아님 |

---

## 3. 도입 결정 근거

### 3.1 빅테크/엔터프라이즈 기준 도입 필요성

**도입해야 한다.**

1. **시스템 내에 예측 패턴의 성공 선례가 존재**:
   `BudgetDepletionForecaster` → `AdaptiveThrottle._check_preemptive_protection()` → `_apply_preemptive_reduction()` 체인이 이미 프로덕션 수준으로 구현됨 (`services/throttle/adaptive.py L1232-1310`). 이 패턴을 범용화하는 것은 검증된 아키텍처의 확장

2. **0 dependency 원칙 유지 가능**: EWMA + Z-Score/IQR는 표준 라이브러리만으로 구현 가능

3. **기존 안전장치가 예측 오판을 방어**: SafetyBounds → AutoRollbackGuard → GovernanceCheck → ParameterBlacklist 4단계 안전망 존재

### 3.2 현재 시스템(Forecaster 없음)의 장단점

#### 장점
1. **단순성/예측 가능성**: rule이 `condition` 람다 하나로 명확히 정의됨 (`decision_engine.py L88-119`)
2. **0 dependency 유지**: 외부 ML 의존성 없음
3. **4단계 안전망**: SafetyBounds(`core/safety_bounds.py`) → AutoRollbackGuard(`core/auto_rollback_guard.py`) → GovernanceCheck(`services/governance_checks.py`) → ParameterBlacklist(`services/learning/service.py`)
4. **낮은 오탐 리스크**: 현재 메트릭의 실제 값만 판단

#### 단점
1. **임계치 도달 후에야 반응**: 모든 rule이 `metric > threshold` 형태
2. **트렌드 분석 원시적**: `get_trend()`는 10개 평균 비교만 수행
3. **메모리 OOM 예측 불가**: `CgroupResourceMonitor`에 트렌드 없음
4. **신뢰도 계산 정적**: CV만 사용, 시계열 특성 미반영 (`decision_engine.py L266-295`)
5. **커넥션 풀 고갈 사전 경고 없음**: 70% 전까지 `HEALTHY` 상태

### 3.3 Forecaster 도입 시 장단점

#### 장점
1. **사전 조치 시간 5~15분 확보** (현재 60초 간격 관찰 대비)
2. **DecisionEngine 신뢰도 강화**: 예측 트렌드 기울기를 `stability_factor`에 반영
3. **CgroupResourceMonitor 보완**: "30분 후 OOM 예상" 수준의 사전 경고 가능
4. **get_trend() 대체**: 통계적으로 유의미한 시계열 예측으로 업그레이드
5. **BudgetDepletionForecaster 패턴의 일반화**: 검증된 패턴의 범용 확장

#### 단점
1. **오탐(False Positive) 리스크**: 기존 4단계 안전망 + DRY_RUN으로 완화 가능
2. **복잡도 증가**: EWMA(1단계)는 파라미터 1개, 상태 O(1)로 최소
3. **히스토리 데이터 요구**: 별도 ring buffer로 기존 컴포넌트와 분리
4. **BudgetDepletionForecaster와 책임 경계**: §5에서 계층화로 해결

---

## 4. 히스토리 크기 설계

### 4.1 현재 히스토리 크기

| 컴포넌트 | 현재 크기 | 코드 위치 | 커버 시간 (수집 간격 기준) |
|---------|----------|----------|--------------------------|
| `DecisionEngine._history` | 100개 | `core/decision_engine.py L323-324` | 100분 (60초 간격) |
| `PoolMonitor._stats_history` | 100개 (le=1000) | `settings/pool_monitor.py L70-75` | 100분 |
| `AutoRollbackGuard._health_history` | 100개 | `core/auto_rollback_guard.py L274-276` | 50분 (30초 간격) |
| `ThrottleLimitHistoryCollector` | 1,000개 | `services/throttle/postmortem.py L71-82` | ~16시간 |
| `drift_reconciliation._max_history` | 1,000개 | `adapters/memory/drift_reconciliation.py L94` | — |

### 4.2 필요 크기 계산

Holt-Winters 계절성 분석에 최소 2~3 시즌(일간 패턴 기준 2~3일) 필요:

| 수집 간격 | 24h × 3일 = 72시간 | 필요 개수 |
|----------|-------------------|----------|
| 60초 (DecisionEngine) | 72 × 60 = 4,320 | **4,320개** |
| 30초 (AutoRollbackGuard) | 72 × 120 = 8,640 | **8,640개** |

### 4.3 메모리 영향 분석

시스템 내 기존 대용량 버퍼와의 비교:

```python
# settings/scale.py L44-72 — 프로파일별 ring_buffer_capacity
PROFILE_DEFAULTS = {
    ScaleProfile.DEVELOPMENT:    {"ring_buffer_capacity": 10_000},
    ScaleProfile.SMALL_BUSINESS: {"ring_buffer_capacity": 100_000},
    ScaleProfile.ENTERPRISE:     {"ring_buffer_capacity": 1_000_000},
    ScaleProfile.HIGH_THROUGHPUT: {"ring_buffer_capacity": 5_000_000},
}
# le=10_000_000까지 허용 (settings/scale.py L129)
```

히스토리 데이터 구조별 메모리:

| 데이터클래스 | 필드 수 | 추정 크기/개 | 10,000개 메모리 |
|------------|--------|------------|---------------|
| `PoolStats` | str + int×3 + datetime | ~200 bytes | ~2 MB |
| `RollbackHealthAssessment` | bool + enum + float×3 + datetime + dict | ~300 bytes | ~3 MB |
| DecisionEngine 히스토리 (dict) | dict(timestamp + metrics + decisions) | ~500 bytes | ~5 MB |

**결론**: 10,000개 히스토리는 **최대 ~5MB** — enterprise `ring_buffer_capacity` 1,000,000개 대비 무시할 수 있는 수준

### 4.4 확정 설계: 히스토리 크기

개발 단계이므로 필요 수치를 그대로 수용. 프로덕션 배포 시 환경변수로 조정.

| 컴포넌트 | 현재 | 변경 후 (기본값) | 상한(le) | 근거 |
|---------|------|----------------|---------|------|
| `PoolMonitorSettings.max_history` | 100 (le=1000) | **5,000** | **10,000** | 72시간 커버 (60초 간격 = 4,320) + 여유 |
| `DecisionEngine._history` | 100 (하드코딩) | **5,000** (Settings 전환) | **10,000** | 동일 |
| `AutoRollbackGuard._health_history` | 100 (하드코딩) | **10,000** (Settings 전환) | **10,000** | 72시간 커버 (30초 간격 = 8,640) + 여유 |
| `get_trend()` 분석 윈도우 | 10개 vs 10개 | **100개 vs 100개** | — | 통계적 유의성 확보 |
| Predictive Forecaster (신규) | — | **10,000** | **10,000** | 독립 ring buffer, 기존 히스토리와 분리 |

환경변수 오버라이드:
- `SELFHEALING_POOL_MONITOR_MAX_HISTORY=5000`
- `SELFHEALING_DECISION_MAX_HISTORY=5000` (신규)
- `SELFHEALING_ROLLBACK_MAX_HISTORY=10000` (신규)

---

## 5. 책임 경계 설계 (BudgetDepletionForecaster vs Predictive Forecaster)

### 5.1 결정: 통일하지 않고 계층화

```
┌──────────────────────────────────────────────────────────────────┐
│           Predictive Forecaster (범용 시계열 예측 인프라)          │
│                                                                  │
│   time_series.py: EWMA/Holt-Winters 경량 예측 엔진              │
│   anomaly_detector.py: Z-Score/IQR 이상 탐지                    │
│                                                                  │
│   소비자:                                                        │
│   ├── DecisionEngine._calculate_confidence() → 예측 컨텍스트      │
│   ├── ConnectionPoolMonitor.get_trend() → 고갈 시점 예측         │
│   └── CgroupResourceMonitor → OOM 시점 예측                     │
└──────────────────────────────────────────────────────────────────┘
                         │
                         │ (내부적으로 EWMA 엔진 활용 가능)
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│       BudgetDepletionForecaster (에러 예산 도메인 특화)            │
│                                                                  │
│   forecaster.py: Burn Rate 기반 예산 소진 예측                   │
│   - 입력: ErrorBudgetStatus.burn_rate_1h/6h                     │
│   - 출력: DepletionForecast (risk_level + recommended_action)    │
│   - 소비자: AdaptiveThrottle._check_preemptive_protection()      │
│                                                                  │
│   고유 책임: 에러 예산 도메인 로직 + 위험 수준 + 권장 조치         │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 근거

`BudgetDepletionForecaster`는 **에러 예산 고유의 비즈니스 로직**을 포함:

```python
# services/error_budget/forecaster.py L62-68
RISK_THRESHOLDS = {
    "critical": 1.0,   # 1시간 이내 소진 → 즉시 Throttle 강화
    "high": 6.0,       # 6시간 이내 소진 → WARNING 레벨 조정
    "medium": 24.0,    # 24시간 이내 소진 → 모니터링 강화
}
ACCELERATION_THRESHOLD = 1.2  # 1h burn rate > 6h × 1.2 시 가속 판정
```

이 도메인 로직(risk_level 임계치, 가속 판단, recommended_action)은 범용 시계열 예측과 **관심사가 다르므로** 통일하면 안 됨.

개선점: `BudgetDepletionForecaster.forecast()`의 **선형 외삽 부분만** Predictive Forecaster의 EWMA로 대체하여 예측 정밀도 향상:

```python
# 현재: 선형 외삽
hourly_depletion = (burn_rate_1h - 1.0) * self.DEFAULT_SLO_ERROR_BUDGET_PERCENT

# 개선: Predictive Forecaster의 EWMA 활용
from selfhealing.services.predictive_forecaster.time_series import EWMAForecaster
forecaster = EWMAForecaster(alpha=0.3)
predicted_burn_rate = forecaster.predict(burn_rate_history, steps_ahead=6)
```

---

## 6. 오판 대비 안전장치 설계

### 6.1 기존 4단계 안전망 (코드 근거)

예측 시스템이 오판하더라도, 기존 안전장치 체인이 **결과를 방어**:

**1단계 — SafetyBounds (절대 범위 + 변경폭 제한)**

```python
# core/safety_bounds.py L168-188
if new_value < bound.min_value:               # 절대 최소값 이하 거부
    return False
if new_value > bound.max_value:               # 절대 최대값 초과 거부
    return False
if change_ratio > bound.max_change_per_cycle: # 변경폭 30% 초과 거부
    return False
```

예측 결과가 아무리 극단적이어도 `max_change_per_cycle`(기본 30%)을 넘는 조정은 **구조적으로 불가능**.

**2단계 — RuntimeFeedbackLoop._detect_degradation() (조정 후 헬스체크)**

```python
# core/runtime_feedback.py L370-395
if error_increase > 0.2:     # 에러율 20% 이상 증가 → 자동 롤백
    return True
if latency_increase > 0.5:   # 레이턴시 50% 이상 증가 → 자동 롤백
    return True
```

**3단계 — AutoRollbackGuard (독립 안전장치)**

RuntimeFeedbackLoop과 **독립 스레드**로 작동:

```python
# core/auto_rollback_guard.py L282-295
def _assess_degradation(self, error_rate, latency_p99):
    if error_rate >= self.ERROR_RATE_CRITICAL:     # 30%+ → CRITICAL
        return RollbackSeverity.CRITICAL
    if error_rate >= self.ERROR_RATE_MAJOR:        # 10%+ → MAJOR
        return RollbackSeverity.MAJOR
```

CRITICAL 감지 시 `_execute_emergency_recovery()` → 3단계 우선순위 복구:
1. Last Known Good (직전 스냅샷)
2. DNA Declared (DNA 선언값)
3. System Defaults (하드코딩 기본값)

(`core/auto_rollback_guard.py L392-440`)

**4단계 — GovernanceCheck (Kill Switch)**

```python
# services/auto_tuning/service.py L146-163
def _check_governance_before_adjustment(self, module, adjustment_type):
    return check_all_governance(
        check_kill_switch=True,
        check_emergency=True,
        check_error_budget=True,
    )
```

### 6.2 Predictive Forecaster 전용 추가 안전장치

기존 4단계 + 예측 시스템 고유의 3가지 추가 방어:

**A. 예측 신뢰도 임계치 (기존 인프라 활용)**

```python
# core/decision_engine.py L253-257
if confidence < rule.min_confidence:
    logger.debug(f"Low confidence ({confidence:.2f}) for {rule.parameter}")
    return None  # 신뢰도 미달 시 조정 자체가 제안되지 않음
```

예측 결과의 confidence를 이 기존 필터에 주입 → 신뢰도 미달 예측은 **자동 무시**.

**B. DRY_RUN 사전 검증 (기존 인프라 활용)**

```python
# services/auto_tuning/service.py L35
class TuningMode(str, Enum):
    DRY_RUN = "dry_run"  # 조정 시뮬레이션만
```

예측 시스템 최초 도입 시 `DRY_RUN` 모드로 운영 → 예측 정확도를 측정한 후 `AUTOMATIC`으로 전환.

**C. ParameterBlacklist 피드백 루프 (기존 인프라 활용)**

```python
# services/learning/service.py L36-100
class ParameterBlacklist:
    def register(self, module, parameter, blocked_values, reason, ...):
        # 반복 오판 파라미터 조합을 블랙리스트 등록
```

예측이 반복적으로 오판하는 파라미터 조합 → LearningService가 블랙리스트에 등록 → 자동 차단.

### 6.3 오판 대응 흐름

```
예측 결과 생성
     │
     ▼
[신뢰도 필터] confidence < min_confidence → 버림
     │
     ▼
[SafetyBounds] 범위 초과 or 변경폭 30%+ → 거부
     │
     ▼
[GovernanceCheck] Kill Switch / Emergency → 차단
     │
     ▼
[DRY_RUN 모드] 시뮬레이션만 → 실제 적용 안 함 (도입 초기)
     │
     ▼
설정 적용 (AUTOMATIC 모드)
     │
     ▼
[RuntimeFeedback] 에러율↑20% or 레이턴시↑50% → 자동 롤백
     │
     ▼
[AutoRollbackGuard] 독립 헬스체크 → 긴급 복구
     │
     ▼
[ParameterBlacklist] 반복 오판 패턴 → 블랙리스트 등록
```

---

## 7. 데이터 확보 전략

### 7.1 기업 프로덕션 데이터 (배포 후)

`MetricsAdapter` 프로토콜이 인터페이스-구현 분리:

```python
# core/runtime_feedback.py L52-55
class MetricsAdapter(Protocol):
    def fetch_current_metrics(self) -> dict[str, float]:
        ...
```

구현체 3종:
- `PrometheusMetricsAdapter` — 기업 Prometheus에서 수집
- `InternalMetricsAdapter` — 내부 메트릭 수집
- `MockMetricsAdapter` — 테스트용

(`adapters/metrics/auto_tuning_adapter.py`)

기업의 기존 Prometheus/Datadog 시계열 데이터를 `PrometheusMetricsAdapter`로 직접 주입 가능. EWMA는 **20~30개 데이터포인트(20~30분)면 안정화**되므로 cold-start 문제 최소.

### 7.2 개발 단계 데이터 확보 (3가지 경로)

#### 경로 1: MockMetricsAdapter 확장 — Synthetic Time Series Generator

기존 시나리오 시뮬레이션:

```python
# adapters/metrics/auto_tuning_adapter.py L271-280
class MockMetricsAdapter:
    def simulate_degradation(self, level: str = "minor"):
        if level == "minor":
            self.metrics["error_rate"] = 0.06
            self.metrics["p99_latency_ms"] = 3500
        elif level == "major":
            self.metrics["error_rate"] = 0.15
            self.metrics["p99_latency_ms"] = 6000
```

확장 방향:

```python
class TimeSeriesScenarioGenerator:
    """시나리오 기반 시계열 생성기 (MockMetricsAdapter 확장)"""

    @staticmethod
    def gradual_degradation(base_latency=150, target=5000, steps=100):
        """점진적 악화: latency가 100스텝에 걸쳐 150→5000ms"""

    @staticmethod
    def spike_and_recovery(spike_at=50, recover_at=70, steps=100):
        """스파이크 후 복구 시나리오"""

    @staticmethod
    def seasonal_pattern(period=1440, amplitude=0.3, steps=4320):
        """일간 계절성 패턴 (Holt-Winters 검증용)"""

    @staticmethod
    def pool_exhaustion(initial_usage=30, growth_rate=0.5, steps=200):
        """커넥션 풀 점진적 고갈 시나리오"""

    @staticmethod
    def memory_leak(initial_mb=500, leak_rate_mb=2, max_mb=1024, steps=300):
        """메모리 누수 시나리오 (OOM 예측 검증)"""
```

#### 경로 2: Locust 부하 테스트 프레임워크

기존 extreme stress test에서 실제 메트릭 생성:

```python
# load_tests/scenarios/load/stage0_selfhealing_extreme.py
EXTREME_TEST_CONFIG = {
    "cb_services": ["payment-service", "order-service", ...],
    "cb_failure_rate": 1.0,       # 100% failure
    "chaos_targets": ["payment", "inventory", "cache"],
    "error_budget_error_count": 1000,
}
```

이 테스트를 docker-compose 테스트베드에서 실행 → Prometheus에 실제 시계열 축적 → Predictive Forecaster에 replay.

#### 경로 3: Chaos 실험 프레임워크

```
services/chaos/experiments/
  ├── latency.py     — LatencyInjectionExperiment (인위적 레이턴시 주입)
  ├── http_errors.py — Error5xxInjection (인위적 에러 주입)
  └── infrastructure.py — SimulatedDiskIO (인프라 장애 시뮬레이션)
```

Chaos 실험으로 **실제 서비스에 인위적 장애 주입** → 생성되는 메트릭 시계열을 검증 데이터로 활용.

#### 데이터 확보 순서

```
Phase 1 (단위 테스트): Synthetic Time Series Generator
     │  시나리오: 점진적 악화, 스파이크, 계절성, 풀 고갈, 메모리 누수
     │  목적: EWMA/Z-Score 알고리즘 정확도 검증
     ▼
Phase 2 (통합 테스트): Locust + Chaos
     │  시나리오: stage0_selfhealing_extreme + chaos injection
     │  목적: 실제 서비스 환경에서의 예측 정확도 검증
     ▼
Phase 3 (배포 후): PrometheusMetricsAdapter
     │  실제 프로덕션 메트릭으로 예측 정밀도 지속 개선
     ▼
Phase 4 (안정화): DRY_RUN → AUTOMATIC 전환
```

---

## 8. 구현 로드맵

### Phase 1: EWMA 기반 단기 예측 (5분)

```
services/predictive_forecaster/
    __init__.py
    time_series.py       # EWMAForecaster
    anomaly_detector.py  # ZScoreDetector, IQRDetector
    proactive_action.py  # ProactiveActionTrigger
    service.py           # PredictiveForecasterService
```

#### time_series.py 핵심 설계

```python
class EWMAForecaster:
    """
    Exponentially Weighted Moving Average 예측기.

    외부 의존성 없이 표준 라이브러리만으로 구현.
    파라미터 1개(α), 상태 O(1), 메모리 불변.

    Args:
        alpha: 평활 계수 (0 < α ≤ 1). 클수록 최근 데이터에 가중.
            - 0.1: 장기 트렌드 (느리게 반응)
            - 0.3: 범용 (기본값)
            - 0.5+: 단기 변동에 민감
    """

    def __init__(self, alpha: float = 0.3):
        self._alpha = alpha
        self._ewma: float | None = None

    def update(self, value: float) -> float:
        """새 데이터포인트 추가 & 현재 EWMA 반환"""
        if self._ewma is None:
            self._ewma = value
        else:
            self._ewma = self._alpha * value + (1 - self._alpha) * self._ewma
        return self._ewma

    def predict(self, steps_ahead: int = 5) -> float | None:
        """N스텝 후 예측값 반환"""
        # 단순 EWMA는 수평 예측 (트렌드 없음)
        # 트렌드 반영은 Phase 2의 Holt-Winters에서
        return self._ewma

    def get_trend_slope(self, history: list[float], window: int = 20) -> float:
        """최근 window 내 기울기 (선형 회귀 근사)"""
        if len(history) < 2:
            return 0.0
        n = min(window, len(history))
        recent = history[-n:]
        # 최소자승법: slope = Σ((xi - x̄)(yi - ȳ)) / Σ((xi - x̄)²)
        x_mean = (n - 1) / 2
        y_mean = sum(recent) / n
        numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(recent))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        return numerator / denominator if denominator > 0 else 0.0
```

#### anomaly_detector.py 핵심 설계

```python
class ZScoreDetector:
    """
    Z-Score 기반 이상 탐지.

    Args:
        threshold: Z-Score 임계값 (기본 3.0 = 99.7% 신뢰구간)
        window: 이동 윈도우 크기
    """

    def __init__(self, threshold: float = 3.0, window: int = 100):
        self._threshold = threshold
        self._window = window
        self._values: collections.deque = collections.deque(maxlen=window)

    def is_anomaly(self, value: float) -> tuple[bool, float]:
        """(is_anomaly, z_score) 반환"""

class IQRDetector:
    """
    IQR(사분위수 범위) 기반 이상 탐지.

    Z-Score보다 이상치에 강건(robust)한 방법.
    정규분포 가정이 불필요.

    Args:
        multiplier: IQR 배수 (기본 1.5 = 일반적 이상치, 3.0 = 극단적 이상치)
    """
```

### Phase 2: Holt-Winters 계절성 분석

충분한 히스토리(2~3 시즌) 축적 후 도입:

```python
class HoltWintersForecaster:
    """
    Holt-Winters 삼중지수평활 (Triple Exponential Smoothing).

    계절성 패턴 감지 및 예측.

    Args:
        alpha: 레벨 평활 (0.1~0.3)
        beta: 트렌드 평활 (0.01~0.1)
        gamma: 계절성 평활 (0.1~0.3)
        season_length: 시즌 길이 (일간 패턴 = 1440 if 60초 간격)
    """
```

### Phase 3: 기존 시스템 연동

| 연동 대상 | 연동 방식 | 코드 위치 |
|----------|----------|----------|
| DecisionEngine | `_calculate_confidence()`에 예측 트렌드 기울기 주입 | `core/decision_engine.py L266-295` |
| PoolMonitor | `get_trend()` 내부에서 EWMA 예측 활용 | `core/pool_monitor.py L356-375` |
| CgroupResourceMonitor | 메모리 사용량 추세의 EWMA 예측 추가 | `core/resource_monitor.py` |
| BudgetDepletionForecaster | 선형 외삽을 EWMA로 대체 (옵션) | `services/error_budget/forecaster.py L108-113` |

---

## 9. 구현 체크리스트

### Phase 1: 핵심 엔진 (EWMA + Z-Score/IQR)
- [ ] `services/predictive_forecaster/__init__.py`
- [ ] `services/predictive_forecaster/time_series.py` — EWMAForecaster
- [ ] `services/predictive_forecaster/anomaly_detector.py` — ZScoreDetector, IQRDetector
- [ ] `services/predictive_forecaster/proactive_action.py` — ProactiveActionTrigger
- [ ] `services/predictive_forecaster/service.py` — PredictiveForecasterService
- [ ] Settings: `settings/predictive_forecaster.py` — Pydantic v2 기반
- [ ] Synthetic Time Series Generator (테스트 데이터 생성기)
- [ ] 단위 테스트: EWMA 수렴 검증, Z-Score 이상 탐지 정확도
- [ ] DRY_RUN 모드 통합 테스트

### Phase 2: 히스토리 크기 확장
- [ ] `PoolMonitorSettings.max_history` 기본값 5,000 / 상한 10,000
- [ ] `DecisionEngine._history` Settings 전환 + 기본값 5,000
- [ ] `AutoRollbackGuard._health_history` Settings 전환 + 기본값 10,000
- [ ] `get_trend()` 분석 윈도우 100개로 확대
- [ ] 각 Settings 환경변수 문서화

### Phase 3: 기존 시스템 연동
- [ ] DecisionEngine.`_calculate_confidence()` 예측 컨텍스트 주입
- [ ] PoolMonitor.`get_trend()` EWMA 기반 예측 대체
- [ ] CgroupResourceMonitor OOM 예측 추가
- [ ] BudgetDepletionForecaster 선형 외삽 → EWMA 옵션

### Phase 4: Holt-Winters (2단계)
- [ ] `time_series.py`에 HoltWintersForecaster 추가
- [ ] 계절성 자동 감지 (auto-detect season_length)
- [ ] 프로덕션 메트릭 기반 검증

---

## 10. 설정 인터페이스

```python
# settings/predictive_forecaster.py (신규)

class PredictiveForecasterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_FORECASTER_",
    )

    # EWMA
    ewma_alpha: float = Field(default=0.3, ge=0.01, le=1.0)

    # Z-Score
    zscore_threshold: float = Field(default=3.0, ge=1.0, le=5.0)
    zscore_window: int = Field(default=100, ge=10, le=10000)

    # IQR
    iqr_multiplier: float = Field(default=1.5, ge=1.0, le=5.0)

    # History
    max_history: int = Field(default=10000, ge=100, le=10000)

    # Prediction
    prediction_steps: int = Field(default=5, ge=1, le=30)

    # Proactive Action
    min_confidence_for_action: float = Field(default=0.7, ge=0.0, le=1.0)
    dry_run: bool = Field(default=True)  # 도입 초기 기본값: DRY_RUN
```

환경변수:
```
SELFHEALING_FORECASTER_EWMA_ALPHA=0.3
SELFHEALING_FORECASTER_ZSCORE_THRESHOLD=3.0
SELFHEALING_FORECASTER_DRY_RUN=true
SELFHEALING_FORECASTER_MAX_HISTORY=10000
```

---

## 부록 A. 0 Dependency 원칙 준수 확인

모든 구현이 Python 표준 라이브러리만 사용:

| 기능 | 사용 모듈 | 외부 의존성 |
|------|----------|----------|
| EWMA | — (산술 연산) | 없음 |
| Holt-Winters | — (산술 연산) | 없음 |
| Z-Score | `math.sqrt` | 없음 |
| IQR | `sorted()` (내장) | 없음 |
| 선형 회귀 근사 | — (최소자승법 직접 구현) | 없음 |
| Ring Buffer | `collections.deque` | 없음 |
| 시계열 저장 | `list` / `deque` | 없음 |

## 부록 B. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `core/decision_engine.py` | DecisionEngine 반응형 규칙, 신뢰도 계산 |
| `core/runtime_feedback.py` | RuntimeFeedbackLoop 관찰-조정 루프 |
| `core/pool_monitor.py` | ConnectionPoolMonitor 트렌드 분석 |
| `core/resource_monitor.py` | CgroupResourceMonitor 메모리 조회 |
| `core/safety_bounds.py` | SafetyBounds 범위/변경폭 제한 |
| `core/auto_rollback_guard.py` | AutoRollbackGuard 독립 안전장치 |
| `services/error_budget/forecaster.py` | BudgetDepletionForecaster 예측 |
| `services/throttle/adaptive.py` | AdaptiveThrottle 선제적 보호 |
| `services/auto_tuning/service.py` | AutoTuningService DRY_RUN/Governance |
| `services/learning/service.py` | ParameterBlacklist 반복 오판 차단 |
| `adapters/metrics/auto_tuning_adapter.py` | MockMetricsAdapter 시뮬레이션 |
| `settings/scale.py` | ScaleSettings ring_buffer_capacity |
| `settings/pool_monitor.py` | PoolMonitorSettings max_history |
| `load_tests/scenarios/load/stage0_selfhealing_extreme.py` | Locust extreme 테스트 |
