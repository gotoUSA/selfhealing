"""
PostmortemGeneratorView 수동 Audit 테스트.

수동으로 Post-mortem을 생성할 때 _write_to_wal()을 통해 Audit이 기록되는지 확인.
"""

import pytest
from unittest.mock import MagicMock, patch

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()


@pytest.fixture
def mock_request():
    """Mock Request 생성."""
    request = MagicMock()
    request.data = {}
    request.user = MagicMock()
    request.user.is_authenticated = True
    request.user.__str__ = lambda self: "test_user"
    return request


@pytest.mark.django_db
class TestPostmortemManualAudit:
    """PostmortemGeneratorView의 수동 Audit 테스트."""

    def test_postmortem_generator_calls_write_to_wal(self, mock_request):
        """PostmortemGeneratorView.post()가 _write_to_wal을 호출하는지 확인."""
        from selfhealing.api.django.views.postmortem import PostmortemGeneratorView

        view = PostmortemGeneratorView()
        mock_request.data = {"incident_id": "MANUAL-TEST-001"}

        wal_calls = []

        def mock_write_to_wal(**kwargs):
            wal_calls.append(kwargs)
            return 1

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = []

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus,
            patch(
                "selfhealing.services.circuit_breaker.get_circuit_breaker_service",
                return_value=mock_cb_service,
            ),
            patch(
                "selfhealing.api.django.views.xtest.base.collect_system_snapshot",
                return_value={},
            ),
            patch(
                "selfhealing.api.django.views.xtest.base.get_healing_events",
                return_value=[],
            ),
            patch("selfhealing.services.postmortem.store.add_healing_incident"),
            patch(
                "selfhealing.services.audit.base._write_to_wal",
                side_effect=mock_write_to_wal,
            ),
        ):
            mock_bus.return_value.get_history.return_value = []

            response = view.post(mock_request)

        # Audit 호출 확인
        postmortem_calls = [c for c in wal_calls if c.get("event_type") == "POSTMORTEM_MANUAL_GENERATED"]
        assert len(postmortem_calls) == 1

        call = postmortem_calls[0]
        assert call["source"] == "API.Postmortem"
        assert call["domain"] == "selfhealing"
        assert call["success"] is True
        assert "incident_id" in call["details"]
        assert call["details"]["incident_id"] == "MANUAL-TEST-001"
        assert call["details"]["triggered_by"] == "test_user"

    def test_postmortem_audit_contains_affected_services(self, mock_request):
        """Audit에 affected_services가 포함되는지 확인."""
        from selfhealing.api.django.views.postmortem import PostmortemGeneratorView

        view = PostmortemGeneratorView()
        mock_request.data = {}

        wal_calls = []

        def mock_write_to_wal(**kwargs):
            wal_calls.append(kwargs)
            return 1

        mock_cb_service = MagicMock()
        mock_state = MagicMock()
        mock_state.service_name = "payment_service"
        mock_state.state = "open"
        mock_cb_service.repository.get_all_states.return_value = [mock_state]

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus,
            patch(
                "selfhealing.services.circuit_breaker.get_circuit_breaker_service",
                return_value=mock_cb_service,
            ),
            patch(
                "selfhealing.api.django.views.xtest.base.collect_system_snapshot",
                return_value={},
            ),
            patch(
                "selfhealing.api.django.views.xtest.base.get_healing_events",
                return_value=[],
            ),
            patch("selfhealing.services.postmortem.store.add_healing_incident"),
            patch(
                "selfhealing.services.audit.base._write_to_wal",
                side_effect=mock_write_to_wal,
            ),
        ):
            mock_bus.return_value.get_history.return_value = []

            response = view.post(mock_request)

        postmortem_calls = [c for c in wal_calls if c.get("event_type") == "POSTMORTEM_MANUAL_GENERATED"]
        assert len(postmortem_calls) == 1

        call = postmortem_calls[0]
        assert "affected_services" in call["details"]
        assert "payment_service" in call["details"]["affected_services"]

    def test_postmortem_audit_includes_duration(self, mock_request):
        """Audit에 duration_seconds가 포함되는지 확인."""
        from selfhealing.api.django.views.postmortem import PostmortemGeneratorView

        view = PostmortemGeneratorView()
        mock_request.data = {"incident_id": "DURATION-TEST-001"}

        wal_calls = []

        def mock_write_to_wal(**kwargs):
            wal_calls.append(kwargs)
            return 1

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = []

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus,
            patch(
                "selfhealing.services.circuit_breaker.get_circuit_breaker_service",
                return_value=mock_cb_service,
            ),
            patch(
                "selfhealing.api.django.views.xtest.base.collect_system_snapshot",
                return_value={},
            ),
            patch(
                "selfhealing.api.django.views.xtest.base.get_healing_events",
                return_value=[],
            ),
            patch("selfhealing.services.postmortem.store.add_healing_incident"),
            patch(
                "selfhealing.services.audit.base._write_to_wal",
                side_effect=mock_write_to_wal,
            ),
        ):
            mock_bus.return_value.get_history.return_value = []

            response = view.post(mock_request)

        postmortem_calls = [c for c in wal_calls if c.get("event_type") == "POSTMORTEM_MANUAL_GENERATED"]
        assert len(postmortem_calls) == 1

        call = postmortem_calls[0]
        assert "duration_seconds" in call["details"]

    def test_postmortem_audit_failure_does_not_break_response(self, mock_request):
        """Audit 실패가 응답에 영향을 주지 않는지 확인."""
        from selfhealing.api.django.views.postmortem import PostmortemGeneratorView

        view = PostmortemGeneratorView()
        mock_request.data = {"incident_id": "AUDIT-FAIL-TEST"}

        def mock_write_to_wal_error(**kwargs):
            raise Exception("Audit connection failed")

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = []

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus,
            patch(
                "selfhealing.services.circuit_breaker.get_circuit_breaker_service",
                return_value=mock_cb_service,
            ),
            patch(
                "selfhealing.api.django.views.xtest.base.collect_system_snapshot",
                return_value={},
            ),
            patch(
                "selfhealing.api.django.views.xtest.base.get_healing_events",
                return_value=[],
            ),
            patch("selfhealing.services.postmortem.store.add_healing_incident"),
            patch(
                "selfhealing.services.audit.base._write_to_wal",
                side_effect=mock_write_to_wal_error,
            ),
        ):
            mock_bus.return_value.get_history.return_value = []

            # Audit 실패해도 응답은 성공해야 함
            response = view.post(mock_request)

        assert response.status_code == 200
        assert response.data["status"] == "success"
        assert "postmortem" in response.data
