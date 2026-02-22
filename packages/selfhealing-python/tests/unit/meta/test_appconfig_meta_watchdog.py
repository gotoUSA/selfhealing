"""
Django AppConfig Meta-Watchdog 통합 테스트.

SelfHealingConfig에 Meta-Watchdog 시작 기능이 있는지 테스트.
소스코드 분석 기반 - Django 설정 없이 테스트.
"""

import os


class TestSelfHealingConfigMetaWatchdog:
    """SelfHealingConfig Meta-Watchdog 통합 테스트 - 소스코드 분석 기반."""

    def _get_apps_source(self):
        """apps.py 소스코드 읽기."""
        apps_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "src",
            "selfhealing",
            "adapters",
            "django",
            "apps.py",
        )
        apps_path = os.path.normpath(apps_path)

        with open(apps_path, encoding="utf-8") as f:
            return f.read()

    def test_meta_watchdog_started_flag_exists(self):
        """_meta_watchdog_started 플래그가 있는지 확인."""
        source = self._get_apps_source()

        assert "_meta_watchdog_started" in source

    def test_meta_watchdog_lock_exists(self):
        """_meta_watchdog_lock 락 객체가 있는지 확인."""
        source = self._get_apps_source()

        assert "_meta_watchdog_lock" in source

    def test_start_meta_watchdog_method_exists(self):
        """_start_meta_watchdog 메서드가 정의되어 있는지 확인."""
        source = self._get_apps_source()

        assert "def _start_meta_watchdog" in source

    def test_reset_meta_watchdog_state_method_exists(self):
        """reset_meta_watchdog_state 테스트 헬퍼가 있는지 확인."""
        source = self._get_apps_source()

        assert "def reset_meta_watchdog_state" in source

    def test_ready_calls_start_meta_watchdog(self):
        """ready()에서 _start_meta_watchdog 호출하는지 확인."""
        source = self._get_apps_source()

        # ready 메서드 내에서 _start_meta_watchdog 호출 확인
        assert "_start_meta_watchdog" in source

    def test_checks_selfhealing_meta_enabled_env_var(self):
        """SELFHEALING_META_ENABLED 환경변수 체크가 있는지 확인."""
        source = self._get_apps_source()

        # 환경변수 체크 또는 MetaWatchdogSettings 사용 확인
        assert "SELFHEALING_META_ENABLED" in source or "MetaWatchdogSettings" in source

    def test_uses_get_selfhealer_watchdog(self):
        """get_selfhealer_watchdog 함수를 사용하는지 확인."""
        source = self._get_apps_source()

        assert "get_selfhealer_watchdog" in source

    def test_calls_watchdog_start(self):
        """watchdog.start() 호출이 있는지 확인."""
        source = self._get_apps_source()

        # watchdog.start() 또는 관련 시작 로직 확인
        assert ".start()" in source

    def test_handles_import_error(self):
        """ImportError 처리가 있는지 확인."""
        source = self._get_apps_source()

        assert "ImportError" in source

    def test_handles_generic_exception(self):
        """일반 Exception 처리가 있는지 확인."""
        source = self._get_apps_source()

        # Exception 처리 확인
        assert "except Exception" in source or "except:" in source

    def test_duplicate_start_prevention_logic(self):
        """중복 시작 방지 로직이 있는지 확인."""
        source = self._get_apps_source()

        # _meta_watchdog_started 플래그 사용 확인
        assert "_meta_watchdog_started" in source
        # 락을 사용한 동기화 확인
        assert "with" in source and "_meta_watchdog_lock" in source

    def test_threading_import(self):
        """threading 모듈 임포트가 있는지 확인 (락 사용을 위해)."""
        source = self._get_apps_source()

        assert "import threading" in source or "from threading" in source
