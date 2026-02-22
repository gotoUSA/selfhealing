"""
Retry Handler with Exponential Backoff

Provides a reusable retry mechanism with:
- Configurable max attempts
- Exponential backoff with jitter
- Idempotency checking
- DLQ routing on exhaustion
- Forensic context capture
- Rate limit awareness (Self-DDoS prevention)

Idempotency 체크, DLQ 라우팅, Rate Limit 인식 기능을 제공합니다.

.. versionadded:: 2.1.0
    ``retry_handler.py`` 플랫 파일에서 ``retry_handler/`` 패키지로 전환.
    기존 ``from selfhealing.services.retry_handler import X`` 임포트 호환 유지.
"""

from __future__ import annotations

import importlib as _importlib
import sys as _sys
import types as _types
from typing import Any as _Any

# Decorators
from .decorators import (
    with_retry,
)

# Guards
from .guards import ErrorBudgetGuard, KillSwitchGuard

# Handler (legacy)
from .handler import (
    RetryHandler,
    _is_system_enabled,
    logger,
)

# Hooks
from .hooks import AuditHook, MetricsHook

# === Explicit re-exports ===
# Models
from .models import (
    MaxRetriesExceededError,
    RetryAction,
    RetryConfig,
    RetryPolicyConfig,
    RetryResult,
    T,
)

# Policy (new)
from .policy import RetryPolicy

# Sinks
from .sinks import DLQSink

__all__ = [
    # models
    "RetryAction",
    "MaxRetriesExceededError",
    "RetryConfig",
    "RetryPolicyConfig",
    "RetryResult",
    "T",
    # handler (legacy)
    "RetryHandler",
    "_is_system_enabled",
    "logger",
    # policy
    "RetryPolicy",
    # guards
    "KillSwitchGuard",
    "ErrorBudgetGuard",
    # hooks
    "AuditHook",
    "MetricsHook",
    # sinks
    "DLQSink",
    # decorators
    "with_retry",
]

# =============================================================================
# Dynamic forwarding (event_bus setattr pattern)
# =============================================================================
# Eagerly copy all sub-module attributes to package level.
# 기존 `from selfhealing.services.retry_handler import X` 패턴 호환 유지.

_SUB_MODULES = ("models", "handler", "policy", "guards", "hooks", "sinks", "decorators")

from . import decorators as _decorators_mod  # noqa: E402
from . import guards as _guards_mod  # noqa: E402
from . import handler as _handler_mod  # noqa: E402
from . import hooks as _hooks_mod  # noqa: E402
from . import models as _models_mod  # noqa: E402
from . import policy as _policy_mod  # noqa: E402
from . import sinks as _sinks_mod  # noqa: E402

_pkg = _sys.modules[__name__]
for _mod in (_models_mod, _handler_mod, _policy_mod, _guards_mod, _hooks_mod, _sinks_mod, _decorators_mod):
    for _name in dir(_mod):
        if not _name.startswith("__") and not hasattr(_pkg, _name):
            setattr(_pkg, _name, getattr(_mod, _name))
del _name, _mod


def __getattr__(name: str) -> _Any:
    """Dynamic attribute forwarding from all sub-modules."""
    for _sub in _SUB_MODULES:
        try:
            _mod = _importlib.import_module(f".{_sub}", __name__)
            if hasattr(_mod, name):
                _val = getattr(_mod, name)
                globals()[name] = _val
                return _val
        except ImportError:
            continue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# =============================================================================
# Module proxy for mock.patch support (setattr forwarding)
# =============================================================================
# mock.patch('selfhealing.services.retry_handler.X') must propagate writes to
# the sub-module that owns X so the patched version is used at call-sites.
# Without this, patching _is_system_enabled on the package would not affect
# handler.py's execute() which references its own module-level function.


class _ForwardingModule(_types.ModuleType):
    """Module proxy: forwards __setattr__ to owning sub-module."""

    def __getattr__(self, name: str) -> _Any:
        for _sub in _SUB_MODULES:
            try:
                _mod = _importlib.import_module(f".{_sub}", self.__name__)
                if hasattr(_mod, name):
                    _val = getattr(_mod, name)
                    object.__setattr__(self, name, _val)
                    return _val
            except ImportError:
                continue
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value: _Any) -> None:
        # Forward writes to the owning sub-module so that
        # mock.patch at package level affects the actual call-site.
        if not name.startswith("__"):
            for _sub in _SUB_MODULES:
                try:
                    _mod = _importlib.import_module(f".{_sub}", self.__name__)
                    if name in _mod.__dict__:
                        _mod.__dict__[name] = value
                        break
                except (ImportError, AttributeError):
                    continue
        object.__setattr__(self, name, value)


_current = _sys.modules[__name__]
_proxy = _ForwardingModule(__name__)
_proxy.__dict__.update(_current.__dict__)
_proxy.__path__ = _current.__path__  # type: ignore[attr-defined]
_proxy.__package__ = _current.__package__
_proxy.__spec__ = _current.__spec__
_proxy.__file__ = _current.__file__  # type: ignore[attr-defined]
_proxy.__loader__ = getattr(_current, "__loader__", None)
_sys.modules[__name__] = _proxy
