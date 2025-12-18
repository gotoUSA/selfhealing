# Phase 1: Exception Hook

## 목적

프레임워크 미들웨어를 통과하지 않는 **전역 예외**를 자동으로 캡처.

**캡처 대상:**
- 미들웨어 도달 전 예외
- Background thread 예외
- Celery task 예외
- 비동기 작업 예외

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    Python Runtime                                │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │ Main Thread │  │ Worker      │  │ Async       │             │
│  │ Exception   │  │ Thread      │  │ Exception   │             │
│  │             │  │ Exception   │  │             │             │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
│         │                │                │                     │
│         ▼                ▼                ▼                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              sys.excepthook (Global)                     │    │
│  │              threading.excepthook (Thread)               │    │
│  │              asyncio exception handler                   │    │
│  └─────────────────────────────────────────────────────────┘    │
│                           │                                      │
│                           ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              SelfHealing Exception Hook                  │    │
│  │  - 예외 정보 수집                                        │    │
│  │  - 이벤트 생성                                           │    │
│  │  - HTTP Exporter로 전송                                  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                           │                                      │
│                           ▼                                      │
│                   HTTP Exporter                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 구현 상세

### 1. Exception Hook 모듈

```python
"""
selfhealing/instrument/exception_hook.py
"""

import asyncio
import logging
import sys
import threading
import traceback
from types import TracebackType
from typing import Any, Callable, Optional, Type

logger = logging.getLogger(__name__)

# 원본 핸들러 저장
_original_excepthook: Optional[Callable] = None
_original_threading_excepthook: Optional[Callable] = None
_original_asyncio_handler: Optional[Callable] = None

_installed = False


def install_exception_hook() -> None:
    """
    전역 예외 훅 설치.
    
    설치되는 훅:
    - sys.excepthook: 메인 스레드 미처리 예외
    - threading.excepthook: 워커 스레드 미처리 예외
    - asyncio exception handler: 비동기 예외
    """
    global _installed, _original_excepthook, _original_threading_excepthook
    
    if _installed:
        return
    
    # 1. sys.excepthook 설치
    _original_excepthook = sys.excepthook
    sys.excepthook = _selfhealing_excepthook
    
    # 2. threading.excepthook 설치 (Python 3.8+)
    if hasattr(threading, "excepthook"):
        _original_threading_excepthook = threading.excepthook
        threading.excepthook = _selfhealing_threading_excepthook
    
    # 3. asyncio exception handler 설치
    _install_asyncio_handler()
    
    _installed = True
    logger.info("[ExceptionHook] Installed global exception hooks")


def uninstall_exception_hook() -> None:
    """예외 훅 제거"""
    global _installed, _original_excepthook, _original_threading_excepthook
    
    if not _installed:
        return
    
    # 원본 복원
    if _original_excepthook:
        sys.excepthook = _original_excepthook
    
    if _original_threading_excepthook and hasattr(threading, "excepthook"):
        threading.excepthook = _original_threading_excepthook
    
    _installed = False
    logger.info("[ExceptionHook] Uninstalled global exception hooks")


def _selfhealing_excepthook(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_tb: Optional[TracebackType],
) -> None:
    """
    메인 스레드 예외 처리기.
    """
    # 이벤트 기록
    _record_exception(
        exc_type=exc_type,
        exc_value=exc_value,
        exc_tb=exc_tb,
        context="main_thread",
    )
    
    # 원본 핸들러 호출 (기본 동작 유지)
    if _original_excepthook:
        _original_excepthook(exc_type, exc_value, exc_tb)


def _selfhealing_threading_excepthook(args: threading.ExceptHookArgs) -> None:
    """
    워커 스레드 예외 처리기 (Python 3.8+).
    """
    _record_exception(
        exc_type=args.exc_type,
        exc_value=args.exc_value,
        exc_tb=args.exc_traceback,
        context="worker_thread",
        thread_name=args.thread.name if args.thread else "unknown",
    )
    
    # 원본 핸들러 호출
    if _original_threading_excepthook:
        _original_threading_excepthook(args)


def _install_asyncio_handler() -> None:
    """asyncio 예외 핸들러 설치"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # 이벤트 루프가 없는 경우
        return
    
    original_handler = loop.get_exception_handler()
    
    def selfhealing_asyncio_handler(loop, context):
        """asyncio 예외 핸들러"""
        exception = context.get("exception")
        message = context.get("message", "")
        
        if exception:
            _record_exception(
                exc_type=type(exception),
                exc_value=exception,
                exc_tb=exception.__traceback__,
                context="asyncio",
                message=message,
            )
        
        # 원본 핸들러 호출
        if original_handler:
            original_handler(loop, context)
        else:
            loop.default_exception_handler(context)
    
    loop.set_exception_handler(selfhealing_asyncio_handler)


def _record_exception(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_tb: Optional[TracebackType],
    context: str,
    **extra
) -> None:
    """
    예외를 이벤트로 기록.
    """
    # 무시할 예외 타입
    if exc_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
        return
    
    try:
        from selfhealing.exporter.events import emit_event, EventType
        
        # Traceback 포맷팅
        tb_str = ""
        if exc_tb:
            tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        
        emit_event(
            EventType.EXCEPTION_CAUGHT,
            error_type=exc_type.__name__,
            error_message=str(exc_value),
            error_traceback=tb_str,
            details={
                "context": context,
                "module": exc_type.__module__,
                **extra,
            }
        )
    
    except Exception as e:
        # 이벤트 기록 실패는 조용히 처리
        logger.debug(f"[ExceptionHook] Failed to record exception: {e}")
```

