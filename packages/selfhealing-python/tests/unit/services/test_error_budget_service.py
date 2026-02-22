"""
Error Budget Service Unit Tests

Tests for:
- ErrorBudgetCalculator
- DeploymentPolicyAdvisor
- FreezeDecisionRecorder
- ErrorBudgetService (integrated)
"""

from datetime import timedelta
from unittest.mock import Mock

from selfhealing.services.error_budget_service import (
    BURN_RATE_THRESHOLDS,
    ERROR_BUDGET_THRESHOLDS,
    DeploymentPolicyAdvisor,
    DeploymentVerdict,
    ErrorBudgetCalculator,
    ErrorBudgetService,
    ErrorBudgetStatus,
    FreezeDecisionRecord,
    FreezeDecisionRecorder,
    FreezeStatus,
    OverrideType,
    configure_error_budget_service,
    get_error_budget_service,
)
from selfhealing.slo import SLI, SLO, SLOConfig


class TestErrorBudgetCalculator:
    """Test ErrorBudgetCalculator class."""

    def test_calculate_budget_status_with_default_slo(self):
        """Test budget calculation with default SLO config."""
        calculator = ErrorBudgetCalculator()

        status = calculator.calculate_budget_status()

        assert isinstance(status, ErrorBudgetStatus)
        assert status.slo_name == "availability"
        assert status.slo_target == 0.999
        assert status.window_days == 30
        assert status.budget_total_minutes > 0

    def test_calculate_budget_status_with_custom_slo(self):
        """Test budget calculation with custom SLO."""
        custom_slo = SLO(
            name="test_slo",
            sli=SLI.AVAILABILITY,
            target=0.99,  # 99% = 1% error budget
            window_days=7,
        )
        config = SLOConfig(slos=[custom_slo])
        calculator = ErrorBudgetCalculator(slo_config=config)

        status = calculator.calculate_budget_status(slo_name="test_slo")

        assert status.slo_name == "test_slo"
        assert status.slo_target == 0.99
        assert status.window_days == 7
        # 7 days * 24 hours * 60 min * 1% = 100.8 minutes
        assert abs(status.budget_total_minutes - 100.8) < 0.1

    def test_calculate_budget_with_error_stats(self):
        """Test budget calculation with mocked error stats."""
        mock_stats_fn = Mock(return_value={"total_errors": 100})
        mock_request_fn = Mock(return_value={"total_requests": 100000})

        calculator = ErrorBudgetCalculator(
            get_failed_operation_stats=mock_stats_fn,
            get_request_stats=mock_request_fn,
        )

        status = calculator.calculate_budget_status()

        assert status.error_count_window == 100
        assert status.total_requests_window == 100000
        assert status.budget_consumed_minutes > 0

    def test_budget_remaining_percent_calculation(self):
        """Test that remaining percent is calculated correctly."""
        calculator = ErrorBudgetCalculator()

        status = calculator.calculate_budget_status()

        # Without errors, should be ~100%
        assert status.budget_remaining_percent >= 0
        assert status.budget_remaining_percent <= 100

    def test_error_budget_status_properties(self):
        """Test ErrorBudgetStatus computed properties."""
        status = ErrorBudgetStatus(
            slo_name="test",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=0,
            budget_remaining_minutes=43.2,
            budget_remaining_percent=100.0,
            burn_rate_1h=0.5,
            burn_rate_6h=0.5,
        )

        assert status.is_healthy is True
        assert status.is_critical is False
        assert status.has_fast_burn is False
        assert status.has_slow_burn is False

    def test_error_budget_status_critical(self):
        """Test critical state detection."""
        status = ErrorBudgetStatus(
            slo_name="test",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=40.0,
            budget_remaining_minutes=3.2,
            budget_remaining_percent=7.4,  # < 20%
            burn_rate_1h=15.0,  # > 14.4 = fast burn
            burn_rate_6h=4.0,  # > 3.0 = slow burn
        )

        assert status.is_healthy is False
        assert status.is_critical is True
        assert status.has_fast_burn is True
        assert status.has_slow_burn is True

    def test_error_budget_status_to_dict(self):
        """Test ErrorBudgetStatus serialization."""
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=10.0,
            budget_remaining_minutes=33.2,
            budget_remaining_percent=76.9,
        )

        result = status.to_dict()

        assert "slo" in result
        assert "budget" in result
        assert "burn_rate" in result
        assert "health" in result
        assert result["slo"]["name"] == "availability"
        assert result["budget"]["remaining_percent"] == 76.9


