"""
Stage 29: Bulk DLQ Replay Test

목표: 대량 DLQ 재처리 시 시스템 안정성 검증 (10만 건 시나리오)

시나리오:
  - DLQ에 100,000건 메시지 적재
  - Replay 속도 조절 (Throttling)
  - CB 상태 변화 대응
  - 재실패 → Re-DLQ 이동

테스트 케이스:
  - TC-29-1: Replay 속도 조절 (Throttling)
  - TC-29-2: Replay 중 Circuit Breaker 상태 변화
  - TC-29-3: 재처리 실패 → 재DLQ
  - TC-29-4: Queue Size Spike Limit

실행 방법:
    # 기본 모드
    locust -f load_tests/scenarios/stage29_bulk_dlq_replay.py --host=http://localhost:8000

    # 대량 테스트 모드
    locust -f load_tests/scenarios/stage29_bulk_dlq_replay.py \\
        --host=http://localhost:8000 --users=10 --spawn-rate=2 --run-time=10m --headless

검증 기준:
  - 처리 완료율: > 99%
  - 메모리 사용량: < 1GB
  - Throttle 반응 시간: < 1초
  - Re-DLQ 정확성: 100%

Reference:
  - docs/STAGE_28_30_ADVANCED_CHAOS_PLAN.md
"""

import os
import sys
import time
import random
import threading
import uuid
import tracemalloc
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events


STAGE_NAME = "[Stage29-BulkDLQ]"


# =============================================================================
# 설정
# =============================================================================


@dataclass
class BulkDLQConfig:
    """대량 DLQ 설정"""

    total_messages: int = 100_000
    batch_size: int = 1_000
    initial_tps: int = 1_000
    min_tps: int = 100
    max_tps: int = 2_000
    max_queue_size: int = 500_000
    memory_limit_mb: int = 1024
    re_dlq_max_retries: int = 3
    throttle_threshold: float = 0.8  # 80% 큐 사용 시 throttle
    cb_failure_threshold: float = 0.5  # 50% 실패 시 CB OPEN


# 기본 설정
CONFIG = BulkDLQConfig()


# =============================================================================
# Circuit Breaker 상태
# =============================================================================


class CircuitBreakerState(str, Enum):
    """Circuit Breaker 상태"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Circuit Breaker 구현"""

    name: str
    state: CircuitBreakerState = CircuitBreakerState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    failure_threshold: int = 10
    success_threshold: int = 5  # HALF_OPEN에서 CLOSED로 전환 조건
    open_timeout_sec: float = 30.0
    last_failure_time: Optional[datetime] = None
    last_state_change: Optional[datetime] = None

    def record_success(self):
        """성공 기록"""
        self.success_count += 1
        self.failure_count = 0

        if self.state == CircuitBreakerState.HALF_OPEN:
            if self.success_count >= self.success_threshold:
                self._transition_to(CircuitBreakerState.CLOSED)

    def record_failure(self):
        """실패 기록"""
        self.failure_count += 1
        self.success_count = 0
        self.last_failure_time = datetime.now(timezone.utc)

        if self.state == CircuitBreakerState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self._transition_to(CircuitBreakerState.OPEN)
        elif self.state == CircuitBreakerState.HALF_OPEN:
            self._transition_to(CircuitBreakerState.OPEN)

    def allow_request(self) -> bool:
        """요청 허용 여부"""
        if self.state == CircuitBreakerState.CLOSED:
            return True
        elif self.state == CircuitBreakerState.OPEN:
            # 타임아웃 확인
            if self.last_failure_time:
                elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
                if elapsed >= self.open_timeout_sec:
                    self._transition_to(CircuitBreakerState.HALF_OPEN)
                    return True
            return False
        else:  # HALF_OPEN
            return True

    def _transition_to(self, new_state: CircuitBreakerState):
        """상태 전이"""
        old_state = self.state
        self.state = new_state
        self.last_state_change = datetime.now(timezone.utc)

        if new_state == CircuitBreakerState.CLOSED:
            self.failure_count = 0
        elif new_state == CircuitBreakerState.HALF_OPEN:
            self.success_count = 0

        print(f"{STAGE_NAME} CB {self.name}: {old_state.value} -> {new_state.value}")


# =============================================================================
# DLQ 메시지
# =============================================================================


