# 76. Cascade Event Audit (연계 이벤트 감사 추적)

> **Version**: 1.7.0  
> **Created**: 2026-01-21  
> **Updated**: 2026-01-23  
> **Status**: Phase 1,2,3,4,5,6,7 Implemented  
> **Parent**: [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md)

## 1. 개요

### 1.1 문제점 (AS-IS)

현재 Audit 시스템은 **개별 이벤트**만 기록합니다:

```
Audit Log (현재):
├── [15:30:00] Emergency LEVEL_3 activated
├── [15:30:01] Governance mode → STRICT
├── [15:30:02] Canary rollout-123 rolled back
└── [15:30:03] Error budget multiplier set to 5x
```

**문제**:
- 이벤트 간 **인과관계**가 기록되지 않음
- "왜 Canary가 롤백되었는가?"에 대한 추적 어려움
- 연계 액션의 **전체 흐름**을 파악하기 어려움

### 1.2 해결책 (TO-BE)

**CascadeEvent** 도입으로 인과관계 묶음 기록:

```
Cascade Event (새로운):
├── Cascade ID: cascade-evt-abc123
├── Trigger: LEVEL_3 Detected (evt-001)
├── Causation Chain: [evt-001] → [evt-002] → [evt-003] → [evt-004]
└── Effects:
    ├── [evt-002] GOVERNANCE_STRICT (caused by evt-001)
    ├── [evt-003] CANARY_ROLLBACK (caused by evt-002)
    └── [evt-004] BUDGET_MULTIPLIER (caused by evt-001)
```

---

## 2. 아키텍처

### 2.1 Cascade Event 구조

