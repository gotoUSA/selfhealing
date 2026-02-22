# 266. Cell OTel Propagation — OpenTelemetry Baggage 통합 전파

> **Version**: 2.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Implemented
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Depends**: [263_CELL_TAGGER.md](263_CELL_TAGGER.md) — `_current_cell_id` ContextVar

---

## 0. 요약

263에서 구현한 `_current_cell_id` ContextVar와 `X-Cell-Id` HTTP 헤더를
**OpenTelemetry Baggage**로 통합하여, 서비스 간 자동 전파를 구현한다.

현재 코드베이스에서 각 모듈이 **독립적인 헤더**로 컨텍스트를 전파하고 있는 현황을
OTel Baggage로 정규화하는 Phase 2 작업이다.

---

## 1. 현재 컨텍스트 전파 현황 — 파편화 문제

### 1.1 모듈별 독립 전파 (현재 상태)

| 모듈 | ContextVar | HTTP 헤더 | Celery 전파 방식 |
|------|-----------|----------|-----------------|
| `audit/trace.py` L19 | `_trace_id_var` | `X-Request-ID` | 수동 dict 전달 |
| `scaling/deadline_context.py` L51 | `_request_deadline` | `X-Deadline-Remaining` | `SelfHealingHttpClient._get_headers()` 수동 주입 |
| `decorators/domain_tag.py` L50 | `_current_domain` | `X-Domain` | DomainMiddlewareMixin |
| `context/actor_context.py` L53 | `_current_actor` | (없음) | `get_actor_for_celery()` → kwargs dict |
| `context/causation_context.py` L226 | `_current_causation` | `x-selfhealing-cascade-id` 외 4개 | `before_task_publish` 시그널 자동 주입 |
| **`context/cell_context.py` (263 신규)** | `_current_cell_id` | `X-Cell-Id` | `before_task_publish` 시그널 자동 주입 |

**문제**: 6개 모듈이 각각 별도의 전파 메커니즘을 구현. 새 Context 추가 시마다
HTTP 클라이언트, Celery 시그널, 미들웨어를 모두 개별 수정해야 한다.

### 1.2 OTel Baggage 통합 (목표 상태)

```
[Service A]                          [Service B]
ContextVar들 → OTel Baggage 통합  →  W3C baggage 헤더로 자동 전파  →  ContextVar 복원
  _current_cell_id                     baggage: selfhealing.cell_id=cell-3,
  _current_domain                               selfhealing.domain=payment,
  _request_deadline                              selfhealing.deadline_remaining=450
```

---

## 2. 기존 OTel 인프라 분석

**코드 근거**: `observability/__init__.py` (582줄)

### 2.1 현재 구현되어 있는 것

| 기능 | 구현 상태 | 파일 |
|------|----------|------|
| `TracerProvider` + `OTLPSpanExporter` | ✅ 완성 | `observability/__init__.py` L37-140 |
| `EmergencyLevelAdaptiveSampler` | ✅ 완성 | `observability/sampler.py` L1-237 |
| `RequestsInstrumentor` (traceparent 자동 주입) | ✅ 완성 | `observability/__init__.py` L284-315 |
| `CeleryInstrumentor` (trace context 전파) | ✅ 완성 | `observability/__init__.py` L339-372 |
| `LoggingInstrumentor` (trace_id/span_id 주입) | ✅ 완성 | `observability/__init__.py` L520-540 |

### 2.2 현재 구현되어 있지 않은 것

| 기능 | 상태 | 필요 사항 |
|------|------|----------|
| `W3CBaggagePropagator` 등록 | ❌ 미구현 | `CompositePropagator`에 추가 |
| 커스텀 Baggage 항목 주입 | ❌ 미구현 | ContextVar → Baggage 자동 동기화 |
| Baggage → ContextVar 복원 | ❌ 미구현 | 수신 측 미들웨어/시그널에서 복원 |

### 2.3 핵심 관찰

`RequestsInstrumentor().instrument()` (`observability/__init__.py` L305)는
**`traceparent` 헤더만** 자동 주입한다. `W3CBaggagePropagator`가 등록되어 있지 않으므로
`baggage` 헤더는 전파되지 않는다. Baggage를 활성화하려면 Propagator 설정이 필요하다.

### 2.4 선행 수정 필수: `trace_id_middleware` try/finally 누락 (기술 부채)

**코드 근거**: `audit/trace.py` L288-319

