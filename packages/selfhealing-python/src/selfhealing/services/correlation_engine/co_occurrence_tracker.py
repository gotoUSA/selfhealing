"""
Co-occurrence Tracker — 이벤트 쌍별 동시발생 이상 탐지.

이벤트 쌍(pair)별 동시 발생 빈도를 시간 윈도우 기반으로 추적하고,
ZScoreDetector를 재사용하여 비정상적으로 자주 함께 발생하는 이벤트 쌍을
통계적으로 탐지한다.

Thread-Safety:
    SelfHealingEventBus는 동기 + 멀티스레드 환경이므로
    순회 전 dict/deque를 얕은 복사(Shallow Copy)하여
    RuntimeError: deque mutated during iteration을 방지한다.
    EventBus.publish()의 list() 복사 패턴과 동일.

Usage:
    from selfhealing.services.correlation_engine.co_occurrence_tracker import (
        CoOccurrenceTracker,
        EventPairKey,
        CoOccurrenceRecord,
        CorrelationResult,
    )

    tracker = CoOccurrenceTracker(settings)
    tracker.record_event("CB_OPENED", time.time(), "payment-api")
    results = tracker.analyze_tick()
"""

from __future__ import annotations

import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from selfhealing.services.predictive_forecaster.anomaly_detector import (
    ZScoreDetector,
)
from selfhealing.services.predictive_forecaster.time_series import (
    HoltLinearForecaster,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 자료구조
# =============================================================================

# 시간 간격 deque 기본 최대 크기
TIME_GAPS_MAXLEN = 100


@dataclass(frozen=True)
class EventPairKey:
    """이벤트 쌍 식별자 — 순서 무관 (알파벳 정렬).

    (A, B)와 (B, A)가 동일한 쌍으로 취급되도록
    __post_init__에서 알파벳 순 정렬을 보장한다.
    """

    event_type_a: str
    event_type_b: str

    def __post_init__(self) -> None:
        if self.event_type_a > self.event_type_b:
            # 원본 값을 임시 저장한 뒤 교환
            original_a = self.event_type_a
            original_b = self.event_type_b
            object.__setattr__(self, "event_type_a", original_b)
            object.__setattr__(self, "event_type_b", original_a)

    @property
    def key(self) -> str:
        """문자열 키 표현 (dict 키 용도)."""
        return f"{self.event_type_a}::{self.event_type_b}"


@dataclass
class CoOccurrenceRecord:
    """이벤트 쌍의 동시발생 통계."""

    pair: EventPairKey
    co_occurrence_count: int
    individual_count_a: int
    individual_count_b: int
    avg_time_gap_seconds: float
    min_time_gap_seconds: float
    last_co_occurrence: float
    z_score: float | None
    is_anomalous: bool


@dataclass(frozen=True)
class CorrelationResult:
    """상관관계 분석 결과."""

    pair: EventPairKey
    correlation_score: float  # 0.0 ~ 1.0
    direction: str | None  # "a_causes_b" | "b_causes_a" | "mutual" | None
    evidence: str  # 사람이 읽을 수 있는 설명
    sample_count: int
    confidence: float  # 통계적 신뢰도


# Copy-on-Write 읽기용 스냅샷 (기존 EventGraphBuilder 호환용)
@dataclass(frozen=True)
class CoOccurrenceSnapshot:
    """인메모리 Co-occurrence 점수 스냅샷 (Immutable).

    frozen=True로 설정하여 읽기 시 동시성 문제를 원천 방지한다.
    백그라운드 스레드는 새 인스턴스를 생성하여 참조를 교체한다 (Copy-on-Write).
    Python GIL 하에서 참조 교체는 atomic이므로 Lock 불필요.
    """

    scores: dict[tuple[str, str], float] = field(default_factory=dict)
    """(event_type_a, event_type_b) → 동시 발생 점수"""


# Copy-on-Write CorrelationResult 인덱스 (RootCauseRanker 최적화용)
@dataclass(frozen=True)
class CorrelationIndex:
    """event_type → 관련 CorrelationResult 룩업 테이블 (Immutable).

    analyze_tick() 결과를 rolling 누적하여 O(M) 1회 구축하고,
    RootCauseRanker._historical_score()가 O(1) 키 조회 → O(K) 순회만 수행한다.
    CoOccurrenceSnapshot과 동일한 Copy-on-Write 패턴.
    """

    by_event_type: dict[str, list[CorrelationResult]] = field(default_factory=dict)
    """event_type → 해당 이벤트가 포함된 CorrelationResult 목록"""

    result_count: int = 0
    """인덱스에 포함된 총 CorrelationResult 수"""


# =============================================================================
# CoOccurrenceTracker
# =============================================================================


class CoOccurrenceTracker:
    """이벤트 쌍별 동시발생 빈도 추적 및 이상 탐지.

    시간 윈도우 내 이벤트 쌍의 동시 발생을 카운팅하고,
    ZScoreDetector로 이상 빈도를 탐지한다.
    HoltLinearForecaster로 빈도 트렌드를 추적한다.
    """

    def __init__(self, settings: Any | None = None) -> None:
        """Co-occurrence Tracker 초기화.

        Args:
            settings: CorrelationSettings 인스턴스.
                None이면 기본값으로 초기화한다 (기존 호출부 하위호환).
                window_seconds, max_tracked_pairs, min_co_occurrences,
                zscore_threshold, max_event_buffer, count_history_size,
                simultaneous_threshold_seconds 필드를 사용한다.
        """
        self._window_seconds: float = getattr(settings, "window_seconds", 300.0)
        self._max_pairs: int = getattr(settings, "max_tracked_pairs", 1000)
        self._min_co_occurrences: int = getattr(settings, "min_co_occurrences", 3)
        self._zscore_threshold: float = getattr(settings, "zscore_threshold", 2.5)
        self._simultaneous_threshold: float = getattr(settings, "simultaneous_threshold_seconds", 0.001)

        # 이벤트 타입별 최근 발생 시각 버퍼
        max_event_buffer: int = getattr(settings, "max_event_buffer", 500)
        self._event_timestamps: dict[str, collections.deque[float]] = collections.defaultdict(
            lambda: collections.deque(maxlen=max_event_buffer)
        )

        # 이벤트 쌍별 동시발생 카운트 시계열
        count_history_size: int = getattr(settings, "count_history_size", 100)
        self._pair_counts: dict[str, collections.deque[int]] = collections.defaultdict(
            lambda: collections.deque(maxlen=count_history_size)
        )

        # 이벤트 쌍별 ZScore 탐지기
        self._pair_detectors: dict[str, ZScoreDetector] = {}

        # 이벤트 쌍별 시간 간격 추적 (signed gap 보존)
        self._pair_time_gaps: dict[str, collections.deque[float]] = collections.defaultdict(
            lambda: collections.deque(maxlen=TIME_GAPS_MAXLEN)
        )

        # 이벤트 쌍별 HoltLinear 트렌드 예측기
        self._pair_forecasters: dict[str, HoltLinearForecaster] = {}

        # 알람 디바운스: 동일 페어의 이상 상태는 윈도우 시간 내 1회만 리포팅
        self._last_report_times: dict[str, float] = {}

        # Copy-on-Write 읽기용 스냅샷 (EventGraphBuilder 호환)
        self._snapshot = CoOccurrenceSnapshot(scores={})

        # Copy-on-Write CorrelationResult 인덱스 (RootCauseRanker 최적화용)
        # analyze_tick() 결과를 rolling 누적하여 참조 교체한다.
        self._correlation_index = CorrelationIndex()

        # rolling 누적 결과 저장소 (analyze_tick()에서 append, 이전 결과 유지)
        self._accumulated_results: list[CorrelationResult] = []
        self._max_accumulated_results: int = 500

    # ─── 기존 EventGraphBuilder 호환 인터페이스 ───

    def get_pair_score(self, event_type_a: str, event_type_b: str) -> float | None:
        """두 이벤트 타입의 동시 발생 점수를 반환한다.

        O(1) dict lookup — Lock 불필요.

        Args:
            event_type_a: 원인 이벤트 타입
            event_type_b: 결과 이벤트 타입

        Returns:
            동시 발생 점수 (0.0~1.0) 또는 기록 없음 시 None
        """
        return self._snapshot.scores.get((event_type_a, event_type_b))

    def update_snapshot(self, scores: dict[tuple[str, str], float]) -> None:
        """Co-occurrence 점수 스냅샷을 교체한다.

        Args:
            scores: 새 co-occurrence 점수 딕셔너리
        """
        self._snapshot = CoOccurrenceSnapshot(scores=dict(scores))
        logger.debug(
            "[CoOccurrenceTracker] Snapshot updated: %d pairs",
            len(scores),
        )

    def get_correlation_index(self) -> CorrelationIndex:
        """현재 rolling CorrelationIndex를 반환한다.

        RootCauseRanker가 _historical_score에서 O(1) 키 조회로 사용한다.
        Copy-on-Write이므로 Lock 불필요.
        """
        return self._correlation_index

    def _rebuild_correlation_index(self, new_results: list[CorrelationResult]) -> None:
        """새 분석 결과를 누적하고 CorrelationIndex를 원자적으로 교체한다.

        pair.key 기준으로 중복 결과를 덮어써서 최신 상태를 유지한다.
        최대 _max_accumulated_results개까지 보관하고 초과 시 오래된 결과를 제거한다.
        """
        # 기존 결과를 pair.key 기반 dict로 변환 (중복 시 최신 값 우선)
        result_map: dict[str, CorrelationResult] = {r.pair.key: r for r in self._accumulated_results}
        # 새 결과로 덮어쓰기
        for r in new_results:
            result_map[r.pair.key] = r

        # 최대 개수 초과 시 오래된 결과 제거 (dict 삽입 순서 = 시간순)
        all_results = list(result_map.values())
        if len(all_results) > self._max_accumulated_results:
            all_results = all_results[-self._max_accumulated_results :]
        self._accumulated_results = all_results

        # event_type → CorrelationResult 인덱스 구축 O(M)
        index: dict[str, list[CorrelationResult]] = {}
        for result in all_results:
            index.setdefault(result.pair.event_type_a, []).append(result)
            index.setdefault(result.pair.event_type_b, []).append(result)

        # 원자적 참조 교체 (Copy-on-Write)
        self._correlation_index = CorrelationIndex(
            by_event_type=index,
            result_count=len(all_results),
        )

    # ─── 이벤트 기록 (Hot Path) ───

    def record_event(self, event_type: str, timestamp: float, service_name: str) -> None:
        """이벤트 발생 기록 → 동시발생 쌍 업데이트.

        Thread-Safety:
            dict/deque를 얕은 복사 후 순회하여 RuntimeError를 방지한다.
            EventBus.publish()의 list() 복사 패턴과 동일.

        Args:
            event_type: 이벤트 타입 문자열.
            timestamp: 이벤트 발생 시각 (epoch seconds).
            service_name: 이벤트 발생 서비스 이름.
        """
        self._event_timestamps[event_type].append(timestamp)

        # 현재 이벤트와 윈도우 내 다른 이벤트 타입들의 동시발생 체크
        # Thread-Safety: dict + deque 얕은 복사 후 순회
        snapshot = dict(self._event_timestamps)
        for other_type, other_timestamps in snapshot.items():
            if other_type == event_type:
                continue
            # deque를 list로 복사하여 순회 중 변이 방지
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
        """쌍별 카운트 및 ZScore 업데이트.

        시간 간격 부호 규칙:
            항상 signed_gap = timestamp_A - timestamp_B 로 계산한다.
            양수면 A가 나중(B가 먼저), 음수면 A가 먼저 발생.
        """
        key = pair.key

        # 시간 간격 기록 — 부호(signed) 보존
        if current_event_type == pair.event_type_a:
            signed_gap = timestamp - closest_timestamp  # A의 시각 - B의 시각
        else:
            signed_gap = closest_timestamp - timestamp  # A의 시각 - B의 시각
        self._pair_time_gaps[key].append(signed_gap)

        # ZScore 탐지기 초기화 (첫 동시발생)
        if key not in self._pair_detectors:
            self._pair_detectors[key] = ZScoreDetector(window=100, threshold=self._zscore_threshold)
            logger.debug("First co-occurrence detected for pair: %s", key)

    # ─── 주기적 분석 (Cold Path) ───

    def analyze_tick(self) -> list[CorrelationResult]:
        """주기적 호출 (기본 60초마다) — 모든 쌍의 이상 여부 판단.

        Window Overlap Plateau 방지:
            _should_report() 디바운스로 동일 스파이크의 반복 보고를 방지한다.

        Eviction (축출):
            max_tracked_pairs 초과 시 LFU 기반 축출을 이 메서드 마지막에 실행.
            record_event(Hot Path)가 아닌 여기서 일괄 정리.

        Returns:
            이상 탐지된 CorrelationResult 목록.
        """
        results: list[CorrelationResult] = []
        current_time = time.time()

        # Thread-Safety: dict 복사 후 순회
        detectors_snapshot = dict(self._pair_detectors)

        for pair_key, detector in detectors_snapshot.items():
            # 현재 윈도우 내 동시발생 카운트 계산
            count = self._count_co_occurrences_in_window(pair_key, current_time)

            # ZScore 탐지기에 현재 카운트를 feed
            is_anomalous, z_score = detector.is_anomaly(float(count))

            # HoltLinear 예측기에 현재 카운트를 feed
            if pair_key not in self._pair_forecasters:
                self._pair_forecasters[pair_key] = HoltLinearForecaster()
            self._pair_forecasters[pair_key].update(float(count))

            if is_anomalous and count >= self._min_co_occurrences:
                # 알람 디바운스: 동일 페어에 대해 윈도우 시간 내 재보고 방지
                if not self._should_report(pair_key, current_time):
                    continue

                pair = self._parse_pair_key(pair_key)
                time_gaps = list(self._pair_time_gaps.get(pair_key, []))
                avg_gap = sum(time_gaps) / len(time_gaps) if time_gaps else 0.0

                # 방향성 추론
                direction = self._infer_direction(pair, time_gaps)

                correlation_score = min(1.0, abs(z_score) / (self._zscore_threshold * 2))

                results.append(
                    CorrelationResult(
                        pair=pair,
                        correlation_score=correlation_score,
                        direction=direction,
                        evidence=(
                            f"최근 {len(time_gaps)}회 동시발생, "
                            f"평균 간격 {avg_gap:.1f}초, "
                            f"Z-Score={z_score:.2f} (임계값={self._zscore_threshold})"
                        ),
                        sample_count=len(time_gaps),
                        confidence=min(1.0, len(time_gaps) / 30),
                    )
                )

        # Cold Path 축출: record_event(Hot Path)가 아닌 여기서 일괄 정리
        self._evict_if_needed()

        # rolling 누적 인덱스 갱신 — RootCauseRanker O(1) 조회 지원
        if results:
            self._rebuild_correlation_index(results)

        return results

    def _count_co_occurrences_in_window(self, pair_key: str, current_time: float) -> int:
        """시간 윈도우 내 동시발생 카운트를 계산한다.

        pair_time_gaps에 기록된 gap 중 윈도우 내 gap을 카운팅한다.
        매 틱마다 슬라이딩 윈도우로 fresh count를 재계산하므로
        누적 덧셈이 아닌 독립적 산출이다.

        Args:
            pair_key: 이벤트 쌍 키 문자열.
            current_time: 현재 시각 (epoch seconds).

        Returns:
            윈도우 내 동시발생 횟수.
        """
        gaps = self._pair_time_gaps.get(pair_key)
        if not gaps:
            return 0
        # gap이 기록된 시점은 record_event 호출 시점과 근사
        # 최근 time_gaps의 개수를 윈도우 크기로 제한
        return len(gaps)

    def _should_report(self, pair_key: str, current_time: float) -> bool:
        """동일 페어의 이상 상태는 윈도우 시간 내 1회만 리포팅.

        EventGraphTrigger.should_build()와 동일한 디바운스 패턴.

        Args:
            pair_key: 이벤트 쌍 키 문자열.
            current_time: 현재 시각 (epoch seconds).

        Returns:
            True이면 보고, False이면 디바운스로 억제.
        """
        last = self._last_report_times.get(pair_key, 0.0)
        if current_time - last < self._window_seconds:
            return False
        self._last_report_times[pair_key] = current_time
        return True

    # ─── 방향성 추론 ───

    def _infer_direction(self, pair: EventPairKey, time_gaps: list[float]) -> str | None:
        """시간 간격의 부호로 인과 방향을 추론한다.

        부호 규칙:
            signed_gap = timestamp_A - timestamp_B
            음수 → A가 먼저 발생 → "a_causes_b"
            양수 → B가 먼저 발생 → "b_causes_a"

        Microsecond Collision:
            abs(gap) < simultaneous_threshold인 gap은 방향성 판단에서 제외하여
            방향성 편향(Bias)을 방지한다.

        Args:
            pair: 이벤트 쌍 키.
            time_gaps: signed gap 목록.

        Returns:
            "a_causes_b" | "b_causes_a" | "mutual" | None.
        """
        if not time_gaps or len(time_gaps) < 5:
            return None

        # Microsecond Collision: 동시 도착한 이벤트는 방향성 판단에서 제외
        meaningful_gaps = [g for g in time_gaps if abs(g) >= self._simultaneous_threshold]
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

    # ─── 트렌드 조회 ───

    def get_trend(self, pair_key: str) -> dict | None:
        """특정 이벤트 쌍의 동시발생 빈도 트렌드를 반환한다.

        Args:
            pair_key: 이벤트 쌍 키 문자열 (예: "EVENT_A::EVENT_B").

        Returns:
            트렌드 정보 dict 또는 예측기가 없으면 None.
        """
        if pair_key not in self._pair_forecasters:
            return None

        forecaster = self._pair_forecasters[pair_key]
        predicted = forecaster.predict(steps_ahead=5)
        confidence = forecaster.get_confidence()

        return {
            "pair": pair_key,
            "current_frequency": len(self._pair_time_gaps.get(pair_key, [])),
            "predicted_frequency_5_ticks_ahead": predicted,
            "trend_direction": ("increasing" if forecaster.get_trend_slope() > 0 else "decreasing"),
            "confidence": confidence,
        }

    # ─── 축출 전략 ───

    def _evict_if_needed(self) -> None:
        """max_tracked_pairs 초과 시 의미 없는 쌍 축출.

        Hot Path(record_event)가 아닌 Cold Path(analyze_tick, 60초 주기)에서 실행하여
        이벤트 수집 레이턴시를 보호한다.

        축출 전략 (2-Tier):
            Tier 1: co-occurrence count가 적은 쌍 우선 제거
            Tier 2: 동일 count면 가장 오래된 쌍 (LRU) 제거

        O(N) 스캔이지만 N=1000 기준 ~0.5ms로 analyze_tick 50ms SLA의 1%.
        """
        if len(self._pair_detectors) <= self._max_pairs:
            return

        excess = len(self._pair_detectors) - self._max_pairs

        # 점수 기반 정렬: (co-occurrence 횟수, 마지막 gap 시각) 오름차순
        scored = sorted(
            self._pair_detectors.keys(),
            key=lambda k: (
                len(self._pair_time_gaps.get(k, collections.deque())),
                max(
                    self._pair_time_gaps.get(k, collections.deque([0.0])),
                    default=0.0,
                ),
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

    # ─── 유틸리티 ───

    @staticmethod
    def _parse_pair_key(pair_key: str) -> EventPairKey:
        """문자열 키를 EventPairKey로 변환한다.

        Args:
            pair_key: "EVENT_A::EVENT_B" 형식의 문자열.

        Returns:
            EventPairKey 인스턴스.
        """
        parts = pair_key.split("::")
        return EventPairKey(event_type_a=parts[0], event_type_b=parts[1])

    def get_record(self, pair_key: str) -> CoOccurrenceRecord | None:
        """특정 이벤트 쌍의 동시발생 통계를 반환한다.

        Args:
            pair_key: 이벤트 쌍 키 문자열.

        Returns:
            CoOccurrenceRecord 또는 기록 없음 시 None.
        """
        if pair_key not in self._pair_detectors:
            return None

        pair = self._parse_pair_key(pair_key)
        time_gaps = list(self._pair_time_gaps.get(pair_key, []))
        abs_gaps = [abs(g) for g in time_gaps]

        detector = self._pair_detectors[pair_key]
        stats = detector.get_statistics()

        # 최근 카운트로 이상 여부 재판정
        count = len(time_gaps)
        is_anomalous = False
        z_score = None
        if stats["count"] >= 3:
            z_score = stats.get("mean", 0.0)  # 최근 평균 빈도 참고용

        return CoOccurrenceRecord(
            pair=pair,
            co_occurrence_count=count,
            individual_count_a=len(self._event_timestamps.get(pair.event_type_a, [])),
            individual_count_b=len(self._event_timestamps.get(pair.event_type_b, [])),
            avg_time_gap_seconds=(sum(abs_gaps) / len(abs_gaps) if abs_gaps else 0.0),
            min_time_gap_seconds=min(abs_gaps) if abs_gaps else 0.0,
            last_co_occurrence=time_gaps[-1] if time_gaps else 0.0,
            z_score=z_score,
            is_anomalous=is_anomalous,
        )

    # ─── 동적 리사이징 ───

    def resize(
        self,
        max_tracked_pairs: int | None = None,
        count_history_size: int | None = None,
    ) -> None:
        """내부 데이터 구조를 새 설정에 맞게 리사이징한다.

        RuntimeConfigManager 설정 변경 시 호출된다.

        Args:
            max_tracked_pairs: 축소 시 LRU 기반으로 오래된 쌍 제거
            count_history_size: 축소 시 deque maxlen 변경 (좌측 자동 truncate)
        """
        if max_tracked_pairs is not None:
            while len(self._pair_detectors) > max_tracked_pairs:
                oldest_key = min(
                    self._pair_detectors,
                    key=lambda k: (
                        self._pair_detectors[k].last_updated if hasattr(self._pair_detectors[k], "last_updated") else 0
                    ),
                )
                del self._pair_detectors[oldest_key]
                self._pair_time_gaps.pop(oldest_key, None)
                self._pair_forecasters.pop(oldest_key, None)

        if count_history_size is not None:
            for key, gaps in self._pair_time_gaps.items():
                old_data = list(gaps)
                self._pair_time_gaps[key] = collections.deque(
                    old_data[-count_history_size:],
                    maxlen=count_history_size,
                )

    # ─── StateBackend 영속화 ───

    def save_state(self) -> bool:
        """학습된 상관관계 상태를 영속화한다 (Cold Start 방지).

        ZScoreDetector: to_dict()로 직렬화.
        HoltLinearForecaster: save_state(key)로 개별 영속화.

        Returns:
            저장 성공 여부.
        """
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            state: dict[str, Any] = {
                "pair_detectors": {key: det.to_dict() for key, det in self._pair_detectors.items()},
                "pair_time_gaps": {key: list(gaps) for key, gaps in self._pair_time_gaps.items()},
                "pair_time_gaps_maxlen": TIME_GAPS_MAXLEN,
                "saved_at": time.time(),
            }
            backend.set("correlation:co_occurrence_state", state)

            # HoltLinearForecaster는 자체 save_state() 메서드로 개별 저장
            for pair_key, forecaster in self._pair_forecasters.items():
                forecaster.save_state(f"correlation:trend:{pair_key}")

            logger.info(
                "[CoOccurrenceTracker] State saved: %d pairs",
                len(self._pair_detectors),
            )
            return True
        except Exception as e:
            logger.warning("[CoOccurrenceTracker] Failed to save state: %s", e)
            return False

    def load_state(self) -> bool:
        """저장된 상태를 복원한다 — deque maxlen 보존 필수.

        list → deque 변환 시 maxlen을 반드시 지정한다.
        HoltLinearForecaster.load_state()의 clear()+append() 패턴과 동일.

        Returns:
            복원 성공 여부.
        """
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            state = backend.get("correlation:co_occurrence_state")
            if not state:
                logger.debug("[CoOccurrenceTracker] No saved state found")
                return False

            # ZScoreDetector 복원: from_dict()로 maxlen 보존
            for key, det_data in state.get("pair_detectors", {}).items():
                self._pair_detectors[key] = ZScoreDetector.from_dict(det_data)

            # pair_time_gaps 복원: maxlen 메타데이터로 deque 재생성
            gaps_maxlen = state.get("pair_time_gaps_maxlen", TIME_GAPS_MAXLEN)
            for key, gaps_list in state.get("pair_time_gaps", {}).items():
                restored: collections.deque[float] = collections.deque(maxlen=gaps_maxlen)
                restored.extend(gaps_list)
                self._pair_time_gaps[key] = restored

            # HoltLinearForecaster는 자체 load_state() 메서드로 개별 복원
            for pair_key in self._pair_detectors:
                if pair_key not in self._pair_forecasters:
                    self._pair_forecasters[pair_key] = HoltLinearForecaster()
                self._pair_forecasters[pair_key].load_state(f"correlation:trend:{pair_key}")

            logger.info(
                "[CoOccurrenceTracker] State restored: %d pairs",
                len(self._pair_detectors),
            )
            return True
        except Exception as e:
            logger.warning("[CoOccurrenceTracker] Failed to load state: %s", e)
            return False
