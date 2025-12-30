"""
Chaos Scheduler Unit Tests

Comprehensive tests for the Autonomous Chaos Engine.

Test Categories:
1. SafetyGuard - Pre-flight checks, error budget validation
2. BlastRadiusManager - Scope control, approval workflow
3. ChaosSchedulerService - Scheduling, execution, kill switch
4. Experiments - Core experiment types
5. Reports - Daily resilience reporting

Reference: docs/self_healing/CHAOS_ENGINEERING.md
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Dict, Any


# =============================================================================
# SafetyGuard Tests
# =============================================================================


class TestSafetyGuard:
    """Tests for SafetyGuard pre-flight checks."""
    
    def test_safety_guard_initialization(self):
        """Test SafetyGuard can be instantiated."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard, SafetyConfig
        
        config = SafetyConfig(error_budget_min_percent=20.0)
        guard = SafetyGuard(config=config)
        
        assert guard is not None
        assert guard._config.error_budget_min_percent == 20.0
    
    def test_safety_guard_get_config(self):
        """Test configuration retrieval."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard, SafetyConfig
        
        config = SafetyConfig(
            error_budget_min_percent=25.0,
            experiment_cooldown_minutes=60,
        )
        guard = SafetyGuard(config=config)
        
        retrieved = guard.get_config()
        assert retrieved.error_budget_min_percent == 25.0
        assert retrieved.experiment_cooldown_minutes == 60
    
    def test_safety_guard_update_config(self):
        """Test configuration update."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard, SafetyConfig
        
        guard = SafetyGuard(config=SafetyConfig())
        
        updated = guard.update_config(
            error_budget_min_percent=30.0,
            experiment_cooldown_minutes=45,
        )
        
        assert updated.error_budget_min_percent == 30.0
        assert updated.experiment_cooldown_minutes == 45
    
    def test_safety_check_passes_with_healthy_budget(self):
        """Test that safety check passes when error budget is healthy."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyConfig, SafetyStatus
        )
        
        guard = SafetyGuard(config=SafetyConfig(error_budget_min_percent=20.0))
        
        # Mock error budget service to return healthy budget
        with patch('selfhealing.services.chaos.safety_guard.get_safety_guard') as mock_guard:
            mock_guard.return_value = guard
            
            with patch.object(guard, '_check_error_budget') as mock_budget:
                mock_budget.return_value = {
                    "remaining_percent": 80.0,
                    "consumed_percent": 20.0,
                    "is_healthy": True,
                }
                
                with patch.object(guard, '_check_kill_switch', return_value=False):
                    with patch.object(guard, '_check_system_health', return_value={"healthy": True}):
                        with patch.object(guard, '_check_active_incidents', return_value={"count": 0}):
                            with patch.object(guard, '_check_deployment_freeze', return_value={"active": False}):
                                with patch.object(guard, '_check_cooldown', return_value={"in_cooldown": False}):
                                    result = guard.check(
                                        experiment_id="test-exp-001",
                                        target_service="payment",
                                    )
        
                                    assert result.allowed is True
                                    assert result.status == SafetyStatus.SAFE.value
    
    def test_safety_check_blocks_low_budget(self):
        """Test that safety check blocks when error budget is low."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyConfig, SafetyStatus, BlockReason
        )
        
        guard = SafetyGuard(config=SafetyConfig(error_budget_min_percent=20.0))
        
        with patch.object(guard, '_check_error_budget') as mock_budget:
            mock_budget.return_value = {
                "remaining_percent": 15.0,  # Below 20% threshold
                "consumed_percent": 85.0,
                "is_healthy": False,
            }
            
            result = guard.check(
                experiment_id="test-exp-002",
                target_service="payment",
            )
            
            assert result.allowed is False
            assert result.status == SafetyStatus.BLOCKED.value
            assert result.block_reason == BlockReason.LOW_ERROR_BUDGET.value
    
    def test_safety_check_blocks_on_kill_switch(self):
        """Test that safety check blocks when kill switch is active."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyConfig, SafetyStatus, BlockReason
        )
        
        guard = SafetyGuard(config=SafetyConfig())
        
        with patch.object(guard, '_check_error_budget') as mock_budget:
            mock_budget.return_value = {"remaining_percent": 80.0, "is_healthy": True}
            
            with patch.object(guard, '_check_kill_switch', return_value=True):
                result = guard.check(
                    experiment_id="test-exp-003",
                    target_service="payment",
                )
                
                assert result.allowed is False
                assert result.block_reason == BlockReason.KILL_SWITCH_ACTIVE.value


# =============================================================================
# BlastRadiusManager Tests
# =============================================================================


class TestBlastRadiusManager:
    """Tests for BlastRadiusManager scope control."""
    
    def test_blast_radius_manager_initialization(self):
        """Test BlastRadiusManager can be instantiated."""
        from selfhealing.services.chaos.blast_radius import (
            BlastRadiusManager, BlastRadiusPolicy
        )
        
        policy = BlastRadiusPolicy()
        manager = BlastRadiusManager(policy=policy)
        
        assert manager is not None
    
    def test_instance_level_auto_approval(self):
        """Test that INSTANCE level experiments are auto-approved."""
        from selfhealing.services.chaos.blast_radius import (
            BlastRadiusManager, BlastRadiusPolicy, BlastRadius, ApprovalStatus
        )
        
        # allow_outside_window=True ensures test is not time-dependent
        policy = BlastRadiusPolicy(instance_auto_approve=True, allow_outside_window=True)
        manager = BlastRadiusManager(policy=policy)
        
        result = manager.check(
            blast_radius=BlastRadius.INSTANCE,
            target_service="payment",
        )
        
        assert result.allowed is True
        assert result.approval_status == ApprovalStatus.NOT_REQUIRED.value
    
    def test_region_level_requires_approval(self):
        """Test that REGION level experiments require manual approval."""
        from selfhealing.services.chaos.blast_radius import (
            BlastRadiusManager, BlastRadiusPolicy, BlastRadius, ApprovalStatus
        )
        
        policy = BlastRadiusPolicy(region_auto_approve=False)
        manager = BlastRadiusManager(policy=policy)
        
        result = manager.check(
            blast_radius=BlastRadius.REGION,
            target_service="payment",
        )
        
        assert result.requires_approval is True
        assert result.approval_status == ApprovalStatus.PENDING.value
    
    def test_excluded_services_blocked(self):
        """Test that excluded services are blocked."""
        from selfhealing.services.chaos.blast_radius import (
            BlastRadiusManager, BlastRadiusPolicy, BlastRadius
        )
        
        policy = BlastRadiusPolicy(excluded_services=["critical-auth"])
        manager = BlastRadiusManager(policy=policy)
        
        result = manager.check(
            blast_radius=BlastRadius.INSTANCE,
            target_service="critical-auth",
        )
        
        assert result.allowed is False
        assert any("excluded" in v.lower() for v in result.violations)
    
    def test_get_policy(self):
        """Test policy retrieval."""
        from selfhealing.services.chaos.blast_radius import (
            BlastRadiusManager, BlastRadiusPolicy
        )
        
        policy = BlastRadiusPolicy(
            instance_max_concurrent=10,
            service_max_concurrent=3,
        )
        manager = BlastRadiusManager(policy=policy)
        
        retrieved = manager.get_policy()
        assert retrieved.instance_max_concurrent == 10
        assert retrieved.service_max_concurrent == 3
    
    def test_update_policy(self):
        """Test policy update via API."""
        from selfhealing.services.chaos.blast_radius import (
            BlastRadiusManager, BlastRadiusPolicy
        )
        
        manager = BlastRadiusManager(policy=BlastRadiusPolicy())
        
        updated = manager.update_policy(
            instance_max_concurrent=15,
            allow_outside_window=True,
        )
        
        assert updated.instance_max_concurrent == 15
        assert updated.allow_outside_window is True


# =============================================================================
# ChaosSchedulerService Tests
# =============================================================================


class TestChaosSchedulerService:
    """Tests for ChaosSchedulerService."""
    
    def test_scheduler_initialization(self):
        """Test ChaosSchedulerService can be instantiated."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig
        )
        
        config = SchedulerConfig()
        scheduler = ChaosSchedulerService(config=config)
        
        assert scheduler is not None
    
    def test_create_schedule(self):
        """Test creating a scheduled experiment."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig, ScheduleType
        )
        
        scheduler = ChaosSchedulerService(config=SchedulerConfig())
        
        with patch.object(scheduler, '_persist_schedules'):
            schedule = scheduler.create_schedule(
                experiment_type="latency_injection",
                target_service="payment",
                schedule_type=ScheduleType.DAILY.value,
                schedule_time="03:00",
                description="Daily latency test",
            )
        
            assert schedule.id is not None
            assert schedule.experiment_type == "latency_injection"
            assert schedule.target_service == "payment"
            assert schedule.schedule_time == "03:00"
    
    def test_list_schedules(self):
        """Test listing scheduled experiments."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig, ScheduleType
        )
        
        scheduler = ChaosSchedulerService(config=SchedulerConfig())
        
        with patch.object(scheduler, '_persist_schedules'):
            scheduler.create_schedule(
                experiment_type="latency_injection",
                target_service="payment",
                schedule_type=ScheduleType.DAILY.value,
                schedule_time="02:00",
            )
            scheduler.create_schedule(
                experiment_type="error_5xx",
                target_service="order",
                schedule_type=ScheduleType.WEEKLY.value,
                schedule_time="04:00",
            )
        
        schedules = scheduler.list_schedules()
        assert len(schedules) >= 2
    
    def test_get_schedule(self):
        """Test getting a specific schedule."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig, ScheduleType
        )
        
        scheduler = ChaosSchedulerService(config=SchedulerConfig())
        
        with patch.object(scheduler, '_persist_schedules'):
            created = scheduler.create_schedule(
                experiment_type="timeout",
                target_service="inventory",
                schedule_type=ScheduleType.ONCE.value,
                schedule_time="05:00",
            )
        
            retrieved = scheduler.get_schedule(created.id)
            assert retrieved is not None
            assert retrieved.id == created.id
            assert retrieved.experiment_type == "timeout"
    
    def test_delete_schedule(self):
        """Test deleting a schedule."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig, ScheduleType
        )
        
        scheduler = ChaosSchedulerService(config=SchedulerConfig())
        
        with patch.object(scheduler, '_persist_schedules'):
            created = scheduler.create_schedule(
                experiment_type="packet_loss",
                target_service="messaging",
                schedule_type=ScheduleType.DAILY.value,
                schedule_time="01:00",
            )
            
            success = scheduler.delete_schedule(created.id)
            assert success is True
            
            retrieved = scheduler.get_schedule(created.id)
            assert retrieved is None
    
    def test_approve_schedule(self):
        """Test approving a pending schedule."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig, ScheduleType,
            ExperimentApprovalStatus
        )
        
        scheduler = ChaosSchedulerService(config=SchedulerConfig())
        
        with patch.object(scheduler, '_persist_schedules'):
            # Create a schedule that requires approval (REGION level)
            created = scheduler.create_schedule(
                experiment_type="resource_exhaustion",
                target_service="database",
                blast_radius="region",  # Requires approval
                schedule_type=ScheduleType.ONCE.value,
                schedule_time="03:00",
            )
            
            # Approve it
            result = scheduler.approve_schedule(
                schedule_id=created.id,
                approved_by="admin@example.com",
            )
            
            assert result.approval_status == ExperimentApprovalStatus.APPROVED.value
    
    def test_deny_schedule(self):
        """Test denying a pending schedule."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig, ScheduleType,
            ExperimentApprovalStatus
        )
        
        scheduler = ChaosSchedulerService(config=SchedulerConfig())
        
        with patch.object(scheduler, '_persist_schedules'):
            created = scheduler.create_schedule(
                experiment_type="resource_exhaustion",
                target_service="database",
                blast_radius="region",
                schedule_type=ScheduleType.ONCE.value,
                schedule_time="03:00",
            )
            
            result = scheduler.deny_schedule(
                schedule_id=created.id,
                denied_by="security@example.com",
                reason="Too risky for production",
            )
            
            assert result.approval_status == ExperimentApprovalStatus.DENIED.value
    
    def test_kill_all_experiments(self):
        """Test killing all experiments."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig
        )
        
        scheduler = ChaosSchedulerService(config=SchedulerConfig())
        
        # kill_all returns count of killed experiments
        # With no running experiments, returns 0
        with patch.object(scheduler, '_running_experiments', {}):
            killed_count = scheduler.kill_all(reason="Emergency stop")
            assert killed_count == 0
    
    def test_get_config(self):
        """Test scheduler config retrieval."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig
        )
        
        config = SchedulerConfig(enabled=True)
        scheduler = ChaosSchedulerService(config=config)
        
        retrieved = scheduler.get_config()
        assert retrieved.enabled is True
    
    def test_update_config(self):
        """Test scheduler config update."""
        from selfhealing.services.chaos.scheduler import (
            ChaosSchedulerService, SchedulerConfig
        )
        
        scheduler = ChaosSchedulerService(config=SchedulerConfig())
        
        updated = scheduler.update_config(
            enabled=False,
            max_concurrent_experiments=5,
        )
        
        assert updated.enabled is False
        assert updated.max_concurrent_experiments == 5


