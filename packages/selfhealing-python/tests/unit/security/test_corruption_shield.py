"""
Corruption Shield Unit Tests.

Tests for the selfhealing.services.corruption_shield module.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestCorruptionShieldConfig:
    """Tests for CorruptionShieldConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        
        assert config.l1_enabled is True
        assert config.l2_enabled is True
        assert config.l3_enabled is True
        assert config.min_amount == 100
        assert config.max_amount == 100_000_000
        assert config.z_score_threshold == 3.0
        assert "amount" in config.required_fields
        assert "order_id" in config.required_fields
    
    def test_custom_config(self):
        """Test custom configuration."""
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(
            l3_enabled=False,
            min_amount=1000,
            max_amount=50_000_000,
            z_score_threshold=2.5,
        )
        
        assert config.l3_enabled is False
        assert config.min_amount == 1000
        assert config.max_amount == 50_000_000
        assert config.z_score_threshold == 2.5
    
    def test_from_dict(self):
        """Test creating config from dictionary."""
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config_dict = {
            "l1_enabled": False,
            "min_amount": 500,
            "allowed_statuses": ["DONE", "PENDING"],
        }
        
        config = CorruptionShieldConfig.from_dict(config_dict)
        
        assert config.l1_enabled is False
        assert config.min_amount == 500
        assert "DONE" in config.allowed_statuses
        assert "PENDING" in config.allowed_statuses


class TestL1SchemaValidator:
    """Tests for L1 Schema Validator."""
    
    def test_valid_data_passes(self):
        """Test valid data passes L1 validation."""
        from selfhealing.services.corruption_shield.validators import L1SchemaValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        validator = L1SchemaValidator(config)
        
        data = {
            "amount": 50000,
            "order_id": "order_12345",
            "status": "DONE",
        }
        
        violations = validator.validate(data)
        assert len(violations) == 0
    
    def test_missing_required_field(self):
        """Test missing required field triggers violation."""
        from selfhealing.services.corruption_shield.validators import L1SchemaValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(required_fields=["amount", "order_id"])
        validator = L1SchemaValidator(config)
        
        data = {"order_id": "order_12345"}  # Missing amount
        
        violations = validator.validate(data)
        assert len(violations) == 1
        assert violations[0].code == "missing_required_field"
        assert violations[0].field == "amount"
    
    def test_sql_injection_detected(self):
        """Test SQL injection attempt is detected."""
        from selfhealing.services.corruption_shield.validators import L1SchemaValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        validator = L1SchemaValidator(config)
        
        data = {
            "amount": 50000,
            "order_id": "order_123'; DROP TABLE orders; --",
        }
        
        violations = validator.validate(data)
        injection_violations = [v for v in violations if v.code == "injection_attempt"]
        assert len(injection_violations) >= 1
        assert injection_violations[0].severity == "critical"
    
    def test_xss_attack_detected(self):
        """Test XSS attack is detected."""
        from selfhealing.services.corruption_shield.validators import L1SchemaValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        validator = L1SchemaValidator(config)
        
        data = {
            "amount": 50000,
            "order_id": "<script>alert('XSS')</script>",
        }
        
        violations = validator.validate(data)
        assert any(v.code == "injection_attempt" for v in violations)
    
    def test_string_too_long(self):
        """Test string exceeding max length."""
        from selfhealing.services.corruption_shield.validators import L1SchemaValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(max_string_length=100)
        validator = L1SchemaValidator(config)
        
        data = {
            "amount": 50000,
            "order_id": "a" * 200,  # 200 chars > 100
        }
        
        violations = validator.validate(data)
        assert any(v.code == "string_too_long" for v in violations)
    
    def test_nan_detected(self):
        """Test NaN values are detected."""
        from selfhealing.services.corruption_shield.validators import L1SchemaValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        validator = L1SchemaValidator(config)
        
        data = {
            "amount": float("nan"),
            "order_id": "order_123",
        }
        
        violations = validator.validate(data)
        assert any(v.code == "invalid_number" for v in violations)


