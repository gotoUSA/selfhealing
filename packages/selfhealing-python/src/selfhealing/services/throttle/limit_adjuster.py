"""
ThrottleLimitAdjuster — EventBus 이벤트 → ThrottlePolicy limit 조정 컴포넌트.

AdaptiveThrottle.__init__()에서 4개의 _subscribe_*_events() 메서드로
하드코딩되어 있던 EventBus 구독을 독립 수명주기 컴포넌트로 분리한다.

PolicyHook이 아닌 독립 수명주기 컴포넌트:
    - PolicyHook은 요청 생명주기(on_execute, on_success, on_failure) 전용
    - EventBus 구독은 시스템 전역 이벤트(429, ErrorBudget, LoadShedding,
      KillSwitch)에 반응하여 limit을 직접 변경하므로 요청 생명주기와 무관
    - HedgingConfigUpdateHook (hedging.py)과 동일한 register() + start() 패턴

Fail-Open 원칙:
    - EventBus 미설치/구독 실패 시 Policy 동작에 영향 없음
    - 개별 핸들러 실패 시 다른 Policy에 전파하지 않음

사용 예시::

    adjuster = ThrottleLimitAdjuster()
    adjuster.register(throttle_policy)
    adjuster.start()
"""

from __future__ import annotations

import structlog
from typing import Any

logger = structlog.get_logger()


