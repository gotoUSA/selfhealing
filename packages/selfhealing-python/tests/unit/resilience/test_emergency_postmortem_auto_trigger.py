"""
Emergency Postmortem Auto Trigger Tests (문서 146).

RecoveryCoordinator 복구 완료 시 자동 Postmortem 생성 테스트.

테스트 항목:
1. EMERGENCY_RECOVERY_COMPLETED 이벤트 발행 확인
2. Emergency Postmortem 핸들러 동작 확인
3. 설정 비활성화 시 스킵 확인
4. 최소 duration 미달 시 스킵 확인
5. Emergency Postmortem 데이터 구조 확인
6. approve_recovery() 시 이벤트 발행 확인
"""

import pytest
from datetime import datetime, timezone as tz
from unittest.mock import MagicMock, patch, PropertyMock


class TestEmergencyRecoveryEventPublish:
    """RecoveryCoordinator 이벤트 발행 테스트."""

    def test_complete_session_publishes_event(self):
        """_complete_session() 호출 시 EMERGENCY_RECOVERY_COMPLETED 이벤트 발행 확인."""
        from selfhealing.services.coordination.recovery_coordinator import (
            RecoveryCoordinator,
            reset_recovery_coordinator,
        )
        from selfhealing.services.coordination.recovery_state import (
            RecoverySession,
            RecoveryStep,
            RecoveryStepType,
        )
        from selfhealing.services.coordination.enums import RecoveryStatus
        from selfhealing.services.event_bus import get_event_bus, EventType

        reset_recovery_coordinator()

        # EventBus 리셋
        bus = get_event_bus()
        bus.reset()

        # Mock backend
        mock_backend = MagicMock()
        mock_backend.get.return_value = None

        # Mock recovery lock
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_lock.release.return_value = None

        coordinator = RecoveryCoordinator(
            backend=mock_backend,
            recovery_lock=mock_lock,
        )

        # 테스트용 세션 생성
        now = datetime.now(tz.utc).isoformat()
        session = RecoverySession(
            id="test-recovery-123",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                    wait_after_seconds=0,
                    params={"target_multiplier": 1.0},
                )
            ],
            current_step_index=1,
            started_at=now,
            initiated_by="system",
        )

        # 이벤트 수신 확인용
        received_events = []

        def capture_event(event):
            received_events.append(event)

        bus.subscribe(EventType.EMERGENCY_RECOVERY_COMPLETED, capture_event)

        # _complete_session 호출
        coordinator._complete_session(session)

        # 이벤트 발행 확인
        assert len(received_events) == 1
        event = received_events[0]
        assert event.event_type == EventType.EMERGENCY_RECOVERY_COMPLETED
        assert event.data["session_id"] == "test-recovery-123"
        assert event.data["namespace"] == "global"
        assert event.data["trigger_level"] == "LEVEL_3"
        assert event.data["steps_executed"] == 1
        assert event.source == "recovery_coordinator"

        # 정리
        bus.reset()
        reset_recovery_coordinator()

    def test_approve_recovery_publishes_event(self):
        """approve_recovery() 호출 시 EMERGENCY_RECOVERY_COMPLETED 이벤트 발행 확인."""
        from selfhealing.services.coordination.recovery_coordinator import (
            RecoveryCoordinator,
            reset_recovery_coordinator,
        )
        from selfhealing.services.coordination.recovery_state import (
            RecoverySession,
            RecoveryStep,
            RecoveryStepType,
        )
        from selfhealing.services.coordination.enums import RecoveryStatus
        from selfhealing.services.event_bus import get_event_bus, EventType

        reset_recovery_coordinator()

        bus = get_event_bus()
        bus.reset()

        # Mock backend - READY_TO_RESTORE 상태 세션 반환
        now = datetime.now(tz.utc).isoformat()
        session_data = {
            "id": "test-recovery-456",
            "namespace": "global",
            "trigger_level": "LEVEL_3",
            "status": "ready_to_restore",
            "steps": [
                {
                    "step_type": "budget_reset",
                    "order": 1,
                    "wait_after_seconds": 0,
                    "params": {"target_multiplier": 1.0},
                    "status": "completed",
                }
            ],
            "current_step_index": 1,
            "started_at": now,
            "initiated_by": "system",
            "metadata": {"requires_approval": True},
        }

        mock_backend = MagicMock()
        mock_backend.get.side_effect = lambda key: (
            "test-recovery-456" if "active" in key else session_data if "session" in key else None
        )

        mock_lock = MagicMock()
        mock_lock.release.return_value = None

        coordinator = RecoveryCoordinator(
            backend=mock_backend,
            recovery_lock=mock_lock,
        )

        # 이벤트 수신 확인용
        received_events = []

        def capture_event(event):
            received_events.append(event)

        bus.subscribe(EventType.EMERGENCY_RECOVERY_COMPLETED, capture_event)

        # approve_recovery 호출
        result = coordinator.approve_recovery("global", "admin_user")

        # 이벤트 발행 확인
        assert len(received_events) == 1
        event = received_events[0]
        assert event.event_type == EventType.EMERGENCY_RECOVERY_COMPLETED
        assert event.data["session_id"] == "test-recovery-456"
        assert event.data["approved_by"] == "admin_user"
        assert event.data["requires_approval"] is True

        # 정리
        bus.reset()
        reset_recovery_coordinator()


