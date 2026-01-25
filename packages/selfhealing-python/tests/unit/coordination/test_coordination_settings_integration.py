"""
Coordination 서비스 Settings 연동 테스트.

104_HARDCODED_CONFIG_COORDINATION_REFACTORING.md Step 2에서 수정된
Coordination 서비스 파일들이 settings를 올바르게 사용하는지 검증합니다.

테스트 대상:
- recovery_tasks.py: get_recovery_beat_schedule() settings 연동
- critical_worker.py: queue_configs settings 연동
- regional_recovery_policy.py: get_default_regional_configs() settings 연동
- redis_key_guard.py: volatile_key_ttl, key_pattern_configs settings 연동
- recovery_coordinator.py: _get_recovery_steps(), check_recovery_trigger() settings 연동
"""

import pytest
from unittest.mock import patch, MagicMock


# =============================================================================
# recovery_tasks.py 테스트
# =============================================================================


class TestRecoveryTasksSettingsIntegration:
    """recovery_tasks.py의 settings 연동 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """테스트 전후 싱글톤 리셋."""
        from selfhealing.settings.recovery_tasks import reset_recovery_tasks_settings
        reset_recovery_tasks_settings()
        yield
        reset_recovery_tasks_settings()

    def test_get_recovery_beat_schedule_uses_settings(self, monkeypatch):
        """get_recovery_beat_schedule()이 settings 값을 사용하는지 검증."""
        from selfhealing.services.coordination.recovery_tasks import (
            get_recovery_beat_schedule,
        )
        from selfhealing.settings.recovery_tasks import reset_recovery_tasks_settings
        
        # 환경변수로 커스텀 값 설정
        monkeypatch.setenv("SELFHEALING_RECOVERY_TASKS_TRIGGER_CHECK_INTERVAL", "120")
        monkeypatch.setenv("SELFHEALING_RECOVERY_TASKS_HEALTH_MONITOR_INTERVAL", "45")
        monkeypatch.setenv("SELFHEALING_RECOVERY_TASKS_STALE_CHECK_INTERVAL", "20")
        
        # 싱글톤 리셋하여 새 환경변수 반영
        reset_recovery_tasks_settings()
        
        schedule = get_recovery_beat_schedule()
        
        # 커스텀 값이 적용되었는지 확인
        assert schedule["check-recovery-trigger-every-minute"]["schedule"] == 120
        assert schedule["monitor-recovery-health-every-30s"]["schedule"] == 45
        assert schedule["check-stale-pending-every-10min"]["schedule"] == 20 * 60  # 분 -> 초

    def test_get_recovery_beat_schedule_default_values(self):
        """기본값으로 스케줄이 올바르게 생성되는지 검증."""
        from selfhealing.services.coordination.recovery_tasks import (
            get_recovery_beat_schedule,
        )
        
        schedule = get_recovery_beat_schedule()
        
        # 기본값: trigger_check=60, health_monitor=30, stale_check=10
        assert schedule["check-recovery-trigger-every-minute"]["schedule"] == 60
        assert schedule["monitor-recovery-health-every-30s"]["schedule"] == 30
        assert schedule["check-stale-pending-every-10min"]["schedule"] == 10 * 60
        
        # 고정값: cleanup은 24시간
        assert schedule["cleanup-old-sessions-daily"]["schedule"] == 86400

    def test_schedule_task_names_and_queues(self):
        """스케줄의 태스크 이름과 큐가 올바른지 검증."""
        from selfhealing.services.coordination.recovery_tasks import (
            get_recovery_beat_schedule,
        )
        
        schedule = get_recovery_beat_schedule()
        
        assert schedule["check-recovery-trigger-every-minute"]["task"] == \
            "selfhealing.check_recovery_trigger"
        assert schedule["check-recovery-trigger-every-minute"]["options"]["queue"] == \
            "selfhealing_recovery"
        
        assert schedule["monitor-recovery-health-every-30s"]["task"] == \
            "selfhealing.monitor_recovery_health"
        
        assert schedule["check-stale-pending-every-10min"]["task"] == \
            "selfhealing.check_stale_pending_recoveries"
        assert schedule["check-stale-pending-every-10min"]["options"]["queue"] == \
            "selfhealing_notifications"


# =============================================================================
# critical_worker.py 테스트
# =============================================================================


class TestCriticalWorkerSettingsIntegration:
    """critical_worker.py의 settings 연동 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """테스트 전후 싱글톤 리셋."""
        from selfhealing.settings.critical_worker import reset_critical_worker_settings
        from selfhealing.services.coordination.critical_worker import (
            _worker_config,
        )
        import selfhealing.services.coordination.critical_worker as cw_module
        
        reset_critical_worker_settings()
        cw_module._worker_config = None
        yield
        reset_critical_worker_settings()
        cw_module._worker_config = None

    def test_queue_configs_use_settings_values(self, monkeypatch):
        """queue_configs가 settings 값을 사용하는지 검증."""
        from selfhealing.settings.critical_worker import reset_critical_worker_settings
        
        # 환경변수로 커스텀 값 설정
        monkeypatch.setenv("SELFHEALING_CRITICALWORKER_CRITICAL_QUEUE_NAME", "custom.critical")
        monkeypatch.setenv("SELFHEALING_CRITICALWORKER_CRITICAL_WORKER_COUNT", "5")
        monkeypatch.setenv("SELFHEALING_CRITICALWORKER_CRITICAL_CONCURRENCY", "3")
        
        reset_critical_worker_settings()
        
        # 새 인스턴스 생성하여 settings 반영 확인
        from selfhealing.services.coordination.critical_worker import (
            CriticalPathDedicatedWorkerConfig,
        )
        
        config = CriticalPathDedicatedWorkerConfig()
        
        assert config.critical_queue_name == "custom.critical"
        assert config.critical_worker_count == 5
        assert config.queue_configs["critical"].queue_name == "custom.critical"
        assert config.queue_configs["critical"].worker_count == 5
        assert config.queue_configs["critical"].concurrency == 3

    def test_default_queue_configs(self):
        """기본 queue_configs가 올바르게 설정되는지 검증."""
        from selfhealing.services.coordination.critical_worker import (
            CriticalPathDedicatedWorkerConfig,
        )
        
        config = CriticalPathDedicatedWorkerConfig()
        
        # 기본 큐 이름 확인
        assert config.queue_configs["critical"].queue_name == "selfhealing.critical"
        assert config.queue_configs["high"].queue_name == "selfhealing.high"
        assert config.queue_configs["default"].queue_name == "selfhealing.default"
        
        # 기본 워커 수 확인
        assert config.queue_configs["critical"].worker_count == 2
        assert config.queue_configs["high"].worker_count == 4
        assert config.queue_configs["default"].worker_count == 8


