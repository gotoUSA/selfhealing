# 270. Celery Context Consolidation — 태스크 컨텍스트 추출 유틸리티 통합

> **Version**: 2.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Done
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Depends**: [266_CELL_OTEL_PROPAGATION.md](266_CELL_OTEL_PROPAGATION.md) — OTel Baggage 통합

---

## 0. 요약

Celery 태스크 수신 시 컨텍스트를 복원하는 로직이 **3개 파일에 분산**되어 있다.
이를 단일 유틸리티 함수로 통합하여 코드 응집도를 높이고,
266에서 도입하는 OTel Baggage 복원 경로도 동일한 진입점에서 관리한다.

---

## 1. 현재 문제 — 컨텍스트 복원 로직의 파편화

### 1.1 파일별 분산 현황

Celery `task_prerun` 시그널에서 컨텍스트를 복원하는 코드가
3개 파일에 독립적으로 존재한다:

| 파일 | 복원 대상 | 시그널 | 핸들러 |
|------|----------|--------|--------|
| `adapters/celery/signal_hooks.py` L606-672 | trace_id, celery_context, causation | `task_prerun` | `on_task_prerun()` |
| `context/celery_cell_propagation.py` L112-122 | cell_id | `task_prerun` | `extract_cell_id_on_prerun()` |
| `context/celery_propagation.py` L54-79 | causation (발행 시) | `before_task_publish` | `on_before_task_publish()` |

### 1.2 코드 근거 — 각 핸들러의 추출 패턴

**signal_hooks.py** `on_task_prerun()` (L606-672):

```python
@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, task=None, kwargs=None, **kw):
    # ① trace_id 복원
    trace_info = kwargs.get("trace_info") if kwargs else None
    if trace_info and trace_info.get("trace_id"):
        trace_id = trace_info["trace_id"]
        set_trace_id(trace_id)
    else:
        trace_id = generate_celery_trace_id(task_id)
        set_trace_id(trace_id)

    # ② celery_context 설정
    _celery_context_var.set({"task_id": task_id, "task_name": task_name, ...})

    # ③ causation 복원
    _setup_causation_context(sender, task_id, task_name)
```

**celery_cell_propagation.py** `extract_cell_id_on_prerun()` (L112-122):

```python
@task_prerun.connect
def extract_cell_id_on_prerun(task=None, **kwargs):
    if task and hasattr(task.request, "get"):
        cell_id = task.request.get("cell_id")
        if cell_id:
            task._cell_id_token = _current_cell_id.set(cell_id)
```

### 1.3 문제점

1. **새 컨텍스트 추가 시 변경 포인트 다수**: Domain, Deadline 등을 Celery로
   전파하려면 또 다른 `@task_prerun.connect` 핸들러를 별도 파일에 추가해야 한다.
2. **정리(cleanup) 로직도 분산**: `on_task_postrun()`(signal_hooks.py)과
   `clear_cell_id_on_postrun()`(celery_cell_propagation.py)이 별도로 존재한다.
3. **OTel Baggage 복원 경로 추가 시 복잡도 증가**: 266에서 Baggage → ContextVar
   복원을 추가하면 4번째 핸들러가 추가되어 파편화가 더 심해진다.

---

## 2. 목표 상태 — 단일 유틸리티 함수

### 2.1 설계 원칙

**선택 근거**: 기존 `_setup_causation_context()` 함수(signal_hooks.py L462-537)의
패턴을 참조한다. 이 함수는 causation 복원 로직을 단일 함수로 캡슐화하고,
`on_task_prerun()`에서 호출하는 구조이다.

동일한 패턴으로, **모든 컨텍스트 복원을 하나의 함수에 집약**한다:

```
on_task_prerun()
    └── restore_all_task_context(task)
            ├── trace_id 복원
            ├── celery_context 설정
            ├── causation 복원 (_setup_causation_context)
            ├── cell_id 복원  ← 현재 별도 핸들러
            ├── domain 복원   ← 266 이후 추가 예정
            └── OTel Baggage → ContextVar 복원  ← 266 이후 추가 예정
```

### 2.2 유틸리티 함수 설계

> **v2.0 변경**: 리뷰를 반영하여 5가지 설계 결함을 수정하였다.
> - R1: 토큰을 `task`(싱글톤) 대신 `task.request`(요청별 격리)에 저장
> - R2: Baggage 토큰을 `dict[str, contextvars.Token]`으로 반환·추적
> - R3: Baggage/Legacy 이중 Set 제거 — 통합 리졸버로 1회만 Set
> - R4: `_setup_causation_context` 로직을 본 모듈로 완전 이관
> - R5: 컨텍스트 중요도별 Fail-Open/Fail-Fast 정책 분리

