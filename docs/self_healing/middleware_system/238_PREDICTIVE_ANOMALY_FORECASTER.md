# 238. Predictive Anomaly Forecaster — 예측적 이상 탐지 엔진

> **문서 번호**: 238
> **작성일**: 2026-02-18
> **최종 수정일**: 2026-02-19 (리뷰 반영 v2)
> **상태**: 설계 확정 (구현 대기)
> **관련 문서**: 36_RUNTIME_FEEDBACK_IMPLEMENTATION.md, 38_AUTO_TUNING_API.md, 12_ERROR_BUDGET.md

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| v1 | 2026-02-18 | 초안 작성 |
| v2 | 2026-02-19 | 리뷰 8건 반영: (1) sensitivity_multiplier 주입, (2) Phase 1 기본 예측기 EWMA→HoltLinear 승격, (3) SpikeClassifier/SpikeType 추가, (4) StateBackend 기반 Cold Start 영속성, (5) warmup_samples Cold Start 보호, (6) has_adjustment Self-Fulfilling Prophecy 태깅, (7) LearningService 연동 설계, (8) 설정 인터페이스 확장 |

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

시계열 예측 모델(Holt Linear/Holt-Winters)로 **5~15분 후 메트릭을 예측**하여 사전 조치:

```
services/
  predictive_forecaster/
    time_series.py       # HoltLinearForecaster (Phase 1) / HoltWintersForecaster (Phase 4)
    anomaly_detector.py  # ZScoreDetector, IQRDetector
    proactive_action.py  # ProactiveActionTrigger, SpikeClassifier
    service.py           # PredictiveForecasterService
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
│   time_series.py:                                                │
│     - HoltLinearForecaster (Phase 1, 트렌드 예측)               │
│     - EWMAForecaster (smoothing 유틸리티)                        │
│     - save_state() / load_state() (StateBackend 영속성)          │
│   anomaly_detector.py: ZScoreDetector, IQRDetector               │
│   proactive_action.py:                                           │
│     - ProactiveActionTrigger                                     │
│     - SpikeClassifier + SpikeType (Flash Sale vs DDoS 분류)      │
│                                                                  │
│   소비자:                                                        │
│   ├── DecisionEngine._calculate_confidence() → 예측 컨텍스트      │
│   ├── ConnectionPoolMonitor.get_trend() → 고갈 시점 예측         │
│   ├── CgroupResourceMonitor → OOM 시점 예측                     │
│   └── LearningService → 예측 정확도 학습 + 블랙리스트 연동        │
└──────────────────────────────────────────────────────────────────┘
                         │
                         │ (내부적으로 EWMAForecaster smoothing 활용 가능)
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

개선점: `BudgetDepletionForecaster.forecast()`의 **선형 외삽 부분만** Predictive Forecaster의 EWMAForecaster로 대체하여 예측 정밀도 향상:

```python
# 현재: 선형 외삽
hourly_depletion = (burn_rate_1h - 1.0) * self.DEFAULT_SLO_ERROR_BUDGET_PERCENT

# 개선: Predictive Forecaster의 EWMAForecaster smoothing 활용
from selfhealing.services.predictive_forecaster.time_series import EWMAForecaster
smoother = EWMAForecaster(alpha=0.3)
smoothed_burn_rate = smoother.update(burn_rate_1h)
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

### Phase 1: Holt Linear 기반 트렌드 예측 + SpikeClassifier

> **v2 변경**: 기본 예측기를 EWMA→HoltLinearForecaster로 승격.
> EWMA는 수평 예측(flat forecast)만 가능하여 트렌드를 포착하지 못함.
> Holt's Linear(이중지수평활)은 레벨 + 트렌드를 분리 추적하여
> 5~15분 후 메트릭 *방향성*을 예측할 수 있음. 파라미터 2개(α, β), 상태 O(1), 0 dependency 유지.

```
services/predictive_forecaster/
    __init__.py
    time_series.py       # HoltLinearForecaster (기본), EWMAForecaster (smoothing 유틸)
    anomaly_detector.py  # ZScoreDetector, IQRDetector
    proactive_action.py  # ProactiveActionTrigger, SpikeClassifier, SpikeType
    service.py           # PredictiveForecasterService
```

#### time_series.py 핵심 설계

