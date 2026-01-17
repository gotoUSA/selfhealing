"""
Chaos Engineering and L2 Storage Configuration Mixins.

Provides get/update methods for:
- Chaos Engineering
- L2 Storage
"""

from __future__ import annotations

import logging
from dataclasses import asdict, fields
from typing import Any, Dict, Optional

from selfhealing.settings import L2StorageSettings as L2StorageConfig

from .constants import STORAGE_KEYS

logger = logging.getLogger(__name__)


class ChaosStorageMixin:
    """Mixin providing Chaos and L2 Storage configuration methods."""

    # =========================================================================
    # Chaos Engineering Config
    # =========================================================================

    def get_chaos_config(self) -> Dict[str, Any]:
        """
        Get Chaos Engineering configuration.

        Returns:
            dict: Chaos scheduler, safety guard, and blast radius settings
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            stored = self._backend.get(storage_key)
            if stored:
                return stored
            # Return empty config if not set
            return {
                "scheduler_config": {},
                "safety_guard_config": {},
                "blast_radius_policy": {},
                "report_config": {},
            }

    def update_chaos_config(
        self,
        scheduler_config: Optional[Dict[str, Any]] = None,
        safety_guard_config: Optional[Dict[str, Any]] = None,
        blast_radius_policy: Optional[Dict[str, Any]] = None,
        report_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update Chaos Engineering configuration.

        Args:
            scheduler_config: ChaosScheduler configuration
            safety_guard_config: SafetyGuard configuration
            blast_radius_policy: BlastRadius policy
            report_config: Report generator configuration

        Returns:
            dict: Updated configuration
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            current = self.get_chaos_config()

            if scheduler_config is not None:
                current["scheduler_config"] = scheduler_config
                logger.info("[RuntimeConfig] Updated chaos.scheduler_config")

            if safety_guard_config is not None:
                current["safety_guard_config"] = safety_guard_config
                logger.info("[RuntimeConfig] Updated chaos.safety_guard_config")

            if blast_radius_policy is not None:
                current["blast_radius_policy"] = blast_radius_policy
                logger.info("[RuntimeConfig] Updated chaos.blast_radius_policy")

            if report_config is not None:
                current["report_config"] = report_config
                logger.info("[RuntimeConfig] Updated chaos.report_config")

            self._backend.set(storage_key, current)
            return current.copy()

    def update_chaos_ttl_config(
        self,
        default_ttl_seconds: Optional[int] = None,
        min_ttl_seconds: Optional[int] = None,
        max_ttl_seconds: Optional[int] = None,
        auto_expiration_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update Chaos TTL configuration.

        Args:
            default_ttl_seconds: Default TTL for experiments (10분 기본)
            min_ttl_seconds: Minimum allowed TTL
            max_ttl_seconds: Maximum allowed TTL
            auto_expiration_enabled: Enable auto-expiration

        Returns:
            dict: Updated TTL configuration
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            current = self.get_chaos_config()
            ttl_config = current.get("ttl_config", {
                "default_ttl_seconds": 600,
                "min_ttl_seconds": 60,
                "max_ttl_seconds": 3600,
                "auto_expiration_enabled": True,
            })

            if default_ttl_seconds is not None:
                ttl_config["default_ttl_seconds"] = default_ttl_seconds
            if min_ttl_seconds is not None:
                ttl_config["min_ttl_seconds"] = min_ttl_seconds
            if max_ttl_seconds is not None:
                ttl_config["max_ttl_seconds"] = max_ttl_seconds
            if auto_expiration_enabled is not None:
                ttl_config["auto_expiration_enabled"] = auto_expiration_enabled

            current["ttl_config"] = ttl_config
            self._backend.set(storage_key, current)
            logger.info("[RuntimeConfig] Updated chaos.ttl_config")
            return ttl_config

    def update_chaos_stop_conditions_config(
        self,
        max_error_rate_percent: Optional[float] = None,
        max_latency_p99_ms: Optional[int] = None,
        max_latency_p95_ms: Optional[int] = None,
        min_error_budget_percent: Optional[float] = None,
        check_interval_seconds: Optional[int] = None,
        consecutive_breaches_required: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update Chaos Stop Conditions configuration.

        Args:
            max_error_rate_percent: Max error rate before auto-stop
            max_latency_p99_ms: Max P99 latency before auto-stop
            max_latency_p95_ms: Max P95 latency before auto-stop
            min_error_budget_percent: Min error budget before auto-stop
            check_interval_seconds: Interval for checking conditions
            consecutive_breaches_required: Required consecutive breaches
            enabled: Enable/disable stop conditions

        Returns:
            dict: Updated stop conditions configuration
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            current = self.get_chaos_config()
            stop_config = current.get("stop_conditions_config", {
                "max_error_rate_percent": 5.0,
                "max_latency_p99_ms": 2000,
                "max_latency_p95_ms": 1000,
                "min_error_budget_percent": 10.0,
                "check_interval_seconds": 10,
                "consecutive_breaches_required": 2,
                "enabled": True,
            })

            if max_error_rate_percent is not None:
                stop_config["max_error_rate_percent"] = max_error_rate_percent
            if max_latency_p99_ms is not None:
                stop_config["max_latency_p99_ms"] = max_latency_p99_ms
            if max_latency_p95_ms is not None:
                stop_config["max_latency_p95_ms"] = max_latency_p95_ms
            if min_error_budget_percent is not None:
                stop_config["min_error_budget_percent"] = min_error_budget_percent
            if check_interval_seconds is not None:
                stop_config["check_interval_seconds"] = check_interval_seconds
            if consecutive_breaches_required is not None:
                stop_config["consecutive_breaches_required"] = consecutive_breaches_required
            if enabled is not None:
                stop_config["enabled"] = enabled

            current["stop_conditions_config"] = stop_config
            self._backend.set(storage_key, current)
            logger.info("[RuntimeConfig] Updated chaos.stop_conditions_config")
            return stop_config

    def update_chaos_dry_run_config(
        self,
        enabled: Optional[bool] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update Chaos Dry Run configuration.

        Args:
            enabled: Enable/disable dry run mode
            reason: Reason for dry run mode

        Returns:
            dict: Updated dry run configuration
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            current = self.get_chaos_config()
            dry_run_config = current.get("dry_run_config", {
                "enabled": True,
                "reason": "Initial deployment - simulation mode",
            })

            if enabled is not None:
                dry_run_config["enabled"] = enabled
            if reason is not None:
                dry_run_config["reason"] = reason

            current["dry_run_config"] = dry_run_config
            self._backend.set(storage_key, current)
            logger.info(f"[RuntimeConfig] Updated chaos.dry_run_config: enabled={dry_run_config['enabled']}")
            return dry_run_config

    # =========================================================================
    # L2 Storage Config
    # =========================================================================

    def get_l2_storage_config(self) -> Dict[str, Any]:
        """
        Get L2 Storage configuration.

        Returns:
            dict: L2 storage configuration
        """
        storage_key = STORAGE_KEYS["l2_storage"]
        with self._lock:
            if "l2_storage" in self._cache:
                return self._cache["l2_storage"]

            stored = self._backend.get(storage_key)
            if stored:
                self._cache["l2_storage"] = stored
                return stored

            default_config = asdict(L2StorageConfig())
            self._cache["l2_storage"] = default_config
            return default_config

    def update_l2_storage_config(self, **kwargs) -> Dict[str, Any]:
        """
        Update L2 Storage configuration.

        Args:
            redis_timeout_ms: Redis timeout in ms
            database_timeout_ms: Database timeout in ms
            fallback_timeout_ms: Fallback timeout in ms
            shadow_log_enabled: Enable shadow logging
            shadow_log_max_entries: Max shadow log entries
            reconciliation_jitter_min_seconds: Min jitter for reconciliation
            reconciliation_jitter_max_seconds: Max jitter for reconciliation
            health_check_interval_seconds: Health check interval
            health_check_timeout_ms: Health check timeout

        Returns:
            dict: Updated configuration
        """
        storage_key = STORAGE_KEYS["l2_storage"]
        with self._lock:
            current = self.get_l2_storage_config()

            # Validate field names
            valid_fields = {f.name for f in fields(L2StorageConfig)}
            for key, value in kwargs.items():
                if key in valid_fields and value is not None:
                    current[key] = value

            self._backend.set(storage_key, current)
            self._cache["l2_storage"] = current
            logger.info(f"[RuntimeConfig] Updated l2_storage config: {list(kwargs.keys())}")
            return current

    def reset_l2_storage_config(self) -> Dict[str, Any]:
        """
        Reset L2 Storage configuration to defaults.

        Returns:
            dict: Default configuration
        """
        storage_key = STORAGE_KEYS["l2_storage"]
        with self._lock:
            default_config = asdict(L2StorageConfig())
            self._backend.set(storage_key, default_config)
            self._cache["l2_storage"] = default_config
            logger.info("[RuntimeConfig] Reset l2_storage config to defaults")
            return default_config
