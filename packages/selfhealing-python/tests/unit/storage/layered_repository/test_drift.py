"""
드리프트 복구 테스트.
"""

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


class TestDriftReconciliation:
    """드리프트 복구 테스트."""

    def setup_method(self):
        """각 테스트 전 DriftReconciler 초기화."""
        from selfhealing.adapters.memory.circuit_breaker import get_drift_reconciler
        self.reconciler = get_drift_reconciler()
        self.reconciler.clear_history()

    def test_most_restrictive_wins_open_vs_closed(self):
        """OPEN > CLOSED 우선순위."""
        from selfhealing.adapters.memory.circuit_breaker import (
            DriftReconciler, DriftReconciliationResult,
        )
        
        reconciler = DriftReconciler()
        
        # When: OPEN vs CLOSED
        winner_state, result = reconciler.reconcile(
            service_name="test-service",
            l1_state="open",
            l2_state="closed",
        )
        
        # Then: OPEN이 승리 (더 제한적)
        assert winner_state == "open"
        assert result == DriftReconciliationResult.L1_WINS

    def test_most_restrictive_wins_half_open_vs_closed(self):
        """HALF_OPEN > CLOSED 우선순위."""
        from selfhealing.adapters.memory.circuit_breaker import (
            DriftReconciler, DriftReconciliationResult,
        )
        
        reconciler = DriftReconciler()
        
        # When: HALF_OPEN vs CLOSED
        winner_state, result = reconciler.reconcile(
            service_name="test-service",
            l1_state="half_open",
            l2_state="closed",
        )
        
        # Then: HALF_OPEN이 승리
        assert winner_state == "half_open"
        assert result == DriftReconciliationResult.L1_WINS

    def test_most_restrictive_wins_open_vs_half_open(self):
        """OPEN > HALF_OPEN 우선순위."""
        from selfhealing.adapters.memory.circuit_breaker import (
            DriftReconciler, DriftReconciliationResult,
        )
        
        reconciler = DriftReconciler()
        
        # When: HALF_OPEN vs OPEN
        winner_state, result = reconciler.reconcile(
            service_name="test-service",
            l1_state="half_open",
            l2_state="open",
        )
        
        # Then: OPEN이 승리 (L2)
        assert winner_state == "open"
        assert result == DriftReconciliationResult.L2_WINS

    def test_timestamp_tiebreaker_same_state(self):
        """같은 상태면 타임스탬프로 결정."""
        from selfhealing.adapters.memory.circuit_breaker import (
            DriftReconciler, DriftReconciliationResult,
        )
        
        reconciler = DriftReconciler()
        
        now = datetime.now(timezone.utc)
        l1_time = now - timedelta(seconds=5)
        l2_time = now  # L2가 더 최신
        
        # When: 같은 상태, L2가 더 최신
        winner_state, result = reconciler.reconcile(
            service_name="test-service",
            l1_state="open",
            l2_state="open",
            l1_updated_at=l1_time,
            l2_updated_at=l2_time,
        )
        
        # Then: 같은 상태면 드리프트 없음
        assert result == DriftReconciliationResult.NO_DRIFT

    def test_timestamp_tiebreaker_different_priority_same_level(self):
        """같은 우선순위 레벨에서 타임스탬프로 결정."""
        from selfhealing.adapters.memory.circuit_breaker import (
            DriftReconciler, DriftReconciliationResult,
        )
        
        reconciler = DriftReconciler()
        
        now = datetime.now(timezone.utc)
        l1_time = now
        l2_time = now - timedelta(seconds=5)
        
        winner_state, result = reconciler.reconcile(
            service_name="test-service",
            l1_state="closed",
            l2_state="closed",
            l1_updated_at=l1_time,
            l2_updated_at=l2_time,
        )
        
        # 같은 상태면 NO_DRIFT
        assert result == DriftReconciliationResult.NO_DRIFT

    def test_jitter_applied_to_reconciliation(self):
        """Jitter가 적용되어 지연됨."""
        from selfhealing.adapters.memory.circuit_breaker import DriftReconciler
        
        reconciler = DriftReconciler(
            min_jitter_seconds=0.0,
            max_jitter_seconds=0.01,  # 10ms
        )
        
        executed = []
        
        def do_reconcile():
            executed.append(True)
        
        # When: 스케줄 실행
        jitter = reconciler.schedule_reconciliation_sync(
            service_name="test-service",
            do_reconcile=do_reconcile,
        )
        
        # Then: 실행 완료 및 Jitter 값 반환
        assert len(executed) == 1
        assert 0.0 <= jitter <= 0.01

    def test_jitter_distribution(self):
        """Jitter가 균등 분포."""
        from selfhealing.adapters.memory.circuit_breaker import DriftReconciler
        
        reconciler = DriftReconciler(
            min_jitter_seconds=0.0,
            max_jitter_seconds=5.0,
        )
        
        jitters = [reconciler.get_jitter() for _ in range(1000)]
        
        # 평균이 약 2.5초 근처
        avg = sum(jitters) / len(jitters)
        assert 2.0 < avg < 3.0, f"Jitter 평균이 예상 범위 벗어남: {avg}"
        
        # 최소/최대가 범위 내
        assert min(jitters) >= 0.0
        assert max(jitters) <= 5.0

    def test_thundering_herd_prevention(self):
        """Thundering Herd 방지."""
        from selfhealing.adapters.memory.circuit_breaker import DriftReconciler
        
        # Given: 100개 Reconciler (각 Pod 시뮬레이션)
        jitters = []
        for _ in range(100):
            reconciler = DriftReconciler(
                min_jitter_seconds=0.0,
                max_jitter_seconds=5.0,
            )
            jitters.append(reconciler.get_jitter())
        
        # Then: 고유 값들이 많이 생성됨
        unique_jitters = set(round(j, 2) for j in jitters)
        assert len(unique_jitters) > 50, f"Jitter 분산 부족: {len(unique_jitters)} unique"
        
        # 시간대가 분산됨
        in_first_second = sum(1 for j in jitters if j < 1.0)
        in_last_second = sum(1 for j in jitters if j >= 4.0)
        assert in_first_second > 10, "첫 1초에 충분한 Jitter 분산 없음"
        assert in_last_second > 10, "마지막 1초에 충분한 Jitter 분산 없음"

    def test_reconciler_history_tracking(self):
        """복구 기록 추적."""
        from selfhealing.adapters.memory.circuit_breaker import DriftReconciler
        
        reconciler = DriftReconciler()
        reconciler.clear_history()
        
        # When: 여러 복구 실행
        reconciler.reconcile("svc-1", "open", "closed")
        reconciler.reconcile("svc-2", "closed", "open")
        reconciler.reconcile("svc-3", "half_open", "half_open")
        
        # Then: 기록 저장됨
        history = reconciler.get_history()
        assert len(history) == 3
        assert history[0].service_name == "svc-1"
        assert history[1].service_name == "svc-2"
        assert history[2].service_name == "svc-3"

    def test_reconciler_stats(self):
        """복구 통계."""
        from selfhealing.adapters.memory.circuit_breaker import (
            DriftReconciler, DriftReconciliationResult,
        )
        
        reconciler = DriftReconciler()
        reconciler.clear_history()
        
        # When: 여러 복구 실행
        reconciler.reconcile("svc-1", "open", "closed")  # L1 wins
        reconciler.reconcile("svc-2", "closed", "open")  # L2 wins
        reconciler.reconcile("svc-3", "closed", "closed")  # No drift
        
        # Then: 통계 확인
        stats = reconciler.get_stats()
        assert stats["total_reconciliations"] == 3
        assert stats["by_result"]["l1_wins"] == 1
        assert stats["by_result"]["l2_wins"] == 1
        assert stats["by_result"]["no_drift"] == 1
        assert len(stats["affected_services"]) == 3