```python
from __future__ import annotations

import collections
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ForecastDataPoint:
    """
    예측 히스토리의 개별 데이터포인트.

    v2 추가: has_adjustment 플래그 — Self-Fulfilling Prophecy 방지용.
    셀프힐링 시스템이 개입(adjustment)한 직후의 메트릭은 자연적 트렌드가 아닌
    인위적 변동이므로, 추후 필터링/가중치 조정의 기반 마커로 사용.
    Phase 1에서는 기록만 하고, Phase 3에서 가중치 차등 적용 검토.
    """
    value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    has_adjustment: bool = False  # True = 이 데이터포인트 직전에 셀프힐링 개입 발생


class HoltLinearForecaster:
    """
    Holt's Linear (Double Exponential Smoothing) 예측기.

    EWMA는 수평 예측만 가능한 반면, Holt Linear는 레벨(level)과
    트렌드(trend)를 분리 추적하여 방향성 있는 예측을 제공한다.

    외부 의존성 없이 표준 라이브러리만으로 구현.
    파라미터 2개(α, β), 상태 O(1), 메모리 불변.

    수식:
        level_t = α * value_t + (1 - α) * (level_{t-1} + trend_{t-1})
        trend_t = β * (level_t - level_{t-1}) + (1 - β) * trend_{t-1}
        forecast_{t+h} = level_t + h * trend_t

    Args:
        alpha: 레벨 평활 계수 (0 < α ≤ 1). 기본 0.3.
        beta: 트렌드 평활 계수 (0 < β ≤ 1). 기본 0.1.
            - 작을수록 트렌드 변화에 보수적 (0.01~0.05: 장기 트렌드)
            - 클수록 트렌드 변화에 민감 (0.1~0.3: 단기 트렌드)
        warmup_samples: 최소 데이터포인트 수 (미달 시 confidence=0)

    코드 근거:
        기존 BudgetDepletionForecaster (services/error_budget/forecaster.py)가
        선형 외삽을 사용하는데, Holt Linear는 이를 지수평활로 자연 확장한 것.
        AdaptiveThrottle._check_preemptive_protection() (services/throttle/adaptive.py L1232)
        패턴과 동일한 사전조치 아키텍처.
    """

    STORAGE_KEY_PREFIX = "forecaster"

    def __init__(
        self,
        alpha: float = 0.3,
        beta: float = 0.1,
        warmup_samples: int = 30,
        max_history: int = 10_000,
    ):
        self._alpha = alpha
        self._beta = beta
        self._warmup_samples = warmup_samples

        self._level: float | None = None
        self._trend: float = 0.0
        self._count: int = 0

        # Ring buffer for history (has_adjustment 태깅 포함)
        self._history: collections.deque[ForecastDataPoint] = collections.deque(
            maxlen=max_history
        )

    @property
    def is_warmed_up(self) -> bool:
        """warmup_samples 이상 데이터가 축적되었는지 여부."""
        return self._count >= self._warmup_samples

    def update(self, value: float, has_adjustment: bool = False) -> float:
        """
        새 데이터포인트 추가 & 현재 레벨 반환.

        Args:
            value: 관측값
            has_adjustment: 이 관측 직전에 셀프힐링 개입 발생 여부
                (Self-Fulfilling Prophecy 태깅, Phase 1에서는 기록만)

        Returns:
            현재 smoothed level
        """
        self._count += 1
        self._history.append(ForecastDataPoint(
            value=value, has_adjustment=has_adjustment
        ))

        if self._level is None:
            # 첫 번째 데이터포인트: 초기화
            self._level = value
            self._trend = 0.0
        else:
            prev_level = self._level
            # Holt's equations
            self._level = (
                self._alpha * value
                + (1 - self._alpha) * (prev_level + self._trend)
            )
            self._trend = (
                self._beta * (self._level - prev_level)
                + (1 - self._beta) * self._trend
            )

        return self._level

    def predict(self, steps_ahead: int = 5) -> float | None:
        """
        N스텝 후 예측값 반환.

        warmup_samples 미달 시 None 반환 (Cold Start 보호).

        Args:
            steps_ahead: 예측할 미래 스텝 수

        Returns:
            예측값 또는 None (데이터 부족 시)
        """
        if self._level is None or not self.is_warmed_up:
            return None
        return self._level + steps_ahead * self._trend

    def get_confidence(self) -> float:
        """
        현재 예측 신뢰도 (0.0 ~ 1.0).

        warmup_samples 미만 시 0.0 반환.
        이후 데이터량에 비례하여 증가, 200개 이상에서 1.0.

        v2 추가: warmup_samples 기반 Cold Start 보호.
        기존 DecisionEngine._calculate_confidence() (core/decision_engine.py L266-295)의
        CV 기반 stability_factor와 동일한 패턴.
        """
        if not self.is_warmed_up:
            return 0.0
        # warmup_samples 이후 선형 증가, 200개에서 1.0
        return min(1.0, self._count / 200)

    def get_trend_slope(self) -> float:
        """현재 트렌드 기울기 반환 (양수=상승, 음수=하강)."""
        return self._trend

    # =================================================================
    # StateBackend 영속성 (Cold Start 해결)
    # =================================================================

    def save_state(self, metric_name: str) -> bool:
        """
        현재 Forecaster 상태를 StateBackend에 저장.

        Cold Start 시 이전 학습 상태를 복원하기 위해 사용.
        ParameterBlacklist._save_to_storage() 패턴과 동일.

        코드 근거:
            - StateBackend ABC: core/state_backend.py
            - FileStateBackend: atomic JSON + tmp rename, 재시작 시에도 유지
            - RedisStateBackend: TTL 지원, 분산 환경 공유
            - ParameterBlacklist 영속성: services/learning/service.py L56-77

        Args:
            metric_name: 메트릭 식별자 (저장 키에 사용)

        Returns:
            저장 성공 여부

        저장 키: "forecaster:{metric_name}"
        TTL: 259,200초 (72시간) — 히스토리 커버 시간과 동일
        """
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            key = f"{self.STORAGE_KEY_PREFIX}:{metric_name}"

            state = {
                "alpha": self._alpha,
                "beta": self._beta,
                "level": self._level,
                "trend": self._trend,
                "count": self._count,
                "warmup_samples": self._warmup_samples,
                "history": [
                    {
                        "value": dp.value,
                        "timestamp": dp.timestamp.isoformat(),
                        "has_adjustment": dp.has_adjustment,
                    }
                    for dp in self._history
                ],
            }

            backend.set(key, state, ttl=259_200)  # 72시간 TTL
            logger.info(
                f"[HoltLinearForecaster] Saved state for '{metric_name}' "
                f"({self._count} points, level={self._level:.4f})"
            )
            return True
        except Exception as e:
            logger.warning(f"[HoltLinearForecaster] Failed to save state: {e}")
            return False

    def load_state(self, metric_name: str) -> bool:
        """
        StateBackend에서 이전 상태를 복원.

        프로세스 재시작 시 Cold Start 없이 즉시 예측 가능.

        코드 근거:
            - ParameterBlacklist._load_from_storage() (services/learning/service.py L79-97)
            - ColdStartSimulator 패턴 (tests/self_healing/integration/test_cold_start_recovery.py)

        Args:
            metric_name: 메트릭 식별자

        Returns:
            복원 성공 여부
        """
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            key = f"{self.STORAGE_KEY_PREFIX}:{metric_name}"

            state = backend.get(key)
            if state is None:
                logger.debug(
                    f"[HoltLinearForecaster] No saved state for '{metric_name}'"
                )
                return False

            self._alpha = state["alpha"]
            self._beta = state["beta"]
            self._level = state["level"]
            self._trend = state["trend"]
            self._count = state["count"]
            self._warmup_samples = state["warmup_samples"]

            # 히스토리 복원
            self._history.clear()
            for dp_dict in state.get("history", []):
                self._history.append(ForecastDataPoint(
                    value=dp_dict["value"],
                    timestamp=datetime.fromisoformat(dp_dict["timestamp"]),
                    has_adjustment=dp_dict.get("has_adjustment", False),
                ))

            logger.info(
                f"[HoltLinearForecaster] Restored state for '{metric_name}' "
                f"({self._count} points, level={self._level:.4f})"
            )
            return True
        except Exception as e:
            logger.warning(f"[HoltLinearForecaster] Failed to load state: {e}")
            return False


class EWMAForecaster:
    """
    Exponentially Weighted Moving Average — smoothing 유틸리티.

    v2 변경: Phase 1 기본 예측기에서 smoothing 유틸리티로 역할 변경.
    HoltLinearForecaster가 트렌드를 포함한 예측을 담당하며,
    EWMAForecaster는 노이즈 제거/smoothing 전처리에 사용.

    용도:
    - BudgetDepletionForecaster 선형 외삽의 입력 smoothing
    - anomaly_detector.py의 Z-Score 계산 전 노이즈 제거
    - 단독 예측에는 사용하지 않음 (수평 예측만 가능하므로)

    Args:
        alpha: 평활 계수 (0 < α ≤ 1). 클수록 최근 데이터에 가중.
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

    def get_smoothed(self) -> float | None:
        """현재 smoothed 값 반환"""
        return self._ewma

    def reset(self) -> None:
        """상태 초기화"""
        self._ewma = None
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

#### proactive_action.py 핵심 설계 (v2 추가: SpikeClassifier)

```python
from enum import Enum


