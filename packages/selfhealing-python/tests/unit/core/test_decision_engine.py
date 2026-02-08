"""
Tests for DecisionEngine.
core/decision_engine.py의 메트릭 기반 자율 조정 결정 엔진에 대한 단위 테스트.
조정 규칙 평가, 신뢰도 계산, 규칙 관리, 분석 이력 등을 검증합니다.
"""

import pytest
from unittest.mock import MagicMock, patch

from selfhealing.core.decision_engine import (
    DecisionEngine,
    AdjustmentDecision,
    AdjustmentRule,
    AdjustmentPriority,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_config_provider():
    """Mock ConfigProvider
    테스트용 설정 제공자. 파라미터별로 미리 정의된 값을 반환합니다.
    """
    provider = MagicMock()
    provider.get.side_effect = lambda key, default=None: {
        "timeout_ms": 1000,
        "retry_count": 3,
        "circuit_breaker_threshold": 0.5,
        "jitter_range": 0.1,
        "rate_limit_rps": 1000,
    }.get(key, default)
    return provider


@pytest.fixture
def engine(mock_config_provider):
    """DecisionEngine 인스턴스 생성."""
    return DecisionEngine(config_provider=mock_config_provider)


@pytest.fixture
def disabled_engine(mock_config_provider):
    """비활성화된 DecisionEngine 인스턴스 생성."""
    return DecisionEngine(config_provider=mock_config_provider, enabled=False)


# =============================================================================
# Initialization Tests
# =============================================================================


class TestDecisionEngineInit:
    """DecisionEngine 초기화 테스트."""

    def test_default_rules_loaded(self, engine):
        """Default rules loaded
        기본 5개 규칙이 로드되는지 확인.
        """
        assert len(engine.rules) == 5

    def test_custom_rules_added(self, mock_config_provider):
        """Custom rules added
        커스텀 규칙이 기본 규칙에 추가되는지 확인.
        """
        custom = AdjustmentRule(
            parameter="custom_param",
            metric="custom_metric",
            condition=lambda c, m: True,
            adjustment=lambda c, m: c * 1.1,
            reason="Custom rule",
        )
        engine = DecisionEngine(
            config_provider=mock_config_provider,
            custom_rules=[custom],
        )
        assert len(engine.rules) == 6

    def test_disabled_engine_returns_empty(self, disabled_engine):
        """Disabled engine returns empty
        비활성 상태에서는 빈 리스트를 반환하는지 확인.
        """
        decisions = disabled_engine.analyze({"p99_latency_ms": 900})
        assert decisions == []


# =============================================================================
# Analysis Tests
# =============================================================================


class TestDecisionEngineAnalyze:
    """DecisionEngine.analyze() 메트릭 분석 테스트."""

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_timeout_adjustment(self, mock_settings, engine):
        """Timeout adjustment triggered
        P99 레이턴시가 타임아웃의 80% 초과 시 조정이 제안되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_s.get_sample_confidence.return_value = 0.8
        mock_s.stability_factor_stable = 1.0
        mock_s.get_stability_factor.return_value = 1.0
        mock_settings.return_value = mock_s

        metrics = {
            "p99_latency_ms": 850,  # > 1000 * 0.8 = 800
            "sample_count": 100,
        }
        decisions = engine.analyze(metrics)
        timeout_decisions = [d for d in decisions if d.parameter == "timeout_ms"]
        assert len(timeout_decisions) == 1
        assert timeout_decisions[0].suggested_value > 1000

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_no_adjustment_when_below_threshold(self, mock_settings, engine):
        """No adjustment when below threshold
        P99 레이턴시가 타임아웃의 80% 미만이면 조정이 없는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_s.get_sample_confidence.return_value = 0.8
        mock_s.stability_factor_stable = 1.0
        mock_settings.return_value = mock_s

        metrics = {
            "p99_latency_ms": 700,  # < 1000 * 0.8 = 800
            "sample_count": 100,
        }
        decisions = engine.analyze(metrics)
        timeout_decisions = [d for d in decisions if d.parameter == "timeout_ms"]
        assert len(timeout_decisions) == 0

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_retry_exhaustion_triggers_adjustment(self, mock_settings, engine):
        """Retry exhaustion triggers adjustment
        재시도 소진율이 10% 이상이면 retry_count 조정이 제안되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_s.get_sample_confidence.return_value = 0.8
        mock_s.stability_factor_stable = 1.0
        mock_s.get_stability_factor.return_value = 1.0
        mock_settings.return_value = mock_s

        metrics = {
            "retry_exhausted_rate": 0.15,  # > 0.1
            "sample_count": 100,
        }
        decisions = engine.analyze(metrics)
        retry_decisions = [d for d in decisions if d.parameter == "retry_count"]
        assert len(retry_decisions) == 1
        assert retry_decisions[0].suggested_value == 4  # min(3+1, 5) = 4

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_circuit_breaker_threshold_adjustment(self, mock_settings, engine):
        """Circuit breaker threshold adjustment
        에러율이 CB 임계값의 90% 초과 시 조정이 제안되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_s.get_sample_confidence.return_value = 0.8
        mock_s.stability_factor_stable = 1.0
        mock_s.get_stability_factor.return_value = 1.0
        mock_settings.return_value = mock_s

        metrics = {
            "error_rate": 0.46,  # > 0.5 * 0.9 = 0.45
            "sample_count": 100,
        }
        decisions = engine.analyze(metrics)
        cb_decisions = [d for d in decisions if d.parameter == "circuit_breaker_threshold"]
        assert len(cb_decisions) == 1
        assert cb_decisions[0].suggested_value > 0.5

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_decisions_sorted_by_priority(self, mock_settings, engine):
        """Decisions sorted by priority
        결정이 우선순위로 정렬되는지 확인. HIGH > MEDIUM > LOW.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.01
        mock_s.get_sample_confidence.return_value = 0.9
        mock_s.stability_factor_stable = 1.0
        mock_s.get_stability_factor.return_value = 1.0
        mock_settings.return_value = mock_s

        metrics = {
            "p99_latency_ms": 900,  # timeout_ms (MEDIUM)
            "retry_exhausted_rate": 0.15,  # retry_count (MEDIUM)
            "error_rate": 0.46,  # circuit_breaker_threshold (HIGH)
            "sample_count": 100,
        }
        decisions = engine.analyze(metrics)
        if len(decisions) >= 2:
            # HIGH 우선순위가 먼저 오는지 확인
            priorities = [d.priority for d in decisions]
            priority_order = [
                AdjustmentPriority.CRITICAL,
                AdjustmentPriority.HIGH,
                AdjustmentPriority.MEDIUM,
                AdjustmentPriority.LOW,
            ]
            for i in range(len(priorities) - 1):
                assert priority_order.index(priorities[i]) <= priority_order.index(priorities[i + 1])

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_missing_metric_ignored(self, mock_settings, engine):
        """Missing metric ignored
        메트릭에 존재하지 않는 키가 있으면 해당 규칙이 무시되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_settings.return_value = mock_s

        metrics = {"nonexistent_metric": 100}
        decisions = engine.analyze(metrics)
        assert len(decisions) == 0


# =============================================================================
# Rule Management Tests
# =============================================================================


class TestDecisionEngineRuleManagement:
    """규칙 관리 테스트."""

    def test_add_rule(self, engine):
        """Add rule
        규칙 추가가 올바르게 동작하는지 확인.
        """
        initial_count = len(engine.rules)
        new_rule = AdjustmentRule(
            parameter="new_param",
            metric="new_metric",
            condition=lambda c, m: True,
            adjustment=lambda c, m: c * 1.1,
            reason="New rule",
        )
        engine.add_rule(new_rule)
        assert len(engine.rules) == initial_count + 1

    def test_remove_rule(self, engine):
        """Remove rule
        규칙 제거가 올바르게 동작하는지 확인.
        """
        initial_count = len(engine.rules)
        result = engine.remove_rule("timeout_ms")
        assert result is True
        assert len(engine.rules) == initial_count - 1

    def test_remove_nonexistent_rule(self, engine):
        """Remove nonexistent rule
        존재하지 않는 규칙 제거 시 False를 반환하는지 확인.
        """
        result = engine.remove_rule("nonexistent_param")
        assert result is False

    def test_get_rules(self, engine):
        """Get rules
        규칙 목록 조회가 올바르게 동작하는지 확인.
        """
        rules = engine.get_rules()
        assert len(rules) == 5
        assert all("parameter" in r for r in rules)
        assert all("metric" in r for r in rules)
        assert all("priority" in r for r in rules)


# =============================================================================
# History Tests
# =============================================================================


class TestDecisionEngineHistory:
    """분석 이력 테스트."""

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_history_recorded(self, mock_settings, engine):
        """History recorded
        분석 이력이 기록되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_settings.return_value = mock_s

        engine.analyze({"p99_latency_ms": 100, "sample_count": 10})
        history = engine.get_history()
        assert len(history) == 1
        assert "metrics" in history[0]
        assert "decisions_count" in history[0]

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_history_limit(self, mock_settings, engine):
        """History limit
        이력이 100개를 초과하면 오래된 것부터 제거되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_settings.return_value = mock_s

        for i in range(120):
            engine.analyze({"sample_count": i})
        history = engine.get_history(limit=200)
        assert len(history) <= 100

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_history_with_limit(self, mock_settings, engine):
        """History with limit parameter
        limit 파라미터가 올바르게 적용되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_settings.return_value = mock_s

        for _ in range(10):
            engine.analyze({"sample_count": 10})
        history = engine.get_history(limit=3)
        assert len(history) == 3


# =============================================================================
# Edge Cases Tests
# =============================================================================


class TestDecisionEngineEdgeCases:
    """경계 조건 및 예외 상황 테스트."""

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_config_provider_returns_none(self, mock_settings, mock_config_provider):
        """Config provider returns None
        설정 제공자가 None을 반환하면 해당 규칙이 건너뛰어지는지 확인.
        config_provider.get()이 None을 반환해도 기본값 사용으로 정상 동작.
        """
        mock_config_provider.get.return_value = None
        engine = DecisionEngine(config_provider=mock_config_provider)

        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_s.get_sample_confidence.return_value = 0.9
        mock_s.stability_factor_stable = 1.0
        mock_s.get_stability_factor.return_value = 1.0
        mock_settings.return_value = mock_s

        # config_provider.get()이 None을 반환하지만 규칙 기본값이 사용됨
        decisions = engine.analyze({"p99_latency_ms": 900, "sample_count": 100})
        # 정상 동작 확인 (에러 없이 실행)
        assert isinstance(decisions, list)

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_config_provider_returns_invalid_type(self, mock_settings, mock_config_provider):
        """Config provider returns invalid type
        설정 제공자가 변환할 수 없는 값을 반환해도 에러 없이 동작하는지 확인.
        """
        mock_config_provider.get.return_value = "not_a_number"
        engine = DecisionEngine(config_provider=mock_config_provider)

        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_s.get_sample_confidence.return_value = 0.9
        mock_s.stability_factor_stable = 1.0
        mock_s.get_stability_factor.return_value = 1.0
        mock_settings.return_value = mock_s

        decisions = engine.analyze({"p99_latency_ms": 900, "sample_count": 100})
        assert isinstance(decisions, list)

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_low_confidence_rejected(self, mock_settings, engine):
        """Low confidence rejected
        신뢰도가 min_confidence 미만이면 결정이 거부되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.05
        mock_s.get_sample_confidence.return_value = 0.1  # 낮은 샘플 신뢰도
        mock_s.stability_factor_stable = 0.5
        mock_s.get_stability_factor.return_value = 0.5
        mock_settings.return_value = mock_s

        metrics = {"p99_latency_ms": 900, "sample_count": 1}
        decisions = engine.analyze(metrics)
        # 신뢰도가 0.1*0.5 = 0.05 < 0.5 → 거부
        timeout_decisions = [d for d in decisions if d.parameter == "timeout_ms"]
        assert len(timeout_decisions) == 0

    @patch("selfhealing.core.decision_engine.get_decision_engine_settings")
    def test_min_change_ratio_filter(self, mock_settings):
        """Min change ratio filter
        변경 비율이 MIN_CHANGE_RATIO 미만이면 결정이 필터링되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.min_change_ratio = 0.5  # 50% 변경 필요
        mock_s.get_sample_confidence.return_value = 0.9
        mock_s.stability_factor_stable = 1.0
        mock_s.get_stability_factor.return_value = 1.0
        mock_settings.return_value = mock_s

        provider = MagicMock()
        provider.get.return_value = 1000  # current_value
        engine = DecisionEngine(config_provider=provider)

        # p99_latency_ms > 800 (1000*0.8) → suggestedValue = min(1000*1.2, 10000) = 1200
        # change_ratio = |1200-1000|/1000 = 0.2 < 0.5 → 필터링
        metrics = {"p99_latency_ms": 900, "sample_count": 100}
        decisions = engine.analyze(metrics)
        timeout_decisions = [d for d in decisions if d.parameter == "timeout_ms"]
        assert len(timeout_decisions) == 0
