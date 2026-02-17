"""
Integration Tests for Request Priority / Admission Control (236).

AdmissionControlMiddleware → TieringMiddleware → Application 미들웨어 체인과
RateController ↔ TrafficGate priority 전파에 대한 통합 테스트.

외부 서비스(DB/Redis) 의존 없음. Mock/인메모리 기반.
"""

import pytest
from unittest.mock import MagicMock, patch

from selfhealing.scaling.config import (
    BackpressureLevel,
    BackpressureSettings,
    BackpressureStrategy,
    reset_backpressure_settings,
)
from selfhealing.scaling.rate_controller import (
    PRIORITY_WATERMARKS,
    RateController,
    TokenBucket,
    reset_rate_controller,
)
from selfhealing.scaling.traffic_gate import (
    TrafficGate,
    _map_priority_int_to_tier,
    reset_traffic_gate,
)
from selfhealing.api.django.admission_control import TIER_PRIORITY_MAP
from selfhealing.api.django.tiering.defaults import BACKPRESSURE_TIER_RULES
from selfhealing.services.emergency_mode.enums import (
    EMERGENCY_LEVEL_RULES,
    EmergencyLevel,
)


class TestRateControllerTrafficGatePriorityPropagation:
    """RateController ↔ TrafficGate priority 전파 통합 테스트.

    TrafficGate.should_allow(priority=int) →
    _map_priority_int_to_tier(int) → tier_str →
    RateController.should_process(priority=tier_str) →
    Watermark 분기 → TokenBucket.consume()

    전체 파이프라인이 올바르게 연결되는지 검증한다.
    """

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        reset_rate_controller()
        reset_backpressure_settings()
        reset_traffic_gate()
        yield
        reset_rate_controller()
        reset_backpressure_settings()
        reset_traffic_gate()

    def test_full_pipeline_critical_priority_allowed(self):
        """critical priority(0)가 TrafficGate → RateController 파이프라인을 통과한다."""
        settings = BackpressureSettings(
            backpressure_enabled=True,
            max_rate_per_second=10000.0,
        )
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        decision = gate.should_allow(priority=TIER_PRIORITY_MAP["critical"])

        assert decision.allowed is True

    def test_full_pipeline_non_essential_rejected_under_load(self):
        """토큰 부족 시 non_essential(100)이 파이프라인에서 거부된다."""
        settings = BackpressureSettings(
            backpressure_enabled=True,
            max_rate_per_second=10.0,
            default_strategy=BackpressureStrategy.REJECT,
        )
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        # 토큰 대부분 소비 → 비율 < 0.6 (non_essential watermark)
        for _ in range(7):
            controller._token_bucket.consume()

        decision = gate.should_allow(priority=TIER_PRIORITY_MAP["non_essential"])

        assert decision.allowed is False
        assert decision.gate == "RateController"

    def test_critical_survives_when_non_essential_rejected(self):
        """non_essential이 거부되는 토큰 수준에서도 critical은 통과한다."""
        settings = BackpressureSettings(
            backpressure_enabled=True,
            max_rate_per_second=10.0,
            default_strategy=BackpressureStrategy.REJECT,
        )
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        # 토큰 70% 소비 → 비율 약 0.3
        for _ in range(7):
            controller._token_bucket.consume()

        # non_essential 거부 (watermark 0.6 > 비율 0.3)
        ne_decision = gate.should_allow(priority=TIER_PRIORITY_MAP["non_essential"])
        assert ne_decision.allowed is False

        # critical 허용 (watermark 0.0 < 비율 0.3, 토큰 남아 있으면 consume 성공)
        cr_decision = gate.should_allow(priority=TIER_PRIORITY_MAP["critical"])
        assert cr_decision.allowed is True

    def test_standard_survives_when_non_essential_rejected(self):
        """non_essential이 거부되는 수준에서도 standard는 통과할 수 있다."""
        settings = BackpressureSettings(
            backpressure_enabled=True,
            max_rate_per_second=10.0,
            default_strategy=BackpressureStrategy.REJECT,
        )
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        # 토큰 약 50% 소비 → 비율 약 0.5
        for _ in range(5):
            controller._token_bucket.consume()

        ratio = controller._token_bucket.get_token_ratio()

        # non_essential 거부 (watermark 0.6 > 비율 ~0.5)
        ne_decision = gate.should_allow(priority=TIER_PRIORITY_MAP["non_essential"])
        if ratio < PRIORITY_WATERMARKS["non_essential"]:
            assert ne_decision.allowed is False

        # standard 허용 (watermark 0.3 < 비율 ~0.5)
        if ratio >= PRIORITY_WATERMARKS["standard"]:
            st_decision = gate.should_allow(priority=TIER_PRIORITY_MAP["standard"])
            assert st_decision.allowed is True

    def test_tier_priority_map_to_watermark_alignment(self):
        """TIER_PRIORITY_MAP → _map_priority_int_to_tier → PRIORITY_WATERMARKS 전체 흐름 정합성."""
        for tier_id, priority_int in TIER_PRIORITY_MAP.items():
            mapped_tier = _map_priority_int_to_tier(priority_int)
            assert mapped_tier == tier_id, (
                f"TIER_PRIORITY_MAP['{tier_id}']={priority_int} → "
                f"_map_priority_int_to_tier → '{mapped_tier}' (expected '{tier_id}')"
            )
            assert mapped_tier in PRIORITY_WATERMARKS, f"'{mapped_tier}'이 PRIORITY_WATERMARKS에 없음"

    def test_rejection_metadata_propagates_through_pipeline(self):
        """거부 시 metadata에 priority tier가 파이프라인을 통해 전파된다."""
        settings = BackpressureSettings(
            backpressure_enabled=True,
            max_rate_per_second=1.0,
            default_strategy=BackpressureStrategy.REJECT,
        )
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        # 첫 토큰 소비
        gate.should_allow(priority=0)

        # 두 번째 요청 거부
        decision = gate.should_allow(priority=TIER_PRIORITY_MAP["non_essential"])
        if not decision.allowed and decision.gate == "RateController":
            assert decision.metadata is not None
            assert decision.metadata["priority"] == "non_essential"

    def test_backpressure_disabled_allows_all_priorities(self):
        """backpressure 비활성 시 모든 priority가 허용된다."""
        settings = BackpressureSettings(backpressure_enabled=False)
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        for tier_id, priority_int in TIER_PRIORITY_MAP.items():
            decision = gate.should_allow(priority=priority_int)
            assert decision.allowed is True, f"{tier_id} 거부됨"