# =============================================================================
# regional_recovery_policy.py 테스트
# =============================================================================


class TestRegionalRecoveryPolicySettingsIntegration:
    """regional_recovery_policy.py의 settings 연동 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """테스트 전후 싱글톤 리셋."""
        from selfhealing.settings.regional_recovery_policy import (
            reset_regional_recovery_policy_settings,
        )
        from selfhealing.services.coordination.regional_recovery_policy import (
            reset_default_regional_configs,
        )
        
        reset_regional_recovery_policy_settings()
        reset_default_regional_configs()
        yield
        reset_regional_recovery_policy_settings()
        reset_default_regional_configs()

    def test_get_default_regional_configs_uses_settings(self, monkeypatch):
        """get_default_regional_configs()가 settings 값을 사용하는지 검증."""
        from selfhealing.settings.regional_recovery_policy import (
            reset_regional_recovery_policy_settings,
        )
        from selfhealing.services.coordination.regional_recovery_policy import (
            get_default_regional_configs,
            reset_default_regional_configs,
        )
        
        # 환경변수로 커스텀 값 설정
        monkeypatch.setenv("SELFHEALING_REGIONAL_RECOVERY_ERROR_RATE_THRESHOLD", "0.08")
        monkeypatch.setenv("SELFHEALING_REGIONAL_RECOVERY_SUCCESS_RATE_THRESHOLD", "0.97")
        
        reset_regional_recovery_policy_settings()
        reset_default_regional_configs()
        
        configs = get_default_regional_configs()
        
        # tokyo 리전은 기본값(settings)을 사용
        assert configs["tokyo"].error_rate_threshold == 0.08
        assert configs["tokyo"].success_rate_threshold == 0.97
        
        # global 리전도 settings에서 가져옴 (from_settings 사용)
        assert configs["global"].error_rate_threshold == 0.08
        assert configs["global"].success_rate_threshold == 0.97

    def test_default_regional_configs_fixed_values(self):
        """고정된 리전 설정값이 올바른지 검증."""
        from selfhealing.services.coordination.regional_recovery_policy import (
            get_default_regional_configs,
        )
        
        configs = get_default_regional_configs()
        
        # 서울 리전: 결제 관련, 가장 엄격
        assert configs["seoul"].error_rate_threshold == 0.05
        assert configs["seoul"].success_rate_threshold == 0.98
        assert configs["seoul"].require_manual_approval is True
        assert configs["seoul"].priority == 100
        
        # 오레곤 리전: 분석 관련, 가장 느슨
        assert configs["oregon"].error_rate_threshold == 0.15
        assert configs["oregon"].success_rate_threshold == 0.90

    def test_policy_engine_uses_dynamic_configs(self):
        """RegionalRecoveryPolicyEngine이 동적 configs를 사용하는지 검증."""
        from selfhealing.services.coordination.regional_recovery_policy import (
            RegionalRecoveryPolicyEngine,
            get_default_regional_configs,
        )
        
        engine = RegionalRecoveryPolicyEngine()
        
        # 등록된 configs와 get_default_regional_configs()가 일치
        expected_configs = get_default_regional_configs()
        
        for namespace in expected_configs:
            config = engine.get_config(namespace)
            assert config.namespace == namespace


# =============================================================================
# redis_key_guard.py 테스트
# =============================================================================


class TestRedisKeyGuardSettingsIntegration:
    """redis_key_guard.py의 settings 연동 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """테스트 전후 싱글톤 리셋."""
        from selfhealing.settings.redis_key_guard import reset_redis_key_guard_settings
        reset_redis_key_guard_settings()
        yield
        reset_redis_key_guard_settings()

    def test_volatile_key_ttl_uses_settings(self, monkeypatch):
        """volatile_key_ttl이 settings 값을 사용하는지 검증."""
        from selfhealing.settings.redis_key_guard import reset_redis_key_guard_settings
        
        # 환경변수로 커스텀 TTL 설정
        monkeypatch.setenv("SELFHEALING_REDIS_GUARD_CACHE_TTL_SECONDS", "7200")
        monkeypatch.setenv("SELFHEALING_REDIS_GUARD_METRICS_REALTIME_TTL_SECONDS", "900")
        
        reset_redis_key_guard_settings()
        
        from selfhealing.services.coordination.redis_key_guard import (
            RedisKeyPriorityEviction,
        )
        
        eviction = RedisKeyPriorityEviction()
        
        assert eviction.volatile_key_ttl["cache:*"] == 7200
        assert eviction.volatile_key_ttl["metrics:realtime:*"] == 900

    def test_key_pattern_configs_ttl_uses_settings(self, monkeypatch):
        """key_pattern_configs의 TTL이 settings 값을 사용하는지 검증."""
        from selfhealing.settings.redis_key_guard import reset_redis_key_guard_settings
        
        monkeypatch.setenv("SELFHEALING_REDIS_GUARD_AUDIT_EVENT_TTL_SECONDS", "1209600")  # 14일
        
        reset_redis_key_guard_settings()
        
        from selfhealing.services.coordination.redis_key_guard import (
            RedisKeyPriorityEviction,
        )
        
        eviction = RedisKeyPriorityEviction()
        
        # P4 감사 이벤트 TTL 확인
        audit_config = next(
            c for c in eviction.key_pattern_configs 
            if c.pattern == "audit:event:*"
        )
        assert audit_config.default_ttl_seconds == 1209600

    def test_memory_thresholds_use_settings(self, monkeypatch):
        """메모리 임계값이 settings 값을 사용하는지 검증."""
        from selfhealing.settings.redis_key_guard import reset_redis_key_guard_settings
        
        monkeypatch.setenv("SELFHEALING_REDIS_GUARD_MEMORY_WARNING_THRESHOLD", "75.0")
        monkeypatch.setenv("SELFHEALING_REDIS_GUARD_MEMORY_CRITICAL_THRESHOLD", "85.0")
        
        reset_redis_key_guard_settings()
        
        from selfhealing.services.coordination.redis_key_guard import (
            RedisKeyPriorityEviction,
        )
        
        eviction = RedisKeyPriorityEviction()
        
        assert eviction.memory_warning_threshold == 75.0
        assert eviction.memory_critical_threshold == 85.0


