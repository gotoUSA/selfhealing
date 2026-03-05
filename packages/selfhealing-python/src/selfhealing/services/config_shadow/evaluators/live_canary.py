"""
Live Canary Evaluator.

Canary 노드의 실시간 메트릭을 기반으로 설정 변경의 실제 영향을 평가한다.
ConfigEvaluator Protocol을 구현하며, EvaluationContext의
time_window_seconds + labels를 사용하여 TimeSeriesMetricsProvider에서
실시간 데이터를 조회한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from selfhealing.services.canary.models import PassCriteria
from selfhealing.services.config_shadow.metrics_provider import (
    TimeSeriesMetricsProvider,
)
from selfhealing.services.config_shadow.models import EvaluationContext, EvaluatorResult

logger = logging.getLogger(__name__)


class LiveCanaryEvaluator:
    """Canary 노드의 실시간 메트릭을 기반으로 설정 변경의 실제 영향을 평가한다.

    ConfigEvaluator Protocol을 구현하며, EvaluationContext의
    time_window_seconds + labels를 사용하여 TimeSeriesMetricsProvider에서
    실시간 데이터를 조회한다.

    PassCriteria의 임계값 데이터를 읽어 판정 기준으로 사용한다.
    """

    def __init__(
        self,
        metrics_provider: TimeSeriesMetricsProvider,
        pass_criteria: PassCriteria | None = None,
    ) -> None:
        self._metrics = metrics_provider
        self._criteria = pass_criteria or PassCriteria()

    @property
    def name(self) -> str:
        return "live_canary"

    @property
    def event_types(self) -> list[str]:
        return ["canary_metrics"]

    def evaluate(self, context: EvaluationContext) -> EvaluatorResult:
        """실시간 메트릭을 조회하여 baseline과 candidate의 동작을 비교한다."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(seconds=context.time_window_seconds)
        warnings: list[str] = []
        criteria = self._criteria

        # 1. 가중치 기반 에러율 스칼라 조회
        baseline_error = self._metrics.query_error_rate_aggregated(
            service_name=context.service_name,
            start=start,
            end=now,
            labels=context.baseline_labels,
        )
        candidate_error = self._metrics.query_error_rate_aggregated(
            service_name=context.service_name,
            start=start,
            end=now,
            labels=context.candidate_labels,
        )
        error_delta = candidate_error - baseline_error

        # 2. 총 요청 수 조회
        candidate_request_count = self._metrics.query_request_count(
            service_name=context.service_name,
            start=start,
            end=now,
            labels=context.candidate_labels,
        )

        # 3. Latency P95/P99 스칼라 조회
        baseline_p95 = self._metrics.query_latency_aggregated(
            service_name=context.service_name,
            start=start,
            end=now,
            percentile=0.95,
            labels=context.baseline_labels,
        )
        candidate_p95 = self._metrics.query_latency_aggregated(
            service_name=context.service_name,
            start=start,
            end=now,
            percentile=0.95,
            labels=context.candidate_labels,
        )
        baseline_p99 = self._metrics.query_latency_aggregated(
            service_name=context.service_name,
            start=start,
            end=now,
            percentile=0.99,
            labels=context.baseline_labels,
        )
        candidate_p99 = self._metrics.query_latency_aggregated(
            service_name=context.service_name,
            start=start,
            end=now,
            percentile=0.99,
            labels=context.candidate_labels,
        )

        # 4. 데이터 충분성 → confidence
        confidence, conf_warnings = self._calculate_confidence(
            candidate_request_count,
        )
        warnings.extend(conf_warnings)

        # 5. 통과 판정 (PassCriteria 임계값 사용)
        passed = True
        details_parts: list[str] = []

        # 5a. 에러율 절대 임계값
        if candidate_error > criteria.error_rate_absolute_max:
            passed = False
            details_parts.append(
                f"Canary error rate {candidate_error:.3f} > "
                f"threshold {criteria.error_rate_absolute_max:.3f}"
            )

        # 5b. 에러율 증가 임계값
        if error_delta > criteria.error_rate_increase_max:
            passed = False
            details_parts.append(
                f"Error rate increase {error_delta:.3f} > "
                f"threshold {criteria.error_rate_increase_max:.3f}"
            )

        # 5c. P95 Latency 절대 증가
        p95_delta = candidate_p95 - baseline_p95
        if p95_delta > criteria.latency_p95_delta_ms:
            passed = False
            details_parts.append(
                f"P95 latency delta {p95_delta:.1f}ms > "
                f"threshold {criteria.latency_p95_delta_ms:.1f}ms"
            )

        # 5d. P99 Latency 비율 증가
        p99_pct = (
            (candidate_p99 - baseline_p99) / baseline_p99 if baseline_p99 > 0 else 0.0
        )
        if baseline_p99 > 0 and p99_pct > criteria.latency_p99_delta_pct:
            passed = False
            details_parts.append(
                f"P99 latency increased by {p99_pct:.1%} > "
                f"threshold {criteria.latency_p99_delta_pct:.1%}"
            )

        if passed:
            details_parts.append(
                f"Canary healthy: error_rate={candidate_error:.3f}, "
                f"delta={error_delta:+.3f}, "
                f"p95={candidate_p95:.1f}ms, p99={candidate_p99:.1f}ms, "
                f"requests={candidate_request_count}"
            )

        return EvaluatorResult(
            evaluator_name=self.name,
            passed=passed,
            confidence_score=confidence,
            baseline_metrics={
                "error_rate": baseline_error,
                "latency_p95_ms": baseline_p95,
                "latency_p99_ms": baseline_p99,
            },
            candidate_metrics={
                "error_rate": candidate_error,
                "request_count": candidate_request_count,
                "latency_p95_ms": candidate_p95,
                "latency_p99_ms": candidate_p99,
            },
            delta={
                "error_rate_delta": error_delta,
                "p95_delta_ms": p95_delta,
                "p99_delta_pct": p99_pct,
            },
            details="; ".join(details_parts),
            warnings=warnings,
        )

    def _calculate_confidence(
        self,
        request_count: int,
    ) -> tuple[float, list[str]]:
        """요청량 기반 신뢰도 계산."""
        warnings: list[str] = []
        min_requests = self._criteria.min_requests_required

        if request_count < min_requests:
            warnings.append(
                f"Low request volume ({request_count} < {min_requests}). "
                f"Confidence reduced."
            )
            if request_count == 0:
                return 0.1, warnings
            return 0.4, warnings

        if request_count < min_requests * 5:
            return 0.7, warnings

        return 0.95, warnings
