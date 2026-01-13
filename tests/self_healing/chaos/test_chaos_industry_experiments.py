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
        
        time.sleep(0.15)  # Wait 150ms for timing tolerance
        
        elapsed = helper.elapsed_seconds()
        assert elapsed >= 0.1  # At least 100ms
        assert elapsed < 1.0  # Should be well under 1 second
    
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


# =============================================================================
# Phase 3-4 Experiment Tests (New)
# Reference: 33_CHAOS_INDUSTRY_EXPERIMENTS.md §2-6
# =============================================================================


class TestDNSFailureExperiment:
    """Test DNSFailureExperiment class."""
    
    def test_class_exists(self):
        """Test DNSFailureExperiment class exists."""
        from selfhealing.services.chaos.experiment_impl import DNSFailureExperiment
        
        assert DNSFailureExperiment is not None
    
    def test_experiment_type(self):
        """Test experiment type is dns_failure."""
        from selfhealing.services.chaos.experiment_impl import DNSFailureExperiment
        
        assert DNSFailureExperiment.experiment_type == "dns_failure"
    
    def test_requires_approval(self):
        """Test requires_approval is True (network-wide impact)."""
        from selfhealing.services.chaos.experiment_impl import DNSFailureExperiment
        
        assert DNSFailureExperiment.requires_approval is True
    
    def test_failure_hypothesis_exists(self):
        """Test failure_hypothesis is defined."""
        from selfhealing.services.chaos.experiment_impl import DNSFailureExperiment
        
        assert hasattr(DNSFailureExperiment, "failure_hypothesis")
        assert DNSFailureExperiment.failure_hypothesis is not None
    
    def test_default_failure_mode(self):
        """Test default failure_mode is timeout."""
        from selfhealing.services.chaos.experiment_impl import (
            DNSFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = DNSFailureExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        assert experiment.failure_mode == "timeout"
    
    def test_custom_failure_mode(self):
        """Test custom failure_mode from config."""
        from selfhealing.services.chaos.experiment_impl import (
            DNSFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = DNSFailureExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"failure_mode": "nxdomain"},
            )
        )
        
        assert experiment.failure_mode == "nxdomain"
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_inject_chaos(self, mock_apply):
        """Test inject_chaos applies config."""
        from selfhealing.services.chaos.experiment_impl import (
            DNSFailureExperiment,
            ExperimentConfig,
        )
        from datetime import datetime, timezone
        
        experiment = DNSFailureExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={
                    "failure_mode": "nxdomain",
                    "affected_domains": ["api.example.com"],
                },
            )
        )
        experiment._effective_ttl = 60
        experiment._expires_at = datetime.now(timezone.utc)
        
        result = experiment.inject_chaos()
        
        assert result is True
        mock_apply.assert_called_once()
        call_args = mock_apply.call_args[0][0]
        assert "dns_failure" in call_args
        assert call_args["dns_failure"]["enabled"] is True
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_rollback(self, mock_apply):
        """Test rollback disables config."""
        from selfhealing.services.chaos.experiment_impl import (
            DNSFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = DNSFailureExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        experiment.rollback()
        
        mock_apply.assert_called_once()
        call_args = mock_apply.call_args[0][0]
        assert call_args["dns_failure"]["enabled"] is False
    
    def test_create_experiment_factory(self):
        """Test create_experiment factory for dns_failure."""
        from selfhealing.services.chaos.experiment_impl import (
            create_experiment,
            DNSFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = create_experiment(
            experiment_type="dns_failure",
            config=ExperimentConfig(target_service="test"),
        )
        
        assert isinstance(experiment, DNSFailureExperiment)


class TestNetworkBlackholeExperiment:
    """Test NetworkBlackholeExperiment class."""
    
    def test_class_exists(self):
        """Test NetworkBlackholeExperiment class exists."""
        from selfhealing.services.chaos.experiment_impl import NetworkBlackholeExperiment
        
        assert NetworkBlackholeExperiment is not None
    
    def test_experiment_type(self):
        """Test experiment type is network_blackhole."""
        from selfhealing.services.chaos.experiment_impl import NetworkBlackholeExperiment
        
        assert NetworkBlackholeExperiment.experiment_type == "network_blackhole"
    
    def test_requires_approval(self):
        """Test requires_approval is True (high risk)."""
        from selfhealing.services.chaos.experiment_impl import NetworkBlackholeExperiment
        
        assert NetworkBlackholeExperiment.requires_approval is True
    
    def test_hard_cap_max_duration(self):
        """Test MAX_DURATION_SECONDS hard cap is 300."""
        from selfhealing.services.chaos.experiment_impl import NetworkBlackholeExperiment
        
        assert NetworkBlackholeExperiment.MAX_DURATION_SECONDS == 300
    
    def test_duration_seconds_capped(self):
        """Test duration_seconds is capped at MAX_DURATION_SECONDS."""
        from selfhealing.services.chaos.experiment_impl import (
            NetworkBlackholeExperiment,
            ExperimentConfig,
        )
        
        experiment = NetworkBlackholeExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"duration_seconds": 1000},  # Over cap
            )
        )
        
        assert experiment.duration_seconds == 300  # Capped
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_inject_chaos(self, mock_apply):
        """Test inject_chaos applies config."""
        from selfhealing.services.chaos.experiment_impl import (
            NetworkBlackholeExperiment,
            ExperimentConfig,
        )
        from datetime import datetime, timezone
        
        experiment = NetworkBlackholeExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"affected_endpoints": ["https://api.test.com"]},
            )
        )
        experiment._effective_ttl = 60
        experiment._expires_at = datetime.now(timezone.utc)
        
        result = experiment.inject_chaos()
        
        assert result is True
        mock_apply.assert_called_once()
        call_args = mock_apply.call_args[0][0]
        assert "network_blackhole" in call_args
        assert call_args["network_blackhole"]["enabled"] is True
    
    def test_create_experiment_factory(self):
        """Test create_experiment factory for network_blackhole."""
        from selfhealing.services.chaos.experiment_impl import (
            create_experiment,
            NetworkBlackholeExperiment,
            ExperimentConfig,
        )
        
        experiment = create_experiment(
            experiment_type="network_blackhole",
            config=ExperimentConfig(target_service="test"),
        )
        
        assert isinstance(experiment, NetworkBlackholeExperiment)


