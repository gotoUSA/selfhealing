"""
DNA Innovation - Auto-Suggestion Engine + Cross-Stage Learning

기능:
1. Auto-Suggestion Engine: 자동 DNA 개선 제안
2. Cross-Stage Learning: Stage 간 학습 및 패턴 전파
3. Evolution Tracking: DNA 진화 추적
4. Best Practice Propagation: 모범 사례 전파

Phase 4 구현
"""

from typing import Dict, List, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from collections import defaultdict
import json
import statistics
import logging

logger = logging.getLogger(__name__)


class SuggestionType(Enum):
    """제안 유형"""
    ADD_MODULE = "add_module"              # 모듈 추가
    REMOVE_MODULE = "remove_module"        # 모듈 제거
    ADJUST_THRESHOLD = "adjust_threshold"  # 임계값 조정
    UPGRADE_TIER = "upgrade_tier"          # SLA 티어 업그레이드
    DOWNGRADE_TIER = "downgrade_tier"      # SLA 티어 다운그레이드
    ENABLE_FEATURE = "enable_feature"      # 기능 활성화
    DISABLE_FEATURE = "disable_feature"    # 기능 비활성화
    APPLY_PATTERN = "apply_pattern"        # 패턴 적용


class SuggestionPriority(Enum):
    """제안 우선순위"""
    CRITICAL = "critical"    # 즉시 적용 필요
    HIGH = "high"            # 높은 우선순위
    MEDIUM = "medium"        # 중간 우선순위
    LOW = "low"              # 낮은 우선순위
    INFO = "info"            # 정보성


@dataclass
class Suggestion:
    """개선 제안"""
    suggestion_id: str
    suggestion_type: SuggestionType
    priority: SuggestionPriority
    
    # 대상
    target_stage: str
    target_field: str
    
    # 변경 내용
    current_value: Any
    suggested_value: Any
    
    # 근거
    reasoning: str
    confidence: float  # 0-1
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    
    # 예상 효과
    expected_improvement: Dict[str, Any] = field(default_factory=dict)
    
    # 메타데이터
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source: str = "auto_suggestion_engine"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.suggestion_id,
            "type": self.suggestion_type.value,
            "priority": self.priority.value,
            "target_stage": self.target_stage,
            "target_field": self.target_field,
            "current": self.current_value,
            "suggested": self.suggested_value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


@dataclass
class StageProfile:
    """Stage 프로파일"""
    stage_name: str
    dna: Dict[str, Any]
    
    # 성능 히스토리
    metrics_history: List[Dict[str, Any]] = field(default_factory=list)
    
    # 분석 결과
    avg_p99: float = 0.0
    avg_error_rate: float = 0.0
    success_rate: float = 0.0
    
    # 패턴
    identified_patterns: List[str] = field(default_factory=list)
    
    def update_stats(self):
        """통계 업데이트"""
        if not self.metrics_history:
            return
        
        p99_values = [m.get("p99", 0) for m in self.metrics_history if "p99" in m]
        error_rates = [m.get("error_rate", 0) for m in self.metrics_history]
        passes = [m.get("passed", False) for m in self.metrics_history]
        
        self.avg_p99 = statistics.mean(p99_values) if p99_values else 0
        self.avg_error_rate = statistics.mean(error_rates) if error_rates else 0
        self.success_rate = sum(passes) / len(passes) if passes else 0


@dataclass
class LearningPattern:
    """학습된 패턴"""
    pattern_id: str
    pattern_name: str
    description: str
    
    # 조건
    trigger_conditions: Dict[str, Any]
    
    # 권장 조치
    recommended_actions: List[Dict[str, Any]]
    
    # 효과성
    times_applied: int = 0
    success_rate: float = 0.0
    avg_improvement: float = 0.0
    
    # 적용 가능 Stage
    applicable_stages: List[str] = field(default_factory=list)


@dataclass
class CrossStageLearningReport:
    """Cross-Stage 학습 보고서"""
    report_id: str
    generated_at: str
    
    # 분석된 Stage
    stages_analyzed: List[str]
    
    # 발견된 패턴
    patterns_discovered: List[LearningPattern]
    
    # 전파 권장
    propagation_recommendations: List[Dict[str, Any]]
    
    # 통계
    total_suggestions: int = 0
    high_priority_count: int = 0


