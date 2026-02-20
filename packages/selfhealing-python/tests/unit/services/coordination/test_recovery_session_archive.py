"""
Recovery Session Archive 단위 테스트.

Phase 1.4: RecoverySessionArchive 테스트

테스트 항목:
- RecoveryStepArchiveData 생성 및 변환
- RecoverySessionArchiveData 생성 및 변환
- RecoverySessionArchiveService CRUD
- Resume 기능
- 통계 집계
"""

import pytest
from datetime import datetime, timezone, timedelta

from selfhealing.services.coordination.recovery_session_archive import (
    RecoveryStepArchiveData,
    RecoverySessionArchiveData,
    RecoverySessionArchiveService,
    get_recovery_session_archive_service,
    reset_recovery_session_archive_service,
)
from selfhealing.services.coordination.recovery_state import (
    RecoverySession,
    RecoveryStep,
    RecoveryStepType,
)
from selfhealing.services.coordination.enums import RecoveryStatus


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_step():
    """샘플 RecoveryStep 생성."""
    return RecoveryStep(
        step_type=RecoveryStepType.BUDGET_RESET,
        order=1,
        status=RecoveryStatus.COMPLETED,
        wait_after_seconds=0,
        params={"target_multiplier": 1.0},
        started_at="2026-01-23T10:00:00+00:00",
        completed_at="2026-01-23T10:00:05+00:00",
    )


@pytest.fixture
def sample_session():
    """샘플 RecoverySession 생성."""
    steps = [
        RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            status=RecoveryStatus.COMPLETED,
            params={"target_multiplier": 1.0},
            started_at="2026-01-23T10:00:00+00:00",
            completed_at="2026-01-23T10:00:05+00:00",
        ),
        RecoveryStep(
            step_type=RecoveryStepType.HEALTH_CHECK,
            order=2,
            status=RecoveryStatus.COMPLETED,
            params={"duration_minutes": 5},
            started_at="2026-01-23T10:00:05+00:00",
            completed_at="2026-01-23T10:05:05+00:00",
        ),
    ]

    return RecoverySession(
        id="recovery-test123",
        namespace="global",
        trigger_level="LEVEL_3",
        status=RecoveryStatus.COMPLETED,
        steps=steps,
        current_step_index=2,
        started_at="2026-01-23T10:00:00+00:00",
        completed_at="2026-01-23T10:05:05+00:00",
        initiated_by="system",
    )


@pytest.fixture
def archive_service():
    """인메모리 아카이브 서비스."""
    reset_recovery_session_archive_service()
    return RecoverySessionArchiveService(use_django=False)


# =============================================================================
# RecoveryStepArchiveData Tests
# =============================================================================


class TestRecoveryStepArchiveData:
    """RecoveryStepArchiveData 테스트."""

    def test_from_step_basic(self, sample_step):
        """기본 변환 테스트."""
        archive = RecoveryStepArchiveData.from_step(sample_step)

        assert archive.step_type == "budget_reset"
        assert archive.order == 1
        assert archive.status == "completed"
        assert archive.params == {"target_multiplier": 1.0}

    def test_from_step_execution_time(self, sample_step):
        """실행 시간 계산 테스트."""
        archive = RecoveryStepArchiveData.from_step(sample_step)

        # 5초 = 5000ms
        assert archive.execution_time_ms == 5000

    def test_to_dict_and_from_dict(self):
        """딕셔너리 변환 왕복 테스트."""
        original = RecoveryStepArchiveData(
            step_type="health_check",
            order=2,
            status="completed",
            wait_after_seconds=60,
            params={"duration_minutes": 5},
            started_at="2026-01-23T10:00:00+00:00",
            completed_at="2026-01-23T10:05:00+00:00",
            execution_time_ms=300000,
            retry_count=1,
        )

        data = original.to_dict()
        restored = RecoveryStepArchiveData.from_dict(data)

        assert restored.step_type == original.step_type
        assert restored.order == original.order
        assert restored.execution_time_ms == original.execution_time_ms
        assert restored.retry_count == original.retry_count


