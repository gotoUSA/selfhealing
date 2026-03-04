# 300. Canary Shadow Gate — Canary 연동 및 Governance Soft Gate

> **Status**: Proposed
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/canary/service.py` — `start_rollout()` 수정
> - `packages/selfhealing-python/src/selfhealing/services/governance/checks.py` — `BlockReason` 확장
> - `packages/selfhealing-python/src/selfhealing/services/canary/audit.py` — Shadow bypass Audit
> - `packages/selfhealing-python/src/selfhealing/settings/config_shadow.py` — Settings
> **References**:
> - [298_EVENT_JOURNAL.md](298_EVENT_JOURNAL.md) — EventJournal 저장소
> - [299_CONFIG_SHADOW_EVALUATOR.md](299_CONFIG_SHADOW_EVALUATOR.md) — 시뮬레이션 엔진
> - `services/canary/service.py` — Canary 롤아웃 서비스
> - `services/governance/checks.py` — 거버넌스 체크 체계

---

## 1. 목적

Config Shadow Evaluator(299)의 시뮬레이션 결과를 Canary 롤아웃 플로우에 통합한다.

- **Soft Gate**: 시뮬레이션 실패 시 경고 후 `start_rollout()` 차단, bypass 가능
- **독립 호출**: Canary 없이도 `evaluate_rollout()`로 직접 시뮬레이션 가능
- **Audit Trail**: 모든 bypass에 대해 감사 기록 필수

---

## 2. 기존 코드 근거

### 2.1 start_rollout() — 현재 플로우

`services/canary/service.py:272-340`:

```python
def start_rollout(
    self,
    rollout_id: str,
    force_during_chaos: bool = False,
) -> bool:
    rollout = self.get_rollout(rollout_id)
    if not rollout:
        return False

    # 상태 검증 — CREATED만 허용
    if rollout.state != CanaryState.CREATED:      # line 295
        return False

    # 카오스 충돌 검사
    chaos_result = self.chaos_guard.check_conflict(  # line 304
        target_clusters=first_stage.clusters,
        force_during_chaos=force_during_chaos,
    )
    if not chaos_result.can_proceed:               # line 309
        return False

    # 첫 번째 단계 적용
    self._apply_to_clusters(rollout, chaos_result.safe_clusters)  # line 317
    rollout.state = CanaryState.CANARY             # line 319
```

Shadow Gate는 **카오스 충돌 검사(line 304)와 클러스터 적용(line 317) 사이에** 삽입한다.

### 2.2 promote() — bypass 패턴 선례

`services/canary/service.py:342-411`:

```python
def promote(
    self,
    rollout_id: str,
    force: bool = False,
    bypass_governance: bool = False,
    bypass_reason: str = "",
    ...
):
    # 거버넌스 체크
    if not bypass_governance:                       # line 377
        governance = check_all_governance(...)
        if not governance.allowed:                  # line 391
            return False
    else:
        # bypass 시: reason 10자 이상 필수 + Audit 기록
        if not bypass_reason or len(bypass_reason) < 10:  # line 409
            return False
        log_canary_action(action="governance_bypass", ...)  # line 413
```

이 **bypass_governance + bypass_reason + Audit** 패턴을 Shadow Gate에 동일하게 적용한다.

### 2.3 BlockReason enum — 현재 정의

`services/governance/checks.py:165-181`:

```python
class BlockReason(str, Enum):
    KILL_SWITCH = "kill_switch"
    EMERGENCY_MODE = "emergency_mode"
    ERROR_BUDGET = "error_budget"
    RATE_LIMITED = "rate_limited"
    MANUALLY_BLOCKED = "manually_blocked"
```

### 2.4 GovernanceCheckResult — 팩토리 패턴

`services/governance/checks.py:184-254`:

```python
@dataclass
class GovernanceCheckResult:
    allowed: bool
    block_reason: BlockReason | None = None
    block_message: str = ""
    emergency_level: str = "UNKNOWN"
    error_budget_percent: float = 100.0
    threshold_percent: float = 0.0

    @classmethod
    def allowed_result(cls) -> GovernanceCheckResult: ...
    @classmethod
    def blocked_by_kill_switch(cls) -> GovernanceCheckResult: ...
    @classmethod
    def blocked_by_emergency(cls, level_name, message) -> GovernanceCheckResult: ...
    @classmethod
    def blocked_by_error_budget(cls, budget_percent, threshold_percent) -> GovernanceCheckResult: ...
