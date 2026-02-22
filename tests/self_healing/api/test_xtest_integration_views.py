"""
Unit Tests for X-Test-Mode Integration Views.

Self-Healing 컴포넌트들의 상호 연동을 검증하기 위한 통합 테스트 API 테스트.

Tests:
- RunScenarioView: 시나리오 실행
- ScenarioStatusView: 시나리오 상태 조회
- FullSnapshotView: 전체 시스템 스냅샷
- ResetView: 시스템 초기화
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIRequestFactory
from rest_framework import status

from selfhealing.api.django.views.xtest.integration import (
    RunScenarioView,
    ScenarioStatusView,
    FullSnapshotView,
    ResetView,
)
from selfhealing.api.django.views.xtest.scenarios import (
    SCENARIO_REGISTRY,
    ScenarioStatus,
    ScenarioResult,
    get_scenario_class,
    list_available_scenarios,
    store_scenario_result,
    get_scenario_result,
    clear_scenario_results,
)


@pytest.fixture
def request_factory():
    """API request factory for creating test requests."""
    return APIRequestFactory()


@pytest.fixture
def chaos_headers():
    """X-Test-Mode required headers."""
    return {"HTTP_X_TEST_MODE": "chaos-monkey"}


@pytest.fixture
def mock_chaos_allowed():
    """Mock XTestModeMixin.is_chaos_allowed to always return True."""
    with patch(
        "selfhealing.api.django.views.xtest.integration.XTestModeMixin.is_chaos_allowed",
        return_value=(True, "Chaos mode allowed"),
    ):
        yield


@pytest.fixture
def mock_cb_service():
    """Mock CircuitBreakerService for testing."""
    mock_service = MagicMock()
    mock_service.get_state.return_value = MagicMock(value="CLOSED")
    mock_service.reset_circuit.return_value = None
    mock_service.record_failure.return_value = None
    mock_service.record_success.return_value = None
    mock_service.should_allow_request.return_value = MagicMock(allowed=False, reason="Circuit is open")
    mock_service.try_recovery_transition.return_value = None
    mock_service.get_failure_count.return_value = 0
    mock_service.get_all_states.return_value = {}

    with patch(
        "selfhealing.services.circuit_breaker_service.get_circuit_breaker_service",
        return_value=mock_service,
    ):
        yield mock_service


@pytest.fixture
def mock_dlq_service():
    """Mock DLQ service for testing."""
    mock_service = MagicMock()

    # Mock store_failure result
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.dlq_id = 123
    mock_result.error = None
    mock_service.store_failure.return_value = mock_result

    # Mock get_stats result
    mock_service.get_stats.return_value = {
        "total": 100,
        "by_status": {"pending": 80, "resolved": 20},
        "by_domain": {"external_service": 50, "internal_process": 50},
    }

    # Mock get_entry result
    mock_service.get_entry.return_value = {
        "id": 123,
        "status": "pending",
        "domain": "test_service",
        "failure_type": "CIRCUIT_OPEN",
    }

    with patch(
        "selfhealing.services.dlq.get_dlq_service",
        return_value=mock_service,
    ):
        yield mock_service


@pytest.fixture
def mock_snapshot():
    """Mock system snapshot collection."""
    with patch(
        "selfhealing.api.django.views.xtest.integration.collect_system_snapshot",
        return_value={"timestamp": "2026-01-26T12:00:00Z", "cpu_percent": 10.0, "metrics_source": "cache"},
    ):
        with patch(
            "selfhealing.api.django.views.xtest.base.collect_system_snapshot",
            return_value={"timestamp": "2026-01-26T12:00:00Z", "cpu_percent": 10.0, "metrics_source": "cache"},
        ):
            yield


@pytest.fixture(autouse=True)
def cleanup_scenarios():
    """Clean up scenario results after each test."""
    yield
    clear_scenario_results()


# =============================================================================
# RunScenarioView Tests
# =============================================================================


class TestRunScenarioView:
    """Tests for RunScenarioView - 시나리오 실행 API."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert RunScenarioView is not None
        assert hasattr(RunScenarioView, "post")

    def test_view_has_no_authentication(self):
        """View가 인증 없이 접근 가능한지 확인 (헤더 기반 보안)."""
        assert RunScenarioView.authentication_classes == []

    def test_run_scenario_missing_scenario_name(self, request_factory, mock_chaos_allowed):
        """시나리오 이름 없이 요청 시 에러 반환."""
        view = RunScenarioView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/integration/run-scenario/",
            {"service_name": "test_service"},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_field"
        assert "available_scenarios" in response.data

    def test_run_scenario_missing_service_name(self, request_factory, mock_chaos_allowed):
        """서비스 이름 없이 요청 시 에러 반환."""
        view = RunScenarioView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/integration/run-scenario/",
            {"scenario": "cb_open_dlq_flow"},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_field"

    def test_run_scenario_unknown_scenario(self, request_factory, mock_chaos_allowed):
        """알 수 없는 시나리오 요청 시 에러 반환."""
        view = RunScenarioView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/integration/run-scenario/",
            {
                "scenario": "unknown_scenario",
                "service_name": "test_service",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "unknown_scenario"
        assert "available_scenarios" in response.data

    def test_run_cb_open_dlq_scenario_success(
        self, request_factory, mock_chaos_allowed, mock_cb_service, mock_dlq_service, mock_snapshot
    ):
        """CB Open → DLQ 시나리오 실행 성공 테스트."""
        view = RunScenarioView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/integration/run-scenario/",
            {
                "scenario": "cb_open_dlq_flow",
                "service_name": "test_service",
                "config": {"failure_count": 5},
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] in ("success", "completed")  # merged result
        assert response.data["scenario"] == "cb_open_dlq_flow"
        assert "scenario_id" in response.data
        assert "steps" in response.data
        assert "timeline" in response.data

    def test_run_retry_exhaust_scenario(self, request_factory, mock_chaos_allowed, mock_dlq_service, mock_snapshot):
        """Retry 소진 → DLQ 시나리오 실행 테스트."""
        view = RunScenarioView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/integration/run-scenario/",
            {
                "scenario": "retry_exhaust_dlq",
                "service_name": "test_service",
                "config": {"max_attempts": 3},
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["scenario"] == "retry_exhaust_dlq"
        assert len(response.data["steps"]) == 6

    def test_run_rate_limit_retry_scenario(self, request_factory, mock_chaos_allowed, mock_snapshot):
        """Rate Limit → Retry 시나리오 실행 테스트."""
        view = RunScenarioView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/integration/run-scenario/",
            {
                "scenario": "rate_limit_retry",
                "service_name": "test_service",
                "config": {"limit": 5},
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["scenario"] == "rate_limit_retry"

    def test_run_full_recovery_scenario(
        self, request_factory, mock_chaos_allowed, mock_cb_service, mock_dlq_service, mock_snapshot
    ):
        """전체 복구 사이클 시나리오 실행 테스트."""
        # Mock error budget service
        with patch("selfhealing.services.error_budget.get_error_budget_service") as mock_eb:
            mock_eb_service = MagicMock()
            mock_eb_status = MagicMock()
            mock_eb_status.remaining_percent = 50.0
            mock_eb_service.get_status.return_value = mock_eb_status
            mock_eb.return_value = mock_eb_service

            view = RunScenarioView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/integration/run-scenario/",
                {
                    "scenario": "full_recovery_cycle",
                    "service_name": "test_service",
                    "config": {"failure_count": 10},
                },
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["scenario"] == "full_recovery_cycle"
            assert len(response.data["steps"]) == 11

    def test_chaos_header_required(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부 테스트."""
        view = RunScenarioView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/integration/run-scenario/",
            {
                "scenario": "cb_open_dlq_flow",
                "service_name": "test_service",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# ScenarioStatusView Tests
# =============================================================================


class TestScenarioStatusView:
    """Tests for ScenarioStatusView - 시나리오 상태 조회 API."""

    def test_view_exists_and_has_get_method(self):
        """View 클래스가 존재하고 get 메서드가 있는지 확인."""
        assert ScenarioStatusView is not None
        assert hasattr(ScenarioStatusView, "get")

    def test_get_scenario_not_found(self, request_factory, mock_chaos_allowed):
        """존재하지 않는 시나리오 조회 시 404 반환."""
        view = ScenarioStatusView.as_view()
        request = request_factory.get(
            "/api/self-healing/xtest/integration/scenario/nonexistent-id/",
        )

        response = view(request, scenario_id="nonexistent-id")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "scenario_not_found"

    def test_get_scenario_success(self, request_factory, mock_chaos_allowed):
        """저장된 시나리오 조회 성공 테스트."""
        # 시나리오 결과 저장
        result = ScenarioResult(
            scenario_id="test-scenario-123",
            scenario="cb_open_dlq_flow",
            service_name="test_service",
            status=ScenarioStatus.COMPLETED,
            started_at="2026-01-26T10:00:00+09:00",
            completed_at="2026-01-26T10:01:00+09:00",
        )
        store_scenario_result(result)

        view = ScenarioStatusView.as_view()
        request = request_factory.get(
            "/api/self-healing/xtest/integration/scenario/test-scenario-123/",
        )

        response = view(request, scenario_id="test-scenario-123")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["scenario_id"] == "test-scenario-123"
        assert response.data["scenario"] == "cb_open_dlq_flow"
        assert response.data["status"] == "completed"


# =============================================================================
# FullSnapshotView Tests
# =============================================================================


class TestFullSnapshotView:
    """Tests for FullSnapshotView - 전체 시스템 스냅샷 API."""

    def test_view_exists_and_has_get_method(self):
        """View 클래스가 존재하고 get 메서드가 있는지 확인."""
        assert FullSnapshotView is not None
        assert hasattr(FullSnapshotView, "get")

    def test_full_snapshot_success(
        self, request_factory, mock_chaos_allowed, mock_cb_service, mock_dlq_service, mock_snapshot
    ):
        """전체 스냅샷 조회 성공 테스트."""
        # Mock additional services
        with patch("selfhealing.services.error_budget.get_error_budget_service") as mock_eb:
            mock_eb_service = MagicMock()
            mock_eb_status = MagicMock()
            mock_eb_status.remaining_percent = 80.0
            mock_eb_status.consumed_percent = 20.0
            mock_eb_service.get_status.return_value = mock_eb_status
            mock_eb.return_value = mock_eb_service

            with patch("selfhealing.api.django.rate_limit.get_redis_health_checker") as mock_health:
                mock_checker = MagicMock()
                mock_checker.state = MagicMock(value="healthy")
                mock_health.return_value = mock_checker

                with patch(
                    "selfhealing.api.django.rate_limit.get_rate_limit_config",
                    return_value={"control_api_rate_limit": 100},
                ):
                    with patch("selfhealing.api.django.rate_limit.RedisHealthState") as mock_state:
                        mock_state.HEALTHY = mock_checker.state

                        view = FullSnapshotView.as_view()
                        request = request_factory.get(
                            "/api/self-healing/xtest/integration/full-snapshot/",
                        )

                        response = view(request)

                        assert response.status_code == status.HTTP_200_OK
                        assert "circuit_breakers" in response.data
                        assert "dlq" in response.data
                        assert "timestamp" in response.data

    def test_full_snapshot_with_service_filter(
        self, request_factory, mock_chaos_allowed, mock_cb_service, mock_dlq_service, mock_snapshot
    ):
        """서비스 필터 적용 스냅샷 테스트."""
        with patch("selfhealing.services.error_budget.get_error_budget_service") as mock_eb:
            mock_eb.return_value = MagicMock()

            with patch("selfhealing.api.django.rate_limit.get_redis_health_checker") as mock_health:
                mock_checker = MagicMock()
                mock_checker.state = MagicMock(value="healthy")
                mock_health.return_value = mock_checker

                with patch(
                    "selfhealing.api.django.rate_limit.get_rate_limit_config",
                    return_value={},
                ):
                    with patch("selfhealing.api.django.rate_limit.RedisHealthState") as mock_state:
                        mock_state.HEALTHY = mock_checker.state

                        view = FullSnapshotView.as_view()
                        request = request_factory.get(
                            "/api/self-healing/xtest/integration/full-snapshot/",
                            {"service_name": "test_service"},
                        )

                        response = view(request)

                        assert response.status_code == status.HTTP_200_OK
                        assert response.data["service_filter"] == "test_service"


# =============================================================================
# ResetView Tests
# =============================================================================


class TestResetView:
    """Tests for ResetView - 시스템 초기화 API."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert ResetView is not None
        assert hasattr(ResetView, "post")

    def test_reset_invalid_component(self, request_factory, mock_chaos_allowed):
        """유효하지 않은 컴포넌트 지정 시 에러 반환."""
        view = ResetView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/integration/reset/",
            {"components": ["invalid_component"]},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "invalid_components"

    def test_reset_all_components(self, request_factory, mock_chaos_allowed, mock_cb_service, mock_dlq_service):
        """모든 컴포넌트 초기화 테스트."""
        with patch("selfhealing.api.django.rate_limit.get_local_limiter") as mock_limiter:
            mock_limiter.return_value = MagicMock()

            view = ResetView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/integration/reset/",
                {"components": ["all"]},
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert "reset_results" in response.data
            assert "circuit_breakers" in response.data["reset_results"]

    def test_reset_specific_components(self, request_factory, mock_chaos_allowed, mock_cb_service):
        """특정 컴포넌트만 초기화 테스트."""
        view = ResetView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/integration/reset/",
            {
                "components": ["circuit_breakers", "scenarios"],
                "service_name": "test_service",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert "circuit_breakers" in response.data["reset_results"]
        assert "scenarios" in response.data["reset_results"]

    def test_reset_xtest_only_flag(self, request_factory, mock_chaos_allowed, mock_cb_service, mock_dlq_service):
        """xtest_only 플래그 테스트."""
        with patch("selfhealing.api.django.rate_limit.get_local_limiter") as mock_limiter:
            mock_limiter.return_value = MagicMock()

            view = ResetView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/integration/reset/",
                {
                    "components": ["all"],
                    "xtest_only": True,
                },
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["xtest_only"] is True


# =============================================================================
# Scenario Registry Tests
# =============================================================================


class TestScenarioRegistry:
    """Tests for scenario registry and utilities."""

    def test_all_scenarios_registered(self):
        """모든 시나리오가 레지스트리에 등록되어 있는지 확인."""
        expected_scenarios = [
            "cb_open_dlq_flow",
            "retry_exhaust_dlq",
            "rate_limit_retry",
            "dlq_replay_success",
            "dlq_replay_failure",
            "full_recovery_cycle",
            "idempotent_replay",
        ]

        for scenario_name in expected_scenarios:
            assert scenario_name in SCENARIO_REGISTRY
            assert get_scenario_class(scenario_name) is not None

    def test_list_available_scenarios(self):
        """사용 가능한 시나리오 목록 조회."""
        scenarios = list_available_scenarios()

        assert len(scenarios) == 7
        assert "cb_open_dlq_flow" in scenarios
        assert "full_recovery_cycle" in scenarios

    def test_get_unknown_scenario(self):
        """알 수 없는 시나리오 조회 시 None 반환."""
        result = get_scenario_class("unknown_scenario")

        assert result is None

    def test_scenario_result_storage(self):
        """시나리오 결과 저장 및 조회."""
        result = ScenarioResult(
            scenario_id="test-id-456",
            scenario="cb_open_dlq_flow",
            service_name="test_service",
            status=ScenarioStatus.COMPLETED,
            started_at="2026-01-26T10:00:00+09:00",
        )

        store_scenario_result(result)
        retrieved = get_scenario_result("test-id-456")

        assert retrieved is not None
        assert retrieved.scenario_id == "test-id-456"
        assert retrieved.status == ScenarioStatus.COMPLETED

    def test_clear_scenario_results(self):
        """시나리오 결과 전체 삭제."""
        result = ScenarioResult(
            scenario_id="test-id-789",
            scenario="cb_open_dlq_flow",
            service_name="test_service",
            status=ScenarioStatus.COMPLETED,
            started_at="2026-01-26T10:00:00+09:00",
        )
        store_scenario_result(result)

        cleared_count = clear_scenario_results()

        assert cleared_count >= 1
        assert get_scenario_result("test-id-789") is None


# =============================================================================
# Scenario Execution Tests
# =============================================================================


class TestScenarioExecution:
    """Tests for individual scenario execution."""

    def test_scenario_has_correct_steps_count(self, mock_cb_service, mock_dlq_service, mock_snapshot):
        """CB Open DLQ 시나리오가 6단계인지 확인."""
        from selfhealing.api.django.views.xtest.scenarios import (
            CBOpenDLQScenario,
        )

        scenario = CBOpenDLQScenario(
            service_name="test_service",
            config={"failure_count": 5},
        )
        result = scenario.run()

        assert len(result.steps) == 6
        assert result.scenario == "cb_open_dlq_flow"

    def test_scenario_timeline_generated(self, mock_cb_service, mock_dlq_service, mock_snapshot):
        """시나리오 실행 시 타임라인이 생성되는지 확인."""
        from selfhealing.api.django.views.xtest.scenarios import (
            CBOpenDLQScenario,
        )

        scenario = CBOpenDLQScenario(
            service_name="test_service",
            config={"failure_count": 5},
        )
        result = scenario.run()

        assert len(result.timeline) == len(result.steps)
        for event in result.timeline:
            assert "timestamp" in event.__dict__
            assert "action" in event.__dict__

    def test_scenario_snapshot_collected(self, mock_cb_service, mock_dlq_service, mock_snapshot):
        """시나리오 완료 후 스냅샷이 수집되는지 확인."""
        from selfhealing.api.django.views.xtest.scenarios import (
            CBOpenDLQScenario,
        )

        scenario = CBOpenDLQScenario(
            service_name="test_service",
            config={"failure_count": 5},
        )
        result = scenario.run()

        assert result.snapshot is not None
        assert "timestamp" in result.snapshot

    def test_scenario_to_dict_serialization(self):
        """ScenarioResult.to_dict() 직렬화 테스트."""
        result = ScenarioResult(
            scenario_id="test-id",
            scenario="cb_open_dlq_flow",
            service_name="test_service",
            status=ScenarioStatus.COMPLETED,
            started_at="2026-01-26T10:00:00+09:00",
            completed_at="2026-01-26T10:01:00+09:00",
        )

        data = result.to_dict()

        assert data["scenario_id"] == "test-id"
        assert data["status"] == "completed"
        assert isinstance(data["steps"], list)
        assert isinstance(data["timeline"], list)
