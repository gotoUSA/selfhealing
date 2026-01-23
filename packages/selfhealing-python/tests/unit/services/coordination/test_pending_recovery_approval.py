"""
Unit tests for Pending Recovery Approval.

Tests:
- 승인 요청 생성
- 승인/거부 처리
- 대기 목록 조회
- 방치 알림
- 만료 처리

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.4
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from selfhealing.services.coordination.pending_recovery_approval import (
    RecoveryApprovalStatus,
    RecoveryApprovalRequest,
    PendingRecoveryApprovalManager,
    get_pending_recovery_approval_manager,
    reset_pending_recovery_approval_manager,
)


class TestRecoveryApprovalRequest:
    """RecoveryApprovalRequest 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        request = RecoveryApprovalRequest(
            session_id="recovery-123",
            namespace="global",
        )
        
        assert request.request_id.startswith("approval-")
        assert request.session_id == "recovery-123"
        assert request.namespace == "global"
        assert request.status == RecoveryApprovalStatus.PENDING
        assert request.requested_at is not None
        assert request.expires_at is not None

    def test_auto_generate_request_id(self):
        """요청 ID 자동 생성."""
        request1 = RecoveryApprovalRequest(session_id="s1", namespace="global")
        request2 = RecoveryApprovalRequest(session_id="s2", namespace="global")
        
        assert request1.request_id != request2.request_id

    def test_expires_at_calculated(self):
        """만료 시각 자동 계산."""
        request = RecoveryApprovalRequest(
            session_id="s1",
            namespace="global",
            timeout_minutes=30,
        )
        
        expected_expiry = request.requested_at + timedelta(minutes=30)
        assert abs((request.expires_at - expected_expiry).total_seconds()) < 1

    def test_is_expired(self):
        """만료 여부 확인."""
        request = RecoveryApprovalRequest(
            session_id="s1",
            namespace="global",
            timeout_minutes=0,  # 즉시 만료
        )
        request.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        
        assert request.is_expired() is True

    def test_is_not_expired(self):
        """미만료 확인."""
        request = RecoveryApprovalRequest(
            session_id="s1",
            namespace="global",
            timeout_minutes=60,
        )
        
        assert request.is_expired() is False

    def test_is_pending(self):
        """대기 중 확인."""
        request = RecoveryApprovalRequest(session_id="s1", namespace="global")
        
        assert request.is_pending() is True
        
        request.status = RecoveryApprovalStatus.APPROVED
        assert request.is_pending() is False

    def test_get_waiting_time(self):
        """대기 시간 계산."""
        request = RecoveryApprovalRequest(session_id="s1", namespace="global")
        request.requested_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        
        waiting = request.get_waiting_time_minutes()
        
        assert 14.9 <= waiting <= 15.1

    def test_to_dict(self):
        """딕셔너리 변환."""
        request = RecoveryApprovalRequest(
            session_id="recovery-123",
            namespace="seoul",
            trigger_level="LEVEL_3",
        )
        
        data = request.to_dict()
        
        assert data["session_id"] == "recovery-123"
        assert data["namespace"] == "seoul"
        assert data["trigger_level"] == "LEVEL_3"
        assert data["status"] == "pending"
        assert "waiting_time_minutes" in data
        assert "is_expired" in data

    def test_from_dict(self):
        """딕셔너리에서 생성."""
        data = {
            "request_id": "approval-abc",
            "session_id": "recovery-123",
            "namespace": "seoul",
            "status": "approved",
            "approved_by": "admin",
        }
        
        request = RecoveryApprovalRequest.from_dict(data)
        
        assert request.request_id == "approval-abc"
        assert request.status == RecoveryApprovalStatus.APPROVED
        assert request.approved_by == "admin"


