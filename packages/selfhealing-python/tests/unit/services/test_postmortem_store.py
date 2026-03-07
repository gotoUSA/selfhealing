"""
Post-mortem Store 단위 테스트.

services/postmortem_store.py 모듈 테스트.
"""



class TestPostmortemStoreInMemory:
    """In-Memory 저장소 테스트."""

    def test_add_healing_incident_stores_in_memory(self):
        """인시던트가 In-Memory에 저장되는지 확인."""
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            clear_healing_incidents,
            get_healing_incidents,
            set_db_persistence_enabled,
        )

        # DB 영속성 비활성화 (순수 인메모리 테스트)
        set_db_persistence_enabled(False)
        clear_healing_incidents()

        incident = {
            "incident_id": "TEST-001",
            "affected_services": ["service_a"],
            "duration_seconds": 120,
        }

        add_healing_incident(incident)
        incidents = get_healing_incidents(limit=10)

        assert len(incidents) == 1
        assert incidents[0]["incident_id"] == "TEST-001"
        assert "recorded_at" in incidents[0]

        # cleanup
        clear_healing_incidents()

    def test_get_healing_incidents_respects_limit(self):
        """limit 파라미터가 적용되는지 확인."""
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            clear_healing_incidents,
            get_healing_incidents,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(False)
        clear_healing_incidents()

        for i in range(5):
            add_healing_incident({"incident_id": f"TEST-{i:03d}"})

        incidents = get_healing_incidents(limit=3)
        assert len(incidents) == 3

        # cleanup
        clear_healing_incidents()

    def test_get_healing_incidents_count(self):
        """인시던트 개수 조회 테스트."""
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            clear_healing_incidents,
            get_healing_incidents_count,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(False)
        clear_healing_incidents()

        for i in range(3):
            add_healing_incident({"incident_id": f"TEST-{i:03d}"})

        count = get_healing_incidents_count()
        assert count == 3

        # cleanup
        clear_healing_incidents()

    def test_clear_healing_incidents(self):
        """인시던트 초기화 테스트."""
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            clear_healing_incidents,
            get_healing_incidents_count,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(False)
        clear_healing_incidents()

        add_healing_incident({"incident_id": "TEST-001"})
        assert get_healing_incidents_count() == 1

        count = clear_healing_incidents()
        assert count == 1
        assert get_healing_incidents_count() == 0


class TestPostmortemStoreDbPersistence:
    """DB 영속성 설정 테스트."""

    def test_db_persistence_toggle(self):
        """DB 영속성 활성화/비활성화 토글 테스트."""
        from selfhealing.services.postmortem.store import (
            get_db_persistence_enabled,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(True)
        assert get_db_persistence_enabled() is True

        set_db_persistence_enabled(False)
        assert get_db_persistence_enabled() is False

    def test_add_incident_skips_db_when_disabled(self):
        """DB 영속성 비활성화 시 DB 저장을 건너뛰는지 확인."""
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            clear_healing_incidents,
            get_healing_incidents_count,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(False)
        clear_healing_incidents()

        # DB 저장 시도하지 않고 인메모리에만 저장됨
        add_healing_incident({"incident_id": "TEST-NO-DB"})

        assert get_healing_incidents_count() == 1

        # cleanup
        clear_healing_incidents()


class TestPostmortemStoreImport:
    """Import 테스트."""

    def test_import_from_postmortem_store(self):
        """postmortem_store.py에서 직접 import 가능한지 확인."""
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            clear_healing_incidents,
            get_healing_incidents,
            get_healing_incidents_count,
            get_incident_by_id,
        )

        assert callable(add_healing_incident)
        assert callable(get_healing_incidents)
        assert callable(get_healing_incidents_count)
        assert callable(get_incident_by_id)
        assert callable(clear_healing_incidents)


class TestGetIncidentById:
    """get_incident_by_id() 함수 테스트."""

    def test_get_incident_by_id_found(self):
        """ID로 인시던트 조회 성공 테스트."""
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            clear_healing_incidents,
            get_incident_by_id,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(False)
        clear_healing_incidents()

        incident = {
            "incident_id": "FIND-ME-001",
            "affected_services": ["test_service"],
            "duration_seconds": 60,
        }
        add_healing_incident(incident)

        result = get_incident_by_id("FIND-ME-001")

        assert result is not None
        assert result["incident_id"] == "FIND-ME-001"
        assert result["affected_services"] == ["test_service"]

        # cleanup
        clear_healing_incidents()

    def test_get_incident_by_id_not_found(self):
        """ID로 인시던트 조회 실패 테스트 (없는 ID)."""
        from selfhealing.services.postmortem.store import (
            clear_healing_incidents,
            get_incident_by_id,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(False)
        clear_healing_incidents()

        result = get_incident_by_id("NON-EXISTENT-ID")

        assert result is None

    def test_get_incident_by_id_returns_dict(self):
        """반환된 인시던트가 dict인지 확인."""
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            clear_healing_incidents,
            get_incident_by_id,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(False)
        clear_healing_incidents()

        incident = {
            "incident_id": "DICT-TEST-001",
            "data": {"key": "value"},
        }
        add_healing_incident(incident)

        result = get_incident_by_id("DICT-TEST-001")
        assert isinstance(result, dict)
        assert result["incident_id"] == "DICT-TEST-001"

        # cleanup
        clear_healing_incidents()

    def test_get_incident_by_id_multiple_incidents(self):
        """여러 인시던트 중 정확한 ID 조회 테스트."""
        from selfhealing.services.postmortem.store import (
            add_healing_incident,
            clear_healing_incidents,
            get_incident_by_id,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(False)
        clear_healing_incidents()

        for i in range(5):
            add_healing_incident(
                {
                    "incident_id": f"MULTI-{i:03d}",
                    "index": i,
                }
            )

        result = get_incident_by_id("MULTI-003")

        assert result is not None
        assert result["incident_id"] == "MULTI-003"
        assert result["index"] == 3

        # cleanup
        clear_healing_incidents()
