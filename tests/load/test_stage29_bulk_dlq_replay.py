"""
Stage 29: Bulk DLQ Replay Test - Unit Tests

이 파일은 Stage 29 시나리오의 핵심 로직을 검증합니다.
"""

import pytest
import time
import threading
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import uuid

import sys
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from load_tests.scenarios.stage29_bulk_dlq_replay import (
    BulkDLQConfig,
    CircuitBreaker,
    CircuitBreakerState,
    DLQMessage,
    DLQManager,
    BulkDLQStats,
    generate_dlq_messages,
    inject_queue_spike,
    force_cb_open,
    reset_dlq,
)


class TestBulkDLQConfig:
    """BulkDLQConfig 테스트"""

    def test_default_config(self):
        """기본 설정 테스트"""
        config = BulkDLQConfig()

        assert config.total_messages == 100_000
        assert config.batch_size == 1_000
        assert config.initial_tps == 1_000
        assert config.min_tps == 100
        assert config.max_tps == 2_000
        assert config.max_queue_size == 500_000
        assert config.memory_limit_mb == 1024
        assert config.re_dlq_max_retries == 3
        assert config.throttle_threshold == 0.8

    def test_custom_config(self):
        """커스텀 설정 테스트"""
        config = BulkDLQConfig(
            total_messages=50_000,
            batch_size=500,
            initial_tps=500,
        )

        assert config.total_messages == 50_000
        assert config.batch_size == 500
        assert config.initial_tps == 500


class TestCircuitBreaker:
    """CircuitBreaker 테스트"""

    @pytest.fixture
    def cb(self):
        """Circuit Breaker 인스턴스"""
        return CircuitBreaker(
            name="test_cb",
            failure_threshold=5,
            success_threshold=3,
            open_timeout_sec=1.0,
        )

    def test_initial_state(self, cb):
        """초기 상태 테스트"""
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0
        assert cb.allow_request() is True

    def test_record_success(self, cb):
        """성공 기록 테스트"""
        cb.record_success()

        assert cb.success_count == 1
        assert cb.failure_count == 0
        assert cb.state == CircuitBreakerState.CLOSED

    def test_record_failure(self, cb):
        """실패 기록 테스트"""
        cb.record_failure()

        assert cb.failure_count == 1
        assert cb.success_count == 0
        assert cb.last_failure_time is not None

    def test_transition_to_open(self, cb):
        """CLOSED -> OPEN 전이 테스트"""
        # failure_threshold (5) 만큼 실패
        for _ in range(5):
            cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN
        assert cb.allow_request() is False

    def test_transition_to_half_open(self, cb):
        """OPEN -> HALF_OPEN 전이 테스트"""
        # OPEN 상태로 만들기
        for _ in range(5):
            cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN

        # 타임아웃 대기
        time.sleep(1.1)

        # 요청 허용 확인 (HALF_OPEN으로 전이)
        assert cb.allow_request() is True
        assert cb.state == CircuitBreakerState.HALF_OPEN

    def test_transition_to_closed_from_half_open(self, cb):
        """HALF_OPEN -> CLOSED 전이 테스트"""
        # HALF_OPEN 상태로 만들기
        for _ in range(5):
            cb.record_failure()
        time.sleep(1.1)
        cb.allow_request()

        assert cb.state == CircuitBreakerState.HALF_OPEN

        # success_threshold (3) 만큼 성공
        for _ in range(3):
            cb.record_success()

        assert cb.state == CircuitBreakerState.CLOSED

    def test_half_open_failure_returns_to_open(self, cb):
        """HALF_OPEN에서 실패 시 OPEN으로 복귀"""
        # HALF_OPEN 상태로 만들기
        for _ in range(5):
            cb.record_failure()
        time.sleep(1.1)
        cb.allow_request()

        assert cb.state == CircuitBreakerState.HALF_OPEN

        # 실패
        cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN


