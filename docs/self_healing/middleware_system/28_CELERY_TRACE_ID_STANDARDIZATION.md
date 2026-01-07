# 28. Celery Task trace_id 표준화 및 자동 주입 구현 계획

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | 1.0.0 |
| 작성일 | 2026-01-07 |
| 선행 문서 | 25_RBAC_AUDIT_INTEGRATION_PLAN.md |
| 관련 리뷰 | RBAC-Audit trace_id 일관성 피드백 |

---

## 0. 배경: 왜 이 개선이 필요한가?

### 0.1 현재 문제점 (코드 근거)

**파일**: `packages/selfhealing-python/src/selfhealing/audit/trace.py` L268-269

```python
# 현재 구현: UUID 기반 자체 생성
trace_id = f"INTERNAL_BEAT_{generate_trace_id()}"
# 결과 예시: INTERNAL_BEAT_req-a1b2c3d4
```

| 문제 | 영향 |
|------|------|
| Flower UI에서 검색 불가 | Celery Task ID와 무관한 UUID → 1:1 매칭 불가능 |
| 재시도 추적 불가 | 재시도마다 새 UUID 생성 → 동일 작업 연결 불가 |
| 수동 코드 필요 | 각 Task에서 `with restore_trace_from_celery(...)` 작성 필요 → 휴먼 에러 가능 |
| 패턴 불일치 | `chaos_scheduler.py`는 `self.request.id` 사용, `dlq_replay.py`는 UUID 사용 |

### 0.2 개선 목표

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         개선 목표                                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1. trace_id 패턴 표준화: CELERY_{task_id}                              │
│     └── Flower UI에서 직접 검색 가능                                    │
│                                                                         │
│  2. task_prerun 시그널로 자동 주입                                      │
│     └── 개발자가 수동으로 trace_id 설정 불필요                          │
│                                                                         │
│  3. celery_context 메타데이터 이중화                                    │
│     └── Grafana 쿼리 시 문자열 파싱 불필요                              │
│                                                                         │
│  4. task_postrun에서 정리                                               │
│     └── Worker 재사용 시 이전 trace_id 잔존 방지                        │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 1. 구현 순서

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           구현 순서 (의존성 기반)                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Phase 1: trace.py 유틸리티 함수 추가                                    │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 1.1 generate_celery_trace_id(task_id) 함수 추가                   │   │
│  │ 1.2 CeleryTraceContext 컨텍스트 변수 추가                         │   │
│  │ 1.3 get_celery_context() 함수 추가                                │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│           ↓                                                              │
│  Phase 2: signal_hooks.py에 task_prerun/postrun 핸들러 추가             │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 2.1 on_task_prerun() 핸들러 구현                                  │   │
│  │ 2.2 on_task_postrun() 핸들러 구현                                 │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│           ↓                                                              │
│  Phase 3: _write_to_wal()에 celery_context 자동 추가                    │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 3.1 CeleryTraceContext에서 celery_context 자동 추출               │   │
│  │ 3.2 WAL 레코드에 celery_context 필드 추가                         │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│           ↓                                                              │
│  Phase 4: 기존 수동 코드 정리 (Optional)                                 │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 4.1 dlq_replay.py의 restore_trace_from_celery() 제거             │   │
│  │ 4.2 chaos_scheduler.py의 task_id 수동 전달 제거                   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│           ↓                                                              │
│  Phase 5: 테스트 및 검증                                                 │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 5.1 단위 테스트 추가                                              │   │
│  │ 5.2 통합 테스트 추가                                              │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase 1: trace.py 유틸리티 함수 추가

### 2.1 generate_celery_trace_id() 함수

**파일**: `packages/selfhealing-python/src/selfhealing/audit/trace.py`

**현재 코드** (L24-35):
```python
def generate_trace_id() -> str:
    """
    Generate a new trace ID.

    Format: "req-{uuid4_short}" (e.g., "req-a1b2c3d4")

    Returns:
        New unique trace ID
    """
    return f"req-{uuid.uuid4().hex[:8]}"
```

