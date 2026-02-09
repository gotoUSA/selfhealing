"""
Load Shedding criticality ↔ Throttle tier_id 매핑 유틸리티.

ServiceConfig.criticality ("critical"|"high"|"medium"|"low") 값과
AdaptiveThrottle.check()의 tier_id ("critical"|"standard"|"non_essential") 간 변환을 제공한다.

ServiceConfig.criticality 유효값:
    models.py ServiceConfig.__post_init__() → valid_levels = {"critical", "high", "medium", "low"}

AdaptiveThrottle tier_id 유효값:
    adaptive.py PROTECTED_TIERS_ON_429 = {"critical"}
    adaptive.py check() tier_id 파라미터 기본값 = "standard"
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ServiceConfig.criticality → AdaptiveThrottle tier_id
# "high"는 critical tier로 보호 (models.py valid_levels에 포함)
CRITICALITY_TO_TIER: dict[str, str] = {
    "critical": "critical",
    "high": "critical",
    "medium": "standard",
    "low": "non_essential",
}

# AdaptiveThrottle tier_id → ServiceConfig.criticality
TIER_TO_CRITICALITY: dict[str, str] = {
    "critical": "critical",
    "standard": "medium",
    "non_essential": "low",
}

# AdaptiveThrottle tier_id 유효값 집합
VALID_TIER_IDS: set[str] = {"critical", "standard", "non_essential"}

_DEFAULT_TIER: str = "standard"
_DEFAULT_CRITICALITY: str = "medium"


def get_tier_from_criticality(criticality: str) -> str:
    """
    ServiceConfig.criticality를 AdaptiveThrottle tier_id로 변환.

    Args:
        criticality: "critical" | "high" | "medium" | "low"

    Returns:
        tier_id: "critical" | "standard" | "non_essential"
    """
    tier = CRITICALITY_TO_TIER.get(criticality.lower(), _DEFAULT_TIER)
    if criticality.lower() not in CRITICALITY_TO_TIER:
        logger.warning(f"[TierMapping] Unknown criticality '{criticality}', " f"falling back to '{_DEFAULT_TIER}'")
    return tier


def get_criticality_from_tier(tier_id: str) -> str:
    """
    AdaptiveThrottle tier_id를 ServiceConfig.criticality로 변환.

    Args:
        tier_id: "critical" | "standard" | "non_essential"

    Returns:
        criticality: "critical" | "medium" | "low"
    """
    criticality = TIER_TO_CRITICALITY.get(tier_id.lower(), _DEFAULT_CRITICALITY)
    if tier_id.lower() not in TIER_TO_CRITICALITY:
        logger.warning(f"[TierMapping] Unknown tier_id '{tier_id}', " f"falling back to '{_DEFAULT_CRITICALITY}'")
    return criticality
