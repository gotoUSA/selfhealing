from django.apps import AppConfig


class ShoppingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shopping"

    def ready(self):
        """
        앱이 준비되면 시그널 등록

        소셜 로그인 시그널을 활성화하여
        자동 이메일 인증 처리가 작동하도록 합니다.

        또한 selfhealing 패키지를 Django 환경에 맞게 설정합니다.
        """
        import shopping.signals  # noqa

        # Configure selfhealing package for Django
        self._configure_selfhealing()

    def _configure_selfhealing(self):
        """
        Configure selfhealing package to use Django adapters.

        The selfhealing package provides all Django adapters internally.
        Shopping app only needs to set 'django' as the default adapter type.
        """
        try:
            from selfhealing.factory import ProviderRegistry

            # Simply set Django as the default adapter type
            # The selfhealing package already has complete Django adapters
            ProviderRegistry._default_repo = "django"

        except ImportError as e:
            # selfhealing package not installed - that's OK for standalone shopping
            pass
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to configure selfhealing: {e}")
