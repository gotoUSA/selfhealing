"""
Phase 6/7 Unit Tests - Resilience 검증 시스템 및 통합 테스트

Tests for:
- ResilienceExpectation data class
- ResilienceAssertion data class  
- ExpectationType enum
- ResilienceValidationResult
- ResilienceValidator
- Integration scenarios (Rate Limit Storm, Emergency Escalation, Panic Recovery)

Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §Phase 6-7
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, List


# =============================================================================
# Phase 6: ResilienceExpectation Tests
# =============================================================================

class TestExpectationType:
    """Test ExpectationType enum."""

    def test_all_expectation_types_exist(self):
        """Test all expected types are defined."""
        from selfhealing.services.chaos.resilience_expectation import ExpectationType
        
        expected_types = [
            "CIRCUIT_BREAKER_OPEN",
            "CIRCUIT_BREAKER_HALF_OPEN",
            "FALLBACK_ACTIVATED",
            "RETRY_TRIGGERED",
            "RATE_LIMIT_ACTIVATED",
            "LOAD_SHEDDING_TRIGGERED",
            "AUTO_SCALE_TRIGGERED",
            "ALERT_FIRED",
            "EMERGENCY_MODE_ACTIVATED",
            "GRACEFUL_DEGRADATION",
            "CUSTOM",
        ]
        
        for type_name in expected_types:
            assert hasattr(ExpectationType, type_name), f"{type_name} not found"

    def test_expectation_type_values(self):
        """Test enum values are snake_case strings."""
        from selfhealing.services.chaos.resilience_expectation import ExpectationType
        
        assert ExpectationType.CIRCUIT_BREAKER_OPEN.value == "circuit_breaker_open"
        assert ExpectationType.FALLBACK_ACTIVATED.value == "fallback_activated"


class TestResilienceAssertion:
    """Test ResilienceAssertion data class."""

    def test_assertion_creation(self):
        """Test basic assertion creation."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceAssertion,
            ExpectationType,
        )
        
        assertion = ResilienceAssertion(
            expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
            target_service="payment",
            expected_within_seconds=10.0,
        )
        
        assert assertion.expectation_type == ExpectationType.CIRCUIT_BREAKER_OPEN
        assert assertion.target_service == "payment"
        assert assertion.expected_within_seconds == 10.0

    def test_assertion_auto_description(self):
        """Test automatic description generation."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceAssertion,
            ExpectationType,
        )
        
        assertion = ResilienceAssertion(
            expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
            target_service="payment",
            expected_within_seconds=10.0,
        )
        
        assert "CB" in assertion.description or "circuit" in assertion.description.lower()
        assert "payment" in assertion.description
        assert "10" in assertion.description

    def test_assertion_custom_description(self):
        """Test custom description overrides auto-generation."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceAssertion,
            ExpectationType,
        )
        
        assertion = ResilienceAssertion(
            expectation_type=ExpectationType.CUSTOM,
            description="My custom assertion",
        )
        
        assert assertion.description == "My custom assertion"

    def test_assertion_to_dict(self):
        """Test to_dict serialization."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceAssertion,
            ExpectationType,
        )
        
        assertion = ResilienceAssertion(
            expectation_type=ExpectationType.FALLBACK_ACTIVATED,
            target_service="order",
            expected_within_seconds=15.0,
        )
        
        result = assertion.to_dict()
        
        assert result["expectation_type"] == "fallback_activated"
        assert result["target_service"] == "order"
        assert result["expected_within_seconds"] == 15.0


class TestResilienceExpectation:
    """Test ResilienceExpectation data class."""

    def test_expectation_creation(self):
        """Test basic expectation creation."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ResilienceAssertion,
            ExpectationType,
        )
        
        expectation = ResilienceExpectation(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                    target_service="payment",
                ),
            ],
            require_all=True,
        )
        
        assert len(expectation.assertions) == 1
        assert expectation.require_all is True

    def test_expect_cb_open_factory(self):
        """Test expect_cb_open factory method."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ExpectationType,
        )
        
        expectation = ResilienceExpectation.expect_cb_open(
            target_service="payment",
            within_seconds=10.0,
        )
        
        assert len(expectation.assertions) == 1
        assert expectation.assertions[0].expectation_type == ExpectationType.CIRCUIT_BREAKER_OPEN
        assert expectation.assertions[0].target_service == "payment"
        assert expectation.assertions[0].expected_within_seconds == 10.0

    def test_expect_fallback_factory(self):
        """Test expect_fallback factory method."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ExpectationType,
        )
        
        expectation = ResilienceExpectation.expect_fallback(
            target_service="order",
            within_seconds=15.0,
        )
        
        assert len(expectation.assertions) == 1
        assert expectation.assertions[0].expectation_type == ExpectationType.FALLBACK_ACTIVATED

    def test_expect_graceful_degradation_factory(self):
        """Test expect_graceful_degradation factory method."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ExpectationType,
        )
        
        expectation = ResilienceExpectation.expect_graceful_degradation(
            target_service="payment",
            within_seconds=30.0,
        )
        
        assert len(expectation.assertions) == 2
        assert expectation.require_all is True
        
        types = [a.expectation_type for a in expectation.assertions]
        assert ExpectationType.CIRCUIT_BREAKER_OPEN in types
        assert ExpectationType.FALLBACK_ACTIVATED in types

    def test_expect_retry_then_cb_open_factory(self):
        """Test expect_retry_then_cb_open factory method."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ExpectationType,
        )
        
        expectation = ResilienceExpectation.expect_retry_then_cb_open(
            target_service="payment",
            retry_count=3,
            cb_open_within_seconds=10.0,
        )
        
        assert len(expectation.assertions) == 2
        
        retry_assertion = next(
            a for a in expectation.assertions
            if a.expectation_type == ExpectationType.RETRY_TRIGGERED
        )
        assert retry_assertion.expected_count == 3

    def test_expect_emergency_mode_factory(self):
        """Test expect_emergency_mode factory method."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ExpectationType,
        )
        
        expectation = ResilienceExpectation.expect_emergency_mode(
            level=2,
            within_seconds=60.0,
        )
        
        assert len(expectation.assertions) == 1
        assert expectation.assertions[0].expectation_type == ExpectationType.EMERGENCY_MODE_ACTIVATED
        assert expectation.assertions[0].expected_state == "level_2"

    def test_add_assertion_builder(self):
        """Test add_assertion builder pattern."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ResilienceAssertion,
            ExpectationType,
        )
        
        expectation = ResilienceExpectation()
        expectation.add_assertion(
            ResilienceAssertion(
                expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                target_service="payment",
            )
        ).add_assertion(
            ResilienceAssertion(
                expectation_type=ExpectationType.ALERT_FIRED,
            )
        )
        
        assert len(expectation.assertions) == 2

    def test_expectation_to_dict(self):
        """Test to_dict serialization."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        
        expectation = ResilienceExpectation.expect_cb_open("payment", 10.0)
        result = expectation.to_dict()
        
        assert "assertions" in result
        assert "require_all" in result
        assert "description" in result


class TestResilienceValidationResult:
    """Test ResilienceValidationResult data class."""

    def test_result_creation(self):
        """Test basic result creation."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceValidationResult,
        )
        
        result = ResilienceValidationResult(
            passed=True,
            total_assertions=3,
            passed_assertions=2,
            failed_assertions=1,
        )
        
        assert result.passed is True
        assert result.resilience_score == pytest.approx(2/3)

    def test_result_skip_factory(self):
        """Test skip factory method."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceValidationResult,
        )
        
        result = ResilienceValidationResult.skip("No expectation defined")
        
        assert result.passed is True
        assert "Skipped" in result.summary

    def test_result_to_dict(self):
        """Test to_dict serialization."""
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceValidationResult,
        )
        
        result = ResilienceValidationResult(
            passed=True,
            total_assertions=2,
            passed_assertions=2,
            failed_assertions=0,
        )
        
        data = result.to_dict()
        
        assert data["passed"] is True
        assert data["resilience_score"] == 1.0


# =============================================================================
# Phase 6: ResilienceValidator Tests
# =============================================================================

class TestResilienceValidator:
    """Test ResilienceValidator."""

    def test_validator_creation(self):
        """Test validator can be created."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        
        validator = ResilienceValidator()
        assert validator is not None

    def test_validate_none_expectation_returns_skip(self):
        """Test validating None expectation returns skip result."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        
        validator = ResilienceValidator()
        result = validator.validate(expectation=None)
        
        assert result.passed is True
        assert "Skipped" in result.summary

    def test_validate_empty_assertions_returns_skip(self):
        """Test validating empty assertions returns skip result."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        
        validator = ResilienceValidator()
        result = validator.validate(
            expectation=ResilienceExpectation(assertions=[]),
        )
        
        assert result.passed is True

    def test_validate_cb_open_with_mocked_provider(self):
        """Test CB OPEN validation with mocked provider."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        
        # Mock CB provider that returns OPEN
        mock_cb_provider = Mock()
        mock_cb_provider.get_status.return_value = {"state": "open"}
        
        validator = ResilienceValidator(cb_provider=mock_cb_provider)
        
        result = validator.validate(
            expectation=ResilienceExpectation.expect_cb_open("payment", 10.0),
        )
        
        assert result.passed is True
        assert result.resilience_score == 1.0
        mock_cb_provider.get_status.assert_called_with("payment")

    def test_validate_cb_open_failure(self):
        """Test CB OPEN validation failure when CB is closed."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        
        # Mock CB provider that returns CLOSED
        mock_cb_provider = Mock()
        mock_cb_provider.get_status.return_value = {"state": "closed"}
        
        validator = ResilienceValidator(cb_provider=mock_cb_provider)
        
        result = validator.validate(
            expectation=ResilienceExpectation.expect_cb_open("payment", 10.0),
        )
        
        assert result.passed is False
        assert result.resilience_score == 0.0

    def test_validate_fallback_with_events(self):
        """Test fallback validation with collected events."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
            DefaultEventCollector,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        from selfhealing.core.timezone import now
        
        # Create event collector with fallback event
        event_collector = DefaultEventCollector()
        event_collector.record_event(
            event_type="fallback",
            service_name="payment",
            data={"reason": "CB open"},
        )
        
        validator = ResilienceValidator(event_collector=event_collector)
        
        result = validator.validate(
            expectation=ResilienceExpectation.expect_fallback("payment", 15.0),
            experiment_start_time=now() - timedelta(seconds=5),
        )
        
        assert result.passed is True

    def test_validate_graceful_degradation_partial_pass(self):
        """Test graceful degradation with partial pass (CB open but no fallback)."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
            DefaultEventCollector,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        
        # Mock CB provider that returns OPEN
        mock_cb_provider = Mock()
        mock_cb_provider.get_status.return_value = {"state": "open"}
        
        # Empty event collector (no fallback events)
        event_collector = DefaultEventCollector()
        
        validator = ResilienceValidator(
            cb_provider=mock_cb_provider,
            event_collector=event_collector,
        )
        
        result = validator.validate(
            expectation=ResilienceExpectation.expect_graceful_degradation("payment"),
        )
        
        # require_all=True, so should fail (CB passed, fallback failed)
        assert result.passed is False
        assert result.passed_assertions == 1
        assert result.failed_assertions == 1
        assert result.resilience_score == 0.5

    def test_validate_require_any_mode(self):
        """Test require_all=False mode (any assertion pass = success)."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ResilienceAssertion,
            ExpectationType,
        )
        
        # Mock CB provider that returns OPEN
        mock_cb_provider = Mock()
        mock_cb_provider.get_status.return_value = {"state": "open"}
        
        validator = ResilienceValidator(cb_provider=mock_cb_provider)
        
        expectation = ResilienceExpectation(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                    target_service="payment",
                ),
                ResilienceAssertion(
                    expectation_type=ExpectationType.FALLBACK_ACTIVATED,
                    target_service="payment",
                ),
            ],
            require_all=False,  # Any pass is success
        )
        
        result = validator.validate(expectation=expectation)
        
        # CB passed, fallback failed, but require_all=False so overall pass
        assert result.passed is True

    def test_validate_emergency_mode(self):
        """Test emergency mode validation."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        
        # Mock emergency provider at level 2
        mock_emergency_provider = Mock()
        mock_emergency_provider.get_current_level.return_value = 2
        mock_emergency_provider.is_active.return_value = True
        
        validator = ResilienceValidator(
            emergency_provider=mock_emergency_provider,
        )
        
        result = validator.validate(
            expectation=ResilienceExpectation.expect_emergency_mode(level=2),
        )
        
        assert result.passed is True

    def test_validate_custom_assertion(self):
        """Test custom assertion with custom validator function."""
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ResilienceAssertion,
            ExpectationType,
        )
        
        # Custom validator that always returns True
        def my_validator(context: Dict[str, Any]) -> bool:
            return context.get("target_service") == "payment"
        
        validator = ResilienceValidator()
        
        expectation = ResilienceExpectation(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.CUSTOM,
                    target_service="payment",
                    custom_validator=my_validator,
                ),
            ],
        )
        
        result = validator.validate(expectation=expectation)
        
        assert result.passed is True

    def test_get_resilience_validator_factory(self):
        """Test get_resilience_validator factory function."""
        from selfhealing.services.chaos.resilience_validator import (
            get_resilience_validator,
            ResilienceValidator,
        )
        
        validator = get_resilience_validator(use_real_providers=False)
        assert isinstance(validator, ResilienceValidator)


