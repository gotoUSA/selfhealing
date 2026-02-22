"""
EmergencyCoordinator Cascade Auditor 연동 단위 테스트.

Phase 7: EmergencyCoordinator에 CascadeEventAuditor 주입 및 
Emergency Level 변경 시 Cascade Event 자동 기록 테스트.

Tests:
- cascade_auditor 주입 및 설정
- on_emergency_level_changed에서 Cascade Event 기록
- record_with_external_trace 호출 (request 있을 때)
- request 없을 때 일반 record 호출
- cascade_auditor 없을 때 기록 생략

Reference:
    docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
"""

from __future__ import annotations

from unittest.mock import MagicMock

from selfhealing.services.coordination.coordinator import EmergencyCoordinator
from selfhealing.services.coordination.enums import ActionType
from selfhealing.services.coordination.models import (
    CoordinationAction,
)
from selfhealing.services.emergency_mode.enums import EmergencyLevel

# =============================================================================
# Cascade Auditor Injection Tests
# =============================================================================


class TestCascadeAuditorInjection:
    """CascadeEventAuditor 주입 테스트."""

    def test_coordinator_without_cascade_auditor(self):
        """cascade_auditor 없이 생성."""
        coordinator = EmergencyCoordinator()

        assert coordinator.get_cascade_auditor() is None
        assert coordinator.has_cascade_auditor() is False

    def test_coordinator_with_cascade_auditor_in_constructor(self):
        """생성자에서 cascade_auditor 주입."""
        mock_auditor = MagicMock()

        coordinator = EmergencyCoordinator(cascade_auditor=mock_auditor)

        assert coordinator.get_cascade_auditor() is mock_auditor
        assert coordinator.has_cascade_auditor() is True

    def test_set_cascade_auditor_after_creation(self):
        """생성 후 set_cascade_auditor로 설정."""
        coordinator = EmergencyCoordinator()
        mock_auditor = MagicMock()

        coordinator.set_cascade_auditor(mock_auditor)

        assert coordinator.get_cascade_auditor() is mock_auditor
        assert coordinator.has_cascade_auditor() is True

    def test_set_cascade_auditor_replaces_existing(self):
        """기존 auditor 교체."""
        old_auditor = MagicMock()
        new_auditor = MagicMock()

        coordinator = EmergencyCoordinator(cascade_auditor=old_auditor)
        coordinator.set_cascade_auditor(new_auditor)

        assert coordinator.get_cascade_auditor() is new_auditor


# =============================================================================
# Cascade Event Recording Tests
# =============================================================================


class TestCascadeEventRecordingOnLevelChange:
    """Emergency Level 변경 시 Cascade Event 기록 테스트."""

    def setup_method(self):
        """각 테스트 전 설정."""
        self.mock_auditor = MagicMock()
        self.coordinator = EmergencyCoordinator(cascade_auditor=self.mock_auditor)

    def test_record_called_on_level_change_without_actions(self):
        """액션 없이 레벨 변경 시 record 호출."""
        result = self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
        )

        assert result.success is True

        # record 호출 확인
        self.mock_auditor.record.assert_called_once()

        call_kwargs = self.mock_auditor.record.call_args[1]
        assert call_kwargs["trigger_type"] == "EMERGENCY_LEVEL_CHANGED"
        assert call_kwargs["namespace"] == "seoul"
        assert call_kwargs["trigger_details"]["old_level"] == "NORMAL"
        assert call_kwargs["trigger_details"]["new_level"] == "LEVEL_3"
        assert call_kwargs["trigger_details"]["transition_type"] == "ACTIVATION"
        assert call_kwargs["triggered_by"] == "system"
        assert call_kwargs["effects"] == []

    def test_record_called_on_level_change_with_actions(self):
        """액션과 함께 레벨 변경 시 record 호출."""
        actions = [
            CoordinationAction(type=ActionType.GOVERNANCE_STRICT),
            CoordinationAction(type=ActionType.CANARY_ROLLBACK),
        ]

        result = self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
            actions=actions,
            force=True,  # 플래핑 가드 무시
        )

        assert result.success is True

        # record 호출 확인
        self.mock_auditor.record.assert_called_once()

        call_kwargs = self.mock_auditor.record.call_args[1]
        effects = call_kwargs["effects"]

        assert len(effects) == 2
        assert effects[0]["action_type"] == "governance_strict"
        assert effects[0]["success"] is True
        assert effects[1]["action_type"] == "canary_rollback"
        assert effects[1]["success"] is True

    def test_record_with_external_trace_when_request_provided(self):
        """request 있을 때 record_with_external_trace 호출."""
        mock_request = MagicMock()
        mock_request.META = {
            "HTTP_TRACEPARENT": "00-abc123-def456-01",
            "HTTP_X_REQUEST_ID": "req-789",
        }

        self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
            request=mock_request,
        )

        # record_with_external_trace 호출 확인 (record 아님)
        self.mock_auditor.record_with_external_trace.assert_called_once()
        self.mock_auditor.record.assert_not_called()

        call_kwargs = self.mock_auditor.record_with_external_trace.call_args[1]
        assert call_kwargs["request"] is mock_request
        assert call_kwargs["trigger_type"] == "EMERGENCY_LEVEL_CHANGED"

    def test_no_record_when_no_auditor(self):
        """auditor 없을 때 기록 생략."""
        coordinator = EmergencyCoordinator()  # auditor 없음

        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
        )

        # 예외 없이 정상 완료
        assert result.success is True


