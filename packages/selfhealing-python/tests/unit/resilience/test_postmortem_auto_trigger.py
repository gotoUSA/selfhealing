"""
Postmortem Auto Trigger Tests (문서 128).

CB CLOSED 이벤트 발생 시 자동 Post-mortem 리포트 생성 테스트.

테스트 항목:
1. 설정 비활성화 시 Post-mortem 미생성 확인
2. 설정 활성화 시 자동 생성 확인
3. 최소 duration 미달 시 생성 스킵 확인
4. 저장된 인시던트 조회 확인
"""

import pytest
from datetime import datetime, timezone as tz
from unittest.mock import MagicMock, patch, PropertyMock


class TestAutoPostmortemSettings:
    """Auto Postmortem 설정 테스트."""

    def test_default_settings_disabled(self):
        """기본 설정에서 auto_postmortem_enabled가 False인지 확인."""
        from selfhealing.settings.api_view import ApiViewSettings, reset_api_view_settings

        reset_api_view_settings()

        settings = ApiViewSettings()

        assert settings.auto_postmortem_enabled is False
        assert settings.auto_postmortem_min_duration == 30

    def test_settings_with_env_enabled(self, monkeypatch):
        """환경변수로 auto_postmortem 활성화 테스트."""
        from selfhealing.settings.api_view import ApiViewSettings, reset_api_view_settings

        reset_api_view_settings()

        monkeypatch.setenv("SELFHEALING_API_VIEW_AUTO_POSTMORTEM_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_API_VIEW_AUTO_POSTMORTEM_MIN_DURATION", "60")

        settings = ApiViewSettings()

        assert settings.auto_postmortem_enabled is True
        assert settings.auto_postmortem_min_duration == 60


