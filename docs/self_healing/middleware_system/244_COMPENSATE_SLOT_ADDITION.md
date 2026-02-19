# 244. Recovery Step Compensate Slot 추가

> **Version**: 1.0.0
> **Created**: 2026-02-19
> **Status**: Approved
> **Parent**: [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md)
> **Priority**: P1 — 저비용, 기존 동작 무변경

## 0. 요약

`RecoveryCoordinator.register_step_handler()`에 **선택적 compensate 함수 슬롯**을 추가한다.
기존 Forward-only 핸들러 등록을 유지하면서, **향후 역순 보상이 필요할 때 즉시 활성화**할 수 있는 구조적 준비.

---

## 1. 문제점 (AS-IS)

### 1.1 현재 코드: Forward 함수만 등록 가능

**파일**: `services/coordination/recovery_coordinator.py` L348-361

```python
def register_step_handler(
    self,
    step_type: RecoveryStepType,
    handler: Callable[[RecoverySession, RecoveryStep], dict[str, Any]],
) -> None:
    """
    커스텀 단계 핸들러 등록.

    Args:
        step_type: 복구 단계 유형
        handler: 핸들러 함수 (session, step) -> {"success": bool, ...}
    """
    self._step_handlers[step_type] = handler
```

### 1.2 기본 핸들러 등록도 Forward만

**파일**: `services/coordination/recovery_coordinator.py` L340-347

```python
def _register_default_handlers(self) -> None:
    """기본 단계 핸들러 등록."""
    self._step_handlers = {
        RecoveryStepType.BUDGET_RESET: self._handle_budget_reset,
        RecoveryStepType.HEALTH_CHECK: self._handle_health_check,
        RecoveryStepType.CANARY_RESUME: self._handle_canary_resume,
        RecoveryStepType.GOVERNANCE_NORMAL: self._handle_governance_normal,
    }
```

### 1.3 실패 시 보상 경로 없음

**파일**: `services/coordination/recovery_coordinator.py` L1329-1355

```python
def _fail_session(self, session, error):
    session.status = RecoveryStatus.FAILED
    session.abort_reason = error
    session.completed_at = datetime.now(timezone.utc).isoformat()
    self._save_session(session)
    self._recovery_lock.release(session.namespace, session.id)
    logger.error(f"[Recovery] Failed: id={session.id}, error={error}")
```

`_fail_session()`은 **상태를 FAILED로 바꾸고 락을 해제**할 뿐, 이미 성공한 Step을 되돌리는 로직이 없다.

---

## 2. 해결책 (TO-BE)

### 2.1 설계 원칙

| 원칙 | 설명 |
|------|------|
| **기존 동작 무변경** | `compensate=None`이 기본값 → 기존 코드 수정 불필요 |
| **선택적 활성화** | compensate가 등록된 Step만 역순 보상 시도 |
| **Fail-Open** | 보상 실패가 세션 실패 처리를 중단시키지 않음 |
| **도메인 프리** | compensate 함수도 Forward와 동일한 시그니처 → 인프라 레벨 |

### 2.2 `register_step_handler()` 변경

```python
def register_step_handler(
    self,
    step_type: RecoveryStepType,
    handler: Callable[[RecoverySession, RecoveryStep], dict[str, Any]],
    compensate: Callable[[RecoverySession, RecoveryStep], dict[str, Any]] | None = None,
) -> None:
    """
    커스텀 단계 핸들러 등록.

    Args:
        step_type: 복구 단계 유형
        handler: Forward 핸들러 함수 (session, step) -> {"success": bool, ...}
        compensate: 보상 핸들러 함수 (선택). Step 실패 시 이전 성공 Step 역순 보상에 사용.
                    None이면 해당 Step은 보상 대상에서 제외.
    """
    self._step_handlers[step_type] = handler
    if compensate is not None:
        self._compensate_handlers[step_type] = compensate
```

### 2.3 `__init__()` 변경

```python
def __init__(self, ...):
    ...
    self._step_handlers: dict[RecoveryStepType, Callable] = {}
    self._compensate_handlers: dict[RecoveryStepType, Callable] = {}  # NEW
    self._register_default_handlers()
```

### 2.4 `_register_default_handlers()` 변경

```python
def _register_default_handlers(self) -> None:
    """기본 단계 핸들러 등록."""
    self._step_handlers = {
        RecoveryStepType.BUDGET_RESET: self._handle_budget_reset,
        RecoveryStepType.HEALTH_CHECK: self._handle_health_check,
        RecoveryStepType.CANARY_RESUME: self._handle_canary_resume,
        RecoveryStepType.GOVERNANCE_NORMAL: self._handle_governance_normal,
    }
    # Compensate 핸들러: 현재는 빈 dict (Phase 2에서 필요 시 등록)
    self._compensate_handlers = {}
```

### 2.5 `_fail_session()`에서 보상 호출 (선택적)

