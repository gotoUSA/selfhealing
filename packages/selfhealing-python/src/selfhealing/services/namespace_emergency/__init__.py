"""
Namespace-Aware Emergency.

리전별 긴급 모드 격리를 위한 컴포넌트.

Phase 1 (P0) - 완료:
- FailFastClusterIdentity: 리전 식별자 필수 검증 (core/cluster_identity.py)
- AtomicStateQuery: Lua 스크립트 기반 원자적 Global+Regional 조회
- EscalationAuditTrail: 오버라이드 의사결정 Audit 로그

Phase 2 (P1) - 완료:
- NamespacedEmergencyTracker: 네임스페이스별 Emergency 상태 관리
- ScopedEmergencyState: 스코프 인지형 상태 모델 (coordination/models.py)
- RegionalCascadeDetector: 다중 리전 연쇄 장애 감지

Phase 3 (P2) - 완료:
- EmergencyHealthPenalty: Health Score 연동 감점 계산
- PartitionReconciliationService: 네트워크 고립 복구

Reference:
    docs/self_healing/middleware_system/73_NAMESPACE_AWARE_EMERGENCY.md
"""

# ScopedEmergencyState는 coordination/models.py에서 재export
from selfhealing.services.coordination.models import ScopedEmergencyState

# Phase 1: 안전 기반
from selfhealing.services.namespace_emergency.atomic_query import (
    AtomicStateQuery,
    get_atomic_state_query,
)
from selfhealing.services.namespace_emergency.cascade_detector import (
    CascadeDetectionEvent,
    RegionalCascadeDetector,
    get_cascade_detector,
    reset_cascade_detector,
)
from selfhealing.services.namespace_emergency.escalation_audit import (
    EscalationAuditEntry,
    EscalationAuditTrail,
    EscalationDecisionType,
    get_escalation_audit_trail,
)

# Phase 3: 고급 기능
from selfhealing.services.namespace_emergency.health_penalty import (
    EmergencyHealthPenalty,
    PenaltyBreakdown,
    get_emergency_health_penalty,
    reset_emergency_health_penalty,
)
from selfhealing.services.namespace_emergency.partition_reconciliation import (
    PartitionReconciliationService,
    PartitionStatus,
    ReconciliationAction,
    ReconciliationResult,
    get_partition_reconciliation_service,
    reset_partition_reconciliation_service,
)

# Phase 2: 핵심 기능
from selfhealing.services.namespace_emergency.tracker import (
    GLOBAL_NAMESPACE,
    NamespacedEmergencyTracker,
    get_namespaced_emergency_tracker,
    reset_namespaced_emergency_tracker,
)

__all__ = [
    # Phase 1
    "AtomicStateQuery",
    "get_atomic_state_query",
    "EscalationAuditTrail",
    "EscalationDecisionType",
    "EscalationAuditEntry",
    "get_escalation_audit_trail",
    # Phase 2
    "NamespacedEmergencyTracker",
    "get_namespaced_emergency_tracker",
    "reset_namespaced_emergency_tracker",
    "GLOBAL_NAMESPACE",
    "RegionalCascadeDetector",
    "CascadeDetectionEvent",
    "get_cascade_detector",
    "reset_cascade_detector",
    "ScopedEmergencyState",
    # Phase 3
    "EmergencyHealthPenalty",
    "PenaltyBreakdown",
    "get_emergency_health_penalty",
    "reset_emergency_health_penalty",
    "PartitionReconciliationService",
    "PartitionStatus",
    "ReconciliationResult",
    "ReconciliationAction",
    "get_partition_reconciliation_service",
    "reset_partition_reconciliation_service",
]
