"""
Secondary 리전의 Primary 장애 감지 및 승격 테스트.

테스트 대상:
- _check_and_failover(): Primary/Secondary 분기 동작
- _check_primary_and_promote(): Secondary 승격 조건 검증
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    reset_multiregion_settings,
)
from selfhealing.multiregion.failover import (
    FailoverState,
    RegionFailover,
)
from selfhealing.multiregion.health_monitor import (
    RegionHealth,
    RegionHealthStatus,
)

# =============================================================================
# _check_and_failover() 동작 검증
# =============================================================================


class TestCheckAndFailoverBehavior:
    """_check_and_failover() Primary/Secondary 분기 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    def test_primary_monitors_peer_unreachable(self) -> None:
        """Primary 리전은 피어 리전의 UNREACHABLE 상태를 감시한다."""
        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            region_role="primary",
        )
        mock_monitor = MagicMock()
        mock_monitor.get_all_health_states.return_value = {
            "us-west-2": RegionHealth(
                region="us-west-2",
                status=RegionHealthStatus.UNREACHABLE,
                latency_ms=0,
                last_check=MagicMock(),
            ),
        }

        failover = RegionFailover(settings=settings, health_monitor=mock_monitor)
        # 에러 없이 실행되어야 함
        failover._check_and_failover()

        mock_monitor.get_all_health_states.assert_called_once()

    def test_secondary_calls_check_primary_and_promote(self) -> None:
        """Secondary 리전은 _check_primary_and_promote()를 호출한다."""
        settings = MultiRegionSettings(
            current_region="us-west-2",
            region_role="secondary",
        )
        mock_monitor = MagicMock()
        all_health = {
            "ap-northeast-2": RegionHealth(
                region="ap-northeast-2",
                status=RegionHealthStatus.HEALTHY,
                latency_ms=10,
                last_check=MagicMock(),
            ),
        }
        mock_monitor.get_all_health_states.return_value = all_health

        failover = RegionFailover(settings=settings, health_monitor=mock_monitor)

        with patch.object(failover, "_check_primary_and_promote") as mock_promote:
            failover._check_and_failover()
            mock_promote.assert_called_once_with(all_health)

    def test_primary_does_not_call_promote(self) -> None:
        """Primary 리전은 _check_primary_and_promote()를 호출하지 않는다."""
        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            region_role="primary",
        )
        mock_monitor = MagicMock()
        mock_monitor.get_all_health_states.return_value = {}

        failover = RegionFailover(settings=settings, health_monitor=mock_monitor)

        with patch.object(failover, "_check_primary_and_promote") as mock_promote:
            failover._check_and_failover()
            mock_promote.assert_not_called()


# =============================================================================
# _check_primary_and_promote() 동작 검증
# =============================================================================


