"""
RegionHeartbeat / MultiRegionShutdownHandler 단위 테스트.

테스트 대상 (237 약점 2):
- RegionHeartbeat: TTL 기반 리전 생존 신호 (Layer 2)
- MultiRegionShutdownHandler: 정상 종료 즉시 통보 (Layer 1)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    reset_multiregion_settings,
)
from selfhealing.multiregion.heartbeat import (
    MultiRegionShutdownHandler,
    RegionHeartbeat,
)


# =============================================================================
# RegionHeartbeat 계약 검증 (Contract)
# =============================================================================


class TestRegionHeartbeatContract:
    """RegionHeartbeat 설계 계약값 검증."""

    def test_heartbeat_key_prefix(self) -> None:
        """HEARTBEAT_KEY_PREFIX 계약값: 'multiregion:heartbeat:'."""
        assert RegionHeartbeat.HEARTBEAT_KEY_PREFIX == "multiregion:heartbeat:"

    def test_heartbeat_ttl(self) -> None:
        """HEARTBEAT_TTL 계약값: 15초."""
        assert RegionHeartbeat.HEARTBEAT_TTL == 15

    def test_heartbeat_interval(self) -> None:
        """HEARTBEAT_INTERVAL 계약값: 5초 (TTL의 1/3)."""
        assert RegionHeartbeat.HEARTBEAT_INTERVAL == 5

    def test_interval_is_one_third_of_ttl(self) -> None:
        """HEARTBEAT_INTERVAL은 HEARTBEAT_TTL의 1/3이다."""
        assert RegionHeartbeat.HEARTBEAT_INTERVAL == RegionHeartbeat.HEARTBEAT_TTL / 3


# =============================================================================
# RegionHeartbeat 동작 검증 (Behavior)
# =============================================================================


class TestRegionHeartbeatBehavior:
    """RegionHeartbeat 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    @pytest.fixture
    def settings(self) -> MultiRegionSettings:
        return MultiRegionSettings(current_region="ap-northeast-2")

    def test_init(self, settings: MultiRegionSettings) -> None:
        """초기화 시 running은 False."""
        hb = RegionHeartbeat(settings)
        assert hb.is_running() is False
        assert hb._settings is settings

    def test_heartbeat_key(self, settings: MultiRegionSettings) -> None:
        """하트비트 키는 PREFIX + current_region 형식이다."""
        hb = RegionHeartbeat(settings)
        expected = f"{RegionHeartbeat.HEARTBEAT_KEY_PREFIX}{settings.current_region}"
        assert hb._heartbeat_key() == expected

    def test_heartbeat_key_different_region(self) -> None:
        """리전이 다르면 다른 키를 생성한다."""
        hb1 = RegionHeartbeat(MultiRegionSettings(current_region="us-east-1"))
        hb2 = RegionHeartbeat(MultiRegionSettings(current_region="eu-west-1"))
        assert hb1._heartbeat_key() != hb2._heartbeat_key()
        assert "us-east-1" in hb1._heartbeat_key()
        assert "eu-west-1" in hb2._heartbeat_key()

    @patch("selfhealing.multiregion.heartbeat.time.sleep", side_effect=InterruptedError)
    def test_start_stop(self, _mock_sleep: MagicMock, settings: MultiRegionSettings) -> None:
        """start() 후 running=True, stop() 후 running=False."""
        hb = RegionHeartbeat(settings)
        hb.start()
        assert hb.is_running() is True
        hb.stop()
        assert hb.is_running() is False

    def test_start_idempotent(self, settings: MultiRegionSettings) -> None:
        """이미 실행 중이면 start()는 아무 것도 하지 않는다."""
        hb = RegionHeartbeat(settings)
        hb._running = True
        hb.start()
        # 워커 스레드가 생성되지 않음
        assert hb._worker is None

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_beat_calls_state_backend(self, mock_get_backend: MagicMock, settings: MultiRegionSettings) -> None:
        """_beat()는 state_backend.set()을 HEARTBEAT_TTL로 호출한다."""
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        hb = RegionHeartbeat(settings)
        hb._beat()

        mock_backend.set.assert_called_once()
        call_args = mock_backend.set.call_args
        assert call_args[0][0] == hb._heartbeat_key()
        # value에는 region과 ts가 포함
        value = call_args[0][1]
        assert value["region"] == settings.current_region
        assert "ts" in value
        # TTL 검증
        assert call_args[1]["ttl_seconds"] == RegionHeartbeat.HEARTBEAT_TTL

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_beat_failure_does_not_raise(self, mock_get_backend: MagicMock, settings: MultiRegionSettings) -> None:
        """_beat() 실패 시 예외를 발생시키지 않는다 (Fail-open)."""
        mock_get_backend.side_effect = Exception("Redis down")

        hb = RegionHeartbeat(settings)
        # 예외 없이 완료되어야 함
        hb._beat()


