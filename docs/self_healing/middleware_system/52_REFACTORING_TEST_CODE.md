# 52. 테스트 코드 분리 전략 상세

> 작성일: 2026-01-18  
> 대상: `packages/selfhealing-python/tests`

---

## 1. 테스트 파일 분리 원칙

### 1.1 긴 테스트 파일의 문제점

| 문제 | 영향 |
|-----|------|
| 테스트 실행 시간 증가 | 단일 파일 변경 시 불필요한 테스트 실행 |
| 코드 탐색 어려움 | 관련 테스트 찾기 힘듦 |
| 병렬 실행 비효율 | pytest-xdist 활용도 저하 |
| 리뷰 부담 증가 | PR에서 관련 테스트만 확인 어려움 |
| 책임 범위 불명확 | 어떤 기능을 테스트하는지 모호 |

### 1.2 분리 기준

| 항목 | 권장 | 최대 허용 |
|-----|-----|---------|
| 파일당 줄 수 | 200~300줄 | 400줄 |
| 테스트 클래스 수 | 2~4개 | 6개 |
| 테스트 메서드 수 | 10~20개 | 30개 |

### 1.3 테스트 분리 유형

1. **대상별 분리**: 테스트 대상 클래스/모듈별로 파일 분리
2. **시나리오별 분리**: 동일 대상의 다른 시나리오별 분리
3. **Mock 분리**: 공통 Mock 클래스를 conftest.py 또는 별도 파일로

---

## 2. test_audit_forensic_bridge.py (1,787줄) → 대상별 분리

### 2.1 현재 구조 분석

| 라인 범위 | 테스트 클래스 | 테스트 대상 | 테스트 수 |
|----------|-------------|-----------|---------|
| 26-65 | MockAuditAdapter | Mock 클래스 | - |
| 66-118 | TestAuditEventTypeAdditions | AuditEventType Enum | 4 |
| 119-229 | TestCorruptionShieldAuditIntegration | CorruptionShield | 8 |
| 230-310 | TestShadowLoggerAuditIntegration | ShadowLogger | 6 |
| 311-411 | TestWALAuditIntegration | WAL | 7 |
| 412-514 | TestForensicAuditBridge | ForensicAuditBridge | 8 |
| 515-550 | TestAuditIntegrationEnd2End | E2E 통합 | 3 |
| 551-711 | TestCorruptionShieldBatching | CorruptionShield 배치 | 10 |
| 712-849 | TestAuditContextAutoInjection | 컨텍스트 주입 | 9 |
| 850-978 | TestForensicMasking | 마스킹 | 8 |
| 979-1154 | TestInMemoryAuditBuffer | InMemoryAuditBuffer | 11 |
| 1155-1288 | TestForensicRateLimiter | RateLimiter | 9 |
| 1289-1510 | TestRedisAuditBuffer | RedisAuditBuffer | 14 |
| 1511-1787 | TestMTTRCalculator | MTTRCalculator | 16 |

### 2.2 분리 전략

```
tests/unit/audit/forensic_bridge/
├── __init__.py
├── conftest.py                      # MockAuditAdapter, 공통 fixtures
├── test_event_types.py              # TestAuditEventTypeAdditions (~70줄)
├── test_corruption_shield.py        # TestCorruptionShieldAuditIntegration + 
│                                    # TestCorruptionShieldBatching (~270줄)
├── test_shadow_logger.py            # TestShadowLoggerAuditIntegration (~100줄)
├── test_wal_integration.py          # TestWALAuditIntegration (~120줄)
├── test_forensic_bridge.py          # TestForensicAuditBridge + 
│                                    # TestForensicMasking (~230줄)
├── test_context_injection.py        # TestAuditContextAutoInjection (~150줄)
├── test_audit_buffer.py             # TestInMemoryAuditBuffer + 
│                                    # TestRedisAuditBuffer (~400줄)
├── test_rate_limiter.py             # TestForensicRateLimiter (~150줄)
├── test_mttr_calculator.py          # TestMTTRCalculator (~280줄)
└── test_e2e.py                      # TestAuditIntegrationEnd2End (~50줄)
```