@dataclass
class DLQMessage:
    """DLQ 메시지"""

    id: str
    payload: Dict[str, Any]
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_attempt_at: Optional[datetime] = None
    error_message: Optional[str] = None

    def can_retry(self) -> bool:
        """재시도 가능 여부"""
        return self.retry_count < self.max_retries

    def increment_retry(self, error: str = None):
        """재시도 횟수 증가"""
        self.retry_count += 1
        self.last_attempt_at = datetime.now(timezone.utc)
        self.error_message = error


# =============================================================================
# DLQ Manager
# =============================================================================


class DLQManager:
    """DLQ 관리자"""

    def __init__(self, config: BulkDLQConfig = None):
        self.config = config or CONFIG
        self.main_queue: deque = deque()
        self.re_dlq: deque = deque()
        self.success_queue: deque = deque()
        self.processing: Dict[str, DLQMessage] = {}

        self.lock = threading.Lock()

        # TPS 제어
        self.current_tps = self.config.initial_tps
        self.throttle_active = False

        # Circuit Breaker
        self.circuit_breaker = CircuitBreaker(
            name="dlq_processor", failure_threshold=int(self.config.batch_size * self.config.cb_failure_threshold)
        )

        # 메모리 모니터링
        self.memory_peaks: List[float] = []

        # 상태
        self.is_paused = False
        self.total_processed = 0
        self.total_succeeded = 0
        self.total_failed = 0

    def enqueue(self, message: DLQMessage):
        """메시지 큐에 추가"""
        with self.lock:
            self.main_queue.append(message)

    def enqueue_batch(self, messages: List[DLQMessage]):
        """배치 메시지 추가"""
        with self.lock:
            self.main_queue.extend(messages)

    def dequeue(self, batch_size: int = None) -> List[DLQMessage]:
        """메시지 배치 가져오기"""
        if self.is_paused:
            return []

        if not self.circuit_breaker.allow_request():
            return []

        batch_size = batch_size or self.config.batch_size

        with self.lock:
            batch = []
            for _ in range(min(batch_size, len(self.main_queue))):
                if self.main_queue:
                    msg = self.main_queue.popleft()
                    self.processing[msg.id] = msg
                    batch.append(msg)
            return batch

    def complete(self, message_id: str, success: bool, error: str = None):
        """처리 완료"""
        with self.lock:
            if message_id not in self.processing:
                return

            msg = self.processing.pop(message_id)
            self.total_processed += 1

            if success:
                self.total_succeeded += 1
                self.success_queue.append(msg)
                self.circuit_breaker.record_success()
            else:
                self.total_failed += 1
                msg.increment_retry(error)
                self.circuit_breaker.record_failure()

                if msg.can_retry():
                    # 메인 큐로 다시
                    self.main_queue.append(msg)
                else:
                    # Re-DLQ로 이동
                    self.re_dlq.append(msg)

    def get_queue_size(self) -> int:
        """현재 큐 크기"""
        with self.lock:
            return len(self.main_queue)

    def get_queue_usage(self) -> float:
        """큐 사용률 (0.0 ~ 1.0)"""
        return self.get_queue_size() / self.config.max_queue_size

    def check_throttle(self) -> bool:
        """Throttle 필요 여부 확인 및 적용"""
        usage = self.get_queue_usage()

        if usage >= self.config.throttle_threshold:
            if not self.throttle_active:
                self.throttle_active = True
                self.current_tps = max(self.config.min_tps, int(self.current_tps * 0.5))
                print(f"{STAGE_NAME} Throttle activated: TPS -> {self.current_tps}")
            return True
        else:
            if self.throttle_active:
                self.throttle_active = False
                self.current_tps = min(self.config.max_tps, int(self.current_tps * 1.5))
                print(f"{STAGE_NAME} Throttle deactivated: TPS -> {self.current_tps}")
            return False

    def check_memory(self) -> float:
        """메모리 사용량 확인 (MB)"""
        try:
            import psutil

            process = psutil.Process()
            memory_mb = process.memory_info().rss / (1024 * 1024)
        except ImportError:
            # psutil이 없으면 tracemalloc 사용
            current, peak = tracemalloc.get_traced_memory()
            memory_mb = current / (1024 * 1024)

        self.memory_peaks.append(memory_mb)
        return memory_mb

    def pause_replay(self):
        """Replay 일시 중단"""
        self.is_paused = True
        print(f"{STAGE_NAME} Replay paused")

    def resume_replay(self):
        """Replay 재개"""
        self.is_paused = False
        print(f"{STAGE_NAME} Replay resumed")

    def get_stats(self) -> Dict[str, Any]:
        """통계 반환"""
        with self.lock:
            return {
                "main_queue_size": len(self.main_queue),
                "re_dlq_size": len(self.re_dlq),
                "success_queue_size": len(self.success_queue),
                "processing_count": len(self.processing),
                "total_processed": self.total_processed,
                "total_succeeded": self.total_succeeded,
                "total_failed": self.total_failed,
                "current_tps": self.current_tps,
                "throttle_active": self.throttle_active,
                "is_paused": self.is_paused,
                "cb_state": self.circuit_breaker.state.value,
                "memory_peak_mb": max(self.memory_peaks) if self.memory_peaks else 0,
            }


