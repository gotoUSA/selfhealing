# 251. Event Graph — 이벤트 DAG 자동 구축

> **Version**: 1.0.0
> **Created**: 2026-02-20
> **Status**: Approved
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `services/correlation_engine/event_graph.py`

---

## 0. 요약

EventBus에서 발생하는 이벤트를 시간순으로 수집하고, `BlastRadiusService`의 서비스 의존성 그래프와 교차하여 **방향성 비순환 그래프(DAG)**를 자동 구축한다. 이 DAG가 Root Cause Ranker(253)와 Incident Timeline(255)의 입력이 된다.

---

## 1. 현재 상태 분석

### 1.1 기존 "그래프" 관련 코드

| 모듈 | 한계 |
|------|------|
| `IncidentGroupManager.get_cascading_pattern()` | 시간 임계값(30초/60초)만 사용. 서비스 의존성 미교차. "simultaneous / cascading / independent" 3가지 단순 분류만 |
| `causation_chain` (CascadeEventAuditor) | 선형 리스트(A → B → C). 단일 cascade event 내부만. **방향성 그래프가 아님** |
| `BlastRadiusService.get_dependencies()` | upstream/downstream **서비스 토폴로지**는 있으나, **이벤트 그래프가 아님** |

### 1.2 하고자 하는 것

서비스 의존성(정적) + 이벤트 발생 시간(동적) → **인과관계 방향이 있는 이벤트 DAG**

---

## 2. 핵심 자료구조

### 2.1 EventNode

```python
@dataclass(frozen=True)
class EventNode:
    """DAG의 노드 — 하나의 이벤트 발생 인스턴스"""
    event_id: str                 # 고유 식별자 (UUID)
    event_type: str               # EventType.value (예: "circuit_breaker_opened")
    service_name: str             # 이벤트가 발생한 서비스
    timestamp: float              # Unix timestamp
    data: dict                    # 원본 event.data (읽기 전용)
    correlation_id: str | None    # 분산 추적 ID
```

### 2.2 CausalEdge

```python
@dataclass(frozen=True)
class CausalEdge:
    """DAG의 엣지 — 인과관계 방향"""
    source: EventNode             # 원인 이벤트
    target: EventNode             # 결과 이벤트
    confidence: float             # 인과관계 확신도 (0.0 ~ 1.0)
    evidence_type: str            # "dependency" | "temporal" | "correlation_id" | "co_occurrence"
    time_gap_seconds: float       # source → target 시간 간격
```

### 2.3 EventDAG

```python
@dataclass
class EventDAG:
    """특정 인시던트 윈도우의 인과관계 그래프"""
    incident_id: str
    window_start: float
    window_end: float
    nodes: dict[str, EventNode]       # event_id → EventNode
    edges: list[CausalEdge]
    root_nodes: list[EventNode]       # 진입차수(in-degree) = 0인 노드들
    leaf_nodes: list[EventNode]       # 진출차수(out-degree) = 0인 노드들

    def get_ancestors(self, node: EventNode) -> list[EventNode]: ...
    def get_descendants(self, node: EventNode) -> list[EventNode]: ...
    def get_critical_path(self) -> list[EventNode]: ...
    def to_dict(self) -> dict: ...    # 직렬화 (Postmortem/Dashboard용)
```

---

## 3. DAG 구축 알고리즘

### 3.1 엣지 생성 규칙 (4단계 Evidence)

인과관계 방향성을 추론하는 **4단계 증거(Evidence)**:

```
Priority 1: Correlation ID 일치
  → 동일 correlation_id를 가진 이벤트는 같은 트랜잭션
  → 시간순으로 source → target 방향
  → confidence = 0.95

Priority 2: 서비스 의존성 + 시간순
  → BlastRadiusService.get_dependencies(target.service)에서
    source.service가 upstream에 있고
    source.timestamp < target.timestamp
  → "업스트림 서비스가 먼저 장애 → 다운스트림에 전파"
  → confidence = 0.85

Priority 3: 동시 발생 이력 (Co-occurrence)
  → CoOccurrenceTracker(252)가 학습한 패턴에서
    (source.event_type, target.event_type) 쌍의
    과거 동시 발생 빈도가 ZScore 이상치
  → confidence = tracker가 보고한 점수 × 0.7

Priority 4: 시간 근접성 (Fallback)
  → 같은 시간 윈도우 내이지만 위 3가지에 해당 안 됨
  → 시간순으로 방향 추정
  → confidence = max(0.3, 1.0 - time_gap / window_size)
```

### 3.2 구축 절차 (Pseudocode)

