# Phase 3: `services/__init__.py` 축소 계획

> **Created**: 2026-01-04
> **Status**: 📋 계획 완료, 작업 대기
> **분석 스크립트**: `scripts/phase3_analysis.py`

---

## 📊 분석 결과 요약

| 항목 | 수치 |
|------|------|
| 현재 `__all__` 심볼 수 | 182개 |
| 실제 사용 심볼 수 (tests + shopping) | 62개 |
| 제거 가능 심볼 수 | 120개 |
| **축소 비율** | **67.0%** |

---

## 1. 사용 현황 분석

### 1.1 Shopping 앱에서 사용 (18개)

| 심볼 | 사용 횟수 | 카테고리 |
|------|----------|----------|
| `get_circuit_breaker_service` | 9 | Circuit Breaker |
| `get_replay_service` | 6 | Replay |
| `get_sla_thresholds` | 2 | Config |
| `SecurityViolationService` | 2 | Security |
| `collect_all_metrics` | 1 | Metrics |
| `get_dlq_service` | 1 | DLQ |
| `record_sla_breach` | 1 | Metrics |
| `ALERTING_RULES` | 1 | Metrics Constants |
| `DOMAINS` | 1 | Metrics Constants |
| `SEVERITY_BY_VIOLATION_TYPE` | 1 | Security |
| `Severity` | 1 | Security |
| `ViolationType` | 1 | Security |
| `NotificationChannel` | 1 | Notification |
| `NotificationConfig` | 1 | Notification |
| `SecurityNotificationService` | 1 | Security |
| `ForensicContext` | 1 | Forensic |
| `DLQService` | 1 | DLQ |
| `CircuitBreakerService` | 1 | Circuit Breaker |

### 1.2 Tests에서 사용 (58개)

상위 사용 빈도:

| 심볼 | 사용 횟수 |
|------|----------|
| `CircuitBreakerService` | 23 |
| `DLQService` | 21 |
| `CircuitState` | 16 |
| `get_rate_limit_tracker` | 12 |
| `CircuitBreakerConfig` | 10 |
| `DLQConfig` | 9 |
| `ReplayService` | 5 |
| `IdempotencyService` | 5 |

### 1.3 전체 유지 필요 심볼 (62개)

```
ALERTING_RULES, BatchReplayResult, CircuitBreakerConfig, CircuitBreakerResult,
CircuitBreakerService, CircuitState, ControlAPIService, ControlRequest,
ControlResponse, DLQConfig, DLQEntryResult, DLQService, DOMAINS, ForensicContext,
IdempotencyDomain, IdempotencyKey, IdempotencyService, MaxRetriesExceededError,
NotificationChannel, NotificationConfig, NotificationResult, RateLimitTracker,
ReplayResult, ReplayService, RetryAction, RetryConfig, RetryHandler, RetryResult,
SEVERITY_BY_VIOLATION_TYPE, SecurityConfig, SecurityNotificationResult,
SecurityNotificationService, SecurityViolationResult, SecurityViolationService,
Severity, ViolationType, circuit_breaker_service, collect_all_metrics,
force_close_circuit, force_open_circuit, get_circuit_breaker_service,
get_dlq_service, get_idempotency_service, get_protection_status,
get_rate_limit_tracker, get_replay_service, get_security_notification_service,
get_security_violation_service, get_sla_thresholds, handle_security_violation,
notify_security_incident, record_circuit_breaker_open_duration,
record_circuit_breaker_state_change, record_dlq_item_created, record_rate_limit,
record_recovery_time, record_replay_attempt, record_retry_attempt,
record_sla_breach, should_allow_request, should_allow_with_protection,
track_recovery_time
```

---

## 2. 작업 전략

### 2.1 옵션 A: 보수적 접근 (권장)

**목표**: 현재 사용 중인 62개만 `__all__`에 유지, 나머지 120개 제거

**장점**:
- 테스트 코드 수정 불필요
- 하위 호환성 유지
- 작업량 최소화

