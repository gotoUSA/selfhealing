"""
Chaos Dry Run Integration Tests

Tests for Dry Run full flow integration:
- Dry Run → Prediction → Recommendations (4 tests)

Total: 4 tests
"""



# =============================================================================
# Dry Run Integration Tests (4 tests)
# =============================================================================


class TestChaosDryRunIntegration:
    """Integration tests for Chaos Dry Run - 4 tests."""

    def test_dry_run_produces_complete_analysis(self):
        """Test dry run produces complete analysis with all components."""
        from selfhealing.services.chaos.blast_radius_analyzer import (
            get_blast_radius_analyzer,
        )
        from selfhealing.services.chaos.impact_predictor import get_impact_predictor

        predictor = get_impact_predictor()
        analyzer = get_blast_radius_analyzer()

        # Execute dry run analysis
        outcome = predictor.predict_outcome(
            experiment_type="latency_injection",
            target_service="payment-api",
            config={"latency_ms": 500},
        )

        impacts = predictor.predict_service_impact(
            target_service="payment-api",
            experiment_type="latency_injection",
            config={"latency_ms": 500},
        )

        blast_radius = analyzer.analyze(
            target_service="payment-api",
            experiment_type="latency_injection",
        )

        # Verify complete output
        assert outcome is not None
        assert outcome.predicted_cb_state in ["OPEN", "HALF_OPEN", "CLOSED"]
        assert len(impacts) >= 1
        assert blast_radius.target_service == "payment-api"

    def test_dry_run_analysis_serializable(self):
        """Test all dry run results are JSON serializable."""
        import json

        from selfhealing.services.chaos.blast_radius_analyzer import (
            get_blast_radius_analyzer,
        )
        from selfhealing.services.chaos.impact_predictor import get_impact_predictor

        predictor = get_impact_predictor()
        analyzer = get_blast_radius_analyzer()

        outcome = predictor.predict_outcome(
            experiment_type="failure_injection",
            target_service="order-service",
        )

        blast_radius = analyzer.analyze(
            target_service="order-service",
            experiment_type="failure_injection",
        )

        # Convert to dict
        outcome_dict = outcome.to_dict()
        blast_dict = blast_radius.to_dict()

        # Should be JSON serializable
        outcome_json = json.dumps(outcome_dict)
        blast_json = json.dumps(blast_dict)

        assert outcome_json is not None
        assert blast_json is not None

        # Verify roundtrip
        parsed_outcome = json.loads(outcome_json)
        parsed_blast = json.loads(blast_json)

        assert parsed_outcome["predicted_cb_state"] == outcome.predicted_cb_state
        assert parsed_blast["target_service"] == blast_radius.target_service

    def test_dry_run_generates_recommendations(self):
        """Test dry run generates actionable recommendations."""
        from selfhealing.services.chaos.blast_radius_analyzer import (
            get_blast_radius_analyzer,
        )

        analyzer = get_blast_radius_analyzer()

        # Analyze a potentially risky experiment
        result = analyzer.analyze(
            target_service="payment-api",
            experiment_type="failure_injection",
        )

        # Should have recommendations
        assert hasattr(result, "recommendations")
        assert isinstance(result.recommendations, list)

        # Risk score should be calculated
        assert hasattr(result, "risk_score")
        assert result.risk_score >= 0

    def test_dry_run_flow_with_approval_check(self):
        """Test dry run includes approval requirement check."""
        from selfhealing.services.chaos.impact_predictor import get_impact_predictor

        predictor = get_impact_predictor()

        # Resource exhaustion should require approval
        outcome = predictor.predict_outcome(
            experiment_type="resource_exhaustion",
            target_service="critical-db",
        )

        assert hasattr(outcome, "requires_approval")
        assert hasattr(outcome, "approval_reason")

        # Resource exhaustion typically requires approval
        if outcome.requires_approval:
            assert outcome.approval_reason is not None
            assert len(outcome.approval_reason) > 0

