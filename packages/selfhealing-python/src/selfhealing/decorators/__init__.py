"""
Selfhealing Decorators Package.

기능 향상을 위한 데코레이터 모음.

Decorators:
- @domain_tag: 에러 발생 시 도메인 자동 태깅
- DomainContext: with 문 기반 도메인 컨텍스트

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md
"""

from selfhealing.decorators.domain_tag import (
    DomainContext,
    clear_domain_context,
    domain_tag,
    get_current_domain,
)

__all__ = [
    "domain_tag",
    "DomainContext",
    "get_current_domain",
    "clear_domain_context",
]
