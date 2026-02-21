# 253. Root Cause Ranker — 근본 원인 순위 산정

> **Version**: 1.1.0
> **Created**: 2026-02-20
> **Status**: Implemented
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
        """DAG + 동시발생 데이터 → 근본 원인 순위

        2단계 랭킹 (§10.3 설계 결정):
            1단계: Connected Component별 독립 랭킹
                   — topology, temporal의 모수(분모)는 컴포넌트 내부 노드로 한정
                   — blast_radius, historical은 전역 데이터 사용
            2단계: 컴포넌트 가중치 적용 → 전역 Softmax 정규화 → Top-P 필터링
        """
        # ── O(M) 1회 인덱싱: Historical Score O(1) 조회용 (§10.4) ──
        co_occurrence_index: dict[str, list[CorrelationResult]] = defaultdict(list)
        for result in co_occurrence_data:
            co_occurrence_index[result.pair.event_type_a].append(result)
            co_occurrence_index[result.pair.event_type_b].append(result)

        # ── Connected Component 분리 (§10.3) ──
        # EventDAG.get_connected_components()는 무방향 인접리스트 DFS로 서브그래프 분리
        # 각 서브 DAG는 독립적 window_start/window_end/root_nodes를 재계산함
        components = dag.get_connected_components()
        total_unique_services = len({n.service_name for n in dag.nodes.values()})

        all_raw_entries: list[dict] = []
        for component in components:
            entries = self._rank_component(
                component, co_occurrence_index, total_unique_services
            )
            all_raw_entries.extend(entries)

        # ── 전역 Softmax 정규화 + Top-P 필터링 + Tie-breaker (§4) ──
        candidates = self._normalize_and_rank(all_raw_entries, dag)
        return self._build_analysis(dag, candidates, co_occurrence_data)

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
        # 컴포넌트 가중치: 고유 서비스 수 기반 (§10.3 — 노드 수 대신 서비스 수)
        component_unique_services = len({n.service_name for n in component.nodes.values()})
        component_weight = component_unique_services / max(1, total_unique_services)

        results: list[dict] = []
        for node_id, node in component.nodes.items():
            topology_score = self._topology_score(node, component)       # 컴포넌트 범위
            temporal_score = self._temporal_score(node, component)       # 컴포넌트 범위
            blast_score = self._blast_radius_score(node, component)      # 전역
            historical_score = self._historical_score(node, co_occurrence_index)  # 전역

            total = (
                self.WEIGHT_TOPOLOGY * topology_score +
                self.WEIGHT_TEMPORAL * temporal_score +
                self.WEIGHT_BLAST_RADIUS * blast_score +
                self.WEIGHT_HISTORICAL * historical_score
            )

            results.append({
                "node_id": node_id,
                "total": total * component_weight,  # 컴포넌트 가중치 적용
                "topology": topology_score,
                "temporal": temporal_score,
                "blast_radius": blast_score,
                "historical": historical_score,
                "component": component,
            })
        return results
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

> **컴포넌트 범위 적용 (§10.3)**: `dag` 파라미터에는 전체 DAG가 아닌 `get_connected_components()`가 반환한
> **서브 DAG(컴포넌트)**가 전달된다. 따라서 `total_nodes`, `in_degree`, `descendants`의 모수는
> 해당 컴포넌트 내부 노드로 한정되어, 단절된 독립 장애 간의 토폴로지 점수 왜곡을 방지한다.

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

> **컴포넌트 범위 적용 (§10.3)**: `dag` 파라미터에는 `_rank_component()`에서 전달한 서브 DAG(컴포넌트)가
> 들어온다. 따라서 `earliest`, `latest`, `span`은 해당 컴포넌트 내부 타임스탬프만을 대상으로 계산된다.
> 예: 컴포넌트 A(t=100~110)와 컴포넌트 B(t=145~150)가 단절되어 있을 때,
> B의 루트 이벤트(t=145)는 전체 DAG 기준에서 temporal_score=0.10이지만,
> 컴포넌트 B 기준에서는 temporal_score=1.0으로 올바르게 산정된다.

