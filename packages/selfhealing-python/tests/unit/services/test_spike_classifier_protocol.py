"""
Tests for SpikeClassifier.classify_features() — ClassificationStrategy Protocol 호환.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: classify_features() 반환 형식 (str, float) 검증
- Behavior: features dict 파싱 및 classify() 위임 동작 검증

참조 소스:
- services/predictive_forecaster/proactive_action.py
  (SpikeClassifier.classify_features, _parse_history)
"""

from __future__ import annotations

import pytest

from selfhealing.services.predictive_forecaster.proactive_action import (
    SpikeClassifier,
    SpikeType,
)


# =============================================================================
# _parse_history 동작 검증
# =============================================================================


class TestParseHistoryBehavior:
    """_parse_history() 정적 메서드 동작 검증."""

    def test_parse_csv_string(self):
        """쉼표 구분 문자열을 list[float]로 변환한다."""
        result = SpikeClassifier._parse_history("1.0, 2.5, 3.0")
        assert result == [1.0, 2.5, 3.0]

    def test_parse_list_input(self):
        """list 입력은 각 요소를 float로 변환한다."""
        result = SpikeClassifier._parse_history([1, 2, 3])
        assert result == [1.0, 2.0, 3.0]

    def test_parse_empty_string_returns_empty_list(self):
        """빈 문자열은 빈 리스트를 반환한다."""
        result = SpikeClassifier._parse_history("")
        assert result == []

    def test_parse_none_returns_empty_list(self):
        """None류 입력은 빈 리스트를 반환한다."""
        result = SpikeClassifier._parse_history(None)
        assert result == []

    def test_parse_whitespace_csv(self):
        """공백 포함 CSV도 정상 파싱된다."""
        result = SpikeClassifier._parse_history(" 1.0 , 2.0 , 3.0 ")
        assert result == [1.0, 2.0, 3.0]


# =============================================================================
# classify_features 계약 검증
# =============================================================================


class TestClassifyFeaturesContract:
    """classify_features() 반환 형식 계약 검증."""

    def test_returns_tuple_str_float(self):
        """classify_features()는 (str, float) 튜플을 반환해야 한다."""
        classifier = SpikeClassifier()
        result = classifier.classify_features(
            {"rps_history": "1,2,3,4,5", "error_rate_history": "0,0,0,0,0", "latency_history": "10,10,10,10,10"}
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], float)

    def test_returned_label_is_valid_spike_type_value(self):
        """반환된 label은 SpikeType enum value 중 하나이어야 한다."""
        classifier = SpikeClassifier()
        valid_values = {st.value for st in SpikeType}
        label, _ = classifier.classify_features(
            {"rps_history": "1,2,3,4,5", "error_rate_history": "0,0,0,0,0", "latency_history": "10,10,10,10,10"}
        )
        assert label in valid_values


# =============================================================================
# classify_features 동작 검증
# =============================================================================


class TestClassifyFeaturesBehavior:
    """classify_features() 동작 검증."""

    def test_delegates_to_classify(self):
        """features에서 파싱된 히스토리가 classify()에 위임된다."""
        classifier = SpikeClassifier()

        rps = [10, 20, 30, 40, 50]
        error = [0.0, 0.0, 0.0, 0.0, 0.0]
        latency = [100, 100, 100, 100, 100]

        direct_result = classifier.classify(rps, error, latency)
        features_result_label, _ = classifier.classify_features(
            {
                "rps_history": ",".join(str(v) for v in rps),
                "error_rate_history": ",".join(str(v) for v in error),
                "latency_history": ",".join(str(v) for v in latency),
            }
        )

        assert features_result_label == direct_result.value

    def test_confidence_increases_with_data_size(self):
        """입력 데이터 크기가 클수록 confidence가 높아진다."""
        classifier = SpikeClassifier()

        _, conf_small = classifier.classify_features(
            {
                "rps_history": "1,2,3",
                "error_rate_history": "0,0,0",
                "latency_history": "10,10,10",
            }
        )

        _, conf_large = classifier.classify_features(
            {
                "rps_history": ",".join(str(i) for i in range(20)),
                "error_rate_history": ",".join("0" for _ in range(20)),
                "latency_history": ",".join("10" for _ in range(20)),
            }
        )

        assert conf_large > conf_small

    def test_confidence_capped_at_one(self):
        """confidence는 1.0을 초과하지 않는다."""
        classifier = SpikeClassifier()
        _, conf = classifier.classify_features(
            {
                "rps_history": ",".join(str(i) for i in range(50)),
                "error_rate_history": ",".join("0" for _ in range(50)),
                "latency_history": ",".join("10" for _ in range(50)),
            }
        )
        assert conf <= 1.0

    def test_empty_features_returns_gradual_degradation(self):
        """빈 features는 히스토리 부족으로 GRADUAL_DEGRADATION을 반환한다."""
        classifier = SpikeClassifier()
        label, _ = classifier.classify_features({})
        assert label == SpikeType.GRADUAL_DEGRADATION.value

    def test_context_parameter_is_ignored(self):
        """context 파라미터는 결과에 영향을 주지 않는다."""
        classifier = SpikeClassifier()
        features = {
            "rps_history": "1,2,3,4,5",
            "error_rate_history": "0,0,0,0,0",
            "latency_history": "10,10,10,10,10",
        }
        r1 = classifier.classify_features(features)
        r2 = classifier.classify_features(features, context={"key": "value"})
        assert r1 == r2

    def test_list_input_in_features(self):
        """features에 list 값도 지원한다."""
        classifier = SpikeClassifier()
        label, conf = classifier.classify_features(
            {
                "rps_history": [1, 2, 3, 4, 5],
                "error_rate_history": [0, 0, 0, 0, 0],
                "latency_history": [10, 10, 10, 10, 10],
            }
        )
        assert isinstance(label, str)
        assert isinstance(conf, float)
