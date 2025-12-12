"""
Django ORM Rate Limit State Repository

DjangoRateLimitStateRepository - Django ORM repository for rate limit state.
Used by DatabaseRateLimitStorage for distributed Self-DDoS prevention.

Reference: docs/SELF_HEALING_EXTRACTION_PLAN.md Phase 1.2
"""

from __future__ import annotations

from typing import Optional


class DjangoRateLimitStateRepository:
    """
    Django ORM repository for rate limit state.

    Used by DatabaseRateLimitStorage for distributed Self-DDoS prevention.

    Note: This requires a RateLimitState model. If not using Django,
    provide a custom repository_factory to DatabaseRateLimitStorage.
    """

    def _get_model(self):
        """Lazy import to avoid circular dependencies."""
        try:
            from shopping.models.rate_limit_state import RateLimitState

            return RateLimitState
        except ImportError:
            # Fallback: Try to create a simple table using raw SQL
            raise ImportError(
                "RateLimitState model not found. Please create the model or " "use Redis/InMemory storage instead."
            )

    def get(self, key: str) -> Optional[dict]:
        """Get rate limit state by key."""
        RateLimitState = self._get_model()

        try:
            obj = RateLimitState.objects.filter(key=key).first()
            if obj is None:
                return None

            return {
                "cooldown_until": obj.cooldown_until,
                "consecutive_429s": obj.consecutive_429s,
                "last_updated": obj.last_updated,
            }
        except Exception:
            return None

    def get_or_create(self, key: str) -> dict:
        """Get or create rate limit state."""
        RateLimitState = self._get_model()

        obj, created = RateLimitState.objects.get_or_create(
            key=key,
            defaults={
                "cooldown_until": 0.0,
                "consecutive_429s": 0,
                "last_updated": 0.0,
            },
        )

        return {
            "cooldown_until": obj.cooldown_until,
            "consecutive_429s": obj.consecutive_429s,
            "last_updated": obj.last_updated,
        }

    def upsert(self, key: str, data: dict) -> None:
        """Insert or update rate limit state."""
        RateLimitState = self._get_model()

        import time

        now = time.time()

        RateLimitState.objects.update_or_create(
            key=key,
            defaults={
                **data,
                "last_updated": data.get("last_updated", now),
            },
        )

    def update(self, key: str, data: dict) -> None:
        """Update rate limit state."""
        RateLimitState = self._get_model()

        import time

        data["last_updated"] = time.time()

        RateLimitState.objects.filter(key=key).update(**data)

    def increment(self, key: str, field: str) -> int:
        """Atomically increment a field."""
        RateLimitState = self._get_model()

        from django.db.models import F
        import time

        # Get or create first
        obj, created = RateLimitState.objects.get_or_create(
            key=key,
            defaults={
                "cooldown_until": 0.0,
                "consecutive_429s": 0,
                "last_updated": time.time(),
            },
        )

        # Atomically increment
        RateLimitState.objects.filter(key=key).update(
            **{field: F(field) + 1},
            last_updated=time.time(),
        )

        # Return new value
        obj.refresh_from_db()
        return getattr(obj, field)

    def delete(self, key: str) -> None:
        """Delete rate limit state."""
        RateLimitState = self._get_model()
        RateLimitState.objects.filter(key=key).delete()
