"""
Integrity Models and Core Functions.

Contains:
- IntegrityInfo: Dataclass for integrity information
- compute_hash: SHA-256 hash computation for dictionaries
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class IntegrityInfo:
    """Integrity information for a log entry."""

    sequence: int
    previous_hash: str
    current_hash: str
    timestamp: str


def compute_hash(data: dict[str, Any]) -> str:
    """
    Compute SHA-256 hash of a dictionary.

    Args:
        data: Dictionary to hash

    Returns:
        SHA-256 hash string
    """
    # Sort keys for deterministic hashing
    json_str = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(json_str.encode()).hexdigest()


__all__ = ["IntegrityInfo", "compute_hash"]
