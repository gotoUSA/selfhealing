"""
Chaos Experiment Services

안전한 Chaos 실험을 위한 서비스 모듈.
Reference: 33_CHAOS_INDUSTRY_EXPERIMENTS.md, 34_CHAOS_SAFETY_MECHANISMS.md
"""

from .constants import (
    CHAOS_DOMAIN_PREFIX,
    CHAOS_METADATA_FLAGS,
    ExperimentHardCaps,
)
from .isolation_helpers import (
    get_isolated_domain,
    strip_isolation_prefix,
    is_chaos_domain,
    get_isolation_metadata,
    should_exclude_from_metrics,
    cleanup_chaos_entries,
    cleanup_all_chaos_domains,
)

__all__ = [
    # Constants
    "CHAOS_DOMAIN_PREFIX",
    "CHAOS_METADATA_FLAGS",
    "ExperimentHardCaps",
    # Isolation Helpers
    "get_isolated_domain",
    "strip_isolation_prefix",
    "is_chaos_domain",
    "get_isolation_metadata",
    "should_exclude_from_metrics",
    "cleanup_chaos_entries",
    "cleanup_all_chaos_domains",
]