**추가할 코드**:
```python
# =============================================================================
# Phase 28: Celery Task trace_id 표준화
# =============================================================================

# Celery 컨텍스트 저장용 변수
_celery_context_var: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "celery_context", default=None
)


def generate_celery_trace_id(task_id: str) -> str:
    """
    Celery Task ID를 기반으로 trace_id를 생성합니다.
    
    Format: "CELERY_{task_id}"
    
    이 형식을 사용하면:
    - Flower UI에서 task_id로 직접 검색 가능
    - 재시도 시에도 동일한 trace_id 유지
    - Audit 로그에서 Celery Task와 1:1 매칭
    
    Args:
        task_id: Celery Task ID (예: "7483abc-1234-...")
        
    Returns:
        str: "CELERY_{task_id}" 형식의 trace_id
        
    Example:
        >>> generate_celery_trace_id("7483abc-1234-5678-90ab-cdef12345678")
        "CELERY_7483abc-1234-5678-90ab-cdef12345678"
    """
    if not task_id:
        # Fallback: task_id가 없으면 기존 방식으로 생성
        return f"CELERY_{generate_trace_id()}"
    return f"CELERY_{task_id}"


def set_celery_context(
    task_id: str,
    task_name: str,
    retries: int = 0,
) -> None:
    """
    현재 Celery Task 컨텍스트를 설정합니다.
    
    task_prerun 시그널에서 호출되어 Task 실행 동안 유지됩니다.
    
    Args:
        task_id: Celery Task ID
        task_name: Celery Task 이름 (예: "selfhealing.adapters.celery.tasks.replay_single_dlq_entry")
        retries: 현재 재시도 횟수
    """
    context = {
        "task_id": task_id,
        "task_name": task_name,
        "retries": retries,
    }
    _celery_context_var.set(context)
    
    # trace_id도 함께 설정
    trace_id = generate_celery_trace_id(task_id)
    set_trace_id(trace_id)


def get_celery_context() -> Optional[dict]:
    """
    현재 Celery Task 컨텍스트를 반환합니다.
    
    Returns:
        dict: {"task_id": ..., "task_name": ..., "retries": ...} 또는 None
    """
    return _celery_context_var.get()


def clear_celery_context() -> None:
    """
    Celery Task 컨텍스트를 정리합니다.
    
    task_postrun 시그널에서 호출되어 Worker 재사용 시 이전 컨텍스트 잔존을 방지합니다.
    """
    _celery_context_var.set(None)
    clear_trace_id()


def is_celery_task() -> bool:
    """
    현재 실행 컨텍스트가 Celery Task 내부인지 확인합니다.
    
    Returns:
        bool: Celery Task 내부이면 True
    """
    return _celery_context_var.get() is not None
```

### 2.2 restore_trace_from_celery() 수정

**기존 코드** (L237-273):
```python
@contextmanager
def restore_trace_from_celery(
    trace_info: Optional[dict[str, Any]] = None
) -> Generator[str, None, None]:
    if trace_info and trace_info.get("trace_id"):
        trace_id = trace_info["trace_id"]
    else:
        # Beat에서 호출된 경우: INTERNAL_BEAT_xxx 형식으로 자체 생성
        trace_id = f"INTERNAL_BEAT_{generate_trace_id()}"
    
    with TraceContext(trace_id) as active_trace_id:
        yield active_trace_id
```

**수정 후 코드**:
```python
@contextmanager
def restore_trace_from_celery(
    trace_info: Optional[dict[str, Any]] = None,
    celery_task_id: Optional[str] = None,
    celery_task_name: Optional[str] = None,
) -> Generator[str, None, None]:
    """
    Celery Task에서 trace 컨텍스트를 복원하거나 자체 생성합니다.
    
    우선순위:
    1. trace_info에 trace_id가 있으면 사용 (HTTP → Celery 전파)
    2. celery_task_id가 있으면 CELERY_{task_id} 생성
    3. 둘 다 없으면 CELERY_{uuid} 생성 (Fallback)
    
    Note:
        task_prerun 시그널이 활성화되면 이 함수는 더 이상 수동 호출 불필요.
        하위 호환성을 위해 유지됨.
    
    Args:
        trace_info: HTTP 요청에서 전파된 trace 정보 (optional)
        celery_task_id: Celery Task ID (optional, self.request.id)
        celery_task_name: Celery Task 이름 (optional)
        
    Yields:
        str: 현재 사용 중인 trace_id
    """
    if trace_info and trace_info.get("trace_id"):
        # HTTP 요청에서 전파된 trace_id 사용
        trace_id = trace_info["trace_id"]
    elif celery_task_id:
        # Celery Task ID 기반 생성
        trace_id = generate_celery_trace_id(celery_task_id)
    else:
        # Fallback: UUID 기반 생성
        trace_id = f"CELERY_{generate_trace_id()}"
    
    # Celery 컨텍스트 설정 (있는 경우)
    if celery_task_id:
        set_celery_context(
            task_id=celery_task_id,
            task_name=celery_task_name or "unknown",
            retries=0,
        )
    
    try:
        with TraceContext(trace_id) as active_trace_id:
            yield active_trace_id
    finally:
        if celery_task_id:
            clear_celery_context()
```

