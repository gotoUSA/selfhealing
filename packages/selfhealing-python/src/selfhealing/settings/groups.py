"""
Settings Domain Groups — Lazy-initialized cached_property containers.

14 logical groups that organize ~90 non-Root settings by domain.
Each group uses @cached_property with lazy import to avoid circular imports
and minimize startup cost.

Usage:
    from selfhealing.settings.root import get_config
    config = get_config()
    config.core.backoff          # CoreGroup
    config.scaling.backpressure  # ScalingGroup
"""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.settings.admission_control import AdmissionControlSettings
    from selfhealing.settings.airgap import AirGapSettings
    from selfhealing.settings.anti_flapping import AntiFlappingSettings
    from selfhealing.settings.api_rate_limit import ApiRateLimitSettings
    from selfhealing.settings.api_view import ApiViewSettings
    from selfhealing.settings.apply_strategy import ApplyStrategySettings
    from selfhealing.settings.audit import AuditSettings
    from selfhealing.settings.audit_integrity import AuditIntegritySettings
    from selfhealing.settings.audit_reconciler import AuditReconcilerSettings
    from selfhealing.settings.audit_sync import AuditSyncSettings
    from selfhealing.settings.audit_watchdog import AuditWatchdogSettings
    from selfhealing.settings.auto_rollback import AutoRollbackSettings
    from selfhealing.settings.backoff import BackoffSettings
    from selfhealing.settings.backpressure import BackpressureSettings
    from selfhealing.settings.batch import BatchSettings
    from selfhealing.settings.bulkhead import BulkheadSettings
    from selfhealing.settings.canary import CanarySettings
    from selfhealing.settings.canary_governance import CanaryGovernanceSettings
    from selfhealing.settings.canary_watchdog import CanaryWatchdogSettings
    from selfhealing.settings.capacity_reservation import CapacityReservationSettings
    from selfhealing.settings.cascade_retention import CascadeRetentionSettings
    from selfhealing.settings.celery_task import CeleryTaskSettings
    from selfhealing.settings.cell_topology import CellTopologySettings
    from selfhealing.settings.chaos_blast_radius import ChaosBlastRadiusSettings
    from selfhealing.settings.chaos_experiment import ChaosExperimentSettings
    from selfhealing.settings.chaos_safety_caps import ChaosSafetyCapsSettings
    from selfhealing.settings.circuit_breaker_advanced import (
        CircuitBreakerAdvancedSettings,
    )
    from selfhealing.settings.circuit_mesh import CircuitMeshSettings
    from selfhealing.settings.cleanup import CleanupSettings
    from selfhealing.settings.config_shadow import ConfigShadowSettings
    from selfhealing.settings.correlation import CorrelationSettings
    from selfhealing.settings.correlation_engine import CorrelationEngineSettings
    from selfhealing.settings.corruption_shield import CorruptionShieldSettings
    from selfhealing.settings.critical_worker import CriticalWorkerSettings
    from selfhealing.settings.daily_report import DailyReportSettings
    from selfhealing.settings.dashboard import DashboardSettings
    from selfhealing.settings.decision_engine import DecisionEngineSettings
    from selfhealing.settings.distributed_lock import DistributedLockSettings
    from selfhealing.settings.domain_sensitivity import DomainSensitivitySettings
    from selfhealing.settings.drift_detection import DriftDetectionSettings
    from selfhealing.settings.error_budget_gate import ErrorBudgetGateSettings
    from selfhealing.settings.error_budget_propagation import (
        ErrorBudgetPropagationSettings,
    )
    from selfhealing.settings.event_buffer import EventBufferSettings
    from selfhealing.settings.event_journal import EventJournalSettings
    from selfhealing.settings.gate_fault import GateFaultSettings
    from selfhealing.settings.graceful_degradation import GracefulDegradationSettings
    from selfhealing.settings.hash_chain import HashChainSettings
    from selfhealing.settings.hedging import HedgingSettings
    from selfhealing.settings.http_client import HttpClientSettings
    from selfhealing.settings.intelligence_task import IntelligenceTaskSettings
    from selfhealing.settings.jitter import JitterSettings
    from selfhealing.settings.leader_election import LeaderElectionSettings
    from selfhealing.settings.meta_watchdog import MetaWatchdogSettings
    from selfhealing.settings.namespace_emergency import NamespaceEmergencySettings
    from selfhealing.settings.notification_channel import NotificationChannelSettings
    from selfhealing.settings.otel import OpenTelemetrySettings
    from selfhealing.settings.pipeline import PipelineSettings
    from selfhealing.settings.pool_monitor import PoolMonitorSettings
    from selfhealing.settings.postmortem import PostmortemSettings
    from selfhealing.settings.precomputed_cache import PrecomputedCacheSettings
    from selfhealing.settings.predictive_forecaster import PredictiveForecasterSettings
    from selfhealing.settings.rate_limit_throttle_integration import (
        RateLimitThrottleIntegrationSettings,
    )
    from selfhealing.settings.recovery_circuit_breaker import (
        RecoveryCircuitBreakerSettings,
    )
    from selfhealing.settings.recovery_coordinator import RecoveryCoordinatorSettings
    from selfhealing.settings.recovery_shutdown import RecoveryShutdownSettings
    from selfhealing.settings.recovery_tasks import RecoveryTasksSettings
    from selfhealing.settings.redis_key_guard import RedisKeyGuardSettings
    from selfhealing.settings.regional_recovery_policy import (
        RegionalRecoveryPolicySettings,
    )
    from selfhealing.settings.replay_automation import ReplayAutomationSettings
    from selfhealing.settings.resilient_recorder import ResilientRecorderSettings
    from selfhealing.settings.resource_guard import ResourceGuardSettings
    from selfhealing.settings.resource_monitor import ResourceMonitorSettings
    from selfhealing.settings.ring_buffer import RingBufferSettings
    from selfhealing.settings.runbook import RunbookSettings
    from selfhealing.settings.runtime_feedback import RuntimeFeedbackSettings
    from selfhealing.settings.safe_gauge import SafeGaugeSettings
    from selfhealing.settings.safety_bounds import SafetyBoundsSettings
    from selfhealing.settings.sampling import SamplingSettings
    from selfhealing.settings.scale import ScaleSettings
    from selfhealing.settings.secrets import SecretsSettings
    from selfhealing.settings.slack_channel import SlackChannelSettings
    from selfhealing.settings.slo import SLOSettings
    from selfhealing.settings.state_cache import StateCacheSettings
    from selfhealing.settings.steady_state import SteadyStateSettings
    from selfhealing.settings.stress_test import StressTestSettings
    from selfhealing.settings.system_metrics_cache import SystemMetricsCacheSettings
    from selfhealing.settings.throttle import ThrottleSettings
    from selfhealing.settings.throttle_sla_notification import (
        ThrottleSLANotificationSettings,
    )
    from selfhealing.settings.xtest_cleanup import XTestCleanupSettings


