# 255. Incident Timeline — 통합 타임라인 자동 생성

> **Version**: 2.0.0
> **Created**: 2026-02-20
> **Updated**: 2026-02-21
> **Status**: Approved
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `services/correlation_engine/incident_timeline.py`
> **Related**: [251_CORRELATION_EVENT_GRAPH.md](251_CORRELATION_EVENT_GRAPH.md), [253_CORRELATION_ROOT_CAUSE_RANKER.md](253_CORRELATION_ROOT_CAUSE_RANKER.md)

---

## 0. 요약

EventDAG(251)와 RootCauseAnalysis(253)를 입력으로, **인시던트의 전체 라이프사이클을 하나의 타임라인 문서**로 자동 생성한다. 기존 `build_timeline()`과 `SnapshotBuilder`를 확장하여, CB 이벤트뿐 아니라 **45개 전체 EventType**을 포함하는 통합 타임라인을 만든다.

### 0.1 v2.0 변경사항 (리뷰 반영)

| 리뷰 | 내용 | 구현 위치 |
|------|------|----------|
| **R1** | Phase 분류 — 전역 필터링 → 상태 머신(State Machine) 단방향 전이 | `_classify_phases_state_machine()`, `_PhaseState` |
| **R2** | TimelineStatus 도입 — Ongoing/Resolved/Flapping/Confirmed 명시 | `TimelineStatus`, `_determine_status()` |
| **R3** | TTAR(Time to Automated Response) 메트릭 신설 | `get_ttar()`, `MITIGATION_EVENT_TYPES`, `_calc_speedup_factor()` |
| **R4** | Payload Trimming — JSON 폭탄 방어 | `_sanitize_value()`, `MAX_METADATA_VALUE_BYTES=1024` |
| **R5** | 미등록 EventType Fallback — _humanize_event_type() + severity 추론 | `_humanize_event_type()`, `_resolve_severity()`, `CATEGORY_SEVERITY_DEFAULTS` |

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
    description: str              # 사람이 읽을 수 있는 설명 (최대 500자)
    severity: str                 # "critical" | "warning" | "info" | "recovery"
    is_root_cause: bool           # Root Cause Ranker가 식별한 근본 원인
    is_resolution: bool           # 복구 완료 이벤트
    causal_parent: str | None     # DAG에서 이 이벤트의 원인 이벤트 ID
    phase_name: str = ""          # R1: 이 이벤트가 속한 Phase 이름 (상태 머신 할당)
    is_re_escalation: bool = False  # R1: Recovery 중 재발한 critical/warning 이벤트
    metadata: dict                # 추가 컨텍스트 (R4: _sanitize_value로 직렬화 시 보호)

    @property
    def formatted_time(self) -> str:
        """HH:MM:SS UTC 포맷"""
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime("%H:%M:%S")
```

### 2.2 TimelineStatus (R2 신규)

```python
class TimelineStatus(str, enum.Enum):
    """인시던트 타임라인 상태.

    IncidentGroupStatus(OPEN→CLOSED→COMPLETED) 패턴과 대응:
      ONGOING    ← OPEN       (recovery 없음 — 장애 진행 중)
      RESOLVED   ← CLOSED     (마지막 recovery 후 critical 재발 없음)
      FLAPPING   ← (신규)     (recovery 후 critical 재발 — 플래핑)
      CONFIRMED  ← COMPLETED  (resolved + 안정화 윈도우 경과)
    """
    ONGOING = "ongoing"
    RESOLVED = "resolved"
    FLAPPING = "flapping"
    CONFIRMED = "confirmed"
```

### 2.3 IncidentTimeline

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
    status: TimelineStatus = TimelineStatus.ONGOING  # R2: 인시던트 상태

    def to_dict(self) -> dict: ...
    def to_markdown(self) -> str: ...      # 사람이 읽을 수 있는 마크다운
    def get_mttr(self) -> float | None: ...  # Mean Time To Resolve
    def get_mttd(self) -> float | None: ...  # Mean Time To Detect
    def get_ttar(self) -> float | None: ...  # R3: Time to Automated Response
```

### 2.4 TimelinePhase

```python
@dataclass(frozen=True)
class TimelinePhase:
    """타임라인의 단계 — R1: 상태 머신에 의해 생성"""
    name: str                    # "detection" | "escalation" | "mitigation" | "recovery" | "post_recovery"
    started_at: float
    ended_at: float | None
    duration_seconds: float | None
    key_events: list[str]        # 이 단계의 주요 이벤트 타입 목록
    re_escalation_count: int = 0 # R1: 이 단계에서 발생한 재에스컬레이션 이벤트 수
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

### 3.2 Phase 분류 — R1: 상태 머신 (State Machine)

> v1.0에서는 전역 필터링(`[e for e in entries if e.severity == "critical"]`)으로
> Phase를 분류했으나, **이벤트 순서를 무시하고 겹칠 위험**이 있었다.
> v2.0에서는 **단방향 상태 머신**으로 변경하여 Phase 역전을 원천 차단한다.

```python
class _PhaseState(str, enum.Enum):
    """Phase 상태 머신 내부 상태"""
    DETECTION = "detection"
    ESCALATION = "escalation"
    MITIGATION = "mitigation"
    RECOVERY = "recovery"
    POST_RECOVERY = "post_recovery"
