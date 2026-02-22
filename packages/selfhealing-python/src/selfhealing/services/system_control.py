"""
System Control Service

Self-healing 시스템의 글로벌 킬 스위치 및 시스템 상태 관리.

Features:
- Thread-safe 상태 관리
- Pluggable backends (File, Redis, Memory)
- 서버 재시작 시 자동 상태 복구
- 다중 서버 간 상태 공유 (Redis 백엔드 사용 시)

Configuration:
    # Django settings.py
    SELFHEALING_STATE_BACKEND = "redis"  # or "file" (default)
    SELFHEALING_REDIS_URL = "redis://localhost:6379/0"

    # Or environment variables
    SELFHEALING_STATE_BACKEND=redis
    SELFHEALING_REDIS_URL=redis://localhost:6379/0
"""

from __future__ import annotations

import structlog
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from selfhealing.core.state_backend import StateBackend, get_state_backend

logger = structlog.get_logger()


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SystemState:
    """Self-healing system state."""

    enabled: bool = True
    dry_run: bool = False  # Dry run mode: observe only, no actual actions
    disabled_at: str | None = None
    disabled_by: str | None = None
    disabled_reason: str | None = None
    enabled_at: str | None = None
    enabled_by: str | None = None
    dry_run_enabled_at: str | None = None
    dry_run_enabled_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemState:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# State key for backend storage
STATE_KEY = "system_control"


# =============================================================================
# System Control Manager
# =============================================================================


class SystemControlManager:
    """
    Manages global self-healing system state with pluggable backend.

    Features:
    - Thread-safe state management
    - Pluggable backends (File, Redis, Memory)
    - Automatic state recovery on restart
    - Shared state across servers (with Redis backend)

    Usage:
        manager = SystemControlManager()

        # Check if system is enabled
        if manager.is_enabled():
            do_healing()

        # Disable system (Kill Switch)
        manager.disable(reason="Emergency maintenance", actor="admin")

        # Re-enable system
        manager.enable(actor="admin")

    Configuration:
        # Django settings.py
        SELFHEALING_STATE_BACKEND = "redis"  # or "file"
        SELFHEALING_REDIS_URL = "redis://localhost:6379/0"
    """

    _instance: SystemControlManager | None = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._state_lock = threading.Lock()
        self._backend: StateBackend = get_state_backend()
        self._load_state()
        self._initialized = True

    def _load_state(self) -> None:
        """Load state from backend."""
        try:
            data = self._backend.get(STATE_KEY)
            if data:
                self._cached_state = SystemState.from_dict(data)
                logger.info(
                    f"[SystemControl] Loaded state: enabled={self._cached_state.enabled}, "
                    f"backend={type(self._backend).__name__}"
                )
            else:
                self._cached_state = SystemState()
                logger.info("system_control.no_existing_state_using")
        except Exception as e:
            logger.warning(
                "system_control.load_state",
                error=e,
            )
            self._cached_state = SystemState()

    def _save_state(self) -> None:
        """Save state to backend."""
        try:
            self._backend.set(STATE_KEY, self._cached_state.to_dict())
            logger.debug("system_control.state_saved")
        except Exception as e:
            logger.error(
                "system_control.failed_save_state",
                error=e,
            )

    def _refresh_state(self) -> SystemState:
        """Refresh state from backend (for multi-server sync)."""
        data = self._backend.get(STATE_KEY)
        if data:
            self._cached_state = SystemState.from_dict(data)
        return self._cached_state

    def _log_audit(
        self,
        action: str,
        actor: str,
        old_state: dict,
        new_state: dict,
        reason: str,
    ) -> None:
        """
        시스템 제어 변경을 Audit 로그에 기록.

        Fail-Open 원칙: Audit 실패가 시스템 제어 로직을 중단시키지 않음.
        """
        try:
            from selfhealing.services.audit_helpers import log_system_control_audit

            log_system_control_audit(
                action=action,
                actor=actor,
                old_state=old_state,
                new_state=new_state,
                reason=reason,
            )
        except Exception as e:
            # Fail-Open: Audit 실패가 시스템 제어를 중단시키지 않음
            logger.debug(
                "system_control.audit_logging_failed_ignored",
                error=e,
            )

    def is_enabled(self) -> bool:
        """
        Check if self-healing system is enabled.

        Note: For Redis backend, this reads from cache for performance.
        Use get_state() for fresh read from backend.
        """
        with self._state_lock:
            return self._cached_state.enabled

    def get_state(self, refresh: bool = True) -> SystemState:
        """
        Get current system state.

        Args:
            refresh: If True, refresh from backend (for multi-server sync)
        """
        with self._state_lock:
            if refresh:
                self._refresh_state()
            return SystemState.from_dict(self._cached_state.to_dict())

    def enable(self, actor: str = "system", reason: str = "") -> SystemState:
        """Enable self-healing system."""
        with self._state_lock:
            # Refresh first for multi-server consistency
            self._refresh_state()
            old_state = self._cached_state.to_dict()
            was_enabled = self._cached_state.enabled

            self._cached_state.enabled = True
            self._cached_state.enabled_at = datetime.now(timezone.utc).isoformat()
            self._cached_state.enabled_by = actor
            self._save_state()

            new_state = self._cached_state.to_dict()

            if not was_enabled:
                logger.info(
                    f"[SystemControl] System ENABLED by {actor}. Reason: {reason or 'N/A'}"
                )
                # Audit 기록
                self._log_audit("enable", actor, old_state, new_state, reason)

            return SystemState.from_dict(self._cached_state.to_dict())

    def disable(self, actor: str = "system", reason: str = "") -> SystemState:
        """
        Disable self-healing system (Kill Switch).

        This immediately stops all self-healing operations.
        State is persisted and shared across servers (with Redis backend).
        """
        with self._state_lock:
            # Refresh first for multi-server consistency
            self._refresh_state()
            old_state = self._cached_state.to_dict()
            was_enabled = self._cached_state.enabled

            self._cached_state.enabled = False
            self._cached_state.disabled_at = datetime.now(timezone.utc).isoformat()
            self._cached_state.disabled_by = actor
            self._cached_state.disabled_reason = reason
            self._save_state()

            new_state = self._cached_state.to_dict()

            if was_enabled:
                logger.warning(
                    f"[SystemControl] System DISABLED (Kill Switch) by {actor}. "
                    f"Reason: {reason or 'N/A'}"
                )
                # Audit 기록
                self._log_audit("disable", actor, old_state, new_state, reason)

            return SystemState.from_dict(self._cached_state.to_dict())

    def enable_dry_run(self, actor: str = "system") -> SystemState:
        """
        Enable dry run mode.

        In dry run mode:
        - All self-healing logic executes normally
        - But actual actions (circuit breaking, retries, DLQ writes) are skipped
        - Actions that "would have been taken" are logged instead

        Use this to safely test self-healing on production traffic.
        """
        with self._state_lock:
            self._refresh_state()
            old_state = self._cached_state.to_dict()
            was_dry_run = self._cached_state.dry_run

            self._cached_state.dry_run = True
            self._cached_state.dry_run_enabled_at = datetime.now(
                timezone.utc
            ).isoformat()
            self._cached_state.dry_run_enabled_by = actor
            self._save_state()

            new_state = self._cached_state.to_dict()

            if not was_dry_run:
                logger.info(
                    f"[SystemControl] DRY RUN mode ENABLED by {actor}. "
                    "Actions will be logged but not executed."
                )
                # Audit 기록
                self._log_audit(
                    "enable_dry_run", actor, old_state, new_state, "dry_run_mode"
                )

            return SystemState.from_dict(self._cached_state.to_dict())

    def disable_dry_run(self, actor: str = "system") -> SystemState:
        """
        Disable dry run mode (go live).

        After disabling dry run, all self-healing actions will be executed for real.
        """
        with self._state_lock:
            self._refresh_state()
            old_state = self._cached_state.to_dict()
            was_dry_run = self._cached_state.dry_run

            self._cached_state.dry_run = False
            self._save_state()

            new_state = self._cached_state.to_dict()

            if was_dry_run:
                logger.warning(
                    f"[SystemControl] DRY RUN mode DISABLED by {actor}. "
                    "Self-healing is now LIVE."
                )
                # Audit 기록
                self._log_audit(
                    "disable_dry_run", actor, old_state, new_state, "go_live"
                )

            return SystemState.from_dict(self._cached_state.to_dict())

    def is_dry_run(self) -> bool:
        """Check if dry run mode is enabled."""
        with self._state_lock:
            return self._cached_state.dry_run

    def reset(self) -> None:
        """Reset to default state (enabled)."""
        with self._state_lock:
            old_state = self._cached_state.to_dict()
            self._cached_state = SystemState()
            self._save_state()
            new_state = self._cached_state.to_dict()
            logger.info("system_control.system_state_reset_defaults")
            # Audit 기록
            self._log_audit(
                "reset", "system", old_state, new_state, "reset_to_defaults"
            )

    def get_backend_info(self) -> dict[str, str]:
        """Get information about the current backend."""
        return {
            "backend_type": type(self._backend).__name__,
            "backend_class": f"{type(self._backend).__module__}.{type(self._backend).__name__}",
        }


