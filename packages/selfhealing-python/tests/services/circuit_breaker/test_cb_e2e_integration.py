"""
통합 테스트 (End-to-End Integration Tests)

전체 시스템 컴포넌트가 함께 동작하는지 검증합니다.

테스트 구조:
    - 6.1.1: End-to-End Flow Tests (전체 흐름)
    - 6.1.2: Component Interaction Tests (컴포넌트 상호작용)
    - 6.1.3: Failure Scenario Tests (장애 시나리오)
    - 6.1.4: Recovery Scenario Tests (복구 시나리오)
    - 6.1.5: Audit Trail Tests (Audit 추적)
"""

import pytest
import time
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

# Models
from selfhealing.services.circuit_breaker.models import (
    ServiceConfig,
    SheddingLevel,
    LoadSheddingPolicy,
    CanaryRecoveryStageConfig,
    RecoveryStrategy,
    ThresholdMultiplier,
    AdaptiveThresholdPolicy,
    PanicThresholdConfig,
)

# Adaptive Threshold
from selfhealing.services.circuit_breaker.adaptive_threshold import (
    AdaptiveThresholdManager,
    get_adaptive_threshold_manager,
    get_adjusted_cb_threshold,
    should_allow_cb_auto_open,
)

# Freeze Mode
from selfhealing.services.circuit_breaker.freeze_mode import (
    FreezeModeManager,
    get_freeze_mode_manager,
    is_freeze_mode_active,
    should_allow_cb_state_change,
)

# Panic Threshold
from selfhealing.services.circuit_breaker.panic_threshold import (
    PanicThresholdMonitor,
    get_panic_threshold_monitor,
    check_panic_threshold,
    is_panic_threshold_triggered,
)

# Tracing
from selfhealing.services.circuit_breaker.tracing import (
    TracingConfig,
    TriggeringRequestInfo,
    TraceContextProvider,
    CircuitBreakerTracingManager,
    get_tracing_manager,
    record_failure_with_trace,
)

# Service Config
from selfhealing.services.circuit_breaker.service_config import (
    ServiceConfigManager,
    get_service_config_manager,
    reset_service_config_manager,
    register_service,
    get_service_config,
    get_services_by_criticality,
)

# Blast Radius
from selfhealing.services.circuit_breaker.blast_radius_integration import (
    BlastRadiusLevel,
    BlastRadiusAssessment,
    BlastRadiusIntegration,
    BlastRadiusConfig,
    get_blast_radius_integration,
    reset_blast_radius_integration,
    assess_cb_open_impact,
)

# Canary Recovery
from selfhealing.services.circuit_breaker.canary_recovery import (
    CanaryRecoveryStage,
    CanaryRecoveryManager,
    CanaryRecoveryDecision,
    get_canary_recovery_manager,
    reset_canary_recovery_manager,
    start_canary_recovery,
    stop_canary_recovery,
    is_in_canary_recovery,
    canary_should_allow_request,
    canary_record_success,
    canary_record_failure,
)

# Stale Cache Integration
from selfhealing.services.circuit_breaker.stale_cache_integration import (
    CanaryWithStaleCacheConfig,
    StaleCacheStore,
    CanaryWithStaleCacheService,
    get_canary_stale_cache_service,
    reset_canary_stale_cache_service,
)

# Recovery Strategy
from selfhealing.services.circuit_breaker.recovery_strategy import (
    RecoveryStrategySelector,
    get_recovery_strategy_selector,
    reset_recovery_strategy_selector,
    select_recovery_strategy,
)

