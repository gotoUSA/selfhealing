"""
🧹 청소부 레인 태스크 단위 테스트

자율 운영 청소부 레인 태스크들 테스트 (Thin Task, Fat Service 패턴)

Tests:
1. archive_old_dlq_entries - DLQ 아카이브 태스크
2. cleanup_expired_config - 만료 설정 정리 태스크
3. expire_approval_requests - 승인 요청 만료 태스크
4. purge_archived_dlq_entries - 영구 삭제 태스크 (고위험)
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock, patch

from selfhealing.tasks.cleanup_tasks import (
    archive_old_dlq_entries,
    cleanup_expired_config,
    expire_approval_requests,
    purge_archived_dlq_entries,
    get_cleanup_beat_schedule,
    # Backward compatibility imports
    ArchiveOldDLQEntriesTask,
    CleanupExpiredConfigTask,
    ExpireApprovalRequestsTask,
    PurgeArchivedDLQEntriesTask,
    CLEANUP_TASKS,
)
from selfhealing.tasks.base import reset_cooldowns


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_services():
    """각 테스트 전 서비스 및 쿨다운 초기화."""
    reset_cooldowns()
    yield
    reset_cooldowns()


@pytest.fixture
def mock_dlq_service():
    """DLQ 서비스 모킹."""
    with patch("selfhealing.services.dlq_service.get_dlq_service") as mock_get:
        mock_service = Mock()
        mock_get.return_value = mock_service
        yield mock_service


@pytest.fixture
def mock_pending_config_service():
    """Pending Config 서비스 모킹."""
    with patch("selfhealing.services.pending_config.get_pending_config_service") as mock_get:
        mock_service = Mock()
        mock_get.return_value = mock_service
        yield mock_service


@pytest.fixture
def mock_runtime_config_manager():
    """Runtime Config Manager 모킹."""
    with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_get:
        mock_manager = Mock()
        mock_get.return_value = mock_manager
        yield mock_manager


# =============================================================================
# archive_old_dlq_entries 함수 테스트
# =============================================================================


class TestArchiveOldDLQEntries:
    """DLQ 아카이브 함수 테스트."""

    def test_function_exists(self):
        """함수 존재 확인."""
        assert callable(archive_old_dlq_entries)

    def test_run_success(self, mock_dlq_service):
        """정상 실행."""
        mock_dlq_service.archive_old_entries.return_value = 42
        
        result = archive_old_dlq_entries(older_than_days=30)
        
        assert result["success"] is True
        assert result["archived_count"] == 42
        assert result["older_than_days"] == 30
        
        mock_dlq_service.archive_old_entries.assert_called_once_with(
            older_than_days=30
        )

    def test_run_custom_days(self, mock_dlq_service):
        """커스텀 일수 설정."""
        mock_dlq_service.archive_old_entries.return_value = 10
        
        result = archive_old_dlq_entries(older_than_days=60)
        
        assert result["older_than_days"] == 60
        mock_dlq_service.archive_old_entries.assert_called_once_with(
            older_than_days=60
        )

    def test_run_failure(self, mock_dlq_service):
        """실행 실패."""
        mock_dlq_service.archive_old_entries.side_effect = Exception("DB Error")
        
        result = archive_old_dlq_entries()
        
        assert result["success"] is False
        assert "DB Error" in result["error"]


# =============================================================================
# cleanup_expired_config 함수 테스트
# =============================================================================


class TestCleanupExpiredConfig:
    """만료 설정 정리 함수 테스트."""

    def test_function_exists(self):
        """함수 존재 확인."""
        assert callable(cleanup_expired_config)

    def test_run_success(self, mock_pending_config_service):
        """정상 실행."""
        mock_pending_config_service.cleanup_expired.return_value = 5
        
        result = cleanup_expired_config(older_than_hours=24)
        
        assert result["success"] is True
        assert result["expired_count"] == 5
        assert result["older_than_hours"] == 24
        
        mock_pending_config_service.cleanup_expired.assert_called_once_with(
            max_age_hours=24
        )

    def test_run_custom_hours(self, mock_pending_config_service):
        """커스텀 시간 설정."""
        mock_pending_config_service.cleanup_expired.return_value = 3
        
        result = cleanup_expired_config(older_than_hours=48)
        
        assert result["older_than_hours"] == 48

    def test_run_failure(self, mock_pending_config_service):
        """실행 실패."""
        mock_pending_config_service.cleanup_expired.side_effect = Exception("Config Error")
        
        result = cleanup_expired_config()
        
        assert result["success"] is False
        assert "Config Error" in result["error"]


# =============================================================================
# expire_approval_requests 함수 테스트
# =============================================================================


class TestExpireApprovalRequests:
    """승인 요청 만료 함수 테스트."""

    def test_function_exists(self):
        """함수 존재 확인."""
        assert callable(expire_approval_requests)

    def test_run_success(self, mock_runtime_config_manager):
        """정상 실행."""
        mock_runtime_config_manager.expire_old_requests.return_value = 3
        
        result = expire_approval_requests(older_than_hours=72)
        
        assert result["success"] is True
        assert result["expired_count"] == 3
        assert result["older_than_hours"] == 72

    def test_run_failure(self, mock_runtime_config_manager):
        """실행 실패."""
        mock_runtime_config_manager.expire_old_requests.side_effect = Exception("Expire Error")
        
        result = expire_approval_requests()
        
        assert result["success"] is False
        assert "Expire Error" in result["error"]


# =============================================================================
# purge_archived_dlq_entries 함수 테스트 (고위험)
# =============================================================================


class TestPurgeArchivedDLQEntries:
    """영구 삭제 함수 테스트 (고위험)."""

    def test_function_exists(self):
        """함수 존재 확인."""
        assert callable(purge_archived_dlq_entries)

    def test_run_success(self, mock_dlq_service):
        """정상 실행."""
        mock_dlq_service.purge_archived.return_value = 100
        
        result = purge_archived_dlq_entries(older_than_days=90)
        
        assert result["success"] is True
        assert result["purged_count"] == 100
        assert result["older_than_days"] == 90
        assert "PERMANENT DELETION" in result["warning"]

    def test_run_custom_days(self, mock_dlq_service):
        """커스텀 일수 설정."""
        mock_dlq_service.purge_archived.return_value = 50
        
        result = purge_archived_dlq_entries(older_than_days=180)
        
        assert result["older_than_days"] == 180

    def test_run_failure(self, mock_dlq_service):
        """실행 실패."""
        mock_dlq_service.purge_archived.side_effect = Exception("Purge Error")
        
        result = purge_archived_dlq_entries()
        
        assert result["success"] is False
        assert "Purge Error" in result["error"]


# =============================================================================
# Backward Compatibility 테스트
# =============================================================================


class TestBackwardCompatibility:
    """레거시 클래스 호환성 테스트."""

    def test_legacy_class_imports(self):
        """레거시 클래스 import 가능."""
        assert ArchiveOldDLQEntriesTask is not None
        assert CleanupExpiredConfigTask is not None
        assert ExpireApprovalRequestsTask is not None
        assert PurgeArchivedDLQEntriesTask is not None

    def test_cleanup_tasks_list(self):
        """CLEANUP_TASKS 리스트 존재."""
        assert CLEANUP_TASKS is not None
        assert len(CLEANUP_TASKS) == 4

    def test_legacy_wrapper_has_name(self):
        """레거시 래퍼에 name 속성 존재."""
        assert hasattr(ArchiveOldDLQEntriesTask, "name")
        assert hasattr(CleanupExpiredConfigTask, "name")
        assert hasattr(ExpireApprovalRequestsTask, "name")
        assert hasattr(PurgeArchivedDLQEntriesTask, "name")


# =============================================================================
# Beat Schedule 테스트
# =============================================================================


class TestCleanupBeatSchedule:
    """Beat Schedule 설정 테스트."""

    def test_schedule_contains_all_tasks(self):
        """모든 태스크가 스케줄에 포함."""
        schedule = get_cleanup_beat_schedule()
        
        assert "cleanup-expired-config" in schedule
        assert "archive-old-dlq-entries" in schedule
        assert "expire-approval-requests" in schedule
        assert "purge-archived-dlq-entries" in schedule

    def test_cleanup_expired_config_schedule(self):
        """만료 설정 정리 스케줄 확인."""
        schedule = get_cleanup_beat_schedule()
        config = schedule["cleanup-expired-config"]
        
        assert config["task"] == "selfhealing.cleanup_expired_config"
        assert config["options"]["queue"] == "maintenance"
        assert config["kwargs"]["older_than_hours"] == 24

    def test_archive_dlq_schedule(self):
        """DLQ 아카이브 스케줄 확인."""
        schedule = get_cleanup_beat_schedule()
        config = schedule["archive-old-dlq-entries"]
        
        assert config["task"] == "selfhealing.archive_old_dlq_entries"
        assert config["options"]["queue"] == "maintenance"
        assert config["kwargs"]["older_than_days"] == 30

    def test_expire_approval_schedule(self):
        """승인 만료 스케줄 확인."""
        schedule = get_cleanup_beat_schedule()
        config = schedule["expire-approval-requests"]
        
        assert config["task"] == "selfhealing.expire_approval_requests"
        assert config["kwargs"]["older_than_hours"] == 72

    def test_purge_dlq_schedule_critical_queue(self):
        """영구 삭제는 critical_maintenance 큐 사용."""
        schedule = get_cleanup_beat_schedule()
        config = schedule["purge-archived-dlq-entries"]
        
        assert config["task"] == "selfhealing.purge_archived_dlq_entries"
        assert config["options"]["queue"] == "critical_maintenance"
        assert config["kwargs"]["older_than_days"] == 90


# =============================================================================
# 통합 시나리오 테스트
# =============================================================================


class TestCleanupTaskIntegration:
    """청소부 레인 통합 테스트."""

    @patch("selfhealing.services.dlq_service.get_dlq_service")
    @patch("selfhealing.services.pending_config.get_pending_config_service")
    @patch("selfhealing.services.runtime_config.get_runtime_config_manager")
    def test_daily_cleanup_simulation(
        self, 
        mock_runtime, 
        mock_pending, 
        mock_dlq
    ):
        """일일 청소 시뮬레이션."""
        # 모킹 설정
        mock_dlq.return_value.archive_old_entries.return_value = 10
        mock_pending.return_value.cleanup_expired.return_value = 5
        mock_runtime.return_value.expire_old_requests.return_value = 2
        
        # 각 함수 실행 (Thin Wrapper → Service 위임)
        archive_result = archive_old_dlq_entries()
        cleanup_result = cleanup_expired_config()
        expire_result = expire_approval_requests()
        
        # 결과 확인
        assert archive_result["archived_count"] == 10
        assert cleanup_result["expired_count"] == 5
        assert expire_result["expired_count"] == 2
        
        # 일일 요약 데이터 집계 가능 확인
        total_cleaned = (
            archive_result["archived_count"] +
            cleanup_result["expired_count"] +
            expire_result["expired_count"]
        )
        assert total_cleaned == 17