```

**전이 규칙 (단방향 — 역전 불가):**

```
DETECTION ──(첫 critical/warning)──→ ESCALATION
                                     │
                                     ├──(첫 recovery 또는 half_open)──→ MITIGATION
                                     │                                   │
                                     │                 (연속 recovery)──→ RECOVERY
                                     │                                   │
                                     │         (Recovery 중 critical)──→ is_re_escalation=True ⚠️
                                     │                                   (Phase 역전 없음)
```

**설계 근거:**
- `RecoveryDampeningManager`(PHASE_1→PHASE_2→COMPLETE) — 단방향 전이
- `IncidentGroupStatus`(OPEN→CLOSED→COMPLETED) — 단방향 전이
- `AntiFlappingGuard`(coordination/anti_flapping.py) — `min_stable_duration_before_recovery_seconds=600`로 역전 방지

**Recovery 중 재발 처리 (is_re_escalation):**

Recovery/Mitigation Phase 중 critical/warning 이벤트가 재발하면:
1. Phase를 역전하지 않음 (단방향 원칙)
2. 해당 이벤트에 `is_re_escalation=True` 태그 부착
3. Phase의 `re_escalation_count` 증가
4. 마크다운 출력 시 `⚠️ Flapping 감지` 경고 표시

### 3.3 R2: 인시던트 상태 결정 (_determine_status)

```python
@staticmethod
def _determine_status(entries, resolved_at) -> TimelineStatus:
    """
    - recovery 없음 → ONGOING
    - 마지막 recovery 후 critical 재발 → FLAPPING
    - 마지막 recovery 후 critical 없음 → RESOLVED
    - (CONFIRMED는 외부에서 안정화 윈도우 경과 후 설정)
    """
```

### 3.4 R3: TTAR (Time to Automated Response)

```python
MITIGATION_EVENT_TYPES: frozenset[str] = frozenset({
    "throttle_limit_changed",           # EventGraphTrigger 매핑
    "emergency_level_changed",
    "emergency_activated",
    "kill_switch_activated",
    "load_shedding_level_changed",
    "rate_limit_cooldown_start",
})

HUMAN_AVG_RESPONSE_SECONDS = 900.0   # 인간 SRE 평균 15분

def get_ttar(self) -> float | None:
    """첫 critical ~ 첫 자동 개입 이벤트까지의 시간."""

def _calc_speedup_factor(ttar) -> float | None:
    """HUMAN_AVG / TTAR — 자동화가 인간보다 몇 배 빠른지."""
```

### 3.5 R4: Payload Trimming (_sanitize_value)

```python
MAX_METADATA_VALUE_BYTES = 1024

def _sanitize_value(value, _depth=0) -> Any:
    """재귀적 dict/list 처리, 문자열 1024B 절삭.
    - 메모리 내에서는 원본 유지 (디버깅/분석용)
    - to_dict() 직렬화 시점에만 적용 (PagerDuty description[:500] 패턴)
    - 재귀 depth > 5 시 '[Max depth exceeded]' 반환
    """
```

### 3.6 R5: 미등록 EventType Fallback

```python
CATEGORY_SEVERITY_DEFAULTS: dict[str, str] = {
    "emergency_": "critical",      # prefix 매칭
    "kill_switch_": "critical",
    "_recovered": "recovery",      # suffix 매칭
    "_failed": "warning",
    ...
}

def _humanize_event_type(event_type: str) -> str:
    """snake_case → 'Title Case' (예: 'saga_timed_out' → 'Saga Timed Out')"""

