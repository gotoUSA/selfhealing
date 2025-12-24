"""
API Tiering System for Criticality-Based Load Shedding.

Provides tier-based rate limiting where different APIs get different
treatment during emergency mode. Critical APIs get priority access
while non-essential APIs are shed first.

Tier Hierarchy:
- Tier 1 (Critical): Self-healing actions, payment APIs - 50% allowed in emergency
- Tier 2 (Standard): Config changes, DLQ replay - 10% allowed in emergency
- Tier 3 (Non-Essential): Dashboard, metrics - Blocked in emergency

Defense-in-Depth Strategy:
- L1: Static Critical Paths (hardcoded, code deployment required)
- L2: Dynamic Mappings (DB/Redis)
- L3: Default Tier (Fail-Closed: non_essential)
- Circuit Breaker: Bypass tiering when engine is slow/failing

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1A.md (Section 3)
"""

from __future__ import annotations

import fnmatch
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from ipaddress import ip_address, ip_network
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# L1: Static Critical Paths (Defense-in-Depth - Last Line of Defense)
# =============================================================================

# Immutable set - requires code deployment to change
STATIC_CRITICAL_PATHS = frozenset([
    "/api/self-healing/control/",
    "/api/self-healing/emergency/",
    "/api/auth/token/",
])

# Prefix matching optimization (tuple for startswith)
STATIC_CRITICAL_PREFIXES = (
    "/api/self-healing/control/",
    "/api/self-healing/emergency/",
)


class TierFallbackReason(Enum):
    """Fallback reason for Shadow Audit tracking."""
    NONE = "none"
    CONFIG_MISSING = "config_missing"
    ENGINE_ERROR = "engine_error"
    ENGINE_TIMEOUT = "engine_timeout"
    CIRCUIT_OPEN = "circuit_open"
    STATIC_PATH_MATCH = "static_path_match"


@dataclass
class TierResult:
    """Tier resolution result with fallback tracking."""
    tier_id: str
    multiplier: float
    is_fallback: bool
    fallback_reason: TierFallbackReason
    latency_ms: float


# =============================================================================
# Data Classes
# =============================================================================


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


class PatternType(str, Enum):
    """Pattern matching type for tier mappings."""
    EXACT = "exact"
    WILDCARD = "wildcard"
    REGEX = "regex"


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


class OverrideIdentifierType(str, Enum):
    """Type of override identifier."""
    IP = "ip"
    USER_ID = "user_id"
    API_KEY = "api_key"


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


# =============================================================================
# Default Templates (Best Practices)
# =============================================================================


DEFAULT_TIER_DEFINITIONS: List[TierDefinition] = [
    TierDefinition(
        id="critical",
        name="Mission Critical",
        multiplier=0.5,  # 50% allowed in emergency
        priority=100,
        description="장애 시에도 반드시 동작해야 하는 핵심 API",
        color="#FF0000",
    ),
    TierDefinition(
        id="standard",
        name="Operational",
        multiplier=0.1,  # 10% allowed in emergency
        priority=50,
        description="일반 운영 API",
        color="#FFA500",
    ),
    TierDefinition(
        id="non_essential",
        name="Non-Essential",
        multiplier=0.0,  # Blocked in emergency
        priority=10,
        description="비필수 API (Load Shedding 대상)",
        color="#808080",
    ),
]


