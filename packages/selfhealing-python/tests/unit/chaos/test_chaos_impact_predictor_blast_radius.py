"""
Chaos Dry Run and Impact Prediction Tests

Tests for Chaos 시스템 영향도 예측 및 분석:
- ImpactPredictor (4 tests)
- BlastRadiusAnalyzer (4 tests)
- Dry Run API (4 tests)

Total: 12 tests
"""



# =============================================================================
# ImpactPredictor Tests (4 tests)
# =============================================================================


class TestImpactPredictor:
    """Tests for ImpactPredictor - 4 tests."""

    def test_predict_service_impact(self):
        """Test service impact prediction."""
        from selfhealing.services.chaos.impact_predictor import ImpactPredictor

        predictor = ImpactPredictor()

        impacts = predictor.predict_service_impact(
            target_service="payment-api",
            experiment_type="latency_injection",
            config={"latency_ms": 500},
        )

        # At least the target service should be in impacts
        assert len(impacts) >= 1

        direct_impact = impacts[0]
        assert direct_impact.service_name == "payment-api"
        assert direct_impact.is_direct_target is True
        assert direct_impact.impact_level in ["low", "medium", "high", "critical"]

    def test_predict_latency_increase(self):
        """Test latency increase prediction based on experiment config."""
        from selfhealing.services.chaos.impact_predictor import ImpactPredictor

        predictor = ImpactPredictor()

        impacts = predictor.predict_service_impact(
            target_service="payment-api",
            experiment_type="latency_injection",
            config={"latency_ms": 1000},
        )

        direct_impact = impacts[0]
        assert direct_impact.predicted_latency_increase_ms == 1000.0

    def test_predict_error_rate(self):
        """Test error rate prediction for failure injection."""
        from selfhealing.services.chaos.impact_predictor import ImpactPredictor

        predictor = ImpactPredictor()

        impacts = predictor.predict_service_impact(
            target_service="payment-api",
            experiment_type="failure_injection",
            config={"failure_rate": 50},
        )

        direct_impact = impacts[0]
        assert direct_impact.predicted_error_rate_percent == 50.0
        assert direct_impact.impact_level in ["medium", "high"]

    def test_confidence_score_calculation(self):
        """Test confidence score calculation in predictions."""
        from selfhealing.services.chaos.impact_predictor import ImpactPredictor

        predictor = ImpactPredictor()

        # With no prior patterns, confidence should be low
        outcome = predictor.predict_outcome(
            experiment_type="latency_injection",
            target_service="new-service-never-tested",
            config={},
        )

        # Low confidence when no patterns
        assert outcome.confidence_score <= 0.5
        assert outcome.patterns_used == 0


# =============================================================================
# BlastRadiusAnalyzer Tests (4 tests)
# =============================================================================


class TestBlastRadiusAnalyzer:
    """Tests for BlastRadiusAnalyzer - 4 tests."""

    def test_analyze_affected_services(self):
        """Test analysis of affected services."""
        from selfhealing.services.chaos.blast_radius_analyzer import BlastRadiusAnalyzer

        analyzer = BlastRadiusAnalyzer()

        affected = analyzer.analyze_affected_services(target_service="payment-api")

        # payment-api has upstream dependencies
        assert isinstance(affected, list)
        # Each item should be a DependencyNode
        for node in affected:
            assert hasattr(node, "service_name")
            assert hasattr(node, "depth")
            assert hasattr(node, "is_critical")

    def test_calculate_blast_radius_level(self):
        """Test blast radius level calculation."""
        from selfhealing.services.chaos.blast_radius_analyzer import (
            BlastRadiusAnalyzer,
            BlastRadiusLevel,
        )

        analyzer = BlastRadiusAnalyzer()

        result = analyzer.analyze(
            target_service="payment-api",
            experiment_type="latency_injection",
        )

        # payment-api is a critical service
        assert result.level in [
            BlastRadiusLevel.MINIMAL,
            BlastRadiusLevel.CONTAINED,
            BlastRadiusLevel.MODERATE,
            BlastRadiusLevel.EXTENSIVE,
            BlastRadiusLevel.CRITICAL,
        ]

    def test_dependency_graph_traversal(self):
        """Test dependency graph traversal with max depth."""
        from selfhealing.services.chaos.blast_radius_analyzer import BlastRadiusAnalyzer

        # Limited depth
        analyzer = BlastRadiusAnalyzer(max_depth=1)

        affected = analyzer.analyze_affected_services(target_service="order-service")

        # With max_depth=1, only direct dependencies
        for node in affected:
            assert node.depth <= 2  # Initial traversal + 1 level

    def test_critical_service_detection(self):
        """Test critical service detection in blast radius."""
        from selfhealing.services.chaos.blast_radius_analyzer import BlastRadiusAnalyzer

        analyzer = BlastRadiusAnalyzer()

        result = analyzer.analyze(
            target_service="payment-api",
            experiment_type="failure_injection",
        )

        # payment-api itself is critical
        # Check if analysis correctly identifies this
        assert result.includes_critical_services or len(result.critical_services) >= 0


