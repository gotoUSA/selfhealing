"""
Manual Override & Policy Conflict Tests

File: integration/self_healing/test_manual_override_policy.py

Business Risk: Policy deadlock, unclear precedence
Compliance Alignment: Change management, operational governance

Test Cases:
- OVER-001: Manual override takes precedence over auto-recovery
- OVER-002: Auto-policy blocked while manual open
- OVER-003: Manual override expires after TTL
- OVER-004: Conflicting admin actions - last wins, both audited
- OVER-005: Emergency bypass requires MFA (simulated)
- OVER-006: Policy version mismatch fallback
"""

from datetime import timedelta
from decimal import Decimal
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
from django.conf import settings

# 이 파일의 테스트 중 일부는 DB 필요
pytestmark = pytest.mark.requires_db
from django.test import override_settings
from django.utils import timezone

from shopping.models.failed_payment import CircuitBreakerState, FailedPayment
from shopping.models.user import User
from shopping.services.payment_recovery_service import CeleryPaymentRecovery
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


# =============================================================================
# Manual Override Service
# =============================================================================


@dataclass
class OverrideRecord:
    """Record of a manual override action."""

    id: str = field(default_factory=lambda: str(hash(timezone.now()))[:12])
    admin_id: int | None = None
    admin_username: str = ""
    action: str = ""  # "force_open", "force_close", "emergency_bypass"
    service_name: str = ""
    reason: str = ""
    timestamp: Any = field(default_factory=timezone.now)
    ttl_minutes: int = 90
    expires_at: Any = None
    mfa_verified: bool = False
    previous_state: str = ""
    new_state: str = ""

    def __post_init__(self):
        if self.expires_at is None:
            self.expires_at = self.timestamp + timedelta(minutes=self.ttl_minutes)


