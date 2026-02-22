"""
Global Leader Election Redis 통합 테스트.

실제 Redis와의 상호작용을 테스트합니다.
Docker Compose 환경에서 실행됩니다.
"""

import os
import time
import threading
import uuid

import pytest
import redis

from selfhealing.coordination import (
    LeaderElectionSettings,
    LeadershipState,
    RedisLeaderElector,
    reset_leader_electors,
    reset_leader_election_settings,
)


# pytest-xdist 비활성화: 테스트 격리 필요
pytestmark = pytest.mark.forked


# 테스트용 Redis URL (docker-compose.test.yml 참조)
# Docker 내부: redis://redis:6379/0
# 로컬 테스트: redis://localhost:16379/0
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")


@pytest.fixture
def redis_client():
    """테스트용 Redis 클라이언트."""
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Redis not available")
    yield client
    # 테스트 후 리더 키 정리
    for key in client.keys("selfhealing:leader:*"):
        client.delete(key)
    for key in client.keys("selfhealing:fencing:*"):
        client.delete(key)


@pytest.fixture
def unique_resource():
    """각 테스트별 고유 리소스 이름 생성."""
    return f"test-resource-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def cleanup():
    """테스트 전후 정리."""
    reset_leader_electors()
    reset_leader_election_settings()
    yield
    reset_leader_electors()
    reset_leader_election_settings()


@pytest.fixture
def settings():
    """테스트용 설정."""
    return LeaderElectionSettings(
        enabled=True,
        backend="redis",
        redis_url=REDIS_URL,
        lease_ttl_seconds=10,
        renew_interval_seconds=3.0,
        retry_interval_seconds=1.0,
        self_fencing_enabled=True,
        node_id="test-node-1",
    )


class TestRedisLeaderElectorIntegration:
    """Redis Leader Elector 통합 테스트."""

    def test_single_elector_becomes_leader(self, redis_client, settings, unique_resource):
        """단일 Elector가 리더가 되는지 확인."""
        elector = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings,
            redis_client=redis_client,
        )

        elector.start()
        time.sleep(2)  # 리더 획득 대기

        assert elector.is_leader() is True
        assert elector.state == LeadershipState.LEADER
        assert elector.get_fencing_token() > 0

        leader = elector.get_leader()
        assert leader is not None
        assert leader.node_id == "test-node-1"
        assert leader.is_self is True

        elector.stop()
        assert elector.state == LeadershipState.STOPPED

    def test_leader_key_persisted_in_redis(self, redis_client, settings, unique_resource):
        """리더 키가 Redis에 저장되는지 확인."""
        elector = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings,
            redis_client=redis_client,
        )

        elector.start()
        time.sleep(2)

        # Redis에서 직접 키 확인
        key = f"selfhealing:leader:{unique_resource}"
        value = redis_client.get(key)
        assert value is not None
        assert "test-node-1" in value

        ttl = redis_client.ttl(key)
        assert ttl > 0
        assert ttl <= 10  # lease_ttl_seconds=10

        elector.stop()

    def test_callbacks_invoked(self, redis_client, settings, unique_resource):
        """리더십 콜백이 호출되는지 확인."""
        elector = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings,
            redis_client=redis_client,
        )

        become_leader_called = threading.Event()
        lose_leader_called = threading.Event()

        @elector.on_become_leader
        def on_become():
            become_leader_called.set()

        @elector.on_lose_leader
        def on_lose():
            lose_leader_called.set()

        elector.start()
        assert become_leader_called.wait(timeout=5.0), "on_become_leader not called"

        elector.stop()
        assert lose_leader_called.wait(timeout=5.0), "on_lose_leader not called"

    def test_lease_renewal(self, redis_client, settings, unique_resource):
        """Lease 갱신이 동작하는지 확인."""
        elector = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings,
            redis_client=redis_client,
        )

        elector.start()
        time.sleep(2)

        key = f"selfhealing:leader:{unique_resource}"

        # 초기 TTL 기록
        initial_ttl = redis_client.ttl(key)
        assert initial_ttl > 0, "Key should exist"

        # 갱신 대기 (renew_interval=3.0초)
        time.sleep(4)

        # Elector가 여전히 리더인지 확인 (갱신이 동작했다면)
        assert elector.is_leader() is True

        elector.stop()


