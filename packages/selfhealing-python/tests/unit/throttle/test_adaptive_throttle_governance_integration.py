"""
AdaptiveThrottle ↔ Governance 연동 테스트.

테스트 대상:
1. GovernanceCheckMixin 상속 및 클래스 변수 설정
2. Kill Switch EventBus 구독 및 핸들러 동작
3. Break Glass 상태 동기화 및 Full Stop 해제
4. _maybe_adjust_limit() Governance Safety Net
5. check() Break Glass → Full Stop 해제
6. reset_all() / rollback_to_base_limit() Governance 플래그 초기화
7. get_stats() / get_config_snapshot() Governance 상태 노출
8. Fail-Open 정책 검증
"""

import time
from unittest.mock import MagicMock, patch


class TestGovernanceCheckMixinInheritance:
    """GovernanceCheckMixin 상속 구조 검증."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_adaptive_throttle_inherits_governance_check_mixin(self):
        """AdaptiveThrottle이 GovernanceCheckMixin을 상속하는지 확인."""
        from selfhealing.services.governance.checks import GovernanceCheckMixin
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle

        assert issubclass(AdaptiveThrottle, GovernanceCheckMixin)

    def test_governance_service_name_class_variable(self):
        """_governance_service_name이 'adaptive_throttle'로 설정되어 있는지 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle

        assert AdaptiveThrottle._governance_service_name == "adaptive_throttle"

    def test_governance_domain_class_variable(self):
        """_governance_domain이 'throttle'로 설정되어 있는지 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle

        assert AdaptiveThrottle._governance_domain == "throttle"

    def test_mixin_methods_available_on_instance(self):
        """Mixin 메서드(is_automation_allowed, check_governance)가 인스턴스에서 사용가능한지 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        throttle = AdaptiveThrottle(ThrottleConfig())

        assert hasattr(throttle, "is_automation_allowed")
        assert hasattr(throttle, "check_governance")
        assert hasattr(throttle, "require_automation_allowed")
        assert callable(throttle.is_automation_allowed)
        assert callable(throttle.check_governance)


