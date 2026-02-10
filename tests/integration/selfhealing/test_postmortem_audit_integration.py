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
        """PostmortemGeneratorView.post()가 _write_to_wal audit를 호출하는지 확인."""
        from selfhealing.api.django.views.postmortem import PostmortemGeneratorView

        view = PostmortemGeneratorView()
        mock_request.data = {"incident_id": "TEST-INCIDENT-001"}
        mock_request.headers = {"X-Test-Session": "test-session-123", "X-Trace-ID": "trace-001"}

        wal_calls = []

        def mock_write_to_wal(**kwargs):
            wal_calls.append(kwargs)

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus,
            patch("selfhealing.services.circuit_breaker_service.get_circuit_breaker_service") as mock_cb,
            patch("selfhealing.api.django.views.xtest.base.collect_system_snapshot", return_value={}),
            patch("selfhealing.api.django.views.xtest.base.get_healing_events", return_value=[]),
            patch("selfhealing.services.postmortem_store.add_healing_incident"),
            patch("selfhealing.services.audit.base._write_to_wal", side_effect=mock_write_to_wal),
        ):
            mock_bus.return_value.get_history.return_value = []
            mock_cb.return_value.repository.get_all_states.return_value = []

            response = view.post(mock_request)

        assert len(wal_calls) == 1
        wal_call = wal_calls[0]
        assert wal_call["event_type"] == "POSTMORTEM_MANUAL_GENERATED"
        assert wal_call["source"] == "API.Postmortem"
        assert wal_call["success"] is True
        assert "incident_id" in wal_call["details"]

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
        from selfhealing.settings.postmortem import reset_postmortem_settings

        reset_api_view_settings()
        reset_postmortem_settings()
        yield
        reset_api_view_settings()
        reset_postmortem_settings()

    def test_auto_postmortem_calls_write_to_wal(self, monkeypatch):
        """자동 Post-mortem 생성 시 Celery task로 위임되는지 확인.

        WAL 기록은 Celery Worker의 process_individual_postmortem task에서 수행됩니다.
        핸들러는 task.delay() 호출만 담당합니다 (문서 213).
        """
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed_postmortem,
            SelfHealingEvent,
            EventType,
        )

        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "0")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_INCIDENT_GROUP_ENABLED", "false")

        from selfhealing.settings.postmortem import reset_postmortem_settings

        reset_postmortem_settings()

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus_fn,
            patch("selfhealing.adapters.celery.tasks.postmortem.process_individual_postmortem.delay") as mock_delay,
        ):
            mock_bus_fn.return_value.get_history.return_value = []

            _on_circuit_breaker_closed_postmortem(event)

        # Celery task로 위임 확인
        mock_delay.assert_called_once()
        call_kwargs = mock_delay.call_args[1]
        assert call_kwargs["service_name"] == "test_service"
        assert call_kwargs["event_type"] == "circuit_breaker_closed"
        assert isinstance(call_kwargs["event_data"], dict)
        assert isinstance(call_kwargs["event_bus_history"], list)

    def test_auto_postmortem_wal_contains_correct_fields(self, monkeypatch):
        """자동 Post-mortem Celery task 위임 시 올바른 필드가 전달되는지 확인.

        WAL 기록은 Celery Worker의 process_individual_postmortem task에서 수행됩니다 (문서 213).
        """
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed_postmortem,
            SelfHealingEvent,
            EventType,
        )

        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "0")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_INCIDENT_GROUP_ENABLED", "false")

        from selfhealing.settings.postmortem import reset_postmortem_settings

        reset_postmortem_settings()

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "payment_service"},
            source="cb_recovery",
        )

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus_fn,
            patch("selfhealing.adapters.celery.tasks.postmortem.process_individual_postmortem.delay") as mock_delay,
        ):
            mock_bus_fn.return_value.get_history.return_value = []

            _on_circuit_breaker_closed_postmortem(event)

        # Celery task 위임 확인
        mock_delay.assert_called_once()
        call_kwargs = mock_delay.call_args[1]

        # 필수 필드 검증
        assert call_kwargs["service_name"] == "payment_service"
        assert call_kwargs["event_type"] == "circuit_breaker_closed"

        # event_data 직렬화 검증 (SelfHealingEvent.to_dict())
        event_data = call_kwargs["event_data"]
        assert isinstance(event_data, dict)
        assert event_data["data"]["service_name"] == "payment_service"

        # event_bus_history 수집 검증
        assert isinstance(call_kwargs["event_bus_history"], list)

    def test_auto_trigger_uses_celery_task_delegation(self, monkeypatch):
        """자동 트리거가 Celery task로 위임하는지 확인.

        POSTMORTEM_AUTO_GENERATED 이벤트 타입의 WAL 기록은
        Celery Worker의 process_individual_postmortem task에서 수행됩니다 (문서 213).
        """
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed_postmortem,
            SelfHealingEvent,
            EventType,
        )

        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "0")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_INCIDENT_GROUP_ENABLED", "false")

        from selfhealing.settings.postmortem import reset_postmortem_settings

        reset_postmortem_settings()

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )

        with (
            patch("selfhealing.services.event_bus.get_event_bus") as mock_bus_fn,
            patch("selfhealing.adapters.celery.tasks.postmortem.process_individual_postmortem.delay") as mock_delay,
        ):
            mock_bus_fn.return_value.get_history.return_value = []

            _on_circuit_breaker_closed_postmortem(event)

        # Celery task 위임 확인
        mock_delay.assert_called_once()
        call_kwargs = mock_delay.call_args[1]

        # event_type이 올바르게 전달되는지 확인
        assert call_kwargs["event_type"] == "circuit_breaker_closed"
        assert call_kwargs["service_name"] == "test_service"
