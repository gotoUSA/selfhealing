"""
DomainPropagationMultiplier 단위 테스트.

의존성 그래프 기반 가중치 감쇠 전파 테스트.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.3
"""

from __future__ import annotations

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.error_budget.propagation import (
    DomainPropagationMultiplier,
    PropagationConfig,
    PropagationResult,
    configure_domain_propagation,
    get_domain_propagation_multiplier,
    reset_domain_propagation,
)

# =============================================================================
# PropagationConfig 테스트
# =============================================================================

class TestPropagationConfig:
    """PropagationConfig 테스트."""

    def test_default_decay_per_hop(self):
        """기본 홉당 감쇠율."""
        config = PropagationConfig()
        assert config.decay_per_hop == 0.5

    def test_default_max_hops(self):
        """기본 최대 홉 수."""
        config = PropagationConfig()
        assert config.max_hops == 3

    def test_default_min_multiplier(self):
        """기본 최소 가중치."""
        config = PropagationConfig()
        assert config.min_multiplier == 1.0

    def test_custom_config(self):
        """커스텀 설정."""
        config = PropagationConfig(
            base_multiplier=10.0,
            decay_per_hop=0.7,
            max_hops=5,
        )

        assert config.base_multiplier == 10.0
        assert config.decay_per_hop == 0.7
        assert config.max_hops == 5


# =============================================================================
# PropagationResult 테스트
# =============================================================================

class TestPropagationResult:
    """PropagationResult 테스트."""

    def test_to_dict(self):
        """딕셔너리 변환."""
        result = PropagationResult(
            crisis_domain="payment",
            error_domain="order",
            hop_distance=1,
            multiplier=2.5,
            path=["payment", "order"],
        )

        data = result.to_dict()

        assert data["crisis_domain"] == "payment"
        assert data["error_domain"] == "order"
        assert data["hop_distance"] == 1
        assert data["multiplier"] == 2.5
        assert data["path"] == ["payment", "order"]


# =============================================================================
# DomainPropagationMultiplier 홉 거리 테스트
# =============================================================================

class TestDomainPropagationHopDistance:
    """홉 거리 계산 테스트."""

    def test_same_domain_zero_hop(self):
        """동일 도메인: 0-hop."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={"order": ["payment"]}
        )

        hop = propagator.get_hop_distance("payment", "payment")
        assert hop == 0

    def test_direct_dependency_one_hop(self):
        """직접 의존: 1-hop."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={"order": ["payment"]}
        )

        # order가 payment에 의존 → payment 장애 시 order는 1-hop
        hop = propagator.get_hop_distance("payment", "order")
        assert hop == 1

    def test_two_hop_dependency(self):
        """2-hop 의존."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={
                "order": ["payment"],
                "cart": ["order"],
            }
        )

        # payment → order → cart (2-hop)
        hop = propagator.get_hop_distance("payment", "cart")
        assert hop == 2

    def test_no_dependency_returns_negative(self):
        """의존 없음: -1 반환."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={"order": ["payment"]}
        )

        hop = propagator.get_hop_distance("payment", "analytics")
        assert hop == -1

    def test_max_hops_limit(self):
        """최대 홉 제한."""
        propagator = DomainPropagationMultiplier(
            config=PropagationConfig(max_hops=2),
            dependency_graph={
                "b": ["a"],
                "c": ["b"],
                "d": ["c"],  # 3-hop
            }
        )

        # a → b → c → d (3-hop) 이지만 max_hops=2로 제한
        hop = propagator.get_hop_distance("a", "d")
        assert hop == -1  # 제한 초과

    def test_cycle_prevention(self):
        """순환 참조 방지 (무한 루프 없이 완료)."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={
                "a": ["b"],
                "b": ["c"],
                "c": ["a"],  # 순환
            }
        )

        # 무한 루프 없이 완료
        hop = propagator.get_hop_distance("a", "x")
        assert hop == -1

    def test_case_insensitive(self):
        """대소문자 구분 없음."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={"ORDER": ["PAYMENT"]}
        )

        hop = propagator.get_hop_distance("payment", "order")
        assert hop == 1


# =============================================================================
# DomainPropagationMultiplier 가중치 테스트
# =============================================================================

