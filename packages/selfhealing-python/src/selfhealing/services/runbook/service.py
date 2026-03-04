"""
RunbookService — Runbook 시스템의 단일 진입점.

273~277 모듈을 조합하여 전체 파이프라인을 오케스트레이션한다.

파이프라인:
1. PatternMatcher (273) — 이벤트/메트릭에서 패턴 감지
2. RunbookRegistry (274) — 패턴 ID로 Runbook 조회
3. RunbookApprovalGate (276) — 거버넌스 체크 + 리스크 기반 승인
4. RunbookExecutor (275) — Step 순차 실행 + 보상
5. RunbookPlaybackRecorder (277) — 감사 기록 + 학습 피드백

Reference:
    docs/self_healing/middleware_system/278_RUNBOOK_SERVICE.md
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from selfhealing.services.runbook.exceptions import (
    RunbookExecutionError,
    RunbookLockConflictError,
    RunbookNotFoundError,
)
from selfhealing.services.runbook.execution_models import (
    ApprovalDecisionType,
    RunbookExecutionContext,
    RunbookExecutionStatus,
)
from selfhealing.settings.runbook import get_runbook_settings

if TYPE_CHECKING:
    from selfhealing.services.event_bus.bus import SelfHealingEventBus
    from selfhealing.services.runbook.approval_gate import RunbookApprovalGate
    from selfhealing.services.runbook.executor import RunbookExecutor
    from selfhealing.services.runbook.pattern_matcher import PatternMatcher
    from selfhealing.services.runbook.recorder import RunbookPlaybackRecorder
    from selfhealing.services.runbook.runbook_registry import Runbook, RunbookRegistry

logger = structlog.get_logger(__name__)

MAX_CASCADE_DEPTH = 3
"""런북 → 런북 체이닝 최대 깊이. 의도적 에스컬레이션을 허용하되 무한 루프 방지."""

SEMAPHORE_KEY = "selfhealing:runbook:global_semaphore"
SEMAPHORE_TTL_SECONDS = 3600


class RunbookService:
    """Runbook 시스템의 단일 진입점.

    273~277 모듈을 조합하여 전체 파이프라인을 오케스트레이션한다.
    LearningService와 동일한 싱글톤 패턴.
    """

    _instance: RunbookService | None = None
    _lock = threading.Lock()

    def __new__(cls) -> RunbookService:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._pattern_matcher: PatternMatcher | None = None
        self._registry: RunbookRegistry | None = None
        self._approval_gate: RunbookApprovalGate | None = None
        self._executor: RunbookExecutor | None = None
        self._recorder: RunbookPlaybackRecorder | None = None
        self._event_bus: SelfHealingEventBus | None = None

        self._enabled = True
        self._initialized = True

        logger.info("runbook_service.initialized")

    # =========================================================================
    # Lazy 의존성 획득
    # =========================================================================

    def _get_pattern_matcher(self) -> PatternMatcher:
        if self._pattern_matcher is None:
            from selfhealing.services.runbook.pattern_matcher import PatternMatcher

            registry = self._get_registry()
            self._pattern_matcher = PatternMatcher(registry=registry)
        return self._pattern_matcher

    def _get_registry(self) -> RunbookRegistry:
        if self._registry is None:
            from selfhealing.services.runbook.runbook_registry import RunbookRegistry

            self._registry = RunbookRegistry()
        return self._registry

    def _get_approval_gate(self) -> RunbookApprovalGate:
        if self._approval_gate is None:
            from selfhealing.services.runbook.approval_gate import (
                get_runbook_approval_gate,
            )

            self._approval_gate = get_runbook_approval_gate()
        return self._approval_gate

    def _get_executor(self) -> RunbookExecutor:
        if self._executor is None:
            from selfhealing.services.runbook.executor import RunbookExecutor

            self._executor = RunbookExecutor()
        return self._executor

    def _get_recorder(self) -> RunbookPlaybackRecorder:
        if self._recorder is None:
            from selfhealing.services.runbook.recorder import RunbookPlaybackRecorder

            self._recorder = RunbookPlaybackRecorder()
        return self._recorder

    def _get_event_bus(self) -> SelfHealingEventBus:
        if self._event_bus is None:
            from selfhealing.services.event_bus import get_event_bus

            self._event_bus = get_event_bus()
        return self._event_bus

    # =========================================================================
    # 자동 실행 파이프라인 (EventBus 이벤트 수신)
    # =========================================================================

    def handle_event(self, event: Any) -> RunbookExecutionContext | None:
        """이벤트 수신 → 패턴 매칭 → Runbook 실행 전체 파이프라인.

        EventBus 핸들러로 등록되어 자동 호출됨.

        Returns:
            RunbookExecutionContext (실행 완료 시)
            None (패턴 미매칭 또는 Runbook 없음)
        """
        if not self._enabled:
            return None

        # 방어 1: 런북 실행으로 인해 파생된 이벤트 필터링
        if hasattr(event, "source") and event.source == "runbook_executor":
            logger.debug(
                "runbook_service.skip_executor_event",
                event_type=getattr(event, "event_type", None),
                source=event.source,
            )
            return None

        # 방어 2: 재귀 깊이 제한
        event_data = getattr(event, "data", {}) or {}
        cascade_depth = event_data.get("trigger_context", {}).get("cascade_depth", 0)
        if cascade_depth >= MAX_CASCADE_DEPTH:
            logger.warning(
                "runbook_service.max_cascade_depth_exceeded",
                event_type=getattr(event, "event_type", None),
                cascade_depth=cascade_depth,
            )
            return None

        # 1. 패턴 매칭 (273)
        matcher = self._get_pattern_matcher()
        registry = self._get_registry()

        triggered_event = (
            event.event_type.value
            if hasattr(event, "event_type") and hasattr(event.event_type, "value")
            else str(getattr(event, "event_type", ""))
        )

        event_context: dict[str, Any] = {}
        if isinstance(event_data, dict):
            event_context = dict(event_data)
        if hasattr(event, "source"):
            event_context["_source"] = event.source

        matched = matcher.evaluate_all(
            metrics={},
            triggered_event=triggered_event,
            event_context=event_context,
        )

        if not matched:
            return None

        # 2. 최고 confidence 런북 선택
        selection = matcher.select_runbook(matched)
        if selection is None:
            return None

        runbook = registry.get(selection.selected.runbook_id)
        if runbook is None:
            return None

        # trigger_event에 cascade_depth 전파
        trigger_event = selection.selected.build_trigger_event(event_data)
        trigger_event.setdefault("trigger_context", {})["cascade_depth"] = (
            cascade_depth + 1
        )

        # 3. 파이프라인 실행
        return self._execute_pipeline(
            runbook=runbook,
            trigger_event=trigger_event,
            namespace=event_data.get("namespace", "global"),
        )

    # =========================================================================
    # 수동 실행 API
    # =========================================================================

    def execute_runbook(
        self,
        runbook_id: str,
        namespace: str = "global",
        trigger_event: dict[str, Any] | None = None,
    ) -> RunbookExecutionContext:
        """Runbook을 수동으로 실행.

        API 또는 CLI에서 호출.

        Raises:
            RunbookNotFoundError: Runbook ID가 레지스트리에 없음
        """
        registry = self._get_registry()
        runbook = registry.get(runbook_id)
        if runbook is None:
            raise RunbookNotFoundError(f"Runbook not found: {runbook_id}")

        return self._execute_pipeline(
            runbook=runbook,
            trigger_event=trigger_event or {"manual": True, "runbook_id": runbook_id},
            namespace=namespace,
        )

    # =========================================================================
    # 파이프라인 실행
    # =========================================================================

    def _execute_pipeline(
        self,
        runbook: Runbook,
        trigger_event: dict[str, Any],
        namespace: str,
    ) -> RunbookExecutionContext:
        """승인 → 실행 → 기록 파이프라인.

        경로 A (LOW 리스크): 승인 즉시 → 실행 → 기록 (동기)
        경로 B (MEDIUM/HIGH): 승인 대기 → Suspend → return
            → 승인 후 _trigger_resume() → resume_pipeline() → 실행 → 기록 (비동기)
        """
        # 0. 글로벌 동시 실행 제한 확인
        if not self._acquire_global_semaphore():
            logger.warning(
                "runbook_service.global_concurrency_limit_reached",
                runbook_id=runbook.id,
                namespace=namespace,
            )
            from selfhealing.services.dlq import store_to_dlq

            settings = get_runbook_settings()
            store_to_dlq(
                domain="runbook",
                failure_type="concurrency_limit",
                entity_type="runbook_execution",
                entity_id=runbook.id,
                error_message=(
                    f"Global concurrency limit reached "
                    f"(max={settings.max_concurrent_runbooks})"
                ),
                snapshot_data={"runbook_id": runbook.id, "namespace": namespace},
                recommended_action="retry_after_current_executions_complete",
            )
            ctx = RunbookExecutionContext(
                execution_id=f"runbook-{uuid4()}",
                runbook_id=runbook.id,
                namespace=namespace,
                trigger_event=trigger_event,
                runbook_version=runbook.version,
            )
            ctx.status = RunbookExecutionStatus.FAILED
            ctx.abort_reason = "Global concurrency limit reached"
            return ctx

        ctx = None
        try:
            approval_gate = self._get_approval_gate()
            executor = self._get_executor()

            execution_id = f"runbook-{uuid4()}"
            ctx = RunbookExecutionContext(
                execution_id=execution_id,
                runbook_id=runbook.id,
                namespace=namespace,
                trigger_event=trigger_event,
                runbook_version=runbook.version,
            )

            # 1. 승인 평가 (276)
            decision = approval_gate.evaluate_approval(runbook, ctx)

            if decision.decision_type == ApprovalDecisionType.BLOCKED:
                ctx.status = RunbookExecutionStatus.FAILED
                ctx.abort_reason = decision.block_message
                executor._save_context(ctx)
                return ctx

            if decision.decision_type == ApprovalDecisionType.WAITING:
                # 경로 B: Suspend — 컨텍스트 저장 후 즉시 반환
                ctx.status = RunbookExecutionStatus.WAITING_APPROVAL
                executor._save_context(ctx)
                return ctx

            # 경로 A: AUTO_APPROVED — 즉시 실행
            return self._run_and_record(runbook, trigger_event, namespace)

        except RunbookLockConflictError:
            # RedisEventBus 환경에서 다수 워커 중복 수신 시 정상 동작
            logger.debug(
                "runbook_service.duplicate_event_filtered_by_lock",
                runbook_id=runbook.id,
                namespace=namespace,
            )
            return RunbookExecutionContext(
                execution_id="",
                runbook_id=runbook.id,
                namespace=namespace,
                trigger_event=trigger_event,
                runbook_version=runbook.version,
                status=RunbookExecutionStatus.CANCELLED,
                abort_reason="Duplicate event — another worker acquired the lock",
            )
        finally:
            # WAITING_APPROVAL로 Suspend된 경우에는 해제하지 않음
            if (
                ctx is not None
                and ctx.status != RunbookExecutionStatus.WAITING_APPROVAL
            ):
                self._release_global_semaphore()

    # =========================================================================
    # 승인 완료 후 파이프라인 재개
    # =========================================================================

    def resume_pipeline(self, execution_id: str) -> RunbookExecutionContext:
        """승인 완료 후 파이프라인 재개.

        호출 경로: approve_runbook() → _trigger_resume() → resume_runbook_task
                 → RunbookService.resume_pipeline()

        RunbookExecutor.resume_execution()의 3단계 방어가 적용된다:
        1. Stale Context 거부
        2. Runbook 버전 호환성 검증
        3. 무한 재개 방지
        """
        executor = self._get_executor()
        recorder = self._get_recorder()

        try:
            ctx = executor.resume_execution(execution_id)

            # 기록 (277)
            runbook = self._get_registry().get(ctx.runbook_id)
            compensation = getattr(ctx, "_compensation_summary", None)
            if runbook is not None:
                recorder.record(ctx, runbook, compensation)

            logger.info(
                "runbook_service.resume_completed",
                execution_id=execution_id,
                status=ctx.status.value,
            )
            return ctx
        finally:
            self._release_global_semaphore()

    # =========================================================================
    # 내부 헬퍼
    # =========================================================================

    def _run_and_record(
        self,
        runbook: Runbook,
        trigger_event: dict[str, Any],
        namespace: str,
    ) -> RunbookExecutionContext:
        """AUTO_APPROVED 실행 + 기록."""
        executor = self._get_executor()
        recorder = self._get_recorder()

        ctx = executor.execute_runbook(runbook, trigger_event, namespace)

        compensation = getattr(ctx, "_compensation_summary", None)
        recorder.record(ctx, runbook, compensation)

        logger.info(
            "runbook_service.pipeline_completed",
            runbook_id=runbook.id,
            status=ctx.status.value,
            namespace=namespace,
        )

        return ctx

    # =========================================================================
    # 우아한 취소 API
    # =========================================================================

    def cancel_runbook_execution(
        self,
        execution_id: str,
        cancelled_by: str,
        reason: str = "",
    ) -> RunbookExecutionContext:
        """실행 중인 런북을 우아하게 취소.

        현재 Step까지만 완료 후 역순 보상(compensate)을 실행하고 CANCELLED로 전환.

        허용 상태:
        - PENDING → 즉시 CANCELLED
        - WAITING_APPROVAL → 즉시 CANCELLED (보상 불필요)
        - EXECUTING → 취소 시그널 → 현재 Step 완료 → 보상 → CANCELLED
        """
        executor = self._get_executor()
        ctx = executor._load_context(execution_id)
        if ctx is None:
            raise RunbookExecutionError(f"Execution not found: {execution_id}")

        cancellable = {
            RunbookExecutionStatus.PENDING,
            RunbookExecutionStatus.WAITING_APPROVAL,
            RunbookExecutionStatus.EXECUTING,
        }
        if ctx.status not in cancellable:
            raise RunbookExecutionError(
                f"Cannot cancel execution in '{ctx.status.value}' state"
            )

        # WAITING_APPROVAL / PENDING → 즉시 취소
        if ctx.status in {
            RunbookExecutionStatus.PENDING,
            RunbookExecutionStatus.WAITING_APPROVAL,
        }:
            ctx.status = RunbookExecutionStatus.CANCELLED
            ctx.abort_reason = f"Cancelled by {cancelled_by}: {reason}"
            executor._save_context(ctx)

            # WAITING_APPROVAL에서 취소 시 세마포어 해제
            if ctx.status == RunbookExecutionStatus.CANCELLED:
                self._release_global_semaphore()

            return ctx

        # EXECUTING → 취소 플래그 설정
        ctx.variables["__cancel_requested"] = True
        ctx.variables["__cancelled_by"] = cancelled_by
        ctx.variables["__cancel_reason"] = reason
        executor._save_context(ctx)

        logger.info(
            "runbook_service.cancel_requested",
            execution_id=execution_id,
            cancelled_by=cancelled_by,
        )

        return ctx

    # =========================================================================
    # EventBus 구독
    # =========================================================================

    def register_subscriptions(self) -> None:
        """EventBus에 Runbook 관련 핸들러를 등록.

        Celery Worker 환경에서만 구독하도록 환경변수로 제어 가능.
        """
        if (
            os.environ.get("SELFHEALING_RUNBOOK_SUBSCRIBE_EVENTS", "true").lower()
            != "true"
        ):
            logger.info("runbook_service.event_subscription_disabled_by_env")
            return

        try:
            from selfhealing.services.event_bus.bus import EventPriority, EventType

            event_bus = self._get_event_bus()

            target_events = [
                EventType.EMERGENCY_LEVEL_CHANGED,
                EventType.ERROR_BUDGET_CRITICAL,
                EventType.CIRCUIT_BREAKER_OPENED,
            ]

            # 존재하는 EventType만 구독
            optional_events = ["HEALTH_CHECK_FAILED", "SLA_VIOLATION_DETECTED"]
            for name in optional_events:
                if hasattr(EventType, name):
                    target_events.append(getattr(EventType, name))

            for event_type in target_events:
                event_bus.subscribe(
                    event_type=event_type,
                    handler=self._on_event_received,
                    priority=EventPriority.LOW,
                )

            logger.info(
                "runbook_service.subscriptions_registered",
                count=len(target_events),
            )
        except (ImportError, Exception) as e:
            logger.warning(
                "runbook_service.subscription_failed",
                error=str(e),
            )

    def _on_event_received(self, event: Any) -> None:
        """EventBus 이벤트 수신 핸들러.

        동기 실행 시 EventBus를 차단하므로,
        Celery 태스크로 비동기 위임한다.
        """
        settings = get_runbook_settings()
        async_execution = (
            os.environ.get("SELFHEALING_RUNBOOK_ASYNC_EXECUTION", "true").lower()
            == "true"
        )

        if async_execution:
            try:
                from selfhealing.adapters.celery.tasks.runbook import (
                    execute_runbook_for_event,
                )

                event_dict = event.to_dict() if hasattr(event, "to_dict") else {}
                execute_runbook_for_event.delay(event_dict)
            except ImportError:
                self.handle_event(event)
            except Exception as e:
                logger.warning("runbook_service.event_dispatch_failed", error=str(e))
        else:
            self.handle_event(event)

    # =========================================================================
    # 글로벌 동시 실행 세마포어
    # =========================================================================

    def _acquire_global_semaphore(self) -> bool:
        """글로벌 동시 실행 제한 확인.

        Redis INCR 기반 카운터.

        Returns:
            True if 실행 가능, False if max_concurrent_runbooks 초과
        """
        try:
            from selfhealing.core.state_backend import get_state_backend

            settings = get_runbook_settings()
            max_concurrent = settings.max_concurrent_runbooks

            backend = get_state_backend()
            redis_client = getattr(backend, "_client", None)
            if redis_client is None:
                return True

            current = redis_client.incr(SEMAPHORE_KEY)
            if current == 1:
                redis_client.expire(SEMAPHORE_KEY, SEMAPHORE_TTL_SECONDS)

            if current > max_concurrent:
                redis_client.decr(SEMAPHORE_KEY)
                return False

            return True
        except Exception:
            # Redis 불가 시 제한 없이 통과 (Fail-Open)
            return True

    def _release_global_semaphore(self) -> None:
        """글로벌 세마포어 해제."""
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            redis_client = getattr(backend, "_client", None)
            if redis_client is None:
                return

            redis_client.decr(SEMAPHORE_KEY)
        except Exception:
            pass

    # =========================================================================
    # 싱글톤 리셋 (테스트용)
    # =========================================================================

    @classmethod
    def reset(cls) -> None:
        """싱글톤 인스턴스 리셋 (테스트 전용)."""
        with cls._lock:
            cls._instance = None


def get_runbook_service() -> RunbookService:
    """Get or create RunbookService singleton."""
    return RunbookService()


def reset_runbook_service() -> None:
    """Reset RunbookService singleton (for testing)."""
    RunbookService.reset()


def initialize_runbook_system() -> RunbookService:
    """Runbook 시스템 초기화.

    Django AppConfig.ready() 또는 Celery worker_init에서 호출.
    """
    service = RunbookService()

    # 빌트인 Runbook/프리미티브 등록 (존재할 경우)
    try:
        from selfhealing.services.runbook.builtins import register_builtin_runbooks

        register_builtin_runbooks()
    except ImportError:
        logger.debug("runbook_system.builtins_not_available")

    try:
        from selfhealing.services.runbook.primitives import (
            register_builtin_primitives,
        )

        register_builtin_primitives()
    except ImportError:
        logger.debug("runbook_system.primitives_not_available")

    # EventBus 구독 등록
    service.register_subscriptions()

    # ProviderRegistry 등록
    from selfhealing.factory import ProviderRegistry

    ProviderRegistry.register("runbook_service", service)

    logger.info("runbook_system.initialized")
    return service