**작업**:
1. `services/__init__.py`에서 사용되지 않는 120개 import 및 `__all__` 항목 제거
2. deprecation warning 추가 (선택)

### 2.2 옵션 B: 적극적 접근 (미래 지향)

**목표**: shopping 앱이 사용하는 18개만 `__all__`에 유지

**장점**:
- Public API 최소화
- 패키지 인터페이스 명확화

**작업**:
1. 테스트 코드 58개 심볼을 직접 import로 변경 (약 86개 파일 수정)
2. `services/__init__.py`를 18개로 축소

**예상 수정 파일 수**: 86개 테스트 파일

---

## 3. 옵션 A 세부 작업 계획

### 3.1 제거할 심볼 (120개)

**Configuration 관련 (21개):**
```
SelfHealingConfig, SLAThresholds, IdempotencyConfig, SecurityThresholds,
NotificationLimits, SlackChannels, RetrySettings, CircuitBreakerSettings,
DLQSettings, ForensicSettings, get_config, reload_config,
get_idempotency_config, get_security_thresholds, get_notification_limits,
get_slack_channels, get_retry_settings, get_circuit_breaker_settings,
get_dlq_settings, get_forensic_settings, RateLimitConfig
```

**Backoff (2개):**
```
BackoffCalculator, calculate_backoff
```

**Forensic Advisor (9개):**
```
AdvisoryLevel, RecommendedAction, FailurePattern, ForensicAdvisory,
ForensicAdvisorService, KNOWN_PATTERNS, get_forensic_advisor,
analyze_failed_operation, analyze_and_update_operation
```

**Chaos Context (8개):**
```
ChaosExperimentType, ChaosExperimentStatus, ChaosExperimentContext,
is_chaos_experiment, get_chaos_context, attach_chaos_context,
resolve_chaos_experiment, create_chaos_context
```

**Control API (1개):**
```
get_control_api_service
```

**DLQ/Replay (5개):**
```
store_to_dlq, ReplayHandler, get_replay_handler, replay_failed_operation,
batch_replay_by_failure_type
```

**Rate Limit (4개):**
```
RateLimitCoordinator, RateLimitResult, get_rate_limit_coordinator,
should_allow_with_ddos_protection
```

**Metrics (9개):**
```
register_domain, get_registered_domains, update_dlq_pending_gauges,
update_dlq_status_gauges, update_circuit_breaker_gauges,
update_retry_success_rates, track_replay
```

**Notification (11개):**
```
send_alert, UnifiedNotificationManager, NotificationPayload,
NotificationPriority, NotificationCategory, RoutingPolicy,
get_unified_notification_manager, notify, notify_security,
notify_sla, notify_error
```

**Forensic (1개):**
```
capture_forensic_context
```

**Idempotency (1개):**
```
IdempotencyResult
```

**Runtime Config (2개):**
```
RuntimeConfigManager, get_runtime_config_manager
```

**Health Check (6개):**
```
HealthCheckService, HealthStatus, ReadinessStatus, PoolHealthStatus,
DatabaseCheck, PoolInfo, get_health_check_service
```

**System Control (5개):**
```
SystemControlManager, SystemState, get_system_control,
is_selfhealing_enabled, is_dry_run, should_execute_action
```

**Governance (13개):**
```
BlockReason, GovernanceCheckResult, is_system_enabled, is_emergency_blocking,
is_error_budget_blocking, check_all_governance, invalidate_governance_cache,
require_system_enabled, require_not_emergency, require_error_budget,
require_governance, GovernanceCheckMixin, TTLCache
```

**Governance Service (4개):**
```
GovernanceService, ExpiryCheckResult, GovernanceNotificationResult,
get_governance_service
```

**Chaos Execution (5개):**
```
ChaosExecutionService, ExperimentExecutionResult, DailyReportResult,
ApprovalCleanupResult, PendingApprovalCheckResult, get_chaos_execution_service
```

