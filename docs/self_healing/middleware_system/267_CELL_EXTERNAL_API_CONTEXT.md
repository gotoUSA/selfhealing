# 267. Cell External API Context — 외부 API 호출 시 cell_id 자동 전파

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Depends**: [263_CELL_TAGGER.md](263_CELL_TAGGER.md), [266_CELL_OTEL_PROPAGATION.md](266_CELL_OTEL_PROPAGATION.md)

---

## 0. 요약

Django 앱이 외부 API(결제 PG사, 외부 MSA 등)를 호출할 때
`cell_id`를 자동 전파하여 **전체 분산 추적과 Blast Radius 격리**를 완성한다.

266에서 구현한 OTel Baggage Propagator를 기반으로,
`SelfHealingHttpClient`의 outgoing 요청에 `cell_id`가 자동 주입되도록 한다.

---

## 1. 현재 외부 API 호출 구조 분석

### 1.1 SelfHealingHttpClient

**코드 근거**: `services/http_client.py` (360줄)

| 기능 | 상태 | 참조 |
|------|------|------|
| Chaos 실험 헤더 전파 (`X-Self-Healing-Synthetic`) | ✅ 구현됨 | `_get_headers()` L138-148 |
| Deadline 헤더 전파 (`X-Deadline-Remaining`) | ✅ 구현됨 | `_get_headers()` L150-160 |
| `traceparent` 자동 주입 (OTel `RequestsInstrumentor`) | ✅ 구현됨 | `observability/__init__.py` L305 |
| OTel Span 자동 생성 | ✅ 구현됨 | `RequestsInstrumentor` |
| `cell_id` 전파 | ❌ **미구현** | 이 문서의 대상 |

### 1.2 기존 `_get_headers()` 패턴

```python
# services/http_client.py L126-160 — 현재 수동 헤더 주입 패턴
def _get_headers(self, extra_headers=None):
    headers = {**self.base_headers}
    if extra_headers:
        headers.update(extra_headers)

    # 1. Chaos 실험 컨텍스트 (ContextVar 기반)
    if _is_chaos_request.get():
        headers[SYNTHETIC_HEADER] = "chaos-experiment"
        if self._experiment_id:
            headers[CHAOS_EXPERIMENT_ID_HEADER] = self._experiment_id

    # 2. Deadline 헤더 (scaling/deadline_context.py 기반)
    try:
        from selfhealing.scaling.deadline_context import (
            DEADLINE_HEADER, get_propagation_header_value,
        )
        deadline_value = get_propagation_header_value()
        if deadline_value is not None:
            headers[DEADLINE_HEADER] = deadline_value
    except ImportError:
        pass

    return headers
```

**관찰**: Chaos 헤더와 Deadline 헤더는 `_get_headers()`에서 **수동 주입**한다.
`traceparent`만 `RequestsInstrumentor`에 의해 자동 주입된다.

---

## 2. 구현 전략

### 2.1 단기 (267 범위) — `_get_headers()` 확장

266의 OTel Baggage가 완성되기 **전**에도 `cell_id`를 전파할 수 있도록,
기존 `_get_headers()` 패턴에 `X-Cell-Id` 헤더를 추가한다.
Deadline 헤더(`X-Deadline-Remaining`) 전파와 동일한 패턴이다.

```python
# services/http_client.py — _get_headers() 확장 (3줄 추가)
def _get_headers(self, extra_headers=None):
    headers = {**self.base_headers}
    # ... 기존 Chaos/Deadline 헤더 ...

    # 3. Cell ID 헤더 전파 (context/cell_context.py 기반)
    try:
        from selfhealing.context.cell_context import get_current_cell_id
        cell_id = get_current_cell_id()
        if cell_id:
            headers["X-Cell-Id"] = cell_id
    except ImportError:
        pass

    return headers
```

### 2.2 장기 (266 완성 후) — OTel Baggage 자동 주입

266에서 `W3CBaggagePropagator`가 등록되면:

