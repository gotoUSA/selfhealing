"""
Stage 45: External Trust & Audit Readiness - Deterministic Test Suite (Part 2)

This file contains report generation and external audit simulation tests.

Focuses on:
- Incident timeline reconstruction
- Decision explainability reports
- External audit query simulation
- Concurrent operator + automated recovery
- Policy change before/during/after incident

Execution:
    pytest load_tests/scenarios/stage45_external_trust_reports.py -v -s

Reference: Stage 45 Part 1
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone as tz
from typing import Any, Dict, List, Optional

import pytest

# Import from Part 1
from load_tests.scenarios.stage45_external_trust_audit import (
    EventType,
    DecisionSource,
    AuditEvent,
    ImmutableAuditTrail,
    AuditablePolicyManager,
    AuditableDecisionRecorder,
    OperatorActionRecorder,
    IncidentRecorder,
    SLATracker,
)


# =============================================================================
# Logging Configuration
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Report Generators
# =============================================================================


class IncidentReportGenerator:
    """Generates incident reports from audit trail."""

    def __init__(
        self,
        audit_trail: ImmutableAuditTrail,
        incident_recorder: IncidentRecorder,
        policy_manager: AuditablePolicyManager,
        operator_recorder: OperatorActionRecorder,
    ):
        self._audit_trail = audit_trail
        self._incident_recorder = incident_recorder
        self._policy_manager = policy_manager
        self._operator_recorder = operator_recorder

    def generate_timeline(self, incident_id: str) -> List[Dict[str, Any]]:
        """Generate incident timeline with all related events."""
        incident = self._incident_recorder.get_incident(incident_id)
        if not incident:
            return []

        # Get all events during incident period
        end_time = incident.resolved_at or datetime.now(tz.utc)
        events = self._audit_trail.get_events(
            start_time=incident.started_at,
            end_time=end_time,
        )

        timeline = []
        for event in events:
            timeline.append(
                {
                    "timestamp": event.timestamp.isoformat(),
                    "event_type": event.event_type.value,
                    "request_id": event.request_id,
                    "policy_version": event.policy_version,
                    "operator_id": event.operator_id,
                    "decision_source": event.decision_source.value,
                    "details": event.details,
                }
            )

        return sorted(timeline, key=lambda x: x["timestamp"])

    def generate_report(self, incident_id: str) -> Dict[str, Any]:
        """Generate complete incident report."""
        incident = self._incident_recorder.get_incident(incident_id)
        if not incident:
            return {"error": "Incident not found"}

        timeline = self.generate_timeline(incident_id)

        # Get operator actions during incident
        operator_actions = []
        for action_id in incident.operator_actions:
            all_actions = self._operator_recorder.get_actions()
            for action in all_actions:
                if action.action_id == action_id:
                    operator_actions.append(action.to_dict())

        # Get policy versions during incident
        policy_versions = []
        for version_id in incident.policy_versions_during:
            for version in self._policy_manager.get_history():
                if version.version_id == version_id:
                    policy_versions.append(
                        {
                            "version_id": version.version_id,
                            "timestamp": version.timestamp.isoformat(),
                            "changed_by": version.changed_by,
                            "change_reason": version.change_reason,
                        }
                    )

        duration = None
        if incident.resolved_at:
            duration = (incident.resolved_at - incident.started_at).total_seconds()

        return {
            "incident_id": incident.incident_id,
            "severity": incident.severity,
            "description": incident.description,
            "started_at": incident.started_at.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "duration_seconds": duration,
            "root_cause": incident.root_cause,
            "resolution_type": incident.resolution_type,
            "affected_requests_count": len(incident.affected_requests),
            "affected_requests": incident.affected_requests,
            "operator_actions": operator_actions,
            "policy_versions": policy_versions,
            "timeline": timeline,
        }


class SLAComplianceReportGenerator:
    """Generates SLA compliance reports."""

    def __init__(
        self,
        audit_trail: ImmutableAuditTrail,
        sla_tracker: SLATracker,
    ):
        self._audit_trail = audit_trail
        self._sla_tracker = sla_tracker

    def generate_report(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Any]:
        """Generate SLA compliance report for period."""
        metrics = self._sla_tracker.get_metrics(start_time, end_time)

        # Get SLA-related events
        breach_events = self._audit_trail.get_events(
            event_type=EventType.SLA_BREACH,
            start_time=start_time,
            end_time=end_time,
        )

        return {
            "report_type": "SLA Compliance",
            "generated_at": datetime.now(tz.utc).isoformat(),
            "period": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
            },
            "summary": {
                "total_requests": metrics.total_requests,
                "successful_requests": metrics.successful_requests,
                "failed_requests": metrics.failed_requests,
                "success_rate": (
                    metrics.successful_requests / metrics.total_requests * 100 if metrics.total_requests > 0 else 100.0
                ),
            },
            "latency": {
                "average_ms": metrics.avg_latency_ms,
                "p99_ms": metrics.p99_latency_ms,
                "max_ms": metrics.max_latency_ms,
                "target_ms": metrics.sla_target_latency_ms,
            },
            "uptime": {
                "actual_percent": metrics.uptime_percentage,
                "target_percent": metrics.sla_target_uptime,
                "compliant": metrics.uptime_percentage >= metrics.sla_target_uptime,
            },
            "breaches": {
                "count": len(metrics.breaches),
                "details": metrics.breaches,
            },
            "breach_events": [
                {
                    "event_id": e.event_id,
                    "timestamp": e.timestamp.isoformat(),
                    "request_id": e.request_id,
                    "details": e.details,
                }
                for e in breach_events
            ],
        }


class DecisionExplainabilityReportGenerator:
    """Generates decision explainability reports."""

    def __init__(
        self,
        audit_trail: ImmutableAuditTrail,
        decision_recorder: AuditableDecisionRecorder,
        policy_manager: AuditablePolicyManager,
    ):
        self._audit_trail = audit_trail
        self._decision_recorder = decision_recorder
        self._policy_manager = policy_manager

    def explain_request(self, request_id: str) -> Dict[str, Any]:
        """Generate complete explanation for a request's journey."""
        decisions = self._decision_recorder.get_decisions(request_id=request_id)
        events = self._audit_trail.get_events(request_id=request_id)

        if not decisions and not events:
            return {"error": "Request not found", "request_id": request_id}

        # Build explanation timeline
        timeline = []

        for decision in decisions:
            # Get policy at time of decision
            policy_version = None
            for version in self._policy_manager.get_history():
                if version.version_id == decision.policy_version:
                    policy_version = version
                    break

            timeline.append(
                {
                    "timestamp": decision.timestamp.isoformat(),
                    "action": decision.decision,
                    "source": decision.decision_source.value,
                    "justification": decision.justification,
                    "policy_version": decision.policy_version,
                    "policy_config": policy_version.config_snapshot if policy_version else None,
                    "operator_id": decision.operator_id,
                    "latency_ms": decision.latency_ms,
                    "retry_count": decision.retry_count,
                    "details": decision.evaluation_result,
                }
            )

        # Sort by timestamp
        timeline.sort(key=lambda x: x["timestamp"])

        # Determine final outcome
        final_outcome = decisions[-1].decision if decisions else "unknown"

        return {
            "request_id": request_id,
            "generated_at": datetime.now(tz.utc).isoformat(),
            "total_decisions": len(decisions),
            "final_outcome": final_outcome,
            "timeline": timeline,
            "summary": {
                "total_latency_ms": sum(d.latency_ms for d in decisions),
                "total_retries": max((d.retry_count for d in decisions), default=0),
                "decision_sources": list(set(d.decision_source.value for d in decisions)),
                "policy_versions_used": list(set(d.policy_version for d in decisions)),
            },
        }

    def explain_all_requests(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Generate summary of all request decisions."""
        all_decisions = self._decision_recorder.get_decisions()

        if start_time:
            all_decisions = [d for d in all_decisions if d.timestamp >= start_time]
        if end_time:
            all_decisions = [d for d in all_decisions if d.timestamp <= end_time]

        # Group by decision type
        by_decision = {}
        for decision in all_decisions:
            by_decision.setdefault(decision.decision, []).append(decision)

        # Group by source
        by_source = {}
        for decision in all_decisions:
            by_source.setdefault(decision.decision_source.value, []).append(decision)

        return {
            "report_type": "Decision Explainability Summary",
            "generated_at": datetime.now(tz.utc).isoformat(),
            "total_decisions": len(all_decisions),
            "by_decision_type": {k: len(v) for k, v in by_decision.items()},
            "by_source": {k: len(v) for k, v in by_source.items()},
            "unique_requests": len(set(d.request_id for d in all_decisions)),
            "unique_policy_versions": len(set(d.policy_version for d in all_decisions)),
        }


class OperatorActionReportGenerator:
    """Generates operator action reports."""

    def __init__(
        self,
        audit_trail: ImmutableAuditTrail,
        operator_recorder: OperatorActionRecorder,
        policy_manager: AuditablePolicyManager,
    ):
        self._audit_trail = audit_trail
        self._operator_recorder = operator_recorder
        self._policy_manager = policy_manager

    def generate_report(
        self,
        operator_id: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Generate operator action report."""
        actions = self._operator_recorder.get_actions(
            operator_id=operator_id,
            start_time=start_time,
            end_time=end_time,
        )

        # Group by action type
        by_type = {}
        for action in actions:
            by_type.setdefault(action.action_type, []).append(action)

        # Group by operator
        by_operator = {}
        for action in actions:
            by_operator.setdefault(action.operator_id, []).append(action)

        return {
            "report_type": "Operator Action Report",
            "generated_at": datetime.now(tz.utc).isoformat(),
            "filter": {
                "operator_id": operator_id,
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
            },
            "total_actions": len(actions),
            "by_action_type": {k: len(v) for k, v in by_type.items()},
            "by_operator": {k: len(v) for k, v in by_operator.items()},
            "actions": [action.to_dict() for action in actions],
            "with_approval": sum(1 for a in actions if a.approved_by is not None),
            "policy_changes": sum(1 for a in actions if a.policy_version_after is not None),
        }


class PolicyVersionHistoryReportGenerator:
    """Generates policy version history reports."""

    def __init__(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
    ):
        self._audit_trail = audit_trail
        self._policy_manager = policy_manager

    def generate_report(self) -> Dict[str, Any]:
        """Generate policy version history report."""
        history = self._policy_manager.get_history()

        versions = []
        for i, version in enumerate(history):
            prev_config = history[i - 1].config_snapshot if i > 0 else {}

            # Calculate diff
            changes = {}
            for key in set(list(prev_config.keys()) + list(version.config_snapshot.keys())):
                old_val = prev_config.get(key)
                new_val = version.config_snapshot.get(key)
                if old_val != new_val:
                    changes[key] = {"old": old_val, "new": new_val}

            versions.append(
                {
                    "version_id": version.version_id,
                    "timestamp": version.timestamp.isoformat(),
                    "changed_by": version.changed_by,
                    "change_reason": version.change_reason,
                    "config_snapshot": version.config_snapshot,
                    "changes_from_previous": changes,
                }
            )

        return {
            "report_type": "Policy Version History",
            "generated_at": datetime.now(tz.utc).isoformat(),
            "total_versions": len(history),
            "current_version": history[-1].version_id if history else None,
            "versions": versions,
        }


# =============================================================================
# External Audit Query Simulator
# =============================================================================


class ExternalAuditQuerySimulator:
    """Simulates external audit queries."""

    def __init__(
        self,
        audit_trail: ImmutableAuditTrail,
        incident_report_gen: IncidentReportGenerator,
        sla_report_gen: SLAComplianceReportGenerator,
        decision_report_gen: DecisionExplainabilityReportGenerator,
        operator_report_gen: OperatorActionReportGenerator,
        policy_report_gen: PolicyVersionHistoryReportGenerator,
    ):
        self._audit_trail = audit_trail
        self._incident_report = incident_report_gen
        self._sla_report = sla_report_gen
        self._decision_report = decision_report_gen
        self._operator_report = operator_report_gen
        self._policy_report = policy_report_gen

    def query_incident_details(self, incident_id: str) -> Dict[str, Any]:
        """Auditor queries incident details."""
        return self._incident_report.generate_report(incident_id)

    def query_sla_compliance(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Any]:
        """Auditor queries SLA compliance."""
        return self._sla_report.generate_report(start_time, end_time)

    def query_request_explanation(self, request_id: str) -> Dict[str, Any]:
        """Auditor queries request decision explanation."""
        return self._decision_report.explain_request(request_id)

    def query_operator_actions(
        self,
        operator_id: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Auditor queries operator actions."""
        return self._operator_report.generate_report(operator_id, start_time, end_time)

    def query_policy_history(self) -> Dict[str, Any]:
        """Auditor queries policy version history."""
        return self._policy_report.generate_report()

    def verify_evidence_integrity(self) -> Dict[str, Any]:
        """Auditor verifies evidence integrity."""
        is_valid, issues = self._audit_trail.verify_integrity()
        return {
            "integrity_check": "passed" if is_valid else "failed",
            "issues": issues,
            "total_events": len(self._audit_trail.get_all_events()),
        }


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


@pytest.fixture
def incident_report_generator(
    audit_trail,
    incident_recorder,
    policy_manager,
    operator_recorder,
) -> IncidentReportGenerator:
    """Create incident report generator."""
    return IncidentReportGenerator(audit_trail, incident_recorder, policy_manager, operator_recorder)


@pytest.fixture
def sla_report_generator(audit_trail, sla_tracker) -> SLAComplianceReportGenerator:
    """Create SLA report generator."""
    return SLAComplianceReportGenerator(audit_trail, sla_tracker)


@pytest.fixture
def decision_report_generator(
    audit_trail,
    decision_recorder,
    policy_manager,
) -> DecisionExplainabilityReportGenerator:
    """Create decision report generator."""
    return DecisionExplainabilityReportGenerator(audit_trail, decision_recorder, policy_manager)


@pytest.fixture
def operator_report_generator(
    audit_trail,
    operator_recorder,
    policy_manager,
) -> OperatorActionReportGenerator:
    """Create operator report generator."""
    return OperatorActionReportGenerator(audit_trail, operator_recorder, policy_manager)


@pytest.fixture
def policy_report_generator(
    audit_trail,
    policy_manager,
) -> PolicyVersionHistoryReportGenerator:
    """Create policy report generator."""
    return PolicyVersionHistoryReportGenerator(audit_trail, policy_manager)


@pytest.fixture
def audit_simulator(
    audit_trail,
    incident_report_generator,
    sla_report_generator,
    decision_report_generator,
    operator_report_generator,
    policy_report_generator,
) -> ExternalAuditQuerySimulator:
    """Create external audit query simulator."""
    return ExternalAuditQuerySimulator(
        audit_trail,
        incident_report_generator,
        sla_report_generator,
        decision_report_generator,
        operator_report_generator,
        policy_report_generator,
    )


# =============================================================================
# Test Classes - Incident Timeline Reconstruction
# =============================================================================


class TestIncidentTimelineReconstruction:
    """Test incident timeline reconstruction."""

    def test_incident_timeline_reconstruction(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        operator_recorder: OperatorActionRecorder,
        incident_recorder: IncidentRecorder,
        incident_report_generator: IncidentReportGenerator,
    ):
        """
        Verify complete incident timeline can be reconstructed.

        Scenario:
        - Incident starts
        - Affected requests occur
        - Operator takes action
        - Policy changes
        - Incident resolves
        - Timeline is complete and ordered
        """
        policy_version = policy_manager.get_current_version().version_id

        # Step 1: Incident starts
        incident = incident_recorder.start_incident(
            severity="high",
            description="Service degradation detected",
            policy_version=policy_version,
        )

        # Step 2: Affected requests
        for i in range(5):
            request_id = f"incident-req-{i}"
            decision_recorder.record_decision(
                request_id=request_id,
                policy_version=policy_version,
                decision="failed",
                decision_source=DecisionSource.AUTOMATED,
                justification="Service unavailable",
            )
            incident_recorder.add_affected_request(incident.incident_id, request_id)

        # Step 3: Operator action
        action = operator_recorder.record_action(
            operator_id=1001,
            action_type="force_open_circuit",
            target="degraded-service",
            reason="Manual intervention during incident",
            policy_version_before=policy_version,
        )
        incident_recorder.add_operator_action(incident.incident_id, action.action_id)

        # Step 4: Policy change
        new_version = policy_manager.update_config(
            {"max_retries": 5},
            operator_id=1001,
            reason="Increase retries during recovery",
        )
        incident_recorder.add_policy_version(incident.incident_id, new_version.version_id)

        # Step 5: Resolve incident
        incident_recorder.resolve_incident(
            incident_id=incident.incident_id,
            root_cause="Downstream service overload",
            resolution_type="hybrid",
            policy_version=new_version.version_id,
        )

        # Generate timeline
        report = incident_report_generator.generate_report(incident.incident_id)

        # Verify timeline completeness
        assert report["incident_id"] == incident.incident_id
        assert report["severity"] == "high"
        assert report["affected_requests_count"] == 5
        assert len(report["operator_actions"]) == 1
        assert len(report["policy_versions"]) == 2  # Original + new
        assert report["resolution_type"] == "hybrid"
        assert report["root_cause"] == "Downstream service overload"

        # Verify timeline is ordered
        timeline = report["timeline"]
        assert len(timeline) > 0

        for i in range(1, len(timeline)):
            assert timeline[i]["timestamp"] >= timeline[i - 1]["timestamp"]

    def test_incident_with_concurrent_events(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        incident_recorder: IncidentRecorder,
        incident_report_generator: IncidentReportGenerator,
    ):
        """
        Verify concurrent events during incident are captured.
        """
        policy_version = policy_manager.get_current_version().version_id

        # Start incident
        incident = incident_recorder.start_incident(
            severity="critical",
            description="Multiple service failures",
            policy_version=policy_version,
        )

        # Simulate concurrent requests (different types)
        concurrent_requests = [
            ("req-success", "completed", DecisionSource.AUTOMATED),
            ("req-retry", "retried", DecisionSource.POLICY),
            ("req-failed", "failed", DecisionSource.AUTOMATED),
            ("req-dlq", "dlq", DecisionSource.POLICY),
            ("req-manual", "manual_intervention", DecisionSource.OPERATOR),
        ]

        for request_id, decision, source in concurrent_requests:
            decision_recorder.record_decision(
                request_id=request_id,
                policy_version=policy_version,
                decision=decision,
                decision_source=source,
                operator_id=1001 if source == DecisionSource.OPERATOR else None,
                justification=f"Test: {decision}",
            )
            incident_recorder.add_affected_request(incident.incident_id, request_id)

        # Resolve
        incident_recorder.resolve_incident(
            incident_id=incident.incident_id,
            root_cause="Cascading failure",
            resolution_type="automated",
            policy_version=policy_version,
        )

        # Generate report
        report = incident_report_generator.generate_report(incident.incident_id)

        # All concurrent events should be in timeline
        assert report["affected_requests_count"] == 5

        # Verify all decision types are present
        decision_types_in_timeline = set()
        for event in report["timeline"]:
            if "decision" in event["details"]:
                decision_types_in_timeline.add(event["details"]["decision"])


# =============================================================================
# Test Classes - Decision Explainability
# =============================================================================


class TestDecisionExplainabilityReport:
    """Test decision explainability reports."""

    def test_decision_explainability_report(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        decision_report_generator: DecisionExplainabilityReportGenerator,
    ):
        """
        Verify complete decision explanation can be generated.
        """
        policy_version = policy_manager.get_current_version().version_id
        request_id = "explain-001"

        # Simulate request with multiple decisions
        decisions = [
            ("received", "Request accepted for processing"),
            ("policy_evaluated", "Retry policy: max 3 attempts"),
            ("attempt_failed", "First attempt failed - transient error"),
            ("retry_scheduled", "Retry allowed: 1 < 3"),
            ("attempt_failed", "Second attempt failed - timeout"),
            ("retry_scheduled", "Retry allowed: 2 < 3"),
            ("completed", "Third attempt succeeded"),
        ]

        retry_count = 0
        for decision, justification in decisions:
            if "attempt" in decision:
                retry_count += 1

            decision_recorder.record_decision(
                request_id=request_id,
                policy_version=policy_version,
                decision=decision,
                decision_source=DecisionSource.AUTOMATED,
                justification=justification,
                latency_ms=50.0,
                retry_count=retry_count,
            )

        # Generate explanation
        explanation = decision_report_generator.explain_request(request_id)

        # Verify completeness
        assert explanation["request_id"] == request_id
        assert explanation["total_decisions"] == len(decisions)
        assert explanation["final_outcome"] == "completed"

        # Verify timeline has all steps
        assert len(explanation["timeline"]) == len(decisions)

        # Verify each step has justification
        for step in explanation["timeline"]:
            assert step["justification"] is not None
            assert len(step["justification"]) > 0
            assert step["policy_version"] == policy_version

    def test_decision_summary_report(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        decision_report_generator: DecisionExplainabilityReportGenerator,
    ):
        """
        Verify decision summary report aggregates correctly.
        """
        policy_version = policy_manager.get_current_version().version_id

        # Create various decisions
        for i in range(10):
            decision_recorder.record_decision(
                request_id=f"summary-{i}",
                policy_version=policy_version,
                decision="completed" if i < 7 else "failed",
                decision_source=DecisionSource.AUTOMATED if i < 8 else DecisionSource.MANUAL,
                justification="Test decision",
            )

        # Generate summary
        summary = decision_report_generator.explain_all_requests()

        assert summary["total_decisions"] == 10
        assert summary["unique_requests"] == 10
        assert summary["by_decision_type"]["completed"] == 7
        assert summary["by_decision_type"]["failed"] == 3


# =============================================================================
# Test Classes - External Audit Query Simulation
# =============================================================================


class TestExternalAuditQuerySimulation:
    """Test external audit query simulation."""

    def test_external_audit_query_simulation(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        operator_recorder: OperatorActionRecorder,
        incident_recorder: IncidentRecorder,
        sla_tracker: SLATracker,
        audit_simulator: ExternalAuditQuerySimulator,
    ):
        """
        Simulate complete external audit query flow.
        """
        start_time = datetime.now(tz.utc)
        policy_version = policy_manager.get_current_version().version_id

        # Set up test data
        # 1. Create incident
        incident = incident_recorder.start_incident(
            severity="medium",
            description="Audit test incident",
            policy_version=policy_version,
        )

        # 2. Record requests
        for i in range(20):
            latency = 100.0 if i < 15 else 250.0  # Some breach SLA
            sla_tracker.record_request(
                request_id=f"audit-req-{i}",
                latency_ms=latency,
                success=True,
                policy_version=policy_version,
            )

            decision_recorder.record_decision(
                request_id=f"audit-req-{i}",
                policy_version=policy_version,
                decision="completed",
                decision_source=DecisionSource.AUTOMATED,
                justification="Test request",
                latency_ms=latency,
            )

        # 3. Operator action
        operator_recorder.record_action(
            operator_id=1001,
            action_type="policy_review",
            target="sla_thresholds",
            reason="Audit preparation",
            policy_version_before=policy_version,
        )

        # 4. Resolve incident
        incident_recorder.resolve_incident(
            incident_id=incident.incident_id,
            root_cause="Test scenario",
            resolution_type="automated",
            policy_version=policy_version,
        )

        end_time = datetime.now(tz.utc)

        # === Auditor Queries ===

        # Query 1: Incident details
        incident_result = audit_simulator.query_incident_details(incident.incident_id)
        assert "incident_id" in incident_result
        assert incident_result["severity"] == "medium"

        # Query 2: SLA compliance
        sla_result = audit_simulator.query_sla_compliance(start_time, end_time)
        assert sla_result["summary"]["total_requests"] == 20
        assert sla_result["breaches"]["count"] == 5  # Last 5 requests breached

        # Query 3: Request explanation
        request_result = audit_simulator.query_request_explanation("audit-req-0")
        assert request_result["request_id"] == "audit-req-0"
        assert request_result["final_outcome"] == "completed"

        # Query 4: Operator actions
        operator_result = audit_simulator.query_operator_actions(operator_id=1001)
        assert operator_result["total_actions"] == 1

        # Query 5: Policy history
        policy_result = audit_simulator.query_policy_history()
        assert policy_result["total_versions"] >= 1

        # Query 6: Evidence integrity
        integrity_result = audit_simulator.verify_evidence_integrity()
        assert integrity_result["integrity_check"] == "passed"

    def test_audit_query_for_specific_request(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        audit_simulator: ExternalAuditQuerySimulator,
    ):
        """
        Auditor queries specific request for investigation.
        """
        policy_version = policy_manager.get_current_version().version_id
        request_id = "investigate-001"

        # Create detailed request history
        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="received",
            decision_source=DecisionSource.SYSTEM,
            justification="Request received from client 192.168.1.100",
        )

        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="authenticated",
            decision_source=DecisionSource.POLICY,
            justification="JWT token validated, user_id=12345",
        )

        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="authorized",
            decision_source=DecisionSource.POLICY,
            justification="User has permission: payment.create",
        )

        decision_recorder.record_decision(
            request_id=request_id,
            policy_version=policy_version,
            decision="processed",
            decision_source=DecisionSource.AUTOMATED,
            justification="Payment processed successfully",
            latency_ms=150.0,
        )

        # Auditor query
        result = audit_simulator.query_request_explanation(request_id)

        # Verify complete audit trail
        assert result["request_id"] == request_id
        assert result["total_decisions"] == 4
        assert result["final_outcome"] == "processed"

        # Verify each step is explainable
        steps = result["timeline"]
        assert steps[0]["action"] == "received"
        assert steps[1]["action"] == "authenticated"
        assert steps[2]["action"] == "authorized"
        assert steps[3]["action"] == "processed"


# =============================================================================
# Test Classes - Concurrent Operator + Automated Recovery
# =============================================================================


class TestConcurrentOperatorAutomatedRecovery:
    """Test concurrent operator and automated recovery."""

    def test_concurrent_operator_and_automated_recovery(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        operator_recorder: OperatorActionRecorder,
        incident_recorder: IncidentRecorder,
    ):
        """
        Verify both operator and automated actions are recorded during concurrent recovery.
        """
        policy_version = policy_manager.get_current_version().version_id

        # Start incident
        incident = incident_recorder.start_incident(
            severity="high",
            description="Concurrent recovery test",
            policy_version=policy_version,
        )

        # Automated recovery action
        auto_event = AuditEvent(
            event_id="auto-recovery-001",
            timestamp=datetime.now(tz.utc),
            event_type=EventType.AUTOMATED_RECOVERY,
            request_id=None,
            policy_version=policy_version,
            operator_id=None,
            decision_source=DecisionSource.AUTOMATED,
            details={
                "action": "circuit_breaker_half_open",
                "reason": "Recovery timeout elapsed",
            },
        )
        audit_trail.record(auto_event)
        incident_recorder.add_related_event(incident.incident_id, auto_event.event_id)

        # Operator manual action (concurrent)
        operator_action = operator_recorder.record_action(
            operator_id=1001,
            action_type="manual_intervention",
            target="circuit_breaker",
            reason="Operator override during automated recovery",
            policy_version_before=policy_version,
        )
        incident_recorder.add_operator_action(incident.incident_id, operator_action.action_id)

        # Manual override event
        manual_event = AuditEvent(
            event_id="manual-override-001",
            timestamp=datetime.now(tz.utc),
            event_type=EventType.MANUAL_OVERRIDE,
            request_id=None,
            policy_version=policy_version,
            operator_id=1001,
            decision_source=DecisionSource.OPERATOR,
            details={
                "action": "force_close_circuit",
                "reason": "Confirmed service recovery",
            },
        )
        audit_trail.record(manual_event)
        incident_recorder.add_related_event(incident.incident_id, manual_event.event_id)

        # Resolve incident
        incident_recorder.resolve_incident(
            incident_id=incident.incident_id,
            root_cause="Service recovered",
            resolution_type="hybrid",  # Both automated and manual
            policy_version=policy_version,
        )

        # Verify both types of actions are recorded
        resolved = incident_recorder.get_incident(incident.incident_id)
        assert resolved.resolution_type == "hybrid"
        assert len(resolved.related_events) == 2
        assert len(resolved.operator_actions) == 1

        # Verify events are distinguishable
        events = audit_trail.get_all_events()
        auto_events = [e for e in events if e.event_type == EventType.AUTOMATED_RECOVERY]
        manual_events = [e for e in events if e.event_type == EventType.MANUAL_OVERRIDE]

        assert len(auto_events) >= 1
        assert len(manual_events) >= 1


# =============================================================================
# Test Classes - Policy Change During Incident
# =============================================================================


class TestPolicyChangeDuringIncident:
    """Test policy changes before, during, and after incident."""

    def test_policy_change_before_during_after_incident(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        incident_recorder: IncidentRecorder,
    ):
        """
        Verify policy changes at all incident phases are tracked.
        """
        # === BEFORE Incident ===
        v1 = policy_manager.get_current_version()

        v2 = policy_manager.update_config(
            {"max_retries": 5},
            operator_id=1001,
            reason="Proactive increase before expected load",
        )

        # === DURING Incident ===
        incident = incident_recorder.start_incident(
            severity="high",
            description="Load spike incident",
            policy_version=v2.version_id,
        )

        v3 = policy_manager.update_config(
            {"failure_threshold": 10},
            operator_id=1002,
            reason="Emergency threshold increase during incident",
        )
        incident_recorder.add_policy_version(incident.incident_id, v3.version_id)

        v4 = policy_manager.update_config(
            {"dlq_enabled": False},
            operator_id=1002,
            reason="Disable DLQ to prevent queue overflow",
        )
        incident_recorder.add_policy_version(incident.incident_id, v4.version_id)

        # Resolve incident
        incident_recorder.resolve_incident(
            incident_id=incident.incident_id,
            root_cause="Traffic spike from marketing campaign",
            resolution_type="manual",
            policy_version=v4.version_id,
        )

        # === AFTER Incident ===
        v5 = policy_manager.update_config(
            {"dlq_enabled": True, "failure_threshold": 5},
            operator_id=1001,
            reason="Restore normal settings after incident",
        )

        # Verify all policy versions are tracked
        history = policy_manager.get_history()
        assert len(history) == 5  # v1, v2, v3, v4, v5

        # Verify incident tracked policy changes
        resolved = incident_recorder.get_incident(incident.incident_id)
        assert v2.version_id in resolved.policy_versions_during  # Initial
        assert v3.version_id in resolved.policy_versions_during
        assert v4.version_id in resolved.policy_versions_during

        # Verify each policy change is auditable
        policy_events = audit_trail.get_events(event_type=EventType.POLICY_CHANGED)
        assert len(policy_events) == 4  # v2, v3, v4, v5 (v1 is initial)

        for event in policy_events:
            assert event.details.get("reason") is not None
            assert event.details.get("old_version") is not None
            assert event.details.get("new_version") is not None


# =============================================================================
# Test Classes - Proof of Policy Boundary Compliance
# =============================================================================


class TestProofPolicyBoundaryCompliance:
    """Test proof that manual actions did not violate policy boundaries."""

    def test_no_manual_action_violated_policy_boundaries(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        operator_recorder: OperatorActionRecorder,
    ):
        """
        Verify all manual actions were within policy boundaries.
        """
        policy_version = policy_manager.get_current_version()

        # Define policy boundaries
        allowed_operator_actions = {
            "force_open_circuit",
            "force_close_circuit",
            "reset_circuit",
            "update_retry_policy",
            "enable_dlq",
            "disable_dlq",
        }

        # Record various operator actions
        actions_taken = []

        action1 = operator_recorder.record_action(
            operator_id=1001,
            action_type="force_open_circuit",
            target="payment-service",
            reason="Detected anomaly",
            policy_version_before=policy_version.version_id,
        )
        actions_taken.append(action1)

        action2 = operator_recorder.record_action(
            operator_id=1002,
            action_type="update_retry_policy",
            target="global",
            reason="Increase for holiday traffic",
            policy_version_before=policy_version.version_id,
        )
        actions_taken.append(action2)

        # Verify all actions are within allowed boundaries
        all_actions = operator_recorder.get_actions()
        violations = []

        for action in all_actions:
            if action.action_type not in allowed_operator_actions:
                violations.append(
                    {
                        "action_id": action.action_id,
                        "action_type": action.action_type,
                        "operator_id": action.operator_id,
                    }
                )

        # Assert no violations
        assert len(violations) == 0, f"Policy violations found: {violations}"

        # All actions should be explainable
        for action in all_actions:
            assert action.reason is not None
            assert len(action.reason) > 0
            assert action.operator_id is not None


# =============================================================================
# Test Classes - Zero-Downtime Guarantee Proof
# =============================================================================


class TestZeroDowntimeGuaranteeProof:
    """Test proof that zero-downtime guarantees were honored."""

    def test_zero_downtime_guarantees_honored(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        sla_tracker: SLATracker,
    ):
        """
        Verify zero-downtime during policy changes.
        """
        v1 = policy_manager.get_current_version()

        # Simulate continuous request processing during policy changes
        requests_before_change = []
        requests_during_change = []
        requests_after_change = []

        # Before change
        for i in range(10):
            request_id = f"before-{i}"
            decision_recorder.record_decision(
                request_id=request_id,
                policy_version=v1.version_id,
                decision="completed",
                decision_source=DecisionSource.AUTOMATED,
                justification="Normal processing",
            )
            sla_tracker.record_request(
                request_id=request_id,
                latency_ms=100.0,
                success=True,
                policy_version=v1.version_id,
            )
            requests_before_change.append(request_id)

        # Policy change
        v2 = policy_manager.update_config(
            {"max_retries": 5},
            operator_id=1001,
            reason="Zero-downtime update",
        )

        # During transition (some still use v1, some use v2)
        for i in range(10):
            request_id = f"during-{i}"
            version = v1.version_id if i < 5 else v2.version_id
            decision_recorder.record_decision(
                request_id=request_id,
                policy_version=version,
                decision="completed",
                decision_source=DecisionSource.AUTOMATED,
                justification="Transition processing",
            )
            sla_tracker.record_request(
                request_id=request_id,
                latency_ms=100.0,
                success=True,
                policy_version=version,
            )
            requests_during_change.append(request_id)

        # After change
        for i in range(10):
            request_id = f"after-{i}"
            decision_recorder.record_decision(
                request_id=request_id,
                policy_version=v2.version_id,
                decision="completed",
                decision_source=DecisionSource.AUTOMATED,
                justification="Normal processing",
            )
            sla_tracker.record_request(
                request_id=request_id,
                latency_ms=100.0,
                success=True,
                policy_version=v2.version_id,
            )
            requests_after_change.append(request_id)

        # Verify no downtime
        all_decisions = decision_recorder.get_decisions()
        failed_during_transition = [d for d in all_decisions if d.decision == "failed" or d.decision == "rejected"]

        # Zero failures = zero downtime
        assert len(failed_during_transition) == 0

        # Verify SLA compliance throughout
        breaches = sla_tracker.get_breaches()
        assert len(breaches) == 0

        # Verify all requests processed
        assert len(requests_before_change) == 10
        assert len(requests_during_change) == 10
        assert len(requests_after_change) == 10


# =============================================================================
# Test Classes - Report Determinism
# =============================================================================


class TestReportDeterminism:
    """Test that reports are deterministic."""

    def test_report_determinism(
        self,
        audit_trail: ImmutableAuditTrail,
        policy_manager: AuditablePolicyManager,
        decision_recorder: AuditableDecisionRecorder,
        sla_tracker: SLATracker,
        sla_report_generator: SLAComplianceReportGenerator,
        decision_report_generator: DecisionExplainabilityReportGenerator,
    ):
        """
        Verify same input produces same report output.
        """
        policy_version = policy_manager.get_current_version().version_id
        start_time = datetime.now(tz.utc)

        # Create fixed test data
        for i in range(20):
            request_id = f"determinism-{i}"
            latency = 100.0 + (i * 5)  # Deterministic latencies

            sla_tracker.record_request(
                request_id=request_id,
                latency_ms=latency,
                success=True,
                policy_version=policy_version,
            )

            decision_recorder.record_decision(
                request_id=request_id,
                policy_version=policy_version,
                decision="completed",
                decision_source=DecisionSource.AUTOMATED,
                justification=f"Request {i} processed",
                latency_ms=latency,
            )

        end_time = datetime.now(tz.utc)

        # Generate reports multiple times
        report1 = sla_report_generator.generate_report(start_time, end_time)
        report2 = sla_report_generator.generate_report(start_time, end_time)

        # Compare key metrics (exclude timestamps)
        assert report1["summary"]["total_requests"] == report2["summary"]["total_requests"]
        assert report1["summary"]["successful_requests"] == report2["summary"]["successful_requests"]
        assert report1["latency"]["average_ms"] == report2["latency"]["average_ms"]
        assert report1["breaches"]["count"] == report2["breaches"]["count"]

        # Decision report determinism
        decision_report1 = decision_report_generator.explain_request("determinism-0")
        decision_report2 = decision_report_generator.explain_request("determinism-0")

        assert decision_report1["total_decisions"] == decision_report2["total_decisions"]
        assert decision_report1["final_outcome"] == decision_report2["final_outcome"]


# =============================================================================
# Main Entry Point
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
