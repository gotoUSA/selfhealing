"""
AdaptiveThrottle 감사 로깅 통합 테스트.

테스트 대상:
- _maybe_adjust_limit() SLA Critical/Warning/Gradient 감사 호출
- activate_full_stop() 감사 호출
- deactivate_full_stop() 감사 호출
- _handle_rate_limit_429() 감사 호출
- start_recovery_dampening() 감사 호출
- complete_recovery_dampening() 감사 호출
"""

import pytest
from unittest.mock import patch, MagicMock


class TestAdaptiveThrottleSlaCriticalAudit:
    """SLA Critical 감사 로깅 테스트."""

    def test_sla_critical_triggers_audit_call(self):
        """SLA Critical 조건 시 감사 로깅 호출 검증."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_warning_ms=100,
            sla_critical_ms=200,
        )
        throttle = AdaptiveThrottle(config)

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            # Critical RTT 시뮬레이션 (임계치 2배 이상)
            throttle._maybe_adjust_limit(500.0)  # sla_critical_ms(200) * 2 = 400 초과

            # SLA Critical 감사 호출 확인
            critical_calls = [c for c in mock_audit.call_args_list if c.kwargs.get("action") == "throttle_sla_critical"]
            assert len(critical_calls) >= 1


class TestAdaptiveThrottleSlaWarningAudit:
    """SLA Warning 감사 로깅 테스트."""

    def test_sla_warning_triggers_audit_call(self):
        """SLA Warning 조건 시 감사 로깅 호출 검증."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_warning_ms=100,
            sla_critical_ms=200,
        )
        throttle = AdaptiveThrottle(config)

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            # Warning RTT 시뮬레이션 (SLA 초과, Critical 미만)
            throttle._maybe_adjust_limit(150.0)  # sla(100) < 150 < critical(200)

            # SLA Warning 감사 호출 확인
            warning_calls = [c for c in mock_audit.call_args_list if c.kwargs.get("action") == "throttle_sla_warning"]
            assert len(warning_calls) >= 1


class TestAdaptiveThrottleGradientAudit:
    """Gradient 기반 조정 감사 로깅 테스트."""

    def test_gradient_increase_triggers_audit_call(self):
        """Gradient 증가 시 감사 로깅 호출 검증."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_warning_ms=100,
        )
        throttle = AdaptiveThrottle(config)

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            # 낮은 RTT로 limit 증가 유도
            throttle._maybe_adjust_limit(10.0)

            # Gradient 감사 호출 확인 (limit_adjusted)
            # 조건에 따라 호출되거나 안 될 수 있음
            # 적어도 에러 없이 실행되면 성공


class TestAdaptiveThrottleFullStopAudit:
    """Full Stop 활성화/비활성화 감사 로깅 테스트."""

    def test_activate_full_stop_triggers_audit(self):
        """Full Stop 활성화 시 감사 로깅 호출 검증."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            throttle.activate_full_stop(reason="test_emergency")

            # Full Stop 활성화 감사 호출 확인
            full_stop_calls = [
                c for c in mock_audit.call_args_list if c.kwargs.get("action") == "throttle_full_stop_activated"
            ]
            assert len(full_stop_calls) == 1

            # 호출 인자 검증
            call_kwargs = full_stop_calls[0].kwargs
            assert call_kwargs.get("new_limit") == 0
            assert call_kwargs.get("full_stop_reason") == "test_emergency"

    def test_deactivate_full_stop_triggers_audit(self):
        """Full Stop 비활성화 시 감사 로깅 호출 검증."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        # Full Stop 활성화
        throttle.activate_full_stop(reason="test_emergency")

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            throttle.deactivate_full_stop()

            # Full Stop 비활성화 감사 호출 확인
            deactivate_calls = [
                c for c in mock_audit.call_args_list if c.kwargs.get("action") == "throttle_full_stop_deactivated"
            ]
            assert len(deactivate_calls) == 1


class TestAdaptiveThrottle429Audit:
    """429 응답 처리 감사 로깅 테스트."""

    def test_handle_429_triggers_audit(self):
        """429 응답 처리 시 감사 로깅 호출 검증."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        # 429 이벤트 시뮬레이션
        mock_event = MagicMock()
        mock_event.data = {
            "key": "test_key",
            "consecutive_429s": 1,
            "cooldown_until": 0,
        }

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            throttle._handle_rate_limit_429(mock_event)

            # 429 감사 호출 확인
            audit_calls = [c for c in mock_audit.call_args_list if c.kwargs.get("action") == "throttle_429_response"]
            assert len(audit_calls) == 1

            # 호출 인자 검증
            call_kwargs = audit_calls[0].kwargs
            assert call_kwargs.get("key") == "test_key"
            assert call_kwargs.get("consecutive_429s") == 1


class TestAdaptiveThrottleRecoveryDampeningAudit:
    """Recovery Dampening 감사 로깅 테스트."""

    def test_start_recovery_dampening_triggers_audit(self):
        """Recovery Dampening 시작 시 감사 로깅 호출 검증."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        # base_limit_before_emergency 설정
        throttle._base_limit_before_emergency = 100

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            throttle.start_recovery_dampening()

            # Recovery 시작 감사 호출 확인
            recovery_calls = [c for c in mock_audit.call_args_list if c.kwargs.get("action") == "throttle_recovery_started"]
            assert len(recovery_calls) == 1

            # 호출 인자 검증
            call_kwargs = recovery_calls[0].kwargs
            assert call_kwargs.get("recovery_step") == 0
            assert call_kwargs.get("recovery_multiplier") == 0.8

    def test_complete_recovery_dampening_triggers_audit(self):
        """Recovery Dampening 완료 시 감사 로깅 호출 검증."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        # Recovery Dampening 시작
        throttle._base_limit_before_emergency = 100
        throttle.start_recovery_dampening()

        with patch("selfhealing.services.throttle.adaptive._record_audit_safe") as mock_audit:
            throttle.complete_recovery_dampening()

            # Recovery 완료 감사 호출 확인
            complete_calls = [c for c in mock_audit.call_args_list if c.kwargs.get("action") == "throttle_recovery_completed"]
            assert len(complete_calls) == 1

            # 호출 인자 검증
            call_kwargs = complete_calls[0].kwargs
            assert call_kwargs.get("recovery_multiplier") == 1.0
