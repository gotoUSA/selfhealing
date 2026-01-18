"""
Forensic 민감정보 마스킹 테스트.

테스트 대상:
- TestForensicMasking: 민감정보 마스킹
"""

import pytest
from unittest.mock import MagicMock


class TestForensicMasking:
    """Forensic 민감정보 마스킹 테스트."""
    
    def test_password_masked_in_context(self):
        """password 필드 마스킹."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge()
        context = {
            "user": "admin",
            "password": "secret123",
            "action": "login",
        }
        
        masked = bridge._mask_context(context)
        
        assert masked["user"] == "admin"
        assert masked["password"] == "***REDACTED***"
        assert masked["action"] == "login"
    
    def test_api_key_masked_in_context(self):
        """api_key 필드 마스킹."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge()
        context = {
            "api_key": "sk-1234567890",
            "endpoint": "/api/v1/data",
        }
        
        masked = bridge._mask_context(context)
        
        assert masked["api_key"] == "***REDACTED***"
        assert masked["endpoint"] == "/api/v1/data"
    
    def test_nested_sensitive_fields_masked(self):
        """중첩된 민감 필드 마스킹."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        # 커스텀 패턴 사용 (credentials가 기본 패턴에 있으므로)
        bridge = ForensicAuditBridge(sensitive_patterns=["password", "token"])
        context = {
            "user": {
                "name": "admin",
                "login_info": {
                    "password": "secret123",
                    "token": "jwt-token-xyz",
                },
            },
            "action": "update",
        }
        
        masked = bridge._mask_context(context)
        
        assert masked["user"]["name"] == "admin"
        assert masked["user"]["login_info"]["password"] == "***REDACTED***"
        assert masked["user"]["login_info"]["token"] == "***REDACTED***"
        assert masked["action"] == "update"
    
    def test_custom_sensitive_patterns(self):
        """커스텀 민감 패턴 사용."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(sensitive_patterns=["custom_secret", "my_key"])
        context = {
            "custom_secret": "value1",
            "my_key": "value2",
            "password": "should-not-be-masked",  # 커스텀 패턴에 없음
        }
        
        masked = bridge._mask_context(context)
        
        assert masked["custom_secret"] == "***REDACTED***"
        assert masked["my_key"] == "***REDACTED***"
        assert masked["password"] == "should-not-be-masked"
    
    def test_on_exception_captured_masks_context(self, mock_audit_adapter):
        """on_exception_captured 시 컨텍스트 마스킹."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)
        
        exception = ValueError("Test error")
        stack_trace = "line 1\nline 2\nline 3"
        context = {
            "user_id": "123",
            "api_key": "secret-key",
        }
        
        bridge.on_exception_captured(
            exception=exception,
            stack_trace=stack_trace,
            context=context,
            sanitized=True,
        )
        
        # Audit 이벤트가 기록되어야 함
        events = mock_audit_adapter.get_events_by_type("FORENSIC_CAPTURE_COMPLETED")
        assert len(events) == 1
        assert events[0]["details"]["sanitized"] is True
    
    def test_on_exception_captured_no_mask_when_sanitized_false(self, mock_audit_adapter):
        """sanitized=False일 때 마스킹 안함."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)
        
        exception = ValueError("Test error")
        stack_trace = "line 1"
        context = {"password": "secret"}
        
        # sanitized=False로 호출해도 _mask_context는 호출되지 않음
        bridge.on_exception_captured(
            exception=exception,
            stack_trace=stack_trace,
            context=context,
            sanitized=False,
        )
        
        events = mock_audit_adapter.get_events_by_type("FORENSIC_CAPTURE_COMPLETED")
        assert len(events) == 1
        assert events[0]["details"]["sanitized"] is False
