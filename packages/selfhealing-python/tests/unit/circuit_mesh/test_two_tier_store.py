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

    def test_redis_key_contract(self):
        """Redis 키 계약값: 'selfhealing:mesh:overrides'."""
        assert TwoTierMeshOverrideStore.REDIS_KEY == "selfhealing:mesh:overrides"


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

    def test_set_syncs_to_l2_cache(self, store, mock_cache):
        """set 시 L2 Redis에 동기화한다."""
        override = _make_override("svc-a")
        store.set("svc-a", override)

        mock_cache.set.assert_called_once()
        call_args = mock_cache.set.call_args
        assert call_args[0][0] == TwoTierMeshOverrideStore.REDIS_KEY
        stored_data = json.loads(call_args[0][1])
        assert "svc-a" in stored_data
        assert stored_data["svc-a"]["adjusted_failure_threshold"] == 10

    def test_remove_removes_from_l2_cache(self, store, mock_cache):
        """remove 시 L2 Redis에서도 제거한다."""
        # Given — L2에 기존 데이터 존재
        existing = json.dumps({"svc-a": {"service_name": "svc-a"}})
        mock_cache.get.return_value = existing

        store.set("svc-a", _make_override("svc-a"))
        mock_cache.reset_mock()
        mock_cache.get.return_value = existing

        store.remove("svc-a")
        mock_cache.set.assert_called_once()
        stored_data = json.loads(mock_cache.set.call_args[0][1])
        assert "svc-a" not in stored_data

    def test_set_appends_to_existing_l2_data(self, store, mock_cache):
        """L2에 기존 데이터가 있을 때 set은 기존 데이터를 보존하며 추가."""
        existing = json.dumps({"svc-existing": {"service_name": "svc-existing"}})
        mock_cache.get.return_value = existing

        store.set("svc-new", _make_override("svc-new"))

        stored_data = json.loads(mock_cache.set.call_args[0][1])
        assert "svc-existing" in stored_data
        assert "svc-new" in stored_data

    def test_l2_sync_failure_does_not_affect_l1(self, store, mock_cache):
        """L2 쓰기 실패 시 L1은 정상 작동 (graceful degradation)."""
        mock_cache.get.side_effect = Exception("Redis connection error")

        override = _make_override("svc-a")
        store.set("svc-a", override)

        # L1은 여전히 정상
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
        # Given — L2에 최신 데이터 존재
        override_data = {
            "svc-a": {
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
        # 예외 없이 L1은 정상
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

    def test_drift_detected_when_keys_differ(self, store, mock_cache):
        """L1과 L2의 키가 다르면 drift 감지."""
        store._l1["svc-a"] = _make_override("svc-a")
        mock_cache.get.return_value = json.dumps({"svc-b": {}})
        result = store.check_drift()
        assert result["drift_detected"] is True
        assert result["drift_count"] == 2  # svc-a + svc-b (symmetric diff)

    def test_no_drift_when_keys_match(self, store, mock_cache):
        """L1과 L2의 키가 동일하면 drift 없음."""
        store._l1["svc-a"] = _make_override("svc-a")
        mock_cache.get.return_value = json.dumps({"svc-a": {}})
        result = store.check_drift()
        assert result["drift_detected"] is False
        assert result["drift_count"] == 0

    def test_drift_check_failure_returns_gracefully(self, store, mock_cache):
        """drift 체크 중 예외 시 graceful 반환."""
        mock_cache.get.side_effect = Exception("Redis timeout")
        result = store.check_drift()
        assert result["drift_detected"] is False
        assert result["drift_count"] == 0
