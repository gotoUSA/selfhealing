"""
AdaptiveThrottleFacade 단위 테스트.

테스트 대상: selfhealing.services.throttle.facade.AdaptiveThrottleFacade

검증 범위:
- check() Guard 체크 → rate limit 체크 → DLQ Sink 순서
- record_response() ThrottlePolicy 위임
- Guard 거부 시 즉시 반환 + DLQ 저장
- Guard 예외 시 Fail-Open (다음 Guard로 진행)
- current_limit 속성 위임
- get_stats() 통계 반환
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from selfhealing.interfaces.resilience_policy import GuardResult
from selfhealing.services.throttle.config import ThrottleResult
from selfhealing.services.throttle.facade import AdaptiveThrottleFacade


# =============================================================================
# check() Guard → rate limit → DLQ 파이프라인 동작 검증
# =============================================================================


class TestAdaptiveThrottleFacadeCheckBehavior:
    """AdaptiveThrottleFacade.check() 파이프라인 동작 검증."""

    def _make_allowed_result(self, limit: int = 100) -> ThrottleResult:
        """허용 ThrottleResult 생성."""
        return ThrottleResult(
            allowed=True,
            current_count=1,
            limit=limit,
            remaining=limit - 1,
            reset_at=0.0,
        )

    def _make_rejected_result(self, limit: int = 100) -> ThrottleResult:
        """거부 ThrottleResult 생성."""
        return ThrottleResult(
            allowed=False,
            current_count=limit,
            limit=limit,
            remaining=0,
            reset_at=0.0,
            reason="rate_limit_exceeded",
        )

    def test_no_guards_delegates_to_policy_check(self):
        """Guard가 없으면 ThrottlePolicy.check()에 직접 위임."""
        mock_policy = MagicMock()
        mock_policy.check.return_value = self._make_allowed_result()
        mock_policy.current_limit = 100

        facade = AdaptiveThrottleFacade(policy=mock_policy)
        result = facade.check("user_1")

        mock_policy.check.assert_called_once_with("user_1")
        assert result.allowed is True

    def test_guard_pass_continues_to_policy(self):
        """Guard가 통과하면 ThrottlePolicy.check()로 진행."""
        mock_guard = MagicMock()
        mock_guard.check.return_value = GuardResult(allowed=True)

        mock_policy = MagicMock()
        mock_policy.check.return_value = self._make_allowed_result()
        mock_policy.current_limit = 100

        facade = AdaptiveThrottleFacade(policy=mock_policy, guards=[mock_guard])
        result = facade.check("user_1")

        mock_guard.check.assert_called_once()
        mock_policy.check.assert_called_once_with("user_1")
        assert result.allowed is True

    def test_guard_reject_returns_immediately(self):
        """Guard가 거부하면 ThrottlePolicy.check()를 호출하지 않고 즉시 반환."""
        mock_guard = MagicMock()
        mock_guard.check.return_value = GuardResult(
            allowed=False,
            reason="kill_switch_disabled",
        )

        mock_policy = MagicMock()
        mock_policy.current_limit = 100

        facade = AdaptiveThrottleFacade(policy=mock_policy, guards=[mock_guard])
        result = facade.check("user_1")

        assert result.allowed is False
        assert result.reason == "kill_switch_disabled"
        mock_policy.check.assert_not_called()

    def test_guard_reject_stores_to_dlq_when_context_provided(self):
        """Guard 거부 + context 제공 시 DLQ Sink에 저장."""
        mock_guard = MagicMock()
        mock_guard.check.return_value = GuardResult(
            allowed=False,
            reason="emergency_level_3",
        )

        mock_policy = MagicMock()
        mock_policy.current_limit = 100

        mock_sink = MagicMock()

        facade = AdaptiveThrottleFacade(
            policy=mock_policy,
            guards=[mock_guard],
            sinks=[mock_sink],
        )
        ctx = {"service_name": "payment", "domain": "order"}
        facade.check("user_1", context=ctx)

        mock_sink.handle_rejection.assert_called_once_with(ctx, "emergency_level_3")

    def test_guard_reject_no_dlq_when_store_rejection_false(self):
        """store_rejection=False면 Guard 거부 시에도 DLQ 미저장."""
        mock_guard = MagicMock()
        mock_guard.check.return_value = GuardResult(
            allowed=False,
            reason="test",
        )

        mock_policy = MagicMock()
        mock_policy.current_limit = 100
        mock_sink = MagicMock()

        facade = AdaptiveThrottleFacade(
            policy=mock_policy,
            guards=[mock_guard],
            sinks=[mock_sink],
        )
        facade.check("user_1", context={"a": 1}, store_rejection=False)

        mock_sink.handle_rejection.assert_not_called()

    def test_guard_exception_failopen(self):
        """Guard 예외 발생 시 Fail-Open (다음 단계로 진행)."""
        mock_guard = MagicMock()
        mock_guard.check.side_effect = RuntimeError("guard error")
        mock_guard.name = "broken_guard"

        mock_policy = MagicMock()
        mock_policy.check.return_value = self._make_allowed_result()
        mock_policy.current_limit = 100

        facade = AdaptiveThrottleFacade(policy=mock_policy, guards=[mock_guard])
        result = facade.check("user_1")

        # Guard 예외 무시하고 policy.check() 실행됨
        assert result.allowed is True

    def test_multiple_guards_first_reject_wins(self):
        """여러 Guard 중 첫 번째 거부가 최종 결과."""
        guard_1 = MagicMock()
        guard_1.check.return_value = GuardResult(allowed=True)
        guard_2 = MagicMock()
        guard_2.check.return_value = GuardResult(
            allowed=False,
            reason="full_stop",
        )

        mock_policy = MagicMock()
        mock_policy.current_limit = 100

        facade = AdaptiveThrottleFacade(
            policy=mock_policy,
            guards=[guard_1, guard_2],
        )
        result = facade.check("user_1")

        assert result.allowed is False
        assert result.reason == "full_stop"
        mock_policy.check.assert_not_called()

    def test_policy_reject_stores_to_dlq(self):
        """ThrottlePolicy가 거부하면 DLQ에 저장."""
        mock_policy = MagicMock()
        mock_policy.check.return_value = self._make_rejected_result()
        mock_policy.current_limit = 100

        mock_sink = MagicMock()
        facade = AdaptiveThrottleFacade(
            policy=mock_policy,
            sinks=[mock_sink],
        )
        ctx = {"service_name": "test"}
        facade.check("user_1", context=ctx)

        mock_sink.handle_rejection.assert_called_once()

    def test_sink_exception_failopen(self):
        """Sink 예외 시 Fail-Open (결과는 여전히 반환)."""
        mock_policy = MagicMock()
        mock_policy.check.return_value = self._make_rejected_result()
        mock_policy.current_limit = 100

        mock_sink = MagicMock()
        mock_sink.handle_rejection.side_effect = RuntimeError("sink error")

        facade = AdaptiveThrottleFacade(
            policy=mock_policy,
            sinks=[mock_sink],
        )
        ctx = {"service_name": "test"}
        # 예외가 전파되지 않아야 함
        result = facade.check("user_1", context=ctx)
        assert result.allowed is False


# =============================================================================
# record_response() 동작 검증
# =============================================================================


class TestAdaptiveThrottleFacadeRecordResponseBehavior:
    """AdaptiveThrottleFacade.record_response() 동작 검증."""

    def test_delegates_to_policy(self):
        """record_response() 호출이 ThrottlePolicy에 위임되어야 한다."""
        mock_policy = MagicMock()
        facade = AdaptiveThrottleFacade(policy=mock_policy)
        facade.record_response(rtt_ms=45.2)
        mock_policy.record_response.assert_called_once_with(45.2)


# =============================================================================
# current_limit 속성 위임 동작 검증
# =============================================================================


class TestAdaptiveThrottleFacadeCurrentLimitBehavior:
    """current_limit 속성 위임 동작 검증."""

    def test_get_current_limit_delegates_to_policy(self):
        """current_limit getter가 policy에 위임되어야 한다."""
        mock_policy = MagicMock()
        mock_policy.current_limit = 200
        facade = AdaptiveThrottleFacade(policy=mock_policy)
        assert facade.current_limit == 200

    def test_set_current_limit_delegates_to_policy(self):
        """current_limit setter가 policy에 위임되어야 한다."""
        mock_policy = MagicMock()
        facade = AdaptiveThrottleFacade(policy=mock_policy)
        facade.current_limit = 300
        # MagicMock에서 property setter 확인
        assert mock_policy.current_limit == 300


# =============================================================================
# get_stats() 동작 검증
# =============================================================================


class TestAdaptiveThrottleFacadeGetStatsBehavior:
    """get_stats() 통계 반환 동작 검증."""

    def test_includes_policy_info(self):
        """get_stats()에 current_limit, guards 등이 포함되어야 한다."""
        mock_policy = MagicMock()
        mock_policy.current_limit = 100
        mock_policy._config = MagicMock()
        mock_policy._config.min_limit = 10
        mock_policy._config.max_limit = 500
        mock_policy.gradient_frozen = False

        guard_1 = MagicMock()
        guard_1.name = "governance"

        facade = AdaptiveThrottleFacade(
            policy=mock_policy,
            guards=[guard_1],
        )
        stats = facade.get_stats()

        assert stats["current_limit"] == 100
        assert stats["min_limit"] == 10
        assert stats["max_limit"] == 500
        assert stats["gradient_frozen"] is False
        assert "governance" in stats["guards"]
