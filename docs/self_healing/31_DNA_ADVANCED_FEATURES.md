# DNA Advanced Features: Mutation, Dependency Graph, Intelligence

📅 **작성일**: 2025-12-29  
🎯 **목적**: 진화적 테스트와 지능형 DNA 시스템 구현  
📋 **버전**: v1.0.0  
📌 **관련 문서**: [29_STAGE_DNA_EVOLUTION_MASTER.md](29_STAGE_DNA_EVOLUTION_MASTER.md)

---

## ⚠️ 기존 구현 현황 (중복 주의!)

### 이 문서의 모든 기능은 🆕 신규 구현입니다

| 기능 | 기존 구현 | 신규 개발 필요 |
|------|----------|---------------|
| **Metrics vs DNA 충돌 감지** | ❌ 없음 | ✅ 완전 신규 |
| **Dependency Graph DNA** | ❌ 없음 | ✅ 완전 신규 |
| **Mutation DNA** | ❌ 없음 | ✅ 완전 신규 |
| **Zero-Base 시나리오** | ❌ 없음 | ✅ 완전 신규 |

### 연계 가능한 기존 모듈

| 기능 | 연계 모듈 | 연계 방법 |
|------|----------|----------|
| Metrics 조회 | `observability.py` | 메트릭 데이터 가져오기 |
| Dashboard 연동 | `dashboard.py` | 결과 시각화 |
| 설정 변경 | `runtime_config.py` | 동적 설정 적용 |
| Chaos 실험 | `chaos.py` | Mutation 테스트에 활용 |

> 💡 **구현 가이드**: 이 문서의 기능들은 모두 신규이지만,
> 기존 모듈의 **데이터를 활용**하여 구현해야 합니다.

---

## 1. Metrics vs DNA 충돌 감지 (Phase 2)

### 1.1 문제 정의

```
┌─────────────────────────────────────────────────────────────────┐
│                    DNA-Metrics 불일치 시나리오                   │
├─────────────────────────────────────────────────────────────────┤
│ 상황:                                                           │
│   - STAGE_DNA 필수 모듈 100% 적용 ✅                            │
│   - DNA 검증 통과 (is_valid: True) ✅                           │
│   - 하지만 P99 SLA 300ms 목표 → 실제 450ms ❌                  │
│                                                                 │
│ 의미:                                                           │
│   "기존 Self-Healing 모듈만으로는 해결 불가능한 새로운 문제"     │
│   → 신규 기능 개발이 필요하다는 가장 강력한 신호                │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 구현 코드

```python
# load_tests/utils/selfhealing/dna_metrics.py
"""
DNA-Metrics Conflict Detection

DNA 가용성 대비 목표 달성률 분석
- DNA 100% 적용했는데 SLA 미달 → 신규 기능 필요
- DNA 50%만 적용했는데 SLA 달성 → DNA 과잉 선언
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import json
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
            self.p99 <= self.target_p99_ms and
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


# =============================================================================
# BaseReport 통합
# =============================================================================

class DNAMetricsReportMixin:
    """BaseReport에 믹스인할 DNA-Metrics 분석 기능"""
    
    def add_dna_metrics_analysis(
        self,
        stage_dna: Dict[str, Any],
        applied_modules: List[str],
    ):
        """리포트에 DNA-Metrics 분석 추가"""
        analyzer = DNAMetricsAnalyzer()
        
        # 현재 메트릭 수집 (BaseReport에서 제공)
        metrics = {
            "p50": getattr(self, "p50_latency", 0),
            "p95": getattr(self, "p95_latency", 0),
            "p99": getattr(self, "p99_latency", 0),
            "error_rate": getattr(self, "error_rate", 0),
            "throughput": getattr(self, "throughput", 0),
            "availability": getattr(self, "availability", 1.0),
        }
        
        analysis = analyzer.analyze(
            stage_name=stage_dna.get("name", "Unknown"),
            dna=stage_dna,
            metrics=metrics,
            applied_modules=applied_modules,
        )
        
        # 리포트에 섹션 추가
        self._dna_metrics_section = f"""
## DNA-Metrics Conflict Analysis

| 항목 | 값 |
|-----|-----|
| 충돌 유형 | {analysis.conflict_type.value} |
| Gap 점수 | {analysis.gap_score:.1f}/100 |
| DNA 적용률 | {analysis.dna_utilization.required_coverage:.1f}% |
| SLA 달성 | {'✅' if analysis.sla_metrics.is_sla_met else '❌'} |

