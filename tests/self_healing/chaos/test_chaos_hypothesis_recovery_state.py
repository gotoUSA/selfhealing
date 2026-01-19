"""
Chaos Hypothesis 및 Recovery State 테스트

Tests for:
- FailureHypothesis data class
- 실험별 기대 가설 상수
- Chaos-Aware 메타데이터
- RECOVERY_MONITORING 상태
- Soft/Hard TTL 이중 구조
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, List


# =============================================================================
# FailureHypothesis Tests (§20.2)
# =============================================================================

class TestFailureHypothesis:
    """Test FailureHypothesis data class."""

    def test_failure_hypothesis_creation(self):
        """Test basic FailureHypothesis creation."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            description="CB Open 실험 시, 30초 내에 Canary Stage 1이 시작되어야 함",
            expected_recovery_time_seconds=60.0,
            expected_canary_stage="canary_1",
            expected_cb_state_after="half_open",
        )
        
        assert hypothesis.expected_recovery_time_seconds == 60.0
        assert hypothesis.expected_canary_stage == "canary_1"
        assert hypothesis.expected_cb_state_after == "half_open"
        assert "Canary Stage 1" in hypothesis.description

    def test_failure_hypothesis_defaults(self):
        """Test FailureHypothesis default values."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis()
        
        assert hypothesis.expected_recovery_time_seconds == 30.0
        assert hypothesis.expected_canary_stage is None
        assert hypothesis.expected_cb_state_after is None
        assert hypothesis.expected_fallback_activated is False
        assert hypothesis.tolerance_percent == 20.0

    def test_validate_recovery_time_pass(self):
        """Test validation passes when recovery time is within tolerance."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=30.0,
            tolerance_percent=20.0,
        )
        
        # 30 * 1.2 = 36초까지 허용
        passed, violations = hypothesis.validate(actual_recovery_time=35.0)
        
        assert passed is True
        assert len(violations) == 0

    def test_validate_recovery_time_fail(self):
        """Test validation fails when recovery time exceeds tolerance."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=30.0,
            tolerance_percent=20.0,
        )
        
        # 30 * 1.2 = 36초 초과
        passed, violations = hypothesis.validate(actual_recovery_time=45.0)
        
        assert passed is False
        assert len(violations) == 1
        assert "Recovery time" in violations[0]
        assert "45.0s" in violations[0]

    def test_validate_canary_stage_pass(self):
        """Test validation passes when canary stage matches."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=60.0,
            expected_canary_stage="canary_1",
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=30.0,
            actual_canary_stage="canary_1",
        )
        
        assert passed is True
        assert len(violations) == 0

    def test_validate_canary_stage_fail(self):
        """Test validation fails when canary stage doesn't match."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=60.0,
            expected_canary_stage="canary_1",
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=30.0,
            actual_canary_stage="canary_2",
        )
        
        assert passed is False
        assert len(violations) == 1
        assert "Canary stage" in violations[0]

    def test_validate_cb_state_pass(self):
        """Test validation passes when CB state matches."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=60.0,
            expected_cb_state_after="half_open",
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=30.0,
            actual_cb_state="half_open",
        )
        
        assert passed is True
        assert len(violations) == 0

    def test_validate_cb_state_fail(self):
        """Test validation fails when CB state doesn't match."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=60.0,
            expected_cb_state_after="half_open",
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=30.0,
            actual_cb_state="open",
        )
        
        assert passed is False
        assert len(violations) == 1
        assert "CB state" in violations[0]

    def test_validate_fallback_activation_pass(self):
        """Test validation passes when fallback is activated as expected."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=60.0,
            expected_fallback_activated=True,
            expected_fallback_type="cache",
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=30.0,
            actual_fallback_activated=True,
            actual_fallback_type="cache",
        )
        
        assert passed is True
        assert len(violations) == 0

    def test_validate_fallback_activation_fail(self):
        """Test validation fails when fallback is not activated."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=60.0,
            expected_fallback_activated=True,
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=30.0,
            actual_fallback_activated=False,
        )
        
        assert passed is False
        assert len(violations) == 1
        assert "Fallback" in violations[0]

    def test_validate_multiple_violations(self):
        """Test validation returns multiple violations."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=30.0,
            expected_canary_stage="canary_1",
            expected_cb_state_after="half_open",
            expected_fallback_activated=True,
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=100.0,  # 초과
            actual_canary_stage="canary_3",  # 불일치
            actual_cb_state="closed",  # 불일치
            actual_fallback_activated=False,  # 비활성화
        )
        
        assert passed is False
        assert len(violations) == 4

    def test_to_dict(self):
        """Test FailureHypothesis.to_dict() serialization."""
        from selfhealing.services.chaos.experiments import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            description="Test hypothesis",
            expected_recovery_time_seconds=60.0,
            expected_canary_stage="canary_1",
            expected_cb_state_after="half_open",
        )
        
        data = hypothesis.to_dict()
        
        assert data["expected_recovery_time_seconds"] == 60.0
        assert data["expected_canary_stage"] == "canary_1"
        assert data["expected_cb_state_after"] == "half_open"
        assert data["description"] == "Test hypothesis"


