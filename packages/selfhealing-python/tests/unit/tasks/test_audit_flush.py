"""
Audit 플러시 Celery 태스크 단위 테스트.

테스트 항목:
- flush_redis_audit_to_db 태스크 동작
- retry_audit_fallback_buffer 태스크 동작
- get_redis_audit_buffer_stats 함수 동작
- Beat 스케줄 등록 확인
"""

from __future__ import annotations

from unittest.mock import Mock, patch


class TestFlushRedisAuditToDb:
    """flush_redis_audit_to_db 태스크 테스트."""

    def test_flush_task_success(self) -> None:
        """정상 플러시 동작."""
        from selfhealing.tasks.audit_flush import flush_redis_audit_to_db

        mock_redis_buffer = Mock()
        mock_redis_buffer.flush_to_external.return_value = 100

        mock_db_adapter = Mock()

        with (
            patch(
                "selfhealing.adapters.audit.redis_buffer.create_redis_audit_buffer",
                return_value=mock_redis_buffer,
            ),
            patch(
                "selfhealing.adapters.audit.django_adapter.DjangoAuditLogAdapter",
                return_value=mock_db_adapter,
            ),
        ):
            result = flush_redis_audit_to_db()

        assert result["success"] is True
        assert result["flushed_count"] == 100
        assert "duration_ms" in result
        mock_redis_buffer.flush_to_external.assert_called_once_with(
            target_adapter=mock_db_adapter,
            batch_size=500,
        )

    def test_flush_task_custom_batch_size(self) -> None:
        """커스텀 배치 크기 사용."""
        from selfhealing.tasks.audit_flush import flush_redis_audit_to_db

        mock_redis_buffer = Mock()
        mock_redis_buffer.flush_to_external.return_value = 50

        with (
            patch(
                "selfhealing.adapters.audit.redis_buffer.create_redis_audit_buffer",
                return_value=mock_redis_buffer,
            ),
            patch(
                "selfhealing.adapters.audit.django_adapter.DjangoAuditLogAdapter",
                return_value=Mock(),
            ),
        ):
            result = flush_redis_audit_to_db(batch_size=200)

        assert result["flushed_count"] == 50
        # 커스텀 배치 크기 확인
        call_kwargs = mock_redis_buffer.flush_to_external.call_args[1]
        assert call_kwargs["batch_size"] == 200

    def test_flush_task_redis_unavailable(self) -> None:
        """Redis 불가용 시 에러 반환."""
        from selfhealing.tasks.audit_flush import flush_redis_audit_to_db

        with patch(
            "selfhealing.adapters.audit.redis_buffer.create_redis_audit_buffer",
            return_value=None,
        ):
            result = flush_redis_audit_to_db()

        assert result["success"] is False
        assert result["flushed_count"] == 0
        assert "error" in result

    def test_flush_task_exception_handling(self) -> None:
        """예외 발생 시 에러 반환."""
        from selfhealing.tasks.audit_flush import flush_redis_audit_to_db

        with patch(
            "selfhealing.adapters.audit.redis_buffer.create_redis_audit_buffer",
            side_effect=Exception("Connection refused"),
        ):
            result = flush_redis_audit_to_db()

        assert result["success"] is False
        assert "Connection refused" in result["error"]


class TestRetryAuditFallbackBuffer:
    """retry_audit_fallback_buffer 태스크 테스트."""

    def test_retry_task_success(self) -> None:
        """폴백 버퍼 재시도 성공."""
        from selfhealing.tasks.audit_flush import retry_audit_fallback_buffer

        mock_redis_buffer = Mock()
        mock_redis_buffer.retry_fallback_buffer.return_value = 5
        mock_redis_buffer.get_fallback_buffer_size.return_value = 0

        with patch(
            "selfhealing.adapters.audit.redis_buffer.create_redis_audit_buffer",
            return_value=mock_redis_buffer,
        ):
            result = retry_audit_fallback_buffer()

        assert result["success"] is True
        assert result["recovered_count"] == 5
        assert result["remaining_count"] == 0

    def test_retry_task_partial_recovery(self) -> None:
        """일부만 복구된 경우."""
        from selfhealing.tasks.audit_flush import retry_audit_fallback_buffer

        mock_redis_buffer = Mock()
        mock_redis_buffer.retry_fallback_buffer.return_value = 3
        mock_redis_buffer.get_fallback_buffer_size.return_value = 2

        with patch(
            "selfhealing.adapters.audit.redis_buffer.create_redis_audit_buffer",
            return_value=mock_redis_buffer,
        ):
            result = retry_audit_fallback_buffer()

        assert result["recovered_count"] == 3
        assert result["remaining_count"] == 2

    def test_retry_task_redis_unavailable(self) -> None:
        """Redis 불가용 시 에러 반환."""
        from selfhealing.tasks.audit_flush import retry_audit_fallback_buffer

        with patch(
            "selfhealing.adapters.audit.redis_buffer.create_redis_audit_buffer",
            return_value=None,
        ):
            result = retry_audit_fallback_buffer()

        assert result["success"] is False
        assert result["remaining_count"] == -1