### 3.4 Factor 3: Blast Radius Score (영향 범위)

> **방향 정의 (§10.1)**: `BlastRadiusService.get_dependencies(service)` 반환값에서
> `upstream` = 해당 서비스를 **호출하는** 서비스들 (`target_service == service`),
> `downstream` = 해당 서비스가 **호출하는** 서비스들 (`source_service == service`).
>
> 따라서 DB/MQ 같은 하부 인프라 서비스는 `upstream`이 많고 `downstream`이 0인 특성을 갖는다.
> 코드 근거: `blast_radius/service.py` L162–166
> ```python
> upstream = [d for d in self._dependencies if d.target_service == service]
> downstream = [d for d in self._dependencies if d.source_service == service]
> ```

```python
    def _blast_radius_score(self, node: EventNode, dag: EventDAG) -> float:
        """서비스 의존성 그래프에서 이 서비스의 중요도

        방향 정의 (blast_radius/service.py 기준):
            upstream  = 나를 호출하는 서비스들 (target_service == me)
            downstream = 내가 호출하는 서비스들 (source_service == me)

        DB/MQ 특성: upstream 多, downstream 0 → 근본 원인일 가능성 높음
        Gateway 특성: upstream 0, downstream 多 → 최상위 진입점
        """
        try:
            blast_radius = BlastRadiusService()
            deps = blast_radius.get_dependencies(node.service_name)
            if not deps:
                return 0.5  # 의존성 정보 없으면 중립 점수

            # set 변환: 동일 서비스 간 다중 dependency_type 중복 카운트 방지 (§10.1)
            # 예: A→DB (sync, critical) + A→DB (async, medium) → DB upstream은 A 1개
            upstream_services = {d.source_service for d in deps["upstream"]}
            downstream_services = {d.target_service for d in deps["downstream"]}
            upstream_count = len(upstream_services)
            downstream_count = len(downstream_services)

            # 나를 호출하는 서비스(upstream)가 많고, 내가 호출하는(downstream) 없으면
            # → DB, Message Queue 등 인프라 코어 서비스
            if downstream_count == 0 and upstream_count > 0:
                return 1.0

            # upstream 비율이 높을수록 하부 인프라 (근본 원인 후보)
            return upstream_count / max(1, upstream_count + downstream_count)
        except Exception:
            return 0.5
```

**수정 근거**: 기존 코드는 `upstream == 0 and downstream > 0`일 때 1.0을 반환했으나,
실제 `BlastRadiusService`에서 DB 서비스는 `upstream`이 많고 `downstream`이 0이므로 방향이 역전되어 있었다.
`_analyze_cascading_impact()` (service.py L246–253)도 `deps["upstream"]`의 `source_service`를
연쇄 영향 대상으로 추적하므로, 동일한 방향 해석을 적용한다.

### 3.5 Factor 4: Historical Score (과거 패턴)

> **O(N×M) → O(N+M) 최적화 (§10.4)**: 기존에는 `co_occurrence_data: list`를 매 노드마다 전체 순회했다.
> 노드 N=200, Co-occurrence M=1000일 때 20만 회 루프가 발생하여 파이썬에서 300~500ms 소요.
> `rank()` 진입 시 `dict[str, list[CorrelationResult]]` 인덱스를 O(M) 1회 구축하고,
> 각 노드는 자신의 `event_type` 키로 O(1) 조회 후 관련 결과만 순회하므로 O(K) (K ≪ M).

```python
    # rank() 내부에서 1회 인덱싱 (§3.1 참조)
    # co_occurrence_index: dict[str, list[CorrelationResult]] = defaultdict(list)
    # for result in co_occurrence_data:
    #     co_occurrence_index[result.pair.event_type_a].append(result)
    #     co_occurrence_index[result.pair.event_type_b].append(result)

    def _historical_score(
        self,
        node: EventNode,
        co_occurrence_index: dict[str, list[CorrelationResult]],
    ) -> float:
        """과거 동시발생 패턴에서 이 이벤트가 원인인 빈도

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
            return 0.5  # 이력 없으면 중립
        return cause_count / total
```

