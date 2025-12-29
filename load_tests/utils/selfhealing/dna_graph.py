"""
Dependency Graph DNA - 서비스 의존성 그래프 기반 연쇄 장애 분석

Phase 2 구현: 서비스 간 의존성을 DNA에 선언하여 연쇄 장애 예측

기능:
1. 서비스 의존성 그래프 구축
2. 연쇄 장애(Cascading Failure) 영향 분석
3. Blast Radius 자동 계산
4. 장애 격리 전략 제안
5. 복구 순서 계산 (위상 정렬)

참조:
- docs/self_healing/31_DNA_ADVANCED_FEATURES.md (Section 2)
- docs/self_healing/29_STAGE_DNA_EVOLUTION_MASTER.md

비즈니스 가치: 연쇄 장애 방지로 대규모 MSA 운영 안정성 확보
"""

from typing import Dict, List, Set, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# 의존성 유형 정의
# =============================================================================

class DependencyType(Enum):
    """의존성 유형"""
    SYNC = "sync"           # 동기 호출 (강한 의존)
    ASYNC = "async"         # 비동기 호출 (약한 의존)
    CACHE = "cache"         # 캐시 의존
    DATABASE = "database"   # DB 의존
    EXTERNAL = "external"   # 외부 서비스
    EVENT = "event"         # 이벤트 기반


class ServiceTier(Enum):
    """서비스 중요도 등급"""
    CRITICAL = "critical"       # 핵심 서비스 (결제, 인증)
    STANDARD = "standard"       # 일반 서비스
    NON_CRITICAL = "non-critical"  # 비핵심 서비스 (로깅, 알림)


class IsolationStrategy(Enum):
    """장애 격리 전략"""
    CIRCUIT_BREAKER = "circuit_breaker"
    BULKHEAD = "bulkhead"
    RETRY = "retry"
    FALLBACK = "fallback"
    RATE_LIMITING = "rate_limiting"
    TIMEOUT = "timeout"


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class ServiceNode:
    """서비스 노드"""
    name: str
    tier: ServiceTier = ServiceTier.STANDARD
    dependencies: Dict[str, DependencyType] = field(default_factory=dict)
    fallback_service: Optional[str] = None
    circuit_breaker_enabled: bool = True
    timeout_ms: int = 5000
    retry_count: int = 3
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_dependency(self, target: str, dep_type: DependencyType):
        """의존성 추가"""
        self.dependencies[target] = dep_type
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier.value,
            "dependencies": {k: v.value for k, v in self.dependencies.items()},
            "fallback_service": self.fallback_service,
            "circuit_breaker_enabled": self.circuit_breaker_enabled,
            "timeout_ms": self.timeout_ms,
            "retry_count": self.retry_count,
        }


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
    impact_score: float  # 0.0 ~ 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "failed_service": self.failed_service,
            "directly_affected": self.directly_affected,
            "indirectly_affected": self.indirectly_affected,
            "total_blast_radius": self.total_blast_radius,
            "critical_services_affected": self.critical_services_affected,
            "suggested_isolation": self.suggested_isolation,
            "recovery_order": self.recovery_order,
            "impact_score": self.impact_score,
        }
    
    def to_markdown(self) -> str:
        """마크다운 리포트 생성"""
        impact_icon = "🔴" if self.impact_score > 0.7 else "🟡" if self.impact_score > 0.4 else "🟢"
        
        lines = [
            f"## 💥 Cascade Analysis: {self.failed_service}",
            "",
            f"**Impact Score**: {impact_icon} {self.impact_score:.2f}",
            f"**Total Blast Radius**: {self.total_blast_radius} services",
            "",
            "### Directly Affected",
            "",
        ]
        
        for svc in self.directly_affected:
            lines.append(f"- 🔗 {svc}")
        
        if self.indirectly_affected:
            lines.extend([
                "",
                "### Indirectly Affected (Cascade)",
                "",
            ])
            for svc in self.indirectly_affected:
                lines.append(f"- 📎 {svc}")
        
        if self.critical_services_affected:
            lines.extend([
                "",
                "### ⚠️ Critical Services Affected",
                "",
            ])
            for svc in self.critical_services_affected:
                lines.append(f"- 🚨 {svc}")
        
        lines.extend([
            "",
            "### Suggested Isolation Strategies",
            "",
        ])
        for suggestion in self.suggested_isolation:
            lines.append(f"- {suggestion}")
        
        lines.extend([
            "",
            "### Recovery Order",
            "",
        ])
        for i, svc in enumerate(self.recovery_order, 1):
            lines.append(f"{i}. {svc}")
        
        return "\n".join(lines)


