"""
Recovery Dampening 테스트.

Phase 5: Recovery Dampening 기능 테스트
- 점진적 복구 단계 (80% → 90% → 100%)
- 타이머 기반 단계 전이
- Gradient 계산 지연
"""

import threading

from selfhealing.services.throttle.recovery_dampening import (
    RecoveryDampeningConfig,
    RecoveryDampeningManager,
    RecoveryPhase,
    ServiceRecoveryState,
    get_recovery_dampening_manager,
    reset_recovery_dampening_manager,
)


class TestRecoveryPhase:
    """복구 단계 enum 테스트."""

    def test_phases(self):
        """모든 복구 단계 확인."""
        assert RecoveryPhase.PHASE_1.value == "phase_1"
        assert RecoveryPhase.PHASE_2.value == "phase_2"
        assert RecoveryPhase.COMPLETE.value == "complete"


class TestRecoveryDampeningConfig:
    """설정 테스트."""

    def test_default_config(self):
        """기본 설정값 확인."""
        config = RecoveryDampeningConfig()

        assert config.enabled is True
        assert config.phase_1_ratio == 0.8
        assert config.phase_2_ratio == 0.9
        assert config.phase_1_duration_seconds == 30.0
        assert config.phase_2_duration_seconds == 30.0

    def test_custom_config(self):
        """커스텀 설정값 확인."""
        config = RecoveryDampeningConfig(
            enabled=False,
            phase_1_ratio=0.7,
            phase_2_ratio=0.85,
            phase_1_duration_seconds=10.0,
        )

        assert config.enabled is False
        assert config.phase_1_ratio == 0.7
        assert config.phase_2_ratio == 0.85
        assert config.phase_1_duration_seconds == 10.0


class TestServiceRecoveryState:
    """서비스 복구 상태 테스트."""

    def test_create_state(self):
        """복구 상태 생성 확인."""
        state = ServiceRecoveryState(
            service_name="payment_api",
            target_limit=100,
        )

        assert state.service_name == "payment_api"
        assert state.target_limit == 100
        assert state.current_phase == RecoveryPhase.PHASE_1
        assert state.is_active is True
        assert state.pending_gradient_limit is None


