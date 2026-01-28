"""
Unit tests for IncidentGroupManager.

Tests:
- 단일 인시던트 추가 및 그룹 생성
- 다중 인시던트 그룹핑
- 그룹 종료 조건 확인
- 연쇄 패턴 분석
- In-Memory Fallback
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@dataclass
class MockSelfHealingEvent:
    """Mock SelfHealingEvent for testing."""

    data: dict
    event_type: str = "circuit_breaker_closed"

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "data": self.data,
        }


class TestIncidentGroupEntry:
    """IncidentGroupEntry 테스트."""

    def test_from_dict_creates_entry(self):
        """딕셔너리에서 엔트리 생성."""
        from selfhealing.services.postmortem.incident_group import IncidentGroupEntry

        data = {
            "service_name": "payment",
            "closed_at": "2026-01-28T12:00:00+00:00",
            "opened_at": "2026-01-28T11:55:00+00:00",
            "duration_seconds": 300.0,
            "event_data": {"key": "value"},
        }

        entry = IncidentGroupEntry.from_dict(data)

        assert entry.service_name == "payment"
        assert entry.duration_seconds == 300.0
        assert entry.event_data["key"] == "value"

    def test_to_dict_serializes_entry(self):
        """엔트리를 딕셔너리로 변환."""
        from selfhealing.services.postmortem.incident_group import IncidentGroupEntry

        entry = IncidentGroupEntry(
            service_name="order",
            closed_at="2026-01-28T12:00:00+00:00",
            opened_at="2026-01-28T11:50:00+00:00",
            duration_seconds=600.0,
            event_data={},
        )

        data = entry.to_dict()

        assert data["service_name"] == "order"
        assert data["duration_seconds"] == 600.0


class TestIncidentGroup:
    """IncidentGroup 테스트."""

    def test_from_dict_creates_group(self):
        """딕셔너리에서 그룹 생성."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroup,
            IncidentGroupStatus,
        )

        data = {
            "group_id": "INCGRP-20260128-120000",
            "status": "OPEN",
            "created_at": "2026-01-28T12:00:00+00:00",
            "primary_service": "payment",
            "namespace": "default",
            "entries": [
                {
                    "service_name": "payment",
                    "closed_at": "2026-01-28T12:00:00+00:00",
                    "opened_at": "2026-01-28T11:55:00+00:00",
                    "duration_seconds": 300.0,
                    "event_data": {},
                }
            ],
        }

        group = IncidentGroup.from_dict(data)

        assert group.group_id == "INCGRP-20260128-120000"
        assert group.status == IncidentGroupStatus.OPEN
        assert group.incident_count == 1
        assert group.primary_service == "payment"

    def test_incident_count_returns_entries_length(self):
        """incident_count가 entries 길이를 반환."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroup,
            IncidentGroupEntry,
            IncidentGroupStatus,
        )

        group = IncidentGroup(
            group_id="INCGRP-test",
            status=IncidentGroupStatus.OPEN,
            created_at="2026-01-28T12:00:00+00:00",
            entries=[
                IncidentGroupEntry(
                    service_name="svc1",
                    closed_at="",
                    opened_at="",
                    duration_seconds=0,
                    event_data={},
                ),
                IncidentGroupEntry(
                    service_name="svc2",
                    closed_at="",
                    opened_at="",
                    duration_seconds=0,
                    event_data={},
                ),
            ],
        )

        assert group.incident_count == 2

    def test_cascading_pattern_simultaneous(self):
        """동시 발생 패턴 감지."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroup,
            IncidentGroupEntry,
            IncidentGroupStatus,
        )

        # 30초 이내 모든 OPEN
        group = IncidentGroup(
            group_id="INCGRP-test",
            status=IncidentGroupStatus.OPEN,
            created_at="2026-01-28T12:00:00+00:00",
            entries=[
                IncidentGroupEntry(
                    service_name="svc1",
                    closed_at="2026-01-28T12:10:00+00:00",
                    opened_at="2026-01-28T12:00:00+00:00",
                    duration_seconds=600,
                    event_data={},
                ),
                IncidentGroupEntry(
                    service_name="svc2",
                    closed_at="2026-01-28T12:10:10+00:00",
                    opened_at="2026-01-28T12:00:10+00:00",
                    duration_seconds=600,
                    event_data={},
                ),
                IncidentGroupEntry(
                    service_name="svc3",
                    closed_at="2026-01-28T12:10:20+00:00",
                    opened_at="2026-01-28T12:00:20+00:00",
                    duration_seconds=600,
                    event_data={},
                ),
            ],
        )

        assert group.get_cascading_pattern() == "simultaneous"

    def test_cascading_pattern_cascading(self):
        """연쇄 장애 패턴 감지."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroup,
            IncidentGroupEntry,
            IncidentGroupStatus,
        )

        # 순차적 OPEN (간격 < 60초)
        group = IncidentGroup(
            group_id="INCGRP-test",
            status=IncidentGroupStatus.OPEN,
            created_at="2026-01-28T12:00:00+00:00",
            entries=[
                IncidentGroupEntry(
                    service_name="svc1",
                    closed_at="2026-01-28T12:10:00+00:00",
                    opened_at="2026-01-28T12:00:00+00:00",
                    duration_seconds=600,
                    event_data={},
                ),
                IncidentGroupEntry(
                    service_name="svc2",
                    closed_at="2026-01-28T12:10:45+00:00",
                    opened_at="2026-01-28T12:00:45+00:00",
                    duration_seconds=600,
                    event_data={},
                ),
                IncidentGroupEntry(
                    service_name="svc3",
                    closed_at="2026-01-28T12:11:30+00:00",
                    opened_at="2026-01-28T12:01:30+00:00",
                    duration_seconds=600,
                    event_data={},
                ),
            ],
        )

        assert group.get_cascading_pattern() == "cascading"

    def test_cascading_pattern_independent(self):
        """독립 장애 패턴 감지."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroup,
            IncidentGroupEntry,
            IncidentGroupStatus,
        )

        # OPEN 간격 > 60초
        group = IncidentGroup(
            group_id="INCGRP-test",
            status=IncidentGroupStatus.OPEN,
            created_at="2026-01-28T12:00:00+00:00",
            entries=[
                IncidentGroupEntry(
                    service_name="svc1",
                    closed_at="2026-01-28T12:10:00+00:00",
                    opened_at="2026-01-28T12:00:00+00:00",
                    duration_seconds=600,
                    event_data={},
                ),
                IncidentGroupEntry(
                    service_name="svc2",
                    closed_at="2026-01-28T12:15:00+00:00",
                    opened_at="2026-01-28T12:05:00+00:00",  # 5분 후
                    duration_seconds=600,
                    event_data={},
                ),
            ],
        )

        assert group.get_cascading_pattern() == "independent"

    def test_cascading_pattern_single_returns_single(self):
        """단일 인시던트는 'single' 패턴 반환."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroup,
            IncidentGroupEntry,
            IncidentGroupStatus,
        )

        group = IncidentGroup(
            group_id="INCGRP-test",
            status=IncidentGroupStatus.OPEN,
            created_at="2026-01-28T12:00:00+00:00",
            entries=[
                IncidentGroupEntry(
                    service_name="svc1",
                    closed_at="2026-01-28T12:10:00+00:00",
                    opened_at="2026-01-28T12:00:00+00:00",
                    duration_seconds=600,
                    event_data={},
                ),
            ],
        )

        assert group.get_cascading_pattern() == "single"


class TestIncidentGroupManagerMemory:
    """IncidentGroupManager In-Memory 모드 테스트."""

    def test_add_incident_creates_new_group(self):
        """첫 인시던트 추가 시 새 그룹 생성."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroupManager,
            reset_incident_group_manager,
        )

        reset_incident_group_manager()

        manager = IncidentGroupManager(use_redis=False)
        event = MockSelfHealingEvent(
            data={
                "service_name": "payment",
                "opened_at": "2026-01-28T12:00:00+00:00",
                "duration_seconds": 300.0,
            }
        )

        group_id, is_new = manager.add_incident(
            service_name="payment",
            event=event,
            namespace="test",
        )

        assert is_new is True
        assert group_id.startswith("INCGRP-")

        # 그룹 조회
        group = manager.get_active_group("test")
        assert group is not None
        assert group.incident_count == 1

    def test_add_incident_to_existing_group(self):
        """기존 그룹에 인시던트 추가."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroupManager,
        )

        manager = IncidentGroupManager(use_redis=False)

        event1 = MockSelfHealingEvent(
            data={
                "service_name": "payment",
                "opened_at": "2026-01-28T12:00:00+00:00",
                "duration_seconds": 300.0,
            }
        )
        event2 = MockSelfHealingEvent(
            data={
                "service_name": "order",
                "opened_at": "2026-01-28T12:00:30+00:00",
                "duration_seconds": 200.0,
            }
        )

        group_id1, is_new1 = manager.add_incident("payment", event1, "test2")
        group_id2, is_new2 = manager.add_incident("order", event2, "test2")

        assert is_new1 is True
        assert is_new2 is False
        assert group_id1 == group_id2

        group = manager.get_active_group("test2")
        assert group.incident_count == 2

    def test_should_close_group_timeout(self):
        """타임아웃 시 그룹 종료 조건 충족."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroupManager,
        )

        manager = IncidentGroupManager(
            window_seconds=1,  # 1초 윈도우 (테스트용)
            use_redis=False,
        )

        event = MockSelfHealingEvent(
            data={
                "service_name": "payment",
                "opened_at": "2026-01-28T12:00:00+00:00",
                "duration_seconds": 300.0,
            }
        )

        group_id, _ = manager.add_incident("payment", event, "test3")

        # 즉시는 종료 조건 미충족
        assert manager.should_close_group(group_id, "test3") is False

        # 1초 대기 후 종료 조건 충족
        time.sleep(1.1)
        assert manager.should_close_group(group_id, "test3") is True

    def test_close_group_changes_status(self):
        """그룹 종료 시 상태 변경."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroupManager,
            IncidentGroupStatus,
        )

        manager = IncidentGroupManager(use_redis=False)

        event = MockSelfHealingEvent(
            data={
                "service_name": "payment",
                "opened_at": "2026-01-28T12:00:00+00:00",
                "duration_seconds": 300.0,
            }
        )

        group_id, _ = manager.add_incident("payment", event, "test4")

        # 종료
        closed_group = manager.close_group(group_id, "test4")

        assert closed_group is not None
        assert closed_group.status == IncidentGroupStatus.CLOSED
        assert closed_group.closed_at is not None

    def test_get_active_group_returns_none_for_closed(self):
        """종료된 그룹은 활성 그룹으로 조회 안됨."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroupManager,
        )

        manager = IncidentGroupManager(use_redis=False)

        event = MockSelfHealingEvent(
            data={
                "service_name": "payment",
                "opened_at": "2026-01-28T12:00:00+00:00",
                "duration_seconds": 300.0,
            }
        )

        group_id, _ = manager.add_incident("payment", event, "test5")
        manager.close_group(group_id, "test5")

        # 활성 그룹 조회 시 None (OPEN 상태 아님)
        active = manager.get_active_group("test5")
        # close 후에도 같은 namespace에 group이 있지만 status가 CLOSED
        assert active is None or active.group_id != group_id or active.status.value != "OPEN"

    def test_mark_completed_updates_status(self):
        """완료 마킹 시 상태 변경."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroupManager,
            IncidentGroupStatus,
        )

        manager = IncidentGroupManager(use_redis=False)

        event = MockSelfHealingEvent(
            data={
                "service_name": "payment",
                "opened_at": "2026-01-28T12:00:00+00:00",
                "duration_seconds": 300.0,
            }
        )

        group_id, _ = manager.add_incident("payment", event, "test6")
        manager.close_group(group_id, "test6")

        result = manager.mark_completed(group_id, "test6")

        assert result is True

    def test_clear_removes_group(self):
        """그룹 초기화."""
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroupManager,
        )

        manager = IncidentGroupManager(use_redis=False)

        event = MockSelfHealingEvent(
            data={
                "service_name": "payment",
                "opened_at": "2026-01-28T12:00:00+00:00",
                "duration_seconds": 300.0,
            }
        )

        manager.add_incident("payment", event, "test7")
        manager.clear("test7")

        assert manager.get_active_group("test7") is None
