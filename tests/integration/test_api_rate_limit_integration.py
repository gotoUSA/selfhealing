"""
API Rate Limit Settings 통합 테스트.

Django 환경에서 api/django/rate_limit.py와 ApiRateLimitSettings 연동 검증.
"""

import pytest
from django.test import override_settings


@pytest.mark.django_db
class TestApiRateLimitSettingsIntegration:
    """api/django/rate_limit.py와 ApiRateLimitSettings 통합 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """각 테스트 전후로 모든 싱글톤 초기화."""
        from selfhealing.settings.api_rate_limit import reset_api_rate_limit_settings
        reset_api_rate_limit_settings()
        yield
        reset_api_rate_limit_settings()

    def test_get_rate_limit_config_uses_settings(self, monkeypatch):
        """_get_setting 함수가 ApiRateLimitSettings의 환경변수 오버라이드를 사용하는지 검증."""
        from selfhealing.settings.api_rate_limit import reset_api_rate_limit_settings
        
        # 환경변수로 설정 변경
        monkeypatch.setenv("SELFHEALING_API_RATE_DEFAULT_LIMIT", "250")
        monkeypatch.setenv("SELFHEALING_API_RATE_EMERGENCY_LIMIT", "25")
        
        reset_api_rate_limit_settings()
        
        # _get_setting 헬퍼 함수 테스트 (settings에서 값을 가져오는 핵심 함수)
        from selfhealing.api.django.rate_limit import _get_setting, _FALLBACK_DEFAULT_RATE_LIMIT
        
        # Settings에서 환경변수 오버라이드된 값을 가져오는지 확인
        default_limit = _get_setting("default_limit", _FALLBACK_DEFAULT_RATE_LIMIT)
        emergency_limit = _get_setting("emergency_limit", 10)
        
        assert default_limit == 250
        assert emergency_limit == 25

    def test_local_memory_rate_limiter_uses_settings(self, monkeypatch):
        """LocalMemoryRateLimiter가 settings 값을 사용하는지 검증."""
        from selfhealing.api.django.rate_limit import LocalMemoryRateLimiter
        from selfhealing.settings.api_rate_limit import reset_api_rate_limit_settings
        
        # 환경변수로 설정 변경
        monkeypatch.setenv("SELFHEALING_API_RATE_EMERGENCY_LIMIT", "15")
        monkeypatch.setenv("SELFHEALING_API_RATE_EMERGENCY_WINDOW_SECONDS", "90")
        monkeypatch.setenv("SELFHEALING_API_RATE_LOCAL_CLEANUP_INTERVAL", "120")
        
        reset_api_rate_limit_settings()
        
        # 기본 생성자 호출 (None 인자)
        limiter = LocalMemoryRateLimiter()
        
        assert limiter.max_requests == 15
        assert limiter.window_seconds == 90
        assert limiter._cleanup_interval == 120

    def test_redis_health_checker_uses_settings(self, monkeypatch):
        """RedisHealthChecker가 settings 값을 사용하는지 검증."""
        from selfhealing.api.django.rate_limit import RedisHealthChecker
        from selfhealing.settings.api_rate_limit import reset_api_rate_limit_settings
        
        # 환경변수로 설정 변경
        monkeypatch.setenv("SELFHEALING_API_RATE_REDIS_PING_INTERVAL", "15")
        monkeypatch.setenv("SELFHEALING_API_RATE_REDIS_FAILURE_THRESHOLD", "7")
        monkeypatch.setenv("SELFHEALING_API_RATE_REDIS_RECOVERY_JITTER_MAX", "30")
        
        reset_api_rate_limit_settings()
        
        checker = RedisHealthChecker()
        
        assert checker.ping_interval == 15
        assert checker.failure_threshold == 7
        assert checker.recovery_jitter_max == 30

    def test_backward_compatibility_constants(self):
        """하위 호환성을 위한 모듈 레벨 상수가 존재하는지 검증."""
        from selfhealing.api.django.rate_limit import (
            DEFAULT_RATE_LIMIT,
            DEFAULT_WINDOW_SECONDS,
            EMERGENCY_RATE_LIMIT,
            EMERGENCY_WINDOW_SECONDS,
            CONTROL_API_PATH_PREFIX,
        )
        
        # 상수가 정의되어 있는지 확인 (하위 호환성)
        assert DEFAULT_RATE_LIMIT == 100
        assert DEFAULT_WINDOW_SECONDS == 60
        assert EMERGENCY_RATE_LIMIT == 10
        assert EMERGENCY_WINDOW_SECONDS == 60
        assert CONTROL_API_PATH_PREFIX == "/api/self-healing/"

    def test_local_limiter_is_allowed_respects_settings(self, monkeypatch):
        """LocalMemoryRateLimiter.is_allowed가 settings 제한을 존중하는지 검증."""
        from selfhealing.api.django.rate_limit import LocalMemoryRateLimiter
        from selfhealing.settings.api_rate_limit import reset_api_rate_limit_settings
        
        # 매우 낮은 제한 설정
        monkeypatch.setenv("SELFHEALING_API_RATE_EMERGENCY_LIMIT", "2")
        reset_api_rate_limit_settings()
        
        limiter = LocalMemoryRateLimiter()
        
        # 첫 번째 요청 - 허용
        allowed, remaining = limiter.is_allowed("test_key")
        assert allowed is True
        assert remaining == 1
        
        # 두 번째 요청 - 허용
        allowed, remaining = limiter.is_allowed("test_key")
        assert allowed is True
        assert remaining == 0
        
        # 세 번째 요청 - 거부 (제한 초과)
        allowed, remaining = limiter.is_allowed("test_key")
        assert allowed is False
        assert remaining == 0
