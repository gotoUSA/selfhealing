"""
Corruption Shield - Unified Data Integrity Protection.

Combines L1, L2, L3 validators into a single defense system.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
from selfhealing.services.corruption_shield.validators import (
    L1SchemaValidator,
    L2BusinessRulesValidator,
    L3AnomalyDetector,
    Violation,
)

logger = logging.getLogger(__name__)


class ViolationSeverity(Enum):
    """Violation severity levels."""
    CRITICAL = "critical"  # Immediate block, security incident
    HIGH = "high"  # Block, log to DLQ
    MEDIUM = "medium"  # Warn, may block
    LOW = "low"  # Log only


@dataclass
class ValidationResult:
    """Result of corruption shield validation."""
    
    is_valid: bool
    violations: List[Violation] = field(default_factory=list)
    blocked: bool = False
    
    # Breakdown by layer
    l1_passed: bool = True
    l2_passed: bool = True
    l3_passed: bool = True
    
    # Metadata
    validation_time_ms: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "blocked": self.blocked,
            "violations": [v.to_dict() for v in self.violations],
            "layers": {
                "l1_passed": self.l1_passed,
                "l2_passed": self.l2_passed,
                "l3_passed": self.l3_passed,
            },
            "validation_time_ms": self.validation_time_ms,
        }


class CorruptionShield:
    """
    Unified Corruption Shield.
    
    Combines L1 (Schema), L2 (Business Rules), L3 (Anomaly Detection)
    into a single, easy-to-use validation system.
    
    Usage:
        shield = CorruptionShield()
        result = shield.validate(data, context)
        
        if not result.is_valid:
            # Handle violations
            for v in result.violations:
                print(f"{v.layer}: {v.message}")
    """
    
    def __init__(self, config: Optional[CorruptionShieldConfig] = None):
        self.config = config or CorruptionShieldConfig()
        
        # Initialize validators
        self._l1 = L1SchemaValidator(self.config)
        self._l2 = L2BusinessRulesValidator(self.config)
        self._l3 = L3AnomalyDetector(self.config)
        
        # Statistics
        self._stats = {
            "total_validations": 0,
            "passed": 0,
            "blocked": 0,
            "l1_violations": 0,
            "l2_violations": 0,
            "l3_violations": 0,
        }
        self._stats_lock = threading.Lock()
    
    def validate(
        self,
        data: dict,
        context: Optional[dict] = None,
        block_on_violation: bool = True,
        request=None,  # Django request 객체 (Audit 통합용)
    ) -> ValidationResult:
        """
        Validate data through all enabled layers.
        
        Args:
            data: Data to validate
            context: Additional context (expected values, user info, etc.)
            block_on_violation: Whether to block on any violation
            request: Django request 객체 (RequestAuditBuffer에 적재)
            
        Returns:
            ValidationResult with all violations and pass/fail status
        """
        import time
        start_time = time.time()
        
        all_violations: List[Violation] = []
        l1_passed = True
        l2_passed = True
        l3_passed = True
        
        # L1: Schema validation
        if self.config.l1_enabled:
            l1_violations = self._l1.validate(data, context)
            if l1_violations:
                all_violations.extend(l1_violations)
                l1_passed = False
                with self._stats_lock:
                    self._stats["l1_violations"] += len(l1_violations)
        
        # L2: Business rules (only if L1 passed or continue on error)
        if self.config.l2_enabled:
            l2_violations = self._l2.validate(data, context)
            if l2_violations:
                all_violations.extend(l2_violations)
                l2_passed = False
                with self._stats_lock:
                    self._stats["l2_violations"] += len(l2_violations)
        
        # L3: Anomaly detection (only if L1 and L2 passed or continue on error)
        if self.config.l3_enabled:
            l3_violations = self._l3.validate(data, context)
            if l3_violations:
                all_violations.extend(l3_violations)
                l3_passed = False
                with self._stats_lock:
                    self._stats["l3_violations"] += len(l3_violations)
        
        # Determine if valid
        is_valid = len(all_violations) == 0
        
        # Determine if should block
        blocked = False
        if block_on_violation and not is_valid:
            # Block on critical/high severity violations
            critical_violations = [
                v for v in all_violations
                if v.severity in ("critical", "high")
            ]
            blocked = len(critical_violations) > 0
        
        # Update stats
        with self._stats_lock:
            self._stats["total_validations"] += 1
            if is_valid:
                self._stats["passed"] += 1
            if blocked:
                self._stats["blocked"] += 1
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        result = ValidationResult(
            is_valid=is_valid,
            violations=all_violations,
            blocked=blocked,
            l1_passed=l1_passed,
            l2_passed=l2_passed,
            l3_passed=l3_passed,
            validation_time_ms=elapsed_ms,
        )
        
        # Log violations
        if self.config.log_violations and not is_valid:
            self._log_violations(data, result)
        
        # Audit 기록 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
        if not is_valid:
            self._record_audit_event(result, data, request)
        
        return result
    
    def _record_audit_event(
        self,
        result: ValidationResult,
        data: dict,
        request=None,
    ) -> None:
        """
        Corruption 이벤트를 Audit 시스템에 기록.
        
        request가 있으면 RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 처리)
        request가 없으면 직접 로깅
        
        Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md
        """
        # Phase 3 패턴: 버퍼 우선
        if request is not None:
            try:
                from selfhealing.audit.event_buffer import (
                    RequestAuditBuffer,
                    AuditEventType,
                )
                
                buffer = RequestAuditBuffer.get_or_create(request)
                
                for violation in result.violations:
                    # 이벤트 유형 결정
                    if result.blocked:
                        event_type = AuditEventType.CORRUPTION_BLOCKED
                    else:
                        event_type = AuditEventType.CORRUPTION_DETECTED
                    
                    buffer.add(
                        event_type=event_type,
                        source="CorruptionShield",
                        details={
                            "layer": violation.layer,
                            "code": violation.code,
                            "message": violation.message,
                            "field": violation.field,
                            "severity": violation.severity,
                            "blocked": result.blocked,
                        },
                        success=False,
                        error_message=violation.message,
                    )
                return
            except ImportError:
                pass  # event_buffer 미사용 환경
        
        # Fallback: 직접 로깅 (request 없는 경우)
        for violation in result.violations:
            logger.warning(
                f"[CorruptionShield/Audit] {violation.layer} violation: "
                f"{violation.code} - {violation.message}"
            )
    
    def _log_violations(self, data: dict, result: ValidationResult) -> None:
        """Log violations for debugging and audit."""
        for violation in result.violations:
            log_level = logging.WARNING
            if violation.severity == "critical":
                log_level = logging.ERROR
            
            logger.log(
                log_level,
                f"[CorruptionShield] {violation.layer} violation: "
                f"{violation.code} - {violation.message}"
            )
        
        # Log to security incident if configured
        if self.config.log_to_security_incident:
            self._maybe_create_security_incident(data, result)
    
    def _maybe_create_security_incident(
        self,
        data: dict,
        result: ValidationResult,
    ) -> None:
        """Create security incident for critical violations."""
        critical_violations = [
            v for v in result.violations
            if v.severity == "critical"
        ]
        
        if not critical_violations:
            return
        
        # Try to create security incident
        try:
            from selfhealing.services.security_violation_service import (
                SecurityViolationService,
            )
            
            service = SecurityViolationService()
            
            for violation in critical_violations:
                service.record_violation(
                    violation_type=f"corruption_{violation.code}",
                    details={
                        "layer": violation.layer,
                        "message": violation.message,
                        "field": violation.field,
                        "data_sample": str(data)[:200],
                    },
                )
        except Exception as e:
            logger.warning(f"[CorruptionShield] Failed to create security incident: {e}")
    
    def get_stats(self) -> dict:
        """Get shield statistics."""
        with self._stats_lock:
            stats = self._stats.copy()
        
        stats["l3_detector"] = self._l3.get_stats()
        return stats
    
    def reset(self) -> None:
        """Reset shield state (for testing)."""
        with self._stats_lock:
            self._stats = {
                "total_validations": 0,
                "passed": 0,
                "blocked": 0,
                "l1_violations": 0,
                "l2_violations": 0,
                "l3_violations": 0,
            }
        self._l3.reset()


# =============================================================================
# Singleton Instance for Global Use
# =============================================================================

_global_shield: Optional[CorruptionShield] = None
_shield_lock = threading.Lock()


def get_corruption_shield(
    config: Optional[CorruptionShieldConfig] = None,
) -> CorruptionShield:
    """
    Get global corruption shield instance.
    
    Thread-safe singleton pattern.
    """
    global _global_shield
    
    with _shield_lock:
        if _global_shield is None:
            _global_shield = CorruptionShield(config)
        return _global_shield


def reset_corruption_shield() -> None:
    """Reset global corruption shield (for testing)."""
    global _global_shield
    
    with _shield_lock:
        if _global_shield is not None:
            _global_shield.reset()
        _global_shield = None
