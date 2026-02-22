"""
Timeout/TTL Settings 단위 테스트.

HTTP 타임아웃, Redis/Cache TTL, Cleanup 보관 기간 등
운영 중 조정이 필요한 시간 관련 설정값 테스트.

테스트 대상 Settings:
- HttpClientSettings.default_timeout: 외부 API 호출 기본 타임아웃
- CeleryTaskSettings.inspect_timeout: Celery 워커 상태 확인 타임아웃
- CanarySettings.propagation_ttl: 크로스 클러스터 전파 요청 Redis TTL
- DailyReportSettings.cache_ttl: 일일 리포트 캐시 TTL
- GovernanceSettings.cache_ttl: 거버넌스 체크 캐시 TTL
- ChaosSettings.experiment_lock_ttl: Chaos 실험 분산 락 TTL
- CleanupSettings.recovery_max_age_hours: 복구 세션 보관 기간
- CleanupSettings.approval_cleanup_max_age_hours: 승인 요청 보관 기간

검증 항목:
- 기본값 정상 로드
- 환경 변수 오버라이드 동작
- 값 범위 검증 (min/max)
- 싱글톤 패턴 동작
"""

import pytest

# =============================================================================
# Test: HttpClientSettings
# =============================================================================


class TestHttpClientSettings:
    """HTTP 클라이언트 Settings 테스트."""

    def test_default_timeout(self):
        """기본 타임아웃 값 확인."""
        from selfhealing.settings.http_client import (
            HttpClientSettings,
            reset_http_client_settings,
        )

        reset_http_client_settings()
        settings = HttpClientSettings()

        assert settings.default_timeout == 30.0

    def test_timeout_env_override(self, monkeypatch):
        """환경 변수로 타임아웃 오버라이드."""
        from selfhealing.settings.http_client import (
            HttpClientSettings,
            reset_http_client_settings,
        )

        monkeypatch.setenv("SELFHEALING_HTTP_CLIENT_DEFAULT_TIMEOUT", "60.0")
        reset_http_client_settings()

        settings = HttpClientSettings()

        assert settings.default_timeout == 60.0

    def test_timeout_validation_min(self):
        """타임아웃 최소값 검증."""
        from selfhealing.settings.http_client import HttpClientSettings

        with pytest.raises(Exception):  # ValidationError
            HttpClientSettings(default_timeout=0.5)

    def test_timeout_validation_max(self):
        """타임아웃 최대값 검증."""
        from selfhealing.settings.http_client import HttpClientSettings

        with pytest.raises(Exception):  # ValidationError
            HttpClientSettings(default_timeout=500.0)

    def test_singleton_pattern(self):
        """싱글톤 패턴 동작 확인."""
        from selfhealing.settings.http_client import (
            get_http_client_settings,
            reset_http_client_settings,
        )

        reset_http_client_settings()

        s1 = get_http_client_settings()
        s2 = get_http_client_settings()

        assert s1 is s2


# =============================================================================
# Test: CeleryTaskSettings.inspect_timeout
# =============================================================================


class TestCeleryTaskInspectTimeout:
    """Celery inspect timeout 설정 테스트."""

    def test_default_inspect_timeout(self):
        """기본 inspect timeout 값 확인."""
        from selfhealing.settings.celery_task import (
            CeleryTaskSettings,
            reset_celery_task_settings,
        )

        reset_celery_task_settings()
        settings = CeleryTaskSettings()

        assert settings.inspect_timeout == 2

    def test_inspect_timeout_env_override(self, monkeypatch):
        """환경 변수로 inspect timeout 오버라이드."""
        from selfhealing.settings.celery_task import (
            CeleryTaskSettings,
            reset_celery_task_settings,
        )

        monkeypatch.setenv("SELFHEALING_CELERY_INSPECT_TIMEOUT", "5")
        reset_celery_task_settings()

        settings = CeleryTaskSettings()

        assert settings.inspect_timeout == 5

    def test_inspect_timeout_validation(self):
        """inspect timeout 범위 검증."""
        from selfhealing.settings.celery_task import CeleryTaskSettings

        # 최소값 미만
        with pytest.raises(Exception):
            CeleryTaskSettings(inspect_timeout=0)


# =============================================================================
# Test: CanarySettings.propagation_ttl
# =============================================================================


