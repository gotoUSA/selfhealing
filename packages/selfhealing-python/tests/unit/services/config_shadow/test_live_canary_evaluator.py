"""
Unit tests for Live Canary Evaluator.

검증 항목:
- evaluator name 계약: "live_canary"
- event_types 계약: ["canary_metrics"]
- _calculate_confidence: 요청량별 신뢰도 구간 (0/1~99/100~499/500+ 경계)
- evaluate: PassCriteria 임계값 기반 통과/차단 판정
  - 에러율 절대 임계값 (error_rate_absolute_max)
  - 에러율 증가 임계값 (error_rate_increase_max)
  - P95 Latency 절대 증가 (latency_p95_delta_ms)
  - P99 Latency 비율 증가 (latency_p99_delta_pct)
- evaluate: baseline_p99=0 시 division-by-zero 안전
- evaluate: 결과 메트릭 구조 (baseline_metrics, candidate_metrics, delta)
- ConfigEvaluator Protocol 적합성

테스트 대상: selfhealing.services.config_shadow.evaluators.live_canary
"""

import pytest

from selfhealing.services.canary.models import PassCriteria
from selfhealing.services.config_shadow.evaluators.live_canary import (
    LiveCanaryEvaluator,
)
from selfhealing.services.config_shadow.metrics_provider import (
    MockTimeSeriesProvider,
)
from selfhealing.services.config_shadow.models import EvaluationContext


def _make_provider(**scalars: float) -> MockTimeSeriesProvider:
    """테스트용 MockTimeSeriesProvider를 생성한다."""
    provider = MockTimeSeriesProvider()
    provider._scalars = scalars
    return provider


def _make_context(
    service_name: str = "svc",
    time_window_seconds: int = 300,
) -> EvaluationContext:
    """테스트용 EvaluationContext를 생성한다."""
    return EvaluationContext(
        baseline_config={"failure_threshold": 5},
        candidate_config={"failure_threshold": 3},
        service_name=service_name,
        time_window_seconds=time_window_seconds,
        baseline_labels={"track": "stable"},
        candidate_labels={"track": "canary"},
    )


class TestLiveCanaryEvaluatorContract:
    """LiveCanaryEvaluator 설계 계약값 검증."""

    def test_name_is_live_canary(self):
        """evaluator name: 'live_canary'."""
        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        assert evaluator.name == "live_canary"

    def test_event_types_is_canary_metrics(self):
        """event_types: ['canary_metrics']."""
        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        assert evaluator.event_types == ["canary_metrics"]

    def test_default_pass_criteria_used_when_none(self):
        """pass_criteria=None 시 기본 PassCriteria가 사용된다."""
        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        assert evaluator._criteria.error_rate_absolute_max == 0.05
        assert evaluator._criteria.min_requests_required == 100

    def test_confidence_zero_requests_is_0_1(self):
        """요청 0건: 신뢰도 0.1."""
        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        conf, warnings = evaluator._calculate_confidence(0)
        assert conf == pytest.approx(0.1)
        assert len(warnings) == 1

    def test_confidence_below_min_requests_is_0_4(self):
        """요청 1~99건 (min_requests=100 미만): 신뢰도 0.4."""
        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        conf, warnings = evaluator._calculate_confidence(50)
        assert conf == pytest.approx(0.4)
        assert len(warnings) == 1

    def test_confidence_min_to_5x_is_0_7(self):
        """요청 100~499건 (min*1 ~ min*5 미만): 신뢰도 0.7."""
        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        conf, warnings = evaluator._calculate_confidence(300)
        assert conf == pytest.approx(0.7)
        assert len(warnings) == 0

    def test_confidence_5x_plus_is_0_95(self):
        """요청 500건 이상 (min*5): 신뢰도 0.95."""
        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        conf, warnings = evaluator._calculate_confidence(500)
        assert conf == pytest.approx(0.95)
        assert len(warnings) == 0

    def test_implements_config_evaluator_protocol(self):
        """LiveCanaryEvaluator는 ConfigEvaluator Protocol을 만족한다."""
        from selfhealing.services.config_shadow.evaluators import ConfigEvaluator

        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        assert isinstance(evaluator, ConfigEvaluator)


