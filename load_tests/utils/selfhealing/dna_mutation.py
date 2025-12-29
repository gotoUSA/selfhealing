"""
Mutation DNA - 진화적 테스트

기능:
1. 기존 모듈 의도적 비활성화
2. 시스템 반응 관찰
3. 대안 솔루션 발견

Phase 3 구현
"""

from typing import Dict, List, Optional, Any, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import random
import copy
import logging

logger = logging.getLogger(__name__)


class MutationType(Enum):
    """변이 유형"""
    REMOVE_MODULE = "remove"       # 모듈 제거
    DEGRADE_MODULE = "degrade"     # 모듈 성능 저하
    REPLACE_MODULE = "replace"     # 대체 모듈로 교체
    INVERT_BEHAVIOR = "invert"     # 동작 반전


@dataclass
class MutationExperiment:
    """변이 실험"""
    experiment_id: str
    original_dna: Dict[str, Any]
    mutation_type: MutationType
    mutated_module: str
    mutated_dna: Dict[str, Any]
    
    # 결과
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    baseline_metrics: Dict[str, float] = field(default_factory=dict)
    mutated_metrics: Dict[str, float] = field(default_factory=dict)
    
    # 분석
    impact_score: float = 0.0  # 0~100, 영향도
    discovered_insights: List[str] = field(default_factory=list)
    alternative_solutions: List[str] = field(default_factory=list)


@dataclass
class MutationReport:
    """변이 실험 보고서"""
    total_experiments: int
    successful_mutations: int
    critical_modules: List[str]  # 제거 시 시스템 붕괴
    redundant_modules: List[str]  # 제거해도 영향 없음
    insights: List[str]
    suggested_improvements: List[str]


