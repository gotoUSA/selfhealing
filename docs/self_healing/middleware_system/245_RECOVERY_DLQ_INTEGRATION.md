# 245. Recovery Session 실패 시 DLQ 자동 연동

> **Version**: 2.0.0
> **Created**: 2026-02-19
> **Updated**: 2026-02-19
> **Status**: Approved
> **Parent**: [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md)
> **Related**: [244_COMPENSATE_SLOT_ADDITION.md](244_COMPENSATE_SLOT_ADDITION.md)
> **Priority**: P1 — Saga와 독립적으로 운영 가시성 개선

## 0. 요약

`RecoveryCoordinator._fail_session()`에서 **실패 정보를 DLQ에 자동 저장**하고,
244번 문서의 `_attempt_compensation()`이 반환한 `CompensationResult`의 **보상 실패 목록을
DLQ 1건에 Aggregation**하여 복구 실패의 추적 가능성과 재처리 가능성을 확보한다.

### 변경 이력 (v1.0.0 → v2.0.0)

| # | 항목 | v1.0.0 | v2.0.0 | 근거 |
|---|------|--------|--------|------|
| 1 | 보상 실패 DLQ | 루프 내 매번 호출 (N건) | `CompensationResult` Aggregation (1건) | `_fail_session()` L1385 주석: `comp_result.failed_steps → DLQ 전송` |
| 2 | Lock ↔ DLQ 순서 | DLQ 저장 → Lock 해제 | `logger.error` → Lock 해제 → DLQ 저장 | SIGKILL 방어 + Lock 보유 시간 최소화 |
| 3 | Failed Step 탐색 | `session.get_current_step()` | `status == RecoveryStatus.FAILED` 탐색 | 커서(index) 대신 결과(status) 기반이 사후 분석에 더 견고 |
| 4 | PII 마스킹 | 미적용 | `mask_sensitive_fields()` 적용 | `audit/masking.py` 기존 유틸리티 활용 |
| 5 | `recommended_action` | 고정 `"manual_review"` | 보상 실패 유무에 따라 동적 분기 | 기존 패턴: `"manual_check"`, `"auto_replay"` 등 snake_case 1~2 단어 |
| 6 | `next_action_hint` | 일반적 안내 | `result_data` 키 포함 구체적 안내 | `RecoveryStep.result_data` 활용 (244번 문서) |
| 7 | `compensation_summary` | 없음 | 요약 문자열 필드 추가 | 운영 대시보드 가독성 (JSON 펼치기 불필요) |
| 8 | `deepcopy` 보안 | 미검토 | 불필요 확인 (새 dict 반환) | `mask_sensitive_fields()`가 `result = {}` 생성 |

---

## 1. 문제점 (AS-IS)

### 1.1 `_fail_session()`에 DLQ 연동 없음

**파일**: `services/coordination/recovery_coordinator.py` L1350-1398

```python
def _fail_session(self, session, error):
    """세션 실패 처리 + 등록된 compensate 핸들러 역순 실행."""
    session.abort_reason = error
    session.status = RecoveryStatus.COMPENSATING
    self._save_session(session)

    comp_result = self._attempt_compensation(session)

    if not comp_result.all_compensated:
        logger.warning(...)
        # Phase 3 (245번 문서): comp_result.failed_steps → DLQ 전송

    session.status = RecoveryStatus.FAILED
    session.completed_at = datetime.now(timezone.utc).isoformat()
    self._save_session(session)

    self._recovery_lock.release(session.namespace, session.id)
    logger.error(f"[Recovery] Failed: id={session.id}, error={error}")
    # ← DLQ 저장 없음
    # ← 실패 컨텍스트(세션 ID, 트리거 레벨, 실패 Step, 에러 메시지) 유실 가능
    # ← comp_result.failed_steps 보상 실패 목록 미활용
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

### 1.4 `_attempt_compensation()`이 이미 Aggregation 구조를 반환

**파일**: `services/coordination/recovery_state.py` L196-228

```python
@dataclass
class CompensationResult:
    """보상 실행 결과."""
    compensated_steps: list[RecoveryStep] = field(default_factory=list)
    failed_steps: list[tuple[RecoveryStep, str]] = field(default_factory=list)
    skipped_steps: list[RecoveryStep] = field(default_factory=list)

    @property
    def all_compensated(self) -> bool:
        return len(self.failed_steps) == 0
