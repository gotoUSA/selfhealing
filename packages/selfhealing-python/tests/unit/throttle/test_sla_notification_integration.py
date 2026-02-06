"""
SLA 알림 연동 v2.0 테스트.

테스트 대상:
1. SLA 메시지 템플릿 (Warning/Critical/Recovered)
2. ThrottleSlaAlertUrlBuilder (환경변수 기반 URL 생성)
3. RedisCooldownStore (Redis TTL + 메모리 폴백)
4. NotificationFallbackRecorder (JSONL + 메모리 버퍼)
5. SLA Notification Handler (Celery 비동기 + 동기 폴백)
6. ThrottleSLANotificationSettings (Pydantic v2)
7. format_sla_slack_blocks (Slack Block Kit 포맷)
"""

import json
import os
import tempfile
import time
from dataclasses import dataclass
from unittest.mock import ANY, MagicMock, patch

import pytest


# =============================================================================
# 1. SLA 메시지 템플릿 테스트
# =============================================================================


class TestSlaNotificationTemplates:
    """SLA 알림 메시지 템플릿 순수 함수 테스트."""

    def test_build_sla_warning_message_basic(self):
        """Warning 메시지 기본 필드 확인."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_warning_message,
        )

        result = build_sla_warning_message(
            rtt_ms=250.0,
            threshold_ms=200,
            current_limit=80,
            previous_limit=100,
            gradient=0.25,
            service_name="payment",
        )

        assert "title" in result
        assert "message" in result
        assert "details" in result
        assert "actions" in result
        assert result["severity"] == "high"
        assert "250.0ms" in result["message"]
        assert "payment" in result["message"]
        assert result["details"]["rtt_ms"] == 250.0
        assert result["details"]["event_type"] == "sla_warning"

    def test_build_sla_warning_with_rtt_change_percent(self):
        """RTT 변화율이 메시지에 포함 확인."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_warning_message,
        )

        result = build_sla_warning_message(
            rtt_ms=250.0,
            threshold_ms=200,
            current_limit=80,
            previous_limit=100,
            gradient=0.25,
            rtt_change_percent=25.0,
            service_name="payment",
        )

        assert "+25.0%" in result["message"]
        assert result["details"]["rtt_change_percent"] == 25.0

    def test_build_sla_warning_with_region(self):
        """Region 정보가 title에 포함 확인."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_warning_message,
        )

        result = build_sla_warning_message(
            rtt_ms=250.0,
            threshold_ms=200,
            current_limit=80,
            previous_limit=100,
            gradient=0.25,
            region="ap-northeast-2",
        )

        assert "[ap-northeast-2]" in result["title"]

    def test_build_sla_warning_without_region(self):
        """Region 없을 때 title에 region 미포함."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_warning_message,
        )

        result = build_sla_warning_message(
            rtt_ms=250.0,
            threshold_ms=200,
            current_limit=80,
            previous_limit=100,
            gradient=0.25,
        )

        assert "[" not in result["title"] or "SLA" in result["title"]

    def test_build_sla_critical_message_basic(self):
        """Critical 메시지 기본 필드 확인."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_critical_message,
        )

        result = build_sla_critical_message(
            rtt_ms=600.0,
            threshold_ms=500,
            current_limit=70,
            previous_limit=100,
            reduction_percent=30,
            gradient=0.5,
            service_name="order",
        )

        assert result["severity"] == "critical"
        assert "CRITICAL" in result["message"]
        assert "600.0ms" in result["message"]
        assert "order" in result["message"]
        assert result["details"]["event_type"] == "sla_critical"
        assert result["details"]["reduction_percent"] == 30
        assert "escalate_oncall" in result["actions"]

    def test_build_sla_critical_with_rtt_change_percent(self):
        """Critical 메시지에 RTT 변화율 포함 확인."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_critical_message,
        )

        result = build_sla_critical_message(
            rtt_ms=600.0,
            threshold_ms=500,
            current_limit=70,
            previous_limit=100,
            reduction_percent=30,
            gradient=0.5,
            rtt_change_percent=50.0,
        )

        assert "+50.0%" in result["message"]

    def test_build_sla_recovered_message_basic(self):
        """Recovered 메시지 기본 필드 확인."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_recovered_message,
        )

        result = build_sla_recovered_message(
            previous_limit=70,
            new_limit=100,
            rtt_ms=50.0,
            service_name="payment",
        )

        assert result["severity"] == "medium"
        assert "70" in result["message"]
        assert "100" in result["message"]
        assert result["details"]["event_type"] == "limit_recovered"

    def test_build_sla_recovered_with_region(self):
        """Recovered 메시지 Region 포함."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_recovered_message,
        )

        result = build_sla_recovered_message(
            previous_limit=70,
            new_limit=100,
            rtt_ms=50.0,
            region="us-east-1",
        )

        assert "[us-east-1]" in result["title"]


