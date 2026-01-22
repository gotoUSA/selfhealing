"""
Error Budget Constants Unit Tests.

통합 Cap 상수(SSOT) 테스트.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §0.1 (11번)
"""

import pytest

from selfhealing.services.error_budget.constants import (
    MAX_CRISIS_MULTIPLIER_CAP,
    MAX_DOMAIN_MULTIPLIER,
    MAX_COMBINED_MULTIPLIER,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_LEVEL_MULTIPLIERS,
    DEFAULT_DOMAIN_SENSITIVITY,
    DEFAULT_PROPAGATION_DECAY,
    DEFAULT_PROPAGATION_MAX_HOPS,
    DEFAULT_REFUND_RATIO,
    REFUND_PROPOSAL_EXPIRY_HOURS,
    DEFAULT_COMBINE_STRATEGY,
)
from selfhealing.services.emergency_mode.enums import EmergencyLevel


class TestCrisisMultiplierCap:
    """MAX_CRISIS_MULTIPLIER_CAP 테스트."""
    
    def test_max_crisis_multiplier_cap_value(self):
        """MAX_CRISIS_MULTIPLIER_CAP = 10.0 확인."""
        assert MAX_CRISIS_MULTIPLIER_CAP == 10.0
    
    def test_max_crisis_multiplier_cap_is_float(self):
        """MAX_CRISIS_MULTIPLIER_CAP는 float 타입."""
        assert isinstance(MAX_CRISIS_MULTIPLIER_CAP, float)


class TestDomainMultiplier:
    """MAX_DOMAIN_MULTIPLIER 테스트."""
    
    def test_max_domain_multiplier_value(self):
        """MAX_DOMAIN_MULTIPLIER = 24.0 확인."""
        assert MAX_DOMAIN_MULTIPLIER == 24.0


class TestCombinedMultiplier:
    """MAX_COMBINED_MULTIPLIER 테스트."""
    
    def test_max_combined_multiplier_value(self):
        """MAX_COMBINED_MULTIPLIER = 10.0 확인."""
        assert MAX_COMBINED_MULTIPLIER == 10.0
    
    def test_combined_matches_crisis_cap(self):
        """결합 후 Cap은 Crisis Cap과 동일."""
        assert MAX_COMBINED_MULTIPLIER == MAX_CRISIS_MULTIPLIER_CAP


class TestCacheTTL:
    """DEFAULT_CACHE_TTL_SECONDS 테스트."""
    
    def test_cache_ttl_value(self):
        """기본 캐시 TTL은 30초."""
        assert DEFAULT_CACHE_TTL_SECONDS == 30.0


class TestDefaultLevelMultipliers:
    """DEFAULT_LEVEL_MULTIPLIERS 테스트."""
    
    def test_normal_level_multiplier(self):
        """NORMAL 레벨은 1.0x."""
        assert DEFAULT_LEVEL_MULTIPLIERS[EmergencyLevel.NORMAL] == 1.0
    
    def test_level_1_multiplier(self):
        """LEVEL_1은 1.5x."""
        assert DEFAULT_LEVEL_MULTIPLIERS[EmergencyLevel.LEVEL_1] == 1.5
    
    def test_level_2_multiplier(self):
        """LEVEL_2는 3.0x."""
        assert DEFAULT_LEVEL_MULTIPLIERS[EmergencyLevel.LEVEL_2] == 3.0
    
    def test_level_3_multiplier(self):
        """LEVEL_3는 5.0x."""
        assert DEFAULT_LEVEL_MULTIPLIERS[EmergencyLevel.LEVEL_3] == 5.0
    
    def test_all_levels_present(self):
        """모든 Emergency Level이 정의됨."""
        for level in EmergencyLevel:
            assert level in DEFAULT_LEVEL_MULTIPLIERS
    
    def test_multipliers_increasing(self):
        """레벨이 높아질수록 가중치 증가."""
        assert DEFAULT_LEVEL_MULTIPLIERS[EmergencyLevel.NORMAL] < \
               DEFAULT_LEVEL_MULTIPLIERS[EmergencyLevel.LEVEL_1] < \
               DEFAULT_LEVEL_MULTIPLIERS[EmergencyLevel.LEVEL_2] < \
               DEFAULT_LEVEL_MULTIPLIERS[EmergencyLevel.LEVEL_3]


class TestDefaultDomainSensitivity:
    """DEFAULT_DOMAIN_SENSITIVITY 테스트."""
    
    def test_payment_highest_sensitivity(self):
        """결제 도메인이 가장 높은 민감도."""
        assert DEFAULT_DOMAIN_SENSITIVITY["payment"] == 10.0
    
    def test_analytics_lowest_sensitivity(self):
        """분석 도메인이 가장 낮은 민감도."""
        assert DEFAULT_DOMAIN_SENSITIVITY["analytics"] == 1.0
    
    def test_expected_domains_present(self):
        """예상 도메인들이 모두 정의됨."""
        expected = ["payment", "order", "inventory", "notification", "analytics"]
        for domain in expected:
            assert domain in DEFAULT_DOMAIN_SENSITIVITY


class TestPropagationSettings:
    """도메인 전파 설정 테스트."""
    
    def test_propagation_decay_value(self):
        """기본 감쇠율은 0.5 (50%)."""
        assert DEFAULT_PROPAGATION_DECAY == 0.5
    
    def test_propagation_max_hops_value(self):
        """최대 전파 홉은 3."""
        assert DEFAULT_PROPAGATION_MAX_HOPS == 3


class TestRefundSettings:
    """환불 설정 테스트."""
    
    def test_refund_ratio_value(self):
        """기본 환불 비율은 50%."""
        assert DEFAULT_REFUND_RATIO == 0.5
    
    def test_refund_expiry_hours_value(self):
        """환불 제안 만료는 24시간."""
        assert REFUND_PROPOSAL_EXPIRY_HOURS == 24


class TestCombineStrategy:
    """결합 전략 테스트."""
    
    def test_default_combine_strategy(self):
        """기본 결합 전략은 'max'."""
        assert DEFAULT_COMBINE_STRATEGY == "max"