```python
def _fail_session(self, session, error):
    """세션 실패 처리 + 등록된 compensate 핸들러 역순 실행."""
    # 1. 이미 완료된 Step 역순 보상 시도
    self._attempt_compensation(session)

    # 2. 기존 실패 처리 (변경 없음)
    session.status = RecoveryStatus.FAILED
    session.abort_reason = error
    session.completed_at = datetime.now(timezone.utc).isoformat()
    self._save_session(session)
    self._recovery_lock.release(session.namespace, session.id)
    logger.error(f"[Recovery] Failed: id={session.id}, error={error}")
```

### 2.6 `_attempt_compensation()` 신규 메서드

```python
def _attempt_compensation(self, session: RecoverySession) -> None:
    """
    완료된 Step을 역순으로 보상 시도.

    Fail-Open 원칙: 보상 실패가 세션 실패 처리를 중단시키지 않음.
    compensate 핸들러가 등록되지 않은 Step은 건너뜀.
    """
    completed_steps = [
        step for step in session.steps
        if step.status == RecoveryStatus.COMPLETED
    ]

    # 역순 정렬 (order 기준 내림차순)
    completed_steps.sort(key=lambda s: s.order, reverse=True)

    for step in completed_steps:
        compensate_handler = self._compensate_handlers.get(step.step_type)
        if compensate_handler is None:
            logger.debug(
                f"[Recovery] No compensate handler for {step.step_type.value}, skipping"
            )
            continue

        try:
            result = compensate_handler(session, step)
            if result.get("success"):
                logger.info(
                    f"[Recovery] Compensated: {step.step_type.value}, "
                    f"session={session.id}"
                )
            else:
                logger.warning(
                    f"[Recovery] Compensation failed: {step.step_type.value}, "
                    f"error={result.get('error')}"
                )
                # 245번 문서: 보상 실패 → DLQ 연동
        except Exception as e:
            logger.warning(
                f"[Recovery] Compensation exception: {step.step_type.value}, "
                f"error={e}"
            )
            # Fail-Open: 보상 실패가 세션 실패 처리를 중단시키지 않음
```

---

## 3. 영향 범위

### 3.1 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `recovery_coordinator.py` | `__init__`, `_register_default_handlers`, `register_step_handler`, `_fail_session`, `_attempt_compensation` (신규) |
| `recovery_state.py` | 변경 없음 |
| `idempotent_step_handlers.py` | 변경 없음 |

### 3.2 하위 호환성

| 항목 | 호환성 |
|------|--------|
| `register_step_handler(step_type, handler)` | ✅ `compensate=None` 기본값으로 기존 코드 무변경 |
| `_register_default_handlers()` | ✅ `_compensate_handlers = {}` → 기존 동작 동일 |
| `_fail_session()` | ✅ `_attempt_compensation()`이 빈 dict이면 no-op |
| `IdempotentStepHandlerRegistry` | ✅ Forward 핸들러와 독립적 |

### 3.3 기존 연동 시스템 영향

| 시스템 | 영향 |
|--------|------|
| `RecoveryCircuitBreaker` | 없음 — 에러율 기반 트립은 별도 경로 |
| `RecoveryAuditRecorder` | Phase 2에서 보상 감사 이벤트 추가 가능 |
| `DistributedRecoveryLock` | 없음 — 보상 실행은 기존 락 해제 전 수행 |
| `EventBus` | Phase 2에서 `COMPENSATION_EXECUTED` 이벤트 추가 가능 |

---

## 4. 테스트 전략

### 4.1 기존 테스트 무영향 확인

```python
# 기존 코드: compensate 없이 등록 → 동일하게 동작해야 함
coordinator.register_step_handler(RecoveryStepType.BUDGET_RESET, my_handler)
```

### 4.2 신규 테스트 케이스

| # | 테스트 | 검증 내용 |
|---|--------|-----------|
| 1 | `test_register_with_compensate` | compensate 함수가 `_compensate_handlers`에 저장됨 |
| 2 | `test_register_without_compensate` | `_compensate_handlers`에 등록되지 않음 (기존 동작) |
| 3 | `test_attempt_compensation_reverse_order` | 완료 Step이 역순으로 보상됨 |
| 4 | `test_attempt_compensation_skip_no_handler` | compensate 미등록 Step은 건너뜀 |
| 5 | `test_attempt_compensation_fail_open` | 보상 실패해도 세션 실패 처리 계속 진행 |
| 6 | `test_attempt_compensation_empty` | 완료 Step 없으면 no-op |
| 7 | `test_fail_session_calls_compensation` | `_fail_session()`이 `_attempt_compensation()` 호출 |

---

## 5. 향후 확장 경로

| Phase | 내용 | 의존성 |
|-------|------|--------|
| Phase 1 (본 문서) | compensate 슬롯 추가 + `_attempt_compensation()` | 없음 |
| Phase 2 | 기본 compensate 핸들러 등록 (BUDGET_RESET → 가중치 복원 등) | 인프라 안정성 검증 |
| Phase 3 | 보상 감사 이벤트 (`RecoveryAuditEventType.COMPENSATION_EXECUTED`) | 문서 245 DLQ 연동 |
| Phase 4 | IdempotentCompensateHandler (보상 멱등성) | `idempotent_step_handlers.py` 확장 |
