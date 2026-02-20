"""
Unit tests for Recovery Circuit Breaker.

Tests:
- 회로 상태 관리 (CLOSED, OPEN, HALF_OPEN)
- 에러율 임계값 초과 시 트립
- 반개방 상태에서 복구 테스트
- 연속 트립 시 영구 차단
- 재-에스컬레이션 플래그

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.1
"""

import pytest
from datetime import datetime, timedelta, timezone

from selfhealing.services.coordination.recovery_circuit_breaker import (
    RecoveryCircuitBreaker,
    RecoveryCircuitBreakerConfig,
    RecoveryCircuitState,
    RecoveryMetricsSnapshot,
    get_recovery_circuit_breaker,
    reset_recovery_circuit_breaker,
)


class TestRecoveryCircuitBreakerConfig:
    """RecoveryCircuitBreakerConfig 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        config = RecoveryCircuitBreakerConfig()
        
        assert config.error_rate_threshold == 0.15
        assert config.sampling_window_seconds == 60
        assert config.min_samples == 10
        assert config.open_duration_seconds == 300
        assert config.half_open_max_requests == 5
        assert config.max_consecutive_trips == 3
        assert config.re_escalation_enabled is True
        assert config.re_escalation_level == "LEVEL_3"

    def test_custom_values(self):
        """커스텀 값 설정."""
        config = RecoveryCircuitBreakerConfig(
            error_rate_threshold=0.20,
            min_samples=5,
            max_consecutive_trips=5,
        )
        
        assert config.error_rate_threshold == 0.20
        assert config.min_samples == 5
        assert config.max_consecutive_trips == 5


class TestRecoveryMetricsSnapshot:
    """RecoveryMetricsSnapshot 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        snapshot = RecoveryMetricsSnapshot()
        
        assert snapshot.total_requests == 0
        assert snapshot.success_count == 0
        assert snapshot.failure_count == 0
        assert snapshot.error_rate == 0.0
        assert snapshot.timestamp is not None

    def test_to_dict(self):
        """딕셔너리 변환."""
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            success_count=90,
            failure_count=10,
            error_rate=0.10,
            namespace="global",
        )
        
        data = snapshot.to_dict()
        
        assert data["total_requests"] == 100
        assert data["error_rate"] == 0.10
        assert data["namespace"] == "global"


class TestRecoveryCircuitBreakerInit:
    """RecoveryCircuitBreaker 초기화 테스트."""

    def test_init_with_defaults(self):
        """기본값으로 초기화."""
        breaker = RecoveryCircuitBreaker()
        
        assert breaker is not None
        assert breaker._config is not None

    def test_init_with_custom_config(self):
        """커스텀 설정으로 초기화."""
        config = RecoveryCircuitBreakerConfig(error_rate_threshold=0.20)
        breaker = RecoveryCircuitBreaker(config=config)
        
        assert breaker._config.error_rate_threshold == 0.20


class TestRecoveryCircuitBreakerState:
    """회로 상태 관리 테스트."""

    @pytest.fixture
    def breaker(self):
        """테스트용 회로 차단기."""
        return RecoveryCircuitBreaker()

    def test_initial_state_is_closed(self, breaker):
        """초기 상태는 CLOSED."""
        state = breaker.get_state("global")
        assert state == RecoveryCircuitState.CLOSED

    def test_reset_sets_closed(self, breaker):
        """reset()은 CLOSED로 설정."""
        breaker.force_open("global", "test")
        
        breaker.reset("global")
        
        state = breaker.get_state("global")
        assert state == RecoveryCircuitState.CLOSED

    def test_force_open_sets_open(self, breaker):
        """force_open()은 OPEN으로 설정."""
        breaker.force_open("global", "manual intervention")
        
        state = breaker.get_state("global")
        assert state == RecoveryCircuitState.OPEN