# =============================================================================
# Phase 6: ExperimentConfig/Result Integration Tests
# =============================================================================

class TestExperimentConfigResilienceIntegration:
    """Test resilience_expectation integration in ExperimentConfig."""

    def test_experiment_config_has_resilience_expectation_field(self):
        """Test ExperimentConfig has resilience_expectation field."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(
            target_service="payment",
        )
        
        assert hasattr(config, "resilience_expectation")
        assert config.resilience_expectation is None

    def test_experiment_config_with_resilience_expectation(self):
        """Test ExperimentConfig with resilience_expectation set."""
        from selfhealing.services.chaos.base import ExperimentConfig
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        
        expectation = ResilienceExpectation.expect_cb_open("payment", 10.0)
        
        config = ExperimentConfig(
            target_service="payment",
            resilience_expectation=expectation,
        )
        
        assert config.resilience_expectation is expectation


class TestExperimentResultResilienceIntegration:
    """Test resilience fields in ExperimentResult."""

    def test_experiment_result_has_resilience_fields(self):
        """Test ExperimentResult has resilience fields."""
        from selfhealing.services.chaos.base import ExperimentResult
        
        result = ExperimentResult(
            experiment_id="test-123",
            experiment_type="latency_injection",
            status="completed",
        )
        
        assert hasattr(result, "resilience_validation")
        assert hasattr(result, "resilience_passed")
        assert result.resilience_passed is True  # Default

    def test_experiment_result_to_dict_includes_resilience(self):
        """Test to_dict includes resilience fields."""
        from selfhealing.services.chaos.base import ExperimentResult
        
        result = ExperimentResult(
            experiment_id="test-123",
            experiment_type="latency_injection",
            status="completed",
            resilience_validation={"passed": True, "score": 1.0},
            resilience_passed=True,
        )
        
        data = result.to_dict()
        
        assert "resilience_validation" in data
        assert "resilience_passed" in data


# =============================================================================
# Phase 7: Integration Test Scenarios
# =============================================================================

class TestIntegrationScenarioRateLimitStorm:
    """Integration test: Rate Limit Storm scenario."""

    def test_rate_limit_storm_triggers_cb_open(self):
        """
        Scenario: Rate Limit Storm
        
        Flow: RateLimitExperiment → CB OPEN → Fast Fail 503
        Expected: CB should OPEN within 10 seconds
        """
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        
        # Mock CB provider simulating OPEN state after rate limit
        mock_cb_provider = Mock()
        mock_cb_provider.get_status.return_value = {"state": "open"}
        
        validator = ResilienceValidator(cb_provider=mock_cb_provider)
        
        # Define expectation: CB should OPEN
        expectation = ResilienceExpectation.expect_cb_open(
            target_service="payment-api",
            within_seconds=10.0,
        )
        
        result = validator.validate(expectation=expectation)
        
        assert result.passed is True
        assert result.resilience_score == 1.0


class TestIntegrationScenarioEmergencyEscalation:
    """Integration test: Emergency Escalation scenario."""

    def test_partial_failure_triggers_emergency_level_2(self):
        """
        Scenario: Emergency Escalation
        
        Flow: PartialFailureExperiment → Emergency Level 2
        Expected: Emergency Mode Level 2 should activate
        """
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        
        # Mock emergency provider at level 2
        mock_emergency_provider = Mock()
        mock_emergency_provider.get_current_level.return_value = 2
        mock_emergency_provider.is_active.return_value = True
        
        validator = ResilienceValidator(
            emergency_provider=mock_emergency_provider,
        )
        
        # Define expectation: Emergency Level 2
        expectation = ResilienceExpectation.expect_emergency_mode(
            level=2,
            within_seconds=60.0,
        )
        
        result = validator.validate(expectation=expectation)
        
        assert result.passed is True


class TestIntegrationScenarioPanicRecovery:
    """Integration test: Panic Recovery scenario."""

    def test_cascading_failure_triggers_emergency_level_3(self):
        """
        Scenario: Panic Recovery
        
        Flow: CascadingFailureExperiment → Panic → Emergency Level 3 → Manual Recovery
        Expected: Emergency Mode Level 3 should activate
        """
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
            ResilienceAssertion,
            ExpectationType,
        )
        
        # Mock emergency provider at level 3 (Panic)
        mock_emergency_provider = Mock()
        mock_emergency_provider.get_current_level.return_value = 3
        mock_emergency_provider.is_active.return_value = True
        
        validator = ResilienceValidator(
            emergency_provider=mock_emergency_provider,
        )
        
        # Define complex expectation: Emergency Level 3
        expectation = ResilienceExpectation(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.EMERGENCY_MODE_ACTIVATED,
                    expected_within_seconds=120.0,
                    expected_state="level_3",
                    description="Panic Threshold should trigger Emergency Level 3",
                ),
            ],
            description="Full panic recovery scenario",
        )
        
        result = validator.validate(expectation=expectation)
        
        assert result.passed is True
        assert result.resilience_score == 1.0


class TestIntegrationScenarioGracefulDegradation:
    """Integration test: Graceful Degradation scenario."""

    def test_latency_injection_triggers_graceful_degradation(self):
        """
        Scenario: Graceful Degradation
        
        Flow: LatencyInjection → CB OPEN → Fallback Activated
        Expected: Both CB and Fallback should activate
        """
        from selfhealing.services.chaos.resilience_validator import (
            ResilienceValidator,
            DefaultEventCollector,
        )
        from selfhealing.services.chaos.resilience_expectation import (
            ResilienceExpectation,
        )
        from selfhealing.core.timezone import now
        from datetime import timedelta
        
        # Mock CB provider that returns OPEN
        mock_cb_provider = Mock()
        mock_cb_provider.get_status.return_value = {"state": "open"}
        
        # Event collector with fallback event
        event_collector = DefaultEventCollector()
        event_collector.record_event(
            event_type="fallback",
            service_name="payment",
            data={"reason": "CB open, using cached response"},
        )
        
        validator = ResilienceValidator(
            cb_provider=mock_cb_provider,
            event_collector=event_collector,
        )
        
        # Define expectation: Graceful Degradation
        expectation = ResilienceExpectation.expect_graceful_degradation(
            target_service="payment",
            within_seconds=30.0,
        )
        
        result = validator.validate(
            expectation=expectation,
            experiment_start_time=now() - timedelta(seconds=5),
        )
        
        assert result.passed is True
        assert result.passed_assertions == 2
        assert result.resilience_score == 1.0


# =============================================================================
# Module Import Tests
# =============================================================================

class TestModuleImports:
    """Test module imports from chaos package."""

    def test_import_from_chaos_package(self):
        """Test all Phase 6 exports are importable from chaos package."""
        from selfhealing.services.chaos import (
            ExpectationType,
            ResilienceAssertion,
            ResilienceExpectation,
            ResilienceValidationResult,
            ResilienceValidator,
            get_resilience_validator,
        )
        
        assert ExpectationType is not None
        assert ResilienceAssertion is not None
        assert ResilienceExpectation is not None
        assert ResilienceValidationResult is not None
        assert ResilienceValidator is not None
        assert get_resilience_validator is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
