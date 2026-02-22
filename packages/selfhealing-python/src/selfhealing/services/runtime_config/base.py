"""
Base Manager for Runtime Configuration.

Core infrastructure including storage, cache, and history tracking.
"""

from __future__ import annotations

import structlog
import threading
from dataclasses import asdict as dataclass_asdict
from dataclasses import fields, is_dataclass
from typing import Any

from selfhealing.core.state_backend import get_state_backend

from .constants import CONFIG_CLASSES, DEFAULT_SLO_CONFIG, STORAGE_KEYS

logger = structlog.get_logger()


def get_field_names(config_class: type) -> set:
    """Get field names from config class, handling both Pydantic and dataclass."""
    if hasattr(config_class, "model_fields"):
        # Pydantic v2
        return set(config_class.model_fields.keys())
    elif is_dataclass(config_class):
        # dataclass
        return {f.name for f in fields(config_class)}
    else:
        raise TypeError(f"Cannot get fields from {config_class}")


def to_dict(obj: Any) -> dict[str, Any]:
    """Convert config object to dict, handling both Pydantic and dataclass."""
    if hasattr(obj, "model_dump"):
        # Pydantic v2
        return obj.model_dump()
    elif is_dataclass(obj) and not isinstance(obj, type):
        # dataclass instance
        return dataclass_asdict(obj)
    else:
        raise TypeError(f"Cannot convert {type(obj)} to dict")


