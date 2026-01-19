"""
Layered Storage 및 관련 컴포넌트 단위 테스트.

계층형 저장소, drift 재조정, shadow logger 테스트.
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from selfhealing.adapters.memory.drift_reconciliation import (
    DriftReconciler,
    get_drift_reconciler,
)
from selfhealing.adapters.memory.shadow_logger import (
    ShadowLogger,
    get_shadow_logger,
)


class TestDriftReconciler:
    """Tests for DriftReconciler."""

    def test_singleton_pattern(self):
        """Test get_drift_reconciler returns singleton."""
        r1 = get_drift_reconciler()
        r2 = get_drift_reconciler()
        assert r1 is r2

    def test_reconciler_instance(self):
        """Test DriftReconciler can be instantiated."""
        reconciler = DriftReconciler()
        assert reconciler is not None

    def test_reconciler_has_required_attributes(self):
        """Test DriftReconciler has required attributes."""
        reconciler = DriftReconciler()
        # Check that required methods exist
        assert hasattr(reconciler, "_lock")


class TestShadowLogger:
    """Tests for ShadowLogger."""

    def test_singleton_pattern(self):
        """Test get_shadow_logger returns singleton."""
        l1 = get_shadow_logger()
        l2 = get_shadow_logger()
        assert l1 is l2

    def test_shadow_logger_instance(self):
        """Test ShadowLogger can be instantiated."""
        logger = ShadowLogger()
        assert logger is not None

    def test_shadow_logger_has_storage(self):
        """Test ShadowLogger has internal storage."""
        logger = ShadowLogger()
        assert hasattr(logger, "_failure_log")
        assert hasattr(logger, "_lock")
