"""
Tests for Recovery Celery Beat Schedule Registration.

Phase 4.5: celery.py beat_schedule 등록 테스트.

테스트 대상:
- check_recovery_trigger 스케줄 등록
- monitor_recovery_health 스케줄 등록
- check_stale_pending_recoveries 스케줄 등록
- cleanup_old_recovery_sessions 스케줄 등록

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#10.2.4.5
"""

import pytest


class TestRecoveryCeleryBeatSchedule:
    """Recovery Celery Beat 스케줄 등록 테스트."""

    @pytest.fixture
    def beat_schedule(self):
        """Celery app의 beat_schedule 설정 로드."""
        # Django/Celery 설정 없이 celery.py의 beat_schedule만 테스트
        # 실제 app은 로드하지 않고 파일 내용을 검증
        import os

        celery_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "..",
            "..",
            "myproject",
            "celery.py",
        )
        celery_path = os.path.abspath(celery_path)

        with open(celery_path, "r", encoding="utf-8") as f:
            content = f.read()

        return content

    def test_check_recovery_trigger_schedule_registered(self, beat_schedule):
        """check_recovery_trigger 스케줄 등록 확인."""
        assert '"check-recovery-trigger"' in beat_schedule
        assert '"selfhealing.check_recovery_trigger"' in beat_schedule

    def test_monitor_recovery_health_schedule_registered(self, beat_schedule):
        """monitor_recovery_health 스케줄 등록 확인."""
        assert '"monitor-recovery-health"' in beat_schedule
        assert '"selfhealing.monitor_recovery_health"' in beat_schedule

    def test_check_stale_pending_recoveries_schedule_registered(self, beat_schedule):
        """check_stale_pending_recoveries 스케줄 등록 확인."""
        assert '"check-stale-pending-recoveries"' in beat_schedule
        assert '"selfhealing.check_stale_pending_recoveries"' in beat_schedule

    def test_cleanup_old_recovery_sessions_schedule_registered(self, beat_schedule):
        """cleanup_old_recovery_sessions 스케줄 등록 확인."""
        assert '"cleanup-old-recovery-sessions"' in beat_schedule
        assert '"selfhealing.cleanup_old_recovery_sessions"' in beat_schedule

    def test_recovery_tasks_use_critical_queue(self, beat_schedule):
        """복구 태스크가 critical 큐를 사용하는지 확인."""
        # check_recovery_trigger, monitor_recovery_health, check_stale_pending_recoveries
        # 모두 selfhealing.critical 큐 사용
        assert 'queue": "selfhealing.critical"' in beat_schedule

    def test_schedule_intervals_are_appropriate(self, beat_schedule):
        """스케줄 간격이 적절한지 확인."""
        # check_recovery_trigger: 60초 (매분)
        assert '"schedule": 60.0' in beat_schedule

        # monitor_recovery_health: 30초
        assert '"schedule": 30.0' in beat_schedule

        # check_stale_pending_recoveries: 600초 (10분)
        assert '"schedule": 600.0' in beat_schedule
