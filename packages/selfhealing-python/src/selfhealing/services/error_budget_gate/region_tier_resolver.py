"""
리전-티어 매핑 해석기.

RegionalRecoveryConfig.priority 기반으로 리전의 서비스 중요도(티어)를 추론한다.
API 경로 기반 티어 결정(TierRegistry.resolve_tier)과 달리,
이 모듈은 리전 자체의 인프라 중요도를 기반으로 티어를 결정한다.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

# RegionalRecoveryConfig.priority → 티어 매핑 범위
# seoul(100) → critical, tokyo(50) → standard, oregon(10) → non_essential
_PRIORITY_TIER_RANGES: list[tuple[int, str]] = [
    (70, "critical"),  # priority >= 70
    (30, "standard"),  # 30 <= priority < 70
    (0, "non_essential"),  # priority < 30
]

_DEFAULT_TIER = "standard"


def resolve_tier_from_region(region: str) -> str:
    """
    리전의 인프라 중요도에서 티어를 추론한다.

    RegionalRecoveryConfig.priority 값을 기반으로 매핑:
    - priority >= 70: "critical" (예: seoul)
    - 30 <= priority < 70: "standard" (예: tokyo)
    - priority < 30: "non_essential" (예: oregon)

    Args:
        region: 리전 식별자 (ClusterIdentity.region 값)

    Returns:
        티어 식별자 ("critical" | "standard" | "non_essential")
    """
    try:
        from selfhealing.services.coordination.regional_recovery_policy import (
            get_default_regional_configs,
        )

        configs = get_default_regional_configs()
        config = configs.get(region)
        if config is None:
            return _DEFAULT_TIER

        for min_priority, tier_id in _PRIORITY_TIER_RANGES:
            if config.priority >= min_priority:
                return tier_id

        return _DEFAULT_TIER

    except Exception as e:
        logger.warning(
            "region_tier_resolver.failed_resolve_tier_region",
            region=region,
            error=e,
        )
        return _DEFAULT_TIER


__all__ = [
    "resolve_tier_from_region",
]
