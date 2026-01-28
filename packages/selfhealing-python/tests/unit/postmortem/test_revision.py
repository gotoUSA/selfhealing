"""
Unit tests for PostmortemRevisionManager.

Tests:
- 리비전 생성 테스트
- diff 계산 테스트
- 봉인 테스트
- 롤백 테스트
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


class TestRevisionDiff:
    """compute_diff 함수 테스트."""

    def test_compute_diff_added_fields(self):
        """새로 추가된 필드 감지."""
        from selfhealing.services.postmortem.revision import compute_diff

        old_data = {"field1": "value1"}
        new_data = {"field1": "value1", "field2": "value2"}

        diff = compute_diff(old_data, new_data)

        assert diff.added == {"field2": "value2"}
        assert diff.removed == {}
        assert diff.modified == {}
        assert "field1" in diff.unchanged

    def test_compute_diff_removed_fields(self):
        """제거된 필드 감지."""
        from selfhealing.services.postmortem.revision import compute_diff

        old_data = {"field1": "value1", "field2": "value2"}
        new_data = {"field1": "value1"}

        diff = compute_diff(old_data, new_data)

        assert diff.added == {}
        assert diff.removed == {"field2": "value2"}
        assert diff.modified == {}
        assert "field1" in diff.unchanged

    def test_compute_diff_modified_fields(self):
        """수정된 필드 감지."""
        from selfhealing.services.postmortem.revision import compute_diff

        old_data = {"field1": "old_value", "field2": "value2"}
        new_data = {"field1": "new_value", "field2": "value2"}

        diff = compute_diff(old_data, new_data)

        assert diff.added == {}
        assert diff.removed == {}
        assert diff.modified == {"field1": {"old": "old_value", "new": "new_value"}}
        assert "field2" in diff.unchanged

    def test_compute_diff_no_changes(self):
        """변경 없음."""
        from selfhealing.services.postmortem.revision import compute_diff

        data = {"field1": "value1", "field2": "value2"}

        diff = compute_diff(data, data.copy())

        assert diff.added == {}
        assert diff.removed == {}
        assert diff.modified == {}
        assert "field1" in diff.unchanged
        assert "field2" in diff.unchanged
        assert not diff.has_changes

    def test_compute_diff_complex_changes(self):
        """복합 변경 감지."""
        from selfhealing.services.postmortem.revision import compute_diff

        old_data = {"a": 1, "b": 2, "c": 3}
        new_data = {"a": 1, "b": 20, "d": 4}

        diff = compute_diff(old_data, new_data)

        assert diff.added == {"d": 4}
        assert diff.removed == {"c": 3}
        assert diff.modified == {"b": {"old": 2, "new": 20}}
        assert "a" in diff.unchanged
        assert diff.has_changes


class TestRevisionChangeType:
    """RevisionChangeType enum 테스트."""

    def test_revision_change_types(self):
        """모든 변경 유형 확인."""
        from selfhealing.services.postmortem.revision import RevisionChangeType

        assert RevisionChangeType.INITIAL.value == "initial"
        assert RevisionChangeType.ANALYSIS_UPDATE.value == "analysis_update"
        assert RevisionChangeType.TIMELINE_CORRECTION.value == "timeline_correction"
        assert RevisionChangeType.IMPROVEMENT_ADDED.value == "improvement_added"
        assert RevisionChangeType.ANNOTATION.value == "annotation"
        assert RevisionChangeType.CORRECTION.value == "correction"
        assert RevisionChangeType.SEALED.value == "sealed"
        assert RevisionChangeType.ROLLBACK.value == "rollback"


class TestPostmortemRevision:
    """PostmortemRevision 데이터클래스 테스트."""

    def test_revision_to_dict(self):
        """딕셔너리 변환."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevision,
            RevisionChangeType,
        )

        revision = PostmortemRevision(
            revision_id="REV-abc123",
            incident_id="AUTO-payment-20260128",
            revision_number=1,
            created_at="2026-01-28T12:00:00+00:00",
            created_by="operator@example.com",
            change_reason="Initial creation",
            change_type=RevisionChangeType.INITIAL,
            data_snapshot={"summary": "test"},
            diff_from_previous={},
            integrity_hash="sha256:abc",
        )

        result = revision.to_dict()

        assert result["revision_id"] == "REV-abc123"
        assert result["incident_id"] == "AUTO-payment-20260128"
        assert result["revision_number"] == 1
        assert result["change_type"] == "initial"
        assert result["data_snapshot"] == {"summary": "test"}

    def test_revision_from_dict(self):
        """딕셔너리에서 생성."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevision,
            RevisionChangeType,
        )

        data = {
            "revision_id": "REV-def456",
            "incident_id": "AUTO-order-20260128",
            "revision_number": 2,
            "created_at": "2026-01-28T14:00:00+00:00",
            "created_by": "admin@example.com",
            "change_reason": "Updated analysis",
            "change_type": "analysis_update",
            "data_snapshot": {"root_cause": "DB timeout"},
            "diff_from_previous": {"modified": {"root_cause": {"old": "", "new": "DB timeout"}}},
            "integrity_hash": "sha256:def",
        }

        revision = PostmortemRevision.from_dict(data)

        assert revision.revision_id == "REV-def456"
        assert revision.revision_number == 2
        assert revision.change_type == RevisionChangeType.ANALYSIS_UPDATE
        assert revision.data_snapshot["root_cause"] == "DB timeout"


class TestPostmortemRevisionManager:
    """PostmortemRevisionManager 테스트."""

    def test_create_initial_revision(self):
        """초기 리비전 생성."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            revision = manager.create_revision(
                incident_id="AUTO-payment-20260128",
                new_data={"summary": "Payment service outage"},
                changed_by="operator@example.com",
                change_reason="Initial postmortem creation",
                change_type=RevisionChangeType.INITIAL,
            )

            assert revision.revision_number == 1
            assert revision.incident_id == "AUTO-payment-20260128"
            assert revision.change_type == RevisionChangeType.INITIAL
            assert revision.data_snapshot["summary"] == "Payment service outage"
            assert revision.revision_id.startswith("REV-")

    def test_create_multiple_revisions(self):
        """여러 리비전 생성."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-order-20260128"

            # 첫 번째 리비전
            rev1 = manager.create_revision(
                incident_id=incident_id,
                new_data={"summary": "Initial summary"},
                changed_by="user1@example.com",
                change_reason="Initial creation",
                change_type=RevisionChangeType.INITIAL,
            )

            # 두 번째 리비전
            rev2 = manager.create_revision(
                incident_id=incident_id,
                new_data={"summary": "Updated summary", "root_cause": "DB failure"},
                changed_by="user2@example.com",
                change_reason="Added root cause analysis",
                change_type=RevisionChangeType.ANALYSIS_UPDATE,
            )

            assert rev1.revision_number == 1
            assert rev2.revision_number == 2
            assert rev2.diff_from_previous["added"]["root_cause"] == "DB failure"
            assert rev2.diff_from_previous["modified"]["summary"]["old"] == "Initial summary"
            assert rev2.diff_from_previous["modified"]["summary"]["new"] == "Updated summary"

    def test_get_revision(self):
        """특정 리비전 조회."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-test-20260128"

            manager.create_revision(
                incident_id=incident_id,
                new_data={"v": 1},
                changed_by="user@example.com",
                change_reason="v1",
                change_type=RevisionChangeType.INITIAL,
            )
            manager.create_revision(
                incident_id=incident_id,
                new_data={"v": 2},
                changed_by="user@example.com",
                change_reason="v2",
                change_type=RevisionChangeType.CORRECTION,
            )

            rev1 = manager.get_revision(incident_id, 1)
            rev2 = manager.get_revision(incident_id, 2)
            rev3 = manager.get_revision(incident_id, 3)

            assert rev1 is not None
            assert rev1.data_snapshot["v"] == 1
            assert rev2 is not None
            assert rev2.data_snapshot["v"] == 2
            assert rev3 is None

    def test_get_all_revisions(self):
        """모든 리비전 목록 조회."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-list-test"

            for i in range(1, 4):
                manager.create_revision(
                    incident_id=incident_id,
                    new_data={"version": i},
                    changed_by="user@example.com",
                    change_reason=f"Version {i}",
                    change_type=RevisionChangeType.CORRECTION,
                )

            all_revisions = manager.get_all_revisions(incident_id)

            assert len(all_revisions) == 3
            assert all_revisions[0].revision_number == 1
            assert all_revisions[1].revision_number == 2
            assert all_revisions[2].revision_number == 3

    def test_get_latest_revision(self):
        """최신 리비전 조회."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-latest-test"

            manager.create_revision(
                incident_id=incident_id,
                new_data={"status": "draft"},
                changed_by="user@example.com",
                change_reason="Draft",
                change_type=RevisionChangeType.INITIAL,
            )
            manager.create_revision(
                incident_id=incident_id,
                new_data={"status": "review"},
                changed_by="user@example.com",
                change_reason="In review",
                change_type=RevisionChangeType.CORRECTION,
            )
            manager.create_revision(
                incident_id=incident_id,
                new_data={"status": "final"},
                changed_by="user@example.com",
                change_reason="Finalized",
                change_type=RevisionChangeType.CORRECTION,
            )

            latest = manager.get_latest_revision(incident_id)

            assert latest is not None
            assert latest.revision_number == 3
            assert latest.data_snapshot["status"] == "final"

    def test_compare_revisions(self):
        """두 리비전 비교."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-compare-test"

            manager.create_revision(
                incident_id=incident_id,
                new_data={"a": 1, "b": 2},
                changed_by="user@example.com",
                change_reason="v1",
                change_type=RevisionChangeType.INITIAL,
            )
            manager.create_revision(
                incident_id=incident_id,
                new_data={"a": 1, "b": 20, "c": 3},
                changed_by="user@example.com",
                change_reason="v2",
                change_type=RevisionChangeType.CORRECTION,
            )

            diff = manager.compare_revisions(incident_id, 1, 2)

            assert diff.added == {"c": 3}
            assert diff.modified == {"b": {"old": 2, "new": 20}}
            assert "a" in diff.unchanged


class TestPostmortemSealing:
    """Postmortem 봉인 기능 테스트."""

    def test_seal_postmortem(self):
        """Postmortem 봉인."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-seal-test"

            manager.create_revision(
                incident_id=incident_id,
                new_data={"analysis": "Complete"},
                changed_by="operator@example.com",
                change_reason="Initial",
                change_type=RevisionChangeType.INITIAL,
            )

            assert not manager.is_sealed(incident_id)

            seal_revision = manager.seal_postmortem(
                incident_id=incident_id,
                sealed_by="admin@example.com",
                seal_reason="Analysis completed and approved",
            )

            assert manager.is_sealed(incident_id)
            assert seal_revision.change_type == RevisionChangeType.SEALED
            assert seal_revision.revision_number == 2

    def test_sealed_postmortem_cannot_be_modified(self):
        """봉인된 Postmortem 수정 불가."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-sealed-no-edit"

            manager.create_revision(
                incident_id=incident_id,
                new_data={"final": True},
                changed_by="user@example.com",
                change_reason="Initial",
                change_type=RevisionChangeType.INITIAL,
            )

            manager.seal_postmortem(
                incident_id=incident_id,
                sealed_by="admin@example.com",
                seal_reason="Finalized",
            )

            with pytest.raises(ValueError) as exc_info:
                manager.create_revision(
                    incident_id=incident_id,
                    new_data={"final": False},
                    changed_by="user@example.com",
                    change_reason="Try to modify",
                    change_type=RevisionChangeType.CORRECTION,
                )

            assert "sealed" in str(exc_info.value).lower()

    def test_unseal_postmortem(self):
        """Postmortem 봉인 해제."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-unseal-test"

            manager.create_revision(
                incident_id=incident_id,
                new_data={"data": "sealed"},
                changed_by="user@example.com",
                change_reason="Initial",
                change_type=RevisionChangeType.INITIAL,
            )

            manager.seal_postmortem(
                incident_id=incident_id,
                sealed_by="admin@example.com",
                seal_reason="Sealed",
            )

            assert manager.is_sealed(incident_id)

            result = manager.unseal_postmortem(
                incident_id=incident_id,
                unsealed_by="superadmin@example.com",
                unseal_reason="Need to add more details",
                approval_chain=["manager@example.com", "director@example.com"],
            )

            assert result is True
            assert not manager.is_sealed(incident_id)

            # 봉인 해제 후 수정 가능
            revision = manager.create_revision(
                incident_id=incident_id,
                new_data={"data": "updated after unseal"},
                changed_by="user@example.com",
                change_reason="Update after unseal",
                change_type=RevisionChangeType.CORRECTION,
            )
            assert revision.revision_number == 3