# =============================================================================
# Transition Type Tests
# =============================================================================


class TestTransitionTypeDetection:
    """Emergency Level 전환 유형 감지 테스트."""

    def setup_method(self):
        """각 테스트 전 설정."""
        self.mock_auditor = MagicMock()
        self.coordinator = EmergencyCoordinator(cascade_auditor=self.mock_auditor)

    def test_activation_from_normal(self):
        """NORMAL → LEVEL_x = ACTIVATION."""
        self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_1,
            namespace="seoul",
            trigger_event_id="trigger-001",
        )

        call_kwargs = self.mock_auditor.record.call_args[1]
        assert call_kwargs["trigger_details"]["transition_type"] == "ACTIVATION"

    def test_deactivation_to_normal(self):
        """LEVEL_x → NORMAL = DEACTIVATION."""
        self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.LEVEL_3,
            new_level=EmergencyLevel.NORMAL,
            namespace="seoul",
            trigger_event_id="trigger-001",
        )

        call_kwargs = self.mock_auditor.record.call_args[1]
        assert call_kwargs["trigger_details"]["transition_type"] == "DEACTIVATION"

    def test_escalation_higher_level(self):
        """낮은 레벨 → 높은 레벨 = ESCALATION."""
        self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.LEVEL_1,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
        )

        call_kwargs = self.mock_auditor.record.call_args[1]
        assert call_kwargs["trigger_details"]["transition_type"] == "ESCALATION"

    def test_de_escalation_lower_level(self):
        """높은 레벨 → 낮은 레벨 = DE_ESCALATION."""
        self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.LEVEL_3,
            new_level=EmergencyLevel.LEVEL_1,
            namespace="seoul",
            trigger_event_id="trigger-001",
        )

        call_kwargs = self.mock_auditor.record.call_args[1]
        assert call_kwargs["trigger_details"]["transition_type"] == "DE_ESCALATION"


# =============================================================================
# Action Result Recording Tests
# =============================================================================


