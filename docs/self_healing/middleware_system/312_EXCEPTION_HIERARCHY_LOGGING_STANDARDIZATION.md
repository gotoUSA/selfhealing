# 312. Exception Hierarchy & Logging Standardization — 예외 계층/로깅 이벤트명 통일

> **Status**: Refactor
> **Severity**: P1 (HIGH)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/exceptions.py` — base 클래스 없는 예외
> - `packages/selfhealing-python/src/selfhealing/factory.py` — 로깅 이벤트명 하드코딩 오류
> - `packages/selfhealing-python/src/selfhealing/services/dlq/entry_operations.py` — ValueError 사용
> - `packages/selfhealing-python/src/selfhealing/resilience/bulkhead/exceptions.py` — 참조 패턴
> **References**:
> - 309 — 아키텍처 패턴 통일
> - 310 — 기능 중복 제거

---

## 1. 현황 및 문제

### 1.1 예외 계층 불일치

동일한 에러 시나리오에 대해 모듈마다 다른 예외 타입을 사용한다.

| 모듈 | 예외 클래스 | Base 클래스 | 패턴 |
|------|-----------|-------------|------|
| `resilience/bulkhead` | `BulkheadError` → `BulkheadFullError` | `BulkheadError(Exception)` | 도메인 base O |
| `core/hedging` | `HedgingError` → `HedgingTimeoutError` 등 | `HedgingError(Exception)` | 도메인 base O |
| `services/circuit_breaker` | `CircuitBreakerOpenError(Exception)` | **없음** (직접 Exception 상속) | 도메인 base X |
| `adapters/ipc` | `IPCError` → `IPCConnectionError` 등 | `IPCError(Exception)` + JSON-RPC 코드 | 도메인 base O + 코드 |
| `services/runbook` | `RunbookNotFoundError` | 불명확 | 도메인 전용 |
| `factory.py` | `ValueError` | Python 내장 | 범용 예외 |
| `services/dlq` | `ValueError` | Python 내장 | 범용 예외 |

**문제**:
- `except CircuitBreakerOpenError`를 하려면 정확한 import가 필요하지만, base class가 없어 `except CircuitBreakerError`로 포괄적 catch 불가
- `except ValueError`로 DLQ 에러를 잡으면 다른 ValueError도 같이 잡힘
- 에러 핸들링 코드에서 일관된 패턴을 적용할 수 없음

### 1.2 Factory.py 로깅 이벤트명 오류

`factory.py`의 **8개 이상의 등록 메서드**가 잘못된 이벤트명을 사용한다.

```python
# factory.py:90 — register_cache 내부
logger.debug("cell_registry.bulkheads_registered", factory_name=name)

# factory.py:100 — register_queue 내부
logger.debug("cell_registry.bulkheads_registered", factory_name=name)

# factory.py:109 — register_failed_operation_repo 내부
logger.debug("cell_registry.bulkheads_registered", factory_name=name)
```

**모든 등록 메서드가 `"cell_registry.bulkheads_registered"`라는 동일하고 잘못된 이벤트명을 사용.**

반면, 후반부 메서드들은 올바른 이벤트명을 사용:

```python
# factory.py:211 — register_statistics_adapter 내부
logger.debug("registry.statistics_adapter_registered", ...)  # 올바름

# factory.py:237 — register_postmortem_model 내부
logger.debug("registry.postmortem_model_registered", ...)  # 올바름
```

**문제**:
- 로그 검색 시 `"cell_registry.bulkheads_registered"`로 검색하면 cache, queue, repo 등록이 모두 섞여 나옴
- 실제로 bulkhead 등록과 무관한 이벤트가 bulkhead 이벤트명을 사용
- 운영 환경에서 로그 기반 모니터링/알림 정확도 저하

---

## 2. 개선 계획

### 2.1 Phase 1: SelfHealingError Base 클래스 도입 (P1)

**목표**: 라이브러리 전체를 포괄하는 예외 계층 구축

#### 2.1.1 예외 계층 설계

```
SelfHealingError (Exception)
├── AdapterError
│   ├── AdapterNotFoundError          # ProviderRegistry에서 어댑터 못 찾음
│   ├── AdapterInitializationError    # 어댑터 초기화 실패
│   └── AdapterConnectionError       # 외부 시스템 연결 실패
├── CircuitBreakerError
│   ├── CircuitBreakerOpenError       # CB 열림 상태에서 요청 차단
│   └── CircuitBreakerTransitionError # CB 상태 전환 실패
├── DLQError
│   ├── DLQEntryNotFoundError         # DLQ 항목 못 찾음
│   └── DLQReplayError               # DLQ 재실행 실패
├── ResilienceError
│   ├── BulkheadError
│   │   └── BulkheadFullError
│   ├── HedgingError
│   │   ├── HedgingTimeoutError
│   │   └── HedgingAllFailedError
│   └── RetryExhaustedError          # 재시도 횟수 초과
├── IPCError
│   ├── IPCConnectionError
│   └── IPCProtocolError
├── RunbookError
│   └── RunbookNotFoundError
└── ConfigurationError                # 설정 관련 에러
    └── SettingsValidationError