# =============================================================================
# Experiment Tests
# =============================================================================


class TestChaosExperiments:
    """Tests for chaos experiment types."""
    
    def test_latency_injection_experiment(self):
        """Test LatencyInjectionExperiment."""
        from selfhealing.services.chaos.experiments import (
            LatencyInjectionExperiment, ExperimentStatus, ExperimentConfig
        )
        
        config = ExperimentConfig(
            target_service="payment",
            parameters={
                "latency_ms": 500,
                "latency_jitter_ms": 100,
            },
            injection_rate=0.1,
        )
        experiment = LatencyInjectionExperiment(config=config)
        
        assert experiment.experiment_type == "latency_injection"
        assert experiment.latency_ms == 500
        assert experiment.status == ExperimentStatus.PENDING.value
    
    def test_error_5xx_experiment(self):
        """Test Error5xxExperiment."""
        from selfhealing.services.chaos.experiments import (
            Error5xxExperiment, ExperimentStatus, ExperimentConfig
        )
        
        config = ExperimentConfig(
            target_service="order",
            parameters={
                "error_code": 503,  # 단수형
            },
            injection_rate=0.05,
        )
        experiment = Error5xxExperiment(config=config)
        
        assert experiment.experiment_type == "error_5xx"
        assert experiment.error_code == 503
    
    def test_packet_loss_experiment(self):
        """Test PacketLossExperiment."""
        from selfhealing.services.chaos.experiments import (
            PacketLossExperiment, ExperimentConfig
        )
        
        config = ExperimentConfig(
            target_service="messaging",
            parameters={
                "loss_rate": 0.02,  # 2% (loss_percent 아님)
            },
        )
        experiment = PacketLossExperiment(config=config)
        
        assert experiment.experiment_type == "packet_loss"
        assert experiment.loss_rate == 0.02
    
    def test_timeout_experiment(self):
        """Test TimeoutExperiment."""
        from selfhealing.services.chaos.experiments import (
            TimeoutExperiment, ExperimentConfig
        )
        
        config = ExperimentConfig(
            target_service="inventory",
            parameters={
                "timeout_delay_seconds": 30,  # timeout_ms 아님
            },
        )
        experiment = TimeoutExperiment(config=config)
        
        assert experiment.experiment_type == "timeout"
        assert experiment.timeout_delay_seconds == 30
    
    def test_resource_exhaustion_experiment(self):
        """Test ResourceExhaustionExperiment."""
        from selfhealing.services.chaos.experiments import (
            ResourceExhaustionExperiment, ExperimentConfig
        )
        
        config = ExperimentConfig(
            target_service="database",
            parameters={
                "resource_type": "cpu",
                "exhaustion_percent": 0.80,  # target_percent 아님, 비율(0~1)
            },
        )
        experiment = ResourceExhaustionExperiment(config=config)
        
        assert experiment.experiment_type == "resource_exhaustion"
        assert experiment.resource_type == "cpu"
        assert experiment.exhaustion_percent == 0.80
    
    def test_experiment_serialization(self):
        """Test experiment has basic attributes for serialization."""
        from selfhealing.services.chaos.experiments import (
            LatencyInjectionExperiment, ExperimentConfig
        )
        
        config = ExperimentConfig(
            target_service="payment",
            parameters={
                "latency_ms": 200,
            },
        )
        experiment = LatencyInjectionExperiment(config=config)
        
        # ChaosExperiment 기본 속성 확인
        assert hasattr(experiment, "experiment_id")
        assert hasattr(experiment, "experiment_type")
        assert experiment.experiment_type == "latency_injection"
        assert hasattr(experiment, "config")
        assert experiment.config.target_service == "payment"


