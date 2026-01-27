"""
Corruption Shield - Multi-Layer Data Integrity Protection.

Provides defense-in-depth against data corruption:
- L1: Schema Validation (syntax, format, types)
- L2: Business Rules (logic, constraints, consistency)
- L3: Anomaly Detection (statistical outliers, Z-Score)

Usage:
    from selfhealing.services.corruption_shield import CorruptionShield

    shield = CorruptionShield()

    result = shield.validate(data={
        "amount": 50000,
        "order_id": "order_123",
        "signature": "abc123",
    }, context={"expected_amount": 50000})

    if not result.is_valid:
        print(f"Blocked: {result.violations}")
"""

from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
from selfhealing.services.corruption_shield.shield import (
    CorruptionShield,
    ValidationResult,
    ViolationSeverity,
    get_corruption_shield,
    reset_corruption_shield,
)
from selfhealing.services.corruption_shield.validators import (
    L1SchemaValidator,
    L2BusinessRulesValidator,
    L3AnomalyDetector,
    Violation,
)

__all__ = [
    "CorruptionShield",
    "ValidationResult",
    "Violation",
    "ViolationSeverity",
    "L1SchemaValidator",
    "L2BusinessRulesValidator",
    "L3AnomalyDetector",
    "CorruptionShieldConfig",
    "get_corruption_shield",
    "reset_corruption_shield",
]
