"""
Redis Leader Elector 테스트.

RedisLeaderElector 클래스의 기능 테스트 (Mock Redis 사용).
"""

import json
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.coordination.base import LeadershipState
from selfhealing.coordination.config import (
    LeaderElectionSettings,
    reset_leader_election_settings,
)
from selfhealing.coordination.redis_elector import RedisLeaderElector


@pytest.fixture(autouse=True)
def reset_settings():
    """각 테스트 전후 설정 리셋."""
    reset_leader_election_settings()
    yield
    reset_leader_election_settings()


@pytest.fixture
def mock_redis():
    """Mock Redis 클라이언트."""
    redis = MagicMock()
    redis.get.return_value = None
    redis.set.return_value = True
    redis.ttl.return_value = 30
    redis.register_script.return_value = MagicMock(return_value=1)
    return redis


@pytest.fixture
def settings():
    """테스트용 설정."""
    return LeaderElectionSettings(
        enabled=True,
        backend="redis",
        node_id="test-node",
        lease_ttl_seconds=30,
        renew_interval_seconds=10.0,
        retry_interval_seconds=2.0,
        self_fencing_enabled=False,  # 테스트를 위해 비활성화
    )


class TestRedisLeaderElectorInit:
    """RedisLeaderElector 초기화 테스트."""

    def test_init_with_defaults(self, mock_redis):
        """기본 설정으로 초기화."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            redis_client=mock_redis,
        )

        assert elector.resource_name == "test-resource"
        assert elector.state == LeadershipState.NOT_STARTED
        assert elector.is_leader() is False

    def test_init_with_custom_settings(self, mock_redis, settings):
        """커스텀 설정으로 초기화."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        assert elector._node_id == "test-node"
        assert elector._settings.lease_ttl_seconds == 30


class TestRedisLeaderElectorLeadership:
    """리더십 획득/상실 테스트."""

    def test_become_leader(self, mock_redis, settings):
        """리더 획득 테스트."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        # Lua 스크립트가 fencing token 1 반환 (획득 성공)
        mock_script = MagicMock(return_value=1)
        mock_redis.register_script.return_value = mock_script

        result = elector._try_acquire()

        assert result is True
        assert elector._fencing_token == 1

    def test_fail_to_acquire(self, mock_redis, settings):
        """리더 획득 실패 테스트."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        # Lua 스크립트가 0 반환 (획득 실패)
        mock_script = MagicMock(return_value=0)
        mock_redis.register_script.return_value = mock_script

        result = elector._try_acquire()

        assert result is False

    def test_renew_lease_success(self, mock_redis, settings):
        """Lease 갱신 성공 테스트."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        # Lua 스크립트가 1 반환 (갱신 성공)
        mock_script = MagicMock(return_value=1)
        mock_redis.register_script.return_value = mock_script

        result = elector._renew_lease()

        assert result is True

    def test_renew_lease_failure(self, mock_redis, settings):
        """Lease 갱신 실패 테스트."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        # Lua 스크립트가 0 반환 (갱신 실패 - 다른 리더)
        mock_script = MagicMock(return_value=0)
        mock_redis.register_script.return_value = mock_script

        result = elector._renew_lease()

        assert result is False


class TestRedisLeaderElectorCallbacks:
    """콜백 테스트."""

    def test_on_become_leader_callback(self, mock_redis, settings):
        """리더가 되었을 때 콜백 호출 테스트."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        callback_called = threading.Event()

        @elector.on_become_leader
        def become_callback():
            callback_called.set()

        # 상태를 FOLLOWER로 설정
        elector._state = LeadershipState.FOLLOWER

        # 리더 되기
        elector._become_leader()

        # 콜백이 비동기로 실행되므로 대기
        assert callback_called.wait(timeout=2.0)
        assert elector.state == LeadershipState.LEADER

    def test_on_lose_leader_callback(self, mock_redis, settings):
        """리더십을 잃었을 때 콜백 호출 테스트."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        callback_called = threading.Event()

        @elector.on_lose_leader
        def lose_callback():
            callback_called.set()

        # 상태를 LEADER로 설정
        elector._state = LeadershipState.LEADER

        # 리더십 상실
        elector._lose_leader()

        # 콜백이 비동기로 실행되므로 대기
        assert callback_called.wait(timeout=2.0)
        assert elector.state == LeadershipState.FOLLOWER