class TestCanaryPropagationTtl:
    """Canary 전파 요청 TTL 설정 테스트."""

    def test_default_propagation_ttl(self):
        """기본 propagation TTL 값 확인 (7일 = 604800초)."""
        from selfhealing.settings.canary import (
            CanarySettings,
            reset_canary_settings,
        )

        reset_canary_settings()
        settings = CanarySettings()

        assert settings.propagation_ttl == 604800  # 7일

    def test_propagation_ttl_env_override(self, monkeypatch):
        """환경 변수로 propagation TTL 오버라이드."""
        from selfhealing.settings.canary import (
            CanarySettings,
            reset_canary_settings,
        )

        monkeypatch.setenv("SELFHEALING_CANARY_PROPAGATION_TTL", "86400")  # 1일
        reset_canary_settings()

        settings = CanarySettings()

        assert settings.propagation_ttl == 86400

    def test_propagation_ttl_validation(self):
        """propagation TTL 범위 검증."""
        from selfhealing.settings.canary import CanarySettings

        # 최소값 미만 (1시간 미만)
        with pytest.raises(Exception):
            CanarySettings(propagation_ttl=1800)


# =============================================================================
# Test: DailyReportSettings.cache_ttl
# =============================================================================


class TestDailyReportCacheTtl:
    """일일 리포트 캐시 TTL 설정 테스트."""

    def test_default_cache_ttl(self):
        """기본 cache TTL 값 확인 (2일 = 172800초)."""
        from selfhealing.settings.daily_report import (
            DailyReportSettings,
            reset_daily_report_settings,
        )

        reset_daily_report_settings()
        settings = DailyReportSettings()

        assert settings.cache_ttl == 172800  # 2일

    def test_cache_ttl_env_override(self, monkeypatch):
        """환경 변수로 cache TTL 오버라이드."""
        from selfhealing.settings.daily_report import (
            DailyReportSettings,
            reset_daily_report_settings,
        )

        monkeypatch.setenv("SELFHEALING_DAILY_REPORT_CACHE_TTL", "86400")  # 1일
        reset_daily_report_settings()

        settings = DailyReportSettings()

        assert settings.cache_ttl == 86400


# =============================================================================
# Test: GovernanceSettings.cache_ttl
# =============================================================================


class TestGovernanceCacheTtl:
    """거버넌스 체크 캐시 TTL 설정 테스트."""

    def test_default_cache_ttl(self):
        """기본 cache TTL 값 확인 (30초)."""
        from selfhealing.settings.governance import (
            GovernanceSettings,
            reset_governance_settings,
        )

        reset_governance_settings()
        settings = GovernanceSettings()

        assert settings.cache_ttl == 30.0

    def test_cache_ttl_env_override(self, monkeypatch):
        """환경 변수로 cache TTL 오버라이드."""
        from selfhealing.settings.governance import (
            GovernanceSettings,
            reset_governance_settings,
        )

        monkeypatch.setenv("SELFHEALING_GOVERNANCE_CACHE_TTL", "60.0")
        reset_governance_settings()

        settings = GovernanceSettings()

        assert settings.cache_ttl == 60.0


# =============================================================================
# Test: ChaosSettings.experiment_lock_ttl
# =============================================================================


class TestChaosExperimentLockTtl:
    """Chaos 실험 분산 락 TTL 설정 테스트."""

    def test_default_experiment_lock_ttl(self):
        """기본 experiment lock TTL 값 확인 (120초)."""
        from selfhealing.settings.chaos import (
            ChaosSettings,
            reset_chaos_settings,
        )

        reset_chaos_settings()
        settings = ChaosSettings()

        assert settings.experiment_lock_ttl == 120

    def test_experiment_lock_ttl_env_override(self, monkeypatch):
        """환경 변수로 experiment lock TTL 오버라이드."""
        from selfhealing.settings.chaos import (
            ChaosSettings,
            reset_chaos_settings,
        )

        monkeypatch.setenv("SELFHEALING_CHAOS_EXPERIMENT_LOCK_TTL", "300")
        reset_chaos_settings()

        settings = ChaosSettings()

        assert settings.experiment_lock_ttl == 300


# =============================================================================
# Test: CleanupSettings - recovery_max_age_hours, approval_cleanup_max_age_hours
# =============================================================================


