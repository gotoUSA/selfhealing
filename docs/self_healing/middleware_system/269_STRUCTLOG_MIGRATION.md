# 269. structlog 전면 전환 마이그레이션 계획

> **문서 번호**: 269
> **작성일**: 2026-02-22
> **상태**: Phase 3 완료 (수동 검수 완료 — import 복구, self._logger 전환, 이벤트 이름 정규화, 단위 테스트 27개 통과)
> **대상**: `packages/selfhealing-python/src/selfhealing/` 전체
> **관련 문서**: 156_OTEL_OBSERVABILITY_OVERVIEW.md, 157_OTEL_SDK_INTEGRATION.md

---

## 1. 현황 분석 (코드 기반 팩트)

### 1.1 정량 현황

| 지표 | 값 | 근거 |
|---|---|---|
| `logging.getLogger(__name__)` 사용 모듈 | **722개** | `grep -rl "logging.getLogger" --include="*.py"` |
| 총 `logger.*()` 호출 수 | **4,810건** | `grep -rcP "logger\.\w+\(" --include="*.py"` 합산 |
| f-string 로깅 (`logger.xxx(f"...")`) | **2,753건** (57.2%) | `grep -rP 'logger\.\w+\(f"' --include="*.py"` |
| plain string 로깅 | **1,999건** (41.6%) | f-string, %s 제외 |
| `%s`-style 로깅 | **58건** (1.2%) | `grep -rP 'logger\.\w+\("[^"]*%[sdf]' --include="*.py"` |
| `logger.exception()` 사용 | **29건** | `grep -rn "logger\.exception" --include="*.py"` |
| 고유 `[PrefixTag]` 수 | **566개** | `grep -rohP '\[\w+\]' --include="*.py" \| sort -u \| wc -l` |

### 1.2 레벨별 분포

| Level | 호출 수 | 비율 |
|---|---|---|
| debug | 1,031 | 21.4% |
| info | 1,356 | 28.2% |
| warning | 1,492 | 31.0% |
| error | 805 | 16.7% |
| exception | 29 | 0.6% |
| critical | ~40 | 0.8% |

### 1.3 모듈별 규모

| 디렉토리 | .py 파일 수 | 설명 |
|---|---|---|
| `services/` | 425 | 핵심 비즈니스 로직 (55개 서브모듈) |
| `adapters/` | 130 | 외부 시스템 어댑터 |
| `api/` | 114 | Django REST API |
| `settings/` | 106 | Pydantic 설정 |
| `audit/` | 98 | 감사 로깅 |
| `core/` | 46 | 코어 유틸리티 |
| `resilience/` | 31 | 회복탄력성 정책 |
| `tasks/` | 18 | Celery 태스크 |
| `metrics/` | 15 | Prometheus 메트릭 |
| `meta/` | 12 | Watchdog/Escalation |
| `multiregion/` | 11 | 멀티리전 |
| `scaling/` | 9 | Rate Controller |
| 기타 | 27 | interfaces, context, coordination 등 |

### 1.4 현재 로깅 패턴의 문제점

#### 문제 1: f-string Eager Evaluation (2,753건)

```python
# services/cell_topology/registry.py:282
logger.info(f"Evicted {len(evicted)} expired services from {cell_id}: {evicted}")
```

- DEBUG 레벨이 꺼져 있어도 `len(evicted)`, `evicted.__repr__()` 가 **항상 실행**됨
- self-healing hot path에서 불필요한 CPU 소비

#### 문제 2: 로그 집계(Aggregation) 불가

```python
# f-string: 매번 다른 문자열 — Sentry/Loki에서 그룹핑 불가
"Cell not found: cell-abc-123"
"Cell not found: cell-xyz-456"
```

#### 문제 3: OTEL LogRecord 구조 손실

```python
# f-string → msg만 있고 args 없음 → OTEL attributes 매핑 불가
logger.warning(f"Cell {cell_id}: manual restore failed: {e}")
# → LogRecord.msg = "Cell cell-abc-123: manual restore failed: timeout"
# → LogRecord.args = None
```

#### 문제 4: 수동 PrefixTag 유지 부담 (566개)

```python
# 현재: 모든 로그에 수동 태그 삽입
logger.info(f"[CircuitBreaker] Serving stale cache for '{service_name}'")
logger.warning(f"[AdaptiveThrottle] Governance blocked limit adjustment")
logger.error(f"[RedisAuditBuffer] Flush error: {e}")
```

- 566개 고유 태그, 오타/불일치 위험
- 클래스 이름과 태그가 불일치하는 경우 존재
  (예: `[CircuitBreaker]` 태그가 `services/error_budget_gate/fault_detector.py`에서도 사용)

---

## 2. 이벤트 네이밍 컨벤션 설계

### 2.1 선택한 방식: `{component}.{action}` (dot-separated snake_case)

