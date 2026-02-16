# 235. 분산 복구 부분 실패 처리 개선 — Saga 미도입, 기존 시스템 조합 해결

작성일: 2026-02-17
범위: `packages/selfhealing-python/src/selfhealing`
근거 기준: 코드 본문(추측 없음)

---

## 1) 배경

Saga/보상 트랜잭션(Temporal, AWS Step Functions 참조) 도입 검토 과정에서,
현재 셀프힐링 시스템의 분산 복구 흐름에 **부분 실패 시 비일관 상태 방치** 문제 3건이 식별되었다.

분석 결과, 3가지 문제 모두 Saga 패턴 도입 없이 **기존 시스템 컴포넌트의 조합 + 소규모 코드 개선**으로 해결 가능하다.

### Saga 미도입 결정 근거

1. **도메인 특성**: 복구 단계의 부작용이 "보상 가능"한 성격이 아님
   - `BUDGET_RESET`(1.0 설정) 보상 = 다시 5.0으로 → 장애 상태 복원이므로 위험
   - `HEALTH_CHECK` → 읽기 전용, 부작용 없음
   - `CANARY_RESUME` 실패 시 → 이미 일시 중지 상태 유지 (안전)
   - `GOVERNANCE_NORMAL` 실패 시 → STRICT 유지 (안전)
2. **기존 안전장치 충분**: `IdempotentStepHandlers`(멱등성 재시도), `DistributedRecoveryLock`(동시성 제어), `AtomicLevelTransition`(원자적 상태 전환)이 이미 Saga가 해결하려는 문제의 대부분을 커버
3. **부분 성공 상태의 실제 위험도 낮음**: "더 보수적인 상태"에 머무는 것이므로 서비스 안전성 측면에서 치명적이지 않음. Saga 보상(장애 상태로 되돌리기)이 오히려 위험

---

## 2) 식별된 3가지 문제와 해결 방안

---

### 문제 ① RecoveryCoordinator 부분 성공 상태 방치

**현상**: 복구 단계 1~3 성공 후 4단계 실패 시, 이전 성공 단계 결과가 그대로 남은 채 세션이 FAILED 처리된다.

**문제 코드**: `recovery_coordinator.py` L530-L555

```python
# 실패 시 _fail_session만 호출
else:
    step.status = RecoveryStatus.FAILED
    step.error_message = result.get("error", "Unknown error")
    self._fail_session(session, step.error_message)
```

`_fail_session()` (L1298-L1308)은 상태만 FAILED로 변경하고 락을 해제한다:

```python
def _fail_session(self, session, error):
    session.status = RecoveryStatus.FAILED
    session.abort_reason = error
    session.completed_at = datetime.now(timezone.utc).isoformat()
    # ← 이전 성공 단계 롤백 없음
    self._save_session(session)
    self._clear_active_session(session.namespace)
    self._recovery_lock.release(session.namespace, session.id)
```

**해결: `resume_recovery()` 메서드 추가 — 기존 컴포넌트 조합**

Saga 도입 없이, 기존 멱등성 인프라를 활용한 "실패 지점 재개" 메서드를 추가한다.

활용하는 기존 컴포넌트:

| 컴포넌트 | 파일 | 역할 |
|---|---|---|
| `IdempotentStepHandlerRegistry` | `idempotent_step_handlers.py` L280-L310 | 각 단계의 `IdempotencyRecord`를 `COMPLETED`/`FAILED` 상태로 영속 저장. 완료된 단계 재실행 시 캐시된 결과 반환 |
| `RecoverySession.current_step_index` | `recovery_state.py` L198-L210 | 실패 세션의 진행 위치가 저장되어 있음 |
| `_check_already_applied()` | `idempotent_step_handlers.py` 각 핸들러 | 비즈니스 레벨에서 이미 적용 여부 확인 (Budget=1.0?, Canary 중지 없음?, Governance=NORMAL?) |

**작동 원리**:

1. 실패한 세션의 `current_step_index` (= 마지막 실패 단계)를 가져옴
2. 새 세션을 해당 인덱스부터 시작
3. `IdempotentStepHandler.execute()`가 이미 완료된 단계를 자동 스킵 (L293-L310)
4. `_check_already_applied()`가 비즈니스 레벨에서도 중복 실행 방지

