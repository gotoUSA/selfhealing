"""
Chaos Engineering Module

Enterprise-grade Continuous Resilience Validation Engine.

This module provides:
- ChaosExperiment Library: 5 core experiment types
- BlastRadiusManager: Scope control (INSTANCE/SERVICE/REGION)
- SafetyGuard: Error budget pre-flight checks
- ChaosSchedulerService: Celery Beat-based scheduling
- DailyResilienceReport: Automated reporting
"""

from .experiments import (
    ChaosExperiment,
    LatencyInjectionExperiment,
    Error5xxExperiment,
    PacketLossExperiment,
    TimeoutExperiment,
    ResourceExhaustionExperiment,
    ExperimentResult,
    ExperimentStatus,
)
from .blast_radius import (
    BlastRadius,
    BlastRadiusManager,
    get_blast_radius_manager,
)
from .safety_guard import (
    SafetyGuard,
    SafetyCheckResult,
    get_safety_guard,
)
from .scheduler import (
    ChaosSchedulerService,
    ScheduledExperiment,
    ExperimentApprovalStatus,
    get_chaos_scheduler,
)
from .reports import (
    DailyResilienceReport,
    ResilienceReportGenerator,
    get_report_generator,
)
from .stop_conditions import (
    StopConditionsConfig,
    StopConditionsChecker,
    StopConditionCheckResult,
    StopConditionViolation,
    TTLConfig,
    DryRunConfig,
    get_stop_conditions_checker,
    get_ttl_config,
    get_dry_run_config,
    get_stop_conditions_config,
)
# Impact Prediction
from .impact_predictor import (
    ImpactPredictor,
    PredictedOutcome,
    ServiceImpact,
    get_impact_predictor,
)
from .blast_radius_analyzer import (
    BlastRadiusAnalyzer,
    BlastRadiusAnalysisResult,
    BlastRadiusLevel,
    DependencyNode,
    get_blast_radius_analyzer,
)
# Resilience Expectation & Validation
from .resilience_expectation import (
    ExpectationType,
    ResilienceAssertion,
    ResilienceExpectation,
    ResilienceValidationResult,
)
from .resilience_validator import (
    ResilienceValidator,
    get_resilience_validator,
)

__all__ = [
    # Experiments
    "ChaosExperiment",
    "LatencyInjectionExperiment",
    "Error5xxExperiment",
    "PacketLossExperiment",
    "TimeoutExperiment",
    "ResourceExhaustionExperiment",
    "ExperimentResult",
    "ExperimentStatus",
    # Blast Radius
    "BlastRadius",
    "BlastRadiusManager",
    "get_blast_radius_manager",
    # Safety Guard
    "SafetyGuard",
    "SafetyCheckResult",
    "get_safety_guard",
    # Scheduler
    "ChaosSchedulerService",
    "ScheduledExperiment",
    "ExperimentApprovalStatus",
    "get_chaos_scheduler",
    # Reports
    "DailyResilienceReport",
    "ResilienceReportGenerator",
    "get_report_generator",
    # Stop Conditions & Safety Mechanisms
    "StopConditionsConfig",
    "StopConditionsChecker",
    "StopConditionCheckResult",
    "StopConditionViolation",
    "TTLConfig",
    "DryRunConfig",
    "get_stop_conditions_checker",
    "get_ttl_config",
    "get_dry_run_config",
    "get_stop_conditions_config",
    # Impact Prediction
    "ImpactPredictor",
    "PredictedOutcome",
    "ServiceImpact",
    "get_impact_predictor",
    "BlastRadiusAnalyzer",
    "BlastRadiusAnalysisResult",
    "BlastRadiusLevel",
    "DependencyNode",
    "get_blast_radius_analyzer",
    # Resilience Expectation & Validation
    "ExpectationType",
    "ResilienceAssertion",
    "ResilienceExpectation",
    "ResilienceValidationResult",
    "ResilienceValidator",
    "get_resilience_validator",
]
