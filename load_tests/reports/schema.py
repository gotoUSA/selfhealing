"""
Load Test Report Schema - 표준 지표 정의.

모든 부하 테스트 스테이지에서 사용하는 표준 데이터 스키마를 정의합니다.
이 스키마는 보고서 생성, JSON 직렬화, 스테이지 간 비교 분석의 기반이 됩니다.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


# ============================================================
# 스키마 버전
# ============================================================
SCHEMA_VERSION = "1.0.0"


class MetricCategory(Enum):
    """메트릭 카테고리 분류."""
    
    BASE = "base"
    SELFHEALING = "selfhealing"
    PLATINUM = "platinum"


# ============================================================
# Base Metrics (모든 스테이지 필수)
# ============================================================
@dataclass
class BaseMetrics:
    """
    모든 스테이지에서 필수로 수집하는 기본 메트릭.
    
    Attributes:
        test_name: 테스트 이름
        stage_id: 스테이지 식별자 (예: "stage12")
        timestamp: 테스트 시작 시간 (ISO 8601)
        test_duration_sec: 테스트 실행 시간 (초)
        max_users: 최대 동시 사용자 수
        min_users: 최소 동시 사용자 수
    """
    
    # 메타데이터
    test_name: str
    stage_id: str
    timestamp: str
    test_duration_sec: int
    max_users: int
    min_users: int
    environment_id: Optional[str] = None  # 테스트 환경 식별자
    chaos_intensity: Optional[float] = None  # 장애 주입 강도 (0.0~1.0)
    
    # 요청 통계
    total_requests: int = 0
    total_errors: int = 0
    error_rate_percent: float = 0.0
    
    # 응답시간 (ms)
    avg_response_ms: float = 0.0
    min_response_ms: float = 0.0
    max_response_ms: float = 0.0
    p50_response_ms: float = 0.0
    p95_response_ms: float = 0.0
    p99_response_ms: float = 0.0
    
    # 처리량
    throughput_rps: float = 0.0
    
    # 최종 결과
    passed: bool = True
    failure_reasons: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "test_name": self.test_name,
            "stage_id": self.stage_id,
            "timestamp": self.timestamp,
            "test_duration_sec": self.test_duration_sec,
            "max_users": self.max_users,
            "min_users": self.min_users,
            "environment_id": self.environment_id,
            "chaos_intensity": self.chaos_intensity,
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "error_rate_percent": self.error_rate_percent,
            "avg_response_ms": self.avg_response_ms,
            "min_response_ms": self.min_response_ms,
            "max_response_ms": self.max_response_ms,
            "p50_response_ms": self.p50_response_ms,
            "p95_response_ms": self.p95_response_ms,
            "p99_response_ms": self.p99_response_ms,
            "throughput_rps": self.throughput_rps,
            "passed": self.passed,
            "failure_reasons": self.failure_reasons,
        }


# ============================================================
# Self-Healing Metrics (Self-Healing 스테이지용)
# ============================================================
@dataclass
class SelfHealingMetrics:
    """
    Self-Healing 스테이지용 메트릭.
    
    Circuit Breaker, Emergency Mode, Error Budget, DLQ 등
    자가 치유 기능 관련 지표를 수집합니다.
    """
    
    # Circuit Breaker
    cb_open_count: int = 0
    cb_close_count: int = 0
    cb_half_open_count: int = 0
    cb_recovery_ms: Optional[float] = None
    cb_services_affected: List[str] = field(default_factory=list)
    
    # Emergency Mode
    emergency_triggered_count: int = 0
    emergency_released_count: int = 0
    emergency_max_level: int = 0
    
    # Error Budget
    error_budget_initial: Optional[float] = None
    error_budget_min: Optional[float] = None
    error_budget_exhausted: bool = False
    error_budget_recovered: bool = False
    
    # DLQ (Dead Letter Queue)
    dlq_max_count: int = 0
    dlq_replay_success: int = 0
    dlq_replay_fail: int = 0
    
    # Recovery
    recovery_latency_sec: Optional[float] = None
    recovery_sla_passed: bool = True  # < 2min (120초)
    
    # Chaos Injection
    chaos_failures_injected: int = 0
    chaos_cb_triggers: int = 0
    chaos_recovery_triggers: int = 0
    
    # Kill Switch
    kill_switch_activated: int = 0
    kill_switch_deactivated: int = 0
    kill_switch_targets: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "circuit_breaker": {
                "open_count": self.cb_open_count,
                "close_count": self.cb_close_count,
                "half_open_count": self.cb_half_open_count,
                "recovery_ms": self.cb_recovery_ms,
                "services_affected": self.cb_services_affected,
            },
            "emergency_mode": {
                "triggered_count": self.emergency_triggered_count,
                "released_count": self.emergency_released_count,
                "max_level": self.emergency_max_level,
            },
            "error_budget": {
                "initial": self.error_budget_initial,
                "min": self.error_budget_min,
                "exhausted": self.error_budget_exhausted,
                "recovered": self.error_budget_recovered,
            },
            "dlq": {
                "max_count": self.dlq_max_count,
                "replay_success": self.dlq_replay_success,
                "replay_fail": self.dlq_replay_fail,
            },
            "recovery": {
                "latency_sec": self.recovery_latency_sec,
                "sla_passed": self.recovery_sla_passed,
            },
            "chaos": {
                "failures_injected": self.chaos_failures_injected,
                "cb_triggers": self.chaos_cb_triggers,
                "recovery_triggers": self.chaos_recovery_triggers,
            },
            "kill_switch": {
                "activated": self.kill_switch_activated,
                "deactivated": self.kill_switch_deactivated,
                "targets": self.kill_switch_targets,
            },
        }


# ============================================================
# Platinum Metrics (Platinum Grade 스테이지용)
# ============================================================
@dataclass
class PlatinumMetrics:
    """
    Platinum Grade 스테이지용 메트릭.
    
    SLA Hard-Cap, Message Storm, Clock Skew, Cascading Failure 등
    고급 테스트 시나리오 관련 지표를 수집합니다.
    """
    
    # SLA Hard-Cap
    sla_p99_max_ms: float = 0.0
    sla_p99_violations: int = 0
    sla_p99_passed: bool = True
    sla_threshold_ms: float = 250.0
    
    # Message Storm & Backpressure
    storm_messages_injected: int = 0
    storm_buffer_overflow: int = 0
    storm_backpressure_activated: bool = False
    storm_main_logic_affected: bool = False
    
    # Clock Skew
    clock_attacks_executed: int = 0
    clock_max_drift_sec: float = 0.0
    clock_consistency_maintained: bool = True
    
    # Cascading Failure
    cascade_triggered: int = 0
    cascade_max_depth: int = 0
    cascade_isolation_success: bool = True
    
    # Retry Storm
    retry_storms_detected: int = 0
    retry_circuit_protected: bool = True
    
    # V2 Optimization Modules
    v2_cache_hits: int = 0
    v2_cache_misses: int = 0
    v2_async_events: int = 0
    v2_jitter_applied: int = 0
    
    # Platinum Grade 최종 판정
    platinum_achieved: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        total_cache = self.v2_cache_hits + self.v2_cache_misses
        cache_hit_rate = (self.v2_cache_hits / total_cache * 100) if total_cache > 0 else 0.0
        
        return {
            "sla_hardcap": {
                "p99_max_ms": self.sla_p99_max_ms,
                "violations": self.sla_p99_violations,
                "passed": self.sla_p99_passed,
                "threshold_ms": self.sla_threshold_ms,
            },
            "message_storm": {
                "messages_injected": self.storm_messages_injected,
                "buffer_overflow": self.storm_buffer_overflow,
                "backpressure_activated": self.storm_backpressure_activated,
                "main_logic_affected": self.storm_main_logic_affected,
            },
            "clock_skew": {
                "attacks_executed": self.clock_attacks_executed,
                "max_drift_sec": self.clock_max_drift_sec,
                "consistency_maintained": self.clock_consistency_maintained,
            },
            "cascading_failure": {
                "triggered": self.cascade_triggered,
                "max_depth": self.cascade_max_depth,
                "isolation_success": self.cascade_isolation_success,
            },
            "retry_storm": {
                "detected": self.retry_storms_detected,
                "circuit_protected": self.retry_circuit_protected,
            },
            "v2_modules": {
                "cache_hits": self.v2_cache_hits,
                "cache_misses": self.v2_cache_misses,
                "cache_hit_rate": cache_hit_rate,
                "async_events": self.v2_async_events,
                "jitter_applied": self.v2_jitter_applied,
            },
            "platinum_achieved": self.platinum_achieved,
        }


# ============================================================
# Phase Metrics (각 테스트 단계별 메트릭)
# ============================================================
@dataclass
class PhaseMetrics:
    """테스트 단계별 메트릭."""
    
    phase_name: str
    requests: int = 0
    errors: int = 0
    response_times: List[float] = field(default_factory=list)
    
    @property
    def error_rate(self) -> float:
        """에러율 계산."""
        if self.requests == 0:
            return 0.0
        return (self.errors / self.requests) * 100
    
    @property
    def avg_response_ms(self) -> float:
        """평균 응답 시간."""
        if not self.response_times:
            return 0.0
        return sum(self.response_times) / len(self.response_times)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "phase_name": self.phase_name,
            "requests": self.requests,
            "errors": self.errors,
            "error_rate_percent": self.error_rate,
            "avg_response_ms": self.avg_response_ms,
        }


# ============================================================
# 시계열 데이터 (Trend Analysis)
# ============================================================
@dataclass
class TimeseriesPoint:
    """시계열 데이터 포인트."""
    
    timestamp: str
    rps: float
    error_rate: float
    avg_response_ms: float
    event_type: Optional[str] = None  # CB_OPEN, EMERGENCY_L1, etc.
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "timestamp": self.timestamp,
            "rps": self.rps,
            "error_rate": self.error_rate,
            "avg_response_ms": self.avg_response_ms,
            "event_type": self.event_type,
        }


@dataclass
class TimeseriesData:
    """시계열 데이터 컬렉션."""
    
    points: List[TimeseriesPoint] = field(default_factory=list)
    interval_sec: int = 1  # 수집 간격
    
    def add_point(self, point: TimeseriesPoint) -> None:
        """포인트 추가."""
        self.points.append(point)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "interval_sec": self.interval_sec,
            "total_points": len(self.points),
            "points": [p.to_dict() for p in self.points],
        }


# ============================================================
# 통합 Report Data
# ============================================================
@dataclass
class ReportData:
    """
    통합 보고서 데이터 컨테이너.
    
    각 스테이지에서 사용하는 모든 메트릭을 포함합니다.
    """
    
    base: BaseMetrics
    selfhealing: Optional[SelfHealingMetrics] = None
    platinum: Optional[PlatinumMetrics] = None
    phases: List[PhaseMetrics] = field(default_factory=list)
    timeseries: Optional[TimeseriesData] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """전체 데이터를 딕셔너리로 변환."""
        result = {
            "schema_version": SCHEMA_VERSION,
            "base": self.base.to_dict(),
            "phases": [p.to_dict() for p in self.phases],
        }
        
        if self.selfhealing:
            result["selfhealing"] = self.selfhealing.to_dict()
        
        if self.platinum:
            result["platinum"] = self.platinum.to_dict()
        
        if self.timeseries:
            result["timeseries"] = self.timeseries.to_dict()
        
        return result