class ThrottleLimitAdjuster:
    """
    EventBus 전역 이벤트를 수신하여 ThrottlePolicy의 limit을 조정하는 컴포넌트.

    AdaptiveThrottle에서 분리된 4개 EventBus 구독 로직을 통합:
    - RATE_LIMIT_429 → limit 감소
    - RATE_LIMIT_COOLDOWN_END → Recovery Dampening 시작
    - ERROR_BUDGET_WARNING/CRITICAL → limit 감소
    - ERROR_BUDGET_RECOVERED → Recovery Dampening 시작
    - LOAD_SHEDDING_LEVEL_CHANGED → limit 조정
    - KILL_SWITCH_ACTIVATED → Gradient Freeze
    - KILL_SWITCH_DEACTIVATED → Recovery Dampening 시작

    HedgingConfigUpdateHook (hedging.py)과 동일한 수명주기 패턴:
    register(policy) → start() → EventBus 구독 시작
    """

    def __init__(self) -> None:
        self._policies: list[Any] = []
        self._started = False

    def register(self, policy: Any) -> None:
        """
        limit 조정 대상 ThrottlePolicy 등록.

        Args:
            policy: ThrottlePolicy 인스턴스.
                reduce_limit_for_429(), adjust_limit_for_error_budget(),
                adjust_limit_for_shedding(), start_recovery_dampening(),
                gradient_frozen setter를 노출해야 한다.
        """
        self._policies.append(policy)

    def start(self) -> None:
        """
        EventBus 구독 시작.

        EventBus가 없는 환경에서는 아무 동작도 하지 않는다 (Fail-Open).
        중복 호출 시 무시한다.
        """
        if self._started:
            return

        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()

            # 429 Rate Limit
            bus.subscribe(EventType.RATE_LIMIT_429, self._handle_rate_limit_429)
            bus.subscribe(
                EventType.RATE_LIMIT_COOLDOWN_END,
                self._handle_cooldown_end,
            )

            # Error Budget
            bus.subscribe(
                EventType.ERROR_BUDGET_WARNING,
                self._handle_error_budget_warning,
            )
            bus.subscribe(
                EventType.ERROR_BUDGET_CRITICAL,
                self._handle_error_budget_critical,
            )
            bus.subscribe(
                EventType.ERROR_BUDGET_RECOVERED,
                self._handle_error_budget_recovered,
            )

            # Load Shedding
            bus.subscribe(
                EventType.LOAD_SHEDDING_LEVEL_CHANGED,
                self._handle_shedding_changed,
            )

            # Kill Switch
            bus.subscribe(
                EventType.KILL_SWITCH_ACTIVATED,
                self._handle_kill_switch_activated,
            )
            bus.subscribe(
                EventType.KILL_SWITCH_DEACTIVATED,
                self._handle_kill_switch_deactivated,
            )

            self._started = True
            logger.info(
                "[ThrottleLimitAdjuster] Subscribed to %d event types " "for %d policies",
                8,
                len(self._policies),
            )
        except ImportError:
            logger.debug(
                "[ThrottleLimitAdjuster] EventBus not available (fail-open)",
            )
        except Exception as e:
            logger.warning(
                "[ThrottleLimitAdjuster] EventBus subscription failed " "(fail-open): %s",
                e,
            )

    # =========================================================================
    # 429 Rate Limit 핸들러
    # =========================================================================

    def _handle_rate_limit_429(self, event: Any) -> None:
        """429 이벤트 → 등록된 모든 Policy의 limit 감소."""
        for policy in self._policies:
            try:
                policy.reduce_limit_for_429(event)
            except Exception as e:
                logger.debug(
                    "[ThrottleLimitAdjuster] 429 handler failed " "(fail-open): %s",
                    e,
                )

    def _handle_cooldown_end(self, event: Any) -> None:
        """429 Cooldown 종료 → Recovery Dampening 시작."""
        for policy in self._policies:
            try:
                policy.start_recovery_dampening()
            except Exception as e:
                logger.debug(
                    "[ThrottleLimitAdjuster] Cooldown end handler failed " "(fail-open): %s",
                    e,
                )

    # =========================================================================
    # Error Budget 핸들러
    # =========================================================================

    def _handle_error_budget_warning(self, event: Any) -> None:
        """Error Budget Warning → limit 20% 감소."""
        for policy in self._policies:
            try:
                policy.adjust_limit_for_error_budget(multiplier=0.8)
            except Exception as e:
                logger.debug(
                    "[ThrottleLimitAdjuster] Error budget warning handler " "failed (fail-open): %s",
                    e,
                )

    def _handle_error_budget_critical(self, event: Any) -> None:
        """Error Budget Critical → limit 50% 감소."""
        for policy in self._policies:
            try:
                policy.adjust_limit_for_error_budget(multiplier=0.5)
            except Exception as e:
                logger.debug(
                    "[ThrottleLimitAdjuster] Error budget critical handler " "failed (fail-open): %s",
                    e,
                )

    def _handle_error_budget_recovered(self, event: Any) -> None:
        """Error Budget 복구 → Recovery Dampening 시작."""
        for policy in self._policies:
            try:
                policy.start_recovery_dampening()
            except Exception as e:
                logger.debug(
                    "[ThrottleLimitAdjuster] Error budget recovered handler " "failed (fail-open): %s",
                    e,
                )

    # =========================================================================
    # Load Shedding 핸들러
    # =========================================================================

    def _handle_shedding_changed(self, event: Any) -> None:
        """Load Shedding 레벨 변경 → limit 조정."""
        event_data = event.data if hasattr(event, "data") else event
        traffic_limit_percent = event_data.get("traffic_limit", 100.0)
        new_level = event_data.get("new_level", -1)

        for policy in self._policies:
            try:
                if new_level < 0:
                    # Shedding 해제 → 원래 limit 복원
                    policy.adjust_limit_for_shedding(
                        suggested_limit=policy._config.max_limit,
                    )
                else:
                    # Shedding 적용 → traffic_limit 비율로 limit 조정
                    suggested = int(
                        policy._config.initial_limit * (traffic_limit_percent / 100.0),
                    )
                    policy.adjust_limit_for_shedding(
                        suggested_limit=max(
                            suggested,
                            policy._config.min_limit,
                        ),
                    )
            except Exception as e:
                logger.debug(
                    "[ThrottleLimitAdjuster] Shedding handler failed " "(fail-open): %s",
                    e,
                )

    # =========================================================================
    # Kill Switch 핸들러
    # =========================================================================

    def _handle_kill_switch_activated(self, event: Any) -> None:
        """Kill Switch 활성화 → Gradient Freeze (limit 유지)."""
        for policy in self._policies:
            try:
                policy.gradient_frozen = True
                logger.warning(
                    "[ThrottleLimitAdjuster] Kill Switch activated: " "gradient frozen for policy",
                )
            except Exception as e:
                logger.debug(
                    "[ThrottleLimitAdjuster] Kill switch activated handler " "failed (fail-open): %s",
                    e,
                )

    def _handle_kill_switch_deactivated(self, event: Any) -> None:
        """Kill Switch 비활성화 → Gradient 재개 + Recovery Dampening 시작."""
        for policy in self._policies:
            try:
                policy.gradient_frozen = False
                policy.start_recovery_dampening()
                logger.info(
                    "[ThrottleLimitAdjuster] Kill Switch deactivated: " "gradient unfrozen, recovery started",
                )
            except Exception as e:
                logger.debug(
                    "[ThrottleLimitAdjuster] Kill switch deactivated handler " "failed (fail-open): %s",
                    e,
                )
