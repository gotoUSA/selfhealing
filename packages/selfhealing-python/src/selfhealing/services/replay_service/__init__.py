"""
DLQ Replay Service Package.

DLQ 재생 기능을 제공합니다.
수동 재생, 배치 재생, 서킷 브레이커 복구 시 조건부 재생을 지원합니다.

Modules:
    - models: ReplayResult, BatchReplayResult 데이터클래스
    - handlers: ReplayHandler ABC, DefaultReplayHandler, 핸들러 레지스트리
    - service: ReplayService 클래스, 싱글턴, 편의 함수

Usage:
    from selfhealing.services.replay_service import (
        ReplayService,
        ReplayResult,
        BatchReplayResult,
        get_replay_service,
        replay_failed_operation,
        batch_replay_by_failure_type,
        register_replay_handler,
        get_replay_handler,
    )

.. versionadded:: 7.0.0
    ``replay_service.py`` 플랫 파일에서 ``replay_service/`` 패키지로 전환.
"""

from selfhealing.services.replay_service.models import (
    ReplayResult,
    BatchReplayResult,
)
from selfhealing.services.replay_service.handlers import (
    ReplayHandler,
    DefaultReplayHandler,
    _replay_handlers,
    register_replay_handler,
    get_replay_handler,
)
from selfhealing.services.replay_service.service import (
    ReplayService,
    _replay_service,
    get_replay_service,
    replay_failed_operation,
    batch_replay_by_failure_type,
    logger,
)

# ---------------------------------------------------------------------------
# Dynamic attribute forwarding – expose ALL sub-module attributes at package
# level so that ``from selfhealing.services.replay_service import <name>``
# keeps working for every symbol, including imports pulled into sub-modules
# (e.g. check_all_governance, log_dlq_replay_audit, GovernanceCheckResult).
#
# This is CRITICAL for test patches such as:
#   @patch("selfhealing.services.replay_service.check_all_governance")
#   @patch("selfhealing.services.replay_service.log_dlq_replay_audit")
# ---------------------------------------------------------------------------
import importlib as _importlib
import sys as _sys
import types as _types
from typing import Any as _Any

_SUB_MODULES = ("models", "handlers", "service")

from selfhealing.services.replay_service import (
    models as _models_mod,
    handlers as _handlers_mod,
    service as _service_mod,
)

_pkg = _sys.modules[__name__]
for _mod in (
    _models_mod,
    _handlers_mod,
    _service_mod,
):
    for _name in dir(_mod):
        if not _name.startswith("__") and not hasattr(_pkg, _name):
            setattr(_pkg, _name, getattr(_mod, _name))
del _name, _mod, _pkg


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

__all__ = [
    # Models
    "ReplayResult",
    "BatchReplayResult",
    # Handlers
    "ReplayHandler",
    "DefaultReplayHandler",
    "_replay_handlers",
    "register_replay_handler",
    "get_replay_handler",
    # Service
    "ReplayService",
    "_replay_service",
    "get_replay_service",
    "replay_failed_operation",
    "batch_replay_by_failure_type",
    "logger",
]
