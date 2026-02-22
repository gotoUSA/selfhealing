"""
Test Zombie Hunter and Safety Mechanisms

Tests for:
1. hunt_zombie_experiments Celery task
2. Monotonic TTL methods in ChaosExperiment
3. Celery Beat schedule

Reference: 34_CHAOS_SAFETY_MECHANISMS.md §5
"""

from unittest.mock import Mock, patch
from datetime import timedelta


# =============================================================================
# Test: hunt_zombie_experiments Celery Task
# =============================================================================


class TestHuntZombieExperimentsTask:
    """Test hunt_zombie_experiments Celery task."""
    
    def test_celery_task_adapter_exists(self):
        """Test hunt_zombie_experiments Celery task adapter exists."""
        from selfhealing.celery_tasks import hunt_zombie_experiments
        
        assert callable(hunt_zombie_experiments)
    
    def test_celery_task_name(self):
        """Test hunt_zombie_experiments Celery task has correct name."""
        from selfhealing.celery_tasks import hunt_zombie_experiments
        
        assert hunt_zombie_experiments.name == "selfhealing.celery_tasks.hunt_zombie_experiments"
    
    def test_business_logic_function_exists(self):
        """Test hunt_zombie_experiments business logic function exists in selfhealing.tasks."""
        from selfhealing.tasks.chaos_scheduler import hunt_zombie_experiments
        
        assert callable(hunt_zombie_experiments)
    
    def test_task_with_no_running_experiments(self):
        """Test task with no RUNNING experiments."""
        from selfhealing.tasks.chaos_scheduler import hunt_zombie_experiments
        
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler:
            mock_scheduler = Mock()
            mock_scheduler.get_experiments_by_status.return_value = []
            mock_get_scheduler.return_value = mock_scheduler
            
            result = hunt_zombie_experiments()
            
            assert result["success"] is True
            assert result["hunted"] == 0
            assert result["skipped"] == 0
            assert result["errors"] == []
    
    def test_task_with_non_expired_experiment(self):
        """Test task skips non-expired experiments."""
        from selfhealing.tasks.chaos_scheduler import hunt_zombie_experiments
        
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler:
            mock_exp = Mock()
            mock_exp.experiment_id = "exp-123"
            mock_exp.is_expired.return_value = False
            mock_exp._is_expired_monotonic.return_value = False
            
            mock_scheduler = Mock()
            mock_scheduler.get_experiments_by_status.return_value = [mock_exp]
            mock_get_scheduler.return_value = mock_scheduler
            
            result = hunt_zombie_experiments()
            
            assert result["success"] is True
            assert result["hunted"] == 0
            # Non-expired experiments are skipped (not counted as skipped due to lock)
    
    def test_task_hunts_expired_experiment(self):
        """Test task hunts expired experiments."""
        from selfhealing.tasks.chaos_scheduler import hunt_zombie_experiments
        from selfhealing.services.chaos.base import ExperimentStatus
        
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler, \
             patch("selfhealing.services.idempotency_service.IdempotencyService") as mock_idem_class:
            
            mock_exp = Mock()
            mock_exp.experiment_id = "exp-zombie"
            mock_exp._is_expired_monotonic.return_value = True
            mock_exp.status = ExperimentStatus.RUNNING
            
            mock_scheduler = Mock()
            mock_scheduler.get_experiments_by_status.return_value = [mock_exp]
            mock_get_scheduler.return_value = mock_scheduler
            
            mock_idempotency = Mock()
            mock_idempotency.acquire_lock.return_value = True
            mock_idem_class.return_value = mock_idempotency
            
            result = hunt_zombie_experiments()
            
            assert result["success"] is True
            assert result["hunted"] == 1
            
            # Verify rollback was called
            mock_exp.rollback.assert_called_once()
            
            # Verify status changed to ABORTED
            assert mock_exp.status == ExperimentStatus.ABORTED
            
            # Verify unregistered from scheduler
            mock_scheduler.unregister_experiment_instance.assert_called_once_with("exp-zombie")
            
            # Verify lock released
            mock_idempotency.release_lock.assert_called_once()
    
    def test_task_skips_if_lock_unavailable(self):
        """Test task skips if distributed lock unavailable (another scheduler handling)."""
        from selfhealing.tasks.chaos_scheduler import hunt_zombie_experiments
        
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler, \
             patch("selfhealing.services.idempotency_service.IdempotencyService") as mock_idem_class:
            
            mock_exp = Mock()
            mock_exp.experiment_id = "exp-locked"
            mock_exp._is_expired_monotonic.return_value = True
            
            mock_scheduler = Mock()
            mock_scheduler.get_experiments_by_status.return_value = [mock_exp]
            mock_get_scheduler.return_value = mock_scheduler
            
            mock_idempotency = Mock()
            mock_idempotency.acquire_lock.return_value = False  # Lock unavailable
            mock_idem_class.return_value = mock_idempotency
            
            result = hunt_zombie_experiments()
            
            assert result["success"] is True
            assert result["hunted"] == 0
            assert result["skipped"] == 1
            
            # Verify rollback NOT called
            mock_exp.rollback.assert_not_called()
    
    def test_task_uses_monotonic_ttl_if_available(self):
        """Test task prefers _is_expired_monotonic over is_expired."""
        from selfhealing.tasks.chaos_scheduler import hunt_zombie_experiments
        
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler, \
             patch("selfhealing.services.idempotency_service.IdempotencyService") as mock_idem_class:
            
            mock_exp = Mock()
            mock_exp.experiment_id = "exp-monotonic"
            mock_exp._is_expired_monotonic.return_value = True
            mock_exp.is_expired.return_value = False  # Regular TTL says not expired
            
            mock_scheduler = Mock()
            mock_scheduler.get_experiments_by_status.return_value = [mock_exp]
            mock_get_scheduler.return_value = mock_scheduler
            
            mock_idempotency = Mock()
            mock_idempotency.acquire_lock.return_value = True
            mock_idem_class.return_value = mock_idempotency
            
            result = hunt_zombie_experiments()
            
            # Should use monotonic TTL result (expired)
            assert result["hunted"] == 1
            
            # is_expired should NOT be called since _is_expired_monotonic exists
            mock_exp.is_expired.assert_not_called()
    
    def test_task_handles_exception(self):
        """Test task handles exceptions gracefully."""
        from selfhealing.tasks.chaos_scheduler import hunt_zombie_experiments
        
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler:
            mock_get_scheduler.side_effect = Exception("Scheduler unavailable")
            
            result = hunt_zombie_experiments()
            
            assert result["success"] is False
            assert "error" in result


