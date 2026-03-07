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
│   └── (RunbookNotFoundError — 별도 마이그레이션 예정, services/runbook/exceptions.py에 유지)
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

    def extra_context(self) -> dict[str, Any]:
        """structlog 바인딩용 컨텍스트 반환. 서브클래스에서 override."""
        return {"error_code": self.code} if self.code else {}


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

factory.py의 `ValueError` → `AdapterNotFoundError` 변경은 breaking change이지만,
프로덕션 배포 전이므로 **전환기 없이 즉시 전환**한다:

```python
# 즉시 전환 (전환기 다중 상속 불필요)
class AdapterNotFoundError(AdapterError):
    """Raised when a requested adapter is not registered in ProviderRegistry."""
    pass
```

> **결정 근거 (Q1)**: 프로덕션 오픈 전 단계에서 외부 소비자가 없으므로 하위 호환성
> 유지는 불필요한 기술 부채. 다중 상속(ValueError, AdapterError)의 MRO 충돌 리스크를
> 원천 차단하고, 호출부 코드를 일괄 수정하여 깔끔하게 전환한다.

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
| `register_alert()` | `"cell_registry.bulkheads_registered"` | `"registry.alert_registered"` |

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

    def test_adapter_not_found_is_not_value_error(self):
        """AdapterNotFoundError는 ValueError가 아닌 AdapterError 계열."""
        from selfhealing.core.exceptions import AdapterNotFoundError, AdapterError, SelfHealingError
        err = AdapterNotFoundError("test")
        assert isinstance(err, AdapterError)
        assert isinstance(err, SelfHealingError)
        assert not isinstance(err, ValueError)

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
| ValueError → AdapterNotFoundError가 breaking change | `except ValueError` 코드가 더 이상 catch 못함 | 프로덕션 전이므로 즉시 전환, 호출부 일괄 수정 |
| 로깅 이벤트명 변경 시 기존 모니터링 대시보드 | 기존 alert/query 깨짐 | 변경 전 운영팀에 이벤트명 변경 공지, 대시보드 업데이트 |
| 예외 계층 추가 시 import depth 증가 | 순환 참조 위험 | `core/exceptions.py`는 pure Python, 외부 의존성 없음 |

---

## 7. 리뷰 결정 사항 (Q1–Q6)

### Q1. Transitional Inheritance — Breaking Change 즉시 허용 ✅

전환기 다중 상속(`AdapterNotFoundError(ValueError, AdapterError)`)을 삭제한다.
프로덕션 배포 전 단계에서 외부 소비자가 없으므로 하위 호환성 유지는 불필요한 기술 부채이다.
MRO 충돌 리스크를 원천 차단하고, 호출부 `except ValueError`를 `except AdapterNotFoundError`로 일괄 수정한다.

- 2.1.3의 전환기 코드를 즉시 전환으로 변경 완료
- 5.1의 `test_adapter_not_found_is_value_error_during_transition` 테스트 삭제 대상

### Q2. Rich Exception Context — `extra_context()` 메서드 패턴 ⚠️ 수정 채택

`__init__`에 `context: dict`를 추가하는 대신 `extra_context()` 메서드 패턴을 사용한다.

- **기존 패턴과 일관성 유지**: 서브클래스는 개별 타입-안전 속성을 사용하되, `extra_context()`를 override하여 dict로 노출
- **Middleware 통합**: `logger.error(msg, **err.extra_context())` 한 줄로 구조적 로그 바인딩
- **선례**: `AutomationBlockedError.to_dict()`, `BulkheadFullError`의 개별 속성 패턴

2.1.2의 `SelfHealingError` 구현에 `extra_context()` 메서드 추가 완료.

### Q3. 명시적 예외 체이닝 — `raise ... from e` 룰 추가 ✅

외부 예외를 도메인 예외로 감쌀 때 반드시 `raise DomainError(...) from original` 형태를 사용한다.

- **Lint 강제**: `ruff` 룰 `B904` (`raise-without-from-inside-except`) 활성화 대상
- **의도적 체인 끊기**: `raise DomainError(...) from None` — 코드 리뷰에서 이유를 주석으로 남길 것
- **참조**: `docs/laws/LOGGING_STANDARDS.md` §2에 룰 명시

### Q4. HTTP Status Code 매핑 — Web Adapter로 책임 한정 ❌

도메인 예외(`core/exceptions.py`)에 `default_http_status`를 넣지 않는다.
HTTP 매핑 책임은 `api/django/exceptions/classifier.py`의 `ExceptionClassifier`에 한정한다.

- **근거**: 동일 예외도 컨텍스트에 따라 다른 HTTP 코드가 적절 (API vs Celery)
- **확장성**: gRPC/GraphQL 추가 시 코어 수정 없이 각 어댑터에서 매핑
- **구현 시**: 312에서 새로 도입하는 예외에 대한 매핑을 `_classify_custom_exception`에 추가

### Q5. Structlog Processor 런타임 이벤트명 검증 — DEV/PROD 분리 채택 ⚠️ 수정 채택

`settings/log_processors.py`에 이벤트명 검증 프로세서를 추가한다.

- **DEV/TEST**: `SELFHEALING_STRICT_LOG_VALIDATION=true` → Exception 발생 (fail-fast)
- **Production**: 위반을 Prometheus counter로 기록만 → 대시보드 모니터링
- **파이프라인 위치**: `add_logger_name` 직후 (무거운 처리 전)
- **참조**: `docs/laws/LOGGING_STANDARDS.md` §1에 이벤트명 컨벤션 정의

### Q6. Suffix별 로그 레벨 — 가이드라인으로 관리 ❌ 런타임 강제 불채택

런타임 강제 대신 `docs/laws/LOGGING_STANDARDS.md`에 가이드라인으로 문서화한다.

| Suffix | 권장 최소 레벨 | 비고 |
|--------|--------------|------|
| `_failed` | `WARNING` | 단, retry 내부 중간 실패는 `DEBUG` 허용 |
| `_exhausted` | `WARNING` | 모든 재시도 소진 |
| `_error` | `ERROR` | 예상치 못한 에러 |
| `_blocked` | `WARNING` | CB open, budget blocked 등 |
| `_registered`, `_created` | `DEBUG` | 정상 흐름 |

- **근거**: 동일 suffix라도 컨텍스트에 따라 적절한 레벨이 다름
- **즉시 수정**: `connection_health.py:377` debug→warning, `sampling.py:139` info→warning

---

## 8. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-06 | 1.0.0 | 초안 작성 |
| 2026-03-07 | 1.1.0 | Q1–Q6 리뷰 결정 반영: 전환기 삭제, extra_context() 추가, 예외 체이닝/HTTP 매핑/로깅 검증/레벨 가이드라인 결정 |