# =============================================================================
# MultiRegionShutdownHandler 동작 검증 (Behavior)
# =============================================================================


class TestMultiRegionShutdownHandlerBehavior:
    """MultiRegionShutdownHandler 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    @pytest.fixture
    def settings(self) -> MultiRegionSettings:
        return MultiRegionSettings(current_region="ap-northeast-2")

    def test_init(self, settings: MultiRegionSettings) -> None:
        """초기화."""
        handler = MultiRegionShutdownHandler(settings)
        assert handler._settings is settings

    @patch("selfhealing.services.event_bus.redis_bus.get_event_bus")
    def test_on_shutdown_start_publishes_event(self, mock_get_bus: MagicMock, settings: MultiRegionSettings) -> None:
        """on_shutdown_start()는 REGION_INSTANCE_STOPPING 이벤트를 발행한다."""
        mock_bus = MagicMock()
        mock_get_bus.return_value = mock_bus

        handler = MultiRegionShutdownHandler(settings)
        handler.on_shutdown_start()

        mock_bus.publish.assert_called_once()
        event = mock_bus.publish.call_args[0][0]
        from selfhealing.services.event_bus.bus import EventType

        assert event.event_type == EventType.REGION_INSTANCE_STOPPING
        assert event.data["region"] == settings.current_region
        assert event.data["reason"] == "graceful_shutdown"
        assert "timestamp" in event.data
        assert event.source == "shutdown_coordinator"

    @patch("selfhealing.services.event_bus.redis_bus.get_event_bus")
    def test_on_shutdown_start_failure_does_not_raise(self, mock_get_bus: MagicMock, settings: MultiRegionSettings) -> None:
        """on_shutdown_start() 실패 시 예외를 발생시키지 않는다."""
        mock_get_bus.side_effect = Exception("Redis down")

        handler = MultiRegionShutdownHandler(settings)
        # 예외 없이 완료
        handler.on_shutdown_start()

    def test_on_drain_complete_is_noop(self, settings: MultiRegionSettings) -> None:
        """on_drain_complete()는 아무 것도 하지 않는다."""
        handler = MultiRegionShutdownHandler(settings)
        # 예외 없이 완료
        handler.on_drain_complete()

    def test_on_force_shutdown_is_noop(self, settings: MultiRegionSettings) -> None:
        """on_force_shutdown()는 아무 것도 하지 않는다."""
        handler = MultiRegionShutdownHandler(settings)
        # 예외 없이 완료
        handler.on_force_shutdown([])

    def test_implements_shutdown_handler_interface(self, settings: MultiRegionSettings) -> None:
        """ShutdownHandler ABC의 3개 메서드를 모두 구현한다."""
        handler = MultiRegionShutdownHandler(settings)
        assert hasattr(handler, "on_shutdown_start")
        assert hasattr(handler, "on_drain_complete")
        assert hasattr(handler, "on_force_shutdown")
        assert callable(handler.on_shutdown_start)
        assert callable(handler.on_drain_complete)
        assert callable(handler.on_force_shutdown)
