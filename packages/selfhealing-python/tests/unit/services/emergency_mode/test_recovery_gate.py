"""
Tests for Recovery Gate

Covers:
- RecoveryGate class
- Recovery checks
- Gradual recovery levels
"""



class TestRecoveryGateInit:
    """Tests for RecoveryGate initialization."""

    def test_init_with_defaults(self):
        """Test initialization with defaults."""
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        gate = RecoveryGate()

        assert gate.config is not None

    def test_init_with_custom_config(self):
        """Test initialization with custom config."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        config = RecoveryGateConfig(cpu_threshold_percent=70.0)
        gate = RecoveryGate(config=config)

        assert gate.config.cpu_threshold_percent == 70.0

    def test_init_with_metrics_checker(self):
        """Test initialization with custom metrics checker."""
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        def custom_checker():
            return {"cpu_percent": 50.0, "error_rate": 0.01}

        gate = RecoveryGate(metrics_checker=custom_checker)

        assert gate._metrics_checker is custom_checker


class TestCheckRecoveryAllowed:
    """Tests for check_recovery_allowed method."""

    def test_allowed_when_metrics_stable(self):
        """Test recovery allowed when metrics stable."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        def good_metrics():
            return {"cpu_percent": 50.0, "error_rate": 0.01}

        config = RecoveryGateConfig(
            cpu_threshold_percent=80.0,
            error_rate_threshold=0.05,
        )
        gate = RecoveryGate(config=config, metrics_checker=good_metrics)

        allowed, reason = gate.check_recovery_allowed()

        assert allowed is True
        assert "within thresholds" in reason.lower()

    def test_blocked_when_cpu_high(self):
        """Test recovery blocked when CPU too high."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        def high_cpu_metrics():
            return {"cpu_percent": 90.0, "error_rate": 0.01}

        config = RecoveryGateConfig(cpu_threshold_percent=80.0)
        gate = RecoveryGate(config=config, metrics_checker=high_cpu_metrics)

        allowed, reason = gate.check_recovery_allowed()

        assert allowed is False
        assert "cpu" in reason.lower()

    def test_blocked_when_error_rate_high(self):
        """Test recovery blocked when error rate too high."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        def high_error_metrics():
            return {"cpu_percent": 50.0, "error_rate": 0.10}

        config = RecoveryGateConfig(error_rate_threshold=0.05)
        gate = RecoveryGate(config=config, metrics_checker=high_error_metrics)

        allowed, reason = gate.check_recovery_allowed()

        assert allowed is False
        assert "error rate" in reason.lower()

    def test_allowed_when_metrics_check_disabled(self):
        """Test recovery allowed when metrics check disabled."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        config = RecoveryGateConfig(require_metrics_stable=False)
        gate = RecoveryGate(config=config)

        allowed, reason = gate.check_recovery_allowed()

        assert allowed is True
        assert "disabled" in reason.lower()

    def test_blocked_on_metrics_checker_failure(self):
        """Test blocked when metrics checker fails."""
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        def failing_checker():
            raise Exception("Prometheus unavailable")

        gate = RecoveryGate(metrics_checker=failing_checker)

        allowed, reason = gate.check_recovery_allowed()

        assert allowed is False
        assert "failed" in reason.lower()


class TestGetNextRecoveryLevel:
    """Tests for get_next_recovery_level method."""

    def test_level_3_to_level_2(self):
        """Test recovery from LEVEL_3 to LEVEL_2."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        gate = RecoveryGate()

        next_level = gate.get_next_recovery_level(EmergencyLevel.LEVEL_3)

        assert next_level == EmergencyLevel.LEVEL_2

    def test_level_2_to_level_1(self):
        """Test recovery from LEVEL_2 to LEVEL_1."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        gate = RecoveryGate()

        next_level = gate.get_next_recovery_level(EmergencyLevel.LEVEL_2)

        assert next_level == EmergencyLevel.LEVEL_1

    def test_level_1_to_normal(self):
        """Test recovery from LEVEL_1 to NORMAL."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        gate = RecoveryGate()

        next_level = gate.get_next_recovery_level(EmergencyLevel.LEVEL_1)

        assert next_level == EmergencyLevel.NORMAL

    def test_normal_returns_none(self):
        """Test NORMAL level returns None."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        gate = RecoveryGate()

        next_level = gate.get_next_recovery_level(EmergencyLevel.NORMAL)

        assert next_level is None


class TestRecoveryGateEdgeCases:
    """Edge case tests for RecoveryGate."""

    def test_missing_cpu_metric(self):
        """Test handling of missing CPU metric."""
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        def incomplete_metrics():
            return {"error_rate": 0.01}  # No CPU

        gate = RecoveryGate(metrics_checker=incomplete_metrics)

        # Should handle gracefully
        allowed, reason = gate.check_recovery_allowed()
        assert isinstance(allowed, bool)

    def test_missing_error_rate_metric(self):
        """Test handling of missing error rate metric."""
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        def incomplete_metrics():
            return {"cpu_percent": 50.0}  # No error_rate

        gate = RecoveryGate(metrics_checker=incomplete_metrics)

        allowed, reason = gate.check_recovery_allowed()
        assert isinstance(allowed, bool)

    def test_boundary_cpu_value(self):
        """Test CPU exactly at threshold."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        def boundary_metrics():
            return {"cpu_percent": 80.0, "error_rate": 0.01}

        config = RecoveryGateConfig(cpu_threshold_percent=80.0)
        gate = RecoveryGate(config=config, metrics_checker=boundary_metrics)

        allowed, reason = gate.check_recovery_allowed()
        # At threshold should still be allowed (not greater than)
        assert allowed is True

    def test_boundary_error_rate_value(self):
        """Test error rate exactly at threshold."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig
        from selfhealing.services.emergency_mode.recovery_gate import RecoveryGate

        def boundary_metrics():
            return {"cpu_percent": 50.0, "error_rate": 0.05}

        config = RecoveryGateConfig(error_rate_threshold=0.05)
        gate = RecoveryGate(config=config, metrics_checker=boundary_metrics)

        allowed, reason = gate.check_recovery_allowed()
        # At threshold should still be allowed
        assert allowed is True
