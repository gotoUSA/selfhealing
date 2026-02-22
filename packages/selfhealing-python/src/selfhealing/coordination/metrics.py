"""
Leader Election Prometheus Metrics.

리더 선출 상태 모니터링을 위한 Prometheus 메트릭.
"""

from __future__ import annotations

import structlog
import time

logger = structlog.get_logger()


# Prometheus 클라이언트 가용성 확인
try:
    from prometheus_client import Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

    # Dummy 메트릭 클래스 (prometheus_client가 없을 때)
    class DummyMetric:
        """Prometheus 클라이언트가 없을 때 사용하는 더미 메트릭."""

        def labels(self, **kwargs):
            return self

        def inc(self, amount=1):
            pass

        def set(self, value):
            pass

        def observe(self, value):
            pass

    def Counter(*args, **kwargs):
        return DummyMetric()

    def Gauge(*args, **kwargs):
        return DummyMetric()

    def Histogram(*args, **kwargs):
        return DummyMetric()


# =============================================================================
# Prometheus Metrics 정의
# =============================================================================

# 리더 여부 (1 = 리더, 0 = 팔로워)
LEADER_ELECTOR_IS_LEADER = Gauge(
    "selfhealing_leader_elector_is_leader",
    "현재 노드가 리더인지 여부 (1=리더, 0=팔로워)",
    ["resource_name", "node_id"],
)

# Lease 만료 예정 타임스탬프 (Unix epoch)
LEADER_ELECTOR_LEASE_EXPIRE_TIMESTAMP = Gauge(
    "selfhealing_leader_elector_lease_expire_timestamp",
    "현재 Lease 만료 예정 Unix 타임스탬프",
    ["resource_name"],
)

# Lease 갱신 실패 횟수
LEADER_ELECTOR_RENEW_ERRORS_TOTAL = Counter(
    "selfhealing_leader_elector_renew_errors_total",
    "Lease 갱신 실패 총 횟수",
    ["resource_name", "error_type"],
)

# 리더 선출 횟수
LEADER_ELECTOR_ELECTIONS_TOTAL = Counter(
    "selfhealing_leader_elector_elections_total",
    "리더 선출 총 횟수",
    ["resource_name", "node_id"],
)

# 리더십 유지 시간 (Histogram)
LEADER_ELECTOR_LEADERSHIP_DURATION_SECONDS = Histogram(
    "selfhealing_leader_elector_leadership_duration_seconds",
    "리더십 유지 시간 (초)",
    ["resource_name"],
    buckets=[10, 30, 60, 300, 600, 1800, 3600, 7200],
)


# =============================================================================
# Metrics Helper Class
# =============================================================================


class LeaderElectorMetrics:
    """
    Leader Elector 메트릭 헬퍼.

    LeaderElector에서 사용하는 메트릭 업데이트를 캡슐화.
    """

    def __init__(self, resource_name: str, node_id: str):
        """
        메트릭 헬퍼 초기화.

        Args:
            resource_name: 리소스 이름
            node_id: 노드 ID
        """
        self._resource_name = resource_name
        self._node_id = node_id
        self._leadership_start_time: float | None = None

    def set_leader(self, is_leader: bool) -> None:
        """리더 상태 설정."""
        LEADER_ELECTOR_IS_LEADER.labels(
            resource_name=self._resource_name,
            node_id=self._node_id,
        ).set(1 if is_leader else 0)

    def set_lease_expire_timestamp(self, expire_timestamp: float) -> None:
        """Lease 만료 시간 설정."""
        LEADER_ELECTOR_LEASE_EXPIRE_TIMESTAMP.labels(
            resource_name=self._resource_name,
        ).set(expire_timestamp)

    def record_renew_error(self, error_type: str = "unknown") -> None:
        """Lease 갱신 실패 기록."""
        LEADER_ELECTOR_RENEW_ERRORS_TOTAL.labels(
            resource_name=self._resource_name,
            error_type=error_type,
        ).inc()

    def record_election(self) -> None:
        """리더 선출 기록."""
        LEADER_ELECTOR_ELECTIONS_TOTAL.labels(
            resource_name=self._resource_name,
            node_id=self._node_id,
        ).inc()
        self._leadership_start_time = time.time()

    def record_leadership_end(self) -> None:
        """리더십 종료 기록 (유지 시간 측정)."""
        if self._leadership_start_time is not None:
            duration = time.time() - self._leadership_start_time
            LEADER_ELECTOR_LEADERSHIP_DURATION_SECONDS.labels(
                resource_name=self._resource_name,
            ).observe(duration)
            self._leadership_start_time = None
