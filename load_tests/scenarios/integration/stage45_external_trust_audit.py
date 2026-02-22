"""
Stage 45: External Trust & Audit Readiness - Deterministic Test Suite (Part 1)

This stage proves that the system can produce externally verifiable evidence for:
- SLA compliance
- Governance correctness
- Operator accountability
- Policy decision traceability
- Incident explainability

This answers the question:
"Can this system defend itself in front of customers, auditors, regulators, or courts?"

Hard Invariants (NON-NEGOTIABLE):
1. Every externally relevant event is recorded
2. No decision is untraceable
3. Every request maps to: policy_version, operator (if any), automated vs manual decision
4. Incident timelines are reconstructible
5. Evidence is immutable once recorded
6. No hidden or implicit behavior exists
7. System explanations are deterministic

Execution:
    pytest load_tests/scenarios/stage45_external_trust_audit.py -v -s

Reference: Stage 44
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone as tz
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Set

import pytest


# =============================================================================
# Logging Configuration
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Core Data Structures for Audit Trail
# =============================================================================


class EventType(str, Enum):
    """Types of auditable events."""

    REQUEST_RECEIVED = "request_received"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"
    POLICY_EVALUATED = "policy_evaluated"
    POLICY_CHANGED = "policy_changed"
    OPERATOR_ACTION = "operator_action"
    CIRCUIT_BREAKER_STATE_CHANGE = "circuit_breaker_state_change"
    RETRY_ATTEMPTED = "retry_attempted"
    DLQ_ENQUEUED = "dlq_enqueued"
    DLQ_REPLAYED = "dlq_replayed"
    SLA_THRESHOLD_APPROACHED = "sla_threshold_approached"
    SLA_BREACH = "sla_breach"
    INCIDENT_STARTED = "incident_started"
    INCIDENT_RESOLVED = "incident_resolved"
    AUTOMATED_RECOVERY = "automated_recovery"
    MANUAL_OVERRIDE = "manual_override"


class DecisionSource(str, Enum):
    """Source of a decision."""

    AUTOMATED = "automated"
    MANUAL = "manual"
    POLICY = "policy"
    OPERATOR = "operator"
    SYSTEM = "system"


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit event record."""

    event_id: str
    timestamp: datetime
    event_type: EventType
    request_id: Optional[str]
    policy_version: str
    operator_id: Optional[int]
    decision_source: DecisionSource
    details: Dict[str, Any]
    parent_event_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "request_id": self.request_id,
            "policy_version": self.policy_version,
            "operator_id": self.operator_id,
            "decision_source": self.decision_source.value,
            "details": self.details,
            "parent_event_id": self.parent_event_id,
        }

    def compute_hash(self) -> str:
        """Compute immutable hash for evidence integrity."""
        data = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]


@dataclass
class PolicyVersion:
    """Immutable policy version snapshot."""

    version_id: str
    timestamp: datetime
    config_snapshot: Dict[str, Any]
    changed_by: Optional[int] = None  # operator_id if manual change
    change_reason: str = ""

    @classmethod
    def create(cls, config: Dict[str, Any], changed_by: Optional[int] = None, reason: str = "") -> "PolicyVersion":
        return cls(
            version_id=str(uuid.uuid4())[:8],
            timestamp=datetime.now(tz.utc),
            config_snapshot=copy.deepcopy(config),
            changed_by=changed_by,
            change_reason=reason,
        )


@dataclass
class DecisionRecord:
    """Decision record with full traceability."""

    request_id: str
    policy_version: str
    decision: str
    decision_source: DecisionSource
    timestamp: datetime
    operator_id: Optional[int] = None
    justification: str = ""
    evaluation_result: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    retry_count: int = 0


@dataclass
class OperatorAction:
    """Operator action record for accountability."""

    action_id: str
    operator_id: int
    action_type: str
    target: str
    timestamp: datetime
    reason: str
    policy_version_before: str
    policy_version_after: Optional[str]
    approved_by: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "operator_id": self.operator_id,
            "action_type": self.action_type,
            "target": self.target,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "policy_version_before": self.policy_version_before,
            "policy_version_after": self.policy_version_after,
            "approved_by": self.approved_by,
        }


@dataclass
class Incident:
    """Incident record for timeline reconstruction."""

    incident_id: str
    started_at: datetime
    resolved_at: Optional[datetime]
    severity: str
    description: str
    root_cause: Optional[str]
    affected_requests: List[str]
    related_events: List[str]
    operator_actions: List[str]
    policy_versions_during: List[str]
    resolution_type: Optional[str] = None  # automated, manual, hybrid


@dataclass
class SLAMetrics:
    """SLA compliance metrics snapshot."""

    timestamp: datetime
    period_start: datetime
    period_end: datetime
    total_requests: int
    successful_requests: int
    failed_requests: int
    avg_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    uptime_percentage: float
    sla_target_latency_ms: float
    sla_target_uptime: float
    breaches: List[Dict[str, Any]]


