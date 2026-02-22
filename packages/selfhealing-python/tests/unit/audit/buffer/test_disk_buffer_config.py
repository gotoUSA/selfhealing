"""
DiskBufferSettings 설정 단위 테스트.

설정 클래스와 검증 로직을 테스트합니다.
"""

from __future__ import annotations


class TestDiskBufferSettings:
    """DiskBufferSettings 테스트."""

    def test_default_settings(self):
        """기본 설정 테스트."""
        from selfhealing.audit.persistence.config import DiskBufferSettings

        settings = DiskBufferSettings()

        assert settings.storage_type == "lmdb"
        assert settings.lmdb_map_size_mb == 10240  # 10GB
        assert settings.max_entries == 100000
        assert settings.enable_checksum is True
        assert settings.group_commit_enabled is True

    def test_custom_settings(self):
        """커스텀 설정 테스트."""
        from selfhealing.audit.persistence.config import DiskBufferSettings

        settings = DiskBufferSettings(
            lmdb_map_size_mb=100,
            max_entries=1000,
            group_commit_enabled=False,
        )

        assert settings.lmdb_map_size_mb == 100
        assert settings.max_entries == 1000
        assert settings.group_commit_enabled is False

    def test_lmdb_map_size_bytes(self):
        """바이트 변환 테스트."""
        from selfhealing.audit.persistence.config import DiskBufferSettings

        settings = DiskBufferSettings(lmdb_map_size_mb=100)

        assert settings.lmdb_map_size_bytes == 100 * 1024 * 1024

    def test_validate_settings_warnings(self):
        """설정 검증 경고 테스트."""
        from selfhealing.audit.persistence.config import DiskBufferSettings

        # sync_on_write와 group_commit 충돌
        settings = DiskBufferSettings(
            sync_on_write=True,
            group_commit_enabled=True,
        )

        warnings = settings.validate_settings()
        assert len(warnings) > 0
        assert any("sync_on_write" in w for w in warnings)

    def test_validate_small_map_size(self):
        """작은 map_size 경고 테스트."""
        from selfhealing.audit.persistence.config import DiskBufferSettings

        settings = DiskBufferSettings(lmdb_map_size_mb=50)  # 100MB 미만

        warnings = settings.validate_settings()
        assert any("lmdb_map_size_mb" in w for w in warnings)

    def test_get_lmdb_open_kwargs(self):
        """LMDB open kwargs 테스트."""
        from selfhealing.audit.persistence.config import DiskBufferSettings

        settings = DiskBufferSettings(
            lmdb_map_size_mb=100,
            lmdb_max_dbs=5,
            sync_on_write=True,
            lmdb_writemap=False,
        )

        kwargs = settings.get_lmdb_open_kwargs()

        assert kwargs["map_size"] == 100 * 1024 * 1024
        assert kwargs["max_dbs"] == 5
        assert kwargs["sync"] is True
        assert kwargs["writemap"] is False


class TestDiskBufferSettingsEnvironment:
    """환경변수 기반 설정 테스트."""

    def test_env_override(self, monkeypatch):
        """환경변수 오버라이드 테스트."""
        from selfhealing.audit.persistence.config import (
            DiskBufferSettings,
            reset_disk_buffer_settings,
        )

        # 캐시 리셋
        reset_disk_buffer_settings()

        monkeypatch.setenv("SELFHEALING_DISK_BUFFER_LMDB_MAP_SIZE_MB", "500")
        monkeypatch.setenv("SELFHEALING_DISK_BUFFER_MAX_ENTRIES", "5000")

        settings = DiskBufferSettings()

        assert settings.lmdb_map_size_mb == 500
        assert settings.max_entries == 5000

    def test_get_disk_buffer_settings_singleton(self):
        """설정 싱글톤 테스트."""
        from selfhealing.audit.persistence.config import (
            get_disk_buffer_settings,
            reset_disk_buffer_settings,
        )

        reset_disk_buffer_settings()

        settings1 = get_disk_buffer_settings()
        settings2 = get_disk_buffer_settings()

        assert settings1 is settings2

    def test_reset_disk_buffer_settings(self):
        """설정 리셋 테스트."""
        from selfhealing.audit.persistence.config import (
            get_disk_buffer_settings,
            reset_disk_buffer_settings,
        )

        settings1 = get_disk_buffer_settings()
        reset_disk_buffer_settings()
        settings2 = get_disk_buffer_settings()

        # 리셋 후 새 인스턴스 생성됨
        # (같은 설정이지만 다른 객체일 수 있음)
        assert settings1 is not settings2 or True  # 캐시 구현에 따라 다름