class SpikeType(str, Enum):
    """
    트래픽 급증 유형 분류.

    v2 추가: Flash Sale(정상 급증) vs DDoS(이상 스파이크) 오분류 방지.
    Forecaster가 트래픽 급증을 감지했을 때, 정상적인 급증(Flash Sale, 프로모션)인지
    이상 급증(DDoS, 시스템 오류)인지를 구분하여 과잉 방어를 방지.

    네이밍 근거:
    - 기존 시스템의 `*Detector` 패턴(ZScoreDetector, IQRDetector)과 구분하기 위해
      `*Classifier`로 명명. Detector는 이상 여부를, Classifier는 유형을 판별.
    """
    HEALTHY_SURGE = "healthy_surge"
    """정상적인 트래픽 급증 (Flash Sale, 프로모션, 시즌성 피크).
    특징: error_rate 정상, latency 비례 증가, 점진적 상승 곡선"""

    ANOMALOUS_SPIKE = "anomalous_spike"
    """이상 급증 (DDoS, 크롤러, BGP 라우팅 오류).
    특징: error_rate 급등, latency 불규칙, 순간 수직 상승"""

    GRADUAL_DEGRADATION = "gradual_degradation"
    """점진적 악화 (메모리 누수, 커넥션 풀 고갈, 디스크 I/O 포화).
    특징: 느린 기울기의 지속적 상승, 급격한 변곡점 없음"""


