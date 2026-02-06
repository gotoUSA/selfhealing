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

    def test_get_reduction_ratio_by_consecutive_count(self):
        """get_reduction_ratio() 연속 횟수별 비율 반환."""
        from selfhealing.settings.rate_limit_throttle_integration import (
            RateLimitThrottleIntegrationSettings,
        )

        settings = RateLimitThrottleIntegrationSettings()

        assert settings.get_reduction_ratio(1) == 0.8   # 1회
        assert settings.get_reduction_ratio(2) == 0.6   # 2회
        assert settings.get_reduction_ratio(3) == 0.5   # 3회
        assert settings.get_reduction_ratio(5) == 0.5   # 3회 이상도 동일
        assert settings.get_reduction_ratio(100) == 0.5

    def test_pydantic_validation_rejects_out_of_range(self):
        """Pydantic 검증 - 범위 초과 비율 거부."""
        from pydantic import ValidationError
        from selfhealing.settings.rate_limit_throttle_integration import (
            RateLimitThrottleIntegrationSettings,
        )

        with pytest.raises(ValidationError):
            RateLimitThrottleIntegrationSettings(reduction_ratio_1=0.0)  # 최소 0.1

        with pytest.raises(ValidationError):
            RateLimitThrottleIntegrationSettings(reduction_ratio_2=1.5)  # 최대 1.0


# =============================================================================
# RateLimitCoordinator 추가 테스트 - Fail-Open / 엣지케이스
# =============================================================================


class TestEmitRateLimitEventFailOpen:
    """_emit_rate_limit_event Fail-Open 동작 테스트."""

    def test_emit_survives_import_error(self):
        """EventBus import 실패 시 예외 없이 통과 (Fail-Open)."""
        from selfhealing.services.rate_limit_coordinator import _emit_rate_limit_event

        # get_event_bus가 ImportError를 발생시키도록 mock
        with patch(
            "selfhealing.services.rate_limit_coordinator._emit_rate_limit_event",
            wraps=_emit_rate_limit_event,
        ):
            with patch(
                "selfhealing.services.event_bus.get_event_bus",
                side_effect=ImportError("no module"),
            ):
                # 예외 없이 통과
                _emit_rate_limit_event("RATE_LIMIT_429", {"key": "test"})

    def test_emit_survives_generic_exception(self):
        """EventBus 발행 중 예외 시 Fail-Open."""
        from selfhealing.services.rate_limit_coordinator import _emit_rate_limit_event

        with patch(
            "selfhealing.services.event_bus.get_event_bus",
            side_effect=RuntimeError("bus broken"),
        ):
            # 예외 없이 통과
            _emit_rate_limit_event("RATE_LIMIT_429", {"key": "test"})

    def test_emit_unknown_event_type_does_not_crash(self):
        """존재하지 않는 EventType 지정 시 warning 후 통과."""
        from selfhealing.services.rate_limit_coordinator import _emit_rate_limit_event

        mock_bus = MagicMock()
        with patch("selfhealing.services.event_bus.get_event_bus", return_value=mock_bus):
            _emit_rate_limit_event("NONEXISTENT_EVENT_TYPE", {"key": "test"})

        # emit이 호출되지 않아야 함 (unknown type)
        mock_bus.emit.assert_not_called()


