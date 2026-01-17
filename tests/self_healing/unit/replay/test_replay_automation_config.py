"""
Phase 1 Tests - DLQ Replay Automation.

Tests for:
1. ReplayAutomationConfig dataclass
2. RuntimeConfigManager replay_automation support
3. EventBus _on_circuit_breaker_closed handler with Track 1
4. ReplayAutomationConfigSerializer validation
5. ReplayAutomationConfigView API

Reference: docs/self_healing/middleware_system/19_DLQ_AUTOMATION_BLUEPRINT.md
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# =============================================================================
# ReplayAutomationConfig Dataclass Tests
# =============================================================================


class TestReplayAutomationConfig:
    """ReplayAutomationConfig dataclass 테스트."""

    def test_default_values(self):
        """기본값이 올바르게 설정되는지 확인."""
        from selfhealing.core.config import ReplayAutomationConfig

        config = ReplayAutomationConfig()

        # Track 1 defaults
        assert config.track1_enabled is True
        assert config.track1_max_items == 50

        # Track 2 defaults
        assert config.track2_enabled is True
        assert config.track2_max_items == 50

        # Track 3 defaults (disabled by default)
        assert config.track3_enabled is False
        assert config.track3_max_items == 30

        # Adaptive defaults
        assert config.adaptive_enabled is False
        assert config.adaptive_min_items == 10
        assert config.adaptive_max_items == 100
        assert config.adaptive_failure_threshold == 0.2

    def test_custom_values(self):
        """커스텀 값 설정 테스트."""
        from selfhealing.core.config import ReplayAutomationConfig

        config = ReplayAutomationConfig(
            track1_enabled=False,
            track1_max_items=100,
            adaptive_enabled=True,
        )

        assert config.track1_enabled is False
        assert config.track1_max_items == 100
        assert config.adaptive_enabled is True

    def test_asdict_serialization(self):
        """config를 dict로 변환 가능 확인 (Pydantic model_dump)."""
        from selfhealing.core.config import ReplayAutomationConfig

        config = ReplayAutomationConfig()
        config_dict = config.model_dump()

        assert isinstance(config_dict, dict)
        assert "track1_enabled" in config_dict
        assert "track2_enabled" in config_dict
        assert "track3_enabled" in config_dict
        assert "adaptive_enabled" in config_dict


# =============================================================================
# RuntimeConfigManager Integration Tests
# =============================================================================


class TestRuntimeConfigManagerReplayAutomation:
    """RuntimeConfigManager의 replay_automation 설정 테스트."""

    def test_storage_key_registered(self):
        """STORAGE_KEYS에 replay_automation이 등록되어 있는지 확인."""
        from selfhealing.services.runtime_config.constants import STORAGE_KEYS

        assert "replay_automation" in STORAGE_KEYS
        assert STORAGE_KEYS["replay_automation"] == "runtime_config:replay_automation"

    def test_config_class_registered(self):
        """CONFIG_CLASSES에 ReplayAutomationConfig가 등록되어 있는지 확인."""
        from selfhealing.services.runtime_config.constants import CONFIG_CLASSES
        from selfhealing.core.config import ReplayAutomationConfig

        assert "replay_automation" in CONFIG_CLASSES
        assert CONFIG_CLASSES["replay_automation"] is ReplayAutomationConfig

    def test_default_config_structure(self):
        """기본 설정 구조 확인."""
        from selfhealing.core.config import ReplayAutomationConfig
        
        config = ReplayAutomationConfig()
        config_dict = config.model_dump()
        
        # 필수 필드 확인
        assert "track1_enabled" in config_dict
        assert "track1_max_items" in config_dict
        assert "track2_enabled" in config_dict
        assert "track3_enabled" in config_dict
        assert "adaptive_enabled" in config_dict


# =============================================================================
# EventBus Handler Tests
# =============================================================================


class TestCircuitBreakerClosedHandler:
    """CB CLOSED 이벤트 핸들러 테스트."""

    def setup_method(self):
        """테스트 전 이벤트 버스 리셋."""
        from selfhealing.services.event_bus import get_event_bus

        self.bus = get_event_bus()
        self.bus.reset()

    def teardown_method(self):
        """테스트 후 이벤트 버스 리셋."""
        self.bus.reset()

    @patch("selfhealing.services.runtime_config.get_runtime_config_manager")
    @patch("selfhealing.adapters.celery.tasks.conditional_replay_on_circuit_close")
    def test_track1_enabled_triggers_replay(self, mock_task, mock_config_manager):
        """Track 1 활성화 시 replay 태스크가 트리거되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed,
            SelfHealingEvent,
            EventType,
        )

        # RuntimeConfig mock - Track 1 활성화
        mock_manager = MagicMock()
        mock_manager._get_config.return_value = {
            "track1_enabled": True,
            "track1_max_items": 75,
        }
        mock_config_manager.return_value = mock_manager

        # Task mock
        mock_task.delay = MagicMock()

        # 이벤트 생성 및 핸들러 호출
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "payment-api"},
            source="test",
        )
        _on_circuit_breaker_closed(event)

        # 검증: delay가 호출되었는지
        mock_task.delay.assert_called_once_with(
            service_name="payment-api",
            max_items=75,
        )

    @patch("selfhealing.services.runtime_config.get_runtime_config_manager")
    @patch("selfhealing.adapters.celery.tasks.conditional_replay_on_circuit_close")
    def test_track1_disabled_skips_replay(self, mock_task, mock_config_manager):
        """Track 1 비활성화 시 replay가 스킵되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed,
            SelfHealingEvent,
            EventType,
        )

        # RuntimeConfig mock - Track 1 비활성화
        mock_manager = MagicMock()
        mock_manager._get_config.return_value = {
            "track1_enabled": False,
            "track1_max_items": 50,
        }
        mock_config_manager.return_value = mock_manager

        # Task mock
        mock_task.delay = MagicMock()

        # 이벤트 생성 및 핸들러 호출
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "payment-api"},
            source="test",
        )
        _on_circuit_breaker_closed(event)

        # 검증: delay가 호출되지 않음
        mock_task.delay.assert_not_called()

    def test_handler_does_not_crash_on_import_error(self):
        """Celery 태스크 import 실패 시에도 핸들러가 정상 동작하는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_closed,
            SelfHealingEvent,
            EventType,
        )

        # 이벤트 생성 및 핸들러 호출 - 예외 없이 완료되어야 함
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "payment-api"},
            source="test",
        )

        # 예외가 발생하지 않아야 함
        try:
            _on_circuit_breaker_closed(event)
        except Exception as e:
            pytest.fail(f"Handler raised exception: {e}")


