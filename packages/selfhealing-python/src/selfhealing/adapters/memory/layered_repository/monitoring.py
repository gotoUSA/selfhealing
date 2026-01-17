"""
Monitoring Operations Mixin.

Provides methods for monitoring, metrics, and health checks.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class MonitoringMixin:
    """Mixin providing monitoring and management operations."""

    def get_storage_info(self) -> Dict[str, Any]:
        """저장소 정보 조회 (L2 상태 및 메트릭 포함)."""
        avg_latency_ms = 0.0
        if self._metrics["l2_latency_count"] > 0:
            avg_latency_ms = self._metrics["l2_latency_total_ms"] / self._metrics["l2_latency_count"]

        return {
            "l1_type": "memory",
            "l1_count": len(self._l1.get_all()),
            "l2_enabled": self._l2 is not None,
            "l2_type": type(self._l2).__name__ if self._l2 else None,
            "l2_adapter_type": self._adapter_type,
            "l2_healthy": self._l2_healthy,
            "l2_was_unhealthy": self._l2_was_unhealthy,
            "l2_consecutive_failures": self._l2_consecutive_failures,
            "l2_last_error_time": (self._l2_last_error_time.isoformat() if self._l2_last_error_time else None),
            "sync_interval_seconds": self._sync_interval,
            "last_sync_time": (self._last_sync_time.isoformat() if self._last_sync_time else None),
            "timeout_ms": self._get_timeout_seconds() * 1000,
            "metrics": {
                "timeout_count": self._metrics["l2_timeout_count"],
                "sync_failure_count": self._metrics["l2_sync_failure_count"],
                "sync_success_count": self._metrics["l2_sync_success_count"],
                "drift_reconciliation_count": self._metrics["drift_reconciliation_count"],
                "avg_latency_ms": round(avg_latency_ms, 2),
            },
            "shadow_log": self._shadow_logger.get_stats(),
            "drift_reconciler": self._drift_reconciler.get_stats(),
        }

    def get_l2_health(self) -> Dict[str, Any]:
        """L2 헬스 상태 조회."""
        return {
            "healthy": self._l2_healthy,
            "was_unhealthy": self._l2_was_unhealthy,
            "consecutive_failures": self._l2_consecutive_failures,
            "last_error_time": (self._l2_last_error_time.isoformat() if self._l2_last_error_time else None),
            "adapter_type": self._adapter_type,
            "timeout_ms": self._get_timeout_seconds() * 1000,
        }

    def reset_l2_health(self) -> None:
        """L2 헬스 상태 리셋 (수동 복구 시)."""
        self._l2_healthy = True
        self._l2_was_unhealthy = False
        self._l2_consecutive_failures = 0
        self._l2_last_error_time = None
        logger.info("[LayeredRepo] L2 health status reset manually")

    def get_metrics(self) -> Dict[str, Any]:
        """내부 메트릭 조회."""
        return dict(self._metrics)

    def reset_metrics(self) -> None:
        """메트릭 리셋 (테스트용)."""
        self._metrics = {
            "l2_timeout_count": 0,
            "l2_sync_failure_count": 0,
            "l2_sync_success_count": 0,
            "l2_latency_total_ms": 0.0,
            "l2_latency_count": 0,
            "drift_reconciliation_count": 0,
        }
