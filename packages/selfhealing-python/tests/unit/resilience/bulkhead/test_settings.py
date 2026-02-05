"""
BulkheadSettings 단위 테스트.

Pydantic v2 기반 격벽 설정의 동작을 검증합니다:
- 기본값 검증
- 환경 변수 로딩
- 유효성 검사
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from selfhealing.settings.bulkhead import (
    BulkheadSettings,
    get_bulkhead_settings,
    reset_bulkhead_settings,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    """각 테스트 전후로 싱글톤 초기화."""
    reset_bulkhead_settings()
    yield
    reset_bulkhead_settings()


class TestBulkheadSettingsDefaults:
    """기본값 테스트."""

    def test_default_enabled(self):
        """기본 활성화 상태."""
        settings = BulkheadSettings()
        assert settings.enabled is True

    def test_default_database_max_concurrent(self):
        """DATABASE 기본 동시 실행 수."""
        settings = BulkheadSettings()
        assert settings.database_max_concurrent == 10

    def test_default_cache_max_concurrent(self):
        """CACHE 기본 동시 실행 수."""
        settings = BulkheadSettings()
        assert settings.cache_max_concurrent == 20

    def test_default_external_api_settings(self):
        """EXTERNAL_API 기본 설정."""
        settings = BulkheadSettings()
        assert settings.external_api_max_workers == 5
        assert settings.external_api_queue_size == 10

    def test_default_message_queue_max_concurrent(self):
        """MESSAGE_QUEUE 기본 동시 실행 수."""
        settings = BulkheadSettings()
        assert settings.message_queue_max_concurrent == 15

    def test_default_max_concurrent(self):
        """커스텀 도메인 기본 동시 실행 수."""
        settings = BulkheadSettings()
        assert settings.default_max_concurrent == 10

    def test_default_acquire_timeout(self):
        """기본 획득 타임아웃."""
        settings = BulkheadSettings()
        assert settings.default_acquire_timeout == 5.0


class TestBulkheadSettingsEnvironmentVariables:
    """환경 변수 로딩 테스트."""

    def test_load_from_env_database_max_concurrent(self):
        """환경 변수에서 DATABASE 동시 실행 수 로딩."""
        with patch.dict(os.environ, {"SELFHEALING_BULKHEAD_DATABASE_MAX_CONCURRENT": "25"}):
            settings = BulkheadSettings()
            assert settings.database_max_concurrent == 25

    def test_load_from_env_cache_max_concurrent(self):
        """환경 변수에서 CACHE 동시 실행 수 로딩."""
        with patch.dict(os.environ, {"SELFHEALING_BULKHEAD_CACHE_MAX_CONCURRENT": "50"}):
            settings = BulkheadSettings()
            assert settings.cache_max_concurrent == 50

    def test_load_from_env_enabled(self):
        """환경 변수에서 활성화 상태 로딩."""
        with patch.dict(os.environ, {"SELFHEALING_BULKHEAD_ENABLED": "false"}):
            settings = BulkheadSettings()
            assert settings.enabled is False

    def test_load_from_env_external_api_workers(self):
        """환경 변수에서 EXTERNAL_API 워커 수 로딩."""
        with patch.dict(os.environ, {"SELFHEALING_BULKHEAD_EXTERNAL_API_MAX_WORKERS": "15"}):
            settings = BulkheadSettings()
            assert settings.external_api_max_workers == 15


class TestBulkheadSettingsValidation:
    """유효성 검사 테스트."""

    def test_database_max_concurrent_min_value(self):
        """DATABASE 동시 실행 수 최소값 검증."""
        with pytest.raises(ValueError):
            BulkheadSettings(database_max_concurrent=0)

    def test_database_max_concurrent_max_value(self):
        """DATABASE 동시 실행 수 최대값 검증."""
        with pytest.raises(ValueError):
            BulkheadSettings(database_max_concurrent=101)

    def test_cache_max_concurrent_max_value(self):
        """CACHE 동시 실행 수 최대값 검증."""
        with pytest.raises(ValueError):
            BulkheadSettings(cache_max_concurrent=201)

    def test_external_api_max_workers_min_value(self):
        """EXTERNAL_API 워커 수 최소값 검증."""
        with pytest.raises(ValueError):
            BulkheadSettings(external_api_max_workers=0)

    def test_acquire_timeout_min_value(self):
        """획득 타임아웃 최소값 검증."""
        # 0.0은 허용됨
        settings = BulkheadSettings(default_acquire_timeout=0.0)
        assert settings.default_acquire_timeout == 0.0

    def test_acquire_timeout_max_value(self):
        """획득 타임아웃 최대값 검증."""
        with pytest.raises(ValueError):
            BulkheadSettings(default_acquire_timeout=61.0)


class TestBulkheadSettingsMultiInstance:
    """멀티 인스턴스 설정 테스트."""

    def test_default_database_aliases(self):
        """기본 DB alias 설정."""
        settings = BulkheadSettings()
        assert "default" in settings.database_aliases
        assert "replica" in settings.database_aliases
        assert settings.database_aliases["default"] == 10
        assert settings.database_aliases["replica"] == 15

    def test_default_cache_instances(self):
        """기본 캐시 인스턴스 설정."""
        settings = BulkheadSettings()
        assert "default" in settings.cache_instances
        assert "session" in settings.cache_instances
        assert settings.cache_instances["default"] == 20
        assert settings.cache_instances["session"] == 10

    def test_custom_database_aliases(self):
        """커스텀 DB alias 설정."""
        settings = BulkheadSettings(database_aliases={"default": 20, "analytics": 30})
        assert settings.database_aliases["default"] == 20
        assert settings.database_aliases["analytics"] == 30


class TestBulkheadSettingsSingleton:
    """싱글톤 테스트."""

    def test_get_bulkhead_settings_returns_same_instance(self):
        """싱글톤 인스턴스 반환."""
        settings1 = get_bulkhead_settings()
        settings2 = get_bulkhead_settings()
        assert settings1 is settings2

    def test_reset_clears_singleton(self):
        """reset으로 싱글톤 초기화."""
        settings1 = get_bulkhead_settings()
        reset_bulkhead_settings()
        settings2 = get_bulkhead_settings()
        assert settings1 is not settings2
