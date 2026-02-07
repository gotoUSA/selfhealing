# 198. 동명이의(同名異義) 클래스 이름 변경 계획

> **상태**: ✅ 구현 완료 (2026-02-07)
> - #1 `DegradationLevel` → `RollbackSeverity` (3파일) — 테스트 71 passed
> - #2 `FallbackResult` → `CircuitBreakerFallbackResult` (5파일) — 테스트 49 passed
> - #3 `CircuitBreakerConfig` → `AuditCircuitBreakerConfig` (6파일) — 테스트 67 passed
> - #4 `CircuitBreakerConfig` → `HashChainCircuitBreakerConfig` (6파일) — 테스트 45 passed

> **목적**: 서로 다른 모듈에 같은 이름으로 정의되었지만 의미·구조가 완전히 다른 클래스들의 이름을 문맥에 맞게 변경하여 혼동을 제거한다.
>
> **원칙**: 이름만 같을 뿐 enum 값, 필드, 용도가 다른 클래스를 대상으로 한다.
> 같은 의미의 중복(예: `FailedOperationStatus` 5곳)은 195·196번 문서에서 단일 소스로 통합하므로 본 문서 범위 밖이다.

---

## 1. `DegradationLevel` — core vs audit

### 1-1. 현황

| 구분 | 위치 | 값(members) | 용도 |
|------|------|-------------|------|
| **A** | `core/auto_rollback_guard.py:60` | `NONE`, `MINOR`, `MAJOR`, `CRITICAL` | error_rate·latency_p99 기반 **롤백 심각도** 판단 |
| **B** | `audit/graceful_degradation/enums.py:21` | `NORMAL`, `DEGRADED`, `EMERGENCY`, `READONLY` | Redis/hash-chain **백엔드 가용성 수준** |

```python
# --- A: core/auto_rollback_guard.py L60-66 ---
class DegradationLevel(str, Enum):
    """저하 수준"""
    NONE = "none"
    MINOR = "minor"       # 경미 - 알림만
    MAJOR = "major"       # 심각 - 롤백 고려
    CRITICAL = "critical"  # 긴급 - 즉시 롤백

# --- B: audit/graceful_degradation/enums.py L21-37 ---
class DegradationLevel(str, Enum):
    """Hash chain degradation levels."""
    NORMAL = "normal"
    DEGRADED = "degraded"
    EMERGENCY = "emergency"
    READONLY = "readonly"
```

### 1-2. 사용 분석

| 대상 | 사용처 수 | 소스 파일 | 테스트 파일 |
|------|-----------|-----------|-------------|
| **A** (core) | 39 | `core/auto_rollback_guard.py` (13곳) | `tests/core/test_auto_rollback_guard.py` (22곳), `tests/unit/selfhealing/test_runtime_feedback.py` (3곳) |
| **B** (audit) | 62 | `audit/graceful_degradation/degradation_manager.py`, `manager.py`, `enums.py`, `__init__.py` | `tests/chaos/test_redis_failure.py`, `tests/unit/audit/graceful_degradation/test_degradation_level.py`, `test_degradation_manager.py`, `test_circuit_breaker.py`, `test_graceful_manager.py` |

### 1-3. 변경 계획

| 변경 대상 | 현재 이름 | 새 이름 | 근거 |
|-----------|-----------|---------|------|
| **A** (core) | `DegradationLevel` | **`RollbackSeverity`** | 값이 심각도(NONE→CRITICAL)이며 `_assess_degradation()`에서 error_rate·latency로 판단 후 롤백 결정에 사용 |
| **B** (audit) | `DegradationLevel` | 유지 (변경 없음) | 패키지명 `graceful_degradation`과 의미 일치, 62곳 사용으로 영향 광범위 |

### 1-4. 수정 대상 파일

| 파일 | 변경 내용 |
|------|-----------|
| `core/auto_rollback_guard.py` | 클래스명 `DegradationLevel` → `RollbackSeverity` (정의 + 내부 13곳) |
| `tests/core/test_auto_rollback_guard.py` | import 및 참조 22곳 |
| `tests/unit/selfhealing/test_runtime_feedback.py` | import 및 참조 3곳 |

