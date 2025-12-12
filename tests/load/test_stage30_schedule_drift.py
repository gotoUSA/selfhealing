"""
Stage 30: Schedule Drift Test - Unit Tests

이 파일은 Stage 30 시나리오의 핵심 로직을 검증합니다.
"""

import pytest
import time
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
import uuid

import sys
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from load_tests.scenarios.stage30_schedule_drift import (
    ScheduleDriftConfig,
    ScheduledJob,
    JobStatus,
    JobPriority,
    MockCeleryBeat,
    ScheduleDriftStats,
    generate_scheduled_jobs,
    generate_dependent_jobs,
    inject_drift,
    trigger_worker_restart,
    add_jobs,
    reset_scheduler,
)


class TestScheduleDriftConfig:
    """ScheduleDriftConfig 테스트"""

    def test_default_config(self):
        """기본 설정 테스트"""
        config = ScheduleDriftConfig()

        assert config.drift_minutes == 10
        assert config.max_concurrent_jobs == 5
        assert config.job_timeout_seconds == 60
        assert config.backlog_throttle_tps == 10
        assert config.skip_stale_jobs_after_minutes == 30

    def test_custom_config(self):
        """커스텀 설정 테스트"""
        config = ScheduleDriftConfig(
            drift_minutes=20,
            max_concurrent_jobs=10,
        )

        assert config.drift_minutes == 20
        assert config.max_concurrent_jobs == 10


class TestScheduledJob:
    """ScheduledJob 테스트"""

    def test_create_job(self):
        """작업 생성 테스트"""
        now = datetime.now(timezone.utc)
        job = ScheduledJob(
            id="test-123",
            name="test_job",
            scheduled_time=now,
        )

        assert job.id == "test-123"
        assert job.name == "test_job"
        assert job.status == JobStatus.PENDING
        assert job.priority == JobPriority.MEDIUM
        assert job.can_run() is True

    def test_job_priority(self):
        """작업 우선순위 테스트"""
        now = datetime.now(timezone.utc)

        high_job = ScheduledJob(id="1", name="high", scheduled_time=now, priority=JobPriority.HIGH)
        low_job = ScheduledJob(id="2", name="low", scheduled_time=now, priority=JobPriority.LOW)

        # HIGH < LOW (숫자가 작을수록 우선순위 높음)
        assert high_job < low_job

    def test_job_is_stale(self):
        """Stale 작업 테스트"""
        # 40분 전에 스케줄된 작업
        past_time = datetime.now(timezone.utc) - timedelta(minutes=40)
        job = ScheduledJob(id="1", name="old_job", scheduled_time=past_time)

        assert job.is_stale(30) is True
        assert job.is_stale(60) is False

    def test_job_can_run(self):
        """실행 가능 여부 테스트"""
        now = datetime.now(timezone.utc)
        job = ScheduledJob(id="1", name="test", scheduled_time=now)

        assert job.can_run() is True

        job.status = JobStatus.RUNNING
        assert job.can_run() is False

        job.status = JobStatus.COMPLETED
        assert job.can_run() is False

    def test_job_dependencies(self):
        """작업 의존성 테스트"""
        now = datetime.now(timezone.utc)
        job = ScheduledJob(id="1", name="child", scheduled_time=now, dependencies=["parent_1", "parent_2"])

        assert len(job.dependencies) == 2
        assert "parent_1" in job.dependencies