### 추천사항
{"".join(f"- {r}" + chr(10) for r in analysis.recommendations)}

### 제안된 신규 기능
{"".join(f"- {f}" + chr(10) for f in analysis.suggested_new_features)}
"""
        return analysis
```

---

## 2. Dependency Graph DNA (Phase 2)

### 2.1 개념

서비스 간 의존성을 DNA에 선언하여 **연쇄 장애(Cascading Failure)** 예측

```
┌─────────────────────────────────────────────────────────────────┐
│                    Dependency Graph 예시                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│    ┌─────────┐      ┌─────────┐      ┌─────────┐               │
│    │ Gateway │─────▶│ Payment │─────▶│   PG    │               │
│    └─────────┘      └────┬────┘      └─────────┘               │
│         │                │                                      │
│         │                ▼                                      │
│         │          ┌─────────┐      ┌─────────┐                │
│         └─────────▶│ Inventory│────▶│   DB    │                │
│                    └─────────┘      └─────────┘                │
│                                                                 │
│  Payment 장애 시 영향 받는 서비스:                               │
│    - Gateway (상위)                                             │
│    - Inventory (형제)                                           │
│    → Blast Radius: 3개 서비스                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 구현 코드

```python
# load_tests/utils/selfhealing/dna_graph.py
"""
Dependency Graph DNA

