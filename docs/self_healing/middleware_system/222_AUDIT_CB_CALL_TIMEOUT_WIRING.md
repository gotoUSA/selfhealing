# 222. Audit Circuit Breaker `call_timeout_seconds` Dead Code 연결

> **상태**: 📋 구현 대기
> **목적**: `AuditCircuitBreakerConfig.call_timeout_seconds`가 설정만 존재하고 실제 사용처가 없는 Dead Code 문제를 해결하여, 외부 감사 백엔드 hang 시 Background flush thread 교착을 방지한다.
> **기준일**: 2026-02-12
> **선행 문서**: 92_CONFIG_IMPLEMENTATION_GUIDE.md

---

## 1. 현재 상태 분석 (코드 근거)

### 1.1 Dead Code: `call_timeout_seconds` 정의만 존재

**파일**: `audit/resilience/circuit_breaker.py` L24-L31

```python
@dataclass
class AuditCircuitBreakerConfig:
    """Configuration for audit circuit breaker."""

    failure_threshold: int = 3  # Failures before opening
    success_threshold: int = 2  # Successes to close from half-open
    timeout_seconds: float = 30.0  # Time before trying half-open
    call_timeout_seconds: float = 5.0  # Timeout for individual calls  ← 사용처 0건
```

- `timeout_seconds`: `_check_timeout()` 메서드에서 OPEN → HALF_OPEN 전환에 사용됨 (정상)
- `call_timeout_seconds`: **코드베이스 전체에서 읽히는 곳이 없음** (Dead Code)

### 1.2 `get_stats()`에서도 누락

**파일**: `audit/resilience/circuit_breaker.py` L175-L191

```python
def get_stats(self) -> dict[str, Any]:
    with self._lock:
        return {
            ...
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "success_threshold": self.config.success_threshold,
                "timeout_seconds": self.config.timeout_seconds,
                # call_timeout_seconds 누락
            },
        }
```

### 1.3 `ResilientRecorderConfig`에서도 전달하지 않음

**파일**: `audit/resilient_recorder.py` L180-L186

```python
self._circuit_breaker = self._cb_registry.get_or_create(
    "audit_primary",
    AuditCircuitBreakerConfig(
        failure_threshold=self._resilient_config.circuit_failure_threshold,
        success_threshold=self._resilient_config.circuit_success_threshold,
        timeout_seconds=self._resilient_config.circuit_timeout_seconds,
        # call_timeout_seconds 전달 안 됨 → 기본값 5.0 사용되지만 어디에서도 안 읽힘
    ),
)
```

### 1.4 `ResilientRecorderSettings`에도 해당 필드 없음

**파일**: `settings/resilient_recorder.py` L93-L109

```python
# Circuit Breaker Settings
circuit_failure_threshold: int = Field(default=3, ...)
circuit_success_threshold: int = Field(default=2, ...)
circuit_timeout_seconds: float = Field(default=30.0, ...)
# call_timeout 관련 설정 없음
```

---

## 2. 문제 시나리오

### 2.1 Timeout 없는 Primary Store 호출

**파일**: `audit/resilient_recorder.py` L365-L382

```python
def _write_with_fallback(self, entry_dict: dict[str, Any]) -> bool:
    start_time = time.time()

    # 1. Primary Store (with Circuit Breaker)
    if self._circuit_breaker.can_execute():
        try:
            self._write_to_primary(entry_dict)    # ← timeout 보호 없음
            self._circuit_breaker.record_success()
            ...
        except Exception as e:
            self._circuit_breaker.record_failure()
```

**파일**: `audit/resilient_recorder.py` L434-L437

```python
def _write_to_primary(self, entry_dict: dict[str, Any]) -> None:
    """Primary Store에 기록."""
    entry = AuditEntry.from_dict(entry_dict)
    self.audit_adapter.log(entry)   # ← 외부 서비스 호출, 무한 대기 가능
```

### 2.2 호출 체인

```
_flush_loop()  [Background daemon thread]
  → _flush_batch()
    → _write_with_fallback()
      → _write_to_primary()
        → audit_adapter.log(entry)  ← CloudWatch/S3/Remote 서버 호출
```

이 코드는 **Background daemon thread**(L311-L324)에서 실행됨:

```python
def _flush_loop(self) -> None:
    while not self._stop_event.is_set():
        try:
            self._flush_batch()
        except Exception as e:
            ...
        self._stop_event.wait(timeout=self._resilient_config.flush_interval_seconds)
```

### 2.3 외부 백엔드 Hang 시 장애 전파 경로

```
외부 백엔드 hang (응답 없이 TCP 연결 유지)
  → _write_to_primary() 무한 대기 (또는 TCP 기본 timeout까지 수분 대기)
    → Background flush thread block
      → RingBuffer에 엔트리 계속 적재
        → 버퍼 오버플로우 → 감사 로그 유실
          → record_failure() 호출 불가 → CB가 OPEN 전환 못함
            → Fallback 경로 진입 불가
```