# =============================================================================
# Audit Trail Storage (Immutable)
# =============================================================================


class ImmutableAuditTrail:
    """
    Immutable audit trail storage.

    Once an event is recorded, it cannot be modified or deleted.
    This simulates production audit requirements.
    """

    def __init__(self):
        self._events: List[AuditEvent] = []
        self._event_hashes: Set[str] = set()
        self._lock = threading.RLock()
        self._sealed = False

    def record(self, event: AuditEvent) -> str:
        """Record an audit event. Returns event hash."""
        with self._lock:
            if self._sealed:
                raise RuntimeError("Audit trail is sealed - no new events allowed")

            event_hash = event.compute_hash()
            if event_hash in self._event_hashes:
                raise ValueError(f"Duplicate event hash: {event_hash}")

            self._events.append(event)
            self._event_hashes.add(event_hash)
            return event_hash

    def get_events(
        self,
        event_type: Optional[EventType] = None,
        request_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        operator_id: Optional[int] = None,
    ) -> List[AuditEvent]:
        """Query events with filters."""
        with self._lock:
            result = self._events.copy()

        if event_type:
            result = [e for e in result if e.event_type == event_type]
        if request_id:
            result = [e for e in result if e.request_id == request_id]
        if start_time:
            result = [e for e in result if e.timestamp >= start_time]
        if end_time:
            result = [e for e in result if e.timestamp <= end_time]
        if operator_id:
            result = [e for e in result if e.operator_id == operator_id]

        return sorted(result, key=lambda e: e.timestamp)

    def get_event_by_id(self, event_id: str) -> Optional[AuditEvent]:
        """Get specific event by ID."""
        with self._lock:
            for event in self._events:
                if event.event_id == event_id:
                    return event
        return None

    def verify_integrity(self) -> Tuple[bool, List[str]]:
        """Verify all events have valid hashes."""
        issues = []
        with self._lock:
            for event in self._events:
                expected_hash = event.compute_hash()
                if expected_hash not in self._event_hashes:
                    issues.append(f"Hash mismatch for event {event.event_id}")

        return len(issues) == 0, issues

    def seal(self) -> None:
        """Seal the audit trail - no more events can be added."""
        with self._lock:
            self._sealed = True

    def get_all_events(self) -> List[AuditEvent]:
        """Get all events."""
        with self._lock:
            return self._events.copy()

    def clear(self) -> None:
        """Clear for testing only."""
        with self._lock:
            self._events.clear()
            self._event_hashes.clear()
            self._sealed = False


# =============================================================================
# Policy Manager with Audit Trail
# =============================================================================


class AuditablePolicyManager:
    """Policy manager with full audit trail."""

    def __init__(
        self,
        initial_config: Dict[str, Any],
        audit_trail: ImmutableAuditTrail,
    ):
        self._config = copy.deepcopy(initial_config)
        self._version = PolicyVersion.create(self._config)
        self._history: List[PolicyVersion] = [self._version]
        self._audit_trail = audit_trail
        self._lock = threading.RLock()

    def get_current_version(self) -> PolicyVersion:
        with self._lock:
            return self._version

    def get_current_config(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._config)

    def capture_snapshot(self) -> PolicyVersion:
        """Capture immutable snapshot for request binding."""
        with self._lock:
            return PolicyVersion(
                version_id=self._version.version_id,
                timestamp=self._version.timestamp,
                config_snapshot=copy.deepcopy(self._config),
                changed_by=self._version.changed_by,
                change_reason=self._version.change_reason,
            )

    def update_config(
        self,
        updates: Dict[str, Any],
        operator_id: Optional[int] = None,
        reason: str = "",
    ) -> PolicyVersion:
        """Update config with audit trail."""
        with self._lock:
            old_version = self._version.version_id

            for key, value in updates.items():
                self._config[key] = value

            self._version = PolicyVersion.create(
                self._config,
                changed_by=operator_id,
                reason=reason,
            )
            self._history.append(self._version)

            # Record audit event
            event = AuditEvent(
                event_id=str(uuid.uuid4())[:8],
                timestamp=datetime.now(tz.utc),
                event_type=EventType.POLICY_CHANGED,
                request_id=None,
                policy_version=self._version.version_id,
                operator_id=operator_id,
                decision_source=DecisionSource.OPERATOR if operator_id else DecisionSource.SYSTEM,
                details={
                    "old_version": old_version,
                    "new_version": self._version.version_id,
                    "changes": updates,
                    "reason": reason,
                },
            )
            self._audit_trail.record(event)

            return self._version

    def get_history(self) -> List[PolicyVersion]:
        with self._lock:
            return list(self._history)

    def get_version_at_time(self, timestamp: datetime) -> Optional[PolicyVersion]:
        """Get policy version that was active at given time."""
        with self._lock:
            for version in reversed(self._history):
                if version.timestamp <= timestamp:
                    return version
        return None


