"""
Region Failover 테스트.

테스트 대상:
- FailoverState: 페일오버 상태 Enum
- FailoverEvent: 페일오버 이벤트 데이터클래스
- RegionFailover: 리전 페일오버 관리자
"""

from datetime import datetime, timezone

import pytest

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    reset_multiregion_settings,
)
from selfhealing.multiregion.failover import (
    FailoverEvent,
    FailoverState,
    RegionFailover,
)


class TestFailoverState:
    """FailoverState Enum 테스트."""

    def test_values(self) -> None:
        """Enum 값 확인."""
        assert FailoverState.NORMAL.value == "normal"
        assert FailoverState.DETECTING.value == "detecting"
        assert FailoverState.FAILOVER_IN_PROGRESS.value == "failover_in_progress"
        assert FailoverState.FAILED_OVER.value == "failed_over"
        assert FailoverState.RECOVERING.value == "recovering"


class TestFailoverEvent:
    """FailoverEvent 데이터클래스 테스트."""

    def test_create_event(self) -> None:
        """이벤트 생성."""
        now = datetime.now(timezone.utc)
        event = FailoverEvent(
            from_region="ap-northeast-2",
            to_region="us-east-1",
            timestamp=now,
            reason="health_check_failed",
            state=FailoverState.FAILED_OVER,
        )

        assert event.from_region == "ap-northeast-2"
        assert event.to_region == "us-east-1"
        assert event.reason == "health_check_failed"
        assert event.state == FailoverState.FAILED_OVER

    def test_with_details(self) -> None:
        """상세 정보 포함."""
        event = FailoverEvent(
            from_region="ap-northeast-2",
            to_region="us-east-1",
            timestamp=datetime.now(timezone.utc),
            reason="manual",
            state=FailoverState.FAILOVER_IN_PROGRESS,
            details={"initiated_by": "admin"},
        )

        assert event.details["initiated_by"] == "admin"


class TestRegionFailover:
    """RegionFailover 클래스 테스트."""

    def setup_method(self) -> None:
        """테스트 전 설정 리셋."""
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        """테스트 후 설정 리셋."""
        reset_multiregion_settings()

    def test_init(self) -> None:
        """초기화."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        assert failover._settings.current_region == "ap-northeast-2"
        assert failover.get_state() == FailoverState.NORMAL

    def test_get_state(self) -> None:
        """상태 조회."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        assert failover.get_state() == FailoverState.NORMAL

    def test_get_current_primary(self) -> None:
        """현재 Primary 조회."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        assert failover.get_current_primary() == "ap-northeast-2"

    def test_start_stop(self) -> None:
        """시작/중지."""
        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            enabled=True,
            failover_enabled=True,
        )
        failover = RegionFailover(settings=settings)

        assert failover._running is False

        failover.start()
        assert failover._running is True

        failover.stop()
        assert failover._running is False

    def test_on_failover_callback(self) -> None:
        """페일오버 콜백."""
        callback_events: list[FailoverEvent] = []

        def on_failover(event: FailoverEvent) -> None:
            callback_events.append(event)

        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings, on_failover=on_failover)

        # 콜백이 등록됨
        assert failover._on_failover is not None
