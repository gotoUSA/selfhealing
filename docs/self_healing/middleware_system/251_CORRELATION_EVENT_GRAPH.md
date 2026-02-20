# 251. Event Graph — 이벤트 DAG 자동 구축

> **Version**: 1.1.0
> **Created**: 2026-02-20
> **Updated**: 2026-02-20
> **Status**: Implemented
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

### 3.1 엣지 생성 규칙 (5단계 Evidence)

인과관계 방향성을 추론하는 **5단계 증거(Evidence)**:

```
Priority 1: Correlation ID 일치
  → 동일 correlation_id를 가진 이벤트는 같은 트랜잭션
  → 시간순으로 source → target 방향
  → confidence = 0.95

Priority 1.5: Contextual Matching (§8 참조)
  → correlation_id가 없는 이벤트 쌍에서
    source.data["service_name"] == target.data["service_name"]이고
    event_type이 서로 다른 경우 (동일 서비스 내 연쇄 장애)
  → confidence = 0.90

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
  → 같은 시간 윈도우 내이지만 위 4가지에 해당 안 됨
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
        # 1) 이벤트 → EventNode 변환, 결정적 정렬 (timestamp, event_id)
        nodes = self._create_nodes(events)
        sorted_nodes = sorted(nodes.values(), key=lambda n: (n.timestamp, n.event_id))

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
        """5단계 Evidence로 인과관계 평가"""
        time_gap = target.timestamp - source.timestamp
        if time_gap < 0 or time_gap > self._window_seconds:
            return None

        # Priority 1: Correlation ID
        if (source.correlation_id and
            source.correlation_id == target.correlation_id):
            return CausalEdge(source, target, 0.95, "correlation_id", time_gap)

        # Priority 1.5: Contextual Matching (service_name 일치)
        #   → §8 참조: correlation_id 누락 시 event.data["service_name"] 교차 검사
        if (not source.correlation_id or not target.correlation_id):
            source_svc = source.data.get("service_name", "")
            target_svc = target.data.get("service_name", "")
            if source_svc and source_svc == target_svc and source.event_type != target.event_type:
                return CausalEdge(source, target, 0.90, "contextual", time_gap)

        # Priority 2: 서비스 의존성
        if source.service_name != target.service_name:
            deps = self._blast_radius.get_dependencies(target.service_name)
            if deps and source.service_name in [d.source_service for d in deps["upstream"]]:
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
  "component_count": 1,
  "components": [
    {"root": "evt_001", "node_count": 6, "services": ["payment-service", "order-service"]}
  ],
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
| **단위** | Diamond 의존성 (A→B→D, A→C→D) | 전이적 축소가 B→D, C→D를 보존 |
| **단위** | 동일 타임스탬프 이벤트 5개 | tiebreaker(event_id) 기반 결정적 정렬 |
| **스트레스** | 동일 타임스탬프 이벤트 200개 (Event Storm) | max_events_per_dag 절삭 + OOM 미발생 |
| **단위** | Disconnected Components (2개 독립 장애) | component_count=2, 각 root 개별 식별 |
| **단위** | Debounce — 60초 이내 재트리거 | 두 번째 트리거 무시 또는 윈도우 연장 |

---

## 7. Window Trigger 전략 — Snapshot 방식

### 7.1 결정: Critical 이벤트 앵커 기반 Snapshot

**Sliding Window 대신 Snapshot 방식을 채택**한다. Critical 이벤트 발생 시점을 앵커로 앞뒤 버퍼를 캡처하는 방식은 PagerDuty, Datadog 등 글로벌 옵저버빌리티 도구들의 표준적인 접근법과 일치하며, 지속적 Sliding Window가 유발하는 CPU 사이클 낭비를 원천 방지한다.

### 7.2 선택 근거

| 옵션 | 설명 | 판정 |
|------|------|------|
| **A. Snapshot (채택)** | 트리거 이벤트 기준 앞뒤 N초 캡처 | ✅ |
| B. Sliding Window | 지속적으로 5분 윈도우를 밀면서 DAG 재구축 | ❌ 고비용 안티패턴 |
| C. IncidentGroup 연동 | 그룹 CLOSE 시점에 DAG 구축 | ❌ 지연 과대 (최대 10분+2분) |

**기존 코드 근거**: `IncidentGroupManager`는 이미 고정 시간 윈도우(`window_seconds=600`) + 비활성 종료(`inactivity_seconds=120`) 패턴을 사용한다.

```
# incident_group.py L500-L535
if now - created_ts >= self.window_seconds:   # 타임아웃 체크
    return True