# =============================================================================
# Serializer Tests
# =============================================================================


class TestReplayAutomationConfigSerializer:
    """ReplayAutomationConfigSerializer 테스트."""

    def test_valid_data(self):
        """유효한 데이터 검증."""
        from selfhealing.api.django.serializers.config import (
            ReplayAutomationConfigSerializer,
        )

        data = {
            "track1_enabled": True,
            "track1_max_items": 100,
            "track3_enabled": True,
            "adaptive_enabled": True,
        }

        serializer = ReplayAutomationConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_invalid_max_items_too_high(self):
        """max_items가 너무 높을 때 검증 실패."""
        from selfhealing.api.django.serializers.config import (
            ReplayAutomationConfigSerializer,
        )

        data = {
            "track1_max_items": 1000,  # 500 초과
        }

        serializer = ReplayAutomationConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "track1_max_items" in serializer.errors

    def test_invalid_adaptive_threshold(self):
        """adaptive_failure_threshold 범위 검증."""
        from selfhealing.api.django.serializers.config import (
            ReplayAutomationConfigSerializer,
        )

        data = {
            "adaptive_failure_threshold": 0.8,  # 0.5 초과
        }

        serializer = ReplayAutomationConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "adaptive_failure_threshold" in serializer.errors

    def test_adaptive_min_max_validation(self):
        """adaptive_min <= adaptive_max 검증."""
        from selfhealing.api.django.serializers.config import (
            ReplayAutomationConfigSerializer,
        )

        data = {
            "adaptive_min_items": 100,
            "adaptive_max_items": 50,  # min > max
        }

        serializer = ReplayAutomationConfigSerializer(data=data)
        assert not serializer.is_valid()

    def test_get_config_changes(self):
        """apply 옵션 제외한 config 변경사항 추출."""
        from selfhealing.api.django.serializers.config import (
            ReplayAutomationConfigSerializer,
        )

        data = {
            "track1_enabled": False,
            "track1_max_items": 75,
            "apply_strategy": "immediate",
            "reason": "test change",
        }

        serializer = ReplayAutomationConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

        config_changes = serializer.get_config_changes()
        assert "track1_enabled" in config_changes
        assert "track1_max_items" in config_changes
        assert "apply_strategy" not in config_changes
        assert "reason" not in config_changes


# =============================================================================
# View Tests
# =============================================================================


class TestReplayAutomationConfigView:
    """ReplayAutomationConfigView API 테스트."""

    def test_view_exists(self):
        """View 클래스가 존재하는지 확인."""
        from selfhealing.api.django.views.config import ReplayAutomationConfigView

        assert ReplayAutomationConfigView is not None
        assert ReplayAutomationConfigView.config_name == "replay_automation"

    def test_view_has_correct_serializer(self):
        """올바른 serializer가 설정되어 있는지 확인."""
        from selfhealing.api.django.views.config import ReplayAutomationConfigView
        from selfhealing.api.django.serializers.config import (
            ReplayAutomationConfigSerializer,
        )

        assert (
            ReplayAutomationConfigView.serializer_class is ReplayAutomationConfigSerializer
        )


# =============================================================================
# URL Tests
# =============================================================================


class TestReplayAutomationURL:
    """URL 라우팅 테스트."""

    def test_url_registered(self):
        """URL이 올바르게 등록되어 있는지 확인."""
        from django.urls import reverse, NoReverseMatch

        try:
            url = reverse("selfhealing:config-replay-automation")
            assert "config/replay-automation" in url
        except NoReverseMatch:
            # URL resolver가 설정되지 않은 환경에서는 직접 패턴 확인
            from selfhealing.api.django.urls import urlpatterns

            url_names = [
                getattr(p, "name", None) for p in urlpatterns if hasattr(p, "name")
            ]
            assert "config-replay-automation" in url_names
