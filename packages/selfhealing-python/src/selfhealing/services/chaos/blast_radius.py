"""
Blast Radius Manager

Controls the scope of chaos experiments to prevent runaway failures.
Implements the principle of "contained chaos" with progressive scopes.

Levels:
- INSTANCE: Single pod/instance (lowest risk)
- SERVICE: Entire service (medium risk)
- REGION: Full region/availability zone (highest risk, requires approval)
"""

from __future__ import annotations

import structlog
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from selfhealing.core.timezone import now
from selfhealing.settings import ChaosBlastRadiusSettings, get_layered_settings

logger = structlog.get_logger()


# =============================================================================
# Enums
# =============================================================================


class BlastRadius(str, Enum):
    """Blast radius levels for chaos experiments."""

    INSTANCE = "instance"
    """Single instance/pod. Lowest risk. No approval required."""

    SERVICE = "service"
    """Entire service. Medium risk. May affect dependent services."""

    REGION = "region"
    """Full region/AZ. Highest risk. Requires manual approval."""


class ApprovalStatus(str, Enum):
    """Approval status for high-risk experiments."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ChaosBlastRadiusPolicy:
    """Policy configuration for blast radius management."""

    # Scope-based restrictions
    instance_max_concurrent: int = 5
    """Maximum concurrent experiments at INSTANCE level."""

    service_max_concurrent: int = 2
    """Maximum concurrent experiments at SERVICE level."""

    region_max_concurrent: int = 1
    """Maximum concurrent experiments at REGION level."""

    # Auto-approval thresholds
    instance_auto_approve: bool = True
    """Auto-approve INSTANCE level experiments."""

    service_auto_approve: bool = False
    """Auto-approve SERVICE level experiments."""

    region_auto_approve: bool = False
    """REGION level NEVER auto-approves."""

    # Time-based restrictions
    allowed_hours_start: int = 2
    """Start hour (UTC) for allowed experiment window (default: 2 AM)."""

    allowed_hours_end: int = 6
    """End hour (UTC) for allowed experiment window (default: 6 AM)."""

    allow_outside_window: bool = False
    """Allow experiments outside the maintenance window."""

    # Traffic restrictions
    max_traffic_percent_instance: float = 100.0
    """Maximum traffic % affected at INSTANCE level."""

    max_traffic_percent_service: float = 50.0
    """Maximum traffic % affected at SERVICE level."""

    max_traffic_percent_region: float = 10.0
    """Maximum traffic % affected at REGION level."""

    # Safety limits
    excluded_services: list[str] = field(default_factory=list)
    """Services that cannot be targeted by chaos experiments."""

    excluded_domains: list[str] = field(default_factory=list)
    """Domains that cannot be targeted."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "instance_max_concurrent": self.instance_max_concurrent,
            "service_max_concurrent": self.service_max_concurrent,
            "region_max_concurrent": self.region_max_concurrent,
            "instance_auto_approve": self.instance_auto_approve,
            "service_auto_approve": self.service_auto_approve,
            "region_auto_approve": self.region_auto_approve,
            "allowed_hours_start": self.allowed_hours_start,
            "allowed_hours_end": self.allowed_hours_end,
            "allow_outside_window": self.allow_outside_window,
            "max_traffic_percent_instance": self.max_traffic_percent_instance,
            "max_traffic_percent_service": self.max_traffic_percent_service,
            "max_traffic_percent_region": self.max_traffic_percent_region,
            "excluded_services": self.excluded_services,
            "excluded_domains": self.excluded_domains,
        }

    @classmethod
    def from_settings(cls) -> ChaosBlastRadiusPolicy:
        """
        LayeredSettings에서 정책 로드.

        92_CONFIG_IMPLEMENTATION_GUIDE.md Week 3 [13] ChaosBlastRadiusSettings 참조.
        91_CONFIG_INVENTORY.md §6.12, §12.1 참조.

        Returns:
            Settings 기반 ChaosBlastRadiusPolicy
        """
        settings = get_layered_settings(ChaosBlastRadiusSettings, "chaos_blast_radius")

        return cls(
            instance_max_concurrent=settings.instance_max_concurrent,
            service_max_concurrent=settings.service_max_concurrent,
            region_max_concurrent=settings.region_max_concurrent,
            instance_auto_approve=settings.instance_auto_approve,
            service_auto_approve=settings.service_auto_approve,
            region_auto_approve=settings.region_auto_approve,
            allowed_hours_start=settings.allowed_hours_start,
            allowed_hours_end=settings.allowed_hours_end,
            allow_outside_window=settings.allow_outside_window,
            max_traffic_percent_instance=settings.max_traffic_percent_instance,
            max_traffic_percent_service=settings.max_traffic_percent_service,
            max_traffic_percent_region=settings.max_traffic_percent_region,
            excluded_services=settings.excluded_services,
            excluded_domains=settings.excluded_domains,
        )

    @classmethod
    def from_env(cls) -> ChaosBlastRadiusPolicy:
        """
        환경변수에서 정책 로드.

        환경변수:
            CHAOS_EXCLUDED_SERVICES: 제외 서비스 (쉼표 구분)
            CHAOS_EXCLUDED_DOMAINS: 제외 도메인 (쉼표 구분)
            CHAOS_MAX_FAILURE_PERCENT: 최대 실패율 (기본: 10.0)
            CHAOS_MAX_AFFECTED_INSTANCES: 최대 영향 인스턴스 (기본: 1)
            CHAOS_ALLOW_OUTSIDE_WINDOW: 유지보수 윈도우 외 허용 (기본: false)

        Returns:
            환경변수 기반 ChaosBlastRadiusPolicy

        Example:
            # .env
            CHAOS_EXCLUDED_SERVICES=payment-core,toss-payment,iamport
            CHAOS_EXCLUDED_DOMAINS=payment,billing,settlement
            CHAOS_MAX_FAILURE_PERCENT=5.0
        """
        import os

        def parse_list(env_key: str) -> list[str]:
            raw = os.getenv(env_key, "")
            return [s.strip() for s in raw.split(",") if s.strip()]

        def parse_bool(env_key: str, default: bool = False) -> bool:
            raw = os.getenv(env_key, "").lower()
            if raw in ("true", "1", "yes"):
                return True
            if raw in ("false", "0", "no"):
                return False
            return default

        excluded_services = parse_list("CHAOS_EXCLUDED_SERVICES")
        excluded_domains = parse_list("CHAOS_EXCLUDED_DOMAINS")

        if excluded_services:
            logger.info(
                "blast_radius.loaded_excluded_services_env",
                excluded_services=excluded_services,
            )
        if excluded_domains:
            logger.info(
                "blast_radius.loaded_excluded_domains_env",
                excluded_domains=excluded_domains,
            )

        return cls(
            excluded_services=excluded_services,
            excluded_domains=excluded_domains,
            max_traffic_percent_region=float(os.getenv("CHAOS_MAX_FAILURE_PERCENT", "10.0")),
            region_max_concurrent=int(os.getenv("CHAOS_MAX_AFFECTED_INSTANCES", "1")),
            allow_outside_window=parse_bool("CHAOS_ALLOW_OUTSIDE_WINDOW", False),
        )

    def is_service_allowed(self, service_name: str) -> bool:
        """
        서비스가 Chaos 실험 대상으로 허용되는지 확인.

        Args:
            service_name: 확인할 서비스 이름

        Returns:
            True if 서비스가 실험 대상으로 허용됨
        """
        if service_name in self.excluded_services:
            logger.warning(
                "blast_radius.service_excluded_list",
                service_name=service_name,
            )
            return False

        # 도메인 패턴 매칭 (서비스명에 도메인이 포함된 경우)
        for domain in self.excluded_domains:
            if domain.lower() in service_name.lower():
                logger.warning(
                    "blast_radius.service_matches_excluded_domain",
                    service_name=service_name,
                    domain=domain,
                )
                return False

        return True


