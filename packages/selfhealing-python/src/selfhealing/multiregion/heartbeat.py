"""
Region Heartbeat — TTL 기반 리전 생존 신호.

5초마다 Redis에 TTL 15초 키를 갱신합니다.
키가 만료되면 해당 리전의 비정상 종료로 간주합니다.

3계층 감지 전략:
- Layer 1 (Push): 정상 종료 시 즉시 통보 (0초)
- Layer 2 (Heartbeat): TTL 만료로 비정상 종료 감지 (15초)
- Layer 3 (Polling): 기존 health_monitor 폴백 (30초)

이 모듈은 Layer 1(MultiRegionShutdownHandler)과
Layer 2(RegionHeartbeat)를 담당합니다.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.multiregion.config import MultiRegionSettings

from selfhealing.core.shutdown_coordinator import ShutdownHandler

logger = logging.getLogger(__name__)


class RegionHeartbeat:
    """
    리전 생존 신호 (TTL 기반).

    Redis에 하트비트 키를 주기적으로 갱신합니다.
    프로세스가 죽으면 TTL이 만료되어 Redis Keyspace Notification으로 즉시 감지됩니다.

    하트비트 키:
        selfhealing:state:multiregion:heartbeat:{region}

    사용 예:
        heartbeat = RegionHeartbeat(settings)
        heartbeat.start()
        # ...
        heartbeat.stop()

    Attributes:
        HEARTBEAT_KEY_PREFIX: 하트비트 키 접두사
        HEARTBEAT_TTL: 키 만료 시간 (초)
        HEARTBEAT_INTERVAL: 갱신 주기 (초, TTL의 1/3)
    """

    HEARTBEAT_KEY_PREFIX = "multiregion:heartbeat:"
    """하트비트 키 접두사 (RedisStateBackend가 selfhealing:state: 접두사를 추가)."""

    HEARTBEAT_TTL = 15
    """키 만료 시간 (초). 기존 polling 30초 대비 50% 단축."""

    HEARTBEAT_INTERVAL = 5
    """갱신 주기 (초). TTL의 1/3로 설정하여 만료 방지."""

    def __init__(self, settings: MultiRegionSettings):
        """
        초기화.

        Args:
            settings: Multi-Region 설정
        """
        self._settings = settings
        self._running = False
        self._worker: threading.Thread | None = None

    def _heartbeat_key(self) -> str:
        """하트비트 키 반환 (리전 단위)."""
        return f"{self.HEARTBEAT_KEY_PREFIX}{self._settings.current_region}"

    def _beat(self) -> None:
        """하트비트 1회 갱신."""
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            backend.set(
                self._heartbeat_key(),
                {"region": self._settings.current_region, "ts": time.time()},
                ttl_seconds=self.HEARTBEAT_TTL,
            )
        except Exception as e:
            logger.warning(f"[Heartbeat] Failed: {e}")

    def start(self) -> None:
        """하트비트 시작."""
        if self._running:
            return

        self._running = True
        self._worker = threading.Thread(
            target=self._run,
            name="RegionHeartbeat",
            daemon=True,
        )
        self._worker.start()
        logger.info(
            f"[Heartbeat] Started for {self._settings.current_region} "
            f"(interval={self.HEARTBEAT_INTERVAL}s, ttl={self.HEARTBEAT_TTL}s)"
        )

    def _run(self) -> None:
        """하트비트 루프."""
        while self._running:
            self._beat()
            time.sleep(self.HEARTBEAT_INTERVAL)

    def stop(self) -> None:
        """하트비트 중지."""
        self._running = False
        if self._worker:
            self._worker.join(timeout=2.0)
        logger.info(f"[Heartbeat] Stopped for {self._settings.current_region}")

    def is_running(self) -> bool:
        """실행 중인지 확인."""
        return self._running


class MultiRegionShutdownHandler(ShutdownHandler):
    """
    정상 종료 시 피어 리전에 즉시 통보 (Layer 1 Push).

    GracefulShutdownCoordinator의 ShutdownHandler 인터페이스를 구현합니다.
    SIGTERM 수신 시 RedisEventBus를 통해 REGION_INSTANCE_STOPPING 이벤트를
    모든 구독 인스턴스에 즉시 전파합니다.

    사용 예:
        from selfhealing.core.shutdown_coordinator import GracefulShutdownCoordinator

        coordinator = GracefulShutdownCoordinator()
        handler = MultiRegionShutdownHandler(settings)
        coordinator.register_handler(handler)
    """

    def __init__(self, settings: MultiRegionSettings):
        """
        초기화.

        Args:
            settings: Multi-Region 설정
        """
        self._settings = settings

    def on_shutdown_start(self) -> None:
        """
        SIGTERM 수신 즉시 실행 — 0초 감지.

        RedisEventBus를 통해 REGION_INSTANCE_STOPPING 이벤트를 발행합니다.
        """
        try:
            from selfhealing.services.event_bus.bus import (
                EventType,
                SelfHealingEvent,
            )
            from selfhealing.services.event_bus.redis_bus import get_event_bus

            bus = get_event_bus(distributed=True)
            bus.publish(
                SelfHealingEvent(
                    event_type=EventType.REGION_INSTANCE_STOPPING,
                    data={
                        "region": self._settings.current_region,
                        "reason": "graceful_shutdown",
                        "timestamp": time.time(),
                    },
                    source="shutdown_coordinator",
                )
            )
            logger.info(f"[Shutdown] Notified peers: " f"{self._settings.current_region} stopping")
        except Exception as e:
            logger.warning(f"[Shutdown] Failed to notify peers: {e}")

    def on_drain_complete(self) -> None:
        """드레인 완료 시 추가 동작 불필요."""
        pass

    def on_force_shutdown(self, pending_requests: list) -> None:
        """강제 종료 시 Polling이 폴백으로 감지."""
        pass