```python
class EventGraphBuilder:
    def __init__(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        self._blast_radius = blast_radius_service
        self._co_occurrence = co_occurrence_tracker

    def build_dag(self, events: list[SelfHealingEvent], window_seconds: float) -> EventDAG:
        """시간 윈도우 내 이벤트 → DAG 구축"""
        # 1) 이벤트 → EventNode 변환, 시간순 정렬
        nodes = self._create_nodes(events)
        sorted_nodes = sorted(nodes.values(), key=lambda n: n.timestamp)

        # 2) 모든 노드 쌍에 대해 엣지 후보 생성
        edge_candidates: list[CausalEdge] = []
        for i, source in enumerate(sorted_nodes):
            for target in sorted_nodes[i + 1:]:
                edge = self._evaluate_causation(source, target)
                if edge and edge.confidence >= self._min_confidence:
                    edge_candidates.append(edge)

        # 3) 순환 제거 (시간순 보장으로 자연스럽게 DAG)
        #    - 이미 source.timestamp < target.timestamp 보장
        edges = edge_candidates

        # 4) 전이적 축소 (Transitive Reduction)
        #    - A→B, B→C, A→C가 있으면 A→C 제거 (간접 인과)
        edges = self._transitive_reduction(nodes, edges)

        # 5) root/leaf 노드 식별
        root_nodes = self._find_roots(nodes, edges)
        leaf_nodes = self._find_leaves(nodes, edges)

        return EventDAG(
            incident_id=self._generate_incident_id(),
            window_start=sorted_nodes[0].timestamp,
            window_end=sorted_nodes[-1].timestamp,
            nodes=nodes,
            edges=edges,
            root_nodes=root_nodes,
            leaf_nodes=leaf_nodes,
        )

    def _evaluate_causation(self, source: EventNode, target: EventNode) -> CausalEdge | None:
        """4단계 Evidence로 인과관계 평가"""
        time_gap = target.timestamp - source.timestamp
        if time_gap <= 0 or time_gap > self._window_seconds:
            return None

        # Priority 1: Correlation ID
        if (source.correlation_id and
            source.correlation_id == target.correlation_id):
            return CausalEdge(source, target, 0.95, "correlation_id", time_gap)

        # Priority 2: 서비스 의존성
        if source.service_name != target.service_name:
            deps = self._blast_radius.get_dependencies(target.service_name)
            if deps and source.service_name in [d.source_service for d in deps.upstream]:
                return CausalEdge(source, target, 0.85, "dependency", time_gap)

        # Priority 3: Co-occurrence 이력
        co_score = self._co_occurrence.get_pair_score(
            source.event_type, target.event_type
        )
        if co_score and co_score > 0.5:
            return CausalEdge(source, target, co_score * 0.7, "co_occurrence", time_gap)

        # Priority 4: 시간 근접성 (Fallback)
        temporal_confidence = max(0.3, 1.0 - time_gap / self._window_seconds)
        if temporal_confidence >= self._min_confidence:
            return CausalEdge(source, target, temporal_confidence, "temporal", time_gap)

        return None
```

### 3.3 전이적 축소 (Transitive Reduction)

DAG의 가독성을 위해, 간접 경로가 존재하면 직접 엣지를 제거한다:

```
Before:  A ──→ B ──→ C
         A ─────────→ C    (A→C 직접 엣지)

After:   A ──→ B ──→ C     (A→C 제거 — B를 거치는 간접 경로 존재)
```

단, `confidence`가 높은 직접 엣지(≥ 0.9)는 유지한다 (강한 직접 인과관계).

---

## 4. 시간 복잡도 분석

| 단계 | 복잡도 | 비고 |
|------|-------|------|
| 노드 생성 | O(N) | N = 윈도우 내 이벤트 수 |
| 엣지 후보 생성 | O(N²) | 모든 쌍 평가 |
| 의존성 조회 | O(N² × D) | D = 평균 의존성 수 (보통 ≤ 10) |
| 전이적 축소 | O(N³) 최악 | DFS 기반 도달성 검사 |

**N의 실제 범위**: 단일 인시던트 윈도우(5분)에 발생하는 이벤트는 보통 **10~50개**이므로, O(N³)도 < 1ms.

설정으로 제한:
- `max_events_per_dag: int = 200` — 초과 시 최근 N개만 사용
- `min_confidence: float = 0.4` — 미만 엣지 제거로 희소 그래프 유지

---

## 5. 직렬화 포맷

`EventDAG.to_dict()` 출력은 Postmortem과 Dashboard에서 소비한다:

```json
{
  "incident_id": "inc_2026-02-20_14:32:01_a1b2c3",
  "window": {"start": 1740062521.0, "end": 1740062821.0},
  "nodes": [
    {
      "event_id": "evt_001",
      "event_type": "circuit_breaker_opened",
      "service_name": "payment-service",
      "timestamp": 1740062530.0
    }
  ],
  "edges": [
    {
      "source": "evt_001",
      "target": "evt_002",
      "confidence": 0.85,
      "evidence_type": "dependency",
      "time_gap_seconds": 2.3
    }
  ],
  "root_causes": ["evt_001"],
  "leaf_effects": ["evt_005", "evt_006"]
}
```

---

## 6. 테스트 전략

| 테스트 유형 | 시나리오 | 검증 |
|------------|---------|------|
| **단위** | 단일 이벤트 → 노드 1개, 엣지 0개 | root = leaf = 해당 노드 |
| **단위** | correlation_id 일치하는 2개 이벤트 | confidence = 0.95 엣지 |
| **단위** | upstream → downstream 시간순 | evidence_type = "dependency" |
| **단위** | 전이적 축소 | A→B→C일 때 A→C 제거 확인 |
| **통합** | BlastRadius mock + 5개 이벤트 | 올바른 DAG 토폴로지 |
| **시나리오** | "DB Pool 고갈 → CB OPEN × 3 → Emergency" 재현 | DB Pool이 root_cause로 식별 |