# =============================================================================
# 실험별 기대 가설 상수 Tests (§20.3)
# =============================================================================

class TestExperimentHypothesisConstants:
    """Test experiment-level hypothesis constants."""

    def test_cb_open_hypothesis_defined(self):
        """Test CB_OPEN_HYPOTHESIS is properly defined."""
        from selfhealing.services.chaos.experiments import (
            CB_OPEN_HYPOTHESIS,
            FailureHypothesis,
        )
        
        assert isinstance(CB_OPEN_HYPOTHESIS, FailureHypothesis)
        assert CB_OPEN_HYPOTHESIS.expected_recovery_time_seconds == 60.0
        assert CB_OPEN_HYPOTHESIS.expected_canary_stage == "canary_1"
        assert CB_OPEN_HYPOTHESIS.expected_cb_state_after == "half_open"
        assert CB_OPEN_HYPOTHESIS.expected_fallback_activated is True
        assert CB_OPEN_HYPOTHESIS.expected_fallback_type == "cache"

    def test_latency_injection_hypothesis_defined(self):
        """Test LATENCY_INJECTION_HYPOTHESIS is properly defined."""
        from selfhealing.services.chaos.experiments import (
            LATENCY_INJECTION_HYPOTHESIS,
            FailureHypothesis,
        )
        
        assert isinstance(LATENCY_INJECTION_HYPOTHESIS, FailureHypothesis)
        assert LATENCY_INJECTION_HYPOTHESIS.expected_recovery_time_seconds == 45.0
        assert LATENCY_INJECTION_HYPOTHESIS.expected_cb_state_after == "open"
        assert LATENCY_INJECTION_HYPOTHESIS.expected_fallback_activated is False

    def test_error_5xx_hypothesis_defined(self):
        """Test ERROR_5XX_HYPOTHESIS is properly defined."""
        from selfhealing.services.chaos.experiments import (
            ERROR_5XX_HYPOTHESIS,
            FailureHypothesis,
        )
        
        assert isinstance(ERROR_5XX_HYPOTHESIS, FailureHypothesis)
        assert ERROR_5XX_HYPOTHESIS.expected_recovery_time_seconds == 30.0
        assert ERROR_5XX_HYPOTHESIS.expected_cb_state_after == "open"
        assert ERROR_5XX_HYPOTHESIS.expected_fallback_activated is True


