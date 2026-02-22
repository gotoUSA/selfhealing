"""
Backoff Calculator Package — Exponential Backoff with Throttle Awareness.

Provides configurable exponential backoff with jitter for retry logic.

Features:
- Exponential backoff: base^attempt (4, 16, 64, ...)
- Maximum delay cap to prevent excessive wait times
- Jitter (±25%) to prevent thundering herd problem
- Per-domain configuration support
- Throttle-aware backoff with dynamic multipliers
- EventBus push-based state caching
- Global (Redis) throttle state sharing

Modules:
    - models: ThrottleState, PushBasedThrottleStateCache, GlobalThrottleState, BackoffConfig
    - budget: AdaptiveRetryBudget
    - global_state: GlobalThrottleStateManager
    - calculator: BackoffCalculator, ThrottleAwareBackoffCalculator, calculate_backoff, get_calculator_for_domain

Usage:
    from selfhealing.services.backoff_calculator import (
        BackoffCalculator,
        BackoffConfig,
        calculate_backoff,
        get_calculator_for_domain,
    )

.. versionadded:: 2.2.0
    ``backoff_calculator.py`` 플랫 파일에서 ``backoff_calculator/`` 패키지로 전환.
"""

# sub-module의 모든 속성을 패키지 레벨에 노출.
# 기존 `from selfhealing.services.backoff_calculator import _xxx` 패턴 호환 유지.
import sys as _sys

from selfhealing.services.backoff_calculator import budget as _budget_module
from selfhealing.services.backoff_calculator import calculator as _calculator_module
from selfhealing.services.backoff_calculator import global_state as _global_state_module
from selfhealing.services.backoff_calculator import models as _models_module
from selfhealing.services.backoff_calculator.budget import (
    AdaptiveRetryBudget,
)
from selfhealing.services.backoff_calculator.calculator import (
    BackoffCalculator,
    ThrottleAwareBackoffCalculator,
    _calculators,
    calculate_backoff,
    get_calculator_for_domain,
)
from selfhealing.services.backoff_calculator.global_state import (
    GlobalThrottleStateManager,
)
from selfhealing.services.backoff_calculator.models import (
    SYSTEM_TIMEOUT_SECONDS,
    BackoffConfig,
    GlobalThrottleState,
    PushBasedThrottleStateCache,
    ThrottleState,
)

_pkg = _sys.modules[__name__]
for _mod in (_models_module, _budget_module, _global_state_module, _calculator_module):
    for _name in dir(_mod):
        if not _name.startswith("__") and not hasattr(_pkg, _name):
            setattr(_pkg, _name, getattr(_mod, _name))
del _name, _mod, _pkg

__all__ = [
    # Constants
    "SYSTEM_TIMEOUT_SECONDS",
    # Models
    "ThrottleState",
    "PushBasedThrottleStateCache",
    "GlobalThrottleState",
    "BackoffConfig",
    # Budget
    "AdaptiveRetryBudget",
    # Global State
    "GlobalThrottleStateManager",
    # Calculator
    "BackoffCalculator",
    "ThrottleAwareBackoffCalculator",
    "calculate_backoff",
    "get_calculator_for_domain",
    "_calculators",
]