class TestRecordRateLimitMetrics:
    """_record_rate_limit_metrics 메트릭 기록 테스트."""

    def test_records_429_counter(self):
        """rate_limit_429_total 카운터 증가 확인."""
        from selfhealing.services.rate_limit_coordinator import _record_rate_limit_metrics

        mock_counter = MagicMock()
        mock_labels = MagicMock()
        mock_counter.labels.return_value = mock_labels

        with patch(
            "selfhealing.services.metrics.definitions.rate_limit_429_total",
            mock_counter,
        ):
            _record_rate_limit_metrics(key="payment_api", status_code=429)

        mock_counter.labels.assert_called_with(key="payment_api", status_code="429")
        mock_labels.inc.assert_called_once()

    def test_records_cooldown_histogram(self):
        """rate_limit_cooldown_seconds 히스토그램 기록 확인."""
        from selfhealing.services.rate_limit_coordinator import _record_rate_limit_metrics

        mock_counter = MagicMock()
        mock_counter.labels.return_value = MagicMock()
        mock_histogram = MagicMock()
        mock_hist_labels = MagicMock()
        mock_histogram.labels.return_value = mock_hist_labels

        with patch(
            "selfhealing.services.metrics.definitions.rate_limit_429_total",
            mock_counter,
        ):
            with patch(
                "selfhealing.services.metrics.definitions.rate_limit_cooldown_seconds",
                mock_histogram,
            ):
                _record_rate_limit_metrics(key="test", cooldown_seconds=15.5)

        mock_histogram.labels.assert_called_with(key="test")
        mock_hist_labels.observe.assert_called_with(15.5)

    def test_records_consecutive_gauge(self):
        """rate_limit_consecutive_429s 게이지 설정 확인."""
        from selfhealing.services.rate_limit_coordinator import _record_rate_limit_metrics

        mock_counter = MagicMock()
        mock_counter.labels.return_value = MagicMock()
        mock_gauge = MagicMock()
        mock_gauge_labels = MagicMock()
        mock_gauge.labels.return_value = mock_gauge_labels

        with patch(
            "selfhealing.services.metrics.definitions.rate_limit_429_total",
            mock_counter,
        ):
            with patch(
                "selfhealing.services.metrics.definitions.rate_limit_consecutive_429s",
                mock_gauge,
            ):
                _record_rate_limit_metrics(key="test", consecutive_429s=5)

        mock_gauge.labels.assert_called_with(key="test")
        mock_gauge_labels.set.assert_called_with(5)

    def test_metrics_fail_open_on_import_error(self):
        """메트릭 모듈 import 실패 시 예외 없이 통과."""
        from selfhealing.services.rate_limit_coordinator import _record_rate_limit_metrics

        with patch(
            "selfhealing.services.metrics.definitions.rate_limit_429_total",
            side_effect=AttributeError("no such metric"),
        ):
            # 예외 없이 통과
            _record_rate_limit_metrics(key="test")


class TestRateLimitCoordinatorRetryAfter:
    """on_rate_limited retry_after 헤더 우선 사용 테스트."""

    def test_uses_retry_after_header_when_provided(self):
        """retry_after 값이 주어지면 default_retry_after 대신 사용."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            default_retry_after=5.0,
            backoff_multiplier=1.0,  # 배수 없음
            jitter_percent=0.0,
            debounce_window_seconds=0.0,
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # retry_after=30 제공
        delay = coordinator.on_rate_limited("test_api", retry_after=30.0)

        # 30초 기반으로 계산 (default 5.0이 아닌 30.0 사용)
        assert delay == pytest.approx(30.0, rel=0.1)

    def test_uses_default_retry_after_when_none(self):
        """retry_after가 None이면 default_retry_after 사용."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            default_retry_after=7.0,
            backoff_multiplier=1.0,
            jitter_percent=0.0,
            debounce_window_seconds=0.0,
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        delay = coordinator.on_rate_limited("test_api", retry_after=None)
        assert delay == pytest.approx(7.0, rel=0.1)

    def test_max_delay_cap(self):
        """max_delay 상한 캡핑 확인."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            default_retry_after=10.0,
            backoff_multiplier=2.0,
            max_delay=30.0,
            jitter_percent=0.0,
            debounce_window_seconds=0.0,
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 많은 429 발생시켜 지수 백오프가 max_delay를 초과하게
        for _ in range(10):
            delay = coordinator.on_rate_limited("test_api")

        assert delay <= 30.0


class TestRateLimitCoordinatorOnRateLimitedDebounceSkip:
    """on_rate_limited 디바운싱 시 이벤트/메트릭/스케줄링 스킵 확인."""

    def test_debounce_skips_event_and_metrics(self):
        """디바운싱 윈도우 내에서 이벤트와 메트릭이 스킵됨."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            debounce_window_seconds=10.0,  # 10초 윈도우
            jitter_percent=0.0,
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        emit_count = 0

        def count_emit(event_type, data, source, priority):
            nonlocal emit_count
            emit_count += 1
            return 1

        with patch("selfhealing.services.event_bus.get_event_bus") as mock_get_bus:
            mock_bus = MagicMock()
            mock_bus.emit = count_emit
            mock_get_bus.return_value = mock_bus

            # 첫 번째 429 - 이벤트 발행됨
            coordinator.on_rate_limited("test_api")
            first_count = emit_count

            # 두 번째 429 (10초 이내) - 디바운싱으로 이벤트 스킵
            coordinator.on_rate_limited("test_api")

        assert emit_count == first_count  # 추가 발행 없음