---

## 3. Phase 2: signal_hooks.py에 task_prerun/postrun 핸들러 추가

### 3.1 현재 상태 분석

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/celery/signal_hooks.py`

**현재 코드** (L41-46):
```python
from celery.signals import (
    task_failure,
    task_success,
    task_retry,
    task_prerun,   # ← import만 되어 있음
    task_postrun,  # ← import만 되어 있음
)
```

### 3.2 on_task_prerun() 핸들러 추가

**추가 위치**: `signal_hooks.py` L200 이후 (Signal Handlers 섹션)

```python
@task_prerun.connect
def on_task_prerun(
    sender=None,
    task_id: str = None,
    task=None,
    args: tuple = None,
    kwargs: dict = None,
    **kw,
):
    """
    Celery Task 시작 전 TraceContext 자동 주입.
    
    Phase 28: 모든 Celery Task에 자동으로 trace_id를 주입합니다.
    
    동작:
    1. kwargs에 trace_info가 있으면 HTTP에서 전파된 것으로 간주 → 원본 trace_id 사용
    2. 없으면 CELERY_{task_id} 형식으로 생성
    
    이를 통해:
    - 개발자가 수동으로 trace_id 설정 불필요
    - 모든 Audit 로그에 자동으로 Celery Task ID 포함
    - Flower UI에서 직접 검색 가능
    """
    if not _config.enabled:
        return
    
    task_name = sender.name if sender else "unknown"
    
    # Skip excluded tasks
    if task_name in _config.excluded_tasks:
        return
    
    try:
        from selfhealing.audit.trace import (
            generate_celery_trace_id,
            set_celery_context,
            set_trace_id,
        )
        
        # HTTP에서 전파된 trace_info 확인
        trace_info = kwargs.get("trace_info") if kwargs else None
        
        if trace_info and trace_info.get("trace_id"):
            # HTTP 요청에서 전파된 trace_id 사용
            trace_id = trace_info["trace_id"]
            set_trace_id(trace_id)
        else:
            # Celery Task ID 기반 trace_id 생성
            trace_id = generate_celery_trace_id(task_id)
            set_trace_id(trace_id)
        
        # 재시도 횟수 추출
        request = sender.request if sender else None
        retries = getattr(request, "retries", 0) if request else 0
        
        # Celery 컨텍스트 설정
        set_celery_context(
            task_id=task_id,
            task_name=task_name,
            retries=retries,
        )
        
        logger.debug(
            f"[SelfHealing Signal] Task prerun: {task_name}, "
            f"task_id={task_id}, trace_id={trace_id}"
        )
        
    except Exception as e:
        # Never let signal handler crash affect task execution
        logger.error(f"[SelfHealing Signal] Error in prerun handler: {e}")


@task_postrun.connect
def on_task_postrun(
    sender=None,
    task_id: str = None,
    task=None,
    args: tuple = None,
    kwargs: dict = None,
    retval=None,
    state: str = None,
    **kw,
):
    """
    Celery Task 완료 후 TraceContext 정리.
    
    Phase 28: Worker 재사용 시 이전 Task의 trace_id/celery_context 잔존 방지.
    """
    if not _config.enabled:
        return
    
    task_name = sender.name if sender else "unknown"
    
    if task_name in _config.excluded_tasks:
        return
    
    try:
        from selfhealing.audit.trace import clear_celery_context
        
        clear_celery_context()
        
        logger.debug(
            f"[SelfHealing Signal] Task postrun: {task_name}, "
            f"task_id={task_id}, state={state}"
        )
        
    except Exception as e:
        logger.error(f"[SelfHealing Signal] Error in postrun handler: {e}")
```

### 3.3 disconnect 함수 업데이트

**현재 코드** (L780 부근):
```python
def disconnect_selfhealing_signals():
    """Disconnect all signal handlers."""
    task_failure.disconnect(on_task_failure)
    # ... 기존 disconnect
```

**추가할 코드**:
```python
def disconnect_selfhealing_signals():
    """Disconnect all signal handlers."""
    global _signals_connected
    
    task_failure.disconnect(on_task_failure)
    task_success.disconnect(on_task_success)
    task_retry.disconnect(on_task_retry)
    task_prerun.disconnect(on_task_prerun)    # Phase 28 추가
    task_postrun.disconnect(on_task_postrun)  # Phase 28 추가
    
    _signals_connected = False
    logger.info("[SelfHealing Signal] All signal handlers disconnected")