def _resolve_severity(event_type, severity_map) -> str:
    """4단계 fallback: 정확 매칭 → prefix → suffix → DEFAULT_SEVERITY('info')
    + logger.info 추적 (tier_mapping.py의 logger.warning fallback 패턴)"""
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
    "status": "resolved",                              // R2: TimelineStatus
    "root_cause": "db-pool-service의 circuit_breaker_opened (87%)",
    "duration_seconds": 222,
    "phases": [
        {"name": "detection", "duration_seconds": 3, "re_escalation_count": 0},
        {"name": "escalation", "duration_seconds": 19, "re_escalation_count": 0},
        {"name": "recovery", "duration_seconds": 200, "re_escalation_count": 0}
    ],
    "entries": [...],        # 전체 TimelineEntry 리스트 (phase_name, is_re_escalation 포함)
    "metrics": {
        "mttd_seconds": 3,   # Mean Time To Detect
        "mttr_seconds": 222,  # Mean Time To Resolve
        "ttar_seconds": 5,    # R3: Time to Automated Response
        "ttar_speedup_factor": 180,  # R3: 인간 대비 180배 빠른 자동 대응
        "human_baseline_seconds": 900,  # R3: 인간 SRE 평균 15분
        "affected_services": 2,
        "total_events": 12,
        "escalation_rate": 0.33,
        "re_escalation_count": 0  # R1: 재에스컬레이션 총 건수
    }
}
```

### 4.2 마크다운 출력

`IncidentTimeline.to_markdown()`으로 사람이 읽을 수 있는 포맷 생성:

```markdown
## Incident Timeline: inc_2026-02-20_14:32:01

**Status**: 🟢 Resolved (복구됨)
**Root Cause**: db-pool-service의 circuit_breaker_opened 이벤트 (확률 87%)
**Duration**: 3분 42초  |  **Affected Services**: 2개
**MTTD**: 3초  |  **MTTR**: 3분 42초
**TTAR**: 5초  |  ⚡ 인간 평균 대비 **180배** 빠른 자동 대응

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

    def get_ttar(self) -> float | None:
        """R3: Time to Automated Response — 첫 critical ~ 첫 자동 개입.

        MITIGATION_EVENT_TYPES (EventGraphTrigger의 6개 트리거 이벤트)를 사용.
        TTAR이 짧을수록 자동화가 빠르게 개입한 것."""
        first_critical = next((e for e in self.entries if e.severity == "critical"), None)
        first_mitigation = next((e for e in self.entries if e.event_type in MITIGATION_EVENT_TYPES), None)
        if first_critical and first_mitigation:
            return max(0.0, first_mitigation.timestamp - first_critical.timestamp)
        return None

    def get_escalation_rate(self) -> float:
        """에스컬레이션 속도 — critical 이벤트 수 / 총 이벤트 수"""
        if not self.entries:
            return 0.0
        critical_count = sum(1 for e in self.entries if e.severity == "critical")
        return critical_count / len(self.entries)
```

### 5.1 TTAR Speedup Factor (R3 신규)

```python
HUMAN_AVG_RESPONSE_SECONDS = 900.0   # 인간 SRE 평균 대응 시간 15분

def _calc_speedup_factor(ttar: float | None) -> float | None:
    """HUMAN_AVG / TTAR — 자동화가 인간보다 몇 배 빠른지.

    예: TTAR=5초 → speedup=180배 (15분 / 5초)
    to_markdown()에서 '⚡ 인간 평균 대비 180배 빠른 자동 대응'으로 렌더링.
    """
```

이 메트릭들은 `DailyReportService`와 `DashboardService`에서도 소비 가능.

---

## 6. 테스트 전략

| 테스트 유형 | 시나리오 | 검증 |
|------------|---------|------|
| **단위** | 단일 이벤트 → 1항목 타임라인 | entries 1개, phases 1개 |
| **단위** | OPEN → CLOSE 체인 | started_at/resolved_at 정확, duration 계산 |
| **단위** | SEVERITY_MAP 미등록 이벤트 | R5: _resolve_severity() 4단계 fallback |
| **단위** | R1: Phase 상태 머신 전이 | DETECTION→ESCALATION→MITIGATION→RECOVERY 단방향 |
| **단위** | R1: Recovery 중 재발 | is_re_escalation=True, Phase 역전 없음, re_escalation_count 집계 |
| **단위** | R2: TimelineStatus 결정 | ONGOING/RESOLVED/FLAPPING 상태 정확 |
| **단위** | R3: TTAR 산출 | 첫 critical ~ 첫 mitigation 시간 차 |
| **단위** | R3: Speedup factor | HUMAN_AVG / TTAR |
| **단위** | R4: _sanitize_value() | 1024B 초과 절삭, 재귀 depth 제한 |
| **단위** | R5: _humanize_event_type() | snake_case → Title Case |
| **단위** | R5: _resolve_severity() prefix/suffix | 카테고리 기반 추론 |
| **통합** | DAG 5노드 → 타임라인 | 시간순 정렬, root_cause 마킹 |
| **통합** | to_markdown() 출력 | Status 배지, TTAR 하이라이트, Flapping 경고 |
| **통합** | R1+R2 Flapping 시나리오 | 재에스컬레이션 + resolved_at=None + ONGOING 상태 |
| **시나리오** | "DB Pool → CB × 3 → Emergency → Recovery" | 3 phases, MTTD/MTTR/TTAR 산출 |