# =============================================================================
# recovery_coordinator.py 테스트
# =============================================================================


class TestRecoveryCoordinatorSettingsIntegration:
    """recovery_coordinator.py의 settings 연동 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """테스트 전후 싱글톤 리셋."""
        from selfhealing.settings.recovery_coordinator import (
            reset_recovery_coordinator_settings,
        )
        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    def test_get_recovery_steps_uses_settings(self, monkeypatch):
        """_get_recovery_steps()가 settings 값을 사용하는지 검증."""
        from selfhealing.settings.recovery_coordinator import (
            reset_recovery_coordinator_settings,
        )
        
        # 환경변수로 LEVEL_3 파라미터 설정
        monkeypatch.setenv("SELFHEALING_RECOVERY_COORD_LEVEL3_HEALTH_CHECK_DURATION_MINUTES", "8")
        monkeypatch.setenv("SELFHEALING_RECOVERY_COORD_LEVEL3_HEALTH_CHECK_SUCCESS_THRESHOLD", "0.98")
        monkeypatch.setenv("SELFHEALING_RECOVERY_COORD_LEVEL3_CANARY_RESUME_WAIT_AFTER", "90")
        
        reset_recovery_coordinator_settings()
        
        from selfhealing.services.coordination.recovery_coordinator import (
            RecoveryCoordinator,
        )
        from selfhealing.services.coordination.recovery_state import RecoveryStepType
        
        coordinator = RecoveryCoordinator()
        steps = coordinator._get_recovery_steps("LEVEL_3", "global")
        
        # HEALTH_CHECK 단계 파라미터 확인
        health_check_step = next(s for s in steps if s.step_type == RecoveryStepType.HEALTH_CHECK)
        assert health_check_step.params["duration_minutes"] == 8
        assert health_check_step.params["success_threshold"] == 0.98
        
        # CANARY_RESUME 단계 wait_after 확인
        canary_step = next(s for s in steps if s.step_type == RecoveryStepType.CANARY_RESUME)
        assert canary_step.wait_after_seconds == 90

    def test_get_recovery_steps_level_specific(self):
        """레벨별로 다른 단계가 생성되는지 검증."""
        from selfhealing.services.coordination.recovery_coordinator import (
            RecoveryCoordinator,
        )
        from selfhealing.services.coordination.recovery_state import RecoveryStepType
        
        coordinator = RecoveryCoordinator()
        
        # LEVEL_3: 4단계 (BUDGET_RESET, HEALTH_CHECK, CANARY_RESUME, GOVERNANCE_NORMAL)
        level3_steps = coordinator._get_recovery_steps("LEVEL_3", "global")
        step_types_3 = [s.step_type for s in level3_steps]
        assert RecoveryStepType.GOVERNANCE_NORMAL in step_types_3
        assert len(level3_steps) == 4
        
        # LEVEL_2: 3단계 (BUDGET_RESET, HEALTH_CHECK, CANARY_RESUME)
        level2_steps = coordinator._get_recovery_steps("LEVEL_2", "global")
        step_types_2 = [s.step_type for s in level2_steps]
        assert RecoveryStepType.GOVERNANCE_NORMAL not in step_types_2
        assert len(level2_steps) == 3
        
        # LEVEL_1: 2단계 (BUDGET_RESET, HEALTH_CHECK)
        level1_steps = coordinator._get_recovery_steps("LEVEL_1", "global")
        step_types_1 = [s.step_type for s in level1_steps]
        assert RecoveryStepType.CANARY_RESUME not in step_types_1
        assert len(level1_steps) == 2

    def test_check_recovery_trigger_uses_settings(self, monkeypatch):
        """check_recovery_trigger()가 settings 값을 사용하는지 검증."""
        from selfhealing.settings.recovery_coordinator import (
            reset_recovery_coordinator_settings,
        )
        
        # stability check 파라미터 설정
        monkeypatch.setenv("SELFHEALING_RECOVERY_COORD_STABILITY_CHECK_DURATION_MINUTES", "15")
        monkeypatch.setenv("SELFHEALING_RECOVERY_COORD_STABILITY_CHECK_ERROR_RATE_THRESHOLD", "0.08")
        
        reset_recovery_coordinator_settings()
        
        from selfhealing.services.coordination.recovery_coordinator import (
            RecoveryCoordinator,
        )
        
        coordinator = RecoveryCoordinator()
        
        # _check_stability가 호출되는지 확인하기 위해 모킹
        with patch.object(coordinator, '_check_stability') as mock_stability:
            mock_stability.return_value = {"stable": True}
            
            with patch.object(coordinator, '_get_current_emergency_level') as mock_level:
                mock_level.return_value = "LEVEL_2"
                
                coordinator.check_recovery_trigger("global")
                
                # settings 값으로 _check_stability 호출되었는지 확인
                mock_stability.assert_called_once_with(
                    namespace="global",
                    duration_minutes=15,
                    error_rate_threshold=0.08,
                )

    def test_default_recovery_steps_still_available(self):
        """레거시 DEFAULT_RECOVERY_STEPS가 여전히 사용 가능한지 검증."""
        from selfhealing.services.coordination.recovery_coordinator import (
            RecoveryCoordinator,
        )
        
        # 클래스 속성으로 접근 가능
        assert "LEVEL_3" in RecoveryCoordinator.DEFAULT_RECOVERY_STEPS
        assert "LEVEL_2" in RecoveryCoordinator.DEFAULT_RECOVERY_STEPS
        assert "LEVEL_1" in RecoveryCoordinator.DEFAULT_RECOVERY_STEPS
