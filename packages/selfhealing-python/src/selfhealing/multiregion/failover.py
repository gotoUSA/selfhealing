"""
Region Failover - 리전 장애 시 자동 페일오버.

리전 장애 감지 시 자동으로 다른 리전으로 페일오버합니다.

페일오버 프로세스:
1. 리전 장애 감지 (RegionHealthMonitor)
2. Quorum 획득 시도 (Split-brain 방지)
3. 페일오버 대상 리전 선정
4. 트래픽 전환 (Route53/GCP)
5. 데이터 정합성 확인
6. 알림 전송
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    get_multiregion_settings,
)
from selfhealing.multiregion.health_monitor import (
    RegionHealthMonitor,
    RegionHealthStatus,
)

logger = logging.getLogger(__name__)


class FailoverState(Enum):
    """
    페일오버 상태.

    상태 전이:
    - NORMAL → DETECTING: 장애 감지 시작
    - DETECTING → FAILOVER_IN_PROGRESS: 페일오버 시작
    - FAILOVER_IN_PROGRESS → FAILED_OVER: 페일오버 완료
    - FAILED_OVER → RECOVERING: 원래 리전 복구 시도
    - RECOVERING → NORMAL: 복구 완료
    """

    NORMAL = "normal"
    """정상 상태."""

    DETECTING = "detecting"
    """장애 감지 중."""

    FAILOVER_IN_PROGRESS = "failover_in_progress"
    """페일오버 진행 중."""

    FAILED_OVER = "failed_over"
    """페일오버 완료됨."""

    RECOVERING = "recovering"
    """복구 중."""


@dataclass
class FailoverEvent:
    """
    페일오버 이벤트.

    페일오버 발생 시 생성되어 콜백으로 전달됩니다.

    Attributes:
        from_region: 원래 Primary 리전
        to_region: 새 Primary 리전
        timestamp: 페일오버 시각
        reason: 페일오버 사유
        state: 현재 상태
        details: 상세 정보
    """

    from_region: str
    """원래 Primary 리전."""

    to_region: str
    """새 Primary 리전."""

    timestamp: datetime
    """페일오버 시각."""

    reason: str
    """페일오버 사유."""

    state: FailoverState
    """현재 상태."""

    details: dict[str, Any] = field(default_factory=dict)
    """상세 정보."""


class RegionFailover:
    """
    Region Failover Manager.

    리전 장애 감지 시 자동으로 다른 리전으로 페일오버합니다.

    페일오버 프로세스:
    1. 리전 장애 감지 (RegionHealthMonitor)
    2. Quorum 획득 시도 (Split-brain 방지)
    3. 페일오버 대상 리전 선정
    4. 트래픽 전환 (Route53/GCP)
    5. 데이터 정합성 확인
    6. 알림 전송

    사용 예:
        failover = RegionFailover()

        # 페일오버 콜백 등록
        def on_failover(event: FailoverEvent):
            print(f"Failover: {event.from_region} → {event.to_region}")
        failover = RegionFailover(on_failover=on_failover)

        # 모니터링 시작
        failover.start()

        # 수동 페일오버 트리거
        failover.trigger_failover(reason="maintenance")

        # 모니터링 중지
        failover.stop()
    """

    def __init__(
        self,
        settings: MultiRegionSettings | None = None,
        health_monitor: RegionHealthMonitor | None = None,
        quorum_witness: Any | None = None,
        on_failover: Callable[[FailoverEvent], None] | None = None,
    ):
        """
        초기화.

        Args:
            settings: Multi-Region 설정
            health_monitor: 리전 건강 모니터
            quorum_witness: Quorum Witness (Split-brain 방지)
            on_failover: 페일오버 시 호출되는 콜백
        """
        self._settings = settings or get_multiregion_settings()
        self._health_monitor = health_monitor or RegionHealthMonitor(settings=self._settings)
        self._quorum_witness = quorum_witness
        self._on_failover = on_failover

        self._lock = threading.RLock()
        self._state = FailoverState.NORMAL
        self._last_failover_time: float = 0
        self._current_primary: str = self._settings.current_region
        self._running = False
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 페일오버 히스토리
        self._history: list[FailoverEvent] = []

    def get_state(self) -> FailoverState:
        """현재 상태 반환."""
        with self._lock:
            return self._state

    def get_current_primary(self) -> str:
        """현재 Primary 리전 반환."""
        with self._lock:
            return self._current_primary

    def _select_failover_target(self) -> str | None:
        """
        페일오버 대상 리전 선정.

        기준:
        1. HEALTHY 상태인 리전
        2. 현재 Primary가 아닌 리전
        3. 우선순위 또는 latency 기준 정렬

        Returns:
            대상 리전 또는 None
        """
        healthy = self._health_monitor.get_healthy_regions()

        if not healthy:
            logger.error("[Failover] No healthy regions available")
            return None

        # 현재 Primary 제외
        candidates = [r for r in healthy if r != self._current_primary]

        if not candidates:
            logger.error("[Failover] No failover candidates")
            return None

        # 우선순위 기준 정렬 (우선순위 낮을수록 높은 우선순위)
        endpoints = {e.region: e for e in self._settings.get_peer_endpoints()}
        candidates.sort(key=lambda r: endpoints.get(r, type("", (), {"priority": 100})()).priority)

        return candidates[0]

    def _can_failover(self) -> bool:
        """페일오버 가능 여부 확인."""
        if not self._settings.failover_enabled:
            return False

        # 쿨다운 확인
        elapsed = time.time() - self._last_failover_time
        if elapsed < self._settings.failover_cooldown_seconds:
            logger.debug(f"[Failover] Cooldown ({elapsed:.0f}s < " f"{self._settings.failover_cooldown_seconds}s)")
            return False

        return True

    def trigger_failover(self, reason: str = "manual") -> bool:
        """
        수동 페일오버 트리거.

        Args:
            reason: 페일오버 사유

        Returns:
            True if 성공
        """
        if not self._can_failover():
            return False

        target = self._select_failover_target()
        if target is None:
            return False

        return self._execute_failover(target, reason)

    def _execute_failover(self, target_region: str, reason: str) -> bool:
        """
        페일오버 실행.

        Args:
            target_region: 대상 리전
            reason: 페일오버 사유

        Returns:
            True if 성공
        """
        with self._lock:
            if self._state == FailoverState.FAILOVER_IN_PROGRESS:
                logger.warning("[Failover] Already in progress")
                return False

            self._state = FailoverState.FAILOVER_IN_PROGRESS

        logger.warning(f"[Failover] Executing failover: " f"{self._current_primary} → {target_region} ({reason})")

        try:
            # 1. Quorum 획득 (Split-brain 방지)
            if self._quorum_witness:
                if not self._quorum_witness.try_acquire_primary():
                    logger.error("[Failover] Cannot become primary: " "quorum witness denied")
                    with self._lock:
                        self._state = FailoverState.NORMAL
                    return False

            # 2. DNS/Load Balancer 전환
            # TODO: Route53 / GCP Global LB API 호출
            self._update_traffic_routing(target_region)

            # 3. 데이터 정합성 확인
            # TODO: 마지막 복제 오프셋 확인
            self._verify_data_consistency(target_region)

            # 4. 상태 업데이트
            with self._lock:
                old_primary = self._current_primary
                self._current_primary = target_region
                self._state = FailoverState.FAILED_OVER
                self._last_failover_time = time.time()

            # 5. 이벤트 생성
            event = FailoverEvent(
                from_region=old_primary,
                to_region=target_region,
                timestamp=datetime.now(timezone.utc),
                reason=reason,
                state=FailoverState.FAILED_OVER,
            )

            # 히스토리 저장
            self._history.append(event)

            # 6. 콜백 호출
            if self._on_failover:
                try:
                    self._on_failover(event)
                except Exception as e:
                    logger.error(f"[Failover] Callback error: {e}")

            # 7. 알림 전송
            self._send_alert(event)

            logger.warning(f"[Failover] Completed: {old_primary} → {target_region}")
            return True

        except Exception as e:
            logger.error(f"[Failover] Failed: {e}")
            with self._lock:
                self._state = FailoverState.NORMAL
            return False

    def _update_traffic_routing(self, target_region: str) -> None:
        """
        트래픽 라우팅 업데이트.

        Args:
            target_region: 대상 리전
        """
        # TODO: Route53 / GCP Global LB API 호출
        logger.info(f"[Failover] Updating traffic routing to {target_region}")

    def _verify_data_consistency(self, target_region: str) -> None:
        """
        데이터 정합성 확인.

        Args:
            target_region: 대상 리전
        """
        # TODO: 마지막 복제 오프셋 확인
        logger.info(f"[Failover] Verifying data consistency for {target_region}")

    def _send_alert(self, event: FailoverEvent) -> None:
        """
        페일오버 알림 전송.

        Args:
            event: 페일오버 이벤트
        """
        try:
            from selfhealing.meta.escalation import (
                EscalationEvent,
                EscalationLevel,
                EscalationManager,
            )

            manager = EscalationManager()
            manager.escalate(
                EscalationEvent(
                    level=EscalationLevel.CRITICAL,
                    title=f"Region Failover: {event.from_region} → {event.to_region}",
                    description=f"Automatic failover executed.\nReason: {event.reason}",
                    component="multiregion",
                    details={
                        "from_region": event.from_region,
                        "to_region": event.to_region,
                        "reason": event.reason,
                    },
                    timestamp=event.timestamp,
                )
            )
        except ImportError:
            logger.warning("[Failover] Escalation module not available")
        except Exception as e:
            logger.error(f"[Failover] Alert error: {e}")

    def _check_and_failover(self) -> None:
        """건강 상태 확인 및 자동 페일오버."""
        # Primary 리전 건강 확인 (자기 자신이 Primary일 때만)
        if not self._settings.is_primary():
            return

        # 피어 리전 중 하나라도 UNREACHABLE이면 체크
        all_health = self._health_monitor.get_all_health_states()

        for region, health in all_health.items():
            if health.status == RegionHealthStatus.UNREACHABLE:
                logger.warning(f"[Failover] Peer region unreachable: {region}")

        # 현재 리전이 Primary이고, 다른 리전에서 장애가 발생하면
        # 해당 리전으로의 복제를 중단하고 알림만 전송
        # (자기 자신이 죽은 경우는 감지 불가)

    def _run_loop(self) -> None:
        """모니터링 루프."""
        while self._running:
            try:
                self._check_and_failover()
            except Exception as e:
                logger.error(f"[Failover] Loop error: {e}")

            self._stop_event.wait(self._settings.health_check_interval_seconds)
            if self._stop_event.is_set():
                break

    def start(self) -> None:
        """페일오버 모니터링 시작."""
        if not self._settings.enabled or not self._settings.failover_enabled:
            logger.info("[Failover] Disabled")
            return

        if self._running:
            return

        # Health Monitor 시작
        self._health_monitor.start()

        self._stop_event.clear()
        self._running = True
        self._worker = threading.Thread(
            target=self._run_loop,
            name="RegionFailover",
            daemon=True,
        )
        self._worker.start()
        logger.info("[Failover] Started")

    def stop(self) -> None:
        """페일오버 모니터링 중지."""
        self._running = False
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=2.0)
        self._health_monitor.stop()
        logger.info("[Failover] Stopped")

    def is_running(self) -> bool:
        """실행 중인지 확인."""
        return self._running

    def get_history(self) -> list[FailoverEvent]:
        """페일오버 히스토리 반환."""
        return list(self._history)

    def get_stats(self) -> dict[str, Any]:
        """통계 반환."""
        with self._lock:
            return {
                "state": self._state.value,
                "current_primary": self._current_primary,
                "last_failover_time": self._last_failover_time,
                "failover_count": len(self._history),
            }