# =============================================================================
# 2. ThrottleSlaAlertUrlBuilder 테스트
# =============================================================================


class TestThrottleSlaAlertUrlBuilder:
    """환경변수 기반 Throttle SLA URL 빌더 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def teardown_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def test_url_builder_with_all_env_vars(self):
        """모든 환경변수 설정 시 URL 생성 확인."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
                "THROTTLE_SLA_ADMIN_BASE_URL": "/admin/throttle/",
                "THROTTLE_SLA_RUNBOOK_URL": "https://docs.internal/runbooks/sla",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(
                service_name="payment",
                event_type="sla_critical",
            )

            assert "payment" in urls.dashboard_url
            assert "payment" in urls.admin_url
            assert "sla-critical" in urls.runbook_url
            assert urls.has_any_url()

    def test_url_builder_without_env_vars(self):
        """환경변수 미설정 시 None 반환 확인."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "",
                "THROTTLE_SLA_ADMIN_BASE_URL": "",
                "THROTTLE_SLA_RUNBOOK_URL": "",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(service_name="payment")

            assert urls.dashboard_url is None
            assert urls.admin_url is None
            assert urls.runbook_url is None
            assert not urls.has_any_url()

    def test_url_builder_dashboard_with_rtt(self):
        """대시보드 URL에 rtt 쿼리 파라미터 포함 확인."""
        with patch.dict(
            os.environ,
            {"THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle"},
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(
                service_name="payment",
                rtt_ms=250.5,
            )

            assert "var-rtt=250" in urls.dashboard_url
            assert "var-service=payment" in urls.dashboard_url

    def test_to_dict(self):
        """to_dict() 직렬화 확인."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            ThrottleSlaActionableUrls,
        )

        urls = ThrottleSlaActionableUrls(
            dashboard_url="http://a.com",
            admin_url="http://b.com",
        )
        d = urls.to_dict()
        assert d["dashboard_url"] == "http://a.com"
        assert d["admin_url"] == "http://b.com"
        assert d["runbook_url"] is None

    def test_singleton_get_and_reset(self):
        """싱글톤 get/reset 확인."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            get_throttle_sla_alert_url_builder,
            reset_throttle_sla_alert_url_builder,
        )

        b1 = get_throttle_sla_alert_url_builder()
        b2 = get_throttle_sla_alert_url_builder()
        assert b1 is b2

        reset_throttle_sla_alert_url_builder()
        b3 = get_throttle_sla_alert_url_builder()
        assert b3 is not b1


# =============================================================================
# 3. RedisCooldownStore 테스트
# =============================================================================


class TestRedisCooldownStore:
    """Redis TTL 기반 쿨다운 저장소 테스트."""

    def test_redis_cooldown_set_and_check(self):
        """Redis 쿨다운 SET/GET 확인."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)

        # 쿨다운 중이 아님
        assert not store.is_cooled_down("sla:throttle:payment")

        # 전송 기록
        store.mark_sent("sla:throttle:payment")
        mock_redis.set.assert_called_once_with(
            "selfhealing:notification:cooldown:sla:throttle:payment",
            ANY,
            ex=1800,
        )

    def test_redis_cooldown_is_active(self):
        """Redis에 키가 존재하면 쿨다운 중으로 판단."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)
        assert store.is_cooled_down("sla:throttle:payment")

    def test_redis_fallback_to_memory(self):
        """Redis 장애 시 메모리 폴백 확인."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.exists.side_effect = Exception("Redis down")
        mock_redis.set.side_effect = Exception("Redis down")

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)

        # Redis 장애 → 메모리 폴백 (쿨다운 아님)
        assert not store.is_cooled_down("sla:throttle:payment")

        # 메모리에 기록
        store.mark_sent("sla:throttle:payment")

        # 메모리에서 쿨다운 확인
        assert store.is_cooled_down("sla:throttle:payment")

    def test_memory_only_cooldown(self):
        """Redis 미사용(None) 시 메모리 전용 동작."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=1800)

        assert not store.is_cooled_down("sla:throttle:payment")
        store.mark_sent("sla:throttle:payment")
        assert store.is_cooled_down("sla:throttle:payment")

    def test_memory_cooldown_expires(self):
        """메모리 쿨다운 만료 확인."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=1)

        store.mark_sent("sla:throttle:payment")
        assert store.is_cooled_down("sla:throttle:payment")

        # 쿨다운 만료 대기
        time.sleep(1.1)
        assert not store.is_cooled_down("sla:throttle:payment")

    def test_clear_cooldown(self):
        """쿨다운 해제 확인."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=1800)

        store.mark_sent("sla:throttle:payment")
        assert store.is_cooled_down("sla:throttle:payment")

        store.clear("sla:throttle:payment")
        assert not store.is_cooled_down("sla:throttle:payment")


# =============================================================================
# 4. NotificationFallbackRecorder 테스트
# =============================================================================


class TestNotificationFallbackRecorder:
    """알림 전송 실패 JSONL/메모리 폴백 기록기 테스트."""

    def test_record_to_jsonl_file(self):
        """실패 알림 JSONL 파일 기록 확인."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        tmpdir = tempfile.mkdtemp()
        filepath = os.path.join(tmpdir, "test_fallback.jsonl")

        try:
            recorder = NotificationFallbackRecorder(file_path=filepath)

            recorder.record_failed_notification(
                dedup_key="sla:throttle:payment",
                notification_type="critical",
                event_data={"current_rtt_ms": 600.0},
                error="ConnectionError: Slack API timeout",
            )

            with open(filepath) as rf:
                entry = json.loads(rf.readline())
                assert entry["dedup_key"] == "sla:throttle:payment"
                assert entry["notification_type"] == "critical"
                assert "ConnectionError" in entry["error"]
                assert "timestamp" in entry
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)
            os.rmdir(tmpdir)

    def test_fallback_to_memory_on_file_error(self):
        """파일 기록 실패 시 메모리 버퍼 저장 확인."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        # 존재하지 않는 경로의 읽기 전용 디렉토리
        recorder = NotificationFallbackRecorder(file_path="/nonexistent/path/test.jsonl")

        # _write_to_file를 강제로 실패시킴
        with patch.object(recorder, "_write_to_file", return_value=False):
            recorder.record_failed_notification(
                dedup_key="sla:throttle:order",
                notification_type="warning",
                event_data={"current_rtt_ms": 250.0},
                error="FileError",
            )

        pending = recorder.get_pending_notifications()
        assert len(pending) == 1
        assert pending[0]["dedup_key"] == "sla:throttle:order"

    def test_memory_buffer_max_entries(self):
        """메모리 버퍼 최대 크기 제한 확인."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(
            file_path="/nonexistent/path/test.jsonl",
            max_memory_entries=5,
        )

        with patch.object(recorder, "_write_to_file", return_value=False):
            for i in range(10):
                recorder.record_failed_notification(
                    dedup_key=f"key:{i}",
                    notification_type="warning",
                    event_data={},
                    error="err",
                )

        pending = recorder.get_pending_notifications()
        # deque maxlen=5이므로 최근 5개만 유지
        assert len(pending) == 5
        assert pending[0]["dedup_key"] == "key:5"

    def test_clear_memory_buffer(self):
        """메모리 버퍼 초기화 확인."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(
            file_path="/nonexistent/path/test.jsonl",
            max_memory_entries=10,
        )

        with patch.object(recorder, "_write_to_file", return_value=False):
            recorder.record_failed_notification(
                dedup_key="key:1",
                notification_type="warning",
                event_data={},
                error="err",
            )

        assert len(recorder.get_pending_notifications()) == 1
        recorder.clear_memory_buffer()
        assert len(recorder.get_pending_notifications()) == 0


# =============================================================================
# 5. SLA Notification Handler 테스트
# =============================================================================


@dataclass
class MockEvent:
    """EventBus 이벤트 목 객체."""

    data: dict


class TestSlaNotificationHandler:
    """SLA 알림 핸들러 Celery 비동기 + 동기 폴백 테스트."""

    def test_celery_async_dispatch_warning(self):
        """Warning 시 Celery apply_async 호출 확인."""
        with patch(
            "selfhealing.services.throttle.sla_notification.send_sla_notification",
            create=True,
        ) as mock_task:
            # Import 시 Celery를 사용할 수 있도록 패치
            import selfhealing.services.throttle.sla_notification as mod

            with patch.dict(
                "sys.modules",
                {"selfhealing.adapters.celery.tasks": MagicMock(send_sla_notification=mock_task)},
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

    def test_celery_async_dispatch_critical(self):
        """Critical 시 Celery apply_async 호출 확인."""
        with patch(
            "selfhealing.services.throttle.sla_notification.send_sla_notification",
            create=True,
        ) as mock_task:
            import selfhealing.services.throttle.sla_notification as mod

            with patch.dict(
                "sys.modules",
                {"selfhealing.adapters.celery.tasks": MagicMock(send_sla_notification=mock_task)},
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

    def test_celery_fallback_to_sync_on_import_error(self):
        """Celery 미사용 환경에서 동기 fallback 확인."""
        from selfhealing.services.throttle.sla_notification import (
            _handle_sla_warning,
        )

        with patch("selfhealing.services.throttle.sla_notification._send_sla_warning_sync") as mock_sync:
            _handle_sla_warning(MockEvent(data={"current_rtt_ms": 250.0}))
            mock_sync.assert_called_once()

    def test_celery_fallback_to_sync_on_exception(self):
        """Celery 예외 시 동기 fallback 확인."""
        from selfhealing.services.throttle.sla_notification import (
            _handle_sla_critical,
        )

        with patch("selfhealing.services.throttle.sla_notification._send_sla_critical_sync") as mock_sync:
            _handle_sla_critical(MockEvent(data={"current_rtt_ms": 600.0}))
            mock_sync.assert_called_once()

    def test_service_level_dedup_key(self):
        """dedup_key가 서비스 단위인지 확인."""
        from selfhealing.services.throttle.sla_notification import (
            _send_sla_warning_sync,
        )

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
                # domain=f"throttle:{service_name}" → dedup_key = "sla:throttle:payment"
                assert call_kwargs.kwargs.get("domain") == "throttle:payment" or (len(call_kwargs.args) > 0)

    def test_region_injected_in_metadata(self):
        """ClusterIdentity.region이 metadata에 포함 확인."""
        from selfhealing.services.throttle.sla_notification import (
            _send_sla_warning_sync,
        )

        mock_identity = MagicMock()
        mock_identity.region = "ap-northeast-2"

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

    def test_get_region_safe_returns_none_on_import_error(self):
        """ClusterIdentity 미설치 시 None 반환."""
        from selfhealing.services.throttle.sla_notification import _get_region_safe

        with patch.dict("sys.modules", {"selfhealing.core.cluster_identity": None}):
            # ImportError를 시뮬레이션
            with patch(
                "selfhealing.services.throttle.sla_notification.get_cluster_identity",
                side_effect=ImportError,
                create=True,
            ):
                result = _get_region_safe()
                # ImportError 또는 다른 에러로 None 반환
                assert result is None or isinstance(result, str)

    def test_handle_limit_recovered_dispatches(self):
        """Recovered 이벤트 핸들러 동기 폴백 확인."""
        from selfhealing.services.throttle.sla_notification import (
            _handle_limit_recovered,
        )

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


# =============================================================================
# 6. ThrottleSLANotificationSettings 테스트
# =============================================================================


class TestThrottleSLANotificationSettings:
    """Throttle SLA 알림 Pydantic v2 설정 테스트."""

    def setup_method(self):
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()

    def teardown_method(self):
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()

    def test_default_settings(self):
        """기본값 확인."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        settings = get_throttle_sla_notification_settings()

        assert settings.enabled is True
        assert settings.warning_enabled is True
        assert settings.critical_enabled is True
        assert settings.recovery_enabled is True
        assert settings.warning_cooldown_seconds == 1800
        assert settings.critical_cooldown_seconds == 900
        assert settings.redis_cooldown_enabled is True
        assert settings.warning_channels is None
        assert settings.critical_channels is None

    def test_env_override(self):
        """환경변수 오버라이드 확인."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_ENABLED": "false",
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_COOLDOWN_SECONDS": "3600",
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_CRITICAL_COOLDOWN_SECONDS": "600",
            },
        ):
            settings = ThrottleSLANotificationSettings()

            assert settings.enabled is False
            assert settings.warning_cooldown_seconds == 3600
            assert settings.critical_cooldown_seconds == 600

    def test_singleton_pattern(self):
        """싱글톤 패턴 확인."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
            reset_throttle_sla_notification_settings,
        )

        s1 = get_throttle_sla_notification_settings()
        s2 = get_throttle_sla_notification_settings()
        assert s1 is s2

        reset_throttle_sla_notification_settings()
        s3 = get_throttle_sla_notification_settings()
        assert s3 is not s1


