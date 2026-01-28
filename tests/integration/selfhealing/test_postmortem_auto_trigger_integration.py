"""
Postmortem Auto Trigger Integration Tests (문서 128).

CB CLOSED 이벤트 발생 시 자동 Post-mortem 생성 통합 테스트.

실제 Django 환경에서 이벤트 발행 → Post-mortem 생성 → 저장 → 조회 전체 흐름 검증.

Requirements:
- Docker Compose for Redis, DB
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: pytest tests/integration/selfhealing/test_postmortem_auto_trigger_integration.py -v
"""

import os
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()


class TestPostmortemAutoTriggerIntegration:
    """
    CB CLOSED 이벤트 → 자동 Post-mortem 생성 통합 테스트.

    실제 이벤트 버스, Settings, 저장소 사용.
    """

    def setup_method(self):
        """각 테스트 전에 상태 초기화."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.api.django.views.xtest.base import (
            _healing_events,
            _healing_incidents,
            _healing_events_lock,
        )

        # 이벤트 버스 리셋
        self.bus = get_event_bus()
        self.bus.reset()

        # Settings 리셋
        reset_api_view_settings()

        # In-memory 저장소 클리어
        with _healing_events_lock:
            _healing_events.clear()
            _healing_incidents.clear()

    def teardown_method(self):
        """각 테스트 후에 상태 초기화."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.api.django.views.xtest.base import (
            _healing_events,
            _healing_incidents,
            _healing_events_lock,
        )

        get_event_bus().reset()
        reset_api_view_settings()

        with _healing_events_lock:
            _healing_events.clear()
            _healing_incidents.clear()

    def test_cb_closed_event_does_not_generate_postmortem_when_disabled(self, monkeypatch):
        """
        auto_postmortem_enabled=False일 때 CB CLOSED 이벤트가 Post-mortem을 생성하지 않음.
        """
        from selfhealing.services.event_bus import (
            get_event_bus,
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )
        from selfhealing.api.django.views.xtest.base import get_healing_incidents

        # 설정: 자동 Post-mortem 비활성화 (기본값)
        monkeypatch.setenv("SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_ENABLED", "false")

        # 기본 핸들러 등록
        register_default_handlers()

        # CB CLOSED 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service", "previous_state": "open"},
            source="integration_test",
        )
        self.bus.publish(event)

        # 저장된 인시던트 확인 - 없어야 함
        incidents = get_healing_incidents(limit=10)
        auto_incidents = [i for i in incidents if "AUTO-" in i.get("incident_id", "")]

        assert len(auto_incidents) == 0, "비활성화 상태에서 Auto Post-mortem이 생성되면 안 됨"

    def test_cb_closed_event_generates_and_stores_postmortem_when_enabled(self, monkeypatch):
        """
        auto_postmortem_enabled=True일 때 CB CLOSED 이벤트가 Post-mortem을 생성하고 저장함.
        """
        from selfhealing.services.event_bus import (
            get_event_bus,
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )
        from selfhealing.api.django.views.xtest.base import (
            get_healing_incidents,
            add_healing_event,
        )

        # 설정: 자동 Post-mortem 활성화, 최소 duration 0으로 설정
        monkeypatch.setenv("SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_MIN_DURATION", "0")

        # 기본 핸들러 등록
        register_default_handlers()

        # 선행 이벤트 기록 (타임라인 구성용)
        add_healing_event(
            {
                "event_type": "circuit_breaker_opened",
                "service": "test_service",
                "details": {"reason": "too many failures"},
            }
        )

        # CB CLOSED 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service", "previous_state": "open"},
            source="integration_test",
        )
        self.bus.publish(event)

        # 저장된 인시던트 확인 - 있어야 함
        incidents = get_healing_incidents(limit=10)
        auto_incidents = [i for i in incidents if "AUTO-" in i.get("incident_id", "")]

        assert len(auto_incidents) >= 1, "활성화 상태에서 Auto Post-mortem이 생성되어야 함"

        # Post-mortem 데이터 구조 검증
        postmortem = auto_incidents[0]
        assert "incident_id" in postmortem
        assert "test_service" in postmortem["incident_id"]
        assert "generated_at" in postmortem
        assert "summary" in postmortem
        assert "timeline" in postmortem

    def test_postmortem_skipped_when_duration_below_minimum(self, monkeypatch):
        """
        인시던트 지속 시간이 min_duration 미만이면 Post-mortem 생성 스킵.
        """
        from selfhealing.services.event_bus import (
            get_event_bus,
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )
        from selfhealing.api.django.views.xtest.base import get_healing_incidents

        # 설정: 자동 Post-mortem 활성화, 최소 duration 3600초 (1시간)
        monkeypatch.setenv("SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_MIN_DURATION", "3600")

        # 기본 핸들러 등록
        register_default_handlers()

        # CB CLOSED 이벤트 발행 (타임라인 없이 - duration 계산 불가 또는 0)
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "short_incident_service", "previous_state": "open"},
            source="integration_test",
        )
        self.bus.publish(event)

        # 저장된 인시던트 확인 - 없어야 함 (duration < min_duration)
        incidents = get_healing_incidents(limit=10)
        auto_incidents = [
            i
            for i in incidents
            if "AUTO-" in i.get("incident_id", "") and "short_incident_service" in i.get("incident_id", "")
        ]

        assert len(auto_incidents) == 0, "duration이 min_duration 미만이면 Post-mortem이 생성되면 안 됨"

    def test_stored_incident_can_be_retrieved(self, monkeypatch):
        """
        저장된 인시던트를 조회할 수 있는지 확인.
        """
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )
        from selfhealing.api.django.views.xtest.base import (
            get_healing_incidents,
            get_healing_incidents_count,
        )

        # 설정: 자동 Post-mortem 활성화
        monkeypatch.setenv("SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_MIN_DURATION", "0")

        # 기본 핸들러 등록
        register_default_handlers()

        # 초기 카운트 확인
        initial_count = get_healing_incidents_count()

        # CB CLOSED 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "retrieval_test_service", "previous_state": "open"},
            source="integration_test",
        )
        self.bus.publish(event)

        # 카운트 증가 확인
        new_count = get_healing_incidents_count()
        assert new_count > initial_count, "인시던트 저장 후 카운트가 증가해야 함"

        # 조회 확인
        incidents = get_healing_incidents(limit=10)
        assert len(incidents) > 0, "저장된 인시던트를 조회할 수 있어야 함"

        # 가장 최근 인시던트가 우리가 생성한 것인지 확인
        latest = incidents[-1]
        assert "retrieval_test_service" in latest.get("incident_id", ""), "조회한 인시던트가 우리가 생성한 것이어야 함"
        assert "recorded_at" in latest, "recorded_at 타임스탬프가 있어야 함"