### 2.3 분리 근거

1. **test_corruption_shield.py**
   - TestCorruptionShieldAuditIntegration (8 tests)
   - TestCorruptionShieldBatching (10 tests)
   - 동일 대상(CorruptionShield)의 다른 관점

2. **test_audit_buffer.py**
   - TestInMemoryAuditBuffer (11 tests)
   - TestRedisAuditBuffer (14 tests)
   - 동일 인터페이스의 두 구현체

3. **test_forensic_bridge.py**
   - TestForensicAuditBridge (8 tests)
   - TestForensicMasking (8 tests)
   - ForensicAuditBridge의 핵심 기능 + 마스킹 기능

### 2.4 conftest.py 내용

```python
# tests/unit/audit/forensic_bridge/conftest.py

import pytest
import tempfile
from typing import Any, Dict, List


class MockAuditAdapter:
    """테스트용 Audit 어댑터."""
    
    def __init__(self):
        self.events: List[Dict[str, Any]] = []
    
    def log_event(
        self,
        event_type: str,
        source: str,
        details: Dict[str, Any],
    ) -> None:
        self.events.append({...})
    
    def get_events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["event_type"] == event_type]


@pytest.fixture
def mock_audit_adapter():
    return MockAuditAdapter()


@pytest.fixture
def temp_wal_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir
```

---

## 3. test_error_budget_gate.py (1,369줄) → 대상별 분리

### 3.1 현재 구조 분석

| 라인 범위 | 테스트 클래스 | 테스트 대상 | 테스트 수 |
|----------|-------------|-----------|---------|
| 23-94 | TestErrorBudgetGateConfig | Config | 4 |
| 95-126 | TestGateCheckResult | Result DTO | 1 |
| 127-221 | TestErrorBudgetGateCore | Gate 핵심 | 7 |
| 222-275 | TestFailOpenBehavior | Fail-Open | 4 |
| 276-348 | TestAutomationBlockedError | Exception | 5 |
| 349-386 | TestConvenienceFunctions | 헬퍼 함수 | 3 |
| 387-433 | TestAutomationGateDecorator | 데코레이터 | 4 |
| 434-531 | TestCaching | 캐싱 | 7 |
| 532-584 | TestConfigUpdate | 설정 업데이트 | 4 |
| 585-678 | TestEdgeCases | 엣지 케이스 | 7 |
| 679-773 | TestInMemoryRateLimiter | RateLimiter | 7 |
| 774-984 | TestFailOpenRateLimiting | Fail-Open RL | 15 |
| 985-1061 | TestInMemoryCircuitBreaker | CB | 6 |
| 1062-1122 | TestGateAlertManager | AlertManager | 5 |
| 1123-1177 | TestGateCircuitBreakerIntegration | CB 통합 | 4 |
| 1178-1229 | TestGateHealthStatus | Health Status | 4 |
| 1230-1298 | TestConfigNewFields | 신규 설정 | 5 |
| 1299-1369 | TestAutomationGateFunctoolsWraps | functools | 5 |

### 3.2 분리 전략

```
tests/unit/resilience/error_budget_gate/
├── __init__.py
├── conftest.py                      # 공통 fixtures, mocks
├── test_config.py                   # TestErrorBudgetGateConfig + 
│                                    # TestConfigNewFields (~140줄)
├── test_core.py                     # TestErrorBudgetGateCore + 
│                                    # TestGateCheckResult (~130줄)
├── test_fail_open.py                # TestFailOpenBehavior (~70줄)
├── test_automation.py               # TestAutomationBlockedError + 
│                                    # TestAutomationGateDecorator +
│                                    # TestAutomationGateFunctoolsWraps (~180줄)
├── test_caching.py                  # TestCaching + TestConfigUpdate (~160줄)
├── test_edge_cases.py               # TestEdgeCases (~110줄)
├── test_rate_limiter.py             # TestInMemoryRateLimiter + 
│                                    # TestFailOpenRateLimiting (~310줄)
├── test_circuit_breaker.py          # TestInMemoryCircuitBreaker + 
│                                    # TestGateCircuitBreakerIntegration (~140줄)
├── test_alert_health.py             # TestGateAlertManager + 
│                                    # TestGateHealthStatus (~130줄)
└── test_helpers.py                  # TestConvenienceFunctions (~50줄)
```

