"""
Emergency 상태 자동 갱신기.

Emergency 상태를 주기적으로 갱신하여 캐시 불일치를 방지합니다.
레벨 변경 감지 시 활성 롤아웃을 자동으로 일시 중지합니다.

주요 기능:
- StateRefresherConfig: 상태 갱신 설정
- EmergencyStateRefresher: 주기적 상태 갱신 로직

Reference:
    docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md §3.11
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.services.canary.interlock import CanarySafetyInterlock

logger = logging.getLogger(__name__)


@dataclass
class StateRefresherConfig:
    """
    상태 갱신기 설정.

    Attributes:
        refresh_interval_seconds: 갱신 주기 (초)
        jitter_max_seconds: 최대 지터 (초)
        enabled: 갱신기 활성화 여부
        on_refresh_failure_action: 갱신 실패 시 동작
        max_consecutive_failures: 최대 연속 실패 횟수
    """

    refresh_interval_seconds: int = 30
    """갱신 주기 (초)."""

    jitter_max_seconds: int = 5
    """최대 지터 (초)."""

    enabled: bool = True
    """갱신기 활성화 여부."""

    on_refresh_failure_action: str = "log_and_continue"
    """갱신 실패 시 동작 (log_and_continue, fail_closed)."""

    max_consecutive_failures: int = 3
    """최대 연속 실패 횟수."""


class EmergencyStateRefresher:
    """
    Emergency 상태 자동 갱신기.

    Emergency 상태를 주기적으로 갱신하여 캐시 불일치를 방지합니다.
    레벨이 상승하면 활성 롤아웃을 자동으로 일시 중지합니다.

    Features:
    - 주기적 상태 갱신
    - 레벨 변경 감지
    - 활성 롤아웃 자동 일시 중지
    """

    def __init__(
        self,
        config: StateRefresherConfig | None = None,
        safety_interlock: CanarySafetyInterlock | None = None,
        canary_service: Any | None = None,
    ):
        """
        EmergencyStateRefresher 초기화.

        Args:
            config: 갱신 설정 (None이면 기본값)
            safety_interlock: CanarySafetyInterlock 인스턴스
            canary_service: CanaryRolloutService 인스턴스
        """
        self._config = config or StateRefresherConfig()
        self._safety_interlock = safety_interlock
        self._canary_service = canary_service

        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # namespace -> level value
        self._last_known_level: dict[str, int] = {}
        self._consecutive_failures = 0

    @property
    def is_running(self) -> bool:
        """실행 중인지 여부."""
        return self._running

    @property
    def last_known_levels(self) -> dict[str, int]:
        """마지막으로 알려진 레벨 (namespace -> level)."""
        return self._last_known_level.copy()

    def start(self) -> bool:
        """
        갱신기 시작.

        Returns:
            True: 시작 성공
            False: 이미 실행 중이거나 비활성화됨
        """
        if not self._config.enabled:
            logger.info("[EmergencyStateRefresher] Disabled, not starting")
            return False

        with self._lock:
            if self._running:
                return False

            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

            logger.info("[EmergencyStateRefresher] Started")
            return True

    def stop(self) -> None:
        """갱신기 중지."""
        with self._lock:
            if not self._running:
                return

            self._running = False
            self._stop_event.set()

            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)

            self._thread = None

            logger.info("[EmergencyStateRefresher] Stopped")

    def force_refresh(self) -> dict[str, Any] | None:
        """
        즉시 상태 갱신 및 레벨 변경 확인.

        Returns:
            레벨 변경이 있으면 변경 정보 dict, 없으면 None
        """
        if self._safety_interlock is None:
            return None

        try:
            result = self._safety_interlock.check(operation="refresh")
            namespace = result.namespace
            new_level = result.emergency_level

            old_level = self._last_known_level.get(namespace)
            self._last_known_level[namespace] = new_level

            # 레벨 변경 감지
            if old_level is not None and new_level != old_level:
                logger.warning(
                    f"[EmergencyStateRefresher] Level changed: "
                    f"{namespace}: {old_level} -> {new_level}"
                )

                # 레벨 상승 시 활성 롤아웃 일시 중지
                if new_level > old_level and self._canary_service:
                    self._pause_all_active_rollouts(
                        reason=f"Emergency level increased to {result.emergency_level_name}"
                    )

                return {
                    "namespace": namespace,
                    "old_level": old_level,
                    "new_level": new_level,
                }

            return None

        except Exception as e:
            logger.error(f"[EmergencyStateRefresher] Refresh failed: {e}")
            return None

    def _run(self) -> None:
        """백그라운드 스레드 메인 루프."""
        import random

        while not self._stop_event.is_set():
            try:
                self.force_refresh()
                self._consecutive_failures = 0
            except Exception as e:
                self._consecutive_failures += 1
                logger.error(
                    f"[EmergencyStateRefresher] Refresh error "
                    f"(failures: {self._consecutive_failures}): {e}"
                )

            # 다음 갱신까지 대기 (with jitter)
            jitter = random.uniform(0, self._config.jitter_max_seconds)
            interval = self._config.refresh_interval_seconds + jitter
            self._stop_event.wait(timeout=interval)

    def _pause_all_active_rollouts(self, reason: str) -> int:
        """
        모든 활성 롤아웃 일시 중지.

        Args:
            reason: 일시 중지 사유

        Returns:
            일시 중지된 롤아웃 수
        """
        if not self._canary_service:
            return 0

        try:
            active_rollouts = self._canary_service.get_active_rollouts()
            count = 0

            for rollout in active_rollouts:
                try:
                    self._canary_service.pause(rollout.id)
                    count += 1
                    logger.warning(
                        f"[EmergencyStateRefresher] Paused rollout: {rollout.id}"
                    )
                except Exception as e:
                    logger.error(
                        f"[EmergencyStateRefresher] Failed to pause {rollout.id}: {e}"
                    )

            return count
        except Exception as e:
            logger.error(
                f"[EmergencyStateRefresher] Failed to get active rollouts: {e}"
            )
            return 0


__all__ = [
    "StateRefresherConfig",
    "EmergencyStateRefresher",
]