```

#### 2.1.2 구현

```python
# core/exceptions.py (신규)
"""
Self-Healing 라이브러리 예외 계층.

모든 라이브러리 예외는 SelfHealingError를 상속한다.
호출자는 `except SelfHealingError`로 모든 라이브러리 에러를 포괄적으로 잡을 수 있다.
"""


class SelfHealingError(Exception):
    """Base exception for all self-healing library errors."""

    def __init__(self, message: str = "", *, code: str = ""):
        super().__init__(message)
        self.code = code


class AdapterError(SelfHealingError):
    """Base exception for adapter-related errors."""
    pass


class AdapterNotFoundError(AdapterError):
    """Raised when a requested adapter is not registered in ProviderRegistry."""
    pass


class AdapterInitializationError(AdapterError):
    """Raised when an adapter fails to initialize."""
    pass


class CircuitBreakerError(SelfHealingError):
    """Base exception for circuit breaker errors."""
    pass


class DLQError(SelfHealingError):
    """Base exception for DLQ (Dead Letter Queue) errors."""
    pass


class DLQEntryNotFoundError(DLQError):
    """Raised when a DLQ entry is not found."""
    pass


class ResilienceError(SelfHealingError):
    """Base exception for resilience pattern errors (bulkhead, hedging, retry)."""
    pass


class RetryExhaustedError(ResilienceError):
    """Raised when all retry attempts are exhausted."""
    pass


class ConfigurationError(SelfHealingError):
    """Base exception for configuration and settings errors."""
    pass
```

#### 2.1.3 기존 예외 마이그레이션

| 기존 예외 | 변경 | Backward Compatibility |
|----------|------|----------------------|
| `CircuitBreakerOpenError(Exception)` | `CircuitBreakerOpenError(CircuitBreakerError)` | `except CircuitBreakerOpenError` 여전히 동작 |
| `BulkheadError(Exception)` | `BulkheadError(ResilienceError)` | `except BulkheadError` 여전히 동작 |
| `HedgingError(Exception)` | `HedgingError(ResilienceError)` | `except HedgingError` 여전히 동작 |
| `ValueError` in factory.py | `AdapterNotFoundError` | breaking change — catch 코드 수정 필요 |
| `ValueError` in DLQ | `DLQEntryNotFoundError` | breaking change — catch 코드 수정 필요 |

**Backward compatibility 전략**:

```python
# services/circuit_breaker/exceptions.py
from selfhealing.core.exceptions import CircuitBreakerError

# 기존: class CircuitBreakerOpenError(Exception):
# 변경:
class CircuitBreakerOpenError(CircuitBreakerError):
    """Raised when a circuit breaker is open and blocking requests."""
    pass
```

기존 `except CircuitBreakerOpenError`는 변경 없이 동작한다 (하위 타입 관계 유지).
새로운 `except CircuitBreakerError`도 가능해진다 (상위 타입 catch).

factory.py의 `ValueError` → `AdapterNotFoundError` 변경은 breaking change이므로:
1. 먼저 `AdapterNotFoundError(ValueError, AdapterError)`로 양쪽 상속
2. 다음 major 버전에서 `ValueError` 상속 제거

```python
# 전환기 (v3.x)
class AdapterNotFoundError(ValueError, AdapterError):
    """Transitional: inherits both ValueError and AdapterError."""
    pass

# 목표 (v4.0)
class AdapterNotFoundError(AdapterError):
    pass