```
circuit_breaker.state_changed
cell_registry.service_evicted
adaptive_throttle.limit_adjusted
resilient_storage.wal_recovery_completed
```

### 2.2 선택 근거 (코드 분석 기반)

현재 코드에서 관찰된 3가지 패턴을 분석하여 가장 적합한 네이밍 전략을 도출하였다.

#### 후보 A: 현재 `[PrefixTag]`를 그대로 snake_case 변환

```
# [SelfHealerWatchdog] {name} unhealthy, attempting recovery
→ event="self_healer_watchdog_unhealthy_attempting_recovery"
```

**불채택 사유**: 자연어 메시지를 이벤트 이름으로 쓰면 이벤트가 무한히 증가. Loki/Datadog에서 이벤트 기반 필터링이 불가능해진다. 실제 현재 코드에서 `[CircuitBreaker]` 뒤에 붙는 메시지가 15종 이상 — 이를 전부 별개 이벤트로 만들면 cardinality 폭발.

#### 후보 B: `{module_path}.{action}` (Python 모듈 경로 기반)

```
# selfhealing.services.circuit_breaker.service.state_changed
→ event="selfhealing.services.circuit_breaker.service.state_changed"
```

**불채택 사유**: `__name__`이 이미 `structlog.get_logger()`에 자동 바인딩되므로 이벤트 이름에 모듈 경로를 중복시킬 이유가 없다. 또한 리팩토링으로 파일이 이동하면 이벤트 이름이 깨진다.

#### 후보 C (채택): `{component}.{action}` (도메인 컴포넌트 기반)

```
# [CircuitBreaker] Serving stale cache for '{service_name}'
→ event="circuit_breaker.stale_cache_served"

# [CellRegistry] Evicted 3 expired services from cell-abc
→ event="cell_registry.services_evicted"
```

**채택 사유**:

1. **현재 566개 PrefixTag가 이미 도메인 컴포넌트를 정의**하고 있음. 이를 정규화하면 자연스러운 이벤트 네임스페이스가 된다.

2. **상위 20개 태그가 전체 호출의 ~60%를 차지** — 소수 컴포넌트가 대부분의 로그를 생산하므로 이벤트 네이밍이 체계적으로 관리 가능:

   | 현재 태그 | → structlog component | 호출 비율 |
   |---|---|---|
   | `[Metrics]` | `metrics` | 82회 |
   | `[AdaptiveThrottle]` | `adaptive_throttle` | 68회 |
   | `[EventHandler]` | `event_handler` | 57회 |
   | `[SelfHealing]` | `self_healing` | 50회 |
   | `[SelfHealerWatchdog]` | `watchdog` | 36회 |
   | `[CircuitBreaker]` | `circuit_breaker` | 30회 |
   | `[Recovery]` | `recovery` | 29회 |

3. **action 부분은 현재 메시지의 핵심 동사에서 도출**. 코드 분석에서 발견된 주요 verb 패턴:

   | 동사 (현재 메시지) | 빈도 | → action 이름 |
   |---|---|---|
   | Failed | 507회 | `*_failed` |
   | Initialized | 27회 | `initialized` |
   | Started | 24회 | `started` |
   | Registered | 31회 | `registered` |
   | Updated | 23회 | `updated` |
   | Stopped | 23회 | `stopped` |
   | Recovery | 22회 | `recovery_*` |
   | Reset | 29회 | `reset` |
   | Flushed | 13회 | `flushed` |
   | Loaded | 13회 | `loaded` |

4. **모듈 경로와 독립** — 파일 리팩토링해도 이벤트 이름 불변. `__name__`은 structlog가 별도 필드(`logger`)로 자동 기록.

### 2.3 이벤트 네이밍 규칙

```
{component}.{action}[.{detail}]
```

| 구성 요소 | 규칙 | 예시 |
|---|---|---|
| `component` | 현재 `[Tag]`의 snake_case 변환 | `circuit_breaker`, `cell_registry` |
| `action` | 과거형 동사 (완료된 사실) | `state_changed`, `services_evicted` |
| `detail` (선택) | 구체적 구분이 필요한 경우 | `recovery.wal_replayed`, `recovery.redis_restored` |

#### 금지 규칙

| 금지 패턴 | 이유 | 올바른 대안 |
|---|---|---|
| `circuit_breaker.serving_stale_cache_for_payment_service` | 변수값 포함 금지 | `circuit_breaker.stale_cache_served` + `service_name=` kwarg |
| `cb_state_change` | 축약/두문자어 금지 | `circuit_breaker.state_changed` |
| `CircuitBreaker.StateChanged` | PascalCase 금지 | `circuit_breaker.state_changed` |
| `error_happened` | 모호한 action 금지 | `circuit_breaker.callback_failed` |

### 2.4 컴포넌트별 이벤트 매핑 (주요 20개)

현재 코드의 실제 로그 호출을 기반으로 매핑한 이벤트 카탈로그:

#### circuit_breaker (services/circuit_breaker/)

| 현재 로그 메시지 | structlog 이벤트 |
|---|---|
| `[CircuitBreaker] Invalid state for callback: {state}` | `circuit_breaker.invalid_callback_state` |
| `[CircuitBreaker] Sync callback failed for '{new_state}': {e}` | `circuit_breaker.sync_callback_failed` |
| `[CircuitBreaker] Serving stale cache for '{service_name}'` | `circuit_breaker.stale_cache_served` |
| `[CircuitBreaker] Queued request to DLQ for '{service_name}'` | `circuit_breaker.request_queued_to_dlq` |
| `[CircuitBreaker] Returning default response for '{service_name}'` | `circuit_breaker.default_response_returned` |

#### cell_registry (services/cell_topology/)

| 현재 로그 메시지 | structlog 이벤트 |
|---|---|
| `Cell not found: {cell_id}` | `cell_registry.cell_not_found` |
| `Cell state changed: {cell_id} {old} → {new} ({reason})` | `cell_registry.state_changed` |
| `Service heartbeat recording failed: {e}` | `cell_registry.heartbeat_failed` |
| `Evicted {n} expired services from {cell_id}` | `cell_registry.services_evicted` |
| `Registered {n} Cell Bulkheads` | `cell_registry.bulkheads_registered` |
| `Cell {cell_id}: ISOLATED — {n} services redistributed` | `cell_evacuation.cell_isolated` |
| `Cell {cell_id}: restored to ACTIVE` | `cell_evacuation.cell_restored` |

#### adaptive_throttle (services/throttle/adaptive/)

| 현재 로그 메시지 | structlog 이벤트 |
|---|---|
| `[AdaptiveThrottle] Governance blocked limit adjustment` | `adaptive_throttle.governance_blocked` |
| `[AdaptiveThrottle] Unknown event type: {name}` | `adaptive_throttle.unknown_event_type` |
| `[AdaptiveThrottle] Published {event_type} event` | `adaptive_throttle.event_published` |
| `[AdaptiveThrottle] Failed to record metrics: {e}` | `adaptive_throttle.metrics_failed` |

#### watchdog (meta/watchdog.py)

| 현재 로그 메시지 | structlog 이벤트 |
|---|---|
| `[SelfHealerWatchdog] {name} unhealthy, attempting recovery` | `watchdog.unhealthy_detected` |
| `[SelfHealerWatchdog] Dry-run: would attempt recovery for {name}` | `watchdog.dry_run_recovery` |
| `[SelfHealerWatchdog] check_health error: {e}` | `watchdog.health_check_failed` |
| `[SelfHealerWatchdog] Recovery cooldown active for {component}` | `watchdog.recovery_cooldown_active` |
| `[SelfHealerWatchdog] Recovery failed for {component}: {e}` | `watchdog.recovery_failed` |

#### resilient_storage (adapters/resilient/backend.py)

| 현재 로그 메시지 | structlog 이벤트 |
|---|---|
| `[ResilientStorage] Redis connected successfully` | `resilient_storage.redis_connected` |
| `[ResilientStorage] Redis init failed: {e}` | `resilient_storage.redis_init_failed` |
| `[ResilientStorage] Operating in DEGRADED mode` | `resilient_storage.degraded_mode_entered` |
| `[ResilientStorage] WAL recovery error: {e}` | `resilient_storage.wal_recovery_failed` |
| `[ResilientStorage] Recovered to REDIS mode` | `resilient_storage.redis_mode_recovered` |

#### escalation (meta/escalation.py)

| 현재 로그 메시지 | structlog 이벤트 |
|---|---|
| `[EscalationManager] Dry-run mode - would escalate` | `escalation.dry_run_escalation` |
| `[EscalationManager] Component {component} in maintenance` | `escalation.maintenance_skipped` |
| `[EscalationManager] Cooldown active for {component}` | `escalation.cooldown_active` |
| `[EscalationManager] Escalated: {component} - {title}` | `escalation.escalated` |
| `[EscalationManager] PagerDuty sent: {title}` | `escalation.pagerduty_sent` |
| `[EscalationManager] Slack sent: {title}` | `escalation.slack_sent` |

---

## 3. structlog 아키텍처 설계

### 3.1 stdlib 호환 모드 (Foreign Code 통합)

structlog를 stdlib `logging`의 **wrapper로** 설정하여, 기존 stdlib 기반 인프라를 그대로 유지한다.

