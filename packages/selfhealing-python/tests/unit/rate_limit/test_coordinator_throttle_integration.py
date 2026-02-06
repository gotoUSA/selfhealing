"""
RateLimitCoordinator-AdaptiveThrottle 통합 단위 테스트.

테스트 대상:
1. 429 발생 시 Throttle limit 감소
2. 연속 429 점진적 감소 (20%/40%/50%)
3. 디바운싱 윈도우 중복 이벤트 방지
4. Cooldown 대기 및 종료 감지
5. Canary Request 모드
6. Conservative Limit (Min-Winner 정책)
7. Priority-aware CRITICAL 티어 보호
8. RateLimitEscalationHandler 임계치 에스컬레이션
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time


# =============================================================================
# 테스트용 InMemoryRateLimitStorage Mock
# =============================================================================


@dataclass
class MockRateLimitState:
    """Mock Rate Limit State."""

    key: str
    is_in_cooldown: bool = False
    remaining_cooldown: float = 0.0
    consecutive_429s: int = 0
    cooldown_until: float = 0.0


class MockInMemoryRateLimitStorage:
    """단위 테스트용 InMemory Rate Limit Storage Mock."""

    def __init__(self):
        self._states: dict[str, MockRateLimitState] = {}
        self._lock = threading.Lock()

    def get_state(self, key: str) -> MockRateLimitState:
        """현재 상태 조회."""
        with self._lock:
            if key not in self._states:
                self._states[key] = MockRateLimitState(key=key)

            state = self._states[key]
            now = time.time()

            # Cooldown 상태 계산
            if state.cooldown_until > now:
                state.is_in_cooldown = True
                state.remaining_cooldown = state.cooldown_until - now
            else:
                state.is_in_cooldown = False
                state.remaining_cooldown = 0.0

            return state

    def set_cooldown(self, key: str, cooldown_until: float) -> None:
        """Cooldown 설정."""
        with self._lock:
            if key not in self._states:
                self._states[key] = MockRateLimitState(key=key)
            self._states[key].cooldown_until = cooldown_until

    def increment_consecutive_429s(self, key: str) -> int:
        """연속 429 횟수 증가."""
        with self._lock:
            if key not in self._states:
                self._states[key] = MockRateLimitState(key=key)
            self._states[key].consecutive_429s += 1
            return self._states[key].consecutive_429s

    def reset_consecutive_429s(self, key: str) -> None:
        """연속 429 횟수 리셋."""
        with self._lock:
            if key in self._states:
                self._states[key].consecutive_429s = 0


# =============================================================================
# RateLimitCoordinator 단위 테스트
# =============================================================================


class TestRateLimitCoordinatorEventEmission:
    """RateLimitCoordinator 이벤트 발행 테스트."""

    def test_on_rate_limited_emits_429_event(self):
        """on_rate_limited() 호출 시 RATE_LIMIT_429 이벤트 발행."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            base_delay=5.0,
            debounce_window_seconds=5.0,
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        emitted_events = []

        def capture_emit(event_type, data, source, priority):
            emitted_events.append(
                {
                    "event_type": str(event_type),
                    "data": data,
                    "source": source,
                }
            )
            return 1

        # EventBus mock - patch at the point of import inside _emit_rate_limit_event
        with patch("selfhealing.services.event_bus.get_event_bus") as mock_get_bus:
            mock_bus = MagicMock()
            mock_bus.emit = capture_emit
            mock_get_bus.return_value = mock_bus

            # 429 발생
            coordinator.on_rate_limited("payment_api", retry_after=5)

        # RATE_LIMIT_429 이벤트 발행 확인
        rate_limit_events = [e for e in emitted_events if "RATE_LIMIT_429" in e["event_type"]]
        assert len(rate_limit_events) >= 1

        event_data = rate_limit_events[0]["data"]
        assert event_data["key"] == "payment_api"
        assert event_data["consecutive_429s"] == 1

    def test_on_rate_limited_calculates_exponential_backoff(self):
        """연속 429 시 지수 백오프 계산 확인."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            base_delay=1.0,
            default_retry_after=1.0,  # retry_after=None일 때 사용되는 값
            backoff_multiplier=2.0,
            max_delay=60.0,
            jitter_percent=0.0,  # 테스트를 위해 jitter 비활성화
            debounce_window_seconds=0.0,  # 디바운싱 비활성화
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 첫 번째 429: 기본 딜레이 (default_retry_after * 2^0 = 1.0)
        delay1 = coordinator.on_rate_limited("test_api")
        assert delay1 == pytest.approx(1.0, rel=0.1)

        # 두 번째 429: 2배 (1.0 * 2^1 = 2.0)
        delay2 = coordinator.on_rate_limited("test_api")
        assert delay2 == pytest.approx(2.0, rel=0.1)

        # 세 번째 429: 4배 (1.0 * 2^2 = 4.0)
        delay3 = coordinator.on_rate_limited("test_api")
        assert delay3 == pytest.approx(4.0, rel=0.1)


class TestRateLimitCoordinatorDebouncing:
    """RateLimitCoordinator 디바운싱 테스트."""

    @freeze_time("2026-02-06 12:00:00")
    def test_debounce_window_prevents_duplicate_events(self):
        """디바운싱 윈도우 내 중복 이벤트 방지."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            debounce_window_seconds=5.0,
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 첫 번째 이벤트 - 발행 허용
        assert coordinator._should_emit_event("test_api") is True

        # 즉시 두 번째 - 디바운싱됨
        assert coordinator._should_emit_event("test_api") is False

    @freeze_time("2026-02-06 12:00:00")
    def test_debounce_window_expires_after_timeout(self):
        """디바운싱 윈도우 만료 후 이벤트 발행 허용."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            debounce_window_seconds=5.0,
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 첫 번째 이벤트
        assert coordinator._should_emit_event("test_api") is True

        # 6초 후로 시간 이동 (윈도우 만료)
        with freeze_time("2026-02-06 12:00:06"):
            # 윈도우 지남 - 발행 허용
            assert coordinator._should_emit_event("test_api") is True

    @freeze_time("2026-02-06 12:00:00")
    def test_debounce_tracks_keys_independently(self):
        """서로 다른 key는 독립적으로 디바운싱."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            debounce_window_seconds=5.0,
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # API A 이벤트
        assert coordinator._should_emit_event("api_a") is True

        # API B 이벤트 - 별도로 허용
        assert coordinator._should_emit_event("api_b") is True

        # API A 중복 - 디바운싱
        assert coordinator._should_emit_event("api_a") is False


