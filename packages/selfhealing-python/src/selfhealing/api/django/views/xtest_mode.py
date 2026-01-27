"""
Stage 48: X-Test-Mode (Chaos Monkey) Control Views

DEPRECATED: 이 파일은 레거시 호환성을 위해서만 유지됩니다.
새 코드에서는 직접 import를 사용하세요:

    # 권장 (직접 import)
    from selfhealing.api.django.views.xtest import XTestModeMixin, InjectCBFailureView

    # 비권장 (레거시 re-export)
    from selfhealing.api.django.views.xtest_mode import XTestModeMixin, InjectCBFailureView

실제 구현은 xtest/ 패키지에 있습니다.
이 파일은 향후 제거될 예정입니다.

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

# Re-export all from xtest package for backward compatibility
from .xtest import (  # Base utilities; Circuit Breaker views; Error Budget views; Snapshot views; Observability views (Stage 51); DLQ X-Test views; Legacy aliases
    BlastRadiusTestView,
    CBStatusDetailView,
    DLQXTestStatusView,
    FastFailTestView,
    ForceStatusView,
    GetHealingIncidentsView,
    HealingTimelineView,
    InjectCBFailureView,
    InjectDLQEntryView,
    InjectErrorBudgetView,
    MultiServiceBlastRadiusView,
    PostmortemGeneratorView,
    RecordHealingEventView,
    ResetCBView,
    ResetDLQXTestView,
    SwitchToAutoModeView,  # New! For releasing manually_controlled state
    SystemSnapshotView,
    TriggerCBRecoveryView,
    TryRecoveryTransitionView,  # Domain-free OPEN → HALF_OPEN transition
    XTestModeMixin,
    _add_healing_event,
    _add_healing_incident,
    _collect_system_snapshot,
    add_healing_event,
    add_healing_incident,
    collect_system_snapshot,
    get_healing_events,
    get_healing_events_count,
    get_healing_incidents,
    get_healing_incidents_count,
)

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
    # Legacy aliases
    "_collect_system_snapshot",
    "_add_healing_event",
    "_add_healing_incident",
]
