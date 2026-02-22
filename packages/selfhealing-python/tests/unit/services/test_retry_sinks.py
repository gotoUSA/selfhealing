"""
DLQSink(Dead Letter Queue Sink) 단위 테스트.

테스트 대상: services/retry_handler/sinks.py
- DLQSink: should_dlq 플래그 기반 DLQ 저장, Fail-Open
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.interfaces.resilience_policy import (
    PolicyContext,
    PolicyOutcome,
    PolicyResult,
)
from selfhealing.services.retry_handler.sinks import DLQSink

# =============================================================================
# DLQSink — 계약 검증
# =============================================================================


class TestDLQSinkContract:
    """DLQSink 구조 및 기본값 검증."""

    def test_has_handle_failure_method(self):
        """DLQSink는 handle_failure 메서드를 가진다."""
        assert hasattr(DLQSink(), "handle_failure")


# =============================================================================
# DLQSink — 동작 검증
# =============================================================================


class TestDLQSinkBehavior:
    """DLQSink 동작 검증. should_dlq 플래그 및 Fail-Open 원칙."""

    def _make_result(self, should_dlq: bool = True) -> PolicyResult:
        return PolicyResult(
            outcome=PolicyOutcome.FAILURE,
            total_attempts=3,
            metadata={
                "should_dlq": should_dlq,
                "domain": "test",
                "retry_history": [],
            },
        )

    def _make_context(self) -> PolicyContext:
        return PolicyContext(
            domain="test",
            tier_id="tier-1",
            region="kr",
        )

    def test_skips_when_should_dlq_false(self):
        """should_dlq=False이면 _store_to_dlq를 호출하지 않는다."""
        sink = DLQSink()
        result = self._make_result(should_dlq=False)
        ret = sink.handle_failure(Exception("err"), self._make_context(), result)
        assert ret is None

    def test_skips_when_should_dlq_key_missing(self):
        """should_dlq 키가 없으면 _store_to_dlq를 호출하지 않는다."""
        sink = DLQSink()
        result = PolicyResult(
            outcome=PolicyOutcome.FAILURE,
            total_attempts=3,
            metadata={"domain": "test"},
        )
        ret = sink.handle_failure(Exception("err"), self._make_context(), result)
        assert ret is None

    @patch("selfhealing.services.dlq.store_to_dlq")
    def test_stores_when_should_dlq_true(self, mock_store):
        """should_dlq=True이면 store_to_dlq를 호출한다."""
        mock_store.return_value = MagicMock(success=True, dlq_id="dlq-123")
        sink = DLQSink()
        result = self._make_result(should_dlq=True)
        ctx = self._make_context()
        err = ValueError("fail")

        ret = sink.handle_failure(err, ctx, result)
        mock_store.assert_called_once()
        assert ret == "dlq-123"

    def test_handles_store_failure_gracefully(self):
        """store_to_dlq 호출 실패 시 예외가 전파되지 않는다 (Fail-Open)."""
        sink = DLQSink()
        result = self._make_result(should_dlq=True)
        with patch(
            "selfhealing.services.dlq.store_to_dlq",
            side_effect=RuntimeError("DLQ down"),
        ):
            ret = sink.handle_failure(Exception("err"), self._make_context(), result)
            assert ret is None

    def test_handles_import_error_gracefully(self):
        """store_to_dlq import 실패 시 예외가 전파되지 않는다 (Fail-Open)."""
        sink = DLQSink()
        result = self._make_result(should_dlq=True)
        with patch(
            "selfhealing.services.dlq.store_to_dlq",
            side_effect=ImportError("no module"),
        ):
            ret = sink.handle_failure(Exception("err"), self._make_context(), result)
            assert ret is None

    def test_context_none_is_safe(self):
        """context=None이어도 에러 없이 동작한다."""
        sink = DLQSink()
        result = self._make_result(should_dlq=True)
        with patch(
            "selfhealing.services.dlq.store_to_dlq",
            return_value=MagicMock(success=True, dlq_id="dlq-456"),
        ):
            ret = sink.handle_failure(Exception("err"), None, result)
            assert ret == "dlq-456"
