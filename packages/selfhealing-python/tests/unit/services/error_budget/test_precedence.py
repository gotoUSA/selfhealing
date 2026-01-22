"""
MultiplierPrecedenceResolver Unit Tests.

Level/Domain 가중치 결합 전략 테스트.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.7
"""

import pytest

from selfhealing.services.error_budget.precedence import (
    MultiplierPrecedenceResolver,
    MultiplierPrecedenceConfig,
    MultiplierCombineStrategy,
    get_precedence_resolver,
    configure_precedence_resolver,
    reset_precedence_resolver,
)


# =============================================================================
# MultiplierCombineStrategy Tests
# =============================================================================

class TestMultiplierCombineStrategy:
    """MultiplierCombineStrategy Enum 테스트."""
    
    def test_max_strategy_value(self):
        """MAX 전략 값 확인."""
        assert MultiplierCombineStrategy.MAX.value == "max"
    
    def test_sum_strategy_value(self):
        """SUM 전략 값 확인."""
        assert MultiplierCombineStrategy.SUM.value == "sum"
    
    def test_multiply_strategy_value(self):
        """MULTIPLY 전략 값 확인."""
        assert MultiplierCombineStrategy.MULTIPLY.value == "multiply"
    
    def test_level_priority_strategy_value(self):
        """LEVEL_PRIORITY 전략 값 확인."""
        assert MultiplierCombineStrategy.LEVEL_PRIORITY.value == "level_priority"
    
    def test_domain_priority_strategy_value(self):
        """DOMAIN_PRIORITY 전략 값 확인."""
        assert MultiplierCombineStrategy.DOMAIN_PRIORITY.value == "domain_priority"


# =============================================================================
# MultiplierPrecedenceConfig Tests
# =============================================================================

class TestMultiplierPrecedenceConfig:
    """MultiplierPrecedenceConfig 테스트."""
    
    def test_default_strategy_is_max(self):
        """기본 전략은 MAX."""
        config = MultiplierPrecedenceConfig()
        
        assert config.combine_strategy == MultiplierCombineStrategy.MAX
    
    def test_default_max_combined_multiplier(self):
        """기본 최대 결합 가중치는 10.0."""
        config = MultiplierPrecedenceConfig()
        
        assert config.max_combined_multiplier == 10.0
    
    def test_custom_config(self):
        """커스텀 설정."""
        config = MultiplierPrecedenceConfig(
            combine_strategy=MultiplierCombineStrategy.MULTIPLY,
            max_combined_multiplier=15.0,
        )
        
        assert config.combine_strategy == MultiplierCombineStrategy.MULTIPLY
        assert config.max_combined_multiplier == 15.0


# =============================================================================
# MultiplierPrecedenceResolver Tests
# =============================================================================

