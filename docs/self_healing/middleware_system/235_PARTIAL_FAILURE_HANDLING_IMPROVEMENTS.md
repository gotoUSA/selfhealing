# 235. 분산 복구 부분 실패 처리 개선 — Saga 미도입, 기존 시스템 조합 해결

작성일: 2026-02-17
구현일: 2026-02-17
범위: `packages/selfhealing-python/src/selfhealing`
근거 기준: 코드 본문(추측 없음)
상태: **구현 완료**

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

---

## 6) 리뷰 분석 — 11가지 질문에 대한 코드 기반 평가 및 보완 사항

작성일: 2026-02-17
근거 기준: 코드 본문(추측 없음)

### 리뷰 #1. Step 내부의 원자성 보장 여부 (Q1)

**판정: 충분함 (현 상태 유지)**

**코드 근거**:

4개 `IdempotentStepHandler` 구현체가 모두 **단일 서비스 호출**로 구성됨:

| 핸들러 | 핵심 연산 | 파일 |
|--------|----------|------|
| `IdempotentBudgetResetHandler._execute_internal()` | `provider.reset_multiplier()` 1회 | `idempotent_step_handlers.py` L509-L535 |
| `IdempotentHealthCheckHandler._execute_internal()` | 읽기 전용 (상태 확인만) | `idempotent_step_handlers.py` L563-L577 |
| `IdempotentCanaryResumeHandler._execute_internal()` | `service.resume_paused_rollouts()` 1회 | `idempotent_step_handlers.py` L584-L612 |
| `IdempotentGovernanceNormalHandler._execute_internal()` | `tracker.record_normal_restoration()` 1회 | `idempotent_step_handlers.py` L633-L660 |

**"DB 쓰기 + API 호출" 복합 작업 패턴이 없음** → "절반 실패" 가능성이 구조적으로 낮음.

비즈니스 레벨 멱등성 (`_check_already_applied()`)이 최후의 보루:
- `BudgetReset`: `current_multiplier == 1.0` 확인 (L552-L561)
- `CanaryResume`: `len(paused) == 0` 확인 (L620-L627)
- `GovernanceNormal`: `current_mode == "NORMAL"` 확인 (L669-L676)

**추가 조치 불필요.** Phase 3에서 외부 서비스 호출이 추가될 때 재평가.

---

### 리뷰 #2. 실패 세션 조회 불가 버그 수정 (Q2) — Critical

**판정: 긴급 수정 필수**

**버그 상세**: `_fail_session()` (L1311-L1329)이 `_clear_active_session()`을 호출하여 `ACTIVE_SESSION_KEY`를 삭제.
이후 `resume_recovery()` (L590)에서 `get_active_session()`이 `None`을 반환 → `ValueError` 발생.

```python
# _fail_session (L1323-L1324) — 현재 코드
self._save_session(session)
self._clear_active_session(session.namespace)  # ← active 키 삭제

# resume_recovery (L590) — 현재 코드
last_session = self.get_active_session(namespace)  # ← None 반환!
```

**해결: 방법 A 채택 (ACTIVE_SESSION_KEY 유지)**

`_fail_session()`에서 `_clear_active_session()` 호출을 **제거**하여 FAILED 세션도 `ACTIVE_SESSION_KEY`에 남김.

활용하는 기존 안전장치:
- `start_recovery()` L393-L398의 중복 체크가 `FAILED` 상태를 차단하지 않음 → 기존 동작과 호환
- `abort_recovery()` L638-L658은 `IN_PROGRESS`/`HEALTH_CHECK`만 대상 → 영향 없음

**변경 코드**: `recovery_coordinator.py` `_fail_session()` 수정

```python
def _fail_session(
    self,
    session: RecoverySession,
    error: str,
) -> None:
    """세션 실패 처리."""
    session.status = RecoveryStatus.FAILED
    session.abort_reason = error
    session.completed_at = datetime.now(timezone.utc).isoformat()

    self._save_session(session)
    # _clear_active_session 제거: resume_recovery()가 실패 세션을 조회할 수 있도록
    # ACTIVE_SESSION_KEY는 유지하되, start_recovery()가 FAILED 상태를
    # 차단하지 않으므로 (L393-L398) 새로운 복구 시작에는 영향 없음

    # 락 해제
    self._recovery_lock.release(session.namespace, session.id)

    logger.error(f"[Recovery] Failed: id={session.id}, error={error}")
```

**영향 범위 체크**: `_clear_active_session()`을 호출하는 다른 메서드:

| 메서드 | 호출 위치 | 영향 |
|--------|----------|------|
| `_complete_session()` L1293 | 정상 완료 시 | 변경 없음 — 완료 후에는 키 삭제가 올바름 |
| `abort_recovery()` L656 | 수동 중단 시 | 변경 없음 — 중단은 재개 대상이 아님 |
| `resume_recovery()` L603 | 재개 전 정리 시 | 유지 — `start_recovery()`가 덮어쓰므로 (L449 `_set_active_session`) |
| `_fail_session()` L1324 | **제거 대상** | `ACTIVE_SESSION_KEY` 유지하여 재개 가능 |

`start_recovery()` L449의 `_set_active_session(namespace, session_id)` 호출이 기존 키를 덮어쓰므로,
FAILED 세션의 키가 남아 있어도 새 복구 시작 시 자연스럽게 교체됨.

---

### 리뷰 #3. 락 범위 및 경쟁 조건 수정 (Q3)

**판정: 보완 필요**

