"""
Celery Beat Schedule에 Governance 태스크 등록 검증 테스트.

get_selfhealing_beat_schedule()에 거버넌스 태스크(긴급 모드 만료 체크)가
올바르게 포함되는지 검증합니다.

Django 설정이 필요하므로 전역 tests 폴더에 위치합니다.

Reference:
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
    - selfhealing/tasks/governance.py
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.django_db


class TestGovernanceTaskInBeatSchedule:
    """거버넌스 태스크가 Celery Beat 스케줄에 포함되는지 테스트."""

    def test_governance_schedule_is_included_by_default(self):
        """기본 설정에서 거버넌스 스케줄이 포함된다."""
        from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule

        schedule = get_selfhealing_beat_schedule()

        # 거버넌스 태스크가 포함되어 있어야 함
        governance_tasks = [
            key for key in schedule.keys()
            if "emergency" in key.lower() or "governance" in key.lower()
        ]
        assert len(governance_tasks) > 0, \
            "Governance tasks should be included in beat schedule by default"

    def test_check_emergency_mode_expiry_task_is_registered(self):
        """check_emergency_mode_expiry 태스크가 Beat 스케줄에 등록되어 있다."""
        from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule

        schedule = get_selfhealing_beat_schedule()

        # check_emergency_mode_expiry 태스크 찾기
        emergency_expiry_task = None
        for key, config in schedule.items():
            task_name = config.get("task", "")
            if "check_emergency_mode_expiry" in task_name:
                emergency_expiry_task = config
                break

        assert emergency_expiry_task is not None, \
            "check_emergency_mode_expiry task should be registered in beat schedule"
        assert "schedule" in emergency_expiry_task, \
            "Task should have a schedule configuration"

    def test_governance_schedule_can_be_excluded_with_flag(self):
        """include_governance=False로 거버넌스 스케줄을 제외할 수 있다."""
        from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule

        schedule = get_selfhealing_beat_schedule(include_governance=False)

        # 거버넌스 태스크가 없어야 함
        for key, config in schedule.items():
            task_name = config.get("task", "")
            assert "check_emergency_mode_expiry" not in task_name, \
                f"Governance task should be excluded: {key}"

    def test_get_selfhealing_beat_schedule_has_governance_parameter(self):
        """get_selfhealing_beat_schedule에 include_governance 파라미터가 존재한다."""
        import inspect
        from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule

        sig = inspect.signature(get_selfhealing_beat_schedule)
        param_names = list(sig.parameters.keys())

        assert "include_governance" in param_names, \
            "get_selfhealing_beat_schedule should have include_governance parameter"


class TestGovernanceBeatScheduleConfiguration:
    """거버넌스 Beat 스케줄 설정 상세 테스트."""

    def test_emergency_expiry_task_has_15_minute_interval(self):
        """긴급 모드 만료 체크 태스크가 15분 간격으로 설정되어 있다."""
        from selfhealing.tasks.governance import get_governance_beat_schedule

        schedule = get_governance_beat_schedule()

        # check-emergency-mode-expiry 찾기
        expiry_task = schedule.get("check-emergency-mode-expiry")
        assert expiry_task is not None, \
            "check-emergency-mode-expiry task should exist in governance schedule"

        task_schedule = expiry_task.get("schedule")
        assert task_schedule is not None, "Task should have schedule"

        # crontab 또는 timedelta 형태일 수 있음
        if hasattr(task_schedule, "run_every"):
            # timedelta schedule
            assert task_schedule.run_every.total_seconds() == 900, \
                "Should run every 15 minutes (900 seconds)"
        elif hasattr(task_schedule, "minute"):
            # crontab schedule - */15
            minute_str = str(task_schedule.minute)
            assert "15" in minute_str or task_schedule.minute == "*/15", \
                f"Crontab should run every 15 minutes, got: {minute_str}"

    def test_governance_beat_schedule_returns_valid_dict(self):
        """get_governance_beat_schedule()이 유효한 dict를 반환한다."""
        from selfhealing.tasks.governance import get_governance_beat_schedule

        schedule = get_governance_beat_schedule()

        assert isinstance(schedule, dict)
        assert len(schedule) > 0, "Should return at least one task"

        for task_name, config in schedule.items():
            assert isinstance(task_name, str)
            assert isinstance(config, dict)
            assert "task" in config, f"{task_name}: 'task' field is required"
            assert "schedule" in config, f"{task_name}: 'schedule' field is required"


class TestBeatScheduleIntegrity:
    """전체 Beat 스케줄 무결성 테스트."""

    def test_validate_schedule_passes_with_governance(self):
        """validate_schedule()이 거버넌스 태스크 포함 상태에서 통과한다."""
        from selfhealing.adapters.celery.beat_schedule import validate_schedule

        result = validate_schedule()

        assert result["valid"] is True, \
            f"Schedule should be valid. Errors: {result.get('errors', [])}"
        assert result["task_count"] > 0

    def test_all_tasks_have_required_fields(self):
        """모든 Beat 스케줄 태스크가 필수 필드를 가진다."""
        from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule

        schedule = get_selfhealing_beat_schedule()

        for task_name, config in schedule.items():
            assert "task" in config, f"{task_name}: missing 'task' field"
            assert "schedule" in config, f"{task_name}: missing 'schedule' field"