```

`_attempt_compensation()`은 보상 실패를 개별 처리하지 않고 `CompensationResult.failed_steps`에 수집하여 반환한다.
`_fail_session()` L1385의 주석 `# Phase 3 (245번 문서): comp_result.failed_steps → DLQ 전송`이
이미 1회 전송(Aggregation) 방식을 가리킨다.

### 1.5 기존 PII 마스킹 유틸리티가 DLQ에 미적용

**파일**: `audit/masking.py` L374-425

```python
def mask_sensitive_fields(data, sensitive_keys=None):
    """Mask sensitive fields in a dictionary."""
    # ...
    result = {}  # ← 새 dict 생성 (In-place 수정 아님)
    for key, value in data.items():
        if is_sensitive:
            result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            result[key] = mask_sensitive_fields(value, sensitive_keys)  # 재귀
        # ...
    return result
```

`RecoveryStep.params`에 커스텀 핸들러가 민감 정보를 포함할 수 있으나,
DLQ 저장 경로(`store_operations.py`)에는 마스킹 로직이 전혀 없다.
기존 `mask_sensitive_fields()`가 새 dict를 반환하므로 `deepcopy` 없이 안전하게 적용 가능하다.

---

## 2. 해결책 (TO-BE)

### 2.1 설계 원칙

| 원칙 | 설명 |
|------|------|
| **Fail-Open** | DLQ 저장 실패가 `_fail_session()` 로직을 중단시키지 않음 |
| **도메인 프리** | `domain="selfhealing"` 사용 → 비즈니스 도메인 비침투 |
| **스냅샷 보존** | 실패 시점의 세션 전체 상태를 `snapshot_data`에 저장 (PII 마스킹 적용) |
| **재처리 경로** | `recommended_action` + `next_action_hint` 동적 생성으로 운영자 가이드 제공 |
| **Aggregation** | 보상 실패를 개별 DLQ가 아닌 `compensation_failures` 리스트로 1건에 집약 |
| **Lock 최소 보유** | DLQ 저장은 Lock 해제 후 수행 — Lock 보유 시간 최소화 |
| **로그 선행** | SIGKILL 방어: `logger.error()`는 Lock 해제 전에 남겨 로그 시스템(ELK/Splunk)에 흔적 보장 |
| **상태 기반 탐색** | Failed Step은 `current_step_index` 커서가 아닌 `status == FAILED` 결과 기반 탐색 |

### 2.2 `_fail_session()`에 DLQ 저장 추가

```python
def _fail_session(self, session: RecoverySession, error: str) -> None:
    """세션 실패 처리 + DLQ 자동 저장."""
    # 1. abort_reason 설정 (보상 핸들러 참조용)
    session.abort_reason = error

    # 2. COMPENSATING 상태 전환 + 보상 시도 (244번 문서)
    session.status = RecoveryStatus.COMPENSATING
    self._save_session(session)
    comp_result = self._attempt_compensation(session)

    # 3. 보상 실패 로깅 (기존)
    if not comp_result.all_compensated:
        logger.warning(
            f"[Recovery] Compensation incomplete: session={session.id}, "
            f"failed={len(comp_result.failed_steps)}, "
            f"skipped={len(comp_result.skipped_steps)}"
        )

    # 4. 최종 실패 상태 설정 + 저장
    session.status = RecoveryStatus.FAILED
    session.completed_at = datetime.now(timezone.utc).isoformat()
    self._save_session(session)

    # 5. 로그 선행 — SIGKILL 방어 (Lock 해제 전)
    #    Lock 해제 후 DLQ 저장 전에 프로세스가 죽어도
    #    최소한 로그 시스템(ELK/Splunk)에 흔적이 남음
    logger.error(f"[Recovery] Failed: id={session.id}, error={error}")

    # 6. 락 해제 — DLQ 저장보다 먼저 (Lock 보유 시간 최소화)
    #    세션이 이미 FAILED 상태로 영속화되었으므로,
    #    다른 Worker가 Lock을 획득해도 execute_next_step()에서
    #    IN_PROGRESS/HEALTH_CHECK 상태 체크에 의해 차단됨
    self._recovery_lock.release(session.namespace, session.id)

    # 7. DLQ 자동 저장 (Lock-free 구간, Fail-Open)
    #    comp_result를 함께 전달하여 보상 실패를 Aggregation
    self._store_failure_to_dlq(session, error, comp_result)
```