```

---

### 2.2 Phase 2: Factory.py 로깅 이벤트명 수정 (P0)

**목표**: 모든 등록 메서드의 이벤트명을 정확하게 수정

#### 2.2.1 수정 매핑

| 메서드 | 현재 이벤트명 | 올바른 이벤트명 |
|--------|-------------|---------------|
| `register_cache()` | `"cell_registry.bulkheads_registered"` | `"registry.cache_registered"` |
| `register_queue()` | `"cell_registry.bulkheads_registered"` | `"registry.queue_registered"` |
| `register_failed_operation_repo()` | `"cell_registry.bulkheads_registered"` | `"registry.failed_operation_repo_registered"` |
| `register_circuit_breaker_repo()` | `"cell_registry.bulkheads_registered"` | `"registry.circuit_breaker_repo_registered"` |
| `register_security_repo()` | `"cell_registry.bulkheads_registered"` | `"registry.security_repo_registered"` |
| `register_event_journal_repo()` | `"cell_registry.bulkheads_registered"` | `"registry.event_journal_repo_registered"` |
| `register_audit_adapter()` | `"cell_registry.bulkheads_registered"` | `"registry.audit_adapter_registered"` |
| `register_alert_adapter()` | `"cell_registry.bulkheads_registered"` | `"registry.alert_adapter_registered"` |

#### 2.2.2 로깅 이벤트명 컨벤션 정의

```
{module}.{action}

module: "registry", "circuit_breaker", "dlq", "audit", "replay", ...
action: "{noun}_{verb_past}", e.g., "cache_registered", "entry_replayed", "state_changed"

예시:
- registry.cache_registered
- registry.queue_registered
- circuit_breaker.state_changed
- dlq.entry_created
- replay.execution_completed
- audit.entry_logged
```

#### 2.2.3 불완전한 로그 문도 수정

```python
# factory.py:784, 812 — 기존
logger.debug("registry")  # 이벤트명만, 컨텍스트 없음

# 수정
logger.debug("registry.instances_cleared", scope="all")
```

---

### 2.3 Phase 3: 이중 감사 경로 문서화 (P2)

DLQServiceBase에서 request 유무에 따라 **2가지 감사 경로**가 존재한다.

```python
# services/dlq/base.py:72-75
# request가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
# request가 없으면 → 직접 adapter 호출 (Celery 등 비동기 컨텍스트)
```

이는 의도적 설계이므로 **코드 변경 없이 ADR로 문서화**:

```
ADR-005: Dual Audit Path Strategy

Context: Django request 내에서는 AuditMiddleware가 응답 완료 후 일괄 기록.
Celery task 등 request 없는 컨텍스트에서는 직접 AuditLogAdapter 호출.

Decision: 두 경로를 유지. 이유:
1. Request context에서는 배치 기록이 성능상 유리
2. Async context에서는 즉시 기록이 데이터 유실 방지에 유리
3. 통합 시 복잡도 대비 이점 불명확

Consequences:
- audit 로그 타이밍이 context에 따라 다름 (배치 vs 즉시)
- 중복 기록 방지는 idempotency key로 처리
```

---

## 3. 로깅 이벤트명 표준 — 전체 컨벤션

### 3.1 Naming Convention

```
{component}.{entity}_{action}

component: 모듈명 (registry, circuit_breaker, dlq, audit, replay, ...)
entity: 대상 (cache, entry, state, adapter, ...)
action: 과거형 (registered, created, changed, failed, completed, ...)
```

### 3.2 표준 이벤트 카탈로그

| 이벤트명 | 사용 시점 | 필수 필드 |
|----------|----------|-----------|
| `registry.{type}_registered` | 어댑터/리포지토리 등록 | `name` |
| `registry.{type}_resolved` | 어댑터/리포지토리 조회 | `name` |
| `registry.instances_cleared` | reset/clear 호출 | `scope` |
| `circuit_breaker.state_changed` | CB 상태 전이 | `service_name`, `from_state`, `to_state` |
| `circuit_breaker.failure_recorded` | 실패 기록 | `service_name`, `failure_count` |
| `dlq.entry_created` | DLQ 항목 생성 | `domain`, `failure_type` |
| `dlq.entry_replayed` | DLQ 재실행 | `entry_id`, `success` |
| `audit.entry_logged` | 감사 로그 기록 | `action`, `actor` |
| `retry.attempt_failed` | 재시도 실패 | `attempt`, `max_retries`, `exception` |
| `retry.exhausted` | 재시도 소진 | `total_attempts` |
| `service.fallback_adapter` | fallback 어댑터 사용 | `adapter` |

---

## 4. 구현 순서

| Phase | 작업 | 파일 수 | 우선순위 |
|-------|------|---------|----------|
| 1 | Factory.py 로깅 이벤트명 수정 | 1 | P0 |
| 2 | `core/exceptions.py` 신규 생성 + base 클래스 정의 | 1 | P1 |
| 3 | CircuitBreakerOpenError base 변경 | 1 | P1 |
| 4 | BulkheadError, HedgingError base 변경 | 2 | P1 |
| 5 | factory.py ValueError → AdapterNotFoundError 전환 | 1 | P1 |
| 6 | DLQ ValueError → DLQEntryNotFoundError 전환 | 1 | P1 |
| 7 | 이벤트명 컨벤션 ADR 작성 | 1 | P2 |

---

## 5. 테스트 계획

### 5.1 예외 계층 테스트

```python
# tests/unit/test_exception_hierarchy.py

