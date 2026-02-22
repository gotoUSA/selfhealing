"""
Recovery Circuit Breaker.

복구 진행 중 지표가 다시 악화되면 즉시 복구를 중단하고
Emergency 상태로 회귀(재-에스컬레이션)합니다.

Features:
- 복구 중 에러율/성공률 모니터링
- 임계값 초과 시 회로 차단 (Trip)
- 재-에스컬레이션으로 Emergency 복귀
- 연속 실패 카운터로 영구 중단 결정

Code reference:
    circuit_breaker.py (CircuitBreaker 패턴)
    anti_flapping.py (AntiFlappingGuard 임계값 패턴)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.1
"""

from __future__ import annotations

import structlog
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from selfhealing.settings import (
    RecoveryCircuitBreakerSettings,
    get_recovery_circuit_breaker_settings,
)

logger = structlog.get_logger()


class RecoveryCircuitState(str, Enum):
    """
    복구 회로 차단기 상태.

    CLOSED: 정상 - 복구 진행 허용
    OPEN: 차단 - 복구 중단, 재-에스컬레이션 필요
    HALF_OPEN: 반개방 - 테스트 복구 진행 중
    """

    CLOSED = "closed"
    """정상 상태: 복구 진행 허용."""

    OPEN = "open"
    """차단 상태: 복구 중단, 재-에스컬레이션 필요."""

    HALF_OPEN = "half_open"
    """반개방 상태: 테스트 복구 진행 중."""


@dataclass
class RecoveryCircuitBreakerConfig:
    """
    복구 회로 차단기 설정.

    Code reference:
        circuit_breaker.py (CircuitBreakerConfig 패턴)
    """

    # 에러율 임계값 (이 값 초과 시 트립)
    error_rate_threshold: float = 0.15
    """15% 이상 에러율 시 차단."""

    # 샘플링 윈도우 (초)
    sampling_window_seconds: int = 60
    """최근 60초 데이터로 판단."""

    # 최소 샘플 수 (이 값 이상 수집 후 판단)
    min_samples: int = 10
    """최소 10개 요청 후 판단."""

    # 차단 유지 시간 (초)
    open_duration_seconds: int = 300
    """차단 후 5분간 유지."""

    # 반개방 상태에서 허용 요청 수
    half_open_max_requests: int = 5
    """반개방 시 5개 요청으로 테스트."""

    # 연속 트립 횟수 (이 값 초과 시 영구 중단)
    max_consecutive_trips: int = 3
    """3회 연속 트립 시 영구 중단."""

    # 재-에스컬레이션 활성화 여부
    re_escalation_enabled: bool = True
    """트립 시 Emergency 레벨로 재-에스컬레이션."""

    # 재-에스컬레이션 대상 레벨
    re_escalation_level: str = "LEVEL_3"
    """재-에스컬레이션 시 전환할 레벨."""

    @classmethod
    def from_settings(
        cls, settings: RecoveryCircuitBreakerSettings | None = None
    ) -> RecoveryCircuitBreakerConfig:
        """
        RecoveryCircuitBreakerSettings에서 Config 생성.

        Args:
            settings: Pydantic Settings 인스턴스 (None이면 기본값 사용)

        Returns:
            RecoveryCircuitBreakerConfig 인스턴스
        """
        s = settings or get_recovery_circuit_breaker_settings()
        return cls(
            error_rate_threshold=s.error_rate_threshold,
            sampling_window_seconds=s.sampling_window_seconds,
            min_samples=s.min_samples,
            open_duration_seconds=s.open_duration_seconds,
            half_open_max_requests=s.half_open_max_requests,
            max_consecutive_trips=s.max_consecutive_trips,
            re_escalation_enabled=s.re_escalation_enabled,
            re_escalation_level=s.re_escalation_level,
        )