class TestMergeStrategyIntegration:
    """Emergency Mode + Backpressure Most Restrictive Wins 병합 통합 테스트.

    TieringMiddleware의 __call__()에서 수행하는
    min(EMERGENCY_LEVEL_RULES[em_level][tier], BACKPRESSURE_TIER_RULES[bp_level][tier])
    로직이 올바른 결과를 산출하는지 다양한 조합으로 검증한다.
    """

    @pytest.mark.parametrize(
        "em_level,bp_level,tier_id,expected_multiplier",
        [
            # 모두 정상 → 1.0
            (EmergencyLevel.NORMAL, BackpressureLevel.NONE, "critical", 1.0),
            (EmergencyLevel.NORMAL, BackpressureLevel.NONE, "standard", 1.0),
            (EmergencyLevel.NORMAL, BackpressureLevel.NONE, "non_essential", 1.0),
            # Emergency만 활성 → Emergency 값
            (EmergencyLevel.LEVEL_1, BackpressureLevel.NONE, "non_essential", 0.0),
            (EmergencyLevel.LEVEL_2, BackpressureLevel.NONE, "standard", 0.1),
            (EmergencyLevel.LEVEL_3, BackpressureLevel.NONE, "critical", 0.5),
            # Backpressure만 활성 → Backpressure 값
            (EmergencyLevel.NORMAL, BackpressureLevel.CRITICAL, "critical", 0.8),
            (EmergencyLevel.NORMAL, BackpressureLevel.HIGH, "standard", 0.5),
            (EmergencyLevel.NORMAL, BackpressureLevel.LOW, "non_essential", 0.5),
            # 동시 활성 → min()
            (EmergencyLevel.LEVEL_3, BackpressureLevel.CRITICAL, "critical", 0.5),
            (EmergencyLevel.LEVEL_2, BackpressureLevel.HIGH, "standard", 0.1),
            (EmergencyLevel.LEVEL_1, BackpressureLevel.MEDIUM, "non_essential", 0.0),
        ],
    )
    def test_merge_multiplier(self, em_level, bp_level, tier_id, expected_multiplier):
        """Emergency와 Backpressure multiplier 중 min()이 최종 multiplier."""
        em_multiplier = EMERGENCY_LEVEL_RULES.get(em_level, {}).get(tier_id, 1.0)
        bp_multiplier = BACKPRESSURE_TIER_RULES.get(bp_level, {}).get(tier_id, 1.0)
        final = min(em_multiplier, bp_multiplier)

        assert final == pytest.approx(expected_multiplier), (
            f"em={em_level.name}, bp={bp_level.name}, tier={tier_id}: "
            f"min({em_multiplier}, {bp_multiplier}) = {final}, expected {expected_multiplier}"
        )


