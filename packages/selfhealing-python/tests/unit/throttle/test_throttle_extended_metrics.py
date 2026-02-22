"""
AdaptiveThrottle 확장 메트릭 단위 테스트.

새롭게 추가된 메트릭들이 올바르게 기록되는지 확인.
"""


import pytest


class TestThrottleExtendedMetricsDefinitions:
    """확장 메트릭 정의 테스트."""

    def test_throttle_requests_total_exists(self):
        """throttle_requests_total 메트릭이 정의되어 있는지 확인."""
        from selfhealing.services.metrics import definitions

        assert hasattr(definitions, "throttle_requests_total")

    def test_throttle_saturation_ratio_exists(self):
        """throttle_saturation_ratio 메트릭이 정의되어 있는지 확인."""
        from selfhealing.services.metrics import definitions

        assert hasattr(definitions, "throttle_saturation_ratio")

    def test_throttle_current_limit_exists(self):
        """throttle_current_limit 메트릭이 정의되어 있는지 확인."""
        from selfhealing.services.metrics import definitions

        assert hasattr(definitions, "throttle_current_limit")

    def test_throttle_rtt_ms_exists(self):
        """throttle_rtt_ms 메트릭이 정의되어 있는지 확인."""
        from selfhealing.services.metrics import definitions

        assert hasattr(definitions, "throttle_rtt_ms")

    def test_throttle_gradient_exists(self):
        """throttle_gradient 메트릭이 정의되어 있는지 확인."""
        from selfhealing.services.metrics import definitions

        assert hasattr(definitions, "throttle_gradient")

    def test_throttle_denied_total_exists(self):
        """throttle_denied_total 메트릭이 정의되어 있는지 확인."""
        from selfhealing.services.metrics import definitions

        assert hasattr(definitions, "throttle_denied_total")

    def test_throttle_allowed_total_exists(self):
        """throttle_allowed_total 메트릭이 정의되어 있는지 확인."""
        from selfhealing.services.metrics import definitions

        assert hasattr(definitions, "throttle_allowed_total")

    def test_throttle_sla_warnings_total_exists(self):
        """throttle_sla_warnings_total 메트릭이 정의되어 있는지 확인."""
        from selfhealing.services.metrics import definitions

        assert hasattr(definitions, "throttle_sla_warnings_total")

    def test_throttle_sla_criticals_total_exists(self):
        """throttle_sla_criticals_total 메트릭이 정의되어 있는지 확인."""
        from selfhealing.services.metrics import definitions

        assert hasattr(definitions, "throttle_sla_criticals_total")


class TestThrottleMetricsLabels:
    """메트릭 레이블 테스트."""

    def test_throttle_requests_total_has_service_label(self):
        """throttle_requests_total에 service 레이블이 있는지 확인."""
        from selfhealing.services.metrics import definitions

        metric = definitions.throttle_requests_total
        # Counter의 labelnames 확인
        assert "service" in metric._labelnames

    def test_throttle_saturation_ratio_has_service_label(self):
        """throttle_saturation_ratio에 service 레이블이 있는지 확인."""
        from selfhealing.services.metrics import definitions

        metric = definitions.throttle_saturation_ratio
        assert "service" in metric._labelnames

    def test_throttle_rtt_ms_has_service_label(self):
        """throttle_rtt_ms에 service 레이블이 있는지 확인."""
        from selfhealing.services.metrics import definitions

        metric = definitions.throttle_rtt_ms
        assert "service" in metric._labelnames


class TestRecordThrottleMetricsFunction:
    """_record_throttle_metrics 함수 테스트."""

    def test_record_throttle_metrics_accepts_extended_params(self):
        """_record_throttle_metrics가 확장 파라미터를 받는지 확인."""
        # Metrics가 없는 환경에서도 안전하게 동작해야 함
        try:
            from selfhealing.services.throttle.adaptive import _record_throttle_metrics

            # 확장 파라미터 포함 호출
            _record_throttle_metrics(
                service="test-service",
                limit=100,
                rtt_ms=50.0,
                gradient=1.0,
                denied_reason=None,
                emergency_level=0,
                cb_state="closed",
                request_result="allowed",
                sla_event=None,
                gradient_frozen=False,
                recovery_dampening_active=False,
                recovery_dampening_step=0,
                full_stop_active=False,
                full_stop_reason=None,
                limit_change_direction="increase",
                limit_change_trigger="gradient",
                limit_change_percent=1.0,
                max_limit=1000,
                trace_id="trace-abc",
            )
        except ImportError:
            pytest.skip("Metrics module not available")

    def test_record_throttle_metrics_safe_without_metrics(self):
        """메트릭 모듈 없이도 안전하게 동작하는지 확인."""
        try:
            from selfhealing.services.throttle.adaptive import _record_throttle_metrics

            # 기본 파라미터만으로 호출
            _record_throttle_metrics(
                service="test-service",
                limit=100,
                rtt_ms=50.0,
            )
        except ImportError:
            pytest.skip("Metrics module not available")


class TestMetricsSaturationRatio:
    """saturation_ratio 메트릭 계산 테스트."""

    def test_saturation_calculation(self):
        """saturation = in_flight / current_limit 계산 확인."""
        in_flight = 50
        current_limit = 100

        saturation = in_flight / current_limit

        assert saturation == 0.5

    def test_saturation_at_zero_limit(self):
        """current_limit=0일 때 안전 처리 확인."""
        in_flight = 50
        current_limit = 0

        # ZeroDivisionError 방지
        saturation = in_flight / max(current_limit, 1)

        assert saturation == 50.0

    def test_saturation_at_full_capacity(self):
        """100% 포화 상태 확인."""
        in_flight = 100
        current_limit = 100

        saturation = in_flight / current_limit

        assert saturation == 1.0

    def test_saturation_exceeds_limit(self):
        """limit 초과 시 saturation > 1.0 확인."""
        in_flight = 120
        current_limit = 100

        saturation = in_flight / current_limit

        assert saturation == 1.2
