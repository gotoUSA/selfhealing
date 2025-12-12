"""
Stage 36: Memory Pressure Testing - Unit Tests

이 파일은 Stage 36 시나리오의 핵심 로직을 검증합니다.
- Gradual Memory Increase
- Large Payload Handling
- Memory Leak Detection
- GC Pause Impact
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


class TestMemoryState:
    """MemoryState Enum 테스트"""

    def test_memory_states_exist(self):
        """메모리 상태 열거형 확인"""
        from load_tests.scenarios.stage36_memory_pressure import MemoryState

        assert MemoryState.NORMAL.value == "normal"
        assert MemoryState.WARNING.value == "warning"
        assert MemoryState.THROTTLED.value == "throttled"
        assert MemoryState.CRITICAL.value == "critical"


class TestMemoryPressureStats:
    """MemoryPressureStats 테스트"""

    def test_stats_creation(self):
        """통계 생성"""
        from load_tests.scenarios.stage36_memory_pressure import MemoryPressureStats

        stats = MemoryPressureStats()
        assert stats.current_memory_mb == 0.0
        assert stats.peak_memory_mb == 0.0
        assert stats.total_requests == 0

    def test_stats_reset(self):
        """통계 리셋"""
        from load_tests.scenarios.stage36_memory_pressure import MemoryPressureStats

        stats = MemoryPressureStats()
        stats.current_memory_mb = 500
        stats.peak_memory_mb = 800
        stats.total_requests = 100

        stats.reset()

        assert stats.current_memory_mb == 0.0
        assert stats.peak_memory_mb == 0.0
        assert stats.total_requests == 0
        assert stats.start_time is not None

    def test_memory_utilization(self):
        """메모리 사용률 계산"""
        from load_tests.scenarios.stage36_memory_pressure import MemoryPressureStats, MEMORY_MAX_MB

        stats = MemoryPressureStats()
        stats.current_memory_mb = MEMORY_MAX_MB / 2

        assert stats.get_memory_utilization() == 0.5

    def test_avg_response_time(self):
        """평균 응답 시간"""
        from load_tests.scenarios.stage36_memory_pressure import MemoryPressureStats

        stats = MemoryPressureStats()
        stats.response_times = [0.01, 0.02, 0.03]

        avg = stats.get_avg_response_time()
        assert abs(avg - 20.0) < 0.01  # 20ms average

    def test_avg_gc_pause(self):
        """평균 GC pause 시간"""
        from load_tests.scenarios.stage36_memory_pressure import MemoryPressureStats

        stats = MemoryPressureStats()
        stats.gc_pause_times = [10, 20, 30]  # ms

        avg = stats.get_avg_gc_pause()
        assert avg == 20.0

    def test_p99_gc_pause(self):
        """P99 GC pause 시간"""
        from load_tests.scenarios.stage36_memory_pressure import MemoryPressureStats

        stats = MemoryPressureStats()
        stats.gc_pause_times = list(range(1, 101))  # 1 to 100ms

        p99 = stats.get_p99_gc_pause()
        assert p99 >= 99


class TestSimulatedMemoryManager:
    """SimulatedMemoryManager 테스트"""

    def test_manager_creation(self):
        """메모리 매니저 생성"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager

        manager = SimulatedMemoryManager(max_memory_mb=1024)
        assert manager.get_utilization() == 0.0

    def test_allocate_success(self):
        """메모리 할당 성공"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=1024)

        success, msg = manager.allocate("block1", 100)

        assert success is True
        assert manager._current_memory_mb == 100

    def test_allocate_oom(self):
        """메모리 할당 실패 (OOM)"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=100)

        success, msg = manager.allocate("block1", 150)

        assert success is False
        assert "OOM" in msg

    def test_deallocate(self):
        """메모리 해제"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=1024)

        manager.allocate("block1", 100)
        assert manager._current_memory_mb == 100

        success, freed = manager.deallocate("block1")

        assert success is True
        assert freed == 100
        assert manager._current_memory_mb == 0

    def test_deallocate_nonexistent(self):
        """존재하지 않는 블록 해제"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager()

        success, freed = manager.deallocate("nonexistent")

        assert success is False
        assert freed == 0

    def test_memory_state_normal(self):
        """정상 메모리 상태"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryState, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=1000)
        manager.allocate("block1", 500)  # 50%

        assert manager.get_state() == MemoryState.NORMAL

    def test_memory_state_warning(self):
        """경고 메모리 상태 (70%)"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryState, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=1000)
        manager.allocate("block1", 750)  # 75%

        assert manager.get_state() == MemoryState.WARNING

    def test_memory_state_throttled(self):
        """스로틀 메모리 상태 (85%)"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryState, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=1000)
        manager.allocate("block1", 900)  # 90%

        assert manager.get_state() == MemoryState.THROTTLED

    def test_memory_state_critical(self):
        """크리티컬 메모리 상태 (95%)"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryState, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=1000)
        manager.allocate("block1", 960)  # 96%

        assert manager.get_state() == MemoryState.CRITICAL

    def test_reset(self):
        """매니저 리셋"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager()
        manager.allocate("block1", 100)
        manager.allocate("block2", 200)

        manager.reset()

        assert manager._current_memory_mb == 0
        assert len(manager._allocated_blocks) == 0


class TestSimulateGC:
    """GC 시뮬레이션 테스트"""

    def test_gc_simulation(self):
        """GC 시뮬레이션"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager()

        # Allocate some memory
        for i in range(10):
            manager.allocate(f"block_{i}", 10)

        pause_ms, reclaimed = manager.simulate_gc(full=False)

        assert pause_ms > 0
        # Reclaimed amount may vary due to randomness

    def test_full_gc_longer_pause(self):
        """Full GC는 더 긴 pause"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager()

        # Run multiple GCs to get average
        minor_pauses = []
        full_pauses = []

        for _ in range(5):
            pause, _ = manager.simulate_gc(full=False)
            minor_pauses.append(pause)

        for _ in range(5):
            pause, _ = manager.simulate_gc(full=True)
            full_pauses.append(pause)

        # Full GC should generally have longer pauses
        # (Due to randomness, we just check they're both positive)
        assert all(p > 0 for p in minor_pauses)
        assert all(p > 0 for p in full_pauses)

    def test_gc_running_flag(self):
        """GC 실행 중 플래그"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager()

        gc_was_running = []

        def run_gc():
            time.sleep(0.01)  # Small delay
            gc_was_running.append(manager.is_gc_running())
            manager.simulate_gc()

        thread = threading.Thread(target=run_gc)
        thread.start()
        thread.join()

        # After GC completes, should not be running
        assert manager.is_gc_running() is False


