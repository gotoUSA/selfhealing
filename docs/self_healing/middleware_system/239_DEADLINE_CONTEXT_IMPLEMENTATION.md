# 239. Deadline Context — gRPC Deadline Propagation 패턴 구현

| 항목 | 내용 |
|------|------|
| **문서번호** | 239 |
| **작성일** | 2026-02-18 |
| **상태** | 설계 완료 |
| **선행 문서** | 236_REQUEST_PRIORITY_ADMISSION_CONTROL.md |
| **후속 문서** | 240, 241, 242, 243 |

---

## 1. 배경 및 문제 정의

### 1.1 현재 상태

현재 시스템에 **deadline 인식이 완전히 부재**합니다.

**AdmissionControlMiddleware** (`api/django/admission_control.py` L120-163)는 요청을 허용/거부만 판단합니다:

```python
def _process_request(self, request):
    path = request.path
    client_ip = self._get_client_ip(request)
    user_id = self._get_user_id(request)
    tier_result = self._registry.resolve_tier_with_fallback(...)
    # ... tier 분류 → TrafficGate 판정 → 허용/거부
```

상위 서비스의 남은 시간 정보를 **전혀 활용하지 않습니다**.

### 1.2 문제 시나리오

MSA 호출 체인 A → B → C 에서:
1. A가 3초 타임아웃으로 B를 호출
2. B가 2.5초 소요 후 C를 호출
3. C는 남은 시간이 0.5초인데 예상 처리시간이 2초
4. C가 2초 동안 작업 후 응답 → A는 이미 타임아웃으로 응답 폐기
5. **C의 2초 작업은 완전히 무의미** → CPU, DB, 네트워크 자원 낭비

### 1.3 기존 코드의 관련 패턴

hedging executor가 이미 `remaining_timeout` 패턴을 사용하고 있어 코드베이스에 해당 설계 패턴이 익숙합니다:

```python
# core/hedging/async_executor.py L333
remaining_timeout = self._config.timeout - (time.perf_counter() - start_time)
while pending and remaining_timeout > 0:
    done, pending = await asyncio.wait(
        pending, timeout=remaining_timeout, ...
    )
    remaining_timeout = self._config.timeout - (time.perf_counter() - start_time)
```

---

## 2. 설계

### 2.1 Deadline Context 모듈

**위치**: `packages/selfhealing-python/src/selfhealing/scaling/deadline_context.py`

`scaling` 패키지에 배치하는 이유: TrafficGate, RateController와 동일 레벨의 트래픽 제어 모듈이며, AdmissionControlMiddleware가 이미 `scaling.traffic_gate`를 참조합니다.

### 2.2 핵심 구성요소

```
┌─────────────────────────────────────────────────┐
│              DeadlineContext                      │
│                                                   │
│  ContextVar[float | None] _request_deadline       │
│  ├── set_deadline(remaining_ms)                   │
│  ├── get_remaining_ms() → float | None            │
│  ├── is_expired() → bool                          │
│  └── should_fast_fail(estimated_ms) → bool        │
│                                                   │
│  parse_deadline_header(header_value) → float | None│
│  DeadlineContextManager                           │
└─────────────────────────────────────────────────┘
```

### 2.3 ContextVar 기반 전파

기존 `SelfHealingHttpClient`의 `_is_chaos_request` ContextVar 패턴을 그대로 따릅니다:

```python
# services/http_client.py L25 — 기존 패턴
_is_chaos_request: ContextVar[bool] = ContextVar("is_chaos_request", default=False)
```

동일하게:
```python
# scaling/deadline_context.py — 신규
_request_deadline: ContextVar[float | None] = ContextVar("request_deadline", default=None)
```

### 2.4 HTTP 헤더 규약

```
X-Deadline-Remaining: 2500ms
```

- 단위: 밀리초 (ms 접미사 포함/미포함 허용)
- 값: 양의 정수 또는 부동소수점
- 부재 시: deadline 미적용 (기존 동작 유지)

### 2.5 Network Latency Buffer (수신 측 보정)

상위 서비스가 "남은 시간 2000ms"를 헤더에 담아 보냈을 때, 수신 시점에는 이미 네트워크 전송 시간이 경과했습니다. 이를 보정하기 위해 **수신 측에서 고정 Buffer를 차감**합니다.