class TestExceptionHierarchy:
    def test_circuit_breaker_open_is_selfhealing_error(self):
        from selfhealing.services.circuit_breaker.exceptions import CircuitBreakerOpenError
        from selfhealing.core.exceptions import SelfHealingError, CircuitBreakerError
        err = CircuitBreakerOpenError("test")
        assert isinstance(err, CircuitBreakerError)
        assert isinstance(err, SelfHealingError)
        assert isinstance(err, Exception)

    def test_bulkhead_full_is_resilience_error(self):
        from selfhealing.resilience.bulkhead.exceptions import BulkheadFullError
        from selfhealing.core.exceptions import ResilienceError, SelfHealingError
        err = BulkheadFullError("test")
        assert isinstance(err, ResilienceError)
        assert isinstance(err, SelfHealingError)

    def test_adapter_not_found_is_value_error_during_transition(self):
        """전환기: ValueError로도 잡을 수 있는지 확인."""
        from selfhealing.core.exceptions import AdapterNotFoundError
        err = AdapterNotFoundError("test")
        assert isinstance(err, ValueError)  # 전환기 호환
        assert isinstance(err, SelfHealingError)

    def test_catch_all_selfhealing_errors(self):
        """SelfHealingError로 모든 라이브러리 에러를 포괄할 수 있는지 확인."""
        from selfhealing.core.exceptions import (
            SelfHealingError, CircuitBreakerError, DLQError,
            ResilienceError, AdapterError,
        )
        for ErrorClass in [CircuitBreakerError, DLQError, ResilienceError, AdapterError]:
            try:
                raise ErrorClass("test")
            except SelfHealingError:
                pass  # 예상대로 catch됨
```

### 5.2 로깅 이벤트명 테스트

```python
# tests/unit/test_factory_logging.py

class TestFactoryLogging:
    def test_no_cell_registry_event_names(self):
        """factory.py에서 'cell_registry' 이벤트명이 사용되지 않는지 검증."""
        from pathlib import Path
        factory_source = Path(
            "packages/selfhealing-python/src/selfhealing/factory.py"
        ).read_text()
        assert "cell_registry" not in factory_source, (
            "factory.py still uses deprecated 'cell_registry' event names"
        )

    def test_all_log_events_follow_convention(self):
        """모든 로그 이벤트가 'module.entity_action' 패턴을 따르는지 검증."""
        import re
        from pathlib import Path
        factory_source = Path(
            "packages/selfhealing-python/src/selfhealing/factory.py"
        ).read_text()
        # logger.debug("event_name", ...) 패턴 추출
        events = re.findall(r'logger\.\w+\("([^"]+)"', factory_source)
        pattern = re.compile(r'^[a-z_]+\.[a-z_]+$')
        for event in events:
            assert pattern.match(event), (
                f"Event name '{event}' doesn't follow 'module.entity_action' convention"
            )
```

---

## 6. 위험 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| ValueError → AdapterNotFoundError가 breaking change | `except ValueError` 코드가 더 이상 catch 못함 | 전환기: `class AdapterNotFoundError(ValueError, AdapterError)` |
| 로깅 이벤트명 변경 시 기존 모니터링 대시보드 | 기존 alert/query 깨짐 | 변경 전 운영팀에 이벤트명 변경 공지, 대시보드 업데이트 |
| 예외 계층 추가 시 import depth 증가 | 순환 참조 위험 | `core/exceptions.py`는 pure Python, 외부 의존성 없음 |

---

## 7. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-06 | 1.0.0 | 초안 작성 |