DEFAULT_TIER_MAPPINGS: List[TierMapping] = [
    # Critical (Tier 1) - Self-healing control actions
    TierMapping(
        pattern="/api/self-healing/control/",
        tier_id="critical",
        pattern_type=PatternType.EXACT,
        priority=100,
        description="자가치유 제어 액션",
    ),
    TierMapping(
        pattern="/api/self-healing/allow/*",
        tier_id="critical",
        pattern_type=PatternType.WILDCARD,
        priority=100,
        description="자가치유 허용 액션",
    ),
    TierMapping(
        pattern="/api/self-healing/block/*",
        tier_id="critical",
        pattern_type=PatternType.WILDCARD,
        priority=100,
        description="자가치유 차단 액션",
    ),
    TierMapping(
        pattern="/api/self-healing/system/*",
        tier_id="critical",
        pattern_type=PatternType.WILDCARD,
        priority=95,
        description="킬 스위치 등 시스템 제어",
    ),
    
    # Standard (Tier 2) - Operational tasks
    TierMapping(
        pattern="/api/self-healing/config/*",
        tier_id="standard",
        pattern_type=PatternType.WILDCARD,
        priority=50,
        description="설정 변경 API",
    ),
    TierMapping(
        pattern="/api/self-healing/dlq/*",
        tier_id="standard",
        pattern_type=PatternType.WILDCARD,
        priority=50,
        description="DLQ 관련 API",
    ),
    TierMapping(
        pattern="/api/self-healing/audit/",
        tier_id="standard",
        pattern_type=PatternType.EXACT,
        priority=50,
        description="감사 로그 조회",
    ),
    TierMapping(
        pattern="/api/self-healing/status/*",
        tier_id="standard",
        pattern_type=PatternType.WILDCARD,
        priority=50,
        description="상태 조회",
    ),
    
    # Non-Essential (Tier 3) - Dashboard, metrics
    TierMapping(
        pattern="/api/self-healing/dashboard/*",
        tier_id="non_essential",
        pattern_type=PatternType.WILDCARD,
        priority=10,
        description="대시보드 API",
    ),
    TierMapping(
        pattern="/api/self-healing/metrics/",
        tier_id="non_essential",
        pattern_type=PatternType.EXACT,
        priority=10,
        description="메트릭 조회 API",
    ),
    TierMapping(
        pattern=r"/api/self-healing/chaos/reports/.*",
        tier_id="non_essential",
        pattern_type=PatternType.REGEX,
        priority=10,
        description="카오스 리포트 API",
    ),
]


DEFAULT_TIER_OVERRIDES: List[TierOverride] = [
    TierOverride(
        identifier="10.0.0.0/8",
        identifier_type=OverrideIdentifierType.IP,
        tier_id="critical",
        reason="Internal monitoring system",
    ),
    TierOverride(
        identifier="172.16.0.0/12",
        identifier_type=OverrideIdentifierType.IP,
        tier_id="critical",
        reason="Internal network",
    ),
    TierOverride(
        identifier="192.168.0.0/16",
        identifier_type=OverrideIdentifierType.IP,
        tier_id="critical",
        reason="Internal network",
    ),
]


# =============================================================================
# =============================================================================
# Tiering Circuit Breaker (Meta Circuit Breaker for Tiering Engine)
# =============================================================================