class TestSimulatedDiskIOExperiment:
    """Test SimulatedDiskIOExperiment class."""
    
    def test_class_exists(self):
        """Test SimulatedDiskIOExperiment class exists."""
        from selfhealing.services.chaos.experiment_impl import SimulatedDiskIOExperiment
        
        assert SimulatedDiskIOExperiment is not None
    
    def test_experiment_type(self):
        """Test experiment type is simulated_disk_io."""
        from selfhealing.services.chaos.experiment_impl import SimulatedDiskIOExperiment
        
        assert SimulatedDiskIOExperiment.experiment_type == "simulated_disk_io"
    
    def test_hard_cap_io_latency(self):
        """Test MAX_IO_LATENCY_MS hard cap is 2000."""
        from selfhealing.services.chaos.experiment_impl import SimulatedDiskIOExperiment
        
        assert SimulatedDiskIOExperiment.MAX_IO_LATENCY_MS == 2000
    
    def test_hard_cap_failure_rate(self):
        """Test MAX_FAILURE_RATE hard cap is 0.30."""
        from selfhealing.services.chaos.experiment_impl import SimulatedDiskIOExperiment
        
        assert SimulatedDiskIOExperiment.MAX_FAILURE_RATE == 0.30
    
    def test_io_latency_capped(self):
        """Test io_latency_ms is capped at MAX_IO_LATENCY_MS."""
        from selfhealing.services.chaos.experiment_impl import (
            SimulatedDiskIOExperiment,
            ExperimentConfig,
        )
        
        experiment = SimulatedDiskIOExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"io_latency_ms": 5000},  # Over cap
            )
        )
        
        assert experiment.io_latency_ms == 2000  # Capped
    
    def test_failure_rate_capped(self):
        """Test failure_rate is capped at MAX_FAILURE_RATE."""
        from selfhealing.services.chaos.experiment_impl import (
            SimulatedDiskIOExperiment,
            ExperimentConfig,
        )
        
        experiment = SimulatedDiskIOExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"failure_rate": 0.80},  # Over cap
            )
        )
        
        assert experiment.failure_rate == 0.30  # Capped
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_inject_chaos(self, mock_apply):
        """Test inject_chaos applies config."""
        from selfhealing.services.chaos.experiment_impl import (
            SimulatedDiskIOExperiment,
            ExperimentConfig,
        )
        from datetime import datetime, timezone
        
        experiment = SimulatedDiskIOExperiment(
            config=ExperimentConfig(target_service="test")
        )
        experiment._effective_ttl = 60
        experiment._expires_at = datetime.now(timezone.utc)
        
        result = experiment.inject_chaos()
        
        assert result is True
        mock_apply.assert_called_once()
        call_args = mock_apply.call_args[0][0]
        assert "disk_io" in call_args
        assert call_args["disk_io"]["enabled"] is True
    
    def test_create_experiment_factory(self):
        """Test create_experiment factory for simulated_disk_io."""
        from selfhealing.services.chaos.experiment_impl import (
            create_experiment,
            SimulatedDiskIOExperiment,
            ExperimentConfig,
        )
        
        experiment = create_experiment(
            experiment_type="simulated_disk_io",
            config=ExperimentConfig(target_service="test"),
        )
        
        assert isinstance(experiment, SimulatedDiskIOExperiment)