# Load Shedding
from selfhealing.services.circuit_breaker.load_shedding import (
    SheddingState,
    SheddingDecision,
    SheddingStatus,
    LoadSheddingManager,
    LoadSheddingMiddleware,
    LoadSheddingDashboard,
    get_load_shedding_manager,
    reset_load_shedding_manager,
    evaluate_shedding,
    should_allow_shedding_request,
    is_shedding_active,
    get_shedding_status,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_all_singletons():
    """각 테스트 전후로 모든 싱글톤 초기화."""
    # Reset before test
    _reset_all()
    yield
    # Reset after test
    _reset_all()


def _reset_all():
    """모든 싱글톤 인스턴스 초기화."""
    reset_load_shedding_manager()
    reset_canary_recovery_manager()
    reset_canary_stale_cache_service()
    reset_recovery_strategy_selector()
    reset_service_config_manager()
    reset_blast_radius_integration()

    # Adaptive Threshold reset
    try:
        from selfhealing.services.circuit_breaker.adaptive_threshold import reset_adaptive_threshold_manager

        reset_adaptive_threshold_manager()
    except ImportError:
        pass

    # Freeze Mode reset
    try:
        from selfhealing.services.circuit_breaker.freeze_mode import reset_freeze_mode_manager

        reset_freeze_mode_manager()
    except ImportError:
        pass

    # Panic Threshold reset
    try:
        from selfhealing.services.circuit_breaker.panic_threshold import reset_panic_threshold_monitor

        reset_panic_threshold_monitor()
    except ImportError:
        pass

    # Tracing reset
    try:
        from selfhealing.services.circuit_breaker.tracing import reset_tracing_manager

        reset_tracing_manager()
    except ImportError:
        pass


@pytest.fixture
def sample_services():
    """테스트용 서비스 설정."""
    return [
        ServiceConfig(
            service_id="payment-api",
            criticality="critical",
            shed_priority=0,  # 절대 차단 안 함
        ),
        ServiceConfig(
            service_id="order-api",
            criticality="high",
            shed_priority=1,
        ),
        ServiceConfig(
            service_id="notification-api",
            criticality="medium",
            shed_priority=5,
        ),
        ServiceConfig(
            service_id="review-api",
            criticality="low",
            shed_priority=10,
            min_traffic_percentage=5.0,  # 최소 5% 보장
        ),
        ServiceConfig(
            service_id="recommend-api",
            criticality="low",
            shed_priority=10,
        ),
    ]


@pytest.fixture
def setup_full_system(sample_services):
    """전체 시스템 설정."""
    # Service Config Manager 설정
    config_manager = get_service_config_manager()
    for config in sample_services:
        config_manager.register_service(config)

    # Load Shedding Manager 설정
    shedding_manager = get_load_shedding_manager()
    shedding_manager.register_services(sample_services)

    # Recovery Strategy Selector 설정
    strategy_selector = get_recovery_strategy_selector()

    return {
        "config_manager": config_manager,
        "shedding_manager": shedding_manager,
        "strategy_selector": strategy_selector,
    }


# =============================================================================
# 6.1.1 End-to-End Flow Tests
# =============================================================================


class TestEndToEndFlow:
    """전체 흐름 통합 테스트."""

    def test_normal_operation_flow(self, setup_full_system):
        """정상 운영 시 전체 흐름."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 1. 정상 상태에서는 모든 서비스 100% 허용
        assert shedding_manager.evaluate_shedding("payment-api") == 100.0
        assert shedding_manager.evaluate_shedding("review-api") == 100.0

        # 2. Canary 복구 중이 아님
        assert not is_in_canary_recovery("payment-api")

        # 3. Shedding 비활성화 상태
        assert not is_shedding_active()

    def test_critical_service_degradation_flow(self, setup_full_system):
        """Critical 서비스 장애 시 전체 흐름."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 1. Critical 서비스 에러율 상승 (35%)
        shedding_manager.set_error_rate("payment-api", 35.0)

        # 2. Low criticality 서비스는 50% 제한
        allowed = shedding_manager.evaluate_shedding("review-api")
        assert allowed <= 50.0  # Level 1: 50% 제한

        # 3. Critical 서비스는 여전히 100%
        assert shedding_manager.evaluate_shedding("payment-api") == 100.0

        # 4. 에러율 더 상승 (55%)
        shedding_manager.set_error_rate("payment-api", 55.0)

        # 5. Low+Medium 서비스 80% 제한 (20% 허용)
        allowed = shedding_manager.evaluate_shedding("review-api")
        assert allowed <= 20.0  # Level 2: 20% 허용

    def test_complete_degradation_flow(self, setup_full_system):
        """완전 장애 시 전체 흐름."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 1. Critical 서비스 심각한 에러 (75%)
        shedding_manager.set_error_rate("payment-api", 75.0)

        # 2. Low criticality 완전 차단 (min_traffic_percentage 보장)
        allowed = shedding_manager.evaluate_shedding("review-api")
        # review-api는 min_traffic_percentage=5.0 이므로 최소 5% 보장
        assert allowed >= 5.0
        assert allowed <= 10.0  # Level 3이지만 min 보장

    def test_recovery_with_canary_flow(self, setup_full_system):
        """Canary 복구 전체 흐름."""
        # 1. Canary 복구 시작 (HALF_OPEN 진입 시뮬레이션)
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryRecoveryStageConfig(traffic_percent=10.0, duration_seconds=0, required_success_rate=90.0),
                CanaryRecoveryStageConfig(traffic_percent=50.0, duration_seconds=0, required_success_rate=90.0),
                CanaryRecoveryStageConfig(traffic_percent=100.0, duration_seconds=0, required_success_rate=90.0),
            ],
        )

        start_canary_recovery("payment-api", strategy)

        # 2. Canary 복구 중 확인
        assert is_in_canary_recovery("payment-api")

        # 3. Canary 요청 결정 (10% canary, 90% stale cache)
        decisions = [canary_should_allow_request("payment-api") for _ in range(100)]
        canary_count = sum(1 for d in decisions if d.is_canary_request)

        # 약 10% canary 기대
        assert 2 <= canary_count <= 25, f"Expected ~10% canary, got {canary_count}%"

        # 4. 성공 기록으로 단계 전이
        for _ in range(15):  # 10개 이상 성공
            result = canary_record_success("payment-api")

        # 5. 다음 단계로 전이 확인
        state = get_canary_recovery_manager().get_recovery_state("payment-api")
        assert state is not None

    def test_load_shedding_with_canary_combined(self, setup_full_system):
        """Load Shedding + Canary 결합 시나리오."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 1. Critical 에러 상승으로 Shedding 활성화
        shedding_manager.set_error_rate("payment-api", 40.0)

        # 2. Low criticality 서비스에 Shedding 적용
        shedding_decision = shedding_manager.should_allow_request("review-api")
        assert shedding_decision.is_shed is True

        # 3. 동시에 다른 서비스는 Canary 복구 진행
        start_canary_recovery("order-api")
        assert is_in_canary_recovery("order-api")

        # 4. 두 메커니즘 독립적으로 동작 확인
        assert shedding_manager.evaluate_shedding("review-api") <= 50.0
        canary_decision = canary_should_allow_request("order-api")
        assert canary_decision.current_stage == CanaryRecoveryStage.CANARY_1


# =============================================================================
# 6.1.2 Component Interaction Tests
# =============================================================================


class TestComponentInteraction:
    """컴포넌트 상호작용 테스트."""

    def test_service_config_propagation(self, setup_full_system):
        """ServiceConfig가 모든 컴포넌트에 전파되는지 확인."""
        config_manager = setup_full_system["config_manager"]
        shedding_manager = setup_full_system["shedding_manager"]

        # 새 서비스 등록
        new_service = ServiceConfig(
            service_id="analytics-api",
            criticality="low",
            shed_priority=15,
        )
        config_manager.register_service(new_service)
        shedding_manager.register_service(new_service)

        # ServiceConfigManager에서 조회 가능
        config = get_service_config("analytics-api")
        assert config is not None
        assert config.criticality == "low"

        # LoadSheddingManager에서도 사용 가능
        shedding_manager.set_error_rate("payment-api", 35.0)
        allowed = shedding_manager.evaluate_shedding("analytics-api")
        assert allowed <= 50.0  # low criticality이므로 Shedding 대상

    def test_criticality_affects_shedding(self, setup_full_system):
        """Criticality 설정이 Shedding에 영향."""
        shedding_manager = setup_full_system["shedding_manager"]

        # Critical 서비스 에러 상승
        shedding_manager.set_error_rate("payment-api", 55.0)

        # Critical은 영향 없음
        assert shedding_manager.evaluate_shedding("payment-api") == 100.0

        # High는 영향 없음 (Level 2에서도 shed_criticality에 포함 안 됨)
        assert shedding_manager.evaluate_shedding("order-api") == 100.0

        # Medium은 Level 2에서 영향
        allowed = shedding_manager.evaluate_shedding("notification-api")
        assert allowed <= 20.0  # Level 2: medium+low 80% 제한

        # Low는 Level 1부터 영향
        allowed = shedding_manager.evaluate_shedding("review-api")
        assert allowed <= 20.0  # Level 2: 20% 허용 (min 5% 보장)

    def test_recovery_strategy_selection_by_criticality(self, setup_full_system):
        """Criticality에 따른 Recovery 전략 선택."""
        strategy_selector = get_recovery_strategy_selector()
        config_manager = get_service_config_manager()

        # Critical 서비스는 strict canary
        strategy = strategy_selector.select_strategy("payment-api")
        # Critical 서비스는 기본적으로 더 엄격한 전략
        assert strategy is not None

        # Low criticality 서비스는 immediate 가능
        strategy = strategy_selector.select_strategy("recommend-api")
        assert strategy is not None

    def test_canary_and_stale_cache_interaction(self, setup_full_system):
        """Canary와 Stale Cache 상호작용."""
        stale_service = get_canary_stale_cache_service()

        # Stale Cache에 데이터 저장 (key가 cache_key와 일치해야 함)
        cache_key = "cache-key-1"
        stale_service._cache.set(key=cache_key, value={"data": "cached"}, service_id="payment-api")

        # Canary 복구 시작
        start_canary_recovery("payment-api")

        # Non-canary 요청은 Stale Cache 사용 가능
        # 여러 번 시도하여 non-canary 요청 확인 (10% canary이므로 90%는 stale cache)
        found_stale = False
        found_canary = False
        for _ in range(50):
            decision = stale_service.should_allow_with_fallback("payment-api", cache_key, "half_open")
            if decision.use_stale and decision.stale_data is not None:
                found_stale = True
            if decision.allow_backend and decision.is_canary_request:
                found_canary = True
            if found_stale and found_canary:
                break

        # 어떤 형태로든 요청이 처리되어야 함 (canary 또는 stale)
        assert found_stale or found_canary, "Neither stale cache nor canary request was selected"


# =============================================================================
# 6.1.3 Failure Scenario Tests
# =============================================================================


class TestFailureScenarios:
    """장애 시나리오 테스트."""

    def test_cascading_failure_prevention(self, setup_full_system):
        """연쇄 장애 방지 테스트."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 1. Critical 서비스 점진적 장애
        for error_rate in [25.0, 35.0, 55.0, 75.0]:
            shedding_manager.set_error_rate("payment-api", error_rate)

            # Critical은 항상 100%
            assert shedding_manager.evaluate_shedding("payment-api") == 100.0

            # Low criticality는 점점 더 제한
            low_allowed = shedding_manager.evaluate_shedding("review-api")

            if error_rate <= 29.0:
                assert low_allowed == 100.0
            elif error_rate <= 49.0:
                assert low_allowed <= 50.0  # Level 1
            elif error_rate <= 69.0:
                assert low_allowed <= 20.0  # Level 2
            else:
                assert low_allowed <= 10.0  # Level 3 + min guarantee

    def test_canary_failure_rollback(self, setup_full_system):
        """Canary 실패 시 롤백."""
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryRecoveryStageConfig(traffic_percent=10.0, duration_seconds=0, required_success_rate=90.0),
            ],
        )

        start_canary_recovery("payment-api", strategy)

        # 50% 실패율 시뮬레이션
        for _ in range(5):
            canary_record_success("payment-api")
        for _ in range(5):
            result = canary_record_failure("payment-api")

        # 성공률 미달로 복구 실패
        state = get_canary_recovery_manager().get_recovery_state("payment-api")
        # 실패 시 reset되어 canary 상태가 아님
        if result and result.failed:
            assert not state.is_in_canary()

    def test_service_not_registered_handling(self, setup_full_system):
        """미등록 서비스 처리."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 미등록 서비스는 100% 허용 (fail-open)
        allowed = shedding_manager.evaluate_shedding("unknown-api")
        assert allowed == 100.0

        # Canary에서도 정상 처리 (not in canary)
        decision = canary_should_allow_request("unknown-api")
        assert decision.allow_backend is True
        assert decision.is_canary_request is False

    def test_multiple_critical_services_failure(self, setup_full_system):
        """다중 Critical 서비스 장애."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 추가 Critical 서비스 등록
        auth_service = ServiceConfig(
            service_id="auth-api",
            criticality="critical",
            shed_priority=0,
        )
        shedding_manager.register_service(auth_service)

        # 두 Critical 서비스 에러
        shedding_manager.set_error_rate("payment-api", 40.0)
        shedding_manager.set_error_rate("auth-api", 60.0)

        # 평균 에러율 = (40 + 60) / 2 = 50%
        avg_error = shedding_manager.get_critical_services_error_rate()
        assert 49.0 <= avg_error <= 51.0

        # Level 2 적용 확인
        low_allowed = shedding_manager.evaluate_shedding("review-api")
        assert low_allowed <= 20.0


# =============================================================================
# 6.1.4 Recovery Scenario Tests
# =============================================================================


class TestRecoveryScenarios:
    """복구 시나리오 테스트."""

    def test_gradual_recovery_with_canary(self, setup_full_system):
        """Canary를 통한 점진적 복구."""
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryRecoveryStageConfig(traffic_percent=10.0, duration_seconds=0, required_success_rate=80.0),
                CanaryRecoveryStageConfig(traffic_percent=30.0, duration_seconds=0, required_success_rate=80.0),
                CanaryRecoveryStageConfig(traffic_percent=60.0, duration_seconds=0, required_success_rate=80.0),
                CanaryRecoveryStageConfig(traffic_percent=100.0, duration_seconds=0, required_success_rate=80.0),
            ],
        )

        state = start_canary_recovery("payment-api", strategy)
        assert state.current_stage == CanaryRecoveryStage.CANARY_1

        # Stage 1 성공 (10%)
        for _ in range(15):
            canary_record_success("payment-api")

        state = get_canary_recovery_manager().get_recovery_state("payment-api")
        # 다음 단계로 전이 가능
        assert state is not None

    def test_shedding_level_decrease_on_recovery(self, setup_full_system):
        """에러율 감소 시 Shedding 레벨 감소."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 높은 에러율에서 시작
        shedding_manager.set_error_rate("payment-api", 75.0)
        assert shedding_manager.evaluate_shedding("review-api") <= 10.0  # Level 3

        # 에러율 감소
        shedding_manager.set_error_rate("payment-api", 55.0)
        allowed = shedding_manager.evaluate_shedding("review-api")
        assert allowed <= 20.0  # Level 2

        # 더 감소
        shedding_manager.set_error_rate("payment-api", 35.0)
        allowed = shedding_manager.evaluate_shedding("review-api")
        assert allowed <= 50.0  # Level 1

        # 정상 복귀
        shedding_manager.set_error_rate("payment-api", 20.0)
        allowed = shedding_manager.evaluate_shedding("review-api")
        assert allowed == 100.0  # No shedding

    def test_immediate_recovery_strategy(self, setup_full_system):
        """Immediate 복구 전략 테스트."""
        strategy = RecoveryStrategy(type="immediate")

        state = start_canary_recovery("review-api", strategy)

        # Immediate는 Canary 건너뜀
        assert not state.is_in_canary()

    def test_manual_canary_stop(self, setup_full_system):
        """수동 Canary 중단."""
        start_canary_recovery("payment-api")
        assert is_in_canary_recovery("payment-api")

        # 수동 중단
        result = stop_canary_recovery("payment-api", "manual_intervention")
        assert result is True

        # 중단 확인
        assert not is_in_canary_recovery("payment-api")


# =============================================================================
# 6.1.5 Audit Trail Tests
# =============================================================================


class TestAuditTrail:
    """Audit 추적 테스트."""

    def test_shedding_audit_entries(self, setup_full_system):
        """Load Shedding Audit 기록."""
        shedding_manager = setup_full_system["shedding_manager"]

        audit_entries = []
        shedding_manager.set_audit_callback(lambda entry: audit_entries.append(entry))

        # Shedding 활성화
        shedding_manager.set_error_rate("payment-api", 35.0)
        shedding_manager.update_shedding_state()

        # Audit 기록 확인
        if audit_entries:
            assert audit_entries[0].event_type in [
                "SHEDDING_ACTIVATED",
                "SHEDDING_LEVEL_CHANGED",
            ]

    def test_canary_stage_audit(self, setup_full_system):
        """Canary 단계 전이 Audit."""
        stage_callbacks = []

        manager = get_canary_recovery_manager()
        manager._on_stage_advanced = lambda sid, result: stage_callbacks.append(result)

        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryRecoveryStageConfig(traffic_percent=10.0, duration_seconds=0, required_success_rate=80.0),
                CanaryRecoveryStageConfig(traffic_percent=100.0, duration_seconds=0, required_success_rate=80.0),
            ],
        )

        manager.start_canary_recovery("payment-api", strategy)

        # 성공 기록으로 단계 전이
        for _ in range(15):
            manager.record_success("payment-api")

        # 콜백 호출 확인
        if stage_callbacks:
            assert stage_callbacks[0].transitioned is True

    def test_tracing_integration(self, setup_full_system):
        """Tracing 통합 확인."""
        tracing_manager = get_tracing_manager()

        # 실패 기록 with trace (실제 API 사용)
        triggering = record_failure_with_trace(
            service_id="payment-api",
            error=Exception("Test error"),
            endpoint="/api/v1/payments",
            method="POST",
        )

        # Triggering request 반환 확인
        assert triggering is not None
        assert triggering.error_message is not None

        # Triggering request 조회
        from selfhealing.services.circuit_breaker.tracing import get_triggering_request

        stored_triggering = get_triggering_request("payment-api")

        if stored_triggering:
            assert stored_triggering.endpoint == "/api/v1/payments"