class SpikeClassifier:
    """
    트래픽 급증 유형 분류기.

    멀티-시그널 기반으로 스파이크의 원인을 분류하여
    Forecaster의 사전 조치(proactive action) 유형과 강도를 결정.

    시그널:
    1. error_rate_delta: 에러율 변화량 (급등 = 이상)
    2. latency_trend: 레이턴시 트렌드 (비례 증가 = 정상, 불규칙 = 이상)
    3. rps_acceleration: 초당 요청 증가 가속도 (수직 = 이상, 곡선 = 정상)

    코드 근거:
        AdaptiveThrottle._check_preemptive_protection() (services/throttle/adaptive.py L1232)이
        이미 BudgetDepletion 기반으로 선제적 보호를 수행하는 패턴.
        SpikeClassifier는 이 패턴을 트래픽 급증 유형 분류로 확장.

    Args:
        error_rate_threshold: 에러율 임계값 (이 이상 증가 시 ANOMALOUS_SPIKE 가중)
        acceleration_threshold: RPS 가속도 임계값 (이 이상 시 ANOMALOUS_SPIKE 가중)
        sensitivity_multiplier: 도메인별 민감도 배율 (환경변수로 주입)
    """

    def __init__(
        self,
        error_rate_threshold: float = 0.05,
        acceleration_threshold: float = 2.0,
        sensitivity_multiplier: float = 1.0,
    ):
        self._error_rate_threshold = error_rate_threshold
        self._acceleration_threshold = acceleration_threshold
        self._sensitivity_multiplier = sensitivity_multiplier

    def classify(
        self,
        rps_history: list[float],
        error_rate_history: list[float],
        latency_history: list[float],
    ) -> SpikeType:
        """
        멀티-시그널 기반 스파이크 유형 분류.

        Args:
            rps_history: 최근 RPS(초당 요청) 히스토리
            error_rate_history: 최근 에러율 히스토리
            latency_history: 최근 레이턴시 히스토리

        Returns:
            SpikeType enum

        분류 로직:
        1. error_rate 급등 (최근 delta > threshold * sensitivity) → ANOMALOUS_SPIKE
        2. RPS 가속도 > threshold * sensitivity & error_rate 안정 → HEALTHY_SURGE
        3. 느린 기울기의 지속 상승 → GRADUAL_DEGRADATION
        """
        if len(rps_history) < 5 or len(error_rate_history) < 5:
            return SpikeType.GRADUAL_DEGRADATION  # 데이터 부족 시 보수적 판정

        # 에러율 변화량
        error_delta = error_rate_history[-1] - error_rate_history[-5]
        adjusted_error_threshold = (
            self._error_rate_threshold / self._sensitivity_multiplier
        )

        if error_delta > adjusted_error_threshold:
            return SpikeType.ANOMALOUS_SPIKE

        # RPS 가속도 (2차 도함수 근사)
        if len(rps_history) >= 10:
            recent_slope = rps_history[-1] - rps_history[-5]
            older_slope = rps_history[-5] - rps_history[-10]
            acceleration = recent_slope - older_slope if older_slope != 0 else 0

            adjusted_accel_threshold = (
                self._acceleration_threshold / self._sensitivity_multiplier
            )

            if abs(acceleration) > adjusted_accel_threshold:
                if error_delta <= adjusted_error_threshold:
                    return SpikeType.HEALTHY_SURGE
                return SpikeType.ANOMALOUS_SPIKE

        return SpikeType.GRADUAL_DEGRADATION


class ProactiveActionTrigger:
    """
    예측 기반 사전 조치 트리거.

    SpikeType에 따라 조치 유형과 강도를 차등 적용:
    - HEALTHY_SURGE: 조치 보류 또는 경미한 준비적 스케일링
    - ANOMALOUS_SPIKE: 즉시 방어적 조치 (throttle 강화, CB 임계치 하향)
    - GRADUAL_DEGRADATION: 점진적 사전 조치 (예측 기반 파라미터 미세 조정)

    코드 근거:
        AdaptiveThrottle._apply_preemptive_reduction()
        (services/throttle/adaptive.py L1269)의 선제적 보호 패턴 일반화.
    """
```

### Phase 2: 히스토리 크기 확장 + StateBackend 연동

| 변경 대상 | 현재 | 변경 | 근거 |
|----------|------|------|------|
| `PoolMonitorSettings.max_history` | 100 (le=1000) | 5,000 (le=10,000) | 72시간 커버 |
| `DecisionEngine._history` | 100 하드코딩 | 5,000 (Settings 전환) | 동일 |
| `AutoRollbackGuard._health_history` | 100 하드코딩 | 10,000 (Settings 전환) | 30초 간격 = 8,640 |
| `get_trend()` 분석 윈도우 | 10 vs 10 | 100 vs 100 | 통계적 유의성 |
| Forecaster ring buffer | — | 10,000 | 독립 buffer |

### Phase 3: 기존 시스템 연동 + Self-Fulfilling Prophecy 처리

| 연동 대상 | 연동 방식 | 코드 위치 |
|----------|----------|----------|
| DecisionEngine | `_calculate_confidence()`에 예측 트렌드 기울기 주입 | `core/decision_engine.py L266-295` |
| PoolMonitor | `get_trend()` 내부에서 HoltLinear 예측 활용 | `core/pool_monitor.py L356-375` |
| CgroupResourceMonitor | 메모리 사용량 추세의 HoltLinear 예측 추가 | `core/resource_monitor.py` |
| BudgetDepletionForecaster | 선형 외삽 입력에 EWMAForecaster smoothing 적용 (옵션) | `services/error_budget/forecaster.py L108-113` |
| **LearningService** | **예측 정확도 패턴 학습 + 블랙리스트 연동** | `services/learning/service.py` |
| **has_adjustment** | **Phase 1에서 기록한 태그 기반 가중치 차등 적용 검토** | `time_series.py` |

#### has_adjustment 가중치 차등 적용 (Phase 3 검토 대상)

Phase 1에서 `ForecastDataPoint.has_adjustment=True`로 기록한 데이터포인트에 대해:

```python
# Phase 3 검토 예시: 개입 직후 데이터에 낮은 가중치 부여
# 주의: Holt Linear는 online 알고리즘이므로 데이터를 "건너뛰기"하면
# 시계열 연속성이 깨짐. 따라서 건너뛰기 대신 가중치 감소 방식 검토.
#
# 옵션 A: alpha를 일시적으로 낮춤 (개입 직후 3~5 스텝)
# 옵션 B: 개입 전후 delta를 보정값으로 기록
# 옵션 C: 별도 "clean" 서브시리즈 유지 (메모리 2배, 복잡도 증가)
#
# 결정 기준: Phase 1~2 운영 데이터에서 has_adjustment=True 비율 확인 후 결정
```

### Phase 4: Holt-Winters 계절성 분석

충분한 히스토리(2~3 시즌) 축적 후 도입:

```python
class HoltWintersForecaster:
    """
    Holt-Winters 삼중지수평활 (Triple Exponential Smoothing).

    계절성 패턴 감지 및 예측.
    HoltLinearForecaster를 상속하여 계절성(gamma) 파라미터 추가.

    Args:
        alpha: 레벨 평활 (0.1~0.3)
        beta: 트렌드 평활 (0.01~0.1)
        gamma: 계절성 평활 (0.1~0.3)
        season_length: 시즌 길이 (일간 패턴 = 1440 if 60초 간격)
    """
