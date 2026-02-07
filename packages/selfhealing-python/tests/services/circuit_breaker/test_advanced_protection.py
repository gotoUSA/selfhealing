"""
Circuit Breaker Advanced Protection Tests

이 모듈은 Circuit Breaker 고급 보호 시스템의 데이터 모델과 설정을 테스트합니다.
"""

import pytest

from selfhealing.services.circuit_breaker.models import (
    ServiceConfig,
    SheddingLevel,
    LoadSheddingPolicy,
    CanaryStage,
    RecoveryStrategy,
    ThresholdMultiplier,
    AdaptiveThresholdPolicy,
    OpenStrategy,
    CircuitBreakerAdvancedConfig,
    PanicThresholdConfig,
    FreezeModeState,
)
from selfhealing.core.config import (
    CircuitBreakerAdvancedConfig as CoreCBAdvancedConfig,
    SelfHealingConfig,
    get_circuit_breaker_advanced_settings,
)


# =============================================================================
# ServiceConfig Tests
# =============================================================================


class TestServiceConfig:
    """ServiceConfig 데이터 모델 테스트."""

    def test_valid_service_config(self):
        """정상적인 서비스 설정 생성."""
        config = ServiceConfig(
            service_id="payment-api",
            criticality="critical",
            shed_priority=0,
            min_traffic_percentage=100.0,
        )
        assert config.service_id == "payment-api"
        assert config.criticality == "critical"
        assert config.shed_priority == 0
        assert config.min_traffic_percentage == 100.0

    def test_all_criticality_levels(self):
        """모든 criticality 레벨 테스트."""
        for level in ["critical", "high", "medium", "low"]:
            config = ServiceConfig(service_id=f"test-{level}", criticality=level)
            assert config.criticality == level

    def test_invalid_criticality_raises_error(self):
        """잘못된 criticality 값은 에러 발생."""
        with pytest.raises(ValueError, match="Invalid criticality"):
            ServiceConfig(service_id="test", criticality="invalid")

    def test_invalid_min_traffic_percentage_negative(self):
        """음수 min_traffic_percentage는 에러 발생."""
        with pytest.raises(ValueError, match="min_traffic_percentage must be between"):
            ServiceConfig(service_id="test", criticality="low", min_traffic_percentage=-1.0)

    def test_invalid_min_traffic_percentage_over_100(self):
        """100 초과 min_traffic_percentage는 에러 발생."""
        with pytest.raises(ValueError, match="min_traffic_percentage must be between"):
            ServiceConfig(service_id="test", criticality="low", min_traffic_percentage=101.0)

    def test_negative_shed_priority_raises_error(self):
        """음수 shed_priority는 에러 발생."""
        with pytest.raises(ValueError, match="shed_priority must be non-negative"):
            ServiceConfig(service_id="test", criticality="low", shed_priority=-1)

    def test_service_config_with_recovery_strategy(self):
        """서비스별 RecoveryStrategy 오버라이드."""
        recovery = RecoveryStrategy(type="canary", strict_mode=True)
        config = ServiceConfig(
            service_id="payment-api",
            criticality="critical",
            recovery_strategy=recovery,
        )
        assert config.recovery_strategy is not None
        assert config.recovery_strategy.strict_mode is True

    def test_service_config_with_threshold_overrides(self):
        """서비스별 CB 임계값 오버라이드."""
        config = ServiceConfig(
            service_id="sensitive-api",
            criticality="high",
            failure_threshold=10,
            window_seconds=120,
        )
        assert config.failure_threshold == 10
        assert config.window_seconds == 120


# =============================================================================
# SheddingLevel Tests
# =============================================================================


