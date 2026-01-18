"""
Hash Chain Manager Protocol.

Contains:
- HashChainManagerProtocol: Interface for hash chain managers
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from typing import Protocol, runtime_checkable
except ImportError:
    from typing_extensions import Protocol, runtime_checkable


@runtime_checkable
class HashChainManagerProtocol(Protocol):
    """
    Protocol for hash chain managers.
    
    Allows switching between local file-based and distributed (Redis)
    implementations without code changes.
    """
    
    def add_integrity(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Add integrity fields to an entry."""
        ...
    
    def get_state(self) -> Dict[str, Any]:
        """Get current chain state."""
        ...


__all__ = ["HashChainManagerProtocol"]