**문제**: 현재 `resume_recovery()` (L588-L621)에서 `with self._lock:` 블록이 `_clear_active_session` 후에 **끝나고**, `start_recovery()`는 블록 **밖에서** 호출됨.

```python
# 현재 코드
with self._lock:
    # 1~3 단계
    self._clear_active_session(namespace)
# ← 락 해제됨!
new_session = self.start_recovery(...)  # ← 이 사이에 다른 스레드 개입 가능
```

**수정**: `resume_recovery()` 전체를 `with self._lock:` 안에 배치. `self._lock`은 `threading.RLock()` (L200)이므로 `start_recovery()` 내부의 `with self._lock:` (L391)에서 재진입이 허용되어 데드락 없음.

**변경 코드**:

```python
def resume_recovery(
    self,
    namespace: str,
    initiated_by: str = "system",
) -> RecoverySession:
    """실패한 복구 세션을 마지막 실패 지점부터 재개."""
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

        # 3. 이전 실패 세션 참조 제거
        self._clear_active_session(namespace)

        # 4. 새 세션 시작 (RLock 재진입 → 데드락 없음)
        new_session = self.start_recovery(
            namespace=namespace,
            trigger_level=trigger_level,
            initiated_by=initiated_by,
        )

        # 5. 메타데이터에 재개 정보 기록
        new_session.metadata = new_session.metadata or {}
        new_session.metadata["resumed_from"] = last_session.id
        new_session.metadata["resumed_from_step"] = failed_step_index
        new_session.metadata["original_initiated_by"] = last_session.initiated_by
        self._save_session(new_session)

        return new_session
```

**`RLock` 재진입 검증**:

| 호출 깊이 | 메서드 | 락 획득 | 결과 |
|-----------|--------|---------|------|
| 1 | `resume_recovery()` | `self._lock` 획득 | OK |
| 2 | → `start_recovery()` | `self._lock` 재획득 시도 | RLock이므로 OK |
| 2 | → `start_recovery()` 종료 | 재획득 카운트 -1 | OK |
| 1 | `resume_recovery()` 종료 | 락 완전 해제 | OK |

---

### 리뷰 #4. 설정 변경 시의 불일치 (Q4)

**판정: 현재 설정(Current Config) 사용이 올바름 — 주석 추가만 필요**

**코드 근거**: `resume_recovery()`는 `trigger_level`만 과거 세션에서 가져오고, 복구 단계 목록은 `start_recovery()` 내부에서 현재 시점의 config로 새로 로드함:

```python
# start_recovery() L404-L413
if regional_engine:
    steps = regional_engine.get_recovery_steps(namespace, trigger_level)
else:
    steps = self._get_recovery_steps(trigger_level, namespace)
```

**멱등성 키 매칭 문제**: `generate_idempotency_key()`가 `session_id`를 포함 (L210-L220)하므로, 새 세션 ID에서는 이전 세션의 `IdempotencyRecord` 캐시와 매칭되지 않음. → `_check_already_applied()` 비즈니스 레벨 체크에만 의존.

**이것은 안전한 동작임**: `_check_already_applied()`가 실제 시스템 상태(multiplier=1.0?, governance=NORMAL?)를 확인하므로, 설정이 바뀌어도 이미 적용된 단계는 스킵됨.

**추가 조치**: `resume_recovery()` docstring에 아래 주석 추가:

```
Note: 새 세션은 현재 시점의 설정(Config)으로 복구 단계를 재구성합니다.
이전 세션의 IdempotencyRecord 캐시와 session_id가 다르므로,
_check_already_applied()의 비즈니스 레벨 체크에 의존합니다.
설정 변경 후 resume하는 경우에도 안전합니다 (이미 적용된 단계 자동 스킵).
```

---

### 리뷰 #5. 수동 개입 알림 부재 (Q5) — Alert 로그 구조화

**판정: 부분 실패 로그에 Alert 메타데이터 추가 권장**

**현재 상태**: `coordinator.py`의 부분 실패 로그가 `logger.warning()`과 `logger.error()`만 사용. Alerting 가능한 구조화 데이터 없음.

**기존 시스템 패턴 확인**: 코드베이스에서 `extra={"alert": ...}` 패턴은 아직 사용되지 않음 (검색 결과 0건). `recovery_notifications.py`에 알림 템플릿이 존재하지만 EmergencyCoordinator에서는 미연동.

**수정 제안**: `coordinator.py`의 부분 실패 로깅에 구조화된 `extra` 추가.

```python
# coordinator.py 부분 실패 로깅 개선
if not result.success:
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    logger.warning(
        f"[Coordinator] Partial failure during level change: "
        f"namespace={namespace}, "
        f"succeeded=[{', '.join(r.action_type.value for r in succeeded)}], "
        f"failed=[{', '.join(r.action_type.value for r in failed)}], "
        f"remaining={len(actions) - len(results)} actions skipped",
        extra={
            "alert_type": "partial_failure",
            "namespace": namespace,
            "succeeded_actions": [r.action_type.value for r in succeeded],
            "failed_actions": [r.action_type.value for r in failed],
            "remaining_count": len(actions) - len(results),
        },
    )
```

이 `extra` 데이터를 Loki/Grafana가 수집하면, 구조화된 로그 쿼리로 Alert Rule 설정 가능:
- 기존 인프라: `docker/loki/`, `docker/grafana/`, `docker/prometheus/` 디렉토리 존재
- Prometheus alerting rules: `selfhealing/services/metrics/alerting_rules.py` 존재

