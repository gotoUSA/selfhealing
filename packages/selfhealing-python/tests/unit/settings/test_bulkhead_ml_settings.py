"""
Tests for BulkheadSettings ML 추론 전용 설정 필드.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: ML 필드 기본값 계약 검증 (하드코딩)

참조 소스:
- settings/bulkhead.py (BulkheadSettings)
"""

from __future__ import annotations

import pytest

from selfhealing.settings.bulkhead import BulkheadSettings, reset_bulkhead_settings


@pytest.fixture(autouse=True)
def _reset_settings():
    """각 테스트 전후 싱글톤 리셋."""
    reset_bulkhead_settings()
    yield
    reset_bulkhead_settings()


class TestBulkheadSettingsMLContract:
    """BulkheadSettings ML 추론 전용 필드 계약값 검증."""

    def test_ml_inference_max_workers_default(self):
        """ML 추론 스레드 풀 워커 기본값: 3."""
        settings = BulkheadSettings()
        assert settings.ml_inference_max_workers == 3

    def test_ml_inference_queue_size_default(self):
        """ML 추론 대기 큐 크기 기본값: 5."""
        settings = BulkheadSettings()
        assert settings.ml_inference_queue_size == 5

    def test_ml_inference_timeout_default(self):
        """ML 추론 타임아웃 기본값: 30.0초."""
        settings = BulkheadSettings()
        assert settings.ml_inference_timeout == 30.0

    def test_ml_inference_max_workers_bounds(self):
        """ML 워커 수 범위: 1~20."""
        field = BulkheadSettings.model_fields["ml_inference_max_workers"]
        metadata = field.metadata
        ge_constraint = None
        le_constraint = None
        for m in metadata:
            if hasattr(m, "ge"):
                ge_constraint = m.ge
            if hasattr(m, "le"):
                le_constraint = m.le
        assert ge_constraint == 1
        assert le_constraint == 20

    def test_ml_inference_queue_size_bounds(self):
        """ML 큐 크기 범위: 0~50."""
        field = BulkheadSettings.model_fields["ml_inference_queue_size"]
        metadata = field.metadata
        ge_constraint = None
        le_constraint = None
        for m in metadata:
            if hasattr(m, "ge"):
                ge_constraint = m.ge
            if hasattr(m, "le"):
                le_constraint = m.le
        assert ge_constraint == 0
        assert le_constraint == 50

    def test_ml_inference_timeout_bounds(self):
        """ML 타임아웃 범위: 1.0~120.0."""
        field = BulkheadSettings.model_fields["ml_inference_timeout"]
        metadata = field.metadata
        ge_constraint = None
        le_constraint = None
        for m in metadata:
            if hasattr(m, "ge"):
                ge_constraint = m.ge
            if hasattr(m, "le"):
                le_constraint = m.le
        assert ge_constraint == 1.0
        assert le_constraint == 120.0
