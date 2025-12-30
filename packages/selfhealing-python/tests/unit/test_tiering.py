"""
Tests for API Tiering System (Criticality-Based Load Shedding).

Tests the tier-based rate limiting system:
- TierDefinition data class
- TierMapping with pattern matching (exact, wildcard, regex)
- TierOverride with IP/user/API key matching
- TierConfigValidator (Safe Boundary rules)
- TierRegistry service layer
- Dry Run / Simulation

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md (Section 4)
"""

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest


# =============================================================================
# TierDefinition Tests
# =============================================================================


class TestTierDefinition:
    """Tests for TierDefinition data class."""

    def test_create_valid_tier(self):
        """Should create a valid tier definition."""
        from selfhealing.api.django.tiering import TierDefinition

        tier = TierDefinition(
            id="critical",
            name="Mission Critical",
            multiplier=0.5,
            priority=100,
            description="핵심 API",
            color="#FF0000",
        )

        assert tier.id == "critical"
        assert tier.name == "Mission Critical"
        assert tier.multiplier == 0.5
        assert tier.priority == 100

    def test_invalid_multiplier_negative(self):
        """Should reject negative multiplier."""
        from selfhealing.api.django.tiering import TierDefinition

        with pytest.raises(ValueError, match="between 0 and 1"):
            TierDefinition(
                id="test",
                name="Test",
                multiplier=-0.1,
            )

    def test_invalid_multiplier_over_one(self):
        """Should reject multiplier over 1."""
        from selfhealing.api.django.tiering import TierDefinition

        with pytest.raises(ValueError, match="between 0 and 1"):
            TierDefinition(
                id="test",
                name="Test",
                multiplier=1.5,
            )

    def test_empty_id_rejected(self):
        """Should reject empty tier ID."""
        from selfhealing.api.django.tiering import TierDefinition

        with pytest.raises(ValueError, match="ID is required"):
            TierDefinition(
                id="",
                name="Test",
                multiplier=0.5,
            )

    def test_to_dict(self):
        """Should convert to dictionary."""
        from selfhealing.api.django.tiering import TierDefinition

        tier = TierDefinition(
            id="critical",
            name="Critical",
            multiplier=0.5,
            priority=100,
        )

        data = tier.to_dict()

        assert data["id"] == "critical"
        assert data["name"] == "Critical"
        assert data["multiplier"] == 0.5
        assert data["priority"] == 100

    def test_from_dict(self):
        """Should create from dictionary."""
        from selfhealing.api.django.tiering import TierDefinition

        data = {
            "id": "standard",
            "name": "Standard",
            "multiplier": 0.1,
            "priority": 50,
            "description": "일반 API",
        }

        tier = TierDefinition.from_dict(data)

        assert tier.id == "standard"
        assert tier.multiplier == 0.1


# =============================================================================
# TierMapping Tests
# =============================================================================


