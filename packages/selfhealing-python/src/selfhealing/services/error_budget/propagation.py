"""
Domain Propagation Multiplier.

도메인 의존성 그래프를 기반으로 가중치를 감쇠 전파합니다.
장애 도메인으로부터의 홉 거리에 따라 가중치가 감소합니다.

Features:
- BFS 기반 홉 거리 계산
- 홉당 감쇠율 적용
- 순환 참조 방지 (visited Set, max_hops)

Usage:
    from selfhealing.services.error_budget.propagation import (
        DomainPropagationMultiplier,
        PropagationConfig,
    )
    
    propagator = DomainPropagationMultiplier(
        dependency_graph={"order": ["payment"]}
    )
    
    # payment 장애 시 order 도메인의 가중치
    multiplier = propagator.get_multiplier(
        crisis_domain="payment",
        error_domain="order",
        crisis_level=EmergencyLevel.LEVEL_3,
    )

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.error_budget.constants import (
    DEFAULT_LEVEL_MULTIPLIERS,
)
from selfhealing.settings import get_error_budget_propagation_settings


logger = logging.getLogger(__name__)


# =============================================================================
# Propagation Config
# =============================================================================

@dataclass
class PropagationConfig:
    """
    도메인 전파 설정.
    
    Attributes:
        base_multiplier: 장애 도메인 기본 가중치
        decay_per_hop: 홉당 감쇠율 (1-hop: 50% 감쇠)
        min_multiplier: 최소 가중치 (감쇠 하한)
        max_hops: 최대 전파 홉 수
        enabled: 전파 활성화 여부
    """
    
    base_multiplier: float = field(
        default_factory=lambda: get_error_budget_propagation_settings().base_multiplier
    )
    """장애 도메인 기본 가중치."""
    
    decay_per_hop: float = field(
        default_factory=lambda: get_error_budget_propagation_settings().decay_per_hop
    )
    """홉당 감쇠율 (1-hop: 50% 감쇠)."""
    
    min_multiplier: float = field(
        default_factory=lambda: get_error_budget_propagation_settings().min_multiplier
    )
    """최소 가중치 (감쇠 하한)."""
    
    max_hops: int = field(
        default_factory=lambda: get_error_budget_propagation_settings().max_hops
    )
    """
    최대 전파 홉 수.
    
    순환 참조 방지 및 성능을 위한 깊이 제한.
    리뷰 §3.2.3 반영.
    """
    
    enabled: bool = field(
        default_factory=lambda: get_error_budget_propagation_settings().enabled
    )
    """전파 활성화 여부."""
    
    @classmethod
    def from_settings(cls) -> "PropagationConfig":
        """Create config from settings."""
        settings = get_error_budget_propagation_settings()
        return cls(
            base_multiplier=settings.base_multiplier,
            decay_per_hop=settings.decay_per_hop,
            min_multiplier=settings.min_multiplier,
            max_hops=settings.max_hops,
            enabled=settings.enabled,
        )


# =============================================================================
# Propagation Result
# =============================================================================

@dataclass
class PropagationResult:
    """
    전파 계산 결과.
    
    Attributes:
        crisis_domain: 장애 발생 도메인
        error_domain: 에러 발생 도메인
        hop_distance: 홉 거리 (-1이면 연결 없음)
        multiplier: 적용 가중치
        path: 경로 (장애 도메인 → 에러 도메인)
    """
    
    crisis_domain: str
    """장애 발생 도메인."""
    
    error_domain: str
    """에러 발생 도메인."""
    
    hop_distance: int
    """홉 거리 (-1이면 연결 없음)."""
    
    multiplier: float
    """적용 가중치."""
    
    path: List[str] = field(default_factory=list)
    """경로 (장애 도메인 → 에러 도메인)."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "crisis_domain": self.crisis_domain,
            "error_domain": self.error_domain,
            "hop_distance": self.hop_distance,
            "multiplier": self.multiplier,
            "path": self.path,
        }


# =============================================================================
# Domain Propagation Multiplier
# =============================================================================

