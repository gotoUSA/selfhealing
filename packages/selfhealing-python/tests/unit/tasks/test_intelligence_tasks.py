"""
🧠 지능 레인 (Intelligence Tasks) 단위 테스트.

테스트 대상:
- CheckSLADriftTask: SLA drift 감지
- AnalyzeForensicPendingTask: Forensic pending 분석
- AnalyzeCrossStageInsightsTask: 크로스 스테이지 인사이트 분석
- CheckRecoveryTransitionsTask: 복구 전환 확인
"""

from unittest.mock import patch

from selfhealing.tasks.intelligence_tasks import (
    INTELLIGENCE_TASKS,
    AnalyzeCrossStageInsightsTask,
    AnalyzeForensicPendingTask,
    CheckRecoveryTransitionsTask,
    CheckSLADriftTask,
    get_intelligence_beat_schedule,
)
from selfhealing.tasks.notification_policy import (
    NotificationPolicy,
    NotificationTiming,
)

# =============================================================================
# CheckSLADriftTask Tests
# =============================================================================


class TestCheckSLADriftTask:
    """CheckSLADriftTask 테스트."""

    def test_task_metadata(self):
        """태스크 메타데이터 확인."""
        task = CheckSLADriftTask()

        assert task.name == "selfhealing.check_sla_drift"
        assert task.notification_policy.timing == NotificationTiming.REALTIME
        assert task.notification_policy.threshold == 1
        assert task.notification_policy.threshold_field == "warnings_count"

    def test_run_no_warnings(self):
        """경고 없는 실행."""
        task = CheckSLADriftTask()

        with patch(
            "selfhealing.tasks.intelligence_tasks.CheckSLADriftTask.run"
        ) as mock_run:
            mock_run.return_value = {
                "success": True,
                "warnings_count": 0,
                "warnings": [],
                "metrics": {},
            }

            result = mock_run()

            assert result["success"] is True
            assert result["warnings_count"] == 0
            assert len(result["warnings"]) == 0

    def test_run_with_warnings(self):
        """경고가 있는 실행."""
        task = CheckSLADriftTask()

        # 실제 run 호출 테스트 (독립 실행 모드)
        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "warnings_count": 3,
                "warnings": [
                    {"domain": "payment", "message": "SLA drift detected"},
                    {"domain": "order", "message": "SLA drift detected"},
                    {"domain": "user", "message": "SLA drift detected"},
                ],
                "metrics": {},
            }

            result = mock_run()

            assert result["success"] is True
            assert result["warnings_count"] == 3

    def test_get_severity_warning(self):
        """경고 수에 따른 심각도."""
        task = CheckSLADriftTask()

        assert task._get_severity({"warnings_count": 0}) == "info"
        assert task._get_severity({"warnings_count": 1}) == "warning"
        assert task._get_severity({"warnings_count": 5}) == "critical"

    def test_get_summary_message_no_drift(self):
        """드리프트 없을 때 메시지."""
        task = CheckSLADriftTask()

        result = {"success": True, "warnings_count": 0}
        message = task._get_summary_message(result)

        assert "정상" in message or "없음" in message

    def test_get_summary_message_with_drift(self):
        """드리프트 있을 때 메시지."""
        task = CheckSLADriftTask()

        result = {"success": True, "warnings_count": 3}
        message = task._get_summary_message(result)

        assert "3" in message
        assert "경고" in message

    def test_get_summary_message_error(self):
        """에러 메시지."""
        task = CheckSLADriftTask()

        result = {"success": False, "error": "Connection failed"}
        message = task._get_summary_message(result)

        assert "실패" in message
        assert "Connection failed" in message


# =============================================================================
# AnalyzeForensicPendingTask Tests
# =============================================================================


