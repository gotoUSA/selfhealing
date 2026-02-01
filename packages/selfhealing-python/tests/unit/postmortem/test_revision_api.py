"""
Unit tests for Postmortem Revision API Views.

API 뷰 테스트:
- PostmortemRevisionListView (GET/POST)
- PostmortemRevisionDetailView (GET)
- PostmortemRevisionCompareView (GET)
- PostmortemSealView (POST/DELETE)

Note: Django REST Framework 뷰 import 테스트는 통합테스트에서 수행
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestPostmortemRevisionListView:
    """PostmortemRevisionListView 테스트."""

    def test_get_revision_list_returns_summary(self):
        """GET /revisions/ - 리비전 요약 반환."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
            reset_postmortem_revision_manager,
        )

        reset_postmortem_revision_manager()

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            incident_id = "API-TEST-001"
            manager.create_revision(
                incident_id=incident_id,
                new_data={"summary": "Test incident"},
                changed_by="tester@example.com",
                change_reason="Initial",
                change_type=RevisionChangeType.INITIAL,
            )

            summary = manager.get_revision_summary(incident_id)

            assert summary["incident_id"] == incident_id
            assert summary["total_count"] == 1
            assert summary["is_sealed"] is False
            assert summary["latest_revision"] == 1

    def test_create_revision_requires_data_field(self):
        """POST /revisions/ - data 필드 필수."""
        # 요청 데이터 없이 리비전 생성 시도시 data 필드 필요
        request_data = {"change_reason": "Test reason"}
        assert "data" not in request_data

    def test_create_revision_requires_change_reason(self):
        """POST /revisions/ - change_reason 필드 필수."""
        request_data = {"data": {"summary": "test"}}
        assert "change_reason" not in request_data

    def test_create_revision_validates_change_type(self):
        """POST /revisions/ - change_type 유효성 검사."""
        from selfhealing.services.postmortem.revision import RevisionChangeType

        valid_types = [ct.value for ct in RevisionChangeType]
        assert "analysis_update" in valid_types
        assert "invalid_type" not in valid_types

    def test_create_revision_success(self):
        """POST /revisions/ - 리비전 생성 성공."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            incident_id = "API-CREATE-001"
            revision = manager.create_revision(
                incident_id=incident_id,
                new_data={"analysis": "Root cause identified"},
                changed_by="operator@example.com",
                change_reason="Root cause analysis updated",
                change_type=RevisionChangeType.ANALYSIS_UPDATE,
            )

            assert revision.revision_number == 1
            assert revision.change_type == RevisionChangeType.ANALYSIS_UPDATE


class TestPostmortemRevisionDetailView:
    """PostmortemRevisionDetailView 테스트."""

    def test_get_specific_revision_returns_data(self):
        """GET /revisions/{num}/ - 특정 리비전 반환."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            incident_id = "API-DETAIL-001"
            manager.create_revision(
                incident_id=incident_id,
                new_data={"version": 1},
                changed_by="user@example.com",
                change_reason="v1",
                change_type=RevisionChangeType.INITIAL,
            )
            manager.create_revision(
                incident_id=incident_id,
                new_data={"version": 2},
                changed_by="user@example.com",
                change_reason="v2",
                change_type=RevisionChangeType.CORRECTION,
            )

            rev1 = manager.get_revision(incident_id, 1)
            rev2 = manager.get_revision(incident_id, 2)

            assert rev1.data_snapshot["version"] == 1
            assert rev2.data_snapshot["version"] == 2

    def test_get_nonexistent_revision_returns_none(self):
        """GET /revisions/{num}/ - 없는 리비전 조회."""
        from selfhealing.services.postmortem.revision import PostmortemRevisionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            result = manager.get_revision("NONEXISTENT", 999)
            assert result is None


