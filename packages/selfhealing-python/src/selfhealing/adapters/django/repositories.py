"""
Django ORM Repository Implementations.

Concrete implementations of repository interfaces using Django ORM.
These adapters translate between abstract interface methods and
Django model operations.

⚠️ BACKWARD COMPATIBILITY:
이 파일은 기존 import 경로와의 호환성을 위해 유지됩니다.
새로운 코드에서는 개별 모듈에서 직접 import하세요:

    from selfhealing.adapters.django.failed_operation_repository import DjangoFailedOperationRepository
    from selfhealing.adapters.django.circuit_breaker_repository import DjangoCircuitBreakerStateRepository
    from selfhealing.adapters.django.security_incident_repository import DjangoSecurityIncidentRepository
"""

from selfhealing.adapters.django.failed_operation_repository import (
    DjangoFailedOperationRepository,
)
from selfhealing.adapters.django.circuit_breaker_repository import (
    DjangoCircuitBreakerStateRepository,
)
from selfhealing.adapters.django.security_incident_repository import (
    DjangoSecurityIncidentRepository,
)


__all__ = [
    "DjangoFailedOperationRepository",
    "DjangoCircuitBreakerStateRepository",
    "DjangoSecurityIncidentRepository",
]
