# 255. Incident Timeline — 통합 타임라인 자동 생성

> **Version**: 1.0.0
> **Created**: 2026-02-20
> **Status**: Approved
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `services/correlation_engine/incident_timeline.py`
> **Related**: [251_CORRELATION_EVENT_GRAPH.md](251_CORRELATION_EVENT_GRAPH.md), [253_CORRELATION_ROOT_CAUSE_RANKER.md](253_CORRELATION_ROOT_CAUSE_RANKER.md)

---

## 0. 요약

EventDAG(251)와 RootCauseAnalysis(253)를 입력으로, **인시던트의 전체 라이프사이클을 하나의 타임라인 문서**로 자동 생성한다. 기존 `build_timeline()`과 `SnapshotBuilder`를 확장하여, CB 이벤트뿐 아니라 **42개 전체 EventType**을 포함하는 통합 타임라인을 만든다.

---

## 1. 현재 타임라인의 한계

### 1.1 기존 구현

| 모듈 | 수집 범위 | 한계 |
|------|----------|------|
| `build_timeline()` — [store.py](../../packages/selfhealing-python/src/selfhealing/services/postmortem/store.py) L542 | CB 상태 변경 + 로컬 힐링 이벤트 | Emergency, Throttle, Error Budget 등 미포함 |
| `SnapshotBuilder` — [snapshot_builder.py](../../packages/selfhealing-python/src/selfhealing/services/postmortem/snapshot_builder.py) | CB OPEN/CLOSE 시점 메트릭 스냅샷 | 중간 이벤트(Half-Open, 레벨 변경 등) 미캡처 |
| `IncidentGroupManager` — [incident_group.py](../../packages/selfhealing-python/src/selfhealing/services/postmortem/incident_group.py) | 다중 CB 이벤트 그룹핑 | 인과관계 없는 시간 윈도우 그룹핑만 |

### 1.2 목표

**Before** (현재):
```
14:32:01  CB OPEN - payment-service
14:32:15  CB OPEN - order-service
14:35:00  CB CLOSE - payment-service
14:35:30  CB CLOSE - order-service
```

**After** (통합 타임라인):
```
14:31:58  [ROOT CAUSE] error_rate_spike - db-pool-service (확률 87%)
14:32:01  circuit_breaker_opened - payment-service ← db-pool-service에서 전파
14:32:03  throttle_limit_changed - payment-service (min_limit으로 강등)
14:32:05  error_budget_critical - payment-service
14:32:15  circuit_breaker_opened - order-service ← db-pool-service에서 전파
14:32:20  emergency_level_changed - system (Level 2)
14:33:00  circuit_breaker_half_opened - payment-service
14:35:00  circuit_breaker_closed - payment-service
14:35:05  throttle_limit_recovered - payment-service
14:35:30  circuit_breaker_closed - order-service
14:35:35  emergency_deactivated - system (Level 0)
14:35:40  [RESOLVED] 총 duration: 3분 42초  |  영향 서비스: 2개
```

---

## 2. 핵심 자료구조

### 2.1 TimelineEntry

```python
@dataclass(frozen=True)
class TimelineEntry:
    """타임라인의 개별 항목"""
    timestamp: float
    event_type: str
    service_name: str
    description: str              # 사람이 읽을 수 있는 설명
    severity: str                 # "critical" | "warning" | "info" | "recovery"
    is_root_cause: bool           # Root Cause Ranker가 식별한 근본 원인
    is_resolution: bool           # 복구 완료 이벤트
    causal_parent: str | None     # DAG에서 이 이벤트의 원인 이벤트 ID
    metadata: dict                # 추가 컨텍스트 (메트릭 값, 설정 변경 등)

    @property
    def formatted_time(self) -> str:
        """HH:MM:SS 포맷"""
        from datetime import datetime
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")
```

### 2.2 IncidentTimeline

```python
@dataclass
class IncidentTimeline:
    """인시던트 전체 타임라인"""
    incident_id: str
    started_at: float
    resolved_at: float | None
    duration_seconds: float | None
    root_cause_summary: str                # RootCauseAnalysis.summary
    entries: list[TimelineEntry]           # 시간순 정렬
    affected_services: list[str]
    phases: list[TimelinePhase]            # 감지 → 대응 → 복구 단계 구분
    dag_reference: str | None              # EventDAG incident_id (교차 참조)

    def to_dict(self) -> dict: ...
    def to_markdown(self) -> str: ...      # 사람이 읽을 수 있는 마크다운
    def get_mttr(self) -> float | None: ...  # Mean Time To Resolve
    def get_mttd(self) -> float | None: ...  # Mean Time To Detect
```

### 2.3 TimelinePhase