```

---

## 4. Phase 3: _write_to_wal()에 celery_context 자동 추가

### 4.1 현재 상태 분석

**파일**: `packages/selfhealing-python/src/selfhealing/services/audit/base.py`

**현재 코드** (L140-153):
```python
wal_entry = {
    "record_id": record_id,
    "event_type": event_type,
    "trace_id": trace_id,  # Phase 25: trace_id 추가
    "source": source,
    "details": details,
    "success": success,
    "error_message": error_message,
    "domain": domain,
    "target_id": target_id,
    "actor_id": actor_id,
    "actor_type": actor_type,
    "actor_roles": actor_roles,
    "timestamp": time.time(),
    "synced": False,
}
```

### 4.2 celery_context 자동 추가

**수정 후 코드**:
```python
    # Phase 28: Celery 컨텍스트 자동 추출
    celery_context = None
    try:
        from selfhealing.audit.trace import get_celery_context, is_celery_task
        if is_celery_task():
            celery_context = get_celery_context()
    except ImportError:
        pass
    except Exception:
        pass
    
    try:
        record_id = f"audit-{uuid.uuid4().hex[:12]}"
        wal_entry = {
            "record_id": record_id,
            "event_type": event_type,
            "trace_id": trace_id,
            "source": source,
            "details": details,
            "success": success,
            "error_message": error_message,
            "domain": domain,
            "target_id": target_id,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "actor_roles": actor_roles,
            "celery_context": celery_context,  # Phase 28: Celery 메타데이터 이중화
            "timestamp": time.time(),
            "synced": False,
        }
```

### 4.3 예상 결과 (WAL 레코드)

```json
{
  "record_id": "audit-a1b2c3d4e5f6",
  "event_type": "DLQ_REPLAY_SUCCESS",
  "trace_id": "CELERY_7483abc-1234-5678-90ab-cdef12345678",
  "actor_id": "system",
  "actor_type": "system",
  "actor_roles": [],
  "celery_context": {
    "task_id": "7483abc-1234-5678-90ab-cdef12345678",
    "task_name": "selfhealing.adapters.celery.tasks.replay_single_dlq_entry",
    "retries": 0
  },
  "details": {
    "dlq_id": 123,
    "domain": "payment"
  }
}
```

**Grafana 쿼리 효율성 비교**:

```sql
-- AS-IS: 문자열 파싱 필요
SELECT * FROM audit_logs 
WHERE trace_id LIKE 'CELERY_%'
  AND SUBSTRING(trace_id FROM 8) = '7483abc-1234...'

-- TO-BE: 전용 필드로 빠른 검색
SELECT * FROM audit_logs 
WHERE celery_context->>'task_id' = '7483abc-1234-5678-90ab-cdef12345678'

-- Task 이름별 그룹화
SELECT celery_context->>'task_name', COUNT(*) 
FROM audit_logs 
WHERE celery_context IS NOT NULL
GROUP BY celery_context->>'task_name'
```

---

## 5. Phase 4: 기존 수동 코드 정리 (Optional)

### 5.1 영향 받는 파일

| 파일 | 현재 코드 | 변경 |
|------|----------|------|
| `dlq_replay.py` | `with restore_trace_from_celery(trace_info):` | 제거 가능 |
| `chaos_scheduler.py` | `task_id=self.request.id` 수동 전달 | 제거 가능 |

### 5.2 마이그레이션 전략

**Phase 28 완료 후**:
1. 기존 수동 코드는 중복이 되지만 **오류는 발생하지 않음** (idempotent)
2. 점진적으로 제거 가능
3. 하위 호환성 유지를 위해 `restore_trace_from_celery()`는 deprecated 마킹만

```python
# dlq_replay.py 수정 (Phase 28 완료 후)
@shared_task(bind=True, ...)
def replay_single_dlq_entry(self, dlq_id: int, actor_info=None, trace_info=None):
    """
    DLQ 항목 리플레이.
    
    Note:
        Phase 28 이후: trace_id는 task_prerun 시그널에서 자동 주입됨.
        trace_info 파라미터는 하위 호환성을 위해 유지되나 사용되지 않음.
    """
    # with restore_trace_from_celery(trace_info):  # 더 이상 필요 없음
    with restore_actor_from_celery(actor_info or {}):
        service = ReplayService()
        result = service.replay_single(dlq_id)
    # ...
