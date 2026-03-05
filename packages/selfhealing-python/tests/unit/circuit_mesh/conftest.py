"""circuit_mesh 테스트 공유 fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from selfhealing.services.circuit_mesh import ThresholdOverride


def make_override(
    service_name: str = "svc-upstream",
    expires_in_seconds: int = 600,
    renewal_count: int = 0,
    adjusted_failure_threshold: int = 10,
    adjusted_recovery_timeout: int = 180,
    reason: str = "downstream:svc-down OPEN (depth=1)",
    expires_at: datetime | None = None,
) -> ThresholdOverride:
    """테스트용 ThresholdOverride 생성 헬퍼."""
    return ThresholdOverride(
        service_name=service_name,
        original_failure_threshold=5,
        adjusted_failure_threshold=adjusted_failure_threshold,
        original_recovery_timeout=60,
        adjusted_recovery_timeout=adjusted_recovery_timeout,
        reason=reason,
        expires_at=expires_at
        or datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
        renewal_count=renewal_count,
    )