class TestTierMapping:
    """Tests for TierMapping with pattern matching."""

    def test_exact_match(self):
        """Should match exact paths."""
        from selfhealing.api.django.tiering import TierMapping, PatternType

        mapping = TierMapping(
            pattern="/api/self-healing/control/",
            tier_id="critical",
            pattern_type=PatternType.EXACT,
        )

        assert mapping.matches("/api/self-healing/control/") is True
        assert mapping.matches("/api/self-healing/control/extra/") is False
        assert mapping.matches("/api/self-healing/") is False

    def test_wildcard_match(self):
        """Should match wildcard patterns."""
        from selfhealing.api.django.tiering import TierMapping, PatternType

        mapping = TierMapping(
            pattern="/api/self-healing/config/*",
            tier_id="standard",
            pattern_type=PatternType.WILDCARD,
        )

        assert mapping.matches("/api/self-healing/config/circuit-breaker") is True
        assert mapping.matches("/api/self-healing/config/dlq") is True
        # Note: fnmatch * matches empty string, so config/ matches config/*
        assert mapping.matches("/api/self-healing/config/") is True
        assert mapping.matches("/api/self-healing/control/") is False

    def test_wildcard_nested(self):
        """Should match nested wildcard patterns."""
        from selfhealing.api.django.tiering import TierMapping, PatternType

        mapping = TierMapping(
            pattern="/api/*/config/*",
            tier_id="standard",
            pattern_type=PatternType.WILDCARD,
        )

        assert mapping.matches("/api/self-healing/config/dlq") is True
        assert mapping.matches("/api/other/config/test") is True

    def test_regex_match(self):
        """Should match regex patterns."""
        from selfhealing.api.django.tiering import TierMapping, PatternType

        mapping = TierMapping(
            pattern=r"/api/self-healing/dashboard/.*",
            tier_id="non_essential",
            pattern_type=PatternType.REGEX,
        )

        assert mapping.matches("/api/self-healing/dashboard/summary") is True
        assert mapping.matches("/api/self-healing/dashboard/") is True
        assert mapping.matches("/api/self-healing/config/") is False

    def test_invalid_regex_rejected(self):
        """Should reject invalid regex patterns."""
        from selfhealing.api.django.tiering import TierMapping, PatternType

        with pytest.raises(ValueError, match="Invalid regex"):
            TierMapping(
                pattern="[invalid",
                tier_id="test",
                pattern_type=PatternType.REGEX,
            )

    def test_to_dict(self):
        """Should convert to dictionary."""
        from selfhealing.api.django.tiering import TierMapping, PatternType

        mapping = TierMapping(
            pattern="/api/test/*",
            tier_id="standard",
            pattern_type=PatternType.WILDCARD,
            priority=50,
        )

        data = mapping.to_dict()

        assert data["pattern"] == "/api/test/*"
        assert data["tier_id"] == "standard"
        assert data["pattern_type"] == "wildcard"

    def test_from_dict(self):
        """Should create from dictionary."""
        from selfhealing.api.django.tiering import TierMapping

        data = {
            "pattern": "/api/test/",
            "tier_id": "critical",
            "pattern_type": "exact",
            "priority": 100,
        }

        mapping = TierMapping.from_dict(data)

        assert mapping.pattern == "/api/test/"
        assert mapping.tier_id == "critical"


# =============================================================================
# TierOverride Tests
# =============================================================================


class TestTierOverride:
    """Tests for TierOverride with client matching."""

    def test_exact_ip_match(self):
        """Should match exact IP addresses."""
        from selfhealing.api.django.tiering import TierOverride, OverrideIdentifierType

        override = TierOverride(
            identifier="192.168.1.100",
            identifier_type=OverrideIdentifierType.IP,
            tier_id="critical",
        )

        assert override.matches_ip("192.168.1.100") is True
        assert override.matches_ip("192.168.1.101") is False

    def test_cidr_ip_match(self):
        """Should match CIDR notation IP ranges."""
        from selfhealing.api.django.tiering import TierOverride, OverrideIdentifierType

        override = TierOverride(
            identifier="10.0.0.0/8",
            identifier_type=OverrideIdentifierType.IP,
            tier_id="critical",
        )

        assert override.matches_ip("10.0.0.1") is True
        assert override.matches_ip("10.255.255.255") is True
        assert override.matches_ip("192.168.1.1") is False

    def test_user_id_match(self):
        """Should match user IDs."""
        from selfhealing.api.django.tiering import TierOverride, OverrideIdentifierType

        override = TierOverride(
            identifier="admin_user",
            identifier_type=OverrideIdentifierType.USER_ID,
            tier_id="critical",
        )

        assert override.matches("admin_user", OverrideIdentifierType.USER_ID) is True
        assert override.matches("other_user", OverrideIdentifierType.USER_ID) is False

    def test_expired_override_not_matched(self):
        """Should not match expired overrides."""
        from selfhealing.api.django.tiering import TierOverride, OverrideIdentifierType

        past_time = datetime.now(timezone.utc) - timedelta(hours=1)

        override = TierOverride(
            identifier="test_user",
            identifier_type=OverrideIdentifierType.USER_ID,
            tier_id="critical",
            expires_at=past_time,
        )

        assert override.is_expired() is True
        assert override.matches("test_user", OverrideIdentifierType.USER_ID) is False

    def test_non_expired_override_matched(self):
        """Should match non-expired overrides."""
        from selfhealing.api.django.tiering import TierOverride, OverrideIdentifierType

        future_time = datetime.now(timezone.utc) + timedelta(hours=1)

        override = TierOverride(
            identifier="test_user",
            identifier_type=OverrideIdentifierType.USER_ID,
            tier_id="critical",
            expires_at=future_time,
        )

        assert override.is_expired() is False
        assert override.matches("test_user", OverrideIdentifierType.USER_ID) is True

    def test_to_dict(self):
        """Should convert to dictionary."""
        from selfhealing.api.django.tiering import TierOverride, OverrideIdentifierType

        override = TierOverride(
            identifier="10.0.0.0/8",
            identifier_type=OverrideIdentifierType.IP,
            tier_id="critical",
            reason="Internal network",
        )

        data = override.to_dict()

        assert data["identifier"] == "10.0.0.0/8"
        assert data["identifier_type"] == "ip"
        assert data["tier_id"] == "critical"