class TestPostmortemRollback:
    """Postmortem 롤백 기능 테스트."""

    def test_rollback_to_previous_revision(self):
        """이전 리비전으로 롤백."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-rollback-test"

            # 버전 1
            manager.create_revision(
                incident_id=incident_id,
                new_data={"version": 1, "correct_data": True},
                changed_by="user@example.com",
                change_reason="Correct version",
                change_type=RevisionChangeType.INITIAL,
            )

            # 버전 2 - 잘못된 수정
            manager.create_revision(
                incident_id=incident_id,
                new_data={"version": 2, "correct_data": False},
                changed_by="user@example.com",
                change_reason="Wrong edit",
                change_type=RevisionChangeType.CORRECTION,
            )

            # 버전 1로 롤백
            rollback_revision = manager.rollback_to_revision(
                incident_id=incident_id,
                target_revision_number=1,
                rolled_back_by="admin@example.com",
                rollback_reason="Wrong data in version 2",
            )

            assert rollback_revision.revision_number == 3
            assert rollback_revision.change_type == RevisionChangeType.ROLLBACK
            assert rollback_revision.data_snapshot["correct_data"] is True
            assert "Rollback to revision 1" in rollback_revision.change_reason

    def test_rollback_sealed_postmortem_fails(self):
        """봉인된 Postmortem 롤백 불가."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-rollback-sealed"

            manager.create_revision(
                incident_id=incident_id,
                new_data={"data": "v1"},
                changed_by="user@example.com",
                change_reason="v1",
                change_type=RevisionChangeType.INITIAL,
            )
            manager.create_revision(
                incident_id=incident_id,
                new_data={"data": "v2"},
                changed_by="user@example.com",
                change_reason="v2",
                change_type=RevisionChangeType.CORRECTION,
            )

            manager.seal_postmortem(
                incident_id=incident_id,
                sealed_by="admin@example.com",
                seal_reason="Finalized",
            )

            with pytest.raises(ValueError) as exc_info:
                manager.rollback_to_revision(
                    incident_id=incident_id,
                    target_revision_number=1,
                    rolled_back_by="admin@example.com",
                    rollback_reason="Try rollback",
                )

            assert "sealed" in str(exc_info.value).lower()