# =============================================================================
# Singleton & Factory Functions
# =============================================================================


# Global instance
_system_control: SystemControlManager | None = None


def get_system_control() -> SystemControlManager:
    """Get the global system control manager."""
    global _system_control
    if _system_control is None:
        _system_control = SystemControlManager()
    return _system_control


def is_selfhealing_enabled() -> bool:
    """
    Quick check if self-healing is enabled.

    Use this at the start of any self-healing operation:

        from selfhealing.services.system_control import is_selfhealing_enabled

        def my_healing_function():
            if not is_selfhealing_enabled():
                return  # Kill switch is active

            # ... healing logic
    """
    return get_system_control().is_enabled()


def is_dry_run() -> bool:
    """
    Quick check if dry run mode is enabled.

    Use this before taking any action:

        from selfhealing.services.system_control import is_dry_run

        def trigger_circuit_breaker(service_name):
            if is_dry_run():
                logger.info(
                    "dry_run_open_circuit",
                    service_name=service_name,
                )
                return

            # Actually open the circuit breaker
            circuit_breaker.open(service_name)
    """
    return get_system_control().is_dry_run()


def should_execute_action() -> bool:
    """
    Check if self-healing actions should be executed.

    Returns True only if:
    - System is enabled (kill switch off)
    - AND dry run mode is disabled

    Usage:
        from selfhealing.services.system_control import should_execute_action

        def my_healing_action():
            if not should_execute_action():
                if is_dry_run():
                    logger.info("system_control.dry_run_action")
                return

            # Execute the actual action
    """
    manager = get_system_control()
    return manager.is_enabled() and not manager.is_dry_run()
