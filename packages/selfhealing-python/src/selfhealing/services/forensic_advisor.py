"""
Forensic Advisor - Decision Support System

Analyzes forensic data from failed operation entries and provides
data-driven recommendations for operators.

Core Principle: "System provides data, humans make decisions."
This module ONLY provides advisory hints - never auto-executes actions.

Reference: docs/self_healing/11_FORENSIC_ADVISOR.md
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from selfhealing.core.timezone import now

logger = logging.getLogger(__name__)


# =============================================================================
# Protocols (Framework-agnostic interfaces)
# =============================================================================


class FailedOperationProtocol(Protocol):
    """Protocol for FailedOperation-like objects."""

    id: Any
    error_code: str | None
    error_message: str | None
    retry_count: int
    metadata: dict[str, Any] | None
    next_action_hint: str
    recommended_action: str

    def save(self, update_fields: list[str] | None = None) -> None:
        """Save the operation."""
        ...


# =============================================================================
# Constants & Enums
# =============================================================================


class AdvisoryLevel(str, Enum):
    """Severity level for advisory hints."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RecommendedAction(str, Enum):
    """Recommended actions for operators."""

    REPLAY = "replay"
    MANUAL_CHECK = "manual_check"
    ESCALATE = "escalate"
    SECURITY_REVIEW = "security_review"
    WAIT_AND_RETRY = "wait_and_retry"
    ARCHIVE = "archive"


# =============================================================================
# Pattern Definitions
# =============================================================================


@dataclass
class FailurePattern:
    """Definition of a known failure pattern."""

    pattern_id: str
    name: str
    error_codes: list[str]
    error_message_patterns: list[str]
    recommended_action: RecommendedAction
    advisory_template: str
    level: AdvisoryLevel = AdvisoryLevel.INFO
    confidence_threshold: float = 0.7