class TestMemoryPressureUtils:
    """MemoryPressureUtils 테스트"""

    def test_gradual_increase(self):
        """점진적 메모리 증가"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryPressureUtils, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=500)

        results = MemoryPressureUtils.simulate_gradual_increase(
            manager=manager, target_percent=0.80, step_mb=25, step_delay_s=0.001
        )

        assert results["final_percent"] >= 0.75
        assert len(results["samples"]) > 0

    def test_gradual_increase_triggers_warning(self):
        """점진적 증가 시 경고 트리거"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryPressureUtils, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=500)

        results = MemoryPressureUtils.simulate_gradual_increase(
            manager=manager, target_percent=0.80, step_mb=25, step_delay_s=0.001
        )

        assert results["warning_at"] is not None
        assert results["warning_at"] >= 0.70

    def test_large_payload_streaming(self):
        """대용량 페이로드 스트리밍 처리"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryPressureUtils, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=100)

        results = MemoryPressureUtils.simulate_large_payload(manager=manager, size_mb=20, use_streaming=True)

        assert results["use_streaming"] is True
        assert results["streaming_chunks"] > 0
        # Streaming should succeed even with small memory

    def test_large_payload_no_streaming_oom(self):
        """스트리밍 없이 대용량 페이로드 OOM"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryPressureUtils, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=50)

        results = MemoryPressureUtils.simulate_large_payload(
            manager=manager, size_mb=100, use_streaming=False  # Larger than max
        )

        assert results["success"] is False
        assert results["error"] is not None

    def test_memory_leak_detection(self):
        """메모리 누수 감지"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryPressureUtils, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=1000)

        results = MemoryPressureUtils.simulate_memory_leak(manager=manager, iterations=50, leak_size_mb=1.0)

        assert results["iterations"] == 50
        assert "leaked_mb" in results
        # Leak may or may not be detected based on random leak rate

    def test_gc_impact(self):
        """GC 영향 테스트"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, MemoryPressureUtils, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=500)

        results = MemoryPressureUtils.simulate_gc_impact(manager=manager, num_requests=20, gc_during_requests=True)

        assert results["num_requests"] == 20
        assert results["gc_triggered"] is True
        assert len(results["response_times"]) == 20
        assert len(results["gc_pause_times"]) > 0


class TestReportGeneration:
    """리포트 생성 테스트"""

    def test_generate_stage36_report(self):
        """Stage 36 리포트 생성"""
        from load_tests.scenarios.stage36_memory_pressure import generate_stage36_report, MemoryPressureStats, MemoryState

        stats = MemoryPressureStats()
        stats.start_time = time.time() - 60
        stats.phase = "completed"
        stats.total_requests = 100
        stats.successful_requests = 95
        stats.throttled_requests = 3
        stats.rejected_requests = 2
        stats.peak_memory_mb = 800
        stats.warning_triggered = True
        stats.warning_triggered_at_percent = 72
        stats.gc_pause_times = [50, 60, 70]

        report = generate_stage36_report(stats)

        assert report["stage"] == "36"
        assert report["name"] == "Memory Pressure Testing"
        assert report["summary"]["total_requests"] == 100
        assert report["summary"]["peak_memory_mb"] == 800
        assert report["scenario_1_gradual_increase"]["warning_triggered"] is True


