"""
Security Violation Data Models.

Contains data classes for security violation handling results
and configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from selfhealing.services.security.policies import ActionPolicy
from selfhealing.settings import get_security_settings


@dataclass
class ProtectionResult:
    """
    보호 조치 실행 결과 (순위 0 - v2.0.0).

    v2.1.0: 롤백 관련 필드 추가 (순위 0.3)
    v2.2.0: triggering_trace_id 추가 - 대시보드에서 "어떤 요청 때문에
    이 사용자의 세션이 무효화되었는가"를 원클릭으로 추적 가능.
    """

    success: bool
    executed_policies: list[ActionPolicy] = field(default_factory=list)
    failed_policies: list[ActionPolicy] = field(default_factory=list)
    highest_priority_succeeded: bool = True
    # v2.1.0: 롤백 관련 (순위 0.3)
    rolled_back_policies: list[ActionPolicy] = field(default_factory=list)
    rollback_success: bool = True
    error_message: str = ""
    # v2.2.0: Tracing Deep Link
    triggering_trace_id: str | None = None
    """보호 조치를 유발한 원본 요청의 trace_id (Jaeger/Zipkin 연동용)."""
    triggering_request_path: str | None = None
    """보호 조치를 유발한 원본 요청 경로 (예: POST /api/payments/)."""

    def get_trace_url(self, template: str = "") -> str | None:
        """
        Trace UI Deep Link 생성.

        Args:
            template: URL 템플릿 (예: "https://jaeger.example.com/trace/{trace_id}")

        Returns:
            Deep Link URL 또는 None
        """
        if not self.triggering_trace_id:
            return None
        if not template:
            template = os.environ.get("SELFHEALING_TRACE_URL_TEMPLATE", "")
        if not template:
            return None
        return template.replace("{trace_id}", self.triggering_trace_id)


@dataclass
class SecurityViolationResult:
    """Result of security violation handling."""

    success: bool
    incident_id: int | None = None
    action_taken: str = ""
    error: str | None = None
    protection_result: ProtectionResult | None = None

    @classmethod
    def handled(
        cls,
        incident_id: int,
        action: str,
        protection_result: ProtectionResult | None = None,
    ) -> SecurityViolationResult:
        """Factory for successfully handled violation."""
        return cls(
            success=True,
            incident_id=incident_id,
            action_taken=action,
            protection_result=protection_result,
        )

    @classmethod
    def failed(cls, error: str) -> SecurityViolationResult:
        """Factory for failed handling."""
        return cls(success=False, error=error)


@dataclass
class SecurityConfig:
    """Configuration for security violation handling."""

    # Rate limit abuse detection
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 100

    # IP ban settings
    temporary_ban_hours: int = 1
    permanent_ban_threshold: int = 5  # violations before permanent ban

    # Suspicious IP tracking cache timeout (seconds)
    suspicious_ip_cache_timeout: int = 86400  # 24 hours

    # Injection attempt ban duration (hours)
    injection_ban_hours: int = 24

    # Suspicious activity detection
    failed_login_threshold: int = 5
    suspicious_ip_cache_prefix: str = "security:suspicious_ip:"
    banned_ip_cache_prefix: str = "security:banned_ip:"

    @classmethod
    def from_settings(cls) -> SecurityConfig:
        """Load configuration from settings."""
        security = get_security_settings()
        return cls(
            rate_limit_window_seconds=security.rate_limit_window_seconds,
            rate_limit_max_requests=security.rate_limit_max_requests,
            temporary_ban_hours=security.temporary_ban_hours,
            permanent_ban_threshold=security.permanent_ban_threshold,
            suspicious_ip_cache_timeout=security.suspicious_ip_cache_timeout,
            injection_ban_hours=security.injection_ban_hours,
            failed_login_threshold=security.failed_login_threshold,
            suspicious_ip_cache_prefix=security.suspicious_ip_cache_prefix,
            banned_ip_cache_prefix=security.banned_ip_cache_prefix,
        )
