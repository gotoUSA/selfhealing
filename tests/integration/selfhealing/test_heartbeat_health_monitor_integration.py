"""
Heartbeat TTL → Health Monitor 체인 통합 테스트.

237 약점 2에서 설계한 3계층 감지 전략 중 Layer 2(TTL Heartbeat)를 검증합니다.

검증 항목:
1. RegionHeartbeat가 Redis에 TTL 키를 주기적으로 갱신
2. TTL 만료 시 Redis Keyspace Notification 발생
3. RegionHealthMonitor._subscribe_heartbeat_expiry()가 만료 감지
4. _mark_unhealthy()로 리전을 UNREACHABLE로 마킹

이 테스트는 실제 Redis를 사용합니다 (Docker Compose 환경).
"""

from __future__ import annotations

import os
import threading
import time

import pytest
import redis

from selfhealing.core.state_backend import (
    RedisStateBackend,
    reset_state_backend,
)
from selfhealing.multiregion.config import (
    MultiRegionSettings,
    reset_multiregion_settings,
)
from selfhealing.multiregion.health_monitor import (
    RegionHealthMonitor,
    RegionHealthStatus,
)
from selfhealing.multiregion.heartbeat import RegionHeartbeat


# Docker Compose 환경의 Redis URL
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

# Keyspace notification 감지 대기 시간 (TTL + 마진)
KEYSPACE_WAIT_TIMEOUT = 10


@pytest.fixture
def redis_client():
    """테스트용 Redis 클라이언트."""
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Redis not available")
    yield client
    # 테스트 후 하트비트 키 정리
    for key in client.keys("selfhealing:state:multiregion:heartbeat:*"):
        client.delete(key)


@pytest.fixture(autouse=True)
def cleanup():
    """테스트 전후 정리."""
    reset_multiregion_settings()
    reset_state_backend()
    yield
    reset_multiregion_settings()
    reset_state_backend()


@pytest.fixture
def redis_state_backend():
    """실제 Redis 기반 StateBackend."""
    backend = RedisStateBackend(redis_url=REDIS_URL)
    return backend


@pytest.fixture
def settings() -> MultiRegionSettings:
    """peer-region이 설정된 MultiRegionSettings."""
    peer_json = (
        '[{"region": "test-heartbeat-region", '
        '"redis_url": "redis://localhost:6379", '
        '"api_endpoint": "http://localhost:8000"}]'
    )
    return MultiRegionSettings(
        current_region="test-heartbeat-region",
        peer_regions=peer_json,
        unhealthy_threshold=3,
    )


class TestHeartbeatRedisIntegration:
    """RegionHeartbeat Redis 통합 테스트."""

    def test_beat_sets_redis_key_with_ttl(self, redis_client: redis.Redis, settings: MultiRegionSettings) -> None:
        """_beat()는 Redis에 TTL이 설정된 키를 생성한다."""
        # 환경변수로 state backend를 redis로 설정
        os.environ["SELFHEALING_STATE_BACKEND"] = "redis"
        os.environ["SELFHEALING_REDIS_URL"] = REDIS_URL
        reset_state_backend()

        hb = RegionHeartbeat(settings)
        hb._beat()

        full_key = f"selfhealing:state:{hb._heartbeat_key()}"
        assert redis_client.exists(full_key) == 1

        ttl = redis_client.ttl(full_key)
        # TTL이 0보다 크고 HEARTBEAT_TTL 이하
        assert 0 < ttl <= RegionHeartbeat.HEARTBEAT_TTL

    def test_heartbeat_key_expires(self, redis_client: redis.Redis, settings: MultiRegionSettings) -> None:
        """하트비트 키가 TTL 만료 후 사라진다."""
        os.environ["SELFHEALING_STATE_BACKEND"] = "redis"
        os.environ["SELFHEALING_REDIS_URL"] = REDIS_URL
        reset_state_backend()

        # 짧은 TTL로 직접 설정 (빠른 테스트를 위해)
        short_ttl = 2
        full_key = f"selfhealing:state:multiregion:heartbeat:{settings.current_region}"
        redis_client.setex(full_key, short_ttl, '{"region": "test", "ts": 0}')

        assert redis_client.exists(full_key) == 1

        # TTL 만료 대기
        time.sleep(short_ttl + 1)

        assert redis_client.exists(full_key) == 0


