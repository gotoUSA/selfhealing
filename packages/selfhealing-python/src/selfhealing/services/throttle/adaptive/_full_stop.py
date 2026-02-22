"""
FullStopMixin for AdaptiveThrottle.

이 모듈은 selfhealing.services.throttle.adaptive 패키지의 내부 구현입니다.
"""

import structlog

import selfhealing.services.throttle.adaptive as _adaptive_mod

logger = structlog.get_logger()


class FullStopMixin:
    """AdaptiveThrottle FullStopMixin."""

    # =========================================================================
    # Phase 4: Full Stop 조건 (3중 조건: LEVEL_3 + DB_CB_OPEN + BUDGET_EXHAUSTED)
    # =========================================================================

    def check_full_stop_conditions(self) -> tuple[bool, str]:
        """
        Full Stop 3중 조건 확인.

        조건:
        1. Emergency LEVEL_3 상태
        2. 핵심 DB Circuit Breaker OPEN 상태
        3. Error Budget 소진 (0% 이하)

        Returns:
            (is_full_stop, reason): Full Stop 여부와 사유
        """
        reasons = []

        # 조건 1: Emergency LEVEL_3
        is_level_3 = self._emergency_level >= 3
        if is_level_3:
            reasons.append("EMERGENCY_LEVEL_3")

        # 조건 2: DB Circuit Breaker OPEN 확인
        db_cb_open = self._check_db_circuit_breaker_open()
        if db_cb_open:
            reasons.append("DB_CB_OPEN")

        # 조건 3: Error Budget 소진 확인
        budget_exhausted = self._check_error_budget_exhausted()
        if budget_exhausted:
            reasons.append("BUDGET_EXHAUSTED")

        # 3중 조건 모두 충족 시 Full Stop
        is_full_stop = is_level_3 and db_cb_open and budget_exhausted
        reason = " + ".join(reasons) if reasons else "NORMAL"

        return is_full_stop, reason

    def _check_db_circuit_breaker_open(self) -> bool:
        """
        핵심 DB Circuit Breaker OPEN 상태 확인.

        Returns:
            True if any DB circuit breaker is OPEN
        """
        try:
            from selfhealing.services.circuit_breaker_service import (
                get_circuit_breaker_service,
            )

            cb_service = get_circuit_breaker_service()

            # 핵심 DB 서비스 목록 (설정 가능하도록 확장 가능)
            db_services = ["database", "db", "postgres", "mysql", "redis", "mongodb"]

            for service_name in db_services:
                try:
                    state = cb_service.get_state(service_name)
                    if state == "open":
                        logger.debug(
                            "adaptive_throttle.db_cb_open_detected",
                            service_name=service_name,
                        )
                        return True
                except Exception:
                    # 서비스가 존재하지 않으면 스킵
                    pass

            return False
        except ImportError:
            logger.debug("adaptive_throttle.circuitbreakerservice_available")
            return False
        except Exception as e:
            logger.warning(
                "adaptive_throttle.failed_check_db_cb",
                error=e,
            )
            return False

    def _check_error_budget_exhausted(self) -> bool:
        """
        Error Budget 소진 상태 확인.

        Returns:
            True if error budget is exhausted (0% or less)
        """
        try:
            from selfhealing.services.error_budget_service import (
                get_error_budget_service,
            )

            service = get_error_budget_service()
            status = service.get_budget_status()

            is_exhausted = status.budget_remaining_percent <= 0
            if is_exhausted:
                logger.debug(
                    "adaptive_throttle.budget_exhausted",
                    status=status.budget_remaining_percent,
                )
            return is_exhausted
        except ImportError:
            logger.debug("adaptive_throttle.errorbudgetservice_available")
            return False
        except Exception as e:
            logger.warning(
                "adaptive_throttle.failed_check_error_budget",
                error=e,
            )
            return False

    def activate_full_stop(self, reason: str) -> None:
        """
        Full Stop 활성화: min_limit=0으로 모든 요청 차단.

        Args:
            reason: Full Stop 사유
        """
        if self._full_stop_active:
            return

        self._full_stop_active = True

        # min_limit을 0으로 설정하여 완전 차단
        previous_limit = self._current_limit
        self._current_limit = 0

        logger.critical(
            "adaptive_throttle.full_stop_activated_limit",
            reason=reason,
            previous_limit=previous_limit,
        )

        # KILL_SWITCH_ACTIVATED 이벤트 발행
        _adaptive_mod._emit_throttle_event(
            "THROTTLE_LIMIT_CHANGED",
            {
                "previous_limit": previous_limit,
                "new_limit": 0,
                "reason": f"full_stop:{reason}",
                "full_stop": True,
            },
            priority_name="CRITICAL",
        )

        # Kill Switch 이벤트도 발행하여 다른 컴포넌트에 알림
        try:
            from selfhealing.services.event_bus import (
                EventPriority,
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.KILL_SWITCH_ACTIVATED,
                data={
                    "reason": f"throttle_full_stop:{reason}",
                    "activated_by": "throttle",
                    "conditions": reason,
                },
                source="throttle",
                priority=EventPriority.CRITICAL,
            )
        except Exception as e:
            logger.warning(
                "adaptive_throttle.failed_emit",
                error=e,
            )

        # 감사 로깅 (Full Stop 활성화 → CascadeEvent 포함)
        _adaptive_mod._record_audit_safe(
            action="throttle_full_stop_activated",
            old_limit=previous_limit,
            new_limit=0,
            full_stop_reason=reason,
            trigger_source="full_stop",
        )

    def deactivate_full_stop(self) -> None:
        """
        Full Stop 비활성화: limit 복구 시작.
        """
        if not self._full_stop_active:
            return

        self._full_stop_active = False

        # Recovery Dampening으로 복구 시작
        self.start_recovery_dampening()

        logger.warning("adaptive_throttle.full_stop_deactivated_starting")

        # 감사 로깅 (Full Stop 비활성화 → CascadeEvent 포함)
        _adaptive_mod._record_audit_safe(
            action="throttle_full_stop_deactivated",
            old_limit=0,
            new_limit=self._current_limit,
            trigger_source="full_stop_recovery",
        )

    def is_full_stop_active(self) -> bool:
        """Full Stop 활성화 여부."""
        return self._full_stop_active
