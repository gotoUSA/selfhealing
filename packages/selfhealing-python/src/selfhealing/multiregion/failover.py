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

import json
import ssl
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    get_multiregion_settings,
)
from selfhealing.multiregion.health_monitor import (
    RegionHealth,
    RegionHealthMonitor,
    RegionHealthStatus,
)

logger = structlog.get_logger()


class FailoverState(str, Enum):
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

        # 트래픽 라우팅 어댑터 (외부 주입 또는 ProviderRegistry 조회)
        self._traffic_adapter: Any | None = None
        # 마지막 라우팅 변경 결과 (롤백용)
        self._last_routing_change: Any | None = None

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
            logger.error("failover")
            return None

        # 현재 Primary 제외
        candidates = [r for r in healthy if r != self._current_primary]

        if not candidates:
            logger.error("failover")
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
            logger.debug(
                "failover.cooldown",
                elapsed=elapsed,
                failover_cooldown_seconds=self._settings.failover_cooldown_seconds,
            )
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
                logger.warning("failover")
                return False

            self._state = FailoverState.FAILOVER_IN_PROGRESS

        logger.warning(
            "failover.executing_failover",
            current_primary=self._current_primary,
            target_region=target_region,
            reason=reason,
        )

        try:
            # 1. Quorum 획득 (Split-brain 방지)
            if self._quorum_witness:
                if not self._quorum_witness.try_acquire_primary():
                    logger.error("failover")
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
                    logger.exception(
                        "failover.callback_error",
                        error=e,
                    )

            # 7. 알림 전송
            self._send_alert(event)

            logger.warning(
                "failover.completed",
                old_primary=old_primary,
                target_region=target_region,
            )
            return True

        except Exception as e:
            logger.exception(
                "failover.failed",
                error=e,
            )
            with self._lock:
                self._state = FailoverState.NORMAL
            return False

    def _update_traffic_routing(self, target_region: str) -> None:
        """
        트래픽 라우팅 업데이트.

        TrafficRoutingAdapter를 통해 DNS/LB 전환을 실행합니다.
        어댑터 미등록 시 LoggingTrafficRoutingAdapter(앱 레벨만)를 사용합니다.

        Args:
            target_region: 대상 리전

        Raises:
            RuntimeError: 라우팅 전환 실패 시
        """
        adapter = self._get_traffic_routing_adapter()
        result = adapter.switch_primary(self._current_primary, target_region)

        if not result.success:
            raise RuntimeError(f"Traffic routing switch failed: {result.details}")

        # 롤백 정보 저장 (복구 시 사용)
        self._last_routing_change = result

        logger.info(
            "failover.traffic_routing_updated",
            current_primary=self._current_primary,
            target_region=target_region,
            routing_result_details=result.details,
        )

    def _get_traffic_routing_adapter(self) -> Any:
        """
        TrafficRoutingAdapter 인스턴스 반환.

        조회 순서:
        1. 외부 주입된 어댑터 (_traffic_adapter)
        2. ProviderRegistry 등록 어댑터
        3. LoggingTrafficRoutingAdapter (기본)
        """
        if self._traffic_adapter is not None:
            return self._traffic_adapter

        # ProviderRegistry에서 조회 시도
        try:
            from selfhealing.factory import ProviderRegistry

            adapter = ProviderRegistry.get_traffic_routing()
            return adapter
        except (ValueError, AttributeError):
            pass

        # 기본 어댑터 사용
        from selfhealing.adapters.traffic_routing.logging_adapter import (
            LoggingTrafficRoutingAdapter,
        )

        return LoggingTrafficRoutingAdapter()

    def _verify_data_consistency(self, target_region: str) -> None:
        """
        데이터 정합성 확인.

        확인 항목:
        1. 복제 큐 잔량 (미복제 이벤트 존재 여부)
        2. 핵심 키 정합성 (Emergency 모드, 시스템 제어 상태)

        이슈가 있어도 failover는 계속 진행합니다 (가용성 우선).
        이슈 정보는 경고 로그로 기록됩니다.

        Args:
            target_region: 대상 리전
        """
        issues: list[str] = []

        # 1. 복제 큐 잔량 확인
        try:
            from selfhealing.multiregion.replicator import RegionReplicator

            replicator = RegionReplicator(settings=self._settings)
            queue_size = replicator.get_queue_size()
            if queue_size > 0:
                issues.append(f"Replication queue not empty: {queue_size} pending")
                logger.warning(
                    "failover.events_pending_replication",
                    queue_size=queue_size,
                    target_region=target_region,
                )
        except Exception as e:
            logger.warning(
                "failover.cannot_check_replication_queue",
                error=e,
            )

        # 2. 핵심 키 정합성 — 로컬 vs 타겟 리전
        try:
            from selfhealing.core.state_backend import get_state_backend

            local_backend = get_state_backend()

            critical_keys = [
                "emergency_mode",
                "system_control",
            ]

            for key in critical_keys:
                local_val = local_backend.get(key)
                # 타겟 리전 값은 API를 통해 조회
                remote_val = self._fetch_remote_state(target_region, key)
                if local_val != remote_val:
                    issues.append(f"Key '{key}' mismatch: " f"local={local_val}, remote={remote_val}")
        except Exception as e:
            logger.warning(
                "failover.consistency_check_partial",
                error=e,
            )

        # 3. 결과 로깅
        if issues:
            logger.warning(
                "failover.data_consistency_issues",
                target_region=target_region,
                consistency_issues="; ".join(issues),
            )
        else:
            logger.info(
                "failover.data_consistency_verified",
                target_region=target_region,
            )

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        """
        HTTP API 호출용 SSL Context 생성.

        config.py의 TLS 설정(tls_enabled, tls_cert_path, tls_key_path,
        tls_ca_path, tls_verify_hostname)을 읽어 mTLS 컨텍스트를 생성합니다.

        TLS 비활성화 시 None을 반환합니다 (일반 HTTP).

        Note:
            SecureRedisClient._create_ssl_context()와 동일한 설정을 사용하지만,
            urllib.request.urlopen()에 전달하기 위한 별도 메서드입니다.
            네이밍을 _build로 하여 _create와 구분합니다.
        """
        if not self._settings.tls_enabled:
            return None

        try:
            context = ssl.create_default_context(
                cafile=self._settings.tls_ca_path,
            )

            # 클라이언트 인증서 로드 (mTLS)
            if self._settings.tls_cert_path and self._settings.tls_key_path:
                context.load_cert_chain(
                    certfile=self._settings.tls_cert_path,
                    keyfile=self._settings.tls_key_path,
                )

            # 호스트명 검증 설정
            # tls_verify_hostname=False 시에도 인증서 체인 검증은 유지 (CERT_REQUIRED)
            context.check_hostname = self._settings.tls_verify_hostname
            context.verify_mode = ssl.CERT_REQUIRED

            return context
        except FileNotFoundError as e:
            logger.warning(
                "failover.tls_certificate_found",
                error=e,
            )
            return None
        except ssl.SSLError as e:
            logger.exception(
                "failover.ssl_context_creation_failed",
                error=e,
            )
            return None

    def _fetch_remote_state(self, region: str, key: str) -> Any:
        """
        타겟 리전의 상태 값 조회 (API 경유).

        mTLS가 활성화된 경우 _build_ssl_context()로 생성한
        SSL Context를 urllib.request.urlopen()에 전달합니다.

        Args:
            region: 타겟 리전 이름
            key: 상태 키 (예: "emergency_mode")

        Returns:
            상태 값 (dict) 또는 None (실패 시)
        """
        endpoints = self._settings.get_peer_endpoints()
        ssl_ctx = self._build_ssl_context()

        for ep in endpoints:
            if ep.region == region and ep.api_endpoint:
                try:
                    url = f"{ep.api_endpoint}/api/v1/state/{key}/"
                    req = urllib.request.Request(
                        url,
                        method="GET",
                        headers={"Accept": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=5, context=ssl_ctx) as resp:
                        if resp.status == 200:
                            return json.loads(resp.read())
                except Exception as e:
                    logger.warning(
                        "failover.failed_fetch_state",
                        state_fetch_key=key,
                        target_region=region,
                        error=e,
                    )
        return None

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
            logger.warning("failover")
        except Exception as e:
            logger.exception(
                "failover.alert_error",
                error=e,
            )

    def _check_and_failover(self) -> None:
        """건강 상태 확인 및 자동 페일오버."""
        all_health = self._health_monitor.get_all_health_states()

        if self._settings.is_primary():
            # Primary: 피어 리전 장애 감시 (기존 동작 유지)
            for region, health in all_health.items():
                if health.status == RegionHealthStatus.UNREACHABLE:
                    logger.warning(
                        "failover.peer_region_unreachable",
                        target_region=region,
                    )
        else:
            # Secondary: Primary 건강 감시 → 승격 시도
            self._check_primary_and_promote(all_health)

    def _check_primary_and_promote(self, all_health: dict[str, RegionHealth]) -> None:
        """
        Secondary 리전에서 Primary 장애 감지 시 승격을 시도.

        승격 조건:
        1. Primary 리전이 UNREACHABLE 상태
        2. QuorumWitness 락 획득 성공 (Split-brain 방지)
        3. 페일오버 쿨다운 미초과

        QuorumWitness.try_acquire_primary()는 DynamoDB Global Table의
        조건부 쓰기(ConditionExpression)로 단 하나의 리전만 승격을 보장한다.
        """
        primary_region = self._current_primary

        # Primary 상태가 UNREACHABLE인지 확인
        primary_health = all_health.get(primary_region)
        if primary_health is None:
            return
        if primary_health.status != RegionHealthStatus.UNREACHABLE:
            return

        # 쿨다운 확인
        if not self._can_failover():
            return

        # Quorum 획득 시도 (Split-brain 방지)
        if not self._quorum_witness:
            logger.warning("failover")
            return

        if not self._quorum_witness.try_acquire_primary():
            logger.info("failover")
            return

        # 승격 성공 → 페일오버 실행
        logger.warning(
            "failover.secondary_promoting_taking_over",
            current_region=self._settings.current_region,
            primary_region=primary_region,
        )
        self._execute_failover(
            target_region=self._settings.current_region,
            reason=f"primary_unreachable:{primary_region}",
        )

    def _run_loop(self) -> None:
        """모니터링 루프."""
        while self._running:
            try:
                self._check_and_failover()
            except Exception as e:
                logger.exception(
                    "failover.loop_error",
                    error=e,
                )

            self._stop_event.wait(self._settings.health_check_interval_seconds)
            if self._stop_event.is_set():
                break

    def start(self) -> None:
        """페일오버 모니터링 시작."""
        if not self._settings.enabled or not self._settings.failover_enabled:
            logger.info("failover_disabled")
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
        logger.info("failover_started")

    def stop(self) -> None:
        """페일오버 모니터링 중지."""
        self._running = False
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=2.0)
        self._health_monitor.stop()
        logger.info("failover_stopped")

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
