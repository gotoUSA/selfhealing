"""
AdaptiveThrottle Load Shedding 연동 단위 테스트.

테스트 대상:
1. __init__: _shedding_suggested_limit, _shedding_affected_services 초기화
2. reset_all: Shedding 상태 초기화
3. conservative_limit: _shedding_suggested_limit 참여 (Min-Winner)
4. _subscribe_load_shedding_events: EventBus 구독 등록
5. _handle_shedding_changed: Shedding 활성화/비활성화 이벤트 처리
6. check(): context.service_id 기반 Load Shedding limit 적용
7. shedding_compensation_factor 보상 계수 적용
"""

import pytest
from unittest.mock import patch, MagicMock

from selfhealing.services.throttle.adaptive import (
    AdaptiveThrottle,
    reset_adaptive_throttle,
)
from selfhealing.services.throttle.config import ThrottleConfig
from selfhealing.services.event_bus.bus import EventType, SelfHealingEvent, EventPriority
from selfhealing.settings.throttle import ThrottleSettings


class TestSheddingStateInitialization:
    """__init__ 시 Load Shedding 연동 상태 초기화 검증."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_shedding_suggested_limit_initialized_to_max_limit(self):
        """_shedding_suggested_limit 초기값 = config.max_limit."""
        config = ThrottleConfig(max_limit=500)
        throttle = AdaptiveThrottle(config)
        assert throttle._shedding_suggested_limit == config.max_limit

    def test_shedding_affected_services_initialized_empty(self):
        """_shedding_affected_services 초기값 = 빈 set."""
        throttle = AdaptiveThrottle(ThrottleConfig())
        assert throttle._shedding_affected_services == set()
        assert isinstance(throttle._shedding_affected_services, set)


class TestSheddingResetAll:
    """reset_all()에서 Load Shedding 상태 초기화 검증."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_reset_all_clears_shedding_suggested_limit(self):
        """reset_all() 후 _shedding_suggested_limit = max_limit."""
        config = ThrottleConfig(max_limit=500)
        throttle = AdaptiveThrottle(config)

        # Shedding 상태 변경
        throttle._shedding_suggested_limit = 100
        throttle._shedding_affected_services = {"order-api"}

        throttle.reset_all()

        assert throttle._shedding_suggested_limit == config.max_limit
        assert throttle._shedding_affected_services == set()


