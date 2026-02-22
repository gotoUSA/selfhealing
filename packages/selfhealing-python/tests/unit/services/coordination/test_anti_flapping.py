"""
Unit tests for AntiFlappingGuard.

Tests:
- EMERGENCY_LEVEL_COOLDOWN_SECONDS SSOT constant
- Flapping detection and lockout
- Cooldown enforcement
- Recovery cooldown
- Transition recording
"""

from datetime import datetime, timedelta, timezone

from selfhealing.services.coordination.anti_flapping import (
    EMERGENCY_LEVEL_COOLDOWN_SECONDS,
    AntiFlappingGuard,
)


class TestEmergencyLevelCooldownSeconds:
    """EMERGENCY_LEVEL_COOLDOWN_SECONDS SSOT 상수 테스트."""

    def test_ssot_value_is_300(self):
        """SSOT 상수 값이 300초(5분)."""
        assert EMERGENCY_LEVEL_COOLDOWN_SECONDS == 300

    def test_default_guard_uses_ssot(self):
        """AntiFlappingGuard 기본값이 SSOT 사용."""
        guard = AntiFlappingGuard()

        assert guard.level_cooldown_seconds == EMERGENCY_LEVEL_COOLDOWN_SECONDS


class TestAntiFlappingGuardDefaults:
    """AntiFlappingGuard 기본값 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        guard = AntiFlappingGuard()

        assert guard.level_cooldown_seconds == 300
        assert guard.cooldown_after_recovery_seconds == 600
        assert guard.min_stable_duration_before_recovery_seconds == 600
        assert guard.max_level_transitions_per_hour == 3
        assert guard.flapping_lockout_minutes == 30


class TestAntiFlappingGuardTransitionAllowed:
    """check_transition_allowed 테스트."""

    def test_allowed_when_no_history(self):
        """이력 없을 때 전환 허용."""
        guard = AntiFlappingGuard()

        allowed, reason = guard.check_transition_allowed()

        assert allowed is True
        assert "allowed" in reason.lower()

    def test_allowed_when_below_max(self):
        """최대 전환 횟수 미만 시 허용."""
        guard = AntiFlappingGuard(max_level_transitions_per_hour=3)
        now = datetime.now(timezone.utc)

        # 최근 1시간 내 2회 전환
        history = [
            now - timedelta(minutes=30),
            now - timedelta(minutes=15),
        ]

        allowed, reason = guard.check_transition_allowed(
            transition_history=history,
            now=now,
        )

        assert allowed is True

    def test_blocked_when_flapping_detected(self):
        """플래핑 감지 시 차단."""
        guard = AntiFlappingGuard(max_level_transitions_per_hour=3)
        now = datetime.now(timezone.utc)

        # 최근 1시간 내 3회 전환 (최대치 도달)
        history = [
            now - timedelta(minutes=45),
            now - timedelta(minutes=30),
            now - timedelta(minutes=15),
        ]

        allowed, reason = guard.check_transition_allowed(
            transition_history=history,
            now=now,
        )

        assert allowed is False
        assert "flapping" in reason.lower()

    def test_lockout_active_after_flapping(self):
        """플래핑 감지 후 잠금 활성화."""
        guard = AntiFlappingGuard(
            max_level_transitions_per_hour=3,
            flapping_lockout_minutes=30,
        )
        now = datetime.now(timezone.utc)

        # 플래핑 감지 유발
        history = [
            now - timedelta(minutes=45),
            now - timedelta(minutes=30),
            now - timedelta(minutes=15),
        ]
        guard.check_transition_allowed(transition_history=history, now=now)

        # 잠금 상태에서 재시도
        allowed, reason = guard.check_transition_allowed(
            transition_history=[],  # 이력 없어도 잠금 상태
            now=now + timedelta(minutes=5),  # 5분 후
        )

        assert allowed is False
        assert "lockout" in reason.lower()

    def test_allowed_after_lockout_expires(self):
        """잠금 만료 후 허용."""
        guard = AntiFlappingGuard(
            max_level_transitions_per_hour=3,
            flapping_lockout_minutes=30,
        )
        now = datetime.now(timezone.utc)

        # 플래핑 감지 유발
        history = [
            now - timedelta(minutes=45),
            now - timedelta(minutes=30),
            now - timedelta(minutes=15),
        ]
        guard.check_transition_allowed(transition_history=history, now=now)

        # 잠금 만료 후 (31분 후)
        allowed, reason = guard.check_transition_allowed(
            transition_history=[],
            now=now + timedelta(minutes=31),
        )

        assert allowed is True


class TestAntiFlappingGuardCooldown:
    """check_cooldown_elapsed 테스트."""

    def test_cooldown_elapsed_when_no_previous(self):
        """이전 전환 없으면 쿨다운 통과."""
        guard = AntiFlappingGuard()

        elapsed, reason = guard.check_cooldown_elapsed(last_transition_at=None)

        assert elapsed is True

    def test_cooldown_not_elapsed_recently(self):
        """최근 전환 시 쿨다운 차단."""
        guard = AntiFlappingGuard(level_cooldown_seconds=300)
        now = datetime.now(timezone.utc)
        last = now - timedelta(seconds=100)  # 100초 전

        elapsed, reason = guard.check_cooldown_elapsed(
            last_transition_at=last,
            now=now,
        )

        assert elapsed is False
        assert "remaining" in reason.lower()

    def test_cooldown_elapsed_after_period(self):
        """쿨다운 기간 경과 시 통과."""
        guard = AntiFlappingGuard(level_cooldown_seconds=300)
        now = datetime.now(timezone.utc)
        last = now - timedelta(seconds=400)  # 400초 전

        elapsed, reason = guard.check_cooldown_elapsed(
            last_transition_at=last,
            now=now,
        )

        assert elapsed is True


class TestAntiFlappingGuardRecoveryCooldown:
    """check_recovery_cooldown 테스트."""

    def test_no_cooldown_without_recovery(self):
        """복구 이력 없으면 쿨다운 없음."""
        guard = AntiFlappingGuard()

        can_reactivate, reason = guard.check_recovery_cooldown()

        assert can_reactivate is True

    def test_cooldown_active_after_recovery(self):
        """복구 직후 쿨다운 활성화."""
        guard = AntiFlappingGuard(cooldown_after_recovery_seconds=600)
        now = datetime.now(timezone.utc)

        guard.record_recovery_complete(at=now)

        can_reactivate, reason = guard.check_recovery_cooldown(
            now=now + timedelta(minutes=5),  # 5분 후
        )

        assert can_reactivate is False
        assert "remaining" in reason.lower()

    def test_cooldown_expires_after_period(self):
        """쿨다운 기간 경과 후 재활성화 가능."""
        guard = AntiFlappingGuard(cooldown_after_recovery_seconds=600)
        now = datetime.now(timezone.utc)

        guard.record_recovery_complete(at=now)

        can_reactivate, reason = guard.check_recovery_cooldown(
            now=now + timedelta(minutes=15),  # 15분 후
        )

        assert can_reactivate is True


class TestAntiFlappingGuardRecording:
    """record_transition / record_recovery_complete 테스트."""

    def test_record_transition(self):
        """전환 기록."""
        guard = AntiFlappingGuard()
        now = datetime.now(timezone.utc)

        guard.record_transition(at=now)

        status = guard.get_status()
        assert status["recent_transitions_count"] == 1

    def test_old_transitions_cleaned_up(self):
        """오래된 전환 기록 정리."""
        guard = AntiFlappingGuard()
        now = datetime.now(timezone.utc)

        # 3시간 전 전환 기록
        guard.record_transition(at=now - timedelta(hours=3))

        # 현재 전환 기록 (정리 트리거)
        guard.record_transition(at=now)

        status = guard.get_status()
        # 2시간 초과 기록은 정리됨
        assert status["recent_transitions_count"] == 1

    def test_record_recovery_complete(self):
        """복구 완료 기록."""
        guard = AntiFlappingGuard()
        now = datetime.now(timezone.utc)

        guard.record_recovery_complete(at=now)

        status = guard.get_status()
        assert status["last_recovery_at"] == now.isoformat()


class TestAntiFlappingGuardClearLockout:
    """clear_lockout 테스트."""

    def test_clear_lockout(self):
        """수동 잠금 해제."""
        guard = AntiFlappingGuard(
            max_level_transitions_per_hour=3,
            flapping_lockout_minutes=30,
        )
        now = datetime.now(timezone.utc)

        # 플래핑으로 잠금 활성화
        history = [now - timedelta(minutes=i * 10) for i in range(3)]
        guard.check_transition_allowed(transition_history=history, now=now)

        # 잠금 확인
        status = guard.get_status()
        assert status["is_locked_out"] is True

        # 수동 해제
        guard.clear_lockout()

        # 잠금 해제 확인
        status = guard.get_status()
        assert status["is_locked_out"] is False


class TestAntiFlappingGuardStatus:
    """get_status 테스트."""

    def test_get_status_returns_all_fields(self):
        """모든 필드 포함."""
        guard = AntiFlappingGuard()

        status = guard.get_status()

        expected_fields = [
            "level_cooldown_seconds",
            "cooldown_after_recovery_seconds",
            "max_level_transitions_per_hour",
            "flapping_lockout_minutes",
            "recent_transitions_count",
            "is_locked_out",
            "lockout_until",
            "last_recovery_at",
        ]

        for field in expected_fields:
            assert field in status