class TestL2BusinessRulesValidator:
    """Tests for L2 Business Rules Validator."""
    
    def test_valid_data_passes(self):
        """Test valid data passes L2 validation."""
        from selfhealing.services.corruption_shield.validators import L2BusinessRulesValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        validator = L2BusinessRulesValidator(config)
        
        data = {
            "amount": 50000,
            "status": "DONE",
        }
        
        violations = validator.validate(data)
        assert len(violations) == 0
    
    def test_negative_amount_rejected(self):
        """Test negative amount is rejected."""
        from selfhealing.services.corruption_shield.validators import L2BusinessRulesValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        validator = L2BusinessRulesValidator(config)
        
        data = {"amount": -10000}
        
        violations = validator.validate(data)
        assert len(violations) == 1
        assert violations[0].code == "negative_amount"
        assert violations[0].severity == "critical"
    
    def test_amount_below_minimum(self):
        """Test amount below minimum is rejected."""
        from selfhealing.services.corruption_shield.validators import L2BusinessRulesValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(min_amount=1000)
        validator = L2BusinessRulesValidator(config)
        
        data = {"amount": 500}
        
        violations = validator.validate(data)
        assert len(violations) == 1
        assert violations[0].code == "amount_below_minimum"
    
    def test_amount_above_maximum(self):
        """Test amount above maximum is rejected."""
        from selfhealing.services.corruption_shield.validators import L2BusinessRulesValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(max_amount=1_000_000)
        validator = L2BusinessRulesValidator(config)
        
        data = {"amount": 10_000_000}
        
        violations = validator.validate(data)
        assert len(violations) == 1
        assert violations[0].code == "amount_above_maximum"
        assert violations[0].severity == "critical"
    
    def test_invalid_status(self):
        """Test invalid status is rejected."""
        from selfhealing.services.corruption_shield.validators import L2BusinessRulesValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(allowed_statuses={"DONE", "PENDING"})
        validator = L2BusinessRulesValidator(config)
        
        data = {"status": "HACKED"}
        
        violations = validator.validate(data)
        assert len(violations) == 1
        assert violations[0].code == "invalid_status"
    
    def test_amount_mismatch_with_context(self):
        """Test amount mismatch detection with context."""
        from selfhealing.services.corruption_shield.validators import L2BusinessRulesValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        validator = L2BusinessRulesValidator(config)
        
        data = {"amount": 50000}
        context = {"expected_amount": 100000}
        
        violations = validator.validate(data, context)
        assert len(violations) == 1
        assert violations[0].code == "amount_mismatch"
        assert violations[0].severity == "critical"
    
    def test_signature_mismatch_with_context(self):
        """Test signature mismatch detection."""
        from selfhealing.services.corruption_shield.validators import L2BusinessRulesValidator
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        validator = L2BusinessRulesValidator(config)
        
        data = {"signature": "abc123"}
        context = {"expected_signature": "xyz789"}
        
        violations = validator.validate(data, context)
        assert len(violations) == 1
        assert violations[0].code == "signature_mismatch"


class TestL3AnomalyDetector:
    """Tests for L3 Anomaly Detector."""
    
    def test_no_anomaly_with_insufficient_data(self):
        """Test no anomaly detected with insufficient historical data."""
        from selfhealing.services.corruption_shield.validators import L3AnomalyDetector
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(min_samples_for_anomaly=10)
        detector = L3AnomalyDetector(config)
        
        # Only 5 samples - not enough for anomaly detection
        for i in range(5):
            detector.validate({"amount": 50000})
        
        # This should not trigger anomaly (not enough data)
        violations = detector.validate({"amount": 1_000_000})
        assert len(violations) == 0
    
    def test_statistical_outlier_detected(self):
        """Test statistical outlier is detected with Z-score."""
        from selfhealing.services.corruption_shield.validators import L3AnomalyDetector
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(
            min_samples_for_anomaly=10,
            z_score_threshold=2.0,
        )
        detector = L3AnomalyDetector(config)
        
        # Build baseline with consistent amounts
        for _ in range(20):
            detector.validate({"amount": 50000})  # All ~50000
        
        # This is a huge outlier (>3 std devs) - but std dev is 0 when all same
        # So we need variance - use slightly different values
        detector2 = L3AnomalyDetector(config)
        import random
        for i in range(20):
            detector2.validate({"amount": 50000 + random.randint(-1000, 1000)})
        
        # Now test with a big outlier
        violations = detector2.validate({"amount": 500_000})  # 10x outlier
        
        # Should detect either statistical or IQR outlier
        has_outlier = any(v.code in ["statistical_outlier", "iqr_outlier"] for v in violations)
        assert has_outlier, f"Expected outlier detection, got {[v.code for v in violations]}"
    
    def test_iqr_outlier_detected(self):
        """Test IQR outlier is detected."""
        from selfhealing.services.corruption_shield.validators import L3AnomalyDetector
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(
            min_samples_for_anomaly=10,
            iqr_multiplier=1.5,
        )
        detector = L3AnomalyDetector(config)
        
        # Build baseline
        for i in range(20):
            detector.validate({"amount": 50000 + (i * 100)})  # 50000-52000
        
        # Check for outlier far outside IQR
        violations = detector.validate({"amount": 1_000_000})
        
        # Should detect either statistical or IQR outlier
        assert any(v.code in ["statistical_outlier", "iqr_outlier"] for v in violations)
    
    def test_get_stats(self):
        """Test get_stats returns detector statistics."""
        from selfhealing.services.corruption_shield.validators import L3AnomalyDetector
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        detector = L3AnomalyDetector(config)
        
        for _ in range(15):
            detector.validate({"amount": 50000})
        
        stats = detector.get_stats()
        
        assert stats["sample_count"] == 15
        assert stats["mean"] is not None
        assert stats["std"] is not None
    
    def test_reset(self):
        """Test reset clears detector state."""
        from selfhealing.services.corruption_shield.validators import L3AnomalyDetector
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig()
        detector = L3AnomalyDetector(config)
        
        for _ in range(15):
            detector.validate({"amount": 50000})
        
        detector.reset()
        
        stats = detector.get_stats()
        assert stats["sample_count"] == 0
        assert stats["mean"] is None