```
┌─────────────────────────────────────────────────────────────────────┐
│                       CascadeEvent Structure                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                      CascadeEvent                             │  │
│  │                                                               │  │
│  │  id: "cascade-evt-abc123"                                    │  │
│  │  timestamp: "2026-01-21T15:30:00Z"                           │  │
│  │  namespace: "seoul"                                          │  │
│  │                                                               │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │ Trigger                                                 │  │  │
│  │  │                                                         │  │  │
│  │  │ type: "EMERGENCY_LEVEL_CHANGED"                        │  │  │
│  │  │ event_id: "evt-001"                                    │  │  │
│  │  │ details: {                                             │  │  │
│  │  │   "old_level": "NORMAL",                               │  │  │
│  │  │   "new_level": "LEVEL_3",                              │  │  │
│  │  │   "activated_by": "system"                             │  │  │
│  │  │ }                                                       │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │ Effects (연쇄 결과)                                     │  │  │
│  │  │                                                         │  │  │
│  │  │ ┌──────────────────────────────────────────────────┐   │  │  │
│  │  │ │ Effect 1: GOVERNANCE_STRICT                      │   │  │  │
│  │  │ │ event_id: "evt-002"                              │   │  │  │
│  │  │ │ caused_by: "evt-001"                             │   │  │  │
│  │  │ │ success: true                                    │   │  │  │
│  │  │ └──────────────────────────────────────────────────┘   │  │  │
│  │  │                                                         │  │  │
│  │  │ ┌──────────────────────────────────────────────────┐   │  │  │
│  │  │ │ Effect 2: CANARY_ROLLBACK                        │   │  │  │
│  │  │ │ event_id: "evt-003"                              │   │  │  │
│  │  │ │ caused_by: "evt-002"                             │   │  │  │
│  │  │ │ success: true                                    │   │  │  │
│  │  │ │ details: {"rollouts": ["rollout-123"]}           │   │  │  │
│  │  │ └──────────────────────────────────────────────────┘   │  │  │
│  │  │                                                         │  │  │
│  │  │ ┌──────────────────────────────────────────────────┐   │  │  │
│  │  │ │ Effect 3: BUDGET_MULTIPLIER                      │   │  │  │
│  │  │ │ event_id: "evt-004"                              │   │  │  │
│  │  │ │ caused_by: "evt-001"                             │   │  │  │
│  │  │ │ success: true                                    │   │  │  │
│  │  │ │ details: {"multiplier": 5.0}                     │   │  │  │
│  │  │ └──────────────────────────────────────────────────┘   │  │  │
│  │  │                                                         │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │ Causation Chain                                         │  │  │
│  │  │                                                         │  │  │
│  │  │ evt-001 ──▶ evt-002 ──▶ evt-003                        │  │  │
│  │  │     │                                                   │  │  │
│  │  │     └────▶ evt-004                                     │  │  │
│  │  │                                                         │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │ Hash Chain (위변조 방지)                                │  │  │
│  │  │                                                         │  │  │
│  │  │ previous_hash: "abc123..."                             │  │  │
│  │  │ current_hash: "def456..."                              │  │  │
│  │  │ signature: "sig789..."                                 │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Hash Chain 연결

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Cascade Event Hash Chain                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐          │
│  │ Cascade #1  │     │ Cascade #2  │     │ Cascade #3  │          │
│  │             │     │             │     │             │          │
│  │ prev: null  │────▶│ prev: h1    │────▶│ prev: h2    │          │
│  │ hash: h1    │     │ hash: h2    │     │ hash: h3    │          │
│  │             │     │             │     │             │          │
│  │ LEVEL_3     │     │ RECOVERY    │     │ LEVEL_2     │          │
│  │ detected    │     │ started     │     │ detected    │          │
│  └─────────────┘     └─────────────┘     └─────────────┘          │
│                                                                      │
│  위변조 시도 시:                                                     │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐          │
│  │ Cascade #1  │  ✗  │ Cascade #2  │     │ Cascade #3  │          │
│  │             │  │  │ (modified)  │     │             │          │
│  │ prev: null  │  │  │ prev: h1    │──✗──│ prev: h2    │          │
│  │ hash: h1    │  │  │ hash: h2'   │     │ hash: h3    │          │
│  │             │  │  │    ↑       │     │     ↑      │          │
│  └─────────────┘  │  │ 변조됨!    │     │ 불일치!    │          │
│                   │  └─────────────┘     └─────────────┘          │
│                   │                                                 │
│                   └─── h2' ≠ h2 (체인 무결성 위반)                   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 보완 설계 (리뷰 반영)

### 3.1 분산 추적(Distributed Tracing) 표준 호환

> **리뷰 ①**: W3C Trace Context / OpenTelemetry와의 호환성 확보

#### 3.1.1 배경

외부 시스템(API Gateway, Microservices)에서 시작된 요청이 우리 시스템의 Emergency를 트리거했을 때,
**외부의 Trace ID와 내부의 Cascade ID를 연결**해야 진정한 E2E 추적이 가능합니다.

#### 3.1.2 필드 설계

```python
@dataclass
class ExternalTraceContext:
    """
    외부 분산 추적 컨텍스트.
    
    W3C Trace Context 및 OpenTelemetry 표준과 호환됩니다.
    
    네이밍 선택 이유:
    - `external_trace_id`: 기존 tracing.py의 `trace_id` 패턴과 일관성 유지
    - `external_` 접두사: 내부 cascade_id와 명확히 구분
    - 프로젝트 내 TracingConfig.captured_headers와 정렬
    
    Reference:
    - services/circuit_breaker/tracing.py#L35-52 (captured_headers 패턴)
    - W3C Trace Context: https://www.w3.org/TR/trace-context/
    """
    
    trace_id: Optional[str] = None
    """W3C traceparent의 trace-id (32 hex characters)."""
    
    span_id: Optional[str] = None
    """W3C traceparent의 parent-id (16 hex characters)."""
    
    trace_flags: Optional[str] = None
    """W3C traceparent의 trace-flags (예: "01" = sampled)."""
    
    baggage: Dict[str, str] = field(default_factory=dict)
    """W3C Baggage 헤더 값들."""
    
    # 벤더별 추가 ID
    aws_xray_trace_id: Optional[str] = None
    """AWS X-Ray trace ID (X-Amzn-Trace-Id)."""
    
    request_id: Optional[str] = None
    """X-Request-ID 헤더 값."""
    
    correlation_id: Optional[str] = None
    """X-Correlation-ID 헤더 값."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "trace_flags": self.trace_flags,
            "baggage": self.baggage,
            "aws_xray_trace_id": self.aws_xray_trace_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
        }
    
    @classmethod
    def from_headers(cls, headers: Dict[str, str]) -> "ExternalTraceContext":
        """HTTP 헤더에서 추출."""
        ctx = cls()
        
        # W3C traceparent: 00-{trace_id}-{span_id}-{flags}
        traceparent = headers.get("traceparent", "")
        if traceparent:
            parts = traceparent.split("-")
            if len(parts) >= 4:
                ctx.trace_id = parts[1]
                ctx.span_id = parts[2]
                ctx.trace_flags = parts[3]
        
        # 기타 헤더
        ctx.aws_xray_trace_id = headers.get("x-amzn-trace-id")
        ctx.request_id = headers.get("x-request-id")
        ctx.correlation_id = headers.get("x-correlation-id")
        
        return ctx
```

#### 3.1.3 CascadeEvent 확장

```python
@dataclass
class CascadeEvent:
    # ... 기존 필드 ...
    
    # 외부 추적 컨텍스트 (리뷰 ① 반영)
    external_trace: Optional[ExternalTraceContext] = None
    """외부 시스템 Trace Context (W3C/OpenTelemetry 호환)."""
```

#### 3.1.4 Trace Context Provider 연동

```python
# services/circuit_breaker/tracing.py의 기존 패턴 활용
def record_with_external_trace(
    self,
    trigger_type: str,
    trigger_details: Dict[str, Any],
    effects: List[Dict[str, Any]],
    namespace: str,
    request: Optional[Any] = None,  # Django HttpRequest
    triggered_by: Optional[str] = None,
) -> CascadeEvent:
    """외부 Trace Context를 포함하여 Cascade Event 기록."""
    
    external_trace = None
    if request:
        from selfhealing.services.circuit_breaker.tracing import TraceContextProvider
        provider = TraceContextProvider()
        trace_info = provider.extract_from_request(request)
        
        external_trace = ExternalTraceContext(
            trace_id=trace_info.trace_id,
            span_id=trace_info.span_id,
            request_id=trace_info.request_id,
            correlation_id=trace_info.correlation_id,
        )
    
    return self.record(
        trigger_type=trigger_type,
        trigger_details=trigger_details,
        effects=effects,
        namespace=namespace,
        triggered_by=triggered_by,
        external_trace=external_trace,
    )
```

---

### 3.2 비동기 경계(Async Boundary) 컨텍스트 전파

> **리뷰 ② + 아키텍트 리뷰 ③**: Celery/Kafka 메시지 경계에서 causation_id 자동 전파 (contextvars 사용)
>
> - 리뷰 ②: 비동기 경계에서 causation_id 전파 필요성
> - 아키텍트 리뷰 ③: Python `contextvars` 모듈 사용 (기존 `actor_context.py`, `domain_tag.py` 패턴 준수)

#### 3.2.1 배경

대부분의 연계 액션(Canary 롤백 등)은 **비동기로 처리**됩니다.
스레드 로컬(threading.local)은 데이터 유실 위험이 있으므로 `contextvars`를 사용해야 합니다.

#### 3.2.2 CausationContext (contextvars 기반)

```python
# packages/selfhealing-python/src/selfhealing/context/causation_context.py

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, Optional
import uuid


@dataclass
class CausationInfo:
    """
    인과관계 추적 정보.
    
    contextvars를 사용하여 스레드/async 안전을 보장합니다.
    
    Code reference:
        context/actor_context.py#L48 (_current_actor ContextVar 패턴)
    """
    
    cascade_id: str
    """현재 Cascade Event ID."""
    
    parent_event_id: str
    """부모 이벤트 ID (인과관계 체인)."""
    
    chain_depth: int = 0
    """현재 체인 깊이 (순환 참조 방지용)."""
    
    namespace: str = "global"
    """네임스페이스."""
    
    metadata: Dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""
    
    def to_dict(self) -> Dict[str, Any]:
        """직렬화 (Celery/Kafka 전송용)."""
        return {
            "cascade_id": self.cascade_id,
            "parent_event_id": self.parent_event_id,
            "chain_depth": self.chain_depth,
            "namespace": self.namespace,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CausationInfo":
        """역직렬화 (수신 측 복원용)."""
        return cls(
            cascade_id=data.get("cascade_id", ""),
            parent_event_id=data.get("parent_event_id", ""),
            chain_depth=data.get("chain_depth", 0),
            namespace=data.get("namespace", "global"),
            metadata=data.get("metadata", {}),
        )


# ContextVar 선언 (actor_context.py 패턴 준수)
_current_causation: ContextVar[Optional[CausationInfo]] = ContextVar(
    "current_causation", default=None
)


class CausationContext:
    """
    인과관계 컨텍스트 관리자.
    
    Usage:
        # 새 Cascade 시작
        with CausationContext.start_cascade(namespace="seoul") as ctx:
            # ctx.cascade_id 사용 가능
            do_work()
        
        # 기존 Cascade 계속
        with CausationContext.continue_cascade(causation_info):
            do_work()
    
    Code reference:
        context/actor_context.py (ActorContext 패턴)
    """
    
    @classmethod
    @contextmanager
    def start_cascade(
        cls,
        namespace: str = "global",
        trigger_event_id: Optional[str] = None,
    ) -> Generator[CausationInfo, None, None]:
        """새 Cascade 시작."""
        cascade_id = f"cascade-{uuid.uuid4().hex[:12]}"
        event_id = trigger_event_id or f"evt-{uuid.uuid4().hex[:8]}"
        
        info = CausationInfo(
            cascade_id=cascade_id,
            parent_event_id=event_id,
            chain_depth=0,
            namespace=namespace,
        )
        
        token = _current_causation.set(info)
        try:
            yield info
        finally:
            _current_causation.reset(token)
    
    @classmethod
    @contextmanager
    def continue_cascade(
        cls,
        info: CausationInfo,
    ) -> Generator[CausationInfo, None, None]:
        """기존 Cascade 계속 (비동기 경계 복원)."""
        # 체인 깊이 증가
        continued_info = CausationInfo(
            cascade_id=info.cascade_id,
            parent_event_id=info.parent_event_id,
            chain_depth=info.chain_depth + 1,
            namespace=info.namespace,
            metadata=info.metadata,
        )
        
        token = _current_causation.set(continued_info)
        try:
            yield continued_info
        finally:
            _current_causation.reset(token)
    
    @classmethod
    def get_current(cls) -> Optional[CausationInfo]:
        """현재 컨텍스트 조회."""
        return _current_causation.get()
```

#### 3.2.3 Celery 컨텍스트 전파 헤더 규격

```python
# 메시지 헤더 상수
CELERY_HEADER_CASCADE_ID = "x-selfhealing-cascade-id"
CELERY_HEADER_PARENT_EVENT = "x-selfhealing-parent-event"
CELERY_HEADER_CHAIN_DEPTH = "x-selfhealing-chain-depth"
CELERY_HEADER_NAMESPACE = "x-selfhealing-namespace"

# Kafka 헤더 (동일 구조)
KAFKA_HEADER_PREFIX = "selfhealing."


def get_causation_for_celery() -> Dict[str, str]:
    """
    Celery Task 호출 시 전달할 causation 헤더 생성.
    
    Usage:
        my_task.apply_async(
            args=[...],
            headers=get_causation_for_celery(),
        )
    
    Code reference:
        context/actor_context.py (get_actor_for_celery 패턴)
    """
    info = CausationContext.get_current()
    if not info:
        return {}
    
    return {
        CELERY_HEADER_CASCADE_ID: info.cascade_id,
        CELERY_HEADER_PARENT_EVENT: info.parent_event_id,
        CELERY_HEADER_CHAIN_DEPTH: str(info.chain_depth),
        CELERY_HEADER_NAMESPACE: info.namespace,
    }


@contextmanager
def restore_causation_from_celery(
    headers: Dict[str, str],
) -> Generator[Optional[CausationInfo], None, None]:
    """
    Celery Task에서 causation 복원.
    
    Usage:
        @shared_task(bind=True)
        def my_task(self, ...):
            with restore_causation_from_celery(self.request.headers or {}):
                do_work()
    
    Code reference:
        context/actor_context.py (restore_actor_from_celery 패턴)
    """
    cascade_id = headers.get(CELERY_HEADER_CASCADE_ID)
    
    if not cascade_id:
        yield None
        return
    
    info = CausationInfo(
        cascade_id=cascade_id,
        parent_event_id=headers.get(CELERY_HEADER_PARENT_EVENT, ""),
        chain_depth=int(headers.get(CELERY_HEADER_CHAIN_DEPTH, "0")),
        namespace=headers.get(CELERY_HEADER_NAMESPACE, "global"),
    )
    
    with CausationContext.continue_cascade(info) as ctx:
        yield ctx
```

#### 3.2.4 Context 전파의 원자성 (시작 시점 복사 강제)

> **추가 리뷰 ⑦**: 비동기 태스크 시작 시점에 컨텍스트 복사 강제

**배경**: Celery 태스크가 실행될 때, 호출 시점의 컨텍스트가 **복사(Copy)**되어야
'부모-자식' 관계가 명확하게 유지됩니다.

**기존 패턴 참조:**
```python
# context/actor_context.py#L410-435
def get_actor_for_celery() -> dict[str, Any]:
    """Get current actor info for passing to Celery task."""
    actor = ActorContext.get_current()
    return {
        "actor_id": actor.actor_id,
        "actor_type": actor.actor_type,
        "source": f"celery_from_{actor.source}",
        "ip_address": actor.ip_address,
        "session_id": actor.session_id,
        "original_set_at": actor.set_at.isoformat(),
        "roles": actor.roles,  # RBAC 역할 전달
    }
```

**Celery task_prerun 시그널을 활용한 자동 복원:**
```python
# adapters/celery/signals.py

from celery.signals import task_prerun, task_postrun

@task_prerun.connect
def setup_causation_context(
    sender: Any,
    task_id: str,
    task: Any,
    args: tuple,
    kwargs: dict,
    **extra: Any,
) -> None:
    """
    Celery Task 시작 시 causation 컨텍스트 자동 복원.
    
    task.request.headers에서 causation 정보를 추출하여
    CausationContext를 설정합니다.
    
    Code reference:
        audit/trace.py#L300-320 (set_celery_context 패턴)
        context/actor_context.py#L447-478 (restore_actor_from_celery 패턴)
    """
    headers = getattr(task.request, "headers", None) or {}
    
    # Causation 헤더 추출
    cascade_id = headers.get(CELERY_HEADER_CASCADE_ID)
    
    if cascade_id:
        # 컨텍스트 복사 (원자성 보장)
        info = CausationInfo(
            cascade_id=cascade_id,
            parent_event_id=headers.get(CELERY_HEADER_PARENT_EVENT, ""),
            chain_depth=int(headers.get(CELERY_HEADER_CHAIN_DEPTH, "0")) + 1,  # 깊이 증가
            namespace=headers.get(CELERY_HEADER_NAMESPACE, "global"),
            metadata={
                "copied_at": datetime.now(timezone.utc).isoformat(),
                "parent_task_id": headers.get("parent_task_id"),
            },
        )
        
        # ContextVar에 설정 (token 저장)
        token = _current_causation.set(info)
        
        # token을 task request에 저장 (postrun에서 정리용)
        task.request._causation_token = token
        
        logger.debug(
            f"[CausationContext] Auto-restored in task: "
            f"cascade={cascade_id}, depth={info.chain_depth}"
        )


@task_postrun.connect
def cleanup_causation_context(
    sender: Any,
    task_id: str,
    task: Any,
    **extra: Any,
) -> None:
    """
    Celery Task 종료 시 causation 컨텍스트 정리.
    
    Code reference:
        audit/trace.py#L333-340 (clear_celery_context 패턴)
    """
    token = getattr(task.request, "_causation_token", None)
    
    if token:
        _current_causation.reset(token)
        delattr(task.request, "_causation_token")
```

**핵심 원칙:**
1. **호출 시점에 직렬화** (`get_causation_for_celery()`)
2. **시작 시점에 복사** (`task_prerun` 시그널에서 새 `CausationInfo` 인스턴스 생성)
3. **chain_depth 자동 증가** (부모-자식 관계 명확화)
4. **종료 시점에 정리** (`task_postrun` 시그널에서 token reset)

---

### 3.3 인과관계 순환 참조(Circular Causality) 방어

> **리뷰 ③**: 최대 체인 깊이 제한 및 순환 감지

#### 3.3.1 배경

자동화 시스템이 서로 연쇄 반응을 일으키면 **A → B → A** 루프가 발생할 수 있습니다.

#### 3.3.2 설정

```python
@dataclass
class CascadeChainConfig:
    """
    Cascade 체인 깊이 설정.
    
    Code reference:
        services/error_budget/propagation.py#L78-84 (max_hops 패턴)
    """
    
    max_chain_depth: int = 10
    """
    최대 체인 깊이.
    
    이 값을 초과하면 경고 발생 또는 차단.
    리뷰 §3.2.3 반영.
    """
    
    warn_at_depth: int = 7
    """경고를 발생시킬 깊이."""
    
    block_on_exceed: bool = True
    """깊이 초과 시 차단 여부 (False면 경고만)."""
    
    detect_cycles: bool = True
    """순환 참조 감지 활성화."""


# 메트릭
CASCADE_CHAIN_DEPTH_EXCEEDED = Counter(
    "selfhealing_cascade_chain_depth_exceeded_total",
    "Number of times cascade chain depth was exceeded",
    ["namespace", "trigger_type"],
)

CASCADE_CYCLE_DETECTED = Counter(
    "selfhealing_cascade_cycle_detected_total",
    "Number of times a cascade cycle was detected",
    ["namespace"],
)
```

#### 3.3.3 체인 깊이 검사 로직

```python
class CascadeChainDepthExceeded(Exception):
    """체인 깊이 초과 예외."""
    
    def __init__(self, depth: int, max_depth: int, cascade_id: str):
        self.depth = depth
        self.max_depth = max_depth
        self.cascade_id = cascade_id
        super().__init__(
            f"Cascade chain depth {depth} exceeds max {max_depth} "
            f"for cascade {cascade_id}"
        )


class CascadeCycleDetected(Exception):
    """순환 참조 감지 예외."""
    
    def __init__(self, cycle_path: List[str], cascade_id: str):
        self.cycle_path = cycle_path
        self.cascade_id = cascade_id
        super().__init__(
            f"Cascade cycle detected: {' -> '.join(cycle_path)} "
            f"in cascade {cascade_id}"
        )


def check_chain_depth(
    current_depth: int,
    config: CascadeChainConfig,
    cascade_id: str,
    namespace: str,
    trigger_type: str,
) -> None:
    """
    체인 깊이 검사.
    
    Args:
        current_depth: 현재 체인 깊이
        config: 체인 설정
        cascade_id: Cascade ID
        namespace: 네임스페이스
        trigger_type: 트리거 유형
    
    Raises:
        CascadeChainDepthExceeded: 깊이 초과 시 (block_on_exceed=True)
    """
    if current_depth >= config.warn_at_depth:
        logger.warning(
            f"[CascadeChain] Depth warning: depth={current_depth}, "
            f"cascade={cascade_id}, namespace={namespace}"
        )
    
    if current_depth >= config.max_chain_depth:
        CASCADE_CHAIN_DEPTH_EXCEEDED.labels(
            namespace=namespace,
            trigger_type=trigger_type,
        ).inc()
        
        if config.block_on_exceed:
            raise CascadeChainDepthExceeded(
                depth=current_depth,
                max_depth=config.max_chain_depth,
                cascade_id=cascade_id,
            )
        else:
            logger.error(
                f"[CascadeChain] Depth exceeded but not blocking: "
                f"depth={current_depth}, max={config.max_chain_depth}"
            )


def detect_cycle(
    effects: List[CascadeEffect],
    trigger_event_id: str,
) -> Optional[List[str]]:
    """
    순환 참조 감지.
    
    Args:
        effects: 효과 목록
        trigger_event_id: 트리거 이벤트 ID
    
    Returns:
        순환 경로 (없으면 None)
    """
    # 그래프 구축
    graph: Dict[str, str] = {trigger_event_id: None}
    for effect in effects:
        graph[effect.event_id] = effect.caused_by
    
    # 방문 추적
    visited: Set[str] = set()
    path: List[str] = []
    
    def dfs(node: str) -> Optional[List[str]]:
        if node in path:
            # 순환 발견
            cycle_start = path.index(node)
            return path[cycle_start:] + [node]
        
        if node in visited:
            return None
        
        visited.add(node)
        path.append(node)
        
        # 이 노드가 원인인 효과들 찾기
        for effect in effects:
            if effect.caused_by == node:
                cycle = dfs(effect.event_id)
                if cycle:
                    return cycle
        
        path.pop()
        return None
    
    return dfs(trigger_event_id)
```

---

### 3.4 데이터 보관 및 정제(Retention & Pruning) 정책

> **리뷰 ④**: 차등 보관 정책 및 정리 스케줄

#### 3.4.1 배경

인과관계로 묶인 CascadeEvent 데이터는 일반 로그보다 용량이 크고 구조가 복잡합니다.
**개별 로그는 짧게, Cascade 묶음은 길게** 보관하는 차등 정책이 필요합니다.

#### 3.4.2 보관 정책 설정

```python
@dataclass
class CascadeRetentionConfig:
    """
    Cascade 데이터 보관 정책.
    
    네이밍 선택 이유:
    - `retention`: 업계 표준 용어 (Kafka, ElasticSearch 등)
    - `cascade_`: 일반 audit 로그와 구분
    - 프로젝트 내 cleanup_tasks.py의 older_than_days 패턴과 일관성
    
    Code reference:
        tasks/cleanup_tasks.py (archive_old_dlq_entries 패턴)
        audit/integrity/anchor.py#L46 (DEFAULT_RETENTION_DAYS)
    """
    
    # Hot 데이터 (Redis)
    hot_retention_days: int = 7
    """Redis 내 보관 기간 (빠른 조회용)."""
    
    hot_max_count: int = 10000
    """Redis 내 최대 개수 (메모리 제한)."""
    
    # Warm 데이터 (PostgreSQL)
    warm_retention_days: int = 90
    """PostgreSQL 내 보관 기간 (Audit 대응용)."""
    
    # Cold 데이터 (Archive)
    cold_retention_days: int = 365
    """아카이브 보관 기간 (법적 요구사항)."""
    
    # Index 보관
    index_retention_days: int = 30
    """인덱스 키 보관 기간."""
    
    # Hash Chain Anchor
    anchor_retention_days: int = 90
    """체크포인트 보관 기간 (anchor.py 패턴)."""


# 기본 설정
DEFAULT_CASCADE_RETENTION = CascadeRetentionConfig()
```

#### 3.4.3 Tiered Storage 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                   Cascade Data Tiered Storage                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ [HOT] Redis (0-7일)                                          │  │
│  │                                                               │  │
│  │ • 실시간 조회 최적화                                          │  │
│  │ • 최대 10,000개 유지 (LTRIM)                                 │  │
│  │ • TTL: 7일                                                    │  │
│  │                                                               │  │
│  │ Keys:                                                         │  │
│  │ - selfhealing:{ns}:audit:cascade:{id}                        │  │
│  │ - selfhealing:{ns}:audit:cascade_index                       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                           │                                          │
│                           ▼ (7일 후 이관)                            │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ [WARM] PostgreSQL (7-90일)                                    │  │
│  │                                                               │  │
│  │ • 복잡한 쿼리 지원                                            │  │
│  │ • 월별 파티셔닝 (selfhealing_cascade_2026_01)                │  │
│  │ • GIN 인덱스 (JSONB 검색)                                     │  │
│  │                                                               │  │
│  │ Table: selfhealing_cascade_events                            │  │
│  │ Partitioned by: RANGE (timestamp)                            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                           │                                          │
│                           ▼ (90일 후 아카이브)                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ [COLD] S3/Archive (90-365일)                                  │  │
│  │                                                               │  │
│  │ • 압축 저장 (gzip)                                            │  │
│  │ • 법적 Audit 대응                                             │  │
│  │ • 연 1회 접근 예상                                            │  │
│  │                                                               │  │
│  │ Path: s3://audit-archive/cascade/{year}/{month}/{id}.json.gz │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

#### 3.4.4 정리 태스크

```python
# tasks/cascade_cleanup_tasks.py

def archive_cascade_events_to_postgres(
    older_than_days: int = 7,
) -> Dict[str, Any]:
    """
    Redis에서 PostgreSQL로 Cascade 이벤트 이관.
    
    Code reference:
        tasks/cleanup_tasks.py (archive_old_dlq_entries 패턴)
    """
    # ... 구현 ...


def purge_old_cascade_events(
    older_than_days: int = 365,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    오래된 Cascade 이벤트 영구 삭제.
    
    ⚠️ 고위험: dry_run=True 기본값
    """
    # ... 구현 ...