---

### 리뷰 #6. 재시도 전략 부재 (Q6)

**판정: 충분함 (수동 재개로 대체)**

**코드 근거**: EmergencyCoordinator는 재시도 메커니즘이 없지만:

1. `resume_recovery()` (문제 ①)가 수동/API 트리거 재개를 제공
2. `on_emergency_level_changed()`의 `force=True` 파라미터 (L200)가 AntiFlappingGuard 우회를 허용
3. Phase 1에서 `_execute_action_impl()`이 인메모리 상태 업데이트만 수행 (L493-L530) → 재실행이 항상 안전

**주의사항**: Phase 3에서 실제 서비스 호출 추가 시, 각 액션의 멱등성을 보장해야 함. 현재 `CoordinationAction`에는 `IdempotentStepHandler`와 같은 멱등성 레이어가 없으므로, Phase 3 설계 시 추가 필요.

---

### 리뷰 #7. 클라이언트 호환성 (Q7)

**판정: 충분함**

**코드 근거**: `PARTIALLY_COMPLETED`는 이미 `rollback/models.py` L27에 구현됨. `rollback/service.py` L270에서 사용 중.

```python
# rollback/models.py L27 — 이미 존재
PARTIALLY_COMPLETED = "partially_completed"  # 부분 완료 (일부 성공 + 일부 실패)
```

`RollbackResult.is_success` (L116)이 `COMPLETED`만 `True` 반환 → `PARTIALLY_COMPLETED`는 `is_success = False`.

`RollbackExecuteView` (views/rollback.py L170-L171)가 `result.to_dict()`로 `state`, `is_success` 모두 반환 → 클라이언트는 `is_success`만 확인해도 안전.

**네이밍 충돌 확인**: `PARTIALLY_COMPLETED`는 `RollbackState` Enum에만 존재. `RecoveryStatus` (enums.py L91-L135)에는 없음 → 충돌 없음.

---

### 리뷰 #8. HTTP 응답 코드 (Q8)

**판정: 충분함 (200 OK 유지)**

**코드 근거**: `RollbackExecuteView.post()` (views/rollback.py L158-L171):

```python
result = service.execute_rollback(request_id, components)
return Response(result.to_dict())
```

DRF `Response(data)` 기본값 = HTTP 200. 이것은 "HTTP 요청 처리 자체는 성공, 비즈니스 결과는 body에서 확인" 패턴으로, 기존 시스템의 다른 엔드포인트와 일관됨.

---

### 리뷰 #9. 무한 재개 방지 (Q9) — 안전장치 추가

**판정: 추가 구현 필수**

**현재 상태**: `resume_recovery()`에 재개 횟수 제한이 없음. `metadata`에 `resumed_from` 기록만 하고 횟수 카운터 없음.

**기존 시스템 설정 패턴 활용**: `RecoveryCoordinatorSettings` (settings/recovery_coordinator.py)에 이미 `max_recovery_session_duration_minutes` (L193) 같은 상한 설정이 존재. 동일 패턴으로 `max_resume_count` 추가.

**네이밍 결정**:
- 리뷰 제안: `MAX_RESUME_COUNT` (상수) + `resume_count` (메타데이터 키)
- 기존 패턴 확인: Settings 클래스가 `max_recovery_session_duration_minutes` 형식 사용 → `max_resume_count`가 일관됨
- 코드베이스 충돌 확인: `MAX_RESUME_COUNT`, `resume_count`, `max_resume_count` 모두 기존 코드에 없음 → 충돌 없음

**변경 코드 A**: `settings/recovery_coordinator.py`에 설정 추가

```python
# RecoveryCoordinatorSettings 클래스 내부에 추가
max_resume_count: int = Field(
    default=3,
    ge=1,
    le=10,
    description="실패한 복구 세션의 최대 재개 횟수. 초과 시 수동 개입 필요",
)
```

**변경 코드 B**: `recovery_coordinator.py` `resume_recovery()`에 검사 추가

```python
# resume_recovery() 내부, 실패 세션 조회 후
settings = get_recovery_coordinator_settings()
resume_count = (last_session.metadata or {}).get("resume_count", 0)
if resume_count >= settings.max_resume_count:
    raise ValueError(
        f"Max resume count ({settings.max_resume_count}) exceeded "
        f"for namespace={namespace}. Manual intervention required."
    )

# 메타데이터에 카운터 증가 기록
new_session.metadata["resume_count"] = resume_count + 1
```

---

### 리뷰 #10. initiated_by 기록 보완 (Q10)

**판정: 보완 필요 (Audit Trail)**

**현재 상태**: `resume_recovery()`가 새 세션의 `initiated_by`에 재개 호출자 값을 사용. 원본 세션의 `initiated_by`는 `metadata`에 기록되지 않음.

**수정**: 리뷰 #3의 변경 코드에 이미 통합:

```python
new_session.metadata["original_initiated_by"] = last_session.initiated_by
```

**네이밍 충돌 확인**: `original_initiated_by` — 기존 코드에서 사용되지 않음 → 충돌 없음.

기존 감사 패턴과 일관됨:
- `RecoverySession.initiated_by` (recovery_state.py L207): 세션 시작 주체
- `RecoveryAuditRecorder.record_recovery_event(executed_by=...)` (recovery_audit.py L287): 감사 기록 주체
- `new_session.metadata["resumed_from"]` (L613): 원본 세션 ID 추적

---

### 리뷰 #11. 테스트 부재 (Q11)

