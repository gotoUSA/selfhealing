"""
Canary Rollout API Tests.

설정 변경의 점진적 배포(Canary Rollout) API 테스트.

Endpoints:
    - GET  /api/self-healing/canary/rollouts/              - 활성 롤아웃 목록 조회
    - POST /api/self-healing/canary/rollouts/              - 새 롤아웃 생성
    - GET  /api/self-healing/canary/rollouts/{id}/         - 롤아웃 상세 조회
    - POST /api/self-healing/canary/rollouts/{id}/start/   - 롤아웃 시작
    - POST /api/self-healing/canary/rollouts/{id}/promote/ - 다음 단계로 프로모션
    - POST /api/self-healing/canary/rollouts/{id}/rollback/ - 롤백
    - POST /api/self-healing/canary/rollouts/{id}/pause/   - 일시 중지
    - POST /api/self-healing/canary/rollouts/{id}/resume/  - 재개
    - POST /api/self-healing/canary/panic-rollback/        - 긴급 롤백

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
"""

import pytest
from unittest.mock import Mock, patch
from selfhealing.utils.time import utc_now

from rest_framework.test import APIRequestFactory
from rest_framework import status as http_status

# Import 전에 환경변수 설정하지 않음 - fixture에서 patch로 처리
from selfhealing.api.django.views.canary import (
    CanaryRolloutListView,
    CanaryRolloutDetailView,
    CanaryRolloutActionView,
    CanaryPanicRollbackView,
    CanaryMetricsView,
    CanaryHistoryView,
)
from selfhealing.services.canary import (
    CanaryRollout,
    CanaryStage,
    CanaryState,
    CanaryMetrics,
    ConfigLockError,
    reset_canary_rollout_service,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def api_factory():
    """API request factory."""
    return APIRequestFactory()


@pytest.fixture
def admin_user():
    """Admin user mock."""
    user = Mock()
    user.is_authenticated = True
    user.username = "admin"
    user.email = "admin@example.com"
    user.is_staff = True
    user.is_superuser = True
    return user


@pytest.fixture
def viewer_user():
    """Viewer user mock."""
    user = Mock()
    user.is_authenticated = True
    user.username = "viewer"
    user.is_staff = True
    user.is_superuser = False
    return user


@pytest.fixture(autouse=True)
def disable_selfhealing_auth_for_canary_tests(monkeypatch):
    """Canary 테스트에서 인증 바이패스 활성화 (테스트 범위 내에서만)."""
    monkeypatch.setenv("DISABLE_SELFHEALING_AUTH", "true")
    yield
    # monkeypatch는 자동으로 원래 값으로 복원됨


@pytest.fixture(autouse=True)
def reset_service():
    """각 테스트 전 서비스 리셋."""
    reset_canary_rollout_service()
    yield
    reset_canary_rollout_service()


@pytest.fixture
def sample_rollout():
    """샘플 롤아웃 객체."""
    return CanaryRollout(
        id="test1234",
        config_type="circuit_breaker",
        previous_values={"failure_threshold": 5},
        new_values={"failure_threshold": 3},
        state=CanaryState.CREATED,
        current_stage_index=0,
        stages=[
            CanaryStage(
                name="canary",
                clusters=["seoul-canary"],
                percentage=10.0,
                duration_minutes=5,
            ),
            CanaryStage(
                name="full",
                clusters=["seoul", "tokyo"],
                percentage=100.0,
                duration_minutes=5,
            ),
        ],
        created_by="admin@example.com",
        created_at=utc_now(),
        reason="Reduce failure threshold",
    )


@pytest.fixture
def active_rollout(sample_rollout):
    """활성 상태(CANARY) 롤아웃."""
    sample_rollout.state = CanaryState.CANARY
    return sample_rollout


@pytest.fixture
def completed_rollout(sample_rollout):
    """완료된 롤아웃."""
    sample_rollout.state = CanaryState.COMPLETED
    sample_rollout.completed_at = utc_now()
    return sample_rollout


# =============================================================================
# CanaryRolloutListView Tests
# =============================================================================


class TestCanaryRolloutListView:
    """CanaryRolloutListView 테스트."""

    def test_get_empty_rollouts(self, api_factory, admin_user):
        """활성 롤아웃이 없을 때 빈 목록 반환."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_rollouts.return_value = []
            mock_service.get_completed_rollouts.return_value = []
            mock_get_service.return_value = mock_service

            request = api_factory.get("/api/self-healing/canary/rollouts/")
            request.user = admin_user

            view = CanaryRolloutListView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_200_OK
            assert response.data["status"] == "success"
            assert response.data["count"] == 0
            assert response.data["rollouts"] == []

    def test_get_active_rollouts(self, api_factory, admin_user, sample_rollout):
        """활성 롤아웃 목록 조회."""
        sample_rollout.state = CanaryState.CANARY

        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_rollouts.return_value = [sample_rollout]
            mock_get_service.return_value = mock_service

            request = api_factory.get("/api/self-healing/canary/rollouts/")
            request.user = admin_user

            view = CanaryRolloutListView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_200_OK
            assert response.data["count"] == 1
            assert response.data["rollouts"][0]["id"] == "test1234"
            assert response.data["rollouts"][0]["config_type"] == "circuit_breaker"

    def test_get_with_include_completed(self, api_factory, admin_user, sample_rollout, completed_rollout):
        """include_completed 파라미터로 완료된 롤아웃 포함."""
        sample_rollout.state = CanaryState.CANARY

        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_rollouts.return_value = [sample_rollout]
            mock_service.get_completed_rollouts.return_value = [completed_rollout]
            mock_get_service.return_value = mock_service

            request = api_factory.get(
                "/api/self-healing/canary/rollouts/",
                {"include_completed": "true"},
            )
            request.user = admin_user

            view = CanaryRolloutListView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_200_OK
            # 활성 1개 + 완료 1개 = 2개 (같은 ID라도 상태가 다름)
            assert response.data["count"] >= 1

    def test_create_rollout_success(self, api_factory, admin_user, sample_rollout):
        """롤아웃 생성 성공."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.create_rollout.return_value = sample_rollout
            mock_get_service.return_value = mock_service

            request = api_factory.post(
                "/api/self-healing/canary/rollouts/",
                {
                    "config_type": "circuit_breaker",
                    "new_values": {"failure_threshold": 3},
                    "stages": [
                        {
                            "name": "canary",
                            "clusters": ["seoul-canary"],
                            "percentage": 10,
                        }
                    ],
                    "reason": "Test rollout",
                },
                format="json",
            )
            request.user = admin_user

            view = CanaryRolloutListView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_201_CREATED
            assert response.data["status"] == "success"
            assert "rollout" in response.data

    def test_create_rollout_missing_config_type(self, api_factory, admin_user):
        """config_type 누락 시 400 에러."""
        request = api_factory.post(
            "/api/self-healing/canary/rollouts/",
            {
                "new_values": {"failure_threshold": 3},
                "stages": [{"name": "canary", "clusters": ["seoul"]}],
            },
            format="json",
        )
        request.user = admin_user

        view = CanaryRolloutListView.as_view()
        response = view(request)

        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        assert "config_type" in response.data["error"]

    def test_create_rollout_empty_stages(self, api_factory, admin_user):
        """stages 누락 시 400 에러."""
        request = api_factory.post(
            "/api/self-healing/canary/rollouts/",
            {
                "config_type": "circuit_breaker",
                "new_values": {"failure_threshold": 3},
                "stages": [],
            },
            format="json",
        )
        request.user = admin_user

        view = CanaryRolloutListView.as_view()
        response = view(request)

        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        assert "stage" in response.data["error"].lower()

    def test_create_rollout_config_locked(self, api_factory, admin_user):
        """이미 롤아웃 진행 중일 때 409 에러."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.create_rollout.side_effect = ConfigLockError(
                "Already in rollout",
                config_type="circuit_breaker",
                current_owner="existing-rollout",
            )
            mock_get_service.return_value = mock_service

            request = api_factory.post(
                "/api/self-healing/canary/rollouts/",
                {
                    "config_type": "circuit_breaker",
                    "new_values": {"failure_threshold": 3},
                    "stages": [{"name": "canary", "clusters": ["seoul"]}],
                },
                format="json",
            )
            request.user = admin_user

            view = CanaryRolloutListView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_409_CONFLICT
            assert response.data["error_type"] == "config_locked"


# =============================================================================
# CanaryRolloutDetailView Tests
# =============================================================================


class TestCanaryRolloutDetailView:
    """CanaryRolloutDetailView 테스트."""

    def test_get_rollout_detail_success(self, api_factory, admin_user, sample_rollout):
        """롤아웃 상세 조회 성공."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = sample_rollout
            mock_get_service.return_value = mock_service

            request = api_factory.get("/api/self-healing/canary/rollouts/test1234/")
            request.user = admin_user

            view = CanaryRolloutDetailView.as_view()
            response = view(request, rollout_id="test1234")

            assert response.status_code == http_status.HTTP_200_OK
            assert response.data["status"] == "success"
            assert response.data["rollout"]["id"] == "test1234"
            assert response.data["rollout"]["config_type"] == "circuit_breaker"
            assert len(response.data["rollout"]["stages"]) == 2

    def test_get_rollout_not_found(self, api_factory, admin_user):
        """존재하지 않는 롤아웃 조회 시 404 에러."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = None
            mock_get_service.return_value = mock_service

            request = api_factory.get("/api/self-healing/canary/rollouts/notfound/")
            request.user = admin_user

            view = CanaryRolloutDetailView.as_view()
            response = view(request, rollout_id="notfound")

            assert response.status_code == http_status.HTTP_404_NOT_FOUND


# =============================================================================
# CanaryRolloutActionView Tests
# =============================================================================


class TestCanaryRolloutActionView:
    """CanaryRolloutActionView 테스트."""

    def test_start_rollout_success(self, api_factory, admin_user, sample_rollout):
        """롤아웃 시작 성공."""
        started_rollout = sample_rollout
        started_rollout.state = CanaryState.CANARY

        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = sample_rollout
            mock_service.start_rollout.return_value = True
            # 업데이트된 롤아웃 반환
            mock_service.get_rollout.side_effect = [sample_rollout, started_rollout]
            mock_get_service.return_value = mock_service

            request = api_factory.post("/api/self-healing/canary/rollouts/test1234/start/")
            request.user = admin_user

            view = CanaryRolloutActionView.as_view()
            response = view(request, rollout_id="test1234", action="start")

            assert response.status_code == http_status.HTTP_200_OK
            assert response.data["action"] == "start"
            mock_service.start_rollout.assert_called_once_with("test1234")

    def test_promote_rollout_success(self, api_factory, admin_user, active_rollout):
        """롤아웃 프로모션 성공."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = active_rollout
            mock_service.promote.return_value = True
            mock_get_service.return_value = mock_service

            request = api_factory.post(
                "/api/self-healing/canary/rollouts/test1234/promote/",
                {"force": False},
                format="json",
            )
            request.user = admin_user

            view = CanaryRolloutActionView.as_view()
            response = view(request, rollout_id="test1234", action="promote")

            assert response.status_code == http_status.HTTP_200_OK
            assert response.data["action"] == "promote"
            mock_service.promote.assert_called_once_with("test1234", force=False)

    def test_force_promote_rollout(self, api_factory, admin_user, active_rollout):
        """강제 프로모션 (메트릭 검증 무시)."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = active_rollout
            mock_service.promote.return_value = True
            mock_get_service.return_value = mock_service

            request = api_factory.post(
                "/api/self-healing/canary/rollouts/test1234/promote/",
                {"force": True},
                format="json",
            )
            request.user = admin_user

            view = CanaryRolloutActionView.as_view()
            response = view(request, rollout_id="test1234", action="promote")

            assert response.status_code == http_status.HTTP_200_OK
            mock_service.promote.assert_called_once_with("test1234", force=True)

    def test_rollback_success(self, api_factory, admin_user, active_rollout):
        """롤백 성공."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            rolled_back = active_rollout
            rolled_back.state = CanaryState.ROLLED_BACK
            mock_service.get_rollout.return_value = active_rollout
            mock_service.rollback.return_value = True
            mock_service.get_rollout.side_effect = [active_rollout, rolled_back]
            mock_get_service.return_value = mock_service

            request = api_factory.post(
                "/api/self-healing/canary/rollouts/test1234/rollback/",
                {"reason": "High error rate detected"},
                format="json",
            )
            request.user = admin_user

            view = CanaryRolloutActionView.as_view()
            response = view(request, rollout_id="test1234", action="rollback")

            assert response.status_code == http_status.HTTP_200_OK
            assert response.data["action"] == "rollback"

    def test_pause_success(self, api_factory, admin_user, active_rollout):
        """일시 중지 성공."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            paused_rollout = active_rollout
            paused_rollout.state = CanaryState.PAUSED
            mock_service.get_rollout.return_value = active_rollout
            mock_service.pause.return_value = True
            mock_service.get_rollout.side_effect = [active_rollout, paused_rollout]
            mock_get_service.return_value = mock_service

            request = api_factory.post("/api/self-healing/canary/rollouts/test1234/pause/")
            request.user = admin_user

            view = CanaryRolloutActionView.as_view()
            response = view(request, rollout_id="test1234", action="pause")

            assert response.status_code == http_status.HTTP_200_OK
            mock_service.pause.assert_called_once_with("test1234")

    def test_resume_success(self, api_factory, admin_user, sample_rollout):
        """재개 성공."""
        sample_rollout.state = CanaryState.PAUSED

        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = sample_rollout
            mock_service.resume.return_value = True
            mock_get_service.return_value = mock_service

            request = api_factory.post("/api/self-healing/canary/rollouts/test1234/resume/")
            request.user = admin_user

            view = CanaryRolloutActionView.as_view()
            response = view(request, rollout_id="test1234", action="resume")

            assert response.status_code == http_status.HTTP_200_OK
            mock_service.resume.assert_called_once_with("test1234")

    def test_cancel_success(self, api_factory, admin_user, sample_rollout):
        """취소 성공."""
        sample_rollout.state = CanaryState.CREATED

        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = sample_rollout
            mock_service.cancel.return_value = True
            mock_get_service.return_value = mock_service

            request = api_factory.post("/api/self-healing/canary/rollouts/test1234/cancel/")
            request.user = admin_user

            view = CanaryRolloutActionView.as_view()
            response = view(request, rollout_id="test1234", action="cancel")

            assert response.status_code == http_status.HTTP_200_OK
            mock_service.cancel.assert_called_once_with("test1234")

    def test_invalid_action(self, api_factory, admin_user, sample_rollout):
        """유효하지 않은 액션 시 400 에러."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = sample_rollout
            mock_get_service.return_value = mock_service

            request = api_factory.post("/api/self-healing/canary/rollouts/test1234/invalid/")
            request.user = admin_user

            view = CanaryRolloutActionView.as_view()
            response = view(request, rollout_id="test1234", action="invalid")

            assert response.status_code == http_status.HTTP_400_BAD_REQUEST
            assert "valid_actions" in response.data

    def test_action_on_not_found_rollout(self, api_factory, admin_user):
        """존재하지 않는 롤아웃에 액션 시 404 에러."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = None
            mock_get_service.return_value = mock_service

            request = api_factory.post("/api/self-healing/canary/rollouts/notfound/start/")
            request.user = admin_user

            view = CanaryRolloutActionView.as_view()
            response = view(request, rollout_id="notfound", action="start")

            assert response.status_code == http_status.HTTP_404_NOT_FOUND


# =============================================================================
# CanaryPanicRollbackView Tests
# =============================================================================


class TestCanaryPanicRollbackView:
    """CanaryPanicRollbackView 테스트."""

    def test_panic_rollback_success(self, api_factory, admin_user, active_rollout):
        """긴급 롤백 성공."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_rollouts.return_value = [active_rollout]
            mock_service.rollback.return_value = True
            mock_get_service.return_value = mock_service

            request = api_factory.post(
                "/api/self-healing/canary/panic-rollback/",
                {
                    "reason": "Production incident",
                    "emergency_code": "INCIDENT-001",
                },
                format="json",
            )
            request.user = admin_user

            view = CanaryPanicRollbackView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_200_OK
            assert response.data["status"] == "success"
            assert len(response.data["rolled_back"]) == 1
            mock_service.rollback.assert_called()

    def test_panic_rollback_no_active_rollouts(self, api_factory, admin_user):
        """활성 롤아웃 없을 때."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_rollouts.return_value = []
            mock_get_service.return_value = mock_service

            request = api_factory.post(
                "/api/self-healing/canary/panic-rollback/",
                {"reason": "Test"},
                format="json",
            )
            request.user = admin_user

            view = CanaryPanicRollbackView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_200_OK
            assert "No active rollouts" in response.data["message"]

    def test_panic_rollback_multiple_rollouts(self, api_factory, admin_user):
        """여러 롤아웃 동시 롤백."""
        rollout1 = CanaryRollout(
            id="rollout1",
            config_type="circuit_breaker",
            previous_values={},
            new_values={"threshold": 3},
            state=CanaryState.CANARY,
            stages=[CanaryStage(name="canary", clusters=["seoul"], percentage=10)],
            created_by="admin",
        )
        rollout2 = CanaryRollout(
            id="rollout2",
            config_type="dlq",
            previous_values={},
            new_values={"max_retries": 5},
            state=CanaryState.CANARY,
            stages=[CanaryStage(name="canary", clusters=["tokyo"], percentage=10)],
            created_by="admin",
        )

        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_rollouts.return_value = [rollout1, rollout2]
            mock_service.rollback.return_value = True
            mock_get_service.return_value = mock_service

            request = api_factory.post(
                "/api/self-healing/canary/panic-rollback/",
                {"reason": "Multi-service incident"},
                format="json",
            )
            request.user = admin_user

            view = CanaryPanicRollbackView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_200_OK
            assert len(response.data["rolled_back"]) == 2
            assert mock_service.rollback.call_count == 2


# =============================================================================
# CanaryMetricsView Tests
# =============================================================================


class TestCanaryMetricsView:
    """CanaryMetricsView 테스트."""

    def test_get_metrics_success(self, api_factory, admin_user, active_rollout):
        """메트릭 조회 성공."""
        mock_metrics = [
            CanaryMetrics(
                cluster="seoul-canary",
                stage_name="canary",
                error_rate_before=0.01,
                error_rate_after=0.02,
                latency_p99_before=50.0,
                latency_p99_after=55.0,
                requests_total=1000,
                is_healthy=True,
            )
        ]

        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = active_rollout
            mock_service.collect_metrics.return_value = mock_metrics
            mock_get_service.return_value = mock_service

            request = api_factory.get("/api/self-healing/canary/rollouts/test1234/metrics/")
            request.user = admin_user

            view = CanaryMetricsView.as_view()
            response = view(request, rollout_id="test1234")

            assert response.status_code == http_status.HTTP_200_OK
            assert response.data["rollout_id"] == "test1234"
            assert len(response.data["metrics"]) == 1
            assert response.data["metrics"][0]["cluster"] == "seoul-canary"

    def test_get_metrics_rollout_not_found(self, api_factory, admin_user):
        """존재하지 않는 롤아웃 메트릭 조회."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_rollout.return_value = None
            mock_get_service.return_value = mock_service

            request = api_factory.get("/api/self-healing/canary/rollouts/notfound/metrics/")
            request.user = admin_user

            view = CanaryMetricsView.as_view()
            response = view(request, rollout_id="notfound")

            assert response.status_code == http_status.HTTP_404_NOT_FOUND


# =============================================================================
# CanaryHistoryView Tests
# =============================================================================


class TestCanaryHistoryView:
    """CanaryHistoryView 테스트."""

    def test_get_history_success(self, api_factory, admin_user, completed_rollout):
        """롤아웃 이력 조회 성공."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_completed_rollouts.return_value = [completed_rollout]
            mock_get_service.return_value = mock_service

            request = api_factory.get("/api/self-healing/canary/history/")
            request.user = admin_user

            view = CanaryHistoryView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_200_OK
            assert response.data["status"] == "success"
            assert response.data["count"] == 1

    def test_get_history_filter_by_config_type(self, api_factory, admin_user, completed_rollout):
        """config_type으로 이력 필터링."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_completed_rollouts.return_value = [completed_rollout]
            mock_get_service.return_value = mock_service

            request = api_factory.get(
                "/api/self-healing/canary/history/",
                {"config_type": "circuit_breaker"},
            )
            request.user = admin_user

            view = CanaryHistoryView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_200_OK
            # circuit_breaker 타입만 포함
            for h in response.data["history"]:
                assert h["config_type"] == "circuit_breaker"

    def test_get_history_filter_by_state(self, api_factory, admin_user, completed_rollout):
        """state로 이력 필터링."""
        with patch(
            "selfhealing.api.django.views.canary.get_canary_rollout_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_completed_rollouts.return_value = [completed_rollout]
            mock_get_service.return_value = mock_service

            request = api_factory.get(
                "/api/self-healing/canary/history/",
                {"state": "completed"},
            )
            request.user = admin_user

            view = CanaryHistoryView.as_view()
            response = view(request)

            assert response.status_code == http_status.HTTP_200_OK