class CoreGroup:
    """Core module settings: backoff, circuit breaker advanced, pool monitor, admission control."""

    @cached_property
    def admission_control(self) -> AdmissionControlSettings:
        from selfhealing.settings.admission_control import AdmissionControlSettings

        return AdmissionControlSettings()

    @cached_property
    def backoff(self) -> BackoffSettings:
        from selfhealing.settings.backoff import BackoffSettings

        return BackoffSettings()

    @cached_property
    def circuit_breaker_advanced(self) -> CircuitBreakerAdvancedSettings:
        from selfhealing.settings.circuit_breaker_advanced import (
            CircuitBreakerAdvancedSettings,
        )

        return CircuitBreakerAdvancedSettings()

    @cached_property
    def pool_monitor(self) -> PoolMonitorSettings:
        from selfhealing.settings.pool_monitor import PoolMonitorSettings

        return PoolMonitorSettings()


class ServicesGroup:
    """Services module settings: chaos, DLQ extensions, recovery, canary, governance, etc."""

    @cached_property
    def anti_flapping(self) -> AntiFlappingSettings:
        from selfhealing.settings.anti_flapping import AntiFlappingSettings

        return AntiFlappingSettings()

    @cached_property
    def api_rate_limit(self) -> ApiRateLimitSettings:
        from selfhealing.settings.api_rate_limit import ApiRateLimitSettings

        return ApiRateLimitSettings()

    @cached_property
    def api_view(self) -> ApiViewSettings:
        from selfhealing.settings.api_view import ApiViewSettings

        return ApiViewSettings()

    @cached_property
    def apply_strategy(self) -> ApplyStrategySettings:
        from selfhealing.settings.apply_strategy import ApplyStrategySettings

        return ApplyStrategySettings()

    @cached_property
    def auto_rollback(self) -> AutoRollbackSettings:
        from selfhealing.settings.auto_rollback import AutoRollbackSettings

        return AutoRollbackSettings()

    @cached_property
    def batch(self) -> BatchSettings:
        from selfhealing.settings.batch import BatchSettings

        return BatchSettings()

    @cached_property
    def canary(self) -> CanarySettings:
        from selfhealing.settings.canary import CanarySettings

        return CanarySettings()

    @cached_property
    def canary_governance(self) -> CanaryGovernanceSettings:
        from selfhealing.settings.canary_governance import CanaryGovernanceSettings

        return CanaryGovernanceSettings()

    @cached_property
    def canary_watchdog(self) -> CanaryWatchdogSettings:
        from selfhealing.settings.canary_watchdog import CanaryWatchdogSettings

        return CanaryWatchdogSettings()

    @cached_property
    def capacity_reservation(self) -> CapacityReservationSettings:
        from selfhealing.settings.capacity_reservation import (
            CapacityReservationSettings,
        )

        return CapacityReservationSettings()

    @cached_property
    def chaos_blast_radius(self) -> ChaosBlastRadiusSettings:
        from selfhealing.settings.chaos_blast_radius import ChaosBlastRadiusSettings

        return ChaosBlastRadiusSettings()

    @cached_property
    def chaos_experiment(self) -> ChaosExperimentSettings:
        from selfhealing.settings.chaos_experiment import ChaosExperimentSettings

        return ChaosExperimentSettings()

    @cached_property
    def chaos_safety_caps(self) -> ChaosSafetyCapsSettings:
        from selfhealing.settings.chaos_safety_caps import ChaosSafetyCapsSettings

        return ChaosSafetyCapsSettings()

    @cached_property
    def circuit_mesh(self) -> CircuitMeshSettings:
        from selfhealing.settings.circuit_mesh import CircuitMeshSettings

        return CircuitMeshSettings()

    @cached_property
    def cleanup(self) -> CleanupSettings:
        from selfhealing.settings.cleanup import CleanupSettings

        return CleanupSettings()

    @cached_property
    def critical_worker(self) -> CriticalWorkerSettings:
        from selfhealing.settings.critical_worker import CriticalWorkerSettings

        return CriticalWorkerSettings()

    @cached_property
    def daily_report(self) -> DailyReportSettings:
        from selfhealing.settings.daily_report import DailyReportSettings

        return DailyReportSettings()

    @cached_property
    def decision_engine(self) -> DecisionEngineSettings:
        from selfhealing.settings.decision_engine import DecisionEngineSettings

        return DecisionEngineSettings()

    @cached_property
    def error_budget_gate(self) -> ErrorBudgetGateSettings:
        from selfhealing.settings.error_budget_gate import ErrorBudgetGateSettings

        return ErrorBudgetGateSettings()

    @cached_property
    def error_budget_propagation(self) -> ErrorBudgetPropagationSettings:
        from selfhealing.settings.error_budget_propagation import (
            ErrorBudgetPropagationSettings,
        )

        return ErrorBudgetPropagationSettings()

    @cached_property
    def intelligence_task(self) -> IntelligenceTaskSettings:
        from selfhealing.settings.intelligence_task import IntelligenceTaskSettings

        return IntelligenceTaskSettings()

    @cached_property
    def precomputed_cache(self) -> PrecomputedCacheSettings:
        from selfhealing.settings.precomputed_cache import PrecomputedCacheSettings

        return PrecomputedCacheSettings()

    @cached_property
    def recovery_circuit_breaker(self) -> RecoveryCircuitBreakerSettings:
        from selfhealing.settings.recovery_circuit_breaker import (
            RecoveryCircuitBreakerSettings,
        )

        return RecoveryCircuitBreakerSettings()

    @cached_property
    def recovery_coordinator(self) -> RecoveryCoordinatorSettings:
        from selfhealing.settings.recovery_coordinator import (
            RecoveryCoordinatorSettings,
        )

        return RecoveryCoordinatorSettings()

    @cached_property
    def recovery_shutdown(self) -> RecoveryShutdownSettings:
        from selfhealing.settings.recovery_shutdown import RecoveryShutdownSettings

        return RecoveryShutdownSettings()

    @cached_property
    def recovery_tasks(self) -> RecoveryTasksSettings:
        from selfhealing.settings.recovery_tasks import RecoveryTasksSettings

        return RecoveryTasksSettings()

    @cached_property
    def replay_automation(self) -> ReplayAutomationSettings:
        from selfhealing.settings.replay_automation import ReplayAutomationSettings

        return ReplayAutomationSettings()

    @cached_property
    def runbook(self) -> RunbookSettings:
        from selfhealing.settings.runbook import RunbookSettings

        return RunbookSettings()

    @cached_property
    def slack_channel(self) -> SlackChannelSettings:
        from selfhealing.settings.slack_channel import SlackChannelSettings

        return SlackChannelSettings()


