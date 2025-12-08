"""
Unit Tests for SLA Timer Policy

Tests for SLA threshold configuration, breach detection,
and domain-specific timeout policies.

Reference: docs/testing/SELF_HEALING_TEST_SPECIFICATIONS.md
Risk Covered: R-012 (SLA breach undetected)
Compliance: SOC 2 (Availability & Monitoring)
"""

from datetime import timedelta

import pytest

from shopping.services.self_healing.config import SLAThresholds


@pytest.mark.tier1
class TestSLAThresholdDefaults:
    """
    Tests for default SLA threshold configuration.

    Purpose:
        Verify that default SLA thresholds match documented policy.

    Compliance:
        SOC 2 CC7.2 (Monitoring) - SLA tracking
    """

    def test_payment_sla_is_1_hour(self):
        """
        Purpose:
            Verify payment domain has strictest SLA (1 hour).

        Expected:
            - payment_hours = 1

        Risk Covered:
            R-012: SLA breach detection
        """
        thresholds = SLAThresholds()
        assert thresholds.payment_hours == 1, (
            "Policy Violation: Payment SLA must be 1 hour (strictest). "
            "Payment failures have immediate revenue impact."
        )

    def test_point_sla_is_4_hours(self):
        """
        Purpose:
            Verify point domain SLA matches policy.

        Expected:
            - point_hours = 4
        """
        thresholds = SLAThresholds()
        assert thresholds.point_hours == 4, (
            "Policy Violation: Point SLA must be 4 hours. "
            "Check SLA.POINT_HOURS configuration."
        )

    def test_inventory_sla_is_2_hours(self):
        """
        Purpose:
            Verify inventory domain SLA matches policy.

        Expected:
            - inventory_hours = 2
        """
        thresholds = SLAThresholds()
        assert thresholds.inventory_hours == 2, (
            "Policy Violation: Inventory SLA must be 2 hours. "
            "Check SLA.INVENTORY_HOURS configuration."
        )

    def test_webhook_sla_is_8_hours(self):
        """
        Purpose:
            Verify webhook domain SLA matches policy.

        Expected:
            - webhook_hours = 8
        """
        thresholds = SLAThresholds()
        assert thresholds.webhook_hours == 8, (
            "Policy Violation: Webhook SLA must be 8 hours. "
            "Check SLA.WEBHOOK_HOURS configuration."
        )

    def test_notification_sla_is_24_hours(self):
        """
        Purpose:
            Verify notification domain has most lenient SLA.

        Expected:
            - notification_hours = 24 (least critical)
        """
        thresholds = SLAThresholds()
        assert thresholds.notification_hours == 24, (
            "Policy Violation: Notification SLA must be 24 hours. "
            "Notifications are non-blocking, lower priority."
        )

    def test_default_sla_is_24_hours(self):
        """
        Purpose:
            Verify fallback SLA for unknown domains.

        Expected:
            - default_hours = 24
        """
        thresholds = SLAThresholds()
        assert thresholds.default_hours == 24, (
            "Policy Violation: Default SLA must be 24 hours for unknown domains."
        )


@pytest.mark.tier1
class TestSLAThresholdRetrieval:
    """
    Tests for retrieving SLA thresholds by domain.

    Purpose:
        Validate correct threshold lookup for each domain.
    """

    def test_get_threshold_returns_timedelta(self):
        """
        Purpose:
            Verify threshold retrieval returns timedelta object.

        Expected:
            - Returns timedelta, not int
        """
        thresholds = SLAThresholds()
        result = thresholds.get_threshold("payment")

        assert isinstance(result, timedelta), (
            f"Expected timedelta, got {type(result).__name__}."
        )

    def test_get_threshold_payment(self):
        """
        Purpose:
            Verify payment threshold is 1 hour timedelta.
        """
        thresholds = SLAThresholds()
        result = thresholds.get_threshold("payment")

        assert result == timedelta(hours=1), (
            f"Payment threshold incorrect: expected 1 hour, got {result}."
        )

    def test_get_threshold_point(self):
        """
        Purpose:
            Verify point threshold is 4 hours timedelta.
        """
        thresholds = SLAThresholds()
        result = thresholds.get_threshold("point")

        assert result == timedelta(hours=4), (
            f"Point threshold incorrect: expected 4 hours, got {result}."
        )

    def test_get_threshold_inventory(self):
        """
        Purpose:
            Verify inventory threshold is 2 hours timedelta.
        """
        thresholds = SLAThresholds()
        result = thresholds.get_threshold("inventory")

        assert result == timedelta(hours=2), (
            f"Inventory threshold incorrect: expected 2 hours, got {result}."
        )

    def test_get_threshold_webhook(self):
        """
        Purpose:
            Verify webhook threshold is 8 hours timedelta.
        """
        thresholds = SLAThresholds()
        result = thresholds.get_threshold("webhook")

        assert result == timedelta(hours=8), (
            f"Webhook threshold incorrect: expected 8 hours, got {result}."
        )

    def test_get_threshold_notification(self):
        """
        Purpose:
            Verify notification threshold is 24 hours timedelta.
        """
        thresholds = SLAThresholds()
        result = thresholds.get_threshold("notification")

        assert result == timedelta(hours=24), (
            f"Notification threshold incorrect: expected 24 hours, got {result}."
        )

    def test_get_threshold_unknown_domain_returns_default(self):
        """
        Purpose:
            Verify unknown domains get default threshold.

        Scenario:
            1. Request threshold for non-existent domain "foobar"
            2. Should return default_hours

        Expected:
            - Returns 24 hours (default)
        """
        thresholds = SLAThresholds()
        result = thresholds.get_threshold("foobar")

        assert result == timedelta(hours=24), (
            f"Unknown domain should return default: expected 24 hours, got {result}."
        )

    def test_get_threshold_case_insensitive(self):
        """
        Purpose:
            Verify domain lookup is case-insensitive.
        """
        thresholds = SLAThresholds()

        assert thresholds.get_threshold("Payment") == timedelta(hours=1)
        assert thresholds.get_threshold("PAYMENT") == timedelta(hours=1)
        assert thresholds.get_threshold("payment") == timedelta(hours=1)