class TestMockCeleryBeat:
    """MockCeleryBeat 테스트"""

    @pytest.fixture
    def scheduler(self):
        """새로운 스케줄러 인스턴스"""
        config = ScheduleDriftConfig(
            max_concurrent_jobs=3,
            skip_stale_jobs_after_minutes=30,
        )
        return MockCeleryBeat(config)

    def test_initial_state(self, scheduler):
        """초기 상태 테스트"""
        assert len(scheduler.jobs) == 0
        assert scheduler.is_drifting is False
        assert scheduler.worker_running is True
        assert scheduler.current_concurrent == 0

    def test_schedule_job(self, scheduler):
        """작업 스케줄 테스트"""
        now = datetime.now(timezone.utc)
        job = ScheduledJob(id="1", name="test", scheduled_time=now)

        scheduler.schedule_job(job)

        assert len(scheduler.jobs) == 1
        assert "1" in scheduler.jobs

    def test_schedule_multiple_jobs(self, scheduler):
        """여러 작업 스케줄 테스트"""
        jobs = generate_scheduled_jobs(10)
        scheduler.schedule_jobs(jobs)

        assert len(scheduler.jobs) == 10

    def test_inject_drift(self, scheduler):
        """드리프트 주입 테스트"""
        scheduler.inject_drift(15)

        assert scheduler.is_drifting is True
        assert scheduler.drift_minutes == 15

    def test_recover_drift(self, scheduler):
        """드리프트 복구 테스트"""
        scheduler.inject_drift(10)
        scheduler.recover_drift()

        assert scheduler.is_drifting is False
        assert scheduler.drift_minutes == 0

    def test_trigger_worker_restart(self, scheduler):
        """Worker 재시작 트리거 테스트"""
        # 먼저 작업 실행 중 상태로 만들기
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        job = ScheduledJob(id="1", name="test", scheduled_time=now)
        scheduler.schedule_job(job)

        runnable = scheduler.get_runnable_jobs(max_count=1)
        if runnable:
            scheduler.start_job(runnable[0])

        # Worker 재시작
        scheduler.trigger_worker_restart()

        assert scheduler.worker_running is False
        assert scheduler.current_concurrent == 0
        assert len(scheduler.running_jobs) == 0

    def test_complete_worker_restart(self, scheduler):
        """Worker 재시작 완료 테스트"""
        scheduler.trigger_worker_restart()
        time.sleep(0.1)
        restart_time = scheduler.complete_worker_restart()

        assert scheduler.worker_running is True
        assert restart_time > 0

    def test_get_runnable_jobs(self, scheduler):
        """실행 가능한 작업 가져오기 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        jobs = [ScheduledJob(id=str(i), name=f"job_{i}", scheduled_time=now) for i in range(5)]
        scheduler.schedule_jobs(jobs)

        runnable = scheduler.get_runnable_jobs(max_count=3)

        assert len(runnable) == 3

    def test_get_runnable_jobs_when_worker_down(self, scheduler):
        """Worker 다운 시 실행 가능 작업 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        job = ScheduledJob(id="1", name="test", scheduled_time=now)
        scheduler.schedule_job(job)

        scheduler.trigger_worker_restart()

        runnable = scheduler.get_runnable_jobs()

        assert len(runnable) == 0

    def test_start_job(self, scheduler):
        """작업 시작 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        job = ScheduledJob(id="1", name="test", scheduled_time=now)
        scheduler.schedule_job(job)

        runnable = scheduler.get_runnable_jobs()
        result = scheduler.start_job(runnable[0])

        assert result is True
        assert scheduler.current_concurrent == 1
        assert "1" in scheduler.running_jobs

    def test_start_job_concurrent_limit(self, scheduler):
        """동시 실행 제한 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        jobs = [ScheduledJob(id=str(i), name=f"job_{i}", scheduled_time=now) for i in range(5)]
        scheduler.schedule_jobs(jobs)

        runnable = scheduler.get_runnable_jobs(max_count=5)

        started = 0
        for job in runnable:
            if scheduler.start_job(job):
                started += 1

        # max_concurrent_jobs = 3
        assert started == 3
        assert scheduler.current_concurrent == 3

    def test_complete_job_success(self, scheduler):
        """작업 성공 완료 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        job = ScheduledJob(id="1", name="test", scheduled_time=now)
        scheduler.schedule_job(job)

        runnable = scheduler.get_runnable_jobs()
        scheduler.start_job(runnable[0])
        scheduler.complete_job("1", success=True)

        assert scheduler.current_concurrent == 0
        assert "1" not in scheduler.running_jobs
        assert "1" in scheduler.executed_job_ids

    def test_complete_job_failure_with_retry(self, scheduler):
        """작업 실패 + 재시도 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        job = ScheduledJob(id="1", name="test", scheduled_time=now, max_retries=2)
        scheduler.schedule_job(job)

        runnable = scheduler.get_runnable_jobs()
        scheduler.start_job(runnable[0])
        scheduler.complete_job("1", success=False, error="Test error")

        # 재시도 가능하므로 pending 큐로 돌아감
        assert len(scheduler.pending_queue) == 1

    def test_duplicate_prevention(self, scheduler):
        """중복 실행 방지 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        job = ScheduledJob(id="1", name="test", scheduled_time=now)
        scheduler.schedule_job(job)

        # 첫 번째 실행
        runnable = scheduler.get_runnable_jobs()
        scheduler.start_job(runnable[0])
        scheduler.complete_job("1", success=True)

        # 같은 작업 다시 스케줄 시도
        scheduler.schedule_job(job)

        # 이미 실행됨
        runnable = scheduler.get_runnable_jobs()
        assert len(runnable) == 0

    def test_stale_job_skip(self, scheduler):
        """Stale 작업 스킵 테스트"""
        # 40분 전 작업 (30분 이상 → stale)
        past_time = datetime.now(timezone.utc) - timedelta(minutes=40)
        job = ScheduledJob(id="1", name="stale_job", scheduled_time=past_time)
        scheduler.schedule_job(job)

        runnable = scheduler.get_runnable_jobs()

        # Stale 작업은 스킵됨
        assert len(runnable) == 0
        assert scheduler.stale_skipped == 1

    def test_priority_ordering(self, scheduler):
        """우선순위 순서 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)

        low_job = ScheduledJob(id="1", name="low", scheduled_time=now, priority=JobPriority.LOW)
        high_job = ScheduledJob(id="2", name="high", scheduled_time=now, priority=JobPriority.HIGH)
        medium_job = ScheduledJob(id="3", name="medium", scheduled_time=now, priority=JobPriority.MEDIUM)

        scheduler.schedule_jobs([low_job, high_job, medium_job])

        runnable = scheduler.get_runnable_jobs(max_count=3)

        # HIGH가 먼저
        assert runnable[0].priority == JobPriority.HIGH

    def test_get_stats(self, scheduler):
        """통계 반환 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        jobs = generate_scheduled_jobs(10)
        scheduler.schedule_jobs(jobs)

        stats = scheduler.get_stats()

        assert stats["pending_count"] == 10
        assert stats["running_count"] == 0
        assert stats["completed_count"] == 0
        assert stats["is_drifting"] is False


class TestJobDependencies:
    """작업 의존성 테스트"""

    @pytest.fixture
    def scheduler(self):
        config = ScheduleDriftConfig(max_concurrent_jobs=5)
        return MockCeleryBeat(config)

    def test_dependent_jobs_wait_for_parent(self, scheduler):
        """의존성 있는 작업이 부모 대기 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)

        parent = ScheduledJob(id="parent", name="parent", scheduled_time=now)
        child = ScheduledJob(id="child", name="child", scheduled_time=now, dependencies=["parent"])

        scheduler.schedule_jobs([parent, child])

        # 부모가 완료되지 않았으므로 자식은 실행 불가
        runnable = scheduler.get_runnable_jobs()
        assert len(runnable) == 1
        assert runnable[0].id == "parent"

    def test_dependent_jobs_run_after_parent_complete(self, scheduler):
        """부모 완료 후 자식 실행 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)

        parent = ScheduledJob(id="parent", name="parent", scheduled_time=now)
        child = ScheduledJob(id="child", name="child", scheduled_time=now, dependencies=["parent"])

        scheduler.schedule_jobs([parent, child])

        # 부모 실행 완료
        runnable = scheduler.get_runnable_jobs()
        scheduler.start_job(runnable[0])
        scheduler.complete_job("parent", success=True)

        # 이제 자식 실행 가능
        runnable = scheduler.get_runnable_jobs()
        assert len(runnable) == 1
        assert runnable[0].id == "child"


class TestConcurrency:
    """동시성 테스트"""

    @pytest.fixture
    def scheduler(self):
        config = ScheduleDriftConfig(max_concurrent_jobs=5)
        return MockCeleryBeat(config)

    def test_concurrent_job_scheduling(self, scheduler):
        """동시 작업 스케줄 테스트"""
        threads = []

        def schedule_jobs():
            jobs = generate_scheduled_jobs(10)
            scheduler.schedule_jobs(jobs)

        for _ in range(5):
            t = threading.Thread(target=schedule_jobs)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(scheduler.jobs) == 50

    def test_concurrent_job_execution(self, scheduler):
        """동시 작업 실행 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        jobs = [ScheduledJob(id=str(i), name=f"job_{i}", scheduled_time=now) for i in range(20)]
        scheduler.schedule_jobs(jobs)

        threads = []
        executed = []

        def execute_jobs():
            for _ in range(4):
                runnable = scheduler.get_runnable_jobs(max_count=1)
                for job in runnable:
                    if scheduler.start_job(job):
                        time.sleep(0.01)
                        scheduler.complete_job(job.id, success=True)
                        executed.append(job.id)

        for _ in range(5):
            t = threading.Thread(target=execute_jobs)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # 최대 동시 실행 수 확인
        max_peak = scheduler.get_stats()["max_concurrent_peak"]
        assert max_peak <= 5