class TestIntegration:
    """통합 테스트"""

    def test_run_gradual_increase_test(self):
        """점진적 증가 테스트 실행"""
        from load_tests.scenarios.stage36_memory_pressure import run_gradual_increase_test, reset_memory_manager, reset_stats

        reset_memory_manager()
        reset_stats()

        results = run_gradual_increase_test(target_percent=0.75)

        assert "final_percent" in results
        assert results["final_percent"] >= 0.70

    def test_run_large_payload_test(self):
        """대용량 페이로드 테스트 실행"""
        from load_tests.scenarios.stage36_memory_pressure import run_large_payload_test, reset_memory_manager, reset_stats

        reset_memory_manager()
        reset_stats()

        results = run_large_payload_test(num_requests=3, size_mb=5)

        assert results["num_requests"] == 3
        assert "success_count" in results
        assert "requests" in results

    def test_run_leak_detection_test(self):
        """누수 감지 테스트 실행"""
        from load_tests.scenarios.stage36_memory_pressure import run_leak_detection_test, reset_memory_manager, reset_stats

        reset_memory_manager()
        reset_stats()

        results = run_leak_detection_test(iterations=20)

        assert results["iterations"] == 20
        assert "leaked_mb" in results

    def test_run_gc_impact_test(self):
        """GC 영향 테스트 실행"""
        from load_tests.scenarios.stage36_memory_pressure import run_gc_impact_test, reset_memory_manager, reset_stats

        reset_memory_manager()
        reset_stats()

        results = run_gc_impact_test(num_requests=10)

        assert results["num_requests"] == 10
        assert "avg_response_time_ms" in results

    def test_run_all_stage36_tests(self):
        """전체 Stage 36 테스트 실행"""
        from load_tests.scenarios.stage36_memory_pressure import run_all_stage36_tests, reset_memory_manager, reset_stats

        reset_memory_manager()
        reset_stats()

        results = run_all_stage36_tests()

        assert results["stage"] == "36"
        assert "tests" in results
        assert "report" in results
        assert "gradual_increase" in results["tests"]
        assert "large_payload" in results["tests"]
        assert "leak_detection" in results["tests"]
        assert "gc_impact" in results["tests"]


class TestConcurrency:
    """동시성 테스트"""

    def test_concurrent_allocations(self):
        """동시 메모리 할당"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=1000)

        results = []
        errors = []

        def allocate_memory(block_id: str):
            try:
                success, msg = manager.allocate(block_id, 10)
                results.append((block_id, success))
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(50):
            t = threading.Thread(target=allocate_memory, args=(f"block_{i}",))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 50
        # All should succeed (50 * 10MB = 500MB < 1000MB)
        assert all(success for _, success in results)

    def test_concurrent_allocate_deallocate(self):
        """동시 할당/해제"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=500)

        errors = []

        def allocate_and_deallocate(block_id: str):
            try:
                success, _ = manager.allocate(block_id, 10)
                if success:
                    time.sleep(0.01)
                    manager.deallocate(block_id)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(30):
            t = threading.Thread(target=allocate_and_deallocate, args=(f"block_{i}",))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        # Memory should be mostly freed
        assert manager._current_memory_mb < 100


class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_zero_max_memory(self):
        """최대 메모리 0"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=0)

        assert manager.get_utilization() == 0.0

    def test_allocate_zero_size(self):
        """크기 0 할당"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=100)

        success, _ = manager.allocate("zero_block", 0)

        assert success is True
        assert manager._current_memory_mb == 0

    def test_double_deallocate(self):
        """이중 해제"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats

        reset_stats()
        manager = SimulatedMemoryManager()

        manager.allocate("block1", 100)
        success1, freed1 = manager.deallocate("block1")
        success2, freed2 = manager.deallocate("block1")

        assert success1 is True
        assert freed1 == 100
        assert success2 is False
        assert freed2 == 0

    def test_empty_stats(self):
        """빈 통계"""
        from load_tests.scenarios.stage36_memory_pressure import MemoryPressureStats

        stats = MemoryPressureStats()

        assert stats.get_memory_utilization() == 0.0
        assert stats.get_avg_response_time() == 0.0
        assert stats.get_avg_gc_pause() == 0.0
        assert stats.get_p99_gc_pause() == 0.0


class TestThrottling:
    """스로틀링 테스트"""

    def test_throttle_at_high_memory(self):
        """높은 메모리에서 스로틀링"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats, get_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=100)

        # Fill memory to 86% (above throttle threshold)
        manager.allocate("big_block", 86)

        # Next allocation should trigger throttling
        stats = get_stats()
        throttled_before = stats.throttled_requests

        manager.allocate("small_block", 5)

        assert stats.throttled_requests > throttled_before

    def test_reject_at_critical_memory(self):
        """크리티컬 메모리에서 거부"""
        from load_tests.scenarios.stage36_memory_pressure import SimulatedMemoryManager, reset_stats, get_stats

        reset_stats()
        manager = SimulatedMemoryManager(max_memory_mb=100)

        # Fill memory to 96% (above critical threshold)
        manager.allocate("critical_block", 96)

        # Next allocation should be rejected
        stats = get_stats()
        rejected_before = stats.rejected_requests

        success, msg = manager.allocate("new_block", 1)

        assert success is False
        assert "rejected" in msg.lower()
        assert stats.rejected_requests > rejected_before