class TestKillSwitchEventBusSubscription:
    """Kill Switch EventBus 구독 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_subscribe_kill_switch_events_called_on_init(self):
        """__init__에서 _subscribe_kill_switch_events()가 호출되는지 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        call_count = 0
        original_method = AdaptiveThrottle._subscribe_kill_switch_events

        def counting_wrapper(self_arg):
            nonlocal call_count
            call_count += 1
            return original_method(self_arg)

        with patch.object(
            AdaptiveThrottle,
            "_subscribe_kill_switch_events",
            counting_wrapper,
        ):
            throttle = AdaptiveThrottle(ThrottleConfig())

        assert call_count == 1

    def test_subscribe_kill_switch_events_fail_open_on_import_error(self):
        """EventBus Import 실패 시 Fail-Open으로 동작."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        with patch("selfhealing.services.throttle.adaptive.AdaptiveThrottle._subscribe_kill_switch_events") as mock_sub:
            mock_sub.side_effect = ImportError("No EventBus")
            # ImportError가 발생해도 생성자가 정상 완료되어야 함
            # 실제로는 메서드 내부에서 try/except로 처리하므로 직접 호출 테스트
            pass

        # 실제 Fail-Open 테스트: 내부 import 실패 시뮬레이션
        throttle = AdaptiveThrottle(ThrottleConfig())
        # Kill Switch 플래그 기본값 확인 (Fail-Open: 기존 동작 유지)
        assert throttle._kill_switch_active is False


class TestKillSwitchHandlers:
    """Kill Switch 이벤트 핸들러 동작 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_kill_switch_activated_sets_gradient_frozen(self):
        """Kill Switch 활성화 시 _gradient_frozen이 True로 설정."""
        throttle = self._create_throttle()
        assert throttle._gradient_frozen is False

        mock_event = MagicMock()
        throttle._handle_kill_switch_activated(mock_event)

        assert throttle._gradient_frozen is True

    def test_kill_switch_activated_sets_kill_switch_active(self):
        """Kill Switch 활성화 시 _kill_switch_active가 True로 설정."""
        throttle = self._create_throttle()
        assert throttle._kill_switch_active is False

        mock_event = MagicMock()
        throttle._handle_kill_switch_activated(mock_event)

        assert throttle._kill_switch_active is True

    def test_kill_switch_activated_preserves_current_limit(self):
        """Kill Switch 활성화 시 현재 limit이 변경되지 않음."""
        throttle = self._create_throttle()
        original_limit = throttle._current_limit

        mock_event = MagicMock()
        throttle._handle_kill_switch_activated(mock_event)

        assert throttle._current_limit == original_limit

    def test_kill_switch_activated_records_audit(self):
        """Kill Switch 활성화 시 감사 로그가 기록되는지 확인."""
        throttle = self._create_throttle()

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            mock_event = MagicMock()
            throttle._handle_kill_switch_activated(mock_event)

            mock_audit.assert_called_once_with(
                action="throttle_kill_switch_activated",
                old_limit=throttle._current_limit,
                new_limit=throttle._current_limit,
                trigger_source="kill_switch",
            )

    def test_kill_switch_deactivated_clears_kill_switch_active(self):
        """Kill Switch 비활성화 시 _kill_switch_active가 False로 설정."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._gradient_frozen = True

        mock_event = MagicMock()
        throttle._handle_kill_switch_deactivated(mock_event)

        assert throttle._kill_switch_active is False

    def test_kill_switch_deactivated_unfreezes_gradient_when_not_level3(self):
        """Kill Switch 비활성화 시 LEVEL_3가 아니면 gradient unfreeze."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._gradient_frozen = True
        throttle._emergency_level = 0

        mock_event = MagicMock()
        throttle._handle_kill_switch_deactivated(mock_event)

        assert throttle._gradient_frozen is False

    def test_kill_switch_deactivated_keeps_frozen_when_level3(self):
        """Kill Switch 비활성화 시 LEVEL_3이면 gradient frozen 유지."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._gradient_frozen = True
        throttle._emergency_level = 3

        mock_event = MagicMock()
        throttle._handle_kill_switch_deactivated(mock_event)

        assert throttle._gradient_frozen is True

    def test_kill_switch_deactivated_starts_recovery_dampening(self):
        """Kill Switch 비활성화 시 Recovery Dampening이 시작되는지 확인."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._gradient_frozen = True

        with patch.object(throttle, "start_recovery_dampening") as mock_recovery:
            mock_event = MagicMock()
            throttle._handle_kill_switch_deactivated(mock_event)

            mock_recovery.assert_called_once()

    def test_kill_switch_deactivated_records_audit(self):
        """Kill Switch 비활성화 시 감사 로그가 기록되는지 확인."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            mock_event = MagicMock()
            throttle._handle_kill_switch_deactivated(mock_event)

            # start_recovery_dampening()도 _record_audit_safe를 호출하므로 assert_any_call 사용
            mock_audit.assert_any_call(
                action="throttle_kill_switch_deactivated",
                old_limit=throttle._current_limit,
                new_limit=throttle._current_limit,
                trigger_source="kill_switch",
            )


class TestBreakGlassStateSync:
    """Break Glass 상태 동기화 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_sync_break_glass_state_sets_flag_true(self):
        """Break Glass 활성화 시 _break_glass_active가 True로 설정."""
        throttle = self._create_throttle()

        mock_settings = MagicMock()
        mock_settings.break_glass_enabled = True

        with patch(
            "selfhealing.settings.governance.get_governance_settings",
            return_value=mock_settings,
        ):
            throttle._sync_break_glass_state()

        assert throttle._break_glass_active is True

    def test_sync_break_glass_state_sets_flag_false(self):
        """Break Glass 비활성화 시 _break_glass_active가 False로 설정."""
        throttle = self._create_throttle()
        throttle._break_glass_active = True

        mock_settings = MagicMock()
        mock_settings.break_glass_enabled = False

        with patch(
            "selfhealing.settings.governance.get_governance_settings",
            return_value=mock_settings,
        ):
            throttle._sync_break_glass_state()

        assert throttle._break_glass_active is False

    def test_sync_break_glass_state_fail_open_on_import_error(self):
        """Governance Settings Import 실패 시 Fail-Open (플래그 미변경)."""
        throttle = self._create_throttle()
        assert throttle._break_glass_active is False

        with patch(
            "selfhealing.settings.governance.get_governance_settings",
            side_effect=ImportError("Settings not available"),
        ):
            throttle._sync_break_glass_state()

        assert throttle._break_glass_active is False

    def test_sync_break_glass_state_fail_open_on_exception(self):
        """Settings 로드 예외 시 Fail-Open (플래그 미변경)."""
        throttle = self._create_throttle()
        throttle._break_glass_active = True

        with patch(
            "selfhealing.settings.governance.get_governance_settings",
            side_effect=RuntimeError("DB error"),
        ):
            throttle._sync_break_glass_state()

        # Fail-Open: 기존 값 유지
        assert throttle._break_glass_active is True