class TestDeploymentPolicyAdvisor:
    """Test DeploymentPolicyAdvisor class."""

    def test_get_deployment_verdict_proceed(self):
        """Test verdict when budget is healthy."""
        # Mock calculator to return healthy status
        mock_calculator = Mock()
        mock_calculator.calculate_budget_status.return_value = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=5.0,
            budget_remaining_minutes=38.2,
            budget_remaining_percent=88.4,  # > 75% = healthy
        )

        advisor = DeploymentPolicyAdvisor(calculator=mock_calculator)
        verdict = advisor.get_deployment_verdict()

        assert verdict.status == FreezeStatus.PROCEED
        assert verdict.can_deploy is True
        assert verdict.requires_override is False
        assert "feature" in verdict.allowed_deployment_types

    def test_get_deployment_verdict_caution(self):
        """Test verdict when budget is in caution zone."""
        mock_calculator = Mock()
        mock_calculator.calculate_budget_status.return_value = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=20.0,
            budget_remaining_minutes=23.2,
            budget_remaining_percent=53.7,  # 50-75% = caution
        )

        advisor = DeploymentPolicyAdvisor(calculator=mock_calculator)
        verdict = advisor.get_deployment_verdict()

        assert verdict.status == FreezeStatus.CAUTION
        assert verdict.can_deploy is True

    def test_get_deployment_verdict_warning(self):
        """Test verdict when budget is in warning zone."""
        mock_calculator = Mock()
        mock_calculator.calculate_budget_status.return_value = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=30.0,
            budget_remaining_minutes=13.2,
            budget_remaining_percent=30.6,  # 20-50% = warning
        )

        advisor = DeploymentPolicyAdvisor(calculator=mock_calculator)
        verdict = advisor.get_deployment_verdict()

        assert verdict.status == FreezeStatus.WARNING
        assert verdict.can_deploy is False
        assert "hotfix" in verdict.allowed_deployment_types
        assert "feature" not in verdict.allowed_deployment_types

    def test_get_deployment_verdict_freeze_recommended(self):
        """Test verdict when budget is exhausted."""
        mock_calculator = Mock()
        mock_calculator.calculate_budget_status.return_value = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=40.0,
            budget_remaining_minutes=3.2,
            budget_remaining_percent=7.4,  # < 20% = freeze
        )

        advisor = DeploymentPolicyAdvisor(calculator=mock_calculator)
        verdict = advisor.get_deployment_verdict()

        assert verdict.status == FreezeStatus.FREEZE_RECOMMENDED
        assert verdict.can_deploy is False
        assert verdict.requires_override is True
        assert "security_patch" in verdict.allowed_deployment_types
        assert "rollback" in verdict.allowed_deployment_types
        assert "feature" not in verdict.allowed_deployment_types
        assert "hotfix" not in verdict.allowed_deployment_types

    def test_get_deployment_verdict_fast_burn(self):
        """Test verdict when fast burn is detected."""
        mock_calculator = Mock()
        mock_calculator.calculate_budget_status.return_value = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=10.0,
            budget_remaining_minutes=33.2,
            budget_remaining_percent=76.9,
            burn_rate_1h=15.0,  # > 14.4 = critical fast burn
        )

        advisor = DeploymentPolicyAdvisor(calculator=mock_calculator)
        verdict = advisor.get_deployment_verdict()

        # Fast burn triggers freeze even if budget looks OK
        assert verdict.status == FreezeStatus.FREEZE_RECOMMENDED
        assert "Fast Burn Rate" in verdict.reasons[0]

    def test_deployment_verdict_to_dict(self):
        """Test DeploymentVerdict serialization."""
        mock_calculator = Mock()
        mock_calculator.calculate_budget_status.return_value = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=0,
            budget_remaining_minutes=43.2,
            budget_remaining_percent=100.0,
        )

        advisor = DeploymentPolicyAdvisor(calculator=mock_calculator)
        verdict = advisor.get_deployment_verdict()

        result = verdict.to_dict()

        assert "verdict" in result
        assert "message" in result
        assert "recommendation" in result
        assert "reasons" in result
        assert "allowed_deployment_types" in result
        assert "budget_status" in result

    def test_check_active_override_none(self):
        """Test no active override."""
        advisor = DeploymentPolicyAdvisor()

        result = advisor.check_active_override()

        assert result is None