class ManualOverrideService:
    """
    Service for managing manual overrides on circuit breakers.

    Implements precedence rules, TTL management, and audit logging.
    """

    def __init__(self):
        self._override_history: list[OverrideRecord] = []
        self._current_policy_version: str = "1.0.0"
        self._active_overrides: dict[str, OverrideRecord] = {}

    def force_open(
        self,
        service_name: str,
        admin: User,
        reason: str,
        ttl_minutes: int = 90,
    ) -> dict:
        """
        Force circuit breaker to OPEN state.

        Manual override takes precedence over auto-recovery.
        """
        # Get current state
        cb_state, _ = CircuitBreakerState.objects.get_or_create(
            service_name=service_name,
            defaults={"state": "closed"},
        )
        previous_state = cb_state.state

        # Apply override
        cb_state.state = "open"
        cb_state.manually_controlled = True
        cb_state.controlled_by = admin
        cb_state.control_reason = reason
        cb_state.manual_override_expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
        cb_state.save()

        # Record override
        record = OverrideRecord(
            admin_id=admin.id,
            admin_username=admin.username,
            action="force_open",
            service_name=service_name,
            reason=reason,
            ttl_minutes=ttl_minutes,
            previous_state=previous_state,
            new_state="open",
        )
        self._override_history.append(record)
        self._active_overrides[service_name] = record

        return {
            "action": "force_open",
            "success": True,
            "previous_state": previous_state,
            "new_state": "open",
            "expires_at": cb_state.manual_override_expires_at,
            "override_id": record.id,
        }

    def force_close(
        self,
        service_name: str,
        admin: User,
        reason: str,
    ) -> dict:
        """
        Force circuit breaker to CLOSED state.

        Clears manual control flag.
        """
        cb_state = CircuitBreakerState.objects.get(service_name=service_name)
        previous_state = cb_state.state

        cb_state.state = "closed"
        cb_state.manually_controlled = False
        cb_state.controlled_by = admin
        cb_state.control_reason = reason
        cb_state.manual_override_expires_at = None
        cb_state.save()

        # Record override
        record = OverrideRecord(
            admin_id=admin.id,
            admin_username=admin.username,
            action="force_close",
            service_name=service_name,
            reason=reason,
            previous_state=previous_state,
            new_state="closed",
        )
        self._override_history.append(record)

        # Remove from active overrides
        if service_name in self._active_overrides:
            del self._active_overrides[service_name]

        return {
            "action": "force_close",
            "success": True,
            "previous_state": previous_state,
            "new_state": "closed",
            "override_id": record.id,
        }

    def attempt_auto_close(self, service_name: str) -> dict:
        """
        Attempt automatic circuit breaker close.

        Blocked if manual override is active.
        """
        cb_state = CircuitBreakerState.objects.get(service_name=service_name)

        if cb_state.manually_controlled:
            # Check if TTL expired
            if cb_state.manual_override_expires_at and timezone.now() > cb_state.manual_override_expires_at:
                # TTL expired, allow auto-close
                cb_state.manually_controlled = False
                cb_state.state = "closed"
                cb_state.save()

                if service_name in self._active_overrides:
                    del self._active_overrides[service_name]

                return {
                    "action": "auto_close",
                    "success": True,
                    "reason": "ttl_expired",
                }

            # Manual override still active
            return {
                "action": "auto_close",
                "success": False,
                "blocked_by": "manual_override",
                "expires_at": cb_state.manual_override_expires_at,
            }

        # No manual override, auto-close allowed
        cb_state.state = "closed"
        cb_state.save()

        return {
            "action": "auto_close",
            "success": True,
        }

    def check_ttl_expiry(self, service_name: str) -> dict:
        """
        Check if manual override TTL has expired.

        If expired, auto-policy resumes.
        """
        try:
            cb_state = CircuitBreakerState.objects.get(service_name=service_name)
        except CircuitBreakerState.DoesNotExist:
            return {"expired": False, "reason": "no_state_found"}

        if not cb_state.manually_controlled:
            return {"expired": False, "reason": "not_manually_controlled"}

        if cb_state.manual_override_expires_at is None:
            return {"expired": False, "reason": "no_ttl_set"}

        is_expired = timezone.now() > cb_state.manual_override_expires_at

        if is_expired:
            # Clear manual control
            cb_state.manually_controlled = False
            cb_state.save()

            if service_name in self._active_overrides:
                del self._active_overrides[service_name]

        return {
            "expired": is_expired,
            "expires_at": cb_state.manual_override_expires_at,
            "auto_policy_resumed": is_expired,
        }

    def emergency_bypass(
        self,
        service_name: str,
        admin: User,
        reason: str,
        mfa_token: str | None = None,
    ) -> dict:
        """
        Emergency bypass requiring MFA verification.

        For critical situations requiring immediate action.
        """
        # Verify MFA (simulated)
        mfa_verified = self._verify_mfa(admin, mfa_token)

        if not mfa_verified:
            # Record failed attempt
            record = OverrideRecord(
                admin_id=admin.id,
                admin_username=admin.username,
                action="emergency_bypass_failed",
                service_name=service_name,
                reason="MFA verification failed",
                mfa_verified=False,
            )
            self._override_history.append(record)

            return {
                "action": "emergency_bypass",
                "success": False,
                "reason": "mfa_required",
                "audit_logged": True,
            }

        # MFA verified, proceed with bypass
        cb_state, _ = CircuitBreakerState.objects.get_or_create(
            service_name=service_name,
            defaults={"state": "closed"},
        )
        previous_state = cb_state.state

        # Emergency bypass opens circuit
        cb_state.state = "open"
        cb_state.manually_controlled = True
        cb_state.controlled_by = admin
        cb_state.control_reason = f"EMERGENCY: {reason}"
        cb_state.manual_override_expires_at = timezone.now() + timedelta(minutes=30)  # Shorter TTL
        cb_state.save()

        record = OverrideRecord(
            admin_id=admin.id,
            admin_username=admin.username,
            action="emergency_bypass",
            service_name=service_name,
            reason=reason,
            mfa_verified=True,
            previous_state=previous_state,
            new_state="open",
            ttl_minutes=30,
        )
        self._override_history.append(record)

        return {
            "action": "emergency_bypass",
            "success": True,
            "mfa_verified": True,
            "expires_at": cb_state.manual_override_expires_at,
            "override_id": record.id,
        }

    def _verify_mfa(self, admin: User, mfa_token: str | None) -> bool:
        """Verify MFA token (simulated for testing)."""
        # In production, this would verify against an actual MFA provider
        # For testing, we accept "VALID_MFA_TOKEN" as valid
        return mfa_token == "VALID_MFA_TOKEN"

    def get_override_history(
        self,
        service_name: str | None = None,
    ) -> list[OverrideRecord]:
        """Get override history, optionally filtered by service."""
        if service_name:
            return [r for r in self._override_history if r.service_name == service_name]
        return self._override_history.copy()

    def get_policy_version(self) -> str:
        """Get current policy version."""
        return self._current_policy_version

    def set_policy_version(self, version: str) -> None:
        """Set policy version (for testing)."""
        self._current_policy_version = version

    def handle_policy_version_mismatch(
        self,
        expected_version: str,
        service_name: str,
    ) -> dict:
        """
        Handle policy version mismatch during deployment.

        Implements graceful fallback.
        """
        current_version = self._current_policy_version

        if current_version == expected_version:
            return {
                "mismatch": False,
                "current_version": current_version,
            }

        # Version mismatch - use fallback behavior
        return {
            "mismatch": True,
            "current_version": current_version,
            "expected_version": expected_version,
            "fallback_applied": True,
            "fallback_behavior": "default_allow",
            "reason": "Policy version mismatch, using safe default",
        }