#### 2.2.1 v1.0.0 대비 순서 변경 이유

**v1.0.0 순서**: `save_session(FAILED)` → `store_dlq()` → `lock.release()` → `logger.error()`

**v2.0.0 순서**: `save_session(FAILED)` → `logger.error()` → `lock.release()` → `store_dlq()`

| 변경 | 이유 | 코드 근거 |
|------|------|-----------|
| `logger.error` Lock 해제 전 이동 | SIGKILL 방어: DLQ 저장 없이 프로세스가 죽어도 로그에 흔적 | `_record_recovery_aborted()` 패턴 — Audit 기록 후 상태 처리 |
| `lock.release` DLQ 저장 전 이동 | DLQ 저장 지연(DB 타임아웃 → 3단계 Fallback) 시 Lock 보유 시간 불필요 증가 방지 | `execute_next_step()` L501-504: FAILED 세션은 상태 체크에서 차단됨 |
| `store_dlq` 최후 실행 | Lock-free 구간에서 Fail-Open 실행. 세션 영속화 완료 후이므로 일관성 문제 없음 | `store_to_dlq()`의 `try/except` Fail-Open + 3단계 Fallback |

### 2.3 `_store_failure_to_dlq()` 신규 메서드

```python
def _store_failure_to_dlq(
    self,
    session: RecoverySession,
    error: str,
    comp_result: CompensationResult | None = None,
) -> None:
    """
    복구 실패 정보를 DLQ에 1건으로 저장.

    보상 실패 목록(comp_result.failed_steps)을 Aggregation하여
    세션 실패 DLQ 1건에 통합 저장한다.
    보상 실패마다 개별 DLQ를 생성하지 않으므로 알림 폭주를 방지한다.

    Fail-Open 원칙: DLQ 저장 실패가 복구 실패 처리를 중단시키지 않음.
    DLQ 자체의 3단계 Fallback이 무손실을 보장함.

    Args:
        session: 실패한 RecoverySession
        error: 에러 메시지
        comp_result: 보상 실행 결과 (None이면 보상 정보 미포함)
    """
    try:
        from selfhealing.audit.masking import mask_sensitive_fields
        from selfhealing.services.dlq import store_to_dlq

        # --- Failed Step 탐색 (status 기반, 커서 기반 아님) ---
        # get_current_step()은 current_step_index 커서를 사용하지만,
        # DLQ는 사후 분석(post-mortem) 용도이므로 "결과(status)"를 신뢰한다.
        # execute_next_step()에서 실패 시 step.status = RecoveryStatus.FAILED를
        # 설정한 후 _fail_session()을 호출하므로, FAILED 상태 Step이 정확히 존재한다.
        failed_step = next(
            (s for s in session.steps if s.status == RecoveryStatus.FAILED),
            None,
        )
        failed_step_info = None
        if failed_step:
            failed_step_info = {
                "step_type": failed_step.step_type.value,
                "order": failed_step.order,
                "started_at": failed_step.started_at,
                "error_message": failed_step.error_message,
                "params": failed_step.params,
            }

        # --- 완료된 Step 목록 ---
        completed_steps = [
            {
                "step_type": step.step_type.value,
                "order": step.order,
                "completed_at": step.completed_at,
            }
            for step in session.steps
            if step.status == RecoveryStatus.COMPLETED
        ]

        # --- 보상 실패 Aggregation (v2.0.0) ---
        # _attempt_compensation()이 반환한 CompensationResult.failed_steps를
        # DLQ 1건에 리스트로 집약한다. 개별 DLQ를 생성하지 않으므로
        # 네트워크 단절로 5개 보상이 모두 실패해도 알림은 1건만 발생한다.
        compensation_failures = None
        compensation_summary = None
        has_compensation_failures = False

        if comp_result and comp_result.failed_steps:
            has_compensation_failures = True
            compensation_failures = [
                {
                    "step_type": step.step_type.value,
                    "order": step.order,
                    "compensation_error": err,
                    "forward_result": step.result_data,
                }
                for step, err in comp_result.failed_steps
            ]
            # 요약 문자열: 운영자가 JSON을 펼치지 않고 심각도를 파악할 수 있음
            compensation_summary = ", ".join(
                f"{step.step_type.value} ({err[:60]})"
                for step, err in comp_result.failed_steps
            )

        # --- PII 마스킹 ---
        # mask_sensitive_fields()는 새 dict를 반환하므로 deepcopy 불필요.
        # session.to_dict()도 새 dict를 생성하므로 원본 객체에 영향 없음.
        # 커스텀 핸들러가 params에 넣은 api_key, token 등이 자동 마스킹됨.
        snapshot = mask_sensitive_fields(session.to_dict())

        # --- recommended_action 동적 분기 ---
        # 기존 시스템 패턴: "manual_check", "auto_replay", "replay" (snake_case 1~2 단어)
        # 보상 실패가 있으면 데이터 불일치 가능성 → 더 강한 액션 권고
        recommended_action = (
            "manual_consistency_check"
            if has_compensation_failures
            else "manual_review"
        )

        # --- next_action_hint 구체화 ---
        # result_data의 키를 포함하여 운영자가 무엇을 확인해야 하는지 안내
        if has_compensation_failures:
            affected_details = []
            for step, _err in comp_result.failed_steps:
                keys = list(step.result_data.keys()) if step.result_data else []
                keys_str = f" (affected: {', '.join(keys)})" if keys else ""
                affected_details.append(f"{step.step_type.value}{keys_str}")
            next_action_hint = (
                f"Recovery session {session.id} failed. "
                f"Automatic compensation failed for: "
                f"{', '.join(affected_details)}. "
                f"Verify affected state manually before resume_recovery()."
            )
        else:
            step_name = failed_step.step_type.value if failed_step else "unknown"
            next_action_hint = (
                f"Recovery session {session.id} failed at step {step_name}. "
                f"Check session state and consider resume_recovery()."
            )

        # --- metadata 구성 ---
        metadata = {
            "namespace": session.namespace,
            "trigger_level": session.trigger_level,
            "initiated_by": session.initiated_by,
            "failed_step": failed_step_info,
            "completed_steps": completed_steps,
            "completed_count": len(completed_steps),
            "total_steps": len(session.steps),
        }

        # 보상 실패 정보 (Aggregation)
        if compensation_failures:
            metadata["compensation_failures"] = compensation_failures
            metadata["compensation_summary"] = compensation_summary

        store_to_dlq(
            domain="selfhealing",
            failure_type="RECOVERY_SESSION_FAILED",
            entity_type="recovery_session",
            entity_id=session.id,
            error_message=error,
            snapshot_data={
                "session": snapshot,
            },
            metadata=metadata,
            next_action_hint=next_action_hint,
            recommended_action=recommended_action,
        )

        logger.info(
            f"[Recovery] Failure stored to DLQ: session={session.id}, "
            f"compensation_failures={len(compensation_failures or [])}"
        )

    except Exception as e:
        # Fail-Open: DLQ 저장 실패가 복구 실패 처리를 중단시키지 않음
        logger.warning(
            f"[Recovery] DLQ store failed (ignored): session={session.id}, "
            f"error={e}"
        )
```

