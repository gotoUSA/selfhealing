"""
ErrorBudgetHandlerMixin for AdaptiveThrottle.

이 모듈은 selfhealing.services.throttle.adaptive 패키지의 내부 구현입니다.
"""

import structlog

import selfhealing.services.throttle.adaptive as _adaptive_mod
from selfhealing.services.throttle.config import ThrottleResult

logger = structlog.get_logger()


class ErrorBudgetHandlerMixin:
    """AdaptiveThrottle ErrorBudgetHandlerMixin."""

    # =========================================================================
    # Error Budget EventBus 연동
    # =========================================================================

    def _subscribe_error_budget_events(self) -> None:
        """Error Budget 이벤트 구독 등록 (Fail-Open)."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()

            # ERROR_BUDGET_WARNING 구독
            bus.subscribe(EventType.ERROR_BUDGET_WARNING, self._handle_error_budget_warning)

            # ERROR_BUDGET_CRITICAL 구독
            bus.subscribe(EventType.ERROR_BUDGET_CRITICAL, self._handle_error_budget_critical)

            # ERROR_BUDGET_RECOVERED 구독
            bus.subscribe(EventType.ERROR_BUDGET_RECOVERED, self._handle_error_budget_recovered)

            logger.info("adaptive_throttle.subscribed_error_budget_events")
        except ImportError:
            logger.debug("adaptive_throttle.eventbus_available_error_budget")
        except Exception as e:
            logger.warning(
                "adaptive_throttle.failed_subscribe_error_budget",
                error=e,
            )

    def set_target_slo_patterns(self, patterns: list[str]) -> None:
        """
        이 Throttle이 반응할 SLO 패턴 설정.

        Args:
            patterns: SLO name 패턴 리스트 (prefix 매칭)
                      예: ["availability:payment"] → payment 도메인만 반응
        """
        self._target_slo_patterns = patterns
        logger.info(
            "adaptive_throttle.target_slo_patterns",
            patterns=patterns,
        )

    def _should_react_to_slo(self, slo_name: str) -> bool:
        """이벤트의 SLO가 이 Throttle의 반응 대상인지 확인."""
        if not self._target_slo_patterns:
            return True  # 패턴 미설정 시 모든 이벤트 반응

        return any(slo_name.startswith(pattern) or pattern == "*" for pattern in self._target_slo_patterns)

    def _handle_error_budget_warning(self, event) -> None:
        """
        Error Budget Warning 이벤트 처리.

        Warning 단계 (10-20%): Limit 20% 감소.
        Recovery Dampening 미적용 (경고 수준이므로 빠른 복구 허용).
        """
        event_data = event.data if hasattr(event, "data") else event

        budget_percent = event_data.get("budget_percent", 100.0)
        slo_name = event_data.get("slo_name", "availability")

        # SLO 필터링
        if not self._should_react_to_slo(slo_name):
            logger.debug(
                "adaptive_throttle.ignoring_warning_slo_target",
                slo_name=slo_name,
                _self=self._target_slo_patterns,
            )
            return

        # Warning 상태 진입 시 limit 저장
        if not self._error_budget_limit_reduction_active:
            self._limit_before_error_budget_reduction = self._current_limit

        self._error_budget_limit_reduction_active = True
        self._error_budget_multiplier = 0.8  # 20% 감소

        previous_limit = self._current_limit
        new_limit = max(
            int(self._limit_before_error_budget_reduction * self._error_budget_multiplier),
            self.config.min_limit,
        )

        self.current_limit = new_limit

        logger.warning(
            "adaptive_throttle.error_budget_warning_limit",
            budget_percent=budget_percent,
            previous_limit=previous_limit,
            new_limit=new_limit,
        )

        # 메트릭 기록
        _adaptive_mod._record_throttle_metrics(
            service=self._service_name,
            limit=new_limit,
            limit_change_direction="down",
            limit_change_trigger="error_budget_warning",
            error_budget_status="warning",
            error_budget_multiplier=self._error_budget_multiplier,
            error_budget_reduction_active=True,
        )

        # 감사 로깅
        _adaptive_mod._record_audit_safe(
            action="throttle_error_budget_warning",
            old_limit=previous_limit,
            new_limit=new_limit,
            error_budget_percent=budget_percent,
            multiplier=self._error_budget_multiplier,
            extra_data={"slo_name": slo_name},
        )

    def _handle_error_budget_critical(self, event) -> None:
        """
        Error Budget Critical 이벤트 처리.

        Critical 단계 (<10%): Limit 50% 감소.
        non_essential 티어 요청 거부.
        """
        import uuid

        event_data = event.data if hasattr(event, "data") else event

        budget_percent = event_data.get("budget_percent", 100.0)
        slo_name = event_data.get("slo_name", "availability")

        # SLO 필터링
        if not self._should_react_to_slo(slo_name):
            logger.debug(
                "adaptive_throttle.ignoring_critical_slo_target",
                slo_name=slo_name,
                _self=self._target_slo_patterns,
            )
            return

        # 위반 ID 생성 (추적용)
        violation_id = f"eb-violation-{slo_name}-{uuid.uuid4().hex[:8]}"

        # Critical 상태 진입 시 limit 저장 (Warning이 선행하지 않은 경우)
        if not self._error_budget_limit_reduction_active:
            self._limit_before_error_budget_reduction = self._current_limit

        self._error_budget_limit_reduction_active = True
        self._error_budget_multiplier = 0.5  # 50% 감소

        previous_limit = self._current_limit
        new_limit = max(
            int(self._limit_before_error_budget_reduction * self._error_budget_multiplier),
            self.config.min_limit,
        )

        self.current_limit = new_limit

        logger.error(
            "adaptive_throttle.error_budget_critical_limit",
            budget_percent=budget_percent,
            previous_limit=previous_limit,
            new_limit=new_limit,
            violation_id=violation_id,
        )

        # 메트릭 기록
        _adaptive_mod._record_throttle_metrics(
            service=self._service_name,
            limit=new_limit,
            limit_change_direction="down",
            limit_change_trigger="error_budget_critical",
            error_budget_status="critical",
            error_budget_multiplier=self._error_budget_multiplier,
            error_budget_reduction_active=True,
        )

        # EventBus 발행
        _adaptive_mod._emit_throttle_event(
            "THROTTLE_SLA_WARNING",
            {
                "trigger": "error_budget_critical",
                "budget_percent": budget_percent,
                "current_limit": new_limit,
                "previous_limit": previous_limit,
                "violation_id": violation_id,
            },
            priority_name="CRITICAL",
        )

        # 감사 로깅 (violation_id를 correlation_id로 사용)
        _adaptive_mod._record_audit_safe(
            action="throttle_error_budget_critical",
            old_limit=previous_limit,
            new_limit=new_limit,
            error_budget_percent=budget_percent,
            multiplier=self._error_budget_multiplier,
            correlation_id=violation_id,
            extra_data={
                "slo_name": slo_name,
                "violation_id": violation_id,
            },
        )

    def _handle_error_budget_recovered(self, event) -> None:
        """
        Error Budget 회복 이벤트 처리.

        Recovery Dampening으로 점진적 복구 (80% → 90% → 100%).
        Thundering Herd 방지를 위한 Jitter 적용.
        """
        if not self._error_budget_limit_reduction_active:
            return

        event_data = event.data if hasattr(event, "data") else event
        budget_percent = event_data.get("budget_percent", 100.0)
        slo_name = event_data.get("slo_name", "availability")

        # SLO 필터링
        if not self._should_react_to_slo(slo_name):
            logger.debug(
                "adaptive_throttle.ignoring_recovery_slo_target",
                slo_name=slo_name,
                _self=self._target_slo_patterns,
            )
            return

        logger.info(
            "adaptive_throttle.error_budget_recovered_starting",
            budget_percent=budget_percent,
        )

        previous_limit = self._current_limit

        # Recovery Dampening 시작
        self._error_budget_limit_reduction_active = False
        self._error_budget_multiplier = 1.0
        self._base_limit_before_emergency = self._limit_before_error_budget_reduction
        self.start_recovery_dampening(apply_jitter=True)

        # 메트릭 기록
        _adaptive_mod._record_throttle_metrics(
            service=self._service_name,
            error_budget_status="recovered",
            error_budget_multiplier=1.0,
            error_budget_reduction_active=False,
        )

        # 감사 로깅
        _adaptive_mod._record_audit_safe(
            action="throttle_error_budget_recovered",
            old_limit=previous_limit,
            new_limit=self._current_limit,
            error_budget_percent=budget_percent,
            extra_data={"slo_name": slo_name, "recovery_mode": "dampening"},
        )

    def _check_preemptive_protection(self) -> None:
        """
        Burn Rate 기반 선제적 보호 체크.

        예산 소진 예측기(BudgetDepletionForecaster)를 사용하여
        예산이 빠르게 소진될 것으로 예측되면 선제적으로 limit을 감소합니다.
        """
        # 이미 Error Budget 감소 상태면 추가 조치 불필요
        if self._error_budget_limit_reduction_active:
            return

        try:
            from selfhealing.services.error_budget.forecaster import (
                BudgetDepletionForecaster,
            )
            from selfhealing.services.error_budget_service import (
                get_error_budget_service,
            )

            service = get_error_budget_service()
            status = service.get_budget_status()

            forecaster = BudgetDepletionForecaster()

            if forecaster.should_preemptive_throttle(status):
                forecast = forecaster.forecast(status)

                logger.warning(
                    "adaptive_throttle.preemptive_throttle_triggered",
                    forecast=forecast.risk_level,
                    estimated_depletion_hours=forecast.estimated_depletion_hours,
                    burn_rate_1h=forecast.burn_rate_1h,
                )

                self._apply_preemptive_reduction(forecast)

        except ImportError:
            logger.debug("adaptive_throttle.forecaster_errorbudgetservice_available")
        except Exception as e:
            logger.debug(
                "adaptive_throttle.preemptive_check_skipped",
                error=e,
            )

    def _apply_preemptive_reduction(self, forecast) -> None:
        """
        선제적 limit 감소 적용.

        Args:
            forecast: BudgetDepletionForecaster.forecast() 결과
        """
        self._limit_before_error_budget_reduction = self._current_limit
        self._error_budget_limit_reduction_active = True

        # 위험 수준에 따른 배율 결정
        if forecast.risk_level == "critical":
            self._error_budget_multiplier = 0.5  # 50% 감소
        else:
            self._error_budget_multiplier = 0.8  # 80% 감소 (기본 선제)

        previous_limit = self._current_limit
        new_limit = max(
            int(self._limit_before_error_budget_reduction * self._error_budget_multiplier),
            self.config.min_limit,
        )

        self.current_limit = new_limit

        logger.warning(
            "adaptive_throttle.preemptive_limit_reduction_applied",
            previous_limit=previous_limit,
            new_limit=new_limit,
            _self=self._error_budget_multiplier,
        )

        # 메트릭 기록
        _adaptive_mod._record_throttle_metrics(
            service=self._service_name,
            limit=new_limit,
            limit_change_direction="down",
            limit_change_trigger="preemptive_protection",
            error_budget_status=forecast.risk_level,
            error_budget_multiplier=self._error_budget_multiplier,
            error_budget_reduction_active=True,
        )

        # 감사 로깅
        _adaptive_mod._record_audit_safe(
            action="throttle_preemptive_reduction",
            old_limit=previous_limit,
            new_limit=new_limit,
            extra_data={
                "risk_level": forecast.risk_level,
                "estimated_depletion_hours": forecast.estimated_depletion_hours,
                "burn_rate_1h": forecast.burn_rate_1h,
                "burn_rate_6h": forecast.burn_rate_6h,
                "is_accelerating": forecast.is_accelerating,
            },
        )

    def _check_error_budget_block(
        self,
        tier_id: str,
        store_rejection: bool,
        context: dict | None,
    ) -> ThrottleResult | None:
        """Error Budget Critical 상태에서 non_essential 티어 거부 판정."""
        if not self._error_budget_limit_reduction_active:
            return None
        if tier_id != "non_essential" or self._error_budget_multiplier > 0.5:
            return None

        trace_id = _adaptive_mod._get_trace_id_safe()
        _adaptive_mod._record_throttle_metrics(
            service=self._service_name,
            request_result="denied",
            denied_reason="error_budget_critical_non_essential_blocked",
            trace_id=trace_id,
        )

        result = ThrottleResult(
            allowed=False,
            current_count=0,
            limit=self._current_limit,
            remaining=0,
            reset_at=0,
            reason="error_budget_critical_non_essential_blocked",
        )

        if store_rejection and context:
            self._auto_store_rejection_to_dlq(context, result.reason)

        return result