class TestSimulatedTLSFailureExperiment:
    """Test SimulatedTLSFailureExperiment class."""
    
    def test_class_exists(self):
        """Test SimulatedTLSFailureExperiment class exists."""
        from selfhealing.services.chaos.experiment_impl import SimulatedTLSFailureExperiment
        
        assert SimulatedTLSFailureExperiment is not None
    
    def test_experiment_type(self):
        """Test experiment type is simulated_tls_failure."""
        from selfhealing.services.chaos.experiment_impl import SimulatedTLSFailureExperiment
        
        assert SimulatedTLSFailureExperiment.experiment_type == "simulated_tls_failure"
    
    def test_requires_approval(self):
        """Test requires_approval is True (security sensitive)."""
        from selfhealing.services.chaos.experiment_impl import SimulatedTLSFailureExperiment
        
        assert SimulatedTLSFailureExperiment.requires_approval is True
    
    def test_hard_cap_failure_rate(self):
        """Test MAX_FAILURE_RATE hard cap is 0.25."""
        from selfhealing.services.chaos.experiment_impl import SimulatedTLSFailureExperiment
        
        assert SimulatedTLSFailureExperiment.MAX_FAILURE_RATE == 0.25
    
    def test_default_failure_type(self):
        """Test default failure_type is handshake_timeout."""
        from selfhealing.services.chaos.experiment_impl import (
            SimulatedTLSFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = SimulatedTLSFailureExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        assert experiment.failure_type == "handshake_timeout"
    
    def test_failure_rate_capped(self):
        """Test failure_rate is capped at MAX_FAILURE_RATE."""
        from selfhealing.services.chaos.experiment_impl import (
            SimulatedTLSFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = SimulatedTLSFailureExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"failure_rate": 0.50},  # Over cap
            )
        )
        
        assert experiment.failure_rate == 0.25  # Capped
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_inject_chaos(self, mock_apply):
        """Test inject_chaos applies config."""
        from selfhealing.services.chaos.experiment_impl import (
            SimulatedTLSFailureExperiment,
            ExperimentConfig,
        )
        from datetime import datetime, timezone
        
        experiment = SimulatedTLSFailureExperiment(
            config=ExperimentConfig(target_service="test")
        )
        experiment._effective_ttl = 60
        experiment._expires_at = datetime.now(timezone.utc)
        
        result = experiment.inject_chaos()
        
        assert result is True
        mock_apply.assert_called_once()
        call_args = mock_apply.call_args[0][0]
        assert "tls_failure" in call_args
        assert call_args["tls_failure"]["enabled"] is True
    
    def test_create_experiment_factory(self):
        """Test create_experiment factory for simulated_tls_failure."""
        from selfhealing.services.chaos.experiment_impl import (
            create_experiment,
            SimulatedTLSFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = create_experiment(
            experiment_type="simulated_tls_failure",
            config=ExperimentConfig(target_service="test"),
        )
        
        assert isinstance(experiment, SimulatedTLSFailureExperiment)


class TestAuditStorageFailureExperiment:
    """Test AuditStorageFailureExperiment class."""
    
    def test_class_exists(self):
        """Test AuditStorageFailureExperiment class exists."""
        from selfhealing.services.chaos.experiment_impl import AuditStorageFailureExperiment
        
        assert AuditStorageFailureExperiment is not None
    
    def test_experiment_type(self):
        """Test experiment type is audit_storage_failure."""
        from selfhealing.services.chaos.experiment_impl import AuditStorageFailureExperiment
        
        assert AuditStorageFailureExperiment.experiment_type == "audit_storage_failure"
    
    def test_requires_approval(self):
        """Test requires_approval is True (audit data risk)."""
        from selfhealing.services.chaos.experiment_impl import AuditStorageFailureExperiment
        
        assert AuditStorageFailureExperiment.requires_approval is True
    
    def test_failure_hypothesis_exists(self):
        """Test failure_hypothesis is defined."""
        from selfhealing.services.chaos.experiment_impl import AuditStorageFailureExperiment
        
        assert hasattr(AuditStorageFailureExperiment, "failure_hypothesis")
        assert AuditStorageFailureExperiment.failure_hypothesis is not None
    
    def test_default_failed_layer(self):
        """Test default failed_layer is l2."""
        from selfhealing.services.chaos.experiment_impl import (
            AuditStorageFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = AuditStorageFailureExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        assert experiment.failed_layer == "l2"
    
    def test_default_recovery_mode(self):
        """Test default recovery_mode is degraded."""
        from selfhealing.services.chaos.experiment_impl import (
            AuditStorageFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = AuditStorageFailureExperiment(
            config=ExperimentConfig(target_service="test")
        )
        
        assert experiment.recovery_mode == "degraded"
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_inject_chaos(self, mock_apply):
        """Test inject_chaos applies config."""
        from selfhealing.services.chaos.experiment_impl import (
            AuditStorageFailureExperiment,
            ExperimentConfig,
        )
        from datetime import datetime, timezone
        
        experiment = AuditStorageFailureExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"failed_layer": "l3"},
            )
        )
        experiment._effective_ttl = 60
        experiment._expires_at = datetime.now(timezone.utc)
        
        result = experiment.inject_chaos()
        
        assert result is True
        mock_apply.assert_called_once()
        call_args = mock_apply.call_args[0][0]
        assert "audit_storage_failure" in call_args
        assert call_args["audit_storage_failure"]["enabled"] is True
    
    def test_create_experiment_factory(self):
        """Test create_experiment factory for audit_storage_failure."""
        from selfhealing.services.chaos.experiment_impl import (
            create_experiment,
            AuditStorageFailureExperiment,
            ExperimentConfig,
        )
        
        experiment = create_experiment(
            experiment_type="audit_storage_failure",
            config=ExperimentConfig(target_service="test"),
        )
        
        assert isinstance(experiment, AuditStorageFailureExperiment)


