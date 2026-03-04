# 297. Executor Enhancements — continue_on_failure, poll_interval, labels Fail-Fast

> **Status**: Implemented
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/runbook/runbook_registry.py` — RunbookStep 모델
> - `packages/selfhealing-python/src/selfhealing/services/runbook/executor.py` — Executor 루프
> - `packages/selfhealing-python/src/selfhealing/services/runbook/primitives.py` — wait.stabilize, assert.metric
> **References**:
> - [275_RUNBOOK_EXECUTOR.md](275_RUNBOOK_EXECUTOR.md) — Step 순차 실행 + 보상 (기반 설계)
> - [273_RUNBOOK_PATTERN_MATCHER.md](273_RUNBOOK_PATTERN_MATCHER.md) — LabelFilter, MetricCondition.labels
> - [296_BUILTIN_RUNBOOKS.md](296_BUILTIN_RUNBOOKS.md) — 빌트인 런북 (본 문서 의존)

---

## 1. 목적

296 빌트인 런북 설계 리뷰에서 발견된 3가지 Executor/Primitive 레벨 개선사항을 구현한다.

| 이슈 | 심각도 | 요약 |
|------|--------|------|
| A. `continue_on_failure` | Critical | assert.metric 실패 시 즉시 abort → prev_failed 조건 Step 미도달 |
| B. `poll_interval_seconds` | Medium | wait.stabilize가 전체 시간 blocking sleep → 장애 방치 |
| C. labels Fail-Fast | Medium | `${trigger.source}` 미치환 시 전역 집계 쿼리 → TSDB 부하/오탐 |

---

## 2. continue_on_failure — 검증 게이트 Step의 비중단 실패

### 2.1 문제

275 Executor의 `_run_from_step()` (`executor.py:361-376`)는 Step 실패 시 즉시 보상 후 종료한다:

```python
if not step_result.success and not step_result.idempotent:
    ctx.abort_reason = f"Step '{step.name}' 실패: {step_result.error}"
    comp_summary = self._compensate_steps(ctx, runbook)
    ctx.status = RunbookExecutionStatus.FAILED
    return ctx  # 즉시 종료
```

296 `circuit_breaker_opened_response`의 Step 3(`assert.metric`)이 에러율 30% 초과로 실패하면,
Step 4(`emergency.activate`, `condition=prev_failed`)는 **영원히 실행되지 않는다.**

동일 문제가 `error_budget_critical_response`의 Step 4 → Step 5에도 존재한다.

### 2.2 해결: RunbookStep.continue_on_failure 필드

```python
@dataclass
class RunbookStep:
    ...
    continue_on_failure: bool = False
```

- `False` (기본값) — 기존 동작 유지. 실패 시 즉시 abort + 보상
- `True` — 실패해도 abort하지 않고 결과만 기록한 뒤 **다음 Step으로 진행**

### 2.3 Executor 수정: _run_from_step() 루프

```python
# executor.py — _run_from_step() 내부
if not step_result.success and not step_result.idempotent:
    if getattr(step, "continue_on_failure", False):
        # 실패 기록만 하고 다음 Step으로 진행
        logger.info(
            "runbook_executor.step_failed_continue",
            runbook_id=runbook.id,
            step=step.name,
            error=step_result.error,
        )
        ctx.step_results[step.name] = step_result
        continue  # abort 하지 않음 — 다음 Step의 condition에서 prev_failed 평가 가능
    else:
        # 기존 동작: 즉시 abort + 보상
        ctx.abort_reason = f"Step '{step.name}' 실패: {step_result.error}"
        comp_summary = self._compensate_steps(ctx, runbook)
        ctx.status = RunbookExecutionStatus.FAILED
        return ctx
```

### 2.4 보상 범위 제한

`continue_on_failure=True`인 Step이 실패해도 **해당 Step은 보상 대상에서 제외**한다.
이유: 검증 게이트(`assert.metric`)는 부작용이 없으므로 보상할 상태 변경이 없다.

만약 후속 Step(예: `emergency.activate`)에서 실패하여 abort이 발생하면,
그 시점부터 역순 보상이 진행되며 `continue_on_failure=True`로 실패한 Step은 건너뛴다.

```python
# _compensate_steps() 내부
for step_result in reversed(executed_steps):
    if not step_result.success and step_result.step.continue_on_failure:
        continue  # 검증 게이트 실패 — 보상 불필요
    # ... 기존 보상 로직
