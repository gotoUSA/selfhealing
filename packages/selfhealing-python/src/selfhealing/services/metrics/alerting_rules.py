"""
Alerting Rule Definitions.

Single source of truth for Prometheus alerting rules.
These rules can be used to generate prometheus alert configuration files.

Run: python manage.py generate_self_healing_alerts
"""

from __future__ import annotations

ALERTING_RULES: dict = {
    # =========================================================================
    # DLQ Alerting Rules
    # =========================================================================
    "DLQPendingHigh": {
        "expr": "dlq_pending_count > 10",
        "for": "5m",
        "severity": "warning",
        "team": "ops",
        "summary": "DLQ pending count is high",
        "description": "More than 10 items pending in DLQ for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/dlq-pending-high",
    },
    "DLQPendingCritical": {
        "expr": "dlq_pending_count > 50",
        "for": "5m",
        "severity": "critical",
        "team": "ops",
        "summary": "DLQ pending count is critical",
        "description": "More than 50 items pending in DLQ for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/dlq-pending-critical",
    },
    "DLQGrowthRateHigh": {
        "expr": "rate(dlq_created_total[5m]) > 5",
        "for": "5m",
        "severity": "warning",
        "team": "ops",
        "summary": "DLQ growth rate is high",
        "description": "More than 5 new DLQ items per minute for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/dlq-growth-high",
    },
    # =========================================================================
    # Retry Alerting Rules
    # =========================================================================
    "RetrySuccessRateLow": {
        "expr": "retry_success_rate < 70",
        "for": "15m",
        "severity": "warning",
        "team": "dev",
        "summary": "Retry success rate is low",
        "description": "Retry success rate below 70% for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/retry-success-low",
    },
    # =========================================================================
    # Circuit Breaker Alerting Rules
    # =========================================================================
    "CircuitBreakerOpen": {
        "expr": "circuit_breaker_state == 1",
        "for": "1m",
        "severity": "critical",
        "team": "ops",
        "summary": "Circuit breaker is open",
        "description": "Circuit breaker for {{ $labels.service }} is in OPEN state",
        "runbook_url": "https://docs.internal/runbooks/circuit-breaker-open",
    },
    "CircuitBreakerOpenLong": {
        "expr": "circuit_breaker_state == 1",
        "for": "10m",
        "severity": "critical",
        "team": "ops",
        "summary": "Circuit breaker open for extended period",
        "description": "Circuit breaker for {{ $labels.service }} has been open for more than 10 minutes",
        "runbook_url": "https://docs.internal/runbooks/circuit-breaker-extended",
    },
    # =========================================================================
    # SLA Alerting Rules
    # =========================================================================
    "SLABreachDetected": {
        "expr": "increase(sla_breach_total[1h]) > 0",
        "for": "0m",
        "severity": "warning",
        "team": "ops",
        "summary": "SLA breach detected",
        "description": "SLA breach detected for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/sla-breach",
    },
    "RecoveryTimeSlow": {
        "expr": "histogram_quantile(0.95, rate(recovery_time_seconds_bucket[1h])) > 1800",
        "for": "15m",
        "severity": "warning",
        "team": "ops",
        "summary": "Recovery time P95 is slow",
        "description": "95th percentile recovery time exceeds 30 minutes",
        "runbook_url": "https://docs.internal/runbooks/recovery-slow",
    },
    "HumanReviewQueueLong": {
        "expr": "histogram_quantile(0.95, rate(human_review_queue_time_seconds_bucket[1h])) > 3600",
        "for": "30m",
        "severity": "warning",
        "team": "ops",
        "summary": "Human review queue time is high",
        "description": "Items waiting more than 1 hour for human review",
        "runbook_url": "https://docs.internal/runbooks/review-queue-long",
    },
    # =========================================================================
    # Replay Alerting Rules
    # =========================================================================
    "ReplayFailureRateHigh": {
        "expr": "sum(rate(replay_outcomes_total{outcome='failure'}[1h])) / sum(rate(replay_outcomes_total[1h])) > 0.5",
        "for": "15m",
        "severity": "warning",
        "team": "dev",
        "summary": "Replay failure rate is high",
        "description": "More than 50% of replay attempts are failing",
        "runbook_url": "https://docs.internal/runbooks/replay-failure-high",
    },
    # =========================================================================
    # Error Budget Alerting Rules
    # =========================================================================
    "ErrorBudgetCritical": {
        "expr": "error_budget_remaining_percent < 20",
        "for": "5m",
        "severity": "critical",
        "team": "ops",
        "summary": "Error budget critical - deployment freeze recommended",
        "description": "Error budget remaining is {{ $value }}%. Deployment freeze is recommended.",
        "runbook_url": "https://docs.internal/runbooks/error-budget-critical",
    },
    "ErrorBudgetWarning": {
        "expr": "error_budget_remaining_percent < 50",
        "for": "10m",
        "severity": "warning",
        "team": "ops",
        "summary": "Error budget warning",
        "description": "Error budget remaining is {{ $value }}%. Consider reducing deployments.",
        "runbook_url": "https://docs.internal/runbooks/error-budget-warning",
    },
    "ErrorBudgetFastBurn": {
        "expr": "error_budget_burn_rate_1h > 14.4",
        "for": "5m",
        "severity": "critical",
        "team": "ops",
        "summary": "Fast error budget burn detected",
        "description": "1-hour burn rate is {{ $value }}x. Consuming 2%+ budget per hour.",
        "runbook_url": "https://docs.internal/runbooks/error-budget-fast-burn",
    },
    "ErrorBudgetSlowBurn": {
        "expr": "error_budget_burn_rate_6h > 3",
        "for": "30m",
        "severity": "warning",
        "team": "ops",
        "summary": "Slow error budget burn detected",
        "description": "6-hour burn rate is {{ $value }}x. Sustained elevated error rate.",
        "runbook_url": "https://docs.internal/runbooks/error-budget-slow-burn",
    },
    "DeploymentFreezeActive": {
        "expr": "deployment_freeze_status >= 3",
        "for": "0m",
        "severity": "info",
        "team": "ops",
        "summary": "Deployment freeze is active",
        "description": "Deployment freeze is recommended or in effect.",
        "runbook_url": "https://docs.internal/runbooks/deployment-freeze",
    },
    # =========================================================================
    # Fail-Safe Alerting Rules (침묵하는 장애 방지)
    # =========================================================================
    "FailSafeTriggered": {
        "expr": "increase(selfhealing_failsafe_triggered_total[5m]) > 0",
        "for": "0m",
        "severity": "critical",
        "team": "ops",
        "summary": "🚨 Self-Healing Fail-Safe mode activated",
        "description": (
            "Self-Healing system component '{{ $labels.component }}' has failed and "
            "Fail-Safe mode is active. Deployments are proceeding but system needs "
            "immediate attention."
        ),
        "runbook_url": "https://docs.internal/runbooks/selfhealing-failsafe",
    },
    "FailSafeModeActive": {
        "expr": "selfhealing_failsafe_mode_active == 1",
        "for": "2m",
        "severity": "critical",
        "team": "ops",
        "summary": "🚨 Self-Healing in degraded mode",
        "description": (
            "Self-Healing '{{ $labels.component }}' is operating in Fail-Safe mode. "
            "Error Budget recommendations are not available. "
            "Investigate and restore normal operation immediately."
        ),
        "runbook_url": "https://docs.internal/runbooks/selfhealing-failsafe",
    },
    # =========================================================================
    # Dead Man's Snitch (Heartbeat Monitoring)
    # =========================================================================
    "SelfHealingServiceDead": {
        "expr": "time() - selfhealing_heartbeat_timestamp_seconds > 120",
        "for": "0m",
        "severity": "critical",
        "team": "ops",
        "summary": "🔴 Self-Healing service is DEAD",
        "description": (
            "No heartbeat received from Self-Healing '{{ $labels.component }}' "
            "for more than 2 minutes. The service may have crashed or is unresponsive. "
            "This is a critical infrastructure failure."
        ),
        "runbook_url": "https://docs.internal/runbooks/selfhealing-dead",
    },
    "SelfHealingHeartbeatMissing": {
        "expr": "absent(selfhealing_heartbeat_timestamp_seconds) == 1",
        "for": "5m",
        "severity": "critical",
        "team": "ops",
        "summary": "🔴 Self-Healing heartbeat metric missing",
        "description": (
            "The Self-Healing heartbeat metric is completely absent. "
            "The service may never have started or is not properly initialized."
        ),
        "runbook_url": "https://docs.internal/runbooks/selfhealing-missing",
    },
    # =========================================================================
    # Override Escalation Alerting Rules
    # =========================================================================
    "OverrideEscalation": {
        "expr": "increase(selfhealing_override_escalation_total[1h]) > 0",
        "for": "0m",
        "severity": "warning",
        "team": "ops",
        "summary": "⚠️ Deployment override escalation",
        "description": (
            "A deployment override of type '{{ $labels.override_type }}' was approved "
            "despite insufficient error budget. This action requires governance review."
        ),
        "runbook_url": "https://docs.internal/runbooks/override-escalation",
    },
    "OverrideEscalationHigh": {
        "expr": "increase(selfhealing_override_escalation_total[24h]) > 5",
        "for": "0m",
        "severity": "critical",
        "team": "ops",
        "summary": "🚨 Excessive deployment overrides",
        "description": (
            "More than 5 deployment overrides in the last 24 hours. "
            "This may indicate process issues or sustained reliability problems."
        ),
        "runbook_url": "https://docs.internal/runbooks/override-escalation-high",
    },
    # =========================================================================
    # X-Test Regional Boundary Alerting Rules
    # =========================================================================
    "XTestCrossRegionDeniedRateHigh": {
        "expr": "rate(selfhealing_xtest_cross_region_denied_total[1m]) > 10",
        "for": "1m",
        "severity": "warning",
        "team": "security",
        "summary": "⚠️ High rate of cross-region X-Test denials",
        "description": (
            "Cross-region X-Test denial rate exceeds 10/min. "
            "Current region: {{ $labels.current_region }}, "
            "Target region: {{ $labels.target_region }}. "
            "This may indicate misconfigured clients or attempted cross-region access."
        ),
        "runbook_url": "https://docs.internal/runbooks/xtest-cross-region-denied",
    },
    "XTestCrossRegionDeniedFromSameSource": {
        "expr": ("sum by (current_region, target_region) " "(increase(selfhealing_xtest_cross_region_denied_total[5m])) > 5"),
        "for": "0m",
        "severity": "warning",
        "team": "security",
        "summary": "🔒 Repeated cross-region X-Test denials detected",
        "description": (
            "More than 5 cross-region denials in 5 minutes from the same source. "
            "This may indicate a security issue or misconfigured automation. "
            "Investigate the source of these requests immediately."
        ),
        "runbook_url": "https://docs.internal/runbooks/xtest-cross-region-repeated",
    },
}
