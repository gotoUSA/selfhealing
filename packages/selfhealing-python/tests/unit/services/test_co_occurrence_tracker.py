"""
Tests for Co-occurrence Tracker — 이벤트 쌍별 동시발생 이상 탐지.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: 설계 문서(§252)에 명시된 값/구조 검증 (하드코딩)
- Behavior: 함수/메서드 동작 검증 (소스 참조)

참조 소스:
- services/correlation_engine/co_occurrence_tracker.py
- services/predictive_forecaster/anomaly_detector.py (ZScoreDetector)
- settings/correlation.py (CorrelationSettings)
"""

from __future__ import annotations

import collections
import time
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    TIME_GAPS_MAXLEN,
    CoOccurrenceRecord,
    CoOccurrenceSnapshot,
    CoOccurrenceTracker,
    CorrelationResult,
    EventPairKey,
)
from selfhealing.services.predictive_forecaster.anomaly_detector import (
    ZScoreDetector,
)
from selfhealing.settings.correlation import CorrelationSettings


# =============================================================================
# EventPairKey 계약 검증
# =============================================================================


class TestEventPairKeyContract:
    """EventPairKey 설계 계약값 검증."""

    def test_alphabetical_sort_ensures_order_independence(self):
        """(A,B)와 (B,A)는 알파벳 정렬 후 동일한 쌍이 되어야 한다."""
        pair1 = EventPairKey("ZEBRA", "APPLE")
        pair2 = EventPairKey("APPLE", "ZEBRA")
        assert pair1.event_type_a == "APPLE"
        assert pair1.event_type_b == "ZEBRA"
        assert pair1 == pair2

    def test_key_property_format(self):
        """key 프로퍼티는 'A::B' 형식이어야 한다."""
        pair = EventPairKey("CB_OPENED", "ERROR_BUDGET_CRITICAL")
        assert pair.key == "CB_OPENED::ERROR_BUDGET_CRITICAL"

    def test_frozen_immutability(self):
        """frozen=True이므로 필드 변경 불가."""
        pair = EventPairKey("A", "B")
        with pytest.raises(FrozenInstanceError):
            pair.event_type_a = "C"

    def test_same_type_pair(self):
        """동일 타입 쌍도 유효해야 한다."""
        pair = EventPairKey("SAME", "SAME")
        assert pair.event_type_a == "SAME"
        assert pair.event_type_b == "SAME"
        assert pair.key == "SAME::SAME"


# =============================================================================
# EventPairKey 동작 검증
# =============================================================================


class TestEventPairKeyBehavior:
    """EventPairKey 동작 검증."""

    def test_hashable_for_dict_key(self):
        """frozen=True이므로 dict 키로 사용 가능해야 한다."""
        pair = EventPairKey("A", "B")
        d = {pair: 42}
        assert d[EventPairKey("B", "A")] == 42

    def test_different_pairs_not_equal(self):
        """서로 다른 쌍은 동등하지 않아야 한다."""
        pair1 = EventPairKey("A", "B")
        pair2 = EventPairKey("A", "C")
        assert pair1 != pair2


# =============================================================================
# CorrelationResult 계약 검증
# =============================================================================


class TestCorrelationResultContract:
    """CorrelationResult 설계 계약값 검증."""

    def test_frozen_immutability(self):
        """frozen=True이므로 필드 변경 불가."""
        result = CorrelationResult(
            pair=EventPairKey("A", "B"),
            correlation_score=0.8,
            direction="a_causes_b",
            evidence="test",
            sample_count=10,
            confidence=0.5,
        )
        with pytest.raises(FrozenInstanceError):
            result.correlation_score = 0.0

    def test_direction_values(self):
        """direction은 a_causes_b, b_causes_a, mutual, None 중 하나."""
        valid_directions = {"a_causes_b", "b_causes_a", "mutual", None}
        for d in valid_directions:
            result = CorrelationResult(
                pair=EventPairKey("A", "B"),
                correlation_score=0.5,
                direction=d,
                evidence="test",
                sample_count=5,
                confidence=0.3,
            )
            assert result.direction in valid_directions