### 2.4 `_attempt_compensation()` 변경 없음

v1.0.0에서는 `_attempt_compensation()` 루프 내에서 보상 실패마다 개별 `_store_failure_to_dlq()`를 호출하는 설계였다.

v2.0.0에서는 **`_attempt_compensation()`은 변경하지 않는다.** 이 메서드는 기존대로 `CompensationResult`를 반환하고, DLQ 저장은 `_fail_session()`에서 `comp_result`를 받아 1회 수행한다.

**이유**: 현재 `_attempt_compensation()` 코드(`recovery_coordinator.py` L1398-1470)가 이미 `CompensationResult.failed_steps`에 실패를 수집하는 구조이며, `_fail_session()` L1385의 주석 `# Phase 3 (245번 문서): comp_result.failed_steps → DLQ 전송`이 Aggregation 방식을 가리킨다.

```
_fail_session()                              ← DLQ 1회 호출 (본 문서)
  ├── _attempt_compensation()                ← 기존 그대로 (244번 문서)
  │     ├── compensated_steps: [step, ...]   → 성공 수집
  │     ├── failed_steps: [(step, err), ...] → 실패 수집 (DLQ 호출 안 함)
  │     └── skipped_steps: [step, ...]       → 건너뜀 수집
  │     └── return CompensationResult        ← Aggregation용 반환값
  └── _store_failure_to_dlq(session, error, comp_result)
        └── compensation_failures 리스트로 통합 저장
```