class TestRedisLeaderElectorGetLeader:
    """리더 정보 조회 테스트."""

    def test_get_leader_when_exists(self, mock_redis, settings):
        """리더가 있을 때 정보 조회."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        leader_data = json.dumps(
            {
                "node_id": "other-node",
                "elected_at": datetime.now(timezone.utc).isoformat(),
                "region_priority": 50,
                "fencing_token": 5,
            }
        )
        mock_redis.get.return_value = leader_data
        mock_redis.ttl.return_value = 25

        leader = elector.get_leader()

        assert leader is not None
        assert leader.node_id == "other-node"
        assert leader.region_priority == 50
        assert leader.is_self is False

    def test_get_leader_when_none(self, mock_redis, settings):
        """리더가 없을 때 None 반환."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        mock_redis.get.return_value = None

        leader = elector.get_leader()

        assert leader is None

    def test_get_leader_is_self(self, mock_redis, settings):
        """자신이 리더일 때 is_self=True."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        leader_data = json.dumps(
            {
                "node_id": "test-node",  # settings.node_id와 동일
                "elected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        mock_redis.get.return_value = leader_data
        mock_redis.ttl.return_value = 25

        leader = elector.get_leader()

        assert leader is not None
        assert leader.is_self is True


class TestRedisLeaderElectorStartStop:
    """Start/Stop 테스트."""

    def test_start_creates_worker_thread(self, mock_redis, settings):
        """start()가 워커 스레드를 생성하는지 확인."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        # Lua 스크립트 모킹
        mock_script = MagicMock(return_value=0)  # 항상 획득 실패
        mock_redis.register_script.return_value = mock_script

        elector.start()

        assert elector._running is True
        assert elector._worker is not None
        assert elector._worker.is_alive()
        assert elector.state == LeadershipState.FOLLOWER

        # 정리
        elector.stop()

    def test_stop_stops_worker_thread(self, mock_redis, settings):
        """stop()이 워커 스레드를 중지하는지 확인."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        mock_script = MagicMock(return_value=0)
        mock_redis.register_script.return_value = mock_script

        elector.start()
        time.sleep(0.1)  # 워커 스레드 시작 대기

        elector.stop()

        assert elector._running is False
        assert elector.state == LeadershipState.STOPPED

    def test_disabled_does_not_start(self, mock_redis):
        """enabled=False면 start()가 아무것도 안 함."""
        settings = LeaderElectionSettings(
            enabled=False,
            node_id="test-node",
        )
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        elector.start()

        assert elector._running is False
        assert elector._worker is None


class TestRedisLeaderElectorFencingToken:
    """Fencing Token 테스트."""

    def test_fencing_token_increases_on_acquire(self, mock_redis, settings):
        """리더 획득 시 Fencing Token 증가."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        # 첫 번째 획득: token=1
        mock_script = MagicMock(return_value=1)
        mock_redis.register_script.return_value = mock_script

        elector._try_acquire()
        assert elector.get_fencing_token() == 1

        # 두 번째 획득: token=2
        mock_script.return_value = 2
        elector._try_acquire()
        assert elector.get_fencing_token() == 2


class TestRedisLeaderElectorLeaseValidity:
    """Lease 유효성 검사 테스트."""

    def test_is_lease_valid_when_leader(self, mock_redis, settings):
        """리더일 때 Lease 유효성 확인."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        # 상태를 LEADER로 설정
        elector._state = LeadershipState.LEADER

        # get_leader가 자신을 반환하도록 설정
        leader_data = json.dumps(
            {
                "node_id": "test-node",
                "elected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        mock_redis.get.return_value = leader_data
        mock_redis.ttl.return_value = 25

        assert elector.is_lease_valid() is True

    def test_is_lease_valid_when_not_leader(self, mock_redis, settings):
        """리더가 아닐 때 Lease 무효."""
        elector = RedisLeaderElector(
            resource_name="test-resource",
            settings=settings,
            redis_client=mock_redis,
        )

        elector._state = LeadershipState.FOLLOWER

        assert elector.is_lease_valid() is False
