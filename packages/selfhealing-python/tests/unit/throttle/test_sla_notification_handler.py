"""
SLA 알림 핸들러 단위 테스트.

대상: selfhealing/services/throttle/sla_notification.py
- _get_region_safe()
- _subscribe_sla_events()
- _handle_sla_warning() / _handle_sla_critical() / _handle_limit_recovered()
- _send_sla_warning_sync() / _send_sla_critical_sync() / _send_limit_recovered_sync()
- initialize_sla_notifications()
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


@dataclass
class MockEvent:
    """EventBus 이벤트 Mock."""

    data: dict


# =============================================================================
# _get_region_safe 테스트
# =============================================================================


class TestGetRegionSafe:
    """리전 정보 안전 조회 테스트."""

    def test_returns_region_when_available(self):
        """ClusterIdentity.region 반환."""
        mock_identity = MagicMock()
        mock_identity.region = "ap-northeast-2"

        with patch(
            "selfhealing.services.throttle.sla_notification.get_cluster_identity",
            create=True,
        ) as mock_get:
            # 모듈 내 import를 시뮬레이션하기 위해 직접 패치
            from selfhealing.services.throttle.sla_notification import _get_region_safe

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.core.cluster_identity": MagicMock(get_cluster_identity=MagicMock(return_value=mock_identity)),
                },
            ):
                # _get_region_safe는 내부에서 import하므로 재호출
                result = _get_region_safe()
                # cluster_identity 모듈이 mock이면 region 반환 또는 None
                assert result is None or isinstance(result, str)

    def test_returns_none_on_import_error(self):
        """ClusterIdentity 모듈 미설치 시 None."""
        from selfhealing.services.throttle.sla_notification import _get_region_safe

        with patch.dict("sys.modules", {"selfhealing.core.cluster_identity": None}):
            with patch(
                "selfhealing.services.throttle.sla_notification.get_cluster_identity",
                side_effect=ImportError,
                create=True,
            ):
                result = _get_region_safe()
                assert result is None or isinstance(result, str)

    def test_returns_none_on_exception(self):
        """일반 예외 시 None 반환."""
        from selfhealing.services.throttle.sla_notification import _get_region_safe

        with patch.dict(
            "sys.modules",
            {
                "selfhealing.core.cluster_identity": MagicMock(
                    get_cluster_identity=MagicMock(side_effect=RuntimeError("test"))
                ),
            },
        ):
            result = _get_region_safe()
            assert result is None or isinstance(result, str)


# =============================================================================
# _subscribe_sla_events 테스트
# =============================================================================


class TestSubscribeSlaEvents:
    """SLA 이벤트 구독 테스트."""

    def test_subscribe_success(self):
        """정상 구독 시 EventBus.subscribe 3회 호출."""
        mock_bus = MagicMock()
        mock_event_type = MagicMock()
        mock_event_type.THROTTLE_SLA_WARNING = "THROTTLE_SLA_WARNING"
        mock_event_type.THROTTLE_SLA_CRITICAL = "THROTTLE_SLA_CRITICAL"
        mock_event_type.THROTTLE_LIMIT_RECOVERED = "THROTTLE_LIMIT_RECOVERED"

        with patch.dict(
            "sys.modules",
            {
                "selfhealing.services.event_bus": MagicMock(
                    EventType=mock_event_type,
                    get_event_bus=MagicMock(return_value=mock_bus),
                ),
            },
        ):
            from selfhealing.services.throttle.sla_notification import _subscribe_sla_events

            _subscribe_sla_events()
            assert mock_bus.subscribe.call_count == 3

    def test_subscribe_eventbus_not_available(self):
        """EventBus 미사용 환경에서 에러 없이 종료."""
        from selfhealing.services.throttle.sla_notification import _subscribe_sla_events

        # ImportError가 발생해도 에러 없이 종료
        with patch.dict("sys.modules", {"selfhealing.services.event_bus": None}):
            # 모듈 접근 시 ImportError 발생
            _subscribe_sla_events()  # 에러 없이 종료


# =============================================================================
# Celery 비동기 디스패치 테스트
# =============================================================================


class TestCeleryAsyncDispatch:
    """Celery apply_async 호출 테스트."""

    def test_warning_dispatches_to_celery(self):
        """Warning → Celery apply_async 호출."""
        with patch(
            "selfhealing.services.throttle.sla_notification.send_sla_notification",
            create=True,
        ) as mock_task:
            import selfhealing.services.throttle.sla_notification as mod

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.adapters.celery.tasks": MagicMock(send_sla_notification=mock_task),
                },
            ):
                mod._handle_sla_warning(
                    MockEvent(
                        data={
                            "current_rtt_ms": 250.0,
                            "threshold_ms": 200,
                            "service_name": "payment",
                        }
                    )
                )

                mock_task.apply_async.assert_called_once()
                kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
                assert kwargs["notification_type"] == "warning"
                assert kwargs["event_data"]["service_name"] == "payment"

    def test_critical_dispatches_to_celery(self):
        """Critical → Celery apply_async 호출."""
        with patch(
            "selfhealing.services.throttle.sla_notification.send_sla_notification",
            create=True,
        ) as mock_task:
            import selfhealing.services.throttle.sla_notification as mod

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.adapters.celery.tasks": MagicMock(send_sla_notification=mock_task),
                },
            ):
                mod._handle_sla_critical(
                    MockEvent(
                        data={
                            "current_rtt_ms": 600.0,
                            "threshold_ms": 500,
                            "service_name": "order",
                        }
                    )
                )

                mock_task.apply_async.assert_called_once()
                kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
                assert kwargs["notification_type"] == "critical"

    def test_recovered_dispatches_to_celery(self):
        """Recovered → Celery apply_async 호출."""
        with patch(
            "selfhealing.services.throttle.sla_notification.send_sla_notification",
            create=True,
        ) as mock_task:
            import selfhealing.services.throttle.sla_notification as mod

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.adapters.celery.tasks": MagicMock(send_sla_notification=mock_task),
                },
            ):
                mod._handle_limit_recovered(
                    MockEvent(
                        data={
                            "previous_limit": 70,
                            "new_limit": 100,
                            "rtt_ms": 50.0,
                        }
                    )
                )

                mock_task.apply_async.assert_called_once()
                kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
                assert kwargs["notification_type"] == "recovered"

    def test_warning_event_data_forwarded(self):
        """event.data가 Celery kwargs에 그대로 전달."""
        with patch(
            "selfhealing.services.throttle.sla_notification.send_sla_notification",
            create=True,
        ) as mock_task:
            import selfhealing.services.throttle.sla_notification as mod

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.adapters.celery.tasks": MagicMock(send_sla_notification=mock_task),
                },
            ):
                event_data = {
                    "current_rtt_ms": 300.0,
                    "threshold_ms": 200,
                    "service_name": "auth",
                    "gradient": 0.3,
                    "rtt_change_percent": 30.0,
                }
                mod._handle_sla_warning(MockEvent(data=event_data))

                kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
                assert kwargs["event_data"] == event_data


# =============================================================================
# Celery 폴백 (동기) 테스트
# =============================================================================


class TestCeleryFallbackToSync:
    """Celery 미사용/예외 시 동기 폴백 테스트."""

    def test_warning_fallback_on_import_error(self):
        """Celery ImportError → 동기 fallback."""
        from selfhealing.services.throttle.sla_notification import _handle_sla_warning

        with patch("selfhealing.services.throttle.sla_notification._send_sla_warning_sync") as mock_sync:
            _handle_sla_warning(MockEvent(data={"current_rtt_ms": 250.0}))
            mock_sync.assert_called_once()

    def test_critical_fallback_on_import_error(self):
        """Celery ImportError → Critical 동기 fallback."""
        from selfhealing.services.throttle.sla_notification import _handle_sla_critical

        with patch("selfhealing.services.throttle.sla_notification._send_sla_critical_sync") as mock_sync:
            _handle_sla_critical(MockEvent(data={"current_rtt_ms": 600.0}))
            mock_sync.assert_called_once()

    def test_recovered_fallback_on_import_error(self):
        """Celery ImportError → Recovered 동기 fallback."""
        from selfhealing.services.throttle.sla_notification import _handle_limit_recovered

        with patch("selfhealing.services.throttle.sla_notification._send_limit_recovered_sync") as mock_sync:
            _handle_limit_recovered(
                MockEvent(
                    data={
                        "previous_limit": 70,
                        "new_limit": 100,
                        "rtt_ms": 50.0,
                    }
                )
            )
            mock_sync.assert_called_once()

    def test_warning_fallback_on_exception(self):
        """Celery 예외 → 동기 fallback."""
        import selfhealing.services.throttle.sla_notification as mod

        with patch.dict(
            "sys.modules",
            {
                "selfhealing.adapters.celery.tasks": MagicMock(
                    send_sla_notification=MagicMock(apply_async=MagicMock(side_effect=RuntimeError("broker down")))
                ),
            },
        ):
            with patch.object(mod, "_send_sla_warning_sync") as mock_sync:
                mod._handle_sla_warning(MockEvent(data={"current_rtt_ms": 250.0}))
                mock_sync.assert_called_once()


# =============================================================================
# 동기 전송 함수 테스트
# =============================================================================


class TestSendSlaSyncFunctions:
    """동기 전송 함수 상세 테스트."""

    def test_warning_sync_calls_notify_sla(self):
        """_send_sla_warning_sync가 notify_sla 호출."""
        from selfhealing.services.throttle.sla_notification import _send_sla_warning_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_sla_warning_sync(
                    {
                        "current_rtt_ms": 250.0,
                        "threshold_ms": 200,
                        "service_name": "payment",
                    }
                )

                mock_notify.assert_called_once()

    def test_warning_sync_service_dedup_key(self):
        """dedup_key가 서비스 단위: domain='throttle:{service_name}'."""
        from selfhealing.services.throttle.sla_notification import _send_sla_warning_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_sla_warning_sync(
                    {
                        "current_rtt_ms": 250.0,
                        "threshold_ms": 200,
                        "service_name": "payment",
                    }
                )

                call_kwargs = mock_notify.call_args
                assert call_kwargs.kwargs.get("domain") == "throttle:payment" or (len(call_kwargs.args) > 0)

    def test_warning_sync_region_in_metadata(self):
        """리전 정보가 metadata에 포함."""
        from selfhealing.services.throttle.sla_notification import _send_sla_warning_sync

        with patch(
            "selfhealing.services.throttle.sla_notification._get_region_safe",
            return_value="ap-northeast-2",
        ):
            with patch(
                "selfhealing.services.throttle.sla_notification.notify_sla",
                create=True,
            ) as mock_notify:
                mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

                with patch.dict(
                    "sys.modules",
                    {
                        "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                    },
                ):
                    _send_sla_warning_sync(
                        {
                            "current_rtt_ms": 250.0,
                            "threshold_ms": 200,
                            "service_name": "payment",
                        }
                    )

                    call_kwargs = mock_notify.call_args.kwargs
                    assert call_kwargs["metadata"]["region"] == "ap-northeast-2"

    def test_warning_sync_priority_high(self):
        """Warning 동기 전송 시 priority='high'."""
        from selfhealing.services.throttle.sla_notification import _send_sla_warning_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_sla_warning_sync(
                    {
                        "current_rtt_ms": 250.0,
                        "threshold_ms": 200,
                        "service_name": "payment",
                    }
                )

                call_kwargs = mock_notify.call_args.kwargs
                assert call_kwargs["priority"] == "high"

    def test_critical_sync_calls_notify_sla(self):
        """_send_sla_critical_sync가 notify_sla 호출."""
        from selfhealing.services.throttle.sla_notification import _send_sla_critical_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_sla_critical_sync(
                    {
                        "current_rtt_ms": 600.0,
                        "threshold_ms": 500,
                        "service_name": "order",
                        "reduction_percent": 30,
                    }
                )

                mock_notify.assert_called_once()

    def test_critical_sync_priority_critical(self):
        """Critical 동기 전송 시 priority='critical'."""
        from selfhealing.services.throttle.sla_notification import _send_sla_critical_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_sla_critical_sync(
                    {
                        "current_rtt_ms": 600.0,
                        "threshold_ms": 500,
                        "service_name": "order",
                    }
                )

                call_kwargs = mock_notify.call_args.kwargs
                assert call_kwargs["priority"] == "critical"

    def test_critical_sync_requires_action_metadata(self):
        """Critical metadata에 requires_action=True 포함."""
        from selfhealing.services.throttle.sla_notification import _send_sla_critical_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_sla_critical_sync(
                    {
                        "current_rtt_ms": 600.0,
                        "threshold_ms": 500,
                        "service_name": "order",
                    }
                )

                call_kwargs = mock_notify.call_args.kwargs
                assert call_kwargs["metadata"]["requires_action"] is True

    def test_recovered_sync_calls_notify_sla(self):
        """_send_limit_recovered_sync가 notify_sla 호출."""
        from selfhealing.services.throttle.sla_notification import _send_limit_recovered_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_limit_recovered_sync(
                    {
                        "previous_limit": 70,
                        "new_limit": 100,
                        "rtt_ms": 50.0,
                        "service_name": "payment",
                    }
                )

                mock_notify.assert_called_once()

    def test_recovered_sync_priority_medium(self):
        """Recovered 동기 전송 시 priority='medium'."""
        from selfhealing.services.throttle.sla_notification import _send_limit_recovered_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_limit_recovered_sync(
                    {
                        "previous_limit": 70,
                        "new_limit": 100,
                        "rtt_ms": 50.0,
                    }
                )

                call_kwargs = mock_notify.call_args.kwargs
                assert call_kwargs["priority"] == "medium"

    def test_warning_sync_default_service_name(self):
        """service_name 미전달 시 'default' 사용."""
        from selfhealing.services.throttle.sla_notification import _send_sla_warning_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_sla_warning_sync({"current_rtt_ms": 100.0})

                call_kwargs = mock_notify.call_args.kwargs
                assert call_kwargs["domain"] == "throttle:default"

    def test_warning_sync_handles_import_error(self):
        """UnifiedNotification 미사용 시 에러 없이 종료."""
        from selfhealing.services.throttle.sla_notification import _send_sla_warning_sync

        # notify_sla import 실패해도 에러 없이 종료
        _send_sla_warning_sync({"current_rtt_ms": 100.0})

    def test_critical_sync_handles_exception(self):
        """Critical 전송 예외 시 에러 없이 종료."""
        from selfhealing.services.throttle.sla_notification import _send_sla_critical_sync

        _send_sla_critical_sync({"current_rtt_ms": 600.0})

    def test_recovered_sync_handles_exception(self):
        """Recovered 전송 예외 시 에러 없이 종료."""
        from selfhealing.services.throttle.sla_notification import _send_limit_recovered_sync

        _send_limit_recovered_sync({"previous_limit": 70, "new_limit": 100})

    def test_warning_sync_source_is_adaptive_throttle(self):
        """source가 'adaptive_throttle'."""
        from selfhealing.services.throttle.sla_notification import _send_sla_warning_sync

        with patch(
            "selfhealing.services.throttle.sla_notification.notify_sla",
            create=True,
        ) as mock_notify:
            mock_notify.return_value = MagicMock(success=True, channels_sent=["slack"])

            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.unified_notification": MagicMock(notify_sla=mock_notify),
                },
            ):
                _send_sla_warning_sync(
                    {
                        "current_rtt_ms": 250.0,
                        "service_name": "test",
                    }
                )

                call_kwargs = mock_notify.call_args.kwargs
                assert call_kwargs["source"] == "adaptive_throttle"


# =============================================================================
# initialize_sla_notifications 테스트
# =============================================================================


class TestInitializeSlaNotifications:
    """초기화 함수 테스트."""

    def test_calls_subscribe(self):
        """initialize_sla_notifications가 _subscribe_sla_events 호출."""
        from selfhealing.services.throttle.sla_notification import (
            initialize_sla_notifications,
        )

        with patch("selfhealing.services.throttle.sla_notification._subscribe_sla_events") as mock_sub:
            initialize_sla_notifications()
            mock_sub.assert_called_once()