class TestFreezeDecisionRecorder:
    """Test FreezeDecisionRecorder class."""

    def test_record_freeze_acknowledged(self):
        """Test recording freeze acknowledgment."""
        mock_advisor = Mock()
        mock_advisor.get_deployment_verdict.return_value = DeploymentVerdict(
            status=FreezeStatus.FREEZE_RECOMMENDED,
            budget_status=ErrorBudgetStatus(
                slo_name="availability",
                slo_target=0.999,
                window_days=30,
                budget_total_minutes=43.2,
                budget_consumed_minutes=40.0,
                budget_remaining_minutes=3.2,
                budget_remaining_percent=7.4,
            ),
            message="Test",
            recommendation="Test",
        )

        recorder = FreezeDecisionRecorder(advisor=mock_advisor)

        record = recorder.record_freeze_acknowledged(decided_by="admin", justification="Critical situation")

        assert record.decision_type == "freeze_acknowledged"
        assert record.decided_by == "admin"
        assert record.justification == "Critical situation"
        assert record.budget_remaining_percent == 7.4

    def test_record_override_approved(self):
        """Test recording override approval."""
        mock_advisor = Mock()
        mock_advisor.get_deployment_verdict.return_value = DeploymentVerdict(
            status=FreezeStatus.FREEZE_RECOMMENDED,
            budget_status=ErrorBudgetStatus(
                slo_name="availability",
                slo_target=0.999,
                window_days=30,
                budget_total_minutes=43.2,
                budget_consumed_minutes=40.0,
                budget_remaining_minutes=3.2,
                budget_remaining_percent=7.4,
            ),
            message="Test",
            recommendation="Test",
        )
        mock_advisor._active_overrides = {}

        recorder = FreezeDecisionRecorder(advisor=mock_advisor)

        record = recorder.record_override_approved(
            decided_by="cto",
            justification="Emergency security patch",
            override_type=OverrideType.SECURITY_PATCH,
            deployment_name="auth-service v2.1.0",
            expires_hours=2,
        )

        assert record.decision_type == "override_approved"
        assert record.decided_by == "cto"
        assert record.override_type == OverrideType.SECURITY_PATCH
        assert record.deployment_name == "auth-service v2.1.0"
        assert record.expires_at is not None

    def test_record_freeze_lifted(self):
        """Test recording freeze lift."""
        mock_advisor = Mock()
        mock_advisor.get_deployment_verdict.return_value = DeploymentVerdict(
            status=FreezeStatus.PROCEED,
            budget_status=ErrorBudgetStatus(
                slo_name="availability",
                slo_target=0.999,
                window_days=30,
                budget_total_minutes=43.2,
                budget_consumed_minutes=5.0,
                budget_remaining_minutes=38.2,
                budget_remaining_percent=88.4,
            ),
            message="Test",
            recommendation="Test",
        )
        mock_advisor._active_overrides = {"test": Mock()}

        recorder = FreezeDecisionRecorder(advisor=mock_advisor)

        record = recorder.record_freeze_lifted(decided_by="admin", justification="Budget recovered")

        assert record.decision_type == "freeze_lifted"
        # Overrides should be cleared
        assert len(mock_advisor._active_overrides) == 0

    def test_get_decision_history(self):
        """Test retrieving decision history."""
        mock_advisor = Mock()
        mock_advisor.get_deployment_verdict.return_value = DeploymentVerdict(
            status=FreezeStatus.PROCEED,
            budget_status=ErrorBudgetStatus(
                slo_name="availability",
                slo_target=0.999,
                window_days=30,
                budget_total_minutes=43.2,
                budget_consumed_minutes=0,
                budget_remaining_minutes=43.2,
                budget_remaining_percent=100.0,
            ),
            message="Test",
            recommendation="Test",
        )
        mock_advisor._active_overrides = {}

        recorder = FreezeDecisionRecorder(advisor=mock_advisor)

        # Create some records
        recorder.record_freeze_acknowledged("user1", "Test 1")
        recorder.record_freeze_lifted("user2", "Test 2")

        history = recorder.get_decision_history(limit=10)

        assert len(history) == 2
        # Check both types are present
        decision_types = [h.decision_type for h in history]
        assert "freeze_acknowledged" in decision_types
        assert "freeze_lifted" in decision_types

    def test_decision_record_to_dict(self):
        """Test FreezeDecisionRecord serialization."""
        from selfhealing.core.timezone import now

        record = FreezeDecisionRecord(
            decision_id="test_123",
            decision_type="override_approved",
            decided_by="admin",
            decided_at=now(),
            budget_remaining_percent=15.0,
            freeze_status=FreezeStatus.FREEZE_RECOMMENDED,
            justification="Emergency fix",
            override_type=OverrideType.HOTFIX,
        )

        result = record.to_dict()

        assert result["decision_id"] == "test_123"
        assert result["decision_type"] == "override_approved"
        assert result["override_type"] == "hotfix"
        assert result["freeze_status"] == "freeze_recommended"