```python
"""
context/celery_context_utils.py — Celery 태스크 컨텍스트 통합 복원/정리.

기존 분산된 복원 로직을 단일 진입점으로 통합.

코드 근거:
- adapters/celery/signal_hooks.py L462-537: _setup_causation_context() 패턴
- context/celery_cell_propagation.py L112-122: cell_id 복원 패턴
- observability/baggage.py: restore_contextvars_from_baggage() (266 이후)

v2.0 리뷰 반영:
- R1: task.request 기반 토큰 저장 (Thread Safety)
- R2: contextvars.Token 명시적 타이핑 + baggage_tokens 추적
- R3: 통합 리졸버 (_resolve_cell_id) — Baggage 우선, 레거시 Fallback
- R4: _setup_causation_context 로직 완전 이관 (순환 참조 제거)
- R5: ContextCriticality 기반 에러 핸들링 정책 분리
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# R5: 컨텍스트 중요도 분류 — Fail-Open / Fail-Fast 정책
# =============================================================================
#
# 코드 근거:
# - cell_context.py: _current_cell_id → DB 라우터, 캐시 격벽, DLQ 파티셔닝에 사용
#   → 누락 시 크로스-테넌트 데이터 오염 가능 → CRITICAL
# - audit/trace.py L19: _trace_id_var → 관측용 trace_id
#   → 누락 시 비즈니스 영향 없음 → OPTIONAL
# - causation_context.py L226: _current_causation → 인과관계 추적
#   → 누락 시 추적 불가하나 비즈니스 동작에 영향 없음 → IMPORTANT
# =============================================================================


class ContextCriticality(Enum):
    """컨텍스트 복원 실패 시 정책."""

    CRITICAL = "critical"    # 실패 → 태스크 중단 (Reject/Raise)
    IMPORTANT = "important"  # 실패 → WARNING 로그 + 메트릭 증가, 태스크 계속
    OPTIONAL = "optional"    # 실패 → DEBUG 로그, 태스크 계속


# 컨텍스트별 중요도 분류.
# 환경별 오버라이드는 CeleryContextSettings.critical_contexts에서 런타임 제어.
CONTEXT_CRITICALITY: dict[str, ContextCriticality] = {
    "cell_id": ContextCriticality.CRITICAL,     # DB 라우팅/캐시 격벽
    "tenant_id": ContextCriticality.CRITICAL,   # 멀티테넌트 격리
    "causation": ContextCriticality.IMPORTANT,  # 인과관계 추적
    "trace_id": ContextCriticality.OPTIONAL,    # 관측용
    "domain": ContextCriticality.OPTIONAL,      # 태깅용
}


class SelfHealingContextError(Exception):
    """
    핵심 컨텍스트(CRITICAL) 복원 실패 시 태스크 실행을 중단하는 예외.

    이 예외는 **재시도 불가(Non-retryable)**로 분류되어야 한다.
    cell_id/tenant_id 같은 격리 컨텍스트가 누락된 상태에서 태스크를 실행하면
    크로스-테넌트 데이터 오염이 발생할 수 있으므로, 재시도해도 동일한 결과이다.

    Celery 연동:
    - autoretry_for=(Exception,) 설정된 태스크에서 무한 재시도 방지를 위해
      dont_autoretry_for=(SelfHealingContextError,) 등록 필수 (3.5항 참조)
    - 기존 non_retryable_exceptions 인프라와 통합 (3.5항 참조)

    선례:
    - core/hedging/exceptions.py L53: NonRetryableHedgingError
      → 재시도 무의미한 확정적 에러를 즉시 실패 처리하는 동일 패턴
    - interfaces/resilience_policy.py L52: PolicyRejectedException
      → Policy에 의해 거부된 경우 발생하는 예외 패턴
    """

    def __init__(self, context_name: str, task_name: str, detail: str = ""):
        self.context_name = context_name
        self.task_name = task_name
        super().__init__(
            f"Critical context '{context_name}' restoration failed for task "
            f"'{task_name}'. Task rejected to prevent data contamination. {detail}"
        )


# =============================================================================
# R2: 명시적 contextvars.Token 타이핑
# =============================================================================
#
# 코드 근거:
# - cell_context.py L28: set_cell_id() -> contextvars.Token[str | None]
#   → 프로젝트 내 유일한 명시적 Token 타이핑 선례
# - 나머지 모든 모듈은 Any로 처리 중
# → 통합 유틸리티에서 명시적 타이핑을 확립하여 프로젝트 표준으로 확대
# =============================================================================


@dataclass
class TaskContextTokens:
    """
    태스크 수명 동안 보관할 ContextVar 토큰 모음.

    task_postrun에서 일괄 정리(reset)하기 위해 토큰을 추적한다.

    R1 변경: task.request에 저장 (task 싱글톤이 아닌 요청별 격리 컨텍스트)
    R2 변경: contextvars.Token 명시적 타이핑 (Any → Token)

    패턴 참조:
    - signal_hooks.py L458: _CAUSATION_TOKEN_ATTR로 task.request에 저장
    - cell_context.py L28: contextvars.Token[str | None] 타이핑 선례
    """

    cell_id_token: contextvars.Token[str | None] | None = None
    causation_token: contextvars.Token | None = None
    domain_token: contextvars.Token[str | None] | None = None
    baggage_tokens: dict[str, contextvars.Token] = field(default_factory=dict)
    # R3: Baggage 복원이 개별 Token을 반환하면 여기에 병합하여 추적


# =============================================================================
# R1: task.request 기반 토큰 저장 (Thread Safety)
# =============================================================================
#
# 코드 근거:
# - signal_hooks.py L554: setattr(request, _CAUSATION_TOKEN_ATTR, token)
#   → 검증된 패턴. request는 요청별 격리 컨텍스트.
# - celery_cell_propagation.py L121: task._cell_id_token = ...
#   → 결함 패턴. task는 워커 내 싱글톤 → Gevent/Eventlet에서 동시성 오염.
#
# Celery 내부 구조:
#   task 인스턴스 = 워커 프로세스 내 싱글톤 (동일 태스크 타입은 1개 인스턴스)
#   task.request = 실행 요청별 Context 객체 (스레드/그린렛 로컬)
# =============================================================================

# task.request에 토큰 저장용 속성명
_CONTEXT_TOKENS_ATTR = "_selfhealing_context_tokens"


def _get_task_request(task: Any) -> Any | None:
    """
    task.request를 안전하게 가져온다.

    Direct Call 방어 (R1 보완):
    - 단위 테스트에서 task()처럼 직접 호출 시 task.request가 빈 Context이거나
      hasattr(task, 'request')가 False일 수 있다.
    - 실제 발생 사례: test_point_tasks.py L50 expire_points_task(),
      test_email_tasks.py L143 retry_failed_emails_task() 등
      → task.apply() 없이 함수 직접 호출하는 테스트 15건 이상 존재

    패턴 참조:
    - signal_hooks.py L506-508: request = sender.request if sender else None
    """
    if task is None:
        return None
    request = getattr(task, "request", None)
    if request is None:
        return None
    # Celery의 빈 Context 객체도 유효하지 않으므로 __class__ 확인
    # (직접 호출 시 request가 존재하지만 비어있을 수 있음)
    return request


# =============================================================================
# R3: 통합 리졸버 — Baggage 우선, Legacy Fallback, 1회만 Set
# =============================================================================
#
# 코드 근거:
# - 266_CELL_OTEL_PROPAGATION.md 3.3항: Baggage가 1순위, 레거시가 2순위 합의
# - 기존 270 v1.0의 결함:
#   3단계 cell_id = task.request.get("cell_id") → _current_cell_id.set() → token₁
#   4단계 restore_contextvars_from_baggage() → _current_cell_id.set() → token₂
#   → token₁이 고아가 되어 메모리 릭 + .reset() 시 ContextVar 스택 오염
#
# 해결: 단일 리졸버에서 "읽기 → 병합 → 1회 Set → 토큰 반환"
# =============================================================================


def _resolve_cell_id(task: Any) -> tuple[str | None, str]:
    """
    cell_id를 단일 진입점에서 결정. Baggage 우선, Legacy Fallback.

    266에서 합의한 우선순위를 구현:
    1. OTel Baggage (selfhealing.cell_id)
    2. Legacy 커스텀 헤더 (task.request.get("cell_id"))
    3. None (복원 불가)

    R3 변경: 이전처럼 순차 Set하지 않고, 최종 값만 1회 Set.

    Returns:
        (cell_id, source) — source는 "baggage" | "legacy_header" | "none"
        source를 반환하여 레거시 사용 비율 모니터링 가능 (R3 보완 조언)
    """
    # ── 1순위: OTel Baggage ──
    try:
        from opentelemetry import baggage as otel_baggage

        cell_id = otel_baggage.get_baggage("selfhealing.cell_id")
        if cell_id:
            return (cell_id, "baggage")
    except ImportError:
        pass  # OTel 미설치 환경
    except Exception:
        pass  # Baggage 파싱 실패

    # ── 2순위: Legacy 커스텀 헤더 ──
    request = _get_task_request(task)
    if request and hasattr(request, "get"):
        cell_id = request.get("cell_id")
        if cell_id:
            return (cell_id, "legacy_header")

    return (None, "none")


def _resolve_domain(task: Any) -> tuple[str | None, str]:
    """
    domain을 단일 진입점에서 결정. Baggage 우선, Legacy Fallback.

    _resolve_cell_id와 동일한 패턴.

    Returns:
        (domain, source) — source는 "baggage" | "legacy_header" | "none"
    """
    # ── 1순위: OTel Baggage ──
    try:
        from opentelemetry import baggage as otel_baggage

        domain = otel_baggage.get_baggage("selfhealing.domain")
        if domain:
            return (domain, "baggage")
    except ImportError:
        pass
    except Exception:
        pass

    # ── 2순위: Legacy 커스텀 헤더 ──
    request = _get_task_request(task)
    if request and hasattr(request, "get"):
        domain = request.get("domain")
        if domain:
            return (domain, "legacy_header")

    return (None, "none")


# =============================================================================
# R4: Causation 복원/정리 로직 완전 이관
# =============================================================================
#
# 코드 근거:
# - signal_hooks.py L462-555: _setup_causation_context() 전체 로직
#   → 의존성: causation_context 모듈만 사용 (signal_hooks 고유 로직 아님)
#   → 시그널 디스패칭 관심사와 무관한 순수 컨텍스트 복원 로직
# - signal_hooks.py L571-597: _cleanup_causation_context() 전체 로직
#   → 동일하게 순수 정리 로직
# - signal_hooks.py L558-575: _detect_causation_source() 보조 함수
#   → causation 복원의 보조 함수이므로 함께 이관
#
# 순환 참조 문제:
#   v1.0: signal_hooks.py → celery_context_utils.py → signal_hooks.py (상호 참조)
#   v2.0: signal_hooks.py → celery_context_utils.py (단방향 의존)
# =============================================================================

# Causation 컨텍스트 token 저장용 (task.request 속성)
_CAUSATION_TOKEN_ATTR = "_selfhealing_causation_token"


def _detect_causation_source(task_name: str) -> str:
    """
    Task 이름에서 causation source 유형 추론.

    signal_hooks.py L558-575에서 완전 이관.

    Returns:
        source 문자열 (celery_beat, management_cmd, scheduler, worker)
    """
    task_name_lower = task_name.lower()

    if any(pattern in task_name_lower for pattern in ["beat", "schedule", "periodic"]):
        return "celery_beat"

    if any(pattern in task_name_lower for pattern in ["manage", "command", "admin"]):
        return "management_cmd"

    if any(pattern in task_name_lower for pattern in ["cron", "cleanup", "expire"]):
        return "scheduler"

    return "worker"


def _setup_causation_context(task: Any, task_id: str, task_name: str) -> contextvars.Token | None:
    """
    Celery Task 시작 시 Causation Context 자동 복원 또는 시스템 Cascade 생성.

    signal_hooks.py L462-555에서 완전 이관.

    R4 변경: signal_hooks.py 대신 본 모듈에서 정의.
    R1 변경: token을 task.request에 저장 (기존 패턴 유지).
    R2 변경: token을 반환하여 TaskContextTokens에서 추적.
    """
    try:
        from selfhealing.context.causation_context import (
            CELERY_HEADER_CASCADE_ID,
            CELERY_HEADER_CHAIN_DEPTH,
            CELERY_HEADER_NAMESPACE,
            CELERY_HEADER_PARENT_EVENT,
            CausationInfo,
            _current_causation,
        )

        request = _get_task_request(task)
        if not request:
            return None

        headers = getattr(request, "headers", None) or {}
        cascade_id = headers.get(CELERY_HEADER_CASCADE_ID)

        if cascade_id:
            info = CausationInfo(
                cascade_id=cascade_id,
                parent_event_id=headers.get(CELERY_HEADER_PARENT_EVENT, ""),
                chain_depth=int(headers.get(CELERY_HEADER_CHAIN_DEPTH, "0")) + 1,
                namespace=headers.get(CELERY_HEADER_NAMESPACE, "global"),
                metadata={
                    "restored_from": "celery_signal",
                    "restored_at": datetime.now(timezone.utc).isoformat(),
                    "task_id": task_id,
                    "task_name": task_name,
                },
            )
        else:
            source = _detect_causation_source(task_name)
            info = CausationInfo(
                cascade_id=f"cascade-{uuid.uuid4().hex[:12]}",
                parent_event_id=f"SYSTEM_ROOT_{source}_{uuid.uuid4().hex[:8]}",
                chain_depth=0,
                namespace="global",
                metadata={
                    "system_source": source,
                    "auto_generated": True,
                    "task_id": task_id,
                    "task_name": task_name,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        token = _current_causation.set(info)
        setattr(request, _CAUSATION_TOKEN_ATTR, token)
        return token

    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"[ContextUtils] causation setup failed: {e}")
        return None


def _cleanup_causation_context(task: Any) -> None:
    """
    Celery Task 종료 시 Causation Context 정리.

    signal_hooks.py L571-597에서 완전 이관.
    """
    try:
        from selfhealing.context.causation_context import _current_causation

        request = _get_task_request(task)
        if not request:
            return

        token = getattr(request, _CAUSATION_TOKEN_ATTR, None)
        if token:
            _current_causation.reset(token)
            try:
                delattr(request, _CAUSATION_TOKEN_ATTR)
            except AttributeError:
                pass

    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"[ContextUtils] causation cleanup failed: {e}")


# =============================================================================
# R5: Strict Mode 설정
# =============================================================================
#
# 코드 근거:
# - settings/ 패키지의 Pydantic BaseSettings 패턴 (observability.py 등)
# - 개발 환경에서는 cell_id 없이도 실행 가능해야 하므로 기본값 False
# - 프로덕션에서 True로 설정하여 CRITICAL 컨텍스트 누락 시 Fail-Fast
# =============================================================================

_strict_cell_context: bool | None = None


def _is_strict_context_enabled() -> bool:
    """
    SELFHEALING_STRICT_CELL_CONTEXT 환경변수 확인. 캐싱 포함.

    Returns:
        True이면 CRITICAL 컨텍스트 누락 시 SelfHealingContextError 발생.
    """
    global _strict_cell_context
    if _strict_cell_context is None:
        import os
        _strict_cell_context = os.environ.get(
            "SELFHEALING_STRICT_CELL_CONTEXT", "false"
        ).lower() in ("true", "1", "yes", "on")
    return _strict_cell_context


# =============================================================================
# 메인 복원/정리 함수
# =============================================================================


def restore_all_task_context(
    task: Any,
    task_id: str,
    task_name: str,
    kwargs: dict | None = None,
) -> TaskContextTokens:
    """
    Celery 태스크의 모든 ContextVar를 일괄 복원.

    호출 위치: on_task_prerun() 내부 (signal_hooks.py)
    기존 개별 핸들러를 대체한다.

    v2.0 리뷰 반영 변경사항:
    - R1: 토큰을 task.request에 저장 (task 싱글톤 아님)
    - R2: Baggage 토큰을 dict[str, Token]으로 추적
    - R3: 통합 리졸버로 Baggage/Legacy 1회만 Set
    - R4: causation 로직을 본 모듈 내부에서 직접 실행
    - R5: CRITICAL 컨텍스트 누락 시 SelfHealingContextError 발생

    복원 순서:
    1. trace_id (kwargs → task_id fallback)           [OPTIONAL]
    2. celery_context                                 [OPTIONAL]
    3. causation (본 모듈 내부 — R4 이관)              [IMPORTANT]
    4. cell_id (통합 리졸버 — R3 Baggage 우선)         [CRITICAL]
    5. domain (통합 리졸버 — R3 Baggage 우선)          [OPTIONAL]

    Args:
        task: Celery task 인스턴스
        task_id: 태스크 ID
        task_name: 태스크 이름
        kwargs: 태스크 kwargs

    Returns:
        TaskContextTokens — cleanup_all_task_context()에 전달

    Raises:
        SelfHealingContextError: CRITICAL 컨텍스트 복원 실패 시
            (SELFHEALING_STRICT_CELL_CONTEXT=true인 경우에만)
    """
    tokens = TaskContextTokens()
    request = _get_task_request(task)

    # ── 1. trace_id 복원 [OPTIONAL] ──
    try:
        from selfhealing.audit.trace import (
            generate_celery_trace_id,
            set_trace_id,
            _celery_context_var,
        )

        trace_info = kwargs.get("trace_info") if kwargs else None
        if trace_info and trace_info.get("trace_id"):
            set_trace_id(trace_info["trace_id"])
        else:
            set_trace_id(generate_celery_trace_id(task_id))

        # celery_context 설정
        retries = getattr(request, "retries", 0) if request else 0
        _celery_context_var.set({
            "task_id": task_id,
            "task_name": task_name,
            "retries": retries,
        })
    except Exception as e:
        logger.debug(f"[ContextUtils] trace_id restore failed: {e}")

    # ── 2. causation 복원 [IMPORTANT] ──
    try:
        tokens.causation_token = _setup_causation_context(task, task_id, task_name)
    except Exception as e:
        # IMPORTANT: WARNING 로그 남기되 태스크는 계속 실행
        logger.warning(f"[ContextUtils] causation restore failed: {e}")

    # ── 3. cell_id 복원 [CRITICAL] — R3 통합 리졸버 ──
    try:
        cell_id, source = _resolve_cell_id(task)
        if cell_id:
            from selfhealing.context.cell_context import _current_cell_id
            tokens.cell_id_token = _current_cell_id.set(cell_id)
            logger.debug(
                f"[ContextUtils] cell_id restored: {cell_id} (source={source})"
            )
        elif _is_strict_context_enabled():
            # R5: CRITICAL 컨텍스트 누락 → Fail-Fast
            raise SelfHealingContextError(
                context_name="cell_id",
                task_name=task_name,
                detail=f"Neither OTel Baggage nor legacy header provided cell_id. "
                       f"Set SELFHEALING_STRICT_CELL_CONTEXT=false to disable.",
            )
    except SelfHealingContextError:
        raise  # Fail-Fast 예외는 그대로 전파
    except Exception as e:
        if _is_strict_context_enabled():
            raise SelfHealingContextError(
                context_name="cell_id",
                task_name=task_name,
                detail=str(e),
            ) from e
        logger.debug(f"[ContextUtils] cell_id restore failed: {e}")

    # ── 4. domain 복원 [OPTIONAL] — R3 통합 리졸버 ──
    try:
        domain, domain_source = _resolve_domain(task)
        if domain:
            from selfhealing.decorators.domain_tag import _current_domain
            tokens.domain_token = _current_domain.set(domain)
            logger.debug(
                f"[ContextUtils] domain restored: {domain} (source={domain_source})"
            )
    except ImportError:
        pass  # domain_tag 모듈 미존재 시 무시
    except Exception as e:
        logger.debug(f"[ContextUtils] domain restore failed: {e}")

    # ── R1: 토큰을 task.request에 저장 (postrun 정리용) ──
    if request is not None:
        try:
            setattr(request, _CONTEXT_TOKENS_ATTR, tokens)
        except AttributeError:
            # Direct Call 환경에서 request가 읽기 전용일 수 있음
            logger.debug("[ContextUtils] Cannot store tokens on task.request")

    return tokens


def cleanup_all_task_context(task: Any) -> None:
    """
    Celery 태스크 종료 시 모든 ContextVar 일괄 정리.

    호출 위치: on_task_postrun() 내부 (signal_hooks.py)
    기존 개별 정리 핸들러를 대체한다.

    v2.0 리뷰 반영 변경사항:
    - R1: task.request에서 토큰 조회 (task 싱글톤 아님)
    - R2: baggage_tokens 순회하며 일괄 reset
    - R4: causation 정리를 본 모듈 내부에서 직접 실행
    """
    request = _get_task_request(task)
    tokens: TaskContextTokens | None = (
        getattr(request, _CONTEXT_TOKENS_ATTR, None) if request else None
    )

    # ── 1. cell_id 정리 ──
    if tokens and tokens.cell_id_token:
        try:
            from selfhealing.context.cell_context import _current_cell_id
            _current_cell_id.reset(tokens.cell_id_token)
        except Exception as e:
            logger.debug(f"[ContextUtils] cell_id cleanup failed: {e}")

    # ── 2. domain 정리 ──
    if tokens and tokens.domain_token:
        try:
            from selfhealing.decorators.domain_tag import _current_domain
            _current_domain.reset(tokens.domain_token)
        except Exception as e:
            logger.debug(f"[ContextUtils] domain cleanup failed: {e}")

    # ── 3. baggage_tokens 일괄 정리 (R2) ──
    if tokens and tokens.baggage_tokens:
        for key, token in tokens.baggage_tokens.items():
            try:
                # 각 baggage 토큰은 해당 ContextVar의 reset에 사용
                # 구체적 ContextVar 매핑은 restore 시 함께 저장
                pass  # 266 구현 시 활성화
            except Exception as e:
                logger.debug(f"[ContextUtils] baggage token '{key}' cleanup failed: {e}")

    # ── 4. causation 정리 (R4: 본 모듈 내부) ──
    try:
        _cleanup_causation_context(task)
    except Exception as e:
        logger.debug(f"[ContextUtils] causation cleanup failed: {e}")

    # ── 5. trace_id / celery_context 정리 ──
    try:
        from selfhealing.audit.trace import clear_trace_id, clear_celery_context
        clear_trace_id()
        clear_celery_context()
    except Exception as e:
        logger.debug(f"[ContextUtils] trace_id cleanup failed: {e}")

    # ── 6. request 속성 정리 (R1: task.request에서 제거) ──
    if request is not None:
        try:
            delattr(request, _CONTEXT_TOKENS_ATTR)
        except AttributeError:
            pass  # 이미 제거되었거나 Direct Call 환경
```

