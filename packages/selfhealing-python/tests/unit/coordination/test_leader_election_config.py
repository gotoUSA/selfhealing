"""
Leader Election 설정 테스트.

LeaderElectionSettings 클래스의 설정 및 검증 로직 테스트.
"""

import os
from unittest.mock import patch

import pytest

from selfhealing.coordination.config import (
    LeaderElectionSettings,
    get_leader_election_settings,
    reset_leader_election_settings,
)


@pytest.fixture(autouse=True)
def reset_settings():
    """각 테스트 전후 설정 리셋."""
    reset_leader_election_settings()
    yield
    reset_leader_election_settings()


class TestLeaderElectionSettings:
    """LeaderElectionSettings 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        settings = LeaderElectionSettings()

        assert settings.enabled is True
        assert settings.backend == "redis"
        assert settings.lease_ttl_seconds == 30
        assert settings.region_priority == 100
        assert settings.self_fencing_enabled is True
        assert settings.redis_key_prefix == "selfhealing:leader:"

    def test_get_node_id_with_explicit_value(self):
        """명시적 node_id 설정 확인."""
        settings = LeaderElectionSettings(node_id="my-custom-node")
        assert settings.get_node_id() == "my-custom-node"

    def test_get_node_id_from_hostname_env(self):
        """HOSTNAME 환경변수에서 node_id 가져오기."""
        with patch.dict(os.environ, {"HOSTNAME": "test-pod-123"}):
            settings = LeaderElectionSettings(node_id="")
            assert settings.get_node_id() == "test-pod-123"

    def test_get_effective_renew_interval_auto_calculated(self):
        """자동 계산된 renew_interval 확인."""
        settings = LeaderElectionSettings(
            lease_ttl_seconds=30,
            renew_interval_seconds=None,
            lease_safety_margin_ratio=0.1,
        )
        # 30/3 - 30*0.1 = 10 - 3 = 7
        assert settings.get_effective_renew_interval() == 7.0

    def test_get_effective_renew_interval_explicit(self):
        """명시적 renew_interval 확인."""
        settings = LeaderElectionSettings(
            lease_ttl_seconds=30,
            renew_interval_seconds=8.0,
        )
        assert settings.get_effective_renew_interval() == 8.0

    def test_timing_validation_renew_too_long(self):
        """renew_interval이 lease_ttl/2 이상일 때 에러."""
        with pytest.raises(ValueError, match="must be < lease_ttl/2"):
            LeaderElectionSettings(
                lease_ttl_seconds=30,
                renew_interval_seconds=20.0,  # 30/2=15보다 큼
            )

    def test_timing_validation_pass(self):
        """유효한 타이밍 설정 통과."""
        settings = LeaderElectionSettings(
            lease_ttl_seconds=60,
            renew_interval_seconds=15.0,  # 60/2=30보다 작음
        )
        assert settings.renew_interval_seconds == 15.0

    def test_region_priority_bounds(self):
        """리전 우선순위 범위 확인."""
        settings = LeaderElectionSettings(region_priority=0)
        assert settings.region_priority == 0

        settings = LeaderElectionSettings(region_priority=1000)
        assert settings.region_priority == 1000

    def test_get_leader_election_settings_singleton(self):
        """싱글톤 패턴 확인."""
        settings1 = get_leader_election_settings()
        settings2 = get_leader_election_settings()
        assert settings1 is settings2

    def test_reset_leader_election_settings(self):
        """설정 리셋 확인."""
        settings1 = get_leader_election_settings()
        reset_leader_election_settings()
        settings2 = get_leader_election_settings()
        # 새 인스턴스이므로 다른 객체
        assert settings1 is not settings2

    def test_env_prefix(self):
        """환경변수 접두사 확인."""
        with patch.dict(
            os.environ,
            {
                "SELFHEALING_LEADER_ENABLED": "false",
                "SELFHEALING_LEADER_BACKEND": "redis",
                "SELFHEALING_LEADER_LEASE_TTL_SECONDS": "60",
            },
        ):
            reset_leader_election_settings()
            settings = LeaderElectionSettings()
            assert settings.enabled is False
            assert settings.lease_ttl_seconds == 60
