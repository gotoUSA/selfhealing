# 257. 기존 시스템 연동 및 서비스 오케스트레이터

> **Version**: 2.0.0
> **Created**: 2026-02-20
> **Updated**: 2026-02-21
> **Status**: Implemented
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `services/correlation_engine/service.py`, `settings/correlation_engine.py`

---

## 0. 요약

Correlation Engine의 오케스트레이터(`service.py`)와 설정(`settings/correlation_engine.py`)을 설계하고, 기존 4개 서비스(EventBus, Postmortem, BlastRadius, Learning)와의 구체적 연동 방식을 정의한다.

**v2.0.0 추가사항** — 리뷰를 통해 다음 5가지 엔터프라이즈급 보완 설계를 반영한다:
- §9. LeaderScheduler 기반 분산 분석 루프 (다중 프로세스 중복 실행 방지)
- §10. Deterministic Incident ID + Postmortem 정밀 타겟팅
- §11. PolicyComposer 기반 Fallback 파이프라인 + Observability
- §12. IdempotencyService 기반 분석 멱등성 보장
- §13. RuntimeConfigManager 연동 동적 설정 리로드 + State Flush

---

## 1. Settings — `settings/correlation_engine.py`

### 1.1 2계층 Settings 아키텍처

실제 구현은 **2개의 Settings 클래스**로 분리한다:

| 클래스 | 파일 | 역할 |
|--------|------|------|
| `CorrelationSettings` | `settings/correlation.py` | 서브 모듈 설정 (Co-occurrence, DAG, Observer) — 252번 문서에서 정의 |
| `CorrelationEngineSettings` | `settings/correlation_engine.py` | 오케스트레이터 설정 (ON/OFF, 분석 주기, 가중치, 연동 플래그) |

`CorrelationSettings`에 이미 정의된 서브 모듈 필드(`window_seconds`, `zscore_threshold`, `min_co_occurrences`, `max_tracked_pairs`, `count_history_size`, `min_confidence`, `max_events_per_dag`, `max_event_buffer`)를 중복 정의하지 않고, `CorrelationEngineSettings`는 오케스트레이터 수준 설정만 관리한다.

**Wildcard Observer의 이벤트 발생률 이상 탐지**: 별도 `event_rate_zscore_threshold` 필드 없이 `CorrelationSettings.zscore_threshold`를 재사용한다.

#### CorrelationEngineSettings — `settings/correlation_engine.py`

