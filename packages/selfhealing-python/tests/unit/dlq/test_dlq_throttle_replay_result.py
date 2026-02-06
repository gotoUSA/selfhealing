"""
DLQ Throttle Replay Result 데이터클래스 단위 테스트.

테스트 대상:
- selfhealing.services.dlq_models.DLQThrottleReplayResult
- selfhealing.services.dlq_models.DLQThrottleBatchReplayResult

테스트 시나리오:
1. DLQThrottleReplayResult 기본값 검증
2. DLQThrottleBatchReplayResult 기본값 검증
3. DLQ __init__.py 에서 import 가능 확인
"""

import pytest

from selfhealing.services.dlq_models import (
    DLQThrottleBatchReplayResult,
    DLQThrottleReplayResult,
)


class TestDLQThrottleReplayResult:
    """DLQThrottleReplayResult 데이터클래스 테스트."""

    def test_success_result(self):
        """성공 결과 생성."""
        result = DLQThrottleReplayResult(success=True, entry_id=42)
        assert result.success is True
        assert result.entry_id == 42
        assert result.error is None
        assert result.retry_after is None

    def test_failure_result_with_retry_after(self):
        """Throttle 거부 실패 결과 생성 (retry_after 포함)."""
        result = DLQThrottleReplayResult(
            success=False,
            error="Throttle rejected: capacity_exceeded",
            retry_after=1.5,
        )
        assert result.success is False
        assert "capacity_exceeded" in result.error
        assert result.retry_after == 1.5
        assert result.entry_id is None

    def test_default_optional_fields(self):
        """선택 필드 기본값 확인."""
        result = DLQThrottleReplayResult(success=False)
        assert result.entry_id is None
        assert result.error is None
        assert result.retry_after is None


class TestDLQThrottleBatchReplayResult:
    """DLQThrottleBatchReplayResult 데이터클래스 테스트."""

    def test_batch_result_with_all_fields(self):
        """모든 필드가 올바르게 설정된다."""
        result = DLQThrottleBatchReplayResult(
            total=20,
            succeeded=15,
            failed=3,
            skipped=2,
            early_stop_reason="emergency_mode_activated",
        )
        assert result.total == 20
        assert result.succeeded == 15
        assert result.failed == 3
        assert result.skipped == 2
        assert result.early_stop_reason == "emergency_mode_activated"

    def test_default_values(self):
        """기본값 확인."""
        result = DLQThrottleBatchReplayResult()
        assert result.total == 0
        assert result.succeeded == 0
        assert result.failed == 0
        assert result.skipped == 0
        assert result.early_stop_reason is None


class TestDLQInitExports:
    """DLQ 패키지 __init__.py 에서 데이터클래스 import 가능 확인."""

    def test_import_from_dlq_package(self):
        """DLQ 패키지에서 직접 import 가능."""
        from selfhealing.services.dlq import (
            DLQThrottleBatchReplayResult,
            DLQThrottleReplayResult,
        )

        assert DLQThrottleReplayResult is not None
        assert DLQThrottleBatchReplayResult is not None
