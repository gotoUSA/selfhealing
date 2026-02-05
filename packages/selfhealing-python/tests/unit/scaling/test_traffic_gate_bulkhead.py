"""
TrafficGate Bulkhead 통합 테스트.

TrafficGate에 bulkhead_name 파라미터 추가 후 동작을 검증합니다:
- Bulkhead 획득 성공 시 allowed=True, bulkhead_acquired=True
- Bulkhead 가득 참 시 allowed=False, gate="Bulkhead"
- Bulkhead 획득 후 다른 단계에서 거부 시 자동 release
"""

from __future__ import annotations

import pytest

from selfhealing.core.connection_health import ConnectionType
from selfhealing.resilience.bulkhead.registry import (
    get_bulkhead_registry,
    reset_bulkhead_registry,
)
from selfhealing.scaling.config import BackpressureLevel
from selfhealing.scaling.traffic_gate import (
    TrafficDecision,
    TrafficGate,
    reset_traffic_gate,
)
from selfhealing.settings.bulkhead import reset_bulkhead_settings


@pytest.fixture(autouse=True)
def reset_singletons():
    """각 테스트 전후로 싱글톤 초기화."""
    reset_bulkhead_registry()
    reset_bulkhead_settings()
    reset_traffic_gate()
    yield
    reset_bulkhead_registry()
    reset_bulkhead_settings()
    reset_traffic_gate()


class TestTrafficGateBulkheadIntegration:
    """TrafficGate Bulkhead 통합 테스트."""

    def test_should_allow_without_bulkhead(self):
        """bulkhead_name 없이 호출하면 기존 동작 유지."""
        gate = TrafficGate()

        decision = gate.should_allow(priority=0)

        assert decision.allowed is True
        assert decision.bulkhead_acquired is False
        assert decision.bulkhead_name is None

    def test_should_allow_with_bulkhead_success(self):
        """bulkhead_name으로 호출하면 격벽 획득 후 허용."""
        gate = TrafficGate()

        decision = gate.should_allow(
            priority=0,
            bulkhead_name=ConnectionType.DATABASE.value,
        )

        assert decision.allowed is True
        assert decision.bulkhead_acquired is True
        assert decision.bulkhead_name == "database"

        # 획득 후 반환
        gate.release_bulkhead("database")

    def test_should_reject_when_bulkhead_full(self):
        """격벽이 가득 차면 거부."""
        gate = TrafficGate()
        registry = get_bulkhead_registry()
        db_bulkhead = registry.get(ConnectionType.DATABASE)

        # 격벽의 모든 슬롯 점유
        max_concurrent = db_bulkhead.get_state().max_concurrent
        for _ in range(max_concurrent):
            db_bulkhead.try_acquire()

        decision = gate.should_allow(
            priority=0,
            bulkhead_name="database",
        )

        assert decision.allowed is False
        assert decision.gate == "Bulkhead"
        assert "database" in decision.reason
        assert decision.bulkhead_acquired is False

        # 정리
        for _ in range(max_concurrent):
            db_bulkhead.release()

    def test_release_bulkhead_method(self):
        """release_bulkhead 메서드 동작 확인."""
        gate = TrafficGate()
        registry = get_bulkhead_registry()
        db_bulkhead = registry.get(ConnectionType.DATABASE)

        initial_active = db_bulkhead.get_state().active_count

        decision = gate.should_allow(
            priority=0,
            bulkhead_name="database",
        )

        assert decision.bulkhead_acquired is True
        assert db_bulkhead.get_state().active_count == initial_active + 1

        gate.release_bulkhead("database")

        assert db_bulkhead.get_state().active_count == initial_active

    def test_unknown_bulkhead_skipped(self):
        """등록되지 않은 bulkhead는 무시하고 진행."""
        gate = TrafficGate()

        decision = gate.should_allow(
            priority=0,
            bulkhead_name="unknown_bulkhead",
        )

        assert decision.allowed is True
        assert decision.bulkhead_acquired is False

    def test_traffic_decision_has_bulkhead_fields(self):
        """TrafficDecision에 bulkhead 관련 필드 존재 확인."""
        decision = TrafficDecision(
            allowed=True,
            reason="test",
            level=BackpressureLevel.NONE,
            gate="test",
        )

        assert hasattr(decision, "bulkhead_acquired")
        assert hasattr(decision, "bulkhead_name")
        assert decision.bulkhead_acquired is False
        assert decision.bulkhead_name is None


class TestTrafficGateBulkheadWithLoadShedding:
    """TrafficGate에서 Bulkhead + LoadShedding 조합 테스트."""

    def test_bulkhead_acquired_then_load_shedding_rejects(self):
        """
        Bulkhead 획득 후 LoadShedding에서 거부 시 Bulkhead 자동 반환.
        """

        # 항상 거부하는 MockLoadShedding
        class MockLoadShedding:
            def should_accept(self, **kwargs):
                return {"accepted": False}

        gate = TrafficGate(load_shedding=MockLoadShedding())
        registry = get_bulkhead_registry()
        db_bulkhead = registry.get(ConnectionType.DATABASE)

        initial_active = db_bulkhead.get_state().active_count

        decision = gate.should_allow(
            priority=5,
            bulkhead_name="database",
        )

        # LoadShedding에서 거부됨
        assert decision.allowed is False
        assert decision.gate == "CascadeLoadShedding"

        # Bulkhead는 자동 반환되어야 함
        assert db_bulkhead.get_state().active_count == initial_active