class TestRevisionIntegrity:
    """리비전 무결성 해시 테스트."""

    def test_revision_has_integrity_hash(self):
        """리비전에 무결성 해시 추가됨."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            revision = manager.create_revision(
                incident_id="AUTO-hash-test",
                new_data={"test": "data"},
                changed_by="user@example.com",
                change_reason="Test",
                change_type=RevisionChangeType.INITIAL,
            )

            assert revision.integrity_hash
            assert len(revision.integrity_hash) > 0


class TestMaxRevisions:
    """최대 리비전 수 제한 테스트."""

    def test_max_revisions_limit(self):
        """최대 리비전 수 초과 시 오래된 리비전 삭제."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
                max_revisions=5,
            )

            incident_id = "AUTO-max-test"

            # 7개 리비전 생성
            for i in range(7):
                manager.create_revision(
                    incident_id=incident_id,
                    new_data={"version": i + 1},
                    changed_by="user@example.com",
                    change_reason=f"Version {i + 1}",
                    change_type=RevisionChangeType.CORRECTION,
                )

            all_revisions = manager.get_all_revisions(incident_id)

            # 최대 5개만 유지
            assert len(all_revisions) == 5
            # 가장 오래된 리비전 삭제됨 (3, 4, 5, 6, 7 유지)
            assert all_revisions[0].revision_number == 3
            assert all_revisions[-1].revision_number == 7


