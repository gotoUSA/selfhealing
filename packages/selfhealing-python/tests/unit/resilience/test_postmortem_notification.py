"""
Postmortem Notification Tests (문서 131).

Post-mortem 생성 완료 시 알림 발송 테스트.

테스트 항목:
1. 알림 비활성화 시 미발송 확인
2. 알림 활성화 시 정상 발송 확인
3. 최소 duration 미달 시 알림 스킵 확인
4. 우선순위 결정 테스트 (5분 이상 또는 3개 이상 서비스 영향 시 HIGH)
5. 중복 알림 방지 (dedup_key) 확인
"""

from unittest.mock import MagicMock, patch


class TestPostmortemNotificationSettings:
    """Post-mortem 알림 설정 테스트."""

    def test_default_settings_enabled(self):
        """기본 설정에서 notification_enabled가 True인지 확인."""
        from selfhealing.settings.postmortem import (
            PostmortemSettings,
            reset_postmortem_settings,
        )

        reset_postmortem_settings()

        settings = PostmortemSettings()

        assert settings.notification_enabled is True
        assert settings.notification_min_duration == 60

    def test_settings_with_env_disabled(self, monkeypatch):
        """환경변수로 postmortem_notification 비활성화 테스트."""
        from selfhealing.settings.postmortem import (
            PostmortemSettings,
            reset_postmortem_settings,
        )

        reset_postmortem_settings()

        monkeypatch.setenv("SELFHEALING_POSTMORTEM_NOTIFICATION_ENABLED", "false")
        monkeypatch.setenv("SELFHEALING_POSTMORTEM_NOTIFICATION_MIN_DURATION", "120")

        settings = PostmortemSettings()

        assert settings.notification_enabled is False
        assert settings.notification_min_duration == 120


