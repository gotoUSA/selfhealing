"""
GovernanceEventMixin for AdaptiveThrottle.

이 모듈은 selfhealing.services.throttle.adaptive 패키지의 내부 구현입니다.
"""

import selfhealing.services.throttle.adaptive as _adaptive_mod
import structlog
import time
logger = structlog.get_logger()







class GovernanceEventMixin:
    """AdaptiveThrottle GovernanceEventMixin."""

    # =========================================================================
    # Kill Switch EventBus 연동 (Governance 통합)
    # =========================================================================

    def _subscribe_kill_switch_events(self) -> None:
        """Kill Switch 이벤트 구독 (Fail-Open)."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(EventType.KILL_SWITCH_ACTIVATED, self._handle_kill_switch_activated)
            bus.subscribe(EventType.KILL_SWITCH_DEACTIVATED, self._handle_kill_switch_deactivated)
            logger.info("adaptive_throttle.subscribed_kill_switch_events")
        except ImportError:
            logger.debug("adaptive_throttle.eventbus_available_kill_switch")
        except Exception as e:
            logger.warning(
                "adaptive_throttle.failed_subscribe_kill_switch",
                error=e,
            )

    def _handle_kill_switch_activated(self, event) -> None:
        """Kill Switch 활성화 → Gradient Freeze + limit 유지 (즉시)."""
        self._kill_switch_active = True
        self._gradient_frozen = True
        logger.warning("adaptive_throttle.kill_switch_activated_gradient")

        _adaptive_mod._record_audit_safe(
            action="throttle_kill_switch_activated",
            old_limit=self._current_limit,
            new_limit=self._current_limit,
            trigger_source="kill_switch",
        )

    def _handle_kill_switch_deactivated(self, event) -> None:
        """Kill Switch 비활성화 → 조건부 Gradient 재개."""
        self._kill_switch_active = False

        # LEVEL_3 Emergency가 활성화되어 있으면 frozen 유지
        if self._emergency_level < 3:
            self._gradient_frozen = False

        self.start_recovery_dampening()
        logger.info("adaptive_throttle.kill_switch_deactivated_recovery")

        _adaptive_mod._record_audit_safe(
            action="throttle_kill_switch_deactivated",
            old_limit=self._current_limit,
            new_limit=self._current_limit,
            trigger_source="kill_switch",
        )

    # =========================================================================
    # Break Glass 상태 동기화 (Governance Settings 기반)
    # =========================================================================

    def _sync_break_glass_state(self) -> None:
        """Break Glass 상태를 로컬 플래그로 동기화 (Fail-Open)."""
        try:
            from selfhealing.settings.governance import get_governance_settings

            self._break_glass_active = get_governance_settings().break_glass_enabled
        except ImportError:
            logger.debug("adaptive_throttle.governance_settings_available")
        except Exception as e:
            logger.debug(
                "adaptive_throttle.break_glass_sync_failed",
                error=e,
            )

    # =========================================================================
    # Load Shedding EventBus 연동
    # =========================================================================

    def _subscribe_load_shedding_events(self) -> None:
        """Load Shedding 이벤트 구독 등록 (Fail-Open)."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(
                EventType.LOAD_SHEDDING_LEVEL_CHANGED,
                self._handle_shedding_changed,
            )
            logger.info("adaptive_throttle.subscribed_load_shedding_events")
        except ImportError:
            logger.debug("adaptive_throttle.eventbus_available_load_shedding")
        except Exception as e:
            logger.warning(
                "adaptive_throttle.failed_subscribe_load_shedding",
                error=e,
            )

    def _handle_shedding_changed(self, event) -> None:
        """Load Shedding 상태 변경 이벤트 처리 — 최소 연산 보장."""
        event_data = event.data if hasattr(event, "data") else event
        new_level = event_data.get("new_level", -1)
        traffic_limit = event_data.get("traffic_limit", 100.0)
        affected = event_data.get("affected_services", [])

        if new_level < 0:
            # Shedding 해제
            self._shedding_affected_services = set()
            self._shedding_suggested_limit = self.config.max_limit

            # 다른 제한이 활성화 상태가 아닐 때만 Dampening 시작
            if not self._emergency_mode_active and not self._429_reduction_active:
                self.start_recovery_dampening(apply_jitter=True)

            logger.info(
                "[AdaptiveThrottle] Load Shedding deactivated, "
                f"shedding_suggested_limit restored to {self.config.max_limit}"
            )
        else:
            # Shedding 활성화: 보상 계수 적용하여 이중 차단 완화
            self._shedding_affected_services = set(affected)
            raw_limit = int(self.config.max_limit * (traffic_limit / 100.0))
            compensated = min(
                self.config.max_limit,
                int(raw_limit * self.config.shedding_compensation_factor),
            )
            self._shedding_suggested_limit = max(compensated, self.config.min_limit)

            logger.warning(
                f"[AdaptiveThrottle] Load Shedding level={new_level}, "
                f"traffic_limit={traffic_limit}%, "
                f"shedding_suggested_limit={self._shedding_suggested_limit}, "
                f"affected_services={affected}"
            )

        # 메트릭 기록
        _adaptive_mod._record_throttle_metrics(
            service=self._service_name,
            limit=self._shedding_suggested_limit,
            limit_change_trigger="load_shedding",
        )

    def _sync_governance_state(self) -> bool:
        """
        Governance 통합 상태 동기화 (Check on Use, 30초 TTL).

        Emergency Level, Kill Switch, Break Glass 상태를 일관되게 동기화.
        EventBus 이벤트 유실 시 Drift 교정 역할.

        Returns:
            True if emergency state drift detected and synced, False otherwise
        """
        now = time.time()

        # TTL 확인
        if now - self._last_emergency_check_time < self._emergency_cache_ttl_seconds:
            return False

        self._last_emergency_check_time = now

        # Kill Switch 상태 동기화 (EventBus 이벤트 유실 대비 Drift 교정)
        self._sync_kill_switch_state()

        # Break Glass 상태 동기화 (Settings 기반)
        self._sync_break_glass_state()

        # Emergency Level 동기화
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager

            manager = get_emergency_manager()
            current_level = manager.get_current_level().value

            # Drift 감지: 캐시된 레벨과 실제 레벨이 다른 경우
            if current_level != self._emergency_level:
                logger.warning(
                    f"[AdaptiveThrottle] Emergency state drift detected: "
                    f"cached={self._emergency_level}, actual={current_level}"
                )
                self.adjust_for_emergency(current_level)
                return True

            # Full Stop 조건 재확인
            if self._emergency_level >= 3:
                is_full_stop, reason = self.check_full_stop_conditions()
                if is_full_stop and not self._full_stop_active:
                    self.activate_full_stop(reason)
                elif not is_full_stop and self._full_stop_active:
                    self.deactivate_full_stop()

            # Burn Rate 기반 선제적 보호 체크
            self._check_preemptive_protection()

            return False

        except ImportError:
            return False
        except Exception as e:
            logger.warning(
                "adaptive_throttle.governance_state_sync_failed",
                error=e,
            )
            return False

    def _sync_kill_switch_state(self) -> None:
        """Kill Switch 상태를 Governance 체크로 동기화 (Drift 교정, Fail-Open)."""
        try:
            from selfhealing.services.governance.checks import is_system_enabled

            system_enabled = is_system_enabled()

            if not system_enabled and not self._kill_switch_active:
                # Kill Switch 활성화 Drift 교정 (EventBus 이벤트 유실 대비)
                self._kill_switch_active = True
                self._gradient_frozen = True
                logger.warning("adaptive_throttle.kill_switch_drift_detected")
            elif system_enabled and self._kill_switch_active:
                # Kill Switch 비활성화 Drift 교정 (EventBus 이벤트 유실 대비)
                self._kill_switch_active = False
                if self._emergency_level < 3:
                    self._gradient_frozen = False
                self.start_recovery_dampening()
                logger.info("adaptive_throttle.kill_switch_drift_corrected")
        except ImportError:
            logger.debug("adaptive_throttle.governance_checks_available_kill")
        except Exception as e:
            logger.debug(
                "adaptive_throttle.kill_switch_sync_failed",
                error=e,
            )

    def check_and_sync_emergency_state(self) -> bool:
        """하위호환 래퍼: _sync_governance_state()로 위임."""
        return self._sync_governance_state()