# =============================================================================
# RecoverySessionArchiveData Tests
# =============================================================================


class TestRecoverySessionArchiveData:
    """RecoverySessionArchiveData 테스트."""

    def test_from_session_basic(self, sample_session):
        """기본 변환 테스트."""
        archive = RecoverySessionArchiveData.from_session(sample_session)

        assert archive.session_id == "recovery-test123"
        assert archive.namespace == "global"
        assert archive.trigger_level == "LEVEL_3"
        assert archive.status == "completed"
        assert len(archive.steps) == 2

    def test_from_session_duration(self, sample_session):
        """총 소요 시간 계산 테스트."""
        archive = RecoverySessionArchiveData.from_session(sample_session)

        # 10:00:00 -> 10:05:05 = 305초
        assert archive.total_duration_seconds == 305

    def test_to_session_for_resume(self, sample_session):
        """Resume용 RecoverySession 변환 테스트."""
        archive = RecoverySessionArchiveData.from_session(sample_session)

        # IN_PROGRESS 상태로 변경 (Resume 가능하게)
        archive.status = "in_progress"
        archive.current_step_index = 1

        restored = archive.to_session()

        assert restored.id == sample_session.id
        assert restored.namespace == sample_session.namespace
        assert restored.status == RecoveryStatus.IN_PROGRESS
        assert restored.current_step_index == 1
        assert len(restored.steps) == 2

    def test_to_dict_and_from_dict(self, sample_session):
        """딕셔너리 변환 왕복 테스트."""
        original = RecoverySessionArchiveData.from_session(sample_session)
        original.metadata = {"test_key": "test_value"}

        data = original.to_dict()
        restored = RecoverySessionArchiveData.from_dict(data)

        assert restored.session_id == original.session_id
        assert restored.total_duration_seconds == original.total_duration_seconds
        assert restored.metadata == {"test_key": "test_value"}
        assert len(restored.steps) == len(original.steps)


# =============================================================================
# RecoverySessionArchiveService Tests
# =============================================================================