@dataclass
class GraphReport:
    """의존성 그래프 리포트"""
    generated_at: str
    total_services: int
    total_dependencies: int
    critical_services: List[str]
    most_depended: List[Tuple[str, int]]  # (service, count)
    most_dependent: List[Tuple[str, int]]  # (service, count)
    circular_dependencies: List[List[str]]
    recommendations: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "total_services": self.total_services,
            "total_dependencies": self.total_dependencies,
            "critical_services": self.critical_services,
            "most_depended": self.most_depended,
            "most_dependent": self.most_dependent,
            "circular_dependencies": self.circular_dependencies,
            "recommendations": self.recommendations,
        }


# =============================================================================
# Dependency Graph
# =============================================================================

class DependencyGraph:
    """
    서비스 의존성 그래프
    
    서비스 간 의존 관계를 그래프로 관리하고
    연쇄 장애 분석, Blast Radius 계산 등을 수행합니다.
    """
    
    def __init__(self):
        self.nodes: Dict[str, ServiceNode] = {}
        self.reverse_deps: Dict[str, Set[str]] = defaultdict(set)  # 역방향 의존성
    
    def add_service(self, service: ServiceNode):
        """
        서비스 추가
        
        Args:
            service: ServiceNode 객체
        """
        self.nodes[service.name] = service
        
        # 역방향 의존성 업데이트
        for dep in service.dependencies:
            self.reverse_deps[dep].add(service.name)
    
    def add_service_from_dict(self, config: Dict[str, Any]) -> ServiceNode:
        """
        딕셔너리에서 서비스 추가
        
        Args:
            config: 서비스 설정 딕셔너리
            
        Returns:
            생성된 ServiceNode
        """
        name = config.get("name", "unknown")
        tier = ServiceTier(config.get("tier", "standard"))
        
        node = ServiceNode(
            name=name,
            tier=tier,
            fallback_service=config.get("fallback"),
            circuit_breaker_enabled=config.get("circuit_breaker", True),
            timeout_ms=config.get("timeout_ms", 5000),
            retry_count=config.get("retry_count", 3),
        )
        
        # 의존성 추가
        depends_on = config.get("depends_on", [])
        dep_types = config.get("dependency_types", {})
        
        for dep in depends_on:
            if isinstance(dep, dict):
                dep_name = dep.get("name")
                dep_type = DependencyType(dep.get("type", "sync"))
            else:
                dep_name = dep
                dep_type = DependencyType(dep_types.get(dep, "sync"))
            
            node.add_dependency(dep_name, dep_type)
        
        self.add_service(node)
        return node
    
    def get_dependents(self, service_name: str) -> Set[str]:
        """
        해당 서비스에 의존하는 서비스들 조회
        
        Args:
            service_name: 서비스 이름
            
        Returns:
            의존하는 서비스 집합
        """
        return self.reverse_deps.get(service_name, set())
    
    def get_dependencies(self, service_name: str) -> Dict[str, DependencyType]:
        """
        해당 서비스가 의존하는 서비스들 조회
        
        Args:
            service_name: 서비스 이름
            
        Returns:
            의존 서비스 딕셔너리
        """
        node = self.nodes.get(service_name)
        return node.dependencies if node else {}
    
    def analyze_cascade(self, failed_service: str) -> CascadeAnalysis:
        """
        연쇄 장애 분석
        
        특정 서비스 장애 시 영향받는 서비스들을 분석합니다.
        
        Args:
            failed_service: 장애 발생 서비스 이름
            
        Returns:
            CascadeAnalysis 객체
        """
        if failed_service not in self.nodes:
            logger.warning(f"Unknown service: {failed_service}")
            return CascadeAnalysis(
                failed_service=failed_service,
                directly_affected=[],
                indirectly_affected=[],
                total_blast_radius=0,
                critical_services_affected=[],
                suggested_isolation=[],
                recovery_order=[],
                impact_score=0.0,
            )
        
        # 1차 영향 (직접 의존)
        directly_affected = list(self.get_dependents(failed_service))
        
        # 2차+ 영향 (간접 의존) - BFS
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
            if s in self.nodes and self.nodes[s].tier == ServiceTier.CRITICAL
        ]
        
        # 격리 제안 생성
        isolation_suggestions = self._generate_isolation_suggestions(
            failed_service, directly_affected
        )
        
        # 복구 순서 계산 (위상 정렬)
        recovery_order = self._calculate_recovery_order(failed_service, visited)
        
        # Impact Score 계산
        impact_score = self._calculate_impact_score(
            failed_service, visited, critical
        )
        
        return CascadeAnalysis(
            failed_service=failed_service,
            directly_affected=directly_affected,
            indirectly_affected=indirectly_affected,
            total_blast_radius=len(visited),
            critical_services_affected=critical,
            suggested_isolation=isolation_suggestions,
            recovery_order=recovery_order,
            impact_score=impact_score,
        )
    
    def _generate_isolation_suggestions(
        self,
        failed_service: str,
        directly_affected: List[str]
    ) -> List[str]:
        """격리 제안 생성"""
        suggestions = []
        
        for service in directly_affected:
            node = self.nodes.get(service)
            if not node:
                continue
            
            dep_type = node.dependencies.get(failed_service, DependencyType.SYNC)
            
            if node.circuit_breaker_enabled:
                suggestions.append(
                    f"🔌 Circuit Breaker: {service} → {failed_service} 차단"
                )
            
            if node.fallback_service:
                suggestions.append(
                    f"🔄 Fallback: {service} → {node.fallback_service}"
                )
            
            if dep_type == DependencyType.SYNC:
                suggestions.append(
                    f"⚠️ SYNC 의존: {service}의 {failed_service} 호출을 ASYNC로 변경 검토"
                )
            
            if dep_type == DependencyType.CACHE:
                suggestions.append(
                    f"💾 Cache: {service}는 캐시 폴백으로 일시적 서비스 가능"
                )
        
        return suggestions
    
    def _calculate_recovery_order(
        self,
        failed_service: str,
        affected: Set[str]
    ) -> List[str]:
        """
        복구 순서 계산 (위상 정렬)
        
        의존성이 적은 서비스부터 복구해야 합니다.
        """
        order = [failed_service]  # 원인 서비스 먼저
        remaining = affected - {failed_service}
        
        while remaining:
            candidates = []
            
            for service in remaining:
                node = self.nodes.get(service)
                if not node:
                    continue
                
                # 현재 remaining 중에서 의존하는 서비스 수
                active_deps = sum(
                    1 for d in node.dependencies
                    if d in remaining and d != service
                )
                
                # 중요도 가중치
                tier_weight = {
                    ServiceTier.CRITICAL: 0,
                    ServiceTier.STANDARD: 1,
                    ServiceTier.NON_CRITICAL: 2,
                }.get(node.tier, 1)
                
                candidates.append((service, active_deps, tier_weight))
            
            if not candidates:
                # 남은 서비스 추가
                order.extend(remaining)
                break
            
            # 의존성 적은 순 → 중요도 높은 순 정렬
            candidates.sort(key=lambda x: (x[1], x[2]))
            next_service = candidates[0][0]
            order.append(next_service)
            remaining.remove(next_service)
        
        return order
    
    def _calculate_impact_score(
        self,
        failed_service: str,
        affected: Set[str],
        critical: List[str]
    ) -> float:
        """
        Impact Score 계산 (0.0 ~ 1.0)
        
        고려 요소:
        - 영향받는 서비스 비율
        - Critical 서비스 포함 여부
        - 장애 서비스의 중요도
        """
        if not self.nodes:
            return 0.0
        
        # 영향 범위 비율 (40%)
        coverage_ratio = len(affected) / len(self.nodes)
        coverage_score = min(coverage_ratio, 1.0) * 0.4
        
        # Critical 서비스 영향 (40%)
        if critical:
            critical_score = min(len(critical) / 3, 1.0) * 0.4
        else:
            critical_score = 0.0
        
        # 장애 서비스 중요도 (20%)
        failed_node = self.nodes.get(failed_service)
        if failed_node:
            tier_score = {
                ServiceTier.CRITICAL: 1.0,
                ServiceTier.STANDARD: 0.5,
                ServiceTier.NON_CRITICAL: 0.2,
            }.get(failed_node.tier, 0.5) * 0.2
        else:
            tier_score = 0.1
        
        return coverage_score + critical_score + tier_score
    
    def detect_circular_dependencies(self) -> List[List[str]]:
        """
        순환 의존성 감지
        
        Returns:
            순환 경로 목록
        """
        cycles = []
        visited = set()
        rec_stack = set()
        
        def dfs(node: str, path: List[str]) -> bool:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            
            node_obj = self.nodes.get(node)
            if node_obj:
                for dep in node_obj.dependencies:
                    if dep not in visited:
                        if dfs(dep, path):
                            return True
                    elif dep in rec_stack:
                        # 순환 발견
                        cycle_start = path.index(dep)
                        cycles.append(path[cycle_start:] + [dep])
                        return True
            
            path.pop()
            rec_stack.remove(node)
            return False
        
        for node in self.nodes:
            if node not in visited:
                dfs(node, [])
        
        return cycles
    
    def get_graph_report(self) -> GraphReport:
        """
        그래프 분석 리포트 생성
        
        Returns:
            GraphReport 객체
        """
        # 총 의존성 수
        total_deps = sum(
            len(node.dependencies) for node in self.nodes.values()
        )
        
        # Critical 서비스
        critical = [
            name for name, node in self.nodes.items()
            if node.tier == ServiceTier.CRITICAL
        ]
        
        # 가장 많이 의존되는 서비스 (Fan-In)
        fan_in = [
            (name, len(self.reverse_deps.get(name, set())))
            for name in self.nodes
        ]
        fan_in.sort(key=lambda x: -x[1])
        most_depended = fan_in[:5]
        
        # 가장 많이 의존하는 서비스 (Fan-Out)
        fan_out = [
            (name, len(node.dependencies))
            for name, node in self.nodes.items()
        ]
        fan_out.sort(key=lambda x: -x[1])
        most_dependent = fan_out[:5]
        
        # 순환 의존성
        cycles = self.detect_circular_dependencies()
        
        # 추천사항
        recommendations = self._generate_graph_recommendations(
            most_depended, cycles, critical
        )
        
        return GraphReport(
            generated_at=datetime.now().isoformat(),
            total_services=len(self.nodes),
            total_dependencies=total_deps,
            critical_services=critical,
            most_depended=most_depended,
            most_dependent=most_dependent,
            circular_dependencies=cycles,
            recommendations=recommendations,
        )
    
    def _generate_graph_recommendations(
        self,
        most_depended: List[Tuple[str, int]],
        cycles: List[List[str]],
        critical: List[str]
    ) -> List[str]:
        """그래프 기반 추천사항 생성"""
        recommendations = []
        
        # 높은 Fan-In 서비스
        if most_depended and most_depended[0][1] > 5:
            name, count = most_depended[0]
            recommendations.append(
                f"⚠️ {name}은 {count}개 서비스가 의존합니다. "
                f"이 서비스의 장애는 큰 영향을 미칩니다."
            )
        
        # 순환 의존성
        if cycles:
            recommendations.append(
                f"🔄 {len(cycles)}개의 순환 의존성이 발견되었습니다. "
                f"데드락 위험이 있습니다."
            )
            for cycle in cycles[:2]:
                recommendations.append(f"   - {' → '.join(cycle)}")
        
        # Critical 서비스
        if not critical:
            recommendations.append(
                "💡 Critical 서비스가 지정되지 않았습니다. "
                "핵심 서비스에 tier: critical 설정을 추가하세요."
            )
        
        return recommendations
    
    def visualize_ascii(self) -> str:
        """ASCII 시각화"""
        lines = [
            "┌" + "─" * 50 + "┐",
            "│ Service Dependency Graph" + " " * 25 + "│",
            "├" + "─" * 50 + "┤",
        ]
        
        for name, node in self.nodes.items():
            tier_icon = {
                ServiceTier.CRITICAL: "⭐",
                ServiceTier.STANDARD: "  ",
                ServiceTier.NON_CRITICAL: "  ",
            }.get(node.tier, "  ")
            
            lines.append(f"│ {tier_icon} {name:40} │")
            
            for dep, dep_type in node.dependencies.items():
                arrow = "──▶" if dep_type == DependencyType.SYNC else "- -▶"
                lines.append(f"│    {arrow} {dep} ({dep_type.value:8}) │")
        
        lines.append("└" + "─" * 50 + "┘")
        
        return "\n".join(lines)
    
    def to_mermaid(self) -> str:
        """Mermaid 다이어그램 생성"""
        lines = ["```mermaid", "graph TD"]
        
        # 노드 정의
        for name, node in self.nodes.items():
            if node.tier == ServiceTier.CRITICAL:
                lines.append(f"    {name}[({name})]")  # 원형
            else:
                lines.append(f"    {name}[{name}]")  # 사각형
        
        # 엣지 정의
        for name, node in self.nodes.items():
            for dep, dep_type in node.dependencies.items():
                if dep_type == DependencyType.SYNC:
                    lines.append(f"    {name} --> {dep}")
                else:
                    lines.append(f"    {name} -.-> {dep}")
        
        lines.append("```")
        
        return "\n".join(lines)


