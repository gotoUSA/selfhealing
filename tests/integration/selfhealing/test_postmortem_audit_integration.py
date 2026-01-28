"""
Postmortem Audit Integration Tests (문서 130).

Postmortem 관련 기능의 Audit 로깅 통합 테스트.

테스트 항목:
1. 수동 API (X-Test View)의 log_xtest_audit 호출 확인
2. 자동 트리거의 _write_to_wal 호출 확인
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_request():
    """Mock Request 생성."""
    request = MagicMock()
    request.data = {}
    request.headers = {"X-Test-Session": "test-session-123"}
    request.user = MagicMock()
    request.user.is_authenticated = True
    request.user.__str__ = lambda self: "test_user"
    return request


@pytest.mark.django_db
class TestPostmortemViewAudit:
    """Postmortem View의 Audit 로깅 테스트."""

    def test_postmortem_generator_view_calls_audit(self, mock_request):
        """PostmortemGeneratorView.post()가 log_xtest_audit을 호출하는지 확인."""
        from selfhealing.api.django.views.xtest.observability import PostmortemGeneratorView

        view = PostmortemGeneratorView()
        mock_request.data = {"incident_id": "TEST-INCIDENT-001"}
        mock_request.headers = {"X-Test-Session": "test-session-123", "X-Trace-ID": "trace-001"}

        audit_calls = []

        def mock_log_xtest_audit(**kwargs):
            audit_calls.append(kwargs)
            return 1

        with (
            patch.object(view, "check_chaos_permission", return_value=None),
            patch.object(view, "log_xtest_audit", side_effect=mock_log_xtest_audit),
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus,
            patch("selfhealing.services.circuit_breaker_service.get_circuit_breaker_service") as mock_cb,
            patch("selfhealing.api.django.views.xtest.base.collect_system_snapshot", return_value={}),
            patch("selfhealing.api.django.views.xtest.base.get_healing_events", return_value=[]),
            patch("selfhealing.services.postmortem_store.add_healing_incident"),
        ):
            mock_bus.return_value.get_history.return_value = []
            mock_cb.return_value.repository.get_all_states.return_value = []

            response = view.post(mock_request)

        assert len(audit_calls) == 1
        audit_call = audit_calls[0]
        assert audit_call["action"] == "generate_postmortem"
        assert audit_call["component"] == "observability"
        assert audit_call["result"] == "success"
        assert "incident_id" in audit_call["details"]

    def test_blast_radius_view_calls_audit(self, mock_request):
        """BlastRadiusTestView.post()가 log_xtest_audit을 호출하는지 확인."""
        from selfhealing.api.django.views.xtest.observability import BlastRadiusTestView

        view = BlastRadiusTestView()
        mock_request.data = {
            "affected_service": "service_a",
            "check_services": ["service_b"],
        }

        audit_calls = []

        def mock_log_xtest_audit(**kwargs):
            audit_calls.append(kwargs)
            return 1

        mock_cb_service = MagicMock()
        mock_cb_service.get_state.return_value = "closed"
        mock_cb_service.should_allow.return_value = True
        mock_cb_service.repository.get_all_states.return_value = []

        with (
            patch.object(view, "check_chaos_permission", return_value=None),
            patch.object(view, "log_xtest_audit", side_effect=mock_log_xtest_audit),
            patch("selfhealing.services.circuit_breaker_service.get_circuit_breaker_service", return_value=mock_cb_service),
            patch("selfhealing.api.django.views.xtest.base.add_healing_event"),
            patch("selfhealing.api.django.views.xtest.base.collect_system_snapshot", return_value={}),
        ):
            response = view.post(mock_request)

        assert len(audit_calls) == 1
        audit_call = audit_calls[0]
        assert audit_call["action"] == "blast_radius_test"
        assert audit_call["component"] == "observability"
        assert "affected_service" in audit_call["details"]
        assert "isolation_verified" in audit_call["details"]

    def test_multi_blast_radius_view_calls_audit(self, mock_request):
        """MultiServiceBlastRadiusView.post()가 log_xtest_audit을 호출하는지 확인."""
        from selfhealing.api.django.views.xtest.observability import MultiServiceBlastRadiusView

        view = MultiServiceBlastRadiusView()
        mock_request.data = {"test_services": ["service_a", "service_b"]}

        audit_calls = []

        def mock_log_xtest_audit(**kwargs):
            audit_calls.append(kwargs)
            return 1

        mock_state_a = MagicMock()
        mock_state_a.service_name = "service_a"
        mock_state_b = MagicMock()
        mock_state_b.service_name = "service_b"

        mock_cb_service = MagicMock()
        mock_cb_service.get_state.return_value = "closed"
        mock_cb_service.should_allow.return_value = True
        mock_cb_service.repository.get_all_states.return_value = [mock_state_a, mock_state_b]

        with (
            patch.object(view, "check_chaos_permission", return_value=None),
            patch.object(view, "log_xtest_audit", side_effect=mock_log_xtest_audit),
            patch("selfhealing.services.circuit_breaker_service.get_circuit_breaker_service", return_value=mock_cb_service),
        ):
            response = view.post(mock_request)

        assert len(audit_calls) == 1
        audit_call = audit_calls[0]
        assert audit_call["action"] == "multi_blast_radius_test"
        assert audit_call["component"] == "observability"
        assert "isolation_score_percent" in audit_call["details"]
        assert "total_services_tested" in audit_call["details"]

    def test_record_healing_event_view_calls_audit(self, mock_request):
        """RecordHealingEventView.post()가 log_xtest_audit을 호출하는지 확인."""
        from selfhealing.api.django.views.xtest.observability import RecordHealingEventView

        view = RecordHealingEventView()
        mock_request.data = {
            "event_type": "custom_event",
            "service": "test_service",
            "details": {"key": "value"},
        }

        audit_calls = []

        def mock_log_xtest_audit(**kwargs):
            audit_calls.append(kwargs)
            return 1

        with (
            patch.object(view, "check_chaos_permission", return_value=None),
            patch.object(view, "log_xtest_audit", side_effect=mock_log_xtest_audit),
            patch("selfhealing.api.django.views.xtest.observability.add_healing_event"),
            patch("selfhealing.api.django.views.xtest.observability.get_healing_events_count", return_value=10),
        ):
            response = view.post(mock_request)

        assert len(audit_calls) == 1
        audit_call = audit_calls[0]
        assert audit_call["action"] == "record_healing_event"
        assert audit_call["component"] == "observability"
        assert audit_call["details"]["event_type"] == "custom_event"
        assert audit_call["details"]["service"] == "test_service"


@pytest.mark.django_db
class TestAutoPostmortemAuditIntegration:
    """자동 Post-mortem 트리거의 Audit 로깅 통합 테스트."""

    @pytest.fixture(autouse=True)
    def reset_settings(self):
        """테스트 전후 설정 리셋."""
        from selfhealing.settings.api_view import reset_api_view_settings

        reset_api_view_settings()
        yield
        reset_api_view_settings()

    def test_auto_postmortem_calls_write_to_wal(self, monkeypatch):
        """자동 Post-mortem 생성 시 _write_to_wal이 호출되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed_postmortem,
            SelfHealingEvent,
            EventType,
        )

        monkeypatch.setenv("SELFHEALING_API_VIEW_AUTO_POSTMORTEM_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_API_VIEW_AUTO_POSTMORTEM_MIN_DURATION", "0")

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )

        wal_calls = []

        def mock_write_to_wal(**kwargs):
            wal_calls.append(kwargs)
            return 1

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = []

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus_fn,
            patch("selfhealing.services.postmortem_store.add_healing_incident"),
            patch("selfhealing.api.django.views.xtest.base.collect_system_snapshot", return_value={}),
            patch("selfhealing.api.django.views.xtest.base.get_healing_events", return_value=[]),
            patch("selfhealing.services.circuit_breaker_service.get_circuit_breaker_service", return_value=mock_cb_service),
            patch("selfhealing.services.audit.base._write_to_wal", side_effect=mock_write_to_wal),
        ):
            mock_bus_fn.return_value.get_history.return_value = []

            _on_circuit_breaker_closed_postmortem(event)

        postmortem_audit_calls = [c for c in wal_calls if c.get("event_type") == "POSTMORTEM_AUTO_GENERATED"]
        assert len(postmortem_audit_calls) == 1

        wal_call = postmortem_audit_calls[0]
        assert wal_call["source"] == "EventHandler.Postmortem"
        assert wal_call["domain"] == "selfhealing"
        assert "incident_id" in wal_call["details"]
        assert "service_name" in wal_call["details"]
        assert wal_call["details"]["service_name"] == "test_service"

    def test_auto_postmortem_wal_contains_correct_fields(self, monkeypatch):
        """자동 Post-mortem Audit이 올바른 필드를 포함하는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed_postmortem,
            SelfHealingEvent,
            EventType,
        )

        monkeypatch.setenv("SELFHEALING_API_VIEW_AUTO_POSTMORTEM_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_API_VIEW_AUTO_POSTMORTEM_MIN_DURATION", "0")

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "payment_service"},
            source="cb_recovery",
        )

        wal_calls = []

        def mock_write_to_wal(**kwargs):
            wal_calls.append(kwargs)
            return 1

        mock_cb_service = MagicMock()
        mock_state = MagicMock()
        mock_state.service_name = "payment_service"
        mock_state.state = "closed"
        mock_cb_service.repository.get_all_states.return_value = [mock_state]

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus_fn,
            patch("selfhealing.services.postmortem_store.add_healing_incident"),
            patch("selfhealing.api.django.views.xtest.base.collect_system_snapshot", return_value={}),
            patch("selfhealing.api.django.views.xtest.base.get_healing_events", return_value=[]),
            patch("selfhealing.services.circuit_breaker_service.get_circuit_breaker_service", return_value=mock_cb_service),
            patch("selfhealing.services.audit.base._write_to_wal", side_effect=mock_write_to_wal),
        ):
            mock_bus_fn.return_value.get_history.return_value = []

            _on_circuit_breaker_closed_postmortem(event)

        postmortem_calls = [c for c in wal_calls if c.get("event_type") == "POSTMORTEM_AUTO_GENERATED"]
        assert len(postmortem_calls) == 1

        call = postmortem_calls[0]

        # 필수 필드 검증
        assert call["event_type"] == "POSTMORTEM_AUTO_GENERATED"
        assert call["source"] == "EventHandler.Postmortem"
        assert call["success"] is True
        assert call["domain"] == "selfhealing"

        # details 필드 검증
        details = call["details"]
        assert "incident_id" in details
        assert details["incident_id"].startswith("AUTO-payment_service-")
        assert details["service_name"] == "payment_service"
        assert details["trigger_event"] == "circuit_breaker_closed"
        assert "duration_seconds" in details
        assert "affected_services" in details

    def test_auto_trigger_uses_postmortem_auto_generated_event_type(self, monkeypatch):
        """자동 트리거가 POSTMORTEM_AUTO_GENERATED 이벤트 타입을 사용하는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed_postmortem,
            SelfHealingEvent,
            EventType,
        )

        monkeypatch.setenv("SELFHEALING_API_VIEW_AUTO_POSTMORTEM_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_API_VIEW_AUTO_POSTMORTEM_MIN_DURATION", "0")

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )

        wal_calls = []

        def mock_write_to_wal(**kwargs):
            wal_calls.append(kwargs)
            return 1

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = []

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus_fn,
            patch("selfhealing.services.postmortem_store.add_healing_incident"),
            patch("selfhealing.api.django.views.xtest.base.collect_system_snapshot", return_value={}),
            patch("selfhealing.api.django.views.xtest.base.get_healing_events", return_value=[]),
            patch("selfhealing.services.circuit_breaker_service.get_circuit_breaker_service", return_value=mock_cb_service),
            patch("selfhealing.services.audit.base._write_to_wal", side_effect=mock_write_to_wal),
        ):
            mock_bus_fn.return_value.get_history.return_value = []

            _on_circuit_breaker_closed_postmortem(event)

        postmortem_calls = [c for c in wal_calls if c.get("event_type") == "POSTMORTEM_AUTO_GENERATED"]
        assert len(postmortem_calls) == 1

        # Source 확인: EventHandler.Postmortem (XTest.observability 아님)
        assert postmortem_calls[0]["source"] == "EventHandler.Postmortem"
