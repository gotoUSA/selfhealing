"""
Hash Chain Integrity Verification.

Implements tamper-evident logging using cryptographic hash chains.
Each log entry includes the hash of the previous entry, making it
impossible to delete or modify entries without breaking the chain.

This is similar to blockchain technology but optimized for audit logs.
"""

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class IntegrityInfo:
    """Integrity information for a log entry."""

    sequence: int
    previous_hash: str
    current_hash: str
    timestamp: str


def compute_hash(data: Dict[str, Any]) -> str:
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


class HashChainVerifier:
    """
    Verifies the integrity of a hash chain.

    Can detect:
    - Deleted entries (missing sequence numbers)
    - Modified entries (hash mismatch)
    - Reordered entries (previous_hash mismatch)
    """

    GENESIS_HASH = "GENESIS"

    def verify_chain(self, entries: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """
        Verify the integrity of an audit log chain.

        Args:
            entries: List of log entries with integrity fields

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not entries:
            return True, None

        previous_hash = self.GENESIS_HASH
        expected_sequence = 1

        for i, entry in enumerate(entries):
            # Check sequence continuity
            seq = entry.get("integrity", {}).get("sequence", 0)
            if seq != expected_sequence:
                return False, f"Missing entry: expected sequence {expected_sequence}, found {seq}"

            # Check previous hash linkage
            prev_hash = entry.get("integrity", {}).get("previous_hash", "")
            if prev_hash != previous_hash:
                return False, f"Chain broken at sequence {seq}: previous_hash mismatch"

            # Verify current hash
            stored_hash = entry.get("integrity", {}).get("current_hash", "")
            entry_copy = self._remove_current_hash(entry)
            computed_hash = compute_hash(entry_copy)

            if stored_hash != computed_hash:
                return False, f"Entry modified at sequence {seq}: hash mismatch"

            previous_hash = stored_hash
            expected_sequence += 1

        return True, None

    def _remove_current_hash(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Remove current_hash from entry for hash verification."""
        entry_copy = json.loads(json.dumps(entry))  # Deep copy
        if "integrity" in entry_copy and "current_hash" in entry_copy["integrity"]:
            del entry_copy["integrity"]["current_hash"]
        return entry_copy

    def find_tampering(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Find all tampered or missing entries in a chain.

        Returns:
            List of issues found
        """
        issues = []

        if not entries:
            return issues

        previous_hash = self.GENESIS_HASH
        expected_sequence = 1

        for entry in entries:
            seq = entry.get("integrity", {}).get("sequence", 0)

            # Check for missing entries
            while expected_sequence < seq:
                issues.append(
                    {
                        "type": "missing_entry",
                        "sequence": expected_sequence,
                        "message": f"Entry {expected_sequence} is missing from the chain",
                    }
                )
                expected_sequence += 1

            # Check previous hash
            prev_hash = entry.get("integrity", {}).get("previous_hash", "")
            if prev_hash != previous_hash:
                issues.append(
                    {
                        "type": "chain_broken",
                        "sequence": seq,
                        "expected_previous_hash": previous_hash,
                        "found_previous_hash": prev_hash,
                        "message": f"Chain broken at entry {seq}",
                    }
                )

            # Verify current hash
            stored_hash = entry.get("integrity", {}).get("current_hash", "")
            entry_copy = self._remove_current_hash(entry)
            computed_hash = compute_hash(entry_copy)

            if stored_hash != computed_hash:
                issues.append(
                    {
                        "type": "entry_modified",
                        "sequence": seq,
                        "stored_hash": stored_hash[:16] + "...",
                        "computed_hash": computed_hash[:16] + "...",
                        "message": f"Entry {seq} has been modified",
                    }
                )

            previous_hash = stored_hash
            expected_sequence = seq + 1

        return issues


class HashChainManager:
    """
    Manages hash chain state for audit logging.

    Thread-safe manager that maintains:
    - Current sequence number
    - Previous hash for chaining
    - Periodic checkpoints
    """

    GENESIS_HASH = "GENESIS"

    def __init__(self, state_file: Optional[Path] = None):
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
                logger.debug(f"[HashChain] Loaded state: seq={self._sequence}")
            except Exception as e:
                logger.warning(f"[HashChain] Failed to load state: {e}")

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
                logger.warning(f"[HashChain] Failed to save state: {e}")

    def add_integrity(self, entry: Dict[str, Any]) -> Dict[str, Any]:
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

    def get_state(self) -> Dict[str, Any]:
        """Get current chain state."""
        with self._lock:
            return {
                "sequence": self._sequence,
                "previous_hash": self._previous_hash[:16] + "..." if len(self._previous_hash) > 16 else self._previous_hash,
            }

    def reset(self) -> None:
        """Reset chain state (use with caution!)."""
        with self._lock:
            self._sequence = 0
            self._previous_hash = self.GENESIS_HASH
            if self._state_file and self._state_file.exists():
                self._state_file.unlink()
            logger.warning("[HashChain] Chain state reset")


def verify_audit_log_integrity(log_file: Path) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Verify the integrity of an audit log file.

    Args:
        log_file: Path to the JSON Lines audit log file

    Returns:
        Tuple of (is_valid, issues_list)
    """
    if not log_file.exists():
        return True, []

    entries = []
    try:
        with open(log_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception as e:
        return False, [{"type": "read_error", "message": str(e)}]

    verifier = HashChainVerifier()
    issues = verifier.find_tampering(entries)

    return len(issues) == 0, issues