class TestSheddingLevel:
    """SheddingLevel 데이터 모델 테스트."""

    def test_valid_shedding_level(self):
        """정상적인 Shedding 레벨 생성."""
        level = SheddingLevel(error_rate=30.0, shed_criticality=["low"], traffic_limit=50.0, description="Level 1")
        assert level.error_rate == 30.0
        assert level.shed_criticality == ["low"]
        assert level.traffic_limit == 50.0

    def test_critical_in_shed_criticality_raises_error(self):
        """critical은 차단 대상에 포함될 수 없음."""
        with pytest.raises(ValueError, match="'critical' cannot be included"):
            SheddingLevel(
                error_rate=70.0,
                shed_criticality=["low", "critical"],
                traffic_limit=0.0,
            )

    def test_invalid_error_rate_negative(self):
        """음수 error_rate는 에러 발생."""
        with pytest.raises(ValueError, match="error_rate must be between"):
            SheddingLevel(error_rate=-10.0, shed_criticality=["low"], traffic_limit=50.0)

    def test_invalid_error_rate_over_100(self):
        """100 초과 error_rate는 에러 발생."""
        with pytest.raises(ValueError, match="error_rate must be between"):
            SheddingLevel(error_rate=150.0, shed_criticality=["low"], traffic_limit=50.0)

    def test_invalid_traffic_limit_negative(self):
        """음수 traffic_limit는 에러 발생."""
        with pytest.raises(ValueError, match="traffic_limit must be between"):
            SheddingLevel(error_rate=30.0, shed_criticality=["low"], traffic_limit=-1.0)


# =============================================================================
# LoadSheddingPolicy Tests
# =============================================================================


class TestLoadSheddingPolicy:
    """LoadSheddingPolicy 데이터 모델 테스트."""

    def test_default_policy(self):
        """기본 Load Shedding 정책."""
        policy = LoadSheddingPolicy()
        assert policy.enabled is True
        assert policy.trigger_threshold == 30.0
        assert len(policy.levels) == 3

    def test_default_levels_progressive(self):
        """기본 레벨은 점진적으로 강화됨."""
        policy = LoadSheddingPolicy()

        # Level 1: 30% 에러율, low만 50% 제한
        assert policy.levels[0].error_rate == 30.0
        assert policy.levels[0].shed_criticality == ["low"]
        assert policy.levels[0].traffic_limit == 50.0

        # Level 2: 50% 에러율, low+medium 80% 제한
        assert policy.levels[1].error_rate == 50.0
        assert "medium" in policy.levels[1].shed_criticality
        assert policy.levels[1].traffic_limit == 20.0

        # Level 3: 70% 에러율, low+medium 완전 차단
        assert policy.levels[2].error_rate == 70.0
        assert policy.levels[2].traffic_limit == 0.0

    def test_custom_policy(self):
        """커스텀 Load Shedding 정책."""
        custom_levels = [
            SheddingLevel(error_rate=40.0, shed_criticality=["low"], traffic_limit=60.0),
            SheddingLevel(error_rate=80.0, shed_criticality=["low", "medium"], traffic_limit=0.0),
        ]
        policy = LoadSheddingPolicy(enabled=True, trigger_threshold=40.0, levels=custom_levels)
        assert policy.trigger_threshold == 40.0
        assert len(policy.levels) == 2


# =============================================================================
# CanaryStage Tests
# =============================================================================


class TestCanaryStage:
    """CanaryStage 데이터 모델 테스트."""

    def test_valid_canary_stage(self):
        """정상적인 Canary 단계 생성."""
        stage = CanaryStage(traffic_percent=10.0, duration_seconds=5, required_success_rate=95.0, description="Stage 1")
        assert stage.traffic_percent == 10.0
        assert stage.duration_seconds == 5
        assert stage.required_success_rate == 95.0

    def test_invalid_traffic_percent_negative(self):
        """음수 traffic_percent는 에러 발생."""
        with pytest.raises(ValueError, match="traffic_percent must be between"):
            CanaryStage(traffic_percent=-10.0, duration_seconds=5, required_success_rate=95.0)

    def test_invalid_traffic_percent_over_100(self):
        """100 초과 traffic_percent는 에러 발생."""
        with pytest.raises(ValueError, match="traffic_percent must be between"):
            CanaryStage(traffic_percent=150.0, duration_seconds=5, required_success_rate=95.0)

    def test_invalid_duration_negative(self):
        """음수 duration_seconds는 에러 발생."""
        with pytest.raises(ValueError, match="duration_seconds must be non-negative"):
            CanaryStage(traffic_percent=10.0, duration_seconds=-1, required_success_rate=95.0)

    def test_invalid_success_rate(self):
        """잘못된 required_success_rate는 에러 발생."""
        with pytest.raises(ValueError, match="required_success_rate must be between"):
            CanaryStage(traffic_percent=10.0, duration_seconds=5, required_success_rate=101.0)