```

### 2.5 check_all_governance() — 체크 순서

`services/governance/checks.py:445-539`:

```python
def check_all_governance(...) -> GovernanceCheckResult:
    # 0. Break Glass (활성화 시 모든 체크 우회)
    # 1. Kill Switch
    # 2. Emergency Level
    # 3. Error Budget
    # → 4. Shadow Evaluation (새로 추가)
```

현재 체크 순서는 0→1→2→3이다. Shadow Evaluation을 **4번째 체크**로 추가한다.

**설계 결정 — 4번 위치 선택 이유**:
Kill Switch/Emergency/Error Budget은 "시스템 전체 건강 상태" 체크이고,
Shadow Evaluation은 "이 특정 설정 변경의 안전성" 체크이다.
시스템이 건강하지 않으면 설정 변경 자체가 의미 없으므로 시스템 체크가 먼저다.

---

## 3. BlockReason 확장

### 3.1 enum 추가

```python
# services/governance/checks.py:165-182에 추가

class BlockReason(str, Enum):
    KILL_SWITCH = "kill_switch"
    EMERGENCY_MODE = "emergency_mode"
    ERROR_BUDGET = "error_budget"
    RATE_LIMITED = "rate_limited"
    MANUALLY_BLOCKED = "manually_blocked"

    SHADOW_EVALUATION_FAILED = "shadow_evaluation_failed"
    """Shadow Evaluation 시뮬레이션 결과 부적격."""
```

### 3.2 GovernanceCheckResult 팩토리 추가

기존 `blocked_by_*` 팩토리 패턴(`checks.py:207-243`)을 따른다:

```python
@classmethod
def blocked_by_shadow_evaluation(
    cls,
    evaluation_id: str,
    summary: str,
    confidence_score: float,
) -> GovernanceCheckResult:
    """Shadow Evaluation 실패로 차단된 결과."""
    return cls(
        allowed=False,
        block_reason=BlockReason.SHADOW_EVALUATION_FAILED,
        block_message=(
            f"Shadow evaluation failed (id={evaluation_id}, "
            f"confidence={confidence_score:.2f}): {summary}"
        ),
    )
```

---

## 4. start_rollout() 수정

### 4.1 시그니처 변경

기존 `promote()`의 bypass 파라미터 패턴(`service.py:342-349`)을 따른다:

```python
def start_rollout(
    self,
    rollout_id: str,
    force_during_chaos: bool = False,
    # 새로 추가
    bypass_shadow: bool = False,
    bypass_shadow_reason: str = "",
) -> bool:
```

### 4.2 Shadow Gate 삽입 위치

```python
def start_rollout(self, rollout_id, force_during_chaos=False,
                  bypass_shadow=False, bypass_shadow_reason="") -> bool:
    rollout = self.get_rollout(rollout_id)
    if not rollout:
        return False

    if rollout.state != CanaryState.CREATED:
        return False

    # === 기존: 카오스 충돌 검사 (line 302-314) ===
    first_stage = rollout.stages[0]
    chaos_result = self.chaos_guard.check_conflict(
        target_clusters=first_stage.clusters,
        force_during_chaos=force_during_chaos,
    )
    if not chaos_result.can_proceed:
        return False

    # === 새로 추가: Shadow Evaluation Gate ===
    shadow_check = self._check_shadow_evaluation(
        rollout=rollout,
        bypass_shadow=bypass_shadow,
        bypass_shadow_reason=bypass_shadow_reason,
    )
    if shadow_check is not None and not shadow_check:
        return False

    # === 기존: 첫 번째 단계 적용 (line 317-331) ===
    self._apply_to_clusters(rollout, chaos_result.safe_clusters)
    rollout.state = CanaryState.CANARY
    ...
