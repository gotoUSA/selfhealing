"""
Recovery Aware Shutdown Hook.

K8s preStop 훅에서 현재 진행 중인 Recovery Session이 있는지 확인하고,
있다면 종료를 지연시켜 복구 프로세스를 물리적으로 보호합니다.

Features:
- RecoveryAwareShutdownConfig: Shutdown 설정
- RecoveryAwareShutdownHook: Recovery 인식 Shutdown Hook
- Recovery 완료 대기 로직

Code reference:
    shutdown_coordinator.py (GracefulShutdownCoordinator 패턴)
    recovery_coordinator.py (RecoveryCoordinator.get_active_session())

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#11.3
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from selfhealing.settings import (
    get_recovery_shutdown_settings,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class RecoveryAwareShutdownConfig:
    """
    Recovery 인식 Shutdown 설정.

    Code reference:
        GracefulShutdownCoordinator (shutdown_coordinator.py)
    """

    default_drain_timeout_seconds: float = field(
        default_factory=lambda: get_recovery_shutdown_settings().default_drain_timeout_seconds
    )
    """기본 drain 타임아웃 (초)."""

    recovery_extension_seconds: float = field(
        default_factory=lambda: get_recovery_shutdown_settings().recovery_extension_seconds
    )
    """Recovery Session 진행 중일 때 추가 대기 시간 (초)."""

    max_shutdown_wait_seconds: float = field(
        default_factory=lambda: get_recovery_shutdown_settings().max_shutdown_wait_seconds
    )
    """최대 대기 시간 (초) - Kubernetes terminationGracePeriodSeconds와 일치해야 함."""

    recovery_check_interval_seconds: float = field(
        default_factory=lambda: get_recovery_shutdown_settings().recovery_check_interval_seconds
    )
    """Recovery Session 체크 간격 (초)."""

    log_interval_seconds: float = field(
        default_factory=lambda: get_recovery_shutdown_settings().log_interval_seconds
    )
    """로그 출력 간격 (초)."""

    allow_force_shutdown: bool = field(
        default_factory=lambda: get_recovery_shutdown_settings().allow_force_shutdown
    )
    """최대 대기 시간 초과 시 강제 종료 허용 여부."""


# =============================================================================
# Shutdown Stats
# =============================================================================


@dataclass
class RecoveryShutdownStats:
    """
    Recovery-aware shutdown 통계.
    """

    shutdown_requested: bool = False
    """Shutdown 요청 여부."""

    recovery_active_at_start: bool = False
    """Shutdown 시작 시 Recovery가 활성화되어 있었는지."""

    waited_for_recovery: bool = False
    """Recovery 완료를 대기했는지."""

    wait_started_at: datetime | None = None
    """대기 시작 시각."""

    wait_ended_at: datetime | None = None
    """대기 종료 시각."""

    total_wait_seconds: float = 0.0
    """총 대기 시간 (초)."""

    force_shutdown: bool = False
    """강제 종료 여부."""

    recovery_completed: bool = False
    """Recovery가 정상 완료되었는지."""


# =============================================================================
# Recovery Aware Shutdown Hook
# =============================================================================


class RecoveryAwareShutdownHook:
    """
    Recovery Session을 인식하는 Shutdown Hook.

    K8s preStop 훅에서 현재 진행 중인 Recovery Session이 있는지 확인하고,
    있다면 종료를 지연시켜 복구 프로세스를 물리적으로 보호합니다.

    Usage:
        # 방법 1: 콜백 함수 사용
        def check_recovery():
            from selfhealing.services.coordination import get_recovery_coordinator
            return get_recovery_coordinator().has_active_session()

        hook = RecoveryAwareShutdownHook(recovery_session_checker=check_recovery)

        # 방법 2: GracefulShutdownCoordinator와 통합
        from selfhealing.core.shutdown_coordinator import (
            GracefulShutdownCoordinator,
            RequestTracker,
        )

        tracker = RequestTracker()
        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=30.0,
        )

        # preStop에서 호출
        hook.on_shutdown_start()
        coordinator.initiate_shutdown()

    Code reference:
        GracefulShutdownCoordinator (shutdown_coordinator.py)
        RecoveryCoordinator.get_active_session() (recovery_coordinator.py)
    """

    def __init__(
        self,
        recovery_session_checker: Callable[[], bool],
        config: RecoveryAwareShutdownConfig | None = None,
        on_recovery_complete: Callable[[], None] | None = None,
        on_force_shutdown: Callable[[], None] | None = None,
    ):
        """
        Args:
            recovery_session_checker: Recovery Session 진행 중 여부 반환 함수
            config: Shutdown 설정
            on_recovery_complete: Recovery 완료 시 콜백
            on_force_shutdown: 강제 종료 시 콜백
        """
        self._check_recovery = recovery_session_checker
        self._config = config or RecoveryAwareShutdownConfig()
        self._on_recovery_complete = on_recovery_complete
        self._on_force_shutdown = on_force_shutdown

        self._shutdown_requested = False
        self._shutdown_complete = threading.Event()
        self._stats = RecoveryShutdownStats()
        self._lock = threading.Lock()

    # ==========================================================================
    # ShutdownHandler Interface (shutdown_coordinator.py 호환)
    # ==========================================================================

    def on_shutdown_start(self) -> None:
        """
        Shutdown 시작 시 Recovery Session 확인.

        Recovery Session이 진행 중이면 추가 대기 시간을 요청합니다.
        """
        with self._lock:
            self._shutdown_requested = True
            self._stats.shutdown_requested = True

        recovery_active = self._check_recovery()
        self._stats.recovery_active_at_start = recovery_active

        if recovery_active:
            logger.warning(
                "[RecoveryAwareShutdownHook] Recovery session in progress. "
                f"Extending shutdown timeout by {self._config.recovery_extension_seconds}s"
            )

            # Recovery 완료까지 대기
            self._wait_for_recovery_completion()
        else:
            logger.info(
                "[RecoveryAwareShutdownHook] No active recovery session. "
                "Proceeding with normal shutdown."
            )

    def on_drain_complete(self) -> None:
        """Drain 완료 시 호출."""
        logger.info("[RecoveryAwareShutdownHook] Drain completed successfully.")
        self._shutdown_complete.set()

    def on_force_shutdown(self, pending_requests: list[Any] | None = None) -> None:
        """강제 종료 시 호출."""
        pending_count = len(pending_requests) if pending_requests else 0

        if pending_count > 0:
            logger.error(
                f"[RecoveryAwareShutdownHook] Force shutdown with "
                f"{pending_count} pending requests!"
            )

        # 강제 종료 시 Recovery Session 상태 기록
        if self._check_recovery():
            logger.critical(
                "[RecoveryAwareShutdownHook] CRITICAL: "
                "Force shutdown during active recovery session! "
                "Recovery state may be inconsistent."
            )
            self._stats.force_shutdown = True

        if self._on_force_shutdown:
            try:
                self._on_force_shutdown()
            except Exception as e:
                logger.error(
                    f"[RecoveryAwareShutdownHook] Force shutdown callback error: {e}"
                )

        self._shutdown_complete.set()

    # ==========================================================================
    # Recovery Wait Logic
    # ==========================================================================

    def _wait_for_recovery_completion(self) -> None:
        """Recovery 완료까지 대기."""
        self._stats.waited_for_recovery = True
        self._stats.wait_started_at = datetime.now(timezone.utc)

        start_time = time.monotonic()
        max_wait = self._config.max_shutdown_wait_seconds
        interval = self._config.recovery_check_interval_seconds
        log_interval = self._config.log_interval_seconds
        last_log_time = start_time

        while time.monotonic() - start_time < max_wait:
            if not self._check_recovery():
                elapsed = time.monotonic() - start_time
                self._stats.total_wait_seconds = elapsed
                self._stats.wait_ended_at = datetime.now(timezone.utc)
                self._stats.recovery_completed = True

                logger.info(
                    f"[RecoveryAwareShutdownHook] Recovery completed after {elapsed:.1f}s. "
                    "Proceeding with shutdown."
                )

                if self._on_recovery_complete:
                    try:
                        self._on_recovery_complete()
                    except Exception as e:
                        logger.error(
                            f"[RecoveryAwareShutdownHook] Recovery complete callback error: {e}"
                        )

                return

            # 주기적 로깅
            current_time = time.monotonic()
            if current_time - last_log_time >= log_interval:
                remaining = max_wait - (current_time - start_time)
                logger.info(
                    f"[RecoveryAwareShutdownHook] Waiting for recovery... "
                    f"({remaining:.0f}s remaining)"
                )
                last_log_time = current_time

            time.sleep(interval)

        # 최대 대기 시간 초과
        elapsed = time.monotonic() - start_time
        self._stats.total_wait_seconds = elapsed
        self._stats.wait_ended_at = datetime.now(timezone.utc)

        if self._config.allow_force_shutdown:
            logger.warning(
                f"[RecoveryAwareShutdownHook] Max wait time ({max_wait}s) exceeded. "
                "Proceeding with shutdown despite active recovery."
            )
            self._stats.force_shutdown = True
        else:
            logger.critical(
                f"[RecoveryAwareShutdownHook] Max wait time ({max_wait}s) exceeded. "
                "Force shutdown not allowed. Waiting indefinitely."
            )
            # 무한 대기 (K8s가 SIGKILL로 강제 종료할 때까지)
            while self._check_recovery():
                time.sleep(interval)

    # ==========================================================================
    # Public API
    # ==========================================================================

    def is_shutdown_safe(self) -> bool:
        """
        현재 Shutdown이 안전한지 확인.

        Returns:
            True if no recovery session is active
        """
        return not self._check_recovery()

    def is_shutdown_requested(self) -> bool:
        """
        Shutdown이 요청되었는지 확인.

        Returns:
            True if shutdown was requested
        """
        return self._shutdown_requested

    def get_stats(self) -> RecoveryShutdownStats:
        """
        Shutdown 통계 반환.

        Returns:
            RecoveryShutdownStats
        """
        return self._stats

    def wait_for_shutdown_complete(self, timeout: float | None = None) -> bool:
        """
        Shutdown 완료 대기.

        Args:
            timeout: 대기 타임아웃 (초)

        Returns:
            완료 여부
        """
        return self._shutdown_complete.wait(timeout=timeout)

    def get_effective_drain_timeout(self) -> float:
        """
        Recovery 상태를 고려한 효과적인 drain 타임아웃 반환.

        복구 중이면 기본 타임아웃에 추가 시간을 더합니다.

        Returns:
            효과적인 타임아웃 (초)
        """
        base = self._config.default_drain_timeout_seconds

        if self._check_recovery():
            return base + self._config.recovery_extension_seconds

        return base


# =============================================================================
# Factory Function
# =============================================================================


def create_recovery_aware_shutdown_hook(
    namespace: str = "global",
    config: RecoveryAwareShutdownConfig | None = None,
) -> RecoveryAwareShutdownHook:
    """
    RecoveryAwareShutdownHook 생성.

    RecoveryCoordinator를 사용하여 Recovery Session 체크 함수를 자동 설정합니다.

    Args:
        namespace: 네임스페이스
        config: Shutdown 설정

    Returns:
        RecoveryAwareShutdownHook 인스턴스
    """

    def check_recovery() -> bool:
        try:
            from selfhealing.services.coordination.recovery_coordinator import (
                get_recovery_coordinator,
            )

            coordinator = get_recovery_coordinator()
            session = coordinator.get_active_session(namespace)
            return session is not None
        except Exception as e:
            logger.warning(f"[RecoveryAwareShutdownHook] Check recovery failed: {e}")
            return False

    return RecoveryAwareShutdownHook(
        recovery_session_checker=check_recovery,
        config=config,
    )


# =============================================================================
# Kubernetes preStop Script
# =============================================================================


def run_prestop_check(
    namespace: str = "global",
    max_wait_seconds: float | None = None,
) -> int:
    """
    K8s preStop 스크립트용 메인 함수.

    이 함수는 preStop 훅에서 직접 호출할 수 있습니다.

    Args:
        namespace: 네임스페이스
        max_wait_seconds: 최대 대기 시간 (없으면 환경변수 사용)

    Returns:
        종료 코드 (0: 정상, 1: 강제 종료)

    Example:
        # k8s preStop command:
        # python -c "from selfhealing.services.coordination.recovery_shutdown import run_prestop_check; exit(run_prestop_check())"
    """
    config = RecoveryAwareShutdownConfig()
    if max_wait_seconds is not None:
        config.max_shutdown_wait_seconds = max_wait_seconds

    hook = create_recovery_aware_shutdown_hook(namespace=namespace, config=config)

    logger.info(f"[preStop] Starting recovery-aware shutdown check for {namespace}")

    hook.on_shutdown_start()

    stats = hook.get_stats()

    if stats.force_shutdown:
        logger.warning("[preStop] Shutdown with force (recovery still active)")
        return 1

    logger.info("[preStop] Shutdown safe")
    return 0
