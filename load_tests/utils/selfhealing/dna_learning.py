"""
Self-Learning DNA - 자가 학습 및 최적화

기능:
1. 테스트 결과 기반 패턴 분석
2. SLA 임계값 자동 조정 제안
3. DNA 설정 자동 최적화
4. 성능 추이 예측

Phase 3 구현
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import deque
import statistics
import logging

logger = logging.getLogger(__name__)


@dataclass
class DNATestSample:
    """DNA 학습용 테스트 샘플 (pytest 수집 방지를 위해 Test로 시작하지 않음)"""
    timestamp: str
    stage_name: str
    metrics: Dict[str, float]
    dna_config: Dict[str, Any]
    passed: bool


@dataclass
class LearningInsight:
    """학습 인사이트"""
    insight_type: str  # threshold, pattern, anomaly
    target_field: str
    current_value: Any
    suggested_value: Any
    confidence: float
    reasoning: str
    evidence: List[Dict[str, Any]]


@dataclass
class LearningReport:
    """학습 리포트"""
    analysis_timestamp: str
    samples_analyzed: int
    insights: List[LearningInsight]
    auto_applied: List[str]
    pending_review: List[str]


class DNALearner:
    """DNA 자가 학습기"""
    
    def __init__(
        self,
        config: Dict[str, Any] = None,
        max_history: int = 100
    ):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.min_samples = self.config.get("min_samples", 5)
        self.learning_rate = self.config.get("learning_rate", 0.1)
        self.confidence_threshold = self.config.get("confidence_threshold", 0.8)
        self.max_adjustment = self.config.get("max_adjustment_per_cycle", 0.2)
        
        self.history: deque = deque(maxlen=max_history)
        self.insights: List[LearningInsight] = []
        self.last_adjustment: Optional[datetime] = None
        self.cooldown_hours = self.config.get("cooldown_hours", 24)
    
    def record_sample(self, sample: DNATestSample):
        """테스트 샘플 기록"""
        self.history.append(sample)
        logger.debug(f"Recorded sample: {sample.stage_name} @ {sample.timestamp}")
    
    def analyze(self) -> LearningReport:
        """패턴 분석 및 인사이트 도출"""
        if len(self.history) < self.min_samples:
            return LearningReport(
                analysis_timestamp=datetime.now().isoformat(),
                samples_analyzed=len(self.history),
                insights=[],
                auto_applied=[],
                pending_review=[f"Insufficient samples ({len(self.history)}/{self.min_samples})"],
            )
        
        samples = list(self.history)
        self.insights = []
        
        # P99 분석
        p99_insight = self._analyze_p99(samples)
        if p99_insight:
            self.insights.append(p99_insight)
        
        # 에러율 분석
        error_insight = self._analyze_error_rate(samples)
        if error_insight:
            self.insights.append(error_insight)
        
        # 복구 시간 분석
        recovery_insight = self._analyze_recovery_time(samples)
        if recovery_insight:
            self.insights.append(recovery_insight)
        
        # 통과율 분석
        pass_rate_insight = self._analyze_pass_rate(samples)
        if pass_rate_insight:
            self.insights.append(pass_rate_insight)
        
        # 자동 적용 판단
        auto_applied = []
        pending_review = []
        
        for insight in self.insights:
            if insight.confidence >= self.confidence_threshold:
                if self._can_auto_apply():
                    auto_applied.append(insight.target_field)
                else:
                    pending_review.append(
                        f"{insight.target_field} (cooldown: {self.cooldown_hours}h)"
                    )
            else:
                pending_review.append(
                    f"{insight.target_field} (confidence: {insight.confidence:.1%})"
                )
        
        return LearningReport(
            analysis_timestamp=datetime.now().isoformat(),
            samples_analyzed=len(samples),
            insights=self.insights,
            auto_applied=auto_applied,
            pending_review=pending_review,
        )
    
    def _analyze_p99(
        self,
        samples: List[DNATestSample]
    ) -> Optional[LearningInsight]:
        """P99 레이턴시 분석"""
        p99_values = [
            s.metrics.get("p99", 0) for s in samples
            if "p99" in s.metrics
        ]
        
        if len(p99_values) < self.min_samples:
            return None
        
        # 현재 임계값
        current_threshold = samples[-1].dna_config.get("p99_threshold_ms", 300)
        
        # 통계 분석
        mean_p99 = statistics.mean(p99_values)
        stdev_p99 = statistics.stdev(p99_values) if len(p99_values) > 1 else 0
        
        # 추천값 계산 (평균 + 2*표준편차, 상한 20% 조정)
        suggested = mean_p99 + 2 * stdev_p99
        max_suggested = current_threshold * (1 + self.max_adjustment)
        min_suggested = current_threshold * (1 - self.max_adjustment)
        
        suggested = max(min(suggested, max_suggested), min_suggested)
        
        # 신뢰도 계산
        # 변동성이 낮을수록 신뢰도 높음
        cv = stdev_p99 / mean_p99 if mean_p99 > 0 else 0
        confidence = max(0, 1 - cv)
        
        # 유의미한 차이가 있는 경우만 제안
        if abs(suggested - current_threshold) < current_threshold * 0.05:
            return None
        
        reasoning = self._generate_reasoning(
            "p99_threshold_ms",
            current_threshold,
            suggested,
            mean_p99,
            stdev_p99,
            len(p99_values)
        )
        
        return LearningInsight(
            insight_type="threshold",
            target_field="p99_threshold_ms",
            current_value=current_threshold,
            suggested_value=round(suggested, 0),
            confidence=confidence,
            reasoning=reasoning,
            evidence=[
                {"mean": mean_p99, "stdev": stdev_p99, "samples": len(p99_values)}
            ],
        )
    
    def _analyze_error_rate(
        self,
        samples: List[DNATestSample]
    ) -> Optional[LearningInsight]:
        """에러율 분석"""
        error_rates = [
            s.metrics.get("error_rate", 0) for s in samples
            if "error_rate" in s.metrics
        ]
        
        if len(error_rates) < self.min_samples:
            return None
        
        current_threshold = samples[-1].dna_config.get("error_rate_threshold", 0.01)
        mean_error = statistics.mean(error_rates)
        
        # 에러율이 임계값의 50% 이하면 임계값 하향 제안
        if mean_error < current_threshold * 0.5:
            suggested = max(mean_error * 2, 0.001)  # 최소 0.1%
            
            return LearningInsight(
                insight_type="threshold",
                target_field="error_rate_threshold",
                current_value=current_threshold,
                suggested_value=round(suggested, 4),
                confidence=0.7,
                reasoning=(
                    f"평균 에러율({mean_error:.2%})이 현재 임계값({current_threshold:.2%})의 "
                    f"50% 미만입니다. 임계값을 {suggested:.2%}로 하향 조정하여 "
                    f"더 엄격한 품질 관리를 권장합니다."
                ),
                evidence=[{"mean_error_rate": mean_error, "samples": len(error_rates)}],
            )
        
        # 에러율이 임계값 초과 빈번 → 임계값 상향 또는 개선 필요
        exceeding_samples = sum(1 for r in error_rates if r > current_threshold)
        if exceeding_samples > len(error_rates) * 0.3:
            return LearningInsight(
                insight_type="pattern",
                target_field="error_rate",
                current_value=mean_error,
                suggested_value=None,
                confidence=0.8,
                reasoning=(
                    f"에러율이 임계값을 초과하는 빈도가 높습니다 "
                    f"({exceeding_samples}/{len(error_rates)} = {exceeding_samples/len(error_rates):.0%}). "
                    f"시스템 안정성 개선이 필요합니다."
                ),
                evidence=[
                    {"exceeding_count": exceeding_samples, "total": len(error_rates)}
                ],
            )
        
        return None
    
    def _analyze_recovery_time(
        self,
        samples: List[DNATestSample]
    ) -> Optional[LearningInsight]:
        """복구 시간 분석"""
        recovery_times = [
            s.metrics.get("recovery_time_seconds", 0) for s in samples
            if "recovery_time_seconds" in s.metrics and s.metrics.get("recovery_time_seconds", 0) > 0
        ]
        
        if len(recovery_times) < self.min_samples:
            return None
        
        mean_recovery = statistics.mean(recovery_times)
        
        # 복구 시간 트렌드 분석
        recent = recovery_times[-3:]
        older = recovery_times[:-3] if len(recovery_times) > 3 else []
        
        if older:
            trend = statistics.mean(recent) - statistics.mean(older)
            
            if trend > mean_recovery * 0.2:  # 20% 이상 증가 추세
                return LearningInsight(
                    insight_type="pattern",
                    target_field="recovery_time",
                    current_value=mean_recovery,
                    suggested_value=None,
                    confidence=0.6,
                    reasoning=(
                        f"복구 시간이 증가 추세입니다. "
                        f"이전 평균: {statistics.mean(older):.1f}s → "
                        f"최근 평균: {statistics.mean(recent):.1f}s. "
                        f"인프라 최적화를 검토하세요."
                    ),
                    evidence=[
                        {"trend": trend, "recent_avg": statistics.mean(recent)}
                    ],
                )
        
        return None
    
    def _analyze_pass_rate(
        self,
        samples: List[DNATestSample]
    ) -> Optional[LearningInsight]:
        """테스트 통과율 분석"""
        if len(samples) < self.min_samples:
            return None
        
        passed_count = sum(1 for s in samples if s.passed)
        pass_rate = passed_count / len(samples)
        
        # 통과율이 낮으면 경고
        if pass_rate < 0.8:
            return LearningInsight(
                insight_type="pattern",
                target_field="pass_rate",
                current_value=pass_rate,
                suggested_value=0.95,  # 목표
                confidence=0.9,
                reasoning=(
                    f"테스트 통과율이 {pass_rate:.0%}로 낮습니다. "
                    f"({passed_count}/{len(samples)} 통과). "
                    f"DNA 설정 또는 시스템 안정성 검토가 필요합니다."
                ),
                evidence=[
                    {"passed": passed_count, "total": len(samples)}
                ],
            )
        
        return None
    
    def _generate_reasoning(
        self,
        field: str,
        current: float,
        suggested: float,
        mean: float,
        stdev: float,
        sample_count: int
    ) -> str:
        """추론 근거 생성"""
        direction = "하향" if suggested < current else "상향"
        change = abs(suggested - current) / current * 100
        
        return (
            f"지난 {sample_count}번의 테스트 결과를 분석했습니다. "
            f"평균: {mean:.1f}ms, 표준편차: {stdev:.1f}ms. "
            f"현재 {field} 값 {current:.0f}ms을(를) "
            f"{suggested:.0f}ms로 {direction} 조정({change:.1f}% 변경)할 것을 권장합니다."
        )
    
    def _can_auto_apply(self) -> bool:
        """자동 적용 가능 여부"""
        if not self.last_adjustment:
            return True
        
        cooldown = timedelta(hours=self.cooldown_hours)
        return datetime.now() - self.last_adjustment >= cooldown
    
    def apply_suggestion(
        self,
        insight: LearningInsight,
        dna: Dict[str, Any]
    ) -> Dict[str, Any]:
        """제안 적용"""
        updated_dna = dna.copy()
        
        if insight.suggested_value is not None:
            updated_dna[insight.target_field] = insight.suggested_value
            self.last_adjustment = datetime.now()
            
            logger.info(
                f"Applied DNA update: {insight.target_field} "
                f"{insight.current_value} → {insight.suggested_value}"
            )
        
        return updated_dna
    
    def get_suggestions_summary(self) -> str:
        """제안 요약"""
        if not self.insights:
            return "현재 제안 사항이 없습니다."
        
        lines = ["## 🧠 Self-Learning Suggestions", ""]
        
        for insight in self.insights:
            icon = "🔴" if insight.confidence >= 0.8 else "🟡"
            lines.append(
                f"{icon} **{insight.target_field}**: "
                f"{insight.current_value} → {insight.suggested_value} "
                f"(신뢰도: {insight.confidence:.1%})"
            )
            lines.append(f"   {insight.reasoning}")
            lines.append("")
        
        return "\n".join(lines)
    
    def get_learning_stats(self) -> Dict[str, Any]:
        """학습 통계"""
        if not self.history:
            return {"status": "no_data"}
        
        samples = list(self.history)
        
        return {
            "total_samples": len(samples),
            "stages_analyzed": len(set(s.stage_name for s in samples)),
            "pass_rate": sum(1 for s in samples if s.passed) / len(samples),
            "insights_generated": len(self.insights),
            "last_adjustment": self.last_adjustment.isoformat() if self.last_adjustment else None,
            "can_auto_apply": self._can_auto_apply(),
        }


def generate_learning_report_section(learner: DNALearner) -> str:
    """Self-Learning 리포트 섹션 생성"""
    report = learner.analyze()
    
    lines = [
        "",
        "## 🧠 Self-Learning DNA Report",
        "",
        f"**Analyzed Samples**: {report.samples_analyzed}",
        f"**Analysis Time**: {report.analysis_timestamp}",
        "",
    ]
    
    if report.insights:
        lines.extend([
            "### Insights & Suggestions",
            "",
            "| Field | Current | Suggested | Confidence | Status |",
            "|-------|---------|-----------|------------|--------|",
        ])
        
        for insight in report.insights:
            status = "Auto-Applied" if insight.target_field in report.auto_applied else "Pending"
            suggested = insight.suggested_value if insight.suggested_value is not None else "N/A"
            lines.append(
                f"| {insight.target_field} | {insight.current_value} | "
                f"{suggested} | {insight.confidence:.1%} | {status} |"
            )
        
        lines.extend([
            "",
            "### Reasoning",
            "",
        ])
        
        for insight in report.insights:
            lines.append(f"- **{insight.target_field}**: {insight.reasoning}")
    else:
        lines.append("현재 제안 사항이 없습니다.")
    
    if report.pending_review:
        lines.extend([
            "",
            "### Pending Review",
            "",
        ])
        for item in report.pending_review:
            lines.append(f"- {item}")
    
    return "\n".join(lines)
