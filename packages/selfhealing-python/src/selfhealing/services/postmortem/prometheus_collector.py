"""
Prometheus Metrics Collector for Postmortem Timeline Snapshot.

Postmortem 생성 시 인시던트 기간 동안의 메트릭을 Prometheus에서 수집하고,
Grafana 대시보드 링크를 생성합니다.

Features:
- 시간 범위 PromQL 쿼리 실행
- 인시던트 기간 피크 메트릭 조회
- Grafana/Prometheus 대시보드 링크 생성
- 연결 실패 시 graceful fallback
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import requests

logger = logging.getLogger(__name__)


@dataclass
class PrometheusQueryResult:
    """Prometheus 쿼리 결과."""

    success: bool
    data: list[dict[str, Any]]
    error_message: str | None = None


@dataclass
class PeakMetrics:
    """인시던트 기간 피크 메트릭."""

    max_cpu_percent: float | None = None
    max_memory_percent: float | None = None
    max_error_rate_percent: float | None = None
    max_latency_p99_seconds: float | None = None
    query_error: str | None = None


class PrometheusMetricsCollector:
    """
    Prometheus에서 인시던트 기간 메트릭을 수집하는 Collector.

    Settings에서 Prometheus URL과 타임아웃을 가져오며,
    연결 실패 시 빈 결과를 반환하는 graceful fallback을 제공합니다.
    """

    def __init__(
        self,
        prometheus_url: str | None = None,
        timeout: int | None = None,
        grafana_base_url: str | None = None,
        grafana_dashboard_uid: str | None = None,
    ):
        """
        PrometheusMetricsCollector 초기화.

        Args:
            prometheus_url: Prometheus 서버 URL (설정에서 로드하지 않을 경우)
            timeout: 쿼리 타임아웃 (초)
            grafana_base_url: Grafana 서버 URL
            grafana_dashboard_uid: Grafana 대시보드 UID
        """
        self._prometheus_url = prometheus_url
        self._timeout = timeout
        self._grafana_base_url = grafana_base_url
        self._grafana_dashboard_uid = grafana_dashboard_uid

    def _get_settings(self):
        """PostmortemSettings에서 설정 로드."""
        try:
            from selfhealing.settings.postmortem import get_postmortem_settings

            return get_postmortem_settings()
        except ImportError:
            return None

    @property
    def prometheus_url(self) -> str:
        """Prometheus URL 반환."""
        if self._prometheus_url:
            return self._prometheus_url
        settings = self._get_settings()
        if settings:
            return getattr(settings, "snapshot_prometheus_url", "http://prometheus:9090")
        return "http://prometheus:9090"

    @property
    def timeout(self) -> int:
        """쿼리 타임아웃 반환."""
        if self._timeout:
            return self._timeout
        settings = self._get_settings()
        if settings:
            return getattr(settings, "snapshot_prometheus_timeout", 10)
        return 10

    @property
    def grafana_base_url(self) -> str:
        """Grafana URL 반환."""
        if self._grafana_base_url:
            return self._grafana_base_url
        settings = self._get_settings()
        if settings:
            return getattr(settings, "snapshot_grafana_base_url", "http://grafana:3000")
        return "http://grafana:3000"

    @property
    def grafana_dashboard_uid(self) -> str:
        """Grafana 대시보드 UID 반환."""
        if self._grafana_dashboard_uid:
            return self._grafana_dashboard_uid
        settings = self._get_settings()
        if settings:
            return getattr(settings, "snapshot_grafana_dashboard_uid", "selfhealing")
        return "selfhealing"

    def is_enabled(self) -> bool:
        """Prometheus 쿼리가 활성화되어 있는지 확인."""
        settings = self._get_settings()
        if settings:
            return getattr(settings, "snapshot_prometheus_enabled", True)
        return True

    def query_instant(
        self,
        query: str,
        timestamp: datetime | None = None,
    ) -> PrometheusQueryResult:
        """
        특정 시점 PromQL 쿼리 실행.

        Args:
            query: PromQL 쿼리 문자열
            timestamp: 쿼리 시점 (None이면 현재 시각)

        Returns:
            쿼리 결과
        """
        if not self.is_enabled():
            return PrometheusQueryResult(
                success=False,
                data=[],
                error_message="Prometheus query disabled",
            )

        try:
            url = f"{self.prometheus_url}/api/v1/query"
            params = {"query": query}
            if timestamp:
                params["time"] = timestamp.timestamp()

            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()

            result = response.json()
            if result.get("status") == "success":
                data = result.get("data", {}).get("result", [])
                return PrometheusQueryResult(success=True, data=data)
            else:
                return PrometheusQueryResult(
                    success=False,
                    data=[],
                    error_message=result.get("error", "Unknown error"),
                )

        except requests.exceptions.Timeout:
            logger.warning(f"[PrometheusCollector] Query timeout: {query}")
            return PrometheusQueryResult(
                success=False,
                data=[],
                error_message="Query timeout",
            )
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"[PrometheusCollector] Connection error: {e}")
            return PrometheusQueryResult(
                success=False,
                data=[],
                error_message=f"Connection error: {e}",
            )
        except Exception as e:
            logger.error(f"[PrometheusCollector] Query failed: {e}")
            return PrometheusQueryResult(
                success=False,
                data=[],
                error_message=str(e),
            )

    def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "60s",
    ) -> PrometheusQueryResult:
        """
        시간 범위 PromQL 쿼리 실행.

        Args:
            query: PromQL 쿼리 문자열
            start: 시작 시각
            end: 종료 시각
            step: 샘플링 간격 (예: "60s", "1m")

        Returns:
            쿼리 결과
        """
        if not self.is_enabled():
            return PrometheusQueryResult(
                success=False,
                data=[],
                error_message="Prometheus query disabled",
            )

        try:
            url = f"{self.prometheus_url}/api/v1/query_range"
            params = {
                "query": query,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            }

            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()

            result = response.json()
            if result.get("status") == "success":
                data = result.get("data", {}).get("result", [])
                return PrometheusQueryResult(success=True, data=data)
            else:
                return PrometheusQueryResult(
                    success=False,
                    data=[],
                    error_message=result.get("error", "Unknown error"),
                )

        except requests.exceptions.Timeout:
            logger.warning(f"[PrometheusCollector] Range query timeout: {query}")
            return PrometheusQueryResult(
                success=False,
                data=[],
                error_message="Query timeout",
            )
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"[PrometheusCollector] Connection error: {e}")
            return PrometheusQueryResult(
                success=False,
                data=[],
                error_message=f"Connection error: {e}",
            )
        except Exception as e:
            logger.error(f"[PrometheusCollector] Range query failed: {e}")
            return PrometheusQueryResult(
                success=False,
                data=[],
                error_message=str(e),
            )

    def _query_and_parse_metric(
        self,
        query: str,
        end: datetime,
        metric_name: str,
        multiplier: float = 1.0,
    ) -> tuple[float | None, str | None]:
        """메트릭 쿼리 및 파싱. (값, 에러메시지) 반환."""
        result = self.query_instant(query, end)
        if result.success and result.data:
            try:
                value = float(result.data[0].get("value", [0, 0])[1])
                return value * multiplier, None
            except (IndexError, ValueError, TypeError):
                return None, f"{metric_name} parse error"
        elif result.error_message:
            return None, f"{metric_name}: {result.error_message}"
        return None, None

    def get_peak_metrics(
        self,
        start: datetime,
        end: datetime,
    ) -> PeakMetrics:
        """
        인시던트 기간 동안의 피크 메트릭 조회.

        max_over_time 함수를 사용하여 기간 내 최대값을 조회합니다.

        Args:
            start: 인시던트 시작 시각
            end: 인시던트 종료 시각

        Returns:
            피크 메트릭
        """
        if not self.is_enabled():
            return PeakMetrics(query_error="Prometheus query disabled")

        duration_seconds = int((end - start).total_seconds())
        if duration_seconds <= 0:
            duration_seconds = 60
        duration = f"{duration_seconds}s"

        peak = PeakMetrics()
        errors = []

        # CPU 최대값
        cpu_val, cpu_err = self._query_and_parse_metric(
            f"max_over_time(process_cpu_seconds_total[{duration}])", end, "CPU", 100
        )
        if cpu_val is not None:
            peak.max_cpu_percent = cpu_val
        if cpu_err:
            errors.append(cpu_err)

        # 메모리 최대값 (바이트 -> MB 변환)
        mem_val, mem_err = self._query_and_parse_metric(
            f"max_over_time(process_resident_memory_bytes[{duration}])", end, "Memory", 1 / (1024 * 1024)
        )
        if mem_val is not None:
            peak.max_memory_percent = mem_val
        if mem_err:
            errors.append(mem_err)

        # 에러율 최대값
        err_val, err_err = self._query_and_parse_metric(
            f"max_over_time(selfhealing_error_rate_percent[{duration}])", end, "Error rate"
        )
        if err_val is not None:
            peak.max_error_rate_percent = err_val
        if err_err:
            errors.append(err_err)

        # P99 지연 최대값
        latency_query = (
            f"max_over_time("
            f"histogram_quantile(0.99, rate(selfhealing_http_request_duration_seconds_bucket[1m]))"
            f"[{duration}])"
        )
        lat_val, lat_err = self._query_and_parse_metric(latency_query, end, "Latency")
        if lat_val is not None:
            peak.max_latency_p99_seconds = lat_val
        if lat_err:
            errors.append(lat_err)

        if errors:
            peak.query_error = "; ".join(errors)

        return peak

    def generate_dashboard_link(
        self,
        start: datetime,
        end: datetime,
        service: str | None = None,
        dashboard_type: str = "overview",
    ) -> str:
        """
        시간 범위가 고정된 Grafana 대시보드 URL 생성.

        Args:
            start: 시작 시각
            end: 종료 시각
            service: 서비스 필터 (선택)
            dashboard_type: 대시보드 타입 ("overview", "service")

        Returns:
            Grafana 대시보드 URL
        """
        # Unix milliseconds 변환
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        # 기본 URL 구성
        dashboard_uid = self.grafana_dashboard_uid
        if dashboard_type == "service" and service:
            dashboard_uid = f"{dashboard_uid}-service"

        params = {
            "orgId": "1",
            "from": str(start_ms),
            "to": str(end_ms),
        }

        if service:
            params["var-service"] = service

        query_string = urlencode(params)
        return f"{self.grafana_base_url}/d/{dashboard_uid}/{dashboard_type}?{query_string}"

    def generate_prometheus_link(
        self,
        query: str,
        start: datetime,
        end: datetime,
    ) -> str:
        """
        Prometheus UI 쿼리 링크 생성.

        Args:
            query: PromQL 쿼리
            start: 시작 시각
            end: 종료 시각

        Returns:
            Prometheus UI URL
        """
        duration_seconds = int((end - start).total_seconds())
        end_iso = end.isoformat()

        params = {
            "g0.expr": query,
            "g0.range_input": f"{duration_seconds}s",
            "g0.end_input": end_iso,
            "g0.tab": "0",
        }

        query_string = urlencode(params, quote_via=quote)
        return f"{self.prometheus_url}/graph?{query_string}"

    def generate_dashboard_links(
        self,
        start: datetime,
        end: datetime,
        service: str | None = None,
    ) -> dict[str, str]:
        """
        Postmortem에 포함할 대시보드 링크 모음 생성.

        Args:
            start: 인시던트 시작 시각
            end: 인시던트 종료 시각
            service: 관련 서비스 (선택)

        Returns:
            대시보드 링크 딕셔너리
        """
        links = {
            "grafana_overview": self.generate_dashboard_link(start, end, dashboard_type="overview"),
        }

        if service:
            links["grafana_service"] = self.generate_dashboard_link(start, end, service=service, dashboard_type="service")

        # 기본 Prometheus 쿼리 링크
        error_query = "selfhealing_error_rate_percent"
        links["prometheus_error_rate"] = self.generate_prometheus_link(error_query, start, end)

        return links


# =============================================================================
# Singleton & Factory
# =============================================================================

_collector: PrometheusMetricsCollector | None = None


def get_prometheus_collector() -> PrometheusMetricsCollector:
    """PrometheusMetricsCollector 싱글톤 반환."""
    global _collector
    if _collector is None:
        _collector = PrometheusMetricsCollector()
    return _collector


def reset_prometheus_collector() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _collector
    _collector = None


__all__ = [
    "PrometheusMetricsCollector",
    "PrometheusQueryResult",
    "PeakMetrics",
    "get_prometheus_collector",
    "reset_prometheus_collector",
]