# =============================================================================
# TierConfigValidator Tests
# =============================================================================


class TestTierConfigValidator:
    """Tests for TierConfigValidator (Safe Boundary)."""

    def test_valid_tier_config(self):
        """Should validate correct tier configuration."""
        from selfhealing.api.django.tiering import (
            TierConfigValidator,
            TierDefinition,
        )

        validator = TierConfigValidator()

        tiers = [
            TierDefinition(id="critical", name="Critical", multiplier=0.5, priority=100),
            TierDefinition(id="standard", name="Standard", multiplier=0.1, priority=50),
        ]

        result = validator.validate_tiers(tiers)

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_too_many_tiers_rejected(self):
        """Should reject configurations with too many tiers."""
        from selfhealing.api.django.tiering import (
            TierConfigValidator,
            TierDefinition,
        )

        validator = TierConfigValidator()

        # Create 11 tiers (max is 10)
        tiers = [TierDefinition(id=f"tier_{i}", name=f"Tier {i}", multiplier=0.5) for i in range(11)]

        result = validator.validate_tiers(tiers)

        assert result.is_valid is False
        assert any("최대" in e for e in result.errors)

    def test_duplicate_tier_ids_rejected(self):
        """Should reject duplicate tier IDs."""
        from selfhealing.api.django.tiering import (
            TierConfigValidator,
            TierDefinition,
        )

        validator = TierConfigValidator()

        tiers = [
            TierDefinition(id="same_id", name="First", multiplier=0.5),
            TierDefinition(id="same_id", name="Second", multiplier=0.3),
        ]

        result = validator.validate_tiers(tiers)

        assert result.is_valid is False
        assert any("중복" in e for e in result.errors)

    def test_missing_critical_tier_warning(self):
        """Should warn if critical tier is missing."""
        from selfhealing.api.django.tiering import (
            TierConfigValidator,
            TierDefinition,
        )

        validator = TierConfigValidator()

        tiers = [
            TierDefinition(id="standard", name="Standard", multiplier=0.5),
            TierDefinition(id="low", name="Low", multiplier=0.1),
        ]

        result = validator.validate_tiers(tiers)

        assert result.is_valid is True  # Still valid, just a warning
        assert any("critical" in w for w in result.warnings)

    def test_mapping_references_invalid_tier(self):
        """Should reject mappings referencing non-existent tiers."""
        from selfhealing.api.django.tiering import (
            TierConfigValidator,
            TierMapping,
            PatternType,
        )

        validator = TierConfigValidator()

        mappings = [
            TierMapping(
                pattern="/api/test/",
                tier_id="non_existent",
                pattern_type=PatternType.EXACT,
            )
        ]

        result = validator.validate_mappings(mappings, tier_ids=["critical", "standard"])

        assert result.is_valid is False
        assert any("존재하지 않는" in e for e in result.errors)

    def test_invalid_override_ip_rejected(self):
        """Should reject invalid IP addresses in overrides."""
        from selfhealing.api.django.tiering import (
            TierConfigValidator,
            TierOverride,
            OverrideIdentifierType,
        )

        validator = TierConfigValidator()

        overrides = [
            TierOverride(
                identifier="invalid.ip.address",
                identifier_type=OverrideIdentifierType.IP,
                tier_id="critical",
            )
        ]

        result = validator.validate_overrides(overrides, tier_ids=["critical"])

        assert result.is_valid is False
        assert any("잘못된 IP" in e for e in result.errors)


