"""
Check-on-Use Multiplier Provider.

매 사용 시점에 Emergency Level을 조회하여 가중치 컨텍스트를 반환합니다.
캐시된 값이 아닌 실시간 상태를 사용하여 정확한 가중치 적용을 보장합니다.

Features:
- 매 호출 시 Emergency Level 조회 (Check on Use)
- MultiplierContext 반환 (level, domain, final multiplier 포함)
- MultiplierPrecedenceResolver 통합

Usage:
    from selfhealing.services.error_budget.provider import (
        CheckOnUseMultiplierProvider,
        MultiplierContext,
    )

    provider = CheckOnUseMultiplierProvider()
    ctx = provider.get_current_multiplier(namespace="seoul", domain="payment")
    print(f"Final multiplier: {ctx.final_multiplier}x")

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.error_budget.constants import (
    DEFAULT_LEVEL_MULTIPLIERS,
    MAX_COMBINED_MULTIPLIER,
    get_domain_sensitivity,
)

logger = structlog.get_logger()


# =============================================================================
# MultiplierContext
# =============================================================================


@dataclass
class MultiplierContext:
    """
    가중치 적용 컨텍스트.

    Error Budget 소진 시 적용되는 가중치와 그 근거를 담습니다.

    Attributes:
        level: 현재 Emergency Level
        level_multiplier: Level 기반 가중치
        domain: 에러 발생 도메인
        domain_multiplier: 도메인 기반 가중치
        final_multiplier: 최종 적용 가중치
        emergency_id: 관련 Emergency ID
    """

    level: EmergencyLevel = EmergencyLevel.NORMAL
    """현재 Emergency Level."""

    level_multiplier: float = 1.0
    """Level 기반 가중치."""

    domain: str | None = None
    """에러 발생 도메인."""

    domain_multiplier: float = 1.0
    """도메인 기반 가중치."""

    final_multiplier: float = 1.0
    """최종 적용 가중치."""

    emergency_id: str | None = None
    """관련 Emergency ID."""

    namespace: str | None = None
    """네임스페이스."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "level": self.level.name,
            "level_multiplier": self.level_multiplier,
            "domain": self.domain,
            "domain_multiplier": self.domain_multiplier,
            "final_multiplier": self.final_multiplier,
            "emergency_id": self.emergency_id,
            "namespace": self.namespace,
        }


# =============================================================================
# CheckOnUseMultiplierProvider
# =============================================================================


