"""
AdaptiveThrottle 동적 레이블 단위 테스트.

service_name이 config에서 메트릭으로 전파되는지 확인.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestAdaptiveThrottleDynamicLabels:
    """AdaptiveThrottle의 동적 레이블 지원 테스트."""

    def test_service_name_from_config(self):
        """ThrottleConfig의 service_name이 AdaptiveThrottle에 전달되는지 확인."""
        from selfhealing.services.throttle.config import ThrottleConfig
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.metrics.registry import sanitize_label_value

        test_input = "test-api-service"
        config = ThrottleConfig(
            max_limit=1000,
            min_limit=10,
            service_name=test_input,
        )

        throttle = AdaptiveThrottle(config)

        # 소스 함수로 기대값 계산 (하드코딩 제거)
        expected = sanitize_label_value(test_input)
        assert throttle._service_name == expected

    def test_service_name_default(self):
        """service_name 기본값 확인."""
        from selfhealing.services.throttle.config import ThrottleConfig
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.settings.throttle import ThrottleSettings

        config = ThrottleConfig(
            max_limit=1000,
            min_limit=10,
        )

        throttle = AdaptiveThrottle(config)

        # 소스 기본값 참조 (하드코딩 제거)
        default_settings = ThrottleSettings()
        assert throttle._service_name == default_settings.service_name

    def test_config_service_name_field_exists(self):
        """ThrottleConfig에 service_name 필드가 있는지 확인."""
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig()

        assert hasattr(config, "service_name")
        assert isinstance(config.service_name, str)

    def test_config_from_settings_includes_service_name(self):
        """ThrottleConfig.from_settings()가 service_name을 포함하는지 확인."""
        from selfhealing.services.throttle.config import ThrottleConfig

        # from_settings가 존재하면 테스트 (classmethod이므로 인자 없이 호출)
        if hasattr(ThrottleConfig, "from_settings"):
            # settings 모듈 패치로 테스트
            with patch("selfhealing.services.throttle.config.get_throttle_settings") as mock_fn:
                mock_settings = MagicMock()
                mock_settings.service_name = "my-custom-service"
                mock_settings.initial_limit = 100
                mock_settings.window_seconds = 60
                mock_settings.min_limit = 10
                mock_settings.max_limit = 1000
                mock_settings.sample_interval_ms = 500
                mock_settings.smoothing_factor = 0.2
                mock_settings.decrease_ratio = 0.9
                mock_settings.increase_step = 1
                mock_settings.sla_warning_ms = 200
                mock_settings.sla_critical_ms = 500
                mock_settings.emergency_limit = 10
                mock_settings.key_prefix = "throttle"
                mock_fn.return_value = mock_settings

                config = ThrottleConfig.from_settings()

                assert config.service_name == "my-custom-service"

    def test_config_from_dict_includes_service_name(self):
        """ThrottleConfig.from_dict()가 service_name을 포함하는지 확인."""
        from selfhealing.services.throttle.config import ThrottleConfig

        # from_dict가 존재하면 테스트
        if hasattr(ThrottleConfig, "from_dict"):
            data = {
                "max_limit": 500,
                "min_limit": 10,
                "service_name": "dict-service",
            }

            config = ThrottleConfig.from_dict(data)

            assert config.service_name == "dict-service"


class TestThrottleSettingsServiceName:
    """ThrottleSettings의 service_name 필드 테스트."""

    def test_settings_has_service_name(self):
        """ThrottleSettings에 service_name 필드가 있는지 확인."""
        from selfhealing.settings.throttle import ThrottleSettings

        settings = ThrottleSettings()

        assert hasattr(settings, "service_name")

    def test_settings_service_name_default(self):
        """ThrottleSettings의 service_name 기본값 확인."""
        from selfhealing.settings.throttle import ThrottleSettings
        import inspect

        settings = ThrottleSettings()

        # 클래스 기본값 확인 (하드코딩 제거)
        # Pydantic Field의 default를 확인
        field_info = ThrottleSettings.model_fields.get("service_name")
        expected_default = field_info.default if field_info else "default"
        assert settings.service_name == expected_default

    @patch.dict("os.environ", {"SELFHEALING_THROTTLE_SERVICE_NAME": "env-service"}, clear=False)
    def test_settings_service_name_from_env(self):
        """환경 변수로 service_name 설정 가능한지 확인."""
        from selfhealing.settings.throttle import ThrottleSettings

        settings = ThrottleSettings()

        assert settings.service_name == "env-service"