**`start_recovery()` 중복 세션 체크 (L393-L398)에서 FAILED 세션은 차단하지 않음** — 이미 해결되어 있음:

```python
# recovery_coordinator.py L393-L398
if active and active.status in (
    RecoveryStatus.IN_PROGRESS,
    RecoveryStatus.HEALTH_CHECK,
    RecoveryStatus.READY_TO_RESTORE,
):  # FAILED는 여기 없음 → 재시작 허용
    raise ValueError(f"Recovery already in progress: {active.id}")
```

**구현 코드**: `recovery_coordinator.py`에 `resume_recovery()` 추가

```python
def resume_recovery(
    self,
    namespace: str,
    initiated_by: str = "system",
) -> RecoverySession:
    """
    실패한 복구 세션을 마지막 실패 지점부터 재개.

    IdempotentStepHandlers가 이미 완료된 단계를 자동 스킵하므로,
    실패 지점의 단계만 재실행된다.

    Args:
        namespace: 네임스페이스
        initiated_by: 재개 주체

    Returns:
        새로 생성된 RecoverySession (실패 지점부터 시작)

    Raises:
        ValueError: 재개할 실패 세션이 없는 경우
    """
    with self._lock:
        # 1. 마지막 실패 세션 조회
        last_session = self.get_active_session(namespace)
        if not last_session or last_session.status != RecoveryStatus.FAILED:
            raise ValueError(
                f"No failed recovery session to resume for namespace={namespace}"
            )

        # 2. 실패 지점 정보 추출
        failed_step_index = last_session.current_step_index
        trigger_level = last_session.trigger_level

        logger.info(
            f"[Recovery] Resuming from step {failed_step_index}: "
            f"session={last_session.id}, level={trigger_level}"
        )

        # 3. 새 세션으로 재시작 (start_recovery가 FAILED 세션은 차단하지 않음)
        # _clear_active_session으로 이전 실패 세션 참조 제거
        self._clear_active_session(namespace)

        # 4. 새 세션 시작 — IdempotentStepHandlers가 완료된 단계 자동 스킵
        new_session = self.start_recovery(
            namespace=namespace,
            trigger_level=trigger_level,
            initiated_by=initiated_by,
        )

        # 5. 메타데이터에 재개 정보 기록
        new_session.metadata = new_session.metadata or {}
        new_session.metadata["resumed_from"] = last_session.id
        new_session.metadata["resumed_from_step"] = failed_step_index
        self._save_session(new_session)

        return new_session
```

---

### 문제 ② EmergencyCoordinator 연계 액션 부분 실패

**현상**: `on_emergency_level_changed()`에서 여러 연계 액션 순차 실행 시, 중간 실패해도 이미 실행된 액션의 상태가 로그에 충분히 기록되지 않고, 실패 후에도 나머지 액션을 계속 실행한다.

**문제 코드**: `coordinator.py` L260-L265

```python
# 5. 액션 실행
results = []
for action in actions:
    result = self._execute_action(action, namespace, trigger_event_id)
    results.append(result)
# ← 중간 실패해도 모든 액션 계속 실행
# ← 부분 실패 시 이미 실행된 액션 상태에 대한 명시적 로깅 없음

# 7. 결과 반환
return CoordinationResult(
    success=all(r.success for r in results),  # 하나라도 실패하면 False
    ...
)
```

**Phase 1 현재 상태**: `_execute_action_impl()` (L484-L500)이 **인메모리 상태 업데이트만** 수행하므로 실제 외부 부작용 없음:

```python
def _execute_action_impl(self, action, namespace, effective_ttl):
    # Phase 1: 로깅만 수행
    logger.info(f"[Coordinator] Executing {action.type.value}...")
    state = self.get_state(namespace)
    if action.type == ActionType.GOVERNANCE_STRICT:
        state.governance_mode = "STRICT"
    # Phase 3에서 실제 서비스 호출 추가 예정
```

**해결: 액션 루프에 부분 실패 로깅 + immediate 액션 fail-fast 추가**

활용하는 기존 컴포넌트:

| 컴포넌트 | 파일 | 역할 |
|---|---|---|
| `CoordinationAction.immediate` | `models.py` L43 | 즉시 실행 필수 여부 플래그 |
| `CoordinationResult.executed_actions` | `models.py` L310-L340 | 각 액션의 성공/실패를 개별 기록 |
| `CascadeEventAuditor` | `coordinator.py` L284-L320 | 각 액션의 `success`, `error_message`를 포함한 `effects` 리스트 기록 |

**구현 코드**: `coordinator.py`의 액션 실행 루프 개선

```python
# 변경 전 (coordinator.py L260-L265)
results = []
for action in actions:
    result = self._execute_action(action, namespace, trigger_event_id)
    results.append(result)

# 변경 후
results = []
for action in actions:
    result = self._execute_action(action, namespace, trigger_event_id)
    results.append(result)

    if not result.success:
        # 부분 실패 시 이미 실행된 액션 상태를 명시적으로 로깅
        succeeded = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        logger.warning(
            f"[Coordinator] Partial failure during level change: "
            f"namespace={namespace}, "
            f"succeeded=[{', '.join(r.action_type.value for r in succeeded)}], "
            f"failed=[{', '.join(r.action_type.value for r in failed)}], "
            f"remaining={len(actions) - len(results)} actions skipped"
        )

        # immediate 액션 실패 시 나머지 액션 중단 (안전 우선)
        if action.immediate:
            logger.error(
                f"[Coordinator] Immediate action failed, "
                f"aborting remaining actions: {action.type.value}"
            )
            break
```

---

### 문제 ③ RollbackService 핸들러 부분 실패

**현상**: 여러 컴포넌트 핸들러 순차 실행 시, 일부 성공 + 일부 실패인 경우 결과가 `FAILED`로만 구분되어 부분 성공 정보가 손실된다.

**문제 코드**: `rollback/service.py` L268-L275

```python
if errors:
    result.state = RollbackState.FAILED
    result.message = f"Rollback failed with {len(errors)} errors"
else:
    result.state = RollbackState.COMPLETED
    result.message = "Rollback completed successfully"
```

**현재 이미 기록되는 정보 (`RollbackResult` 모델, `rollback/models.py` L93-L107)**:

```python
@dataclass
class RollbackResult:
    request_id: str
    state: RollbackState
    affected_components: list[str] = field(default_factory=list)  # 성공한 컴포넌트들
    errors: list[str] = field(default_factory=list)               # 실패한 컴포넌트들
```

그리고 `_log_audit()` (L290-L304)에서 `affected_components`와 `errors` 모두 Audit에 기록된다.

**해결: `PARTIALLY_COMPLETED` 상태 추가 + 판정 로직 분기**

**구현 코드 ③-A**: `rollback/models.py`의 `RollbackState`에 상태 추가

```python
class RollbackState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"  # 신규 추가
    FAILED = "failed"
    CANCELLED = "cancelled"
```

**구현 코드 ③-B**: `rollback/service.py`의 판정 로직 수정

```python
# 변경 전
if errors:
    result.state = RollbackState.FAILED
    result.message = f"Rollback failed with {len(errors)} errors"
else:
    result.state = RollbackState.COMPLETED
    result.message = "Rollback completed successfully"

# 변경 후
if errors and affected:
    result.state = RollbackState.PARTIALLY_COMPLETED
    result.message = (
        f"Rollback partially completed: "
        f"{len(affected)} succeeded, {len(errors)} failed"
    )
elif errors:
    result.state = RollbackState.FAILED
    result.message = f"Rollback failed with {len(errors)} errors"
else:
    result.state = RollbackState.COMPLETED
    result.message = "Rollback completed successfully"
```

---

## 3) EmergencyCoordinator 에러 핸들링 개선 — 부분 실패 로깅 구현

이전 분석에서 "개선이 필요한 최소 영역"으로 식별된 부분이다.
Saga가 아닌 단순한 에러 핸들링 개선으로 해결한다.

### 3-1. 변경 대상

| 파일 | 변경 내용 |
|------|-----------|
| `coordinator.py` L260-L265 | 액션 실행 루프에 부분 실패 로깅 + immediate fail-fast 추가 |

### 3-2. 변경 전 코드 (coordinator.py L260-L265)

```python
# 5. 액션 실행
results = []
for action in actions:
    result = self._execute_action(action, namespace, trigger_event_id)
    results.append(result)
```

