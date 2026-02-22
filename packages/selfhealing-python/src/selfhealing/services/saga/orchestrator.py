"""
Saga Orchestrator.

분산 Saga 오케스트레이터.
순방향 실행 → 실패 시 역순 compensate → compensate 실패 시 DLQ 저장의
전체 흐름을 관리한다.

기존 인프라 7종을 재사용한다:
- DistributedRecoveryLock: 분산 락
- IdempotencyService: 중복 실행 방지
- DLQService: 실패 데이터 저장 (3단계 fallback)
- EventBus: 이벤트 발행
- AtomicTransition: Redis Lua 기반 원자적 상태 전환
- RecoveryCircuitBreaker: 서킷브레이커
- BlastRadiusService: 영향도 평가
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.services.coordination.recovery_coordinator import (
    SessionVersionConflictError,
)
from selfhealing.services.saga.events import SagaEventType
from selfhealing.services.saga.lua_scripts import (
    SAGA_INSTANCE_CAS_SCRIPT,
    SAGA_TRANSITION_SCRIPT,
)
from selfhealing.services.saga.models import (
    SagaContext,
    SagaDefinition,
    SagaInstance,
    SagaStatus,
    SagaStepInstance,
    SagaStepStatus,
    StepResult,
)
from selfhealing.services.saga.registry import get_saga_definition

if TYPE_CHECKING:
    from selfhealing.services.blast_radius.service import BlastRadiusService
    from selfhealing.services.coordination.distributed_recovery_lock import (
        DistributedRecoveryLock,
    )
    from selfhealing.services.coordination.recovery_circuit_breaker import (
        RecoveryCircuitBreaker,
    )
    from selfhealing.services.dlq.base import DLQService
    from selfhealing.services.event_bus.bus import EventBus
    from selfhealing.services.idempotency.service import IdempotencyService

logger = structlog.get_logger()

# =============================================================================
# 상수
# =============================================================================

HEARTBEAT_INTERVAL = 60
"""Lock Heartbeat 간격 (초). 60초마다 락 TTL을 연장한다."""

EXTEND_SECONDS = 300
"""Lock TTL 연장 시간 (초). 5분."""

MAX_RESUME_COUNT = 10
"""무한 재개 방지 최대 카운터."""

STALE_THRESHOLD_SECONDS = 300
"""고아 Saga 판별 임계값 (초). 5분."""


# =============================================================================
# SagaOrchestrator
# =============================================================================


class SagaOrchestrator:
    """분산 Saga 오케스트레이터.

    기존 RecoveryCoordinator와 동일한 의존성 주입 패턴.
    모든 외부 의존성은 생성자에서 주입받거나 ProviderRegistry로 지연 로드.

    Usage::

        orchestrator = SagaOrchestrator()
        result = orchestrator.execute_saga("order_creation", {
            "order_id": 123,
            "user_id": 456,
            "amount": 50000,
        })
    """

    def __init__(
        self,
        lock: DistributedRecoveryLock | None = None,
        idempotency: IdempotencyService | None = None,
        dlq: DLQService | None = None,
        event_bus: EventBus | None = None,
        circuit_breaker: RecoveryCircuitBreaker | None = None,
        blast_radius: BlastRadiusService | None = None,
        backend: Any | None = None,
    ):
        """의존성 주입.

        None이면 ProviderRegistry 또는 기본 구현 사용.
        RecoveryCoordinator.__init__()과 동일한 패턴.
        """
        self._lock = lock
        self._idempotency = idempotency
        self._dlq = dlq
        self._event_bus = event_bus
        self._circuit_breaker = circuit_breaker
        self._blast_radius = blast_radius
        self._backend = backend

    # =========================================================================
    # 진입점
    # =========================================================================

    def execute_saga(
        self,
        saga_name: str,
        initial_data: dict[str, Any],
        initiated_by: str = "system",
        correlation_id: str | None = None,
    ) -> SagaInstance:
        """Saga 실행 진입점.

        1. SagaDefinition 조회 (레지스트리에서)
        2. SagaInstance 생성 (PENDING)
        3. 거버넌스 체크
        4. 분산 락 획득
        5. Forward 실행 루프 (_execute_forward)
        6. 실패 시 Compensate 루프 (_execute_compensation)
        7. 분산 락 해제
        8. SagaInstance 반환

        Args:
            saga_name: 등록된 SagaDefinition 이름
            initial_data: 초기 데이터 (Step들에게 전달)
            initiated_by: 시작 주체
            correlation_id: EventBus 추적 ID

        Returns:
            SagaInstance — 최종 상태 포함

        Raises:
            ValueError: saga_name이 등록되지 않은 경우
        """
        # 1. 정의 조회
        definition = get_saga_definition(saga_name)
        if not definition:
            raise ValueError(f"Saga '{saga_name}' not registered")

        # 2. 인스턴스 생성
        instance = self._create_instance(definition, initial_data, initiated_by, correlation_id)

        # 3. 거버넌스 체크
        governance = self._check_governance(saga_name)
        if governance is not None and not governance.allowed:
            instance.status = SagaStatus.COMPENSATION_FAILED
            instance.error_message = f"Governance blocked: {governance.block_message}"
            return instance

        # 4. 분산 락 획득
        lock_namespace = f"saga:{saga_name}:{instance.id}"
        acquired = self._lock.acquire(
            namespace=lock_namespace,
            session_id=instance.id,
        )
        if not acquired:
            instance.status = SagaStatus.COMPENSATION_FAILED
            instance.error_message = "Failed to acquire distributed lock"
            return instance

        try:
            # EventBus 알림
            self._emit_event(
                SagaEventType.SAGA_STARTED,
                data={"saga_name": saga_name, "instance_id": instance.id},
                correlation_id=correlation_id,
            )

            # 초기 Checkpoint
            instance.version += 1
            self._save_instance(instance)

            # 5. Forward 실행
            instance = self._execute_forward(instance, definition)

            # 6. 실패 시 Compensation
            if instance.status == SagaStatus.COMPENSATING:
                instance = self._execute_compensation(instance, definition)

        finally:
            # 7. 락 해제
            self._lock.release(
                namespace=lock_namespace,
                session_id=instance.id,
            )

        # 8. 최종 이벤트 발행
        self._emit_final_event(instance)

        return instance

    # =========================================================================
    # Forward 실행
    # =========================================================================

    def _execute_forward(
        self,
        instance: SagaInstance,
        definition: SagaDefinition,
    ) -> SagaInstance:
        """Forward Step들을 순차 실행.

        성공: current_step_index 증가, StepResult.data를 context에 merge
        실패: status → COMPENSATING으로 전환

        각 Step 실행 전:
        1. 서킷브레이커 상태 확인
        2. 멱등성 확인
        3. can_execute 확인
        4. 타임아웃 + Lock Heartbeat 적용 실행
        """
        instance.status = SagaStatus.RUNNING
        instance.started_at = datetime.now(timezone.utc).isoformat()

        # RUNNING 전환 Checkpoint
        instance.version += 1
        self._save_instance(instance)

        lock_namespace = f"saga:{instance.saga_name}:{instance.id}"

        start_index = instance.current_step_index
        for i in range(start_index, len(definition.steps)):
            step_def = definition.steps[i]
            step_instance = instance.step_instances[i]

            # 1. 서킷브레이커 확인
            cb_state = self._get_circuit_breaker_state(step_def.name)
            if cb_state is not None and cb_state.value == "open":
                instance.status = SagaStatus.SUSPENDED
                instance.error_message = f"Circuit breaker OPEN for step '{step_def.name}'"

                # SUSPENDED Checkpoint
                instance.version += 1
                self._save_instance(instance)

                self._emit_event(
                    SagaEventType.SAGA_SUSPENDED,
                    data={
                        "step": step_def.name,
                        "instance_id": instance.id,
                    },
                )
                return instance

            # 2. 멱등성 확인
            if self._check_idempotency(instance.id, step_def.name, "execute"):
                step_instance.status = SagaStepStatus.EXECUTED
                instance.current_step_index = i + 1
                continue

            # 3. can_execute 확인
            can_exec, reason = step_def.can_execute(instance.context)
            if not can_exec:
                step_instance.status = SagaStepStatus.EXECUTE_FAILED
                step_instance.error_message = reason

                # Context Injection (can_execute 실패 시에도 적용)
                instance.context.set_compensation_context(
                    failed_step_name=step_def.name,
                    abort_reason=f"can_execute rejected: {reason}",
                )

                instance.status = SagaStatus.COMPENSATING
                instance.compensate_step_index = i - 1

                # COMPENSATING 전환 Checkpoint
                instance.version += 1
                self._save_instance(instance)
                return instance

            # 4. execute 실행 (타임아웃 적용)
            step_instance.status = SagaStepStatus.EXECUTING
            step_instance.execute_started_at = datetime.now(timezone.utc).isoformat()

            timeout = step_def.timeout_seconds or definition.timeout_seconds

            logger.info(
                "[Saga] Step execute",
                extra={
                    "saga_name": instance.saga_name,
                    "instance_id": instance.id,
                    "step_name": step_def.name,
                    "step_index": i,
                    "total_steps": len(definition.steps),
                    "correlation_id": instance.correlation_id,
                },
            )

            # Lock Heartbeat 매개변수 전달
            step_start_time = datetime.now(timezone.utc)
            result = self._execute_with_timeout(
                step_def.execute,
                instance.context,
                timeout,
                lock_namespace=lock_namespace,
                instance_id=instance.id,
            )
            step_elapsed = (datetime.now(timezone.utc) - step_start_time).total_seconds()

            # Prometheus: saga_step_duration_seconds (execute phase)
            self._record_step_duration(
                saga_name=instance.saga_name,
                step_name=step_def.name,
                phase="execute",
                duration_seconds=step_elapsed,
            )

            if result.success:
                step_instance.status = SagaStepStatus.EXECUTED
                step_instance.execute_completed_at = datetime.now(timezone.utc).isoformat()
                instance.context.merge_step_result(step_def.name, result.data)
                instance.current_step_index = i + 1

                # Step 성공 Checkpoint
                instance.version += 1
                self._save_instance(instance)

                self._emit_event(
                    SagaEventType.SAGA_STEP_COMPLETED,
                    data={
                        "step": step_def.name,
                        "instance_id": instance.id,
                    },
                )
            else:
                step_instance.status = SagaStepStatus.EXECUTE_FAILED
                step_instance.error_message = result.error
                step_instance.error_code = result.error_code
                step_instance.execute_completed_at = datetime.now(timezone.utc).isoformat()

                # partial_execution 전파
                step_instance.partial_execution = result.partial_execution

                # Step 실패 Checkpoint
                instance.version += 1
                self._save_instance(instance)

                # Async Yield — 재시도 가능하면 Celery countdown 위임
                if result.retryable and step_instance.retry_count < definition.max_retries_per_step:
                    step_instance.retry_count += 1

                    # 백오프 계산
                    from selfhealing.core.backoff import get_backoff_calculator

                    calculator = get_backoff_calculator(
                        strategy=definition.retry_backoff_strategy,
                    )
                    delay = calculator.calculate(attempt=step_instance.retry_count)

                    step_instance.next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()

                    # RETRY_SCHEDULED 상태로 전환
                    step_instance.status = SagaStepStatus.RETRY_SCHEDULED

                    instance.version += 1
                    self._save_instance(instance)

                    # Celery countdown으로 미래 시점에 재실행
                    from selfhealing.services.saga.tasks import (
                        resume_saga_instance_task,
                    )

                    resume_saga_instance_task.apply_async(
                        args=[instance.id],
                        countdown=delay,
                    )
                    return instance  # 현재 워커는 즉시 반환

                # Context Injection — COMPENSATING 전환 직전
                instance.context.set_compensation_context(
                    failed_step_name=step_def.name,
                    abort_reason=result.error,
                    abort_error_code=result.error_code,
                )

                # compensate_step_index 보정
                # partial_execution=True면 현재 Step도 보상 대상이므로 i부터 시작
                # False면 직전 성공 Step(i-1)부터 역순
                instance.status = SagaStatus.COMPENSATING
                instance.compensate_step_index = i if result.partial_execution else i - 1

                # COMPENSATING Checkpoint
                instance.version += 1
                self._save_instance(instance)

                self._emit_event(
                    SagaEventType.SAGA_STEP_FAILED,
                    data={
                        "step": step_def.name,
                        "error": result.error,
                        "instance_id": instance.id,
                    },
                )
                return instance

        # 모든 Step 성공
        instance.status = SagaStatus.COMPLETED
        instance.completed_at = datetime.now(timezone.utc).isoformat()

        # 최종 COMPLETED Checkpoint
        instance.version += 1
        self._save_instance(instance)

        return instance

    # =========================================================================
    # Compensation 실행
    # =========================================================================

    def _execute_compensation(
        self,
        instance: SagaInstance,
        definition: SagaDefinition,
    ) -> SagaInstance:
        """성공한 Step들을 역순으로 compensate.

        마지막 성공 Step부터 첫 Step까지 역순으로 compensate() 호출.
        compensate 실패 시 DLQ에 저장한다.
        """
        compensation_failures: list[str] = []

        lock_namespace = f"saga:{instance.saga_name}:{instance.id}"

        # 역순 순회: 마지막 성공 Step → 첫 Step
        for i in range(instance.compensate_step_index, -1, -1):
            step_def = definition.steps[i]
            step_instance = instance.step_instances[i]

            # 보상 대상 필터
            # EXECUTED 또는 (EXECUTE_FAILED + partial_execution)만 보상
            is_compensation_target = step_instance.status == SagaStepStatus.EXECUTED or (
                step_instance.status == SagaStepStatus.EXECUTE_FAILED and step_instance.partial_execution
            )
            if not is_compensation_target:
                continue

            # 멱등성 확인
            if self._check_idempotency(instance.id, step_def.name, "compensate"):
                step_instance.status = SagaStepStatus.COMPENSATED
                continue

            # 매 Step 보상 전 Lock TTL 연장 (하트비트)
            self._try_extend_lock(lock_namespace, instance.id, step_def.name)

            # compensate 실행
            step_instance.status = SagaStepStatus.COMPENSATING
            step_instance.compensate_started_at = datetime.now(timezone.utc).isoformat()

            try:
                timeout = step_def.timeout_seconds or definition.timeout_seconds

                # Compensation 루프도 Lock Heartbeat 매개변수 전달
                comp_start_time = datetime.now(timezone.utc)
                result = self._execute_with_timeout(
                    step_def.compensate,
                    instance.context,
                    timeout,
                    lock_namespace=lock_namespace,
                    instance_id=instance.id,
                )
                comp_elapsed = (datetime.now(timezone.utc) - comp_start_time).total_seconds()

                # Prometheus: saga_step_duration_seconds (compensate phase)
                self._record_step_duration(
                    saga_name=instance.saga_name,
                    step_name=step_def.name,
                    phase="compensate",
                    duration_seconds=comp_elapsed,
                )

                if result.success:
                    step_instance.status = SagaStepStatus.COMPENSATED
                    step_instance.compensate_completed_at = datetime.now(timezone.utc).isoformat()

                    # Prometheus: saga_compensation_steps_total
                    self._record_compensation_metric(
                        saga_name=instance.saga_name,
                        step_name=step_def.name,
                        status="success",
                    )

                    # Compensate 성공 Checkpoint
                    instance.compensate_step_index = i - 1
                    instance.version += 1
                    self._save_instance(instance)

                    self._emit_event(
                        SagaEventType.SAGA_COMPENSATING,
                        data={
                            "step": step_def.name,
                            "instance_id": instance.id,
                            "direction": "compensated",
                        },
                    )
                else:
                    step_instance.status = SagaStepStatus.COMPENSATE_FAILED
                    step_instance.error_message = result.error
                    step_instance.compensate_completed_at = datetime.now(timezone.utc).isoformat()
                    compensation_failures.append(step_def.name)

                    # Prometheus: saga_compensation_steps_total
                    self._record_compensation_metric(
                        saga_name=instance.saga_name,
                        step_name=step_def.name,
                        status="failure",
                    )

                    # Compensate 실패 Checkpoint
                    instance.version += 1
                    self._save_instance(instance)

            except Exception as e:
                step_instance.status = SagaStepStatus.COMPENSATE_FAILED
                step_instance.error_message = str(e)
                step_instance.compensate_completed_at = datetime.now(timezone.utc).isoformat()
                compensation_failures.append(step_def.name)

                # Prometheus: saga_compensation_steps_total
                self._record_compensation_metric(
                    saga_name=instance.saga_name,
                    step_name=step_def.name,
                    status="failure",
                )

                # Compensate 예외 Checkpoint
                instance.version += 1
                self._save_instance(instance)

        if not compensation_failures:
            # 모든 compensate 성공
            instance.status = SagaStatus.COMPENSATED
            instance.completed_at = datetime.now(timezone.utc).isoformat()
        else:
            # compensate 실패 → DLQ 저장
            instance.status = SagaStatus.COMPENSATION_FAILED
            instance.completed_at = datetime.now(timezone.utc).isoformat()
            instance.error_message = f"Compensation failed for steps: {compensation_failures}"
            self._store_compensation_failure_to_dlq(instance, compensation_failures)

        # 최종 상태 Checkpoint
        instance.version += 1
        self._save_instance(instance)

        return instance

    # =========================================================================
    # DLQ 통합
    # =========================================================================

    def _store_compensation_failure_to_dlq(
        self,
        instance: SagaInstance,
        failed_steps: list[str],
    ) -> None:
        """Compensation 실패를 DLQ에 저장.

        DLQService.store_failure()의 3단계 fallback (DB → LMDB → JSONL → stderr).
        snapshot_data에 전체 Saga 컨텍스트를 포함한다.
        """
        self._dlq.store_failure(
            domain="saga",
            failure_type="COMPENSATION_FAILED",
            entity_type="saga_instance",
            entity_id=instance.id,
            error_code="SAGA_COMPENSATION_FAILED",
            error_message=instance.error_message or "",
            snapshot_data={
                "saga_name": instance.saga_name,
                "instance": instance.to_dict(),
                "failed_compensation_steps": failed_steps,
                "executed_steps": [s.step_name for s in instance.step_instances if s.status == SagaStepStatus.EXECUTED],
                "compensated_steps": [s.step_name for s in instance.step_instances if s.status == SagaStepStatus.COMPENSATED],
            },
            metadata={
                "correlation_id": instance.correlation_id,
                "initiated_by": instance.initiated_by,
            },
            recommended_action=(
                "Manual compensation required for failed steps. " "Check snapshot_data for full saga context."
            ),
        )

        # EventBus 알림
        from selfhealing.services.event_bus.bus import EventPriority

        self._emit_event(
            SagaEventType.SAGA_COMPENSATION_FAILED,
            data={
                "saga_name": instance.saga_name,
                "instance_id": instance.id,
                "failed_steps": failed_steps,
            },
            priority=EventPriority.CRITICAL,
            correlation_id=instance.correlation_id,
        )

        # BlastRadius 평가
        if self._blast_radius:
            try:
                self._blast_radius.assess_impact(
                    stage_name=f"saga:{instance.saga_name}",
                    trigger_event="SAGA_COMPENSATION_FAILED",
                    failing_services=failed_steps,
                )
            except Exception as e:
                logger.warning(
                    "[Saga] BlastRadius assess_impact failed",
                    extra={"instance_id": instance.id, "error": str(e)},
                )

    # =========================================================================
    # 타임아웃 실행 + Lock Heartbeat
    # =========================================================================

    def _execute_with_timeout(
        self,
        fn: Callable[[SagaContext], StepResult],
        ctx: SagaContext,
        timeout_seconds: int,
        lock_namespace: str | None = None,
        instance_id: str | None = None,
    ) -> StepResult:
        """Step 함수를 타임아웃 내에서 실행 + Lock Heartbeat.

        concurrent.futures.ThreadPoolExecutor로 실행하고,
        HEARTBEAT_INTERVAL(60초)마다 깨어나 Lock TTL을 EXTEND_SECONDS(300초) 연장.
        타임아웃 초과 시 StepResult.failed()를 반환한다.
        """
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(fn, ctx)

            elapsed = 0
            while elapsed < timeout_seconds:
                wait_time = min(HEARTBEAT_INTERVAL, timeout_seconds - elapsed)
                try:
                    result = future.result(timeout=wait_time)
                    return result
                except FuturesTimeoutError:
                    elapsed += wait_time

                    # Lock Heartbeat: Polling 루프 내 락 연장
                    if lock_namespace and instance_id and self._lock:
                        try:
                            self._lock.extend(
                                namespace=lock_namespace,
                                session_id=instance_id,
                                additional_seconds=EXTEND_SECONDS,
                            )
                        except Exception as e:
                            logger.warning(
                                "[Saga] Lock extend failed during step execution",
                                extra={
                                    "instance_id": instance_id,
                                    "elapsed": elapsed,
                                    "error": str(e),
                                },
                            )
                except Exception as e:
                    return StepResult.failed(
                        error=(f"Step execution error: " f"{type(e).__name__}: {str(e)[:200]}"),
                        error_code="STEP_EXCEPTION",
                        retryable=False,
                    )

            # 최종 타임아웃 — future 취소 시도 후 비-블로킹 정리
            future.cancel()
            return StepResult.failed(
                error=f"Step execution timed out after {timeout_seconds}s",
                error_code="STEP_TIMEOUT",
                retryable=True,
            )
        finally:
            executor.shutdown(wait=False)

    # =========================================================================
    # 상태 영속화 (Fail-fast)
    # =========================================================================

    def _save_instance(self, instance: SagaInstance) -> None:
        """SagaInstance를 저장소에 영속화 (OCC 적용).

        저장 실패 시 Fail-fast 전략:
        - SessionVersionConflictError → re-raise (동시 쓰기 충돌)
        - Redis 장애/타임아웃 → re-raise (Split-brain 방지)

        OCC 메커니즘:
        - 호출 전 instance.version += 1 (호출부 책임)
        - Redis Lua 스크립트로 원자적 CAS
        - 버전 불일치 시 SessionVersionConflictError 발생
        """
        backend = self._get_backend()
        key = f"saga:instance:{instance.id}"
        expected_version = instance.version - 1  # 호출부에서 이미 +1 했으므로

        try:
            data = instance.to_dict()

            if hasattr(backend, "_client"):
                # Redis: Lua CAS 스크립트로 원자적 저장
                serialized = json.dumps(data, default=str)
                result = backend._client.eval(
                    SAGA_INSTANCE_CAS_SCRIPT,
                    1,
                    backend._make_key(key),
                    serialized,
                    str(expected_version),
                )
                if result == 0:
                    instance.version = expected_version  # 롤백
                    raise SessionVersionConflictError(instance.id, expected_version, instance.version)
            else:
                # InMemory/테스트: 단순 저장
                backend.set(key, data)

        except SessionVersionConflictError:
            # OCC 충돌: 동시 쓰기 감지 → re-raise
            raise

        except Exception as e:
            # Redis 장애/타임아웃: Split-brain 방지를 위해 re-raise
            logger.exception(
                "[Saga] _save_instance failed — Fail-fast",
                extra={
                    "instance_id": instance.id,
                    "version": instance.version,
                    "error": str(e),
                },
            )
            raise

    def _load_instance(self, instance_id: str) -> SagaInstance | None:
        """저장소에서 SagaInstance를 로드."""
        backend = self._get_backend()
        key = f"saga:instance:{instance_id}"

        try:
            data = backend.get(key)
            if data is None:
                return None
            if isinstance(data, str):
                data = json.loads(data)
            return SagaInstance.from_dict(data)
        except Exception as e:
            logger.exception(
                "[Saga] _load_instance failed",
                extra={"instance_id": instance_id, "error": str(e)},
            )
            return None

    # =========================================================================
    # Saga 재개
    # =========================================================================

    def resume_saga(self, instance_id: str) -> SagaInstance:
        """중단된 Saga를 현재 상태에서 이어서 실행.

        재개 대상 상태:
        - SUSPENDED → 서킷브레이커 상태 재확인 후 Forward 재개
        - RUNNING (고아) → current_step_index부터 Forward 재개
        - COMPENSATING (고아) → compensate_step_index부터 Compensate 재개

        무한 재개 방지:
        - instance.metadata에 resume_count 기록
        - MAX_RESUME_COUNT(10) 초과 시 COMPENSATION_FAILED 전환 + DLQ 저장

        GC Pause 방어:
        - 재개 시 반드시 분산 락 획득을 선행

        Args:
            instance_id: Saga 인스턴스 ID

        Returns:
            SagaInstance — 재개 후 최종 상태

        Raises:
            ValueError: 인스턴스를 찾을 수 없는 경우
        """
        # 1. 인스턴스 로드
        instance = self._load_instance(instance_id)
        if instance is None:
            raise ValueError(f"Saga instance '{instance_id}' not found")

        # 2. 재개 가능 상태 확인
        resumable_statuses = {
            SagaStatus.SUSPENDED,
            SagaStatus.RUNNING,
            SagaStatus.COMPENSATING,
        }
        if instance.status not in resumable_statuses:
            logger.info(
                "[Saga] Instance not resumable",
                extra={
                    "instance_id": instance_id,
                    "status": instance.status.value,
                },
            )
            return instance

        # 3. 무한 재개 방지 카운터
        if instance.metadata is None:
            instance.metadata = {}
        resume_count = instance.metadata.get("resume_count", 0)
        if resume_count >= MAX_RESUME_COUNT:
            instance.status = SagaStatus.COMPENSATION_FAILED
            instance.error_message = f"Max resume count ({MAX_RESUME_COUNT}) exceeded"
            instance.version += 1
            self._save_instance(instance)
            self._store_compensation_failure_to_dlq(instance, [])
            return instance
        instance.metadata["resume_count"] = resume_count + 1

        # 4. 정의 조회 + 버전 호환성 검증
        definition = get_saga_definition(instance.saga_name)
        if definition is None:
            instance.error_message = f"Saga definition '{instance.saga_name}' not found"
            instance.version += 1
            self._save_instance(instance)
            return instance

        if not self._validate_version_compatibility(instance, definition):
            instance.version += 1
            self._save_instance(instance)
            return instance

        # 5. 분산 락 획득 (GC Pause 방어)
        lock_namespace = f"saga:{instance.saga_name}:{instance.id}"
        acquired = self._lock.acquire(
            namespace=lock_namespace,
            session_id=instance.id,
        )
        if not acquired:
            logger.info(
                "[Saga] Lock acquisition failed on resume — another worker active",
                extra={"instance_id": instance_id},
            )
            return instance

        try:
            self._emit_event(
                SagaEventType.SAGA_RESUMED,
                data={
                    "instance_id": instance.id,
                    "resume_count": resume_count + 1,
                },
            )

            # 6. 상태별 재개
            if instance.status in {SagaStatus.SUSPENDED, SagaStatus.RUNNING}:
                # Forward 재개: current_step_index부터
                instance = self._execute_forward(instance, definition)
                if instance.status == SagaStatus.COMPENSATING:
                    instance = self._execute_compensation(instance, definition)

            elif instance.status == SagaStatus.COMPENSATING:
                # Compensate 재개: compensate_step_index부터
                instance = self._execute_compensation(instance, definition)

        finally:
            self._lock.release(
                namespace=lock_namespace,
                session_id=instance.id,
            )

        self._emit_final_event(instance)
        return instance

    # =========================================================================
    # 헬퍼 메서드
    # =========================================================================

    def _create_instance(
        self,
        definition: SagaDefinition,
        initial_data: dict[str, Any],
        initiated_by: str,
        correlation_id: str | None,
    ) -> SagaInstance:
        """SagaInstance를 생성한다."""
        instance_id = f"saga-{uuid.uuid4().hex[:12]}"

        step_instances = [SagaStepInstance(step_name=step.name, order=i) for i, step in enumerate(definition.steps)]

        context = SagaContext(
            saga_instance_id=instance_id,
            initial_data=initial_data,
        )

        return SagaInstance(
            id=instance_id,
            saga_name=definition.name,
            definition_version=definition.version,
            status=SagaStatus.PENDING,
            step_instances=step_instances,
            context=context,
            initiated_by=initiated_by,
            correlation_id=correlation_id,
        )

    def _get_backend(self):
        """StateBackend 인스턴스 획득."""
        if self._backend is not None:
            return self._backend

        from selfhealing.core.state_backend import get_state_backend

        return get_state_backend()

    def _check_governance(self, saga_name: str):
        """거버넌스 체크. 모듈 미설치 시 None 반환."""
        try:
            from selfhealing.services.governance.checks import (
                check_all_governance,
            )

            return check_all_governance(
                check_kill_switch=True,
                check_emergency=True,
                emergency_min_level=2,
                check_error_budget=True,
                operation_name=f"saga:{saga_name}",
                service_name="SagaOrchestrator",
                domain="saga",
                audit_on_block=True,
            )
        except Exception:
            return None

    def _get_circuit_breaker_state(self, step_name: str):
        """서킷브레이커 상태 조회. 미설정 시 None 반환."""
        if not self._circuit_breaker:
            return None
        try:
            return self._circuit_breaker.get_state(namespace=f"saga:{step_name}")
        except Exception:
            return None

    def _check_idempotency(self, instance_id: str, step_name: str, operation: str) -> bool:
        """멱등성 확인. 이미 수행된 작업이면 True를 반환한다."""
        if not self._idempotency:
            return False
        try:
            from selfhealing.services.idempotency.models import (
                IdempotencyDomain,
                IdempotencyKey,
            )

            idemp_key = IdempotencyKey.for_operation(
                entity_type="saga_step",
                entity_id=instance_id,
                operation=f"{step_name}:{operation}",
                domain=IdempotencyDomain.INTERNAL_PROCESS,
            )
            idemp_result = self._idempotency.check(idemp_key)
            return idemp_result.is_duplicate
        except Exception:
            return False

    def _try_extend_lock(
        self,
        lock_namespace: str,
        instance_id: str,
        step_name: str,
    ) -> None:
        """Lock TTL 연장을 시도한다. 실패해도 진행한다."""
        if not self._lock:
            return
        try:
            self._lock.extend(
                namespace=lock_namespace,
                session_id=instance_id,
                additional_seconds=EXTEND_SECONDS,
            )
        except Exception as e:
            logger.warning(
                "[Saga] Lock extend failed before compensate",
                extra={
                    "instance_id": instance_id,
                    "step": step_name,
                    "error": str(e),
                },
            )

    def _emit_event(
        self,
        event_type: str,
        data: dict[str, Any],
        correlation_id: str | None = None,
        priority: Any = None,
    ) -> None:
        """EventBus로 이벤트를 발행한다. 미설정 시 무시."""
        if not self._event_bus:
            return
        try:
            kwargs: dict[str, Any] = {
                "event_type": event_type,
                "data": data,
                "source": "SagaOrchestrator",
            }
            if correlation_id:
                kwargs["correlation_id"] = correlation_id
            if priority:
                kwargs["priority"] = priority
            self._event_bus.emit(**kwargs)
        except Exception as e:
            logger.warning(
                "[Saga] EventBus emit failed",
                extra={"event_type": event_type, "error": str(e)},
            )

    def _emit_final_event(self, instance: SagaInstance) -> None:
        """Saga 완료/실패 후 최종 이벤트를 발행한다."""
        event_map = {
            SagaStatus.COMPLETED: SagaEventType.SAGA_COMPLETED,
            SagaStatus.COMPENSATED: SagaEventType.SAGA_COMPENSATED,
            SagaStatus.COMPENSATION_FAILED: SagaEventType.SAGA_COMPENSATION_FAILED,
        }
        event_type = event_map.get(instance.status)
        if event_type:
            self._emit_event(
                event_type,
                data={
                    "saga_name": instance.saga_name,
                    "instance_id": instance.id,
                    "status": instance.status.value,
                },
                correlation_id=instance.correlation_id,
            )

        # Prometheus: saga_executions_total
        self._record_execution_metric(
            saga_name=instance.saga_name,
            status=instance.status.value,
        )

    def _validate_version_compatibility(
        self,
        instance: SagaInstance,
        definition: SagaDefinition,
    ) -> bool:
        """인스턴스의 definition_version과 현재 정의의 version이 호환되는지 확인한다."""
        if instance.definition_version != definition.version:
            instance.status = SagaStatus.SUSPENDED
            instance.error_message = (
                f"Definition version mismatch: " f"instance={instance.definition_version}, " f"current={definition.version}"
            )
            return False
        return True

    # =========================================================================
    # Prometheus 메트릭 계측
    # =========================================================================

    def _record_execution_metric(self, saga_name: str, status: str) -> None:
        """saga_executions_total Counter를 증가시킨다."""
        try:
            from selfhealing.services.metrics.definitions import (
                saga_executions_total,
            )

            saga_executions_total.labels(
                saga_name=saga_name,
                status=status,
            ).inc()
        except Exception:
            pass  # 메트릭 실패가 Saga 실행을 중단시키지 않는다

    def _record_step_duration(
        self,
        saga_name: str,
        step_name: str,
        phase: str,
        duration_seconds: float,
    ) -> None:
        """saga_step_duration_seconds Histogram에 관측값을 기록한다."""
        try:
            from selfhealing.services.metrics.definitions import (
                saga_step_duration_seconds,
            )

            saga_step_duration_seconds.labels(
                saga_name=saga_name,
                step_name=step_name,
                phase=phase,
            ).observe(duration_seconds)
        except Exception:
            pass

    def _record_compensation_metric(self, saga_name: str, step_name: str, status: str) -> None:
        """saga_compensation_steps_total Counter를 증가시킨다."""
        try:
            from selfhealing.services.metrics.definitions import (
                saga_compensation_steps_total,
            )

            saga_compensation_steps_total.labels(
                saga_name=saga_name,
                step_name=step_name,
                status=status,
            ).inc()
        except Exception:
            pass

    # =========================================================================
    # Redis Lua 원자적 상태 전환
    # =========================================================================

    def _transition_status(
        self,
        instance: SagaInstance,
        expected_status: SagaStatus,
        new_status: SagaStatus,
    ) -> bool:
        """Redis Lua SAGA_TRANSITION_SCRIPT로 원자적 상태 전환.

        Redis backend에서만 Lua CAS를 수행하고,
        InMemory backend에서는 Python 레벨 비교 후 직접 변경한다.

        Returns:
            True if transition succeeded, False if status mismatch.
        """
        backend = self._get_backend()

        if hasattr(backend, "_client"):
            # Redis: Lua CAS
            key = f"saga:instance:{instance.id}"
            try:
                result = backend._client.eval(
                    SAGA_TRANSITION_SCRIPT,
                    1,
                    backend._make_key(key),
                    expected_status.value,
                    new_status.value,
                    datetime.now(timezone.utc).isoformat(),
                )
                if result[0] == 0:
                    logger.warning(
                        "[Saga] Atomic status transition failed — status mismatch",
                        extra={
                            "instance_id": instance.id,
                            "expected": expected_status.value,
                            "actual": result[2] if len(result) > 2 else "unknown",
                        },
                    )
                    return False
                instance.status = new_status
                return True
            except Exception as e:
                logger.warning(
                    "[Saga] Lua transition script failed, falling back to Python",
                    extra={"instance_id": instance.id, "error": str(e)},
                )

        # InMemory / fallback: Python 레벨 전환
        if instance.status != expected_status:
            return False
        instance.status = new_status
        return True
