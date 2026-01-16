"""
Tier Registry - Service Layer.

Thread-safe singleton for managing API tiering configuration.
Handles tier definitions, mappings, and overrides with fallback chain.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .enums import TierFallbackReason, OverrideIdentifierType
from .models import TierDefinition, TierMapping, TierOverride, TierResult
from .defaults import (
    STATIC_CRITICAL_PATHS,
    STATIC_CRITICAL_PREFIXES,
    DEFAULT_TIER_DEFINITIONS,
    DEFAULT_TIER_MAPPINGS,
    DEFAULT_TIER_OVERRIDES,
)
from .circuit_breaker import get_tiering_circuit_breaker
from .validator import TierConfigValidator, ValidationResult

logger = logging.getLogger(__name__)


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
        
        # Before Mutation Snapshot: 롤백용 이전 상태 저장 (최대 10개)
        self._previous_configs: List[Dict[str, Any]] = []
        
        # Load defaults
        self._load_defaults()
    
    def _load_defaults(self):
        """Load default tier configuration (clears existing first)."""
        self._tiers.clear()
        
        for tier in DEFAULT_TIER_DEFINITIONS:
            self._tiers[tier.id] = tier
        self._mappings = list(DEFAULT_TIER_MAPPINGS)
        self._overrides = list(DEFAULT_TIER_OVERRIDES)
        
        # Sort mappings by priority (descending)
        self._mappings.sort(key=lambda m: m.priority, reverse=True)
    
    # -------------------------------------------------------------------------
    # Before Mutation Snapshot (롤백 지원)
    # -------------------------------------------------------------------------
    
    def _save_previous_config(self, action: str):
        """
        설정 변경 전 스냅샷 저장.
        
        롤백 가능하도록 이전 설정을 저장합니다.
        최대 10개까지 유지합니다.
        """
        from datetime import datetime, timezone
        
        snapshot = {
            "config": self.export_config(),
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._previous_configs.append(snapshot)
        
        # 최대 10개 유지
        if len(self._previous_configs) > 10:
            self._previous_configs = self._previous_configs[-10:]
        
        logger.debug(f"[TierRegistry] Saved pre-mutation snapshot: action={action}")
    
    def get_previous_configs(self) -> List[Dict[str, Any]]:
        """
        이전 설정 스냅샷 목록 조회.
        
        Returns:
            스냅샷 목록 (최신순)
        """
        with self._data_lock:
            return list(reversed(self._previous_configs))
    
    def rollback_to_previous(self, index: int = 0) -> Optional[ValidationResult]:
        """
        이전 설정으로 롤백.
        
        Args:
            index: 롤백할 스냅샷 인덱스 (0=가장 최근, 1=그 이전...)
            
        Returns:
            ValidationResult (성공 시), 실패 시 None
        """
        with self._data_lock:
            if not self._previous_configs:
                logger.warning("[TierRegistry] No previous config to rollback")
                return None
            
            # 역순 인덱스 (0=가장 최근)
            actual_index = len(self._previous_configs) - 1 - index
            if actual_index < 0:
                logger.warning(f"[TierRegistry] Invalid rollback index: {index}")
                return None
            
            snapshot = self._previous_configs[actual_index]
            old_config = snapshot["config"]
            
            # 현재 상태를 스냅샷에 저장 (롤백의 롤백 가능)
            self._save_previous_config("rollback")
            
            # 설정 복원 (import_config 사용)
            logger.warning(
                f"[TierRegistry] Rolling back to snapshot at {snapshot['timestamp']}, "
                f"original action={snapshot['action']}"
            )
            
            # 직접 복원 (import_config 호출 시 무한 루프 방지)
            tiers = [TierDefinition.from_dict(t) for t in old_config.get("tiers", [])]
            mappings = [TierMapping.from_dict(m) for m in old_config.get("mappings", [])]
            overrides = [TierOverride.from_dict(o) for o in old_config.get("overrides", [])]
            
            self._tiers = {t.id: t for t in tiers}
            self._mappings = sorted(mappings, key=lambda m: m.priority, reverse=True)
            self._overrides = overrides
            
            return ValidationResult(is_valid=True, errors=[], warnings=["Rolled back"])
    
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
            # Before Mutation Snapshot: 변경 전 상태 저장
            self._save_previous_config("set_tiers")
            
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
            # Before Mutation Snapshot: 변경 전 상태 저장
            self._save_previous_config("set_mappings")
            
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
            # Before Mutation Snapshot: 변경 전 상태 저장
            self._save_previous_config("set_overrides")
            
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
        override_tier = self.get_override_tier(
            client_ip=client_ip,
            user_id=user_id,
            api_key=api_key,
        )
        if override_tier:
            return override_tier
        
        return self.get_tier_for_path(path)
    
    def _is_static_critical(self, path: str) -> bool:
        """
        Check if path is in the static critical list (L1 Defense).
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
        """
        if self._is_static_critical(path):
            return TierResult(
                tier_id="critical",
                multiplier=0.5,
                is_fallback=True,
                fallback_reason=TierFallbackReason.STATIC_PATH_MATCH,
                latency_ms=0.0,
            )
        
        return TierResult(
            tier_id="non_essential",
            multiplier=0.0,
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
        """
        start_time = time.perf_counter()
        circuit_breaker = get_tiering_circuit_breaker()
        
        if circuit_breaker.is_open:
            result = self._static_or_default_tier(
                path,
                TierFallbackReason.CIRCUIT_OPEN,
            )
            result.latency_ms = (time.perf_counter() - start_time) * 1000
            self._log_fallback_audit(path, result)
            return result
        
        try:
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
        Resolve tier with Fail-Safe guarantee (LEGACY).
        
        WARNING: This uses Fail-Open strategy. Consider using 
        resolve_tier_with_fallback() instead.
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
            
            return TierDefinition(
                id="_default",
                name="Default (No Match)",
                multiplier=default_multiplier,
                priority=0,
                description="티어링 매핑 없음 - 기본 허용",
            )
            
        except Exception as e:
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
        """
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
        
        sorted_mappings = sorted(mappings, key=lambda m: m.priority, reverse=True)
        tier_dict = {t.id: t for t in tiers}
        
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
        
        affected_paths = []
        for path in test_paths:
            current_tier = self.get_tier_for_path(path)
            current_tier_id = current_tier.id if current_tier else None
            
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
            # Before Mutation Snapshot: 변경 전 상태 저장
            self._save_previous_config("import_config")
            
            self._tiers = {t.id: t for t in tiers}
            self._mappings = sorted(mappings, key=lambda m: m.priority, reverse=True)
            self._overrides = overrides
            self._log_change("full_config", config)
        
        return result
    
    def reset_to_defaults(self):
        """Reset to default configuration."""
        with self._data_lock:
            # Before Mutation Snapshot: 변경 전 상태 저장
            self._save_previous_config("reset_to_defaults")
            
            self._load_defaults()
            self._log_change("reset", {"action": "reset_to_defaults"})


def get_tier_registry() -> TierRegistry:
    """Get the singleton TierRegistry instance."""
    return TierRegistry()
