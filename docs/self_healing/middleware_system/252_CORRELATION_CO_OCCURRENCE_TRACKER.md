# 252. Co-occurrence Tracker — 동시발생 이상 탐지

> **Version**: 1.0.0
> **Created**: 2026-02-20
> **Status**: Approved
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `services/correlation_engine/co_occurrence_tracker.py`

---

## 0. 요약

이벤트 쌍(pair)별 동시 발생 빈도를 시간 윈도우 기반으로 추적하고, 기존 `ZScoreDetector`를 재사용하여 **"비정상적으로 자주 함께 발생하는 이벤트 쌍"**을 통계적으로 탐지한다. 이것이 원안의 Granger Causality를 **순수 Python + 외부 의존성 없이** 대체하는 핵심 모듈이다.

---

## 1. 왜 Granger Causality가 아닌가

### 1.1 Granger Causality의 전제 조건

| 전제 | 현재 시스템 현실 | 충족 여부 |
|------|----------------|----------|
| 등간격 시계열 데이터 | EventType은 불규칙 발생 (CB OPEN은 장애 시에만) | ❌ |
| 충분한 샘플 수 (≥30~50) | Emergency는 월 1~2회 | ❌ |
| 정상성(Stationarity) | 운영 환경 변화로 분포 지속 변동 | ❌ |
| numpy/scipy 의존성 | 프로젝트 기조: 순수 Python | ❌ |

### 1.2 Co-occurrence 접근법의 장점

| 장점 | 상세 |
|------|------|
| **불규칙 이벤트 대응** | 발생 여부(boolean)만 추적, 등간격 불필요 |
| **소량 샘플 유효** | 10회 동시 발생만으로도 ZScore 이상치 판단 가능 |
| **순수 Python** | 기존 `ZScoreDetector` 재사용, 외부 의존성 없음 |
| **해석 가능성** | "이 두 이벤트가 최근 N회 중 M회 함께 발생" — 직관적 |

---

## 2. 핵심 자료구조

### 2.1 EventPairKey

```python
@dataclass(frozen=True)
class EventPairKey:
    """이벤트 쌍 식별자 — 순서 무관 (정렬)"""
    event_type_a: str
    event_type_b: str

    def __post_init__(self):
        # 정렬하여 (A,B) = (B,A) 보장
        if self.event_type_a > self.event_type_b:
            object.__setattr__(self, 'event_type_a', self.event_type_b)
            object.__setattr__(self, 'event_type_b', self.event_type_a)

    @property
    def key(self) -> str:
        return f"{self.event_type_a}::{self.event_type_b}"
```

### 2.2 CoOccurrenceRecord

```python
@dataclass
class CoOccurrenceRecord:
    """이벤트 쌍의 동시발생 통계"""
    pair: EventPairKey
    co_occurrence_count: int          # 시간 윈도우 내 동시 발생 횟수
    individual_count_a: int           # event_type_a 단독 발생 횟수
    individual_count_b: int           # event_type_b 단독 발생 횟수
    avg_time_gap_seconds: float       # 평균 시간 간격
    min_time_gap_seconds: float       # 최소 시간 간격
    last_co_occurrence: float         # 마지막 동시 발생 timestamp
    z_score: float | None             # 현재 빈도의 ZScore
    is_anomalous: bool                # ZScore 이상치 여부
```

### 2.3 CorrelationResult

```python
@dataclass(frozen=True)
class CorrelationResult:
    """상관관계 분석 결과"""
    pair: EventPairKey
    correlation_score: float          # 0.0 ~ 1.0
    direction: str | None             # "a_causes_b" | "b_causes_a" | "mutual" | None
    evidence: str                     # 사람이 읽을 수 있는 설명
    sample_count: int                 # 기반 샘플 수
    confidence: float                 # 통계적 신뢰도
```

---

## 3. 알고리즘 상세

### 3.1 시간 윈도우 기반 동시 발생 카운팅

