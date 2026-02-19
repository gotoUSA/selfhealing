# 245. Recovery Session 실패 시 DLQ 자동 연동

> **Version**: 1.0.0
> **Created**: 2026-02-19
> **Status**: Approved
> **Parent**: [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md)
> **Related**: [244_COMPENSATE_SLOT_ADDITION.md](244_COMPENSATE_SLOT_ADDITION.md)
> **Priority**: P1 — Saga와 독립적으로 운영 가시성 개선

## 0. 요약

`RecoveryCoordinator._fail_session()`에서 **실패 정보를 DLQ에 자동 저장**하고,
244번 문서의 `_attempt_compensation()`에서 **보상 실패 시에도 DLQ 저장**하여
복구 실패의 추적 가능성과 재처리 가능성을 확보한다.

---

## 1. 문제점 (AS-IS)

### 1.1 `_fail_session()`에 DLQ 연동 없음

**파일**: `services/coordination/recovery_coordinator.py` L1329-1355

```python
def _fail_session(self, session, error):
    """세션 실패 처리."""
    session.status = RecoveryStatus.FAILED
    session.abort_reason = error
    session.completed_at = datetime.now(timezone.utc).isoformat()
    self._save_session(session)
    self._recovery_lock.release(session.namespace, session.id)
    logger.error(f"[Recovery] Failed: id={session.id}, error={error}")
    # ← DLQ 저장 없음
    # ← 실패 컨텍스트(세션 ID, 트리거 레벨, 실패 Step, 에러 메시지) 유실 가능
```

### 1.2 기존 DLQ가 이미 범용 실패 저장을 지원

**파일**: `services/dlq/__init__.py` L87-125

```python
def store_to_dlq(
    domain: str,
    failure_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    ...
    snapshot_data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    ...
) -> DLQEntryResult:
    """Convenience function to store a failure in the DLQ."""
    return get_dlq_service().store_failure(...)
```

DLQ는 `domain`과 `failure_type` 기반 범용 저장을 지원하므로, `domain="selfhealing"`, `failure_type="RECOVERY_SESSION_FAILED"` 형태로 복구 실패를 저장할 수 있다.

### 1.3 DLQ의 3단계 Fallback이 무손실 보장

**파일**: `services/dlq/store_operations.py` L156-249

```python
def _write_to_local_fallback(self, entry_data, original_error):
    """3단계 Fallback 체인으로 DLQ 데이터 무손실 보장."""
    # 1차: DiskPersistentBuffer (LMDB)
    # 2차: JSONL 파일
    # 3차: stderr 출력
```

DLQ 저장 자체가 실패하더라도 로컬 Fallback으로 데이터 유실을 방지한다.

---

## 2. 해결책 (TO-BE)

### 2.1 설계 원칙

| 원칙 | 설명 |
|------|------|
| **Fail-Open** | DLQ 저장 실패가 `_fail_session()` 로직을 중단시키지 않음 |
| **도메인 프리** | `domain="selfhealing"` 사용 → 비즈니스 도메인 비침투 |
| **스냅샷 보존** | 실패 시점의 세션 전체 상태를 `snapshot_data`에 저장 |
| **재처리 경로** | `recommended_action` 필드로 운영자 가이드 제공 |

### 2.2 `_fail_session()`에 DLQ 저장 추가

```python
def _fail_session(self, session: RecoverySession, error: str) -> None:
    """세션 실패 처리 + DLQ 자동 저장."""
    # 1. 보상 시도 (244번 문서)
    self._attempt_compensation(session)

    # 2. 기존 실패 처리
    session.status = RecoveryStatus.FAILED
    session.abort_reason = error
    session.completed_at = datetime.now(timezone.utc).isoformat()
    self._save_session(session)

    # 3. DLQ 자동 저장 (NEW)
    self._store_failure_to_dlq(session, error)

    # 4. 락 해제
    self._recovery_lock.release(session.namespace, session.id)
    logger.error(f"[Recovery] Failed: id={session.id}, error={error}")
```

### 2.3 `_store_failure_to_dlq()` 신규 메서드