```

### 2.5 사용 제약

`continue_on_failure`는 **부작용 없는 검증/확인 Step에만** 사용해야 한다:

| 허용 | 금지 |
|------|------|
| `assert.metric` (읽기 전용 검증) | `config.set` (상태 변경) |
| `notify.send` (알림 — 무시 가능) | `recovery.start` (복구 시작) |
| | `emergency.activate` (에스컬레이션) |

상태 변경 Step에 `continue_on_failure=True`를 설정하면 **실패한 상태 변경이 보상 없이 방치**되므로
`Runbook.validate()`에서 경고를 발생시킨다:

```python
# runbook_registry.py — Runbook.validate() 확장
STATE_CHANGING_ACTIONS = {"config.set", "recovery.start", "emergency.activate", "emergency.deactivate"}

for step in self.steps:
    if step.continue_on_failure and step.action in STATE_CHANGING_ACTIONS:
        warnings.append(
            f"Step '{step.name}': continue_on_failure=True on state-changing action '{step.action}' "
            f"— failed state changes will not be compensated"
        )
```

### 2.6 영향받는 런북 (296)

| 런북 | Step | 변경 |
|------|------|------|
| `circuit_breaker_opened_response` | Step 3 (`error_rate_check`) | `continue_on_failure=True` 추가 |
| `error_budget_critical_response` | Step 4 (`error_rate_check`) | `continue_on_failure=True` 추가 |

Emergency Recovery 런북(LEVEL_1/2/3)의 `health_check_gate`는 `continue_on_failure`를 사용하지 않는다.
이유: 에러율이 임계값을 초과하면 Canary Resume/Governance 전환을 진행해서는 안 되므로 abort가 올바른 동작이다.

---

## 3. poll_interval_seconds — wait.stabilize 조기 종료

### 3.1 문제

현재 `_handle_wait_stabilize` (`primitives.py:379-419`)는 전체 대기 시간을 `time.sleep()`으로 통째 차단한다:

```python
time.sleep(params.seconds)  # 300초 blocking — 도중 장애 확인 불가

if params.assert_metric and params.threshold is not None:
    metric_value = _query_metric(params.assert_metric)  # 사후 검증
```

LEVEL_3 복구의 300초 대기 중 10초 만에 에러율이 100%로 치솟아도 290초를 추가로 대기한다.

### 3.2 해결: WaitStabilizeParams.poll_interval_seconds

```python
class WaitStabilizeParams(BaseModel):
    seconds: int = Field(ge=1, le=3600)
    assert_metric: str | None = None
    threshold: float | None = None
    labels: dict[str, str] | None = None          # 메트릭 라벨 필터 (§4 연계)
    poll_interval_seconds: int = Field(default=0, ge=0, le=300)
```

- `poll_interval_seconds=0` (기본값) — 기존 동작 유지 (전체 sleep)
- `poll_interval_seconds > 0` — 해당 간격마다 메트릭을 확인하고 임계값 초과 시 즉시 Fail-Fast

### 3.3 구현

```python
def _handle_wait_stabilize(ctx: RunbookStepContext) -> StepResult:
    params = WaitStabilizeParams(**ctx.params)

    if params.poll_interval_seconds > 0 and params.assert_metric and params.threshold is not None:
        # Polling 모드: 주기적 메트릭 확인 + 조기 종료
        elapsed = 0
        while elapsed < params.seconds:
            sleep_chunk = min(params.poll_interval_seconds, params.seconds - elapsed)
            time.sleep(sleep_chunk)
            elapsed += sleep_chunk

            metric_value = _query_metric(params.assert_metric, labels=params.labels)
            if metric_value is not None and metric_value >= params.threshold:
                return StepResult.failed(
                    error=f"Fail-fast: {params.assert_metric}={metric_value:.3f} "
                          f">= threshold {params.threshold} at {elapsed}s/{params.seconds}s",
                    result_data={
                        "waited_seconds": elapsed,
                        "total_seconds": params.seconds,
                        "fail_fast": True,
                        "metric_value": metric_value,
                    },
                )

        # 전체 대기 완료 — 최종 검증
        metric_value = _query_metric(params.assert_metric, labels=params.labels)
        if metric_value is not None and metric_value >= params.threshold:
            return StepResult.failed(
                error=f"Stabilization failed: {params.assert_metric}={metric_value:.3f} >= {params.threshold}",
            )
    else:
        # 기존 동작: 전체 sleep + 사후 검증
        time.sleep(params.seconds)
        if params.assert_metric and params.threshold is not None:
            metric_value = _query_metric(params.assert_metric, labels=params.labels)
            if metric_value is None:
                return StepResult.failed(error=f"Metric '{params.assert_metric}' not available")
            if metric_value >= params.threshold:
                return StepResult.failed(
                    error=f"{params.assert_metric}={metric_value:.3f} >= {params.threshold}",
                )

    return StepResult.succeeded({"waited_seconds": params.seconds})