### 3.3 분리 근거

1. **test_rate_limiter.py** (가장 큰 그룹)
   - TestInMemoryRateLimiter: 기본 기능 (7 tests)
   - TestFailOpenRateLimiting: Fail-Open 동작 (15 tests)
   - 310줄: 추가 분리 고려 대상

2. **test_automation.py**
   - TestAutomationBlockedError: 예외 처리
   - TestAutomationGateDecorator: 데코레이터 기능
   - TestAutomationGateFunctoolsWraps: functools 호환성
   - 모두 "자동화 제어" 관련

3. **test_circuit_breaker.py**
   - TestInMemoryCircuitBreaker: CB 기본
   - TestGateCircuitBreakerIntegration: Gate-CB 통합
   - CB 관련 테스트 집중

---

## 4. test_shadow_budget_weighted.py (1,128줄) → 시나리오별 분리

### 4.1 현재 구조 분석

| 라인 범위 | 테스트 클래스 | 시나리오 | 테스트 수 |
|----------|-------------|---------|---------|
| 53-80 | TestWeightedBudgetConstants | 상수 정의 | 4 |
| 81-134 | TestSourceReliabilityWeight | 소스 신뢰도 | 5 |
| 135-154 | TestSeverityWeightConstants | 심각도 상수 | 4 |
| 155-215 | TestSeverityWeighting | 심각도 가중치 | 5 |
| 216-258 | TestMixedSeverityWeighting | 혼합 심각도 | 3 |
| 259-330 | TestCalculateShadowBudgetWithWeighting | 계산 | 5 |
| 331-371 | TestEdgeCases | 엣지 케이스 | 3 |
| 372-382 | TestDomainSLAWeightConstants | 도메인 상수 | 1 |
| 383-459 | TestDomainWeighting | 도메인 가중치 | 6 |
| 460-494 | TestDomainWeightIntegration | 도메인 통합 | 3 |
| 495-508 | TestPatternWeightConstants | 패턴 상수 | 1 |
| 509-633 | TestPatternWeighting | 패턴 가중치 | 9 |
| 634-674 | TestPatternWeightIntegration | 패턴 통합 | 3 |
| 675-790 | TestMultiplierCap | 승수 상한 | 8 |
| 791-859 | TestCalculateShadowBudgetWithDomainAndPattern | 복합 계산 | 5 |
| 860-894 | TestSimulationBridge | 시뮬레이션 | 3 |
| 895-965 | TestPendingReconciliationFreeze | 보류 조정 | 5 |
| 966-1013 | TestAuditEvents | 감사 이벤트 | 4 |
| 1014-1059 | TestAccuracyAudit | 정확도 감사 | 4 |
| 1060-1128 | TestNotificationIntegration | 알림 통합 | 5 |

### 4.2 분리 전략

```
tests/unit/resilience/shadow_budget/
├── __init__.py
├── conftest.py                      # shadow_calculator fixture
├── test_constants.py                # TestWeightedBudgetConstants + 
│                                    # TestSeverityWeightConstants +
│                                    # TestDomainSLAWeightConstants +
│                                    # TestPatternWeightConstants (~80줄)
├── test_source_reliability.py       # TestSourceReliabilityWeight (~70줄)
├── test_severity_weighting.py       # TestSeverityWeighting + 
│                                    # TestMixedSeverityWeighting (~110줄)
├── test_domain_weighting.py         # TestDomainWeighting + 
│                                    # TestDomainWeightIntegration (~120줄)
├── test_pattern_weighting.py        # TestPatternWeighting + 
│                                    # TestPatternWeightIntegration (~180줄)
├── test_multiplier_cap.py           # TestMultiplierCap (~130줄)
├── test_calculation.py              # TestCalculateShadowBudgetWithWeighting +
│                                    # TestCalculateShadowBudgetWithDomainAndPattern (~160줄)
├── test_edge_cases.py               # TestEdgeCases (~60줄)
├── test_reconciliation.py           # TestSimulationBridge + 
│                                    # TestPendingReconciliationFreeze (~120줄)
└── test_audit_notification.py       # TestAuditEvents + TestAccuracyAudit +
                                     # TestNotificationIntegration (~130줄)
```