if now - last_ts >= self.inactivity_seconds:   # 비활성 체크
    return True
```

### 7.3 트리거 이벤트 목록

| EventType | Priority |
|-----------|----------|
| `EMERGENCY_LEVEL_CHANGED` | CRITICAL |
| `EMERGENCY_ACTIVATED` | CRITICAL |
| `CIRCUIT_BREAKER_OPENED` | HIGH |
| `SECURITY_VIOLATION_CRITICAL` | CRITICAL |
| `KILL_SWITCH_ACTIVATED` | CRITICAL |
| `LOAD_SHEDDING_LEVEL_CHANGED` (level ≥ 2) | HIGH |

### 7.4 윈도우 캡처 설정

```python
# settings/correlation.py (신규)
correlation_lookback_seconds: int = Field(
    default=300, ge=30, le=900,
    description="트리거 이벤트 기준 과거 캡처 범위 (초)",
)
correlation_lookahead_seconds: int = Field(
    default=60, ge=10, le=300,
    description="트리거 이벤트 기준 미래 캡처 범위 (초)",
)
```

이벤트 원천: `SelfHealingEventBus._event_history` 링버퍼(최대 1000건)에서 시간 범위 슬라이싱.

```
# bus/__init__.py — _event_history 링버퍼 (최대 1000건)
# 트리거 시점 기준 [anchor - lookback, anchor + lookahead] 범위의
# 이벤트를 슬라이싱하여 build_dag()에 전달
```

### 7.5 Debounce & Cooldown 메커니즘

대형 장애(Event Storm) 시 수십 개의 Critical 이벤트가 동시 발생할 수 있다. 매번 DAG를 생성하면 분석 엔진 과부하 + Alert Fatigue를 유발하므로, **Debounce 메커니즘**을 필수로 포함한다.

**기존 선례**: `RateLimitCoordinator._should_emit_event()` 패턴을 그대로 차용한다.

```
# rate_limit_coordinator/coordinator.py L98-L155
self._last_event_emit_times: dict[str, float] = {}
self._debounce_lock = threading.Lock()

def _should_emit_event(self, key: str) -> bool:
    now = time.time()
    with self._debounce_lock:
        last_time = self._last_event_emit_times.get(key, 0)
        if now - last_time < self._config.debounce_window_seconds:
            return False
        self._last_event_emit_times[key] = now
        return True
```

**EventGraphBuilder용 Debounce 설계**:

```python
class EventGraphTrigger:
    """DAG 빌드 트리거 — Debounce 내장."""

    def __init__(self, cooldown_seconds: float = 60.0):
        self._cooldown_seconds = cooldown_seconds
        self._last_build_times: dict[str, float] = {}  # namespace → last_build_time
        self._lock = threading.Lock()

    def should_build(self, namespace: str) -> bool:
        """동일 namespace에서 cooldown 이내 재트리거 방지."""
        now = time.time()
        with self._lock:
            last = self._last_build_times.get(namespace, 0)
            if now - last < self._cooldown_seconds:
                return False  # 기존 DAG 윈도우 연장 또는 무시
            self._last_build_times[namespace] = now
            return True
