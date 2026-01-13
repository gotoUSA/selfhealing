"""
Phase 6: 업계 표준 Chaos 실험 테스트

테스트 대상:
- MonotonicTTLHelper (ClockSkew 보호용)
- CertificateExpiryExperiment
- ClockSkewExperiment

Reference:
- 33_CHAOS_INDUSTRY_EXPERIMENTS.md §4, §5
- 34_CHAOS_SAFETY_MECHANISMS.md §2 Monotonic Clock 보호

작성일: 2026-01-14
"""

import time
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, MagicMock

# =============================================================================
# MonotonicTTLHelper Tests
# =============================================================================


class TestMonotonicTTLHelper:
    """Test MonotonicTTLHelper class."""
    
    def test_class_exists(self):
        """Test MonotonicTTLHelper class exists."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        assert MonotonicTTLHelper is not None
    
    def test_init_with_ttl(self):
        """Test initialization with TTL seconds."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        helper = MonotonicTTLHelper(ttl_seconds=300.0)
        
        assert helper.ttl_seconds == 300.0
        assert helper.is_started() is False
        assert helper.is_expired() is False
    
    def test_start_begins_timer(self):
        """Test start() begins the timer."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        helper = MonotonicTTLHelper(ttl_seconds=60.0)
        
        assert helper.is_started() is False
        
        helper.start()
        
        assert helper.is_started() is True
        assert helper._start_time > 0
    
    def test_elapsed_seconds_before_start(self):
        """Test elapsed_seconds() returns 0 before start."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        helper = MonotonicTTLHelper(ttl_seconds=60.0)
        
        assert helper.elapsed_seconds() == 0.0
    
    def test_elapsed_seconds_after_start(self):
        """Test elapsed_seconds() returns actual elapsed time."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        helper = MonotonicTTLHelper(ttl_seconds=60.0)
        helper.start()
        
        time.sleep(0.1)  # Wait 100ms
        
        elapsed = helper.elapsed_seconds()
        assert elapsed >= 0.1
        assert elapsed < 0.5  # Should be close to 100ms
    
    def test_remaining_seconds(self):
        """Test remaining_seconds() calculates correctly."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        helper = MonotonicTTLHelper(ttl_seconds=60.0)
        
        # Before start: full TTL remaining
        assert helper.remaining_seconds() == 60.0
        
        helper.start()
        time.sleep(0.1)
        
        remaining = helper.remaining_seconds()
        assert remaining < 60.0
        assert remaining > 59.0
    
    def test_is_expired_false_initially(self):
        """Test is_expired() returns False when not expired."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        helper = MonotonicTTLHelper(ttl_seconds=60.0)
        helper.start()
        
        assert helper.is_expired() is False
    
    def test_is_expired_true_after_ttl(self):
        """Test is_expired() returns True after TTL."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        # Very short TTL for testing
        helper = MonotonicTTLHelper(ttl_seconds=0.05)  # 50ms
        helper.start()
        
        assert helper.is_expired() is False
        
        time.sleep(0.1)  # Wait 100ms
        
        assert helper.is_expired() is True
    
    def test_reset_restarts_timer(self):
        """Test reset() restarts the timer."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        helper = MonotonicTTLHelper(ttl_seconds=60.0)
        helper.start()
        
        time.sleep(0.1)
        elapsed_before_reset = helper.elapsed_seconds()
        
        helper.reset()
        
        elapsed_after_reset = helper.elapsed_seconds()
        
        assert elapsed_before_reset > elapsed_after_reset
        assert elapsed_after_reset < 0.01  # Almost 0
    
    def test_to_dict_serialization(self):
        """Test to_dict() returns proper dictionary."""
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        helper = MonotonicTTLHelper(ttl_seconds=60.0)
        helper.start()
        
        result = helper.to_dict()
        
        assert "ttl_seconds" in result
        assert "started" in result
        assert "elapsed_seconds" in result
        assert "remaining_seconds" in result
        assert "is_expired" in result
        
        assert result["ttl_seconds"] == 60.0
        assert result["started"] is True
        assert result["is_expired"] is False
    
    def test_monotonic_unaffected_by_system_time(self):
        """
        Test that monotonic clock is unaffected by system time changes.
        
        Note: We can't actually change system time in tests,
        but we verify that time.monotonic() is used, not datetime.now().
        """
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        helper = MonotonicTTLHelper(ttl_seconds=60.0)
        helper.start()
        
        # Record monotonic time directly
        start_monotonic = time.monotonic()
        
        time.sleep(0.1)
        
        elapsed = helper.elapsed_seconds()
        expected_elapsed = time.monotonic() - start_monotonic
        
        # Should be very close (within 10ms tolerance)
        assert abs(elapsed - expected_elapsed) < 0.01


# =============================================================================
# CertificateExpiryExperiment Tests
# =============================================================================


class TestCertificateExpiryExperiment:
    """Test CertificateExpiryExperiment class."""
    
    def test_class_exists(self):
        """Test CertificateExpiryExperiment class exists."""
        from selfhealing.services.chaos.experiment_impl import CertificateExpiryExperiment
        
        assert CertificateExpiryExperiment is not None
    
    def test_experiment_type(self):
        """Test experiment_type is correct."""
        from selfhealing.services.chaos.experiment_impl import CertificateExpiryExperiment
        
        assert CertificateExpiryExperiment.experiment_type == "certificate_expiry"
    
    def test_requires_approval_false(self):
        """Test requires_approval is False (low risk)."""
        from selfhealing.services.chaos.experiment_impl import CertificateExpiryExperiment
        
        assert CertificateExpiryExperiment.requires_approval is False
    
    def test_has_failure_hypothesis(self):
        """Test experiment has failure_hypothesis."""
        from selfhealing.services.chaos.experiment_impl import (
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        experiment = CertificateExpiryExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        assert hasattr(CertificateExpiryExperiment, "failure_hypothesis")
        assert CertificateExpiryExperiment.failure_hypothesis is not None
    
    def test_simulated_days_remaining_default(self):
        """Test simulated_days_remaining default value."""
        from selfhealing.services.chaos.experiment_impl import (
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        experiment = CertificateExpiryExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        assert experiment.simulated_days_remaining == 5
    
    def test_simulated_days_remaining_from_config(self):
        """Test simulated_days_remaining from config."""
        from selfhealing.services.chaos.experiment_impl import (
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        experiment = CertificateExpiryExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"simulated_days_remaining": 3},
            )
        )
        
        assert experiment.simulated_days_remaining == 3
    
    def test_affected_endpoints_default(self):
        """Test affected_endpoints default value."""
        from selfhealing.services.chaos.experiment_impl import (
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        experiment = CertificateExpiryExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        assert experiment.affected_endpoints == []
    
    def test_affected_endpoints_from_config(self):
        """Test affected_endpoints from config."""
        from selfhealing.services.chaos.experiment_impl import (
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        endpoints = ["https://api.example.com", "https://auth.example.com"]
        experiment = CertificateExpiryExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"affected_endpoints": endpoints},
            )
        )
        
        assert experiment.affected_endpoints == endpoints
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_inject_chaos_with_endpoints(self, mock_apply):
        """Test inject_chaos with affected endpoints."""
        from selfhealing.services.chaos.experiment_impl import (
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        endpoints = ["https://api.example.com"]
        experiment = CertificateExpiryExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={
                    "simulated_days_remaining": 3,
                    "affected_endpoints": endpoints,
                },
            )
        )
        
        # Set TTL
        experiment._effective_ttl = 300
        experiment._expires_at = datetime.now(timezone.utc) + timedelta(seconds=300)
        
        result = experiment.inject_chaos()
        
        assert result is True
        mock_apply.assert_called_once()
        
        # Check config applied
        config = mock_apply.call_args[0][0]
        assert config["certificate_expiry"]["enabled"] is True
        assert config["certificate_expiry"]["simulated_days_remaining"] == 3
        assert config["certificate_expiry"]["affected_endpoints"] == endpoints
    
    def test_get_alerts_triggered(self):
        """Test get_alerts_triggered returns copy of alerts."""
        from selfhealing.services.chaos.experiment_impl import (
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        experiment = CertificateExpiryExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        alerts = experiment.get_alerts_triggered()
        
        assert isinstance(alerts, list)
        assert len(alerts) == 0
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_rollback_clears_config(self, mock_apply):
        """Test rollback clears the chaos config."""
        from selfhealing.services.chaos.experiment_impl import (
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        experiment = CertificateExpiryExperiment(
            config=ExperimentConfig(target_service="test")
        )
        experiment.experiment_id = "test-123"
        
        experiment.rollback()
        
        mock_apply.assert_called_once()
        config = mock_apply.call_args[0][0]
        assert config["certificate_expiry"]["enabled"] is False
        assert config["certificate_expiry"]["experiment_id"] == "test-123"
    
    def test_rollback_idempotent(self):
        """Test rollback is idempotent."""
        from selfhealing.services.chaos.experiment_impl import (
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        experiment = CertificateExpiryExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        with patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config") as mock:
            experiment.rollback()
            experiment.rollback()  # Second call
            experiment.rollback()  # Third call
            
            # Should only be called once
            assert mock.call_count == 1
    
    def test_create_experiment_factory(self):
        """Test create_experiment factory for certificate_expiry."""
        from selfhealing.services.chaos.experiment_impl import (
            create_experiment,
            CertificateExpiryExperiment,
            ExperimentConfig,
        )
        
        experiment = create_experiment(
            experiment_type="certificate_expiry",
            config=ExperimentConfig(target_service="test"),
        )
        
        assert isinstance(experiment, CertificateExpiryExperiment)


# =============================================================================
# ClockSkewExperiment Tests
# =============================================================================


class TestClockSkewExperiment:
    """Test ClockSkewExperiment class."""
    
    def test_class_exists(self):
        """Test ClockSkewExperiment class exists."""
        from selfhealing.services.chaos.experiment_impl import ClockSkewExperiment
        
        assert ClockSkewExperiment is not None
    
    def test_experiment_type(self):
        """Test experiment_type is correct."""
        from selfhealing.services.chaos.experiment_impl import ClockSkewExperiment
        
        assert ClockSkewExperiment.experiment_type == "clock_skew"
    
    def test_requires_approval_true(self):
        """Test requires_approval is True (high risk)."""
        from selfhealing.services.chaos.experiment_impl import ClockSkewExperiment
        
        assert ClockSkewExperiment.requires_approval is True
    
    def test_max_skew_seconds_constant(self):
        """Test MAX_SKEW_SECONDS constant exists."""
        from selfhealing.services.chaos.experiment_impl import ClockSkewExperiment
        
        assert ClockSkewExperiment.MAX_SKEW_SECONDS == 86400  # 1 day
    
    def test_skew_seconds_default(self):
        """Test skew_seconds default value."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        assert experiment.skew_seconds == 300  # 5 minutes
    
    def test_skew_seconds_from_config(self):
        """Test skew_seconds from config."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"skew_seconds": 600},
            )
        )
        
        assert experiment.skew_seconds == 600
    
    def test_skew_seconds_hard_cap_positive(self):
        """Test skew_seconds is capped at MAX_SKEW_SECONDS."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        
        # Try to set skew to 2 days (exceeds 1 day cap)
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"skew_seconds": 172800},  # 2 days
            )
        )
        
        # Should be capped at 86400 (1 day)
        assert experiment.skew_seconds == 86400
    
    def test_skew_seconds_hard_cap_negative(self):
        """Test negative skew_seconds is capped correctly."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        
        # Try to set skew to -2 days
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"skew_seconds": -172800},  # -2 days
            )
        )
        
        # Should be capped at -86400 (preserving sign)
        assert experiment.skew_seconds == -86400
    
    def test_has_failure_hypothesis(self):
        """Test experiment has failure_hypothesis."""
        from selfhealing.services.chaos.experiment_impl import ClockSkewExperiment
        
        assert hasattr(ClockSkewExperiment, "failure_hypothesis")
        assert ClockSkewExperiment.failure_hypothesis is not None
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_inject_chaos_starts_monotonic_timer(self, mock_apply):
        """Test inject_chaos starts monotonic TTL timer."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(target_service="test")
        )
        experiment._effective_ttl = 300
        experiment._expires_at = datetime.now(timezone.utc) + timedelta(seconds=300)
        
        result = experiment.inject_chaos()
        
        assert result is True
        assert experiment._monotonic_ttl is not None
        assert experiment._monotonic_ttl.is_started() is True
    
    def test_is_expired_monotonic_uses_helper(self):
        """Test is_expired_monotonic uses MonotonicTTLHelper."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        # Create helper with very short TTL
        experiment._monotonic_ttl = MonotonicTTLHelper(ttl_seconds=0.05)
        experiment._monotonic_ttl.start()
        
        assert experiment.is_expired_monotonic() is False
        
        time.sleep(0.1)
        
        assert experiment.is_expired_monotonic() is True
    
    def test_is_expired_monotonic_fallback_without_helper(self):
        """Test is_expired_monotonic falls back when no helper."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(target_service="test")
        )
        experiment._monotonic_ttl = None
        experiment._expires_at = datetime.now(timezone.utc) + timedelta(seconds=300)
        
        # Should use regular is_expired()
        result = experiment.is_expired_monotonic()
        
        assert result is False  # Not expired yet
    
    def test_get_monotonic_remaining(self):
        """Test get_monotonic_remaining returns correct value."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(target_service="test")
        )
        experiment._monotonic_ttl = MonotonicTTLHelper(ttl_seconds=60.0)
        experiment._monotonic_ttl.start()
        
        remaining = experiment.get_monotonic_remaining()
        
        assert remaining > 59.0
        assert remaining <= 60.0
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_rollback_clears_config(self, mock_apply):
        """Test rollback clears the chaos config."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        from selfhealing.services.chaos.base import MonotonicTTLHelper
        
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(target_service="test")
        )
        experiment.experiment_id = "test-123"
        experiment._monotonic_ttl = MonotonicTTLHelper(ttl_seconds=60.0)
        experiment._monotonic_ttl.start()
        
        experiment.rollback()
        
        mock_apply.assert_called_once()
        config = mock_apply.call_args[0][0]
        assert config["clock_skew"]["enabled"] is False
        assert config["clock_skew"]["experiment_id"] == "test-123"
    
    def test_rollback_idempotent(self):
        """Test rollback is idempotent."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        with patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config") as mock:
            experiment.rollback()
            experiment.rollback()  # Second call
            
            # Should only be called once
            assert mock.call_count == 1
    
    def test_create_experiment_factory(self):
        """Test create_experiment factory for clock_skew."""
        from selfhealing.services.chaos.experiment_impl import (
            create_experiment,
            ClockSkewExperiment,
            ExperimentConfig,
        )
        
        experiment = create_experiment(
            experiment_type="clock_skew",
            config=ExperimentConfig(target_service="test"),
        )
        
        assert isinstance(experiment, ClockSkewExperiment)


# =============================================================================
# ExperimentType Enum Tests
# =============================================================================


class TestExperimentTypeEnum:
    """Test ExperimentType enum additions."""
    
    def test_certificate_expiry_exists(self):
        """Test CERTIFICATE_EXPIRY exists in enum."""
        from selfhealing.services.chaos.base import ExperimentType
        
        assert hasattr(ExperimentType, "CERTIFICATE_EXPIRY")
        assert ExperimentType.CERTIFICATE_EXPIRY.value == "certificate_expiry"
    
    def test_dns_failure_exists(self):
        """Test DNS_FAILURE exists in enum."""
        from selfhealing.services.chaos.base import ExperimentType
        
        assert hasattr(ExperimentType, "DNS_FAILURE")
        assert ExperimentType.DNS_FAILURE.value == "dns_failure"
    
    def test_clock_skew_exists(self):
        """Test CLOCK_SKEW exists in enum."""
        from selfhealing.services.chaos.base import ExperimentType
        
        assert hasattr(ExperimentType, "CLOCK_SKEW")
        assert ExperimentType.CLOCK_SKEW.value == "clock_skew"


# =============================================================================
# Integration Tests
# =============================================================================


class TestMonotonicTTLIntegration:
    """Integration tests for Monotonic TTL with ClockSkewExperiment."""
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_full_lifecycle_with_monotonic_ttl(self, mock_apply):
        """Test full experiment lifecycle with monotonic TTL protection."""
        from selfhealing.services.chaos.experiment_impl import (
            ClockSkewExperiment,
            ExperimentConfig,
        )
        
        experiment = ClockSkewExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"skew_seconds": 300},
                ttl_seconds=60,
            )
        )
        
        # Inject
        experiment._effective_ttl = 60
        experiment._expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        
        inject_result = experiment.inject_chaos()
        assert inject_result is True
        
        # Verify monotonic timer started
        assert experiment._monotonic_ttl is not None
        assert experiment._monotonic_ttl.ttl_seconds == 60.0
        assert experiment._monotonic_ttl.is_started() is True
        
        # Check remaining time
        remaining = experiment.get_monotonic_remaining()
        assert remaining > 59.0
        
        # Rollback
        experiment.rollback()
        assert experiment._rollback_completed is True