```

### 4.3 _check_shadow_evaluation() 구현

```python
def _check_shadow_evaluation(
    self,
    rollout: CanaryRollout,
    bypass_shadow: bool,
    bypass_shadow_reason: str,
) -> bool | None:
    """
    Shadow Evaluation 결과를 확인한다.

    Returns:
        True: 통과 (시작 가능)
        False: 차단 (시작 불가)
        None: Shadow Evaluation 미실행 또는 비활성화 (체크 생략)
    """
    # 설정 확인
    try:
        from selfhealing.settings.config_shadow import get_config_shadow_settings
        settings = get_config_shadow_settings()
        if not settings.gate_enabled:
            return None
    except ImportError:
        return None

    # Shadow Evaluation 결과 조회
    try:
        from selfhealing.services.config_shadow import get_shadow_evaluator_service
        service = get_shadow_evaluator_service()
        evaluation = service.get_latest_for_rollout(rollout.rollout_id)
    except ImportError:
        logger.debug("canary_rollout.shadow_evaluator_not_available")
        return None
    except Exception as e:
        logger.warning("canary_rollout.shadow_check_error", error=e)
        # Fail-Open: Shadow Evaluation 오류 시 차단하지 않음
        return None

    # 평가가 없으면 생략 (아직 evaluate_rollout() 미호출)
    if evaluation is None:
        if settings.require_evaluation:
            logger.warning(
                "canary_rollout.shadow_evaluation_required_not_found",
                rollout_id=rollout.rollout_id,
            )
            return False
        return None

    # 평가가 통과했으면 허용
    if evaluation.report and evaluation.report.passed:
        logger.info(
            "canary_rollout.shadow_evaluation_passed",
            evaluation_id=evaluation.evaluation_id,
            confidence=evaluation.report.confidence_score,
        )
        return True

    # === 평가 실패: bypass 확인 ===
    if bypass_shadow:
        # promote()의 bypass_reason 패턴 (service.py:409-411)
        if not bypass_shadow_reason or len(bypass_shadow_reason) < 10:
            logger.error(
                "canary_rollout.shadow_bypass_reason_required",
                min_chars=10,
            )
            return False

        # Audit 기록
        log_canary_action(
            action="shadow_evaluation_bypass",
            rollout=rollout,
            safety_check_result={
                "evaluation_id": evaluation.evaluation_id,
                "bypass_reason": bypass_shadow_reason,
                "evaluation_summary": evaluation.report.summary if evaluation.report else "",
                "confidence_score": evaluation.report.confidence_score if evaluation.report else 0,
            },
        )

        logger.warning(
            "canary_rollout.shadow_evaluation_bypassed",
            rollout_id=rollout.rollout_id,
            evaluation_id=evaluation.evaluation_id,
            bypass_reason=bypass_shadow_reason,
        )
        return True

    # bypass 없이 실패 → 차단
    logger.warning(
        "canary_rollout.shadow_evaluation_failed_blocking",
        rollout_id=rollout.rollout_id,
        evaluation_id=evaluation.evaluation_id,
        summary=evaluation.report.summary if evaluation.report else "",
    )
    return False
```

**설계 결정 — Fail-Open 선택 이유**:
기존 `is_error_budget_blocking()` (`checks.py:441-442`)도 Fail-Open이다:
```python
# Fail-open: 에러 예산 확인 실패 시 허용
return False, 100.0, 0.0
```
Shadow Evaluation은 새로 도입되는 시스템이므로 Fail-Closed보다 Fail-Open이 안전하다.
Shadow Evaluator 자체의 버그로 긴급 설정 변경이 차단되면 더 큰 장애가 된다.

**설계 결정 — `require_evaluation` 설정 이유**:
초기에는 `require_evaluation=False`로 운영하다가 시스템이 안정화되면
`True`로 변경하여 Shadow Evaluation 없이는 `start_rollout()`을 실행할 수 없게 만든다.
이는 Soft Gate에서 Hard Gate로의 점진적 전환을 지원한다.

---

## 5. evaluate_rollout() — 독립 호출 메서드

Canary 서비스에 추가하는 것이 아니라, `ShadowEvaluatorService`에 편의 메서드를 둔다.

```python
# services/config_shadow/service.py에 추가

def evaluate_for_rollout(
    self,
    rollout_id: str,
    config_type: str,
    baseline_config: dict[str, Any],
    candidate_config: dict[str, Any],
    service_name: str = "",
    time_window_hours: int = 336,
) -> ShadowEvaluation:
    """
    Canary rollout에 연결된 Shadow Evaluation을 실행한다.

    evaluate()와 동일하되 rollout_id를 연결하고 결과를 캐시한다.
    이후 start_rollout()의 _check_shadow_evaluation()에서 조회된다.
    """
    evaluation = self.evaluate(
        config_type=config_type,
        baseline_config=baseline_config,
        candidate_config=candidate_config,
        service_name=service_name,
        time_window_hours=time_window_hours,
        rollout_id=rollout_id,
    )

    # 결과 캐시 (rollout_id로 조회 가능하도록)
    self._rollout_evaluations[rollout_id] = evaluation

    return evaluation


