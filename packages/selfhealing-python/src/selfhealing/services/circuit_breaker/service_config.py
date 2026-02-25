"""
Service Configuration Manager for Circuit Breaker

서비스 설정을 관리하고, criticality 기반 조회 및 Load Shedding 대상 선택을 제공합니다.

사용 예시:
    manager = ServiceConfigManager()

    # 서비스 등록
    manager.register_service(ServiceConfig(
        service_id="payment-api",
        criticality="critical",
        shed_priority=0,  # 절대 차단 안 함
    ))

    # criticality로 조회
    critical_services = manager.get_services_by_criticality("critical")

    # Load Shedding 대상 조회
    targets = manager.get_shedding_targets(["low", "medium"])
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from selfhealing.services.circuit_breaker.models import (
    CircuitBreakerAdvancedConfig,
    RecoveryStrategy,
    ServiceConfig,
)

logger = structlog.get_logger()


# =============================================================================
# Service Config Manager
# =============================================================================


class ServiceConfigManager:
    """
    서비스 설정 관리자.

    서비스 등록, criticality 기반 조회, Load Shedding 대상 선택 등을 담당합니다.

    Attributes:
        _services: 등록된 서비스 설정 (service_id -> ServiceConfig)
        _initialized: 초기화 여부

    Usage:
        manager = ServiceConfigManager()

        # 서비스 등록
        manager.register_service(ServiceConfig(
            service_id="payment-api",
            criticality="critical",
        ))

        # 서비스 조회
        config = manager.get_service_config("payment-api")

        # criticality별 서비스 조회
        low_services = manager.get_services_by_criticality("low")

    Reference:
        docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md
        Section 2 - 서비스 Criticality 설정
    """

    _instance: ServiceConfigManager | None = None

    def __new__(cls):
        """싱글톤 패턴."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return

        self._services: dict[str, ServiceConfig] = {}
        self._default_recovery: RecoveryStrategy = RecoveryStrategy()
        self._initialized = True

        logger.debug("initialized")

    @classmethod
    def reset_instance(cls) -> None:
        """싱글톤 인스턴스 초기화 (테스트용)."""
        cls._instance = None

    # =========================================================================
    # Service Registration
    # =========================================================================

    def register_service(self, config: ServiceConfig) -> bool:
        """
        서비스 등록.

        Args:
            config: 서비스 설정

        Returns:
            bool: 등록 성공 여부 (이미 존재하면 업데이트)

        Example:
            >>> manager.register_service(ServiceConfig(
            ...     service_id="payment-api",
            ...     criticality="critical",
            ...     shed_priority=0,
            ... ))
            True
        """
        service_id = config.service_id
        is_update = service_id in self._services

        self._services[service_id] = config

        action = "updated" if is_update else "registered"
        logger.info(
            "service_config_manager.service",
            config_action=action,
            service_id=service_id,
            service_criticality=config.criticality,
            shed_priority=config.shed_priority,
        )

        return True

    def register_services(self, configs: list[ServiceConfig]) -> int:
        """
        여러 서비스 일괄 등록.

        Args:
            configs: 서비스 설정 목록

        Returns:
            int: 등록된 서비스 수
        """
        count = 0
        for config in configs:
            if self.register_service(config):
                count += 1
        return count

    def unregister_service(self, service_id: str) -> bool:
        """
        서비스 등록 해제.

        Args:
            service_id: 서비스 ID

        Returns:
            bool: 해제 성공 여부 (없으면 False)
        """
        if service_id not in self._services:
            logger.warning(
                "service_config_manager.service_found",
                service_id=service_id,
            )
            return False

        del self._services[service_id]
        logger.info(
            "service_config_manager.service_unregistered",
            service_id=service_id,
        )
        return True

    def clear_services(self) -> int:
        """
        모든 서비스 등록 해제.

        Returns:
            int: 해제된 서비스 수
        """
        count = len(self._services)
        self._services.clear()
        logger.info(
            "service_config_manager.all_services_cleared_services",
            cleared_services_count=count,
        )
        return count

    # =========================================================================
    # Service Retrieval
    # =========================================================================

    def get_service_config(self, service_id: str) -> ServiceConfig | None:
        """
        서비스 ID로 설정 조회.

        Args:
            service_id: 서비스 ID

        Returns:
            ServiceConfig or None if not found
        """
        return self._services.get(service_id)

    def get_all_services(self) -> list[ServiceConfig]:
        """
        모든 등록된 서비스 목록 조회.

        Returns:
            List[ServiceConfig]: 모든 서비스 설정
        """
        return list(self._services.values())

    def get_service_count(self) -> int:
        """
        등록된 서비스 수.

        Returns:
            int: 서비스 수
        """
        return len(self._services)

    def is_service_registered(self, service_id: str) -> bool:
        """
        서비스 등록 여부 확인.

        Args:
            service_id: 서비스 ID

        Returns:
            bool: 등록 여부
        """
        return service_id in self._services

    # =========================================================================
    # Criticality-based Retrieval
    # =========================================================================

    def get_services_by_criticality(self, criticality: str) -> list[ServiceConfig]:
        """
        criticality로 서비스 목록 조회.

        Args:
            criticality: 중요도 레벨 ("critical", "high", "medium", "low")

        Returns:
            List[ServiceConfig]: 해당 criticality의 서비스 목록

        Example:
            >>> critical_services = manager.get_services_by_criticality("critical")
            >>> for svc in critical_services:
            ...     print(svc.service_id)
            payment-api
        """
        return [config for config in self._services.values() if config.criticality == criticality]

    def get_critical_services(self) -> list[ServiceConfig]:
        """
        critical 서비스 목록 조회.

        Returns:
            List[ServiceConfig]: critical 서비스 목록
        """
        return self.get_services_by_criticality("critical")

    def get_non_critical_services(self) -> list[ServiceConfig]:
        """
        비핵심 서비스 목록 조회 (high, medium, low).

        Returns:
            List[ServiceConfig]: 비핵심 서비스 목록
        """
        return [config for config in self._services.values() if config.criticality != "critical"]

    # =========================================================================
    # Load Shedding Support
    # =========================================================================

    def get_shedding_targets(
        self,
        shed_criticality: list[str],
    ) -> list[ServiceConfig]:
        """
        Load Shedding 대상 서비스 목록 조회.

        shed_priority가 0보다 큰 서비스 중에서 shed_criticality에 해당하는
        서비스를 shed_priority 내림차순으로 정렬하여 반환합니다.
        (높은 priority가 먼저 차단됨)

        Args:
            shed_criticality: 차단 대상 criticality 목록 (e.g., ["low", "medium"])

        Returns:
            List[ServiceConfig]: 차단 대상 서비스 목록 (shed_priority 내림차순)

        Example:
            >>> targets = manager.get_shedding_targets(["low", "medium"])
            >>> # shed_priority 높은 순서로 반환 (먼저 차단할 서비스)
        """
        targets = [
            config for config in self._services.values() if config.criticality in shed_criticality and config.shed_priority > 0
        ]
        return sorted(targets, key=lambda s: s.shed_priority, reverse=True)

    def get_shedding_order(self) -> list[ServiceConfig]:
        """
        Load Shedding 순서로 모든 서비스 조회.

        shed_priority > 0인 서비스를 priority 내림차순으로 반환합니다.
        priority가 0인 서비스(절대 차단 안 함)는 제외됩니다.

        Returns:
            List[ServiceConfig]: 차단 순서대로 정렬된 서비스 목록
        """
        targets = [config for config in self._services.values() if config.shed_priority > 0]
        return sorted(targets, key=lambda s: s.shed_priority, reverse=True)

    def is_sheddable(self, service_id: str) -> bool:
        """
        서비스가 Load Shedding 대상인지 확인.

        Args:
            service_id: 서비스 ID

        Returns:
            bool: Shedding 대상 여부 (shed_priority > 0)
        """
        config = self.get_service_config(service_id)
        if config is None:
            return False
        return config.shed_priority > 0

    # =========================================================================
    # Recovery Strategy
    # =========================================================================

    def set_default_recovery_strategy(self, strategy: RecoveryStrategy) -> None:
        """
        기본 Recovery 전략 설정.

        Args:
            strategy: 기본 Recovery 전략
        """
        self._default_recovery = strategy
        logger.info(
            "service_config_manager.default_recovery_strategy_set",
            strategy=strategy.type,
        )

    def get_recovery_strategy(self, service_id: str) -> RecoveryStrategy:
        """
        서비스의 Recovery 전략 조회.

        서비스별 전략이 없으면 기본 전략 반환.

        Args:
            service_id: 서비스 ID

        Returns:
            RecoveryStrategy: 서비스별 또는 기본 전략
        """
        config = self.get_service_config(service_id)
        if config is not None and config.recovery_strategy is not None:
            return config.recovery_strategy
        return self._default_recovery

    # =========================================================================
    # Service Threshold Override
    # =========================================================================

    def get_failure_threshold(
        self,
        service_id: str,
        default: int = 5,
    ) -> int:
        """
        서비스의 실패 임계값 조회.

        Args:
            service_id: 서비스 ID
            default: 기본값 (서비스별 설정 없을 때)

        Returns:
            int: 실패 임계값
        """
        config = self.get_service_config(service_id)
        if config is not None and config.failure_threshold is not None:
            return config.failure_threshold
        return default

    def get_window_seconds(
        self,
        service_id: str,
        default: int = 60,
    ) -> int:
        """
        서비스의 관찰 윈도우 조회.

        Args:
            service_id: 서비스 ID
            default: 기본값 (서비스별 설정 없을 때)

        Returns:
            int: 관찰 윈도우 (초)
        """
        config = self.get_service_config(service_id)
        if config is not None and config.window_seconds is not None:
            return config.window_seconds
        return default

    # =========================================================================
    # Bulk Configuration
    # =========================================================================

    def configure_from_advanced_config(
        self,
        config: CircuitBreakerAdvancedConfig,
    ) -> int:
        """
        CircuitBreakerAdvancedConfig에서 서비스 설정 로드.

        Args:
            config: 고급 설정

        Returns:
            int: 등록된 서비스 수
        """
        # 기존 서비스 초기화
        self.clear_services()

        # 서비스 등록
        count = self.register_services(config.services)

        # 기본 Recovery 전략 설정
        self.set_default_recovery_strategy(config.default_recovery)

        logger.info(
            "service_config_manager.configured_advanced_config_services",
            registered_services_count=count,
        )

        return count

    # =========================================================================
    # Status
    # =========================================================================

    def get_status(self) -> dict[str, Any]:
        """
        현재 상태 조회.

        Returns:
            dict: 상태 정보
        """
        services_by_criticality = {
            "critical": len(self.get_services_by_criticality("critical")),
            "high": len(self.get_services_by_criticality("high")),
            "medium": len(self.get_services_by_criticality("medium")),
            "low": len(self.get_services_by_criticality("low")),
        }

        sheddable_count = len([s for s in self._services.values() if s.shed_priority > 0])

        return {
            "total_services": len(self._services),
            "services_by_criticality": services_by_criticality,
            "sheddable_services": sheddable_count,
            "default_recovery_type": self._default_recovery.type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def to_dict(self) -> dict[str, Any]:
        """
        설정을 딕셔너리로 변환.

        Returns:
            dict: 설정 딕셔너리
        """
        return {
            "services": [
                {
                    "service_id": s.service_id,
                    "criticality": s.criticality,
                    "shed_priority": s.shed_priority,
                    "min_traffic_percentage": s.min_traffic_percentage,
                    "failure_threshold": s.failure_threshold,
                    "window_seconds": s.window_seconds,
                }
                for s in self._services.values()
            ],
            "default_recovery_type": self._default_recovery.type,
        }


# =============================================================================
# Module-level Convenience Functions
# =============================================================================


_manager: ServiceConfigManager | None = None


def get_service_config_manager() -> ServiceConfigManager:
    """
    ServiceConfigManager 싱글톤 인스턴스 반환.

    Returns:
        ServiceConfigManager: 싱글톤 인스턴스
    """
    global _manager
    if _manager is None:
        _manager = ServiceConfigManager()
    return _manager


def reset_service_config_manager() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _manager
    _manager = None
    ServiceConfigManager.reset_instance()


def register_service(config: ServiceConfig) -> bool:
    """
    서비스 등록.

    Args:
        config: 서비스 설정

    Returns:
        bool: 등록 성공 여부
    """
    return get_service_config_manager().register_service(config)


def get_service_config(service_id: str) -> ServiceConfig | None:
    """
    서비스 설정 조회.

    Args:
        service_id: 서비스 ID

    Returns:
        ServiceConfig or None
    """
    return get_service_config_manager().get_service_config(service_id)


def get_services_by_criticality(criticality: str) -> list[ServiceConfig]:
    """
    criticality로 서비스 조회.

    Args:
        criticality: 중요도 레벨

    Returns:
        List[ServiceConfig]: 서비스 목록
    """
    return get_service_config_manager().get_services_by_criticality(criticality)


def get_shedding_targets(shed_criticality: list[str]) -> list[ServiceConfig]:
    """
    Load Shedding 대상 조회.

    Args:
        shed_criticality: 차단 대상 criticality 목록

    Returns:
        List[ServiceConfig]: 차단 대상 서비스 목록
    """
    return get_service_config_manager().get_shedding_targets(shed_criticality)


def is_critical_service(service_id: str) -> bool:
    """
    critical 서비스 여부 확인.

    Args:
        service_id: 서비스 ID

    Returns:
        bool: critical 여부
    """
    config = get_service_config(service_id)
    if config is None:
        return False
    return config.criticality == "critical"