서비스 의존성 그래프 기반:
1. 연쇄 장애 예측
2. Blast Radius 자동 계산
3. 장애 격리 전략 제안
"""

from typing import Dict, List, Set, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import json
import logging

logger = logging.getLogger(__name__)


class DependencyType(Enum):
    """의존성 유형"""
    SYNC = "sync"           # 동기 호출 (강한 의존)
    ASYNC = "async"         # 비동기 호출 (약한 의존)
    CACHE = "cache"         # 캐시 의존
    DATABASE = "database"   # DB 의존
    EXTERNAL = "external"   # 외부 서비스


@dataclass
class ServiceNode:
    """서비스 노드"""
    name: str
    tier: str = "standard"  # critical, standard, non-critical
    dependencies: Dict[str, DependencyType] = field(default_factory=dict)
    fallback_service: Optional[str] = None
    circuit_breaker_enabled: bool = True
    
    def add_dependency(self, target: str, dep_type: DependencyType):
        self.dependencies[target] = dep_type


@dataclass
class CascadeAnalysis:
    """연쇄 장애 분석 결과"""
    failed_service: str
    directly_affected: List[str]
    indirectly_affected: List[str]
    total_blast_radius: int
    critical_services_affected: List[str]
    suggested_isolation: List[str]
    recovery_order: List[str]


class DependencyGraph:
    """서비스 의존성 그래프"""
    
    def __init__(self):
        self.nodes: Dict[str, ServiceNode] = {}
        self.reverse_deps: Dict[str, Set[str]] = defaultdict(set)  # 역방향 의존성
    
    def add_service(self, service: ServiceNode):
        """서비스 추가"""
        self.nodes[service.name] = service
        
        # 역방향 의존성 업데이트
        for dep in service.dependencies:
            self.reverse_deps[dep].add(service.name)
    
    def get_dependents(self, service_name: str) -> Set[str]:
        """해당 서비스에 의존하는 서비스들 조회"""
        return self.reverse_deps.get(service_name, set())
    
    def analyze_cascade(self, failed_service: str) -> CascadeAnalysis:
        """연쇄 장애 분석"""
        if failed_service not in self.nodes:
            raise ValueError(f"Unknown service: {failed_service}")
        
        # BFS로 영향 범위 계산
        directly_affected = list(self.get_dependents(failed_service))
        
        # 간접 영향 (2차, 3차 영향)
        indirectly_affected = []
        visited = {failed_service} | set(directly_affected)
        queue = list(directly_affected)
        
        while queue:
            current = queue.pop(0)
            for dependent in self.get_dependents(current):
                if dependent not in visited:
                    visited.add(dependent)
                    indirectly_affected.append(dependent)
                    queue.append(dependent)
        
        # Critical 서비스 필터링
        critical = [
            s for s in visited
            if s in self.nodes and self.nodes[s].tier == "critical"
        ]
        
        # 격리 제안
        isolation_suggestions = []
        for service in directly_affected:
            node = self.nodes.get(service)
            if node:
                if node.circuit_breaker_enabled:
                    isolation_suggestions.append(
                        f"CB: {service} → {failed_service} 차단"
                    )
                if node.fallback_service:
                    isolation_suggestions.append(
                        f"Fallback: {service} → {node.fallback_service}"
                    )
        
        # 복구 순서 (의존성 역순)
        recovery_order = self._calculate_recovery_order(failed_service, visited)
        
        return CascadeAnalysis(
            failed_service=failed_service,
            directly_affected=directly_affected,
            indirectly_affected=indirectly_affected,
            total_blast_radius=len(visited),
            critical_services_affected=critical,
            suggested_isolation=isolation_suggestions,
            recovery_order=recovery_order,
        )
    
    def _calculate_recovery_order(
        self,
        failed_service: str,
        affected: Set[str]
    ) -> List[str]:
        """복구 순서 계산 (위상 정렬)"""
        # 간단한 구현: 의존성이 적은 서비스부터
        order = []
        remaining = set(affected)
        
        while remaining:
            # 현재 remaining 중 의존성이 가장 적은 것
            candidates = []
            for service in remaining:
                node = self.nodes.get(service)
                if node:
                    active_deps = sum(
                        1 for d in node.dependencies
                        if d in remaining and d != service
                    )
                    candidates.append((service, active_deps))
            
            if not candidates:
                break
            
            # 의존성 적은 순 정렬
            candidates.sort(key=lambda x: x[1])
            next_service = candidates[0][0]
            order.append(next_service)
            remaining.remove(next_service)
        
        return order
    
    def visualize_ascii(self) -> str:
        """ASCII 시각화"""
        lines = ["Service Dependency Graph", "=" * 40]
        
        for name, node in self.nodes.items():
            tier_icon = "⭐" if node.tier == "critical" else "  "
            lines.append(f"{tier_icon} {name}")
            
            for dep, dep_type in node.dependencies.items():
                arrow = "──▶" if dep_type == DependencyType.SYNC else "- -▶"
                lines.append(f"    {arrow} {dep} ({dep_type.value})")
        
        return "\n".join(lines)
    
    def to_mermaid(self) -> str:
        """Mermaid 다이어그램 생성"""
        lines = ["graph TD"]
        
        for name, node in self.nodes.items():
            # 노드 스타일
            if node.tier == "critical":
                lines.append(f"    {name}[({name})]")
            else:
                lines.append(f"    {name}[{name}]")
            
            # 엣지
            for dep, dep_type in node.dependencies.items():
                if dep_type == DependencyType.SYNC:
                    lines.append(f"    {name} --> {dep}")
                else:
                    lines.append(f"    {name} -.-> {dep}")
        
        return "\n".join(lines)


# =============================================================================
# STAGE_DNA 확장
# =============================================================================

def parse_dna_dependencies(stage_dna: Dict[str, Any]) -> DependencyGraph:
    """STAGE_DNA에서 의존성 그래프 파싱"""
    graph = DependencyGraph()
    
    # DNA의 dependencies 필드 파싱
    deps_config = stage_dna.get("dependencies", {})
    
    # 예시 DNA 형식:
    # "dependencies": {
    #     "payment": {
    #         "tier": "critical",
    #         "depends_on": ["pg", "db"],
    #         "fallback": "payment_cache"
    #     }
    # }
    
    for service_name, config in deps_config.items():
        node = ServiceNode(
            name=service_name,
            tier=config.get("tier", "standard"),
            fallback_service=config.get("fallback"),
            circuit_breaker_enabled=config.get("circuit_breaker", True),
        )
        
        for dep in config.get("depends_on", []):
            dep_type = DependencyType.SYNC
            if isinstance(dep, dict):
                dep_name = dep.get("name")
                dep_type = DependencyType(dep.get("type", "sync"))
            else:
                dep_name = dep
            
            node.add_dependency(dep_name, dep_type)
        
        graph.add_service(node)
    
    return graph


# =============================================================================
# DNA 확장 스키마
# =============================================================================

EXTENDED_DNA_SCHEMA = {
    "name": "str",
    "type": "str",
    "required_modules": "List[str]",
    "optional_modules": "List[str]",
    
    # 의존성 그래프
    "dependencies": {
        "<service_name>": {
            "tier": "critical | standard | non-critical",
            "depends_on": ["service1", "service2"],
            "dependency_types": {"service1": "sync", "service2": "async"},
            "fallback": "fallback_service_name",
            "circuit_breaker": True,
        }
    },
    
    # Blast Radius 설정
    "blast_radius": {
        "max_affected_services": 3,
        "isolation_strategy": "circuit_breaker | bulkhead | retry",
        "auto_isolate": True,
    },
}
```

---

## 3. Mutation DNA (Phase 3)

### 3.1 개념

**의도적으로 기존 기능을 제외/변형**하여 새로운 문제 해결 방법 발견

```
┌─────────────────────────────────────────────────────────────────┐
│                    Mutation DNA 동작 방식                        │
├─────────────────────────────────────────────────────────────────┤
│ 1. 정상 DNA: circuit_breaker, dlq, health                       │
│                                                                 │
│ 2. Mutation 1: circuit_breaker 제거                             │
│    → "CB 없이 장애를 막으려면 뭐가 필요할까?"                    │
│    → 발견: "선제적 헬스체크로 장애 전에 차단 가능"              │
│                                                                 │
│ 3. Mutation 2: dlq 제거                                         │
│    → "DLQ 없이 실패 복구는?"                                    │
│    → 발견: "동기식 재시도 + 멱등성 키로 대체 가능"              │
│                                                                 │
│ 결론: 기존 모듈의 한계와 대안 발견                               │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 구현 코드

```python
# load_tests/utils/selfhealing/dna_mutation.py
"""
Mutation DNA - 진화적 테스트