# =============================================================================
# Test: Monotonic TTL Methods in ChaosExperiment
# =============================================================================


class TestMonotonicTTLMethods:
    """Test Monotonic TTL methods in ChaosExperiment base class."""
    
    def test_start_monotonic_timer_creates_helper(self):
        """Test _start_monotonic_timer creates MonotonicTTLHelper."""
        from selfhealing.services.chaos.base import ChaosExperiment, MonotonicTTLHelper
        
        # Create a concrete subclass for testing
        class TestExperiment(ChaosExperiment):
            experiment_type = "test"
            
            def inject_chaos(self) -> bool:
                return True
            
            def rollback(self) -> bool:
                return True
        
        experiment = TestExperiment()
        experiment._effective_ttl = 300
        
        experiment._start_monotonic_timer()
        
        assert hasattr(experiment, "_monotonic_ttl_helper")
        assert experiment._monotonic_ttl_helper is not None
        assert isinstance(experiment._monotonic_ttl_helper, MonotonicTTLHelper)
        assert experiment._monotonic_ttl_helper.is_started()
    
    def test_is_expired_monotonic_uses_helper(self):
        """Test _is_expired_monotonic uses MonotonicTTLHelper."""
        from selfhealing.services.chaos.base import ChaosExperiment
        
        class TestExperiment(ChaosExperiment):
            experiment_type = "test"
            
            def inject_chaos(self) -> bool:
                return True
            
            def rollback(self) -> bool:
                return True
        
        experiment = TestExperiment()
        experiment._effective_ttl = 0.01  # 10ms for quick test
        
        experiment._start_monotonic_timer()
        
        # Should not be expired immediately
        assert experiment._is_expired_monotonic() is False
        
        # Wait for TTL to expire
        import time
        time.sleep(0.02)  # 20ms
        
        # Should be expired now
        assert experiment._is_expired_monotonic() is True
    
    def test_is_expired_monotonic_fallback_without_helper(self):
        """Test _is_expired_monotonic falls back to is_expired without helper."""
        from selfhealing.services.chaos.base import ChaosExperiment
        from django.utils import timezone
        
        class TestExperiment(ChaosExperiment):
            experiment_type = "test"
            
            def inject_chaos(self) -> bool:
                return True
            
            def rollback(self) -> bool:
                return True
        
        experiment = TestExperiment()
        # Use timezone-aware datetime (Django timezone.now() is aware)
        experiment._expires_at = timezone.now() - timedelta(seconds=10)  # Already expired
        
        # Without monotonic helper, should use regular is_expired
        assert experiment._is_expired_monotonic() is True
    
    def test_get_elapsed_monotonic(self):
        """Test get_elapsed_monotonic returns elapsed time."""
        from selfhealing.services.chaos.base import ChaosExperiment
        
        class TestExperiment(ChaosExperiment):
            experiment_type = "test"
            
            def inject_chaos(self) -> bool:
                return True
            
            def rollback(self) -> bool:
                return True
        
        experiment = TestExperiment()
        experiment._effective_ttl = 300
        
        experiment._start_monotonic_timer()
        
        import time
        time.sleep(0.1)  # 100ms
        
        elapsed = experiment.get_elapsed_monotonic()
        
        # Allow some tolerance for timing (0.08s instead of 0.1s)
        assert elapsed >= 0.08
        assert elapsed < 0.5  # Should be close to 100ms
    
    def test_get_remaining_monotonic(self):
        """Test get_remaining_monotonic returns remaining time."""
        from selfhealing.services.chaos.base import ChaosExperiment
        
        class TestExperiment(ChaosExperiment):
            experiment_type = "test"
            
            def inject_chaos(self) -> bool:
                return True
            
            def rollback(self) -> bool:
                return True
        
        experiment = TestExperiment()
        experiment._effective_ttl = 300
        
        experiment._start_monotonic_timer()
        
        remaining = experiment.get_remaining_monotonic()
        
        # remaining should be close to 300s (accounting for slight execution time)
        assert remaining <= 300.0
        assert remaining > 299.0


