"""
Throttle SLA 자동 조정 규칙.

P99 레이턴시 메트릭을 기반으로 AdaptiveThrottle의
sla_warning_ms / sla_critical_ms 임계값을 자동 조정하는 규칙 정의.

DecisionEngine의 custom_rules로 주입하여 사용한다.
"""

from selfhealing.core.decision_engine import AdjustmentPriority, AdjustmentRule

# SLA Warning/Critical 임계값 자동 조정 규칙
# 메트릭: p99_latency_ms vs 현재 sla_warning_ms / sla_critical_ms 값
THROTTLE_SLA_RULES: list[AdjustmentRule] = [
    # sla_warning_ms 상향: P99가 임계값의 90% 이상이면 warning 빈도를 줄여 불필요한 limit 감소 방지
    AdjustmentRule(
        parameter="throttle_sla_warning_ms",
        metric="p99_latency_ms",
        condition=lambda current, metric: metric > current * 0.9 and metric < current,
        adjustment=lambda current, metric: min(current * 1.15, 2000),
        reason="P99 레이턴시가 SLA Warning 임계값의 90% 이상 → 임계값 상향 (과도한 warning 방지)",
        priority=AdjustmentPriority.LOW,
        min_confidence=0.6,
    ),
    # sla_warning_ms 하향: P99가 임계값의 50% 미만이면 감도 향상
    AdjustmentRule(
        parameter="throttle_sla_warning_ms",
        metric="p99_latency_ms",
        condition=lambda current, metric: metric < current * 0.5 and current > 100,
        adjustment=lambda current, metric: max(current * 0.85, 50),
        reason="P99 레이턴시가 SLA Warning 임계값의 50% 미만 → 임계값 하향 (감도 향상)",
        priority=AdjustmentPriority.LOW,
        min_confidence=0.6,
    ),
    # sla_critical_ms 상향: P99가 임계값의 85% 이상이면 Critical 도달 전 여유 확보
    AdjustmentRule(
        parameter="throttle_sla_critical_ms",
        metric="p99_latency_ms",
        condition=lambda current, metric: metric > current * 0.85 and metric < current,
        adjustment=lambda current, metric: min(current * 1.15, 5000),
        reason="P99 레이턴시가 SLA Critical 임계값의 85% 이상 → 임계값 상향",
        priority=AdjustmentPriority.MEDIUM,
        min_confidence=0.7,
    ),
]