**Aggregation 효과**: 네트워크 단절로 5개 Step 보상이 모두 실패해도 **DLQ 엔트리 1건**만 생성된다.

---

## 3. DLQ 데이터 구조

### 3.1 세션 실패 DLQ 엔트리 (보상 실패 없음)

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
            "steps": ["..."],
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
            "error_message": "Stability check failed",
            "params": {"duration_minutes": 5, "error_rate_threshold": 0.1}
        },
        "completed_steps": [
            {"step_type": "budget_reset", "order": 1, "completed_at": "2026-02-19T10:01:00Z"}
        ],
        "completed_count": 1,
        "total_steps": 4
    },
    "recommended_action": "manual_review",
    "next_action_hint": "Recovery session recovery-abc123def456 failed at step health_check. Check session state and consider resume_recovery()."
}
```

### 3.2 세션 실패 DLQ 엔트리 (보상 실패 포함 — Aggregation)

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
            "steps": ["..."]
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
            "error_message": "Stability check failed",
            "params": {"duration_minutes": 5}
        },
        "completed_steps": [
            {"step_type": "budget_reset", "order": 1, "completed_at": "2026-02-19T10:01:00Z"}
        ],
        "completed_count": 1,
        "total_steps": 4,
        "compensation_failures": [
            {
                "step_type": "budget_reset",
                "order": 1,
                "compensation_error": "Provider unavailable: connection timeout",
                "forward_result": {"success": true, "multiplier": 1.0}
            }
        ],
        "compensation_summary": "budget_reset (Provider unavailable: connection timeout)"
    },
    "recommended_action": "manual_consistency_check",
    "next_action_hint": "Recovery session recovery-abc123def456 failed. Automatic compensation failed for: budget_reset (affected: success, multiplier). Verify affected state manually before resume_recovery()."
}
```

### 3.3 v1.0.0 대비 변경 사항