# =============================================================================
# TierRegistry Tests
# =============================================================================


class TestTierRegistry:
    """Tests for TierRegistry service layer."""

    def test_singleton_pattern(self):
        """Should return the same instance."""
        from selfhealing.api.django.tiering import get_tier_registry

        registry1 = get_tier_registry()
        registry2 = get_tier_registry()

        assert registry1 is registry2

    def test_default_tiers_loaded(self):
        """Should have default tiers loaded."""
        from selfhealing.api.django.tiering import TierRegistry

        # Create fresh instance for testing
        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        tiers = registry.get_all_tiers()

        assert len(tiers) >= 3
        tier_ids = [t.id for t in tiers]
        assert "critical" in tier_ids
        assert "standard" in tier_ids
        assert "non_essential" in tier_ids

    def test_get_tier_for_path_critical(self):
        """Should resolve critical paths correctly."""
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        tier = registry.get_tier_for_path("/api/self-healing/control/")

        assert tier is not None
        assert tier.id == "critical"

    def test_get_tier_for_path_standard(self):
        """Should resolve standard paths correctly."""
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        tier = registry.get_tier_for_path("/api/self-healing/config/circuit-breaker")

        assert tier is not None
        assert tier.id == "standard"

    def test_get_tier_for_path_non_essential(self):
        """Should resolve non-essential paths correctly."""
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        tier = registry.get_tier_for_path("/api/self-healing/dashboard/summary")

        assert tier is not None
        assert tier.id == "non_essential"

    def test_get_tier_for_unmatched_path(self):
        """Should return None for unmatched paths."""
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        tier = registry.get_tier_for_path("/api/unknown/endpoint/")

        assert tier is None

    def test_override_takes_precedence(self):
        """Override tier should take precedence over path-based tier."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            TierOverride,
            OverrideIdentifierType,
        )

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Dashboard is normally non_essential
        path = "/api/self-healing/dashboard/summary"
        path_tier = registry.get_tier_for_path(path)
        assert path_tier.id == "non_essential"

        # But with internal IP override, should be critical
        resolved = registry.resolve_tier(
            path=path,
            client_ip="10.0.0.1",  # Internal IP
        )

        assert resolved is not None
        assert resolved.id == "critical"

    def test_set_tiers_validates(self):
        """Should validate tiers before setting."""
        from selfhealing.api.django.tiering import TierRegistry, TierDefinition

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Invalid: no tiers
        result = registry.set_tiers([])

        assert result.is_valid is False

    def test_export_import_config(self):
        """Should export and import configuration."""
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Export
        config = registry.export_config()

        assert "tiers" in config
        assert "mappings" in config
        assert "overrides" in config

        # Import
        result = registry.import_config(config)

        assert result.is_valid is True

    def test_reset_to_defaults(self):
        """Should reset to default configuration."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            TierDefinition,
            DEFAULT_TIER_DEFINITIONS,
        )

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Modify configuration - must include critical for validation to pass
        result = registry.set_tiers(
            [
                TierDefinition(id="critical", name="Critical", multiplier=0.5),
                TierDefinition(id="custom", name="Custom", multiplier=0.3),
            ]
        )
        assert result.is_valid is True

        # Verify custom tier was added
        tier_ids_before = [t.id for t in registry.get_all_tiers()]
        assert "custom" in tier_ids_before

        # Reset
        registry.reset_to_defaults()

        # Should be back to defaults
        tiers = registry.get_all_tiers()
        tier_ids = [t.id for t in tiers]

        assert "critical" in tier_ids
        assert "custom" not in tier_ids


# =============================================================================
# Simulation Tests
# =============================================================================


