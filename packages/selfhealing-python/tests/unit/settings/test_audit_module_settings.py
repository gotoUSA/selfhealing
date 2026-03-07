"""
Tests for Audit Module Settings Extensions.

Step 1,2 of 105_HARDCODED_CONFIG_AUDIT_REFACTORING.md

Tests:
- AuditIntegritySettings: anchor, cross_cluster, health_score, s3_worm 필드 추가
- CascadeRetentionSettings: max_cascade_index_size 필드 추가
- ResilientRecorderSettings: memory_buffer 필드 추가
- AuditSettings: compliance_max_retention_days 필드 추가
- HashChainSettings: 신규 모듈
"""

import pytest
from pydantic import ValidationError


class TestAuditIntegritySettingsExtensions:
    """AuditIntegritySettings 확장 필드 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.audit_integrity import reset_audit_integrity_settings
        reset_audit_integrity_settings()
        yield
        reset_audit_integrity_settings()

    def test_anchor_retention_days_default(self):
        """anchor_retention_days 기본값 90일 검증."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        settings = AuditIntegritySettings()
        assert settings.anchor_retention_days == 90

    def test_cross_cluster_ttl_defaults(self):
        """cross_cluster TTL 기본값 검증."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        settings = AuditIntegritySettings()
        assert settings.cross_cluster_local_ttl_days == 90
        assert settings.cross_cluster_global_ttl_days == 365

    def test_cross_cluster_ttl_env_override(self, monkeypatch):
        """cross_cluster TTL 환경변수 오버라이드 검증."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_CROSS_CLUSTER_LOCAL_TTL_DAYS", "60")
        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_CROSS_CLUSTER_GLOBAL_TTL_DAYS", "180")

        settings = AuditIntegritySettings()
        assert settings.cross_cluster_local_ttl_days == 60
        assert settings.cross_cluster_global_ttl_days == 180

    def test_cross_cluster_ttl_validation_global_lt_local_fails(self, monkeypatch):
        """global TTL이 local보다 작으면 ValidationError 발생."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_CROSS_CLUSTER_LOCAL_TTL_DAYS", "180")
        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_CROSS_CLUSTER_GLOBAL_TTL_DAYS", "90")

        with pytest.raises(ValidationError) as exc_info:
            AuditIntegritySettings()

        assert "cross_cluster_global_ttl_days" in str(exc_info.value)

    def test_health_score_thresholds_defaults(self):
        """health_score 임계값 기본값 검증."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        settings = AuditIntegritySettings()
        assert settings.health_healthy_threshold == 95.0
        assert settings.health_warning_threshold == 80.0
        assert settings.health_critical_threshold == 50.0

    def test_health_score_thresholds_env_override(self, monkeypatch):
        """health_score 임계값 환경변수 오버라이드 검증."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_HEALTH_HEALTHY_THRESHOLD", "90.0")
        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_HEALTH_WARNING_THRESHOLD", "70.0")
        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_HEALTH_CRITICAL_THRESHOLD", "30.0")

        settings = AuditIntegritySettings()
        assert settings.health_healthy_threshold == 90.0
        assert settings.health_warning_threshold == 70.0
        assert settings.health_critical_threshold == 30.0

    def test_health_score_thresholds_validation_healthy_le_warning_fails(self, monkeypatch):
        """healthy <= warning 이면 ValidationError 발생."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_HEALTH_HEALTHY_THRESHOLD", "80.0")
        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_HEALTH_WARNING_THRESHOLD", "85.0")

        with pytest.raises(ValidationError) as exc_info:
            AuditIntegritySettings()

        assert "health_healthy_threshold" in str(exc_info.value)

    def test_health_score_thresholds_validation_warning_le_critical_fails(self, monkeypatch):
        """warning <= critical 이면 ValidationError 발생."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_HEALTH_WARNING_THRESHOLD", "50.0")
        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_HEALTH_CRITICAL_THRESHOLD", "60.0")

        with pytest.raises(ValidationError) as exc_info:
            AuditIntegritySettings()

        assert "health_warning_threshold" in str(exc_info.value)

    def test_s3_worm_retention_days_default(self):
        """s3_worm_retention_days 기본값 365일 검증."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        settings = AuditIntegritySettings()
        assert settings.s3_worm_retention_days == 365

    def test_s3_worm_retention_days_env_override(self, monkeypatch):
        """s3_worm_retention_days 환경변수 오버라이드 검증."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        monkeypatch.setenv("SELFHEALING_AUDIT_INTEGRITY_S3_WORM_RETENTION_DAYS", "730")

        settings = AuditIntegritySettings()
        assert settings.s3_worm_retention_days == 730

    def test_s3_worm_retention_days_validation_range(self):
        """s3_worm_retention_days 범위 검증."""
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        # 최소 90일
        with pytest.raises(ValidationError):
            AuditIntegritySettings(s3_worm_retention_days=30)

        # 최대 2555일 (7년)
        with pytest.raises(ValidationError):
            AuditIntegritySettings(s3_worm_retention_days=3000)


class TestCascadeRetentionSettingsExtensions:
    """CascadeRetentionSettings 확장 필드 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.cascade_retention import (
            reset_cascade_retention_settings,
        )
        reset_cascade_retention_settings()
        yield
        reset_cascade_retention_settings()

    def test_max_cascade_index_size_default(self):
        """max_cascade_index_size 기본값 10000 검증."""
        from selfhealing.settings.cascade_retention import CascadeRetentionSettings

        settings = CascadeRetentionSettings()
        assert settings.max_cascade_index_size == 10000

    def test_max_cascade_index_size_env_override(self, monkeypatch):
        """max_cascade_index_size 환경변수 오버라이드 검증."""
        from selfhealing.settings.cascade_retention import CascadeRetentionSettings

        monkeypatch.setenv("SELFHEALING_CASCADE_RETENTION_MAX_CASCADE_INDEX_SIZE", "50000")

        settings = CascadeRetentionSettings()
        assert settings.max_cascade_index_size == 50000

    def test_max_cascade_index_size_validation_range(self):
        """max_cascade_index_size 범위 검증."""
        from selfhealing.settings.cascade_retention import CascadeRetentionSettings

        # 최소 1000
        with pytest.raises(ValidationError):
            CascadeRetentionSettings(max_cascade_index_size=500)

        # 최대 100000
        with pytest.raises(ValidationError):
            CascadeRetentionSettings(max_cascade_index_size=200000)


