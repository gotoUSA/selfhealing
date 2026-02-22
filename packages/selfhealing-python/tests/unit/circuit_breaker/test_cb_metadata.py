"""
CircuitBreakerStateData metadata 필드 단위 테스트.

테스트 대상:
- interfaces/repositories.py CircuitBreakerStateData.metadata
- adapters/memory/circuit_breaker.py update_metadata()
- services/circuit_breaker/service.py get_all_states() metadata 직렬화
"""

from __future__ import annotations

from dataclasses import fields
from unittest.mock import MagicMock

from selfhealing.adapters.memory.circuit_breaker import (
    InMemoryCircuitBreakerStateRepository,
)
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateData,
    CircuitBreakerStateRepository,
)
from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
from selfhealing.services.circuit_breaker.service import CircuitBreakerService

# =============================================================================
# 계약 검증 (Contract)
# =============================================================================


class TestCircuitBreakerMetadataContract:
    """CircuitBreakerStateData metadata 필드 계약 검증."""

    def test_metadata_field_exists(self):
        """CircuitBreakerStateData에 metadata 필드가 존재한다."""
        field_names = [f.name for f in fields(CircuitBreakerStateData)]
        assert "metadata" in field_names

    def test_metadata_default_is_empty_dict(self):
        """metadata 기본값은 빈 dict이다."""
        state = CircuitBreakerStateData(service_name="test")
        assert state.metadata == {}

    def test_metadata_default_factory_creates_independent_dicts(self):
        """각 인스턴스의 metadata는 독립된 dict이다 (mutable default 안전)."""
        state1 = CircuitBreakerStateData(service_name="svc1")
        state2 = CircuitBreakerStateData(service_name="svc2")
        state1.metadata["key"] = "value"
        assert state2.metadata == {}

    def test_metadata_field_type_annotation(self):
        """metadata 필드의 타입 어노테이션은 dict[str, Any]이다."""
        for f in fields(CircuitBreakerStateData):
            if f.name == "metadata":
                assert "dict" in str(f.type)
                break

    def test_get_all_states_includes_metadata_key(self):
        """get_all_states()의 직렬화 결과에 'metadata' 키가 포함된다."""
        repo = InMemoryCircuitBreakerStateRepository()
        repo.get_or_create("test_service")
        service = CircuitBreakerService(
            config=CircuitBreakerConfig(enabled=True),
            repository=repo,
        )
        states = service.get_all_states()
        assert len(states) == 1
        assert "metadata" in states[0]


# =============================================================================
# 동작 검증 (Behavior)
# =============================================================================


class TestCircuitBreakerMetadataBehavior:
    """CircuitBreakerStateData metadata 동작 검증."""

    def test_metadata_can_store_arbitrary_data(self):
        """metadata에 임의의 dict 데이터를 저장할 수 있다."""
        meta = {"region_id": "us-east-1", "tenant_id": "t-123"}
        state = CircuitBreakerStateData(service_name="svc", metadata=meta)
        assert state.metadata == meta

    def test_backward_compatibility_without_metadata(self):
        """metadata 없이 생성해도 기존 필드가 정상 동작한다."""
        state = CircuitBreakerStateData(service_name="svc", state="open")
        assert state.service_name == "svc"
        assert state.state == "open"
        assert state.metadata == {}


class TestInMemoryUpdateMetadataBehavior:
    """InMemory Repository update_metadata() 동작 검증."""

    def test_update_metadata_returns_true_on_success(self):
        """존재하는 서비스의 metadata 업데이트는 True를 반환한다."""
        repo = InMemoryCircuitBreakerStateRepository()
        repo.get_or_create("svc")
        result = repo.update_metadata("svc", {"key": "value"})
        assert result is True

    def test_update_metadata_returns_false_for_nonexistent(self):
        """존재하지 않는 서비스의 metadata 업데이트는 False를 반환한다."""
        repo = InMemoryCircuitBreakerStateRepository()
        result = repo.update_metadata("nonexistent", {"key": "value"})
        assert result is False

    def test_update_metadata_changes_only_metadata_field(self):
        """update_metadata는 metadata만 변경하고 state 등 다른 필드는 영향 없다."""
        repo = InMemoryCircuitBreakerStateRepository()
        repo.get_or_create("svc")
        repo.update_state("svc", "open", failure_count=5)

        repo.update_metadata("svc", {"region_id": "ap-northeast-2"})

        state = repo.get_by_service_name("svc")
        assert state.state == "open"
        assert state.failure_count == 5
        assert state.metadata == {"region_id": "ap-northeast-2"}

    def test_update_metadata_replaces_entire_metadata(self):
        """update_metadata는 기존 metadata를 완전 교체한다 (merge 아님)."""
        repo = InMemoryCircuitBreakerStateRepository()
        repo.get_or_create("svc")
        repo.update_metadata("svc", {"a": 1, "b": 2})
        repo.update_metadata("svc", {"c": 3})

        state = repo.get_by_service_name("svc")
        assert state.metadata == {"c": 3}


class TestABCUpdateMetadataDefaultBehavior:
    """ABC update_metadata() 기본 구현 동작 검증."""

    def test_abc_default_returns_false_when_service_not_found(self):
        """기본 구현은 서비스가 없으면 False를 반환한다."""
        repo = MagicMock(spec=CircuitBreakerStateRepository)
        repo.get_by_service_name.return_value = None
        # ABC 기본 구현을 직접 호출
        result = CircuitBreakerStateRepository.update_metadata(repo, "nonexistent", {"key": "val"})
        assert result is False


class TestGetAllStatesSerializationBehavior:
    """get_all_states() metadata 직렬화 동작 검증."""

    def test_metadata_values_serialized_correctly(self):
        """저장된 metadata 값이 get_all_states()에 정확히 직렬화된다."""
        repo = InMemoryCircuitBreakerStateRepository()
        repo.get_or_create("svc")
        repo.update_metadata("svc", {"deployment_version": "v2.1.0"})

        service = CircuitBreakerService(
            config=CircuitBreakerConfig(enabled=True),
            repository=repo,
        )
        states = service.get_all_states()
        assert states[0]["metadata"] == {"deployment_version": "v2.1.0"}

    def test_empty_metadata_serialized_as_empty_dict(self):
        """metadata 없는 서비스는 빈 dict으로 직렬화된다."""
        repo = InMemoryCircuitBreakerStateRepository()
        repo.get_or_create("svc")

        service = CircuitBreakerService(
            config=CircuitBreakerConfig(enabled=True),
            repository=repo,
        )
        states = service.get_all_states()
        assert states[0]["metadata"] == {}
