"""
Drift Detection Integration Tests.

These tests require Docker environment with database.
Run with: docker-compose exec web pytest tests/self_healing/django/test_drift_detection_integration.py
"""

import pytest


@pytest.mark.django_db(transaction=True)
class TestDriftDetectionIntegration:
    """Integration tests with actual database."""

    def test_full_drift_detection_cycle(self):
        """Test full drift detection cycle with real data."""
        from shopping.tasks.drift_detection_tasks import check_sla_drift

        result = check_sla_drift()

        assert result["success"] is True
        assert "domains_checked" in result
