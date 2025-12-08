"""
Security Review Management Command

Run comprehensive security review for the self-healing system.

Usage:
    python manage.py security_review
    python manage.py security_review --output /path/to/results.json
    python manage.py security_review --quiet
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError


# =============================================================================
# Configuration
# =============================================================================

REVIEW_VERSION = "1.0.0"


class CheckResult:
    """Result of a single security check."""

    def __init__(self, category: str, name: str, passed: bool, details: str = ""):
        self.category = category
        self.name = name
        self.passed = passed
        self.details = details


# =============================================================================
# Security Check Functions
# =============================================================================


def check_security_violation_service() -> list[CheckResult]:
    """Check security violation service implementation."""
    results = []

    try:
        from shopping.services.self_healing.security_violation_service import (
            SEVERITY_BY_VIOLATION_TYPE,
            SecurityViolationService,
            Severity,
            ViolationType,
        )

        # Check 1: All violation types have severity mapping
        unmapped = []
        for vtype in ViolationType:
            if (
                vtype not in SEVERITY_BY_VIOLATION_TYPE
                and vtype.value not in SEVERITY_BY_VIOLATION_TYPE
            ):
                unmapped.append(vtype.value)

        results.append(
            CheckResult(
                "violation_handling",
                "All violation types have severity mapping",
                len(unmapped) == 0,
                f"Unmapped: {unmapped}" if unmapped else "",
            )
        )

        # Check 2: Critical violations properly classified
        critical_types = [
            ViolationType.WEBHOOK_SIGNATURE_INVALID,
            ViolationType.PAYMENT_AMOUNT_TAMPERED,
            ViolationType.TOKEN_FORGED,
            ViolationType.REPLAY_ATTACK,
        ]
        critical_ok = all(
            SEVERITY_BY_VIOLATION_TYPE.get(
                vt, SEVERITY_BY_VIOLATION_TYPE.get(vt.value)
            )
            == Severity.CRITICAL
            for vt in critical_types
        )
        results.append(
            CheckResult(
                "violation_handling",
                "Critical violations properly classified",
                critical_ok,
            )
        )

        # Check 3: Service instantiates
        try:
            SecurityViolationService()
            results.append(
                CheckResult(
                    "violation_handling",
                    "SecurityViolationService instantiates",
                    True,
                )
            )
        except Exception as e:
            results.append(
                CheckResult(
                    "violation_handling",
                    "SecurityViolationService instantiates",
                    False,
                    str(e),
                )
            )

    except ImportError as e:
        results.append(
            CheckResult(
                "violation_handling",
                "Security violation service exists",
                False,
                str(e),
            )
        )

    return results


def check_security_notification_service() -> list[CheckResult]:
    """Check security notification service implementation."""
    results = []

    try:
        from shopping.services.self_healing.security_notification_service import (
            NotificationChannel,
            NotificationConfig,
            SecurityNotificationService,
        )

        # Check 1: Service instantiates
        try:
            config = NotificationConfig(dry_run=True)
            SecurityNotificationService(config=config)
            results.append(
                CheckResult(
                    "notifications",
                    "SecurityNotificationService instantiates",
                    True,
                )
            )
        except Exception as e:
            results.append(
                CheckResult(
                    "notifications",
                    "SecurityNotificationService instantiates",
                    False,
                    str(e),
                )
            )

        # Check 2: Multi-channel support
        channels = list(NotificationChannel)
        expected = ["slack", "email", "sms", "pagerduty"]
        has_all = all(
            any(c.value.lower() == exp for c in channels) for exp in expected
        )
        results.append(
            CheckResult(
                "notifications",
                "Multi-channel notification support",
                has_all,
                f"Available: {[c.value for c in channels]}",
            )
        )

    except ImportError as e:
        results.append(
            CheckResult(
                "notifications",
                "Security notification service exists",
                False,
                str(e),
            )
        )

    return results


def check_security_incident_model() -> list[CheckResult]:
    """Check SecurityIncident model implementation."""
    results = []

    try:
        from shopping.models.security_incident import SecurityIncident

        # Check 1: Required fields
        required = ["incident_type", "severity", "status", "source_ip", "description"]
        fields = [f.name for f in SecurityIncident._meta.get_fields()]
        missing = [f for f in required if f not in fields]

        results.append(
            CheckResult(
                "model",
                "SecurityIncident has required fields",
                len(missing) == 0,
                f"Missing: {missing}" if missing else "",
            )
        )

        # Check 2: Severity choices
        results.append(
            CheckResult(
                "model",
                "Severity choices defined",
                hasattr(SecurityIncident, "Severity"),
            )
        )

        # Check 3: Status choices
        results.append(
            CheckResult(
                "model",
                "Status choices defined",
                hasattr(SecurityIncident, "Status"),
            )
        )

    except ImportError as e:
        results.append(
            CheckResult("model", "SecurityIncident model exists", False, str(e))
        )

    return results


def check_data_protection() -> list[CheckResult]:
    """Check sensitive data protection."""
    results = []

    try:
        from shopping.services.self_healing.forensic_context import ForensicContext

        results.append(
            CheckResult("data_protection", "ForensicContext class exists", True)
        )
    except ImportError as e:
        results.append(
            CheckResult("data_protection", "ForensicContext exists", False, str(e))
        )

    try:
        from shopping.services.self_healing.dlq_service import DLQService

        results.append(CheckResult("data_protection", "DLQ service exists", True))
    except ImportError as e:
        results.append(
            CheckResult("data_protection", "DLQ service exists", False, str(e))
        )

    return results


def check_access_control() -> list[CheckResult]:
    """Check access control implementation."""
    results = []

    try:
        import inspect

        from shopping.services.self_healing.circuit_breaker_service import (
            CircuitBreakerService,
        )

        service = CircuitBreakerService()

        # Check force_open tracking (uses controlled_by parameter)
        sig = inspect.signature(service.force_open)
        has_controlled_by = "controlled_by" in sig.parameters
        results.append(
            CheckResult("access_control", "force_open tracks operator", has_controlled_by)
        )

        # Check force_close tracking (uses controlled_by parameter)
        sig = inspect.signature(service.force_close)
        has_controlled_by = "controlled_by" in sig.parameters
        results.append(
            CheckResult("access_control", "force_close tracks operator", has_controlled_by)
        )

    except ImportError as e:
        results.append(
            CheckResult("access_control", "CircuitBreakerService exists", False, str(e))
        )

    try:
        from shopping.models.failed_operation import FailedOperation

        fields = [f.name for f in FailedOperation._meta.get_fields()]
        has_resolved_by = "resolved_by" in fields
        results.append(
            CheckResult(
                "access_control",
                "FailedOperation tracks resolved_by",
                has_resolved_by,
            )
        )
    except ImportError as e:
        results.append(
            CheckResult("access_control", "FailedOperation model check", False, str(e))
        )

    return results


def check_audit_trail() -> list[CheckResult]:
    """Check audit trail implementation."""
    results = []

    try:
        from shopping.models.failed_operation import FailedOperation

        fields = [f.name for f in FailedOperation._meta.get_fields()]

        results.append(
            CheckResult(
                "audit_trail", "FailedOperation has created_at", "created_at" in fields
            )
        )
        results.append(
            CheckResult(
                "audit_trail", "FailedOperation has updated_at", "updated_at" in fields
            )
        )
        results.append(
            CheckResult(
                "audit_trail",
                "FailedOperation has resolved_at",
                "resolved_at" in fields,
            )
        )

        # Soft-delete check
        has_archived = hasattr(FailedOperation, "Status") and hasattr(
            FailedOperation.Status, "ARCHIVED"
        )
        results.append(
            CheckResult(
                "audit_trail", "Soft-delete (ARCHIVED status) implemented", has_archived
            )
        )

    except ImportError as e:
        results.append(
            CheckResult("audit_trail", "FailedOperation model exists", False, str(e))
        )

    return results


def check_ip_management() -> list[CheckResult]:
    """Check IP management for security."""
    results = []

    try:
        from shopping.services.self_healing.security_violation_service import (
            SecurityViolationService,
        )

        service = SecurityViolationService()

        # Check for IP-related methods
        ip_methods = [
            m for m in dir(service) if not m.startswith("_") and "ip" in m.lower()
        ]
        results.append(
            CheckResult(
                "ip_management",
                "IP management methods exist",
                len(ip_methods) > 0,
                f"Methods: {ip_methods}",
            )
        )

        # Check for ban capability
        has_ban = any(
            "ban" in m.lower() or "block" in m.lower()
            for m in dir(service)
            if not m.startswith("_")
        )
        results.append(
            CheckResult("ip_management", "Temporary ban capability", has_ban)
        )

    except ImportError as e:
        results.append(
            CheckResult("ip_management", "Security violation service exists", False, str(e))
        )

    return results


# =============================================================================
# Command
# =============================================================================


class Command(BaseCommand):
    """Security review management command."""

    help = "Run comprehensive security review for the self-healing system"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            "-o",
            type=str,
            help="Output path for JSON results",
        )
        parser.add_argument(
            "--quiet",
            "-q",
            action="store_true",
            help="Only show summary, not individual checks",
        )

    def handle(self, *args, **options):
        quiet = options.get("quiet", False)
        output_path = options.get("output")

        review_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not quiet:
            self.stdout.write(self.style.NOTICE(f"\nSecurity Review v{REVIEW_VERSION}"))
            self.stdout.write(f"Date: {review_date}\n")

        # Run all checks
        all_results: list[CheckResult] = []

        check_functions = [
            ("Security Violation Service", check_security_violation_service),
            ("Security Notification Service", check_security_notification_service),
            ("Security Incident Model", check_security_incident_model),
            ("Data Protection", check_data_protection),
            ("Access Control", check_access_control),
            ("Audit Trail", check_audit_trail),
            ("IP Management", check_ip_management),
        ]

        for section_name, check_func in check_functions:
            if not quiet:
                self.stdout.write(f"\n{'=' * 50}")
                self.stdout.write(self.style.MIGRATE_HEADING(section_name))
                self.stdout.write("=" * 50)

            results = check_func()
            all_results.extend(results)

            if not quiet:
                for r in results:
                    if r.passed:
                        msg = self.style.SUCCESS(f"  ✓ PASS - {r.name}")
                    else:
                        msg = self.style.ERROR(f"  ✗ FAIL - {r.name}")

                    self.stdout.write(msg)
                    if r.details:
                        self.stdout.write(self.style.WARNING(f"        {r.details}"))

        # Calculate summary
        passed = sum(1 for r in all_results if r.passed)
        failed = sum(1 for r in all_results if not r.passed)
        total = passed + failed
        pass_rate = (passed / total * 100) if total > 0 else 0

        # Print summary
        self.stdout.write(f"\n{'=' * 50}")
        self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
        self.stdout.write("=" * 50)
        self.stdout.write(f"  Total Checks: {total}")
        self.stdout.write(self.style.SUCCESS(f"  Passed: {passed}"))
        self.stdout.write(self.style.ERROR(f"  Failed: {failed}"))
        self.stdout.write(f"  Pass Rate: {pass_rate:.1f}%")

        if pass_rate >= 90:
            self.stdout.write(self.style.SUCCESS("\n  ✓ SECURITY REVIEW PASSED"))
        elif pass_rate >= 70:
            self.stdout.write(self.style.WARNING("\n  ⚠ SECURITY REVIEW NEEDS ATTENTION"))
        else:
            self.stdout.write(self.style.ERROR("\n  ✗ SECURITY REVIEW FAILED"))

        # Export results if output path specified
        if output_path:
            results_data = {
                "version": REVIEW_VERSION,
                "date": review_date,
                "passed": passed,
                "failed": failed,
                "pass_rate": pass_rate,
                "results": [
                    {
                        "category": r.category,
                        "name": r.name,
                        "passed": r.passed,
                        "details": r.details,
                    }
                    for r in all_results
                ],
            }

            with open(output_path, "w") as f:
                json.dump(results_data, f, indent=2, default=str)

            self.stdout.write(f"\n  Results exported to: {output_path}")

        # Return exit code
        if pass_rate < 70:
            raise CommandError("Security review failed")