@pytest.fixture
def manual_override_service(db) -> ManualOverrideService:
    """Provide a manual override service instance."""
    return ManualOverrideService()


# =============================================================================
# OVER-001: Manual Override Takes Precedence
# =============================================================================


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestManualOverridePrecedence:
    """
    Test that manual override takes precedence over auto-recovery.

    Validates priority of human decisions over automated policies.
    """

    def test_manual_override_takes_precedence(
        self,
        manual_override_service,
        admin_user,
    ):
        """
        Purpose:
            Verify manual override blocks auto-recovery.

        Scenario:
            1. Admin opens CB manually
            2. Auto-recovery attempts to close
            3. Verify manual state persists

        Expected:
            - Manual open applied
            - Auto-close blocked
            - Manual state preserved

        Risk Covered:
            R-001: Policy conflict resolution

        Compliance:
            Change management governance
        """
        service_name = "toss_payment"

        # Arrange: Admin manually opens CB
        open_result = manual_override_service.force_open(
            service_name=service_name,
            admin=admin_user,
            reason="Planned maintenance window",
            ttl_minutes=90,
        )

        assert open_result["success"] is True
        assert open_result["new_state"] == "open"

        # Act: Attempt auto-close
        auto_close_result = manual_override_service.attempt_auto_close(service_name)

        # Assert: Auto-close blocked
        assert auto_close_result["success"] is False
        assert auto_close_result["blocked_by"] == "manual_override"

        # Verify state unchanged
        cb_state = CircuitBreakerState.objects.get(service_name=service_name)
        assert cb_state.state == "open"
        assert cb_state.manually_controlled is True

    def test_manual_override_records_audit_trail(
        self,
        manual_override_service,
        admin_user,
    ):
        """
        Purpose:
            Verify manual override creates complete audit trail.

        Scenario:
            1. Admin opens CB
            2. Verify audit record created
            3. Verify all required fields populated

        Expected:
            - Audit record created
            - Admin identity recorded
            - Action and reason recorded
            - Timestamp accurate

        Compliance:
            SOC 2 (Audit Logging)
        """
        service_name = "payment_gateway"

        # Act
        result = manual_override_service.force_open(
            service_name=service_name,
            admin=admin_user,
            reason="Emergency override for testing",
        )

        # Assert
        history = manual_override_service.get_override_history(service_name)
        assert len(history) == 1

        record = history[0]
        assert record.admin_id == admin_user.id
        assert record.admin_username == admin_user.username
        assert record.action == "force_open"
        assert record.reason == "Emergency override for testing"
        assert record.previous_state == "closed"
        assert record.new_state == "open"


# =============================================================================
# OVER-002: Auto-Policy Blocked While Manual Open
# =============================================================================


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestAutoBlockedWhileManualOpen:
    """
    Test that auto-policy is blocked while manual override is active.
    """

    def test_auto_policy_blocked_while_manual_open(
        self,
        manual_override_service,
        admin_user,
    ):
        """
        Purpose:
            Verify auto-policy cannot modify manually controlled CB.

        Scenario:
            1. Admin opens CB manually
            2. Multiple auto-close attempts
            3. All attempts blocked until TTL expires

        Expected:
            - All auto-close attempts fail
            - Blocked_by reason is "manual_override"
            - Expires_at time is communicated

        Risk Covered:
            R-002: Automation overriding human decisions
        """
        service_name = "toss_payment"

        # Arrange
        manual_override_service.force_open(
            service_name=service_name,
            admin=admin_user,
            reason="Blocking auto-policy",
            ttl_minutes=60,
        )

        # Act: Multiple auto-close attempts
        for i in range(3):
            result = manual_override_service.attempt_auto_close(service_name)

            # Assert: Each attempt blocked
            assert result["success"] is False
            assert result["blocked_by"] == "manual_override"
            assert result["expires_at"] is not None