def get_latest_for_rollout(self, rollout_id: str) -> ShadowEvaluation | None:
    """rollout에 연결된 최신 Shadow Evaluation을 반환한다."""
    return self._rollout_evaluations.get(rollout_id)
```

**사용 플로우**:

```python
# 1. Canary rollout 생성
rollout = canary_service.create_rollout(config_type="circuit_breaker", ...)

# 2. Shadow Evaluation 실행 (여러 번 가능)
shadow = get_shadow_evaluator_service()
result1 = shadow.evaluate_for_rollout(
    rollout_id=rollout.rollout_id,
    config_type="circuit_breaker",
    baseline_config={"failure_threshold": 5},
    candidate_config={"failure_threshold": 10},
)

# 3. 결과 확인 후 start
if result1.report.passed:
    canary_service.start_rollout(rollout.rollout_id)
else:
    # bypass로 강제 시작
    canary_service.start_rollout(
        rollout.rollout_id,
        bypass_shadow=True,
        bypass_shadow_reason="긴급 설정 변경: CB false positive 빈발로 threshold 상향 필요",
    )
```

---

## 6. check_all_governance() 연동

### 6.1 4번째 체크 추가

`checks.py:445-539`의 체크 순서에 Shadow Evaluation을 추가한다.
단, `check_all_governance()`는 범용 함수이므로 **선택적 파라미터**로 추가한다:

```python
def check_all_governance(
    check_kill_switch: bool = True,
    check_emergency: bool = True,
    emergency_min_level: int | None = None,
    check_error_budget: bool = True,
    # 새로 추가
    check_shadow_evaluation: bool = False,
    shadow_rollout_id: str | None = None,
    ...
) -> GovernanceCheckResult:
    ...

    # 0. Break Glass
    # 1. Kill Switch
    # 2. Emergency Mode
    # 3. Error Budget

    # 4. Shadow Evaluation (선택적)
    if check_shadow_evaluation and shadow_rollout_id:
        try:
            from selfhealing.services.config_shadow import get_shadow_evaluator_service
            service = get_shadow_evaluator_service()
            evaluation = service.get_latest_for_rollout(shadow_rollout_id)

            if evaluation and evaluation.report and not evaluation.report.passed:
                if audit_on_block:
                    _log_governance_blocked(
                        block_reason="shadow_evaluation_failed",
                        operation_name=operation_name,
                        details={
                            "evaluation_id": evaluation.evaluation_id,
                            "summary": evaluation.report.summary,
                            "confidence_score": evaluation.report.confidence_score,
                        },
                        service_name=service_name,
                        domain=domain,
                    )
                return GovernanceCheckResult.blocked_by_shadow_evaluation(
                    evaluation_id=evaluation.evaluation_id,
                    summary=evaluation.report.summary,
                    confidence_score=evaluation.report.confidence_score,
                )
        except ImportError:
            pass  # Shadow Evaluator 미설치 시 건너뜀
        except Exception as e:
            logger.warning("governance_checks.shadow_check_error", error=e)
            # Fail-Open

    return GovernanceCheckResult.allowed_result()
```

**설계 결정 — `check_shadow_evaluation=False` 기본값 이유**:
기존 `check_all_governance()` 호출부(ReplayService, CB ManualControl 등)는
Shadow Evaluation과 무관하다. 기본값 `False`로 두어 기존 코드에 영향 없이 도입한다.
Canary 서비스만 `check_shadow_evaluation=True`를 명시적으로 전달한다.

---

## 7. Settings

### 7.1 ConfigShadowSettings

기존 `settings/canary_governance.py:16-88` 패턴을 따른다:

```python
# settings/config_shadow.py