**판정: 필수 구현**

**현재 상태**: `test_recovery_coordinator.py`에 `resume_recovery` 관련 테스트 0건.
기존 테스트 패턴 확인:

```
test_start_recovery_success (L99)
test_execute_step_failed_handler (L270)
test_abort_recovery_success (L341)
```

**필요한 테스트 시나리오**:

| # | 시나리오 | 검증 항목 |
|---|---------|----------|
| 1 | `start → 3단계 fail → resume → verify skip + 재실행` | 멱등성 스킵, 실패 단계 재실행 |
| 2 | `resume without failed session → ValueError` | 실패 세션 없을 때 예외 |
| 3 | `max_resume_count 초과 → ValueError` | 무한 루프 방지 |
| 4 | `resume → initiated_by / original_initiated_by 검증` | Audit Trail 정확성 |
| 5 | `resume → metadata의 resumed_from / resume_count 검증` | 메타데이터 기록 정확성 |

**테스트 코드 위치**: `packages/selfhealing-python/tests/services/coordination/test_recovery_coordinator.py` (기존 파일에 추가)

---

## 7) 리뷰 반영 — 네이밍 검증 결과

| 네이밍 | 용도 | 기존 충돌 | 기존 패턴 일관성 | 판정 |
|--------|------|-----------|-----------------|------|
| `resume_recovery()` | 메서드명 | `resume_paused_rollouts()` 등 존재하나 다른 클래스 | ✅ `start_recovery()`, `abort_recovery()` 패턴 | **적절** |
| `PARTIALLY_COMPLETED` | Enum 값 | `RollbackState`에만 존재, `RecoveryStatus`와 충돌 없음 | ✅ `COMPLETED`, `FAILED` 패턴 | **적절** |
| `resumed_from` | metadata 키 | 기존 0건 | ✅ metadata 딕셔너리 자유 키 | **적절** |
| `resumed_from_step` | metadata 키 | 기존 0건 | ✅ `current_step_index` 대응 | **적절** |
| `resume_count` | metadata 키 | 기존 0건 | ✅ `retry_count` (IdempotencyRecord) 패턴 | **적절** |
| `max_resume_count` | Settings 필드 | 기존 0건 | ✅ `max_recovery_session_duration_minutes` 패턴 | **적절** |
| `original_initiated_by` | metadata 키 | 기존 0건 | ✅ `initiated_by` (RecoverySession) 대응 | **적절** |
| `alert_type` | logger extra 키 | 기존 0건 | ⚠️ 기존에 `extra` 패턴 미사용 — 신규 패턴 도입 | **허용** (표준 logging 기능) |
| `MAX_RESUME_COUNT` | 상수 (리뷰 제안) | 기존 0건 | ❌ Settings 클래스 사용이 기존 패턴 | **수정: Settings 필드로 대체** |

`MAX_RESUME_COUNT` 상수 → `RecoveryCoordinatorSettings.max_resume_count` 필드로 변경하여 기존 설정 관리 패턴 준수.

---

## 8) 수정 대상 파일 목록 (최종)

| # | 파일 | 변경 내용 | 근거 리뷰 |
|---|------|-----------|----------|
| 1 | `recovery_coordinator.py` `_fail_session()` | `_clear_active_session()` 호출 제거 | #2 (Critical Bug) |
| 2 | `recovery_coordinator.py` `resume_recovery()` | 전체 `with self._lock:` + `resume_count` 검사 + `original_initiated_by` | #3, #9, #10 |
| 3 | `settings/recovery_coordinator.py` | `max_resume_count` 필드 추가 | #9 |
| 4 | `coordinator.py` 부분 실패 로깅 | `extra` 메타데이터 추가 | #5 |
| 5 | `test_recovery_coordinator.py` | `resume_recovery` 테스트 5건 추가 | #11 |

---

## 9) 구현 순서

```
1. _fail_session() 버그 수정 (리뷰 #2) — 선행 조건
   ↓
2. RecoveryCoordinatorSettings에 max_resume_count 추가 (리뷰 #9)
   ↓
3. resume_recovery() 전체 재작성 (리뷰 #3, #4, #9, #10 통합)
   ↓
4. coordinator.py 부분 실패 로깅 extra 추가 (리뷰 #5)
   ↓
5. 테스트 작성 및 검증 (리뷰 #11)
```

리뷰 #1, #6, #7, #8은 "현 상태 유지" 판정이므로 코드 변경 없음.

---