### 4.3 분리 근거

1. **상수 테스트 통합 (test_constants.py)**
   - 4개의 작은 상수 테스트 클래스를 하나로
   - 각 클래스가 1~4개 테스트로 매우 작음

2. **가중치 유형별 분리**
   - test_source_reliability.py: 소스 신뢰도
   - test_severity_weighting.py: 심각도
   - test_domain_weighting.py: 도메인
   - test_pattern_weighting.py: 패턴
   - 각각 독립적인 가중치 계산 로직

3. **통합/부가 기능 분리**
   - test_reconciliation.py: 시뮬레이션 + 보류 조정
   - test_audit_notification.py: 감사 + 알림

---

## 5. test_hash_chain_graceful_degradation.py (1,049줄) → 대상별 분리

### 5.1 현재 구조 분석

| 라인 범위 | 테스트 클래스 | 테스트 대상 | 테스트 수 |
|----------|-------------|-----------|---------|
| 45-151 | MockRedisClient | Mock 클래스 | - |
| 152-184 | MockPipeline | Mock 클래스 | - |
| 185-242 | MockDistributedLock | Mock 클래스 | - |
| 243-262 | TestDegradationLevel | Enum | 2 |
| 263-357 | TestHashChainFallbackChain | FallbackChain | 7 |
| 358-434 | TestDegradedEntryMarker | Marker | 6 |
| 435-576 | TestHashChainWALRecovery | WAL | 10 |
| 577-678 | TestHashChainDegradationManager | Manager | 8 |
| 679-834 | TestHashChainCircuitBreaker | CB | 12 |
| 835-918 | TestHashChainGracefulDegradationManager | 통합 Manager | 7 |
| 919-1049 | TestPhase4Integration | 통합 테스트 | 10 |

### 5.2 분리 전략

```
tests/unit/audit/graceful_degradation/
├── __init__.py
├── conftest.py                      # MockRedisClient, MockPipeline, 
│                                    # MockDistributedLock, 공통 fixtures (~210줄)
├── test_enums.py                    # TestDegradationLevel (~30줄)
├── test_fallback_chain.py           # TestHashChainFallbackChain (~110줄)
├── test_degraded_marker.py          # TestDegradedEntryMarker (~90줄)
├── test_wal_recovery.py             # TestHashChainWALRecovery (~160줄)
├── test_degradation_manager.py      # TestHashChainDegradationManager (~120줄)
├── test_circuit_breaker.py          # TestHashChainCircuitBreaker (~170줄)
├── test_unified_manager.py          # TestHashChainGracefulDegradationManager (~100줄)
└── test_integration.py              # TestPhase4Integration (~150줄)
```

### 5.3 분리 근거

1. **conftest.py로 Mock 이동**
   - MockRedisClient (106줄)
   - MockPipeline (32줄)
   - MockDistributedLock (57줄)
   - 총 195줄의 Mock 코드를 conftest.py로

2. **컴포넌트별 분리**
   - 소스 코드 분리 전략(51_REFACTORING_SOURCE_CODE.md)과 1:1 대응
   - 각 테스트 파일이 해당 소스 파일 테스트

---

## 6. test_error_budget_reconciliation.py (1,016줄) → 대상별 분리

### 6.1 현재 구조 분석

