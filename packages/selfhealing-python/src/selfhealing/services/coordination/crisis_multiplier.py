"""
Domain-Aware Crisis Multiplier.

도메인 인지형 위기 가중치 계산기.
장애가 발생한 도메인과 연관된 에러에 대해서만 높은 가중치를 주고,
관련 없는 도메인의 에러는 일반 가중치를 유지합니다.

Code reference:
    error_budget/reconciliation/shadow_calculator.py#L222 (domain_multiplier)
    error_budget/reconciliation/shadow_calculator.py#L245 (_get_domain_weight)

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.error_budget.constants import (
    DEFAULT_LEVEL_MULTIPLIERS,
    MAX_CRISIS_MULTIPLIER_CAP,
    get_domain_sensitivity,
)

logger = structlog.get_logger()


# SSOT: error_budget.constants 에서 가져옴 (backward compat re-export)
DEFAULT_DOMAIN_SENSITIVITY = get_domain_sensitivity()

# 최대 위기 가중치 (Cap) - SSOT에서 가져옴
MAX_CRISIS_MULTIPLIER = MAX_CRISIS_MULTIPLIER_CAP


@dataclass
class DomainAwareCrisisMultiplier:
    """
    도메인 인지형 위기 가중치.

    장애 도메인과 에러 도메인이 일치할 때만 높은 가중치를 적용하여,
    관련 없는 도메인의 에러가 불필요하게 증폭되지 않도록 합니다.

    Code reference:
        shadow_calculator.py#L222 (_get_domain_weight 패턴)

    Usage:
        multiplier = DomainAwareCrisisMultiplier()
        weight = multiplier.get_multiplier(
            crisis_domain="payment",
            error_domain="payment",
            crisis_level=EmergencyLevel.LEVEL_3,
        )
        # weight = 5.0 * 10.0 = 50.0 → capped to 10.0
    """

    # 기본 위기 가중치
    base_crisis_multiplier: float = 5.0
    """LEVEL_3 기본 가중치."""

    # 도메인 인지 기능 활성화 여부
    domain_aware_enabled: bool = True
    """False면 도메인 무관하게 레벨 기반 일괄 적용."""

    # 도메인별 민감도 가중치
    domain_sensitivity: dict[str, float] = field(default_factory=lambda: dict(get_domain_sensitivity()))
    """도메인별 민감도 (높을수록 중요). SSOT: DomainSensitivitySettings."""

    # 레벨별 기본 가중치
    level_multipliers: dict[EmergencyLevel, float] = field(default_factory=lambda: dict(DEFAULT_LEVEL_MULTIPLIERS))
    """Emergency 레벨별 기본 가중치."""

    # 최대 가중치 Cap
    max_multiplier: float = MAX_CRISIS_MULTIPLIER
    """가중치 상한선."""

    def get_multiplier(
        self,
        crisis_domain: str,
        error_domain: str,
        crisis_level: EmergencyLevel,
    ) -> float:
        """
        도메인 기반 가중치 계산.

        장애 도메인과 에러 도메인이 일치하면 높은 가중치,
        다르면 기본 가중치(1.0)를 반환합니다.

        Args:
            crisis_domain: 현재 위기가 발생한 도메인
            error_domain: 에러가 발생한 도메인
            crisis_level: 현재 Emergency 레벨

        Returns:
            적용할 가중치 (1.0 ~ max_multiplier)

        Example:
            # 동일 도메인: 전체 가중치 적용
            get_multiplier("payment", "payment", LEVEL_3)  # → 10.0 (capped)

            # 다른 도메인: 기본 가중치
            get_multiplier("payment", "analytics", LEVEL_3)  # → 1.0
        """
        if not self.domain_aware_enabled:
            # 도메인 인지 비활성화 시 레벨 기반 일괄 적용
            return self._get_level_multiplier(crisis_level)

        # 동일 도메인: 전체 가중치 적용
        if crisis_domain.lower() == error_domain.lower():
            domain_weight = self.domain_sensitivity.get(error_domain.lower(), 1.0)
            level_weight = self._get_level_multiplier(crisis_level)

            # 가중치 계산 및 Cap 적용
            raw_multiplier = level_weight * domain_weight
            final_multiplier = min(raw_multiplier, self.max_multiplier)

            logger.debug(
                "crisis_multiplier.same_domain",
                crisis_domain=crisis_domain,
                error_domain=error_domain,
                crisis_level=crisis_level.name,
                domain_weight=domain_weight,
                level_weight=level_weight,
                raw_multiplier=raw_multiplier,
                final_multiplier=final_multiplier,
            )

            return final_multiplier

        # 다른 도메인: 기본 가중치 유지
        logger.debug(
            "crisis_multiplier.different_domain_using_default",
            crisis_domain=crisis_domain,
            error_domain=error_domain,
        )
        return 1.0

    def _get_level_multiplier(self, level: EmergencyLevel) -> float:
        """레벨 기반 기본 가중치."""
        return self.level_multipliers.get(level, 1.0)

    def get_weighted_budget_consumption(
        self,
        base_consumption: float,
        crisis_domain: str,
        error_domain: str,
        crisis_level: EmergencyLevel,
    ) -> float:
        """
        가중치 적용된 Budget 소진량 계산.

        Args:
            base_consumption: 기본 Budget 소진량
            crisis_domain: 위기 도메인
            error_domain: 에러 도메인
            crisis_level: Emergency 레벨

        Returns:
            가중치 적용된 Budget 소진량
        """
        multiplier = self.get_multiplier(
            crisis_domain=crisis_domain,
            error_domain=error_domain,
            crisis_level=crisis_level,
        )
        return base_consumption * multiplier

    def set_domain_sensitivity(
        self,
        domain: str,
        sensitivity: float,
    ) -> None:
        """
        도메인 민감도 설정.

        Args:
            domain: 도메인 이름
            sensitivity: 민감도 가중치 (1.0 이상)
        """
        if sensitivity < 1.0:
            logger.warning(
                "crisis_multiplier.sensitivity_recommended",
                domain=domain,
                sensitivity=sensitivity,
            )
        self.domain_sensitivity[domain.lower()] = sensitivity

    def remove_domain_sensitivity(self, domain: str) -> bool:
        """
        도메인 민감도 제거 (기본값 사용).

        Args:
            domain: 도메인 이름

        Returns:
            제거 성공 여부
        """
        domain_lower = domain.lower()
        if domain_lower in self.domain_sensitivity:
            del self.domain_sensitivity[domain_lower]
            return True
        return False

    def get_all_sensitivities(self) -> dict[str, float]:
        """모든 도메인 민감도 조회."""
        return dict(self.domain_sensitivity)


class CrisisMultiplierRegistry:
    """
    네임스페이스별 Crisis Multiplier 레지스트리.

    리전별로 다른 민감도 설정을 지원합니다.

    Usage:
        registry = CrisisMultiplierRegistry()

        # 서울 리전에 결제 도메인 고민감도 설정
        registry.get_or_create("seoul").set_domain_sensitivity("payment", 15.0)

        # 도쿄 리전은 기본값 사용
        multiplier = registry.get_or_create("tokyo").get_multiplier(...)
    """

    def __init__(self):
        self._multipliers: dict[str, DomainAwareCrisisMultiplier] = {}

    def get_or_create(
        self,
        namespace: str,
    ) -> DomainAwareCrisisMultiplier:
        """
        네임스페이스별 Multiplier 조회 또는 생성.

        Args:
            namespace: 네임스페이스

        Returns:
            해당 네임스페이스의 DomainAwareCrisisMultiplier
        """
        if namespace not in self._multipliers:
            self._multipliers[namespace] = DomainAwareCrisisMultiplier()
        return self._multipliers[namespace]

    def set_multiplier(
        self,
        namespace: str,
        multiplier: DomainAwareCrisisMultiplier,
    ) -> None:
        """네임스페이스별 Multiplier 설정."""
        self._multipliers[namespace] = multiplier

    def remove(self, namespace: str) -> bool:
        """네임스페이스 Multiplier 제거."""
        if namespace in self._multipliers:
            del self._multipliers[namespace]
            return True
        return False

    def list_namespaces(self) -> list:
        """등록된 네임스페이스 목록."""
        return list(self._multipliers.keys())
