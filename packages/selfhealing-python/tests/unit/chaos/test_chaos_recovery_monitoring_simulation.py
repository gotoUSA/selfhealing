"""
Phase 5 Unit Tests - Chaos System Integration (32_CHAOS_SYSTEM_INTEGRATION.md)

Tests for:
- Phase 5-1: 비동기 복구 모니터링 (§15)
  - is_hard_ttl_expired()
  - complete_recovery_monitoring()
  - force_complete()
  - transition_to_recovery_monitoring()
  
- Phase 5-2: 시뮬레이션 인터페이스 (§16)
  - PoolMonitor.set_simulation_override()
  - ConnectionHealthMonitor.set_simulation_override()
  - set_partition_simulation()
  
- Phase 5-3: Load Shedding 연동 (§6, §22.2.1)
  - PartialFailureExperiment._trigger_load_shedding()
  - PartialFailureExperiment._verify_shedding_behavior()
  - PartialFailureExperiment._deactivate_load_shedding()
  
- Phase 5-4: 모니터 스냅샷 (§13, §22.2.4)
  - _get_pool_state_snapshot()
  - _get_cert_state_snapshot()
  - _get_connection_health_snapshot()
  - capture_comprehensive_snapshot()

Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §22.5
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any


# Helper: Use LatencyInjectionExperiment as concrete implementation of ChaosExperiment
def create_test_experiment(config):
    """Create a concrete experiment instance for testing base class methods."""
    from selfhealing.services.chaos.experiments import LatencyInjectionExperiment
    return LatencyInjectionExperiment(config=config)  # Must use keyword argument


# =============================================================================
# Phase 5-1: 비동기 복구 모니터링 Tests (§15)
# =============================================================================

class TestHardTTLExpiration:
    """Test is_hard_ttl_expired() method."""

    def test_hard_ttl_not_expired_within_grace_period(self):
        """Test that hard TTL is not expired within grace period."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(
            target_service="test-service",
            ttl_seconds=60,
            grace_period_seconds=30,
        )
        
        experiment = create_test_experiment(config)
        # Set expires_at to now (soft TTL just expired)
        experiment._expires_at = datetime.now(timezone.utc)
        
        # Hard TTL should NOT be expired yet (within grace period)
        assert experiment.is_hard_ttl_expired() is False

    def test_hard_ttl_expired_after_grace_period(self):
        """Test that hard TTL is expired after grace period."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(
            target_service="test-service",
            ttl_seconds=60,
            grace_period_seconds=30,
        )
        
        experiment = create_test_experiment(config)
        # Set expires_at to past so that now > expires_at + grace_period
        # expires_at + 30s (grace) should be in the past
        # Set to 60 seconds ago to ensure hard TTL (30s grace) is definitely expired
        experiment._expires_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        
        # Hard TTL = expires_at + grace_period = 60s ago + 30s = 30s ago
        # 30s ago < now, so hard TTL SHOULD be expired
        assert experiment.is_hard_ttl_expired() is True

    def test_hard_ttl_no_expires_at(self):
        """Test is_hard_ttl_expired returns False when no expires_at set."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        experiment._expires_at = None
        
        assert experiment.is_hard_ttl_expired() is False


class TestCompleteRecoveryMonitoring:
    """Test complete_recovery_monitoring() method."""

    def test_complete_recovery_monitoring_from_recovery_monitoring_status(self):
        """Test completing recovery monitoring from RECOVERY_MONITORING status."""
        from selfhealing.services.chaos.base import ExperimentConfig, ExperimentStatus
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        experiment.status = ExperimentStatus.RECOVERY_MONITORING
        
        experiment.complete_recovery_monitoring()
        
        assert experiment.status == ExperimentStatus.COMPLETED
        assert experiment.completed_at is not None

    def test_complete_recovery_monitoring_from_wrong_status(self):
        """Test that completing from wrong status logs warning."""
        from selfhealing.services.chaos.base import ExperimentConfig, ExperimentStatus
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        experiment.status = ExperimentStatus.RUNNING
        
        experiment.complete_recovery_monitoring()
        
        # Status should remain unchanged
        assert experiment.status == ExperimentStatus.RUNNING


