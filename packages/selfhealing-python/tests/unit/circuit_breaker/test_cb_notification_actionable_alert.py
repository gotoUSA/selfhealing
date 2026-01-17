"""
Actionable Alert Tests.

Tests for:
1. ActionableAlertUrlBuilder - URL 생성 로직
2. CB 알림 핸들러의 Actionable URL 포함 확인
3. Slack Block Kit 포맷터 테스트
4. 환경변수 설정에 따른 동작 확인

Actionable Alert: 신중한 보수주의
"""

import os
import pytest
from unittest.mock import MagicMock, patch


class TestActionableAlertUrlBuilder:
    """ActionableAlertUrlBuilder 클래스 테스트."""
    
    def setup_method(self):
        """테스트 전 환경변수 초기화 및 싱글톤 리셋."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            reset_actionable_alert_url_builder,
        )
        reset_actionable_alert_url_builder()
        # 환경변수 백업
        self._env_backup = {
            "CB_DASHBOARD_URL": os.environ.get("CB_DASHBOARD_URL"),
            "CB_ADMIN_BASE_URL": os.environ.get("CB_ADMIN_BASE_URL"),
            "CB_RUNBOOK_URL": os.environ.get("CB_RUNBOOK_URL"),
        }
    
    def teardown_method(self):
        """테스트 후 환경변수 복원 및 싱글톤 리셋."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            reset_actionable_alert_url_builder,
        )
        reset_actionable_alert_url_builder()
        # 환경변수 복원
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    
    def test_builder_exists(self):
        """ActionableAlertUrlBuilder 클래스가 존재하는지 확인."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            ActionableAlertUrlBuilder,
            get_actionable_alert_url_builder,
        )
        
        builder = get_actionable_alert_url_builder()
        assert isinstance(builder, ActionableAlertUrlBuilder)
    
    def test_build_cb_open_urls_with_all_env_vars(self):
        """모든 환경변수가 설정된 경우 URL 생성 테스트."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            get_actionable_alert_url_builder,
            reset_actionable_alert_url_builder,
        )
        
        # 환경변수 설정
        os.environ["CB_DASHBOARD_URL"] = "https://grafana.internal/d/circuit-breaker"
        os.environ["CB_ADMIN_BASE_URL"] = "/admin/selfhealing/circuitbreaker/"
        os.environ["CB_RUNBOOK_URL"] = "https://docs.internal/runbooks/circuit-breaker-recovery"
        reset_actionable_alert_url_builder()
        
        builder = get_actionable_alert_url_builder()
        urls = builder.build_cb_open_urls(
            service_name="payment_service",
            trigger_time="2026-01-06T10:00:00Z",
        )
        
        # Dashboard URL 확인
        assert urls.dashboard_url is not None
        assert "payment_service" in urls.dashboard_url
        assert "grafana.internal" in urls.dashboard_url
        
        # Admin URL 확인
        assert urls.admin_url is not None
        assert "service_id=payment_service" in urls.admin_url
        assert "action=review" in urls.admin_url
        assert "trigger_time=2026-01-06T10" in urls.admin_url
        
        # Runbook URL 확인
        assert urls.runbook_url is not None
        assert "circuit-breaker-recovery" in urls.runbook_url
    
    def test_build_cb_open_urls_without_env_vars(self):
        """환경변수가 없는 경우 None 반환 테스트."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            get_actionable_alert_url_builder,
            reset_actionable_alert_url_builder,
        )
        
        # 환경변수 제거
        os.environ.pop("CB_DASHBOARD_URL", None)
        os.environ.pop("CB_ADMIN_BASE_URL", None)
        os.environ.pop("CB_RUNBOOK_URL", None)
        reset_actionable_alert_url_builder()
        
        builder = get_actionable_alert_url_builder()
        urls = builder.build_cb_open_urls(service_name="test_service")
        
        assert urls.dashboard_url is None
        assert urls.admin_url is None
        assert urls.runbook_url is None
        assert not urls.has_any_url()
    
    def test_dashboard_url_with_query_param_separator(self):
        """대시보드 URL에 이미 ?가 있는 경우 & 사용 확인."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            get_actionable_alert_url_builder,
            reset_actionable_alert_url_builder,
        )
        
        os.environ["CB_DASHBOARD_URL"] = "https://grafana.internal/d/cb?orgId=1"
        reset_actionable_alert_url_builder()
        
        builder = get_actionable_alert_url_builder()
        urls = builder.build_cb_open_urls(service_name="order_service")
        
        assert urls.dashboard_url is not None
        assert "orgId=1&service=order_service" in urls.dashboard_url
    
    def test_admin_url_query_params(self):
        """Admin URL 쿼리 파라미터 형식 확인."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            get_actionable_alert_url_builder,
            reset_actionable_alert_url_builder,
        )
        
        os.environ["CB_ADMIN_BASE_URL"] = "/admin/selfhealing/circuitbreaker"
        reset_actionable_alert_url_builder()
        
        builder = get_actionable_alert_url_builder()
        urls = builder.build_cb_open_urls(
            service_name="inventory_service",
            trigger_time="2026-01-06T12:00:00Z",
        )
        
        assert urls.admin_url is not None
        # URL 인코딩된 형식 확인
        assert "service_id=inventory_service" in urls.admin_url
        assert "action=review" in urls.admin_url
    
    def test_build_cb_closed_urls(self):
        """CB CLOSED 이벤트용 URL 생성 테스트."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            get_actionable_alert_url_builder,
            reset_actionable_alert_url_builder,
        )
        
        os.environ["CB_DASHBOARD_URL"] = "https://grafana.internal/d/cb"
        os.environ["CB_ADMIN_BASE_URL"] = "/admin/selfhealing/circuitbreaker/"
        os.environ["CB_RUNBOOK_URL"] = "https://docs.internal/runbooks/cb"
        reset_actionable_alert_url_builder()
        
        builder = get_actionable_alert_url_builder()
        urls = builder.build_cb_closed_urls(
            service_name="user_service",
            recovery_time="2026-01-06T14:00:00Z",
        )
        
        assert urls.dashboard_url is not None
        assert urls.admin_url is not None
        assert "action=history" in urls.admin_url
        # 복구 시에는 Runbook 불필요
        assert urls.runbook_url is None
    
    def test_build_governance_blocked_urls(self):
        """Governance Blocked 이벤트용 URL 생성 테스트."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            get_actionable_alert_url_builder,
            reset_actionable_alert_url_builder,
        )
        
        os.environ["CB_DASHBOARD_URL"] = "https://grafana.internal/d/cb"
        os.environ["CB_ADMIN_BASE_URL"] = "/admin/selfhealing/circuitbreaker/"
        os.environ["CB_RUNBOOK_URL"] = "https://docs.internal/runbooks/cb"
        reset_actionable_alert_url_builder()
        
        builder = get_actionable_alert_url_builder()
        urls = builder.build_governance_blocked_urls(
            service_name="blocked_service",
            reason="blast_radius_exceeded",
        )
        
        assert urls.dashboard_url is not None
        assert urls.admin_url is not None
        assert "action=governance_review" in urls.admin_url
        # Governance 섹션으로 이동
        assert urls.runbook_url is not None
        assert "#governance" in urls.runbook_url
    
    def test_is_configured(self):
        """환경변수 설정 상태 확인 테스트."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            get_actionable_alert_url_builder,
            reset_actionable_alert_url_builder,
        )
        
        # 모두 비활성화
        os.environ.pop("CB_DASHBOARD_URL", None)
        os.environ.pop("CB_ADMIN_BASE_URL", None)
        os.environ.pop("CB_RUNBOOK_URL", None)
        reset_actionable_alert_url_builder()
        
        builder = get_actionable_alert_url_builder()
        assert not builder.is_configured()
        
        # 하나만 설정
        os.environ["CB_DASHBOARD_URL"] = "https://grafana.internal"
        reset_actionable_alert_url_builder()
        
        builder = get_actionable_alert_url_builder()
        assert builder.is_configured()
    
    def test_get_config_status(self):
        """설정 상태 딕셔너리 반환 테스트."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            get_actionable_alert_url_builder,
            reset_actionable_alert_url_builder,
        )
        
        os.environ["CB_DASHBOARD_URL"] = "https://grafana.internal"
        os.environ.pop("CB_ADMIN_BASE_URL", None)
        os.environ["CB_RUNBOOK_URL"] = "https://docs.internal"
        reset_actionable_alert_url_builder()
        
        builder = get_actionable_alert_url_builder()
        status = builder.get_config_status()
        
        assert status["dashboard_configured"] is True
        assert status["admin_configured"] is False
        assert status["runbook_configured"] is True


class TestActionableUrls:
    """ActionableUrls 데이터클래스 테스트."""
    
    def test_to_dict(self):
        """to_dict 메서드 테스트."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import ActionableUrls
        
        urls = ActionableUrls(
            dashboard_url="https://dashboard.test",
            admin_url="/admin/test",
            runbook_url="https://runbook.test",
        )
        
        result = urls.to_dict()
        
        assert result["dashboard_url"] == "https://dashboard.test"
        assert result["admin_url"] == "/admin/test"
        assert result["runbook_url"] == "https://runbook.test"
    
    def test_has_any_url_true(self):
        """has_any_url이 True인 경우 테스트."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import ActionableUrls
        
        urls = ActionableUrls(dashboard_url="https://test.com")
        assert urls.has_any_url() is True
    
    def test_has_any_url_false(self):
        """has_any_url이 False인 경우 테스트."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import ActionableUrls
        
        urls = ActionableUrls()
        assert urls.has_any_url() is False