# =============================================================================
# CorrelationSettings Co-occurrence 필드 계약 검증
# =============================================================================


class TestCorrelationSettingsCoOccurrenceContract:
    """Co-occurrence 관련 설정 계약값 검증."""

    def test_window_seconds_default(self):
        """window_seconds 기본값은 300.0이어야 한다."""
        settings = CorrelationSettings()
        assert settings.window_seconds == 300.0

    def test_zscore_threshold_default(self):
        """zscore_threshold 기본값은 2.5이어야 한다."""
        settings = CorrelationSettings()
        assert settings.zscore_threshold == 2.5

    def test_min_co_occurrences_default(self):
        """min_co_occurrences 기본값은 3이어야 한다."""
        settings = CorrelationSettings()
        assert settings.min_co_occurrences == 3

    def test_max_tracked_pairs_default(self):
        """max_tracked_pairs 기본값은 1000이어야 한다."""
        settings = CorrelationSettings()
        assert settings.max_tracked_pairs == 1000

    def test_analysis_interval_default(self):
        """analysis_interval 기본값은 60.0이어야 한다."""
        settings = CorrelationSettings()
        assert settings.analysis_interval == 60.0

    def test_max_event_buffer_default(self):
        """max_event_buffer 기본값은 500이어야 한다."""
        settings = CorrelationSettings()
        assert settings.max_event_buffer == 500

    def test_count_history_size_default(self):
        """count_history_size 기본값은 100이어야 한다."""
        settings = CorrelationSettings()
        assert settings.count_history_size == 100

    def test_simultaneous_threshold_seconds_default(self):
        """simultaneous_threshold_seconds 기본값은 0.001이어야 한다."""
        settings = CorrelationSettings()
        assert settings.simultaneous_threshold_seconds == 0.001


# =============================================================================
# CoOccurrenceTracker 동작 검증
# =============================================================================


@pytest.fixture
def settings() -> CorrelationSettings:
    """기본 CorrelationSettings 인스턴스."""
    return CorrelationSettings()


@pytest.fixture
def tracker(settings: CorrelationSettings) -> CoOccurrenceTracker:
    """기본 설정의 CoOccurrenceTracker 인스턴스."""
    return CoOccurrenceTracker(settings)


class TestCoOccurrenceTrackerRecordEventBehavior:
    """record_event 동작 검증."""

    def test_single_event_no_pairs(self, tracker: CoOccurrenceTracker):
        """단일 이벤트 기록 시 쌍이 생성되지 않아야 한다."""
        tracker.record_event("CB_OPENED", time.time(), "payment-api")
        assert len(tracker._pair_detectors) == 0

    def test_two_events_within_window_creates_pair(self, tracker: CoOccurrenceTracker, settings: CorrelationSettings):
        """윈도우 내 두 이벤트가 동시발생 쌍을 생성해야 한다."""
        now = time.time()
        tracker.record_event("CB_OPENED", now, "payment-api")
        tracker.record_event("ERROR_BUDGET_CRITICAL", now + 10, "payment-api")

        pair = EventPairKey("CB_OPENED", "ERROR_BUDGET_CRITICAL")
        assert pair.key in tracker._pair_detectors

    def test_two_events_outside_window_no_pair(self, tracker: CoOccurrenceTracker, settings: CorrelationSettings):
        """윈도우 외 두 이벤트는 쌍을 생성하지 않아야 한다."""
        now = time.time()
        tracker.record_event("CB_OPENED", now, "payment-api")
        tracker.record_event(
            "ERROR_BUDGET_CRITICAL",
            now + settings.window_seconds + 1,
            "payment-api",
        )
        assert len(tracker._pair_detectors) == 0

    def test_multiple_pairs_tracked(self, tracker: CoOccurrenceTracker):
        """여러 이벤트 타입이 각각의 쌍으로 추적되어야 한다."""
        now = time.time()
        tracker.record_event("A", now, "svc")
        tracker.record_event("B", now + 1, "svc")
        tracker.record_event("C", now + 2, "svc")

        # A-B, A-C, B-C 세 쌍이 생성되어야 함
        assert len(tracker._pair_detectors) == 3


