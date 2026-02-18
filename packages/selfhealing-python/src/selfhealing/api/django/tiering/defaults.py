"""
Default Tiering Configuration Templates.

Best practice defaults for tier definitions, mappings, and overrides.
Backpressure Level별 tier 배율 규칙도 포함.
"""

from __future__ import annotations

from selfhealing.scaling.config import BackpressureLevel

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
    # =========================================================================
    # Method-Specific Mappings (method+path 조합이 path-only보다 우선)
    # =========================================================================
    # POST/PUT/PATCH/DELETE /config/* → critical (설정 변경은 치유 동작)
    TierMapping(
        pattern="/api/self-healing/config/*",
        tier_id="critical",
        pattern_type=TierMatchType.WILDCARD,
        priority=70,
        description="설정 변경 API (쓰기)",
        methods=frozenset({"POST", "PUT", "PATCH", "DELETE"}),
    ),
    # POST/PUT/DELETE /dlq/* → critical (DLQ 재처리는 치유 동작)
    TierMapping(
        pattern="/api/self-healing/dlq/*",
        tier_id="critical",
        pattern_type=TierMatchType.WILDCARD,
        priority=70,
        description="DLQ 재처리 (쓰기)",
        methods=frozenset({"POST", "PUT", "DELETE"}),
    ),
    # GET/HEAD /config/* → non_essential (설정 조회는 비필수)
    TierMapping(
        pattern="/api/self-healing/config/*",
        tier_id="non_essential",
        pattern_type=TierMatchType.WILDCARD,
        priority=55,
        description="설정 조회 API (읽기)",
        methods=frozenset({"GET", "HEAD"}),
    ),
    # =========================================================================
    # Path-Only Mappings (모든 HTTP 메서드에 적용)
    # =========================================================================
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


# =============================================================================
# Backpressure Level별 Tier 트래픽 배율 규칙
# =============================================================================
# Emergency Mode의 EMERGENCY_LEVEL_RULES(services/emergency_mode/enums.py)와
# 동일한 패턴. TieringMiddleware에서 Most Restrictive Wins 병합에 사용.
# 값이 클수록 더 많은 트래픽 허용 (1.0=전부 허용, 0.0=전부 차단).

BACKPRESSURE_TIER_RULES: dict[BackpressureLevel, dict[str, float]] = {
    BackpressureLevel.NONE: {"critical": 1.0, "standard": 1.0, "non_essential": 1.0},
    BackpressureLevel.LOW: {"critical": 1.0, "standard": 1.0, "non_essential": 0.5},
    BackpressureLevel.MEDIUM: {"critical": 1.0, "standard": 0.8, "non_essential": 0.2},
    BackpressureLevel.HIGH: {"critical": 1.0, "standard": 0.5, "non_essential": 0.05},
    BackpressureLevel.CRITICAL: {"critical": 0.8, "standard": 0.1, "non_essential": 0.02},
}
