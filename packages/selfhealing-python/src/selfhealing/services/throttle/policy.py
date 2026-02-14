"""
ThrottlePolicy — 순수 rate limit Policy.

AdaptiveThrottle에서 13건의 외부 의존성을 분리한 순수 Policy.
SlidingWindowThrottle의 rate limit 로직 + GradientCalculator의
RTT 기반 limit 자동 조정만 담당한다.

Guard/Hook/Sink는 PolicyComposer가 외부에서 연결한다.

구현 원칙:
    - SlidingWindowThrottle을 내부 엔진으로 래핑 (상속 아님)
    - GradientCalculator로 RTT gradient 기반 limit 동적 조정
    - RecoveryDampeningManager 연동 시 Dampening Cap 적용
    - 비즈니스 예외는 PolicyResult(FAILURE)로 변환
    - context.extra["throttle_key"]로 요청별 키 지정 가능
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from selfhealing.interfaces.resilience_policy import (
    PolicyContext,
    PolicyOutcome,
    PolicyResult,
)
from selfhealing.services.throttle.base import SlidingWindowThrottle
from selfhealing.services.throttle.config import ThrottleConfig, ThrottleResult

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ThrottlePolicy:
    """
    순수 Throttle Policy — SlidingWindowThrottle + GradientCalculator 래핑.

    13건의 하드코딩 외부 의존성(Emergency, CB, ErrorBudget, KillSwitch,
    LoadShedding, DLQ, Prometheus, Audit 등)을 모두 제거하고
    rate limit 검사 + gradient 기반 limit 조정만 수행한다.

    Guard/Hook/Sink는 PolicyComposer.add_guard()/add_hook()/add_sink()으로
    소비자가 선택적으로 등록한다.

    사용 예시::

        from selfhealing.services.throttle.policy import ThrottlePolicy
        policy = ThrottlePolicy(config=ThrottleConfig(initial_limit=100))
        result = policy.execute(call_api, order_id=123)
    """

    def __init__(
        self,
        config: ThrottleConfig | None = None,
        gradient_calculator: Any | None = None,
        dampening_manager: Any | None = None,
    ) -> None:
        """
        초기화.

        Args:
            config: Throttle 설정 (ThrottleSettings 호환).
            gradient_calculator: GradientCalculator 인스턴스.
                None이면 config.smoothing_factor로 기본 생성.
            dampening_manager: RecoveryDampeningManager 인스턴스.
                None이면 Dampening Cap 미적용 (gradient가 max_limit까지 자유 조정).
        """
        from selfhealing.services.throttle.adaptive import GradientCalculator

        self._config = config or ThrottleConfig()
        self._engine = SlidingWindowThrottle(self._config)
        self._gradient = gradient_calculator or GradientCalculator(
            smoothing_factor=self._config.smoothing_factor,
        )
        self._dampening_manager = dampening_manager
        self._current_limit = self._config.initial_limit
        self._gradient_frozen = False

        # 내부 엔진의 limit을 동기화
        self._engine.current_limit = self._current_limit

    @property
    def name(self) -> str:
        """Policy 식별자."""
        return "throttle"

    @property
    def current_limit(self) -> int:
        """현재 rate limit."""
        return self._current_limit

    @current_limit.setter
    def current_limit(self, value: int) -> None:
        """현재 rate limit 변경 (ThrottleLimitAdjuster가 사용)."""
        self._current_limit = max(
            self._config.min_limit,
            min(value, self._config.max_limit),
        )
        self._engine.current_limit = self._current_limit

    @property
    def gradient_frozen(self) -> bool:
        """Gradient 조정 Freeze 여부."""
        return self._gradient_frozen

    @gradient_frozen.setter
    def gradient_frozen(self, value: bool) -> None:
        """Gradient Freeze 설정 (ThrottleLimitAdjuster가 KillSwitch 시 사용)."""
        self._gradient_frozen = value

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        Throttle 검사 후 함수 실행.

        rate limit 통과 시 함수를 실행하고 RTT를 측정하여
        gradient 기반으로 limit을 자동 조정한다.

        throttle key 결정 순서:
        1. context.extra["throttle_key"] (요청별 지정)
        2. config.service_name (Policy 인스턴스별 기본값)
        3. "default" (최종 폴백)

        Args:
            func: 실행할 함수
            *args: 함수 위치 인자
            context: PolicyContext (throttle_key, tier_id 등)
            **kwargs: 함수 키워드 인자

        Returns:
            PolicyResult — 통과 시 함수 실행 결과, 거부 시 REJECTED
        """
        key = self._resolve_throttle_key(context)
        result = self._engine.check(key)

        if not result.allowed:
            return PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                metadata={
                    "policy": "throttle",
                    "reason": result.reason or "rate_limit_exceeded",
                    "limit": result.limit,
                    "remaining": result.remaining,
                },
            )

        # Rate limit 통과 → 함수 실행
        start = time.time()
        try:
            value = func(*args, **kwargs)
            elapsed_ms = (time.time() - start) * 1000
            self._gradient.add_sample(elapsed_ms)
            self._maybe_adjust_limit(elapsed_ms)

            return PolicyResult(
                value=value,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["throttle"],
                total_duration_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            return PolicyResult(
                outcome=PolicyOutcome.FAILURE,
                error=e,
                executed_policies=["throttle"],
                total_duration_ms=elapsed_ms,
            )

    def check(self, key: str) -> ThrottleResult:
        """
        순수 rate limit 검사 (execute() 없이 사용).

        AdaptiveThrottleFacade에서 레거시 API 호환을 위해 사용한다.

        Args:
            key: 요청 식별자

        Returns:
            ThrottleResult — 허용/거부 판정
        """
        return self._engine.check(key)

    def record_response(self, rtt_ms: float) -> None:
        """
        RTT 기록 + gradient 기반 limit 동적 조정.

        execute() 외부에서 함수를 직접 실행할 때 사용한다.
        AdaptiveThrottleFacade의 레거시 record_response() API를 지원한다.

        Args:
            rtt_ms: 응답 시간 (밀리초)
        """
        self._gradient.add_sample(rtt_ms)
        self._maybe_adjust_limit(rtt_ms)

    def reduce_limit_for_429(self, event: Any) -> None:
        """
        429 Rate Limit 이벤트에 의한 limit 감소.

        ThrottleLimitAdjuster가 EventBus RATE_LIMIT_429 이벤트 수신 시 호출한다.
        AdaptiveThrottle._handle_rate_limit_429()과 동일한 감소 로직.

        Args:
            event: EventBus 이벤트 (data.reduction_percent 참조)
        """
        event_data = event.data if hasattr(event, "data") else event
        reduction_percent = event_data.get("reduction_percent", 50.0)
        multiplier = 1.0 - (reduction_percent / 100.0)

        previous = self._current_limit
        self.current_limit = max(
            int(self._current_limit * multiplier),
            self._config.min_limit,
        )
        logger.info(
            "[ThrottlePolicy] 429 limit reduced: %d → %d (%.0f%%)",
            previous,
            self._current_limit,
            reduction_percent,
        )

    def adjust_limit_for_error_budget(
        self,
        multiplier: float,
    ) -> None:
        """
        Error Budget 이벤트에 의한 limit 조정.

        ThrottleLimitAdjuster가 ERROR_BUDGET_WARNING/CRITICAL 이벤트 수신 시 호출한다.

        Args:
            multiplier: 적용할 배율 (0.8=20% 감소, 0.5=50% 감소)
        """
        previous = self._current_limit
        self.current_limit = max(
            int(self._config.initial_limit * multiplier),
            self._config.min_limit,
        )
        logger.info(
            "[ThrottlePolicy] Error budget limit adjusted: %d → %d (×%.2f)",
            previous,
            self._current_limit,
            multiplier,
        )

    def adjust_limit_for_shedding(
        self,
        suggested_limit: int,
    ) -> None:
        """
        Load Shedding 이벤트에 의한 limit 조정.

        ThrottleLimitAdjuster가 LOAD_SHEDDING_LEVEL_CHANGED 이벤트 수신 시 호출한다.

        Args:
            suggested_limit: 권장 limit (현재 limit보다 낮으면 적용)
        """
        if suggested_limit < self._current_limit:
            previous = self._current_limit
            self.current_limit = suggested_limit
            logger.info(
                "[ThrottlePolicy] Shedding limit adjusted: %d → %d",
                previous,
                self._current_limit,
            )

    def start_recovery_dampening(self, target_limit: int | None = None) -> None:
        """
        Recovery Dampening 시작.

        ThrottleLimitAdjuster가 KILL_SWITCH_DEACTIVATED 또는
        ERROR_BUDGET_RECOVERED 이벤트 수신 시 호출한다.

        Args:
            target_limit: 복구 목표 limit. None이면 config.initial_limit 사용.
        """
        if self._dampening_manager is None:
            return

        target = target_limit or self._config.initial_limit
        phase_1_limit = self._dampening_manager.start_recovery(
            service_name=self._config.service_name,
            target_limit=target,
            current_limit=self._current_limit,
        )
        self.current_limit = phase_1_limit
        logger.info(
            "[ThrottlePolicy] Recovery dampening started: target=%d, phase_1=%d",
            target,
            phase_1_limit,
        )

    def _resolve_throttle_key(self, context: PolicyContext | None) -> str:
        """
        Throttle key 결정.

        우선순위:
        1. context.extra["throttle_key"]
        2. config.service_name
        3. "default"
        """
        if context and context.extra:
            key = context.extra.get("throttle_key")
            if key:
                return str(key)
        return self._config.service_name or "default"

    def _maybe_adjust_limit(self, rtt_ms: float) -> None:
        """
        Gradient 기반 limit 조정 — 순수 RTT/SLA 로직만 수행.

        AdaptiveThrottle._maybe_adjust_limit()에서 거버넌스 체크,
        Emergency 동기화, Kill Switch Drift 교정 등 외부 의존성을
        모두 제거한 순수 Gradient + SLA 기반 limit 변경 로직.

        Dampening 활성 시 limit 상향에만 Cap을 적용한다.
        limit 감소는 Dampening 중에도 허용 (서비스 보호).
        """
        if self._gradient_frozen:
            logger.debug(
                "[ThrottlePolicy] Gradient frozen, " "skipping limit adjustment (RTT data collected)",
            )
            return

        gradient = self._gradient.get_gradient()
        previous_limit = self._current_limit

        if rtt_ms >= self._config.sla_critical_ms:
            # SLA Critical 임계값 초과 → 급격한 감소 (30%)
            self._current_limit = max(
                int(self._current_limit * 0.7),
                self._config.min_limit,
            )
        elif rtt_ms >= self._config.sla_warning_ms:
            # SLA Warning 임계값 초과 → 점진적 감소 (config.decrease_ratio)
            self._current_limit = max(
                int(self._current_limit * self._config.decrease_ratio),
                self._config.min_limit,
            )
        elif gradient > 0.1:
            # RTT 상승 추세 → 점진적 감소
            self._current_limit = max(
                int(self._current_limit * self._config.decrease_ratio),
                self._config.min_limit,
            )
        elif gradient < -0.05:
            # RTT 하강 추세 → 점진적 증가 (Dampening Cap 적용)
            new_limit = self._current_limit + self._config.increase_step
            new_limit = self._apply_dampening_cap(new_limit)
            self._current_limit = min(new_limit, self._config.max_limit)

        # 내부 엔진 limit 동기화
        if self._current_limit != previous_limit:
            self._engine.current_limit = self._current_limit

    def _apply_dampening_cap(self, new_limit: int) -> int:
        """
        Recovery Dampening Cap 적용.

        Dampening 활성 시 gradient에 의한 limit 상향을
        현재 복구 단계의 dampened_limit 이하로 제한한다.

        dampened_limit = target_limit × current_multiplier
        (RecoveryDampeningManager의 기존 공개 API만 사용)

        Args:
            new_limit: gradient가 계산한 새 limit

        Returns:
            Dampening Cap이 적용된 limit
        """
        if self._dampening_manager is None:
            return new_limit

        service_name = self._config.service_name
        if not self._dampening_manager.is_recovery_active(service_name):
            return new_limit

        # RecoveryDampeningManager의 기존 공개 API로 dampened_limit 계산
        # get_current_multiplier() → 현재 단계 배율 (0.8/0.9/1.0)
        # get_recovery_state() → target_limit 포함
        recovery_state = self._dampening_manager.get_recovery_state(service_name)
        if recovery_state is None:
            return new_limit

        target_limit = recovery_state.get("target_limit", self._config.max_limit)
        multiplier = self._dampening_manager.get_current_multiplier(service_name)
        dampened_limit = int(target_limit * multiplier)

        if new_limit > dampened_limit:
            logger.debug(
                "[ThrottlePolicy] Dampening cap applied: " "gradient=%d → capped=%d (phase multiplier=%.2f)",
                new_limit,
                dampened_limit,
                multiplier,
            )
            return dampened_limit

        return new_limit