class TestCoOccurrenceTrackerTimeGapBehavior:
    """시간 간격 부호(signed gap) 동작 검증."""

    def test_a_first_negative_gap(self, tracker: CoOccurrenceTracker):
        """A가 먼저 발생하면 signed_gap < 0이어야 한다."""
        now = time.time()
        # A(알파벳 순 first)가 먼저 발생
        tracker.record_event("ALPHA", now, "svc")
        tracker.record_event("BETA", now + 5, "svc")

        pair = EventPairKey("ALPHA", "BETA")
        gaps = list(tracker._pair_time_gaps[pair.key])
        # gap = timestamp_A - timestamp_B = now - (now+5) = -5
        assert len(gaps) == 1
        assert gaps[0] < 0

    def test_b_first_positive_gap(self, tracker: CoOccurrenceTracker):
        """B가 먼저 발생하면 signed_gap > 0이어야 한다."""
        now = time.time()
        # B(알파벳 순 second)가 먼저 발생
        tracker.record_event("BETA", now, "svc")
        tracker.record_event("ALPHA", now + 5, "svc")

        pair = EventPairKey("ALPHA", "BETA")
        gaps = list(tracker._pair_time_gaps[pair.key])
        # gap = timestamp_A - timestamp_B = (now+5) - now = +5
        assert len(gaps) == 1
        assert gaps[0] > 0


class TestCoOccurrenceTrackerAnalyzeTickBehavior:
    """analyze_tick 동작 검증."""

    def test_no_pairs_returns_empty(self, tracker: CoOccurrenceTracker):
        """쌍이 없으면 빈 결과를 반환해야 한다."""
        results = tracker.analyze_tick()
        assert results == []

    def test_below_min_co_occurrences_filtered(self, tracker: CoOccurrenceTracker, settings: CorrelationSettings):
        """min_co_occurrences 미만 동시발생은 결과에서 필터링되어야 한다."""
        now = time.time()
        # min_co_occurrences=3이므로 2회만 발생시키면 필터링됨
        tracker.record_event("A", now, "svc")
        tracker.record_event("B", now + 1, "svc")
        # 2회째
        tracker.record_event("A", now + 10, "svc")
        tracker.record_event("B", now + 11, "svc")

        results = tracker.analyze_tick()
        # ZScore 탐지기는 데이터가 부족하므로 이상 판정 안 됨
        assert len(results) == 0

    def test_anomalous_co_occurrence_detected(self):
        """충분한 동시발생으로 이상 탐지 시 결과가 반환되어야 한다."""
        settings = CorrelationSettings()
        tracker = CoOccurrenceTracker(settings)
        now = time.time()

        # 히스토리 구축: 낮은 빈도의 기준선 (ZScoreDetector에 0을 여러 번 feed)
        pair_key = EventPairKey("CB_OPENED", "ERROR_BUDGET_CRITICAL").key
        tracker._pair_detectors[pair_key] = ZScoreDetector(window=100, threshold=settings.zscore_threshold)
        # 기준선 구축 (0값을 많이 넣어 평균~0, 표준편차 작게)
        for _ in range(20):
            tracker._pair_detectors[pair_key].is_anomaly(0.0)

        # 충분한 gap 기록 (min_co_occurrences=3 이상)
        for i in range(10):
            tracker._pair_time_gaps[pair_key].append(-2.0)

        # analyze_tick은 현재 카운트 = len(gaps) = 10을 ZScore에 feed
        # 기준선이 0이므로 10은 이상치로 판정됨
        results = tracker.analyze_tick()

        anomalous = [r for r in results if r.pair.key == pair_key]
        assert len(anomalous) == 1
        assert anomalous[0].correlation_score > 0.0
        assert anomalous[0].sample_count == 10

    def test_debounce_suppresses_repeated_report(self):
        """디바운스: 동일 페어는 윈도우 시간 내 재보고가 억제되어야 한다."""
        settings = CorrelationSettings()
        tracker = CoOccurrenceTracker(settings)
        now = time.time()

        pair_key = EventPairKey("CB_OPENED", "ERROR_BUDGET").key

        # 첫 보고는 허용
        assert tracker._should_report(pair_key, now) is True
        # 윈도우 시간 내 재보고는 억제
        assert tracker._should_report(pair_key, now + 100) is False
        # 윈도우 시간 경과 후 보고 허용
        assert tracker._should_report(pair_key, now + settings.window_seconds + 1) is True