> **✅ 최적화 구현 완료 (§10.4)**: `CoOccurrenceTracker.analyze_tick()`이 60초 주기로 실행될 때
> rolling 누적 결과를 `CorrelationIndex` (frozen dataclass, Copy-on-Write) 형태로
> 사전 구축하여 제공한다. `get_correlation_index()`로 O(1) 키 조회가 가능하며,
> `_rebuild_correlation_index()`에서 pair.key 기준 중복 제거 + 최대 500개 유지.

---

## 4. 정규화 및 순위 산정

> **Sum-normalization → Softmax 변경 (§10.2)**: 기존 `scores["total"] / total` 방식은
> 이벤트 100개 시 1위 점수가 5% 수준으로 희석되어 대시보드 신뢰도가 하락했다.
> Softmax(temperature)로 변경하여 확률 해석(합=1.0)을 유지하면서 격차를 극대화한다.

### 4.1 Softmax 정규화

```python
    # 설정 가능 파라미터
    SOFTMAX_TEMPERATURE = 1.0        # T→0: winner-take-all, T→∞: uniform
    TOP_P_THRESHOLD = 0.95           # 누적 확률 95%까지만 반환
    MIN_CANDIDATE_SCORE = 0.01       # 개별 점수 1% 미만은 즉시 제외
    TIMESTAMP_EPSILON = 0.001        # 1ms 이내는 동시 발생으로 간주

    def _normalize_and_rank(
        self, raw_entries: list[dict], dag: EventDAG
    ) -> list[RootCauseCandidate]:
        """원시 점수 → Softmax 정규화 → Top-P 필터링 → Tie-breaker 순위 부여"""
        if not raw_entries:
            return []

        import math

        totals = [e["total"] for e in raw_entries]
        max_total = max(totals) if totals else 1.0
        temperature = self.SOFTMAX_TEMPERATURE

        # Softmax: exp((score - max) / T) / Σexp((score - max) / T)
        # max를 빼는 이유: 수치 안정성 (overflow 방지)
        exp_scores = [
            math.exp((e["total"] - max_total) / temperature)
            for e in raw_entries
        ]
        exp_sum = sum(exp_scores)

        candidates = []
        for i, entry in enumerate(raw_entries):
            normalized_score = exp_scores[i] / exp_sum if exp_sum > 0 else 0.0
            node = dag.nodes[entry["node_id"]]
            component = entry["component"]

            evidence = self._build_evidence(node, entry, component)
            descendants = component.get_descendants(node)

            candidates.append(RootCauseCandidate(
                event_node=node,
                score=round(normalized_score, 4),
                rank=0,  # 아래 Tie-breaker에서 설정
                evidence=evidence,
                contributing_factors=entry,
                affected_services=list({d.service_name for d in descendants}),
                cascade_depth=self._max_depth_from(node, component),
            ))

        # ── Deterministic Tie-breaker 4단계 정렬 (§10.5) ──
        # timestamp를 floor 양자화하여 Jitter/Clock Skew 방어
        # CoOccurrenceTracker._infer_direction()의 simultaneous_threshold(0.001s)과 동일 해상도
        epsilon = self.TIMESTAMP_EPSILON
        candidates.sort(key=lambda c: (
            -c.score,                                          # 1차: 점수 높은 순
            -c.cascade_depth,                                  # 2차: cascade 깊은 순
            int(c.event_node.timestamp / epsilon),             # 3차: 먼저 발생한 순 (양자화)
            c.event_node.event_id,                             # 4차: UUID 알파벳순 (결정론적)
        ))
        for i, c in enumerate(candidates):
            object.__setattr__(c, 'rank', i + 1)

        # ── Top-P + Min Threshold 하이브리드 필터링 (§10.2) ──
        candidates = self._filter_top_p(candidates)

        return candidates
```

### 4.2 Top-P + Min Threshold 하이브리드 필터링