| 라인 범위 | 테스트 클래스 | 테스트 대상 | 테스트 수 |
|----------|-------------|-----------|---------|
| 82-133 | TestFailSafePeriodModel | Model | 4 |
| 134-189 | TestShadowBudgetModel | Model | 4 |
| 190-282 | TestFailSafePeriodTracker | Tracker | 7 |
| 283-388 | TestShadowBudgetCalculator | Calculator | 8 |
| 389-434 | TestReconciliationConfig | Config | 4 |
| 435-644 | TestReconciliationService | Service | 16 |
| 645-695 | TestFactoryFunctions | Factory | 4 |
| 696-797 | TestReconciliationScenarios | 시나리오 | 8 |
| 798-1016 | TestReconciliationHistoryIntegration | 히스토리 통합 | 16 |

### 6.2 분리 전략

```
tests/unit/resilience/error_budget_reconciliation/
├── __init__.py
├── conftest.py                      # 공통 fixtures
├── test_models.py                   # TestFailSafePeriodModel + 
│                                    # TestShadowBudgetModel (~120줄)
├── test_config.py                   # TestReconciliationConfig (~60줄)
├── test_tracker.py                  # TestFailSafePeriodTracker (~110줄)
├── test_calculator.py               # TestShadowBudgetCalculator (~120줄)
├── test_service.py                  # TestReconciliationService (~230줄)
├── test_factory.py                  # TestFactoryFunctions (~60줄)
├── test_scenarios.py                # TestReconciliationScenarios (~120줄)
└── test_history_integration.py      # TestReconciliationHistoryIntegration (~230줄)
```

---

## 7. test_emergency_mode.py (978줄) → 대상별 분리

### 7.1 현재 구조 분석

| 라인 범위 | 테스트 클래스 | 테스트 대상 | 테스트 수 |
|----------|-------------|-----------|---------|
| 33-59 | TestEmergencyLevel | Enum | 3 |
| 60-113 | TestEmergencyLevelRules | Rules | 4 |
| 114-141 | TestRecoveryGateConfig | Config | 2 |
| 142-213 | TestRecoveryGate | RecoveryGate | 6 |
| 214-246 | TestEmergencyState | State | 3 |
| 247-460 | TestGracefulDegradationManager | Manager | 16 |
| 461-514 | TestConvenienceFunctions | Helpers | 4 |
| 515-596 | TestThreadSafety | 스레드 안전성 | 6 |
| 597-634 | TestIntegrationWithMockBackend | 통합 | 3 |
| 635-820 | TestEmergencyModeConfigHistoryIntegration | 히스토리 | 14 |
| 821-978 | TestEmergencyModeSnapshot | 스냅샷 | 12 |

### 7.2 분리 전략

```
tests/unit/resilience/emergency_mode/
├── __init__.py
├── conftest.py                      # 공통 fixtures
├── test_enums_config.py             # TestEmergencyLevel + TestEmergencyLevelRules +
│                                    # TestRecoveryGateConfig + TestEmergencyState (~120줄)
├── test_recovery_gate.py            # TestRecoveryGate (~90줄)
├── test_manager.py                  # TestGracefulDegradationManager (~230줄)
├── test_helpers.py                  # TestConvenienceFunctions (~70줄)
├── test_thread_safety.py            # TestThreadSafety (~100줄)
├── test_integration.py              # TestIntegrationWithMockBackend (~50줄)
├── test_config_history.py           # TestEmergencyModeConfigHistoryIntegration (~200줄)
└── test_snapshot.py                 # TestEmergencyModeSnapshot (~170줄)
```

---

## 8. 600~1,000줄 파일 분리 요약

### 8.1 test_hash_chain_performance.py (968줄)

```
분리:
tests/unit/audit/hash_chain_performance/
├── conftest.py                # MockRedisClient, MockPipeline
├── test_lua_atomic.py         # TestLuaAtomicHashChain
├── test_pipeline_batch.py     # TestPipelineBatchQuery
├── test_batch_flush.py        # TestBatchFlushWriter
├── test_async_writer.py       # TestAsyncAuditWriter
├── test_sampling.py           # TestSamplingVerifier
├── test_watchdog.py           # TestPendingSequenceWatchdog
├── test_manager.py            # TestHashChainPerformanceManager
└── test_integration.py        # TestPhase3Integration
```