class TestPostmortemHandlerPriorityIntegration:
    """
    Post-mortem 핸들러 우선순위 통합 테스트.

    Track 1 Replay 핸들러(NORMAL) 이후에 Post-mortem 핸들러(LOW)가 실행되는지 확인.
    """

    def setup_method(self):
        """각 테스트 전에 상태 초기화."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings

        self.bus = get_event_bus()
        self.bus.reset()
        reset_api_view_settings()

    def teardown_method(self):
        """각 테스트 후에 상태 초기화."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings

        get_event_bus().reset()
        reset_api_view_settings()

    def test_handlers_registered_in_correct_priority_order(self):
        """
        CB CLOSED 핸들러가 올바른 우선순위로 등록되는지 확인.

        - _on_circuit_breaker_closed: NORMAL (Track 1 Replay)
        - _on_circuit_breaker_closed_postmortem: LOW (자동 Post-mortem)
        """
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            EventPriority,
            _on_circuit_breaker_closed,
            _on_circuit_breaker_closed_postmortem,
        )

        register_default_handlers()

        # 구독 정보 조회
        subscriptions = self.bus._subscriptions.get(EventType.CIRCUIT_BREAKER_CLOSED, [])

        # 핸들러별 우선순위 확인
        replay_handler = next((s for s in subscriptions if s.handler == _on_circuit_breaker_closed), None)
        postmortem_handler = next((s for s in subscriptions if s.handler == _on_circuit_breaker_closed_postmortem), None)

        assert replay_handler is not None, "Replay 핸들러가 등록되어야 함"
        assert postmortem_handler is not None, "Postmortem 핸들러가 등록되어야 함"

        assert replay_handler.priority == EventPriority.NORMAL, "Replay 핸들러는 NORMAL 우선순위여야 함"
        assert postmortem_handler.priority == EventPriority.LOW, "Postmortem 핸들러는 LOW 우선순위여야 함"

        # 우선순위 순서 확인 (NORMAL > LOW, 높은 것이 먼저)
        assert (
            replay_handler.priority.value > postmortem_handler.priority.value
        ), "Replay 핸들러가 Postmortem 핸들러보다 먼저 실행되어야 함"