### 3-3. 변경 후 코드

```python
# 5. 액션 실행 (부분 실패 시 명시적 로깅 + immediate 액션 fail-fast)
results = []
for action in actions:
    result = self._execute_action(action, namespace, trigger_event_id)
    results.append(result)

    if not result.success:
        # 부분 실패 시 이미 실행된 액션 상태를 명시적으로 로깅
        succeeded = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        logger.warning(
            f"[Coordinator] Partial failure during level change: "
            f"namespace={namespace}, "
            f"succeeded=[{', '.join(r.action_type.value for r in succeeded)}], "
            f"failed=[{', '.join(r.action_type.value for r in failed)}], "
            f"remaining={len(actions) - len(results)} actions skipped"
        )

        # immediate 액션 실패 시 나머지 액션 중단 (안전 우선)
        if action.immediate:
            logger.error(
                f"[Coordinator] Immediate action failed, "
                f"aborting remaining actions: {action.type.value}"
            )
            break
```

### 3-4. 동작 시나리오

**시나리오 A: 3개 액션 중 2번째(immediate=True) 실패**

```
actions = [
    CoordinationAction(type=GOVERNANCE_STRICT, immediate=True),
    CoordinationAction(type=CANARY_ROLLBACK, immediate=True),   # ← 실패
    CoordinationAction(type=BUDGET_MULTIPLIER, immediate=False),
]
```

결과:
1. `GOVERNANCE_STRICT` 실행 → 성공
2. `CANARY_ROLLBACK` 실행 → 실패
3. 로그: `"Partial failure: succeeded=[governance_strict], failed=[canary_rollback], remaining=1 actions skipped"`
4. `immediate=True`이므로 `BUDGET_MULTIPLIER` 스킵
5. `CoordinationResult.success=False`, `executed_actions`에 1~2번 결과만 포함

**시나리오 B: 3개 액션 중 3번째(immediate=False) 실패**

```
actions = [
    CoordinationAction(type=GOVERNANCE_STRICT, immediate=True),
    CoordinationAction(type=CANARY_ROLLBACK, immediate=True),
    CoordinationAction(type=NOTIFICATION, immediate=False),  # ← 실패
]
```

결과:
1. `GOVERNANCE_STRICT` 실행 → 성공
2. `CANARY_ROLLBACK` 실행 → 성공
3. `NOTIFICATION` 실행 → 실패
4. 로그: `"Partial failure: succeeded=[governance_strict, canary_rollback], failed=[notification], remaining=0 actions skipped"`
5. `immediate=False`이므로 중단 없음 (이미 마지막)
6. `CoordinationResult.success=False`, `executed_actions`에 3개 결과 모두 포함

---

## 4) 전체 구현 체크리스트

| # | 파일 | 변경 | 상태 |
|---|------|------|------|
| ①-A | `recovery_coordinator.py` | `resume_recovery()` 메서드 추가 | 구현 |
| ②-A | `coordinator.py` L260-L265 | 액션 루프 부분 실패 로깅 + immediate fail-fast | 구현 |
| ③-A | `rollback/models.py` | `RollbackState.PARTIALLY_COMPLETED` 추가 | 구현 |
| ③-B | `rollback/service.py` L268-L275 | 부분 성공 판정 분기 추가 | 구현 |

---

## 5) 해결 방안 요약 — Saga 불필요, 기존 조합 충분

| 문제 | Saga 필요? | 해결 방법 | 활용하는 기존 컴포넌트 |
|------|-----------|-----------|----------------------|
| ① RecoveryCoordinator 부분 성공 방치 | **No** | `resume_recovery()` 추가 (실패 지점 재개) | `IdempotentStepHandlerRegistry`, `RecoverySession.current_step_index`, `_check_already_applied()` |
| ② EmergencyCoordinator 액션 부분 실패 | **No** | 부분 실패 로깅 + immediate fail-fast | `CoordinationAction.immediate`, `CoordinationResult.executed_actions`, `CascadeEventAuditor` |
| ③ RollbackService 핸들러 부분 실패 | **No** | `PARTIALLY_COMPLETED` 상태 + 판정 분기 | `RollbackResult.affected_components`, `RollbackResult.errors`, `_log_audit()` |
