"""
Protection Mixin for Circuit Breaker Service

Provides rate limit cascade detection and self-DDoS protection functionality.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

import structlog

from .rate_limit_tracker import get_rate_limit_tracker

if TYPE_CHECKING:
    from selfhealing.services.circuit_breaker_service import CircuitBreakerResult

    from .config import CircuitBreakerConfig

logger = structlog.get_logger()


class ProtectionMixin:
    """
    Mixin class providing protection functionality for CircuitBreakerService.

    Includes:
    - Rate limit cascade detection
    - Self-DDoS protection
    - Adaptive backoff calculation
    """

    # These will be provided by the main service class
    config: CircuitBreakerConfig
    is_enabled: bool
    should_allow: callable
    get_state: callable
    force_open: callable

    # =========================================================================
    # Rate Limit Cascade Detection
    # =========================================================================

    def record_rate_limit_response(self, service_name: str) -> CircuitBreakerResult | None:
        """
        Record a 429 rate limit response and check for cascade.

        Call this method when receiving a 429 response from an external service.
        If a rate limit cascade is detected (too many 429s in a short window),
        the circuit breaker will automatically open to prevent self-DDoS.

        Args:
            service_name: Name of the external service

        Returns:
            CircuitBreakerResult if circuit was opened, None otherwise
        """
        if not self.is_enabled:
            return None

        tracker = get_rate_limit_tracker()
        tracker.record_rate_limit(service_name)

        # Check for cascade condition
        rate_limit_count = tracker.get_rate_limit_count(service_name, self.config.rate_limit_cascade_window_seconds)

        if rate_limit_count >= self.config.rate_limit_cascade_threshold:
            logger.warning(
                "circuit_breaker.rate_limit_cascade_detected",
                service_name=service_name,
                rate_limit_count=rate_limit_count,
                rate_limit_cascade_window_seconds=self.config.rate_limit_cascade_window_seconds,
            )

            # Auto-open circuit breaker
            result = self.force_open(
                service_name=service_name,
                reason=f"Rate limit cascade detected ({rate_limit_count} 429s in "
                f"{self.config.rate_limit_cascade_window_seconds}s)",
            )

            if result.success:
                # Increment backoff level for this service
                tracker.increment_backoff(service_name)
                logger.warning(
                    "circuit_breaker.auto_opened_circuit_due",
                    service_name=service_name,
                )

            return result

        return None

    def check_rate_limit_cascade(self, service_name: str) -> bool:
        """
        Check if a rate limit cascade is occurring for a service.

        Args:
            service_name: Name of the external service

        Returns:
            True if cascade is detected, False otherwise
        """
        tracker = get_rate_limit_tracker()
        rate_limit_count = tracker.get_rate_limit_count(service_name, self.config.rate_limit_cascade_window_seconds)
        return rate_limit_count >= self.config.rate_limit_cascade_threshold

    # =========================================================================
    # Self-DDoS Protection
    # =========================================================================

    def should_allow_with_ddos_protection(self, service_name: str) -> tuple[bool, float]:
        """
        Check if request should be allowed with self-DDoS protection.

        This method combines circuit breaker check with self-DDoS protection.
        If the request rate is too high, it returns a suggested backoff delay.

        Args:
            service_name: Name of the external service

        Returns:
            Tuple of (should_allow, suggested_backoff_seconds)
            - If should_allow is False, the request should be blocked
            - suggested_backoff_seconds indicates how long to wait before retry
        """
        # First, check standard circuit breaker
        if not self.should_allow(service_name):
            backoff = self.calculate_adaptive_backoff(service_name)
            return False, backoff

        # Check self-DDoS protection
        if not self.config.self_ddos_protection_enabled:
            return True, 0.0

        tracker = get_rate_limit_tracker()
        tracker.record_request(service_name)

        request_count = tracker.get_request_count(service_name, self.config.self_ddos_window_seconds)

        if request_count > self.config.self_ddos_request_threshold:
            # Too many requests - suggest backoff but don't block
            backoff = self.calculate_adaptive_backoff(service_name)
            logger.warning(
                "circuit_breaker.self_ddos_protection_triggered",
                service_name=service_name,
                request_count=request_count,
                self_ddos_window_seconds=self.config.self_ddos_window_seconds,
                backoff=backoff,
            )
            return True, backoff  # Allow but suggest delay

        return True, 0.0

    def calculate_adaptive_backoff(self, service_name: str) -> float:
        """
        Calculate adaptive backoff delay based on current conditions.

        Uses exponential backoff with jitter to prevent thundering herd.

        Args:
            service_name: Name of the external service

        Returns:
            Backoff delay in seconds
        """
        tracker = get_rate_limit_tracker()
        backoff_level = tracker.get_backoff_level(service_name)

        # Base backoff: 1 second, exponentially increasing
        base_backoff = 1.0
        max_backoff = 60.0  # Maximum 60 seconds

        # Calculate exponential backoff
        backoff = min(
            base_backoff * (self.config.self_ddos_backoff_multiplier**backoff_level),
            max_backoff,
        )

        # Add jitter (±25%) to prevent thundering herd
        jitter = backoff * 0.25 * (2 * random.random() - 1)
        backoff_with_jitter = max(0.1, backoff + jitter)

        return backoff_with_jitter

    def reset_backoff(self, service_name: str) -> None:
        """
        Reset backoff level for a service after successful recovery.

        Call this after a service has recovered to reset adaptive backoff.

        Args:
            service_name: Name of the external service
        """
        tracker = get_rate_limit_tracker()
        tracker.reset_backoff(service_name)
        logger.info(
            "circuit_breaker.reset_backoff_level",
            service_name=service_name,
        )

    def is_self_ddos_detected(self, service_name: str) -> bool:
        """
        Check if self-DDoS conditions are detected for a service.

        Args:
            service_name: Name of the external service

        Returns:
            True if self-DDoS is detected, False otherwise
        """
        if not self.config.self_ddos_protection_enabled:
            return False

        tracker = get_rate_limit_tracker()
        request_count = tracker.get_request_count(service_name, self.config.self_ddos_window_seconds)
        return request_count > self.config.self_ddos_request_threshold

    def get_protection_status(self, service_name: str) -> dict[str, Any]:
        """
        Get comprehensive protection status for a service.

        Returns:
            Dictionary with protection status details
        """
        tracker = get_rate_limit_tracker()

        return {
            "service_name": service_name,
            "circuit_state": self.get_state(service_name),
            "circuit_breaker_enabled": self.is_enabled,
            "rate_limit_cascade": {
                "detected": self.check_rate_limit_cascade(service_name),
                "count_in_window": tracker.get_rate_limit_count(service_name, self.config.rate_limit_cascade_window_seconds),
                "threshold": self.config.rate_limit_cascade_threshold,
                "window_seconds": self.config.rate_limit_cascade_window_seconds,
            },
            "self_ddos_protection": {
                "enabled": self.config.self_ddos_protection_enabled,
                "detected": self.is_self_ddos_detected(service_name),
                "request_count_in_window": tracker.get_request_count(service_name, self.config.self_ddos_window_seconds),
                "threshold": self.config.self_ddos_request_threshold,
                "window_seconds": self.config.self_ddos_window_seconds,
            },
            "backoff": {
                "current_level": tracker.get_backoff_level(service_name),
                "suggested_delay_seconds": self.calculate_adaptive_backoff(service_name),
            },
        }
