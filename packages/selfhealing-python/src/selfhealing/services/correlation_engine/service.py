"""
Correlation Engine Service — 오케스트레이터 + 전략 교체 관리.

Correlation Engine의 서브 모듈 초기화, 주기적 분석, 온디맨드 분석,
기존 서비스(EventBus, Postmortem, Learning, BlastRadius) 연동,
전략(Strategy) 교체를 총괄하는 오케스트레이터이다.

전략 패턴:
    - CorrelationStrategy: 상관관계 분석 (기본: CoOccurrenceTracker)
    - RootCauseStrategy: 근본 원인 분석 (기본: RootCauseRanker)
    - GraphBuildStrategy: DAG 구축 (기본: EventGraphBuilder)

ML 추론 격리:
    - BulkheadRegistry의 "ml_inference" 도메인을 사용하여
      ML/LLM 추론을 전용 스레드 풀에서 격리 실행한다.
    - BulkheadFullError/BulkheadTimeoutError 발생 시 Fallback 전략으로 전환한다.

분산 분석 루프:
    - LeaderScheduler 기반 분산 분석 루프로 다중 프로세스 중복 실행 방지.

연동:
    - EventBus: WildcardObserver로 전체 이벤트 수신, CB_CLOSED 등 분석 트리거
    - Postmortem: incident_id 기반 정밀 타겟팅으로 분석 결과 주입
    - Learning: 발견된 상관관계 패턴 축적
    - BlastRadius: DAG 구축 시 서비스 의존성 그래프 참조

Usage:
    from selfhealing.services.correlation_engine.service import (
        CorrelationEngineService,
    )

    engine = CorrelationEngineService.get_instance()
    engine.initialize()
    engine.start_analysis_loop()
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

from selfhealing.interfaces.ml_strategy import (
    AnomalyDetectionStrategy,
    BatchCapable,
    StrategyLifecycle,
)
from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CoOccurrenceTracker,
    CorrelationResult,
)
from selfhealing.services.correlation_engine.event_graph import EventDAG, EventNode
from selfhealing.services.correlation_engine.interfaces import (
    CorrelationStrategy,
    GraphBuildStrategy,
    RootCauseStrategy,
)
from selfhealing.services.correlation_engine.root_cause_ranker import (
    RootCauseAnalysis,
    RootCauseRanker,
    StrategyMetadata,
)
from selfhealing.settings.correlation import (
    CorrelationSettings,
    get_correlation_settings,
)
from selfhealing.settings.correlation_engine import (
    CorrelationEngineSettings,
    get_correlation_engine_settings,
)

logger = logging.getLogger(__name__)


# =============================================================================
# ML 추론 인시던트 심각도별 우선순위 watermark
# =============================================================================

ML_PRIORITY_WATERMARKS: dict[str, float] = {
    "critical": 0.0,  # 항상 처리
    "standard": 0.4,  # 토큰 40% 이상 시 처리
    "background": 0.7,  # 토큰 70% 이상 시만 처리 (학습 등)
}
"""RateController의 PRIORITY_WATERMARKS 패턴 적용."""


class CorrelationEngineService:
    """Correlation Engine 오케스트레이터.

    책임:
    1. 서브 모듈 초기화 및 수명주기 관리
    2. 주기적 분석 틱 실행 (LeaderScheduler 기반)
    3. 인시던트 발생 시 온디맨드 분석
    4. 전략(Strategy) 교체 관리
    5. 기존 서비스(EventBus, Postmortem, Learning, BlastRadius) 연동

    설계 원칙:
    - 이 서비스가 제거되어도 나머지 시스템 정상 동작
    - 이 서비스 내부 오류가 복원력 기능에 영향 없음
    - 전략 교체 시 코어 코드 수정 불필요
    """

    _instance: CorrelationEngineService | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> CorrelationEngineService:
        """Singleton 인스턴스."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """인스턴스 리셋 (테스트용)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown()
            cls._instance = None

    def __init__(self, settings: CorrelationSettings | None = None) -> None:
        self._settings = settings or get_correlation_settings()
        self._engine_settings = get_correlation_engine_settings()
        self._initialized = False
        self._running = False
        self._tick_count = 0

        # 기본 전략 (Day-1 제공)
        self._correlation_strategy: CorrelationStrategy = CoOccurrenceTracker(
            self._settings,
        )
        self._root_cause_strategy: RootCauseStrategy = RootCauseRanker()

        # Fallback 전략 (기본: RootCauseRanker)
        self._root_cause_fallback: RootCauseStrategy = RootCauseRanker()
        self._primary_strategy_name: str = type(self._root_cause_strategy).__name__
        self._fallback_strategy_name: str = type(self._root_cause_fallback).__name__

        # GraphBuildStrategy는 EventGraphBuilder 의존성이 있으므로 지연 초기화
        self._graph_strategy: GraphBuildStrategy | None = None

        # 서브 모듈 (initialize() 호출 전까지 None)
        self._observer = None
        self._co_occurrence: CoOccurrenceTracker | None = None
        self._graph_builder = None
        self._root_cause_ranker: RootCauseRanker | None = None
        self._timeline_builder = None

        # LeaderScheduler 참조 (start_analysis_loop 호출 후 설정)
        self._scheduler = None

        # ML 추론 격벽 — 지연 초기화 (BulkheadRegistry import 비용 절감)
        self._ml_bulkhead: Any = None

    # ─────────────────────────────────────────────
    # 서브 모듈 초기화 및 수명주기
    # ─────────────────────────────────────────────

    def initialize(self) -> bool:
        """엔진 초기화 — 서브 모듈 생성 및 EventBus 구독 등록.

        Returns:
            초기화 성공 여부. enabled=False 시 False 반환.
        """
        if not self._engine_settings.enabled:
            logger.info("[CorrelationEngine] Disabled by settings")
            return False

        if self._initialized:
            return True

        try:
            # 1. Sub-module 초기화
            self._co_occurrence = CoOccurrenceTracker(self._settings)
            self._correlation_strategy = self._co_occurrence

            from selfhealing.services.correlation_engine.event_graph_builder import (
                EventGraphBuilder,
            )

            self._graph_builder = EventGraphBuilder(
                blast_radius_service=self._get_blast_radius_service(),
                co_occurrence_tracker=self._co_occurrence,
                max_events_per_dag=self._settings.max_events_per_dag,
                min_confidence=self._settings.min_confidence,
                max_graph_depth=self._settings.max_graph_depth,
            )
            self._root_cause_ranker = RootCauseRanker()
            self._root_cause_strategy = self._root_cause_ranker

            from selfhealing.services.correlation_engine.incident_timeline import (
                IncidentTimelineBuilder,
            )

            self._timeline_builder = IncidentTimelineBuilder()

            # 2. 상태 복원 (Cold Start 방지)
            if self._engine_settings.state_persistence_enabled:
                self._co_occurrence.load_state()

            # 3. Wildcard Observer 등록
            from selfhealing.services.correlation_engine.wildcard_observer import (
                WildcardObserver,
            )

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

    def start_analysis_loop(self) -> None:
        """주기적 분석 루프 시작 — LeaderScheduler 기반.

        다중 프로세스 환경에서 리더 노드 1개만 분석 루프를 실행한다.
        """
        from selfhealing.coordination.scheduler import get_leader_scheduler

        scheduler = get_leader_scheduler("correlation-engine")
        scheduler.add_job(
            name="correlation_periodic_analysis",
            func=self._run_periodic_analysis,
            interval_seconds=self._engine_settings.analysis_interval_seconds,
        )
        scheduler.start()
        self._scheduler = scheduler
        self._running = True

    def shutdown(self) -> None:
        """엔진 종료 — 분석 루프 중단, EventBus 구독 해제, 상태 영속화."""
        # 1. LeaderScheduler 분석 루프 종료
        if self._scheduler is not None:
            try:
                self._scheduler.stop()
            except Exception as e:
                logger.debug(f"[CorrelationEngine] Scheduler stop error: {e}")
            self._scheduler = None

        self._running = False

        # 2. Wildcard Observer 구독 해제
        if self._observer is not None:
            try:
                self._observer.unregister(self._get_event_bus())
            except Exception as e:
                logger.debug(f"[CorrelationEngine] Observer unregister error: {e}")

        # 3. 상태 영속화
        if self._engine_settings.state_persistence_enabled and self._co_occurrence is not None:
            self._co_occurrence.save_state()

        # 4. ML 전략 Lifecycle teardown
        self._shutdown_ml_strategies()

        self._initialized = False
        logger.info("[CorrelationEngine] Shut down")

    def _shutdown_ml_strategies(self) -> None:
        """ML 전략의 StrategyLifecycle.teardown()을 호출한다."""
        strategies: list[tuple[str, Any]] = [
            ("correlation", self._correlation_strategy),
            ("root_cause", self._root_cause_strategy),
        ]
        if self._graph_strategy is not None:
            strategies.append(("graph_build", self._graph_strategy))

        for name, strategy in strategies:
            if isinstance(strategy, StrategyLifecycle):
                logger.info(f"[CorrelationEngine] Tearing down ML strategy: {name}")
                strategy.teardown()

    # ─────────────────────────────────────────────
    # 주기적 분석 틱
    # ─────────────────────────────────────────────

    def _run_periodic_analysis(self) -> None:
        """주기적 분석 실행 — LeaderScheduler job으로 등록됨.

        Self-Fencing: lease 만료 시 분석 즉시 중단.
        """
        if not self._co_occurrence:
            return

        # Self-Fencing: 장기 분석 중 lease 만료 감지
        try:
            from selfhealing.coordination.factory import get_leader_elector

            elector = get_leader_elector("correlation-engine")
            if not elector.is_lease_valid():
                logger.warning("[CorrelationEngine] Lease expired, aborting analysis")
                return
        except Exception:
            pass  # elector 미사용 환경에서는 무시

        # 1. 이벤트 발생률 이상 체크
        if self._observer is not None:
            rate_anomaly = self._observer.check_event_rate_anomaly()
            if rate_anomaly:
                logger.warning(f"[CorrelationEngine] {rate_anomaly['message']}")

        # 2. Co-occurrence 분석
        correlation_results = self._co_occurrence.analyze_tick()

        # 3. 새로운 상관관계 발견 시 → Learning에 축적
        if correlation_results and self._engine_settings.learning_integration_enabled:
            self._report_to_learning(correlation_results)

        # 4. 상태 영속화 (5틱마다)
        if self._engine_settings.state_persistence_enabled:
            self._tick_count += 1
            if self._tick_count % 5 == 0:
                self._co_occurrence.save_state()

    # ─────────────────────────────────────────────
    # 온디맨드 분석 (인시던트 발생 시)
    # ─────────────────────────────────────────────

    def analyze_incident(
        self,
        incident_id: str | None = None,
        window_seconds: float | None = None,
    ) -> dict | None:
        """현재 시간 윈도우의 인시던트 분석 (온디맨드).

        Args:
            incident_id: 분석 대상 인시던트 ID.
                         None이면 DAG Root Node 기반 결정론적 ID를 자동 생성한다.
            window_seconds: 분석 윈도우 (None이면 settings 기본값)

        Returns:
            분석 결과 dict 또는 이벤트 부족 시 None.
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

            # 3. Incident ID 확정 (Deterministic Fallback)
            resolved_id = incident_id or self._generate_deterministic_incident_id(dag)

            # 4. 멱등성 검사 (IdempotencyService)
            if self._check_already_analyzed(resolved_id):
                logger.debug(f"[CorrelationEngine] Already analyzed: {resolved_id}")
                return None

            # 5. Root Cause 분석
            co_data = self._co_occurrence.analyze_tick() if self._co_occurrence else []
            root_cause = self.analyze_root_cause(dag, co_data)

            # 6. 타임라인 생성
            timeline = self._timeline_builder.build(dag, root_cause)

            # 7. Postmortem 연동 (incident_id 기반 정밀 타겟팅)
            if self._engine_settings.postmortem_integration_enabled:
                self._inject_to_postmortem(resolved_id, timeline, root_cause)

            # 8. 분석 완료 마킹
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

    # ─────────────────────────────────────────────
    # Deterministic Incident ID 생성
    # ─────────────────────────────────────────────

    @staticmethod
    def _generate_deterministic_incident_id(dag: EventDAG) -> str:
        """DAG Root Node 기반 결정론적 인시던트 ID 생성.

        동일 인시던트(동일 root event + 동일 시간 윈도우)에 대해
        항상 동일한 ID를 반환한다.

        양자화 단위: 60초 (1분)
        """
        if dag.root_nodes:
            root = dag.root_nodes[0]
            quantized_ts = int(root.timestamp // 60)
            content = f"{root.event_type}:{root.service_name}:{quantized_ts}"
        else:
            quantized_ts = int(dag.window_start // 60)
            content = f"no_root:{quantized_ts}"

        digest = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"corr_{digest}"

    # ─────────────────────────────────────────────
    # EventBus 연동
    # ─────────────────────────────────────────────

    @staticmethod
    def _get_event_bus():
        """EventBus 싱글톤 인스턴스 조회."""
        from selfhealing.services.event_bus.bus import get_event_bus

        return get_event_bus()

    def _register_event_handlers(self) -> None:
        """분석 트리거 이벤트 등록."""
        try:
            from selfhealing.services.event_bus.bus import (
                EventPriority,
                EventType,
            )

            bus = self._get_event_bus()

            bus.subscribe(
                EventType.CIRCUIT_BREAKER_CLOSED,
                self._on_incident_resolved,
                priority=EventPriority.LOW,
            )
            bus.subscribe(
                EventType.EMERGENCY_RECOVERY_COMPLETED,
                self._on_incident_resolved,
                priority=EventPriority.LOW,
            )
        except Exception as e:
            logger.debug(f"[CorrelationEngine] Event handler registration failed: {e}")

    def _on_incident_resolved(self, event) -> None:
        """인시던트 해소 시 자동 분석.

        event.data에서 incident_id를 추출하여 정밀 타겟팅하고,
        IdempotencyService로 중복 분석을 차단한다.
        """
        try:
            incident_id = event.data.get("incident_id") if hasattr(event, "data") and isinstance(event.data, dict) else None

            result = self.analyze_incident(incident_id=incident_id)
            if result:
                logger.info(
                    f"[CorrelationEngine] Auto-analysis complete: "
                    f"incident={result['incident_id']}, "
                    f"root_cause="
                    f"{result['root_cause'].primary_cause.event_node.service_name} "
                    f"({result['root_cause'].primary_cause.score:.0%})"
                )
        except Exception as e:
            logger.debug(f"[CorrelationEngine] Auto-analysis skipped: {e}")

    # ─────────────────────────────────────────────
    # Postmortem 연동
    # ─────────────────────────────────────────────

    def _inject_to_postmortem(
        self,
        incident_id: str,
        timeline,
        root_cause: RootCauseAnalysis,
    ) -> None:
        """Postmortem 데이터에 상관관계 분석 결과 주입.

        incident_id 기반 정밀 타겟팅으로 올바른 인시던트에만 주입한다.
        """
        try:
            from selfhealing.services.postmortem.store import (
                get_incident_by_id,
                update_incident_fields,
            )

            incident = get_incident_by_id(incident_id)
            if not incident:
                logger.debug(f"[CorrelationEngine] Postmortem not found: {incident_id}, " f"skipping injection")
                return

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

    # ─────────────────────────────────────────────
    # Learning 연동
    # ─────────────────────────────────────────────

    def _report_to_learning(self, results: list[CorrelationResult]) -> None:
        """발견된 상관관계를 LearningService에 축적."""
        try:
            from selfhealing.services.learning import (
                LearningService,
                PatternType,
            )

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

    # ─────────────────────────────────────────────
    # BlastRadius 연동
    # ─────────────────────────────────────────────

    @staticmethod
    def _get_blast_radius_service():
        """BlastRadiusService 인스턴스 조회 (없으면 None → 시간순 DAG만 사용)."""
        try:
            from selfhealing.services.blast_radius.service import (
                BlastRadiusService,
            )

            return BlastRadiusService.get_instance()
        except Exception:
            return None

    # ─────────────────────────────────────────────
    # 멱등성 (IdempotencyService)
    # ─────────────────────────────────────────────

    @staticmethod
    def _check_already_analyzed(incident_id: str) -> bool:
        """이 인시던트가 이미 분석되었는지 확인한다.

        IdempotencyService의 Cache(Redis) → DB 2단 조회를 사용하여
        프로세스/Pod 간 완전한 멱등성을 보장한다.
        """
        try:
            from selfhealing.services.idempotency.models import (
                IdempotencyDomain,
                IdempotencyKey,
            )
            from selfhealing.services.idempotency.service import (
                IdempotencyService,
            )

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
            return False  # Fail-Open: 장애 시 분석 허용

    @staticmethod
    def _mark_analysis_complete(incident_id: str) -> None:
        """분석 완료를 마킹한다."""
        try:
            from selfhealing.services.idempotency.models import (
                IdempotencyDomain,
                IdempotencyKey,
            )
            from selfhealing.services.idempotency.service import (
                IdempotencyService,
            )

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

    # ─────────────────────────────────────────────
    # 동적 설정 리로드 (RuntimeConfigManager)
    # ─────────────────────────────────────────────

    def _subscribe_config_updates(self) -> None:
        """RuntimeConfigManager 설정 변경 이벤트 구독.

        BulkheadRegistry._subscribe_config_updates() 패턴과 동일.
        """
        try:
            from selfhealing.services.event_bus.bus import (
                EventPriority,
                EventType,
            )

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
        - max_tracked_pairs: CoOccurrenceTracker 내부 dict 크기 제한
        - count_history_size: deque maxlen 리사이징
        - zscore_threshold: 즉시 반영 (다음 analyze_tick에서 적용)
        - enabled: False → 분석 루프 중단 + EventBus 구독 해제
        """
        config_type = event.data.get("config_type", "") if hasattr(event, "data") and isinstance(event.data, dict) else ""
        if config_type != "correlation":
            return

        try:
            from selfhealing.settings.correlation import (
                reset_correlation_settings,
            )
            from selfhealing.settings.correlation_engine import (
                reset_correlation_engine_settings,
            )

            # 1. Settings 캐시 무효화 → 새 값으로 재생성
            reset_correlation_settings()
            reset_correlation_engine_settings()
            self._settings = get_correlation_settings()
            self._engine_settings = get_correlation_engine_settings()

            # 2. CoOccurrenceTracker 내부 데이터 구조 리사이징
            if self._co_occurrence is not None:
                self._co_occurrence.resize(
                    max_tracked_pairs=self._settings.max_tracked_pairs,
                    count_history_size=self._settings.count_history_size,
                )

            # 3. enabled=False 전환 시 엔진 중단
            if not self._engine_settings.enabled:
                self.shutdown()
                logger.info("[CorrelationEngine] Disabled via dynamic config")
                return

            logger.info(
                f"[CorrelationEngine] Config reloaded: "
                f"zscore={self._settings.zscore_threshold}, "
                f"window={self._settings.window_seconds}s"
            )

        except Exception as e:
            logger.error(f"[CorrelationEngine] Config reload failed: {e}")

    # ─────────────────────────────────────────────
    # 전략 교체 API
    # ─────────────────────────────────────────────

    def set_correlation_strategy(self, strategy: CorrelationStrategy) -> None:
        """상관관계 분석 전략 교체."""
        if not isinstance(strategy, CorrelationStrategy):
            raise TypeError(f"CorrelationStrategy Protocol을 구현해야 합니다: " f"{type(strategy).__name__}")
        self._correlation_strategy = strategy

    def set_root_cause_strategy(
        self,
        primary: RootCauseStrategy,
        fallback: RootCauseStrategy | None = None,
    ) -> None:
        """근본 원인 분석 전략 교체.

        Args:
            primary: 주 전략
            fallback: 대체 전략 (None이면 기본 RootCauseRanker)
        """
        if not isinstance(primary, RootCauseStrategy):
            raise TypeError(f"RootCauseStrategy Protocol을 구현해야 합니다: " f"{type(primary).__name__}")
        self._root_cause_strategy = primary
        self._root_cause_fallback = fallback or RootCauseRanker()
        self._primary_strategy_name = type(primary).__name__
        self._fallback_strategy_name = type(self._root_cause_fallback).__name__

    def set_graph_strategy(self, strategy: GraphBuildStrategy) -> None:
        """DAG 구축 전략 교체."""
        if not isinstance(strategy, GraphBuildStrategy):
            raise TypeError(f"GraphBuildStrategy Protocol을 구현해야 합니다: " f"{type(strategy).__name__}")
        self._graph_strategy = strategy

    # ─────────────────────────────────────────────
    # 전략 접근자
    # ─────────────────────────────────────────────

    @property
    def correlation_strategy(self) -> CorrelationStrategy:
        """현재 상관관계 분석 전략."""
        return self._correlation_strategy

    @property
    def root_cause_strategy(self) -> RootCauseStrategy:
        """현재 근본 원인 분석 전략."""
        return self._root_cause_strategy

    @property
    def graph_strategy(self) -> GraphBuildStrategy | None:
        """현재 DAG 구축 전략."""
        return self._graph_strategy

    # ─────────────────────────────────────────────
    # ML Bulkhead 격리 실행
    # ─────────────────────────────────────────────

    def _get_ml_bulkhead(self) -> Any:
        """ML 추론 격벽을 지연 초기화하여 반환."""
        if self._ml_bulkhead is None:
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )
            from selfhealing.settings.bulkhead import get_bulkhead_settings

            bh_settings = get_bulkhead_settings()
            registry = get_bulkhead_registry()
            self._ml_bulkhead = registry.get_or_create(
                name="ml_inference",
                max_concurrent=bh_settings.ml_inference_max_workers,
                bulkhead_type="thread_pool",
            )
        return self._ml_bulkhead

    def _execute_with_ml_bulkhead(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """ML/LLM 추론을 격벽 내에서 실행.

        ThreadPoolBulkhead.execute()가 타임아웃 + 큐 제한을 처리한다.
        BulkheadFullError 발생 시 호출부에서 Fallback으로 전환한다.
        """
        from selfhealing.settings.bulkhead import get_bulkhead_settings

        timeout = get_bulkhead_settings().ml_inference_timeout
        bulkhead = self._get_ml_bulkhead()
        return bulkhead.execute(fn, *args, timeout=timeout, **kwargs)

    # ─────────────────────────────────────────────
    # 근본 원인 분석 (PolicyComposer Fallback 파이프라인)
    # ─────────────────────────────────────────────

    def analyze_root_cause(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        """PolicyComposer 기반 Fallback 파이프라인으로 근본 원인 분석.

        파이프라인 구성 (바깥 → 안쪽):
          BulkheadPolicy(ml_inference, timeout=30s)
            → FallbackPolicy(default_ranker)
              → primary_strategy.rank_causes()

        Args:
            dag: 이벤트 인과관계 그래프
            co_occurrence_data: 동시발생 통계 분석 결과

        Returns:
            근본 원인 분석 결과 (StrategyMetadata 포함)
        """
        from selfhealing.resilience.bulkhead.exceptions import (
            BulkheadFullError,
            BulkheadTimeoutError,
        )

        start_ms = time.monotonic() * 1000

        try:
            # ML Bulkhead 내에서 주 전략 실행
            result = self._execute_with_ml_bulkhead(
                self._root_cause_strategy.rank_causes,
                dag,
                co_occurrence_data,
            )
            duration_ms = time.monotonic() * 1000 - start_ms

            # 성공 — 전략 메타데이터 첨부
            result.strategy_metadata = StrategyMetadata(
                strategy_name=self._primary_strategy_name,
                fallback_used=False,
                analysis_duration_ms=duration_ms,
            )
            return result

        except (BulkheadFullError, BulkheadTimeoutError, Exception) as e:
            # Fallback — FallbackPolicy.metadata 패턴 준수
            logger.warning(f"[CorrelationEngine] Primary strategy failed, " f"falling back: {type(e).__name__}: {e}")
            result = self._root_cause_fallback.rank_causes(dag, co_occurrence_data)
            duration_ms = time.monotonic() * 1000 - start_ms

            result.strategy_metadata = StrategyMetadata(
                strategy_name=self._fallback_strategy_name,
                fallback_used=True,
                fallback_reason=f"{type(e).__name__}: {str(e)[:200]}",
                primary_strategy_name=self._primary_strategy_name,
                analysis_duration_ms=duration_ms,
            )
            return result

    # ─────────────────────────────────────────────
    # 배치 이상 탐지 (BatchCapable 분기)
    # ─────────────────────────────────────────────

    @staticmethod
    def detect_anomalies(
        strategy: AnomalyDetectionStrategy,
        values: list[float],
        contexts: list[dict[str, Any]] | None = None,
    ) -> list[tuple[bool, float]]:
        """배치 가능 전략이면 배치 호출, 아니면 단건 루프.

        Args:
            strategy: 이상 탐지 전략
            values: 검사할 값 리스트
            contexts: 값별 메타데이터 리스트 (선택적)

        Returns:
            (is_anomalous, score) 튜플 리스트
        """
        if isinstance(strategy, BatchCapable):
            return strategy.detect_batch(values, contexts)
        ctx_list = contexts or [None] * len(values)
        return [strategy.detect(v, c) for v, c in zip(values, ctx_list)]

    # ─────────────────────────────────────────────
    # 라이프사이클 관리 (ML 전략)
    # ─────────────────────────────────────────────

    def startup(self) -> None:
        """앱 시작 시 1회 호출 — Django AppConfig.ready()에서.

        순서: initialize() → warmup() → is_ready() 검증.
        """
        strategies: list[tuple[str, Any]] = [
            ("correlation", self._correlation_strategy),
            ("root_cause", self._root_cause_strategy),
        ]
        if self._graph_strategy is not None:
            strategies.append(("graph_build", self._graph_strategy))

        for name, strategy in strategies:
            if isinstance(strategy, StrategyLifecycle):
                logger.info(f"[CorrelationEngine] Initializing ML strategy: {name}")
                strategy.initialize()
                strategy.warmup()
                if not strategy.is_ready():
                    logger.error(f"[CorrelationEngine] Strategy '{name}' " f"failed readiness check")

    def get_ml_strategies(self) -> list[tuple[str, Any]]:
        """등록된 ML 전략 목록.

        HealthCheckService.get_readiness()에서 ML 전략 readiness를
        확인하는 데 사용한다.

        Returns:
            (이름, 전략) 튜플 리스트
        """
        strategies: list[tuple[str, Any]] = [
            ("correlation", self._correlation_strategy),
            ("root_cause", self._root_cause_strategy),
        ]
        if self._graph_strategy is not None:
            strategies.append(("graph_build", self._graph_strategy))
        return strategies

    # ─────────────────────────────────────────────
    # 대시보드 / API 인터페이스
    # ─────────────────────────────────────────────

    def get_status(self) -> dict:
        """엔진 상태 조회 (대시보드/API용)."""
        return {
            "enabled": self._engine_settings.enabled,
            "initialized": self._initialized,
            "running": self._running,
            "observer_stats": (self._observer.get_statistics() if self._observer else None),
            "tracked_pairs": (len(self._co_occurrence._pair_detectors) if self._co_occurrence else 0),
            "settings": {
                "window_seconds": self._settings.window_seconds,
                "zscore_threshold": self._settings.zscore_threshold,
                "analysis_interval": self._engine_settings.analysis_interval_seconds,
            },
        }

    def get_top_correlations(self, limit: int = 10) -> list[dict]:
        """현재 상위 상관관계 (API용)."""
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
