"""
Replay Service Data Models.

ReplayResult 및 BatchReplayResult 데이터클래스를 제공합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from selfhealing.services.governance.checks import GovernanceCheckResult

# =============================================================================
# Replay Result
# =============================================================================


@dataclass
class ReplayResult:
    """Result of a replay operation."""

    success: bool
    dlq_id: int
    message: str = ""
    error: str | None = None
    data: dict[str, Any] | None = None

    @classmethod
    def succeeded(cls, dlq_id: int, message: str = "", data: dict | None = None) -> ReplayResult:
        """Factory for successful replay."""
        return cls(success=True, dlq_id=dlq_id, message=message, data=data)

    @classmethod
    def failed(cls, dlq_id: int, error: str) -> ReplayResult:
        """Factory for failed replay."""
        return cls(success=False, dlq_id=dlq_id, error=error)

    @classmethod
    def blocked(cls, dlq_id: int, governance_result: GovernanceCheckResult) -> ReplayResult:
        """Factory for governance-blocked replay."""
        return cls(
            success=False,
            dlq_id=dlq_id,
            error=governance_result.block_message,
            data={
                "blocked": True,
                "block_reason": (governance_result.block_reason.value if governance_result.block_reason else None),
            },
        )


@dataclass
class BatchReplayResult:
    """Result of a batch replay operation."""

    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    results: list[ReplayResult] | None = None
    governance_blocked: bool = False
    governance_block_reason: str = ""
    # 도메인 우선순위 기반 재생 정보
    priority_used: bool = False
    domains_processed: list[str] | None = None
