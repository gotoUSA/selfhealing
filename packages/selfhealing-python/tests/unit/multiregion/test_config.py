"""
Multi-Region 설정 테스트.

테스트 대상:
- RegionEndpoint: 리전 엔드포인트
- MultiRegionSettings: Multi-Region 설정
- _load_dynamic_peers: Redis 동적 피어 레지스트리 (237 약점 1)
"""

import os
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    RegionEndpoint,
    get_multiregion_settings,
    reset_multiregion_settings,
)


class TestRegionEndpoint:
    """RegionEndpoint 클래스 테스트."""

    def test_create_region_endpoint(self) -> None:
        """리전 엔드포인트 생성."""
        endpoint = RegionEndpoint(
            region="us-east-1",
            redis_url="redis://redis.us-east-1:6379",
            kafka_bootstrap="kafka.us-east-1:9092",
            api_endpoint="https://api.us-east-1.example.com",
            priority=50,
        )

        assert endpoint.region == "us-east-1"
        assert endpoint.redis_url == "redis://redis.us-east-1:6379"
        assert endpoint.kafka_bootstrap == "kafka.us-east-1:9092"
        assert endpoint.api_endpoint == "https://api.us-east-1.example.com"
        assert endpoint.priority == 50

    def test_default_priority(self) -> None:
        """기본 우선순위는 100."""
        endpoint = RegionEndpoint(
            region="ap-northeast-2",
            redis_url="redis://localhost:6379",
            kafka_bootstrap="localhost:9092",
            api_endpoint="http://localhost:8000",
        )

        assert endpoint.priority == 100

    def test_repr(self) -> None:
        """문자열 표현."""
        endpoint = RegionEndpoint(
            region="eu-west-1",
            redis_url="",
            kafka_bootstrap="",
            api_endpoint="",
            priority=10,
        )

        assert "eu-west-1" in repr(endpoint)
        assert "10" in repr(endpoint)


class TestMultiRegionSettings:
    """MultiRegionSettings 클래스 테스트."""

    def setup_method(self) -> None:
        """테스트 전 설정 캐시 리셋."""
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        """테스트 후 설정 캐시 리셋."""
        reset_multiregion_settings()

    def test_default_settings(self) -> None:
        """기본 설정값 확인."""
        settings = MultiRegionSettings()

        assert settings.enabled is False
        assert settings.current_region == "ap-northeast-2"
        assert settings.region_role == "primary"
        assert settings.replication_mode == "async"
        assert settings.conflict_resolution == "lww"
        assert settings.failover_enabled is True

    def test_settings_from_env(self) -> None:
        """환경변수에서 설정 로드."""
        env = {
            "SELFHEALING_MULTIREGION_ENABLED": "true",
            "SELFHEALING_MULTIREGION_CURRENT_REGION": "us-east-1",
            "SELFHEALING_MULTIREGION_REGION_ROLE": "secondary",
            "SELFHEALING_MULTIREGION_REPLICATION_MODE": "sync",
        }

        with mock.patch.dict(os.environ, env, clear=False):
            reset_multiregion_settings()
            settings = MultiRegionSettings()

            assert settings.enabled is True
            assert settings.current_region == "us-east-1"
            assert settings.region_role == "secondary"
            assert settings.replication_mode == "sync"

    def test_peer_regions_parsing(self) -> None:
        """피어 리전 JSON 파싱."""
        peer_json = """[
            {"region": "us-east-1", "redis_url": "redis://us:6379", "api_endpoint": "https://us.api"},
            {"region": "eu-west-1", "redis_url": "redis://eu:6379", "priority": 50}
        ]"""

        settings = MultiRegionSettings(peer_regions=peer_json)
        endpoints = settings.get_peer_endpoints()

        assert len(endpoints) == 2
        assert endpoints[0].region == "us-east-1"
        assert endpoints[0].redis_url == "redis://us:6379"
        assert endpoints[1].region == "eu-west-1"
        assert endpoints[1].priority == 50

    def test_empty_peer_regions(self) -> None:
        """빈 피어 리전 목록."""
        settings = MultiRegionSettings(peer_regions="[]")
        endpoints = settings.get_peer_endpoints()

        assert endpoints == []

    def test_invalid_peer_regions_json(self) -> None:
        """잘못된 JSON 형식."""
        with pytest.raises(ValueError, match="valid JSON"):
            MultiRegionSettings(peer_regions="invalid json")

    def test_is_primary(self) -> None:
        """Primary 역할 확인."""
        settings = MultiRegionSettings(region_role="primary")
        assert settings.is_primary() is True
        assert settings.is_secondary() is False
        assert settings.is_readonly() is False

    def test_is_secondary(self) -> None:
        """Secondary 역할 확인."""
        settings = MultiRegionSettings(region_role="secondary")
        assert settings.is_primary() is False
        assert settings.is_secondary() is True
        assert settings.is_readonly() is False

    def test_is_readonly(self) -> None:
        """읽기 전용 역할 확인."""
        settings = MultiRegionSettings(region_role="readonly")
        assert settings.is_primary() is False
        assert settings.is_secondary() is False
        assert settings.is_readonly() is True

    def test_singleton(self) -> None:
        """싱글톤 패턴 확인."""
        settings1 = get_multiregion_settings()
        settings2 = get_multiregion_settings()

        assert settings1 is settings2

    def test_reset_singleton(self) -> None:
        """싱글톤 리셋."""
        settings1 = get_multiregion_settings()
        reset_multiregion_settings()
        settings2 = get_multiregion_settings()

        # 새 인스턴스지만 값은 동일
        assert settings1 is not settings2

    def test_replication_settings(self) -> None:
        """복제 설정."""
        settings = MultiRegionSettings(
            replication_batch_size=500,
            replication_interval_seconds=0.5,
            replication_queue_size=50000,
        )

        assert settings.replication_batch_size == 500
        assert settings.replication_interval_seconds == 0.5
        assert settings.replication_queue_size == 50000

    def test_health_check_settings(self) -> None:
        """건강 체크 설정."""
        settings = MultiRegionSettings(
            health_check_interval_seconds=30.0,
            health_check_timeout_seconds=10.0,
            unhealthy_threshold=5,
        )

        assert settings.health_check_interval_seconds == 30.0
        assert settings.health_check_timeout_seconds == 10.0
        assert settings.unhealthy_threshold == 5

    def test_failover_settings(self) -> None:
        """페일오버 설정."""
        settings = MultiRegionSettings(
            failover_enabled=False,
            failover_cooldown_seconds=600.0,
        )

        assert settings.failover_enabled is False
        assert settings.failover_cooldown_seconds == 600.0

    def test_tls_settings(self) -> None:
        """TLS 설정."""
        settings = MultiRegionSettings(
            tls_enabled=False,
            tls_verify_hostname=False,
        )

        assert settings.tls_enabled is False
        assert settings.tls_verify_hostname is False

    def test_clock_skew_settings(self) -> None:
        """Clock skew 설정."""
        settings = MultiRegionSettings(
            clock_skew_tolerance_ms=10,
            clock_skew_fallback_seconds=2.0,
        )

        assert settings.clock_skew_tolerance_ms == 10
        assert settings.clock_skew_fallback_seconds == 2.0