```

---

## 6. Phase 5: 테스트 및 검증

### 6.1 단위 테스트

**파일**: `tests/unit/test_celery_trace_standardization.py`

```python
"""
Phase 28: Celery Task trace_id 표준화 테스트.

테스트 범위:
1. generate_celery_trace_id() 함수
2. set_celery_context() / get_celery_context() / clear_celery_context()
3. is_celery_task() 함수
4. task_prerun 시그널 핸들러
5. task_postrun 시그널 핸들러
6. _write_to_wal()의 celery_context 자동 추가
"""

import pytest
from unittest.mock import MagicMock, patch


class TestGenerateCeleryTraceId:
    """generate_celery_trace_id() 함수 테스트."""
    
    def test_generates_celery_prefix(self):
        """CELERY_ 접두사가 붙는지 검증."""
        from selfhealing.audit.trace import generate_celery_trace_id
        
        task_id = "7483abc-1234-5678-90ab-cdef12345678"
        result = generate_celery_trace_id(task_id)
        
        assert result == f"CELERY_{task_id}"
        assert result.startswith("CELERY_")
    
    def test_fallback_when_no_task_id(self):
        """task_id가 None일 때 Fallback 동작 검증."""
        from selfhealing.audit.trace import generate_celery_trace_id
        
        result = generate_celery_trace_id(None)
        
        assert result.startswith("CELERY_req-")  # Fallback UUID
    
    def test_empty_task_id_fallback(self):
        """빈 task_id일 때 Fallback 동작 검증."""
        from selfhealing.audit.trace import generate_celery_trace_id
        
        result = generate_celery_trace_id("")
        
        assert result.startswith("CELERY_req-")


class TestCeleryContextManagement:
    """Celery 컨텍스트 관리 함수 테스트."""
    
    def test_set_and_get_celery_context(self):
        """set_celery_context() 후 get_celery_context()로 조회 가능한지 검증."""
        from selfhealing.audit.trace import (
            set_celery_context,
            get_celery_context,
            clear_celery_context,
        )
        
        set_celery_context(
            task_id="test-task-123",
            task_name="my_task",
            retries=2,
        )
        
        context = get_celery_context()
        
        assert context is not None
        assert context["task_id"] == "test-task-123"
        assert context["task_name"] == "my_task"
        assert context["retries"] == 2
        
        # Cleanup
        clear_celery_context()
    
    def test_clear_celery_context(self):
        """clear_celery_context() 후 None이 되는지 검증."""
        from selfhealing.audit.trace import (
            set_celery_context,
            get_celery_context,
            clear_celery_context,
        )
        
        set_celery_context(task_id="test", task_name="test", retries=0)
        clear_celery_context()
        
        assert get_celery_context() is None
    
    def test_is_celery_task_true(self):
        """Celery 컨텍스트 설정 후 is_celery_task()가 True 반환하는지 검증."""
        from selfhealing.audit.trace import (
            set_celery_context,
            is_celery_task,
            clear_celery_context,
        )
        
        set_celery_context(task_id="test", task_name="test", retries=0)
        
        assert is_celery_task() is True
        
        clear_celery_context()
    
    def test_is_celery_task_false(self):
        """Celery 컨텍스트 없을 때 is_celery_task()가 False 반환하는지 검증."""
        from selfhealing.audit.trace import (
            is_celery_task,
            clear_celery_context,
        )
        
        clear_celery_context()  # 확실히 정리
        
        assert is_celery_task() is False


class TestTaskPrerunHandler:
    """task_prerun 시그널 핸들러 테스트."""
    
    @patch("selfhealing.adapters.celery.signal_hooks._config")
    def test_prerun_sets_celery_trace_id(self, mock_config):
        """task_prerun이 CELERY_{task_id} 형식의 trace_id를 설정하는지 검증."""
        from selfhealing.adapters.celery.signal_hooks import on_task_prerun
        from selfhealing.audit.trace import get_trace_id, clear_celery_context
        
        mock_config.enabled = True
        mock_config.excluded_tasks = set()
        
        mock_sender = MagicMock()
        mock_sender.name = "my_test_task"
        mock_sender.request.retries = 0
        
        on_task_prerun(
            sender=mock_sender,
            task_id="abc-123-def",
            task=None,
            args=(),
            kwargs={},
        )
        
        trace_id = get_trace_id()
        assert trace_id == "CELERY_abc-123-def"
        
        clear_celery_context()
    
    @patch("selfhealing.adapters.celery.signal_hooks._config")
    def test_prerun_preserves_http_trace_id(self, mock_config):
        """HTTP에서 전파된 trace_id가 있으면 그대로 유지하는지 검증."""
        from selfhealing.adapters.celery.signal_hooks import on_task_prerun
        from selfhealing.audit.trace import get_trace_id, clear_celery_context
        
        mock_config.enabled = True
        mock_config.excluded_tasks = set()
        
        mock_sender = MagicMock()
        mock_sender.name = "my_test_task"
        mock_sender.request.retries = 0
        
        on_task_prerun(
            sender=mock_sender,
            task_id="abc-123-def",
            task=None,
            args=(),
            kwargs={"trace_info": {"trace_id": "req-original-http"}},
        )
        
        trace_id = get_trace_id()
        assert trace_id == "req-original-http"
        
        clear_celery_context()