class TestDomainPropagationMultiplier:
    """가중치 계산 테스트."""

    def test_same_domain_full_multiplier(self):
        """동일 도메인: 전체 가중치."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={"order": ["payment"]}
        )

        multiplier = propagator.get_multiplier(
            crisis_domain="payment",
            error_domain="payment",
            crisis_level=EmergencyLevel.LEVEL_3,
        )

        # LEVEL_3 = 5.0x
        assert multiplier == 5.0

    def test_one_hop_50_percent_decay(self):
        """1-hop: 50% 감쇠."""
        config = PropagationConfig(
            base_multiplier=5.0,
            decay_per_hop=0.5,
        )
        propagator = DomainPropagationMultiplier(
            config=config,
            dependency_graph={"order": ["payment"]}
        )

        multiplier = propagator.get_multiplier(
            crisis_domain="payment",
            error_domain="order",
            crisis_level=EmergencyLevel.LEVEL_3,
        )

        # 5.0 * 0.5^1 = 2.5
        assert multiplier == 2.5

    def test_two_hop_25_percent(self):
        """2-hop: 25% 가중치."""
        config = PropagationConfig(
            base_multiplier=4.0,
            decay_per_hop=0.5,
        )
        propagator = DomainPropagationMultiplier(
            config=config,
            dependency_graph={
                "order": ["payment"],
                "cart": ["order"],
            }
        )

        multiplier = propagator.get_multiplier(
            crisis_domain="payment",
            error_domain="cart",
            crisis_level=EmergencyLevel.LEVEL_3,
        )

        # LEVEL_3 = 5.0, 5.0 * 0.5^2 = 1.25, max(1.25, 1.0) = 1.25
        assert multiplier == 1.25

    def test_no_dependency_min_multiplier(self):
        """의존 없음: 최소 가중치."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={"order": ["payment"]}
        )

        multiplier = propagator.get_multiplier(
            crisis_domain="payment",
            error_domain="analytics",
            crisis_level=EmergencyLevel.LEVEL_3,
        )

        assert multiplier == 1.0  # min_multiplier

    def test_disabled_returns_level_multiplier(self):
        """비활성화: 레벨 가중치 반환."""
        config = PropagationConfig(enabled=False)
        propagator = DomainPropagationMultiplier(config=config)

        multiplier = propagator.get_multiplier(
            crisis_domain="payment",
            error_domain="order",
            crisis_level=EmergencyLevel.LEVEL_2,
        )

        # LEVEL_2 = 3.0
        assert multiplier == 3.0

    def test_level_1_multiplier(self):
        """LEVEL_1 가중치."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={"order": ["payment"]}
        )

        multiplier = propagator.get_multiplier(
            crisis_domain="payment",
            error_domain="payment",
            crisis_level=EmergencyLevel.LEVEL_1,
        )

        # LEVEL_1 = 1.5
        assert multiplier == 1.5


# =============================================================================
# 경로 테스트
# =============================================================================

class TestDomainPropagationPath:
    """경로 조회 테스트."""

    def test_hop_distance_with_path(self):
        """경로와 함께 홉 거리."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={
                "order": ["payment"],
                "cart": ["order"],
            }
        )

        hop, path = propagator.get_hop_distance_with_path("payment", "cart")

        assert hop == 2
        assert path == ["payment", "order", "cart"]

    def test_same_domain_path(self):
        """동일 도메인 경로."""
        propagator = DomainPropagationMultiplier()

        hop, path = propagator.get_hop_distance_with_path("payment", "payment")

        assert hop == 0
        assert path == ["payment"]

    def test_no_connection_empty_path(self):
        """연결 없음: 빈 경로."""
        propagator = DomainPropagationMultiplier(
            dependency_graph={"order": ["payment"]}
        )

        hop, path = propagator.get_hop_distance_with_path("payment", "analytics")

        assert hop == -1
        assert path == []


# =============================================================================
# 영향 도메인 조회
# =============================================================================

class TestGetAffectedDomains:
    """영향 도메인 조회 테스트."""

    def test_affected_domains(self):
        """영향 받는 도메인 목록."""
        propagator = DomainPropagationMultiplier(
            config=PropagationConfig(base_multiplier=5.0, decay_per_hop=0.5),
            dependency_graph={
                "order": ["payment"],
                "inventory": ["payment"],
            }
        )

        affected = propagator.get_affected_domains("payment")

        assert "payment" in affected
        assert affected["payment"] == 5.0
        assert "order" in affected
        assert affected["order"] == 2.5
        assert "inventory" in affected
        assert affected["inventory"] == 2.5


# =============================================================================
# Singleton 테스트
# =============================================================================

class TestDomainPropagationSingleton:
    """싱글톤 테스트."""

    def setup_method(self):
        """테스트 전 초기화."""
        reset_domain_propagation()

    def teardown_method(self):
        """테스트 후 정리."""
        reset_domain_propagation()

    def test_get_returns_singleton(self):
        """싱글톤 반환."""
        p1 = get_domain_propagation_multiplier()
        p2 = get_domain_propagation_multiplier()

        assert p1 is p2

    def test_configure_creates_new_instance(self):
        """설정 시 새 인스턴스."""
        p1 = get_domain_propagation_multiplier()

        config = PropagationConfig(max_hops=5)
        p2 = configure_domain_propagation(config)

        assert p1 is not p2
        assert p2.config.max_hops == 5

    def test_reset_clears_singleton(self):
        """리셋 후 새 인스턴스."""
        p1 = get_domain_propagation_multiplier()
        reset_domain_propagation()
        p2 = get_domain_propagation_multiplier()

        assert p1 is not p2


# =============================================================================
# 의존성 관리 테스트
# =============================================================================

class TestDependencyManagement:
    """의존성 관리 테스트."""

    def test_set_dependency(self):
        """의존성 설정."""
        propagator = DomainPropagationMultiplier()
        propagator.set_dependency("order", ["payment", "inventory"])

        hop = propagator.get_hop_distance("payment", "order")
        assert hop == 1

    def test_add_dependency(self):
        """의존성 추가."""
        propagator = DomainPropagationMultiplier()
        propagator.add_dependency("order", "payment")
        propagator.add_dependency("order", "inventory")

        hop1 = propagator.get_hop_distance("payment", "order")
        hop2 = propagator.get_hop_distance("inventory", "order")

        assert hop1 == 1
        assert hop2 == 1