# =============================================================================
# RecoveryStrategy Tests
# =============================================================================


class TestRecoveryStrategy:
    """RecoveryStrategy 데이터 모델 테스트."""

    def test_default_canary_strategy(self):
        """기본 Canary 전략."""
        strategy = RecoveryStrategy()
        assert strategy.type == "canary"
        assert len(strategy.canary_stages) == 4
        assert strategy.on_stage_failure == "restart"
        assert strategy.strict_mode is False

    def test_default_canary_stages_progressive(self):
        """기본 Canary 단계는 10% → 30% → 60% → 100%."""
        strategy = RecoveryStrategy()
        expected_percents = [10.0, 30.0, 60.0, 100.0]
        actual_percents = [stage.traffic_percent for stage in strategy.canary_stages]
        assert actual_percents == expected_percents

    def test_immediate_strategy(self):
        """Immediate 전략."""
        strategy = RecoveryStrategy(type="immediate")
        assert strategy.type == "immediate"

    def test_invalid_type_raises_error(self):
        """잘못된 type은 에러 발생."""
        with pytest.raises(ValueError, match="Invalid type"):
            RecoveryStrategy(type="delayed")  # Delayed는 안티패턴으로 미지원

    def test_invalid_on_stage_failure_raises_error(self):
        """잘못된 on_stage_failure는 에러 발생."""
        with pytest.raises(ValueError, match="Invalid on_stage_failure"):
            RecoveryStrategy(on_stage_failure="ignore")

    def test_strict_mode_for_payment(self):
        """결제 서비스용 strict_mode (100% 성공률 요구)."""
        strategy = RecoveryStrategy(strict_mode=True)
        assert strategy.strict_mode is True


# =============================================================================
# ThresholdMultiplier Tests
# =============================================================================


class TestThresholdMultiplier:
    """ThresholdMultiplier 데이터 모델 테스트."""

    def test_valid_multiplier(self):
        """정상적인 배율 생성."""
        multiplier = ThresholdMultiplier(failure=2.0, window=2.0, description="경고: 10회/120초")
        assert multiplier.failure == 2.0
        assert multiplier.window == 2.0

    def test_infinity_for_lockdown(self):
        """LOCKDOWN용 무한대 배율."""
        multiplier = ThresholdMultiplier(failure=float("inf"), window=float("inf"), description="잠금: 자동 OPEN 금지")
        assert multiplier.failure == float("inf")
        assert multiplier.window == float("inf")

    def test_negative_failure_raises_error(self):
        """음수 failure 배율은 에러 발생."""
        with pytest.raises(ValueError, match="failure multiplier must be non-negative"):
            ThresholdMultiplier(failure=-1.0, window=1.0)

    def test_negative_window_raises_error(self):
        """음수 window 배율은 에러 발생."""
        with pytest.raises(ValueError, match="window multiplier must be non-negative"):
            ThresholdMultiplier(failure=1.0, window=-1.0)


# =============================================================================
# AdaptiveThresholdPolicy Tests
# =============================================================================