class TestForceComplete:
    """Test force_complete() method."""

    def test_force_complete_with_reason(self):
        """Test force completing an experiment with reason."""
        from selfhealing.services.chaos.base import ExperimentConfig, ExperimentStatus
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        experiment.status = ExperimentStatus.RECOVERY_MONITORING
        
        experiment.force_complete(reason="hard_ttl_expired")
        
        assert experiment.status == ExperimentStatus.COMPLETED
        assert experiment.completed_at is not None

    def test_force_complete_from_any_status(self):
        """Test force_complete works from any status."""
        from selfhealing.services.chaos.base import ExperimentConfig, ExperimentStatus
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        experiment.status = ExperimentStatus.RUNNING
        
        experiment.force_complete(reason="admin_intervention")
        
        assert experiment.status == ExperimentStatus.COMPLETED


class TestTransitionToRecoveryMonitoring:
    """Test transition_to_recovery_monitoring() method."""

    def test_transition_from_running(self):
        """Test transition from RUNNING to RECOVERY_MONITORING."""
        from selfhealing.services.chaos.base import ExperimentConfig, ExperimentStatus
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        experiment.status = ExperimentStatus.RUNNING
        
        experiment.transition_to_recovery_monitoring()
        
        assert experiment.status == ExperimentStatus.RECOVERY_MONITORING


# =============================================================================
# Phase 5-2: 시뮬레이션 인터페이스 Tests (§16)
# =============================================================================

