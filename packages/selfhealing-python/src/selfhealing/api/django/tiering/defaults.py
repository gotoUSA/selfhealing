"""
Default Tiering Configuration Templates.

Best practice defaults for tier definitions, mappings, and overrides.
"""

from __future__ import annotations

from .enums import OverrideIdentifierType, TierMatchType
from .models import TierDefinition, TierMapping, TierOverride

# =============================================================================
# L1: Static Critical Paths (Defense-in-Depth - Last Line of Defense)
# =============================================================================

# Immutable set - requires code deployment to change
STATIC_CRITICAL_PATHS = frozenset(
    [
        "/api/self-healing/control/",
        "/api/self-healing/emergency/",
        "/api/auth/token/",
    ]
)

# Prefix matching optimization (tuple for startswith)
STATIC_CRITICAL_PREFIXES = (
    "/api/self-healing/control/",
    "/api/self-healing/emergency/",
)


# =============================================================================
# Default Templates (Best Practices)
# =============================================================================


DEFAULT_TIER_DEFINITIONS: list[TierDefinition] = [
    TierDefinition(
        id="critical",
        name="Mission Critical",
        multiplier=0.5,  # 50% allowed in emergency
        priority=100,
        description="장애 시에도 반드시 동작해야 하는 핵심 API",
        color="#FF0000",
    ),
    TierDefinition(
        id="standard",
        name="Operational",
        multiplier=0.1,  # 10% allowed in emergency
        priority=50,
        description="일반 운영 API",
        color="#FFA500",
    ),
    TierDefinition(
        id="non_essential",
        name="Non-Essential",
        multiplier=0.0,  # Blocked in emergency
        priority=10,
        description="비필수 API (Load Shedding 대상)",
        color="#808080",
    ),
]


DEFAULT_TIER_MAPPINGS: list[TierMapping] = [
    # Critical (Tier 1) - Self-healing control actions
    TierMapping(
        pattern="/api/self-healing/control/",
        tier_id="critical",
        pattern_type=TierMatchType.EXACT,
        priority=100,
        description="자가치유 제어 액션",
    ),
    TierMapping(
        pattern="/api/self-healing/allow/*",
        tier_id="critical",
        pattern_type=TierMatchType.WILDCARD,
        priority=100,
        description="자가치유 허용 액션",
    ),
    TierMapping(
        pattern="/api/self-healing/block/*",
        tier_id="critical",
        pattern_type=TierMatchType.WILDCARD,
        priority=100,
        description="자가치유 차단 액션",
    ),
    TierMapping(
        pattern="/api/self-healing/system/*",
        tier_id="critical",
        pattern_type=TierMatchType.WILDCARD,
        priority=95,
        description="킬 스위치 등 시스템 제어",
    ),
    # Standard (Tier 2) - Operational tasks
    TierMapping(
        pattern="/api/self-healing/config/*",
        tier_id="standard",
        pattern_type=TierMatchType.WILDCARD,
        priority=50,
        description="설정 변경 API",
    ),
    TierMapping(
        pattern="/api/self-healing/dlq/*",
        tier_id="standard",
        pattern_type=TierMatchType.WILDCARD,
        priority=50,
        description="DLQ 관련 API",
    ),
    TierMapping(
        pattern="/api/self-healing/audit/",
        tier_id="standard",
        pattern_type=TierMatchType.EXACT,
        priority=50,
        description="감사 로그 조회",
    ),
    TierMapping(
        pattern="/api/self-healing/status/*",
        tier_id="standard",
        pattern_type=TierMatchType.WILDCARD,
        priority=50,
        description="상태 조회",
    ),
    # Non-Essential (Tier 3) - Dashboard, metrics
    TierMapping(
        pattern="/api/self-healing/dashboard/*",
        tier_id="non_essential",
        pattern_type=TierMatchType.WILDCARD,
        priority=10,
        description="대시보드 API",
    ),
    TierMapping(
        pattern="/api/self-healing/metrics/",
        tier_id="non_essential",
        pattern_type=TierMatchType.EXACT,
        priority=10,
        description="메트릭 조회 API",
    ),
    TierMapping(
        pattern=r"/api/self-healing/chaos/reports/.*",
        tier_id="non_essential",
        pattern_type=TierMatchType.REGEX,
        priority=10,
        description="카오스 리포트 API",
    ),
]


DEFAULT_TIER_OVERRIDES: list[TierOverride] = [
    TierOverride(
        identifier="10.0.0.0/8",
        identifier_type=OverrideIdentifierType.IP,
        tier_id="critical",
        reason="Internal monitoring system",
    ),
    TierOverride(
        identifier="172.16.0.0/12",
        identifier_type=OverrideIdentifierType.IP,
        tier_id="critical",
        reason="Internal network",
    ),
    TierOverride(
        identifier="192.168.0.0/16",
        identifier_type=OverrideIdentifierType.IP,
        tier_id="critical",
        reason="Internal network",
    ),
]
