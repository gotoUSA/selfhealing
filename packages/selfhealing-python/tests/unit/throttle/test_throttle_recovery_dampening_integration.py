"""
AdaptiveThrottle Recovery Dampening 연동 테스트.

테스트 대상:
1. start_recovery_dampening() - Dampening 시작 (80%)
2. advance_recovery_dampening() - 단계 진행 (90% → 100%)
3. complete_recovery_dampening() - 즉시 완료
4. is_recovery_dampening_active() - 상태 조회
5. get_recovery_dampening_progress() - 진행 상황 조회
6. rollback_to_base_limit() - 즉시 롤백
7. Emergency 복구 시 Recovery Dampening 연동
"""

import time

from selfhealing.services.throttle.adaptive import (
    get_adaptive_throttle,
    reset_adaptive_throttle,
)


class TestStartRecoveryDampening:
    """start_recovery_dampening() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_sets_dampening_active(self):
        """Dampening 시작 시 active 플래그 설정."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100

        throttle.start_recovery_dampening()

        assert throttle.is_recovery_dampening_active() is True

    def test_sets_limit_to_80_percent(self):
        """Dampening 시작 시 limit을 80%로 설정."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100

        throttle.start_recovery_dampening()

        assert throttle.current_limit == 80  # 100 × 0.8

    def test_resets_step_to_zero(self):
        """Dampening 시작 시 step을 0으로 리셋."""
        throttle = get_adaptive_throttle()
        throttle._recovery_dampening_step = 2  # 이전 상태
        throttle._base_limit_before_emergency = 100

        throttle.start_recovery_dampening()

        assert throttle._recovery_dampening_step == 0

    def test_records_start_time(self):
        """Dampening 시작 시 시간 기록."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100

        before = time.time()
        throttle.start_recovery_dampening()
        after = time.time()

        assert before <= throttle._recovery_dampening_last_time <= after


class TestAdvanceRecoveryDampening:
    """advance_recovery_dampening() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_returns_false_when_inactive(self):
        """Dampening 비활성 시 False 반환."""
        throttle = get_adaptive_throttle()

        result = throttle.advance_recovery_dampening()

        assert result is False

    def test_does_not_advance_within_interval(self):
        """인터벌 내에서는 진행 안함."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100
        throttle.start_recovery_dampening()

        result = throttle.advance_recovery_dampening()

        assert result is False
        assert throttle._recovery_dampening_step == 0

    def test_advances_to_step_1_after_interval(self):
        """인터벌 후 step 1로 진행 (90%)."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100
        throttle._recovery_dampening_interval_seconds = 0  # 즉시 진행
        throttle.start_recovery_dampening()

        # 시간 경과 시뮬레이션
        throttle._recovery_dampening_last_time = 0

        result = throttle.advance_recovery_dampening()

        assert result is True
        assert throttle._recovery_dampening_step == 1
        assert throttle.current_limit == 90  # 100 × 0.9

    def test_advances_to_complete(self):
        """step 2에서 완료 처리."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100
        throttle._recovery_dampening_interval_seconds = 0
        throttle._recovery_dampening_active = True
        throttle._recovery_dampening_step = 1
        throttle._recovery_dampening_last_time = 0

        result = throttle.advance_recovery_dampening()

        assert result is True
        assert throttle.current_limit == 100  # 100%

        # 한 번 더 진행하면 완료
        throttle._recovery_dampening_last_time = 0
        result = throttle.advance_recovery_dampening()

        assert result is False
        assert throttle.is_recovery_dampening_active() is False


