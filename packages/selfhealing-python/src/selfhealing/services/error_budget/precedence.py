"""
Multiplier Precedence Resolver.

Level 가중치와 Domain 가중치가 충돌할 때 최종 가중치를 결정합니다.
다양한 결합 전략(max, sum, multiply 등)을 지원합니다.

Features:
- 다양한 결합 전략 지원 (MAX, SUM, MULTIPLY)
- 통합 Cap 적용 (MAX_CRISIS_MULTIPLIER_CAP)
- 결정 로깅 및 감사

Usage:
    from selfhealing.services.error_budget.precedence import (
        MultiplierPrecedenceResolver,
        MultiplierCombineStrategy,
    )

    resolver = MultiplierPrecedenceResolver()

    # Level=3.0x, Domain=5.0x → max(3.0, 5.0) = 5.0x
    final = resolver.resolve(level_multiplier=3.0, domain_multiplier=5.0)

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.7
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import structlog

from selfhealing.services.error_budget.constants import MAX_COMBINED_MULTIPLIER

logger = structlog.get_logger()


# =============================================================================
# Combine Strategy Enum
# =============================================================================


class MultiplierCombineStrategy(str, Enum):
    """
    가중치 결합 전략.

    Level 가중치와 Domain 가중치를 어떻게 결합할지 결정합니다.

    Attributes:
        MAX: 최대값 사용 (보수적, 기본값)
        SUM: 합산 (위험)
        MULTIPLY: 곱셈 (매우 위험, Cap 필수)
        LEVEL_PRIORITY: Level 가중치 우선
        DOMAIN_PRIORITY: Domain 가중치 우선
    """

    MAX = "max"
    """최대값 사용 (보수적). 권장."""

    SUM = "sum"
    """합산 (중복 제거 후). 위험."""

    MULTIPLY = "multiply"
    """곱셈. 매우 위험, Cap 필수."""

    LEVEL_PRIORITY = "level_priority"
    """Level 가중치 우선."""

    DOMAIN_PRIORITY = "domain_priority"
    """Domain 가중치 우선."""


# =============================================================================
# Precedence Config
# =============================================================================


@dataclass
class MultiplierPrecedenceConfig:
    """
    가중치 우선순위 설정.

    Attributes:
        combine_strategy: 결합 전략
        max_combined_multiplier: 최대 결합 가중치 (통합 Cap)
        level_weight: Level 가중치 비중 (가중 평균 시 사용)
        domain_weight: Domain 가중치 비중 (가중 평균 시 사용)
    """

    combine_strategy: MultiplierCombineStrategy = MultiplierCombineStrategy.MAX
    """결합 전략."""

    max_combined_multiplier: float = MAX_COMBINED_MULTIPLIER
    """
    최대 결합 가중치 (통합 Cap).

    SSOT: 모든 가중치 결합 후 이 값을 초과할 수 없음.
    """

    level_weight: float = 1.0
    """Level 가중치 비중 (가중 평균 시 사용)."""

    domain_weight: float = 1.0
    """Domain 가중치 비중 (가중 평균 시 사용)."""


# =============================================================================
# Precedence Resolver
# =============================================================================


class MultiplierPrecedenceResolver:
    """
    가중치 충돌 해결기.

    Level 가중치와 Domain 가중치가 모두 적용될 때
    최종 가중치를 결정합니다.

    Features:
    - 다양한 결합 전략 지원 (max, sum, multiply)
    - 통합 Cap 적용 (MAX_CRISIS_MULTIPLIER_CAP)
    - 결정 로깅 및 감사

    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.7
    """

    def __init__(
        self,
        config: MultiplierPrecedenceConfig | None = None,
    ):
        """
        MultiplierPrecedenceResolver 초기화.

        Args:
            config: 우선순위 설정
        """
        self.config = config or MultiplierPrecedenceConfig()

    def resolve(
        self,
        level_multiplier: float,
        domain_multiplier: float,
    ) -> float:
        """
        가중치 충돌 해결.

        Args:
            level_multiplier: Emergency Level 기반 가중치
            domain_multiplier: 도메인 기반 가중치

        Returns:
            최종 적용 가중치
        """
        strategy = self.config.combine_strategy

        if strategy == MultiplierCombineStrategy.MAX:
            result = max(level_multiplier, domain_multiplier)

        elif strategy == MultiplierCombineStrategy.SUM:
            # 기본값 1.0 중복 제거
            result = level_multiplier + domain_multiplier - 1.0

        elif strategy == MultiplierCombineStrategy.MULTIPLY:
            result = level_multiplier * domain_multiplier

        elif strategy == MultiplierCombineStrategy.LEVEL_PRIORITY:
            result = level_multiplier if level_multiplier > 1.0 else domain_multiplier

        elif strategy == MultiplierCombineStrategy.DOMAIN_PRIORITY:
            result = domain_multiplier if domain_multiplier > 1.0 else level_multiplier

        else:
            # 알 수 없는 전략: 기본값 max
            result = max(level_multiplier, domain_multiplier)

        # 최소값 보장 (1.0 미만 방지)
        result = max(result, 1.0)

        # Cap 적용
        final = min(result, self.config.max_combined_multiplier)

        if result != final:
            logger.warning(
                "precedence_resolver.multiplier_capped",
                result=result,
                final=final,
                strategy=strategy.value,
            )

        return final

    def explain(
        self,
        level_multiplier: float,
        domain_multiplier: float,
    ) -> str:
        """
        결정 과정 설명.

        Args:
            level_multiplier: Level 기반 가중치
            domain_multiplier: Domain 기반 가중치

        Returns:
            사람이 읽을 수 있는 설명
        """
        final = self.resolve(level_multiplier, domain_multiplier)
        strategy = self.config.combine_strategy

        return (
            f"Level={level_multiplier}x, Domain={domain_multiplier}x, "
            f"Strategy={strategy.value}, Final={final}x"
        )

    def set_strategy(self, strategy: MultiplierCombineStrategy) -> None:
        """
        결합 전략 변경.

        Args:
            strategy: 새 결합 전략
        """
        self.config.combine_strategy = strategy
        logger.info(
            "precedence_resolver.strategy_changed",
            strategy=strategy.value,
        )

    def get_strategy(self) -> MultiplierCombineStrategy:
        """현재 결합 전략 반환."""
        return self.config.combine_strategy


# =============================================================================
# Singleton
# =============================================================================

_precedence_resolver: MultiplierPrecedenceResolver | None = None


def get_precedence_resolver() -> MultiplierPrecedenceResolver:
    """MultiplierPrecedenceResolver 싱글톤 반환."""
    global _precedence_resolver
    if _precedence_resolver is None:
        _precedence_resolver = MultiplierPrecedenceResolver()
    return _precedence_resolver


def configure_precedence_resolver(
    config: MultiplierPrecedenceConfig | None = None,
) -> MultiplierPrecedenceResolver:
    """
    MultiplierPrecedenceResolver 싱글톤 설정.

    Args:
        config: 우선순위 설정

    Returns:
        설정된 MultiplierPrecedenceResolver 인스턴스
    """
    global _precedence_resolver
    _precedence_resolver = MultiplierPrecedenceResolver(config=config)
    return _precedence_resolver


def reset_precedence_resolver() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _precedence_resolver
    _precedence_resolver = None