```python
# 현재 코드 — try/finally 누락
def trace_id_middleware(get_response):
    def middleware(request):
        trace_id = extract_trace_id_from_request(request) or generate_trace_id()
        set_trace_id(trace_id)
        request.trace_id = trace_id
        response = get_response(request)       # ← 여기서 예외 발생 시
        response["X-Request-ID"] = trace_id
        clear_trace_id()                       # ← 이 줄 미실행 → trace_id 잔존
        return response
    return middleware
```

**문제**: 뷰에서 예외 발생 시 `clear_trace_id()`가 호출되지 않아,
WSGI 워커 재사용 환경에서 이전 요청의 `trace_id`가 다음 요청으로 누수된다.
ASGI(uvicorn) 환경에서는 더 심각한 문제가 될 수 있다.

**비교**: 프로젝트의 다른 미들웨어는 모두 `try/finally` 패턴을 사용한다:

| 미들웨어 | 패턴 | 파일 |
|----------|------|------|
| `CellTaggingMiddleware` | `token = .set()` → `try/finally: .reset(token)` ✅ | `api/django/cell/middleware.py` L59-65 |
| `ActorContextMiddleware` | `with ActorContext.set_actor():` (내부 try/finally) ✅ | `api/django/middleware/actor_context.py` L44-50 |
| `suppress_otel_instrumentation()` | `token = attach()` → `try/finally: detach(token)` ✅ | `services/http_client.py` L67-71 |
| **`trace_id_middleware`** | **try/finally 없음 ⚠️** | `audit/trace.py` L303-316 |

**수정 방안**: 266 구현 전에 Hotfix로 수정해야 한다.

```python
# 수정 후 — try/finally 적용
def trace_id_middleware(get_response):
    def middleware(request):
        trace_id = extract_trace_id_from_request(request) or generate_trace_id()
        set_trace_id(trace_id)
        request.trace_id = trace_id
        try:
            response = get_response(request)
            response["X-Request-ID"] = trace_id
            return response
        finally:
            clear_trace_id()
    return middleware
```

**이 수정을 선행하는 이유**: OTel Baggage 통합 후 `context.attach()`/`detach()`
토큰 관리가 추가되는데, 기존 미들웨어에서 컨텍스트 누수가 있으면
Baggage 값까지 오염되어 디버깅이 극히 어려워진다.

### 2.5 DjangoInstrumentor 부재 — 설정은 존재하나 소비자 없음

**코드 근거**: `settings/observability.py` L96-127

```python
# ① 설정은 이미 존재 — but 소비하는 코드 없음
django_instrument_enabled: bool = Field(
    default=True,
    validation_alias="OTEL_DJANGO_INSTRUMENT_ENABLED",
)

# ② excluded_urls — 기본값에 헬스체크/메트릭 경로 이미 포함
excluded_urls: str = Field(
    default="/health,/health/,/health/ready,/health/live,/health/l3,/metrics",
    validation_alias="OTEL_EXCLUDED_URLS",
)

def get_excluded_urls_list(self) -> list[str]:
    ...
```

**코드 근거**: `observability/__init__.py` (582줄 전체 검색)

`DjangoInstrumentor`를 호출하는 코드가 **단 한 줄도 없다.**
`django_instrument_enabled` 설정과 `excluded_urls` 설정이 선언만 되어 있고
소비하는 코드가 없는 상태이다.

**영향**: `DjangoInstrumentor`가 없으면 수신 측에서 `baggage` HTTP 헤더를
OTel Context로 자동 파싱하는 계층이 존재하지 않는다. 이는 Baggage 통합의
수신 측 복원(`restore_contextvars_from_baggage()`)이 동작하지 않음을 의미한다.

**해결**: 3.4항에서 `instrument_django()` 함수를 정의하고,
이미 존재하는 설정 인프라를 연결한다.

---

## 3. 구현 계획

### 3.1 CompositePropagator 설정

```python
"""
OTel Baggage Propagator 설정.

W3C TraceContext + Baggage Propagator를 CompositePropagator로 등록하여
모든 outgoing HTTP 요청에 baggage 헤더를 자동 주입한다.

기존 코드 참조:
- observability/__init__.py: TracerProvider 초기화
- RequestsInstrumentor: traceparent 자동 주입 (L305)
"""

from opentelemetry import propagate
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation import TraceContextTextMapPropagator


def setup_baggage_propagation() -> None:
    """
    W3C Baggage Propagator 등록.

    이 함수 호출 후 RequestsInstrumentor가 inject()를 실행할 때
    traceparent + baggage 헤더가 함께 전파된다.

    호출 시점: initialize_opentelemetry() 성공 후
    """
    propagate.set_global_textmap(
        CompositePropagator([
            TraceContextTextMapPropagator(),
            W3CBaggagePropagator(),
        ])
    )
```