```python
class CoOccurrenceTracker:
    """이벤트 쌍별 동시발생 빈도 추적기"""

    def __init__(self, settings: CorrelationEngineSettings):
        self._window_seconds: float = settings.window_seconds       # 기본 300초
        self._max_pairs: int = settings.max_tracked_pairs           # 기본 1000
        self._min_co_occurrences: int = settings.min_co_occurrences # 기본 3
        self._zscore_threshold: float = settings.zscore_threshold   # 기본 2.5

        # 이벤트 타입별 최근 발생 시각 버퍼
        self._event_timestamps: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=settings.max_event_buffer)  # 기본 500
        )

        # 이벤트 쌍별 동시발생 카운트 시계열
        self._pair_counts: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=settings.count_history_size)  # 기본 100
        )

        # 이벤트 쌍별 ZScore 탐지기 (기존 모듈 재사용)
        self._pair_detectors: dict[str, ZScoreDetector] = {}

        # 이벤트 쌍별 시간 간격 추적
        self._pair_time_gaps: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=100)
        )

    def record_event(self, event_type: str, timestamp: float, service_name: str) -> None:
        """이벤트 발생 기록 → 동시발생 쌍 업데이트"""
        self._event_timestamps[event_type].append(timestamp)

        # 현재 이벤트와 윈도우 내 다른 이벤트 타입들의 동시발생 체크
        for other_type, other_timestamps in self._event_timestamps.items():
            if other_type == event_type:
                continue
            # 윈도우 내에 other_type 발생이 있는지
            recent = [t for t in other_timestamps if timestamp - t <= self._window_seconds]
            if recent:
                pair = EventPairKey(event_type, other_type)
                self._update_pair_count(pair, timestamp, min(recent, key=lambda t: abs(timestamp - t)))

    def _update_pair_count(self, pair: EventPairKey, timestamp: float, closest_timestamp: float) -> None:
        """쌍별 카운트 및 ZScore 업데이트"""
        key = pair.key

        # 시간 간격 기록
        time_gap = abs(timestamp - closest_timestamp)
        self._pair_time_gaps[key].append(time_gap)

        # 현재 윈도우의 동시발생 카운트 증가
        # (슬라이딩 윈도우: 일정 주기마다 count를 ZScore에 feed)
        if key not in self._pair_detectors:
            self._pair_detectors[key] = ZScoreDetector(
                window_size=100, threshold=self._zscore_threshold
            )
```

### 3.2 주기적 분석 (Tick)

```python
    def analyze_tick(self) -> list[CorrelationResult]:
        """주기적 호출 (기본 60초마다) — 모든 쌍의 이상 여부 판단"""
        results: list[CorrelationResult] = []
        current_time = time.time()

        for pair_key, detector in self._pair_detectors.items():
            # 현재 윈도우 내 동시발생 카운트 계산
            count = self._count_co_occurrences_in_window(pair_key, current_time)
            is_anomalous, z_score = detector.is_anomaly(float(count))

            if is_anomalous and count >= self._min_co_occurrences:
                pair = self._parse_pair_key(pair_key)
                time_gaps = list(self._pair_time_gaps[pair_key])
                avg_gap = sum(time_gaps) / len(time_gaps) if time_gaps else 0

                # 방향성 추론: 평균적으로 어떤 이벤트가 먼저 발생하는지
                direction = self._infer_direction(pair, time_gaps)

                correlation_score = min(1.0, abs(z_score) / (self._zscore_threshold * 2))

                results.append(CorrelationResult(
                    pair=pair,
                    correlation_score=correlation_score,
                    direction=direction,
                    evidence=(
                        f"최근 {len(time_gaps)}회 동시발생, "
                        f"평균 간격 {avg_gap:.1f}초, "
                        f"Z-Score={z_score:.2f} (임계값={self._zscore_threshold})"
                    ),
                    sample_count=len(time_gaps),
                    confidence=min(1.0, len(time_gaps) / 30),  # 30회 이상이면 신뢰도 1.0
                ))

        return results
```

### 3.3 방향성 추론

```python
    def _infer_direction(self, pair: EventPairKey, time_gaps: list[float]) -> str | None:
        """시간 간격의 부호로 인과 방향 추론"""
        if not time_gaps or len(time_gaps) < 5:
            return None

        # time_gap이 양수면 A가 먼저, 음수면 B가 먼저
        a_first_count = sum(1 for g in time_gaps if g > 0)
        b_first_count = sum(1 for g in time_gaps if g < 0)

        ratio = a_first_count / len(time_gaps)
        if ratio >= 0.7:
            return "a_causes_b"
        elif ratio <= 0.3:
            return "b_causes_a"
        else:
            return "mutual"  # 양방향 또는 공통 원인
```

---

## 4. 시계열 트렌드 추적

동시발생 빈도의 **시간적 트렌드**를 기존 `HoltLinearForecaster`로 추적한다:

```python
    def get_trend(self, pair_key: str) -> dict | None:
        """특정 이벤트 쌍의 동시발생 빈도 트렌드"""
        if pair_key not in self._pair_forecasters:
            return None

        forecaster = self._pair_forecasters[pair_key]
        predicted = forecaster.predict(steps_ahead=5)
        confidence = forecaster.get_confidence()

        return {
            "pair": pair_key,
            "current_frequency": self._get_current_frequency(pair_key),
            "predicted_frequency_5_ticks_ahead": predicted,
            "trend_direction": "increasing" if forecaster._trend > 0 else "decreasing",
            "confidence": confidence,
        }
```

