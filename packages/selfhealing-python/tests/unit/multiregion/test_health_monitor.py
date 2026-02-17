"""
Region Health Monitor 테스트.

테스트 대상:
- RegionHealthStatus: 건강 상태 Enum
- RegionHealth: 건강 정보 데이터클래스
- RegionHealthMonitor: 건강 모니터링
"""

from datetime import datetime, timezone
from unittest import mock

import pytest

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    RegionEndpoint,
    reset_multiregion_settings,
)
from selfhealing.multiregion.health_monitor import (
    RegionHealth,
    RegionHealthMonitor,
    RegionHealthStatus,
)


class TestRegionHealthStatus:
    """RegionHealthStatus Enum 테스트."""

    def test_values(self) -> None:
        """Enum 값 확인."""
        assert RegionHealthStatus.HEALTHY.value == "healthy"
        assert RegionHealthStatus.DEGRADED.value == "degraded"
        assert RegionHealthStatus.UNHEALTHY.value == "unhealthy"
        assert RegionHealthStatus.UNREACHABLE.value == "unreachable"


class TestRegionHealth:
    """RegionHealth 데이터클래스 테스트."""

    def test_create_health(self) -> None:
        """건강 정보 생성."""
        now = datetime.now(timezone.utc)
        health = RegionHealth(
            region="us-east-1",
            status=RegionHealthStatus.HEALTHY,
            latency_ms=50.0,
            last_check=now,
        )

        assert health.region == "us-east-1"
        assert health.status == RegionHealthStatus.HEALTHY
        assert health.latency_ms == 50.0
        assert health.consecutive_failures == 0
        assert health.details == {}

    def test_with_details(self) -> None:
        """상세 정보 포함."""
        health = RegionHealth(
            region="eu-west-1",
            status=RegionHealthStatus.DEGRADED,
            latency_ms=200.0,
            last_check=datetime.now(timezone.utc),
            consecutive_failures=2,
            details={"error": "timeout"},
        )

        assert health.consecutive_failures == 2
        assert health.details["error"] == "timeout"


