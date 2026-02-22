"""
Kafka 메트릭 단위 테스트.

Time Lag 추적 및 Prometheus 메트릭 기록을 테스트합니다.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from selfhealing.adapters.kafka.metrics import (
    TimeLagTracker,
    record_kafka_message_processed,
    record_offset_lag,
)


class TestRecordKafkaMessageProcessed:
    """record_kafka_message_processed 테스트."""

    def test_returns_time_lag(self) -> None:
        """Time Lag 계산 및 반환."""
        # 1분 전 메시지
        message_timestamp = time.time() - 60

        time_lag = record_kafka_message_processed(
            topic="test.audit.events",
            partition=0,
            consumer_group="test-group",
            message_timestamp=message_timestamp,
        )

        # 약 60초 (오차 허용)
        assert 59 <= time_lag <= 62

    def test_recent_message_low_lag(self) -> None:
        """최근 메시지는 낮은 lag."""
        message_timestamp = time.time() - 0.1

        time_lag = record_kafka_message_processed(
            topic="test.audit.events",
            partition=0,
            consumer_group="test-group",
            message_timestamp=message_timestamp,
        )

        assert time_lag < 1.0

    def test_old_message_high_lag(self) -> None:
        """오래된 메시지는 높은 lag."""
        message_timestamp = time.time() - 3600  # 1시간 전

        time_lag = record_kafka_message_processed(
            topic="test.audit.events",
            partition=0,
            consumer_group="test-group",
            message_timestamp=message_timestamp,
        )

        assert time_lag >= 3599


class TestRecordOffsetLag:
    """record_offset_lag 테스트."""

    def test_record_without_prometheus(self) -> None:
        """prometheus-client 없어도 오류 없음."""
        # 예외 없이 실행되어야 함
        record_offset_lag(
            topic="test.audit.events",
            partition=0,
            consumer_group="test-group",
            lag=100,
        )


class TestTimeLagTracker:
    """TimeLagTracker 단위 테스트."""

    def test_initialization(self) -> None:
        """초기화 확인."""
        tracker = TimeLagTracker(
            consumer_group="test-group",
            alert_threshold_seconds=60.0,
            critical_threshold_seconds=300.0,
        )

        assert tracker._consumer_group == "test-group"
        assert tracker._alert_threshold == 60.0
        assert tracker._critical_threshold == 300.0

    def test_record_message_processed(self) -> None:
        """메시지 처리 기록."""
        tracker = TimeLagTracker(consumer_group="test-group")

        message_timestamp = time.time() - 30

        time_lag = tracker.record_message_processed(
            topic="test.audit.events",
            partition=0,
            message_timestamp=message_timestamp,
        )

        assert 29 <= time_lag <= 32

    def test_get_last_lag(self) -> None:
        """마지막 lag 조회."""
        tracker = TimeLagTracker(consumer_group="test-group")

        message_timestamp = time.time() - 10
        tracker.record_message_processed(
            topic="test.topic",
            partition=2,
            message_timestamp=message_timestamp,
        )

        lag = tracker.get_last_lag("test.topic", 2)

        assert lag is not None
        assert 9 <= lag <= 12

    def test_get_last_lag_not_found(self) -> None:
        """존재하지 않는 파티션 조회."""
        tracker = TimeLagTracker(consumer_group="test-group")

        lag = tracker.get_last_lag("unknown.topic", 99)

        assert lag is None

    def test_get_all_lags(self) -> None:
        """모든 lag 조회."""
        tracker = TimeLagTracker(consumer_group="test-group")

        current_time = time.time()
        tracker.record_message_processed(
            topic="topic1",
            partition=0,
            message_timestamp=current_time - 10,
        )
        tracker.record_message_processed(
            topic="topic1",
            partition=1,
            message_timestamp=current_time - 20,
        )
        tracker.record_message_processed(
            topic="topic2",
            partition=0,
            message_timestamp=current_time - 30,
        )

        all_lags = tracker.get_all_lags()

        assert len(all_lags) == 3
        assert "topic1:0" in all_lags
        assert "topic1:1" in all_lags
        assert "topic2:0" in all_lags

    def test_get_max_lag(self) -> None:
        """최대 lag 조회."""
        tracker = TimeLagTracker(consumer_group="test-group")

        current_time = time.time()
        tracker.record_message_processed(
            topic="topic1",
            partition=0,
            message_timestamp=current_time - 10,
        )
        tracker.record_message_processed(
            topic="topic1",
            partition=1,
            message_timestamp=current_time - 50,  # 최대
        )
        tracker.record_message_processed(
            topic="topic2",
            partition=0,
            message_timestamp=current_time - 20,
        )

        max_lag = tracker.get_max_lag()

        assert 49 <= max_lag <= 52

    def test_get_max_lag_empty(self) -> None:
        """비어있을 때 최대 lag."""
        tracker = TimeLagTracker(consumer_group="test-group")

        assert tracker.get_max_lag() == 0.0

    def test_is_healthy_true(self) -> None:
        """건강한 상태."""
        tracker = TimeLagTracker(
            consumer_group="test-group",
            alert_threshold_seconds=60.0,
        )

        # 임계값 미만
        tracker.record_message_processed(
            topic="topic1",
            partition=0,
            message_timestamp=time.time() - 30,
        )

        assert tracker.is_healthy() is True

    def test_is_healthy_false(self) -> None:
        """비건강 상태."""
        tracker = TimeLagTracker(
            consumer_group="test-group",
            alert_threshold_seconds=60.0,
        )

        # 임계값 초과
        tracker.record_message_processed(
            topic="topic1",
            partition=0,
            message_timestamp=time.time() - 120,  # 2분
        )

        assert tracker.is_healthy() is False

    def test_is_healthy_empty(self) -> None:
        """비어있을 때 건강."""
        tracker = TimeLagTracker(consumer_group="test-group")

        assert tracker.is_healthy() is True

    def test_warning_log_on_high_lag(self) -> None:
        """높은 lag에서 경고 로그."""
        tracker = TimeLagTracker(
            consumer_group="test-group",
            alert_threshold_seconds=30.0,
            critical_threshold_seconds=60.0,
        )

        with patch("selfhealing.adapters.kafka.metrics.logger") as mock_logger:
            # 경고 임계값 초과
            tracker.record_message_processed(
                topic="topic1",
                partition=0,
                message_timestamp=time.time() - 45,
            )

            mock_logger.warning.assert_called()

    def test_error_log_on_critical_lag(self) -> None:
        """심각한 lag에서 에러 로그."""
        tracker = TimeLagTracker(
            consumer_group="test-group",
            alert_threshold_seconds=30.0,
            critical_threshold_seconds=60.0,
        )

        with patch("selfhealing.adapters.kafka.metrics.logger") as mock_logger:
            # 심각 임계값 초과
            tracker.record_message_processed(
                topic="topic1",
                partition=0,
                message_timestamp=time.time() - 120,
            )

            mock_logger.error.assert_called()
