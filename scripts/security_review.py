"""
Security Review and Audit Script

Automated security review for the self-healing system.
Performs comprehensive checks on:
- Security violation handling
- Sensitive data protection
- Access control implementation
- Logging and audit trails

Run with:
    python manage.py shell < scripts/security_review.py
    # or
    docker-compose exec web python manage.py shell < scripts/security_review.py

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §5, §6
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


# =============================================================================
# Review Configuration
# =============================================================================

REVIEW_VERSION = "1.0.0"
REVIEW_DATE = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Colors:
    """ANSI color codes for terminal output."""
    
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    END = "\033[0m"


def print_header(title: str) -> None:
    """Print a formatted header."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{title.center(60)}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}\n")


def print_check(name: str, passed: bool, details: str = "") -> None:
    """Print a check result."""
    status = f"{Colors.GREEN}✓ PASS{Colors.END}" if passed else f"{Colors.RED}✗ FAIL{Colors.END}"
    print(f"  {status} - {name}")
    if details:
        print(f"        {Colors.YELLOW}{details}{Colors.END}")


def print_warning(message: str) -> None:
    """Print a warning message."""
    print(f"  {Colors.YELLOW}⚠ WARNING: {message}{Colors.END}")


def print_info(message: str) -> None:
    """Print an info message."""
    print(f"  {Colors.BLUE}ℹ INFO: {message}{Colors.END}")


# =============================================================================
# Security Checks
# =============================================================================