class TestPoolMonitorSimulationOverride:
    """Test ConnectionPoolMonitor.set_simulation_override()."""

    def test_set_simulation_override(self):
        """Test setting simulation override."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )
        
        monitor = ConnectionPoolMonitor()
        
        monitor.set_simulation_override(
            health_status=PoolHealthStatus.EXHAUSTED,
            experiment_id="exp-123",
        )
        
        assert monitor.is_simulation_active() is True
        assert monitor.get_simulation_experiment_id() == "exp-123"
        assert monitor._simulation_override == PoolHealthStatus.EXHAUSTED

    def test_clear_simulation_override(self):
        """Test clearing simulation override."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )
        
        monitor = ConnectionPoolMonitor()
        monitor.set_simulation_override(
            health_status=PoolHealthStatus.CRITICAL,
            experiment_id="exp-456",
        )
        
        monitor.clear_simulation_override()
        
        assert monitor.is_simulation_active() is False
        assert monitor._simulation_override is None

    def test_check_health_returns_simulated_status(self):
        """Test that check_health returns simulated status when override is set."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )
        
        monitor = ConnectionPoolMonitor()
        monitor.set_simulation_override(
            health_status=PoolHealthStatus.EXHAUSTED,
            experiment_id="exp-789",
        )
        
        status, stats = monitor.check_health()
        
        assert status == PoolHealthStatus.EXHAUSTED
        assert stats.active_connections == 100  # Default simulated stats


class TestConnectionHealthMonitorSimulationOverride:
    """Test DefaultConnectionHealthMonitor.set_simulation_override()."""

    def test_set_simulation_override(self):
        """Test setting simulation override for connection."""
        from selfhealing.core.connection_health import (
            DefaultConnectionHealthMonitor,
            ConnectionType,
            ConnectionStatus,
        )
        
        monitor = DefaultConnectionHealthMonitor()
        
        monitor.set_simulation_override(
            connection_type=ConnectionType.DATABASE,
            name="primary",
            status=ConnectionStatus.UNHEALTHY,
            experiment_id="exp-123",
        )
        
        assert monitor.is_simulation_active() is True
        assert monitor.get_simulation_experiment_id() == "exp-123"

    def test_set_partition_simulation(self):
        """Test setting partition simulation."""
        from selfhealing.core.connection_health import (
            DefaultConnectionHealthMonitor,
            PartitionState,
        )
        
        monitor = DefaultConnectionHealthMonitor()
        # PartitionState uses db_available, cache_available as fields
        # is_partial_partition and is_full_partition are computed properties
        partition = PartitionState(
            db_available=True,
            cache_available=False,  # This creates a partial partition
        )
        
        monitor.set_partition_simulation(
            partition_state=partition,
            experiment_id="exp-partition-1",
        )
        
        assert monitor.is_simulation_active() is True
        assert monitor._partition_override == partition
        assert partition.is_partial_partition is True  # Computed property

    def test_clear_all_simulation_overrides(self):
        """Test clearing all simulation overrides."""
        from selfhealing.core.connection_health import (
            DefaultConnectionHealthMonitor,
            ConnectionType,
            ConnectionStatus,
            PartitionState,
        )
        
        monitor = DefaultConnectionHealthMonitor()
        monitor.set_simulation_override(
            connection_type=ConnectionType.DATABASE,
            name="primary",
            status=ConnectionStatus.UNHEALTHY,
        )
        monitor.set_partition_simulation(
            partition_state=PartitionState(
                db_available=True,
                cache_available=False,
            ),
        )
        
        monitor.clear_all_simulation_overrides()
        
        assert monitor.is_simulation_active() is False
        assert len(monitor._simulation_overrides) == 0
        assert monitor._partition_override is None


# =============================================================================
# Phase 5-3: Load Shedding 연동 Tests (§6, §22.2.1)
# =============================================================================

class TestTriggerLoadShedding:
    """Test PartialFailureExperiment._trigger_load_shedding()."""

    @patch("selfhealing.services.circuit_breaker.load_shedding.get_load_shedding_manager")
    def test_trigger_load_shedding(self, mock_get_manager):
        """Test triggering load shedding."""
        from selfhealing.services.chaos.experiments import PartialFailureExperiment
        from selfhealing.services.chaos.base import ExperimentConfig
        
        # Mock LoadSheddingManager
        mock_manager = MagicMock()
        mock_before_status = MagicMock()
        mock_before_status.active = False
        mock_before_status.current_level_index = -1
        mock_before_status.to_dict.return_value = {
            "active": False,
            "current_level_index": -1,
        }
        
        mock_after_status = MagicMock()
        mock_after_status.active = True
        mock_after_status.current_level_index = 0
        mock_after_status.to_dict.return_value = {
            "active": True,
            "current_level_index": 0,
        }
        
        mock_manager.get_status.side_effect = [mock_before_status, mock_after_status]
        mock_get_manager.return_value = mock_manager
        
        config = ExperimentConfig(
            target_service="test-service",
            parameters={"trigger_shedding": True},
        )
        experiment = PartialFailureExperiment(config)
        
        result = experiment._trigger_load_shedding()
        
        assert result["shedding_triggered"] is True
        assert result["after"]["active"] is True
        mock_manager.force_activate.assert_called_once()

    @patch("selfhealing.services.circuit_breaker.load_shedding.get_load_shedding_manager")
    def test_trigger_load_shedding_already_active(self, mock_get_manager):
        """Test triggering when shedding is already active."""
        from selfhealing.services.chaos.experiments import PartialFailureExperiment
        from selfhealing.services.chaos.base import ExperimentConfig
        
        mock_manager = MagicMock()
        mock_status = MagicMock()
        mock_status.active = True
        mock_status.current_level_index = 1
        mock_status.to_dict.return_value = {"active": True, "current_level_index": 1}
        mock_manager.get_status.return_value = mock_status
        mock_get_manager.return_value = mock_manager
        
        config = ExperimentConfig(
            target_service="test-service",
            parameters={"trigger_shedding": True},
        )
        experiment = PartialFailureExperiment(config)
        
        result = experiment._trigger_load_shedding()
        
        # Should not trigger since already active
        assert result["shedding_triggered"] is False
        mock_manager.force_activate.assert_not_called()


class TestVerifySheddingBehavior:
    """Test PartialFailureExperiment._verify_shedding_behavior()."""

    @patch("selfhealing.services.circuit_breaker.load_shedding.get_load_shedding_manager")
    def test_verify_shedding_behavior(self, mock_get_manager):
        """Test verifying shedding behavior."""
        from selfhealing.services.chaos.experiments import PartialFailureExperiment
        from selfhealing.services.chaos.base import ExperimentConfig
        
        mock_manager = MagicMock()
        mock_status = MagicMock()
        mock_status.active = True
        mock_status.current_level_index = 0
        mock_status.current_level_description = "Level 1: Low priority shedding"
        mock_status.shed_services = ["review-api", "recommendation-api"]
        mock_status.shed_criticality = ["low"]
        mock_status.traffic_limit = 50.0
        mock_status.timestamp = "2026-01-09T12:00:00Z"
        mock_manager.get_status.return_value = mock_status
        mock_get_manager.return_value = mock_manager
        
        config = ExperimentConfig(target_service="test-service")
        experiment = PartialFailureExperiment(config)
        
        result = experiment._verify_shedding_behavior()
        
        assert result["shedding_active"] is True
        assert result["current_level_index"] == 0
        assert "review-api" in result["shed_services"]
        assert result["traffic_limit"] == 50.0


class TestDeactivateLoadShedding:
    """Test PartialFailureExperiment._deactivate_load_shedding()."""

    @patch("selfhealing.services.circuit_breaker.load_shedding.get_load_shedding_manager")
    def test_deactivate_load_shedding(self, mock_get_manager):
        """Test deactivating load shedding on rollback."""
        from selfhealing.services.chaos.experiments import PartialFailureExperiment
        from selfhealing.services.chaos.base import ExperimentConfig
        
        mock_manager = MagicMock()
        mock_manager.is_shedding_active.return_value = True
        mock_get_manager.return_value = mock_manager
        
        config = ExperimentConfig(target_service="test-service")
        experiment = PartialFailureExperiment(config)
        
        experiment._deactivate_load_shedding()
        
        mock_manager.force_deactivate.assert_called_once()

    @patch("selfhealing.services.circuit_breaker.load_shedding.get_load_shedding_manager")
    def test_deactivate_load_shedding_not_active(self, mock_get_manager):
        """Test that deactivate does nothing if not active."""
        from selfhealing.services.chaos.experiments import PartialFailureExperiment
        from selfhealing.services.chaos.base import ExperimentConfig
        
        mock_manager = MagicMock()
        mock_manager.is_shedding_active.return_value = False
        mock_get_manager.return_value = mock_manager
        
        config = ExperimentConfig(target_service="test-service")
        experiment = PartialFailureExperiment(config)
        
        experiment._deactivate_load_shedding()
        
        mock_manager.force_deactivate.assert_not_called()


# =============================================================================
# Phase 5-4: 모니터 스냅샷 Tests (§13, §22.2.4)
# =============================================================================

class TestPoolStateSnapshot:
    """Test _get_pool_state_snapshot() method."""

    def test_pool_state_snapshot_no_provider(self):
        """Test pool state snapshot when no stats provider."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        
        result = experiment._get_pool_state_snapshot()
        
        assert result["available"] is False
        assert "no_stats_provider" in result.get("reason", "")

    @patch("selfhealing.core.pool_monitor.ConnectionPoolMonitor")
    def test_pool_state_snapshot_success(self, MockMonitor):
        """Test successful pool state snapshot."""
        from selfhealing.services.chaos.base import ExperimentConfig
        from selfhealing.core.pool_monitor import PoolHealthStatus, PoolStats
        
        # Mock the monitor
        mock_instance = MagicMock()
        mock_instance._stats_provider = True
        mock_stats = PoolStats(
            pool_name="test_pool",
            max_connections=100,
            active_connections=50,
            available_connections=50,
            waiting_requests=0,
        )
        mock_instance.check_health.return_value = (PoolHealthStatus.HEALTHY, mock_stats)
        MockMonitor.return_value = mock_instance
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        
        result = experiment._get_pool_state_snapshot()
        
        assert result["health_status"] == "healthy"
        assert result["active_connections"] == 50