# =============================================================================
# Decision Recorder with Full Traceability
# =============================================================================


class AuditableDecisionRecorder:
    """Records decisions with full audit trail."""

    def __init__(self, audit_trail: ImmutableAuditTrail):
        self._audit_trail = audit_trail
        self._decisions: List[DecisionRecord] = []
        self._lock = threading.Lock()

    def record_decision(
        self,
        request_id: str,
        policy_version: str,
        decision: str,
        decision_source: DecisionSource,
        operator_id: Optional[int] = None,
        justification: str = "",
        latency_ms: float = 0.0,
        retry_count: int = 0,
        details: Dict[str, Any] = None,
    ) -> DecisionRecord:
        """Record a decision with audit trail."""
        record = DecisionRecord(
            request_id=request_id,
            policy_version=policy_version,
            decision=decision,
            decision_source=decision_source,
            timestamp=datetime.now(tz.utc),
            operator_id=operator_id,
            justification=justification,
            latency_ms=latency_ms,
            retry_count=retry_count,
            evaluation_result=details or {},
        )

        with self._lock:
            self._decisions.append(record)

        # Map decision to event type
        if decision == "completed":
            event_type = EventType.REQUEST_COMPLETED
        elif decision == "failed":
            event_type = EventType.REQUEST_FAILED
        elif decision == "retried":
            event_type = EventType.RETRY_ATTEMPTED
        elif decision == "dlq":
            event_type = EventType.DLQ_ENQUEUED
        else:
            event_type = EventType.POLICY_EVALUATED

        event = AuditEvent(
            event_id=str(uuid.uuid4())[:8],
            timestamp=record.timestamp,
            event_type=event_type,
            request_id=request_id,
            policy_version=policy_version,
            operator_id=operator_id,
            decision_source=decision_source,
            details={
                "decision": decision,
                "justification": justification,
                "latency_ms": latency_ms,
                "retry_count": retry_count,
                **(details or {}),
            },
        )
        self._audit_trail.record(event)

        return record

    def get_decisions(
        self,
        request_id: Optional[str] = None,
        policy_version: Optional[str] = None,
    ) -> List[DecisionRecord]:
        """Query decisions."""
        with self._lock:
            result = self._decisions.copy()

        if request_id:
            result = [d for d in result if d.request_id == request_id]
        if policy_version:
            result = [d for d in result if d.policy_version == policy_version]

        return result

    def clear(self) -> None:
        with self._lock:
            self._decisions.clear()


# =============================================================================
# Operator Action Recorder
# =============================================================================