class TestExperimentClassesHaveHypothesis:
    """Test experiment classes have failure_hypothesis attribute."""

    def test_latency_injection_has_hypothesis(self):
        """Test LatencyInjectionExperiment has failure_hypothesis."""
        from selfhealing.services.chaos.experiments import (
            LatencyInjectionExperiment,
            LATENCY_INJECTION_HYPOTHESIS,
        )
        
        assert hasattr(LatencyInjectionExperiment, "failure_hypothesis")
        assert LatencyInjectionExperiment.failure_hypothesis is LATENCY_INJECTION_HYPOTHESIS

    def test_error_5xx_has_hypothesis(self):
        """Test Error5xxExperiment has failure_hypothesis."""
        from selfhealing.services.chaos.experiments import (
            Error5xxExperiment,
            ERROR_5XX_HYPOTHESIS,
        )
        
        assert hasattr(Error5xxExperiment, "failure_hypothesis")
        assert Error5xxExperiment.failure_hypothesis is ERROR_5XX_HYPOTHESIS

    def test_circuit_breaker_open_has_hypothesis(self):
        """Test CircuitBreakerOpenExperiment has failure_hypothesis."""
        from selfhealing.services.chaos.experiments import (
            CircuitBreakerOpenExperiment,
            CB_OPEN_HYPOTHESIS,
        )
        
        assert hasattr(CircuitBreakerOpenExperiment, "failure_hypothesis")
        assert CircuitBreakerOpenExperiment.failure_hypothesis is CB_OPEN_HYPOTHESIS


# =============================================================================
# Chaos-Aware 메타데이터 Tests (§14)
# =============================================================================

class TestChaosAwareMetadata:
    """Test Chaos-Aware metadata in EmergencyState."""

    def test_emergency_state_has_metadata_field(self):
        """Test EmergencyState has metadata field."""
        from selfhealing.services.emergency_mode.models import EmergencyState
        
        state = EmergencyState()
        assert hasattr(state, "metadata")
        assert state.metadata is None  # 기본값

    def test_activate_manual_with_chaos_experiment(self):
        """Test activate_manual sets is_chaos_experiment metadata."""
        from selfhealing.services.emergency_mode.manager import GracefulDegradationManager
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        
        # 싱글톤 초기화
        GracefulDegradationManager._instance = None
        manager = GracefulDegradationManager()
        
        # 카오스 실험으로 활성화
        state = manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Chaos Test: CB OPEN experiment",
            activated_by="chaos_engine",
            is_chaos_experiment=True,
            experiment_id="exp-123",
        )
        
        assert state.metadata is not None
        assert state.metadata["is_chaos_experiment"] is True
        assert state.metadata["experiment_id"] == "exp-123"
        assert state.metadata["classification"] == "chaos_induced_test"
        
        # 정리
        manager.deactivate("test")
        GracefulDegradationManager._instance = None

    def test_activate_manual_without_chaos_experiment(self):
        """Test activate_manual sets infrastructure_incident classification."""
        from selfhealing.services.emergency_mode.manager import GracefulDegradationManager
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        
        # 싱글톤 초기화
        GracefulDegradationManager._instance = None
        manager = GracefulDegradationManager()
        
        # 일반 활성화 (카오스 아님)
        state = manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="High error rate detected",
            activated_by="admin",
        )
        
        assert state.metadata is not None
        assert state.metadata["is_chaos_experiment"] is False
        assert state.metadata["classification"] == "infrastructure_incident"
        
        # 정리
        manager.deactivate("test")
        GracefulDegradationManager._instance = None

    def test_emergency_state_metadata_serialization(self):
        """Test EmergencyState.to_dict() includes metadata."""
        from selfhealing.services.emergency_mode.models import EmergencyState
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        
        state = EmergencyState(
            level=EmergencyLevel.LEVEL_2,
            is_active=True,
            metadata={
                "is_chaos_experiment": True,
                "experiment_id": "exp-456",
            },
        )
        
        data = state.to_dict()
        
        assert "metadata" in data
        assert data["metadata"]["is_chaos_experiment"] is True
        assert data["metadata"]["experiment_id"] == "exp-456"