class TestLiveCanaryEvaluatorBehavior:
    """LiveCanaryEvaluator.evaluate 동작 검증."""

    def test_healthy_canary_passes(self):
        """모든 메트릭이 임계값 내이면 passed=True."""
        # Given
        provider = _make_provider(
            **{
                "svc:error_rate_agg": 0.02,
                "svc:request_count": 500,
                "svc:latency_p95": 100.0,
                "svc:latency_p99": 200.0,
            }
        )
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        # When
        result = evaluator.evaluate(context)

        # Then
        assert result.passed is True
        assert result.evaluator_name == "live_canary"
        assert result.confidence_score == pytest.approx(0.95)

    def test_error_rate_absolute_exceeds_threshold_fails(self):
        """candidate 에러율이 절대 임계값 초과 시 passed=False."""
        # Given — candidate error > 0.05
        provider = MockTimeSeriesProvider()
        provider._scalars = {
            "svc:error_rate_agg": 0.06,
            "svc:request_count": 500,
            "svc:latency_p95": 100.0,
            "svc:latency_p99": 200.0,
        }
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        # When
        result = evaluator.evaluate(context)

        # Then
        assert result.passed is False
        assert "error rate" in result.details.lower()

    def test_error_rate_increase_exceeds_threshold_fails(self):
        """candidate와 baseline 에러율 차이가 증가 임계값 초과 시 passed=False."""
        # Given — Use label-differentiated keys so baseline and candidate
        # resolve to different error rates via MockTimeSeriesProvider.
        # baseline (track=stable) error_rate=0.01, candidate (track=canary)=0.03
        # → error_delta = 0.03 - 0.01 = 0.02, which exceeds the default
        #   PassCriteria.error_rate_increase_max of 0.01.
        # All other metrics (latency, request_count) are set to safe values
        # so that only the error-rate-increase check triggers the failure.
        provider = MockTimeSeriesProvider()
        provider._scalars = {
            "svc:error_rate_agg:track=stable": 0.01,
            "svc:error_rate_agg:track=canary": 0.03,
            "svc:request_count:track=canary": 500,
            "svc:latency_p95:track=stable": 100.0,
            "svc:latency_p95:track=canary": 100.0,
            "svc:latency_p99:track=stable": 200.0,
            "svc:latency_p99:track=canary": 200.0,
        }
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        # When
        result = evaluator.evaluate(context)

        # Then — error_delta=0.02 > default threshold 0.01
        assert result.passed is False
        assert "increase" in result.details.lower()

    def test_p95_latency_delta_exceeds_threshold_fails(self):
        """P95 latency delta가 임계값 초과 시 passed=False."""
        # Given — Use label-differentiated keys to set distinct P95 latencies
        # for baseline and candidate.
        # baseline (track=stable) P95=100ms, candidate (track=canary) P95=160ms
        # → p95_delta = 160 - 100 = 60ms, which exceeds the default
        #   PassCriteria.latency_p95_delta_ms of 50ms.
        # Error rates are equal (0.01) and P99 values are identical (200ms)
        # so that only the P95 latency delta check triggers the failure.
        provider = MockTimeSeriesProvider()
        provider._scalars = {
            "svc:error_rate_agg:track=stable": 0.01,
            "svc:error_rate_agg:track=canary": 0.01,
            "svc:request_count:track=canary": 500,
            "svc:latency_p95:track=stable": 100.0,
            "svc:latency_p95:track=canary": 160.0,
            "svc:latency_p99:track=stable": 200.0,
            "svc:latency_p99:track=canary": 200.0,
        }
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        # When
        result = evaluator.evaluate(context)

        # Then — p95_delta=60ms > default threshold 50ms
        assert result.passed is False
        assert "p95" in result.details.lower()

    def test_p99_latency_pct_exceeds_threshold_fails(self):
        """P99 latency 비율 증가가 임계값 초과 시 passed=False."""
        # Given — Use label-differentiated keys to set distinct P99 latencies
        # for baseline and candidate.
        # baseline (track=stable) P99=200ms, candidate (track=canary) P99=260ms
        # → p99_pct = (260 - 200) / 200 = 0.30 (30%), which exceeds the default
        #   PassCriteria.latency_p99_delta_pct of 0.20 (20%).
        # Error rates are equal (0.01) and P95 values are identical (100ms)
        # so that only the P99 latency percentage check triggers the failure.
        provider = MockTimeSeriesProvider()
        provider._scalars = {
            "svc:error_rate_agg:track=stable": 0.01,
            "svc:error_rate_agg:track=canary": 0.01,
            "svc:request_count:track=canary": 500,
            "svc:latency_p95:track=stable": 100.0,
            "svc:latency_p95:track=canary": 100.0,
            "svc:latency_p99:track=stable": 200.0,
            "svc:latency_p99:track=canary": 260.0,
        }
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        # When
        result = evaluator.evaluate(context)

        # Then — p99_pct=30% > default threshold 20%
        assert result.passed is False
        assert "p99" in result.details.lower()

    def test_baseline_p99_zero_skips_pct_check(self):
        """baseline P99=0 시 비율 체크를 건너뛴다 (division by zero 방지)."""
        # Given
        provider = MockTimeSeriesProvider()
        provider._scalars = {
            "svc:error_rate_agg": 0.01,
            "svc:request_count": 500,
            "svc:latency_p95": 100.0,
            "svc:latency_p99": 0.0,
        }
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        # When
        result = evaluator.evaluate(context)

        # Then
        assert result.passed is True
        assert result.delta["p99_delta_pct"] == 0.0

    def test_result_contains_expected_metric_keys(self):
        """결과에 baseline_metrics, candidate_metrics, delta 키가 포함된다."""
        # Given
        provider = _make_provider(
            **{
                "svc:error_rate_agg": 0.02,
                "svc:request_count": 200,
                "svc:latency_p95": 50.0,
                "svc:latency_p99": 100.0,
            }
        )
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        # When
        result = evaluator.evaluate(context)

        # Then — baseline_metrics
        assert "error_rate" in result.baseline_metrics
        assert "latency_p95_ms" in result.baseline_metrics
        assert "latency_p99_ms" in result.baseline_metrics

        # Then — candidate_metrics
        assert "error_rate" in result.candidate_metrics
        assert "request_count" in result.candidate_metrics
        assert "latency_p95_ms" in result.candidate_metrics
        assert "latency_p99_ms" in result.candidate_metrics

        # Then — delta
        assert "error_rate_delta" in result.delta
        assert "p95_delta_ms" in result.delta
        assert "p99_delta_pct" in result.delta

    def test_multiple_failures_all_reported(self):
        """여러 임계값 동시 위반 시 모든 실패 사유가 details에 포함된다."""
        # Given — Set up label-differentiated keys where ALL four threshold
        # checks fail simultaneously. This verifies that the evaluator does
        # not short-circuit on the first failure but reports every violation.
        #
        # baseline (track=stable): error_rate=0.01, P95=100ms, P99=200ms
        # candidate (track=canary): error_rate=0.10, P95=200ms, P99=300ms
        #
        # Expected violations:
        #   1) error_rate_absolute: 0.10 > 0.05 (default threshold)
        #   2) error_rate_increase: 0.10 - 0.01 = 0.09 > 0.01 (default threshold)
        #   3) p95_delta: 200 - 100 = 100ms > 50ms (default threshold)
        #   4) p99_pct: (300 - 200) / 200 = 50% > 20% (default threshold)
        provider = MockTimeSeriesProvider()
        provider._scalars = {
            "svc:error_rate_agg:track=stable": 0.01,
            "svc:error_rate_agg:track=canary": 0.10,
            "svc:request_count:track=canary": 500,
            "svc:latency_p95:track=stable": 100.0,
            "svc:latency_p95:track=canary": 200.0,
            "svc:latency_p99:track=stable": 200.0,
            "svc:latency_p99:track=canary": 300.0,
        }
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        # When
        result = evaluator.evaluate(context)

        # Then — All four failure reasons must appear in details string
        assert result.passed is False
        details_lower = result.details.lower()
        assert "error rate" in details_lower
        assert "increase" in details_lower
        assert "p95" in details_lower
        assert "p99" in details_lower

    def test_custom_pass_criteria_applied(self):
        """커스텀 PassCriteria가 판정에 사용된다."""
        # Given — very lenient criteria
        provider = MockTimeSeriesProvider()
        provider._scalars = {
            "svc:error_rate_agg": 0.5,
            "svc:request_count": 500,
            "svc:latency_p95": 100.0,
            "svc:latency_p99": 200.0,
        }
        lenient_criteria = PassCriteria(
            error_rate_absolute_max=1.0,
            error_rate_increase_max=1.0,
            latency_p95_delta_ms=10000.0,
            latency_p99_delta_pct=10.0,
        )
        evaluator = LiveCanaryEvaluator(
            metrics_provider=provider, pass_criteria=lenient_criteria
        )
        context = _make_context()

        # When
        result = evaluator.evaluate(context)

        # Then
        assert result.passed is True

    def test_passed_result_contains_healthy_summary(self):
        """통과 시 details에 healthy 요약이 포함된다."""
        provider = _make_provider(
            **{
                "svc:error_rate_agg": 0.01,
                "svc:request_count": 500,
                "svc:latency_p95": 50.0,
                "svc:latency_p99": 100.0,
            }
        )
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        result = evaluator.evaluate(context)

        assert result.passed is True
        assert "healthy" in result.details.lower()


