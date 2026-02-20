# 252. Co-occurrence Tracker — 동시발생 이상 탐지

> **Version**: 1.1.0
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
        """이벤트 발생 기록 → 동시발생 쌍 업데이트

        Thread-Safety:
            SelfHealingEventBus는 동기 + 멀티스레드 환경이므로
            (RedisEventBus 리스너 스레드, Celery 워커 등),
            순회 전 dict/deque를 얕은 복사(Shallow Copy)하여
            RuntimeError: deque mutated during iteration을 방지한다.
            기존 CoOccurrenceSnapshot(frozen=True)의 Copy-on-Write 패턴과 동일.
            — co_occurrence_tracker.py (CoOccurrenceSnapshot 참조)
        """
        self._event_timestamps[event_type].append(timestamp)

        # 현재 이벤트와 윈도우 내 다른 이벤트 타입들의 동시발생 체크
        # ── Thread-Safety: dict + deque 얕은 복사 후 순회
        snapshot = dict(self._event_timestamps)
        for other_type, other_timestamps in snapshot.items():
            if other_type == event_type:
                continue
            # 윈도우 내에 other_type 발생이 있는지
            # ── deque를 list로 복사하여 순회 중 변이 방지
            ts_copy = list(other_timestamps)
            recent = [t for t in ts_copy if timestamp - t <= self._window_seconds]
            if recent:
                pair = EventPairKey(event_type, other_type)
                closest = min(recent, key=lambda t: abs(timestamp - t))
                self._update_pair_count(pair, event_type, timestamp, closest)

    def _update_pair_count(
        self,
        pair: EventPairKey,
        current_event_type: str,
        timestamp: float,
        closest_timestamp: float,
    ) -> None:
        """쌍별 카운트 및 ZScore 업데이트

        시간 간격 부호 규칙:
            항상 signed_gap = timestamp_A - timestamp_B 로 계산한다.
            양수면 A가 나중(B가 먼저), 음수면 A가 먼저 발생.
            current_event_type 파라미터로 정렬 후 A/B를 구분하여
            알파벳 정렬과 부호가 일관되도록 보장한다.
        """
        key = pair.key

        # 시간 간격 기록 — 부호(signed) 보존
        # 부호 규칙: gap = timestamp(정렬된 A) - timestamp(정렬된 B)
        #   양수 → A가 나중 발생 (B가 원인 후보)
        #   음수 → A가 먼저 발생 (A가 원인 후보)
        if current_event_type == pair.event_type_a:
            signed_gap = timestamp - closest_timestamp   # A의 시각 - B의 시각
        else:
            signed_gap = closest_timestamp - timestamp   # A의 시각 - B의 시각
        self._pair_time_gaps[key].append(signed_gap)

        # 현재 윈도우의 동시발생 카운트 증가
        # (슬라이딩 윈도우: 일정 주기마다 count를 ZScore에 feed)
        if key not in self._pair_detectors:
            self._pair_detectors[key] = ZScoreDetector(
                window_size=100, threshold=self._zscore_threshold
            )
            # Audit Logging: 분산 0에서 첫 동시발생 기록
            # — 사후 장애 분석 시 미지의 패턴 발견에 유용
            logger.debug("First co-occurrence detected for pair: %s", key)