# =============================================================================
# DNA 통합
# =============================================================================

def parse_dna_dependencies(stage_dna: Dict[str, Any]) -> DependencyGraph:
    """
    STAGE_DNA에서 의존성 그래프 파싱
    
    Args:
        stage_dna: Stage DNA 딕셔너리
        
    Returns:
        DependencyGraph 객체
        
    예시 DNA 형식:
    ```python
    STAGE_DNA = {
        "dependencies": {
            "payment": {
                "tier": "critical",
                "depends_on": ["pg", "db"],
                "fallback": "payment_cache",
                "circuit_breaker": True,
            },
            "order": {
                "tier": "standard",
                "depends_on": ["payment", "inventory"],
            },
        }
    }
    ```
    """
    graph = DependencyGraph()
    
    deps_config = stage_dna.get("dependencies", {})
    
    for service_name, config in deps_config.items():
        # config가 딕셔너리인지 확인
        if not isinstance(config, dict):
            continue
        
        # name 필드 추가
        config_with_name = {"name": service_name, **config}
        graph.add_service_from_dict(config_with_name)
    
    # 의존 대상 서비스가 그래프에 없으면 추가
    for node in list(graph.nodes.values()):
        for dep in node.dependencies:
            if dep not in graph.nodes:
                # 기본 서비스로 추가
                graph.add_service(ServiceNode(
                    name=dep,
                    tier=ServiceTier.STANDARD,
                ))
    
    return graph