**총 영향**: 소스 1파일 + 테스트 2파일 = **3파일**

---

## 2. `FallbackResult` — core vs services/circuit_breaker vs api/rate_limit

### 2-1. 현황

| 구분 | 위치 | 필드 | 용도 |
|------|------|------|------|
| **A** | `core/fallback_strategy.py:37` | `Generic[T]`: `value`, `used_fallback`, `fallback_mode`, `original_error` | 범용 **fallback 전략 결과** (cache miss, API down 등) |
| **B** | `services/circuit_breaker/config.py:179` | `allowed`, `fallback_used`, `fallback_type`, `fallback_data`, `message` + 팩토리 메서드 5개 (`allow()`, `block()`, `from_cache()`, `to_dlq()`, `default_response()`) | **서킷 브레이커 전용** fallback 응답 |
| **C** | `api/django/rate_limit.py:645` | `bypassed`, `reason`, `hook_name`, `priority` | **함수 스코프** 로컬 클래스 (ImportError fallback용) |

```python
# --- A: core/fallback_strategy.py L37-48 ---
@dataclass
class FallbackResult(Generic[T]):
    """Result of a fallback operation"""
    value: T | None
    used_fallback: bool
    fallback_mode: FallbackMode | None = None
    original_error: str | None = None

# --- B: services/circuit_breaker/config.py L179-237 ---
@dataclass
class FallbackResult:
    """Result when circuit breaker provides a fallback response."""
    allowed: bool
    fallback_used: bool = False
    fallback_type: str = ""        # "cache", "dlq", "default", "none"
    fallback_data: Any = None
    message: str = ""
    # + classmethod: allow(), block(), from_cache(), to_dlq(), default_response()

# --- C: api/django/rate_limit.py L645 (함수 내부) ---
@dataclass
class FallbackResult:            # ImportError 시 로컬 fallback
    bypassed: bool = False
    reason: str = "Hook registry not available"
    hook_name: str = ""
    priority: int = 0
```

### 2-2. 사용 분석

| 대상 | 사용처 수 | 주요 소스 파일 |
|------|-----------|---------------|
| **A** (core) | 46 | `core/__init__.py` (re-export), `core/fallback_strategy.py`, `core/hedging/async_strategy.py`, `core/hedging/strategy.py`, `tests/core/test_fallback_strategy.py`, `tests/self_healing/integration/test_partial_partition_integration.py` |
| **B** (services) | 33 | `services/circuit_breaker/__init__.py`, `services/circuit_breaker/service.py`, `tests/services/circuit_breaker/test_config.py`, `tests/services/test_circuit_breaker_enhancements.py` |
| **C** (api) | 0 (외부 노출 없음) | 함수 내부에서 생성·반환 후 소멸 |

### 2-3. 변경 계획

| 변경 대상 | 현재 이름 | 새 이름 | 근거 |
|-----------|-----------|---------|------|
| **A** (core) | `FallbackResult` | 유지 (변경 없음) | `core/__init__.py`에서 re-export하는 공개 API, `Generic[T]` 범용 설계 |
| **B** (services) | `FallbackResult` | **`CircuitBreakerFallbackResult`** | CB 전용 팩토리 메서드(`allow`, `block`, `from_cache`, `to_dlq`) 보유, `allowed` 필드가 CB 게이팅 의미 |
| **C** (api) | `FallbackResult` | 유지 (변경 없음) | 함수 스코프 로컬 클래스, 외부 노출 없음 |

### 2-4. 수정 대상 파일

| 파일 | 변경 내용 |
|------|-----------|
| `services/circuit_breaker/config.py` | 클래스명 `FallbackResult` → `CircuitBreakerFallbackResult` (정의 + 내부 6곳) |
| `services/circuit_breaker/__init__.py` | import 및 참조 2곳 |
| `services/circuit_breaker/service.py` | import 및 참조 10곳 |
| `tests/services/circuit_breaker/test_config.py` | import 및 참조 10곳 |
| `tests/services/test_circuit_breaker_enhancements.py` | import 및 참조 6곳 |

**총 영향**: 소스 3파일 + 테스트 2파일 = **5파일**

