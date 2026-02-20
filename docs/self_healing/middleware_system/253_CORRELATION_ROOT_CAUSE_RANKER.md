# 253. Root Cause Ranker — 근본 원인 순위 산정

> **Version**: 1.0.0
> **Created**: 2026-02-20
> **Status**: Approved
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `services/correlation_engine/root_cause_ranker.py`
> **Related**: [251_CORRELATION_EVENT_GRAPH.md](251_CORRELATION_EVENT_GRAPH.md), [252_CORRELATION_CO_OCCURRENCE_TRACKER.md](252_CORRELATION_CO_OCCURRENCE_TRACKER.md)

---

## 0. 요약

EventDAG(251)에서 구축된 인과관계 그래프를 입력으로, 각 이벤트의 **근본 원인 확률 점수**를 산정한다. "Pool 고갈이 근본 원인일 확률 87%"와 같은 출력을 생성하여, Postmortem의 `root_cause_hypothesis`를 키워드 매칭에서 **구조적 분석 기반**으로 업그레이드한다.

---

## 1. 현재 Root Cause 분석의 한계

### 1.1 기존 구현: 키워드 매칭

`generate_root_cause_hypothesis()` ([postmortem_root_cause.py](../../packages/selfhealing-python/src/selfhealing/utils/postmortem_root_cause.py)):

```python
# 현재 방식 — if/elif 키워드 매칭
if multiple_services:
    hypothesis = "인프라 전체 장애 가능성"
elif "db" in trigger or "pool" in trigger:
    hypothesis = "데이터베이스 연결 문제"
elif "timeout" in trigger:
    hypothesis = "네트워크 지연 또는 서비스 과부하"
else:
    hypothesis = f"단일 서비스 장애: {service}"
```

**한계**:
- 새로운 장애 유형 → 키워드 추가 필요 (코드 수정)
- 확률/확신도 개념 없음 (단정적 문자열)
- 여러 후보 간 순위 비교 불가

### 1.2 목표: 구조적 분석 기반

```python
# 목표 — DAG 토폴로지 + 시간순 + 통계적 증거
[
    RootCauseCandidate("db_pool_exhaustion", score=0.87, evidence="DAG root, 최초 발생, 3서비스 cascading"),
    RootCauseCandidate("network_timeout",    score=0.12, evidence="DB 이후 2초 뒤 발생, downstream"),
    RootCauseCandidate("memory_pressure",    score=0.01, evidence="관련 엣지 없음"),
]
```

---

## 2. 핵심 자료구조

```python
@dataclass(frozen=True)
class RootCauseCandidate:
    """근본 원인 후보"""
    event_node: EventNode         # DAG 내 이벤트 노드
    score: float                  # 0.0 ~ 1.0 (정규화된 점수)
    rank: int                     # 1 = 최유력 후보
    evidence: list[str]           # 사람이 읽을 수 있는 근거 목록
    contributing_factors: dict    # 점수 구성 요소 상세
    affected_services: list[str]  # 이 원인으로 인해 영향받은 서비스 목록
    cascade_depth: int            # DAG에서 이 노드부터 leaf까지 최대 깊이


@dataclass
class RootCauseAnalysis:
    """근본 원인 분석 결과 전체"""
    incident_id: str
    analyzed_at: float
    dag: EventDAG
    candidates: list[RootCauseCandidate]   # score 내림차순 정렬
    primary_cause: RootCauseCandidate      # candidates[0]
    confidence: float                       # 분석 전체 신뢰도
    summary: str                            # 사람이 읽을 수 있는 요약
```

---

## 3. 점수 산정 알고리즘

### 3.1 4가지 Factor 가중합

```python
class RootCauseRanker:
    """DAG 기반 근본 원인 순위 산정"""

    # 가중치 (설정 가능)
    WEIGHT_TOPOLOGY = 0.35     # DAG 토폴로지 기반
    WEIGHT_TEMPORAL = 0.25     # 시간순 기반
    WEIGHT_BLAST_RADIUS = 0.25 # 영향 범위 기반
    WEIGHT_HISTORICAL = 0.15   # 과거 패턴 기반

    def rank(self, dag: EventDAG, co_occurrence_data: list[CorrelationResult]) -> RootCauseAnalysis:
        """DAG + 동시발생 데이터 → 근본 원인 순위"""
        raw_scores: dict[str, dict] = {}

        for node_id, node in dag.nodes.items():
            topology_score = self._topology_score(node, dag)
            temporal_score = self._temporal_score(node, dag)
            blast_score = self._blast_radius_score(node, dag)
            historical_score = self._historical_score(node, co_occurrence_data)

            total = (
                self.WEIGHT_TOPOLOGY * topology_score +
                self.WEIGHT_TEMPORAL * temporal_score +
                self.WEIGHT_BLAST_RADIUS * blast_score +
                self.WEIGHT_HISTORICAL * historical_score
            )

            raw_scores[node_id] = {
                "total": total,
                "topology": topology_score,
                "temporal": temporal_score,
                "blast_radius": blast_score,
                "historical": historical_score,
            }

        # 정규화: 모든 점수를 0~1로 변환 (합계 = 1.0)
        candidates = self._normalize_and_rank(raw_scores, dag)
        return self._build_analysis(dag, candidates, co_occurrence_data)
```