| 필드 | v1.0.0 | v2.0.0 |
|------|--------|--------|
| `failure_type` | `RECOVERY_SESSION_FAILED` 또는 `RECOVERY_COMPENSATION_FAILED` (별도 엔트리) | `RECOVERY_SESSION_FAILED` 1건에 통합 |
| `metadata.compensation_failures` | 없음 (별도 DLQ) | 보상 실패 리스트 (step_type, error, forward_result 포함) |
| `metadata.compensation_summary` | 없음 | 요약 문자열 ("STEP (에러 50자)" 형식) |
| `snapshot_data.session` | 원본 `to_dict()` | `mask_sensitive_fields()` 적용 후 저장 |
| `recommended_action` | 고정 `"manual_review"` | 보상 실패 시 `"manual_consistency_check"` |
| `next_action_hint` | "Check session state..." | "Automatic compensation failed for: budget_reset (affected: multiplier)..." |

---

## 4. 설계 결정 근거

### 4.1 Aggregation: 개별 DLQ vs 1건 통합

**결정**: 1건 통합

**근거**:
- `_attempt_compensation()`이 이미 `CompensationResult.failed_steps`에 수집 후 반환하는 구조 (`recovery_state.py` L196-228)
- `_fail_session()` L1385 주석이 `comp_result.failed_steps → DLQ 전송`으로 1회 전송을 명시
- 네트워크 단절로 5개 보상 실패 시 v1.0.0은 6건(보상 5 + 세션 1), v2.0.0은 1건

### 4.2 Lock 순서: DLQ 저장 위치

**결정**: Lock 해제 후 DLQ 저장

**근거**:
- `execute_next_step()` L501-504: `session.status`가 `IN_PROGRESS` 또는 `HEALTH_CHECK`이 아니면 `None` 반환 → FAILED 세션은 다른 Worker가 실행 불가
- `store_to_dlq()`의 primary path는 PostgreSQL INSERT 1건(수십 ms)이지만, DB 타임아웃 시 3단계 Fallback까지 지연 가능 → Lock 보유 시간 불필요 증가
- `logger.error()`를 Lock 해제 전에 배치하여 SIGKILL 엣지 케이스 방어

### 4.3 Failed Step 탐색: 커서 vs 상태

**결정**: `status == RecoveryStatus.FAILED` 탐색

**근거**:
- `current_step_index`는 실행 흐름 제어용 커서, `status`는 실행 결과
- DLQ는 사후 분석(post-mortem) 용도이므로 결과를 신뢰하는 것이 논리적
- `execute_next_step()` L551: 실패 시 `step.status = RecoveryStatus.FAILED` 설정 후 `_fail_session()` 호출 → 항상 FAILED Step이 존재

### 4.4 PII 마스킹: deepcopy 필요 여부

**결정**: `deepcopy` 불필요

**근거**:
- `mask_sensitive_fields()` (`audit/masking.py` L386): `result = {}` — 매 레벨에서 새 dict 생성
- `session.to_dict()` (`recovery_state.py` L297): 새 dict 반환
- `RecoveryStep.to_dict()` (`recovery_state.py` L155): 새 dict 반환
- 기본 타입(`str`, `int`, `None`)은 Python에서 immutable → shallow reference 무해
- 따라서 `mask_sensitive_fields(session.to_dict())`로 충분하며, `copy.deepcopy()` 성능 비용 불필요

### 4.5 `recommended_action` 네이밍

**결정**: `"manual_review"` / `"manual_consistency_check"`

**근거** (기존 시스템 패턴):
- `retry_handler/sinks.py` L101: `"manual_check"`
- `retry_handler/handler.py` L688: `"manual_check"`
- `adaptive_dlq_replay.py` L190: `"auto_replay"`
- `dlq_integration.py` L141: `"auto_replay"`
- `self_healing.py` L442: `"replay"`

기존 패턴: **snake_case, 1~2 단어**. 초안의 `"manual_db_consistency_check"`는 4단어로 너무 긺.
`"manual_consistency_check"`로 축약: `db`를 뺀 이유는 Recovery Step 보상 실패가 반드시 DB 문제는 아님 (예: Canary Resume 보상 실패는 배포 시스템 문제).

