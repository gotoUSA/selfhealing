"""
Unit Tests for X-Test-Mode Replay Views.

X-Test-Mode 환경에서 DLQ Replay 동작을 테스트하기 위한 API 테스트.

Tests:
- ReplaySingleView: 단일 DLQ 항목 재생
- ReplayBatchView: 다수 항목 배치 재생
- TriggerReplayOnCBCloseView: CB 복구 시 자동 재생 트리거
- ReplayStatusView: 재생 상태 조회
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIRequestFactory
from rest_framework import status

from selfhealing.api.django.views.xtest.replay import (
    ReplaySingleView,
    ReplayBatchView,
    TriggerReplayOnCBCloseView,
    ReplayStatusView,
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
        "selfhealing.api.django.views.xtest.replay.XTestModeMixin.is_chaos_allowed",
        return_value=(True, "Chaos mode allowed"),
    ):
        yield


@pytest.fixture
def mock_replay_service():
    """Mock ReplayService for testing."""
    mock_service = MagicMock()

    # Mock repository
    mock_repo = MagicMock()
    mock_service.repository = mock_repo
    mock_service.config = {"max_replay_attempts": 5}

    # Mock get_by_id result
    mock_entry = MagicMock()
    mock_entry.id = 123
    mock_entry.status = "pending"
    mock_entry.domain = "external_service"
    mock_entry.failure_type = "TIMEOUT"
    mock_entry.retry_count = 1
    mock_repo.get_by_id.return_value = mock_entry

    # Mock replay_single result
    mock_replay_result = MagicMock()
    mock_replay_result.success = True
    mock_replay_result.dlq_id = 123
    mock_replay_result.message = "Replay completed successfully"
    mock_replay_result.error = None
    mock_replay_result.data = None
    mock_service.replay_single.return_value = mock_replay_result

    # Mock replay_batch result
    mock_batch_result = MagicMock()
    mock_batch_result.total = 5
    mock_batch_result.success_count = 4
    mock_batch_result.failed_count = 1
    mock_batch_result.skipped_count = 0
    mock_batch_result.governance_blocked = False
    mock_batch_result.governance_block_reason = ""
    mock_batch_result.results = []
    mock_service.replay_batch.return_value = mock_batch_result

    # Mock replay_on_circuit_close result
    mock_service.replay_on_circuit_close.return_value = mock_batch_result

    # Mock get_pending_entries
    mock_repo.get_pending_entries.return_value = [mock_entry]

    with patch(
        "selfhealing.services.replay_service.get_replay_service",
        return_value=mock_service,
    ):
        yield mock_service


@pytest.fixture
def mock_governance_checks():
    """Mock governance checks to allow all operations."""
    mock_result = MagicMock()
    mock_result.allowed = True
    mock_result.block_reason = None
    mock_result.block_message = ""

    with patch(
        "selfhealing.services.governance.checks.check_all_governance",
        return_value=mock_result,
    ):
        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            return_value=True,
        ):
            with patch(
                "selfhealing.services.governance.checks.is_emergency_blocking",
                return_value=(False, "NORMAL"),
            ):
                with patch(
                    "selfhealing.services.governance.checks.is_error_budget_blocking",
                    return_value=(False, 100.0, 20.0),
                ):
                    yield mock_result


@pytest.fixture
def mock_dlq_service():
    """Mock DLQ service for status view."""
    mock_service = MagicMock()
    mock_service.get_stats.return_value = {
        "total": 100,
        "by_status": {"pending": 80, "resolved": 20},
        "by_domain": {"external_service": 50, "internal_process": 30},
    }

    with patch(
        "selfhealing.services.dlq.get_dlq_service",
        return_value=mock_service,
    ):
        yield mock_service


@pytest.fixture
def mock_circuit_breaker():
    """Mock circuit breaker service for CB-related tests."""
    mock_cb_service = MagicMock()
    mock_cb_service.get_status.return_value = {"state": "CLOSED"}
    mock_cb_service.get_all_status.return_value = {
        "database": {"state": "CLOSED"},
        "external_api": {"state": "CLOSED"},
    }

    with patch(
        "selfhealing.services.circuit_breaker.get_circuit_breaker_service",
        return_value=mock_cb_service,
    ):
        yield mock_cb_service


# =============================================================================
# ReplaySingleView 테스트
# =============================================================================


class TestReplaySingleView:
    """Tests for ReplaySingleView - 단일 DLQ 항목 재생."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert ReplaySingleView is not None
        assert hasattr(ReplaySingleView, "post")

    def test_view_has_no_authentication(self):
        """View가 인증 없이 접근 가능한지 확인 (헤더 기반 보안)."""
        assert ReplaySingleView.authentication_classes == []

    def test_replay_single_success(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
        mock_governance_checks,
    ):
        """단일 항목 재생 성공 테스트."""
        view = ReplaySingleView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/single/",
            {"dlq_id": 123},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert response.data["dlq_id"] == 123
        assert "governance_result" in response.data
        assert "replay_duration_ms" in response.data
        assert "snapshot" in response.data

    def test_replay_single_missing_dlq_id(
        self,
        request_factory,
        mock_chaos_allowed,
    ):
        """dlq_id 누락 시 에러 테스트."""
        view = ReplaySingleView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/single/",
            {},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_field"

    def test_replay_single_invalid_dlq_id(
        self,
        request_factory,
        mock_chaos_allowed,
    ):
        """잘못된 dlq_id 형식 테스트."""
        view = ReplaySingleView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/single/",
            {"dlq_id": "not_a_number"},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "invalid_dlq_id"

    def test_replay_single_dry_run(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
        mock_governance_checks,
    ):
        """dry_run 모드 테스트 - 실제 재생 없이 검증만 수행."""
        view = ReplaySingleView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/single/",
            {"dlq_id": 123, "dry_run": True},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "dry_run"
        assert "validation" in response.data
        # dry_run에서는 replay_single이 호출되지 않아야 함
        mock_replay_service.replay_single.assert_not_called()

    def test_replay_single_governance_blocked(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
    ):
        """거버넌스 차단 시 테스트."""
        mock_gov_result = MagicMock()
        mock_gov_result.allowed = False
        mock_gov_result.block_reason = MagicMock()
        mock_gov_result.block_reason.value = "kill_switch"
        mock_gov_result.block_message = "Kill Switch is active"

        with patch(
            "selfhealing.services.governance.checks.check_all_governance",
            return_value=mock_gov_result,
        ):
            view = ReplaySingleView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/replay/single/",
                {"dlq_id": 123},
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["status"] == "blocked"
            assert response.data["success"] is False
            assert response.data["governance_result"]["allowed"] is False

    def test_replay_single_skip_governance(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
    ):
        """skip_governance 옵션 테스트."""
        view = ReplaySingleView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/single/",
            {"dlq_id": 123, "skip_governance": True},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["governance_result"]["skipped"] is True