class AutoSuggestionEngine:
    """자동 제안 엔진"""
    
    # SLA 티어별 기본 임계값
    TIER_THRESHOLDS = {
        "bronze": {"p99_ms": 1000, "error_rate": 0.05, "availability": 0.95},
        "silver": {"p99_ms": 500, "error_rate": 0.02, "availability": 0.99},
        "gold": {"p99_ms": 300, "error_rate": 0.01, "availability": 0.999},
        "platinum": {"p99_ms": 100, "error_rate": 0.001, "availability": 0.9999},
    }
    
    # 모듈별 권장 조합
    MODULE_COMBOS = {
        "high_availability": ["circuit_breaker", "health", "dlq", "retry"],
        "low_latency": ["circuit_breaker", "rate_limiter", "tiering"],
        "data_integrity": ["corruption_shield", "reconciliation", "dlq"],
        "observability": ["observability", "alerts", "dashboard"],
        "cost_optimized": ["tiering", "rate_limiter", "emergency"],
    }
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.suggestions: List[Suggestion] = []
        self.suggestion_counter = 0
        
        # 설정
        self.min_confidence = self.config.get("min_confidence", 0.6)
        self.min_samples = self.config.get("min_samples", 3)
    
    def analyze_stage(
        self,
        stage_name: str,
        dna: Dict[str, Any],
        metrics_history: List[Dict[str, Any]],
    ) -> List[Suggestion]:
        """Stage 분석 및 제안 생성"""
        suggestions = []
        
        if len(metrics_history) < self.min_samples:
            logger.debug(f"Insufficient samples for {stage_name}")
            return suggestions
        
        # 1. 모듈 제안
        module_suggestions = self._analyze_modules(stage_name, dna, metrics_history)
        suggestions.extend(module_suggestions)
        
        # 2. 임계값 제안
        threshold_suggestions = self._analyze_thresholds(stage_name, dna, metrics_history)
        suggestions.extend(threshold_suggestions)
        
        # 3. 티어 제안
        tier_suggestions = self._analyze_tier(stage_name, dna, metrics_history)
        suggestions.extend(tier_suggestions)
        
        # 4. 기능 제안
        feature_suggestions = self._analyze_features(stage_name, dna, metrics_history)
        suggestions.extend(feature_suggestions)
        
        # 신뢰도 필터
        suggestions = [s for s in suggestions if s.confidence >= self.min_confidence]
        
        self.suggestions.extend(suggestions)
        return suggestions
    
    def _analyze_modules(
        self,
        stage_name: str,
        dna: Dict[str, Any],
        metrics: List[Dict[str, Any]],
    ) -> List[Suggestion]:
        """모듈 분석"""
        suggestions = []
        current_modules = set(dna.get("required_modules", []))
        
        # 평균 지표 계산
        error_rates = [m.get("error_rate", 0) for m in metrics]
        avg_error = statistics.mean(error_rates) if error_rates else 0
        
        p99_values = [m.get("p99", 0) for m in metrics if "p99" in m]
        avg_p99 = statistics.mean(p99_values) if p99_values else 0
        
        # 에러율 높음 → DLQ/Circuit Breaker 추가 제안
        if avg_error > 0.02 and "dlq" not in current_modules:
            suggestions.append(self._create_suggestion(
                stage_name=stage_name,
                suggestion_type=SuggestionType.ADD_MODULE,
                priority=SuggestionPriority.HIGH,
                target_field="required_modules",
                current_value=list(current_modules),
                suggested_value=list(current_modules) + ["dlq"],
                reasoning=f"에러율 {avg_error:.1%}이 높음. DLQ로 실패 메시지 재처리 권장",
                confidence=0.85,
                evidence=[{"avg_error_rate": avg_error}],
            ))
        
        if avg_error > 0.03 and "circuit_breaker" not in current_modules:
            suggestions.append(self._create_suggestion(
                stage_name=stage_name,
                suggestion_type=SuggestionType.ADD_MODULE,
                priority=SuggestionPriority.CRITICAL,
                target_field="required_modules",
                current_value=list(current_modules),
                suggested_value=list(current_modules) + ["circuit_breaker"],
                reasoning=f"에러율 {avg_error:.1%}이 매우 높음. Circuit Breaker 필수",
                confidence=0.9,
                evidence=[{"avg_error_rate": avg_error}],
            ))
        
        # P99 높음 → Rate Limiter/Tiering 제안
        if avg_p99 > 500 and "rate_limiter" not in current_modules:
            suggestions.append(self._create_suggestion(
                stage_name=stage_name,
                suggestion_type=SuggestionType.ADD_MODULE,
                priority=SuggestionPriority.MEDIUM,
                target_field="required_modules",
                current_value=list(current_modules),
                suggested_value=list(current_modules) + ["rate_limiter"],
                reasoning=f"P99 {avg_p99:.0f}ms이 높음. Rate Limiter로 부하 제어 권장",
                confidence=0.7,
                evidence=[{"avg_p99_ms": avg_p99}],
            ))
        
        # 불필요한 모듈 제거 제안
        # 에러율이 매우 낮으면 일부 모듈 제거 가능
        if avg_error < 0.001 and "emergency" in current_modules:
            suggestions.append(self._create_suggestion(
                stage_name=stage_name,
                suggestion_type=SuggestionType.REMOVE_MODULE,
                priority=SuggestionPriority.LOW,
                target_field="required_modules",
                current_value=list(current_modules),
                suggested_value=[m for m in current_modules if m != "emergency"],
                reasoning=f"에러율 {avg_error:.3%}로 매우 낮음. emergency 모듈 비활성화 검토",
                confidence=0.6,
                evidence=[{"avg_error_rate": avg_error}],
            ))
        
        return suggestions
    
    def _analyze_thresholds(
        self,
        stage_name: str,
        dna: Dict[str, Any],
        metrics: List[Dict[str, Any]],
    ) -> List[Suggestion]:
        """임계값 분석"""
        suggestions = []
        
        # P99 임계값 분석
        current_p99_threshold = dna.get("p99_threshold_ms", 300)
        actual_p99_values = [m.get("p99", 0) for m in metrics if "p99" in m]
        
        if actual_p99_values:
            actual_p99_avg = statistics.mean(actual_p99_values)
            actual_p99_p95 = sorted(actual_p99_values)[int(len(actual_p99_values) * 0.95)] if len(actual_p99_values) >= 5 else max(actual_p99_values)
            
            # 임계값이 너무 빡빡함
            if actual_p99_p95 > current_p99_threshold * 1.2:
                suggested = int(actual_p99_p95 * 1.1)
                suggestions.append(self._create_suggestion(
                    stage_name=stage_name,
                    suggestion_type=SuggestionType.ADJUST_THRESHOLD,
                    priority=SuggestionPriority.MEDIUM,
                    target_field="p99_threshold_ms",
                    current_value=current_p99_threshold,
                    suggested_value=suggested,
                    reasoning=f"현재 P99({actual_p99_p95:.0f}ms)이 임계값({current_p99_threshold}ms)을 초과. 현실적 조정 권장",
                    confidence=0.75,
                    evidence=[{
                        "actual_p99_avg": actual_p99_avg,
                        "actual_p99_p95": actual_p99_p95,
                    }],
                ))
            
            # 임계값이 너무 여유로움
            elif actual_p99_p95 < current_p99_threshold * 0.5:
                suggested = int(actual_p99_p95 * 1.3)
                suggestions.append(self._create_suggestion(
                    stage_name=stage_name,
                    suggestion_type=SuggestionType.ADJUST_THRESHOLD,
                    priority=SuggestionPriority.LOW,
                    target_field="p99_threshold_ms",
                    current_value=current_p99_threshold,
                    suggested_value=suggested,
                    reasoning=f"현재 P99({actual_p99_p95:.0f}ms)이 임계값({current_p99_threshold}ms)보다 훨씬 낮음. 더 엄격한 SLA 설정 가능",
                    confidence=0.65,
                    evidence=[{
                        "actual_p99_avg": actual_p99_avg,
                        "actual_p99_p95": actual_p99_p95,
                    }],
                ))
        
        return suggestions
    
    def _analyze_tier(
        self,
        stage_name: str,
        dna: Dict[str, Any],
        metrics: List[Dict[str, Any]],
    ) -> List[Suggestion]:
        """SLA 티어 분석"""
        suggestions = []
        current_tier = dna.get("sla_tier", "silver")
        
        # 실제 성능 측정
        p99_values = [m.get("p99", 0) for m in metrics if "p99" in m]
        avg_p99 = statistics.mean(p99_values) if p99_values else 0
        
        error_rates = [m.get("error_rate", 0) for m in metrics]
        avg_error = statistics.mean(error_rates) if error_rates else 0
        
        # 현재 티어 요구사항 확인
        current_thresholds = self.TIER_THRESHOLDS.get(current_tier, {})
        
        # 업그레이드 가능 여부
        tiers_order = ["bronze", "silver", "gold", "platinum"]
        current_idx = tiers_order.index(current_tier) if current_tier in tiers_order else 1
        
        # 상위 티어로 업그레이드 가능한지 확인
        if current_idx < len(tiers_order) - 1:
            next_tier = tiers_order[current_idx + 1]
            next_thresholds = self.TIER_THRESHOLDS[next_tier]
            
            if (avg_p99 <= next_thresholds["p99_ms"] and 
                avg_error <= next_thresholds["error_rate"]):
                suggestions.append(self._create_suggestion(
                    stage_name=stage_name,
                    suggestion_type=SuggestionType.UPGRADE_TIER,
                    priority=SuggestionPriority.MEDIUM,
                    target_field="sla_tier",
                    current_value=current_tier,
                    suggested_value=next_tier,
                    reasoning=f"현재 성능이 {next_tier} 티어 요구사항 충족. 업그레이드 권장",
                    confidence=0.7,
                    evidence=[{
                        "avg_p99": avg_p99,
                        "avg_error_rate": avg_error,
                        "next_tier_requirements": next_thresholds,
                    }],
                ))
        
        # 다운그레이드 필요 여부
        if current_idx > 0:
            if (avg_p99 > current_thresholds.get("p99_ms", 1000) * 1.5 or
                avg_error > current_thresholds.get("error_rate", 0.05) * 2):
                prev_tier = tiers_order[current_idx - 1]
                suggestions.append(self._create_suggestion(
                    stage_name=stage_name,
                    suggestion_type=SuggestionType.DOWNGRADE_TIER,
                    priority=SuggestionPriority.HIGH,
                    target_field="sla_tier",
                    current_value=current_tier,
                    suggested_value=prev_tier,
                    reasoning=f"현재 성능이 {current_tier} 티어 요구사항 미충족. {prev_tier}로 조정 권장",
                    confidence=0.8,
                    evidence=[{
                        "avg_p99": avg_p99,
                        "avg_error_rate": avg_error,
                        "current_requirements": current_thresholds,
                    }],
                ))
        
        return suggestions
    
    def _analyze_features(
        self,
        stage_name: str,
        dna: Dict[str, Any],
        metrics: List[Dict[str, Any]],
    ) -> List[Suggestion]:
        """기능 분석"""
        suggestions = []
        
        # Self-Learning 활성화 제안
        if not dna.get("enable_self_learning", False):
            pass_rate = sum(1 for m in metrics if m.get("passed", False)) / len(metrics)
            
            if pass_rate < 0.95:
                suggestions.append(self._create_suggestion(
                    stage_name=stage_name,
                    suggestion_type=SuggestionType.ENABLE_FEATURE,
                    priority=SuggestionPriority.MEDIUM,
                    target_field="enable_self_learning",
                    current_value=False,
                    suggested_value=True,
                    reasoning=f"테스트 통과율 {pass_rate:.1%}. Self-Learning으로 자동 최적화 권장",
                    confidence=0.7,
                    evidence=[{"pass_rate": pass_rate}],
                ))
        
        return suggestions
    
    def _create_suggestion(
        self,
        stage_name: str,
        suggestion_type: SuggestionType,
        priority: SuggestionPriority,
        target_field: str,
        current_value: Any,
        suggested_value: Any,
        reasoning: str,
        confidence: float,
        evidence: List[Dict[str, Any]] = None,
    ) -> Suggestion:
        """제안 생성"""
        self.suggestion_counter += 1
        return Suggestion(
            suggestion_id=f"sug_{self.suggestion_counter:04d}",
            suggestion_type=suggestion_type,
            priority=priority,
            target_stage=stage_name,
            target_field=target_field,
            current_value=current_value,
            suggested_value=suggested_value,
            reasoning=reasoning,
            confidence=confidence,
            evidence=evidence or [],
        )
    
    def get_suggestions_by_priority(
        self,
        min_priority: SuggestionPriority = SuggestionPriority.MEDIUM,
    ) -> List[Suggestion]:
        """우선순위별 제안 조회"""
        priority_order = {
            SuggestionPriority.CRITICAL: 0,
            SuggestionPriority.HIGH: 1,
            SuggestionPriority.MEDIUM: 2,
            SuggestionPriority.LOW: 3,
            SuggestionPriority.INFO: 4,
        }
        
        min_level = priority_order.get(min_priority, 2)
        filtered = [
            s for s in self.suggestions
            if priority_order.get(s.priority, 4) <= min_level
        ]
        
        return sorted(filtered, key=lambda s: priority_order.get(s.priority, 4))


