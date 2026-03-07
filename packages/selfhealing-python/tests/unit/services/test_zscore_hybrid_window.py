"""
Unit tests for ZScoreDetector hybrid window (max_age_seconds).

검증 항목:
- 계약: max_age_seconds=None 시 하위 호환 (기존 동작 보존)
- 동작: 시간 경과 시 오래된 데이터 포인트 제거
- 직렬화: to_dict/from_dict 왕복 시 max_age_seconds 보존
- 엣지 케이스: 전체 데이터 만료, 빈 윈도우

테스트 대상: selfhealing.services.predictive_forecaster.anomaly_detector.ZScoreDetector
참조: 313_SETTINGS_CONFIGURATION_CONSISTENCY.md Q6 결정
"""

from __future__ import annotations

from unittest.mock import patch

from selfhealing.services.predictive_forecaster.anomaly_detector import ZScoreDetector

# =============================================================================
# 계약 검증: max_age_seconds 하위 호환
# =============================================================================


class TestZScoreHybridWindowContract:
    """ZScoreDetector hybrid window 설계 계약 검증."""

    def test_max_age_seconds_default_is_none(self):
        """max_age_seconds 기본값: None (시간 제한 없음). 313 Q6 설계 계약."""
        detector = ZScoreDetector()
        assert detector._max_age_seconds is None

    def test_timestamps_deque_initialized_empty(self):
        """max_age_seconds 활성 시 _timestamps deque가 초기화된다."""
        detector = ZScoreDetector(max_age_seconds=60.0)
        assert len(detector._timestamps) == 0

    def test_without_max_age_no_timestamps_stored(self):
        """max_age_seconds=None이면 타임스탬프를 저장하지 않는다."""
        detector = ZScoreDetector(window=10)
        for v in [1.0, 2.0, 3.0]:
            detector.is_anomaly(v)
        assert len(detector._timestamps) == 0
        assert len(detector._values) == 3


# =============================================================================
# 동작 검증: 시간 기반 제거
# =============================================================================


class TestZScoreHybridWindowBehavior:
    """ZScoreDetector hybrid window 동작 검증."""

    def test_stale_data_evicted_after_max_age(self):
        """max_age_seconds 초과 데이터 포인트가 제거된다."""
        detector = ZScoreDetector(window=100, max_age_seconds=10.0)

        # Given: monotonic 시간을 제어하여 데이터 삽입
        base_time = 1000.0
        with patch("time.monotonic", return_value=base_time):
            for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
                detector._append_value(v)

        assert len(detector._values) == 5

        # When: 11초 후 (max_age 초과)
        with patch("time.monotonic", return_value=base_time + 11.0):
            detector._evict_stale()

        # Then: 모든 이전 데이터 제거
        assert len(detector._values) == 0

    def test_partial_eviction_keeps_recent_data(self):
        """일부만 만료된 경우 최근 데이터는 유지된다."""
        detector = ZScoreDetector(window=100, max_age_seconds=10.0)

        # Given: 두 시점에서 데이터 삽입
        with patch("time.monotonic", return_value=1000.0):
            detector._append_value(1.0)
            detector._append_value(2.0)

        with patch("time.monotonic", return_value=1008.0):
            detector._append_value(3.0)
            detector._append_value(4.0)

        assert len(detector._values) == 4

        # When: 1011초 시점 (첫 두 개만 만료)
        with patch("time.monotonic", return_value=1011.0):
            detector._evict_stale()

        # Then: 최근 2개만 유지
        assert len(detector._values) == 2
        assert list(detector._values) == [3.0, 4.0]

    def test_is_anomaly_triggers_eviction(self):
        """is_anomaly() 호출 시 자동으로 stale 데이터가 제거된다."""
        detector = ZScoreDetector(window=100, threshold=3.0, max_age_seconds=5.0)

        # Given: 오래된 데이터 삽입
        with patch("time.monotonic", return_value=1000.0):
            for v in [10.0, 10.0, 10.0, 10.0]:
                detector._append_value(v)

        # When: 충분한 시간 경과 후 새 값으로 is_anomaly 호출
        with patch("time.monotonic", return_value=1010.0):
            result, z_score = detector.is_anomaly(10.0)

        # Then: 오래된 4개 제거, 새 1개만 남아 데이터 부족 → (False, 0.0)
        assert result is False
        assert z_score == 0.0
        assert len(detector._values) == 1

    def test_evict_stale_noop_when_max_age_none(self):
        """max_age_seconds=None이면 _evict_stale은 no-op이다."""
        detector = ZScoreDetector(window=10)
        for v in [1.0, 2.0, 3.0]:
            detector._values.append(v)

        detector._evict_stale()
        assert len(detector._values) == 3

    def test_get_statistics_evicts_stale_before_computing(self):
        """get_statistics()가 stale 데이터를 제거한 후 통계를 계산한다."""
        detector = ZScoreDetector(window=100, max_age_seconds=5.0)

        with patch("time.monotonic", return_value=1000.0):
            for v in [10.0, 20.0, 30.0]:
                detector._append_value(v)

        # When: 모든 데이터 만료 후 통계 조회
        with patch("time.monotonic", return_value=1010.0):
            stats = detector.get_statistics()

        # Then: 빈 윈도우 통계
        assert stats["count"] == 0

    def test_reset_clears_timestamps(self):
        """reset()이 _timestamps도 함께 초기화한다."""
        detector = ZScoreDetector(window=10, max_age_seconds=60.0)

        with patch("time.monotonic", return_value=1000.0):
            detector._append_value(1.0)
            detector._append_value(2.0)

        assert len(detector._timestamps) == 2

        detector.reset()
        assert len(detector._timestamps) == 0
        assert len(detector._values) == 0


