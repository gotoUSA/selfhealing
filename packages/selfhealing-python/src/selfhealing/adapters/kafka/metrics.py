"""
Kafka Consumer 메트릭.

Time Lag 및 처리 지연 시간을 추적하여 Consumer 상태를 모니터링합니다.

핵심 메트릭:
- kafka_consumer_time_lag_seconds: 메시지 타임스탬프와 처리 시간의 차이
- kafka_message_processing_latency_seconds: 메시지 처리 지연 시간

Usage:
    from selfhealing.adapters.kafka.metrics import (
        TimeLagTracker,
        record_kafka_message_processed,
    )

    # 메시지 처리 완료 후 기록
    record_kafka_message_processed(
        topic="selfhealing.audit.events",
        partition=0,
        consumer_group="my-group",
        message_timestamp=1704067200.0,
    )
"""

from __future__ import annotations

import structlog
import time
from typing import Any

logger = structlog.get_logger()


# =============================================================================
# Prometheus 메트릭 정의
# =============================================================================

_TIME_LAG_GAUGE = None
_PROCESSING_LATENCY_HISTOGRAM = None
_OFFSET_LAG_GAUGE = None


def _get_time_lag_gauge() -> Any | None:
    """Time Lag Gauge 싱글톤 반환."""
    global _TIME_LAG_GAUGE
    if _TIME_LAG_GAUGE is None:
        try:
            from prometheus_client import Gauge

            _TIME_LAG_GAUGE = Gauge(
                "kafka_consumer_time_lag_seconds",
                "메시지 타임스탬프와 처리 시간의 차이 (초)",
                ["topic", "partition", "consumer_group"],
            )
        except ImportError:
            logger.debug("kafka_metrics.prometheus_client_미설치")
    return _TIME_LAG_GAUGE


def _get_processing_latency_histogram() -> Any | None:
    """처리 지연 시간 Histogram 싱글톤 반환."""
    global _PROCESSING_LATENCY_HISTOGRAM
    if _PROCESSING_LATENCY_HISTOGRAM is None:
        try:
            from prometheus_client import Histogram

            _PROCESSING_LATENCY_HISTOGRAM = Histogram(
                "kafka_message_processing_latency_seconds",
                "메시지 생성부터 처리 완료까지의 지연 시간",
                ["topic"],
                buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
            )
        except ImportError:
            logger.debug("kafka_metrics.prometheus_client_미설치")
    return _PROCESSING_LATENCY_HISTOGRAM


def _get_offset_lag_gauge() -> Any | None:
    """Offset Lag Gauge 싱글톤 반환."""
    global _OFFSET_LAG_GAUGE
    if _OFFSET_LAG_GAUGE is None:
        try:
            from prometheus_client import Gauge

            _OFFSET_LAG_GAUGE = Gauge(
                "kafka_consumer_offset_lag",
                "Consumer 오프셋 지연 (미처리 메시지 수)",
                ["topic", "partition", "consumer_group"],
            )
        except ImportError:
            logger.debug("kafka_metrics.prometheus_client_미설치")
    return _OFFSET_LAG_GAUGE


# =============================================================================
# 메트릭 기록 함수
# =============================================================================


def record_kafka_message_processed(
    topic: str,
    partition: int,
    consumer_group: str,
    message_timestamp: float,
) -> float:
    """
    메시지 처리 완료 시 메트릭 기록.

    Args:
        topic: 토픽 이름
        partition: 파티션 번호
        consumer_group: Consumer 그룹 ID
        message_timestamp: 메시지 타임스탬프 (Unix timestamp, 초)

    Returns:
        계산된 Time Lag (초)
    """
    current_time = time.time()
    time_lag = current_time - message_timestamp

    # Time Lag 기록
    time_lag_gauge = _get_time_lag_gauge()
    if time_lag_gauge:
        time_lag_gauge.labels(
            topic=topic,
            partition=str(partition),
            consumer_group=consumer_group,
        ).set(time_lag)

    # Processing Latency 기록
    latency_histogram = _get_processing_latency_histogram()
    if latency_histogram:
        latency_histogram.labels(topic=topic).observe(time_lag)

    return time_lag


def record_offset_lag(
    topic: str,
    partition: int,
    consumer_group: str,
    lag: int,
) -> None:
    """
    Offset Lag 메트릭 기록.

    Args:
        topic: 토픽 이름
        partition: 파티션 번호
        consumer_group: Consumer 그룹 ID
        lag: 미처리 메시지 수
    """
    offset_lag_gauge = _get_offset_lag_gauge()
    if offset_lag_gauge:
        offset_lag_gauge.labels(
            topic=topic,
            partition=str(partition),
            consumer_group=consumer_group,
        ).set(lag)


# =============================================================================
# TimeLagTracker 클래스
# =============================================================================


class TimeLagTracker:
    """
    Consumer Time Lag 추적기.

    메시지 처리 시 Time Lag을 자동으로 추적하고
    Prometheus 메트릭으로 노출합니다.
    """

    def __init__(
        self,
        consumer_group: str,
        alert_threshold_seconds: float = 60.0,
        critical_threshold_seconds: float = 300.0,
    ):
        """
        TimeLagTracker 초기화.

        Args:
            consumer_group: Consumer 그룹 ID
            alert_threshold_seconds: 경고 임계값 (초)
            critical_threshold_seconds: 심각 임계값 (초)
        """
        self._consumer_group = consumer_group
        self._alert_threshold = alert_threshold_seconds
        self._critical_threshold = critical_threshold_seconds
        self._last_lags: dict[str, float] = {}

    def record_message_processed(
        self,
        topic: str,
        partition: int,
        message_timestamp: float,
    ) -> float:
        """
        메시지 처리 완료 기록.

        Args:
            topic: 토픽 이름
            partition: 파티션 번호
            message_timestamp: 메시지 타임스탬프

        Returns:
            계산된 Time Lag
        """
        time_lag = record_kafka_message_processed(
            topic=topic,
            partition=partition,
            consumer_group=self._consumer_group,
            message_timestamp=message_timestamp,
        )

        # 캐시 저장
        key = f"{topic}:{partition}"
        self._last_lags[key] = time_lag

        # 임계값 체크
        if time_lag >= self._critical_threshold:
            logger.error(
                "time_lag_tracker.심각_time_lag",
                time_lag=time_lag,
                topic=topic,
                partition=partition,
            )
        elif time_lag >= self._alert_threshold:
            logger.warning(
                "time_lag_tracker.경고_time_lag",
                time_lag=time_lag,
                topic=topic,
                partition=partition,
            )

        return time_lag

    def get_last_lag(self, topic: str, partition: int) -> float | None:
        """마지막 Time Lag 조회."""
        key = f"{topic}:{partition}"
        return self._last_lags.get(key)

    def get_all_lags(self) -> dict[str, float]:
        """모든 파티션의 마지막 Time Lag 조회."""
        return dict(self._last_lags)

    def get_max_lag(self) -> float:
        """최대 Time Lag 조회."""
        if not self._last_lags:
            return 0.0
        return max(self._last_lags.values())

    def is_healthy(self) -> bool:
        """
        Consumer 상태 건강성 확인.

        모든 파티션의 Time Lag이 경고 임계값 미만이면 True.
        """
        if not self._last_lags:
            return True
        return all(lag < self._alert_threshold for lag in self._last_lags.values())