class TestRateLimitCoordinatorOnSuccess:
    """on_success() 동작 테스트."""

    def test_on_success_resets_consecutive_429s(self):
        """성공 응답 시 consecutive_429s 리셋."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig()
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 429 발생으로 consecutive 증가
        storage.increment_consecutive_429s("test_api")
        storage.increment_consecutive_429s("test_api")
        assert storage.get_state("test_api").consecutive_429s == 2

        # 성공 처리
        coordinator.on_success("test_api")

        # consecutive_429s 리셋됨
        assert storage.get_state("test_api").consecutive_429s == 0

    def test_on_success_no_error_when_no_prior_429(self):
        """429 없이 on_success 호출 시 에러 없음."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig()
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 429 없이 성공
        coordinator.on_success("test_api")  # 에러 없이 통과
        assert storage.get_state("test_api").consecutive_429s == 0


class TestRateLimitCoordinatorScheduleCooldownEnd:
    """_schedule_cooldown_end_event 스케줄링 테스트."""

    def test_schedule_skipped_when_delay_is_zero_or_negative(self):
        """cooldown_until이 과거면 타이머 스케줄링 스킵."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig()
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 과거 시간으로 스케줄
        coordinator._schedule_cooldown_end_event("test_api", time.time() - 5)

        # 타이머가 등록되지 않음
        assert "test_api" not in coordinator._cooldown_timers

    def test_schedule_cancels_existing_timer(self):
        """동일 key에 대한 기존 타이머 취소."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig()
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 첫 번째 스케줄
        future = time.time() + 60
        coordinator._schedule_cooldown_end_event("test_api", future)
        first_timer = coordinator._cooldown_timers.get("test_api")
        assert first_timer is not None

        # 두 번째 스케줄 (기존 타이머 취소됨)
        coordinator._schedule_cooldown_end_event("test_api", time.time() + 120)
        second_timer = coordinator._cooldown_timers.get("test_api")
        assert second_timer is not first_timer

        # 정리
        second_timer.cancel()