```

dedup 키는 **`namespace` 단위**로 설정한다. 서비스 단위로 하면 연쇄 장애 시 payment-service 트리거와 order-service 트리거가 각각 DAG를 생성하여 중복이 발생한다.

추가 선례: `IncidentGroupManager.inactivity_seconds`(120초), `NotificationAggregator.window_seconds`(60초), `UnifiedNotificationManager._cooldown_cache`, `RedisCooldownStore`(Redis TTL 기반) 등 코드베이스 전체에서 일관적으로 사용되는 패턴이다.

---

## 8. Correlation ID 표준화 전략

### 8.1 현재 상태 — 42개 EventType 중 ~10개만 전달

`correlation_id`는 `SelfHealingEvent`의 **first-class Optional 필드**이다.

```
# bus/__init__.py L209-L217
@dataclass
class SelfHealingEvent:
    event_type: EventType
    data: dict[str, Any]
    source: str
    timestamp: datetime = ...
    priority: EventPriority = EventPriority.NORMAL
    correlation_id: str | None = None          # ← Optional
```

그러나 실제 `correlation_id`를 전달하는 이벤트는 **Saga 계열 ~10개뿐**이다.

| 카테고리 | 전달 여부 | 이벤트 수 |
|----------|----------|-----------|
| Saga (`_emit_event`에서 `correlation_id=instance.correlation_id`) | ✅ 전달 | ~10 |
| 시스템 상태 전이 (Emergency, Load Shedding, Region 등) | ❌ 자연스럽게 없음 | ~15 |
| 요청 기반이지만 누락 (CB, Security, Kill Switch 등) | ❌ **누락** | ~17 |

### 8.2 별도 trace_id 시스템의 비연동

`audit.trace` 모듈은 `contextvars.ContextVar`로 per-request `trace_id`를 관리하지만, EventBus `emit()`에 자동 주입되지 않는다.

```
# audit/trace.py — get_trace_id() → contextvars.ContextVar
# Django 미들웨어가 X-Request-ID / X-Trace-ID / traceparent 헤더에서 추출
# 그러나 bus.emit() 호출 시 이 trace_id가 correlation_id에 자동 전파되지 않음
```

### 8.3 대응 전략

**Phase 1 (Day-1)**: Priority 1 적용 범위가 좁음을 전제로 설계. Priority 2(서비스 의존성)와 Priority 4(시간 근접성)가 대부분의 엣지를 생성.

**Phase 2**: `emit()` wrapping으로 `get_trace_id()` → `correlation_id` 자동 주입. 커버리지 ~10개 → ~25개로 확대.

### 8.4 Priority 1.5: Contextual Matching (service_name 교차 검사)

`correlation_id`가 없을 때 Priority 4(시간 근접성)에 과도하게 의존하면 트래픽이 높은 구간에서 False Positive가 폭증한다.

**보조 식별자로 `event.data["service_name"]`을 사용**한다. 전수 조사 결과, `service_name`은 CB/Throttle SLA/Load Shedding 등 6개 이상의 이벤트 카테고리에서 공통으로 포함되는 가장 범용적인 비즈니스 식별자이다.

| data 키 | 출현 이벤트 카테고리 수 | 비고 |
|---------|----------------------|------|
| `service_name` | 6+ | CB, Throttle, Security 등 |
| `reason` | 5+ | 텍스트 매칭 부적합 |
| `instance_id` | 7 | Saga 전용 (이미 correlation_id 있음) |
| `incident_id` | 2 | Security, Emergency |

`order_id`, `user_id` 등은 Saga 이벤트에만 존재하고 Saga는 이미 correlation_id가 있으므로 중복이다. 따라서 **`service_name` 일치 한 가지만** Phase 1에서 구현한다.

```python
# 3.2 _evaluate_causation() 내 Priority 1.5 구현
if (not source.correlation_id or not target.correlation_id):
    source_svc = source.data.get("service_name", "")
    target_svc = target.data.get("service_name", "")
    if source_svc and source_svc == target_svc and source.event_type != target.event_type:
        return CausalEdge(source, target, 0.90, "contextual", time_gap)
```

---

## 9. CoOccurrenceTracker 조회 비용 — 인메모리 O(1) 보장

### 9.1 현재 상태

`CoOccurrenceTracker`는 **아직 구현되지 않았다** (설계 문서 252만 존재). `correlation_engine/` 디렉토리 자체가 코드베이스에 없으며, `258_GAP_ANALYSIS.md`에서 "구현 필요"로 명시.

### 9.2 BlastRadiusService.get_dependencies() — 완전 인메모리, 동기

```
# blast_radius/service.py L151-L167
def get_dependencies(self, service: str) -> dict[str, list[ServiceDependencyEdge]]:
    upstream = [d for d in self._dependencies if d.target_service == service]
    downstream = [d for d in self._dependencies if d.source_service == service]
    return {"upstream": upstream, "downstream": downstream}
