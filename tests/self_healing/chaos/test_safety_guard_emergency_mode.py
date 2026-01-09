"""
SafetyGuard Emergency Mode 체크 테스트.

SafetyGuard가 Emergency Mode LEVEL_2+ 상태에서 Chaos 실험을 차단하는지 검증합니다.

Reference: docs/self_healing/middleware_system/26_IMPROVEMENT_PART1_GOVERNANCE_INTEGRATION.md
"""

import pytest
from unittest.mock import patch, MagicMock


# 패치 경로: _check_emergency_mode 내부에서 import하는 함수 경로
EMERGENCY_MANAGER_PATCH = "selfhealing.services.emergency_mode.get_emergency_manager"


class TestSafetyGuardEmergencyMode:
    """SafetyGuard Emergency Mode 체크 테스트."""

    def test_blocked_by_emergency_level_2(self):
        """Emergency Mode LEVEL_2에서 실험 차단."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard, BlockReason
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
                    mock_manager = MagicMock()
                    mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_2
                    mock_em.return_value = mock_manager

                    result = guard.check(experiment_id="test-001")

                    assert not result.allowed
                    assert result.block_reason == BlockReason.EMERGENCY_MODE_ACTIVE.value
                    assert result.emergency_level == "LEVEL_2"
                    assert result.emergency_mode_active is True
                    assert "emergency_mode" in result.checks_failed

    def test_blocked_by_emergency_level_3(self):
        """Emergency Mode LEVEL_3에서 실험 차단."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard, BlockReason
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
                    mock_manager = MagicMock()
                    mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_3
                    mock_em.return_value = mock_manager

                    result = guard.check(experiment_id="test-002")

                    assert not result.allowed
                    assert result.block_reason == BlockReason.EMERGENCY_MODE_ACTIVE.value
                    assert result.emergency_level == "LEVEL_3"
                    assert result.emergency_mode_active is True

    def test_allowed_on_level_1(self):
        """Emergency Mode LEVEL_1에서는 허용."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
                    mock_manager = MagicMock()
                    mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_1
                    mock_em.return_value = mock_manager

                    # error budget도 통과하도록 설정
                    with patch.object(
                        guard,
                        "_check_error_budget",
                        return_value={"remaining_percent": 100.0},
                    ):
                        result = guard.check(experiment_id="test-003")

                    # emergency_mode 체크는 통과
                    assert "emergency_mode" in result.checks_passed
                    assert result.emergency_mode_active is False
                    assert result.emergency_level == "LEVEL_1"

    def test_allowed_on_normal(self):
        """Emergency Mode NORMAL에서는 허용."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
                    mock_manager = MagicMock()
                    mock_manager.get_current_level.return_value = EmergencyLevel.NORMAL
                    mock_em.return_value = mock_manager

                    with patch.object(
                        guard,
                        "_check_error_budget",
                        return_value={"remaining_percent": 100.0},
                    ):
                        result = guard.check(experiment_id="test-004")

                    assert "emergency_mode" in result.checks_passed
                    assert result.emergency_mode_active is False
                    assert result.emergency_level == "NORMAL"

    def test_fail_open_on_exception(self):
        """Emergency Mode 확인 실패 시 Fail-open."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(
                    EMERGENCY_MANAGER_PATCH,
                    side_effect=ImportError("Module not found"),
                ):
                    with patch.object(
                        guard,
                        "_check_error_budget",
                        return_value={"remaining_percent": 100.0},
                    ):
                        result = guard.check(experiment_id="test-005")

                    # Fail-open: 예외 발생 시 허용
                    assert "emergency_mode" in result.checks_passed
                    assert result.emergency_level == "UNKNOWN"

    def test_check_emergency_mode_method_returns_correct_structure(self):
        """_check_emergency_mode() 메서드가 올바른 구조를 반환하는지 확인."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
            mock_manager = MagicMock()
            mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_2
            mock_em.return_value = mock_manager

            result = guard._check_emergency_mode()

            assert "active" in result
            assert "level" in result
            assert "level_value" in result
            assert result["active"] is True
            assert result["level"] == "LEVEL_2"
            assert result["level_value"] == 2

    def test_safety_check_result_to_dict_includes_emergency_fields(self):
        """SafetyCheckResult.to_dict()에 emergency 필드가 포함되는지 확인."""
        from selfhealing.services.chaos.safety_guard import SafetyCheckResult, SafetyStatus

        result = SafetyCheckResult(
            status=SafetyStatus.BLOCKED.value,
            allowed=False,
            emergency_mode_active=True,
            emergency_level="LEVEL_2",
        )

        result_dict = result.to_dict()

        assert "emergency_mode_active" in result_dict
        assert "emergency_level" in result_dict
        assert result_dict["emergency_mode_active"] is True
        assert result_dict["emergency_level"] == "LEVEL_2"

    def test_block_reason_enum_has_emergency_mode_active(self):
        """BlockReason enum에 EMERGENCY_MODE_ACTIVE가 있는지 확인."""
        from selfhealing.services.chaos.safety_guard import BlockReason

        assert hasattr(BlockReason, "EMERGENCY_MODE_ACTIVE")
        assert BlockReason.EMERGENCY_MODE_ACTIVE.value == "emergency_mode_active"