# =============================================================================
# Test: Celery Beat Schedule
# =============================================================================


class TestCeleryBeatScheduleZombieHunter:
    """Test Celery Beat schedule includes zombie hunter."""
    
    def test_zombie_hunter_in_schedule(self):
        """Test chaos-hunt-zombie-experiments is in Celery Beat schedule."""
        from myproject.celery import app
        
        schedule = app.conf.beat_schedule
        
        assert "chaos-hunt-zombie-experiments" in schedule
        task_config = schedule["chaos-hunt-zombie-experiments"]
        
        assert task_config["task"] == "selfhealing.celery_tasks.hunt_zombie_experiments"
        assert task_config["schedule"] == 60.0  # 1 minute
        assert task_config["options"]["queue"] == "chaos"


# =============================================================================
# Test: Constants and Isolation Helpers (packages version)
# =============================================================================


class TestConstantsPackage:
    """Test constants module in packages."""
    
    def test_constants_exists(self):
        """Test constants.py exists in packages."""
        from selfhealing.services.chaos.constants import (
            CHAOS_DOMAIN_PREFIX,
            CHAOS_METADATA_FLAGS,
        )
        
        assert CHAOS_DOMAIN_PREFIX == "chaos_test:"
        assert CHAOS_METADATA_FLAGS["is_synthetic"] is True
        assert CHAOS_METADATA_FLAGS["is_chaos_experiment"] is True
    
    def test_experiment_hard_caps(self):
        """Test ExperimentHardCaps class."""
        from selfhealing.services.chaos.constants import ExperimentHardCaps
        
        assert ExperimentHardCaps.DISK_IO_MAX_LATENCY_MS == 2000
        assert ExperimentHardCaps.REPLAY_FLOOD_MAX_ENTRIES == 5000
        assert ExperimentHardCaps.CLOCK_SKEW_MAX_SECONDS == 86400
    
    def test_apply_cap(self):
        """Test ExperimentHardCaps.apply_cap method."""
        from selfhealing.services.chaos.constants import ExperimentHardCaps
        
        # Value within cap
        result = ExperimentHardCaps.apply_cap("simulated_disk_io", "latency_ms", 1000)
        assert result == 1000
        
        # Value exceeds cap
        result = ExperimentHardCaps.apply_cap("simulated_disk_io", "latency_ms", 5000)
        assert result == 2000  # Capped to max
        
        # Unknown experiment type
        result = ExperimentHardCaps.apply_cap("unknown", "field", 9999)
        assert result == 9999  # No cap applied