class TestErrorBudgetService:
    """Test integrated ErrorBudgetService."""

    def test_get_budget_status(self):
        """Test getting budget status through service."""
        service = ErrorBudgetService()

        status = service.get_budget_status()

        assert isinstance(status, ErrorBudgetStatus)
        assert status.slo_name == "availability"

    def test_get_deployment_verdict(self):
        """Test getting deployment verdict through service."""
        service = ErrorBudgetService()

        verdict = service.get_deployment_verdict()

        assert isinstance(verdict, DeploymentVerdict)
        assert verdict.status in FreezeStatus

    def test_full_workflow(self):
        """Test complete workflow: verdict -> acknowledge -> override -> lift."""
        service = ErrorBudgetService()

        # 1. Get initial verdict
        verdict = service.get_deployment_verdict()
        assert verdict is not None

        # 2. Acknowledge freeze
        ack_record = service.acknowledge_freeze(decided_by="ops_lead", justification="Acknowledging freeze recommendation")
        assert ack_record.decision_type == "freeze_acknowledged"

        # 3. Approve override
        override_record = service.approve_override(
            decided_by="cto",
            justification="Critical security patch",
            override_type=OverrideType.SECURITY_PATCH,
            deployment_name="payment-v1.0.1",
        )
        assert override_record.decision_type == "override_approved"

        # 4. Check active override
        active = service.check_active_override()
        assert active is not None
        assert active.override_type == OverrideType.SECURITY_PATCH

        # 5. Lift freeze
        lift_record = service.lift_freeze(decided_by="ops_lead", justification="Situation resolved")
        assert lift_record.decision_type == "freeze_lifted"

        # 6. Check history
        history = service.get_decision_history()
        assert len(history) == 3

    def test_service_singleton(self):
        """Test that get_error_budget_service returns singleton."""
        service1 = get_error_budget_service()
        service2 = get_error_budget_service()

        assert service1 is service2

    def test_configure_service(self):
        """Test service configuration."""
        mock_stats = Mock(return_value={"total_errors": 50})

        service = configure_error_budget_service(
            get_failed_operation_stats=mock_stats,
        )

        # Should use the configured stats function
        status = service.get_budget_status()
        assert status.error_count_window == 50


class TestConstants:
    """Test constant values."""

    def test_error_budget_thresholds(self):
        """Test that thresholds are properly ordered."""
        assert ERROR_BUDGET_THRESHOLDS["healthy"] > ERROR_BUDGET_THRESHOLDS["caution"]
        assert ERROR_BUDGET_THRESHOLDS["caution"] > ERROR_BUDGET_THRESHOLDS["warning"]
        assert ERROR_BUDGET_THRESHOLDS["warning"] > ERROR_BUDGET_THRESHOLDS["critical"]

    def test_burn_rate_thresholds(self):
        """Test burn rate threshold values."""
        assert BURN_RATE_THRESHOLDS["fast_critical"] == 14.4
        assert BURN_RATE_THRESHOLDS["slow_warning"] == 3.0


class TestOverrideExpiration:
    """Test override expiration logic."""

    def test_expired_override_not_returned(self):
        """Test that expired overrides are filtered out."""
        from selfhealing.core.timezone import now

        advisor = DeploymentPolicyAdvisor()

        # Add an expired override
        expired_record = FreezeDecisionRecord(
            decision_id="expired_123",
            decision_type="override_approved",
            decided_by="admin",
            decided_at=now() - timedelta(hours=10),
            budget_remaining_percent=10.0,
            freeze_status=FreezeStatus.FREEZE_RECOMMENDED,
            justification="Test",
            override_type=OverrideType.HOTFIX,
            expires_at=now() - timedelta(hours=5),  # Expired 5 hours ago
        )
        advisor._active_overrides["expired_123"] = expired_record

        # Should return None (expired override filtered out)
        result = advisor.check_active_override()

        assert result is None
        assert "expired_123" not in advisor._active_overrides