class TestLiveCanaryConfidenceBoundaryBehavior:
    """_calculate_confidence 경계값 동작 검증."""

    def test_boundary_at_min_requests_transitions_to_0_7(self):
        """min_requests 정확히 도달 시 0.4 → 0.7 전이."""
        provider = MockTimeSeriesProvider()
        criteria = PassCriteria(min_requests_required=100)
        evaluator = LiveCanaryEvaluator(
            metrics_provider=provider, pass_criteria=criteria
        )

        # 99건: 0.4
        conf_below, _ = evaluator._calculate_confidence(99)
        assert conf_below == pytest.approx(0.4)

        # 100건: 0.7
        conf_at, _ = evaluator._calculate_confidence(100)
        assert conf_at == pytest.approx(0.7)

    def test_boundary_at_5x_min_requests_transitions_to_0_95(self):
        """min_requests*5 정확히 도달 시 0.7 → 0.95 전이."""
        provider = MockTimeSeriesProvider()
        criteria = PassCriteria(min_requests_required=100)
        evaluator = LiveCanaryEvaluator(
            metrics_provider=provider, pass_criteria=criteria
        )

        # 499건: 0.7
        conf_below, _ = evaluator._calculate_confidence(499)
        assert conf_below == pytest.approx(0.7)

        # 500건: 0.95
        conf_at, _ = evaluator._calculate_confidence(500)
        assert conf_at == pytest.approx(0.95)

    def test_low_volume_warning_included_below_min(self):
        """min_requests 미만 시 Low request volume 경고가 포함된다."""
        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)

        _, warnings = evaluator._calculate_confidence(50)
        assert len(warnings) == 1
        assert "Low request volume" in warnings[0]

    def test_no_warning_at_or_above_min(self):
        """min_requests 이상 시 경고가 없다."""
        provider = MockTimeSeriesProvider()
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)

        _, warnings = evaluator._calculate_confidence(100)
        assert len(warnings) == 0