```

### 3.2 주기적 분석 (Tick)

```python
    def __init__(self, settings: CorrelationEngineSettings):
        # ... (§3.1 참조) ...

        # 알람 디바운스: 동일 페어의 이상 상태는 윈도우 시간 내 1회만 리포팅
        # — EventGraphTrigger.should_build() 및
        #   RateLimitCoordinator._should_emit_event() 패턴 차용
        self._last_report_times: dict[str, float] = {}

    def analyze_tick(self) -> list[CorrelationResult]:
        """주기적 호출 (기본 60초마다) — 모든 쌍의 이상 여부 판단

        Window Overlap Plateau 방지:
            300초 윈도우 + 60초 틱에서, 1번의 스파이크가 최대 5번의 틱에 걸쳐
            이상으로 보고되는 Plateau 현상을 _should_report() 디바운스로 방지한다.
            EventGraphTrigger(correlation_engine/event_graph_trigger.py)의
            should_build() 패턴과 동일.

        Eviction (축출):
            max_tracked_pairs 초과 시 LFU 기반 축출을 이 메서드 마지막에 실행.
            record_event(Hot Path)가 아닌 analyze_tick(Cold Path, 60초 주기)에서
            일괄 정리하여 이벤트 수집 레이턴시를 보호한다.
        """
        results: list[CorrelationResult] = []
        current_time = time.time()

        for pair_key, detector in self._pair_detectors.items():
            # 현재 윈도우 내 동시발생 카운트 계산
            count = self._count_co_occurrences_in_window(pair_key, current_time)
            is_anomalous, z_score = detector.is_anomaly(float(count))

            if is_anomalous and count >= self._min_co_occurrences:
                # 알람 디바운스: 동일 페어에 대해 윈도우 시간 내 재보고 방지
                if not self._should_report(pair_key, current_time):
                    continue

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

        # Cold Path 축출: record_event(Hot Path)가 아닌 여기서 일괄 정리
        self._evict_if_needed()

        return results

    def _should_report(self, pair_key: str, current_time: float) -> bool:
        """동일 페어의 이상 상태는 윈도우 시간 내 1회만 리포팅.

        EventGraphTrigger.should_build()와 동일한 디바운스 패턴.
        — correlation_engine/event_graph_trigger.py L62-76 참조
        — rate_limit_coordinator/coordinator.py L134-155 참조
        """
        last = self._last_report_times.get(pair_key, 0.0)
        if current_time - last < self._window_seconds:
            return False
        self._last_report_times[pair_key] = current_time
        return True
```

### 3.3 방향성 추론

```python
    def _infer_direction(self, pair: EventPairKey, time_gaps: list[float]) -> str | None:
        """시간 간격의 부호로 인과 방향 추론

        부호 규칙 (§3.1 _update_pair_count 참조):
            signed_gap = timestamp_A - timestamp_B
            음수 → A가 먼저 발생 → "a_causes_b"
            양수 → B가 먼저 발생 → "b_causes_a"

        Microsecond Collision 처리:
            비동기/멀티스레드 MSA 환경에서 두 이벤트가 동일한 밀리초에 도달하여
            signed_gap ≈ 0.0이 되는 경우가 발생한다.
            abs(gap) < simultaneous_threshold인 gap은 방향성 판단에서 제외하여
            방향성 편향(Bias)을 방지한다.
        """
        if not time_gaps or len(time_gaps) < 5:
            return None

        # Microsecond Collision: 동시 도착한 이벤트는 방향성 판단에서 제외
        # — 설정 가능: SELFHEALING_CORRELATION_SIMULTANEOUS_THRESHOLD_SECONDS
        meaningful_gaps = [
            g for g in time_gaps
            if abs(g) >= self._simultaneous_threshold
        ]
        if len(meaningful_gaps) < 5:
            return "mutual"  # 대부분 동시 도착 → 공통 원인 가능성

        # signed_gap < 0 → A가 먼저 → a_causes_b
        a_first_count = sum(1 for g in meaningful_gaps if g < 0)

        ratio = a_first_count / len(meaningful_gaps)
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

### 5.1 ZScoreDetector 직렬화 인터페이스

기존 `ZScoreDetector`(`predictive_forecaster/anomaly_detector.py`)에는
`to_dict()` / `from_dict()` 인터페이스가 없다.
`HoltLinearForecaster`는 `save_state(metric_name)` / `load_state(metric_name)`으로
`level`, `trend`, `count`, `warmup_samples`, `history`를 완전 복원하지만,
`ZScoreDetector`는 `_values` deque만 보유하며 영속화 인터페이스가 부재하다.

캡슐화를 유지하기 위해 `ZScoreDetector`에 `to_dict()` / `from_dict()` 를 추가한다:

```python
# predictive_forecaster/anomaly_detector.py — ZScoreDetector에 추가

    def to_dict(self) -> dict:
        """직렬화: 학습 상태를 dict로 내보내기."""
        return {
            "values": list(self._values),
            "threshold": self._threshold,
            "window": self._window,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ZScoreDetector:
        """역직렬화: dict에서 인스턴스 복원.

        주의: deque 생성 시 반드시 maxlen=window를 지정한다.
        maxlen이 누락되면 메모리 누수(Memory Leak)의 원인이 된다.
        HoltLinearForecaster.load_state()는 기존 deque를 clear() 후
        재적재하여 maxlen을 보존한다 — 동일한 패턴을 적용.
        (time_series.py L308-315 참조)
        """
        det = cls(threshold=data["threshold"], window=data["window"])
        # maxlen=window가 __init__에서 이미 설정된 deque에 extend
        det._values.extend(data["values"])
        return det
```

### 5.2 save_state / load_state

```python
    def save_state(self) -> bool:
        """학습된 상관관계 상태 영속화 (Cold Start 방지)

        ZScoreDetector: to_dict()로 직렬화 (§5.1)
        HoltLinearForecaster: save_state(key)로 개별 영속화
            — time_series.py L222-275 참조
        """
        state = {
            "pair_detectors": {
                key: det.to_dict()
                for key, det in self._pair_detectors.items()
            },
            "pair_time_gaps": {
                key: list(gaps) for key, gaps in self._pair_time_gaps.items()
            },
            "pair_time_gaps_maxlen": 100,  # deque maxlen 메타데이터
            "saved_at": time.time(),
        }
        success = StateBackend.set("correlation:co_occurrence_state", state)

        # HoltLinearForecaster는 자체 save_state() 메서드로 개별 저장
        for pair_key, forecaster in self._pair_forecasters.items():
            forecaster.save_state(f"correlation:trend:{pair_key}")

        return success

    def load_state(self) -> bool:
        """저장된 상태 복원 — deque maxlen 보존 필수

        주의: list → deque 변환 시 maxlen을 반드시 지정한다.
        maxlen이 풀려버리면 메모리 누수(Memory Leak)의 원인이 된다.
        HoltLinearForecaster.load_state()는 기존 deque를 clear() 후
        재적재하여 maxlen을 자연스럽게 보존한다.
        — time_series.py L290-315 참조
        """
        state = StateBackend.get("correlation:co_occurrence_state")
        if not state:
            return False

        # ZScoreDetector 복원: from_dict()로 maxlen 보존
        for key, det_data in state.get("pair_detectors", {}).items():
            self._pair_detectors[key] = ZScoreDetector.from_dict(det_data)

        # pair_time_gaps 복원: maxlen 메타데이터로 deque 재생성
        gaps_maxlen = state.get("pair_time_gaps_maxlen", 100)
        for key, gaps_list in state.get("pair_time_gaps", {}).items():
            restored = deque(maxlen=gaps_maxlen)
            restored.extend(gaps_list)
            self._pair_time_gaps[key] = restored

        # HoltLinearForecaster는 자체 load_state() 메서드로 개별 복원
        for pair_key in self._pair_detectors:
            if pair_key not in self._pair_forecasters:
                self._pair_forecasters[pair_key] = HoltLinearForecaster()
            self._pair_forecasters[pair_key].load_state(
                f"correlation:trend:{pair_key}"
            )

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
| `SELFHEALING_CORRELATION_SIMULTANEOUS_THRESHOLD_SECONDS` | `0.001` | 동시 도착 판정 임계값 (초). 이 미만의 시간 간격은 방향성 판단에서 제외 |

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
| **단위** | A가 항상 B보다 먼저 발생 (signed_gap < 0) | `direction="a_causes_b"` |
| **단위** | 양방향 동시발생 | `direction="mutual"` |
| **단위** | 두 이벤트 동시 도착 (gap < 0.001s) | meaningful_gaps 제외, `direction="mutual"` |
| **단위** | 디바운스: 동일 페어 300초 내 재보고 | 2번째 이상 보고가 억제됨 |
| **통합** | StateBackend save → 재시작 → load | 학습 상태 복원 + deque maxlen 보존 확인 |
| **통합** | ZScoreDetector.to_dict() → from_dict() | `_values` deque maxlen 유지 확인 |
| **통합** | HoltLinearForecaster 상태 포함 save/load | trend, level 복원 확인 |
| **성능** | 1000쌍 × 100 히스토리 | analyze_tick() ≤ 50ms (축출 O(N) 포함) |
| **경계값** | max_tracked_pairs 초과 | Cold Path 축출: analyze_tick에서 LFU 일괄 정리 |
| **동시성** | 멀티스레드 record_event 동시 호출 | deque 얕은 복사로 RuntimeError 미발생 확인 |

---

## 10. 구현 보완 사항

v1.0.0 설계 리뷰에서 도출된 6가지 보완 사항을 문서화한다.
각 항목은 기존 코드베이스의 실제 구현을 근거로 한다.

### 10.1 ZScoreDetector Zero-Inflation 안전성 확인

**결론**: 추가 조치 불필요 — 기존 방어 로직으로 완전 보호됨.

기존 `ZScoreDetector` (`predictive_forecaster/anomaly_detector.py` L75-106):

- `MIN_STD_DEV = 1e-10`: std_dev가 이 값 미만이면 `(False, 0.0)` 반환 → **ZeroDivisionError 원천 차단**
- 최소 샘플 수 체크: `len(self._values) < 3`이면 `(False, 0.0)` → **콜드 스타트 보호**

동시발생 카운트가 `[0, 0, ..., 0]`으로 쌓인 상태에서 첫 1회 발생 시 mean≈0, std_dev≈0이 되어
`MIN_STD_DEV` 가드에 걸리므로 안전하다. `min_co_occurrences: 3` 설정이 이 간극을 보완한다.

**추가 적용**: `_update_pair_count`에서 새 페어가 처음 등록되는 시점에
`logger.debug("First co-occurrence detected for pair: %s", key)` 로깅을 추가하여
사후 장애 분석에 활용한다 (§3.1 코드 반영 완료).

### 10.2 시간 간격 부호(Signed Gap)와 알파벳 정렬의 일관성

**결론**: 원본의 `abs()` 사용이 방향성 추론을 무효화하는 논리적 결함이 있어 수정함.

**문제**: `_update_pair_count`에서 `time_gap = abs(timestamp - closest_timestamp)` 계산 시
모든 gap이 양수 → `_infer_direction`의 a_first_count가 항상 100% → 항상 `"a_causes_b"` 판정.

**수정**: `_update_pair_count`에 `current_event_type` 파라미터를 추가하여
항상 `signed_gap = timestamp_A - timestamp_B`로 일관 계산:
- 음수 → A가 먼저 발생 → `"a_causes_b"`
- 양수 → B가 먼저 발생 → `"b_causes_a"`

**Microsecond Collision**: `abs(gap) < SIMULTANEOUS_THRESHOLD_SECONDS` (기본 0.001초)인 gap은
방향성 판단에서 제외하여 편향 방지. 설정 가능한 환경변수로 분리 (§6 참조).

### 10.3 deque 순회 Thread-Safety

**결론**: `list()` 얕은 복사를 1차 전략으로 적용.

`SelfHealingEventBus`는 동기 핸들러 호출 + 멀티스레드 환경이다
(`event_bus/bus/__init__.py` L414: `self._subscription_lock = threading.RLock()`).
`RedisEventBus` 리스너 스레드, Celery 워커 등이 동시에 `record_event`를 호출할 수 있다.

기존 코드의 선례:
- `CoOccurrenceSnapshot(frozen=True)` — Copy-on-Write 패턴 (`co_occurrence_tracker.py`)
- `SelfHealingEventBus.publish()` — `subscriptions = list(subscriptions)` 복사 후 순회 (`bus/__init__.py` L421)

`record_event`에서 `dict(self._event_timestamps)` + `list(other_timestamps)` 복사를 적용 (§3.1 반영 완료).

**성능 근거**: `max_event_buffer=500` 기준 float 500개 = ~4KB 복사로 GC 부하 미미.
Event Storm에서 P99 > 1ms 확인 시에만 `threading.RLock()` 짧은 critical section으로 전환 검토.

### 10.4 중복 카운팅과 Window Overlap Plateau

**결론**: 중복 카운팅은 의도적 — 빈도 신호 자체가 메트릭이므로 올바른 설계.

`_count_co_occurrences_in_window`는 매 틱마다 슬라이딩 윈도우로 fresh count를 재계산하므로,
60초 틱 간 누적 덧셈이 아닌 독립적 산출이다.

**Plateau 문제**: 300초 윈도우 + 60초 틱에서 1번의 스파이크가 최대 ⌈300/60⌉ = 5번 틱에 걸쳐 보고됨.
`_should_report()` 디바운스로 방지:
- `EventGraphTrigger.should_build()` (`correlation_engine/event_graph_trigger.py` L62-76) 패턴 차용
- `RateLimitCoordinator._should_emit_event()` (`rate_limit_coordinator/coordinator.py` L134-155) 패턴 차용
- 동일 페어의 이상 상태는 `window_seconds` (300초) 내 1회만 리포팅 (§3.2 반영 완료)

### 10.5 max_tracked_pairs 초과 시 축출 전략

**결론**: LFU 기반 2-Tier 축출을 `analyze_tick()` Cold Path에서 실행.

42개 EventType 기준 조합 수 = C(42,2) = 861로 기본 한도(1000) 내이나,
커스텀 이벤트 확장 시 초과 가능.

```python
    def _evict_if_needed(self) -> None:
        """max_tracked_pairs 초과 시 의미 없는 쌍 축출.

        Hot Path(record_event)가 아닌 Cold Path(analyze_tick, 60초 주기)에서 실행하여
        이벤트 수집 레이턴시를 보호한다.

        축출 전략 (2-Tier):
            Tier 1: min_co_occurrences 미만 + 가장 오래된 쌍 (LRU)
            Tier 2: 전체 중 최저 co-occurrence count 쌍 (LFU)

        O(N) 스캔이지만 N=1000 기준 ~0.5ms로 analyze_tick 50ms SLA의 1%.
        """
        if len(self._pair_detectors) <= self._max_pairs:
            return

        excess = len(self._pair_detectors) - self._max_pairs

        # 점수 기반 정렬: (co-occurrence 횟수, 마지막 gap 시각) 오름차순
        scored = sorted(
            self._pair_detectors.keys(),
            key=lambda k: (
                len(self._pair_time_gaps.get(k, deque())),
                max(self._pair_time_gaps.get(k, deque([0.0])), default=0.0),
            ),
        )

        # 하위 excess개 축출
        for key in scored[:excess]:
            del self._pair_detectors[key]
            self._pair_time_gaps.pop(key, None)
            self._pair_counts.pop(key, None)
            self._pair_forecasters.pop(key, None)
            self._last_report_times.pop(key, None)
            logger.debug("[CoOccurrenceTracker] Evicted pair: %s", key)