class TestKeyspaceNotificationIntegration:
    """Redis Keyspace Notification을 통한 하트비트 만료 감지 통합 테스트."""

    def test_keyspace_notification_on_expiry(self, redis_client: redis.Redis) -> None:
        """키 만료 시 Keyspace Notification이 발생한다."""
        # Keyspace notification 활성화
        try:
            redis_client.config_set("notify-keyspace-events", "Ex")
        except redis.exceptions.ResponseError:
            pytest.skip("CONFIG SET not supported (managed Redis)")

        # Pub/Sub 구독
        pubsub = redis_client.pubsub()
        pubsub.psubscribe("__keyevent@*__:expired")
        # 구독 확인 메시지 소비
        pubsub.get_message(timeout=1)

        # 짧은 TTL의 하트비트 키 설정
        test_region = "integration-test-region"
        full_key = f"selfhealing:state:multiregion:heartbeat:{test_region}"
        redis_client.setex(full_key, 2, '{"region": "test", "ts": 0}')

        # 만료 대기 및 알림 수신
        expired_key = None
        deadline = time.time() + KEYSPACE_WAIT_TIMEOUT
        while time.time() < deadline:
            msg = pubsub.get_message(timeout=1)
            if msg and msg["type"] == "pmessage":
                if full_key in str(msg.get("data", "")):
                    expired_key = msg["data"]
                    break

        pubsub.unsubscribe()
        pubsub.close()

        assert expired_key is not None
        assert test_region in expired_key


class TestHeartbeatHealthMonitorChainIntegration:
    """
    Heartbeat TTL → Health Monitor 전체 체인 통합 테스트.

    Layer 2 감지 전략:
    1. RegionHeartbeat가 Redis에 TTL 키 갱신
    2. 프로세스 사망 시뮬레이션 (heartbeat 중지)
    3. TTL 만료 → Keyspace Notification
    4. RegionHealthMonitor가 알림 수신 → _mark_unhealthy()
    5. 리전 상태가 UNREACHABLE로 변경
    """

    def test_full_chain_heartbeat_to_unhealthy(self, redis_client: redis.Redis) -> None:
        """TTL 만료 시 health_monitor가 리전을 UNREACHABLE로 마킹한다."""
        # Keyspace notification 활성화
        try:
            redis_client.config_set("notify-keyspace-events", "Ex")
        except redis.exceptions.ResponseError:
            pytest.skip("CONFIG SET not supported (managed Redis)")

        os.environ["SELFHEALING_STATE_BACKEND"] = "redis"
        os.environ["SELFHEALING_REDIS_URL"] = REDIS_URL
        reset_state_backend()

        test_region = "chain-test-region"
        peer_json = (
            f'[{{"region": "{test_region}", '
            f'"redis_url": "redis://localhost:6379", '
            f'"api_endpoint": "http://localhost:8000"}}]'
        )
        settings = MultiRegionSettings(
            current_region="local-region",
            peer_regions=peer_json,
            unhealthy_threshold=3,
        )

        monitor = RegionHealthMonitor(settings=settings)

        # 초기 상태 HEALTHY 확인
        health = monitor.get_region_health(test_region)
        assert health is not None
        assert health.status == RegionHealthStatus.HEALTHY

        # heartbeat 구독 스레드 시작
        monitor._running = True
        heartbeat_thread = threading.Thread(
            target=monitor._subscribe_heartbeat_expiry,
            daemon=True,
        )
        heartbeat_thread.start()

        # 짧은 TTL의 하트비트 키 설정 (프로세스 사망 시뮬레이션)
        short_ttl = 2
        full_key = f"selfhealing:state:multiregion:heartbeat:{test_region}"
        redis_client.setex(full_key, short_ttl, '{"region": "test", "ts": 0}')

        # TTL 만료 + 감지 대기
        deadline = time.time() + KEYSPACE_WAIT_TIMEOUT
        detected = False
        while time.time() < deadline:
            health = monitor.get_region_health(test_region)
            if health and health.status == RegionHealthStatus.UNREACHABLE:
                detected = True
                break
            time.sleep(0.5)

        # 정리
        monitor._running = False
        heartbeat_thread.join(timeout=3)

        assert detected, (
            f"Expected {test_region} to be UNREACHABLE after TTL expiry, "
            f"but status was {health.status if health else 'None'}"
        )
        assert health.details.get("reason") == "heartbeat_expired"