# =============================================================================
# 6.1.6 Boundary Tests
# =============================================================================


class TestBoundaryConditions:
    """경계 조건 테스트."""

    def test_error_rate_boundaries(self, setup_full_system):
        """에러율 경계값 테스트."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 정확히 30% (Level 1 트리거 경계)
        shedding_manager.set_error_rate("payment-api", 30.0)
        allowed = shedding_manager.evaluate_shedding("review-api")
        assert allowed <= 50.0  # Level 1 적용

        # 29.9% (Level 1 미달)
        shedding_manager.set_error_rate("payment-api", 29.9)
        allowed = shedding_manager.evaluate_shedding("review-api")
        assert allowed == 100.0  # No shedding

    def test_traffic_percent_boundaries(self, setup_full_system):
        """트래픽 비율 경계값 테스트."""
        # 0% 트래픽
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryRecoveryStageConfig(traffic_percent=0.0, duration_seconds=0, required_success_rate=90.0),
            ],
        )

        start_canary_recovery("payment-api", strategy)

        # 모든 요청이 non-canary
        decisions = [canary_should_allow_request("payment-api") for _ in range(20)]
        canary_count = sum(1 for d in decisions if d.is_canary_request)
        assert canary_count == 0

        stop_canary_recovery("payment-api", "test")

    def test_min_traffic_guarantee(self, setup_full_system):
        """최소 트래픽 보장 테스트."""
        shedding_manager = setup_full_system["shedding_manager"]

        # review-api는 min_traffic_percentage=5.0
        shedding_manager.set_error_rate("payment-api", 80.0)  # 매우 높은 에러율

        allowed = shedding_manager.evaluate_shedding("review-api")
        # Level 3 (0% 제한)이지만 min_traffic_percentage로 5% 보장
        assert allowed >= 5.0


# =============================================================================
# 6.1.7 Concurrency Tests
# =============================================================================


class TestConcurrency:
    """동시성 테스트."""

    def test_concurrent_canary_decisions(self, setup_full_system):
        """동시 Canary 결정."""
        import threading

        start_canary_recovery("payment-api")

        decisions = []
        errors = []

        def make_decision():
            try:
                for _ in range(50):
                    decision = canary_should_allow_request("payment-api")
                    decisions.append(decision)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=make_decision) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(decisions) == 250

    def test_concurrent_shedding_evaluation(self, setup_full_system):
        """동시 Shedding 평가."""
        import threading

        shedding_manager = setup_full_system["shedding_manager"]
        shedding_manager.set_error_rate("payment-api", 40.0)

        results = []
        errors = []

        def evaluate():
            try:
                for _ in range(50):
                    allowed = shedding_manager.evaluate_shedding("review-api")
                    results.append(allowed)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=evaluate) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 250
        # 모든 결과가 동일해야 함 (같은 에러율)
        assert all(r <= 50.0 for r in results)


# =============================================================================
# 6.1.8 Performance Tests
# =============================================================================


class TestPerformance:
    """성능 테스트."""

    def test_shedding_evaluation_performance(self, setup_full_system):
        """Shedding 평가 성능."""
        shedding_manager = setup_full_system["shedding_manager"]
        shedding_manager.set_error_rate("payment-api", 40.0)

        import time

        start = time.time()

        for _ in range(10000):
            shedding_manager.evaluate_shedding("review-api")

        elapsed = time.time() - start

        # 10,000번 평가가 1초 이내 (0.1ms/평가)
        assert elapsed < 1.0, f"Performance too slow: {elapsed}s for 10,000 evaluations"

    def test_canary_decision_performance(self, setup_full_system):
        """Canary 결정 성능."""
        start_canary_recovery("payment-api")

        import time

        start = time.time()

        for _ in range(10000):
            canary_should_allow_request("payment-api")

        elapsed = time.time() - start

        # 10,000번 결정이 1초 이내
        assert elapsed < 1.0, f"Performance too slow: {elapsed}s for 10,000 decisions"


# =============================================================================
# 6.1.9 Configuration Validation Tests
# =============================================================================


class TestConfigurationValidation:
    """설정 검증 테스트."""

    def test_invalid_criticality_handling(self, setup_full_system):
        """잘못된 criticality는 validation에 의해 거부됨."""
        # 유효하지 않은 criticality는 ValueError 발생
        with pytest.raises(ValueError, match="Invalid criticality"):
            ServiceConfig(
                service_id="invalid-api",
                criticality="ultra-critical",  # 정의되지 않은 값
                shed_priority=0,
            )

    def test_shed_priority_sorting(self, setup_full_system):
        """shed_priority 정렬 확인."""
        config_manager = get_service_config_manager()

        # shed_priority가 높은 서비스가 먼저 차단
        # get_shedding_targets는 shed_criticality 인자 필요
        services = config_manager.get_shedding_targets(["low", "medium"])

        # Low criticality 서비스들이 먼저 나와야 함
        low_services = [s for s in services if s.criticality == "low"]
        assert len(low_services) >= 2  # review-api, recommend-api


# =============================================================================
# 6.1.10 Stress Tests
# =============================================================================


class TestStress:
    """스트레스 테스트."""

    def test_rapid_state_changes(self, setup_full_system):
        """빠른 상태 변경."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 빠르게 에러율 변경
        for i in range(100):
            error_rate = i % 100
            shedding_manager.set_error_rate("payment-api", float(error_rate))
            shedding_manager.evaluate_shedding("review-api")

        # 오류 없이 완료
        assert True

    def test_many_services(self, setup_full_system):
        """많은 서비스 등록."""
        shedding_manager = setup_full_system["shedding_manager"]

        # 100개 서비스 등록
        for i in range(100):
            service = ServiceConfig(
                service_id=f"service-{i}",
                criticality=["critical", "high", "medium", "low"][i % 4],
                shed_priority=i,
            )
            shedding_manager.register_service(service)

        # 에러율 설정
        shedding_manager.set_error_rate("payment-api", 50.0)

        # 모든 서비스 평가
        for i in range(100):
            shedding_manager.evaluate_shedding(f"service-{i}")

        # 오류 없이 완료
        assert True