class TestTierSimulation:
    """Tests for tier configuration simulation (dry run)."""

    def test_simulate_no_changes(self):
        """Should detect no changes when using same config."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            DEFAULT_TIER_DEFINITIONS,
            DEFAULT_TIER_MAPPINGS,
        )

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        result = registry.simulate(
            tiers=list(DEFAULT_TIER_DEFINITIONS),
            mappings=list(DEFAULT_TIER_MAPPINGS),
        )

        assert result["status"] == "success"
        assert result["statistics"]["changed_count"] == 0

    def test_simulate_detects_changes(self):
        """Should detect changes when config differs."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            TierDefinition,
            TierMapping,
            PatternType,
            DEFAULT_TIER_DEFINITIONS,
        )

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Change dashboard from non_essential to critical
        new_mappings = [
            TierMapping(
                pattern="/api/self-healing/dashboard/*",
                tier_id="critical",  # Changed!
                pattern_type=PatternType.WILDCARD,
                priority=100,
            ),
        ]

        result = registry.simulate(
            tiers=list(DEFAULT_TIER_DEFINITIONS),
            mappings=new_mappings,
            test_paths=["/api/self-healing/dashboard/summary"],
        )

        assert result["status"] == "success"
        assert result["statistics"]["changed_count"] >= 1

    def test_simulate_validation_failure(self):
        """Should return validation errors."""
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Empty tiers should fail validation
        result = registry.simulate(
            tiers=[],
            mappings=[],
        )

        assert result["status"] == "error"


# =============================================================================
# Pattern Matching Edge Cases
# =============================================================================


class TestPatternMatchingEdgeCases:
    """Tests for pattern matching edge cases."""

    def test_wildcard_single_segment(self):
        """Wildcard should match single path segment."""
        from selfhealing.api.django.tiering import TierMapping, PatternType

        mapping = TierMapping(
            pattern="/api/*/status/",
            tier_id="test",
            pattern_type=PatternType.WILDCARD,
        )

        assert mapping.matches("/api/selfhealing/status/") is True
        assert mapping.matches("/api/other/status/") is True

    def test_regex_complex_pattern(self):
        """Complex regex patterns should work."""
        from selfhealing.api.django.tiering import TierMapping, PatternType

        mapping = TierMapping(
            pattern=r"/api/v[0-9]+/.*",
            tier_id="test",
            pattern_type=PatternType.REGEX,
        )

        assert mapping.matches("/api/v1/users") is True
        assert mapping.matches("/api/v2/orders/123") is True
        assert mapping.matches("/api/vx/test") is False

    def test_priority_ordering(self):
        """Higher priority mappings should match first."""
        from selfhealing.api.django.tiering import TierRegistry, TierMapping, PatternType

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Add conflicting mappings with different priorities
        registry.set_mappings(
            [
                TierMapping(
                    pattern="/api/self-healing/*",
                    tier_id="standard",
                    pattern_type=PatternType.WILDCARD,
                    priority=10,  # Lower priority
                ),
                TierMapping(
                    pattern="/api/self-healing/control/*",
                    tier_id="critical",
                    pattern_type=PatternType.WILDCARD,
                    priority=100,  # Higher priority
                ),
            ]
        )

        # Control path should match critical (higher priority)
        tier = registry.get_tier_for_path("/api/self-healing/control/allow")
        assert tier is not None
        assert tier.id == "critical"

        # Other paths should match standard
        tier = registry.get_tier_for_path("/api/self-healing/config/test")
        assert tier is not None
        assert tier.id == "standard"


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestTierRegistryThreadSafety:
    """Tests for thread safety of TierRegistry."""

    def test_concurrent_reads(self):
        """Should handle concurrent reads safely."""
        import threading
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        results = []
        errors = []

        def read_tiers():
            try:
                for _ in range(100):
                    tiers = registry.get_all_tiers()
                    results.append(len(tiers))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_tiers) for _ in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 500  # 5 threads * 100 reads

    def test_concurrent_reads_and_writes(self):
        """Should handle concurrent reads and writes safely."""
        import threading
        from selfhealing.api.django.tiering import TierRegistry, TierDefinition

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        errors = []

        def read_tiers():
            try:
                for _ in range(50):
                    registry.get_all_tiers()
                    registry.get_tier_for_path("/api/self-healing/control/")
            except Exception as e:
                errors.append(e)

        def write_tiers():
            try:
                for _ in range(10):
                    registry.set_tiers(
                        [
                            TierDefinition(id="critical", name="Critical", multiplier=0.5),
                            TierDefinition(id="standard", name="Standard", multiplier=0.1),
                        ]
                    )
            except Exception as e:
                errors.append(e)

        read_threads = [threading.Thread(target=read_tiers) for _ in range(3)]
        write_threads = [threading.Thread(target=write_tiers) for _ in range(2)]

        all_threads = read_threads + write_threads

        for t in all_threads:
            t.start()
        for t in all_threads:
            t.join()

        assert len(errors) == 0


