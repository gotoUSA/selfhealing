"""
Config Shadow Evaluator Data Models.

Shadow Evaluation 엔티티 및 비교 리포트 모델.
Canary 서비스와 독립적으로 설정 변경 효과를 사전 시뮬레이션한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EvaluationStatus(str, Enum):
    """Shadow Evaluation 상태."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class EvaluatorResult:
    """개별 Evaluator의 비교 결과."""

    evaluator_name: str
    passed: bool
    confidence_score: float

    baseline_metrics: dict[str, Any] = field(default_factory=dict)
    candidate_metrics: dict[str, Any] = field(default_factory=dict)
    delta: dict[str, Any] = field(default_factory=dict)

    details: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class EvaluationReport:
    """시뮬레이션 비교 결과 리포트."""

    events_analyzed: int
    time_range_start: datetime
    time_range_end: datetime

    evaluator_results: list[EvaluatorResult] = field(default_factory=list)

    passed: bool = False
    confidence_score: float = 0.0
    summary: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ShadowEvaluation:
    """단일 Shadow Evaluation 실행."""

    evaluation_id: str
    rollout_id: str | None
    status: EvaluationStatus
    created_at: datetime
    completed_at: datetime | None = None

    config_type: str = ""
    baseline_config: dict[str, Any] = field(default_factory=dict)
    candidate_config: dict[str, Any] = field(default_factory=dict)
    service_name: str = ""
    time_window_hours: int = 336
    region: str = ""

    report: EvaluationReport | None = None
    error_message: str = ""


@dataclass
class SimulationResult:
    """CB 시뮬레이션 집계 결과."""

    open_count: int = 0
    total_open_seconds: float = 0.0
    avg_recovery_seconds: float = 0.0


@dataclass
class BudgetSimulationResult:
    """Error Budget 시뮬레이션 집계 결과."""

    total_drain_percent: float = 0.0
    critical_episodes: int = 0
    max_burn_rate_1h: float = 0.0