class TestSendPostmortemNotification:
    """_send_postmortem_notification 함수 테스트."""

    def test_notification_skipped_when_disabled(self):
        """notification_enabled=False일 때 알림 미발송 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification

        # Mock settings with notification disabled
        mock_settings = MagicMock()
        mock_settings.notification_enabled = False

        postmortem = {
            "incident_id": "TEST-001",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:05:00Z",
            "recommendations": ["권장 조치 1"],
        }

        with patch("selfhealing.services.event_bus.bus._cb_handlers.logger") as mock_logger:
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="TEST-001",
                service_name="test_service",
                duration=300,
                affected_services=["service_a", "service_b"],
            )

            # DEBUG 로그 확인: notification disabled
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("postmortem_notification_disabled" in call for call in debug_calls)

    def test_notification_skipped_when_duration_below_min(self):
        """duration이 notification_min_duration 미만일 때 알림 미발송 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification

        # Mock settings with min_duration = 60
        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 60

        postmortem = {
            "incident_id": "TEST-002",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:00:30Z",
            "recommendations": [],
        }

        with patch("selfhealing.services.event_bus.bus._cb_handlers.logger") as mock_logger:
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="TEST-002",
                service_name="test_service",
                duration=30,  # 60초 미만
                affected_services=["service_a"],
            )

            # DEBUG 로그 확인: duration < min
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("postmortem_notification_skipped_duration" in call for call in debug_calls)

    def test_notification_sent_when_enabled(self):
        """알림 활성화 시 정상 발송 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 60

        postmortem = {
            "incident_id": "TEST-003",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:05:00Z",
            "recommendations": ["권장 조치 1", "권장 조치 2"],
        }

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = False
        mock_manager.notify.return_value = mock_result

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager",
            return_value=mock_manager,
        ):
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="TEST-003",
                service_name="test_service",
                duration=300,
                affected_services=["service_a", "service_b"],
            )

            # UnifiedNotificationManager.notify가 호출되었는지 확인
            mock_manager.notify.assert_called_once()

            # 호출된 payload 검증
            payload = mock_manager.notify.call_args[0][0]
            assert "Post-mortem 생성: TEST-003" in payload.title
            assert payload.category.value == "operations"
            assert payload.dedup_key == "postmortem:TEST-003"

    def test_notification_priority_high_when_duration_over_5_minutes(self):
        """duration >= 300초(5분) 일 때 우선순위 HIGH 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification
        from selfhealing.services.unified_notification import NotificationPriority

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 60

        postmortem = {
            "incident_id": "TEST-004",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:06:00Z",
            "recommendations": [],
        }

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = False
        mock_manager.notify.return_value = mock_result

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager",
            return_value=mock_manager,
        ):
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="TEST-004",
                service_name="test_service",
                duration=360,  # 6분 = 360초
                affected_services=["service_a"],  # 1개만
            )

            payload = mock_manager.notify.call_args[0][0]
            assert payload.priority == NotificationPriority.HIGH

    def test_notification_priority_high_when_many_affected_services(self):
        """affected_services >= 3일 때 우선순위 HIGH 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification
        from selfhealing.services.unified_notification import NotificationPriority

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 60

        postmortem = {
            "incident_id": "TEST-005",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:02:00Z",
            "recommendations": [],
        }

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = False
        mock_manager.notify.return_value = mock_result

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager",
            return_value=mock_manager,
        ):
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="TEST-005",
                service_name="test_service",
                duration=120,  # 2분
                affected_services=["service_a", "service_b", "service_c"],  # 3개
            )

            payload = mock_manager.notify.call_args[0][0]
            assert payload.priority == NotificationPriority.HIGH

    def test_notification_priority_medium_when_small_incident(self):
        """duration < 300초 and affected_services < 3일 때 우선순위 MEDIUM 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification
        from selfhealing.services.unified_notification import NotificationPriority

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 60

        postmortem = {
            "incident_id": "TEST-006",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:02:00Z",
            "recommendations": [],
        }

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = False
        mock_manager.notify.return_value = mock_result

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager",
            return_value=mock_manager,
        ):
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="TEST-006",
                service_name="test_service",
                duration=120,  # 2분
                affected_services=["service_a", "service_b"],  # 2개
            )

            payload = mock_manager.notify.call_args[0][0]
            assert payload.priority == NotificationPriority.MEDIUM

    def test_notification_dedup_key_format(self):
        """dedup_key가 postmortem:{incident_id} 형식인지 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 0

        postmortem = {
            "incident_id": "TEST-007",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:02:00Z",
            "recommendations": [],
        }

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = False
        mock_manager.notify.return_value = mock_result

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager",
            return_value=mock_manager,
        ):
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="MY-INCIDENT-123",
                service_name="test_service",
                duration=120,
                affected_services=[],
            )

            payload = mock_manager.notify.call_args[0][0]
            assert payload.dedup_key == "postmortem:MY-INCIDENT-123"

    def test_notification_metadata_contains_required_fields(self):
        """알림 메타데이터에 필수 필드가 포함되어 있는지 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 0

        postmortem = {
            "incident_id": "TEST-008",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:05:00Z",
            "recommendations": ["조치 1"],
        }

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = False
        mock_manager.notify.return_value = mock_result

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager",
            return_value=mock_manager,
        ):
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="TEST-008",
                service_name="test_service",
                duration=300,
                affected_services=["service_a", "service_b"],
            )

            payload = mock_manager.notify.call_args[0][0]
            metadata = payload.metadata

            assert metadata["incident_id"] == "TEST-008"
            assert metadata["service_name"] == "test_service"
            assert metadata["duration_seconds"] == 300
            assert metadata["affected_services"] == ["service_a", "service_b"]
            assert metadata["resolved_at"] == "2026-01-28T10:05:00Z"
            assert "postmortem_url" in metadata

    def test_notification_category_is_operations(self):
        """알림 카테고리가 OPERATIONS인지 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification
        from selfhealing.services.unified_notification import NotificationCategory

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 0

        postmortem = {
            "incident_id": "TEST-009",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:02:00Z",
            "recommendations": [],
        }

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = False
        mock_manager.notify.return_value = mock_result

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager",
            return_value=mock_manager,
        ):
            _send_postmortem_notification(
                settings=mock_settings,
                postmortem=postmortem,
                incident_id="TEST-009",
                service_name="test_service",
                duration=120,
                affected_services=[],
            )

            payload = mock_manager.notify.call_args[0][0]
            assert payload.category == NotificationCategory.OPERATIONS

    def test_notification_handles_exception_gracefully(self):
        """알림 발송 중 예외 발생 시 시스템에 영향 없이 처리되는지 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 0

        postmortem = {
            "incident_id": "TEST-010",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:02:00Z",
            "recommendations": [],
        }

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager",
            side_effect=Exception("Notification system error"),
        ):
            with patch("selfhealing.services.event_bus.bus._cb_handlers.logger") as mock_logger:
                # 예외가 발생해도 함수가 정상 종료되어야 함
                _send_postmortem_notification(
                    settings=mock_settings,
                    postmortem=postmortem,
                    incident_id="TEST-010",
                    service_name="test_service",
                    duration=120,
                    affected_services=[],
                )

                # WARNING 로그 확인
                warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
                assert any("failed_send_postmortem_notification" in call for call in warning_calls)

    def test_notification_suppressed_logged(self):
        """알림이 suppressed된 경우 DEBUG 로그 확인."""
        from selfhealing.services.event_bus import _send_postmortem_notification

        mock_settings = MagicMock()
        mock_settings.notification_enabled = True
        mock_settings.notification_min_duration = 0

        postmortem = {
            "incident_id": "TEST-011",
            "started_at": "2026-01-28T10:00:00Z",
            "resolved_at": "2026-01-28T10:02:00Z",
            "recommendations": [],
        }

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = True
        mock_result.suppression_reason = "cooldown"
        mock_manager.notify.return_value = mock_result

        with patch(
            "selfhealing.services.unified_notification.UnifiedNotificationManager",
            return_value=mock_manager,
        ):
            with patch("selfhealing.services.event_bus.bus._cb_handlers.logger") as mock_logger:
                _send_postmortem_notification(
                    settings=mock_settings,
                    postmortem=postmortem,
                    incident_id="TEST-011",
                    service_name="test_service",
                    duration=120,
                    affected_services=[],
                )

                # DEBUG 로그에서 suppressed 확인
                debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
                assert any("suppressed" in call for call in debug_calls)


class TestCircuitBreakerClosedPostmortemWithNotification:
    """CB CLOSED Postmortem 핸들러에서 알림 발송 통합 테스트."""

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

    def test_send_postmortem_notification_function_signature(self):
        """_send_postmortem_notification 함수가 올바른 시그니처를 갖는지 확인."""
        import inspect

        from selfhealing.services.event_bus import _send_postmortem_notification

        sig = inspect.signature(_send_postmortem_notification)
        params = list(sig.parameters.keys())

        assert "settings" in params
        assert "postmortem" in params
        assert "incident_id" in params
        assert "service_name" in params
        assert "duration" in params
        assert "affected_services" in params

    def test_notification_settings_exposed_in_postmortem_settings(self):
        """PostmortemSettings에 notification 관련 설정이 포함되어 있는지 확인."""
        from selfhealing.settings.postmortem import PostmortemSettings

        settings = PostmortemSettings()

        # 기본값 확인
        assert hasattr(settings, "notification_enabled")
        assert hasattr(settings, "notification_min_duration")
        assert settings.notification_enabled is True
        assert settings.notification_min_duration == 60
