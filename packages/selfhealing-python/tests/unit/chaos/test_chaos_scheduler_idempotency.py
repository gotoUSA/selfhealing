"""
ChaosScheduler Idempotency 테스트

순위 6 구현 테스트:
- ChaosScheduler에서 IdempotencyKey.for_chaos_experiment 사용
- 중복 실험 실행 방지
"""

from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from selfhealing.services.idempotency_service import IdempotencyKey, IdempotencyDomain


# =============================================================================
# IdempotencyKey.for_chaos_experiment Tests (순위 6 관련)
# =============================================================================


class TestIdempotencyKeyForChaosExperiment:
    """IdempotencyKey.for_chaos_experiment 테스트."""

    def test_creates_correct_domain(self):
        """
        Purpose:
            for_chaos_experiment가 올바른 도메인을 설정하는지 확인.
        """
        key = IdempotencyKey.for_chaos_experiment(
            schedule_id="sched-123",
            experiment_type="latency_injection",
            target_service="payment",
        )

        assert key.domain == IdempotencyDomain.CHAOS_EXPERIMENT

    def test_includes_all_components_in_key(self):
        """
        Purpose:
            for_chaos_experiment가 모든 컴포넌트를 키에 포함하는지 확인.
        """
        key = IdempotencyKey.for_chaos_experiment(
            schedule_id="sched-456",
            experiment_type="fault_injection",
            target_service="order",
        )

        assert "sched-456" in key.key
        assert "fault_injection" in key.key
        assert "order" in key.key

    def test_stores_components(self):
        """
        Purpose:
            for_chaos_experiment가 컴포넌트를 저장하는지 확인.
        """
        key = IdempotencyKey.for_chaos_experiment(
            schedule_id="sched-789",
            experiment_type="resource_exhaustion",
            target_service="inventory",
        )

        assert key.components["schedule_id"] == "sched-789"
        assert key.components["experiment_type"] == "resource_exhaustion"
        assert key.components["target_service"] == "inventory"

    def test_cache_key_format(self):
        """
        Purpose:
            cache_key가 올바른 형식인지 확인.
        """
        key = IdempotencyKey.for_chaos_experiment(
            schedule_id="test",
            experiment_type="test_type",
            target_service="test_svc",
        )

        assert key.cache_key.startswith("idempotency:")
        assert "chaos_experiment" in key.cache_key


# =============================================================================
# ChaosScheduler Idempotency Method Tests (순위 6)
# =============================================================================


class TestChaosSchedulerIdempotencyMethods:
    """ChaosScheduler Idempotency 메서드 테스트."""

    @pytest.fixture
    def mock_schedule(self):
        """Mock ScheduledExperiment."""
        schedule = MagicMock()
        schedule.id = "test-schedule-123"
        schedule.experiment_type = "latency_injection"
        schedule.target_service = "payment"
        schedule.target_domain = ""
        schedule.blast_radius = "instance"
        schedule.enabled = True
        schedule.approval_status = "auto_approved"
        schedule.experiment_config = {}
        schedule.run_count = 0
        return schedule

    def test_check_idempotency_method_exists(self):
        """
        Purpose:
            _check_idempotency 메서드가 존재하는지 확인.
        """
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService

        scheduler = ChaosSchedulerService()
        assert hasattr(scheduler, "_check_idempotency")
        assert callable(getattr(scheduler, "_check_idempotency"))

    def test_mark_idempotency_processed_method_exists(self):
        """
        Purpose:
            _mark_idempotency_processed 메서드가 존재하는지 확인.
        """
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService

        scheduler = ChaosSchedulerService()
        assert hasattr(scheduler, "_mark_idempotency_processed")
        assert callable(getattr(scheduler, "_mark_idempotency_processed"))

    def test_check_idempotency_returns_none_when_not_duplicate(self, mock_schedule):
        """
        Purpose:
            중복이 아닐 때 None을 반환하는지 확인.
        """
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService

        scheduler = ChaosSchedulerService()
        
        with patch("selfhealing.services.idempotency_service.get_idempotency_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_result = MagicMock()
            mock_result.is_duplicate = False
            mock_svc.check.return_value = mock_result
            mock_get_svc.return_value = mock_svc

            result = scheduler._check_idempotency(
                mock_schedule,
                "test-schedule-123",
                "chaos-abc123",
                datetime.now(),
            )

            assert result is None

    def test_check_idempotency_returns_result_when_duplicate(self, mock_schedule):
        """
        Purpose:
            중복일 때 ExecutionResult를 반환하는지 확인.
        """
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService

        scheduler = ChaosSchedulerService()
        
        with patch("selfhealing.services.idempotency_service.get_idempotency_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_result = MagicMock()
            mock_result.is_duplicate = True
            mock_result.message = "Already processed"
            mock_svc.check.return_value = mock_result
            mock_get_svc.return_value = mock_svc

            result = scheduler._check_idempotency(
                mock_schedule,
                "test-schedule-123",
                "chaos-abc123",
                datetime.now(),
            )

            assert result is not None
            assert result.status == "duplicate"
            assert result.skipped is True

    def test_check_idempotency_graceful_on_import_error(self, mock_schedule):
        """
        Purpose:
            ImportError 시 graceful하게 처리되는지 확인.
        """
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService

        scheduler = ChaosSchedulerService()
        
        # patch import to raise ImportError - 내부 import이므로 idempotency_service 모듈 패치
        with patch.dict("sys.modules", {"selfhealing.services.idempotency_service": None}):
            result = scheduler._check_idempotency(
                mock_schedule,
                "test-schedule-123",
                "chaos-abc123",
                datetime.now(),
            )

            # ImportError → None (통과)
            assert result is None

    def test_mark_idempotency_processed_calls_service(self, mock_schedule):
        """
        Purpose:
            _mark_idempotency_processed가 서비스를 호출하는지 확인.
        """
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService

        scheduler = ChaosSchedulerService()
        
        with patch("selfhealing.services.idempotency_service.get_idempotency_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_get_svc.return_value = mock_svc

            scheduler._mark_idempotency_processed(mock_schedule)

            mock_svc.mark_as_processed.assert_called_once()