class DomainPropagationMultiplier:
    """
    도메인 의존성 기반 가중치 전파기.
    
    장애 도메인으로부터의 의존성 거리(hop)에 따라 가중치를 감쇠 적용합니다.
    
    Features:
    - BFS 기반 최단 홉 거리 계산
    - 홉 수에 따른 감쇠 가중치 적용
    - 최대 홉 수 제한 (무한 전파 방지)
    - 순환 참조 방지 (visited Set)
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.3
    """
    
    def __init__(
        self,
        config: Optional[PropagationConfig] = None,
        dependency_graph: Optional[Dict[str, List[str]]] = None,
        level_multipliers: Optional[Dict[EmergencyLevel, float]] = None,
    ):
        """
        DomainPropagationMultiplier 초기화.
        
        Args:
            config: 전파 설정
            dependency_graph: 도메인 의존성 그래프 {domain: [depends_on_domains]}
            level_multipliers: 레벨별 가중치 맵
        """
        self.config = config or PropagationConfig()
        self._graph: Dict[str, Set[str]] = {}
        self._level_multipliers = level_multipliers or dict(DEFAULT_LEVEL_MULTIPLIERS)
        self._depth_limit_counter: Optional[Any] = None
        self._cycle_detected_counter: Optional[Any] = None
        
        if dependency_graph:
            for domain, deps in dependency_graph.items():
                self._graph[domain.lower()] = set(d.lower() for d in deps)
    
    def set_dependency(self, domain: str, depends_on: List[str]) -> None:
        """
        도메인 의존성 설정.
        
        Args:
            domain: 도메인 이름
            depends_on: 의존하는 도메인 목록
        """
        self._graph[domain.lower()] = set(d.lower() for d in depends_on)
    
    def add_dependency(self, domain: str, depends_on: str) -> None:
        """
        도메인 의존성 추가.
        
        Args:
            domain: 도메인 이름
            depends_on: 의존할 도메인
        """
        domain = domain.lower()
        depends_on = depends_on.lower()
        
        if domain not in self._graph:
            self._graph[domain] = set()
        self._graph[domain].add(depends_on)
    
    def get_hop_distance(
        self,
        crisis_domain: str,
        error_domain: str,
    ) -> int:
        """
        장애 도메인으로부터의 홉 거리 계산 (BFS).
        
        순환 참조 방지:
        - visited Set으로 이미 방문한 노드 추적
        - max_hops 제한으로 무한 루프 방지
        - 리뷰 §3.2.3 반영: 깊이 제한(Depth Limit) 적용
        
        Args:
            crisis_domain: 장애 발생 도메인
            error_domain: 에러 발생 도메인
        
        Returns:
            홉 거리 (-1이면 연결 없음)
        """
        crisis = crisis_domain.lower()
        error = error_domain.lower()
        
        if crisis == error:
            return 0
        
        # BFS로 최단 거리 찾기
        visited: Set[str] = {crisis}
        queue: List[Tuple[str, int]] = [(crisis, 0)]
        depth_limit_reached = False
        
        while queue:
            current, distance = queue.pop(0)
            
            # 깊이 제한 확인 (리뷰 §3.2.3)
            if distance >= self.config.max_hops:
                depth_limit_reached = True
                continue
            
            # 역방향 탐색: 현재 도메인에 의존하는 도메인들
            for domain, deps in self._graph.items():
                if current in deps and domain not in visited:
                    if domain == error:
                        return distance + 1
                    visited.add(domain)
                    queue.append((domain, distance + 1))
        
        # 깊이 제한으로 탐색 중단된 경우 메트릭 기록
        if depth_limit_reached:
            self._record_depth_limit_metric(crisis, error)
        
        return -1  # 연결 없음
    
    def get_hop_distance_with_path(
        self,
        crisis_domain: str,
        error_domain: str,
    ) -> Tuple[int, List[str]]:
        """
        홉 거리와 경로 함께 반환.
        
        Args:
            crisis_domain: 장애 발생 도메인
            error_domain: 에러 발생 도메인
        
        Returns:
            (홉 거리, 경로 리스트)
        """
        crisis = crisis_domain.lower()
        error = error_domain.lower()
        
        if crisis == error:
            return (0, [crisis])
        
        visited: Set[str] = {crisis}
        queue: List[Tuple[str, int, List[str]]] = [(crisis, 0, [crisis])]
        
        while queue:
            current, distance, path = queue.pop(0)
            
            if distance >= self.config.max_hops:
                continue
            
            for domain, deps in self._graph.items():
                if current in deps and domain not in visited:
                    new_path = path + [domain]
                    if domain == error:
                        return (distance + 1, new_path)
                    visited.add(domain)
                    queue.append((domain, distance + 1, new_path))
        
        return (-1, [])
    
    def get_multiplier(
        self,
        crisis_domain: str,
        error_domain: str,
        crisis_level: EmergencyLevel,
    ) -> float:
        """
        도메인 전파 기반 가중치 계산.
        
        Args:
            crisis_domain: 장애 발생 도메인
            error_domain: 에러 발생 도메인
            crisis_level: Emergency 레벨
        
        Returns:
            적용할 가중치
        """
        if not self.config.enabled:
            return self._level_multipliers.get(crisis_level, self.config.base_multiplier)
        
        hop_distance = self.get_hop_distance(crisis_domain, error_domain)
        
        if hop_distance < 0:
            # 연결 없음: 최소 가중치
            return self.config.min_multiplier
        
        if hop_distance == 0:
            # 동일 도메인: 전체 가중치
            base = self._level_multipliers.get(crisis_level, self.config.base_multiplier)
            return base
        
        # 감쇠 적용: base * (decay ^ hop)
        base = self._level_multipliers.get(crisis_level, self.config.base_multiplier)
        decayed = base * (self.config.decay_per_hop ** hop_distance)
        
        final = max(decayed, self.config.min_multiplier)
        
        logger.debug(
            f"[DomainPropagation] crisis={crisis_domain}, error={error_domain}, "
            f"hop={hop_distance}, base={base:.2f}, decayed={final:.2f}"
        )
        
        return final
    
    def get_propagation_result(
        self,
        crisis_domain: str,
        error_domain: str,
        crisis_level: EmergencyLevel,
    ) -> PropagationResult:
        """
        전파 계산 결과 상세 반환.
        
        Args:
            crisis_domain: 장애 발생 도메인
            error_domain: 에러 발생 도메인
            crisis_level: Emergency 레벨
        
        Returns:
            PropagationResult
        """
        hop_distance, path = self.get_hop_distance_with_path(crisis_domain, error_domain)
        multiplier = self.get_multiplier(crisis_domain, error_domain, crisis_level)
        
        return PropagationResult(
            crisis_domain=crisis_domain.lower(),
            error_domain=error_domain.lower(),
            hop_distance=hop_distance,
            multiplier=multiplier,
            path=path,
        )
    
    def get_affected_domains(
        self,
        crisis_domain: str,
    ) -> Dict[str, float]:
        """
        장애 도메인의 영향을 받는 모든 도메인과 가중치 조회.
        
        Args:
            crisis_domain: 장애 발생 도메인
        
        Returns:
            {domain: multiplier} 딕셔너리
        """
        result = {crisis_domain.lower(): self.config.base_multiplier}
        
        for domain in self._graph.keys():
            if domain != crisis_domain.lower():
                hop = self.get_hop_distance(crisis_domain, domain)
                if hop >= 0:
                    result[domain] = self.get_multiplier(
                        crisis_domain, domain, EmergencyLevel.LEVEL_3
                    )
        
        return result
    
    def _record_depth_limit_metric(
        self,
        crisis_domain: str,
        error_domain: str,
    ) -> None:
        """깊이 제한 도달 시 메트릭 기록."""
        try:
            from prometheus_client import Counter
            
            if self._depth_limit_counter is None:
                self._depth_limit_counter = Counter(
                    "selfhealing_domain_propagation_depth_limit_reached_total",
                    "Number of times domain propagation hit depth limit",
                    ["crisis_domain", "error_domain"],
                )
            
            self._depth_limit_counter.labels(
                crisis_domain=crisis_domain,
                error_domain=error_domain,
            ).inc()
            
            logger.debug(
                f"[DomainPropagation] Depth limit reached: "
                f"crisis={crisis_domain}, error={error_domain}, "
                f"max_hops={self.config.max_hops}"
            )
        except Exception:
            pass  # 메트릭 실패는 무시
    
    def _record_cycle_detected_metric(
        self,
        domain: str,
    ) -> None:
        """순환 참조 감지 시 메트릭 기록 (리뷰 §3.2.3 권장)."""
        try:
            from prometheus_client import Counter
            
            if self._cycle_detected_counter is None:
                self._cycle_detected_counter = Counter(
                    "selfhealing_domain_propagation_cycle_detected_total",
                    "Number of times domain propagation detected a cycle",
                    ["domain"],
                )
            
            self._cycle_detected_counter.labels(domain=domain).inc()
            
            logger.warning(f"[DomainPropagation] Cycle detected at domain: {domain}")
        except Exception:
            pass


# =============================================================================
# Singleton
# =============================================================================

_domain_propagator: Optional[DomainPropagationMultiplier] = None


def get_domain_propagation_multiplier() -> DomainPropagationMultiplier:
    """DomainPropagationMultiplier 싱글톤 반환."""
    global _domain_propagator
    if _domain_propagator is None:
        _domain_propagator = DomainPropagationMultiplier()
    return _domain_propagator


def configure_domain_propagation(
    config: PropagationConfig,
    dependency_graph: Optional[Dict[str, List[str]]] = None,
) -> DomainPropagationMultiplier:
    """DomainPropagationMultiplier 설정 및 반환."""
    global _domain_propagator
    _domain_propagator = DomainPropagationMultiplier(
        config=config,
        dependency_graph=dependency_graph,
    )
    return _domain_propagator


def reset_domain_propagation() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _domain_propagator
    _domain_propagator = None