class TestCircuitBreakerClosedPostmortemHandler:
    """CB CLOSED Postmortem 핸들러 테스트."""

    def setup_method(self):
        """테스트 전 이벤트 버스 및 설정 리셋."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings

        self.bus = get_event_bus()
        self.bus.reset()
        reset_api_view_settings()

    def teardown_method(self):
        """테스트 후 이벤트 버스 및 설정 리셋."""
        from selfhealing.settings.api_view import reset_api_view_settings

        self.bus.reset()
        reset_api_view_settings()

    def test_handler_skips_when_disabled(self, monkeypatch):
        """auto_postmortem_enabled=False일 때 Post-mortem 생성 스킵 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed_postmortem,
            SelfHealingEvent,
            EventType,
        )
        from selfhealing.settings.api_view import reset_api_view_settings

        reset_api_view_settings()
        monkeypatch.setenv("SELFHEALING_API_VIEW_AUTO_POSTMORTEM_ENABLED", "false")

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )

        # 비활성화 상태에서는 핸들러가 초기 Settings 확인 후 바로 return해야 함
        # 따라서 logger.debug 메시지로 확인 (ImportError 발생 안 함)
        import logging

        with patch("selfhealing.services.event_bus.bus.logger") as mock_logger:
            _on_circuit_breaker_closed_postmortem(event)
            # DEBUG 로그가 호출되었는지 확인
            debug_calls = [call for call in mock_logger.debug.call_args_list]
            assert any("Auto postmortem disabled" in str(call) for call in debug_calls)

    def test_handler_generates_postmortem_when_enabled_with_full_mocking(self, monkeypatch):
        """auto_postmortem_enabled=True일 때 Post-mortem 자동 생성 확인 (전체 모킹)."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed_postmortem,
            SelfHealingEvent,
            EventType,
        )
        from selfhealing.settings.postmortem import reset_postmortem_settings

        # 핸들러는 PostmortemSettings (SELFHEALING_POSTMORTEM_ prefix) 사용
        # 환경변수 먼저 설정한 후 reset 호출해야 새 인스턴스에서 반영됨
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "0")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_INCIDENT_GROUP_ENABLED", "false")
        reset_postmortem_settings()

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )

        # Mock 생성: add_healing_incident가 호출되는지 추적
        add_incident_called = []

        def mock_add_healing_incident(incident):
            add_incident_called.append(incident)

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = []

        mock_timeline = [
            {
                "timestamp": datetime.now(tz.utc).isoformat(),
                "event_type": "circuit_breaker_opened",
                "details": {"service_name": "test_service"},
            }
        ]

        # Mock modules for dynamic import in handler
        import sys
        from types import ModuleType

        # Create mock module for selfhealing.api.django.views.xtest.base
        mock_base_module = ModuleType("selfhealing.api.django.views.xtest.base")
        mock_base_module.collect_system_snapshot = lambda: {"cpu": 50}
        mock_base_module.get_healing_events = lambda limit: []

        # Create mock module for selfhealing.services.postmortem_store (핸들러가 실제 import하는 경로)
        mock_store_module = ModuleType("selfhealing.services.postmortem_store")
        mock_store_module.add_healing_incident = mock_add_healing_incident
        mock_store_module.build_timeline = lambda h, l: mock_timeline
        mock_store_module.collect_service_states = lambda cb: ([], [])
        mock_store_module.generate_postmortem_data = lambda *args, **kwargs: {
            "incident_id": "AUTO-test-123",
            "duration_seconds": 120,
            "timeline": mock_timeline,
        }

        # Mock django.utils.timezone
        mock_timezone_module = MagicMock()
        mock_timezone_module.now.return_value = MagicMock(strftime=lambda fmt: "20260127-120000")

        with (
            patch.dict(
                sys.modules,
                {
                    "selfhealing.api.django.views.xtest.base": mock_base_module,
                    "selfhealing.services.postmortem_store": mock_store_module,
                    "django.utils.timezone": mock_timezone_module,
                },
            ),
            patch(
                "selfhealing.services.circuit_breaker_service.get_circuit_breaker_service",
                return_value=mock_cb_service,
            ),
        ):
            _on_circuit_breaker_closed_postmortem(event)

            # add_healing_incident가 호출되었는지 확인
            assert len(add_incident_called) == 1
            assert "AUTO" in add_incident_called[0]["incident_id"]

    def test_handler_skips_when_duration_below_minimum_with_mocking(self, monkeypatch):
        """duration이 min_duration 미만일 때 생성 스킵 확인 (전체 모킹)."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed_postmortem,
            SelfHealingEvent,
            EventType,
        )
        from selfhealing.settings.postmortem import reset_postmortem_settings

        # 올바른 prefix: SELFHEALING_POSTMORTEM_ 사용
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "60")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_INCIDENT_GROUP_ENABLED", "false")
        reset_postmortem_settings()

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )

        add_incident_called = []

        def mock_add_healing_incident(incident):
            add_incident_called.append(incident)

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = []

        import sys
        from types import ModuleType

        mock_base_module = ModuleType("selfhealing.api.django.views.xtest.base")
        mock_base_module.add_healing_incident = mock_add_healing_incident
        mock_base_module.collect_system_snapshot = lambda: {}
        mock_base_module.get_healing_events = lambda limit: []

        mock_store_module = ModuleType("selfhealing.services.postmortem_store")
        mock_store_module.add_healing_incident = mock_add_healing_incident
        mock_store_module.build_timeline = lambda h, l: []
        mock_store_module.collect_service_states = lambda cb: ([], [])
        mock_store_module.generate_postmortem_data = lambda *args, **kwargs: {
            "incident_id": "AUTO-test-123",
            "duration_seconds": 10,  # min_duration(60) 미만
            "timeline": [],
        }

        with (
            patch.dict(
                sys.modules,
                {
                    "selfhealing.api.django.views.xtest.base": mock_base_module,
                    "selfhealing.services.postmortem_store": mock_store_module,
                },
            ),
            patch(
                "selfhealing.services.circuit_breaker_service.get_circuit_breaker_service",
                return_value=mock_cb_service,
            ),
        ):
            _on_circuit_breaker_closed_postmortem(event)

            # duration이 최소값 미만이므로 저장되지 않아야 함
            assert len(add_incident_called) == 0


