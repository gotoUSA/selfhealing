"""
Legacy Stats Adapter - 기존 _extreme_stats 변환 어댑터.

기존 스테이지에서 사용하던 _extreme_stats 딕셔너리를 
새로운 스키마 형식으로 변환합니다.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
from datetime import datetime
from typing import Dict, Any, Tuple, List

from ..schema import (
    BaseMetrics,
    SelfHealingMetrics,
    PlatinumMetrics,
    PhaseMetrics,
)


def adapt_extreme_stats(
    stats: Dict[str, Any],
    config: Dict[str, Any],
) -> Tuple[BaseMetrics, SelfHealingMetrics, PlatinumMetrics, List[PhaseMetrics]]:
    """
    기존 _extreme_stats 딕셔너리를 새 스키마로 변환.
    
    Args:
        stats: 기존 _extreme_stats 딕셔너리
        config: 테스트 설정 (stage_id, max_users, test_duration 등)
        
    Returns:
        Tuple[BaseMetrics, SelfHealingMetrics, PlatinumMetrics, List[PhaseMetrics]]
    """
    # Phase Metrics 추출
    phases = _extract_phases(stats)
    
    # 전체 통계 계산
    total_requests, total_errors, all_response_times = _calculate_totals(phases)
    
    # Base Metrics
    base = _create_base_metrics(stats, config, total_requests, total_errors, all_response_times)
    
    # Self-Healing Metrics
    sh = _create_selfhealing_metrics(stats)
    
    # Platinum Metrics
    platinum = _create_platinum_metrics(stats, config, phases)
    
    return base, sh, platinum, phases


def _extract_phases(stats: Dict[str, Any]) -> List[PhaseMetrics]:
    """Phase별 메트릭 추출."""
    phases = []
    metrics_per_phase = stats.get("metrics_per_phase", {})
    
    for phase_name, phase_data in metrics_per_phase.items():
        if phase_data.get("requests", 0) > 0:
            phase = PhaseMetrics(
                phase_name=phase_name,
                requests=phase_data.get("requests", 0),
                errors=phase_data.get("errors", 0),
                response_times=phase_data.get("response_times", []),
            )
            phases.append(phase)
    
    return phases


def _calculate_totals(phases: List[PhaseMetrics]) -> Tuple[int, int, List[float]]:
    """전체 요청/에러/응답시간 계산."""
    total_requests = 0
    total_errors = 0
    all_response_times = []
    
    for phase in phases:
        total_requests += phase.requests
        total_errors += phase.errors
        all_response_times.extend(phase.response_times)
    
    return total_requests, total_errors, all_response_times


def _calculate_percentile(data: List[float], percentile: float) -> float:
    """퍼센타일 계산."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    index = int(len(sorted_data) * percentile / 100)
    return sorted_data[min(index, len(sorted_data) - 1)]


def _create_base_metrics(
    stats: Dict[str, Any],
    config: Dict[str, Any],
    total_requests: int,
    total_errors: int,
    all_response_times: List[float],
) -> BaseMetrics:
    """BaseMetrics 생성."""
    test_duration = config.get("test_duration", 0)
    error_rate = (total_errors / total_requests * 100) if total_requests > 0 else 0.0
    throughput = total_requests / test_duration if test_duration > 0 else 0.0
    
    # 응답시간 통계
    avg_response = sum(all_response_times) / len(all_response_times) if all_response_times else 0.0
    min_response = min(all_response_times) if all_response_times else 0.0
    max_response = max(all_response_times) if all_response_times else 0.0
    p50 = _calculate_percentile(all_response_times, 50)
    p95 = _calculate_percentile(all_response_times, 95)
    p99 = _calculate_percentile(all_response_times, 99)
    
    # SLA 기반 Pass/Fail 판정
    sla_threshold = config.get("sla_p99_threshold_ms", 250)
    error_rate_threshold = config.get("error_rate_threshold", 5.0)
    
    failure_reasons = []
    if p99 > sla_threshold:
        failure_reasons.append(f"P99 응답시간 초과: {p99:.0f}ms > {sla_threshold}ms")
    if error_rate > error_rate_threshold:
        failure_reasons.append(f"에러율 초과: {error_rate:.2f}% > {error_rate_threshold}%")
    
    passed = len(failure_reasons) == 0
    
    return BaseMetrics(
        test_name=config.get("test_name", f"Stage {config.get('stage_id', 'unknown')}"),
        stage_id=config.get("stage_id", "unknown"),
        timestamp=config.get("timestamp", datetime.now().isoformat()),
        test_duration_sec=test_duration,
        max_users=config.get("max_users", 0),
        min_users=config.get("min_users", 0),
        environment_id=config.get("environment_id"),
        chaos_intensity=config.get("chaos_intensity"),
        total_requests=total_requests,
        total_errors=total_errors,
        error_rate_percent=error_rate,
        avg_response_ms=avg_response,
        min_response_ms=min_response,
        max_response_ms=max_response,
        p50_response_ms=p50,
        p95_response_ms=p95,
        p99_response_ms=p99,
        throughput_rps=throughput,
        passed=passed,
        failure_reasons=failure_reasons,
    )