```
┌──────────────────────────────────────────────────────┐
│  Application Code                                     │
│  logger = structlog.get_logger()                      │
│  logger.info("circuit_breaker.state_changed",         │
│              service_name="payment", new_state="open")│
└───────────────────┬──────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  structlog Processor Pipeline                         │
│  1. structlog.contextvars.merge_contextvars           │
│  2. structlog.stdlib.add_log_level                    │
│  3. structlog.stdlib.add_logger_name                  │
│  4. structlog.processors.TimeStamper(fmt="iso")       │
│  5. _inject_otel_trace_context  (커스텀)               │
│  6. structlog.processors.StackInfoRenderer()          │
│  7. structlog.processors.format_exc_info              │
│  8. Renderer (JSON / Console — 환경별)                 │
└───────────────────┬──────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  stdlib logging (최종 출력)                             │
│  ┌────────────────────────────────────────────┐      │
│  │ logging.StreamHandler → 콘솔 출력           │      │
│  │ IncidentLogHandler → Postmortem 버퍼 (기존) │      │
│  │ OTEL LoggingInstrumentor → Loki 전송 (기존) │      │
│  └────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────┘
```

**핵심**: structlog는 `structlog.stdlib.ProcessorFormatter`를 통해 최종적으로 stdlib `logging.LogRecord`를 생성하므로, 기존의 다음 컴포넌트가 **코드 변경 없이** 작동한다:

| 기존 컴포넌트 | 위치 | 작동 이유 |
|---|---|---|
| `IncidentLogHandler` | `services/postmortem/log_buffer.py:242` | `logging.Handler` 서브클래스 — stdlib LogRecord를 수신 |
| OTEL `LoggingInstrumentor` | `observability/__init__.py:525-535` | stdlib `logging`을 계측 — structlog wrapper 아래에서 정상 작동 |
| `LoggingSettings` | `settings/logging_config.py` | 로그 레벨 설정 — stdlib logger에 적용 |
| Django/Celery 내부 로깅 | 서드파티 | `structlog.stdlib.ProcessorFormatter`가 외부 stdlib 로그도 파이프라인에 통합 |

### 3.2 초기화 설정 코드

`settings/structlog_config.py` (신규 파일):

```python
"""
structlog Configuration — stdlib 호환 모드.

structlog를 stdlib logging wrapper로 설정하여:
- 기존 OTEL LoggingInstrumentor 유지
- 기존 IncidentLogHandler 유지
- 기존 LoggingSettings 레벨 설정 유지

환경별 Renderer:
- production (structured_json=True): JSONRenderer → Loki/Datadog 파싱 최적
- development (structured_json=False): ConsoleRenderer → 터미널 가독성 최적
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

import structlog

_otel_injection_in_progress = threading.local()


def configure_structlog() -> None:
    """structlog 전역 설정 초기화."""
    from selfhealing.settings.logging_config import get_logging_settings

    settings = get_logging_settings()

    # 환경별 Renderer 선택
    if settings.structured_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # 공유 프로세서 (structlog + stdlib 공통)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _inject_otel_trace_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # structlog 설정
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # stdlib logging 설정 — structlog ProcessorFormatter 적용
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [
        h
        for h in root_logger.handlers
        if not isinstance(
            getattr(h, "formatter", None),
            structlog.stdlib.ProcessorFormatter,
        )
    ]
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)


def _inject_otel_trace_context(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """OTEL trace context를 로그에 자동 주입하는 프로세서."""
    if getattr(_otel_injection_in_progress, "active", False):
        return event_dict

    _otel_injection_in_progress.active = True
    try:
        from selfhealing.observability import (
            get_current_span_id_from_otel,
            get_current_trace_id_from_otel,
        )

        trace_id = get_current_trace_id_from_otel()
        span_id = get_current_span_id_from_otel()

        if trace_id:
            event_dict["trace_id"] = trace_id
        if span_id:
            event_dict["span_id"] = span_id
    except ImportError:
        pass
    finally:
        _otel_injection_in_progress.active = False

    return event_dict
```

### 3.3 `structured_json` 설정 연동

현재 `LoggingSettings`에 이미 존재하는 필드 (`settings/logging_config.py:107`):

```python
structured_json: bool = Field(
    default=True,
    description="Use structured JSON format for logs",
)
```

| `structured_json` 값 | Renderer | 출력 예시 |
|---|---|---|
| `True` (production) | `JSONRenderer` | `{"event": "circuit_breaker.state_changed", "service_name": "payment", "new_state": "open", "timestamp": "2026-02-22T10:00:00Z", "level": "info", "trace_id": "abc123"}` |
| `False` (development) | `ConsoleRenderer` | `2026-02-22 10:00:00 [info] circuit_breaker.state_changed service_name=payment new_state=open` |

### 3.4 기존 OTEL 연동 아키텍처 보존

현재 `observability/__init__.py`의 `instrument_logging()` (line 501-543)은 `LoggingInstrumentor().instrument()`를 호출하여 stdlib `logging`에 OTEL handler를 추가한다.

structlog가 stdlib wrapper 모드이므로:

```
structlog.info("event", key=val)
    → stdlib logging.info(formatted_message)
        → OTEL LoggingInstrumentor가 LogRecord 가로챔
            → OTLP LogExporter → OTEL Collector → Loki
```

