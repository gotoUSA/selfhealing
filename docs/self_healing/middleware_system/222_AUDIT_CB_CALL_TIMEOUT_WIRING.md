# 222. Audit Circuit Breaker `call_timeout_seconds` Dead Code 연결

> **상태**: ✅ 구현 완료 (2026-02-12)
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
| `audit/resilient_recorder.py` | `__init__`에 인스턴스 레벨 `_write_executor` 생성, `stop()`에서 `shutdown(wait=False)` |
| `audit/resilient_recorder.py` | `AuditCircuitBreakerConfig` 생성 시 `call_timeout_seconds` 전달 |
| `audit/resilient_recorder.py` | `get_health_status()`에 좀비 스레드 모니터링 항목 추가 |
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

#### 인스턴스 레벨 `_write_executor` 초기화 (`__init__`에 추가)

```python
# __init__에서 (신규 구성요소 섹션)
from concurrent.futures import ThreadPoolExecutor

self._write_executor: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="audit_write",
)
```

#### `stop()` 메서드에 executor 정리 추가

> **순서 중요**: `_flush_remaining()` **이후에** `shutdown(wait=False)`를 호출해야 한다.
> `_flush_remaining()`은 내부적으로 `_write_with_fallback()` → `_write_to_primary_with_timeout()`을
> 호출하므로, executor가 살아있어야 남은 버퍼를 drain할 수 있다.
> CB가 OPEN 상태라면 `can_execute()` → `False`이므로 primary를 skip하고
> fallback 경로로 drain되어 executor hang과 무관하게 종료된다.
>
> 근거: `audit/resilient_recorder.py` L249-L265 — `stop()` 내부에서
> `self._stop_event.set()` → `self._flush_thread.join()` → **`self._flush_remaining()`** → `self._started = False`

```python
def stop(self, timeout: float = 5.0) -> None:
    """Background flush worker 중지."""
    if not self._started:
        return

    self._stop_event.set()

    if self._flush_thread:
        self._flush_thread.join(timeout=timeout)

    # Drain remaining buffer (executor 필요)
    self._flush_remaining()

    # executor 정리: _flush_remaining() 완료 후에 호출
    # wait=False — hang된 좀비 스레드가 있어도 애플리케이션 종료를 막지 않음
    self._write_executor.shutdown(wait=False)

    self._started = False
    self_audit().log(SelfAuditEvent.SHUTDOWN, "Background flush worker stopped")
    logger.info("[ResilientRecorder] Background flush worker stopped")
```

#### `_write_to_primary`를 timeout 보호로 감싸기

> **⚠ 주의**: `with ThreadPoolExecutor()` 컨텍스트 매니저를 사용하지 않는다.
> `with` 블록 탈출 시 내부적으로 `shutdown(wait=True)`가 호출되어,
> hang된 worker thread가 종료될 때까지 Background flush thread도 무한 대기하게 되므로
> 이 구현이 해결하려는 바로 그 문제(flush thread 교착)가 재현된다.
>
> 근거: Python `concurrent.futures` 소스 — `ThreadPoolExecutor.__exit__` → `self.shutdown(wait=True)`

```python
def _write_to_primary_with_timeout(
    self, entry_dict: dict[str, Any], timeout: float,
) -> None:
    """Primary Store에 timeout 제한으로 기록."""
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    future = self._write_executor.submit(self._write_to_primary, entry_dict)
    try:
        future.result(timeout=timeout)
    except FuturesTimeoutError:
        # future.cancel()의 이중 역할 (영속적 executor 환경):
        #
        # 1. 이미 실행 중인 작업: 무효 (Python thread는 강제 종료 불가)
        # 2. 큐에서 대기 중인 작업: 큐에서 제거하여 뒤늦은 실행 방지
        #    → 이 엔트리는 fallback으로 이미 기록되므로,
        #      cancel() 없이 워커가 풀렸을 때 뒤늦게 실행되면
        #      primary + fallback 모두에 중복 기록되는 위험이 있음
        #
        # 선례: resilience/bulkhead/threadpool.py L211-L218 동일 패턴
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

**선택: 인스턴스 레벨 단일 executor 공유 (`__init__`에서 1회 생성)**

이유:
- `_flush_batch()`는 **엔트리별 루프**로 `_write_with_fallback`를 호출함 (L340)

  ```python
  # audit/resilient_recorder.py L326-L348
  def _flush_batch(self) -> int:
      batch = self._buffer.get_batch(self._resilient_config.flush_batch_size)  # 기본 100
      if not batch:
          return 0
      processed = 0
      for entry_dict in batch:
          success = self._write_with_fallback(entry_dict)  # ← 엔트리별 호출
          if success:
              processed += 1
      return processed
  ```

- `flush_interval_seconds=1.0`, `flush_batch_size=100` 기본값 기준 — **초당 최대 100건** 처리
- 매 건마다 ThreadPoolExecutor 생성 시: OS thread 생성/파괴 100회/초, 컨텍스트 스위칭 × 100
- 인스턴스 레벨 단일 executor(`max_workers=1`) 사용 시: 동일 thread 재사용, OS 오버헤드 제거
- hang 발생 시에도 **좀비 스레드가 최대 1개로 제한됨** (7.3절 참조)
- `stop()` 시점에 `shutdown(wait=False)` 호출로 lifecycle 명확

기존 코드베이스 선례 — `ThreadPoolBulkhead`도 인스턴스 레벨 executor 사용:

```python
# resilience/bulkhead/threadpool.py L233-L240
def shutdown(self, wait: bool = True) -> None:
    self._executor.shutdown(wait=wait)
