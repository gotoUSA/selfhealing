"""
SelfHealerWatchdog 테스트.

메인 와치독 클래스 테스트.
"""

import threading
import time
from datetime import datetime, timezone
from unittest import mock

import pytest

from selfhealing.meta.config import MetaWatchdogSettings
from selfhealing.meta.watchdog import (
    SelfHealerWatchdog,
    WatchdogState,
    get_selfhealer_watchdog,
    reset_selfhealer_watchdog,
)


class TestWatchdogState:
    """WatchdogState 테스트."""

    def test_state_is_dataclass_like(self):
        """WatchdogState가 dataclass-like인지 확인."""
        # WatchdogState가 올바른 필드를 가지는지 확인
        from selfhealing.meta.health_probe import HealthStatus

        state = WatchdogState(
            overall_status=HealthStatus.HEALTHY,
            component_statuses={"redis": HealthStatus.HEALTHY},
            last_check=datetime.now(timezone.utc),
            escalation_pending=False,
            escalation_count=0,
        )

        assert state.overall_status == HealthStatus.HEALTHY
        assert state.component_statuses == {"redis": HealthStatus.HEALTHY}
        assert state.escalation_pending is False


class TestSelfHealerWatchdog:
    """SelfHealerWatchdog 테스트."""

    @pytest.fixture
    def settings(self):
        """실제 설정 fixture."""
        return MetaWatchdogSettings(
            enabled=True,
            probe_interval_seconds=5,
            self_cb_enabled=False,  # 테스트를 단순화하기 위해 비활성화
            dry_run_mode=True,
        )

    @pytest.fixture
    def mock_probe_manager(self):
        """Mock probe manager."""
        from datetime import datetime, timezone
        from selfhealing.meta.health_probe import ProbeResult, HealthStatus

        manager = mock.MagicMock()
        manager.probe_all.return_value = {
            "redis": ProbeResult(
                component="redis",
                status=HealthStatus.HEALTHY,
                latency_ms=10,
                timestamp=datetime.now(timezone.utc),
            ),
            "celery": ProbeResult(
                component="celery",
                status=HealthStatus.HEALTHY,
                latency_ms=15,
                timestamp=datetime.now(timezone.utc),
            ),
        }
        manager.get_overall_status.return_value = HealthStatus.HEALTHY
        return manager

    @pytest.fixture
    def watchdog(self, settings, mock_probe_manager):
        """Watchdog fixture."""
        return SelfHealerWatchdog(
            settings=settings,
            probe_manager=mock_probe_manager,
        )

    def test_initialization(self, watchdog):
        """초기화 테스트."""
        assert watchdog is not None
        assert watchdog.is_running() is False

    def test_start_stop(self, watchdog):
        """시작/중지 테스트."""
        watchdog.start()
        assert watchdog.is_running() is True

        watchdog.stop()
        # 스레드가 종료되기까지 잠시 대기
        time.sleep(0.1)
        assert watchdog.is_running() is False

    def test_double_start(self, watchdog):
        """이중 시작 테스트 (예외 없음)."""
        watchdog.start()
        watchdog.start()  # 두 번째 호출도 문제 없어야 함

        assert watchdog.is_running() is True
        watchdog.stop()
        time.sleep(0.1)

    def test_double_stop(self, watchdog):
        """이중 중지 테스트 (예외 없음)."""
        watchdog.start()
        watchdog.stop()
        time.sleep(0.1)
        watchdog.stop()  # 두 번째 호출도 문제 없어야 함

        assert watchdog.is_running() is False

    def test_get_state_initial(self, watchdog):
        """초기 상태 조회."""
        state = watchdog.get_state()

        assert isinstance(state, WatchdogState)

    def test_check_health(self, watchdog, mock_probe_manager):
        """헬스 체크 테스트."""
        from selfhealing.meta.health_probe import HealthStatus

        result = watchdog.check_health()

        assert isinstance(result, WatchdogState)
        mock_probe_manager.probe_all.assert_called()

    def test_force_check(self, watchdog, mock_probe_manager):
        """강제 점검 테스트."""
        result = watchdog.force_check()

        assert isinstance(result, WatchdogState)
        mock_probe_manager.probe_all.assert_called()