class TestCheckPrimaryAndPromoteBehavior:
    """_check_primary_and_promote() 승격 조건 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    def _make_failover(
        self,
        quorum_witness: MagicMock | None = None,
        current_region: str = "us-west-2",
        current_primary: str = "ap-northeast-2",
    ) -> RegionFailover:
        settings = MultiRegionSettings(
            current_region=current_region,
            region_role="secondary",
            failover_enabled=True,
            failover_cooldown_seconds=30,
        )
        mock_monitor = MagicMock()
        failover = RegionFailover(
            settings=settings,
            health_monitor=mock_monitor,
            quorum_witness=quorum_witness,
        )
        failover._current_primary = current_primary
        return failover

    def _make_health(self, region: str, status: RegionHealthStatus) -> RegionHealth:
        return RegionHealth(
            region=region,
            status=status,
            latency_ms=0,
            last_check=MagicMock(),
        )

    def test_skips_when_primary_not_in_health(self) -> None:
        """Primary 리전이 health 맵에 없으면 무시한다."""
        failover = self._make_failover()
        failover._check_primary_and_promote({})
        # 아무 동작 없이 정상 종료
        assert failover.get_state() == FailoverState.NORMAL

    def test_skips_when_primary_healthy(self) -> None:
        """Primary 리전이 HEALTHY이면 승격을 시도하지 않는다."""
        failover = self._make_failover()
        all_health = {
            "ap-northeast-2": self._make_health("ap-northeast-2", RegionHealthStatus.HEALTHY),
        }
        failover._check_primary_and_promote(all_health)
        assert failover.get_state() == FailoverState.NORMAL

    def test_skips_when_primary_degraded(self) -> None:
        """Primary 리전이 DEGRADED이면 승격을 시도하지 않는다."""
        failover = self._make_failover()
        all_health = {
            "ap-northeast-2": self._make_health("ap-northeast-2", RegionHealthStatus.DEGRADED),
        }
        failover._check_primary_and_promote(all_health)
        assert failover.get_state() == FailoverState.NORMAL

    def test_skips_when_cooldown_active(self) -> None:
        """쿨다운 기간 중에는 승격을 시도하지 않는다."""
        settings = MultiRegionSettings(
            current_region="us-west-2",
            region_role="secondary",
            failover_enabled=True,
            failover_cooldown_seconds=3600,
        )
        mock_monitor = MagicMock()
        mock_quorum = MagicMock()
        failover = RegionFailover(
            settings=settings,
            health_monitor=mock_monitor,
            quorum_witness=mock_quorum,
        )
        failover._current_primary = "ap-northeast-2"
        # 마지막 페일오버 시간을 현재로 설정하여 쿨다운 활성화
        import time

        failover._last_failover_time = time.time()

        all_health = {
            "ap-northeast-2": self._make_health("ap-northeast-2", RegionHealthStatus.UNREACHABLE),
        }
        failover._check_primary_and_promote(all_health)
        mock_quorum.try_acquire_primary.assert_not_called()

    def test_skips_when_no_quorum_witness(self) -> None:
        """QuorumWitness가 설정되지 않으면 승격하지 않는다."""
        failover = self._make_failover(quorum_witness=None)
        all_health = {
            "ap-northeast-2": self._make_health("ap-northeast-2", RegionHealthStatus.UNREACHABLE),
        }
        failover._check_primary_and_promote(all_health)
        assert failover.get_state() == FailoverState.NORMAL

    def test_skips_when_quorum_denied(self) -> None:
        """QuorumWitness가 승격을 거부하면 승격하지 않는다."""
        mock_quorum = MagicMock()
        mock_quorum.try_acquire_primary.return_value = False

        failover = self._make_failover(quorum_witness=mock_quorum)
        all_health = {
            "ap-northeast-2": self._make_health("ap-northeast-2", RegionHealthStatus.UNREACHABLE),
        }
        failover._check_primary_and_promote(all_health)

        mock_quorum.try_acquire_primary.assert_called_once()
        assert failover.get_state() == FailoverState.NORMAL

    def test_executes_failover_on_all_conditions_met(self) -> None:
        """모든 조건이 충족되면 _execute_failover()를 호출한다."""
        mock_quorum = MagicMock()
        mock_quorum.try_acquire_primary.return_value = True

        failover = self._make_failover(quorum_witness=mock_quorum)
        all_health = {
            "ap-northeast-2": self._make_health("ap-northeast-2", RegionHealthStatus.UNREACHABLE),
        }

        with patch.object(failover, "_execute_failover") as mock_exec:
            mock_exec.return_value = True
            failover._check_primary_and_promote(all_health)

            mock_exec.assert_called_once_with(
                target_region="us-west-2",
                reason="primary_unreachable:ap-northeast-2",
            )

    def test_failover_reason_includes_primary_region(self) -> None:
        """페일오버 사유에 Primary 리전 이름이 포함된다."""
        mock_quorum = MagicMock()
        mock_quorum.try_acquire_primary.return_value = True

        failover = self._make_failover(
            quorum_witness=mock_quorum,
            current_primary="eu-west-1",
        )
        all_health = {
            "eu-west-1": self._make_health("eu-west-1", RegionHealthStatus.UNREACHABLE),
        }

        with patch.object(failover, "_execute_failover") as mock_exec:
            mock_exec.return_value = True
            failover._check_primary_and_promote(all_health)

            reason = mock_exec.call_args[1]["reason"]
            assert "eu-west-1" in reason
            assert "primary_unreachable" in reason

    def test_skips_when_failover_disabled(self) -> None:
        """failover_enabled=False이면 승격을 시도하지 않는다."""
        settings = MultiRegionSettings(
            current_region="us-west-2",
            region_role="secondary",
            failover_enabled=False,
        )
        mock_monitor = MagicMock()
        mock_quorum = MagicMock()
        failover = RegionFailover(
            settings=settings,
            health_monitor=mock_monitor,
            quorum_witness=mock_quorum,
        )
        failover._current_primary = "ap-northeast-2"

        all_health = {
            "ap-northeast-2": self._make_health("ap-northeast-2", RegionHealthStatus.UNREACHABLE),
        }
        failover._check_primary_and_promote(all_health)
        mock_quorum.try_acquire_primary.assert_not_called()