def _create_selfhealing_metrics(stats: Dict[str, Any]) -> SelfHealingMetrics:
    """SelfHealingMetrics 생성."""
    cb = stats.get("circuit_breaker", {})
    em = stats.get("emergency", {})
    eb = stats.get("error_budget", {})
    dlq = stats.get("dlq", {})
    chaos = stats.get("chaos", {})
    ks = stats.get("kill_switch", {})
    recovery = stats.get("recovery", {})
    
    recovery_latency = recovery.get("recovery_latency_seconds")
    recovery_sla_passed = recovery_latency is None or recovery_latency < 120
    
    return SelfHealingMetrics(
        # Circuit Breaker
        cb_open_count=cb.get("open_count", 0),
        cb_close_count=cb.get("close_count", 0),
        cb_half_open_count=cb.get("half_open_count", 0),
        cb_recovery_ms=cb.get("recovery_latency_ms"),
        cb_services_affected=cb.get("services_affected", []),
        
        # Emergency Mode
        emergency_triggered_count=em.get("triggered_count", 0),
        emergency_released_count=em.get("released_count", 0),
        emergency_max_level=em.get("max_level", 0),
        
        # Error Budget
        error_budget_initial=eb.get("initial_remaining"),
        error_budget_min=eb.get("min_remaining"),
        error_budget_exhausted=eb.get("exhausted", False),
        error_budget_recovered=eb.get("recovered", False),
        
        # DLQ
        dlq_max_count=dlq.get("max_count", 0),
        dlq_replay_success=dlq.get("replay_success_count", 0),
        dlq_replay_fail=dlq.get("replay_fail_count", 0),
        
        # Recovery
        recovery_latency_sec=recovery_latency,
        recovery_sla_passed=recovery_sla_passed,
        
        # Chaos Injection
        chaos_failures_injected=chaos.get("failures_injected", 0),
        chaos_cb_triggers=chaos.get("cb_triggers", 0),
        chaos_recovery_triggers=chaos.get("recovery_triggers", 0),
        
        # Kill Switch
        kill_switch_activated=ks.get("activated_count", 0),
        kill_switch_deactivated=ks.get("deactivated_count", 0),
        kill_switch_targets=ks.get("targets", []),
    )


def _create_platinum_metrics(
    stats: Dict[str, Any],
    config: Dict[str, Any],
    phases: List[PhaseMetrics],
) -> PlatinumMetrics:
    """PlatinumMetrics 생성."""
    sla = stats.get("sla_hardcap", {})
    storm = stats.get("message_storm", {})
    clock = stats.get("clock_skew", {})
    cascade = stats.get("cascading_failure", {})
    retry = stats.get("retry_storm", {})
    v2 = stats.get("v2_modules", {})
    
    sla_threshold = config.get("sla_p99_threshold_ms", 250)
    
    # 복구 후 P99 계산 (STABILIZE 단계)
    stabilize_phase = next((p for p in phases if p.phase_name.lower() == "stabilize"), None)
    recovery_p99 = _calculate_percentile(stabilize_phase.response_times, 99) if stabilize_phase else 0.0
    
    # SLA 판정
    sla_p99_passed = recovery_p99 <= sla_threshold or sla.get("p99_violations", 0) == 0
    
    # 개별 항목 판정
    main_logic_affected = storm.get("main_logic_affected", False)
    consistency_maintained = clock.get("consistency_maintained", True)
    isolation_success = cascade.get("isolation_success", True)
    circuit_protected = retry.get("circuit_protected", True)
    
    # Platinum Grade 최종 판정
    platinum_achieved = (
        sla_p99_passed and
        not main_logic_affected and
        consistency_maintained and
        isolation_success and
        circuit_protected
    )
    
    return PlatinumMetrics(
        # SLA Hard-Cap
        sla_p99_max_ms=sla.get("p99_max_ms", 0.0),
        sla_p99_violations=sla.get("p99_violations", 0),
        sla_p99_passed=sla_p99_passed,
        sla_threshold_ms=sla_threshold,
        
        # Message Storm & Backpressure
        storm_messages_injected=storm.get("messages_injected", 0),
        storm_buffer_overflow=storm.get("buffer_overflow_count", 0),
        storm_backpressure_activated=storm.get("backpressure_activated", False),
        storm_main_logic_affected=main_logic_affected,
        
        # Clock Skew
        clock_attacks_executed=clock.get("attacks_executed", 0),
        clock_max_drift_sec=clock.get("max_drift_sec", 0.0),
        clock_consistency_maintained=consistency_maintained,
        
        # Cascading Failure
        cascade_triggered=cascade.get("cascades_triggered", 0),
        cascade_max_depth=cascade.get("max_cascade_depth", 0),
        cascade_isolation_success=isolation_success,
        
        # Retry Storm
        retry_storms_detected=retry.get("storms_detected", 0),
        retry_circuit_protected=circuit_protected,
        
        # V2 Modules
        v2_cache_hits=v2.get("cache_hits", 0),
        v2_cache_misses=v2.get("cache_misses", 0),
        v2_async_events=v2.get("async_events", 0),
        v2_jitter_applied=v2.get("jitter_applied", 0),
        
        # Platinum Grade
        platinum_achieved=platinum_achieved,
    )


def create_report_from_legacy_stats(
    stats: Dict[str, Any],
    config: Dict[str, Any],
    report_type: str = "platinum",
):
    """
    레거시 stats에서 보고서 객체 생성.
    
    Args:
        stats: 기존 _extreme_stats 딕셔너리
        config: 테스트 설정
        report_type: 보고서 타입 ("base", "selfhealing", "platinum")
        
    Returns:
        보고서 객체 (SimpleReport, SelfHealingReport, 또는 PlatinumReport)
    """
    from ..base_report import SimpleReport
    from ..selfhealing_report import SelfHealingReport
    from ..platinum_report_v2 import PlatinumReport
    
    base, sh, platinum, phases = adapt_extreme_stats(stats, config)
    
    if report_type == "base":
        return SimpleReport(metrics=base, phases=phases)
    elif report_type == "selfhealing":
        return SelfHealingReport(base_metrics=base, sh_metrics=sh, phases=phases)
    else:  # platinum
        return PlatinumReport(
            base_metrics=base,
            sh_metrics=sh,
            platinum_metrics=platinum,
            phases=phases,
        )
