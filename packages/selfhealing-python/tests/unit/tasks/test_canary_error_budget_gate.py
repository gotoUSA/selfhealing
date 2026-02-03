"""
Canary Error Budget Gate 단위 테스트.

172_CANARY_ERROR_BUDGET_GATE.md 구현 검증.

Tests:
    - auto_promote_eligible 거버넌스 체크
    - Zombie 판정 제외 로직
    - pause() 시그니처 확장
    - resume_paused_rollouts Whitelist 필터링
    - promote() 거버넌스 체크
    - Break Glass 동작
    - Redis 하위 호환성
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


class TestWatchdogResult:
    """WatchdogResult 확장 테스트."""

    def test_watchdog_result_has_governance_fields(self):
        """WatchdogResult에 governance 필드가 있는지 확인."""
        from selfhealing.tasks.canary_watchdog import WatchdogResult

        result = WatchdogResult()

        assert hasattr(result, "governance_blocked")
        assert hasattr(result, "governance_block_reason")
        assert result.governance_blocked is False
        assert result.governance_block_reason == ""

    def test_watchdog_result_to_dict_includes_governance_fields(self):
        """to_dict()에 governance 필드가 포함되는지 확인."""
        from selfhealing.tasks.canary_watchdog import WatchdogResult

        result = WatchdogResult(
            governance_blocked=True,
            governance_block_reason="Error budget critically low",
        )

        result_dict = result.to_dict()

        assert result_dict["governance_blocked"] is True
        assert result_dict["governance_block_reason"] == "Error budget critically low"


class TestCanaryRolloutModelExtensions:
    """CanaryRollout 모델 확장 테스트."""

    def test_canary_rollout_has_pause_fields(self):
        """CanaryRollout에 pause 관련 필드가 있는지 확인."""
        from selfhealing.services.canary.models import CanaryRollout

        rollout = CanaryRollout(
            id="test-123",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
        )

        assert hasattr(rollout, "pause_reason")
        assert hasattr(rollout, "pause_triggered_by")
        assert hasattr(rollout, "paused_at")
        assert rollout.pause_reason is None
        assert rollout.pause_triggered_by is None
        assert rollout.paused_at is None

    def test_pause_trigger_priority_enum_exists(self):
        """PauseTriggerPriority Enum이 존재하는지 확인."""
        from selfhealing.services.canary.models import PauseTriggerPriority

        assert PauseTriggerPriority.METRICS == 100
        assert PauseTriggerPriority.ERROR_BUDGET == 80
        assert PauseTriggerPriority.MANUAL == 10

    def test_trigger_priority_map_exists(self):
        """TRIGGER_PRIORITY_MAP이 존재하고 올바른 값인지 확인."""
        from selfhealing.services.canary.models import TRIGGER_PRIORITY_MAP

        assert "error_budget" in TRIGGER_PRIORITY_MAP
        assert "governance" in TRIGGER_PRIORITY_MAP
        assert "manual" in TRIGGER_PRIORITY_MAP
        assert TRIGGER_PRIORITY_MAP["error_budget"] == 80

    def test_zombie_exempt_triggers_exists(self):
        """ZOMBIE_EXEMPT_TRIGGERS가 존재하는지 확인."""
        from selfhealing.services.canary.models import ZOMBIE_EXEMPT_TRIGGERS

        assert "error_budget" in ZOMBIE_EXEMPT_TRIGGERS
        assert "governance" in ZOMBIE_EXEMPT_TRIGGERS
        assert "manual" not in ZOMBIE_EXEMPT_TRIGGERS


class TestAutoPromoteGovernance:
    """auto_promote_eligible 거버넌스 체크 테스트."""

    @pytest.fixture
    def watchdog(self):
        """Watchdog fixture."""
        from selfhealing.tasks.canary_watchdog import RolloutWatchdog, WatchdogConfig

        config = WatchdogConfig(enable_auto_promote=True)
        watchdog = RolloutWatchdog(config)
        watchdog._service = MagicMock()
        return watchdog

    def test_blocked_by_error_budget(self, watchdog):
        """에러 예산 부족 시 자동 프로모션 차단."""
        from selfhealing.services.governance_checks import (
            BlockReason,
            GovernanceCheckResult,
        )

        blocked_result = GovernanceCheckResult(
            allowed=False,
            block_reason=BlockReason.ERROR_BUDGET,
            block_message="Error budget critically low (5.0%)",
        )

        with patch("selfhealing.services.governance_checks.check_all_governance") as mock_gov:
            mock_gov.return_value = blocked_result

            result = watchdog.auto_promote_eligible()

            assert result.governance_blocked is True
            assert "error budget" in result.governance_block_reason.lower()
            assert result.promote_count == 0

    def test_blocked_by_emergency_mode(self, watchdog):
        """비상 모드 시 자동 프로모션 차단."""
        from selfhealing.services.governance_checks import (
            BlockReason,
            GovernanceCheckResult,
        )

        blocked_result = GovernanceCheckResult(
            allowed=False,
            block_reason=BlockReason.EMERGENCY_MODE,
            block_message="Emergency mode LEVEL_2 is active",
        )

        with patch("selfhealing.services.governance_checks.check_all_governance") as mock_gov:
            mock_gov.return_value = blocked_result

            result = watchdog.auto_promote_eligible()

            assert result.governance_blocked is True
            assert "emergency" in result.governance_block_reason.lower()

    def test_blocked_by_kill_switch(self, watchdog):
        """Kill Switch 활성화 시 자동 프로모션 차단."""
        from selfhealing.services.governance_checks import (
            BlockReason,
            GovernanceCheckResult,
        )

        blocked_result = GovernanceCheckResult(
            allowed=False,
            block_reason=BlockReason.KILL_SWITCH,
            block_message="Kill Switch is active",
        )

        with patch("selfhealing.services.governance_checks.check_all_governance") as mock_gov:
            mock_gov.return_value = blocked_result

            result = watchdog.auto_promote_eligible()

            assert result.governance_blocked is True
            assert "kill switch" in result.governance_block_reason.lower()

    def test_allowed_when_governance_passes(self, watchdog):
        """거버넌스 통과 시 정상 진행."""
        from selfhealing.services.governance_checks import GovernanceCheckResult

        allowed_result = GovernanceCheckResult.allowed_result()

        with patch("selfhealing.services.governance_checks.check_all_governance") as mock_gov:
            mock_gov.return_value = allowed_result
            watchdog.service.get_active_rollouts.return_value = []

            result = watchdog.auto_promote_eligible()

            assert result.governance_blocked is False
            mock_gov.assert_called_once()

    def test_fail_closed_on_governance_error(self, watchdog):
        """거버넌스 체크 실패 시 Fail-Closed."""
        with patch("selfhealing.services.governance_checks.check_all_governance") as mock_gov:
            mock_gov.side_effect = Exception("Redis connection failed")

            result = watchdog.auto_promote_eligible()

            # Fail-Closed: 에러 시 차단
            assert result.governance_blocked is True
            assert "error" in result.governance_block_reason.lower()


class TestZombieExemption:
    """Zombie 판정 제외 테스트."""

    @pytest.fixture
    def watchdog(self):
        """Watchdog fixture."""
        from selfhealing.tasks.canary_watchdog import RolloutWatchdog, WatchdogConfig

        config = WatchdogConfig(zombie_threshold_minutes=30)
        return RolloutWatchdog(config)

    def test_error_budget_paused_not_zombie(self, watchdog):
        """error_budget 사유로 PAUSED된 롤아웃은 Zombie 아님."""
        from selfhealing.services.canary.models import CanaryRollout, CanaryState

        rollout = CanaryRollout(
            id="test-123",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.PAUSED,
            pause_triggered_by="error_budget",
            created_at=datetime.utcnow() - timedelta(minutes=60),  # 60분 경과
        )

        now = datetime.utcnow()
        result = watchdog._check_zombie(rollout, now)

        assert result is None  # Zombie 아님

    def test_governance_paused_not_zombie(self, watchdog):
        """governance 사유로 PAUSED된 롤아웃은 Zombie 아님."""
        from selfhealing.services.canary.models import CanaryRollout, CanaryState

        rollout = CanaryRollout(
            id="test-456",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.PAUSED,
            pause_triggered_by="governance",
            created_at=datetime.utcnow() - timedelta(minutes=60),
        )

        now = datetime.utcnow()
        result = watchdog._check_zombie(rollout, now)

        assert result is None  # Zombie 아님

    def test_manual_paused_is_zombie(self, watchdog):
        """manual 사유로 PAUSED된 롤아웃은 Zombie 맞음."""
        from selfhealing.services.canary.models import CanaryRollout, CanaryState

        rollout = CanaryRollout(
            id="test-789",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.PAUSED,
            pause_triggered_by="manual",
            created_at=datetime.utcnow() - timedelta(minutes=60),
        )

        now = datetime.utcnow()
        result = watchdog._check_zombie(rollout, now)

        assert result is not None  # Zombie 맞음
        assert "Paused for" in result.reason

    def test_metrics_paused_is_zombie(self, watchdog):
        """metrics 사유로 PAUSED된 롤아웃은 Zombie 맞음."""
        from selfhealing.services.canary.models import CanaryRollout, CanaryState

        rollout = CanaryRollout(
            id="test-abc",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.PAUSED,
            pause_triggered_by="metrics",
            created_at=datetime.utcnow() - timedelta(minutes=60),
        )

        now = datetime.utcnow()
        result = watchdog._check_zombie(rollout, now)

        assert result is not None  # Zombie 맞음

    def test_no_trigger_paused_is_zombie(self, watchdog):
        """pause_triggered_by가 없는 PAUSED 롤아웃은 Zombie 맞음."""
        from selfhealing.services.canary.models import CanaryRollout, CanaryState

        rollout = CanaryRollout(
            id="test-old",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.PAUSED,
            # pause_triggered_by 없음 (구버전 데이터)
            created_at=datetime.utcnow() - timedelta(minutes=60),
        )

        now = datetime.utcnow()
        result = watchdog._check_zombie(rollout, now)

        assert result is not None  # Zombie 맞음


class TestBreakGlass:
    """Break Glass (비상 탈출구) 테스트."""

    def test_break_glass_settings_exist(self):
        """GovernanceSettings에 Break Glass 설정이 있는지 확인."""
        from selfhealing.settings.governance import GovernanceSettings

        settings = GovernanceSettings()

        assert hasattr(settings, "break_glass_enabled")
        assert hasattr(settings, "break_glass_audit_required")
        assert settings.break_glass_enabled is False
        assert settings.break_glass_audit_required is True

    def test_break_glass_bypasses_all_checks(self):
        """Break Glass 활성화 시 모든 체크 우회."""
        from selfhealing.services.governance_checks import check_all_governance

        with patch("selfhealing.settings.governance.get_governance_settings") as mock_settings:
            mock_settings.return_value.break_glass_enabled = True
            mock_settings.return_value.break_glass_audit_required = True

            with patch("selfhealing.services.governance_checks._log_governance_blocked"):
                result = check_all_governance(
                    check_kill_switch=True,
                    check_emergency=True,
                    check_error_budget=True,
                    operation_name="test_operation",
                )

                assert result.allowed is True


class TestCanaryGovernanceSettings:
    """CanaryGovernanceSettings 테스트."""

    def test_settings_exist(self):
        """CanaryGovernanceSettings가 존재하는지 확인."""
        from selfhealing.settings.canary_governance import (
            CanaryGovernanceSettings,
            get_canary_governance_settings,
        )

        settings = get_canary_governance_settings()

        assert hasattr(settings, "zombie_exempt_triggers")
        assert hasattr(settings, "resume_whitelist_triggers")
        assert hasattr(settings, "governance_check_on_manual_promote")
        assert hasattr(settings, "pause_trigger_priority")

    def test_default_values(self):
        """기본값이 올바른지 확인."""
        from selfhealing.settings.canary_governance import CanaryGovernanceSettings

        settings = CanaryGovernanceSettings()

        assert "error_budget" in settings.zombie_exempt_triggers
        assert "governance" in settings.zombie_exempt_triggers
        assert "error_budget" in settings.resume_whitelist_triggers
        assert settings.governance_check_on_manual_promote is True
        assert settings.resume_max_batch_size == 5
        assert settings.resume_interval_seconds == 60


class TestCanaryAuditActionsExtension:
    """CANARY_ACTIONS 확장 테스트."""

    def test_governance_actions_exist(self):
        """governance 관련 액션이 CANARY_ACTIONS에 있는지 확인."""
        from selfhealing.services.canary.audit import CANARY_ACTIONS

        assert "governance_blocked" in CANARY_ACTIONS
        assert "governance_bypass" in CANARY_ACTIONS


class TestRedisBackwardCompatibility:
    """Redis 데이터 하위 호환성 테스트."""

    def test_deserialize_without_pause_fields(self):
        """구버전 데이터 (pause 필드 없음) 역직렬화."""
        from selfhealing.services.canary.service import CanaryRolloutService

        old_data = {
            "id": "test-123",
            "config_type": "circuit_breaker",
            "previous_values": {},
            "new_values": {},
            "state": "paused",
            "current_stage_index": 0,
            "stages": [
                {
                    "name": "canary",
                    "clusters": ["test-cluster"],
                    "percentage": 10.0,
                }
            ],
            "created_by": "admin",
            "created_at": "2026-02-04T10:00:00",
            "reason": "Test",
            # pause_reason, pause_triggered_by, paused_at 없음
        }

        service = CanaryRolloutService()
        rollout = service._deserialize_rollout(old_data)

        assert rollout.pause_reason is None
        assert rollout.pause_triggered_by is None
        assert rollout.paused_at is None

    def test_deserialize_with_pause_fields(self):
        """신버전 데이터 (pause 필드 있음) 역직렬화."""
        from selfhealing.services.canary.service import CanaryRolloutService

        new_data = {
            "id": "test-456",
            "config_type": "circuit_breaker",
            "previous_values": {},
            "new_values": {},
            "state": "paused",
            "current_stage_index": 0,
            "stages": [
                {
                    "name": "canary",
                    "clusters": ["test-cluster"],
                    "percentage": 10.0,
                }
            ],
            "created_by": "admin",
            "created_at": "2026-02-04T10:00:00",
            "reason": "Test",
            "pause_reason": "Error budget low",
            "pause_triggered_by": "error_budget",
            "paused_at": "2026-02-04T10:30:00",
        }

        service = CanaryRolloutService()
        rollout = service._deserialize_rollout(new_data)

        assert rollout.pause_reason == "Error budget low"
        assert rollout.pause_triggered_by == "error_budget"
        assert rollout.paused_at is not None

    def test_serialize_includes_pause_fields(self):
        """직렬화 시 pause 필드가 포함되는지 확인."""
        from datetime import datetime

        from selfhealing.services.canary.models import (
            CanaryRollout,
            CanaryStage,
            CanaryState,
        )
        from selfhealing.services.canary.service import CanaryRolloutService

        rollout = CanaryRollout(
            id="test-789",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.PAUSED,
            stages=[CanaryStage(name="canary", clusters=["test"], percentage=10.0)],
            created_by="admin",
            pause_reason="Error budget low",
            pause_triggered_by="error_budget",
            paused_at=datetime(2026, 2, 4, 10, 30, 0),
        )

        service = CanaryRolloutService()
        data = service._serialize_rollout(rollout)

        assert "pause_reason" in data
        assert "pause_triggered_by" in data
        assert "paused_at" in data
        assert data["pause_reason"] == "Error budget low"
        assert data["pause_triggered_by"] == "error_budget"


class TestPrometheusMetrics:
    """Prometheus 메트릭 테스트."""

    def test_canary_governance_metrics_exist(self):
        """Canary 거버넌스 메트릭이 존재하는지 확인."""
        from selfhealing.services.metrics.definitions import (
            canary_governance_blocked_total,
            canary_governance_bypass_total,
            canary_pending_promotion_gauge,
        )

        # 메트릭이 존재하는지 확인 (에러 없이 임포트)
        assert canary_governance_blocked_total is not None
        assert canary_pending_promotion_gauge is not None
        assert canary_governance_bypass_total is not None