class TestRevisionSummary:
    """리비전 요약 정보 테스트."""

    def test_get_revision_summary(self):
        """리비전 요약 조회."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
            )

            incident_id = "AUTO-summary-test"

            manager.create_revision(
                incident_id=incident_id,
                new_data={"step": 1},
                changed_by="user1@example.com",
                change_reason="Step 1",
                change_type=RevisionChangeType.INITIAL,
            )
            manager.create_revision(
                incident_id=incident_id,
                new_data={"step": 2},
                changed_by="user2@example.com",
                change_reason="Step 2",
                change_type=RevisionChangeType.ANALYSIS_UPDATE,
            )

            summary = manager.get_revision_summary(incident_id)

            assert summary["incident_id"] == incident_id
            assert summary["total_count"] == 2
            assert summary["is_sealed"] is False
            assert summary["latest_revision"] == 2
            assert len(summary["revisions"]) == 2
            assert summary["revisions"][0]["change_type"] == "initial"
            assert summary["revisions"][1]["change_type"] == "analysis_update"


class TestVersioningDisabled:
    """버전 관리 비활성화 테스트."""

    def test_versioning_disabled_returns_minimal_revision(self):
        """버전 관리 비활성화 시 최소 리비전만 반환."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            manager = PostmortemRevisionManager(
                hash_chain_state_file=state_file,
                versioning_enabled=False,
            )

            revision = manager.create_revision(
                incident_id="AUTO-disabled-test",
                new_data={"data": "test"},
                changed_by="user@example.com",
                change_reason="Test",
                change_type=RevisionChangeType.INITIAL,
            )

            assert revision.revision_number == 1
            assert revision.data_snapshot == {"data": "test"}