@dataclass
class RecoveryMetricsSnapshot:
    """
    복구 중 메트릭 스냅샷.

    회로 차단기가 판단에 사용하는 메트릭.
    """

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """스냅샷 시각."""

    total_requests: int = 0
    """총 요청 수."""

    success_count: int = 0
    """성공 요청 수."""

    failure_count: int = 0
    """실패 요청 수."""

    error_rate: float = 0.0
    """에러율 (0.0 ~ 1.0)."""

    latency_p95_ms: float = 0.0
    """P95 레이턴시 (ms)."""

    namespace: str = ""
    """네임스페이스."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "error_rate": self.error_rate,
            "latency_p95_ms": self.latency_p95_ms,
            "namespace": self.namespace,
        }


class RecoveryCircuitBreaker:
    """
    복구 회로 차단기.

    복구 진행 중 시스템 지표가 다시 악화되면 즉시 복구를 중단하고
    Emergency 상태로 재-에스컬레이션합니다.

    이는 불완전한 복구로 인한 이차 장애를 방지합니다.

    Pattern source:
        circuit_breaker.py (CircuitBreaker 패턴)
        anti_flapping.py (히스테리시스 패턴)

    Usage:
        config = RecoveryCircuitBreakerConfig(error_rate_threshold=0.15)
        breaker = RecoveryCircuitBreaker(config=config)

        # 복구 시작 시
        breaker.reset("global")

        # 주기적 모니터링
        metrics = get_current_metrics()
        snapshot = RecoveryMetricsSnapshot(
            total_requests=metrics["total"],
            failure_count=metrics["failures"],
            error_rate=metrics["error_rate"],
        )

        result = breaker.check_and_trip("global", snapshot)
        if result["tripped"]:
            # 복구 중단 및 재-에스컬레이션
            recovery_coordinator.abort_recovery("global", result["reason"])

    Reference:
        77_RECOVERY_COORDINATOR.md#8.1
    """

    def __init__(
        self,
        config: RecoveryCircuitBreakerConfig | None = None,
        metrics_provider: Callable[[str], RecoveryMetricsSnapshot] | None = None,
    ):
        """
        Args:
            config: 회로 차단기 설정
            metrics_provider: 메트릭 제공 함수 (namespace -> snapshot)
        """
        self._config = config or RecoveryCircuitBreakerConfig()
        self._metrics_provider = metrics_provider

        # 네임스페이스별 상태
        self._states: dict[str, RecoveryCircuitState] = {}
        self._trip_counts: dict[str, int] = {}  # 연속 트립 횟수
        self._last_trip_at: dict[str, datetime] = {}  # 마지막 트립 시각
        self._snapshots: dict[str, list[RecoveryMetricsSnapshot]] = (
            {}
        )  # 메트릭 히스토리

        # 반개방 상태 요청 카운터
        self._half_open_requests: dict[str, int] = {}
        self._half_open_failures: dict[str, int] = {}

        self._lock = threading.RLock()

    def get_state(self, namespace: str) -> RecoveryCircuitState:
        """
        현재 회로 상태 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            현재 회로 상태
        """
        with self._lock:
            # OPEN 상태에서 시간 경과 확인
            if self._states.get(namespace) == RecoveryCircuitState.OPEN:
                if self._should_transition_to_half_open(namespace):
                    self._states[namespace] = RecoveryCircuitState.HALF_OPEN
                    self._half_open_requests[namespace] = 0
                    self._half_open_failures[namespace] = 0
                    logger.info(
                        f"[RecoveryCircuitBreaker] Transitioned to HALF_OPEN: "
                        f"namespace={namespace}"
                    )

            return self._states.get(namespace, RecoveryCircuitState.CLOSED)

    def check_and_trip(
        self,
        namespace: str,
        snapshot: RecoveryMetricsSnapshot | None = None,
    ) -> dict[str, Any]:
        """
        메트릭 확인 및 필요시 회로 차단.

        Args:
            namespace: 네임스페이스
            snapshot: 현재 메트릭 스냅샷 (없으면 provider 사용)

        Returns:
            {
                "tripped": bool,  # 이번 호출로 트립 발생 여부
                "state": str,     # 현재 상태
                "reason": str,    # 트립 사유 (트립 시)
                "should_re_escalate": bool,  # 재-에스컬레이션 필요 여부
                "trip_count": int,  # 연속 트립 횟수
            }
        """
        with self._lock:
            current_state = self.get_state(namespace)

            # 이미 OPEN 상태면 스킵
            if current_state == RecoveryCircuitState.OPEN:
                return {
                    "tripped": False,
                    "state": current_state.value,
                    "reason": "Already in OPEN state",
                    "should_re_escalate": False,
                    "trip_count": self._trip_counts.get(namespace, 0),
                }

            # 스냅샷 획득
            if snapshot is None and self._metrics_provider:
                snapshot = self._metrics_provider(namespace)

            if snapshot is None:
                return {
                    "tripped": False,
                    "state": current_state.value,
                    "reason": "No metrics available",
                    "should_re_escalate": False,
                    "trip_count": self._trip_counts.get(namespace, 0),
                }

            # 스냅샷 저장
            snapshot.namespace = namespace
            if namespace not in self._snapshots:
                self._snapshots[namespace] = []
            self._snapshots[namespace].append(snapshot)
            self._cleanup_old_snapshots(namespace)

            # HALF_OPEN 상태 처리
            if current_state == RecoveryCircuitState.HALF_OPEN:
                return self._handle_half_open(namespace, snapshot)

            # CLOSED 상태에서 임계값 확인
            should_trip, reason = self._should_trip(namespace, snapshot)

            if should_trip:
                return self._trip(namespace, reason)

            return {
                "tripped": False,
                "state": RecoveryCircuitState.CLOSED.value,
                "reason": reason,  # 트립 안 한 이유도 반환
                "should_re_escalate": False,
                "trip_count": self._trip_counts.get(namespace, 0),
            }

    def reset(self, namespace: str) -> None:
        """
        회로 차단기 리셋.

        새 복구 세션 시작 시 호출합니다.

        Args:
            namespace: 네임스페이스
        """
        with self._lock:
            self._states[namespace] = RecoveryCircuitState.CLOSED
            self._trip_counts[namespace] = 0
            self._last_trip_at.pop(namespace, None)
            self._snapshots[namespace] = []
            self._half_open_requests.pop(namespace, None)
            self._half_open_failures.pop(namespace, None)

            logger.info(
                "recovery_circuit_breaker.reset",
                namespace=namespace,
            )

    def force_open(self, namespace: str, reason: str = "") -> None:
        """
        강제 차단.

        외부에서 강제로 회로를 차단합니다.

        Args:
            namespace: 네임스페이스
            reason: 차단 사유
        """
        with self._lock:
            self._states[namespace] = RecoveryCircuitState.OPEN
            self._last_trip_at[namespace] = datetime.now(timezone.utc)
            self._trip_counts[namespace] = self._trip_counts.get(namespace, 0) + 1

            logger.warning(
                f"[RecoveryCircuitBreaker] Force opened: "
                f"namespace={namespace}, reason={reason}"
            )

    def get_status(self, namespace: str) -> dict[str, Any]:
        """
        상태 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            상태 정보
        """
        with self._lock:
            state = self.get_state(namespace)
            trip_count = self._trip_counts.get(namespace, 0)
            last_trip = self._last_trip_at.get(namespace)
            recent_snapshots = self._snapshots.get(namespace, [])[-5:]

            return {
                "namespace": namespace,
                "state": state.value,
                "trip_count": trip_count,
                "max_consecutive_trips": self._config.max_consecutive_trips,
                "is_permanently_open": trip_count >= self._config.max_consecutive_trips,
                "last_trip_at": last_trip.isoformat() if last_trip else None,
                "config": {
                    "error_rate_threshold": self._config.error_rate_threshold,
                    "sampling_window_seconds": self._config.sampling_window_seconds,
                    "open_duration_seconds": self._config.open_duration_seconds,
                    "re_escalation_enabled": self._config.re_escalation_enabled,
                },
                "recent_snapshots": [s.to_dict() for s in recent_snapshots],
            }

    def is_permanently_open(self, namespace: str) -> bool:
        """
        영구 차단 여부 확인.

        연속 트립 횟수가 max_consecutive_trips를 초과하면 영구 차단.
        이 경우 수동 개입 필요.

        Args:
            namespace: 네임스페이스

        Returns:
            영구 차단 여부
        """
        with self._lock:
            trip_count = self._trip_counts.get(namespace, 0)
            return trip_count >= self._config.max_consecutive_trips

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _should_trip(
        self,
        namespace: str,
        snapshot: RecoveryMetricsSnapshot,
    ) -> tuple[bool, str]:
        """트립 여부 판단."""
        # 최소 샘플 확인
        if snapshot.total_requests < self._config.min_samples:
            return False, f"Insufficient samples: {snapshot.total_requests}"

        # 에러율 확인
        if snapshot.error_rate >= self._config.error_rate_threshold:
            return True, (
                f"Error rate {snapshot.error_rate:.2%} >= "
                f"threshold {self._config.error_rate_threshold:.2%}"
            )

        return False, ""

    def _trip(self, namespace: str, reason: str) -> dict[str, Any]:
        """회로 차단 실행."""
        self._states[namespace] = RecoveryCircuitState.OPEN
        self._last_trip_at[namespace] = datetime.now(timezone.utc)
        self._trip_counts[namespace] = self._trip_counts.get(namespace, 0) + 1

        trip_count = self._trip_counts[namespace]
        is_permanent = trip_count >= self._config.max_consecutive_trips
        should_re_escalate = self._config.re_escalation_enabled and not is_permanent

        logger.warning(
            f"[RecoveryCircuitBreaker] TRIPPED: namespace={namespace}, "
            f"reason={reason}, trip_count={trip_count}, "
            f"permanent={is_permanent}"
        )

        return {
            "tripped": True,
            "state": RecoveryCircuitState.OPEN.value,
            "reason": reason,
            "should_re_escalate": should_re_escalate,
            "re_escalation_level": (
                self._config.re_escalation_level if should_re_escalate else None
            ),
            "trip_count": trip_count,
            "is_permanently_open": is_permanent,
        }

    def _should_transition_to_half_open(self, namespace: str) -> bool:
        """OPEN -> HALF_OPEN 전환 여부 판단."""
        last_trip = self._last_trip_at.get(namespace)
        if not last_trip:
            return False

        elapsed = datetime.now(timezone.utc) - last_trip
        return elapsed.total_seconds() >= self._config.open_duration_seconds

    def _handle_half_open(
        self,
        namespace: str,
        snapshot: RecoveryMetricsSnapshot,
    ) -> dict[str, Any]:
        """HALF_OPEN 상태 처리."""
        self._half_open_requests[namespace] = (
            self._half_open_requests.get(namespace, 0) + 1
        )

        # 실패 시 다시 OPEN
        if snapshot.error_rate >= self._config.error_rate_threshold:
            self._half_open_failures[namespace] = (
                self._half_open_failures.get(namespace, 0) + 1
            )

            if self._half_open_failures[namespace] >= 2:  # 2번 이상 실패
                return self._trip(namespace, "Failed in HALF_OPEN state")

        # 충분한 요청 성공 시 CLOSED로 전환
        if self._half_open_requests[namespace] >= self._config.half_open_max_requests:
            if self._half_open_failures.get(namespace, 0) == 0:
                self._states[namespace] = RecoveryCircuitState.CLOSED
                # 연속 트립 카운터 리셋
                self._trip_counts[namespace] = 0

                logger.info(
                    f"[RecoveryCircuitBreaker] Transitioned to CLOSED: "
                    f"namespace={namespace}"
                )

                return {
                    "tripped": False,
                    "state": RecoveryCircuitState.CLOSED.value,
                    "reason": "Recovery successful in HALF_OPEN",
                    "should_re_escalate": False,
                    "trip_count": 0,
                }

        return {
            "tripped": False,
            "state": RecoveryCircuitState.HALF_OPEN.value,
            "reason": f"Testing in HALF_OPEN: {self._half_open_requests[namespace]}/{self._config.half_open_max_requests}",
            "should_re_escalate": False,
            "trip_count": self._trip_counts.get(namespace, 0),
        }

    def _cleanup_old_snapshots(self, namespace: str) -> None:
        """오래된 스냅샷 정리."""
        if namespace not in self._snapshots:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self._config.sampling_window_seconds * 2
        )

        self._snapshots[namespace] = [
            s for s in self._snapshots[namespace] if s.timestamp > cutoff
        ]


# =============================================================================
# Singleton
# =============================================================================

_recovery_circuit_breaker: RecoveryCircuitBreaker | None = None
_breaker_lock = threading.Lock()


def get_recovery_circuit_breaker(
    config: RecoveryCircuitBreakerConfig | None = None,
) -> RecoveryCircuitBreaker:
    """RecoveryCircuitBreaker 싱글톤 반환."""
    global _recovery_circuit_breaker

    if _recovery_circuit_breaker is not None:
        return _recovery_circuit_breaker

    with _breaker_lock:
        if _recovery_circuit_breaker is None:
            _recovery_circuit_breaker = RecoveryCircuitBreaker(config=config)
        return _recovery_circuit_breaker


def reset_recovery_circuit_breaker() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _recovery_circuit_breaker
    with _breaker_lock:
        _recovery_circuit_breaker = None