class TestResilientRecorderSettingsExtensions:
    """ResilientRecorderSettings 확장 필드 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.resilient_recorder import (
            reset_resilient_recorder_settings,
        )
        reset_resilient_recorder_settings()
        yield
        reset_resilient_recorder_settings()

    def test_memory_buffer_max_entries_default(self):
        """memory_buffer_max_entries 기본값 10000 검증."""
        from selfhealing.settings.resilient_recorder import ResilientRecorderSettings

        settings = ResilientRecorderSettings()
        assert settings.memory_buffer_max_entries == 10000

    def test_memory_buffer_flush_interval_default(self):
        """memory_buffer_flush_interval 기본값 30.0초 검증."""
        from selfhealing.settings.resilient_recorder import ResilientRecorderSettings

        settings = ResilientRecorderSettings()
        assert settings.memory_buffer_flush_interval == 30.0

    def test_memory_buffer_env_override(self, monkeypatch):
        """memory_buffer 필드 환경변수 오버라이드 검증."""
        from selfhealing.settings.resilient_recorder import ResilientRecorderSettings

        monkeypatch.setenv("SELFHEALING_RESILIENT_RECORDER_MEMORY_BUFFER_MAX_ENTRIES", "5000")
        monkeypatch.setenv("SELFHEALING_RESILIENT_RECORDER_MEMORY_BUFFER_FLUSH_INTERVAL", "60.0")

        settings = ResilientRecorderSettings()
        assert settings.memory_buffer_max_entries == 5000
        assert settings.memory_buffer_flush_interval == 60.0

    def test_memory_buffer_max_entries_validation_range(self):
        """memory_buffer_max_entries 범위 검증."""
        from selfhealing.settings.resilient_recorder import ResilientRecorderSettings

        # 최소 100
        with pytest.raises(ValidationError):
            ResilientRecorderSettings(memory_buffer_max_entries=50)

        # 최대 100000
        with pytest.raises(ValidationError):
            ResilientRecorderSettings(memory_buffer_max_entries=200000)

    def test_memory_buffer_flush_interval_validation_range(self):
        """memory_buffer_flush_interval 범위 검증."""
        from selfhealing.settings.resilient_recorder import ResilientRecorderSettings

        # 최소 5.0초
        with pytest.raises(ValidationError):
            ResilientRecorderSettings(memory_buffer_flush_interval=1.0)

        # 최대 300.0초
        with pytest.raises(ValidationError):
            ResilientRecorderSettings(memory_buffer_flush_interval=500.0)


class TestAuditSettingsExtensions:
    """AuditSettings 확장 필드 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.audit import reset_audit_settings
        reset_audit_settings()
        yield
        reset_audit_settings()

    def test_compliance_max_retention_days_default(self):
        """compliance_max_retention_days 기본값 365일 검증."""
        from selfhealing.settings.audit import AuditSettings

        settings = AuditSettings()
        assert settings.compliance_max_retention_days == 365

    def test_compliance_max_retention_days_env_override(self, monkeypatch):
        """compliance_max_retention_days 환경변수 오버라이드 검증."""
        from selfhealing.settings.audit import AuditSettings

        monkeypatch.setenv("SELFHEALING_AUDIT_COMPLIANCE_MAX_RETENTION_DAYS", "1825")

        settings = AuditSettings()
        assert settings.compliance_max_retention_days == 1825

    def test_compliance_max_retention_days_validation_range(self):
        """compliance_max_retention_days 범위 검증."""
        from selfhealing.settings.audit import AuditSettings

        # 최소 90일
        with pytest.raises(ValidationError):
            AuditSettings(compliance_max_retention_days=30)

        # 최대 2555일 (7년)
        with pytest.raises(ValidationError):
            AuditSettings(compliance_max_retention_days=3000)


