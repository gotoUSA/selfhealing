"""
Tiering System Data Models.

Data classes for tier definitions, mappings, overrides, and results.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from typing import Any, Dict, Optional

from .enums import PatternType, OverrideIdentifierType, TierFallbackReason


@dataclass
class TierResult:
    """Tier resolution result with fallback tracking."""
    tier_id: str
    multiplier: float
    is_fallback: bool
    fallback_reason: TierFallbackReason
    latency_ms: float


@dataclass
class TierDefinition:
    """
    Tier definition with emergency mode behavior.
    
    Attributes:
        id: Unique identifier (e.g., "critical")
        name: Display name (e.g., "Mission Critical")
        multiplier: Emergency mode rate multiplier (0.0 ~ 1.0)
        priority: Priority level (higher = more important)
        description: Description of the tier
        color: UI display color
    """
    id: str
    name: str
    multiplier: float  # 0.0 = blocked, 1.0 = full access
    priority: int = 0
    description: str = ""
    color: str = "#000000"
    
    def __post_init__(self):
        """Validate tier definition."""
        if self.multiplier < 0 or self.multiplier > 1:
            raise ValueError(f"Multiplier must be between 0 and 1, got {self.multiplier}")
        if not self.id:
            raise ValueError("Tier ID is required")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "multiplier": self.multiplier,
            "priority": self.priority,
            "description": self.description,
            "color": self.color,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TierDefinition":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            multiplier=data["multiplier"],
            priority=data.get("priority", 0),
            description=data.get("description", ""),
            color=data.get("color", "#000000"),
        )


@dataclass
class TierMapping:
    """
    API path to tier mapping.
    
    Attributes:
        pattern: Path pattern (exact, wildcard, or regex)
        tier_id: Target tier ID
        pattern_type: Type of pattern matching
        priority: Mapping priority (higher = matched first)
        description: Description of the mapping
    """
    pattern: str
    tier_id: str
    pattern_type: PatternType = PatternType.EXACT
    priority: int = 0
    description: str = ""
    
    # Compiled regex cache
    _compiled_pattern: Optional[re.Pattern] = field(default=None, repr=False, compare=False)
    
    def __post_init__(self):
        """Compile regex pattern if needed."""
        if self.pattern_type == PatternType.REGEX:
            try:
                self._compiled_pattern = re.compile(self.pattern)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{self.pattern}': {e}")
    
    def matches(self, path: str) -> bool:
        """
        Check if the path matches this mapping.
        
        Args:
            path: API path to check
            
        Returns:
            True if path matches
        """
        if self.pattern_type == PatternType.EXACT:
            return path == self.pattern
        elif self.pattern_type == PatternType.WILDCARD:
            return fnmatch.fnmatch(path, self.pattern)
        elif self.pattern_type == PatternType.REGEX:
            if self._compiled_pattern is None:
                self._compiled_pattern = re.compile(self.pattern)
            return bool(self._compiled_pattern.match(path))
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pattern": self.pattern,
            "tier_id": self.tier_id,
            "pattern_type": self.pattern_type.value,
            "priority": self.priority,
            "description": self.description,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TierMapping":
        """Create from dictionary."""
        return cls(
            pattern=data["pattern"],
            tier_id=data["tier_id"],
            pattern_type=PatternType(data.get("pattern_type", "exact")),
            priority=data.get("priority", 0),
            description=data.get("description", ""),
        )


@dataclass
class TierOverride:
    """
    Per-client tier override.
    
    Attributes:
        identifier: IP, user ID, or API key
        identifier_type: Type of identifier
        tier_id: Tier to apply
        reason: Reason for override
        expires_at: Optional expiration time
    """
    identifier: str
    identifier_type: OverrideIdentifierType
    tier_id: str
    reason: str = ""
    expires_at: Optional[datetime] = None
    
    def is_expired(self) -> bool:
        """Check if override has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at
    
    def matches_ip(self, client_ip: str) -> bool:
        """
        Check if the client IP matches this override.
        
        Supports both exact IP and CIDR notation.
        """
        if self.identifier_type != OverrideIdentifierType.IP:
            return False
        
        try:
            client = ip_address(client_ip)
            if "/" in self.identifier:
                # CIDR notation
                network = ip_network(self.identifier, strict=False)
                return client in network
            else:
                # Exact IP
                return client == ip_address(self.identifier)
        except ValueError:
            return False
    
    def matches(self, identifier: str, identifier_type: OverrideIdentifierType) -> bool:
        """Check if identifier matches this override."""
        if self.identifier_type != identifier_type:
            return False
        
        if self.is_expired():
            return False
        
        if identifier_type == OverrideIdentifierType.IP:
            return self.matches_ip(identifier)
        
        return self.identifier == identifier

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "identifier": self.identifier,
            "identifier_type": self.identifier_type.value,
            "tier_id": self.tier_id,
            "reason": self.reason,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TierOverride":
        """Create from dictionary."""
        expires_at = None
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
        
        return cls(
            identifier=data["identifier"],
            identifier_type=OverrideIdentifierType(data["identifier_type"]),
            tier_id=data["tier_id"],
            reason=data.get("reason", ""),
            expires_at=expires_at,
        )
