"""
Stop Conditions Tests for Chaos Engine Safety Mechanisms

Tests the Stop Conditions functionality that automatically stops experiments
when SLA metrics exceed thresholds (error rate, latency, error budget).

Phase 3: Chaos Safety Implementation Plan
Reference: docs/self_healing/CHAOS_SAFETY_IMPLEMENTATION_PLAN.md
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.chaos.stop_conditions import (
    StopConditionsConfig,
    StopConditionViolation,
    StopConditionCheckResult,
    StopConditionsChecker,
    get_stop_conditions_checker,
    reset_stop_conditions_checker,
)


# =============================================================================
# StopConditionsConfig Tests
# =============================================================================


class TestStopConditionsConfig:
    """StopConditionsConfig 데이터 클래스 테스트."""

    def test_default_values(self):
        """기본값이 올바르게 설정되는지 확인."""
        config = StopConditionsConfig()

        assert config.max_error_rate_percent == 5.0
        assert config.max_latency_p99_ms == 2000
        assert config.max_latency_p95_ms == 1000
        assert config.min_error_budget_percent == 10.0
        assert config.check_interval_seconds == 10
        assert config.consecutive_breaches_required == 2
        assert config.enabled is True

    def test_to_dict(self):
        """딕셔너리 변환 테스트."""
        config = StopConditionsConfig(
            max_error_rate_percent=3.0,
            max_latency_p99_ms=1500,
            consecutive_breaches_required=3,
        )

        result = config.to_dict()

        assert result["max_error_rate_percent"] == 3.0
        assert result["max_latency_p99_ms"] == 1500
        assert result["consecutive_breaches_required"] == 3

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트."""
        data = {
            "max_error_rate_percent": 10.0,
            "max_latency_p99_ms": 3000,
            "min_error_budget_percent": 15.0,
            "enabled": False,
        }

        config = StopConditionsConfig.from_dict(data)

        assert config.max_error_rate_percent == 10.0
        assert config.max_latency_p99_ms == 3000
        assert config.min_error_budget_percent == 15.0
        assert config.enabled is False

    def test_from_dict_with_defaults(self):
        """부분 데이터로 생성 시 기본값 적용 테스트."""
        data = {"max_error_rate_percent": 7.5}

        config = StopConditionsConfig.from_dict(data)

        assert config.max_error_rate_percent == 7.5
        assert config.max_latency_p99_ms == 2000  # 기본값
        assert config.enabled is True  # 기본값


# =============================================================================
# StopConditionViolation Tests
# =============================================================================


class TestStopConditionViolation:
    """StopConditionViolation 데이터 클래스 테스트."""

    def test_creation(self):
        """Violation 객체 생성 테스트."""
        violation = StopConditionViolation(
            condition_type="error_rate",
            current_value=7.5,
            threshold_value=5.0,
            message="Error rate 7.5% > 5.0%",
        )

        assert violation.condition_type == "error_rate"
        assert violation.current_value == 7.5
        assert violation.threshold_value == 5.0
        assert "7.5%" in violation.message

    def test_to_dict(self):
        """딕셔너리 변환 테스트."""
        violation = StopConditionViolation(
            condition_type="latency_p99",
            current_value=2500,
            threshold_value=2000,
            message="Latency P99 2500ms > 2000ms",
        )

        result = violation.to_dict()

        assert result["condition_type"] == "latency_p99"
        assert result["current_value"] == 2500
        assert result["threshold_value"] == 2000


# =============================================================================
# StopConditionCheckResult Tests
# =============================================================================