### 3.2 Factor 1: Topology Score (DAG 구조)

```python
    def _topology_score(self, node: EventNode, dag: EventDAG) -> float:
        """DAG에서 이 노드의 구조적 위치"""
        in_degree = self._count_in_edges(node, dag)
        out_degree = self._count_out_edges(node, dag)
        descendants = len(dag.get_descendants(node))
        total_nodes = len(dag.nodes)

        # 근본 원인 특성: 진입차수 낮고, 영향 범위(후손) 넓음
        root_factor = 1.0 / (1.0 + in_degree)           # in_degree=0이면 1.0
        influence_factor = descendants / max(1, total_nodes - 1)  # 영향 비율

        return root_factor * 0.6 + influence_factor * 0.4
```

**핵심**: `in_degree = 0`인 노드(DAG의 루트)가 근본 원인 후보로 가장 높은 점수.

### 3.3 Factor 2: Temporal Score (시간순)

```python
    def _temporal_score(self, node: EventNode, dag: EventDAG) -> float:
        """먼저 발생할수록 높은 점수"""
        all_timestamps = sorted(n.timestamp for n in dag.nodes.values())
        if len(all_timestamps) <= 1:
            return 1.0

        # 정규화: 첫 번째 = 1.0, 마지막 = 0.0
        earliest = all_timestamps[0]
        latest = all_timestamps[-1]
        span = latest - earliest
        if span == 0:
            return 1.0

        return 1.0 - (node.timestamp - earliest) / span
```

### 3.4 Factor 3: Blast Radius Score (영향 범위)

```python
    def _blast_radius_score(self, node: EventNode, dag: EventDAG) -> float:
        """서비스 의존성 그래프에서 이 서비스의 중요도"""
        try:
            blast_radius = BlastRadiusService()
            deps = blast_radius.get_dependencies(node.service_name)
            if not deps:
                return 0.5  # 의존성 정보 없으면 중립 점수

            # downstream이 많을수록 높은 점수 (영향력 큰 서비스)
            downstream_count = len(deps.downstream) if hasattr(deps, 'downstream') else 0
            upstream_count = len(deps.upstream) if hasattr(deps, 'upstream') else 0

            # upstream이 적고 downstream이 많으면 → 인프라/코어 서비스
            if upstream_count == 0 and downstream_count > 0:
                return 1.0  # DB, Message Queue 등
            return downstream_count / max(1, downstream_count + upstream_count)
        except Exception:
            return 0.5
```

### 3.5 Factor 4: Historical Score (과거 패턴)

```python
    def _historical_score(self, node: EventNode, co_occurrence_data: list[CorrelationResult]) -> float:
        """과거 동시발생 패턴에서 이 이벤트가 원인인 빈도"""
        cause_count = 0
        effect_count = 0

        for result in co_occurrence_data:
            if result.pair.event_type_a == node.event_type:
                if result.direction == "a_causes_b":
                    cause_count += 1
                elif result.direction == "b_causes_a":
                    effect_count += 1
            elif result.pair.event_type_b == node.event_type:
                if result.direction == "b_causes_a":
                    cause_count += 1
                elif result.direction == "a_causes_b":
                    effect_count += 1

        total = cause_count + effect_count
        if total == 0:
            return 0.5  # 이력 없으면 중립
        return cause_count / total
```

---

## 4. 정규화 및 순위 산정

```python
    def _normalize_and_rank(self, raw_scores: dict, dag: EventDAG) -> list[RootCauseCandidate]:
        """원시 점수 → 정규화(합계=1.0) → 순위 부여"""
        total = sum(s["total"] for s in raw_scores.values())
        if total == 0:
            total = 1.0

        candidates = []
        for node_id, scores in raw_scores.items():
            node = dag.nodes[node_id]
            normalized_score = scores["total"] / total

            evidence = self._build_evidence(node, scores, dag)
            descendants = dag.get_descendants(node)

            candidates.append(RootCauseCandidate(
                event_node=node,
                score=round(normalized_score, 4),
                rank=0,  # 아래에서 설정
                evidence=evidence,
                contributing_factors=scores,
                affected_services=list({d.service_name for d in descendants}),
                cascade_depth=self._max_depth_from(node, dag),
            ))

        # 점수 내림차순 정렬 → 순위 부여
        candidates.sort(key=lambda c: c.score, reverse=True)
        for i, c in enumerate(candidates):
            object.__setattr__(c, 'rank', i + 1)

        return candidates
```