class TestConservativeLimitWithShedding:
    """conservative_limit에 _shedding_suggested_limit 참여 검증."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_shedding_limit_participates_in_min_winner(self):
        """_shedding_suggested_limit이 Min-Winner 정책에 참여."""
        config = ThrottleConfig(initial_limit=100, max_limit=500)
        throttle = AdaptiveThrottle(config)
        throttle._conservative_enabled = True

        # Shedding limit을 다른 suggested limit보다 낮게 설정
        throttle._shedding_suggested_limit = 50
        throttle._rtt_suggested_limit = 200
        throttle._429_suggested_limit = 500

        assert throttle.conservative_limit == 50

    def test_shedding_max_limit_does_not_affect_min_winner(self):
        """_shedding_suggested_limit = max_limit이면 min()에 영향 없음."""
        config = ThrottleConfig(initial_limit=100, max_limit=500)
        throttle = AdaptiveThrottle(config)
        throttle._conservative_enabled = True

        # 기본값(max_limit)이면 min()에 영향 없어야 함
        assert throttle._shedding_suggested_limit == config.max_limit

        throttle._rtt_suggested_limit = 100
        throttle._429_suggested_limit = 500

        # rtt_suggested_limit이 최소값이어야 함
        assert throttle.conservative_limit == throttle._rtt_suggested_limit

    def test_conservative_disabled_ignores_shedding(self):
        """conservative 비활성화 시 _shedding_suggested_limit 무시."""
        config = ThrottleConfig(initial_limit=100, max_limit=500)
        throttle = AdaptiveThrottle(config)
        throttle._conservative_enabled = False

        throttle._shedding_suggested_limit = 10

        # conservative 비활성화 시 _current_limit 그대로 반환
        assert throttle.conservative_limit == throttle._current_limit


class TestHandleSheddingChanged:
    """_handle_shedding_changed() 이벤트 핸들러 검증."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def _make_event(self, new_level, traffic_limit=100.0, affected_services=None):
        """테스트용 SelfHealingEvent 생성."""
        return SelfHealingEvent(
            event_type=EventType.LOAD_SHEDDING_LEVEL_CHANGED,
            data={
                "new_level": new_level,
                "traffic_limit": traffic_limit,
                "affected_services": affected_services or [],
            },
            source="load_shedding_manager",
            priority=EventPriority.HIGH,
        )

    def test_shedding_activated_sets_affected_services(self):
        """Shedding 활성화 시 affected_services 설정."""
        config = ThrottleConfig(max_limit=500)
        throttle = AdaptiveThrottle(config)

        event = self._make_event(
            new_level=0,
            traffic_limit=50.0,
            affected_services=["order-api", "review-api"],
        )
        throttle._handle_shedding_changed(event)

        assert throttle._shedding_affected_services == {"order-api", "review-api"}

    def test_shedding_activated_applies_compensation_factor(self):
        """Shedding 활성화 시 보상 계수가 적용되어 이중 차단 완화."""
        settings = ThrottleSettings()
        compensation_factor = settings.shedding_compensation_factor
        config = ThrottleConfig(max_limit=500, min_limit=10)
        throttle = AdaptiveThrottle(config)

        event = self._make_event(
            new_level=0,
            traffic_limit=50.0,
            affected_services=["review-api"],
        )
        throttle._handle_shedding_changed(event)

        raw_limit = int(config.max_limit * (50.0 / 100.0))  # 250
        compensated = min(config.max_limit, int(raw_limit * compensation_factor))
        expected = max(compensated, config.min_limit)

        assert throttle._shedding_suggested_limit == expected

    def test_shedding_activated_respects_min_limit(self):
        """Shedding 활성화 시 min_limit 이상 보장."""
        config = ThrottleConfig(max_limit=500, min_limit=10)
        throttle = AdaptiveThrottle(config)

        # 극단적으로 낮은 traffic_limit
        event = self._make_event(
            new_level=2,
            traffic_limit=0.1,
            affected_services=["review-api"],
        )
        throttle._handle_shedding_changed(event)

        assert throttle._shedding_suggested_limit >= config.min_limit

    def test_shedding_activated_respects_max_limit(self):
        """Shedding 활성화 시 max_limit 초과 불가."""
        config = ThrottleConfig(max_limit=500, min_limit=10)
        throttle = AdaptiveThrottle(config)

        # traffic_limit 100%에 compensation_factor 적용해도 max_limit 초과 불가
        event = self._make_event(
            new_level=0,
            traffic_limit=100.0,
            affected_services=["review-api"],
        )
        throttle._handle_shedding_changed(event)

        assert throttle._shedding_suggested_limit <= config.max_limit

    def test_shedding_deactivated_restores_max_limit(self):
        """Shedding 해제 시 shedding_suggested_limit = max_limit 복구."""
        config = ThrottleConfig(max_limit=500)
        throttle = AdaptiveThrottle(config)

        # 먼저 활성화
        activate_event = self._make_event(
            new_level=0,
            traffic_limit=50.0,
            affected_services=["review-api"],
        )
        throttle._handle_shedding_changed(activate_event)
        assert throttle._shedding_suggested_limit < config.max_limit

        # 해제 (new_level < 0)
        deactivate_event = self._make_event(new_level=-1)
        throttle._handle_shedding_changed(deactivate_event)

        assert throttle._shedding_suggested_limit == config.max_limit
        assert throttle._shedding_affected_services == set()

    def test_shedding_deactivated_starts_recovery_dampening(self):
        """Shedding 해제 시 다른 제한 미활성화면 Recovery Dampening 시작."""
        config = ThrottleConfig(max_limit=500)
        throttle = AdaptiveThrottle(config)
        throttle._emergency_mode_active = False
        throttle._429_reduction_active = False

        with patch.object(throttle, "start_recovery_dampening") as mock_dampening:
            event = self._make_event(new_level=-1)
            throttle._handle_shedding_changed(event)

            mock_dampening.assert_called_once_with(apply_jitter=True)

    def test_shedding_deactivated_skips_dampening_when_emergency_active(self):
        """Emergency 활성 시 Shedding 해제해도 Dampening 미시작."""
        config = ThrottleConfig(max_limit=500)
        throttle = AdaptiveThrottle(config)
        throttle._emergency_mode_active = True

        with patch.object(throttle, "start_recovery_dampening") as mock_dampening:
            event = self._make_event(new_level=-1)
            throttle._handle_shedding_changed(event)

            mock_dampening.assert_not_called()

    def test_shedding_deactivated_skips_dampening_when_429_active(self):
        """429 감소 활성 시 Shedding 해제해도 Dampening 미시작."""
        config = ThrottleConfig(max_limit=500)
        throttle = AdaptiveThrottle(config)
        throttle._429_reduction_active = True

        with patch.object(throttle, "start_recovery_dampening") as mock_dampening:
            event = self._make_event(new_level=-1)
            throttle._handle_shedding_changed(event)

            mock_dampening.assert_not_called()