```python
# scaling/deadline_context.py

# 네트워크 레이턴시 보정 버퍼 (환경변수: SELFHEALING_DEADLINE_NETWORK_BUFFER_MS)
DEFAULT_NETWORK_LATENCY_BUFFER_MS: float = 50.0


def set_deadline(remaining_ms: float) -> None:
    """
    현재 컨텍스트에 deadline 설정.
    네트워크 레이턴시 Buffer를 차감하여 보수적으로 계산합니다.
    """
    adjusted = remaining_ms - DEFAULT_NETWORK_LATENCY_BUFFER_MS

    if adjusted <= 0:
        # 도착 시점에 이미 만료 — 네트워크 혼잡 의심
        logger.warning(
            "[DeadlineContext] Deadline exhausted on arrival: "
            "remaining=%.0fms, buffer=%.0fms — possible network congestion",
            remaining_ms,
            DEFAULT_NETWORK_LATENCY_BUFFER_MS,
        )
        adjusted = 0

    deadline = time.monotonic() + (adjusted / 1000.0)
    _request_deadline.set(deadline)
```

**Buffer 값 50ms 산정 근거**:

| 구간 | 예상 레이턴시 |
|------|---------------|
| 같은 AZ 내 Pod 간 | 1~5ms |
| Cross-AZ (같은 Region) | 10~30ms |
| Nginx → Gunicorn 로컬 | ~1ms (`nginx.conf` upstream keepalive 16) |
| **안전 마진 (2× Cross-AZ)** | **50ms** |

Nginx의 `proxy_connect_timeout 3s` (`nginx.conf` L82)와 Gunicorn gthread 워커 간 통신(`docker-compose.yml` L36)을 고려하면, 내부 서비스 간 50ms는 충분히 보수적입니다.

**Congestion 모니터링**: `adjusted <= 0` 상황은 단순 만료가 아니라 **네트워크/큐 대기 시간이 비정상적으로 긴 상태(Congestion)**를 의미합니다. 이를 `selfhealing_deadline_exhausted_on_arrival_total` Counter로 추적하여 네트워크 인프라 팀과의 소통 근거로 활용합니다 (6.2절 참조).

### 2.6 Remaining Duration 방식 선택 근거

절대 시간(Absolute Timestamp) 대신 **Remaining Duration(남은 시간)** 방식을 선택합니다.

**이유**: 시스템이 내부적으로 `time.monotonic()`을 사용하며, 이는 각 노드의 로컬 단조 시계로 노드 간 동기화가 불가능합니다. 기존 hedging executor도 동일한 패턴입니다:

```python
# core/hedging/async_executor.py L337
remaining_timeout = self._config.timeout - (time.perf_counter() - start_time)
```

| 비교 항목 | Remaining Duration | Absolute Timestamp |
|---|---|---|
| NTP 의존성 | 없음 | 필수 (±1~50ms drift) |
| K8s Pod 재시작 시 | 영향 없음 | drift 누적 위험 |
| gRPC 표준 | `grpc-timeout` 헤더와 동일 | 비표준 |
| 네트워크 전송 중 시간 경과 | 2.5절 Buffer로 보정 | NTP 정확도에 의존 |

Docker 컨테이너 환경(`docker-compose.yml`)에서 Pod 간 시계 동기화를 보장하기 어려우므로, 업계 표준(gRPC `grpc-timeout`)을 따릅니다.

### 2.7 실행 환경 및 ContextVar 안전성

#### WSGI + gthread 환경

현재 시스템은 **WSGI (Gunicorn gthread)** 기반입니다:

```bash
# docker-compose.yml L36
gunicorn myproject.wsgi:application --bind 0.0.0.0:8000 \
    --workers 4 --threads 4 --timeout 60 --worker-class gthread
```

`gthread` 워커 클래스에서 Python `ContextVar`는 스레드별로 독립된 컨텍스트를 유지하므로 요청 간 격리가 보장됩니다. `async_to_sync` 문제는 WSGI 전용 환경이므로 해당하지 않습니다.

#### Thread Pool에서의 ContextVar 전파

별도 스레드를 사용하는 로직(Bulkhead ThreadPool, Hedging Executor)이 존재하며, 이미 `contextvars.copy_context().run()` 패턴이 구현되어 있습니다:

```python
# resilience/bulkhead/threadpool.py L170
ctx = contextvars.copy_context()
def wrapped() -> T:
    return ctx.run(fn, *args, **kwargs)
return self._executor.submit(wrapped)

# core/hedging/executor.py L104
ctx = contextvars.copy_context()
def wrapper():
    return ctx.run(fn)
return executor.submit(wrapper)
```

`_request_deadline` ContextVar는 이 기존 인프라를 통해 **자동으로 워커 스레드에 전파**됩니다. 추가 작업이 필요하지 않습니다.

#### Celery Task와의 관계

HTTP Deadline을 Celery Task에 **전파하지 않습니다**. Celery Task는 독립 라이프사이클을 가집니다.

**이유**:
1. 기존 Celery 전파 시스템(`context/celery_propagation.py`)은 **CausationContext**(인과관계 추적)만 전파
2. Celery Task는 자체 `time_limit`/`soft_time_limit`을 보유 (예: `adapters/celery/tasks/postmortem.py` L36-37: `time_limit=120, soft_time_limit=110`)
3. Task 발행(publish)과 실행(execution) 사이 큐 대기 시간이 존재하여 HTTP 남은 시간이 무의미
4. HTTP 요청의 3초 deadline이 전파되면 대부분의 비동기 Task가 실패

---

## 3. 구현 상세

### 3.1 deadline_context.py

```python
"""
Deadline Context — gRPC Deadline Propagation 패턴.

상위 서비스의 deadline을 하위 서비스에 ContextVar + HTTP 헤더로 전파합니다.
남은 시간이 예상 처리시간 미만이면 즉시 거절(Fast-Fail)하여
무의미한 작업을 방지합니다.

패턴 참조: core/hedging/async_executor.py L333 (remaining_timeout 패턴)
헤더 전파 참조: services/http_client.py L25 (_is_chaos_request ContextVar)
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator

logger = logging.getLogger(__name__)

# HTTP 헤더 이름
DEADLINE_HEADER = "X-Deadline-Remaining"

# Django META 키 (HTTP_X_DEADLINE_REMAINING)
DEADLINE_META_KEY = "HTTP_X_DEADLINE_REMAINING"

# ContextVar: 요청 deadline (Unix timestamp, monotonic clock)
_request_deadline: ContextVar[float | None] = ContextVar(
    "request_deadline", default=None
)

# 최소 유효 시간 (ms) — 이보다 적으면 Fast-Fail
DEFAULT_MINIMUM_USEFUL_TIME_MS: float = 50.0

# 헤더 파싱용 정규식
_DEADLINE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:ms)?\s*$", re.IGNORECASE)


def parse_deadline_header(header_value: str) -> float | None:
    """
    X-Deadline-Remaining 헤더 값 파싱.

    Args:
        header_value: 헤더 값 (예: "2500ms", "2500", "1500.5ms")

    Returns:
        남은 시간(ms) 또는 파싱 실패 시 None
    """
    if not header_value:
        return None

    match = _DEADLINE_PATTERN.match(header_value)
    if match:
        return float(match.group(1))

    logger.debug(
        "[DeadlineContext] Failed to parse header: %s", header_value
    )
    return None


# 네트워크 레이턴시 보정 버퍼 (환경변수: SELFHEALING_DEADLINE_NETWORK_BUFFER_MS)
DEFAULT_NETWORK_LATENCY_BUFFER_MS: float = 50.0


def set_deadline(remaining_ms: float) -> None:
    """
    현재 컨텍스트에 deadline 설정.
    네트워크 레이턴시 Buffer를 차감하여 보수적으로 계산합니다.

    Args:
        remaining_ms: 남은 시간 (밀리초)
    """
    adjusted = remaining_ms - DEFAULT_NETWORK_LATENCY_BUFFER_MS

    if adjusted <= 0:
        logger.warning(
            "[DeadlineContext] Deadline exhausted on arrival: "
            "remaining=%.0fms, buffer=%.0fms — possible network congestion",
            remaining_ms,
            DEFAULT_NETWORK_LATENCY_BUFFER_MS,
        )
        adjusted = 0

    deadline = time.monotonic() + (adjusted / 1000.0)
    _request_deadline.set(deadline)


def get_remaining_ms() -> float | None:
    """
    현재 컨텍스트의 남은 시간 반환.

    Returns:
        남은 시간(ms) 또는 deadline 미설정 시 None
    """
    deadline = _request_deadline.get()
    if deadline is None:
        return None
    remaining = (deadline - time.monotonic()) * 1000.0
    return max(0.0, remaining)


def is_expired() -> bool:
    """
    deadline이 만료되었는지 확인.

    Returns:
        만료 시 True, deadline 미설정 시 False
    """
    remaining = get_remaining_ms()
    if remaining is None:
        return False
    return remaining <= 0.0


def should_fast_fail(
    estimated_processing_ms: float,
    minimum_useful_ms: float = DEFAULT_MINIMUM_USEFUL_TIME_MS,
) -> bool:
    """
    남은 시간이 예상 처리시간 미만이면 True (Fast-Fail 권장).

    Args:
        estimated_processing_ms: 예상 처리시간 (밀리초)
        minimum_useful_ms: 최소 유효 시간 (밀리초)

    Returns:
        True이면 Fast-Fail 권장
    """
    remaining = get_remaining_ms()
    if remaining is None:
        return False  # deadline 미설정 시 Fast-Fail 하지 않음

    if remaining < minimum_useful_ms:
        return True  # 최소 유효 시간 미만

    return remaining < estimated_processing_ms


def clear_deadline() -> None:
    """현재 컨텍스트의 deadline 제거."""
    _request_deadline.set(None)


def get_propagation_header_value() -> str | None:
    """
    하위 서비스로 전파할 헤더 값 생성.

    Returns:
        "1234ms" 형식 또는 deadline 미설정 시 None
    """
    remaining = get_remaining_ms()
    if remaining is None or remaining <= 0:
        return None
    return f"{remaining:.0f}ms"


@contextmanager
def deadline_scope(remaining_ms: float) -> Generator[None, None, None]:
    """
    deadline 범위 컨텍스트 매니저.

    Usage:
        with deadline_scope(3000):
            # 이 블록 내에서 deadline 활성화
            if should_fast_fail(estimated_ms=2000):
                raise TimeoutError("Fast-Fail")
            process_request()

    Args:
        remaining_ms: 남은 시간 (밀리초)
    """
    previous = _request_deadline.get()
    set_deadline(remaining_ms)
    try:
        yield
    finally:
        _request_deadline.set(previous)
```