```python
@dataclass(frozen=True)
class TimelinePhase:
    """타임라인의 단계"""
    name: str                    # "detection" | "escalation" | "mitigation" | "recovery" | "post_recovery"
    started_at: float
    ended_at: float | None
    duration_seconds: float | None
    key_events: list[str]        # 이 단계의 주요 이벤트 ID
```

---

## 3. 타임라인 생성 알고리즘

### 3.1 TimelineBuilder

```python
class IncidentTimelineBuilder:
    """DAG + Root Cause → 통합 타임라인 생성"""

    # 이벤트 타입별 severity 매핑
    SEVERITY_MAP: dict[str, str] = {
        "circuit_breaker_opened": "critical",
        "emergency_level_changed": "critical",
        "kill_switch_activated": "critical",
        "error_budget_critical": "warning",
        "throttle_limit_changed": "warning",
        "security_violation_detected": "warning",
        "circuit_breaker_half_opened": "info",
        "circuit_breaker_closed": "recovery",
        "emergency_deactivated": "recovery",
        "error_budget_recovered": "recovery",
        "emergency_recovery_completed": "recovery",
    }

    # 이벤트 타입별 설명 템플릿
    DESCRIPTION_MAP: dict[str, str] = {
        "circuit_breaker_opened": "Circuit Breaker OPEN — {service_name} 트래픽 차단",
        "circuit_breaker_closed": "Circuit Breaker CLOSED — {service_name} 정상 복구",
        "circuit_breaker_half_opened": "Circuit Breaker HALF-OPEN — {service_name} 시범 트래픽",
        "emergency_level_changed": "Emergency Level → {level}",
        "emergency_deactivated": "Emergency 해제 — 정상 모드 복귀",
        "throttle_limit_changed": "Throttle 제한 변경 — {service_name}",
        "error_budget_critical": "Error Budget 임계치 초과 — {service_name}",
        "error_budget_recovered": "Error Budget 복구 — {service_name}",
    }

    def build(
        self,
        dag: EventDAG,
        root_cause_analysis: RootCauseAnalysis,
        snapshot_data: dict | None = None,
    ) -> IncidentTimeline:
        """DAG + 분석 결과 → 통합 타임라인"""
        entries: list[TimelineEntry] = []

        # 1) DAG 노드 → TimelineEntry 변환
        for node in sorted(dag.nodes.values(), key=lambda n: n.timestamp):
            is_root = node in [rc.event_node for rc in root_cause_analysis.candidates[:1]]
            causal_parent = self._find_causal_parent(node, dag)

            entry = TimelineEntry(
                timestamp=node.timestamp,
                event_type=node.event_type,
                service_name=node.service_name,
                description=self._format_description(node),
                severity=self.SEVERITY_MAP.get(node.event_type, "info"),
                is_root_cause=is_root,
                is_resolution=self._is_resolution_event(node),
                causal_parent=causal_parent.event_id if causal_parent else None,
                metadata=self._extract_metadata(node, snapshot_data),
            )
            entries.append(entry)

        # 2) Phase 분류
        phases = self._classify_phases(entries)

        # 3) 메타 정보 산출
        started_at = entries[0].timestamp if entries else 0
        resolved_at = self._find_resolution_time(entries)
        duration = (resolved_at - started_at) if resolved_at else None

        return IncidentTimeline(
            incident_id=dag.incident_id,
            started_at=started_at,
            resolved_at=resolved_at,
            duration_seconds=duration,
            root_cause_summary=root_cause_analysis.summary,
            entries=entries,
            affected_services=list({e.service_name for e in entries}),
            phases=phases,
            dag_reference=dag.incident_id,
        )
```

### 3.2 Phase 분류

```python
    def _classify_phases(self, entries: list[TimelineEntry]) -> list[TimelinePhase]:
        """타임라인 항목들을 인시던트 단계로 분류"""
        phases: list[TimelinePhase] = []

        # Detection: 첫 번째 critical 이벤트까지
        detection_end = next(
            (e for e in entries if e.severity == "critical"), entries[0] if entries else None
        )

        # Escalation: critical 이벤트 연속 구간
        escalation_events = [e for e in entries if e.severity in ("critical", "warning")]

        # Mitigation: 첫 recovery 이벤트부터
        first_recovery = next((e for e in entries if e.severity == "recovery"), None)

        # Recovery: 모든 recovery 이벤트
        recovery_events = [e for e in entries if e.severity == "recovery"]

        if detection_end:
            phases.append(TimelinePhase(
                name="detection",
                started_at=entries[0].timestamp,
                ended_at=detection_end.timestamp,
                duration_seconds=detection_end.timestamp - entries[0].timestamp,
                key_events=[detection_end.event_type],
            ))

        if escalation_events:
            phases.append(TimelinePhase(
                name="escalation",
                started_at=escalation_events[0].timestamp,
                ended_at=escalation_events[-1].timestamp,
                duration_seconds=escalation_events[-1].timestamp - escalation_events[0].timestamp,
                key_events=[e.event_type for e in escalation_events],
            ))

        if first_recovery and recovery_events:
            phases.append(TimelinePhase(
                name="recovery",
                started_at=first_recovery.timestamp,
                ended_at=recovery_events[-1].timestamp,
                duration_seconds=recovery_events[-1].timestamp - first_recovery.timestamp,
                key_events=[e.event_type for e in recovery_events],
            ))

        return phases
```