class TestMultipleElectorsIntegration:
    """여러 Elector 간의 리더 선출 통합 테스트."""

    def test_only_one_leader_among_multiple_electors(self, redis_client, unique_resource):
        """여러 Elector 중 하나만 리더가 되는지 확인."""
        electors = []
        leaders = []

        for i in range(3):
            settings = LeaderElectionSettings(
                enabled=True,
                backend="redis",
                redis_url=REDIS_URL,
                lease_ttl_seconds=10,
                renew_interval_seconds=3.0,
                retry_interval_seconds=1.0,
                self_fencing_enabled=True,
                node_id=f"test-node-{i}",
            )
            elector = RedisLeaderElector(
                resource_name=unique_resource,
                settings=settings,
                redis_client=redis_client,
            )
            electors.append(elector)

        # 모든 Elector 시작
        for elector in electors:
            elector.start()

        # 리더 선출 대기
        time.sleep(3)

        # 리더 카운트
        for elector in electors:
            if elector.is_leader():
                leaders.append(elector)

        # 정확히 1명의 리더만 있어야 함
        assert len(leaders) == 1

        # 모든 Elector 중지
        for elector in electors:
            elector.stop()

    def test_leader_failover(self, redis_client, unique_resource):
        """리더 중지 시 다른 노드가 리더가 되는지 확인."""
        settings1 = LeaderElectionSettings(
            enabled=True,
            backend="redis",
            redis_url=REDIS_URL,
            lease_ttl_seconds=5,  # 짧은 TTL
            renew_interval_seconds=1.5,
            retry_interval_seconds=1.0,
            self_fencing_enabled=True,
            node_id="leader-node",
        )
        settings2 = LeaderElectionSettings(
            enabled=True,
            backend="redis",
            redis_url=REDIS_URL,
            lease_ttl_seconds=5,
            renew_interval_seconds=1.5,
            retry_interval_seconds=1.0,
            self_fencing_enabled=True,
            node_id="follower-node",
        )

        elector1 = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings1,
            redis_client=redis_client,
        )
        elector2 = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings2,
            redis_client=redis_client,
        )

        # 첫 번째 Elector가 먼저 리더가 됨
        elector1.start()
        time.sleep(2)
        assert elector1.is_leader() is True

        # 두 번째 Elector 시작 (팔로워)
        elector2.start()
        time.sleep(2)
        assert elector2.is_leader() is False

        # 첫 번째 Elector 중지
        elector1.stop()

        # 리더 키 만료 대기 + 팔로워가 리더가 되는 시간
        time.sleep(7)

        # 두 번째 Elector가 리더가 됨
        assert elector2.is_leader() is True

        elector2.stop()


class TestFencingTokenIntegration:
    """Fencing Token 통합 테스트."""

    def test_fencing_token_increases_on_new_leader(self, redis_client, unique_resource):
        """새 리더 선출 시 Fencing Token이 증가하는지 확인."""
        settings = LeaderElectionSettings(
            enabled=True,
            backend="redis",
            redis_url=REDIS_URL,
            lease_ttl_seconds=5,
            renew_interval_seconds=1.5,
            retry_interval_seconds=1.0,
            self_fencing_enabled=True,
            node_id="fencing-test-node-1",
        )

        elector1 = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings,
            redis_client=redis_client,
        )

        elector1.start()
        time.sleep(2)
        token1 = elector1.get_fencing_token()
        assert token1 > 0, "First token should be positive"
        elector1.stop()

        # 리더 키 만료 대기
        time.sleep(6)

        # 두 번째 리더
        settings2 = LeaderElectionSettings(
            enabled=True,
            backend="redis",
            redis_url=REDIS_URL,
            lease_ttl_seconds=5,
            renew_interval_seconds=1.5,
            retry_interval_seconds=1.0,
            self_fencing_enabled=True,
            node_id="fencing-test-node-2",
        )
        elector2 = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings2,
            redis_client=redis_client,
        )

        elector2.start()
        time.sleep(2)
        token2 = elector2.get_fencing_token()

        # 토큰이 증가해야 함
        assert token2 > token1, f"Token should increase: {token2} > {token1}"

        elector2.stop()


class TestRegionPriorityIntegration:
    """리전 우선순위 통합 테스트."""

    def test_higher_priority_takes_leadership(self, redis_client, unique_resource):
        """우선순위가 높은 노드가 리더십을 획득하는지 확인."""
        # 낮은 우선순위 (높은 숫자)
        settings_low = LeaderElectionSettings(
            enabled=True,
            backend="redis",
            redis_url=REDIS_URL,
            lease_ttl_seconds=10,
            renew_interval_seconds=3.0,
            retry_interval_seconds=1.0,
            self_fencing_enabled=True,
            node_id="low-priority-node",
            region_priority=100,  # 낮은 우선순위
        )

        # 높은 우선순위 (낮은 숫자)
        settings_high = LeaderElectionSettings(
            enabled=True,
            backend="redis",
            redis_url=REDIS_URL,
            lease_ttl_seconds=10,
            renew_interval_seconds=3.0,
            retry_interval_seconds=1.0,
            self_fencing_enabled=True,
            node_id="high-priority-node",
            region_priority=10,  # 높은 우선순위
        )

        # 낮은 우선순위 먼저 리더가 됨
        elector_low = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings_low,
            redis_client=redis_client,
        )
        elector_low.start()
        time.sleep(2)
        assert elector_low.is_leader() is True

        # 높은 우선순위 노드 시작 - 리더십 탈취
        elector_high = RedisLeaderElector(
            resource_name=unique_resource,
            settings=settings_high,
            redis_client=redis_client,
        )
        elector_high.start()
        time.sleep(3)

        # 높은 우선순위 노드가 리더가 됨
        assert elector_high.is_leader() is True
        # 낮은 우선순위는 더 이상 리더가 아님
        assert elector_low.is_leader() is False

        elector_low.stop()
        elector_high.stop()