class TestWalCeleryContext:
    """_write_to_wal()의 celery_context 자동 추가 테스트."""
    
    @patch("selfhealing.services.audit.base._get_wal")
    def test_wal_includes_celery_context(self, mock_get_wal):
        """WAL 레코드에 celery_context가 포함되는지 검증."""
        from selfhealing.services.audit.base import _write_to_wal
        from selfhealing.audit.trace import set_celery_context, clear_celery_context
        
        mock_wal = MagicMock()
        mock_wal.write.return_value = 1
        mock_get_wal.return_value = mock_wal
        
        # Celery 컨텍스트 설정
        set_celery_context(
            task_id="test-task-999",
            task_name="my_replay_task",
            retries=1,
        )
        
        _write_to_wal(
            event_type="TEST_EVENT",
            source="test",
            details={"foo": "bar"},
        )
        
        # WAL에 기록된 데이터 확인
        call_args = mock_wal.write.call_args[0][0]
        assert "celery_context" in call_args
        assert call_args["celery_context"]["task_id"] == "test-task-999"
        assert call_args["celery_context"]["task_name"] == "my_replay_task"
        assert call_args["celery_context"]["retries"] == 1
        
        clear_celery_context()
    
    @patch("selfhealing.services.audit.base._get_wal")
    def test_wal_celery_context_none_outside_celery(self, mock_get_wal):
        """Celery Task 외부에서는 celery_context가 None인지 검증."""
        from selfhealing.services.audit.base import _write_to_wal
        from selfhealing.audit.trace import clear_celery_context
        
        mock_wal = MagicMock()
        mock_wal.write.return_value = 1
        mock_get_wal.return_value = mock_wal
        
        clear_celery_context()  # 확실히 정리
        
        _write_to_wal(
            event_type="TEST_EVENT",
            source="test",
            details={"foo": "bar"},
        )
        
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["celery_context"] is None
```

### 6.2 통합 테스트

**파일**: `tests/integration/test_celery_trace_flow.py`

```python
"""
Phase 28: Celery Task trace_id 표준화 통합 테스트.

E2E 흐름:
1. Celery Task 시작 → task_prerun에서 trace_id 자동 설정
2. Task 내에서 Audit 기록 → trace_id와 celery_context 포함
3. Task 종료 → task_postrun에서 정리
"""

import pytest
from unittest.mock import MagicMock, patch


class TestCeleryTraceFlowE2E:
    """Celery → Audit 전체 흐름 E2E 테스트."""
    
    @patch("selfhealing.services.audit.base._get_wal")
    def test_full_celery_task_trace_flow(self, mock_get_wal):
        """
        전체 흐름 테스트:
        task_prerun → Audit 기록 → task_postrun
        """
        from selfhealing.adapters.celery.signal_hooks import on_task_prerun, on_task_postrun
        from selfhealing.services.audit.base import _write_to_wal
        from selfhealing.audit.trace import get_trace_id, is_celery_task, clear_celery_context
        
        mock_wal = MagicMock()
        mock_wal.write.return_value = 1
        mock_get_wal.return_value = mock_wal
        
        # Given: Task 정보
        task_id = "e2e-test-task-123"
        task_name = "selfhealing.adapters.celery.tasks.replay_single_dlq_entry"
        
        mock_sender = MagicMock()
        mock_sender.name = task_name
        mock_sender.request.retries = 0
        
        # Step 1: task_prerun 시그널
        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = set()
            
            on_task_prerun(
                sender=mock_sender,
                task_id=task_id,
                task=None,
                args=(),
                kwargs={},
            )
        
        # 검증: trace_id가 CELERY_ 형식으로 설정됨
        assert get_trace_id() == f"CELERY_{task_id}"
        assert is_celery_task() is True
        
        # Step 2: Task 내에서 Audit 기록
        _write_to_wal(
            event_type="DLQ_REPLAY_SUCCESS",
            source="ReplayService",
            details={"dlq_id": 123, "domain": "payment"},
        )
        
        # 검증: WAL 레코드에 trace_id와 celery_context 포함
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["trace_id"] == f"CELERY_{task_id}"
        assert call_args["celery_context"]["task_id"] == task_id
        assert call_args["celery_context"]["task_name"] == task_name
        
        # Step 3: task_postrun 시그널
        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = set()
            
            on_task_postrun(
                sender=mock_sender,
                task_id=task_id,
                task=None,
                args=(),
                kwargs={},
                retval={"success": True},
                state="SUCCESS",
            )
        
        # 검증: 컨텍스트 정리됨
        assert is_celery_task() is False
    
    @patch("selfhealing.services.audit.base._get_wal")
    def test_http_to_celery_trace_propagation(self, mock_get_wal):
        """
        HTTP → Celery 전파 테스트:
        HTTP 요청의 trace_id가 Celery Task까지 전파되는지 검증
        """
        from selfhealing.adapters.celery.signal_hooks import on_task_prerun
        from selfhealing.audit.trace import get_trace_id, clear_celery_context
        
        mock_sender = MagicMock()
        mock_sender.name = "test_task"
        mock_sender.request.retries = 0
        
        # HTTP 요청에서 생성된 원본 trace_id
        http_trace_id = "req-a1b2c3d4"
        
        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = set()
            
            on_task_prerun(
                sender=mock_sender,
                task_id="celery-task-456",
                task=None,
                args=(),
                kwargs={"trace_info": {"trace_id": http_trace_id}},
            )
        
        # 검증: HTTP trace_id가 유지됨 (CELERY_로 덮어쓰지 않음)
        assert get_trace_id() == http_trace_id
        
        clear_celery_context()