class AuditGroup:
    """Audit module settings: audit logging, hash chain, WAL, reconciler, etc."""

    @cached_property
    def audit(self) -> AuditSettings:
        from selfhealing.settings.audit import AuditSettings

        return AuditSettings()

    @cached_property
    def audit_integrity(self) -> AuditIntegritySettings:
        from selfhealing.settings.audit_integrity import AuditIntegritySettings

        return AuditIntegritySettings()

    @cached_property
    def audit_reconciler(self) -> AuditReconcilerSettings:
        from selfhealing.settings.audit_reconciler import AuditReconcilerSettings

        return AuditReconcilerSettings()

    @cached_property
    def audit_sync(self) -> AuditSyncSettings:
        from selfhealing.settings.audit_sync import AuditSyncSettings

        return AuditSyncSettings()

    @cached_property
    def audit_watchdog(self) -> AuditWatchdogSettings:
        from selfhealing.settings.audit_watchdog import AuditWatchdogSettings

        return AuditWatchdogSettings()

    @cached_property
    def hash_chain(self) -> HashChainSettings:
        from selfhealing.settings.hash_chain import HashChainSettings

        return HashChainSettings()

    @cached_property
    def cascade_retention(self) -> CascadeRetentionSettings:
        from selfhealing.settings.cascade_retention import CascadeRetentionSettings

        return CascadeRetentionSettings()

    @cached_property
    def event_journal(self) -> EventJournalSettings:
        from selfhealing.settings.event_journal import EventJournalSettings

        return EventJournalSettings()