```

### 7.2 `with ThreadPoolExecutor()` 컨텍스트 매니저 사용 금지 근거

Python의 `ThreadPoolExecutor.__exit__`는 내부적으로 `shutdown(wait=True)`를 호출한다.
이는 실행 중인 worker thread가 **완전히 종료될 때까지** 호출부 thread를 무한 대기시킨다.

**Deadlock 시나리오:**

```
_flush_loop()  [Background daemon thread, L241: daemon=True]
  → _write_to_primary_with_timeout()
    → with ThreadPoolExecutor() as executor:      # ← 컨텍스트 매니저 진입
      → future.result(timeout=5.0)                # ← 5초 후 FuturesTimeoutError 발생 (OK)
      → future.cancel()                            # ← 실행 중인 thread에는 무효 (OK)
    → with 블록 탈출: __exit__ → shutdown(wait=True)
      → hang된 worker thread 종료 대기 → **무한 대기**
        → Background flush thread block
          → 문서 2.3의 장애 전파 경로가 그대로 재현됨
```

**해결책:** `with` 문 사용 금지 → 수동 `shutdown(wait=False)` 관리

```python
# ✘ 금지 — Deadlock 위험
with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(self._write_to_primary, entry_dict)
    future.result(timeout=timeout)  # timeout 후에도 with 탈출 시 무한 대기

# ✔ 안전 — 인스턴스 레벨 executor + stop()에서 shutdown(wait=False)
future = self._write_executor.submit(self._write_to_primary, entry_dict)
future.result(timeout=timeout)  # timeout 후 즉시 반환, flush thread 계속 진행
```

### 7.3 좀비 스레드(Zombie Thread) 누적 위험 및 대비책

`future.cancel()`은 CPython에서 이미 실행 중인 callable을 중단하지 못한다.
`shutdown(wait=False)` 사용 시 hang된 worker thread는 프로세스 메모리에 계속 잔류한다.

**인스턴스 레벨 단일 executor(`max_workers=1`) 사용 시 좀비 제한 메커니즘:**

```
1회차 timeout 발생:
  → worker thread #1이 엔트리 A 실행 시작 → hang (좀비)
  → future.cancel() → A는 이미 실행 중이므로 무효
  → record_failure() 호출 (failure_count = 1)

2회차 submit:
  → max_workers=1이므로 worker thread #1(A에 묶임)이 정리될 때까지 대기 큐에 적재
  → future.result(timeout=5.0) → 워커가 A에 묶여 B는 시작조차 못함 → timeout
  → future.cancel() → B는 큐에서 대기 중이므로 큐에서 제거됨 (뒤늦은 실행 및 중복 기록 방지)
  → record_failure() (failure_count = 2)

3회차 submit:
  → 동일 패턴
  → record_failure() (failure_count = 3 = failure_threshold)
  → CB OPEN → can_execute() → False
  → 이후 Primary 호출 완전 skip