### 8.2 test_audit_integration.py (934줄)

```
분리:
tests/unit/audit/audit_integration/
├── test_async_logger.py       # TestAsyncLoggerAdapter
├── test_observers.py          # TestAuditEventObserver, TestAsyncLoggerObserver
├── test_recorder.py           # TestIntegratedAuditRecorder
├── test_helpers.py            # TestConvenienceFunctions
├── test_enums.py              # TestEventSeverity, TestAuditEventType
├── test_data.py               # TestAuditEventData
└── test_scenarios.py          # TestIntegrationScenarios
```

### 8.3 test_audit_helpers_chaos_emergency.py (907줄)

```
분리:
tests/unit/audit/helpers/
├── test_event_types.py        # TestAuditEventTypeChaosEmergency
├── test_chaos_audit.py        # TestLogChaosExperimentAudit
├── test_emergency_audit.py    # TestLogEmergencyModeAudit
├── test_budget_audit.py       # TestLogErrorBudgetBlockedAudit
├── test_migration.py          # TestChaosExperimentMigration, TestEmergencyModeManagerMigration, TestErrorBudgetGateMigration
└── test_buffer.py             # TestBufferIntegration
```

### 8.4 test_layered_repository.py (862줄)

```
분리:
tests/unit/storage/layered_repository/
├── test_l2_timeout.py         # TestL2Timeout
├── test_shadow_logging.py     # TestShadowLogging
├── test_forensic_analysis.py  # TestShadowLogForensicAnalysis
├── test_drift.py              # TestDriftReconciliation
├── test_cold_start.py         # TestColdStartProtection
├── test_fallback.py           # TestIntelligentFallback
├── test_basic.py              # TestLayeredRepositoryBasic
└── test_health_check.py       # TestL2HealthCheck
```

### 8.5 test_hash_chain_core.py (862줄)

```
분리:
tests/unit/audit/hash_chain_core/
├── conftest.py                # MockRedisClient, MockPipeline
├── test_pending_sequence.py   # TestPendingSequenceManager
├── test_daily_anchor.py       # TestDailyHashAnchor
├── test_startup_sync.py       # TestStartupHashChainSync
├── test_reconciler.py         # TestHashChainReconciler
└── test_integration.py        # TestHashChainCoreIntegration
```

### 8.6 test_memory_repositories.py (835줄)

```
분리:
tests/unit/adapters/memory_repositories/
├── test_failed_operation.py   # TestInMemoryFailedOperationRepository
├── test_circuit_breaker.py    # TestInMemoryCircuitBreakerStateRepository
├── test_security_incident.py  # TestInMemorySecurityIncidentRepository
├── test_provider_registry.py  # TestProviderRegistry
├── test_integration.py        # TestIntegrationScenarios
└── test_cleanup.py            # TestCleanupOperations
```

---

## 9. 공통 패턴: conftest.py 구조

### 9.1 패키지 레벨 conftest.py

```python
# tests/unit/audit/forensic_bridge/conftest.py
"""
Forensic Bridge 테스트 공통 설정.

이 패키지의 모든 테스트에서 사용하는 fixtures와 mocks.
"""

import pytest
import tempfile
from typing import Any, Dict, List
from unittest.mock import MagicMock


# =============================================================================
# Mock Classes
# =============================================================================


class MockAuditAdapter:
    """테스트용 Audit 어댑터."""
    
    def __init__(self):
        self.events: List[Dict[str, Any]] = []
    
    def log_event(self, event_type: str, source: str, details: Dict[str, Any]) -> None:
        self.events.append({
            "event_type": event_type,
            "source": source,
            "details": details,
        })
    
    def get_events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["event_type"] == event_type]
    
    def clear(self) -> None:
        self.events.clear()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_audit_adapter():
    """Fresh MockAuditAdapter for each test."""
    return MockAuditAdapter()


@pytest.fixture
def temp_wal_dir():
    """임시 WAL 디렉토리."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_audit_event():
    """샘플 감사 이벤트."""
    return {
        "event_type": "test_event",
        "source": "test_source",
        "details": {"key": "value"},
    }
```