---

## 4. Postmortem 연동

### 4.1 기존 필드 확장

기존 `generate_postmortem_data()`의 `timeline` 필드와 **하위호환 유지**:

```python
# 기존 (유지)
"timeline": [
    {"timestamp": "...", "event": "CB_OPEN", "service": "payment"},
    ...
]

# 추가 (새 필드)
"correlation_timeline": {
    "incident_id": "inc_2026-02-20_14:32:01_a1b2c3",
    "root_cause": "db-pool-service의 circuit_breaker_opened (87%)",
    "duration_seconds": 222,
    "phases": [
        {"name": "detection", "duration_seconds": 3},
        {"name": "escalation", "duration_seconds": 19},
        {"name": "recovery", "duration_seconds": 200}
    ],
    "entries": [...],        # 전체 TimelineEntry 리스트
    "metrics": {
        "mttd_seconds": 3,   # Mean Time To Detect
        "mttr_seconds": 222,  # Mean Time To Resolve
        "affected_services": 2,
        "total_events": 12
    }
}
```

### 4.2 마크다운 출력

`IncidentTimeline.to_markdown()`으로 사람이 읽을 수 있는 포맷 생성:

```markdown
## Incident Timeline: inc_2026-02-20_14:32:01

**Root Cause**: db-pool-service의 circuit_breaker_opened 이벤트 (확률 87%)
**Duration**: 3분 42초  |  **Affected Services**: 2개
**MTTD**: 3초  |  **MTTR**: 3분 42초

### Detection Phase (3초)
| Time | Service | Event | Severity |
|------|---------|-------|----------|
| 14:31:58 | db-pool-service | **[ROOT CAUSE] error_rate_spike** | 🔴 critical |

### Escalation Phase (19초)
| Time | Service | Event | Severity |
|------|---------|-------|----------|
| 14:32:01 | payment-service | circuit_breaker_opened | 🔴 critical |
| 14:32:03 | payment-service | throttle_limit_changed | 🟡 warning |
| 14:32:15 | order-service | circuit_breaker_opened | 🔴 critical |
| 14:32:20 | system | emergency_level_changed (L2) | 🔴 critical |

### Recovery Phase (3분 20초)
| Time | Service | Event | Severity |
|------|---------|-------|----------|
| 14:35:00 | payment-service | circuit_breaker_closed | 🟢 recovery |
| 14:35:30 | order-service | circuit_breaker_closed | 🟢 recovery |
| 14:35:35 | system | emergency_deactivated | 🟢 recovery |
```

---

## 5. SRE 메트릭 자동 산출

```python
    def get_mttd(self) -> float | None:
        """Mean Time To Detect — 첫 이벤트부터 첫 critical 알림까지"""
        first = self.entries[0] if self.entries else None
        first_critical = next((e for e in self.entries if e.severity == "critical"), None)
        if first and first_critical:
            return first_critical.timestamp - first.timestamp
        return None

    def get_mttr(self) -> float | None:
        """Mean Time To Resolve — 첫 이벤트부터 마지막 recovery까지"""
        if self.started_at and self.resolved_at:
            return self.resolved_at - self.started_at
        return None

    def get_escalation_rate(self) -> float:
        """에스컬레이션 속도 — critical 이벤트 수 / 총 이벤트 수"""
        if not self.entries:
            return 0.0
        critical_count = sum(1 for e in self.entries if e.severity == "critical")
        return critical_count / len(self.entries)
```

이 메트릭들은 `DailyReportService`와 `DashboardService`에서도 소비 가능.

---

## 6. 테스트 전략

| 테스트 유형 | 시나리오 | 검증 |
|------------|---------|------|
| **단위** | 단일 이벤트 → 1항목 타임라인 | entries 1개, phases 1개 |
| **단위** | OPEN → CLOSE 체인 | started_at/resolved_at 정확, duration 계산 |
| **단위** | SEVERITY_MAP 미등록 이벤트 | severity = "info" (기본값) |
| **통합** | DAG 5노드 → 타임라인 | 시간순 정렬, root_cause 마킹 |
| **통합** | to_markdown() 출력 | 유효한 마크다운 테이블 |
| **시나리오** | "DB Pool → CB × 3 → Emergency → Recovery" | 4 phases 구분, MTTD/MTTR 산출 |
