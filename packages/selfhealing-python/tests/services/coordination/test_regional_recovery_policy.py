"""
Unit tests for Regional Recovery Policy.

Tests:
- 리전별 설정 관리
- 기본 설정 조회
- 복구 단계 생성
- 수동 승인 필수 여부
- 우선순위 정렬

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.2
"""

import pytest

from selfhealing.services.coordination.regional_recovery_policy import (
    RegionalRecoveryConfig,
    RegionalRecoveryPolicyEngine,
    DEFAULT_REGIONAL_CONFIGS,
    get_regional_recovery_policy_engine,
    reset_regional_recovery_policy_engine,
)
from selfhealing.services.coordination.recovery_state import RecoveryStepType


class TestRegionalRecoveryConfig:
    """RegionalRecoveryConfig 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        config = RegionalRecoveryConfig()
        
        assert config.namespace == ""
        assert config.stability_check_duration_minutes == 10
        assert config.error_rate_threshold == 0.10
        assert config.success_rate_threshold == 0.95
        assert config.require_manual_approval is False
        assert config.priority == 0

    def test_custom_values(self):
        """커스텀 값 설정."""
        config = RegionalRecoveryConfig(
            namespace="payment",
            stability_check_duration_minutes=15,
            error_rate_threshold=0.05,
            require_manual_approval=True,
            priority=100,
        )
        
        assert config.namespace == "payment"
        assert config.stability_check_duration_minutes == 15
        assert config.error_rate_threshold == 0.05
        assert config.require_manual_approval is True
        assert config.priority == 100

    def test_to_dict(self):
        """딕셔너리 변환."""
        config = RegionalRecoveryConfig(
            namespace="seoul",
            priority=50,
        )
        
        data = config.to_dict()
        
        assert data["namespace"] == "seoul"
        assert data["priority"] == 50
        assert "stability_check_duration_minutes" in data

    def test_from_dict(self):
        """딕셔너리에서 생성."""
        data = {
            "namespace": "tokyo",
            "stability_check_duration_minutes": 7,
            "require_manual_approval": True,
        }
        
        config = RegionalRecoveryConfig.from_dict(data)
        
        assert config.namespace == "tokyo"
        assert config.stability_check_duration_minutes == 7
        assert config.require_manual_approval is True


class TestDefaultRegionalConfigs:
    """DEFAULT_REGIONAL_CONFIGS 테스트."""

    def test_seoul_config_exists(self):
        """서울 설정 존재."""
        assert "seoul" in DEFAULT_REGIONAL_CONFIGS
        
        config = DEFAULT_REGIONAL_CONFIGS["seoul"]
        assert config.namespace == "seoul"
        assert config.require_manual_approval is True  # 수동 승인 필수
        assert config.priority == 100  # 최우선

    def test_global_config_exists(self):
        """글로벌 설정 존재."""
        assert "global" in DEFAULT_REGIONAL_CONFIGS
        
        config = DEFAULT_REGIONAL_CONFIGS["global"]
        assert config.namespace == "global"
        assert config.require_manual_approval is False
        assert config.priority == 0

    def test_tokyo_less_strict_than_seoul(self):
        """도쿄는 서울보다 덜 엄격."""
        seoul = DEFAULT_REGIONAL_CONFIGS["seoul"]
        tokyo = DEFAULT_REGIONAL_CONFIGS["tokyo"]
        
        assert tokyo.error_rate_threshold > seoul.error_rate_threshold
        assert tokyo.priority < seoul.priority

    def test_oregon_most_relaxed(self):
        """오레곤은 가장 느슨."""
        oregon = DEFAULT_REGIONAL_CONFIGS["oregon"]
        
        assert oregon.error_rate_threshold == 0.15
        assert oregon.require_manual_approval is False
        assert oregon.priority == 10


class TestRegionalRecoveryPolicyEngine:
    """RegionalRecoveryPolicyEngine 테스트."""

    @pytest.fixture
    def engine(self):
        """테스트용 정책 엔진."""
        return RegionalRecoveryPolicyEngine()

    def test_get_config_known_namespace(self, engine):
        """등록된 네임스페이스 설정 조회."""
        config = engine.get_config("seoul")
        
        assert config.namespace == "seoul"
        assert config.require_manual_approval is True

    def test_get_config_unknown_namespace_fallback(self, engine):
        """알 수 없는 네임스페이스는 global 사용."""
        config = engine.get_config("singapore")
        
        # global 설정 반환
        assert config.namespace == "global"

    def test_register_config(self, engine):
        """설정 등록."""
        new_config = RegionalRecoveryConfig(
            namespace="singapore",
            stability_check_duration_minutes=8,
            priority=60,
        )
        
        engine.register_config(new_config)
        
        config = engine.get_config("singapore")
        assert config.namespace == "singapore"
        assert config.stability_check_duration_minutes == 8

    def test_remove_config(self, engine):
        """설정 제거."""
        engine.register_config(RegionalRecoveryConfig(namespace="test"))
        
        result = engine.remove_config("test")
        
        assert result is True
        # 제거 후 조회 시 global 반환
        config = engine.get_config("test")
        assert config.namespace == "global"

    def test_remove_nonexistent_config(self, engine):
        """없는 설정 제거."""
        result = engine.remove_config("nonexistent")
        assert result is False

    def test_list_configs_sorted_by_priority(self, engine):
        """설정 목록은 우선순위 순."""
        configs = engine.list_configs()
        
        # 첫 번째가 가장 높은 우선순위
        assert configs[0].namespace == "seoul"
        assert configs[0].priority == 100
        
        # 마지막이 가장 낮은 우선순위
        assert configs[-1].namespace == "global"
        assert configs[-1].priority == 0


class TestRegionalRecoverySteps:
    """복구 단계 생성 테스트."""

    @pytest.fixture
    def engine(self):
        """테스트용 정책 엔진."""
        return RegionalRecoveryPolicyEngine()

    def test_get_recovery_steps_level3(self, engine):
        """LEVEL_3 복구 단계 (전체 포함)."""
        steps = engine.get_recovery_steps("global", "LEVEL_3")
        
        step_types = [s.step_type for s in steps]
        
        assert RecoveryStepType.BUDGET_RESET in step_types
        assert RecoveryStepType.HEALTH_CHECK in step_types
        assert RecoveryStepType.CANARY_RESUME in step_types
        assert RecoveryStepType.GOVERNANCE_NORMAL in step_types

    def test_get_recovery_steps_level2(self, engine):
        """LEVEL_2 복구 단계 (GOVERNANCE_NORMAL 제외)."""
        steps = engine.get_recovery_steps("global", "LEVEL_2")
        
        step_types = [s.step_type for s in steps]
        
        assert RecoveryStepType.BUDGET_RESET in step_types
        assert RecoveryStepType.HEALTH_CHECK in step_types
        assert RecoveryStepType.CANARY_RESUME in step_types
        assert RecoveryStepType.GOVERNANCE_NORMAL not in step_types

    def test_steps_use_regional_config(self, engine):
        """단계가 리전별 설정 사용."""
        seoul_steps = engine.get_recovery_steps("seoul", "LEVEL_3")
        global_steps = engine.get_recovery_steps("global", "LEVEL_3")
        
        seoul_health = next(s for s in seoul_steps if s.step_type == RecoveryStepType.HEALTH_CHECK)
        global_health = next(s for s in global_steps if s.step_type == RecoveryStepType.HEALTH_CHECK)
        
        # 서울은 더 엄격한 임계값
        assert seoul_health.params["error_rate_threshold"] < global_health.params["error_rate_threshold"]

    def test_steps_order(self, engine):
        """단계 순서 확인."""
        steps = engine.get_recovery_steps("global", "LEVEL_3")
        
        for i, step in enumerate(steps):
            assert step.order == i + 1

    def test_budget_reset_always_first(self, engine):
        """BUDGET_RESET이 항상 첫 번째."""
        steps = engine.get_recovery_steps("global", "LEVEL_3")
        
        assert steps[0].step_type == RecoveryStepType.BUDGET_RESET

    def test_health_check_params(self, engine):
        """HEALTH_CHECK 파라미터 확인."""
        steps = engine.get_recovery_steps("seoul", "LEVEL_3")
        
        health_step = next(s for s in steps if s.step_type == RecoveryStepType.HEALTH_CHECK)
        
        # 서울 설정 사용
        assert health_step.params["duration_minutes"] == 10
        assert health_step.params["error_rate_threshold"] == 0.05  # 5%


class TestRegionalApprovalSettings:
    """수동 승인 설정 테스트."""

    @pytest.fixture
    def engine(self):
        """테스트용 정책 엔진."""
        return RegionalRecoveryPolicyEngine()

    def test_should_require_approval_seoul(self, engine):
        """서울은 수동 승인 필수."""
        assert engine.should_require_approval("seoul") is True

    def test_should_require_approval_tokyo(self, engine):
        """도쿄는 수동 승인 불필요."""
        assert engine.should_require_approval("tokyo") is False

    def test_get_approval_timeout(self, engine):
        """승인 타임아웃 조회."""
        timeout = engine.get_approval_timeout("seoul")
        
        assert timeout == 30  # 서울은 30분

    def test_get_namespaces_by_priority(self, engine):
        """우선순위 순 네임스페이스 목록."""
        namespaces = engine.get_namespaces_by_priority()
        
        assert namespaces[0] == "seoul"  # 최우선
        assert "global" in namespaces


class TestRegionalRecoveryNoCanaryResume:
    """CANARY_RESUME 비활성화 테스트."""

    def test_no_canary_resume_when_disabled(self):
        """include_canary_resume=False면 제외."""
        engine = RegionalRecoveryPolicyEngine()
        
        # 커스텀 설정 등록
        engine.register_config(RegionalRecoveryConfig(
            namespace="analytics",
            include_canary_resume=False,
        ))
        
        steps = engine.get_recovery_steps("analytics", "LEVEL_3")
        step_types = [s.step_type for s in steps]
        
        assert RecoveryStepType.CANARY_RESUME not in step_types


class TestRegionalRecoverySingleton:
    """싱글톤 테스트."""

    def test_singleton(self):
        """싱글톤 동작 확인."""
        reset_regional_recovery_policy_engine()
        
        engine1 = get_regional_recovery_policy_engine()
        engine2 = get_regional_recovery_policy_engine()
        
        assert engine1 is engine2
        
        reset_regional_recovery_policy_engine()

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        reset_regional_recovery_policy_engine()
        
        engine1 = get_regional_recovery_policy_engine()
        reset_regional_recovery_policy_engine()
        engine2 = get_regional_recovery_policy_engine()
        
        assert engine1 is not engine2
        
        reset_regional_recovery_policy_engine()
