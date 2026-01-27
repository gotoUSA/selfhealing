"""
Corruption Shield Validators.

L1: Schema Validation - Format, types, required fields
L2: Business Rules - Logic, constraints, consistency  
L3: Anomaly Detection - Statistical outliers
"""

from __future__ import annotations

import logging
import math
import re
import threading
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Any

from selfhealing.services.corruption_shield.config import CorruptionShieldConfig

logger = logging.getLogger(__name__)


@dataclass
class Violation:
    """Single validation violation."""

    layer: str  # "L1", "L2", "L3"
    code: str  # "invalid_type", "negative_amount", etc.
    message: str
    field: str | None = None
    severity: str = "high"  # "critical", "high", "medium", "low"
    value: Any | None = None

    def to_dict(self) -> dict:
        return {
            "layer": self.layer,
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "severity": self.severity,
        }


class BaseValidator(ABC):
    """Abstract base class for validators."""

    def __init__(self, config: CorruptionShieldConfig):
        self.config = config

    @abstractmethod
    def validate(self, data: dict, context: dict | None = None) -> list[Violation]:
        """
        Validate data and return list of violations.

        Args:
            data: Data to validate
            context: Additional context (expected values, user info, etc.)

        Returns:
            List of Violation objects (empty if valid)
        """
        pass


class L1SchemaValidator(BaseValidator):
    """
    Layer 1: Schema/Syntax Validation.

    Checks:
    - Required fields present
    - Correct data types
    - Format validation (no SQL injection, XSS, etc.)
    - String length limits
    """

    # Patterns that indicate injection attempts
    INJECTION_PATTERNS = [
        r"(?i)(select|insert|update|delete|drop|union|exec|execute)\s",  # SQL
        r"<script[^>]*>",  # XSS
        r"javascript:",  # XSS
        r"(?i)(eval|alert|document\.cookie)",  # JS injection
        r"--",  # SQL comment
        r";.*--",  # SQL injection
        r"\x00",  # Null byte
    ]

    def __init__(self, config: CorruptionShieldConfig):
        super().__init__(config)
        self._compiled_patterns = [
            re.compile(pattern) for pattern in self.INJECTION_PATTERNS
        ]

    def validate(self, data: dict, context: dict | None = None) -> list[Violation]:
        violations = []

        # Check required fields
        for field_name in self.config.required_fields:
            if field_name not in data or data[field_name] is None:
                violations.append(
                    Violation(
                        layer="L1",
                        code="missing_required_field",
                        message=f"Required field '{field_name}' is missing",
                        field=field_name,
                        severity="high",
                    )
                )

        # Check each field
        for field_name, value in data.items():
            field_violations = self._validate_field(field_name, value)
            violations.extend(field_violations)

        return violations

    def _validate_field(self, field_name: str, value: Any) -> list[Violation]:
        """Validate a single field."""
        violations = []

        # Type-specific validation
        if isinstance(value, str):
            violations.extend(self._validate_string(field_name, value))
        elif isinstance(value, (int, float)):
            violations.extend(self._validate_number(field_name, value))

        return violations

    def _validate_string(self, field_name: str, value: str) -> list[Violation]:
        """Validate string fields."""
        violations = []

        # Length check
        if len(value) > self.config.max_string_length:
            violations.append(
                Violation(
                    layer="L1",
                    code="string_too_long",
                    message=f"Field '{field_name}' exceeds max length ({len(value)} > {self.config.max_string_length})",
                    field=field_name,
                    severity="medium",
                )
            )

        # Injection pattern check
        for pattern in self._compiled_patterns:
            if pattern.search(value):
                violations.append(
                    Violation(
                        layer="L1",
                        code="injection_attempt",
                        message=f"Potential injection attack detected in '{field_name}'",
                        field=field_name,
                        severity="critical",
                        value=value[:50] + "..." if len(value) > 50 else value,
                    )
                )
                break  # One match is enough

        return violations

    def _validate_number(self, field_name: str, value: Any) -> list[Violation]:
        """Validate numeric fields."""
        violations = []

        # NaN/Inf check
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                violations.append(
                    Violation(
                        layer="L1",
                        code="invalid_number",
                        message=f"Field '{field_name}' has invalid numeric value (NaN/Inf)",
                        field=field_name,
                        severity="high",
                    )
                )

        return violations