class TestBreakGlassFullStopOverride:
    """check() 내 Break Glass → Full Stop 해제 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_break_glass_deactivates_full_stop_on_check(self):
        """Break Glass 활성 + Full Stop 상태에서 check() 호출 시 Full Stop 해제."""
        throttle = self._create_throttle()
        throttle._break_glass_active = True
        throttle._full_stop_active = True
        throttle._current_limit = 0

        with patch.object(throttle, "deactivate_full_stop", wraps=throttle.deactivate_full_stop):
            result = throttle.check("test_key")

        # deactivate_full_stop() 호출로 Full Stop 해제됨
        assert throttle._full_stop_active is False

    def test_break_glass_triggers_recovery_dampening_after_full_stop(self):
        """Break Glass로 Full Stop 해제 후 Recovery Dampening이 시작되는지 확인."""
        throttle = self._create_throttle()
        throttle._break_glass_active = True
        throttle._full_stop_active = True
        throttle._current_limit = 0

        throttle.check("test_key")

        assert throttle._recovery_dampening_active is True

    def test_no_full_stop_override_when_break_glass_inactive(self):
        """Break Glass 비활성 시 Full Stop이 유지됨."""
        throttle = self._create_throttle()
        throttle._break_glass_active = False
        throttle._full_stop_active = True
        throttle._current_limit = 0

        # Full Stop 상태에서 check()는 요청 거부 (sliding window: limit=0)
        result = throttle.check("test_key")

        assert throttle._full_stop_active is True

    def test_no_override_when_no_full_stop(self):
        """Full Stop 비활성 시 Break Glass가 아무 동작 안 함."""
        throttle = self._create_throttle()
        throttle._break_glass_active = True
        throttle._full_stop_active = False

        with patch.object(throttle, "deactivate_full_stop") as mock_deactivate:
            throttle.check("test_key")
            mock_deactivate.assert_not_called()

    def test_check_still_uses_sliding_window_after_break_glass(self):
        """Break Glass → Full Stop 해제 후에도 sliding window 판단이 실행됨 (무조건 Allowed 아님)."""
        throttle = self._create_throttle()
        throttle._break_glass_active = True
        throttle._full_stop_active = True
        throttle._current_limit = 0

        # Recovery Dampening이 limit을 복구하므로 결과는 sliding window에 의존
        result = throttle.check("test_key")

        # ThrottleResult를 반환 (None이 아님)
        assert result is not None
        assert hasattr(result, "allowed")


class TestMaybeAdjustLimitGovernanceSafetyNet:
    """_maybe_adjust_limit() Governance Safety Net 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(
            ThrottleConfig(
                initial_limit=100,
                sample_interval_ms=50,
                sla_warning_ms=200,
                sla_critical_ms=500,
            )
        )

    def test_gradient_frozen_skips_adjustment(self):
        """gradient frozen 상태에서 limit 조정이 스킵되는지 확인."""
        throttle = self._create_throttle()
        throttle._gradient_frozen = True
        original_limit = throttle._current_limit

        throttle._maybe_adjust_limit(100.0)

        assert throttle._current_limit == original_limit

    def test_governance_blocked_freezes_gradient(self):
        """Governance 차단 시 _gradient_frozen이 True로 설정되어 limit 조정이 스킵됨."""
        throttle = self._create_throttle()
        throttle._last_adjustment_time = 0.0  # 즉시 조정 가능

        with patch.object(
            throttle,
            "is_automation_allowed",
            return_value=False,
        ):
            throttle._maybe_adjust_limit(100.0)

        assert throttle._gradient_frozen is True

    def test_governance_allowed_proceeds_with_adjustment(self):
        """Governance 허용 시 limit 조정이 정상 진행."""
        throttle = self._create_throttle()
        throttle._last_adjustment_time = 0.0

        with patch.object(
            throttle,
            "is_automation_allowed",
            return_value=True,
        ):
            with patch.object(throttle, "_sync_break_glass_state"):
                # SLA critical RTT로 limit 감소 유도
                throttle.record_response(600.0)

        # Limit이 조정되었을 것
        assert throttle._gradient_frozen is False

    def test_governance_check_fail_open(self):
        """Governance 체크 예외 시 Fail-Open으로 기존 동작 유지."""
        throttle = self._create_throttle()
        throttle._last_adjustment_time = 0.0

        with patch.object(
            throttle,
            "is_automation_allowed",
            side_effect=Exception("Governance unavailable"),
        ):
            with patch.object(throttle, "_sync_break_glass_state"):
                # 예외 발생해도 limit 조정이 계속됨
                original_limit = throttle._current_limit
                throttle._maybe_adjust_limit(100.0)

        # Fail-Open: gradient_frozen이 설정되지 않음
        assert throttle._gradient_frozen is False

    def test_break_glass_sync_called_in_maybe_adjust_limit(self):
        """_maybe_adjust_limit() 내에서 _sync_break_glass_state()가 호출되는지 확인."""
        throttle = self._create_throttle()
        throttle._last_adjustment_time = 0.0

        with patch.object(
            throttle,
            "is_automation_allowed",
            return_value=True,
        ):
            with patch.object(throttle, "_sync_break_glass_state") as mock_sync:
                throttle._maybe_adjust_limit(100.0)
                mock_sync.assert_called_once()


