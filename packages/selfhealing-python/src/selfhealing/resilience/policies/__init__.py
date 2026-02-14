"""
Resilience Policies — 통합 Policy 패키지.

각 resilience 패턴의 순수 Policy 구현과 PolicyComposer 조합 엔진을 제공한다.

사용 예시::

    from selfhealing.resilience.policies import compose, FallbackPolicy
    result = compose(
        FallbackPolicy(default_value={"degraded": True}),
    ).execute(lambda: fetch_a())

Note:
    HedgingPolicy, AsyncHedgingPolicy, HedgingConfigUpdateHook은
    core.hedging과의 순환 참조로 인해 lazy import로 제공된다.
    from selfhealing.resilience.policies.hedging import HedgingPolicy 직접 사용 권장.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Core interfaces (re-export from interfaces)
from selfhealing.interfaces.resilience_policy import (
    AsyncResiliencePolicy,
    PolicyContext,
    PolicyOutcome,
    PolicyRejectedException,
    PolicyResult,
    ResiliencePolicy,
)

# Composer
from selfhealing.resilience.policies.composer import (
    AsyncPolicyComposer,
    PolicyComposer,
    compose,
    compose_async,
)

# Policies — Fallback (순환 참조 없음)
from selfhealing.resilience.policies.fallback import (
    AsyncFallbackPolicy,
    FallbackPolicy,
    partition_aware_chain,
)

# Guards
from selfhealing.resilience.policies.guards import (
    ErrorBudgetGuard,
    KillSwitchGuard,
)

# Hooks
from selfhealing.resilience.policies.hooks import (
    AuditHook,
    EventBusHook,
    MetricsHook,
)

# Sinks
from selfhealing.resilience.policies.sinks import DLQSink

# Presets
from selfhealing.resilience.policies.presets import ha_pipeline, standard_pipeline

if TYPE_CHECKING:
    from selfhealing.resilience.policies.hedging import (
        AsyncHedgingPolicy,
        HedgingConfigUpdateHook,
        HedgingPolicy,
    )


def __getattr__(name: str):
    """Lazy import for hedging policies (circular import 방지)."""
    _hedging_names = {"AsyncHedgingPolicy", "HedgingConfigUpdateHook", "HedgingPolicy"}
    if name in _hedging_names:
        from selfhealing.resilience.policies import hedging as _hedging_mod

        return getattr(_hedging_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Core interfaces
    "AsyncResiliencePolicy",
    "PolicyContext",
    "PolicyOutcome",
    "PolicyRejectedException",
    "PolicyResult",
    "ResiliencePolicy",
    # Composer
    "AsyncPolicyComposer",
    "PolicyComposer",
    "compose",
    "compose_async",
    # Policies
    "AsyncFallbackPolicy",
    "AsyncHedgingPolicy",
    "FallbackPolicy",
    "HedgingConfigUpdateHook",
    "HedgingPolicy",
    "partition_aware_chain",
    # Guards
    "ErrorBudgetGuard",
    "KillSwitchGuard",
    # Hooks
    "AuditHook",
    "EventBusHook",
    "MetricsHook",
    # Sinks
    "DLQSink",
    # Presets
    "ha_pipeline",
    "standard_pipeline",
]
