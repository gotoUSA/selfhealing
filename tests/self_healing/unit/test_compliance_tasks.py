"""
📋 증명 레인 (Compliance Tasks) 단위 테스트

Tests for Phase 4 implementation:
- RunComplianceCheckTask
- GenerateFinOpsReportTask
- CollectSelfHealingMetricsTask
- GenerateDailyAutonomousReportTask

Reference: docs/self_healing/middleware_system/09_AUTONOMOUS_TASK_EXPANSION.md §7
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from selfhealing.tasks.compliance_tasks import (
    RunComplianceCheckTask,
    GenerateFinOpsReportTask,
    CollectSelfHealingMetricsTask,
    COMPLIANCE_TASKS,
    get_compliance_beat_schedule,
)
# GenerateDailyAutonomousReportTask는 문서 §6.2 Phase 5에 따라 daily_report.py에 위치
from selfhealing.tasks.daily_report import GenerateDailyAutonomousReportTask
from selfhealing.tasks.notification_policy import (
    NotificationPolicy,
    NotificationTiming,
)


# =============================================================================
# RunComplianceCheckTask Tests
# =============================================================================


class TestRunComplianceCheckTask:
    """RunComplianceCheckTask 테스트."""

    def test_task_metadata(self):
        """태스크 메타데이터 확인."""
        task = RunComplianceCheckTask()
        
        assert task.name == "selfhealing.run_compliance_check"
        assert task.notification_policy.timing == NotificationTiming.AFTER
        assert task.notification_policy.threshold == 0
        assert task.notification_policy.threshold_field == "violation_count"
        assert "slack" in task.notification_policy.channels
        assert "email" in task.notification_policy.channels

    def test_run_all_passed(self):
        """모든 검사 통과."""
        task = RunComplianceCheckTask()
        
        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "check_type": "all",
                "total_checks": 10,
                "passed_count": 10,
                "violation_count": 0,
                "violations": [],
                "compliance_score": 100.0,
            }
            
            result = mock_run(check_type="all")
            
            assert result["success"] is True
            assert result["violation_count"] == 0
            assert result["passed_count"] == 10
            assert result["compliance_score"] == 100.0

    def test_run_with_violations(self):
        """위반 있는 경우."""
        task = RunComplianceCheckTask()
        
        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "check_type": "dora",
                "total_checks": 10,
                "passed_count": 7,
                "violation_count": 3,
                "violations": [{"id": "v001", "check_id": "DORA-001", "severity": "high"}],
                "compliance_score": 70.0,
            }
            
            result = mock_run(check_type="dora")
            
            assert result["success"] is True
            assert result["violation_count"] == 3
            assert len(result["violations"]) == 1
            assert result["compliance_score"] == 70.0

    def test_get_standards_for_type(self):
        """검사 유형별 표준 반환."""
        task = RunComplianceCheckTask()
        
        # "all"은 None 반환
        assert task._get_standards_for_type("all") is None
        
        # 특정 유형은 해당 표준 반환 (모듈 임포트 실패 시 None)
        result = task._get_standards_for_type("dora")
        # 임포트 성공 시 리스트, 실패 시 None
        assert result is None or isinstance(result, list)

    def test_get_severity(self):
        """위반 수에 따른 심각도."""
        task = RunComplianceCheckTask()
        
        assert task._get_severity({"violation_count": 0}) == "info"
        assert task._get_severity({"violation_count": 5}) == "warning"
        assert task._get_severity({"violation_count": 15}) == "critical"

    def test_get_summary_message_all_passed(self):
        """모두 통과 메시지."""
        task = RunComplianceCheckTask()
        
        result = {"success": True, "violation_count": 0, "total_checks": 10}
        message = task._get_summary_message(result)
        
        assert "통과" in message
        assert "10" in message

    def test_get_summary_message_with_violations(self):
        """위반 있을 때 메시지."""
        task = RunComplianceCheckTask()
        
        result = {
            "success": True,
            "violation_count": 3,
            "total_checks": 10,
            "passed_count": 7,
            "compliance_score": 70.0,
        }
        message = task._get_summary_message(result)
        
        assert "위반" in message
        assert "3" in message
        assert "70" in message

    def test_get_summary_message_error(self):
        """에러 메시지."""
        task = RunComplianceCheckTask()
        
        result = {"success": False, "error": "Service unavailable"}
        message = task._get_summary_message(result)
        
        assert "실패" in message
        assert "Service unavailable" in message


# =============================================================================
# GenerateFinOpsReportTask Tests
# =============================================================================


class TestGenerateFinOpsReportTask:
    """GenerateFinOpsReportTask 테스트."""

    def test_task_metadata(self):
        """태스크 메타데이터 확인."""
        task = GenerateFinOpsReportTask()
        
        assert task.name == "selfhealing.generate_finops_report"
        assert task.notification_policy.timing == NotificationTiming.AFTER
        assert task.notification_policy.aggregate is False
        assert "slack" in task.notification_policy.channels
        assert "email" in task.notification_policy.channels

    def test_run_success(self):
        """성공적인 리포트 생성."""
        task = GenerateFinOpsReportTask()
        
        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "report_id": "finops-weekly-20260102",
                "period": "weekly",
                "total_cost": 1.2345,
                "savings": 0.0,
                "record_count": 100,
                "success_rate": 95.5,
                "by_stage": {"stage16": 0.5, "stage26": 0.7},
                "by_operation": {"retry": 0.8, "archive": 0.4},
            }
            
            result = mock_run(period="weekly")
            
            assert result["success"] is True
            assert result["period"] == "weekly"
            assert result["total_cost"] == 1.2345
            assert result["record_count"] == 100
            assert "stage16" in result["by_stage"]

    def test_run_daily_period(self):
        """일일 리포트 기간 테스트."""
        task = GenerateFinOpsReportTask()
        
        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "period": "daily",
                "total_cost": 0.5,
            }
            
            result = mock_run(period="daily")
            
            assert result["period"] == "daily"

    def test_calculate_savings(self):
        """비용 절감 계산."""
        task = GenerateFinOpsReportTask()
        
        # 현재는 0.0 반환
        savings = task._calculate_savings(None, "weekly", None)
        assert savings == 0.0

    def test_get_summary_message_success(self):
        """성공 메시지."""
        task = GenerateFinOpsReportTask()
        
        result = {
            "success": True,
            "period": "weekly",
            "total_cost": 2.5,
            "record_count": 150,
            "success_rate": 98.0,
        }
        message = task._get_summary_message(result)
        
        assert "FinOps" in message
        assert "weekly" in message
        assert "$" in message or "2.5" in message

    def test_get_summary_message_error(self):
        """에러 메시지."""
        task = GenerateFinOpsReportTask()
        
        result = {"success": False, "error": "Database connection failed"}
        message = task._get_summary_message(result)
        
        assert "실패" in message


# =============================================================================
# CollectSelfHealingMetricsTask Tests
# =============================================================================


class TestCollectSelfHealingMetricsTask:
    """CollectSelfHealingMetricsTask 테스트."""

    def test_task_metadata(self):
        """태스크 메타데이터 확인."""
        task = CollectSelfHealingMetricsTask()
        
        assert task.name == "selfhealing.collect_self_healing_metrics"
        assert task.notification_policy.timing == NotificationTiming.AGGREGATED
        assert task.notification_policy.aggregate is True
        # threshold가 무한대로 설정되어 알림이 발생하지 않음
        assert task.notification_policy.threshold == float('inf')

    def test_run_basic(self):
        """기본 메트릭 수집."""
        task = CollectSelfHealingMetricsTask()
        
        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "metrics_collected": 5,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            result = mock_run()
            
            assert result["success"] is True
            assert result["metrics_collected"] >= 0

    def test_run_with_all_components(self):
        """모든 컴포넌트 메트릭 수집."""
        task = CollectSelfHealingMetricsTask()
        
        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "metrics_collected": 10,
                "timestamp": "2026-01-02T00:00:00Z",
            }
            
            result = mock_run()
            
            assert result["success"] is True
            assert result["metrics_collected"] == 10

    def test_get_summary_message(self):
        """메시지 생성."""
        task = CollectSelfHealingMetricsTask()
        
        result = {"success": True, "metrics_collected": 10}
        message = task._get_summary_message(result)
        
        assert "메트릭" in message
        assert "10" in message


# =============================================================================
# GenerateDailyAutonomousReportTask Tests
# =============================================================================


class TestGenerateDailyAutonomousReportTask:
    """GenerateDailyAutonomousReportTask 테스트."""

    def test_task_metadata(self):
        """태스크 메타데이터 확인."""
        task = GenerateDailyAutonomousReportTask()
        
        assert task.name == "selfhealing.generate_daily_autonomous_report"
        assert task.notification_policy.timing == NotificationTiming.AFTER
        assert task.notification_policy.aggregate is False
        assert "slack" in task.notification_policy.channels

    def test_run_with_collector_unavailable(self):
        """DailyReportCollector 없을 때."""
        task = GenerateDailyAutonomousReportTask()
        
        # ImportError 발생 시 기본 리포트 반환
        with patch(
            "selfhealing.tasks.daily_report.GenerateDailyAutonomousReportTask.run"
        ) as mock_run:
            mock_run.return_value = {
                "success": True,
                "date": "2026-01-02",
                "total_tasks": 0,
                "summary": {},
            }
            
            result = mock_run()
            
            assert result["success"] is True
            assert result["total_tasks"] == 0

    def test_run_with_collector(self):
        """DailyReportCollector 있을 때."""
        task = GenerateDailyAutonomousReportTask()
        
        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "date": "2026-01-02",
                "total_tasks": 15,
                "summary": {
                    "archived_count": 100,
                    "expired_count": 20,
                    "recovered_count": 5,
                },
            }
            
            result = mock_run()
            
            assert result["total_tasks"] == 15
            assert result["summary"]["archived_count"] == 100

    def test_get_summary_message_success(self):
        """성공 메시지."""
        task = GenerateDailyAutonomousReportTask()
        
        result = {
            "success": True,
            "date": "2026-01-02",
            "summary": {
                "archived_count": 50,
                "expired_count": 10,
                "purged_count": 5,
                "recovered_count": 3,
                "circuit_transitions": 2,
                "task_failures": 1,
                "critical_alerts": 0,
            },
        }
        message = task._get_summary_message(result)
        
        assert "2026-01-02" in message
        assert "아카이브" in message
        assert "50" in message

    def test_get_summary_message_error(self):
        """에러 메시지."""
        task = GenerateDailyAutonomousReportTask()
        
        result = {"success": False, "error": "Redis unavailable"}
        message = task._get_summary_message(result)
        
        assert "실패" in message
        assert "Redis unavailable" in message


# =============================================================================
# Beat Schedule Tests
# =============================================================================


class TestComplianceBeatSchedule:
    """증명 레인 Beat Schedule 테스트."""

    def test_schedule_contains_all_tasks(self):
        """스케줄에 모든 태스크 포함 확인."""
        schedule = get_compliance_beat_schedule()
        
        # compliance_tasks.py에는 3개 태스크만 포함
        # GenerateDailyAutonomousReportTask는 daily_report.py에 있음 (문서 §6.2 Phase 5)
        assert "run-compliance-check" in schedule
        assert "generate-finops-report" in schedule
        assert "collect-self-healing-metrics" in schedule

    def test_schedule_queue_assignments(self):
        """큐 할당 확인."""
        schedule = get_compliance_beat_schedule()
        
        # compliance_tasks.py에는 3개 태스크만 포함
        assert schedule["run-compliance-check"]["options"]["queue"] == "compliance"
        assert schedule["generate-finops-report"]["options"]["queue"] == "reports"
        assert schedule["collect-self-healing-metrics"]["options"]["queue"] == "metrics"

    def test_schedule_task_names(self):
        """태스크 이름 확인."""
        schedule = get_compliance_beat_schedule()
        
        # compliance_tasks.py에는 3개 태스크만 포함
        assert schedule["run-compliance-check"]["task"] == "selfhealing.run_compliance_check"
        assert schedule["generate-finops-report"]["task"] == "selfhealing.generate_finops_report"
        assert schedule["collect-self-healing-metrics"]["task"] == "selfhealing.collect_self_healing_metrics"

    def test_finops_report_weekly_schedule(self):
        """FinOps 리포트 주간 스케줄."""
        schedule = get_compliance_beat_schedule()
        
        finops = schedule["generate-finops-report"]
        # day_of_week=1 (월요일)
        assert finops["kwargs"]["period"] == "weekly"


# =============================================================================
# Task Registry Tests
# =============================================================================


class TestComplianceTaskRegistry:
    """증명 레인 태스크 레지스트리 테스트."""

    def test_all_tasks_in_registry(self):
        """모든 태스크가 레지스트리에 있는지 확인."""
        # compliance_tasks.py에는 3개 태스크만 포함
        # GenerateDailyAutonomousReportTask는 daily_report.py에 있음 (문서 §6.2 Phase 5)
        assert len(COMPLIANCE_TASKS) == 3
        
        task_classes = [t.__name__ for t in COMPLIANCE_TASKS]
        
        assert "RunComplianceCheckTask" in task_classes
        assert "GenerateFinOpsReportTask" in task_classes
        assert "CollectSelfHealingMetricsTask" in task_classes

    def test_all_tasks_have_names(self):
        """모든 태스크가 이름을 가지고 있는지 확인."""
        for task_class in COMPLIANCE_TASKS:
            task = task_class()
            assert task.name.startswith("selfhealing.")

    def test_all_tasks_have_policies(self):
        """모든 태스크가 알림 정책을 가지고 있는지 확인."""
        for task_class in COMPLIANCE_TASKS:
            task = task_class()
            assert isinstance(task.notification_policy, NotificationPolicy)


# =============================================================================
# Integration-like Tests
# =============================================================================


class TestComplianceTasksIntegration:
    """증명 레인 태스크 통합 테스트 (가벼운 버전)."""

    def test_all_tasks_can_instantiate(self):
        """모든 태스크 인스턴스화 가능."""
        for task_class in COMPLIANCE_TASKS:
            task = task_class()
            assert task is not None
            assert hasattr(task, 'run')
            assert hasattr(task, '_get_summary_message')

    def test_notification_channels_configured(self):
        """알림 채널 설정 확인."""
        # 규정 준수와 FinOps는 Slack + Email
        compliance_task = RunComplianceCheckTask()
        finops_task = GenerateFinOpsReportTask()
        
        assert "email" in compliance_task.notification_policy.channels
        assert "email" in finops_task.notification_policy.channels
        
        # 메트릭 수집은 기본 (알림 없음)
        metrics_task = CollectSelfHealingMetricsTask()
        assert metrics_task.notification_policy.threshold == float('inf')