# =============================================================================
# ReplayBatchView 테스트
# =============================================================================


class TestReplayBatchView:
    """Tests for ReplayBatchView - 배치 재생."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert ReplayBatchView is not None
        assert hasattr(ReplayBatchView, "post")

    def test_view_has_no_authentication(self):
        """View가 인증 없이 접근 가능한지 확인."""
        assert ReplayBatchView.authentication_classes == []

    def test_replay_batch_success(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
        mock_governance_checks,
    ):
        """배치 재생 성공 테스트."""
        view = ReplayBatchView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/batch/",
            {"domain": "external_service", "batch_size": 10},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "total" in response.data
        assert "success_count" in response.data
        assert "failed_count" in response.data
        assert "snapshot" in response.data

    def test_replay_batch_size_exceeded(
        self,
        request_factory,
        mock_chaos_allowed,
    ):
        """최대 배치 크기 초과 테스트."""
        view = ReplayBatchView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/batch/",
            {"batch_size": 100},  # Max is 50
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "batch_size_exceeded"

    def test_replay_batch_dry_run(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
        mock_governance_checks,
    ):
        """배치 dry_run 모드 테스트."""
        view = ReplayBatchView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/batch/",
            {"domain": "external_service", "dry_run": True},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "dry_run"
        assert "eligible_entries" in response.data
        assert "governance_status" in response.data
        # dry_run에서는 replay_batch가 호출되지 않아야 함
        mock_replay_service.replay_batch.assert_not_called()

    def test_replay_batch_with_domain_filter(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
        mock_governance_checks,
    ):
        """도메인 필터 적용 테스트."""
        view = ReplayBatchView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/batch/",
            {"domain": "external_service", "batch_size": 5},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        mock_replay_service.replay_batch.assert_called_once()
        call_args = mock_replay_service.replay_batch.call_args
        assert call_args.kwargs.get("domain") == "external_service"


# =============================================================================
# TriggerReplayOnCBCloseView 테스트
# =============================================================================


class TestTriggerReplayOnCBCloseView:
    """Tests for TriggerReplayOnCBCloseView - CB 복구 시 자동 재생 트리거."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert TriggerReplayOnCBCloseView is not None
        assert hasattr(TriggerReplayOnCBCloseView, "post")

    def test_view_has_no_authentication(self):
        """View가 인증 없이 접근 가능한지 확인."""
        assert TriggerReplayOnCBCloseView.authentication_classes == []

    def test_trigger_replay_on_cb_close_success(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
        mock_circuit_breaker,
    ):
        """CB 복구 시 재생 트리거 성공 테스트."""
        view = TriggerReplayOnCBCloseView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/trigger-on-cb-close/",
            {"service_name": "database"},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "triggered" in response.data
        assert "eligible_count" in response.data
        assert "replayed_count" in response.data
        assert "cb_previous_state" in response.data
        assert "cb_current_state" in response.data

    def test_trigger_replay_missing_service_name(
        self,
        request_factory,
        mock_chaos_allowed,
    ):
        """service_name 누락 시 에러 테스트."""
        view = TriggerReplayOnCBCloseView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/trigger-on-cb-close/",
            {},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_field"

    def test_trigger_replay_simulate_close_false(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
        mock_circuit_breaker,
    ):
        """simulate_close=false 테스트 - CB 상태 변경 없이 재생만."""
        view = TriggerReplayOnCBCloseView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/trigger-on-cb-close/",
            {"service_name": "database", "simulate_close": False},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        # force_close가 호출되지 않아야 함
        mock_circuit_breaker.force_close.assert_not_called()