class CoordinationGroup:
    """Coordination module settings: distributed lock, leader election, Redis key guard."""

    @cached_property
    def distributed_lock(self) -> DistributedLockSettings:
        from selfhealing.settings.distributed_lock import DistributedLockSettings

        return DistributedLockSettings()

    @cached_property
    def leader_election(self) -> LeaderElectionSettings:
        from selfhealing.settings.leader_election import LeaderElectionSettings

        return LeaderElectionSettings()

    @cached_property
    def redis_key_guard(self) -> RedisKeyGuardSettings:
        from selfhealing.settings.redis_key_guard import RedisKeyGuardSettings

        return RedisKeyGuardSettings()


class MultiRegionGroup:
    """Multi-region module settings: namespace emergency, cell topology, regional recovery."""

    @cached_property
    def namespace_emergency(self) -> NamespaceEmergencySettings:
        from selfhealing.settings.namespace_emergency import NamespaceEmergencySettings

        return NamespaceEmergencySettings()

    @cached_property
    def cell_topology(self) -> CellTopologySettings:
        from selfhealing.settings.cell_topology import CellTopologySettings

        return CellTopologySettings()

    @cached_property
    def regional_recovery_policy(self) -> RegionalRecoveryPolicySettings:
        from selfhealing.settings.regional_recovery_policy import (
            RegionalRecoveryPolicySettings,
        )

        return RegionalRecoveryPolicySettings()


