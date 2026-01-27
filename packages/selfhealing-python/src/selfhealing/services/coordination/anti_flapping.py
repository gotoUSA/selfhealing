"""
Anti-Flapping Guard (히스테리시스 가드).

레벨이 빈번하게 변하며 시스템이 요동치는 '플래핑(Flapping)' 현상을 방지합니다.

Features:
- Emergency Level 전환 간 최소 대기 시간 (쿨다운)
- 복구 후 재활성화 제한 (Post-Recovery Cooldown)
- 플래핑 감지 및 자동 잠금
- Recovery Hysteresis Factor: 복구 시 추가 안정화 시간 적용

Code reference:
    models.py#L24 (RecoveryGateConfig.stabilization_period_seconds = 300)

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from selfhealing.settings import get_anti_flapping_settings

logger = logging.getLogger(__name__)


# SSOT: Emergency Level 쿨다운 상수 (3가지 보완사항 ③)
EMERGENCY_LEVEL_COOLDOWN_SECONDS: int = 300
"""
Emergency Level 전환 간 최소 대기 시간 (초).

장애 상황에서 지표가 경계선에 걸쳐 있어 레벨이 초 단위로 출렁거릴 때,
시스템이 불필요하게 롤백과 복구를 반복하는 현상을 방지.

Code reference:
    models.py#L24 (RecoveryGateConfig.stabilization_period_seconds = 300)