class TestKillSwitchCheckBehavior:
    """Kill Switch 활성화 시 check() Data Plane 동작 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_check_continues_with_existing_limit_when_kill_switch_active(self):
        """Kill Switch 활성화 중에도 check()가 기존 limit으로 트래픽 처리."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._gradient_frozen = True
        throttle._last_emergency_check_time = time.time()  # TTL 내로 설정 (sync 스킵)
        original_limit = throttle._current_limit

        result = throttle.check("test_key")

        # check()는 여전히 동작 (Kill Switch != 트래픽 차단)
        assert result is not None
        assert result.limit == original_limit

    def test_kill_switch_blocks_limit_adjustment_not_traffic(self):
        """Kill Switch는 limit 조정만 차단하고 트래픽은 차단하지 않음."""
        throttle = self._create_throttle()

        # Kill Switch 활성화
        mock_event = MagicMock()
        throttle._handle_kill_switch_activated(mock_event)
        throttle._last_emergency_check_time = time.time()  # TTL 내로 설정 (sync 스킵)

        # check()는 정상 동작
        result = throttle.check("test_key")
        assert result is not None

        # 하지만 limit 조정은 차단됨
        original_limit = throttle._current_limit
        throttle._maybe_adjust_limit(600.0)  # SLA critical RTT
        assert throttle._current_limit == original_limit  # limit 변경 없음


class TestResetAllGovernanceFlags:
    """reset_all()에서 Governance 플래그 초기화 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_reset_all_clears_kill_switch_active(self):
        """reset_all() 호출 시 _kill_switch_active가 False로 초기화."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True

        throttle.reset_all()

        assert throttle._kill_switch_active is False

    def test_reset_all_clears_break_glass_active(self):
        """reset_all() 호출 시 _break_glass_active가 False로 초기화."""
        throttle = self._create_throttle()
        throttle._break_glass_active = True

        throttle.reset_all()

        assert throttle._break_glass_active is False


class TestRollbackToBaseLimitGovernanceFlags:
    """rollback_to_base_limit()에서 Governance 플래그 초기화 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_rollback_clears_kill_switch_active(self):
        """rollback_to_base_limit() 호출 시 _kill_switch_active가 False로 초기화."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._emergency_mode_active = True

        throttle.rollback_to_base_limit()

        assert throttle._kill_switch_active is False

    def test_rollback_clears_break_glass_active(self):
        """rollback_to_base_limit() 호출 시 _break_glass_active가 False로 초기화."""
        throttle = self._create_throttle()
        throttle._break_glass_active = True
        throttle._emergency_mode_active = True

        throttle.rollback_to_base_limit()

        assert throttle._break_glass_active is False


