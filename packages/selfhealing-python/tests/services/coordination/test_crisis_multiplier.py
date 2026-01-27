"""
DomainAwareCrisisMultiplier 단위 테스트.

도메인 인지형 위기 가중치 계산을 검증합니다.
"""

import pytest

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.coordination.crisis_multiplier import (
    DomainAwareCrisisMultiplier,
    CrisisMultiplierRegistry,
    MAX_CRISIS_MULTIPLIER,
    DEFAULT_LEVEL_MULTIPLIERS,
    DEFAULT_DOMAIN_SENSITIVITY,
)


class TestDomainAwareCrisisMultiplier:
    """DomainAwareCrisisMultiplier 테스트."""
    
    @pytest.fixture
    def multiplier(self):
        return DomainAwareCrisisMultiplier()
    
    def test_same_domain_high_multiplier(self, multiplier):
        """동일 도메인: 높은 가중치 적용."""
        weight = multiplier.get_multiplier(
            crisis_domain="payment",
            error_domain="payment",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        # payment 민감도(10.0) * LEVEL_3 가중치(5.0) = 50.0 → capped to 10.0
        assert weight == MAX_CRISIS_MULTIPLIER
    
    def test_different_domain_default_multiplier(self, multiplier):
        """다른 도메인: 기본 가중치 1.0."""
        weight = multiplier.get_multiplier(
            crisis_domain="payment",
            error_domain="analytics",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        assert weight == 1.0
    
    def test_level_based_multipliers(self, multiplier):
        """레벨별 기본 가중치."""
        # NORMAL
        weight_normal = multiplier.get_multiplier(
            crisis_domain="order",
            error_domain="order",
            crisis_level=EmergencyLevel.NORMAL,
        )
        # order 민감도(5.0) * NORMAL(1.0) = 5.0
        assert weight_normal == 5.0
        
        # LEVEL_1
        weight_l1 = multiplier.get_multiplier(
            crisis_domain="order",
            error_domain="order",
            crisis_level=EmergencyLevel.LEVEL_1,
        )
        # order 민감도(5.0) * LEVEL_1(1.5) = 7.5
        assert weight_l1 == 7.5
    
    def test_domain_aware_disabled(self):
        """도메인 인지 비활성화 시 레벨 기반 일괄 적용."""
        multiplier = DomainAwareCrisisMultiplier(domain_aware_enabled=False)
        
        # 다른 도메인이어도 레벨 가중치 적용
        weight = multiplier.get_multiplier(
            crisis_domain="payment",
            error_domain="analytics",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        assert weight == DEFAULT_LEVEL_MULTIPLIERS[EmergencyLevel.LEVEL_3]
    
    def test_unknown_domain_default_sensitivity(self, multiplier):
        """알 수 없는 도메인은 기본 민감도 1.0."""
        weight = multiplier.get_multiplier(
            crisis_domain="unknown_domain",
            error_domain="unknown_domain",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        # 기본 민감도(1.0) * LEVEL_3(5.0) = 5.0
        assert weight == 5.0
    
    def test_case_insensitive_domain_matching(self, multiplier):
        """도메인 대소문자 무시."""
        weight_lower = multiplier.get_multiplier(
            crisis_domain="payment",
            error_domain="PAYMENT",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        weight_mixed = multiplier.get_multiplier(
            crisis_domain="Payment",
            error_domain="payment",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        assert weight_lower == weight_mixed == MAX_CRISIS_MULTIPLIER
    
    def test_get_weighted_budget_consumption(self, multiplier):
        """가중치 적용된 Budget 소진량 계산."""
        base = 10.0
        
        # 동일 도메인 (높은 가중치)
        weighted_same = multiplier.get_weighted_budget_consumption(
            base_consumption=base,
            crisis_domain="payment",
            error_domain="payment",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        # 다른 도메인 (기본 가중치)
        weighted_diff = multiplier.get_weighted_budget_consumption(
            base_consumption=base,
            crisis_domain="payment",
            error_domain="analytics",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        assert weighted_same == base * MAX_CRISIS_MULTIPLIER
        assert weighted_diff == base * 1.0
    
    def test_set_domain_sensitivity(self, multiplier):
        """도메인 민감도 설정."""
        multiplier.set_domain_sensitivity("custom", 8.0)
        
        weight = multiplier.get_multiplier(
            crisis_domain="custom",
            error_domain="custom",
            crisis_level=EmergencyLevel.LEVEL_2,
        )
        
        # custom(8.0) * LEVEL_2(3.0) = 24.0 → capped to 10.0
        assert weight == MAX_CRISIS_MULTIPLIER
    
    def test_remove_domain_sensitivity(self, multiplier):
        """도메인 민감도 제거."""
        # payment 민감도 제거
        result = multiplier.remove_domain_sensitivity("payment")
        
        assert result is True
        
        # 제거 후 기본값(1.0) 사용
        weight = multiplier.get_multiplier(
            crisis_domain="payment",
            error_domain="payment",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        # 기본(1.0) * LEVEL_3(5.0) = 5.0
        assert weight == 5.0
    
    def test_get_all_sensitivities(self, multiplier):
        """모든 도메인 민감도 조회."""
        sensitivities = multiplier.get_all_sensitivities()
        
        assert "payment" in sensitivities
        assert "order" in sensitivities
        assert sensitivities["payment"] == 10.0
    
    def test_max_multiplier_cap(self):
        """최대 가중치 Cap 적용."""
        multiplier = DomainAwareCrisisMultiplier(
            domain_sensitivity={"critical": 100.0},  # 매우 높은 민감도
        )
        
        weight = multiplier.get_multiplier(
            crisis_domain="critical",
            error_domain="critical",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        
        # 100.0 * 5.0 = 500.0 → capped to 10.0
        assert weight == MAX_CRISIS_MULTIPLIER


class TestCrisisMultiplierRegistry:
    """CrisisMultiplierRegistry 테스트."""
    
    @pytest.fixture
    def registry(self):
        return CrisisMultiplierRegistry()
    
    def test_get_or_create_multiplier(self, registry):
        """네임스페이스별 Multiplier 생성."""
        seoul = registry.get_or_create("seoul")
        tokyo = registry.get_or_create("tokyo")
        
        assert seoul is not tokyo
        assert isinstance(seoul, DomainAwareCrisisMultiplier)
    
    def test_same_namespace_returns_same_instance(self, registry):
        """동일 네임스페이스는 동일 인스턴스."""
        first = registry.get_or_create("seoul")
        second = registry.get_or_create("seoul")
        
        assert first is second
    
    def test_namespace_specific_configuration(self, registry):
        """네임스페이스별 독립 설정."""
        seoul = registry.get_or_create("seoul")
        tokyo = registry.get_or_create("tokyo")
        
        # 서울에만 고민감도 설정
        seoul.set_domain_sensitivity("local_payment", 15.0)
        
        # 도쿄는 영향 없음
        assert "local_payment" in seoul.get_all_sensitivities()
        assert "local_payment" not in tokyo.get_all_sensitivities()
    
    def test_set_multiplier(self, registry):
        """Multiplier 직접 설정."""
        custom = DomainAwareCrisisMultiplier(
            base_crisis_multiplier=10.0,
        )
        
        registry.set_multiplier("custom_ns", custom)
        
        retrieved = registry.get_or_create("custom_ns")
        assert retrieved.base_crisis_multiplier == 10.0
    
    def test_remove_namespace(self, registry):
        """네임스페이스 제거."""
        registry.get_or_create("to_remove")
        
        result = registry.remove("to_remove")
        
        assert result is True
        assert "to_remove" not in registry.list_namespaces()
    
    def test_list_namespaces(self, registry):
        """등록된 네임스페이스 목록."""
        registry.get_or_create("ns1")
        registry.get_or_create("ns2")
        
        namespaces = registry.list_namespaces()
        
        assert "ns1" in namespaces
        assert "ns2" in namespaces