class TestDLQMessage:
    """DLQMessage 테스트"""

    def test_create_message(self):
        """메시지 생성 테스트"""
        msg = DLQMessage(
            id="test-123",
            payload={"order_id": "order_1", "amount": 10000},
        )

        assert msg.id == "test-123"
        assert msg.payload["order_id"] == "order_1"
        assert msg.retry_count == 0
        assert msg.max_retries == 3
        assert msg.can_retry() is True

    def test_can_retry(self):
        """재시도 가능 여부 테스트"""
        msg = DLQMessage(id="test", payload={}, max_retries=2)

        assert msg.can_retry() is True

        msg.increment_retry()
        assert msg.can_retry() is True

        msg.increment_retry()
        assert msg.can_retry() is False

    def test_increment_retry(self):
        """재시도 횟수 증가 테스트"""
        msg = DLQMessage(id="test", payload={})

        msg.increment_retry("Error 1")

        assert msg.retry_count == 1
        assert msg.error_message == "Error 1"
        assert msg.last_attempt_at is not None


class TestDLQManager:
    """DLQManager 테스트"""

    @pytest.fixture
    def manager(self):
        """새로운 DLQ 매니저 인스턴스"""
        config = BulkDLQConfig(
            batch_size=10,
            initial_tps=100,
            min_tps=10,
            max_tps=200,
            max_queue_size=1000,
            re_dlq_max_retries=2,
        )
        return DLQManager(config)

    def test_initial_state(self, manager):
        """초기 상태 테스트"""
        assert manager.get_queue_size() == 0
        assert manager.total_processed == 0
        assert manager.throttle_active is False
        assert manager.is_paused is False

    def test_enqueue_single(self, manager):
        """단일 메시지 추가 테스트"""
        msg = DLQMessage(id="1", payload={"test": True})
        manager.enqueue(msg)

        assert manager.get_queue_size() == 1

    def test_enqueue_batch(self, manager):
        """배치 메시지 추가 테스트"""
        messages = [DLQMessage(id=str(i), payload={}) for i in range(100)]
        manager.enqueue_batch(messages)

        assert manager.get_queue_size() == 100

    def test_dequeue(self, manager):
        """메시지 배치 가져오기 테스트"""
        messages = [DLQMessage(id=str(i), payload={}) for i in range(50)]
        manager.enqueue_batch(messages)

        batch = manager.dequeue(batch_size=10)

        assert len(batch) == 10
        assert manager.get_queue_size() == 40
        assert len(manager.processing) == 10

    def test_dequeue_when_paused(self, manager):
        """일시 중단 시 dequeue 테스트"""
        messages = [DLQMessage(id=str(i), payload={}) for i in range(50)]
        manager.enqueue_batch(messages)

        manager.pause_replay()
        batch = manager.dequeue()

        assert len(batch) == 0

    def test_dequeue_when_cb_open(self, manager):
        """CB OPEN 시 dequeue 테스트"""
        messages = [DLQMessage(id=str(i), payload={}) for i in range(50)]
        manager.enqueue_batch(messages)

        # CB를 OPEN 상태로 만들기
        for _ in range(manager.circuit_breaker.failure_threshold + 1):
            manager.circuit_breaker.record_failure()

        batch = manager.dequeue()

        assert len(batch) == 0

    def test_complete_success(self, manager):
        """성공 완료 테스트"""
        msg = DLQMessage(id="1", payload={})
        manager.enqueue(msg)
        manager.dequeue()

        manager.complete("1", success=True)

        assert manager.total_processed == 1
        assert manager.total_succeeded == 1
        assert manager.total_failed == 0
        assert len(manager.success_queue) == 1

    def test_complete_failure_with_retry(self, manager):
        """실패 + 재시도 테스트"""
        msg = DLQMessage(id="1", payload={}, max_retries=2)
        manager.enqueue(msg)
        manager.dequeue()

        manager.complete("1", success=False, error="Test error")

        assert manager.total_processed == 1
        assert manager.total_failed == 1
        # 재시도 가능하므로 메인 큐로 돌아감
        assert manager.get_queue_size() == 1
        assert len(manager.re_dlq) == 0

    def test_complete_failure_to_re_dlq(self, manager):
        """실패 → Re-DLQ 테스트"""
        msg = DLQMessage(id="1", payload={}, max_retries=1)
        msg.retry_count = 1  # 이미 1회 재시도
        manager.enqueue(msg)
        manager.dequeue()

        manager.complete("1", success=False, error="Final failure")

        # 재시도 불가능하므로 Re-DLQ로 이동
        assert len(manager.re_dlq) == 1

    def test_throttle_activation(self, manager):
        """Throttle 활성화 테스트"""
        # 큐를 80% 이상으로 채우기
        messages = [DLQMessage(id=str(i), payload={}) for i in range(850)]
        manager.enqueue_batch(messages)

        # Throttle 체크
        manager.check_throttle()

        assert manager.throttle_active is True
        assert manager.current_tps < manager.config.initial_tps

    def test_throttle_deactivation(self, manager):
        """Throttle 비활성화 테스트"""
        # 먼저 Throttle 활성화
        messages = [DLQMessage(id=str(i), payload={}) for i in range(850)]
        manager.enqueue_batch(messages)
        manager.check_throttle()

        assert manager.throttle_active is True

        # 큐 비우기
        while manager.get_queue_size() > 100:
            batch = manager.dequeue(batch_size=100)
            for msg in batch:
                manager.complete(msg.id, success=True)

        manager.check_throttle()

        assert manager.throttle_active is False

    def test_get_queue_usage(self, manager):
        """큐 사용률 테스트"""
        assert manager.get_queue_usage() == 0.0

        messages = [DLQMessage(id=str(i), payload={}) for i in range(500)]
        manager.enqueue_batch(messages)

        assert manager.get_queue_usage() == 0.5

    def test_pause_and_resume(self, manager):
        """일시 중단 및 재개 테스트"""
        manager.pause_replay()
        assert manager.is_paused is True

        manager.resume_replay()
        assert manager.is_paused is False

    def test_get_stats(self, manager):
        """통계 반환 테스트"""
        messages = [DLQMessage(id=str(i), payload={}) for i in range(100)]
        manager.enqueue_batch(messages)

        batch = manager.dequeue(batch_size=10)
        for msg in batch[:5]:
            manager.complete(msg.id, success=True)
        for msg in batch[5:]:
            manager.complete(msg.id, success=False, error="Test")

        stats = manager.get_stats()

        assert stats["main_queue_size"] == 95  # 100 - 10 + 5 (재시도)
        assert stats["total_processed"] == 10
        assert stats["total_succeeded"] == 5
        assert stats["total_failed"] == 5