class TestRecoveryCircuitBreakerTrip:
    """트립 동작 테스트."""

    @pytest.fixture
    def breaker(self):
        """테스트용 회로 차단기."""
        config = RecoveryCircuitBreakerConfig(
            error_rate_threshold=0.15,
            min_samples=10,
        )
        return RecoveryCircuitBreaker(config=config)

    def test_no_trip_below_threshold(self, breaker):
        """임계값 미만이면 트립 안 함."""
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            failure_count=10,
            error_rate=0.10,  # 10% < 15%
        )
        
        result = breaker.check_and_trip("global", snapshot)
        
        assert result["tripped"] is False
        assert result["state"] == "closed"

    def test_trip_at_threshold(self, breaker):
        """임계값 도달 시 트립."""
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            failure_count=15,
            error_rate=0.15,  # 15% >= 15%
        )
        
        result = breaker.check_and_trip("global", snapshot)
        
        assert result["tripped"] is True
        assert result["state"] == "open"
        assert "15.00%" in result["reason"]

    def test_trip_above_threshold(self, breaker):
        """임계값 초과 시 트립."""
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            failure_count=25,
            error_rate=0.25,  # 25% > 15%
        )
        
        result = breaker.check_and_trip("global", snapshot)
        
        assert result["tripped"] is True
        assert result["state"] == "open"
        assert result["should_re_escalate"] is True

    def test_no_trip_insufficient_samples(self, breaker):
        """샘플 부족 시 트립 안 함."""
        snapshot = RecoveryMetricsSnapshot(
            total_requests=5,  # < 10
            failure_count=5,
            error_rate=1.0,  # 100%
        )
        
        result = breaker.check_and_trip("global", snapshot)
        
        assert result["tripped"] is False
        assert "Insufficient samples" in result["reason"]

    def test_no_trip_already_open(self, breaker):
        """이미 OPEN이면 트립 안 함."""
        breaker.force_open("global", "test")
        
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            failure_count=50,
            error_rate=0.50,
        )
        
        result = breaker.check_and_trip("global", snapshot)
        
        assert result["tripped"] is False
        assert result["state"] == "open"


class TestRecoveryCircuitBreakerReEscalation:
    """재-에스컬레이션 테스트."""

    def test_re_escalation_enabled(self):
        """재-에스컬레이션 활성화."""
        config = RecoveryCircuitBreakerConfig(
            re_escalation_enabled=True,
            re_escalation_level="LEVEL_3",
        )
        breaker = RecoveryCircuitBreaker(config=config)
        
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            error_rate=0.20,
        )
        
        result = breaker.check_and_trip("global", snapshot)
        
        assert result["tripped"] is True
        assert result["should_re_escalate"] is True
        assert result["re_escalation_level"] == "LEVEL_3"

    def test_re_escalation_disabled(self):
        """재-에스컬레이션 비활성화."""
        config = RecoveryCircuitBreakerConfig(re_escalation_enabled=False)
        breaker = RecoveryCircuitBreaker(config=config)
        
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            error_rate=0.20,
        )
        
        result = breaker.check_and_trip("global", snapshot)
        
        assert result["tripped"] is True
        assert result["should_re_escalate"] is False


class TestRecoveryCircuitBreakerConsecutiveTrips:
    """연속 트립 테스트."""

    def test_consecutive_trips_count(self):
        """연속 트립 횟수 증가."""
        config = RecoveryCircuitBreakerConfig(max_consecutive_trips=3)
        breaker = RecoveryCircuitBreaker(config=config)
        
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            error_rate=0.20,
        )
        
        # 첫 번째 트립
        result1 = breaker.check_and_trip("global", snapshot)
        assert result1["trip_count"] == 1
        
        # 리셋 후 두 번째 트립
        breaker._states["global"] = RecoveryCircuitState.CLOSED
        result2 = breaker.check_and_trip("global", snapshot)
        assert result2["trip_count"] == 2
        
        # 리셋 후 세 번째 트립
        breaker._states["global"] = RecoveryCircuitState.CLOSED
        result3 = breaker.check_and_trip("global", snapshot)
        assert result3["trip_count"] == 3

    def test_permanent_open_after_max_trips(self):
        """max_consecutive_trips 초과 시 영구 차단."""
        config = RecoveryCircuitBreakerConfig(max_consecutive_trips=2)
        breaker = RecoveryCircuitBreaker(config=config)
        
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            error_rate=0.20,
        )
        
        # 두 번 트립
        breaker.check_and_trip("global", snapshot)
        breaker._states["global"] = RecoveryCircuitState.CLOSED
        result = breaker.check_and_trip("global", snapshot)
        
        assert result["is_permanently_open"] is True
        assert result["should_re_escalate"] is False  # 영구 차단 시 재-에스컬레이션 안 함

    def test_is_permanently_open(self):
        """영구 차단 상태 확인."""
        config = RecoveryCircuitBreakerConfig(max_consecutive_trips=2)
        breaker = RecoveryCircuitBreaker(config=config)
        
        assert breaker.is_permanently_open("global") is False
        
        # 두 번 트립
        breaker._trip_counts["global"] = 2
        
        assert breaker.is_permanently_open("global") is True