class TestLiveCanaryEvaluatorEdgeCaseBehavior:
    """LiveCanaryEvaluator 엣지 케이스 동작 검증."""

    def test_zero_requests_very_low_confidence(self):
        """요청 0건: 평가는 수행되나 confidence=0.1."""
        provider = _make_provider(
            **{
                "svc:error_rate_agg": 0.0,
                "svc:request_count": 0,
                "svc:latency_p95": 0.0,
                "svc:latency_p99": 0.0,
            }
        )
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        result = evaluator.evaluate(context)

        assert result.confidence_score == pytest.approx(0.1)
        assert len(result.warnings) >= 1

    def test_all_metrics_zero_passes(self):
        """모든 메트릭이 0이면 임계값 위반 없이 passed=True."""
        provider = MockTimeSeriesProvider()
        # all scalars default to 0.0
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        result = evaluator.evaluate(context)

        assert result.passed is True

    def test_error_rate_at_exact_threshold_passes(self):
        """에러율이 정확히 임계값(0.05)이면 통과 (> 비교이므로)."""
        provider = MockTimeSeriesProvider()
        provider._scalars = {
            "svc:error_rate_agg": 0.05,
            "svc:request_count": 500,
            "svc:latency_p95": 100.0,
            "svc:latency_p99": 200.0,
        }
        evaluator = LiveCanaryEvaluator(metrics_provider=provider)
        context = _make_context()

        result = evaluator.evaluate(context)

        # 0.05 is NOT > 0.05, so it should pass (on error_rate_absolute_max)
        # error_delta = 0.0, also passes
        assert result.passed is True
