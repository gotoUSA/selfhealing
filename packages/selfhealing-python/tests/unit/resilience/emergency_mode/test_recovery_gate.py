"""
RecoveryGate Tests.

Tests for RecoveryGate functionality.
"""

import pytest


class TestRecoveryGate:
    """RecoveryGate 테스트."""
    
    def test_check_recovery_allowed_when_metrics_check_disabled(self):
        """메트릭 확인이 비활성화되면 항상 허용해야 함."""
        from selfhealing.services.emergency_mode import RecoveryGateConfig, RecoveryGate
        
        config = RecoveryGateConfig(require_metrics_stable=False)
        gate = RecoveryGate(config=config)
        
        allowed, reason = gate.check_recovery_allowed()
        assert allowed is True
        assert "disabled" in reason.lower()
    
    def test_check_recovery_allowed_with_good_metrics(self):
        """메트릭이 양호하면 복구를 허용해야 함."""
        from selfhealing.services.emergency_mode import RecoveryGateConfig, RecoveryGate
        
        def good_metrics():
            return {"cpu_percent": 50.0, "error_rate": 0.01}
        
        config = RecoveryGateConfig(
            require_metrics_stable=True,
            cpu_threshold_percent=80.0,
            error_rate_threshold=0.05,
        )
        gate = RecoveryGate(config=config, metrics_checker=good_metrics)
        
        allowed, reason = gate.check_recovery_allowed()
        assert allowed is True
    
    def test_check_recovery_blocked_by_high_cpu(self):
        """CPU가 높으면 복구를 차단해야 함."""
        from selfhealing.services.emergency_mode import RecoveryGateConfig, RecoveryGate
        
        def high_cpu_metrics():
            return {"cpu_percent": 95.0, "error_rate": 0.01}
        
        config = RecoveryGateConfig(
            require_metrics_stable=True,
            cpu_threshold_percent=80.0,
        )
        gate = RecoveryGate(config=config, metrics_checker=high_cpu_metrics)
        
        allowed, reason = gate.check_recovery_allowed()
        assert allowed is False
        assert "CPU" in reason
    
    def test_check_recovery_blocked_by_high_error_rate(self):
        """에러율이 높으면 복구를 차단해야 함."""
        from selfhealing.services.emergency_mode import RecoveryGateConfig, RecoveryGate
        
        def high_error_metrics():
            return {"cpu_percent": 50.0, "error_rate": 0.10}
        
        config = RecoveryGateConfig(
            require_metrics_stable=True,
            error_rate_threshold=0.05,
        )
        gate = RecoveryGate(config=config, metrics_checker=high_error_metrics)
        
        allowed, reason = gate.check_recovery_allowed()
        assert allowed is False
        assert "Error" in reason
    
    def test_get_next_recovery_level(self):
        """점진적 복구에서 다음 레벨이 올바르게 반환되어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, RecoveryGate
        
        gate = RecoveryGate()
        
        assert gate.get_next_recovery_level(EmergencyLevel.LEVEL_3) == EmergencyLevel.LEVEL_2
        assert gate.get_next_recovery_level(EmergencyLevel.LEVEL_2) == EmergencyLevel.LEVEL_1
        assert gate.get_next_recovery_level(EmergencyLevel.LEVEL_1) == EmergencyLevel.NORMAL
        assert gate.get_next_recovery_level(EmergencyLevel.NORMAL) is None