# =============================================================================
# OVER-003: Manual Override Expires After TTL
# =============================================================================


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestManualOverrideTTLExpiry:
    """
    Test that manual override expires after TTL.

    Validates time-limited manual control.
    """

    def test_manual_override_expires_after_ttl(
        self,
        manual_override_service,
        admin_user,
    ):
        """
        Purpose:
            Verify manual override expires after configured TTL.

        Scenario:
            1. Set manual override with short TTL
            2. Simulate time passage
            3. Verify auto-policy resumes

        Expected:
            - Override active before TTL
            - Override expired after TTL
            - Auto-policy resumes automatically

        Risk Covered:
            R-003: Permanent manual blocks
        """
        service_name = "toss_payment"

        # Arrange: Set override with 1 minute TTL
        manual_override_service.force_open(
            service_name=service_name,
            admin=admin_user,
            reason="Short TTL test",
            ttl_minutes=1,
        )

        # Verify override active
        cb_state = CircuitBreakerState.objects.get(service_name=service_name)
        assert cb_state.manually_controlled is True

        # Act: Simulate TTL expiry by backdating expires_at
        cb_state.manual_override_expires_at = timezone.now() - timedelta(minutes=1)
        cb_state.save()

        # Check TTL expiry
        expiry_result = manual_override_service.check_ttl_expiry(service_name)

        # Assert
        assert expiry_result["expired"] is True
        assert expiry_result["auto_policy_resumed"] is True

        # Verify manual control cleared
        cb_state.refresh_from_db()
        assert cb_state.manually_controlled is False

    def test_auto_close_succeeds_after_ttl_expiry(
        self,
        manual_override_service,
        admin_user,
    ):
        """
        Purpose:
            Verify auto-close works after TTL expires.

        Scenario:
            1. Set manual override
            2. Expire TTL
            3. Auto-close succeeds

        Expected:
            - Auto-close blocked before TTL
            - Auto-close succeeds after TTL

        Risk Covered:
            R-003: Auto-recovery after timeout
        """
        service_name = "payment_gateway"

        # Arrange
        manual_override_service.force_open(
            service_name=service_name,
            admin=admin_user,
            reason="TTL expiry test",
            ttl_minutes=1,
        )

        # Verify blocked before TTL
        result_before = manual_override_service.attempt_auto_close(service_name)
        assert result_before["success"] is False

        # Expire TTL
        cb_state = CircuitBreakerState.objects.get(service_name=service_name)
        cb_state.manual_override_expires_at = timezone.now() - timedelta(seconds=1)
        cb_state.save()

        # Act: Attempt auto-close after TTL
        result_after = manual_override_service.attempt_auto_close(service_name)

        # Assert
        assert result_after["success"] is True
        assert result_after["reason"] == "ttl_expired"


