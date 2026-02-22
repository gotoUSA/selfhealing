# 270. Celery Context Consolidation — 태스크 컨텍스트 추출 유틸리티 통합

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
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

```python
"""
context/celery_context_utils.py — Celery 태스크 컨텍스트 통합 복원/정리.

기존 분산된 복원 로직을 단일 진입점으로 통합.

코드 근거:
- adapters/celery/signal_hooks.py L462-537: _setup_causation_context() 패턴
- context/celery_cell_propagation.py L112-122: cell_id 복원 패턴
- observability/baggage.py: restore_contextvars_from_baggage() (266 이후)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TaskContextTokens:
    """
    태스크 수명 동안 보관할 ContextVar 토큰 모음.

    task_postrun에서 일괄 정리(reset)하기 위해 토큰을 추적한다.

    패턴 참조:
    - signal_hooks.py L458: _CAUSATION_TOKEN_ATTR로 task.request에 저장
    - celery_cell_propagation.py L129: task._cell_id_token으로 저장
    → 통합하여 단일 dataclass로 관리
    """

    cell_id_token: Any = None
    causation_token: Any = None
    domain_token: Any = None
    # 확장 가능: 새 ContextVar 추가 시 필드만 추가


# task 객체에 토큰 저장용 속성명
_CONTEXT_TOKENS_ATTR = "_selfhealing_context_tokens"


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

    복원 순서:
    1. trace_id (kwargs → task_id fallback)
    2. celery_context
    3. causation (_setup_causation_context 위임)
    4. cell_id (task.request.get("cell_id"))
    5. OTel Baggage → ContextVar (266 이후)

    Args:
        task: Celery task 인스턴스
        task_id: 태스크 ID
        task_name: 태스크 이름
        kwargs: 태스크 kwargs

    Returns:
        TaskContextTokens — cleanup_all_task_context()에 전달
    """
    tokens = TaskContextTokens()

    # ── 1. trace_id 복원 ──
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
        request = task.request if task else None
        retries = getattr(request, "retries", 0) if request else 0
        _celery_context_var.set({
            "task_id": task_id,
            "task_name": task_name,
            "retries": retries,
        })
    except Exception as e:
        logger.debug(f"[ContextUtils] trace_id restore failed: {e}")

    # ── 2. causation 복원 ──
    try:
        from selfhealing.adapters.celery.signal_hooks import _setup_causation_context
        _setup_causation_context(task, task_id, task_name)
    except Exception as e:
        logger.debug(f"[ContextUtils] causation restore failed: {e}")

    # ── 3. cell_id 복원 ──
    try:
        if task and hasattr(task.request, "get"):
            cell_id = task.request.get("cell_id")
            if cell_id:
                from selfhealing.context.cell_context import _current_cell_id
                tokens.cell_id_token = _current_cell_id.set(cell_id)
    except Exception as e:
        logger.debug(f"[ContextUtils] cell_id restore failed: {e}")

    # ── 4. OTel Baggage → ContextVar 복원 (266 이후 활성화) ──
    try:
        from selfhealing.observability.baggage import restore_contextvars_from_baggage
        restore_contextvars_from_baggage()
    except ImportError:
        pass  # 266 미구현 시 무시
    except Exception as e:
        logger.debug(f"[ContextUtils] baggage restore failed: {e}")

    # 토큰을 task에 저장 (postrun 정리용)
    if task:
        setattr(task, _CONTEXT_TOKENS_ATTR, tokens)

    return tokens


def cleanup_all_task_context(task: Any) -> None:
    """
    Celery 태스크 종료 시 모든 ContextVar 일괄 정리.

    호출 위치: on_task_postrun() 내부 (signal_hooks.py)
    기존 개별 정리 핸들러를 대체한다.

    패턴 참조:
    - signal_hooks.py L575-597: _cleanup_causation_context()
    - celery_cell_propagation.py L125-132: clear_cell_id_on_postrun()
    """
    tokens: TaskContextTokens | None = getattr(task, _CONTEXT_TOKENS_ATTR, None)

    # ── 1. cell_id 정리 ──
    if tokens and tokens.cell_id_token:
        try:
            from selfhealing.context.cell_context import _current_cell_id
            _current_cell_id.reset(tokens.cell_id_token)
        except Exception as e:
            logger.debug(f"[ContextUtils] cell_id cleanup failed: {e}")

    # ── 2. causation 정리 ──
    try:
        from selfhealing.adapters.celery.signal_hooks import _cleanup_causation_context
        _cleanup_causation_context(task)
    except Exception as e:
        logger.debug(f"[ContextUtils] causation cleanup failed: {e}")

    # ── 3. trace_id / celery_context 정리 ──
    try:
        from selfhealing.audit.trace import clear_trace_id, clear_celery_context
        clear_trace_id()
        clear_celery_context()
    except Exception as e:
        logger.debug(f"[ContextUtils] trace_id cleanup failed: {e}")

    # ── 4. task 속성 정리 ──
    if task and hasattr(task, _CONTEXT_TOKENS_ATTR):
        delattr(task, _CONTEXT_TOKENS_ATTR)
```

