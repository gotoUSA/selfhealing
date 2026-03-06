"""
TwoTierMeshOverrideStore 단위 테스트.

테스트 대상: services/circuit_mesh/store.py — TwoTierMeshOverrideStore
검증 기법: L1/L2 동기화, 의존성 상호작용, 무효화 이벤트, drift 감지,
          TTL 만료, graceful degradation (L2 장애 시)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from selfhealing.services.circuit_mesh.store import (
    MeshOverrideStore,
    TwoTierMeshOverrideStore,
)

from .conftest import make_override as _make_override

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_cache():
    """Mock CacheProviderInterface."""
    cache = MagicMock()
    cache.get.return_value = None
    return cache


@pytest.fixture
def mock_bus():
    """Mock SelfHealingEventBus."""
    return MagicMock()


@pytest.fixture
def store(mock_cache, mock_bus):
    """TwoTierMeshOverrideStore 인스턴스."""
    return TwoTierMeshOverrideStore(cache=mock_cache, event_bus=mock_bus)


# =============================================================================
# 계약 검증 (Contract)
# =============================================================================


class TestTwoTierStoreContract:
    """TwoTierMeshOverrideStore 프로토콜 계약 검증."""

    def test_implements_mesh_override_store_protocol(self, store):
        """MeshOverrideStore 프로토콜을 만족한다."""
        assert isinstance(store, MeshOverrideStore)

    def test_redis_key_prefix_contract(self):
        """Redis 키 접두사 계약값: 'selfhealing:mesh:override:'."""
        assert TwoTierMeshOverrideStore.REDIS_KEY_PREFIX == "selfhealing:mesh:override:"


# =============================================================================
# 동작 검증 (Behavior) — L1 CRUD
# =============================================================================


class TestTwoTierStoreL1Behavior:
    """L1 인메모리 CRUD 동작 검증."""

    def test_get_returns_none_for_nonexistent_key(self, store):
        """존재하지 않는 키 조회 시 None 반환."""
        assert store.get("nonexistent") is None

    def test_set_stores_in_l1(self, store):
        """set 후 L1에서 즉시 조회 가능."""
        override = _make_override("svc-a")
        store.set("svc-a", override)
        assert store.get("svc-a") is not None
        assert store.get("svc-a").service_name == "svc-a"

    def test_remove_clears_from_l1(self, store):
        """remove 후 L1에서 조회 불가."""
        override = _make_override("svc-a")
        store.set("svc-a", override)
        store.remove("svc-a")
        assert store.get("svc-a") is None

    def test_get_all_returns_all_l1_entries(self, store):
        """get_all은 L1의 모든 항목 반환."""
        store.set("svc-a", _make_override("svc-a"))
        store.set("svc-b", _make_override("svc-b"))
        result = store.get_all()
        assert len(result) == 2
        assert "svc-a" in result
        assert "svc-b" in result

    def test_get_returns_none_for_expired_override(self, store):
        """TTL 만료 오버라이드 get 시 None 반환."""
        expired = _make_override("svc-a", expires_in_seconds=-1)
        store.set("svc-a", expired)
        assert store.get("svc-a") is None

    def test_get_all_excludes_expired_entries(self, store):
        """get_all은 만료 항목 제외."""
        store.set("svc-active", _make_override("svc-active", expires_in_seconds=600))
        store.set("svc-expired", _make_override("svc-expired", expires_in_seconds=-1))
        result = store.get_all()
        assert "svc-active" in result
        assert "svc-expired" not in result


# =============================================================================
# 동작 검증 (Behavior) — L2 동기화
# =============================================================================


class TestTwoTierStoreL2SyncBehavior:
    """L2 Redis 동기화 동작 검증."""

    def test_set_syncs_to_l2_cache_per_key(self, store, mock_cache):
        """set 시 L2 Redis에 per-key로 동기화한다."""
        override = _make_override("svc-a")
        store.set("svc-a", override)

        mock_cache.set.assert_called_once()
        call_args = mock_cache.set.call_args
        expected_key = f"{TwoTierMeshOverrideStore.REDIS_KEY_PREFIX}svc-a"
        assert call_args[0][0] == expected_key
        stored_data = json.loads(call_args[0][1])
        assert stored_data["adjusted_failure_threshold"] == 10

    def test_remove_deletes_from_l2_cache(self, store, mock_cache):
        """remove 시 L2 Redis에서 per-key 삭제한다."""
        store.set("svc-a", _make_override("svc-a"))
        mock_cache.reset_mock()

        store.remove("svc-a")
        expected_key = f"{TwoTierMeshOverrideStore.REDIS_KEY_PREFIX}svc-a"
        mock_cache.delete.assert_called_once_with(expected_key)

    def test_l2_sync_failure_does_not_affect_l1(self, store, mock_cache):
        """L2 쓰기 실패 시 L1은 정상 작동 (graceful degradation)."""
        mock_cache.set.side_effect = Exception("Redis connection error")

        override = _make_override("svc-a")
        store.set("svc-a", override)

        assert store.get("svc-a") is not None


# =============================================================================
# 동작 검증 (Behavior) — 무효화 이벤트
# =============================================================================


class TestTwoTierStoreInvalidationBehavior:
    """무효화 이벤트 발행/수신 동작 검증."""

    def test_set_publishes_invalidation_event(self, store, mock_bus):
        """set 시 무효화 이벤트를 발행한다."""
        store.set("svc-a", _make_override("svc-a"))

        mock_bus.emit.assert_called_once_with(
            event_type="mesh_override_invalidation",
            data={"service_name": "svc-a", "action": "set"},
            source="mesh_override_store",
        )

    def test_remove_publishes_invalidation_event(self, store, mock_bus):
        """remove 시 무효화 이벤트를 발행한다."""
        store.set("svc-a", _make_override("svc-a"))
        mock_bus.reset_mock()
        store.remove("svc-a")

        mock_bus.emit.assert_called_once_with(
            event_type="mesh_override_invalidation",
            data={"service_name": "svc-a", "action": "remove"},
            source="mesh_override_store",
        )

    def test_on_invalidation_remove_action_removes_from_l1(self, store):
        """remove 무효화 이벤트 수신 시 L1에서 제거."""
        store._l1["svc-a"] = _make_override("svc-a")

        event = MagicMock()
        event.data = {"service_name": "svc-a", "action": "remove"}
        store.on_invalidation_event(event)

        assert "svc-a" not in store._l1

    def test_on_invalidation_set_action_fetches_from_l2(self, store, mock_cache):
        """set 무효화 이벤트 수신 시 L2에서 가져와 L1 갱신."""
        override_data = {
            "service_name": "svc-a",
            "original_failure_threshold": 5,
            "adjusted_failure_threshold": 15,
            "original_recovery_timeout": 60,
            "adjusted_recovery_timeout": 180,
            "reason": "downstream:svc-down OPEN (depth=1)",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=600)
            ).isoformat(),
            "renewal_count": 0,
        }
        mock_cache.get.return_value = json.dumps(override_data)

        event = MagicMock()
        event.data = {"service_name": "svc-a", "action": "set"}
        store.on_invalidation_event(event)

        assert "svc-a" in store._l1
        assert store._l1["svc-a"].adjusted_failure_threshold == 15

    def test_invalidation_publish_failure_does_not_raise(self, store, mock_bus):
        """이벤트 발행 실패 시 예외 전파 없음 (graceful degradation)."""
        mock_bus.emit.side_effect = Exception("EventBus failure")
        store.set("svc-a", _make_override("svc-a"))
        assert store.get("svc-a") is not None


# =============================================================================
# 동작 검증 (Behavior) — Drift 감지
# =============================================================================


class TestTwoTierStoreDriftBehavior:
    """L1↔L2 drift 감지 동작 검증."""

    def test_no_drift_when_both_empty(self, store, mock_cache):
        """L1, L2 모두 비어있으면 drift 없음."""
        mock_cache.get.return_value = None
        result = store.check_drift()
        assert result["drift_detected"] is False
        assert result["drift_count"] == 0

    def test_drift_detected_when_l1_has_data_but_l2_empty(self, store, mock_cache):
        """L1에 데이터 있으나 L2가 비어있으면 drift 감지."""
        store._l1["svc-a"] = _make_override("svc-a")
        mock_cache.get.return_value = None
        result = store.check_drift()
        assert result["drift_detected"] is True
        assert result["drift_count"] == 1

    def test_no_drift_when_l2_has_data(self, store, mock_cache):
        """L1과 L2 모두 데이터가 있으면 drift 없음."""
        store._l1["svc-a"] = _make_override("svc-a")
        mock_cache.get.return_value = json.dumps({"service_name": "svc-a"})
        result = store.check_drift()
        assert result["drift_detected"] is False
        assert result["drift_count"] == 0

    def test_drift_check_failure_returns_gracefully(self, store, mock_cache):
        """drift 체크 중 예외 시 graceful 반환."""
        store._l1["svc-a"] = _make_override("svc-a")
        mock_cache.get.side_effect = Exception("Redis timeout")
        result = store.check_drift()
        assert result["drift_detected"] is False
        assert result["drift_count"] == 0


# =============================================================================
# 동작 검증 (Behavior) — clear_l1
# =============================================================================


class TestTwoTierStoreClearL1Behavior:
    """clear_l1() 동작 검증."""

    def test_clear_l1_removes_all_entries(self, store):
        """clear_l1은 L1의 모든 항목을 제거한다."""
        store.set("svc-a", _make_override("svc-a"))
        store.set("svc-b", _make_override("svc-b"))
        store.clear_l1()
        assert store.get_all() == {}

    def test_clear_l1_does_not_touch_l2(self, store, mock_cache):
        """clear_l1은 L2(Redis)를 건드리지 않는다."""
        store.set("svc-a", _make_override("svc-a"))
        mock_cache.reset_mock()
        store.clear_l1()
        mock_cache.delete.assert_not_called()

    def test_clear_l1_on_empty_store_is_safe(self, store):
        """빈 스토어에서 clear_l1 호출 시 예외 없음."""
        store.clear_l1()
        assert store.get_all() == {}

    def test_clear_l1_idempotent(self, store):
        """clear_l1 연속 호출은 동일 결과."""
        store.set("svc-a", _make_override("svc-a"))
        store.clear_l1()
        store.clear_l1()
        assert store.get_all() == {}


# =============================================================================
# 동작 검증 (Behavior) — hydrate_from_l2
# =============================================================================


class TestTwoTierStoreHydrateFromL2Behavior:
    """hydrate_from_l2() 동작 검증."""

    def test_hydrate_restores_valid_overrides(self, store, mock_cache):
        """hydrate_from_l2는 만료되지 않은 오버라이드를 L1에 복원한다."""
        from datetime import timedelta

        future_time = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        override_data = json.dumps(
            {
                "service_name": "svc-a",
                "original_failure_threshold": 5,
                "adjusted_failure_threshold": 10,
                "original_recovery_timeout": 60,
                "adjusted_recovery_timeout": 180,
                "reason": "test",
                "expires_at": future_time,
                "renewal_count": 0,
            }
        )

        mock_cache.keys.return_value = [
            f"{TwoTierMeshOverrideStore.REDIS_KEY_PREFIX}svc-a"
        ]
        mock_cache.get.return_value = override_data

        restored = store.hydrate_from_l2()
        assert restored == 1
        assert store.get("svc-a") is not None
        assert store.get("svc-a").adjusted_failure_threshold == 10

    def test_hydrate_skips_expired_overrides(self, store, mock_cache):
        """hydrate_from_l2는 만료된 오버라이드를 복원하지 않는다."""
        from datetime import timedelta

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        override_data = json.dumps(
            {
                "service_name": "svc-expired",
                "original_failure_threshold": 5,
                "adjusted_failure_threshold": 10,
                "original_recovery_timeout": 60,
                "adjusted_recovery_timeout": 180,
                "reason": "test",
                "expires_at": past_time,
                "renewal_count": 0,
            }
        )

        mock_cache.keys.return_value = [
            f"{TwoTierMeshOverrideStore.REDIS_KEY_PREFIX}svc-expired"
        ]
        mock_cache.get.return_value = override_data

        restored = store.hydrate_from_l2()
        assert restored == 0

    def test_hydrate_returns_zero_when_no_keys(self, store, mock_cache):
        """L2에 키가 없으면 0을 반환한다."""
        mock_cache.keys.return_value = []
        restored = store.hydrate_from_l2()
        assert restored == 0

    def test_hydrate_failure_returns_zero_gracefully(self, store, mock_cache):
        """L2 접근 실패 시 0을 반환한다 (graceful degradation)."""
        mock_cache.keys.side_effect = Exception("Redis connection error")
        restored = store.hydrate_from_l2()
        assert restored == 0

    def test_hydrate_restores_multiple_overrides(self, store, mock_cache):
        """hydrate_from_l2는 여러 오버라이드를 모두 복원한다."""
        from datetime import timedelta

        future_time = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()

        def mock_get(key):
            svc_name = key.removeprefix(TwoTierMeshOverrideStore.REDIS_KEY_PREFIX)
            return json.dumps(
                {
                    "service_name": svc_name,
                    "original_failure_threshold": 5,
                    "adjusted_failure_threshold": 10,
                    "original_recovery_timeout": 60,
                    "adjusted_recovery_timeout": 180,
                    "reason": "test",
                    "expires_at": future_time,
                    "renewal_count": 0,
                }
            )

        prefix = TwoTierMeshOverrideStore.REDIS_KEY_PREFIX
        mock_cache.keys.return_value = [f"{prefix}svc-a", f"{prefix}svc-b"]
        mock_cache.get.side_effect = mock_get

        restored = store.hydrate_from_l2()
        assert restored == 2
        assert store.get("svc-a") is not None
        assert store.get("svc-b") is not None