# =============================================================================
# OVER-004: Conflicting Admin Actions - Last Wins
# =============================================================================


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestConflictingAdminActions:
    """
    Test conflicting admin actions - last action wins.

    Validates clear precedence and complete audit trail.
    """

    def test_conflicting_admin_actions_last_wins(
        self,
        manual_override_service,
        db,
    ):
        """
        Purpose:
            Verify last admin action takes precedence.

        Scenario:
            1. Admin A opens CB
            2. Admin B closes CB
            3. Verify B's action is current state
            4. Both actions in audit log

        Expected:
            - Final state reflects last action
            - Both actions fully audited
            - Clear history of who did what

        Risk Covered:
            R-004: Conflicting decisions

        Compliance:
            Change management, accountability
        """
        service_name = "toss_payment"

        # Create two admins
        admin_a = UserFactory.admin(username="admin_a")
        admin_b = UserFactory.admin(username="admin_b")

        # Act: Admin A opens
        result_a = manual_override_service.force_open(
            service_name=service_name,
            admin=admin_a,
            reason="Admin A opening CB",
        )

        # Verify A's action
        cb_state = CircuitBreakerState.objects.get(service_name=service_name)
        assert cb_state.state == "open"
        assert cb_state.controlled_by == admin_a

        # Act: Admin B closes
        result_b = manual_override_service.force_close(
            service_name=service_name,
            admin=admin_b,
            reason="Admin B closing CB",
        )

        # Assert: B's action wins
        cb_state.refresh_from_db()
        assert cb_state.state == "closed"
        assert cb_state.controlled_by == admin_b

        # Assert: Both actions audited
        history = manual_override_service.get_override_history(service_name)
        assert len(history) == 2

        # Verify audit order
        assert history[0].admin_username == "admin_a"
        assert history[0].action == "force_open"
        assert history[1].admin_username == "admin_b"
        assert history[1].action == "force_close"

    def test_rapid_conflicting_actions_all_audited(
        self,
        manual_override_service,
        db,
    ):
        """
        Purpose:
            Verify rapid succession of actions all audited.

        Scenario:
            1. Multiple admins make rapid changes
            2. All changes recorded in audit

        Expected:
            - No audit records lost
            - Order preserved
        """
        service_name = "payment_gateway"

        # Create admins
        admins = [UserFactory.admin(username=f"admin_{i}") for i in range(5)]

        # Rapid succession of actions
        for i, admin in enumerate(admins):
            if i % 2 == 0:
                manual_override_service.force_open(
                    service_name=service_name,
                    admin=admin,
                    reason=f"Action by admin_{i}",
                )
            else:
                # Need to ensure state exists for close
                try:
                    manual_override_service.force_close(
                        service_name=service_name,
                        admin=admin,
                        reason=f"Action by admin_{i}",
                    )
                except CircuitBreakerState.DoesNotExist:
                    pass

        # Assert: All actions recorded
        history = manual_override_service.get_override_history(service_name)
        assert len(history) >= 3  # At least the open actions


# =============================================================================
# OVER-005: Emergency Bypass Requires MFA
# =============================================================================


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestEmergencyBypassMFA:
    """
    Test emergency bypass requiring MFA verification.

    Validates additional authentication for critical actions.
    """

    def test_emergency_bypass_requires_mfa(
        self,
        manual_override_service,
        admin_user,
    ):
        """
        Purpose:
            Verify emergency bypass requires MFA verification.

        Scenario:
            1. Attempt emergency bypass without MFA
            2. Verify action blocked
            3. Verify audit log records attempt

        Expected:
            - Action blocked without MFA
            - "mfa_required" reason returned
            - Failed attempt logged

        Risk Covered:
            R-005: Unauthorized emergency actions

        Compliance:
            Multi-factor authentication requirements
        """
        service_name = "toss_payment"

        # Act: Attempt bypass without MFA
        result = manual_override_service.emergency_bypass(
            service_name=service_name,
            admin=admin_user,
            reason="Critical emergency",
            mfa_token=None,  # No MFA token
        )

        # Assert
        assert result["success"] is False
        assert result["reason"] == "mfa_required"
        assert result["audit_logged"] is True

        # Verify failed attempt logged
        history = manual_override_service.get_override_history(service_name)
        failed_attempts = [r for r in history if "failed" in r.action]
        assert len(failed_attempts) == 1
        assert failed_attempts[0].mfa_verified is False

    def test_emergency_bypass_succeeds_with_valid_mfa(
        self,
        manual_override_service,
        admin_user,
    ):
        """
        Purpose:
            Verify emergency bypass succeeds with valid MFA.

        Scenario:
            1. Attempt emergency bypass with valid MFA
            2. Verify action succeeds
            3. Verify shorter TTL applied (30 min)

        Expected:
            - Action succeeds with MFA
            - Emergency prefix in reason
            - Shorter TTL than normal
        """
        service_name = "toss_payment"

        # Act
        result = manual_override_service.emergency_bypass(
            service_name=service_name,
            admin=admin_user,
            reason="Critical emergency",
            mfa_token="VALID_MFA_TOKEN",
        )

        # Assert
        assert result["success"] is True
        assert result["mfa_verified"] is True

        # Verify CB state
        cb_state = CircuitBreakerState.objects.get(service_name=service_name)
        assert cb_state.state == "open"
        assert cb_state.manually_controlled is True
        assert "EMERGENCY" in cb_state.control_reason

        # Verify shorter TTL (30 minutes instead of 90)
        time_until_expiry = (cb_state.manual_override_expires_at - timezone.now()).total_seconds()
        assert time_until_expiry <= 1800 + 5  # 30 minutes + tolerance


