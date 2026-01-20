"""
Multi-Cluster Namespace Integration Tests.

Docker Compose Redis를 사용하여 다중 클러스터 네임스페이스 격리를 테스트합니다.

Prerequisites:
- Docker Compose running with Redis:
  docker-compose up -d redis

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

import os
import pytest
import tempfile
from datetime import datetime, timezone

# Import test constants for Redis configuration
from tests.factories.constants import REDIS_CONFIG

# Redis 연결 설정 - RedisTestConfig 사용
# Docker Compose test 환경: TEST_PORT(16379), 일반 docker-compose: DEFAULT_PORT(6379)
# 환경변수 REDIS_URL이 설정되면 해당 값 사용
REDIS_URL = os.getenv("REDIS_URL", REDIS_CONFIG.test_redis_url)


def redis_available():
    """Check if Redis is available."""
    try:
        import redis
        r = redis.from_url(REDIS_URL)
        r.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not redis_available(),
    reason="Redis not available. Run: docker-compose up -d redis"
)


@pytest.fixture(scope="function")
def temp_wal_dir():
    """Create temporary WAL directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture(scope="function")
def reset_all_singletons():
    """Reset all singletons before and after test."""
    from selfhealing.settings.namespace import reset_namespace_settings
    from selfhealing.core.cluster_identity import reset_cluster_identity
    from selfhealing.core.tiered_redis import reset_tiered_redis_provider
    from selfhealing.adapters.resilient.backend import reset_storage_backend
    
    # 환경변수 정리
    env_keys = [
        "SELFHEALING_NAMESPACE_ENABLED",
        "SELFHEALING_NAMESPACE",
        "SELFHEALING_REGION",
        "SELFHEALING_TENANT",
        "SELFHEALING_ENV",
        "SELFHEALING_CLUSTER_ID",
        "SELFHEALING_FAIL_FAST",
    ]
    for key in env_keys:
        os.environ.pop(key, None)
    
    reset_namespace_settings()
    reset_cluster_identity()
    reset_tiered_redis_provider()
    reset_storage_backend()
    
    yield
    
    # Cleanup after test
    for key in env_keys:
        os.environ.pop(key, None)
    
    reset_namespace_settings()
    reset_cluster_identity()
    reset_tiered_redis_provider()
    reset_storage_backend()


@pytest.fixture(scope="function")
def redis_client():
    """Get Redis client and cleanup test keys."""
    import redis
    r = redis.from_url(REDIS_URL)
    
    yield r
    
    # Cleanup: delete all test keys
    for pattern in [
        "selfhealing:*",
        "cb:*",
        "dlq:*",
    ]:
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)