class TestCompleteRecoveryDampening:
    """complete_recovery_dampening() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_immediately_completes_dampening(self):
        """즉시 Dampening 완료."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100
        throttle.start_recovery_dampening()

        throttle.complete_recovery_dampening()

        assert throttle.is_recovery_dampening_active() is False
        assert throttle.current_limit == 100

    def test_sets_limit_to_100_percent(self):
        """완료 시 limit을 100%로 설정."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 150
        throttle.start_recovery_dampening()

        throttle.complete_recovery_dampening()

        assert throttle.current_limit == 150

    def test_does_nothing_when_inactive(self):
        """비활성 상태에서 호출 무시."""
        throttle = get_adaptive_throttle()
        throttle.current_limit = 80

        throttle.complete_recovery_dampening()

        assert throttle.current_limit == 80  # 변경 없음


class TestIsRecoveryDampeningActive:
    """is_recovery_dampening_active() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_returns_false_initially(self):
        """초기 상태에서 False 반환."""
        throttle = get_adaptive_throttle()

        assert throttle.is_recovery_dampening_active() is False

    def test_returns_true_after_start(self):
        """시작 후 True 반환."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100
        throttle.start_recovery_dampening()

        assert throttle.is_recovery_dampening_active() is True


class TestGetRecoveryDampeningProgress:
    """get_recovery_dampening_progress() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_returns_inactive_state(self):
        """비활성 시 inactive 상태 반환."""
        throttle = get_adaptive_throttle()

        progress = throttle.get_recovery_dampening_progress()

        assert progress["active"] is False
        assert progress["step"] == 0
        assert progress["multiplier"] == 1.0
        assert progress["percent"] == 100

    def test_returns_active_progress(self):
        """활성 시 진행 상황 반환."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100
        throttle.start_recovery_dampening()

        progress = throttle.get_recovery_dampening_progress()

        assert progress["active"] is True
        assert progress["step"] == 0
        assert progress["multiplier"] == 0.8
        assert progress["percent"] == 80
        assert "elapsed_seconds" in progress
        assert "interval_seconds" in progress


class TestRollbackToBaseLimit:
    """rollback_to_base_limit() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_restores_base_limit(self):
        """base limit으로 즉시 복구."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 150
        throttle.current_limit = 50

        result = throttle.rollback_to_base_limit()

        assert result == 150
        assert throttle.current_limit == 150

    def test_clears_all_emergency_state(self):
        """모든 Emergency 상태 해제."""
        throttle = get_adaptive_throttle()
        throttle._emergency_mode_active = True
        throttle._emergency_level = 3
        throttle._gradient_frozen = True
        throttle._full_stop_active = True
        throttle._recovery_dampening_active = True
        throttle._base_limit_before_emergency = 100

        throttle.rollback_to_base_limit()

        assert throttle.is_emergency_active() is False
        assert throttle.get_emergency_level() == 0
        assert throttle.is_gradient_frozen() is False
        assert throttle.is_full_stop_active() is False
        assert throttle.is_recovery_dampening_active() is False


class TestRecoveryDampeningInStats:
    """get_stats()에 Recovery Dampening 정보 포함 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_stats_include_recovery_section(self):
        """stats에 recovery 섹션 포함."""
        throttle = get_adaptive_throttle()
        stats = throttle.get_stats()

        assert "recovery" in stats
        assert "dampening_active" in stats["recovery"]
        assert "dampening_step" in stats["recovery"]

    def test_stats_reflect_dampening_state(self):
        """stats가 Dampening 상태 반영."""
        throttle = get_adaptive_throttle()

        # 비활성 상태
        stats1 = throttle.get_stats()
        assert stats1["recovery"]["dampening_active"] is False

        # 활성화
        throttle._base_limit_before_emergency = 100
        throttle.start_recovery_dampening()
        stats2 = throttle.get_stats()
        assert stats2["recovery"]["dampening_active"] is True


class TestEmergencyDeactivationTriggersRecoveryDampening:
    """Emergency 비활성화 시 Recovery Dampening 연동 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_level_0_starts_recovery_dampening(self):
        """Level 0으로 복구 시 Recovery Dampening 시작."""
        throttle = get_adaptive_throttle()
        throttle.current_limit = 100
        throttle.adjust_for_emergency(2)  # LEVEL_2

        throttle.adjust_for_emergency(0)  # 복구

        assert throttle.is_recovery_dampening_active() is True
        assert throttle.current_limit == 80  # 100 × 0.8

    def test_emergency_reactivation_stops_dampening(self):
        """Emergency 재활성화 시 Dampening 중단."""
        throttle = get_adaptive_throttle()
        throttle.current_limit = 100
        throttle.adjust_for_emergency(1)  # 활성화
        throttle.adjust_for_emergency(0)  # 복구 (Dampening 시작)

        assert throttle.is_recovery_dampening_active() is True

        throttle.adjust_for_emergency(2)  # 재활성화

        assert throttle.is_recovery_dampening_active() is False


class TestResetClearsDampeningState:
    """reset_all()이 Dampening 상태도 초기화하는지 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_reset_clears_dampening_state(self):
        """reset_all()이 Dampening 상태 초기화."""
        throttle = get_adaptive_throttle()
        throttle._base_limit_before_emergency = 100
        throttle.start_recovery_dampening()

        assert throttle.is_recovery_dampening_active() is True

        throttle.reset_all()

        assert throttle.is_recovery_dampening_active() is False
        assert throttle._recovery_dampening_step == 0