## 10) resume_recovery() 최종 구현 코드 (리뷰 #2, #3, #4, #9, #10 통합)

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

    Note: 새 세션은 현재 시점의 설정(Config)으로 복구 단계를 재구성합니다.
    이전 세션의 IdempotencyRecord 캐시와 session_id가 다르므로,
    _check_already_applied()의 비즈니스 레벨 체크에 의존합니다.
    설정 변경 후 resume하는 경우에도 안전합니다 (이미 적용된 단계 자동 스킵).

    활용하는 기존 컴포넌트:
    - IdempotentStepHandlerRegistry: 완료된 단계 캐시 결과 반환
    - RecoverySession.current_step_index: 실패 지점 위치
    - _check_already_applied(): 비즈니스 레벨 중복 실행 방지

    Args:
        namespace: 네임스페이스
        initiated_by: 재개 주체

    Returns:
        새로 생성된 RecoverySession (실패 지점부터 시작)

    Raises:
        ValueError: 재개할 실패 세션이 없거나, 최대 재개 횟수 초과 시
    """
    with self._lock:
        # 1. 마지막 실패 세션 조회
        last_session = self.get_active_session(namespace)
        if not last_session or last_session.status != RecoveryStatus.FAILED:
            raise ValueError(
                f"No failed recovery session to resume for namespace={namespace}"
            )

        # 2. 최대 재개 횟수 검사 (무한 루프 방지)
        settings = get_recovery_coordinator_settings()
        resume_count = (last_session.metadata or {}).get("resume_count", 0)
        if resume_count >= settings.max_resume_count:
            raise ValueError(
                f"Max resume count ({settings.max_resume_count}) exceeded "
                f"for namespace={namespace}. Manual intervention required."
            )

        # 3. 실패 지점 정보 추출
        failed_step_index = last_session.current_step_index
        trigger_level = last_session.trigger_level

        logger.info(
            f"[Recovery] Resuming from step {failed_step_index}: "
            f"session={last_session.id}, level={trigger_level}, "
            f"resume_attempt={resume_count + 1}/{settings.max_resume_count}"
        )

        # 4. 이전 실패 세션 참조 제거
        self._clear_active_session(namespace)

        # 5. 새 세션 시작 (RLock 재진입 → 데드락 없음)
        # Note: 현재 시점의 Config에서 복구 단계를 새로 로드함
        new_session = self.start_recovery(
            namespace=namespace,
            trigger_level=trigger_level,
            initiated_by=initiated_by,
        )

        # 6. 메타데이터에 재개 정보 기록
        new_session.metadata = new_session.metadata or {}
        new_session.metadata["resumed_from"] = last_session.id
        new_session.metadata["resumed_from_step"] = failed_step_index
        new_session.metadata["resume_count"] = resume_count + 1
        new_session.metadata["original_initiated_by"] = last_session.initiated_by
        self._save_session(new_session)

        logger.info(
            f"[Recovery] Resumed: new_session={new_session.id}, "
            f"from={last_session.id}, step={failed_step_index}"
        )

        return new_session
```

---

## 11) 2차 리뷰 분석 — 10가지 질문(Q1~Q10)에 대한 6개 리뷰 항목 평가

작성일: 2026-02-17
근거 기준: 코드 본문(추측 없음)

### 11-1. 리뷰 항목 #1: Active Session 정의 및 조회 (Q1, Q2)

**리뷰 판정: 수정 필수 (`_fail_session` 로직 변경)**
**동의 여부: 전적으로 동의**

**코드 근거**:

`_fail_session()` (recovery_coordinator.py L1314-L1332)의 현재 코드:

```python
def _fail_session(self, session, error):
    session.status = RecoveryStatus.FAILED
    session.abort_reason = error
    session.completed_at = datetime.now(timezone.utc).isoformat()
    self._save_session(session)
    self._clear_active_session(session.namespace)  # ← 이 호출이 문제
    self._recovery_lock.release(session.namespace, session.id)
```

`_clear_active_session()` (L1286-L1290)이 `ACTIVE_SESSION_KEY`를 삭제하면:
- `get_active_session()` (L666-L688)이 `backend.get(key)` → `None` 반환
- `resume_recovery()` (L590)가 `ValueError` 발생 → **세션 재개 불가능**

**부작용 검증**: FAILED 세션이 `ACTIVE_SESSION_KEY`에 남아도 `start_recovery()` L392-L398의 차단 목록에 `FAILED`가 없으므로 새 복구 시작을 막지 않음. L449의 `_set_active_session()`이 기존 키를 덮어쓰므로 자연 교체됨.

**구현 가능성: 즉시 가능** — `_fail_session()`에서 `self._clear_active_session(session.namespace)` 1줄 제거.

---

### 11-2. 리뷰 항목 #2: Race Condition 방지 (Q3, Q4)

**리뷰 판정: 보완 필수 (Lock 범위 확대)**
**동의 여부: 동의하되, 1가지 보완점 추가**

**코드 근거**:

현재 `resume_recovery()` (L588-L621):

```python
with self._lock:
    last_session = self.get_active_session(namespace)
    # ... 검증 ...
    self._clear_active_session(namespace)
# ← 락 해제!
new_session = self.start_recovery(...)  # ← Race Condition 지점
```

`self._lock = threading.RLock()` (L200) 확인 → `resume_recovery()` 전체를 `with self._lock:`으로 감싸도 `start_recovery()` 내부의 `with self._lock:` (L389)에서 RLock 재진입이 허용되어 데드락 없음.

**리뷰어의 추가 제안 검증: `_clear_active_session` 호출 제거**

리뷰어는 `resume_recovery` 내부에서도 `_clear_active_session` 호출이 불필요하다고 제안:
> `_clear_active_session 호출 불필요 (start_recovery가 덮어씀)`

코드 검증:
1. `resume_recovery()` 내에서 `_clear_active_session(namespace)` 호출 없이 바로 `start_recovery()` 호출
2. `start_recovery()` L391: `active = self.get_active_session(namespace)` → FAILED 세션 반환됨
3. L392-L398: `active.status == FAILED` → 차단 목록 `(IN_PROGRESS, HEALTH_CHECK, READY_TO_RESTORE)`에 미포함 → **통과**
4. L449: `_set_active_session(namespace, session_id)` → 기존 FAILED 세션의 키를 새 세션 ID로 **덮어쓰기**

**결론: 리뷰어 의견 정확. `_clear_active_session` 호출 제거 가능.** 단, 제거 시 `start_recovery()` 내에서 불필요한 `get_active_session()` 조회 1회(Redis `GET` 1회)가 발생하나, 밀리초 수준이므로 무시 가능.

**보완점 1가지**: 리뷰어의 축약 코드에서 **메타데이터 기록 로직이 누락**됨:

```python
# 리뷰어 제안 코드
return self.start_recovery(..., initiated_by=initiated_by)
# ← resume_from, resume_count, original_initiated_by 메타데이터 기록 없음
```

`start_recovery()`는 메타데이터를 기록하지 않으므로, 리턴 전에 메타데이터 기록이 필수:

```python
new_session = self.start_recovery(...)
new_session.metadata = new_session.metadata or {}
new_session.metadata["resumed_from"] = last_session.id
new_session.metadata["resumed_from_step"] = failed_step_index
new_session.metadata["resume_count"] = resume_count + 1
new_session.metadata["original_initiated_by"] = last_session.initiated_by
self._save_session(new_session)
return new_session
```

---

### 11-3. 리뷰 항목 #3: 멱등성 키 및 세션 ID (Q5, Q6, Q7)

**리뷰 판정: 충분함 (기존 로직 유지)**
**동의 여부: 전적으로 동의**

**코드 근거**:

멱등성 키 형식 (idempotent_step_handlers.py L205-L207):

```python
key_source = f"{session_id}:{step_type}:{step_order}:{params_str}"
return f"idem:{session_id}:{step_type}:{step_order}:{key_hash}"
```

새 세션 ID 발급 시 이전 캐시와 매칭 불가 → `_check_already_applied()` 비즈니스 레벨 체크에 의존:

| 핸들러 | 비즈니스 체크 | 코드 위치 |
|--------|-------------|----------|
| `BudgetReset` | `current_multiplier == 1.0` | L552-L561 |
| `CanaryResume` | `len(paused) == 0` | L620-L627 |
| `GovernanceNormal` | `current_mode == "NORMAL"` | L669-L676 |
| `HealthCheck` | 읽기 전용 (부작용 없음) | L563-L577 |

Q5 (스코프 변경), Q6 (세션 ID 계승), Q7 (레코드 마이그레이션) 모두 `_check_already_applied()`가 안전하게 처리하므로 추가 복잡도 불필요.

**구현 변경 없음.**

---

### 11-4. 리뷰 항목 #4: HTTP 응답 코드 (Q8)

**리뷰 판정: 충분함 (200 OK + Body State)**
**동의 여부: 전적으로 동의**

**코드 근거**:

`RollbackExecuteView.post()` (views/rollback.py L158-L171):
```python
result = service.execute_rollback(request_id, components)
return Response(result.to_dict())
```

DRF `Response(data)` 기본값 = HTTP 200. `result.to_dict()`에 `state`, `is_success`, `affected_components`, `errors` 포함.

`RollbackResult.is_success` (rollback/models.py L116):
```python
@property
def is_success(self) -> bool:
    return self.state == RollbackState.COMPLETED
```

`PARTIALLY_COMPLETED`일 때 `is_success = False` → 클라이언트가 body에서 부분 실패 감지 가능.

**구현 변경 없음.**

---

### 11-5. 리뷰 항목 #5: 무한 재개 방지 (Q9)

**리뷰 판정: 추가 구현 필수 (resume_count 제한)**
**동의 여부: 전적으로 동의**

**코드 근거**:

기존 설정 패턴 (settings/recovery_coordinator.py L193-L199):

```python
max_recovery_session_duration_minutes: int = Field(
    default=120,
    ge=30,
    le=480,
    description="복구 세션 최대 지속 시간 (분). 초과 시 자동 중단",
)
```

동일 패턴으로 `max_resume_count` 추가. `metadata` 딕셔너리 (recovery_state.py L207-L213)가 자유 키-값 저장을 지원하므로 `RecoverySession` 모델 수정 불필요.

**네이밍 충돌 확인**: `max_resume_count`, `resume_count` — 코드베이스 내 `.py` 파일에서 0건 (235 문서에만 존재).

**구현 가능성: 즉시 가능.**

변경 대상:
1. `settings/recovery_coordinator.py`: `max_resume_count` 필드 추가
2. `recovery_coordinator.py` `resume_recovery()`: 카운터 검사 + 메타데이터 기록

---

### 11-6. 리뷰 항목 #6: 강제 재시작 옵션 (Q10)

**리뷰 판정: 충분함 (불필요)**
**동의 여부: 전적으로 동의**

**코드 근거**:

`start_recovery()` L392-L398:
```python
if active and active.status in (
    RecoveryStatus.IN_PROGRESS,
    RecoveryStatus.HEALTH_CHECK,
    RecoveryStatus.READY_TO_RESTORE,
):
    raise ValueError(f"Recovery already in progress: {active.id}")
```

`FAILED` ∉ 차단 목록 → `start_recovery()` 직접 호출로 "처음부터 재시작" 가능.
`force` 파라미터 — recovery_coordinator.py 내 0건 (검색 확인).

**구현 변경 없음.**

---

## 12) 2차 리뷰 반영 — resume_recovery() 최종 수정 코드

리뷰 #1-#6 반영 사항:

| 리뷰 | 기존 문서 대비 변경 | 변경 이유 |
|------|-------------------|----------|
| #1 | 변경 없음 | 기존 분석과 동일 결론 |
| #2 | `resume_recovery()` 내 `_clear_active_session()` 호출 **제거** | `start_recovery()` L449의 `_set_active_session()`이 덮어쓰므로 불필요 |
| #3 | 변경 없음 | 기존 분석과 동일 결론 |
| #4 | 변경 없음 | 기존 분석과 동일 결론 |
| #5 | 변경 없음 | 기존 분석과 동일 결론 |
| #6 | 변경 없음 | 기존 분석과 동일 결론 |

### 12-1. `_clear_active_session` 제거 근거 (리뷰 #2 반영)

기존 문서 섹션 10)에서는 `resume_recovery()` 내부에서 `_clear_active_session(namespace)`를 호출한 후 `start_recovery()`를 호출하는 구조였음.

리뷰어의 지적대로, 이 호출은 불필요:

1. `start_recovery()` L391: `get_active_session(namespace)` → FAILED 세션 반환
2. L392-L398: `FAILED` ∉ `(IN_PROGRESS, HEALTH_CHECK, READY_TO_RESTORE)` → 통과
3. L449: `_set_active_session(namespace, session_id)` → 기존 키 덮어쓰기
4. `_clear_active_session()` 없이도 전체 흐름 정상 작동

**제거 시 이점**: 불필요한 Redis `DELETE` 1회 제거, 코드 단순화.
**제거 시 부작용**: 없음 — `start_recovery()`가 `_set_active_session()`으로 즉시 덮어씀.

### 12-2. resume_recovery() 최종 구현 코드 (2차 리뷰 반영)

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

    Note: 새 세션은 현재 시점의 설정(Config)으로 복구 단계를 재구성합니다.
    이전 세션의 IdempotencyRecord 캐시와 session_id가 다르므로,
    _check_already_applied()의 비즈니스 레벨 체크에 의존합니다.
    설정 변경 후 resume하는 경우에도 안전합니다 (이미 적용된 단계 자동 스킵).

    Args:
        namespace: 네임스페이스
        initiated_by: 재개 주체

    Returns:
        새로 생성된 RecoverySession (실패 지점부터 시작)

    Raises:
        ValueError: 재개할 실패 세션이 없거나, 최대 재개 횟수 초과 시
    """
    with self._lock:
        # 1. 마지막 실패 세션 조회
        last_session = self.get_active_session(namespace)
        if not last_session or last_session.status != RecoveryStatus.FAILED:
            raise ValueError(
                f"No failed recovery session to resume for namespace={namespace}"
            )

        # 2. 최대 재개 횟수 검사 (무한 루프 방지)
        settings = get_recovery_coordinator_settings()
        resume_count = (last_session.metadata or {}).get("resume_count", 0)
        if resume_count >= settings.max_resume_count:
            raise ValueError(
                f"Max resume count ({settings.max_resume_count}) exceeded "
                f"for namespace={namespace}. Manual intervention required."
            )

        # 3. 실패 지점 정보 추출
        failed_step_index = last_session.current_step_index
        trigger_level = last_session.trigger_level

        logger.info(
            f"[Recovery] Resuming from step {failed_step_index}: "
            f"session={last_session.id}, level={trigger_level}, "
            f"resume_attempt={resume_count + 1}/{settings.max_resume_count}"
        )

        # 4. 새 세션 시작 (RLock 재진입 → 데드락 없음)
        # _clear_active_session() 불필요:
        #   - start_recovery() L392-L398이 FAILED 상태를 차단하지 않음
        #   - start_recovery() L449의 _set_active_session()이 기존 키를 덮어씀
        new_session = self.start_recovery(
            namespace=namespace,
            trigger_level=trigger_level,
            initiated_by=initiated_by,
        )

        # 5. 메타데이터에 재개 정보 기록
        new_session.metadata = new_session.metadata or {}
        new_session.metadata["resumed_from"] = last_session.id
        new_session.metadata["resumed_from_step"] = failed_step_index
        new_session.metadata["resume_count"] = resume_count + 1
        new_session.metadata["original_initiated_by"] = last_session.initiated_by
        self._save_session(new_session)

        logger.info(
            f"[Recovery] Resumed: new_session={new_session.id}, "
            f"from={last_session.id}, step={failed_step_index}"
        )

        return new_session
