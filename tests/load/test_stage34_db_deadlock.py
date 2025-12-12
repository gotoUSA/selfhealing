"""
Stage 34: DB Deadlock Locust Extended Test - Unit Tests

이 파일은 Stage 34 시나리오의 핵심 로직을 검증합니다.
- Concurrent Order Deadlock
- Payment + Point Deadlock
- Pool Exhaustion + Deadlock Compound
"""

import pytest
import time
import threading
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass

import sys
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestConnectionPool:
    """Connection Pool 관련 테스트"""

    def test_acquire_connection_success(self):
        """커넥션 획득 성공"""
        from load_tests.scenarios.stage34_db_deadlock import (
            acquire_db_connection,
            _db_state,
            reset_connection_pool,
        )

        reset_connection_pool()
        initial = _db_state.connections_available

        with acquire_db_connection() as acquired:
            assert acquired is True
            assert _db_state.connections_available == initial - 1

        # Connection should be released after context
        assert _db_state.connections_available == initial

    def test_acquire_connection_pool_exhausted(self):
        """커넥션 풀 고갈 시 획득 실패"""
        from load_tests.scenarios.stage34_db_deadlock import (
            acquire_db_connection,
            _db_state,
        )

        # Exhaust pool
        _db_state.connections_available = 0

        with acquire_db_connection(timeout=0.1) as acquired:
            assert acquired is False

        # Restore
        _db_state.connections_available = _db_state.connections_max

    def test_get_pool_utilization(self):
        """풀 사용률 조회"""
        from load_tests.scenarios.stage34_db_deadlock import (
            _db_state,
            get_pool_utilization,
            DB_CONNECTION_POOL_SIZE,
        )

        # Full pool
        _db_state.connections_available = DB_CONNECTION_POOL_SIZE
        assert get_pool_utilization() == 0.0

        # Half used
        _db_state.connections_available = DB_CONNECTION_POOL_SIZE // 2
        assert get_pool_utilization() == 0.5

        # Empty pool
        _db_state.connections_available = 0
        assert get_pool_utilization() == 1.0

        # Restore
        _db_state.connections_available = DB_CONNECTION_POOL_SIZE

    def test_reset_connection_pool(self):
        """풀 리셋"""
        from load_tests.scenarios.stage34_db_deadlock import (
            _db_state,
            reset_connection_pool,
            DB_CONNECTION_POOL_SIZE,
        )

        _db_state.connections_available = 10
        _db_state.connection_waiters = 5

        reset_connection_pool()

        assert _db_state.connections_available == DB_CONNECTION_POOL_SIZE
        assert _db_state.connection_waiters == 0