---

## 3. `CircuitBreakerConfig` — services vs audit/resilience vs audit/graceful_degradation

### 3-1. 현황

| 구분 | 위치 | 필드 수 | 주요 필드 | 용도 |
|------|------|---------|-----------|------|
| **A** | `services/circuit_breaker/config.py:34` | 20+ | `enabled`, `failure_threshold=5`, `recovery_timeout=60`, `minimum_calls=10`, `sliding_window_size`, `failure_rate_threshold`, `fallback_strategy`, DDoS 방어 등 | **메인 서비스** CB 설정 (RuntimeConfigManager 연동) |
| **B** | `audit/resilience/circuit_breaker.py:27` | 4 | `failure_threshold=3`, `success_threshold=2`, `timeout_seconds=30`, `call_timeout_seconds=5` | **audit 감사 레코더** 전용 경량 CB |
| **C** | `audit/graceful_degradation/enums.py:93` | 4 | `failure_threshold=5`, `recovery_timeout_seconds=30`, `half_open_requests=3`, `success_threshold=2` + `from_settings()` | **hash chain fallback** 전용 CB |

```python
# --- A: services/circuit_breaker/config.py L34-80 ---
@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker operations."""
    enabled: bool = False
    failure_threshold: int = 5
    recovery_timeout: int = 60
    success_threshold: int = 2
    minimum_calls: int = 10
    sliding_window_size: int = 100
    failure_rate_threshold: float = 0.0
    fallback_strategy: str = "block"
    fallback_cache_ttl_seconds: int = 300
    cb_open_burn_rate_multiplier: float = 10.0
    manual_override_ttl_minutes: int = 90
    half_open_request_limit: int = 10
    max_pending_duration_hours: int = 4
    max_retry_lifetime_hours: int = 24
    rate_limit_cascade_threshold: int = 10
    rate_limit_cascade_window_seconds: int = 60
    self_ddos_protection_enabled: bool = True
    self_ddos_request_threshold: int = 100
    self_ddos_window_seconds: int = 10
    self_ddos_backoff_multiplier: float = 2.0

# --- B: audit/resilience/circuit_breaker.py L27-33 ---
@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 3
    success_threshold: int = 2
    timeout_seconds: float = 30.0
    call_timeout_seconds: float = 5.0

# --- C: audit/graceful_degradation/enums.py L93-137 ---
@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_requests: int = 3
    success_threshold: int = 2
    # + from_settings() classmethod
```

### 3-2. 사용 분석

| 대상 | 사용처 수 | 주요 소스 파일 |
|------|-----------|---------------|
| **A** (services) | — | `services/circuit_breaker/config.py` (정의), `services/circuit_breaker/__init__.py`, `services/circuit_breaker/service.py`, `from_settings()` 경유 전역 사용 |
| **B** (audit/resilience) | 26 | `audit/resilience/circuit_breaker.py` (정의+4곳), `audit/resilience/__init__.py` (re-export), `audit/__init__.py` (lazy import re-export), `audit/resilient_recorder.py` (import+인스턴스 생성), `tests/audit/test_resilience.py` (14곳), `tests/unit/audit/pipeline/test_audit_lazy_import.py` (2곳) |
| **C** (audit/graceful_degradation) | 28 | `audit/graceful_degradation/enums.py` (정의), `audit/graceful_degradation/__init__.py` (re-export), `audit/graceful_degradation/circuit_breaker.py` (3곳), `tests/chaos/test_redis_failure.py`, `tests/integration/test_settings_from_settings_pattern.py` (5곳), `tests/unit/audit/graceful_degradation/test_circuit_breaker.py` (14곳) |

### 3-3. 변경 계획

| 변경 대상 | 현재 이름 | 새 이름 | 근거 |
|-----------|-----------|---------|------|
| **A** (services) | `CircuitBreakerConfig` | 유지 (변경 없음) | 메인 서비스 레이어의 정식 설정, 20+ 필드로 가장 포괄적, RuntimeConfigManager 연동 |
| **B** (audit/resilience) | `CircuitBreakerConfig` | **`AuditCircuitBreakerConfig`** | audit 감사 레코더 전용 4필드 경량 설정, `resilient_recorder.py`에서만 인스턴스 생성 |
| **C** (audit/graceful_degradation) | `CircuitBreakerConfig` | **`HashChainCircuitBreakerConfig`** | hash chain fallback 전용, `from_settings()` → `GracefulDegradationSettings` 연동, `half_open_requests` 필드가 hash chain 특화 |