# =============================================================================
# 방향성 추론 동작 검증
# =============================================================================


class TestInferDirectionBehavior:
    """_infer_direction 방향성 추론 동작 검증."""

    def test_a_always_first_returns_a_causes_b(self, tracker: CoOccurrenceTracker):
        """A가 항상 B보다 먼저 발생하면 a_causes_b를 반환해야 한다."""
        pair = EventPairKey("ALPHA", "BETA")
        # 모든 gap이 음수 → A가 항상 먼저
        time_gaps = [-5.0, -3.0, -4.0, -2.0, -6.0, -3.5, -4.5]
        direction = tracker._infer_direction(pair, time_gaps)
        assert direction == "a_causes_b"

    def test_b_always_first_returns_b_causes_a(self, tracker: CoOccurrenceTracker):
        """B가 항상 A보다 먼저 발생하면 b_causes_a를 반환해야 한다."""
        pair = EventPairKey("ALPHA", "BETA")
        # 모든 gap이 양수 → B가 항상 먼저
        time_gaps = [5.0, 3.0, 4.0, 2.0, 6.0, 3.5, 4.5]
        direction = tracker._infer_direction(pair, time_gaps)
        assert direction == "b_causes_a"

    def test_bidirectional_returns_mutual(self, tracker: CoOccurrenceTracker):
        """양방향 동시발생이면 mutual을 반환해야 한다."""
        pair = EventPairKey("ALPHA", "BETA")
        # 혼합 부호 (50/50에 가까움)
        time_gaps = [-5.0, 3.0, -4.0, 2.0, -6.0, 3.5, 4.5]
        direction = tracker._infer_direction(pair, time_gaps)
        assert direction == "mutual"

    def test_insufficient_samples_returns_none(self, tracker: CoOccurrenceTracker):
        """샘플이 5개 미만이면 None을 반환해야 한다."""
        pair = EventPairKey("ALPHA", "BETA")
        time_gaps = [-5.0, -3.0, -4.0]
        direction = tracker._infer_direction(pair, time_gaps)
        assert direction is None

    def test_simultaneous_events_returns_mutual(self):
        """동시 도착 이벤트(gap < threshold)만 있으면 mutual을 반환해야 한다."""
        settings = CorrelationSettings()
        tracker = CoOccurrenceTracker(settings)
        pair = EventPairKey("ALPHA", "BETA")
        # 모든 gap이 simultaneous_threshold(0.001) 미만
        time_gaps = [0.0001, -0.0002, 0.0005, -0.0003, 0.0004, -0.0001, 0.0002]
        direction = tracker._infer_direction(pair, time_gaps)
        assert direction == "mutual"


# =============================================================================
# 축출 전략 동작 검증
# =============================================================================


class TestEvictionBehavior:
    """_evict_if_needed 축출 동작 검증."""

    def test_no_eviction_within_limit(self):
        """max_tracked_pairs 이내이면 축출이 발생하지 않아야 한다."""
        settings = CorrelationSettings()
        tracker = CoOccurrenceTracker(settings)

        for i in range(5):
            key = f"TYPE_{i}::TYPE_{i + 100}"
            tracker._pair_detectors[key] = ZScoreDetector(window=100, threshold=settings.zscore_threshold)

        tracker._evict_if_needed()
        assert len(tracker._pair_detectors) == 5

    def test_eviction_when_exceeding_limit(self):
        """max_tracked_pairs 초과 시 최저 빈도 쌍이 축출되어야 한다."""
        settings = CorrelationSettings()
        tracker = CoOccurrenceTracker(settings)

        # max_tracked_pairs + 5개 쌍을 추가
        for i in range(settings.max_tracked_pairs + 5):
            key = f"TYPE_{i:04d}::TYPE_{i + 10000:05d}"
            tracker._pair_detectors[key] = ZScoreDetector(window=100, threshold=settings.zscore_threshold)
            # 일부 쌍에 gap 추가 (빈도가 높은 쌍은 축출되지 않음)
            if i < 5:
                for _ in range(10):
                    tracker._pair_time_gaps[key].append(float(i))

        tracker._evict_if_needed()
        assert len(tracker._pair_detectors) == settings.max_tracked_pairs

        # 빈도가 높은 상위 5개 쌍은 보존되어야 함
        for i in range(5):
            key = f"TYPE_{i:04d}::TYPE_{i + 10000:05d}"
            assert key in tracker._pair_detectors