# Celery Beat 스케줄
CASCADE_CLEANUP_SCHEDULE = {
    "archive-cascade-to-postgres": {
        "task": "selfhealing.tasks.cascade_cleanup.archive_cascade_events_to_postgres",
        "schedule": crontab(hour=3, minute=0),  # 매일 03:00
        "kwargs": {"older_than_days": 7},
    },
    "create-cascade-daily-anchor": {
        "task": "selfhealing.tasks.cascade_cleanup.create_daily_anchor",
        "schedule": crontab(hour=0, minute=5),  # 매일 00:05
    },
    "verify-cascade-chain-integrity": {
        "task": "selfhealing.tasks.cascade_cleanup.verify_chain_integrity",
        "schedule": crontab(hour=4, minute=0),  # 매일 04:00
    },
}
```

---

### 3.5 PostgreSQL 테이블 파티셔닝

> **아키텍트 리뷰 ①**: 월별 파티셔닝으로 대규모 데이터 관리

#### 3.5.1 DDL

```sql
-- Cascade Events 테이블 (월별 파티셔닝)
CREATE TABLE selfhealing_cascade_events (
    id VARCHAR(50) PRIMARY KEY,
    namespace VARCHAR(100) NOT NULL,
    trigger_type VARCHAR(100) NOT NULL,
    trigger_details JSONB NOT NULL,
    effects JSONB NOT NULL,
    causation_chain JSONB NOT NULL,
    external_trace JSONB,
    previous_hash VARCHAR(64),
    current_hash VARCHAR(64) NOT NULL,
    total_effects INTEGER NOT NULL,
    success_count INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    version VARCHAR(10) DEFAULT '1.0'
) PARTITION BY RANGE (timestamp);

-- 월별 파티션 생성 (예시)
CREATE TABLE selfhealing_cascade_events_2026_01 
    PARTITION OF selfhealing_cascade_events
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE TABLE selfhealing_cascade_events_2026_02 
    PARTITION OF selfhealing_cascade_events
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