### 4.6 `compensation_summary` 네이밍

**결정**: `metadata["compensation_summary"]`

**근거** (기존 시스템 패턴):
- `compliance_tasks.py` L163: `_get_summary_message()`
- `intelligence_tasks.py` L137: `_get_summary_message()`

`_summary` 접미사 패턴이 이미 사용됨. `compensation_summary`는 시스템 내 미존재 — 충돌 없음.
역할: JSON을 펼치지 않고 한 줄로 심각도 파악. 예: `"budget_reset (Timeout), canary_resume (500 Error)"`.

---

## 5. 영향 범위

### 5.1 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `recovery_coordinator.py` | `_fail_session()` 순서 변경 + DLQ 호출 추가, `_store_failure_to_dlq()` 신규 |

**v1.0.0 대비 축소**: `_attempt_compensation()` 변경 삭제. 보상 실패 DLQ를 루프에서 호출하는 대신 `_fail_session()`에서 `comp_result` 기반 Aggregation으로 통합.

### 5.2 의존 관계

```
_fail_session()
  ├── _attempt_compensation()                    ← 244번 문서 (변경 없음)
  │     └── return CompensationResult            ← failed_steps 수집
  │
  ├── session.status = FAILED + _save_session()  ← 영속화 완료
  ├── logger.error(...)                          ← SIGKILL 방어 (Lock 해제 전)
  ├── _recovery_lock.release()                   ← Lock 해제
  │
  └── _store_failure_to_dlq(session, error, comp_result)  ← Lock-free DLQ 저장
        ├── mask_sensitive_fields(session.to_dict())       ← PII 마스킹
        ├── compensation_failures Aggregation              ← 보상 실패 통합
        ├── recommended_action 동적 분기                    ← manual_review / manual_consistency_check
        └── store_to_dlq()                                 ← 기존 DLQ 편의 함수
              └── DLQService.store_failure()
                    └── 3단계 Fallback (LMDB → JSONL → stderr)
```

### 5.3 하위 호환성

| 항목 | 호환성 |
|------|--------|
| `_fail_session()` 기존 동작 | ✅ DLQ 저장은 추가 동작, 실패해도 Fail-Open |
| DLQ 비활성화 시 | ✅ `store_to_dlq()`가 `DLQEntryResult.failed("DLQ is disabled")` 반환 → no-op |
| DLQ import 실패 시 | ✅ `try/except`로 무시 |
| `mask_sensitive_fields` import 실패 시 | ✅ 전체 `try/except` 블록 내 — 마스킹 실패 시 DLQ 저장 자체를 건너뜀 (Fail-Open) |
| `resume_recovery()` | ✅ 기존과 동일. DLQ 엔트리는 참조 정보, 복구 재개는 세션 기반 |
| `_attempt_compensation()` | ✅ 변경 없음. 기존 `CompensationResult` 반환 구조 그대로 유지 |

### 5.4 기존 연동 시스템 영향

| 시스템 | 영향 |
|--------|------|
| `DLQService` | 기존 `store_failure()` API 그대로 사용 |
| `RecoveryAuditRecorder` | 없음 — Audit과 DLQ는 독립적 보완 경로 |
| DLQ Replay | `domain="selfhealing"` 필터로 복구 실패만 조회 가능 |
| DLQ Dashboard | 자동 노출 (기존 리스트 UI에서 `selfhealing` 도메인으로 필터) |
| `mask_sensitive_fields` | 기존 유틸리티 재사용. 새 dict 반환 특성상 원본 미영향 |

---

## 6. 운영 활용 시나리오

### 6.1 복구 실패 모니터링

```python
# DLQ에서 복구 실패 조회
entries = dlq_service.get_pending_entries(domain="selfhealing")
for entry in entries:
    if entry.failure_type == "RECOVERY_SESSION_FAILED":
        session_id = entry.entity_id
        failed_step = entry.metadata.get("failed_step", {})
        print(f"Session {session_id} failed at {failed_step.get('step_type')}")
```