```

### 3.4 빌트인 런북 적용 (296)

| 런북 | Step | 현재 seconds | poll_interval_seconds 권장값 |
|------|------|-------------|---------------------------|
| `emergency_recovery_level3` | `stabilization_wait` | 300 | 30 |
| `emergency_recovery_level2` | `stabilization_wait` | 180 | 20 |
| `emergency_recovery_level1` | `stabilization_wait` | 120 | 15 |
| `circuit_breaker_opened_response` | `stabilization_wait` | 60 | 10 |
| `error_budget_critical_response` | `stabilization_wait` | 120 | 15 |

이 값들은 296 문서의 다음 개정 시 `wait.stabilize` params에 추가한다.
본 문서에서는 프리미티브 레벨 구현만 정의하며, 런북 정의의 파라미터 추가는 296의 범위이다.

### 3.5 Distributed Lock 호환성

Polling 모드에서 총 대기 시간이 길어질 수 있으므로(최대 3600초),
Executor의 `_execute_with_timeout`에서 수행하는 분산 락 TTL 갱신(heartbeat)은
`poll_interval_seconds`와 무관하게 기존 주기(5분)를 유지한다.
Polling은 프리미티브 내부의 메트릭 확인일 뿐, 락 관리는 Executor 레벨이다.

---

## 4. labels Fail-Fast — 미치환 변수 방어

### 4.1 문제

296 `circuit_breaker_opened_response`의 assert.metric Step은
`labels: {"service": "${trigger.source}"}`를 사용한다.

`${trigger.source}`가 미치환(None 또는 빈 문자열)된 채 Prometheus 쿼리로 전달되면:
- 빈 `service=""` 라벨 → 매칭되는 시계열 없음 → metric_value=None → 항상 실패
- 또는 TSDB 구현에 따라 라벨 필터 무시 → **전역 집계 쿼리** → TSDB 부하 + 오탐

### 4.2 해결: assert.metric 핸들러의 필수 라벨 검증

```python
def _handle_assert_metric(ctx: RunbookStepContext) -> StepResult:
    params = AssertMetricParams(**ctx.params)

    # 필수 라벨 Fail-Fast 검증
    if params.labels:
        for label_key, label_value in params.labels.items():
            if not label_value or label_value.startswith("${"):
                return StepResult.failed(
                    error=f"Required label '{label_key}' not resolved: '{label_value}'. "
                          f"Variable interpolation failed — aborting to prevent unscoped metric query.",
                    retryable=False,
                )

    metric_value = _query_metric(params.metric_name, labels=params.labels)
    # ... 기존 비교 로직
```

### 4.3 wait.stabilize 동일 적용

`wait.stabilize`도 `labels` 파라미터를 지원하므로(`§3.2`) 동일한 Fail-Fast 검증을 적용한다:

```python
def _handle_wait_stabilize(ctx: RunbookStepContext) -> StepResult:
    params = WaitStabilizeParams(**ctx.params)

    # 필수 라벨 Fail-Fast 검증
    if params.labels:
        for label_key, label_value in params.labels.items():
            if not label_value or label_value.startswith("${"):
                return StepResult.failed(
                    error=f"Required label '{label_key}' not resolved: '{label_value}'.",
                    retryable=False,
                )

    # ... 기존/polling 대기 로직
```

### 4.4 Pydantic 스키마 확장

```python
class AssertMetricParams(BaseModel):
    metric_name: str
    operator: Literal["lt", "lte", "gt", "gte", "eq", "neq"]
    threshold: float
    labels: dict[str, str] | None = None  # 메트릭 라벨 필터 (273 LabelFilter 연계)