### 3.2 AdmissionControlMiddleware 수정

**파일**: `api/django/admission_control.py`

현재 `_process_request()` 메서드에 deadline 체크 단계를 삽입합니다. 기존 파이프라인 순서(tier 분류 → TrafficGate) 앞에 위치합니다.

```python
def _process_request(self, request):
    """요청 분류 → Deadline 체크 → TrafficGate 판정 → 허용/거부."""

    # 0단계: Deadline Context 설정 (신규)
    from selfhealing.scaling.deadline_context import (
        DEADLINE_META_KEY,
        parse_deadline_header,
        set_deadline,
        should_fast_fail,
        DEFAULT_MINIMUM_USEFUL_TIME_MS,
    )

    deadline_header = request.META.get(DEADLINE_META_KEY)
    if deadline_header:
        remaining_ms = parse_deadline_header(deadline_header)
        if remaining_ms is not None:
            set_deadline(remaining_ms)
            # 최소 유효 시간 미만이면 즉시 거절
            if remaining_ms < DEFAULT_MINIMUM_USEFUL_TIME_MS:
                logger.info(
                    "[AdmissionControlMiddleware] Deadline Fast-Fail: "
                    "remaining=%.0fms < minimum=%.0fms, path=%s",
                    remaining_ms,
                    DEFAULT_MINIMUM_USEFUL_TIME_MS,
                    request.path,
                )
                return self._create_deadline_rejection_response(
                    request, remaining_ms
                )

    # 1단계: TierRegistry로 tier 분류 (기존 코드)
    path = request.path
    # ... 이하 기존 로직 동일
```

### 3.3 SelfHealingHttpClient 수정

**파일**: `services/http_client.py`

기존 `_get_headers()` 메서드에 deadline 전파 로직을 추가합니다. `_is_chaos_request` 전파 패턴과 동일한 구조입니다.