```

---

## 8.1. LearningService 연동 설계 (v2 신규)

### 8.1.1 연동 가능성 분석

**결론: 연동 가능하며, 기존 인프라를 그대로 활용할 수 있다.**

`LearningService`(`services/learning/service.py`)는 다음 인터페이스를 이미 제공:

| 인터페이스 | 용도 (Forecaster 연동) | 코드 위치 |
|-----------|----------------------|----------|
| `learn_pattern(PatternType.ANOMALY, ...)` | 예측 이상 패턴 학습 | L342-397 |
| `learn_pattern(PatternType.PERFORMANCE, ...)` | 예측 정확도 패턴 학습 | L342-397 |
| `record_metric(metric_name, value, ...)` | 예측 정확도 메트릭 기록 | L418-458 |
| `_detect_anomaly(metric)` | 내장 이상 탐지 (평균의 2배 초과) | L460-478 |
| `_check_and_generate_suggestions(pattern)` | 패턴 기반 사전 조치 제안 생성 | L400-415 |
| `register_dangerous_parameter(...)` | 반복 오판 파라미터 블랙리스트 | L628-677 |
| `is_parameter_blocked(...)` | 블랙리스트 확인 | L679-695 |
| `is_manual_only_mode(module)` | Manual Only 모드 확인 | L715-731 |

핵심 모델:
- `PatternType` enum: `FAILURE`, `RECOVERY`, `PERFORMANCE`, `ANOMALY`, `OPTIMIZATION` — 모두 Forecaster 출력에 대응
- `LearningPattern` dataclass: `confidence`, `occurrence_count`, `features`, `metadata` — Forecaster 예측 메타데이터 저장 가능
- `Suggestion` dataclass: `action`, `parameters`, `expected_improvement` — Forecaster 기반 사전 조치 제안에 적합
- `ParameterBlacklist`: StateBackend 영속성 이미 구현 — Forecaster와 동일 패턴

### 8.1.2 연동 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│              PredictiveForecasterService                      │
│                                                              │
│   HoltLinearForecaster.predict()                             │
│        │                                                     │
│        ▼                                                     │
│   SpikeClassifier.classify()                                 │
│        │                                                     │
│        ├── ANOMALOUS_SPIKE ──► LearningService.learn_pattern │
│        │      (PatternType.ANOMALY)                          │
│        │                                                     │
│        ├── 예측 결과 ──► ProactiveActionTrigger               │
│        │      │                                              │
│        │      ▼                                              │
│        │  [블랙리스트 확인] ◄── LearningService               │
│        │      │               .is_parameter_blocked()        │
│        │      ▼                                              │
│        │  [Manual Only 확인] ◄── LearningService             │
│        │      │                .is_manual_only_mode()        │
│        │      ▼                                              │
│        │  사전 조치 실행                                      │
│        │                                                     │
│        ▼                                                     │
│   예측 정확도 평가                                             │
│        │                                                     │
│        ├── 정확 ──► LearningService.record_metric             │
│        │      ("forecaster_accuracy", value)                 │
│        │                                                     │
│        └── 반복 오판 ──► LearningService                      │
│               .register_dangerous_parameter()                │
│               (module="predictive_forecaster", ...)          │
└─────────────────────────────────────────────────────────────┘
```

### 8.1.3 연동 포인트 상세

#### A. 예측 이상 패턴 → LearningService 학습

```python
# service.py — PredictiveForecasterService
def _report_anomaly_to_learning(
    self,
    metric_name: str,
    spike_type: SpikeType,
    predicted_value: float,
    confidence: float,
):
    """
    감지된 이상을 LearningService에 패턴으로 등록.
    LearningService._check_and_generate_suggestions()가 3회 이상 발생 시
    자동으로 사전 조치 Suggestion을 생성.
    """
    from selfhealing.services.learning.service import LearningService

    learning = LearningService()  # 싱글톤
    learning.learn_pattern(
        pattern_type=PatternType.ANOMALY,
        name=f"PredictedAnomaly:{metric_name}:{spike_type.value}",
        description=f"Forecaster predicted {spike_type.value} for {metric_name}",
        features={
            "metric_name": metric_name,
            "spike_type": spike_type.value,
            "predicted_value": predicted_value,
        },
        confidence=confidence,
        metadata={"source": "predictive_forecaster"},
    )
```

#### B. 사전 조치 전 블랙리스트 확인

```python
# proactive_action.py — ProactiveActionTrigger
def should_take_action(self, module: str, parameter: str, value: str) -> bool:
    """
    사전 조치 실행 전 LearningService 블랙리스트 확인.
    과거에 반복 오판으로 블랙리스트된 파라미터 조합은 차단.
    """
    from selfhealing.services.learning.service import LearningService

    learning = LearningService()

    # Manual Only 모드 확인
    if learning.is_manual_only_mode("predictive_forecaster"):
        logger.warning("[ProactiveAction] Forecaster is in MANUAL ONLY mode")
        return False

    # 블랙리스트 확인
    is_blocked, entry = learning.is_parameter_blocked(module, parameter, value)
    if is_blocked:
        logger.warning(
            f"[ProactiveAction] Blocked by blacklist: {module}:{parameter}={value}"
        )
        return False

    return True
```

#### C. 예측 정확도 메트릭 기록