class TestEmergencyPostmortemHandler:
    """Emergency Postmortem 핸들러 테스트."""

    def setup_method(self):
        """테스트 전 이벤트 버스 및 설정 리셋."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.postmortem import reset_postmortem_settings

        self.bus = get_event_bus()
        self.bus.reset()
        reset_postmortem_settings()

    def teardown_method(self):
        """테스트 후 이벤트 버스 및 설정 리셋."""
        from selfhealing.settings.postmortem import reset_postmortem_settings

        self.bus.reset()
        reset_postmortem_settings()

    def test_handler_skips_when_disabled(self, monkeypatch):
        """auto_enabled=False일 때 핸들러 스킵 확인."""
        from selfhealing.services.event_bus import (
            _on_emergency_recovery_completed_postmortem,
            SelfHealingEvent,
            EventType,
        )
        from selfhealing.settings.postmortem import reset_postmortem_settings

        reset_postmortem_settings()
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "false")

        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_RECOVERY_COMPLETED,
            data={
                "session_id": "test-session-123",
                "namespace": "global",
                "trigger_level": "LEVEL_3",
                "duration_seconds": 300,
            },
            source="recovery_coordinator",
        )

        with patch("selfhealing.services.event_bus.logger") as mock_logger:
            _on_emergency_recovery_completed_postmortem(event)
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("Auto postmortem disabled" in call for call in debug_calls)

    def test_handler_skips_when_duration_below_min(self, monkeypatch):
        """duration이 최소 duration 미만일 때 스킵 확인."""
        from selfhealing.services.event_bus import (
            _on_emergency_recovery_completed_postmortem,
            SelfHealingEvent,
            EventType,
        )
        from selfhealing.settings.postmortem import reset_postmortem_settings

        reset_postmortem_settings()
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "300")

        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_RECOVERY_COMPLETED,
            data={
                "session_id": "test-session-123",
                "namespace": "global",
                "trigger_level": "LEVEL_3",
                "duration_seconds": 60,  # 300초 미만
            },
            source="recovery_coordinator",
        )

        with patch("selfhealing.services.event_bus.logger") as mock_logger:
            _on_emergency_recovery_completed_postmortem(event)
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("skipped" in call.lower() for call in debug_calls)

    def test_handler_generates_postmortem_when_enabled(self, monkeypatch):
        """auto_enabled=True일 때 Postmortem 생성 확인."""
        from selfhealing.services.event_bus import (
            _on_emergency_recovery_completed_postmortem,
            SelfHealingEvent,
            EventType,
        )
        from selfhealing.settings.postmortem import reset_postmortem_settings
        import sys
        from types import ModuleType

        reset_postmortem_settings()
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "0")

        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_RECOVERY_COMPLETED,
            data={
                "session_id": "test-session-456",
                "namespace": "global",
                "trigger_level": "LEVEL_3",
                "started_at": datetime.now(tz.utc).isoformat(),
                "completed_at": datetime.now(tz.utc).isoformat(),
                "duration_seconds": 300,
                "steps_executed": 4,
                "total_steps": 4,
                "requires_approval": False,
                "approved_by": None,
            },
            source="recovery_coordinator",
        )

        # Mock add_healing_incident
        added_incidents = []

        def mock_add_incident(incident):
            added_incidents.append(incident)

        # Create mock module for selfhealing.api.django.views.xtest.base
        mock_base_module = ModuleType("selfhealing.api.django.views.xtest.base")
        mock_base_module.collect_system_snapshot = lambda: {"cpu": 50}
        sys.modules["selfhealing.api.django.views.xtest.base"] = mock_base_module

        try:
            with patch(
                "selfhealing.services.postmortem_store.add_healing_incident",
                mock_add_incident,
            ):
                with patch(
                    "selfhealing.services.audit.base._write_to_wal",
                    return_value=1,
                ):
                    _on_emergency_recovery_completed_postmortem(event)

            # Postmortem 생성 확인
            assert len(added_incidents) == 1
            postmortem = added_incidents[0]
            assert postmortem["recovery_type"] == "emergency"
            assert postmortem["namespace"] == "global"
            assert postmortem["trigger_level"] == "LEVEL_3"
            assert postmortem["recovery_session_id"] == "test-session-456"
            assert "EMERGENCY-global" in postmortem["incident_id"]
        finally:
            # Cleanup mock module
            if "selfhealing.api.django.views.xtest.base" in sys.modules:
                del sys.modules["selfhealing.api.django.views.xtest.base"]


class TestGenerateEmergencyPostmortemData:
    """Emergency Postmortem 데이터 생성 테스트."""

    def test_generate_emergency_postmortem_data_structure(self):
        """Emergency Postmortem 데이터 구조 확인."""
        from selfhealing.services.event_bus import _generate_emergency_postmortem_data

        session_data = {
            "session_id": "recovery-abc123",
            "namespace": "seoul",
            "trigger_level": "LEVEL_3",
            "started_at": "2026-01-28T10:00:00+00:00",
            "completed_at": "2026-01-28T10:10:00+00:00",
            "duration_seconds": 600,
            "steps_executed": 4,
            "total_steps": 4,
            "requires_approval": True,
            "approved_by": "admin",
        }

        event_bus_history = [
            {
                "event_type": "emergency_activated",
                "timestamp": "2026-01-28T10:00:00+00:00",
                "data": {"level": 3},
            },
            {
                "event_type": "emergency_recovery_completed",
                "timestamp": "2026-01-28T10:10:00+00:00",
                "data": {"session_id": "recovery-abc123"},
            },
        ]

        snapshot = {"cpu": 50, "memory": 60}

        result = _generate_emergency_postmortem_data(
            session_data=session_data,
            event_bus_history=event_bus_history,
            snapshot=snapshot,
        )

        # 필수 필드 확인
        assert "incident_id" in result
        assert result["incident_id"].startswith("EMERGENCY-seoul")
        assert result["recovery_type"] == "emergency"
        assert result["namespace"] == "seoul"
        assert result["trigger_level"] == "LEVEL_3"
        assert result["recovery_session_id"] == "recovery-abc123"
        assert result["requires_approval"] is True
        assert result["approved_by"] == "admin"

        # 타임라인 확인
        assert "timeline" in result
        assert len(result["timeline"]) > 0

        # 복구 단계 확인
        assert "recovery_steps" in result
        assert len(result["recovery_steps"]) == 4

        # Action items 확인
        assert "auto_actions" in result
        assert "recommendations" in result

    def test_generate_emergency_postmortem_data_without_approval(self):
        """승인 없이 완료된 Emergency Postmortem 데이터 확인."""
        from selfhealing.services.event_bus import _generate_emergency_postmortem_data

        session_data = {
            "session_id": "recovery-def456",
            "namespace": "global",
            "trigger_level": "LEVEL_2",
            "started_at": "2026-01-28T11:00:00+00:00",
            "completed_at": "2026-01-28T11:05:00+00:00",
            "duration_seconds": 300,
            "steps_executed": 3,
            "total_steps": 3,
            "requires_approval": False,
            "approved_by": None,
        }

        result = _generate_emergency_postmortem_data(
            session_data=session_data,
            event_bus_history=[],
            snapshot={},
        )

        assert result["requires_approval"] is False
        assert result["approved_by"] is None
        assert result["trigger_level"] == "LEVEL_2"

        # 승인 관련 action이 없어야 함
        action_descriptions = [a["action"] for a in result["auto_actions"]]
        assert "MANUAL_APPROVAL" not in action_descriptions

    def test_emergency_postmortem_includes_cb_events(self):
        """Emergency Postmortem 타임라인에 CB 이벤트 포함 확인."""
        from selfhealing.services.event_bus import _generate_emergency_postmortem_data

        session_data = {
            "session_id": "recovery-xyz",
            "namespace": "global",
            "trigger_level": "LEVEL_3",
            "started_at": "2026-01-28T12:00:00+00:00",
            "completed_at": "2026-01-28T12:15:00+00:00",
            "duration_seconds": 900,
            "steps_executed": 4,
            "total_steps": 4,
        }

        event_bus_history = [
            {
                "event_type": "emergency_activated",
                "timestamp": "2026-01-28T12:00:00+00:00",
                "data": {},
            },
            {
                "event_type": "circuit_breaker_opened",
                "timestamp": "2026-01-28T12:01:00+00:00",
                "data": {"service_name": "payment"},
            },
            {
                "event_type": "circuit_breaker_closed",
                "timestamp": "2026-01-28T12:10:00+00:00",
                "data": {"service_name": "payment"},
            },
            {
                "event_type": "emergency_recovery_completed",
                "timestamp": "2026-01-28T12:15:00+00:00",
                "data": {},
            },
        ]

        result = _generate_emergency_postmortem_data(
            session_data=session_data,
            event_bus_history=event_bus_history,
            snapshot={},
        )

        # 타임라인에 Emergency + CB 이벤트 모두 포함
        event_types = [e["event_type"] for e in result["timeline"]]
        assert "emergency_activated" in event_types
        assert "circuit_breaker_opened" in event_types
        assert "circuit_breaker_closed" in event_types
        assert "emergency_recovery_completed" in event_types


class TestEmergencyPostmortemHandlerRegistration:
    """핸들러 등록 확인 테스트."""

    def test_handler_registered_in_default_handlers(self):
        """register_default_handlers()에서 핸들러 등록 확인."""
        from selfhealing.services.event_bus import (
            get_event_bus,
            register_default_handlers,
            EventType,
        )

        bus = get_event_bus()
        bus.reset()

        register_default_handlers()

        subscriptions = bus.get_subscriptions(EventType.EMERGENCY_RECOVERY_COMPLETED)

        # EMERGENCY_RECOVERY_COMPLETED에 핸들러가 등록되어 있어야 함
        assert len(subscriptions) >= 1

        handler_names = [s["handler_name"] for s in subscriptions]
        assert "_on_emergency_recovery_completed_postmortem" in handler_names

        # Priority LOW로 등록되어 있어야 함
        for sub in subscriptions:
            if sub["handler_name"] == "_on_emergency_recovery_completed_postmortem":
                assert sub["priority"] == "LOW"

        bus.reset()