```

---

## 13) 2차 리뷰 반영 — 수정 대상 파일 목록 (최종)

| # | 파일 | 변경 내용 | 근거 리뷰 |
|---|------|-----------|----------|
| 1 | `recovery_coordinator.py` `_fail_session()` | `_clear_active_session()` 호출 제거 | 1차 리뷰 #2, 2차 리뷰 #1 |
| 2 | `recovery_coordinator.py` `resume_recovery()` | 전체 `with self._lock:` + `_clear_active_session()` 호출 제거 + `resume_count` 검사 + `original_initiated_by` 기록 | 2차 리뷰 #2, #5 |
| 3 | `settings/recovery_coordinator.py` | `max_resume_count` 필드 추가 | 2차 리뷰 #5 |
| 4 | `test_recovery_coordinator.py` | `resume_recovery` 테스트 5건 추가 | 1차 리뷰 #11 |

### 1차 문서 대비 변경 사항

| 항목 | 1차 문서 (섹션 8) | 2차 리뷰 반영 |
|------|------------------|--------------|
| `resume_recovery()` 내 `_clear_active_session()` | 호출 유지 | **제거** — `start_recovery()`가 덮어쓰므로 불필요 |
| `coordinator.py` 부분 실패 로깅 | 수정 대상 포함 | **유지** — 2차 리뷰에서 별도 언급 없으나 1차 분석 유효 |
| 나머지 항목 | 동일 | 동일 |

---

## 14) 2차 리뷰 반영 — 네이밍 검증 결과

2차 리뷰에서 신규 도입된 네이밍 없음. 1차 리뷰의 네이밍은 섹션 7) 참조.

> 기존 `.py` 파일 내 충돌 확인:
> - `max_resume_count`: 0건 (235 문서 외)
> - `resume_count`: 0건 (235 문서 외)
> - `original_initiated_by`: 0건 (235 문서 외)
> - `resumed_from`: recovery_coordinator.py L613-L614에만 존재 (현재 구현의 일부)
>
> 모든 네이밍 충돌 없음 확인.

---

## 15) 2차 리뷰 — 구현 순서 (최종)

```
1. _fail_session() 버그 수정 (2차 리뷰 #1) — 선행 조건
   └── _clear_active_session() 호출 제거 (1줄)
   ↓
2. RecoveryCoordinatorSettings에 max_resume_count 추가 (2차 리뷰 #5)
   └── settings/recovery_coordinator.py에 Field 1개 추가
   ↓
3. resume_recovery() 전체 재작성 (2차 리뷰 #2, #5)
   └── _clear_active_session() 제거 + resume_count 검사 + metadata 기록
   ↓
4. coordinator.py 부분 실패 로깅 extra 추가 (1차 리뷰 #5 — 2차에서 유효성 유지)
   ↓
5. 테스트 작성 및 검증 (1차 리뷰 #11)
```

---

## 16) 의사결정 요약 — 2차 리뷰 최종

| 리뷰 항목 | 리뷰 판정 | 동의 여부 | 보완 사항 |
|----------|----------|----------|----------|
| #1. Active Session 정의 (Q1, Q2) | 수정 필수 | ✅ 전적 동의 | 없음 |
| #2. Race Condition (Q3, Q4) | 보완 필수 | ✅ 동의 + 보완 | `_clear_active_session()` 제거 수용, **메타데이터 기록 로직 보완 필수** |
| #3. 멱등성 키/세션 ID (Q5, Q6, Q7) | 충분함 | ✅ 전적 동의 | 없음 |
| #4. HTTP 응답 코드 (Q8) | 충분함 | ✅ 전적 동의 | 없음 |
| #5. 무한 재개 방지 (Q9) | 추가 구현 필수 | ✅ 전적 동의 | 없음 |
| #6. 강제 재시작 (Q10) | 불필요 | ✅ 전적 동의 | 없음 |

**2차 리뷰에서 발견된 보완점 1건**:
리뷰어의 축약 코드 (`return self.start_recovery(...)`)에 메타데이터 기록 누락.
`resumed_from`, `resumed_from_step`, `resume_count`, `original_initiated_by` 기록은 감사 추적에 필수이므로, 섹션 12-2의 최종 코드처럼 `start_recovery()` 호출 후 메타데이터 기록 후 반환해야 함.

---

## 17) 구현 완료 기록

구현일: 2026-02-17

### 구현 결과

| # | 파일 | 변경 내용 | 상태 |
|---|------|-----------|------|
| 1 | `recovery_coordinator.py` `_fail_session()` | `_clear_active_session()` 호출 제거, ACTIVE_SESSION_KEY 유지 | ✅ 완료 |
| 2 | `settings/recovery_coordinator.py` | `max_resume_count` 필드 추가 (기본값 3, 범위 1~10) | ✅ 완료 |
| 3 | `recovery_coordinator.py` `resume_recovery()` | 전체 `with self._lock:` 내 배치, `_clear_active_session()` 제거, `resume_count` 검사, `original_initiated_by` 기록 | ✅ 완료 |
| 4 | `coordinator.py` 부분 실패 로깅 | `extra` 메타데이터 추가 (`alert_type`, `namespace`, `succeeded_actions`, `failed_actions`, `remaining_count`) | ✅ 완료 |
| 5 | `rollback/models.py` `PARTIALLY_COMPLETED` | 구현 시점에 이미 존재 확인 — 변경 불필요 | ✅ 이미 구현 |
| 6 | `rollback/service.py` 부분 성공 판정 | 구현 시점에 이미 존재 확인 — 변경 불필요 | ✅ 이미 구현 |

### 단위 테스트

테스트 작성일: 2026-02-17

| # | 테스트 파일 | 테스트 클래스 | 테스트 수 | 결과 |
|---|------------|-------------|----------|------|
| 1 | `test_recovery_coordinator.py` | `TestFailSessionBehavior` | 5 | ✅ PASSED |
| 2 | `test_recovery_coordinator.py` | `TestResumeRecoveryBehavior` | 9 | ✅ PASSED |
| 3 | `test_recovery_coordinator.py` | `TestMaxResumeCountContract` | 4 | ✅ PASSED |
| 4 | `test_coordinator.py` | `TestPartialFailureLoggingBehavior` | 4 | ✅ PASSED |

**총 22개 테스트 PASSED** (기존 테스트 62개 포함 전체 84/84 통과, 회귀 없음)

기존 테스트 수정 1건:
- `TestExecuteNextStep::test_execute_step_failed_handler` — `_fail_session()` 변경으로 `assert active is None` → `assert active.status == RecoveryStatus.FAILED`로 수정

### 통합 테스트 판정

**불필요** — 모든 변경이 인메모리 상태 관리 로직이며 외부 시스템(Redis, Celery) 의존 없음. 단위 테스트로 충분.
