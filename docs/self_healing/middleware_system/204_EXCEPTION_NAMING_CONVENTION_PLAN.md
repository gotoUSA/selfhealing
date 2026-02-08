# 204. 예외 클래스 접미사 통일 (Error vs Exception)

> **상태**: ✅ 완료 (2026-02-09)
> **목적**: 커스텀 예외 클래스의 접미사를 `~Error`로 통일한다.

---

## 1. 현황 (변경 전)

### 1-1. 주류 패턴: `~Error` (47건)

프로젝트 내 커스텀 예외 클래스의 **78.3%**가 `~Error` 접미사:

```python
# services/retry_handler.py L58
class MaxRetriesExceededError(Exception):

# services/error_budget_gate/exceptions.py L15
class AutomationBlockedError(Exception):

# services/coordination/distributed_recovery_lock.py L34
class RecoveryLockError(Exception):

# services/canary/versioning.py L40
class VersionConflictError(Exception):

# services/canary/locking.py L31
class ConfigLockError(Exception):

# interfaces/task_queue.py L208
class TaskQueueError(Exception):

# interfaces/task_queue.py L214
class TaskNotFoundError(TaskQueueError):

# interfaces/task_queue.py L220
class TaskTimeoutError(TaskQueueError):

# interfaces/task_queue.py L226
class TaskRevokedError(TaskQueueError):

# interfaces/web_framework.py L391
class WebFrameworkError(Exception):

# interfaces/web_framework.py L397
class RouteNotFoundError(WebFrameworkError):

# interfaces/web_framework.py L403
class AuthenticationError(WebFrameworkError):

# interfaces/web_framework.py L409
class PermissionDeniedError(WebFrameworkError):

# interfaces/rate_limit_storage.py L181
class RateLimitStorageError(Exception):

# interfaces/rate_limit_storage.py L187
class RateLimitStorageUnavailableError(RateLimitStorageError):

# interfaces/cache_provider.py L124
class LockAcquisitionError(Exception):

# interfaces/cache_provider.py L130
class LockNotOwnedError(Exception):

# core/safe_defaults.py L636
class FatalConfigError(Exception):

# audit/cascade_exceptions.py L18
class CascadeAuditError(Exception):

# audit/cascade_exceptions.py L124
class CascadeIntegrityError(CascadeAuditError):

# audit/checkpoint_strategy.py L123
class CheckpointError(Exception):

# audit/checkpoint_strategy.py L129
class CheckpointCorruptedError(CheckpointError):

# audit/checkpoint_manager.py L100 (checkpoint_strategy.py에서 re-export)

# audit/kafka_checkpoint.py L99
class KafkaCheckpointError(Exception):

# audit/wal.py L154
class WALError(Exception):

# audit/wal.py L160
class WALCorruptionError(WALError):

# audit/persistence/mmap_buffer.py L37
class MmapBufferError(Exception):

# audit/persistence/disk_buffer.py L70
class DiskBufferError(Exception):

# adapters/ipc/exceptions.py L27
class IPCError(Exception):

# adapters/ipc/exceptions.py L43-L121 (10개 하위 에러 모두 ~Error)
class IPCConnectionError(IPCError):
class IPCTimeoutError(IPCError):
class IPCAuthenticationError(IPCError):
class IPCAuthorizationError(IPCError):
class IPCMethodNotFoundError(IPCError):
class IPCInvalidParamsError(IPCError):
class IPCParseError(IPCError):
class IPCInternalError(IPCError):
class IPCRateLimitedError(IPCError):
class IPCCircuitBreakerOpenError(IPCError):
class IPCServiceUnavailableError(IPCError):

# adapters/ipc/uds_client.py L60
class UDSClientError(Exception):

# adapters/kafka/schemas.py L152
class SchemaRegistryNotConfiguredError(Exception):

# adapters/kafka/schemas.py L158
class SchemaCompatibilityError(Exception):

# adapters/ipc/protocol/json_rpc.py L357
class JSONRPCParseError(Exception):
```

### 1-2. 소수 패턴: `~Exception` (13건)

**3개 모듈**에서 `~Exception` 접미사 사용:

```python
# resilience/bulkhead/exceptions.py (3건)
class BulkheadException(Exception):
class BulkheadFullException(BulkheadException):
class BulkheadTimeoutException(BulkheadException):

# core/hedging/exceptions.py (3건)
class HedgingException(Exception):
class HedgingAllFailedException(HedgingException):
class HedgingTimeoutException(HedgingException):

# shopping/chaos/decorators.py (7건)
class ChaosException(Exception):
class PartialFailureException(ChaosException):
class AsyncTaskChaosException(ChaosException):
class Phase2OrphanPGException(ChaosException):
class Phase2RollbackFailureException(ChaosException):
class Phase2SilentTaskException(ChaosException):
class Phase2PointOrphanException(ChaosException):
```

### 1-3. 혼합 사례 (hedging 내부 — 변경 전)

```python
# core/hedging/exceptions.py L51
class NonRetryableHedgingError(HedgingException):   # ← Error 접미사
# core/hedging/exceptions.py L75
class HedgingDisabledError(HedgingException):        # ← Error 접미사
```

`core/hedging/exceptions.py` 파일 내에서조차 **같은 계층에 `~Exception`과 `~Error`가 공존**했음.

