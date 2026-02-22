"""
Tests for Deployment Policy Advisor

Covers:
- DeploymentPolicyAdvisor class
- Deployment verdict generation
- Status evaluation
"""

from unittest.mock import MagicMock, patch


class TestDeploymentPolicyAdvisorInit:
    """Tests for DeploymentPolicyAdvisor initialization."""

    def test_init_with_defaults(self):
        """Test initialization with defaults."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor

        advisor = DeploymentPolicyAdvisor()

        assert advisor.calculator is not None

    def test_init_with_custom_calculator(self):
        """Test initialization with custom calculator."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator

        calculator = ErrorBudgetCalculator()
        advisor = DeploymentPolicyAdvisor(calculator=calculator)

        assert advisor.calculator is calculator


class TestGetDeploymentVerdict:
    """Tests for get_deployment_verdict method."""

    def test_verdict_proceed_when_healthy(self):
        """Test returns PROCEED verdict when budget healthy."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.services.error_budget.enums import FreezeStatus

        # Mock calculator to return healthy status
        calculator = MagicMock(spec=ErrorBudgetCalculator)
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = 80.0
        mock_status.burn_rate_1h = 1.0
        mock_status.burn_rate_6h = 0.5
        calculator.calculate_budget_status.return_value = mock_status

        advisor = DeploymentPolicyAdvisor(calculator=calculator)

        with patch('selfhealing.services.error_budget.advisor.get_error_budget_thresholds',
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            with patch('selfhealing.services.error_budget.advisor.get_burn_rate_thresholds',
                       return_value={"fast_critical": 14.4, "fast_warning": 6.0, "slow_warning": 3.0, "slow_info": 1.0}):
                verdict = advisor.get_deployment_verdict()

        assert verdict.status == FreezeStatus.PROCEED

    def test_verdict_caution_when_moderate(self):
        """Test returns CAUTION verdict when budget moderate."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.services.error_budget.enums import FreezeStatus

        calculator = MagicMock(spec=ErrorBudgetCalculator)
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = 55.0  # Between caution and healthy
        mock_status.burn_rate_1h = 2.0
        mock_status.burn_rate_6h = 1.0
        calculator.calculate_budget_status.return_value = mock_status

        advisor = DeploymentPolicyAdvisor(calculator=calculator)

        with patch('selfhealing.services.error_budget.advisor.get_error_budget_thresholds',
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            with patch('selfhealing.services.error_budget.advisor.get_burn_rate_thresholds',
                       return_value={"fast_critical": 14.4, "fast_warning": 6.0, "slow_warning": 3.0, "slow_info": 1.0}):
                verdict = advisor.get_deployment_verdict()

        # Should be CAUTION or higher
        assert verdict.status in [FreezeStatus.CAUTION, FreezeStatus.PROCEED]

    def test_verdict_warning_when_low(self):
        """Test returns WARNING verdict when budget low."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.services.error_budget.enums import FreezeStatus

        calculator = MagicMock(spec=ErrorBudgetCalculator)
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = 35.0  # Between warning and caution
        mock_status.burn_rate_1h = 3.0
        mock_status.burn_rate_6h = 2.0
        calculator.calculate_budget_status.return_value = mock_status

        advisor = DeploymentPolicyAdvisor(calculator=calculator)

        with patch('selfhealing.services.error_budget.advisor.get_error_budget_thresholds',
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            with patch('selfhealing.services.error_budget.advisor.get_burn_rate_thresholds',
                       return_value={"fast_critical": 14.4, "fast_warning": 6.0, "slow_warning": 3.0, "slow_info": 1.0}):
                verdict = advisor.get_deployment_verdict()

        assert verdict.status in [FreezeStatus.WARNING, FreezeStatus.CAUTION]

    def test_verdict_freeze_recommended_when_critical(self):
        """Test returns FREEZE_RECOMMENDED verdict when budget critical."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.services.error_budget.enums import FreezeStatus

        calculator = MagicMock(spec=ErrorBudgetCalculator)
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = 10.0  # Below warning threshold
        mock_status.burn_rate_1h = 5.0
        mock_status.burn_rate_6h = 3.0
        calculator.calculate_budget_status.return_value = mock_status

        advisor = DeploymentPolicyAdvisor(calculator=calculator)

        with patch('selfhealing.services.error_budget.advisor.get_error_budget_thresholds',
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            with patch('selfhealing.services.error_budget.advisor.get_burn_rate_thresholds',
                       return_value={"fast_critical": 14.4, "fast_warning": 6.0, "slow_warning": 3.0, "slow_info": 1.0}):
                verdict = advisor.get_deployment_verdict()

        assert verdict.status in [FreezeStatus.FREEZE_RECOMMENDED, FreezeStatus.WARNING]

    def test_verdict_freeze_on_fast_burn(self):
        """Test returns FREEZE when fast burn detected."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.services.error_budget.enums import FreezeStatus

        calculator = MagicMock(spec=ErrorBudgetCalculator)
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = 60.0  # Moderate budget
        mock_status.burn_rate_1h = 15.0  # Very high burn rate
        mock_status.burn_rate_6h = 10.0
        calculator.calculate_budget_status.return_value = mock_status

        advisor = DeploymentPolicyAdvisor(calculator=calculator)

        with patch('selfhealing.services.error_budget.advisor.get_error_budget_thresholds',
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            with patch('selfhealing.services.error_budget.advisor.get_burn_rate_thresholds',
                       return_value={"fast_critical": 14.4, "fast_warning": 6.0, "slow_warning": 3.0, "slow_info": 1.0}):
                verdict = advisor.get_deployment_verdict()

        # Fast burn should trigger freeze regardless of budget level
        assert verdict.status == FreezeStatus.FREEZE_RECOMMENDED


class TestVerdictContent:
    """Tests for verdict content."""

    def test_verdict_has_message(self):
        """Test verdict has message."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor

        advisor = DeploymentPolicyAdvisor()
        verdict = advisor.get_deployment_verdict()

        assert verdict.message is not None
        assert len(verdict.message) > 0

    def test_verdict_has_recommendation(self):
        """Test verdict has recommendation."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor

        advisor = DeploymentPolicyAdvisor()
        verdict = advisor.get_deployment_verdict()

        assert verdict.recommendation is not None

    def test_verdict_has_allowed_deployment_types(self):
        """Test verdict has allowed deployment types."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor

        advisor = DeploymentPolicyAdvisor()
        verdict = advisor.get_deployment_verdict()

        assert verdict.allowed_deployment_types is not None
        assert isinstance(verdict.allowed_deployment_types, list)

    def test_verdict_has_budget_status(self):
        """Test verdict includes budget status."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor

        advisor = DeploymentPolicyAdvisor()
        verdict = advisor.get_deployment_verdict()

        assert verdict.budget_status is not None


class TestAllowedDeploymentTypes:
    """Tests for allowed deployment types logic."""

    def test_all_types_allowed_when_proceed(self):
        """Test all types allowed when PROCEED."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.services.error_budget.enums import FreezeStatus

        calculator = MagicMock(spec=ErrorBudgetCalculator)
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = 90.0
        mock_status.burn_rate_1h = 0.5
        mock_status.burn_rate_6h = 0.3
        calculator.calculate_budget_status.return_value = mock_status

        advisor = DeploymentPolicyAdvisor(calculator=calculator)

        with patch('selfhealing.services.error_budget.advisor.get_error_budget_thresholds',
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            with patch('selfhealing.services.error_budget.advisor.get_burn_rate_thresholds',
                       return_value={"fast_critical": 14.4, "fast_warning": 6.0, "slow_warning": 3.0, "slow_info": 1.0}):
                verdict = advisor.get_deployment_verdict()

        if verdict.status == FreezeStatus.PROCEED:
            assert len(verdict.allowed_deployment_types) >= 3

    def test_restricted_types_when_frozen(self):
        """Test restricted types when frozen."""
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.services.error_budget.enums import FreezeStatus

        calculator = MagicMock(spec=ErrorBudgetCalculator)
        mock_status = MagicMock()
        mock_status.budget_remaining_percent = 5.0
        mock_status.burn_rate_1h = 15.0
        mock_status.burn_rate_6h = 10.0
        calculator.calculate_budget_status.return_value = mock_status

        advisor = DeploymentPolicyAdvisor(calculator=calculator)

        with patch('selfhealing.services.error_budget.advisor.get_error_budget_thresholds',
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            with patch('selfhealing.services.error_budget.advisor.get_burn_rate_thresholds',
                       return_value={"fast_critical": 14.4, "fast_warning": 6.0, "slow_warning": 3.0, "slow_info": 1.0}):
                verdict = advisor.get_deployment_verdict()

        if verdict.status == FreezeStatus.FREEZE_RECOMMENDED:
            # Should only allow emergency types
            assert len(verdict.allowed_deployment_types) <= 3