---

## 3. 마이그레이션 계획

### 3.1 단계별 전환

**Phase 1**: `context/celery_context_utils.py` 생성 (신규 파일)
- `TaskContextTokens`, `SelfHealingContextError`, `ContextCriticality`
- `restore_all_task_context()`, `cleanup_all_task_context()`
- `_setup_causation_context()`, `_cleanup_causation_context()` (R4 이관)
- `_detect_causation_source()` (R4 이관)
- `_resolve_cell_id()`, `_resolve_domain()` (R3 통합 리졸버)

**Phase 2**: `signal_hooks.py`의 `on_task_prerun()` 내부에서
`restore_all_task_context()` 호출로 대체

```python
# Before (signal_hooks.py on_task_prerun L606-672):
@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, task=None, kwargs=None, **kw):
    set_trace_id(...)
    _celery_context_var.set(...)
    _setup_causation_context(...)

# After:
@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, task=None, kwargs=None, **kw):
    if not _config.enabled:
        return
    task_name = sender.name if sender else "unknown"
    if task_name in _config.excluded_tasks:
        return
    restore_all_task_context(sender, task_id, task_name, kwargs)
```

**Phase 3**: `celery_cell_propagation.py`의 `extract_cell_id_on_prerun()` 제거
(로직이 `restore_all_task_context()` 내부로 이동했으므로)