class TestDLQMessageGeneration:
    """메시지 생성 테스트"""

    def test_generate_messages(self):
        """메시지 생성 테스트"""
        messages = generate_dlq_messages(100, failure_rate=0.2)

        assert len(messages) == 100
        assert all(isinstance(m, DLQMessage) for m in messages)
        assert all(m.id is not None for m in messages)

    def test_failure_rate(self):
        """실패율 테스트"""
        messages = generate_dlq_messages(1000, failure_rate=0.3)

        failures = sum(1 for m in messages if m.payload.get("should_fail"))
        failure_rate = failures / len(messages)

        # 30% 실패율 (0.25 ~ 0.35 범위)
        assert 0.25 <= failure_rate <= 0.35


class TestDLQConcurrency:
    """동시성 테스트"""

    @pytest.fixture
    def manager(self):
        config = BulkDLQConfig(batch_size=10, max_queue_size=10000)
        return DLQManager(config)

    def test_concurrent_enqueue(self, manager):
        """동시 enqueue 테스트"""
        threads = []

        def enqueue_batch():
            messages = [DLQMessage(id=str(uuid.uuid4()), payload={}) for _ in range(100)]
            manager.enqueue_batch(messages)

        for _ in range(10):
            t = threading.Thread(target=enqueue_batch)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert manager.get_queue_size() == 1000

    def test_concurrent_dequeue_and_complete(self, manager):
        """동시 dequeue + complete 테스트"""
        messages = [DLQMessage(id=str(i), payload={}) for i in range(1000)]
        manager.enqueue_batch(messages)

        threads = []

        def process_batch():
            batch = manager.dequeue(batch_size=50)
            for msg in batch:
                manager.complete(msg.id, success=True)

        for _ in range(20):
            t = threading.Thread(target=process_batch)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # 모든 메시지가 처리되거나 큐에 남아있어야 함
        total = manager.total_processed + manager.get_queue_size() + len(manager.processing)
        assert total <= 1000


