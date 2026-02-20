# 257. 기존 시스템 연동 및 서비스 오케스트레이터

> **Version**: 1.0.0
> **Created**: 2026-02-20
> **Status**: Approved
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `services/correlation_engine/service.py`, `settings/correlation_engine.py`

---

## 0. 요약

Correlation Engine의 오케스트레이터(`service.py`)와 설정(`settings/correlation_engine.py`)을 설계하고, 기존 4개 서비스(EventBus, Postmortem, BlastRadius, Learning)와의 구체적 연동 방식을 정의한다.

---

## 1. Settings — `settings/correlation_engine.py`

### 1.1 기존 Settings 패턴 준수

[predictive_forecaster.py](../../packages/selfhealing-python/src/selfhealing/settings/predictive_forecaster.py) 패턴과 동일:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CorrelationEngineSettings(BaseSettings):
    """Metric Correlation Engine 설정

    환경변수 prefix: SELFHEALING_CORRELATION_
    예: SELFHEALING_CORRELATION_ENABLED=true
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CORRELATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ── 전체 ON/OFF ──
    enabled: bool = Field(
        default=True,
        description="Correlation Engine 활성화 여부",
    )

    # ── 시간 윈도우 ──
    window_seconds: float = Field(
        default=300.0,
        ge=30.0,
        le=3600.0,
        description="동시발생 판단 시간 윈도우 (초)",
    )
    analysis_interval_seconds: float = Field(
        default=60.0,
        ge=10.0,
        le=600.0,
        description="주기적 분석 틱 간격 (초)",
    )

    # ── Co-occurrence Tracker ──
    zscore_threshold: float = Field(
        default=2.5,
        ge=1.0,
        le=5.0,
        description="동시발생 빈도 이상 판단 ZScore 임계값",
    )
    min_co_occurrences: int = Field(
        default=3,
        ge=1,
        le=100,
        description="최소 동시발생 횟수 (이하 무시)",
    )
    max_tracked_pairs: int = Field(
        default=1000,
        ge=10,
        le=10000,
        description="추적 가능한 최대 이벤트 쌍 수",
    )
    count_history_size: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="쌍별 카운트 히스토리 크기",
    )

    # ── Event Graph ──
    min_confidence: float = Field(
        default=0.4,
        ge=0.1,
        le=0.9,
        description="DAG 엣지 최소 신뢰도",
    )
    max_events_per_dag: int = Field(
        default=200,
        ge=10,
        le=1000,
        description="DAG당 최대 이벤트 수",
    )

    # ── Wildcard Observer ──
    max_event_buffer: int = Field(
        default=10000,
        ge=100,
        le=100000,
        description="이벤트 타입별 최대 타임스탬프 버퍼",
    )
    event_rate_zscore_threshold: float = Field(
        default=3.0,
        ge=1.5,
        le=5.0,
        description="이벤트 발생률 이상 탐지 ZScore 임계값",
    )

    # ── Root Cause Ranker 가중치 ──
    weight_topology: float = Field(default=0.35, ge=0.0, le=1.0)
    weight_temporal: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_blast_radius: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_historical: float = Field(default=0.15, ge=0.0, le=1.0)

    # ── 연동 ──
    learning_integration_enabled: bool = Field(
        default=True,
        description="LearningService 패턴 축적 연동 활성화",
    )
    postmortem_integration_enabled: bool = Field(
        default=True,
        description="Postmortem 자동 타임라인 주입 활성화",
    )
    state_persistence_enabled: bool = Field(
        default=True,
        description="StateBackend 영속화 활성화 (Cold Start 방지)",
    )


# ── Singleton ──
_settings: CorrelationEngineSettings | None = None


def get_correlation_engine_settings() -> CorrelationEngineSettings:
    global _settings
    if _settings is None:
        _settings = CorrelationEngineSettings()
    return _settings


def reset_correlation_engine_settings() -> None:
    global _settings
    _settings = None
```

---

## 2. Service Orchestrator — `services/correlation_engine/service.py`

### 2.1 전체 구조

```python
import logging
import threading
import time
from typing import Protocol

from selfhealing.services.correlation_engine.wildcard_observer import WildcardObserver
from selfhealing.services.correlation_engine.co_occurrence_tracker import CoOccurrenceTracker
from selfhealing.services.correlation_engine.event_graph import EventGraphBuilder
from selfhealing.services.correlation_engine.root_cause_ranker import RootCauseRanker
from selfhealing.services.correlation_engine.incident_timeline import IncidentTimelineBuilder
from selfhealing.services.correlation_engine.interfaces import (
    CorrelationStrategy,
    RootCauseStrategy,
    GraphBuildStrategy,
)
from selfhealing.settings.correlation_engine import get_correlation_engine_settings

logger = logging.getLogger(__name__)


class CorrelationEngineService:
    """Metric Correlation Engine 오케스트레이터

    책임:
    1. 서브 모듈 초기화 및 수명주기 관리
    2. 주기적 분석 틱 실행
    3. 인시던트 발생 시 온디맨드 분석
    4. 전략(Strategy) 교체 관리
    5. 기존 서비스(EventBus, Postmortem, Learning, BlastRadius) 연동

    설계 원칙:
    - 이 서비스가 제거되어도 나머지 시스템 정상 동작
    - 이 서비스 내부 오류가 복원력 기능에 영향 없음
    - 전략 교체 시 코어 코드 수정 불필요
    """

    _instance: "CorrelationEngineService | None" = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "CorrelationEngineService":
        """Singleton 인스턴스"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """인스턴스 리셋 (테스트용)"""
        with cls._lock:
            if cls._instance:
                cls._instance.shutdown()
            cls._instance = None

    def __init__(self):
        self._settings = get_correlation_engine_settings()
        self._initialized = False
        self._running = False

        # 서브 모듈 (초기화 전 None)
        self._observer: WildcardObserver | None = None
        self._co_occurrence: CoOccurrenceTracker | None = None
        self._graph_builder: EventGraphBuilder | None = None
        self._root_cause_ranker: RootCauseRanker | None = None
        self._timeline_builder: IncidentTimelineBuilder | None = None

        # 분석 스레드
        self._analysis_thread: threading.Thread | None = None

    def initialize(self) -> bool:
        """엔진 초기화 — EventBus 구독 등록 포함"""
        if not self._settings.enabled:
            logger.info("[CorrelationEngine] Disabled by settings")
            return False

        if self._initialized:
            return True

        try:
            # 1. Sub-module 초기화
            self._co_occurrence = CoOccurrenceTracker(self._settings)
            self._graph_builder = EventGraphBuilder(
                blast_radius_service=self._get_blast_radius_service(),
                co_occurrence_tracker=self._co_occurrence,
                settings=self._settings,
            )
            self._root_cause_ranker = RootCauseRanker(self._settings)
            self._timeline_builder = IncidentTimelineBuilder()

            # 2. 상태 복원 (Cold Start 방지)
            if self._settings.state_persistence_enabled:
                self._co_occurrence.load_state()

            # 3. Wildcard Observer 등록 (마지막)
            self._observer = WildcardObserver(self._settings, self._co_occurrence)
            self._observer.register(self._get_event_bus())

            self._initialized = True
            logger.info("[CorrelationEngine] Initialized successfully")
            return True

        except Exception as e:
            logger.error(f"[CorrelationEngine] Initialization failed: {e}")
            return False

    def shutdown(self) -> None:
        """엔진 종료"""
        self._running = False

        if self._observer:
            self._observer.unregister(self._get_event_bus())

        if self._settings.state_persistence_enabled and self._co_occurrence:
            self._co_occurrence.save_state()

        self._initialized = False
        logger.info("[CorrelationEngine] Shut down")
```

### 2.2 주기적 분석 틱

```python
    def start_analysis_loop(self) -> None:
        """주기적 분석 루프 시작 (백그라운드 스레드)"""
        if self._running:
            return
        self._running = True
        self._analysis_thread = threading.Thread(
            target=self._analysis_loop,
            name="CorrelationEngine-AnalysisLoop",
            daemon=True,
        )
        self._analysis_thread.start()

    def _analysis_loop(self) -> None:
        """분석 루프 — analysis_interval_seconds마다 실행"""
        while self._running:
            try:
                self._run_periodic_analysis()
            except Exception as e:
                logger.error(f"[CorrelationEngine] Analysis tick error: {e}")
            time.sleep(self._settings.analysis_interval_seconds)

    def _run_periodic_analysis(self) -> None:
        """주기적 분석 실행"""
        if not self._co_occurrence:
            return

        # 1. 이벤트 발생률 이상 체크
        rate_anomaly = self._observer.check_event_rate_anomaly() if self._observer else None
        if rate_anomaly:
            logger.warning(f"[CorrelationEngine] {rate_anomaly['message']}")

        # 2. Co-occurrence 분석
        correlation_results = self._co_occurrence.analyze_tick()

        # 3. 새로운 상관관계 발견 시 → Learning에 축적
        if correlation_results and self._settings.learning_integration_enabled:
            self._report_to_learning(correlation_results)

        # 4. 상태 영속화 (5틱마다)
        if (self._settings.state_persistence_enabled and
            hasattr(self, '_tick_count')):
            self._tick_count = getattr(self, '_tick_count', 0) + 1
            if self._tick_count % 5 == 0:
                self._co_occurrence.save_state()
```

### 2.3 온디맨드 분석 (인시던트 발생 시)

```python
    def analyze_incident(self, window_seconds: float | None = None) -> dict | None:
        """현재 시간 윈도우의 인시던트 분석 (온디맨드)

        Returns:
            {
                "dag": EventDAG,
                "root_cause": RootCauseAnalysis,
                "timeline": IncidentTimeline,
                "correlations": list[CorrelationResult],
            }
            또는 이벤트 부족 시 None
        """
        if not self._initialized:
            return None

        window = window_seconds or self._settings.window_seconds

        try:
            # 1. 현재 윈도우 이벤트 수집
            events = self._observer.get_current_window() if self._observer else []
            if len(events) < 2:
                return None

            # 2. DAG 구축
            dag = self._graph_builder.build_dag(events, window)

            # 3. Root Cause 분석
            co_data = self._co_occurrence.analyze_tick() if self._co_occurrence else []
            root_cause = self._root_cause_ranker.rank(dag, co_data)

            # 4. 타임라인 생성
            timeline = self._timeline_builder.build(dag, root_cause)

            # 5. Postmortem 연동
            if self._settings.postmortem_integration_enabled:
                self._inject_to_postmortem(timeline, root_cause)

            return {
                "dag": dag,
                "root_cause": root_cause,
                "timeline": timeline,
                "correlations": co_data,
            }

        except Exception as e:
            logger.error(f"[CorrelationEngine] Incident analysis failed: {e}")
            return None
```

---

## 3. 기존 시스템 연동 상세

### 3.1 EventBus 연동

| 연동 지점 | 방식 | 코드 위치 |
|-----------|------|----------|
| **입력**: 모든 이벤트 수신 | `WildcardObserver.register(bus)` | `initialize()` |
| **출력**: 분석 결과 이벤트 발행 (선택) | `bus.emit(EventType.CORRELATION_DETECTED, ...)` | `_run_periodic_analysis()` |

EventBus 핸들러 등록:

```python
    def _register_event_handlers(self) -> None:
        """분석 트리거 이벤트 등록"""
        bus = self._get_event_bus()

        # CB CLOSED 시 자동 인시던트 분석 트리거
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            self._on_incident_resolved,
            priority=EventPriority.LOW,
        )

        # Emergency Recovery 완료 시 분석 트리거
        bus.subscribe(
            EventType.EMERGENCY_RECOVERY_COMPLETED,
            self._on_incident_resolved,
            priority=EventPriority.LOW,
        )

    def _on_incident_resolved(self, event) -> None:
        """인시던트 해소 시 자동 분석"""
        try:
            result = self.analyze_incident()
            if result:
                logger.info(
                    f"[CorrelationEngine] Auto-analysis complete: "
                    f"root_cause={result['root_cause'].primary_cause.event_node.service_name} "
                    f"({result['root_cause'].primary_cause.score:.0%})"
                )
        except Exception as e:
            logger.debug(f"[CorrelationEngine] Auto-analysis skipped: {e}")
```

### 3.2 Postmortem 연동

```python
    def _inject_to_postmortem(self, timeline: IncidentTimeline, root_cause: RootCauseAnalysis) -> None:
        """Postmortem 데이터에 상관관계 분석 결과 주입"""
        try:
            from selfhealing.services.postmortem.store import PostmortemStore

            store = PostmortemStore.get_instance()

            # 최근 Postmortem에 분석 결과 추가
            latest = store.get_latest_incident()
            if latest and latest.get("incident_id"):
                store.update_incident_fields(
                    incident_id=latest["incident_id"],
                    fields={
                        "correlation_timeline": timeline.to_dict(),
                        "root_cause_analysis": {
                            "primary_cause": {
                                "event": root_cause.primary_cause.event_node.event_type,
                                "service": root_cause.primary_cause.event_node.service_name,
                                "score": root_cause.primary_cause.score,
                                "evidence": root_cause.primary_cause.evidence,
                            },
                            "alternative_causes": [
                                {
                                    "event": c.event_node.event_type,
                                    "service": c.event_node.service_name,
                                    "score": c.score,
                                }
                                for c in root_cause.candidates[1:3]
                            ],
                            "dag_summary": {
                                "nodes": len(root_cause.dag.nodes),
                                "edges": len(root_cause.dag.edges),
                            },
                            "analysis_confidence": root_cause.confidence,
                        },
                    },
                )
        except Exception as e:
            logger.debug(f"[CorrelationEngine] Postmortem injection skipped: {e}")
```

### 3.3 Learning 연동

```python
    def _report_to_learning(self, results: list) -> None:
        """발견된 상관관계를 LearningService에 축적"""
        try:
            from selfhealing.services.learning import LearningService, PatternType

            learning = LearningService.get_instance()
            for result in results:
                learning.learn_pattern(
                    pattern_type=PatternType.ANOMALY,
                    name=f"CoOccurrence:{result.pair.key}",
                    features={
                        "event_type_a": result.pair.event_type_a,
                        "event_type_b": result.pair.event_type_b,
                        "correlation_score": result.correlation_score,
                        "direction": result.direction,
                        "sample_count": result.sample_count,
                    },
                    confidence=result.confidence,
                )
        except Exception as e:
            logger.debug(f"[CorrelationEngine] Learning report skipped: {e}")
```

### 3.4 BlastRadius 연동

```python
    @staticmethod
    def _get_blast_radius_service():
        """BlastRadiusService 인스턴스 조회"""
        try:
            from selfhealing.services.blast_radius import BlastRadiusService
            return BlastRadiusService.get_instance()
        except Exception:
            return None  # BlastRadius 없어도 DAG 구축 가능 (시간순만 사용)
```

---

## 4. 연동 의존성 방향

```
                  ┌──────────────────┐
                  │  EventBus        │
                  │  (이벤트 소스)    │
                  └────────┬─────────┘
                           │ subscribe (소비자)
                           ▼
              ┌────────────────────────┐
              │  CorrelationEngine     │
              │  (분석 + 오케스트레이션) │
              └─┬──────┬──────┬───────┘
                │      │      │
       consume  │      │      │ consume
                ▼      ▼      ▼
    ┌─────────┐ ┌──────┐ ┌─────────┐
    │BlastRad.│ │Learn.│ │Postmort.│
    │(의존성  │ │(패턴 │ │(결과   │
    │ 그래프) │ │축적) │ │ 주입)  │
    └─────────┘ └──────┘ └─────────┘
```

**의존 방향**: Correlation Engine → 기존 서비스 (단방향)
- 기존 서비스는 Correlation Engine을 **모름** (Correlation Engine이 제거되어도 영향 없음)
- Correlation Engine은 기존 서비스를 **소비** (import + try/except로 graceful fallback)

---

## 5. 전략 교체 API

```python
    # ── Strategy Setters ── (256번 ML Strategy Interface 참조)

    def set_correlation_strategy(self, strategy: CorrelationStrategy) -> None:
        """상관관계 분석 전략 교체 (런타임)"""
        self._co_occurrence = strategy  # type: ignore
        if self._graph_builder:
            self._graph_builder._co_occurrence = strategy

    def set_root_cause_strategy(self, strategy: RootCauseStrategy) -> None:
        """근본 원인 분석 전략 교체 (런타임)"""
        self._root_cause_ranker = strategy  # type: ignore

    def set_graph_strategy(self, strategy: GraphBuildStrategy) -> None:
        """DAG 구축 전략 교체 (런타임)"""
        self._graph_builder = strategy  # type: ignore
```

---

## 6. 대시보드/API 인터페이스

```python
    def get_status(self) -> dict:
        """엔진 상태 조회 (대시보드/API용)"""
        return {
            "enabled": self._settings.enabled,
            "initialized": self._initialized,
            "running": self._running,
            "observer_stats": self._observer.get_statistics() if self._observer else None,
            "tracked_pairs": len(self._co_occurrence._pair_detectors) if self._co_occurrence else 0,
            "settings": {
                "window_seconds": self._settings.window_seconds,
                "zscore_threshold": self._settings.zscore_threshold,
                "analysis_interval": self._settings.analysis_interval_seconds,
            },
        }

    def get_top_correlations(self, limit: int = 10) -> list[dict]:
        """현재 상위 상관관계 (API용)"""
        if not self._co_occurrence:
            return []
        results = self._co_occurrence.analyze_tick()
        sorted_results = sorted(results, key=lambda r: r.correlation_score, reverse=True)
        return [
            {
                "pair": r.pair.key,
                "score": r.correlation_score,
                "direction": r.direction,
                "evidence": r.evidence,
            }
            for r in sorted_results[:limit]
        ]
```

---

## 7. 초기화 시점 (Django Integration)

```python
# Django AppConfig에서 초기화
class SelfHealingConfig(AppConfig):
    def ready(self):
        # 기존 초기화 코드 ...

        # Correlation Engine 초기화 (마지막)
        from selfhealing.services.correlation_engine.service import CorrelationEngineService
        engine = CorrelationEngineService.get_instance()
        if engine.initialize():
            engine.start_analysis_loop()
```

비-Django 환경:
```python
# 직접 초기화
engine = CorrelationEngineService.get_instance()
engine.initialize()
engine.start_analysis_loop()
```

---

## 8. 테스트 전략

| 테스트 유형 | 시나리오 | 검증 |
|------------|---------|------|
| **단위** | `enabled=False` | initialize() → False, 구독 안 됨 |
| **단위** | initialize() → shutdown() 순환 | 리소스 정리 확인 |
| **단위** | analyze_incident() 이벤트 부족 | None 반환 |
| **통합** | 5개 이벤트 발행 → analyze_incident() | DAG + Root Cause + Timeline |
| **통합** | CB_CLOSED 이벤트 → _on_incident_resolved 자동 트리거 | 자동 분석 실행 |
| **통합** | Learning mock → _report_to_learning() | learn_pattern 호출됨 |
| **통합** | Postmortem mock → _inject_to_postmortem() | update_incident_fields 호출됨 |
| **통합** | BlastRadius 없는 환경 | graceful fallback, DAG 시간순만 사용 |
| **전략 교체** | set_correlation_strategy(mock) | mock 전략으로 분석 실행 |
| **성능** | 분석 루프 60초마다 실행 | CPU 사용률 ≤ 1% |
