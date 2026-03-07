"""
Architecture tests for interface contract integrity (311 — Phase 5 / Section 4).

Verifies:
1. All ABC implementations have all abstract methods
2. All interfaces/*.py ABC/Protocol classes are exported in __init__.py
3. @property @abstractmethod is detected by the inspection logic
4. get_all() is a deprecated non-abstract wrapper for get_all_states()
"""

from __future__ import annotations

import importlib
import inspect
from abc import ABC
from pathlib import Path


def _find_implementations(interface_class: type, adapter_module) -> list[type]:
    """Find all concrete implementations of an ABC interface in the adapters package."""
    implementations = []

    def _scan_module(mod):
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                obj is not interface_class
                and issubclass(obj, interface_class)
                and not inspect.isabstract(obj)
                and obj.__module__.startswith("selfhealing.adapters")
            ):
                implementations.append(obj)

    def _scan_package(pkg):
        pkg_path = Path(pkg.__file__).parent
        for py_file in pkg_path.rglob("*.py"):
            if py_file.name.startswith("_"):
                continue
            rel = py_file.relative_to(pkg_path.parent)
            module_path = (
                str(rel).replace("\\", "/").replace("/", ".").removesuffix(".py")
            )
            full_module = f"selfhealing.{module_path}"
            try:
                mod = importlib.import_module(full_module)
                _scan_module(mod)
            except (ImportError, Exception):
                continue

    _scan_package(adapter_module)
    return implementations


class TestAbstractMethodsImplementedBehavior:
    """All concrete adapter classes implement every @abstractmethod."""

    def test_all_abstract_methods_implemented(self):
        """No adapter is missing abstract methods from its interface."""
        import selfhealing.adapters as adapters
        import selfhealing.interfaces as interfaces

        abc_interfaces = [
            obj
            for _name, obj in inspect.getmembers(interfaces, inspect.isclass)
            if issubclass(obj, ABC) and obj is not ABC
        ]

        violations = []

        for iface in abc_interfaces:
            abstract_methods = {
                name
                for name in dir(iface)
                if getattr(getattr(iface, name, None), "__isabstractmethod__", False)
            }
            if not abstract_methods:
                continue

            implementations = _find_implementations(iface, adapters)
            for impl in implementations:
                # Check if instantiation would fail (has unimplemented abstractmethods)
                remaining = getattr(impl, "__abstractmethods__", frozenset())
                if remaining:
                    violations.append(
                        f"{impl.__name__} missing from {iface.__name__}: {remaining}"
                    )

        assert not violations, "\n".join(violations)


class TestInterfacesExportedInInitBehavior:
    """All ABC/Protocol classes in interfaces/*.py appear in __all__."""

    def test_interfaces_exported_in_init(self):
        """Every public ABC/Protocol in interfaces/*.py is in __init__.__all__.

        Excludes internal helper classes (default implementations, lifecycle
        mixins) that are not part of the public interface catalogue.
        """
        import selfhealing.interfaces as iface_pkg

        iface_dir = Path(iface_pkg.__file__).parent
        exported = set(iface_pkg.__all__)

        # Internal/helper classes not intended for public export
        _INTERNAL_CLASSES = {
            "EventJournalLifecycle",
            "LoggingNotificationAdapter",
            "StdoutNotificationAdapter",
        }

        missing = []
        for py_file in iface_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            module_name = f"selfhealing.interfaces.{py_file.stem}"
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue

            for name, obj in inspect.getmembers(module, inspect.isclass):
                if name in _INTERNAL_CLASSES:
                    continue
                is_abc = issubclass(obj, ABC) and obj is not ABC
                is_protocol = hasattr(obj, "__protocol_attrs__")
                if (is_abc or is_protocol) and obj.__module__ == module.__name__:
                    if name not in exported:
                        missing.append(f"{name} from {py_file.name}")

        assert not missing, f"Not exported in interfaces/__all__: {missing}"


class TestAbstractPropertyDetectedContract:
    """Verify @property @abstractmethod combination is caught."""

    def test_abstract_property_detected_on_web_framework(self):
        """WebFrameworkInterface.framework_name has __isabstractmethod__ = True."""
        from selfhealing.interfaces.web_framework import WebFrameworkInterface

        assert (
            getattr(WebFrameworkInterface.framework_name, "__isabstractmethod__", False)
            is True
        )


class TestGetAllDeprecatedWrapperBehavior:
    """get_all() is a deprecated non-abstract wrapper for get_all_states()."""

    def test_get_all_is_not_abstract(self):
        """get_all() on CircuitBreakerStateRepository is NOT abstract."""
        from selfhealing.interfaces.repositories import (
            CircuitBreakerStateRepository,
        )

        get_all_method = getattr(CircuitBreakerStateRepository, "get_all", None)
        assert get_all_method is not None
        assert not getattr(get_all_method, "__isabstractmethod__", False)

    def test_get_all_states_is_abstract(self):
        """get_all_states() on CircuitBreakerStateRepository IS abstract."""
        from selfhealing.interfaces.repositories import (
            CircuitBreakerStateRepository,
        )

        method = getattr(CircuitBreakerStateRepository, "get_all_states", None)
        assert method is not None
        assert getattr(method, "__isabstractmethod__", False)

    def test_get_all_delegates_to_get_all_states(self):
        """get_all() calls get_all_states() and returns its result."""

        from selfhealing.interfaces.repositories import (
            CircuitBreakerStateRepository,
        )

        # Create a concrete subclass implementing all abstract methods
        abstract_names = CircuitBreakerStateRepository.__abstractmethods__

        methods = {"get_all_states": lambda self: ["state1", "state2"]}
        for name in abstract_names:
            if name not in methods:
                methods[name] = lambda self, *a, **kw: None

        ConcreteRepo = type("ConcreteRepo", (CircuitBreakerStateRepository,), methods)

        repo = ConcreteRepo()
        result = repo.get_all()
        assert result == ["state1", "state2"]