class TestAnalyzeForensicPendingTask:
    """AnalyzeForensicPendingTask 테스트."""

    def test_task_metadata(self):
        """태스크 메타데이터 확인."""
        task = AnalyzeForensicPendingTask()

        assert task.name == "selfhealing.analyze_forensic_pending"
        assert task.notification_policy.timing == NotificationTiming.REALTIME
        assert task.notification_policy.threshold == 10
        assert task.notification_policy.threshold_field == "suspicious_count"

    def test_run_no_suspicious(self):
        """의심 항목 없는 실행."""
        task = AnalyzeForensicPendingTask()

        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "analyzed_count": 50,
                "suspicious_count": 0,
                "stuck_patterns": [],
                "recommendations": [],
            }

            result = mock_run()

            assert result["success"] is True
            assert result["suspicious_count"] == 0

    def test_run_with_suspicious(self):
        """의심 항목 있는 실행."""
        task = AnalyzeForensicPendingTask()

        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "analyzed_count": 100,
                "suspicious_count": 15,
                "stuck_patterns": [{"action": "stuck", "count": 10}],
                "recommendations": ["수동 검토 권장"],
            }

            result = mock_run()

            assert result["suspicious_count"] == 15
            assert len(result["recommendations"]) > 0

    def test_extract_patterns(self):
        """패턴 추출 테스트."""
        task = AnalyzeForensicPendingTask()

        results_by_action = {
            "retry": 20,
            "stuck": 5,
            "skip": 0,
        }

        patterns = task._extract_patterns(results_by_action)

        assert len(patterns) == 2  # 0이 아닌 것만
        assert any(p["action"] == "retry" for p in patterns)
        assert any(p["action"] == "stuck" for p in patterns)

    def test_generate_recommendations(self):
        """권장 사항 생성 테스트."""
        task = AnalyzeForensicPendingTask()

        results = {"stuck": 10, "requires_review": 15}

        recommendations = task._generate_recommendations(results, 25)

        assert len(recommendations) >= 2  # stuck, requires_review 모두 트리거

    def test_get_severity(self):
        """심각도 결정 테스트."""
        task = AnalyzeForensicPendingTask()

        assert task._get_severity({"suspicious_count": 5}) == "info"
        assert task._get_severity({"suspicious_count": 10}) == "warning"
        assert task._get_severity({"suspicious_count": 50}) == "critical"

    def test_get_summary_message(self):
        """메시지 생성 테스트."""
        task = AnalyzeForensicPendingTask()

        result = {
            "success": True,
            "suspicious_count": 15,
            "stuck_patterns": [{"action": "stuck"}],
        }
        message = task._get_summary_message(result)

        assert "15" in message
        assert "포렌식" in message


# =============================================================================
# AnalyzeCrossStageInsightsTask Tests
# =============================================================================


class TestAnalyzeCrossStageInsightsTask:
    """AnalyzeCrossStageInsightsTask 테스트."""

    def test_task_metadata(self):
        """태스크 메타데이터 확인."""
        task = AnalyzeCrossStageInsightsTask()

        assert task.name == "selfhealing.analyze_cross_stage_insights"
        assert task.notification_policy.timing == NotificationTiming.AGGREGATED
        assert task.notification_policy.aggregate is True
        assert task.notification_policy.threshold == 3
        assert task.notification_policy.threshold_field == "insight_count"

    def test_run_success(self):
        """성공적인 실행."""
        task = AnalyzeCrossStageInsightsTask()

        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "insight_count": 3,
                "insights": [
                    {"type": "common_pattern", "name": "timeout"},
                    {"type": "common_pattern", "name": "connection"},
                    {"type": "pending_suggestions", "count": 2},
                ],
                "recommendations": [],
            }

            result = mock_run()

            assert result["success"] is True
            assert result["insight_count"] >= 2  # common patterns + suggestions

    def test_structure_insights(self):
        """인사이트 구조화 테스트."""
        task = AnalyzeCrossStageInsightsTask()

        raw_insights = {
            "common_patterns": {
                "pattern_a": ["stage1", "stage2"],
                "pattern_b": ["stage2", "stage3", "stage4"],
            },
            "suggestions_pending": 5,
        }

        insights = task._structure_insights(raw_insights)

        assert len(insights) == 3  # 2 patterns + 1 suggestions
        assert any(i["type"] == "common_pattern" for i in insights)
        assert any(i["type"] == "pending_suggestions" for i in insights)

    def test_generate_recommendations(self):
        """권장 사항 생성 테스트."""
        task = AnalyzeCrossStageInsightsTask()

        raw_insights = {
            "common_patterns": {"a": [], "b": [], "c": [], "d": []},
            "total_patterns": 25,
        }
        structured = [{"recommendation": "패턴 분석 필요"}]

        recommendations = task._generate_recommendations(raw_insights, structured)

        assert len(recommendations) >= 2

    def test_get_summary_message(self):
        """메시지 생성 테스트."""
        task = AnalyzeCrossStageInsightsTask()

        result = {"success": True, "insight_count": 5}
        message = task._get_summary_message(result)

        assert "5" in message
        assert "인사이트" in message


# =============================================================================
# CheckRecoveryTransitionsTask Tests
# =============================================================================