"""


@dataclass
class AntiFlappingGuard:
    """
    플래핑 방지를 위한 히스테리시스 가드.

    Recovery 시 안정성을 충분히 확인한 뒤에 자동화를 재개하는
    신중한 복구 로직을 구현합니다.

    Code reference:
        models.py#L24 (RecoveryGateConfig.stabilization_period_seconds 패턴)
    """

    # 레벨 전환 간 최소 대기 시간 (초) - Settings에서 로드
    level_cooldown_seconds: int = field(
        default_factory=lambda: get_anti_flapping_settings().level_cooldown_seconds
    )
    """레벨 전환 후 다음 전환까지의 최소 대기 시간."""

    # 복구 후 대기 시간 (재활성화 제한)
    cooldown_after_recovery_seconds: int = field(
        default_factory=lambda: get_anti_flapping_settings().cooldown_after_recovery_seconds
    )
    """복구 완료 후 일정 시간 동안 재활성화 제한."""

    # 복구 전 최소 안정 유지 시간
    min_stable_duration_before_recovery_seconds: int = field(
        default_factory=lambda: get_anti_flapping_settings().min_stable_duration_before_recovery_seconds
    )
    """10분간 안정 상태 유지 후에만 복구 가능."""

    # 시간당 최대 전환 횟수
    max_level_transitions_per_hour: int = field(
        default_factory=lambda: get_anti_flapping_settings().max_level_transitions_per_hour
    )
    """플래핑 감지 임계값: 시간당 3회 초과 시 경고."""

    # 플래핑 감지 시 강제 쿨다운
    flapping_lockout_minutes: int = field(
        default_factory=lambda: get_anti_flapping_settings().flapping_lockout_minutes
    )
    """플래핑 감지 시 30분간 레벨 변경 잠금."""

    # Recovery Hysteresis Factor (72번 문서 §5.1.1)
    recovery_hysteresis_factor: float = field(
        default_factory=lambda: get_anti_flapping_settings().recovery_hysteresis_factor
    )
    """
    복구 윈도우 히스테리시스 팩터.

    긴급 상황 전파 속도보다 복구 승인 속도를 의도적으로 느리게 하여
    이차 장애 발생 가능성을 낮춥니다.

    Values:
    - 1.0: 비대칭 없음 (Emergency와 동일한 속도로 복구)
    - 1.15: 복구 조건 확인에 15% 더 긴 시간 필요 (권장)
    - 1.20: 복구 조건 확인에 20% 더 긴 시간 필요 (보수적)

    Calculation:
    - 기본 안정화 대기 시간: 600초 (10분)
    - 히스테리시스 적용 후: 600 * 1.15 = 690초 (11.5분)

    Environment:
        SELFHEALING_RECOVERY_HYSTERESIS_FACTOR (기본값: 1.15)

    Reference:
        72_EMERGENCY_COORDINATION_LAYER.md#§5.1.1
        77_RECOVERY_COORDINATOR.md#RecoveryCircuitBreaker
    """

    # 내부 상태
    _transition_history: list[datetime] = field(default_factory=list)
    """레벨 전환 이력."""

    _last_recovery_at: datetime | None = None
    """마지막 복구 완료 시각."""

    _flapping_lockout_until: datetime | None = None
    """플래핑 잠금 해제 시각."""

    def check_transition_allowed(
        self,
        transition_history: list[datetime] | None = None,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """
        레벨 전환 허용 여부 확인.

        Args:
            transition_history: 외부 전환 이력 (없으면 내부 이력 사용)
            now: 현재 시각 (테스트용)

        Returns:
            (is_allowed, reason): 전환 가능 여부와 사유
        """
        now = now or datetime.now(timezone.utc)
        history = (
            transition_history
            if transition_history is not None
            else self._transition_history
        )

        # 플래핑 잠금 상태 확인
        if self._flapping_lockout_until and now < self._flapping_lockout_until:
            remaining = (self._flapping_lockout_until - now).total_seconds()
            return (False, f"Flapping lockout active. {remaining:.0f}s remaining.")

        # 최근 1시간 내 전환 횟수 확인
        one_hour_ago = now - timedelta(hours=1)
        recent_transitions = [t for t in history if t > one_hour_ago]

        if len(recent_transitions) >= self.max_level_transitions_per_hour:
            # 플래핑 감지 - 잠금 활성화
            self._flapping_lockout_until = now + timedelta(
                minutes=self.flapping_lockout_minutes
            )
            logger.warning(
                f"[AntiFlappingGuard] Flapping detected: "
                f"{len(recent_transitions)} transitions in last hour. "
                f"Lockout for {self.flapping_lockout_minutes} minutes."
            )
            return (
                False,
                f"Flapping detected: {len(recent_transitions)} transitions "
                f"in last hour (max: {self.max_level_transitions_per_hour}). "
                f"Lockout for {self.flapping_lockout_minutes} minutes.",
            )

        return (True, "Transition allowed")

    def check_cooldown_elapsed(
        self,
        last_transition_at: datetime | None = None,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """
        쿨다운 경과 여부 확인.

        Args:
            last_transition_at: 마지막 전환 시각
            now: 현재 시각 (테스트용)

        Returns:
            (is_elapsed, reason): 쿨다운 경과 여부와 사유
        """
        if last_transition_at is None:
            return (True, "No previous transition")

        now = now or datetime.now(timezone.utc)
        elapsed = (now - last_transition_at).total_seconds()

        if elapsed < self.level_cooldown_seconds:
            remaining = self.level_cooldown_seconds - elapsed
            return (
                False,
                f"Cooldown active. {remaining:.0f}s remaining "
                f"(required: {self.level_cooldown_seconds}s).",
            )

        return (
            True,
            f"Cooldown elapsed ({elapsed:.0f}s >= {self.level_cooldown_seconds}s)",
        )

    def check_recovery_cooldown(
        self,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """
        복구 후 쿨다운 확인 (재활성화 제한).

        Args:
            now: 현재 시각 (테스트용)

        Returns:
            (can_reactivate, reason): 재활성화 가능 여부와 사유
        """
        if self._last_recovery_at is None:
            return (True, "No recent recovery")

        now = now or datetime.now(timezone.utc)
        elapsed = (now - self._last_recovery_at).total_seconds()

        if elapsed < self.cooldown_after_recovery_seconds:
            remaining = self.cooldown_after_recovery_seconds - elapsed
            return (
                False,
                f"Post-recovery cooldown active. {remaining:.0f}s remaining.",
            )

        return (True, "Post-recovery cooldown elapsed")

    def record_transition(self, at: datetime | None = None) -> None:
        """
        레벨 전환 기록.

        Args:
            at: 전환 시각 (없으면 현재 시각)
        """
        transition_time = at or datetime.now(timezone.utc)
        self._transition_history.append(transition_time)

        # 오래된 기록 정리 (최근 2시간만 유지)
        cutoff = transition_time - timedelta(hours=2)
        self._transition_history = [t for t in self._transition_history if t > cutoff]

        logger.debug(
            f"[AntiFlappingGuard] Transition recorded at {transition_time.isoformat()}. "
            f"History size: {len(self._transition_history)}"
        )

    def record_recovery_complete(self, at: datetime | None = None) -> None:
        """
        복구 완료 기록.

        Args:
            at: 복구 완료 시각 (없으면 현재 시각)
        """
        self._last_recovery_at = at or datetime.now(timezone.utc)
        logger.info(
            f"[AntiFlappingGuard] Recovery completed at "
            f"{self._last_recovery_at.isoformat()}"
        )

    def clear_lockout(self) -> None:
        """플래핑 잠금 해제 (수동 복구용)."""
        self._flapping_lockout_until = None
        logger.info("[AntiFlappingGuard] Flapping lockout cleared manually")

    def get_status(self) -> dict:
        """현재 상태 조회."""
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        recent_transitions = [t for t in self._transition_history if t > one_hour_ago]

        return {
            "level_cooldown_seconds": self.level_cooldown_seconds,
            "cooldown_after_recovery_seconds": self.cooldown_after_recovery_seconds,
            "max_level_transitions_per_hour": self.max_level_transitions_per_hour,
            "flapping_lockout_minutes": self.flapping_lockout_minutes,
            "recent_transitions_count": len(recent_transitions),
            "is_locked_out": (
                self._flapping_lockout_until is not None
                and now < self._flapping_lockout_until
            ),
            "lockout_until": (
                self._flapping_lockout_until.isoformat()
                if self._flapping_lockout_until
                else None
            ),
            "last_recovery_at": (
                self._last_recovery_at.isoformat() if self._last_recovery_at else None
            ),
            "recovery_hysteresis_factor": self.recovery_hysteresis_factor,
        }

    def get_effective_stability_duration(self, is_recovery: bool = False) -> int:
        """
        유효 안정화 대기 시간 계산.

        Recovery 시에는 히스테리시스 팩터를 적용하여 더 긴 대기 시간을 반환합니다.
        이는 Emergency 상황에서 복구가 너무 빨리 진행되어 이차 장애가 발생하는 것을 방지합니다.

        Args:
            is_recovery: True면 복구 상황, False면 Emergency 활성화 상황

        Returns:
            유효 안정화 대기 시간 (초)

        Example:
            >>> guard = AntiFlappingGuard(
            ...     min_stable_duration_before_recovery_seconds=600,
            ...     recovery_hysteresis_factor=1.15,
            ... )
            >>> guard.get_effective_stability_duration(is_recovery=False)
            600
            >>> guard.get_effective_stability_duration(is_recovery=True)
            690  # 600 * 1.15

        Reference:
            72_EMERGENCY_COORDINATION_LAYER.md#§5.1.1
        """
        base_duration = self.min_stable_duration_before_recovery_seconds

        if is_recovery:
            # 복구 시 히스테리시스 팩터 적용
            effective_duration = int(base_duration * self.recovery_hysteresis_factor)
            logger.debug(
                f"[AntiFlappingGuard] Recovery stability duration: "
                f"{base_duration}s * {self.recovery_hysteresis_factor} = {effective_duration}s"
            )
            return effective_duration

        return base_duration
