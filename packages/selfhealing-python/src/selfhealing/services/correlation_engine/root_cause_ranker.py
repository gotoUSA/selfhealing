"""
Root Cause Ranker — DAG 기반 근본 원인 순위 산정.

EventDAG에서 구축된 인과관계 그래프를 입력으로, 각 이벤트의
근본 원인 확률 점수를 산정한다.
"Pool 고갈이 근본 원인일 확률 87%"와 같은 출력을 생성하여,
Postmortem의 root_cause_hypothesis를 키워드 매칭에서
구조적 분석 기반으로 업그레이드한다.

4가지 Factor 가중합:
    - Topology (0.35): DAG에서 노드의 구조적 위치 (in-degree, 후손 수)
    - Temporal (0.25): 시간순 위치 (먼저 발생할수록 높음)
    - Blast Radius (0.25): 서비스 의존성 그래프에서의 중요도
    - Historical (0.15): 과거 동시발생 패턴에서 원인으로 식별된 빈도

2단계 랭킹 전략:
    1단계: Connected Component별 독립 랭킹
    2단계: 컴포넌트 가중치 적용 → 전역 Softmax 정규화 → Top-P 필터링
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

from selfhealing.services.blast_radius.service import BlastRadiusService
from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CorrelationResult,
)
from selfhealing.services.correlation_engine.event_graph import (
    EventDAG,
    EventNode,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 상수 정의
# =============================================================================

# Factor 가중치
WEIGHT_TOPOLOGY = 0.35
"""DAG 토폴로지 기반 가중치"""

WEIGHT_TEMPORAL = 0.25
"""시간순 기반 가중치"""

WEIGHT_BLAST_RADIUS = 0.25
"""영향 범위 기반 가중치"""

WEIGHT_HISTORICAL = 0.15
"""과거 패턴 기반 가중치"""

# Topology Score 내부 가중치
TOPOLOGY_ROOT_FACTOR_WEIGHT = 0.6
"""진입차수 기반 루트 팩터 가중치"""

TOPOLOGY_INFLUENCE_FACTOR_WEIGHT = 0.4
"""후손 영향 비율 팩터 가중치"""

# 정규화 파라미터
SOFTMAX_TEMPERATURE = 1.0
"""Softmax temperature — T→0: winner-take-all, T→∞: uniform"""

TOP_P_THRESHOLD = 0.95
"""누적 확률 95%까지만 반환"""

MIN_CANDIDATE_SCORE = 0.01
"""개별 점수 1% 미만은 즉시 제외"""

TIMESTAMP_EPSILON = 0.001
"""1ms 이내는 동시 발생으로 간주 (Tie-breaker 양자화 단위)"""

# 기본 점수
NEUTRAL_SCORE = 0.5
"""정보 없을 때 중립 점수"""

# Historical Score 방향 판정 임계값
HISTORICAL_HIGH_THRESHOLD = 0.7
"""과거 원인 이력이 높다고 판단하는 임계값"""


# =============================================================================
# 자료구조
# =============================================================================


@dataclass(frozen=True)
class RootCauseCandidate:
    """근본 원인 후보."""

    event_node: EventNode
    """DAG 내 이벤트 노드"""

    score: float
    """0.0 ~ 1.0 (정규화된 점수)"""

    rank: int
    """1 = 최유력 후보"""

    evidence: list[str]
    """사람이 읽을 수 있는 근거 목록"""

    contributing_factors: dict
    """점수 구성 요소 상세"""

    affected_services: list[str]
    """이 원인으로 인해 영향받은 서비스 목록"""

    cascade_depth: int
    """DAG에서 이 노드부터 leaf까지 최대 깊이"""


@dataclass
class RootCauseAnalysis:
    """근본 원인 분석 결과 전체."""

    incident_id: str
    analyzed_at: float
    dag: EventDAG
    candidates: list[RootCauseCandidate]
    """score 내림차순 정렬"""

    primary_cause: RootCauseCandidate
    """candidates[0]"""

    confidence: float
    """분석 전체 신뢰도"""

    summary: str
    """사람이 읽을 수 있는 요약"""


# =============================================================================
# RootCauseRanker — 근본 원인 순위 산정 엔진
# =============================================================================


class RootCauseRanker:
    """DAG 기반 근본 원인 순위 산정.

    4가지 Factor의 가중합을 Connected Component별로 독립 산정한 후,
    전역 Softmax 정규화와 Top-P 필터링을 통해 최종 순위를 결정한다.
    """

    def __init__(
        self,
        weight_topology: float = WEIGHT_TOPOLOGY,
        weight_temporal: float = WEIGHT_TEMPORAL,
        weight_blast_radius: float = WEIGHT_BLAST_RADIUS,
        weight_historical: float = WEIGHT_HISTORICAL,
        softmax_temperature: float = SOFTMAX_TEMPERATURE,
        top_p_threshold: float = TOP_P_THRESHOLD,
        min_candidate_score: float = MIN_CANDIDATE_SCORE,
        timestamp_epsilon: float = TIMESTAMP_EPSILON,
    ) -> None:
        self.weight_topology = weight_topology
        self.weight_temporal = weight_temporal
        self.weight_blast_radius = weight_blast_radius
        self.weight_historical = weight_historical
        self.softmax_temperature = softmax_temperature
        self.top_p_threshold = top_p_threshold
        self.min_candidate_score = min_candidate_score
        self.timestamp_epsilon = timestamp_epsilon

    # ─────────────────────────────────────────────
    # 공개 API
    # ─────────────────────────────────────────────

    def rank(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        """DAG + 동시발생 데이터 → 근본 원인 순위.

        2단계 랭킹:
            1단계: Connected Component별 독립 랭킹
                   — topology, temporal의 모수는 컴포넌트 내부 노드로 한정
                   — blast_radius, historical은 전역 데이터 사용
            2단계: 컴포넌트 가중치 적용 → 전역 Softmax 정규화 → Top-P 필터링

        Args:
            dag: EventDAG 인과관계 그래프.
            co_occurrence_data: CoOccurrenceTracker.analyze_tick() 결과.

        Returns:
            RootCauseAnalysis: 순위가 매겨진 근본 원인 후보 목록.
        """
        # O(M) 1회 인덱싱: Historical Score O(1) 조회용
        co_occurrence_index: dict[str, list[CorrelationResult]] = defaultdict(list)
        for result in co_occurrence_data:
            co_occurrence_index[result.pair.event_type_a].append(result)
            co_occurrence_index[result.pair.event_type_b].append(result)

        # Connected Component 분리
        components = dag.get_connected_components()
        total_unique_services = len({n.service_name for n in dag.nodes.values()})

        all_raw_entries: list[dict] = []
        for component in components:
            entries = self._rank_component(component, co_occurrence_index, total_unique_services)
            all_raw_entries.extend(entries)

        # 전역 Softmax 정규화 + Top-P 필터링 + Tie-breaker
        candidates = self._normalize_and_rank(all_raw_entries, dag)
        return self._build_analysis(dag, candidates, co_occurrence_data)

    # ─────────────────────────────────────────────
    # 1단계: 컴포넌트별 독립 랭킹
    # ─────────────────────────────────────────────

    def _rank_component(
        self,
        component: EventDAG,
        co_occurrence_index: dict[str, list[CorrelationResult]],
        total_unique_services: int,
    ) -> list[dict]:
        """단일 컴포넌트 내부 랭킹.

        Args:
            component: get_connected_components()가 반환한 서브 DAG.
            co_occurrence_index: event_type → CorrelationResult 룩업 테이블.
            total_unique_services: 전체 DAG의 고유 서비스 수.

        Returns:
            노드별 raw score dict 리스트 (컴포넌트 가중치 적용됨).
        """
        # 컴포넌트 가중치: 고유 서비스 수 기반 (노드 수 대신 서비스 수)
        component_unique_services = len({n.service_name for n in component.nodes.values()})
        component_weight = component_unique_services / max(1, total_unique_services)

        results: list[dict] = []
        for node_id, node in component.nodes.items():
            topology_score = self._topology_score(node, component)
            temporal_score = self._temporal_score(node, component)
            blast_score = self._blast_radius_score(node, component)
            historical_score = self._historical_score(node, co_occurrence_index)

            total = (
                self.weight_topology * topology_score
                + self.weight_temporal * temporal_score
                + self.weight_blast_radius * blast_score
                + self.weight_historical * historical_score
            )

            results.append(
                {
                    "node_id": node_id,
                    "total": total * component_weight,
                    "topology": topology_score,
                    "temporal": temporal_score,
                    "blast_radius": blast_score,
                    "historical": historical_score,
                    "component": component,
                }
            )
        return results

    # ─────────────────────────────────────────────
    # Factor 1: Topology Score (DAG 구조)
    # ─────────────────────────────────────────────

    def _topology_score(self, node: EventNode, dag: EventDAG) -> float:
        """DAG에서 이 노드의 구조적 위치.

        근본 원인 특성: 진입차수(in-degree) 낮고, 영향 범위(후손) 넓음.
        컴포넌트 범위: dag 파라미터에는 서브 DAG(컴포넌트)가 전달됨.

        Args:
            node: 점수를 산정할 이벤트 노드.
            dag: 해당 노드가 속한 컴포넌트 서브 DAG.
        """
        in_degree = self._count_in_edges(node, dag)
        descendants = len(dag.get_descendants(node))
        total_nodes = len(dag.nodes)

        # in_degree=0이면 1.0 (DAG 루트)
        root_factor = 1.0 / (1.0 + in_degree)
        # 후손 비율 (자기 자신 제외)
        influence_factor = descendants / max(1, total_nodes - 1)

        return root_factor * TOPOLOGY_ROOT_FACTOR_WEIGHT + influence_factor * TOPOLOGY_INFLUENCE_FACTOR_WEIGHT

    # ─────────────────────────────────────────────
    # Factor 2: Temporal Score (시간순)
    # ─────────────────────────────────────────────

    def _temporal_score(self, node: EventNode, dag: EventDAG) -> float:
        """먼저 발생할수록 높은 점수.

        정규화: 첫 번째 = 1.0, 마지막 = 0.0.
        컴포넌트 범위: dag 파라미터에는 서브 DAG(컴포넌트)가 전달됨.

        Args:
            node: 점수를 산정할 이벤트 노드.
            dag: 해당 노드가 속한 컴포넌트 서브 DAG.
        """
        all_timestamps = sorted(n.timestamp for n in dag.nodes.values())
        if len(all_timestamps) <= 1:
            return 1.0

        earliest = all_timestamps[0]
        latest = all_timestamps[-1]
        span = latest - earliest
        if span == 0:
            return 1.0

        return 1.0 - (node.timestamp - earliest) / span

    # ─────────────────────────────────────────────
    # Factor 3: Blast Radius Score (영향 범위)
    # ─────────────────────────────────────────────

    def _blast_radius_score(self, node: EventNode, dag: EventDAG) -> float:
        """서비스 의존성 그래프에서 이 서비스의 중요도.

        방향 정의 (blast_radius/service.py 기준):
            upstream  = 나를 호출하는 서비스들 (target_service == me)
            downstream = 내가 호출하는 서비스들 (source_service == me)

        DB/MQ 특성: upstream 多, downstream 0 → 근본 원인일 가능성 높음
        Gateway 특성: upstream 0, downstream 多 → 최상위 진입점

        Args:
            node: 점수를 산정할 이벤트 노드.
            dag: 해당 노드가 속한 DAG (현재 미사용, 시그니처 일관성 유지).
        """
        try:
            blast_radius = BlastRadiusService()
            deps = blast_radius.get_dependencies(node.service_name)
            if not deps or (not deps["upstream"] and not deps["downstream"]):
                return NEUTRAL_SCORE

            # set 변환: 동일 서비스 간 다중 dependency_type 중복 카운트 방지
            upstream_services = {d.source_service for d in deps["upstream"]}
            downstream_services = {d.target_service for d in deps["downstream"]}
            upstream_count = len(upstream_services)
            downstream_count = len(downstream_services)

            # upstream이 많고 downstream이 없으면 → DB/MQ 등 인프라 코어 서비스
            if downstream_count == 0 and upstream_count > 0:
                return 1.0

            # upstream 비율이 높을수록 하부 인프라 (근본 원인 후보)
            return upstream_count / max(1, upstream_count + downstream_count)
        except Exception:
            return NEUTRAL_SCORE

    # ─────────────────────────────────────────────
    # Factor 4: Historical Score (과거 패턴)
    # ─────────────────────────────────────────────

    def _historical_score(
        self,
        node: EventNode,
        co_occurrence_index: dict[str, list[CorrelationResult]],
    ) -> float:
        """과거 동시발생 패턴에서 이 이벤트가 원인인 빈도.

        Args:
            node: 점수를 산정할 이벤트 노드.
            co_occurrence_index: event_type → 관련 CorrelationResult 룩업 테이블.
                rank() 진입 시 O(M) 1회 구축됨.

        Complexity: O(K) — K = 이 event_type과 관련된 결과 수.
        """
        related = co_occurrence_index.get(node.event_type, [])
        cause_count = 0
        effect_count = 0

        for result in related:
            if result.pair.event_type_a == node.event_type:
                if result.direction == "a_causes_b":
                    cause_count += 1
                elif result.direction == "b_causes_a":
                    effect_count += 1
            else:  # event_type_b == node.event_type
                if result.direction == "b_causes_a":
                    cause_count += 1
                elif result.direction == "a_causes_b":
                    effect_count += 1

        total = cause_count + effect_count
        if total == 0:
            return NEUTRAL_SCORE
        return cause_count / total

    # ─────────────────────────────────────────────
    # 2단계: 정규화 및 순위 산정
    # ─────────────────────────────────────────────

    def _normalize_and_rank(
        self,
        raw_entries: list[dict],
        dag: EventDAG,
    ) -> list[RootCauseCandidate]:
        """원시 점수 → Softmax 정규화 → Top-P 필터링 → Tie-breaker 순위 부여."""
        if not raw_entries:
            return []

        totals = [e["total"] for e in raw_entries]
        max_total = max(totals) if totals else 1.0
        temperature = self.softmax_temperature

        # Softmax: exp((score - max) / T) / Σexp((score - max) / T)
        # max를 빼 수치 안정성 확보 (overflow 방지)
        exp_scores = [math.exp((e["total"] - max_total) / temperature) for e in raw_entries]
        exp_sum = sum(exp_scores)

        candidates = []
        for i, entry in enumerate(raw_entries):
            normalized_score = exp_scores[i] / exp_sum if exp_sum > 0 else 0.0
            node = dag.nodes[entry["node_id"]]
            component: EventDAG = entry["component"]

            evidence = self._build_evidence(node, entry, component)
            descendants = component.get_descendants(node)

            candidates.append(
                RootCauseCandidate(
                    event_node=node,
                    score=round(normalized_score, 4),
                    rank=0,  # 아래 Tie-breaker에서 설정
                    evidence=evidence,
                    contributing_factors={
                        "topology": entry["topology"],
                        "temporal": entry["temporal"],
                        "blast_radius": entry["blast_radius"],
                        "historical": entry["historical"],
                        "raw_total": entry["total"],
                    },
                    affected_services=list({d.service_name for d in descendants}),
                    cascade_depth=self._max_depth_from(node, component),
                )
            )

        # Deterministic Tie-breaker 4단계 정렬
        # timestamp를 floor 양자화하여 Jitter/Clock Skew 방어
        epsilon = self.timestamp_epsilon
        candidates.sort(
            key=lambda c: (
                -c.score,  # 1차: 점수 높은 순
                -c.cascade_depth,  # 2차: cascade 깊은 순
                int(c.event_node.timestamp / epsilon),  # 3차: 먼저 발생한 순 (양자화)
                c.event_node.event_id,  # 4차: UUID 알파벳순 (결정론적)
            )
        )
        for i, c in enumerate(candidates):
            object.__setattr__(c, "rank", i + 1)

        # Top-P + Min Threshold 하이브리드 필터링
        candidates = self._filter_top_p(candidates)

        return candidates

    def _filter_top_p(self, candidates: list[RootCauseCandidate]) -> list[RootCauseCandidate]:
        """누적 확률 Top-P + 개별 최소 임계값으로 필터링.

        Top-K(고정 개수)와 달리, 장애 규모에 따라 반환 개수가 유연하게 조절된다.

        균등 분포 방어: min_candidate_score 미만은 즉시 컷오프하여
        모든 이벤트가 동일 점수인 시나리오에서 전부 반환되는 것을 방지.
        """
        cumulative = 0.0
        filtered: list[RootCauseCandidate] = []
        for c in candidates:  # 이미 score 내림차순 정렬
            if c.score < self.min_candidate_score:
                break
            filtered.append(c)
            cumulative += c.score
            if cumulative >= self.top_p_threshold:
                break
        return filtered if filtered else candidates[:1]  # 최소 1개 보장

    # ─────────────────────────────────────────────
    # Evidence 및 요약 생성
    # ─────────────────────────────────────────────

    def _build_evidence(self, node: EventNode, scores: dict, dag: EventDAG) -> list[str]:
        """점수 구성 요소를 사람이 읽을 수 있는 문장으로 변환.

        Args:
            node: 대상 이벤트 노드.
            scores: 해당 노드의 factor 점수 dict.
            dag: 해당 노드가 속한 컴포넌트 서브 DAG.
        """
        evidence: list[str] = []

        # Topology
        in_degree = self._count_in_edges(node, dag)
        out_degree = self._count_out_edges(node, dag)
        if in_degree == 0:
            evidence.append("DAG 루트 노드 (선행 원인 이벤트 없음)")
        evidence.append(f"후속 영향 이벤트 {out_degree}개 발생")

        # Temporal
        sorted_nodes = sorted(dag.nodes.values(), key=lambda n: n.timestamp)
        position = sorted_nodes.index(node)
        evidence.append(f"시간순 {position + 1}/{len(dag.nodes)}번째 발생")

        # Blast Radius
        descendants = dag.get_descendants(node)
        if descendants:
            services = {d.service_name for d in descendants}
            evidence.append(f"영향받은 서비스: {', '.join(sorted(services))} ({len(services)}개)")

        # Historical
        if scores["historical"] > HISTORICAL_HIGH_THRESHOLD:
            evidence.append("과거 인시던트에서도 원인으로 식별된 이력")

        return evidence

    def _generate_summary(self, primary: RootCauseCandidate, dag: EventDAG) -> str:
        """Postmortem에 들어갈 요약 문장."""
        percentage = round(primary.score * 100)
        return (
            f"[{primary.event_node.service_name}]의 "
            f"{primary.event_node.event_type} 이벤트가 "
            f"근본 원인일 확률 {percentage}%. "
            f"{len(primary.affected_services)}개 서비스에 영향, "
            f"cascade 깊이 {primary.cascade_depth}단계. "
            f"근거: {'; '.join(primary.evidence[:3])}"
        )

    def _build_analysis(
        self,
        dag: EventDAG,
        candidates: list[RootCauseCandidate],
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        """최종 RootCauseAnalysis 객체를 조립한다."""
        primary = (
            candidates[0]
            if candidates
            else RootCauseCandidate(
                event_node=EventNode(
                    event_id="unknown",
                    event_type="unknown",
                    service_name="unknown",
                    timestamp=0.0,
                    data={},
                    correlation_id=None,
                ),
                score=0.0,
                rank=1,
                evidence=["분석 가능한 이벤트 없음"],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            )
        )

        # 분석 신뢰도: 1위와 2위 점수 차이 + 데이터 풍부도
        confidence = self._calculate_confidence(candidates, dag, co_occurrence_data)

        return RootCauseAnalysis(
            incident_id=dag.incident_id,
            analyzed_at=time.time(),
            dag=dag,
            candidates=candidates,
            primary_cause=primary,
            confidence=confidence,
            summary=self._generate_summary(primary, dag),
        )

    def _calculate_confidence(
        self,
        candidates: list[RootCauseCandidate],
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> float:
        """분석 전체 신뢰도를 산정한다.

        신뢰도 구성:
        - 1위 점수 (40%): 1위 후보의 확률 점수가 높을수록 신뢰
        - 점수 격차 (30%): 1위와 2위 차이가 클수록 명확한 판정
        - 데이터 풍부도 (30%): DAG 노드 수 + 과거 이력 데이터 유무
        """
        if not candidates:
            return 0.0

        primary_score = candidates[0].score

        # 1위와 2위 점수 격차
        gap = 0.0
        if len(candidates) >= 2:
            gap = candidates[0].score - candidates[1].score
        else:
            gap = candidates[0].score  # 후보가 1개면 격차 = 점수 자체

        # 데이터 풍부도: 노드 수가 많고 이력 데이터가 있으면 높음
        node_factor = min(1.0, len(dag.nodes) / 10)  # 10개 이상이면 포화
        history_factor = 1.0 if co_occurrence_data else 0.5

        data_richness = (node_factor + history_factor) / 2

        return round(
            primary_score * 0.4 + gap * 0.3 + data_richness * 0.3,
            4,
        )

    # ─────────────────────────────────────────────
    # 유틸리티 메서드
    # ─────────────────────────────────────────────

    @staticmethod
    def _count_in_edges(node: EventNode, dag: EventDAG) -> int:
        """노드의 진입차수(in-degree)를 계산한다."""
        return sum(1 for e in dag.edges if e.target.event_id == node.event_id)

    @staticmethod
    def _count_out_edges(node: EventNode, dag: EventDAG) -> int:
        """노드의 진출차수(out-degree)를 계산한다."""
        return sum(1 for e in dag.edges if e.source.event_id == node.event_id)

    @staticmethod
    def _max_depth_from(node: EventNode, dag: EventDAG) -> int:
        """이 노드부터 leaf까지의 최대 깊이를 DFS로 계산한다."""
        forward_adj: dict[str, list[str]] = {eid: [] for eid in dag.nodes}
        for edge in dag.edges:
            forward_adj[edge.source.event_id].append(edge.target.event_id)

        memo: dict[str, int] = {}

        def _depth(eid: str) -> int:
            if eid in memo:
                return memo[eid]
            children = forward_adj.get(eid, [])
            if not children:
                memo[eid] = 0
                return 0
            max_child = max(_depth(c) for c in children)
            memo[eid] = max_child + 1
            return memo[eid]

        return _depth(node.event_id)
