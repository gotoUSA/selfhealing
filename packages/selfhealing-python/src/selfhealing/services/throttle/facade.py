"""
AdaptiveThrottleFacade — 레거시 check()/record_response() API 호환 Facade.

기존 AdaptiveThrottle의 check() + record_response() 2단계 API를
ThrottlePolicy 기반으로 위임하여 하위 호환성을 보장한다.

레거시 소비자:
    - services/throttle/__init__.py의 Usage 패턴
    - throttle_adapter.py
    - throttle_simulation.py
    - load_tests controller

기존 API 동일 시그니처:
    result = throttle.check("user_123")
    throttle.record_response(response_time_ms=45.2)

내부적으로 Guards → ThrottlePolicy.check() → DLQ Sink 순서로 위임한다.
"""

from __future__ import annotations

from typing import Any

import structlog

from selfhealing.services.throttle.config import ThrottleResult

logger = structlog.get_logger()


class AdaptiveThrottleFacade:
    """
    레거시 check()/record_response() API를 유지하는 Facade.

    내부적으로 ThrottlePolicy에 위임한다.

    사용 예시::

        facade = AdaptiveThrottleFacade(
            policy=throttle_policy,
            guards=[governance_guard, full_stop_guard],
            sinks=[dlq_sink],
        )
        result = facade.check("user_123")
        facade.record_response(rtt_ms=45.2)
    """

    def __init__(
        self,
        policy: Any,
        guards: list[Any] | None = None,
        limit_adjuster: Any | None = None,
        sinks: list[Any] | None = None,
    ) -> None:
        """
        초기화.

        Args:
            policy: ThrottlePolicy 인스턴스
            guards: Guard 목록 (ThrottleGovernanceGuard, FullStopGuard 등)
            limit_adjuster: ThrottleLimitAdjuster (EventBus limit 조정 컴포넌트)
            sinks: ThrottleDLQSink 등 거부 시 처리 컴포넌트 목록
        """
        self._policy = policy
        self._guards = guards or []
        self._limit_adjuster = limit_adjuster
        self._sinks = sinks or []

    @property
    def current_limit(self) -> int:
        """현재 rate limit (ThrottlePolicy에 위임)."""
        return self._policy.current_limit

    @current_limit.setter
    def current_limit(self, value: int) -> None:
        """현재 rate limit 설정 (ThrottlePolicy에 위임)."""
        self._policy.current_limit = value

    def check(
        self,
        key: str,
        tier_id: str = "standard",
        context: dict[str, Any] | None = None,
        store_rejection: bool = True,
    ) -> ThrottleResult:
        """
        기존 AdaptiveThrottle.check() API와 동일한 시그니처.

        실행 순서:
        1. Guards 순서대로 체크 (하나라도 거부하면 즉시 반환)
        2. ThrottlePolicy 내부 엔진의 rate limit 체크
        3. 거부 시 DLQ Sink 처리

        Args:
            key: 요청 식별자 (user_id, ip 등)
            tier_id: 요청 티어 ("critical"/"standard"/"non_essential")
            context: 요청 컨텍스트 (DLQ 저장용, domain/order_id 등)
            store_rejection: 거부 시 DLQ 저장 여부

        Returns:
            ThrottleResult — 허용/거부 판정 + 적응형 정보
        """
        # Guard 체크
        for guard in self._guards:
            try:
                guard_result = guard.check()
                if not guard_result.allowed:
                    throttle_result = ThrottleResult(
                        allowed=False,
                        current_count=0,
                        limit=self._policy.current_limit,
                        remaining=0,
                        reset_at=0,
                        reason=guard_result.reason,
                    )

                    if store_rejection and context:
                        self._store_rejection(
                            context,
                            guard_result.reason or "guard_rejected",
                        )

                    return throttle_result
            except Exception as e:
                logger.debug(
                    "[AdaptiveThrottleFacade] Guard %s failed (fail-open): %s",
                    getattr(guard, "name", "unknown"),
                    e,
                )

        # 순수 rate limit 체크
        throttle_result = self._policy.check(key)

        # 거부 시 DLQ Sink 처리
        if not throttle_result.allowed and store_rejection and context:
            self._store_rejection(
                context,
                throttle_result.reason or "rate_limit_exceeded",
            )

        return throttle_result

    def record_response(self, rtt_ms: float) -> None:
        """
        RTT 기록 + Gradient 기반 limit 동적 조정.

        기존 AdaptiveThrottle.record_response()와 동일한 동작을
        ThrottlePolicy에 위임한다.

        Args:
            rtt_ms: 응답 시간 (밀리초)
        """
        self._policy.record_response(rtt_ms)

    def get_stats(self) -> dict[str, Any]:
        """Throttle 통계 반환."""
        return {
            "current_limit": self._policy.current_limit,
            "min_limit": self._policy._config.min_limit,
            "max_limit": self._policy._config.max_limit,
            "gradient_frozen": self._policy.gradient_frozen,
            "guards": [getattr(g, "name", type(g).__name__) for g in self._guards],
        }

    def _store_rejection(self, context: dict[str, Any], reason: str) -> None:
        """거부 요청을 DLQ Sink에 저장 (Fail-Open)."""
        for sink in self._sinks:
            try:
                sink.handle_rejection(context, reason)
            except Exception as e:
                logger.debug(
                    "[AdaptiveThrottleFacade] Sink failed (fail-open): %s",
                    e,
                )
