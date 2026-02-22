"""
Event Graph Builder — 이벤트 DAG 구축 엔진.

시간 윈도우 내 이벤트를 수집하여 5단계 Evidence 평가를 통해
방향성 비순환 그래프(DAG)를 구축한다.

Evidence 우선순위:
  1. Correlation ID 일치 (confidence=0.95)
  1.5. Contextual Matching — 동일 service_name (confidence=0.90)
  2. 서비스 의존성 + 시간순 (confidence=0.85)
  3. Co-occurrence 이력 (confidence=score×0.7)
  4. 시간 근접성 Fallback (confidence=max(0.3, 1.0-gap/window))
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog

from selfhealing.services.blast_radius.service import BlastRadiusService
from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CoOccurrenceTracker,
)
from selfhealing.services.correlation_engine.event_graph import (
    CO_OCCURRENCE_CONFIDENCE_MULTIPLIER,
    CO_OCCURRENCE_MIN_SCORE,
    CONFIDENCE_CONTEXTUAL,
    CONFIDENCE_CORRELATION_ID,
    CONFIDENCE_DEPENDENCY,
    DEFAULT_MAX_EVENTS_PER_DAG,
    DEFAULT_MAX_GRAPH_DEPTH,
    DEFAULT_MIN_CONFIDENCE,
    EVIDENCE_CO_OCCURRENCE,
    EVIDENCE_CONTEXTUAL,
    EVIDENCE_CORRELATION_ID,
    EVIDENCE_DEPENDENCY,
    EVIDENCE_TEMPORAL,
    TEMPORAL_MIN_CONFIDENCE,
    TRANSITIVE_REDUCTION_KEEP_THRESHOLD,
    CausalEdge,
    EventDAG,
    EventNode,
)
from selfhealing.services.event_bus.bus import SelfHealingEvent

logger = structlog.get_logger()


class EventGraphBuilder:
    """이벤트 DAG를 구축하는 빌더.

    BlastRadiusService의 서비스 의존성(정적)과
    이벤트 발생 시간(동적)을 교차하여 인과관계 DAG를 생성한다.
    """

    def __init__(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
        *,
        max_events_per_dag: int = DEFAULT_MAX_EVENTS_PER_DAG,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_graph_depth: int = DEFAULT_MAX_GRAPH_DEPTH,
    ) -> None:
        self._blast_radius = blast_radius_service
        self._co_occurrence = co_occurrence_tracker
        self._max_events_per_dag = max_events_per_dag
        self._min_confidence = min_confidence
        self._max_graph_depth = max_graph_depth

    def build_dag(
        self,
        events: list[SelfHealingEvent],
        window_seconds: float,
    ) -> EventDAG:
        """시간 윈도우 내 이벤트로부터 DAG를 구축한다.

        Args:
            events: SelfHealingEvent 목록
            window_seconds: 인과관계 판단 시간 윈도우 (초)

        Returns:
            구축된 EventDAG
        """
        if not events:
            return EventDAG.create_empty()

        # 최대 이벤트 수 절삭 (최근 N개)
        if len(events) > self._max_events_per_dag:
            events = sorted(
                events,
                key=lambda e: e.timestamp.timestamp() if isinstance(e.timestamp, datetime) else e.timestamp,
            )
            events = events[-self._max_events_per_dag :]

        # 1) 이벤트 → EventNode 변환
        nodes = self._create_nodes(events)
        sorted_nodes = sorted(
            nodes.values(),
            key=lambda n: (n.timestamp, n.event_id),
        )

        # 2) 모든 노드 쌍에 대해 엣지 후보 생성
        edge_candidates: list[CausalEdge] = []
        for i, source in enumerate(sorted_nodes):
            for target in sorted_nodes[i + 1 :]:
                edge = self._evaluate_causation(source, target, window_seconds)
                if edge and edge.confidence >= self._min_confidence:
                    edge_candidates.append(edge)

        # 3) 시간순 보장으로 자연스럽게 DAG (순환 없음)
        edges = edge_candidates

        # 4) 전이적 축소 (Transitive Reduction)
        edges = self._transitive_reduction(nodes, edges)

        # 4.5) max_graph_depth 제한 — OOM 방지 (§12.1)
        edges = self._enforce_max_depth(nodes, edges)

        # 5) root/leaf 노드 식별
        root_nodes = self._find_roots(nodes, edges)
        leaf_nodes = self._find_leaves(nodes, edges)

        incident_id = self._generate_incident_id(sorted_nodes)

        return EventDAG(
            incident_id=incident_id,
            window_start=sorted_nodes[0].timestamp,
            window_end=sorted_nodes[-1].timestamp,
            nodes=nodes,
            edges=edges,
            root_nodes=root_nodes,
            leaf_nodes=leaf_nodes,
        )

    def _create_nodes(
        self,
        events: list[SelfHealingEvent],
    ) -> dict[str, EventNode]:
        """SelfHealingEvent 목록을 EventNode dict로 변환한다."""
        nodes: dict[str, EventNode] = {}
        for event in events:
            event_id = str(uuid.uuid4())
            timestamp = event.timestamp.timestamp() if isinstance(event.timestamp, datetime) else float(event.timestamp)
            node = EventNode(
                event_id=event_id,
                event_type=event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
                service_name=event.source,
                timestamp=timestamp,
                data=dict(event.data) if event.data else {},
                correlation_id=event.correlation_id,
            )
            nodes[event_id] = node
        return nodes

    def _evaluate_causation(
        self,
        source: EventNode,
        target: EventNode,
        window_seconds: float,
    ) -> CausalEdge | None:
        """5단계 Evidence로 인과관계를 평가한다.

        Args:
            source: 원인 후보 노드
            target: 결과 후보 노드
            window_seconds: 시간 윈도우

        Returns:
            CausalEdge 또는 인과관계 없으면 None
        """
        time_gap = target.timestamp - source.timestamp
        # time_gap < 0은 역인과 (불가), time_gap == 0은 허용 (§11.2)
        if time_gap < 0 or time_gap > window_seconds:
            return None

        # Priority 1: Correlation ID 일치
        if source.correlation_id and source.correlation_id == target.correlation_id:
            return CausalEdge(
                source,
                target,
                CONFIDENCE_CORRELATION_ID,
                EVIDENCE_CORRELATION_ID,
                time_gap,
            )

        # Priority 1.5: Contextual Matching (동일 service_name + 다른 event_type)
        if not source.correlation_id or not target.correlation_id:
            source_svc = source.data.get("service_name", "")
            target_svc = target.data.get("service_name", "")
            if source_svc and source_svc == target_svc and source.event_type != target.event_type:
                return CausalEdge(
                    source,
                    target,
                    CONFIDENCE_CONTEXTUAL,
                    EVIDENCE_CONTEXTUAL,
                    time_gap,
                )

        # Priority 2: 서비스 의존성 (upstream → downstream)
        if source.service_name != target.service_name:
            deps = self._blast_radius.get_dependencies(target.service_name)
            if deps:
                upstream_services = [d.source_service for d in deps["upstream"]]
                if source.service_name in upstream_services:
                    return CausalEdge(
                        source,
                        target,
                        CONFIDENCE_DEPENDENCY,
                        EVIDENCE_DEPENDENCY,
                        time_gap,
                    )

        # Priority 3: Co-occurrence 이력
        co_score = self._co_occurrence.get_pair_score(
            source.event_type,
            target.event_type,
        )
        if co_score is not None and co_score > CO_OCCURRENCE_MIN_SCORE:
            confidence = co_score * CO_OCCURRENCE_CONFIDENCE_MULTIPLIER
            if confidence >= self._min_confidence:
                return CausalEdge(
                    source,
                    target,
                    confidence,
                    EVIDENCE_CO_OCCURRENCE,
                    time_gap,
                )

        # Priority 4: 시간 근접성 (Fallback)
        # time_gap = 0인 쌍에서는 시간 근접성 미적용 (§11.2)
        if time_gap == 0:
            return None

        if window_seconds > 0:
            temporal_confidence = max(
                TEMPORAL_MIN_CONFIDENCE,
                1.0 - time_gap / window_seconds,
            )
        else:
            temporal_confidence = TEMPORAL_MIN_CONFIDENCE

        if temporal_confidence >= self._min_confidence:
            return CausalEdge(
                source,
                target,
                temporal_confidence,
                EVIDENCE_TEMPORAL,
                time_gap,
            )

        return None

    def _transitive_reduction(
        self,
        nodes: dict[str, EventNode],
        edges: list[CausalEdge],
    ) -> list[CausalEdge]:
        """전이적 축소: 간접 경로가 존재하면 직접 엣지를 제거한다.

        단, confidence >= 0.9인 직접 엣지는 유지한다 (강한 직접 인과관계).
        """
        if not edges:
            return edges

        # 순방향 인접 리스트 구축
        forward_adj: dict[str, set[str]] = {eid: set() for eid in nodes}
        for edge in edges:
            forward_adj[edge.source.event_id].add(edge.target.event_id)

        # 도달성 검사: source에서 target으로 직접 엣지를 제외하고 도달 가능한지
        def _is_reachable_without_direct(src_id: str, tgt_id: str) -> bool:
            """src에서 tgt로 직접 엣지를 거치지 않고 도달 가능한지 DFS로 확인."""
            visited: set[str] = set()
            stack = [neighbor for neighbor in forward_adj.get(src_id, set()) if neighbor != tgt_id]
            while stack:
                current = stack.pop()
                if current == tgt_id:
                    return True
                if current in visited:
                    continue
                visited.add(current)
                stack.extend(forward_adj.get(current, set()) - visited)
            return False

        kept_edges: list[CausalEdge] = []
        for edge in edges:
            # 높은 confidence(≥ 0.9) 엣지는 항상 유지
            if edge.confidence >= TRANSITIVE_REDUCTION_KEEP_THRESHOLD:
                kept_edges.append(edge)
                continue

            # 간접 경로가 존재하면 제거
            if _is_reachable_without_direct(
                edge.source.event_id,
                edge.target.event_id,
            ):
                logger.debug(
                    "[EventGraphBuilder] Transitive reduction: removed %s → %s " "(confidence=%.2f, indirect path exists)",
                    edge.source.event_id,
                    edge.target.event_id,
                    edge.confidence,
                )
                continue

            kept_edges.append(edge)

        return kept_edges

    def _enforce_max_depth(
        self,
        nodes: dict[str, EventNode],
        edges: list[CausalEdge],
    ) -> list[CausalEdge]:
        """max_graph_depth를 초과하는 깊이의 엣지를 제거한다 (§12.1 OOM 방지)."""
        if not edges:
            return edges

        # 루트 노드 식별 (in-degree = 0)
        target_ids = {e.target.event_id for e in edges}
        root_ids = [eid for eid in nodes if eid not in target_ids]
        if not root_ids:
            return edges

        # 순방향 인접 리스트
        forward_adj: dict[str, list[str]] = {eid: [] for eid in nodes}
        for edge in edges:
            forward_adj[edge.source.event_id].append(edge.target.event_id)

        # BFS로 루트에서 최소 깊이(hop count) 계산
        depths: dict[str, int] = {}
        queue = list(root_ids)
        for rid in root_ids:
            depths[rid] = 0

        head = 0
        while head < len(queue):
            current = queue[head]
            head += 1
            current_depth = depths[current]
            for child in forward_adj.get(current, []):
                child_depth = current_depth + 1
                if child not in depths or depths[child] > child_depth:
                    depths[child] = child_depth
                    queue.append(child)

        # target 깊이가 max_graph_depth 이하인 엣지만 유지
        kept = [e for e in edges if depths.get(e.target.event_id, 0) <= self._max_graph_depth]

        if len(kept) < len(edges):
            logger.debug(
                "[EventGraphBuilder] max_graph_depth=%d: pruned %d edges",
                self._max_graph_depth,
                len(edges) - len(kept),
            )

        return kept

    @staticmethod
    def _find_roots(
        nodes: dict[str, EventNode],
        edges: list[CausalEdge],
    ) -> list[EventNode]:
        """진입차수(in-degree) = 0인 노드를 찾는다."""
        target_ids = {edge.target.event_id for edge in edges}
        return [node for node in nodes.values() if node.event_id not in target_ids]

    @staticmethod
    def _find_leaves(
        nodes: dict[str, EventNode],
        edges: list[CausalEdge],
    ) -> list[EventNode]:
        """진출차수(out-degree) = 0인 노드를 찾는다."""
        source_ids = {edge.source.event_id for edge in edges}
        return [node for node in nodes.values() if node.event_id not in source_ids]

    @staticmethod
    def _generate_incident_id(sorted_nodes: list[EventNode]) -> str:
        """인시던트 ID를 생성한다."""
        if sorted_nodes:
            anchor_ts = sorted_nodes[0].timestamp
            dt = datetime.fromtimestamp(anchor_ts, tz=timezone.utc)
            formatted = dt.strftime("%Y-%m-%d_%H:%M:%S")
            short_hash = uuid.uuid4().hex[:6]
            return f"inc_{formatted}_{short_hash}"
        return f"inc_{uuid.uuid4().hex[:12]}"