# =============================================================================
# 동적 피어 레지스트리 동작 검증 (237 약점 1)
# =============================================================================


class TestDynamicPeerRegistryBehavior:
    """_load_dynamic_peers() 및 get_peer_endpoints() Redis-first 폴백 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_get_peer_endpoints_redis_first(self, mock_get_backend: MagicMock) -> None:
        """Redis에 동적 피어가 있으면 환경변수 JSON보다 우선한다."""
        mock_backend = MagicMock()
        mock_backend.get.return_value = {
            "endpoints": [
                {
                    "region": "redis-region-1",
                    "redis_url": "redis://redis1:6379",
                    "api_endpoint": "http://redis1:8000",
                }
            ]
        }
        mock_get_backend.return_value = mock_backend

        # 환경변수에도 설정되어 있지만 Redis가 우선
        env_json = '[{"region": "env-region-1", "redis_url": "redis://env:6379"}]'
        settings = MultiRegionSettings(peer_regions=env_json)
        endpoints = settings.get_peer_endpoints()

        assert len(endpoints) == 1
        assert endpoints[0].region == "redis-region-1"

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_get_peer_endpoints_fallback_to_env(self, mock_get_backend: MagicMock) -> None:
        """Redis에 데이터가 없으면 환경변수 JSON으로 폴백한다."""
        mock_backend = MagicMock()
        mock_backend.get.return_value = None
        mock_get_backend.return_value = mock_backend

        env_json = '[{"region": "env-region-1", "redis_url": "redis://env:6379", "api_endpoint": "http://env:8000"}]'
        settings = MultiRegionSettings(peer_regions=env_json)
        endpoints = settings.get_peer_endpoints()

        assert len(endpoints) == 1
        assert endpoints[0].region == "env-region-1"

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_get_peer_endpoints_redis_exception_fallback(self, mock_get_backend: MagicMock) -> None:
        """Redis 연결 실패 시 환경변수 JSON으로 폴백한다."""
        mock_get_backend.side_effect = Exception("Redis connection refused")

        env_json = '[{"region": "env-region-1", "redis_url": "redis://env:6379", "api_endpoint": "http://env:8000"}]'
        settings = MultiRegionSettings(peer_regions=env_json)
        endpoints = settings.get_peer_endpoints()

        assert len(endpoints) == 1
        assert endpoints[0].region == "env-region-1"

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_load_dynamic_peers_returns_none_on_empty_data(self, mock_get_backend: MagicMock) -> None:
        """Redis에 endpoints 키가 없으면 None을 반환한다."""
        mock_backend = MagicMock()
        mock_backend.get.return_value = {"other_key": "value"}
        mock_get_backend.return_value = mock_backend

        settings = MultiRegionSettings()
        result = settings._load_dynamic_peers()

        assert result is None

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_load_dynamic_peers_parses_priority(self, mock_get_backend: MagicMock) -> None:
        """동적 피어의 priority 필드가 파싱된다."""
        mock_backend = MagicMock()
        mock_backend.get.return_value = {
            "endpoints": [
                {
                    "region": "us-east-1",
                    "redis_url": "redis://us:6379",
                    "api_endpoint": "http://us:8000",
                    "priority": 50,
                }
            ]
        }
        mock_get_backend.return_value = mock_backend

        settings = MultiRegionSettings()
        result = settings._load_dynamic_peers()

        assert result is not None
        assert result[0].priority == 50

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_load_dynamic_peers_default_priority(self, mock_get_backend: MagicMock) -> None:
        """priority가 없으면 기본값 100을 사용한다."""
        mock_backend = MagicMock()
        mock_backend.get.return_value = {
            "endpoints": [
                {
                    "region": "us-east-1",
                    "redis_url": "redis://us:6379",
                }
            ]
        }
        mock_get_backend.return_value = mock_backend

        settings = MultiRegionSettings()
        result = settings._load_dynamic_peers()

        assert result is not None
        assert result[0].priority == 100