class DNAMutator:
    """DNA 변이 생성기"""
    
    # 모듈별 대안 매핑
    ALTERNATIVES = {
        "circuit_breaker": [
            "proactive_health_check",
            "rate_limiting",
            "timeout_with_retry",
        ],
        "dlq": [
            "sync_retry_with_idempotency",
            "saga_pattern",
            "event_sourcing",
        ],
        "error_budget": [
            "static_threshold",
            "adaptive_throttling",
            "manual_intervention",
        ],
        "health": [
            "synthetic_monitoring",
            "real_user_monitoring",
            "passive_health_inference",
        ],
        "retry": [
            "exponential_backoff",
            "circuit_breaker_retry",
            "queue_based_retry",
        ],
        "rate_limiter": [
            "adaptive_throttling",
            "priority_queue",
            "token_bucket",
        ],
    }
    
    def __init__(self, base_dna: Dict[str, Any]):
        self.base_dna = base_dna
        self.experiments: List[MutationExperiment] = []
    
    def generate_mutations(
        self,
        modules_to_mutate: List[str] = None,
        mutation_types: List[MutationType] = None,
    ) -> List[Dict[str, Any]]:
        """변이 DNA 생성"""
        if modules_to_mutate is None:
            modules_to_mutate = self.base_dna.get("required_modules", [])
        
        if mutation_types is None:
            mutation_types = [MutationType.REMOVE_MODULE]
        
        mutations = []
        
        for module in modules_to_mutate:
            for mut_type in mutation_types:
                mutated = self._apply_mutation(module, mut_type)
                if mutated:
                    mutations.append(mutated)
        
        return mutations
    
    def _apply_mutation(
        self,
        module: str,
        mutation_type: MutationType
    ) -> Optional[Dict[str, Any]]:
        """변이 적용"""
        mutated_dna = copy.deepcopy(self.base_dna)
        
        if mutation_type == MutationType.REMOVE_MODULE:
            # 모듈 제거
            if module in mutated_dna.get("required_modules", []):
                mutated_dna["required_modules"].remove(module)
                mutated_dna["_mutation"] = {
                    "type": "remove",
                    "module": module,
                    "description": f"Removed {module} to test system resilience",
                }
                return mutated_dna
        
        elif mutation_type == MutationType.REPLACE_MODULE:
            # 대체 모듈로 교체
            alternatives = self.ALTERNATIVES.get(module, [])
            if alternatives:
                replacement = random.choice(alternatives)
                if module in mutated_dna.get("required_modules", []):
                    mutated_dna["required_modules"].remove(module)
                    mutated_dna["required_modules"].append(replacement)
                    mutated_dna["_mutation"] = {
                        "type": "replace",
                        "original": module,
                        "replacement": replacement,
                        "description": f"Replaced {module} with {replacement}",
                    }
                    return mutated_dna
        
        elif mutation_type == MutationType.DEGRADE_MODULE:
            # 성능 저하 설정
            mutated_dna["_mutation"] = {
                "type": "degrade",
                "module": module,
                "degradation": {
                    "latency_multiplier": 2.0,
                    "error_rate_increase": 0.1,
                },
                "description": f"Degraded {module} performance",
            }
            return mutated_dna
        
        elif mutation_type == MutationType.INVERT_BEHAVIOR:
            # 동작 반전 (예: retry를 no-retry로)
            mutated_dna["_mutation"] = {
                "type": "invert",
                "module": module,
                "inverted_behavior": f"disabled_{module}",
                "description": f"Inverted {module} behavior",
            }
            return mutated_dna
        
        return None
    
    def run_experiment(
        self,
        mutated_dna: Dict[str, Any],
        test_runner: Callable[[Dict[str, Any]], Dict[str, float]],
        baseline_metrics: Dict[str, float] = None,
    ) -> MutationExperiment:
        """변이 실험 실행"""
        mutation_info = mutated_dna.get("_mutation", {})
        
        experiment = MutationExperiment(
            experiment_id=f"mut_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}",
            original_dna=self.base_dna,
            mutation_type=MutationType(mutation_info.get("type", "remove")),
            mutated_module=mutation_info.get("module", mutation_info.get("original", "unknown")),
            mutated_dna=mutated_dna,
            started_at=datetime.now().isoformat(),
            baseline_metrics=baseline_metrics or {},
        )
        
        # 테스트 실행
        try:
            experiment.mutated_metrics = test_runner(mutated_dna)
        except Exception as e:
            logger.error(f"Mutation experiment failed: {e}")
            experiment.mutated_metrics = {"error": 1.0, "error_message": str(e)}
        
        experiment.completed_at = datetime.now().isoformat()
        
        # 영향도 분석
        experiment.impact_score = self._calculate_impact(
            experiment.baseline_metrics,
            experiment.mutated_metrics
        )
        
        # 인사이트 도출
        experiment.discovered_insights = self._generate_insights(experiment)
        experiment.alternative_solutions = self._suggest_alternatives(experiment)
        
        self.experiments.append(experiment)
        return experiment
    
    def _calculate_impact(
        self,
        baseline: Dict[str, float],
        mutated: Dict[str, float]
    ) -> float:
        """영향도 계산"""
        if not baseline or not mutated:
            return 0.0
        
        # 에러 발생 시 최대 영향도
        if mutated.get("error", 0) > 0:
            return 100.0
        
        impact = 0.0
        
        # P99 변화
        if "p99" in baseline and "p99" in mutated:
            p99_change = (mutated["p99"] - baseline["p99"]) / max(baseline["p99"], 1)
            impact += min(abs(p99_change) * 30, 30)
        
        # 에러율 변화
        if "error_rate" in baseline and "error_rate" in mutated:
            err_change = mutated["error_rate"] - baseline["error_rate"]
            impact += min(err_change * 200, 40)
        
        # 가용성 변화
        if "availability" in baseline and "availability" in mutated:
            avail_change = baseline["availability"] - mutated["availability"]
            impact += min(avail_change * 100, 30)
        
        return min(impact, 100.0)
    
    def _generate_insights(
        self,
        experiment: MutationExperiment
    ) -> List[str]:
        """인사이트 도출"""
        insights = []
        
        if experiment.impact_score > 80:
            insights.append(
                f"🔴 {experiment.mutated_module}은 Critical 모듈입니다. "
                f"제거 시 시스템 영향도 {experiment.impact_score:.0f}%"
            )
        elif experiment.impact_score > 40:
            insights.append(
                f"🟡 {experiment.mutated_module}은 Important 모듈입니다. "
                f"대체 솔루션 검토 권장"
            )
        else:
            insights.append(
                f"🟢 {experiment.mutated_module}은 영향도가 낮습니다. "
                f"정말 필요한지 검토 필요"
            )
        
        # 변이 유형별 추가 인사이트
        if experiment.mutation_type == MutationType.REPLACE_MODULE:
            mutation_info = experiment.mutated_dna.get("_mutation", {})
            replacement = mutation_info.get("replacement", "")
            if replacement and experiment.impact_score < 40:
                insights.append(
                    f"✅ {replacement}로 대체 가능 - 성능 영향 미미"
                )
        
        return insights
    
    def _suggest_alternatives(
        self,
        experiment: MutationExperiment
    ) -> List[str]:
        """대안 솔루션 제안"""
        module = experiment.mutated_module
        return self.ALTERNATIVES.get(module, [])
    
    def generate_report(self) -> MutationReport:
        """전체 실험 보고서 생성"""
        critical = [
            e.mutated_module for e in self.experiments
            if e.impact_score > 80
        ]
        
        redundant = [
            e.mutated_module for e in self.experiments
            if e.impact_score < 20
        ]
        
        all_insights = []
        all_improvements = []
        
        for exp in self.experiments:
            all_insights.extend(exp.discovered_insights)
            all_improvements.extend(exp.alternative_solutions)
        
        return MutationReport(
            total_experiments=len(self.experiments),
            successful_mutations=len([e for e in self.experiments if e.mutated_metrics]),
            critical_modules=list(set(critical)),
            redundant_modules=list(set(redundant)),
            insights=list(set(all_insights)),
            suggested_improvements=list(set(all_improvements)),
        )
    
    def get_module_criticality(self) -> Dict[str, str]:
        """모듈별 중요도 분류"""
        criticality = {}
        
        for exp in self.experiments:
            module = exp.mutated_module
            if exp.impact_score > 80:
                criticality[module] = "critical"
            elif exp.impact_score > 40:
                criticality[module] = "important"
            else:
                criticality[module] = "optional"
        
        return criticality