```python
# service.py — PredictiveForecasterService
def _evaluate_prediction_accuracy(
    self,
    metric_name: str,
    predicted_value: float,
    actual_value: float,
):
    """
    예측 정확도를 LearningService에 메트릭으로 기록.
    LearningService._detect_anomaly()가 정확도 급락 시 자동 패턴 학습.
    """
    from selfhealing.services.learning.service import LearningService

    accuracy = 1.0 - abs(predicted_value - actual_value) / max(abs(actual_value), 1e-10)
    accuracy = max(0.0, min(1.0, accuracy))

    learning = LearningService()
    learning.record_metric(
        metric_name=f"forecaster_accuracy:{metric_name}",
        value=accuracy,
        stage_name="predictive_forecaster",
        tags={"metric": metric_name, "predicted": str(predicted_value)},
    )
```

#### D. 반복 오판 시 블랙리스트 자동 등록

```python
# service.py — PredictiveForecasterService
def _handle_repeated_misprediction(
    self,
    metric_name: str,
    parameter: str,
    misprediction_count: int,
    threshold: int = 3,
):
    """
    동일 메트릭/파라미터에서 threshold회 연속 오판 시
    LearningService 블랙리스트에 자동 등록.
    """
    if misprediction_count < threshold:
        return

    from selfhealing.services.learning.service import LearningService
    from selfhealing.services.learning.models import BlacklistReason

    learning = LearningService()
    learning.register_dangerous_parameter(
        module="predictive_forecaster",
        parameter=parameter,
        blocked_values={metric_name},
        reason=BlacklistReason.FLAPPING,
        ttl_hours=168,  # 7일 후 자동 해제
    )
    logger.warning(
        f"[Forecaster] Blacklisted: predictive_forecaster:{parameter}={metric_name} "
        f"({misprediction_count} consecutive mispredictions)"
    )
```

### 8.1.4 LearningService 연동 이점

1. **반복 오판 자동 차단**: ParameterBlacklist가 이미 StateBackend 영속성을 갖추고 있어, 프로세스 재시작 후에도 블랙리스트 유지
2. **Cross-Stage 인사이트**: `LearningService.get_cross_stage_insights()`로 여러 Stage에서 공통으로 발생하는 예측 이상 패턴 발견 가능
3. **수동 개입 지원**: `set_manual_only_mode("predictive_forecaster")`로 운영자가 Forecaster 자동 조치를 일시 중단 가능
4. **기존 인프라 100% 재사용**: 새로운 모델/인터페이스 추가 없이 기존 `PatternType`, `BlacklistReason`, `Suggestion` 등을 그대로 활용

### Phase 1: 핵심 엔진 (HoltLinear + SpikeClassifier + Z-Score/IQR)
- [ ] `services/predictive_forecaster/__init__.py`
- [ ] `services/predictive_forecaster/time_series.py` — HoltLinearForecaster (기본), EWMAForecaster (smoothing 유틸)
- [ ] `services/predictive_forecaster/time_series.py` — ForecastDataPoint (has_adjustment 태깅)
- [ ] `services/predictive_forecaster/time_series.py` — save_state() / load_state() (StateBackend 영속성)
- [ ] `services/predictive_forecaster/anomaly_detector.py` — ZScoreDetector, IQRDetector
- [ ] `services/predictive_forecaster/proactive_action.py` — ProactiveActionTrigger
- [ ] `services/predictive_forecaster/proactive_action.py` — SpikeClassifier, SpikeType
- [ ] `services/predictive_forecaster/service.py` — PredictiveForecasterService
- [ ] Settings: `settings/predictive_forecaster.py` — Pydantic v2 기반 (sensitivity_multiplier, holt_beta, warmup_samples 포함)
- [ ] Synthetic Time Series Generator (테스트 데이터 생성기)
- [ ] 단위 테스트: HoltLinear 트렌드 예측 정확도, warmup_samples 동작 검증
- [ ] 단위 테스트: SpikeClassifier HEALTHY_SURGE vs ANOMALOUS_SPIKE 분류 정확도
- [ ] 단위 테스트: StateBackend save/load 왕복 검증
- [ ] 단위 테스트: has_adjustment 태깅 기록 검증
- [ ] DRY_RUN 모드 통합 테스트

### Phase 2: 히스토리 크기 확장 + StateBackend 연동
- [ ] `PoolMonitorSettings.max_history` 기본값 5,000 / 상한 10,000
- [ ] `DecisionEngine._history` Settings 전환 + 기본값 5,000
- [ ] `AutoRollbackGuard._health_history` Settings 전환 + 기본값 10,000
- [ ] `get_trend()` 분석 윈도우 100개로 확대
- [ ] 각 Settings 환경변수 문서화
- [ ] StateBackend 저장 주기 설정 (forecaster_save_interval)

### Phase 3: 기존 시스템 연동 + LearningService 연동
- [ ] DecisionEngine.`_calculate_confidence()` 예측 컨텍스트 주입
- [ ] PoolMonitor.`get_trend()` HoltLinear 기반 예측 대체
- [ ] CgroupResourceMonitor OOM 예측 추가
- [ ] BudgetDepletionForecaster 선형 외삽 입력에 EWMAForecaster smoothing 적용 (옵션)
- [ ] LearningService 연동: 예측 이상 패턴 → learn_pattern(PatternType.ANOMALY)
- [ ] LearningService 연동: 사전 조치 전 is_parameter_blocked() 확인
- [ ] LearningService 연동: 예측 정확도 → record_metric()
- [ ] LearningService 연동: 반복 오판 → register_dangerous_parameter()
- [ ] has_adjustment 가중치 차등 적용 검토 (운영 데이터 기반 결정)

