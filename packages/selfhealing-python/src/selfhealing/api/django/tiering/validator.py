"""
Tiering Configuration Validator.

Validates tier configurations with Safe Boundary rules.
Prevents dangerous configurations that could break the system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from typing import Any, Dict, List

from .enums import PatternType, OverrideIdentifierType
from .models import TierDefinition, TierMapping, TierOverride


@dataclass
class ValidationResult:
    """Validation result for tier configuration."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class TierConfigValidator:
    """
    Tier configuration validator with Safe Boundary rules.
    
    Prevents dangerous configurations that could break the system.
    """
    
    # Safe Boundary Rules
    RULES = {
        "max_multiplier": 1.0,           # Multiplier cannot exceed 1.0
        "min_tiers": 1,                  # At least 1 tier required
        "max_tiers": 10,                 # Maximum 10 tiers
        "require_critical_tier": True,   # Critical tier is recommended
        "min_critical_multiplier": 0.1,  # Critical tier minimum 10% access
    }
    
    def validate_tiers(self, tier_definitions: List[TierDefinition]) -> ValidationResult:
        """
        Validate tier definitions.
        
        Args:
            tier_definitions: List of tier definitions
            
        Returns:
            ValidationResult with errors and warnings
        """
        errors: List[str] = []
        warnings: List[str] = []
        
        # Rule 1: Minimum tier count
        if len(tier_definitions) < self.RULES["min_tiers"]:
            errors.append(f"최소 {self.RULES['min_tiers']}개의 티어가 필요합니다.")
        
        # Rule 2: Maximum tier count
        if len(tier_definitions) > self.RULES["max_tiers"]:
            errors.append(
                f"티어는 최대 {self.RULES['max_tiers']}개까지 허용됩니다. "
                f"(현재: {len(tier_definitions)}개)"
            )
        
        # Check for duplicate IDs
        tier_ids = [t.id for t in tier_definitions]
        if len(tier_ids) != len(set(tier_ids)):
            errors.append("중복된 티어 ID가 있습니다.")
        
        for tier in tier_definitions:
            # Rule 3: Multiplier range
            if tier.multiplier < 0:
                errors.append(
                    f"티어 '{tier.id}': 배율은 0 이상이어야 합니다. "
                    f"(현재: {tier.multiplier})"
                )
            if tier.multiplier > self.RULES["max_multiplier"]:
                errors.append(
                    f"티어 '{tier.id}': 배율은 {self.RULES['max_multiplier']}를 "
                    f"초과할 수 없습니다. (현재: {tier.multiplier})"
                )
        
        # Rule 4: Critical tier recommendation
        if self.RULES["require_critical_tier"]:
            critical_tiers = [t for t in tier_definitions if t.id == "critical"]
            if not critical_tiers:
                warnings.append(
                    "'critical' 티어가 없습니다. "
                    "비상 시 핵심 API 보호가 어려울 수 있습니다."
                )
            elif critical_tiers[0].multiplier < self.RULES["min_critical_multiplier"]:
                warnings.append(
                    f"'critical' 티어 배율이 너무 낮습니다. "
                    f"최소 {self.RULES['min_critical_multiplier']} 권장. "
                    f"(현재: {critical_tiers[0].multiplier})"
                )
        
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
    
    def validate_mappings(
        self, 
        mappings: List[TierMapping], 
        tier_ids: List[str]
    ) -> ValidationResult:
        """
        Validate tier mappings.
        
        Args:
            mappings: List of tier mappings
            tier_ids: List of valid tier IDs
            
        Returns:
            ValidationResult with errors and warnings
        """
        errors: List[str] = []
        warnings: List[str] = []
        
        for mapping in mappings:
            # Check tier exists
            if mapping.tier_id not in tier_ids:
                errors.append(
                    f"매핑 '{mapping.pattern}': 존재하지 않는 티어 ID "
                    f"'{mapping.tier_id}'를 참조합니다."
                )
            
            # Validate regex patterns
            if mapping.pattern_type == PatternType.REGEX:
                try:
                    re.compile(mapping.pattern)
                except re.error as e:
                    errors.append(
                        f"매핑 '{mapping.pattern}': 잘못된 정규식 - {e}"
                    )
        
        # Check for overlapping patterns (warning only)
        patterns = [m.pattern for m in mappings]
        if len(patterns) != len(set(patterns)):
            warnings.append("중복된 패턴이 있습니다. 우선순위에 따라 처리됩니다.")
        
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
    
    def validate_overrides(
        self,
        overrides: List[TierOverride],
        tier_ids: List[str]
    ) -> ValidationResult:
        """
        Validate tier overrides.
        
        Args:
            overrides: List of tier overrides
            tier_ids: List of valid tier IDs
            
        Returns:
            ValidationResult with errors and warnings
        """
        errors: List[str] = []
        warnings: List[str] = []
        
        for override in overrides:
            # Check tier exists
            if override.tier_id not in tier_ids:
                errors.append(
                    f"오버라이드 '{override.identifier}': 존재하지 않는 티어 ID "
                    f"'{override.tier_id}'를 참조합니다."
                )
            
            # Validate IP format
            if override.identifier_type == OverrideIdentifierType.IP:
                try:
                    if "/" in override.identifier:
                        ip_network(override.identifier, strict=False)
                    else:
                        ip_address(override.identifier)
                except ValueError as e:
                    errors.append(
                        f"오버라이드 '{override.identifier}': 잘못된 IP 형식 - {e}"
                    )
            
            # Check for expired overrides
            if override.is_expired():
                warnings.append(
                    f"오버라이드 '{override.identifier}'가 만료되었습니다."
                )
        
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
    
    def validate_all(
        self,
        tiers: List[TierDefinition],
        mappings: List[TierMapping],
        overrides: List[TierOverride],
    ) -> ValidationResult:
        """
        Validate all tier configurations.
        
        Args:
            tiers: Tier definitions
            mappings: Tier mappings
            overrides: Tier overrides
            
        Returns:
            Combined ValidationResult
        """
        tier_result = self.validate_tiers(tiers)
        tier_ids = [t.id for t in tiers]
        
        mapping_result = self.validate_mappings(mappings, tier_ids)
        override_result = self.validate_overrides(overrides, tier_ids)
        
        return ValidationResult(
            is_valid=(
                tier_result.is_valid and 
                mapping_result.is_valid and 
                override_result.is_valid
            ),
            errors=tier_result.errors + mapping_result.errors + override_result.errors,
            warnings=tier_result.warnings + mapping_result.warnings + override_result.warnings,
        )
