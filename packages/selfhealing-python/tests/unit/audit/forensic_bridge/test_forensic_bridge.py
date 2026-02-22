"""
ForensicAuditBridge 테스트.

테스트 대상:
- TestForensicAuditBridge: ForensicAuditBridge 핵심 기능
"""

from unittest.mock import MagicMock


class TestForensicAuditBridge:
    """ForensicAuditBridge 테스트."""

    def test_on_exception_captured(self, mock_audit_adapter):
        """예외 캡처 시 Audit 기록."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge

        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)

        try:
            raise ValueError("Test exception")
        except ValueError as e:
            bridge.on_exception_captured(
                exception=e,
                stack_trace="line 1\nline 2\nline 3",
                context={"request_id": "req-123", "user_id": "user-456"},
                sanitized=True,
            )

        # Audit 이벤트 확인
        events = mock_audit_adapter.get_events_by_type("FORENSIC_CAPTURE_COMPLETED")
        assert len(events) == 1
        assert events[0]["source"] == "ForensicCapture"
        assert events[0]["details"]["exception_type"] == "ValueError"
        assert events[0]["details"]["capture_reason"] == "exception"

    def test_on_anomaly_detected(self, mock_audit_adapter):
        """이상 패턴 감지 시 Audit 기록."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge

        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)

        bridge.on_anomaly_detected(
            anomaly_type="statistical",
            score=4.5,
            threshold=3.0,
            context={"metric": "response_time", "value": 1500},
        )

        # Audit 이벤트 확인
        events = mock_audit_adapter.get_events_by_type("FORENSIC_ANOMALY_DETECTED")
        assert len(events) == 1
        assert events[0]["details"]["anomaly_type"] == "statistical"
        assert events[0]["details"]["score"] == 4.5
        assert events[0]["details"]["exceeded_by"] == 1.5

    def test_on_memory_snapshot(self, mock_audit_adapter):
        """메모리 스냅샷 시 Audit 기록."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge

        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)

        bridge.on_memory_snapshot(
            snapshot_id="snap-001",
            memory_mb=256.5,
            object_count=12345,
        )

        # Audit 이벤트 확인
        events = mock_audit_adapter.get_events_by_type("FORENSIC_CAPTURE_STARTED")
        assert len(events) == 1
        assert events[0]["details"]["capture_type"] == "memory_snapshot"
        assert events[0]["details"]["snapshot_id"] == "snap-001"
        assert events[0]["details"]["memory_mb"] == 256.5

    def test_context_summarization(self, mock_audit_adapter):
        """컨텍스트 요약 (긴 값 truncate)."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge

        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)

        long_value = "x" * 200  # 200자
        context = {"short": "abc", "long": long_value}

        summary = bridge._summarize_context(context)

        assert summary["short"] == "abc"
        assert len(summary["long"]) < 150  # truncated
        assert "truncated" in summary["long"]

    def test_audit_failure_does_not_raise(self, mock_audit_adapter):
        """Audit 실패 시 예외 발생 안함."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge

        # 실패하는 audit adapter
        failing_adapter = MagicMock()
        failing_adapter.log_event.side_effect = Exception("Audit failed")

        bridge = ForensicAuditBridge(audit_adapter=failing_adapter)

        # 예외 발생하지 않아야 함
        bridge.on_exception_captured(
            exception=ValueError("Test"),
            stack_trace="",
            context={},
        )
