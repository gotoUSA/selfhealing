"""
Phase 25: RBAC-Audit 연동 Phase 2, 3 테스트

Phase 2 구현 사항:
- _write_to_wal()에 actor_roles 파라미터 추가 및 자동 전파
- _try_add_to_buffer()에 actor_roles 파라미터 추가 및 자동 전파

Phase 3 구현 사항:
- 개별 서비스 연동 (DLQ, CB, Replay, Retry)
- Celery Task actor_info 전달
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

from selfhealing.context.actor_context import (
    Actor,
    ActorContext,
    get_actor_for_celery,
    restore_actor_from_celery,
)


class TestWriteToWalWithActorRoles:
    """Phase 2.1: _write_to_wal() actor_roles 지원 테스트."""

    @patch("selfhealing.services.audit.base._get_wal")
    def test_write_to_wal_includes_actor_roles_from_context(self, mock_get_wal):
        """ActorContext에서 actor_roles가 자동으로 추출되어 WAL에 기록되는지 확인."""
        from selfhealing.services.audit.base import _write_to_wal
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 1
        mock_get_wal.return_value = mock_wal
        
        # When: ActorContext 설정 후 WAL 기록
        with ActorContext.set_actor(
            actor_id="admin@example.com",
            actor_type="selfhealing_admin",
            roles=["selfhealing_admin", "selfhealing_operator"],
        ):
            seq = _write_to_wal(
                event_type="CB_STATE_CHANGE",
                source="TestSource",
                details={"test": "data"},
            )
        
        # Then: WAL에 actor_roles가 포함되어야 함
        assert seq == 1
        mock_wal.write.assert_called_once()
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["actor_id"] == "admin@example.com"
        assert wal_entry["actor_type"] == "selfhealing_admin"
        assert wal_entry["actor_roles"] == ["selfhealing_admin", "selfhealing_operator"]

    @patch("selfhealing.services.audit.base._get_wal")
    def test_write_to_wal_uses_explicit_actor_roles(self, mock_get_wal):
        """명시적으로 전달된 actor_roles가 사용되는지 확인."""
        from selfhealing.services.audit.base import _write_to_wal
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 2
        mock_get_wal.return_value = mock_wal
        
        # When: 명시적 actor_roles와 함께 WAL 기록
        with ActorContext.set_actor(
            actor_id="user@example.com",
            actor_type="user",
            roles=["selfhealing_viewer"],  # Context에는 viewer
        ):
            seq = _write_to_wal(
                event_type="DLQ_STORE",
                source="TestSource",
                details={"test": "data"},
                actor_roles=["selfhealing_admin"],  # 명시적으로 admin 전달
            )
        
        # Then: 명시적 actor_roles가 사용되어야 함
        assert seq == 2
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["actor_roles"] == ["selfhealing_admin"]

    @patch("selfhealing.services.audit.base._get_wal")
    def test_write_to_wal_without_actor_context(self, mock_get_wal):
        """ActorContext 없을 때 빈 actor_roles로 기록되는지 확인."""
        from selfhealing.services.audit.base import _write_to_wal
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 3
        mock_get_wal.return_value = mock_wal
        
        # When: ActorContext 없이 WAL 기록
        seq = _write_to_wal(
            event_type="RETRY_ATTEMPT",
            source="TestSource",
            details={"test": "data"},
        )
        
        # Then: 빈 actor_roles로 기록되어야 함
        assert seq == 3
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["actor_roles"] == []
        assert wal_entry["actor_id"] is None
        assert wal_entry["actor_type"] == "system"


class TestTryAddToBufferWithActorRoles:
    """Phase 2.2: _try_add_to_buffer() actor_roles 지원 테스트."""

    @patch("selfhealing.audit.event_buffer.RequestAuditBuffer")
    def test_try_add_to_buffer_includes_actor_roles_in_details(
        self, mock_buffer_class
    ):
        """_try_add_to_buffer가 details에 actor_roles를 포함하는지 확인."""
        from selfhealing.services.audit.base import _try_add_to_buffer
        from selfhealing.audit.event_buffer import AuditEventType
        
        # Given: Mock buffer
        mock_buffer = MagicMock()
        mock_buffer_class.get_or_create.return_value = mock_buffer
        mock_request = MagicMock()
        
        # When: ActorContext 설정 후 버퍼에 추가
        with ActorContext.set_actor(
            actor_id="operator@example.com",
            actor_type="selfhealing_operator",
            roles=["selfhealing_operator"],
        ):
            result = _try_add_to_buffer(
                request=mock_request,
                event_type=AuditEventType.DLQ_STORE,
                source="TestSource",
                details={"dlq_id": 123},
            )
        
        # Then: buffer.add()가 호출되고 details에 _actor_roles가 포함됨
        assert result is True
        mock_buffer.add.assert_called_once()
        call_kwargs = mock_buffer.add.call_args[1]
        assert "_actor_roles" in call_kwargs["details"]
        assert call_kwargs["details"]["_actor_roles"] == ["selfhealing_operator"]

    def test_try_add_to_buffer_returns_false_when_no_request(self):
        """request가 None일 때 False 반환하는지 확인."""
        from selfhealing.services.audit.base import _try_add_to_buffer
        
        # When: request=None으로 호출
        result = _try_add_to_buffer(
            request=None,
            event_type="DLQ_STORE",
            source="TestSource",
            details={"test": "data"},
        )
        
        # Then: False 반환
        assert result is False


class TestPhase3CeleryTaskActorInfo:
    """Phase 3: Celery Task actor_info 전달 테스트."""

    def test_get_actor_for_celery_includes_roles(self):
        """get_actor_for_celery()가 roles를 포함하는지 확인."""
        # Given: ActorContext 설정
        with ActorContext.set_actor(
            actor_id="admin@example.com",
            actor_type="selfhealing_admin",
            roles=["selfhealing_admin", "selfhealing_operator"],
            source="api",
        ):
            # When: actor_info 추출
            actor_info = get_actor_for_celery()
        
        # Then: roles가 포함되어야 함
        assert actor_info["actor_id"] == "admin@example.com"
        assert actor_info["actor_type"] == "selfhealing_admin"
        assert actor_info["roles"] == ["selfhealing_admin", "selfhealing_operator"]
        assert "celery_from_api" in actor_info["source"]

    def test_restore_actor_from_celery_restores_roles(self):
        """restore_actor_from_celery()가 roles를 복원하는지 확인."""
        # Given: actor_info
        actor_info = {
            "actor_id": "operator@example.com",
            "actor_type": "selfhealing_operator",
            "roles": ["selfhealing_operator", "selfhealing_viewer"],
            "source": "celery_from_api",
        }
        
        # When: Celery Task에서 복원
        with restore_actor_from_celery(actor_info) as actor:
            # Then: 복원된 Actor에 roles가 있어야 함
            assert actor.actor_id == "operator@example.com"
            assert actor.actor_type == "selfhealing_operator"
            assert actor.roles == ["selfhealing_operator", "selfhealing_viewer"]
            
            # ActorContext에서도 동일하게 조회 가능해야 함
            current = ActorContext.get_current()
            assert current.roles == ["selfhealing_operator", "selfhealing_viewer"]

    def test_restore_actor_from_celery_handles_empty_info(self):
        """actor_info가 비어있을 때 SYSTEM_ACTOR로 처리되는지 확인."""
        from selfhealing.context.actor_context import SYSTEM_ACTOR
        
        # When: 빈 actor_info로 복원
        with restore_actor_from_celery({}) as actor:
            # Then: SYSTEM_ACTOR가 반환됨 (warning 로그와 함께)
            assert actor.actor_id == SYSTEM_ACTOR.actor_id  # "system"
            assert actor.roles == []

    @patch("selfhealing.services.audit.base._get_wal")
    def test_celery_task_preserves_actor_roles_in_audit(self, mock_get_wal):
        """Celery Task 내에서 Audit 기록 시 actor_roles가 유지되는지 확인."""
        from selfhealing.services.audit.base import _write_to_wal
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 10
        mock_get_wal.return_value = mock_wal
        
        # Given: actor_info (View에서 전달된 것처럼)
        actor_info = {
            "actor_id": "admin@example.com",
            "actor_type": "selfhealing_admin",
            "roles": ["selfhealing_admin"],
            "source": "celery_from_api",
        }
        
        # When: Celery Task 내에서 ActorContext 복원 후 Audit 기록
        with restore_actor_from_celery(actor_info):
            seq = _write_to_wal(
                event_type="DLQ_REPLAY_SUCCESS",
                source="ReplayService",
                details={"dlq_id": 456},
            )
        
        # Then: WAL에 actor_roles가 포함되어야 함
        assert seq == 10
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["actor_id"] == "admin@example.com"
        assert wal_entry["actor_roles"] == ["selfhealing_admin"]


class TestPhase3DLQAuditIntegration:
    """Phase 3.1: DLQ Audit actor_roles 자동 전파 테스트."""

    @patch("selfhealing.services.audit.base._get_wal")
    @patch("selfhealing.services.audit.dlq_audit._get_audit_adapter")
    def test_log_dlq_store_audit_includes_actor_roles(
        self, mock_adapter, mock_get_wal
    ):
        """log_dlq_store_audit이 actor_roles를 포함하는지 확인."""
        from selfhealing.services.audit.dlq_audit import log_dlq_store_audit
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 100
        mock_get_wal.return_value = mock_wal
        mock_adapter.return_value = None  # Adapter 없음
        
        # When: ActorContext 설정 후 DLQ store audit 기록
        with ActorContext.set_actor(
            actor_id="system@example.com",
            actor_type="selfhealing_operator",
            roles=["selfhealing_operator"],
        ):
            seq = log_dlq_store_audit(
                dlq_id=123,
                domain="payment",
                failure_type="PG_TIMEOUT",
            )
        
        # Then: WAL에 actor_roles가 포함되어야 함
        assert seq == 100
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["actor_id"] == "system@example.com"
        assert wal_entry["actor_roles"] == ["selfhealing_operator"]


class TestPhase3CBAuditIntegration:
    """Phase 3.2: CB Audit actor_roles 자동 전파 테스트."""

    @patch("selfhealing.services.audit.base._get_wal")
    def test_log_cb_state_change_audit_includes_actor_roles(self, mock_get_wal):
        """log_cb_state_change_audit이 actor_roles를 포함하는지 확인."""
        from selfhealing.services.audit.cb_audit import log_cb_state_change_audit
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 200
        mock_get_wal.return_value = mock_wal
        
        # When: ActorContext 설정 후 CB state change audit 기록
        with ActorContext.set_actor(
            actor_id="admin@example.com",
            actor_type="selfhealing_admin",
            roles=["selfhealing_admin"],
        ):
            seq = log_cb_state_change_audit(
                cb_name="toss-api",
                old_state="closed",
                new_state="open",
                reason="PG 점검",
            )
        
        # Then: WAL에 actor_roles가 포함되어야 함
        assert seq == 200
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["actor_id"] == "admin@example.com"
        assert wal_entry["actor_type"] == "selfhealing_admin"
        assert wal_entry["actor_roles"] == ["selfhealing_admin"]


class TestPhase3EndToEndFlow:
    """Phase 3: HTTP → Celery → Audit 전체 흐름 테스트."""

    @patch("selfhealing.services.audit.base._get_wal")
    def test_http_to_celery_to_audit_flow(self, mock_get_wal):
        """HTTP 요청 → Celery Task → Audit 전체 흐름에서 actor_roles 유지 확인."""
        from selfhealing.services.audit.base import _write_to_wal
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.side_effect = [1, 2]  # 두 번 호출됨
        mock_get_wal.return_value = mock_wal
        
        # === Step 1: HTTP 요청 컨텍스트 (View에서) ===
        with ActorContext.set_actor(
            actor_id="admin@example.com",
            actor_type="selfhealing_admin",
            roles=["selfhealing_admin", "selfhealing_operator"],
            source="api",
        ):
            # HTTP 요청에서 직접 Audit 기록
            _write_to_wal(
                event_type="DLQ_REPLAY_START",
                source="DLQReplayView",
                details={"dlq_id": 789},
            )
            
            # Celery Task에 전달할 actor_info 추출
            actor_info = get_actor_for_celery()
        
        # === Step 2: Celery Task에서 (별도 컨텍스트) ===
        with restore_actor_from_celery(actor_info):
            _write_to_wal(
                event_type="DLQ_REPLAY_SUCCESS",
                source="ReplayService",
                details={"dlq_id": 789, "result": "success"},
            )
        
        # Then: 두 WAL 기록 모두 동일한 actor_roles를 가져야 함
        assert mock_wal.write.call_count == 2
        
        first_entry = mock_wal.write.call_args_list[0][0][0]
        second_entry = mock_wal.write.call_args_list[1][0][0]
        
        # HTTP 요청에서 직접 기록한 것
        assert first_entry["actor_id"] == "admin@example.com"
        assert first_entry["actor_roles"] == ["selfhealing_admin", "selfhealing_operator"]
        
        # Celery Task에서 기록한 것 (actor_info를 통해 복원됨)
        assert second_entry["actor_id"] == "admin@example.com"
        assert second_entry["actor_roles"] == ["selfhealing_admin", "selfhealing_operator"]