**Phase 4**: `celery_cell_propagation.py`의 `clear_cell_id_on_postrun()` 제거
(로직이 `cleanup_all_task_context()` 내부로 이동했으므로)

**Phase 5** (R4): `signal_hooks.py`에서 `_setup_causation_context()`,
`_cleanup_causation_context()`, `_detect_causation_source()` 삭제 후
하위 호환 re-export 추가

```python
# signal_hooks.py — R4 하위 호환 re-export (Deprecation 경고 포함)
# Phase 5 완료 후 다음 마이너 버전에서 제거 예정

def _setup_causation_context(sender, task_id, task_name):
    """Deprecated: Use celery_context_utils._setup_causation_context instead."""
    import warnings
    warnings.warn(
        "_setup_causation_context has moved to "
        "selfhealing.context.celery_context_utils._setup_causation_context",
        DeprecationWarning,
        stacklevel=2,
    )
    from selfhealing.context.celery_context_utils import _setup_causation_context as _impl
    return _impl(sender, task_id, task_name)
```

> **선택 이유**: 테스트 2개 파일에서 `from selfhealing.adapters.celery.signal_hooks
> import _setup_causation_context`를 직접 import하고 있으므로 (3.4항 참조),
> 즉시 삭제 대신 DeprecationWarning re-export를 거쳐 다음 버전에서 완전 제거한다.
> re-export 없이 즉시 삭제하면 CI에서 `ImportError`가 발생한다.