-- 인덱스
CREATE INDEX idx_cascade_namespace_timestamp 
    ON selfhealing_cascade_events (namespace, timestamp DESC);

CREATE INDEX idx_cascade_trigger_type 
    ON selfhealing_cascade_events (trigger_type);

CREATE INDEX idx_cascade_hash 
    ON selfhealing_cascade_events (current_hash);

-- JSONB 검색용 GIN 인덱스
CREATE INDEX idx_cascade_effects_gin 
    ON selfhealing_cascade_events USING GIN (effects);
```

---

### 3.6 무결성 검증 체크포인트

> **아키텍트 리뷰 ②**: O(1) 체크포인트로 검증 효율화

#### 3.6.1 DailyHashAnchor 통합

```python
class CascadeEventAuditor:
    """Cascade Event 감사기 (체크포인트 지원)."""
    
    # 기존 키 + 체크포인트 키
    CHECKPOINT_KEY = "selfhealing:{namespace}:audit:cascade_checkpoint"
    
    def __init__(self, anchor: Optional[DailyHashAnchor] = None):
        self._lock = threading.RLock()
        self._anchor = anchor  # DailyHashAnchor 인스턴스
    
    def verify_chain_integrity_from_checkpoint(
        self,
        namespace: str,
    ) -> Dict[str, Any]:
        """
        체크포인트 이후만 검증 (효율적).
        
        기존 verify_chain_integrity()는 처음부터 검증하지만,
        이 메서드는 마지막 체크포인트 이후만 검증합니다.
        
        Code reference:
            audit/integrity/anchor.py (DailyHashAnchor 패턴)
            audit/integrity/health_score.py#L71 (last_verified_sequence)
        """
        # 1. 체크포인트 조회
        checkpoint = self._get_checkpoint(namespace)
        
        if not checkpoint:
            # 체크포인트 없으면 전체 검증
            return self.verify_chain_integrity(namespace)
        
        # 2. 체크포인트 이후 이벤트만 조회
        events = self._get_events_after_checkpoint(
            namespace=namespace,
            after_hash=checkpoint["last_hash"],
        )
        
        if not events:
            return {
                "valid": True,
                "checked": 0,
                "from_checkpoint": checkpoint["verified_at"],
                "errors": [],
            }
        
        # 3. 첫 이벤트가 체크포인트와 연결되는지 확인
        errors = []
        first_event = events[0]
        
        if first_event.previous_hash != checkpoint["last_hash"]:
            errors.append({
                "cascade_id": first_event.id,
                "error": "checkpoint_mismatch",
                "expected_previous": checkpoint["last_hash"],
                "actual_previous": first_event.previous_hash,
            })
        
        # 4. 나머지 체인 검증
        for i, event in enumerate(events):
            recalculated = event.calculate_hash()
            if recalculated != event.current_hash:
                errors.append({
                    "cascade_id": event.id,
                    "error": "hash_mismatch",
                })
            
            if i < len(events) - 1:
                next_event = events[i + 1]
                if next_event.previous_hash != event.current_hash:
                    errors.append({
                        "cascade_id": next_event.id,
                        "error": "chain_broken",
                    })
        
        return {
            "valid": len(errors) == 0,
            "checked": len(events),
            "from_checkpoint": checkpoint["verified_at"],
            "errors": errors,
        }
    
    def create_checkpoint(self, namespace: str) -> Dict[str, Any]:
        """
        현재 상태를 체크포인트로 저장.
        
        Daily Celery Beat에서 호출됩니다.
        """
        backend = self._get_backend()
        
        # 최신 이벤트의 해시 조회
        last_hash = self._get_last_hash(namespace)
        
        checkpoint = {
            "last_hash": last_hash,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "event_count": self._get_event_count(namespace),
        }
        
        key = self.CHECKPOINT_KEY.format(namespace=namespace)
        backend.set(key, checkpoint)
        
        logger.info(f"[CascadeAudit] Checkpoint created: namespace={namespace}")
        return checkpoint
```

---

### 3.7 수동 개입(Human-in-the-loop) 기록

> **아키텍트 리뷰 ④**: 자동화 결정을 사람이 뒤집은 기록

#### 3.7.1 ManualInterventionEffect

```python
@dataclass
class ManualInterventionEffect(CascadeEffect):
    """
    수동 개입으로 인한 효과.
    
    시스템의 자동화 결정을 사람이 오버라이드했을 때 기록합니다.
    
    Code reference:
        services/namespace_emergency/atomic_query.py#L34 (precedence 패턴)
    """
    
    intervention_type: str = "OVERRIDE"
    """개입 유형: OVERRIDE, CANCEL, APPROVE, REJECT."""
    
    overridden_decision: Optional[Dict[str, Any]] = None
    """오버라이드된 자동화 결정 정보."""
    
    justification: Optional[str] = None
    """개입 사유."""
    
    approved_by: Optional[str] = None
    """승인자 (2인 승인 시)."""
    
    related_cascade_id: Optional[str] = None
    """관련 Cascade ID (기존 자동화 흐름 참조)."""
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "intervention_type": self.intervention_type,
            "overridden_decision": self.overridden_decision,
            "justification": self.justification,
            "approved_by": self.approved_by,
            "related_cascade_id": self.related_cascade_id,
        })
        return base


# 개입 유형 상수
class InterventionType:
    OVERRIDE = "OVERRIDE"      # 자동화 결정 덮어쓰기
    CANCEL = "CANCEL"          # 진행 중인 자동화 취소
    APPROVE = "APPROVE"        # 대기 중인 자동화 승인
    REJECT = "REJECT"          # 대기 중인 자동화 거부
    ESCALATE = "ESCALATE"      # 수동 격상
    DEESCALATE = "DEESCALATE"  # 수동 해제
```

---

### 3.8 Backpressure 및 Load Shedding

> **아키텍트 리뷰 ⑤**: 대규모 폭주 시 버퍼 보호

#### 3.8.1 AuditBufferBackpressure

```python
@dataclass
class AuditBackpressureConfig:
    """
    Audit 버퍼 배압 설정.
    
    Code reference:
        services/chaos/experiments/audit.py#L354-380 (verify_backpressure 패턴)
    """
    
    buffer_warning_threshold: float = 0.7
    """버퍼 70% 도달 시 경고."""
    
    buffer_critical_threshold: float = 0.85
    """버퍼 85% 도달 시 Load Shedding 시작."""
    
    max_buffer_size: int = 10000
    """최대 버퍼 크기."""
    
    load_shedding_enabled: bool = True
    """Load Shedding 활성화."""


@dataclass
class CascadeEventPriority:
    """
    Cascade 이벤트 우선순위.
    
    Load Shedding 시 낮은 우선순위부터 버림.
    """
    
    P0_CRITICAL = 0    # 절대 버리지 않음 (Emergency, Security)
    P1_HIGH = 1        # 최대한 보존 (Governance 변경)
    P2_MEDIUM = 2      # 버퍼 85%에서 버림 (일반 Canary)
    P3_LOW = 3         # 버퍼 70%에서 버림 (정보성 로그)


class CascadeLoadShedding:
    """
    Cascade Audit Load Shedding 관리자.
    
    버퍼가 임계치에 도달하면 낮은 우선순위 이벤트를 버립니다.
    
    Code reference:
        test_lazy_import.py#L105-117 (get_load_shedding_manager 패턴)
    """
    
    def __init__(self, config: Optional[AuditBackpressureConfig] = None):
        self.config = config or AuditBackpressureConfig()
        self._buffer: List[Tuple[int, CascadeEvent]] = []  # (priority, event)
        self._dropped_count: Dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0}
        self._lock = threading.Lock()
    
    def should_accept(self, priority: int) -> bool:
        """
        이벤트 수락 여부 결정.
        
        Args:
            priority: 이벤트 우선순위 (0=P0, 3=P3)
        
        Returns:
            수락 여부
        """
        if not self.config.load_shedding_enabled:
            return True
        
        buffer_ratio = len(self._buffer) / self.config.max_buffer_size
        
        # P0: 항상 수락
        if priority == CascadeEventPriority.P0_CRITICAL:
            return True
        
        # P1: 95%까지 수락
        if priority == CascadeEventPriority.P1_HIGH:
            return buffer_ratio < 0.95
        
        # P2: critical threshold까지 수락
        if priority == CascadeEventPriority.P2_MEDIUM:
            return buffer_ratio < self.config.buffer_critical_threshold
        
        # P3: warning threshold까지 수락
        return buffer_ratio < self.config.buffer_warning_threshold
    
    def add_event(
        self,
        event: CascadeEvent,
        priority: int = CascadeEventPriority.P2_MEDIUM,
    ) -> bool:
        """
        이벤트 추가 (Load Shedding 적용).
        
        Returns:
            추가 성공 여부
        """
        with self._lock:
            if not self.should_accept(priority):
                self._dropped_count[priority] += 1
                logger.warning(
                    f"[LoadShedding] Dropped P{priority} event: "
                    f"cascade={event.id}, buffer_size={len(self._buffer)}"
                )
                return False
            
            self._buffer.append((priority, event))
            return True
    
    def get_backpressure_status(self) -> Dict[str, Any]:
        """배압 상태 조회."""
        with self._lock:
            buffer_size = len(self._buffer)
            buffer_ratio = buffer_size / self.config.max_buffer_size
            
            return {
                "active": buffer_ratio >= self.config.buffer_warning_threshold,
                "buffer_size": buffer_size,
                "buffer_ratio": buffer_ratio,
                "dropped_counts": dict(self._dropped_count),
                "load_shedding_triggered": buffer_ratio >= self.config.buffer_critical_threshold,
            }
```

---

### 3.9 Fail-Soft (Redis 장애 시 로컬 폴백)

> **추가 리뷰 ⑥**: Redis 장애 시 LocalFileBackend로 폴백 (Plan 42 코드 재사용)

#### 3.9.1 배경

CascadeEventAuditor가 Redis에 저장하지만, Redis 장애 시에도 **Audit 데이터 손실 없이**
로컬 파일에 기록되어야 합니다. Plan 42 (72문서)에서 구현된 `CriticalPathFallback` 패턴을 재사용합니다.

#### 3.9.2 기존 코드 참조

```python
# services/coordination/critical_path_fallback.py#L36-47
class CriticalPathFallback:
    """
    연계 레이어 핵심 경로의 로컬 폴백.
    
    Fallback Order (우선순위순):
        1. Redis Primary - 분산 상태 저장소
        2. Local File - 로컬 파일 시스템 (영속)
        3. Memory Buffer - 메모리 버퍼 (휘발성, 최후 수단)
    
    Code reference:
        audit/graceful_degradation/fallback.py#HashChainFallbackChain
    """