class TestIsolationHelpersPackage:
    """Test isolation_helpers module in packages."""
    
    def test_get_isolated_domain(self):
        """Test get_isolated_domain function."""
        from selfhealing.services.chaos.isolation_helpers import get_isolated_domain
        
        assert get_isolated_domain("payment") == "chaos_test:payment"
        assert get_isolated_domain("chaos_test:payment") == "chaos_test:payment"  # No double prefix
    
    def test_strip_isolation_prefix(self):
        """Test strip_isolation_prefix function."""
        from selfhealing.services.chaos.isolation_helpers import strip_isolation_prefix
        
        assert strip_isolation_prefix("chaos_test:payment") == "payment"
        assert strip_isolation_prefix("payment") == "payment"
    
    def test_is_chaos_domain(self):
        """Test is_chaos_domain function."""
        from selfhealing.services.chaos.isolation_helpers import is_chaos_domain
        
        assert is_chaos_domain("chaos_test:payment") is True
        assert is_chaos_domain("payment") is False
    
    def test_get_isolation_metadata(self):
        """Test get_isolation_metadata function."""
        from selfhealing.services.chaos.isolation_helpers import get_isolation_metadata
        
        metadata = get_isolation_metadata("exp-123", "replay_flood")
        
        assert metadata["is_synthetic"] is True
        assert metadata["is_chaos_experiment"] is True
        assert metadata["chaos_experiment_id"] == "exp-123"
        assert metadata["chaos_experiment_type"] == "replay_flood"
        assert metadata["chaos_domain_prefix"] == "chaos_test:"
    
    def test_should_exclude_from_metrics(self):
        """Test should_exclude_from_metrics function."""
        from selfhealing.services.chaos.isolation_helpers import should_exclude_from_metrics
        
        assert should_exclude_from_metrics(None) is False
        assert should_exclude_from_metrics({}) is False
        assert should_exclude_from_metrics({"is_chaos_experiment": True}) is True
        assert should_exclude_from_metrics({"is_synthetic": True}) is True
        assert should_exclude_from_metrics({"is_chaos_experiment": False}) is False


# =============================================================================
# Test: IdempotencyDomain.CHAOS_ZOMBIE_HUNTER
# =============================================================================


class TestIdempotencyDomainZombieHunter:
    """Test CHAOS_ZOMBIE_HUNTER is in IdempotencyDomain."""
    
    def test_chaos_zombie_hunter_domain_exists(self):
        """Test CHAOS_ZOMBIE_HUNTER exists in IdempotencyDomain."""
        from selfhealing.services.idempotency_service import IdempotencyDomain
        
        assert hasattr(IdempotencyDomain, "CHAOS_ZOMBIE_HUNTER")
        assert IdempotencyDomain.CHAOS_ZOMBIE_HUNTER.value == "chaos_zombie_hunter"