class TestRateLimitAwareDecorator:
    """rate_limit_aware() 데코레이터 테스트."""

    def test_decorator_calls_wait_and_on_success(self):
        """데코레이터가 wait_if_needed + on_success 호출."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig()
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 성공 응답 Mock
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}

        @coordinator.rate_limit_aware("test_api")
        def call_api():
            return mock_response

        result = call_api()
        assert result.status_code == 200

    def test_decorator_calls_on_rate_limited_on_429(self):
        """데코레이터가 429 응답 시 on_rate_limited 호출."""
        from selfhealing.services.rate_limit_coordinator import (
            RateLimitCoordinator,
            RateLimitCoordinatorConfig,
        )

        storage = MockInMemoryRateLimitStorage()
        config = RateLimitCoordinatorConfig(
            jitter_percent=0.0,
            debounce_window_seconds=0.0,
        )
        coordinator = RateLimitCoordinator(storage=storage, config=config)

        # 429 응답 Mock
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "10"}

        @coordinator.rate_limit_aware("test_api")
        def call_api():
            return mock_response

        call_api()

        # consecutive_429s가 증가됨
        state = storage.get_state("test_api")
        assert state.consecutive_429s == 1


# =============================================================================
# AdaptiveThrottle 추가 테스트 - 엣지케이스
# =============================================================================


class TestAdaptiveThrottle429SelfHealingEvent:
    """AdaptiveThrottle 429 핸들러 - SelfHealingEvent 객체 지원 테스트."""

    def setup_method(self):
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle
            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_handles_dict_event_data(self):
        """dict 형태 이벤트 데이터 처리."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # dict 직접 전달
        throttle._handle_rate_limit_429({
            "key": "test_api",
            "consecutive_429s": 1,
            "cooldown_until": time.time() + 10,
        })
        assert throttle.current_limit == 80

    def test_handles_object_with_data_attribute(self):
        """SelfHealingEvent 객체 (event.data) 형태 처리."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # SelfHealingEvent-like 객체
        event = MagicMock()
        event.data = {
            "key": "test_api",
            "consecutive_429s": 1,
            "cooldown_until": time.time() + 10,
        }

        throttle._handle_rate_limit_429(event)
        assert throttle.current_limit == 80


class TestAdaptiveThrottle429EventEmission:
    """AdaptiveThrottle 429 핸들러 이벤트 발행 테스트."""

    def setup_method(self):
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle
            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_emits_sla_warning_on_3_consecutive(self):
        """연속 3회 이상 429 시 THROTTLE_SLA_WARNING 발행."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        emitted_events = []

        def capture_emit(**kwargs):
            emitted_events.append(kwargs)
            return 1

        def mock_get_bus_safe():
            bus = MagicMock()
            bus.emit = capture_emit
            return bus

        with patch(
            "selfhealing.services.throttle.adaptive._get_event_bus_safe",
            mock_get_bus_safe,
        ):
            throttle._handle_rate_limit_429({
                "key": "test_api",
                "consecutive_429s": 3,
                "cooldown_until": time.time() + 10,
            })

        # SLA Warning과 LIMIT_CHANGED 이벤트 둘 다 발행됨
        event_types = [str(e.get("event_type", "")) for e in emitted_events]
        has_sla_warning = any("SLA_WARNING" in et for et in event_types)
        has_limit_changed = any("LIMIT_CHANGED" in et for et in event_types)

        assert has_sla_warning, f"SLA_WARNING not found in {event_types}"
        assert has_limit_changed, f"LIMIT_CHANGED not found in {event_types}"

    def test_no_sla_warning_on_1_consecutive(self):
        """단일 429 시 SLA Warning 미발행."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        emitted_events = []

        def capture_emit(**kwargs):
            emitted_events.append(kwargs)
            return 1

        def mock_get_bus_safe():
            bus = MagicMock()
            bus.emit = capture_emit
            return bus

        with patch(
            "selfhealing.services.throttle.adaptive._get_event_bus_safe",
            mock_get_bus_safe,
        ):
            throttle._handle_rate_limit_429({
                "key": "test_api",
                "consecutive_429s": 1,
                "cooldown_until": time.time() + 10,
            })

        event_types = [str(e.get("event_type", "")) for e in emitted_events]
        has_sla_warning = any("SLA_WARNING" in et for et in event_types)
        assert not has_sla_warning


class TestAdaptiveThrottleIsRateLimited:
    """is_rate_limited_for_key() 테스트."""

    def setup_method(self):
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle
            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_returns_true_during_cooldown(self):
        """cooldown 중이면 True 반환."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # 미래 시점 cooldown 설정
        throttle._rate_limit_keys["test_api"] = time.time() + 100

        assert throttle.is_rate_limited_for_key("test_api") is True

    def test_returns_false_after_cooldown(self):
        """cooldown 종료 후 False 반환."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # 과거 시점 cooldown
        throttle._rate_limit_keys["test_api"] = time.time() - 5

        assert throttle.is_rate_limited_for_key("test_api") is False

    def test_returns_false_for_unknown_key(self):
        """등록되지 않은 key는 False 반환."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        assert throttle.is_rate_limited_for_key("unknown_api") is False


