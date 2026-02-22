"""
AuditSettings 샘플링 비율 설정 테스트.

테스트 대상:
- sampling_rate_limit_adjusted 필드
- sampling_rate_429 필드
"""

import pytest


class TestSamplingRateLimitAdjustedField:
    """sampling_rate_limit_adjusted 필드 테스트."""

    def test_field_exists(self):
        """필드 존재 확인."""
        from selfhealing.settings.audit_settings import AuditSettings

        settings = AuditSettings()

        assert hasattr(settings, "sampling_rate_limit_adjusted")

    def test_default_value(self):
        """기본값 확인 (0.1 = 10%)."""
        from selfhealing.settings.audit_settings import AuditSettings

        field_info = AuditSettings.model_fields.get("sampling_rate_limit_adjusted")
        expected_default = field_info.default

        settings = AuditSettings()

        assert settings.sampling_rate_limit_adjusted == expected_default
        assert settings.sampling_rate_limit_adjusted == 0.1

    def test_value_range_minimum(self):
        """최소값 제약 확인 (0.0)."""

        from selfhealing.settings.audit_settings import AuditSettings

        # 0.0은 허용됨
        settings = AuditSettings(sampling_rate_limit_adjusted=0.0)
        assert settings.sampling_rate_limit_adjusted == 0.0

    def test_value_range_maximum(self):
        """최대값 제약 확인 (1.0)."""

        from selfhealing.settings.audit_settings import AuditSettings

        # 1.0은 허용됨
        settings = AuditSettings(sampling_rate_limit_adjusted=1.0)
        assert settings.sampling_rate_limit_adjusted == 1.0

    def test_value_below_minimum_raises(self):
        """최소값 미만 시 ValidationError 발생."""
        from pydantic import ValidationError

        from selfhealing.settings.audit_settings import AuditSettings

        with pytest.raises(ValidationError):
            AuditSettings(sampling_rate_limit_adjusted=-0.1)

    def test_value_above_maximum_raises(self):
        """최대값 초과 시 ValidationError 발생."""
        from pydantic import ValidationError

        from selfhealing.settings.audit_settings import AuditSettings

        with pytest.raises(ValidationError):
            AuditSettings(sampling_rate_limit_adjusted=1.1)


class TestSamplingRate429Field:
    """sampling_rate_429 필드 테스트."""

    def test_field_exists(self):
        """필드 존재 확인."""
        from selfhealing.settings.audit_settings import AuditSettings

        settings = AuditSettings()

        assert hasattr(settings, "sampling_rate_429")

    def test_default_value(self):
        """기본값 확인 (0.5 = 50%)."""
        from selfhealing.settings.audit_settings import AuditSettings

        field_info = AuditSettings.model_fields.get("sampling_rate_429")
        expected_default = field_info.default

        settings = AuditSettings()

        assert settings.sampling_rate_429 == expected_default
        assert settings.sampling_rate_429 == 0.5

    def test_value_range_minimum(self):
        """최소값 제약 확인 (0.0)."""
        from selfhealing.settings.audit_settings import AuditSettings

        # 0.0은 허용됨
        settings = AuditSettings(sampling_rate_429=0.0)
        assert settings.sampling_rate_429 == 0.0

    def test_value_range_maximum(self):
        """최대값 제약 확인 (1.0)."""
        from selfhealing.settings.audit_settings import AuditSettings

        # 1.0은 허용됨
        settings = AuditSettings(sampling_rate_429=1.0)
        assert settings.sampling_rate_429 == 1.0

    def test_value_below_minimum_raises(self):
        """최소값 미만 시 ValidationError 발생."""
        from pydantic import ValidationError

        from selfhealing.settings.audit_settings import AuditSettings

        with pytest.raises(ValidationError):
            AuditSettings(sampling_rate_429=-0.1)

    def test_value_above_maximum_raises(self):
        """최대값 초과 시 ValidationError 발생."""
        from pydantic import ValidationError

        from selfhealing.settings.audit_settings import AuditSettings

        with pytest.raises(ValidationError):
            AuditSettings(sampling_rate_429=1.1)


class TestSamplingSettingsDescription:
    """샘플링 설정 필드 description 테스트."""

    def test_limit_adjusted_has_description(self):
        """sampling_rate_limit_adjusted 필드에 description 존재 확인."""
        from selfhealing.settings.audit_settings import AuditSettings

        field_info = AuditSettings.model_fields.get("sampling_rate_limit_adjusted")

        assert field_info.description is not None
        assert len(field_info.description) > 0

    def test_429_has_description(self):
        """sampling_rate_429 필드에 description 존재 확인."""
        from selfhealing.settings.audit_settings import AuditSettings

        field_info = AuditSettings.model_fields.get("sampling_rate_429")

        assert field_info.description is not None
        assert len(field_info.description) > 0