class L2BusinessRulesValidator(BaseValidator):
    """
    Layer 2: Business Rules Validation.

    Checks:
    - Amount within valid range
    - Status is valid
    - Amount matches expected value (if context provided)
    - Signature validation (if applicable)
    """

    def validate(self, data: dict, context: dict | None = None) -> list[Violation]:
        violations = []
        context = context or {}

        # Amount validation
        if "amount" in data:
            violations.extend(self._validate_amount(data["amount"], context))

        # Status validation
        if "status" in data:
            violations.extend(self._validate_status(data["status"]))

        # Amount mismatch check (if expected amount provided)
        if "expected_amount" in context and "amount" in data:
            violations.extend(
                self._validate_amount_match(data["amount"], context["expected_amount"])
            )

        # Signature validation (if expected signature provided)
        if "expected_signature" in context and "signature" in data:
            if data["signature"] != context["expected_signature"]:
                violations.append(
                    Violation(
                        layer="L2",
                        code="signature_mismatch",
                        message="Signature does not match expected value",
                        field="signature",
                        severity="critical",
                    )
                )

        return violations

    def _validate_amount(self, amount: Any, context: dict) -> list[Violation]:
        """Validate amount field."""
        violations = []

        # Type check
        if not isinstance(amount, (int, float)):
            violations.append(
                Violation(
                    layer="L2",
                    code="invalid_amount_type",
                    message=f"Amount must be numeric, got {type(amount).__name__}",
                    field="amount",
                    severity="high",
                )
            )
            return violations

        # Negative amount
        if amount < 0:
            violations.append(
                Violation(
                    layer="L2",
                    code="negative_amount",
                    message=f"Amount cannot be negative: {amount}",
                    field="amount",
                    severity="critical",
                    value=amount,
                )
            )

        # Below minimum
        elif amount < self.config.min_amount:
            violations.append(
                Violation(
                    layer="L2",
                    code="amount_below_minimum",
                    message=f"Amount {amount} is below minimum {self.config.min_amount}",
                    field="amount",
                    severity="high",
                    value=amount,
                )
            )

        # Above maximum
        elif amount > self.config.max_amount:
            violations.append(
                Violation(
                    layer="L2",
                    code="amount_above_maximum",
                    message=f"Amount {amount} exceeds maximum {self.config.max_amount}",
                    field="amount",
                    severity="critical",
                    value=amount,
                )
            )

        return violations

    def _validate_status(self, status: Any) -> list[Violation]:
        """Validate status field."""
        violations = []

        if not isinstance(status, str):
            violations.append(
                Violation(
                    layer="L2",
                    code="invalid_status_type",
                    message=f"Status must be string, got {type(status).__name__}",
                    field="status",
                    severity="high",
                )
            )
            return violations

        if status not in self.config.allowed_statuses:
            violations.append(
                Violation(
                    layer="L2",
                    code="invalid_status",
                    message=f"Status '{status}' is not valid. Allowed: {self.config.allowed_statuses}",
                    field="status",
                    severity="medium",
                )
            )

        return violations

    def _validate_amount_match(self, actual: Any, expected: Any) -> list[Violation]:
        """Validate amount matches expected value."""
        violations = []

        if actual != expected:
            violations.append(
                Violation(
                    layer="L2",
                    code="amount_mismatch",
                    message=f"Amount {actual} does not match expected {expected}",
                    field="amount",
                    severity="critical",
                    value={"actual": actual, "expected": expected},
                )
            )

        return violations


class L3AnomalyDetector(BaseValidator):
    """
    Layer 3: Statistical Anomaly Detection.

    Checks:
    - Z-Score based outlier detection for amounts
    - IQR (Interquartile Range) based detection
    - Rate of requests anomaly

    Requires historical data to build baseline.
    """

    def __init__(self, config: CorruptionShieldConfig):
        super().__init__(config)

        # Historical data for baseline
        self._amount_history: deque = deque(maxlen=1000)
        self._lock = threading.Lock()

        # Cached statistics
        self._mean: float | None = None
        self._std: float | None = None
        self._q1: float | None = None
        self._q3: float | None = None

    def validate(self, data: dict, context: dict | None = None) -> list[Violation]:
        violations = []

        if "amount" in data and isinstance(data["amount"], (int, float)):
            amount = data["amount"]

            # Check if we have enough history
            with self._lock:
                sample_count = len(self._amount_history)

            if sample_count >= self.config.min_samples_for_anomaly:
                # Z-Score check
                z_score = self._calculate_z_score(amount)
                if z_score is not None and abs(z_score) > self.config.z_score_threshold:
                    violations.append(
                        Violation(
                            layer="L3",
                            code="statistical_outlier",
                            message=f"Amount {amount} is a statistical outlier (Z-score: {z_score:.2f})",
                            field="amount",
                            severity="high",
                            value={"amount": amount, "z_score": z_score},
                        )
                    )

                # IQR check
                if self._is_iqr_outlier(amount):
                    violations.append(
                        Violation(
                            layer="L3",
                            code="iqr_outlier",
                            message=f"Amount {amount} is outside IQR bounds",
                            field="amount",
                            severity="medium",
                            value=amount,
                        )
                    )

            # Always add to history (even if anomaly)
            self._add_to_history(amount)

        return violations

    def _add_to_history(self, amount: float) -> None:
        """Add amount to history and update statistics."""
        with self._lock:
            self._amount_history.append(amount)
            self._update_statistics()

    def _update_statistics(self) -> None:
        """Update cached statistics."""
        if len(self._amount_history) < 2:
            return

        amounts = list(self._amount_history)
        n = len(amounts)

        # Mean
        self._mean = sum(amounts) / n

        # Standard deviation
        variance = sum((x - self._mean) ** 2 for x in amounts) / n
        self._std = variance**0.5

        # Quartiles
        sorted_amounts = sorted(amounts)
        self._q1 = sorted_amounts[n // 4]
        self._q3 = sorted_amounts[3 * n // 4]

    def _calculate_z_score(self, value: float) -> float | None:
        """Calculate Z-score for a value."""
        with self._lock:
            if self._mean is None or self._std is None or self._std == 0:
                return None
            return (value - self._mean) / self._std

    def _is_iqr_outlier(self, value: float) -> bool:
        """Check if value is an IQR outlier."""
        with self._lock:
            if self._q1 is None or self._q3 is None:
                return False

            iqr = self._q3 - self._q1
            lower_bound = self._q1 - self.config.iqr_multiplier * iqr
            upper_bound = self._q3 + self.config.iqr_multiplier * iqr

            return value < lower_bound or value > upper_bound

    def get_stats(self) -> dict:
        """Get detector statistics."""
        with self._lock:
            return {
                "sample_count": len(self._amount_history),
                "mean": self._mean,
                "std": self._std,
                "q1": self._q1,
                "q3": self._q3,
            }

    def reset(self) -> None:
        """Reset detector state."""
        with self._lock:
            self._amount_history.clear()
            self._mean = None
            self._std = None
            self._q1 = None
            self._q3 = None