class TestStopConditionCheckResult:
    """StopConditionCheckResult 데이터 클래스 테스트."""

    def test_default_values(self):
        """기본값 테스트."""
        result = StopConditionCheckResult()

        assert result.should_stop is False
        assert result.violations == []
        assert result.consecutive_breach_count == 0

    def test_with_violations(self):
        """위반 사항이 있는 경우 테스트."""
        violation = StopConditionViolation(
            condition_type="error_rate",
            current_value=7.5,
            threshold_value=5.0,
            message="Error rate exceeded",
        )

        result = StopConditionCheckResult(
            should_stop=True,
            violations=[violation],
            consecutive_breach_count=2,
            checked_at="2025-12-21T10:00:00",
        )

        assert result.should_stop is True
        assert len(result.violations) == 1
        assert result.consecutive_breach_count == 2

    def test_to_dict(self):
        """딕셔너리 변환 테스트."""
        violation = StopConditionViolation(
            condition_type="error_budget",
            current_value=5.0,
            threshold_value=10.0,
            message="Error budget low",
        )

        result = StopConditionCheckResult(
            should_stop=True,
            violations=[violation],
            consecutive_breach_count=3,
            metrics_snapshot={"error_rate": 7.5},
        )

        result_dict = result.to_dict()

        assert result_dict["should_stop"] is True
        assert len(result_dict["violations"]) == 1
        assert result_dict["consecutive_breach_count"] == 3


# =============================================================================
# StopConditionsChecker Tests
# =============================================================================