---

## 10. 분리 작업 순서

### Phase 1 (1주차): 1,000줄 이상 파일

1. test_audit_forensic_bridge.py → forensic_bridge/
2. test_error_budget_gate.py → error_budget_gate/
3. test_shadow_budget_weighted.py → shadow_budget/
4. test_hash_chain_graceful_degradation.py → graceful_degradation/
5. test_error_budget_reconciliation.py → error_budget_reconciliation/

### Phase 2 (2주차): 800~1,000줄 파일

6. test_emergency_mode.py → emergency_mode/
7. test_hash_chain_performance.py → hash_chain_performance/
8. test_audit_integration.py → audit_integration/
9. test_audit_helpers_chaos_emergency.py → helpers/

### Phase 3 (3주차): 600~800줄 파일

10. test_layered_repository.py → layered_repository/
11. test_hash_chain_core.py → hash_chain_core/
12. test_memory_repositories.py → memory_repositories/
13. test_opentelemetry_adapter.py → opentelemetry/
14. test_pydantic_settings_phase2.py → pydantic_settings/

---

## 11. 테스트 네이밍 규칙

### 11.1 파일명 규칙

```
test_{대상}_{관점}.py

예시:
- test_error_budget_gate_core.py      # 핵심 기능
- test_error_budget_gate_failopen.py  # Fail-Open 동작
- test_error_budget_gate_cache.py     # 캐싱
```

### 11.2 클래스명 규칙

```
class Test{대상}{관점}:

예시:
- class TestErrorBudgetGateCore:
- class TestErrorBudgetGateFailOpen:
- class TestErrorBudgetGateCaching:
```

### 11.3 메서드명 규칙

```
def test_{동작}_{조건}_{예상결과}:

예시:
- def test_gate_allows_when_budget_healthy():
- def test_gate_blocks_when_budget_critical():
- def test_gate_warns_when_budget_low():
```

---

## 12. 기존 테스트 파일 호환성

### 12.1 호환성 전략: Import 전체 변경 (권장)

분리 후 기존 facade를 유지하는 대신, **모든 import 경로를 직접 수정**하는 것을 권장합니다.

**권장 접근법**:
```python
# Before (구 경로)
from selfhealing.services.chaos.experiment_impl import LatencyInjectionExperiment

# After (새 경로)
from selfhealing.services.chaos.experiments import LatencyInjectionExperiment
```

**장점**:
- 임시방편(facade)이 아닌 완전한 리팩토링
- 코드베이스의 일관성 유지
- 유지보수성 향상
- 명확한 모듈 경계

### 12.2 실제 적용 사례 (2025-06-16 완료)

1. `experiment_impl.py` → `experiments/` 패키지
   - 기존 facade 파일 삭제
   - 모든 테스트 import 경로 수정
   
2. `integrity.py` → `integrity/` 패키지
   - 기존 facade 파일 삭제
   - 패키지 `__init__.py`에서 re-export
   
3. `hash_chain_graceful_degradation.py` → `graceful_degradation/` 패키지
   - 기존 facade 파일 삭제
   - 모든 테스트 import 경로 수정

### 12.3 점진적 마이그레이션 (대안)

facade를 일시적으로 유지해야 하는 경우에만 사용:

1. 새 패키지 구조 생성
2. 테스트를 새 파일로 이동
3. 기존 파일을 facade로 변경 (임시)
4. CI/CD에서 양쪽 경로 테스트 확인
5. **즉시** 모든 import 수정 후 facade 제거

**주의**: Facade는 임시방편이므로 가능한 빨리 제거해야 합니다.