# =============================================================================
# Defense-in-Depth Tests (Static Critical Paths + Circuit Breaker)
# =============================================================================


class TestStaticCriticalPaths:
    """Tests for L1 Static Critical Paths (hardcoded fallback)."""

    def test_static_critical_paths_defined(self):
        """Should have static critical paths defined."""
        from selfhealing.api.django.tiering import (
            STATIC_CRITICAL_PATHS,
            STATIC_CRITICAL_PREFIXES,
        )

        assert "/api/self-healing/control/" in STATIC_CRITICAL_PATHS
        assert "/api/self-healing/emergency/" in STATIC_CRITICAL_PATHS
        assert "/api/auth/token/" in STATIC_CRITICAL_PATHS

        assert "/api/self-healing/control/" in STATIC_CRITICAL_PREFIXES
        assert "/api/self-healing/emergency/" in STATIC_CRITICAL_PREFIXES

    def test_static_critical_paths_immutable(self):
        """Static critical paths should be immutable (frozenset)."""
        from selfhealing.api.django.tiering import STATIC_CRITICAL_PATHS

        assert isinstance(STATIC_CRITICAL_PATHS, frozenset)

        # Should not be modifiable
        with pytest.raises(AttributeError):
            STATIC_CRITICAL_PATHS.add("/api/new/")

    def test_is_static_critical_exact_match(self):
        """Should identify exact static critical paths."""
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        assert registry._is_static_critical("/api/self-healing/control/") is True
        assert registry._is_static_critical("/api/self-healing/emergency/") is True
        assert registry._is_static_critical("/api/auth/token/") is True

    def test_is_static_critical_prefix_match(self):
        """Should identify paths starting with static critical prefixes."""
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        assert registry._is_static_critical("/api/self-healing/control/allow/") is True
        assert registry._is_static_critical("/api/self-healing/emergency/trigger/") is True
        assert registry._is_static_critical("/api/self-healing/control/block/") is True

    def test_is_static_critical_non_critical_paths(self):
        """Should not match non-critical paths."""
        from selfhealing.api.django.tiering import TierRegistry

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        assert registry._is_static_critical("/api/dashboard/") is False
        assert registry._is_static_critical("/api/self-healing/config/") is False
        assert registry._is_static_critical("/api/users/") is False


class TestTierFallbackReason:
    """Tests for TierFallbackReason enum."""

    def test_fallback_reason_values(self):
        """Should have all expected fallback reasons."""
        from selfhealing.api.django.tiering import TierFallbackReason

        assert TierFallbackReason.NONE.value == "none"
        assert TierFallbackReason.CONFIG_MISSING.value == "config_missing"
        assert TierFallbackReason.ENGINE_ERROR.value == "engine_error"
        assert TierFallbackReason.ENGINE_TIMEOUT.value == "engine_timeout"
        assert TierFallbackReason.CIRCUIT_OPEN.value == "circuit_open"
        assert TierFallbackReason.STATIC_PATH_MATCH.value == "static_path_match"


class TestTierResult:
    """Tests for TierResult dataclass."""

    def test_tier_result_creation(self):
        """Should create TierResult with all fields."""
        from selfhealing.api.django.tiering import TierResult, TierFallbackReason

        result = TierResult(
            tier_id="critical",
            multiplier=0.5,
            is_fallback=True,
            fallback_reason=TierFallbackReason.STATIC_PATH_MATCH,
            latency_ms=1.5,
        )

        assert result.tier_id == "critical"
        assert result.multiplier == 0.5
        assert result.is_fallback is True
        assert result.fallback_reason == TierFallbackReason.STATIC_PATH_MATCH
        assert result.latency_ms == 1.5