class TestNamespaceIsolation:
    """다른 네임스페이스가 서로 격리되는지 테스트."""
    
    def test_circuit_breaker_namespace_isolation(
        self, reset_all_singletons, redis_client, temp_wal_dir
    ):
        """
        다른 네임스페이스의 Circuit Breaker가 서로 격리됨.
        
        Seoul과 Tokyo 클러스터가 같은 Redis를 사용해도
        서로의 CB 상태에 영향을 주지 않음.
        """
        from selfhealing.settings.namespace import reset_namespace_settings
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            reset_storage_backend,
        )
        from selfhealing.adapters.redis.circuit_breaker import (
            RedisCircuitBreakerStateRepository,
        )
        from selfhealing.interfaces.repositories import CircuitBreakerStateEnum
        
        service_name = "payment-api"
        
        # === Seoul Cluster ===
        os.environ["SELFHEALING_NAMESPACE_ENABLED"] = "true"
        os.environ["SELFHEALING_REGION"] = "seoul"
        reset_namespace_settings()
        reset_storage_backend()
        
        seoul_config = ResilientStorageConfig(
            redis_url=REDIS_URL,
            wal_dir=temp_wal_dir,
            key_prefix="selfhealing:seoul:",
        )
        seoul_backend = ResilientStorageBackend(seoul_config)
        seoul_cb = RedisCircuitBreakerStateRepository(seoul_backend)
        
        # Seoul에서 상태 생성 및 실패 기록
        seoul_state = seoul_cb.get_or_create(service_name)
        seoul_cb.increment_failure(service_name)
        seoul_cb.increment_failure(service_name)
        seoul_cb.increment_failure(service_name)
        
        seoul_state_after = seoul_cb.get_state(service_name)
        assert seoul_state_after.failure_count == 3, "Seoul should have 3 failures"
        
        # === Tokyo Cluster ===
        os.environ["SELFHEALING_REGION"] = "tokyo"
        reset_namespace_settings()
        reset_storage_backend()
        
        tokyo_config = ResilientStorageConfig(
            redis_url=REDIS_URL,
            wal_dir=temp_wal_dir,
            key_prefix="selfhealing:tokyo:",
        )
        tokyo_backend = ResilientStorageBackend(tokyo_config)
        tokyo_cb = RedisCircuitBreakerStateRepository(tokyo_backend)
        
        # Tokyo에서 동일 서비스 조회 - 격리되어 있어야 함
        tokyo_state = tokyo_cb.get_state(service_name)
        
        # Tokyo는 아직 상태가 없거나 failure_count가 0이어야 함
        if tokyo_state is not None:
            assert tokyo_state.failure_count == 0, (
                f"Tokyo should be isolated from Seoul, but has {tokyo_state.failure_count} failures"
            )
        
        # Tokyo에서 새로 생성
        tokyo_state = tokyo_cb.get_or_create(service_name)
        assert tokyo_state.failure_count == 0, "Tokyo should start fresh"
        
        # Tokyo에서 1개 실패
        tokyo_cb.increment_failure(service_name)
        tokyo_state_after = tokyo_cb.get_state(service_name)
        assert tokyo_state_after.failure_count == 1, "Tokyo should have 1 failure"
        
        # Seoul 재확인 - 여전히 3개
        os.environ["SELFHEALING_REGION"] = "seoul"
        reset_namespace_settings()
        reset_storage_backend()
        
        seoul_backend2 = ResilientStorageBackend(seoul_config)
        seoul_cb2 = RedisCircuitBreakerStateRepository(seoul_backend2)
        seoul_final = seoul_cb2.get_state(service_name)
        assert seoul_final.failure_count == 3, (
            f"Seoul should still have 3 failures, got {seoul_final.failure_count}"
        )
        
        # Cleanup
        seoul_backend.close()
        tokyo_backend.close()
        seoul_backend2.close()
    
    def test_dlq_namespace_isolation(
        self, reset_all_singletons, redis_client, temp_wal_dir
    ):
        """
        다른 네임스페이스의 DLQ가 서로 격리됨.
        """
        from selfhealing.settings.namespace import reset_namespace_settings
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            reset_storage_backend,
        )
        from selfhealing.adapters.redis.dlq import RedisDLQRepository
        
        # === Seoul Cluster ===
        os.environ["SELFHEALING_NAMESPACE_ENABLED"] = "true"
        os.environ["SELFHEALING_REGION"] = "seoul"
        reset_namespace_settings()
        reset_storage_backend()
        
        seoul_config = ResilientStorageConfig(
            redis_url=REDIS_URL,
            wal_dir=temp_wal_dir,
            key_prefix="selfhealing:seoul:",
        )
        seoul_backend = ResilientStorageBackend(seoul_config)
        seoul_dlq = RedisDLQRepository(seoul_backend)
        
        # Seoul에서 DLQ 엔트리 생성
        seoul_entry = seoul_dlq.create(
            domain="payment",
            failure_type="timeout",
            error_message="Seoul payment timeout",
        )
        
        # === Tokyo Cluster ===
        os.environ["SELFHEALING_REGION"] = "tokyo"
        reset_namespace_settings()
        reset_storage_backend()
        
        tokyo_config = ResilientStorageConfig(
            redis_url=REDIS_URL,
            wal_dir=temp_wal_dir,
            key_prefix="selfhealing:tokyo:",
        )
        tokyo_backend = ResilientStorageBackend(tokyo_config)
        tokyo_dlq = RedisDLQRepository(tokyo_backend)
        
        # Tokyo에서 pending 조회 - Seoul 것이 보이면 안 됨
        tokyo_pending = tokyo_dlq.list_pending(limit=10)
        assert len(tokyo_pending) == 0, (
            f"Tokyo should not see Seoul's DLQ entries, got {len(tokyo_pending)}"
        )
        
        # Tokyo에서 새 엔트리 생성
        tokyo_entry = tokyo_dlq.create(
            domain="order",
            failure_type="validation",
            error_message="Tokyo order validation error",
        )
        
        tokyo_pending_after = tokyo_dlq.list_pending(limit=10)
        assert len(tokyo_pending_after) == 1, "Tokyo should have 1 entry"
        
        # Cleanup
        seoul_backend.close()
        tokyo_backend.close()


