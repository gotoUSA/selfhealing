"""
Namespace-Aware Emergency.

리전별 긴급 모드 격리를 위한 컴포넌트.

Phase 1 (P0):
- FailFastClusterIdentity: 리전 식별자 필수 검증 (core/cluster_identity.py)
- AtomicStateQuery: Lua 스크립트 기반 원자적 Global+Regional 조회
- EscalationAuditTrail: 오버라이드 의사결정 Audit 로그

Reference:
    docs/self_healing/middleware_system/73_NAMESPACE_AWARE_EMERGENCY.md
"""

from selfhealing.services.namespace_emergency.atomic_query import (
    AtomicStateQuery,
    get_atomic_state_query,
)
from selfhealing.services.namespace_emergency.escalation_audit import (
    EscalationAuditTrail,
    EscalationDecisionType,
    EscalationAuditEntry,
    get_escalation_audit_trail,
)

__all__ = [
    "AtomicStateQuery",
    "get_atomic_state_query",
    "EscalationAuditTrail",
    "EscalationDecisionType",
    "EscalationAuditEntry",
    "get_escalation_audit_trail",
]
