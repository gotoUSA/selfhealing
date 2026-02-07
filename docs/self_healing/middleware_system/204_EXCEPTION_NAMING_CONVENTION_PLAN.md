# 204. 예외 클래스 접미사 통일 계획 (Error vs Exception)

> **상태**: 📋 계획
> **목적**: 커스텀 예외 클래스의 접미사를 `~Error` 또는 `~Exception` 중 하나로 통일한다.

---

## 1. 현황

### 1-1. 주류 패턴: `~Error` (46건)

프로젝트 내 커스텀 예외 클래스의 **88.5%**가 `~Error` 접미사:

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

# audit/checkpoint_manager.py L98
class CheckpointError(Exception):

# audit/checkpoint_strategy.py L123
class CheckpointError(Exception):

# audit/checkpoint_strategy.py L129
class CheckpointCorruptedError(CheckpointError):

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

### 1-2. 소수 패턴: `~Exception` (6건)

**2개 모듈**에서만 `~Exception` 접미사 사용:

```python
# resilience/bulkhead/exceptions.py L12
class BulkheadException(Exception):
    """Base exception for bulkhead operations."""

# resilience/bulkhead/exceptions.py L18
class BulkheadFullException(BulkheadException):
    """Raised when bulkhead is at capacity."""

# resilience/bulkhead/exceptions.py L43
class BulkheadTimeoutException(BulkheadException):
    """Raised when waiting for bulkhead slot times out."""


# core/hedging/exceptions.py L11
class HedgingException(Exception):
    """Base exception for hedging operations."""

# core/hedging/exceptions.py L17
class HedgingAllFailedException(HedgingException):
    """Raised when all hedging attempts fail."""

# core/hedging/exceptions.py L35
class HedgingTimeoutException(HedgingException):
    """Raised when hedging operation times out."""
```

### 1-3. 혼합 사례 (hedging 내부)

```python
# core/hedging/exceptions.py L51
class NonRetryableHedgingError(HedgingException):   # ← Error 접미사
    """..."""

# core/hedging/exceptions.py L75
class HedgingDisabledError(HedgingException):        # ← Error 접미사
    """..."""
```

`core/hedging/exceptions.py` 파일 내에서조차 **같은 계층에 `~Exception`과 `~Error`가 공존**.

---

## 2. 문제점

| 항목 | 설명 |
|------|------|
| **일관성 부족** | 프로젝트의 88.5%는 `~Error`, 11.5%는 `~Exception` |
| **except 절 혼란** | `except BulkheadException`과 `except IPCError`를 동시에 처리 |
| **같은 파일 내 불일치** | `HedgingException` 베이스에 `NonRetryableHedgingError` 자식 |
| **Python PEP 8 관례** | Python 표준 라이브러리는 `~Error` 접미사가 주류 (`ValueError`, `TypeError`, `ConnectionError`) |

---

## 3. 수정 계획

### 3-1. 방향: `~Exception` → `~Error` 통일

Python PEP 8 및 프로젝트 관례 (88.5%) 에 맞춰 `~Error`로 통일.

### 3-2. 변경 대상

| 현재 이름 | 변경 후 | 파일 |
|-----------|---------|------|
| `BulkheadException` | `BulkheadError` | `resilience/bulkhead/exceptions.py:12` |
| `BulkheadFullException` | `BulkheadFullError` | `resilience/bulkhead/exceptions.py:18` |
| `BulkheadTimeoutException` | `BulkheadTimeoutError` | `resilience/bulkhead/exceptions.py:43` |
| `HedgingException` | `HedgingError` | `core/hedging/exceptions.py:11` |
| `HedgingAllFailedException` | `HedgingAllFailedError` | `core/hedging/exceptions.py:17` |
| `HedgingTimeoutException` | `HedgingTimeoutError` | `core/hedging/exceptions.py:35` |

### 3-3. 하위 호환성 처리

```python
# resilience/bulkhead/exceptions.py — 추가
BulkheadException = BulkheadError  # deprecated alias
BulkheadFullException = BulkheadFullError
BulkheadTimeoutException = BulkheadTimeoutError

# core/hedging/exceptions.py — 추가
HedgingException = HedgingError  # deprecated alias
HedgingAllFailedException = HedgingAllFailedError
HedgingTimeoutException = HedgingTimeoutError
```

### 3-4. 검증 항목

- [ ] 전체 `except BulkheadException` / `except HedgingException` 참조 업데이트
- [ ] `__all__` 목록에 새 이름 반영
- [ ] deprecated alias에 `warnings.warn()` 추가 검토
- [ ] 테스트 코드 내 참조 업데이트
