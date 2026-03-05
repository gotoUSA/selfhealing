"""
InMemoryMeshOverrideStore 단위 테스트.

테스트 대상: services/circuit_mesh/store.py — InMemoryMeshOverrideStore
검증 기법: CRUD 동작, TTL 만료 (시간 의존성), 멱등성
"""

from __future__ import annotations

import pytest

from selfhealing.services.circuit_mesh.store import (
    InMemoryMeshOverrideStore,
    MeshOverrideStore,
)

from .conftest import make_override as _make_override

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def store():
    """InMemoryMeshOverrideStore 인스턴스."""
    return InMemoryMeshOverrideStore()


# =============================================================================
# 계약 검증 (Contract)
# =============================================================================


class TestInMemoryMeshOverrideStoreContract:
    """InMemoryMeshOverrideStore가 MeshOverrideStore 프로토콜을 충족."""

    def test_implements_mesh_override_store_protocol(self):
        """InMemoryMeshOverrideStore는 MeshOverrideStore 프로토콜을 만족한다."""
        store = InMemoryMeshOverrideStore()
        assert isinstance(store, MeshOverrideStore)


# =============================================================================
# 동작 검증 (Behavior)
# =============================================================================


class TestInMemoryMeshOverrideStoreBehavior:
    """InMemoryMeshOverrideStore CRUD 동작 검증."""

    def test_get_returns_none_for_nonexistent_key(self, store):
        """존재하지 않는 키 조회 시 None 반환."""
        assert store.get("nonexistent") is None

    def test_set_and_get_returns_stored_override(self, store):
        """set 후 get으로 동일 오버라이드 반환."""
        override = _make_override("svc-a")
        store.set("svc-a", override)
        result = store.get("svc-a")
        assert result is not None
        assert result.service_name == "svc-a"
        assert result.adjusted_failure_threshold == 10

    def test_set_overwrites_existing_override(self, store):
        """동일 키에 set 시 기존 오버라이드 교체."""
        override_1 = _make_override("svc-a")
        override_2 = _make_override("svc-a")
        override_2.adjusted_failure_threshold = 20

        store.set("svc-a", override_1)
        store.set("svc-a", override_2)

        result = store.get("svc-a")
        assert result.adjusted_failure_threshold == 20

    def test_remove_deletes_override(self, store):
        """remove 후 get은 None 반환."""
        override = _make_override("svc-a")
        store.set("svc-a", override)
        store.remove("svc-a")
        assert store.get("svc-a") is None

    def test_remove_nonexistent_key_does_not_raise(self, store):
        """존재하지 않는 키 remove 시 에러 없음."""
        store.remove("nonexistent")

    def test_get_all_returns_all_stored_overrides(self, store):
        """get_all은 저장된 모든 오버라이드를 반환."""
        store.set("svc-a", _make_override("svc-a"))
        store.set("svc-b", _make_override("svc-b"))

        all_overrides = store.get_all()
        assert len(all_overrides) == 2
        assert "svc-a" in all_overrides
        assert "svc-b" in all_overrides

    def test_get_all_returns_copy_not_reference(self, store):
        """get_all 반환값 수정이 내부 저장소에 영향 없음."""
        store.set("svc-a", _make_override("svc-a"))
        result = store.get_all()
        result.pop("svc-a", None)
        assert store.get("svc-a") is not None

    def test_get_all_returns_empty_dict_when_empty(self, store):
        """빈 저장소에서 get_all은 빈 dict 반환."""
        assert store.get_all() == {}


class TestInMemoryMeshOverrideStoreTtlBehavior:
    """InMemoryMeshOverrideStore TTL 만료 동작 검증."""

    def test_get_returns_none_for_expired_override(self, store):
        """TTL 만료된 오버라이드 get 시 None 반환."""
        expired = _make_override("svc-a", expires_in_seconds=-1)
        store.set("svc-a", expired)
        assert store.get("svc-a") is None

    def test_get_removes_expired_from_internal_store(self, store):
        """TTL 만료 오버라이드 조회 시 내부에서도 제거."""
        expired = _make_override("svc-a", expires_in_seconds=-1)
        store.set("svc-a", expired)
        store.get("svc-a")
        assert "svc-a" not in store._store

    def test_get_all_excludes_expired_overrides(self, store):
        """get_all은 만료된 오버라이드를 제외."""
        store.set("svc-active", _make_override("svc-active", expires_in_seconds=600))
        store.set("svc-expired", _make_override("svc-expired", expires_in_seconds=-1))

        result = store.get_all()
        assert "svc-active" in result
        assert "svc-expired" not in result

    def test_get_all_cleans_up_expired_from_internal_store(self, store):
        """get_all 호출 시 만료 오버라이드가 내부에서도 제거."""
        store.set("svc-expired", _make_override("svc-expired", expires_in_seconds=-1))
        store.get_all()
        assert "svc-expired" not in store._store


class TestInMemoryMeshOverrideStoreIdempotencyBehavior:
    """InMemoryMeshOverrideStore 멱등성 검증."""

    def test_double_remove_is_idempotent(self, store):
        """동일 키 2회 remove는 멱등."""
        store.set("svc-a", _make_override("svc-a"))
        store.remove("svc-a")
        store.remove("svc-a")
        assert store.get("svc-a") is None

    def test_set_same_override_twice_is_idempotent(self, store):
        """동일 오버라이드 2회 set 시 결과 동일."""
        override = _make_override("svc-a")
        store.set("svc-a", override)
        store.set("svc-a", override)
        assert store.get("svc-a") is override
        assert len(store.get_all()) == 1