기능:
1. 기존 모듈 의도적 비활성화
2. 시스템 반응 관찰
3. 대안 솔루션 발견
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
            experiment_id=f"mut_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            original_dna=self.base_dna,
            mutation_type=MutationType(mutation_info.get("type", "remove")),
            mutated_module=mutation_info.get("module", "unknown"),
            mutated_dna=mutated_dna,
            started_at=datetime.now().isoformat(),
            baseline_metrics=baseline_metrics or {},
        )
        
        # 테스트 실행
        try:
            experiment.mutated_metrics = test_runner(mutated_dna)
        except Exception as e:
            logger.error(f"Mutation experiment failed: {e}")
            experiment.mutated_metrics = {"error": str(e)}
        
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
```

---

## 4. Zero-Base 시나리오 (Phase 4)

### 4.1 개념

**기존 카테고리에 속하지 않는 미분류 시나리오**를 정기적으로 수행

```
┌─────────────────────────────────────────────────────────────────┐
│                    Zero-Base 시나리오 예시                       │
├─────────────────────────────────────────────────────────────────┤
│ 기존 27개 모듈로 설명 불가능한 상황:                             │
│                                                                 │
│ 1. "AI 모델 추론 지연으로 인한 타임아웃"                        │
│    → 기존: circuit_breaker (X, 모델은 외부 서비스 아님)         │
│    → 신규: inference_timeout_handler 필요                       │
│                                                                 │
│ 2. "데이터 암호화로 인한 CPU 병목"                              │
│    → 기존: rate_limiter (X, 요청 수가 아닌 CPU 문제)            │
│    → 신규: crypto_offloader 필요                                │
│                                                                 │
│ 3. "글로벌 트래픽 불균형"                                       │
│    → 기존: load (X, 단일 리전 기준)                             │
│    → 신규: geo_aware_balancer 필요                              │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 구현 가이드