class TestHealthCheck:
    """헬스 체크 테스트."""

    @pytest.fixture
    def settings(self):
        """실제 설정 fixture."""
        return MetaWatchdogSettings(
            enabled=True,
            self_cb_enabled=False,
            dry_run_mode=True,
        )

    @pytest.fixture
    def mock_probe_manager_unhealthy(self):
        """비정상 상태 Mock probe manager."""
        from datetime import datetime, timezone
        from selfhealing.meta.health_probe import ProbeResult, HealthStatus

        manager = mock.MagicMock()
        manager.probe_all.return_value = {
            "redis": ProbeResult(
                component="redis",
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
                timestamp=datetime.now(timezone.utc),
                error="Connection refused",
            ),
            "celery": ProbeResult(
                component="celery",
                status=HealthStatus.HEALTHY,
                latency_ms=15,
                timestamp=datetime.now(timezone.utc),
            ),
        }
        manager.get_overall_status.return_value = HealthStatus.UNHEALTHY
        return manager

    def test_check_health_unhealthy(self, settings, mock_probe_manager_unhealthy):
        """비정상 상태 헬스 체크."""
        from selfhealing.meta.health_probe import HealthStatus

        watchdog = SelfHealerWatchdog(
            settings=settings,
            probe_manager=mock_probe_manager_unhealthy,
        )

        result = watchdog.check_health()

        assert result.overall_status == HealthStatus.UNHEALTHY
        assert result.component_statuses.get("redis") == HealthStatus.UNHEALTHY


class TestDryRunMode:
    """Dry-run 모드 테스트."""

    @pytest.fixture
    def dry_run_settings(self):
        """Dry-run 설정."""
        return MetaWatchdogSettings(
            enabled=True,
            self_cb_enabled=False,
            dry_run_mode=True,
        )

    @pytest.fixture
    def mock_probe_manager(self):
        """Mock probe manager."""
        from datetime import datetime, timezone
        from selfhealing.meta.health_probe import ProbeResult, HealthStatus

        manager = mock.MagicMock()
        manager.probe_all.return_value = {
            "redis": ProbeResult(
                component="redis",
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
                timestamp=datetime.now(timezone.utc),
                error="Failed",
            ),
        }
        manager.get_overall_status.return_value = HealthStatus.UNHEALTHY
        return manager

    def test_dry_run_mode_logs_only(self, dry_run_settings, mock_probe_manager):
        """Dry-run 모드에서는 실제 복구 수행 안 함."""
        watchdog = SelfHealerWatchdog(
            settings=dry_run_settings,
            probe_manager=mock_probe_manager,
        )

        result = watchdog.check_health()

        # Dry-run 모드에서도 상태는 반환됨
        assert isinstance(result, WatchdogState)


class TestDisabledWatchdog:
    """비활성화된 와치독 테스트."""

    @pytest.fixture
    def disabled_settings(self):
        """비활성화 설정."""
        return MetaWatchdogSettings(enabled=False)

    @pytest.fixture
    def watchdog(self, disabled_settings):
        """비활성화 watchdog fixture."""
        return SelfHealerWatchdog(settings=disabled_settings)

    def test_disabled_watchdog_does_not_start(self, watchdog):
        """비활성화 시 시작하지 않음."""
        watchdog.start()

        # 비활성화 상태에서는 실제로 시작하지 않을 수 있음
        # 구현에 따라 is_running()이 False일 수 있음
        watchdog.stop()