class TestHashChainSettings:
    """HashChainSettings 신규 모듈 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.hash_chain import reset_hash_chain_settings
        reset_hash_chain_settings()
        yield
        reset_hash_chain_settings()

    def test_default_values(self):
        """기본값 검증."""
        from selfhealing.settings.hash_chain import HashChainSettings

        settings = HashChainSettings()

        assert settings.merge_swap_timeout_seconds == 300
        assert settings.merge_swap_blocking_timeout_seconds == 10.0
        assert settings.date_lock_timeout_seconds == 120
        assert settings.date_lock_blocking_timeout_seconds == 5.0
        assert settings.integrity_trail_max_redis_entries == 1000

    def test_env_override_merge_swap_timeout(self, monkeypatch):
        """merge_swap 타임아웃 환경변수 오버라이드 검증."""
        from selfhealing.settings.hash_chain import HashChainSettings

        monkeypatch.setenv("SELFHEALING_HASH_CHAIN_MERGE_SWAP_TIMEOUT_SECONDS", "600")
        monkeypatch.setenv("SELFHEALING_HASH_CHAIN_MERGE_SWAP_BLOCKING_TIMEOUT_SECONDS", "30.0")

        settings = HashChainSettings()
        assert settings.merge_swap_timeout_seconds == 600
        assert settings.merge_swap_blocking_timeout_seconds == 30.0

    def test_env_override_date_lock_timeout(self, monkeypatch):
        """date_lock 타임아웃 환경변수 오버라이드 검증."""
        from selfhealing.settings.hash_chain import HashChainSettings

        monkeypatch.setenv("SELFHEALING_HASH_CHAIN_DATE_LOCK_TIMEOUT_SECONDS", "180")
        monkeypatch.setenv("SELFHEALING_HASH_CHAIN_DATE_LOCK_BLOCKING_TIMEOUT_SECONDS", "10.0")

        settings = HashChainSettings()
        assert settings.date_lock_timeout_seconds == 180
        assert settings.date_lock_blocking_timeout_seconds == 10.0

    def test_env_override_integrity_trail(self, monkeypatch):
        """integrity_trail 환경변수 오버라이드 검증."""
        from selfhealing.settings.hash_chain import HashChainSettings

        monkeypatch.setenv("SELFHEALING_HASH_CHAIN_INTEGRITY_TRAIL_MAX_REDIS_ENTRIES", "5000")

        settings = HashChainSettings()
        assert settings.integrity_trail_max_redis_entries == 5000

    def test_validation_merge_swap_timeout_range(self):
        """merge_swap_timeout_seconds 범위 검증."""
        from selfhealing.settings.hash_chain import HashChainSettings

        # 최소 60초
        with pytest.raises(ValidationError):
            HashChainSettings(merge_swap_timeout_seconds=30)

        # 최대 600초
        with pytest.raises(ValidationError):
            HashChainSettings(merge_swap_timeout_seconds=1000)

    def test_validation_date_lock_timeout_range(self):
        """date_lock_timeout_seconds 범위 검증."""
        from selfhealing.settings.hash_chain import HashChainSettings

        # 최소 30초
        with pytest.raises(ValidationError):
            HashChainSettings(date_lock_timeout_seconds=10)

        # 최대 300초
        with pytest.raises(ValidationError):
            HashChainSettings(date_lock_timeout_seconds=500)

    def test_validation_integrity_trail_range(self):
        """integrity_trail_max_redis_entries 범위 검증."""
        from selfhealing.settings.hash_chain import HashChainSettings

        # 최소 100개
        with pytest.raises(ValidationError):
            HashChainSettings(integrity_trail_max_redis_entries=50)

        # 최대 10000개
        with pytest.raises(ValidationError):
            HashChainSettings(integrity_trail_max_redis_entries=20000)

    def test_singleton_pattern(self):
        """싱글톤 패턴 동작 검증."""
        from selfhealing.settings.hash_chain import get_hash_chain_settings

        settings1 = get_hash_chain_settings()
        settings2 = get_hash_chain_settings()

        assert settings1 is settings2

    def test_export_from_init(self):
        """settings/__init__.py에서 정상 export 되는지 검증."""
        from selfhealing.settings import (
            HashChainSettings,
            get_hash_chain_settings,
            reset_hash_chain_settings,
        )

        reset_hash_chain_settings()
        settings = get_hash_chain_settings()
        assert isinstance(settings, HashChainSettings)