class OperatorActionRecorder:
    """Records operator actions for accountability."""

    def __init__(self, audit_trail: ImmutableAuditTrail):
        self._audit_trail = audit_trail
        self._actions: List[OperatorAction] = []
        self._lock = threading.Lock()

    def record_action(
        self,
        operator_id: int,
        action_type: str,
        target: str,
        reason: str,
        policy_version_before: str,
        policy_version_after: Optional[str] = None,
        approved_by: Optional[int] = None,
    ) -> OperatorAction:
        """Record an operator action."""
        action = OperatorAction(
            action_id=str(uuid.uuid4())[:8],
            operator_id=operator_id,
            action_type=action_type,
            target=target,
            timestamp=datetime.now(tz.utc),
            reason=reason,
            policy_version_before=policy_version_before,
            policy_version_after=policy_version_after,
            approved_by=approved_by,
        )

        with self._lock:
            self._actions.append(action)

        event = AuditEvent(
            event_id=str(uuid.uuid4())[:8],
            timestamp=action.timestamp,
            event_type=EventType.OPERATOR_ACTION,
            request_id=None,
            policy_version=policy_version_after or policy_version_before,
            operator_id=operator_id,
            decision_source=DecisionSource.OPERATOR,
            details=action.to_dict(),
        )
        self._audit_trail.record(event)

        return action

    def get_actions(
        self,
        operator_id: Optional[int] = None,
        action_type: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[OperatorAction]:
        """Query operator actions."""
        with self._lock:
            result = self._actions.copy()

        if operator_id:
            result = [a for a in result if a.operator_id == operator_id]
        if action_type:
            result = [a for a in result if a.action_type == action_type]
        if start_time:
            result = [a for a in result if a.timestamp >= start_time]
        if end_time:
            result = [a for a in result if a.timestamp <= end_time]

        return sorted(result, key=lambda a: a.timestamp)

    def clear(self) -> None:
        with self._lock:
            self._actions.clear()


# =============================================================================
# Incident Recorder
# =============================================================================


class IncidentRecorder:
    """Records and tracks incidents."""

    def __init__(self, audit_trail: ImmutableAuditTrail):
        self._audit_trail = audit_trail
        self._incidents: Dict[str, Incident] = {}
        self._lock = threading.Lock()

    def start_incident(
        self,
        severity: str,
        description: str,
        policy_version: str,
    ) -> Incident:
        """Start tracking an incident."""
        incident = Incident(
            incident_id=str(uuid.uuid4())[:8],
            started_at=datetime.now(tz.utc),
            resolved_at=None,
            severity=severity,
            description=description,
            root_cause=None,
            affected_requests=[],
            related_events=[],
            operator_actions=[],
            policy_versions_during=[policy_version],
        )

        with self._lock:
            self._incidents[incident.incident_id] = incident

        event = AuditEvent(
            event_id=str(uuid.uuid4())[:8],
            timestamp=incident.started_at,
            event_type=EventType.INCIDENT_STARTED,
            request_id=None,
            policy_version=policy_version,
            operator_id=None,
            decision_source=DecisionSource.SYSTEM,
            details={
                "incident_id": incident.incident_id,
                "severity": severity,
                "description": description,
            },
        )
        self._audit_trail.record(event)

        return incident

    def add_affected_request(self, incident_id: str, request_id: str) -> None:
        with self._lock:
            if incident_id in self._incidents:
                self._incidents[incident_id].affected_requests.append(request_id)

    def add_related_event(self, incident_id: str, event_id: str) -> None:
        with self._lock:
            if incident_id in self._incidents:
                self._incidents[incident_id].related_events.append(event_id)

    def add_operator_action(self, incident_id: str, action_id: str) -> None:
        with self._lock:
            if incident_id in self._incidents:
                self._incidents[incident_id].operator_actions.append(action_id)

    def add_policy_version(self, incident_id: str, version_id: str) -> None:
        with self._lock:
            if incident_id in self._incidents:
                if version_id not in self._incidents[incident_id].policy_versions_during:
                    self._incidents[incident_id].policy_versions_during.append(version_id)

    def resolve_incident(
        self,
        incident_id: str,
        root_cause: str,
        resolution_type: str,
        policy_version: str,
    ) -> Optional[Incident]:
        """Resolve an incident."""
        with self._lock:
            if incident_id not in self._incidents:
                return None

            incident = self._incidents[incident_id]
            incident.resolved_at = datetime.now(tz.utc)
            incident.root_cause = root_cause
            incident.resolution_type = resolution_type

        event = AuditEvent(
            event_id=str(uuid.uuid4())[:8],
            timestamp=incident.resolved_at,
            event_type=EventType.INCIDENT_RESOLVED,
            request_id=None,
            policy_version=policy_version,
            operator_id=None,
            decision_source=DecisionSource.SYSTEM,
            details={
                "incident_id": incident_id,
                "root_cause": root_cause,
                "resolution_type": resolution_type,
                "duration_seconds": (incident.resolved_at - incident.started_at).total_seconds(),
            },
        )
        self._audit_trail.record(event)

        return incident

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        with self._lock:
            return self._incidents.get(incident_id)

    def get_all_incidents(self) -> List[Incident]:
        with self._lock:
            return list(self._incidents.values())

    def clear(self) -> None:
        with self._lock:
            self._incidents.clear()


# =============================================================================
# SLA Tracker
# =============================================================================


class SLATracker:
    """Tracks SLA compliance with audit trail."""

    def __init__(
        self,
        audit_trail: ImmutableAuditTrail,
        target_latency_ms: float = 200.0,
        target_uptime: float = 99.9,
    ):
        self._audit_trail = audit_trail
        self._target_latency_ms = target_latency_ms
        self._target_uptime = target_uptime
        self._requests: List[Dict[str, Any]] = []
        self._breaches: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def record_request(
        self,
        request_id: str,
        latency_ms: float,
        success: bool,
        policy_version: str,
    ) -> Optional[Dict[str, Any]]:
        """Record a request and check for SLA breach."""
        with self._lock:
            self._requests.append(
                {
                    "request_id": request_id,
                    "latency_ms": latency_ms,
                    "success": success,
                    "timestamp": datetime.now(tz.utc),
                    "policy_version": policy_version,
                }
            )

            breach = None
            breach_time = datetime.now(tz.utc)
            if latency_ms > self._target_latency_ms:
                breach = {
                    "type": "latency",
                    "request_id": request_id,
                    "value": latency_ms,
                    "threshold": self._target_latency_ms,
                    "timestamp": breach_time,
                    "policy_version": policy_version,
                }
                self._breaches.append(breach)

                event = AuditEvent(
                    event_id=str(uuid.uuid4())[:8],
                    timestamp=breach_time,
                    event_type=EventType.SLA_BREACH,
                    request_id=request_id,
                    policy_version=policy_version,
                    operator_id=None,
                    decision_source=DecisionSource.SYSTEM,
                    details={
                        "type": breach["type"],
                        "value": breach["value"],
                        "threshold": breach["threshold"],
                        "timestamp": breach_time.isoformat(),
                    },
                )
                self._audit_trail.record(event)

            return breach

    def get_metrics(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> SLAMetrics:
        """Get SLA metrics for a period."""
        with self._lock:
            period_requests = [r for r in self._requests if start_time <= r["timestamp"] <= end_time]

            if not period_requests:
                return SLAMetrics(
                    timestamp=datetime.now(tz.utc),
                    period_start=start_time,
                    period_end=end_time,
                    total_requests=0,
                    successful_requests=0,
                    failed_requests=0,
                    avg_latency_ms=0.0,
                    p99_latency_ms=0.0,
                    max_latency_ms=0.0,
                    uptime_percentage=100.0,
                    sla_target_latency_ms=self._target_latency_ms,
                    sla_target_uptime=self._target_uptime,
                    breaches=[],
                )

            latencies = [r["latency_ms"] for r in period_requests]
            latencies.sort()

            successful = sum(1 for r in period_requests if r["success"])
            failed = len(period_requests) - successful

            p99_idx = int(len(latencies) * 0.99)
            p99_latency = latencies[min(p99_idx, len(latencies) - 1)]

            period_breaches = [b for b in self._breaches if start_time <= b["timestamp"] <= end_time]

            return SLAMetrics(
                timestamp=datetime.now(tz.utc),
                period_start=start_time,
                period_end=end_time,
                total_requests=len(period_requests),
                successful_requests=successful,
                failed_requests=failed,
                avg_latency_ms=sum(latencies) / len(latencies),
                p99_latency_ms=p99_latency,
                max_latency_ms=max(latencies),
                uptime_percentage=(successful / len(period_requests)) * 100,
                sla_target_latency_ms=self._target_latency_ms,
                sla_target_uptime=self._target_uptime,
                breaches=[{**b, "timestamp": b["timestamp"].isoformat()} for b in period_breaches],
            )

    def get_breaches(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._breaches)

    def clear(self) -> None:
        with self._lock:
            self._requests.clear()
            self._breaches.clear()


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def audit_trail() -> ImmutableAuditTrail:
    """Create audit trail."""
    trail = ImmutableAuditTrail()
    yield trail
    trail.clear()


@pytest.fixture
def initial_config() -> Dict[str, Any]:
    """Initial policy configuration."""
    return {
        "max_retries": 3,
        "failure_threshold": 5,
        "recovery_timeout": 30,
        "dlq_enabled": True,
        "circuit_breaker_enabled": True,
        "sla_latency_ms": 200.0,
        "sla_uptime_percent": 99.9,
    }


@pytest.fixture
def policy_manager(initial_config, audit_trail) -> AuditablePolicyManager:
    """Create policy manager."""
    return AuditablePolicyManager(initial_config, audit_trail)


@pytest.fixture
def decision_recorder(audit_trail) -> AuditableDecisionRecorder:
    """Create decision recorder."""
    recorder = AuditableDecisionRecorder(audit_trail)
    yield recorder
    recorder.clear()


@pytest.fixture
def operator_recorder(audit_trail) -> OperatorActionRecorder:
    """Create operator action recorder."""
    recorder = OperatorActionRecorder(audit_trail)
    yield recorder
    recorder.clear()


@pytest.fixture
def incident_recorder(audit_trail) -> IncidentRecorder:
    """Create incident recorder."""
    recorder = IncidentRecorder(audit_trail)
    yield recorder
    recorder.clear()


@pytest.fixture
def sla_tracker(audit_trail) -> SLATracker:
    """Create SLA tracker."""
    tracker = SLATracker(audit_trail, target_latency_ms=200.0, target_uptime=99.9)
    yield tracker
    tracker.clear()


# =============================================================================
# Test Classes - SLA Compliance
# =============================================================================


class TestSLAComplianceReportGeneration:
    """Test SLA compliance report generation."""

    def test_sla_compliance_report_generation(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        sla_tracker: SLATracker,
    ):
        """
        Verify SLA compliance report can be generated from recorded data.

        Scenario:
        - Simulate 100 requests with varying latencies
        - Generate SLA report
        - Verify report accuracy
        """
        start_time = datetime.now(tz.utc)
        policy_version = policy_manager.get_current_version().version_id

        # Simulate requests with controlled latencies
        latencies = [50, 100, 150, 180, 220, 250, 300]  # Some breach SLA
        for i in range(100):
            latency = latencies[i % len(latencies)]
            sla_tracker.record_request(
                request_id=f"req-{i:03d}",
                latency_ms=float(latency),
                success=True,
                policy_version=policy_version,
            )

        end_time = datetime.now(tz.utc)

        # Generate report
        metrics = sla_tracker.get_metrics(start_time, end_time)

        # Verify report
        assert metrics.total_requests == 100
        assert metrics.successful_requests == 100
        assert metrics.sla_target_latency_ms == 200.0

        # Count expected breaches (latencies > 200: 220, 250, 300)
        expected_breaches = sum(1 for i in range(100) if latencies[i % len(latencies)] > 200)
        assert len(metrics.breaches) == expected_breaches

        # Verify all breaches are traceable
        for breach in metrics.breaches:
            assert "request_id" in breach
            assert "value" in breach
            assert "threshold" in breach
            assert "policy_version" in breach

    def test_sla_near_breach_without_violation(
        self,
        audit_trail: ImmutableAuditTrail,
        sla_tracker: SLATracker,
        policy_manager: AuditablePolicyManager,
    ):
        """
        Verify near-breach tracking (threshold approached but not exceeded).
        """
        policy_version = policy_manager.get_current_version().version_id
        start_time = datetime.now(tz.utc)

        # All requests just under threshold
        for i in range(50):
            sla_tracker.record_request(
                request_id=f"near-{i:03d}",
                latency_ms=195.0,  # Just under 200ms threshold
                success=True,
                policy_version=policy_version,
            )

        end_time = datetime.now(tz.utc)
        metrics = sla_tracker.get_metrics(start_time, end_time)

        # No breaches should be recorded
        assert len(metrics.breaches) == 0
        assert metrics.avg_latency_ms == 195.0
        assert metrics.p99_latency_ms == 195.0


class TestSLABreachRootCauseTrace:
    """Test SLA breach root cause traceability."""

    def test_sla_breach_root_cause_trace(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        sla_tracker: SLATracker,
        incident_recorder: IncidentRecorder,
    ):
        """
        Verify SLA breach can be traced to root cause.

        Scenario:
        - Request experiences high latency due to retries
        - SLA breach is recorded
        - Root cause (retries) is traceable
        """
        policy_version = policy_manager.get_current_version().version_id
        request_id = "breach-001"

        # Simulate request with retries causing high latency
        total_latency = 0.0
        retry_count = 0

        for attempt in range(3):
            retry_count += 1
            attempt_latency = 80.0  # Each attempt takes 80ms
            total_latency += attempt_latency

            if attempt < 2:
                # Record retry
                decision_recorder.record_decision(
                    request_id=request_id,
                    policy_version=policy_version,
                    decision="retried",
                    decision_source=DecisionSource.AUTOMATED,
                    latency_ms=attempt_latency,
                    retry_count=attempt + 1,
                    details={"attempt": attempt + 1, "reason": "transient_failure"},
                )

        # Final completion
        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="completed",
            decision_source=DecisionSource.AUTOMATED,
            latency_ms=total_latency,
            retry_count=retry_count,
            details={"total_attempts": retry_count},
        )

        # Record SLA breach
        breach = sla_tracker.record_request(
            request_id=request_id,
            latency_ms=total_latency,  # 240ms > 200ms threshold
            success=True,
            policy_version=policy_version,
        )

        # Verify breach was recorded
        assert breach is not None
        assert breach["type"] == "latency"
        assert breach["value"] == 240.0

        # Trace root cause through decisions
        decisions = decision_recorder.get_decisions(request_id=request_id)

        # Verify full trace exists
        assert len(decisions) == 3  # 2 retries + 1 completion

        retry_decisions = [d for d in decisions if d.decision == "retried"]
        assert len(retry_decisions) == 2

        # Verify causal chain
        for decision in decisions:
            assert decision.policy_version == policy_version
            assert decision.request_id == request_id

        # Root cause: retries added latency
        total_retry_latency = sum(d.latency_ms for d in retry_decisions)
        assert total_retry_latency == 160.0  # 2 * 80ms


# =============================================================================
# Test Classes - Operator Accountability
# =============================================================================


class TestOperatorActionAuditReport:
    """Test operator action audit trail."""

    def test_operator_action_audit_report(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        operator_recorder: OperatorActionRecorder,
    ):
        """
        Verify all operator actions are recorded with WHO, WHEN, WHAT, WHY.
        """
        initial_version = policy_manager.get_current_version().version_id

        # Operator 1001 performs action
        action1 = operator_recorder.record_action(
            operator_id=1001,
            action_type="force_open_circuit",
            target="payment-service",
            reason="High error rate detected during deployment",
            policy_version_before=initial_version,
            policy_version_after=None,
        )

        # Operator 1002 performs action with approval
        new_version = policy_manager.update_config(
            {"max_retries": 5},
            operator_id=1002,
            reason="Increase retries for holiday traffic",
        )

        action2 = operator_recorder.record_action(
            operator_id=1002,
            action_type="update_retry_policy",
            target="global",
            reason="Increase retries for holiday traffic",
            policy_version_before=initial_version,
            policy_version_after=new_version.version_id,
            approved_by=1003,  # Supervisor approval
        )

        # Query actions
        all_actions = operator_recorder.get_actions()

        # Verify all fields are present (WHO, WHEN, WHAT, WHY)
        assert len(all_actions) == 2

        for action in all_actions:
            # WHO
            assert action.operator_id is not None
            # WHEN
            assert action.timestamp is not None
            # WHAT
            assert action.action_type is not None
            assert action.target is not None
            # WHY
            assert action.reason is not None
            assert len(action.reason) > 0

        # Verify approval chain
        assert all_actions[1].approved_by == 1003

    def test_operator_action_during_incident(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        operator_recorder: OperatorActionRecorder,
        incident_recorder: IncidentRecorder,
    ):
        """
        Verify operator actions during incident are linked.
        """
        policy_version = policy_manager.get_current_version().version_id

        # Start incident
        incident = incident_recorder.start_incident(
            severity="high",
            description="Payment service degradation",
            policy_version=policy_version,
        )

        # Operator action during incident
        action = operator_recorder.record_action(
            operator_id=1001,
            action_type="force_open_circuit",
            target="payment-service",
            reason="Manual intervention during incident " + incident.incident_id,
            policy_version_before=policy_version,
        )

        # Link action to incident
        incident_recorder.add_operator_action(incident.incident_id, action.action_id)

        # Resolve incident
        incident_recorder.resolve_incident(
            incident_id=incident.incident_id,
            root_cause="Downstream service timeout",
            resolution_type="manual",
            policy_version=policy_version,
        )

        # Verify incident has linked operator action
        resolved_incident = incident_recorder.get_incident(incident.incident_id)
        assert action.action_id in resolved_incident.operator_actions


# =============================================================================
# Test Classes - Policy Version Accountability
# =============================================================================


class TestPolicyVersionAccountability:
    """Test policy version accountability per request."""

    def test_policy_version_accountability_per_request(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
    ):
        """
        Verify every request is bound to a specific policy version.
        """
        requests_by_version: Dict[str, List[str]] = {}

        # Process requests under version 1
        v1 = policy_manager.get_current_version()
        for i in range(5):
            request_id = f"v1-req-{i}"
            decision_recorder.record_decision(
                request_id=request_id,
                policy_version=v1.version_id,
                decision="completed",
                decision_source=DecisionSource.AUTOMATED,
            )
            requests_by_version.setdefault(v1.version_id, []).append(request_id)

        # Update policy
        v2 = policy_manager.update_config(
            {"max_retries": 5},
            operator_id=1001,
            reason="Testing",
        )

        # Process requests under version 2
        for i in range(5):
            request_id = f"v2-req-{i}"
            decision_recorder.record_decision(
                request_id=request_id,
                policy_version=v2.version_id,
                decision="completed",
                decision_source=DecisionSource.AUTOMATED,
            )
            requests_by_version.setdefault(v2.version_id, []).append(request_id)

        # Verify accountability
        all_decisions = decision_recorder.get_decisions()

        for decision in all_decisions:
            # Every decision must have policy version
            assert decision.policy_version is not None
            assert decision.policy_version in [v1.version_id, v2.version_id]

        # Verify v1 requests
        v1_decisions = decision_recorder.get_decisions(policy_version=v1.version_id)
        assert len(v1_decisions) == 5
        for d in v1_decisions:
            assert d.request_id.startswith("v1-")

        # Verify v2 requests
        v2_decisions = decision_recorder.get_decisions(policy_version=v2.version_id)
        assert len(v2_decisions) == 5
        for d in v2_decisions:
            assert d.request_id.startswith("v2-")

    def test_policy_change_during_request_binding(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
    ):
        """
        Verify in-flight request maintains original policy binding.
        """
        # Capture policy at request start
        v1 = policy_manager.capture_snapshot()
        request_id = "inflight-001"

        # Simulate in-flight processing start
        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=v1.version_id,
            decision="processing_started",
            decision_source=DecisionSource.AUTOMATED,
        )

        # Policy changes while request is in-flight
        v2 = policy_manager.update_config(
            {"max_retries": 10},
            operator_id=1001,
        )

        # Request completes with ORIGINAL policy
        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=v1.version_id,  # Still v1!
            decision="completed",
            decision_source=DecisionSource.AUTOMATED,
            details={"bound_to_original_policy": True},
        )

        # Verify all decisions for this request use v1
        request_decisions = decision_recorder.get_decisions(request_id=request_id)
        for decision in request_decisions:
            assert decision.policy_version == v1.version_id
            assert decision.policy_version != v2.version_id


