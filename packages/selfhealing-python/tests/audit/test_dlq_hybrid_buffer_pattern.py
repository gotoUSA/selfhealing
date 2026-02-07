"""
DLQService와 audit_helpers의 하이브리드 로직 테스트

요구사항:
1. audit_helpers.py에 request 파라미터 추가 (선택적) - ✅ 구현됨
2. 기존 직접 호출 유지 (하위 호환) - ✅ 구현됨
3. 새 코드에서 버퍼 패턴 사용 - ✅ 구현됨

이 테스트는 DLQService가 request 파라미터를 통해 버퍼 패턴을 올바르게 사용하는지 검증합니다.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Any


class MockRequest:
    """Django HttpRequest 모킹."""

    def __init__(self):
        self.META = {}
        self.path = "/api/test/"
        self.method = "POST"


class TestDLQServiceWithRequest:
    """DLQService 테스트 - request가 있는 경우 버퍼에 적재."""

    def test_store_failure_with_request_adds_to_buffer(self):
        """store_failure가 request와 함께 호출되면 버퍼에 이벤트가 적재되어야 함."""
        from selfhealing.services.dlq_service import DLQService, DLQConfig
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType

        # Mock repository
        mock_repo = MagicMock()
        mock_entry = MagicMock()
        mock_entry.id = 123
        mock_repo.create.return_value = mock_entry

        # Mock request
        request = MockRequest()

        # DLQService with mock repo
        config = DLQConfig(enabled=True)
        service = DLQService(config=config, repository=mock_repo)

        # Execute
        result = service.store_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            error_message="Connection failed",
            request=request,
        )

        # Verify buffer has the event
        buffer = RequestAuditBuffer.get(request)
        assert buffer is not None
        assert buffer.event_count() >= 1

        # Check event type
        events = buffer.get_events_by_type(AuditEventType.DLQ_STORE)
        assert len(events) >= 1
        assert events[0].domain == "payment"

    def test_store_failure_without_request_logs_directly(self):
        """store_failure가 request 없이 호출되면 직접 로깅."""
        from selfhealing.services.dlq_service import DLQService, DLQConfig

        # Mock repository
        mock_repo = MagicMock()
        mock_entry = MagicMock()
        mock_entry.id = 456
        mock_repo.create.return_value = mock_entry

        # DLQService with mock repo
        config = DLQConfig(enabled=True)
        service = DLQService(config=config, repository=mock_repo)

        # Execute without request - should not raise
        result = service.store_failure(
            domain="point",
            failure_type="AMOUNT_MISMATCH",
            error_message="Amount does not match",
        )

        # Verify success
        assert result.success
        assert result.dlq_id == 456


class TestDLQServiceReplay:
    """DLQService.replay 테스트."""

    def test_replay_with_request_logs_to_buffer(self):
        """replay가 request와 함께 호출되면 버퍼에 이벤트가 적재되어야 함."""
        from selfhealing.services.dlq_service import DLQService, DLQConfig
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType

        # Mock repository
        mock_repo = MagicMock()
        mock_entry = MagicMock()
        mock_entry.id = 100
        mock_entry.domain = "payment"
        mock_entry.failure_type = "PG_TIMEOUT"
        mock_repo.find_by_status.return_value = [mock_entry]

        # Mock request
        request = MockRequest()

        # DLQService with mock repo
        config = DLQConfig(enabled=True)
        service = DLQService(config=config, repository=mock_repo)

        # Mock replay handler via replay_service module
        with patch("selfhealing.services.replay_service.get_replay_handler") as mock_get_handler:
            mock_handler = MagicMock()
            mock_handler.can_replay.return_value = (True, "")
            mock_result = MagicMock()
            mock_result.success = True
            mock_handler.replay.return_value = mock_result
            mock_get_handler.return_value = mock_handler

            # Execute
            result = service.replay(domain="payment", request=request)

        # Verify buffer has replay event
        buffer = RequestAuditBuffer.get(request)
        assert buffer is not None

        # Check for DLQ_REPLAY event
        events = buffer.get_events_by_type(AuditEventType.DLQ_REPLAY)
        assert len(events) >= 1


class TestAuditHelpersHybridWithRequest:
    """audit_helpers 하이브리드 로직 테스트 - request 있음."""

    def test_log_dlq_store_with_request_adds_to_buffer(self):
        """request와 함께 호출 시 버퍼에 적재."""
        from selfhealing.services.audit_helpers import log_dlq_store_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType

        request = MockRequest()

        log_dlq_store_audit(
            dlq_id=123,
            domain="payment",
            failure_type="PG_TIMEOUT",
            request=request,
        )

        buffer = RequestAuditBuffer.get(request)
        assert buffer is not None
        assert buffer.event_count() >= 1

        events = buffer.get_events_by_type(AuditEventType.DLQ_STORE)
        assert len(events) >= 1

    def test_log_dlq_replay_with_request_adds_to_buffer(self):
        """request와 함께 호출 시 버퍼에 적재."""
        from selfhealing.services.audit_helpers import log_dlq_replay_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType

        request = MockRequest()

        log_dlq_replay_audit(
            dlq_id=456,
            domain="point",
            success=True,
            request=request,
        )

        buffer = RequestAuditBuffer.get(request)
        assert buffer is not None

        events = buffer.get_events_by_type(AuditEventType.DLQ_REPLAY)
        assert len(events) >= 1

    def test_log_cb_state_change_with_request_adds_to_buffer(self):
        """request와 함께 호출 시 버퍼에 적재."""
        from selfhealing.services.audit_helpers import log_cb_state_change_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType

        request = MockRequest()

        log_cb_state_change_audit(
            cb_name="payment_cb",
            old_state="closed",
            new_state="open",
            request=request,
        )

        buffer = RequestAuditBuffer.get(request)
        assert buffer is not None

        events = buffer.get_events_by_type(AuditEventType.CB_STATE_CHANGE)
        assert len(events) >= 1

    def test_backward_compatibility_without_request(self):
        """request 없이 호출 시 하위 호환 유지."""
        from selfhealing.services.audit_helpers import (
            log_dlq_store_audit,
            log_dlq_replay_audit,
            log_cb_state_change_audit,
            log_governance_blocked_audit,
            log_rate_limited_audit,
            log_pool_cb_rejection_audit,
        )

        # 모든 함수가 request 없이도 정상 동작해야 함
        log_dlq_store_audit(dlq_id=1, domain="payment", failure_type="TIMEOUT")
        log_dlq_replay_audit(dlq_id=2, domain="point", success=True)
        log_cb_state_change_audit(cb_name="test", old_state="closed", new_state="open")
        log_governance_blocked_audit(action="replay", block_reason="kill_switch")
        log_rate_limited_audit(client_ip="127.0.0.1", endpoint="/test", limit_type="global")
        log_pool_cb_rejection_audit(pool_name="default", current_utilization=0.9, threshold=0.8)

        # 에러 없이 완료되면 성공


class TestBufferIntegrity:
    """버퍼 무결성 테스트."""

    def test_multiple_operations_accumulate_in_buffer(self):
        """여러 작업이 동일 버퍼에 누적되어야 함."""
        from selfhealing.services.audit_helpers import (
            log_dlq_store_audit,
            log_cb_state_change_audit,
            log_rate_limited_audit,
        )
        from selfhealing.audit.event_buffer import RequestAuditBuffer

        request = MockRequest()

        # 여러 작업 수행
        log_dlq_store_audit(dlq_id=1, domain="payment", failure_type="TIMEOUT", request=request)
        log_cb_state_change_audit(cb_name="payment", old_state="closed", new_state="open", request=request)
        log_rate_limited_audit(client_ip="127.0.0.1", endpoint="/test", limit_type="global", request=request)

        buffer = RequestAuditBuffer.get(request)
        assert buffer is not None
        assert buffer.event_count() == 3

    def test_events_maintain_order(self):
        """이벤트가 적재 순서를 유지해야 함."""
        from selfhealing.services.audit_helpers import log_dlq_store_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer

        request = MockRequest()

        log_dlq_store_audit(dlq_id=1, domain="payment", failure_type="A", request=request)
        log_dlq_store_audit(dlq_id=2, domain="point", failure_type="B", request=request)
        log_dlq_store_audit(dlq_id=3, domain="webhook", failure_type="C", request=request)

        buffer = RequestAuditBuffer.get(request)
        events = buffer.get_events()

        assert events[0].details.get("failure_type") == "A"
        assert events[1].details.get("failure_type") == "B"
        assert events[2].details.get("failure_type") == "C"