class TestCertStateSnapshot:
    """Test _get_cert_state_snapshot() method."""

    def test_cert_state_snapshot(self):
        """Test cert state snapshot."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        
        result = experiment._get_cert_state_snapshot()
        
        # Should return check_performed=True or check_performed=False
        assert "check_performed" in result or "reason" in result


class TestConnectionHealthSnapshot:
    """Test _get_connection_health_snapshot() method."""

    def test_connection_health_snapshot(self):
        """Test connection health snapshot."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        
        result = experiment._get_connection_health_snapshot()
        
        # Should have partition state info or error
        assert "is_partial_partition" in result or "available" in result or "error" in result


class TestCaptureComprehensiveSnapshot:
    """Test capture_comprehensive_snapshot() method."""

    def test_capture_comprehensive_snapshot_includes_all(self):
        """Test that comprehensive snapshot includes all components."""
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(target_service="test-service")
        experiment = create_test_experiment(config)
        
        result = experiment.capture_comprehensive_snapshot()
        
        # Should include all Phase 4 components
        assert "circuit_breaker" in result
        assert "corruption_shield" in result
        assert "dlq" in result
        assert "throttle" in result
        
        # Should include Phase 5-4 monitor snapshots
        assert "pool" in result
        assert "cert" in result
        assert "connection_health" in result
        
        # Should have timestamp
        assert "timestamp" in result


