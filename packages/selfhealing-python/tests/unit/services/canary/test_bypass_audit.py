"""
Phase 3: InterlockBypassAuditEntry, InterlockBypassAuditor 단위 테스트.

인터락 우회 감사 로깅 기능 테스트.

Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
"""

import pytest

from selfhealing.services.canary.bypass_audit import (
    InterlockBypassAuditEntry,
    InterlockBypassAuditor,
    get_interlock_bypass_auditor,
    reset_interlock_bypass_auditor,
)
from selfhealing.services.canary.override import (
    EmergencyOverrideRequest,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_auditor():
    """각 테스트 전후로 InterlockBypassAuditor 싱글톤 초기화."""
    reset_interlock_bypass_auditor()
    yield
    reset_interlock_bypass_auditor()


# =============================================================================
# Test: InterlockBypassAuditEntry
# =============================================================================


class TestInterlockBypassAuditEntry:
    """InterlockBypassAuditEntry 테스트."""

    def test_create_entry(self):
        """항목 생성."""
        entry = InterlockBypassAuditEntry(
            audit_id="audit-123",
            timestamp="2026-01-22T10:00:00+00:00",
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            operation="promote",
            emergency_level=3,
            emergency_level_name="LEVEL_3",
            namespace="production",
            bypassed_by="sre@example.com",
            bypass_reason="Critical hotfix",
            ticket_id="INC-12345",
        )

        assert entry.audit_id == "audit-123"
        assert entry.severity == "CRITICAL"
        assert entry.requires_incident_review is True
        assert entry.incident_review_due_hours == 48

    def test_to_dict(self):
        """딕셔너리 변환."""
        entry = InterlockBypassAuditEntry(
            audit_id="audit-123",
            timestamp="2026-01-22T10:00:00+00:00",
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            operation="promote",
            emergency_level=3,
            emergency_level_name="LEVEL_3",
            namespace="production",
            bypassed_by="sre@example.com",
            bypass_reason="Critical hotfix",
            acknowledged_risks=["risk1", "risk2"],
        )

        data = entry.to_dict()

        assert data["event_type"] == "DANGEROUS_BYPASS_INTERLOCK"
        assert data["severity"] == "CRITICAL"
        assert data["governance"]["requires_incident_review"] is True
        assert "review_due_at" in data["governance"]

    def test_default_acknowledged_risks_empty(self):
        """acknowledged_risks 기본값은 빈 리스트."""
        entry = InterlockBypassAuditEntry(
            audit_id="audit-123",
            timestamp="2026-01-22T10:00:00+00:00",
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            operation="promote",
            emergency_level=3,
            emergency_level_name="LEVEL_3",
            namespace="production",
            bypassed_by="sre@example.com",
            bypass_reason="Critical hotfix",
        )

        assert entry.acknowledged_risks == []


# =============================================================================
# Test: InterlockBypassAuditor
# =============================================================================


class TestInterlockBypassAuditor:
    """InterlockBypassAuditor 테스트."""

    def test_record_bypass(self):
        """우회 이벤트 기록."""
        auditor = InterlockBypassAuditor(enable_notifications=False)

        override = EmergencyOverrideRequest(
            reason="Critical hotfix for production",
            requested_by="sre@example.com",
            ticket_id="INC-12345",
            acknowledged_risks=["risk1"],
        )

        entry = auditor.record_bypass(
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            operation="promote",
            emergency_level=3,
            emergency_level_name="LEVEL_3",
            namespace="production",
            override=override,
        )

        assert entry.audit_id is not None
        assert entry.bypassed_by == "sre@example.com"
        assert entry.bypass_reason == "Critical hotfix for production"
        assert entry.ticket_id == "INC-12345"
        assert entry.requires_incident_review is True

    def test_get_entries(self):
        """기록된 항목 조회."""
        auditor = InterlockBypassAuditor(enable_notifications=False)

        override = EmergencyOverrideRequest(
            reason="Test override reason",
            requested_by="test@example.com",
        )

        auditor.record_bypass(
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            operation="promote",
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="test",
            override=override,
        )

        entries = auditor.get_entries()

        assert len(entries) == 1
        assert entries[0].rollout_id == "rollout-1"

    def test_get_entry_by_id(self):
        """ID로 항목 조회."""
        auditor = InterlockBypassAuditor(enable_notifications=False)

        override = EmergencyOverrideRequest(
            reason="Test override reason",
            requested_by="test@example.com",
        )

        entry = auditor.record_bypass(
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            operation="promote",
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="test",
            override=override,
        )

        found = auditor.get_entry(entry.audit_id)

        assert found is not None
        assert found.audit_id == entry.audit_id

    def test_get_entry_not_found(self):
        """존재하지 않는 ID 조회 시 None."""
        auditor = InterlockBypassAuditor(enable_notifications=False)

        found = auditor.get_entry("nonexistent")

        assert found is None

    def test_get_pending_reviews(self):
        """PIR 필요 항목 조회."""
        auditor = InterlockBypassAuditor(enable_notifications=False)

        override = EmergencyOverrideRequest(
            reason="Test override reason",
            requested_by="test@example.com",
        )

        auditor.record_bypass(
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            operation="promote",
            emergency_level=3,
            emergency_level_name="LEVEL_3",
            namespace="test",
            override=override,
        )

        pending = auditor.get_pending_reviews()

        assert len(pending) == 1
        assert pending[0].requires_incident_review is True

    def test_clear(self):
        """모든 항목 삭제."""
        auditor = InterlockBypassAuditor(enable_notifications=False)

        override = EmergencyOverrideRequest(
            reason="Test override reason",
            requested_by="test@example.com",
        )

        auditor.record_bypass(
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            operation="promote",
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="test",
            override=override,
        )

        auditor.clear()

        assert len(auditor.get_entries()) == 0


# =============================================================================
# Test: InterlockBypassAuditor Singleton
# =============================================================================


class TestInterlockBypassAuditorSingleton:
    """InterlockBypassAuditor 싱글톤 테스트."""

    def test_get_returns_same_instance(self):
        """get_interlock_bypass_auditor()가 동일 인스턴스 반환."""
        instance1 = get_interlock_bypass_auditor()
        instance2 = get_interlock_bypass_auditor()

        assert instance1 is instance2

    def test_reset_clears_singleton(self):
        """reset_interlock_bypass_auditor()가 싱글톤 초기화."""
        instance1 = get_interlock_bypass_auditor()
        reset_interlock_bypass_auditor()
        instance2 = get_interlock_bypass_auditor()

        assert instance1 is not instance2