```

---

## 7. 파일 변경 목록

| Phase | 파일 | 변경 내용 |
|-------|------|-----------|
| 1.1 | `audit/trace.py` | `generate_celery_trace_id()` 함수 추가 |
| 1.2 | `audit/trace.py` | `_celery_context_var` 컨텍스트 변수 추가 |
| 1.3 | `audit/trace.py` | `set_celery_context()`, `get_celery_context()`, `clear_celery_context()`, `is_celery_task()` 추가 |
| 1.4 | `audit/trace.py` | `restore_trace_from_celery()` 수정 (celery_task_id 파라미터 추가) |
| 2.1 | `adapters/celery/signal_hooks.py` | `on_task_prerun()` 핸들러 추가 |
| 2.2 | `adapters/celery/signal_hooks.py` | `on_task_postrun()` 핸들러 추가 |
| 2.3 | `adapters/celery/signal_hooks.py` | `disconnect_selfhealing_signals()` 업데이트 |
| 3.1 | `services/audit/base.py` | `_write_to_wal()`에 celery_context 자동 추가 |
| 5.1 | `tests/unit/test_celery_trace_standardization.py` | 단위 테스트 추가 |
| 5.2 | `tests/integration/test_celery_trace_flow.py` | 통합 테스트 추가 |

---

## 8. 예상 결과

### 8.1 기존 Audit 로그 (INTERNAL_BEAT 패턴)

```json
{
  "event_type": "DLQ_REPLAY_SUCCESS",
  "trace_id": "INTERNAL_BEAT_req-a1b2c3d4",
  "actor_type": "system",
  "details": {"dlq_id": 123}
}
```

### 8.2 개선 후 Audit 로그 (CELERY_ 패턴)

```json
{
  "event_type": "DLQ_REPLAY_SUCCESS",
  "trace_id": "CELERY_7483abc-1234-5678-90ab-cdef12345678",
  "actor_type": "system",
  "celery_context": {
    "task_id": "7483abc-1234-5678-90ab-cdef12345678",
    "task_name": "selfhealing.adapters.celery.tasks.replay_single_dlq_entry",
    "retries": 0
  },
  "details": {"dlq_id": 123}
}
```

### 8.3 운영 효율성 비교

| 항목 | 기존 (INTERNAL_BEAT_UUID) | 개선 (CELERY_{task_id}) |
|------|---------------------------|-------------------------|
| **Flower 연동** | ❌ 불가능 (추적 단절) | ✅ 완벽 연동 (1:1 매칭) |
| **재시도 추적** | ❌ 재시도마다 ID 변경 | ✅ 동일 Task ID 유지 |
| **로그 정렬** | ⚠️ 시간순 정렬만 가능 | ✅ Task ID 기반 그룹화 |
| **Grafana 쿼리** | ⚠️ 문자열 파싱 필요 | ✅ 전용 필드로 빠른 검색 |
| **개발자 경험** | ⚠️ 수동 코드 필요 | ✅ 자동 주입 (휴먼 에러 제거) |

---

## 9. 작업 체크리스트

### Phase 1: trace.py 유틸리티 함수
- [x] **1.1**: `generate_celery_trace_id()` 함수 추가
- [x] **1.2**: `_celery_context_var` 컨텍스트 변수 추가
- [x] **1.3**: `set_celery_context()`, `get_celery_context()`, `clear_celery_context()`, `is_celery_task()` 추가
- [x] **1.4**: `restore_trace_from_celery()` 수정
- [x] **1.5**: Phase 1 단위 테스트 작성 및 통과 (12개 테스트)

### Phase 2: signal_hooks.py 핸들러
- [x] **2.1**: `on_task_prerun()` 핸들러 구현
- [x] **2.2**: `on_task_postrun()` 핸들러 구현
- [x] **2.3**: `disconnect_selfhealing_signals()` 업데이트

### Phase 3: _write_to_wal() 수정
- [x] **3.1**: `celery_context` 자동 추출 및 WAL 레코드에 추가

### Phase 4: 기존 코드 정리 (Optional)
- [x] **4.1**: `dlq_replay.py`의 `restore_trace_from_celery()` 제거
- [x] **4.2**: `chaos_scheduler.py`의 수동 task_id 전달 제거

### Phase 5: 테스트
- [x] **5.1**: 단위 테스트 작성 (Phase 1)
- [x] **5.2**: 통합 테스트 작성 (Phase 2, 3 포함 - 26개 테스트)

---

## 10. FAQ

### Q1: 왜 CELERY_ 접두사를 선택했나요?

**A**: 다른 후보들과 비교:

| 후보 | 문제점 |
|------|--------|
| `TASK_` | 너무 일반적. 다른 시스템의 "task"와 혼동 가능 |
| `ASYNC_` | Python의 async/await와 혼동 가능 |
| `BEAT_` | Beat 스케줄러만 의미. 수동 트리거 Task에 부적합 |
| `CELERY_` | ✅ 명확히 Celery Task임을 나타냄 |

### Q2: task_prerun 시그널이 이미 trace_id를 설정하는데, 기존 코드는 어떻게 되나요?

**A**: 하위 호환성을 위해 기존 코드는 그대로 동작합니다.

```python
# 기존 코드 (여전히 동작)
with restore_trace_from_celery(trace_info):
    ...