# =============================================================================
# Test Classes - Evidence Immutability
# =============================================================================


class TestEvidenceImmutability:
    """Test that evidence is immutable once recorded."""

    def test_evidence_is_immutable_once_recorded(
        self,
        audit_trail: ImmutableAuditTrail,
    ):
        """
        Verify audit events cannot be modified after recording.
        """
        # Record an event
        event = AuditEvent(
            event_id="immutable-001",
            timestamp=datetime.now(tz.utc),
            event_type=EventType.REQUEST_COMPLETED,
            request_id="req-001",
            policy_version="v1",
            operator_id=None,
            decision_source=DecisionSource.AUTOMATED,
            details={"test": "data"},
        )

        original_hash = audit_trail.record(event)

        # Verify event is recorded
        retrieved = audit_trail.get_event_by_id("immutable-001")
        assert retrieved is not None
        assert retrieved.event_id == event.event_id

        # Verify hash is stable
        assert retrieved.compute_hash() == original_hash

        # Verify integrity check passes
        is_valid, issues = audit_trail.verify_integrity()
        assert is_valid
        assert len(issues) == 0

    def test_sealed_audit_trail_rejects_new_events(
        self,
        audit_trail: ImmutableAuditTrail,
    ):
        """
        Verify sealed audit trail cannot accept new events.
        """
        # Record some events
        for i in range(3):
            event = AuditEvent(
                event_id=f"seal-test-{i}",
                timestamp=datetime.now(tz.utc),
                event_type=EventType.REQUEST_COMPLETED,
                request_id=f"req-{i}",
                policy_version="v1",
                operator_id=None,
                decision_source=DecisionSource.AUTOMATED,
                details={},
            )
            audit_trail.record(event)

        # Seal the trail
        audit_trail.seal()

        # Attempt to add new event should fail
        new_event = AuditEvent(
            event_id="post-seal",
            timestamp=datetime.now(tz.utc),
            event_type=EventType.REQUEST_COMPLETED,
            request_id="req-new",
            policy_version="v1",
            operator_id=None,
            decision_source=DecisionSource.AUTOMATED,
            details={},
        )

        with pytest.raises(RuntimeError, match="sealed"):
            audit_trail.record(new_event)

    def test_no_duplicate_events_allowed(
        self,
        audit_trail: ImmutableAuditTrail,
    ):
        """
        Verify duplicate events are rejected.
        """
        timestamp = datetime.now(tz.utc)

        event = AuditEvent(
            event_id="dup-001",
            timestamp=timestamp,
            event_type=EventType.REQUEST_COMPLETED,
            request_id="req-001",
            policy_version="v1",
            operator_id=None,
            decision_source=DecisionSource.AUTOMATED,
            details={"key": "value"},
        )

        # First record succeeds
        audit_trail.record(event)

        # Duplicate should fail
        with pytest.raises(ValueError, match="Duplicate"):
            audit_trail.record(event)


