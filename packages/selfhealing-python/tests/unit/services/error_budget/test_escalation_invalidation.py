"""
EscalationTriggeredInvalidation 단위 테스트.

Emergency 격상 시 캐시 푸시 무효화 테스트.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.5
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.services.error_budget.escalation_invalidation import (
    LEVEL_ORDER,
    EscalationTriggeredInvalidation,
    get_escalation_triggered_invalidation,
    reset_escalation_invalidation,
)

# =============================================================================
# LEVEL_ORDER 테스트
# =============================================================================

class TestLevelOrder:
    """레벨 순서 상수 테스트."""

    def test_normal_is_0(self):
        """NORMAL = 0."""
        assert LEVEL_ORDER["NORMAL"] == 0

    def test_level_1_is_1(self):
        """LEVEL_1 = 1."""
        assert LEVEL_ORDER["LEVEL_1"] == 1

    def test_level_2_is_2(self):
        """LEVEL_2 = 2."""
        assert LEVEL_ORDER["LEVEL_2"] == 2

    def test_level_3_is_3(self):
        """LEVEL_3 = 3."""
        assert LEVEL_ORDER["LEVEL_3"] == 3

    def test_order_is_ascending(self):
        """순서가 오름차순."""
        levels = ["NORMAL", "LEVEL_1", "LEVEL_2", "LEVEL_3"]
        orders = [LEVEL_ORDER[l] for l in levels]

        assert orders == sorted(orders)


# =============================================================================
# EscalationTriggeredInvalidation 테스트
# =============================================================================

class TestEscalationTriggeredInvalidation:
    """EscalationTriggeredInvalidation 테스트."""

    def test_initial_state(self):
        """초기 상태."""
        invalidation = EscalationTriggeredInvalidation()

        assert invalidation.get_target_count() == 0
        assert invalidation.get_invalidation_count() == 0
        assert invalidation.is_registered() is False

    def test_register_target(self):
        """대상 등록."""
        invalidation = EscalationTriggeredInvalidation()
        mock_fn = MagicMock()

        invalidation.register_target(mock_fn)

        assert invalidation.get_target_count() == 1

    def test_register_target_no_duplicates(self):
        """중복 등록 방지."""
        invalidation = EscalationTriggeredInvalidation()
        mock_fn = MagicMock()

        invalidation.register_target(mock_fn)
        invalidation.register_target(mock_fn)

        assert invalidation.get_target_count() == 1

    def test_unregister_target(self):
        """대상 해제."""
        invalidation = EscalationTriggeredInvalidation()
        mock_fn = MagicMock()

        invalidation.register_target(mock_fn)
        result = invalidation.unregister_target(mock_fn)

        assert result is True
        assert invalidation.get_target_count() == 0

    def test_unregister_nonexistent_target(self):
        """없는 대상 해제."""
        invalidation = EscalationTriggeredInvalidation()
        mock_fn = MagicMock()

        result = invalidation.unregister_target(mock_fn)

        assert result is False


# =============================================================================
# 격상/하강 판별 테스트
# =============================================================================

class TestEscalationDetection:
    """격상/하강 판별 테스트."""

    def test_is_escalation_normal_to_level_1(self):
        """NORMAL → LEVEL_1: 격상."""
        invalidation = EscalationTriggeredInvalidation()

        assert invalidation._is_escalation("NORMAL", "LEVEL_1") is True

    def test_is_escalation_level_1_to_level_3(self):
        """LEVEL_1 → LEVEL_3: 격상."""
        invalidation = EscalationTriggeredInvalidation()

        assert invalidation._is_escalation("LEVEL_1", "LEVEL_3") is True

    def test_is_escalation_level_3_to_level_1(self):
        """LEVEL_3 → LEVEL_1: 격상 아님."""
        invalidation = EscalationTriggeredInvalidation()

        assert invalidation._is_escalation("LEVEL_3", "LEVEL_1") is False

    def test_is_escalation_same_level(self):
        """동일 레벨: 격상 아님."""
        invalidation = EscalationTriggeredInvalidation()

        assert invalidation._is_escalation("LEVEL_2", "LEVEL_2") is False

    def test_is_deescalation_level_3_to_normal(self):
        """LEVEL_3 → NORMAL: 하강."""
        invalidation = EscalationTriggeredInvalidation()

        assert invalidation._is_deescalation("LEVEL_3", "NORMAL") is True

    def test_is_deescalation_normal_to_level_1(self):
        """NORMAL → LEVEL_1: 하강 아님."""
        invalidation = EscalationTriggeredInvalidation()

        assert invalidation._is_deescalation("NORMAL", "LEVEL_1") is False


# =============================================================================
# 무효화 트리거 테스트
# =============================================================================

class TestInvalidationTrigger:
    """무효화 트리거 테스트."""

    def test_escalation_triggers_invalidation(self):
        """격상 시 무효화 호출."""
        invalidation = EscalationTriggeredInvalidation()
        mock_invalidate = MagicMock()
        invalidation.register_target(mock_invalidate)

        # 격상 이벤트 시뮬레이션
        event = {"old_level": "LEVEL_1", "new_level": "LEVEL_3"}
        invalidation._on_level_changed(event)

        mock_invalidate.assert_called_once()

    def test_deescalation_does_not_trigger(self):
        """하강 시 무효화 미호출."""
        invalidation = EscalationTriggeredInvalidation()
        mock_invalidate = MagicMock()
        invalidation.register_target(mock_invalidate)

        # 하강 이벤트
        event = {"old_level": "LEVEL_3", "new_level": "LEVEL_1"}
        invalidation._on_level_changed(event)

        mock_invalidate.assert_not_called()

    def test_same_level_does_not_trigger(self):
        """동일 레벨 변경 시 무효화 미호출."""
        invalidation = EscalationTriggeredInvalidation()
        mock_invalidate = MagicMock()
        invalidation.register_target(mock_invalidate)

        event = {"old_level": "LEVEL_2", "new_level": "LEVEL_2"}
        invalidation._on_level_changed(event)

        mock_invalidate.assert_not_called()

    def test_multiple_targets_invalidated(self):
        """다중 대상 무효화."""
        invalidation = EscalationTriggeredInvalidation()
        mock1 = MagicMock()
        mock2 = MagicMock()
        mock3 = MagicMock()

        invalidation.register_target(mock1)
        invalidation.register_target(mock2)
        invalidation.register_target(mock3)

        event = {"old_level": "NORMAL", "new_level": "LEVEL_3"}
        invalidation._on_level_changed(event)

        mock1.assert_called_once()
        mock2.assert_called_once()
        mock3.assert_called_once()

    def test_target_error_continues_others(self):
        """하나의 대상 실패해도 나머지 진행."""
        invalidation = EscalationTriggeredInvalidation()

        mock1 = MagicMock(side_effect=Exception("Error"))
        mock2 = MagicMock()

        invalidation.register_target(mock1)
        invalidation.register_target(mock2)

        event = {"old_level": "NORMAL", "new_level": "LEVEL_3"}
        invalidation._on_level_changed(event)

        # mock1은 실패했지만 mock2는 호출됨
        mock2.assert_called_once()

    def test_invalidation_count_incremented(self):
        """무효화 카운트 증가."""
        invalidation = EscalationTriggeredInvalidation()
        mock_fn = MagicMock()
        invalidation.register_target(mock_fn)

        assert invalidation.get_invalidation_count() == 0

        event = {"old_level": "NORMAL", "new_level": "LEVEL_3"}
        invalidation._on_level_changed(event)

        assert invalidation.get_invalidation_count() == 1


# =============================================================================
# 수동 무효화 테스트
# =============================================================================

class TestManualInvalidation:
    """수동 무효화 테스트."""

    def test_trigger_manual_invalidation(self):
        """수동 무효화 트리거."""
        invalidation = EscalationTriggeredInvalidation()
        mock_fn = MagicMock()
        invalidation.register_target(mock_fn)

        count = invalidation.trigger_manual_invalidation("test reason")

        assert count == 1
        mock_fn.assert_called_once()

    def test_manual_invalidation_returns_success_count(self):
        """성공 횟수 반환."""
        invalidation = EscalationTriggeredInvalidation()

        mock1 = MagicMock()
        mock2 = MagicMock(side_effect=Exception("Error"))
        mock3 = MagicMock()

        invalidation.register_target(mock1)
        invalidation.register_target(mock2)
        invalidation.register_target(mock3)

        count = invalidation.trigger_manual_invalidation("test")

        # mock2는 실패, 나머지 2개 성공
        assert count == 2


# =============================================================================
# 이벤트 핸들러 등록 테스트
# =============================================================================

class TestEventHandlerRegistration:
    """이벤트 핸들러 등록 테스트."""

    def test_is_registered_false_initially(self):
        """초기에 미등록."""
        invalidation = EscalationTriggeredInvalidation()
        assert invalidation.is_registered() is False


# =============================================================================
# 이벤트 객체 처리 테스트
# =============================================================================

class TestEventObjectHandling:
    """다양한 이벤트 객체 형식 처리."""

    def test_dict_event(self):
        """딕셔너리 이벤트."""
        invalidation = EscalationTriggeredInvalidation()
        mock_fn = MagicMock()
        invalidation.register_target(mock_fn)

        event = {"old_level": "NORMAL", "new_level": "LEVEL_3"}
        invalidation._on_level_changed(event)

        mock_fn.assert_called_once()

    def test_object_with_data_attribute(self):
        """data 속성 있는 객체 이벤트."""
        invalidation = EscalationTriggeredInvalidation()
        mock_fn = MagicMock()
        invalidation.register_target(mock_fn)

        class MockEvent:
            data = {"old_level": "NORMAL", "new_level": "LEVEL_3"}

        invalidation._on_level_changed(MockEvent())

        mock_fn.assert_called_once()

    def test_event_with_namespace(self):
        """네임스페이스 포함 이벤트."""
        invalidation = EscalationTriggeredInvalidation()
        mock_fn = MagicMock()
        invalidation.register_target(mock_fn)

        event = {
            "old_level": "NORMAL",
            "new_level": "LEVEL_3",
            "namespace": "seoul",
        }
        invalidation._on_level_changed(event)

        mock_fn.assert_called_once()


# =============================================================================
# Singleton 테스트
# =============================================================================

class TestEscalationInvalidationSingleton:
    """싱글톤 테스트."""

    def setup_method(self):
        """테스트 전 초기화."""
        reset_escalation_invalidation()

    def teardown_method(self):
        """테스트 후 정리."""
        reset_escalation_invalidation()

    def test_get_returns_singleton(self):
        """싱글톤 반환."""
        with patch(
            "selfhealing.services.error_budget.escalation_invalidation."
            "EscalationTriggeredInvalidation.register_event_handler",
            return_value=False,
        ):
            i1 = get_escalation_triggered_invalidation()
            i2 = get_escalation_triggered_invalidation()

        assert i1 is i2

    def test_reset_clears_singleton(self):
        """리셋 후 새 인스턴스."""
        with patch(
            "selfhealing.services.error_budget.escalation_invalidation."
            "EscalationTriggeredInvalidation.register_event_handler",
            return_value=False,
        ):
            i1 = get_escalation_triggered_invalidation()
            reset_escalation_invalidation()
            i2 = get_escalation_triggered_invalidation()

        assert i1 is not i2
