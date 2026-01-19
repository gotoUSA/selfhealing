"""
Isolation Services - Regional Isolation Gate.

Provides region-level traffic isolation for multi-cluster environments.
"""

from selfhealing.services.isolation.regional_gate import (
    RegionalIsolationGate,
    IsolationInfo,
    get_regional_isolation_gate,
    reset_regional_isolation_gate,
)

__all__ = [
    "RegionalIsolationGate",
    "IsolationInfo",
    "get_regional_isolation_gate",
    "reset_regional_isolation_gate",
]