def validate_dependency_dna(deps_config: Dict[str, Any]) -> List[str]:
    """
    의존성 DNA 설정 검증
    
    Args:
        deps_config: STAGE_DNA["dependencies"] 딕셔너리
        
    Returns:
        경고 메시지 목록
    """
    warnings = []
    
    # 빈 설정 체크
    if not deps_config:
        return ["dependencies 설정이 비어 있습니다."]
    
    valid_tiers = {"critical", "standard", "non-critical"}
    
    for service_name, config in deps_config.items():
        if not isinstance(config, dict):
            warnings.append(f"{service_name}: 설정은 딕셔너리여야 합니다.")
            continue
        
        # tier 검증
        tier = config.get("tier", "standard")
        if tier not in valid_tiers:
            warnings.append(f"{service_name}: tier는 {valid_tiers} 중 하나여야 합니다.")
        
        # depends_on 검증
        depends_on = config.get("depends_on", [])
        if not isinstance(depends_on, list):
            warnings.append(f"{service_name}: depends_on은 리스트여야 합니다.")
        
        # 자기 자신 의존 체크
        if service_name in depends_on:
            warnings.append(f"{service_name}: 자기 자신에 대한 의존은 허용되지 않습니다.")
    
    return warnings


# =============================================================================
# CLI 인터페이스
# =============================================================================