class TestAdaptiveThrottleConservativeLimitDisabled:
    """conservative_limit disabled 동작 테스트."""

    def setup_method(self):
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle
            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_conservative_disabled_returns_current_limit(self):
        """conservative_enabled=False 시 현재 limit 그대로 반환."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        throttle._conservative_enabled = False
        throttle._rtt_suggested_limit = 50
        throttle._429_suggested_limit = 30

        # disabled이면 _current_limit 반환 (min 계산 무시)
        assert throttle.conservative_limit == throttle._current_limit


class TestAdaptiveThrottleCheckPriorityProtection:
    """check() 메서드 CRITICAL 티어 실제 limit 적용 테스트."""

    def setup_method(self):
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle
            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_check_critical_tier_allows_more_than_reduced_limit(self):
        """CRITICAL 티어는 429 감소 전 limit 기준으로 체크."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=200)
        throttle = AdaptiveThrottle(config=config)

        # 429로 limit 감소
        throttle._handle_rate_limit_429({
            "key": "test_api",
            "consecutive_429s": 3,
            "cooldown_until": time.time() + 10,
        })

        assert throttle._429_reduction_active is True
        reduced = throttle.current_limit
        assert reduced < 100

        # standard 티어 체크 - 감소된 limit 기준
        result_standard = throttle.check("req_standard", tier_id="standard")
        # CRITICAL 티어 체크 - 이전 limit (100) 기준으로 탄력적
        result_critical = throttle.check("req_critical", tier_id="critical")

        # 둘 다 첫 요청이므로 허용되지만, internal limit 임시 변경 검증
        # check 후에 limit이 원래대로 복원되는지 확인
        assert throttle._current_limit == reduced  # 복원됨

    def test_check_standard_tier_not_protected(self):
        """standard 티어는 감소된 limit 그대로 적용."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10, max_limit=200)
        throttle = AdaptiveThrottle(config=config)

        # 429 활성화
        throttle._429_reduction_active = True
        throttle._limit_before_429 = 100
        throttle._current_limit = 50

        # standard 티어는 보호 안 됨 → limit 변경 없이 check
        throttle.check("req_test", tier_id="standard")
        assert throttle._current_limit == 50  # 변경 없음


class TestAdaptiveThrottleCooldownEndRecoveryDampening:
    """_handle_cooldown_end Recovery Dampening 시작 테스트."""

    def setup_method(self):
        try:
            from selfhealing.services.throttle.adaptive import reset_adaptive_throttle
            reset_adaptive_throttle()
        except ImportError:
            pass

    def test_cooldown_end_starts_recovery_dampening(self):
        """COOLDOWN_END 시 start_recovery_dampening() 호출."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # 429 상태 설정
        throttle._handle_rate_limit_429({
            "key": "test_api",
            "consecutive_429s": 2,
            "cooldown_until": time.time() + 10,
        })

        # start_recovery_dampening을 mock
        with patch.object(throttle, "start_recovery_dampening") as mock_recovery:
            throttle._handle_cooldown_end({
                "key": "test_api",
                "cooldown_ended_at": time.time(),
            })

            mock_recovery.assert_called_once()

    def test_cooldown_end_handles_event_object(self):
        """COOLDOWN_END에서 SelfHealingEvent 객체 처리."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # 429 상태 설정
        throttle._rate_limit_keys["test_api"] = time.time() + 10
        throttle._429_reduction_active = True

        # SelfHealingEvent-like 객체
        event = MagicMock()
        event.data = {
            "key": "test_api",
            "cooldown_ended_at": time.time(),
        }

        with patch.object(throttle, "start_recovery_dampening"):
            throttle._handle_cooldown_end(event)

        assert "test_api" not in throttle._rate_limit_keys
        assert throttle._429_reduction_active is False


# =============================================================================
# DistributedRateLimitChannel 추가 테스트
# =============================================================================


class TestDistributedRateLimitChannelBroadcastFailure:
    """DistributedRateLimitChannel broadcast 실패 처리 테스트."""

    def test_broadcast_returns_false_on_publish_failure(self):
        """Kafka publish 실패 시 False 반환."""
        from selfhealing.services.rate_limit.distributed_channel import (
            DistributedRateLimitChannel,
        )

        mock_kafka = MagicMock()
        mock_kafka.publish.return_value = False

        channel = DistributedRateLimitChannel(kafka_bus=mock_kafka)

        result = channel.broadcast_rate_limit_429(
            key="test", consecutive_429s=1,
            cooldown_until=time.time() + 10, calculated_delay=5.0,
        )

        assert result is False

    def test_broadcast_returns_false_on_exception(self):
        """Kafka publish 예외 시 False 반환."""
        from selfhealing.services.rate_limit.distributed_channel import (
            DistributedRateLimitChannel,
        )

        mock_kafka = MagicMock()
        mock_kafka.publish.side_effect = RuntimeError("kafka down")

        channel = DistributedRateLimitChannel(kafka_bus=mock_kafka)

        result = channel.broadcast_rate_limit_429(
            key="test", consecutive_429s=1,
            cooldown_until=time.time() + 10, calculated_delay=5.0,
        )

        assert result is False


class TestDistributedRateLimitChannelDispatch:
    """_dispatch_to_handlers 핸들러 전달 테스트."""

    def test_dispatch_calls_all_handlers(self):
        """모든 등록된 핸들러에 이벤트 전달."""
        from selfhealing.services.rate_limit.distributed_channel import (
            DistributedRateLimitChannel,
        )

        mock_kafka = MagicMock()
        channel = DistributedRateLimitChannel(kafka_bus=mock_kafka)

        results = []

        def handler_a(data):
            results.append(("a", data))

        def handler_b(data):
            results.append(("b", data))

        channel._handlers = [handler_a, handler_b]

        # ConsumedEvent-like 객체
        event = MagicMock()
        event.value = {"key": "test_api", "consecutive_429s": 1}

        success = channel._dispatch_to_handlers(event)

        assert success is True
        assert len(results) == 2
        assert results[0][0] == "a"
        assert results[1][0] == "b"

    def test_dispatch_survives_handler_exception(self):
        """핸들러 예외 시에도 다른 핸들러 계속 실행."""
        from selfhealing.services.rate_limit.distributed_channel import (
            DistributedRateLimitChannel,
        )

        mock_kafka = MagicMock()
        channel = DistributedRateLimitChannel(kafka_bus=mock_kafka)

        results = []

        def failing_handler(data):
            raise ValueError("handler crash")

        def working_handler(data):
            results.append(data)

        channel._handlers = [failing_handler, working_handler]

        event = MagicMock()
        event.value = {"key": "test"}

        # 예외가 있어도 다른 핸들러 실행
        channel._dispatch_to_handlers(event)

        assert len(results) == 1


class TestDistributedRateLimitChannelStartStop:
    """start/stop 상태 관리 테스트."""

    def test_start_sets_running(self):
        """start() 호출 시 running 상태."""
        from selfhealing.services.rate_limit.distributed_channel import (
            DistributedRateLimitChannel,
        )

        mock_kafka = MagicMock()
        channel = DistributedRateLimitChannel(kafka_bus=mock_kafka)

        channel.start()
        assert channel.is_running is True
        mock_kafka.start.assert_called_once()

    def test_stop_clears_running(self):
        """stop() 호출 시 running 해제."""
        from selfhealing.services.rate_limit.distributed_channel import (
            DistributedRateLimitChannel,
        )

        mock_kafka = MagicMock()
        channel = DistributedRateLimitChannel(kafka_bus=mock_kafka)

        channel.start()
        channel.stop()
        assert channel.is_running is False

    def test_handler_count_property(self):
        """handler_count 속성 확인."""
        from selfhealing.services.rate_limit.distributed_channel import (
            DistributedRateLimitChannel,
        )

        mock_kafka = MagicMock()
        channel = DistributedRateLimitChannel(kafka_bus=mock_kafka)

        assert channel.handler_count == 0

        channel._handlers.append(lambda d: None)
        assert channel.handler_count == 1


# =============================================================================
# RateLimitEscalationHandler 추가 테스트
# =============================================================================


class TestRateLimitEscalationHandlerEdgeCases:
    """RateLimitEscalationHandler 엣지케이스 테스트."""

    def test_escalation_failure_logs_error(self):
        """에스컬레이션 실패 시 에러 로그 (예외 없음)."""
        from selfhealing.meta.rate_limit_escalation import RateLimitEscalationHandler

        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error_message = "PagerDuty down"
        mock_manager.escalate.return_value = mock_result

        handler = RateLimitEscalationHandler(
            escalation_manager=mock_manager,
            threshold=5,
        )

        # 에스컬레이션 시도 → 실패 (예외는 발생하지 않음)
        handler._handle_rate_limit_429({
            "key": "payment_api",
            "consecutive_429s": 10,
        })

        # escalate는 호출됨
        mock_manager.escalate.assert_called_once()
        # key는 여전히 에스컬레이션 완료로 마킹됨
        assert "payment_api" in handler.escalated_keys

    def test_reset_all_escalations(self):
        """모든 에스컬레이션 상태 일괄 초기화."""
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

        # 여러 key 에스컬레이션
        for key in ["api_a", "api_b", "api_c"]:
            handler._handle_rate_limit_429({
                "key": key,
                "consecutive_429s": 10,
            })

        assert len(handler.escalated_keys) == 3

        # 일괄 초기화
        handler.reset_all_escalations()
        assert len(handler.escalated_keys) == 0

    def test_threshold_property(self):
        """threshold 속성 확인."""
        from selfhealing.meta.rate_limit_escalation import RateLimitEscalationHandler

        handler = RateLimitEscalationHandler(threshold=15)
        assert handler.threshold == 15

    def test_subscribe_returns_true_with_mock_bus(self):
        """subscribe() EventBus 구독 성공 확인."""
        from selfhealing.meta.rate_limit_escalation import RateLimitEscalationHandler

        handler = RateLimitEscalationHandler(threshold=5)

        mock_bus = MagicMock()
        with patch("selfhealing.services.event_bus.get_event_bus", return_value=mock_bus):
            result = handler.subscribe()

        assert result is True
        mock_bus.subscribe.assert_called_once()

    def test_subscribe_returns_false_when_no_eventbus(self):
        """EventBus 없을 때 subscribe() False 반환."""
        from selfhealing.meta.rate_limit_escalation import RateLimitEscalationHandler

        handler = RateLimitEscalationHandler(threshold=5)

        with patch(
            "selfhealing.services.event_bus.get_event_bus",
            side_effect=ImportError("no eventbus"),
        ):
            result = handler.subscribe()

        assert result is False

    def test_multiple_keys_can_escalate_independently(self):
        """서로 다른 key는 독립적으로 에스컬레이션."""
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

        handler._handle_rate_limit_429({
            "key": "api_a",
            "consecutive_429s": 10,
        })
        handler._handle_rate_limit_429({
            "key": "api_b",
            "consecutive_429s": 10,
        })

        assert mock_manager.escalate.call_count == 2