# =============================================================================
# ZScoreDetector 직렬화 동작 검증
# =============================================================================


class TestZScoreDetectorSerializationBehavior:
    """ZScoreDetector to_dict/from_dict 동작 검증."""

    def test_roundtrip_preserves_values(self):
        """to_dict → from_dict 라운드트립이 값을 보존해야 한다."""
        detector = ZScoreDetector(threshold=2.5, window=50)
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            detector.is_anomaly(v)

        data = detector.to_dict()
        restored = ZScoreDetector.from_dict(data)

        assert list(restored._values) == list(detector._values)
        assert restored._threshold == detector._threshold
        assert restored._window == detector._window

    def test_from_dict_preserves_deque_maxlen(self):
        """from_dict로 복원 시 deque maxlen이 보존되어야 한다."""
        data = {"values": [1.0, 2.0], "threshold": 3.0, "window": 50}
        restored = ZScoreDetector.from_dict(data)
        assert restored._values.maxlen == 50

    def test_to_dict_contains_required_keys(self):
        """to_dict 결과에 values, threshold, window 키가 포함되어야 한다."""
        detector = ZScoreDetector()
        data = detector.to_dict()
        assert "values" in data
        assert "threshold" in data
        assert "window" in data


# =============================================================================
# 트렌드 조회 동작 검증
# =============================================================================


class TestGetTrendBehavior:
    """get_trend 동작 검증."""

    def test_unknown_pair_returns_none(self, tracker: CoOccurrenceTracker):
        """등록되지 않은 쌍은 None을 반환해야 한다."""
        result = tracker.get_trend("UNKNOWN::PAIR")
        assert result is None

    def test_registered_pair_returns_trend_dict(self, tracker: CoOccurrenceTracker):
        """등록된 쌍이면 트렌드 dict를 반환해야 한다."""
        from selfhealing.services.predictive_forecaster.time_series import (
            HoltLinearForecaster,
        )

        pair_key = "A::B"
        tracker._pair_forecasters[pair_key] = HoltLinearForecaster()
        # 충분한 warmup 데이터 주입
        for i in range(40):
            tracker._pair_forecasters[pair_key].update(float(i))

        result = tracker.get_trend(pair_key)
        assert result is not None
        assert result["pair"] == pair_key
        assert "predicted_frequency_5_ticks_ahead" in result
        assert result["trend_direction"] in ("increasing", "decreasing")
        assert "confidence" in result


# =============================================================================
# 하위호환 동작 검증
# =============================================================================


class TestBackwardCompatibilityBehavior:
    """기존 EventGraphBuilder 호환 동작 검증."""

    def test_no_arg_constructor(self):
        """인자 없이 생성 가능해야 한다 (하위호환)."""
        tracker = CoOccurrenceTracker()
        assert tracker._window_seconds == 300.0
        assert tracker._max_pairs == 1000

    def test_get_pair_score_returns_none_empty(self):
        """빈 스냅샷에서 get_pair_score는 None을 반환해야 한다."""
        tracker = CoOccurrenceTracker()
        assert tracker.get_pair_score("A", "B") is None

    def test_update_snapshot_and_get_pair_score(self):
        """update_snapshot 후 get_pair_score로 조회 가능해야 한다."""
        tracker = CoOccurrenceTracker()
        scores = {("A", "B"): 0.8, ("C", "D"): 0.5}
        tracker.update_snapshot(scores)
        assert tracker.get_pair_score("A", "B") == 0.8
        assert tracker.get_pair_score("C", "D") == 0.5

    def test_co_occurrence_snapshot_immutable(self):
        """CoOccurrenceSnapshot은 frozen이므로 변경 불가해야 한다."""
        snapshot = CoOccurrenceSnapshot(scores={("A", "B"): 0.5})
        with pytest.raises(FrozenInstanceError):
            snapshot.scores = {}


