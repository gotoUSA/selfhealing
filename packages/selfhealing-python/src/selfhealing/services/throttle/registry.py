"""
서비스별 Throttle 인스턴스 관리 레지스트리.

각 외부 서비스마다 독립적인 AdaptiveThrottle 인스턴스를 관리하며,
Circuit Breaker 상태 변경에 따라 자동으로 limit을 조정합니다.

Usage:
    registry = get_throttle_registry()

    # 서비스별 Throttle 가져오기
    throttle = registry.get_throttle("payment_api")
    result = throttle.check("user_123")

    # CB 상태 변경 시 자동 조정
    registry.on_circuit_breaker_state_changed("payment_api", "open")
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from selfhealing.audit.graceful_degradation.enums import CircuitState
from selfhealing.services.throttle.adaptive import AdaptiveThrottle
from selfhealing.services.throttle.config import ThrottleConfig

if TYPE_CHECKING:
    from selfhealing.services.circuit_breaker.service import CircuitBreakerService

logger = logging.getLogger(__name__)


@dataclass
class ServiceThrottleState:
    """서비스별 Throttle 상태 정보."""

    service_name: str
    throttle: AdaptiveThrottle
    cb_state: CircuitState = CircuitState.CLOSED
    original_limit: int | None = None  # CB OPEN 전 원래 limit (복구용)


@dataclass
class ServiceThrottleConfig:
    """서비스별 Throttle 설정."""

    service_name: str
    initial_limit: int = 100
    min_limit: int = 10
    max_limit: int = 1000
    sla_warning_ms: int = 200
    sla_critical_ms: int = 500

    # CB 연동 설정
    cb_open_limit_ratio: float = 0.0  # CB OPEN 시 limit 비율 (0 = min_limit 사용)
    cb_half_open_limit_ratio: float = 0.5  # CB HALF_OPEN 시 limit 비율

    def to_throttle_config(self) -> ThrottleConfig:
        """ThrottleConfig로 변환."""
        return ThrottleConfig(
            initial_limit=self.initial_limit,
            min_limit=self.min_limit,
            max_limit=self.max_limit,
            sla_warning_ms=self.sla_warning_ms,
            sla_critical_ms=self.sla_critical_ms,
        )


class ThrottleRegistry:
    """
    서비스별 Throttle 인스턴스 관리 레지스트리.

    각 외부 서비스마다 독립적인 AdaptiveThrottle을 관리하며,
    Circuit Breaker 상태에 따라 limit을 자동 조정합니다.

    Thread-safe 싱글톤 패턴으로 구현됩니다.
    """

    _instance: "ThrottleRegistry | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "ThrottleRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance

    def _init(self) -> None:
        """초기화."""
        self._throttles: dict[str, ServiceThrottleState] = {}
        self._configs: dict[str, ServiceThrottleConfig] = {}
        self._throttle_lock = threading.RLock()

        # 기본 글로벌 Throttle (서비스 미지정 시 사용)
        self._default_config = ServiceThrottleConfig(service_name="__default__")

        # CB 연동 콜백 등록 여부
        self._cb_callbacks_registered = False

    def register_service_config(self, config: ServiceThrottleConfig) -> None:
        """
        서비스별 Throttle 설정 등록.

        Args:
            config: 서비스별 설정
        """
        with self._throttle_lock:
            self._configs[config.service_name] = config
            logger.info(
                f"[ThrottleRegistry] Registered config for '{config.service_name}': "
                f"limit={config.initial_limit}, min={config.min_limit}, max={config.max_limit}"
            )

    def get_throttle(self, service_name: str) -> AdaptiveThrottle:
        """
        서비스별 Throttle 인스턴스 가져오기 (없으면 생성).

        Args:
            service_name: 서비스 이름

        Returns:
            해당 서비스의 AdaptiveThrottle 인스턴스
        """
        with self._throttle_lock:
            if service_name not in self._throttles:
                self._create_throttle(service_name)

            return self._throttles[service_name].throttle

    def _create_throttle(self, service_name: str) -> None:
        """서비스별 Throttle 인스턴스 생성 (내부 호출)."""
        config = self._configs.get(service_name, self._default_config)
        throttle_config = config.to_throttle_config()
        throttle = AdaptiveThrottle(config=throttle_config)

        self._throttles[service_name] = ServiceThrottleState(
            service_name=service_name,
            throttle=throttle,
            cb_state=CircuitState.CLOSED,
        )

        logger.debug(f"[ThrottleRegistry] Created throttle for '{service_name}'")

    def on_circuit_breaker_state_changed(
        self,
        service_name: str,
        new_state: str,
        old_state: str | None = None,
    ) -> None:
        """
        Circuit Breaker 상태 변경 시 해당 서비스 Throttle limit 자동 조정.

        이 메서드는 CB 동기 콜백으로 등록되어 즉시 호출됩니다.

        Args:
            service_name: 서비스 이름
            new_state: 새 CB 상태 ("open", "closed", "half_open")
            old_state: 이전 CB 상태 (옵션)
        """
        with self._throttle_lock:
            # Throttle 인스턴스 확보
            if service_name not in self._throttles:
                self._create_throttle(service_name)

            state = self._throttles[service_name]
            throttle = state.throttle
            config = self._configs.get(service_name, self._default_config)

            try:
                new_cb_state = CircuitState(new_state)
            except ValueError:
                logger.warning(f"[ThrottleRegistry] Invalid CB state: {new_state}")
                return

            previous_limit = throttle.current_limit

            if new_cb_state == CircuitState.OPEN:
                # CB OPEN: min_limit으로 즉시 강등
                state.original_limit = previous_limit  # 복구용 저장
                state.cb_state = CircuitState.OPEN

                if config.cb_open_limit_ratio > 0:
                    new_limit = int(config.initial_limit * config.cb_open_limit_ratio)
                else:
                    new_limit = throttle.config.min_limit

                throttle.current_limit = new_limit
                logger.warning(
                    f"[ThrottleRegistry] CB OPEN for '{service_name}', " f"limit: {previous_limit} → {throttle.current_limit}"
                )

            elif new_cb_state == CircuitState.HALF_OPEN:
                # CB HALF_OPEN: initial_limit의 50%로 제한적 허용
                state.cb_state = CircuitState.HALF_OPEN

                new_limit = int(config.initial_limit * config.cb_half_open_limit_ratio)
                throttle.current_limit = new_limit
                logger.info(
                    f"[ThrottleRegistry] CB HALF_OPEN for '{service_name}', "
                    f"limit: {previous_limit} → {throttle.current_limit}"
                )

            elif new_cb_state == CircuitState.CLOSED:
                # CB CLOSED: 원래 limit 또는 initial_limit으로 복구
                state.cb_state = CircuitState.CLOSED

                if state.original_limit is not None:
                    new_limit = state.original_limit
                    state.original_limit = None
                else:
                    new_limit = config.initial_limit

                throttle.current_limit = new_limit
                logger.info(
                    f"[ThrottleRegistry] CB CLOSED for '{service_name}', "
                    f"limit: {previous_limit} → {throttle.current_limit}"
                )

    def register_circuit_breaker_callbacks(
        self,
        cb_service: "CircuitBreakerService",
    ) -> None:
        """
        CircuitBreakerService에 동기 콜백 등록.

        이 메서드를 호출하면 CB 상태 변경 시 자동으로
        해당 서비스의 Throttle limit이 조정됩니다.

        Args:
            cb_service: CircuitBreakerService 인스턴스
        """
        if self._cb_callbacks_registered:
            logger.debug("[ThrottleRegistry] CB callbacks already registered")
            return

        # 각 상태별 콜백 등록
        for state in ["open", "closed", "half_open"]:
            cb_service.register_state_change_callback(
                state=state,
                callback=self._on_cb_state_changed_callback,
            )

        self._cb_callbacks_registered = True
        logger.info("[ThrottleRegistry] Registered CB state change callbacks")

    def _on_cb_state_changed_callback(
        self,
        service_name: str,
        old_state: str,
        new_state: str,
    ) -> None:
        """
        CB 동기 콜백 핸들러.

        Args:
            service_name: 서비스 이름
            old_state: 이전 상태
            new_state: 새 상태
        """
        self.on_circuit_breaker_state_changed(
            service_name=service_name,
            new_state=new_state,
            old_state=old_state,
        )

    def get_service_state(self, service_name: str) -> dict | None:
        """
        서비스의 현재 Throttle 상태 조회.

        Args:
            service_name: 서비스 이름

        Returns:
            상태 정보 딕셔너리 또는 None
        """
        with self._throttle_lock:
            if service_name not in self._throttles:
                return None

            state = self._throttles[service_name]
            return {
                "service_name": service_name,
                "current_limit": state.throttle.current_limit,
                "cb_state": state.cb_state.value,
                "original_limit": state.original_limit,
                "stats": state.throttle.get_stats(),
            }

    def get_all_service_states(self) -> list[dict]:
        """
        모든 서비스의 Throttle 상태 조회.

        Returns:
            상태 정보 리스트
        """
        with self._throttle_lock:
            return [self.get_service_state(name) for name in self._throttles if self.get_service_state(name) is not None]

    def reset(self) -> None:
        """레지스트리 초기화 (테스트용)."""
        with self._throttle_lock:
            for state in self._throttles.values():
                state.throttle.reset_all()
            self._throttles.clear()
            self._configs.clear()
            self._cb_callbacks_registered = False

        logger.info("[ThrottleRegistry] Registry reset")


# =============================================================================
# Singleton Factory
# =============================================================================

_registry: ThrottleRegistry | None = None
_registry_lock = threading.Lock()


def get_throttle_registry() -> ThrottleRegistry:
    """
    전역 ThrottleRegistry 인스턴스 가져오기.

    Thread-safe 싱글톤 패턴.

    Returns:
        ThrottleRegistry 인스턴스
    """
    global _registry

    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ThrottleRegistry()

    return _registry


def reset_throttle_registry() -> None:
    """레지스트리 초기화 (테스트용)."""
    global _registry

    with _registry_lock:
        if _registry is not None:
            _registry.reset()
        _registry = None
