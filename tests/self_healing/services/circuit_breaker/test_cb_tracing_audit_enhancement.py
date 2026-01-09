"""
Phase 2: Circuit Breaker Audit Enhancement Tests

테스트 대상:
1. Distributed Tracing (tracing.py)
2. CB 상태 변화 Audit with trace_id
3. GOVERNANCE_BLOCKED CB Audit

Reference: docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md
Section 11.1 - Phase 2: Audit 강화
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
from typing import Optional, Dict, Any


# =============================================================================
# TracingConfig Tests
# =============================================================================


class TestTracingConfig:
    """TracingConfig 설정 테스트."""
    
    def test_default_config(self):
        """기본 설정 값 확인."""
        from selfhealing.services.circuit_breaker.tracing import TracingConfig
        
        config = TracingConfig()
        
        assert config.enabled is True
        assert config.record_triggering_request is True
        assert config.create_spans is True
        assert "X-Trace-ID" in config.captured_headers
        assert "traceparent" in config.captured_headers
    
    def test_config_from_env(self):
        """환경변수에서 설정 로드 테스트."""
        from selfhealing.services.circuit_breaker.tracing import TracingConfig
        
        with patch.dict('os.environ', {
            'CB_TRACING_ENABLED': 'false',
            'CB_TRACING_RECORD_REQUEST': 'false',
            'CB_TRACE_URL_TEMPLATE': 'https://jaeger.example.com/trace/{trace_id}',
        }):
            config = TracingConfig.from_env()
            
            assert config.enabled is False
            assert config.record_triggering_request is False
            assert config.trace_url_template == 'https://jaeger.example.com/trace/{trace_id}'


# =============================================================================
# TriggeringRequestInfo Tests
# =============================================================================


class TestTriggeringRequestInfo:
    """TriggeringRequestInfo 데이터 모델 테스트."""
    
    def test_create_triggering_request_info(self):
        """TriggeringRequestInfo 생성 테스트."""
        from selfhealing.services.circuit_breaker.tracing import TriggeringRequestInfo
        
        info = TriggeringRequestInfo(
            trace_id="req-abc123",
            span_id="span456",
            request_id="request789",
            endpoint="/api/v1/payments",
            method="POST",
            error_message="Connection timeout",
            trace_url="https://jaeger.example.com/trace/abc123",
        )
        
        assert info.trace_id == "req-abc123"
        assert info.span_id == "span456"
        assert info.endpoint == "/api/v1/payments"
        assert info.method == "POST"
        assert info.error_message == "Connection timeout"
        assert info.trace_url is not None
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트."""
        from selfhealing.services.circuit_breaker.tracing import TriggeringRequestInfo
        
        info = TriggeringRequestInfo(
            trace_id="req-abc123",
            endpoint="/api/v1/payments",
            method="POST",
        )
        
        data = info.to_dict()
        
        assert data["trace_id"] == "req-abc123"
        assert data["endpoint"] == "/api/v1/payments"
        assert data["method"] == "POST"
        assert "timestamp" in data
    
    def test_from_dict(self):
        """딕셔너리에서 생성 테스트."""
        from selfhealing.services.circuit_breaker.tracing import TriggeringRequestInfo
        
        data = {
            "trace_id": "req-xyz789",
            "endpoint": "/api/orders",
            "method": "GET",
            "error_message": "Service unavailable",
        }
        
        info = TriggeringRequestInfo.from_dict(data)
        
        assert info.trace_id == "req-xyz789"
        assert info.endpoint == "/api/orders"
        assert info.error_message == "Service unavailable"


# =============================================================================
# TraceContextProvider Tests
# =============================================================================


