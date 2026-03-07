"""
Tests for update_incident_fields() — 인시던트 부분 업데이트.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Behavior: In-Memory 캐시 부분 업데이트, dict deep merge, 존재하지 않는 인시던트

참조 소스:
- services/postmortem/store.py (update_incident_fields)
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _setup_inmemory_store():
    """In-Memory 저장소를 격리하고 DB 영속성을 비활성화한다."""
    from selfhealing.services.postmortem.store import (
        clear_healing_incidents,
        set_db_persistence_enabled,
    )

    set_db_persistence_enabled(False)
    clear_healing_incidents()
    yield
    clear_healing_incidents()


class TestUpdateIncidentFieldsBehavior:
    """update_incident_fields() In-Memory 동작 검증."""

    def test_updates_simple_field(self):
        """단순 필드(스칼라)를 업데이트한다."""
        from selfhealing.services.postmortem.store import (
            update_incident_fields,
        )
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            get_healing_incidents,
        )

        add_healing_incident({"incident_id": "INC-UP-001", "status": "open"})

        result = update_incident_fields(
            incident_id="INC-UP-001",
            fields={"status": "closed"},
        )

        assert result is True
        incidents = get_healing_incidents(limit=10)
        updated = next(i for i in incidents if i["incident_id"] == "INC-UP-001")
        assert updated["status"] == "closed"

    def test_deep_merges_dict_fields(self):
        """dict 타입 필드는 deep merge(기존 키 보존)한다."""
        from selfhealing.services.postmortem.store import (
            update_incident_fields,
        )
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            get_healing_incidents,
        )

        add_healing_incident(
            {
                "incident_id": "INC-UP-002",
                "metadata": {"author": "system", "version": 1},
            }
        )

        result = update_incident_fields(
            incident_id="INC-UP-002",
            fields={"metadata": {"correlation": True}},
        )

        assert result is True
        incidents = get_healing_incidents(limit=10)
        updated = next(i for i in incidents if i["incident_id"] == "INC-UP-002")
        # 기존 키 보존 + 새 키 추가
        assert updated["metadata"]["author"] == "system"
        assert updated["metadata"]["correlation"] is True

    def test_adds_new_field(self):
        """기존에 없던 필드를 추가한다."""
        from selfhealing.services.postmortem.store import (
            update_incident_fields,
        )
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            get_healing_incidents,
        )

        add_healing_incident({"incident_id": "INC-UP-003"})

        result = update_incident_fields(
            incident_id="INC-UP-003",
            fields={"root_cause_analysis": {"score": 0.95}},
        )

        assert result is True
        incidents = get_healing_incidents(limit=10)
        updated = next(i for i in incidents if i["incident_id"] == "INC-UP-003")
        assert updated["root_cause_analysis"]["score"] == 0.95

    def test_returns_false_for_nonexistent_incident(self):
        """존재하지 않는 incident_id는 False 반환."""
        from selfhealing.services.postmortem.store import (
            update_incident_fields,
        )

        result = update_incident_fields(
            incident_id="INC-NONEXIST",
            fields={"status": "closed"},
        )

        assert result is False