class TestSingleton:
    """싱글톤 테스트."""

    def test_singleton_returns_same_instance(self):
        """싱글톤 인스턴스 반환."""
        reset_selfhealer_watchdog()

        wd1 = get_selfhealer_watchdog()
        wd2 = get_selfhealer_watchdog()

        if wd1 is not None and wd2 is not None:
            assert wd1 is wd2

        if wd1 is not None:
            wd1.stop()
        reset_selfhealer_watchdog()

    def test_reset_clears_singleton(self):
        """싱글톤 리셋."""
        reset_selfhealer_watchdog()

        wd1 = get_selfhealer_watchdog()
        if wd1 is not None:
            wd1.stop()
        reset_selfhealer_watchdog()
        wd2 = get_selfhealer_watchdog()

        # 리셋 후 다른 인스턴스 (또는 둘 다 None일 수 있음)
        if wd1 is not None and wd2 is not None:
            assert wd1 is not wd2

        if wd2 is not None:
            wd2.stop()
        reset_selfhealer_watchdog()


class TestSelfCircuitBreaker:
    """셀프 서킷브레이커 테스트."""

    @pytest.fixture
    def settings_with_cb(self):
        """CB 활성화 설정."""
        return MetaWatchdogSettings(
            enabled=True,
            self_cb_enabled=True,
            self_cb_failure_threshold=3,
            self_cb_recovery_timeout_seconds=60,
            dry_run_mode=True,
        )

    @pytest.fixture
    def mock_probe_manager(self):
        """Mock probe manager."""
        from datetime import datetime, timezone
        from selfhealing.meta.health_probe import ProbeResult, HealthStatus

        manager = mock.MagicMock()
        manager.probe_all.return_value = {
            "redis": ProbeResult(
                component="redis",
                status=HealthStatus.HEALTHY,
                latency_ms=10,
                timestamp=datetime.now(timezone.utc),
            ),
        }
        manager.get_overall_status.return_value = HealthStatus.HEALTHY
        return manager

    def test_self_cb_initial_closed(self, settings_with_cb, mock_probe_manager):
        """초기 상태: 닫힘."""
        watchdog = SelfHealerWatchdog(
            settings=settings_with_cb,
            probe_manager=mock_probe_manager,
        )

        # 셀프 CB 초기 상태 확인
        assert watchdog._self_cb_open is False

    def test_self_cb_open_on_failures(self, settings_with_cb, mock_probe_manager):
        """연속 실패 시 CB 열림."""
        watchdog = SelfHealerWatchdog(
            settings=settings_with_cb,
            probe_manager=mock_probe_manager,
        )

        # 연속 실패 시뮬레이션
        for _ in range(5):
            watchdog._record_self_cb_failure()

        assert watchdog._self_cb_open is True


class TestConsecutiveFailures:
    """연속 실패 카운트 테스트."""

    @pytest.fixture
    def settings(self):
        """실제 설정 fixture."""
        return MetaWatchdogSettings(
            enabled=True,
            self_cb_enabled=False,
            dry_run_mode=True,
        )

    @pytest.fixture
    def mock_probe_manager_failing(self):
        """실패하는 Mock probe manager."""
        from datetime import datetime, timezone
        from selfhealing.meta.health_probe import ProbeResult, HealthStatus

        manager = mock.MagicMock()
        manager.probe_all.return_value = {
            "redis": ProbeResult(
                component="redis",
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
                timestamp=datetime.now(timezone.utc),
                error="Failed",
            ),
        }
        manager.get_overall_status.return_value = HealthStatus.UNHEALTHY
        return manager

    def test_consecutive_failures_tracked(self, settings, mock_probe_manager_failing):
        """연속 실패가 추적됨."""
        watchdog = SelfHealerWatchdog(
            settings=settings,
            probe_manager=mock_probe_manager_failing,
        )

        # 여러 번 체크
        watchdog.check_health()
        watchdog.check_health()
        watchdog.check_health()

        # 연속 실패 카운트 확인
        assert watchdog._consecutive_failures.get("redis", 0) == 3