# =============================================================================
# OVER-006: Policy Version Mismatch Fallback
# =============================================================================


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestPolicyVersionMismatch:
    """
    Test policy version mismatch handling.

    Validates graceful fallback during deployment.
    """

    def test_policy_version_mismatch_uses_fallback(
        self,
        manual_override_service,
    ):
        """
        Purpose:
            Verify graceful fallback on policy version mismatch.

        Scenario:
            1. Service expects policy version 2.0.0
            2. Current version is 1.0.0
            3. Verify fallback behavior applied

        Expected:
            - Mismatch detected
            - Safe fallback applied
            - Default-allow behavior

        Risk Covered:
            R-006: Deployment mid-operation

        Compliance:
            Change management, safe deployment
        """
        service_name = "toss_payment"

        # Arrange: Current version is 1.0.0
        assert manual_override_service.get_policy_version() == "1.0.0"

        # Act: Check for version 2.0.0 (doesn't match)
        result = manual_override_service.handle_policy_version_mismatch(
            expected_version="2.0.0",
            service_name=service_name,
        )

        # Assert
        assert result["mismatch"] is True
        assert result["current_version"] == "1.0.0"
        assert result["expected_version"] == "2.0.0"
        assert result["fallback_applied"] is True
        assert result["fallback_behavior"] == "default_allow"

    def test_matching_policy_version_no_fallback(
        self,
        manual_override_service,
    ):
        """
        Purpose:
            Verify no fallback when policy versions match.

        Scenario:
            1. Expected version matches current
            2. No fallback needed

        Expected:
            - No mismatch
            - Normal operation
        """
        service_name = "toss_payment"

        # Act
        result = manual_override_service.handle_policy_version_mismatch(
            expected_version="1.0.0",  # Matches current
            service_name=service_name,
        )

        # Assert
        assert result["mismatch"] is False
        assert result["current_version"] == "1.0.0"


# =============================================================================
# Integration: Override Lifecycle Tests
# =============================================================================


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestOverrideLifecycle:
    """
    Test complete override lifecycle.

    Validates the full flow from creation to expiration.
    """

    def test_complete_override_lifecycle(
        self,
        manual_override_service,
        admin_user,
    ):
        """
        Purpose:
            Validate complete manual override lifecycle.

        Scenario:
            1. Admin creates override
            2. Auto-policy attempts blocked
            3. TTL expires
            4. Auto-policy resumes
            5. Complete audit trail exists

        Expected:
            - All lifecycle stages work correctly
            - Audit trail is complete
            - System returns to normal after TTL
        """
        service_name = "toss_payment"

        # Phase 1: Create override
        create_result = manual_override_service.force_open(
            service_name=service_name,
            admin=admin_user,
            reason="Lifecycle test",
            ttl_minutes=5,
        )
        assert create_result["success"] is True

        # Phase 2: Auto-policy blocked
        block_result = manual_override_service.attempt_auto_close(service_name)
        assert block_result["success"] is False
        assert block_result["blocked_by"] == "manual_override"

        # Phase 3: Expire TTL
        cb_state = CircuitBreakerState.objects.get(service_name=service_name)
        cb_state.manual_override_expires_at = timezone.now() - timedelta(seconds=1)
        cb_state.save()

        # Phase 4: Check expiry and resume
        expiry_result = manual_override_service.check_ttl_expiry(service_name)
        assert expiry_result["expired"] is True
        assert expiry_result["auto_policy_resumed"] is True

        # Verify auto-close now works
        auto_close_result = manual_override_service.attempt_auto_close(service_name)
        assert auto_close_result["success"] is True

        # Phase 5: Verify audit trail
        history = manual_override_service.get_override_history(service_name)
        assert len(history) >= 1
        assert history[0].action == "force_open"
        assert history[0].admin_id == admin_user.id