class TestRateLimitCoordinatorCanary:
    """RateLimitCoordinator Canary Request 테스트."""

    def test_wait_if_needed_returns_canary_after_429(self):
        """429 발생 후 첫 요청은 Canary 모드."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig()
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 이전에 429가 발생한 상태 시뮬레이션
        storage.increment_consecutive_429s("test_api")
        # Cooldown은 이미 종료됨 (cooldown_until = 0)

        # 첫 요청 - Canary 모드
        result = coordinator.wait_if_needed("test_api")
        assert result.is_canary is True

    def test_on_success_clears_canary_state(self):
        """성공 후 Canary 상태 해제."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig()
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # Canary 상태 설정
        storage.increment_consecutive_429s("test_api")
        result1 = coordinator.wait_if_needed("test_api")
        assert result1.is_canary is True

        # 성공 처리
        coordinator.on_success("test_api")

        # 다음 요청 - Canary 아님
        result2 = coordinator.wait_if_needed("test_api")
        assert result2.is_canary is False


class TestRateLimitCoordinatorCooldown:
    """RateLimitCoordinator Cooldown 테스트."""

    def test_cooldown_state_detection(self):
        """Cooldown 상태 감지 테스트."""
        storage = MockInMemoryRateLimitStorage()

        # 미래 시점에 cooldown 설정 (현재 시간 + 10초)
        cooldown_until = time.time() + 10.0
        storage.set_cooldown("test_api", cooldown_until)
        storage.increment_consecutive_429s("test_api")

        # Cooldown 중
        state = storage.get_state("test_api")
        assert state.is_in_cooldown is True
        assert state.remaining_cooldown > 0
        assert state.remaining_cooldown <= 10.0

    def test_cooldown_expired(self):
        """Cooldown 만료 테스트."""
        storage = MockInMemoryRateLimitStorage()

        # 과거 시점에 cooldown 설정 (이미 만료)
        cooldown_until = time.time() - 5.0
        storage.set_cooldown("test_api", cooldown_until)

        # Cooldown 종료
        state = storage.get_state("test_api")
        assert state.is_in_cooldown is False
        assert state.remaining_cooldown == 0.0