### 3-4. 수정 대상 파일

#### B → `AuditCircuitBreakerConfig`

| 파일 | 변경 내용 |
|------|-----------|
| `audit/resilience/circuit_breaker.py` | 클래스명 변경 (정의 + 내부 4곳) |
| `audit/resilience/__init__.py` | `__all__` 및 import 수정 |
| `audit/__init__.py` | lazy import dict 키 수정 (`"AuditCircuitBreakerConfig"`) |
| `audit/resilient_recorder.py` | import 및 인스턴스 생성 2곳 |
| `tests/audit/test_resilience.py` | import 및 참조 14곳 |
| `tests/unit/audit/pipeline/test_audit_lazy_import.py` | 참조 2곳 |

**소계**: 소스 4파일 + 테스트 2파일 = **6파일**

#### C → `HashChainCircuitBreakerConfig`

| 파일 | 변경 내용 |
|------|-----------|
| `audit/graceful_degradation/enums.py` | 클래스명 변경 (정의 + `from_settings` 반환 타입 + `__all__`) |
| `audit/graceful_degradation/__init__.py` | re-export 수정 |
| `audit/graceful_degradation/circuit_breaker.py` | import 및 참조 3곳 |
| `tests/chaos/test_redis_failure.py` | import 및 참조 |
| `tests/integration/test_settings_from_settings_pattern.py` | import 및 참조 5곳 |
| `tests/unit/audit/graceful_degradation/test_circuit_breaker.py` | import 및 참조 14곳 |

**소계**: 소스 3파일 + 테스트 3파일 = **6파일**

---

## 4. 변경하지 않는 항목과 근거

| 클래스 | 위치 | 유지 근거 |
|--------|------|-----------|
| `DegradationLevel` | `audit/graceful_degradation/enums.py` | 패키지명(`graceful_degradation`)과 의미 일치, 62곳 사용 |
| `FallbackResult` | `core/fallback_strategy.py` | `core/__init__.py` re-export 공개 API, `Generic[T]` 범용 설계, 46곳 사용 |
| `FallbackResult` | `api/django/rate_limit.py:645` | 함수 내부 로컬 클래스, `ImportError` fallback 전용, 외부 노출 0 |
| `CircuitBreakerConfig` | `services/circuit_breaker/config.py` | 메인 서비스 정식 설정, RuntimeConfigManager 연동, 가장 포괄적 |

---

## 5. 실행 요약

| # | 현재 이름 | 새 이름 | 영향 파일 수 |
|---|-----------|---------|-------------|
| 1 | `core/auto_rollback_guard.py::DegradationLevel` | `RollbackSeverity` | 3 |
| 2 | `services/circuit_breaker/config.py::FallbackResult` | `CircuitBreakerFallbackResult` | 5 |
| 3 | `audit/resilience/circuit_breaker.py::CircuitBreakerConfig` | `AuditCircuitBreakerConfig` | 6 |
| 4 | `audit/graceful_degradation/enums.py::CircuitBreakerConfig` | `HashChainCircuitBreakerConfig` | 6 |
| **합계** | | | **20파일** (중복 제거 시 약 17파일) |

### 실행 순서

1. **#3, #4** (audit 내부) — audit 서브시스템은 독립적이므로 먼저 변경해도 메인 서비스에 영향 없음
2. **#2** — `services/circuit_breaker` 내부 완결, core에 영향 없음
3. **#1** — `core/auto_rollback_guard.py` 단일 파일 + 테스트 2파일

### 주의사항

- 각 변경 후 해당 모듈의 `__all__` 목록 반드시 동기화
- `audit/__init__.py`의 lazy import dict 키는 새 이름으로 변경 필수
- 이전 이름으로의 역호환 alias가 필요한 경우 deprecated warning과 함께 추가 가능 (선택)