**변경 불필요**. `instrument_logging()` 코드를 수정할 필요 없다.

### 3.5 IncidentLogHandler 호환성

`services/postmortem/log_buffer.py:242`의 `IncidentLogHandler(logging.Handler)`는 `emit(record: logging.LogRecord)` 메서드에서 `record.created`, `record.levelname`, `record.name` 등을 사용한다.

structlog stdlib wrapper 모드에서는 이 모든 필드가 정상적으로 `LogRecord`에 포함되므로 **변경 불필요**.

추가 이점: structlog의 `event_dict`에 포함된 구조화된 키-값 쌍이 `LogRecord`의 `extra` 필드를 통해 `IncidentLogHandler`에도 전달되어, Postmortem 타임라인에 더 풍부한 컨텍스트를 제공할 수 있다.

---

## 4. 변환 패턴 카탈로그

### 4.1 모듈 헤더 변환

**모든 722개 모듈**에 동일하게 적용:

```python
# Before (722/722 모듈 동일 패턴)
import logging
logger = logging.getLogger(__name__)

# After
import structlog
logger = structlog.get_logger()
```

### 4.2 f-string 로깅 → structlog 키워드 인자 (2,753건)

#### 패턴 A: 단순 변수 삽입

```python
# Before — services/cell_topology/registry.py:184
logger.warning(f"Cell not found: {cell_id}")

# After
logger.warning("cell_registry.cell_not_found", cell_id=cell_id)
```

#### 패턴 B: 다중 변수 + 계산식

```python
# Before — services/cell_topology/registry.py:282
logger.info(f"Evicted {len(evicted)} expired services from {cell_id}: {evicted}")

# After
logger.info(
    "cell_registry.services_evicted",
    cell_id=cell_id,
    count=len(evicted),
    services=evicted,
)
```

#### 패턴 C: 상태 전이

```python
# Before — services/cell_topology/registry.py:195
logger.info(f"Cell state changed: {cell_id} " f"{old_state.value} → {state.value} ({reason})")

# After
logger.info(
    "cell_registry.state_changed",
    cell_id=cell_id,
    old_state=old_state.value,
    new_state=state.value,
    reason=reason,
)
```

#### 패턴 D: Exception 캡처

```python
# Before — meta/watchdog.py:327
logger.error(f"[SelfHealerWatchdog] Recovery failed for {component}: {e}")

# After
logger.error(
    "watchdog.recovery_failed",
    component=component,
    error=str(e),
)
```

#### 패턴 E: `logger.exception` (29건)

```python
# Before — tasks/base.py:113
logger.exception(f"[BaseNotifyingTask] Task {self.name} failed: {e}")

# After — structlog은 exc_info를 자동 처리
logger.exception(
    "celery_task.execution_failed",
    task_name=self.name,
    error=str(e),
)
```

### 4.3 %s-style 로깅 → structlog 키워드 인자 (58건)

```python
# Before — adapters/resilient/backend.py:176
logger.warning("[ResilientStorage] Redis init failed: %s", err_msg)

# After
logger.warning("resilient_storage.redis_init_failed", error=err_msg)
```

```python
# Before — services/cell_topology/policy.py:346
logger.info("  Service '%s': %s -> %s", svc, cell_id, new_cell)

# After
logger.info(
    "cell_evacuation.service_redistributed",
    service=svc,
    from_cell=cell_id,
    to_cell=new_cell,
)
```

### 4.4 `extra={}` 패턴 전환 (metrics/event_handlers.py)

```python
# Before — metrics/event_handlers.py:72
logger.log(level, message, extra=extra)

# After — structlog은 키워드 인자가 곧 extra
logger.log(level, message, **extra)
```

### 4.5 Context Binding (수동 태그 제거)

현재 566개 고유 `[PrefixTag]`를 `bind()` 또는 모듈 레벨 바인딩으로 대체:

```python
# Before — meta/watchdog.py (36회 반복되는 "[SelfHealerWatchdog]" 접두사)
logger.warning(f"[SelfHealerWatchdog] {name} unhealthy, attempting recovery")
logger.info(f"[SelfHealerWatchdog] Dry-run: would attempt recovery for {name}")
logger.error(f"[SelfHealerWatchdog] check_health error: {e}")

# After — 모듈 레벨 bind로 component 자동 주입
logger = structlog.get_logger().bind(component="watchdog")

logger.warning("watchdog.unhealthy_detected", name=name)
logger.info("watchdog.dry_run_recovery", name=name)
logger.error("watchdog.health_check_failed", error=str(e))
```

클래스 레벨에서도 가능:

```python
class CellRegistry:
    def __init__(self):
        self._log = structlog.get_logger().bind(component="cell_registry")

    def set_cell_state(self, cell_id, state, reason):
        self._log.info(
            "cell_registry.state_changed",
            cell_id=cell_id,
            new_state=state.value,
            reason=reason,
        )
```

