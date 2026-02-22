"""
Corruption Shield - Unified Data Integrity Protection.

Combines L1, L2, L3 validators into a single defense system.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import structlog

from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
from selfhealing.services.corruption_shield.validators import (
    L1SchemaValidator,
    L2BusinessRulesValidator,
    L3AnomalyDetector,
    Violation,
)

logger = structlog.get_logger()

# ViolationSeverity: 단일 소스는 services/compliance/models.py (Item 34 중복 제거)
from selfhealing.services.compliance.models import ViolationSeverity  # noqa: E402, F401


@dataclass
class ValidationResult:
    """Result of corruption shield validation."""

    is_valid: bool
    violations: list[Violation] = field(default_factory=list)
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

    def __init__(self, config: CorruptionShieldConfig | None = None):
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
        context: dict | None = None,
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

        # 각 레이어별 검증 수행
        all_violations, layer_results = self._validate_all_layers(data, context)

        # 결과 판정
        is_valid = len(all_violations) == 0
        blocked = self._should_block(all_violations, block_on_violation, is_valid)

        # 통계 업데이트
        self._update_stats(is_valid, blocked)

        elapsed_ms = (time.time() - start_time) * 1000

        result = ValidationResult(
            is_valid=is_valid,
            violations=all_violations,
            blocked=blocked,
            l1_passed=layer_results["l1"],
            l2_passed=layer_results["l2"],
            l3_passed=layer_results["l3"],
            validation_time_ms=elapsed_ms,
        )

        # 로깅 및 Audit
        if self.config.log_violations and not is_valid:
            self._log_violations(data, result)

        if not is_valid:
            self._record_audit_event(result, data, request)

        return result

    def _validate_all_layers(
        self,
        data: dict,
        context: dict | None,
    ) -> tuple[list[Violation], dict[str, bool]]:
        """모든 레이어에서 검증 수행."""
        all_violations: list[Violation] = []
        layer_results = {"l1": True, "l2": True, "l3": True}

        # L1: Schema validation
        if self.config.l1_enabled:
            l1_violations = self._l1.validate(data, context)
            if l1_violations:
                all_violations.extend(l1_violations)
                layer_results["l1"] = False
                self._increment_violation_count("l1", len(l1_violations))

        # L2: Business rules
        if self.config.l2_enabled:
            l2_violations = self._l2.validate(data, context)
            if l2_violations:
                all_violations.extend(l2_violations)
                layer_results["l2"] = False
                self._increment_violation_count("l2", len(l2_violations))

        # L3: Anomaly detection
        if self.config.l3_enabled:
            l3_violations = self._l3.validate(data, context)
            if l3_violations:
                all_violations.extend(l3_violations)
                layer_results["l3"] = False
                self._increment_violation_count("l3", len(l3_violations))

        return all_violations, layer_results

    def _increment_violation_count(self, layer: str, count: int) -> None:
        """레이어별 위반 카운트 증가."""
        with self._stats_lock:
            self._stats[f"{layer}_violations"] += count

    def _should_block(
        self,
        violations: list[Violation],
        block_on_violation: bool,
        is_valid: bool,
    ) -> bool:
        """차단 여부 판단."""
        if not block_on_violation or is_valid:
            return False
        critical_violations = [v for v in violations if v.severity in ("critical", "high")]
        return len(critical_violations) > 0

    def _update_stats(self, is_valid: bool, blocked: bool) -> None:
        """통계 업데이트."""
        with self._stats_lock:
            self._stats["total_validations"] += 1
            if is_valid:
                self._stats["passed"] += 1
            if blocked:
                self._stats["blocked"] += 1

    def _record_audit_event(
        self,
        result: ValidationResult,
        data: dict,
        request=None,
    ) -> None:
        """
        Corruption 이벤트를 Audit 시스템에 기록.

        request가 있으면 RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 처리)
        request가 없으면 _write_to_wal()을 통해 직접 기록

        배칭: 단일 이벤트에 모든 violations 포함 (개별 이벤트 대신)
        10개 필드 위반 시 10개 로그 → 1개 로그로 최적화
        """
        # 이벤트 유형 결정
        if result.blocked:
            event_type_str = "CORRUPTION_BLOCKED"
        else:
            event_type_str = "CORRUPTION_DETECTED"

        # violations 리스트 구성 (배칭)
        violations_list = [
            {
                "layer": v.layer,
                "code": v.code,
                "message": v.message,
                "field": v.field,
                "severity": v.severity,
            }
            for v in result.violations
        ]

        # 배칭된 상세 정보
        batched_details = {
            "violation_count": len(result.violations),
            "blocked": result.blocked,
            "violations": violations_list,
            "layers": {
                "l1_passed": result.l1_passed,
                "l2_passed": result.l2_passed,
                "l3_passed": result.l3_passed,
            },
        }

        # 버퍼 우선 패턴
        if request is not None:
            try:
                from selfhealing.audit.event_buffer import (
                    AuditEventType,
                    RequestAuditBuffer,
                )

                buffer = RequestAuditBuffer.get_or_create(request)

                # 배칭: 단일 이벤트에 모든 violations 포함
                event_type = AuditEventType.CORRUPTION_BLOCKED if result.blocked else AuditEventType.CORRUPTION_DETECTED

                buffer.add(
                    event_type=event_type,
                    source="CorruptionShield",
                    details=batched_details,
                    success=False,
                    error_message=f"{len(result.violations)} violation(s) detected",
                )
                return
            except ImportError:
                pass  # event_buffer 미사용 환경

        # Fallback: _write_to_wal()로 직접 기록 (ActorContext/TraceContext 자동 결합)
        try:
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type=event_type_str,
                source="CorruptionShield",
                details=batched_details,
                success=False,
                error_message=f"{len(result.violations)} violation(s) detected",
            )
        except ImportError:
            # _write_to_wal 미사용 환경: 로거로 폴백
            logger.warning(
                "violations_detected",
                count=len(result.violations),
                result=result.blocked,
            )

    def _log_violations(self, data: dict, result: ValidationResult) -> None:
        """Log violations for debugging and audit."""
        for violation in result.violations:
            log_level = logging.WARNING
            if violation.severity == "critical":
                log_level = logging.ERROR

            logger.log(
                log_level,
                f"[CorruptionShield] {violation.layer} violation: " f"{violation.code} - {violation.message}",  # noqa: G004
            )

        # Log to security incident if configured
        if self.config.log_to_security_incident:
            self._maybe_create_security_incident(data, result)

    def _maybe_create_security_incident(
        self,
        data: dict,
        result: ValidationResult,
    ) -> None:
        """
        Create security incident for critical violations.

        표준 ViolationType 매핑 적용
        """
        critical_violations = [v for v in result.violations if v.severity == "critical"]

        if not critical_violations:
            return

        # Try to create security incident
        try:
            from selfhealing.services.security import (
                SecurityViolationService,
            )

            service = SecurityViolationService()

            for violation in critical_violations:
                # ✅ 순위 3: 표준 ViolationType 매핑
                violation_type = self._map_to_violation_type(violation)

                service.record_violation(
                    violation_type=violation_type,
                    details={
                        "layer": violation.layer,
                        "message": violation.message,
                        "field": violation.field,
                        "data_sample": str(data)[:200],
                    },
                )
        except Exception as e:
            logger.warning(
                "corruption_shield.failed_create_security_incident",
                error=e,
            )

    def _map_to_violation_type(self, violation) -> str:
        """
        Corruption 위반을 표준 ViolationType으로 매핑.

        레이어별 매핑:
        - L1 → SCHEMA_VIOLATION
        - L2 → BUSINESS_RULE_VIOLATION
        - L3 → ANOMALY_STATISTICAL 또는 ANOMALY_BEHAVIORAL

        Args:
            violation: CorruptionShield의 Violation 객체

        Returns:
            ViolationType enum 값 (문자열)
        """
        try:
            from selfhealing.services.security import ViolationType
        except ImportError:
            # Fallback to string
            return f"corruption_{violation.code}"

        # Layer 기반 매핑
        layer_mapping = {
            "L1": ViolationType.SCHEMA_VIOLATION,
            "L2": ViolationType.BUSINESS_RULE_VIOLATION,
            "L3": ViolationType.ANOMALY_STATISTICAL,
        }

        # 특수 케이스: 행위 이상 (behavioral anomaly)
        if hasattr(violation, "code") and "anomaly" in violation.code.lower():
            if "behavioral" in violation.code.lower():
                return ViolationType.ANOMALY_BEHAVIORAL.value
            return ViolationType.ANOMALY_STATISTICAL.value

        # Layer 기반 기본 매핑
        layer = getattr(violation, "layer", "")
        if layer in layer_mapping:
            return layer_mapping[layer].value

        # 알 수 없는 경우 SUSPICIOUS_ACTIVITY로 폴백
        return ViolationType.SUSPICIOUS_ACTIVITY.value

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

_global_shield: CorruptionShield | None = None
_shield_lock = threading.Lock()


def get_corruption_shield(
    config: CorruptionShieldConfig | None = None,
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
