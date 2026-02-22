"""
비동기 복구 모니터링 및 시뮬레이션 인터페이스 테스트

Tests for:
- RECOVERY_MONITORING 상태 전환, Hard TTL 체크, 스냅샷 메서드
- set_simulation_override() 인터페이스 (PoolMonitor, ConnectionHealthMonitor)
- PoolExhaustionExperiment, ConnectionPartitionExperiment 실험 타입
"""

from datetime import timedelta

import pytest

# =============================================================================
# Phase 5-1: 비동기 복구 모니터링 Tests (§15, §22.2.3)
# =============================================================================

class TestRecoveryMonitoringMethods:
    """Test RECOVERY_MONITORING related methods."""

    def test_recovery_monitoring_status_exists(self):
        """Test RECOVERY_MONITORING status is defined."""
        from selfhealing.services.chaos.base import ExperimentStatus

        assert hasattr(ExperimentStatus, "RECOVERY_MONITORING")
        assert ExperimentStatus.RECOVERY_MONITORING.value == "recovery_monitoring"

    def test_is_hard_ttl_expired_method_exists(self):
        """Test is_hard_ttl_expired method exists on ChaosExperiment."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        assert hasattr(experiment, "is_hard_ttl_expired")
        assert callable(experiment.is_hard_ttl_expired)

    def test_is_hard_ttl_expired_when_not_started(self):
        """Test is_hard_ttl_expired returns False when experiment not started."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        # _expires_at is None when not started
        assert experiment.is_hard_ttl_expired() is False

    def test_is_hard_ttl_expired_within_grace_period(self):
        """Test is_hard_ttl_expired returns False within grace period."""
        from selfhealing.core.timezone import now
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(
            target_service="test",
            ttl_seconds=60,
            grace_period_seconds=300,
        )
        experiment = LatencyInjectionExperiment(config=config)

        # Simulate Soft TTL expired but within grace period
        experiment._expires_at = now() - timedelta(seconds=30)  # 30초 전 만료

        # Hard TTL = expires_at + grace_period = 30초 전 + 300초 = 270초 후
        assert experiment.is_hard_ttl_expired() is False

    def test_is_hard_ttl_expired_after_grace_period(self):
        """Test is_hard_ttl_expired returns True after grace period."""
        from selfhealing.core.timezone import now
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(
            target_service="test",
            ttl_seconds=60,
            grace_period_seconds=300,
        )
        experiment = LatencyInjectionExperiment(config=config)

        # Simulate both Soft TTL and Grace Period expired
        experiment._expires_at = now() - timedelta(seconds=400)  # 400초 전 만료

        # Hard TTL = expires_at + grace_period = 400초 전 + 300초 = 100초 전
        assert experiment.is_hard_ttl_expired() is True

    def test_complete_recovery_monitoring_method_exists(self):
        """Test complete_recovery_monitoring method exists."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        assert hasattr(experiment, "complete_recovery_monitoring")
        assert callable(experiment.complete_recovery_monitoring)

    def test_complete_recovery_monitoring_from_valid_state(self):
        """Test complete_recovery_monitoring from RECOVERY_MONITORING state."""
        from selfhealing.services.chaos.base import ExperimentStatus
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        # Set to RECOVERY_MONITORING
        experiment.status = ExperimentStatus.RECOVERY_MONITORING

        # Complete
        experiment.complete_recovery_monitoring()

        assert experiment.status == ExperimentStatus.COMPLETED
        assert experiment.completed_at is not None

    def test_complete_recovery_monitoring_from_invalid_state(self):
        """Test complete_recovery_monitoring from non-RECOVERY_MONITORING state does nothing."""
        from selfhealing.services.chaos.base import ExperimentStatus
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        # Set to RUNNING (not RECOVERY_MONITORING)
        experiment.status = ExperimentStatus.RUNNING

        # Attempt to complete (should log warning and not change status)
        experiment.complete_recovery_monitoring()

        # Status should remain RUNNING
        assert experiment.status == ExperimentStatus.RUNNING

    def test_force_complete_method_exists(self):
        """Test force_complete method exists."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        assert hasattr(experiment, "force_complete")
        assert callable(experiment.force_complete)

    def test_force_complete_changes_status(self):
        """Test force_complete changes status to COMPLETED."""
        from selfhealing.services.chaos.base import ExperimentStatus
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        experiment.status = ExperimentStatus.RUNNING
        experiment.force_complete(reason="hard_ttl_expired")

        assert experiment.status == ExperimentStatus.COMPLETED
        assert experiment.completed_at is not None

    def test_transition_to_recovery_monitoring_method_exists(self):
        """Test transition_to_recovery_monitoring method exists."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        assert hasattr(experiment, "transition_to_recovery_monitoring")
        assert callable(experiment.transition_to_recovery_monitoring)


# =============================================================================
# Phase 5-1: 스냅샷 메서드 Tests (§13, §22.2.4)
# =============================================================================

class TestSnapshotMethods:
    """Test snapshot methods for monitors."""

    def test_get_pool_state_snapshot_method_exists(self):
        """Test _get_pool_state_snapshot method exists."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        assert hasattr(experiment, "_get_pool_state_snapshot")
        assert callable(experiment._get_pool_state_snapshot)

    def test_get_pool_state_snapshot_returns_dict(self):
        """Test _get_pool_state_snapshot returns a dictionary."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        result = experiment._get_pool_state_snapshot()

        assert isinstance(result, dict)

    def test_get_cert_state_snapshot_method_exists(self):
        """Test _get_cert_state_snapshot method exists."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        assert hasattr(experiment, "_get_cert_state_snapshot")
        assert callable(experiment._get_cert_state_snapshot)

    def test_get_connection_health_snapshot_method_exists(self):
        """Test _get_connection_health_snapshot method exists."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        assert hasattr(experiment, "_get_connection_health_snapshot")
        assert callable(experiment._get_connection_health_snapshot)

    def test_get_connection_health_snapshot_returns_dict(self):
        """Test _get_connection_health_snapshot returns a dictionary."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            LatencyInjectionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = LatencyInjectionExperiment(config=config)

        result = experiment._get_connection_health_snapshot()

        assert isinstance(result, dict)