### 4.6 동적 로그 레벨 패턴 보존

현재 `metrics/event_handlers.py`의 `_log_event()`은 `EventLoggingConfig`에서 런타임 로그 레벨을 조회한다:

```python
# Before
def _log_event(level_getter: str, message: str, **extra) -> None:
    config = _get_logging_config()
    level_name = getattr(config, level_getter)()
    level = config.get_log_level_int(level_name)
    logger.log(level, message, extra=extra)

# After — structlog.stdlib.BoundLogger도 .log() 지원
def _log_event(level_getter: str, event: str, **kw) -> None:
    config = _get_logging_config()
    level_name = getattr(config, level_getter)()
    level = config.get_log_level_int(level_name)
    logger.log(level, event, **kw)
```

### 4.7 Critical/DEGRADED 모드 로깅

```python
# Before — adapters/resilient/backend.py:188
logger.critical(
    "[ResilientStorage] Redis unavailable. "
    "Operating in DEGRADED mode with Memory + WAL."
)

# After
logger.critical(
    "resilient_storage.degraded_mode_entered",
    reason="redis_unavailable",
    fallback="memory_wal",
)
```

---

## 5. 테스트 영향 분석

### 5.1 현재 테스트 로깅 패턴 (229건)

| 패턴 | 건수 | structlog 전환 영향 |
|---|---|---|
| `caplog` (pytest built-in) | 다수 | stdlib 호환 모드에서 **그대로 작동** |
| `patch("selfhealing.xxx.logger")` | 다수 | `structlog.get_logger()` mock으로 변경 필요 |
| `mock_logger.warning.assert_called()` | 다수 | 동일 API — structlog `BoundLogger`도 `.warning()` 지원 |

### 5.2 caplog 호환성

structlog stdlib 호환 모드에서는 `caplog`이 정상 작동한다. 이유: structlog가 최종적으로 stdlib `logging.Logger.info()` 등을 호출하므로 pytest의 `caplog` handler가 이를 캡처한다.

```python
# 기존 테스트 — 변경 불필요
def test_cell_not_found(caplog):
    registry = CellRegistry()
    with caplog.at_level(logging.WARNING):
        registry.set_cell_state("nonexistent", CellState.ACTIVE, "test")
    assert "cell_registry.cell_not_found" in caplog.text
```

### 5.3 mock 패턴 변경

```python
# Before
with patch("selfhealing.meta.watchdog.logger") as mock_logger:
    watchdog.check_health()
    mock_logger.error.assert_called_once()

# After — structlog.get_logger() 반환값 mock
with patch("selfhealing.meta.watchdog.logger") as mock_logger:
    watchdog.check_health()
    mock_logger.error.assert_called_once()
# → structlog의 BoundLogger도 .error() 메서드를 가지므로 동일하게 작동
```

실질적으로 **대부분의 mock 테스트는 변경 불필요**. `structlog.stdlib.BoundLogger`가 stdlib `logging.Logger`와 동일한 메서드 시그니처를 제공하기 때문이다.

---

## 6. 의존성 변경

### 6.1 pyproject.toml 수정

```toml
# packages/selfhealing-python/pyproject.toml
[project]
dependencies = [
    # ... 기존 의존성 ...
    "structlog>=24.1.0",  # 추가
]
```

- structlog는 **순수 Python** 패키지, 추가 C 의존성 없음
- 크기: ~100KB
- 최소 Python 버전: 3.8 (현재 프로젝트 3.10 이상이므로 호환)

### 6.2 ruff 설정 변경

structlog 전환 완료 후, `[Tag]` 수동 접두사와 f-string 로깅을 방지하는 린트 규칙 추가:

```toml
# packages/selfhealing-python/pyproject.toml
[tool.ruff]
select = [
    "E", "W", "F", "I", "B", "C4", "UP",
    "G",   # flake8-logging-format — f-string 로깅 금지
    "LOG", # flake8-logging — logging 직접 사용 감지
]
```

---

## 7. 마이그레이션 실행 계획

### 7.1 Phase 1: 인프라 준비 (변환 전)

| 작업 | 대상 파일 | 설명 |
|---|---|---|
| `structlog` 의존성 추가 | `pyproject.toml` | `dependencies`에 `structlog>=24.1.0` 추가 |
| structlog 설정 모듈 생성 | `settings/structlog_config.py` (신규) | §3.2의 `configure_structlog()` 구현 |
| 앱 진입점에서 호출 | `__init__.py` 또는 Django `AppConfig.ready()` | `configure_structlog()` 호출 |

### 7.2 Phase 2: 일괄 자동 변환 ✅ 완료 (2026-02-22)

`scripts/migrate_to_structlog.py`로 720개 모듈의 기계적 변환을 완료했다.

