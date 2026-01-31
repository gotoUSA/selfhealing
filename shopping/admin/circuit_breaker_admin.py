"""
Admin configuration for Circuit Breaker domain.

CircuitBreakerState 모델의 Django Admin 설정.
selfhealing 패키지의 BaseCircuitBreakerStateAdmin을 상속하여 사용합니다.
"""

from django.contrib import admin

from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin
from shopping.models.failed_external_request import CircuitBreakerState


@admin.register(CircuitBreakerState)
class CircuitBreakerStateAdmin(BaseCircuitBreakerStateAdmin):
    """
    Circuit Breaker State Admin 설정.

    selfhealing 패키지의 BaseCircuitBreakerStateAdmin을 상속하여
    모든 기본 설정을 재사용합니다.

    호스트 앱에서 추가 커스터마이징이 필요한 경우
    이 클래스에서 오버라이드할 수 있습니다.
    """

    pass