class TestTraceContextProvider:
    """TraceContextProvider 테스트."""
    
    def test_get_current_context(self):
        """현재 context 조회 테스트."""
        from selfhealing.services.circuit_breaker.tracing import TraceContextProvider
        
        provider = TraceContextProvider()
        ctx = provider.get_current_context()
        
        assert ctx.trace_id is not None
        assert ctx.trace_id.startswith("req-")
    
    def test_extract_from_request_with_x_trace_id(self):
        """Django request에서 X-Trace-ID 추출 테스트."""
        from selfhealing.services.circuit_breaker.tracing import TraceContextProvider
        
        mock_request = Mock()
        mock_request.META = {
            "HTTP_X_TRACE_ID": "custom-trace-123",
        }
        mock_request.path = "/api/v1/payments"
        mock_request.method = "POST"
        
        provider = TraceContextProvider()
        
        with patch('selfhealing.services.circuit_breaker.tracing.TraceContextProvider._extract_trace_id_from_request') as mock_extract:
            mock_extract.return_value = "custom-trace-123"
            info = provider.extract_from_request(
                request=mock_request,
                error_message="Test error",
            )
        
        assert info.endpoint == "/api/v1/payments"
        assert info.method == "POST"
        assert info.error_message == "Test error"
    
    def test_extract_from_request_with_traceparent(self):
        """W3C traceparent 헤더에서 span_id 추출 테스트."""
        from selfhealing.services.circuit_breaker.tracing import TraceContextProvider
        
        mock_request = Mock()
        mock_request.META = {
            "HTTP_TRACEPARENT": "00-abc123def456-span789xyz-01",
        }
        mock_request.path = "/api/test"
        mock_request.method = "GET"
        
        provider = TraceContextProvider()
        span_id = provider._extract_span_id_from_request(mock_request)
        
        assert span_id == "span789xyz"
    
    def test_build_trace_url(self):
        """Trace URL 생성 테스트."""
        from selfhealing.services.circuit_breaker.tracing import (
            TraceContextProvider, TracingConfig
        )
        
        config = TracingConfig(
            trace_url_template="https://jaeger.example.com/trace/{trace_id}"
        )
        provider = TraceContextProvider(config)
        
        url = provider._build_trace_url("abc123")
        
        assert url == "https://jaeger.example.com/trace/abc123"
    
    def test_build_trace_url_no_template(self):
        """Trace URL 템플릿 없을 때 테스트."""
        from selfhealing.services.circuit_breaker.tracing import (
            TraceContextProvider, TracingConfig
        )
        
        config = TracingConfig(trace_url_template="")
        provider = TraceContextProvider(config)
        
        url = provider._build_trace_url("abc123")
        
        assert url is None


# =============================================================================
# CircuitBreakerTracingManager Tests
# =============================================================================


class TestCircuitBreakerTracingManager:
    """CircuitBreakerTracingManager 테스트."""
    
    def setup_method(self):
        """테스트 전 인스턴스 리셋."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        CircuitBreakerTracingManager.reset_instance()
    
    def teardown_method(self):
        """테스트 후 인스턴스 리셋."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        CircuitBreakerTracingManager.reset_instance()
    
    def test_singleton_pattern(self):
        """싱글톤 패턴 테스트."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        
        manager1 = CircuitBreakerTracingManager()
        manager2 = CircuitBreakerTracingManager()
        
        assert manager1 is manager2
    
    def test_record_failure_with_trace(self):
        """실패 기록 시 trace 정보 저장 테스트."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        
        manager = CircuitBreakerTracingManager()
        
        error = Exception("Connection timeout")
        info = manager.record_failure_with_trace(
            service_id="payment-api",
            error=error,
            endpoint="/api/v1/payments",
            method="POST",
        )
        
        assert info.trace_id is not None
        assert info.error_message == "Connection timeout"
        assert info.endpoint == "/api/v1/payments"
        assert info.method == "POST"
        
        # 저장된 정보 조회
        stored_info = manager.get_triggering_request("payment-api")
        assert stored_info is not None
        assert stored_info.trace_id == info.trace_id
    
    def test_get_triggering_request_not_found(self):
        """존재하지 않는 서비스 조회 테스트."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        
        manager = CircuitBreakerTracingManager()
        
        info = manager.get_triggering_request("nonexistent-service")
        
        assert info is None
    
    def test_clear_triggering_request(self):
        """triggering request 삭제 테스트."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        
        manager = CircuitBreakerTracingManager()
        
        # 기록
        manager.record_failure_with_trace(
            service_id="payment-api",
            error=Exception("Test"),
        )
        
        # 삭제
        manager.clear_triggering_request("payment-api")
        
        # 조회 시 None
        assert manager.get_triggering_request("payment-api") is None
    
    def test_record_failure_when_disabled(self):
        """Tracing 비활성화 시 테스트."""
        from selfhealing.services.circuit_breaker.tracing import (
            CircuitBreakerTracingManager, TracingConfig
        )
        
        # 리셋 후 비활성화 config로 생성
        CircuitBreakerTracingManager.reset_instance()
        
        # 새 인스턴스에 비활성화 config 설정
        manager = CircuitBreakerTracingManager()
        manager.config = TracingConfig(enabled=False)
        
        info = manager.record_failure_with_trace(
            service_id="payment-api",
            error=Exception("Test"),
        )
        
        assert info.trace_id == "disabled"