---

## 5. 사람이 읽을 수 있는 Evidence 생성

```python
    def _build_evidence(self, node: EventNode, scores: dict, dag: EventDAG) -> list[str]:
        """점수 구성 요소를 사람이 읽을 수 있는 문장으로 변환"""
        evidence = []

        # Topology
        in_degree = self._count_in_edges(node, dag)
        out_degree = self._count_out_edges(node, dag)
        if in_degree == 0:
            evidence.append(f"DAG 루트 노드 (선행 원인 이벤트 없음)")
        evidence.append(f"후속 영향 이벤트 {out_degree}개 발생")

        # Temporal
        all_timestamps = sorted(n.timestamp for n in dag.nodes.values())
        position = sorted(dag.nodes.values(), key=lambda n: n.timestamp).index(node)
        evidence.append(f"시간순 {position + 1}/{len(dag.nodes)}번째 발생")

        # Blast Radius
        descendants = dag.get_descendants(node)
        if descendants:
            services = {d.service_name for d in descendants}
            evidence.append(f"영향받은 서비스: {', '.join(services)} ({len(services)}개)")

        # Historical
        if scores["historical"] > 0.7:
            evidence.append("과거 인시던트에서도 원인으로 식별된 이력")

        return evidence
```

---

## 6. 요약 문장 자동 생성

```python
    def _generate_summary(self, primary: RootCauseCandidate, dag: EventDAG) -> str:
        """Postmortem에 들어갈 요약 문장"""
        percentage = round(primary.score * 100)
        return (
            f"[{primary.event_node.service_name}]의 "
            f"{primary.event_node.event_type} 이벤트가 "
            f"근본 원인일 확률 {percentage}%. "
            f"{len(primary.affected_services)}개 서비스에 영향, "
            f"cascade 깊이 {primary.cascade_depth}단계. "
            f"근거: {'; '.join(primary.evidence[:3])}"
        )
```

출력 예시:
```
[db-pool-service]의 circuit_breaker_opened 이벤트가 근본 원인일 확률 87%.
3개 서비스에 영향, cascade 깊이 3단계.
근거: DAG 루트 노드 (선행 원인 이벤트 없음); 시간순 1/5번째 발생; 영향받은 서비스: payment, order, point (3개)
```

---

## 7. Strategy 인터페이스를 통한 교체 가능성

기본 제공하는 `DefaultRootCauseRanker` 외에, `RootCauseStrategy` Protocol(256번)을 통해 교체 가능:

```python
class RootCauseStrategy(Protocol):
    """근본 원인 분석 전략 — ML/LLM으로 교체 가능"""
    def rank_causes(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis: ...
```

구매자 확장 예시:
- LLM 기반: DAG + 이벤트 데이터를 프롬프트로 구성 → GPT/Claude가 근본 원인 분석
- Bayesian Network: DAG의 조건부 확률 표를 학습하여 확률적 추론
- Graph Neural Network: DAG를 GNN 입력으로 사용

---

## 8. Postmortem 연동

기존 `generate_postmortem_data()`가 생성하는 `root_cause_hypothesis` 필드를 확장:

```python
# 기존 (키워드 매칭)
"root_cause_hypothesis": "데이터베이스 연결 문제"

# 확장 후 (RootCauseAnalysis 통합)
"root_cause_hypothesis": "데이터베이스 연결 문제",
"root_cause_analysis": {
    "primary_cause": {
        "event": "circuit_breaker_opened",
        "service": "db-pool-service",
        "score": 0.87,
        "evidence": ["DAG 루트 노드", "시간순 1/5번째 발생", "3서비스 cascading"]
    },
    "alternative_causes": [...],
    "dag_summary": {"nodes": 5, "edges": 4, "depth": 3},
    "analysis_confidence": 0.82
}
```

→ 기존 필드는 하위호환을 위해 유지, 새 필드로 상세 분석 추가.

---

## 9. 테스트 전략

| 테스트 유형 | 시나리오 | 검증 |
|------------|---------|------|
| **단위** | 단일 노드 DAG | score=1.0, rank=1 |
| **단위** | 선형 체인 A→B→C | A가 rank=1 (in_degree=0, 최초 발생) |
| **단위** | 다이아몬드 DAG (A→B, A→C, B→D, C→D) | A가 rank=1 |
| **단위** | 동시 발생 2개 root | 두 후보 모두 높은 점수, 차이 ≤ 0.1 |
| **통합** | BlastRadius mock: DB upstream of 3 services | DB 서비스가 rank=1 |
| **통합** | Historical: 과거 5회 A가 원인 | historical_score ≥ 0.8 |
| **시나리오** | "DB Pool → Error Rate → CB OPEN × 3 → Emergency" | DB Pool이 87%+ |