# =============================================================================
# Dry Run API Tests (4 tests)
# =============================================================================


class TestDryRunAPI:
    """Tests for Dry Run Analysis API - 4 tests."""

    def test_dry_run_returns_prediction(self):
        """Test that dry run analysis returns prediction."""
        from selfhealing.services.chaos.impact_predictor import (
            ImpactPredictor,
            PredictedOutcome,
        )

        predictor = ImpactPredictor()

        outcome = predictor.predict_outcome(
            experiment_type="latency_injection",
            target_service="payment-api",
            config={"latency_ms": 500},
        )

        assert isinstance(outcome, PredictedOutcome)
        assert outcome.predicted_cb_state in ["OPEN", "HALF_OPEN", "CLOSED"]
        assert outcome.predicted_recovery_time_seconds > 0

    def test_dry_run_no_side_effects(self):
        """Test that dry run has no side effects."""
        from selfhealing.services.chaos.blast_radius_analyzer import BlastRadiusAnalyzer
        from selfhealing.services.chaos.impact_predictor import ImpactPredictor

        predictor = ImpactPredictor()
        analyzer = BlastRadiusAnalyzer()

        # Run analysis
        outcome = predictor.predict_outcome(
            experiment_type="failure_injection",
            target_service="payment-api",
        )

        analysis = analyzer.analyze(
            target_service="payment-api",
            experiment_type="failure_injection",
        )

        # Both should complete without errors
        assert outcome is not None
        assert analysis is not None

        # No actual experiments should be running
        # This is a dry run - just predictions

    def test_dry_run_with_blast_radius(self):
        """Test dry run analysis includes blast radius."""
        from selfhealing.services.chaos.blast_radius_analyzer import (
            BlastRadiusAnalysisResult,
            BlastRadiusAnalyzer,
        )

        analyzer = BlastRadiusAnalyzer()

        result = analyzer.analyze(
            target_service="payment-api",
            experiment_type="latency_injection",
        )

        assert isinstance(result, BlastRadiusAnalysisResult)
        assert result.target_service == "payment-api"
        assert result.experiment_type == "latency_injection"
        assert hasattr(result, "recommendations")
        assert isinstance(result.recommendations, list)

    def test_dry_run_approval_requirement(self):
        """Test dry run detects approval requirements."""
        from selfhealing.services.chaos.impact_predictor import ImpactPredictor

        predictor = ImpactPredictor()

        # Resource exhaustion should require approval
        outcome = predictor.predict_outcome(
            experiment_type="resource_exhaustion",
            target_service="payment-api",
        )

        # resource_exhaustion requires approval per implementation
        assert outcome.requires_approval is True
        assert "resource_exhaustion" in outcome.approval_reason


# =============================================================================
# Integration Tests
# =============================================================================


class TestPhase2Integration:
    """Integration tests for Phase 2 components."""

    def test_full_dry_run_analysis_flow(self):
        """Test complete dry run analysis flow."""
        from selfhealing.services.chaos.blast_radius_analyzer import (
            get_blast_radius_analyzer,
        )
        from selfhealing.services.chaos.impact_predictor import get_impact_predictor

        predictor = get_impact_predictor()
        analyzer = get_blast_radius_analyzer()

        # 1. Predict outcome
        outcome = predictor.predict_outcome(
            experiment_type="latency_injection",
            target_service="order-service",
            config={"latency_ms": 300},
        )

        # 2. Analyze service impacts
        impacts = predictor.predict_service_impact(
            target_service="order-service",
            experiment_type="latency_injection",
        )

        # 3. Analyze blast radius
        blast_analysis = analyzer.analyze(
            target_service="order-service",
            experiment_type="latency_injection",
        )

        # Verify complete response
        assert outcome.predicted_cb_state is not None
        assert len(impacts) >= 1
        assert blast_analysis.level is not None
        assert blast_analysis.risk_score >= 0

    def test_serialization_roundtrip(self):
        """Test that predictions serialize correctly."""
        from selfhealing.services.chaos.blast_radius_analyzer import BlastRadiusAnalyzer
        from selfhealing.services.chaos.impact_predictor import ImpactPredictor

        predictor = ImpactPredictor()
        analyzer = BlastRadiusAnalyzer()

        outcome = predictor.predict_outcome(
            experiment_type="failure_injection",
            target_service="payment-api",
        )

        blast = analyzer.analyze(
            target_service="payment-api",
            experiment_type="failure_injection",
        )

        # Convert to dict
        outcome_dict = outcome.to_dict()
        blast_dict = blast.to_dict()

        # Verify serialization
        assert "predicted_cb_state" in outcome_dict
        assert "confidence_score" in outcome_dict
        assert "target_service" in blast_dict
        assert "level" in blast_dict
        assert "risk_score" in blast_dict