### 3.2 제거 대상 코드 목록

| 파일 | 함수 | 라인 | 이동 위치 |
|------|------|------|----------|
| `celery_cell_propagation.py` | `extract_cell_id_on_prerun()` | L112-122 | `restore_all_task_context()` |
| `celery_cell_propagation.py` | `clear_cell_id_on_postrun()` | L125-143 | `cleanup_all_task_context()` |
| `signal_hooks.py` | trace_id/celery_context 설정 (on_task_prerun 내부) | L640-672 | `restore_all_task_context()` |
| `signal_hooks.py` | trace_id/celery_context 정리 (on_task_postrun 내부) | L700-714 | `cleanup_all_task_context()` |
| `signal_hooks.py` | `_setup_causation_context()` | L462-555 | `celery_context_utils._setup_causation_context()` (R4) |
| `signal_hooks.py` | `_cleanup_causation_context()` | L571-597 | `celery_context_utils._cleanup_causation_context()` (R4) |
| `signal_hooks.py` | `_detect_causation_source()` | L558-575 | `celery_context_utils._detect_causation_source()` (R4) |

### 3.3 유지되는 코드

| 파일 | 함수 | 이유 |
|------|------|------|
| `celery_cell_propagation.py` | `add_cell_id_to_task()` (`before_task_publish`) | 발행 측 로직 — 수신 측 통합과 무관 |
| `celery_propagation.py` | `on_before_task_publish()` (`before_task_publish`) | 발행 측 Causation 주입 — 수신 측 통합과 무관 |
| `signal_hooks.py` | `on_before_task_publish()` (`before_task_publish`) | 발행 측 Causation 헤더 주입 |
| `signal_hooks.py` | `on_task_failure()`, `on_task_success()`, `on_task_retry()` | CB/DLQ/포렌식/메트릭 — 컨텍스트 복원과 무관 |