**Config Apply (2개):**
```
ConfigApplyService, get_config_apply_service
```

**Factory (11개):**
```
create_failed_operation_repository, create_circuit_breaker_repository,
create_security_incident_repository, create_dlq_service, create_replay_service,
create_circuit_breaker_service, create_security_violation_service,
get_dlq_service_with_di, get_replay_service_with_di,
get_circuit_breaker_service_with_di, get_security_violation_service_with_di,
reset_service_singletons
```

### 3.2 유지할 `__all__` (62개)

```python
__all__ = [
    # === Circuit Breaker ===
    "CircuitBreakerService",
    "CircuitBreakerConfig",
    "CircuitBreakerResult",
    "CircuitState",
    "get_circuit_breaker_service",
    "should_allow_request",
    "force_open_circuit",
    "force_close_circuit",
    "circuit_breaker_service",  # module alias

    # === DLQ ===
    "DLQService",
    "DLQConfig",
    "DLQEntryResult",
    "get_dlq_service",

    # === Replay ===
    "ReplayService",
    "ReplayResult",
    "BatchReplayResult",
    "get_replay_service",

    # === Rate Limit ===
    "RateLimitTracker",
    "get_rate_limit_tracker",
    "record_rate_limit",
    "should_allow_with_protection",
    "get_protection_status",

    # === Retry ===
    "RetryHandler",
    "RetryConfig",
    "RetryResult",
    "RetryAction",
    "MaxRetriesExceededError",

    # === Idempotency ===
    "IdempotencyService",
    "IdempotencyKey",
    "IdempotencyDomain",
    "get_idempotency_service",

    # === Forensic ===
    "ForensicContext",

    # === Control API ===
    "ControlAPIService",
    "ControlRequest",
    "ControlResponse",

    # === Security Violation ===
    "SecurityViolationService",
    "SecurityViolationResult",
    "SecurityConfig",
    "ViolationType",
    "Severity",
    "SEVERITY_BY_VIOLATION_TYPE",
    "get_security_violation_service",
    "handle_security_violation",

    # === Security Notification ===
    "SecurityNotificationService",
    "SecurityNotificationResult",
    "NotificationResult",
    "NotificationConfig",
    "NotificationChannel",
    "get_security_notification_service",
    "notify_security_incident",

    # === Metrics ===
    "ALERTING_RULES",
    "DOMAINS",
    "collect_all_metrics",
    "record_dlq_item_created",
    "record_retry_attempt",
    "record_recovery_time",
    "record_sla_breach",
    "record_circuit_breaker_state_change",
    "record_circuit_breaker_open_duration",
    "record_replay_attempt",
    "track_recovery_time",

    # === Config ===
    "get_sla_thresholds",
]
```

---

## 4. 검증 계획

### 4.1 변경 전 확인

```bash
# 테스트 baseline
pytest tests/ --tb=no -q 2>&1 | tail -5
```

### 4.2 변경 후 검증

```bash
# 1. Import 오류 확인
python -c "from selfhealing.services import *"

# 2. 테스트 실행
pytest tests/ -x

# 3. Shopping 앱 import 확인
python -c "
from shopping.tasks import self_healing_tasks
from shopping.admin import circuit_breaker_admin
from shopping.management.commands import security_review
print('All imports OK')
"
```

---

## 5. 롤백 계획

변경 사항을 Git에서 쉽게 롤백할 수 있도록:

```bash
git checkout HEAD~1 -- packages/selfhealing-python/src/selfhealing/services/__init__.py
```

---

## 6. 작업 체크리스트

- [ ] 현재 테스트 통과 확인
- [ ] `services/__init__.py` 백업
- [ ] 사용되지 않는 import 문 제거
- [ ] `__all__` 62개로 축소
- [ ] Import 오류 확인
- [ ] 전체 테스트 실행
- [ ] Shopping 앱 테스트
- [ ] 문서 업데이트