```python
    def _filter_top_p(self, candidates: list[RootCauseCandidate]) -> list[RootCauseCandidate]:
        """누적 확률 Top-P + 개별 최소 임계값으로 필터링.

        Top-K(고정 개수)와 달리, 장애 규모에 따라 반환 개수가 유연하게 조절된다.
        예: 1위가 90%면 2개만 반환, 상위 3개가 30%씩이면 4개 반환.

        균등 분포 방어: min_candidate_score 미만은 즉시 컷오프하여
        100개 이벤트가 모두 1%씩인 시나리오에서 전부 반환되는 것을 방지.
        """
        cumulative = 0.0
        filtered = []
        for c in candidates:  # 이미 score 내림차순 정렬
            if c.score < self.MIN_CANDIDATE_SCORE:
                break
            filtered.append(c)
            cumulative += c.score
            if cumulative >= self.TOP_P_THRESHOLD:
                break
        return filtered if filtered else candidates[:1]  # 최소 1개 보장
```

### 4.3 Tie-breaker 정렬 규칙

| 순위 | 기준 | 방향 | 근거 |
|------|------|------|------|
| **1차** | `score` | 내림차순 | Softmax 정규화된 확률 점수 |
| **2차** | `cascade_depth` | 내림차순 | 더 깊은 연쇄 = 더 큰 영향력 → 근본 원인 우선 |
| **3차** | `timestamp` (양자화) | 오름차순 | 먼저 발생한 이벤트가 원인일 가능성 ↑ |
| **4차** | `event_id` | 오름차순 | UUID 알파벳순으로 완전한 결정론적 보장 |

> **Timestamp 양자화 (§10.5)**: `round(t, 3)` 대신 `int(t / epsilon)`으로 floor 양자화를 사용한다.
> `round()`는 경계값(0.0005)에서 비대칭적 반올림이 발생하여 순서를 왜곡할 수 있다.
> floor 양자화는 항상 내림이므로 경계값 문제가 없으며,
> `CoOccurrenceTracker._infer_direction()`의 `simultaneous_threshold=0.001`과 동일한 해상도를 유지한다.

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
| **단위** | Softmax temperature=0.5 vs 2.0 | T↓ 시 1위 점수 ↑, T↑ 시 균등화 |
| **단위** | Top-P 필터링 (P=0.95): 1위 90% | 반환 후보 ≤ 2개 |
| **단위** | Top-P 필터링: 상위 10개 각 10% | Min threshold(1%)로 10개 반환 후 컷 |
| **단위** | Tie-breaker: 완전 동일 score 2개 | cascade_depth → timestamp → event_id 순 결정론적 |
| **단위** | Timestamp 양자화: 0.3ms 차이 2개 노드 | 동일 양자화값 → event_id로 결정 |
| **통합** | 단절 서브그래프 2개 (A그룹 t=100, B그룹 t=200) | B그룹 루트의 temporal_score=1.0 (왜곡 없음) |
| **통합** | 컴포넌트 가중치: 5노드(3서비스) vs 100노드(2서비스) | 3서비스 컴포넌트의 1위가 전역 1위 |
| **통합** | BlastRadius 방향: DB(upstream=3, downstream=0) | blast_score=1.0 (방향 역전 수정 검증) |
| **통합** | 다중 dependency_type: A→DB sync + A→DB async | upstream_count=1 (set 중복 제거) |
| **통합** | Historical 인덱싱: N=200, M=1000 | O(N+M) 이내 완료 (< 50ms) |
| **통합** | Co-occurrence index: event_type "X" 관련 5건 | _historical_score가 5건만 순회 |

---

## 10. 설계 결정 기록 (Design Decision Log)

v1.1.0에서 반영된 5가지 설계 결정의 상세 근거를 기록한다.

### 10.1 Blast Radius 방향 정의 수정 (Critical)

**문제**: 기존 `_blast_radius_score`에서 `upstream_count == 0 and downstream_count > 0`일 때 DB/MQ로 간주하여 1.0점을 부여했으나, 실제 `BlastRadiusService`의 방향 정의와 정반대였다.

**코드 근거** — `blast_radius/service.py` L162–166:
```python
# get_dependencies(service) 반환값
upstream = [d for d in self._dependencies if d.target_service == service]
downstream = [d for d in self._dependencies if d.source_service == service]
```