### 3.4 Import 경로 수정 대상 (R4 보완)

`_setup_causation_context` / `_cleanup_causation_context` 이관 시
**기존 직접 import를 사용하는 파일**을 모두 수정해야 한다.

프로젝트 전체 검색 결과 (`grep -r "from selfhealing.adapters.celery.signal_hooks import _setup_causation_context"`):

| 파일 | 유형 | 수정 내용 |
|------|------|----------|
| `tests/unit/audit/trace/test_celery_causation_signals.py` L85, L102, L116 | 테스트 import | `from selfhealing.context.celery_context_utils import _setup_causation_context` |
| `tests/unit/audit/trace/test_celery_causation_signals.py` L184, L195 | 테스트 import | `from selfhealing.context.celery_context_utils import _cleanup_causation_context` |
| `tests/unit/adapters/test_celery_causation_propagation.py` L271, L300 | 테스트 import | `from selfhealing.context.celery_context_utils import _setup_causation_context` |
| `context/celery_cell_propagation.py` L119 | 주석 참조 | 주석 내 경로 갱신 |

> Phase 5의 DeprecationWarning re-export가 있으므로 즉시 CI가 깨지지는 않지만,
> 테스트 import도 함께 수정하여 경고 없는 깨끗한 상태를 목표로 한다.

참조하는 문서 (경로 갱신 필요):

| 문서 | 참조 위치 |
|------|----------|
| `135_EXCEPTION_HANDLER_ENHANCEMENT.md` | L348 |
| `136_EXCEPTION_HANDLER_6_ENHANCEMENTS.md` | L451 |
| `263_CELL_TAGGER.md` | L605 |
| `76_CASCADE_EVENT_AUDIT.md` | L2533-2534 |