```

**일시적 초과 허용**: 틱 사이(60초) 동안 최대 ~126개의 새 쌍이 추가될 수 있으나
(새 EventType 3개 × 기존 42개), 추가 메모리 ~100KB로 무시 가능.

### 10.6 StateBackend 직렬화 인터페이스

**결론**: `ZScoreDetector`에 `to_dict()` / `from_dict()` 추가, `HoltLinearForecaster`는 기존 `save_state()`/`load_state()` 활용.

| 클래스 | 기존 인터페이스 | 보완 |
|--------|--------------|------|
| `ZScoreDetector` | 없음 | `to_dict()` / `from_dict(cls, data)` 추가 (§5.1) |
| `HoltLinearForecaster` | `save_state(metric_name)` / `load_state(metric_name)` — `level`, `trend`, `count`, `warmup_samples`, `history` 완전 복원 (`time_series.py` L222-315) | 그대로 사용 |

**deque maxlen 복원 주의**:
- `from_dict()`에서 `cls(threshold=..., window=...)` 호출 → `__init__`에서 `deque(maxlen=window)` 생성 → `extend()`로 데이터 주입
- `pair_time_gaps` 복원 시 `deque(maxlen=gaps_maxlen)` 명시적 지정
- `HoltLinearForecaster.load_state()`의 `self._history.clear()` + `append()` 패턴과 동일 (`time_series.py` L308-315)
- maxlen 누락 시 메모리 누수 → OOM Kill 위험
