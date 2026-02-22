"""
Recovery Dampening 모듈.

CB CLOSE 또는 Emergency NORMAL 복귀 시 즉시 100% 복구하지 않고
점진적으로 limit을 증가시켜 Thundering Herd를 방지합니다.

복구 단계:
1단계: 이전 제한의 80% (즉시)
2단계: 이전 제한의 90% (30초 후)
3단계: 100% (60초 후)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import structlog

logger = structlog.get_logger()


class RecoveryPhase(str, Enum):
    """복구 단계."""

    PHASE_1 = "phase_1"  # 80%
    PHASE_2 = "phase_2"  # 90%
    COMPLETE = "complete"  # 100%


@dataclass
class RecoveryDampeningConfig:
    """Recovery Dampening 설정."""

    # 활성화 여부
    enabled: bool = True

    # 1단계 복구 비율 (80%)
    phase_1_ratio: float = 0.8

    # 2단계 복구 비율 (90%)
    phase_2_ratio: float = 0.9

    # 1단계 → 2단계 대기 시간 (초)
    phase_1_duration_seconds: float = 30.0

    # 2단계 → 완전 복구 대기 시간 (초)
    phase_2_duration_seconds: float = 30.0


@dataclass
class ServiceRecoveryState:
    """서비스별 복구 상태."""

    service_name: str
    target_limit: int  # 최종 목표 limit
    current_phase: RecoveryPhase = RecoveryPhase.PHASE_1
    phase_started_at: float = field(default_factory=time.time)
    is_active: bool = True

    # Gradient 계산 결과 (적용은 지연)
    pending_gradient_limit: int | None = None


class RecoveryDampeningManager:
    """
    Recovery Dampening 관리자.

    CB CLOSE 또는 비상 모드 해제 시 점진적 복구를 관리합니다.
    Gradient 알고리즘은 백그라운드에서 계속 계산하되, 적용만 지연합니다.
    """

    def __init__(
        self,
        config: RecoveryDampeningConfig | None = None,
        on_limit_change: Callable[[str, int], None] | None = None,
    ):
        """
        초기화.

        Args:
            config: 댐핑 설정
            on_limit_change: limit 변경 시 호출할 콜백 (service_name, new_limit)
        """
        self.config = config or RecoveryDampeningConfig()
        self._on_limit_change = on_limit_change

        # 서비스별 복구 상태
        self._recovery_states: dict[str, ServiceRecoveryState] = {}
        self._lock = threading.RLock()

        # 백그라운드 타이머 (복구 단계 전이용)
        self._phase_timers: dict[str, threading.Timer] = {}

    def start_recovery(
        self,
        service_name: str,
        target_limit: int,
        current_limit: int,
    ) -> int:
        """
        복구 시작 및 1단계 limit 반환.

        Args:
            service_name: 서비스 이름
            target_limit: 최종 목표 limit
            current_limit: 현재 limit (복구 전)

        Returns:
            1단계 limit (target의 80%)
        """
        if not self.config.enabled:
            logger.info(
                "recovery_dampening.disabled_returning_full_limit",
                service_name=service_name,
                target_limit=target_limit,
            )
            return target_limit

        with self._lock:
            # 기존 타이머 취소
            self._cancel_timer(service_name)

            # 1단계 limit 계산
            phase_1_limit = int(target_limit * self.config.phase_1_ratio)

            # 복구 상태 생성
            state = ServiceRecoveryState(
                service_name=service_name,
                target_limit=target_limit,
                current_phase=RecoveryPhase.PHASE_1,
                phase_started_at=time.time(),
                is_active=True,
            )
            self._recovery_states[service_name] = state

            # 2단계 전이 타이머 시작
            self._schedule_phase_transition(
                service_name,
                RecoveryPhase.PHASE_2,
                self.config.phase_1_duration_seconds,
            )

            logger.info(
                "recovery_dampening.started_recovery",
                service_name=service_name,
                target_limit=target_limit,
                phase_1_limit=phase_1_limit,
            )

            return phase_1_limit

    def _schedule_phase_transition(
        self,
        service_name: str,
        next_phase: RecoveryPhase,
        delay_seconds: float,
    ) -> None:
        """복구 단계 전이 타이머 예약."""
        timer = threading.Timer(
            delay_seconds,
            self._on_phase_transition,
            args=(service_name, next_phase),
        )
        timer.daemon = True
        timer.start()

        self._phase_timers[service_name] = timer

    def _on_phase_transition(
        self,
        service_name: str,
        next_phase: RecoveryPhase,
    ) -> None:
        """복구 단계 전이 처리."""
        with self._lock:
            state = self._recovery_states.get(service_name)
            if state is None or not state.is_active:
                return

            state.current_phase = next_phase
            state.phase_started_at = time.time()

            # 새 limit 계산
            if next_phase == RecoveryPhase.PHASE_2:
                new_limit = int(state.target_limit * self.config.phase_2_ratio)

                # 다음 단계 타이머 예약
                self._schedule_phase_transition(
                    service_name,
                    RecoveryPhase.COMPLETE,
                    self.config.phase_2_duration_seconds,
                )

                logger.info(
                    "recovery_dampening.phase",
                    service_name=service_name,
                    new_limit=new_limit,
                )

            elif next_phase == RecoveryPhase.COMPLETE:
                new_limit = state.target_limit
                state.is_active = False

                logger.info(
                    "recovery_dampening.complete",
                    service_name=service_name,
                    new_limit=new_limit,
                )

            else:
                return

            # 콜백 호출
            if self._on_limit_change:
                try:
                    self._on_limit_change(service_name, new_limit)
                except Exception as e:
                    logger.exception(
                        "recovery_dampening.callback_failed",
                        error=e,
                    )

    def cancel_recovery(self, service_name: str) -> None:
        """복구 취소 (새 장애 발생 시)."""
        with self._lock:
            self._cancel_timer(service_name)

            state = self._recovery_states.get(service_name)
            if state:
                state.is_active = False
                logger.info(
                    "recovery_dampening.cancelled",
                    service_name=service_name,
                )

    def _cancel_timer(self, service_name: str) -> None:
        """타이머 취소."""
        timer = self._phase_timers.pop(service_name, None)
        if timer:
            timer.cancel()

    def is_recovery_active(self, service_name: str) -> bool:
        """복구가 진행 중인지 확인."""
        with self._lock:
            state = self._recovery_states.get(service_name)
            return state is not None and state.is_active

    def get_current_multiplier(self, service_name: str) -> float:
        """현재 복구 단계의 배율 반환."""
        with self._lock:
            state = self._recovery_states.get(service_name)
            if state is None or not state.is_active:
                return 1.0

            if state.current_phase == RecoveryPhase.PHASE_1:
                return self.config.phase_1_ratio
            elif state.current_phase == RecoveryPhase.PHASE_2:
                return self.config.phase_2_ratio
            else:
                return 1.0

    def store_pending_gradient_limit(
        self,
        service_name: str,
        gradient_limit: int,
    ) -> None:
        """
        Gradient 계산 결과 저장 (적용 지연).

        복구 중에도 Gradient 알고리즘은 계속 계산되지만,
        limit 적용은 복구 완료 후 수행합니다.

        Args:
            service_name: 서비스 이름
            gradient_limit: Gradient 계산된 limit
        """
        with self._lock:
            state = self._recovery_states.get(service_name)
            if state and state.is_active:
                state.pending_gradient_limit = gradient_limit

    def get_recovery_state(self, service_name: str) -> dict[str, Any] | None:
        """서비스 복구 상태 조회."""
        with self._lock:
            state = self._recovery_states.get(service_name)
            if state is None:
                return None

            elapsed = time.time() - state.phase_started_at

            return {
                "service_name": service_name,
                "target_limit": state.target_limit,
                "current_phase": state.current_phase.value,
                "phase_elapsed_seconds": elapsed,
                "is_active": state.is_active,
                "current_multiplier": self.get_current_multiplier(service_name),
                "pending_gradient_limit": state.pending_gradient_limit,
            }

    def get_all_recovery_states(self) -> list[dict[str, Any]]:
        """모든 복구 상태 조회."""
        with self._lock:
            return [
                self.get_recovery_state(name) for name in self._recovery_states if self.get_recovery_state(name) is not None
            ]

    def reset(self) -> None:
        """모든 상태 초기화 (테스트용)."""
        with self._lock:
            for timer in self._phase_timers.values():
                timer.cancel()
            self._phase_timers.clear()
            self._recovery_states.clear()


# =============================================================================
# Singleton
# =============================================================================

_recovery_dampening_manager: RecoveryDampeningManager | None = None
_manager_lock = threading.Lock()


def get_recovery_dampening_manager(
    config: RecoveryDampeningConfig | None = None,
    on_limit_change: Callable[[str, int], None] | None = None,
) -> RecoveryDampeningManager:
    """전역 RecoveryDampeningManager 인스턴스."""
    global _recovery_dampening_manager

    if _recovery_dampening_manager is None:
        with _manager_lock:
            if _recovery_dampening_manager is None:
                _recovery_dampening_manager = RecoveryDampeningManager(config, on_limit_change)

    return _recovery_dampening_manager


def reset_recovery_dampening_manager() -> None:
    """테스트용 리셋."""
    global _recovery_dampening_manager

    with _manager_lock:
        if _recovery_dampening_manager:
            _recovery_dampening_manager.reset()
        _recovery_dampening_manager = None
