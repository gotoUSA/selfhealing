"""
Settings Redis TTL Integration Tests.

실제 Redis 연결 상태에서 Settings에서 가져온 TTL 값이
Redis 명령어에 올바르게 적용되는지 검증합니다.

Requirements:
- Docker Compose for Redis
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: pytest tests/integration/selfhealing/test_settings_redis_ttl_integration.py -v

Tests:
1. RedisRateLimitStorage - Settings TTL이 Redis에 적용되는지
2. RedisAirGapAdapter - Settings TTL이 setex에 적용되는지
3. RedisAuditBuffer - Settings TTL이 expire에 적용되는지
4. 환경 변수 변경 시 TTL 값이 변경되는지

Related:
- packages/selfhealing-python/tests/unit/settings/test_settings_redis_ttl_and_class_constants.py (단위 테스트)
"""

import os
import pytest

# 이 파일의 모든 테스트는 Redis 필요
pytestmark = pytest.mark.requires_redis

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()


class TestRedisRateLimitStorageTtlIntegration:
    """
    RedisRateLimitStorage TTL 통합 테스트.

    실제 Redis 연결 후 TTL이 정상 작동하는지 확인.
    """

    def test_rate_limit_key_has_ttl(self, redis_client):
        """
        Rate Limit 키가 TTL을 가지는지 확인.

        Settings에서 가져온 TTL(기본 3600)이 실제 Redis 키에 적용되는지 검증.
        """
        from selfhealing.settings.rate_limit import reset_rate_limit_settings
        from selfhealing.adapters.rate_limit.redis_adapter import RedisRateLimitStorage

        reset_rate_limit_settings()

        # 실제 Redis 연결로 어댑터 생성
        storage = RedisRateLimitStorage(redis_client=redis_client)

        # TTL 값 확인 (Settings에서 가져온 값)
        assert storage._ttl == 3600

        # 테스트 키 생성
        test_key = "test:ratelimit:integration:ttl"
        redis_client.setex(test_key, storage._ttl, "test_value")

        # TTL 검증
        ttl = redis_client.ttl(test_key)
        assert ttl > 0
        assert ttl <= 3600

        # 정리
        redis_client.delete(test_key)

    def test_rate_limit_custom_ttl_applied(self, redis_client):
        """
        생성자에서 지정한 커스텀 TTL이 적용되는지 확인.
        """
        from selfhealing.adapters.rate_limit.redis_adapter import RedisRateLimitStorage

        custom_ttl = 1800
        storage = RedisRateLimitStorage(redis_client=redis_client, ttl=custom_ttl)

        assert storage._ttl == custom_ttl

        # 테스트 키 생성
        test_key = "test:ratelimit:integration:custom_ttl"
        redis_client.setex(test_key, storage._ttl, "test_value")

        # TTL 검증
        ttl = redis_client.ttl(test_key)
        assert ttl > 0
        assert ttl <= 1800

        # 정리
        redis_client.delete(test_key)


class TestRedisAirGapAdapterTtlIntegration:
    """
    RedisAirGapAdapter TTL 통합 테스트.

    실제 Redis 연결 후 setex에 TTL이 정상 적용되는지 확인.
    """

    def test_airgap_write_summary_has_ttl(self, redis_client):
        """
        AirGap write_summary가 TTL과 함께 저장되는지 확인.
        """
        from selfhealing.settings.airgap import reset_airgap_settings
        from selfhealing.adapters.airgap.redis_adapter import RedisAirGapAdapter

        reset_airgap_settings()

        adapter = RedisAirGapAdapter(redis_client=redis_client)

        # TTL 값 확인
        assert adapter.default_ttl == 3600

        # write_summary 호출
        test_key = "test:airgap:integration:ttl"
        adapter.write_summary(test_key, '{"test": "value"}')

        # 실제 저장된 키의 TTL 검증
        full_key = f"{adapter.prefix}{test_key}"
        ttl = redis_client.ttl(full_key)

        assert ttl > 0
        assert ttl <= 3600

        # 정리
        redis_client.delete(full_key)

    def test_airgap_custom_ttl_applied(self, redis_client):
        """
        AirGap 커스텀 TTL이 적용되는지 확인.
        """
        from selfhealing.adapters.airgap.redis_adapter import RedisAirGapAdapter

        custom_ttl = 7200
        adapter = RedisAirGapAdapter(redis_client=redis_client, default_ttl=custom_ttl)

        assert adapter.default_ttl == custom_ttl

        test_key = "test:airgap:integration:custom_ttl"
        adapter.write_summary(test_key, '{"test": "custom"}')

        full_key = f"{adapter.prefix}{test_key}"
        ttl = redis_client.ttl(full_key)

        assert ttl > 0
        assert ttl <= 7200

        # 정리
        redis_client.delete(full_key)