class TestActionResultRecording:
    """액션 실행 결과 Cascade Effect 기록 테스트."""

    def setup_method(self):
        """각 테스트 전 설정."""
        self.mock_auditor = MagicMock()
        self.coordinator = EmergencyCoordinator(cascade_auditor=self.mock_auditor)

    def test_successful_action_recorded_as_effect(self):
        """성공한 액션이 effect로 기록."""
        actions = [
            CoordinationAction(type=ActionType.GOVERNANCE_STRICT),
        ]

        self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
            actions=actions,
            force=True,
        )

        call_kwargs = self.mock_auditor.record.call_args[1]
        effects = call_kwargs["effects"]

        assert len(effects) == 1
        assert effects[0]["success"] is True
        assert effects[0]["action_type"] == "governance_strict"

    def test_dry_run_action_includes_dry_run_flag(self):
        """Dry-Run 액션은 dry_run 플래그 포함."""
        self.coordinator.set_dry_run_mode(True)

        actions = [
            CoordinationAction(type=ActionType.GOVERNANCE_STRICT),
        ]

        self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
            actions=actions,
            force=True,
        )

        call_kwargs = self.mock_auditor.record.call_args[1]
        effects = call_kwargs["effects"]

        assert effects[0]["details"]["dry_run"] is True

    def test_multiple_actions_all_recorded(self):
        """여러 액션 모두 effect로 기록."""
        actions = [
            CoordinationAction(type=ActionType.GOVERNANCE_STRICT),
            CoordinationAction(type=ActionType.CANARY_ROLLBACK),
            CoordinationAction(type=ActionType.BUDGET_MULTIPLIER),
        ]

        self.coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
            actions=actions,
            force=True,
        )

        call_kwargs = self.mock_auditor.record.call_args[1]
        effects = call_kwargs["effects"]

        assert len(effects) == 3
        action_types = [e["action_type"] for e in effects]
        assert "governance_strict" in action_types
        assert "canary_rollback" in action_types
        assert "budget_multiplier" in action_types


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestCascadeRecordingErrorHandling:
    """Cascade 기록 실패 시 에러 처리 테스트."""

    def test_record_failure_does_not_block_level_change(self):
        """record 실패해도 레벨 변경은 성공."""
        mock_auditor = MagicMock()
        mock_auditor.record.side_effect = Exception("Redis connection failed")

        coordinator = EmergencyCoordinator(cascade_auditor=mock_auditor)

        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
        )

        # 예외에도 불구하고 레벨 변경은 성공
        assert result.success is True

        # 상태 업데이트 확인
        state = coordinator.get_state("seoul")
        assert state.emergency_level == EmergencyLevel.LEVEL_3

    def test_record_with_external_trace_failure_graceful(self):
        """record_with_external_trace 실패도 graceful 처리."""
        mock_auditor = MagicMock()
        mock_auditor.record_with_external_trace.side_effect = Exception("DB error")

        coordinator = EmergencyCoordinator(cascade_auditor=mock_auditor)
        mock_request = MagicMock()

        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-001",
            request=mock_request,
        )

        assert result.success is True


# =============================================================================
# Flapping Guard Integration Tests
# =============================================================================


class TestCascadeRecordingWithFlappingGuard:
    """플래핑 가드와 Cascade 기록 통합 테스트."""

    def test_blocked_by_cooldown_no_cascade_recorded(self):
        """쿨다운으로 차단 시 Cascade 기록 안함."""
        mock_auditor = MagicMock()
        coordinator = EmergencyCoordinator(cascade_auditor=mock_auditor)

        # 첫 번째 변경 (성공)
        coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_1,
            namespace="seoul",
            trigger_event_id="trigger-001",
        )

        first_call_count = mock_auditor.record.call_count

        # 즉시 두 번째 변경 시도 (쿨다운으로 차단)
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.LEVEL_1,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-002",
        )

        # 차단됨
        assert result.success is False

        # record가 추가로 호출되지 않음
        assert mock_auditor.record.call_count == first_call_count

    def test_forced_bypasses_cooldown_and_records_cascade(self):
        """force=True면 쿨다운 무시하고 Cascade 기록."""
        mock_auditor = MagicMock()
        coordinator = EmergencyCoordinator(cascade_auditor=mock_auditor)

        # 첫 번째 변경
        coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_1,
            namespace="seoul",
            trigger_event_id="trigger-001",
        )

        first_call_count = mock_auditor.record.call_count

        # 즉시 두 번째 변경 (force=True)
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.LEVEL_1,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-002",
            force=True,
        )

        # 성공
        assert result.success is True

        # record가 추가로 호출됨
        assert mock_auditor.record.call_count == first_call_count + 1