[predictive_forecaster.py](../../packages/selfhealing-python/src/selfhealing/settings/predictive_forecaster.py) 패턴과 동일:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CorrelationEngineSettings(BaseSettings):
    """Metric Correlation Engine 오케스트레이터 설정.

    환경변수 prefix: SELFHEALING_CORRELATION_
    예: SELFHEALING_CORRELATION_ENABLED=true

    서브 모듈 설정(Co-occurrence, DAG, Observer)은
    settings.correlation.CorrelationSettings에서 관리한다.
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

    # ── 주기적 분석 틱 ──
    analysis_interval_seconds: float = Field(
        default=60.0,
        ge=10.0,
        le=600.0,
        description="주기적 분석 틱 간격 (초)",
    )

    # ── Root Cause Ranker 가중치 ──
    weight_topology: float = Field(default=0.35, ge=0.0, le=1.0)
    weight_temporal: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_blast_radius: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_historical: float = Field(default=0.15, ge=0.0, le=1.0)

    # ── 연동 플래그 ──
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
from selfhealing.services.correlation_engine.event_graph_builder import EventGraphBuilder
from selfhealing.services.correlation_engine.root_cause_ranker import RootCauseRanker
from selfhealing.services.correlation_engine.incident_timeline import IncidentTimelineBuilder
from selfhealing.services.correlation_engine.interfaces import (
    CorrelationStrategy,
    RootCauseStrategy,
    GraphBuildStrategy,
)
from selfhealing.settings.correlation import get_correlation_settings
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

    def __init__(self, settings: CorrelationSettings | None = None):
        self._settings = settings or get_correlation_settings()
        self._engine_settings = get_correlation_engine_settings()
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
        if not self._engine_settings.enabled:
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
                max_events_per_dag=self._settings.max_events_per_dag,
                min_confidence=self._settings.min_confidence,
                max_graph_depth=self._settings.max_graph_depth,
            )
            self._root_cause_ranker = RootCauseRanker(self._settings)
            self._timeline_builder = IncidentTimelineBuilder()

            # 2. 상태 복원 (Cold Start 방지)
            if self._engine_settings.state_persistence_enabled:
                self._co_occurrence.load_state()

            # 3. Wildcard Observer 등록
            self._observer = WildcardObserver(self._settings, self._co_occurrence)
            self._observer.register(self._get_event_bus())

            # 4. 분석 트리거 이벤트 구독
            self._register_event_handlers()

            # 5. 동적 설정 변경 구독
            self._subscribe_config_updates()

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

        if self._engine_settings.state_persistence_enabled and self._co_occurrence:
            self._co_occurrence.save_state()

        self._initialized = False
        logger.info("[CorrelationEngine] Shut down")
```

### 2.2 주기적 분석 틱

> **⚠️ v2.0.0 변경**: `threading.Thread` 직접 사용 → `LeaderScheduler` 기반으로 교체.
> 상세 설계는 **§9**를 참조한다.

```python
    def start_analysis_loop(self) -> None:
        """주기적 분석 루프 시작 — LeaderScheduler 기반

        v2.0.0: threading.Thread 대신 LeaderScheduler를 사용하여
        다중 프로세스 환경에서 리더 노드 1개만 분석 루프를 실행한다.
        상세 설계 → §9 참조.
        """
        from selfhealing.coordination.scheduler import get_leader_scheduler

        scheduler = get_leader_scheduler("correlation-engine")
        scheduler.add_job(
            name="correlation_periodic_analysis",
            func=self._run_periodic_analysis,
            interval_seconds=self._settings.analysis_interval_seconds,
        )
        scheduler.start()
        self._running = True

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

> **⚠️ v2.0.0 변경**: `incident_id` 파라미터 추가, 멱등성 검사 내장, Postmortem 정밀 타겟팅.
> 상세 설계는 **§10** (Deterministic ID), **§12** (멱등성)를 참조한다.

```python
    def analyze_incident(
        self,
        incident_id: str | None = None,
        window_seconds: float | None = None,
    ) -> dict | None:
        """현재 시간 윈도우의 인시던트 분석 (온디맨드)

        Args:
            incident_id: 분석 대상 인시던트 ID.
                         None이면 DAG Root Node 기반 결정론적 ID를 자동 생성한다 (§10).
            window_seconds: 분석 윈도우 (None이면 settings 기본값)

        Returns:
            {
                "incident_id": str,
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

            # 3. Incident ID 확정 (§10: Deterministic Fallback)
            resolved_id = incident_id or dag.incident_id

            # 4. 멱등성 검사 (§12: IdempotencyService)
            if self._check_already_analyzed(resolved_id):
                logger.debug(f"[CorrelationEngine] Already analyzed: {resolved_id}")
                return None

            # 5. Root Cause 분석
            co_data = self._co_occurrence.analyze_tick() if self._co_occurrence else []
            root_cause = self._root_cause_ranker.rank(dag, co_data)

            # 6. 타임라인 생성
            timeline = self._timeline_builder.build(dag, root_cause)

            # 7. Postmortem 연동 (§10: incident_id 기반 정밀 타겟팅)
            if self._settings.postmortem_integration_enabled:
                self._inject_to_postmortem(resolved_id, timeline, root_cause)

            # 8. 분석 완료 마킹 (§12)
            self._mark_analysis_complete(resolved_id)

            return {
                "incident_id": resolved_id,
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
        """인시던트 해소 시 자동 분석

        v2.0.0 변경:
        - event.data에서 incident_id를 추출하여 정밀 타겟팅 (§10)
        - IdempotencyService로 중복 분석 차단 (§12)
        """
        try:
            incident_id = event.data.get("incident_id") if hasattr(event, "data") and isinstance(event.data, dict) else None

            result = self.analyze_incident(incident_id=incident_id)
            if result:
                logger.info(
                    f"[CorrelationEngine] Auto-analysis complete: "
                    f"incident={result['incident_id']}, "
                    f"root_cause={result['root_cause'].primary_cause.event_node.service_name} "
                    f"({result['root_cause'].primary_cause.score:.0%})"
                )
        except Exception as e:
            logger.debug(f"[CorrelationEngine] Auto-analysis skipped: {e}")
```

### 3.2 Postmortem 연동

> **⚠️ v2.0.0 변경**: `get_latest_incident()` 제거 → `incident_id` 파라미터 기반 정밀 타겟팅.
> `get_latest_incident()`는 대형 장애 시 Race Condition으로 결제 장애 분석이 로그인 장애
> 포스트모템에 덮어씌워지는 위험이 있었다. 상세 설계는 **§10**을 참조한다.

```python
    def _inject_to_postmortem(
        self,
        incident_id: str,
        timeline: IncidentTimeline,
        root_cause: RootCauseAnalysis,
    ) -> None:
        """Postmortem 데이터에 상관관계 분석 결과 주입

        v2.0.0: get_latest_incident() → incident_id 기반 정밀 타겟팅 (§10)

        Args:
            incident_id: 분석 대상 인시던트 ID (§10에서 보장)
            timeline: 인시던트 타임라인
            root_cause: 근본 원인 분석 결과
        """
        try:
            from selfhealing.services.postmortem.store import get_incident_by_id

            # §10: incident_id 기반 정밀 조회 (get_latest_incident가 아님)
            incident = get_incident_by_id(incident_id)
            if not incident:
                logger.debug(
                    f"[CorrelationEngine] Postmortem not found: {incident_id}, "
                    f"skipping injection"
                )
                return

            # update_incident_fields() 는 §10.3에서 신규 구현
            from selfhealing.services.postmortem.store import update_incident_fields

            update_incident_fields(
                incident_id=incident_id,
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

> **⚠️ v2.0.0 변경**: 실제 구현(`service.py`)에 이미 존재하는 Bulkhead + Fallback 패턴을 문서에 반영.
> 실제 코드에서 `set_root_cause_strategy(primary, fallback)`로 Primary/Fallback 분리가 완료되어 있다.
> PolicyComposer 선언적 전환은 **§11**에서 설계한다.

```python
    # ── Strategy Setters ── (256번 ML Strategy Interface 참조)

    def set_correlation_strategy(self, strategy: CorrelationStrategy) -> None:
        """상관관계 분석 전략 교체 (런타임)"""
        if not isinstance(strategy, CorrelationStrategy):
            raise TypeError(
                f"CorrelationStrategy Protocol을 구현해야 합니다: "
                f"{type(strategy).__name__}"
            )
        self._correlation_strategy = strategy

    def set_root_cause_strategy(
        self,
        primary: RootCauseStrategy,
        fallback: RootCauseStrategy | None = None,
    ) -> None:
        """근본 원인 분석 전략 교체 (런타임)

        Args:
            primary: 주 전략 (ML/LLM)
            fallback: 대체 전략 (None이면 기본 RootCauseRanker)
        """
        if not isinstance(primary, RootCauseStrategy):
            raise TypeError(
                f"RootCauseStrategy Protocol을 구현해야 합니다: "
                f"{type(primary).__name__}"
            )
        self._root_cause_strategy = primary
        self._root_cause_fallback = fallback or RootCauseRanker()
        self._primary_strategy_name = type(primary).__name__
        self._fallback_strategy_name = type(self._root_cause_fallback).__name__

    def set_graph_strategy(self, strategy: GraphBuildStrategy) -> None:
        """DAG 구축 전략 교체 (런타임)"""
        if not isinstance(strategy, GraphBuildStrategy):
            raise TypeError(
                f"GraphBuildStrategy Protocol을 구현해야 합니다: "
                f"{type(strategy).__name__}"
            )
        self._graph_strategy = strategy
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
| **분산** | 워커 4개 환경에서 start_analysis_loop() | 리더 1개만 분석 실행 (§9) |
| **분산** | 리더 크래시 → 새 리더 승계 | 분석 재개, 중복 실행 없음 (§9, §12) |
| **멱등성** | CB_CLOSED 이벤트 3번 연속 수신 | analyze_incident() 1회만 실행 (§12) |
| **Postmortem** | 동시 다발 장애 (결제+로그인) | 각각 올바른 incident에 주입 (§10) |
| **Fallback** | ML 전략 30초 Timeout | Default Ranker Fallback + 메트릭 (§11) |
| **설정** | API로 zscore_threshold 변경 | 즉시 반영 + deque 리사이징 (§13) |

---

## 9. 분산 분석 루프 — LeaderScheduler 적용

### 9.1 문제

`CorrelationEngineService`가 Python 싱글톤(`_instance`)으로 구현되어 있고, `threading.Thread`로 `_analysis_loop`를 실행한다. Gunicorn/uWSGI 워커가 4개 떠 있으면 **프로세스마다** 독립 분석 루프가 돌아간다:
- DB/Redis I/O가 4배로 증가
- LearningService에 동일 패턴이 중복 보고
- CoOccurrenceTracker 상태 영속화가 Race Condition 발생

### 9.2 해결: LeaderScheduler

기존 인프라인 [`coordination/scheduler.py`](../../packages/selfhealing-python/src/selfhealing/coordination/scheduler.py)의 `LeaderScheduler`를 사용한다.

**선택 이유**: `DLQConsumerCoordinator`가 이미 동일한 패턴(리더 1개만 소비)으로 운영 중이며, Redis 기반 `SET NX EX` + Lua 스크립트, Fencing Token, Self-Fencing, Region Priority까지 검증된 인프라이다.

```python
# initialize() 내부에서
from selfhealing.coordination.scheduler import get_leader_scheduler

def start_analysis_loop(self) -> None:
    scheduler = get_leader_scheduler("correlation-engine")
    scheduler.add_job(
        name="correlation_periodic_analysis",
        func=self._run_periodic_analysis,
        interval_seconds=self._settings.analysis_interval_seconds,
    )
    scheduler.start()
    self._running = True
```

### 9.3 Rolling Update Graceful Shutdown 보완

코드 검증 결과 LeaderScheduler에 **3가지 잠재적 문제**가 확인되었다:

| 문제 | 코드 위치 | 영향 |
|------|----------|------|
| SIGTERM 시 `scheduler.stop()` 미호출 | [`shutdown_integration.py`](../../packages/selfhealing-python/src/selfhealing/coordination/shutdown_integration.py) — `shutdown_all_electors()`가 **elector만** stop | 리더십 반납 후에도 분석 job이 잠시 실행 → 이중 실행 |
| 5초 하드코딩 타임아웃 | [`scheduler.py` L253](../../packages/selfhealing-python/src/selfhealing/coordination/scheduler.py) — `join(timeout=5.0)` | 대규모 DAG 분석이 5초 초과 시 강제 중단 |
| `is_lease_valid()` 미사용 | [`scheduler.py` `_execute_job()`](../../packages/selfhealing-python/src/selfhealing/coordination/scheduler.py) | 장기 분석 중 lease 만료 감지 불가 → stale leader |

**보완 설계**:

```python
# 1) shutdown_integration에 scheduler 자체를 등록
#    → SIGTERM 시 scheduler.stop() 호출 보장
from selfhealing.coordination.shutdown_integration import (
    register_for_graceful_shutdown,
)

def start_analysis_loop(self) -> None:
    scheduler = get_leader_scheduler("correlation-engine")
    # ...
    scheduler.start()

    # shutdown 시 scheduler.stop()이 호출되도록 elector 외에
    # scheduler도 등록 (elector는 scheduler 내부에서 이미 등록됨)
    # → scheduler.stop()은 스레드 join 후 elector.stop() 순서를 보장
    self._scheduler = scheduler

def shutdown(self) -> None:
    # 2) 분석 루프 먼저 종료 → 리더십 반납 순서 보장
    if hasattr(self, '_scheduler') and self._scheduler:
        self._scheduler.stop()  # join(5s) + elector.stop()
    # ... 기존 cleanup
```

```python
# 3) _run_periodic_analysis에 Self-Fencing 삽입
def _run_periodic_analysis(self) -> None:
    from selfhealing.coordination.factory import get_leader_elector

    elector = get_leader_elector("correlation-engine")
    if not elector.is_lease_valid():
        logger.warning("[CorrelationEngine] Lease expired, aborting analysis")
        return

    # ... 기존 분석 로직
```

### 9.4 테스트 시나리오

| 시나리오 | 검증 |
|---------|------|
| 워커 4개 → start_analysis_loop() | `correlation_periodic_analysis` job이 리더 1개에서만 실행 |
| 리더 Pod 종료 (SIGTERM) | scheduler.stop() → join → elector.stop() 순서 보장 |
| 리더 크래시 → 새 리더 | lease TTL(30초) 후 새 리더가 분석 재개 |
| 장기 분석 중 lease 만료 | `is_lease_valid()` 체크로 조기 중단 |

---

## 10. Deterministic Incident ID + Postmortem 정밀 타겟팅

### 10.1 문제

기존 `_inject_to_postmortem()`의 `store.get_latest_incident()`는:
- **Race Condition**: 결제 장애 분석이 로그인 장애 포스트모템에 덮어씌워짐
- **구현 완료**: `get_incident_by_id()`, `update_incident_fields()` 모두 store.py에 구현됨 (store.py L522)
- **비결정적 ID**: `EventGraphBuilder._generate_incident_id()`가 `uuid.uuid4().hex[:6]`를 포함하여 동일 이벤트도 다른 ID 생성

### 10.2 Deterministic Incident ID 생성

**선택**: DAG Root Node의 event_type + service_name + 양자화된 시간 윈도우 해시 기반

**선택 이유**: `SelfHealingEvent`에 `incident_id` 필드가 없고(`data` dict에 키로 넣을 수도 없을 수도 있는 구조), `correlation_id`도 optional이므로, DAG 구축 후 확정되는 Root Node 정보가 가장 안정적이다.

```python
import hashlib

def _generate_deterministic_incident_id(
    self, dag: EventDAG,
) -> str:
    """DAG Root Node 기반 결정론적 인시던트 ID 생성.

    동일 인시던트(동일 root event + 동일 시간 윈도우)에 대해
    항상 동일한 ID를 반환한다. 이를 통해:
    - Postmortem 정밀 타겟팅 (§10.1)
    - IdempotencyService 멱등성 키 (§12)
    를 보장한다.

    양자화 단위: 60초 (1분)
    → 같은 1분 안에 동일 서비스의 동일 이벤트 타입이 발생하면 같은 ID.
    """
    if dag.root_nodes:
        root = dag.root_nodes[0]  # 시간순 정렬된 첫 번째 root
        quantized_ts = int(root.timestamp // 60)  # 1분 단위 양자화
        content = f"{root.event_type}:{root.service_name}:{quantized_ts}"
    else:
        # root가 없는 극단적 케이스 → window 기반 fallback
        quantized_ts = int(dag.window_start // 60)
        content = f"no_root:{quantized_ts}"

    digest = hashlib.sha256(content.encode()).hexdigest()[:12]
    return f"corr_{digest}"
```

### 10.3 Postmortem Store 확장 — `update_incident_fields()`

현재 [`store.py`](../../packages/selfhealing-python/src/selfhealing/services/postmortem/store.py)에는 `get_incident_by_id()`만 존재하고, `update_incident_fields()`는 없다. 신규 구현이 필요하다:

```python
# postmortem/store.py 에 추가
def update_incident_fields(incident_id: str, fields: dict[str, Any]) -> bool:
    """기존 인시던트의 특정 필드를 부분 업데이트한다.

    In-Memory 캐시와 PostgreSQL 모두 업데이트를 시도한다.
    JSONField는 기존 dict 데이터와 deep merge하여 보존한다.

    Args:
        incident_id: 업데이트 대상 인시던트 ID (unique)
        fields: 업데이트할 필드 dict

    Returns:
        업데이트 성공 여부
    """
    updated = False

    # 1. In-Memory 캐시 업데이트
    with _healing_incidents_lock:
        for incident in _healing_incidents:
            if incident.get("incident_id") == incident_id:
                for key, value in fields.items():
                    existing = incident.get(key)
                    if isinstance(existing, dict) and isinstance(value, dict):
                        existing.update(value)
                    else:
                        incident[key] = value
                updated = True
                break

    # 2. PostgreSQL 업데이트
    if _db_persistence_enabled:
        try:
            db_updated = _update_incident_fields_in_db(incident_id, fields)
            updated = updated or db_updated
        except Exception as e:
            logger.warning(f"[Postmortem] DB update_incident_fields failed: {e}")

    return updated
```

### 10.4 analyze_incident() 내 Fallback 체인

```python
def analyze_incident(self, incident_id=None, window_seconds=None):
    # ...
    dag = self._graph_builder.build_dag(events, window)

    # Incident ID 확정 — 3단계 Fallback
    # 1) 파라미터로 전달된 명시적 ID
    # 2) event.data.get("incident_id") 에서 추출 (이벤트 트리거 경로)
    # 3) DAG Root 기반 결정론적 생성 (§10.2)
    resolved_id = incident_id or dag.incident_id
    # dag.incident_id는 EventGraphBuilder._generate_incident_id()에서 생성
    # (현재는 비결정적이므로 _generate_deterministic_incident_id로 교체 필요)
```

### 10.5 테스트 시나리오

| 시나리오 | 검증 |
|---------|------|
| 동일 이벤트 셋으로 2회 분석 | 동일한 `corr_...` ID 생성 |
| 결제 장애 + 로그인 장애 동시 발생 | 서로 다른 ID → 서로 다른 Postmortem에 주입 |
| event.data에 incident_id 누락 | DAG Root 기반 fallback ID 자동 생성 |
| `update_incident_fields()` 호출 | 기존 Postmortem 필드 보존 + correlation 필드만 추가 |

---

## 11. PolicyComposer 기반 Fallback 파이프라인 + Observability

### 11.1 현재 상태 (v1.0.0)

실제 [`service.py`](../../packages/selfhealing-python/src/selfhealing/services/correlation_engine/service.py)의 `analyze_root_cause()` 메서드에는 이미 **Bulkhead Timeout + Fallback**이 수동 try/except로 구현되어 있다:

```python
# service.py L196-241 (현재 코드)
try:
    result = self._execute_with_ml_bulkhead(
        self._root_cause_strategy.rank_causes, dag, co_occurrence_data)
    result.strategy_metadata = StrategyMetadata(
        strategy_name=self._primary_strategy_name, fallback_used=False, ...)
except (BulkheadFullError, BulkheadTimeoutError, Exception) as e:
    result = self._root_cause_fallback.rank_causes(dag, co_occurrence_data)
    result.strategy_metadata = StrategyMetadata(
        fallback_used=True,
        fallback_reason=f"{type(e).__name__}: {str(e)[:200]}", ...)
```

이 코드는 **기능적으로 동작**하지만, 선언적 PolicyComposer 패턴과 불일치하며 Fallback Observability가 누락되어 있다.

### 11.2 현재 구현: ML Bulkhead + 수동 Fallback

현재 `analyze_root_cause()`는 `_execute_with_ml_bulkhead()` + 수동 try/except Fallback으로 구현되어 있다.
선언적 PolicyComposer 전환은 **향후 리팩토링 대상**이다.

```python
def analyze_root_cause(self, dag, co_occurrence_data) -> RootCauseAnalysis:
    """ML Bulkhead + Fallback 기반 근본 원인 분석.

    구성:
      _execute_with_ml_bulkhead(primary_strategy.rank_causes())
        → 실패 시 root_cause_fallback.rank_causes()
    """
    try:
        result = self._execute_with_ml_bulkhead(
            self._root_cause_strategy.rank_causes, dag, co_occurrence_data)
        result.strategy_metadata = StrategyMetadata(
            strategy_name=self._primary_strategy_name,
            fallback_used=False,
            analysis_duration_ms=duration_ms)
    except (BulkheadFullError, BulkheadTimeoutError, Exception) as e:
        result = self._root_cause_fallback.rank_causes(dag, co_occurrence_data)
        result.strategy_metadata = StrategyMetadata(
            strategy_name=self._fallback_strategy_name,
            fallback_used=True,
            fallback_reason=f"{type(e).__name__}: {str(e)[:200]}",
            primary_strategy_name=self._primary_strategy_name,
            analysis_duration_ms=duration_ms)
    return result
```

### 11.3 향후 목표: PolicyComposer 선언적 파이프라인

`compose()` + `FallbackPolicy` + `BulkheadPolicy` + `MetricsHook` 조합으로 전환하면 Fallback Observability가 자동 확보된다. [`resilience/policies/composer.py`](../../packages/selfhealing-python/src/selfhealing/resilience/policies/composer.py)의 `compose()`가 역순 중첩 래핑, Guard/Hook/Sink 파이프라인을 완전 지원하며, [`presets.py`](../../packages/selfhealing-python/src/selfhealing/resilience/policies/presets.py)의 `ha_pipeline()`이 이미 동일 패턴을 사용 중이다.

### 11.4 Fallback Observability 보완

코드 검증 결과, 기존 `MetricsHook`의 `on_success()`에서 `SUCCESS`와 `SUCCESS_WITH_FALLBACK`을 **구분하지 않는** 갭이 확인되었다:

| Hook | 현재 상태 | 보완 |
|------|----------|------|
| `MetricsHook` | `selfhealing_pipeline_success_total`에 SUCCESS_WITH_FALLBACK 합산 | `selfhealing_pipeline_fallback_total` Counter 신규 추가 |
| `AuditHook` | outcome/metadata 로깅 안 함 | `result.outcome`, `result.metadata["original_error"]` 로깅 추가 |
| `EventBusHook` | fallback 정보 미포함 | 페이로드에 `fallback_used`, `original_error` 추가 |

**MetricsHook 확장** (Fallback 전용 Counter):

```python
# hooks/metrics.py on_success() 확장
def on_success(self, policy_name: str, result: PolicyResult) -> None:
    self._success_counter.labels(pipeline=self._name).inc()
    self._duration_histogram.labels(pipeline=self._name).observe(
        result.total_duration_ms / 1000
    )

    # v2.0.0: Fallback 전용 메트릭
    if result.outcome == PolicyOutcome.SUCCESS_WITH_FALLBACK:
        self._fallback_counter.labels(
            pipeline=self._name,
            error_type=result.metadata.get("original_error", "unknown")[:50],
        ).inc()
```

이를 통해 SRE 팀이 Grafana에서 `rate(selfhealing_pipeline_fallback_total[5m])`으로 ML 인프라 가용성을 실시간 모니터링할 수 있다.

### 11.4 테스트 시나리오

| 시나리오 | 검증 |
|---------|------|
| ML 전략 정상 응답 | PolicyOutcome.SUCCESS, fallback 메트릭 미증가 |
| ML 전략 30초 Timeout | PolicyOutcome.SUCCESS_WITH_FALLBACK, Default Ranker 결과 반환 |
| ML 전략 502 에러 | Fallback Counter `error_type="BulkheadFullError"` 증가 |
| Fallback도 실패 | PolicyOutcome.FAILURE → DLQSink 전달 (ha_pipeline 패턴) |

---

## 12. IdempotencyService 기반 분석 멱등성 보장

### 12.1 문제

`CB_CLOSED`/`EMERGENCY_RECOVERY_COMPLETED` 이벤트가 Event Storm 시 단시간 내 복수 발행되면 `analyze_incident()`가 중복 실행된다.

### 12.2 선택지 분석

| 방식 | 장점 | 단점 |
|------|------|------|
| 인메모리 Debounce (`EventGraphTrigger` 패턴) | 구현 간단, 의존성 없음 | 리더 크래시 시 상태 소실 |
| `RedisCooldownStore` | Pod 간 공유, Redis TTL 자동 만료 | 별도 key prefix 필요, cooldown만 지원 |
| **`IdempotencyService`** + Deterministic ID | Cache+DB 2단 조회, 완전 멱등성 | 약간의 오버헤드 |

**선택**: `IdempotencyService` + §10의 Deterministic Incident ID

**선택 이유**: §10에서 결정론적 `incident_id`를 생성하므로, `IdempotencyService`의 `check()` → `mark_as_processed()` 패턴과 자연스럽게 결합된다. 이미 [`services/idempotency/service.py`](../../packages/selfhealing-python/src/selfhealing/services/idempotency/service.py)가 `IdempotencyDomain.INTERNAL_PROCESS` 도메인을 지원하며 Cache(Redis) → DB fallback 2단 조회로 검증되어 있다. 단순 cooldown이 아닌 **"이 인시던트를 이미 분석했는가"** 라는 정확한 의미론(semantics)을 제공한다.

```python
from selfhealing.services.idempotency import (
    IdempotencyService,
    IdempotencyKey,
    IdempotencyDomain,
)

def _check_already_analyzed(self, incident_id: str) -> bool:
    """이 인시던트가 이미 분석되었는지 확인한다 (§12).

    IdempotencyService의 Cache(Redis) → DB 2단 조회를 사용하여
    프로세스/Pod 간 완전한 멱등성을 보장한다.
    """
    try:
        service = IdempotencyService()
        key = IdempotencyKey.for_operation(
            entity_type="correlation_analysis",
            entity_id=incident_id,
            operation="analyze",
            domain=IdempotencyDomain.INTERNAL_PROCESS,
        )
        result = service.check(key)
        return result.is_duplicate
    except Exception:
        return False  # IdempotencyService 장애 시 → 분석 허용 (Fail-Open)

def _mark_analysis_complete(self, incident_id: str) -> None:
    """분석 완료를 마킹한다 (§12)."""
    try:
        service = IdempotencyService()
        key = IdempotencyKey.for_operation(
            entity_type="correlation_analysis",
            entity_id=incident_id,
            operation="analyze",
            domain=IdempotencyDomain.INTERNAL_PROCESS,
        )
        service.mark_as_processed(key)
    except Exception as e:
        logger.debug(f"[CorrelationEngine] Idempotency mark failed: {e}")
```

### 12.3 analyze_incident() 통합 흐름

```
analyze_incident(incident_id=None)
  │
  ├─ 1. 이벤트 수집 + DAG 구축
  ├─ 2. incident_id 확정 (§10 Deterministic Fallback)
  ├─ 3. IdempotencyService.check(incident_id)  ← §12 멱등성 검사
  │     └─ is_duplicate=True → return None (이미 분석됨)
  ├─ 4. Root Cause 분석 (§11 PolicyComposer)
  ├─ 5. Timeline 생성
  ├─ 6. Postmortem 주입 (§10 정밀 타겟팅)
  ├─ 7. IdempotencyService.mark_as_processed()  ← §12 완료 마킹
  └─ 8. 결과 반환
```

### 12.4 테스트 시나리오

| 시나리오 | 검증 |
|---------|------|
| CB_CLOSED 이벤트 3회 연속 수신 | analyze_incident() 1회 실행, 2·3회는 `is_duplicate=True` |
| 리더 크래시 → 새 리더 | IdempotencyService Redis 조회로 이미 분석된 건 스킵 |
| IdempotencyService Redis 장애 | Fail-Open → 분석 허용 (안전한 방향) |
| 1분 간격으로 같은 서비스 같은 이벤트 | 동일한 결정론적 ID → 중복 분석 차단 |

---

## 13. RuntimeConfigManager 연동 동적 설정 리로드 + State Flush

### 13.1 기존 2계층 설정 구조

| 계층 | 구현 | 동적 여부 |
|------|------|----------|
| 정적 | `get_correlation_engine_settings()` — Pydantic BaseSettings | Pod 재시작 필요 |
| 동적 | [`RuntimeConfigManager`](../../packages/selfhealing-python/src/selfhealing/services/runtime_config/) — Redis StateBackend | 라이브 변경 가능 |

### 13.2 API 엔드포인트 등록

기존 패턴(`GET/PUT /api/self-healing/config/{type}/`)에 `correlation` 타입을 추가한다:

| Method | Endpoint | 설명 |
|--------|---------|------|
| `GET` | `/api/self-healing/config/correlation/` | 현재 설정 조회 |
| `PUT` | `/api/self-healing/config/correlation/` | 설정 변경 (IMMEDIATE/DELAYED/GRACEFUL) |
| `GET` | `/api/self-healing/config/correlation/history/` | 변경 이력 |
| `POST` | `/api/self-healing/config/correlation/rollback/` | 특정 버전 롤백 |

### 13.3 CONFIG_UPDATED 이벤트 구독 — State Flush & Resize

**코드 검증 결과 발견된 시스템 수준 갭**: `RuntimeConfigManager._update_config()`는 설정 변경 후 `EventType.CONFIG_UPDATED` 이벤트를 EventBus에 **발행하지 않는다**. audit 로그만 기록한다. 이로 인해 `BulkheadRegistry._on_config_updated()`와 `HedgingStrategy._on_config_updated()` 등 기존 구독자도 **실제로는 동작하지 않는 상태**이다.

**선행 조건**: `RuntimeConfigManager._update_config()` 끝에 EventBus 이벤트 발행 코드를 추가해야 한다. 이는 257번 범위를 넘는 시스템 수준 수정이므로 별도 이슈로 트래킹한다.

**CorrelationEngine 구독 코드** (선행 조건 충족 후 동작):

```python
def _subscribe_config_updates(self) -> None:
    """RuntimeConfigManager 설정 변경 이벤트 구독.

    BulkheadRegistry._subscribe_config_updates() 패턴과 동일.
    """
    try:
        bus = self._get_event_bus()
        bus.subscribe(
            EventType.CONFIG_UPDATED,
            self._on_config_updated,
            priority=EventPriority.NORMAL,
        )
    except Exception as e:
        logger.debug(f"[CorrelationEngine] Config update subscription failed: {e}")

def _on_config_updated(self, event) -> None:
    """설정 변경 시 내부 상태 Flush & Resize.

    변경 가능한 설정별 영향:
    - window_seconds: 이벤트 수집 윈도우 크기 변경
    - max_tracked_pairs: CoOccurrenceTracker 내부 dict 크기 제한
    - count_history_size: deque maxlen 리사이징
    - zscore_threshold: 즉시 반영 (다음 analyze_tick에서 적용)
    - enabled: False → 분석 루프 중단 + EventBus 구독 해제
    """
    config_type = event.data.get("config_type", "") if hasattr(event, "data") and isinstance(event.data, dict) else ""
    if config_type != "correlation":
        return

    try:
        # 1. Pydantic Settings 캐시 무효화 → 새 값으로 재생성
        from selfhealing.settings.correlation_engine import (
            reset_correlation_engine_settings,
            get_correlation_engine_settings,
        )
        reset_correlation_engine_settings()
        new_settings = get_correlation_engine_settings()
        self._settings = new_settings

        # 2. CoOccurrenceTracker 내부 데이터 구조 리사이징
        if self._co_occurrence:
            self._co_occurrence.resize(
                max_tracked_pairs=new_settings.max_tracked_pairs,
                count_history_size=new_settings.count_history_size,
            )

        # 3. enabled=False 전환 시 엔진 중단
        if not new_settings.enabled:
            self.shutdown()
            logger.info("[CorrelationEngine] Disabled via dynamic config")
            return

        logger.info(
            f"[CorrelationEngine] Config reloaded: "
            f"zscore={new_settings.zscore_threshold}, "
            f"window={new_settings.window_seconds}s"
        )

    except Exception as e:
        logger.error(f"[CorrelationEngine] Config reload failed: {e}")
```

### 13.4 CoOccurrenceTracker.resize() 설계

```python
# co_occurrence_tracker.py 에 추가
def resize(
    self,
    max_tracked_pairs: int | None = None,
    count_history_size: int | None = None,
) -> None:
    """내부 데이터 구조를 새 설정에 맞게 리사이징한다.

    - max_tracked_pairs 축소 시: LRU 기반으로 오래된 쌍 제거
    - count_history_size 축소 시: deque maxlen 변경 (좌측 자동 truncate)
    """
    if max_tracked_pairs is not None:
        while len(self._pair_detectors) > max_tracked_pairs:
            # 가장 오래된(access 빈도 낮은) 쌍부터 제거
            oldest_key = min(
                self._pair_detectors,
                key=lambda k: self._pair_detectors[k].last_update,
            )
            del self._pair_detectors[oldest_key]

    if count_history_size is not None:
        from collections import deque
        for detector in self._pair_detectors.values():
            old_data = list(detector.count_history)
            detector.count_history = deque(
                old_data[-count_history_size:],
                maxlen=count_history_size,
            )
```

### 13.5 선행 이슈 트래킹

| 이슈 | 범위 | 설명 |
|------|------|------|
| `CONFIG_UPDATED` 이벤트 미발행 | **시스템 전체** | `RuntimeConfigManager._update_config()` 끝에 EventBus 발행 추가 필요. BulkheadRegistry, HedgingStrategy 등 기존 구독자도 이 수정에 의존. |

### 13.6 테스트 시나리오

| 시나리오 | 검증 |
|---------|------|
| API로 `zscore_threshold` 2.5 → 3.5 | 다음 analyze_tick에서 새 임계값 적용 |
| API로 `max_tracked_pairs` 1000 → 500 | 초과 500쌍 즉시 제거, 메모리 감소 확인 |
| API로 `enabled=False` | 분석 루프 중단 + EventBus 구독 해제 |
| API로 `count_history_size` 100 → 50 | deque maxlen 50으로 리사이징, 기존 데이터 보존 (최근 50건) |
| `CONFIG_UPDATED` 미발행 환경 | 설정 변경이 런타임에 반영되지 않음 (선행 이슈) |