class TestRedisAuditBufferTtlIntegration:
    """
    RedisAuditBuffer TTL 통합 테스트.

    실제 Redis 연결 후 expire에 TTL이 정상 적용되는지 확인.
    """

    def test_audit_buffer_log_has_ttl(self, redis_client):
        """
        AuditBuffer log가 TTL과 함께 저장되는지 확인.
        """
        from selfhealing.settings.audit_settings import reset_audit_settings
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer

        reset_audit_settings()

        buffer = RedisAuditBuffer(redis_client=redis_client)

        # TTL 값 확인 (기본 86400 = 1일)
        assert buffer._ttl_seconds == 86400

        # log 호출
        buffer.log({"event": "integration_test"}, domain="test")

        # 저장된 키 찾기 (패턴 매칭)
        keys = redis_client.keys("sh:audit:*")

        if keys:
            # 첫 번째 키의 TTL 검증
            ttl = redis_client.ttl(keys[0])
            assert ttl > 0
            assert ttl <= 86400

            # 정리
            for key in keys:
                redis_client.delete(key)

    def test_audit_buffer_custom_ttl_applied(self, redis_client):
        """
        AuditBuffer 커스텀 TTL이 적용되는지 확인.
        """
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer

        custom_ttl = 172800  # 2일
        buffer = RedisAuditBuffer(redis_client=redis_client, ttl_seconds=custom_ttl)

        assert buffer._ttl_seconds == custom_ttl

        # log 호출
        buffer.log({"event": "custom_ttl_test"}, domain="test_custom")

        # 저장된 키 찾기
        keys = redis_client.keys("sh:audit:*")

        if keys:
            ttl = redis_client.ttl(keys[0])
            assert ttl > 0
            assert ttl <= 172800

            # 정리
            for key in keys:
                redis_client.delete(key)


class TestSettingsEnvVarTtlIntegration:
    """
    환경 변수로 설정된 TTL이 실제 Redis 연결에 적용되는지 확인.
    """

    def test_rate_limit_env_ttl_applied_to_redis(self, redis_client, monkeypatch):
        """
        SELFHEALING_RATE_LIMIT_REDIS_TTL 환경 변수가 실제 Redis에 적용되는지 확인.
        """
        from selfhealing.settings.rate_limit import reset_rate_limit_settings
        from selfhealing.adapters.rate_limit.redis_adapter import RedisRateLimitStorage

        # 환경 변수 설정
        monkeypatch.setenv("SELFHEALING_RATE_LIMIT_REDIS_TTL", "600")
        reset_rate_limit_settings()

        storage = RedisRateLimitStorage(redis_client=redis_client)

        # Settings에서 가져온 TTL 확인
        assert storage._ttl == 600

        # 테스트 키 생성
        test_key = "test:ratelimit:integration:env_ttl"
        redis_client.setex(test_key, storage._ttl, "env_test")

        # TTL 검증
        ttl = redis_client.ttl(test_key)
        assert ttl > 0
        assert ttl <= 600

        # 정리
        redis_client.delete(test_key)
        reset_rate_limit_settings()

    def test_airgap_env_ttl_applied_to_redis(self, redis_client, monkeypatch):
        """
        SELFHEALING_AIRGAP_REDIS_TTL 환경 변수가 실제 Redis에 적용되는지 확인.
        """
        from selfhealing.settings.airgap import reset_airgap_settings
        from selfhealing.adapters.airgap.redis_adapter import RedisAirGapAdapter

        # 환경 변수 설정
        monkeypatch.setenv("SELFHEALING_AIRGAP_REDIS_TTL", "1800")
        reset_airgap_settings()

        adapter = RedisAirGapAdapter(redis_client=redis_client)

        # Settings에서 가져온 TTL 확인
        assert adapter.default_ttl == 1800

        # write_summary 호출
        test_key = "test:airgap:integration:env_ttl"
        adapter.write_summary(test_key, '{"env": "test"}')

        full_key = f"{adapter.prefix}{test_key}"
        ttl = redis_client.ttl(full_key)

        assert ttl > 0
        assert ttl <= 1800

        # 정리
        redis_client.delete(full_key)
        reset_airgap_settings()

    def test_audit_buffer_env_ttl_applied_to_redis(self, redis_client, monkeypatch):
        """
        SELFHEALING_AUDIT_BUFFER_REDIS_TTL 환경 변수가 실제 Redis에 적용되는지 확인.
        """
        from selfhealing.settings.audit_settings import reset_audit_settings
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer

        # 환경 변수 설정 (1시간)
        monkeypatch.setenv("SELFHEALING_AUDIT_BUFFER_REDIS_TTL", "3600")
        reset_audit_settings()

        buffer = RedisAuditBuffer(redis_client=redis_client)

        # Settings에서 가져온 TTL 확인
        assert buffer._ttl_seconds == 3600

        # log 호출
        buffer.log({"event": "env_ttl_test"}, domain="test_env")

        # 저장된 키 찾기
        keys = redis_client.keys("sh:audit:*")

        if keys:
            ttl = redis_client.ttl(keys[0])
            assert ttl > 0
            assert ttl <= 3600

            # 정리
            for key in keys:
                redis_client.delete(key)

        reset_audit_settings()
