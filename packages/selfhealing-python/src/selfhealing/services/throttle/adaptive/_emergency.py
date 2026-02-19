"""
EmergencyModeMixin for AdaptiveThrottle.

이 모듈은 selfhealing.services.throttle.adaptive 패키지의 내부 구현입니다.
"""

import selfhealing.services.throttle.adaptive as _adaptive_mod
import logging
import time
logger = logging.getLogger(__name__)






class EmergencyModeMixin:
    """AdaptiveThrottle EmergencyModeMixin."""

    # =========================================================================
    # Emergency Mode 연동 메서드
    # =========================================================================

    def adjust_for_emergency(self, level: int) -> None:
        """
        Emergency Level에 따라 limit을 자동 조정.

        배율 매핑:
        - NORMAL (0): 1.0 (전체 용량)
        - LEVEL_1 (1): 0.8 (80% 용량)
        - LEVEL_2 (2): 0.5 (50% 용량)
        - LEVEL_3 (3): min_limit 고정 + Gradient Freeze

        Args:
            level: Emergency Level (0-3)
        """
        previous_level = self._emergency_level
        self._emergency_level = level

        if level == 0:
            # NORMAL: Emergency 모드 해제
            self._emergency_mode_active = False
            self._gradient_frozen = False

            # Full Stop 해제 (활성화되어 있었다면)
            if self._full_stop_active:
                self.deactivate_full_stop()
                return  # Recovery Dampening이 limit 복구 처리

            # Recovery Dampening으로 점진적 복구
            self.start_recovery_dampening()
            logger.info(f"[AdaptiveThrottle] Emergency deactivated, " f"starting recovery dampening")
        else:
            # Emergency 활성화
            if not self._emergency_mode_active:
                # 최초 활성화 시 현재 limit 저장
                self._base_limit_before_emergency = self._current_limit
            self._emergency_mode_active = True

            # Recovery Dampening 중이라면 중단
            self._recovery_dampening_active = False

            if level >= 3:
                # LEVEL_3: min_limit 고정 + Gradient Freeze
                self._gradient_frozen = True
                new_limit = self.config.min_limit
                previous_limit = self._current_limit

                # Full Stop 3중 조건 확인
                is_full_stop, reason = self.check_full_stop_conditions()
                if is_full_stop:
                    self.activate_full_stop(reason)
                    return  # Full Stop이 limit을 0으로 설정

                logger.warning(
                    f"[AdaptiveThrottle] Emergency LEVEL_3, " f"limit frozen to min_limit={new_limit}, Gradient frozen"
                )
                self.current_limit = new_limit
                # Emergency 조정 메트릭 기록
                _adaptive_mod._record_throttle_metrics(
                    service=self._service_name,
                    limit=new_limit,
                    emergency_level=level,
                )
                # Emergency 조정 감사 로그 기록
                _adaptive_mod._record_audit_safe(
                    action="throttle_emergency_sync",
                    old_limit=previous_limit,
                    new_limit=new_limit,
                    emergency_level=level,
                    applied_multiplier=0.0,
                )
                # Postmortem용 이력 기록
                _adaptive_mod._record_limit_history(
                    previous_limit=previous_limit,
                    new_limit=new_limit,
                    reason=f"emergency_level_{level}",
                    trigger_source="emergency_mode",
                )
            else:
                # LEVEL_1, LEVEL_2: 배율 적용
                self._gradient_frozen = False
                multiplier = _adaptive_mod.EMERGENCY_LEVEL_LIMIT_MULTIPLIERS.get(level, 1.0)
                previous_limit = self._current_limit
                new_limit = int(self._base_limit_before_emergency * multiplier)
                logger.info(
                    f"[AdaptiveThrottle] Emergency level {previous_level} → {level}, "
                    f"limit: {self._current_limit} → {new_limit} (×{multiplier})"
                )
                self.current_limit = new_limit
                # Emergency 조정 메트릭 기록
                _adaptive_mod._record_throttle_metrics(
                    service=self._service_name,
                    limit=new_limit,
                    emergency_level=level,
                )
                # Emergency 조정 감사 로그 기록
                _adaptive_mod._record_audit_safe(
                    action="throttle_emergency_sync",
                    old_limit=previous_limit,
                    new_limit=new_limit,
                    emergency_level=level,
                    applied_multiplier=multiplier,
                )
                # Postmortem용 이력 기록
                _adaptive_mod._record_limit_history(
                    previous_limit=previous_limit,
                    new_limit=new_limit,
                    reason=f"emergency_level_{level}",
                    trigger_source="emergency_mode",
                )

        self._cache_emergency_tier_multipliers(level)

    def _cache_emergency_tier_multipliers(self, level: int) -> None:
        """
        Emergency Level에 대응하는 티어별 배율을 캐싱.

        EMERGENCY_LEVEL_RULES에서 티어별 배율을 가져와 캐시합니다.

        Args:
            level: Emergency Level (0-3)
        """
        try:
            from selfhealing.services.emergency_mode.enums import (
                EMERGENCY_LEVEL_RULES,
                EmergencyLevel,
            )

            level_enum = EmergencyLevel(level)
            rules = EMERGENCY_LEVEL_RULES.get(level_enum, EMERGENCY_LEVEL_RULES[EmergencyLevel.NORMAL])
            self._emergency_tier_multipliers = rules.copy()
            logger.debug(f"[AdaptiveThrottle] Cached tier multipliers for level {level}: {rules}")
        except (ImportError, ValueError) as e:
            logger.debug(f"[AdaptiveThrottle] Could not cache tier multipliers: {e}")
            self._emergency_tier_multipliers = {}

    def _apply_emergency_cap(self, gradient_limit: int, tier_id: str = "standard") -> int:
        """
        Gradient limit에 Emergency 배율을 Hard-Cap으로 적용.

        공식: EffectiveLimit = min(gradient_limit, CB_min) × EmergencyMultiplier

        Args:
            gradient_limit: Gradient 계산으로 결정된 limit
            tier_id: 티어 ID (critical, standard, non_essential)

        Returns:
            Emergency 배율이 적용된 최종 limit
        """
        if not self._emergency_mode_active:
            return gradient_limit

        # 티어별 배율 조회 (기본값 1.0)
        multiplier = self._emergency_tier_multipliers.get(tier_id, 1.0)

        # Hard-Cap 적용
        effective_limit = int(gradient_limit * multiplier)

        # min_limit 이상 보장
        effective_limit = max(effective_limit, self.config.min_limit)

        logger.debug(
            f"[AdaptiveThrottle] Hard-Cap applied: "
            f"gradient_limit={gradient_limit}, tier={tier_id}, "
            f"multiplier={multiplier}, effective_limit={effective_limit}"
        )

        return effective_limit

    def get_effective_limit(self, tier_id: str = "standard") -> int:
        """
        티어별 실효 limit 조회.

        Emergency 모드 시 티어별 배율이 적용된 limit을 반환합니다.

        Args:
            tier_id: 티어 ID (critical, standard, non_essential)

        Returns:
            실효 limit
        """
        return self._apply_emergency_cap(self._current_limit, tier_id)

    def is_emergency_active(self) -> bool:
        """Emergency 모드 활성화 여부."""
        return self._emergency_mode_active

    def is_gradient_frozen(self) -> bool:
        """Gradient 적용이 Freeze 상태인지 여부."""
        return self._gradient_frozen

    def get_emergency_level(self) -> int:
        """현재 Emergency Level 조회."""
        return self._emergency_level

    # =========================================================================
    # Governance 통합 상태 동기화 (Emergency + Kill Switch + Break Glass, 30초 TTL)
    # =========================================================================

    def sync_emergency_state_on_init(self) -> None:
        """
        Throttle 초기화 시 현재 Emergency Level 확인 및 동기화.

        애플리케이션 시작 시 또는 리셋 후 호출됩니다.
        """
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager

            manager = get_emergency_manager()
            level = manager.get_current_level()

            if level.value > 0:
                logger.info(f"[AdaptiveThrottle] Syncing emergency state on init: " f"level={level.name}")
                self.adjust_for_emergency(level.value)

            self._last_emergency_check_time = time.time()

        except ImportError:
            logger.debug("[AdaptiveThrottle] EmergencyMode not available for sync")
        except Exception as e:
            logger.warning(f"[AdaptiveThrottle] Failed to sync emergency state: {e}")

