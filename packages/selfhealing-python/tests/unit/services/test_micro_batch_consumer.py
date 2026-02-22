"""
Tests for MicroBatchConsumer — 마이크로배칭 이벤트 소비자.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: 상수 기본값, 큐 크기 제한
- Behavior: 배치 수집/플러시, BatchCapable 분기,
    큐 포화 시 Fail-Open, 라이프사이클

참조 소스:
- services/correlation_engine/micro_batch.py (MicroBatchConsumer)
- interfaces/ml_strategy.py (AnomalyDetectionStrategy, BatchCapable)
"""

from __future__ import annotations

import time
from typing import Any

from selfhealing.services.correlation_engine.micro_batch import (
    DEFAULT_FLUSH_INTERVAL_MS,
    DEFAULT_MAX_BATCH_SIZE,
    DEFAULT_QUEUE_MAX_SIZE,
    QUEUE_POLL_TIMEOUT,
    MicroBatchConsumer,
)

# =============================================================================
# Stub 전략 (테스트 전용)
# =============================================================================


class SpyAnomalyDetector:
    """detect() 호출을 기록하는 AnomalyDetectionStrategy 스파이."""

    def __init__(self) -> None:
        self.detect_calls: list[tuple[float, dict | None]] = []

    def detect(self, value: float, context: dict[str, Any] | None = None) -> tuple[bool, float]:
        self.detect_calls.append((value, context))
        return (False, 0.0)

    def update(self, value: float, context: dict[str, Any] | None = None) -> None:
        pass

    def reset(self) -> None:
        pass

    def get_feature_schema(self) -> dict[str, str] | None:
        return None


class SpyBatchDetector:
    """detect_batch() 호출을 기록하는 BatchCapable + AnomalyDetectionStrategy 스파이."""

    def __init__(self) -> None:
        self.batch_calls: list[tuple[list[float], list | None]] = []

    def detect(self, value: float, context: dict[str, Any] | None = None) -> tuple[bool, float]:
        return (False, 0.0)

    def update(self, value: float, context: dict[str, Any] | None = None) -> None:
        pass

    def reset(self) -> None:
        pass

    def get_feature_schema(self) -> dict[str, str] | None:
        return None

    def detect_batch(
        self,
        values: list[float],
        contexts: list[dict[str, Any]] | None = None,
    ) -> list[tuple[bool, float]]:
        self.batch_calls.append((list(values), contexts))
        return [(False, 0.0)] * len(values)

    def update_batch(self, values: list[float]) -> None:
        pass


# =============================================================================
# Contract Tests — 상수 기본값
# =============================================================================


class TestMicroBatchConstantsContract:
    """MicroBatchConsumer 상수 계약값 검증."""

    def test_default_flush_interval_ms(self):
        """기본 플러시 간격: 50ms."""
        assert DEFAULT_FLUSH_INTERVAL_MS == 50.0

    def test_default_max_batch_size(self):
        """기본 최대 배치 크기: 128."""
        assert DEFAULT_MAX_BATCH_SIZE == 128

    def test_default_queue_max_size(self):
        """기본 큐 최대 크기: 10000."""
        assert DEFAULT_QUEUE_MAX_SIZE == 10000

    def test_queue_poll_timeout(self):
        """큐 폴링 타임아웃: 10ms (0.01초)."""
        assert QUEUE_POLL_TIMEOUT == 0.01


# =============================================================================
# Behavior Tests — 라이프사이클
# =============================================================================


class TestMicroBatchLifecycleBehavior:
    """MicroBatchConsumer 라이프사이클 동작 검증."""

    def test_start_creates_daemon_thread(self):
        """start() 호출 시 daemon 스레드 생성."""
        detector = SpyAnomalyDetector()
        consumer = MicroBatchConsumer(detector, flush_interval_ms=10.0)

        consumer.start()
        try:
            assert consumer.is_running
        finally:
            consumer.stop()

    def test_stop_terminates_thread(self):
        """stop() 호출 시 스레드 종료."""
        detector = SpyAnomalyDetector()
        consumer = MicroBatchConsumer(detector, flush_interval_ms=10.0)

        consumer.start()
        consumer.stop(timeout=2.0)

        assert not consumer.is_running

    def test_double_start_is_idempotent(self):
        """이미 실행 중이면 start()는 무시."""
        detector = SpyAnomalyDetector()
        consumer = MicroBatchConsumer(detector, flush_interval_ms=10.0)

        consumer.start()
        consumer.start()  # 두 번째 호출
        try:
            assert consumer.is_running
        finally:
            consumer.stop()