```

- **동기(sync)**, 인메모리 리스트 컴프리헨션 — I/O 없음
- 싱글톤 인스턴스의 `self._dependencies` (Python list) 순회
- N=50 시 O(N²)=2,500회 호출해도 총 **< 0.1ms**

### 9.3 CoOccurrenceTracker 구현 시 필수 제약

루프 내부(O(N²))에서 Redis 등 외부 I/O를 태우면 1ms 제약 달성이 불가능하다. **인메모리 dict O(1) 조회를 필수**로 한다.

### 9.4 다중 인스턴스 동기화 — Copy-on-Write 패턴 (RWLock 아님)

엔진이 Scale-out 시 각 인스턴스 메모리의 dict가 파편화될 수 있다. 이를 위해 **Eventual Consistency** 동기화를 적용하되, 프로젝트 표준인 **Copy-on-Write** 패턴을 사용한다.

**선택 근거 — RWLock vs Copy-on-Write**:

| 옵션 | 프로젝트 사용 건수 | 판정 |
|------|-------------------|------|
| `RWLock` / `ReadWriteLock` | **0건** (코드베이스 전체) | ❌ 프로젝트 관례 불일치 |
| **Copy-on-Write** (GIL atomic swap) | `SystemMetricsCache`, `ResiliencePolicy` 등 다수 | ✅ 채택 |

**기존 선례**: `SystemMetricsCache.CachedMetrics`

```
# system_metrics_cache.py L24-L36
@dataclass(frozen=True)
class CachedMetrics:
    """
    frozen=True로 설정하여 읽기 시 동시성 문제를 원천 방지한다.
    백그라운드 스레드는 새 인스턴스를 생성하여 참조를 교체한다 (Copy-on-Write).
    Python GIL 하에서 참조 교체는 atomic이므로 Lock 불필요.
    """
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    ...
```

**CoOccurrenceTracker 설계**:

```python
@dataclass(frozen=True)
class CoOccurrenceSnapshot:
    """인메모리 Co-occurrence 점수 스냅샷 (Immutable)."""
    scores: dict[tuple[str, str], float]  # (event_type_a, event_type_b) → score

class CoOccurrenceTracker:
    """
    Co-occurrence 점수 조회 — 읽기: 인메모리 O(1), 쓰기: 백그라운드 동기화.

    SystemMetricsCache의 Copy-on-Write 패턴을 동일하게 적용한다.
    """
    def __init__(self):
        self._snapshot = CoOccurrenceSnapshot(scores={})  # GIL atomic read

    def get_pair_score(self, event_type_a: str, event_type_b: str) -> float | None:
        """O(1) dict lookup — Lock 불필요."""
        return self._snapshot.scores.get((event_type_a, event_type_b))

    def _refresh_from_backend(self):
        """백그라운드 Timer(daemon)에서 주기적 호출 (60초 간격)."""
        new_scores = self._pull_from_state_backend()  # Redis/DB
        self._snapshot = CoOccurrenceSnapshot(scores=new_scores)  # atomic swap
