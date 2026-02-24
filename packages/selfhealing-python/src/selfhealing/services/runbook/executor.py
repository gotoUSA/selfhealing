"""
Runbook Executor — Runbook Step 순차 실행 + 역순 보상 엔진.

기존 패턴 결합:
- SagaStep.execute() + compensate() 흐름 (services/saga/step.py)
- IdempotentStepHandler 멱등성 보장 (services/coordination/idempotent_step_handlers.py)
- ActionExecutor ExecutionMode 준수 (core/action_executor.py)
- DistributedRecoveryLock 동시 실행 방지 (services/coordination/distributed_recovery_lock.py)
- _fail_session() 보상 → FAILED → DLQ 패턴 (_session_persistence.py)
- SagaOrchestrator._execute_with_timeout() ThreadPoolExecutor + Heartbeat Polling

Reference:
    docs/self_healing/middleware_system/275_RUNBOOK_EXECUTOR.md
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from selfhealing.services.runbook.exceptions import (
    RunbookExecutionError,
    RunbookLockConflictError,
    RunbookStaleContextError,
    RunbookStepTimeoutError,
    RunbookVersionMismatchError,
)
from selfhealing.services.runbook.execution_models import (
    CompensationSummary,
    RunbookExecutionContext,
    RunbookExecutionStatus,
    RunbookStepResult,
)
from selfhealing.services.runbook.resolvers import DotPathResolver, resolve_params

if TYPE_CHECKING:
    from selfhealing.core.action_executor import ActionExecutor, ActionResult
    from selfhealing.core.state_backend import StateBackend
    from selfhealing.services.coordination.distributed_recovery_lock import (
        DistributedRecoveryLock,
    )
    from selfhealing.services.coordination.idempotent_step_handlers import (
        IdempotencyRecord,
    )
    from selfhealing.services.runbook.runbook_registry import (
        ActionPrimitiveRegistry,
        Runbook,
        RunbookStep,
    )

logger = structlog.get_logger()

# =============================================================================
# 기본 상수 — RunbookSettings 폴백값
# RunbookSettings(settings/runbook.py)가 동일한 기본값을 가진다.
# 런타임에서는 _get_settings()를 통해 환경 변수 오버라이드 가능.
# 이 상수들은 설계 계약 기본값이자, Settings 로드 실패 시 폴백으로 사용된다.
# =============================================================================

LOCK_HEARTBEAT_INTERVAL = 60
"""Lock Heartbeat Polling 간격 (초). SagaOrchestrator.HEARTBEAT_INTERVAL과 동일."""

LOCK_EXTEND_SECONDS = 300
"""Lock TTL 연장 기본값 (초). SagaOrchestrator.EXTEND_SECONDS와 동일."""

MAX_RESUME_COUNT = 10
"""무한 재개 방지 카운터. SagaOrchestrator.MAX_RESUME_COUNT와 동일."""

CONTEXT_TTL_SECONDS = 86400
"""컨텍스트 영속화 TTL (초). 기본 24시간."""

CONTEXT_KEY_TEMPLATE = "selfhealing:runbook:execution:{execution_id}"
IDEMPOTENCY_KEY_PREFIX = "idem:runbook"


# =============================================================================
# RunbookExecutor
# =============================================================================


class RunbookExecutor:
    """Runbook Step들을 순차 실행하고, 실패 시 역순 보상을 수행하는 실행 엔진.

    기존 패턴 결합:
    - SagaStep.execute() + compensate() 흐름
    - IdempotentStepHandler 멱등성 보장
    - ActionExecutor ExecutionMode 준수
    - DistributedRecoveryLock 동시 실행 방지
    - _fail_session() 보상 → FAILED → DLQ 패턴
    """

    def __init__(
        self,
        action_executor: ActionExecutor | None = None,
        recovery_lock: DistributedRecoveryLock | None = None,
        state_backend: StateBackend | None = None,
        primitive_registry: ActionPrimitiveRegistry | None = None,
    ) -> None:
        """의존성 주입. None이면 모듈-레벨 팩토리 함수로 지연 로드."""
        self._action_executor = action_executor
        self._recovery_lock = recovery_lock
        self._backend = state_backend
        self._primitive_registry = primitive_registry

    # =========================================================================
    # 지연 로드 — SagaOrchestrator 패턴
    # =========================================================================

    def _get_action_executor(self) -> ActionExecutor:
        if self._action_executor is None:
            from selfhealing.core.action_executor import ActionExecutor

            self._action_executor = ActionExecutor()
        return self._action_executor

    def _get_recovery_lock(self) -> DistributedRecoveryLock:
        if self._recovery_lock is None:
            from selfhealing.services.coordination.distributed_recovery_lock import (
                get_distributed_recovery_lock,
            )

            self._recovery_lock = get_distributed_recovery_lock()
        return self._recovery_lock

    def _get_backend(self) -> StateBackend | None:
        if self._backend is None:
            try:
                from selfhealing.core.state_backend import get_state_backend

                self._backend = get_state_backend()
            except Exception:
                pass
        return self._backend

    def _get_primitive_registry(self) -> ActionPrimitiveRegistry | None:
        return self._primitive_registry

    def _get_settings(self):
        from selfhealing.settings.runbook import get_runbook_settings

        return get_runbook_settings()

    # =========================================================================
    # Settings 기반 값 조회 — 모듈 상수를 폴백으로 사용
    # =========================================================================

    def _get_lock_heartbeat_interval(self) -> int:
        """Lock Heartbeat Polling 간격 (초). Settings 우선, 폴백 LOCK_HEARTBEAT_INTERVAL."""
        try:
            return self._get_settings().lock_heartbeat_interval
        except Exception:
            return LOCK_HEARTBEAT_INTERVAL

    def _get_lock_extend_seconds(self) -> int:
        """Lock TTL 연장값 (초). Settings 우선, 폴백 LOCK_EXTEND_SECONDS."""
        try:
            return self._get_settings().lock_extend_seconds
        except Exception:
            return LOCK_EXTEND_SECONDS

    def _get_max_resume_count(self) -> int:
        """무한 재개 방지 카운터. Settings 우선, 폴백 MAX_RESUME_COUNT."""
        try:
            return self._get_settings().max_resume_count
        except Exception:
            return MAX_RESUME_COUNT

    def _get_context_ttl_seconds(self) -> int:
        """컨텍스트 영속화 TTL (초). Settings 우선, 폴백 CONTEXT_TTL_SECONDS."""
        try:
            return self._get_settings().context_ttl_seconds
        except Exception:
            return CONTEXT_TTL_SECONDS

    # =========================================================================
    # Public API
    # =========================================================================

    def execute_runbook(
        self,
        runbook: Runbook,
        trigger_event: dict[str, Any],
        namespace: str,
    ) -> RunbookExecutionContext:
        """런북 실행. Lock 획득 → 순차 Step 실행 → (실패 시) 역순 보상.

        Args:
            runbook: 실행할 Runbook 정의 스냅샷
            trigger_event: 런북을 트리거한 이벤트 데이터
            namespace: 대상 네임스페이스

        Returns:
            실행 완료된 RunbookExecutionContext

        Raises:
            RunbookLockConflictError: 같은 namespace의 다른 복구/런북이 Lock을 보유 중
        """
        execution_id = f"runbook-{uuid4()}"
        lock = self._get_recovery_lock()

        acquired = lock.acquire(namespace, execution_id)
        if not acquired:
            raise RunbookLockConflictError(
                f"Namespace '{namespace}'은 다른 복구/런북 실행이 Lock을 보유 중이다. " f"execution_id={execution_id}"
            )

        try:
            return self._run(runbook, trigger_event, namespace, execution_id)
        finally:
            lock.release(namespace, execution_id)

    def resume_execution(
        self,
        execution_id: str,
        force: bool = False,
    ) -> RunbookExecutionContext:
        """중단된 실행 재개. 3단계 방어 적용.

        1단계: Stale Context 거부 - 설정 threshold 초과 시
        2단계: Runbook 버전 호환성 검증
        3단계: 무한 재개 방지 - MAX_RESUME_COUNT 초과 시

        Args:
            execution_id: 재개할 실행 ID
            force: True이면 stale 체크를 건너뜀

        Raises:
            RunbookExecutionError: 컨텍스트를 찾지 못한 경우 또는 재개 불가 상태
            RunbookStaleContextError: stale 임계값 초과 (force=False인 경우)
            RunbookVersionMismatchError: Runbook 버전 불일치
        """
        ctx = self._load_context(execution_id)
        if ctx is None:
            raise RunbookExecutionError(f"실행을 찾을 수 없다: {execution_id}")

        resumable = {RunbookExecutionStatus.FAILED, RunbookExecutionStatus.EXECUTING}
        if ctx.status not in resumable:
            raise RunbookExecutionError(f"상태 '{ctx.status.value}'인 실행은 재개할 수 없다.")

        # 1. Stale Context 방어 (scan_orphan_sagas STALE_THRESHOLD_SECONDS 패턴)
        if not force and ctx.started_at:
            try:
                settings = self._get_settings()
                stale_threshold = getattr(settings, "resume_stale_threshold_seconds", 3600)
                started = datetime.fromisoformat(ctx.started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                if elapsed > stale_threshold:
                    raise RunbookStaleContextError(
                        f"컨텍스트가 {elapsed / 3600:.1f}시간 경과했다 "
                        f"(임계값: {stale_threshold / 3600:.1f}시간). "
                        f"force=True로 재시도하거나 새 실행을 생성하라."
                    )
            except (ValueError, TypeError):
                pass

        # 2. Runbook 버전 호환성 검증 (_validate_version_compatibility 패턴)
        from selfhealing.services.runbook.runbook_registry import RunbookRegistry

        registry = RunbookRegistry()
        runbook = registry.get(ctx.runbook_id)
        if runbook is None:
            raise RunbookExecutionError(f"Runbook '{ctx.runbook_id}'을 레지스트리에서 찾을 수 없다.")
        if ctx.runbook_version != runbook.version:
            raise RunbookVersionMismatchError(
                f"Runbook 버전 불일치: execution={ctx.runbook_version}, "
                f"current={runbook.version}. "
                f"새 실행을 생성하거나 Runbook을 이전 버전으로 롤백하라."
            )

        # 3. 무한 재개 방지 (SagaOrchestrator.resume_saga MAX_RESUME_COUNT 패턴)
        max_resume = self._get_max_resume_count()
        resume_count = ctx.variables.get("__resume_count", 0)
        if resume_count >= max_resume:
            ctx.status = RunbookExecutionStatus.FAILED
            ctx.abort_reason = f"최대 재개 횟수({max_resume})를 초과했다."
            self._save_context(ctx)
            self._store_to_dlq(ctx, ctx.abort_reason, CompensationSummary())
            return ctx
        ctx.variables["__resume_count"] = resume_count + 1

        # 4. 분산 Lock 획득 (SagaOrchestrator.resume_saga GC Pause 방어 패턴)
        # resume_saga()는 실행 재개 전 반드시 Lock을 획득하여
        # 다른 워커가 동시에 같은 Execution을 재개하는 Split-brain을 방지한다.
        lock = self._get_recovery_lock()
        acquired = lock.acquire(ctx.namespace, ctx.execution_id)
        if not acquired:
            raise RunbookLockConflictError(
                f"Namespace '{ctx.namespace}'은 다른 복구/런북 실행이 Lock을 보유 중이다. " f"execution_id={ctx.execution_id}"
            )

        try:
            return self._run_from_step(runbook, ctx, ctx.current_step_index)
        finally:
            lock.release(ctx.namespace, ctx.execution_id)

    # =========================================================================
    # 내부 실행 흐름
    # =========================================================================

    def _run(
        self,
        runbook: Runbook,
        trigger_event: dict[str, Any],
        namespace: str,
        execution_id: str,
    ) -> RunbookExecutionContext:
        """실행 컨텍스트 생성 후 첫 번째 Step부터 순차 실행."""
        from selfhealing.core.timezone import now

        ctx = RunbookExecutionContext(
            execution_id=execution_id,
            runbook_id=runbook.id,
            namespace=namespace,
            trigger_event=trigger_event,
            runbook_version=runbook.version,
            status=RunbookExecutionStatus.EXECUTING,
            started_at=now().isoformat(),
        )
        self._save_context(ctx)

        logger.info(
            "runbook_executor.started",
            runbook_id=runbook.id,
            execution_id=execution_id,
            namespace=namespace,
            steps=len(runbook.steps),
        )

        return self._run_from_step(runbook, ctx, start_index=0)

    def _run_from_step(
        self,
        runbook: Runbook,
        ctx: RunbookExecutionContext,
        start_index: int,
    ) -> RunbookExecutionContext:
        """지정 인덱스부터 순차 Step 실행. resume 시 사용."""
        from selfhealing.core.timezone import now

        ordered_steps = sorted(runbook.steps, key=lambda s: getattr(s, "order", 0) if hasattr(s, "order") else 0)

        for idx, step in enumerate(ordered_steps):
            if idx < start_index:
                continue

            ctx.current_step_index = idx
            self._save_context(ctx)

            step_result = self._execute_step(step, ctx, runbook)
            ctx.step_results[step.name] = step_result

            if not step_result.success and not step_result.idempotent:
                # 실패 — 역순 보상
                logger.warning(
                    "runbook_executor.step_failed",
                    runbook_id=runbook.id,
                    execution_id=ctx.execution_id,
                    step=step.name,
                    error=step_result.error,
                )
                ctx.abort_reason = f"Step '{step.name}' 실패: {step_result.error}"
                comp_summary = self._compensate_steps(ctx, runbook)
                ctx.status = RunbookExecutionStatus.FAILED
                ctx.completed_at = now().isoformat()
                self._save_context(ctx)
                self._store_to_dlq(ctx, ctx.abort_reason or "step_failed", comp_summary)
                return ctx

            # 안정화 대기 (wait_after_seconds)
            wait = getattr(step, "wait_after_seconds", 0) or 0
            if wait > 0:
                self._recovery_lock_extend(
                    ctx.namespace,
                    ctx.execution_id,
                    additional_seconds=wait + self._get_lock_extend_seconds(),
                )
                logger.info(
                    "runbook_executor.stabilization_wait",
                    step=step.name,
                    wait_seconds=wait,
                )
                time.sleep(wait)

            # 매 Step 완료 후 Lock Heartbeat 연장
            self._recovery_lock_extend(
                ctx.namespace,
                ctx.execution_id,
                additional_seconds=self._get_lock_extend_seconds(),
            )

        # 전체 성공
        ctx.status = RunbookExecutionStatus.COMPLETED
        ctx.completed_at = now().isoformat()
        self._save_context(ctx)

        logger.info(
            "runbook_executor.completed",
            runbook_id=runbook.id,
            execution_id=ctx.execution_id,
            steps_executed=len(ctx.step_results),
        )
        return ctx

    # =========================================================================
    # 단일 Step 실행
    # =========================================================================

    def _execute_step(
        self,
        step: RunbookStep,
        ctx: RunbookExecutionContext,
        runbook: Runbook,
    ) -> RunbookStepResult:
        """단일 Step 실행.

        흐름: condition 평가 → 멱등성 체크 → params 치환 → ActionExecutor 실행
        """
        from selfhealing.core.action_executor import Action
        from selfhealing.core.timezone import now
        from selfhealing.services.coordination.idempotent_step_handlers import (
            IdempotencyStatus,
            IdempotencyRecord,
            generate_idempotency_key,
        )

        started_at = now().isoformat()

        # 1. Condition 평가 (SagaStep.can_execute 패턴)
        condition = getattr(step, "condition", None)
        if condition is not None:
            can_run, skip_reason = self._evaluate_condition(condition, step, ctx)
            if not can_run:
                logger.info(
                    "runbook_executor.step_skipped",
                    step=step.name,
                    reason=skip_reason,
                )
                return RunbookStepResult(
                    step_name=step.name,
                    action_name=step.action,
                    success=True,
                    executed=False,
                    result_data={"skipped": True, "reason": skip_reason},
                    started_at=started_at,
                    completed_at=now().isoformat(),
                )

        # 2. 멱등성 체크 (IdempotentStepHandler 패턴)
        idempotency_key = generate_idempotency_key(
            session_id=ctx.execution_id,
            step_type=step.action,
            step_order=getattr(step, "order", 0),
            params={**step.params, "__runbook_version": runbook.version},
        )
        existing_record = self._get_idempotency_record(idempotency_key)
        if existing_record is not None and not existing_record.is_safe_to_execute():
            logger.info(
                "runbook_executor.step_idempotent",
                step=step.name,
                key=idempotency_key,
                status=existing_record.status.value,
            )
            return RunbookStepResult(
                step_name=step.name,
                action_name=step.action,
                success=existing_record.status == IdempotencyStatus.COMPLETED,
                executed=False,
                idempotent=True,
                result_data=existing_record.result or {},
                started_at=started_at,
                completed_at=now().isoformat(),
            )

        # 3. Params 변수 치환 (DotPathResolver — §9)
        resolved_params = resolve_params(step.params, ctx, DotPathResolver())

        # 4. ActionPrimitiveRegistry에서 핸들러 조회
        registry = self._get_primitive_registry()
        action_handler = registry.get(step.action) if registry else None

        if action_handler is None:
            error_msg = f"Action primitive를 찾을 수 없다: '{step.action}'"
            logger.error(
                "runbook_executor.primitive_not_found",
                step=step.name,
                action=step.action,
            )
            self._update_idempotency_record(
                idempotency_key,
                None,
                ctx.execution_id,
                step,
                failed=True,
                error_msg=error_msg,
            )
            return RunbookStepResult(
                step_name=step.name,
                action_name=step.action,
                success=False,
                executed=False,
                error=error_msg,
                started_at=started_at,
                completed_at=now().isoformat(),
            )

        # 5. RunbookStepContext 생성 (ActionHandler 호출용)
        from selfhealing.services.runbook.runbook_registry import RunbookStepContext

        step_ctx = RunbookStepContext(
            runbook_id=runbook.id,
            step_name=step.name,
            params=resolved_params,
            prev_results={k: v.result_data for k, v in ctx.step_results.items()},
            execution_id=ctx.execution_id,
        )

        action = Action(
            name=step.action,
            target=getattr(step, "target", None) or ctx.namespace,
            execute_fn=lambda: action_handler(step_ctx),
            params=resolved_params,
        )

        # 6. EXECUTING 상태 마킹 (IdempotentStepHandler 패턴)
        self._mark_idempotency_executing(idempotency_key, ctx.execution_id, step)

        # 7. 타임아웃 적용 실행 + Lock Heartbeat (SagaOrchestrator._execute_with_timeout 패턴)
        step_timeout = getattr(step, "timeout_seconds", None) or getattr(runbook, "global_timeout_seconds", None)

        try:
            action_result: ActionResult = self._execute_with_timeout(
                fn=lambda: self._get_action_executor().execute(action),
                timeout_seconds=step_timeout,
                step_name=step.name,
                ctx=ctx,
            )
        except RunbookStepTimeoutError as e:
            # In-doubt State — 타임아웃 시 partial_execution=True로 보상 대상 포함
            self._update_idempotency_record(
                idempotency_key,
                None,
                ctx.execution_id,
                step,
                failed=True,
                error_msg=str(e),
            )
            completed_at = now().isoformat()
            return RunbookStepResult(
                step_name=step.name,
                action_name=step.action,
                success=False,
                executed=True,
                partial_execution=True,
                error=str(e),
                result_data={"timeout": True, "in_doubt": True},
                started_at=started_at,
                completed_at=completed_at,
            )

        # 8. 멱등성 레코드 갱신
        self._update_idempotency_record(
            idempotency_key,
            action_result,
            ctx.execution_id,
            step,
        )

        # Shadow 모드(executed=False)이면 success=True로 간주 (실행 자체가 없음)
        effective_success = action_result.success if action_result.executed else True
        error = action_result.error if action_result.executed else None

        completed_at = now().isoformat()
        return RunbookStepResult(
            step_name=step.name,
            action_name=step.action,
            success=effective_success,
            executed=action_result.executed,
            result_data={"action_result": action_result.to_dict()},
            error=error,
            started_at=started_at,
            completed_at=completed_at,
        )

    # =========================================================================
    # 타임아웃 + Lock Heartbeat Polling
    # =========================================================================

    def _execute_with_timeout(
        self,
        fn: Callable[[], Any],
        timeout_seconds: int | None,
        step_name: str,
        ctx: RunbookExecutionContext,
    ) -> Any:
        """SagaOrchestrator._execute_with_timeout 패턴 완전 차용.

        1. ThreadPoolExecutor(max_workers=1)로 Step을 별도 스레드에서 실행
        2. Settings.lock_heartbeat_interval(기본 60초)마다 future.result()를 Polling
        3. 타임아웃이 아니면 Lock TTL을 Settings.lock_extend_seconds(기본 300초) 연장
        4. 전체 timeout_seconds 초과 시 RunbookStepTimeoutError raise
        """
        if timeout_seconds is None or timeout_seconds <= 0:
            return fn()

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future: Future = executor.submit(fn)
            elapsed = 0

            heartbeat_interval = self._get_lock_heartbeat_interval()
            extend_seconds = self._get_lock_extend_seconds()

            while elapsed < timeout_seconds:
                wait_time = min(heartbeat_interval, timeout_seconds - elapsed)
                try:
                    result = future.result(timeout=wait_time)
                    return result
                except FuturesTimeoutError:
                    elapsed += wait_time
                    if elapsed >= timeout_seconds:
                        break
                    # Lock Heartbeat: Step 실행 중에도 Lock TTL 연장
                    self._recovery_lock_extend(
                        ctx.namespace,
                        ctx.execution_id,
                        additional_seconds=extend_seconds,
                    )

            # 전체 타임아웃 초과
            future.cancel()
            raise RunbookStepTimeoutError(f"Runbook step '{step_name}'이 {timeout_seconds}초 후 타임아웃됐다.")
        finally:
            executor.shutdown(wait=False)

    # =========================================================================
    # 역순 보상 (_attempt_compensation 패턴)
    # =========================================================================

    def _compensate_steps(
        self,
        ctx: RunbookExecutionContext,
        runbook: Runbook,
    ) -> CompensationSummary:
        """성공/In-doubt Step들을 역순으로 보상.

        _fail_session() → _attempt_compensation() 패턴 적용:
        - COMPENSATING 상태 전환 → 역순 정렬 → 각 step 보상 → Fail-Open
        """
        ctx.status = RunbookExecutionStatus.COMPENSATING
        self._save_context(ctx)

        summary = CompensationSummary()

        # 보상 대상: 성공한 step 또는 In-doubt(partial_execution=True) step
        # SagaInstance.get_compensation_targets(): EXECUTED 또는 (EXECUTE_FAILED + partial_execution)
        compensation_targets = [
            (name, result)
            for name, result in ctx.step_results.items()
            if (result.success and result.executed) or result.partial_execution
        ]

        # 역순 정렬: Step order 역순 (보상은 실행의 반대 순서)
        def _step_order(name: str) -> int:
            for i, step in enumerate(runbook.steps):
                if step.name == name:
                    return getattr(step, "order", i)
            return 0

        compensation_targets.sort(key=lambda x: _step_order(x[0]), reverse=True)

        registry = self._get_primitive_registry()

        for step_name, step_result in compensation_targets:
            # 이미 보상 완료면 skip
            if step_result.compensation_status == "compensated":
                summary.compensated.append(step_name)
                continue

            # 보상 프리미티브 조회: on_failure_action → step.action → step_name 순서
            # register_compensate()는 action_name 기준으로 등록하므로
            # RunbookStep.on_failure_action이 있으면 우선, 없으면 step.action으로 조회
            compensate_key = self._resolve_compensate_key(step_name, runbook)
            compensate_fn = registry.get_compensate(compensate_key) if registry else None
            if compensate_fn is None:
                logger.debug(
                    "runbook_executor.no_compensate_fn",
                    step=step_name,
                    compensate_key=compensate_key,
                )
                summary.skipped.append(step_name)
                continue

            # Lock TTL 연장 (하트비트)
            self._recovery_lock_extend(
                ctx.namespace,
                ctx.execution_id,
                additional_seconds=self._get_lock_extend_seconds(),
            )

            try:
                compensate_params = self._build_compensate_params(step_name, step_result, ctx, runbook)
                from selfhealing.core.action_executor import Action

                action = Action(
                    name=f"compensate_{step_name}",
                    target=ctx.namespace,
                    execute_fn=lambda params=compensate_params: compensate_fn(**params),
                    params=compensate_params,
                )
                result = self._get_action_executor().execute(action)

                if result.success:
                    step_result.compensation_status = "compensated"
                    self._save_context(ctx)
                    summary.compensated.append(step_name)
                    logger.info(
                        "runbook_executor.step_compensated",
                        step=step_name,
                        execution_id=ctx.execution_id,
                    )
                else:
                    step_result.compensation_status = "compensate_failed"
                    self._save_context(ctx)
                    summary.failed.append((step_name, result.error or "Unknown"))
                    logger.warning(
                        "runbook_executor.compensation_failed",
                        step=step_name,
                        error=result.error,
                    )
            except Exception as e:
                # Fail-Open: 보상 실패가 전체를 중단시키지 않음
                step_result.compensation_status = "compensate_failed"
                self._save_context(ctx)
                summary.failed.append((step_name, str(e)))
                logger.warning(
                    "runbook_executor.compensation_exception",
                    step=step_name,
                    error=str(e),
                )

        return summary

    def _build_compensate_params(
        self,
        step_name: str,
        step_result: RunbookStepResult,
        ctx: RunbookExecutionContext,
        runbook: Runbook,
    ) -> dict[str, Any]:
        """보상 파라미터 생성.

        정방향 Step의 on_failure_params를 기반으로 하되,
        ${step.*} / ${trigger.*} 변수를 치환한다.

        정적 역연산: on_failure_params={"value_delta": -20}
        동적 참조:   on_failure_params={"ids": "${step.kill.result_data.killed_ids}"}
        """
        # 해당 Step의 on_failure_params 조회
        on_failure_params: dict[str, Any] = {}
        for step in runbook.steps:
            if step.name == step_name:
                on_failure_params = getattr(step, "on_failure_params", {}) or {}
                break

        return resolve_params(on_failure_params, ctx, DotPathResolver())

    @staticmethod
    def _resolve_compensate_key(step_name: str, runbook: Runbook) -> str:
        """보상 프리미티브 조회 키 결정.

        우선순위:
        1. RunbookStep.on_failure_action (명시적 보상 Action 지정)
        2. RunbookStep.action (실행 프리미티브 이름 — register_compensate의 action_name과 대응)

        register_compensate(action_name, fn)의 키가 action_name이므로
        step.action을 기본 조회 키로 사용한다.
        """
        for step in runbook.steps:
            if step.name == step_name:
                if getattr(step, "on_failure_action", None):
                    return step.on_failure_action
                return step.action
        return step_name

    # =========================================================================
    # Condition 평가 (SagaStep.can_execute 패턴)
    # =========================================================================

    def _evaluate_condition(
        self,
        condition: Any,
        step: RunbookStep,
        ctx: RunbookExecutionContext,
    ) -> tuple[bool, str]:
        """StepCondition을 실행 컨텍스트에서 평가.

        type:
            "always"         — 항상 실행
            "prev_failed"    — 이전 Step 실패 시만 실행
            "prev_succeeded" — 이전 Step 성공 시만 실행
            "data_match"     — source_step 결과 데이터 기반 분기
        """
        condition_type = getattr(condition, "type", "always")

        if condition_type == "always":
            return True, ""

        if condition_type in ("prev_failed", "prev_succeeded"):
            prev_result = self._get_prev_step_result(step, ctx)
            if prev_result is None:
                return True, "이전 Step 결과 없음 — 실행"
            if condition_type == "prev_failed":
                if not prev_result.success:
                    return True, ""
                return False, "이전 Step이 성공했으므로 skip"
            else:
                if prev_result.success:
                    return True, ""
                return False, "이전 Step이 실패했으므로 skip"

        if condition_type == "data_match":
            return self._evaluate_data_match_condition(condition, ctx)

        return True, ""

    def _get_prev_step_result(
        self,
        step: RunbookStep,
        ctx: RunbookExecutionContext,
    ) -> RunbookStepResult | None:
        """현재 Step 이전에 실행된 Step의 결과 반환."""
        # ctx.step_results는 삽입 순서를 유지하므로 마지막 키가 바로 이전 Step
        if not ctx.step_results:
            return None
        last_key = list(ctx.step_results.keys())[-1]
        return ctx.step_results.get(last_key)

    def _evaluate_data_match_condition(
        self,
        condition: Any,
        ctx: RunbookExecutionContext,
    ) -> tuple[bool, str]:
        """data_match 조건 평가 — source_step 결과에서 field를 읽어 비교."""
        source_step = getattr(condition, "source_step", None)
        field = getattr(condition, "field", None)
        operator = getattr(condition, "operator", None)
        value = getattr(condition, "value", None)

        if not source_step or not field:
            return True, "data_match 조건에 source_step 또는 field 없음 — 실행"

        prev = ctx.step_results.get(source_step)
        if prev is None:
            return True, f"source_step '{source_step}' 결과 없음 — 실행"

        # dot-path로 필드 접근
        from selfhealing.services.runbook.resolvers import DotPathResolver

        actual = DotPathResolver._traverse(prev.result_data, field.split("."))

        return self._compare_values(actual, operator, value)

    @staticmethod
    def _compare_values(
        actual: Any,
        operator: str | None,
        expected: Any,
    ) -> tuple[bool, str]:
        """값 비교. StepCondition.operator 기반."""
        if operator is None:
            return True, ""
        try:
            if operator == "eq":
                result = actual == expected
            elif operator == "neq":
                result = actual != expected
            elif operator == "gt":
                result = actual > expected
            elif operator == "gte":
                result = actual >= expected
            elif operator == "lt":
                result = actual < expected
            elif operator == "lte":
                result = actual <= expected
            elif operator == "contains":
                result = expected in actual
            elif operator == "not_none":
                result = actual is not None
            else:
                return True, f"알 수 없는 operator '{operator}' — 실행"
            if result:
                return True, ""
            return False, f"{actual!r} {operator} {expected!r} 불충족 — skip"
        except (TypeError, ValueError) as e:
            return True, f"비교 오류: {e} — 실행"

    # =========================================================================
    # 멱등성 레코드 관리
    # =========================================================================

    def _get_idempotency_record(
        self,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        """StateBackend에서 멱등성 레코드 조회."""
        from selfhealing.services.coordination.idempotent_step_handlers import (
            IdempotencyRecord,
        )

        backend = self._get_backend()
        if backend is None:
            return None
        try:
            data = backend.get(idempotency_key)
            if data and isinstance(data, dict):
                return IdempotencyRecord.from_dict(data)
        except Exception as e:
            logger.warning("runbook_executor.idempotency_get_failed", key=idempotency_key, error=str(e))
        return None

    def _mark_idempotency_executing(
        self,
        idempotency_key: str,
        execution_id: str,
        step: RunbookStep,
    ) -> None:
        """멱등성 레코드를 EXECUTING 상태로 마킹."""
        from selfhealing.services.coordination.idempotent_step_handlers import (
            IdempotencyRecord,
            IdempotencyStatus,
            IDEMPOTENCY_KEY_TTL_HOURS,
        )
        from selfhealing.core.timezone import now

        record = self._get_idempotency_record(idempotency_key) or IdempotencyRecord(
            idempotency_key=idempotency_key,
            session_id=execution_id,
            step_type=step.action,
            step_order=getattr(step, "order", 0),
        )
        record.status = IdempotencyStatus.EXECUTING
        record.started_at = now().isoformat()
        record.retry_count = (record.retry_count or 0) + 1

        backend = self._get_backend()
        if backend:
            try:
                backend.set(
                    idempotency_key,
                    record.to_dict(),
                    ttl=IDEMPOTENCY_KEY_TTL_HOURS * 3600,
                )
            except Exception as e:
                logger.warning("runbook_executor.idempotency_mark_failed", error=str(e))

    def _update_idempotency_record(
        self,
        idempotency_key: str,
        action_result: Any,
        execution_id: str,
        step: RunbookStep,
        failed: bool = False,
        error_msg: str | None = None,
    ) -> None:
        """멱등성 레코드를 COMPLETED 또는 FAILED로 업데이트."""
        from selfhealing.services.coordination.idempotent_step_handlers import (
            IdempotencyRecord,
            IdempotencyStatus,
            IDEMPOTENCY_KEY_TTL_HOURS,
        )
        from selfhealing.core.timezone import now

        record = self._get_idempotency_record(idempotency_key) or IdempotencyRecord(
            idempotency_key=idempotency_key,
            session_id=execution_id,
            step_type=step.action,
            step_order=getattr(step, "order", 0),
        )

        if failed or (action_result is not None and not getattr(action_result, "success", True)):
            record.status = IdempotencyStatus.FAILED
            record.error_message = error_msg or (getattr(action_result, "error", None) if action_result else None)
        else:
            record.status = IdempotencyStatus.COMPLETED
            record.result = action_result.to_dict() if action_result else {}

        record.completed_at = now().isoformat()

        backend = self._get_backend()
        if backend:
            try:
                backend.set(
                    idempotency_key,
                    record.to_dict(),
                    ttl=IDEMPOTENCY_KEY_TTL_HOURS * 3600,
                )
            except Exception as e:
                logger.warning("runbook_executor.idempotency_update_failed", error=str(e))

    # =========================================================================
    # 컨텍스트 영속화 (_save_session 패턴)
    # =========================================================================

    def _save_context(self, ctx: RunbookExecutionContext) -> None:
        """컨텍스트 영속화. StateBackend 사용. Settings.context_ttl_seconds TTL."""
        backend = self._get_backend()
        if backend is None:
            return
        key = CONTEXT_KEY_TEMPLATE.format(execution_id=ctx.execution_id)
        try:
            backend.set(key, ctx.to_dict(), ttl=self._get_context_ttl_seconds())
        except Exception as e:
            logger.warning("runbook_executor.save_context_failed", execution_id=ctx.execution_id, error=str(e))

    def _load_context(self, execution_id: str) -> RunbookExecutionContext | None:
        """영속화된 컨텍스트 로드. resume_execution() 시 사용."""
        backend = self._get_backend()
        if backend is None:
            return None
        key = CONTEXT_KEY_TEMPLATE.format(execution_id=execution_id)
        try:
            data = backend.get(key)
            if data and isinstance(data, dict):
                return RunbookExecutionContext.from_dict(data)
        except Exception as e:
            logger.warning("runbook_executor.load_context_failed", execution_id=execution_id, error=str(e))
        return None

    # =========================================================================
    # Lock TTL 연장 (Fail-Open)
    # =========================================================================

    def _recovery_lock_extend(
        self,
        namespace: str,
        execution_id: str,
        additional_seconds: int = LOCK_EXTEND_SECONDS,
    ) -> None:
        """Lock TTL 연장. Fail-Open — 실패해도 실행 흐름을 중단하지 않는다."""
        try:
            self._get_recovery_lock().extend(namespace, execution_id, additional_seconds=additional_seconds)
        except Exception as e:
            logger.warning(
                "runbook_executor.lock_extend_failed",
                namespace=namespace,
                execution_id=execution_id,
                error=str(e),
            )

    # =========================================================================
    # DLQ (_store_failure_to_dlq 패턴)
    # =========================================================================

    def _store_to_dlq(
        self,
        ctx: RunbookExecutionContext,
        error: str,
        compensation: CompensationSummary,
    ) -> None:
        """실패 정보를 DLQ에 저장. Fail-Open — 실패해도 실행 흐름을 중단하지 않는다."""
        try:
            from selfhealing.services.dlq import store_to_dlq

            store_to_dlq(
                domain="selfhealing",
                failure_type="RUNBOOK_EXECUTION_FAILED",
                entity_type="runbook_execution",
                entity_id=ctx.execution_id,
                error_message=error,
                snapshot_data={"context": ctx.to_dict()},
                metadata={
                    "runbook_id": ctx.runbook_id,
                    "namespace": ctx.namespace,
                    "completed_steps": list(ctx.step_results.keys()),
                    "compensation_failed": compensation.failed,
                },
                next_action_hint=(
                    f"Runbook '{ctx.runbook_id}' 실패. " f"resume_execution('{ctx.execution_id}') 호출을 검토하라."
                ),
                recommended_action="manual_review",
            )
        except Exception as e:
            logger.warning("runbook_executor.dlq_failed", error=str(e))