# =============================================================================
# Behavior Tests — 배치 수집 및 플러시
# =============================================================================


class TestMicroBatchFlushBehavior:
    """MicroBatchConsumer 배치 플러시 동작 검증."""

    def test_single_detector_receives_individual_calls(self):
        """비배치 전략은 단건 detect()가 값마다 호출된다."""
        detector = SpyAnomalyDetector()
        consumer = MicroBatchConsumer(
            detector,
            flush_interval_ms=20.0,
            max_batch_size=10,
        )

        consumer.start()
        for v in [1.0, 2.0, 3.0]:
            consumer.submit(v)

        # 플러시 대기
        time.sleep(0.2)
        consumer.stop()

        assert len(detector.detect_calls) == 3
        submitted_values = [call[0] for call in detector.detect_calls]
        assert 1.0 in submitted_values
        assert 2.0 in submitted_values
        assert 3.0 in submitted_values

    def test_batch_detector_receives_batch_call(self):
        """BatchCapable 전략은 detect_batch()가 호출된다."""
        detector = SpyBatchDetector()
        consumer = MicroBatchConsumer(
            detector,
            flush_interval_ms=20.0,
            max_batch_size=10,
        )

        consumer.start()
        for v in [10.0, 20.0, 30.0]:
            consumer.submit(v)

        time.sleep(0.2)
        consumer.stop()

        # detect_batch가 최소 1회 호출됨
        assert len(detector.batch_calls) >= 1
        all_values = []
        for values, _ in detector.batch_calls:
            all_values.extend(values)
        assert 10.0 in all_values
        assert 20.0 in all_values
        assert 30.0 in all_values

    def test_max_batch_size_triggers_flush(self):
        """max_batch_size 도달 시 즉시 플러시."""
        detector = SpyBatchDetector()
        consumer = MicroBatchConsumer(
            detector,
            flush_interval_ms=5000.0,  # 긴 간격 (크기로 트리거)
            max_batch_size=3,
        )

        consumer.start()
        for v in [1.0, 2.0, 3.0]:
            consumer.submit(v)

        time.sleep(0.2)
        consumer.stop()

        # 3개가 모이면 즉시 플러시
        assert len(detector.batch_calls) >= 1
        first_batch_values = detector.batch_calls[0][0]
        assert len(first_batch_values) == 3


# =============================================================================
# Behavior Tests — 큐 포화 시 Fail-Open
# =============================================================================


class TestMicroBatchQueueOverflowBehavior:
    """큐 포화 시 Fail-Open 동작 검증."""

    def test_submit_drops_on_full_queue(self):
        """큐 포화 시 submit()은 예외 없이 드랍."""
        detector = SpyAnomalyDetector()
        consumer = MicroBatchConsumer(
            detector,
            flush_interval_ms=10.0,
            queue_max_size=2,
        )
        # start()하지 않아 큐가 소비되지 않음

        consumer.submit(1.0)
        consumer.submit(2.0)
        consumer.submit(3.0)  # 큐 포화 — 드랍

        assert consumer.dropped_count >= 1
        assert consumer.queue_size <= 2


# =============================================================================
# Behavior Tests — 메트릭
# =============================================================================


class TestMicroBatchMetricsBehavior:
    """MicroBatchConsumer 메트릭 동작 검증."""

    def test_processed_count_increments(self):
        detector = SpyAnomalyDetector()
        consumer = MicroBatchConsumer(detector, flush_interval_ms=10.0, max_batch_size=10)

        consumer.start()
        for v in [1.0, 2.0, 3.0]:
            consumer.submit(v)

        time.sleep(0.2)
        consumer.stop()

        assert consumer.processed_count == 3
        assert consumer.flush_count >= 1