# =============================================================================
# ReplayStatusView 테스트
# =============================================================================


class TestReplayStatusView:
    """Tests for ReplayStatusView - 재생 상태 조회."""

    def test_view_exists_and_has_get_method(self):
        """View 클래스가 존재하고 get 메서드가 있는지 확인."""
        assert ReplayStatusView is not None
        assert hasattr(ReplayStatusView, "get")

    def test_view_has_no_authentication(self):
        """View가 인증 없이 접근 가능한지 확인."""
        assert ReplayStatusView.authentication_classes == []

    def test_replay_status_success(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_dlq_service,
        mock_governance_checks,
        mock_circuit_breaker,
    ):
        """재생 상태 조회 성공 테스트."""
        view = ReplayStatusView.as_view()
        request = request_factory.get(
            "/api/self-healing/xtest/replay/status/",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "pending_count" in response.data
        assert "by_domain" in response.data
        assert "governance_status" in response.data
        assert "cb_states" in response.data
        assert "snapshot" in response.data

    def test_replay_status_with_domain_filter(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_dlq_service,
        mock_governance_checks,
        mock_circuit_breaker,
    ):
        """도메인 필터 적용 상태 조회 테스트."""
        view = ReplayStatusView.as_view()
        request = request_factory.get(
            "/api/self-healing/xtest/replay/status/",
            {"domain": "external_service"},
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        # 도메인 필터가 적용되었는지 확인
        assert "by_domain" in response.data


# =============================================================================
# 통합 시나리오 테스트
# =============================================================================


class TestReplayIntegrationScenarios:
    """통합 시나리오 테스트 - DLQ → Replay 사이클."""

    def test_dlq_to_replay_cycle(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
        mock_governance_checks,
        mock_dlq_service,
    ):
        """DLQ 생성 후 Replay 시나리오 테스트."""
        # Step 1: 상태 조회 (pending 확인)
        status_view = ReplayStatusView.as_view()
        status_request = request_factory.get("/api/self-healing/xtest/replay/status/")
        status_response = status_view(status_request)

        assert status_response.status_code == status.HTTP_200_OK
        assert "pending_count" in status_response.data

        # Step 2: 단일 재생 실행
        single_view = ReplaySingleView.as_view()
        single_request = request_factory.post(
            "/api/self-healing/xtest/replay/single/",
            {"dlq_id": 123},
            format="json",
        )
        single_response = single_view(single_request)

        assert single_response.status_code == status.HTTP_200_OK
        assert single_response.data["success"] is True

        # Step 3: 배치 재생 실행
        batch_view = ReplayBatchView.as_view()
        batch_request = request_factory.post(
            "/api/self-healing/xtest/replay/batch/",
            {"domain": "external_service", "batch_size": 10},
            format="json",
        )
        batch_response = batch_view(batch_request)

        assert batch_response.status_code == status.HTTP_200_OK
        assert "success_count" in batch_response.data

    def test_governance_check_flow(
        self,
        request_factory,
        mock_chaos_allowed,
        mock_replay_service,
    ):
        """거버넌스 체크 흐름 테스트."""
        # 거버넌스 차단 상태 시뮬레이션
        mock_gov_result = MagicMock()
        mock_gov_result.allowed = False
        mock_gov_result.block_reason = MagicMock()
        mock_gov_result.block_reason.value = "error_budget"
        mock_gov_result.block_message = "Error budget exhausted"

        with patch(
            "selfhealing.services.governance.checks.check_all_governance",
            return_value=mock_gov_result,
        ):
            view = ReplaySingleView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/replay/single/",
                {"dlq_id": 123},
                format="json",
            )

            response = view(request)

            # 거버넌스 체크로 차단됨
            assert response.data["status"] == "blocked"
            assert response.data["governance_result"]["allowed"] is False
            assert "error_budget" in response.data["governance_result"]["checks_failed"]

        # skip_governance로 우회
        view = ReplaySingleView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/replay/single/",
            {"dlq_id": 123, "skip_governance": True},
            format="json",
        )

        response = view(request)

        # 거버넌스 스킵됨
        assert response.data["governance_result"]["skipped"] is True
