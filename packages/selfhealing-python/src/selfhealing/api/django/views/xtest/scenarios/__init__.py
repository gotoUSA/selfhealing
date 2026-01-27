"""
X-Test 통합 테스트 시나리오 패키지.

도메인별로 분리된 시나리오 모듈을 통합하여 제공합니다.
"""

from typing import Dict, List, Optional

# Base classes and models
from .base import (
    ScenarioStatus,
    ScenarioStep,
    TimelineEvent,
    ScenarioResult,
    IntegrationScenario,
    store_scenario_result,
    get_scenario_result,
    clear_scenario_results,
)

# Circuit Breaker scenarios
from .circuit_breaker import (
    CBOpenDLQScenario,
)

# DLQ and Replay scenarios
from .dlq_replay import (
    RetryExhaustScenario,
    RateLimitRetryScenario,
    DLQReplaySuccessScenario,
    DLQReplayFailureScenario,
    IdempotentReplayScenario,
)

# Recovery scenarios
from .recovery import (
    FullRecoveryScenario,
)

# Emergency scenarios
from .emergency import (
    FullEmergencyRecoveryScenario,
    SafetyInterlockCanaryRollbackScenario,
)

# Regional scenarios (144 문서 구현)
from .regional import (
    RegionalOverrideConflictScenario,
    MultiRegionIsolationTestScenario,
)


# =============================================================================
# 시나리오 레지스트리
# =============================================================================


SCENARIO_REGISTRY: Dict[str, type] = {
    # Circuit Breaker scenarios
    "cb_open_dlq_flow": CBOpenDLQScenario,
    
    # DLQ and Replay scenarios
    "retry_exhaust_dlq": RetryExhaustScenario,
    "rate_limit_retry": RateLimitRetryScenario,
    "dlq_replay_success": DLQReplaySuccessScenario,
    "dlq_replay_failure": DLQReplayFailureScenario,
    "idempotent_replay": IdempotentReplayScenario,
    
    # Recovery scenarios
    "full_recovery_cycle": FullRecoveryScenario,
    
    # Emergency scenarios
    "full_emergency_recovery_flow": FullEmergencyRecoveryScenario,
    "safety_interlock_canary_rollback": SafetyInterlockCanaryRollbackScenario,
    
    # Regional scenarios (144 문서 구현)
    "regional_override_conflict": RegionalOverrideConflictScenario,
    "multi_region_isolation_test": MultiRegionIsolationTestScenario,
}


def get_scenario_class(scenario_name: str) -> Optional[type]:
    """시나리오 이름으로 클래스 조회."""
    return SCENARIO_REGISTRY.get(scenario_name)


def list_available_scenarios() -> List[str]:
    """사용 가능한 시나리오 목록 반환."""
    return list(SCENARIO_REGISTRY.keys())


__all__ = [
    # Base classes and models
    "ScenarioStatus",
    "ScenarioStep",
    "TimelineEvent",
    "ScenarioResult",
    "IntegrationScenario",
    "store_scenario_result",
    "get_scenario_result",
    "clear_scenario_results",
    
    # Scenario classes
    "CBOpenDLQScenario",
    "RetryExhaustScenario",
    "RateLimitRetryScenario",
    "DLQReplaySuccessScenario",
    "DLQReplayFailureScenario",
    "IdempotentReplayScenario",
    "FullRecoveryScenario",
    "FullEmergencyRecoveryScenario",
    "SafetyInterlockCanaryRollbackScenario",
    "RegionalOverrideConflictScenario",
    "MultiRegionIsolationTestScenario",
    
    # Registry and helpers
    "SCENARIO_REGISTRY",
    "get_scenario_class",
    "list_available_scenarios",
]