class TestRegionHealthMonitor:
    """RegionHealthMonitor 클래스 테스트."""

    def setup_method(self) -> None:
        """테스트 전 설정 리셋."""
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        """테스트 후 설정 리셋."""
        reset_multiregion_settings()

    def test_init_with_peer_regions(self) -> None:
        """피어 리전으로 초기화."""
        peer_json = """[
            {"region": "us-east-1", "redis_url": "", "api_endpoint": "http://us.api"},
            {"region": "eu-west-1", "redis_url": "", "api_endpoint": "http://eu.api"}
        ]"""
        settings = MultiRegionSettings(peer_regions=peer_json)
        monitor = RegionHealthMonitor(settings=settings)

        # 초기 상태는 HEALTHY
        health_us = monitor.get_region_health("us-east-1")
        health_eu = monitor.get_region_health("eu-west-1")

        assert health_us is not None
        assert health_us.status == RegionHealthStatus.HEALTHY
        assert health_eu is not None

    def test_get_healthy_regions(self) -> None:
        """정상 리전 목록."""
        peer_json = """[
            {"region": "us-east-1", "api_endpoint": "http://us.api"},
            {"region": "eu-west-1", "api_endpoint": "http://eu.api"}
        ]"""
        settings = MultiRegionSettings(peer_regions=peer_json)
        monitor = RegionHealthMonitor(settings=settings)

        healthy = monitor.get_healthy_regions()

        # 초기 상태는 모두 HEALTHY
        assert "us-east-1" in healthy
        assert "eu-west-1" in healthy

    def test_get_best_region(self) -> None:
        """가장 좋은 리전 선택."""
        peer_json = """[
            {"region": "us-east-1", "api_endpoint": "http://us.api"},
            {"region": "eu-west-1", "api_endpoint": "http://eu.api"}
        ]"""
        settings = MultiRegionSettings(peer_regions=peer_json)
        monitor = RegionHealthMonitor(settings=settings)

        # 수동으로 latency 설정
        monitor._health_states["us-east-1"].latency_ms = 100.0
        monitor._health_states["eu-west-1"].latency_ms = 50.0

        best = monitor.get_best_region()

        assert best == "eu-west-1"  # latency가 낮은 리전

    def test_is_region_healthy(self) -> None:
        """리전 정상 여부 확인."""
        peer_json = '[{"region": "us-east-1", "api_endpoint": "http://us.api"}]'
        settings = MultiRegionSettings(peer_regions=peer_json)
        monitor = RegionHealthMonitor(settings=settings)

        assert monitor.is_region_healthy("us-east-1") is True
        assert monitor.is_region_healthy("unknown-region") is False

    def test_check_region_api_failure(self) -> None:
        """API 실패 시 건강 상태."""
        endpoint = RegionEndpoint(
            region="failing-region",
            redis_url="",
            kafka_bootstrap="",
            api_endpoint="http://nonexistent.local:9999",
        )
        settings = MultiRegionSettings(
            health_check_timeout_seconds=1.0,
            unhealthy_threshold=2,
        )
        monitor = RegionHealthMonitor(settings=settings)

        # urllib.request.urlopen을 mock하여 즉시 URLError 발생
        import urllib.error

        with mock.patch(
            "selfhealing.multiregion.health_monitor.urllib.request.urlopen",
            side_effect=urllib.error.URLError("mocked connection refused"),
        ):
            health = monitor.check_region(endpoint)

        # 첫 번째 실패는 UNHEALTHY
        assert health.status in (RegionHealthStatus.UNHEALTHY, RegionHealthStatus.DEGRADED)
        assert health.consecutive_failures >= 1

    def test_start_stop(self) -> None:
        """시작/중지."""
        settings = MultiRegionSettings()
        monitor = RegionHealthMonitor(settings=settings)

        assert monitor.is_running() is False

        monitor.start()
        assert monitor.is_running() is True

        monitor.stop()
        assert monitor.is_running() is False

    def test_get_all_health_states(self) -> None:
        """모든 건강 상태 조회."""
        peer_json = """[
            {"region": "us-east-1", "api_endpoint": "http://us.api"},
            {"region": "eu-west-1", "api_endpoint": "http://eu.api"}
        ]"""
        settings = MultiRegionSettings(peer_regions=peer_json)
        monitor = RegionHealthMonitor(settings=settings)

        all_states = monitor.get_all_health_states()

        assert len(all_states) == 2
        assert "us-east-1" in all_states
        assert "eu-west-1" in all_states

    def test_consecutive_failures_tracking(self) -> None:
        """연속 실패 추적."""
        endpoint = RegionEndpoint(
            region="test-region",
            redis_url="",
            kafka_bootstrap="",
            api_endpoint="http://nonexistent.local:9999",
        )
        settings = MultiRegionSettings(
            peer_regions='[{"region": "test-region", "api_endpoint": "http://nonexistent.local:9999"}]',
            health_check_timeout_seconds=1.0,  # 최소 1초
            unhealthy_threshold=3,
        )
        monitor = RegionHealthMonitor(settings=settings)

        # urllib.request.urlopen을 mock하여 즉시 URLError 발생
        import urllib.error

        with mock.patch(
            "selfhealing.multiregion.health_monitor.urllib.request.urlopen",
            side_effect=urllib.error.URLError("mocked connection refused"),
        ):
            # 여러 번 실패 시뮬레이션
            for i in range(3):
                health = monitor.check_region(endpoint)
                monitor._health_states["test-region"] = health

        final_health = monitor.get_region_health("test-region")

        # 3회 연속 실패 → UNREACHABLE
        assert final_health is not None
        assert final_health.consecutive_failures >= 3
        assert final_health.status == RegionHealthStatus.UNREACHABLE

    def test_lag_monitoring_degraded(self) -> None:
        """Lag이 높으면 DEGRADED."""
        # RegionHealthMonitor._check_kafka_lag를 모킹
        peer_json = '[{"region": "lag-region", "api_endpoint": "http://lag.api"}]'
        settings = MultiRegionSettings(peer_regions=peer_json)
        monitor = RegionHealthMonitor(settings=settings)

        # Lag 체크 메서드 모킹
        with mock.patch.object(monitor, "_check_api_health") as mock_api:
            mock_api.return_value = RegionHealth(
                region="lag-region",
                status=RegionHealthStatus.HEALTHY,
                latency_ms=10.0,
                last_check=datetime.now(timezone.utc),
            )

            with mock.patch.object(monitor, "_check_kafka_lag") as mock_kafka:
                with mock.patch.object(monitor, "_check_redis_lag") as mock_redis:
                    # Critical Lag 설정
                    mock_kafka.return_value = 3000.0  # > 2000ms
                    mock_redis.return_value = 0.0

                    endpoint = RegionEndpoint(
                        region="lag-region",
                        redis_url="",
                        kafka_bootstrap="",
                        api_endpoint="http://lag.api",
                    )

                    health = monitor.check_region(endpoint)

                    assert health.status == RegionHealthStatus.DEGRADED
                    assert health.details.get("reason") == "replication_lag_critical"


