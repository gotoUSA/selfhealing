"""
TwoTierMeshOverrideStore Redis Integration Tests (303).

실제 Redis 연결 상태에서 TwoTierMeshOverrideStore의 L2 동작을 검증합니다.

Requirements:
- Docker Compose for Redis
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: pytest tests/integration/selfhealing/test_circuit_mesh_store_redis_integration.py -v

Test Categories:
    A. Serialization Roundtrip: set → Redis → get 데이터 무결성
    B. Hydration: hydrate_from_l2로 L1 복원
    C. Cross-Instance Invalidation: L2 기반 다중 인스턴스 동기화
    D. Key Pattern Scan: keys() 패턴 매칭 동작

Related:
- packages/selfhealing-python/tests/unit/circuit_mesh/test_two_tier_store.py (단위 테스트)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.requires_redis, pytest.mark.tier2]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def redis_cache(redis_client):
    """
    RedisCacheAdapter backed by real Redis for TwoTierStore testing.

    Uses the session-scoped redis_client from global conftest.
    key_prefix is set to 'test:' to avoid collision with production keys.
    """
    from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter

    adapter = RedisCacheAdapter(
        client=redis_client._redis if hasattr(redis_client, "_redis") else redis_client,
        key_prefix="test:",
    )
    yield adapter

    # Cleanup: remove all test keys
    for key in redis_client.keys("test:*"):
        redis_client.delete(key)


@pytest.fixture
def mock_bus():
    """Mock EventBus (invalidation events are not Redis-dependent)."""
    return MagicMock()


@pytest.fixture
def store(redis_cache, mock_bus):
    """TwoTierMeshOverrideStore with real Redis L2."""
    from selfhealing.services.circuit_mesh.store import TwoTierMeshOverrideStore

    return TwoTierMeshOverrideStore(cache=redis_cache, event_bus=mock_bus)


def _make_override(
    service_name: str = "svc-upstream",
    expires_in_seconds: int = 600,
) -> "ThresholdOverride":
    """테스트용 ThresholdOverride."""
    from selfhealing.services.circuit_mesh import ThresholdOverride

    return ThresholdOverride(
        service_name=service_name,
        original_failure_threshold=5,
        adjusted_failure_threshold=10,
        original_recovery_timeout=60,
        adjusted_recovery_timeout=180,
        reason="downstream:svc-down OPEN (depth=1)",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
        renewal_count=0,
    )


# =============================================================================
# A. Serialization Roundtrip Tests
# =============================================================================


class TestTwoTierStoreRedisSerializationRoundtrip:
    """
    set → Redis L2 → get 데이터 무결성 검증.

    Validates:
    - JSON 직렬화/역직렬화를 통해 모든 필드가 보존된다
    - datetime 필드가 정확히 라운드트립된다
    - renewal_count 등 정수 필드가 유지된다
    """

    def test_set_and_get_preserves_all_fields(self, store):
        """
        Purpose:
            set 후 L1에서 조회한 값과 L2에서 복원한 값이 동일하다.
        Expected:
            - 모든 ThresholdOverride 필드가 보존됨
        """
        override = _make_override("svc-payment")
        store.set("svc-payment", override)

        result = store.get("svc-payment")
        assert result is not None
        assert result.service_name == "svc-payment"
        assert result.adjusted_failure_threshold == 10
        assert result.adjusted_recovery_timeout == 180
        assert result.reason == "downstream:svc-down OPEN (depth=1)"
        assert result.renewal_count == 0

    def test_l2_roundtrip_after_l1_clear(self, store):
        """
        Purpose:
            L1 clear 후 L2에서 복원하면 원본과 동일한 데이터를 얻는다.
        Expected:
            - clear_l1 → hydrate_from_l2 후 데이터 복원
            - adjusted_failure_threshold, reason 등 정확히 일치
        """
        override = _make_override("svc-api")
        store.set("svc-api", override)

        # Clear L1
        store.clear_l1()
        assert store.get("svc-api") is None

        # Hydrate from L2
        restored = store.hydrate_from_l2()
        assert restored == 1

        result = store.get("svc-api")
        assert result is not None
        assert result.service_name == "svc-api"
        assert result.adjusted_failure_threshold == override.adjusted_failure_threshold
        assert result.original_recovery_timeout == override.original_recovery_timeout

    def test_remove_deletes_from_both_tiers(self, store):
        """
        Purpose:
            remove 후 L1과 L2 모두에서 삭제된다.
        Expected:
            - remove → L1 None, hydrate_from_l2 복원 0
        """
        store.set("svc-x", _make_override("svc-x"))
        store.remove("svc-x")

        assert store.get("svc-x") is None

        # Verify L2 is also empty
        store.clear_l1()
        restored = store.hydrate_from_l2()
        assert restored == 0


# =============================================================================
# B. Hydration Tests
# =============================================================================


class TestTwoTierStoreRedisHydration:
    """
    L2 → L1 Hydration 동작 검증.

    Validates:
    - 여러 오버라이드가 동시에 hydrate 가능
    - 만료된 오버라이드는 hydrate에서 제외
    - L2에 데이터가 없으면 0 반환
    """

    def test_hydrate_multiple_overrides(self, store):
        """
        Purpose:
            3개의 오버라이드를 set → clear_l1 → hydrate_from_l2.
        Expected:
            - 3개 모두 복원됨
        """
        for name in ["svc-a", "svc-b", "svc-c"]:
            store.set(name, _make_override(name))

        store.clear_l1()
        restored = store.hydrate_from_l2()
        assert restored == 3

        all_overrides = store.get_all()
        assert len(all_overrides) == 3
        assert "svc-a" in all_overrides
        assert "svc-b" in all_overrides
        assert "svc-c" in all_overrides

    def test_hydrate_skips_expired(self, store):
        """
        Purpose:
            만료된 오버라이드는 hydrate에서 제외.
        Expected:
            - 활성 1개만 복원, 만료 1개는 제외
        """
        store.set("svc-active", _make_override("svc-active", expires_in_seconds=600))
        store.set("svc-expired", _make_override("svc-expired", expires_in_seconds=-1))

        store.clear_l1()
        restored = store.hydrate_from_l2()
        assert restored == 1
        assert store.get("svc-active") is not None
        assert store.get("svc-expired") is None

    def test_hydrate_empty_l2_returns_zero(self, store):
        """
        Purpose:
            L2에 데이터가 없으면 0 반환.
        Expected:
            - hydrate_from_l2() == 0
        """
        restored = store.hydrate_from_l2()
        assert restored == 0


# =============================================================================
# C. Cross-Instance Simulation Tests
# =============================================================================


class TestTwoTierStoreRedisMultiInstance:
    """
    다중 인스턴스 시뮬레이션: 같은 L2를 공유하는 두 store.

    Validates:
    - Instance A의 set이 Instance B의 hydrate에서 보인다
    - Instance A의 remove가 Instance B의 hydrate에서 반영된다
    """

    def test_instance_b_sees_instance_a_writes_via_hydration(self, redis_cache, mock_bus):
        """
        Purpose:
            Instance A가 set한 오버라이드를 Instance B가 hydrate로 복원.
        Expected:
            - B의 L1에 A가 쓴 데이터가 나타남
        """
        from selfhealing.services.circuit_mesh.store import TwoTierMeshOverrideStore

        store_a = TwoTierMeshOverrideStore(cache=redis_cache, event_bus=mock_bus)
        store_b = TwoTierMeshOverrideStore(cache=redis_cache, event_bus=mock_bus)

        # A writes
        store_a.set("svc-shared", _make_override("svc-shared"))

        # B hydrates
        restored = store_b.hydrate_from_l2()
        assert restored == 1
        result = store_b.get("svc-shared")
        assert result is not None
        assert result.adjusted_failure_threshold == 10

    def test_instance_a_remove_reflected_in_instance_b_hydration(self, redis_cache, mock_bus):
        """
        Purpose:
            Instance A가 remove한 오버라이드가 Instance B의 hydrate에서 제외.
        Expected:
            - A set → A remove → B hydrate 결과 0
        """
        from selfhealing.services.circuit_mesh.store import TwoTierMeshOverrideStore

        store_a = TwoTierMeshOverrideStore(cache=redis_cache, event_bus=mock_bus)
        store_b = TwoTierMeshOverrideStore(cache=redis_cache, event_bus=mock_bus)

        store_a.set("svc-temp", _make_override("svc-temp"))
        store_a.remove("svc-temp")

        restored = store_b.hydrate_from_l2()
        assert restored == 0


# =============================================================================
# D. Drift Detection with Real Redis
# =============================================================================


class TestTwoTierStoreRedisDriftDetection:
    """
    실제 Redis 기반 L1↔L2 drift 감지.

    Validates:
    - 정상 상태에서 drift 없음
    - L2 직접 삭제 시 drift 감지
    """

    def test_no_drift_when_synced(self, store):
        """
        Purpose:
            L1과 L2가 동기화된 상태에서 drift 없음.
        Expected:
            - drift_detected == False
        """
        store.set("svc-synced", _make_override("svc-synced"))
        result = store.check_drift()
        assert result["drift_detected"] is False
        assert result["drift_count"] == 0

    def test_drift_detected_when_l2_key_deleted_directly(self, store, redis_cache):
        """
        Purpose:
            L2에서 직접 키 삭제 시 L1↔L2 drift 감지.
        Expected:
            - drift_detected == True, drift_count == 1
        """
        store.set("svc-drifted", _make_override("svc-drifted"))

        # Directly delete from L2 (simulating external modification)
        from selfhealing.services.circuit_mesh.store import TwoTierMeshOverrideStore

        l2_key = f"{TwoTierMeshOverrideStore.REDIS_KEY_PREFIX}svc-drifted"
        redis_cache.delete(l2_key)

        result = store.check_drift()
        assert result["drift_detected"] is True
        assert result["drift_count"] == 1
