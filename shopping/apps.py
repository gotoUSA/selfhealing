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
        Configure selfhealing package.

        Redis is now the default adapter (with ResilientStorageBackend fallback).
        No explicit configuration needed - ProviderRegistry handles everything.
        """
        # Redis가 기본값이므로 별도 설정 불필요
        # ProviderRegistry는 자동으로 "redis"를 사용하며,
        # ResilientStorageBackend가 Redis 장애 시 Memory+WAL fallback 제공
        pass