class MetricsGroup:
    """Metrics module settings: drift detection, safe gauge, system metrics cache."""

    @cached_property
    def drift_detection(self) -> DriftDetectionSettings:
        from selfhealing.settings.drift_detection import DriftDetectionSettings

        return DriftDetectionSettings()

    @cached_property
    def safe_gauge(self) -> SafeGaugeSettings:
        from selfhealing.settings.safe_gauge import SafeGaugeSettings

        return SafeGaugeSettings()

    @cached_property
    def system_metrics_cache(self) -> SystemMetricsCacheSettings:
        from selfhealing.settings.system_metrics_cache import SystemMetricsCacheSettings

        return SystemMetricsCacheSettings()


class ScalingGroup:
    """Scaling module settings: backpressure, event buffer, load shedding, throttle, etc."""

    @cached_property
    def backpressure(self) -> BackpressureSettings:
        from selfhealing.settings.backpressure import BackpressureSettings

        return BackpressureSettings()

    @cached_property
    def event_buffer(self) -> EventBufferSettings:
        from selfhealing.settings.event_buffer import EventBufferSettings

        return EventBufferSettings()

    @cached_property
    def graceful_degradation(self) -> GracefulDegradationSettings:
        from selfhealing.settings.graceful_degradation import (
            GracefulDegradationSettings,
        )

        return GracefulDegradationSettings()

    @cached_property
    def rate_limit_throttle_integration(self) -> RateLimitThrottleIntegrationSettings:
        from selfhealing.settings.rate_limit_throttle_integration import (
            RateLimitThrottleIntegrationSettings,
        )

        return RateLimitThrottleIntegrationSettings()

    @cached_property
    def ring_buffer(self) -> RingBufferSettings:
        from selfhealing.settings.ring_buffer import RingBufferSettings

        return RingBufferSettings()

    @cached_property
    def scale(self) -> ScaleSettings:
        from selfhealing.settings.scale import ScaleSettings

        return ScaleSettings()

    @cached_property
    def state_cache(self) -> StateCacheSettings:
        from selfhealing.settings.state_cache import StateCacheSettings

        return StateCacheSettings()

    @cached_property
    def throttle(self) -> ThrottleSettings:
        from selfhealing.settings.throttle import ThrottleSettings

        return ThrottleSettings()

    @cached_property
    def throttle_sla_notification(self) -> ThrottleSLANotificationSettings:
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        return ThrottleSLANotificationSettings()


class ResilienceGroup:
    """Resilience module settings: bulkhead, hedging, resilient recorder, resource monitor."""

    @cached_property
    def bulkhead(self) -> BulkheadSettings:
        from selfhealing.settings.bulkhead import BulkheadSettings

        return BulkheadSettings()

    @cached_property
    def hedging(self) -> HedgingSettings:
        from selfhealing.settings.hedging import HedgingSettings

        return HedgingSettings()

    @cached_property
    def resilient_recorder(self) -> ResilientRecorderSettings:
        from selfhealing.settings.resilient_recorder import ResilientRecorderSettings

        return ResilientRecorderSettings()

    @cached_property
    def resource_monitor(self) -> ResourceMonitorSettings:
        from selfhealing.settings.resource_monitor import ResourceMonitorSettings

        return ResourceMonitorSettings()


class ObservabilityGroup:
    """Observability module settings: correlation, OTEL, etc."""

    @cached_property
    def correlation(self) -> CorrelationSettings:
        from selfhealing.settings.correlation import CorrelationSettings

        return CorrelationSettings()

    @cached_property
    def correlation_engine(self) -> CorrelationEngineSettings:
        from selfhealing.settings.correlation_engine import CorrelationEngineSettings

        return CorrelationEngineSettings()

    @cached_property
    def otel(self) -> OpenTelemetrySettings:
        from selfhealing.settings.otel import OpenTelemetrySettings

        return OpenTelemetrySettings()


