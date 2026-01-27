"""
Chaos Engineering Module

Enterprise-grade Continuous Resilience Validation Engine.

This module provides:
- ChaosExperiment Library: 5 core experiment types
- BlastRadiusManager: Scope control (INSTANCE/SERVICE/REGION)
- SafetyGuard: Error budget pre-flight checks
- ChaosSchedulerService: Celery Beat-based scheduling
- DailyResilienceReport: Automated reporting
- ChaosEngine: Unified facade for all Chaos subsystems

Usage:
    # Recommended: Single facade entry point
    from selfhealing.services.chaos import get_chaos_engine

    engine = get_chaos_engine()
    engine.scheduler.schedule_experiment(...)
    engine.safety_guard.check_error_budget(...)
    engine.blast_radius.get_current_policy()
    engine.reports.generate_daily_report()
    engine.analyzer.analyze_experiment(...)

    # Legacy: Individual helper functions (still supported)
    from selfhealing.services.chaos import get_chaos_scheduler
    scheduler = get_chaos_scheduler()
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .blast_radius import BlastRadiusManager
    from .blast_radius_analyzer import BlastRadiusAnalyzer
    from .reports import ResilienceReportGenerator
    from .safety_guard import SafetyGuard
    from .scheduler import ChaosSchedulerService


# =============================================================================
# ChaosEngine Facade
# =============================================================================


class ChaosEngine:
    """
    Unified facade for all Chaos Engineering subsystems.

    Provides a single entry point to access:
    - scheduler: Experiment scheduling and execution
    - safety_guard: Safety checks and error budget validation
    - blast_radius: Blast radius policy management
    - reports: Report generation
    - analyzer: Blast radius analysis

    All subsystems are lazily loaded on first access.

    Example:
        engine = get_chaos_engine()

        # Schedule an experiment
        engine.scheduler.schedule_experiment(config)

        # Check safety before execution
        if engine.safety_guard.check_error_budget():
            engine.scheduler.execute_experiment(exp_id)

        # Generate a report
        engine.reports.generate_daily_report()
    """

    def __init__(self) -> None:
        """Initialize ChaosEngine with lazy-loaded subsystems."""
        self._scheduler: ChaosSchedulerService | None = None
        self._safety_guard: SafetyGuard | None = None
        self._blast_radius: BlastRadiusManager | None = None
        self._reports: ResilienceReportGenerator | None = None
        self._analyzer: BlastRadiusAnalyzer | None = None

    @property
    def scheduler(self) -> ChaosSchedulerService:
        """
        Get the ChaosSchedulerService instance.

        Provides experiment CRUD, scheduling, and execution capabilities.
        """
        if self._scheduler is None:
            from .scheduler import get_chaos_scheduler

            self._scheduler = get_chaos_scheduler()
        return self._scheduler

    @property
    def safety_guard(self) -> SafetyGuard:
        """
        Get the SafetyGuard instance.

        Provides error budget validation and safety checks.
        """
        if self._safety_guard is None:
            from .safety_guard import get_safety_guard

            self._safety_guard = get_safety_guard()
        return self._safety_guard

    @property
    def blast_radius(self) -> BlastRadiusManager:
        """
        Get the BlastRadiusManager instance.

        Provides blast radius policy management.
        """
        if self._blast_radius is None:
            from .blast_radius import get_blast_radius_manager

            self._blast_radius = get_blast_radius_manager()
        return self._blast_radius

    @property
    def reports(self) -> ResilienceReportGenerator:
        """
        Get the ResilienceReportGenerator instance.

        Provides report generation capabilities.
        """
        if self._reports is None:
            from .reports import get_report_generator

            self._reports = get_report_generator()
        return self._reports

    @property
    def analyzer(self) -> BlastRadiusAnalyzer:
        """
        Get the BlastRadiusAnalyzer instance.

        Provides blast radius analysis capabilities.
        """
        if self._analyzer is None:
            from .blast_radius_analyzer import get_blast_radius_analyzer

            self._analyzer = get_blast_radius_analyzer()
        return self._analyzer

    def reset(self) -> None:
        """
        Reset all cached subsystem instances.

        Used for testing to ensure fresh instances.
        """
        self._scheduler = None
        self._safety_guard = None
        self._blast_radius = None
        self._reports = None
        self._analyzer = None


# =============================================================================
# ChaosEngine Singleton
# =============================================================================

_chaos_engine_instance: ChaosEngine | None = None
_chaos_engine_lock = threading.Lock()


def get_chaos_engine() -> ChaosEngine:
    """
    Get the singleton ChaosEngine instance.

    Returns:
        ChaosEngine: The unified facade for all Chaos subsystems.

    Example:
        engine = get_chaos_engine()
        engine.scheduler.schedule_experiment(...)
        engine.safety_guard.check_error_budget(...)
    """
    global _chaos_engine_instance

    if _chaos_engine_instance is None:
        with _chaos_engine_lock:
            if _chaos_engine_instance is None:
                _chaos_engine_instance = ChaosEngine()

    return _chaos_engine_instance


def reset_chaos_engine() -> None:
    """
    Reset the singleton ChaosEngine instance.

    Used for testing to ensure a fresh instance is created on next access.
    Also resets all internal subsystem references.
    """
    global _chaos_engine_instance
    with _chaos_engine_lock:
        if _chaos_engine_instance is not None:
            _chaos_engine_instance.reset()
        _chaos_engine_instance = None


# =============================================================================
# Legacy Imports (Backward Compatible)
# =============================================================================

from .base import (
    ChaosExperiment,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
)
from .blast_radius import (
    BlastRadius,
    BlastRadiusManager,
    get_blast_radius_manager,
)
from .blast_radius_analyzer import (
    BlastRadiusAnalysisResult,
    BlastRadiusAnalyzer,
    BlastRadiusLevel,
    DependencyNode,
    get_blast_radius_analyzer,
)
from .experiments import (
    Error5xxExperiment,
    LatencyInjectionExperiment,
    PacketLossExperiment,
    ResourceExhaustionExperiment,
    TimeoutExperiment,
)

# Impact Prediction
from .impact_predictor import (
    ImpactPredictor,
    PredictedOutcome,
    ServiceImpact,
    get_impact_predictor,
)
from .reports import (
    DailyResilienceReport,
    ResilienceReportGenerator,
    get_report_generator,
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
from .safety_guard import (
    SafetyCheckResult,
    SafetyGuard,
    get_safety_guard,
)
from .scheduler import (
    ChaosSchedulerService,
    ExecutionResult,
    ExperimentApprovalStatus,
    ScheduledExperiment,
    SchedulerConfig,
    ScheduleType,
    get_chaos_scheduler,
    reset_chaos_scheduler,
)
from .stop_conditions import (
    DryRunConfig,
    StopConditionCheckResult,
    StopConditionsChecker,
    StopConditionsConfig,
    StopConditionViolation,
    TTLConfig,
    get_dry_run_config,
    get_stop_conditions_checker,
    get_stop_conditions_config,
    get_ttl_config,
)

__all__ = [
    # Chaos Engine Facade (New Unified Entry Point)
    "ChaosEngine",
    "get_chaos_engine",
    "reset_chaos_engine",
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
    "ScheduleType",
    "SchedulerConfig",
    "ExecutionResult",
    "get_chaos_scheduler",
    "reset_chaos_scheduler",
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