class TestGetRedisAuditBufferStats:
    """get_redis_audit_buffer_stats 함수 테스트."""

    def test_stats_success(self) -> None:
        """통계 조회 성공."""
        from selfhealing.tasks.audit_flush import get_redis_audit_buffer_stats

        mock_stats = {
            "total_writes": 100,
            "total_batch_writes": 10,
            "total_flushes": 50,
        }
        mock_redis_buffer = Mock()
        mock_redis_buffer.get_buffer_stats.return_value = mock_stats

        with patch(
            "selfhealing.adapters.audit.redis_buffer.create_redis_audit_buffer",
            return_value=mock_redis_buffer,
        ):
            result = get_redis_audit_buffer_stats()

        assert result == mock_stats

    def test_stats_redis_unavailable(self) -> None:
        """Redis 불가용 시 에러 반환."""
        from selfhealing.tasks.audit_flush import get_redis_audit_buffer_stats

        with patch(
            "selfhealing.adapters.audit.redis_buffer.create_redis_audit_buffer",
            return_value=None,
        ):
            result = get_redis_audit_buffer_stats()

        assert "error" in result


class TestAuditFlushBeatSchedule:
    """Celery Beat 스케줄 테스트."""

    def test_get_audit_flush_beat_schedule_structure(self) -> None:
        """스케줄 구조 확인."""
        from selfhealing.tasks.audit_flush import get_audit_flush_beat_schedule

        schedule = get_audit_flush_beat_schedule()

        # 필수 키 확인
        assert "flush-redis-audit-to-db" in schedule
        assert "retry-audit-fallback-buffer" in schedule

        # 플러시 태스크 설정 확인
        flush_task = schedule["flush-redis-audit-to-db"]
        assert flush_task["task"] == "selfhealing.tasks.audit_flush.flush_redis_audit_to_db"
        assert "schedule" in flush_task
        assert flush_task["options"]["queue"] == "audit_flush"

        # 재시도 태스크 설정 확인
        retry_task = schedule["retry-audit-fallback-buffer"]
        assert retry_task["task"] == "selfhealing.tasks.audit_flush.retry_audit_fallback_buffer"
        assert retry_task["schedule"] == 60.0

    def test_schedule_intervals(self) -> None:
        """스케줄 주기 확인."""
        from selfhealing.tasks.audit_flush import (
            REDIS_AUDIT_FLUSH_INTERVAL,
            get_audit_flush_beat_schedule,
        )

        schedule = get_audit_flush_beat_schedule()

        # 플러시 주기 = 환경변수 기본값 10초
        assert schedule["flush-redis-audit-to-db"]["schedule"] == float(REDIS_AUDIT_FLUSH_INTERVAL)

        # 재시도 주기 = 60초
        assert schedule["retry-audit-fallback-buffer"]["schedule"] == 60.0


class TestBeatScheduleIntegration:
    """beat_schedule.py 통합 테스트."""

    def test_audit_flush_included_in_selfhealing_schedule(self) -> None:
        """audit flush가 selfhealing beat schedule에 포함되는지 확인."""
        from selfhealing.adapters.celery.beat_schedule import (
            get_selfhealing_beat_schedule,
        )

        schedule = get_selfhealing_beat_schedule(
            include_cleanup=False,
            include_intelligence=False,
            include_compliance=False,
            include_traffic_aware=False,
            include_canary_watchdog=False,
            include_governance=False,
            include_xtest_cleanup=False,
            include_audit_flush=True,
            include_legacy=False,
        )

        # audit flush 태스크 포함 확인
        assert "flush-redis-audit-to-db" in schedule
        assert "retry-audit-fallback-buffer" in schedule

    def test_audit_flush_queue_in_config(self) -> None:
        """audit_flush 큐가 SELFHEALING_QUEUE_CONFIG에 포함되는지 확인."""
        from selfhealing.adapters.celery.beat_schedule import SELFHEALING_QUEUE_CONFIG

        assert "audit_flush" in SELFHEALING_QUEUE_CONFIG
        assert SELFHEALING_QUEUE_CONFIG["audit_flush"]["exchange"] == "selfhealing"
        assert SELFHEALING_QUEUE_CONFIG["audit_flush"]["routing_key"] == "audit_flush"