# =============================================================================
# AdaptiveThrottle 429 연동 테스트
# =============================================================================


class TestAdaptiveThrottle429Integration:
    """AdaptiveThrottle 429 이벤트 연동 테스트."""

    def setup_method(self):
        """테스트 전 AdaptiveThrottle 상태 초기화."""
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

            reset_adaptive_throttle()
        except ImportError:
            pass

    def teardown_method(self):
        """테스트 후 AdaptiveThrottle 상태 초기화."""
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_handle_rate_limit_429_reduces_limit(self):
        """429 이벤트 수신 시 limit 감소 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        initial_limit = throttle.current_limit
        assert initial_limit == 100

        # 429 이벤트 직접 핸들러 호출
        throttle._handle_rate_limit_429(
            {
                "key": "test_api",
                "consecutive_429s": 1,
                "cooldown_until": time.time() + 10,
            }
        )

        # 20% 감소 → 80
        assert throttle.current_limit == 80

    def test_consecutive_429_progressive_reduction(self):
        """연속 429 시 점진적 감소 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        limits = [throttle.current_limit]  # [100]

        # 연속 429 발생
        for i in range(1, 4):
            throttle._handle_rate_limit_429(
                {
                    "key": "test_api",
                    "consecutive_429s": i,
                    "cooldown_until": time.time() + (i * 10),
                }
            )
            limits.append(throttle.current_limit)

        # 점진적 감소 확인
        # 1회: 100 * 0.8 = 80
        # 2회: 80 * 0.6 = 48
        # 3회 이상: 48 * 0.5 = 24
        assert limits == [100, 80, 48, 24]

    def test_429_respects_min_limit(self):
        """429 감소가 min_limit 이상 유지."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=20, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # 여러 번 429 - min_limit 이하로 내려가지 않음
        for i in range(1, 10):
            throttle._handle_rate_limit_429(
                {
                    "key": "test_api",
                    "consecutive_429s": i,
                    "cooldown_until": time.time() + 10,
                }
            )

        assert throttle.current_limit >= 10


class TestAdaptiveThrottleConservativeLimit:
    """AdaptiveThrottle Conservative Limit (Min-Winner) 정책 테스트."""

    def setup_method(self):
        """테스트 전 상태 초기화."""
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_conservative_limit_selects_lower_value(self):
        """RTT와 429 limit 중 낮은 값 선택."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # RTT 기반 limit: 80
        throttle._rtt_suggested_limit = 80
        # 429 기반 limit: 60
        throttle._429_suggested_limit = 60

        # Min-Winner: 60
        assert throttle.conservative_limit == 60

    def test_conservative_limit_when_429_higher(self):
        """429 limit이 더 높으면 RTT limit 선택."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # RTT 기반 limit이 더 낮음
        throttle._rtt_suggested_limit = 50
        throttle._429_suggested_limit = 100

        # Min-Winner: 50
        assert throttle.conservative_limit == 50


class TestAdaptiveThrottlePriorityProtection:
    """AdaptiveThrottle Priority-aware CRITICAL 보호 테스트."""

    def setup_method(self):
        """테스트 전 상태 초기화."""
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_critical_tier_uses_pre_429_limit_during_reduction(self):
        """429 감소 상태에서 CRITICAL 티어는 이전 limit 사용."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # 429로 limit 감소
        throttle._handle_rate_limit_429(
            {
                "key": "test_api",
                "consecutive_429s": 3,
                "cooldown_until": time.time() + 10,
            }
        )

        # 감소된 limit 확인
        reduced_limit = throttle.current_limit
        assert reduced_limit < 100

        # CRITICAL 티어는 429 감소 전 limit 기준
        assert throttle._429_reduction_active is True
        assert throttle._limit_before_429 == 100


