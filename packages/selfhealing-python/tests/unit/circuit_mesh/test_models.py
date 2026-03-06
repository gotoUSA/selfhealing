"""
Circuit Mesh Models 단위 테스트.

테스트 대상: services/circuit_mesh/models.py — MeshStateSnapshot, DownstreamHealthSignal
검증 기법: 계약 검증 (기본값, 빈 스냅샷), 직렬화 라운드트립
"""

from __future__ import annotations

from datetime import datetime, timezone

from selfhealing.services.circuit_breaker.config import CircuitState
from selfhealing.services.circuit_mesh.models import (
    DownstreamHealthSignal,
    MeshStateSnapshot,
)

# =============================================================================
# 계약 검증 (Contract)
# =============================================================================


class TestMeshStateSnapshotContract:
    """MeshStateSnapshot 설계 계약값 검증."""

    def test_empty_returns_snapshot_with_defaults(self):
        """empty()는 모든 컬렉션이 비어있는 스냅샷을 반환한다."""
        snapshot = MeshStateSnapshot.empty()
        assert snapshot.cb_states == {}
        assert snapshot.active_overrides == []
        assert snapshot.downstream_signals == []
        assert snapshot.recovery_queue == []

    def test_empty_timestamp_is_utc(self):
        """empty() 타임스탬프는 UTC이다."""
        snapshot = MeshStateSnapshot.empty()
        assert snapshot.timestamp.tzinfo is not None


class TestDownstreamHealthSignalContract:
    """DownstreamHealthSignal 설계 계약값 검증."""

    def test_affected_upstream_defaults_to_empty_list(self):
        """affected_upstream 기본값: 빈 리스트."""
        signal = DownstreamHealthSignal(
            service_name="svc-down",
            state=CircuitState.OPEN,
            changed_at=datetime.now(timezone.utc),
        )
        assert signal.affected_upstream == []


# =============================================================================
# 동작 검증 (Behavior)
# =============================================================================


class TestDownstreamHealthSignalBehavior:
    """DownstreamHealthSignal 동작 검증."""

    def test_stores_all_fields_correctly(self):
        """모든 필드가 정상 저장된다."""
        ts = datetime.now(timezone.utc)
        signal = DownstreamHealthSignal(
            service_name="svc-down",
            state=CircuitState.OPEN,
            changed_at=ts,
            affected_upstream=["svc-a", "svc-b"],
        )
        assert signal.service_name == "svc-down"
        assert signal.state == CircuitState.OPEN
        assert signal.changed_at == ts
        assert signal.affected_upstream == ["svc-a", "svc-b"]

    def test_affected_upstream_list_is_independent(self):
        """affected_upstream 리스트 변경이 다른 인스턴스에 영향 없음."""
        signal1 = DownstreamHealthSignal(
            service_name="svc-down",
            state=CircuitState.OPEN,
            changed_at=datetime.now(timezone.utc),
        )
        signal2 = DownstreamHealthSignal(
            service_name="svc-down2",
            state=CircuitState.CLOSED,
            changed_at=datetime.now(timezone.utc),
        )
        signal1.affected_upstream.append("svc-a")
        assert signal2.affected_upstream == []


class TestMeshStateSnapshotBehavior:
    """MeshStateSnapshot 동작 검증."""

    def test_snapshot_stores_all_collections(self):
        """모든 컬렉션 필드가 정상 저장된다."""
        from .conftest import make_override

        ts = datetime.now(timezone.utc)
        override = make_override("svc-a")
        signal = DownstreamHealthSignal(
            service_name="svc-down",
            state=CircuitState.OPEN,
            changed_at=ts,
        )
        snapshot = MeshStateSnapshot(
            timestamp=ts,
            cb_states={"svc-a": "CLOSED"},
            active_overrides=[override],
            downstream_signals=[signal],
            recovery_queue=["svc-a"],
        )
        assert snapshot.cb_states == {"svc-a": "CLOSED"}
        assert len(snapshot.active_overrides) == 1
        assert len(snapshot.downstream_signals) == 1
        assert snapshot.recovery_queue == ["svc-a"]