class TestJobGeneration:
    """작업 생성 테스트"""

    def test_generate_scheduled_jobs(self):
        """스케줄된 작업 생성 테스트"""
        jobs = generate_scheduled_jobs(10)

        assert len(jobs) == 10
        assert all(isinstance(j, ScheduledJob) for j in jobs)

    def test_generate_dependent_jobs(self):
        """의존성 있는 작업 생성 테스트"""
        jobs = generate_dependent_jobs(5)

        assert len(jobs) == 5
        # 첫 번째는 의존성 없음
        assert len(jobs[0].dependencies) == 0
        # 나머지는 첫 번째에 의존
        assert all(jobs[0].id in j.dependencies for j in jobs[1:])


class TestValidationCriteria:
    """검증 기준 테스트"""

    @pytest.fixture
    def scheduler(self):
        config = ScheduleDriftConfig(max_concurrent_jobs=5)
        return MockCeleryBeat(config)

    def test_no_duplicate_execution(self, scheduler):
        """중복 실행 0건 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        job = ScheduledJob(id="1", name="test", scheduled_time=now)
        scheduler.schedule_job(job)

        # 첫 번째 실행
        runnable = scheduler.get_runnable_jobs()
        scheduler.start_job(runnable[0])
        scheduler.complete_job("1", success=True)

        # 같은 ID로 다시 시작 시도
        job2 = ScheduledJob(id="1", name="test", scheduled_time=now)
        scheduler.schedule_job(job2)

        result = scheduler.start_job(job2)

        # 중복 방지됨
        assert result is False
        assert scheduler.duplicate_prevented >= 1

    def test_max_concurrent_limit(self, scheduler):
        """최대 동시 실행 ≤ 5 테스트"""
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        jobs = [ScheduledJob(id=str(i), name=f"job_{i}", scheduled_time=now) for i in range(10)]
        scheduler.schedule_jobs(jobs)

        runnable = scheduler.get_runnable_jobs(max_count=10)

        started = 0
        for job in runnable:
            if scheduler.start_job(job):
                started += 1

        assert started <= 5
        assert scheduler.current_concurrent <= 5

    def test_worker_restart_under_30_seconds(self, scheduler):
        """Worker 재시작 복구 < 30초 테스트"""
        scheduler.trigger_worker_restart()
        time.sleep(0.5)  # 짧은 대기
        restart_time = scheduler.complete_worker_restart()

        assert restart_time < 30

    def test_stale_jobs_skipped(self, scheduler):
        """Stale 작업 스킵 테스트"""
        # 40분 전 작업들
        past_time = datetime.now(timezone.utc) - timedelta(minutes=40)
        stale_jobs = [ScheduledJob(id=str(i), name=f"stale_{i}", scheduled_time=past_time) for i in range(5)]
        scheduler.schedule_jobs(stale_jobs)

        # 모두 스킵됨
        runnable = scheduler.get_runnable_jobs()

        assert len(runnable) == 0
        assert scheduler.stale_skipped == 5


class TestExternalControls:
    """외부 제어 함수 테스트"""

    def test_inject_drift(self):
        """드리프트 주입 테스트"""
        reset_scheduler()
        from load_tests.scenarios.stage30_schedule_drift import _celery_beat

        inject_drift(15)

        assert _celery_beat.is_drifting is True
        assert _celery_beat.drift_minutes == 15

    def test_trigger_worker_restart(self):
        """Worker 재시작 트리거 테스트"""
        reset_scheduler()
        from load_tests.scenarios.stage30_schedule_drift import _celery_beat

        trigger_worker_restart()

        assert _celery_beat.worker_running is False

    def test_add_jobs(self):
        """작업 추가 테스트"""
        reset_scheduler()
        from load_tests.scenarios.stage30_schedule_drift import _celery_beat

        initial_count = len(_celery_beat.jobs)
        add_jobs(50)

        assert len(_celery_beat.jobs) == initial_count + 50

    def test_reset_scheduler(self):
        """스케줄러 초기화 테스트"""
        from load_tests.scenarios.stage30_schedule_drift import _celery_beat

        add_jobs(100)
        reset_scheduler()

        from load_tests.scenarios.stage30_schedule_drift import _celery_beat as new_scheduler

        assert len(new_scheduler.jobs) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