# Known failure patterns for pattern matching
KNOWN_PATTERNS: list[FailurePattern] = [
    # Transient Network Issues
    FailurePattern(
        pattern_id="TRANSIENT_NETWORK",
        name="Transient Network Failure",
        error_codes=["ETIMEDOUT", "ECONNRESET", "ECONNREFUSED", "TIMEOUT", "PG_TIMEOUT"],
        error_message_patterns=[
            r"timeout",
            r"connection.*reset",
            r"connection.*refused",
            r"network.*unreachable",
            r"temporary.*failure",
        ],
        recommended_action=RecommendedAction.REPLAY,
        advisory_template=(
            "패턴 분석 결과: 일시적 네트워크 장애로 보입니다. "
            "최근 {retry_count}회 재시도 이력이 있으며, 평균 지연시간은 {avg_latency}ms입니다. "
            "Replay를 권장합니다."
        ),
        level=AdvisoryLevel.INFO,
    ),
    # Rate Limit / Throttling
    FailurePattern(
        pattern_id="RATE_LIMIT",
        name="Rate Limit Exceeded",
        error_codes=["429", "RATE_LIMITED", "TOO_MANY_REQUESTS", "QUOTA_EXCEEDED"],
        error_message_patterns=[
            r"rate.*limit",
            r"too.*many.*requests",
            r"quota.*exceeded",
            r"throttl",
        ],
        recommended_action=RecommendedAction.WAIT_AND_RETRY,
        advisory_template=(
            "패턴 분석 결과: 레이트 리밋 초과 상태입니다. "
            "외부 서비스의 요청 제한에 도달했습니다. "
            "{wait_time}분 대기 후 재시도를 권장합니다."
        ),
        level=AdvisoryLevel.WARNING,
    ),
    # Authentication / Authorization Issues
    FailurePattern(
        pattern_id="AUTH_FAILURE",
        name="Authentication/Authorization Failure",
        error_codes=["401", "403", "UNAUTHORIZED", "FORBIDDEN", "AUTH_FAILED"],
        error_message_patterns=[
            r"unauthorized",
            r"forbidden",
            r"authentication.*fail",
            r"invalid.*token",
            r"expired.*token",
            r"permission.*denied",
        ],
        recommended_action=RecommendedAction.SECURITY_REVIEW,
        advisory_template=(
            "패턴 분석 결과: 인증/권한 오류입니다. "
            "반복적인 {error_code} 에러가 {occurrence_count}회 발생했습니다. "
            "보안 점검 후 처리를 권장합니다."
        ),
        level=AdvisoryLevel.WARNING,
    ),
    # Data Validation Issues
    FailurePattern(
        pattern_id="VALIDATION_ERROR",
        name="Data Validation Failure",
        error_codes=["400", "VALIDATION_ERROR", "INVALID_PARAM", "AMOUNT_MISMATCH"],
        error_message_patterns=[
            r"invalid.*param",
            r"validation.*fail",
            r"amount.*mismatch",
            r"required.*field",
            r"invalid.*format",
        ],
        recommended_action=RecommendedAction.MANUAL_CHECK,
        advisory_template=(
            "패턴 분석 결과: 데이터 검증 실패입니다. "
            "요청 데이터에 문제가 있을 수 있습니다. "
            "원본 요청 데이터 확인 후 수정이 필요합니다."
        ),
        level=AdvisoryLevel.WARNING,
    ),
    # External Service Down
    FailurePattern(
        pattern_id="SERVICE_UNAVAILABLE",
        name="External Service Unavailable",
        error_codes=["503", "502", "504", "SERVICE_UNAVAILABLE", "BAD_GATEWAY"],
        error_message_patterns=[
            r"service.*unavailable",
            r"bad.*gateway",
            r"gateway.*timeout",
            r"upstream.*error",
            r"maintenance",
        ],
        recommended_action=RecommendedAction.WAIT_AND_RETRY,
        advisory_template=(
            "패턴 분석 결과: 외부 서비스 일시 장애입니다. "
            "현재 Circuit Breaker 상태를 확인하세요. "
            "서비스 복구 후 자동 Replay가 가능합니다."
        ),
        level=AdvisoryLevel.CRITICAL,
    ),
    # Repeated Failures (Escalation needed)
    FailurePattern(
        pattern_id="REPEATED_FAILURE",
        name="Repeated Failure - Escalation Required",
        error_codes=[],  # Any error code with high retry count
        error_message_patterns=[],  # Pattern based on retry count
        recommended_action=RecommendedAction.ESCALATE,
        advisory_template=(
            "패턴 분석 결과: 반복 실패 패턴이 감지되었습니다. "
            "{retry_count}회 재시도 후에도 실패가 지속됩니다. "
            "시니어 개발자 또는 운영팀 에스컬레이션을 권장합니다."
        ),
        level=AdvisoryLevel.CRITICAL,
    ),
    # Idempotency / Duplicate Detection
    FailurePattern(
        pattern_id="DUPLICATE_REQUEST",
        name="Duplicate Request Detected",
        error_codes=["DUPLICATE", "ALREADY_PROCESSED", "IDEMPOTENCY_CONFLICT"],
        error_message_patterns=[
            r"duplicate",
            r"already.*processed",
            r"idempoten",
        ],
        recommended_action=RecommendedAction.ARCHIVE,
        advisory_template=(
            "패턴 분석 결과: 중복 요청으로 감지되었습니다. "
            "동일한 작업이 이미 처리되었을 가능성이 높습니다. "
            "원본 처리 결과 확인 후 Archive 처리를 권장합니다."
        ),
        level=AdvisoryLevel.INFO,
    ),
]


# =============================================================================
# Analysis Result
# =============================================================================


@dataclass
class ForensicAdvisory:
    """Result of forensic analysis with advisory hints."""

    # Analysis metadata
    analyzed_at: str = field(default_factory=lambda: now().isoformat())
    analyzer_version: str = "1.0.0"

    # Pattern matching result
    matched_pattern_id: str = ""
    matched_pattern_name: str = ""
    confidence: float = 0.0

    # Advisory content
    level: str = AdvisoryLevel.INFO.value
    recommended_action: str = RecommendedAction.MANUAL_CHECK.value
    advisory_message: str = ""

    # Supporting evidence
    evidence: dict[str, Any] = field(default_factory=dict)

    # Decision traceability (for audit)
    decision_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "analyzed_at": self.analyzed_at,
            "analyzer_version": self.analyzer_version,
            "matched_pattern_id": self.matched_pattern_id,
            "matched_pattern_name": self.matched_pattern_name,
            "confidence": self.confidence,
            "level": self.level,
            "recommended_action": self.recommended_action,
            "advisory_message": self.advisory_message,
            "evidence": self.evidence,
            "decision_factors": self.decision_factors,
        }

    def to_hint_string(self) -> str:
        """Generate human-readable hint string for next_action_hint field."""
        action_labels = {
            "replay": "Replay 권장",
            "manual_check": "수동 확인 필요",
            "escalate": "에스컬레이션 필요",
            "security_review": "보안 검토 필요",
            "wait_and_retry": "대기 후 재시도",
            "archive": "Archive 처리 권장",
        }
        action_label = action_labels.get(self.recommended_action, "확인 필요")

        if self.advisory_message:
            return f"[{action_label}] {self.advisory_message}"
        return f"[{action_label}] 패턴 분석 완료. 상세 내용은 metadata.forensic_advisory 참조."