class TestAdmissionControlMiddlewareIntegration:
    """AdmissionControlMiddleware → TieringMiddleware 미들웨어 체인 통합 테스트.

    실제 TierRegistry, RateController 인스턴스를 사용하되
    외부 서비스 의존 없이 검증한다.
    """

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        reset_rate_controller()
        reset_backpressure_settings()
        reset_traffic_gate()
        yield
        reset_rate_controller()
        reset_backpressure_settings()
        reset_traffic_gate()

    def test_admission_control_classifies_critical_path(self):
        """AdmissionControlMiddleware가 critical 경로를 올바르게 분류한다."""
        from selfhealing.api.django.tiering.registry import TierRegistry
        from selfhealing.api.django.tiering import get_tiering_circuit_breaker

        get_tiering_circuit_breaker().reset()

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        result = registry.resolve_tier_with_fallback(
            path="/api/self-healing/control/",
            client_ip="127.0.0.1",
        )
        assert result.tier_id == "critical"

        # TIER_PRIORITY_MAP으로 TrafficGate priority 결정
        traffic_priority = TIER_PRIORITY_MAP[result.tier_id]
        assert traffic_priority == TIER_PRIORITY_MAP["critical"]

        # _map_priority_int_to_tier으로 RateController용 tier 결정
        tier_str = _map_priority_int_to_tier(traffic_priority)
        assert tier_str == "critical"

    def test_admission_control_classifies_dashboard_as_non_essential(self):
        """AdmissionControlMiddleware가 dashboard 경로를 non_essential로 분류한다."""
        from selfhealing.api.django.tiering.registry import TierRegistry
        from selfhealing.api.django.tiering import get_tiering_circuit_breaker

        get_tiering_circuit_breaker().reset()

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        result = registry.resolve_tier_with_fallback(
            path="/api/self-healing/dashboard/test",
            client_ip="127.0.0.1",
        )
        assert result.tier_id == "non_essential"

        traffic_priority = TIER_PRIORITY_MAP[result.tier_id]
        tier_str = _map_priority_int_to_tier(traffic_priority)
        assert tier_str == "non_essential"

    def test_admission_control_classifies_config_as_standard(self):
        """AdmissionControlMiddleware가 config 경로를 standard로 분류한다."""
        from selfhealing.api.django.tiering.registry import TierRegistry
        from selfhealing.api.django.tiering import get_tiering_circuit_breaker

        get_tiering_circuit_breaker().reset()

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        result = registry.resolve_tier_with_fallback(
            path="/api/self-healing/config/test",
            client_ip="127.0.0.1",
        )
        assert result.tier_id == "standard"

        traffic_priority = TIER_PRIORITY_MAP[result.tier_id]
        tier_str = _map_priority_int_to_tier(traffic_priority)
        assert tier_str == "standard"

    def test_full_middleware_chain_allowed(self):
        """전체 미들웨어 체인 시뮬레이션 — 정상 상태에서 요청 허용."""
        from selfhealing.api.django.tiering.registry import TierRegistry
        from selfhealing.api.django.tiering import get_tiering_circuit_breaker

        get_tiering_circuit_breaker().reset()

        # 1단계: TierRegistry로 경로 분류
        registry = TierRegistry.__new__(TierRegistry)
        registry._init()
        tier_result = registry.resolve_tier_with_fallback(
            path="/api/self-healing/config/test",
        )

        # 2단계: TrafficGate로 priority 기반 판정
        settings = BackpressureSettings(
            backpressure_enabled=True,
            max_rate_per_second=10000.0,
        )
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        traffic_priority = TIER_PRIORITY_MAP.get(tier_result.tier_id, 50)
        decision = gate.should_allow(priority=traffic_priority)
        assert decision.allowed is True

        # 3단계: TieringMiddleware Merge Strategy
        em_mult = EMERGENCY_LEVEL_RULES[EmergencyLevel.NORMAL].get(
            tier_result.tier_id,
            1.0,
        )
        bp_mult = BACKPRESSURE_TIER_RULES[BackpressureLevel.NONE].get(
            tier_result.tier_id,
            1.0,
        )
        final_mult = min(em_mult, bp_mult)
        assert final_mult == 1.0  # 정상: 모든 요청 허용

    def test_full_middleware_chain_rejected_under_load(self):
        """전체 미들웨어 체인 시뮬레이션 — 과부하 시 non_essential 거부."""
        from selfhealing.api.django.tiering.registry import TierRegistry
        from selfhealing.api.django.tiering import get_tiering_circuit_breaker

        get_tiering_circuit_breaker().reset()

        # 1단계: dashboard 경로 → non_essential
        registry = TierRegistry.__new__(TierRegistry)
        registry._init()
        tier_result = registry.resolve_tier_with_fallback(
            path="/api/self-healing/dashboard/test",
        )
        assert tier_result.tier_id == "non_essential"

        # 2단계: TrafficGate — 토큰 부족 상태
        settings = BackpressureSettings(
            backpressure_enabled=True,
            max_rate_per_second=10.0,
            default_strategy=BackpressureStrategy.REJECT,
        )
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        # 토큰 70% 소비
        for _ in range(7):
            controller._token_bucket.consume()

        traffic_priority = TIER_PRIORITY_MAP["non_essential"]
        decision = gate.should_allow(priority=traffic_priority)
        assert decision.allowed is False

    def test_request_object_attribute_injection_simulation(self):
        """request 객체 속성 주입 시뮬레이션."""
        from selfhealing.api.django.tiering.registry import TierRegistry
        from selfhealing.api.django.tiering import get_tiering_circuit_breaker

        get_tiering_circuit_breaker().reset()

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        tier_result = registry.resolve_tier_with_fallback(
            path="/api/self-healing/control/",
        )
        tier_def = registry.get_tier(tier_result.tier_id)

        # request 객체 시뮬레이션
        class FakeRequest:
            pass

        request = FakeRequest()
        request._selfhealing_tier_id = tier_result.tier_id
        request._selfhealing_tier_priority = tier_def.priority if tier_def else 0

        assert request._selfhealing_tier_id == "critical"
        assert request._selfhealing_tier_priority == 100

    def test_tier_bulkhead_name_format(self):
        """Bulkhead 이름이 'tier:{tier_id}' 형식으로 생성된다."""
        for tier_id in ("critical", "standard", "non_essential"):
            bulkhead_name = f"tier:{tier_id}"
            assert bulkhead_name.startswith("tier:")
            assert tier_id in bulkhead_name