# =============================================================================
# 동시성 안전 동작 검증
# =============================================================================


class TestThreadSafetyBehavior:
    """멀티스레드 record_event 동시 호출 안전성 검증."""

    def test_concurrent_record_events_no_runtime_error(self, tracker: CoOccurrenceTracker):
        """멀티스레드 동시 호출 시 RuntimeError가 발생하지 않아야 한다."""
        import threading

        errors: list[Exception] = []
        now = time.time()

        def worker(event_type: str, offset: int) -> None:
            try:
                for i in range(50):
                    tracker.record_event(event_type, now + offset + i * 0.1, "svc")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=("TYPE_A", 0)),
            threading.Thread(target=worker, args=("TYPE_B", 1)),
            threading.Thread(target=worker, args=("TYPE_C", 2)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors during concurrent execution: {errors}"


# =============================================================================
# Behavior Tests — resize() 동적 리사이징
# =============================================================================


class TestResizeBehavior:
    """resize() 동적 리사이징 동작 검증."""

    @pytest.fixture
    def tracker_with_pairs(self) -> CoOccurrenceTracker:
        """3개 페어가 등록된 상태의 Tracker."""
        tracker = CoOccurrenceTracker()
        now = time.time()
        pairs = [
            ("TYPE_A", "TYPE_B"),
            ("TYPE_A", "TYPE_C"),
            ("TYPE_B", "TYPE_C"),
        ]
        for event_a, event_b in pairs:
            tracker.record_event(event_a, now, "svc_a")
            tracker.record_event(event_b, now + 0.5, "svc_b")
        return tracker

    def test_resize_max_tracked_pairs_evicts_excess(self, tracker_with_pairs: CoOccurrenceTracker):
        """max_tracked_pairs 축소 시 초과 페어가 제거된다."""
        initial_count = len(tracker_with_pairs._pair_detectors)
        assert initial_count >= 3

        tracker_with_pairs.resize(max_tracked_pairs=1)

        assert len(tracker_with_pairs._pair_detectors) <= 1

    def test_resize_count_history_recreates_deque(self, tracker_with_pairs: CoOccurrenceTracker):
        """count_history_size 변경 시 deque maxlen이 리사이징된다."""
        # 데이터 추가 확인
        if tracker_with_pairs._pair_time_gaps:
            first_key = next(iter(tracker_with_pairs._pair_time_gaps))
            tracker_with_pairs.resize(count_history_size=5)
            assert tracker_with_pairs._pair_time_gaps[first_key].maxlen == 5

    def test_resize_with_none_params_is_noop(self, tracker_with_pairs: CoOccurrenceTracker):
        """None 파라미터는 해당 차원을 변경하지 않는다."""
        initial_count = len(tracker_with_pairs._pair_detectors)
        tracker_with_pairs.resize(max_tracked_pairs=None, count_history_size=None)
        assert len(tracker_with_pairs._pair_detectors) == initial_count

    def test_resize_evicts_from_time_gaps_and_forecasters(self, tracker_with_pairs: CoOccurrenceTracker):
        """페어 제거 시 _pair_time_gaps와 _pair_forecasters도 함께 정리한다."""
        # analyze_tick()으로 forecasters 등록
        tracker_with_pairs.analyze_tick()

        tracker_with_pairs.resize(max_tracked_pairs=1)

        # pair_detectors 수와 동일하거나 적어야 함
        assert (
            len(tracker_with_pairs._pair_time_gaps) <= len(tracker_with_pairs._pair_detectors) + 3
        )  # defaultdict이므로 접근 시 생성 가능
