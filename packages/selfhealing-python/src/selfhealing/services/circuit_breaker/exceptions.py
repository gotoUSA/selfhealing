"""
Circuit Breaker Exceptions

Circuit Breaker Policy에서 사용하는 범용 예외 타입 정의.
"""

from __future__ import annotations

from selfhealing.core.exceptions import CircuitBreakerError


class CircuitBreakerOpenError(CircuitBreakerError):
    """Circuit Breaker가 OPEN 상태일 때 요청이 거부되었음을 나타내는 예외.

    Attributes:
        service_name: OPEN 상태인 서비스 이름
    """

    def __init__(self, service_name: str, message: str | None = None):
        self.service_name = service_name
        super().__init__(message or f"Circuit breaker '{service_name}' is OPEN")
