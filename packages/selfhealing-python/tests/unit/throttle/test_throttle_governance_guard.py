"""
ThrottleGovernanceGuard 단위 테스트.

테스트 대상: selfhealing.resilience.policies.guards.governance.ThrottleGovernanceGuard

검증 범위:
- name 속성 계약값
- check() Kill Switch → Emergency → Error Budget 순서
- Break Glass 활성 시 체크 우회
- 각 모듈 import 실패 시 Fail-Open
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.interfaces.resilience_policy import PolicyContext
from selfhealing.resilience.policies.guards.governance import (
    ThrottleGovernanceGuard,
)


# =============================================================================
# name 계약 검증
# =============================================================================


class TestThrottleGovernanceGuardNameContract:
    """ThrottleGovernanceGuard.name 계약값 검증."""

    def test_name_is_throttle_governance(self):
        """name은 'throttle_governance'여야 한다."""
        guard = ThrottleGovernanceGuard()
        assert guard.name == "throttle_governance"


# =============================================================================
# check() 통과 동작 검증
# =============================================================================


class TestThrottleGovernanceGuardPassBehavior:
    """모든 체크 통과 동작 검증."""

    def test_all_checks_pass_returns_allowed(self):
        """Kill Switch, Emergency, ErrorBudget 모두 정상이면 allowed=True."""
        guard = ThrottleGovernanceGuard()

        with (
            patch.object(guard, "_is_break_glass_active", return_value=False),
            patch.object(guard, "_check_kill_switch", return_value=MagicMock(allowed=True)),
            patch.object(guard, "_check_emergency_level", return_value=MagicMock(allowed=True)),
            patch.object(guard, "_check_error_budget", return_value=MagicMock(allowed=True)),
        ):
            result = guard.check()
            assert result.allowed is True


# =============================================================================
# Break Glass 우회 동작 검증
# =============================================================================


class TestThrottleGovernanceGuardBreakGlassBehavior:
    """Break Glass 활성 시 체크 우회 동작 검증."""

    def test_break_glass_active_bypasses_all_checks(self):
        """Break Glass 활성 시 다른 체크 없이 즉시 allowed=True."""
        guard = ThrottleGovernanceGuard()

        with (
            patch.object(guard, "_is_break_glass_active", return_value=True),
            patch.object(guard, "_check_kill_switch") as mock_ks,
            patch.object(guard, "_check_emergency_level") as mock_em,
            patch.object(guard, "_check_error_budget") as mock_eb,
        ):
            result = guard.check()
            assert result.allowed is True
            mock_ks.assert_not_called()
            mock_em.assert_not_called()
            mock_eb.assert_not_called()


# =============================================================================
# Kill Switch 거부 동작 검증
# =============================================================================


class TestThrottleGovernanceGuardKillSwitchBehavior:
    """Kill Switch 거부 동작 검증."""

    def test_kill_switch_disabled_rejects(self):
        """Kill Switch 비활성 시 allowed=False를 반환해야 한다."""
        guard = ThrottleGovernanceGuard()
        mock_checks = MagicMock()
        mock_checks.is_system_enabled.return_value = False

        with (
            patch.object(guard, "_is_break_glass_active", return_value=False),
            patch.dict("sys.modules", {"selfhealing.services.governance.checks": mock_checks}),
        ):
            result = guard.check()
            assert result.allowed is False
            assert result.reason == "kill_switch_disabled"

    def test_kill_switch_import_error_failopen(self):
        """Kill Switch 모듈 import 실패 시 Fail-Open (통과 허용)."""
        guard = ThrottleGovernanceGuard()

        with (
            patch.object(guard, "_is_break_glass_active", return_value=False),
            patch.dict("sys.modules", {"selfhealing.services.governance.checks": None}),
            patch.object(guard, "_check_emergency_level", return_value=MagicMock(allowed=True)),
            patch.object(guard, "_check_error_budget", return_value=MagicMock(allowed=True)),
        ):
            result = guard.check()
            assert result.allowed is True


# =============================================================================
# Emergency Level 거부 동작 검증
# =============================================================================


class TestThrottleGovernanceGuardEmergencyBehavior:
    """Emergency Level 거부 동작 검증."""

    def test_emergency_level_3_rejects(self):
        """Emergency LEVEL_3 이상 시 거부해야 한다."""
        guard = ThrottleGovernanceGuard()

        mock_manager = MagicMock()
        mock_level = MagicMock()
        mock_level.value = 3
        mock_manager.get_current_level.return_value = mock_level
        mock_em_module = MagicMock()
        mock_em_module.get_emergency_manager.return_value = mock_manager

        with (
            patch.object(guard, "_is_break_glass_active", return_value=False),
            patch.object(guard, "_check_kill_switch", return_value=MagicMock(allowed=True)),
            patch.dict("sys.modules", {"selfhealing.services.emergency_mode": mock_em_module}),
            patch.object(guard, "_check_error_budget", return_value=MagicMock(allowed=True)),
        ):
            result = guard.check()
            assert result.allowed is False
            assert "emergency_level_3" in result.reason

    def test_emergency_level_2_passes(self):
        """Emergency LEVEL_2는 통과해야 한다 (3 미만)."""
        guard = ThrottleGovernanceGuard()

        mock_manager = MagicMock()
        mock_level = MagicMock()
        mock_level.value = 2
        mock_manager.get_current_level.return_value = mock_level
        mock_em_module = MagicMock()
        mock_em_module.get_emergency_manager.return_value = mock_manager

        with (
            patch.object(guard, "_is_break_glass_active", return_value=False),
            patch.object(guard, "_check_kill_switch", return_value=MagicMock(allowed=True)),
            patch.dict("sys.modules", {"selfhealing.services.emergency_mode": mock_em_module}),
            patch.object(guard, "_check_error_budget", return_value=MagicMock(allowed=True)),
        ):
            result = guard.check()
            assert result.allowed is True

    def test_emergency_import_error_failopen(self):
        """Emergency 모듈 import 실패 시 Fail-Open."""
        guard = ThrottleGovernanceGuard()

        with (
            patch.object(guard, "_is_break_glass_active", return_value=False),
            patch.object(guard, "_check_kill_switch", return_value=MagicMock(allowed=True)),
            patch.dict("sys.modules", {"selfhealing.services.emergency_mode": None}),
            patch.object(guard, "_check_error_budget", return_value=MagicMock(allowed=True)),
        ):
            result = guard.check()
            assert result.allowed is True


# =============================================================================
# Error Budget 거부 동작 검증
# =============================================================================


class TestThrottleGovernanceGuardErrorBudgetBehavior:
    """Error Budget 거부 동작 검증."""

    def test_error_budget_exhausted_rejects(self):
        """Error Budget 소진 시 거부해야 한다."""
        guard = ThrottleGovernanceGuard()

        mock_gate_result = MagicMock()
        mock_gate_result.allowed = False
        mock_gate_result.reason = "error_budget_exhausted"
        mock_gate_result.error_budget_percent = 0.0
        mock_gate_module = MagicMock()
        mock_gate_module.check_automation_allowed.return_value = mock_gate_result

        with (
            patch.object(guard, "_is_break_glass_active", return_value=False),
            patch.object(guard, "_check_kill_switch", return_value=MagicMock(allowed=True)),
            patch.object(guard, "_check_emergency_level", return_value=MagicMock(allowed=True)),
            patch.dict("sys.modules", {"selfhealing.services.error_budget_gate.gate": mock_gate_module}),
        ):
            result = guard.check()
            assert result.allowed is False
            assert "error_budget" in result.reason

    def test_error_budget_import_error_failopen(self):
        """ErrorBudgetGate import 실패 시 Fail-Open."""
        guard = ThrottleGovernanceGuard()

        with (
            patch.object(guard, "_is_break_glass_active", return_value=False),
            patch.object(guard, "_check_kill_switch", return_value=MagicMock(allowed=True)),
            patch.object(guard, "_check_emergency_level", return_value=MagicMock(allowed=True)),
            patch.dict("sys.modules", {"selfhealing.services.error_budget_gate.gate": None}),
        ):
            result = guard.check()
            assert result.allowed is True


# =============================================================================
# Break Glass Fail-Open 동작 검증
# =============================================================================


class TestThrottleGovernanceGuardBreakGlassFailOpenBehavior:
    """Break Glass 모듈 실패 시 Fail-Open 동작 검증."""

    def test_break_glass_import_error_returns_false(self):
        """Break Glass 모듈 import 실패 시 비활성 간주 (False)."""
        guard = ThrottleGovernanceGuard()

        with patch.dict("sys.modules", {"selfhealing.settings.governance": None}):
            assert guard._is_break_glass_active() is False