class TestRecoverySessionArchiveService:
    """RecoverySessionArchiveService 테스트."""

    def test_archive_session(self, archive_service, sample_session):
        """세션 아카이브 테스트."""
        result = archive_service.archive_session(sample_session)

        assert result.session_id == sample_session.id
        assert result.status == "completed"
        assert result.archived_at is not None

    def test_archive_session_with_metadata(self, archive_service, sample_session):
        """메타데이터 포함 아카이브 테스트."""
        metadata = {"environment": "production", "region": "ap-northeast-2"}

        result = archive_service.archive_session(sample_session, metadata=metadata)

        assert result.metadata["environment"] == "production"
        assert result.metadata["region"] == "ap-northeast-2"

    def test_get_session(self, archive_service, sample_session):
        """세션 조회 테스트."""
        archive_service.archive_session(sample_session)

        result = archive_service.get_session(sample_session.id)

        assert result is not None
        assert result.session_id == sample_session.id

    def test_get_session_not_found(self, archive_service):
        """존재하지 않는 세션 조회 테스트."""
        result = archive_service.get_session("nonexistent-id")

        assert result is None

    def test_get_history_basic(self, archive_service, sample_session):
        """히스토리 조회 테스트."""
        archive_service.archive_session(sample_session)

        # 다른 세션 추가
        sample_session.id = "recovery-test456"
        sample_session.namespace = "seoul"
        archive_service.archive_session(sample_session)

        history = archive_service.get_history()

        assert len(history) == 2

    def test_get_history_with_namespace_filter(self, archive_service, sample_session):
        """네임스페이스 필터링 히스토리 테스트."""
        archive_service.archive_session(sample_session)

        # seoul 네임스페이스 세션 추가
        sample_session.id = "recovery-test456"
        sample_session.namespace = "seoul"
        archive_service.archive_session(sample_session)

        history = archive_service.get_history(namespace="seoul")

        assert len(history) == 1
        assert history[0].namespace == "seoul"

    def test_get_history_with_status_filter(self, archive_service, sample_session):
        """상태 필터링 히스토리 테스트."""
        archive_service.archive_session(sample_session)

        # 실패 세션 추가
        sample_session.id = "recovery-failed123"
        sample_session.status = RecoveryStatus.FAILED
        archive_service.archive_session(sample_session)

        history = archive_service.get_history(status="failed")

        assert len(history) == 1
        assert history[0].status == "failed"

    def test_get_history_with_limit(self, archive_service, sample_session):
        """제한된 히스토리 조회 테스트."""
        # 5개 세션 생성
        for i in range(5):
            sample_session.id = f"recovery-test{i}"
            archive_service.archive_session(sample_session)

        history = archive_service.get_history(limit=3)

        assert len(history) == 3

    def test_load_for_resume_in_progress(self, archive_service, sample_session):
        """IN_PROGRESS 세션 Resume 테스트."""
        # IN_PROGRESS 상태로 변경
        sample_session.status = RecoveryStatus.IN_PROGRESS
        sample_session.current_step_index = 1
        sample_session.completed_at = None

        archive_service.archive_session(sample_session)

        result = archive_service.load_for_resume(sample_session.id)

        assert result is not None
        assert result.status == RecoveryStatus.IN_PROGRESS
        assert result.current_step_index == 1

    def test_load_for_resume_completed_session(self, archive_service, sample_session):
        """완료된 세션 Resume 불가 테스트."""
        archive_service.archive_session(sample_session)

        result = archive_service.load_for_resume(sample_session.id)

        assert result is None

    def test_get_resumable_sessions(self, archive_service, sample_session):
        """Resume 가능 세션 목록 테스트."""
        # 완료 세션
        archive_service.archive_session(sample_session)

        # IN_PROGRESS 세션
        sample_session.id = "recovery-inprogress"
        sample_session.status = RecoveryStatus.IN_PROGRESS
        archive_service.archive_session(sample_session)

        resumable = archive_service.get_resumable_sessions()

        assert len(resumable) == 1
        assert resumable[0].session_id == "recovery-inprogress"

    def test_get_statistics(self, archive_service, sample_session):
        """통계 집계 테스트."""
        # 완료 세션 3개
        for i in range(3):
            sample_session.id = f"recovery-completed{i}"
            sample_session.status = RecoveryStatus.COMPLETED
            archive_service.archive_session(sample_session)

        # 실패 세션 1개
        sample_session.id = "recovery-failed"
        sample_session.status = RecoveryStatus.FAILED
        archive_service.archive_session(sample_session)

        stats = archive_service.get_statistics()

        assert stats["total_sessions"] == 4
        assert stats["completed"] == 3
        assert stats["failed"] == 1
        assert stats["success_rate"] == 75.0

    def test_cleanup_old_archives(self, archive_service, sample_session):
        """오래된 아카이브 정리 테스트."""
        # 현재 세션
        archive_service.archive_session(sample_session)

        # 오래된 세션 (1년 전)
        old_session = RecoverySession(
            id="recovery-old",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.COMPLETED,
            steps=[],
            started_at=(datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
        )
        archive_service.archive_session(old_session)

        # 정리 실행 (365일 보관)
        deleted = archive_service.cleanup_old_archives(retention_days=365)

        assert deleted == 1

        # 현재 세션은 남아있어야 함
        remaining = archive_service.get_history()
        assert len(remaining) == 1


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    """싱글톤 테스트."""

    def test_get_singleton(self):
        """싱글톤 획득 테스트."""
        reset_recovery_session_archive_service()

        service1 = get_recovery_session_archive_service()
        service2 = get_recovery_session_archive_service()

        # 동일 인스턴스
        assert service1 is service2

    def test_reset_singleton(self):
        """싱글톤 리셋 테스트."""
        service1 = get_recovery_session_archive_service()

        reset_recovery_session_archive_service()

        service2 = get_recovery_session_archive_service()

        # 다른 인스턴스
        assert service1 is not service2