class TestStopConditionsChecker:
    """StopConditionsChecker 클래스 테스트."""

    def setup_method(self):
        """각 테스트 전에 싱글톤 리셋."""
        reset_stop_conditions_checker()

    def test_default_config(self):
        """기본 설정으로 생성 테스트."""
        checker = StopConditionsChecker()

        assert checker.config.max_error_rate_percent == 5.0
        assert checker.config.enabled is True

    def test_custom_config(self):
        """커스텀 설정으로 생성 테스트."""
        config = StopConditionsConfig(
            max_error_rate_percent=10.0,
            max_latency_p99_ms=3000,
        )
        checker = StopConditionsChecker(config=config)

        assert checker.config.max_error_rate_percent == 10.0
        assert checker.config.max_latency_p99_ms == 3000

    def test_update_config(self):
        """설정 업데이트 테스트."""
        checker = StopConditionsChecker()

        updated = checker.update_config(
            max_error_rate_percent=8.0,
            consecutive_breaches_required=3,
        )

        assert updated.max_error_rate_percent == 8.0
        assert updated.consecutive_breaches_required == 3

    @patch.object(StopConditionsChecker, "_collect_metrics")
    def test_check_no_violations(self, mock_collect):
        """위반 없는 경우 체크 테스트."""
        mock_collect.return_value = {
            "error_rate_percent": 1.0,
            "latency_p99_ms": 500,
            "latency_p95_ms": 300,
            "error_budget_remaining_percent": 80.0,
        }

        checker = StopConditionsChecker()
        result = checker.check(
            experiment_id="test-123",
            target_service="payment",
        )

        assert result.should_stop is False
        assert len(result.violations) == 0

    @patch.object(StopConditionsChecker, "_collect_metrics")
    def test_check_error_rate_violation(self, mock_collect):
        """에러율 위반 체크 테스트."""
        mock_collect.return_value = {
            "error_rate_percent": 7.5,  # 5% 초과
            "latency_p99_ms": 500,
            "latency_p95_ms": 300,
            "error_budget_remaining_percent": 80.0,
        }

        config = StopConditionsConfig(consecutive_breaches_required=1)
        checker = StopConditionsChecker(config=config)

        result = checker.check(
            experiment_id="test-123",
            target_service="payment",
        )

        assert result.should_stop is True
        assert len(result.violations) == 1
        assert result.violations[0].condition_type == "error_rate"

    @patch.object(StopConditionsChecker, "_collect_metrics")
    def test_check_latency_violation(self, mock_collect):
        """지연시간 위반 체크 테스트."""
        mock_collect.return_value = {
            "error_rate_percent": 1.0,
            "latency_p99_ms": 2500,  # 2000ms 초과
            "latency_p95_ms": 1200,  # 1000ms 초과
            "error_budget_remaining_percent": 80.0,
        }

        config = StopConditionsConfig(consecutive_breaches_required=1)
        checker = StopConditionsChecker(config=config)

        result = checker.check(
            experiment_id="test-123",
            target_service="payment",
        )

        assert result.should_stop is True
        assert len(result.violations) == 2  # P99, P95 둘 다 위반

    @patch.object(StopConditionsChecker, "_collect_metrics")
    def test_check_error_budget_violation(self, mock_collect):
        """에러 버짓 위반 체크 테스트."""
        mock_collect.return_value = {
            "error_rate_percent": 1.0,
            "latency_p99_ms": 500,
            "latency_p95_ms": 300,
            "error_budget_remaining_percent": 5.0,  # 10% 미만
        }

        config = StopConditionsConfig(consecutive_breaches_required=1)
        checker = StopConditionsChecker(config=config)

        result = checker.check(
            experiment_id="test-123",
            target_service="payment",
        )

        assert result.should_stop is True
        assert any(v.condition_type == "error_budget" for v in result.violations)

    @patch.object(StopConditionsChecker, "_collect_metrics")
    def test_consecutive_breaches_required(self, mock_collect):
        """연속 위반 횟수 요구 테스트."""
        mock_collect.return_value = {
            "error_rate_percent": 7.5,  # 위반
            "latency_p99_ms": 500,
            "latency_p95_ms": 300,
            "error_budget_remaining_percent": 80.0,
        }

        config = StopConditionsConfig(consecutive_breaches_required=2)
        checker = StopConditionsChecker(config=config)

        # 첫 번째 체크 - 아직 중단 안 함
        result1 = checker.check("test-123", "payment")
        assert result1.should_stop is False
        assert result1.consecutive_breach_count == 1

        # 두 번째 체크 - 이제 중단
        result2 = checker.check("test-123", "payment")
        assert result2.should_stop is True
        assert result2.consecutive_breach_count == 2

    @patch.object(StopConditionsChecker, "_collect_metrics")
    def test_breach_count_reset_on_success(self, mock_collect):
        """성공 시 위반 횟수 리셋 테스트."""
        checker = StopConditionsChecker(config=StopConditionsConfig(consecutive_breaches_required=2))

        # 첫 번째 체크 - 위반
        mock_collect.return_value = {
            "error_rate_percent": 7.5,
            "latency_p99_ms": 500,
            "latency_p95_ms": 300,
            "error_budget_remaining_percent": 80.0,
        }
        result1 = checker.check("test-123", "payment")
        assert result1.consecutive_breach_count == 1

        # 두 번째 체크 - 정상
        mock_collect.return_value = {
            "error_rate_percent": 1.0,  # 정상
            "latency_p99_ms": 500,
            "latency_p95_ms": 300,
            "error_budget_remaining_percent": 80.0,
        }
        result2 = checker.check("test-123", "payment")
        assert result2.consecutive_breach_count == 0

        # 세 번째 체크 - 다시 위반 (카운트 1부터 시작)
        mock_collect.return_value = {
            "error_rate_percent": 7.5,
            "latency_p99_ms": 500,
            "latency_p95_ms": 300,
            "error_budget_remaining_percent": 80.0,
        }
        result3 = checker.check("test-123", "payment")
        assert result3.consecutive_breach_count == 1

    def test_reset_breach_count(self):
        """위반 횟수 수동 리셋 테스트."""
        checker = StopConditionsChecker()
        checker._consecutive_breaches["test-123"] = 5

        checker.reset_breach_count("test-123")

        assert "test-123" not in checker._consecutive_breaches

    @patch.object(StopConditionsChecker, "_collect_metrics")
    def test_check_disabled(self, mock_collect):
        """비활성화 상태에서 체크 테스트."""
        config = StopConditionsConfig(enabled=False)
        checker = StopConditionsChecker(config=config)

        result = checker.check("test-123", "payment")

        assert result.should_stop is False
        mock_collect.assert_not_called()


# =============================================================================
# Singleton Tests
# =============================================================================


class TestStopConditionsCheckerSingleton:
    """싱글톤 패턴 테스트."""

    def setup_method(self):
        """각 테스트 전에 싱글톤 리셋."""
        reset_stop_conditions_checker()

    def test_get_singleton_instance(self):
        """싱글톤 인스턴스 반환 테스트."""
        checker1 = get_stop_conditions_checker()
        checker2 = get_stop_conditions_checker()

        assert checker1 is checker2

    def test_reset_singleton(self):
        """싱글톤 리셋 테스트."""
        checker1 = get_stop_conditions_checker()
        reset_stop_conditions_checker()
        checker2 = get_stop_conditions_checker()

        assert checker1 is not checker2