class CrossStageLearner:
    """Cross-Stage 학습기"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.stage_profiles: Dict[str, StageProfile] = {}
        self.patterns: List[LearningPattern] = []
        self.pattern_counter = 0
    
    def register_stage(
        self,
        stage_name: str,
        dna: Dict[str, Any],
    ):
        """Stage 등록"""
        self.stage_profiles[stage_name] = StageProfile(
            stage_name=stage_name,
            dna=dna,
        )
    
    def record_metrics(
        self,
        stage_name: str,
        metrics: Dict[str, Any],
    ):
        """메트릭 기록"""
        if stage_name not in self.stage_profiles:
            logger.warning(f"Unknown stage: {stage_name}")
            return
        
        self.stage_profiles[stage_name].metrics_history.append(metrics)
        self.stage_profiles[stage_name].update_stats()
    
    def discover_patterns(self) -> List[LearningPattern]:
        """패턴 발견"""
        discovered = []
        
        # 유사 성능 Stage 그룹화
        performance_groups = self._group_by_performance()
        
        for group_key, stages in performance_groups.items():
            if len(stages) < 2:
                continue
            
            # 공통 모듈 패턴
            common_modules = self._find_common_modules(stages)
            if common_modules:
                pattern = self._create_pattern(
                    name=f"common_modules_{group_key}",
                    description=f"{group_key} 성능 그룹의 공통 모듈 패턴",
                    trigger_conditions={"performance_group": group_key},
                    recommended_actions=[{
                        "action": "add_modules",
                        "modules": common_modules,
                    }],
                    applicable_stages=[s.stage_name for s in stages],
                )
                discovered.append(pattern)
        
        # 고성능 Stage 패턴 추출
        high_performers = self._get_high_performers()
        if len(high_performers) >= 2:
            hp_modules = self._find_common_modules(high_performers)
            pattern = self._create_pattern(
                name="high_performance_pattern",
                description="고성능 Stage들의 공통 설정 패턴",
                trigger_conditions={"target": "improve_performance"},
                recommended_actions=[
                    {"action": "add_modules", "modules": hp_modules},
                    {"action": "set_tier", "tier": "gold"},
                ],
                applicable_stages=[s.stage_name for s in high_performers],
            )
            pattern.success_rate = 0.9
            discovered.append(pattern)
        
        self.patterns.extend(discovered)
        return discovered
    
    def propagate_best_practices(
        self,
        source_stage: str,
        target_stages: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """모범 사례 전파"""
        if source_stage not in self.stage_profiles:
            return []
        
        source = self.stage_profiles[source_stage]
        recommendations = []
        
        target_profiles = [
            self.stage_profiles[s] for s in (target_stages or self.stage_profiles.keys())
            if s != source_stage and s in self.stage_profiles
        ]
        
        for target in target_profiles:
            # 소스가 더 좋은 성능을 보이는 경우만 전파
            if source.success_rate <= target.success_rate:
                continue
            
            # 모듈 차이 분석
            source_modules = set(source.dna.get("required_modules", []))
            target_modules = set(target.dna.get("required_modules", []))
            
            missing_modules = source_modules - target_modules
            if missing_modules:
                recommendations.append({
                    "source": source_stage,
                    "target": target.stage_name,
                    "recommendation": "add_modules",
                    "modules": list(missing_modules),
                    "expected_improvement": {
                        "success_rate": f"+{(source.success_rate - target.success_rate) * 100:.1f}%",
                    },
                })
            
            # SLA 설정 비교
            source_tier = source.dna.get("sla_tier")
            target_tier = target.dna.get("sla_tier")
            
            if source_tier and target_tier and source_tier != target_tier:
                recommendations.append({
                    "source": source_stage,
                    "target": target.stage_name,
                    "recommendation": "update_tier",
                    "current_tier": target_tier,
                    "suggested_tier": source_tier,
                })
        
        return recommendations
    
    def generate_cross_stage_report(self) -> CrossStageLearningReport:
        """Cross-Stage 학습 보고서 생성"""
        # 패턴 발견
        patterns = self.discover_patterns()
        
        # 전파 권장
        all_recommendations = []
        high_performers = self._get_high_performers()
        
        for hp in high_performers:
            recs = self.propagate_best_practices(hp.stage_name)
            all_recommendations.extend(recs)
        
        report = CrossStageLearningReport(
            report_id=f"csl_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            generated_at=datetime.now().isoformat(),
            stages_analyzed=list(self.stage_profiles.keys()),
            patterns_discovered=patterns,
            propagation_recommendations=all_recommendations,
            total_suggestions=len(all_recommendations),
            high_priority_count=len([r for r in all_recommendations if r.get("expected_improvement")]),
        )
        
        return report
    
    def _group_by_performance(self) -> Dict[str, List[StageProfile]]:
        """성능별 그룹화"""
        groups = defaultdict(list)
        
        for profile in self.stage_profiles.values():
            if profile.success_rate >= 0.98:
                groups["excellent"].append(profile)
            elif profile.success_rate >= 0.95:
                groups["good"].append(profile)
            elif profile.success_rate >= 0.90:
                groups["average"].append(profile)
            else:
                groups["poor"].append(profile)
        
        return dict(groups)
    
    def _get_high_performers(self) -> List[StageProfile]:
        """고성능 Stage 조회"""
        return [
            p for p in self.stage_profiles.values()
            if p.success_rate >= 0.95 and len(p.metrics_history) >= 3
        ]
    
    def _find_common_modules(
        self,
        profiles: List[StageProfile],
    ) -> List[str]:
        """공통 모듈 찾기"""
        if not profiles:
            return []
        
        common = set(profiles[0].dna.get("required_modules", []))
        for profile in profiles[1:]:
            common &= set(profile.dna.get("required_modules", []))
        
        return list(common)
    
    def _create_pattern(
        self,
        name: str,
        description: str,
        trigger_conditions: Dict[str, Any],
        recommended_actions: List[Dict[str, Any]],
        applicable_stages: List[str] = None,
    ) -> LearningPattern:
        """패턴 생성"""
        self.pattern_counter += 1
        return LearningPattern(
            pattern_id=f"pat_{self.pattern_counter:04d}",
            pattern_name=name,
            description=description,
            trigger_conditions=trigger_conditions,
            recommended_actions=recommended_actions,
            applicable_stages=applicable_stages or [],
        )


class DNAEvolutionTracker:
    """DNA 진화 추적기"""
    
    def __init__(self):
        self.evolution_history: List[Dict[str, Any]] = []
        self.version_counter: Dict[str, int] = defaultdict(int)
    
    def track_change(
        self,
        stage_name: str,
        old_dna: Dict[str, Any],
        new_dna: Dict[str, Any],
        change_reason: str,
        change_source: str = "manual",
    ):
        """변경 추적"""
        self.version_counter[stage_name] += 1
        
        # 변경 사항 계산
        changes = self._calculate_changes(old_dna, new_dna)
        
        record = {
            "stage_name": stage_name,
            "version": self.version_counter[stage_name],
            "timestamp": datetime.now().isoformat(),
            "change_reason": change_reason,
            "change_source": change_source,
            "changes": changes,
            "old_dna_hash": hash(json.dumps(old_dna, sort_keys=True)),
            "new_dna_hash": hash(json.dumps(new_dna, sort_keys=True)),
        }
        
        self.evolution_history.append(record)
        logger.info(f"Tracked DNA change for {stage_name}: v{record['version']}")
        
        return record
    
    def _calculate_changes(
        self,
        old_dna: Dict[str, Any],
        new_dna: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """변경 사항 계산"""
        changes = []
        
        all_keys = set(old_dna.keys()) | set(new_dna.keys())
        
        for key in all_keys:
            old_val = old_dna.get(key)
            new_val = new_dna.get(key)
            
            if old_val != new_val:
                changes.append({
                    "field": key,
                    "old_value": old_val,
                    "new_value": new_val,
                    "change_type": self._get_change_type(old_val, new_val),
                })
        
        return changes
    
    def _get_change_type(self, old_val: Any, new_val: Any) -> str:
        """변경 유형 결정"""
        if old_val is None:
            return "added"
        if new_val is None:
            return "removed"
        return "modified"
    
    def get_evolution_timeline(
        self,
        stage_name: str,
    ) -> List[Dict[str, Any]]:
        """진화 타임라인 조회"""
        return [
            r for r in self.evolution_history
            if r["stage_name"] == stage_name
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 조회"""
        return {
            "total_changes": len(self.evolution_history),
            "stages_tracked": len(self.version_counter),
            "versions_per_stage": dict(self.version_counter),
        }