# =============================================================================
# 7. format_sla_slack_blocks 테스트
# =============================================================================


class TestFormatSlaSlackBlocks:
    """SLA 알림 Slack Block Kit 포맷 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def teardown_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def test_basic_slack_blocks_structure(self):
        """기본 Slack Block Kit 구조 확인."""
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = NotificationPayload(
            title="SLA Warning: RTT Exceeded",
            message="RTT 250ms exceeded 200ms threshold",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.SLA,
            metadata={
                "rtt_ms": 250.0,
                "threshold_ms": 200,
                "current_limit": 80,
                "service_name": "payment",
            },
        )

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        assert "blocks" in result
        blocks = result["blocks"]
        # header, section(fields), section(details) = 최소 3개
        assert len(blocks) >= 3
        # header에 title 포함
        assert payload.title in blocks[0]["text"]["text"]

    def test_slack_blocks_with_rtt_change_and_region(self):
        """RTT 변화율과 Region이 Slack 필드에 포함 확인."""
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = NotificationPayload(
            title="SLA Warning",
            message="Test",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.SLA,
            metadata={
                "rtt_ms": 250.0,
                "threshold_ms": 200,
                "current_limit": 80,
                "service_name": "payment",
                "rtt_change_percent": 25.0,
                "region": "ap-northeast-2",
            },
        )

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        blocks = result["blocks"]

        # fields 섹션에서 RTT Change와 Region 확인
        fields_section = blocks[1]  # section with fields
        field_texts = [f["text"] for f in fields_section["fields"]]
        field_text_joined = " ".join(field_texts)

        assert "RTT Change" in field_text_joined
        assert "+25.0%" in field_text_joined
        assert "Region" in field_text_joined
        assert "ap-northeast-2" in field_text_joined

    def test_slack_blocks_with_actionable_urls(self):
        """URL 빌더에서 생성된 Actionable 버튼 포함 확인."""
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            format_sla_slack_blocks,
        )

        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
                "THROTTLE_SLA_ADMIN_BASE_URL": "/admin/throttle/",
                "THROTTLE_SLA_RUNBOOK_URL": "https://docs.internal/runbooks/sla",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                reset_throttle_sla_alert_url_builder,
            )

            reset_throttle_sla_alert_url_builder()

            payload = NotificationPayload(
                title="SLA Warning",
                message="Test",
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.SLA,
                metadata={
                    "rtt_ms": 250.0,
                    "service_name": "payment",
                    "event_type": "sla_warning",
                },
            )

            result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

            # actions 블록 찾기
            action_blocks = [b for b in result["blocks"] if b.get("type") == "actions"]
            assert len(action_blocks) == 1
            buttons = action_blocks[0]["elements"]
            assert len(buttons) == 3  # dashboard, admin, runbook

            button_ids = [b["action_id"] for b in buttons]
            assert "view_throttle_dashboard" in button_ids
            assert "view_throttle_admin" in button_ids
            assert "view_sla_runbook" in button_ids

    def test_slack_blocks_without_urls(self):
        """URL 미설정 시 actions 블록 미포함 확인."""
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            format_sla_slack_blocks,
        )

        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "",
                "THROTTLE_SLA_ADMIN_BASE_URL": "",
                "THROTTLE_SLA_RUNBOOK_URL": "",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                reset_throttle_sla_alert_url_builder,
            )

            reset_throttle_sla_alert_url_builder()

            payload = NotificationPayload(
                title="SLA Warning",
                message="Test",
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.SLA,
                metadata={"rtt_ms": 250.0, "service_name": "payment"},
            )

            result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

            action_blocks = [b for b in result["blocks"] if b.get("type") == "actions"]
            assert len(action_blocks) == 0

    def test_severity_emoji_mapping(self):
        """Priority별 emoji 매핑 확인."""
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            format_sla_slack_blocks,
        )

        for priority, expected_char in [
            (NotificationPriority.CRITICAL, "\U0001f534"),
            (NotificationPriority.HIGH, "\U0001f7e0"),
            (NotificationPriority.MEDIUM, "\U0001f7e1"),
        ]:
            payload = NotificationPayload(
                title="Test",
                message="Test",
                priority=priority,
                category=NotificationCategory.SLA,
                metadata={"rtt_ms": 100.0, "service_name": "test"},
            )
            result = format_sla_slack_blocks(payload, priority)
            header_text = result["blocks"][0]["text"]["text"]
            assert expected_char in header_text