# =============================================================================
# Phase 5-2: PoolMonitor Simulation Override Tests (§16.2.1, §22.2.2)
# =============================================================================

class TestPoolMonitorSimulationOverride:
    """Test ConnectionPoolMonitor simulation override."""

    def test_set_simulation_override_method_exists(self):
        """Test set_simulation_override method exists."""
        from selfhealing.core.pool_monitor import ConnectionPoolMonitor

        monitor = ConnectionPoolMonitor()

        assert hasattr(monitor, "set_simulation_override")
        assert callable(monitor.set_simulation_override)

    def test_clear_simulation_override_method_exists(self):
        """Test clear_simulation_override method exists."""
        from selfhealing.core.pool_monitor import ConnectionPoolMonitor

        monitor = ConnectionPoolMonitor()

        assert hasattr(monitor, "clear_simulation_override")
        assert callable(monitor.clear_simulation_override)

    def test_is_simulation_active_method_exists(self):
        """Test is_simulation_active method exists."""
        from selfhealing.core.pool_monitor import ConnectionPoolMonitor

        monitor = ConnectionPoolMonitor()

        assert hasattr(monitor, "is_simulation_active")
        assert callable(monitor.is_simulation_active)

    def test_simulation_override_changes_check_health_result(self):
        """Test simulation override changes check_health result."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )

        monitor = ConnectionPoolMonitor()

        # Set simulation override to EXHAUSTED
        monitor.set_simulation_override(
            health_status=PoolHealthStatus.EXHAUSTED,
            experiment_id="exp-123",
        )

        assert monitor.is_simulation_active() is True

        # check_health should return simulated status
        status, stats = monitor.check_health()

        assert status == PoolHealthStatus.EXHAUSTED
        assert stats.available_connections == 0

    def test_clear_simulation_override_restores_normal(self):
        """Test clearing simulation override restores normal behavior."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )

        monitor = ConnectionPoolMonitor()

        # Set and then clear
        monitor.set_simulation_override(
            health_status=PoolHealthStatus.EXHAUSTED,
            experiment_id="exp-123",
        )
        monitor.clear_simulation_override()

        assert monitor.is_simulation_active() is False

    def test_get_simulation_experiment_id(self):
        """Test get_simulation_experiment_id returns correct ID."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )

        monitor = ConnectionPoolMonitor()

        monitor.set_simulation_override(
            health_status=PoolHealthStatus.WARNING,
            experiment_id="exp-456",
        )

        assert monitor.get_simulation_experiment_id() == "exp-456"

    def test_simulation_with_different_statuses(self):
        """Test simulation works with different statuses."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )

        monitor = ConnectionPoolMonitor()

        for status in [
            PoolHealthStatus.HEALTHY,
            PoolHealthStatus.WARNING,
            PoolHealthStatus.CRITICAL,
            PoolHealthStatus.EXHAUSTED,
        ]:
            monitor.set_simulation_override(health_status=status)
            result_status, _ = monitor.check_health()
            assert result_status == status


