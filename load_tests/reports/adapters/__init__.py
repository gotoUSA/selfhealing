"""
Load Test Reports - Adapters Package.

기존 레거시 데이터를 새 스키마로 변환하는 어댑터 모음.
"""

from .legacy_stats import (
    adapt_extreme_stats,
    create_report_from_legacy_stats,
)

__all__ = [
    "adapt_extreme_stats",
    "create_report_from_legacy_stats",
]