class TestPostmortemHandlerRegistration:
    """Postmortem 핸들러 등록 테스트."""

    def setup_method(self):
        """테스트 전 이벤트 버스 리셋."""
        from selfhealing.services.event_bus import get_event_bus

        self.bus = get_event_bus()
        self.bus.reset()

    def teardown_method(self):
        """테스트 후 이벤트 버스 리셋."""
        self.bus.reset()

    def test_postmortem_handler_registered_with_low_priority(self):
        """Postmortem 핸들러가 LOW 우선순위로 등록되는지 확인."""
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            EventPriority,
            _on_circuit_breaker_closed_postmortem,
        )

        register_default_handlers()

        # CB CLOSED 구독 목록 확인 (속성명: _subscriptions)
        subscriptions = self.bus._subscriptions.get(EventType.CIRCUIT_BREAKER_CLOSED, [])

        # 핸들러 중 postmortem 핸들러 찾기
        postmortem_subs = [s for s in subscriptions if s.handler == _on_circuit_breaker_closed_postmortem]

        assert len(postmortem_subs) == 1
        assert postmortem_subs[0].priority == EventPriority.LOW

    def test_both_cb_closed_handlers_registered(self):
        """CB CLOSED에 replay 핸들러와 postmortem 핸들러 모두 등록되는지 확인."""
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            _on_circuit_breaker_closed,
            _on_circuit_breaker_closed_postmortem,
        )

        register_default_handlers()

        subscriptions = self.bus._subscriptions.get(EventType.CIRCUIT_BREAKER_CLOSED, [])
        handler_funcs = [s.handler for s in subscriptions]

        assert _on_circuit_breaker_closed in handler_funcs
        assert _on_circuit_breaker_closed_postmortem in handler_funcs


class TestEventTriggeredPostmortem:
    """이벤트 트리거를 통한 Postmortem 생성 통합 테스트."""

    def setup_method(self):
        """테스트 전 이벤트 버스 리셋."""
        from selfhealing.services.event_bus import get_event_bus

        self.bus = get_event_bus()
        self.bus.reset()

    def teardown_method(self):
        """테스트 후 이벤트 버스 리셋."""
        self.bus.reset()

    def test_cb_closed_event_triggers_postmortem_handler(self, monkeypatch):
        """CB CLOSED 이벤트 발행 시 postmortem 핸들러가 호출되는지 확인."""
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            SelfHealingEvent,
            _on_circuit_breaker_closed_postmortem,
        )
        from selfhealing.settings.api_view import reset_api_view_settings

        reset_api_view_settings()

        # 기본 핸들러 등록
        register_default_handlers()

        # 핸들러 호출 추적
        handler_called = []

        original_handler = _on_circuit_breaker_closed_postmortem

        def tracking_handler(event):
            handler_called.append(event)
            # 원래 핸들러는 호출하지 않음 (mock 환경이므로)

        # 핸들러 교체 (테스트용) - _subscriptions 사용
        subscriptions = self.bus._subscriptions.get(EventType.CIRCUIT_BREAKER_CLOSED, [])
        for sub in subscriptions:
            if sub.handler == original_handler:
                sub.handler = tracking_handler

        # 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="test",
        )
        self.bus.publish(event)

        # 핸들러가 호출되었는지 확인
        assert len(handler_called) == 1
        assert handler_called[0].data["service_name"] == "test_service"


class TestGeneratePostmortemDataPure:
    """postmortem 데이터 구조 테스트 (Django 의존성 없이)."""

    def test_postmortem_data_has_required_fields(self):
        """postmortem 데이터에 필수 필드가 포함되어 있는지 확인."""
        # 순수 utils 모듈만 사용하여 테스트
        from selfhealing.utils.duration import calculate_incident_duration

        timeline = [
            {
                "timestamp": "2026-01-27T10:00:00+00:00",
                "event_type": "circuit_breaker_opened",
                "details": {"service_name": "test_service"},
            },
            {
                "timestamp": "2026-01-27T10:05:00+00:00",
                "event_type": "circuit_breaker_closed",
                "details": {"service_name": "test_service"},
            },
        ]

        current_time = "2026-01-27T10:10:00+00:00"
        result = calculate_incident_duration(timeline, current_time)

        # duration 계산 결과 확인
        assert result.started_at is not None
        assert result.resolved_at is not None
        assert result.duration_seconds is not None
        assert result.duration_seconds >= 0

    def test_postmortem_action_items_generation(self):
        """동적 action items 생성 테스트."""
        from selfhealing.utils.postmortem_actions import generate_dynamic_actions

        timeline = [
            {
                "timestamp": "2026-01-27T10:00:00+00:00",
                "event_type": "circuit_breaker_opened",
                "details": {"service_name": "test_service"},
            },
        ]
        affected_services = ["test_service"]
        duration_seconds = 300.0
        current_timestamp = "2026-01-27T10:05:00+00:00"

        auto_actions, recommendations = generate_dynamic_actions(
            timeline=timeline,
            affected_services=affected_services,
            duration_seconds=duration_seconds,
            current_timestamp=current_timestamp,
        )

        # 결과 타입 확인
        assert isinstance(auto_actions, list)
        assert isinstance(recommendations, list)