1. `sync_contextvars_to_baggage()`가 `_current_cell_id` → `selfhealing.cell_id` Baggage로 동기화
2. `RequestsInstrumentor`가 outgoing 요청 시 `baggage: selfhealing.cell_id=cell-3` 헤더 자동 주입
3. `_get_headers()`의 수동 `X-Cell-Id` 주입은 **제거 가능** (하위 호환 전환 기간 후)

```
[전환 기간]
  X-Cell-Id 헤더 (수동)  + baggage 헤더 (자동) → 양쪽 모두 전송
  수신 측: X-Cell-Id 우선, 없으면 baggage에서 추출

[전환 완료 후]
  baggage 헤더만 전송 → X-Cell-Id 수동 주입 코드 제거
```

---

## 3. 수신 측 처리

### 3.1 외부 MSA에서 cell_id 수신

266에서 구현한 `restore_contextvars_from_baggage()`가 수신 측 미들웨어에서 자동 복원.

추가로, OTel Baggage를 지원하지 않는 레거시 서비스를 위해
`X-Cell-Id` 헤더 직접 읽기도 지원:

```python
# CellTaggingMiddleware 확장 — 수신 헤더에서 cell_id 읽기
def __call__(self, request):
    # 1순위: 수신 헤더에서 이미 전파된 cell_id 확인
    incoming_cell_id = request.META.get("HTTP_X_CELL_ID")
    if incoming_cell_id:
        # 상위 서비스에서 전파된 cell_id를 그대로 사용 (재해시 방지)
        request.cell_id = incoming_cell_id
        token = _current_cell_id.set(incoming_cell_id)
        # ...
    else:
        # 2순위: 로컬에서 cell_id 결정
        cell_id = tagger.resolve_cell_id_from_request(request)
        # ...
```

### 3.2 Blast Radius 추적 흐름

```
[API Gateway]
  → [Service A: CellTaggingMiddleware]
      cell_id = "cell-3" (user_id 기반 해싱)
      _current_cell_id.set("cell-3")
      → SelfHealingHttpClient.post(payment_api_url, ...)
          → X-Cell-Id: cell-3 (수동)
          → baggage: selfhealing.cell_id=cell-3 (자동, 266 이후)
          → [외부 PG사]
              (cell_id 무시하거나 로깅)
      → force_open_circuit_breaker.delay(service_name="toss_api")
          → Celery headers["cell_id"] = "cell-3" (263 하이브리드 1순위)
          → [Worker: cell-3에서 CB 처리]
```

cell-3에서 PG사 장애 발생 시:
- cell-3의 Bulkhead만 소진 → 다른 Cell의 결제 요청은 영향 없음
- OTel Span에 `selfhealing.cell_id=cell-3` Baggage → Grafana에서 Cell별 장애 범위 추적

---

## 4. 변경 범위

| 파일 | 변경 | 유형 |
|------|------|------|
| `services/http_client.py` | `_get_headers()`에 `X-Cell-Id` 추가 (3줄) | 필수 |
| `api/django/cell/middleware.py` | 수신 `HTTP_X_CELL_ID` 헤더 처리 추가 | 선택 |

**기존 파일 최소 변경**: `SelfHealingHttpClient._get_headers()` 3줄 추가만 필수.
OTel Baggage 자동 주입은 266 완성 시 추가 코드 변경 없이 자동 적용.

---

## 5. 관련 문서

| 문서 | 관계 |
|------|------|
| `263_CELL_TAGGER.md` | `_current_cell_id` ContextVar 제공자 (선행 의존) |
| `266_CELL_OTEL_PROPAGATION.md` | OTel Baggage Propagator (장기 전략 기반) |
| `265_CELL_EVACUATION_POLICY.md` | Cell 장애 시 트래픽 대피 정책 |
| `services/http_client.py` | 외부 API 호출 클라이언트 (수정 대상) |
| `observability/__init__.py` | `RequestsInstrumentor` — traceparent 자동 주입 |