---

## 3. 마이그레이션 계획

### 3.1 단계별 전환

**Phase 1**: `context/celery_context_utils.py` 생성 (신규 파일)

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

### 3.2 제거 대상 코드 목록

| 파일 | 함수 | 라인 | 이동 위치 |
|------|------|------|----------|
| `celery_cell_propagation.py` | `extract_cell_id_on_prerun()` | L112-122 | `restore_all_task_context()` |
| `celery_cell_propagation.py` | `clear_cell_id_on_postrun()` | L125-143 | `cleanup_all_task_context()` |
| `signal_hooks.py` | trace_id/celery_context 설정 (on_task_prerun 내부) | L640-672 | `restore_all_task_context()` |
| `signal_hooks.py` | trace_id/celery_context 정리 (on_task_postrun 내부) | L700-714 | `cleanup_all_task_context()` |

### 3.3 유지되는 코드

| 파일 | 함수 | 이유 |
|------|------|------|
| `celery_cell_propagation.py` | `add_cell_id_to_task()` (`before_task_publish`) | 발행 측 로직 — 수신 측 통합과 무관 |
| `celery_propagation.py` | `on_before_task_publish()` (`before_task_publish`) | 발행 측 Causation 주입 — 수신 측 통합과 무관 |
| `signal_hooks.py` | `on_before_task_publish()` (`before_task_publish`) | 발행 측 Causation 헤더 주입 |
| `signal_hooks.py` | `_setup_causation_context()` | 내부 함수로 `restore_all_task_context()`에서 호출 (재사용) |
| `signal_hooks.py` | `_cleanup_causation_context()` | 내부 함수로 `cleanup_all_task_context()`에서 호출 (재사용) |

---

## 4. 변경 범위

| 파일 | 변경 | 유형 |
|------|------|------|
| `context/celery_context_utils.py` | 신규 생성 — `restore_all_task_context()`, `cleanup_all_task_context()` | 필수 |
| `adapters/celery/signal_hooks.py` | `on_task_prerun()`/`on_task_postrun()` 내부 로직을 유틸리티 호출로 대체 | 필수 |
| `context/celery_cell_propagation.py` | `extract_cell_id_on_prerun()`, `clear_cell_id_on_postrun()` 제거 | 필수 |

---

## 5. 의존성

추가 패키지 없음. 기존 모듈 간 import 경로만 재구성.

---

## 6. 관련 문서

| 문서 | 관계 |
|------|------|
| `266_CELL_OTEL_PROPAGATION.md` | OTel Baggage 복원 경로 제공 (선행 의존) |
| `263_CELL_TAGGER.md` | `_current_cell_id` ContextVar 제공자 |
| `adapters/celery/signal_hooks.py` | 기존 `on_task_prerun()`/`_setup_causation_context()` 코드 |
| `context/celery_cell_propagation.py` | 기존 cell_id 복원/정리 코드 (통합 후 부분 제거) |
| `context/celery_propagation.py` | Causation 발행 측 코드 (유지) |