# =============================================================================
# RECOVERY_MONITORING 상태 Tests (§15.2)
# =============================================================================

class TestRecoveryMonitoringStatus:
    """Test RECOVERY_MONITORING experiment status."""

    def test_recovery_monitoring_status_exists(self):
        """Test RECOVERY_MONITORING status is defined in ExperimentStatus."""
        from selfhealing.services.chaos.base import ExperimentStatus
        
        assert hasattr(ExperimentStatus, "RECOVERY_MONITORING")
        assert ExperimentStatus.RECOVERY_MONITORING.value == "recovery_monitoring"

    def test_all_experiment_statuses(self):
        """Test all expected experiment statuses exist."""
        from selfhealing.services.chaos.base import ExperimentStatus
        
        expected_statuses = [
            "PENDING",
            "AWAITING_APPROVAL",
            "RUNNING",
            "COMPLETED",
            "FAILED",
            "ABORTED",
            "SKIPPED",
            "ROLLED_BACK",
            "RECOVERY_MONITORING",  # 신규 추가
        ]
        
        for status_name in expected_statuses:
            assert hasattr(ExperimentStatus, status_name), f"{status_name} not found"


# =============================================================================
# Soft/Hard TTL 이중 구조 Tests (§15.4)
# =============================================================================

class TestSoftHardTTL:
    """Test Soft/Hard TTL dual structure in ExperimentConfig."""

    def test_grace_period_default(self):
        """Test grace_period_seconds has default value of 300."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig()
        assert config.grace_period_seconds == 300  # 5분

    def test_hard_ttl_calculation_with_ttl(self):
        """Test hard_ttl_seconds = ttl_seconds + grace_period_seconds."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(
            ttl_seconds=120,  # 2분
            grace_period_seconds=60,  # 1분
        )
        
        assert config.hard_ttl_seconds == 180  # 3분

    def test_hard_ttl_calculation_without_ttl(self):
        """Test hard_ttl_seconds uses default 600s when ttl_seconds is None."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(
            ttl_seconds=None,  # 기본값 사용
            grace_period_seconds=300,  # 5분
        )
        
        # 기본 TTL 600초 + grace period 300초
        assert config.hard_ttl_seconds == 900  # 15분

    def test_custom_grace_period(self):
        """Test custom grace_period_seconds."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(
            ttl_seconds=300,
            grace_period_seconds=600,  # 10분 grace period
        )
        
        assert config.grace_period_seconds == 600
        assert config.hard_ttl_seconds == 900


# =============================================================================
# Integration Tests
# =============================================================================

class TestPhase1Integration:
    """Integration tests for Phase 1 features."""

    def test_experiment_with_hypothesis_validation(self):
        """Test experiment instance can access failure_hypothesis."""
        from selfhealing.services.chaos.experiments import (
            LatencyInjectionExperiment,
            FailureHypothesis,
        )
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(
            target_service="payment-api",
            ttl_seconds=60,
        )
        
        experiment = LatencyInjectionExperiment(config)
        
        # 클래스 레벨 hypothesis 접근
        assert hasattr(experiment, "failure_hypothesis")
        assert isinstance(experiment.failure_hypothesis, FailureHypothesis)
        
        # 가설 검증 가능
        passed, violations = experiment.failure_hypothesis.validate(
            actual_recovery_time=30.0,
            actual_cb_state="open",
        )
        assert passed is True

    def test_config_ttl_structure(self):
        """Test ExperimentConfig with complete TTL structure."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(
            target_service="payment-api",
            ttl_seconds=120,  # Soft TTL: 2분
            grace_period_seconds=180,  # Grace: 3분
        )
        
        # Soft TTL
        assert config.ttl_seconds == 120
        
        # Grace Period
        assert config.grace_period_seconds == 180
        
        # Hard TTL = Soft + Grace
        assert config.hard_ttl_seconds == 300  # 5분