@pytest.mark.tier1
class TestSLAThresholdAllDomains:
    """
    Tests for retrieving all thresholds at once.

    Purpose:
        Validate bulk threshold retrieval.
    """

    def test_get_all_thresholds_returns_dict(self):
        """
        Purpose:
            Verify all thresholds method returns dict.
        """
        thresholds = SLAThresholds()
        result = thresholds.get_all_thresholds()

        assert isinstance(result, dict), (
            f"Expected dict, got {type(result).__name__}."
        )

    def test_get_all_thresholds_contains_all_domains(self):
        """
        Purpose:
            Verify all known domains are included.
        """
        thresholds = SLAThresholds()
        result = thresholds.get_all_thresholds()

        expected_domains = {"payment", "point", "inventory", "webhook", "notification"}
        actual_domains = set(result.keys())

        assert expected_domains == actual_domains, (
            f"Missing domains: {expected_domains - actual_domains}. "
            f"Unexpected domains: {actual_domains - expected_domains}."
        )

    def test_get_all_thresholds_values_are_timedeltas(self):
        """
        Purpose:
            Verify all values are timedelta objects.
        """
        thresholds = SLAThresholds()
        result = thresholds.get_all_thresholds()

        for domain, td in result.items():
            assert isinstance(td, timedelta), (
                f"Domain '{domain}' has non-timedelta value: {type(td).__name__}."
            )

    def test_get_all_thresholds_values_match_individual(self):
        """
        Purpose:
            Verify bulk retrieval matches individual lookups.
        """
        thresholds = SLAThresholds()
        all_thresholds = thresholds.get_all_thresholds()

        for domain, expected in all_thresholds.items():
            individual = thresholds.get_threshold(domain)
            assert individual == expected, (
                f"Mismatch for domain '{domain}': "
                f"bulk={expected}, individual={individual}."
            )


@pytest.mark.tier1
class TestSLAThresholdPriority:
    """
    Tests for SLA priority ordering.

    Purpose:
        Validate that critical domains have stricter SLAs.
    """

    def test_payment_is_strictest(self):
        """
        Purpose:
            Verify payment has the shortest SLA (highest priority).

        Expected:
            - payment < point < inventory < webhook < notification
        """
        thresholds = SLAThresholds()
        all_thresholds = thresholds.get_all_thresholds()

        payment_sla = all_thresholds["payment"]

        for domain, sla in all_thresholds.items():
            if domain != "payment":
                assert payment_sla < sla, (
                    f"Payment SLA ({payment_sla}) should be stricter than "
                    f"{domain} SLA ({sla})."
                )

    def test_sla_ordering_matches_business_criticality(self):
        """
        Purpose:
            Verify SLA ordering matches business priority.

        Expected Order (strictest to most lenient):
            1. payment (1h) - revenue impact
            2. inventory (2h) - stock accuracy
            3. point (4h) - customer satisfaction
            4. webhook (8h) - integration reliability
            5. notification (24h) - informational only
        """
        thresholds = SLAThresholds()
        all_thresholds = thresholds.get_all_thresholds()

        expected_order = [
            ("payment", 1),
            ("inventory", 2),
            ("point", 4),
            ("webhook", 8),
            ("notification", 24),
        ]

        for domain, expected_hours in expected_order:
            actual = all_thresholds[domain]
            expected = timedelta(hours=expected_hours)
            assert actual == expected, (
                f"Domain '{domain}' SLA mismatch: expected {expected}, got {actual}."
            )


@pytest.mark.tier1
class TestSLAThresholdImmutability:
    """
    Tests for SLA threshold immutability.

    Purpose:
        Verify thresholds are frozen (cannot be modified).
    """

    def test_sla_thresholds_is_frozen_dataclass(self):
        """
        Purpose:
            Verify SLAThresholds cannot be modified after creation.

        Expected:
            - Attempting to modify raises FrozenInstanceError
        """
        thresholds = SLAThresholds()

        with pytest.raises(Exception):  # FrozenInstanceError
            thresholds.payment_hours = 999

    def test_immutability_prevents_accidental_modification(self):
        """
        Purpose:
            Verify configuration safety - no runtime mutations.
        """
        thresholds = SLAThresholds()
        original_payment = thresholds.payment_hours

        try:
            thresholds.payment_hours = 999
            pytest.fail("Should not be able to modify frozen dataclass")
        except Exception:
            pass  # Expected

        assert thresholds.payment_hours == original_payment, (
            "SLA threshold was modified despite being frozen."
        )