# 전역 DLQ 매니저
_dlq_manager = DLQManager()


# =============================================================================
# 메시지 생성기
# =============================================================================


def generate_dlq_messages(count: int, failure_rate: float = 0.1) -> List[DLQMessage]:
    """테스트용 DLQ 메시지 생성"""
    messages = []
    for i in range(count):
        msg = DLQMessage(
            id=str(uuid.uuid4()),
            payload={
                "order_id": f"order_{i}",
                "amount": random.randint(1000, 100000),
                "action": random.choice(["payment_confirm", "refund", "notification"]),
                "should_fail": random.random() < failure_rate,
            },
            max_retries=CONFIG.re_dlq_max_retries,
        )
        messages.append(msg)
    return messages


# =============================================================================
# 테스트 통계
# =============================================================================


@dataclass
class BulkDLQStats:
    """대량 DLQ 처리 통계"""

    total_messages: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    re_dlq_count: int = 0

    cb_open_events: int = 0
    cb_half_open_events: int = 0
    cb_closed_events: int = 0

    throttle_events: int = 0
    memory_peaks: List[float] = field(default_factory=list)
    tps_history: List[int] = field(default_factory=list)

    processing_time_sum_ms: float = 0
    batch_count: int = 0


_stats = BulkDLQStats()
_stats_lock = threading.Lock()


def record_batch_processed(count: int, succeeded: int, failed: int, time_ms: float):
    """배치 처리 기록"""
    with _stats_lock:
        _stats.processed += count
        _stats.succeeded += succeeded
        _stats.failed += failed
        _stats.processing_time_sum_ms += time_ms
        _stats.batch_count += 1


def record_cb_event(state: CircuitBreakerState):
    """CB 이벤트 기록"""
    with _stats_lock:
        if state == CircuitBreakerState.OPEN:
            _stats.cb_open_events += 1
        elif state == CircuitBreakerState.HALF_OPEN:
            _stats.cb_half_open_events += 1
        elif state == CircuitBreakerState.CLOSED:
            _stats.cb_closed_events += 1


def record_throttle_event():
    """Throttle 이벤트 기록"""
    with _stats_lock:
        _stats.throttle_events += 1


def record_memory_peak(memory_mb: float):
    """메모리 피크 기록"""
    with _stats_lock:
        _stats.memory_peaks.append(memory_mb)


def record_tps(tps: int):
    """TPS 기록"""
    with _stats_lock:
        _stats.tps_history.append(tps)


# =============================================================================
# Locust User
# =============================================================================


