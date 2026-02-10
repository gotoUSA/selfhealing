"""
Postmortem Timeline Snapshot 단위 테스트.

테스트 대상:
- PrometheusMetricsCollector: Prometheus 쿼리, 피크 메트릭, 대시보드 링크
- IncidentLogBuffer: 로그 버퍼링, 기간별 조회
- SnapshotBuilder: 타임라인 스냅샷 빌드

외부 의존성은 모킹하여 순수 단위 테스트로 작성합니다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest


class TestPrometheusMetricsCollector:
    """PrometheusMetricsCollector 단위 테스트."""

    def test_init_with_custom_settings(self):
        """커스텀 설정으로 초기화."""
        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector(
            prometheus_url="http://custom:9090",
            timeout=20,
            grafana_base_url="http://grafana-custom:3000",
            grafana_dashboard_uid="custom-dashboard",
        )

        assert collector.prometheus_url == "http://custom:9090"
        assert collector.timeout == 20
        assert collector.grafana_base_url == "http://grafana-custom:3000"
        assert collector.grafana_dashboard_uid == "custom-dashboard"

    def test_query_instant_disabled(self):
        """Prometheus 비활성화 시 빈 결과 반환."""
        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector()

        with mock.patch.object(collector, "is_enabled", return_value=False):
            result = collector.query_instant("test_query")

        assert result.success is False
        assert result.data == []
        assert "disabled" in result.error_message.lower()

    def test_query_instant_success(self):
        """정상 Prometheus 쿼리."""
        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector(prometheus_url="http://test:9090")

        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "success",
            "data": {"result": [{"metric": {"name": "test"}, "value": [1234567890, "42.5"]}]},
        }

        with mock.patch.object(collector, "is_enabled", return_value=True):
            with mock.patch("requests.get", return_value=mock_response):
                result = collector.query_instant("test_query")

        assert result.success is True
        assert len(result.data) == 1
        assert result.data[0]["value"][1] == "42.5"

    def test_query_instant_timeout(self):
        """Prometheus 쿼리 타임아웃 처리."""
        import requests

        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector(prometheus_url="http://test:9090")

        with mock.patch.object(collector, "is_enabled", return_value=True):
            with mock.patch("requests.get", side_effect=requests.exceptions.Timeout):
                result = collector.query_instant("test_query")

        assert result.success is False
        assert "timeout" in result.error_message.lower()

    def test_query_instant_connection_error(self):
        """Prometheus 연결 실패 처리."""
        import requests

        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector(prometheus_url="http://test:9090")

        with mock.patch.object(collector, "is_enabled", return_value=True):
            with mock.patch(
                "requests.get",
                side_effect=requests.exceptions.ConnectionError("Connection refused"),
            ):
                result = collector.query_instant("test_query")

        assert result.success is False
        assert "connection" in result.error_message.lower()

    def test_query_range_success(self):
        """시간 범위 쿼리 성공."""
        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector(prometheus_url="http://test:9090")

        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"name": "test"},
                        "values": [[1234567890, "10"], [1234567950, "20"]],
                    }
                ]
            },
        }

        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        with mock.patch.object(collector, "is_enabled", return_value=True):
            with mock.patch("requests.get", return_value=mock_response):
                result = collector.query_range("test_query", start, end)

        assert result.success is True
        assert len(result.data) == 1

    def test_get_peak_metrics_disabled(self):
        """Prometheus 비활성화 시 피크 메트릭 조회."""
        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector()

        with mock.patch.object(collector, "is_enabled", return_value=False):
            start = datetime.now(timezone.utc) - timedelta(hours=1)
            end = datetime.now(timezone.utc)
            peak = collector.get_peak_metrics(start, end)

        assert peak.query_error is not None
        assert "disabled" in peak.query_error.lower()

    def test_generate_dashboard_link(self):
        """Grafana 대시보드 링크 생성."""
        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector(
            grafana_base_url="http://grafana:3000",
            grafana_dashboard_uid="selfhealing",
        )

        start = datetime(2026, 1, 28, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 28, 11, 0, 0, tzinfo=timezone.utc)

        link = collector.generate_dashboard_link(start, end, service="database")

        assert "http://grafana:3000" in link
        assert "selfhealing" in link
        assert "var-service=database" in link
        # Unix milliseconds 확인
        start_ms = str(int(start.timestamp() * 1000))
        end_ms = str(int(end.timestamp() * 1000))
        assert f"from={start_ms}" in link
        assert f"to={end_ms}" in link

    def test_generate_prometheus_link(self):
        """Prometheus UI 링크 생성."""
        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector(prometheus_url="http://prometheus:9090")

        start = datetime(2026, 1, 28, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 28, 11, 0, 0, tzinfo=timezone.utc)

        link = collector.generate_prometheus_link("selfhealing_error_rate_percent", start, end)

        assert "http://prometheus:9090" in link
        assert "g0.expr" in link
        assert "selfhealing_error_rate_percent" in link

    def test_generate_dashboard_links_all(self):
        """전체 대시보드 링크 모음 생성."""
        from selfhealing.services.postmortem.prometheus_collector import (
            PrometheusMetricsCollector,
        )

        collector = PrometheusMetricsCollector(
            prometheus_url="http://prometheus:9090",
            grafana_base_url="http://grafana:3000",
            grafana_dashboard_uid="selfhealing",
        )

        start = datetime(2026, 1, 28, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 28, 11, 0, 0, tzinfo=timezone.utc)

        links = collector.generate_dashboard_links(start, end, service="database")

        assert "grafana_overview" in links
        assert "grafana_service" in links
        assert "prometheus_error_rate" in links


class TestIncidentLogBuffer:
    """IncidentLogBuffer 단위 테스트."""

    def test_add_and_get_logs(self):
        """로그 추가 및 조회."""
        from selfhealing.services.postmortem.log_buffer import (
            reset_incident_log_buffer,
            get_incident_log_buffer,
        )

        reset_incident_log_buffer()
        buffer = get_incident_log_buffer()

        # 로그 추가
        buffer.add_log(
            level="ERROR",
            message="Test error message",
            service="test-service",
            trace_id="trace-123",
        )

        # 최근 로그 조회
        logs = buffer.get_recent_logs(count=10)

        assert len(logs) == 1
        assert logs[0]["level"] == "ERROR"
        assert logs[0]["message"] == "Test error message"
        assert logs[0]["service"] == "test-service"
        assert logs[0]["trace_id"] == "trace-123"

        reset_incident_log_buffer()

    def test_message_truncation(self):
        """긴 메시지 잘림 처리."""
        from selfhealing.services.postmortem.log_buffer import (
            reset_incident_log_buffer,
            get_incident_log_buffer,
        )

        reset_incident_log_buffer()
        buffer = get_incident_log_buffer()

        # 긴 메시지 추가 (500자 초과)
        long_message = "A" * 1000
        buffer.add_log(level="ERROR", message=long_message, service="test")

        logs = buffer.get_recent_logs(count=1)

        # 메시지가 잘렸는지 확인
        assert len(logs[0]["message"]) <= 500
        assert logs[0]["message"].endswith("...")

        reset_incident_log_buffer()

    def test_get_logs_for_period(self):
        """기간 기반 로그 조회."""
        from selfhealing.services.postmortem.log_buffer import (
            reset_incident_log_buffer,
            get_incident_log_buffer,
        )

        reset_incident_log_buffer()
        buffer = get_incident_log_buffer()

        now = datetime.now(timezone.utc)

        # 시간대별 로그 추가
        buffer.add_log(
            level="ERROR",
            message="Old error",
            service="test",
            timestamp=now - timedelta(hours=2),
        )
        buffer.add_log(
            level="ERROR",
            message="Recent error",
            service="test",
            timestamp=now - timedelta(minutes=30),
        )
        buffer.add_log(
            level="ERROR",
            message="Very recent error",
            service="test",
            timestamp=now - timedelta(minutes=5),
        )

        # 1시간 내 로그 조회
        start = now - timedelta(hours=1)
        end = now
        logs = buffer.get_logs_for_period(start, end)

        assert len(logs) == 2  # Old error 제외
        assert any("Recent error" in log["message"] for log in logs)
        assert any("Very recent error" in log["message"] for log in logs)

        reset_incident_log_buffer()

    def test_clear_old_logs(self):
        """오래된 로그 정리."""
        from selfhealing.services.postmortem.log_buffer import (
            reset_incident_log_buffer,
            get_incident_log_buffer,
        )

        reset_incident_log_buffer()
        buffer = get_incident_log_buffer()

        now = datetime.now(timezone.utc)

        # 오래된 로그 추가
        buffer.add_log(
            level="ERROR",
            message="Old error",
            service="test",
            timestamp=now - timedelta(hours=2),
        )
        buffer.add_log(
            level="ERROR",
            message="Recent error",
            service="test",
            timestamp=now,
        )

        # 1시간 이전 로그 삭제
        deleted = buffer.clear_old_logs(before=now - timedelta(hours=1))

        assert deleted == 1
        assert buffer.get_buffer_size() == 1

        reset_incident_log_buffer()

    def test_buffer_size_limit(self):
        """버퍼 크기 제한."""
        from selfhealing.services.postmortem.log_buffer import (
            reset_incident_log_buffer,
            get_incident_log_buffer,
        )

        reset_incident_log_buffer()
        buffer = get_incident_log_buffer()
        buffer._buffer = buffer._buffer.__class__(maxlen=10)  # 테스트용으로 제한

        # 제한보다 많은 로그 추가
        for i in range(15):
            buffer.add_log(level="ERROR", message=f"Error {i}", service="test")

        # 최대 10개만 유지
        assert buffer.get_buffer_size() == 10

        reset_incident_log_buffer()


class TestIncidentLogHandler:
    """IncidentLogHandler 단위 테스트."""

    def test_handler_captures_error_logs(self):
        """logging.Handler로 ERROR 로그 캡처."""
        from selfhealing.services.postmortem.log_buffer import (
            IncidentLogHandler,
            reset_incident_log_buffer,
            get_incident_log_buffer,
        )

        reset_incident_log_buffer()
        buffer = get_incident_log_buffer()

        # 핸들러 생성 및 로거에 추가
        handler = IncidentLogHandler(level=logging.ERROR, service_name="test-handler")
        test_logger = logging.getLogger("test.incident.handler")
        original_level = test_logger.level
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.DEBUG)

        try:
            # 로그 발생
            test_logger.error("Test error via handler")
            test_logger.warning("This should not be captured")

            # 버퍼 확인
            logs = buffer.get_recent_logs(count=10)

            assert len(logs) == 1
            assert "Test error via handler" in logs[0]["message"]
            assert logs[0]["level"] == "ERROR"
            assert logs[0]["service"] == "test-handler"

        finally:
            test_logger.removeHandler(handler)
            test_logger.setLevel(original_level)
            reset_incident_log_buffer()


class TestSnapshotBuilder:
    """SnapshotBuilder 단위 테스트."""

    def test_build_without_redis(self):
        """Redis 없이 스냅샷 빌드."""
        from selfhealing.services.postmortem.snapshot_builder import SnapshotBuilder

        builder = SnapshotBuilder(
            service_name="test-service",
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            end_time=datetime.now(timezone.utc),
        )

        # 모든 내부 메서드 모킹
        mock_close_snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu_percent": 45.0,
            "memory_percent": 60.0,
        }

        with mock.patch.object(builder, "_get_redis_client", return_value=None):
            with mock.patch.object(builder, "_collect_close_snapshot", return_value=mock_close_snapshot):
                with mock.patch.object(builder, "_query_prometheus_peaks", return_value={}):
                    with mock.patch.object(builder, "_collect_error_logs", return_value=[]):
                        with mock.patch.object(builder, "_generate_dashboard_links", return_value={}):
                            snapshot = builder.build()

        assert snapshot.metrics_at_open == {}  # Redis 없음
        assert snapshot.metrics_at_close["cpu_percent"] == 45.0
        assert snapshot.metrics_at_close["memory_percent"] == 60.0

    def test_build_dict_format(self):
        """딕셔너리 형태로 스냅샷 빌드."""
        from selfhealing.services.postmortem.snapshot_builder import SnapshotBuilder

        builder = SnapshotBuilder(
            service_name="test-service",
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            end_time=datetime.now(timezone.utc),
        )

        mock_close_snapshot = {"timestamp": "2026-01-28T10:00:00Z", "cpu_percent": 30.0}

        with mock.patch.object(builder, "_get_redis_client", return_value=None):
            with mock.patch.object(builder, "_collect_close_snapshot", return_value=mock_close_snapshot):
                with mock.patch.object(builder, "_query_prometheus_peaks", return_value={}):
                    with mock.patch.object(builder, "_collect_error_logs", return_value=[]):
                        with mock.patch.object(builder, "_generate_dashboard_links", return_value={}):
                            result = builder.build_dict()

        assert isinstance(result, dict)
        assert "events" in result
        assert "metrics_at_open" in result
        assert "metrics_at_close" in result
        assert "peak_metrics" in result
        assert "captured_logs" in result
        assert "dashboard_links" in result

    def test_build_with_timeline_events(self):
        """타임라인 이벤트 포함 빌드."""
        from selfhealing.services.postmortem.snapshot_builder import SnapshotBuilder

        timeline = [
            {"timestamp": "2026-01-28T10:00:00Z", "event_type": "cb_opened"},
            {"timestamp": "2026-01-28T10:30:00Z", "event_type": "cb_closed"},
        ]

        builder = SnapshotBuilder(
            service_name="test-service",
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            end_time=datetime.now(timezone.utc),
        )

        mock_close_snapshot = {"timestamp": "2026-01-28T10:30:00Z", "cpu_percent": 25.0}

        with mock.patch.object(builder, "_get_redis_client", return_value=None):
            with mock.patch.object(builder, "_collect_close_snapshot", return_value=mock_close_snapshot):
                with mock.patch.object(builder, "_query_prometheus_peaks", return_value={}):
                    with mock.patch.object(builder, "_collect_error_logs", return_value=[]):
                        with mock.patch.object(builder, "_generate_dashboard_links", return_value={}):
                            snapshot = builder.build(timeline_events=timeline)

        assert len(snapshot.events) == 2
        assert snapshot.events[0]["event_type"] == "cb_opened"

    def test_build_with_prometheus_peaks(self):
        """Prometheus 피크 메트릭 포함 빌드."""
        from selfhealing.services.postmortem.snapshot_builder import SnapshotBuilder

        builder = SnapshotBuilder(
            service_name="test-service",
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            end_time=datetime.now(timezone.utc),
        )

        mock_close_snapshot = {"timestamp": "2026-01-28T10:30:00Z", "cpu_percent": 25.0}
        mock_peak_metrics = {
            "max_cpu_percent": 85.0,
            "max_memory_mb": 1024.0,
            "max_error_rate_percent": 5.5,
        }

        with mock.patch.object(builder, "_get_redis_client", return_value=None):
            with mock.patch.object(builder, "_collect_close_snapshot", return_value=mock_close_snapshot):
                with mock.patch.object(builder, "_query_prometheus_peaks", return_value=mock_peak_metrics):
                    with mock.patch.object(builder, "_collect_error_logs", return_value=[]):
                        with mock.patch.object(builder, "_generate_dashboard_links", return_value={}):
                            snapshot = builder.build()

        assert snapshot.peak_metrics["max_cpu_percent"] == 85.0
        assert snapshot.peak_metrics["max_memory_mb"] == 1024.0
        assert snapshot.peak_metrics["max_error_rate_percent"] == 5.5

    def test_build_with_captured_logs(self):
        """캡처된 에러 로그 포함 빌드."""
        from selfhealing.services.postmortem.snapshot_builder import SnapshotBuilder

        builder = SnapshotBuilder(
            service_name="test-service",
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            end_time=datetime.now(timezone.utc),
        )

        mock_close_snapshot = {"timestamp": "2026-01-28T10:30:00Z", "cpu_percent": 25.0}
        mock_logs = [
            {"timestamp": "2026-01-28T10:15:00Z", "level": "ERROR", "message": "Connection failed"},
            {"timestamp": "2026-01-28T10:20:00Z", "level": "CRITICAL", "message": "Service unavailable"},
        ]

        with mock.patch.object(builder, "_get_redis_client", return_value=None):
            with mock.patch.object(builder, "_collect_close_snapshot", return_value=mock_close_snapshot):
                with mock.patch.object(builder, "_query_prometheus_peaks", return_value={}):
                    with mock.patch.object(builder, "_collect_error_logs", return_value=mock_logs):
                        with mock.patch.object(builder, "_generate_dashboard_links", return_value={}):
                            snapshot = builder.build()

        assert len(snapshot.captured_logs) == 2
        assert snapshot.captured_logs[0]["level"] == "ERROR"
        assert snapshot.captured_logs[1]["level"] == "CRITICAL"

    def test_build_with_dashboard_links(self):
        """대시보드 링크 포함 빌드."""
        from selfhealing.services.postmortem.snapshot_builder import SnapshotBuilder

        builder = SnapshotBuilder(
            service_name="test-service",
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            end_time=datetime.now(timezone.utc),
        )

        mock_close_snapshot = {"timestamp": "2026-01-28T10:30:00Z", "cpu_percent": 25.0}
        mock_links = {
            "grafana_overview": "http://grafana:3000/d/selfhealing/overview?from=...",
            "grafana_service": "http://grafana:3000/d/selfhealing-service/service?...",
            "prometheus_error_rate": "http://prometheus:9090/graph?...",
        }

        with mock.patch.object(builder, "_get_redis_client", return_value=None):
            with mock.patch.object(builder, "_collect_close_snapshot", return_value=mock_close_snapshot):
                with mock.patch.object(builder, "_query_prometheus_peaks", return_value={}):
                    with mock.patch.object(builder, "_collect_error_logs", return_value=[]):
                        with mock.patch.object(builder, "_generate_dashboard_links", return_value=mock_links):
                            snapshot = builder.build()

        assert "grafana_overview" in snapshot.dashboard_links
        assert "grafana_service" in snapshot.dashboard_links
        assert "prometheus_error_rate" in snapshot.dashboard_links


class TestTimelineSnapshot:
    """TimelineSnapshot 데이터클래스 테스트."""

    def test_to_dict(self):
        """딕셔너리 변환."""
        from selfhealing.services.postmortem.snapshot_builder import TimelineSnapshot

        snapshot = TimelineSnapshot(
            events=[{"event": "test"}],
            metrics_at_open={"cpu": 50},
            metrics_at_close={"cpu": 30},
            peak_metrics={"max_cpu": 80},
            captured_logs=[{"message": "error"}],
            dashboard_links={"grafana": "http://example.com"},
        )

        result = snapshot.to_dict()

        assert result["events"] == [{"event": "test"}]
        assert result["metrics_at_open"] == {"cpu": 50}
        assert result["metrics_at_close"] == {"cpu": 30}
        assert result["peak_metrics"] == {"max_cpu": 80}
        assert result["captured_logs"] == [{"message": "error"}]
        assert result["dashboard_links"] == {"grafana": "http://example.com"}


class TestSnapshotRedisOperations:
    """Redis 스냅샷 저장/조회 테스트."""

    def test_save_open_snapshot_to_redis(self):
        """CB OPEN 스냅샷 Redis 저장."""
        from selfhealing.services.postmortem.snapshot_builder import (
            save_open_snapshot_to_redis,
            SnapshotBuilder,
        )

        mock_redis = mock.Mock()

        with mock.patch(
            "selfhealing.services.postmortem.snapshot_builder.get_redis_client",
            return_value=mock_redis,
        ):
            result = save_open_snapshot_to_redis(
                service_name="test-service",
                snapshot_data={"cpu_percent": 45.0, "memory_percent": 60.0},
            )

        assert result is True
        mock_redis.hset.assert_called()
        mock_redis.expire.assert_called_once_with(
            SnapshotBuilder.OPEN_SNAPSHOT_KEY_PATTERN.format(service="test-service"),
            SnapshotBuilder.OPEN_SNAPSHOT_TTL,
        )

    def test_save_open_snapshot_no_redis(self):
        """Redis 없을 때 저장 실패."""
        from selfhealing.services.postmortem.snapshot_builder import (
            save_open_snapshot_to_redis,
        )

        with mock.patch(
            "selfhealing.services.postmortem.snapshot_builder.get_redis_client",
            return_value=None,
        ):
            result = save_open_snapshot_to_redis(
                service_name="test-service",
                snapshot_data={"cpu_percent": 45.0},
            )

        assert result is False

    def test_delete_open_snapshot_from_redis(self):
        """CB OPEN 스냅샷 Redis 삭제."""
        from selfhealing.services.postmortem.snapshot_builder import (
            delete_open_snapshot_from_redis,
        )

        mock_redis = mock.Mock()

        with mock.patch(
            "selfhealing.services.postmortem.snapshot_builder.get_redis_client",
            return_value=mock_redis,
        ):
            result = delete_open_snapshot_from_redis("test-service")

        assert result is True
        mock_redis.delete.assert_called_once()


class TestPostmortemSettingsSnapshot:
    """PostmortemSettings 스냅샷 설정 테스트."""

    def test_snapshot_settings_defaults(self):
        """스냅샷 설정 기본값."""
        from selfhealing.settings.postmortem import PostmortemSettings

        settings = PostmortemSettings()

        # Prometheus 설정
        assert settings.snapshot_prometheus_enabled is True
        assert settings.snapshot_prometheus_url == "http://prometheus:9090"
        assert settings.snapshot_prometheus_timeout == 10

        # 로그 수집 설정
        assert settings.snapshot_logs_enabled is True
        assert settings.snapshot_logs_max_count == 50
        assert settings.snapshot_logs_max_length == 500

        # Grafana 설정
        assert settings.snapshot_grafana_base_url == "http://grafana:3000"
        assert settings.snapshot_grafana_dashboard_uid == "selfhealing"

    def test_snapshot_settings_from_env(self):
        """환경 변수에서 스냅샷 설정 로드."""
        import os
        from selfhealing.settings.postmortem import PostmortemSettings

        env_vars = {
            "SELFHEALING_POSTMORTEM_SNAPSHOT_PROMETHEUS_ENABLED": "false",
            "SELFHEALING_POSTMORTEM_SNAPSHOT_PROMETHEUS_URL": "http://custom-prom:9090",
            "SELFHEALING_POSTMORTEM_SNAPSHOT_PROMETHEUS_TIMEOUT": "30",
            "SELFHEALING_POSTMORTEM_SNAPSHOT_LOGS_ENABLED": "false",
            "SELFHEALING_POSTMORTEM_SNAPSHOT_LOGS_MAX_COUNT": "100",
            "SELFHEALING_POSTMORTEM_SNAPSHOT_GRAFANA_BASE_URL": "http://custom-grafana:3000",
            "SELFHEALING_POSTMORTEM_SNAPSHOT_GRAFANA_DASHBOARD_UID": "custom-dash",
        }

        with mock.patch.dict(os.environ, env_vars):
            settings = PostmortemSettings()

        assert settings.snapshot_prometheus_enabled is False
        assert settings.snapshot_prometheus_url == "http://custom-prom:9090"
        assert settings.snapshot_prometheus_timeout == 30
        assert settings.snapshot_logs_enabled is False
        assert settings.snapshot_logs_max_count == 100
        assert settings.snapshot_grafana_base_url == "http://custom-grafana:3000"
        assert settings.snapshot_grafana_dashboard_uid == "custom-dash"