```

결과: **좀비 스레드 최대 1개**로 제한.
CB가 `timeout_seconds=30.0`초 후 HALF_OPEN → 1건 재시도 시에도 동일 executor의 동일 thread가 재사용될 수 있으므로 추가 좀비 발생 없음.

#### 7.3.1 예상 동작: 워커 스레드 점유에 의한 후속 작업 큐잉 (Worker Starvation)

`max_workers=1`인 영속적 executor에서 선행 작업이 hang되면 후속 작업에 특수한 동작이 발생한다.
이는 **의도된 Fail Fast 동작**이며 정상이다.

**동작 원리:**

| 단계 | flush loop (Background daemon thread) | worker thread |
|---|---|---|
| 1 | 엔트리 A `submit()` | A 실행 시작 → hang |
| 2 | `future.result(timeout=5.0)` → 5초 후 `TimeoutError` | A 계속 hang |
| 3 | `future.cancel()` → A는 이미 실행 중이므로 무효 | A 계속 hang |
| 4 | `record_failure()` (failure_count=1) | A 계속 hang |
| 5 | 엔트리 B `submit()` → worker 큐에 적재 (즉시 반환) | A 계속 hang |
| 6 | `future.result(timeout=5.0)` → 워커가 A에 묶여 B **시작조차 못함** → 5초 후 `TimeoutError` | A 계속 hang |
| 7 | `future.cancel()` → B는 **큐에서 대기 중**이므로 **큐에서 제거됨** | A 계속 hang |
| 8 | `record_failure()` (failure_count=2) | A 계속 hang |

**핵심:**
- 작업이 "실행되다가 hang"된 것(A)뿐 아니라, "선행 작업 때문에 실행 기회를 얻지 못한 것"(B)도
  `TimeoutError`로 처리되어 CB 실패 카운트에 **올바르게 집계**됨
- `future.cancel()`은 큐에서 대기 중인 B를 제거하여, 나중에 워커가 풀렸을 때
  이미 fallback으로 기록된 B를 primary에 중복 기록하는 것을 방지함
- 이것은 시스템이 의도한 Fail Fast 동작이므로 정상임

> 근거:
> - `audit/resilient_recorder.py` L326-L348 — `_flush_batch()`가 엔트리별 루프로 `_write_with_fallback` 호출
> - `audit/resilience/circuit_breaker.py` L105-L108 — CB OPEN 시 `can_execute()` → `False`
> - `resilience/bulkhead/threadpool.py` L211-L218 — 동일한 `future.cancel()` 패턴 선례

**좀비 vs 매 호출 생성 비교:**

| 방식 | 좀비 스레드 상한 | 1시간 장애 시 |
|---|---|---|
| 매 호출 `with` 생성 (**기존 문서**) | Deadlock으로 실용 불가 | flush thread 교착 |
| 매 호출 수동 생성 + `shutdown(wait=False)` | 초기 3개 + 30초마다 1개 ≈ **123개/시간** | 메모리 누수 위험 |
| **인스턴스 레벨 단일 executor** (채택) | **최대 1개** | 안전 |

**모니터링:** `get_health_status()`에 활성 thread 수 추가

```python
# get_health_status() 에 항목 추가
"write_executor": {
    "active_threads": len(self._write_executor._threads),
    "pending_tasks": self._write_executor._work_queue.qsize(),
},
```

### 7.4 백엔드 라이브러리 자체 Timeout — 다층 방어 권장사항

ThreadPoolExecutor로 감싸는 방식은 **최후의 안전망**이다.
boto3, httpx 등 클라이언트 라이브러리는 자체적으로 `connect_timeout`/`read_timeout`을 지원하며,
이를 우선 적용하면 좀비 스레드 자체가 발생하지 않는다.

**현재 코드 상태:** 모든 백엔드가 stub(Interface Only)이므로 라이브러리 timeout 주입이 불가능

| 백엔드 | 파일 | 자체 timeout 상태 | 코드 근거 |
|---|---|---|---|
| CloudWatchBackend | `audit/backends/cloudwatch.py` | **stub** — boto3 client 생성 코드 주석 처리 | L61: `self._client = None  # boto3 client placeholder` |
| S3WORMBackend | `audit/backends/s3_worm.py` | **stub** — 동일 | L88-L89: `# import boto3`, `# self._client = boto3.client(...)` |
| RemoteAuditBackend | `audit/backends/remote.py` | timeout 파라미터 존재하나 **사용처 주석 처리** | L70: `self._timeout = timeout` → L94: `#     timeout=self._timeout,` |
| LocalFileBackend | `audit/backends/local.py` | timeout 개념 없음 (로컬 디스크) | timeout 관련 코드 0건 |

**권장 다층 방어 구조:**

```
1차 방어 (우선): 백엔드 라이브러리 자체 timeout
  boto3: Config(connect_timeout=2, read_timeout=5)
  httpx:  timeout=httpx.Timeout(connect=2.0, read=5.0)
  → 정상적으로 예외 발생, CB record_failure() 즉시 호출, 좀비 스레드 없음

2차 방어 (안전망): ThreadPoolExecutor + future.result(timeout=)
  → 라이브러리 timeout이 동작하지 않는 엣지 케이스 대비
  → NFS hang, 예상치 못한 OS-level blocking 등
```

**후속 작업:** 백엔드 stub이 실체화될 때 `call_timeout_seconds` 값을
각 백엔드 `__init__`에 주입하여 라이브러리 자체 timeout을 1차 방어로 활성화해야 한다.
이 작업은 별도 문서로 추적한다.

---

## 8. 테스트 계획

> **상태**: ✅ 단위 테스트 33개 구현 완료 (2026-02-12)
> **파일**: `packages/selfhealing-python/tests/unit/audit/call_timeout_wiring/test_call_timeout_wiring.py`

