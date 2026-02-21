# 266. Cell OTel Propagation — OpenTelemetry Baggage 통합 전파

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
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
        OTel context token (복원용)
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

### 3.3 Baggage → ContextVar 복원 (수신 측)

```python
def restore_contextvars_from_baggage() -> None:
    """
    수신된 OTel Baggage에서 ContextVar 복원.

    Django 미들웨어 또는 Celery task_prerun에서 호출.
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

---

## 4. SelfHealingHttpClient 연동

### 4.1 현재 수동 헤더 주입 (제거 대상)

**코드 근거**: `services/http_client.py` L140-155

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

### 4.2 OTel Baggage 통합 후

Baggage Propagator가 등록되면 `RequestsInstrumentor`가 자동으로
`baggage` 헤더를 주입하므로, `_get_headers()`의 수동 deadline 전파 코드를
**점진적으로 제거**할 수 있다. 단, 하위 호환성을 위해 양쪽 모두 유지하는
전환 기간(Transition Period)이 필요하다.

---

## 5. 변경 범위

| 파일 | 변경 | 유형 |
|------|------|------|
| `observability/baggage.py` | 신규 생성 — Propagator 설정 + 동기화 로직 | 필수 |
| `observability/__init__.py` | `initialize_opentelemetry()` 후 `setup_baggage_propagation()` 호출 추가 | 필수 |
| `api/django/cell/middleware.py` | `sync_contextvars_to_baggage()` 호출 추가 | 필수 |
| `services/http_client.py` | 수동 deadline 헤더 주입 → Baggage로 전환 (점진적) | 선택 |

---

## 6. 의존성

| 패키지 | 용도 | 현재 상태 |
|--------|------|----------|
| `opentelemetry-api` | `baggage`, `propagate` 모듈 | ✅ 이미 설치됨 |
| `opentelemetry-sdk` | TracerProvider | ✅ 이미 설치됨 |
| Baggage Propagator | `W3CBaggagePropagator` | `opentelemetry-api`에 포함 |

추가 패키지 설치 불필요.

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `263_CELL_TAGGER.md` | `_current_cell_id` ContextVar 제공자 (선행 의존) |
| `267_CELL_EXTERNAL_API_CONTEXT.md` | Baggage 기반 외부 API 전파 (후속) |
| `265_CELL_EVACUATION_POLICY.md` | Cell 상태 변경 이벤트의 OTel Span 기록 |
| `observability/__init__.py` | OTel SDK 초기화 코드 |
| `services/http_client.py` | 수동 헤더 전파 → Baggage 전환 대상 |
| `context/celery_propagation.py` | Celery Causation 전파 패턴 참조 |