class TestLockManagement:
    """Lock 관리 테스트"""

    def test_acquire_lock_success(self):
        """Lock 획득 성공"""
        from load_tests.scenarios.stage34_db_deadlock import (
            acquire_lock,
            release_lock,
            _db_state,
        )

        # Clear locks
        _db_state.locked_resources.clear()

        success, wait_time = acquire_lock("resource_1", "holder_1")

        assert success is True
        assert wait_time >= 0
        assert _db_state.locked_resources.get("resource_1") == "holder_1"

        # Cleanup
        release_lock("resource_1", "holder_1")

    def test_acquire_lock_already_owned(self):
        """이미 소유한 Lock 재획득"""
        from load_tests.scenarios.stage34_db_deadlock import (
            acquire_lock,
            release_lock,
            _db_state,
        )

        _db_state.locked_resources.clear()

        # First acquire
        success1, _ = acquire_lock("resource_1", "holder_1")
        assert success1 is True

        # Second acquire by same holder
        success2, wait_time = acquire_lock("resource_1", "holder_1")
        assert success2 is True
        assert wait_time == 0  # Immediate

        # Cleanup
        release_lock("resource_1", "holder_1")

    def test_acquire_lock_timeout(self):
        """Lock 획득 타임아웃"""
        from load_tests.scenarios.stage34_db_deadlock import (
            acquire_lock,
            release_lock,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.locked_resources["resource_1"] = "holder_1"

        # Try to acquire by different holder
        success, wait_time = acquire_lock("resource_1", "holder_2", timeout=0.1)

        assert success is False
        assert wait_time >= 100  # At least 100ms

        # Cleanup
        _db_state.locked_resources.clear()

    def test_release_lock(self):
        """Lock 해제"""
        from load_tests.scenarios.stage34_db_deadlock import (
            release_lock,
            _db_state,
        )

        _db_state.locked_resources["resource_1"] = "holder_1"

        release_lock("resource_1", "holder_1")

        assert "resource_1" not in _db_state.locked_resources

    def test_release_lock_wrong_holder(self):
        """다른 holder가 Lock 해제 시도"""
        from load_tests.scenarios.stage34_db_deadlock import (
            release_lock,
            _db_state,
        )

        _db_state.locked_resources["resource_1"] = "holder_1"

        release_lock("resource_1", "holder_2")  # Wrong holder

        # Lock should still be held
        assert _db_state.locked_resources.get("resource_1") == "holder_1"

        # Cleanup
        _db_state.locked_resources.clear()


class TestDeadlockDetection:
    """Deadlock 감지 테스트"""

    def test_detect_deadlock_no_cycle(self):
        """순환 없음 - Deadlock 아님"""
        from load_tests.scenarios.stage34_db_deadlock import (
            detect_deadlock,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.lock_wait_queue.clear()

        _db_state.locked_resources["resource_1"] = "holder_1"

        # holder_2 waits for resource_1, but no cycle
        is_deadlock = detect_deadlock("holder_2", "resource_1")

        assert is_deadlock is False

        # Cleanup
        _db_state.locked_resources.clear()

    def test_detect_deadlock_cycle(self):
        """순환 존재 - Deadlock 감지"""
        from load_tests.scenarios.stage34_db_deadlock import (
            detect_deadlock,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.lock_wait_queue.clear()

        # holder_1 holds resource_1, holder_2 holds resource_2
        _db_state.locked_resources["resource_1"] = "holder_1"
        _db_state.locked_resources["resource_2"] = "holder_2"

        # holder_2 waits for resource_1
        _db_state.lock_wait_queue["resource_1"].append("holder_2")

        # holder_1 waits for resource_2 -> cycle!
        is_deadlock = detect_deadlock("holder_1", "resource_2")

        assert is_deadlock is True

        # Cleanup
        _db_state.locked_resources.clear()
        _db_state.lock_wait_queue.clear()

    def test_resolve_deadlock(self):
        """Deadlock 해결"""
        from load_tests.scenarios.stage34_db_deadlock import (
            resolve_deadlock,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.lock_wait_queue.clear()

        # Set up deadlock scenario
        _db_state.locked_resources["resource_1"] = "holder_1"
        _db_state.locked_resources["resource_2"] = "holder_1"
        _db_state.lock_wait_queue["resource_3"].append("holder_1")

        resolve_deadlock("holder_1")

        # All locks should be released
        assert "resource_1" not in _db_state.locked_resources
        assert "resource_2" not in _db_state.locked_resources


class TestStockOperations:
    """재고 연산 테스트"""

    def test_update_stock_success(self):
        """재고 업데이트 성공"""
        from load_tests.scenarios.stage34_db_deadlock import (
            update_stock,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.product_stock["product_test"] = 100

        success, error = update_stock("product_test", -10, "txn_1")

        assert success is True
        assert error == ""
        assert _db_state.product_stock["product_test"] == 90

    def test_update_stock_insufficient(self):
        """재고 부족"""
        from load_tests.scenarios.stage34_db_deadlock import (
            update_stock,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.product_stock["product_test"] = 5

        success, error = update_stock("product_test", -10, "txn_1")

        assert success is False
        assert error == "insufficient_stock"


class TestBalanceOperations:
    """잔액 연산 테스트"""

    def test_update_balance_success(self):
        """잔액 업데이트 성공"""
        from load_tests.scenarios.stage34_db_deadlock import (
            update_balance,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.user_balances["user_test"] = 1000.0

        success, error = update_balance("user_test", -100.0, "txn_1")

        assert success is True
        assert error == ""
        assert _db_state.user_balances["user_test"] == 900.0

    def test_update_balance_insufficient(self):
        """잔액 부족"""
        from load_tests.scenarios.stage34_db_deadlock import (
            update_balance,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.user_balances["user_test"] = 50.0

        success, error = update_balance("user_test", -100.0, "txn_1")

        assert success is False
        assert error == "insufficient_balance"


class TestPointOperations:
    """포인트 연산 테스트"""

    def test_update_points_success(self):
        """포인트 업데이트 성공"""
        from load_tests.scenarios.stage34_db_deadlock import (
            update_points,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.user_points["user_test"] = 500

        success, error = update_points("user_test", 100, "txn_1")

        assert success is True
        assert error == ""
        assert _db_state.user_points["user_test"] == 600


class TestOrderProcessing:
    """주문 처리 테스트"""

    def test_process_order_success(self):
        """주문 처리 성공"""
        from load_tests.scenarios.stage34_db_deadlock import (
            process_order_with_retry,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.product_stock["product_0"] = 100
        _db_state.product_stock["product_1"] = 100

        success, error = process_order_with_retry("user_1", ["product_0", "product_1"], [5, 3])

        assert success is True
        assert error == ""
        assert _db_state.product_stock["product_0"] == 95
        assert _db_state.product_stock["product_1"] == 97


class TestPaymentProcessing:
    """결제 처리 테스트"""

    def test_process_payment_with_points_success(self):
        """결제 + 포인트 적립 성공"""
        from load_tests.scenarios.stage34_db_deadlock import (
            process_payment_with_points,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.user_balances["user_test"] = 1000.0
        _db_state.user_points["user_test"] = 100

        success, error = process_payment_with_points("user_test", 100.0, 10)

        assert success is True
        assert error == ""
        assert _db_state.user_balances["user_test"] == 900.0
        assert _db_state.user_points["user_test"] == 110


class TestDBDeadlockStats:
    """DBDeadlockStats 관련 테스트"""

    def test_stats_initialization(self):
        """통계 초기화 테스트"""
        from load_tests.scenarios.stage34_db_deadlock import DBDeadlockStats

        stats = DBDeadlockStats()

        assert stats.total_requests == 0
        assert stats.successful_requests == 0
        assert stats.order_deadlocks == 0
        assert stats.phase == "baseline"

    def test_verification_fields(self):
        """검증 필드 테스트"""
        from load_tests.scenarios.stage34_db_deadlock import DBDeadlockStats

        stats = DBDeadlockStats()

        assert "deadlock_detection_under_3s" in stats.verification
        assert "auto_retry_success_95" in stats.verification
        assert "data_consistency_100" in stats.verification
        assert "pool_recovery_under_10s" in stats.verification


class TestPhaseManagement:
    """Phase 관리 테스트"""

    def test_phase_transitions(self):
        """Phase 전환 테스트"""
        from load_tests.scenarios.stage34_db_deadlock import (
            _deadlock_stats,
            _get_current_phase,
            PHASE_1_BASELINE,
        )

        # 시작 전
        _deadlock_stats.start_time = None
        assert _get_current_phase() == "baseline"

        # 시작 직후
        _deadlock_stats.start_time = time.time()
        assert _get_current_phase() == "baseline"

        # 시간 경과 후
        _deadlock_stats.start_time = time.time() - PHASE_1_BASELINE - 1
        phase = _get_current_phase()
        assert phase in ["order_deadlock", "payment_point", "pool_deadlock", "verification"]


class TestScenario1OrderDeadlock:
    """Scenario 1: Concurrent Order Deadlock 테스트"""

    def test_concurrent_order_no_deadlock(self):
        """동시 주문 - Deadlock 없음"""
        from load_tests.scenarios.stage34_db_deadlock import (
            process_order_with_retry,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.product_stock["product_0"] = 1000

        # Sequential orders (no deadlock)
        success1, _ = process_order_with_retry("user_1", ["product_0"], [10])
        success2, _ = process_order_with_retry("user_2", ["product_0"], [10])

        assert success1 is True
        assert success2 is True
        assert _db_state.product_stock["product_0"] == 980

    def test_stock_consistency_after_operations(self):
        """연산 후 재고 일관성"""
        from load_tests.scenarios.stage34_db_deadlock import (
            _db_state,
            update_stock,
            NUM_PRODUCTS,
            INITIAL_STOCK,
        )

        _db_state.locked_resources.clear()

        # Reset stock
        for i in range(NUM_PRODUCTS):
            _db_state.product_stock[f"product_{i}"] = INITIAL_STOCK

        # Perform updates
        for i in range(10):
            update_stock(f"product_{i % NUM_PRODUCTS}", -10, f"txn_{i}")

        # Verify total stock change
        total_stock = sum(_db_state.product_stock.values())
        expected_total = NUM_PRODUCTS * INITIAL_STOCK - 100  # 10 updates * 10 each

        assert total_stock == expected_total


class TestScenario2PaymentPointDeadlock:
    """Scenario 2: Payment + Point Deadlock 테스트"""

    def test_payment_point_no_deadlock(self):
        """결제 + 포인트 - Deadlock 없음"""
        from load_tests.scenarios.stage34_db_deadlock import (
            process_payment_with_points,
            _db_state,
        )

        _db_state.locked_resources.clear()
        _db_state.user_balances["user_1"] = 10000.0
        _db_state.user_points["user_1"] = 0

        success, _ = process_payment_with_points("user_1", 100.0, 10)

        assert success is True
        assert _db_state.user_balances["user_1"] == 9900.0
        assert _db_state.user_points["user_1"] == 10


class TestScenario3PoolDeadlock:
    """Scenario 3: Pool + Deadlock 테스트"""

    def test_pool_exhaustion_detection(self):
        """풀 고갈 감지"""
        from load_tests.scenarios.stage34_db_deadlock import (
            _db_state,
            _deadlock_stats,
            acquire_db_connection,
        )

        initial_events = _deadlock_stats.pool_exhaustion_events
        _db_state.connections_available = 0

        with acquire_db_connection(timeout=0.1) as acquired:
            pass

        assert _deadlock_stats.pool_exhaustion_events > initial_events

        # Restore
        _db_state.connections_available = _db_state.connections_max


class TestVerification:
    """검증 로직 테스트"""

    def test_all_verifications_pass(self):
        """모든 검증 통과 시나리오"""
        from load_tests.scenarios.stage34_db_deadlock import DBDeadlockStats

        stats = DBDeadlockStats()

        # Set up passing scenario
        stats.deadlock_detection_time_ms = [500, 1000, 2000]  # All under 3s
        stats.auto_retry_attempts = 10
        stats.auto_retry_success = 10  # 100% success
        stats.stock_consistency_errors = 0
        stats.balance_consistency_errors = 0
        stats.pool_recovery_time_ms = [1000, 5000, 8000]  # All under 10s

        # Verify
        stats.verification["deadlock_detection_under_3s"] = max(stats.deadlock_detection_time_ms) < 3000
        stats.verification["auto_retry_success_95"] = (stats.auto_retry_success / stats.auto_retry_attempts) >= 0.95
        stats.verification["data_consistency_100"] = (stats.stock_consistency_errors + stats.balance_consistency_errors) == 0
        stats.verification["pool_recovery_under_10s"] = max(stats.pool_recovery_time_ms) < 10000

        assert all(v for v in stats.verification.values() if v is not None)

    def test_deadlock_detection_fails(self):
        """Deadlock 감지 시간 초과 실패"""
        from load_tests.scenarios.stage34_db_deadlock import DBDeadlockStats

        stats = DBDeadlockStats()
        stats.deadlock_detection_time_ms = [500, 4000]  # One exceeds 3s

        stats.verification["deadlock_detection_under_3s"] = max(stats.deadlock_detection_time_ms) < 3000

        assert stats.verification["deadlock_detection_under_3s"] is False

    def test_retry_success_fails(self):
        """Retry 성공률 미달 실패"""
        from load_tests.scenarios.stage34_db_deadlock import DBDeadlockStats

        stats = DBDeadlockStats()
        stats.auto_retry_attempts = 100
        stats.auto_retry_success = 90  # 90% < 95%

        stats.verification["auto_retry_success_95"] = (stats.auto_retry_success / stats.auto_retry_attempts) >= 0.95

        assert stats.verification["auto_retry_success_95"] is False


class TestIntegration:
    """통합 테스트"""

    def test_full_deadlock_scenario(self):
        """전체 Deadlock 시나리오"""
        from load_tests.scenarios.stage34_db_deadlock import (
            _db_state,
            _deadlock_stats,
            process_order_with_retry,
            process_payment_with_points,
            reset_connection_pool,
            DBDeadlockStats,
        )

        # Reset state
        reset_connection_pool()
        _db_state.locked_resources.clear()
        _db_state.product_stock["product_0"] = 1000
        _db_state.user_balances["user_1"] = 10000.0
        _db_state.user_points["user_1"] = 0

        stats = DBDeadlockStats()
        stats.start_time = time.time()

        # Process order
        order_success, order_error = process_order_with_retry("user_1", ["product_0"], [10])
        assert order_success is True
        stats.order_success += 1

        # Process payment
        payment_success, payment_error = process_payment_with_points("user_1", 100.0, 10)
        assert payment_success is True
        stats.payment_point_success += 1

        # Verify data
        assert _db_state.product_stock["product_0"] == 990
        assert _db_state.user_balances["user_1"] == 9900.0
        assert _db_state.user_points["user_1"] == 10

    def test_concurrent_operations(self):
        """동시 연산 테스트"""
        import threading
        from load_tests.scenarios.stage34_db_deadlock import (
            _db_state,
            process_order_with_retry,
            reset_connection_pool,
        )

        reset_connection_pool()
        _db_state.locked_resources.clear()
        _db_state.product_stock["product_0"] = 10000

        results = []

        def order_task(user_id):
            success, _ = process_order_with_retry(user_id, ["product_0"], [10])
            results.append(success)

        # Run concurrent orders
        threads = [threading.Thread(target=order_task, args=(f"user_{i}",)) for i in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        success_count = sum(1 for r in results if r)
        assert success_count >= 5  # At least half should succeed

        # Stock should be consistent
        expected_deduction = success_count * 10
        assert _db_state.product_stock["product_0"] == 10000 - expected_deduction


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