class TestReplayFloodExperiment:
    """Test ReplayFloodExperiment class."""
    
    def test_class_exists(self):
        """Test ReplayFloodExperiment class exists."""
        from selfhealing.services.chaos.experiment_impl import ReplayFloodExperiment
        
        assert ReplayFloodExperiment is not None
    
    def test_experiment_type(self):
        """Test experiment type is replay_flood."""
        from selfhealing.services.chaos.experiment_impl import ReplayFloodExperiment
        
        assert ReplayFloodExperiment.experiment_type == "replay_flood"
    
    def test_requires_approval(self):
        """Test requires_approval is True (resource intensive)."""
        from selfhealing.services.chaos.experiment_impl import ReplayFloodExperiment
        
        assert ReplayFloodExperiment.requires_approval is True
    
    def test_hard_cap_max_entries(self):
        """Test MAX_ENTRIES_TO_CREATE hard cap is 5000."""
        from selfhealing.services.chaos.experiment_impl import ReplayFloodExperiment
        
        assert ReplayFloodExperiment.MAX_ENTRIES_TO_CREATE == 5000
    
    def test_hard_cap_max_rate(self):
        """Test MAX_REPLAY_RATE hard cap is 500."""
        from selfhealing.services.chaos.experiment_impl import ReplayFloodExperiment
        
        assert ReplayFloodExperiment.MAX_REPLAY_RATE == 500
    
    def test_entries_to_create_capped(self):
        """Test entries_to_create is capped at MAX_ENTRIES_TO_CREATE."""
        from selfhealing.services.chaos.experiment_impl import (
            ReplayFloodExperiment,
            ExperimentConfig,
        )
        
        experiment = ReplayFloodExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"entries_to_create": 10000},  # Over cap
            )
        )
        
        assert experiment.entries_to_create == 5000  # Capped
    
    def test_replay_rate_capped(self):
        """Test replay_rate is capped at MAX_REPLAY_RATE."""
        from selfhealing.services.chaos.experiment_impl import (
            ReplayFloodExperiment,
            ExperimentConfig,
        )
        
        experiment = ReplayFloodExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"replay_rate": 1000},  # Over cap
            )
        )
        
        assert experiment.replay_rate == 500  # Capped
    
    def test_isolated_domain_prefix(self):
        """Test isolated_domain has chaos prefix."""
        from selfhealing.services.chaos.experiment_impl import (
            ReplayFloodExperiment,
            ExperimentConfig,
        )
        
        experiment = ReplayFloodExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"domain": "payment"},
            )
        )
        
        assert experiment.isolated_domain == "chaos_test:payment"
    
    def test_isolated_domain_no_double_prefix(self):
        """Test isolated_domain doesn't double-prefix."""
        from selfhealing.services.chaos.experiment_impl import (
            ReplayFloodExperiment,
            ExperimentConfig,
        )
        
        experiment = ReplayFloodExperiment(
            config=ExperimentConfig(
                target_service="test",
                parameters={"domain": "chaos_test:payment"},
            )
        )
        
        assert experiment.isolated_domain == "chaos_test:payment"
    
    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_inject_chaos(self, mock_apply):
        """Test inject_chaos applies config with isolation metadata."""
        from selfhealing.services.chaos.experiment_impl import (
            ReplayFloodExperiment,
            ExperimentConfig,
        )
        from datetime import datetime, timezone
        
        experiment = ReplayFloodExperiment(
            config=ExperimentConfig(target_service="test")
        )
        experiment._effective_ttl = 60
        experiment._expires_at = datetime.now(timezone.utc)
        
        result = experiment.inject_chaos()
        
        assert result is True
        mock_apply.assert_called_once()
        call_args = mock_apply.call_args[0][0]
        assert "replay_flood" in call_args
        assert call_args["replay_flood"]["enabled"] is True
        # Check isolation metadata
        metadata = call_args["replay_flood"]["isolation_metadata"]
        assert metadata["is_synthetic"] is True
        assert metadata["is_chaos_experiment"] is True
    
    def test_create_experiment_factory(self):
        """Test create_experiment factory for replay_flood."""
        from selfhealing.services.chaos.experiment_impl import (
            create_experiment,
            ReplayFloodExperiment,
            ExperimentConfig,
        )
        
        experiment = create_experiment(
            experiment_type="replay_flood",
            config=ExperimentConfig(target_service="test"),
        )
        
        assert isinstance(experiment, ReplayFloodExperiment)