핵심: 예외가 발생하기 **전에** 스레드가 stuck 되므로 Circuit Breaker가 **작동하지 않는다.**

### 2.4 영향받는 백엔드

| 백엔드 | 파일 | 네트워크 호출 | Hang 가능성 |
|---|---|---|---|
| CloudWatchBackend | `audit/backends/cloudwatch.py` | AWS API (boto3) | **있음** — API 지연 |
| S3WORMBackend | `audit/backends/s3_worm.py` | S3 API (boto3) | **있음** — 업로드 지연 |
| RemoteAuditBackend | `audit/backends/remote.py` | HTTP (httpx) | **있음** — 서버 hang |
| LocalFileBackend | `audit/backends/local.py` | 로컬 디스크 | **낮음** — NFS 마운트 시 가능 |

---

## 3. 플랫폼 고려사항

### 3.1 `signal.SIGALRM` 사용 불가한 이유

1. **Background daemon thread에서 실행**: Python의 signal handler는 메인 스레드에서만 동작
2. **Windows 미지원**: `signal.SIGALRM` 자체가 없음

코드베이스의 기존 플랫폼 분기 패턴:

**파일**: `audit/resilience/syslog_fallback.py` L55-L67

```python
if sys.platform == "win32":
    self._syslog_available = False
    logger.debug("[SyslogFallback] Windows detected, using stderr fallback")
else:
    import syslog
    syslog.openlog(
        ident="selfhealing-audit",
        logoption=syslog.LOG_PID | syslog.LOG_CONS,
        facility=syslog.LOG_AUTH,
    )
```

### 3.2 결론: 플랫폼 무관하게 thread 기반 timeout 필요

Background thread 내에서 안전하게 동작하며 Windows/Linux 모두 호환되는 방식이 필요하다.

---

## 4. 기존 코드베이스의 선례 패턴

### 4.1 `ThreadPoolBulkhead.execute()` — 동일 패턴 이미 존재

**파일**: `resilience/bulkhead/threadpool.py` L211-L218

```python
future = self.submit(fn, *args, **kwargs)
try:
    return future.result(timeout=timeout)
except TimeoutError:
    future.cancel()
    raise BulkheadTimeoutError(
        bulkhead_name=self._name,
        timeout=timeout,
    )
```

`concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=)` 패턴:
- Background thread 내에서 안전
- Windows/Linux 모두 동작
- 코드베이스에서 이미 검증된 패턴

---

## 5. 변경 범위

### 5.1 수정 대상 파일 목록

| 파일 | 변경 내용 |
|---|---|
| `settings/resilient_recorder.py` | `circuit_call_timeout_seconds` 필드 추가 |
| `audit/resilient_recorder.py` | `ResilientRecorderConfig`에 `circuit_call_timeout_seconds` 추가 |
| `audit/resilient_recorder.py` | `_write_to_primary()`를 timeout 보호로 감싸는 메서드 추가 |
| `audit/resilient_recorder.py` | `AuditCircuitBreakerConfig` 생성 시 `call_timeout_seconds` 전달 |
| `audit/resilience/circuit_breaker.py` | `get_stats()`에 `call_timeout_seconds` 추가 |

### 5.2 변경하지 않는 것

| 항목 | 이유 |
|---|---|
| `AuditCircuitBreakerConfig` 클래스 자체 | 이미 `call_timeout_seconds` 필드가 올바르게 정의되어 있음 |
| `CircuitBreaker` 클래스 로직 | CB의 상태 전환 로직에는 문제 없음. timeout 강제는 호출부 책임 |
| `RetryHandler` | Celery 태스크 기반 설계이므로 `time_limit`/`soft_time_limit`이 커버 |

---

## 6. 구현 설계

### 6.1 `settings/resilient_recorder.py` 변경

```python
# Circuit Breaker Settings 섹션에 추가
circuit_call_timeout_seconds: float = Field(
    default=5.0,
    ge=0.5,
    le=60.0,
    description="개별 호출 타임아웃 (초). 외부 백엔드 hang 방지.",
)
```

환경변수: `SELFHEALING_RESILIENT_RECORDER_CIRCUIT_CALL_TIMEOUT_SECONDS=5.0`

### 6.2 `audit/resilient_recorder.py` 변경

#### ResilientRecorderConfig에 필드 추가

```python
@dataclass
class ResilientRecorderConfig:
    # ... 기존 필드 ...
    circuit_call_timeout_seconds: float = 5.0  # 개별 호출 timeout
```

#### CB 생성 시 `call_timeout_seconds` 전달

```python
self._circuit_breaker = self._cb_registry.get_or_create(
    "audit_primary",
    AuditCircuitBreakerConfig(
        failure_threshold=self._resilient_config.circuit_failure_threshold,
        success_threshold=self._resilient_config.circuit_success_threshold,
        timeout_seconds=self._resilient_config.circuit_timeout_seconds,
        call_timeout_seconds=self._resilient_config.circuit_call_timeout_seconds,  # 추가
    ),
)
```

