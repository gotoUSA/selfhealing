"""
🧹 청소부 레인 태스크 단위 테스트

Phase 2 테스트: 자율 운영 청소부 레인 태스크들

Tests:
1. ArchiveOldDLQEntriesTask - DLQ 아카이브 태스크
2. CleanupExpiredConfigTask - 만료 설정 정리 태스크
3. ExpireApprovalRequestsTask - 승인 요청 만료 태스크
4. PurgeArchivedDLQEntriesTask - 영구 삭제 태스크 (고위험)

Reference: docs/self_healing/middleware_system/09_AUTONOMOUS_TASK_EXPANSION.md §7
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

from selfhealing.tasks.cleanup_tasks import (
    ArchiveOldDLQEntriesTask,
    CleanupExpiredConfigTask,
    ExpireApprovalRequestsTask,
    PurgeArchivedDLQEntriesTask,
    CLEANUP_TASKS,
    get_cleanup_beat_schedule,
)
from selfhealing.tasks.base import reset_cooldowns
from selfhealing.tasks.notification_policy import (
    NotificationPolicy,
    NotificationTiming,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_cooldowns_fixture():
    """각 테스트 전 쿨다운 초기화."""
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
# ArchiveOldDLQEntriesTask 테스트
# =============================================================================


class TestArchiveOldDLQEntriesTask:
    """DLQ 아카이브 태스크 테스트."""

    def test_task_name(self):
        """태스크 이름 확인."""
        task = ArchiveOldDLQEntriesTask()
        assert task.name == "selfhealing.archive_old_dlq_entries"

    def test_notification_policy(self):
        """알림 정책 확인."""
        task = ArchiveOldDLQEntriesTask()
        policy = task.notification_policy
        
        assert policy.timing == NotificationTiming.AGGREGATED
        assert policy.aggregate is True
        assert policy.default_severity == "info"
        assert policy.cooldown_seconds == 86400  # 24시간

    def test_run_success(self, mock_dlq_service):
        """정상 실행."""
        mock_dlq_service.archive_old_entries.return_value = 42
        
        task = ArchiveOldDLQEntriesTask()
        result = task.run(older_than_days=30)
        
        assert result["success"] is True
        assert result["archived_count"] == 42
        assert result["older_than_days"] == 30
        
        mock_dlq_service.archive_old_entries.assert_called_once_with(
            older_than_days=30
        )

    def test_run_custom_days(self, mock_dlq_service):
        """커스텀 일수 설정."""
        mock_dlq_service.archive_old_entries.return_value = 10
        
        task = ArchiveOldDLQEntriesTask()
        result = task.run(older_than_days=60)
        
        assert result["older_than_days"] == 60
        mock_dlq_service.archive_old_entries.assert_called_once_with(
            older_than_days=60
        )

    def test_run_failure(self, mock_dlq_service):
        """실행 실패."""
        mock_dlq_service.archive_old_entries.side_effect = Exception("DB Error")
        
        task = ArchiveOldDLQEntriesTask()
        result = task.run()
        
        assert result["success"] is False
        assert "DB Error" in result["error"]

    def test_summary_message_success(self):
        """성공 메시지."""
        task = ArchiveOldDLQEntriesTask()
        result = {"archived_count": 42, "older_than_days": 30}
        
        message = task._get_summary_message(result)
        
        assert "📦" in message
        assert "42건" in message
        assert "30일" in message

    def test_summary_message_error(self):
        """에러 메시지."""
        task = ArchiveOldDLQEntriesTask()
        result = {"error": "Test error"}
        
        message = task._get_summary_message(result)
        
        assert "❌" in message
        assert "실패" in message


# =============================================================================
# CleanupExpiredConfigTask 테스트
# =============================================================================


class TestCleanupExpiredConfigTask:
    """만료 설정 정리 태스크 테스트."""

    def test_task_name(self):
        """태스크 이름 확인."""
        task = CleanupExpiredConfigTask()
        assert task.name == "selfhealing.cleanup_expired_config"

    def test_notification_policy(self):
        """알림 정책 확인."""
        task = CleanupExpiredConfigTask()
        policy = task.notification_policy
        
        assert policy.timing == NotificationTiming.AGGREGATED
        assert policy.aggregate is True
        assert policy.default_severity == "info"

    def test_run_success(self, mock_pending_config_service):
        """정상 실행."""
        mock_pending_config_service.cleanup_expired.return_value = 5
        
        task = CleanupExpiredConfigTask()
        result = task.run(older_than_hours=24)
        
        assert result["success"] is True
        assert result["expired_count"] == 5
        assert result["older_than_hours"] == 24
        
        mock_pending_config_service.cleanup_expired.assert_called_once_with(
            max_age_hours=24
        )

    def test_run_custom_hours(self, mock_pending_config_service):
        """커스텀 시간 설정."""
        mock_pending_config_service.cleanup_expired.return_value = 3
        
        task = CleanupExpiredConfigTask()
        result = task.run(older_than_hours=48)
        
        assert result["older_than_hours"] == 48

    def test_run_failure(self, mock_pending_config_service):
        """실행 실패."""
        mock_pending_config_service.cleanup_expired.side_effect = Exception("Config Error")
        
        task = CleanupExpiredConfigTask()
        result = task.run()
        
        assert result["success"] is False
        assert "Config Error" in result["error"]

    def test_summary_message(self):
        """성공 메시지."""
        task = CleanupExpiredConfigTask()
        result = {"expired_count": 5}
        
        message = task._get_summary_message(result)
        
        assert "🧹" in message
        assert "5건" in message


# =============================================================================
# ExpireApprovalRequestsTask 테스트
# =============================================================================


class TestExpireApprovalRequestsTask:
    """승인 요청 만료 태스크 테스트."""

    def test_task_name(self):
        """태스크 이름 확인."""
        task = ExpireApprovalRequestsTask()
        assert task.name == "selfhealing.expire_approval_requests"

    def test_notification_policy_threshold(self):
        """임계값 기반 알림 정책 확인."""
        task = ExpireApprovalRequestsTask()
        policy = task.notification_policy
        
        assert policy.timing == NotificationTiming.AGGREGATED
        assert policy.threshold == 5  # 5건 이상일 때만 알림
        assert policy.threshold_field == "expired_count"
        assert policy.default_severity == "warning"

    def test_run_success(self, mock_runtime_config_manager):
        """정상 실행."""
        mock_runtime_config_manager.expire_old_requests.return_value = 3
        
        task = ExpireApprovalRequestsTask()
        result = task.run(older_than_hours=72)
        
        assert result["success"] is True
        assert result["expired_count"] == 3
        assert result["older_than_hours"] == 72

    def test_should_notify_below_threshold(self, mock_runtime_config_manager):
        """임계값 미달 시 알림 안함."""
        mock_runtime_config_manager.expire_old_requests.return_value = 3
        
        task = ExpireApprovalRequestsTask()
        result = task.run()
        
        # 5건 미만이므로 알림 안함
        assert result["expired_count"] == 3
        assert task._should_notify(result) is False

    def test_should_notify_above_threshold(self):
        """임계값 초과 시 알림."""
        task = ExpireApprovalRequestsTask()
        result = {"expired_count": 10, "older_than_hours": 72}
        
        # 5건 이상이므로 알림
        assert task._should_notify(result) is True

    def test_summary_message(self):
        """성공 메시지."""
        task = ExpireApprovalRequestsTask()
        result = {"expired_count": 8, "older_than_hours": 72}
        
        message = task._get_summary_message(result)
        
        assert "⏰" in message
        assert "8건" in message
        assert "72시간" in message


# =============================================================================
# PurgeArchivedDLQEntriesTask 테스트 (고위험)
# =============================================================================


class TestPurgeArchivedDLQEntriesTask:
    """영구 삭제 태스크 테스트 (고위험)."""

    def test_task_name(self):
        """태스크 이름 확인."""
        task = PurgeArchivedDLQEntriesTask()
        assert task.name == "selfhealing.purge_archived_dlq_entries"

    def test_notification_policy_high_risk(self):
        """고위험 알림 정책 확인."""
        task = PurgeArchivedDLQEntriesTask()
        policy = task.notification_policy
        
        # 사전 승인 필수
        assert policy.timing == NotificationTiming.BEFORE
        assert policy.requires_approval is True
        
        # 항상 critical
        assert policy.default_severity == "critical"
        
        # Emergency Level 3에서도 승인 필요
        assert policy.escalate_on_emergency is False
        
        # 다중 채널 알림
        assert "slack" in policy.channels
        assert "email" in policy.channels

    def test_run_success(self, mock_dlq_service):
        """정상 실행."""
        mock_dlq_service.purge_archived.return_value = 100
        
        task = PurgeArchivedDLQEntriesTask()
        result = task.run(older_than_days=90)
        
        assert result["success"] is True
        assert result["purged_count"] == 100
        assert result["older_than_days"] == 90
        assert "PERMANENT DELETION" in result["warning"]

    def test_run_custom_days(self, mock_dlq_service):
        """커스텀 일수 설정."""
        mock_dlq_service.purge_archived.return_value = 50
        
        task = PurgeArchivedDLQEntriesTask()
        result = task.run(older_than_days=180)
        
        assert result["older_than_days"] == 180

    def test_run_failure(self, mock_dlq_service):
        """실행 실패."""
        mock_dlq_service.purge_archived.side_effect = Exception("Purge Error")
        
        task = PurgeArchivedDLQEntriesTask()
        result = task.run()
        
        assert result["success"] is False
        assert "Purge Error" in result["error"]

    def test_severity_always_critical(self):
        """항상 critical severity."""
        task = PurgeArchivedDLQEntriesTask()
        
        # 성공해도 critical
        result_success = {"purged_count": 10}
        assert task._get_severity(result_success) == "critical"
        
        # 실패해도 critical
        result_fail = {"error": "Test"}
        assert task._get_severity(result_fail) == "critical"

    def test_summary_message_warning(self):
        """경고 메시지 확인."""
        task = PurgeArchivedDLQEntriesTask()
        result = {"purged_count": 100}
        
        message = task._get_summary_message(result)
        
        assert "⚠️" in message
        assert "100건" in message
        assert "복구 불가" in message


# =============================================================================
# Task Registry 테스트
# =============================================================================


class TestCleanupTasksRegistry:
    """태스크 레지스트리 테스트."""

    def test_all_tasks_in_registry(self):
        """모든 태스크가 레지스트리에 등록."""
        assert len(CLEANUP_TASKS) == 4
        
        task_names = [t.name for t in [t() for t in CLEANUP_TASKS]]
        
        assert "selfhealing.archive_old_dlq_entries" in task_names
        assert "selfhealing.cleanup_expired_config" in task_names
        assert "selfhealing.expire_approval_requests" in task_names
        assert "selfhealing.purge_archived_dlq_entries" in task_names


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

    def test_all_tasks_have_notification_policy(self):
        """모든 태스크에 알림 정책이 설정됨."""
        for task_class in CLEANUP_TASKS:
            task = task_class()
            assert hasattr(task, "notification_policy")
            assert isinstance(task.notification_policy, NotificationPolicy)

    def test_high_risk_task_identified(self):
        """고위험 태스크 식별."""
        high_risk_tasks = []
        
        for task_class in CLEANUP_TASKS:
            task = task_class()
            if task.notification_policy.requires_approval:
                high_risk_tasks.append(task.name)
        
        # PurgeArchivedDLQEntriesTask만 고위험
        assert len(high_risk_tasks) == 1
        assert "purge_archived_dlq_entries" in high_risk_tasks[0]

    def test_aggregated_tasks(self):
        """집계 대상 태스크 확인."""
        aggregated_tasks = []
        
        for task_class in CLEANUP_TASKS:
            task = task_class()
            if task.notification_policy.aggregate:
                aggregated_tasks.append(task.name)
        
        # Archive, Cleanup, Expire 3개가 집계 대상
        assert len(aggregated_tasks) == 3

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
        
        # 각 태스크 실행
        archive_task = ArchiveOldDLQEntriesTask()
        cleanup_task = CleanupExpiredConfigTask()
        expire_task = ExpireApprovalRequestsTask()
        
        archive_result = archive_task.run()
        cleanup_result = cleanup_task.run()
        expire_result = expire_task.run()
        
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
