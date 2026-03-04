"""
빌트인 ActionPrimitive 등록.

시스템 기본 제공 프리미티브를 ActionPrimitiveRegistry에 등록한다.
6개 카테고리(config / assert / notify / recovery / emergency / wait)에 대응하는
7개 핸들러 + 6개 Pydantic 파라미터 스키마를 정의한다.

Reference:
    docs/self_healing/middleware_system/274_RUNBOOK_REGISTRY.md §5, §8.2
    docs/self_healing/middleware_system/278_RUNBOOK_SERVICE.md §8.1
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

import structlog
from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from selfhealing.services.runbook.runbook_registry import (
        ActionPrimitiveRegistry,
        RunbookStepContext,
    )

from selfhealing.services.saga.models import StepResult

logger = structlog.get_logger(__name__)


# =============================================================================
# Pydantic 파라미터 스키마 (274 §8.2)
# =============================================================================


class ConfigSetParams(BaseModel):
    """config.set action 파라미터."""

    key: str
    value: Any | None = None
    value_delta: int | float | None = None

    @model_validator(mode="after")
    def _validate_value_or_delta(self) -> ConfigSetParams:
        if self.value is None and self.value_delta is None:
            raise ValueError("value 또는 value_delta 중 하나는 필수")
        return self


class AssertMetricParams(BaseModel):
    """assert.metric action 파라미터."""

    metric_name: str
    operator: Literal["gt", "lt", "eq", "gte", "lte"]
    threshold: float


class NotifySendParams(BaseModel):
    """notify.send action 파라미터."""

    title: str
    message: str = ""
    priority: Literal["low", "medium", "high", "critical"] = "medium"


class RecoveryStartParams(BaseModel):
    """recovery.start action 파라미터."""

    namespace: str = "global"
    trigger_level: str


class EmergencyActivateParams(BaseModel):
    """emergency.activate action 파라미터."""

    level: int = Field(ge=1, le=5)
    reason: str


class WaitStabilizeParams(BaseModel):
    """wait.stabilize action 파라미터."""

    seconds: int = Field(ge=1, le=3600)
    assert_metric: str | None = None
    threshold: float | None = None


# =============================================================================
# 핸들러 함수 (ActionHandler 시그니처: RunbookStepContext → StepResult)
# =============================================================================

_OPERATOR_MAP: dict[str, Any] = {
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
    "eq": lambda a, b: a == b,
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
}


def _handle_config_set(ctx: RunbookStepContext) -> StepResult:
    """config.set — RuntimeConfigManager를 통해 설정값 변경.

    state-based 멱등성: 동일 key/value로 재실행해도 부작용 없음.
    """
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        params = ConfigSetParams(**ctx.params)
        manager = get_runtime_config_manager()

        parts = params.key.rsplit(".", 1)
        if len(parts) == 2:
            config_type, field_name = parts
        else:
            config_type, field_name = "general", params.key

        if params.value is not None:
            update_kwargs = {field_name: params.value}
        else:
            current = manager._get_config(config_type)
            current_value = current.get(field_name, 0)
            update_kwargs = {field_name: current_value + params.value_delta}

        result = manager._update_config(
            config_type,
            changed_by=ctx.initiated_by,
            reason=f"runbook:{ctx.runbook_id}",
            **update_kwargs,
        )

        logger.info(
            "primitive.config_set.success",
            runbook_id=ctx.runbook_id,
            key=params.key,
        )
        return StepResult.succeeded({"config_type": config_type, "updated": result})

    except Exception as exc:
        logger.error("primitive.config_set.failed", error=str(exc))
        return StepResult.failed(
            error=f"config.set failed: {exc}",
            error_code="CONFIG_SET_ERROR",
            retryable=True,
        )


def _handle_assert_metric(ctx: RunbookStepContext) -> StepResult:
    """assert.metric — 메트릭 현재값을 threshold와 비교하는 검증 게이트.

    검증 실패 시 StepResult.failed()를 반환하여 파이프라인을 중단한다.
    """
    try:
        params = AssertMetricParams(**ctx.params)

        metric_value = _query_metric(params.metric_name)
        if metric_value is None:
            return StepResult.failed(
                error=f"Metric '{params.metric_name}' not found",
                error_code="METRIC_NOT_FOUND",
                retryable=True,
            )

        op_fn = _OPERATOR_MAP[params.operator]
        passed = op_fn(metric_value, params.threshold)

        if not passed:
            return StepResult.failed(
                error=(
                    f"Assertion failed: {params.metric_name}={metric_value} "
                    f"{params.operator} {params.threshold}"
                ),
                error_code="ASSERT_METRIC_FAILED",
            )

        logger.info(
            "primitive.assert_metric.passed",
            metric=params.metric_name,
            value=metric_value,
        )
        return StepResult.succeeded(
            {
                "metric_name": params.metric_name,
                "value": metric_value,
                "passed": True,
            }
        )

    except Exception as exc:
        logger.error("primitive.assert_metric.failed", error=str(exc))
        return StepResult.failed(
            error=f"assert.metric failed: {exc}",
            error_code="ASSERT_METRIC_ERROR",
            retryable=True,
        )


def _handle_notify_send(ctx: RunbookStepContext) -> StepResult:
    """notify.send — UnifiedNotificationManager를 통해 알림 발송."""
    try:
        from selfhealing.services.unified_notification.models import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
        )
        from selfhealing.services.unified_notification.service import (
            UnifiedNotificationManager,
        )

        params = NotifySendParams(**ctx.params)

        priority_map = {
            "low": NotificationPriority.LOW,
            "medium": NotificationPriority.MEDIUM,
            "high": NotificationPriority.HIGH,
            "critical": NotificationPriority.CRITICAL,
        }

        payload = NotificationPayload(
            title=params.title,
            message=params.message,
            priority=priority_map[params.priority],
            category=NotificationCategory.OPERATIONS,
            source=f"runbook:{ctx.runbook_id}",
        )

        manager = UnifiedNotificationManager()
        result = manager.notify(payload)

        logger.info(
            "primitive.notify_send.success",
            runbook_id=ctx.runbook_id,
            title=params.title,
        )
        return StepResult.succeeded(
            {
                "notified": result.success,
                "suppressed": getattr(result, "suppressed", False),
            }
        )

    except Exception as exc:
        logger.error("primitive.notify_send.failed", error=str(exc))
        return StepResult.failed(
            error=f"notify.send failed: {exc}",
            error_code="NOTIFY_SEND_ERROR",
            retryable=True,
        )


def _handle_recovery_start(ctx: RunbookStepContext) -> StepResult:
    """recovery.start — RecoveryCoordinator.start_recovery() 호출."""
    try:
        from selfhealing.services.coordination.recovery_coordinator import (
            RecoveryCoordinator,
        )

        params = RecoveryStartParams(**ctx.params)

        coordinator = RecoveryCoordinator()
        session = coordinator.start_recovery(
            namespace=params.namespace,
            trigger_level=params.trigger_level,
            initiated_by=ctx.initiated_by,
        )

        logger.info(
            "primitive.recovery_start.success",
            runbook_id=ctx.runbook_id,
            namespace=params.namespace,
            trigger_level=params.trigger_level,
        )
        return StepResult.succeeded(
            {
                "session_id": str(getattr(session, "session_id", "")),
                "namespace": params.namespace,
            }
        )

    except ValueError as exc:
        logger.warning("primitive.recovery_start.rejected", error=str(exc))
        return StepResult.failed(
            error=f"recovery.start rejected: {exc}",
            error_code="RECOVERY_START_REJECTED",
        )
    except Exception as exc:
        logger.error("primitive.recovery_start.failed", error=str(exc))
        return StepResult.failed(
            error=f"recovery.start failed: {exc}",
            error_code="RECOVERY_START_ERROR",
            retryable=True,
        )


def _handle_emergency_activate(ctx: RunbookStepContext) -> StepResult:
    """emergency.activate — GracefulDegradationManager.activate_auto() 호출."""
    try:
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        params = EmergencyActivateParams(**ctx.params)

        level_map = {
            1: EmergencyLevel.LEVEL_1,
            2: EmergencyLevel.LEVEL_2,
            3: EmergencyLevel.LEVEL_3,
        }
        emergency_level = level_map.get(params.level)
        if emergency_level is None:
            return StepResult.failed(
                error=f"Unsupported emergency level: {params.level}",
                error_code="INVALID_EMERGENCY_LEVEL",
            )

        manager = GracefulDegradationManager()
        state = manager.activate_auto(
            level=emergency_level,
            reason=f"[runbook:{ctx.runbook_id}] {params.reason}",
        )

        logger.info(
            "primitive.emergency_activate.success",
            runbook_id=ctx.runbook_id,
            level=params.level,
        )
        return StepResult.succeeded(
            {
                "activated_level": state.level.value
                if hasattr(state, "level")
                else params.level,
            }
        )

    except Exception as exc:
        logger.error("primitive.emergency_activate.failed", error=str(exc))
        return StepResult.failed(
            error=f"emergency.activate failed: {exc}",
            error_code="EMERGENCY_ACTIVATE_ERROR",
            retryable=True,
        )


def _handle_emergency_deactivate(ctx: RunbookStepContext) -> StepResult:
    """emergency.deactivate — GracefulDegradationManager.deactivate() 호출."""
    try:
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()
        state = manager.deactivate(
            deactivated_by=ctx.initiated_by,
            reason=f"runbook:{ctx.runbook_id}",
        )

        logger.info(
            "primitive.emergency_deactivate.success",
            runbook_id=ctx.runbook_id,
        )
        return StepResult.succeeded(
            {
                "deactivated": True,
                "level": state.level.value if hasattr(state, "level") else 0,
            }
        )

    except Exception as exc:
        logger.error("primitive.emergency_deactivate.failed", error=str(exc))
        return StepResult.failed(
            error=f"emergency.deactivate failed: {exc}",
            error_code="EMERGENCY_DEACTIVATE_ERROR",
            retryable=True,
        )


def _handle_wait_stabilize(ctx: RunbookStepContext) -> StepResult:
    """wait.stabilize — 지정 시간 대기 후 선택적 메트릭 검증.

    assert_metric/threshold가 지정되면 대기 후 메트릭을 확인한다.
    """
    try:
        params = WaitStabilizeParams(**ctx.params)

        time.sleep(params.seconds)

        if params.assert_metric and params.threshold is not None:
            metric_value = _query_metric(params.assert_metric)
            if metric_value is None:
                return StepResult.failed(
                    error=f"Metric '{params.assert_metric}' not found after wait",
                    error_code="WAIT_METRIC_NOT_FOUND",
                    retryable=True,
                )
            if metric_value < params.threshold:
                return StepResult.failed(
                    error=(
                        f"Stabilization check failed: {params.assert_metric}="
                        f"{metric_value} < {params.threshold}"
                    ),
                    error_code="WAIT_STABILIZE_FAILED",
                )

        logger.info(
            "primitive.wait_stabilize.done",
            runbook_id=ctx.runbook_id,
            seconds=params.seconds,
        )
        return StepResult.succeeded({"waited_seconds": params.seconds})

    except Exception as exc:
        logger.error("primitive.wait_stabilize.failed", error=str(exc))
        return StepResult.failed(
            error=f"wait.stabilize failed: {exc}",
            error_code="WAIT_STABILIZE_ERROR",
            retryable=True,
        )


# =============================================================================
# 메트릭 조회 헬퍼
# =============================================================================


def _query_metric(metric_name: str) -> float | None:
    """Prometheus / RunbookMetricsProvider를 통해 메트릭 현재값 조회."""
    try:
        from selfhealing.factory import ProviderRegistry

        provider = ProviderRegistry.get("runbook_metrics_provider")
        if provider and hasattr(provider, "query"):
            return provider.query(metric_name)
    except Exception:
        pass

    try:
        from selfhealing.metrics import get_metric_value

        return get_metric_value(metric_name)
    except Exception:
        pass

    logger.warning("primitive.query_metric.unavailable", metric=metric_name)
    return None


# =============================================================================
# 빌트인 프리미티브 매핑 — ActionPrimitiveRegistry._register_builtins() 에서 사용
# =============================================================================

BUILTIN_PRIMITIVES: dict[str, tuple] = {
    "config.set": (_handle_config_set, ConfigSetParams),
    "assert.metric": (_handle_assert_metric, AssertMetricParams),
    "notify.send": (_handle_notify_send, NotifySendParams),
    "recovery.start": (_handle_recovery_start, RecoveryStartParams),
    "emergency.activate": (_handle_emergency_activate, EmergencyActivateParams),
    "emergency.deactivate": (_handle_emergency_deactivate, None),
    "wait.stabilize": (_handle_wait_stabilize, WaitStabilizeParams),
}


# =============================================================================
# 외부 초기화 진입점 (initialize_runbook_system → register_builtin_primitives)
# =============================================================================


def register_builtin_primitives(
    registry: ActionPrimitiveRegistry | None = None,
) -> None:
    """빌트인 ActionPrimitive를 레지스트리에 등록.

    Args:
        registry: 대상 ActionPrimitiveRegistry. None이면 로깅만 수행
                  (ActionPrimitiveRegistry.__init__에서 _register_builtins로
                  자체 등록하는 경우).
    """
    if registry is None:
        logger.debug("runbook_primitives.register_called", note="no_registry")
        return

    for action_name, (handler, schema) in BUILTIN_PRIMITIVES.items():
        registry.register(action_name, handler, schema)

    logger.info(
        "runbook_primitives.registered",
        count=len(BUILTIN_PRIMITIVES),
        actions=list(BUILTIN_PRIMITIVES.keys()),
    )