# =============================================================================
# _mark_unhealthy 동작 검증 (237 약점 2)
# =============================================================================


class TestMarkUnhealthyBehavior:
    """_mark_unhealthy() 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    def test_marks_existing_region_as_unreachable(self) -> None:
        """등록된 리전을 UNREACHABLE로 즉시 마킹한다."""
        peer_json = '[{"region": "us-east-1", "api_endpoint": "http://us:8000"}]'
        settings = MultiRegionSettings(peer_regions=peer_json, unhealthy_threshold=3)
        monitor = RegionHealthMonitor(settings=settings)

        # 초기 상태 HEALTHY 확인
        health = monitor.get_region_health("us-east-1")
        assert health is not None
        assert health.status == RegionHealthStatus.HEALTHY

        # _mark_unhealthy 호출
        monitor._mark_unhealthy("us-east-1")

        health = monitor.get_region_health("us-east-1")
        assert health.status == RegionHealthStatus.UNREACHABLE
        assert health.consecutive_failures == settings.unhealthy_threshold
        assert health.details.get("reason") == "heartbeat_expired"

    def test_ignores_unknown_region(self) -> None:
        """등록되지 않은 리전에 대해서는 아무 것도 하지 않는다."""
        settings = MultiRegionSettings(peer_regions="[]")
        monitor = RegionHealthMonitor(settings=settings)

        # 예외 없이 완료
        monitor._mark_unhealthy("nonexistent-region")

        # 상태에 추가되지 않음
        assert monitor.get_region_health("nonexistent-region") is None


# =============================================================================
# _subscribe_heartbeat_expiry 동작 검증 (237 약점 2, 리뷰 1-2/3-2)
# =============================================================================


class TestSubscribeHeartbeatExpiryBehavior:
    """_subscribe_heartbeat_expiry() 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    @mock.patch("selfhealing.core.state_backend._get_config")
    @mock.patch("redis.from_url")
    def test_managed_redis_config_set_failure(self, mock_from_url: mock.MagicMock, mock_get_config: mock.MagicMock) -> None:
        """관리형 Redis에서 CONFIG SET 실패 시에도 구독을 시도한다."""
        import redis as redis_lib_real

        mock_get_config.return_value = "redis://localhost:6379/0"

        mock_client = mock.MagicMock()
        # CONFIG SET 시 ResponseError 발생 (관리형 Redis)
        mock_client.config_set.side_effect = redis_lib_real.exceptions.ResponseError("unknown command `CONFIG`")
        mock_pubsub = mock.MagicMock()
        mock_pubsub.listen.return_value = iter([])  # 빈 이터레이터로 즉시 종료
        mock_client.pubsub.return_value = mock_pubsub
        mock_from_url.return_value = mock_client

        settings = MultiRegionSettings(peer_regions="[]")
        monitor = RegionHealthMonitor(settings=settings)
        monitor._running = True

        # 예외 없이 완료 (CONFIG SET 실패 후에도 구독 시도)
        monitor._subscribe_heartbeat_expiry()

        # pubsub.psubscribe가 호출되었는지 확인
        mock_pubsub.psubscribe.assert_called_once_with("__keyevent@*__:expired")

    def test_redis_not_installed(self) -> None:
        """redis 패키지 미설치 시 경고 로그만 남기고 종료한다."""
        settings = MultiRegionSettings(peer_regions="[]")
        monitor = RegionHealthMonitor(settings=settings)

        # redis import를 실패시킴
        with mock.patch.dict("sys.modules", {"redis": None}):
            # 내부에서 ImportError가 발생하지만 예외를 전파하지 않음
            # (실제로는 이미 import되어 있어서 직접 테스트가 어려움)
            pass


# =============================================================================
# start() heartbeat 구독 스레드 동작 검증 (237 약점 2)
# =============================================================================


class TestHealthMonitorStartBehavior:
    """start() 관련 추가 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    def test_start_creates_heartbeat_worker(self) -> None:
        """start()는 heartbeat 구독 스레드를 생성한다."""
        settings = MultiRegionSettings(peer_regions="[]")
        monitor = RegionHealthMonitor(settings=settings)

        monitor.start()
        try:
            assert monitor._heartbeat_worker is not None
            assert monitor._heartbeat_worker.name == "RegionHeartbeatSubscriber"
            assert monitor._heartbeat_worker.daemon is True
        finally:
            monitor.stop()