# =============================================================================
# Audit Helper Tests - log_cb_state_change_with_trace_audit
# =============================================================================


class TestLogCbStateChangeWithTraceAudit:
    """log_cb_state_change_with_trace_audit 함수 테스트."""
    
    @patch('selfhealing.services.audit_helpers._write_to_wal')
    def test_log_state_change_with_trace(self, mock_wal):
        """trace 정보 포함 상태 변경 로그 테스트."""
        from selfhealing.services.audit_helpers import log_cb_state_change_with_trace_audit
        
        mock_wal.return_value = 12345
        
        triggering_info = {
            "trace_id": "req-abc123",
            "endpoint": "/api/v1/payments",
            "method": "POST",
            "error_message": "Connection timeout",
        }
        
        wal_seq = log_cb_state_change_with_trace_audit(
            cb_name="payment-api",
            old_state="CLOSED",
            new_state="OPEN",
            trigger="AUTO_THRESHOLD",
            reason="Failure threshold exceeded",
            trace_id="req-abc123",
            triggering_request_info=triggering_info,
        )
        
        assert wal_seq == 12345
        
        # WAL 호출 확인
        mock_wal.assert_called_once()
        call_kwargs = mock_wal.call_args[1]
        assert call_kwargs["event_type"] == "CB_STATE_CHANGE_WITH_TRACE"
        assert call_kwargs["source"] == "CircuitBreakerTracing"
        
        details = call_kwargs["details"]
        assert details["cb_name"] == "payment-api"
        assert details["old_state"] == "CLOSED"
        assert details["new_state"] == "OPEN"
        assert details["trace_id"] == "req-abc123"
        assert details["triggering_request"] == triggering_info
        assert "debug_hint" in details
    
    @patch('selfhealing.services.audit_helpers._write_to_wal')
    def test_log_state_change_without_trace(self, mock_wal):
        """trace 정보 없이 상태 변경 로그 테스트."""
        from selfhealing.services.audit_helpers import log_cb_state_change_with_trace_audit
        
        mock_wal.return_value = 12346
        
        wal_seq = log_cb_state_change_with_trace_audit(
            cb_name="order-api",
            old_state="OPEN",
            new_state="CLOSED",
            trigger="MANUAL",
            reason="Service recovered",
        )
        
        assert wal_seq == 12346
        
        call_kwargs = mock_wal.call_args[1]
        details = call_kwargs["details"]
        assert "trace_id" not in details  # None 값은 제거됨
        assert "triggering_request" not in details


# =============================================================================
# Audit Helper Tests - log_governance_blocked_cb_audit
# =============================================================================