### 3.2 ContextVar → Baggage 동기화 미들웨어

```python
"""
ContextVar → OTel Baggage 동기화.

Django 미들웨어에서 ContextVar 값을 OTel Baggage에 주입하면,
이후 SelfHealingHttpClient의 outgoing 요청에 baggage 헤더가 자동 포함된다.

대상 ContextVar:
- _current_cell_id (context/cell_context.py)
- _current_domain (decorators/domain_tag.py L50)
- _request_deadline (scaling/deadline_context.py L51)
"""

from opentelemetry import baggage, context

BAGGAGE_PREFIX = "selfhealing"

_CONTEXTVAR_BAGGAGE_MAP = {
    "cell_id": "selfhealing.context.cell_context:get_current_cell_id",
    "domain": "selfhealing.decorators.domain_tag:get_current_domain",
}


def sync_contextvars_to_baggage() -> object:
    """
    현재 ContextVar 값을 OTel Baggage에 동기화.

    Returns:
        OTel context token (복원용 — 반드시 detach 해야 함)
    """
    ctx = context.get_current()

    for key, getter_path in _CONTEXTVAR_BAGGAGE_MAP.items():
        module_path, func_name = getter_path.rsplit(":", 1)
        module = __import__(module_path, fromlist=[func_name])
        getter = getattr(module, func_name)
        value = getter()
        if value is not None:
            ctx = baggage.set_baggage(
                f"{BAGGAGE_PREFIX}.{key}", str(value), context=ctx
            )

    return context.attach(ctx)
```

**BaggageSyncMiddleware — try/finally Token 관리 필수**

OTel Context는 Immutable 객체이다. `context.attach()`가 반환한 token을
`context.detach(token)`으로 해제하지 않으면 다음 요청으로 컨텍스트가 누수된다.

기존 프로젝트의 표준 패턴(`services/http_client.py` L67-71)을 따른다:

```python
from opentelemetry import context


class BaggageSyncMiddleware:
    """
    ContextVar ↔ OTel Baggage 양방향 동기화 미들웨어.

    배치: CellTaggingMiddleware 직후 ([6.6])
    - 모든 ContextVar가 설정된 후 실행되어야 Baggage에 최신값이 반영됨
    - try/finally로 OTel Context token의 격리를 보장

    패턴 참조: services/http_client.py L67-71 (suppress_otel_instrumentation)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 수신 측: Baggage → ContextVar 복원
        restore_contextvars_from_baggage()

        # 송신 측: ContextVar → Baggage 동기화
        token = sync_contextvars_to_baggage()
        try:
            response = self.get_response(request)
        finally:
            # 요청 종료 시 OTel Context 복원 — 누수 방지
            context.detach(token)
        return response
```

### 3.3 Baggage → ContextVar 복원 (수신 측)

```python
def restore_contextvars_from_baggage() -> None:
    """
    수신된 OTel Baggage에서 ContextVar 복원.

    Django 미들웨어 또는 Celery task_prerun에서 호출.
    DjangoInstrumentor(3.4항)가 baggage 헤더를 OTel Context에 적재한 후
    호출되어야 한다.
    """
    cell_id = baggage.get_baggage(f"{BAGGAGE_PREFIX}.cell_id")
    if cell_id:
        from selfhealing.context.cell_context import _current_cell_id
        _current_cell_id.set(cell_id)

    domain = baggage.get_baggage(f"{BAGGAGE_PREFIX}.domain")
    if domain:
        from selfhealing.decorators.domain_tag import _current_domain
        _current_domain.set(domain)
```

### 3.4 DjangoInstrumentor 도입 — 수신 측 Baggage 자동 추출

**배경**: `DjangoInstrumentor` 없이는 수신 HTTP 요청의 `baggage` 헤더를
OTel Context에 자동으로 적재하는 계층이 존재하지 않는다.
3.3항의 `restore_contextvars_from_baggage()`가 `baggage.get_baggage()`를
호출해도 항상 `None`이 반환된다.

**기존 인프라**: `settings/observability.py`에 `django_instrument_enabled`(L96),
`excluded_urls`(L117), `get_excluded_urls_list()`(L123)가 이미 정의되어 있다.
소비하는 코드만 추가하면 된다.