class TestCBNotificationHandlerActionableUrls:
    """CB 알림 핸들러의 Actionable URL 포함 테스트."""
    
    def setup_method(self):
        """테스트 전 환경변수 초기화."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            reset_actionable_alert_url_builder,
        )
        from selfhealing.services.event_bus import get_event_bus
        
        reset_actionable_alert_url_builder()
        self.bus = get_event_bus()
        self.bus.reset()
        
        self._env_backup = {
            "CB_DASHBOARD_URL": os.environ.get("CB_DASHBOARD_URL"),
            "CB_ADMIN_BASE_URL": os.environ.get("CB_ADMIN_BASE_URL"),
            "CB_RUNBOOK_URL": os.environ.get("CB_RUNBOOK_URL"),
        }
    
    def teardown_method(self):
        """테스트 후 환경변수 복원."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            reset_actionable_alert_url_builder,
        )
        
        reset_actionable_alert_url_builder()
        self.bus.reset()
        
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    
    @patch("selfhealing.services.unified_notification.get_unified_notification_manager")
    def test_notification_includes_actionable_urls(self, mock_get_manager):
        """알림 metadata에 Actionable URL들이 포함되는지 확인."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            reset_actionable_alert_url_builder,
        )
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )
        
        # 환경변수 설정
        os.environ["CB_DASHBOARD_URL"] = "https://grafana.test/d/cb"
        os.environ["CB_ADMIN_BASE_URL"] = "/admin/cb/"
        os.environ["CB_RUNBOOK_URL"] = "https://docs.test/runbook"
        reset_actionable_alert_url_builder()
        
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={
                "service_name": "payment_service",
                "timestamp": "2026-01-06T10:00:00Z",
            },
            source="test",
        )
        
        _on_circuit_breaker_opened_notify(event)
        
        call_args = mock_get_manager.return_value.notify.call_args
        payload = call_args[0][0]
        metadata = payload.metadata
        
        # Actionable URL들이 포함되어 있는지 확인
        assert "dashboard_url" in metadata
        assert "admin_url" in metadata
        assert "runbook_url" in metadata
        
        assert "grafana.test" in metadata["dashboard_url"]
        assert "service_id=payment_service" in metadata["admin_url"]
        assert "action=review" in metadata["admin_url"]
        assert "docs.test/runbook" in metadata["runbook_url"]
    
    @patch("selfhealing.services.unified_notification.get_unified_notification_manager")
    def test_notification_handles_missing_env_vars(self, mock_get_manager):
        """환경변수가 없어도 알림이 정상 발송되는지 확인."""
        from selfhealing.services.circuit_breaker.actionable_alert_urls import (
            reset_actionable_alert_url_builder,
        )
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )
        
        # 환경변수 제거
        os.environ.pop("CB_DASHBOARD_URL", None)
        os.environ.pop("CB_ADMIN_BASE_URL", None)
        os.environ.pop("CB_RUNBOOK_URL", None)
        reset_actionable_alert_url_builder()
        
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={"service_name": "test_service"},
            source="test",
        )
        
        _on_circuit_breaker_opened_notify(event)
        
        # 알림이 정상 발송됨
        mock_manager.notify.assert_called_once()
        
        call_args = mock_manager.notify.call_args
        payload = call_args[0][0]
        metadata = payload.metadata
        
        # URL들은 None이지만 키는 존재
        assert metadata["dashboard_url"] is None
        assert metadata["admin_url"] is None
        assert metadata["runbook_url"] is None


class TestSlackBlockKitFormatter:
    """Slack Block Kit 포맷터 테스트."""
    
    def test_format_cb_slack_blocks_exists(self):
        """format_cb_slack_blocks 함수가 존재하는지 확인."""
        from selfhealing.services.unified_notification import format_cb_slack_blocks
        
        assert callable(format_cb_slack_blocks)
    
    def test_format_cb_slack_blocks_structure(self):
        """Slack Block Kit 메시지 구조 확인."""
        from selfhealing.services.unified_notification import (
            format_cb_slack_blocks,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        
        payload = NotificationPayload(
            title="🔴 Circuit Breaker OPEN: payment_service",
            message="서비스 'payment_service'의 Circuit Breaker가 열렸습니다.",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.CIRCUIT_BREAKER,
            source="circuit_breaker_service",
            metadata={
                "service_name": "payment_service",
                "trace_url": "https://jaeger.test/trace/abc123",
                "trigger_time": "2026-01-06T10:00:00Z",
                "dashboard_url": "https://grafana.test/d/cb?service=payment_service",
                "admin_url": "/admin/cb/?service_id=payment_service&action=review",
                "runbook_url": "https://docs.test/runbook",
            },
        )
        
        result = format_cb_slack_blocks(payload, NotificationPriority.HIGH)
        
        assert "blocks" in result
        blocks = result["blocks"]
        
        # Header 블록 확인
        header_block = blocks[0]
        assert header_block["type"] == "header"
        assert "OPEN" in header_block["text"]["text"]
        
        # Actions 블록 (버튼) 확인
        action_blocks = [b for b in blocks if b["type"] == "actions"]
        assert len(action_blocks) == 1
        
        action_elements = action_blocks[0]["elements"]
        button_texts = [e["text"]["text"] for e in action_elements]
        
        assert "📊 Dashboard" in button_texts
        assert "⚙️ Admin Panel" in button_texts
        assert "📖 Runbook" in button_texts
    
    def test_format_cb_slack_blocks_without_urls(self):
        """Actionable URL이 없는 경우 버튼이 생성되지 않는지 확인."""
        from selfhealing.services.unified_notification import (
            format_cb_slack_blocks,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        
        payload = NotificationPayload(
            title="🔴 Circuit Breaker OPEN: test_service",
            message="Test message",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.CIRCUIT_BREAKER,
            source="test",
            metadata={
                "service_name": "test_service",
                "dashboard_url": None,
                "admin_url": None,
                "runbook_url": None,
            },
        )
        
        result = format_cb_slack_blocks(payload, NotificationPriority.HIGH)
        
        blocks = result["blocks"]
        action_blocks = [b for b in blocks if b["type"] == "actions"]
        
        # Actions 블록이 없어야 함
        assert len(action_blocks) == 0
    
    def test_format_cb_slack_blocks_with_trace_url(self):
        """Trace URL이 있을 때 Jaeger 링크가 포함되는지 확인."""
        from selfhealing.services.unified_notification import (
            format_cb_slack_blocks,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        
        payload = NotificationPayload(
            title="Test",
            message="Test",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.CIRCUIT_BREAKER,
            source="test",
            metadata={
                "service_name": "test",
                "trace_url": "https://jaeger.test/trace/xyz789",
            },
        )
        
        result = format_cb_slack_blocks(payload, NotificationPriority.HIGH)
        
        blocks = result["blocks"]
        # Trace 링크가 포함된 section 블록 찾기
        trace_blocks = [
            b for b in blocks
            if b["type"] == "section" and 
            "text" in b and 
            "mrkdwn" in str(b.get("text", {}).get("type", "")) and
            "jaeger" in str(b.get("text", {}).get("text", "")).lower()
        ]
        
        assert len(trace_blocks) == 1
        assert "View in Jaeger" in trace_blocks[0]["text"]["text"]
    
    def test_format_cb_slack_blocks_priority_emoji(self):
        """우선순위별 이모지 매핑 확인."""
        from selfhealing.services.unified_notification import (
            format_cb_slack_blocks,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        
        test_cases = [
            (NotificationPriority.CRITICAL, "🔴"),
            (NotificationPriority.HIGH, "🟠"),
            (NotificationPriority.MEDIUM, "🟡"),
            (NotificationPriority.LOW, "🔵"),
            (NotificationPriority.INFO, "⚪"),
        ]
        
        for priority, expected_emoji in test_cases:
            payload = NotificationPayload(
                title="Test",
                message="Test",
                priority=priority,
                category=NotificationCategory.CIRCUIT_BREAKER,
                source="test",
                metadata={"service_name": "test"},
            )
            
            result = format_cb_slack_blocks(payload, priority)
            header_text = result["blocks"][0]["text"]["text"]
            
            assert expected_emoji in header_text, f"Expected {expected_emoji} for {priority}"
    
    def test_admin_button_has_primary_style(self):
        """Admin Panel 버튼에 primary 스타일이 적용되는지 확인."""
        from selfhealing.services.unified_notification import (
            format_cb_slack_blocks,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        
        payload = NotificationPayload(
            title="Test",
            message="Test",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.CIRCUIT_BREAKER,
            source="test",
            metadata={
                "service_name": "test",
                "admin_url": "/admin/test/",
            },
        )
        
        result = format_cb_slack_blocks(payload, NotificationPriority.HIGH)
        
        action_blocks = [b for b in result["blocks"] if b["type"] == "actions"]
        admin_buttons = [
            e for e in action_blocks[0]["elements"]
            if e.get("action_id") == "view_admin"
        ]
        
        assert len(admin_buttons) == 1
        assert admin_buttons[0].get("style") == "primary"


class TestFormatCBNotificationWithActions:
    """format_cb_notification_with_actions 함수 테스트."""
    
    def test_function_exists(self):
        """format_cb_notification_with_actions 함수가 존재하는지 확인."""
        from selfhealing.services.unified_notification import format_cb_notification_with_actions
        
        assert callable(format_cb_notification_with_actions)
    
    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_emergency_level_escalation_applied(self, mock_manager):
        """Emergency Level에 따른 우선순위 에스컬레이션이 적용되는지 확인."""
        from selfhealing.services.unified_notification import (
            format_cb_notification_with_actions,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        
        mock_em = MagicMock()
        mock_em.get_current_level.return_value = 3  # Level 3
        mock_manager.return_value = mock_em
        
        payload = NotificationPayload(
            title="Test",
            message="Test",
            priority=NotificationPriority.MEDIUM,  # MEDIUM → HIGH로 에스컬레이션
            category=NotificationCategory.CIRCUIT_BREAKER,
            source="test",
            metadata={"service_name": "test"},
        )
        
        result = format_cb_notification_with_actions(payload)
        
        # Header에 HIGH 우선순위 이모지 (🟠)가 있어야 함
        header_text = result["blocks"][0]["text"]["text"]
        assert "🟠" in header_text  # HIGH 이모지
    
    def test_handles_import_error_gracefully(self):
        """EmergencyModeManager import 실패 시 graceful 처리 확인."""
        from selfhealing.services.unified_notification import (
            format_cb_notification_with_actions,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        
        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager",
            side_effect=ImportError("Test import error"),
        ):
            payload = NotificationPayload(
                title="Test",
                message="Test",
                priority=NotificationPriority.MEDIUM,
                category=NotificationCategory.CIRCUIT_BREAKER,
                source="test",
                metadata={"service_name": "test"},
            )
            
            # 예외 없이 정상 동작해야 함
            result = format_cb_notification_with_actions(payload)
            
            assert "blocks" in result
            # 원래 MEDIUM 우선순위 이모지 (🟡)가 사용됨
            header_text = result["blocks"][0]["text"]["text"]
            assert "🟡" in header_text