class TestAdaptiveThresholdPolicy:
    """AdaptiveThresholdPolicy 데이터 모델 테스트."""

    def test_default_policy(self):
        """기본 Adaptive Threshold 정책."""
        policy = AdaptiveThresholdPolicy()
        assert policy.enabled is True
        assert policy.base_failure_threshold == 5
        assert policy.base_window_seconds == 60
        assert len(policy.level_multipliers) == 5

    def test_all_emergency_levels_defined(self):
        """모든 Emergency Level에 대한 배율 정의."""
        policy = AdaptiveThresholdPolicy()
        expected_levels = ["NORMAL", "ELEVATED", "HIGH", "CRITICAL", "LOCKDOWN"]
        for level in expected_levels:
            assert level in policy.level_multipliers

    def test_get_adjusted_threshold_normal(self):
        """NORMAL 레벨 조정된 임계값."""
        policy = AdaptiveThresholdPolicy()
        failure, window = policy.get_adjusted_threshold("NORMAL")
        assert failure == 5.0  # 5 * 1.0
        assert window == 60.0  # 60 * 1.0

    def test_get_adjusted_threshold_critical(self):
        """CRITICAL 레벨 조정된 임계값 (더 보수적)."""
        policy = AdaptiveThresholdPolicy()
        failure, window = policy.get_adjusted_threshold("CRITICAL")
        assert failure == 15.0  # 5 * 3.0
        assert window == 180.0  # 60 * 3.0

    def test_get_adjusted_threshold_lockdown(self):
        """LOCKDOWN 레벨 조정된 임계값 (무한대)."""
        policy = AdaptiveThresholdPolicy()
        failure, window = policy.get_adjusted_threshold("LOCKDOWN")
        assert failure == float("inf")
        assert window == float("inf")

    def test_get_adjusted_threshold_unknown_level(self):
        """알 수 없는 레벨은 NORMAL로 폴백."""
        policy = AdaptiveThresholdPolicy()
        failure, window = policy.get_adjusted_threshold("UNKNOWN")
        assert failure == 5.0  # NORMAL 기본값
        assert window == 60.0

    def test_progressive_multipliers(self):
        """Emergency Level이 높아질수록 더 보수적 (배율 증가)."""
        policy = AdaptiveThresholdPolicy()

        normal = policy.level_multipliers["NORMAL"]
        elevated = policy.level_multipliers["ELEVATED"]
        high = policy.level_multipliers["HIGH"]
        critical = policy.level_multipliers["CRITICAL"]

        assert normal.failure < elevated.failure < high.failure < critical.failure
        assert normal.window < elevated.window < high.window < critical.window


# =============================================================================
# OpenStrategy Tests
# =============================================================================


class TestOpenStrategy:
    """OpenStrategy 데이터 모델 테스트."""

    def test_default_immediate(self):
        """기본값은 immediate."""
        strategy = OpenStrategy()
        assert strategy.type == "immediate"
        assert strategy.drain_timeout_seconds == 30

    def test_graceful_strategy(self):
        """Graceful 전략 (진행중 요청 완료 후 차단)."""
        strategy = OpenStrategy(type="graceful", drain_timeout_seconds=60)
        assert strategy.type == "graceful"
        assert strategy.drain_timeout_seconds == 60

    def test_invalid_type_raises_error(self):
        """잘못된 type은 에러 발생. Delayed는 안티패턴."""
        with pytest.raises(ValueError, match="Invalid type"):
            OpenStrategy(type="delayed")

    def test_negative_drain_timeout_raises_error(self):
        """음수 drain_timeout은 에러 발생."""
        with pytest.raises(ValueError, match="drain_timeout_seconds must be non-negative"):
            OpenStrategy(drain_timeout_seconds=-1)


# =============================================================================
# CircuitBreakerAdvancedConfig Tests
# =============================================================================