**실제 적용 결과**: 720개 파일 변경, import 720개, f-string 2,624건, %s 59건, plain 776건 (총 3,459건), SyntaxError 0건

**단위 테스트**: `packages/selfhealing-python/tests/unit/test_migrate_to_structlog.py` 66개 전부 통과

**의도적 보존 파일 (변환 제외)**:
- `settings/structlog_config.py`: structlog 직접 설정 파일
- `services/postmortem/log_buffer.py`: IncidentLogHandler stdlib 호환성 (§3.5)
- `interfaces/notification.py`, `audit/self_audit.py`: stdlib logging 레벨 상수 직접 사용 (`import logging` 재추가)
- `core/connection_health.py`, `core/pool_monitor.py`: 인라인 import logging 패턴

원래 문서의 변환 계획:

**변환 단계**:

1. **import 문 교체** (722개 모듈):
   - `import logging` → `import structlog`
   - `logger = logging.getLogger(__name__)` → `logger = structlog.get_logger()`

2. **`[PrefixTag]` 추출 및 bind 변환** (566개 고유 태그):
   - 모듈 내에서 단일 태그만 사용하는 경우: 모듈 레벨 `logger = structlog.get_logger().bind(component="tag")`
   - 복수 태그 사용 모듈: 클래스/함수 레벨 bind

3. **f-string → 키워드 인자 변환** (2,753건):
   - 정규식 + AST 조합으로 `f"..."` 내의 `{expr}`를 키워드 인자로 추출
   - `event` 이름은 §2.4 이벤트 카탈로그 기반 매핑

4. **%s-style → 키워드 인자 변환** (58건):
   - `"msg %s", arg` → `"event_name", key=arg`

5. **`logger.exception` 변환** (29건):
   - f-string 제거 + 키워드 인자 변환 (exc_info는 structlog이 자동 처리)

### 7.3 Phase 3: 수동 검수

자동 변환 후 수동 검수가 필요한 영역:

| 영역 | 건수 | 이유 |
|---|---|---|
| 이벤트 이름 최종 확정 | ~200개 고유 이벤트 | 자동 생성된 이벤트 이름의 의미 적절성 검증 |
| multi-line 로거 호출 | ~30건 | 자동 변환기가 multi-line을 안전하게 처리했는지 확인 |
| `self._logger` 패턴 | `audit/self_audit.py` 등 | 인스턴스 로거 패턴 별도 처리 |
| 동적 로그 레벨 | `metrics/event_handlers.py` | `_log_event()` 함수의 `logger.log()` 호출 검증 |
| `logging.Handler` 서브클래스 | `services/postmortem/log_buffer.py` | `IncidentLogHandler`의 stdlib 호환성 재확인 |

#### Phase 3 검수 결과 (완료: 2026-02-22)

| 처리 항목 | 파일 | 내용 |
|---|---|---|
| `import logging` 복구 | `services/postmortem/log_buffer.py` | `IncidentLogHandler`가 `logging.Handler` 서브클래스이므로 stdlib import 필수 재추가 |
| `self._logger` 전환 | `audit/self_audit.py` | `logging.getLogger("audit.self")` → `structlog.get_logger().bind(component="self_audit")`  |
| `LoggingNotificationAdapter` 전환 | `interfaces/notification.py` | `import logging` 제거, `_SEVERITY_TO_LOG_METHOD` 매핑 + structlog 사용 |
| 인라인 logging 제거 (6건) | `core/connection_health.py` | 모듈 레벨 `logger = structlog.get_logger().bind(component="connection_health_monitor")` 추가 |
| 인라인 logging 제거 (3건) | `core/pool_monitor.py` | 모듈 레벨 `logger = structlog.get_logger().bind(component="pool_monitor")` 추가 |
| 고아 import 제거 | `audit/persistence/config.py` | 함수 내 `import logging` + 중복 `logger` 선언 제거, 모듈 레벨로 정리 |
| `[Tag]` 이벤트 이름 변환 | `adapters/celery/tasks/`, `celery_tasks/`, `services/throttle/` | 18건 `"{component}.{action}"` 형식으로 변환 |
| dot-less 이벤트 이름 변환 | 30+ 파일 | 60건 `opentelemetry_*`, `shutdown_*` 등 → `otel.*`, `shutdown.*` 형식 |
| `adapters/celery/signal_hooks.py` | — | 3건 dot-less/f-string 이벤트 이름 변환 |
| 한글 이벤트 이름 수동 수정 | `api/django/views/grafana_webhook.py` | `grafana_webhook_alert_목록` → `grafana_webhook.no_alerts` |

**단위 테스트**: `tests/unit/audit/test_self_audit_structlog.py` (10개), `tests/unit/test_phase3_structlog_migration.py` (17개) — **27개 전체 통과**

### 7.4 Phase 4: 테스트 및 린트