class TieringCircuitBreaker:
    """
    Circuit Breaker for Tiering Engine itself.
    
    When RegEx evaluation is slow or failing, bypass tiering
    and use static fallback. Prevents tiering from becoming
    a performance bottleneck.
    
    Reference: Envoy Proxy routing bypass pattern
    """
    
    FAILURE_THRESHOLD = 5       # 5 consecutive failures → OPEN
    TIMEOUT_MS = 50             # 50ms → count as slow
    SLOW_THRESHOLD = 10         # 10 slow responses → OPEN
    HALF_OPEN_DELAY_SEC = 30    # 30s before trying again
    
    _instance: Optional["TieringCircuitBreaker"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "TieringCircuitBreaker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance
    
    def _init(self):
        """Initialize circuit breaker state."""
        self._state = "CLOSED"
        self._failure_count = 0
        self._slow_count = 0
        self._last_failure_time: Optional[float] = None
        self._state_lock = threading.Lock()
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open (tiering should be bypassed)."""
        with self._state_lock:
            if self._state == "OPEN":
                if self._last_failure_time and \
                   time.time() - self._last_failure_time > self.HALF_OPEN_DELAY_SEC:
                    self._state = "HALF_OPEN"
                    logger.info("[TieringCB] Transitioning to HALF_OPEN")
                    return False
                return True
            return False
    
    @property
    def state(self) -> str:
        """Get current circuit breaker state."""
        with self._state_lock:
            return self._state
    
    def record_success(self, latency_ms: float):
        """Record successful tiering evaluation."""
        with self._state_lock:
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                logger.info("[TieringCB] CLOSED - recovered")
                self._record_metrics(is_open=False)
            
            self._failure_count = 0
            
            if latency_ms > self.TIMEOUT_MS:
                self._slow_count += 1
                if self._slow_count >= self.SLOW_THRESHOLD:
                    self._trip(f"slow_responses ({self._slow_count} > {self.SLOW_THRESHOLD})")
            else:
                self._slow_count = 0
    
    def record_failure(self, error: Exception):
        """Record failed tiering evaluation."""
        with self._state_lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._failure_count >= self.FAILURE_THRESHOLD:
                self._trip(f"failures: {error}")
    
    def _trip(self, reason: str):
        """Open the circuit breaker."""
        self._state = "OPEN"
        self._last_failure_time = time.time()
        logger.critical(f"[TieringCB] OPEN - {reason}")
        self._record_metrics(is_open=True)
        self._log_shadow_audit(reason)
    
    def _record_metrics(self, is_open: bool):
        """Update Prometheus metrics."""
        try:
            from prometheus_client import Gauge
            gauge = Gauge(
                'selfhealing_tiering_circuit_open',
                'Tiering circuit breaker state (1=open)',
                registry=None,  # Use default registry
            )
            gauge.set(1 if is_open else 0)
        except Exception:
            pass  # Best-effort metrics
    
    def _log_shadow_audit(self, reason: str):
        """Log circuit breaker trip to audit."""
        try:
            from selfhealing.audit import log_config_change
            
            log_config_change(
                config_type="tiering_circuit_breaker",
                config_key="circuit_state",
                old_value="CLOSED",
                new_value={
                    "state": "OPEN",
                    "reason": reason,
                    "failure_count": self._failure_count,
                    "slow_count": self._slow_count,
                    "severity": "critical",
                    "tag": "TIERING_CB_OPEN",
                },
                user="system",
            )
        except Exception as e:
            logger.error(f"[TieringCB] Shadow audit failed: {e}")
    
    def reset(self):
        """Reset circuit breaker (for testing)."""
        with self._state_lock:
            self._state = "CLOSED"
            self._failure_count = 0
            self._slow_count = 0
            self._last_failure_time = None


def get_tiering_circuit_breaker() -> TieringCircuitBreaker:
    """Get singleton TieringCircuitBreaker instance."""
    return TieringCircuitBreaker()


# =============================================================================
# Validation Result
# =============================================================================


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


# =============================================================================
# Tier Config Validator (Safe Boundary)
# =============================================================================


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


# =============================================================================
# Tier Registry (Service Layer)
# =============================================================================


class TierRegistry:
    """
    Tier Registry - manages tier definitions, mappings, and overrides.
    
    Thread-safe singleton for managing API tiering configuration.
    """
    
    _instance: Optional["TierRegistry"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "TierRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance
    
    def _init(self):
        """Initialize the registry with default values."""
        self._tiers: Dict[str, TierDefinition] = {}
        self._mappings: List[TierMapping] = []
        self._overrides: List[TierOverride] = []
        self._validator = TierConfigValidator()
        self._data_lock = threading.RLock()
        
        # Load defaults
        self._load_defaults()
    
    def _load_defaults(self):
        """Load default tier configuration (clears existing first)."""
        # Clear existing data first
        self._tiers.clear()
        
        # Load defaults
        for tier in DEFAULT_TIER_DEFINITIONS:
            self._tiers[tier.id] = tier
        self._mappings = list(DEFAULT_TIER_MAPPINGS)
        self._overrides = list(DEFAULT_TIER_OVERRIDES)
        
        # Sort mappings by priority (descending)
        self._mappings.sort(key=lambda m: m.priority, reverse=True)
    
    # -------------------------------------------------------------------------
    # Tier Definition Methods
    # -------------------------------------------------------------------------
    
    def get_tier(self, tier_id: str) -> Optional[TierDefinition]:
        """Get a tier definition by ID."""
        with self._data_lock:
            return self._tiers.get(tier_id)
    
    def get_all_tiers(self) -> List[TierDefinition]:
        """Get all tier definitions."""
        with self._data_lock:
            return list(self._tiers.values())
    
    def set_tiers(self, tiers: List[TierDefinition]) -> ValidationResult:
        """
        Replace all tier definitions.
        
        Args:
            tiers: New tier definitions
            
        Returns:
            ValidationResult
        """
        result = self._validator.validate_tiers(tiers)
        if not result.is_valid:
            return result
        
        with self._data_lock:
            self._tiers = {t.id: t for t in tiers}
            self._log_change("tiers", [t.to_dict() for t in tiers])
        
        return result
    
    # -------------------------------------------------------------------------
    # Tier Mapping Methods
    # -------------------------------------------------------------------------
    
    def get_all_mappings(self) -> List[TierMapping]:
        """Get all tier mappings."""
        with self._data_lock:
            return list(self._mappings)
    
    def set_mappings(self, mappings: List[TierMapping]) -> ValidationResult:
        """
        Replace all tier mappings.
        
        Args:
            mappings: New tier mappings
            
        Returns:
            ValidationResult
        """
        with self._data_lock:
            tier_ids = list(self._tiers.keys())
        
        result = self._validator.validate_mappings(mappings, tier_ids)
        if not result.is_valid:
            return result
        
        with self._data_lock:
            self._mappings = sorted(mappings, key=lambda m: m.priority, reverse=True)
            self._log_change("mappings", [m.to_dict() for m in mappings])
        
        return result
    
    def get_tier_for_path(self, path: str) -> Optional[TierDefinition]:
        """
        Get the tier for an API path.
        
        Args:
            path: API path (e.g., "/api/self-healing/control/")
            
        Returns:
            TierDefinition or None if no mapping matches
        """
        with self._data_lock:
            # Mappings are sorted by priority (descending)
            for mapping in self._mappings:
                if mapping.matches(path):
                    return self._tiers.get(mapping.tier_id)
        return None
    
    # -------------------------------------------------------------------------
    # Tier Override Methods
    # -------------------------------------------------------------------------
    
    def get_all_overrides(self) -> List[TierOverride]:
        """Get all tier overrides."""
        with self._data_lock:
            # Filter out expired overrides
            return [o for o in self._overrides if not o.is_expired()]
    
    def set_overrides(self, overrides: List[TierOverride]) -> ValidationResult:
        """
        Replace all tier overrides.
        
        Args:
            overrides: New tier overrides
            
        Returns:
            ValidationResult
        """
        with self._data_lock:
            tier_ids = list(self._tiers.keys())
        
        result = self._validator.validate_overrides(overrides, tier_ids)
        if not result.is_valid:
            return result
        
        with self._data_lock:
            self._overrides = list(overrides)
            self._log_change("overrides", [o.to_dict() for o in overrides])
        
        return result
    
    def get_override_tier(
        self,
        client_ip: Optional[str] = None,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> Optional[TierDefinition]:
        """
        Get tier override for a client.
        
        Args:
            client_ip: Client IP address
            user_id: User ID
            api_key: API key
            
        Returns:
            TierDefinition if override exists, None otherwise
        """
        with self._data_lock:
            for override in self._overrides:
                if override.is_expired():
                    continue
                
                if (
                    client_ip and 
                    override.matches(client_ip, OverrideIdentifierType.IP)
                ):
                    return self._tiers.get(override.tier_id)
                
                if (
                    user_id and 
                    override.matches(user_id, OverrideIdentifierType.USER_ID)
                ):
                    return self._tiers.get(override.tier_id)
                
                if (
                    api_key and 
                    override.matches(api_key, OverrideIdentifierType.API_KEY)
                ):
                    return self._tiers.get(override.tier_id)
        
        return None
    
    # -------------------------------------------------------------------------
    # Combined Resolution
    # -------------------------------------------------------------------------
    
    def resolve_tier(
        self,
        path: str,
        client_ip: Optional[str] = None,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> Optional[TierDefinition]:
        """
        Resolve the effective tier for a request.
        
        Override tier takes precedence over path-based tier.
        
        Args:
            path: API path
            client_ip: Client IP address
            user_id: User ID
            api_key: API key
            
        Returns:
            TierDefinition or None
        """
        # Check overrides first
        override_tier = self.get_override_tier(
            client_ip=client_ip,
            user_id=user_id,
            api_key=api_key,
        )
        if override_tier:
            return override_tier
        
        # Fall back to path-based tier
        return self.get_tier_for_path(path)
    
    def _is_static_critical(self, path: str) -> bool:
        """
        Check if path is in the static critical list (L1 Defense).
        
        These paths are ALWAYS treated as critical, regardless of
        dynamic mappings or tiering system state.
        """
        if path in STATIC_CRITICAL_PATHS:
            return True
        return path.startswith(STATIC_CRITICAL_PREFIXES)
    
    def _static_or_default_tier(
        self,
        path: str,
        reason: TierFallbackReason,
    ) -> TierResult:
        """
        Fallback tier resolution: Static Critical → Default (Fail-Closed).
        
        Args:
            path: API path
            reason: Reason for fallback
            
        Returns:
            TierResult with appropriate tier
        """
        # L1: Static Critical Path check
        if self._is_static_critical(path):
            return TierResult(
                tier_id="critical",
                multiplier=0.5,  # Critical tier default multiplier
                is_fallback=True,
                fallback_reason=TierFallbackReason.STATIC_PATH_MATCH,
                latency_ms=0.0,
            )
        
        # L2: Default tier (Fail-Closed: non_essential)
        return TierResult(
            tier_id="non_essential",
            multiplier=0.0,  # Non-essential: blocked in emergency
            is_fallback=True,
            fallback_reason=reason,
            latency_ms=0.0,
        )
    
    def resolve_tier_with_fallback(
        self,
        path: str,
        client_ip: Optional[str] = None,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> TierResult:
        """
        Resolve tier with Defense-in-Depth fallback chain.
        
        This is the RECOMMENDED method for production use.
        
        Fallback Chain:
        1. Circuit Breaker OPEN → Static Critical + Default
        2. Dynamic Mapping (try)
        3. On exception → Static Critical + Default + Shadow Audit
        4. On no match → Static Critical + Default
        
        Strategy: Fail-Closed-with-Critical-Exceptions
        - Unknown paths → non_essential (blocked in emergency)
        - Static critical paths → ALWAYS critical (protected)
        
        Reference: 
        - Netflix Hystrix "Fallback of last resort"
        - Google SRE "Fail-Safe Defaults with exceptions"
        
        Args:
            path: API path
            client_ip: Client IP address
            user_id: User ID
            api_key: API key
            
        Returns:
            TierResult (never None, always has a valid tier)
        """
        start_time = time.perf_counter()
        circuit_breaker = get_tiering_circuit_breaker()
        
        # Circuit Breaker OPEN → Bypass tiering, use static fallback
        if circuit_breaker.is_open:
            result = self._static_or_default_tier(
                path,
                TierFallbackReason.CIRCUIT_OPEN,
            )
            result.latency_ms = (time.perf_counter() - start_time) * 1000
            self._log_fallback_audit(path, result)
            return result
        
        try:
            # Try dynamic tier resolution
            tier = self.resolve_tier(
                path=path,
                client_ip=client_ip,
                user_id=user_id,
                api_key=api_key,
            )
            
            latency_ms = (time.perf_counter() - start_time) * 1000
            circuit_breaker.record_success(latency_ms)
            
            if tier is not None:
                return TierResult(
                    tier_id=tier.id,
                    multiplier=tier.multiplier,
                    is_fallback=False,
                    fallback_reason=TierFallbackReason.NONE,
                    latency_ms=latency_ms,
                )
            
            # No match → Static or Default (Fail-Closed)
            result = self._static_or_default_tier(
                path,
                TierFallbackReason.CONFIG_MISSING,
            )
            result.latency_ms = latency_ms
            self._log_fallback_audit(path, result)
            return result
            
        except Exception as e:
            circuit_breaker.record_failure(e)
            
            result = self._static_or_default_tier(
                path,
                TierFallbackReason.ENGINE_ERROR,
            )
            result.latency_ms = (time.perf_counter() - start_time) * 1000
            self._log_fallback_audit(path, result, error=e)
            return result
    
    def _log_fallback_audit(
        self,
        path: str,
        result: TierResult,
        error: Optional[Exception] = None,
    ):
        """Log fallback event to Shadow Audit."""
        try:
            from selfhealing.audit import log_config_change
            
            log_config_change(
                config_type="tiering_fallback",
                config_key=path,
                old_value=None,
                new_value={
                    "tier_id": result.tier_id,
                    "reason": result.fallback_reason.value,
                    "error": str(error) if error else None,
                    "severity": "warning" if not error else "error",
                    "tag": "TIERING_FALLBACK",
                    "latency_ms": result.latency_ms,
                },
                user="system",
            )
        except Exception as audit_error:
            logger.error(f"[TierRegistry] Shadow audit failed: {audit_error}")
    
    def resolve_tier_safe(
        self,
        path: str,
        client_ip: Optional[str] = None,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
        default_multiplier: float = 1.0,
    ) -> TierDefinition:
        """
        Resolve tier with Fail-Safe guarantee (LEGACY - use resolve_tier_with_fallback).
        
        If tiering system fails (exception, no match), returns a default
        tier that allows requests through. This prevents tiering system
        failures from blocking all traffic.
        
        WARNING: This uses Fail-Open strategy which may not be suitable
        for security-critical applications. Consider using 
        resolve_tier_with_fallback() instead.
        
        Fail-Safe Strategy:
        - On success: Return resolved tier
        - On no match: Return default tier (100% allowed)
        - On exception: Return default tier + log warning
        
        Args:
            path: API path
            client_ip: Client IP address
            user_id: User ID
            api_key: API key
            default_multiplier: Multiplier for default tier (default: 1.0 = allow all)
            
        Returns:
            TierDefinition (never None)
        """
        try:
            tier = self.resolve_tier(
                path=path,
                client_ip=client_ip,
                user_id=user_id,
                api_key=api_key,
            )
            
            if tier is not None:
                return tier
            
            # No tier matched - return default (allow all)
            return TierDefinition(
                id="_default",
                name="Default (No Match)",
                multiplier=default_multiplier,
                priority=0,
                description="티어링 매핑 없음 - 기본 허용",
            )
            
        except Exception as e:
            # Fail-Safe: Log and return default tier
            logger.warning(
                f"[TierRegistry] Fail-safe activated: {e}. "
                f"Returning default tier for path={path}"
            )
            return TierDefinition(
                id="_failsafe",
                name="Fail-Safe",
                multiplier=default_multiplier,
                priority=0,
                description="티어링 시스템 장애 - Fail-Safe 모드",
            )
    
    # -------------------------------------------------------------------------
    # Dry Run / Simulation
    # -------------------------------------------------------------------------
    
    def simulate(
        self,
        tiers: List[TierDefinition],
        mappings: List[TierMapping],
        test_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Simulate tier configuration changes.
        
        Does NOT apply changes, just shows what would happen.
        
        Args:
            tiers: Proposed tier definitions
            mappings: Proposed tier mappings
            test_paths: Optional list of paths to test
            
        Returns:
            Simulation result with affected APIs
        """
        # Validate first
        result = self._validator.validate_tiers(tiers)
        if not result.is_valid:
            return {
                "status": "error",
                "validation": result.to_dict(),
            }
        
        tier_ids = [t.id for t in tiers]
        result = self._validator.validate_mappings(mappings, tier_ids)
        if not result.is_valid:
            return {
                "status": "error",
                "validation": result.to_dict(),
            }
        
        # Sort mappings by priority
        sorted_mappings = sorted(mappings, key=lambda m: m.priority, reverse=True)
        tier_dict = {t.id: t for t in tiers}
        
        # If no test paths provided, use default test paths
        if not test_paths:
            test_paths = [
                "/api/self-healing/control/",
                "/api/self-healing/allow/test/",
                "/api/self-healing/block/test/",
                "/api/self-healing/config/circuit-breaker/",
                "/api/self-healing/dlq/replay/",
                "/api/self-healing/dashboard/summary/",
                "/api/self-healing/metrics/",
                "/api/self-healing/audit/",
            ]
        
        # Simulate each path
        affected_paths = []
        for path in test_paths:
            # Current tier
            current_tier = self.get_tier_for_path(path)
            current_tier_id = current_tier.id if current_tier else None
            
            # New tier
            new_tier_id = None
            for mapping in sorted_mappings:
                if mapping.matches(path):
                    new_tier_id = mapping.tier_id
                    break
            
            new_tier = tier_dict.get(new_tier_id) if new_tier_id else None
            
            affected_paths.append({
                "path": path,
                "current_tier": current_tier_id,
                "new_tier": new_tier_id,
                "changed": current_tier_id != new_tier_id,
                "new_multiplier": new_tier.multiplier if new_tier else None,
            })
        
        # Statistics
        changed_count = sum(1 for p in affected_paths if p["changed"])
        
        return {
            "status": "success",
            "affected_paths": affected_paths,
            "statistics": {
                "total_paths": len(affected_paths),
                "changed_count": changed_count,
                "unchanged_count": len(affected_paths) - changed_count,
            },
            "validation": {
                "is_valid": True,
                "errors": [],
                "warnings": result.warnings,
            },
        }
    
    # -------------------------------------------------------------------------
    # Persistence / Audit
    # -------------------------------------------------------------------------
    
    def _log_change(self, config_type: str, changes: Any):
        """Log configuration change to audit service."""
        try:
            from selfhealing.audit import log_config_change
            
            log_config_change(
                config_type=f"tiering_{config_type}",
                config_key="tier_config",
                old_value=None,
                new_value=changes,
                user="TierRegistry",
            )
        except Exception as e:
            logger.warning(f"[TierRegistry] Failed to log change: {e}")
    
    def export_config(self) -> Dict[str, Any]:
        """Export current configuration."""
        with self._data_lock:
            return {
                "tiers": [t.to_dict() for t in self._tiers.values()],
                "mappings": [m.to_dict() for m in self._mappings],
                "overrides": [o.to_dict() for o in self._overrides if not o.is_expired()],
            }
    
    def import_config(self, config: Dict[str, Any]) -> ValidationResult:
        """
        Import configuration.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            ValidationResult
        """
        tiers = [TierDefinition.from_dict(t) for t in config.get("tiers", [])]
        mappings = [TierMapping.from_dict(m) for m in config.get("mappings", [])]
        overrides = [TierOverride.from_dict(o) for o in config.get("overrides", [])]
        
        result = self._validator.validate_all(tiers, mappings, overrides)
        if not result.is_valid:
            return result
        
        with self._data_lock:
            self._tiers = {t.id: t for t in tiers}
            self._mappings = sorted(mappings, key=lambda m: m.priority, reverse=True)
            self._overrides = overrides
            self._log_change("full_config", config)
        
        return result
    
    def reset_to_defaults(self):
        """Reset to default configuration."""
        with self._data_lock:
            self._load_defaults()
            self._log_change("reset", {"action": "reset_to_defaults"})


# =============================================================================
# Singleton Accessor
# =============================================================================


def get_tier_registry() -> TierRegistry:
    """Get the singleton TierRegistry instance."""
    return TierRegistry()


# =============================================================================
# TieringMiddleware - Traffic Control based on Emergency Level
# =============================================================================


class TieringMiddleware:
    """
    Django Middleware for Emergency Mode Traffic Control.
    
    비상 모드(Emergency Mode)에서 API Tier에 따라 트래픽을 제어합니다.
    
    동작 방식:
    1. EmergencyManager에서 현재 비상 모드 레벨 확인
    2. 요청 경로의 Tier 확인 (TierRegistry 사용)
    3. Tier의 multiplier에 따라 확률적으로 요청 허용/차단
    4. 차단 시 503 Service Unavailable 응답
    
    Emergency Level별 동작:
    - NORMAL (0): 모든 요청 허용
    - LEVEL_1 (1): non_essential 차단
    - LEVEL_2 (2): standard 90% 차단, non_essential 100% 차단
    - LEVEL_3 (3): critical 50% 차단, standard/non_essential 100% 차단
    
    Configuration:
        # settings.py
        MIDDLEWARE = [
            ...
            'selfhealing.api.django.tiering.TieringMiddleware',
            ...
        ]
        
        # Optional: Disable middleware
        SELFHEALING_TIERING_MIDDLEWARE_ENABLED = True
    
    Reference:
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1A.md (Section 3)
    - Netflix Hystrix Load Shedding
    - Google SRE "Handling Overload"
    """
    
    def __init__(self, get_response):
        """
        Initialize middleware.
        
        Args:
            get_response: Django's get_response callable
        """
        self.get_response = get_response
        self._registry = get_tier_registry()
        self._random = __import__('random').Random()
        
        # Check if middleware is enabled
        self._enabled = self._check_enabled()
        
        if self._enabled:
            logger.info("[TieringMiddleware] Initialized and enabled")
        else:
            logger.info("[TieringMiddleware] Initialized but DISABLED")
    
    def _check_enabled(self) -> bool:
        """Check if middleware is enabled via settings."""
        try:
            from django.conf import settings
            return getattr(settings, 'SELFHEALING_TIERING_MIDDLEWARE_ENABLED', True)
        except Exception:
            return True  # Default: enabled
    
    def __call__(self, request):
        """
        Process the request.
        
        Args:
            request: Django HttpRequest
            
        Returns:
            HttpResponse
        """
        # Skip if middleware is disabled
        if not self._enabled:
            return self.get_response(request)
        
        # Check emergency mode
        try:
            from selfhealing.services.emergency_mode import (
                get_emergency_manager,
                EmergencyLevel,
                EMERGENCY_LEVEL_RULES,
            )
            
            manager = get_emergency_manager()
            
            # If not in emergency mode, allow all requests
            if not manager.is_active():
                return self.get_response(request)
            
            current_level = manager.get_current_level()
            
            # NORMAL level = allow all
            if current_level == EmergencyLevel.NORMAL:
                return self.get_response(request)
            
            # Get tier for this request
            path = request.path
            client_ip = self._get_client_ip(request)
            user_id = self._get_user_id(request)
            
            tier_result = self._registry.resolve_tier_with_fallback(
                path=path,
                client_ip=client_ip,
                user_id=str(user_id) if user_id else None,
            )
            
            # Get the traffic multiplier for this tier at current emergency level
            level_rules = EMERGENCY_LEVEL_RULES.get(current_level, {})
            multiplier = level_rules.get(tier_result.tier_id, 0.0)
            
            # Apply probabilistic load shedding
            if not self._should_allow_request(multiplier):
                return self._create_load_shedding_response(
                    request=request,
                    tier_id=tier_result.tier_id,
                    multiplier=multiplier,
                    emergency_level=current_level,
                )
            
            # Allow request
            return self.get_response(request)
            
        except Exception as e:
            # Fail-open: On middleware error, allow request through
            logger.error(f"[TieringMiddleware] Error: {e}, allowing request")
            return self.get_response(request)
    
    def _get_client_ip(self, request) -> Optional[str]:
        """Extract client IP from request."""
        # Check X-Forwarded-For header (for proxied requests)
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        
        # Fall back to REMOTE_ADDR
        return request.META.get('REMOTE_ADDR')
    
    def _get_user_id(self, request) -> Optional[int]:
        """Extract user ID from request."""
        if hasattr(request, 'user') and request.user.is_authenticated:
            return request.user.id
        return None
    
    def _should_allow_request(self, multiplier: float) -> bool:
        """
        Determine if request should be allowed based on multiplier.
        
        Args:
            multiplier: Traffic multiplier (0.0 = block all, 1.0 = allow all)
            
        Returns:
            True if request should be allowed
        """
        if multiplier >= 1.0:
            return True
        if multiplier <= 0.0:
            return False
        
        # Probabilistic: allow based on multiplier percentage
        return self._random.random() < multiplier
    
    def _create_load_shedding_response(
        self,
        request,
        tier_id: str,
        multiplier: float,
        emergency_level,
    ):
        """
        Create a 503 Load Shedding response.
        
        Args:
            request: Django HttpRequest
            tier_id: Tier ID of the request
            multiplier: Traffic multiplier
            emergency_level: Current emergency level
            
        Returns:
            JsonResponse with 503 status
        """
        from django.http import JsonResponse
        
        # Log the load shedding event
        logger.warning(
            f"[TieringMiddleware] Load shedding: "
            f"path={request.path}, tier={tier_id}, "
            f"multiplier={multiplier}, level={emergency_level.name}"
        )
        
        # Record metrics
        self._record_load_shedding_metrics(tier_id, emergency_level)
        
        # Return 503 with Retry-After header
        response = JsonResponse(
            {
                "error": "Service Temporarily Unavailable",
                "code": "LOAD_SHEDDING",
                "message": (
                    f"시스템 부하 관리를 위해 요청이 일시적으로 제한되었습니다. "
                    f"잠시 후 다시 시도해주세요."
                ),
                "tier": tier_id,
                "emergency_level": emergency_level.name,
                "retry_after": 30,  # Suggest retry after 30 seconds
            },
            status=503,
        )
        response['Retry-After'] = '30'
        
        return response
    
    def _record_load_shedding_metrics(self, tier_id: str, emergency_level):
        """Record load shedding metrics to Prometheus."""
        try:
            from prometheus_client import Counter
            
            counter = Counter(
                'selfhealing_tiering_load_shedding_total',
                'Total load shedding events by tier and level',
                ['tier_id', 'emergency_level'],
                registry=None,  # Use default registry
            )
            counter.labels(
                tier_id=tier_id,
                emergency_level=emergency_level.name,
            ).inc()
        except Exception:
            pass  # Best-effort metrics

