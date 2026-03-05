"""
Unit tests for Time Series Metrics Provider.

검증 항목:
- TimeSeriesMetricsProvider Protocol 적합성
- MockTimeSeriesProvider: 데이터 주입 및 시간 필터링
- MockTimeSeriesProvider: 빈 데이터, 미등록 키
- MockTimeSeriesProvider: 스칼라 집계 메서드 (error_rate_agg, request_count, latency)
- get_metrics_provider / reset_metrics_provider 싱글톤 라이프사이클

테스트 대상: selfhealing.services.config_shadow.metrics_provider
"""

from datetime import datetime, timezone

import pytest

from selfhealing.services.config_shadow.metrics_provider import (
    MockTimeSeriesProvider,
    TimeSeriesMetricsProvider,
    get_metrics_provider,
    reset_metrics_provider,
    set_metrics_provider,
)


class TestTimeSeriesMetricsProviderContract:
    """TimeSeriesMetricsProvider Protocol 계약 검증."""

    def test_mock_provider_is_protocol_instance(self):
        """MockTimeSeriesProvider는 TimeSeriesMetricsProvider Protocol을 만족한다."""
        provider = MockTimeSeriesProvider()
        assert isinstance(provider, TimeSeriesMetricsProvider)


class TestMockTimeSeriesProviderBehavior:
    """MockTimeSeriesProvider 동작 검증."""

    def test_query_error_rate_returns_matching_data(self):
        """시간 범위 내 error_rate 데이터를 반환한다."""
        t1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc)
        t3 = datetime(2026, 1, 1, 10, 2, tzinfo=timezone.utc)

        provider = MockTimeSeriesProvider(
            data={"svc:error_rate": [(t1, 0.01), (t2, 0.05), (t3, 0.1)]}
        )

        result = provider.query_error_rate("svc", start=t1, end=t2)
        assert len(result) == 1
        assert result[0] == (t1, 0.01)

    def test_query_error_rate_empty_for_no_matching_key(self):
        """미등록 키: 빈 리스트 반환."""
        provider = MockTimeSeriesProvider()
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = provider.query_error_rate("unknown", start=t, end=t)
        assert result == []

    def test_query_request_rate_returns_matching_data(self):
        """시간 범위 내 request_rate 데이터를 반환한다."""
        t1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc)

        provider = MockTimeSeriesProvider(
            data={"svc:request_rate": [(t1, 100.0), (t2, 200.0)]}
        )

        result = provider.query_request_rate("svc", start=t1, end=t2)
        assert len(result) == 1
        assert result[0] == (t1, 100.0)

    def test_query_filters_by_start_end_exclusive(self):
        """end는 exclusive로 필터링된다 (start <= ts < end)."""
        t1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc)

        provider = MockTimeSeriesProvider(
            data={"svc:error_rate": [(t1, 0.01), (t2, 0.05)]}
        )

        # end=t2이면 t2는 제외
        result = provider.query_error_rate("svc", start=t1, end=t2)
        assert len(result) == 1

    def test_empty_provider_returns_empty(self):
        """데이터 없는 provider: 빈 리스트."""
        provider = MockTimeSeriesProvider()
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert provider.query_error_rate("any", t, t) == []
        assert provider.query_request_rate("any", t, t) == []


class TestMockTimeSeriesProviderScalarBehavior:
    """MockTimeSeriesProvider 스칼라 집계 메서드 동작 검증."""

    def test_query_error_rate_aggregated_returns_injected_value(self):
        """주입된 error_rate_agg 스칼라를 반환한다."""
        provider = MockTimeSeriesProvider()
        provider._scalars = {"svc:error_rate_agg": 0.035}
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)

        result = provider.query_error_rate_aggregated("svc", start=t, end=t)
        assert result == pytest.approx(0.035)

    def test_query_error_rate_aggregated_defaults_to_zero(self):
        """미등록 키: 0.0 반환."""
        provider = MockTimeSeriesProvider()
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert provider.query_error_rate_aggregated("unknown", t, t) == 0.0

    def test_query_request_count_returns_injected_value(self):
        """주입된 request_count 스칼라를 정수로 반환한다."""
        provider = MockTimeSeriesProvider()
        provider._scalars = {"svc:request_count": 500.0}
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)

        result = provider.query_request_count("svc", start=t, end=t)
        assert result == 500
        assert isinstance(result, int)

    def test_query_request_count_defaults_to_zero(self):
        """미등록 키: 0 반환."""
        provider = MockTimeSeriesProvider()
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert provider.query_request_count("unknown", t, t) == 0

    def test_query_latency_aggregated_p95_returns_correct_key(self):
        """percentile=0.95 시 'latency_p95' 키에서 값을 조회한다."""
        provider = MockTimeSeriesProvider()
        provider._scalars = {"svc:latency_p95": 42.5}
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)

        result = provider.query_latency_aggregated(
            "svc", start=t, end=t, percentile=0.95
        )
        assert result == pytest.approx(42.5)

    def test_query_latency_aggregated_p99_returns_correct_key(self):
        """percentile=0.99 시 'latency_p99' 키에서 값을 조회한다."""
        provider = MockTimeSeriesProvider()
        provider._scalars = {"svc:latency_p99": 150.0}
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)

        result = provider.query_latency_aggregated(
            "svc", start=t, end=t, percentile=0.99
        )
        assert result == pytest.approx(150.0)

    def test_query_latency_aggregated_defaults_to_zero(self):
        """미등록 latency 키: 0.0 반환."""
        provider = MockTimeSeriesProvider()
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert provider.query_latency_aggregated("unknown", t, t) == 0.0


class TestMetricsProviderSingletonBehavior:
    """get_metrics_provider / set / reset 싱글톤 라이프사이클 검증."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        """각 테스트 전후에 싱글톤을 리셋한다."""
        reset_metrics_provider()
        yield
        reset_metrics_provider()

    def test_get_returns_same_instance(self):
        """get_metrics_provider()는 동일 인스턴스를 반환한다."""
        first = get_metrics_provider()
        second = get_metrics_provider()
        assert first is second

    def test_get_returns_mock_by_default(self):
        """기본 provider는 MockTimeSeriesProvider이다."""
        provider = get_metrics_provider()
        assert isinstance(provider, MockTimeSeriesProvider)

    def test_reset_clears_cached_instance(self):
        """reset 후 새 인스턴스가 생성된다."""
        first = get_metrics_provider()
        reset_metrics_provider()
        second = get_metrics_provider()
        assert first is not second

    def test_set_overrides_provider(self):
        """set_metrics_provider()로 커스텀 provider를 등록할 수 있다."""
        custom = MockTimeSeriesProvider()
        set_metrics_provider(custom)
        assert get_metrics_provider() is custom