class TestCircuitBreakerAdvancedConfig:
    """CircuitBreakerAdvancedConfig 통합 설정 테스트."""

    def test_default_config(self):
        """기본 설정 생성."""
        config = CircuitBreakerAdvancedConfig()
        assert config.blast_radius_integration is True
        assert config.blast_radius_block_on_critical is True
        assert config.freeze_on_lockdown is True
        assert config.allow_manual_override_in_lockdown is True

    def test_get_service_config_found(self):
        """서비스 설정 조회 - 존재하는 경우."""
        config = CircuitBreakerAdvancedConfig(
            services=[
                ServiceConfig(service_id="payment-api", criticality="critical"),
                ServiceConfig(service_id="order-api", criticality="high"),
            ]
        )

        service = config.get_service_config("payment-api")
        assert service is not None
        assert service.service_id == "payment-api"
        assert service.criticality == "critical"

    def test_get_service_config_not_found(self):
        """서비스 설정 조회 - 존재하지 않는 경우."""
        config = CircuitBreakerAdvancedConfig()
        service = config.get_service_config("unknown-api")
        assert service is None

    def test_get_services_by_criticality(self):
        """criticality로 서비스 목록 조회."""
        config = CircuitBreakerAdvancedConfig(
            services=[
                ServiceConfig(service_id="payment-api", criticality="critical"),
                ServiceConfig(service_id="auth-api", criticality="critical"),
                ServiceConfig(service_id="order-api", criticality="high"),
                ServiceConfig(service_id="review-api", criticality="low"),
            ]
        )

        critical_services = config.get_services_by_criticality("critical")
        assert len(critical_services) == 2
        assert all(s.criticality == "critical" for s in critical_services)

    def test_get_shedding_targets(self):
        """Load Shedding 대상 서비스 조회 (shed_priority > 0)."""
        config = CircuitBreakerAdvancedConfig(
            services=[
                ServiceConfig(service_id="payment-api", criticality="critical", shed_priority=0),
                ServiceConfig(service_id="review-api", criticality="low", shed_priority=10),
                ServiceConfig(service_id="recommend-api", criticality="low", shed_priority=5),
            ]
        )

        targets = config.get_shedding_targets(["low"])
        assert len(targets) == 2
        # 높은 priority가 먼저 (먼저 차단됨)
        assert targets[0].service_id == "review-api"
        assert targets[1].service_id == "recommend-api"

    def test_critical_service_not_in_shedding_targets(self):
        """critical 서비스는 shedding 대상에서 제외."""
        config = CircuitBreakerAdvancedConfig(
            services=[
                ServiceConfig(service_id="payment-api", criticality="critical", shed_priority=0),
            ]
        )

        # critical을 포함해도 shed_priority=0이면 제외
        targets = config.get_shedding_targets(["critical"])
        assert len(targets) == 0


# =============================================================================
# PanicThresholdConfig Tests
# =============================================================================


class TestPanicThresholdConfig:
    """PanicThresholdConfig 데이터 모델 테스트."""

    def test_default_config(self):
        """기본 Panic Threshold 설정."""
        config = PanicThresholdConfig()
        assert config.enabled is True
        assert config.threshold_percent == 70.0
        assert config.action == "freeze"

    def test_alert_only_action(self):
        """alert_only 액션."""
        config = PanicThresholdConfig(action="alert_only")
        assert config.action == "alert_only"

    def test_invalid_threshold_percent(self):
        """잘못된 threshold_percent는 에러 발생."""
        with pytest.raises(ValueError, match="threshold_percent must be between"):
            PanicThresholdConfig(threshold_percent=150.0)

    def test_invalid_action(self):
        """잘못된 action은 에러 발생."""
        with pytest.raises(ValueError, match="Invalid action"):
            PanicThresholdConfig(action="shutdown")


# =============================================================================
# FreezeModeState Tests
# =============================================================================


class TestFreezeModeState:
    """FreezeModeState 데이터 모델 테스트."""

    def test_default_inactive(self):
        """기본값은 비활성화."""
        state = FreezeModeState()
        assert state.active is False
        assert state.activated_at is None
        assert state.reason == ""
        assert state.activated_by == ""

    def test_active_state(self):
        """활성화 상태."""
        state = FreezeModeState(
            active=True,
            activated_at="2026-01-05T14:30:00Z",
            reason="LOCKDOWN 진입으로 인한 Freeze Mode 활성화",
            activated_by="system",
        )
        assert state.active is True
        assert state.activated_at == "2026-01-05T14:30:00Z"
        assert "LOCKDOWN" in state.reason

    def test_operator_activation(self):
        """운영자에 의한 활성화."""
        state = FreezeModeState(
            active=True, activated_at="2026-01-05T14:30:00Z", reason="긴급 점검", activated_by="operator:admin"
        )
        assert state.activated_by == "operator:admin"


# =============================================================================
# Core Config Integration Tests
# =============================================================================