```python
def _get_headers(self, extra_headers=None):
    headers = {**self.base_headers}
    if extra_headers:
        headers.update(extra_headers)

    # Chaos 실험 컨텍스트 전파 (기존)
    if _is_chaos_request.get():
        headers[SYNTHETIC_HEADER] = "chaos-experiment"
        if self._experiment_id:
            headers[CHAOS_EXPERIMENT_ID_HEADER] = self._experiment_id

    # Deadline 전파 (신규)
    try:
        from selfhealing.scaling.deadline_context import (
            DEADLINE_HEADER,
            get_propagation_header_value,
        )
        deadline_value = get_propagation_header_value()
        if deadline_value is not None:
            headers[DEADLINE_HEADER] = deadline_value
    except ImportError:
        pass

    return headers
```

또한 `_execute_request()`에서 deadline 기반 동적 timeout 조정:

```python
def _execute_request(self, method, url, **kwargs):
    headers = self._get_headers(kwargs.pop("headers", None))
    timeout = kwargs.pop("timeout", self.default_timeout)

    # Deadline 기반 timeout 자동 조정 (신규)
    try:
        from selfhealing.scaling.deadline_context import get_remaining_ms
        remaining = get_remaining_ms()
        if remaining is not None:
            deadline_timeout = remaining / 1000.0  # ms → seconds
            if timeout is None or deadline_timeout < timeout:
                timeout = deadline_timeout
    except ImportError:
        pass

    request_func = getattr(req_lib, method.lower())
    # ... 이하 기존 로직
```

### 3.4 503 Deadline 거절 응답

**AdmissionControlMiddleware에 추가**:

Deadline 초과 시 **503 Service Unavailable + `Retry-After: 0`**을 반환합니다.

**408을 사용하지 않는 이유**:
1. RFC 7231에 따르면 408은 "서버가 클라이언트의 요청을 기다리다 타임아웃"을 의미하며, deadline 초과와 의미가 다름
2. 많은 HTTP 클라이언트/LB(AWS ALB, Envoy proxy 등)가 408을 **자동 재시도**하도록 설정됨
3. 이미 시간이 부족한 요청을 재시도하면 **Cascading Failure** 발생

기존 시스템의 거부 응답 패턴과 동일하게 503을 사용합니다:
- `_create_rejection_response()` → `status=503`, `Retry-After: 30` (`admission_control.py` L204-206)
- `_create_overload_response()` → `status=503`, `Retry-After` 동적 (`middleware/backpressure.py` L88-100)
- `_create_load_shedding_response()` → `status=503` (`tiering/middleware.py` L184)

`code: "DEADLINE_FAST_FAIL"`로 일반 503(과부하)과 구분하고, `Retry-After: 0`으로 재시도를 금지합니다.

```python
def _create_deadline_rejection_response(self, request, remaining_ms):
    """503 Service Unavailable 응답 — deadline 만료 근접."""
    from django.http import JsonResponse

    response = JsonResponse(
        {
            "error": "Deadline Exceeded",
            "code": "DEADLINE_FAST_FAIL",
            "message": (
                "상위 서비스의 deadline이 만료에 근접하여 "
                "요청이 즉시 거절되었습니다."
            ),
            "remaining_ms": remaining_ms,
            "retry_after": 0,
        },
        status=503,
    )
    response["Retry-After"] = "0"
    return response
```

**기존 패턴과의 일관성**: `_create_rejection_response()`와 동일하게 body에 `retry_after` 필드를 포함하고 HTTP 헤더 `Retry-After`도 설정합니다. `retryable` 필드는 기존 거부 응답에 없으므로 추가하지 않습니다.

### 3.5 DB Query Timeout 연동

`DeadlineContext`의 남은 시간을 PostgreSQL `statement_timeout`에 동적으로 주입합니다.

**기존 인프라**: `adapters/postgres/repository.py`에 이미 `timeout_context()` 컨텍스트 매니저가 존재합니다:

```python
# adapters/postgres/repository.py L417-436
@contextmanager
def timeout_context(self, lock_timeout_ms=0, statement_timeout_ms=0):
    try:
        if statement_timeout_ms > 0:
            self.set_statement_timeout(statement_timeout_ms)
        yield
    finally:
        self.reset_timeouts()
```

