from django.apps import AppConfig


class ShoppingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shopping"

    def ready(self):
        """
        앱이 준비되면 시그널 등록

        소셜 로그인 시그널을 활성화하여
        자동 이메일 인증 처리가 작동하도록 합니다.
        
        또한 selfhealing 패키지에 shopping 앱의 Django Repository를 등록합니다.
        """
        import shopping.signals  # noqa
        
        # Register shopping app's Django repositories with selfhealing package
        self._register_selfhealing_repositories()
    
    def _register_selfhealing_repositories(self):
        """Register Django ORM repositories from shopping app with selfhealing package."""
        try:
            from selfhealing.factory import ProviderRegistry
            from shopping.services.self_healing.adapters.django_repositories import (
                DjangoFailedOperationRepository,
                DjangoCircuitBreakerStateRepository,
                DjangoSecurityIncidentRepository,
            )
            
            # Override the selfhealing package's incomplete Django repos
            # with shopping app's complete implementations
            ProviderRegistry.register_failed_operation_repo("django", DjangoFailedOperationRepository)
            ProviderRegistry.register_circuit_breaker_repo("django", DjangoCircuitBreakerStateRepository)
            ProviderRegistry.register_security_repo("django", DjangoSecurityIncidentRepository)
            
            # Also set as default
            ProviderRegistry._default_repo = "django"
            
        except ImportError as e:
            # selfhealing package not installed
            pass
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to register selfhealing repositories: {e}")