class TestRecoveryCircuitBreakerHalfOpen:
    """반개방 상태 테스트."""

    @pytest.fixture
    def breaker(self):
        """테스트용 회로 차단기."""
        config = RecoveryCircuitBreakerConfig(
            open_duration_seconds=1,  # 빠른 테스트를 위해 1초
            half_open_max_requests=3,
        )
        return RecoveryCircuitBreaker(config=config)

    def test_transition_to_half_open(self, breaker):
        """OPEN -> HALF_OPEN 전환."""
        # OPEN 상태로 설정
        breaker.force_open("global", "test")
        
        # 시간 경과 시뮬레이션
        breaker._last_trip_at["global"] = datetime.now(timezone.utc) - timedelta(seconds=2)
        
        # 상태 조회 시 HALF_OPEN으로 전환
        state = breaker.get_state("global")
        
        assert state == RecoveryCircuitState.HALF_OPEN

    def test_half_open_success_returns_closed(self, breaker):
        """HALF_OPEN에서 성공 시 CLOSED 복귀."""
        breaker._states["global"] = RecoveryCircuitState.HALF_OPEN
        breaker._half_open_requests["global"] = 2
        breaker._half_open_failures["global"] = 0
        
        # 성공 스냅샷
        snapshot = RecoveryMetricsSnapshot(
            total_requests=10,
            error_rate=0.05,  # 5% < 15%
        )
        
        result = breaker.check_and_trip("global", snapshot)
        
        # 3번째 요청 성공 -> CLOSED
        assert result["state"] == "closed"
        assert result["tripped"] is False


class TestRecoveryCircuitBreakerNamespaceIsolation:
    """네임스페이스 격리 테스트."""

    def test_namespaces_isolated(self):
        """네임스페이스별 독립적 상태."""
        breaker = RecoveryCircuitBreaker()
        
        snapshot = RecoveryMetricsSnapshot(
            total_requests=100,
            error_rate=0.20,
        )
        
        # global 트립
        breaker.check_and_trip("global", snapshot)
        
        # seoul은 여전히 CLOSED
        seoul_state = breaker.get_state("seoul")
        global_state = breaker.get_state("global")
        
        assert seoul_state == RecoveryCircuitState.CLOSED
        assert global_state == RecoveryCircuitState.OPEN


class TestRecoveryCircuitBreakerStatus:
    """상태 조회 테스트."""

    def test_get_status(self):
        """상태 정보 조회."""
        breaker = RecoveryCircuitBreaker()
        
        status = breaker.get_status("global")
        
        assert status["namespace"] == "global"
        assert status["state"] == "closed"
        assert status["trip_count"] == 0
        assert "config" in status
        assert status["config"]["error_rate_threshold"] == 0.15


class TestRecoveryCircuitBreakerSingleton:
    """싱글톤 테스트."""

    def test_singleton(self):
        """싱글톤 동작 확인."""
        reset_recovery_circuit_breaker()
        
        breaker1 = get_recovery_circuit_breaker()
        breaker2 = get_recovery_circuit_breaker()
        
        assert breaker1 is breaker2
        
        reset_recovery_circuit_breaker()

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        reset_recovery_circuit_breaker()
        
        breaker1 = get_recovery_circuit_breaker()
        reset_recovery_circuit_breaker()
        breaker2 = get_recovery_circuit_breaker()
        
        assert breaker1 is not breaker2
        
        reset_recovery_circuit_breaker()