# =============================================================================
# ExperimentType Enum Tests (Extended)
# =============================================================================


class TestExperimentTypeEnumExtended:
    """Test ExperimentType enum for Phase 3-4 additions."""
    
    def test_network_blackhole_exists(self):
        """Test NETWORK_BLACKHOLE exists in enum."""
        from selfhealing.services.chaos.base import ExperimentType
        
        assert hasattr(ExperimentType, "NETWORK_BLACKHOLE")
        assert ExperimentType.NETWORK_BLACKHOLE.value == "network_blackhole"
    
    def test_simulated_disk_io_exists(self):
        """Test SIMULATED_DISK_IO exists in enum."""
        from selfhealing.services.chaos.base import ExperimentType
        
        assert hasattr(ExperimentType, "SIMULATED_DISK_IO")
        assert ExperimentType.SIMULATED_DISK_IO.value == "simulated_disk_io"
    
    def test_simulated_tls_failure_exists(self):
        """Test SIMULATED_TLS_FAILURE exists in enum."""
        from selfhealing.services.chaos.base import ExperimentType
        
        assert hasattr(ExperimentType, "SIMULATED_TLS_FAILURE")
        assert ExperimentType.SIMULATED_TLS_FAILURE.value == "simulated_tls_failure"
    
    def test_audit_storage_failure_exists(self):
        """Test AUDIT_STORAGE_FAILURE exists in enum."""
        from selfhealing.services.chaos.base import ExperimentType
        
        assert hasattr(ExperimentType, "AUDIT_STORAGE_FAILURE")
        assert ExperimentType.AUDIT_STORAGE_FAILURE.value == "audit_storage_failure"
    
    def test_replay_flood_exists(self):
        """Test REPLAY_FLOOD exists in enum."""
        from selfhealing.services.chaos.base import ExperimentType
        
        assert hasattr(ExperimentType, "REPLAY_FLOOD")
        assert ExperimentType.REPLAY_FLOOD.value == "replay_flood"
    
    def test_total_experiment_types_count(self):
        """Test total number of experiment types is 21."""
        from selfhealing.services.chaos.base import ExperimentType
        
        # Count all enum members
        count = len(list(ExperimentType))
        
        # Should be 21 as per 33_CHAOS_INDUSTRY_EXPERIMENTS.md §8
        assert count >= 21, f"Expected at least 21 experiment types, got {count}"