**불필요한 SET 명령 스킵 최적화**: Production 기본 `statement_timeout=30000`(`settings/production.py` L58)이므로, deadline 남은 시간이 기본 DB timeout 이상이면 `SET statement_timeout` 쿼리를 생략하여 불필요한 DB Round Trip을 방지합니다.

```python
def get_deadline_aware_statement_timeout(
    default_db_timeout_ms: int = 30_000,
) -> int | None:
    """
    DeadlineContext 남은 시간과 기본 DB timeout 중 작은 값 반환.
    deadline 미설정이거나 기본 DB timeout보다 넉넉하면 None (SET 불필요).

    Args:
        default_db_timeout_ms: DB 기본 statement_timeout (production.py와 동기화)

    Returns:
        설정할 timeout(ms) 또는 None(SET 불필요)
    """
    from selfhealing.scaling.deadline_context import get_remaining_ms

    remaining = get_remaining_ms()
    if remaining is None:
        return None  # deadline 미설정

    if remaining >= default_db_timeout_ms:
        return None  # 넉넉하면 SET 스킵

    return max(1, int(remaining))  # 최소 1ms
```

**고정 임계값(예: 10초)을 사용하지 않는 이유**: DB timeout이 5초인 환경에서는 10초 임계값이 무의미합니다. `default_db_timeout_ms`와 비교하는 것이 환경에 무관하게 정확합니다.

---

## 4. 통합 지점

### 4.1 기존 파이프라인 내 위치

```
HTTP 요청 수신
    ↓
[Deadline Context 설정 + Fast-Fail 체크]  ← 신규 (0단계)
    ↓
[TierRegistry tier 분류]                  ← 기존 (1단계)
    ↓
[TrafficGate 판정]                        ← 기존 (2단계)
    ↓
[비즈니스 로직 처리]
    ↓
[하위 서비스 호출 시 deadline 헤더 자동 전파]  ← 신규
```

### 4.2 TrafficGate와의 연동

TrafficGate의 `should_allow()` 메서드 (`scaling/traffic_gate.py` L195-248)에도 선택적으로 deadline 체크를 추가할 수 있습니다:

```python
def should_allow(self, priority=0, bulkhead_name=None, metadata=None):
    # 기존 3단계 파이프라인 전에 deadline 확인
    from selfhealing.scaling.deadline_context import is_expired
    if is_expired():
        return TrafficDecision(
            allowed=False,
            reason="Deadline expired",
            level=current_level,
            gate="DeadlineContext",
            metadata=metadata,
        )
    # ... 이하 기존 Bulkhead → LoadShedding → RateController
```

### 4.3 영향 범위

| 파일 | 변경 유형 | 규모 |
|------|-----------|------|
| `scaling/deadline_context.py` | 신규 생성 | ~150줄 |
| `api/django/admission_control.py` | `_process_request()` 수정 | ~20줄 추가 |
| `services/http_client.py` | `_get_headers()`, `_execute_request()` 수정 | ~15줄 추가 |
| `scaling/traffic_gate.py` | `should_allow()` 선택적 수정 | ~8줄 추가 |
| `nginx/nginx.conf` | 헤더 Sanitization 추가 | ~2줄 추가 |

---

## 5. 테스트 전략

### 5.1 단위 테스트

```
tests/unit/scaling/test_deadline_context.py
├── TestParseDeadlineHeader
│   ├── test_valid_ms_suffix          # "2500ms" → 2500.0
│   ├── test_valid_no_suffix          # "2500" → 2500.0
│   ├── test_valid_float              # "1500.5ms" → 1500.5
│   ├── test_invalid_format           # "abc" → None
│   └── test_empty_string             # "" → None
├── TestDeadlineContext
│   ├── test_set_and_get_remaining    # set 후 get 검증 (buffer 차감 반영)
│   ├── test_is_expired               # 0ms 설정 시 expired
│   ├── test_should_fast_fail         # 남은 500ms, 예상 2000ms → True
│   ├── test_no_deadline_no_fast_fail # 미설정 시 False
│   ├── test_deadline_scope           # context manager 정상 복원
│   └── test_network_buffer_deduction # 2000ms 설정 → 1950ms 반환 (50ms 차감)
├── TestNetworkCongestionDetection
│   ├── test_exhausted_on_arrival     # 30ms 설정 시 adjusted <= 0, WARNING 로그
│   └── test_buffer_equals_remaining  # 50ms 설정 시 adjusted == 0
├── TestDeadlinePropagation
│   ├── test_propagation_header_value # 남은 시간 → "1234ms"
│   └── test_propagation_when_expired # 만료 시 None
├── TestDbTimeoutIntegration
│   ├── test_deadline_aware_timeout_shorter  # 남은 1000ms → timeout=1000
│   ├── test_deadline_aware_timeout_skip     # 남은 35000ms → None (SET 불필요)
│   └── test_deadline_aware_timeout_none     # deadline 미설정 → None
```

