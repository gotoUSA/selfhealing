"""
Audit Redis 통합 테스트.

ConfigHistoryService의 실제 Redis 연동 검증.
WAL은 파일 기반이므로 Redis 테스트 대상 아님.

실행:
    docker-compose -f docker-compose.test.yml up -d db redis
    docker-compose -f docker-compose.test.yml run --rm test-audit-integration
"""

import os
import pytest
import json
from unittest.mock import patch

pytestmark = pytest.mark.requires_redis


def get_redis_host():
    """환경에 따른 Redis 호스트 반환."""
    # Docker 내부: redis, 로컬: localhost
    redis_url = os.environ.get("REDIS_URL", "")
    if "redis://" in redis_url:
        # redis://redis:6379/0 형태
        return redis_url.split("://")[1].split(":")[0]
    return "localhost"


def get_redis_port():
    """환경에 따른 Redis 포트 반환."""
    redis_url = os.environ.get("REDIS_URL", "")
    if "redis://" in redis_url:
        # Docker 내부: 6379
        return 6379
    # 로컬: docker-compose.test.yml의 16379
    return 16379


class TestConfigHistoryRedisIntegration:
    """ConfigHistoryService가 실제 Redis에 기록하는지 검증."""

    @pytest.fixture
    def redis_client(self):
        """실제 Redis 클라이언트."""
        import redis
        client = redis.Redis(
            host=get_redis_host(),
            port=get_redis_port(),
            db=0,
            decode_responses=False,  # ConfigHistoryService는 bytes 사용
        )
        client.ping()
        yield client
        # Cleanup
        for key in client.keys(b"selfhealing:config:*"):
            client.delete(key)

    def test_save_version_persists_to_redis(self, redis_client):
        """save_version이 실제 Redis에 저장."""
        from selfhealing.services.config_history import ConfigHistoryService

        service = ConfigHistoryService()
        service._redis_client = redis_client

        result = service.save_version(
            config_type="circuit_breaker",
            values={"failure_threshold": 10},
            changed_by="integration_test",
            reason="Redis integration test",
        )

        assert result is not None
        assert result.version >= 1

        # Redis에서 직접 확인
        history_key = b"selfhealing:config:history:circuit_breaker"
        entries = redis_client.lrange(history_key, 0, -1)
        assert len(entries) > 0

        # 저장된 데이터 파싱
        entry = json.loads(entries[0])
        assert entry["config_type"] == "circuit_breaker"
        assert entry["values"]["failure_threshold"] == 10

    def test_rollback_creates_new_version(self, redis_client):
        """rollback이 새 버전을 생성하고 Redis에 저장."""
        from selfhealing.services.config_history import ConfigHistoryService

        service = ConfigHistoryService()
        service._redis_client = redis_client

        # 버전 1 저장
        v1 = service.save_version(
            config_type="circuit_breaker",
            values={"threshold": 5},
            changed_by="test",
            reason="v1",
        )

        # 버전 2 저장
        v2 = service.save_version(
            config_type="circuit_breaker",
            values={"threshold": 10},
            changed_by="test",
            reason="v2",
        )

        # 버전 1로 롤백
        with patch("selfhealing.services.config_history.log_config_apply_audit"):
            with patch("selfhealing.services.config_history.log_rollback_audit"):
                rolled_back = service.rollback(
                    config_type="circuit_breaker",
                    target_version=v1.version,
                    rolled_back_by="admin",
                )

        assert rolled_back is not None
        assert rolled_back.version == v2.version + 1
        assert rolled_back.values["threshold"] == 5