# audit/graceful_degradation/fallback.py#L24-45
class HashChainFallbackChain:
    """
    Multi-tier fallback chain for hash chain operations.
    
    Fallback order:
    1. Redis Primary - Full distributed functionality
    2. Redis Replica - Read-only, degraded writes to local
    3. Local File - Persistent but not distributed
    4. Memory Buffer - Last resort, volatile
    """
```

#### 3.9.3 CascadeEventAuditor에 Fail-Soft 통합

```python
class CascadeEventAuditor:
    """Cascade Event 감사기 (Fail-Soft 지원)."""
    
    # 기존 키 + 로컬 폴백 경로
    LOCAL_FALLBACK_PATH = Path("/tmp/cascade_audit_fallback.jsonl")
    
    def __init__(
        self,
        fallback: Optional[CriticalPathFallback] = None,
    ):
        self._lock = threading.RLock()
        self._fallback = fallback or CriticalPathFallback(
            local_audit_path=self.LOCAL_FALLBACK_PATH,
        )
        self._current_tier: str = "redis"
    
    def record(
        self,
        trigger_type: str,
        trigger_details: Dict[str, Any],
        effects: List[Dict[str, Any]],
        namespace: str,
        triggered_by: Optional[str] = None,
    ) -> CascadeEvent:
        """Cascade Event 기록 (Fail-Soft 적용)."""
        
        # ... 기존 CascadeEvent 생성 로직 ...
        
        # 저장 (Fail-Soft)
        tier = self._save_with_fallback(cascade_event)
        self._current_tier = tier
        
        if tier != "redis":
            logger.warning(
                f"[CascadeAudit] Saved to fallback tier: {tier}, "
                f"cascade={cascade_event.id}"
            )
            CASCADE_FALLBACK_EVENTS.labels(tier=tier, namespace=namespace).inc()
        
        return cascade_event
    
    def _save_with_fallback(self, event: CascadeEvent) -> str:
        """
        Fallback 적용 저장.
        
        Code reference:
            services/coordination/critical_path_fallback.py#L160-210
        
        Returns:
            저장된 tier ('redis', 'local', 'memory')
        """
        tier = "memory"
        
        # 1. Redis 시도
        try:
            self._save_cascade_event(event)
            self._update_last_hash(event.namespace, event.current_hash)
            self._add_to_index(event.namespace, event.id)
            return "redis"
        except Exception as e:
            logger.warning(f"[CascadeAudit] Redis save failed: {e}")
        
        # 2. Local File 폴백
        tier = self._fallback.append_audit_log(event.to_dict())
        
        return tier
    
    def recover_from_local_fallback(
        self,
        namespace: str,
    ) -> Dict[str, Any]:
        """
        로컬 폴백에서 Redis로 복구.
        
        Redis 복구 후 로컬에 쌓인 엔트리를 Redis로 이관합니다.
        
        Code reference:
            audit/graceful_degradation/manager.py#L180-220 (reconcile 패턴)
        
        Returns:
            복구 결과 통계
        """
        recovered = 0
        failed = 0
        
        if not self.LOCAL_FALLBACK_PATH.exists():
            return {"recovered": 0, "failed": 0, "message": "No fallback data"}
        
        with open(self.LOCAL_FALLBACK_PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("namespace") == namespace:
                        # Redis로 저장 시도
                        event = CascadeEvent.from_dict(entry)
                        self._save_cascade_event(event)
                        recovered += 1
                except Exception as e:
                    logger.error(f"[CascadeAudit] Recovery failed: {e}")
                    failed += 1
        
        # 복구 완료 후 fallback 파일 정리 (선택적)
        if recovered > 0 and failed == 0:
            self.LOCAL_FALLBACK_PATH.unlink(missing_ok=True)
        
        return {
            "recovered": recovered,
            "failed": failed,
            "message": "Recovery completed" if failed == 0 else "Partial recovery",
        }


# 메트릭
CASCADE_FALLBACK_EVENTS = Counter(
    "selfhealing_cascade_fallback_events_total",
    "Cascade events saved to fallback tier",
    ["tier", "namespace"],
)
```

#### 3.9.4 복구 태스크

```python
# tasks/cascade_cleanup_tasks.py

@shared_task
def recover_cascade_from_fallback(namespace: str = "global") -> Dict[str, Any]:
    """
    Redis 복구 후 로컬 폴백 데이터 이관.
    
    Redis 장애 복구 시 수동 또는 자동으로 호출됩니다.
    """
    auditor = get_cascade_event_auditor()
    return auditor.recover_from_local_fallback(namespace)
```

---

## 4. 구현 상세

### 4.1 CascadeEvent 모델

```python
# packages/selfhealing-python/src/selfhealing/audit/cascade_event.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import hashlib
import json
import uuid


@dataclass
class CascadeEffect:
    """연쇄 효과 (Cascade Event 내 개별 액션)."""
    
    event_id: str
    """이벤트 고유 ID."""
    
    action_type: str
    """액션 유형 (GOVERNANCE_STRICT, CANARY_ROLLBACK, etc.)."""
    
    caused_by: str
    """원인 이벤트 ID."""
    
    success: bool
    """성공 여부."""
    
    target: Optional[str] = None
    """대상 (롤아웃 ID, 서비스 이름 등)."""
    
    details: Dict[str, Any] = field(default_factory=dict)
    """상세 정보."""
    
    error_message: Optional[str] = None
    """실패 시 에러 메시지."""
    
    executed_at: Optional[str] = None
    """실행 시각."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "event_id": self.event_id,
            "action_type": self.action_type,
            "caused_by": self.caused_by,
            "success": self.success,
            "target": self.target,
            "details": self.details,
            "error_message": self.error_message,
            "executed_at": self.executed_at,
        }


@dataclass
class CascadeTrigger:
    """연쇄 트리거 (Cascade Event의 시작점)."""
    
    trigger_type: str
    """트리거 유형 (EMERGENCY_LEVEL_CHANGED, MANUAL_ACTIVATION, etc.)."""
    
    event_id: str
    """트리거 이벤트 ID."""
    
    details: Dict[str, Any] = field(default_factory=dict)
    """트리거 상세 정보."""
    
    triggered_by: Optional[str] = None
    """트리거한 주체 (user, system)."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "trigger_type": self.trigger_type,
            "event_id": self.event_id,
            "details": self.details,
            "triggered_by": self.triggered_by,
        }


