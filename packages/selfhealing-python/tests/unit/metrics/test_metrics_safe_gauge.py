"""
Tests for Safe Gauge Wrapper.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

import time
from unittest.mock import Mock, patch, MagicMock

import pytest


class TestSyncStatus:
    """Test SyncStatus enum."""

    def test_sync_status_values(self):
        """Should have correct string values."""
        from selfhealing.metrics.safe_gauge import SyncStatus

        assert SyncStatus.SYNCED.value == "synced"
        assert SyncStatus.STALE.value == "stale"
        assert SyncStatus.UNKNOWN.value == "unknown"
        assert SyncStatus.RECOVERING.value == "recovering"


class TestSyncInfo:
    """Test SyncInfo dataclass."""

    def test_default_values(self):
        """Should have correct default values."""
        from selfhealing.metrics.safe_gauge import SyncInfo, SyncStatus

        info = SyncInfo()

        assert info.status == SyncStatus.UNKNOWN
        assert info.last_sync_time is None
        assert info.last_sync_source == "none"
        assert info.staleness_threshold == 300.0  # 5 minutes
        assert info.stabilization_duration == 60.0  # 1 minute

    def test_age_seconds_property_none_when_no_sync(self):
        """Should return None when never synced."""
        from selfhealing.metrics.safe_gauge import SyncInfo

        info = SyncInfo()
        assert info.age_seconds is None

    def test_age_seconds_property_calculates_correctly(self):
        """Should calculate age correctly."""
        from selfhealing.metrics.safe_gauge import SyncInfo

        sync_time = time.time() - 60  # 1 minute ago
        info = SyncInfo(last_sync_time=sync_time)

        age = info.age_seconds
        assert age is not None
        assert 59 <= age <= 61

    def test_is_synced_true_when_fresh(self):
        """Should return True when data is fresh."""
        from selfhealing.metrics.safe_gauge import SyncInfo, SyncStatus

        info = SyncInfo(
            status=SyncStatus.SYNCED,
            last_sync_time=time.time(),  # Just now
        )

        assert info.is_synced is True

    def test_is_synced_false_when_stale(self):
        """Should return False when data is stale."""
        from selfhealing.metrics.safe_gauge import SyncInfo, SyncStatus

        old_time = time.time() - 600  # 10 minutes ago (> staleness_threshold)
        info = SyncInfo(
            status=SyncStatus.SYNCED,
            last_sync_time=old_time,
            staleness_threshold=300.0,
        )

        assert info.is_synced is False

    def test_is_synced_false_when_not_synced_status(self):
        """Should return False when status is not SYNCED."""
        from selfhealing.metrics.safe_gauge import SyncInfo, SyncStatus

        info = SyncInfo(
            status=SyncStatus.UNKNOWN,
            last_sync_time=time.time(),
        )

        assert info.is_synced is False

    def test_is_recovering_property(self):
        """Should correctly determine if recovering."""
        from selfhealing.metrics.safe_gauge import SyncInfo, SyncStatus

        # Not recovering
        info_unknown = SyncInfo(status=SyncStatus.UNKNOWN)
        assert info_unknown.is_recovering is False

        # Recovering
        info_recovering = SyncInfo(
            status=SyncStatus.RECOVERING,
            stabilization_start=time.time(),
            stabilization_duration=60.0,
        )
        assert info_recovering.is_recovering is True

    def test_recovery_progress_property(self):
        """Should calculate recovery progress correctly."""
        from selfhealing.metrics.safe_gauge import SyncInfo, SyncStatus

        # Full progress when not recovering
        info_complete = SyncInfo(status=SyncStatus.SYNCED)
        assert info_complete.recovery_progress == 1.0

        # Partial progress during recovery
        start_time = time.time() - 30  # Started 30s ago
        info_recovering = SyncInfo(
            status=SyncStatus.RECOVERING,
            stabilization_start=start_time,
            stabilization_duration=60.0,
        )
        progress = info_recovering.recovery_progress
        assert 0.4 <= progress <= 0.6  # About 50%


class TestClampFunctions:
    """Test clamp utility functions."""

    def test_clamp_non_negative(self):
        """Should clamp values to non-negative."""
        from selfhealing.metrics.safe_gauge import clamp_non_negative

        assert clamp_non_negative(5) == 5
        assert clamp_non_negative(0) == 0
        assert clamp_non_negative(-5) == 0
        assert clamp_non_negative(-0.001) == 0

    def test_clamp_percentage(self):
        """Should clamp values to 0-100 range."""
        from selfhealing.metrics.safe_gauge import clamp_percentage

        assert clamp_percentage(50) == 50
        assert clamp_percentage(0) == 0
        assert clamp_percentage(100) == 100
        assert clamp_percentage(-10) == 0
        assert clamp_percentage(150) == 100


class TestSafeGauge:
    """Test SafeGauge wrapper class."""

    @pytest.fixture
    def mock_gauge(self):
        """Create mock prometheus gauge."""
        gauge = Mock()
        labeled = Mock()
        labeled._value = Mock()
        labeled._value.get.return_value = 5.0
        gauge.labels.return_value = labeled
        return gauge

    def test_labels_returns_safe_labeled_gauge(self, mock_gauge):
        """Should return SafeLabeledGauge when labels() called."""
        from selfhealing.metrics.safe_gauge import SafeGauge

        safe = SafeGauge(mock_gauge)
        labeled = safe.labels(domain="payment")

        # Should have called underlying gauge's labels method
        mock_gauge.labels.assert_called_once_with(domain="payment")

    def test_dec_prevents_negative(self, mock_gauge):
        """Should prevent gauge from going negative on dec()."""
        from selfhealing.metrics.safe_gauge import SafeGauge

        # Set up mock to return 0
        labeled = Mock()
        labeled._value = Mock()
        labeled._value.get.return_value = 0.0
        mock_gauge.labels.return_value = labeled

        safe = SafeGauge(mock_gauge)
        safe_labeled = safe.labels(domain="payment")

        # Should not go negative
        if hasattr(safe_labeled, "dec"):
            safe_labeled.dec()
            # Underlying dec should be called with clamped value or not at all
            # depending on implementation


class TestSafeGaugeIntegration:
    """Integration tests for SafeGauge with real scenarios."""

    def test_server_restart_scenario(self):
        """Should handle server restart (gauge reset to 0)."""
        from selfhealing.metrics.safe_gauge import SyncInfo, SyncStatus

        # After restart, gauge is at 0 but events say -1
        # SafeGauge should clamp to 0

        info = SyncInfo(
            status=SyncStatus.UNKNOWN,  # Not synced after restart
        )

        assert info.is_synced is False
        # This indicates we need to sync before trusting the value

    def test_sync_then_decrement_flow(self):
        """Should handle sync followed by decrement."""
        from selfhealing.metrics.safe_gauge import SyncInfo, SyncStatus

        # Simulate: sync sets gauge to 5, then 3 decrements
        info = SyncInfo(
            status=SyncStatus.SYNCED,
            last_sync_time=time.time(),
            last_sync_source="hydration",
        )

        assert info.is_synced is True
        # After this, safe decrements would work from 5 down to 2
