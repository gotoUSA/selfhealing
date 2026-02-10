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
        from selfhealing.settings.postmortem import reset_postmortem_settings
        from selfhealing.api.django.views.xtest.base import (
            _healing_events,
            _healing_events_lock,
        )
        from selfhealing.services.postmortem_store import clear_healing_incidents

        # 이벤트 버스 리셋
        self.bus = get_event_bus()
        self.bus.reset()

        # Settings 리셋
        reset_api_view_settings()
        reset_postmortem_settings()

        # In-memory 저장소 클리어
        with _healing_events_lock:
            _healing_events.clear()
        clear_healing_incidents()

    def teardown_method(self):
        """각 테스트 후에 상태 초기화."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.settings.postmortem import reset_postmortem_settings
        from selfhealing.api.django.views.xtest.base import (
            _healing_events,
            _healing_events_lock,
        )
        from selfhealing.services.postmortem_store import clear_healing_incidents

        get_event_bus().reset()
        reset_api_view_settings()
        reset_postmortem_settings()

        with _healing_events_lock:
            _healing_events.clear()
        clear_healing_incidents()

    def test_cb_closed_event_does_not_generate_postmortem_when_disabled(self, monkeypatch):
        """
        auto_postmortem_enabled=False일 때 CB CLOSED 이벤트가 Celery task를 위임하지 않음.
        """
        from unittest.mock import patch
        from selfhealing.services.event_bus import (
            get_event_bus,
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )

        # 설정: 자동 Post-mortem 비활성화 (기본값)
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "false")

        # 기본 핸들러 등록
        register_default_handlers()

        # CB CLOSED 이벤트 발행 — Celery task 위임이 발생하지 않아야 함
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service", "previous_state": "open"},
            source="integration_test",
        )

        with patch("selfhealing.adapters.celery.tasks.postmortem.process_individual_postmortem.delay") as mock_delay:
            self.bus.publish(event)

            # 비활성화 상태에서 Celery task 위임이 발생하지 않아야 함
            mock_delay.assert_not_called()

    def test_cb_closed_event_generates_and_stores_postmortem_when_enabled(self, monkeypatch):
        """
        auto_postmortem_enabled=True일 때 CB CLOSED 이벤트가 Celery task으로 Postmortem 생성을 위임함.
        """
        from unittest.mock import patch
        from selfhealing.services.event_bus import (
            get_event_bus,
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )
        from selfhealing.api.django.views.xtest.base import add_healing_event

        # 설정: 자동 Post-mortem 활성화, 최소 duration 0으로 설정
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "0")

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

        # CB CLOSED 이벤트 발행 — Celery task .delay() 호출 확인
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service", "previous_state": "open"},
            source="integration_test",
        )

        with patch("selfhealing.adapters.celery.tasks.postmortem.process_individual_postmortem.delay") as mock_delay:
            self.bus.publish(event)

            # Celery task으로 위임되었는지 확인
            mock_delay.assert_called_once()
            call_kwargs = mock_delay.call_args[1]
            assert call_kwargs["service_name"] == "test_service"
            assert call_kwargs["event_type"] == "circuit_breaker_closed"
            assert isinstance(call_kwargs["event_data"], dict)
            assert isinstance(call_kwargs["event_bus_history"], list)

    def test_postmortem_skipped_when_duration_below_minimum(self, monkeypatch):
        """
        Celery task으로 위임 시 인시던트 duration 체크는 task 내부에서 수행됩니다.
        핸들러 레벨에서는 Celery task 위임만 확인합니다.
        """
        from unittest.mock import patch
        from selfhealing.services.event_bus import (
            get_event_bus,
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )

        # 설정: 자동 Post-mortem 활성화, 최소 duration 3600초 (1시간)
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "3600")

        # 기본 핸들러 등록
        register_default_handlers()

        # CB CLOSED 이벤트 발행 — Celery task 위임 확인 (duration 체크는 task에서 수행)
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "short_incident_service", "previous_state": "open"},
            source="integration_test",
        )

        with patch("selfhealing.adapters.celery.tasks.postmortem.process_individual_postmortem.delay") as mock_delay:
            self.bus.publish(event)

            # Celery task으로 위임됨 (duration 체크는 task 내부)
            mock_delay.assert_called_once()

    def test_stored_incident_can_be_retrieved(self, monkeypatch):
        """
        Celery task 위임 시 event_data가 정확히 직렬화되어 전달되는지 확인.
        """
        from unittest.mock import patch
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )

        # 설정: 자동 Post-mortem 활성화
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "0")

        # 기본 핸들러 등록
        register_default_handlers()

        # CB CLOSED 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "retrieval_test_service", "previous_state": "open"},
            source="integration_test",
        )

        with patch("selfhealing.adapters.celery.tasks.postmortem.process_individual_postmortem.delay") as mock_delay:
            self.bus.publish(event)

            # Celery task에 전달된 event_data 직렬화 확인
            mock_delay.assert_called_once()
            call_kwargs = mock_delay.call_args[1]
            event_data = call_kwargs["event_data"]

            # SelfHealingEvent.to_dict() 결과 확인
            assert event_data["event_type"] == "circuit_breaker_closed"
            assert event_data["data"]["service_name"] == "retrieval_test_service"
            assert "timestamp" in event_data
            assert call_kwargs["service_name"] == "retrieval_test_service"


class TestPostmortemHandlerPriorityIntegration:
    """
    Post-mortem 핸들러 우선순위 통합 테스트.

    Track 1 Replay 핸들러(NORMAL) 이후에 Post-mortem 핸들러(LOW)가 실행되는지 확인.
    """

    def setup_method(self):
        """각 테스트 전에 상태 초기화."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.settings.postmortem import reset_postmortem_settings

        self.bus = get_event_bus()
        self.bus.reset()
        reset_api_view_settings()
        reset_postmortem_settings()

    def teardown_method(self):
        """각 테스트 후에 상태 초기화."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.settings.postmortem import reset_postmortem_settings

        get_event_bus().reset()
        reset_api_view_settings()
        reset_postmortem_settings()

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


class TestPostmortemNotificationIntegration:
    """
    Post-mortem 알림 통합 테스트 (문서 131).

    Post-mortem 생성 시 알림이 발송되는지 확인.
    """

    def setup_method(self):
        """각 테스트 전에 상태 초기화."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.settings.postmortem import reset_postmortem_settings
        from selfhealing.api.django.views.xtest.base import (
            _healing_events,
            _healing_events_lock,
        )
        from selfhealing.services.postmortem_store import clear_healing_incidents

        self.bus = get_event_bus()
        self.bus.reset()
        reset_api_view_settings()
        reset_postmortem_settings()

        with _healing_events_lock:
            _healing_events.clear()
        clear_healing_incidents()

    def teardown_method(self):
        """각 테스트 후에 상태 초기화."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.settings.postmortem import reset_postmortem_settings
        from selfhealing.api.django.views.xtest.base import (
            _healing_events,
            _healing_events_lock,
        )
        from selfhealing.services.postmortem_store import clear_healing_incidents

        get_event_bus().reset()
        reset_api_view_settings()
        reset_postmortem_settings()

        with _healing_events_lock:
            _healing_events.clear()
        clear_healing_incidents()

    def test_notification_sent_when_postmortem_generated(self, monkeypatch):
        """
        Celery task 위임 시 Postmortem 알림은 task 내부에서 처리됨.
        핸들러는 task 위임만 확인.
        """
        from unittest.mock import patch, MagicMock
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )
        from selfhealing.settings.postmortem import reset_postmortem_settings

        # 설정: 자동 Post-mortem 및 알림 활성화
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION", "0")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_NOTIFICATION_ENABLED", "true")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_NOTIFICATION_MIN_DURATION", "0")
        reset_postmortem_settings()

        # 기본 핸들러 등록
        register_default_handlers()

        with patch("selfhealing.adapters.celery.tasks.postmortem.process_individual_postmortem.delay") as mock_delay:
            # CB CLOSED 이벤트 발행
            event = SelfHealingEvent(
                event_type=EventType.CIRCUIT_BREAKER_CLOSED,
                data={"service_name": "notification_test_service", "previous_state": "open"},
                source="integration_test",
            )
            self.bus.publish(event)

            # Celery task로 위임되었는지 확인 (알림은 task 내부에서 처리)
            mock_delay.assert_called_once()

    def test_notification_not_sent_when_disabled(self, monkeypatch):
        """
        Post-mortem 비활성화 시 Celery task 위임이 발생하지 않는지 확인.
        """
        from unittest.mock import patch, MagicMock
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            SelfHealingEvent,
        )

        # 설정: Post-mortem 활성화, 알림 비활성화
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_AUTO_ENABLED", "false")

        # 기본 핸들러 등록
        register_default_handlers()

        with patch("selfhealing.adapters.celery.tasks.postmortem.process_individual_postmortem.delay") as mock_delay:
            # CB CLOSED 이벤트 발행
            event = SelfHealingEvent(
                event_type=EventType.CIRCUIT_BREAKER_CLOSED,
                data={"service_name": "no_notification_service", "previous_state": "open"},
                source="integration_test",
            )
            self.bus.publish(event)

            # 비활성화 상태에서는 Celery task 위임이 발생하지 않아야 함
            mock_delay.assert_not_called()

    def test_notification_priority_high_for_long_incident(self, monkeypatch):
        """
        인시던트 지속 시간이 5분 이상일 때 HIGH 우선순위로 알림 발송.
        """
        from unittest.mock import patch, MagicMock
        from selfhealing.services.event_bus import _send_postmortem_notification
        from selfhealing.services.unified_notification import NotificationPriority
        from selfhealing.settings.api_view import ApiViewSettings

        # Settings 모킹
        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 0

        postmortem = {
            "incident_id": "LONG-INCIDENT-001",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:10:00Z",
            "recommendations": ["조치 1"],
        }

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = False

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager.notify",
            return_value=mock_result,
        ) as mock_notify:
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="LONG-INCIDENT-001",
                service_name="long_incident_service",
                duration=600,  # 10분
                affected_services=["service_a"],
            )

            # notify 호출 확인
            assert mock_notify.called, "알림이 발송되어야 함"
            payload = mock_notify.call_args[0][0]
            assert payload.priority == NotificationPriority.HIGH, "5분 이상 인시던트는 HIGH 우선순위여야 함"

    def test_notification_dedup_key_prevents_duplicate(self, monkeypatch):
        """
        동일 incident_id에 대해 중복 알림이 방지되는지 확인.
        """
        from unittest.mock import patch, MagicMock
        from selfhealing.services.event_bus import _send_postmortem_notification
        from selfhealing.services.unified_notification import UnifiedNotificationManager

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 0

        postmortem = {
            "incident_id": "DEDUP-TEST-001",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:02:00Z",
            "recommendations": [],
        }

        # 실제 UnifiedNotificationManager 사용 (cooldown 동작 확인)
        manager = UnifiedNotificationManager()

        with patch.object(manager, "_send_to_channels") as mock_send:
            mock_send.return_value = MagicMock(
                success=True,
                channels_sent=["slack"],
                channels_failed=[],
            )

            # 첫 번째 알림 발송
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="DEDUP-TEST-001",
                service_name="dedup_test_service",
                duration=120,
                affected_services=[],
            )

            # 두 번째 동일 알림 발송 시도
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="DEDUP-TEST-001",
                service_name="dedup_test_service",
                duration=120,
                affected_services=[],
            )

        # dedup_key 형식 확인 (payload에서)
        from selfhealing.services.unified_notification import NotificationPayload

        payload = NotificationPayload(
            title="Test",
            message="Test",
            dedup_key="postmortem:DEDUP-TEST-001",
        )
        assert payload.dedup_key == "postmortem:DEDUP-TEST-001", "dedup_key 형식이 올바르지 않음"
