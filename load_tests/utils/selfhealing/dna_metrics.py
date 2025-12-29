"""
DNA-Metrics Conflict Detection

DNA 가용성 대비 목표 달성률 분석
- DNA 100% 적용했는데 SLA 미달 → 신규 기능 필요
- DNA 50%만 적용했는데 SLA 달성 → DNA 과잉 선언

Phase 3 구현
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ConflictType(Enum):
    """충돌 유형"""
    DNA_SUFFICIENT_SLA_FAIL = "dna_ok_sla_fail"      # DNA 충분, SLA 실패
    DNA_INSUFFICIENT_SLA_OK = "dna_fail_sla_ok"      # DNA 부족, SLA 성공
    DNA_INSUFFICIENT_SLA_FAIL = "dna_fail_sla_fail"  # DNA 부족, SLA 실패
    DNA_SUFFICIENT_SLA_OK = "dna_ok_sla_ok"          # 이상적 상태


@dataclass
class SLAMetrics:
    """SLA 메트릭"""
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_rate: float
    throughput_rps: float
    availability: float
    
    # 목표값
    target_p99_ms: float = 300.0
    target_error_rate: float = 0.01
    target_availability: float = 0.999
    
    @property
    def is_sla_met(self) -> bool:
        """SLA 충족 여부"""
        return (
            self.p99_ms <= self.target_p99_ms and
            self.error_rate <= self.target_error_rate and
            self.availability >= self.target_availability
        )


@dataclass
class DNAUtilization:
    """DNA 활용률"""
    required_modules: List[str]
    applied_modules: List[str]
    optional_modules: List[str]
    active_optional: List[str]
    
    @property
    def required_coverage(self) -> float:
        """필수 모듈 적용률"""
        if not self.required_modules:
            return 100.0
        applied = set(self.applied_modules) & set(self.required_modules)
        return len(applied) / len(self.required_modules) * 100
    
    @property
    def optional_usage(self) -> float:
        """선택 모듈 활용률"""
        if not self.optional_modules:
            return 0.0
        active = set(self.active_optional) & set(self.optional_modules)
        return len(active) / len(self.optional_modules) * 100


@dataclass
class ConflictAnalysis:
    """충돌 분석 결과"""
    stage_name: str
    analysis_timestamp: str
    conflict_type: ConflictType
    dna_utilization: DNAUtilization
    sla_metrics: SLAMetrics
    
    # 분석 결과
    gap_score: float  # 0~100, 높을수록 갭이 큼
    recommendations: List[str]
    suggested_new_features: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "timestamp": self.analysis_timestamp,
            "conflict_type": self.conflict_type.value,
            "gap_score": self.gap_score,
            "dna_coverage": self.dna_utilization.required_coverage,
            "sla_met": self.sla_metrics.is_sla_met,
            "recommendations": self.recommendations,
            "suggested_features": self.suggested_new_features,
        }


class DNAMetricsAnalyzer:
    """DNA-Metrics 충돌 분석기"""
    
    # 신규 기능 제안 룰
    FEATURE_SUGGESTIONS = {
        "high_latency": [
            "adaptive_caching",
            "connection_pooling",
            "async_processing",
            "edge_computing",
        ],
        "high_error_rate": [
            "retry_with_backoff",
            "circuit_breaker_tuning",
            "graceful_degradation",
            "fallback_service",
        ],
        "low_throughput": [
            "horizontal_scaling",
            "load_balancing",
            "queue_based_processing",
            "batch_optimization",
        ],
        "availability": [
            "multi_region_failover",
            "health_check_optimization",
            "warm_standby",
            "chaos_engineering",
        ],
    }
    
    def __init__(self):
        self.history: List[ConflictAnalysis] = []
    
    def analyze(
        self,
        stage_name: str,
        dna: Dict[str, Any],
        metrics: Dict[str, Any],
        applied_modules: List[str],
    ) -> ConflictAnalysis:
        """DNA-Metrics 충돌 분석"""
        
        # DNA 활용률 계산
        dna_util = DNAUtilization(
            required_modules=dna.get("required_modules", []),
            applied_modules=applied_modules,
            optional_modules=dna.get("optional_modules", []),
            active_optional=[m for m in applied_modules if m in dna.get("optional_modules", [])],
        )
        
        # SLA 메트릭 파싱
        sla = SLAMetrics(
            p50_ms=metrics.get("p50", 0),
            p95_ms=metrics.get("p95", 0),
            p99_ms=metrics.get("p99", 0),
            error_rate=metrics.get("error_rate", 0),
            throughput_rps=metrics.get("throughput", 0),
            availability=metrics.get("availability", 1.0),
            target_p99_ms=dna.get("p99_threshold_ms", 300),
            target_error_rate=dna.get("error_rate_threshold", 0.01),
            target_availability=dna.get("availability_threshold", 0.999),
        )
        
        # 충돌 유형 판별
        dna_ok = dna_util.required_coverage >= 100
        sla_ok = sla.is_sla_met
        
        if dna_ok and sla_ok:
            conflict_type = ConflictType.DNA_SUFFICIENT_SLA_OK
        elif dna_ok and not sla_ok:
            conflict_type = ConflictType.DNA_SUFFICIENT_SLA_FAIL
        elif not dna_ok and sla_ok:
            conflict_type = ConflictType.DNA_INSUFFICIENT_SLA_OK
        else:
            conflict_type = ConflictType.DNA_INSUFFICIENT_SLA_FAIL
        
        # Gap 점수 계산 (높을수록 문제)
        gap_score = self._calculate_gap_score(dna_util, sla)
        
        # 추천사항 생성
        recommendations, suggested_features = self._generate_recommendations(
            conflict_type, dna_util, sla
        )
        
        analysis = ConflictAnalysis(
            stage_name=stage_name,
            analysis_timestamp=datetime.now().isoformat(),
            conflict_type=conflict_type,
            dna_utilization=dna_util,
            sla_metrics=sla,
            gap_score=gap_score,
            recommendations=recommendations,
            suggested_new_features=suggested_features,
        )
        
        self.history.append(analysis)
        return analysis
    
    def _calculate_gap_score(
        self,
        dna: DNAUtilization,
        sla: SLAMetrics
    ) -> float:
        """Gap 점수 계산"""
        score = 0.0
        
        # DNA 미적용 패널티
        if dna.required_coverage < 100:
            score += (100 - dna.required_coverage) * 0.3
        
        # SLA 미달 패널티
        if sla.p99_ms > sla.target_p99_ms:
            ratio = sla.p99_ms / sla.target_p99_ms
            score += min(ratio * 20, 40)  # 최대 40점
        
        if sla.error_rate > sla.target_error_rate:
            ratio = sla.error_rate / max(sla.target_error_rate, 0.001)
            score += min(ratio * 15, 30)  # 최대 30점
        
        if sla.availability < sla.target_availability:
            gap = (sla.target_availability - sla.availability) * 1000
            score += min(gap * 10, 30)  # 최대 30점
        
        return min(score, 100.0)
    
    def _generate_recommendations(
        self,
        conflict_type: ConflictType,
        dna: DNAUtilization,
        sla: SLAMetrics
    ) -> Tuple[List[str], List[str]]:
        """추천사항 및 신규 기능 제안 생성"""
        recommendations = []
        suggested_features = []
        
        if conflict_type == ConflictType.DNA_SUFFICIENT_SLA_FAIL:
            recommendations.append(
                "🔴 DNA 100% 적용했으나 SLA 미달 - 신규 기능 개발 필요"
            )
            
            # 문제 유형별 기능 제안
            if sla.p99_ms > sla.target_p99_ms:
                recommendations.append(
                    f"  - P99 {sla.p99_ms:.0f}ms > 목표 {sla.target_p99_ms:.0f}ms"
                )
                suggested_features.extend(self.FEATURE_SUGGESTIONS["high_latency"])
            
            if sla.error_rate > sla.target_error_rate:
                recommendations.append(
                    f"  - 에러율 {sla.error_rate*100:.2f}% > 목표 {sla.target_error_rate*100:.2f}%"
                )
                suggested_features.extend(self.FEATURE_SUGGESTIONS["high_error_rate"])
            
            if sla.availability < sla.target_availability:
                recommendations.append(
                    f"  - 가용성 {sla.availability*100:.3f}% < 목표 {sla.target_availability*100:.3f}%"
                )
                suggested_features.extend(self.FEATURE_SUGGESTIONS["availability"])
        
        elif conflict_type == ConflictType.DNA_INSUFFICIENT_SLA_OK:
            recommendations.append(
                "🟡 DNA 미적용 모듈이 있으나 SLA 달성 - DNA 과잉 선언 가능성"
            )
            missing = set(dna.required_modules) - set(dna.applied_modules)
            recommendations.append(
                f"  - 미적용 모듈: {', '.join(missing)}"
            )
            recommendations.append(
                "  - 해당 모듈이 정말 필요한지 검토 권장"
            )
        
        elif conflict_type == ConflictType.DNA_INSUFFICIENT_SLA_FAIL:
            recommendations.append(
                "🔴 DNA 미적용 + SLA 미달 - 즉시 DNA 적용 필요"
            )
            missing = set(dna.required_modules) - set(dna.applied_modules)
            for module in missing:
                recommendations.append(f"  - {module} 모듈 적용 필요")
        
        else:  # DNA_SUFFICIENT_SLA_OK
            recommendations.append("✅ 이상적 상태 - DNA 적용 및 SLA 모두 달성")
        
        return recommendations, list(set(suggested_features))
    
    def get_trend_analysis(self, last_n: int = 10) -> Dict[str, Any]:
        """추이 분석"""
        if len(self.history) < 2:
            return {"trend": "insufficient_data"}
        
        recent = self.history[-last_n:]
        
        # 평균 Gap 점수 추이
        gap_scores = [a.gap_score for a in recent]
        avg_gap = sum(gap_scores) / len(gap_scores)
        
        # SLA 달성률 추이
        sla_met_count = sum(1 for a in recent if a.sla_metrics.is_sla_met)
        sla_success_rate = sla_met_count / len(recent) * 100
        
        # 가장 자주 제안되는 신규 기능
        all_suggestions = []
        for a in recent:
            all_suggestions.extend(a.suggested_new_features)
        
        from collections import Counter
        top_suggestions = Counter(all_suggestions).most_common(5)
        
        return {
            "period": f"Last {len(recent)} analyses",
            "average_gap_score": avg_gap,
            "sla_success_rate": sla_success_rate,
            "top_suggested_features": top_suggestions,
            "trend": "improving" if gap_scores[-1] < gap_scores[0] else "degrading",
        }


def generate_metrics_report_section(analyzer: DNAMetricsAnalyzer) -> str:
    """DNA-Metrics 리포트 섹션 생성"""
    if not analyzer.history:
        return "## DNA-Metrics Analysis\n\n분석 데이터가 없습니다."
    
    latest = analyzer.history[-1]
    
    lines = [
        "",
        "## 📊 DNA-Metrics Conflict Analysis",
        "",
        f"**Stage**: {latest.stage_name}",
        f"**Analyzed**: {latest.analysis_timestamp}",
        "",
        "### Summary",
        "",
        "| 항목 | 값 |",
        "|-----|-----|",
        f"| 충돌 유형 | {latest.conflict_type.value} |",
        f"| Gap 점수 | {latest.gap_score:.1f}/100 |",
        f"| DNA 적용률 | {latest.dna_utilization.required_coverage:.1f}% |",
        f"| SLA 달성 | {'✅' if latest.sla_metrics.is_sla_met else '❌'} |",
        "",
        "### Recommendations",
        "",
    ]
    
    for rec in latest.recommendations:
        lines.append(f"- {rec}")
    
    if latest.suggested_new_features:
        lines.extend([
            "",
            "### Suggested New Features",
            "",
        ])
        for feature in latest.suggested_new_features:
            lines.append(f"- `{feature}`")
    
    return "\n".join(lines)