class TestLegacyCompatibility:
    """기존 시스템(namespace 비활성화)과의 호환성 테스트."""
    
    def test_legacy_mode_uses_old_key_format(
        self, reset_all_singletons, redis_client, temp_wal_dir
    ):
        """
        namespace_enabled=False일 때 기존 키 형식 사용.
        """
        from selfhealing.settings.namespace import (
            get_namespace_settings,
            reset_namespace_settings,
        )
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            reset_storage_backend,
        )
        from selfhealing.adapters.redis.circuit_breaker import (
            RedisCircuitBreakerStateRepository,
        )
        
        # Legacy mode (namespace disabled)
        os.environ.pop("SELFHEALING_NAMESPACE_ENABLED", None)
        reset_namespace_settings()
        reset_storage_backend()
        
        settings = get_namespace_settings()
        assert settings.namespace_enabled is False, "Should be disabled by default"
        
        # 레거시 모드에서도 ResilientStorageBackend는 기본 key_prefix="selfhealing:"를 사용함
        # 이것은 기존 시스템과의 호환성을 위해 의도적으로 유지됨
        config = ResilientStorageConfig(
            redis_url=REDIS_URL,
            wal_dir=temp_wal_dir,
            # key_prefix 기본값: "selfhealing:"
        )
        backend = ResilientStorageBackend(config)
        cb = RedisCircuitBreakerStateRepository(backend)
        
        # CB 레벨의 key prefix는 "cb:" (네임스페이스 비활성화)
        assert cb.KEY_PREFIX == "cb:", f"Expected 'cb:', got '{cb.KEY_PREFIX}'"
        
        # Create state
        state = cb.get_or_create("legacy-service")
        
        # Verify key in Redis
        # 최종 키 = backend.key_prefix + cb.KEY_PREFIX + service_name
        #          = "selfhealing:" + "cb:" + "legacy-service"
        expected_key = f"{config.key_prefix}cb:legacy-service"
        exists = redis_client.exists(expected_key)
        assert exists, f"Key {expected_key} should exist in Redis (legacy format with backend prefix)"
        
        backend.close()


class TestTieredRedisProvider:
    """TieredRedisProvider 통합 테스트."""
    
    def test_tiered_redis_health_check(self, reset_all_singletons):
        """
        TieredRedisProvider의 health_check 테스트.
        """
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        provider = TieredRedisProvider(
            local_url=REDIS_URL,
            global_url=REDIS_URL,  # 테스트에서는 동일 Redis
        )
        
        result = provider.health_check()
        
        assert result["local"]["status"] == "healthy"
        assert result["global"]["status"] == "healthy"
        
        provider.close()
    
    def test_tiered_redis_get_redis(self, reset_all_singletons):
        """
        TieredRedisProvider에서 Redis 클라이언트 가져오기.
        """
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        provider = TieredRedisProvider(
            local_url=REDIS_URL,
            global_url=REDIS_URL,
        )
        
        local_client = provider.get_redis(RedisScope.LOCAL)
        global_client = provider.get_redis(RedisScope.GLOBAL)
        
        # 실제 Redis 작동 확인
        local_client.set("tiered_test_key", "local_value")
        value = local_client.get("tiered_test_key")
        assert value == b"local_value"
        
        # 동일 URL이므로 global에서도 접근 가능
        value_global = global_client.get("tiered_test_key")
        assert value_global == b"local_value"
        
        # Cleanup
        local_client.delete("tiered_test_key")
        provider.close()


class TestClusterIdentity:
    """ClusterIdentity 통합 테스트."""
    
    def test_cluster_identity_from_env(self, reset_all_singletons):
        """
        환경변수에서 ClusterIdentity 로드.
        """
        os.environ["SELFHEALING_CLUSTER_ID"] = "integration-test-cluster"
        os.environ["SELFHEALING_REGION"] = "ap-northeast-2"
        os.environ["SELFHEALING_ENV"] = "testing"
        os.environ["SELFHEALING_FAIL_FAST"] = "false"
        
        from selfhealing.core.cluster_identity import (
            get_cluster_identity,
            reset_cluster_identity,
        )
        
        reset_cluster_identity()
        identity = get_cluster_identity(skip_validation=True)
        
        assert identity.cluster_id == "integration-test-cluster"
        assert identity.region == "ap-northeast-2"
        assert identity.environment == "testing"
        assert identity.namespace == "ap-northeast-2"  # region 우선
        assert "ap-n" in identity.full_prefix
        
        # Trace ID prefix: 리전 앞 3글자 + 환경 앞 1글자
        assert identity.trace_id_prefix == "ap-t"
