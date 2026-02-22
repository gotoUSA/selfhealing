"""
Tests for EmergencyDecisionEngine — RootCauseAnalysis → EmergencyLevel 권고.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: 레벨별 임계값 상수, EmergencyDecisionResult frozen 속성 검증
- Behavior: evaluate() 레벨 분류 로직, threshold override, 경계값 동작 검증

참조 소스:
- services/correlation_engine/emergency_decision.py
  (EmergencyDecisionEngine, EmergencyDecisionResult, LEVEL_*_MIN_*)
- services/correlation_engine/root_cause_ranker.py
  (RootCauseAnalysis, RootCauseCandidate)
- services/correlation_engine/event_graph.py (EventNode, EventDAG)
- services/emergency_mode/enums.py (EmergencyLevel)
"""

from __future__ import annotations

import time
from dataclasses import FrozenInstanceError

import pytest

from selfhealing.services.correlation_engine.emergency_decision import (
    LEVEL_1_MIN_CONFIDENCE,
    LEVEL_1_MIN_SCORE,
    LEVEL_2_MIN_SCORE,
    LEVEL_2_MIN_SERVICES,
    LEVEL_3_MIN_CASCADE_DEPTH,
    LEVEL_3_MIN_SCORE,
    LEVEL_3_MIN_SERVICES,
    EmergencyDecisionEngine,
    EmergencyDecisionResult,
)
from selfhealing.services.correlation_engine.event_graph import (
    EventDAG,
    EventNode,
)
from selfhealing.services.correlation_engine.root_cause_ranker import (
    RootCauseAnalysis,
    RootCauseCandidate,
)
from selfhealing.services.emergency_mode.enums import EmergencyLevel

# =============================================================================
# 헬퍼
# =============================================================================


def _make_event_node(service_name: str = "payment-api") -> EventNode:
    """테스트용 EventNode 생성."""
    return EventNode(
        event_id="test-event-001",
        event_type="circuit_breaker_opened",
        service_name=service_name,
        timestamp=time.time(),
        data={},
        correlation_id=None,
    )


def _make_analysis(
    score: float = 0.5,
    confidence: float = 0.7,
    affected_services: list[str] | None = None,
    cascade_depth: int = 1,
) -> RootCauseAnalysis:
    """테스트용 RootCauseAnalysis 생성."""
    if affected_services is None:
        affected_services = ["svc-a"]

    node = _make_event_node()
    candidate = RootCauseCandidate(
        event_node=node,
        score=score,
        rank=1,
        evidence=["test evidence"],
        contributing_factors={"test": 1.0},
        affected_services=affected_services,
        cascade_depth=cascade_depth,
    )

    return RootCauseAnalysis(
        incident_id="incident-001",
        analyzed_at=time.time(),
        dag=EventDAG(
            incident_id="incident-001",
            window_start=time.time() - 300,
            window_end=time.time(),
        ),
        candidates=[candidate],
        primary_cause=candidate,
        confidence=confidence,
        summary="Test analysis",
    )


# =============================================================================
# 임계값 상수 계약 검증
# =============================================================================


class TestEmergencyDecisionConstantsContract:
    """Emergency Decision 레벨별 임계값 설계 계약."""

    def test_level_3_score_threshold(self):
        """LEVEL_3 최소 score 임계값은 0.8이다."""
        assert LEVEL_3_MIN_SCORE == 0.8

    def test_level_3_min_services(self):
        """LEVEL_3 최소 영향 서비스 수는 3이다."""
        assert LEVEL_3_MIN_SERVICES == 3

    def test_level_3_min_cascade_depth(self):
        """LEVEL_3 최소 cascade 깊이는 3이다."""
        assert LEVEL_3_MIN_CASCADE_DEPTH == 3

    def test_level_2_score_threshold(self):
        """LEVEL_2 최소 score 임계값은 0.6이다."""
        assert LEVEL_2_MIN_SCORE == 0.6

    def test_level_2_min_services(self):
        """LEVEL_2 최소 영향 서비스 수는 2이다."""
        assert LEVEL_2_MIN_SERVICES == 2

    def test_level_1_score_threshold(self):
        """LEVEL_1 최소 score 임계값은 0.4이다."""
        assert LEVEL_1_MIN_SCORE == 0.4

    def test_level_1_min_confidence(self):
        """LEVEL_1 최소 confidence는 0.5이다."""
        assert LEVEL_1_MIN_CONFIDENCE == 0.5


# =============================================================================
# EmergencyDecisionResult 계약 검증
# =============================================================================


class TestEmergencyDecisionResultContract:
    """EmergencyDecisionResult frozen 데이터클래스 계약."""

    def test_frozen_prevents_mutation(self):
        """EmergencyDecisionResult는 frozen=True이므로 필드 변경이 금지된다."""
        result = EmergencyDecisionResult(
            suggested_level=EmergencyLevel.NORMAL,
            reason="test",
            primary_service="svc",
            primary_score=0.1,
            analysis_confidence=0.5,
            affected_service_count=1,
            cascade_depth=1,
        )
        with pytest.raises(FrozenInstanceError):
            result.suggested_level = EmergencyLevel.LEVEL_3  # type: ignore[misc]

    def test_to_dict_includes_level_name(self):
        """to_dict()는 suggested_level의 name과 value 모두 포함한다."""
        result = EmergencyDecisionResult(
            suggested_level=EmergencyLevel.LEVEL_2,
            reason="test reason",
            primary_service="svc",
            primary_score=0.6,
            analysis_confidence=0.8,
            affected_service_count=2,
            cascade_depth=2,
        )
        d = result.to_dict()
        assert d["suggested_level"] == EmergencyLevel.LEVEL_2.value
        assert d["suggested_level_name"] == "LEVEL_2"


