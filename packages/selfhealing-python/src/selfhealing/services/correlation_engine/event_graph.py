"""
Event Graph — 이벤트 DAG 자동 구축.

EventBus에서 발생하는 이벤트를 시간순으로 수집하고,
BlastRadiusService의 서비스 의존성 그래프와 교차하여
방향성 비순환 그래프(DAG)를 자동 구축한다.

이 DAG는 Root Cause Ranker와 Incident Timeline의 입력이 된다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# =============================================================================
# 상수 정의
# =============================================================================

# 5단계 Evidence 타입별 인과관계 확신도
CONFIDENCE_CORRELATION_ID = 0.95
"""동일 correlation_id를 공유하는 이벤트 간 확신도"""

CONFIDENCE_CONTEXTUAL = 0.90
"""동일 service_name + 서로 다른 event_type 간 확신도"""

CONFIDENCE_DEPENDENCY = 0.85
"""서비스 의존성(upstream → downstream) 기반 확신도"""

CO_OCCURRENCE_CONFIDENCE_MULTIPLIER = 0.7
"""CoOccurrenceTracker 점수에 곱하는 가중치"""

CO_OCCURRENCE_MIN_SCORE = 0.5
"""Co-occurrence 점수가 이 값 초과일 때만 엣지 생성"""

TEMPORAL_MIN_CONFIDENCE = 0.3
"""시간 근접성 기반 최소 확신도"""

TRANSITIVE_REDUCTION_KEEP_THRESHOLD = 0.9
"""전이적 축소 시 이 이상의 confidence를 가진 직접 엣지는 유지"""

# Evidence 타입 문자열 상수
EVIDENCE_CORRELATION_ID = "correlation_id"
EVIDENCE_CONTEXTUAL = "contextual"
EVIDENCE_DEPENDENCY = "dependency"
EVIDENCE_CO_OCCURRENCE = "co_occurrence"
EVIDENCE_TEMPORAL = "temporal"

# DAG 제한 설정
DEFAULT_MAX_EVENTS_PER_DAG = 200
"""단일 DAG에 포함 가능한 최대 이벤트 수"""

DEFAULT_MIN_CONFIDENCE = 0.4
"""이 값 미만의 엣지는 제거"""


# =============================================================================
# EventNode — DAG의 노드
# =============================================================================


@dataclass(frozen=True)
class EventNode:
    """DAG의 노드 — 하나의 이벤트 발생 인스턴스."""

    event_id: str
    """고유 식별자 (UUID)"""

    event_type: str
    """EventType.value (예: 'circuit_breaker_opened')"""

    service_name: str
    """이벤트가 발생한 서비스"""

    timestamp: float
    """Unix timestamp"""

    data: dict
    """원본 event.data (읽기 전용)"""

    correlation_id: str | None
    """분산 추적 ID"""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EventNode):
            return NotImplemented
        return self.event_id == other.event_id

    def __hash__(self) -> int:
        return hash(self.event_id)


# =============================================================================
# CausalEdge — DAG의 엣지
# =============================================================================


@dataclass(frozen=True)
class CausalEdge:
    """DAG의 엣지 — 인과관계 방향."""

    source: EventNode
    """원인 이벤트"""

    target: EventNode
    """결과 이벤트"""

    confidence: float
    """인과관계 확신도 (0.0 ~ 1.0)"""

    evidence_type: str
    """'correlation_id' | 'contextual' | 'dependency' | 'co_occurrence' | 'temporal'"""

    time_gap_seconds: float
    """source → target 시간 간격"""


# =============================================================================
# EventDAG — 인과관계 그래프
# =============================================================================


@dataclass
class EventDAG:
    """특정 인시던트 윈도우의 인과관계 그래프."""

    incident_id: str
    window_start: float
    window_end: float
    nodes: dict[str, EventNode] = field(default_factory=dict)
    """event_id → EventNode"""
    edges: list[CausalEdge] = field(default_factory=list)
    root_nodes: list[EventNode] = field(default_factory=list)
    """진입차수(in-degree) = 0인 노드들"""
    leaf_nodes: list[EventNode] = field(default_factory=list)
    """진출차수(out-degree) = 0인 노드들"""

    def get_ancestors(self, node: EventNode) -> list[EventNode]:
        """특정 노드의 모든 조상(선행) 노드를 반환한다."""
        ancestors: set[str] = set()
        # 역방향 인접 리스트: target → [source ...]
        reverse_adj: dict[str, list[str]] = {eid: [] for eid in self.nodes}
        for edge in self.edges:
            reverse_adj[edge.target.event_id].append(edge.source.event_id)

        stack = list(reverse_adj.get(node.event_id, []))
        while stack:
            current = stack.pop()
            if current in ancestors:
                continue
            ancestors.add(current)
            stack.extend(reverse_adj.get(current, []))

        return [self.nodes[eid] for eid in ancestors if eid in self.nodes]

    def get_descendants(self, node: EventNode) -> list[EventNode]:
        """특정 노드의 모든 자손(후행) 노드를 반환한다."""
        descendants: set[str] = set()
        forward_adj: dict[str, list[str]] = {eid: [] for eid in self.nodes}
        for edge in self.edges:
            forward_adj[edge.source.event_id].append(edge.target.event_id)

        stack = list(forward_adj.get(node.event_id, []))
        while stack:
            current = stack.pop()
            if current in descendants:
                continue
            descendants.add(current)
            stack.extend(forward_adj.get(current, []))

        return [self.nodes[eid] for eid in descendants if eid in self.nodes]

    def get_critical_path(self) -> list[EventNode]:
        """DAG에서 가장 긴 경로(노드 수 기준)를 반환한다.

        동일 길이 경로가 여러 개면 시간순 + event_id 기준 결정적으로 선택한다.
        """
        if not self.nodes:
            return []

        forward_adj: dict[str, list[str]] = {eid: [] for eid in self.nodes}
        for edge in self.edges:
            forward_adj[edge.source.event_id].append(edge.target.event_id)

        # 각 노드에서 시작하는 최장 경로를 DFS + 메모이제이션으로 계산
        memo: dict[str, list[str]] = {}

        def _longest_path_from(eid: str) -> list[str]:
            if eid in memo:
                return memo[eid]

            children = forward_adj.get(eid, [])
            if not children:
                memo[eid] = [eid]
                return memo[eid]

            best_child_path: list[str] = []
            for child in sorted(children):
                child_path = _longest_path_from(child)
                if len(child_path) > len(best_child_path):
                    best_child_path = child_path
                elif len(child_path) == len(best_child_path) and child_path < best_child_path:
                    best_child_path = child_path

            memo[eid] = [eid] + best_child_path
            return memo[eid]

        best_path: list[str] = []
        for eid in sorted(self.nodes.keys()):
            path = _longest_path_from(eid)
            if len(path) > len(best_path):
                best_path = path
            elif len(path) == len(best_path) and path < best_path:
                best_path = path

        return [self.nodes[eid] for eid in best_path]

    def get_connected_components(self) -> list[EventDAG]:
        """단절된 서브그래프를 개별 EventDAG로 분리한다."""
        adj: dict[str, set[str]] = {eid: set() for eid in self.nodes}
        for edge in self.edges:
            adj[edge.source.event_id].add(edge.target.event_id)
            adj[edge.target.event_id].add(edge.source.event_id)

        visited: set[str] = set()
        components: list[set[str]] = []

        for eid in self.nodes:
            if eid not in visited:
                component: set[str] = set()
                stack = [eid]
                while stack:
                    current = stack.pop()
                    if current in visited:
                        continue
                    visited.add(current)
                    component.add(current)
                    stack.extend(adj[current] - visited)
                components.append(component)

        return [self._sub_dag(c) for c in components]

    def _sub_dag(self, node_ids: set[str]) -> EventDAG:
        """노드 ID 집합으로 서브 DAG를 생성한다."""
        sub_nodes = {eid: self.nodes[eid] for eid in node_ids if eid in self.nodes}
        sub_edges = [e for e in self.edges if e.source.event_id in node_ids and e.target.event_id in node_ids]

        target_ids = {e.target.event_id for e in sub_edges}
        source_ids = {e.source.event_id for e in sub_edges}

        sub_root_nodes = [n for n in sub_nodes.values() if n.event_id not in target_ids]
        sub_leaf_nodes = [n for n in sub_nodes.values() if n.event_id not in source_ids]

        timestamps = [n.timestamp for n in sub_nodes.values()]
        return EventDAG(
            incident_id=self.incident_id,
            window_start=min(timestamps) if timestamps else self.window_start,
            window_end=max(timestamps) if timestamps else self.window_end,
            nodes=sub_nodes,
            edges=sub_edges,
            root_nodes=sub_root_nodes,
            leaf_nodes=sub_leaf_nodes,
        )

    def to_dict(self) -> dict:
        """JSON 직렬화 (Postmortem/Dashboard용)."""
        components = self.get_connected_components()
        return {
            "incident_id": self.incident_id,
            "window": {
                "start": self.window_start,
                "end": self.window_end,
            },
            "component_count": len(components),
            "components": [
                {
                    "root": comp.root_nodes[0].event_id if comp.root_nodes else None,
                    "node_count": len(comp.nodes),
                    "services": sorted({n.service_name for n in comp.nodes.values()}),
                }
                for comp in components
            ],
            "nodes": [
                {
                    "event_id": n.event_id,
                    "event_type": n.event_type,
                    "service_name": n.service_name,
                    "timestamp": n.timestamp,
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "source": e.source.event_id,
                    "target": e.target.event_id,
                    "confidence": e.confidence,
                    "evidence_type": e.evidence_type,
                    "time_gap_seconds": e.time_gap_seconds,
                }
                for e in self.edges
            ],
            "root_causes": [n.event_id for n in self.root_nodes],
            "leaf_effects": [n.event_id for n in self.leaf_nodes],
        }

    @staticmethod
    def create_empty(incident_id: str = "") -> EventDAG:
        """빈 DAG를 생성한다."""
        if not incident_id:
            incident_id = f"inc_{uuid.uuid4().hex[:12]}"
        return EventDAG(
            incident_id=incident_id,
            window_start=0.0,
            window_end=0.0,
        )