```

백그라운드 리프레시 패턴 선례: `EmergencyStateRefresher`(30초+jitter), `PrecomputedCacheWorker`, `ConfigPropagator`(Redis Pub/Sub) 등 프로젝트 내 15+곳에서 `threading.Timer(daemon=True)` 또는 `threading.Thread(daemon=True)` 사용.

---

## 10. DAG 분리 기준 — 1개 DAG 내 Disconnected Components

### 10.1 결정: 단절 서브그래프를 포함한 1개 EventDAG 반환

5분 윈도우 내 완전히 무관한 두 개의 독립 장애(예: 결제 서비스 장애 + 이미지 리사이징 장애)가 동시 발생 시, **2개의 단절된 서브그래프가 포함된 1개의 EventDAG 객체**로 반환한다.

### 10.2 선택 근거

| 옵션 | 설명 | 판정 |
|------|------|------|
| **A. 1개 DAG + Disconnected Components (채택)** | 정보 유실 없음, 숨겨진 공통 원인 발견 여지 보존 | ✅ |
| B. Connected Component별 분리 | 2개 EventDAG 반환 | ❌ 공통 원인 단서 유실 |

**기존 코드 근거**: `IncidentGroupManager`는 `get_cascading_pattern()` 에서 `"independent"` 패턴을 분류하지만 **별도 그룹으로 분리하지 않는다**. 동일 시간 윈도우의 모든 이벤트를 하나의 그룹으로 유지한다. 또한, 코드베이스 전체에 connected component 분리 로직은 존재하지 않는다.

### 10.3 Component Tagging — 직렬화 시 component_count 노출

`component_count > 1`은 SRE에게 "의존성 그래프에 잡히지 않는 인프라 레벨의 동시다발적 문제일 확률이 높다"는 강력한 통찰을 제공한다.

**EventDAG 메서드 추가**:

```python
@dataclass
class EventDAG:
    # ... 기존 필드 ...

    def get_connected_components(self) -> list[EventDAG]:
        """단절된 서브그래프를 개별 EventDAG로 분리."""
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
        # 각 component를 개별 EventDAG로 포장
        return [self._sub_dag(c) for c in components]

    def to_dict(self) -> dict:
        components = self.get_connected_components()
        return {
            "incident_id": self.incident_id,
            "window": {"start": self.window_start, "end": self.window_end},
            "component_count": len(components),
            "components": [
                {
                    "root": comp.root_nodes[0].event_id if comp.root_nodes else None,
                    "node_count": len(comp.nodes),
                    "services": sorted(set(n.service_name for n in comp.nodes.values())),
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
```

---

## 11. 동일 타임스탬프 처리 정책

### 11.1 문제

`_evaluate_causation()`에서 `time_gap <= 0 → return None` 이었으므로 동일 타임스탬프 이벤트 간 엣지가 생성되지 않았다. 그러나 동일 시각에 발생한 이벤트 간에도 다른 Evidence(서비스 의존성, correlation_id)로 인과관계를 추론할 수 있다.

### 11.2 결정: time_gap = 0 허용, 방향은 Evidence로 결정

```python
# 변경: time_gap <= 0 → time_gap < 0
if time_gap < 0 or time_gap > self._window_seconds:
    return None
```

- `time_gap = 0`이면 시간순으로는 방향을 결정할 수 없다
- Priority 1(correlation_id), Priority 1.5(contextual), Priority 2(서비스 의존성)에서 방향이 결정된다
- Priority 4(시간 근접성)에서 `time_gap = 0`이면 `confidence = max(0.3, 1.0 - 0/window) = 1.0`이 되므로, **`time_gap = 0`인 쌍에서 Priority 4는 적용하지 않는다** (Priority 1~3에서 잡히지 않으면 엣지 미생성)

### 11.3 결정적 정렬 (Deterministic Sort)

동일 타임스탬프 노드 간 정렬 순서가 비결정적이면 실행마다 DAG 토폴로지가 달라질 수 있다. **tiebreaker로 `event_id`를 사용**한다.

```python
# 변경 전: sorted(nodes.values(), key=lambda n: n.timestamp)
# 변경 후:
sorted_nodes = sorted(nodes.values(), key=lambda n: (n.timestamp, n.event_id))
```

`event_id`는 UUID이므로 결정적이고 충돌이 없다.

---

## 12. 테스트 전략 보완 — 의존성 그래프 depth 기준 및 엣지 케이스

### 12.1 max_depth=3 이 프로젝트 표준

프로덕션 의존성 그래프 depth는 **3이 사실상 표준**이다. 두 곳에서 독립적으로 동일한 기본값을 사용한다:

| 모듈 | depth 기본값 | 위치 |
|------|-------------|------|
| `BlastRadiusAnalyzer` (Chaos) | `max_depth=3` | `chaos/blast_radius_analyzer.py L188` |
| `ErrorBudgetPropagation` | `max_hops=3` (ge=1, le=10) | `settings/error_budget_propagation.py L62-L66` |

마이크로서비스 간 실제 호출 체인이 **API Gateway → Service → DB/Cache** 등 3홉 이내가 대부분이기 때문이다.

**주의**: `BlastRadiusService._analyze_cascading_impact()`와 `ServiceDependencyGraph.get_cascading_affected()`에는 depth limit이 **전혀 없다** (visited 기반 무한루프 방지만 존재). EventGraphBuilder 구현 시 별도의 `max_graph_depth` 설정(기본 3, 최대 10)을 두어 OOM을 방지한다.

### 12.2 기존 테스트 패턴 (준수할 것)

`test_blast_radius_cascade.py`의 패턴을 그대로 따른다:

1. **싱글톤 리셋**: `setup_method()`에서 `reset_*()` 호출
2. **인메모리 그래프 직접 구성**: `register_dependency()`로 체인 수동 설정
3. **외부 의존성 없음**: Redis/DB 모킹 불필요 (전부 인메모리)
4. **순환 방지 테스트**: 수동 순환 추가 후 무한루프 방지 확인

### 12.3 필수 테스트 케이스 (depth별)

| 케이스 | depth | 구조 | 검증 대상 |
|--------|-------|------|----------|
| 최소 | 1 | A → B | 단일 홉 엣지 생성 |
| 표준 | 3 | A → B → C → D | 프로덕션 기본값과 동일 |
| 스트레스 | 5 | A → B → C → D → E → F | max_hops 상한 테스트 |
| 순환 | 2+ | A → B → A | 순환 방지 검증 (visited 체크) |
| 팬아웃 | 2 | A → {B, C, D} → {E, F} | 너비 확장 |
| 단절 | 1 | {A → B}, {X → Y} | 독립 컴포넌트 (component_count=2) |

### 12.4 Diamond Dependency 테스트

MSA 환경에서 가장 잦은 위상 정렬 버그를 유발하는 패턴이다. 기존 테스트(`test_blast_radius_cascade.py`)에서 체인과 팬아웃은 있지만 **diamond는 커버되지 않는다**.

```
Diamond 구조:
  A ──→ B ──→ D
  A ──→ C ──→ D

전이적 축소 적용 대상이 아님:
  A→D 직접 엣지가 없으므로 B→D, C→D 모두 보존되어야 한다.
  _transitive_reduction()이 잘못 작동하면 B→D 또는 C→D를 제거할 수 있다.
```

**검증 항목**:
- 엣지 4개 모두 보존: A→B, A→C, B→D, C→D
- `root_nodes = [A]`, `leaf_nodes = [D]`
- `get_ancestors(D)` = [A, B, C]
- `get_critical_path()`가 두 경로 중 하나를 결정적으로 선택

### 12.5 Event Storm 극단값 테스트

동일 타임스탬프 이벤트 200개가 동시 주입되는 시나리오.

**검증 항목**:
- `max_events_per_dag=200` 절삭이 패닉 없이 작동
- 정렬 알고리즘이 결정적 (tiebreaker로 `event_id` 사용)
- O(N²) 엣지 후보 생성에서 OOM 미발생 (200개 → 최대 19,900쌍)
- `time_gap = 0`인 쌍에서 Priority 4(시간 근접성) 미적용 확인

---

## Appendix A. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-20 | 초기 작성 |
| 1.1.0 | 2026-02-20 | §7 Window Trigger Snapshot 전략 + Debounce 추가, §8 Correlation ID 표준화 + Priority 1.5 Contextual Matching 추가, §9 CoOccurrenceTracker 인메모리 O(1) + Copy-on-Write 동기화 추가, §10 DAG 분리 기준 + Component Tagging 추가, §11 동일 타임스탬프 처리 정책 + 결정적 정렬 추가, §12 테스트 전략 보완(Diamond/Storm/depth 기준) 추가, §3.1 4단계→5단계 Evidence 확장, §3.2 정렬 tiebreaker 반영 + deps["upstream"] 접근 수정, §5 직렬화에 component_count/components 추가 |