```python
def _store_failure_to_dlq(
    self,
    session: RecoverySession,
    error: str,
    failure_type: str = "RECOVERY_SESSION_FAILED",
) -> None:
    """
    복구 실패 정보를 DLQ에 저장.

    Fail-Open 원칙: DLQ 저장 실패가 복구 실패 처리를 중단시키지 않음.
    DLQ 자체의 3단계 Fallback이 무손실을 보장함.

    Args:
        session: 실패한 RecoverySession
        error: 에러 메시지
        failure_type: DLQ failure_type (기본: RECOVERY_SESSION_FAILED)
    """
    try:
        from selfhealing.services.dlq import store_to_dlq

        # 실패 시점의 Step 정보
        failed_step = session.get_current_step()
        failed_step_info = None
        if failed_step:
            failed_step_info = {
                "step_type": failed_step.step_type.value,
                "order": failed_step.order,
                "started_at": failed_step.started_at,
                "error_message": failed_step.error_message,
                "params": failed_step.params,
            }

        # 완료된 Step 목록
        completed_steps = [
            {
                "step_type": step.step_type.value,
                "order": step.order,
                "completed_at": step.completed_at,
            }
            for step in session.steps
            if step.status == RecoveryStatus.COMPLETED
        ]

        store_to_dlq(
            domain="selfhealing",
            failure_type=failure_type,
            entity_type="recovery_session",
            entity_id=session.id,
            error_message=error,
            snapshot_data={
                "session": session.to_dict(),
            },
            metadata={
                "namespace": session.namespace,
                "trigger_level": session.trigger_level,
                "initiated_by": session.initiated_by,
                "failed_step": failed_step_info,
                "completed_steps": completed_steps,
                "completed_count": len(completed_steps),
                "total_steps": len(session.steps),
            },
            next_action_hint=(
                f"Recovery session {session.id} failed at step "
                f"{failed_step.step_type.value if failed_step else 'unknown'}. "
                f"Check session state and consider resume_recovery()."
            ),
            recommended_action="manual_review",
        )

        logger.info(
            f"[Recovery] Failure stored to DLQ: session={session.id}, "
            f"failure_type={failure_type}"
        )

    except Exception as e:
        # Fail-Open: DLQ 저장 실패가 복구 실패 처리를 중단시키지 않음
        logger.warning(
            f"[Recovery] DLQ store failed (ignored): session={session.id}, "
            f"error={e}"
        )
```

### 2.4 보상 실패 시 DLQ 저장 (244번 문서 연동)

244번 문서의 `_attempt_compensation()`에서 보상 실패 시:

```python
def _attempt_compensation(self, session: RecoverySession) -> None:
    """완료된 Step을 역순으로 보상 시도."""
    completed_steps = [
        step for step in session.steps
        if step.status == RecoveryStatus.COMPLETED
    ]
    completed_steps.sort(key=lambda s: s.order, reverse=True)

    for step in completed_steps:
        compensate_handler = self._compensate_handlers.get(step.step_type)
        if compensate_handler is None:
            continue

        try:
            result = compensate_handler(session, step)
            if not result.get("success"):
                # ← 보상 실패 → DLQ 저장
                self._store_failure_to_dlq(
                    session=session,
                    error=f"Compensation failed for {step.step_type.value}: "
                          f"{result.get('error', 'unknown')}",
                    failure_type="RECOVERY_COMPENSATION_FAILED",
                )
        except Exception as e:
            # ← 보상 예외 → DLQ 저장
            self._store_failure_to_dlq(
                session=session,
                error=f"Compensation exception for {step.step_type.value}: {e}",
                failure_type="RECOVERY_COMPENSATION_FAILED",
            )
```

---

## 3. DLQ 데이터 구조

### 3.1 세션 실패 DLQ 엔트리

```json
{
    "domain": "selfhealing",
    "failure_type": "RECOVERY_SESSION_FAILED",
    "entity_type": "recovery_session",
    "entity_id": "recovery-abc123def456",
    "error_message": "Stability check failed: error rate 0.25 >= threshold 0.1",
    "snapshot_data": {
        "session": {
            "id": "recovery-abc123def456",
            "namespace": "global",
            "trigger_level": "LEVEL_3",
            "status": "failed",
            "steps": [...],
            "current_step_index": 2,
            "started_at": "2026-02-19T10:00:00Z",
            "completed_at": "2026-02-19T10:05:30Z"
        }
    },
    "metadata": {
        "namespace": "global",
        "trigger_level": "LEVEL_3",
        "initiated_by": "system",
        "failed_step": {
            "step_type": "health_check",
            "order": 2,
            "started_at": "2026-02-19T10:03:00Z",
            "error_message": "Stability check failed"
        },
        "completed_steps": [
            {"step_type": "budget_reset", "order": 1, "completed_at": "..."}
        ],
        "completed_count": 1,
        "total_steps": 4
    },
    "recommended_action": "manual_review",
    "next_action_hint": "Recovery session recovery-abc123def456 failed at step health_check. Check session state and consider resume_recovery()."
}
```

