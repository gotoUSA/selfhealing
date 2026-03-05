"""
Unit tests for Time Series Metrics Provider.

검증 항목:
- TimeSeriesMetricsProvider Protocol 적합성
- MockTimeSeriesProvider: 데이터 주입 및 시간 필터링
- MockTimeSeriesProvider: 빈 데이터, 미등록 키

테스트 대상: selfhealing.services.config_shadow.metrics_provider
"""

from datetime import datetime, timezone

from selfhealing.services.config_shadow.metrics_provider import (
    MockTimeSeriesProvider,
    TimeSeriesMetricsProvider,
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
