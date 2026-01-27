"""
X-Test-Mode Integration Test Scenarios

Self-Healing 컴포넌트들의 상호 연동을 검증하기 위한 통합 테스트 시나리오 정의.

** 주의: 이 파일은 하위 호환성을 위해 유지됩니다. **
** 새로운 시나리오는 scenarios/ 패키지에 추가하세요. **

Scenarios:
- cb_open_dlq_flow: Circuit Breaker Open → DLQ 저장 플로우
- retry_exhaust_dlq: Retry 소진 → DLQ 플로우
- rate_limit_retry: Rate Limit → Retry 백오프
- dlq_replay_success: DLQ → Replay 성공
- dlq_replay_failure: DLQ → Replay 실패 → 재DLQ
- full_recovery_cycle: 전체 장애 → 복구 사이클
- idempotent_replay: Replay 멱등성 보장
- full_emergency_recovery_flow: Emergency LEVEL_3 → 복구 사이클
- safety_interlock_canary_rollback: SafetyInterlock 에스컬레이션
- regional_override_conflict: Global vs Regional 상태 우선순위 (144 문서)
- multi_region_isolation_test: 다중 리전 격리 검증 (144 문서)

Security:
- X-Test-Mode: chaos-monkey 헤더 필수
- DEBUG 또는 CHAOS_ENABLED 환경 변수 필요
- production 환경에서는 완전 차단

Refactored Structure:
- scenarios/base.py: 공통 모델 및 IntegrationScenario ABC
- scenarios/circuit_breaker.py: CB 관련 시나리오
- scenarios/dlq_replay.py: DLQ/Replay 관련 시나리오
- scenarios/recovery.py: Recovery 사이클 시나리오
- scenarios/emergency.py: Emergency/SafetyInterlock 시나리오
- scenarios/regional.py: Regional 격리 시나리오 (144 문서)
"""

from __future__ import annotations

# =============================================================================
# 하위 호환성을 위해 scenarios 패키지에서 모든 클래스 재export
# =============================================================================

from .scenarios import (
    # Base classes and models
    ScenarioStatus,
    ScenarioStep,
    TimelineEvent,
    ScenarioResult,
    IntegrationScenario,
    store_scenario_result,
    get_scenario_result,
    clear_scenario_results,
    
    # Scenario classes
    CBOpenDLQScenario,
    RetryExhaustScenario,
    RateLimitRetryScenario,
    DLQReplaySuccessScenario,
    DLQReplayFailureScenario,
    IdempotentReplayScenario,
    FullRecoveryScenario,
    FullEmergencyRecoveryScenario,
    SafetyInterlockCanaryRollbackScenario,
    RegionalOverrideConflictScenario,
    MultiRegionIsolationTestScenario,
    
    # Registry and helpers
    SCENARIO_REGISTRY,
    get_scenario_class,
    list_available_scenarios,
)

# System snapshot function (still needed for views)
# collect_system_snapshot is in the xtest/base.py module
from .base import collect_system_snapshot


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
    "collect_system_snapshot",
    
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