# =============================================================================
# Report Tests
# =============================================================================


class TestResilienceReports:
    """Tests for resilience report generation."""
    
    def test_report_generator_initialization(self):
        """Test ResilienceReportGenerator can be instantiated."""
        from selfhealing.services.chaos.reports import (
            ResilienceReportGenerator, ReportConfig
        )
        
        config = ReportConfig()
        generator = ResilienceReportGenerator(config=config)
        
        assert generator is not None
    
    def test_generate_daily_report(self):
        """Test daily report generation."""
        from selfhealing.services.chaos.reports import (
            ResilienceReportGenerator, ReportConfig, ResilienceGrade
        )
        
        generator = ResilienceReportGenerator(config=ReportConfig())
        
        # Mock experiment data
        with patch.object(generator, '_collect_experiment_results') as mock_exp:
            mock_exp.return_value = []
            
            with patch.object(generator, '_run_forensic_analysis') as mock_forensic:
                mock_forensic.return_value = {}
                
                with patch.object(generator, '_persist_reports'):
                    with patch.object(generator, '_record_metrics'):
                        with patch.object(generator, '_record_audit'):
                            with patch.object(generator, '_send_notifications'):
                                report = generator.generate_daily_report()
                                
                                assert report is not None
                                assert report.report_id is not None
                                assert report.grade is not None
    
    def test_get_reports(self):
        """Test report retrieval."""
        from selfhealing.services.chaos.reports import (
            ResilienceReportGenerator, ReportConfig
        )
        
        generator = ResilienceReportGenerator(config=ReportConfig())
        
        reports = generator.get_reports(days=7)
        
        assert isinstance(reports, list)
    
    def test_get_grade_history(self):
        """Test grade history retrieval."""
        from selfhealing.services.chaos.reports import (
            ResilienceReportGenerator, ReportConfig
        )
        
        generator = ResilienceReportGenerator(config=ReportConfig())
        
        history = generator.get_grade_history(days=30)
        
        assert isinstance(history, list)
    
    def test_resilience_grade_calculation(self):
        """Test resilience grade calculation logic."""
        from selfhealing.services.chaos.reports import ResilienceGrade
        
        # Grade A: 100% pass rate
        # Grade B: 90%+ pass rate
        # Grade C: 70%+ pass rate
        # Grade D: 50%+ pass rate
        # Grade F: Below 50%
        
        assert ResilienceGrade.A.value == "A"
        assert ResilienceGrade.F.value == "F"