# =============================================================================
# Phase 5-2: ConnectionHealthMonitor Simulation Override Tests (§16.2.2)
# =============================================================================

class TestConnectionHealthMonitorSimulationOverride:
    """Test DefaultConnectionHealthMonitor simulation override."""

    def test_set_simulation_override_method_exists(self):
        """Test set_simulation_override method exists."""
        from selfhealing.core.connection_health import DefaultConnectionHealthMonitor

        monitor = DefaultConnectionHealthMonitor()

        assert hasattr(monitor, "set_simulation_override")
        assert callable(monitor.set_simulation_override)

    def test_set_partition_simulation_method_exists(self):
        """Test set_partition_simulation method exists."""
        from selfhealing.core.connection_health import DefaultConnectionHealthMonitor

        monitor = DefaultConnectionHealthMonitor()

        assert hasattr(monitor, "set_partition_simulation")
        assert callable(monitor.set_partition_simulation)

    def test_clear_all_simulation_overrides_method_exists(self):
        """Test clear_all_simulation_overrides method exists."""
        from selfhealing.core.connection_health import DefaultConnectionHealthMonitor

        monitor = DefaultConnectionHealthMonitor()

        assert hasattr(monitor, "clear_all_simulation_overrides")
        assert callable(monitor.clear_all_simulation_overrides)

    def test_is_simulation_active_method_exists(self):
        """Test is_simulation_active method exists."""
        from selfhealing.core.connection_health import DefaultConnectionHealthMonitor

        monitor = DefaultConnectionHealthMonitor()

        assert hasattr(monitor, "is_simulation_active")
        assert callable(monitor.is_simulation_active)

    def test_connection_override_changes_check_health(self):
        """Test connection override changes check_health result."""
        from selfhealing.core.connection_health import (
            ConnectionStatus,
            ConnectionType,
            DefaultConnectionHealthMonitor,
        )

        monitor = DefaultConnectionHealthMonitor()

        # Set simulation override for database
        monitor.set_simulation_override(
            connection_type=ConnectionType.DATABASE,
            name="primary",
            status=ConnectionStatus.UNHEALTHY,
            experiment_id="exp-123",
        )

        assert monitor.is_simulation_active() is True

        # check_health should return simulated status
        health = monitor.check_health(ConnectionType.DATABASE, "primary")

        assert health.status == ConnectionStatus.UNHEALTHY

    def test_partition_simulation_changes_get_partition_state(self):
        """Test partition simulation changes get_partition_state result."""
        from selfhealing.core.connection_health import (
            DefaultConnectionHealthMonitor,
            PartitionState,
        )

        monitor = DefaultConnectionHealthMonitor()

        # Set partition simulation (partial partition: DB down, cache up)
        simulated_partition = PartitionState(
            db_available=False,
            cache_available=True,
        )

        monitor.set_partition_simulation(
            partition_state=simulated_partition,
            experiment_id="exp-123",
        )

        # get_partition_state should return simulated state
        partition = monitor.get_partition_state()

        assert partition.db_available is False
        assert partition.cache_available is True
        assert partition.is_partial_partition is True

    def test_clear_all_simulation_overrides(self):
        """Test clearing all simulation overrides."""
        from selfhealing.core.connection_health import (
            ConnectionStatus,
            ConnectionType,
            DefaultConnectionHealthMonitor,
            PartitionState,
        )

        monitor = DefaultConnectionHealthMonitor()

        # Set both overrides
        monitor.set_simulation_override(
            ConnectionType.DATABASE, "primary", ConnectionStatus.UNHEALTHY
        )
        monitor.set_partition_simulation(
            PartitionState(db_available=False, cache_available=True)
        )

        assert monitor.is_simulation_active() is True

        # Clear all
        monitor.clear_all_simulation_overrides()

        assert monitor.is_simulation_active() is False