# =============================================================================
# evaluate() 동작 검증
# =============================================================================


class TestEmergencyDecisionEngineBehavior:
    """EmergencyDecisionEngine.evaluate() 동작 검증."""

    def test_level_3_all_thresholds_met(self):
        """score≥0.8 + services≥3 + depth≥3 → LEVEL_3."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(
            score=0.85,
            confidence=0.9,
            affected_services=["a", "b", "c"],
            cascade_depth=3,
        )
        result = engine.evaluate(analysis)
        assert result.suggested_level == EmergencyLevel.LEVEL_3

    def test_level_2_score_and_services(self):
        """score≥0.6 + services≥2 (depth<3) → LEVEL_2."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(
            score=0.65,
            confidence=0.8,
            affected_services=["a", "b"],
            cascade_depth=2,
        )
        result = engine.evaluate(analysis)
        assert result.suggested_level == EmergencyLevel.LEVEL_2

    def test_level_1_score_and_confidence(self):
        """score≥0.4 + confidence≥0.5 (services<2) → LEVEL_1."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(
            score=0.45,
            confidence=0.6,
            affected_services=["a"],
            cascade_depth=1,
        )
        result = engine.evaluate(analysis)
        assert result.suggested_level == EmergencyLevel.LEVEL_1

    def test_normal_when_below_all_thresholds(self):
        """모든 임계값 미달 시 → NORMAL."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(
            score=0.2,
            confidence=0.3,
            affected_services=["a"],
            cascade_depth=1,
        )
        result = engine.evaluate(analysis)
        assert result.suggested_level == EmergencyLevel.NORMAL

    def test_level_1_rejected_when_low_confidence(self):
        """score≥0.4이지만 confidence<0.5 → NORMAL (LEVEL_1 거부)."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(
            score=0.45,
            confidence=0.3,
            affected_services=["a"],
            cascade_depth=1,
        )
        result = engine.evaluate(analysis)
        assert result.suggested_level == EmergencyLevel.NORMAL

    def test_level_3_rejected_when_insufficient_services(self):
        """score≥0.8이지만 services<3 → LEVEL_3 아님 (LEVEL_2 또는 하위)."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(
            score=0.85,
            confidence=0.9,
            affected_services=["a", "b"],
            cascade_depth=3,
        )
        result = engine.evaluate(analysis)
        # services=2 < 3 → LEVEL_3 미달, score 0.85≥0.6 + services=2≥2 → LEVEL_2
        assert result.suggested_level == EmergencyLevel.LEVEL_2

    def test_threshold_override(self):
        """score_threshold_override로 레벨별 임계값을 동적 조정한다."""
        engine = EmergencyDecisionEngine(score_threshold_override={"level_3_score": 0.5})
        analysis = _make_analysis(
            score=0.55,
            confidence=0.9,
            affected_services=["a", "b", "c"],
            cascade_depth=3,
        )
        result = engine.evaluate(analysis)
        assert result.suggested_level == EmergencyLevel.LEVEL_3

    def test_result_factors_populated(self):
        """evaluate() 결과의 factors에 분석 지표가 포함된다."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(score=0.5, confidence=0.6)
        result = engine.evaluate(analysis)

        assert "primary_score" in result.factors
        assert "confidence" in result.factors
        assert "cascade_depth" in result.factors
        assert "candidate_count" in result.factors

    def test_result_primary_service_matches_node(self):
        """결과의 primary_service가 분석의 primary_cause 노드 서비스명과 일치."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(score=0.5, confidence=0.6)

        result = engine.evaluate(analysis)
        assert result.primary_service == analysis.primary_cause.event_node.service_name

    def test_boundary_level_3_exact_thresholds(self):
        """정확히 LEVEL_3 임계값 경계에서 LEVEL_3이 선택된다."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(
            score=LEVEL_3_MIN_SCORE,
            confidence=0.9,
            affected_services=["a", "b", "c"],
            cascade_depth=LEVEL_3_MIN_CASCADE_DEPTH,
        )
        result = engine.evaluate(analysis)
        assert result.suggested_level == EmergencyLevel.LEVEL_3

    def test_boundary_level_2_exact_thresholds(self):
        """정확히 LEVEL_2 임계값 경계에서 LEVEL_2가 선택된다."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(
            score=LEVEL_2_MIN_SCORE,
            confidence=0.4,
            affected_services=["a", "b"],
            cascade_depth=1,
        )
        result = engine.evaluate(analysis)
        assert result.suggested_level == EmergencyLevel.LEVEL_2

    def test_boundary_level_1_exact_thresholds(self):
        """정확히 LEVEL_1 임계값 경계에서 LEVEL_1이 선택된다."""
        engine = EmergencyDecisionEngine()
        analysis = _make_analysis(
            score=LEVEL_1_MIN_SCORE,
            confidence=LEVEL_1_MIN_CONFIDENCE,
            affected_services=["a"],
            cascade_depth=1,
        )
        result = engine.evaluate(analysis)
        assert result.suggested_level == EmergencyLevel.LEVEL_1