# =============================================================================
# Integration Tests
# =============================================================================

class TestPhase5Integration:
    """Integration tests for Phase 5 features."""

    def test_simulation_override_to_snapshot_flow(self):
        """Test flow from simulation override to snapshot capture."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )
        from selfhealing.services.chaos.base import ExperimentConfig
        
        # Set up simulation override
        monitor = ConnectionPoolMonitor()
        monitor.set_simulation_override(
            health_status=PoolHealthStatus.CRITICAL,
            experiment_id="integration-test-1",
        )
        
        try:
            # Verify simulation is active
            assert monitor.is_simulation_active() is True
            
            # Create experiment and capture snapshot
            config = ExperimentConfig(target_service="test-service")
            experiment = create_test_experiment(config)
            
            # Note: snapshot creates new monitor instance, so won't see override
            # This tests the snapshot mechanism itself
            snapshot = experiment.capture_comprehensive_snapshot()
            assert "pool" in snapshot
            
        finally:
            # Clean up
            monitor.clear_simulation_override()

    def test_recovery_monitoring_lifecycle(self):
        """Test full recovery monitoring lifecycle."""
        from selfhealing.services.chaos.base import ExperimentConfig, ExperimentStatus
        
        config = ExperimentConfig(
            target_service="test-service",
            ttl_seconds=60,
            grace_period_seconds=30,
        )
        experiment = create_test_experiment(config)
        
        # Start in PENDING
        assert experiment.status == ExperimentStatus.PENDING
        
        # Transition to RUNNING
        experiment.status = ExperimentStatus.RUNNING
        assert experiment.status == ExperimentStatus.RUNNING
        
        # Transition to RECOVERY_MONITORING
        experiment.transition_to_recovery_monitoring()
        assert experiment.status == ExperimentStatus.RECOVERY_MONITORING
        
        # Complete recovery monitoring
        experiment.complete_recovery_monitoring()
        assert experiment.status == ExperimentStatus.COMPLETED
        assert experiment.completed_at is not None