class TestCheckWithServiceIdShedding:
    """check()에서 context.service_id 기반 Load Shedding limit 분기 검증."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_shedding_affected_service_uses_reduced_limit(self):
        """Shedding 대상 service_id의 요청은 감소된 limit 적용."""
        config = ThrottleConfig(initial_limit=100, max_limit=500, window_seconds=60)
        throttle = AdaptiveThrottle(config)

        throttle._shedding_affected_services = {"order-api"}
        throttle._shedding_suggested_limit = 5  # 매우 낮은 limit

        # 대상 서비스: shedding_suggested_limit 적용 (동일 key 반복)
        context = {"service_id": "order-api"}
        results = [throttle.check("same_key", context=context) for _ in range(10)]
        allowed = sum(1 for r in results if r.allowed)
        assert allowed == 5  # shedding limit에 의해 제한

    def test_non_affected_service_uses_full_limit(self):
        """Shedding 비대상 service_id의 요청은 기본 limit 적용."""
        config = ThrottleConfig(initial_limit=100, max_limit=500, window_seconds=60)
        throttle = AdaptiveThrottle(config)

        throttle._shedding_affected_services = {"order-api"}
        throttle._shedding_suggested_limit = 5

        # 비대상 서비스: 기본 limit 적용
        context = {"service_id": "payment-api"}
        results = [throttle.check(f"key_{i}", context=context) for i in range(10)]
        allowed = sum(1 for r in results if r.allowed)
        # 기본 initial_limit=100이므로 10개 모두 허용
        assert allowed == 10

    def test_no_context_uses_full_limit(self):
        """context 없는 요청은 기본 limit 적용."""
        config = ThrottleConfig(initial_limit=100, max_limit=500, window_seconds=60)
        throttle = AdaptiveThrottle(config)

        throttle._shedding_affected_services = {"order-api"}
        throttle._shedding_suggested_limit = 5

        results = [throttle.check(f"key_{i}") for i in range(10)]
        allowed = sum(1 for r in results if r.allowed)
        assert allowed == 10

    def test_no_service_id_in_context_uses_full_limit(self):
        """context에 service_id가 없으면 기본 limit 적용."""
        config = ThrottleConfig(initial_limit=100, max_limit=500, window_seconds=60)
        throttle = AdaptiveThrottle(config)

        throttle._shedding_affected_services = {"order-api"}
        throttle._shedding_suggested_limit = 5

        context = {"domain": "shopping"}  # service_id 없음
        results = [throttle.check(f"key_{i}", context=context) for i in range(10)]
        allowed = sum(1 for r in results if r.allowed)
        assert allowed == 10

    def test_empty_affected_services_uses_full_limit(self):
        """affected_services 비어있으면 모든 요청에 기본 limit 적용."""
        config = ThrottleConfig(initial_limit=100, max_limit=500, window_seconds=60)
        throttle = AdaptiveThrottle(config)

        throttle._shedding_affected_services = set()
        throttle._shedding_suggested_limit = 5  # 이 값은 무의미

        context = {"service_id": "order-api"}
        results = [throttle.check(f"key_{i}", context=context) for i in range(10)]
        allowed = sum(1 for r in results if r.allowed)
        assert allowed == 10

    def test_shedding_limit_swap_restores_original_limit(self):
        """임시 swap 후 원래 _current_limit이 복구됨."""
        config = ThrottleConfig(initial_limit=100, max_limit=500, window_seconds=60)
        throttle = AdaptiveThrottle(config)

        original_limit = throttle._current_limit
        throttle._shedding_affected_services = {"order-api"}
        throttle._shedding_suggested_limit = 5

        context = {"service_id": "order-api"}
        throttle.check("key_1", context=context)

        # 원래 limit 복구 확인
        assert throttle._current_limit == original_limit


class TestSubscribeLoadSheddingEvents:
    """_subscribe_load_shedding_events() 구독 등록 검증."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_subscribes_to_load_shedding_level_changed(self):
        """LOAD_SHEDDING_LEVEL_CHANGED 이벤트 구독 등록."""
        mock_bus = MagicMock()

        with patch("selfhealing.services.event_bus.get_event_bus", return_value=mock_bus):
            throttle = AdaptiveThrottle(ThrottleConfig())
            # __init__에서 _subscribe_load_shedding_events() 호출됨

            # subscribe 호출 확인 (여러 subscribe 중 LOAD_SHEDDING 확인)
            subscribe_calls = mock_bus.subscribe.call_args_list
            shedding_calls = [c for c in subscribe_calls if c[0][0] == EventType.LOAD_SHEDDING_LEVEL_CHANGED]
            assert len(shedding_calls) == 1

    def test_subscribe_fails_gracefully_on_import_error(self):
        """EventBus import 실패 시 예외 발생하지 않음."""
        # _subscribe_load_shedding_events 내부의 import를 실패시킴
        with patch.dict("sys.modules", {"selfhealing.services.event_bus": None}):
            # 예외가 발생하지 않아야 함 (Fail-Open)
            throttle = AdaptiveThrottle(ThrottleConfig())
            assert throttle._shedding_suggested_limit == throttle.config.max_limit
