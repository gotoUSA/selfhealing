"""
Leader Elector Graceful Shutdown 통합.

SIGTERM/SIGINT 수신 시 자동으로 리더십을 안전하게 반납.
기존 GracefulShutdownCoordinator와 통합 지원.
"""

from __future__ import annotations

import atexit
import signal
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from selfhealing.coordination.base import LeaderElector

logger = structlog.get_logger()

_registered_electors: list[LeaderElector] = []
_handlers_installed = False


def register_for_graceful_shutdown(elector: LeaderElector) -> None:
    """
    LeaderElector를 Graceful Shutdown에 등록.

    SIGTERM/SIGINT 수신 시 자동으로 stop() 호출.

    Args:
        elector: 등록할 LeaderElector 인스턴스

    Usage:
        elector = get_leader_elector("dlq-consumer")
        register_for_graceful_shutdown(elector)
        elector.start()
    """
    global _registered_electors, _handlers_installed

    if elector not in _registered_electors:
        _registered_electors.append(elector)
        logger.debug(
            "leader_elector.graceful_shutdown_registered",
            elector=elector.resource_name,
        )

    # 최초 등록 시 시그널 핸들러 설정
    if not _handlers_installed:
        _setup_signal_handlers()
        atexit.register(shutdown_all_electors)
        _handlers_installed = True


def unregister_from_graceful_shutdown(elector: LeaderElector) -> None:
    """
    LeaderElector를 Graceful Shutdown에서 등록 해제.

    Args:
        elector: 해제할 LeaderElector 인스턴스
    """
    global _registered_electors

    if elector in _registered_electors:
        _registered_electors.remove(elector)
        logger.debug(
            "leader_elector.graceful_shutdown_unregistered",
            elector=elector.resource_name,
        )


def _setup_signal_handlers() -> None:
    """SIGTERM/SIGINT 핸들러 설정.

    Gunicorn Worker에서는 등록을 건너뛴다. Gunicorn Master가
    프로세스 라이프사이클을 제어하며, worker_exit 훅에서 정리한다.
    """
    from selfhealing.core.process_utils import is_gunicorn_worker

    if is_gunicorn_worker():
        logger.info("leader_elector.skipping_signal_registration_gunicorn")
        return

    if sys.platform == "win32":
        # Windows는 SIGTERM 미지원
        signal.signal(signal.SIGINT, _signal_handler)
    else:
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

    logger.info("leader_elector.graceful_shutdown_handlers_installed")


def _signal_handler(signum: int, frame) -> None:
    """시그널 핸들러."""
    signal_name = signal.Signals(signum).name
    logger.info(
        "leader_elector.signal_received_shutdown_started",
        signal_name=signal_name,
    )
    shutdown_all_electors()


def shutdown_all_electors() -> None:
    """모든 등록된 Elector 종료."""
    global _registered_electors

    for elector in list(_registered_electors):
        try:
            logger.info(
                "leader_elector.stopping",
                elector=elector.resource_name,
            )
            elector.stop()
            logger.info(
                "leader_elector.stopped",
                elector=elector.resource_name,
            )
        except Exception as e:
            logger.exception(
                "leader_elector.stop_failed",
                elector=elector.resource_name,
                error=e,
            )

    _registered_electors.clear()


def integrate_with_shutdown_coordinator() -> None:
    """
    GracefulShutdownCoordinator와 통합.

    기존 시스템의 Graceful Shutdown 인프라 활용.
    """
    try:
        from selfhealing.core.shutdown_coordinator import (
            ShutdownHandler,
        )

        class LeaderElectorShutdownHandler(ShutdownHandler):
            """LeaderElector용 Shutdown 핸들러."""

            def on_shutdown_start(self) -> None:
                """Shutdown 시작 시 리더십 반납."""
                logger.info("leader_elector.shutdown_started_releasing_leadership")
                shutdown_all_electors()

            def on_drain_complete(self) -> None:
                """Drain 완료 시 (추가 작업 없음)."""
                pass

            def on_force_shutdown(self, pending_requests) -> None:
                """강제 종료 시 리더십 반납."""
                shutdown_all_electors()

        logger.info("leader_elector.graceful_shutdown_coordinator_integration_ready")
        return LeaderElectorShutdownHandler()

    except ImportError:
        logger.debug("leader_elector.graceful_shutdown_coordinator_not_found")
        return None