class TestCleanupRecoveryMaxAgeHours:
    """복구 세션 정리 기간 설정 테스트."""

    def test_default_recovery_max_age_hours(self):
        """기본 recovery max age hours 값 확인 (168시간 = 7일)."""
        from selfhealing.settings.cleanup import (
            CleanupSettings,
            reset_cleanup_settings,
        )

        reset_cleanup_settings()
        settings = CleanupSettings()

        assert settings.recovery_max_age_hours == 168

    def test_recovery_max_age_hours_env_override(self, monkeypatch):
        """환경 변수로 recovery max age hours 오버라이드."""
        from selfhealing.settings.cleanup import (
            CleanupSettings,
            reset_cleanup_settings,
        )

        monkeypatch.setenv("SELFHEALING_CLEANUP_RECOVERY_MAX_AGE_HOURS", "336")  # 14일
        reset_cleanup_settings()

        settings = CleanupSettings()

        assert settings.recovery_max_age_hours == 336


class TestCleanupApprovalMaxAgeHours:
    """승인 요청 정리 기간 설정 테스트."""

    def test_default_approval_cleanup_max_age_hours(self):
        """기본 approval cleanup max age hours 값 확인 (24시간)."""
        from selfhealing.settings.cleanup import (
            CleanupSettings,
            reset_cleanup_settings,
        )

        reset_cleanup_settings()
        settings = CleanupSettings()

        assert settings.approval_cleanup_max_age_hours == 24

    def test_approval_cleanup_max_age_hours_env_override(self, monkeypatch):
        """환경 변수로 approval cleanup max age hours 오버라이드."""
        from selfhealing.settings.cleanup import (
            CleanupSettings,
            reset_cleanup_settings,
        )

        monkeypatch.setenv("SELFHEALING_CLEANUP_APPROVAL_CLEANUP_MAX_AGE_HOURS", "48")
        reset_cleanup_settings()

        settings = CleanupSettings()

        assert settings.approval_cleanup_max_age_hours == 48


# =============================================================================
# Integration Tests: 코드에서 Settings 사용 확인
# =============================================================================


class TestHttpClientUsesSettings:
    """HTTP 클라이언트가 Settings를 사용하는지 확인."""

    def test_http_client_uses_default_timeout_from_settings(self):
        """SelfHealingHttpClient가 Settings에서 기본 타임아웃을 가져오는지 확인."""
        from selfhealing.services.http_client import SelfHealingHttpClient
        from selfhealing.settings.http_client import reset_http_client_settings

        reset_http_client_settings()

        client = SelfHealingHttpClient()

        assert client.default_timeout == 30.0

    def test_http_client_uses_custom_timeout_from_settings(self, monkeypatch):
        """SelfHealingHttpClient가 환경변수로 설정된 타임아웃을 사용하는지 확인."""
        from selfhealing.services.http_client import SelfHealingHttpClient
        from selfhealing.settings.http_client import reset_http_client_settings

        monkeypatch.setenv("SELFHEALING_HTTP_CLIENT_DEFAULT_TIMEOUT", "45.0")
        reset_http_client_settings()

        client = SelfHealingHttpClient()

        assert client.default_timeout == 45.0

    def test_http_client_explicit_timeout_overrides_settings(self):
        """명시적 timeout 인자가 Settings보다 우선하는지 확인."""
        from selfhealing.services.http_client import SelfHealingHttpClient
        from selfhealing.settings.http_client import reset_http_client_settings

        reset_http_client_settings()

        client = SelfHealingHttpClient(timeout=90.0)

        assert client.default_timeout == 90.0


class TestGovernanceChecksUsesSettings:
    """거버넌스 체크가 Settings를 사용하는지 확인."""

    def test_ttl_cache_uses_settings(self, monkeypatch):
        """TTLCache가 Settings에서 TTL을 가져오는지 확인."""
        from selfhealing.settings.governance import reset_governance_settings

        monkeypatch.setenv("SELFHEALING_GOVERNANCE_CACHE_TTL", "60.0")
        reset_governance_settings()

        # 모듈 재로드하여 새 설정 적용 확인
        from selfhealing.services import governance_checks

        new_cache = governance_checks._create_governance_cache()

        assert new_cache._default_ttl == 60.0
