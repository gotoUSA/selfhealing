# 203. Enum 베이스 클래스 통일

> **상태**: ✅ 완료 (2026-02-09)
> **목적**: Enum 정의 시 `str, Enum` vs plain `Enum` vs `IntEnum` 사용 기준을 수립하고 불일치를 수정한다.

---

## 1. 확정 규칙

| 조건 | 사용할 베이스 | 사유 |
|------|-------------|------|
| 값이 **문자열** | `str, Enum` | JSON 자동 직렬화, 문자열 비교 |
| 값이 **정수**이고 **순서 비교** 필요 | `IntEnum` | `<`, `>` 비교 지원 |
| 내부 전용, 직렬화 불필요 | `str, Enum` (통일) | 일관성 우선 |

**결론**: 문자열 값 → `str, Enum`, 정수 값 + 순서 비교 → `IntEnum`.

---

## 2. 수정 완료 내역

### 2-1. plain `Enum` → `str, Enum` (문자열 값, 48건)

| 파일 | 클래스 |
|------|--------|
| `services/event_bus.py` | `EventType` |
| `utils/async_logger.py` | `WALPolicy` |
| `utils/async_logger.py` | `QueueOverflowPolicy` |
| `adapters/resilient/backend.py` | `ResilientStorageMode` |
| `services/governance.py` | `OperationMode` |
| `services/rollback/models.py` | `RollbackStrategy` |
| `services/rollback/models.py` | `RollbackState` |
| `services/learning/models.py` | `PatternType` |
| `services/learning/models.py` | `SuggestionPriority` |
| `services/pending_config.py` | `PendingStatus` |
| `services/event_bus_redis.py` | `EventChannel` |
| `services/finops/models.py` | `CostTier` |
| `services/idempotency_service.py` | `IdempotencyDomain` |
| `services/retry_handler.py` | `RetryAction` |
| `services/error_budget/exception_weights.py` | `WeightCombinePolicy` |
| `meta/health_probe.py` | `HealthStatus` |
| `audit/verify_audit_integrity.py` | `OutputFormat` |
| `audit/wal.py` | `WALState` |
| `audit/self_audit.py` | `SelfAuditEvent` |
| `scaling/config.py` | `BackpressureLevel` |
| `scaling/config.py` | `BackpressureStrategy` |
| `services/config/propagator.py` | `ConfigScope` |
| `services/config/propagator.py` | `PropagationTier` |
| `services/compliance/models.py` | `ComplianceStandard` |
| `services/compliance/models.py` | `ViolationSeverity` |
| `multiregion/replicator.py` | `ReplicationEventType` |
| `multiregion/health_monitor.py` | `RegionHealthStatus` |
| `multiregion/failover.py` | `FailoverState` |
| `metrics/safe_gauge/sync.py` | `SyncStatus` |
| `metrics/reliability_manager.py` | `ReliabilityLevel` |
| `metrics/reliability_manager.py` | `OperatingMode` |
| `meta/recovery_adapter.py` | `RecoveryAction` |
| `metrics/reliability.py` | `MetricReliability` |
| `interfaces/rate_limit_storage.py` | `RateLimitStorageType` |
| `meta/escalation.py` | `EscalationLevel` |
| `core/apply_strategy.py` | `ApplyStrategy` |
| `coordination/base.py` | `LeadershipState` |
| `core/auto_rollback_guard.py` | `RecoveryStrategy` (내부 클래스) |
| `audit/masking.py` | `MaskingLevel` |
| `core/tiered_redis.py` | `RedisScope` |
| `audit/logger.py` | `ConfigAuditAction` |
| `audit/event_buffer.py` | `AuditEventType` |
| `audit/audit_watchdog.py` | `AuditWatchdogStatus` |
| `audit/audit_integration.py` | `AuditObserverEventType` |
| `audit/backends/base.py` | `BackendStatus` |
| `api/django/tiering/enums.py` | `TierFallbackReason` |
| `api/django/views/xtest/scenarios/base.py` | `ScenarioStatus` |
| `api/django/rate_limit.py` | `RedisHealthState` |
| `adapters/memory/drift_reconciliation.py` | `DriftReconciliationResult` |

### 2-2. plain `Enum` / `int, Enum` → `IntEnum` (정수 값 + 순서/산술, 6건)

`.value` 기반 순서 비교 또는 산술 연산이 코드에서 확인된 정수 값 Enum:

| 파일 | 클래스 | 원본 | 근거 |
|------|--------|------|------|
| `services/event_bus.py` | `EventPriority` | plain `Enum` | `.value` 비교: `LOW.value < NORMAL.value` |
| `utils/async_logger.py` | `EventSeverity` | plain `Enum` | `.value` 비교: `DEBUG.value < INFO.value` |
| `services/emergency_mode/enums.py` | `EmergencyLevel` | plain `Enum` | `.value` 비교: `level.value >= LEVEL_2.value` |
| `scaling/graceful_degradation.py` | `FeaturePriority` | plain `Enum` | `.value` 비교: `CRITICAL.value == 0` |
| `audit/persistence/disk_buffer.py` | `BufferState` | plain `Enum` | 상태 머신 (정수 값 0-4) |
| `interfaces/task_queue.py` | `TaskPriority` | `int, Enum` | `.value` 산술 연산: `10 - priority.value` (celery_adapter) |

### 2-3. IntEnum — 기존 유지 (7건)

| 파일 | 클래스 | 사유 |
|------|--------|------|
| `services/coordination/redis_key_guard.py` | `RedisKeyPriority` | 키 우선순위 숫자 비교 |
| `services/coordination/enums.py` | `CommandPrecedence` | 명령 우선순위 숫자 비교 |
| `services/coordination/critical_worker.py` | `CriticalTaskPriority` | 태스크 우선순위 숫자 비교 |
| `services/canary/models.py` | `PauseTriggerPriority` | Pause 트리거 우선순위 비교 |
| `adapters/ipc/cb_state_snapshot.py` | `CBState` | CB 상태 정수 매핑 |
| `adapters/ipc/protocol/json_rpc.py` | `JSONRPCErrorCode` | JSON-RPC 에러 코드 |
| `audit/cascade_event.py` | `CascadeEventPriority` | 이벤트 우선순위 비교 |

---

## 3. 검증 결과

- [x] 기존 `.value` 접근 패턴이 전환 후에도 호환됨 (10426 테스트 통과)
- [x] `json.dumps` 호출부에서 커스텀 인코더 불필요 확인
- [x] `str, Enum` 전환으로 문자열 직접 비교 가능