### Phase 4: Holt-Winters (계절성)
- [ ] `time_series.py`에 HoltWintersForecaster 추가
- [ ] 계절성 자동 감지 (auto-detect season_length)
- [ ] 프로덕션 메트릭 기반 검증

---

## 10. 설정 인터페이스

```python
# settings/predictive_forecaster.py (신규)

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PredictiveForecasterSettings(BaseSettings):
    """
    Predictive Anomaly Forecaster 설정.

    v2 확장:
    - holt_beta: Holt Linear 트렌드 평활 계수 (v2 추가)
    - sensitivity_multiplier: 도메인별 민감도 배율 (v2 추가)
    - warmup_samples: Cold Start 보호 최소 데이터 수 (v2 추가)
    - spike_error_rate_threshold: SpikeClassifier 에러율 임계값 (v2 추가)
    - spike_acceleration_threshold: SpikeClassifier RPS 가속도 임계값 (v2 추가)
    - state_save_interval: StateBackend 저장 주기 (v2 추가)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_FORECASTER_",
    )

    # ── HoltLinearForecaster (Phase 1 기본 예측기) ──
    ewma_alpha: float = Field(
        default=0.3, ge=0.01, le=1.0,
        description="레벨 평활 계수 (α). EWMA smoothing에도 공유."
    )
    holt_beta: float = Field(
        default=0.1, ge=0.001, le=1.0,
        description="Holt Linear 트렌드 평활 계수 (β). "
                    "작을수록 트렌드 변화에 보수적 (0.01~0.05: 장기), "
                    "클수록 트렌드 변화에 민감 (0.1~0.3: 단기)."
    )

    # ── 이상 탐지 ──
    zscore_threshold: float = Field(
        default=3.0, ge=1.0, le=5.0,
        description="Z-Score 이상 탐지 임계값 (3.0 = 99.7% 신뢰구간)."
    )
    zscore_window: int = Field(
        default=100, ge=10, le=10000,
        description="Z-Score 이동 윈도우 크기."
    )
    iqr_multiplier: float = Field(
        default=1.5, ge=1.0, le=5.0,
        description="IQR 이상 탐지 배수 (1.5 = 일반, 3.0 = 극단적)."
    )

    # ── 히스토리 / Cold Start ──
    max_history: int = Field(
        default=10000, ge=100, le=10000,
        description="예측 히스토리 ring buffer 크기."
    )
    warmup_samples: int = Field(
        default=30, ge=5, le=1000,
        description="Cold Start 보호: 이 수 미만의 데이터포인트에서는 "
                    "confidence를 0으로 반환하여 예측 기반 조치를 억제."
    )

    # ── 예측 ──
    prediction_steps: int = Field(
        default=5, ge=1, le=30,
        description="기본 예측 스텝 수 (60초 간격 시 5 = 5분 후 예측)."
    )

    # ── 사전 조치 ──
    min_confidence_for_action: float = Field(
        default=0.7, ge=0.0, le=1.0,
        description="사전 조치 실행에 필요한 최소 예측 신뢰도."
    )
    dry_run: bool = Field(
        default=True,
        description="도입 초기 기본값: DRY_RUN. "
                    "예측 정확도 검증 후 False로 전환."
    )

    # ── 민감도 (v2 추가) ──
    sensitivity_multiplier: float = Field(
        default=1.0, ge=0.1, le=100.0,
        description="도메인별 민감도 배율. "
                    "DomainSensitivitySettings (settings/domain_sensitivity.py)와 "
                    "동일한 0.1~100.0 범위. "
                    "payment 도메인은 10.0+, 낮은 중요도는 0.5 등. "
                    "SpikeClassifier의 임계값에 반비례 적용 "
                    "(높은 민감도 = 낮은 임계값 = 더 민감한 탐지)."
    )

    # ── SpikeClassifier (v2 추가) ──
    spike_error_rate_threshold: float = Field(
        default=0.05, ge=0.001, le=1.0,
        description="SpikeClassifier: 에러율 변화량이 이 값 초과 시 "
                    "ANOMALOUS_SPIKE로 분류 (sensitivity_multiplier 적용 전 기본값)."
    )
    spike_acceleration_threshold: float = Field(
        default=2.0, ge=0.1, le=100.0,
        description="SpikeClassifier: RPS 가속도 임계값."
    )

    # ── StateBackend 영속성 (v2 추가) ──
    state_save_interval: int = Field(
        default=300, ge=60, le=3600,
        description="StateBackend 저장 주기 (초). 기본 5분(300초)."
    )
    state_ttl: int = Field(
        default=259200, ge=3600, le=604800,
        description="StateBackend 상태 TTL (초). 기본 72시간(259,200초)."
    )
```

환경변수:
```bash
# HoltLinearForecaster
SELFHEALING_FORECASTER_EWMA_ALPHA=0.3
SELFHEALING_FORECASTER_HOLT_BETA=0.1

# 이상 탐지
SELFHEALING_FORECASTER_ZSCORE_THRESHOLD=3.0
SELFHEALING_FORECASTER_ZSCORE_WINDOW=100
SELFHEALING_FORECASTER_IQR_MULTIPLIER=1.5

# 히스토리 / Cold Start
SELFHEALING_FORECASTER_MAX_HISTORY=10000
SELFHEALING_FORECASTER_WARMUP_SAMPLES=30

# 예측 / 사전 조치
SELFHEALING_FORECASTER_PREDICTION_STEPS=5
SELFHEALING_FORECASTER_MIN_CONFIDENCE_FOR_ACTION=0.7
SELFHEALING_FORECASTER_DRY_RUN=true

# 민감도
SELFHEALING_FORECASTER_SENSITIVITY_MULTIPLIER=1.0

# SpikeClassifier
SELFHEALING_FORECASTER_SPIKE_ERROR_RATE_THRESHOLD=0.05
SELFHEALING_FORECASTER_SPIKE_ACCELERATION_THRESHOLD=2.0

# StateBackend 영속성
SELFHEALING_FORECASTER_STATE_SAVE_INTERVAL=300
SELFHEALING_FORECASTER_STATE_TTL=259200
```