class DNAInnovationManager:
    """DNA Innovation 통합 관리자"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.suggestion_engine = AutoSuggestionEngine(config)
        self.cross_stage_learner = CrossStageLearner(config)
        self.evolution_tracker = DNAEvolutionTracker()
    
    def analyze_and_suggest(
        self,
        stage_name: str,
        dna: Dict[str, Any],
        metrics_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """분석 및 제안"""
        # Stage 등록
        self.cross_stage_learner.register_stage(stage_name, dna)
        
        # 메트릭 기록
        for metrics in metrics_history:
            self.cross_stage_learner.record_metrics(stage_name, metrics)
        
        # 제안 생성
        suggestions = self.suggestion_engine.analyze_stage(
            stage_name, dna, metrics_history
        )
        
        return {
            "stage_name": stage_name,
            "suggestions_count": len(suggestions),
            "suggestions": [s.to_dict() for s in suggestions],
            "high_priority": [
                s.to_dict() for s in suggestions
                if s.priority in [SuggestionPriority.CRITICAL, SuggestionPriority.HIGH]
            ],
        }
    
    def run_cross_stage_analysis(self) -> CrossStageLearningReport:
        """Cross-Stage 분석 실행"""
        return self.cross_stage_learner.generate_cross_stage_report()
    
    def apply_suggestion(
        self,
        suggestion: Suggestion,
        dna: Dict[str, Any],
    ) -> Dict[str, Any]:
        """제안 적용"""
        old_dna = dna.copy()
        new_dna = dna.copy()
        
        # 제안 유형별 적용
        if suggestion.suggestion_type == SuggestionType.ADD_MODULE:
            new_dna["required_modules"] = suggestion.suggested_value
        
        elif suggestion.suggestion_type == SuggestionType.REMOVE_MODULE:
            new_dna["required_modules"] = suggestion.suggested_value
        
        elif suggestion.suggestion_type == SuggestionType.ADJUST_THRESHOLD:
            new_dna[suggestion.target_field] = suggestion.suggested_value
        
        elif suggestion.suggestion_type in [SuggestionType.UPGRADE_TIER, SuggestionType.DOWNGRADE_TIER]:
            new_dna["sla_tier"] = suggestion.suggested_value
        
        elif suggestion.suggestion_type == SuggestionType.ENABLE_FEATURE:
            new_dna[suggestion.target_field] = True
        
        elif suggestion.suggestion_type == SuggestionType.DISABLE_FEATURE:
            new_dna[suggestion.target_field] = False
        
        # 진화 추적
        self.evolution_tracker.track_change(
            stage_name=suggestion.target_stage,
            old_dna=old_dna,
            new_dna=new_dna,
            change_reason=suggestion.reasoning,
            change_source="auto_suggestion",
        )
        
        return new_dna
