"""
Unit tests for IntegritySealer.

Tests:
- Postmortem 봉인 (무결성 필드 추가)
- 단일 레코드 검증
- 체인 검증
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestIntegritySealer:
    """IntegritySealer 테스트."""

    def test_seal_adds_integrity_fields(self):
        """봉인 시 무결성 필드 추가."""
        from selfhealing.services.postmortem.integrity_sealer import IntegritySealer

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            sealer = IntegritySealer(state_file=state_file)

            postmortem = {
                "incident_id": "AUTO-payment-20260128",
                "generated_at": "2026-01-28T12:00:00+00:00",
                "duration_seconds": 300,
            }

            sealed = sealer.seal(postmortem)

            assert "integrity_sequence" in sealed
            assert "integrity_prev_hash" in sealed
            assert "integrity_hash" in sealed
            assert "sealed_at" in sealed
            assert sealed["integrity_sequence"] == 1
            assert sealed["integrity_prev_hash"] == "GENESIS"

    def test_seal_increments_sequence(self):
        """연속 봉인 시 시퀀스 증가."""
        from selfhealing.services.postmortem.integrity_sealer import IntegritySealer

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            sealer = IntegritySealer(state_file=state_file)

            postmortem1 = {"incident_id": "AUTO-1", "data": "first"}
            postmortem2 = {"incident_id": "AUTO-2", "data": "second"}

            sealed1 = sealer.seal(postmortem1)
            sealed2 = sealer.seal(postmortem2)

            assert sealed1["integrity_sequence"] == 1
            assert sealed2["integrity_sequence"] == 2
            assert sealed2["integrity_prev_hash"] == sealed1["integrity_hash"]

    def test_seal_preserves_original_data(self):
        """봉인 시 원본 데이터 보존."""
        from selfhealing.services.postmortem.integrity_sealer import IntegritySealer

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            sealer = IntegritySealer(state_file=state_file)

            postmortem = {
                "incident_id": "AUTO-test",
                "summary": {"affected_services": ["payment"]},
                "duration_seconds": 600,
            }

            sealed = sealer.seal(postmortem)

            assert sealed["incident_id"] == "AUTO-test"
            assert sealed["summary"]["affected_services"] == ["payment"]
            assert sealed["duration_seconds"] == 600

    def test_get_chain_state_returns_state(self):
        """체인 상태 조회."""
        from selfhealing.services.postmortem.integrity_sealer import IntegritySealer

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            sealer = IntegritySealer(state_file=state_file)

            # 초기 상태
            state = sealer.get_chain_state()
            assert state["sequence"] == 0

            # 봉인 후
            sealer.seal({"incident_id": "test"})
            state = sealer.get_chain_state()
            assert state["sequence"] == 1

    def test_reset_clears_chain(self):
        """체인 초기화."""
        from selfhealing.services.postmortem.integrity_sealer import IntegritySealer

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "chain_state.json"
            sealer = IntegritySealer(state_file=state_file)

            sealer.seal({"incident_id": "test"})
            sealer.reset()

            state = sealer.get_chain_state()
            assert state["sequence"] == 0


class TestIntegritySealerSingleton:
    """IntegritySealer 싱글톤 테스트."""

    def test_get_integrity_sealer_returns_singleton(self):
        """싱글톤 인스턴스 반환."""
        from selfhealing.services.postmortem.integrity_sealer import (
            get_integrity_sealer,
            reset_integrity_sealer,
        )

        reset_integrity_sealer()

        sealer1 = get_integrity_sealer()
        sealer2 = get_integrity_sealer()

        assert sealer1 is sealer2

    def test_reset_integrity_sealer_clears_singleton(self):
        """싱글톤 초기화."""
        from selfhealing.services.postmortem.integrity_sealer import (
            get_integrity_sealer,
            reset_integrity_sealer,
        )

        sealer1 = get_integrity_sealer()
        reset_integrity_sealer()
        sealer2 = get_integrity_sealer()

        assert sealer1 is not sealer2