# =============================================================================
# Phase 5-2: PoolExhaustionExperiment Tests (§16.3, §22.2.2)
# =============================================================================

class TestPoolExhaustionExperiment:
    """Test PoolExhaustionExperiment simulation experiment."""

    def test_experiment_type_exists(self):
        """Test POOL_EXHAUSTION experiment type is defined."""
        from selfhealing.services.chaos.base import ExperimentType

        assert hasattr(ExperimentType, "POOL_EXHAUSTION")
        assert ExperimentType.POOL_EXHAUSTION.value == "pool_exhaustion"

    def test_experiment_class_exists(self):
        """Test PoolExhaustionExperiment class exists."""
        from selfhealing.services.chaos.experiments import PoolExhaustionExperiment

        assert PoolExhaustionExperiment is not None

    def test_experiment_has_failure_hypothesis(self):
        """Test PoolExhaustionExperiment has failure_hypothesis."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            PoolExhaustionExperiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = PoolExhaustionExperiment(config=config)

        assert hasattr(experiment, "failure_hypothesis")
        assert experiment.failure_hypothesis is not None

    def test_experiment_requires_approval(self):
        """Test PoolExhaustionExperiment requires approval."""
        from selfhealing.services.chaos.experiments import PoolExhaustionExperiment

        assert PoolExhaustionExperiment.requires_approval is True

    def test_experiment_inject_chaos(self):
        """Test inject_chaos sets simulation override."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            PoolExhaustionExperiment,
        )

        config = ExperimentConfig(
            target_service="payment",
            parameters={"simulated_status": "exhausted"},
        )
        experiment = PoolExhaustionExperiment(config=config)
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is True
        assert experiment._monitor_instance is not None
        assert experiment._monitor_instance.is_simulation_active() is True

    def test_experiment_rollback(self):
        """Test rollback clears simulation override."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            PoolExhaustionExperiment,
        )

        config = ExperimentConfig(
            target_service="payment",
            parameters={"simulated_status": "exhausted"},
        )
        experiment = PoolExhaustionExperiment(config=config)
        experiment._calculate_expires_at()

        # Inject and rollback
        experiment.inject_chaos()
        experiment.rollback()

        assert experiment._rollback_completed is True

    def test_create_experiment_factory(self):
        """Test create_experiment factory supports pool_exhaustion."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            PoolExhaustionExperiment,
            create_experiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = create_experiment("pool_exhaustion", config=config)

        assert isinstance(experiment, PoolExhaustionExperiment)


# =============================================================================
# Phase 5-2: ConnectionPartitionExperiment Tests (§16.2.2)
# =============================================================================

