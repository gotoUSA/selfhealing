"""
Tests for Correlation Engine Interfaces — 전략 Protocol 검증.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: Protocol isinstance, 메서드 시그니처 검증 (하드코딩)
- Behavior: 전략 교체, 전략 등록/조회 동작 검증

참조 소스:
- services/correlation_engine/interfaces.py (CorrelationStrategy, RootCauseStrategy, GraphBuildStrategy)
- services/correlation_engine/root_cause_ranker.py (StrategyMetadata)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from selfhealing.services.correlation_engine.interfaces import (
    CorrelationStrategy,
    GraphBuildStrategy,
    RootCauseStrategy,
)
from selfhealing.services.correlation_engine.root_cause_ranker import (
    StrategyMetadata,
)


# =============================================================================
# Stub 구현 (테스트 전용)
# =============================================================================


class StubCorrelationStrategy:
    """CorrelationStrategy Protocol 구현 스텁."""

    def analyze(
        self,
        event_pairs: list[tuple[str, str, float]],
        time_window: float,
    ) -> list:
        return []

    def get_pair_score(self, event_type_a: str, event_type_b: str) -> float | None:
        return 0.5


class StubRootCauseStrategy:
    """RootCauseStrategy Protocol 구현 스텁."""

    def rank_causes(self, dag: Any, co_occurrence_data: list) -> Any:
        return None


class StubGraphBuildStrategy:
    """GraphBuildStrategy Protocol 구현 스텁."""

    def build_dag(self, events: list, window_seconds: float) -> Any:
        return None


class IncompleteCorrelationStrategy:
    """CorrelationStrategy 미완성 — get_pair_score() 누락."""

    def analyze(self, event_pairs: list, time_window: float) -> list:
        return []


# =============================================================================
# Contract Tests — Protocol isinstance 검증
# =============================================================================


class TestCorrelationStrategyContract:
    """CorrelationStrategy Protocol 계약 검증."""

    def test_isinstance_passes_for_compliant_class(self):
        strategy = StubCorrelationStrategy()
        assert isinstance(strategy, CorrelationStrategy)

    def test_isinstance_fails_for_incomplete_class(self):
        incomplete = IncompleteCorrelationStrategy()
        assert not isinstance(incomplete, CorrelationStrategy)

    def test_isinstance_fails_for_plain_object(self):
        assert not isinstance(object(), CorrelationStrategy)


class TestRootCauseStrategyContract:
    """RootCauseStrategy Protocol 계약 검증."""

    def test_isinstance_passes_for_compliant_class(self):
        strategy = StubRootCauseStrategy()
        assert isinstance(strategy, RootCauseStrategy)

    def test_isinstance_fails_for_plain_object(self):
        assert not isinstance(object(), RootCauseStrategy)


class TestGraphBuildStrategyContract:
    """GraphBuildStrategy Protocol 계약 검증."""

    def test_isinstance_passes_for_compliant_class(self):
        strategy = StubGraphBuildStrategy()
        assert isinstance(strategy, GraphBuildStrategy)

    def test_isinstance_fails_for_plain_object(self):
        assert not isinstance(object(), GraphBuildStrategy)


# =============================================================================
# StrategyMetadata Contract Tests
# =============================================================================


class TestStrategyMetadataContract:
    """StrategyMetadata 데이터 구조 계약 검증."""

    def test_default_values(self):
        """기본값: fallback_used=False, analysis_duration_ms=0.0."""
        meta = StrategyMetadata(strategy_name="TestStrategy")
        assert meta.strategy_name == "TestStrategy"
        assert meta.fallback_used is False
        assert meta.fallback_reason is None
        assert meta.primary_strategy_name is None
        assert meta.analysis_duration_ms == 0.0
        assert meta.model_version is None

    def test_fallback_metadata(self):
        """Fallback 발동 시 메타데이터 기록."""
        meta = StrategyMetadata(
            strategy_name="RootCauseRanker",
            fallback_used=True,
            fallback_reason="BulkheadTimeoutError: ml_inference timeout after 30.0s",
            primary_strategy_name="LLMRootCauseAnalyzer",
            analysis_duration_ms=30142.5,
        )
        assert meta.fallback_used is True
        assert meta.primary_strategy_name == "LLMRootCauseAnalyzer"
        assert "BulkheadTimeoutError" in meta.fallback_reason

    def test_model_version_tracking(self):
        """ML 모델 버전 추적."""
        meta = StrategyMetadata(
            strategy_name="IsolationForestDetector",
            model_version="v2.1.0-20260221",
        )
        assert meta.model_version == "v2.1.0-20260221"