---

## 2. 문제점

| 항목 | 설명 |
|------|------|
| **일관성 부족** | 프로젝트의 78.3%는 `~Error`, 21.7%는 `~Exception` (3개 모듈 13건) |
| **except 절 혼란** | `except BulkheadException`과 `except IPCError`를 동시에 처리 |
| **같은 파일 내 불일치** | `HedgingException` 베이스에 `NonRetryableHedgingError` 자식 |
| **Python PEP 8 관례** | Python 표준 라이브러리는 `~Error` 접미사가 주류 (`ValueError`, `TypeError`, `ConnectionError`) |

---

## 3. 수정 결과

### 3-1. 방향: `~Exception` → `~Error` 통일

Python PEP 8 및 프로젝트 관례에 맞춰 `~Error`로 통일.

### 3-2. 변경 대상 (13건)

| 변경 전 | 변경 후 | 파일 |
|---------|---------|------|
| `BulkheadException` | `BulkheadError` | `resilience/bulkhead/exceptions.py` |
| `BulkheadFullException` | `BulkheadFullError` | `resilience/bulkhead/exceptions.py` |
| `BulkheadTimeoutException` | `BulkheadTimeoutError` | `resilience/bulkhead/exceptions.py` |
| `HedgingException` | `HedgingError` | `core/hedging/exceptions.py` |
| `HedgingAllFailedException` | `HedgingAllFailedError` | `core/hedging/exceptions.py` |
| `HedgingTimeoutException` | `HedgingTimeoutError` | `core/hedging/exceptions.py` |
| `ChaosException` | `ChaosError` | `shopping/chaos/decorators.py` |
| `PartialFailureException` | `PartialFailureError` | `shopping/chaos/decorators.py` |
| `AsyncTaskChaosException` | `AsyncTaskChaosError` | `shopping/chaos/decorators.py` |
| `Phase2OrphanPGException` | `Phase2OrphanPGError` | `shopping/chaos/decorators.py` |
| `Phase2RollbackFailureException` | `Phase2RollbackFailureError` | `shopping/chaos/decorators.py` |
| `Phase2SilentTaskException` | `Phase2SilentTaskError` | `shopping/chaos/decorators.py` |
| `Phase2PointOrphanException` | `Phase2PointOrphanError` | `shopping/chaos/decorators.py` |

### 3-3. 하위 호환성 처리

각 모듈 하단에 deprecated alias 추가:

```python
# resilience/bulkhead/exceptions.py
BulkheadException = BulkheadError
BulkheadFullException = BulkheadFullError
BulkheadTimeoutException = BulkheadTimeoutError

# core/hedging/exceptions.py
HedgingException = HedgingError
HedgingAllFailedException = HedgingAllFailedError
HedgingTimeoutException = HedgingTimeoutError

# shopping/chaos/decorators.py
ChaosException = ChaosError
PartialFailureException = PartialFailureError
AsyncTaskChaosException = AsyncTaskChaosError
Phase2OrphanPGException = Phase2OrphanPGError
Phase2RollbackFailureException = Phase2RollbackFailureError
Phase2SilentTaskException = Phase2SilentTaskError
Phase2PointOrphanException = Phase2PointOrphanError
```

### 3-4. 참조 업데이트 완료

- [x] 전체 `except ~Exception` 참조 → `except ~Error`로 업데이트
- [x] `__all__` 목록에 새 이름 + deprecated alias 반영
- [x] deprecated alias 추가 (3개 모듈)
- [x] 테스트 코드 내 참조 업데이트
- [x] settings `excluded_exceptions` FQN 문자열 업데이트
- [x] docstring 내 Raises 참조 업데이트

### 3-5. 영향 받은 파일 목록

**예외 정의 (3개 파일)**
- `packages/selfhealing-python/src/selfhealing/resilience/bulkhead/exceptions.py`
- `packages/selfhealing-python/src/selfhealing/core/hedging/exceptions.py`
- `shopping/chaos/decorators.py`

**소스 코드 (13개 파일)**
- `resilience/bulkhead/__init__.py`, `semaphore.py`, `async_semaphore.py`, `threadpool.py`, `base.py`, `decorator.py`
- `core/hedging/__init__.py`, `strategy.py`, `async_strategy.py`, `executor.py`, `async_executor.py`
- `adapters/memory/layered_repository/base.py`
- `settings/circuit_breaker.py`

**프로덕션 참조 (3개 파일)**
- `shopping/tasks/payment_tasks.py`
- `shopping/services/payment_service.py`
- `shopping/tasks/point_tasks.py`

**테스트 코드 (6개 파일)**
- `tests/unit/resilience/bulkhead/test_semaphore_bulkhead.py`
- `tests/unit/resilience/bulkhead/test_async_semaphore_bulkhead.py`
- `tests/unit/resilience/bulkhead/test_threadpool_bulkhead.py`
- `tests/unit/resilience/bulkhead/test_decorator.py`
- `tests/unit/core/test_hedging.py`
- `tests/unit/settings/test_circuit_breaker_bulkhead_excluded.py`, `test_pydantic_settings.py`

### 3-6. 테스트 결과

- bulkhead + hedging + settings 관련 **183개 테스트 전부 통과**