- `add_dependency(source="OrderService", target="DB")` = "OrderService가 DB를 호출"
- `get_dependencies("DB").upstream` = DB를 **호출하는** 서비스들 (많음)
- `get_dependencies("DB").downstream` = DB가 **호출하는** 서비스들 (0)

**`_analyze_cascading_impact()`** (service.py L246–253)도 `deps["upstream"]`의 `source_service`를 연쇄 영향 대상으로 추적하므로, 동일한 방향 해석을 적용한다.

**결정**: 조건을 `downstream_count == 0 and upstream_count > 0`으로 역전하고, 비율 산식도 `upstream_count / (upstream_count + downstream_count)`으로 변경.

**추가 방어 — 고유 서비스 집합**: 동일 서비스 간 다중 `dependency_type` 등록 시 카운트 부풀림 방지를 위해 `set` 변환을 사용한다.
```python
upstream_services = {d.source_service for d in deps["upstream"]}     # set
downstream_services = {d.target_service for d in deps["downstream"]}  # set
```
BFS 순환 방어는 `_analyze_cascading_impact()`의 `all_affected: set` + `not in all_affected` 체크로 이미 구현되어 있으나, 카운트 산정 시의 중복은 별도 방어가 필요하다.

### 10.2 Softmax 정규화 + Top-P 필터링

**문제**: 기존 Sum-normalization(`score / total`)은 이벤트 100개 시 1위 점수가 5% 수준으로 희석되어 대시보드 신뢰도가 하락했다.

**3안 비교**:
| 방식 | 합=1 | 격차 극대화 | 확률 해석 | 선택 |
|------|------|-----------|----------|------|
| Sum-norm | ✅ | ❌ | ✅ | ❌ |
| Max-scaling | ❌ | ✅ | ❌ | ❌ |
| **Softmax** | ✅ | ✅ | ✅ | ✅ |

**Temperature 파라미터**: `SELFHEALING_ROOT_CAUSE_SOFTMAX_TEMPERATURE` 환경변수로 노출.
- T → 0: winner-take-all (1위만 100%)
- T → ∞: uniform (모두 동일)
- 기본값 1.0

**Top-P (Nucleus Filtering)**: Top-K(고정 개수)보다 장애 규모에 따라 유연하게 조절된다.
- 누적 확률이 `TOP_P_THRESHOLD` (기본 0.95)에 도달하면 컷오프
- 개별 점수가 `MIN_CANDIDATE_SCORE` (기본 0.01) 미만이면 즉시 컷오프
- 하이브리드 방식으로 균등 분포 시나리오도 방어

### 10.3 Connected Component 단위 랭킹

**문제**: 251번 문서에서 "단절된 독립 장애 2개라도 1개 DAG 객체로 반환"하기로 결정했다. 이 상태에서 `_temporal_score`/`_topology_score`를 전체 DAG 기준으로 계산하면, 나중에 발생한 독립 장애 그룹의 진짜 원인이 시간 점수를 심각하게 손해본다.

**왜곡 시나리오**:
| 이벤트 | 컴포넌트 | 타임스탬프 | 전체 DAG temporal | 컴포넌트별 temporal |
|--------|---------|-----------|------------------|-------------------|
| A (DB) | 1 | 100 | 1.0 | 1.0 |
| B | 1 | 102 | 0.96 | 0.6 |
| C (네트워크) | 2 | 145 | **0.10** ← 왜곡 | **1.0** ← 정상 |
| D | 2 | 150 | 0.0 | 0.0 |

**인프라 근거** — `EventDAG.get_connected_components()` (event_graph.py L225–250):
- 무방향 인접리스트 DFS로 서브그래프 분리
- `_sub_dag()`가 각 component마다 독립적 `window_start`, `window_end`, `root_nodes` 재계산

**2단계 랭킹 전략**:
1. **로컬**: `_topology_score`, `_temporal_score`는 컴포넌트 내부 노드만 대상
2. **전역**: `_blast_radius_score`, `_historical_score`는 외부 데이터 기반이므로 전역