class AdaptersGroup:
    """Adapters module settings: celery task, config shadow, HTTP client, etc."""

    @cached_property
    def celery_task(self) -> CeleryTaskSettings:
        from selfhealing.settings.celery_task import CeleryTaskSettings

        return CeleryTaskSettings()

    @cached_property
    def config_shadow(self) -> ConfigShadowSettings:
        from selfhealing.settings.config_shadow import ConfigShadowSettings

        return ConfigShadowSettings()

    @cached_property
    def http_client(self) -> HttpClientSettings:
        from selfhealing.settings.http_client import HttpClientSettings

        return HttpClientSettings()

    @cached_property
    def notification_channel(self) -> NotificationChannelSettings:
        from selfhealing.settings.notification_channel import (
            NotificationChannelSettings,
        )

        return NotificationChannelSettings()

    @cached_property
    def secrets(self) -> SecretsSettings:
        from selfhealing.settings.secrets import SecretsSettings

        return SecretsSettings()


class SecurityGroup:
    """Security module settings: corruption shield, domain sensitivity."""

    @cached_property
    def corruption_shield(self) -> CorruptionShieldSettings:
        from selfhealing.settings.corruption_shield import CorruptionShieldSettings

        return CorruptionShieldSettings()

    @cached_property
    def domain_sensitivity(self) -> DomainSensitivitySettings:
        from selfhealing.settings.domain_sensitivity import DomainSensitivitySettings

        return DomainSensitivitySettings()


class SLOGroup:
    """SLO module settings: dashboard, postmortem, SLO, steady state."""

    @cached_property
    def dashboard(self) -> DashboardSettings:
        from selfhealing.settings.dashboard import DashboardSettings

        return DashboardSettings()

    @cached_property
    def postmortem(self) -> PostmortemSettings:
        from selfhealing.settings.postmortem import PostmortemSettings

        return PostmortemSettings()

    @cached_property
    def slo(self) -> SLOSettings:
        from selfhealing.settings.slo import SLOSettings

        return SLOSettings()

    @cached_property
    def steady_state(self) -> SteadyStateSettings:
        from selfhealing.settings.steady_state import SteadyStateSettings

        return SteadyStateSettings()


class MetaGroup:
    """Meta module settings: gate fault, meta watchdog, pipeline, resource guard, etc."""

    @cached_property
    def gate_fault(self) -> GateFaultSettings:
        from selfhealing.settings.gate_fault import GateFaultSettings

        return GateFaultSettings()

    @cached_property
    def meta_watchdog(self) -> MetaWatchdogSettings:
        from selfhealing.settings.meta_watchdog import MetaWatchdogSettings

        return MetaWatchdogSettings()

    @cached_property
    def pipeline(self) -> PipelineSettings:
        from selfhealing.settings.pipeline import PipelineSettings

        return PipelineSettings()

    @cached_property
    def resource_guard(self) -> ResourceGuardSettings:
        from selfhealing.settings.resource_guard import ResourceGuardSettings

        return ResourceGuardSettings()

    @cached_property
    def runtime_feedback(self) -> RuntimeFeedbackSettings:
        from selfhealing.settings.runtime_feedback import RuntimeFeedbackSettings

        return RuntimeFeedbackSettings()

    @cached_property
    def safety_bounds(self) -> SafetyBoundsSettings:
        from selfhealing.settings.safety_bounds import SafetyBoundsSettings

        return SafetyBoundsSettings()


class TestingGroup:
    """Testing module settings: airgap, jitter, predictive forecaster, sampling, etc."""

    @cached_property
    def airgap(self) -> AirGapSettings:
        from selfhealing.settings.airgap import AirGapSettings

        return AirGapSettings()

    @cached_property
    def jitter(self) -> JitterSettings:
        from selfhealing.settings.jitter import JitterSettings

        return JitterSettings()

    @cached_property
    def predictive_forecaster(self) -> PredictiveForecasterSettings:
        from selfhealing.settings.predictive_forecaster import (
            PredictiveForecasterSettings,
        )

        return PredictiveForecasterSettings()

    @cached_property
    def sampling(self) -> SamplingSettings:
        from selfhealing.settings.sampling import SamplingSettings

        return SamplingSettings()

    @cached_property
    def stress_test(self) -> StressTestSettings:
        from selfhealing.settings.stress_test import StressTestSettings

        return StressTestSettings()

    @cached_property
    def xtest_cleanup(self) -> XTestCleanupSettings:
        from selfhealing.settings.xtest_cleanup import XTestCleanupSettings

        return XTestCleanupSettings()