---

## 11. 리뷰 반영 결정 근거 요약 (v2)

| # | 리뷰 항목 | 결정 | 핵심 근거 |
|---|----------|------|----------|
| 1 | sensitivity_multiplier 주입 | ✅ 반영 | DomainSensitivitySettings (settings/domain_sensitivity.py)와 동일 패턴. 도메인-프리 원칙 준수: 하드코딩 없이 env var로 주입. |
| 2 | EWMA → HoltLinearForecaster | ✅ Phase 1 기본 예측기로 승격 | EWMA는 수평 예측만 가능 (트렌드 없음). Holt Linear는 level + trend 분리 추적으로 방향성 예측 가능. 파라미터 1개 추가(β)로 예측력 대폭 향상. 0 dependency 유지. |
| 3 | SpikeClassifier 추가 | ✅ proactive_action.py에 반영 | Flash Sale(정상 급증)에 방어적 조치를 취하면 매출 손실. 멀티-시그널(error_rate + RPS acceleration) 기반 분류. Detector vs Classifier 네이밍 구분 명확. |
| 4 | StateBackend 영속성 | ✅ save_state/load_state 반영 | ParameterBlacklist가 이미 동일 StateBackend 패턴 사용 (services/learning/service.py L56-77). FileStateBackend (atomic JSON + tmp rename), RedisStateBackend (TTL, 분산) 모두 지원. 72시간 TTL. |
| 5 | warmup_samples | ✅ Cold Start 보호 반영 | 데이터 부족 시 예측 기반 조치를 억제. DecisionEngine._calculate_confidence()의 min_confidence 필터와 동일 안전 패턴. 기본 30개 (30분). |
| 6 | has_adjustment 태깅 | ✅ 기록 반영, 필터링은 Phase 3 | ForecastDataPoint.has_adjustment로 셀프힐링 개입 마킹. Holt Linear는 online 알고리즘이므로 데이터 건너뛰기 시 시계열 연속성 깨짐. Phase 1에서는 기록만, Phase 3에서 가중치 차등 적용 검토. |
| 7 | LearningService 연동 | ✅ §8.1 신규 섹션으로 반영 | 기존 인프라 100% 재사용: PatternType.ANOMALY 학습, ParameterBlacklist 블랙리스트, record_metric() 정확도 기록. 새 모델/인터페이스 추가 불필요. |
| 8 | 설정 인터페이스 확장 | ✅ §10 확장 반영 | holt_beta, sensitivity_multiplier, warmup_samples, spike_*, state_* 필드 추가. 기존 Pydantic v2 BaseSettings + env_prefix 패턴 동일. |

---

## 부록 A. 0 Dependency 원칙 준수 확인

모든 구현이 Python 표준 라이브러리만 사용:

| 기능 | 사용 모듈 | 외부 의존성 |
|------|----------|----------|
| Holt Linear (이중지수평활) | — (산술 연산) | 없음 |
| EWMA | — (산술 연산) | 없음 |
| Holt-Winters (삼중지수평활) | — (산술 연산) | 없음 |
| Z-Score | `math.sqrt` | 없음 |
| IQR | `sorted()` (내장) | 없음 |
| SpikeClassifier | — (산술 연산, 비교) | 없음 |
| Ring Buffer | `collections.deque` | 없음 |
| 시계열 저장 | `list` / `deque` | 없음 |
| StateBackend 영속성 | `json` (표준 라이브러리) | 없음 |
| 타임스탬프 | `datetime` (표준 라이브러리) | 없음 |

## 부록 B. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `core/decision_engine.py` | DecisionEngine 반응형 규칙, 신뢰도 계산 |
| `core/runtime_feedback.py` | RuntimeFeedbackLoop 관찰-조정 루프, AdjustmentResult |
| `core/pool_monitor.py` | ConnectionPoolMonitor 트렌드 분석 |
| `core/resource_monitor.py` | CgroupResourceMonitor 메모리 조회 |
| `core/safety_bounds.py` | SafetyBounds 범위/변경폭 제한 |
| `core/auto_rollback_guard.py` | AutoRollbackGuard 독립 안전장치 |
| `core/state_backend.py` | StateBackend ABC, FileStateBackend, RedisStateBackend |
| `services/error_budget/forecaster.py` | BudgetDepletionForecaster 예측 |
| `services/throttle/adaptive.py` | AdaptiveThrottle 선제적 보호 |
| `services/auto_tuning/service.py` | AutoTuningService DRY_RUN/Governance |
| `services/learning/service.py` | LearningService, ParameterBlacklist |
| `services/learning/models.py` | PatternType, BlacklistReason, LearningPattern, Suggestion |
| `adapters/metrics/auto_tuning_adapter.py` | MockMetricsAdapter 시뮬레이션 |
| `settings/scale.py` | ScaleSettings ring_buffer_capacity |
| `settings/pool_monitor.py` | PoolMonitorSettings max_history |
| `settings/domain_sensitivity.py` | DomainSensitivitySettings sensitivity 범위 참조 |
| `load_tests/scenarios/load/stage0_selfhealing_extreme.py` | Locust extreme 테스트 |
| `tests/self_healing/integration/test_cold_start_recovery.py` | ColdStartSimulator 패턴 참조 |