class BaseConfigManager:
    """
    Base Configuration Manager.

    Thread-safe manager with persistent storage via StateBackend.

    Features:
    - Get/Update config with type safety
    - Persistent storage (survives restarts)
    - Thread-safe operations
    - Audit logging and history tracking
    """

    def __init__(self):
        """Initialize BaseConfigManager."""
        self._lock = threading.RLock()
        self._backend = get_state_backend()
        self._cache: dict[str, Any] = {}
        self._load_all_configs()

    def _load_all_configs(self) -> None:
        """Load all configs from storage or use defaults."""
        with self._lock:
            for config_type, storage_key in STORAGE_KEYS.items():
                stored = self._backend.get(storage_key)
                if stored:
                    self._cache[config_type] = stored
                else:
                    # Use defaults
                    config_class = CONFIG_CLASSES[config_type]
                    if config_class is not None:
                        self._cache[config_type] = to_dict(config_class())
                    else:
                        # SLO는 별도 기본값 사용
                        self._cache[config_type] = DEFAULT_SLO_CONFIG.copy()

    def _save_config(self, config_type: str, config_dict: dict[str, Any]) -> None:
        """Save config to storage."""
        storage_key = STORAGE_KEYS[config_type]
        self._backend.set(storage_key, config_dict)
        self._cache[config_type] = config_dict

    def _get_config(self, config_type: str) -> dict[str, Any]:
        """
        Get config by type.

        새 필드가 추가되었을 경우, 저장된 설정과 기본값을 병합하여 반환합니다.
        """
        with self._lock:
            config_class = CONFIG_CLASSES.get(config_type)

            # Get defaults
            if config_class is not None:
                defaults = to_dict(config_class())
            elif config_type == "slo":
                defaults = DEFAULT_SLO_CONFIG.copy()
            else:
                defaults = {}

            if config_type not in self._cache:
                self._cache[config_type] = defaults.copy()
            else:
                # Merge defaults with cached values (cached values take precedence)
                # This ensures new fields from defaults are included
                merged = defaults.copy()
                merged.update(self._cache[config_type])
                self._cache[config_type] = merged

            return self._cache[config_type].copy()

    def _update_config(self, config_type: str, changed_by: str = "system", reason: str = "", **kwargs) -> dict[str, Any]:
        """Update config fields with history tracking.

        Args:
            config_type: Type of config (e.g., "circuit_breaker")
            changed_by: User or system that made the change
            reason: Reason for the change
            **kwargs: Config fields to update

        Returns:
            Updated config values
        """
        with self._lock:
            current = self._get_config(config_type)
            previous = current.copy()  # Snapshot before changes
            config_class = CONFIG_CLASSES.get(config_type)

            # Get valid field names from config class (if available)
            if config_class is not None:
                valid_fields = get_field_names(config_class)
            else:
                valid_fields = set(current.keys())

            # Track Safe Default applications
            applied_safe_defaults = []

            # Update only provided fields that are valid
            for key, value in kwargs.items():
                if key in valid_fields:
                    # Check if Safe Default should be applied
                    from selfhealing.core.safe_defaults import (
                        get_safe_default,
                        is_valid_value,
                    )

                    if not is_valid_value(config_type, key, value):
                        safe_value = get_safe_default(config_type, key)
                        if safe_value is not None:
                            applied_safe_defaults.append(f"{key}: {value!r} → {safe_value!r}")
                            current[key] = safe_value
                            logger.warning(
                                f"[RuntimeConfig] Safe default applied: " f"{config_type}.{key} ({value!r} → {safe_value!r})"
                            )
                        else:
                            current[key] = value
                            logger.info(
                                "runtime_config.updated",
                                config_type=config_type,
                                key=key,
                                value=value,
                            )
                    else:
                        current[key] = value
                        logger.info(
                            "runtime_config.updated",
                            config_type=config_type,
                            key=key,
                            value=value,
                        )

            # Diff-Aware: Only save if there are actual changes
            if previous == current:
                logger.debug(
                    "runtime_config.no_changes_detected",
                    config_type=config_type,
                )
                return current.copy()

            # Compute diff for audit
            diff = self._compute_diff(previous, current)

            self._save_config(config_type, current)

            # Build final reason with Safe Default marker
            final_reason = reason or f"Updated: {list(kwargs.keys())}"
            if applied_safe_defaults:
                final_reason = f"⚠️ Safe Default applied: {', '.join(applied_safe_defaults)} | " f"{final_reason}"

            # Save to ConfigHistory (best-effort)
            self._save_to_history(
                config_type=config_type,
                values=current,
                changed_by=changed_by,
                reason=final_reason,
            )

            # Emit CONFIG_CHANGE audit event
            if diff:
                self._emit_config_change_audit(
                    config_type=config_type,
                    changed_by=changed_by,
                    reason=final_reason,
                    old_values=diff["old"],
                    new_values=diff["new"],
                )

            # Emit EventBus CONFIG_UPDATED event (best-effort)
            # BulkheadRegistry, HedgingStrategy, CorrelationEngine 등
            # CONFIG_UPDATED 구독자에게 변경 사항을 전파한다.
            if diff:
                self._emit_config_updated_event(
                    config_type=config_type,
                    changed_by=changed_by,
                    old_values=diff["old"],
                    new_values=diff["new"],
                )

            return current.copy()

    def _compute_diff(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        변경된 필드만 추출.

        Args:
            old: 이전 설정 딕셔너리
            new: 새 설정 딕셔너리

        Returns:
            {"old": {changed_fields}, "new": {changed_fields}} 또는 None
        """
        old_diff = {}
        new_diff = {}

        for key in set(old.keys()) | set(new.keys()):
            if old.get(key) != new.get(key):
                old_diff[key] = old.get(key)
                new_diff[key] = new.get(key)

        if old_diff:
            return {"old": old_diff, "new": new_diff}
        return None

    def _emit_config_change_audit(
        self,
        config_type: str,
        changed_by: str,
        reason: str,
        old_values: dict[str, Any],
        new_values: dict[str, Any],
    ) -> None:
        """
        AuditEventType.CONFIG_CHANGE 발행.

        Best-effort: 실패해도 설정 업데이트에 영향 없음.

        Args:
            config_type: 설정 타입
            changed_by: 변경자
            reason: 변경 사유
            old_values: 변경 전 값들
            new_values: 변경 후 값들
        """
        try:
            from selfhealing.audit import log_config_change

            # 각 변경된 필드에 대해 audit 로그 기록
            for key in new_values:
                log_config_change(
                    config_type=config_type.upper(),
                    config_key=key,
                    old_value=old_values.get(key),
                    new_value=new_values[key],
                    user=changed_by,
                    reason=reason,
                )

            logger.info(
                f"[RuntimeConfig] Audit logged: {config_type} " f"changed by {changed_by}, fields: {list(new_values.keys())}"
            )
        except Exception as e:
            # Graceful degradation - audit failure should not break config update
            logger.warning(
                "runtime_config.failed_emit_audit",
                error=e,
            )

    def _emit_config_updated_event(
        self,
        config_type: str,
        changed_by: str,
        old_values: dict[str, Any],
        new_values: dict[str, Any],
    ) -> None:
        """EventBus CONFIG_UPDATED 이벤트 발행.

        BulkheadRegistry, HedgingStrategy, CorrelationEngine 등
        CONFIG_UPDATED 이벤트를 구독하는 모든 컴포넌트에 설정 변경을 전파한다.

        Best-effort: 실패해도 설정 업데이트에 영향 없음.
        """
        try:
            from selfhealing.services.event_bus.bus import (
                EventPriority,
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.CONFIG_UPDATED,
                data={
                    "config_type": config_type,
                    "changed_by": changed_by,
                    "old_values": old_values,
                    "new_values": new_values,
                },
                source="runtime_config_manager",
                priority=EventPriority.NORMAL,
            )
            logger.debug(
                "runtime_config.event_emitted",
                config_type=config_type,
            )
        except Exception as e:
            logger.warning(
                "runtime_config.failed_emit",
                error=e,
            )

    def _save_to_history(
        self,
        config_type: str,
        values: dict[str, Any],
        changed_by: str,
        reason: str,
    ) -> None:
        """Save config version to history (best-effort).

        This method never raises exceptions - history saving failure
        should not break config updates.

        Args:
            config_type: Type of config
            values: Current config values
            changed_by: User or system that made the change
            reason: Reason for the change
        """
        try:
            from selfhealing.services.config_history import get_config_history_service

            history_service = get_config_history_service()
            history_service.save_version(
                config_type=config_type,
                values=values,
                changed_by=changed_by,
                reason=reason,
            )
            logger.debug(
                "runtime_config.saved_history",
                config_type=config_type,
                changed_by=changed_by,
            )
        except Exception as e:
            # Graceful degradation - history save failure should not break config update
            logger.warning(
                "runtime_config.failed_save_history",
                error=e,
            )

    def get_all_config(self) -> dict[str, dict[str, Any]]:
        """Get all configuration."""
        with self._lock:
            return {config_type: self._get_config(config_type) for config_type in STORAGE_KEYS.keys()}

    def reset_to_defaults(self) -> dict[str, dict[str, Any]]:
        """Reset all configuration to defaults."""
        with self._lock:
            for config_type, config_class in CONFIG_CLASSES.items():
                if config_class is not None:
                    default_config = to_dict(config_class())
                else:
                    # SLO는 별도 기본값 사용
                    default_config = DEFAULT_SLO_CONFIG.copy()
                self._save_config(config_type, default_config)
                logger.info(
                    "runtime_config.reset_defaults",
                    config_type=config_type,
                )

            return self.get_all_config()