이를 통해 "이 상관관계가 점점 강해지고 있다" / "약해지고 있다"를 판단.

---

## 5. StateBackend 영속화

```python
    def save_state(self) -> bool:
        """학습된 상관관계 상태 영속화 (Cold Start 방지)"""
        state = {
            "pair_detectors": {
                key: {"values": list(det._values), "threshold": det._threshold}
                for key, det in self._pair_detectors.items()
            },
            "pair_time_gaps": {
                key: list(gaps) for key, gaps in self._pair_time_gaps.items()
            },
            "saved_at": time.time(),
        }
        return StateBackend.set("correlation:co_occurrence_state", state)

    def load_state(self) -> bool:
        """저장된 상태 복원"""
        state = StateBackend.get("correlation:co_occurrence_state")
        if not state:
            return False
        # ... 복원 로직
        return True
```

---

## 6. 설정 항목

`CorrelationEngineSettings`의 Co-occurrence 관련 필드 (257번에서 전체 설정 상세):

| 환경변수 | 기본값 | 설명 |
|---------|-------|------|
| `SELFHEALING_CORRELATION_WINDOW_SECONDS` | `300` | 동시발생 판단 시간 윈도우 (초) |
| `SELFHEALING_CORRELATION_ZSCORE_THRESHOLD` | `2.5` | ZScore 이상치 임계값 |
| `SELFHEALING_CORRELATION_MIN_CO_OCCURRENCES` | `3` | 최소 동시발생 횟수 (이하 무시) |
| `SELFHEALING_CORRELATION_MAX_TRACKED_PAIRS` | `1000` | 추적 가능한 최대 이벤트 쌍 수 |
| `SELFHEALING_CORRELATION_ANALYSIS_INTERVAL` | `60` | 분석 틱 주기 (초) |
| `SELFHEALING_CORRELATION_MAX_EVENT_BUFFER` | `500` | 이벤트 타입별 최대 타임스탬프 버퍼 |
| `SELFHEALING_CORRELATION_COUNT_HISTORY_SIZE` | `100` | 쌍별 카운트 히스토리 크기 |

---

## 7. 메모리 사용량 분석

| 구성 요소 | 최대 크기 | 산출 근거 |
|-----------|----------|----------|
| `_event_timestamps` | 42 타입 × 500 float = ~168KB | 42개 EventType × 500 deque |
| `_pair_detectors` | 1000 쌍 × 100 float = ~800KB | max_tracked_pairs × window_size |
| `_pair_time_gaps` | 1000 쌍 × 100 float = ~800KB | 동일 |
| `_pair_forecasters` | 1000 × ~200B = ~200KB | HoltLinearForecaster 인스턴스 |
| **합계** | **~2MB** | 설정 기본값 기준 |

→ `max_event_buffer=10MB` 비기능 요구사항 내.

---

## 8. 기존 모듈 재사용 매핑

| 이 모듈에서 사용 | 기존 모듈 | 재사용 방식 |
|----------------|----------|-----------|
| `ZScoreDetector` | `predictive_forecaster/anomaly_detector.py` | 동시발생 카운트의 이상치 판단에 직접 인스턴스화 |
| `HoltLinearForecaster` | `predictive_forecaster/time_series.py` | 동시발생 빈도의 트렌드 예측에 직접 인스턴스화 |
| `StateBackend` | `state_backend.py` | 학습 상태 영속화 |
| `LearningService.learn_pattern()` | `learning/` | 발견된 상관 패턴 축적 (PatternType.ANOMALY) |

---

## 9. 테스트 전략

| 테스트 유형 | 시나리오 | 검증 |
|------------|---------|------|
| **단위** | 5분 내 CB_OPENED + ERROR_BUDGET_CRITICAL 3회 동시 | `is_anomalous=True` 반환 |
| **단위** | 동시발생 횟수 < min_co_occurrences | 결과에서 필터링됨 |
| **단위** | A가 항상 B보다 먼저 발생 | `direction="a_causes_b"` |
| **단위** | 양방향 동시발생 | `direction="mutual"` |
| **통합** | StateBackend save → 재시작 → load | 학습 상태 복원 확인 |
| **성능** | 1000쌍 × 100 히스토리 | analyze_tick() ≤ 50ms |
| **경계값** | max_tracked_pairs 초과 | LRU로 오래된 쌍 제거 확인 |
