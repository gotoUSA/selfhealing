"""
Stress Test Service - Data Models.

스트레스 테스트 결과를 표현하는 데이터 클래스 모음.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# =============================================================================
# Data Classes for Stress Test Results
# =============================================================================


@dataclass
class StressTestResult:
    """스트레스 테스트 결과 데이터 클래스."""

    status: str
    elapsed_seconds: float = 0.0
    message: str = ""
    error: str | None = None
    error_type: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """결과를 딕셔너리로 변환."""
        result = {
            "status": self.status,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }
        if self.message:
            result["message"] = self.message
        if self.error:
            result["error"] = self.error
        if self.error_type:
            result["error_type"] = self.error_type
        result.update(self.extra)
        return result


@dataclass
class PoolStatusResult:
    """커넥션 풀 상태 결과."""

    status: str
    sqlalchemy_pool: dict = field(default_factory=dict)
    pg_stats: dict = field(default_factory=dict)
    connection_usable: bool = True
    use_connection_pool: bool = False
    error: str | None = None
    error_type: str | None = None

    def to_dict(self) -> dict:
        """결과를 딕셔너리로 변환."""
        result = {
            "status": self.status,
            "sqlalchemy_pool": self.sqlalchemy_pool,
            "pg_stats": self.pg_stats,
            "connection_usable": self.connection_usable,
            "use_connection_pool": self.use_connection_pool,
        }
        if self.error:
            result["error"] = self.error
        if self.error_type:
            result["error_type"] = self.error_type
        return result


@dataclass
class LockContentionResult:
    """락 경합 테스트 결과."""

    status: str
    lock_id: int
    duration_seconds: float
    total_attempts: int = 0
    success_count: int = 0
    fail_count: int = 0
    success_rate_percent: float = 0.0
    avg_wait_ms: float = 0.0
    lock_hold_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        """결과를 딕셔너리로 변환."""
        result = {
            "status": self.status,
            "lock_id": self.lock_id,
            "duration_seconds": round(self.duration_seconds, 2),
        }
        if self.status == "completed":
            result.update(
                {
                    "total_attempts": self.total_attempts,
                    "success_count": self.success_count,
                    "fail_count": self.fail_count,
                    "success_rate_percent": self.success_rate_percent,
                    "avg_wait_ms": self.avg_wait_ms,
                    "lock_hold_ms": self.lock_hold_ms,
                }
            )
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class BurstFailureResult:
    """Burst 장애 테스트 결과."""

    status: str
    lock_id: int
    lock_timeout_ms: int
    burst_duration_seconds: float
    total_attempts: int = 0
    timeout_count: int = 0
    success_count: int = 0
    deadlock_count: int = 0
    failure_rate_percent: float = 0.0
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        """결과를 딕셔너리로 변환."""
        result = {
            "status": self.status,
            "lock_id": self.lock_id,
            "lock_timeout_ms": self.lock_timeout_ms,
            "burst_duration_seconds": round(self.burst_duration_seconds, 2),
            "total_attempts": self.total_attempts,
            "timeout_count": self.timeout_count,
            "success_count": self.success_count,
            "deadlock_count": self.deadlock_count,
            "failure_rate_percent": self.failure_rate_percent,
        }
        if self.message:
            result["message"] = self.message
        if self.error:
            result["error"] = self.error
        return result