class TestPendingRecoveryApprovalManagerCreate:
    """요청 생성 테스트."""

    @pytest.fixture
    def manager(self):
        """테스트용 관리자."""
        return PendingRecoveryApprovalManager()

    def test_create_request(self, manager):
        """요청 생성."""
        request = manager.create_request(
            session_id="recovery-123",
            namespace="seoul",
            trigger_level="LEVEL_3",
        )
        
        assert request.session_id == "recovery-123"
        assert request.namespace == "seoul"
        assert request.trigger_level == "LEVEL_3"
        assert request.status == RecoveryApprovalStatus.PENDING

    def test_create_request_with_metadata(self, manager):
        """메타데이터 포함 요청 생성."""
        request = manager.create_request(
            session_id="recovery-123",
            namespace="seoul",
            trigger_level="LEVEL_3",
            metadata={"initiated_by": "system"},
        )
        
        assert request.metadata["initiated_by"] == "system"

    def test_create_duplicate_raises(self, manager):
        """중복 요청 생성 시 에러."""
        manager.create_request(
            session_id="recovery-123",
            namespace="seoul",
            trigger_level="LEVEL_3",
        )
        
        with pytest.raises(ValueError) as exc_info:
            manager.create_request(
                session_id="recovery-123",
                namespace="seoul",
                trigger_level="LEVEL_3",
            )
        
        assert "already exists" in str(exc_info.value)

    def test_create_after_approved_allowed(self, manager):
        """승인 후 동일 세션 재생성 가능."""
        request = manager.create_request(
            session_id="recovery-123",
            namespace="seoul",
            trigger_level="LEVEL_3",
        )
        
        # 승인 처리
        manager.approve(request.request_id, "admin")
        
        # 재생성 가능
        new_request = manager.create_request(
            session_id="recovery-123",
            namespace="seoul",
            trigger_level="LEVEL_3",
        )
        
        assert new_request.request_id != request.request_id


class TestPendingRecoveryApprovalManagerApprove:
    """승인/거부 테스트."""

    @pytest.fixture
    def manager(self):
        """테스트용 관리자."""
        return PendingRecoveryApprovalManager()

    def test_approve_request(self, manager):
        """요청 승인."""
        request = manager.create_request(
            session_id="recovery-123",
            namespace="seoul",
            trigger_level="LEVEL_3",
        )
        
        result = manager.approve(
            request_id=request.request_id,
            approved_by="admin@example.com",
            reason="Manual review completed",
        )
        
        assert result.status == RecoveryApprovalStatus.APPROVED
        assert result.approved_by == "admin@example.com"
        assert result.approval_reason == "Manual review completed"
        assert result.approved_at is not None

    def test_approve_nonexistent(self, manager):
        """없는 요청 승인."""
        result = manager.approve(
            request_id="nonexistent",
            approved_by="admin",
        )
        
        assert result is None

    def test_approve_already_approved(self, manager):
        """이미 승인된 요청."""
        request = manager.create_request(
            session_id="recovery-123",
            namespace="seoul",
            trigger_level="LEVEL_3",
        )
        
        manager.approve(request.request_id, "admin1")
        result = manager.approve(request.request_id, "admin2")
        
        # 이미 승인됨, 첫 승인자 유지
        assert result.approved_by == "admin1"

    def test_reject_request(self, manager):
        """요청 거부."""
        request = manager.create_request(
            session_id="recovery-123",
            namespace="seoul",
            trigger_level="LEVEL_3",
        )
        
        result = manager.reject(
            request_id=request.request_id,
            rejected_by="admin@example.com",
            reason="Stability not confirmed",
        )
        
        assert result.status == RecoveryApprovalStatus.REJECTED
        assert result.approved_by == "admin@example.com"


class TestPendingRecoveryApprovalManagerList:
    """목록 조회 테스트."""

    @pytest.fixture
    def manager(self):
        """테스트용 관리자."""
        return PendingRecoveryApprovalManager()

    def test_list_pending_requests(self, manager):
        """대기 중인 요청 목록."""
        manager.create_request("s1", "seoul", "LEVEL_3")
        manager.create_request("s2", "tokyo", "LEVEL_2")
        
        pending = manager.list_pending_requests()
        
        assert len(pending) == 2

    def test_list_pending_by_namespace(self, manager):
        """네임스페이스별 대기 목록."""
        manager.create_request("s1", "seoul", "LEVEL_3")
        manager.create_request("s2", "tokyo", "LEVEL_2")
        
        seoul_pending = manager.list_pending_requests(namespace="seoul")
        
        assert len(seoul_pending) == 1
        assert seoul_pending[0].namespace == "seoul"

    def test_list_pending_excludes_approved(self, manager):
        """승인된 요청 제외."""
        r1 = manager.create_request("s1", "seoul", "LEVEL_3")
        manager.create_request("s2", "tokyo", "LEVEL_2")
        
        manager.approve(r1.request_id, "admin")
        
        pending = manager.list_pending_requests()
        
        assert len(pending) == 1
        assert pending[0].namespace == "tokyo"

    def test_list_stale_requests(self, manager):
        """방치된 요청 목록."""
        request = manager.create_request("s1", "seoul", "LEVEL_3")
        
        # 시간 조작
        request.requested_at = datetime.now(timezone.utc) - timedelta(minutes=45)
        
        stale = manager.list_stale_requests(stale_threshold_minutes=30)
        
        assert len(stale) == 1

    def test_list_stale_excludes_recent(self, manager):
        """최근 요청은 방치 아님."""
        manager.create_request("s1", "seoul", "LEVEL_3")
        
        stale = manager.list_stale_requests(stale_threshold_minutes=30)
        
        assert len(stale) == 0