```python
# load_tests/scenarios/discovery/stage_zero_base.py
"""
Zero-Base Stage - 미분류 시나리오 탐험

이 Stage는 기존 Self-Healing 모듈로 설명되지 않는
새로운 문제를 발견하기 위한 탐험적 테스트입니다.
"""

STAGE_DNA = {
    "name": "Stage Zero - Uncharted Territory",
    "type": "discovery",  # 새로운 타입!
    
    # 의도적으로 최소한의 모듈만 사용
    "required_modules": ["health"],
    "optional_modules": [],
    
    # Zero-Base 설정
    "zero_base": {
        "enabled": True,
        "exploration_mode": "aggressive",
        "document_all_failures": True,
        "suggest_new_modules": True,
    },
    
    # 테스트 대상 (기존에 테스트되지 않은 영역)
    "exploration_targets": [
        "ml_inference_latency",
        "crypto_operations",
        "cross_region_sync",
        "cold_start_lambda",
        "data_serialization",
    ],
}


class ZeroBaseExplorer:
    """Zero-Base 탐험기"""
    
    def __init__(self, targets: List[str]):
        self.targets = targets
        self.discoveries: List[Dict[str, Any]] = []
    
    def explore(self, target: str, test_fn: Callable) -> Dict[str, Any]:
        """단일 타겟 탐험"""
        discovery = {
            "target": target,
            "timestamp": datetime.now().isoformat(),
            "existing_module_applicable": None,
            "new_module_needed": None,
            "observations": [],
        }
        
        try:
            result = test_fn()
            
            # 기존 모듈로 해결 가능한지 분석
            applicable = self._find_applicable_module(result)
            
            if applicable:
                discovery["existing_module_applicable"] = applicable
                discovery["observations"].append(
                    f"기존 {applicable} 모듈로 해결 가능"
                )
            else:
                # 신규 모듈 제안
                new_module = self._suggest_new_module(target, result)
                discovery["new_module_needed"] = new_module
                discovery["observations"].append(
                    f"신규 모듈 필요: {new_module}"
                )
        
        except Exception as e:
            discovery["error"] = str(e)
            discovery["observations"].append(f"탐험 실패: {e}")
        
        self.discoveries.append(discovery)
        return discovery
    
    def _find_applicable_module(
        self,
        result: Dict[str, Any]
    ) -> Optional[str]:
        """기존 모듈 중 적용 가능한 것 찾기"""
        from load_tests.utils.selfhealing.stage_dna import ALL_AVAILABLE_MODULES
        
        # 결과 기반 모듈 매칭 로직
        if result.get("timeout", False):
            return "circuit_breaker"
        if result.get("rate_exceeded", False):
            return "rate_limiter"
        if result.get("data_corruption", False):
            return "corruption_shield"
        
        return None
    
    def _suggest_new_module(
        self,
        target: str,
        result: Dict[str, Any]
    ) -> str:
        """신규 모듈 이름 제안"""
        # 타겟 기반 제안
        suggestions = {
            "ml_inference": "inference_optimizer",
            "crypto": "crypto_offloader",
            "cross_region": "geo_balancer",
            "cold_start": "warm_pool_manager",
            "serialization": "schema_optimizer",
        }
        
        for key, module in suggestions.items():
            if key in target:
                return module
        
        return f"{target}_handler"
    
    def generate_new_module_spec(
        self,
        discovery: Dict[str, Any]
    ) -> str:
        """신규 모듈 스펙 생성"""
        module_name = discovery.get("new_module_needed", "unknown")
        
        return f'''
# 신규 모듈 제안: {module_name}
# 발견 일시: {discovery.get("timestamp")}
# 대상: {discovery.get("target")}

class {module_name.title().replace("_", "")}Client:
    """
    자동 생성된 신규 모듈 스펙
    
    TODO: 실제 구현 필요
    """
    
    def __init__(self):
        pass
    
    def handle(self, *args, **kwargs):
        """메인 처리 로직"""
        raise NotImplementedError("구현 필요")
'''
```

---

## 5. 요약 및 다음 단계

### 5.1 구현 파일 목록

| 파일 | 기능 | Phase |
|-----|------|-------|
| `dna_metrics.py` | DNA-Metrics 충돌 감지 | Phase 2 |
| `dna_graph.py` | 의존성 그래프 | Phase 2 |
| `dna_mutation.py` | Mutation 테스트 | Phase 3 |
| `stage_zero_base.py` | Zero-Base 탐험 | Phase 4 |

### 5.2 DNA 스키마 확장

```python
STAGE_DNA = {
    # 기본 (기존)
    "name": "...",
    "type": "...",
    "required_modules": [...],
    
    # Phase 2: 의존성
    "dependencies": {...},
    
    # Phase 3: 변이
    "mutation": {
        "enabled": True,
        "modules_to_mutate": [...],
    },
    
    # Phase 4: Zero-Base
    "zero_base": {
        "enabled": False,
        "exploration_targets": [...],
    },
}
```

---

## 6. 다음 문서

- [32번 문서](32_DNA_ENTERPRISE_FEATURES.md) - FinOps, Compliance, Self-Learning
- [33번 문서](33_DNA_SAFETY_FEATURES.md) - Rollback, Blast Radius

---

**작성자**: GitHub Copilot (Claude Opus 4.5)  
**검토자**: System Architect  
**승인일**: 2025-12-29