class TestTieringCircuitBreaker:
    """Tests for TieringCircuitBreaker."""

    def test_initial_state_closed(self):
        """Circuit breaker should start CLOSED."""
        from selfhealing.api.django.tiering import TieringCircuitBreaker

        cb = TieringCircuitBreaker.__new__(TieringCircuitBreaker)
        cb._init()

        assert cb.state == "CLOSED"
        assert cb.is_open is False

    def test_record_success_resets_failures(self):
        """Recording success should reset failure count."""
        from selfhealing.api.django.tiering import TieringCircuitBreaker

        cb = TieringCircuitBreaker.__new__(TieringCircuitBreaker)
        cb._init()

        # Record some failures (not enough to trip)
        for _ in range(3):
            cb.record_failure(Exception("test"))

        assert cb._failure_count == 3

        # Success resets
        cb.record_success(latency_ms=10.0)

        assert cb._failure_count == 0
        assert cb.state == "CLOSED"

    def test_trips_after_threshold_failures(self):
        """Should trip to OPEN after threshold failures."""
        from selfhealing.api.django.tiering import TieringCircuitBreaker

        cb = TieringCircuitBreaker.__new__(TieringCircuitBreaker)
        cb._init()

        # Record failures up to threshold
        for i in range(cb.FAILURE_THRESHOLD):
            cb.record_failure(Exception(f"test {i}"))

        assert cb.state == "OPEN"
        assert cb.is_open is True

    def test_trips_after_slow_responses(self):
        """Should trip to OPEN after too many slow responses."""
        from selfhealing.api.django.tiering import TieringCircuitBreaker

        cb = TieringCircuitBreaker.__new__(TieringCircuitBreaker)
        cb._init()

        # Record slow successes (above TIMEOUT_MS threshold)
        for _ in range(cb.SLOW_THRESHOLD):
            cb.record_success(latency_ms=100.0)  # > 50ms

        assert cb.state == "OPEN"
        assert cb.is_open is True

    def test_fast_responses_reset_slow_count(self):
        """Fast responses should reset slow count."""
        from selfhealing.api.django.tiering import TieringCircuitBreaker

        cb = TieringCircuitBreaker.__new__(TieringCircuitBreaker)
        cb._init()

        # Record some slow responses
        for _ in range(5):
            cb.record_success(latency_ms=100.0)

        assert cb._slow_count == 5

        # Fast response resets slow count
        cb.record_success(latency_ms=10.0)

        assert cb._slow_count == 0

    def test_half_open_transition(self):
        """Should transition to HALF_OPEN after delay."""
        import time
        from selfhealing.api.django.tiering import TieringCircuitBreaker

        cb = TieringCircuitBreaker.__new__(TieringCircuitBreaker)
        cb._init()
        cb.HALF_OPEN_DELAY_SEC = 0.1  # Short delay for testing

        # Trip the circuit
        for _ in range(cb.FAILURE_THRESHOLD):
            cb.record_failure(Exception("test"))

        assert cb.state == "OPEN"

        # Wait for half-open transition
        time.sleep(0.15)

        # Checking is_open should trigger transition
        assert cb.is_open is False
        assert cb.state == "HALF_OPEN"

    def test_half_open_success_closes(self):
        """Success in HALF_OPEN should close circuit."""
        from selfhealing.api.django.tiering import TieringCircuitBreaker

        cb = TieringCircuitBreaker.__new__(TieringCircuitBreaker)
        cb._init()
        cb._state = "HALF_OPEN"

        cb.record_success(latency_ms=10.0)

        assert cb.state == "CLOSED"

    def test_reset(self):
        """Reset should clear all state."""
        from selfhealing.api.django.tiering import TieringCircuitBreaker

        cb = TieringCircuitBreaker.__new__(TieringCircuitBreaker)
        cb._init()

        # Trip the circuit
        for _ in range(cb.FAILURE_THRESHOLD):
            cb.record_failure(Exception("test"))

        assert cb.state == "OPEN"

        cb.reset()

        assert cb.state == "CLOSED"
        assert cb._failure_count == 0
        assert cb._slow_count == 0

