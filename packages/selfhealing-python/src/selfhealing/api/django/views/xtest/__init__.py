"""
X-Test-Mode (Chaos Monkey) Control Views Package

Rate Limiter(L1)를 우회하여 L2/L3 동작을 직접 관찰하기 위한 테스트 전용 API.

Security:
- X-Test-Mode: chaos-monkey 헤더 필수
- DEBUG 또는 CHAOS_ENABLED 환경 변수 필요
- production 환경에서는 완전 차단

Endpoints:
- POST /api/self-healing/xtest/inject-cb-failure/ - CB 장애 주입
- POST /api/self-healing/xtest/reset-cb/ - CB 상태 초기화
- GET  /api/self-healing/xtest/cb-status/ - CB 상태 확인 (상세)
- POST /api/self-healing/xtest/inject-error-budget/ - Error Budget 차감
- GET  /api/self-healing/xtest/snapshot/ - 시스템 스냅샷
- GET  /api/self-healing/xtest/fast-fail-test/ - Fast Fail 검증
- POST /api/self-healing/xtest/trigger-cb-recovery/ - CB 복구 트리거

Stage 51 Observability:
- GET  /api/self-healing/xtest/healing-timeline/ - 힐링 타임라인
- POST /api/self-healing/xtest/blast-radius-test/ - Blast Radius 테스트
- POST /api/self-healing/xtest/multi-blast-radius/ - 다중 서비스 격리 매트릭스
- POST /api/self-healing/xtest/generate-postmortem/ - Post-mortem 생성
- POST /api/self-healing/xtest/record-healing-event/ - 힐링 이벤트 기록
- GET  /api/self-healing/xtest/healing-incidents/ - 인시던트 목록

DLQ Test Endpoints:
- POST /api/self-healing/xtest/dlq/inject/ - DLQ 테스트 항목 생성
- GET  /api/self-healing/xtest/dlq/status/ - DLQ 현황 조회
- POST /api/self-healing/xtest/dlq/force-status/ - DLQ 상태 강제 변경
- POST /api/self-healing/xtest/dlq/reset/ - X-Test-Mode 생성 항목 초기화
"""

# Base utilities
from .base import (
    XTestModeMixin,
    add_healing_event,
    add_healing_incident,
    collect_system_snapshot,
    get_healing_events,
    get_healing_events_count,
    get_healing_incidents,
    get_healing_incidents_count,
)

# Circuit Breaker views
from .circuit_breaker import (
    CBStatusDetailView,
    FastFailTestView,
    InjectCBFailureView,
    ResetCBView,
    TriggerCBRecoveryView,
    TryRecoveryTransitionView,  # Domain-free OPEN → HALF_OPEN transition
    SwitchToAutoModeView,  # New! For releasing manually_controlled state
)

# Error Budget views
from .error_budget import (
    InjectErrorBudgetView,
)

# Snapshot views
from .snapshot import (
    SystemSnapshotView,
)

# Observability views (Stage 51)
from .observability import (
    BlastRadiusTestView,
    GetHealingIncidentsView,
    HealingTimelineView,
    MultiServiceBlastRadiusView,
    PostmortemGeneratorView,
    RecordHealingEventView,
)

# DLQ X-Test views
from .dlq import (
    InjectDLQEntryView,
    DLQXTestStatusView,
    ForceStatusView,
    ResetDLQXTestView,
)

# Replay X-Test views
from .replay import (
    ReplaySingleView,
    ReplayBatchView,
    TriggerReplayOnCBCloseView,
    ReplayStatusView,
)

# Retry X-Test views
from .retry import (
    BackoffPreviewView,
    RetrySimulateView,
    RetryRateLimitStatusView,
    RetryConfigView,
)

# Rate Limit X-Test views
from .rate_limit import (
    RateLimitStatusView,
    RateLimitClientView,
    RateLimitHistoryView,
    RateLimitConfigXTestView,
    RateLimitResetView,
)

# Legacy aliases for backward compatibility
_collect_system_snapshot = collect_system_snapshot
_add_healing_event = add_healing_event
_add_healing_incident = add_healing_incident

__all__ = [
    # Base utilities
    "XTestModeMixin",
    "collect_system_snapshot",
    "add_healing_event",
    "add_healing_incident",
    "get_healing_events",
    "get_healing_events_count",
    "get_healing_incidents",
    "get_healing_incidents_count",
    # Circuit Breaker views
    "InjectCBFailureView",
    "ResetCBView",
    "CBStatusDetailView",
    "FastFailTestView",
    "TriggerCBRecoveryView",
    "TryRecoveryTransitionView",  # Domain-free OPEN → HALF_OPEN
    "SwitchToAutoModeView",  # New!
    # Error Budget views
    "InjectErrorBudgetView",
    # Snapshot views
    "SystemSnapshotView",
    # Observability views (Stage 51)
    "HealingTimelineView",
    "BlastRadiusTestView",
    "MultiServiceBlastRadiusView",
    "PostmortemGeneratorView",
    "RecordHealingEventView",
    "GetHealingIncidentsView",
    # DLQ X-Test views
    "InjectDLQEntryView",
    "DLQXTestStatusView",
    "ForceStatusView",
    "ResetDLQXTestView",
    # Replay X-Test views
    "ReplaySingleView",
    "ReplayBatchView",
    "TriggerReplayOnCBCloseView",
    "ReplayStatusView",
    # Retry X-Test views
    "BackoffPreviewView",
    "RetrySimulateView",
    "RetryRateLimitStatusView",
    "RetryConfigView",
    # Rate Limit X-Test views
    "RateLimitStatusView",
    "RateLimitClientView",
    "RateLimitHistoryView",
    "RateLimitConfigXTestView",
    "RateLimitResetView",
    # Legacy aliases
    "_collect_system_snapshot",
    "_add_healing_event",
    "_add_healing_incident",
]