```

### 4.5 _query_metric 시그니처 확장

```python
def _query_metric(
    metric_name: str,
    labels: dict[str, str] | None = None,
) -> float | None:
    """메트릭 조회. labels가 있으면 Per-Service 스코프, 없으면 Global."""
    try:
        from selfhealing.factory import ProviderRegistry
        metrics_provider = ProviderRegistry.get("metrics_provider")
        return metrics_provider.get_metric(metric_name, labels=labels)
    except Exception:
        return None
```

이는 273에서 정의한 `RunbookMetricsProvider.get_metric(metric_name, labels)` 프로토콜과 일치한다.

---

## 5. 모듈 변경 요약

| 파일 | 변경 | 섹션 |
|------|------|------|
| `runbook_registry.py` | `RunbookStep.continue_on_failure: bool = False` 필드 추가 | §2.2 |
| `runbook_registry.py` | `Runbook.validate()` — 상태 변경 action + continue_on_failure 경고 | §2.5 |
| `executor.py` | `_run_from_step()` — continue_on_failure 분기 추가 | §2.3 |
| `executor.py` | `_compensate_steps()` — continue_on_failure 실패 Step 건너뛰기 | §2.4 |
| `primitives.py` | `WaitStabilizeParams` — `poll_interval_seconds`, `labels` 추가 | §3.2 |
| `primitives.py` | `_handle_wait_stabilize()` — polling 모드 구현 | §3.3 |
| `primitives.py` | `AssertMetricParams` — `labels` 추가 | §4.4 |
| `primitives.py` | `_handle_assert_metric()` — labels Fail-Fast 검증 | §4.2 |
| `primitives.py` | `_handle_wait_stabilize()` — labels Fail-Fast 검증 | §4.3 |
| `primitives.py` | `_query_metric()` — `labels` 파라미터 추가 | §4.5 |

---

## 6. 테스트 전략

테스트 위치: `packages/selfhealing-python/tests/unit/test_executor_enhancements.py`

### 6.1 continue_on_failure 테스트

```python
def test_continue_on_failure_step_does_not_abort():
    """continue_on_failure=True인 Step이 실패해도 다음 Step이 실행되는지 확인."""
    # Step 1: 성공, Step 2: 실패(continue_on_failure=True), Step 3: prev_failed 조건
    # → Step 3이 실행되어야 함

def test_continue_on_failure_false_aborts_on_failure():
    """continue_on_failure=False(기본값)인 Step이 실패하면 즉시 abort하는지 확인."""

def test_continue_on_failure_step_excluded_from_compensation():
    """continue_on_failure=True로 실패한 Step이 보상 대상에서 제외되는지 확인."""

def test_validate_warns_state_changing_action_with_continue_on_failure():
    """상태 변경 action에 continue_on_failure=True 설정 시 validate 경고."""
```

### 6.2 poll_interval 테스트

```python
def test_wait_stabilize_poll_interval_fail_fast():
    """poll_interval_seconds > 0일 때 임계값 초과 시 즉시 실패하는지 확인."""
    # 300초 대기 중 30초 시점에 에러율 100% → 즉시 실패 반환

def test_wait_stabilize_poll_interval_zero_full_sleep():
    """poll_interval_seconds=0이면 기존 전체 sleep 동작 유지."""

def test_wait_stabilize_poll_interval_completes_full_duration():
    """전체 대기 동안 임계값 미초과 시 정상 완료."""
```

### 6.3 labels Fail-Fast 테스트

```python
def test_assert_metric_labels_unresolved_variable_fails():
    """labels에 ${...} 미치환 변수가 있으면 즉시 실패."""
    params = {"metric_name": "error_rate", "operator": "lte", "threshold": 0.3,
              "labels": {"service": "${trigger.source}"}}
    result = _handle_assert_metric(ctx_with(params))
    assert not result.success
    assert "not resolved" in result.error

def test_assert_metric_labels_empty_string_fails():
    """labels 값이 빈 문자열이면 즉시 실패."""

def test_assert_metric_labels_valid_passes_to_query():
    """labels가 정상 치환되면 _query_metric에 labels 전달."""

def test_wait_stabilize_labels_unresolved_fails():
    """wait.stabilize에서도 미치환 labels Fail-Fast 동작 확인."""
```
