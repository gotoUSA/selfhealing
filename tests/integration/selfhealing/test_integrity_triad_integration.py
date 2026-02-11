"""
Hash Chain Integrity Triad 통합 테스트.

EventBus → IntegrityGate → HashChainVerifier 간의 실제 연동을 테스트합니다.
- CB CLOSED 이벤트 발행 시 IntegrityGate 핸들러의 CRITICAL 우선순위 실행 검증
- 정상 체인: 게이트 통과 후 리플레이 허용
- 위반 체인: 게이트 차단으로 리플레이 블록
- Fail-Open / Fail-Secure 정책 적용 확인

Requirements:
- Docker Compose for Redis and Postgres
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: docker-compose -f docker-compose.test.yml run test pytest tests/integration/selfhealing/test_integrity_triad_integration.py -v
"""

import os
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import patch, MagicMock

from selfhealing.services.event_bus.integrity_gate import (
    INTEGRITY_GATE_KEY,
    INTEGRITY_FAILED_KEY,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_eventbus_and_settings():
    """각 테스트 전/후 EventBus 및 설정 상태 리셋."""
    from selfhealing.services.event_bus import get_event_bus
    from selfhealing.settings.audit_integrity import reset_audit_integrity_settings

    get_event_bus().reset()
    reset_audit_integrity_settings()
    yield
    get_event_bus().reset()
    reset_audit_integrity_settings()


# =============================================================================
# Test Class: IntegrityGate ↔ EventBus Integration
# =============================================================================


@pytest.mark.django_db
class TestIntegrityGateEventBusIntegration:
    """IntegrityGate와 EventBus 간 통합 테스트."""

    def test_integrity_gate_registered_as_critical_priority(self):
        """IntegrityGate 핸들러가 CRITICAL 우선순위로 등록된다."""
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.bus import register_default_handlers

        bus = SelfHealingEventBus()
        bus.reset()
        register_default_handlers()

        # CIRCUIT_BREAKER_CLOSED 구독 확인
        # get_subscriptions()는 dict 리스트 반환: {"priority": "CRITICAL", ...}
        subscriptions = bus.get_subscriptions(EventType.CIRCUIT_BREAKER_CLOSED)
        has_critical = any(sub["priority"] == EventPriority.CRITICAL.name for sub in subscriptions)
        assert has_critical, "IntegrityGate must be registered with CRITICAL priority"

    @patch("selfhealing.services.event_bus.integrity_gate._get_unsynced_wal_entries")
    @patch("selfhealing.services.event_bus.integrity_gate.get_integrity_health_score")
    @patch("selfhealing.services.event_bus.integrity_gate.HashChainVerifier")
    def test_valid_chain_allows_replay(self, mock_verifier_cls, mock_health, mock_get_entries):
        """정상 해시체인이면 IntegrityGate가 리플레이를 허용한다."""
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        # Mock 설정: 정상 체인
        mock_get_entries.return_value = [{"event_type": "TEST", "seq": 1}]
        verifier_instance = MagicMock()
        verifier_instance.verify_chain.return_value = (True, None)
        mock_verifier_cls.return_value = verifier_instance
        mock_health.return_value = MagicMock()

        bus = SelfHealingEventBus()
        bus.reset()

        # 핸들러 등록: IntegrityGate (CRITICAL) + replay mock (NORMAL)
        replay_called = []

        def mock_replay_handler(event):
            """리플레이 핸들러 — IntegrityGate 이후 실행."""
            if not event.data.get(INTEGRITY_FAILED_KEY, False):
                replay_called.append(True)

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            mock_replay_handler,
            priority=EventPriority.NORMAL,
        )

        # 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )
        bus.publish(event)

        # 검증
        assert event.data.get(INTEGRITY_FAILED_KEY) is False
        assert len(replay_called) == 1, "Replay should be allowed on valid chain"

    @patch("selfhealing.services.event_bus.integrity_gate._send_integrity_violation_alert")
    @patch("selfhealing.services.event_bus.integrity_gate._get_unsynced_wal_entries")
    @patch("selfhealing.services.event_bus.integrity_gate.get_integrity_health_score")
    @patch("selfhealing.services.event_bus.integrity_gate.HashChainVerifier")
    def test_broken_chain_blocks_replay(self, mock_verifier_cls, mock_health, mock_get_entries, mock_alert):
        """위반 해시체인이면 IntegrityGate가 리플레이를 차단한다."""
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        # Mock 설정: 위반 체인
        mock_get_entries.return_value = [{"event_type": "TEST", "seq": 1}]
        verifier_instance = MagicMock()
        verifier_instance.verify_chain.return_value = (False, "hash mismatch")
        verifier_instance.find_tampering.return_value = [{"message": "tampered"}]
        mock_verifier_cls.return_value = verifier_instance
        mock_health.return_value = MagicMock()

        bus = SelfHealingEventBus()
        bus.reset()

        replay_called = []

        def mock_replay_handler(event):
            """리플레이 핸들러 — integrity_failed 시 스킵."""
            if not event.data.get(INTEGRITY_FAILED_KEY, False):
                replay_called.append(True)

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            mock_replay_handler,
            priority=EventPriority.NORMAL,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )
        bus.publish(event)

        assert event.data.get(INTEGRITY_FAILED_KEY) is True
        assert len(replay_called) == 0, "Replay should be BLOCKED on broken chain"

    @patch("selfhealing.services.event_bus.integrity_gate._get_unsynced_wal_entries")
    @patch("selfhealing.services.event_bus.integrity_gate.get_integrity_health_score")
    @patch("selfhealing.services.event_bus.integrity_gate.HashChainVerifier")
    def test_critical_priority_executes_before_normal(self, mock_verifier_cls, mock_health, mock_get_entries):
        """CRITICAL 핸들러가 NORMAL 핸들러보다 먼저 실행된다."""
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        mock_get_entries.return_value = []
        mock_health.return_value = MagicMock()

        bus = SelfHealingEventBus()
        bus.reset()

        execution_order = []

        def critical_handler(event):
            execution_order.append("critical")
            on_circuit_breaker_closed_integrity_gate(event)

        def normal_handler(event):
            execution_order.append("normal")

        def low_handler(event):
            execution_order.append("low")

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            low_handler,
            priority=EventPriority.LOW,
        )
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            normal_handler,
            priority=EventPriority.NORMAL,
        )
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            critical_handler,
            priority=EventPriority.CRITICAL,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )
        bus.publish(event)

        assert execution_order[0] == "critical", f"CRITICAL must execute first, got: {execution_order}"
        # NORMAL before LOW
        assert execution_order.index("normal") < execution_order.index("low")

    @patch("selfhealing.services.event_bus.integrity_gate._verify_recovery_window_integrity")
    def test_fail_open_on_gate_exception(self, mock_verify):
        """IntegrityGate 예외 + fail_open=True → 리플레이 허용."""
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )
        from selfhealing.settings.audit_integrity import get_audit_integrity_settings

        mock_verify.side_effect = RuntimeError("Redis down")

        bus = SelfHealingEventBus()
        bus.reset()

        # fail_open 기본값 확인
        settings = get_audit_integrity_settings()
        assert settings.integrity_gate_fail_open is True

        replay_called = []

        def mock_replay_handler(event):
            if not event.data.get(INTEGRITY_FAILED_KEY, False):
                replay_called.append(True)

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            mock_replay_handler,
            priority=EventPriority.NORMAL,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )
        bus.publish(event)

        assert event.data.get(INTEGRITY_FAILED_KEY) is False
        assert len(replay_called) == 1

    @patch("selfhealing.services.event_bus.integrity_gate._get_unsynced_wal_entries")
    @patch("selfhealing.services.event_bus.integrity_gate.get_integrity_health_score")
    @patch("selfhealing.services.event_bus.integrity_gate.HashChainVerifier")
    def test_empty_wal_passes_gate(self, mock_verifier_cls, mock_health, mock_get_entries):
        """WAL 엔트리가 없으면 게이트를 통과한다."""
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        mock_get_entries.return_value = []
        mock_health.return_value = MagicMock()

        bus = SelfHealingEventBus()
        bus.reset()

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )
        bus.publish(event)

        assert event.data.get(INTEGRITY_FAILED_KEY) is False
        gate_result = event.data.get(INTEGRITY_GATE_KEY, {})
        assert gate_result.get("valid") is True