### 3.2 보상 실패 DLQ 엔트리

```json
{
    "domain": "selfhealing",
    "failure_type": "RECOVERY_COMPENSATION_FAILED",
    "entity_type": "recovery_session",
    "entity_id": "recovery-abc123def456",
    "error_message": "Compensation failed for budget_reset: Provider unavailable",
    "snapshot_data": { "session": {...} },
    "metadata": {
        "namespace": "global",
        "trigger_level": "LEVEL_3",
        "failed_step": {
            "step_type": "budget_reset",
            "order": 1
        }
    },
    "recommended_action": "manual_review"
}
```

---

## 4. 영향 범위

### 4.1 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `recovery_coordinator.py` | `_fail_session()` DLQ 호출 추가, `_store_failure_to_dlq()` 신규 |
| `recovery_coordinator.py` | `_attempt_compensation()` 보상 실패 시 DLQ 호출 (244번 문서와 결합) |

### 4.2 의존 관계

```
_fail_session()
  ├── _attempt_compensation()    ← 244번 문서
  │     └── _store_failure_to_dlq(failure_type="RECOVERY_COMPENSATION_FAILED")
  ├── _store_failure_to_dlq(failure_type="RECOVERY_SESSION_FAILED")  ← 본 문서
  │     └── store_to_dlq()       ← 기존 DLQ 편의 함수
  │           └── DLQService.store_failure()
  │                 └── 3단계 Fallback (LMDB → JSONL → stderr)
  └── session 상태 변경 + 락 해제 (기존 로직)
```

### 4.3 하위 호환성

| 항목 | 호환성 |
|------|--------|
| `_fail_session()` 기존 동작 | ✅ DLQ 저장은 추가 동작, 실패해도 Fail-Open |
| DLQ 비활성화 시 | ✅ `store_to_dlq()`가 `DLQEntryResult.failed("DLQ is disabled")` 반환 → no-op |
| DLQ import 실패 시 | ✅ `try/except`로 무시 |
| `resume_recovery()` | ✅ 기존과 동일. DLQ 엔트리는 참조 정보, 복구 재개는 세션 기반 |

### 4.4 기존 연동 시스템 영향

| 시스템 | 영향 |
|--------|------|
| `DLQService` | 기존 `store_failure()` API 그대로 사용 |
| `RecoveryAuditRecorder` | 없음 — Audit과 DLQ는 독립적 보완 경로 |
| DLQ Replay | `domain="selfhealing"` 필터로 복구 실패만 조회 가능 |
| DLQ Dashboard | 자동 노출 (기존 리스트 UI에서 `selfhealing` 도메인으로 필터) |

---

## 5. 운영 활용 시나리오

### 5.1 복구 실패 모니터링

```python
# DLQ에서 복구 실패 조회
entries = dlq_service.get_pending_entries(domain="selfhealing")
for entry in entries:
    if entry.failure_type == "RECOVERY_SESSION_FAILED":
        session_id = entry.entity_id
        failed_step = entry.metadata.get("failed_step", {})
        print(f"Session {session_id} failed at {failed_step.get('step_type')}")
```

### 5.2 보상 실패 추적

```python
# 보상 실패만 필터
compensation_failures = [
    e for e in entries
    if e.failure_type == "RECOVERY_COMPENSATION_FAILED"
]
# → 보상이 실패한 Step을 수동으로 처리해야 함을 알 수 있음
```

---

## 6. 테스트 전략

| # | 테스트 | 검증 내용 |
|---|--------|-----------|
| 1 | `test_fail_session_stores_to_dlq` | `_fail_session()` 호출 시 DLQ에 엔트리 생성 |
| 2 | `test_fail_session_dlq_fail_open` | DLQ 저장 실패해도 세션 FAILED 상태 정상 처리 |
| 3 | `test_fail_session_dlq_disabled` | DLQ 비활성화 시 예외 없이 진행 |
| 4 | `test_dlq_entry_contains_session_snapshot` | `snapshot_data`에 세션 전체 상태 포함 |
| 5 | `test_dlq_entry_contains_failed_step_info` | `metadata.failed_step`에 실패 Step 정보 포함 |
| 6 | `test_compensation_failure_stores_to_dlq` | 보상 실패 시 `RECOVERY_COMPENSATION_FAILED` 타입으로 저장 |
| 7 | `test_dlq_entry_entity_id_is_session_id` | `entity_id`가 세션 ID와 동일 |
