"""
Region Health Monitor - 리전 건강 상태 모니터링.

모든 피어 리전의 건강 상태를 주기적으로 확인하고
장애를 감지합니다.

체크 항목:
- API 엔드포인트 응답
- 복제 지연 (Kafka/Redis Lag)
- 연속 실패 횟수

상태:
- HEALTHY: 정상
- DEGRADED: 복제 지연 또는 일시적 장애
- UNHEALTHY: 건강 체크 실패
- UNREACHABLE: 연속 실패로 도달 불가
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    RegionEndpoint,
    get_multiregion_settings,
)

logger = structlog.get_logger()


class RegionHealthStatus(str, Enum):
    """
    리전 건강 상태.

    상태 전이:
    - HEALTHY → DEGRADED: 복제 지연 또는 간헐적 오류
    - DEGRADED → HEALTHY: 복구됨
    - DEGRADED → UNHEALTHY: 건강 체크 실패
    - UNHEALTHY → UNREACHABLE: 연속 실패 임계치 초과
    - UNREACHABLE → HEALTHY: 복구 확인됨
    """

    HEALTHY = "healthy"
    """정상 상태."""

    DEGRADED = "degraded"
    """저하 상태 (복제 지연 또는 일시적 장애)."""

    UNHEALTHY = "unhealthy"
    """비정상 상태 (건강 체크 실패)."""

    UNREACHABLE = "unreachable"
    """도달 불가 (연속 실패, 페일오버 대상)."""


@dataclass
class RegionHealth:
    """
    리전 건강 정보.

    Attributes:
        region: 리전 이름
        status: 건강 상태
        latency_ms: API 응답 시간 (ms)
        last_check: 마지막 체크 시각
        consecutive_failures: 연속 실패 횟수
        details: 상세 정보 (오류 메시지, lag 정보 등)
    """

    region: str
    """리전 이름."""

    status: RegionHealthStatus
    """건강 상태."""

    latency_ms: float
    """응답 시간 (ms)."""

    last_check: datetime
    """마지막 체크 시각."""

    consecutive_failures: int = 0
    """연속 실패 횟수."""

    details: dict[str, Any] = field(default_factory=dict)
    """상세 정보 (오류, lag 등)."""


class RegionHealthMonitor:
    """
    Region Health Monitor.

    모든 피어 리전의 건강 상태를 주기적으로 모니터링합니다.

    체크 항목:
    1. API 엔드포인트 응답 여부
    2. 복제 지연 (Kafka Lag, Redis Lag)

    사용 예:
        monitor = RegionHealthMonitor()

        # 모니터링 시작
        monitor.start()

        # 정상 리전 조회
        healthy = monitor.get_healthy_regions()

        # 특정 리전 상태 확인
        health = monitor.get_region_health("us-east-1")

        # 모니터링 중지
        monitor.stop()
    """

    # Lag 임계치 (ms)
    REPLICATION_LAG_WARNING_MS = 500
    """복제 지연 경고 임계치 (ms)."""

    REPLICATION_LAG_CRITICAL_MS = 2000
    """복제 지연 치명적 임계치 (ms). 초과 시 DEGRADED."""

    def __init__(
        self,
        settings: MultiRegionSettings | None = None,
    ):
        """
        초기화.

        Args:
            settings: Multi-Region 설정 (None이면 기본 설정 사용)
        """
        self._settings = settings or get_multiregion_settings()
        self._lock = threading.RLock()
        self._health_states: dict[str, RegionHealth] = {}
        self._running = False
        self._worker: threading.Thread | None = None
        self._heartbeat_worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 초기 상태 설정
        for endpoint in self._settings.get_peer_endpoints():
            self._health_states[endpoint.region] = RegionHealth(
                region=endpoint.region,
                status=RegionHealthStatus.HEALTHY,
                latency_ms=0,
                last_check=datetime.now(timezone.utc),
            )

    def check_region(self, endpoint: RegionEndpoint) -> RegionHealth:
        """
        단일 리전 건강 체크.

        체크 항목:
        1. API 엔드포인트 응답
        2. 복제 지연 (선택적)

        Args:
            endpoint: 리전 엔드포인트

        Returns:
            RegionHealth: 건강 상태
        """
        start = time.time()

        # 1. API 헬스 체크
        api_health = self._check_api_health(endpoint, start)
        if api_health.status in (RegionHealthStatus.UNHEALTHY, RegionHealthStatus.UNREACHABLE):
            return api_health

        # 2. 복제 지연 체크 (API 정상일 때만)
        kafka_lag = self._check_kafka_lag(endpoint)
        redis_lag = self._check_redis_lag(endpoint)
        max_lag = max(kafka_lag, redis_lag)

        # 3. Lag 기반 상태 결정
        if max_lag > self.REPLICATION_LAG_CRITICAL_MS:
            return RegionHealth(
                region=endpoint.region,
                status=RegionHealthStatus.DEGRADED,
                latency_ms=api_health.latency_ms,
                last_check=datetime.now(timezone.utc),
                consecutive_failures=0,
                details={
                    "kafka_lag_ms": kafka_lag,
                    "redis_lag_ms": redis_lag,
                    "reason": "replication_lag_critical",
                },
            )
        elif max_lag > self.REPLICATION_LAG_WARNING_MS:
            logger.warning(
                "region_health.replication_lag_warning_ms",
                endpoint=endpoint.region,
                kafka_lag=kafka_lag,
                redis_lag=redis_lag,
            )

        # 4. 정상
        return RegionHealth(
            region=endpoint.region,
            status=RegionHealthStatus.HEALTHY,
            latency_ms=api_health.latency_ms,
            last_check=datetime.now(timezone.utc),
            consecutive_failures=0,
            details={
                "kafka_lag_ms": kafka_lag,
                "redis_lag_ms": redis_lag,
            },
        )

    def _check_api_health(
        self,
        endpoint: RegionEndpoint,
        start_time: float,
    ) -> RegionHealth:
        """
        API 엔드포인트 건강 체크.

        Args:
            endpoint: 리전 엔드포인트
            start_time: 시작 시각

        Returns:
            RegionHealth: API 건강 상태
        """
        try:
            health_url = f"{endpoint.api_endpoint}/health/"
            req = urllib.request.Request(
                health_url,
                method="GET",
                headers={"Accept": "application/json"},
            )

            with urllib.request.urlopen(
                req,
                timeout=self._settings.health_check_timeout_seconds,
            ) as resp:
                latency_ms = (time.time() - start_time) * 1000

                if resp.status == 200:
                    return RegionHealth(
                        region=endpoint.region,
                        status=RegionHealthStatus.HEALTHY,
                        latency_ms=latency_ms,
                        last_check=datetime.now(timezone.utc),
                        consecutive_failures=0,
                    )
                else:
                    return RegionHealth(
                        region=endpoint.region,
                        status=RegionHealthStatus.DEGRADED,
                        latency_ms=latency_ms,
                        last_check=datetime.now(timezone.utc),
                        details={"status_code": resp.status},
                    )
        except urllib.error.URLError as e:
            latency_ms = (time.time() - start_time) * 1000

            with self._lock:
                prev = self._health_states.get(endpoint.region)
                failures = (prev.consecutive_failures + 1) if prev else 1

            status = (
                RegionHealthStatus.UNREACHABLE
                if failures >= self._settings.unhealthy_threshold
                else RegionHealthStatus.UNHEALTHY
            )

            return RegionHealth(
                region=endpoint.region,
                status=status,
                latency_ms=latency_ms,
                last_check=datetime.now(timezone.utc),
                consecutive_failures=failures,
                details={"error": str(e)},
            )
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000

            with self._lock:
                prev = self._health_states.get(endpoint.region)
                failures = (prev.consecutive_failures + 1) if prev else 1

            return RegionHealth(
                region=endpoint.region,
                status=RegionHealthStatus.UNHEALTHY,
                latency_ms=latency_ms,
                last_check=datetime.now(timezone.utc),
                consecutive_failures=failures,
                details={"error": str(e)},
            )

    def _check_kafka_lag(self, endpoint: RegionEndpoint) -> float:
        """
        Kafka Consumer Lag 확인.

        Args:
            endpoint: 리전 엔드포인트

        Returns:
            Lag (ms). 오류 시 0 반환 (무시).
        """
        try:
            metrics_url = f"{endpoint.api_endpoint}/metrics/replication_lag"
            req = urllib.request.Request(
                metrics_url,
                method="GET",
                headers={"Accept": "application/json"},
            )

            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read())
                    return float(data.get("kafka_lag_ms", 0))
                return 0.0

        except Exception as e:
            logger.debug(
                "region_health.kafka_lag_check_skipped",
                error=e,
            )
            return 0.0

    def _check_redis_lag(self, endpoint: RegionEndpoint) -> float:
        """
        Redis Replication Lag 확인.

        Args:
            endpoint: 리전 엔드포인트

        Returns:
            Lag (ms). 오류 시 0 반환 (무시).
        """
        try:
            metrics_url = f"{endpoint.api_endpoint}/metrics/replication_lag"
            req = urllib.request.Request(
                metrics_url,
                method="GET",
                headers={"Accept": "application/json"},
            )

            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read())
                    return float(data.get("redis_lag_ms", 0))
                return 0.0

        except Exception as e:
            logger.debug(
                "region_health.redis_lag_check_skipped",
                error=e,
            )
            return 0.0

    def check_all_regions(self) -> dict[str, RegionHealth]:
        """
        모든 피어 리전 건강 체크.

        Returns:
            리전별 건강 상태 딕셔너리
        """
        results = {}

        for endpoint in self._settings.get_peer_endpoints():
            try:
                health = self.check_region(endpoint)
                results[endpoint.region] = health
            except Exception as e:
                logger.exception(
                    "region_health.check_error",
                    endpoint=endpoint.region,
                    error=e,
                )
                results[endpoint.region] = RegionHealth(
                    region=endpoint.region,
                    status=RegionHealthStatus.UNHEALTHY,
                    latency_ms=0,
                    last_check=datetime.now(timezone.utc),
                    details={"error": str(e)},
                )

        with self._lock:
            self._health_states = results

        return results

    def get_healthy_regions(self) -> list[str]:
        """
        정상 리전 목록 반환.

        Returns:
            HEALTHY 상태인 리전 이름 목록
        """
        with self._lock:
            return [region for region, health in self._health_states.items() if health.status == RegionHealthStatus.HEALTHY]

    def get_region_health(self, region: str) -> RegionHealth | None:
        """
        특정 리전 건강 정보 반환.

        Args:
            region: 리전 이름

        Returns:
            RegionHealth 또는 None
        """
        with self._lock:
            return self._health_states.get(region)

    def get_best_region(self) -> str | None:
        """
        가장 좋은 리전 반환.

        기준: HEALTHY 상태 중 latency가 가장 낮은 리전

        Returns:
            리전 이름 또는 None
        """
        with self._lock:
            healthy = [
                (region, health)
                for region, health in self._health_states.items()
                if health.status == RegionHealthStatus.HEALTHY
            ]

            if not healthy:
                return None

            healthy.sort(key=lambda x: x[1].latency_ms)
            return healthy[0][0]

    def is_region_healthy(self, region: str) -> bool:
        """
        리전이 정상인지 확인.

        Args:
            region: 리전 이름

        Returns:
            True if 정상
        """
        with self._lock:
            health = self._health_states.get(region)
            return health is not None and health.status == RegionHealthStatus.HEALTHY

    def get_all_health_states(self) -> dict[str, RegionHealth]:
        """
        모든 건강 상태 반환.

        Returns:
            리전별 건강 상태 딕셔너리 복사본
        """
        with self._lock:
            return dict(self._health_states)

    def _run_loop(self) -> None:
        """모니터링 루프."""
        while self._running:
            try:
                self.check_all_regions()
            except Exception as e:
                logger.exception(
                    "region_health.loop_error",
                    error=e,
                )

            self._stop_event.wait(self._settings.health_check_interval_seconds)
            if self._stop_event.is_set():
                break

    def _mark_unhealthy(self, region: str) -> None:
        """
        특정 리전을 UNREACHABLE로 즉시 마킹.

        Keyspace Notification 또는 Push 이벤트로 감지된 장애를 반영합니다.
        다음 check_all_regions() 루프에서 정상 확인 시 자동 복구됩니다.

        Args:
            region: UNREACHABLE로 마킹할 리전 이름
        """
        with self._lock:
            if region in self._health_states:
                self._health_states[region] = RegionHealth(
                    region=region,
                    status=RegionHealthStatus.UNREACHABLE,
                    latency_ms=0,
                    last_check=datetime.now(timezone.utc),
                    consecutive_failures=self._settings.unhealthy_threshold,
                    details={"reason": "heartbeat_expired"},
                )
                logger.warning(
                    "region_health.marked_unreachable_heartbeat_expired",
                    region=region,
                )

    def _subscribe_heartbeat_expiry(self) -> None:
        """
        Redis Keyspace Notification으로 하트비트 만료 감지.

        CONFIG SET 권한이 없는 관리형 Redis(ElastiCache, Memorystore 등)에서는
        파라미터 그룹에서 미리 notify-keyspace-events = Ex 를 설정해야 합니다.
        CONFIG SET 실패 시에도 구독을 시도합니다 (이미 설정되어 있을 수 있음).
        """
        try:
            import redis as redis_lib

            from selfhealing.core.state_backend import _get_config

            redis_url = _get_config("SELFHEALING_REDIS_URL", "redis://localhost:6379/0")
            client = redis_lib.from_url(redis_url, decode_responses=True)

            # 관리형 Redis 대응: CONFIG SET 시도 후 실패 시 로그만 남기고 구독 시도
            try:
                client.config_set("notify-keyspace-events", "Ex")
                logger.info("region_health.keyspace_notifications_enabled_notify")
            except redis_lib.exceptions.ResponseError as e:
                error_msg = str(e).lower()
                if "unknown command" in error_msg or "permission" in error_msg:
                    logger.warning("region_health.config_set_permitted_using")
                else:
                    raise  # 다른 ResponseError는 상위로 전파

            pubsub = client.pubsub()
            pubsub.psubscribe("__keyevent@*__:expired")

            for message in pubsub.listen():
                if not self._running:
                    break
                if message["type"] == "pmessage":
                    key = message["data"]
                    if key.startswith("selfhealing:state:multiregion:heartbeat:"):
                        region = key.split(":")[-1]
                        logger.warning(
                            "region_health.heartbeat_expired",
                            region=region,
                        )
                        self._mark_unhealthy(region)

        except ImportError:
            logger.warning("region_health.redis_package_installed_keyspace")
        except Exception as e:
            logger.warning(
                "region_health.keyspace_notification_unavailable",
                error=e,
            )

    def start(self) -> None:
        """
        모니터링 시작.

        백그라운드 스레드에서 주기적으로 건강 체크를 실행합니다.
        Redis 백엔드 사용 시 하트비트 만료 Keyspace Notification도 구독합니다.
        """
        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        self._worker = threading.Thread(
            target=self._run_loop,
            name="RegionHealthMonitor",
            daemon=True,
        )
        self._worker.start()

        # 하트비트 만료 구독 스레드 시작 (Redis 백엔드 사용 시)
        self._heartbeat_worker = threading.Thread(
            target=self._subscribe_heartbeat_expiry,
            name="RegionHeartbeatSubscriber",
            daemon=True,
        )
        self._heartbeat_worker.start()

        logger.info("started")

    def stop(self) -> None:
        """
        모니터링 중지.

        백그라운드 스레드를 종료합니다.
        """
        self._running = False
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=2.0)
        logger.info("stopped")

    def is_running(self) -> bool:
        """모니터링 실행 중인지 확인."""
        return self._running