class TestSafetyGuardEmergencyAudit:
    """SafetyGuard Emergency Mode 차단 시 Audit 로깅 테스트.
    
    Reference: 26_IMPROVEMENT_PART1 섹션 8.4 - Emergency Audit 보완
    리뷰 피드백: "왜 이때 카오스 실험이 안 돌았지?"에 대한 증적 기록
    """

    def test_log_governance_blocked_audit_called_on_emergency_block(self):
        """Emergency Mode 차단 시 log_governance_blocked_audit() 호출."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
                    mock_manager = MagicMock()
                    mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_2
                    mock_em.return_value = mock_manager

                    with patch(
                        "selfhealing.services.audit_helpers.log_governance_blocked_audit"
                    ) as mock_audit:
                        result = guard.check(experiment_id="test-audit-001")

                        # Audit 함수 호출 확인
                        mock_audit.assert_called_once()
                        call_kwargs = mock_audit.call_args[1]

                        assert call_kwargs["action"] == "chaos_experiment"
                        assert call_kwargs["block_reason"] == "emergency_mode_active"
                        assert call_kwargs["details"]["current_emergency_level"] == "LEVEL_2"
                        assert call_kwargs["details"]["emergency_level_value"] == 2
                        assert "blocked_by" in call_kwargs["details"]

    def test_audit_includes_emergency_level_3(self):
        """Emergency Mode LEVEL_3 차단 시 레벨 정보가 Audit에 포함."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
                    mock_manager = MagicMock()
                    mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_3
                    mock_em.return_value = mock_manager

                    with patch(
                        "selfhealing.services.audit_helpers.log_governance_blocked_audit"
                    ) as mock_audit:
                        guard.check(experiment_id="test-audit-002")

                        call_kwargs = mock_audit.call_args[1]
                        assert call_kwargs["details"]["current_emergency_level"] == "LEVEL_3"
                        assert call_kwargs["details"]["emergency_level_value"] == 3

    def test_audit_not_called_when_allowed(self):
        """Emergency Mode가 비활성 상태면 Audit 호출 안 함."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
                    mock_manager = MagicMock()
                    mock_manager.get_current_level.return_value = EmergencyLevel.NORMAL
                    mock_em.return_value = mock_manager

                    with patch.object(
                        guard,
                        "_check_error_budget",
                        return_value={"remaining_percent": 100.0},
                    ):
                        with patch(
                            "selfhealing.services.audit_helpers.log_governance_blocked_audit"
                        ) as mock_audit:
                            result = guard.check(experiment_id="test-audit-003")

                            # NORMAL 상태에서는 Audit 호출 안 함
                            mock_audit.assert_not_called()
                            assert result.allowed or "emergency_mode" in result.checks_passed

    def test_audit_failure_does_not_affect_block(self):
        """Audit 로깅 실패해도 차단은 정상 동작."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
                    mock_manager = MagicMock()
                    mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_2
                    mock_em.return_value = mock_manager

                    with patch(
                        "selfhealing.services.audit_helpers.log_governance_blocked_audit",
                        side_effect=Exception("Audit failed"),
                    ):
                        # Audit 실패해도 차단은 정상 동작해야 함
                        result = guard.check(experiment_id="test-audit-004")

                        assert not result.allowed
                        assert result.block_reason == "emergency_mode_active"

    def test_log_emergency_block_audit_method_exists(self):
        """_log_emergency_block_audit() 메서드 존재 확인."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard

        guard = SafetyGuard()
        
        assert hasattr(guard, "_log_emergency_block_audit")
        assert callable(guard._log_emergency_block_audit)

    def test_audit_details_structure(self):
        """Audit 로그의 details 구조 검증."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        guard = SafetyGuard()

        with patch.object(guard, "_check_global_block", return_value=False):
            with patch.object(guard, "_check_kill_switch", return_value=False):
                with patch(EMERGENCY_MANAGER_PATCH) as mock_em:
                    mock_manager = MagicMock()
                    mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_2
                    mock_em.return_value = mock_manager

                    with patch(
                        "selfhealing.services.audit_helpers.log_governance_blocked_audit"
                    ) as mock_audit:
                        guard.check(experiment_id="test-audit-005")

                        call_kwargs = mock_audit.call_args[1]
                        details = call_kwargs["details"]

                        # 필수 필드 확인 (대시보드 통계용)
                        assert "current_emergency_level" in details
                        assert "emergency_level_value" in details
                        assert "blocked_by" in details
                        
                        # 값 타입 확인
                        assert isinstance(details["current_emergency_level"], str)
                        assert isinstance(details["emergency_level_value"], int)