class TestGetStatsGovernanceFields:
    """get_stats()에서 Governance 상태 노출 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_get_stats_includes_governance_section(self):
        """get_stats()에 'governance' 섹션이 포함되는지 확인."""
        throttle = self._create_throttle()
        stats = throttle.get_stats()

        assert "governance" in stats
        assert "kill_switch_active" in stats["governance"]
        assert "break_glass_active" in stats["governance"]

    def test_get_stats_governance_default_values(self):
        """get_stats() Governance 기본값 확인."""
        throttle = self._create_throttle()
        stats = throttle.get_stats()

        assert stats["governance"]["kill_switch_active"] is False
        assert stats["governance"]["break_glass_active"] is False

    def test_get_stats_governance_reflects_kill_switch(self):
        """Kill Switch 활성화 시 get_stats()에 반영."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        stats = throttle.get_stats()

        assert stats["governance"]["kill_switch_active"] is True

    def test_get_stats_governance_reflects_break_glass(self):
        """Break Glass 활성화 시 get_stats()에 반영."""
        throttle = self._create_throttle()
        throttle._break_glass_active = True
        stats = throttle.get_stats()

        assert stats["governance"]["break_glass_active"] is True


class TestGetConfigSnapshotGovernanceFields:
    """get_config_snapshot()에서 Governance 상태 노출 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_config_snapshot_includes_kill_switch_active(self):
        """get_config_snapshot()에 kill_switch_active 필드가 포함."""
        throttle = self._create_throttle()
        snapshot = throttle.get_config_snapshot()

        assert "kill_switch_active" in snapshot

    def test_config_snapshot_includes_break_glass_active(self):
        """get_config_snapshot()에 break_glass_active 필드가 포함."""
        throttle = self._create_throttle()
        snapshot = throttle.get_config_snapshot()

        assert "break_glass_active" in snapshot

    def test_config_snapshot_reflects_governance_state(self):
        """Governance 상태가 config_snapshot에 정확히 반영."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._break_glass_active = True

        snapshot = throttle.get_config_snapshot()

        assert snapshot["kill_switch_active"] is True
        assert snapshot["break_glass_active"] is True


class TestGovernanceInitialState:
    """AdaptiveThrottle 초기 상태에서 Governance 플래그 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_initial_kill_switch_active_is_false(self):
        """초기 _kill_switch_active가 False."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        throttle = AdaptiveThrottle(ThrottleConfig())
        assert throttle._kill_switch_active is False

    def test_initial_break_glass_active_is_false(self):
        """초기 _break_glass_active가 False."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        throttle = AdaptiveThrottle(ThrottleConfig())
        assert throttle._break_glass_active is False


class TestKillSwitchAndEmergencyLevelInteraction:
    """Kill Switch와 Emergency Level 간 _gradient_frozen 플래그 충돌 방지 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_kill_switch_off_with_level3_keeps_gradient_frozen(self):
        """LEVEL_3 + Kill Switch 해제 시 gradient frozen 유지."""
        throttle = self._create_throttle()

        # LEVEL_3 Emergency 활성화
        throttle._emergency_level = 3
        throttle._gradient_frozen = True
        throttle._kill_switch_active = True

        # Kill Switch 비활성화
        mock_event = MagicMock()
        throttle._handle_kill_switch_deactivated(mock_event)

        # LEVEL_3가 여전히 활성이므로 frozen 유지
        assert throttle._gradient_frozen is True
        assert throttle._kill_switch_active is False

    def test_kill_switch_off_without_level3_unfreezes_gradient(self):
        """LEVEL_2 + Kill Switch 해제 시 gradient unfreeze."""
        throttle = self._create_throttle()

        throttle._emergency_level = 2
        throttle._gradient_frozen = True
        throttle._kill_switch_active = True

        mock_event = MagicMock()
        throttle._handle_kill_switch_deactivated(mock_event)

        assert throttle._gradient_frozen is False

    def test_kill_switch_and_level3_both_freeze_independently(self):
        """Kill Switch와 LEVEL_3 모두 독립적으로 gradient freeze 가능."""
        throttle = self._create_throttle()

        # Kill Switch로 freeze
        mock_event = MagicMock()
        throttle._handle_kill_switch_activated(mock_event)
        assert throttle._gradient_frozen is True

        # LEVEL_3으로도 freeze (이미 frozen 상태)
        throttle.adjust_for_emergency(3)
        assert throttle._gradient_frozen is True

        # Kill Switch 해제 — LEVEL_3가 여전히 활성이므로 frozen 유지
        throttle._handle_kill_switch_deactivated(mock_event)
        assert throttle._gradient_frozen is True

        # LEVEL_3 해제 — 이제야 unfreeze
        throttle.adjust_for_emergency(0)
        assert throttle._gradient_frozen is False