@dataclass
class ChaosApprovalRequest:
    """Approval request for high-risk experiments."""

    experiment_id: str
    blast_radius: str
    target_service: str
    target_domain: str

    # Request metadata
    requested_by: str = ""
    requested_at: str = field(default_factory=lambda: now().isoformat())
    reason: str = ""

    # Approval metadata
    status: str = ApprovalStatus.PENDING.value
    approved_by: str = ""
    approved_at: str = ""
    denial_reason: str = ""

    # Expiry
    expires_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "experiment_id": self.experiment_id,
            "blast_radius": self.blast_radius,
            "target_service": self.target_service,
            "target_domain": self.target_domain,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "reason": self.reason,
            "status": self.status,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "denial_reason": self.denial_reason,
            "expires_at": self.expires_at,
        }


@dataclass
class BlastRadiusCheckResult:
    """Result of a blast radius validation check."""

    allowed: bool
    blast_radius: str
    requires_approval: bool
    approval_status: str

    # Violation details
    violations: list[str] = field(default_factory=list)

    # Computed limits
    max_traffic_percent: float = 100.0
    max_concurrent: int = 5
    current_concurrent: int = 0

    # Time window
    within_allowed_window: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "allowed": self.allowed,
            "blast_radius": self.blast_radius,
            "requires_approval": self.requires_approval,
            "approval_status": self.approval_status,
            "violations": self.violations,
            "max_traffic_percent": self.max_traffic_percent,
            "max_concurrent": self.max_concurrent,
            "current_concurrent": self.current_concurrent,
            "within_allowed_window": self.within_allowed_window,
        }


