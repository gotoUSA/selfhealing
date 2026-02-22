"""
Integration Tests for Co-occurrence Tracker — StateBackend 영속화.

CoOccurrenceTracker + MemoryStateBackend 조합 동작을 검증한다.
InMemory Repository 사용으로 Docker 불필요.

테스트 시나리오 (§252 §9):
- StateBackend save → 재시작 → load (학습 상태 복원 + deque maxlen 보존)
- ZScoreDetector.to_dict() → from_dict() roundtrip
- HoltLinearForecaster 상태 포함 save/load
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from selfhealing.core.state_backend import MemoryStateBackend
from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    TIME_GAPS_MAXLEN,
    CoOccurrenceTracker,
    EventPairKey,
)
from selfhealing.services.predictive_forecaster.anomaly_detector import (
    ZScoreDetector,
)
from selfhealing.settings.correlation import CorrelationSettings

STATE_BACKEND_PATCH_PATH = "selfhealing.core.state_backend.get_state_backend"


@pytest.fixture
def settings() -> CorrelationSettings:
    """기본 CorrelationSettings."""
    return CorrelationSettings()


@pytest.fixture
def memory_backend() -> MemoryStateBackend:
    """MemoryStateBackend 인스턴스."""
    return MemoryStateBackend()


class TestStatePersistenceIntegration:
    """CoOccurrenceTracker + StateBackend 영속화 통합 검증."""

    def test_save_and_load_roundtrip_restores_detectors(
        self, settings: CorrelationSettings, memory_backend: MemoryStateBackend
    ):
        """save_state → load_state로 ZScoreDetector 상태가 복원되어야 한다."""
        tracker = CoOccurrenceTracker(settings)

        # 이벤트 기록를 통해 쌍 생성
        now = time.time()
        for i in range(5):
            tracker.record_event("CB_OPENED", now + i * 10, "payment-api")
            tracker.record_event("ERROR_BUDGET_CRITICAL", now + i * 10 + 2, "payment-api")

        pair_key = EventPairKey("CB_OPENED", "ERROR_BUDGET_CRITICAL").key
        assert pair_key in tracker._pair_detectors

        # ZScoreDetector에 데이터를 주입
        for _ in range(10):
            tracker._pair_detectors[pair_key].is_anomaly(5.0)

        original_values = list(tracker._pair_detectors[pair_key]._values)
        original_gaps = list(tracker._pair_time_gaps.get(pair_key, []))

        # save
        with patch(STATE_BACKEND_PATCH_PATH, return_value=memory_backend):
            assert tracker.save_state() is True

        # 새 tracker로 load (재시작 시뮬레이션)
        new_tracker = CoOccurrenceTracker(settings)
        assert len(new_tracker._pair_detectors) == 0

        with patch(STATE_BACKEND_PATCH_PATH, return_value=memory_backend):
            assert new_tracker.load_state() is True

        # ZScoreDetector 복원 확인
        assert pair_key in new_tracker._pair_detectors
        restored_values = list(new_tracker._pair_detectors[pair_key]._values)
        assert restored_values == original_values

    def test_load_preserves_deque_maxlen(self, settings: CorrelationSettings, memory_backend: MemoryStateBackend):
        """load_state 후 pair_time_gaps의 deque maxlen이 보존되어야 한다."""
        tracker = CoOccurrenceTracker(settings)

        pair_key = "A::B"
        tracker._pair_detectors[pair_key] = ZScoreDetector(window=100, threshold=settings.zscore_threshold)
        for i in range(10):
            tracker._pair_time_gaps[pair_key].append(float(i))

        with patch(STATE_BACKEND_PATCH_PATH, return_value=memory_backend):
            tracker.save_state()

        new_tracker = CoOccurrenceTracker(settings)
        with patch(STATE_BACKEND_PATCH_PATH, return_value=memory_backend):
            new_tracker.load_state()

        # deque maxlen 보존 확인
        restored_gaps = new_tracker._pair_time_gaps[pair_key]
        assert restored_gaps.maxlen == TIME_GAPS_MAXLEN
        assert list(restored_gaps) == list(range(10))

    def test_zscore_detector_roundtrip_preserves_maxlen(self):
        """ZScoreDetector to_dict → from_dict 라운드트립에서 maxlen이 보존되어야 한다."""
        detector = ZScoreDetector(threshold=2.5, window=50)
        for v in range(30):
            detector.is_anomaly(float(v))

        data = detector.to_dict()
        restored = ZScoreDetector.from_dict(data)

        assert restored._values.maxlen == 50
        assert restored._threshold == 2.5
        assert restored._window == 50
        assert list(restored._values) == list(detector._values)

        # 복원 후에도 이상 탐지가 동작해야 함
        is_anomalous, z_score = restored.is_anomaly(1000.0)
        assert isinstance(is_anomalous, bool)
        assert isinstance(z_score, float)

    def test_load_state_returns_false_when_no_saved_state(
        self, settings: CorrelationSettings, memory_backend: MemoryStateBackend
    ):
        """저장된 상태가 없으면 load_state가 False를 반환해야 한다."""
        tracker = CoOccurrenceTracker(settings)
        with patch(STATE_BACKEND_PATCH_PATH, return_value=memory_backend):
            assert tracker.load_state() is False

    def test_save_load_with_multiple_pairs(self, settings: CorrelationSettings, memory_backend: MemoryStateBackend):
        """여러 쌍의 상태가 올바르게 저장/복원되어야 한다."""
        tracker = CoOccurrenceTracker(settings)

        # 3개 쌍 생성
        pairs = ["A::B", "C::D", "E::F"]
        for pk in pairs:
            tracker._pair_detectors[pk] = ZScoreDetector(window=100, threshold=settings.zscore_threshold)
            for v in range(5):
                tracker._pair_detectors[pk].is_anomaly(float(v))
                tracker._pair_time_gaps[pk].append(float(v) * -1)

        with patch(STATE_BACKEND_PATCH_PATH, return_value=memory_backend):
            tracker.save_state()

        new_tracker = CoOccurrenceTracker(settings)
        with patch(STATE_BACKEND_PATCH_PATH, return_value=memory_backend):
            new_tracker.load_state()

        # 모든 쌍이 복원되었는지 확인
        for pk in pairs:
            assert pk in new_tracker._pair_detectors
            assert pk in new_tracker._pair_time_gaps
            assert len(new_tracker._pair_time_gaps[pk]) == 5
