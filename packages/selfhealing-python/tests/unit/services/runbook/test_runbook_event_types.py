"""
Unit tests for Runbook EventType entries.

검증 항목:
- RUNBOOK_* 이벤트 8개의 존재 및 문자열 값 계약
- EventType enum 멤버 타입 일관성

테스트 대상: selfhealing.services.event_bus.bus.EventType
"""


class TestRunbookEventTypeContract:
    """RUNBOOK_* EventType 설계 계약값 검증.

    272_RUNBOOK_ARCHITECTURE_OVERVIEW.md §8에 명시된 이벤트 타입 문자열 값을 검증한다.
    """

    def test_runbook_triggered_value(self):
        """RUNBOOK_TRIGGERED의 값은 'runbook_triggered'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_TRIGGERED == "runbook_triggered"

    def test_runbook_step_completed_value(self):
        """RUNBOOK_STEP_COMPLETED의 값은 'runbook_step_completed'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_STEP_COMPLETED == "runbook_step_completed"

    def test_runbook_step_failed_value(self):
        """RUNBOOK_STEP_FAILED의 값은 'runbook_step_failed'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_STEP_FAILED == "runbook_step_failed"

    def test_runbook_completed_value(self):
        """RUNBOOK_COMPLETED의 값은 'runbook_completed'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_COMPLETED == "runbook_completed"

    def test_runbook_failed_value(self):
        """RUNBOOK_FAILED의 값은 'runbook_failed'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_FAILED == "runbook_failed"

    def test_runbook_approval_required_value(self):
        """RUNBOOK_APPROVAL_REQUIRED의 값은 'runbook_approval_required'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_APPROVAL_REQUIRED == "runbook_approval_required"

    def test_runbook_approval_granted_value(self):
        """RUNBOOK_APPROVAL_GRANTED의 값은 'runbook_approval_granted'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_APPROVAL_GRANTED == "runbook_approval_granted"

    def test_runbook_approval_rejected_value(self):
        """RUNBOOK_APPROVAL_REJECTED의 값은 'runbook_approval_rejected'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_APPROVAL_REJECTED == "runbook_approval_rejected"

    def test_runbook_event_count(self):
        """RUNBOOK_* 이벤트는 8개 존재한다."""
        from selfhealing.services.event_bus.bus import EventType

        runbook_events = [e for e in EventType if e.value.startswith("runbook_")]
        assert len(runbook_events) == 8

    def test_all_runbook_events_are_str_enum(self):
        """모든 RUNBOOK_* 이벤트는 str 타입이다."""
        from selfhealing.services.event_bus.bus import EventType

        runbook_events = [e for e in EventType if e.value.startswith("runbook_")]
        for event in runbook_events:
            assert isinstance(event, str)
            assert isinstance(event.value, str)