# =============================================================================
# Celery Task Tests
# =============================================================================


class TestChaosSchedulerTasks:
    """Tests for Celery scheduler tasks."""
    
    def test_run_scheduled_experiments_no_due(self):
        """Test run_scheduled_experiments with no due experiments."""
        from selfhealing.tasks.chaos_scheduler import run_scheduled_experiments
        
        with patch('selfhealing.services.execution_services.get_chaos_execution_service') as mock_svc:
            mock_service = MagicMock()
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"checked": 0, "executed": 0, "blocked": 0}
            mock_service.run_scheduled_experiments.return_value = mock_result
            mock_svc.return_value = mock_service
            
            result = run_scheduled_experiments()
            
            assert result["checked"] == 0
            assert result["executed"] == 0
    
    def test_run_scheduled_experiments_with_blocked(self):
        """Test that blocked experiments are reported."""
        from selfhealing.tasks.chaos_scheduler import run_scheduled_experiments
        
        with patch('selfhealing.services.execution_services.get_chaos_execution_service') as mock_svc:
            mock_service = MagicMock()
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"checked": 1, "executed": 0, "blocked": 1}
            mock_service.run_scheduled_experiments.return_value = mock_result
            mock_svc.return_value = mock_service
            
            result = run_scheduled_experiments()
            
            assert result["checked"] == 1
            assert result["blocked"] == 1
    
    def test_generate_daily_resilience_report_task(self):
        """Test daily report generation task."""
        from selfhealing.tasks.chaos_scheduler import generate_daily_resilience_report
        
        with patch('selfhealing.services.execution_services.get_chaos_execution_service') as mock_svc:
            mock_service = MagicMock()
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {
                "success": True,
                "report_id": "report-123",
                "grade": "A",
                "total_experiments": 10,
                "passed_count": 10,
                "failed_count": 0,
            }
            mock_service.generate_daily_report.return_value = mock_result
            mock_svc.return_value = mock_service
            
            result = generate_daily_resilience_report()
            
            assert result["success"] is True
            assert result["report_id"] == "report-123"
            assert result["grade"] == "A"
    
    def test_cleanup_expired_approvals_task(self):
        """Test approval cleanup task."""
        from selfhealing.tasks.chaos_scheduler import cleanup_expired_approvals
        
        with patch('selfhealing.services.execution_services.get_chaos_execution_service') as mock_svc:
            mock_service = MagicMock()
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {
                "schedule_expired": 2,
                "blast_radius_expired": 1,
            }
            mock_service.cleanup_expired_approvals.return_value = mock_result
            mock_svc.return_value = mock_service
            
            result = cleanup_expired_approvals()
            
            assert result["schedule_expired"] == 2
            assert result["blast_radius_expired"] == 1


# =============================================================================
# Integration Tests
# =============================================================================


class TestChaosEngineSingleton:
    """Tests for singleton pattern in chaos services."""
    
    def test_safety_guard_singleton(self):
        """Test SafetyGuard singleton."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        
        guard1 = get_safety_guard()
        guard2 = get_safety_guard()
        
        assert guard1 is guard2
    
    def test_blast_radius_manager_singleton(self):
        """Test BlastRadiusManager singleton."""
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
        
        manager1 = get_blast_radius_manager()
        manager2 = get_blast_radius_manager()
        
        assert manager1 is manager2
    
    def test_chaos_scheduler_singleton(self):
        """Test ChaosSchedulerService singleton."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler1 = get_chaos_scheduler()
        scheduler2 = get_chaos_scheduler()
        
        assert scheduler1 is scheduler2
    
    def test_report_generator_singleton(self):
        """Test ResilienceReportGenerator singleton."""
        from selfhealing.services.chaos.reports import get_report_generator
        
        generator1 = get_report_generator()
        generator2 = get_report_generator()
        
        assert generator1 is generator2