| 작업 | 설명 |
|---|---|
| 전체 테스트 실행 | `pytest` — caplog 기반 테스트 정상 작동 확인 |
| mock 패턴 수정 | `patch("...logger")` 테스트 중 실패 건 수정 |
| ruff `G` + `LOG` 규칙 활성화 | 새로운 f-string 로깅/직접 logging 사용 방지 |
| OTEL 연동 테스트 | `instrument_logging()` → Loki 로그 수신 확인 |

### 7.5 Phase 5: 정리

| 작업 | 설명 |
|---|---|
| `import logging` 잔여 제거 | structlog 전환 후 불필요한 `import logging` 정리 |
| 이벤트 카탈로그 문서화 | 최종 확정된 이벤트 이름 목록을 별도 문서로 관리 |
| `LoggingSettings` 확장 | structlog 전용 설정 필드 추가 (필요시) |

---

## 8. JSON 출력 예시 (Loki/Datadog 수신 형태)

### 8.1 정상 운영 로그

```json
{
  "event": "cell_registry.state_changed",
  "level": "info",
  "logger": "selfhealing.services.cell_topology.registry",
  "timestamp": "2026-02-22T10:30:00.123456Z",
  "component": "cell_registry",
  "cell_id": "cell-ap-northeast-2-a",
  "old_state": "ACTIVE",
  "new_state": "DRAINING",
  "reason": "health_score_below_threshold",
  "trace_id": "a1b2c3d4e5f6789012345678abcdef01",
  "span_id": "1234567890abcdef"
}
```

### 8.2 에러 로그

```json
{
  "event": "watchdog.recovery_failed",
  "level": "error",
  "logger": "selfhealing.meta.watchdog",
  "timestamp": "2026-02-22T10:30:05.456789Z",
  "component": "watchdog",
  "target_component": "circuit_breaker",
  "error": "ConnectionRefusedError: Redis connection refused",
  "consecutive_failures": 3,
  "trace_id": "a1b2c3d4e5f6789012345678abcdef01"
}
```

### 8.3 Critical 로그

```json
{
  "event": "resilient_storage.degraded_mode_entered",
  "level": "critical",
  "logger": "selfhealing.adapters.resilient.backend",
  "timestamp": "2026-02-22T10:30:10.789012Z",
  "component": "resilient_storage",
  "reason": "redis_unavailable",
  "fallback": "memory_wal"
}
```

### 8.4 Grafana Loki 쿼리 예시

```logql
# structlog 전환 전 — 정규식 필수
{service="selfhealing"} |~ "\\[CircuitBreaker\\].*state changed"

# structlog 전환 후 — 구조화된 필터
{service="selfhealing"} | json | event="circuit_breaker.state_changed"
{service="selfhealing"} | json | component="watchdog" | level="error"
{service="selfhealing"} | json | cell_id="cell-ap-northeast-2-a"
```

---

## 9. Observability 시스템별 이점 요약

| 시스템 | 전환 전 | 전환 후 |
|---|---|---|
| **Grafana Loki** | 정규식 파싱 필수, `[Tag]` 수동 검색 | JSON 필드 자동 인덱싱, `event=`, `component=`, `cell_id=` 직접 필터 |
| **OTEL Collector** | f-string → `LogRecord.msg`에 평문, `args=None` | 구조화 필드가 `LogRecord.extra` → OTEL `attributes` 자동 매핑 |
| **Datadog** | 로그 파이프라인에서 grok 파서 필요 | JSON 자동 파싱, `@event`, `@component` 필드 즉시 사용 |
| **Prometheus** | 직접 연결 없음 | structlog 프로세서에서 특정 이벤트 → Counter 증가 가능 |
| **Sentry** | f-string → 매번 새 이슈 (grouping 실패) | `event` 이름 기반 자동 그룹핑 |
| **Mimir** | 메트릭 스토리지, 로그 무관 | 구조화 로그 → LogQL metric 쿼리로 대시보드 생성 가능 |
| **Tempo** | trace_id 수동 추출 필요 | `trace_id` 필드 자동 주입 → logs-to-traces 상관관계 자동 |

---

## 10. 리스크 및 완화 전략

| 리스크 | 영향도 | 완화 전략 |
|---|---|---|
| 4,810건 일괄 변환 시 회귀 버그 | 높음 | AST 기반 자동 변환 + 전체 테스트 스위트 실행 |
| 이벤트 이름 cardinality 폭발 | 중간 | §2.3 네이밍 규칙 준수, 변수값 포함 금지 |
| 서드파티 라이브러리(Django, Celery) 로그 통합 | 낮음 | `foreign_pre_chain`으로 외부 stdlib 로그도 structlog 파이프라인 통과 |
| `caplog` 테스트 실패 | 낮음 | stdlib 호환 모드에서 caplog 정상 작동 확인됨 |
| 성능 오버헤드 | 낮음 | structlog 프로세서 체인 오버헤드 < f-string eager evaluation 절감분. 순이익 |