### 6.2 보상 실패 추적 (Aggregation)

```python
# DLQ 1건에서 보상 실패 목록 확인
for entry in entries:
    comp_summary = entry.metadata.get("compensation_summary")
    if comp_summary:
        # JSON을 펼치지 않고 한 줄로 심각도 파악
        print(f"⚠️ Session {entry.entity_id}: {comp_summary}")
        # 출력: ⚠️ Session recovery-abc123: budget_reset (Timeout), canary_resume (500 Error)

    # 상세 보상 실패 목록
    comp_failures = entry.metadata.get("compensation_failures", [])
    for failure in comp_failures:
        step_type = failure["step_type"]
        forward_result = failure.get("forward_result", {})
        comp_error = failure["compensation_error"]
        # forward_result로 "무엇을 했는지" 확인 → 수동 되돌리기 가능
        print(f"  {step_type}: Forward={forward_result}, CompError={comp_error}")
```

### 6.3 보상 실패 시 운영자 Action 가이드

```python
# recommended_action에 따른 운영 대응
for entry in entries:
    if entry.recommended_action == "manual_consistency_check":
        # 보상 실패 → 데이터 불일치 가능 → 수동 확인 필요
        # next_action_hint에 affected 키가 포함됨
        print(f"🔴 CONSISTENCY CHECK REQUIRED: {entry.next_action_hint}")
        # 출력: 🔴 CONSISTENCY CHECK REQUIRED: Recovery session recovery-abc123 failed.
        #       Automatic compensation failed for: budget_reset (affected: success, multiplier).
        #       Verify affected state manually before resume_recovery().
    elif entry.recommended_action == "manual_review":
        # 보상 성공 또는 보상 대상 없음 → 일반 리뷰
        print(f"🟡 REVIEW: {entry.next_action_hint}")
```

---

## 7. 테스트 전략

| # | 테스트 | 검증 내용 |
|---|--------|-----------|
| 1 | `test_fail_session_stores_to_dlq` | `_fail_session()` 호출 시 DLQ에 엔트리 1건 생성 |
| 2 | `test_fail_session_dlq_fail_open` | DLQ 저장 실패해도 세션 FAILED 상태 정상 처리 |
| 3 | `test_fail_session_dlq_disabled` | DLQ 비활성화 시 예외 없이 진행 |
| 4 | `test_dlq_entry_contains_masked_snapshot` | `snapshot_data`에 PII 마스킹된 세션 상태 포함 |
| 5 | `test_dlq_entry_contains_failed_step_by_status` | `metadata.failed_step`이 `status == FAILED` 기반 탐색 결과 |
| 6 | `test_dlq_entry_aggregates_compensation_failures` | 보상 실패 5건 → DLQ 1건, `metadata.compensation_failures` 리스트 5항목 |
| 7 | `test_dlq_entry_compensation_summary` | `metadata.compensation_summary`에 요약 문자열 포함 |
| 8 | `test_dlq_recommended_action_dynamic` | 보상 실패 있으면 `"manual_consistency_check"`, 없으면 `"manual_review"` |
| 9 | `test_dlq_next_action_hint_includes_result_data_keys` | 보상 실패 시 `next_action_hint`에 `result_data` 키 포함 |
| 10 | `test_dlq_stored_after_lock_release` | DLQ 저장이 Lock 해제 후 수행됨 (호출 순서 검증) |
| 11 | `test_logger_error_before_lock_release` | `logger.error()`가 Lock 해제 전에 호출됨 |
| 12 | `test_dlq_entry_entity_id_is_session_id` | `entity_id`가 세션 ID와 동일 |
| 13 | `test_mask_sensitive_fields_no_side_effect` | 마스킹이 원본 세션 객체에 영향 없음 확인 |
