"""
Rate Limit State Model

Stores distributed rate limit state for Self-DDoS prevention.
Used by the RateLimitCoordinator to coordinate across multiple workers/servers.

Reference: packages/selfhealing-python/docs/RATE_LIMIT_COORDINATOR.md
"""

from django.db import models


class RateLimitState(models.Model):
    """
    Distributed rate limit state storage.

    Used by DatabaseRateLimitStorage adapter to share rate limit
    state across multiple workers and servers.

    Fields:
        key: Unique identifier (e.g., "payment_api", "external_service")
        cooldown_until: Unix timestamp when cooldown ends
        consecutive_429s: Counter for exponential backoff calculation
        last_updated: Unix timestamp of last update
    """

    key = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique identifier for the rate-limited resource",
    )

    cooldown_until = models.FloatField(
        default=0.0,
        help_text="Unix timestamp when cooldown period ends",
    )

    consecutive_429s = models.IntegerField(
        default=0,
        help_text="Number of consecutive 429 responses for backoff calculation",
    )

    last_updated = models.FloatField(
        default=0.0,
        help_text="Unix timestamp of last state update",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "selfhealing_ratelimitstate"
        verbose_name = "Rate Limit State"
        verbose_name_plural = "Rate Limit States"
        indexes = [
            models.Index(fields=["key"], name="idx_ratelimit_key"),
        ]

    def __str__(self):
        return f"RateLimitState({self.key}, cooldown_until={self.cooldown_until})"

    @property
    def is_in_cooldown(self) -> bool:
        """Check if currently in cooldown period."""
        import time

        return time.time() < self.cooldown_until

    @property
    def remaining_cooldown(self) -> float:
        """Get remaining cooldown time in seconds."""
        import time

        return max(0.0, self.cooldown_until - time.time())