@dataclass
class CascadeEvent:
    """
    연쇄 이벤트.
    
    하나의 트리거로 인해 발생한 모든 연계 액션을 묶어서 기록합니다.
    
    Features:
    - 인과관계 추적 (causation chain)
    - 위변조 방지 (hash chain)
    - 전체 흐름 시각화
    
    Reference:
    - docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
    """
    
    id: str
    """Cascade Event 고유 ID."""
    
    trigger: CascadeTrigger
    """트리거 정보."""
    
    effects: List[CascadeEffect]
    """연쇄 효과 목록."""
    
    namespace: str
    """네임스페이스."""
    
    timestamp: str
    """생성 시각 (ISO format)."""
    
    # Hash Chain
    previous_hash: Optional[str] = None
    """이전 CascadeEvent의 해시."""
    
    current_hash: Optional[str] = None
    """현재 CascadeEvent의 해시."""
    
    # 메타데이터
    version: str = "1.0"
    """스키마 버전."""
    
    total_effects: int = 0
    """총 효과 수."""
    
    success_count: int = 0
    """성공한 효과 수."""
    
    failure_count: int = 0
    """실패한 효과 수."""
    
    def __post_init__(self):
        """초기화 후처리."""
        self.total_effects = len(self.effects)
        self.success_count = sum(1 for e in self.effects if e.success)
        self.failure_count = self.total_effects - self.success_count
    
    def get_causation_chain(self) -> List[str]:
        """인과관계 체인 반환."""
        chain = [self.trigger.event_id]
        for effect in self.effects:
            if effect.event_id not in chain:
                chain.append(effect.event_id)
        return chain
    
    def calculate_hash(self) -> str:
        """현재 이벤트의 해시 계산."""
        content = {
            "id": self.id,
            "trigger": self.trigger.to_dict(),
            "effects": [e.to_dict() for e in self.effects],
            "namespace": self.namespace,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
        }
        content_str = json.dumps(content, sort_keys=True)
        return hashlib.sha256(content_str.encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "id": self.id,
            "trigger": self.trigger.to_dict(),
            "effects": [e.to_dict() for e in self.effects],
            "causation_chain": self.get_causation_chain(),
            "namespace": self.namespace,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "current_hash": self.current_hash,
            "version": self.version,
            "total_effects": self.total_effects,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeEvent":
        """딕셔너리에서 생성."""
        trigger = CascadeTrigger(
            trigger_type=data["trigger"]["trigger_type"],
            event_id=data["trigger"]["event_id"],
            details=data["trigger"].get("details", {}),
            triggered_by=data["trigger"].get("triggered_by"),
        )
        
        effects = [
            CascadeEffect(
                event_id=e["event_id"],
                action_type=e["action_type"],
                caused_by=e["caused_by"],
                success=e["success"],
                target=e.get("target"),
                details=e.get("details", {}),
                error_message=e.get("error_message"),
                executed_at=e.get("executed_at"),
            )
            for e in data.get("effects", [])
        ]
        
        return cls(
            id=data["id"],
            trigger=trigger,
            effects=effects,
            namespace=data["namespace"],
            timestamp=data["timestamp"],
            previous_hash=data.get("previous_hash"),
            current_hash=data.get("current_hash"),
            version=data.get("version", "1.0"),
        )
```

### 4.2 CascadeEventAuditor

```python
class CascadeEventAuditor:
    """
    Cascade Event 감사기.
    
    연계 이벤트를 생성, 저장, 조회하고 해시 체인 무결성을 검증합니다.
    
    Features:
    - Cascade Event 생성 및 저장
    - Hash Chain 연결
    - 무결성 검증
    - 인과관계 조회
    
    Reference:
    - docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
    """
    
    # Redis 키 패턴
    CASCADE_KEY = "selfhealing:{namespace}:audit:cascade:{cascade_id}"
    CASCADE_INDEX_KEY = "selfhealing:{namespace}:audit:cascade_index"
    LAST_HASH_KEY = "selfhealing:{namespace}:audit:cascade_last_hash"
    
    def __init__(self):
        self._lock = threading.RLock()
    
    def _get_backend(self):
        """State backend 획득."""
        from selfhealing.core.state_backend import get_state_backend
        return get_state_backend()
    
    def record(
        self,
        trigger_type: str,
        trigger_details: Dict[str, Any],
        effects: List[Dict[str, Any]],
        namespace: str,
        triggered_by: Optional[str] = None,
    ) -> CascadeEvent:
        """
        Cascade Event 기록.
        
        Args:
            trigger_type: 트리거 유형
            trigger_details: 트리거 상세 정보
            effects: 연쇄 효과 목록
            namespace: 네임스페이스
            triggered_by: 트리거 주체
        
        Returns:
            생성된 CascadeEvent
        """
        with self._lock:
            # 1. ID 생성
            cascade_id = f"cascade-{uuid.uuid4().hex[:12]}"
            trigger_event_id = f"evt-{uuid.uuid4().hex[:8]}"
            now = datetime.now(timezone.utc).isoformat()
            
            # 2. 트리거 생성
            trigger = CascadeTrigger(
                trigger_type=trigger_type,
                event_id=trigger_event_id,
                details=trigger_details,
                triggered_by=triggered_by,
            )
            
            # 3. 효과 생성
            cascade_effects = []
            previous_event_id = trigger_event_id
            
            for effect_data in effects:
                effect_event_id = f"evt-{uuid.uuid4().hex[:8]}"
                
                effect = CascadeEffect(
                    event_id=effect_event_id,
                    action_type=effect_data.get("action_type", "UNKNOWN"),
                    caused_by=effect_data.get("caused_by", previous_event_id),
                    success=effect_data.get("success", True),
                    target=effect_data.get("target"),
                    details=effect_data.get("details", {}),
                    error_message=effect_data.get("error_message"),
                    executed_at=now,
                )
                cascade_effects.append(effect)
                previous_event_id = effect_event_id
            
            # 4. 이전 해시 조회
            previous_hash = self._get_last_hash(namespace)
            
            # 5. Cascade Event 생성
            cascade_event = CascadeEvent(
                id=cascade_id,
                trigger=trigger,
                effects=cascade_effects,
                namespace=namespace,
                timestamp=now,
                previous_hash=previous_hash,
            )
            
            # 6. 해시 계산 및 설정
            cascade_event.current_hash = cascade_event.calculate_hash()
            
            # 7. 저장
            self._save_cascade_event(cascade_event)
            self._update_last_hash(namespace, cascade_event.current_hash)
            self._add_to_index(namespace, cascade_id)
            
            logger.info(
                f"[CascadeAudit] Recorded: id={cascade_id}, "
                f"trigger={trigger_type}, effects={len(cascade_effects)}, "
                f"namespace={namespace}"
            )
            
            return cascade_event
    
    def get_cascade_event(
        self,
        cascade_id: str,
        namespace: str,
    ) -> Optional[CascadeEvent]:
        """
        Cascade Event 조회.
        
        Args:
            cascade_id: Cascade Event ID
            namespace: 네임스페이스
        
        Returns:
            CascadeEvent 또는 None
        """
        backend = self._get_backend()
        key = self.CASCADE_KEY.format(namespace=namespace, cascade_id=cascade_id)
        data = backend.get(key)
        
        if data:
            return CascadeEvent.from_dict(data)
        return None
    
    def get_recent_events(
        self,
        namespace: str,
        limit: int = 100,
    ) -> List[CascadeEvent]:
        """
        최근 Cascade Event 목록 조회.
        
        Args:
            namespace: 네임스페이스
            limit: 최대 개수
        
        Returns:
            CascadeEvent 목록 (최신순)
        """
        backend = self._get_backend()
        index_key = self.CASCADE_INDEX_KEY.format(namespace=namespace)
        
        # 인덱스에서 최근 ID 목록 조회
        cascade_ids = backend.lrange(index_key, 0, limit - 1)
        
        events = []
        for cascade_id in cascade_ids:
            event = self.get_cascade_event(cascade_id, namespace)
            if event:
                events.append(event)
        
        return events
    
    def verify_chain_integrity(
        self,
        namespace: str,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """
        Hash Chain 무결성 검증.
        
        Args:
            namespace: 네임스페이스
            limit: 검증할 최대 이벤트 수
        
        Returns:
            검증 결과
        """
        events = self.get_recent_events(namespace, limit)
        
        if not events:
            return {"valid": True, "checked": 0, "errors": []}
        
        errors = []
        
        for i, event in enumerate(events):
            # 1. 해시 재계산
            recalculated_hash = event.calculate_hash()
            if recalculated_hash != event.current_hash:
                errors.append({
                    "cascade_id": event.id,
                    "error": "hash_mismatch",
                    "expected": event.current_hash,
                    "actual": recalculated_hash,
                })
            
            # 2. 체인 연결 확인 (마지막 제외)
            if i < len(events) - 1:
                next_event = events[i + 1]
                if event.previous_hash != next_event.current_hash:
                    errors.append({
                        "cascade_id": event.id,
                        "error": "chain_broken",
                        "expected_previous": next_event.current_hash,
                        "actual_previous": event.previous_hash,
                    })
        
        return {
            "valid": len(errors) == 0,
            "checked": len(events),
            "errors": errors,
        }
    
    def find_by_trigger_event(
        self,
        trigger_event_id: str,
        namespace: str,
    ) -> Optional[CascadeEvent]:
        """
        트리거 이벤트 ID로 Cascade Event 조회.
        
        Args:
            trigger_event_id: 트리거 이벤트 ID
            namespace: 네임스페이스
        
        Returns:
            CascadeEvent 또는 None
        """
        events = self.get_recent_events(namespace, limit=1000)
        
        for event in events:
            if event.trigger.event_id == trigger_event_id:
                return event
        
        return None
    
    def get_causation_trace(
        self,
        effect_event_id: str,
        namespace: str,
    ) -> List[Dict[str, Any]]:
        """
        효과 이벤트의 인과관계 추적.
        
        특정 효과가 왜 발생했는지 역추적합니다.
        
        Args:
            effect_event_id: 효과 이벤트 ID
            namespace: 네임스페이스
        
        Returns:
            인과관계 추적 결과 (트리거까지 역추적)
        """
        events = self.get_recent_events(namespace, limit=1000)
        
        for cascade in events:
            for effect in cascade.effects:
                if effect.event_id == effect_event_id:
                    # 인과관계 역추적
                    trace = []
                    current_id = effect_event_id
                    
                    while True:
                        # 현재 ID에 해당하는 효과 찾기
                        found = False
                        for e in cascade.effects:
                            if e.event_id == current_id:
                                trace.append({
                                    "event_id": e.event_id,
                                    "action_type": e.action_type,
                                    "caused_by": e.caused_by,
                                })
                                current_id = e.caused_by
                                found = True
                                break
                        
                        if not found:
                            # 트리거에 도달
                            if current_id == cascade.trigger.event_id:
                                trace.append({
                                    "event_id": cascade.trigger.event_id,
                                    "action_type": cascade.trigger.trigger_type,
                                    "caused_by": None,
                                })
                            break
                    
                    return list(reversed(trace))
        
        return []
    
    # =========================================================================
    # Private Methods
    # =========================================================================
    
    def _get_last_hash(self, namespace: str) -> Optional[str]:
        """마지막 해시 조회."""
        backend = self._get_backend()
        key = self.LAST_HASH_KEY.format(namespace=namespace)
        return backend.get(key)
    
    def _update_last_hash(self, namespace: str, hash_value: str) -> None:
        """마지막 해시 업데이트."""
        backend = self._get_backend()
        key = self.LAST_HASH_KEY.format(namespace=namespace)
        backend.set(key, hash_value)
    
    def _save_cascade_event(self, event: CascadeEvent) -> None:
        """Cascade Event 저장."""
        backend = self._get_backend()
        key = self.CASCADE_KEY.format(
            namespace=event.namespace,
            cascade_id=event.id,
        )
        backend.set(key, event.to_dict())
    
    def _add_to_index(self, namespace: str, cascade_id: str) -> None:
        """인덱스에 추가."""
        backend = self._get_backend()
        key = self.CASCADE_INDEX_KEY.format(namespace=namespace)
        backend.lpush(key, cascade_id)
        # 최대 10000개 유지
        backend.ltrim(key, 0, 9999)


# =============================================================================
# Singleton
# =============================================================================

_cascade_auditor: Optional[CascadeEventAuditor] = None


def get_cascade_event_auditor() -> CascadeEventAuditor:
    """CascadeEventAuditor 싱글톤 반환."""
    global _cascade_auditor
    if _cascade_auditor is None:
        _cascade_auditor = CascadeEventAuditor()
    return _cascade_auditor
```

---

## 5. EmergencyCoordinator 연동

### 5.1 Cascade 기록 통합

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/coordinator.py

class EmergencyCoordinator:
    """Emergency Coordination Layer 중앙 조율자."""
    
    def __init__(
        self,
        policy_engine: CoordinationPolicyEngine,
        cascade_auditor: CascadeEventAuditor,
    ):
        self.policy_engine = policy_engine
        self.cascade_auditor = cascade_auditor
    
    def on_emergency_level_changed(
        self,
        old_level: EmergencyLevel,
        new_level: EmergencyLevel,
        namespace: str,
        triggered_by: str = "system",
    ) -> CoordinationResult:
        """
        Emergency Level 변경 시 연계 액션 실행 및 Cascade 기록.
        """
        # 1. 정책 조회
        actions = self.policy_engine.get_actions_for_level_change(
            old_level=old_level,
            new_level=new_level,
            namespace=namespace,
        )
        
        # 2. 액션 실행
        effect_results = []
        for action in actions:
            result = self._execute_action(action, namespace)
            effect_results.append({
                "action_type": action.type.value,
                "success": result.success,
                "target": result.target,
                "details": result.details,
                "error_message": result.error_message,
            })
        
        # 3. Cascade Event 기록
        cascade_event = self.cascade_auditor.record(
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            trigger_details={
                "old_level": old_level.name,
                "new_level": new_level.name,
            },
            effects=effect_results,
            namespace=namespace,
            triggered_by=triggered_by,
        )
        
        return CoordinationResult(
            success=all(e["success"] for e in effect_results),
            cascade_event_id=cascade_event.id,
            executed_actions=effect_results,
        )
```

---

## 6. API 엔드포인트

### 6.1 Cascade Event 조회 API

```python
# packages/selfhealing-python/src/selfhealing/api/django/views/cascade.py

class CascadeEventListView(APIView):
    """Cascade Event 목록 조회 API."""
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request) -> Response:
        """최근 Cascade Event 목록 조회."""
        namespace = request.query_params.get("namespace", "global")
        limit = int(request.query_params.get("limit", 50))
        
        auditor = get_cascade_event_auditor()
        events = auditor.get_recent_events(namespace, limit)
        
        return Response({
            "events": [e.to_dict() for e in events],
            "count": len(events),
            "namespace": namespace,
        })


class CascadeEventDetailView(APIView):
    """Cascade Event 상세 조회 API."""
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request, cascade_id: str) -> Response:
        """Cascade Event 상세 조회."""
        namespace = request.query_params.get("namespace", "global")
        
        auditor = get_cascade_event_auditor()
        event = auditor.get_cascade_event(cascade_id, namespace)
        
        if not event:
            return Response(
                {"error": "Cascade event not found"},
                status=404,
            )
        
        return Response(event.to_dict())


class CascadeChainVerifyView(APIView):
    """Hash Chain 무결성 검증 API."""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request: Request) -> Response:
        """Hash Chain 무결성 검증."""
        namespace = request.data.get("namespace", "global")
        limit = request.data.get("limit", 1000)
        
        auditor = get_cascade_event_auditor()
        result = auditor.verify_chain_integrity(namespace, limit)
        
        return Response(result)


class CausationTraceView(APIView):
    """인과관계 추적 API."""
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request, event_id: str) -> Response:
        """효과 이벤트의 인과관계 추적."""
        namespace = request.query_params.get("namespace", "global")
        
        auditor = get_cascade_event_auditor()
        trace = auditor.get_causation_trace(event_id, namespace)
        
        if not trace:
            return Response(
                {"error": "Event not found or no causation trace"},
                status=404,
            )
        
        return Response({
            "event_id": event_id,
            "trace": trace,
            "depth": len(trace),
        })
```

---

## 7. 테스트

### 7.1 단위 테스트

```python
class TestCascadeEventAuditor:
    """CascadeEventAuditor 단위 테스트."""
    
    def test_record_cascade_event(self):
        """Cascade Event 기록."""
        auditor = CascadeEventAuditor()
        
        event = auditor.record(
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            trigger_details={"old_level": "NORMAL", "new_level": "LEVEL_3"},
            effects=[
                {"action_type": "GOVERNANCE_STRICT", "success": True},
                {"action_type": "CANARY_ROLLBACK", "success": True},
            ],
            namespace="test",
            triggered_by="test_user",
        )
        
        assert event.id.startswith("cascade-")
        assert event.trigger.trigger_type == "EMERGENCY_LEVEL_CHANGED"
        assert len(event.effects) == 2
        assert event.total_effects == 2
        assert event.success_count == 2
    
    def test_hash_chain_integrity(self):
        """Hash Chain 무결성."""
        auditor = CascadeEventAuditor()
        
        # 2개의 Cascade Event 생성
        event1 = auditor.record(
            trigger_type="EVENT_1",
            trigger_details={},
            effects=[],
            namespace="test",
        )
        
        event2 = auditor.record(
            trigger_type="EVENT_2",
            trigger_details={},
            effects=[],
            namespace="test",
        )
        
        # event2의 previous_hash는 event1의 current_hash
        assert event2.previous_hash == event1.current_hash
        
        # 무결성 검증
        result = auditor.verify_chain_integrity("test", limit=10)
        assert result["valid"] is True
    
    def test_causation_trace(self):
        """인과관계 추적."""
        auditor = CascadeEventAuditor()
        
        event = auditor.record(
            trigger_type="LEVEL_3_DETECTED",
            trigger_details={},
            effects=[
                {"action_type": "GOVERNANCE_STRICT", "success": True},
                {"action_type": "CANARY_ROLLBACK", "success": True},
            ],
            namespace="test",
        )
        
        # 마지막 효과의 인과관계 추적
        last_effect_id = event.effects[-1].event_id
        trace = auditor.get_causation_trace(last_effect_id, "test")
        
        assert len(trace) >= 2
        assert trace[0]["action_type"] == "LEVEL_3_DETECTED"
        assert trace[-1]["event_id"] == last_effect_id
```

---

## 8. 모니터링

### 8.1 메트릭

```python
CASCADE_EVENTS_TOTAL = Counter(
    "selfhealing_cascade_events_total",
    "Total cascade events recorded",
    ["trigger_type", "namespace"],
)

CASCADE_EFFECTS_TOTAL = Counter(
    "selfhealing_cascade_effects_total",
    "Total cascade effects executed",
    ["action_type", "success", "namespace"],
)

CASCADE_CHAIN_INTEGRITY = Gauge(
    "selfhealing_cascade_chain_integrity",
    "Hash chain integrity status (1=valid, 0=invalid)",
    ["namespace"],
)
```

### 8.2 알림 템플릿

```
🔗 Cascade Event Recorded

Cascade ID: cascade-evt-abc123
Trigger: EMERGENCY_LEVEL_CHANGED (NORMAL → LEVEL_3)
Namespace: seoul
Time: 2026-01-21T15:30:00Z

Effects Executed:
✅ GOVERNANCE_STRICT → Mode changed to STRICT
✅ CANARY_ROLLBACK → 2 rollouts rolled back
✅ BUDGET_MULTIPLIER → Set to 5.0x

Causation Chain:
[evt-001] LEVEL_3_DETECTED
    └─▶ [evt-002] GOVERNANCE_STRICT
        └─▶ [evt-003] CANARY_ROLLBACK
    └─▶ [evt-004] BUDGET_MULTIPLIER

Hash Chain: ✅ Verified
Previous: abc123...
Current: def456...

View Details: https://dashboard/cascade/cascade-evt-abc123
```

---

## 9. 구현 순서

### 9.1 Phase 1: 핵심 모델 및 저장소 (2일)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 1-1 | CascadeEffect, CascadeTrigger, CascadeEvent 모델 | `audit/cascade_event.py` | 없음 |
| 1-2 | ExternalTraceContext 모델 | `audit/cascade_event.py` | 1-1 |
| 1-3 | ManualInterventionEffect 모델 | `audit/cascade_event.py` | 1-1 |
| 1-4 | CascadeEventAuditor 기본 구현 | `audit/cascade_auditor.py` | 1-1 |
| 1-5 | Redis 저장/조회 (record, get_cascade_event) | `audit/cascade_auditor.py` | 1-4 |
| 1-6 | 단위 테스트 | `tests/unit/audit/test_cascade_event.py` | 1-1~1-5 |

### 9.2 Phase 2: 컨텍스트 전파 및 순환 방어 (2일)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 2-1 | CausationInfo, CausationContext (contextvars) | `context/causation_context.py` | 없음 |
| 2-2 | Celery 헤더 전파 함수 | `context/causation_context.py` | 2-1 |
| 2-3 | CascadeChainConfig 설정 | `audit/cascade_config.py` | 없음 |
| 2-4 | check_chain_depth, detect_cycle 로직 | `audit/cascade_chain.py` | 2-3 |
| 2-5 | CascadeChainDepthExceeded, CascadeCycleDetected 예외 | `audit/exceptions.py` | 없음 |
| 2-6 | 단위 테스트 | `tests/unit/audit/test_causation_context.py` | 2-1~2-5 |

### 9.3 Phase 3: Hash Chain 및 무결성 검증 (1.5일)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 3-1 | calculate_hash 구현 | `audit/cascade_event.py` | 1-1 |
| 3-2 | verify_chain_integrity 구현 | `audit/cascade_auditor.py` | 3-1 |
| 3-3 | verify_chain_integrity_from_checkpoint | `audit/cascade_auditor.py` | 3-2 |
| 3-4 | create_checkpoint (DailyHashAnchor 통합) | `audit/cascade_auditor.py` | 3-2 |
| 3-5 | 무결성 검증 테스트 | `tests/unit/audit/test_hash_chain.py` | 3-1~3-4 |

### 9.4 Phase 4: 보관 정책 및 정리 태스크 (1.5일)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 4-1 | CascadeRetentionConfig 설정 | `audit/cascade_config.py` | 없음 |
| 4-2 | PostgreSQL 테이블 DDL (파티셔닝) | `migrations/xxxx_cascade_events.py` | 없음 |
| 4-3 | archive_cascade_events_to_postgres 태스크 | `tasks/cascade_cleanup_tasks.py` | 4-1, 4-2 |
| 4-4 | purge_old_cascade_events 태스크 | `tasks/cascade_cleanup_tasks.py` | 4-1 |
| 4-5 | Celery Beat 스케줄 등록 | `celery_app.py` | 4-3, 4-4 |
| 4-6 | 정리 태스크 테스트 | `tests/unit/tasks/test_cascade_cleanup.py` | 4-3~4-5 |

### 9.5 Phase 5: Backpressure, Load Shedding 및 Fail-Soft (1.5일)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 5-1 | AuditBackpressureConfig 설정 | `audit/cascade_config.py` | 없음 |
| 5-2 | CascadeEventPriority 정의 | `audit/cascade_event.py` | 없음 |
| 5-3 | CascadeLoadShedding 구현 | `audit/cascade_load_shedding.py` | 5-1, 5-2 |
| 5-4 | CascadeEventAuditor에 Load Shedding 통합 | `audit/cascade_auditor.py` | 5-3 |
| 5-5 | Fail-Soft (CriticalPathFallback 통합) | `audit/cascade_auditor.py` | Phase 1 |
| 5-6 | recover_from_local_fallback 구현 | `audit/cascade_auditor.py` | 5-5 |
| 5-7 | recover_cascade_from_fallback 태스크 | `tasks/cascade_cleanup_tasks.py` | 5-6 |
| 5-8 | Backpressure / Fail-Soft 테스트 | `tests/unit/audit/test_load_shedding.py` | 5-3~5-7 |

### 9.6 Phase 6: Context 원자성 및 Celery 시그널 (0.5일)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 6-1 | task_prerun 시그널 (자동 컨텍스트 복원) | `adapters/celery/signals.py` | Phase 2 |
| 6-2 | task_postrun 시그널 (컨텍스트 정리) | `adapters/celery/signals.py` | 6-1 |
| 6-3 | Celery 시그널 테스트 | `tests/unit/adapters/test_celery_signals.py` | 6-1, 6-2 |

### 9.7 Phase 7: EmergencyCoordinator 연동 (1일)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 7-1 | EmergencyCoordinator에 cascade_auditor 주입 | `services/coordination/coordinator.py` | Phase 1 |
| 7-2 | on_emergency_level_changed에 Cascade 기록 | `services/coordination/coordinator.py` | 7-1 |
| 7-3 | record_with_external_trace 구현 | `audit/cascade_auditor.py` | 7-1 |
| 7-4 | 통합 테스트 | `tests/integration/test_coordinator_cascade.py` | 7-1~7-3 |

### 9.8 Phase 8: API 엔드포인트 (0.5일)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 8-1 | CascadeEventListView | `api/django/views/cascade.py` | Phase 1 |
| 8-2 | CascadeEventDetailView | `api/django/views/cascade.py` | Phase 1 |
| 8-3 | CascadeChainVerifyView | `api/django/views/cascade.py` | Phase 3 |
| 8-4 | CausationTraceView | `api/django/views/cascade.py` | Phase 1 |
| 8-5 | URL 라우팅 등록 | `api/django/urls.py` | 8-1~8-4 |
| 8-6 | API 테스트 | `tests/api/test_cascade_api.py` | 8-1~8-5 |

### 9.9 Phase 9: 모니터링 및 알림 (0.5일)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 9-1 | Prometheus 메트릭 정의 | `audit/cascade_metrics.py` | 없음 |
| 9-2 | 메트릭 수집 통합 | `audit/cascade_auditor.py` | 9-1 |
| 9-3 | 알림 템플릿 정의 | `notifications/templates/cascade.py` | 없음 |
| 9-4 | Grafana 대시보드 JSON | `docker/grafana/dashboards/cascade.json` | 9-1 |

### 9.10 구현 순서 요약

```
┌─────────────────────────────────────────────────────────────────────┐
│                    76문서 구현 순서 (총 11일)                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Phase 1 (2일)              Phase 2 (2일)                           │
│  ┌─────────────────┐        ┌─────────────────┐                     │
│  │ 핵심 모델       │───────▶│ 컨텍스트 전파   │                     │
│  │ - CascadeEvent  │        │ - contextvars   │                     │
│  │ - Auditor 기본  │        │ - 순환 방어     │                     │
│  └─────────────────┘        └─────────────────┘                     │
│           │                          │                               │
│           ▼                          ▼                               │
│  Phase 3 (1.5일)            Phase 4 (1.5일)                         │
│  ┌─────────────────┐        ┌─────────────────┐                     │
│  │ Hash Chain      │        │ 보관 정책       │                     │
│  │ - 무결성 검증   │        │ - PostgreSQL    │                     │
│  │ - 체크포인트    │        │ - 정리 태스크   │                     │
│  └─────────────────┘        └─────────────────┘                     │
│           │                          │                               │
│           ▼                          ▼                               │
│  Phase 5 (1.5일)            Phase 6 (0.5일)                         │
│  ┌─────────────────┐        ┌─────────────────┐                     │
│  │ Backpressure    │        │ Context 원자성  │                     │
│  │ - Load Shedding │        │ - task_prerun   │                     │
│  │ - Fail-Soft     │        │ - task_postrun  │                     │
│  └─────────────────┘        └─────────────────┘                     │
│           │                          │                               │
│           ▼                          ▼                               │
│  Phase 7 (1일)              Phase 8+9 (1일)                         │
│  ┌─────────────────┐        ┌─────────────────┐                     │
│  │ Coordinator     │        │ API + 모니터링  │                     │
│  │ - 연동          │        │                 │                     │
│  └─────────────────┘        └─────────────────┘                     │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |
| 1.1.0 | 2026-01-22 | 보완 설계 추가 (섹션 3) - 총 9개 항목 | AI Assistant |
| | | **초기 리뷰 4개:** | |
| | | - ① 분산 추적 표준 호환 (ExternalTraceContext) | |
| | | - ② 비동기 컨텍스트 전파 (CausationContext) | |
| | | - ③ 순환 참조 방어 (max_chain_depth) | |
| | | - ④ 차등 보관 정책 (CascadeRetentionConfig) | |
| | | **아키텍트 리뷰 5개:** | |
| | | - ① PostgreSQL 파티셔닝 DDL (3.5) | |
| | | - ② 체크포인트 검증 / DailyHashAnchor 통합 (3.6) | |
| | | - ③ contextvars 사용 (3.2에 통합 - 리뷰 ②와 동일 주제) | |
| | | - ④ 수동 개입 기록 / ManualInterventionEffect (3.7) | |
| | | - ⑤ Backpressure / Load Shedding (3.8) | |
| 1.2.0 | 2026-01-23 | 구현 순서 추가 (섹션 9) | AI Assistant |
| 1.3.0 | 2026-01-23 | 추가 리뷰 2개 반영 | AI Assistant |
| | | **추가 리뷰:** | |
| | | - ⑥ Fail-Soft / CriticalPathFallback 재사용 (3.9) | |
| | | - ⑦ Context 전파 원자성 / task_prerun 시그널 (3.2.4) | |
| | | - Phase 5, 6 업데이트 | |
| 1.4.0 | 2026-01-23 | **Phase 1, 2 구현 완료** | AI Assistant |
| | | **Phase 1 구현:** | |
| | | - CascadeEffect, CascadeTrigger, CascadeEvent 모델 (`audit/cascade_event.py`) | |
| | | - ExternalTraceContext, ManualInterventionEffect 모델 | |
| | | - CascadeEventAuditor (`audit/cascade_auditor.py`) | |
| | | - 37개 단위 테스트 통과 (`tests/unit/audit/test_cascade_event.py`) | |
| | | **Phase 2 구현:** | |
| | | - CausationInfo, CausationContext (`context/causation_context.py`) | |
| | | - Celery/Kafka 헤더 전파 함수 | |
| | | - CascadeChainConfig (`audit/cascade_config.py`) | |
| | | - check_chain_depth, detect_cycle (`audit/cascade_chain.py`) | |
| | | - 예외 클래스 (`audit/cascade_exceptions.py`) | |
| | | - 45개 단위 테스트 통과 (`tests/unit/audit/test_causation_context.py`) | |
| 1.5.0 | 2026-01-23 | **Phase 3, 4 구현 완료** | AI Assistant |
| | | **Phase 3 구현 (Hash Chain 및 체크포인트):** | |
| | | - create_checkpoint: 체크포인트 생성 (`audit/cascade_auditor.py`) | |
| | | - get_checkpoint: 체크포인트 조회 | |
| | | - verify_chain_integrity_from_checkpoint: 체크포인트 기반 무결성 검증 | |
| | | - get_events_after_timestamp: 특정 시각 이후 이벤트 조회 | |
| | | - 20개 단위 테스트 통과 (`tests/unit/audit/test_hash_chain.py`) | |
| | | **Phase 4 구현 (보관 정책 및 정리 태스크):** | |
| | | - CascadeRetentionConfig: 계층별 보관 정책 설정 (`audit/cascade_config.py`) | |
| | | - archive_cascade_events: Redis → PostgreSQL 이관 태스크 | |
| | | - purge_old_cascade_events: 오래된 이벤트 영구 삭제 태스크 | |
| | | - create_cascade_daily_checkpoint: 일일 체크포인트 생성 태스크 | |
| | | - verify_cascade_chain_integrity: 체인 무결성 검증 태스크 | |
| | | - recover_cascade_from_fallback: 로컬 폴백 복구 태스크 | |
| | | - CASCADE_CLEANUP_SCHEDULE: Celery Beat 스케줄 정의 | |
| | | - 16개 단위 테스트 통과 (`tests/unit/tasks/test_cascade_cleanup.py`) | |
| | | **총 118개 테스트 통과** | |
| 1.6.0 | 2026-01-23 | **Phase 5, 6 구현 완료** | AI Assistant |
| | | **Phase 5 구현 (Backpressure, Load Shedding 및 Fail-Soft):** | |
| | | - AuditBackpressureConfig: Backpressure 설정 (`audit/cascade_config.py`) | |
| | | - CascadeEventPriority: 이벤트 우선순위 정의 (`audit/cascade_event.py`) | |
| | | - TRIGGER_TYPE_PRIORITY: 트리거별 우선순위 매핑 | |
| | | - CascadeLoadShedding: 우선순위 기반 Load Shedding (`audit/cascade_load_shedding.py`) | |
| | | - record_with_load_shedding: Load Shedding 적용 기록 | |
| | | - _save_to_local_fallback: 로컬 폴백 저장 | |
| | | - recover_from_local_fallback: 폴백 복구 | |
| | | - get_load_shedding_status: 상태 조회 | |
| | | - 27개 단위 테스트 통과 (`tests/unit/audit/test_cascade_load_shedding.py`) | |
| | | **Phase 6 구현 (Context 원자성 및 Celery 시그널):** | |
| | | - _setup_causation_context: task_prerun 시그널 Causation 복원 | |
| | | - _cleanup_causation_context: task_postrun 시그널 Causation 정리 | |
| | | - chain_depth 자동 증가 (부모-자식 관계 추적) | |
| | | - 17개 단위 테스트 통과 (`tests/unit/audit/test_celery_causation_signals.py`) | |
| | | **총 162개 테스트 통과** | |
| 1.7.0 | 2026-01-23 | **Phase 7 구현 완료** | AI Assistant |
| | | **Phase 7 구현 (EmergencyCoordinator 연동):** | |
| | | - EmergencyCoordinator에 cascade_auditor 주입 (`services/coordination/coordinator.py`) | |
| | | - on_emergency_level_changed에 Cascade Event 자동 기록 | |
| | | - _record_cascade_event: 내부 메서드로 Cascade 기록 분리 | |
| | | - _get_transition_type: ACTIVATION/DEACTIVATION/ESCALATION/DE_ESCALATION 판별 | |
| | | - request 있을 때 record_with_external_trace 호출 (W3C Trace Context 추출) | |
| | | - set_cascade_auditor/get_cascade_auditor/has_cascade_auditor 메서드 | |
| | | - Cascade 기록 실패 시 Emergency 처리 중단 방지 (graceful 처리) | |
| | | - 19개 단위 테스트 통과 (`tests/unit/services/coordination/test_coordinator_cascade.py`) | |
| | | **총 181개 테스트 통과** | |
