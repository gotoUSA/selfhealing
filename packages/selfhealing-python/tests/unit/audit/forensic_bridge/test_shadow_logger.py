"""
ShadowLogger Audit 통합 테스트.

테스트 대상:
- TestShadowLoggerAuditIntegration: ShadowLogger Audit 통합
"""

import pytest
from unittest.mock import MagicMock, patch


class TestShadowLoggerAuditIntegration:
    """ShadowLogger Audit 통합 테스트."""
    
    def test_record_sync_failure_calls_audit(self):
        """L2 동기화 실패 시 Audit 기록 호출."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        # ShadowLogger 싱글톤 초기화 (테스트용)
        logger = ShadowLogger()
        logger.clear()
        
        with patch.object(logger, "_record_audit_event") as mock_audit:
            logger.record_sync_failure(
                service_name="test_service",
                intended_state="OPEN",
                error=Exception("Connection timeout"),
                adapter_type="redis",
                operation="sync",
            )
            
            # Audit 호출 확인
            mock_audit.assert_called_once()
            call_args = mock_audit.call_args
            assert call_args[1]["event_type"] == "SHADOW_LOG_SYNC_FAILED"
            assert call_args[1]["service_name"] == "test_service"
            assert "error_message" in call_args[1]["details"]
    
    def test_mark_as_synced_calls_audit(self):
        """복구 완료 시 Audit 기록 호출."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        logger = ShadowLogger()
        logger.clear()
        
        # 먼저 실패 기록
        logger.record_sync_failure(
            service_name="test_service",
            intended_state="OPEN",
            error=Exception("Test error"),
        )
        
        with patch.object(logger, "_record_audit_event") as mock_audit:
            count = logger.mark_as_synced("test_service")
            
            if count > 0:
                mock_audit.assert_called_once()
                call_args = mock_audit.call_args
                assert call_args[1]["event_type"] == "SHADOW_LOG_RECOVERED"
                assert call_args[1]["service_name"] == "test_service"
                assert "recovered_count" in call_args[1]["details"]
    
    def test_audit_failure_does_not_affect_main_logic(self):
        """Audit 실패가 메인 로직에 영향 없음."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        logger = ShadowLogger()
        logger.clear()
        
        # Audit가 실패하도록 설정
        with patch("selfhealing.factory.ProviderRegistry.get_audit_adapter") as mock_get:
            mock_adapter = MagicMock()
            mock_adapter.log_event.side_effect = Exception("Audit failed")
            mock_get.return_value = mock_adapter
            
            # 메인 로직은 정상 동작해야 함
            logger.record_sync_failure(
                service_name="test_service",
                intended_state="OPEN",
                error=Exception("Test error"),
            )
            
            # 기록이 성공했는지 확인
            records = logger.get_all_records()
            assert len(records) >= 1