### 3.5 Non-Retryable 예외 연동 (R5)

`SelfHealingContextError`가 `task_prerun` 시그널에서 raise되면,
Celery는 이를 일반 태스크 실패로 간주한다.

**문제**: 현재 프로젝트의 태스크 다수가 `autoretry_for=(Exception,)`으로 설정되어 있다.

코드 근거:

```python
# adapters/celery/tasks/cell_evacuation.py L22-27
@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.notify_cell_isolation",
    autoretry_for=(Exception,),  # ← 모든 Exception 재시도
    max_retries=3,
    ...
)
```

`SelfHealingContextError`는 `Exception`의 하위 클래스이므로,
`autoretry_for=(Exception,)`에 걸려 **cell_id 없이 3번 재시도**된다.
이는 크로스-테넌트 오염 위험을 3배로 증폭시킨다.

**해결 — 선택지 분석**:

| 방안 | 설명 | 장점 | 단점 |
|------|------|------|------|
| A. `dont_autoretry_for` 등록 | 각 태스크에 `dont_autoretry_for=(SelfHealingContextError,)` 추가 | Celery 네이티브 | 모든 태스크 수정 필요 |
| B. `non_retryable_exceptions` 통합 | 기존 `RetryConfig.non_retryable_exceptions`에 등록 | 기존 인프라 활용 | selfhealing retry_handler 통과 태스크만 적용 |
| C. `on_task_failure`에서 감지 | signal_hooks.py `on_task_failure()`에서 `isinstance` 체크 후 DLQ 직행 | 중앙 집중 | 이미 재시도 발생 후 감지 |

**선택: A+B 병행**

- **A**: `setup_selfhealing_signals()` 호출 시 Celery 앱 설정에
  `SelfHealingContextError`를 글로벌 `dont_autoretry_for`에 등록.
  프로젝트의 모든 태스크가 `@shared_task`를 사용하므로 Celery base task 클래스에서
  일괄 적용 가능.
- **B**: 기존 `RetryConfig.non_retryable_exceptions` 인프라에도 등록하여
  selfhealing retry_handler를 통과하는 태스크에서도 재시도 차단.

> **방안 C 제외 이유**: `on_task_failure()`은 태스크 실행이 이미 완료(실패)된 후에
> 호출된다. `SelfHealingContextError`의 목적은 **태스크 실행 자체를 차단**하는 것이므로,
> 실행 후 감지하는 C는 근본적 해결이 아니다.

코드 근거 — 기존 Non-Retryable 인프라:
```python
# services/retry_handler/policy.py L175-181
def _should_retry(self, exception: Exception, attempt: int) -> bool:
    if isinstance(exception, self._config.non_retryable_exceptions):
        return False  # ← 즉시 실패, 재시도 하지 않음

# core/hedging/exceptions.py L53-66
class NonRetryableHedgingError(HedgingError):
    """재시도 불가 에러 - 즉시 실패 처리."""
    # ← SelfHealingContextError와 동일한 "재시도 무의미" 패턴
```

구현 예시:
```python
# adapters/celery/signal_hooks.py — setup_selfhealing_signals() 내부
def setup_selfhealing_signals(app=None):
    """..."""
    # R5: CRITICAL 컨텍스트 실패 시 재시도 방지
    if app:
        from selfhealing.context.celery_context_utils import SelfHealingContextError

        # Celery base task 클래스의 dont_autoretry_for에 등록
        base_task = app.Task
        existing = getattr(base_task, 'dont_autoretry_for', ()) or ()
        if SelfHealingContextError not in existing:
            base_task.dont_autoretry_for = (*existing, SelfHealingContextError)
```

---

## 4. 변경 범위

| 파일 | 변경 | 유형 | 리뷰 항목 |
|------|------|------|----------|
| `context/celery_context_utils.py` | 신규 생성 — 전체 유틸리티 | 필수 | R1-R5 |
| `adapters/celery/signal_hooks.py` | `on_task_prerun()`/`on_task_postrun()` 유틸리티 호출로 대체 | 필수 | — |
| `adapters/celery/signal_hooks.py` | `_setup_causation_context` 등 3개 함수 → DeprecationWarning re-export | 필수 | R4 |
| `adapters/celery/signal_hooks.py` | `setup_selfhealing_signals()` — `dont_autoretry_for` 등록 | 필수 | R5 |
| `context/celery_cell_propagation.py` | `extract_cell_id_on_prerun()`, `clear_cell_id_on_postrun()` 제거 | 필수 | — |
| `tests/unit/audit/trace/test_celery_causation_signals.py` | import 경로 변경 (5개소) | 필수 | R4 |
| `tests/unit/adapters/test_celery_causation_propagation.py` | import 경로 변경 (2개소) | 필수 | R4 |

---

## 5. v1.0 → v2.0 변경 요약 (리뷰 반영)

### 5.1 R1: Thread Safety — `task.request` 기반 토큰 저장

**문제**: v1.0에서 `setattr(task, _CONTEXT_TOKENS_ATTR, tokens)`로 토큰을
task 객체(싱글톤)에 저장. Gevent/Eventlet/Thread 풀에서 동시 실행 시 토큰 덮어쓰기.

**코드 근거**:
- `signal_hooks.py` L554: `setattr(request, _CAUSATION_TOKEN_ATTR, token)`
  → 기존 causation은 이미 `request`에 저장하는 **올바른 패턴** 사용 중
- `celery_cell_propagation.py` L121: `task._cell_id_token = ...`
  → **결함 패턴**. task 싱글톤에 저장

**변경**: `setattr(task, ...)` → `setattr(request, ...)`
+ `_get_task_request()` 헬퍼로 Direct Call 방어 (테스트 환경 15건 이상)