class TestConnectionPartitionExperiment:
    """Test ConnectionPartitionExperiment simulation experiment."""

    def test_experiment_type_exists(self):
        """Test CONNECTION_PARTITION experiment type is defined."""
        from selfhealing.services.chaos.base import ExperimentType

        assert hasattr(ExperimentType, "CONNECTION_PARTITION")
        assert ExperimentType.CONNECTION_PARTITION.value == "connection_partition"

    def test_experiment_class_exists(self):
        """Test ConnectionPartitionExperiment class exists."""
        from selfhealing.services.chaos.experiments import ConnectionPartitionExperiment

        assert ConnectionPartitionExperiment is not None

    def test_experiment_has_failure_hypothesis(self):
        """Test ConnectionPartitionExperiment has failure_hypothesis."""
        from selfhealing.services.chaos.experiments import (
            ConnectionPartitionExperiment,
            ExperimentConfig,
        )

        config = ExperimentConfig(target_service="test")
        experiment = ConnectionPartitionExperiment(config=config)

        assert hasattr(experiment, "failure_hypothesis")
        assert experiment.failure_hypothesis is not None

    def test_experiment_requires_approval(self):
        """Test ConnectionPartitionExperiment requires approval."""
        from selfhealing.services.chaos.experiments import ConnectionPartitionExperiment

        assert ConnectionPartitionExperiment.requires_approval is True

    def test_experiment_inject_chaos(self):
        """Test inject_chaos sets partition simulation."""
        from selfhealing.services.chaos.experiments import (
            ConnectionPartitionExperiment,
            ExperimentConfig,
        )

        config = ExperimentConfig(
            target_service="payment",
            parameters={
                "partition_type": "partial",
                "db_available": False,
                "cache_available": True,
            },
        )
        experiment = ConnectionPartitionExperiment(config=config)
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is True
        assert experiment._monitor_instance is not None
        assert experiment._monitor_instance.is_simulation_active() is True

    def test_experiment_rollback(self):
        """Test rollback clears partition simulation."""
        from selfhealing.services.chaos.experiments import (
            ConnectionPartitionExperiment,
            ExperimentConfig,
        )

        config = ExperimentConfig(
            target_service="payment",
            parameters={
                "partition_type": "partial",
                "db_available": False,
                "cache_available": True,
            },
        )
        experiment = ConnectionPartitionExperiment(config=config)
        experiment._calculate_expires_at()

        # Inject and rollback
        experiment.inject_chaos()
        experiment.rollback()

        assert experiment._rollback_completed is True

    def test_create_experiment_factory(self):
        """Test create_experiment factory supports connection_partition."""
        from selfhealing.services.chaos.experiments import (
            ConnectionPartitionExperiment,
            ExperimentConfig,
            create_experiment,
        )

        config = ExperimentConfig(target_service="test")
        experiment = create_experiment("connection_partition", config=config)

        assert isinstance(experiment, ConnectionPartitionExperiment)


# =============================================================================
# PoolExhaustionExperiment 선택 이유 설명 테스트
# =============================================================================

class TestPoolExhaustionExperimentDesign:
    """
    PoolExhaustionExperiment가 "선택" 구현으로 표시된 이유:
    
    문서 (32_CHAOS_SYSTEM_INTEGRATION.md §22.2.2)에서 "선택"으로 표시된 이유:
    
    1. **의존성**: PoolExhaustionExperiment는 set_simulation_override() 인터페이스가
       먼저 구현되어야 동작함. Phase 5-2에서 시뮬레이션 인터페이스를 먼저 구현해야 함.
    
    2. **단계적 구현**: 
       - Phase 5-2 필수: set_simulation_override() 구현
       - Phase 5-2 선택: PoolExhaustionExperiment (시뮬레이션 인터페이스 활용)
    
    3. **실제 필요성**:
       - 실제 Pool 고갈은 ResourceExhaustionExperiment로도 테스트 가능
       - 시뮬레이션은 "실제 인프라 변경 없이" 알림/복구 체인 테스트가 목적
       - 따라서 시뮬레이션 인터페이스 자체가 핵심이고, 실험 타입은 부가적
    
    이 테스트 클래스는 위 설계 결정을 검증함.
    """

    def test_simulation_interface_is_independent(self):
        """Test simulation interface can be used without experiment class."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )

        # 시뮬레이션 인터페이스는 실험 클래스 없이도 직접 사용 가능
        monitor = ConnectionPoolMonitor()
        monitor.set_simulation_override(
            health_status=PoolHealthStatus.EXHAUSTED,
            experiment_id="manual-test-001",
        )

        status, stats = monitor.check_health()

        assert status == PoolHealthStatus.EXHAUSTED

        # 정리
        monitor.clear_simulation_override()

    def test_experiment_uses_simulation_interface(self):
        """Test PoolExhaustionExperiment uses the simulation interface."""
        from selfhealing.services.chaos.experiments import (
            ExperimentConfig,
            PoolExhaustionExperiment,
        )

        config = ExperimentConfig(
            target_service="test",
            parameters={"simulated_status": "critical"},
        )
        experiment = PoolExhaustionExperiment(config=config)
        experiment._calculate_expires_at()

        experiment.inject_chaos()

        # 실험이 내부적으로 시뮬레이션 인터페이스를 사용함을 확인
        assert experiment._monitor_instance is not None
        assert experiment._monitor_instance.is_simulation_active() is True

        # 정리
        experiment.rollback()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