# =============================================================================
# Forensic Advisor Service
# =============================================================================


class ForensicAdvisorService:
    """
    Analyzes failed operation entries and provides decision support.

    This service is READ-ONLY and ADVISORY-ONLY.
    It NEVER executes actions automatically.

    Usage:
        advisor = ForensicAdvisorService()
        advisory = advisor.analyze(failed_operation)

        # Advisory is stored but action requires human approval
        print(advisory.advisory_message)
        print(advisory.recommended_action)
    """

    def __init__(self, patterns: list[FailurePattern] | None = None):
        """
        Initialize the advisor.

        Args:
            patterns: Custom patterns to use. Defaults to KNOWN_PATTERNS.
        """
        self.patterns = patterns or KNOWN_PATTERNS

    def analyze(self, operation: FailedOperationProtocol) -> ForensicAdvisory:
        """
        Analyze a failed operation and generate advisory hints.

        Args:
            operation: Failed operation instance to analyze

        Returns:
            ForensicAdvisory with recommendations
        """
        advisory = ForensicAdvisory()
        evidence: dict[str, Any] = {}
        decision_factors: list[str] = []

        # Extract analysis data
        error_code = operation.error_code or ""
        error_message = operation.error_message or ""
        retry_count = operation.retry_count
        metadata = operation.metadata or {}

        # Calculate metrics
        retry_history = metadata.get("retry_history", [])
        avg_latency = self._calculate_avg_latency(retry_history)
        occurrence_count = len(retry_history)

        evidence["error_code"] = error_code
        evidence["retry_count"] = retry_count
        evidence["avg_latency_ms"] = avg_latency
        evidence["occurrence_count"] = occurrence_count

        # Check for repeated failure pattern first (based on retry count)
        if retry_count >= 3:
            pattern = self._find_pattern_by_id("REPEATED_FAILURE")
            if pattern:
                advisory.matched_pattern_id = pattern.pattern_id
                advisory.matched_pattern_name = pattern.name
                advisory.confidence = 0.9
                advisory.level = pattern.level.value
                advisory.recommended_action = pattern.recommended_action.value
                advisory.advisory_message = pattern.advisory_template.format(
                    retry_count=retry_count,
                    error_code=error_code,
                )
                decision_factors.append(f"High retry count: {retry_count} >= 3")
                advisory.evidence = evidence
                advisory.decision_factors = decision_factors

                logger.info(
                    f"[ForensicAdvisor] Pattern matched: {pattern.pattern_id} "
                    f"for operation {operation.id} (confidence: {advisory.confidence})"
                )
                return advisory

        # Try to match other patterns
        best_match: FailurePattern | None = None
        best_confidence = 0.0

        for pattern in self.patterns:
            if pattern.pattern_id == "REPEATED_FAILURE":
                continue  # Already handled above

            confidence = self._calculate_pattern_confidence(pattern, error_code, error_message)

            if confidence > best_confidence:
                best_confidence = confidence
                best_match = pattern
                decision_factors = [
                    f"Error code match: {error_code}",
                    f"Message pattern match confidence: {confidence:.2f}",
                ]

        # Apply best match if above threshold
        if best_match and best_confidence >= best_match.confidence_threshold:
            advisory.matched_pattern_id = best_match.pattern_id
            advisory.matched_pattern_name = best_match.name
            advisory.confidence = best_confidence
            advisory.level = best_match.level.value
            advisory.recommended_action = best_match.recommended_action.value
            advisory.advisory_message = best_match.advisory_template.format(
                retry_count=retry_count,
                avg_latency=avg_latency,
                wait_time=15,  # Default wait time suggestion
                error_code=error_code,
                occurrence_count=occurrence_count,
            )
            advisory.decision_factors = decision_factors

            logger.info(
                f"[ForensicAdvisor] Pattern matched: {best_match.pattern_id} "
                f"for operation {operation.id} (confidence: {best_confidence})"
            )
        else:
            # No pattern matched - provide generic advice
            advisory.matched_pattern_id = "UNKNOWN"
            advisory.matched_pattern_name = "Unknown Pattern"
            advisory.confidence = 0.3
            advisory.level = AdvisoryLevel.WARNING.value
            advisory.recommended_action = RecommendedAction.MANUAL_CHECK.value
            advisory.advisory_message = (
                "알려진 패턴과 일치하지 않습니다. "
                f"에러 코드: {error_code or 'N/A'}, 재시도 횟수: {retry_count}. "
                "수동 확인이 필요합니다."
            )
            decision_factors = ["No known pattern matched"]

            logger.info(f"[ForensicAdvisor] No pattern matched for operation {operation.id}")

        advisory.evidence = evidence
        advisory.decision_factors = decision_factors

        return advisory

    def analyze_and_update(self, operation: FailedOperationProtocol) -> ForensicAdvisory:
        """
        Analyze operation and update its metadata with advisory.

        This method updates the operation's metadata but does NOT
        execute any automatic actions.

        Args:
            operation: Failed operation instance to analyze and update

        Returns:
            ForensicAdvisory with recommendations
        """
        advisory = self.analyze(operation)

        # Update operation metadata with advisory
        if operation.metadata is None:
            operation.metadata = {}

        operation.metadata["forensic_advisory"] = advisory.to_dict()
        operation.next_action_hint = advisory.to_hint_string()

        # Map recommended action to model's RecommendedAction choice
        action_mapping = {
            "replay": "replay",
            "manual_check": "manual_check",
            "escalate": "escalate",
            "security_review": "manual_check",  # Maps to manual_check
            "wait_and_retry": "replay",  # Maps to replay
            "archive": "archive",
        }
        operation.recommended_action = action_mapping.get(advisory.recommended_action, "manual_check")

        operation.save(update_fields=["metadata", "next_action_hint", "recommended_action", "updated_at"])

        logger.info(
            f"[ForensicAdvisor] Updated operation {operation.id} with advisory: "
            f"action={advisory.recommended_action}, confidence={advisory.confidence}"
        )

        return advisory

    def _calculate_pattern_confidence(
        self,
        pattern: FailurePattern,
        error_code: str,
        error_message: str,
    ) -> float:
        """
        Calculate confidence score for pattern match.

        Returns:
            Float between 0.0 and 1.0
        """
        score = 0.0
        max_score = 2.0  # Max possible score

        # Check error code match (weight: 1.0)
        if error_code:
            error_code_upper = error_code.upper()
            for code in pattern.error_codes:
                if code.upper() in error_code_upper or error_code_upper in code.upper():
                    score += 1.0
                    break

        # Check error message pattern match (weight: 1.0)
        if error_message:
            error_message_lower = error_message.lower()
            for regex_pattern in pattern.error_message_patterns:
                if re.search(regex_pattern, error_message_lower):
                    score += 1.0
                    break

        return min(score / max_score, 1.0)

    def _calculate_avg_latency(self, retry_history: list[dict]) -> int:
        """Calculate average latency from retry history."""
        if not retry_history:
            return 0

        latencies = []
        for attempt in retry_history:
            # Try to get latency from different possible field names
            latency = attempt.get("latency_ms") or attempt.get("backoff_seconds", 0) * 1000
            if latency:
                latencies.append(latency)

        return int(sum(latencies) / len(latencies)) if latencies else 0

    def _find_pattern_by_id(self, pattern_id: str) -> FailurePattern | None:
        """Find pattern by ID."""
        for pattern in self.patterns:
            if pattern.pattern_id == pattern_id:
                return pattern
        return None


# =============================================================================
# Module-level Singleton
# =============================================================================

_advisor_instance: ForensicAdvisorService | None = None


def get_forensic_advisor() -> ForensicAdvisorService:
    """Get or create the ForensicAdvisorService singleton."""
    global _advisor_instance
    if _advisor_instance is None:
        _advisor_instance = ForensicAdvisorService()
    return _advisor_instance


def analyze_failed_operation(operation: FailedOperationProtocol) -> ForensicAdvisory:
    """
    Convenience function to analyze a failed operation.

    Args:
        operation: Failed operation instance to analyze

    Returns:
        ForensicAdvisory with recommendations
    """
    return get_forensic_advisor().analyze(operation)


def analyze_and_update_operation(operation: FailedOperationProtocol) -> ForensicAdvisory:
    """
    Convenience function to analyze and update a failed operation.

    Args:
        operation: Failed operation instance to analyze and update

    Returns:
        ForensicAdvisory with recommendations
    """
    return get_forensic_advisor().analyze_and_update(operation)