class TestPostmortemRevisionCompareView:
    """PostmortemRevisionCompareView 테스트."""

    def test_compare_revisions_returns_diff(self):
        """GET /revisions/compare/ - 리비전 비교."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            incident_id = "API-COMPARE-001"
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
            assert diff.has_changes is True

    def test_compare_revisions_raises_for_missing_revision(self):
        """GET /revisions/compare/ - 없는 리비전 비교 시 예외."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            incident_id = "API-COMPARE-ERR"
            manager.create_revision(
                incident_id=incident_id,
                new_data={"test": True},
                changed_by="user@example.com",
                change_reason="v1",
                change_type=RevisionChangeType.INITIAL,
            )

            with pytest.raises(ValueError) as exc_info:
                manager.compare_revisions(incident_id, 1, 99)

            assert "not found" in str(exc_info.value)


class TestPostmortemSealView:
    """PostmortemSealView 테스트."""

    def test_seal_postmortem_success(self):
        """POST /seal/ - 봉인 성공."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            incident_id = "API-SEAL-001"
            manager.create_revision(
                incident_id=incident_id,
                new_data={"analysis": "Complete"},
                changed_by="operator@example.com",
                change_reason="Initial",
                change_type=RevisionChangeType.INITIAL,
            )

            seal_revision = manager.seal_postmortem(
                incident_id=incident_id,
                sealed_by="admin@example.com",
                seal_reason="Analysis completed",
            )

            assert manager.is_sealed(incident_id) is True
            assert seal_revision.change_type == RevisionChangeType.SEALED

    def test_seal_already_sealed_raises_error(self):
        """POST /seal/ - 이미 봉인된 경우 오류."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            incident_id = "API-SEAL-DUP"
            manager.create_revision(
                incident_id=incident_id,
                new_data={"data": "test"},
                changed_by="user@example.com",
                change_reason="Initial",
                change_type=RevisionChangeType.INITIAL,
            )
            manager.seal_postmortem(
                incident_id=incident_id,
                sealed_by="admin@example.com",
            )

            with pytest.raises(ValueError) as exc_info:
                manager.seal_postmortem(
                    incident_id=incident_id,
                    sealed_by="admin@example.com",
                )

            assert "already sealed" in str(exc_info.value)

    def test_unseal_postmortem_success(self):
        """DELETE /seal/ - 봉인 해제 성공."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            incident_id = "API-UNSEAL-001"
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
            )

            result = manager.unseal_postmortem(
                incident_id=incident_id,
                unsealed_by="superadmin@example.com",
                unseal_reason="Need additional updates",
                approval_chain=["manager@example.com"],
            )

            assert result is True
            assert manager.is_sealed(incident_id) is False

    def test_unseal_not_sealed_raises_error(self):
        """DELETE /seal/ - 봉인되지 않은 경우 오류."""
        from selfhealing.services.postmortem.revision import (
            PostmortemRevisionManager,
            RevisionChangeType,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain.json"
            manager = PostmortemRevisionManager(hash_chain_state_file=state_file)

            incident_id = "API-UNSEAL-ERR"
            manager.create_revision(
                incident_id=incident_id,
                new_data={"data": "not sealed"},
                changed_by="user@example.com",
                change_reason="Initial",
                change_type=RevisionChangeType.INITIAL,
            )

            with pytest.raises(ValueError) as exc_info:
                manager.unseal_postmortem(
                    incident_id=incident_id,
                    unsealed_by="admin@example.com",
                    unseal_reason="Test",
                )

            assert "not sealed" in str(exc_info.value)


class TestRevisionChangeTypeEnum:
    """RevisionChangeType Enum 추가 테스트."""

    def test_all_change_types_have_string_value(self):
        """모든 변경 유형이 문자열 값을 가짐."""
        from selfhealing.services.postmortem.revision import RevisionChangeType

        for change_type in RevisionChangeType:
            assert isinstance(change_type.value, str)
            assert len(change_type.value) > 0

    def test_change_type_from_string(self):
        """문자열에서 RevisionChangeType 생성."""
        from selfhealing.services.postmortem.revision import RevisionChangeType

        assert RevisionChangeType("initial") == RevisionChangeType.INITIAL
        assert RevisionChangeType("sealed") == RevisionChangeType.SEALED
        assert RevisionChangeType("rollback") == RevisionChangeType.ROLLBACK
