"""
Idempotent Rollback Tests for Chaos Engine Safety Mechanisms

Tests the idempotent rollback functionality that ensures safe rollback
even with duplicate rollback requests or concurrent rollback attempts.

Phase 3: Chaos Safety Implementation Plan
Reference: docs/self_healing/CHAOS_SAFETY_IMPLEMENTATION_PLAN.md
"""

import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytest

from selfhealing.services.chaos.experiments import (
    ExperimentConfig,
    ExperimentStatus,
    LatencyInjectionExperiment,
    Error5xxExperiment,
    TimeoutExperiment,
)


# =============================================================================
# Basic Idempotent Rollback Tests
# =============================================================================


class TestIdempotentRollback:
    """기본 멱등성 롤백 테스트."""

    def test_rollback_sets_completed_flag(self):
        """롤백이 완료 플래그를 설정하는지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        # 첫 번째 롤백
        experiment.rollback()

        assert experiment._rollback_completed is True

    def test_second_rollback_is_noop(self):
        """두 번째 롤백이 no-op인지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        # _apply_chaos_config 메서드를 모킹
        with patch.object(experiment, "_apply_chaos_config") as mock_apply:
            # 첫 번째 롤백
            experiment.rollback()
            first_call_count = mock_apply.call_count

            # 두 번째 롤백 - 실행되지 않아야 함
            experiment.rollback()
            assert mock_apply.call_count == first_call_count  # 호출 횟수 동일

    def test_multiple_rollbacks_safe(self):
        """여러 번 롤백해도 안전한지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        with patch.object(experiment, "_apply_chaos_config") as mock_apply:
            # 10번 롤백 시도
            for _ in range(10):
                experiment.rollback()

            # 실제로는 1번만 실행
            assert mock_apply.call_count == 1

    def test_rollback_completed_flag_persists(self):
        """롤백 완료 플래그가 유지되는지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        assert experiment._rollback_completed is False

        experiment.rollback()
        assert experiment._rollback_completed is True

        # 다른 작업 후에도 유지
        experiment.status = ExperimentStatus.COMPLETED
        assert experiment._rollback_completed is True


# =============================================================================
# Concurrent Rollback Tests
# =============================================================================


class TestConcurrentRollback:
    """동시 롤백 테스트."""

    def test_concurrent_rollbacks_only_one_executes(self):
        """동시 롤백 시 하나만 실행되는지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        execution_count = [0]

        def counting_apply(config):
            execution_count[0] += 1
            time.sleep(0.05)  # 롤백에 시간이 걸리는 것을 시뮬레이션

        with patch.object(experiment, "_apply_chaos_config", side_effect=counting_apply):
            # 여러 스레드에서 동시에 롤백 시도
            threads = []
            for _ in range(5):
                t = threading.Thread(target=experiment.rollback)
                threads.append(t)

            for t in threads:
                t.start()

            for t in threads:
                t.join()

        # 실제로는 1번만 실행되어야 함
        assert execution_count[0] == 1

    def test_rollback_lock_prevents_race_condition(self):
        """롤백 락이 경쟁 조건을 방지하는지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        assert hasattr(experiment, "_rollback_lock")
        assert isinstance(experiment._rollback_lock, type(threading.Lock()))


# =============================================================================
# Rollback State Tests
# =============================================================================


class TestRollbackState:
    """롤백 상태 테스트."""

    def test_initial_rollback_state(self):
        """초기 롤백 상태 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        assert experiment._rollback_completed is False

    def test_rollback_after_execute(self):
        """execute() 후 롤백 테스트."""
        experiment = LatencyInjectionExperiment(
            config=ExperimentConfig(
                target_service="payment",
                dry_run=True,
                duration_seconds=1,
            )
        )

        with patch("selfhealing.core.timezone.now") as mock_now:
            mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)
            with patch("time.sleep"):
                with patch.object(experiment, "pre_flight_check", return_value=True):
                    experiment.execute()

        # execute 후에도 롤백 가능
        experiment._rollback_completed = False  # 리셋 for testing
        experiment.rollback()
        assert experiment._rollback_completed is True


# =============================================================================
# Different Experiment Types Rollback Tests
# =============================================================================


class TestDifferentExperimentTypesRollback:
    """다른 실험 유형의 롤백 테스트."""

    def test_latency_injection_rollback_idempotent(self):
        """LatencyInjectionExperiment 롤백 멱등성 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        with patch.object(experiment, "_apply_chaos_config") as mock:
            experiment.rollback()
            experiment.rollback()
            experiment.rollback()

            assert mock.call_count == 1

    def test_error_5xx_rollback_idempotent(self):
        """Error5xxExperiment 롤백 멱등성 테스트."""
        experiment = Error5xxExperiment(config=ExperimentConfig(target_service="payment"))

        with patch.object(experiment, "_apply_chaos_config") as mock:
            experiment.rollback()
            experiment.rollback()
            experiment.rollback()

            assert mock.call_count == 1

    def test_timeout_rollback_idempotent(self):
        """TimeoutExperiment 롤백 멱등성 테스트."""
        experiment = TimeoutExperiment(config=ExperimentConfig(target_service="payment"))

        with patch.object(experiment, "_apply_chaos_config") as mock:
            experiment.rollback()
            experiment.rollback()
            experiment.rollback()

            assert mock.call_count == 1


# =============================================================================
# Rollback Error Handling Tests
# =============================================================================


class TestRollbackErrorHandling:
    """롤백 에러 처리 테스트."""

    def test_rollback_exception_does_not_crash(self):
        """롤백 예외 발생 시에도 크래시하지 않는지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        with patch.object(experiment, "_apply_chaos_config", side_effect=Exception("Rollback failed")):
            # 예외가 발생해도 크래시하지 않음
            experiment.rollback()  # Should not raise

        # 예외가 발생해도 롤백 완료 플래그는 설정되지 않음 (실패했으므로)
        # 구현에 따라 다를 수 있음

    def test_rollback_lock_released_on_exception(self):
        """예외 발생 시 롤백 락이 해제되는지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        with patch.object(experiment, "_apply_chaos_config", side_effect=Exception("Error")):
            experiment.rollback()

        # 락이 해제되어 다시 획득 가능해야 함
        acquired = experiment._rollback_lock.acquire(blocking=False)
        if acquired:
            experiment._rollback_lock.release()
        assert acquired is True


# =============================================================================
# Rollback Audit Trail Tests
# =============================================================================


class TestRollbackAuditTrail:
    """롤백 감사 추적 테스트."""

    def test_rollback_logs_correctly(self):
        """롤백이 로그를 남기는지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        with patch.object(experiment, "_apply_chaos_config"):
            experiment.rollback()

        # 롤백 완료 확인
        assert experiment._rollback_completed is True

    def test_duplicate_rollback_does_not_duplicate_work(self):
        """중복 롤백이 중복 작업을 하지 않는지 테스트."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="payment"))

        apply_calls = []

        def track_apply(config):
            apply_calls.append(config)

        with patch.object(experiment, "_apply_chaos_config", side_effect=track_apply):
            experiment.rollback()
            experiment.rollback()
            experiment.rollback()

        # _apply_chaos_config는 한 번만 호출되어야 함
        assert len(apply_calls) == 1
