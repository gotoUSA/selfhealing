"""
GateFaultDetector (Circuit Breaker) 테스트.

Fault Detector 상태 전환 및 Gate 통합 테스트.
"""

import pytest
from unittest.mock import patch


class TestInMemoryCircuitBreaker:
    """GateFaultDetector 테스트 (하위 호환성을 위해 클래스명 유지)."""
    
    def test_circuit_breaker_initial_state(self):
        """초기 상태 HEALTHY."""
        from selfhealing.services.error_budget_gate import GateFaultDetector, GateFaultState
        
        cb = GateFaultDetector(failure_threshold=3, recovery_timeout=30)
        
        status = cb.get_status()
        assert status["state"] == GateFaultState.HEALTHY.value
        assert status["failure_count"] == 0
    
    def test_circuit_breaker_opens_on_threshold(self):
        """실패 임계값 초과 시 DEGRADED 상태 전환."""
        from selfhealing.services.error_budget_gate import GateFaultDetector, GateFaultState
        
        cb = GateFaultDetector(failure_threshold=3, recovery_timeout=30)
        
        # 3회 실패
        cb.record_failure()
        cb.record_failure()
        assert cb.can_execute() is True  # 아직 CLOSED
        
        cb.record_failure()  # 3번째 실패
        assert cb.can_execute() is False  # DEGRADED
        
        status = cb.get_status()
        assert status["state"] == GateFaultState.DEGRADED.value
    
    def test_circuit_breaker_success_resets(self):
        """성공 시 실패 카운트 리셋."""
        from selfhealing.services.error_budget_gate import GateFaultDetector
        
        cb = GateFaultDetector(failure_threshold=3, recovery_timeout=30)
        
        cb.record_failure()
        cb.record_failure()
        
        cb.record_success()  # 성공 시 리셋
        
        status = cb.get_status()
        assert status["failure_count"] == 0
    
    def test_circuit_breaker_reset(self):
        """Gate Fault Detector 리셋."""
        from selfhealing.services.error_budget_gate import GateFaultDetector, GateFaultState
        
        cb = GateFaultDetector(failure_threshold=2, recovery_timeout=30)
        
        cb.record_failure()
        cb.record_failure()
        assert cb.can_execute() is False
        
        cb.reset()
        
        assert cb.can_execute() is True
        assert cb.get_status()["state"] == GateFaultState.HEALTHY.value
    
    def test_circuit_breaker_config_update(self):
        """설정 동적 업데이트."""
        from selfhealing.services.error_budget_gate import GateFaultDetector
        
        cb = GateFaultDetector(failure_threshold=3, recovery_timeout=30)
        
        cb.update_config(failure_threshold=5, recovery_timeout=60)
        
        status = cb.get_status()
        assert status["failure_threshold"] == 5
        assert status["recovery_timeout"] == 60


class TestGateCircuitBreakerIntegration:
    """Gate와 Circuit Breaker (Fault Detector) 통합 테스트."""
    
    def test_gate_fault_detector_status(self):
        """
        Gate에서 Fault Detector 상태 조회.
        
        Uses get_fault_detector_status() instead of deprecated get_circuit_breaker_status().
        """
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=5,
            circuit_breaker_recovery_timeout=30,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Use new method get_fault_detector_status() instead of deprecated get_circuit_breaker_status()
        cb_status = gate.get_fault_detector_status()
        
        assert cb_status["enabled"] is True
        assert cb_status["failure_threshold"] == 5
        assert cb_status["state"] == "healthy"  # GateFaultState.HEALTHY
    
    def test_gate_reset_fault_detector(self):
        """
        Gate에서 Fault Detector 리셋.
        
        Uses reset_fault_detector() instead of deprecated reset_circuit_breaker().
        """
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=2,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Fault Detector 트리거 (강제 실패)
        gate._fault_detector.record_failure()
        gate._fault_detector.record_failure()
        
        # Use new method get_fault_detector_status()
        assert gate.get_fault_detector_status()["state"] == "degraded"  # GateFaultState.DEGRADED
        
        # 리셋 - Use new method reset_fault_detector()
        gate.reset_fault_detector()
        
        assert gate.get_fault_detector_status()["state"] == "healthy"  # GateFaultState.HEALTHY
