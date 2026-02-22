"""
AdaptiveThrottle.check() 거부 시 DLQ 자동 저장 단위 테스트.

테스트 대상: selfhealing.services.throttle.adaptive.AdaptiveThrottle.check()
  - context + store_rejection 파라미터 추가
  - _auto_store_rejection_to_dlq() Fail-Open 위임

테스트 시나리오:
1. 거부 + context 제공 시 DLQ 자동 저장 호출
2. 거부 + context=None 시 DLQ 저장 스킵
3. 거부 + store_rejection=False 시 DLQ 저장 스킵
4. 허용 시 DLQ 저장 스킵
5. Error Budget Critical non_essential 거부 시 DLQ 자동 저장
6. _auto_store_rejection_to_dlq 예외 시 Fail-Open (Throttle 결과 정상 반환)
7. 기존 호출 하위 호환성 (context/store_rejection 없이 호출)
"""

from unittest.mock import MagicMock

from selfhealing.services.throttle.config import ThrottleConfig


def _make_throttle(**overrides):
    """테스트용 AdaptiveThrottle 생성 헬퍼."""
    from selfhealing.services.throttle.adaptive import AdaptiveThrottle

    defaults = {
        "initial_limit": 100,
        "sample_interval_ms": 50,
    }
    defaults.update(overrides)
    config = ThrottleConfig(**defaults)
    return AdaptiveThrottle(config)


class TestCheckAutoStoreOnRejection:
    """check() 거부 시 context 기반 DLQ 자동 저장 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_rejected_with_context_calls_auto_store(self):
        """거부 + context 제공 시 store_throttle_rejection_to_dlq가 호출된다."""
        throttle = _make_throttle(initial_limit=1)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        # 동일 key로 limit 소진 (limit=1이므로 첫 요청 허용, 두 번째 거부)
        throttle.check(key="same_key", tier_id="standard")

        context = {"domain": "payment", "tier_id": "critical"}
        result = throttle.check(
            key="same_key",
            tier_id="standard",
            context=context,
            store_rejection=True,
        )

        assert result.allowed is False
        mock_dlq.store_failure.assert_called_once()

    def test_rejected_without_context_skips_auto_store(self):
        """거부 + context=None 시 DLQ 저장을 호출하지 않는다."""
        throttle = _make_throttle(initial_limit=1)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        # 동일 key로 limit 소진
        throttle.check(key="same_key", tier_id="standard")

        result = throttle.check(
            key="same_key",
            tier_id="standard",
            context=None,
            store_rejection=True,
        )

        assert result.allowed is False
        mock_dlq.store_failure.assert_not_called()

    def test_rejected_with_store_rejection_false_skips_auto_store(self):
        """거부 + store_rejection=False 시 DLQ 저장을 호출하지 않는다."""
        throttle = _make_throttle(initial_limit=1)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        # 동일 key로 limit 소진
        throttle.check(key="same_key", tier_id="standard")

        context = {"domain": "payment", "tier_id": "critical"}
        result = throttle.check(
            key="same_key",
            tier_id="standard",
            context=context,
            store_rejection=False,
        )

        assert result.allowed is False
        mock_dlq.store_failure.assert_not_called()

    def test_allowed_request_skips_auto_store(self):
        """허용된 요청은 DLQ 저장을 호출하지 않는다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "payment", "tier_id": "critical"}
        result = throttle.check(
            key="test_request",
            tier_id="standard",
            context=context,
            store_rejection=True,
        )

        assert result.allowed is True
        mock_dlq.store_failure.assert_not_called()


class TestCheckErrorBudgetCriticalAutoStore:
    """Error Budget Critical 거부 시 DLQ 자동 저장 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_error_budget_critical_non_essential_stores_to_dlq(self):
        """Error Budget Critical + non_essential 거부 시 _auto_store_rejection_to_dlq 호출."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        # Error Budget Critical 상태 강제 설정
        throttle._error_budget_limit_reduction_active = True
        throttle._error_budget_multiplier = 0.3  # <= 0.5

        # non_essential 티어지만 critical로 context 설정하여 저장 확인
        # (non_essential tier_id context는 store_throttle_rejection_to_dlq에서 필터링됨)
        context = {"domain": "log", "tier_id": "critical"}
        result = throttle.check(
            key="test_request",
            tier_id="non_essential",
            context=context,
            store_rejection=True,
        )

        assert result.allowed is False
        assert result.reason == "error_budget_critical_non_essential_blocked"
        mock_dlq.store_failure.assert_called_once()

    def test_error_budget_critical_without_context_skips_dlq(self):
        """Error Budget Critical 거부 + context=None 시 DLQ 저장 스킵."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        throttle._error_budget_limit_reduction_active = True
        throttle._error_budget_multiplier = 0.3

        result = throttle.check(
            key="test_request",
            tier_id="non_essential",
            context=None,
        )

        assert result.allowed is False
        mock_dlq.store_failure.assert_not_called()


class TestAutoStoreDlqFailOpen:
    """_auto_store_rejection_to_dlq Fail-Open 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_dlq_exception_does_not_affect_check_result(self):
        """DLQ 저장 예외 발생 시에도 check() 결과는 정상 반환된다."""
        throttle = _make_throttle(initial_limit=1)
        mock_dlq = MagicMock()
        mock_dlq.store_failure.side_effect = RuntimeError("DLQ unavailable")
        throttle._dlq_service = mock_dlq

        # 동일 key로 limit 소진
        throttle.check(key="same_key", tier_id="standard")

        context = {"domain": "payment", "tier_id": "critical"}
        result = throttle.check(
            key="same_key",
            tier_id="standard",
            context=context,
            store_rejection=True,
        )

        # Fail-Open: 예외가 있어도 check() 결과는 정상
        assert result.allowed is False
        assert result.limit > 0 or result.limit == 0

    def test_auto_store_catches_any_exception(self):
        """_auto_store_rejection_to_dlq는 모든 예외를 잡아서 Fail-Open."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        mock_dlq.store_failure.side_effect = Exception("unexpected error")
        throttle._dlq_service = mock_dlq

        # 예외 없이 실행 완료
        throttle._auto_store_rejection_to_dlq(
            context={"domain": "test", "tier_id": "critical"},
            rejection_reason="capacity_exceeded",
        )


class TestCheckBackwardCompatibility:
    """check() 하위 호환성 테스트 — context/store_rejection 없이 기존처럼 동작."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_check_without_new_params_works(self):
        """기존 check(key, tier_id) 호출이 정상 동작한다."""
        throttle = _make_throttle(initial_limit=100)

        result = throttle.check(key="test_request", tier_id="standard")

        assert result.allowed is True

    def test_check_with_only_key_works(self):
        """check(key)만으로 호출해도 정상 동작한다."""
        throttle = _make_throttle(initial_limit=100)

        result = throttle.check(key="test_request")

        assert result.allowed is True

    def test_rejected_without_context_does_not_crash(self):
        """context=None 기본값으로 거부 시 예외 없이 동작한다."""
        throttle = _make_throttle(initial_limit=1)

        # 동일 key로 limit 소진
        throttle.check(key="same_key")

        result = throttle.check(key="same_key")

        assert result.allowed is False