class TestCorruptionShield:
    """Tests for unified Corruption Shield."""
    
    def test_valid_data_passes_all_layers(self):
        """Test valid data passes all validation layers."""
        from selfhealing.services.corruption_shield.shield import CorruptionShield
        
        shield = CorruptionShield()
        
        data = {
            "amount": 50000,
            "order_id": "order_12345",
            "status": "DONE",
        }
        
        result = shield.validate(data)
        
        assert result.is_valid is True
        assert result.blocked is False
        assert result.l1_passed is True
        assert result.l2_passed is True
        assert result.l3_passed is True
        assert len(result.violations) == 0
    
    def test_l1_violation_blocks(self):
        """Test L1 violation blocks request."""
        from selfhealing.services.corruption_shield.shield import CorruptionShield
        
        shield = CorruptionShield()
        
        data = {
            "amount": 50000,
            "order_id": "'; DROP TABLE users; --",
        }
        
        result = shield.validate(data)
        
        assert result.is_valid is False
        assert result.blocked is True  # Critical violation
        assert result.l1_passed is False
    
    def test_l2_violation_blocks(self):
        """Test L2 violation blocks request."""
        from selfhealing.services.corruption_shield.shield import CorruptionShield
        
        shield = CorruptionShield()
        
        data = {
            "amount": -50000,  # Negative
            "order_id": "order_12345",
        }
        
        result = shield.validate(data)
        
        assert result.is_valid is False
        assert result.blocked is True  # Critical violation
        assert result.l2_passed is False
    
    def test_context_validation(self):
        """Test context-based validation."""
        from selfhealing.services.corruption_shield.shield import CorruptionShield
        
        shield = CorruptionShield()
        
        data = {
            "amount": 50000,
            "order_id": "order_12345",
        }
        context = {"expected_amount": 100000}  # Mismatch!
        
        result = shield.validate(data, context)
        
        assert result.is_valid is False
        assert result.blocked is True
        assert any(v.code == "amount_mismatch" for v in result.violations)
    
    def test_get_stats(self):
        """Test get_stats returns shield statistics."""
        from selfhealing.services.corruption_shield.shield import CorruptionShield
        
        shield = CorruptionShield()
        
        # Run some validations
        shield.validate({"amount": 50000, "order_id": "order_1"})
        shield.validate({"amount": -100, "order_id": "order_2"})  # Invalid
        shield.validate({"amount": 30000, "order_id": "order_3"})
        
        stats = shield.get_stats()
        
        assert stats["total_validations"] == 3
        assert stats["passed"] == 2
        assert stats["blocked"] >= 1
    
    def test_layers_can_be_disabled(self):
        """Test individual layers can be disabled."""
        from selfhealing.services.corruption_shield.shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(
            l1_enabled=False,  # Disable L1
            l2_enabled=True,
            l3_enabled=False,  # Disable L3
        )
        shield = CorruptionShield(config)
        
        # SQL injection should pass since L1 is disabled
        data = {
            "amount": 50000,
            "order_id": "'; DROP TABLE users; --",
        }
        
        result = shield.validate(data)
        
        # L1 disabled, so SQL injection not detected
        # Only L2 runs - amount is valid
        assert result.l1_passed is True  # Not checked
        assert result.l2_passed is True
    
    def test_reset(self):
        """Test reset clears shield state."""
        from selfhealing.services.corruption_shield.shield import CorruptionShield
        
        shield = CorruptionShield()
        
        for _ in range(10):
            shield.validate({"amount": 50000, "order_id": "order_123"})
        
        shield.reset()
        
        stats = shield.get_stats()
        assert stats["total_validations"] == 0


class TestGlobalShieldInstance:
    """Tests for global shield singleton."""
    
    def test_get_corruption_shield_singleton(self):
        """Test get_corruption_shield returns same instance."""
        from selfhealing.services.corruption_shield.shield import (
            get_corruption_shield,
            reset_corruption_shield,
        )
        
        reset_corruption_shield()
        
        s1 = get_corruption_shield()
        s2 = get_corruption_shield()
        
        assert s1 is s2
    
    def test_reset_creates_new_instance(self):
        """Test reset creates new instance."""
        from selfhealing.services.corruption_shield.shield import (
            get_corruption_shield,
            reset_corruption_shield,
        )
        
        s1 = get_corruption_shield()
        reset_corruption_shield()
        s2 = get_corruption_shield()
        
        assert s1 is not s2