class TestPendingRecoveryApprovalManagerReminders:
    """리마인더 테스트."""

    def test_check_and_send_reminders(self):
        """리마인더 발송."""
        callback = MagicMock()
        manager = PendingRecoveryApprovalManager(
            notification_callback=callback,
            reminder_intervals_minutes=[5, 10],
        )
        
        request = manager.create_request("s1", "seoul", "LEVEL_3")
        
        # 시간 조작 (6분 경과)
        request.requested_at = datetime.now(timezone.utc) - timedelta(minutes=6)
        
        reminded = manager.check_and_send_reminders()
        
        assert len(reminded) == 1
        assert reminded[0].reminder_count == 1
        assert callback.call_count >= 2  # created + reminder

    def test_expire_old_requests(self):
        """만료 처리."""
        manager = PendingRecoveryApprovalManager()
        
        request = manager.create_request(
            "s1", "seoul", "LEVEL_3",
            timeout_minutes=30,
        )
        
        # 만료 시간 조작
        request.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        
        expired = manager.expire_old_requests()
        
        assert len(expired) == 1
        assert expired[0].status == RecoveryApprovalStatus.EXPIRED


class TestPendingRecoveryApprovalManagerCleanup:
    """정리 테스트."""

    @pytest.fixture
    def manager(self):
        """테스트용 관리자."""
        return PendingRecoveryApprovalManager()

    def test_cleanup_old_requests(self, manager):
        """오래된 요청 정리."""
        request = manager.create_request("s1", "seoul", "LEVEL_3")
        manager.approve(request.request_id, "admin")
        
        # 승인 시간 조작 (25시간 전)
        request.approved_at = datetime.now(timezone.utc) - timedelta(hours=25)
        
        cleaned = manager.cleanup_old_requests(max_age_hours=24)
        
        assert cleaned == 1

    def test_cleanup_keeps_pending(self, manager):
        """대기 중인 요청은 유지."""
        request = manager.create_request("s1", "seoul", "LEVEL_3")
        request.requested_at = datetime.now(timezone.utc) - timedelta(hours=48)
        
        cleaned = manager.cleanup_old_requests(max_age_hours=24)
        
        assert cleaned == 0  # 대기 중이므로 유지


class TestPendingRecoveryApprovalManagerStats:
    """통계 테스트."""

    @pytest.fixture
    def manager(self):
        """테스트용 관리자."""
        return PendingRecoveryApprovalManager()

    def test_get_stats(self, manager):
        """통계 조회."""
        r1 = manager.create_request("s1", "seoul", "LEVEL_3")
        r2 = manager.create_request("s2", "tokyo", "LEVEL_2")
        manager.approve(r1.request_id, "admin")
        
        stats = manager.get_stats()
        
        assert stats["total_requests"] == 2
        assert stats["pending_count"] == 1
        assert stats["approved_count"] == 1
        assert stats["rejected_count"] == 0
        assert "pending_by_namespace" in stats


class TestPendingRecoveryApprovalManagerSessionLookup:
    """세션 ID 조회 테스트."""

    @pytest.fixture
    def manager(self):
        """테스트용 관리자."""
        return PendingRecoveryApprovalManager()

    def test_get_request_by_session(self, manager):
        """세션 ID로 요청 조회."""
        manager.create_request("recovery-123", "seoul", "LEVEL_3")
        
        request = manager.get_request_by_session("recovery-123")
        
        assert request is not None
        assert request.session_id == "recovery-123"

    def test_get_request_by_session_not_found(self, manager):
        """없는 세션 조회."""
        request = manager.get_request_by_session("nonexistent")
        
        assert request is None


class TestPendingRecoveryApprovalManagerSingleton:
    """싱글톤 테스트."""

    def test_singleton(self):
        """싱글톤 동작 확인."""
        reset_pending_recovery_approval_manager()
        
        manager1 = get_pending_recovery_approval_manager()
        manager2 = get_pending_recovery_approval_manager()
        
        assert manager1 is manager2
        
        reset_pending_recovery_approval_manager()

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        reset_pending_recovery_approval_manager()
        
        manager1 = get_pending_recovery_approval_manager()
        reset_pending_recovery_approval_manager()
        manager2 = get_pending_recovery_approval_manager()
        
        assert manager1 is not manager2
        
        reset_pending_recovery_approval_manager()