# =============================================================================
# Blast Radius Manager
# =============================================================================


class BlastRadiusManager:
    """
    Manages blast radius policies and approvals for chaos experiments.

    Responsibilities:
    1. Validate experiment scope against policy
    2. Manage approval workflow for high-risk experiments
    3. Track concurrent experiments by scope
    4. Enforce time-based restrictions
    5. Integrate with ControlAPIService for governance

    Usage:
        manager = get_blast_radius_manager()

        # Check if experiment is allowed
        result = manager.check(
            blast_radius=BlastRadius.SERVICE,
            target_service="payment",
            experiment_id="chaos-abc123"
        )

        if not result.allowed:
            print(f"Blocked: {result.violations}")
        elif result.requires_approval:
            # Submit for approval
            manager.request_approval(...)
    """

    def __init__(self, policy: ChaosBlastRadiusPolicy | None = None):
        """Initialize BlastRadiusManager."""
        self._policy = policy or ChaosBlastRadiusPolicy()
        self._lock = threading.RLock()

        # Tracking state
        self._active_experiments: dict[str, dict[str, Any]] = {}
        self._pending_approvals: dict[str, ChaosApprovalRequest] = {}
        self._approved_experiments: set[str] = set()

    def _log_blast_radius_audit(
        self,
        experiment_id: str,
        blast_radius: str,
        target_service: str,
        action: str,
        allowed: bool = True,
        violations: list[str] | None = None,
        approval_status: str | None = None,
        target_domain: str | None = None,
        traffic_percent: float | None = None,
        reason: str | None = None,
    ) -> None:
        """Audit 헬퍼를 통해 blast radius 이벤트 기록."""
        try:
            from selfhealing.services.audit_helpers import log_blast_radius_audit

            log_blast_radius_audit(
                experiment_id=experiment_id,
                blast_radius=blast_radius,
                target_service=target_service,
                action=action,
                allowed=allowed,
                violations=violations,
                approval_status=approval_status,
                target_domain=target_domain,
                traffic_percent=traffic_percent,
                reason=reason,
            )
        except Exception as e:
            logger.debug(
                "blast_radius_manager.audit_logging_failed",
                error=e,
            )

    # =========================================================================
    # Policy Management
    # =========================================================================

    def get_policy(self) -> ChaosBlastRadiusPolicy:
        """Get current policy."""
        return self._policy

    def update_policy(self, **kwargs) -> ChaosBlastRadiusPolicy:
        """
        Update policy settings.

        Args:
            **kwargs: Policy fields to update

        Returns:
            Updated policy
        """
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._policy, key):
                    setattr(self._policy, key, value)
                    logger.info(
                        "blast_radius.updated_policy",
                        key=key,
                        value=value,
                    )

            self._persist_policy()
            return self._policy

    def _persist_policy(self) -> None:
        """Persist policy to storage."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            manager.update_chaos_config(blast_radius_policy=self._policy.to_dict())
        except Exception as e:
            logger.warning(
                "blast_radius.persist_policy",
                error=e,
            )

    def _load_policy(self) -> None:
        """Load policy from storage."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            config = manager.get_chaos_config()
            policy_data = config.get("blast_radius_policy", {})

            if policy_data:
                for key, value in policy_data.items():
                    if hasattr(self._policy, key):
                        setattr(self._policy, key, value)
        except Exception as e:
            logger.warning(
                "blast_radius.load_policy",
                error=e,
            )

    # =========================================================================
    # Blast Radius Validation
    # =========================================================================

    def _check_exclusions(self, target_service: str, target_domain: str, violations: list) -> None:
        """Check if service/domain is excluded."""
        if target_service in self._policy.excluded_services:
            violations.append(f"Service '{target_service}' is excluded from chaos experiments")
        if target_domain and target_domain in self._policy.excluded_domains:
            violations.append(f"Domain '{target_domain}' is excluded from chaos experiments")

    def _check_time_window_violation(self, violations: list) -> bool:
        """Check time window and add violation if needed. Returns within_window."""
        within_window = self._check_time_window()
        if not within_window and not self._policy.allow_outside_window:
            violations.append(
                f"Outside allowed experiment window "
                f"({self._policy.allowed_hours_start}:00 - {self._policy.allowed_hours_end}:00 UTC)"
            )
        return within_window

    def _check_concurrent_limit(self, blast_radius: BlastRadius, violations: list) -> tuple[int, int]:
        """Check concurrent limit. Returns (current, max)."""
        current = self._count_concurrent(blast_radius)
        max_limit = self._get_max_concurrent(blast_radius)
        if current >= max_limit:
            violations.append(f"Concurrent experiment limit reached for {blast_radius.value}: {current}/{max_limit}")
        return current, max_limit

    def _check_traffic_limit(self, blast_radius: BlastRadius, traffic_percent: float, violations: list) -> float:
        """Check traffic limit. Returns max_traffic."""
        max_traffic = self._get_max_traffic(blast_radius)
        if traffic_percent > max_traffic:
            violations.append(
                f"Traffic percent {traffic_percent}% exceeds limit {max_traffic}% " f"for {blast_radius.value} level"
            )
        return max_traffic

    def _check_approval_requirements(
        self, blast_radius: BlastRadius, experiment_id: str, violations: list
    ) -> tuple[bool, str]:
        """Check approval requirements. Returns (requires_approval, approval_status)."""
        if blast_radius == BlastRadius.REGION:
            approval_status = self._get_approval_status(experiment_id)
            if approval_status != ApprovalStatus.APPROVED.value:
                violations.append(f"REGION level experiments require manual approval. Current status: {approval_status}")
            return True, approval_status

        if blast_radius == BlastRadius.SERVICE and not self._policy.service_auto_approve:
            approval_status = self._get_approval_status(experiment_id)
            if approval_status not in (
                ApprovalStatus.APPROVED.value,
                ApprovalStatus.NOT_REQUIRED.value,
            ):
                violations.append(f"SERVICE level experiments require approval. Current status: {approval_status}")
            return True, approval_status

        return False, ApprovalStatus.NOT_REQUIRED.value

    def check(
        self,
        blast_radius: BlastRadius | str,
        target_service: str,
        target_domain: str = "",
        experiment_id: str = "",
        traffic_percent: float = 100.0,
    ) -> BlastRadiusCheckResult:
        """
        Check if an experiment is allowed under current policy.

        Args:
            blast_radius: Requested blast radius level
            target_service: Target service name
            target_domain: Target domain (optional)
            experiment_id: Experiment ID (for approval lookup)
            traffic_percent: Percentage of traffic to affect

        Returns:
            BlastRadiusCheckResult with validation details
        """
        if isinstance(blast_radius, str):
            blast_radius = BlastRadius(blast_radius)

        violations = []

        with self._lock:
            self._check_exclusions(target_service, target_domain, violations)
            within_window = self._check_time_window_violation(violations)
            current_concurrent, max_concurrent = self._check_concurrent_limit(blast_radius, violations)
            max_traffic = self._check_traffic_limit(blast_radius, traffic_percent, violations)
            requires_approval, approval_status = self._check_approval_requirements(blast_radius, experiment_id, violations)

        result = BlastRadiusCheckResult(
            allowed=len(violations) == 0,
            blast_radius=blast_radius.value,
            requires_approval=requires_approval,
            approval_status=approval_status,
            violations=violations,
            max_traffic_percent=max_traffic,
            max_concurrent=max_concurrent,
            current_concurrent=current_concurrent,
            within_allowed_window=within_window,
        )

        # Audit 로깅
        self._log_blast_radius_audit(
            experiment_id=experiment_id,
            blast_radius=blast_radius.value,
            target_service=target_service,
            action="check",
            allowed=result.allowed,
            violations=violations if violations else None,
            approval_status=approval_status,
            target_domain=target_domain,
            traffic_percent=traffic_percent,
        )

        return result

    def _check_time_window(self) -> bool:
        """Check if current time is within allowed window."""
        current_hour = now().hour
        start = self._policy.allowed_hours_start
        end = self._policy.allowed_hours_end

        if start <= end:
            return start <= current_hour < end
        else:
            # Window spans midnight
            return current_hour >= start or current_hour < end

    def _get_max_concurrent(self, blast_radius: BlastRadius) -> int:
        """Get maximum concurrent experiments for blast radius level."""
        return {
            BlastRadius.INSTANCE: self._policy.instance_max_concurrent,
            BlastRadius.SERVICE: self._policy.service_max_concurrent,
            BlastRadius.REGION: self._policy.region_max_concurrent,
        }.get(blast_radius, 1)

    def _get_max_traffic(self, blast_radius: BlastRadius) -> float:
        """Get maximum traffic percentage for blast radius level."""
        return {
            BlastRadius.INSTANCE: self._policy.max_traffic_percent_instance,
            BlastRadius.SERVICE: self._policy.max_traffic_percent_service,
            BlastRadius.REGION: self._policy.max_traffic_percent_region,
        }.get(blast_radius, 100.0)

    def _count_concurrent(self, blast_radius: BlastRadius) -> int:
        """Count currently active experiments at given blast radius level."""
        count = 0
        for exp_data in self._active_experiments.values():
            if exp_data.get("blast_radius") == blast_radius.value:
                count += 1
        return count

    def _get_approval_status(self, experiment_id: str) -> str:
        """Get approval status for experiment."""
        if experiment_id in self._approved_experiments:
            return ApprovalStatus.APPROVED.value

        if experiment_id in self._pending_approvals:
            return self._pending_approvals[experiment_id].status

        return ApprovalStatus.PENDING.value

    # =========================================================================
    # Experiment Lifecycle Tracking
    # =========================================================================

    def register_experiment(
        self,
        experiment_id: str,
        blast_radius: BlastRadius | str,
        target_service: str,
        target_domain: str = "",
    ) -> bool:
        """
        Register an experiment as active.

        Args:
            experiment_id: Unique experiment ID
            blast_radius: Blast radius level
            target_service: Target service
            target_domain: Target domain

        Returns:
            True if registered successfully
        """
        if isinstance(blast_radius, str):
            blast_radius = BlastRadius(blast_radius)

        with self._lock:
            self._active_experiments[experiment_id] = {
                "blast_radius": blast_radius.value,
                "target_service": target_service,
                "target_domain": target_domain,
                "started_at": now().isoformat(),
            }
            logger.info(
                "cell_registry.bulkheads_registered",
                experiment_id=experiment_id,
                blast_radius=blast_radius.value,
            )
            return True

    def unregister_experiment(self, experiment_id: str) -> bool:
        """
        Unregister an experiment when it completes.

        Args:
            experiment_id: Experiment ID to unregister

        Returns:
            True if unregistered successfully
        """
        with self._lock:
            if experiment_id in self._active_experiments:
                del self._active_experiments[experiment_id]
                logger.info(
                    "blast_radius.unregistered_experiment",
                    experiment_id=experiment_id,
                )
                return True
            return False

    def get_active_experiments(self) -> dict[str, dict[str, Any]]:
        """Get all currently active experiments."""
        with self._lock:
            return self._active_experiments.copy()

    # =========================================================================
    # Approval Workflow
    # =========================================================================

    def request_approval(
        self,
        experiment_id: str,
        blast_radius: BlastRadius | str,
        target_service: str,
        target_domain: str = "",
        requested_by: str = "",
        reason: str = "",
        expires_in_hours: int = 24,
    ) -> ChaosApprovalRequest:
        """
        Request approval for a high-risk experiment.

        Args:
            experiment_id: Experiment ID
            blast_radius: Blast radius level
            target_service: Target service
            target_domain: Target domain
            requested_by: Requester identity
            reason: Reason for experiment
            expires_in_hours: Hours until approval expires

        Returns:
            ChaosApprovalRequest with current status
        """
        if isinstance(blast_radius, str):
            blast_radius = BlastRadius(blast_radius)

        from datetime import timedelta

        with self._lock:
            request = ChaosApprovalRequest(
                experiment_id=experiment_id,
                blast_radius=blast_radius.value,
                target_service=target_service,
                target_domain=target_domain,
                requested_by=requested_by,
                reason=reason,
                status=ApprovalStatus.PENDING.value,
                expires_at=(now() + timedelta(hours=expires_in_hours)).isoformat(),
            )

            self._pending_approvals[experiment_id] = request
            logger.info(
                f"[BlastRadius] Approval requested for {experiment_id} " f"({blast_radius.value} level) by {requested_by}"
            )

            # Send notification (best-effort)
            self._notify_approval_requested(request)

            return request

    def approve(
        self,
        experiment_id: str,
        approved_by: str,
    ) -> ChaosApprovalRequest:
        """
        Approve a pending experiment.

        Args:
            experiment_id: Experiment ID to approve
            approved_by: Approver identity

        Returns:
            Updated ChaosApprovalRequest
        """
        with self._lock:
            if experiment_id not in self._pending_approvals:
                raise ValueError(f"No pending approval for experiment {experiment_id}")

            request = self._pending_approvals[experiment_id]
            request.status = ApprovalStatus.APPROVED.value
            request.approved_by = approved_by
            request.approved_at = now().isoformat()

            self._approved_experiments.add(experiment_id)

            logger.info(
                "blast_radius.experiment_approved",
                experiment_id=experiment_id,
                approved_by=approved_by,
            )

            # Audit record
            self._record_approval_decision(request)

            return request

    def deny(
        self,
        experiment_id: str,
        denied_by: str,
        reason: str = "",
    ) -> ChaosApprovalRequest:
        """
        Deny a pending experiment.

        Args:
            experiment_id: Experiment ID to deny
            denied_by: Denier identity
            reason: Reason for denial

        Returns:
            Updated ChaosApprovalRequest
        """
        with self._lock:
            if experiment_id not in self._pending_approvals:
                raise ValueError(f"No pending approval for experiment {experiment_id}")

            request = self._pending_approvals[experiment_id]
            request.status = ApprovalStatus.DENIED.value
            request.approved_by = denied_by
            request.approved_at = now().isoformat()
            request.denial_reason = reason

            logger.info(
                "blast_radius.experiment_denied",
                experiment_id=experiment_id,
                denied_by=denied_by,
                reason=reason,
            )

            # Audit record
            self._record_approval_decision(request)

            return request

    def get_pending_approvals(self) -> list[ChaosApprovalRequest]:
        """Get all pending approval requests."""
        with self._lock:
            return [req for req in self._pending_approvals.values() if req.status == ApprovalStatus.PENDING.value]

    # =========================================================================
    # ControlAPIService Integration
    # =========================================================================

    def requires_control_api_approval(self, blast_radius: BlastRadius | str) -> bool:
        """
        Check if blast radius requires ControlAPIService approval.

        REGION level always requires RequiresManualApproval flag.
        """
        if isinstance(blast_radius, str):
            blast_radius = BlastRadius(blast_radius)

        return blast_radius == BlastRadius.REGION

    def get_control_api_flags(self, blast_radius: BlastRadius | str) -> dict[str, Any]:
        """
        Get ControlAPIService flags for blast radius level.

        Returns:
            Dict with flags for ControlAPIService integration
        """
        if isinstance(blast_radius, str):
            blast_radius = BlastRadius(blast_radius)

        return {
            "requires_manual_approval": blast_radius == BlastRadius.REGION,
            "risk_level": {
                BlastRadius.INSTANCE: "info",
                BlastRadius.SERVICE: "warning",
                BlastRadius.REGION: "critical",
            }.get(blast_radius, "warning"),
            "environment": "chaos",
        }

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _notify_approval_requested(self, request: ChaosApprovalRequest) -> None:
        """Send notification for approval request."""
        try:
            from selfhealing.adapters.alert import get_alert_adapter

            adapter = get_alert_adapter()
            if adapter:
                adapter.alert(
                    severity="warning",
                    title=f"Chaos Experiment Approval Required: {request.blast_radius.upper()}",
                    message=(
                        f"Experiment {request.experiment_id} targeting {request.target_service} "
                        f"requires approval.\n\nReason: {request.reason}\n"
                        f"Requested by: {request.requested_by}"
                    ),
                    tags=["chaos", "approval", request.blast_radius],
                )
        except Exception as e:
            logger.warning(
                "blast_radius.send_approval_notification",
                error=e,
            )

    def _record_approval_decision(self, request: ChaosApprovalRequest) -> None:
        """Record approval decision to audit trail."""
        try:
            logger.info(
                f"[BlastRadius] Approval decision recorded: "
                f"experiment={request.experiment_id}, "
                f"status={request.status}, "
                f"by={request.approved_by}"
            )
        except Exception as e:
            logger.warning(
                "blast_radius.record_decision",
                error=e,
            )


# =============================================================================
# Singleton
# =============================================================================


_blast_radius_manager: BlastRadiusManager | None = None
_manager_lock = threading.Lock()


def get_blast_radius_manager() -> BlastRadiusManager:
    """Get the singleton BlastRadiusManager instance."""
    global _blast_radius_manager

    if _blast_radius_manager is None:
        with _manager_lock:
            if _blast_radius_manager is None:
                _blast_radius_manager = BlastRadiusManager()
                _blast_radius_manager._load_policy()

    return _blast_radius_manager


def reset_blast_radius_manager() -> None:
    """Reset the singleton (for testing)."""
    global _blast_radius_manager
    with _manager_lock:
        _blast_radius_manager = None