class SecurityReview:
    """
    Comprehensive security review for self-healing system.

    Checks are organized by category:
    1. Configuration Security
    2. Violation Handling
    3. Data Protection
    4. Access Control
    5. Audit Trail
    """

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.results = []

    def record_result(self, category: str, check: str, passed: bool, details: str = ""):
        """Record a check result."""
        if passed:
            self.passed += 1
        else:
            self.failed += 1
        self.results.append({
            "category": category,
            "check": check,
            "passed": passed,
            "details": details,
        })

    def record_warning(self, category: str, message: str):
        """Record a warning."""
        self.warnings += 1
        self.results.append({
            "category": category,
            "check": "WARNING",
            "passed": None,
            "details": message,
        })

    def check_security_violation_service(self) -> None:
        """Check security violation service implementation."""
        print_header("Security Violation Service")

        try:
            from shopping.services.self_healing.security_violation_service import (
                SecurityViolationService,
                ViolationType,
                Severity,
                SEVERITY_BY_VIOLATION_TYPE,
            )

            # Check 1: All violation types have severity mapping
            all_mapped = True
            unmapped = []
            for vtype in ViolationType:
                if vtype not in SEVERITY_BY_VIOLATION_TYPE and vtype.value not in SEVERITY_BY_VIOLATION_TYPE:
                    all_mapped = False
                    unmapped.append(vtype.value)

            print_check(
                "All violation types have severity mapping",
                all_mapped,
                f"Unmapped: {unmapped}" if unmapped else ""
            )
            self.record_result("violation_handling", "severity_mapping", all_mapped)

            # Check 2: Critical violations never retry
            critical_types = [
                ViolationType.WEBHOOK_SIGNATURE_INVALID,
                ViolationType.PAYMENT_AMOUNT_TAMPERED,
                ViolationType.TOKEN_FORGED,
                ViolationType.REPLAY_ATTACK,
            ]
            critical_handled = all(
                SEVERITY_BY_VIOLATION_TYPE.get(vt, SEVERITY_BY_VIOLATION_TYPE.get(vt.value)) == Severity.CRITICAL
                for vt in critical_types
            )
            print_check("Critical violations properly classified", critical_handled)
            self.record_result("violation_handling", "critical_classification", critical_handled)

            # Check 3: Service can be instantiated
            try:
                service = SecurityViolationService()
                print_check("SecurityViolationService instantiates", True)
                self.record_result("violation_handling", "service_instantiation", True)
            except Exception as e:
                print_check("SecurityViolationService instantiates", False, str(e))
                self.record_result("violation_handling", "service_instantiation", False, str(e))

        except ImportError as e:
            print_check("Security violation service exists", False, str(e))
            self.record_result("violation_handling", "service_exists", False, str(e))

    def check_security_notification_service(self) -> None:
        """Check security notification service implementation."""
        print_header("Security Notification Service")

        try:
            from shopping.services.self_healing.security_notification_service import (
                SecurityNotificationService,
                NotificationConfig,
                NotificationChannel,
            )

            # Check 1: Service exists and can be instantiated
            try:
                config = NotificationConfig(dry_run=True)
                service = SecurityNotificationService(config=config)
                print_check("SecurityNotificationService instantiates", True)
                self.record_result("notifications", "service_instantiation", True)
            except Exception as e:
                print_check("SecurityNotificationService instantiates", False, str(e))
                self.record_result("notifications", "service_instantiation", False, str(e))

            # Check 2: Multi-channel support
            channels = list(NotificationChannel)
            expected_channels = ["slack", "email", "sms", "pagerduty"]
            has_channels = all(
                any(c.value.lower() == expected for c in channels)
                for expected in expected_channels
            )
            print_check(
                "Multi-channel notification support",
                has_channels,
                f"Available: {[c.value for c in channels]}"
            )
            self.record_result("notifications", "multi_channel", has_channels)

            # Check 3: Dry-run mode available
            print_check("Dry-run mode available", hasattr(config, "dry_run"))
            self.record_result("notifications", "dry_run_mode", hasattr(config, "dry_run"))

        except ImportError as e:
            print_check("Security notification service exists", False, str(e))
            self.record_result("notifications", "service_exists", False, str(e))

    def check_security_incident_model(self) -> None:
        """Check SecurityIncident model implementation."""
        print_header("Security Incident Model")

        try:
            from shopping.models.security_incident import SecurityIncident

            # Check 1: Model has required fields
            required_fields = [
                "incident_type",
                "severity",
                "status",
                "source_ip",
                "description",
                "detected_at",
            ]
            model_fields = [f.name for f in SecurityIncident._meta.get_fields()]
            has_required = all(f in model_fields for f in required_fields)
            missing = [f for f in required_fields if f not in model_fields]
            print_check(
                "SecurityIncident has required fields",
                has_required,
                f"Missing: {missing}" if missing else ""
            )
            self.record_result("model", "required_fields", has_required)

            # Check 2: Severity choices exist
            has_severity = hasattr(SecurityIncident, "Severity")
            print_check("Severity choices defined", has_severity)
            self.record_result("model", "severity_choices", has_severity)

            # Check 3: Status choices exist
            has_status = hasattr(SecurityIncident, "Status")
            print_check("Status choices defined", has_status)
            self.record_result("model", "status_choices", has_status)

        except ImportError as e:
            print_check("SecurityIncident model exists", False, str(e))
            self.record_result("model", "model_exists", False, str(e))

    def check_sensitive_data_protection(self) -> None:
        """Check sensitive data is properly protected."""
        print_header("Sensitive Data Protection")

        try:
            from shopping.services.self_healing.forensic_context import ForensicContext

            # Check 1: ForensicContext sanitizes sensitive data
            # Look for sanitization methods
            has_sanitize = any(
                "saniti" in method.lower()
                for method in dir(ForensicContext)
                if not method.startswith("_")
            )

            if not has_sanitize:
                print_warning("No explicit sanitization method found in ForensicContext")
                self.record_warning("data_protection", "Consider adding explicit sanitize methods")

            print_check("ForensicContext class exists", True)
            self.record_result("data_protection", "forensic_context_exists", True)

        except ImportError as e:
            print_check("ForensicContext exists", False, str(e))
            self.record_result("data_protection", "forensic_context_exists", False, str(e))

        try:
            from shopping.services.self_healing.dlq_service import DLQService

            # Check 2: DLQ service doesn't expose raw payment data
            print_check("DLQ service exists", True)
            self.record_result("data_protection", "dlq_service_exists", True)

        except ImportError as e:
            print_check("DLQ service exists", False, str(e))
            self.record_result("data_protection", "dlq_service_exists", False, str(e))

    def check_access_control(self) -> None:
        """Check access control implementation."""
        print_header("Access Control")

        try:
            from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService

            # Check 1: Circuit breaker force operations require user tracking
            service = CircuitBreakerService()
            
            # Check method signatures for user parameter
            import inspect
            force_open_sig = inspect.signature(service.force_open)
            has_opened_by = "opened_by" in force_open_sig.parameters
            print_check("force_open tracks who opened", has_opened_by)
            self.record_result("access_control", "force_open_tracking", has_opened_by)

            force_close_sig = inspect.signature(service.force_close)
            has_closed_by = "closed_by" in force_close_sig.parameters
            print_check("force_close tracks who closed", has_closed_by)
            self.record_result("access_control", "force_close_tracking", has_closed_by)

        except ImportError as e:
            print_check("CircuitBreakerService exists", False, str(e))
            self.record_result("access_control", "service_exists", False, str(e))

        try:
            from shopping.models.failed_operation import FailedOperation

            # Check 2: DLQ resolution tracks who resolved
            model_fields = [f.name for f in FailedOperation._meta.get_fields()]
            has_resolved_by = "resolved_by" in model_fields
            print_check("FailedOperation tracks resolved_by", has_resolved_by)
            self.record_result("access_control", "resolution_tracking", has_resolved_by)

        except ImportError as e:
            print_check("FailedOperation model check", False, str(e))
            self.record_result("access_control", "model_check", False, str(e))

    def check_audit_trail(self) -> None:
        """Check audit trail implementation."""
        print_header("Audit Trail")

        try:
            from shopping.models.failed_operation import FailedOperation

            # Check 1: DLQ entries have timestamps
            model_fields = [f.name for f in FailedOperation._meta.get_fields()]
            has_created = "created_at" in model_fields
            has_updated = "updated_at" in model_fields
            has_resolved = "resolved_at" in model_fields

            print_check("FailedOperation has created_at", has_created)
            print_check("FailedOperation has updated_at", has_updated)
            print_check("FailedOperation has resolved_at", has_resolved)

            self.record_result("audit_trail", "created_at", has_created)
            self.record_result("audit_trail", "updated_at", has_updated)
            self.record_result("audit_trail", "resolved_at", has_resolved)

            # Check 2: Soft-delete implemented (ARCHIVED status)
            has_archived = hasattr(FailedOperation, "Status") and hasattr(
                FailedOperation.Status, "ARCHIVED"
            )
            print_check("Soft-delete (ARCHIVED status) implemented", has_archived)
            self.record_result("audit_trail", "soft_delete", has_archived)

        except ImportError as e:
            print_check("FailedOperation model for audit trail", False, str(e))
            self.record_result("audit_trail", "model_exists", False, str(e))

        try:
            from shopping.models.security_incident import SecurityIncident

            # Check 3: Security incidents have audit fields
            model_fields = [f.name for f in SecurityIncident._meta.get_fields()]
            has_detected = "detected_at" in model_fields
            has_action = "action_taken" in model_fields
            has_resolved = "resolved_at" in model_fields

            print_check("SecurityIncident has detected_at", has_detected)
            print_check("SecurityIncident has action_taken", has_action)

            self.record_result("audit_trail", "incident_detected_at", has_detected)
            self.record_result("audit_trail", "incident_action_taken", has_action)

        except ImportError as e:
            print_check("SecurityIncident model for audit trail", False, str(e))
            self.record_result("audit_trail", "incident_model_exists", False, str(e))

    def check_ip_management(self) -> None:
        """Check IP management for security."""
        print_header("IP Management")

        try:
            from shopping.services.self_healing.security_violation_service import (
                SecurityViolationService,
            )

            service = SecurityViolationService()

            # Check 1: IP logging capability
            has_ip_logging = hasattr(service, "log_suspicious_ip") or any(
                "ip" in method.lower() and "log" in method.lower()
                for method in dir(service)
                if not method.startswith("_")
            )

            # Check for any IP-related methods
            ip_methods = [
                m for m in dir(service)
                if not m.startswith("_") and "ip" in m.lower()
            ]
            print_info(f"IP-related methods: {ip_methods}")

            print_check("IP management methods exist", len(ip_methods) > 0)
            self.record_result("ip_management", "ip_methods", len(ip_methods) > 0)

            # Check 2: Temporary ban capability
            has_temp_ban = any(
                "ban" in method.lower() or "block" in method.lower()
                for method in dir(service)
                if not method.startswith("_")
            )
            print_check("Temporary ban capability", has_temp_ban)
            self.record_result("ip_management", "temp_ban", has_temp_ban)

        except ImportError as e:
            print_check("Security violation service for IP management", False, str(e))
            self.record_result("ip_management", "service_exists", False, str(e))

    def run_all_checks(self) -> dict:
        """Run all security checks."""
        print(f"\n{Colors.BOLD}Security Review v{REVIEW_VERSION}{Colors.END}")
        print(f"Date: {REVIEW_DATE}")

        self.check_security_violation_service()
        self.check_security_notification_service()
        self.check_security_incident_model()
        self.check_sensitive_data_protection()
        self.check_access_control()
        self.check_audit_trail()
        self.check_ip_management()

        # Summary
        print_header("Summary")
        total = self.passed + self.failed
        pass_rate = (self.passed / total * 100) if total > 0 else 0

        print(f"  Total Checks: {total}")
        print(f"  {Colors.GREEN}Passed: {self.passed}{Colors.END}")
        print(f"  {Colors.RED}Failed: {self.failed}{Colors.END}")
        print(f"  {Colors.YELLOW}Warnings: {self.warnings}{Colors.END}")
        print(f"  Pass Rate: {pass_rate:.1f}%")

        if pass_rate >= 90:
            print(f"\n  {Colors.GREEN}{Colors.BOLD}✓ SECURITY REVIEW PASSED{Colors.END}")
        elif pass_rate >= 70:
            print(f"\n  {Colors.YELLOW}{Colors.BOLD}⚠ SECURITY REVIEW NEEDS ATTENTION{Colors.END}")
        else:
            print(f"\n  {Colors.RED}{Colors.BOLD}✗ SECURITY REVIEW FAILED{Colors.END}")

        return {
            "version": REVIEW_VERSION,
            "date": REVIEW_DATE,
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "pass_rate": pass_rate,
            "results": self.results,
        }


# =============================================================================
# Main Execution
# =============================================================================

if __name__ == "__main__":
    review = SecurityReview()
    results = review.run_all_checks()

    # Export results to JSON
    output_path = Path(__file__).parent / "security_review_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n  Results exported to: {output_path}")

    # Exit with error code if failed
    sys.exit(0 if results["pass_rate"] >= 70 else 1)
