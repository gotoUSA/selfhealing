"""
Meta-Watchdog Module.

Self-Healing 시스템 자체의 건강 상태를 모니터링하고,
장애 시 자동 복구 또는 인간 에스컬레이션을 수행합니다.

핵심 컴포넌트:
- MetaWatchdogSettings: 설정 관리
- HealthProbeManager: 서브시스템 건강 상태 수집
- StuckDetector: Zero-variance 기반 Stuck 감지
- EscalationManager: 인간 개입 요청 (PagerDuty, Slack)
- SelfHealerWatchdog: 통합 Watchdog

사용 예시:
    from selfhealing.meta.watchdog import get_selfhealer_watchdog

    watchdog = get_selfhealer_watchdog()
    watchdog.start()

    state = watchdog.get_state()
    print(f"Overall: {state.overall_status}")

    watchdog.stop()
"""

from selfhealing.meta.config import (
    MetaWatchdogSettings,
    get_meta_watchdog_settings,
    reset_meta_watchdog_settings,
)
from selfhealing.meta.escalation import (
    EscalationEvent,
    EscalationLevel,
    EscalationManager,
)
from selfhealing.meta.health_probe import (
    HealthProbe,
    HealthProbeManager,
    HealthStatus,
    ProbeResult,
)
from selfhealing.meta.stuck_detector import (
    StuckDetectionResult,
    StuckDetector,
    get_stuck_detector,
)
from selfhealing.meta.watchdog import (
    SelfHealerWatchdog,
    WatchdogState,
    get_selfhealer_watchdog,
    reset_selfhealer_watchdog,
)

__all__ = [
    # Config
    "MetaWatchdogSettings",
    "get_meta_watchdog_settings",
    "reset_meta_watchdog_settings",
    # Health Probe
    "HealthStatus",
    "ProbeResult",
    "HealthProbe",
    "HealthProbeManager",
    # Stuck Detector
    "StuckDetectionResult",
    "StuckDetector",
    "get_stuck_detector",
    # Escalation
    "EscalationLevel",
    "EscalationEvent",
    "EscalationManager",
    # Watchdog
    "WatchdogState",
    "SelfHealerWatchdog",
    "get_selfhealer_watchdog",
    "reset_selfhealer_watchdog",
]