**컴포넌트 가중치** — 노드 수가 아닌 **고유 서비스 수** 기반:
```python
component_weight = len({n.service_name for n in component.nodes.values()}) / total_unique_services
```
노드 수 기반은 로그 폭풍이나 retry 이벤트로 부풀려질 수 있으나, 고유 서비스 수는 실제 장애 범위를 반영한다.
`EventDAG.to_dict()` (event_graph.py L277)에서 이미 `services: sorted({n.service_name for ...})` 형태로 컴포넌트별 고유 서비스를 추출하고 있어, 일관된 패턴이다.

### 10.4 Historical Score O(N×M) → O(N+M) 인덱싱

**문제**: 기존 `_historical_score`는 매 노드마다 `co_occurrence_data` 리스트 전체를 순회했다. N=200, M=1000일 때 20만 회 루프 = 파이썬에서 300–500ms.

**인덱싱 전략**:
```python
# rank() 진입 시 O(M) 1회 구축
co_occurrence_index: dict[str, list[CorrelationResult]] = defaultdict(list)
for result in co_occurrence_data:
    co_occurrence_index[result.pair.event_type_a].append(result)
    co_occurrence_index[result.pair.event_type_b].append(result)
```

`EventPairKey`가 알파벳 정렬을 보장하므로(`__post_init__`에서 교환, co_occurrence_tracker.py L65–71), 양쪽 키에 모두 등록하는 것이 안전하다.

**✅ 최적화 구현 완료 — CoOccurrenceTracker 위임**:
- `analyze_tick()` 결과를 `_accumulated_results`에 rolling 누적 (pair.key 기준 중복 제거)
- 누적 결과를 `CorrelationIndex` (frozen dataclass, Copy-on-Write)로 원자적 교체
- `CoOccurrenceSnapshot`과 동일한 Copy-on-Write 패턴
- Ranker는 `get_correlation_index()`로 인덱스 빌드 비용 0, O(1) 조회만 수행
- `_max_accumulated_results = 500`으로 메모리 상한 제어

**설계 반영**: `analyze_tick()`은 이상 탐지된 결과만 반환하므로, rolling 누적으로 과거 이력을 보존한다.

### 10.5 Deterministic Tie-breaker + Timestamp 양자화

**문제**: 시간 간격 0 + 동일 토폴로지인 형제 노드들이 소수점 4자리까지 동일한 점수를 가질 수 있다.

**4단계 튜플 정렬**:
```python
candidates.sort(key=lambda c: (
    -c.score,                                      # 1차
    -c.cascade_depth,                              # 2차
    int(c.event_node.timestamp / TIMESTAMP_EPSILON),  # 3차 (양자화)
    c.event_node.event_id,                         # 4차 (결정론적)
))
```

**Severity를 정렬 기준에 포함하지 않는 이유**:
- `EventNode.data: dict`에 severity 포함이 보장되지 않음 (스키마 미정)
- Severity는 **결과(증상)**이지 원인이 아님 — DB Pool 고갈(Warning)이 Emergency 서비스 다운의 진짜 원인일 수 있음
- `cascade_depth` + `timestamp`로 충분한 차별화가 가능

**Timestamp `round()` 대신 floor 양자화를 선택한 이유**:
- `round(1000.0004999, 3)` = 1000.0, `round(1000.0005001, 3)` = 1000.001 → 경계값 비대칭
- `int(t / 0.001)`은 항상 floor이므로 경계값 문제 없음
- `CoOccurrenceTracker._infer_direction()` (co_occurrence_tracker.py L388)의 `simultaneous_threshold=0.001`과 동일한 해상도를 유지하여 프로젝트 전체 일관성 확보
- `TIMESTAMP_EPSILON`을 설정으로 노출하여 운영 환경의 clock skew 특성에 맞게 조절 가능

**기존 Deterministic 정렬 선례** — `EventDAG.get_critical_path()` (event_graph.py L186–196):
```python
# 동일 길이 경로 tie-breaking: event_id 알파벳 순
for child in sorted(children):
    child_path = _longest_path_from(child)
    if len(child_path) == len(best_child_path) and child_path < best_child_path:
        best_child_path = child_path
```