class TestRecoveryDampeningManager:
    """Recovery Dampening 관리자 테스트."""

    def setup_method(self):
        """각 테스트 전 리셋."""
        reset_recovery_dampening_manager()

    def teardown_method(self):
        """각 테스트 후 리셋."""
        reset_recovery_dampening_manager()

    def test_disabled_returns_full_limit(self):
        """비활성화 시 전체 limit 반환."""
        config = RecoveryDampeningConfig(enabled=False)
        manager = RecoveryDampeningManager(config=config)

        result = manager.start_recovery(
            service_name="test_service",
            target_limit=100,
            current_limit=10,
        )

        assert result == 100  # 전체 limit

    def test_start_recovery_returns_phase1_limit(self):
        """복구 시작 시 1단계 limit (80%) 반환."""
        config = RecoveryDampeningConfig(
            phase_1_ratio=0.8,
            phase_1_duration_seconds=60.0,  # 타이머 실행 방지
        )
        manager = RecoveryDampeningManager(config=config)

        result = manager.start_recovery(
            service_name="payment_api",
            target_limit=100,
            current_limit=10,
        )

        assert result == 80  # 100 * 0.8

    def test_recovery_active_check(self):
        """복구 활성화 상태 확인."""
        config = RecoveryDampeningConfig(phase_1_duration_seconds=60.0)
        manager = RecoveryDampeningManager(config=config)

        # 복구 시작 전
        assert manager.is_recovery_active("payment_api") is False

        # 복구 시작
        manager.start_recovery(
            service_name="payment_api",
            target_limit=100,
            current_limit=10,
        )

        assert manager.is_recovery_active("payment_api") is True

    def test_current_multiplier(self):
        """현재 복구 배율 확인."""
        config = RecoveryDampeningConfig(
            phase_1_ratio=0.8,
            phase_1_duration_seconds=60.0,
        )
        manager = RecoveryDampeningManager(config=config)

        # 복구 없을 때
        assert manager.get_current_multiplier("payment_api") == 1.0

        # 복구 시작
        manager.start_recovery(
            service_name="payment_api",
            target_limit=100,
            current_limit=10,
        )

        assert manager.get_current_multiplier("payment_api") == 0.8

    def test_cancel_recovery(self):
        """복구 취소 테스트."""
        config = RecoveryDampeningConfig(phase_1_duration_seconds=60.0)
        manager = RecoveryDampeningManager(config=config)

        manager.start_recovery(
            service_name="payment_api",
            target_limit=100,
            current_limit=10,
        )

        assert manager.is_recovery_active("payment_api") is True

        manager.cancel_recovery("payment_api")

        assert manager.is_recovery_active("payment_api") is False

    def test_store_pending_gradient_limit(self):
        """Gradient 계산 결과 저장 (적용 지연)."""
        config = RecoveryDampeningConfig(phase_1_duration_seconds=60.0)
        manager = RecoveryDampeningManager(config=config)

        manager.start_recovery(
            service_name="payment_api",
            target_limit=100,
            current_limit=10,
        )

        # Gradient 계산 결과 저장
        manager.store_pending_gradient_limit("payment_api", 85)

        state = manager.get_recovery_state("payment_api")
        assert state is not None
        assert state["pending_gradient_limit"] == 85

    def test_get_recovery_state(self):
        """복구 상태 조회."""
        config = RecoveryDampeningConfig(
            phase_1_ratio=0.8,
            phase_1_duration_seconds=60.0,
        )
        manager = RecoveryDampeningManager(config=config)

        manager.start_recovery(
            service_name="payment_api",
            target_limit=100,
            current_limit=10,
        )

        state = manager.get_recovery_state("payment_api")

        assert state is not None
        assert state["service_name"] == "payment_api"
        assert state["target_limit"] == 100
        assert state["current_phase"] == "phase_1"
        assert state["is_active"] is True
        assert state["current_multiplier"] == 0.8

    def test_nonexistent_service_returns_none(self):
        """존재하지 않는 서비스 조회 시 None 반환."""
        manager = RecoveryDampeningManager()

        state = manager.get_recovery_state("nonexistent")
        assert state is None

    def test_phase_transition_callback(self):
        """단계 전이 시 콜백 호출 테스트."""
        callback_event = threading.Event()
        callback_calls = []

        def on_limit_change(service_name: str, new_limit: int):
            callback_calls.append((service_name, new_limit))
            callback_event.set()

        config = RecoveryDampeningConfig(
            phase_1_ratio=0.8,
            phase_2_ratio=0.9,
            phase_1_duration_seconds=0.1,  # 빠른 전이
            phase_2_duration_seconds=0.1,
        )
        manager = RecoveryDampeningManager(
            config=config,
            on_limit_change=on_limit_change,
        )

        manager.start_recovery(
            service_name="payment_api",
            target_limit=100,
            current_limit=10,
        )

        # Event 기반 대기 (최대 5초, 시스템 부하에도 안정적)
        callback_event.wait(timeout=5.0)

        # 콜백 호출 확인
        assert len(callback_calls) >= 1

    def test_singleton_instance(self):
        """싱글톤 인스턴스 테스트."""
        instance1 = get_recovery_dampening_manager()
        instance2 = get_recovery_dampening_manager()

        assert instance1 is instance2

        reset_recovery_dampening_manager()
        instance3 = get_recovery_dampening_manager()

        assert instance1 is not instance3

    def test_reset_clears_all_state(self):
        """reset 시 모든 상태 초기화."""
        config = RecoveryDampeningConfig(phase_1_duration_seconds=60.0)
        manager = RecoveryDampeningManager(config=config)

        manager.start_recovery("service1", 100, 10)
        manager.start_recovery("service2", 200, 20)

        manager.reset()

        assert manager.get_all_recovery_states() == []