### 2. Celery Task Exception Hook

```python
"""
selfhealing/instrument/celery_hook.py
"""

import logging
import traceback
from typing import Any, Optional

logger = logging.getLogger(__name__)

_celery_instrumented = False


def instrument_celery() -> bool:
    """
    Celery task 예외 자동 캡처 설정.
    
    Celery가 설치되어 있지 않으면 무시.
    """
    global _celery_instrumented
    
    if _celery_instrumented:
        return True
    
    try:
        from celery import signals
        
        @signals.task_failure.connect
        def on_task_failure(
            sender=None,
            task_id=None,
            exception=None,
            args=None,
            kwargs=None,
            traceback=None,
            einfo=None,
            **kw
        ):
            """Task 실패 시 이벤트 기록"""
            _record_task_failure(
                task_name=sender.name if sender else "unknown",
                task_id=task_id,
                exception=exception,
                args=args,
                kwargs=kwargs,
                tb=traceback,
            )
        
        @signals.task_retry.connect
        def on_task_retry(
            sender=None,
            reason=None,
            request=None,
            **kw
        ):
            """Task 재시도 시 이벤트 기록"""
            _record_task_retry(
                task_name=sender.name if sender else "unknown",
                task_id=request.id if request else None,
                reason=str(reason),
            )
        
        _celery_instrumented = True
        logger.info("[CeleryHook] Celery task exception hooks installed")
        return True
    
    except ImportError:
        logger.debug("[CeleryHook] Celery not installed, skipping")
        return False
    except Exception as e:
        logger.error(f"[CeleryHook] Failed to instrument Celery: {e}")
        return False


def _record_task_failure(
    task_name: str,
    task_id: Optional[str],
    exception: Optional[Exception],
    args: Optional[tuple],
    kwargs: Optional[dict],
    tb: Optional[Any],
) -> None:
    """Task 실패 이벤트 기록"""
    try:
        from selfhealing.exporter.events import emit_event, EventType
        
        tb_str = ""
        if tb:
            tb_str = str(tb)
        
        emit_event(
            EventType.EXCEPTION_CAUGHT,
            error_type=type(exception).__name__ if exception else "Unknown",
            error_message=str(exception) if exception else "",
            error_traceback=tb_str,
            details={
                "context": "celery_task",
                "task_name": task_name,
                "task_id": task_id,
                "args": str(args)[:500] if args else None,
                "kwargs": str(kwargs)[:500] if kwargs else None,
            }
        )
    except Exception as e:
        logger.debug(f"[CeleryHook] Failed to record task failure: {e}")


def _record_task_retry(
    task_name: str,
    task_id: Optional[str],
    reason: str,
) -> None:
    """Task 재시도 이벤트 기록"""
    try:
        from selfhealing.exporter.events import emit_event, EventType
        
        emit_event(
            EventType.RETRY_ATTEMPTED,
            details={
                "context": "celery_task",
                "task_name": task_name,
                "task_id": task_id,
                "reason": reason,
            }
        )
    except Exception as e:
        logger.debug(f"[CeleryHook] Failed to record task retry: {e}")
```

---

## 캡처되는 예외 컨텍스트

| 컨텍스트 | 훅 | 추가 정보 |
|---------|-----|----------|
| `main_thread` | sys.excepthook | - |
| `worker_thread` | threading.excepthook | thread_name |
| `asyncio` | loop.set_exception_handler | message |
| `celery_task` | celery.signals | task_name, task_id, args, kwargs |

---

## 무시되는 예외

```python
IGNORED_EXCEPTIONS = (
    KeyboardInterrupt,  # Ctrl+C
    SystemExit,         # sys.exit()
    GeneratorExit,      # Generator close
)
```

---

## 이벤트 형식

```json
{
  "type": "exception.caught",
  "timestamp": "2025-12-18T10:30:00.123Z",
  "error_type": "ValueError",
  "error_message": "Invalid payment amount",
  "error_traceback": "Traceback (most recent call last):\n  File ...",
  "details": {
    "context": "celery_task",
    "task_name": "shopping.tasks.process_payment",
    "task_id": "abc123",
    "module": "shopping.tasks"
  }
}
```

---

## 통합 흐름

```python
# selfhealing/__init__.py의 init() 함수에서 자동 호출

def init(api_key, ...):
    # ...
    
    # Exception Hook 설치
    from selfhealing.instrument.exception_hook import install_exception_hook
    install_exception_hook()
    
    # Celery Hook 설치 (Celery가 있는 경우)
    from selfhealing.instrument.celery_hook import instrument_celery
    instrument_celery()
    
    # ...
```

---

## 다음 단계

→ [04-PHASE2-CLOUD-DASHBOARD.md](04-PHASE2-CLOUD-DASHBOARD.md)