class ConfigShadowSettings(BaseSettings):
    """
    Config Shadow Evaluation 설정.

    Environment variables:
        SELFHEALING_SHADOW_GATE_ENABLED=true
        SELFHEALING_SHADOW_REQUIRE_EVALUATION=false
        SELFHEALING_SHADOW_DEFAULT_TIME_WINDOW_HOURS=336
        SELFHEALING_SHADOW_MIN_CONFIDENCE=0.3
        SELFHEALING_SHADOW_BYPASS_MIN_REASON_LENGTH=10
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SHADOW_",
        env_file=".env",
        extra="ignore",
    )

    gate_enabled: bool = Field(
        default=True,
        description="Shadow Gate 활성화 여부",
    )
    require_evaluation: bool = Field(
        default=False,
        description=(
            "True: Shadow Evaluation 없이 start_rollout() 불가. "
            "False: 평가 없으면 경고만 (기본값)"
        ),
    )
    default_time_window_hours: int = Field(
        default=336,
        ge=24,
        le=720,
        description="기본 분석 시간 범위 (336 = 14일)",
    )
    min_confidence: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="이 미만의 confidence에서는 경고 추가",
    )
    bypass_min_reason_length: int = Field(
        default=10,
        ge=5,
        le=500,
        description="bypass_shadow_reason 최소 길이",
    )
```

**설계 결정 — `require_evaluation=False` 기본값 이유**:
도입 초기에는 운영자가 Shadow Evaluation 워크플로우에 익숙하지 않다.
`False`로 시작하여 평가 없이도 `start_rollout()`이 가능하게 하고,
시스템이 안정화된 후 `True`로 전환하여 필수화한다.

---

## 8. Audit 로그

### 8.1 Shadow bypass Audit

기존 `log_canary_action()` (`services/canary/audit.py:54-129`)을 활용한다:

```python
# action="shadow_evaluation_bypass" 전달 시 기록되는 데이터

{
    "action": "shadow_evaluation_bypass",
    "rollout_id": "abc12345",
    "config_type": "circuit_breaker",
    "evaluation_id": "xyz67890",
    "bypass_reason": "긴급 설정 변경: CB false positive 빈발로 threshold 상향 필요",
    "evaluation_summary": "CB 개방 62.5% 감소, 단 MTTR 12분 증가 위험",
    "confidence_score": 0.85,
    "timestamp": "2026-03-05T12:00:00Z",
}
```

별도 Auditor 클래스를 만들지 않고 기존 `log_canary_action()`을 재활용한다.
`InterlockBypassAuditor` (`services/canary/bypass_audit.py:141-261`)처럼 별도 클래스를 만들 필요는
현 단계에서 없다. bypass 빈도가 높아지면 그때 분리한다.

---

## 9. 전체 플로우 요약

```
운영자                    Canary Service              Shadow Evaluator
  │                           │                            │
  ├─ create_rollout() ───────▶│                            │
  │◀── rollout_id ────────────│                            │
  │                           │                            │
  ├─ evaluate_for_rollout() ──┼───────────────────────────▶│
  │   (config_type,           │                            ├─ EventJournal 조회
  │    baseline, candidate)   │                            ├─ CB/ErrorBudget 시뮬레이션
  │◀── EvaluationReport ──────┼────────────────────────────│
  │                           │                            │
  │   [What-if 반복 가능]     │                            │
  │                           │                            │
  ├─ start_rollout() ────────▶│                            │
  │                           ├─ 상태 검증 (CREATED)       │
  │                           ├─ 카오스 충돌 검사          │
  │                           ├─ _check_shadow_evaluation()│
  │                           │   └─ get_latest_for_rollout()
  │                           │      ├─ passed → 진행      │
  │                           │      ├─ failed + bypass → Audit + 진행
  │                           │      └─ failed → 차단      │
  │                           ├─ 클러스터 적용             │
  │◀── 결과 ──────────────────│                            │
```

---

## 10. 파일 변경 요약

| 파일 | 변경 유형 | 내용 |
|------|-----------|------|
| `services/governance/checks.py` | 수정 | `BlockReason.SHADOW_EVALUATION_FAILED` 추가, `blocked_by_shadow_evaluation()` 팩토리, `check_all_governance()` 4번째 체크 |
| `services/canary/service.py` | 수정 | `start_rollout()` 시그니처에 `bypass_shadow` 파라미터 추가, `_check_shadow_evaluation()` 메서드 추가 |
| `services/config_shadow/service.py` | 수정 | `evaluate_for_rollout()`, `get_latest_for_rollout()` 메서드 추가 |
| `settings/config_shadow.py` | 신규 | `ConfigShadowSettings` |

기존 `start_rollout()` 호출부는 새 파라미터가 모두 기본값(`bypass_shadow=False`)이므로
**호출부 변경 불필요**.