def generate_mutation_report_section(mutator: DNAMutator) -> str:
    """Mutation DNA 리포트 섹션 생성"""
    report = mutator.generate_report()
    
    lines = [
        "",
        "## 🧬 Mutation DNA Report",
        "",
        f"**Total Experiments**: {report.total_experiments}",
        f"**Successful Mutations**: {report.successful_mutations}",
        "",
        "### Module Criticality",
        "",
        "| Module | Impact | Criticality |",
        "|--------|--------|-------------|",
    ]
    
    for exp in mutator.experiments:
        level = "🔴 Critical" if exp.impact_score > 80 else (
            "🟡 Important" if exp.impact_score > 40 else "🟢 Optional"
        )
        lines.append(f"| {exp.mutated_module} | {exp.impact_score:.0f}% | {level} |")
    
    if report.critical_modules:
        lines.extend([
            "",
            "### Critical Modules (제거 불가)",
            "",
        ])
        for module in report.critical_modules:
            lines.append(f"- `{module}`")
    
    if report.redundant_modules:
        lines.extend([
            "",
            "### Potentially Redundant Modules (검토 필요)",
            "",
        ])
        for module in report.redundant_modules:
            lines.append(f"- `{module}`")
    
    if report.insights:
        lines.extend([
            "",
            "### Insights",
            "",
        ])
        for insight in report.insights[:5]:  # 상위 5개
            lines.append(f"- {insight}")
    
    return "\n".join(lines)
