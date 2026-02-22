"""
Load Shedding Dashboard API.

운영자가 Shedding 상태를 조회하고 제어할 수 있는 API를 제공합니다.

Usage:
    dashboard = LoadSheddingDashboard(manager)

    # 현재 상태 조회
    status = dashboard.get_status()

    # 수동 활성화
    dashboard.activate(level=1, reason="Planned maintenance")

    # 수동 비활성화
    dashboard.deactivate(reason="Recovery confirmed")
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from selfhealing.services.circuit_breaker.load_shedding.manager import (
        LoadSheddingManager,
    )

logger = structlog.get_logger()


class LoadSheddingDashboard:
    """
    Load Shedding 대시보드 API.

    운영자가 Shedding 상태를 조회하고 제어할 수 있는 API를 제공합니다.
    """

    def __init__(self, manager: LoadSheddingManager | None = None):
        """초기화."""
        self._manager = manager

    @property
    def manager(self) -> LoadSheddingManager:
        """Manager 인스턴스."""
        if self._manager is None:
            from . import (
                get_load_shedding_manager,
            )

            self._manager = get_load_shedding_manager()
        return self._manager

    def get_status(self) -> dict[str, Any]:
        """
        현재 Shedding 상태 조회.

        Returns:
            상태 딕셔너리
        """
        return self.manager.get_status().to_dict()

    def get_service_status(self, service_id: str) -> dict[str, Any]:
        """
        특정 서비스의 Shedding 상태 조회.

        Args:
            service_id: 서비스 ID

        Returns:
            서비스별 상태 딕셔너리
        """
        allowed_percent = self.manager.evaluate_shedding(service_id)
        config = self.manager.get_service_config(service_id)

        return {
            "service_id": service_id,
            "allowed_traffic_percent": allowed_percent,
            "is_shed": allowed_percent < 100.0,
            "criticality": config.criticality if config else "unknown",
            "shed_priority": config.shed_priority if config else 0,
            "min_traffic_percentage": config.min_traffic_percentage if config else 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_all_services_status(self) -> list[dict[str, Any]]:
        """
        모든 서비스의 Shedding 상태 조회.

        Returns:
            서비스별 상태 리스트
        """
        return [
            self.get_service_status(service_id)
            for service_id in self.manager._service_configs.keys()
        ]

    def activate(
        self,
        level: int = 0,
        reason: str = "manual_activation",
        operator: str = "unknown",
    ) -> dict[str, Any]:
        """
        Shedding 수동 활성화.

        Args:
            level: 활성화할 레벨 (0-based)
            reason: 활성화 사유
            operator: 운영자 ID

        Returns:
            결과 딕셔너리
        """
        success = self.manager.force_activate(level, f"{reason} (by {operator})")

        return {
            "success": success,
            "action": "activate",
            "level": level,
            "reason": reason,
            "operator": operator,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_status": self.get_status(),
        }

    def deactivate(
        self,
        reason: str = "manual_deactivation",
        operator: str = "unknown",
    ) -> dict[str, Any]:
        """
        Shedding 수동 비활성화.

        Args:
            reason: 비활성화 사유
            operator: 운영자 ID

        Returns:
            결과 딕셔너리
        """
        success = self.manager.force_deactivate(f"{reason} (by {operator})")

        return {
            "success": success,
            "action": "deactivate",
            "reason": reason,
            "operator": operator,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_status": self.get_status(),
        }

    def get_policy(self) -> dict[str, Any]:
        """
        현재 정책 조회.

        Returns:
            정책 딕셔너리
        """
        policy = self.manager.policy
        return {
            "enabled": policy.enabled,
            "trigger_threshold": policy.trigger_threshold,
            "levels": [
                {
                    "error_rate": level.error_rate,
                    "shed_criticality": level.shed_criticality,
                    "traffic_limit": level.traffic_limit,
                    "description": level.description,
                }
                for level in policy.levels
            ],
        }