```python
"""
observability/__init__.py에 추가할 instrument_django() 함수.

기존 instrument_requests(), instrument_celery()와 동일한 패턴.
settings/observability.py의 django_instrument_enabled, excluded_urls 설정을
소비한다.
"""

_django_instrumented: bool = False


def instrument_django() -> bool:
    """
    Enable automatic instrumentation for Django.

    WSGI 레벨에서 traceparent + baggage 헤더를 자동 추출하고,
    Django 요청에 대한 span을 자동 생성한다.

    DjangoInstrumentor는 내부적으로 MIDDLEWARE 최상단에
    _DjangoMiddleware를 자동 삽입한다 (settings.MIDDLEWARE.insert(0, ...)).
    따라서 BaggageSyncMiddleware보다 반드시 먼저 실행된다.

    excluded_urls: /health, /metrics 등 불필요한 span/baggage 파싱 제외.
    기본값: settings/observability.py L117-120
        "/health,/health/,/health/ready,/health/live,/health/l3,/metrics"

    Returns:
        bool: True if instrumentation was successful, False otherwise
    """
    global _django_instrumented

    if _django_instrumented:
        return True

    if not is_otel_enabled():
        return False

    try:
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        from selfhealing.settings.observability import get_otel_settings

        settings = get_otel_settings()

        if not settings.django_instrument_enabled:
            logger.debug("Django instrumentation disabled via OTEL_DJANGO_INSTRUMENT_ENABLED=false")
            return False

        # excluded_urls 설정 적용 — 환경변수 OTEL_PYTHON_DJANGO_EXCLUDED_URLS 사용
        import os
        excluded = ",".join(settings.get_excluded_urls_list())
        if excluded:
            os.environ.setdefault("OTEL_PYTHON_DJANGO_EXCLUDED_URLS", excluded)

        DjangoInstrumentor().instrument()
        _django_instrumented = True
        logger.info(
            "OpenTelemetry Django instrumentation enabled "
            "(excluded_urls=%s)",
            excluded or "none",
        )
        return True

    except ImportError:
        logger.debug("opentelemetry-instrumentation-django not installed")
        return False
    except Exception as e:
        logger.warning("Failed to instrument Django: %s", e)
        return False
```

**MIDDLEWARE 실행 순서 (DjangoInstrumentor 도입 후)**:

```
[auto-0] DjangoInstrumentor._DjangoMiddleware  ← 자동 삽입 (traceparent + baggage 추출)
[0]      PrometheusBeforeMiddleware
[1]      trace_id_middleware           ← 2.4항 Hotfix 적용 후
...
[6]      Django Core
[6.5]    CellTaggingMiddleware         ← ContextVar 설정
[6.6]    BaggageSyncMiddleware         ← 신규 (Baggage 복원 + ContextVar→Baggage 동기화)
[7]      HybridRateLimitMiddleware
```

`DjangoInstrumentor`가 `[auto-0]`에서 `baggage` 헤더를 파싱하여 OTel Context에
적재한 후, `[6.6]` `BaggageSyncMiddleware`에서 `restore_contextvars_from_baggage()`가
해당 값을 ContextVar로 복원한다.

---

## 4. SelfHealingHttpClient 연동 — 수동 헤더 전파 일괄 제거

### 4.1 현재 수동 헤더 주입 (제거 대상)

**코드 근거**: `services/http_client.py` L150-160

```python
# 현재: deadline 헤더를 수동으로 주입
def _get_headers(self, extra_headers=None):
    headers = {**self.base_headers}
    # ...
    # Deadline 헤더 전파 (상위 서비스 → 하위 서비스)
    try:
        from selfhealing.scaling.deadline_context import (
            DEADLINE_HEADER, get_propagation_header_value,
        )
        deadline_value = get_propagation_header_value()
        if deadline_value is not None:
            headers[DEADLINE_HEADER] = deadline_value
    except ImportError:
        pass
```

### 4.2 OTel Baggage로 일괄 전환 (점진적 전환 불필요)

현재 프로젝트는 **개발 단계**이며, 프로덕션에서 가동 중인 하위 서비스가 없다.
따라서 레거시 헤더와 Baggage를 양쪽 모두 유지하는 전환 기간(Transition Period)은
**불필요**하다. 다음을 일괄 수행한다:

**제거 대상 — 수동 헤더 주입 코드**:

| 파일 | 제거 내용 | 대체 |
|------|----------|------|
| `services/http_client.py` L150-160 | `_get_headers()` 내 Deadline 수동 주입 블록 | Baggage 자동 전파 |

**제거하지 않는 것 — Chaos 플래그**:

`_get_headers()`의 Chaos 실험 헤더(`X-Self-Healing-Synthetic`,
`X-Chaos-Experiment-Id`)는 OTel Baggage 전파 대상이 아니다.
이는 테스트 인프라 전용 메커니즘이므로 그대로 유지한다.

**수신 측 변경**:

수신 측에서 `X-Deadline-Remaining` 커스텀 헤더를 읽는 코드는
Baggage에서 읽도록 변경한다. 레거시 Fallback 없이 Baggage만 사용:

```python
def resolve_deadline_from_request(request) -> float | None:
    """
    Deadline 값 해석 — Baggage에서 직접 읽기.

    개발 단계이므로 레거시 헤더 Fallback 없이
    Baggage를 유일한 Source of Truth로 사용.
    """
    from opentelemetry import baggage

    deadline_baggage = baggage.get_baggage("selfhealing.deadline_remaining")
    if deadline_baggage is not None:
        return float(deadline_baggage)

    return None
```

### 4.3 Pre-request Hook — Mid-flight ContextVar 변경 대응

요청 사이클 초반(미들웨어)에서 ContextVar → Baggage 동기화를 수행한 후,
비즈니스 로직이 ContextVar를 중간에 변경하면 이후 outgoing 요청에
옛 Baggage 값이 전파된다.

**해결**: `SelfHealingHttpClient`의 요청 메서드에서 Pre-request Hook으로
ContextVar → Baggage 재동기화를 수행한다.

기존 `_get_headers()` 패턴 참조 — 이미 매 요청마다 `get_propagation_header_value()`로
**최신값을 읽는 구조**이므로, 동일한 위치에서 Baggage 동기화를 수행:

```python
def _sync_and_request(self, method, url, **kwargs):
    """
    요청 직전 ContextVar → Baggage 재동기화.

    패턴 참조: _get_headers()의 매 요청 최신값 읽기 (L150-160)
    """
    token = sync_contextvars_to_baggage()
    try:
        return self.session.request(method, url, **kwargs)
    finally:
        context.detach(token)
```

---

## 5. 변경 범위

| 파일 | 변경 | 유형 |
|------|------|------|
| `audit/trace.py` L288-319 | `trace_id_middleware` try/finally 패턴 적용 (선행 Hotfix) | **필수 (선행)** |
| `observability/baggage.py` | 신규 생성 — Propagator 설정 + 동기화 로직 | 필수 |
| `observability/__init__.py` | `instrument_django()` 함수 추가 + `initialize_opentelemetry()` 후 `setup_baggage_propagation()` 호출 | 필수 |
| `api/django/cell/middleware.py` | `BaggageSyncMiddleware` 추가 (try/finally detach 포함) | 필수 |
| `myproject/settings/base.py` | MIDDLEWARE 배열에 `BaggageSyncMiddleware` 추가 ([6.6] 위치) | 필수 |
| `services/http_client.py` | `_get_headers()` 내 수동 deadline 전파 블록 제거 + Pre-request Hook 추가 | 필수 |

---

## 6. 의존성

| 패키지 | 용도 | 현재 상태 |
|--------|------|----------|
| `opentelemetry-api` | `baggage`, `propagate` 모듈 | ✅ 이미 설치됨 |
| `opentelemetry-sdk` | TracerProvider | ✅ 이미 설치됨 |
| `opentelemetry-instrumentation-django` | `DjangoInstrumentor` | ❓ 설치 확인 필요 |
| Baggage Propagator | `W3CBaggagePropagator` | `opentelemetry-api`에 포함 |

`opentelemetry-instrumentation-django`가 미설치 시 `requirements.txt`에 추가.

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `263_CELL_TAGGER.md` | `_current_cell_id` ContextVar 제공자 (선행 의존) |
| `267_CELL_EXTERNAL_API_CONTEXT.md` | Baggage 기반 외부 API 전파 (후속) |
| `265_CELL_EVACUATION_POLICY.md` | Cell 상태 변경 이벤트의 OTel Span 기록 |
| `270_CELERY_CONTEXT_CONSOLIDATION.md` | Celery 컨텍스트 추출 유틸리티 통합 (후속) |
| `observability/__init__.py` | OTel SDK 초기화 코드 |
| `settings/observability.py` | `django_instrument_enabled`, `excluded_urls` 설정 (이미 존재) |
| `services/http_client.py` | 수동 헤더 전파 → Baggage 전환 대상 |
| `context/celery_propagation.py` | Celery Causation 전파 패턴 참조 |
