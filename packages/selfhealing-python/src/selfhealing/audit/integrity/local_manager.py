"""
Local File-based Hash Chain Manager.

Contains:
- HashChainManager: Thread-safe manager for local hash chain state
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from selfhealing.audit.integrity.models import compute_hash
from selfhealing.core.file_utils import safe_unlink

logger = structlog.get_logger()


class HashChainManager:
    """
    Manages hash chain state for audit logging.

    Thread-safe manager that maintains:
    - Current sequence number
    - Previous hash for chaining
    - Periodic checkpoints
    """

    GENESIS_HASH = "GENESIS"

    def __init__(self, state_file: Path | None = None):
        """
        Initialize hash chain manager.

        Args:
            state_file: Optional path to persist chain state
        """
        self._lock = threading.RLock()
        self._sequence = 0
        self._previous_hash = self.GENESIS_HASH
        self._state_file = state_file

        if state_file:
            self._load_state()

    def _load_state(self) -> None:
        """Load chain state from file."""
        if self._state_file and self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                self._sequence = data.get("sequence", 0)
                self._previous_hash = data.get("previous_hash", self.GENESIS_HASH)
                logger.debug(
                    "hash_chain.loaded_state",
                    sequence=self._sequence,
                )
            except Exception as e:
                logger.warning(
                    "hash_chain.failed_load_state",
                    error=e,
                )

    def _save_state(self) -> None:
        """Save chain state to file."""
        if self._state_file:
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                data = {
                    "sequence": self._sequence,
                    "previous_hash": self._previous_hash,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self._state_file.write_text(json.dumps(data, indent=2))
            except Exception as e:
                logger.warning(
                    "hash_chain.failed_save_state",
                    error=e,
                )

    def add_integrity(self, entry: dict[str, Any]) -> dict[str, Any]:
        """
        Add integrity fields to a log entry.

        Args:
            entry: Log entry dictionary

        Returns:
            Entry with integrity fields added
        """
        with self._lock:
            self._sequence += 1

            # Add integrity info (without current_hash for now)
            entry["integrity"] = {
                "sequence": self._sequence,
                "previous_hash": self._previous_hash,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Compute hash of entry
            current_hash = compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash

            # Update state for next entry
            self._previous_hash = current_hash

            # Persist state periodically (every 10 entries)
            if self._sequence % 10 == 0:
                self._save_state()

            return entry

    def get_state(self) -> dict[str, Any]:
        """Get current chain state."""
        with self._lock:
            return {
                "sequence": self._sequence,
                "previous_hash": (
                    self._previous_hash[:16] + "..."
                    if len(self._previous_hash) > 16
                    else self._previous_hash
                ),
            }

    def reset(self) -> None:
        """Reset chain state (use with caution!)."""
        with self._lock:
            self._sequence = 0
            self._previous_hash = self.GENESIS_HASH
            if self._state_file:
                safe_unlink(self._state_file)
            logger.warning("hash_chain.chain_state_reset")


__all__ = ["HashChainManager"]