class TestBulkDLQStats:
    """통계 테스트"""

    def test_stats_initialization(self):
        """통계 초기화 테스트"""
        stats = BulkDLQStats()

        assert stats.total_messages == 0
        assert stats.processed == 0
        assert stats.succeeded == 0
        assert stats.failed == 0
        assert stats.re_dlq_count == 0
        assert stats.cb_open_events == 0
        assert stats.throttle_events == 0


class TestValidationCriteria:
    """검증 기준 테스트"""

    @pytest.fixture
    def manager(self):
        config = BulkDLQConfig(
            batch_size=100,
            max_queue_size=10000,
            memory_limit_mb=1024,
        )
        return DLQManager(config)

    def test_completion_rate_above_99_percent(self, manager):
        """처리 완료율 > 99% 테스트"""
        # 실패율 0으로 설정하여 순수 완료율 테스트
        messages = generate_dlq_messages(1000, failure_rate=0.0)
        manager.enqueue_batch(messages)

        while manager.get_queue_size() > 0:
            batch = manager.dequeue(batch_size=100)
            for msg in batch:
                should_fail = msg.payload.get("should_fail", False)
                manager.complete(msg.id, success=not should_fail, error="fail" if should_fail else None)

        stats = manager.get_stats()
        # 실패율 0이므로 모든 메시지가 성공해야 함
        completion_rate = stats["total_succeeded"] / stats["total_processed"]

        assert completion_rate >= 0.99

    def test_re_dlq_accuracy(self, manager):
        """Re-DLQ 정확성 테스트"""
        # max_retries=2인 메시지 생성
        messages = []
        for i in range(100):
            msg = DLQMessage(
                id=str(i),
                payload={"should_fail": i < 10},  # 처음 10개만 실패
                max_retries=2,
            )
            messages.append(msg)

        manager.enqueue_batch(messages)

        # 3번 처리 (재시도 2회 + 최초 1회)
        for _ in range(3):
            while manager.get_queue_size() > 0 or len(manager.processing) > 0:
                batch = manager.dequeue(batch_size=100)
                if not batch:
                    break
                for msg in batch:
                    should_fail = msg.payload.get("should_fail", False)
                    manager.complete(msg.id, success=not should_fail, error="fail" if should_fail else None)

        # 처음 10개 실패 메시지가 Re-DLQ에 있어야 함
        assert len(manager.re_dlq) == 10

    def test_throttle_response_time(self, manager):
        """Throttle 반응 시간 < 1초 테스트"""
        # 큐를 급증시키기
        messages = [DLQMessage(id=str(i), payload={}) for i in range(9000)]
        manager.enqueue_batch(messages)

        start_time = time.time()
        manager.check_throttle()
        throttle_time = time.time() - start_time

        assert throttle_time < 1.0
        assert manager.throttle_active is True


class TestExternalControls:
    """외부 제어 함수 테스트"""

    def test_inject_queue_spike(self):
        """큐 급증 주입 테스트"""
        reset_dlq()
        from load_tests.scenarios.stage29_bulk_dlq_replay import _dlq_manager

        initial_size = _dlq_manager.get_queue_size()
        inject_queue_spike(1000)

        assert _dlq_manager.get_queue_size() == initial_size + 1000

    def test_force_cb_open(self):
        """CB 강제 OPEN 테스트"""
        reset_dlq()
        from load_tests.scenarios.stage29_bulk_dlq_replay import _dlq_manager

        force_cb_open()

        assert _dlq_manager.circuit_breaker.state == CircuitBreakerState.OPEN

    def test_reset_dlq(self):
        """DLQ 초기화 테스트"""
        from load_tests.scenarios.stage29_bulk_dlq_replay import _dlq_manager

        messages = [DLQMessage(id=str(i), payload={}) for i in range(100)]
        _dlq_manager.enqueue_batch(messages)

        reset_dlq()

        from load_tests.scenarios.stage29_bulk_dlq_replay import _dlq_manager as new_manager

        assert new_manager.get_queue_size() == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