class TestAdaptiveThrottleCooldownEndRecovery:
    """AdaptiveThrottle COOLDOWN_END 이벤트 복구 테스트."""

    def setup_method(self):
        """테스트 전 상태 초기화."""
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_handle_cooldown_end_clears_429_state(self):
        """COOLDOWN_END 시 429 상태 초기화."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # 429 상태 설정
        throttle._handle_rate_limit_429(
            {
                "key": "test_api",
                "consecutive_429s": 2,
                "cooldown_until": time.time() + 10,
            }
        )

        assert "test_api" in throttle._rate_limit_keys
        assert throttle._429_reduction_active is True

        # COOLDOWN_END 처리
        throttle._handle_cooldown_end(
            {
                "key": "test_api",
                "cooldown_ended_at": time.time(),
            }
        )

        assert "test_api" not in throttle._rate_limit_keys
        assert throttle._429_reduction_active is False


# =============================================================================
# RateLimitEscalationHandler 테스트
# =============================================================================


class TestRateLimitEscalationHandler:
    """RateLimitEscalationHandler 에스컬레이션 테스트."""

    def test_escalation_triggered_at_threshold(self):
        """임계치 도달 시 에스컬레이션 발동."""
        from selfhealing.meta.rate_limit_escalation import RateLimitEscalationHandler

        # Mock EscalationManager
        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.channels_sent = ["pagerduty"]
        mock_manager.escalate.return_value = mock_result

        handler = RateLimitEscalationHandler(
            escalation_manager=mock_manager,
            threshold=5,
        )

        # 임계치(5) 미만 - 에스컬레이션 없음
        handler._handle_rate_limit_429(
            {
                "key": "payment_api",
                "consecutive_429s": 3,
                "cooldown_until": time.time() + 10,
            }
        )
        mock_manager.escalate.assert_not_called()

        # 임계치(5) 도달 - 에스컬레이션 발동
        handler._handle_rate_limit_429(
            {
                "key": "payment_api",
                "consecutive_429s": 5,
                "cooldown_until": time.time() + 10,
            }
        )
        mock_manager.escalate.assert_called_once()

        # EscalationEvent 검증
        call_args = mock_manager.escalate.call_args
        event = call_args[0][0]
        assert "payment_api" in event.title
        assert event.details["consecutive_429s"] == 5

    def test_no_duplicate_escalation_for_same_key(self):
        """동일 key에 대한 중복 에스컬레이션 방지."""
        from selfhealing.meta.rate_limit_escalation import RateLimitEscalationHandler

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.channels_sent = ["pagerduty"]
        mock_manager.escalate.return_value = mock_result

        handler = RateLimitEscalationHandler(
            escalation_manager=mock_manager,
            threshold=5,
        )

        # 첫 번째 에스컬레이션
        handler._handle_rate_limit_429(
            {
                "key": "payment_api",
                "consecutive_429s": 10,
            }
        )

        # 두 번째 시도 - 동일 key이므로 스킵
        handler._handle_rate_limit_429(
            {
                "key": "payment_api",
                "consecutive_429s": 15,
            }
        )

        # 한 번만 호출됨
        assert mock_manager.escalate.call_count == 1

    def test_reset_escalation_allows_new_escalation(self):
        """에스컬레이션 리셋 후 새로운 에스컬레이션 허용."""
        from selfhealing.meta.rate_limit_escalation import RateLimitEscalationHandler

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.channels_sent = ["pagerduty"]
        mock_manager.escalate.return_value = mock_result

        handler = RateLimitEscalationHandler(
            escalation_manager=mock_manager,
            threshold=5,
        )

        # 첫 번째 에스컬레이션
        handler._handle_rate_limit_429(
            {
                "key": "payment_api",
                "consecutive_429s": 10,
            }
        )
        assert mock_manager.escalate.call_count == 1

        # 리셋
        handler.reset_escalation("payment_api")

        # 두 번째 에스컬레이션 - 허용됨
        handler._handle_rate_limit_429(
            {
                "key": "payment_api",
                "consecutive_429s": 10,
            }
        )
        assert mock_manager.escalate.call_count == 2

    def test_escalated_keys_property(self):
        """escalated_keys 속성 확인."""
        from selfhealing.meta.rate_limit_escalation import RateLimitEscalationHandler

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.channels_sent = ["pagerduty"]
        mock_manager.escalate.return_value = mock_result

        handler = RateLimitEscalationHandler(
            escalation_manager=mock_manager,
            threshold=5,
        )

        handler._handle_rate_limit_429(
            {
                "key": "api_a",
                "consecutive_429s": 10,
            }
        )
        handler._handle_rate_limit_429(
            {
                "key": "api_b",
                "consecutive_429s": 10,
            }
        )

        assert "api_a" in handler.escalated_keys
        assert "api_b" in handler.escalated_keys

        # 읽기 전용 (frozenset)
        assert isinstance(handler.escalated_keys, frozenset)


# =============================================================================
# DistributedRateLimitChannel 테스트
# =============================================================================


class TestDistributedRateLimitChannel:
    """DistributedRateLimitChannel 단위 테스트."""

    def test_broadcast_rate_limit_429_publishes_to_kafka(self):
        """broadcast_rate_limit_429()이 Kafka에 메시지 발행."""
        from selfhealing.services.rate_limit.distributed_channel import (
            DistributedRateLimitChannel,
            RATE_LIMIT_TOPIC,
        )

        # Mock KafkaEventBus
        mock_kafka_bus = MagicMock()
        mock_kafka_bus.publish.return_value = True

        channel = DistributedRateLimitChannel(kafka_bus=mock_kafka_bus)

        success = channel.broadcast_rate_limit_429(
            key="payment_api",
            consecutive_429s=3,
            cooldown_until=time.time() + 10,
            calculated_delay=5.0,
        )

        assert success is True
        mock_kafka_bus.publish.assert_called_once()

        # 호출 인자 검증
        call_kwargs = mock_kafka_bus.publish.call_args[1]
        assert call_kwargs["topic"] == RATE_LIMIT_TOPIC
        assert call_kwargs["key"] == "payment_api"
        assert call_kwargs["event"]["event_type"] == "RATE_LIMIT_429"
        assert call_kwargs["event"]["consecutive_429s"] == 3

    def test_subscribe_registers_handler(self):
        """subscribe_rate_limit_429()이 핸들러 등록."""
        from selfhealing.services.rate_limit.distributed_channel import (
            DistributedRateLimitChannel,
        )

        mock_kafka_bus = MagicMock()
        channel = DistributedRateLimitChannel(kafka_bus=mock_kafka_bus)

        handler_called = []

        def test_handler(event_data):
            handler_called.append(event_data)

        channel.subscribe_rate_limit_429(test_handler)

        # 핸들러 등록 확인
        assert len(channel._handlers) == 1
        mock_kafka_bus.subscribe.assert_called_once()


# =============================================================================
# RateLimitThrottleIntegrationSettings 테스트
# =============================================================================


class TestRateLimitThrottleIntegrationSettings:
    """RateLimitThrottleIntegrationSettings 설정 테스트."""

    def test_default_settings(self):
        """기본 설정 값 확인."""
        from selfhealing.settings.rate_limit_throttle_integration import (
            RateLimitThrottleIntegrationSettings,
        )

        settings = RateLimitThrottleIntegrationSettings()

        assert settings.enabled is True
        assert settings.debounce_window_seconds == 5.0
        assert settings.sla_warning_threshold == 3
        assert settings.recovery_strategy == "gradual"
        assert settings.escalation_enabled is True
        assert settings.escalation_threshold_consecutive_429s == 10

    def test_reduction_ratios(self):
        """429 감소 비율 설정 확인."""
        from selfhealing.settings.rate_limit_throttle_integration import (
            RateLimitThrottleIntegrationSettings,
        )

        settings = RateLimitThrottleIntegrationSettings()

        # 기본 감소 비율 (개별 필드로 정의됨)
        assert settings.reduction_ratio_1 == 0.8  # 20% 감소
        assert settings.reduction_ratio_2 == 0.6  # 40% 감소
        assert settings.reduction_ratio_3 == 0.5  # 50% 감소

    def test_custom_settings(self):
        """커스텀 설정 적용 확인."""
        from selfhealing.settings.rate_limit_throttle_integration import (
            RateLimitThrottleIntegrationSettings,
        )

        settings = RateLimitThrottleIntegrationSettings(
            enabled=False,
            debounce_window_seconds=10.0,
            escalation_threshold_consecutive_429s=20,
        )

        assert settings.enabled is False
        assert settings.debounce_window_seconds == 10.0
        assert settings.escalation_threshold_consecutive_429s == 20