class TestLogGovernanceBlockedCbAudit:
    """log_governance_blocked_cb_audit 함수 테스트."""
    
    @patch('selfhealing.services.audit_helpers._write_to_wal')
    def test_log_governance_blocked_blast_radius(self, mock_wal):
        """Blast Radius CRITICAL로 인한 GOVERNANCE_BLOCKED 로그 테스트."""
        from selfhealing.services.audit_helpers import log_governance_blocked_cb_audit
        
        mock_wal.return_value = 12347
        
        wal_seq = log_governance_blocked_cb_audit(
            service_id="payment-api",
            action="auto_open",
            block_reason="blast_radius_critical",
            blast_radius_level="CRITICAL",
            affected_services=["order-api", "cart-api", "inventory-api"],
            assessment_id="assessment-123",
            cascading_risk=True,
            trace_id="req-xyz789",
            requires_manual_approval=True,
        )
        
        assert wal_seq == 12347
        
        # WAL 호출 확인
        mock_wal.assert_called_once()
        call_kwargs = mock_wal.call_args[1]
        assert call_kwargs["event_type"] == "GOVERNANCE_BLOCKED"
        assert call_kwargs["source"] == "CircuitBreakerBlastRadius"
        assert call_kwargs["target_id"] == "payment-api"
        
        details = call_kwargs["details"]
        assert details["action"] == "auto_open"
        assert details["blocked_reason"] == "blast_radius_critical"
        assert details["blast_radius_level"] == "CRITICAL"
        assert details["affected_services"] == ["order-api", "cart-api", "inventory-api"]
        assert details["affected_count"] == 3
        assert details["cascading_risk"] is True
        assert details["assessment_id"] == "assessment-123"
        assert "message" in details
        assert "Blast Radius: CRITICAL" in details["message"]
    
    @patch('selfhealing.services.audit_helpers._write_to_wal')
    def test_log_governance_blocked_no_affected_services(self, mock_wal):
        """영향받는 서비스 없는 GOVERNANCE_BLOCKED 로그 테스트."""
        from selfhealing.services.audit_helpers import log_governance_blocked_cb_audit
        
        mock_wal.return_value = 12348
        
        wal_seq = log_governance_blocked_cb_audit(
            service_id="review-api",
            action="auto_open",
            block_reason="freeze_mode_active",
        )
        
        assert wal_seq == 12348
        
        call_kwargs = mock_wal.call_args[1]
        details = call_kwargs["details"]
        assert details["affected_count"] == 0


# =============================================================================
# Convenience Functions Tests
# =============================================================================


class TestConvenienceFunctions:
    """편의 함수 테스트."""
    
    def setup_method(self):
        """테스트 전 인스턴스 리셋."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        CircuitBreakerTracingManager.reset_instance()
    
    def teardown_method(self):
        """테스트 후 인스턴스 리셋."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        CircuitBreakerTracingManager.reset_instance()
    
    def test_get_tracing_manager(self):
        """get_tracing_manager 함수 테스트."""
        from selfhealing.services.circuit_breaker.tracing import (
            get_tracing_manager, CircuitBreakerTracingManager
        )
        
        manager = get_tracing_manager()
        
        assert isinstance(manager, CircuitBreakerTracingManager)
    
    def test_record_failure_with_trace_function(self):
        """record_failure_with_trace 편의 함수 테스트."""
        from selfhealing.services.circuit_breaker.tracing import record_failure_with_trace
        
        info = record_failure_with_trace(
            service_id="test-service",
            error=Exception("Test error"),
            endpoint="/test",
            method="GET",
        )
        
        assert info.trace_id is not None
        assert info.error_message == "Test error"
    
    def test_get_triggering_request_function(self):
        """get_triggering_request 편의 함수 테스트."""
        from selfhealing.services.circuit_breaker.tracing import (
            record_failure_with_trace, get_triggering_request
        )
        
        # 기록
        record_failure_with_trace(
            service_id="test-service",
            error=Exception("Test"),
        )
        
        # 조회
        info = get_triggering_request("test-service")
        
        assert info is not None
        assert info.trace_id is not None


# =============================================================================
# Integration Tests
# =============================================================================