class TestCoreConfigIntegration:
    """core/config.py 통합 테스트.

    NOTE: Pydantic v2 마이그레이션 후 API 변경됨.
    - circuit_breaker_advanced는 분리된 설정으로 관리
    - get_circuit_breaker_advanced_settings()로 접근
    - model_dump(), model_validate() 사용
    """

    def test_circuit_breaker_advanced_config_available(self):
        """CircuitBreakerAdvancedSettings 독립적으로 접근 가능."""
        settings = get_circuit_breaker_advanced_settings()
        assert settings is not None
        assert isinstance(settings, CoreCBAdvancedConfig)

    def test_default_values(self):
        """기본값 확인."""
        cb_advanced = get_circuit_breaker_advanced_settings()

        assert cb_advanced.enabled is True
        assert cb_advanced.load_shedding_enabled is True
        assert cb_advanced.adaptive_threshold_enabled is True
        assert cb_advanced.canary_recovery_enabled is True
        assert cb_advanced.blast_radius_integration is True
        assert cb_advanced.freeze_on_lockdown is True
        assert cb_advanced.panic_threshold_enabled is True

    def test_model_validate(self):
        """Pydantic v2 model_validate로 설정 로드."""
        config_dict = {
            "enabled": False,
            "panic_threshold_percent": 80.0,
        }
        config = CoreCBAdvancedConfig.model_validate(config_dict)

        assert config.enabled is False
        assert config.panic_threshold_percent == 80.0

    def test_model_dump(self):
        """Pydantic v2 model_dump로 설정 직렬화."""
        config = CoreCBAdvancedConfig()
        config_dict = config.model_dump()

        assert "enabled" in config_dict
        assert config_dict["enabled"] is True
        assert config_dict["panic_threshold_percent"] == 70.0

    def test_get_circuit_breaker_advanced_settings(self):
        """convenience getter 함수 테스트."""
        # 기본값 반환 확인
        settings = get_circuit_breaker_advanced_settings()
        assert settings.enabled is True
        assert settings.panic_threshold_percent == 70.0


# =============================================================================
# Design Decision Tests (문서 검증)
# =============================================================================


class TestDesignDecisions:
    """문서에 명시된 설계 결정 검증."""

    def test_delayed_strategy_not_supported(self):
        """Delayed 전략은 안티패턴으로 지원하지 않음."""
        with pytest.raises(ValueError):
            OpenStrategy(type="delayed")
        with pytest.raises(ValueError):
            RecoveryStrategy(type="delayed")

    def test_critical_cannot_be_shed(self):
        """critical 서비스는 Load Shedding 대상에 포함될 수 없음."""
        with pytest.raises(ValueError):
            SheddingLevel(
                error_rate=70.0,
                shed_criticality=["low", "critical"],  # critical 포함 시 에러
                traffic_limit=0.0,
            )

    def test_canary_stages_prevent_thundering_herd(self):
        """Canary 단계는 Thundering Herd 방지를 위해 점진적."""
        strategy = RecoveryStrategy()
        stages = strategy.canary_stages

        # 연속 단계 간 트래픽 비율 차이 확인
        for i in range(len(stages) - 1):
            current = stages[i].traffic_percent
            next_stage = stages[i + 1].traffic_percent
            ratio = next_stage / current if current > 0 else float("inf")
            # 5배 이상 급증하지 않도록 (10→50 대신 10→30→60 등)
            assert ratio <= 5, f"Stage {i} to {i+1} ratio is {ratio}, should be <= 5"

    def test_lockdown_freezes_all_automatic_changes(self):
        """LOCKDOWN에서 Adaptive Threshold는 무한대로 자동 OPEN 금지."""
        policy = AdaptiveThresholdPolicy()
        failure, window = policy.get_adjusted_threshold("LOCKDOWN")

        assert failure == float("inf")
        assert window == float("inf")

    def test_emergency_level_more_conservative(self):
        """위기 상황일수록 더 보수적 (더 높은 임계값)."""
        policy = AdaptiveThresholdPolicy()

        levels = ["NORMAL", "ELEVATED", "HIGH", "CRITICAL"]
        prev_failure = 0
        prev_window = 0

        for level in levels:
            failure, window = policy.get_adjusted_threshold(level)
            assert failure > prev_failure, f"{level} should be more conservative than previous"
            assert window > prev_window, f"{level} should have longer window than previous"
            prev_failure = failure
            prev_window = window