class TestMultiplierPrecedenceResolver:
    """MultiplierPrecedenceResolver 테스트."""
    
    # -------------------------------------------------------------------------
    # MAX Strategy Tests
    # -------------------------------------------------------------------------
    
    def test_max_strategy_returns_larger(self):
        """MAX 전략: 더 큰 값 선택."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.MAX
            )
        )
        
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)
        
        assert result == 5.0
    
    def test_max_strategy_level_larger(self):
        """MAX 전략: Level이 더 클 때."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.MAX
            )
        )
        
        result = resolver.resolve(level_multiplier=5.0, domain_multiplier=3.0)
        
        assert result == 5.0
    
    def test_max_strategy_equal_values(self):
        """MAX 전략: 같은 값."""
        resolver = MultiplierPrecedenceResolver()
        
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=3.0)
        
        assert result == 3.0
    
    # -------------------------------------------------------------------------
    # SUM Strategy Tests
    # -------------------------------------------------------------------------
    
    def test_sum_strategy(self):
        """SUM 전략: 합산 (중복 제거)."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.SUM
            )
        )
        
        # 3.0 + 5.0 - 1.0 = 7.0
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)
        
        assert result == 7.0
    
    def test_sum_strategy_capped(self):
        """SUM 전략: Cap 적용."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.SUM,
                max_combined_multiplier=5.0,
            )
        )
        
        # 3.0 + 5.0 - 1.0 = 7.0 → 5.0으로 Cap
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)
        
        assert result == 5.0
    
    # -------------------------------------------------------------------------
    # MULTIPLY Strategy Tests
    # -------------------------------------------------------------------------
    
    def test_multiply_strategy(self):
        """MULTIPLY 전략: 곱셈."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.MULTIPLY,
                max_combined_multiplier=20.0,
            )
        )
        
        # 3.0 * 5.0 = 15.0
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)
        
        assert result == 15.0
    
    def test_multiply_strategy_capped(self):
        """MULTIPLY 전략: Cap 적용."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.MULTIPLY,
                max_combined_multiplier=10.0,
            )
        )
        
        # 3.0 * 5.0 = 15.0 → 10.0으로 Cap
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)
        
        assert result == 10.0
    
    # -------------------------------------------------------------------------
    # LEVEL_PRIORITY Strategy Tests
    # -------------------------------------------------------------------------
    
    def test_level_priority_level_active(self):
        """LEVEL_PRIORITY: Level > 1.0일 때 Level 우선."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.LEVEL_PRIORITY
            )
        )
        
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)
        
        assert result == 3.0
    
    def test_level_priority_level_inactive(self):
        """LEVEL_PRIORITY: Level == 1.0일 때 Domain 사용."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.LEVEL_PRIORITY
            )
        )
        
        result = resolver.resolve(level_multiplier=1.0, domain_multiplier=5.0)
        
        assert result == 5.0
    
    # -------------------------------------------------------------------------
    # DOMAIN_PRIORITY Strategy Tests
    # -------------------------------------------------------------------------
    
    def test_domain_priority_domain_active(self):
        """DOMAIN_PRIORITY: Domain > 1.0일 때 Domain 우선."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.DOMAIN_PRIORITY
            )
        )
        
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)
        
        assert result == 5.0
    
    def test_domain_priority_domain_inactive(self):
        """DOMAIN_PRIORITY: Domain == 1.0일 때 Level 사용."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.DOMAIN_PRIORITY
            )
        )
        
        result = resolver.resolve(level_multiplier=3.0, domain_multiplier=1.0)
        
        assert result == 3.0
    
    # -------------------------------------------------------------------------
    # Cap and Minimum Tests
    # -------------------------------------------------------------------------
    
    def test_cap_applied(self):
        """Cap 초과 시 제한."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.MAX,
                max_combined_multiplier=5.0,
            )
        )
        
        result = resolver.resolve(level_multiplier=10.0, domain_multiplier=3.0)
        
        assert result == 5.0
    
    def test_minimum_value_guaranteed(self):
        """최소값 1.0 보장."""
        resolver = MultiplierPrecedenceResolver(
            config=MultiplierPrecedenceConfig(
                combine_strategy=MultiplierCombineStrategy.SUM
            )
        )
        
        # 0.5 + 0.3 - 1.0 = -0.2 → 1.0으로 보장
        result = resolver.resolve(level_multiplier=0.5, domain_multiplier=0.3)
        
        assert result == 1.0
    
    # -------------------------------------------------------------------------
    # Explain Tests
    # -------------------------------------------------------------------------
    
    def test_explain_output(self):
        """explain()이 사람 읽기 가능한 문자열 반환."""
        resolver = MultiplierPrecedenceResolver()
        
        explanation = resolver.explain(level_multiplier=3.0, domain_multiplier=5.0)
        
        assert "Level=3.0x" in explanation
        assert "Domain=5.0x" in explanation
        assert "Strategy=max" in explanation
        assert "Final=5.0x" in explanation
    
    # -------------------------------------------------------------------------
    # Strategy Setter Tests
    # -------------------------------------------------------------------------
    
    def test_set_strategy(self):
        """set_strategy로 전략 변경."""
        resolver = MultiplierPrecedenceResolver()
        
        assert resolver.get_strategy() == MultiplierCombineStrategy.MAX
        
        resolver.set_strategy(MultiplierCombineStrategy.MULTIPLY)
        
        assert resolver.get_strategy() == MultiplierCombineStrategy.MULTIPLY


# =============================================================================
# Singleton Tests
# =============================================================================

class TestPrecedenceResolverSingleton:
    """싱글톤 팩토리 테스트."""
    
    def setup_method(self):
        """각 테스트 전 싱글톤 리셋."""
        reset_precedence_resolver()
    
    def teardown_method(self):
        """각 테스트 후 싱글톤 리셋."""
        reset_precedence_resolver()
    
    def test_get_returns_singleton(self):
        """get_precedence_resolver는 같은 인스턴스 반환."""
        resolver1 = get_precedence_resolver()
        resolver2 = get_precedence_resolver()
        
        assert resolver1 is resolver2
    
    def test_configure_creates_new_instance(self):
        """configure_precedence_resolver는 새 인스턴스 생성."""
        resolver1 = get_precedence_resolver()
        
        config = MultiplierPrecedenceConfig(
            combine_strategy=MultiplierCombineStrategy.MULTIPLY
        )
        resolver2 = configure_precedence_resolver(config=config)
        
        assert resolver2 is not resolver1
        assert resolver2.get_strategy() == MultiplierCombineStrategy.MULTIPLY
    
    def test_reset_clears_singleton(self):
        """reset_precedence_resolver는 싱글톤 초기화."""
        resolver1 = get_precedence_resolver()
        
        reset_precedence_resolver()
        
        resolver2 = get_precedence_resolver()
        
        assert resolver2 is not resolver1
