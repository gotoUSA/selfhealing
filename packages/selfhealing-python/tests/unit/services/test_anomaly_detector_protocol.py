"""
Tests for AnomalyDetectionStrategy Protocol 호환 메서드.

ZScoreDetector 및 IQRDetector에 추가된 detect(), update(),
get_feature_schema() 메서드를 검증한다.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: detect()이 is_anomaly()와 동일한 반환 형식을 보장
- Behavior: detect/update/get_feature_schema 동작 검증

참조 소스:
- services/predictive_forecaster/anomaly_detector.py
  (ZScoreDetector.detect, IQRDetector.detect, update, get_feature_schema)
"""

from __future__ import annotations

from selfhealing.services.predictive_forecaster.anomaly_detector import (
    IQRDetector,
    ZScoreDetector,
)

# =============================================================================
# ZScoreDetector Protocol 메서드 계약 검증
# =============================================================================


class TestZScoreDetectorProtocolContract:
    """ZScoreDetector의 AnomalyDetectionStrategy Protocol 호환 계약."""

    def test_detect_returns_tuple_bool_float(self):
        """detect()는 (bool, float) 튜플을 반환해야 한다."""
        detector = ZScoreDetector(threshold=3.0, window=10)
        result = detector.detect(1.0)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], float)

    def test_get_feature_schema_returns_none(self):
        """통계 기반 전략이므로 get_feature_schema()는 None을 반환한다."""
        detector = ZScoreDetector()
        assert detector.get_feature_schema() is None


# =============================================================================
# ZScoreDetector Protocol 메서드 동작 검증
# =============================================================================


class TestZScoreDetectorProtocolBehavior:
    """ZScoreDetector detect/update 동작 검증."""

    def test_detect_delegates_to_is_anomaly(self):
        """detect()는 is_anomaly()와 동일한 결과를 반환한다."""
        d1 = ZScoreDetector(threshold=3.0, window=10)
        d2 = ZScoreDetector(threshold=3.0, window=10)

        values = [1.0, 2.0, 1.5, 1.0, 2.0, 1.5, 1.0, 100.0]
        for v in values[:-1]:
            d1.is_anomaly(v)
            d2.detect(v)

        result_is_anomaly = d1.is_anomaly(values[-1])
        result_detect = d2.detect(values[-1])

        assert result_detect == result_is_anomaly

    def test_detect_context_is_ignored(self):
        """detect()의 context 파라미터는 무시된다."""
        d1 = ZScoreDetector(threshold=3.0, window=10)
        d2 = ZScoreDetector(threshold=3.0, window=10)

        for v in [1.0, 2.0, 1.5, 1.0]:
            d1.detect(v)
            d2.detect(v, context={"key": "value"})

        r1 = d1.detect(5.0)
        r2 = d2.detect(5.0, context={"key": "value"})
        assert r1 == r2

    def test_update_adds_value_to_window(self):
        """update()는 윈도우에 값을 추가한다."""
        detector = ZScoreDetector(threshold=3.0, window=10)
        detector.update(5.0)
        detector.update(5.0)
        detector.update(5.0)

        stats = detector.get_statistics()
        assert stats["count"] == 3

    def test_update_context_is_ignored(self):
        """update()의 context 파라미터는 무시된다."""
        detector = ZScoreDetector(threshold=3.0, window=10)
        detector.update(5.0, context={"meta": True})
        assert detector.get_statistics()["count"] == 1


# =============================================================================
# IQRDetector Protocol 메서드 계약 검증
# =============================================================================


class TestIQRDetectorProtocolContract:
    """IQRDetector의 AnomalyDetectionStrategy Protocol 호환 계약."""

    def test_detect_returns_tuple_bool_float(self):
        """detect()는 (bool, float) 튜플을 반환해야 한다."""
        detector = IQRDetector(multiplier=1.5, window=10)
        result = detector.detect(1.0)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], float)

    def test_get_feature_schema_returns_none(self):
        """통계 기반 전략이므로 get_feature_schema()는 None을 반환한다."""
        detector = IQRDetector()
        assert detector.get_feature_schema() is None


# =============================================================================
# IQRDetector Protocol 메서드 동작 검증
# =============================================================================


class TestIQRDetectorProtocolBehavior:
    """IQRDetector detect/update 동작 검증."""

    def test_detect_delegates_to_is_anomaly(self):
        """detect()는 is_anomaly()와 동일한 결과를 반환한다."""
        d1 = IQRDetector(multiplier=1.5, window=10)
        d2 = IQRDetector(multiplier=1.5, window=10)

        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 100.0]
        for v in values[:-1]:
            d1.is_anomaly(v)
            d2.detect(v)

        result_is_anomaly = d1.is_anomaly(values[-1])
        result_detect = d2.detect(values[-1])

        assert result_detect == result_is_anomaly

    def test_update_adds_value_to_window(self):
        """update()는 윈도우에 값을 추가한다."""
        detector = IQRDetector(multiplier=1.5, window=10)
        for v in [1.0, 2.0, 3.0, 4.0]:
            detector.update(v)

        bounds = detector.get_bounds()
        assert bounds is not None
        assert bounds["q1"] > 0
