"""
Throttle Prometheus 메트릭 테스트.

테스트 대상:
1. throttle_current_limit (Gauge)
2. throttle_rtt_ms (Histogram)
3. throttle_gradient (Gauge)
4. throttle_denied_total (Counter)
5. throttle_emergency_adjustments_total (Counter)
6. throttle_cb_adjustments_total (Counter)
"""

from unittest.mock import patch


class TestThrottleCurrentLimitMetric:
    """throttle_current_limit 메트릭 테스트."""

    def test_register_throttle_current_limit(self):
        """throttle_current_limit 메트릭 등록 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_gauge

        # definitions 모듈 리로드하면 메트릭이 등록됨
        # 등록이 예외 없이 완료되는지 확인
        metric = get_or_create_gauge(
            "throttle_current_limit",
            "현재 적용 중인 스로틀 한도",
            ["service"],
        )
        assert metric is not None

    def test_throttle_current_limit_with_label(self):
        """throttle_current_limit 메트릭 라벨 설정 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_gauge

        metric = get_or_create_gauge(
            "throttle_current_limit",
            "현재 적용 중인 스로틀 한도",
            ["service"],
        )
        # 라벨로 메트릭 사용 가능한지 확인
        labeled = metric.labels(service="test-service")
        assert labeled is not None


class TestThrottleRttMsMetric:
    """throttle_rtt_ms 메트릭 테스트."""

    def test_register_throttle_rtt_ms(self):
        """throttle_rtt_ms 메트릭 등록 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_histogram

        metric = get_or_create_histogram(
            "throttle_rtt_ms",
            "응답 시간 분포 (밀리초)",
            ["service"],
            buckets=[10, 25, 50, 100, 250, 500, 1000, 2500, 5000],
        )
        assert metric is not None

    def test_throttle_rtt_ms_observe(self):
        """throttle_rtt_ms 메트릭 observe 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_histogram

        metric = get_or_create_histogram(
            "throttle_rtt_ms",
            "응답 시간 분포 (밀리초)",
            ["service"],
            buckets=[10, 25, 50, 100, 250, 500, 1000, 2500, 5000],
        )
        labeled = metric.labels(service="test-service")
        # observe 호출이 예외 없이 완료되는지 확인
        labeled.observe(150.0)


class TestThrottleGradientMetric:
    """throttle_gradient 메트릭 테스트."""

    def test_register_throttle_gradient(self):
        """throttle_gradient 메트릭 등록 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_gauge

        metric = get_or_create_gauge(
            "throttle_gradient",
            "현재 그래디언트 값",
            ["service"],
        )
        assert metric is not None


class TestThrottleDeniedTotalMetric:
    """throttle_denied_total 메트릭 테스트."""

    def test_register_throttle_denied_total(self):
        """throttle_denied_total 메트릭 등록 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_counter

        metric = get_or_create_counter(
            "throttle_denied_total",
            "스로틀로 인해 거부된 총 요청 수",
            ["service", "reason"],
        )
        assert metric is not None

    def test_throttle_denied_total_with_reason_label(self):
        """throttle_denied_total 메트릭 reason 라벨 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_counter

        metric = get_or_create_counter(
            "throttle_denied_total",
            "스로틀로 인해 거부된 총 요청 수",
            ["service", "reason"],
        )
        labeled = metric.labels(service="test-service", reason="limit_exceeded")
        assert labeled is not None


class TestThrottleEmergencyAdjustmentsMetric:
    """throttle_emergency_adjustments_total 메트릭 테스트."""

    def test_register_emergency_adjustments(self):
        """throttle_emergency_adjustments_total 메트릭 등록 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_counter

        metric = get_or_create_counter(
            "throttle_emergency_adjustments_total",
            "Emergency Level 변경으로 인한 한도 조정 횟수",
            ["level"],
        )
        assert metric is not None

    def test_emergency_adjustments_with_level_label(self):
        """throttle_emergency_adjustments_total 메트릭 level 라벨 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_counter

        metric = get_or_create_counter(
            "throttle_emergency_adjustments_total",
            "Emergency Level 변경으로 인한 한도 조정 횟수",
            ["level"],
        )
        labeled = metric.labels(level="2")
        assert labeled is not None


class TestThrottleCbAdjustmentsMetric:
    """throttle_cb_adjustments_total 메트릭 테스트."""

    def test_register_cb_adjustments(self):
        """throttle_cb_adjustments_total 메트릭 등록 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_counter

        metric = get_or_create_counter(
            "throttle_cb_adjustments_total",
            "CB 상태 변경으로 인한 한도 조정 횟수",
            ["service", "cb_state"],
        )
        assert metric is not None

    def test_cb_adjustments_with_state_label(self):
        """throttle_cb_adjustments_total 메트릭 cb_state 라벨 테스트."""
        from selfhealing.services.metrics.registry import get_or_create_counter

        metric = get_or_create_counter(
            "throttle_cb_adjustments_total",
            "CB 상태 변경으로 인한 한도 조정 횟수",
            ["service", "cb_state"],
        )
        labeled = metric.labels(service="test-service", cb_state="OPEN")
        assert labeled is not None


class TestRecordThrottleMetricsHelper:
    """_record_throttle_metrics 헬퍼 함수 테스트."""

    def test_record_throttle_metrics_success(self):
        """정상적인 메트릭 기록 테스트."""
        from selfhealing.services.throttle.adaptive import _record_throttle_metrics

        # 예외 없이 완료되면 성공
        _record_throttle_metrics(
            service="test-service",
            limit=100,
            rtt_ms=50.0,
            gradient=1.2,
            cb_state="OPEN",
        )

    def test_record_throttle_metrics_partial(self):
        """일부 값만 전달했을 때 테스트."""
        from selfhealing.services.throttle.adaptive import _record_throttle_metrics

        # 필수 값만 전달해도 예외 없이 완료
        _record_throttle_metrics(
            service="test-service",
            limit=100,
        )

    def test_record_throttle_metrics_fail_open(self):
        """메트릭 기록 실패 시 Fail-Open 테스트."""
        from selfhealing.services.throttle.adaptive import _record_throttle_metrics

        with patch(
            "selfhealing.services.metrics.definitions.throttle_current_limit",
        ) as mock_metric:
            mock_metric.labels.side_effect = Exception("Test error")
            # 예외가 전파되지 않고 조용히 실패
            _record_throttle_metrics(
                service="test-service",
                limit=100,
            )
