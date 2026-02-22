"""
RecoveryDampeningMixin for AdaptiveThrottle.

이 모듈은 selfhealing.services.throttle.adaptive 패키지의 내부 구현입니다.
"""

import threading
import time

import structlog

import selfhealing.services.throttle.adaptive as _adaptive_mod

logger = structlog.get_logger()


class RecoveryDampeningMixin:
    """AdaptiveThrottle RecoveryDampeningMixin."""

    RECOVERY_DAMPENING_MULTIPLIERS: tuple[float, ...] = (0.8, 0.9, 1.0)

    def start_recovery_dampening(self, apply_jitter: bool = False) -> None:
        """
        Recovery Dampening 시작: 80%부터 점진적으로 복구.

        Emergency 비활성화 후 Thundering Herd 방지를 위해
        limit을 80% → 90% → 100%로 점진적으로 복구합니다.

        Args:
            apply_jitter: Thundering Herd 방지용 랜덤 지연 적용 여부
        """
        import random

        # Jitter 적용 (Pod간 복구 시점 분산)
        if apply_jitter and self._recovery_jitter_max_seconds > 0:
            jitter_seconds = random.uniform(0, self._recovery_jitter_max_seconds)

            logger.info(
                "adaptive_throttle.recovery_jitter_applied_waiting",
                jitter_seconds=jitter_seconds,
            )

            # 비동기 지연 후 실제 복구 시작
            self._schedule_dampening_start(jitter_seconds)
            return

        self._do_start_recovery_dampening()

    def _schedule_dampening_start(self, delay_seconds: float) -> None:
        """지연 후 Dampening 시작 스케줄링."""

        def delayed_start():
            time.sleep(delay_seconds)
            self._do_start_recovery_dampening()

        thread = threading.Thread(target=delayed_start, daemon=True)
        thread.start()

    def _do_start_recovery_dampening(self) -> None:
        """실제 Recovery Dampening 시작 로직."""
        previous_limit = self._current_limit
        self._recovery_dampening_active = True
        self._recovery_dampening_step = 0
        self._recovery_dampening_last_time = time.time()

        # 첫 단계: 80% 적용
        target_limit = int(self._base_limit_before_emergency * self.RECOVERY_DAMPENING_MULTIPLIERS[0])
        self.current_limit = target_limit

        logger.info(
            "adaptive_throttle.recovery_dampening_started",
            target_limit=target_limit,
        )

        # 감사 로깅 (Recovery Dampening 시작)
        _adaptive_mod._record_audit_safe(
            action="throttle_recovery_started",
            old_limit=previous_limit,
            new_limit=target_limit,
            recovery_step=0,
            recovery_multiplier=self.RECOVERY_DAMPENING_MULTIPLIERS[0],
        )

    def advance_recovery_dampening(self) -> bool:
        """
        Recovery Dampening 다음 단계로 진행.

        Returns:
            True if advanced to next step, False if already complete
        """
        if not self._recovery_dampening_active:
            return False

        now = time.time()
        elapsed = now - self._recovery_dampening_last_time

        # 인터벌 확인 (기본 30초)
        if elapsed < self._recovery_dampening_interval_seconds:
            return False

        self._recovery_dampening_step += 1
        self._recovery_dampening_last_time = now

        if self._recovery_dampening_step >= len(self.RECOVERY_DAMPENING_MULTIPLIERS):
            # 복구 완료
            self._recovery_dampening_active = False
            self._recovery_dampening_step = 0
            logger.info("adaptive_throttle.recovery_dampening_completed")
            return False

        # 다음 단계 적용
        multiplier = self.RECOVERY_DAMPENING_MULTIPLIERS[self._recovery_dampening_step]
        target_limit = int(self._base_limit_before_emergency * multiplier)
        self.current_limit = target_limit

        logger.info(
            "adaptive_throttle.recovery_dampening_advanced",
            _self=self._recovery_dampening_step,
            int=int(multiplier * 100),
            target_limit=target_limit,
        )

        return True

    def complete_recovery_dampening(self) -> None:
        """
        Recovery Dampening 즉시 완료: 100%로 복구.

        수동 복구 또는 테스트용.
        """
        if not self._recovery_dampening_active:
            return

        previous_limit = self._current_limit
        self._recovery_dampening_active = False
        self._recovery_dampening_step = 0

        # 100%로 복구
        self.current_limit = self._base_limit_before_emergency

        logger.info(
            "adaptive_throttle.recovery_dampening_completed_immediately",
            _self=self._base_limit_before_emergency,
        )

        # 감사 로깅 (Recovery Dampening 완료)
        _adaptive_mod._record_audit_safe(
            action="throttle_recovery_completed",
            old_limit=previous_limit,
            new_limit=self._base_limit_before_emergency,
            recovery_step=len(self.RECOVERY_DAMPENING_MULTIPLIERS),
            recovery_multiplier=1.0,
        )

    def is_recovery_dampening_active(self) -> bool:
        """Recovery Dampening 활성화 여부."""
        return self._recovery_dampening_active

    def get_recovery_dampening_progress(self) -> dict:
        """
        Recovery Dampening 진행 상황 조회.

        Returns:
            진행 상황 정보
        """
        if not self._recovery_dampening_active:
            return {
                "active": False,
                "step": 0,
                "multiplier": 1.0,
                "percent": 100,
            }

        step = self._recovery_dampening_step
        multiplier = self.RECOVERY_DAMPENING_MULTIPLIERS[step]

        return {
            "active": True,
            "step": step,
            "multiplier": multiplier,
            "percent": int(multiplier * 100),
            "elapsed_seconds": time.time() - self._recovery_dampening_last_time,
            "interval_seconds": self._recovery_dampening_interval_seconds,
        }