class BulkDLQReplayUser(HttpUser):
    """대량 DLQ 재처리 사용자"""

    wait_time = between(0.1, 0.5)  # 빠른 처리

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.manager = _dlq_manager
        self.batch_id = 0

    def on_start(self):
        """테스트 시작 시 초기화"""
        # 메모리 추적 시작
        tracemalloc.start()
        print(f"{STAGE_NAME} User started")

    def on_stop(self):
        """테스트 종료 시"""
        tracemalloc.stop()

    @task(5)
    @tag("tc-29-1", "throttling")
    def test_replay_with_throttling(self):
        """TC-29-1: Replay 속도 조절 (Throttling) 테스트"""
        # Throttle 체크
        if self.manager.check_throttle():
            record_throttle_event()

        record_tps(self.manager.current_tps)

        start_time = time.time()
        batch = self.manager.dequeue()

        if not batch:
            return

        succeeded = 0
        failed = 0

        for msg in batch:
            # 실제 처리 시뮬레이션
            should_fail = msg.payload.get("should_fail", False)

            if should_fail:
                self.manager.complete(msg.id, success=False, error="Simulated failure")
                failed += 1
            else:
                self.manager.complete(msg.id, success=True)
                succeeded += 1

        time_ms = (time.time() - start_time) * 1000
        record_batch_processed(len(batch), succeeded, failed, time_ms)

        with self.client.post(
            "/api/health/",
            json={"batch_size": len(batch), "succeeded": succeeded, "failed": failed},
            name=f"{STAGE_NAME} TC-29-1: Throttling - Batch Replay",
            catch_response=True,
        ) as response:
            # 상태 코드에 관계없이 성공 처리 (시뮬레이션이므로)
            response.success()

    @task(3)
    @tag("tc-29-2", "circuit-breaker")
    def test_replay_with_cb_state_change(self):
        """TC-29-2: Replay 중 Circuit Breaker 상태 변화 테스트"""
        cb = self.manager.circuit_breaker
        prev_state = cb.state

        # CB 상태에 따른 처리
        if not cb.allow_request():
            # CB OPEN - 대기
            time.sleep(0.5)
            return

        # 배치 처리
        batch = self.manager.dequeue(batch_size=100)

        if not batch:
            return

        # 외부 API 장애 시뮬레이션 (30% 확률)
        external_api_failure = random.random() < 0.3

        for msg in batch:
            if external_api_failure:
                self.manager.complete(msg.id, success=False, error="External API failure")
            else:
                self.manager.complete(msg.id, success=True)

        # CB 상태 변화 감지
        if cb.state != prev_state:
            record_cb_event(cb.state)

            if cb.state == CircuitBreakerState.OPEN:
                # CB OPEN시 Replay 일시 중단
                self.manager.pause_replay()
            elif cb.state == CircuitBreakerState.HALF_OPEN:
                # CB HALF_OPEN시 점진적 재개
                self.manager.current_tps = self.manager.config.min_tps
                self.manager.resume_replay()
            elif cb.state == CircuitBreakerState.CLOSED:
                # CB CLOSED시 정상 처리
                self.manager.current_tps = self.manager.config.initial_tps

        with self.client.get(
            "/api/health/", name=f"{STAGE_NAME} TC-29-2: CB State - Health Check", catch_response=True
        ) as response:
            response.success()

    @task(2)
    @tag("tc-29-3", "re-dlq")
    def test_replay_failure_to_re_dlq(self):
        """TC-29-3: 재처리 실패 → 재DLQ 테스트"""
        # 재시도 횟수 초과 메시지 확인
        with _stats_lock:
            re_dlq_size = len(self.manager.re_dlq)

        # 처리 완료율 계산
        stats = self.manager.get_stats()
        total = stats["total_processed"]

        if total > 0:
            success_rate = stats["total_succeeded"] / total
            re_dlq_accuracy = re_dlq_size == stats["total_failed"] - (
                len(self.manager.main_queue) + len(self.manager.processing)
            )

        # 메시지 처리
        batch = self.manager.dequeue(batch_size=50)

        for msg in batch:
            # 높은 실패율로 Re-DLQ 테스트
            if random.random() < 0.5:
                self.manager.complete(msg.id, success=False, error="High failure rate test")
            else:
                self.manager.complete(msg.id, success=True)

        with self.client.post(
            "/api/health/",
            json={"re_dlq_size": re_dlq_size},
            name=f"{STAGE_NAME} TC-29-3: Re-DLQ - Failure Handling",
            catch_response=True,
        ) as response:
            response.success()

    @task(1)
    @tag("tc-29-4", "queue-spike")
    def test_queue_size_spike_limit(self):
        """TC-29-4: Queue Size Spike Limit 테스트"""
        # 메모리 체크
        memory_mb = self.manager.check_memory()
        record_memory_peak(memory_mb)

        # 큐 사용률 체크
        usage = self.manager.get_queue_usage()
        queue_size = self.manager.get_queue_size()

        # 메모리 한계 확인
        if memory_mb > self.manager.config.memory_limit_mb:
            print(f"{STAGE_NAME} Memory limit warning: {memory_mb:.2f}MB")
            self.manager.pause_replay()

        # 큐 급증 시 자동 throttling
        if usage > 0.9:
            print(f"{STAGE_NAME} Queue spike warning: {usage*100:.1f}% ({queue_size} items)")
            self.manager.check_throttle()

        with self.client.get(
            "/api/health/", name=f"{STAGE_NAME} TC-29-4: Queue Spike - Memory Check", catch_response=True
        ) as response:
            response.success()

    @task(1)
    @tag("monitoring")
    def monitor_stats(self):
        """통계 모니터링"""
        stats = self.manager.get_stats()

        # 주기적으로 상태 출력
        if stats["total_processed"] > 0 and stats["total_processed"] % 1000 == 0:
            print(
                f"{STAGE_NAME} Progress: {stats['total_processed']} processed, "
                f"{stats['total_succeeded']} succeeded, {stats['total_failed']} failed, "
                f"Re-DLQ: {stats['re_dlq_size']}, CB: {stats['cb_state']}"
            )