def main():
    """CLI 엔트리포인트"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Dependency Graph DNA - 연쇄 장애 분석"
    )
    parser.add_argument(
        "--analyze",
        type=str,
        help="장애 분석할 서비스 이름",
    )
    parser.add_argument(
        "--format",
        choices=["text", "mermaid", "json"],
        default="text",
        help="출력 형식",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="데모 데이터로 실행",
    )
    
    args = parser.parse_args()
    
    if args.demo:
        # 데모 그래프 생성
        graph = DependencyGraph()
        
        graph.add_service(ServiceNode(
            name="gateway",
            tier=ServiceTier.CRITICAL,
            dependencies={"payment": DependencyType.SYNC, "order": DependencyType.SYNC},
        ))
        graph.add_service(ServiceNode(
            name="payment",
            tier=ServiceTier.CRITICAL,
            dependencies={"pg": DependencyType.SYNC, "db": DependencyType.SYNC},
            fallback_service="payment_cache",
        ))
        graph.add_service(ServiceNode(
            name="order",
            tier=ServiceTier.STANDARD,
            dependencies={"inventory": DependencyType.SYNC, "db": DependencyType.SYNC},
        ))
        graph.add_service(ServiceNode(
            name="inventory",
            tier=ServiceTier.STANDARD,
            dependencies={"db": DependencyType.SYNC},
        ))
        graph.add_service(ServiceNode(
            name="pg",
            tier=ServiceTier.CRITICAL,
        ))
        graph.add_service(ServiceNode(
            name="db",
            tier=ServiceTier.CRITICAL,
        ))
        graph.add_service(ServiceNode(
            name="payment_cache",
            tier=ServiceTier.NON_CRITICAL,
        ))
        
        print("\n📊 Demo Dependency Graph")
        print("=" * 60)
        
        if args.format == "mermaid":
            print(graph.to_mermaid())
        elif args.format == "json":
            report = graph.get_graph_report()
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(graph.visualize_ascii())
        
        # 장애 분석
        if args.analyze:
            analysis = graph.analyze_cascade(args.analyze)
            print("\n" + analysis.to_markdown())
        else:
            # 기본으로 payment 장애 분석
            print("\n🔥 Simulating payment service failure...")
            analysis = graph.analyze_cascade("payment")
            print(analysis.to_markdown())
    else:
        print("--demo 옵션을 사용하여 데모를 실행하세요.")
        print("예: python dna_graph.py --demo --analyze payment")


if __name__ == "__main__":
    main()