class CheckOnUseMultiplierProvider:
    """
    Check-on-Use 패턴의 가중치 제공자.

    에러 발생 시점마다 현재 Emergency Level을 실시간으로 조회하여
    정확한 가중치를 반환합니다. 캐시를 사용하지 않음.

    Features:
    - 매 호출 시 Emergency Level 조회 (Check on Use)
    - 도메인 기반 추가 가중치 지원
    - MultiplierPrecedenceResolver 통합

    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.0
    """

    def __init__(
        self,
        level_multipliers: dict[EmergencyLevel, float] | None = None,
        domain_sensitivity: dict[str, float] | None = None,
        emergency_tracker: Any | None = None,
        precedence_resolver: Any | None = None,
        max_multiplier: float = MAX_COMBINED_MULTIPLIER,
    ):
        """
        CheckOnUseMultiplierProvider 초기화.

        Args:
            level_multipliers: Level별 가중치 맵
            domain_sensitivity: 도메인별 민감도 맵
            emergency_tracker: Emergency 상태 추적기 (lazy loading)
            precedence_resolver: 가중치 충돌 해결기 (lazy loading)
            max_multiplier: 최대 가중치 Cap
        """
        self._multipliers = level_multipliers or dict(DEFAULT_LEVEL_MULTIPLIERS)
        self._domain_sensitivity = domain_sensitivity or dict(get_domain_sensitivity())
        self._emergency_tracker = emergency_tracker
        self._precedence_resolver = precedence_resolver
        self._max_multiplier = max_multiplier

    def _get_emergency_tracker(self) -> Any:
        """EmergencyTracker 획득 (lazy loading)."""
        if self._emergency_tracker is None:
            try:
                from selfhealing.services.namespace_emergency import (
                    get_namespaced_emergency_tracker,
                )

                self._emergency_tracker = get_namespaced_emergency_tracker()
            except ImportError:
                logger.warning("provider")
        return self._emergency_tracker

    def _get_precedence_resolver(self) -> Any:
        """PrecedenceResolver 획득 (lazy loading)."""
        if self._precedence_resolver is None:
            try:
                from selfhealing.services.error_budget.precedence import (
                    MultiplierPrecedenceResolver,
                )

                self._precedence_resolver = MultiplierPrecedenceResolver()
            except ImportError:
                logger.debug("provider")
        return self._precedence_resolver

    def get_current_multiplier(
        self,
        namespace: str | None = None,
        domain: str | None = None,
    ) -> MultiplierContext:
        """
        현재 가중치 컨텍스트 조회 (Check on Use).

        매 호출마다 실시간 Emergency Level을 조회합니다.

        Args:
            namespace: 네임스페이스
            domain: 에러 발생 도메인

        Returns:
            MultiplierContext: 가중치 컨텍스트
        """
        # Emergency Level 실시간 조회
        current_level = self._get_current_level(namespace)
        level_multiplier = self._multipliers.get(current_level, 1.0)

        # 도메인 가중치 조회 (있는 경우)
        domain_multiplier = 1.0
        if domain:
            domain_multiplier = self._get_domain_multiplier(domain)

        # 가중치 결합
        final_multiplier = self._combine_multipliers(level_multiplier, domain_multiplier)

        # Emergency ID 조회
        emergency_id = self._get_current_emergency_id(namespace)

        ctx = MultiplierContext(
            level=current_level,
            level_multiplier=level_multiplier,
            domain=domain,
            domain_multiplier=domain_multiplier,
            final_multiplier=final_multiplier,
            emergency_id=emergency_id,
            namespace=namespace,
        )

        logger.debug(
            "provider.context",
            current_level=current_level.name,
            level_multiplier=level_multiplier,
            domain=domain,
            domain_multiplier=domain_multiplier,
            final_multiplier=final_multiplier,
        )

        return ctx

    def _get_current_level(
        self,
        namespace: str | None = None,
    ) -> EmergencyLevel:
        """현재 Emergency Level 조회."""
        tracker = self._get_emergency_tracker()
        if tracker:
            try:
                state = tracker.get_effective_state(namespace=namespace)
                return state.emergency_level
            except Exception as e:
                logger.warning(
                    "provider.failed_get_level",
                    error=e,
                )

        return EmergencyLevel.NORMAL

    def _get_domain_multiplier(self, domain: str) -> float:
        """
        도메인별 가중치 조회.

        도메인 민감도 맵에서 조회하거나 기본값 1.0 반환.
        """
        return self._domain_sensitivity.get(domain.lower(), 1.0)

    def _combine_multipliers(
        self,
        level_multiplier: float,
        domain_multiplier: float,
    ) -> float:
        """가중치 결합 및 Cap 적용."""
        resolver = self._get_precedence_resolver()

        if resolver:
            result = resolver.resolve(level_multiplier, domain_multiplier)
        else:
            # 기본: max 전략
            result = max(level_multiplier, domain_multiplier)

        # Cap 적용
        return min(result, self._max_multiplier)

    def _get_current_emergency_id(
        self,
        namespace: str | None = None,
    ) -> str | None:
        """현재 활성 Emergency ID 조회."""
        tracker = self._get_emergency_tracker()
        if tracker:
            try:
                if hasattr(tracker, "get_active_emergency_id"):
                    return tracker.get_active_emergency_id(namespace)
                state = tracker.get_effective_state(namespace=namespace)
                return getattr(state, "emergency_id", None)
            except Exception:
                pass
        return None

    def set_domain_sensitivity(
        self,
        domain: str,
        sensitivity: float,
    ) -> None:
        """
        도메인 민감도 설정.

        Args:
            domain: 도메인 이름
            sensitivity: 민감도 가중치 (1.0 이상 권장)
        """
        if sensitivity < 1.0:
            logger.warning(
                "provider.sensitivity_recommended",
                domain=domain,
                sensitivity=sensitivity,
            )
        self._domain_sensitivity[domain.lower()] = sensitivity

    def get_all_multipliers(self) -> dict[str, float]:
        """모든 레벨의 가중치 반환."""
        return {level.name: value for level, value in self._multipliers.items()}


# =============================================================================
# Singleton
# =============================================================================

_check_on_use_provider: CheckOnUseMultiplierProvider | None = None


def get_check_on_use_provider() -> CheckOnUseMultiplierProvider:
    """CheckOnUseMultiplierProvider 싱글톤 반환."""
    global _check_on_use_provider
    if _check_on_use_provider is None:
        _check_on_use_provider = CheckOnUseMultiplierProvider()
    return _check_on_use_provider


def reset_check_on_use_provider() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _check_on_use_provider
    _check_on_use_provider = None