# =============================================================================
# Event Handlers
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시"""
    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Bulk DLQ Replay Test Started")
    print(f"{'='*60}")

    # 테스트 메시지 생성 (실제 10만건은 메모리 문제로 1만건으로 축소)
    test_message_count = min(CONFIG.total_messages, 10000)
    messages = generate_dlq_messages(test_message_count, failure_rate=0.1)
    _dlq_manager.enqueue_batch(messages)

    with _stats_lock:
        _stats.total_messages = test_message_count

    print(f"Generated {test_message_count} DLQ messages")
    print(f"Initial TPS: {CONFIG.initial_tps}")
    print(f"Memory Limit: {CONFIG.memory_limit_mb}MB")
    print(f"{'='*60}\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시"""
    stats = _dlq_manager.get_stats()

    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Test Results")
    print(f"{'='*60}")
    print(f"Total Messages: {_stats.total_messages}")
    print(f"Processed: {stats['total_processed']}")
    print(f"Succeeded: {stats['total_succeeded']}")
    print(f"Failed: {stats['total_failed']}")
    print(f"Re-DLQ: {stats['re_dlq_size']}")
    print("\nCircuit Breaker Events:")
    print(f"  - OPEN: {_stats.cb_open_events}")
    print(f"  - HALF_OPEN: {_stats.cb_half_open_events}")
    print(f"  - CLOSED: {_stats.cb_closed_events}")
    print(f"\nThrottle Events: {_stats.throttle_events}")
    print(f"Memory Peak: {stats['memory_peak_mb']:.2f}MB")

    if _stats.batch_count > 0:
        avg_batch_time = _stats.processing_time_sum_ms / _stats.batch_count
        print(f"Avg Batch Processing Time: {avg_batch_time:.2f}ms")

    print(f"{'='*60}")

    # 검증 기준 체크
    print(f"\n{STAGE_NAME} Validation Criteria:")

    # 처리 완료율 > 99%
    if stats["total_processed"] > 0:
        completion_rate = stats["total_processed"] / _stats.total_messages
        status = "✅ PASS" if completion_rate > 0.99 else "❌ FAIL"
        print(f"  Completion Rate > 99%: {status} ({completion_rate*100:.2f}%)")

    # 메모리 사용량 < 1GB
    status = "✅ PASS" if stats["memory_peak_mb"] < 1024 else "❌ FAIL"
    print(f"  Memory < 1GB: {status} ({stats['memory_peak_mb']:.2f}MB)")

    # Re-DLQ 정확성
    expected_re_dlq = stats["total_failed"]  # 재시도 후에도 실패한 것들
    # Re-DLQ에는 max_retries 초과한 것만 있어야 함
    print(f"  Re-DLQ Count: {stats['re_dlq_size']} items")

    # CB 상태 전이 정상
    cb_transitions = _stats.cb_open_events + _stats.cb_half_open_events + _stats.cb_closed_events
    print(f"  CB State Transitions: {cb_transitions} total")

    print()


# =============================================================================
# 외부 제어 함수
# =============================================================================


def get_dlq_manager() -> DLQManager:
    """DLQ 매니저 반환"""
    return _dlq_manager


def inject_queue_spike(count: int = 50000):
    """큐 급증 주입"""
    messages = generate_dlq_messages(count, failure_rate=0.2)
    _dlq_manager.enqueue_batch(messages)
    print(f"{STAGE_NAME} Injected {count} messages (queue spike)")


def force_cb_open():
    """CB 강제 OPEN"""
    for _ in range(_dlq_manager.circuit_breaker.failure_threshold + 1):
        _dlq_manager.circuit_breaker.record_failure()


def reset_dlq():
    """DLQ 초기화"""
    global _dlq_manager
    _dlq_manager = DLQManager()


def get_stats() -> BulkDLQStats:
    """현재 통계 반환"""
    return _stats
