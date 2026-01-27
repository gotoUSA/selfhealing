"""
Reconciliation Enums.

Status and mode enumerations for error budget reconciliation.
"""

from __future__ import annotations

from enum import Enum


class ReconciliationStatus(str, Enum):
    """Reconciliation 상태."""

    PENDING = "pending"
    """대기 중 - 아직 Shadow Budget 계산 전."""

    CALCULATED = "calculated"
    """계산 완료 - Shadow Budget 계산됨, 운영자 승인 대기."""

    APPROVED = "approved"
    """승인됨 - 운영자가 Primary Budget에 반영 승인."""

    REJECTED = "rejected"
    """거부됨 - 운영자가 반영 거부 (Excluded Period로 처리)."""

    APPLIED = "applied"
    """적용됨 - Primary Budget에 반영 완료."""

    EXCLUDED = "excluded"
    """제외됨 - 해당 기간은 Budget 계산에서 제외."""


class ApplyMode(str, Enum):
    """Shadow Budget 적용 방식."""

    IMMEDIATE = "immediate"
    """즉시 전액 반영."""

    CAPPED = "capped"
    """최대 N% 포인트까지만 반영 (나머지는 다음 주기)."""

    GRADUAL = "gradual"
    """점진적 반영 (시간에 따라 분산)."""
