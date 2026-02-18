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


def set_deadline(remaining_ms: float) -> None:
    """
    현재 컨텍스트에 deadline 설정.

    Args:
        remaining_ms: 남은 시간 (밀리초)
    """
    deadline = time.monotonic() + (remaining_ms / 1000.0)
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

```python
def _create_deadline_rejection_response(self, request, remaining_ms):
    """408 Request Timeout 응답 — deadline 만료."""
    from django.http import JsonResponse

    return JsonResponse(
        {
            "error": "Deadline Exceeded",
            "code": "DEADLINE_FAST_FAIL",
            "message": (
                "상위 서비스의 deadline이 만료에 근접하여 "
                "요청이 즉시 거절되었습니다."
            ),
            "remaining_ms": remaining_ms,
        },
        status=408,
    )
```

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
│   ├── test_set_and_get_remaining    # set 후 get 검증
│   ├── test_is_expired               # 0ms 설정 시 expired
│   ├── test_should_fast_fail         # 남은 500ms, 예상 2000ms → True
│   ├── test_no_deadline_no_fast_fail # 미설정 시 False
│   └── test_deadline_scope           # context manager 정상 복원
├── TestDeadlinePropagation
│   ├── test_propagation_header_value # 남은 시간 → "1234ms"
│   └── test_propagation_when_expired # 만료 시 None
```

### 5.2 통합 테스트

```
tests/unit/api/test_admission_control_deadline.py
├── test_deadline_header_fast_fail    # 남은 30ms → 408 응답
├── test_deadline_header_allowed      # 남은 5000ms → 정상 통과
├── test_no_deadline_header           # 헤더 없음 → 기존 동작 유지
├── test_invalid_deadline_header      # 잘못된 형식 → 무시, 기존 동작

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

### 6.2 Prometheus 메트릭

| 메트릭 | 타입 | 라벨 | 설명 |
|--------|------|------|------|
| `selfhealing_deadline_fast_fail_total` | Counter | `tier`, `path_prefix` | Fast-Fail 거절 횟수 |
| `selfhealing_deadline_remaining_ms` | Histogram | `tier` | 수신 시점의 남은 시간 분포 |

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