### 8.1 단위 테스트 (33개)

#### 계약 검증 (Contract) — 8개

| 클래스 | 테스트 | 검증 내용 |
|---|---|---|
| `TestCallTimeoutSettingsContract` | `test_default_value_is_5_seconds` | `ResilientRecorderSettings.circuit_call_timeout_seconds` 기본값 5.0 |
| | `test_minimum_bound_is_0_5_seconds` | 최솟값 제약 0.5 |
| | `test_maximum_bound_is_60_seconds` | 최댓값 제약 60.0 |
| `TestCallTimeoutDataclassContract` | `test_default_value_is_5_seconds` | `AuditCircuitBreakerConfig.call_timeout_seconds` 기본값 5.0 |
| | `test_resilient_recorder_config_default_is_5_seconds` | `ResilientRecorderConfig.circuit_call_timeout_seconds` 기본값 5.0 |
| `TestGetStatsIncludesCallTimeoutContract` | `test_config_section_has_call_timeout_seconds_key` | `get_stats()['config']`에 키 존재 |
| | `test_config_call_timeout_seconds_matches_config_value` | 커스텀 설정값과 일치 |
| `TestGetStatsConfigKeysContract` | `test_config_has_exactly_four_keys` | config 섹션 키 4개 |

#### 동작 검증 (Behavior) — 25개

| 클래스 | 테스트 | 검증 내용 |
|---|---|---|
| `TestSettingsEnvOverrideBehavior` | `test_env_override_applies_custom_value` | 환경변수 오버라이드 적용 |
| `TestCallTimeoutConfigPropagationBehavior` | `test_from_settings_propagates_call_timeout` | Settings → Config 전파 |
| | `test_recorder_passes_call_timeout_to_circuit_breaker` | Config → CB config 전파 |
| `TestWriteToPrimaryWithTimeoutBehavior` | `test_slow_primary_raises_timeout_error` | 느린 Primary → TimeoutError |
| | `test_fast_primary_completes_without_error` | 빠른 Primary → 정상 완료 |
| | `test_timeout_calls_future_cancel` | timeout 시 future.cancel() 호출 |
| `TestTimeoutTriggersCircuitFailureBehavior` | `test_timeout_increments_failure_count` | timeout → failure_count 증가 |
| | `test_consecutive_timeouts_open_circuit` | 연속 timeout → CB OPEN |
| `TestFallbackAfterTimeoutCbOpenBehavior` | `test_cb_open_skips_primary_and_uses_stderr` | CB OPEN → Primary skip |
| | `test_cb_open_then_fallback_file_used` | CB OPEN → Fallback 파일 사용 |
| `TestExecutorShutdownOnStopBehavior` | `test_stop_calls_executor_shutdown_wait_false` | stop() → shutdown(wait=False) |
| | `test_stop_shuts_down_executor_after_flush_remaining` | flush_remaining → shutdown 순서 |
| `TestHealthStatusWriteExecutorBehavior` | `test_health_status_has_write_executor_section` | write_executor 섹션 존재 |
| | `test_write_executor_has_active_threads_key` | active_threads 키 존재 |
| | `test_write_executor_has_pending_tasks_key` | pending_tasks 키 존재 |
| | `test_initial_active_threads_is_zero` | 초기 active_threads == 0 |
| | `test_initial_pending_tasks_is_zero` | 초기 pending_tasks == 0 |
| `TestWriteExecutorInstanceLevelBehavior` | `test_executor_max_workers_is_one` | max_workers == 1 |
| | `test_executor_thread_name_prefix` | thread_name_prefix == "audit_write" |
| | `test_zombie_thread_limited_to_one` | hang 시 좀비 스레드 ≤ 1개 |
| `TestNoDeadlockWithoutContextManagerBehavior` | `test_flush_thread_not_blocked_after_timeout` | timeout 후 flush thread 즉시 반환 |
| `TestSettingsValidationBehavior` | `test_rejects_below_minimum` | 0.5 미만 → ValidationError |
| | `test_rejects_above_maximum` | 60.0 초과 → ValidationError |
| | `test_accepts_minimum_boundary` | 0.5 경계값 허용 |
| | `test_accepts_maximum_boundary` | 60.0 경계값 허용 |

### 8.2 기존 테스트 영향

| 테스트 파일 | 영향 |
|---|---|
| `tests/audit/test_resilience.py` | `get_stats()` 반환값 검증 시 `call_timeout_seconds` 키 추가 필요 |
| `tests/unit/storage/test_resilient_recorder.py` | `_write_with_fallback` 호출 시그니처 변경 없음 (내부 구현만 변경). `stop()` 호출 시 executor 정리 검증 추가 필요 |

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
