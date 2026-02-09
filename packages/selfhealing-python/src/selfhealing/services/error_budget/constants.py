"""
Error Budget Constants (SSOT).

Crisis Multiplier 및 Error Budget 관련 통합 상수 정의.
모든 컴포넌트가 참조하는 단일 진실 공급원(Single Source of Truth).

Features:
- 최대 가중치 Cap 정의
- 도메인별 기본 민감도 정의
- Emergency Level별 기본 가중치 정의

Usage:
    from selfhealing.services.error_budget.constants import (
        MAX_CRISIS_MULTIPLIER_CAP,
        MAX_DOMAIN_MULTIPLIER,
        DEFAULT_LEVEL_MULTIPLIERS,
    )

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §0.1 (11번)
    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 4 [23] DomainSensitivitySettings 참조.
"""

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.settings.domain_sensitivity import get_domain_sensitivity_settings

# =============================================================================
# 최대 가중치 Cap (SSOT)
# =============================================================================

MAX_CRISIS_MULTIPLIER_CAP: float = 10.0
"""
Emergency Level 기반 최대 가중치 Cap.

LEVEL_3 × 도메인 가중치 결합 후에도 이 값을 초과할 수 없음.
Code reference: coordination/crisis_multiplier.py#L42
"""

MAX_DOMAIN_MULTIPLIER: float = 24.0
"""
도메인 기반 최대 가중치.

SLA 기반 역수 가중치의 최대값 (1h SLA = 24.0).
Code reference: shadow_calculator.py#L245
"""

MAX_COMBINED_MULTIPLIER: float = 10.0
"""
Level + Domain 결합 후 최대 가중치.

MultiplierPrecedenceResolver에서 사용.
"""


# =============================================================================
# 기본 캐시 TTL
# =============================================================================

DEFAULT_CACHE_TTL_SECONDS: float = 30.0
"""
CrisisMultiplierProvider 기본 캐시 TTL (30초).

격상 시 EscalationTriggeredInvalidation으로 즉시 무효화됨.
"""


# =============================================================================
# Emergency Level별 기본 가중치
# =============================================================================


def get_level_multipliers() -> dict[EmergencyLevel, float]:
    """DomainSensitivitySettings에서 레벨 승수 가져오기."""
    settings = get_domain_sensitivity_settings()
    return {
        EmergencyLevel.NORMAL: settings.level_multiplier_normal,
        EmergencyLevel.LEVEL_1: settings.level_multiplier_level_1,
        EmergencyLevel.LEVEL_2: settings.level_multiplier_level_2,
        EmergencyLevel.LEVEL_3: settings.level_multiplier_level_3,
    }


# Backward compatibility: settings 기반 동적 평가
DEFAULT_LEVEL_MULTIPLIERS: dict[EmergencyLevel, float] = get_level_multipliers()
"""
Emergency Level별 Error Budget 소진 가중치.
Note: 설정 기반 동적 값. 런타임 설정은 get_level_multipliers() 사용.
"""


# =============================================================================
# 도메인별 기본 민감도
# =============================================================================


def get_domain_sensitivity() -> dict[str, float]:
    """DomainSensitivitySettings에서 도메인 민감도 가져오기."""
    settings = get_domain_sensitivity_settings()
    return settings.as_domain_dict()


# Backward compatibility: settings 기반 동적 평가
DEFAULT_DOMAIN_SENSITIVITY: dict[str, float] = get_domain_sensitivity()
"""
도메인별 민감도 가중치.
Note: 설정 기반 동적 값. 런타임 설정은 get_domain_sensitivity() 사용.
"""


# =============================================================================
# 도메인 전파 설정
# =============================================================================

DEFAULT_PROPAGATION_DECAY: float = 0.5
"""
홉당 감쇠율.

1-hop: 50% 감쇠, 2-hop: 25% 감쇠.
"""

DEFAULT_PROPAGATION_MAX_HOPS: int = 3
"""
최대 전파 홉 수.

순환 참조 방지 및 성능을 위한 깊이 제한.
리뷰 §3.2.3 반영.
"""


# =============================================================================
# 환불 설정
# =============================================================================

DEFAULT_REFUND_RATIO: float = 0.5
"""
오탐 시 기본 환불 비율 (50%).

100% 환불은 시스템 요동 유발 가능.
"""

REFUND_PROPOSAL_EXPIRY_HOURS: int = 24
"""
환불 제안 만료 시간 (24시간).

미처리 제안은 자동 만료됨.
"""


# =============================================================================
# 가중치 결합 전략 기본값
# =============================================================================

DEFAULT_COMBINE_STRATEGY: str = "max"
"""
Level/Domain 가중치 결합 기본 전략.

'max': 둘 중 큰 값 사용 (보수적)
'sum': 합산 (위험)
'multiply': 곱셈 (매우 위험, Cap 필수)
"""
