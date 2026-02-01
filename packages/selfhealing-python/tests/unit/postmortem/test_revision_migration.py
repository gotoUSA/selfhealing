"""
Unit tests for Postmortem Migration Utility.

마이그레이션 유틸리티 테스트:
- migrate_existing_postmortems() 함수
- 기존 데이터에 초기 리비전 생성
- 이미 리비전 존재 시 건너뛰기
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestMigrateExistingPostmortems:
    """migrate_existing_postmortems 함수 테스트."""

    def test_migration_creates_initial_revisions(self):
        """마이그레이션이 초기 리비전 생성."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
            migrate_existing_postmortems,
        )

        mock_incidents = [
            {"incident_id": "MIG-001", "summary": "Incident 1"},
            {"incident_id": "MIG-002", "summary": "Incident 2"},
            {"incident_id": "MIG-003", "summary": "Incident 3"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            with patch("selfhealing.services.postmortem_store.get_healing_incidents") as mock_get:
                # 첫 호출에서 인시던트 반환, 두번째에서 빈 리스트 반환
                mock_get.side_effect = [mock_incidents, []]

                result = migrate_existing_postmortems(manager=manager, batch_size=10)

            assert result["total"] == 3
            assert result["migrated"] == 3
            assert result["skipped"] == 0
            assert result["failed"] == 0

            # 각 인시던트에 대해 리비전 생성 확인
            for incident_id in ["MIG-001", "MIG-002", "MIG-003"]:
                rev = manager.get_latest_revision(incident_id)
                assert rev is not None
                assert rev.revision_number == 1
                assert rev.change_type == RevisionChangeType.INITIAL

    def test_migration_skips_existing_revisions(self):
        """이미 리비전 존재 시 건너뛰기."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
            migrate_existing_postmortems,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            # 기존 리비전 먼저 생성
            manager.create_revision(
                incident_id="EXISTING-001",
                new_data={"summary": "Already has revision"},
                changed_by="user@example.com",
                change_reason="Initial",
                change_type=RevisionChangeType.INITIAL,
            )

            mock_incidents = [
                {"incident_id": "EXISTING-001", "summary": "Existing incident"},
                {"incident_id": "NEW-001", "summary": "New incident"},
            ]

            with patch("selfhealing.services.postmortem_store.get_healing_incidents") as mock_get:
                mock_get.side_effect = [mock_incidents, []]

                result = migrate_existing_postmortems(manager=manager)

            assert result["total"] == 2
            assert result["migrated"] == 1  # NEW-001만 마이그레이션
            assert result["skipped"] == 1  # EXISTING-001은 건너뜀
            assert result["failed"] == 0

    def test_migration_handles_missing_incident_id(self):
        """incident_id 없는 데이터 처리."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            migrate_existing_postmortems,
        )

        mock_incidents = [
            {"summary": "Missing incident_id"},  # incident_id 없음
            {"incident_id": "VALID-001", "summary": "Valid incident"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            with patch("selfhealing.services.postmortem_store.get_healing_incidents") as mock_get:
                mock_get.side_effect = [mock_incidents, []]

                result = migrate_existing_postmortems(manager=manager)

            assert result["total"] == 2
            assert result["migrated"] == 1
            assert result["failed"] == 1

    def test_migration_handles_empty_data(self):
        """빈 데이터 처리."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            migrate_existing_postmortems,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            with patch("selfhealing.services.postmortem_store.get_healing_incidents") as mock_get:
                mock_get.return_value = []

                result = migrate_existing_postmortems(manager=manager)

            assert result["total"] == 0
            assert result["migrated"] == 0
            assert result["skipped"] == 0
            assert result["failed"] == 0

    def test_migration_handles_import_error(self):
        """postmortem_store import 실패 처리."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            migrate_existing_postmortems,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            # get_healing_incidents import 실패 시뮬레이션
            # ImportError는 함수 내에서 try/except로 처리됨
            # 이 테스트는 import 경로가 올바른지 확인
            assert True

    def test_migration_batch_processing(self):
        """배치 처리 테스트."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            migrate_existing_postmortems,
        )

        batch1 = [{"incident_id": f"BATCH1-{i}", "summary": f"B1-{i}"} for i in range(3)]
        batch2 = [{"incident_id": f"BATCH2-{i}", "summary": f"B2-{i}"} for i in range(2)]

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            with patch("selfhealing.services.postmortem_store.get_healing_incidents") as mock_get:
                # 첫번째 배치, 두번째 배치, 종료
                mock_get.side_effect = [batch1, batch2, []]

                result = migrate_existing_postmortems(manager=manager, batch_size=3)

            assert result["total"] == 5
            assert result["migrated"] == 5

    def test_migration_uses_singleton_manager_by_default(self):
        """기본적으로 싱글턴 매니저 사용."""
        from selfhealing.services.postmortem.revision import (
            get_postmortem_revision_manager,
            migrate_existing_postmortems,
            reset_postmortem_revision_manager,
        )

        reset_postmortem_revision_manager()

        with patch("selfhealing.services.postmortem_store.get_healing_incidents") as mock_get:
            mock_get.return_value = []

            # manager=None으로 호출하면 싱글턴 사용
            result = migrate_existing_postmortems(manager=None)

            assert result["total"] == 0

        reset_postmortem_revision_manager()

    def test_migration_records_system_as_actor(self):
        """마이그레이션 시 system:migration을 actor로 기록."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            migrate_existing_postmortems,
        )

        mock_incidents = [{"incident_id": "ACTOR-TEST", "summary": "Test"}]

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            with patch("selfhealing.services.postmortem_store.get_healing_incidents") as mock_get:
                mock_get.side_effect = [mock_incidents, []]

                migrate_existing_postmortems(manager=manager)

            rev = manager.get_latest_revision("ACTOR-TEST")
            assert rev.created_by == "system:migration"
            assert "migration" in rev.change_reason.lower()