### 5.2 R2: Baggage 토큰 추적 — `contextvars.Token` 명시적 타이핑

**문제**: v1.0에서 266의 `restore_contextvars_from_baggage()`가 토큰을 버림 (`→ None`).
cleanup 시 `.reset()` 불가 → 메모리 릭.

**코드 근거**:
- `266_CELL_OTEL_PROPAGATION.md` 3.3항: `_current_cell_id.set(cell_id)` 반환값 무시
- `cell_context.py` L28: `→ contextvars.Token[str | None]` — 프로젝트 유일한 명시적 타이핑

**변경**:
- `TaskContextTokens` 필드를 `Any` → `contextvars.Token[...] | None`으로 변경
- `baggage_tokens: dict[str, contextvars.Token]` 필드 추가
- 266 구현 시 `restore_contextvars_from_baggage()` → `dict[str, Token]` 반환으로 수정 필요

### 5.3 R3: Baggage/Legacy 이중 Set 제거 — 통합 리졸버

**문제**: v1.0의 3단계(Legacy cell_id set → token₁) + 4단계(Baggage set → token₂)
순차 실행으로 token₁이 고아 → ContextVar 스택 오염.

**코드 근거**:
- `266_CELL_OTEL_PROPAGATION.md`: "Baggage가 1순위, 레거시가 2순위" 합의
- v1.0 코드와 266 합의 간 불일치

**변경**:
- `_resolve_cell_id(task) → (cell_id, source)` 통합 리졸버 도입
- "Baggage 읽기 → 없으면 Legacy 읽기 → 최종 값 1회만 Set → 토큰 1개만 관리"
- `source` 반환으로 레거시 사용 비율 모니터링 가능 (향후 메트릭 연동)

### 5.4 R4: Causation 로직 완전 이관 — 순환 참조 제거

**문제**: v1.0에서 `celery_context_utils.py → signal_hooks.py`
역방향 import로 상호 참조 발생.

**코드 근거**:
- `signal_hooks.py` L462-555: `_setup_causation_context()`의 의존성은
  `causation_context` 모듈뿐. signal_hooks 고유 로직이 아님.
- `signal_hooks.py` L558-575: `_detect_causation_source()` — causation 보조 함수

**변경**:
- 3개 함수(`_setup_causation_context`, `_cleanup_causation_context`,
  `_detect_causation_source`)를 `celery_context_utils.py`로 완전 이관
- `signal_hooks.py`에 DeprecationWarning re-export 추가 (테스트 하위 호환)
- 의존 방향: `signal_hooks.py → celery_context_utils.py` (단방향)

**테스트 impact**: 7개 import 수정 필요 (3.4항)

### 5.5 R5: Fail-Open/Fail-Fast 정책 분리 — `SelfHealingContextError`

**문제**: v1.0에서 모든 컨텍스트 복원 실패를 `logger.debug()`로 조용히 무시 (Fail-Open).
cell_id 같은 라우팅/격리 컨텍스트 누락 시 크로스-테넌트 오염 위험.

**코드 근거**:
- `cell_context.py`: `_current_cell_id` → DB 라우터, 캐시 격벽에 사용
- `NonRetryableHedgingError` (hedging/exceptions.py L53): 재시도 무의미 예외 패턴 선례
- `RetryConfig.non_retryable_exceptions` (retry_handler/models.py L56): 기존 인프라

**변경**:
- `ContextCriticality` Enum: `CRITICAL` / `IMPORTANT` / `OPTIONAL` 3단계
- `SelfHealingContextError`: CRITICAL 컨텍스트 실패 시 Raise
- `SELFHEALING_STRICT_CELL_CONTEXT` 환경변수로 Strict Mode 제어
  (기본값 `false` — 개발 환경 호환, 프로덕션 `true`)
- `dont_autoretry_for` 글로벌 등록으로 무한 재시도 차단 (3.5항)

---

## 6. 의존성

추가 패키지 없음. 기존 모듈 간 import 경로만 재구성.

| 모듈 | 의존 방향 | 비고 |
|------|----------|------|
| `signal_hooks.py` → `celery_context_utils.py` | 단방향 (v2.0) | R4: 순환 참조 제거 |
| `celery_context_utils.py` → `audit/trace.py` | 기존 유지 | trace_id 복원 |
| `celery_context_utils.py` → `context/causation_context.py` | 기존 `signal_hooks.py`에서 이전 | R4 이관 |
| `celery_context_utils.py` → `context/cell_context.py` | 기존 `celery_cell_propagation.py`에서 이전 | cell_id 복원 |
| `celery_context_utils.py` → `opentelemetry.baggage` (optional) | R3 통합 리졸버 | ImportError 시 무시 |

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `266_CELL_OTEL_PROPAGATION.md` | OTel Baggage 복원 경로 제공 (선행 의존). R2·R3에서 266의 `restore_contextvars_from_baggage()` 시그니처 변경을 요구 |
| `263_CELL_TAGGER.md` | `_current_cell_id` ContextVar 제공자. `contextvars.Token` 타이핑 선례 (R2) |
| `adapters/celery/signal_hooks.py` | 기존 `on_task_prerun()` 코드. R4 이관 후 시그널 디스패처 역할만 유지 |
| `context/celery_cell_propagation.py` | 기존 cell_id 복원/정리 코드 (통합 후 부분 제거) |
| `context/celery_propagation.py` | Causation 발행 측 코드 (유지) |
| `core/hedging/exceptions.py` | `NonRetryableHedgingError` — `SelfHealingContextError` 패턴 선례 (R5) |
| `services/retry_handler/models.py` | `non_retryable_exceptions` 인프라 (R5 연동) |
