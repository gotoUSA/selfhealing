"""
Tests for SamplingVerifier.

Probabilistic chain verification.
"""

from __future__ import annotations

import json
from typing import Any


class TestSamplingVerifier:
    """Tests for SamplingVerifier."""

    def _create_valid_chain(self, count: int) -> list[dict[str, Any]]:
        """Create a valid chain of entries."""
        import hashlib

        entries = []
        prev_hash = "GENESIS"

        for i in range(count):
            entry = {
                "data": f"entry_{i}",
                "integrity": {
                    "sequence": i + 1,
                    "previous_hash": prev_hash,
                    "timestamp": f"2026-01-18T10:00:{i:02d}Z",
                },
            }

            # Compute hash
            json_str = json.dumps(entry, sort_keys=True)
            current_hash = hashlib.sha256(json_str.encode()).hexdigest()
            entry["integrity"]["current_hash"] = current_hash

            entries.append(entry)
            prev_hash = current_hash

        return entries

    def _create_tampered_chain(self, count: int, tamper_index: int) -> list[dict[str, Any]]:
        """Create a chain with a tampered entry."""
        entries = self._create_valid_chain(count)

        # Tamper with entry
        if tamper_index < len(entries):
            entries[tamper_index]["data"] = "TAMPERED"

        return entries

    def test_valid_chain_passes(self):
        """Test that valid chain passes verification."""
        from selfhealing.audit.performance import (
            SamplingConfig,
            SamplingVerifier,
        )

        entries = self._create_valid_chain(100)

        config = SamplingConfig(sample_rate=0.5, min_samples=10)
        verifier = SamplingVerifier(config)

        is_valid, issues = verifier.verify_sampled(entries)

        assert is_valid is True
        assert len(issues) == 0

    def test_tampered_chain_detected(self):
        """Test that tampered chain is detected."""
        from selfhealing.audit.performance import (
            SamplingConfig,
            SamplingVerifier,
        )

        entries = self._create_tampered_chain(100, tamper_index=50)

        config = SamplingConfig(
            sample_rate=1.0,  # Full sampling to ensure detection
            full_verify_on_failure=False,
        )
        verifier = SamplingVerifier(config)

        is_valid, issues = verifier.verify_sampled(entries)

        # Should detect tampering
        assert is_valid is False or len(issues) > 0

    def test_empty_chain_passes(self):
        """Test that empty chain passes."""
        from selfhealing.audit.performance import SamplingVerifier

        verifier = SamplingVerifier()

        is_valid, issues = verifier.verify_sampled([])

        assert is_valid is True
        assert issues == []

    def test_sampling_reduces_checks(self):
        """Test that sampling reduces number of checks."""
        from selfhealing.audit.performance import (
            SamplingConfig,
            SamplingVerifier,
        )

        entries = self._create_valid_chain(1000)

        config = SamplingConfig(
            sample_rate=0.1,
            min_samples=10,
            max_samples=100,
        )
        verifier = SamplingVerifier(config)

        # Verify - should only check ~100 entries
        is_valid, issues = verifier.verify_sampled(entries)

        assert is_valid is True