class TestTracingIntegration:
    """Tracing 통합 테스트."""
    
    def setup_method(self):
        """테스트 전 인스턴스 리셋."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        CircuitBreakerTracingManager.reset_instance()
    
    def teardown_method(self):
        """테스트 후 인스턴스 리셋."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        CircuitBreakerTracingManager.reset_instance()
    
    @patch('selfhealing.services.audit_helpers._write_to_wal')
    def test_full_tracing_flow(self, mock_wal):
        """전체 Tracing 플로우 테스트: 실패 기록 → 상태 변경 로그."""
        from selfhealing.services.circuit_breaker.tracing import (
            CircuitBreakerTracingManager, get_tracing_manager
        )
        
        mock_wal.return_value = 12349
        
        manager = get_tracing_manager()
        
        # 1. 실패 기록 시 trace 정보 저장
        failure_info = manager.record_failure_with_trace(
            service_id="payment-api",
            error=Exception("Gateway timeout"),
            endpoint="/api/v1/payments/charge",
            method="POST",
        )
        
        # 2. 저장된 triggering request 확인
        stored_info = manager.get_triggering_request("payment-api")
        assert stored_info is not None
        assert stored_info.trace_id == failure_info.trace_id
        
        # 3. 상태 변경 로그 기록 (triggering request 포함)
        wal_seq = manager.log_state_change_with_trace(
            service_id="payment-api",
            previous_state="CLOSED",
            new_state="OPEN",
            trigger="AUTO_THRESHOLD",
            reason="5 consecutive failures",
        )
        
        # 4. 결과 확인
        assert wal_seq == 12349
    
    def test_tracing_export_from_module(self):
        """모듈에서 Tracing 관련 클래스/함수 export 확인."""
        from selfhealing.services.circuit_breaker import (
            TracingConfig,
            TriggeringRequestInfo,
            TraceContextProvider,
            CircuitBreakerTracingManager,
            get_tracing_manager,
            record_failure_with_trace,
            get_triggering_request,
            log_state_change_with_trace,
        )
        
        assert TracingConfig is not None
        assert TriggeringRequestInfo is not None
        assert TraceContextProvider is not None
        assert CircuitBreakerTracingManager is not None
        assert callable(get_tracing_manager)
        assert callable(record_failure_with_trace)
        assert callable(get_triggering_request)
        assert callable(log_state_change_with_trace)


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """엣지 케이스 테스트."""
    
    def setup_method(self):
        """테스트 전 인스턴스 리셋."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        CircuitBreakerTracingManager.reset_instance()
    
    def teardown_method(self):
        """테스트 후 인스턴스 리셋."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        CircuitBreakerTracingManager.reset_instance()
    
    def test_record_failure_without_error(self):
        """에러 객체 없이 실패 기록 테스트."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        
        manager = CircuitBreakerTracingManager()
        
        info = manager.record_failure_with_trace(
            service_id="test-api",
            error=None,
            endpoint="/test",
        )
        
        assert info.trace_id is not None
        assert info.error_message is None
    
    def test_record_failure_without_request(self):
        """request 객체 없이 실패 기록 테스트."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        
        manager = CircuitBreakerTracingManager()
        
        info = manager.record_failure_with_trace(
            service_id="test-api",
            error=Exception("Test"),
            request=None,
        )
        
        assert info.trace_id is not None
    
    def test_extract_from_request_with_none(self):
        """None request에서 추출 테스트."""
        from selfhealing.services.circuit_breaker.tracing import TraceContextProvider
        
        provider = TraceContextProvider()
        
        result = provider._extract_header(None, "HTTP_X_TRACE_ID")
        
        assert result is None
    
    def test_multiple_services_independent_tracking(self):
        """여러 서비스의 독립적인 tracking 테스트."""
        from selfhealing.services.circuit_breaker.tracing import CircuitBreakerTracingManager
        
        manager = CircuitBreakerTracingManager()
        
        # 서비스 1 실패 기록
        info1 = manager.record_failure_with_trace(
            service_id="service-1",
            error=Exception("Error 1"),
        )
        
        # 서비스 2 실패 기록
        info2 = manager.record_failure_with_trace(
            service_id="service-2",
            error=Exception("Error 2"),
        )
        
        # 각 서비스 독립적으로 조회
        stored1 = manager.get_triggering_request("service-1")
        stored2 = manager.get_triggering_request("service-2")
        
        assert stored1 is not None
        assert stored2 is not None
        assert stored1.error_message == "Error 1"
        assert stored2.error_message == "Error 2"
        
        # 서비스 1 삭제 후에도 서비스 2는 유지
        manager.clear_triggering_request("service-1")
        
        assert manager.get_triggering_request("service-1") is None
        assert manager.get_triggering_request("service-2") is not None