#### `_write_to_primary`를 timeout 보호로 감싸기

```python
def _write_to_primary_with_timeout(
    self, entry_dict: dict[str, Any], timeout: float,
) -> None:
    """Primary Store에 timeout 제한으로 기록."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit_write") as executor:
        future = executor.submit(self._write_to_primary, entry_dict)
        try:
            future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            raise TimeoutError(
                f"Primary store write timed out after {timeout}s"
            )
```

#### `_write_with_fallback` 호출부 변경

```python
if self._circuit_breaker.can_execute():
    try:
        self._write_to_primary_with_timeout(
            entry_dict,
            timeout=self._circuit_breaker.config.call_timeout_seconds,
        )
        self._circuit_breaker.record_success()
        ...
```

### 6.3 `audit/resilience/circuit_breaker.py` 변경

#### `get_stats()` 에 `call_timeout_seconds` 포함

```python
"config": {
    "failure_threshold": self.config.failure_threshold,
    "success_threshold": self.config.success_threshold,
    "timeout_seconds": self.config.timeout_seconds,
    "call_timeout_seconds": self.config.call_timeout_seconds,  # 추가
},
```

---

## 7. ThreadPoolExecutor 풀 관리 고려사항

### 7.1 매 호출마다 `with ThreadPoolExecutor()` 생성 vs 인스턴스 공유

**선택: 매 호출마다 생성 (`with` 문)**

이유:
- `_flush_batch()`는 1초(`flush_interval_seconds`)마다 최대 100건(`flush_batch_size`) 처리
- 엔트리당 ThreadPoolExecutor 생성 오버헤드 ≈ ~0.1ms (Python 3.12 기준)
- 100건 × 0.1ms = 10ms — 1초 주기 대비 무시할 수 있는 수준
- 인스턴스 공유 시 shutdown/lifecycle 관리 복잡도가 불필요하게 증가
- `ThreadPoolBulkhead`와 달리 이 사용처는 단발성 보호 목적

### 7.2 Timeout 후 thread 누수 방지

`future.cancel()`은 이미 실행 중인 thread를 강제 종료하지 않음. 그러나:
- `with ThreadPoolExecutor()` 블록이 끝나면 `shutdown(wait=True)` 호출됨
- 이는 의도적 설계 — timeout 후 원래 thread가 종료되기를 잠시 대기
- 실제 hang 시에는 daemon thread이므로 프로세스 종료 시 정리됨

---

## 8. 테스트 계획

### 8.1 단위 테스트

```python
class TestCallTimeoutWiring:
    """call_timeout_seconds 연결 테스트."""

    def test_timeout_triggers_on_slow_primary(self):
        """느린 Primary Store가 call_timeout_seconds 내에 중단된다."""

    def test_timeout_triggers_circuit_failure(self):
        """timeout 시 record_failure()가 호출되어 CB 카운트가 증가한다."""

    def test_cb_opens_after_timeout_threshold(self):
        """연속 timeout으로 failure_threshold 도달 시 CB가 OPEN된다."""

    def test_fallback_chain_after_timeout_cb_open(self):
        """CB OPEN 후 Fallback → Syslog → stderr 체인이 작동한다."""

    def test_get_stats_includes_call_timeout(self):
        """get_stats() 결과에 call_timeout_seconds가 포함된다."""

    def test_settings_env_override(self):
        """환경변수로 circuit_call_timeout_seconds를 오버라이드할 수 있다."""
```

### 8.2 기존 테스트 영향

| 테스트 파일 | 영향 |
|---|---|
| `tests/audit/test_resilience.py` | `get_stats()` 반환값 검증 시 `call_timeout_seconds` 키 추가 필요 |
| `tests/unit/storage/test_resilient_recorder.py` | `_write_with_fallback` 호출 시그니처 변경 없음 (내부 구현만 변경) |

---

## 9. 설정 기본값 근거

| 설정 | 값 | 근거 |
|---|---|---|
| `call_timeout_seconds` | `5.0` | 기존 `AuditCircuitBreakerConfig` 기본값 유지. AWS API 평균 응답 ~200ms, S3 PUT ~500ms 기준으로 5초는 충분한 마진 |
| `flush_interval_seconds` | `1.0` | 기존값. 1초 주기 대비 5초 timeout이면 최악의 경우 5배 지연이지만, CB가 3회 실패 후 OPEN되므로 최대 15초 후 fallback 진입 |

---

## 10. 롤백 계획

- `circuit_call_timeout_seconds`를 원래 기본값(5.0)보다 크게 설정하면 사실상 비활성화 효과 (예: `60.0`)
- 환경변수 `SELFHEALING_RESILIENT_RECORDER_CIRCUIT_CALL_TIMEOUT_SECONDS=60` 으로 즉시 완화 가능
- 코드 롤백 없이 설정만으로 제어 가능