# =============================================================================
# Test Classes - No Implicit Decisions
# =============================================================================


class TestNoImplicitDecisions:
    """Test that no implicit decisions exist."""

    def test_no_implicit_decisions_exist(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
    ):
        """
        Verify every system decision is explicitly recorded.
        """
        policy_version = policy_manager.get_current_version().version_id

        # Simulate request lifecycle with ALL decisions recorded
        request_id = "explicit-001"

        # Decision 1: Request received
        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="received",
            decision_source=DecisionSource.SYSTEM,
            justification="Request accepted for processing",
        )

        # Decision 2: Policy evaluated
        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="policy_evaluated",
            decision_source=DecisionSource.POLICY,
            justification="Retry policy allows 3 attempts",
            details={"max_retries": 3, "dlq_enabled": True},
        )

        # Decision 3: Processing attempted
        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="attempt_1_failed",
            decision_source=DecisionSource.AUTOMATED,
            justification="Transient error, will retry",
            retry_count=1,
        )

        # Decision 4: Retry decision
        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="retry_scheduled",
            decision_source=DecisionSource.POLICY,
            justification="Retry allowed: attempt 1 < max 3",
            details={"current_attempt": 1, "max_retries": 3},
        )

        # Decision 5: Final completion
        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="completed",
            decision_source=DecisionSource.AUTOMATED,
            justification="Request completed successfully on attempt 2",
            retry_count=2,
        )

        # Verify all decisions are explicit
        decisions = decision_recorder.get_decisions(request_id=request_id)

        assert len(decisions) == 5

        for decision in decisions:
            # Every decision must have explicit source
            assert decision.decision_source in DecisionSource.__members__.values()
            # Every decision must have justification
            assert decision.justification is not None
            assert len(decision.justification) > 0
            # Every decision must have policy version
            assert decision.policy_version == policy_version

    def test_all_decisions_have_justification(
        self,
        audit_trail: ImmutableAuditTrail,
        decision_recorder: AuditableDecisionRecorder,
        policy_manager: AuditablePolicyManager,
    ):
        """
        Verify all decision types have proper justification.
        """
        policy_version = policy_manager.get_current_version().version_id
        decision_types = [
            ("completed", "Request processed successfully"),
            ("failed", "Max retries exhausted"),
            ("retried", "Transient error detected"),
            ("dlq", "Sent to DLQ for manual review"),
            ("rejected", "Request rejected by circuit breaker"),
        ]

        for i, (decision_type, justification) in enumerate(decision_types):
            decision_recorder.record_decision(
                request_id=f"just-{i}",
                policy_version=policy_version,
                decision=decision_type,
                decision_source=DecisionSource.AUTOMATED,
                justification=justification,
            )

        decisions = decision_recorder.get_decisions()

        for decision in decisions:
            assert decision.justification is not None
            assert len(decision.justification) > 0


# =============================================================================
# Main Entry Point
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