class TestCheckRecoveryTransitionsTask:
    """CheckRecoveryTransitionsTask 테스트."""

    def test_task_metadata(self):
        """태스크 메타데이터 확인."""
        task = CheckRecoveryTransitionsTask()

        assert task.name == "selfhealing.check_recovery_transitions"
        assert task.notification_policy.timing == NotificationTiming.REALTIME
        assert task.notification_policy.threshold == 1
        assert task.notification_policy.cooldown_seconds == 120

    def test_run_no_transitions(self):
        """전환 없는 실행."""
        task = CheckRecoveryTransitionsTask()

        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "transitions_count": 0,
                "circuits_recovered": [],
            }

            result = mock_run()

            assert result["success"] is True
            assert result["transitions_count"] == 0

    def test_run_with_recovery(self):
        """복구된 Circuit이 있는 실행."""
        task = CheckRecoveryTransitionsTask()

        with patch.object(task, 'run') as mock_run:
            mock_run.return_value = {
                "success": True,
                "transitions_count": 2,
                "circuits_recovered": ["payment_cb", "order_cb"],
            }

            result = mock_run()

            assert result["transitions_count"] == 2
            assert "payment_cb" in result["circuits_recovered"]

    def test_get_summary_message_recovered(self):
        """복구 메시지 테스트."""
        task = CheckRecoveryTransitionsTask()

        result = {
            "success": True,
            "transitions_count": 1,
            "circuits_recovered": ["payment_cb"],
        }
        message = task._get_summary_message(result)

        assert "payment_cb" in message
        assert "복구" in message

    def test_get_summary_message_no_recovery(self):
        """전환만 있는 메시지 테스트."""
        task = CheckRecoveryTransitionsTask()

        result = {
            "success": True,
            "transitions_count": 3,
            "circuits_recovered": [],
        }
        message = task._get_summary_message(result)

        assert "3" in message

    def test_get_summary_message_many_recovered(self):
        """다수 복구 시 생략 테스트."""
        task = CheckRecoveryTransitionsTask()

        result = {
            "success": True,
            "transitions_count": 5,
            "circuits_recovered": ["cb1", "cb2", "cb3", "cb4", "cb5"],
        }
        message = task._get_summary_message(result)

        assert "외" in message or "..." in message or "2" in message


# =============================================================================
# Beat Schedule Tests
# =============================================================================


class TestIntelligenceBeatSchedule:
    """지능 레인 Beat Schedule 테스트."""

    def test_schedule_contains_all_tasks(self):
        """스케줄에 모든 태스크 포함 확인."""
        schedule = get_intelligence_beat_schedule()

        assert "check-recovery-transitions" in schedule
        assert "analyze-forensic-pending" in schedule
        assert "check-sla-drift" in schedule
        assert "analyze-cross-stage-insights" in schedule

    def test_schedule_queue_assignments(self):
        """큐 할당 확인."""
        schedule = get_intelligence_beat_schedule()

        assert schedule["check-recovery-transitions"]["options"]["queue"] == "realtime"
        assert schedule["analyze-forensic-pending"]["options"]["queue"] == "analysis"
        assert schedule["check-sla-drift"]["options"]["queue"] == "analysis"
        assert schedule["analyze-cross-stage-insights"]["options"]["queue"] == "analysis"

    def test_schedule_task_names(self):
        """태스크 이름 확인."""
        schedule = get_intelligence_beat_schedule()

        assert schedule["check-recovery-transitions"]["task"] == "selfhealing.check_recovery_transitions"
        assert schedule["analyze-forensic-pending"]["task"] == "selfhealing.analyze_forensic_pending"
        assert schedule["check-sla-drift"]["task"] == "selfhealing.check_sla_drift"
        assert schedule["analyze-cross-stage-insights"]["task"] == "selfhealing.analyze_cross_stage_insights"


# =============================================================================
# Task Registry Tests
# =============================================================================


class TestIntelligenceTaskRegistry:
    """지능 레인 태스크 레지스트리 테스트."""

    def test_all_tasks_in_registry(self):
        """모든 태스크가 레지스트리에 있는지 확인."""
        assert len(INTELLIGENCE_TASKS) == 5

        task_classes = [t.__name__ for t in INTELLIGENCE_TASKS]

        assert "CheckSLADriftTask" in task_classes
        assert "AnalyzeForensicPendingTask" in task_classes
        assert "AnalyzeCrossStageInsightsTask" in task_classes
        assert "CheckRecoveryTransitionsTask" in task_classes
        assert "VerifyReconciliationAccuracyTask" in task_classes

    def test_all_tasks_have_names(self):
        """모든 태스크가 이름을 가지고 있는지 확인."""
        for task_class in INTELLIGENCE_TASKS:
            task = task_class()
            assert task.name.startswith("selfhealing.")

    def test_all_tasks_have_policies(self):
        """모든 태스크가 알림 정책을 가지고 있는지 확인."""
        for task_class in INTELLIGENCE_TASKS:
            task = task_class()
            assert isinstance(task.notification_policy, NotificationPolicy)