class TestFailSafeResponses:
    """Test Fail-Safe response generation for API views."""

    def test_failsafe_verdict_response(self):
        """Test fail-safe verdict returns PROCEED (fail-open)."""
        from selfhealing.services.error_budget_service import (
            get_failsafe_verdict_response,
        )

        response = get_failsafe_verdict_response("Database connection error")

        # Fail-open: 기본 PROCEED
        assert response["status"] == "degraded"
        assert response["data"]["verdict"]["status"] == "proceed"
        assert response["data"]["verdict"]["can_deploy"] is True
        assert response["degraded_mode"] is True
        assert response["failsafe_applied"] is True
        assert "Database connection error" in response["error"]
        # 모든 배포 유형 허용
        assert "feature" in response["data"]["allowed_deployment_types"]
        assert "hotfix" in response["data"]["allowed_deployment_types"]

    def test_failsafe_status_response(self):
        """Test fail-safe status returns healthy (fail-open)."""
        from selfhealing.services.error_budget_service import (
            get_failsafe_status_response,
        )

        response = get_failsafe_status_response("Redis timeout")

        # Fail-open: 건강하다고 가정
        assert response["status"] == "degraded"
        assert response["data"]["health"]["is_healthy"] is True
        assert response["data"]["health"]["is_critical"] is False
        assert response["data"]["budget"]["remaining_percent"] == 100.0
        assert response["degraded_mode"] is True
        assert response["failsafe_applied"] is True

    def test_failsafe_does_not_block_cicd(self):
        """
        Test that fail-safe design ensures CI/CD is never blocked.

        Core principle: Error Budget 시스템 장애로 인해
        배포 파이프라인이 중단되면 안 됨.
        """
        from selfhealing.services.error_budget_service import (
            get_failsafe_verdict_response,
        )

        # 어떤 에러가 발생해도
        for error in [
            "Database connection refused",
            "Redis timeout",
            "Internal server error",
            "SLO not found",
            None,  # 예외 없이도 테스트
        ]:
            if error:
                response = get_failsafe_verdict_response(error)
                # 항상 배포 가능
                assert response["data"]["verdict"]["can_deploy"] is True
                assert response["data"]["verdict"]["status"] == "proceed"


class TestNegativeErrorBudget:
    """Test negative Error Budget scenarios (SLO violation)."""

    def test_error_budget_can_go_negative(self):
        """Test that Error Budget can go negative when SLO is violated."""
        # consumed_ratio > 1.0 → remaining_percent < 0
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=86.4,  # 200% consumed
            budget_remaining_minutes=-43.2,  # negative
            budget_remaining_percent=-100.0,  # negative
        )

        assert status.budget_remaining_percent < 0
        assert status.budget_remaining_minutes < 0
        assert status.is_over_budget is True
        assert status.is_healthy is False
        assert status.is_critical is True

    def test_is_over_budget_property(self):
        """Test is_over_budget property correctly identifies SLO violation."""
        # Just barely over budget
        slightly_over = ErrorBudgetStatus(
            slo_name="test",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=44.0,
            budget_remaining_minutes=-0.8,
            budget_remaining_percent=-1.85,
        )
        assert slightly_over.is_over_budget is True

        # Exactly at 0%
        at_zero = ErrorBudgetStatus(
            slo_name="test",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=43.2,
            budget_remaining_minutes=0,
            budget_remaining_percent=0,
        )
        assert at_zero.is_over_budget is False

        # Healthy budget
        healthy = ErrorBudgetStatus(
            slo_name="test",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=10.0,
            budget_remaining_minutes=33.2,
            budget_remaining_percent=76.9,
        )
        assert healthy.is_over_budget is False

    def test_negative_budget_in_to_dict(self):
        """Test that to_dict() correctly serializes negative budget values."""
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=64.8,
            budget_remaining_minutes=-21.6,
            budget_remaining_percent=-50.0,
        )

        result = status.to_dict()

        assert result["budget"]["remaining_percent"] == -50.0
        assert result["budget"]["remaining_minutes"] == -21.6
        assert result["budget"]["is_over_budget"] is True
        assert result["health"]["is_over_budget"] is True
        assert result["health"]["is_critical"] is True

    def test_calculator_allows_over_100_percent_consumption(self):
        """Test calculator doesn't clamp consumed_ratio at 1.0."""
        # Mock high error rate (5% when only 0.1% allowed)
        mock_stats_fn = Mock(return_value={"total_errors": 5000})
        mock_request_fn = Mock(return_value={"total_requests": 100000})  # 5% error rate

        calculator = ErrorBudgetCalculator(
            get_failed_operation_stats=mock_stats_fn,
            get_request_stats=mock_request_fn,
        )

        status = calculator.calculate_budget_status()

        # 5% error / 0.1% allowed = 50x over budget
        # So consumed should be 5000% (50x)
        assert status.budget_remaining_percent < 0, (
            f"Expected negative remaining, got {status.budget_remaining_percent}"
        )
        assert status.is_over_budget is True, "Should be over budget with 5% error rate"