# =============================================================================
# 직렬화 왕복 검증
# =============================================================================


class TestZScoreHybridWindowSerializationBehavior:
    """ZScoreDetector to_dict/from_dict hybrid window 왕복 검증."""

    def test_round_trip_preserves_max_age_seconds(self):
        """to_dict → from_dict 왕복 시 max_age_seconds가 보존된다."""
        original = ZScoreDetector(threshold=2.5, window=50, max_age_seconds=120.0)
        for v in [1.0, 2.0, 3.0]:
            original._values.append(v)

        serialized = original.to_dict()
        restored = ZScoreDetector.from_dict(serialized)

        assert restored._threshold == original._threshold
        assert restored._window == original._window
        assert restored._max_age_seconds == original._max_age_seconds
        assert list(restored._values) == list(original._values)

    def test_round_trip_restores_timestamps_when_max_age_set(self):
        """from_dict()가 max_age_seconds 설정 시 _timestamps를 복원한다."""
        original = ZScoreDetector(threshold=2.5, window=50, max_age_seconds=60.0)
        original._values.extend([1.0, 2.0, 3.0])

        restored = ZScoreDetector.from_dict(original.to_dict())

        assert len(restored._timestamps) == len(restored._values)
        assert len(restored._timestamps) == 3

    def test_round_trip_no_timestamps_when_max_age_none(self):
        """from_dict()가 max_age_seconds=None 시 _timestamps를 생성하지 않는다."""
        original = ZScoreDetector(threshold=3.0, window=100)
        original._values.extend([1.0, 2.0, 3.0])

        restored = ZScoreDetector.from_dict(original.to_dict())

        assert len(restored._timestamps) == 0

    def test_round_trip_append_then_evict_keeps_alignment(self):
        """복원 후 값 추가 + 제거 시 _values와 _timestamps 정렬이 유지된다."""
        # 복원: 5개 값, 타임스탬프는 "지금" 기준으로 생성됨
        data = {
            "values": [1.0, 2.0, 3.0, 4.0, 5.0],
            "threshold": 3.0,
            "window": 100,
            "max_age_seconds": 10.0,
        }

        with patch("time.monotonic", return_value=1000.0):
            restored = ZScoreDetector.from_dict(data)

        assert len(restored._values) == 5
        assert len(restored._timestamps) == 5

        # 새 값 3개 추가 (8초 후)
        with patch("time.monotonic", return_value=1008.0):
            for v in [6.0, 7.0, 8.0]:
                restored._append_value(v)

        assert len(restored._values) == 8
        assert len(restored._timestamps) == 8

        # 11초 후 제거 → 복원된 5개만 만료, 새 3개 유지
        with patch("time.monotonic", return_value=1011.0):
            restored._evict_stale()

        assert len(restored._values) == 3
        assert len(restored._timestamps) == 3
        assert list(restored._values) == [6.0, 7.0, 8.0]

    def test_round_trip_without_max_age_preserves_none(self):
        """max_age_seconds=None인 경우 직렬화/역직렬화 시 None 유지."""
        original = ZScoreDetector(threshold=3.0, window=100)
        original._values.extend([1.0, 2.0, 3.0])

        serialized = original.to_dict()
        assert "max_age_seconds" not in serialized

        restored = ZScoreDetector.from_dict(serialized)
        assert restored._max_age_seconds is None

    def test_serialized_keys_include_max_age_when_set(self):
        """max_age_seconds가 설정되면 직렬화 dict에 포함된다."""
        detector = ZScoreDetector(max_age_seconds=300.0)
        data = detector.to_dict()
        assert "max_age_seconds" in data
        assert data["max_age_seconds"] == 300.0

    def test_serialized_keys_exclude_max_age_when_none(self):
        """max_age_seconds=None이면 직렬화 dict에 포함되지 않는다."""
        detector = ZScoreDetector()
        data = detector.to_dict()
        assert "max_age_seconds" not in data