# Phase 28 이후: 위 코드는 중복이지만 오류 없음
# task_prerun이 먼저 설정하고, restore_trace_from_celery가 덮어쓰거나 유지
```

점진적으로 제거 가능하며, deprecated 마킹만 해두면 됩니다.

### Q3: celery_context와 trace_id 둘 다 있는 이유는?

**A**: 

| 필드 | 용도 |
|------|------|
| `trace_id` | 분산 추적의 핵심. 모든 시스템에서 동일 형식 |
| `celery_context` | Celery 전용 메타데이터. Grafana 쿼리 최적화, 재시도 횟수 등 추가 정보 |

`trace_id`만으로도 Flower 검색은 가능하지만, `celery_context`가 있으면 문자열 파싱 없이 직접 필드 검색 가능.

### Q4: HTTP 요청에서 시작된 Celery Task는 어떤 trace_id를 사용하나요?

**A**: HTTP 요청의 원본 trace_id를 유지합니다.

```
HTTP Request (trace_id: req-abc123)
    ↓
View에서 Celery Task 호출
    ↓ kwargs={"trace_info": {"trace_id": "req-abc123"}}
Celery Task
    ↓ task_prerun: trace_info가 있으므로 원본 유지
Audit 로그: trace_id = "req-abc123"  ← HTTP와 동일!
```

이를 통해 HTTP → Celery 전체 흐름을 단일 trace_id로 추적 가능.

---

## 변경 이력

| 버전 | 날짜 | 작성자 | 변경 내용 |
|------|------|--------|-----------|
| 1.0.0 | 2026-01-07 | AI Assistant | 초안 작성 - CELERY_ 접두사 표준화, task_prerun/postrun 핸들러, celery_context 이중화 설계 |
| 1.1.0 | 2026-01-08 | AI Assistant | Phase 2, 3 구현 완료 - on_task_prerun/postrun 핸들러, _write_to_wal celery_context 추가, 테스트 26개 작성 |
| 1.2.0 | 2026-01-08 | AI Assistant | Phase 4 구현 완료 - dlq_replay.py의 restore_trace_from_celery() 제거, chaos_scheduler.py의 task_id 수동 전달 제거 |