### 5.2 통합 테스트

```
tests/unit/api/test_admission_control_deadline.py
├── test_deadline_header_fast_fail    # 남은 30ms → 503 응답 (DEADLINE_FAST_FAIL)
├── test_deadline_header_allowed      # 남은 5000ms → 정상 통과
├── test_no_deadline_header           # 헤더 없음 → 기존 동작 유지
├── test_invalid_deadline_header      # 잘못된 형식 → 무시, 기존 동작
├── test_deadline_response_retry_after # 503 응답에 Retry-After: 0 포함
├── test_deadline_response_code       # body.code == "DEADLINE_FAST_FAIL"

tests/unit/services/test_http_client_deadline.py
├── test_deadline_header_propagation  # 헤더 자동 주입 확인
├── test_timeout_adjustment           # deadline < default_timeout 시 조정
```

---

## 6. 설정

### 6.1 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SELFHEALING_DEADLINE_ENABLED` | `true` | Deadline Context 활성화 여부 |
| `SELFHEALING_DEADLINE_MINIMUM_USEFUL_MS` | `50` | 최소 유효 시간 (ms) |
| `SELFHEALING_DEADLINE_NETWORK_BUFFER_MS` | `50` | 네트워크 레이턴시 보정 Buffer (ms) |

### 6.2 Prometheus 메트릭

기존 메트릭 네이밍 규약(`selfhealing_` 접두사 + `_total`/`_ms` 접미사)을 따릅니다.
참조: `scaling/metrics.py`의 `BackpressureMetrics`, `resilience/bulkhead/metrics.py`의 `selfhealing_bulkhead_*`.

| 메트릭 | 타입 | 라벨 | 설명 |
|--------|------|------|------|
| `selfhealing_deadline_fast_fail_total` | Counter | `tier`, `path_prefix` | Fast-Fail 거절 횟수 |
| `selfhealing_deadline_remaining_ms` | Histogram | `tier` | 수신 시점의 남은 시간 분포 |
| `selfhealing_deadline_exhausted_on_arrival_total` | Counter | `path_prefix` | 도착 시점에 이미 만료된 요청 수 (Buffer 차감 후 ≤0) |

**`exhausted_on_arrival` 네이밍 근거**: 리뷰에서 `exhausted_by_buffer`가 제안되었으나, Buffer가 원인이 아니라 "도착 시점에 이미 만료"가 정확한 의미이므로 `on_arrival`로 명명합니다. 기존 `BUDGET_EXHAUSTED_BY_SLO_KEY`(`services/error_budget_gate/redis_flag.py` L19)와 도메인이 완전히 다르므로 혼동 없습니다.

---

## 7. Fail-Open 안전성

Deadline Context는 **모든 경로에서 Fail-Open**으로 동작합니다:

1. **헤더 파싱 실패**: `parse_deadline_header()` → `None` 반환 → 기존 동작 유지
2. **ContextVar 미설정**: `get_remaining_ms()` → `None` → Fast-Fail 하지 않음
3. **ImportError**: `try/except ImportError`로 모든 통합 지점 보호
4. **예외 발생**: AdmissionControlMiddleware의 기존 `__call__()` (`admission_control.py` L113-119)이 모든 예외를 catch하여 요청을 통과시킴:

```python
def __call__(self, request):
    if not self._enabled:
        return self.get_response(request)
    try:
        return self._process_request(request)
    except Exception as e:
        logger.error("[AdmissionControlMiddleware] Error: %s, allowing request", e)
        return self.get_response(request)
```

---

## 8. 보안: 헤더 Sanitization

### 8.1 문제

클라이언트가 `X-Deadline-Remaining: 1ms`를 보내면 모든 요청이 Fast-Fail 되어 **DoS 공격**이 됩니다.

