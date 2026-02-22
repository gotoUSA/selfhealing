"""
Safety Guard - Main Guard Class.

Pre-flight safety checks for chaos experiments.
Implements error budget-based gating and system health verification.

Core Principle: "Never sacrifice production stability for testing."

Features:
- Error budget threshold checks (default: 20% minimum)
- System health verification
- Active incident detection
- Kill switch status check
- Deployment freeze detection
"""

from __future__ import annotations

import structlog
import threading
from datetime import datetime, timedelta
from typing import Any

from selfhealing.core.timezone import now

from .enums import ChaosBlockReason, SafetyStatus
from .models import SafetyCheckResult, SafetyConfig

logger = structlog.get_logger()


class SafetyGuard:
    """
    Pre-flight safety checks for chaos experiments.

    Implements a multi-layer safety check system:
    1. Error Budget Check - Primary gate
    2. System Health Check - Secondary gate
    3. Incident Detection - Tertiary gate
    4. Deployment Freeze - Policy gate
    5. Kill Switch - Override gate
    6. Cooldown - Rate limiting

    Usage:
        guard = get_safety_guard()

        result = guard.check(experiment_id="chaos-abc123")

        if not result.allowed:
            if result.block_reason == ChaosBlockReason.LOW_ERROR_BUDGET.value:
                notify("ChaosSkippedDueToLowBudget", result)
            return

        # Proceed with experiment
    """

    def __init__(self, config: SafetyConfig | None = None):
        """Initialize SafetyGuard."""
        self._config = config or SafetyConfig()
        self._lock = threading.RLock()

        # State tracking
        self._last_experiment_at: datetime | None = None
        self._manual_blocks: dict[str, str] = {}  # experiment_id -> reason
        self._global_block: bool = False
        self._global_block_reason: str = ""

    # =========================================================================
    # Configuration
    # =========================================================================

    def get_config(self) -> SafetyConfig:
        """Get current configuration."""
        return self._config

    def update_config(self, **kwargs) -> SafetyConfig:
        """
        Update configuration.

        Args:
            **kwargs: Config fields to update

        Returns:
            Updated config
        """
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
                    logger.info(
                        "safety_guard.updated_config",
                        key=key,
                        value=value,
                    )

            self._persist_config()
            return self._config

    def _persist_config(self) -> None:
        """Persist configuration to storage."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            manager.update_chaos_config(safety_guard_config=self._config.to_dict())
        except Exception as e:
            logger.warning(
                "safety_guard.persist_config",
                error=e,
            )

    def _load_config(self) -> None:
        """Load configuration from storage."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            config = manager.get_chaos_config()
            config_data = config.get("safety_guard_config", {})

            if config_data:
                for key, value in config_data.items():
                    if hasattr(self._config, key):
                        setattr(self._config, key, value)
        except Exception as e:
            logger.warning(
                "safety_guard.load_config",
                error=e,
            )

    # =========================================================================
    # Main Safety Check
    # =========================================================================

    def _check_global_block(self, result: SafetyCheckResult) -> bool:
        """Check global block. Returns True if blocked."""
        result.checks_performed.append("global_block")
        if self._global_block:
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = ChaosBlockReason.MANUAL_BLOCK.value
            result.block_message = self._global_block_reason
            result.checks_failed.append("global_block")
            return True
        result.checks_passed.append("global_block")
        return False

    def _check_kill_switch_status(self, result: SafetyCheckResult) -> bool:
        """Check kill switch status. Returns True if blocked."""
        result.checks_performed.append("kill_switch")
        kill_switch_result = self._check_kill_switch()
        if kill_switch_result:
            result.kill_switch_active = True
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = ChaosBlockReason.KILL_SWITCH_ACTIVE.value
            result.block_message = "Kill switch is active"
            result.checks_failed.append("kill_switch")
            return True
        result.checks_passed.append("kill_switch")
        return False

    def _check_error_budget_status(
        self, result: SafetyCheckResult, experiment_id: str
    ) -> bool:
        """Check error budget. Returns True if blocked."""
        result.checks_performed.append("error_budget")
        budget_result = self._check_error_budget()
        result.error_budget_remaining_percent = budget_result["remaining_percent"]
        result.error_budget_threshold = self._config.error_budget_min_percent

        if budget_result["remaining_percent"] < self._config.error_budget_min_percent:
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = ChaosBlockReason.LOW_ERROR_BUDGET.value
            result.block_message = (
                f"Error budget at {budget_result['remaining_percent']:.1f}% "
                f"(minimum: {self._config.error_budget_min_percent}%)"
            )
            result.checks_failed.append("error_budget")
            self._notify_low_budget(experiment_id, budget_result)
            return True

        if (
            budget_result["remaining_percent"]
            < self._config.error_budget_warning_percent
        ):
            result.warnings.append(
                f"Error budget low: {budget_result['remaining_percent']:.1f}%"
            )
            result.status = SafetyStatus.WARNING.value

        result.checks_passed.append("error_budget")
        return False

    def _check_system_health_status(self, result: SafetyCheckResult) -> bool:
        """Check system health. Returns True if blocked."""
        result.checks_performed.append("system_health")
        health_result = self._check_system_health()
        result.system_healthy = health_result["healthy"]

        if not health_result["healthy"]:
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = ChaosBlockReason.UNHEALTHY_SYSTEM.value
            result.block_message = health_result.get("message", "System unhealthy")
            result.checks_failed.append("system_health")
            return True
        result.checks_passed.append("system_health")
        return False

    def _check_active_incidents_status(self, result: SafetyCheckResult) -> bool:
        """Check active incidents. Returns True if blocked."""
        result.checks_performed.append("active_incidents")
        incident_result = self._check_active_incidents()
        result.active_incidents = incident_result["count"]

        if incident_result["count"] > 0:
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = ChaosBlockReason.ACTIVE_INCIDENT.value
            result.block_message = f"{incident_result['count']} active incident(s)"
            result.checks_failed.append("active_incidents")
            return True
        result.checks_passed.append("active_incidents")
        return False

    def _check_deployment_freeze_status(self, result: SafetyCheckResult) -> bool:
        """Check deployment freeze. Returns True if blocked."""
        result.checks_performed.append("deployment_freeze")
        freeze_result = self._check_deployment_freeze()
        result.deployment_freeze_active = freeze_result["active"]

        if freeze_result["active"]:
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = ChaosBlockReason.DEPLOYMENT_FREEZE.value
            result.block_message = "Deployment freeze is active"
            result.checks_failed.append("deployment_freeze")
            return True
        result.checks_passed.append("deployment_freeze")
        return False

    def _check_freeze_mode_status(self, result: SafetyCheckResult) -> bool:
        """
        Check CB Freeze Mode status. Returns True if blocked.

        CB Freeze Mode가 활성화되면 모든 카오스 실험을 차단합니다.
        Freeze Mode는 LOCKDOWN 상태에서 CB 상태를 동결하여
        시스템 안정성을 보호합니다.
        """
        result.checks_performed.append("freeze_mode")

        try:
            from selfhealing.services.circuit_breaker.freeze_mode import (
                FreezeModeManager,
            )

            manager = FreezeModeManager()
            is_active = manager.is_active()
            result.freeze_mode_active = is_active

            if is_active:
                state = manager.get_state()
                result.status = SafetyStatus.BLOCKED.value
                result.allowed = False
                result.block_reason = ChaosBlockReason.CB_FREEZE_MODE_ACTIVE.value
                result.block_message = f"CB Freeze Mode active: {state.reason or 'System stability protection'}"
                result.checks_failed.append("freeze_mode")
                logger.warning(
                    f"[SafetyGuard] CB Freeze Mode active, blocking chaos experiment. "
                    f"Reason: {state.reason}"
                )
                return True

            result.checks_passed.append("freeze_mode")
            return False

        except ImportError:
            logger.debug(
                "[SafetyGuard] FreezeModeManager not available, skipping check"
            )
            result.checks_passed.append("freeze_mode")
            return False
        except Exception as e:
            logger.warning(
                "safety_guard.freeze_mode_check_failed",
                error=e,
            )
            if self._config.fail_safe_on_error:
                result.freeze_mode_active = True
                result.status = SafetyStatus.BLOCKED.value
                result.allowed = False
                result.block_reason = ChaosBlockReason.CB_FREEZE_MODE_ACTIVE.value
                result.block_message = f"Freeze mode check failed (fail-safe): {e}"
                result.checks_failed.append("freeze_mode")
                return True
            result.checks_passed.append("freeze_mode")
            return False

    def _check_cooldown_status(self, result: SafetyCheckResult) -> None:
        """Check cooldown status and add warnings if needed."""
        result.checks_performed.append("cooldown")
        cooldown_result = self._check_cooldown()
        result.last_experiment_at = cooldown_result.get("last_experiment_at", "")
        result.cooldown_remaining_minutes = cooldown_result.get("remaining_minutes", 0)

        if cooldown_result.get("in_cooldown", False):
            result.warnings.append(
                f"Cooldown active: {cooldown_result['remaining_minutes']} minutes remaining"
            )
            if result.status == SafetyStatus.SAFE.value:
                result.status = SafetyStatus.WARNING.value

        result.checks_passed.append("cooldown")

    def _run_core_checks(self, result: SafetyCheckResult, experiment_id: str) -> bool:
        """Run core safety checks (always required). Returns True if blocked."""
        # 1. Check global block
        if self._check_global_block(result):
            return True

        # 2. Check kill switch
        if self._check_kill_switch_status(result):
            return True

        # 3. Check emergency mode (LEVEL_2+에서 차단)
        if self._check_emergency_mode_status(result):
            return True

        # 4. Check panic threshold
        if self._check_panic_threshold_status(result):
            return True

        # 5. Check chaos budget
        if self._check_chaos_budget_status(result):
            return True

        # 6. Check CB Freeze Mode
        if self._config.require_no_freeze_mode:
            if self._check_freeze_mode_status(result):
                return True

        # 7. Check error budget (CRITICAL)
        if self._check_error_budget_status(result, experiment_id):
            return True

        return False

    def _run_optional_checks(self, result: SafetyCheckResult) -> bool:
        """Run optional checks (skippable with force). Returns True if blocked."""
        # 4. Check system health
        if self._config.require_healthy_system:
            if self._check_system_health_status(result):
                return True

        # 5. Check active incidents
        if self._config.require_no_active_incidents:
            if self._check_active_incidents_status(result):
                return True

        # 6. Check deployment freeze
        if self._config.require_no_deployment_freeze:
            if self._check_deployment_freeze_status(result):
                return True

        return False

    def _log_check_result(self, result: SafetyCheckResult, experiment_id: str) -> None:
        """Log the result of safety checks."""
        if result.status == SafetyStatus.SAFE.value:
            logger.info(
                "safety_guard.all_checks_passed",
                experiment_id=experiment_id,
            )
        else:
            logger.warning(
                f"[SafetyGuard] Checks passed with warnings for {experiment_id}: "
                f"{result.warnings}"
            )

    def _handle_check_error(self, e: Exception) -> SafetyCheckResult:
        """Handle errors during safety check."""
        logger.exception(
            "safety_guard.error_during_safety_check",
            error=e,
        )

        if self._config.fail_safe_on_error:
            return SafetyCheckResult(
                status=SafetyStatus.ERROR.value,
                allowed=False,
                block_reason="safety_check_error",
                block_message=f"Safety check failed: {e}",
            )
        # Fail-open (not recommended for production)
        return SafetyCheckResult(
            status=SafetyStatus.WARNING.value,
            allowed=True,
            warnings=[f"Safety check error (fail-open): {e}"],
        )

    def check(
        self,
        experiment_id: str = "",
        target_service: str = "",
        force: bool = False,
    ) -> SafetyCheckResult:
        """
        Perform comprehensive safety checks.

        Args:
            experiment_id: Experiment ID for tracking
            target_service: Target service name
            force: Skip non-critical checks (for testing)

        Returns:
            SafetyCheckResult with detailed check results
        """
        result = SafetyCheckResult(
            status=SafetyStatus.SAFE.value,
            allowed=True,
        )

        try:
            with self._lock:
                # Run core checks (always required)
                if self._run_core_checks(result, experiment_id):
                    return result

                # Run optional checks (skippable with force)
                if not force and self._run_optional_checks(result):
                    return result

                # Check cooldown (warning only)
                self._check_cooldown_status(result)

                # Log result
                self._log_check_result(result, experiment_id)
                return result

        except Exception as e:
            return self._handle_check_error(e)

    # =========================================================================
    # Individual Checks
    # =========================================================================

    def _check_error_budget(self) -> dict[str, Any]:
        """Check current error budget status."""
        try:
            from selfhealing.services.error_budget_service import (
                get_error_budget_service,
            )

            service = get_error_budget_service()
            status = service.get_status()

            return {
                "remaining_percent": status.get("remaining_percent", 100.0),
                "consumed_percent": 100.0 - status.get("remaining_percent", 100.0),
                "is_healthy": status.get("is_healthy", True),
            }
        except Exception as e:
            logger.warning(
                "safety_guard.check_error_budget",
                error=e,
            )
            # Fail-safe: assume budget is available
            return {
                "remaining_percent": 100.0,
                "consumed_percent": 0.0,
                "is_healthy": True,
            }

    def _check_kill_switch(self) -> bool:
        """Check if kill switch is active."""
        try:
            from selfhealing.services.system_control import get_system_control

            control = get_system_control()
            return not control.is_selfhealing_enabled()
        except Exception as e:
            logger.warning(
                "safety_guard.check_kill_switch",
                error=e,
            )
            return False

    def _check_system_health(self) -> dict[str, Any]:
        """Check overall system health."""
        try:
            from selfhealing.services.health_check import get_health_check_service

            service = get_health_check_service()
            status = service.check_health()

            return {
                "healthy": status.is_healthy,
                "message": status.message if hasattr(status, "message") else "",
            }
        except Exception as e:
            logger.warning(
                "safety_guard.check_system_health",
                error=e,
            )
            # Fail-safe: assume healthy
            return {"healthy": True, "message": ""}

    def _check_active_incidents(self) -> dict[str, Any]:
        """Check for active incidents."""
        try:
            from selfhealing.services.dlq_service import get_dlq_service

            service = get_dlq_service()
            # Consider DLQ items with status 'pending' as active incidents
            pending_count = service.get_pending_count()

            # Only block if there are significant pending items
            return {
                "count": pending_count if pending_count > 10 else 0,
                "items": [],
            }
        except Exception as e:
            logger.warning(
                "safety_guard.check_active_incidents",
                error=e,
            )
            return {"count": 0, "items": []}

    def _check_deployment_freeze(self) -> dict[str, Any]:
        """Check if deployment freeze is active."""
        try:
            from selfhealing.services.error_budget_service import (
                get_error_budget_service,
            )

            service = get_error_budget_service()
            verdict = service.get_deployment_verdict()

            freeze_active = verdict.get("status") in ("freeze_recommended", "warning")

            return {
                "active": freeze_active,
                "reason": verdict.get("reason", ""),
            }
        except Exception as e:
            logger.warning(
                "safety_guard.check_deployment_freeze",
                error=e,
            )
            return {"active": False, "reason": ""}

    def _check_emergency_mode(self) -> dict[str, Any]:
        """Check current emergency mode status."""
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager
            from selfhealing.services.emergency_mode.enums import EmergencyLevel

            manager = get_emergency_manager()
            level = manager.get_current_level()

            return {
                "active": level.value >= EmergencyLevel.LEVEL_2.value,
                "level": level.name,
                "level_value": level.value,
            }
        except Exception as e:
            logger.warning(
                "safety_guard.check_emergency_mode",
                error=e,
            )
            # Fail-open: 비상 모드 확인 실패 시 허용
            return {"active": False, "level": "UNKNOWN", "level_value": 0}

    def _check_emergency_mode_status(self, result: SafetyCheckResult) -> bool:
        """Check emergency mode status. Returns True if blocked."""
        result.checks_performed.append("emergency_mode")
        emergency_result = self._check_emergency_mode()
        result.emergency_mode_active = emergency_result["active"]
        result.emergency_level = emergency_result["level"]

        if emergency_result["active"]:
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = ChaosBlockReason.EMERGENCY_MODE_ACTIVE.value
            result.block_message = (
                f"Emergency mode {emergency_result['level']} is active: "
                f"chaos experiments blocked"
            )
            result.checks_failed.append("emergency_mode")

            # Audit 로그 기록 (리뷰 피드백 반영 - 26_IMPROVEMENT_PART1)
            self._log_emergency_block_audit(emergency_result)

            return True

        result.checks_passed.append("emergency_mode")
        return False

    def _log_emergency_block_audit(self, emergency_result: dict[str, Any]) -> None:
        """
        Emergency Mode 차단 시 Audit 로그 기록.

        리뷰 피드백: "왜 이때 카오스 실험이 안 돌았지?"라는 질문에
        시스템이 "비상 상황이라 내가 막았다"고 대답할 수 있도록 증적을 남김.

        Args:
            emergency_result: Emergency mode 체크 결과 (level, level_value 포함)
        """
        try:
            from selfhealing.services.audit_helpers import log_governance_blocked_audit

            log_governance_blocked_audit(
                action="chaos_experiment",
                block_reason="emergency_mode_active",
                details={
                    "current_emergency_level": emergency_result["level"],
                    "emergency_level_value": emergency_result["level_value"],
                    "blocked_by": "SafetyGuard._check_emergency_mode_status",
                },
            )
        except Exception as e:
            # Audit 실패는 실험 차단에 영향을 주지 않음 (non-critical)
            logger.debug(
                "safety_guard.audit_logging_failed_non",
                error=e,
            )

    # =========================================================================
    # Panic Threshold Check
    # =========================================================================

    def _check_panic_threshold(self) -> dict[str, Any]:
        """
        Panic Threshold 상태 확인.

        70% 이상의 Circuit Breaker가 OPEN 상태이면 시스템 전체 붕괴로 판단.

        Returns:
            Dict with:
                - triggered: Panic 발동 여부
                - open_rate: OPEN 비율 (%)
                - open_count: OPEN 상태 CB 수
                - total_count: 전체 CB 수
                - open_circuits: OPEN 상태인 서비스 목록
        """
        try:
            from selfhealing.services.circuit_breaker.panic_threshold import (
                PanicThresholdMonitor,
            )

            monitor = PanicThresholdMonitor()
            result = monitor.check_panic_threshold()

            return {
                "triggered": result.triggered,
                "open_rate": result.open_rate,
                "open_count": result.open_count,
                "total_count": result.total_count,
                "open_circuits": result.open_circuits,
            }
        except Exception as e:
            logger.warning(
                "safety_guard.check_panic_threshold",
                error=e,
            )
            # Fail-open: panic threshold 확인 실패 시 허용
            return {
                "triggered": False,
                "open_rate": 0.0,
                "open_count": 0,
                "total_count": 0,
                "open_circuits": [],
            }

    def _check_panic_threshold_status(self, result: SafetyCheckResult) -> bool:
        """
        Check panic threshold status. Returns True if blocked.

        Panic Threshold 발동 시 모든 카오스 실험 차단.
        50% 이상 OPEN이면 경고.
        """
        panic_result = self._check_panic_threshold()

        result.panic_threshold_triggered = panic_result["triggered"]
        result.panic_open_rate = panic_result["open_rate"]
        result.panic_open_circuits = panic_result["open_circuits"]

        # Panic 발동 상태: 차단
        if panic_result["triggered"]:
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = ChaosBlockReason.PANIC_THRESHOLD_TRIGGERED.value
            result.block_message = (
                f"PANIC: {panic_result['open_rate']:.1f}% CB OPEN "
                f"({panic_result['open_count']}/{panic_result['total_count']}) - "
                f"시스템 전체 불안정"
            )
            result.checks_failed.append("panic_threshold")

            # Audit 로그 기록
            self._log_panic_block_audit(panic_result)

            return True

        # 경고 수준 (50% 이상): 경고만, 차단하지 않음
        if panic_result["open_rate"] >= 50.0:
            result.warnings.append(
                f"Warning: {panic_result['open_rate']:.1f}% CB OPEN - "
                f"저위험 실험만 권장"
            )
            if result.status == SafetyStatus.SAFE.value:
                result.status = SafetyStatus.WARNING.value

        result.checks_passed.append("panic_threshold")
        return False

    def _log_panic_block_audit(self, panic_result: dict[str, Any]) -> None:
        """
        Panic Threshold 차단 시 Audit 로그 기록.

        Args:
            panic_result: Panic threshold 체크 결과
        """
        try:
            from selfhealing.services.audit_helpers import log_governance_blocked_audit

            log_governance_blocked_audit(
                action="chaos_experiment",
                block_reason="panic_threshold_triggered",
                details={
                    "open_rate": panic_result["open_rate"],
                    "open_count": panic_result["open_count"],
                    "total_count": panic_result["total_count"],
                    "open_circuits": panic_result["open_circuits"],
                    "blocked_by": "SafetyGuard._check_panic_threshold_status",
                },
            )
        except Exception as e:
            # Audit 실패는 실험 차단에 영향을 주지 않음 (non-critical)
            logger.debug(
                "safety_guard.audit_logging_failed_non",
                error=e,
            )

    # =========================================================================
    # Chaos Budget Check
    # =========================================================================

    def _check_chaos_budget_status(self, result: SafetyCheckResult) -> bool:
        """
        Check chaos budget status. Returns True if blocked.

        Args:
            result: SafetyCheckResult to update

        Returns:
            True if budget exceeded and experiment should be blocked
        """
        result.checks_performed.append("chaos_budget")

        try:
            from selfhealing.services.finops.service import FinOpsService

            finops = FinOpsService()
            budget_status = finops.get_chaos_budget_status()

            # Store budget info in result
            if not hasattr(result, "chaos_budget_usage_percent"):
                result.chaos_budget_usage_percent = 0.0

            if not budget_status.get("configured", False):
                # 예산 미설정 시 통과
                result.checks_passed.append("chaos_budget")
                return False

            usage_percent = budget_status.get("usage_percent", 0.0)
            result.chaos_budget_usage_percent = usage_percent

            # 100% 소진: 차단
            if budget_status.get("is_over_budget", False):
                result.status = SafetyStatus.BLOCKED.value
                result.allowed = False
                result.block_reason = ChaosBlockReason.CHAOS_BUDGET_EXCEEDED.value
                result.block_message = f"Chaos budget exhausted: {usage_percent:.1f}%"
                result.checks_failed.append("chaos_budget")

                # 알림 발송
                self._notify_chaos_budget_exceeded(budget_status)
                return True

            # 80% 이상: 경고
            alert_threshold = budget_status.get("alert_threshold", 0.8)
            if usage_percent >= alert_threshold * 100:
                result.warnings.append(f"Chaos budget usage high: {usage_percent:.1f}%")
                if result.status == SafetyStatus.SAFE.value:
                    result.status = SafetyStatus.WARNING.value

                # 알림 발송
                self._notify_chaos_budget_warning(budget_status)

            result.checks_passed.append("chaos_budget")
            return False

        except Exception as e:
            logger.warning(
                "safety_guard.check_chaos_budget",
                error=e,
            )
            result.checks_passed.append("chaos_budget")
            return False

    def _notify_chaos_budget_exceeded(self, budget_status: dict[str, Any]) -> None:
        """예산 초과 알림."""
        try:
            from selfhealing.adapters.alert import get_alert_adapter

            adapter = get_alert_adapter()
            if adapter:
                adapter.alert(
                    severity="critical",
                    title="ChaosBudgetExhausted",
                    message=(
                        f"Chaos experiment budget exhausted.\n\n"
                        f"Current spent: ${budget_status.get('current_spent', 'N/A')}\n"
                        f"Max budget: ${budget_status.get('max_budget', 'N/A')}\n"
                        f"Usage: {budget_status.get('usage_percent', 0):.1f}%\n\n"
                        "Increase budget or wait for reset to enable chaos experiments."
                    ),
                    tags=["chaos", "safety", "finops", "budget"],
                )
        except Exception as e:
            logger.warning(
                f"[SafetyGuard] Could not send budget exceeded notification: {e}"
            )

    def _notify_chaos_budget_warning(self, budget_status: dict[str, Any]) -> None:
        """예산 경고 알림 (80%+ 사용)."""
        try:
            from selfhealing.adapters.alert import get_alert_adapter

            adapter = get_alert_adapter()
            if adapter:
                adapter.alert(
                    severity="warning",
                    title="ChaosBudgetWarning",
                    message=(
                        f"Chaos experiment budget usage is high.\n\n"
                        f"Current spent: ${budget_status.get('current_spent', 'N/A')}\n"
                        f"Max budget: ${budget_status.get('max_budget', 'N/A')}\n"
                        f"Usage: {budget_status.get('usage_percent', 0):.1f}%"
                    ),
                    tags=["chaos", "safety", "finops", "budget"],
                )
        except Exception as e:
            logger.debug(
                f"[SafetyGuard] Could not send budget warning notification: {e}"
            )

    def _check_cooldown(self) -> dict[str, Any]:
        """Check if cooldown period is active."""
        if self._last_experiment_at is None:
            return {
                "in_cooldown": False,
                "remaining_minutes": 0,
                "last_experiment_at": "",
            }

        cooldown_end = self._last_experiment_at + timedelta(
            minutes=self._config.experiment_cooldown_minutes
        )

        current = now()
        if current < cooldown_end:
            remaining = (cooldown_end - current).total_seconds() / 60
            return {
                "in_cooldown": True,
                "remaining_minutes": int(remaining),
                "last_experiment_at": self._last_experiment_at.isoformat(),
            }

        return {
            "in_cooldown": False,
            "remaining_minutes": 0,
            "last_experiment_at": self._last_experiment_at.isoformat(),
        }

    # =========================================================================
    # Manual Controls
    # =========================================================================

    def block_globally(self, reason: str) -> None:
        """
        Block all chaos experiments globally.

        Args:
            reason: Reason for global block
        """
        with self._lock:
            self._global_block = True
            self._global_block_reason = reason
            logger.warning(
                "safety_guard.global_block_activated",
                reason=reason,
            )

    def unblock_globally(self) -> None:
        """Remove global block."""
        with self._lock:
            self._global_block = False
            self._global_block_reason = ""
            logger.info("safety_guard.global_block_removed")

    def is_globally_blocked(self) -> tuple[bool, str]:
        """Check if globally blocked."""
        return self._global_block, self._global_block_reason

    def record_experiment_completed(self) -> None:
        """Record that an experiment just completed (for cooldown tracking)."""
        with self._lock:
            self._last_experiment_at = now()

    # =========================================================================
    # Notifications
    # =========================================================================

    def _notify_low_budget(
        self, experiment_id: str, budget_result: dict[str, Any]
    ) -> None:
        """Send notification for low error budget blocking experiment."""
        try:
            from selfhealing.adapters.alert import get_alert_adapter

            adapter = get_alert_adapter()
            if adapter:
                adapter.alert(
                    severity="warning",
                    title="ChaosSkippedDueToLowBudget",
                    message=(
                        f"Chaos experiment {experiment_id} was automatically skipped.\n\n"
                        f"Error budget remaining: {budget_result['remaining_percent']:.1f}%\n"
                        f"Minimum required: {self._config.error_budget_min_percent}%\n\n"
                        "Increase error budget or adjust threshold to enable chaos experiments."
                    ),
                    tags=["chaos", "safety", "error_budget"],
                )
        except Exception as e:
            logger.warning(
                "safety_guard.send_notification",
                error=e,
            )
