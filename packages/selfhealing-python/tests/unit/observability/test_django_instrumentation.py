"""
DjangoInstrumentor 통합 단위 테스트.

대상: selfhealing.observability.instrument_django()
"""

import os
from unittest.mock import MagicMock, patch

from selfhealing.observability import (
    is_django_instrumented,
    reset_opentelemetry,
)
from selfhealing.settings.observability import reset_otel_settings


class TestInstrumentDjangoContract:
    """instrument_django() 계약 검증."""

    def setup_method(self):
        reset_opentelemetry()
        reset_otel_settings()

    def teardown_method(self):
        reset_opentelemetry()
        reset_otel_settings()

    def test_django_instrumented_flag_initial_false(self):
        """초기 상태에서 Django는 instrumented 되지 않은 상태이다."""
        assert is_django_instrumented() is False


class TestInstrumentDjangoBehavior:
    """instrument_django() 동작 검증."""

    def setup_method(self):
        reset_opentelemetry()
        reset_otel_settings()

    def teardown_method(self):
        reset_opentelemetry()
        reset_otel_settings()

    def test_returns_false_when_otel_disabled(self):
        """OTel이 비활성화되면 False를 반환한다."""
        from selfhealing.observability import instrument_django

        with patch("selfhealing.observability.is_otel_enabled", return_value=False):
            result = instrument_django()
            assert result is False

    def test_returns_false_when_django_instrument_disabled(self):
        """OTEL_DJANGO_INSTRUMENT_ENABLED=false이면 False를 반환한다."""
        from selfhealing.observability import instrument_django

        with patch("selfhealing.observability.is_otel_enabled", return_value=True):
            mock_settings = MagicMock()
            mock_settings.django_instrument_enabled = False
            with patch(
                "selfhealing.settings.observability.get_otel_settings",
                return_value=mock_settings,
            ):
                result = instrument_django()
                assert result is False

    def test_returns_false_when_django_instrumentor_not_installed(self):
        """opentelemetry-instrumentation-django 미설치 시 False를 반환한다."""
        from selfhealing.observability import instrument_django

        with patch("selfhealing.observability.is_otel_enabled", return_value=True):
            with patch.dict("sys.modules", {"opentelemetry.instrumentation.django": None}):
                result = instrument_django()
                assert result is False

    def test_idempotent_returns_true_on_second_call(self):
        """이미 instrumented 상태에서 True를 반환한다."""
        # 강제 instrumented 상태로 설정
        import selfhealing.observability as obs_mod
        from selfhealing.observability import instrument_django

        obs_mod._django_instrumented = True
        try:
            result = instrument_django()
            assert result is True
        finally:
            obs_mod._django_instrumented = False

    def test_sets_excluded_urls_env_var(self):
        """instrument_django()가 OTEL_PYTHON_DJANGO_EXCLUDED_URLS 환경변수를 설정한다."""
        from selfhealing.observability import instrument_django

        mock_settings = MagicMock()
        mock_settings.django_instrument_enabled = True
        mock_settings.get_excluded_urls_list.return_value = ["/health", "/metrics"]

        mock_instrumentor = MagicMock()

        with (
            patch("selfhealing.observability.is_otel_enabled", return_value=True),
            patch(
                "selfhealing.settings.observability.get_otel_settings",
                return_value=mock_settings,
            ),
            patch.dict(os.environ, {}, clear=False),
        ):
            # DjangoInstrumentor mock
            mock_module = MagicMock()
            mock_module.DjangoInstrumentor.return_value = mock_instrumentor
            with patch.dict(
                "sys.modules",
                {"opentelemetry.instrumentation.django": mock_module},
            ):
                instrument_django()
                assert os.environ.get("OTEL_PYTHON_DJANGO_EXCLUDED_URLS") == "/health,/metrics"

    def test_reset_clears_django_instrumented_flag(self):
        """reset_opentelemetry()가 _django_instrumented를 False로 리셋한다."""
        import selfhealing.observability as obs_mod

        obs_mod._django_instrumented = True
        reset_opentelemetry()
        assert obs_mod._django_instrumented is False
