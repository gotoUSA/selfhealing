"""
RateLimitHandlerMixin for AdaptiveThrottle.

이 모듈은 selfhealing.services.throttle.adaptive 패키지의 내부 구현입니다.
"""

import time

import structlog

import selfhealing.services.throttle.adaptive as _adaptive_mod

logger = structlog.get_logger()







class RateLimitHandlerMixin:
    """AdaptiveThrottle RateLimitHandlerMixin."""

    # =========================================================================
    # 429 Rate Limit EventBus 연동
    # =========================================================================

    def _subscribe_rate_limit_events(self) -> None:
        """Rate Limit 이벤트 구독 등록 (Fail-Open)."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()

            # 429 이벤트 구독
            bus.subscribe(EventType.RATE_LIMIT_429, self._handle_rate_limit_429)

            # Cooldown 종료 이벤트 구독
            bus.subscribe(EventType.RATE_LIMIT_COOLDOWN_END, self._handle_cooldown_end)

            logger.info("adaptive_throttle.subscribed_rate_limit_events")
        except ImportError:
            logger.debug("adaptive_throttle.eventbus_available_subscription")
        except Exception as e:
            logger.warning(
                "adaptive_throttle.failed_subscribe",
                error=e,
            )

    def _handle_rate_limit_429(self, event) -> None:
        """
        429 이벤트 수신 시 limit 조정.

        전략:
        - consecutive_429s에 따른 단계별 감소
        - 1회: 20% 감소
        - 2회: 40% 감소
        - 3회 이상: 50% 감소 + SLA Warning 발행
        """
        # SelfHealingEvent에서 data 추출
        event_data = event.data if hasattr(event, "data") else event

        key = event_data.get("key", "unknown")
        consecutive = event_data.get("consecutive_429s", 1)
        cooldown_until = event_data.get("cooldown_until", 0)

        # Cooldown 상태 저장
        self._rate_limit_keys[key] = cooldown_until

        # 429 감소 전 limit 저장 (CRITICAL 보호용)
        if not self._429_reduction_active:
            self._limit_before_429 = self._current_limit

        self._429_reduction_active = True

        # 감소 비율 결정
        if consecutive >= 3:
            reduction_percent = 0.5  # 50%
        elif consecutive == 2:
            reduction_percent = 0.6  # 40%
        else:
            reduction_percent = 0.8  # 20%

        previous_limit = self._current_limit

        # 429 기반 limit 계산
        self._429_suggested_limit = max(
            int(self._current_limit * reduction_percent),
            self.config.min_limit,
        )

        # Conservative Limit 적용 (Min-Winner)
        new_limit = self.conservative_limit

        logger.warning(
            "adaptive_throttle.response_reducing_limit",
            key=key,
            previous_limit=previous_limit,
            new_limit=new_limit,
            consecutive=consecutive,
            int=int((1-reduction_percent)*100),
        )

        self.current_limit = new_limit

        # Prometheus 메트릭 기록
        _adaptive_mod._record_throttle_metrics(
            service=self._service_name,
            limit=new_limit,
            denied_reason="rate_limit_429",
        )

        # SLA Warning 발행 (3회 이상)
        if consecutive >= 3:
            _adaptive_mod._emit_throttle_event(
                "THROTTLE_SLA_WARNING",
                {
                    "trigger": "rate_limit_429",
                    "key": key,
                    "consecutive_429s": consecutive,
                    "current_limit": new_limit,
                    "previous_limit": previous_limit,
                },
                priority_name="HIGH",
            )

        # Limit 변경 이벤트 발행
        _adaptive_mod._emit_throttle_event(
            "THROTTLE_LIMIT_CHANGED",
            {
                "previous_limit": previous_limit,
                "new_limit": new_limit,
                "reason": "rate_limit_429",
                "key": key,
                "consecutive_429s": consecutive,
            },
            priority_name="HIGH",
        )

        # 감사 로깅 (429 응답 처리)
        _adaptive_mod._record_audit_safe(
            action="throttle_429_response",
            old_limit=previous_limit,
            new_limit=new_limit,
            key=key,
            consecutive_429s=consecutive,
            reduction_percent=int((1 - reduction_percent) * 100),
        )

    def _handle_cooldown_end(self, event) -> None:
        """
        Cooldown 종료 시 Recovery Dampening 시작.

        CRITICAL 보호 해제 및 기존 RECOVERY_DAMPENING_MULTIPLIERS 활용.
        """
        # SelfHealingEvent에서 data 추출
        event_data = event.data if hasattr(event, "data") else event

        key = event_data.get("key", "unknown")

        # 해당 Key의 429 상태 제거
        if key in self._rate_limit_keys:
            del self._rate_limit_keys[key]

        # CRITICAL 보호 해제
        self._429_reduction_active = False

        # Recovery Dampening 시작 (기존 메서드 활용)
        self.start_recovery_dampening()

        logger.info(
            "adaptive_throttle.cooldown_ended_starting_recovery",
            key=key,
        )

    def is_rate_limited_for_key(self, key: str) -> bool:
        """특정 외부 API가 현재 cooldown 상태인지 확인."""
        cooldown_until = self._rate_limit_keys.get(key, 0)
        return time.time() < cooldown_until