현재 Nginx 설정(`nginx.conf` L67-78)에는 `X-Deadline-Remaining` 처리가 **전혀 없습니다**:

```properties
location / {
    proxy_pass http://django_app;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Host $host;
    proxy_redirect off;
    # X-Deadline-Remaining에 대한 처리 없음
}
```

### 8.2 Nginx 설정 수정

Trusted Boundary(외부 진입점)에서 클라이언트 헤더를 **제거**합니다.

```nginx
# nginx/nginx.conf — location / 블록에 추가
proxy_set_header X-Deadline-Remaining "";

# nginx/nginx.conf — location /api/ 블록에 추가
proxy_set_header X-Deadline-Remaining "";
```

이렇게 하면:
- **외부 유입**: 클라이언트가 보낸 `X-Deadline-Remaining` 헤더가 제거됨
- **내부 서비스 간**: `SelfHealingHttpClient._get_headers()`가 자동 전파하므로 영향 없음

### 8.3 Initial Emitter

최초 `X-Deadline-Remaining` 헤더는 **맨 앞단 Django 서버(또는 API Gateway)**에서 생성합니다.

| 생성 방식 | 적용 시점 | 비고 |
|---|---|---|
| Nginx에서 초기 deadline 설정 | `proxy_set_header X-Deadline-Remaining "30000ms"` | `proxy_read_timeout=30s`와 동기화 |
| Django 미들웨어에서 설정 | AdmissionControlMiddleware 0단계 | 헤더 없을 때 기본값 설정 가능 |
| 하위 서비스 호출 시 자동 전파 | `SelfHealingHttpClient._get_headers()` | 기존 `_is_chaos_request` 전파 패턴과 동일 |

클라이언트(앱/웹)에서 **절대 생성하지 않습니다**.

### 8.4 헤더 네이밍 일관성

| 용도 | 헤더 패턴 | 예시 |
|---|---|---|
| **요청 헤더** (서비스 간 전파) | `X-{도메인명}` | `X-Deadline-Remaining`, `X-Self-Healing-Synthetic` |
| **응답 헤더** (시스템 상태 노출) | `X-SelfHealing-{속성}` | `X-SelfHealing-Backpressure-Level`, `X-SelfHealing-Degraded-Features` |

`X-Deadline-Remaining`은 **요청 헤더**이므로 `X-SelfHealing-` 접두사를 사용하지 않습니다. gRPC 업계 관례(`grpc-timeout`)와 일관성을 유지합니다.

---

## 9. 설계 결정 요약

논의를 통해 확정된 10가지 설계 결정을 정리합니다.

| # | 항목 | 결정 | 근거 코드 |
|---|------|------|-----------|
| 1 | Network Latency Buffer | 50ms 차감 + `exhausted_on_arrival` 메트릭 | `nginx.conf` L82 (proxy_connect_timeout 3s) |
| 2 | Duration 방식 | Remaining Duration (절대 시간 아님) | `core/hedging/async_executor.py` L337 (time.perf_counter) |
| 3 | 실행 환경 | WSGI gthread, ContextVar 안전 | `docker-compose.yml` L36 (--worker-class gthread) |
| 4 | Thread 전파 | 기존 `copy_context()` 인프라 활용 | `resilience/bulkhead/threadpool.py` L170 |
| 5 | DB Timeout 연동 | `min(remaining, db_default)`, 넉넉하면 스킵 | `settings/production.py` L58 (statement_timeout=30000) |
| 6 | Transaction 안전 | Fast-Fail은 DB 접근 전이므로 안전 | `admission_control.py` L120 (_process_request 0단계) |
| 7 | Celery 비전파 | 독립 라이프사이클 유지 | `context/celery_propagation.py` (CausationContext만 전파) |
| 8 | Nginx Sanitization | 외부 헤더 제거 필수 | `nginx.conf` L67-78 (현재 처리 없음) |
| 9 | estimated_ms 출처 | 1단계 Hardcoded → 2단계 Metrics 기반 | `core/hedging/latency_tracker.py` (ADAPTIVE P50 패턴) |
| 10 | 응답 코드 | 503 + Retry-After: 0 (408 사용 안 함) | `admission_control.py` L204 (기존 503 패턴) |
