"""
ThrottleDLQSink 단위 테스트.

테스트 대상: selfhealing.services.throttle.dlq_sink.ThrottleDLQSink

검증 범위:
- handle_rejection() 정상 동작
- DLQ integration 주입 및 lazy import
- Fail-Open: DLQ 모듈 미설치/저장 실패 시 예외 전파 없음
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.services.throttle.dlq_sink import ThrottleDLQSink

# =============================================================================
# handle_rejection() 동작 검증
# =============================================================================


class TestThrottleDLQSinkHandleRejectionBehavior:
    """ThrottleDLQSink.handle_rejection() 동작 검증."""

    def test_delegates_to_integration(self):
        """handle_rejection() 호출 시 integration.store_denied_request()에 위임."""
        mock_integration = MagicMock()
        sink = ThrottleDLQSink(dlq_integration=mock_integration)

        context = {
            "service_name": "svc_alpha",
            "domain": "domain_a",
            "tier_id": "critical",
        }
        sink.handle_rejection(context, reason="rate_limit_exceeded")

        mock_integration.store_denied_request.assert_called_once_with(
            service_name="svc_alpha",
            domain="domain_a",
            tier_id="critical",
            reason="rate_limit_exceeded",
            request_data=context,
        )

    def test_missing_context_keys_use_defaults(self):
        """context에 키가 없으면 기본값을 사용해야 한다."""
        mock_integration = MagicMock()
        sink = ThrottleDLQSink(dlq_integration=mock_integration)

        sink.handle_rejection({}, reason="test")

        call_kwargs = mock_integration.store_denied_request.call_args.kwargs
        assert call_kwargs["service_name"] == "unknown"
        assert call_kwargs["domain"] == ""
        assert call_kwargs["tier_id"] == "standard"

    def test_failopen_on_integration_exception(self):
        """integration.store_denied_request() 예외 시 무시 (Fail-Open)."""
        mock_integration = MagicMock()
        mock_integration.store_denied_request.side_effect = RuntimeError("DLQ down")
        sink = ThrottleDLQSink(dlq_integration=mock_integration)

        # 예외가 전파되지 않아야 함
        sink.handle_rejection({"service_name": "test"}, reason="test")


# =============================================================================
# Lazy import / Fail-Open 동작 검증
# =============================================================================


class TestThrottleDLQSinkLazyImportBehavior:
    """DLQ integration lazy import 및 Fail-Open 동작 검증."""

    def test_none_integration_triggers_lazy_import(self):
        """dlq_integration=None일 때 _get_dlq_integration()이 lazy import를 시도."""
        sink = ThrottleDLQSink(dlq_integration=None)
        with patch(
            "selfhealing.services.throttle.dlq_sink.ThrottleDLQSink._get_dlq_integration",
            return_value=None,
        ):
            # integration이 None이면 handle_rejection은 아무 동작 없이 반환
            sink.handle_rejection({"service_name": "test"}, reason="test")

    def test_lazy_import_failure_failopen(self):
        """lazy import 실패 시 None 반환 (Fail-Open)."""
        sink = ThrottleDLQSink(dlq_integration=None)
        with patch.dict(
            "sys.modules",
            {"selfhealing.services.throttle.dlq_integration": None},
        ):
            result = sink._get_dlq_integration()
            assert result is None

    def test_injected_integration_used_directly(self):
        """dlq_integration이 주입되면 lazy import 없이 직접 사용."""
        mock_integration = MagicMock()
        sink = ThrottleDLQSink(dlq_integration=mock_integration)
        assert sink._get_dlq_integration() is mock_integration
