# 258. 갭 분석 · 요구사항 매트릭스 · 확장 전략

> **Version**: 1.0.0
> **Created**: 2026-02-20
> **Status**: Approved
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Related**: [256_CORRELATION_ML_STRATEGY_INTERFACE.md](256_CORRELATION_ML_STRATEGY_INTERFACE.md)

---

## 0. 요약

이 문서는 Metric Correlation Engine의 **비즈니스 맥락**을 기록한다:
1. 기존 시스템의 구조적 갭 6가지와 각 갭의 해결 매핑
2. 요구사항 추적 매트릭스 (문서 ↔ 갭 ↔ 모듈)
3. "닫힌 완성형"이 아닌 "확장 가능한 프레임워크"로서의 설계 근거
4. AI/ML 확장 로드맵
5. 기존 시장 대비 포지셔닝

---

## 1. 구조적 갭 분석 (코드 검증 완료)

### 1.1 갭 → 해결 모듈 매핑

| # | 갭 | 코드 근거 | 해결 모듈 | 문서 |
|---|---|---------|----------|------|
| **Gap-1** | EventType 폐쇄성 — Wildcard 구독 없음 | `EventType(str, Enum)`, `publish()` 구독자 없으면 무시 | `wildcard_observer.py` — 42개 전체 구독 | [254](254_CORRELATION_WILDCARD_OBSERVER.md) |
| **Gap-2** | 이벤트 간 상관 분석 부재 — 시간 임계값(30/60초)만 사용 | `IncidentGroupManager.get_cascading_pattern()` | `co_occurrence_tracker.py` + `event_graph.py` | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md), [251](251_CORRELATION_EVENT_GRAPH.md) |
| **Gap-3** | Emergency Level 자율 결정 부재 | `activate_auto(level=)` 호출자가 레벨 명시 | `root_cause_ranker.py` — 자율적 원인 특정으로 의사결정 보조 | [253](253_CORRELATION_ROOT_CAUSE_RANKER.md) |
| **Gap-4** | 메트릭 자동 발견 부재 | `ingest_metric()` 명시 호출만 | `wildcard_observer.py` — 이벤트 빈도를 자동 수집 | [254](254_CORRELATION_WILDCARD_OBSERVER.md) |
| **Gap-5** | L3 이상 탐지 범위 한정 (`amount` 필드만) | `L3AnomalyDetector` | `co_occurrence_tracker.py` — 이벤트 빈도의 ZScore 이상 탐지 | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md) |
| **Gap-6** | Watchdog 행동 이상 미감지 | `_attempt_recovery()` 컴포넌트명 하드코딩 | `wildcard_observer.py` — 이벤트 발생률 이상 탐지로 간접 감시 | [254](254_CORRELATION_WILDCARD_OBSERVER.md) |

### 1.2 Gap-3 보충 설명

Correlation Engine은 Emergency Level을 **직접 결정하지 않는다**. 그러나 `RootCauseAnalysis`가 "DB Pool 고갈 87%"를 제공하면, Emergency Manager의 호출자가 **정보 기반 의사결정**을 할 수 있다. 이것은 Gap-3의 **완전 해결이 아닌 보조적 해결**이다.

완전 해결(자율적 Emergency Level 결정)은 별도의 "Emergency Decision Engine" 설계가 필요하며, Correlation Engine의 출력을 입력으로 사용하는 것이 자연스러운 확장 방향이다.

---

## 2. 요구사항 추적 매트릭스

### 2.1 기능 요구사항 (FR)

| ID | 요구사항 | 문서 | 모듈 | 우선순위 |
|----|---------|------|------|---------|
| FR-01 | 이벤트 발생 시간순 DAG 자동 구축 | [251](251_CORRELATION_EVENT_GRAPH.md) | `event_graph.py` | P1 |
| FR-02 | 서비스 의존성 × 시간순 교차로 인과 방향 추론 | [251](251_CORRELATION_EVENT_GRAPH.md) | `event_graph.py` | P1 |
| FR-03 | 동시발생 빈도의 통계적 이상 탐지 | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md) | `co_occurrence_tracker.py` | P1 |
| FR-04 | KScore 기반 이상치 판단 (기존 ZScoreDetector 재사용) | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md) | `co_occurrence_tracker.py` | P1 |
| FR-05 | 동시발생 빈도 트렌드 추적 (기존 HoltLinearForecaster 재사용) | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md) | `co_occurrence_tracker.py` | P2 |
| FR-06 | 근본 원인 확률 점수 산정 (4-factor 가중합) | [253](253_CORRELATION_ROOT_CAUSE_RANKER.md) | `root_cause_ranker.py` | P1 |
| FR-07 | 사람이 읽을 수 있는 Evidence 자동 생성 | [253](253_CORRELATION_ROOT_CAUSE_RANKER.md) | `root_cause_ranker.py` | P1 |
| FR-08 | 42개 EventType 전체 구독 관찰 | [254](254_CORRELATION_WILDCARD_OBSERVER.md) | `wildcard_observer.py` | P1 |
| FR-09 | 이벤트 발생률 이상 탐지 | [254](254_CORRELATION_WILDCARD_OBSERVER.md) | `wildcard_observer.py` | P2 |
| FR-10 | 인시던트 Phase 분류 (detection/escalation/recovery) | [255](255_CORRELATION_INCIDENT_TIMELINE.md) | `incident_timeline.py` | P1 |
| FR-11 | MTTD/MTTR 자동 산출 | [255](255_CORRELATION_INCIDENT_TIMELINE.md) | `incident_timeline.py` | P1 |
| FR-12 | 마크다운 타임라인 출력 | [255](255_CORRELATION_INCIDENT_TIMELINE.md) | `incident_timeline.py` | P2 |
| FR-13 | ML Strategy Protocol 정의 | [256](256_CORRELATION_ML_STRATEGY_INTERFACE.md) | `interfaces/ml_strategy.py` | P1 |
| FR-14 | 전략 런타임 교체 | [256](256_CORRELATION_ML_STRATEGY_INTERFACE.md), [257](257_CORRELATION_SERVICE_INTEGRATION.md) | `service.py` | P1 |
| FR-15 | Postmortem 자동 주입 | [257](257_CORRELATION_SERVICE_INTEGRATION.md) | `service.py` | P1 |
| FR-16 | LearningService 패턴 축적 | [257](257_CORRELATION_SERVICE_INTEGRATION.md) | `service.py` | P1 |
| FR-17 | CB CLOSED / Emergency Recovery 시 자동 분석 트리거 | [257](257_CORRELATION_SERVICE_INTEGRATION.md) | `service.py` | P1 |
| FR-18 | StateBackend 영속화 (Cold Start 방지) | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md), [257](257_CORRELATION_SERVICE_INTEGRATION.md) | `co_occurrence_tracker.py`, `service.py` | P1 |

### 2.2 비기능 요구사항 (NFR)

| ID | 요구사항 | 기준 | 문서 |
|----|---------|------|------|
| NFR-01 | 이벤트 처리 오버헤드 | ≤ 1ms per event | [254](254_CORRELATION_WILDCARD_OBSERVER.md) §6 (~17μs 달성) |
| NFR-02 | 메모리 사용량 | ≤ 10MB | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md) §7 (~2MB), [254](254_CORRELATION_WILDCARD_OBSERVER.md) §6.2 (~1.2MB) |
| NFR-03 | Fail-Safe | 엔진 오류 시 복원력 기능 영향 없음 | [250](250_CORRELATION_ENGINE_OVERVIEW.md) §2.1, [257](257_CORRELATION_SERVICE_INTEGRATION.md) |
| NFR-04 | 모듈 독립성 | 폴더 전체 제거 시 나머지 정상 | [250](250_CORRELATION_ENGINE_OVERVIEW.md) §2.1 |
| NFR-05 | Cold Start | 재시작 후 학습 상태 유지 | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md) §5 |
| NFR-06 | 멀티리전 | 리전별 독립 분석 | [250](250_CORRELATION_ENGINE_OVERVIEW.md) §7 |
| NFR-07 | Thread Safety | 동시 이벤트 처리 안전 | [254](254_CORRELATION_WILDCARD_OBSERVER.md) §5 |

---

## 3. "닫힌 완성형" vs "확장 가능한 프레임워크"

### 3.1 설계 결정: 확장 가능한 프레임워크

이 시스템은 **판매 후 구매자가 자체 확장할 수 있는 기반**을 제공하는 것이 목표이다.

| 계층 | Day-1 제공 | 구매자 확장 가능 |
|------|-----------|----------------|
| **이상 탐지** | ZScoreDetector, IQRDetector | `AnomalyDetectionStrategy` Protocol로 교체 |
| **상관 분석** | Co-occurrence + ZScore | `CorrelationStrategy` Protocol로 교체 |
| **근본 원인** | DAG 토폴로지 + 가중합 | `RootCauseStrategy` Protocol로 교체 |
| **DAG 구축** | BlastRadius × 시간순 | `GraphBuildStrategy` Protocol로 교체 |
| **시계열 예측** | HoltLinearForecaster | `ForecastStrategy` Protocol로 교체 |
| **분류** | SpikeClassifier (규칙 기반) | `ClassificationStrategy` Protocol로 교체 |

### 3.2 확장 진입점

구매자가 확장할 수 있는 구체적 진입점:

```python
# 1. 전략 교체 (코어 코드 수정 없음)
engine = CorrelationEngineService.get_instance()
engine.set_root_cause_strategy(MyCustomRanker())

# 2. 어댑터 등록 (코어 코드 수정 없음)
ProviderRegistry.register_cache("dynamodb", DynamoDBCacheAdapter)

# 3. EventBus 핸들러 추가 (코어 코드 수정 없음)
bus = get_event_bus()
bus.subscribe(EventType.CIRCUIT_BREAKER_OPENED, my_custom_handler)

# 4. Settings 오버라이드 (환경변수만)
# SELFHEALING_CORRELATION_ZSCORE_THRESHOLD=3.0

# 5. pyproject.toml extras (pip install)
# pip install selfhealing[ml]
```

---

## 4. AI/ML 확장 로드맵

### 4.1 현재 → ML-Ready → ML-Native 진화 경로

```
Phase 0 (현재)          Phase 1 (이번 구현)        Phase 2 (구매자/추후)
─────────────────       ──────────────────────     ──────────────────────
통계 기반 탐지           Strategy Interface 도입     ML 전략 플러그인
ZScore/IQR/Holt          + Protocol 정의             + Isolation Forest
                         + Adapter Wrapper           + Prophet
규칙 기반 분류            + Default 전략 제공          + LSTM
SpikeClassifier           + pyproject.toml [ml]       + Bayesian Network

키워드 기반 Root Cause   구조적 Root Cause           LLM 기반 Root Cause
if "db" in trigger       DAG + 가중합 점수            GPT/Claude API 연동

Postmortem 수동 가설      자동 Root Cause 순위        자연어 Postmortem 생성
```

### 4.2 ML 확장 시 변경 범위

| 구매자 행동 | 변경 범위 |
|------------|----------|
| `pip install selfhealing[ml]` | 의존성 추가만 |
| ML 전략 구현체 작성 | 1개 Python 파일 (Protocol 구현) |
| `set_*_strategy()` 호출 | AppConfig.ready()에 1줄 추가 |
| 설정 튜닝 | `.env` 파일 수정만 |

**코어 코드 수정: 0줄**

---

## 5. 경쟁 포지셔닝

### 5.1 이 시스템만의 차별점

현재 시장에 존재하는 제품들은 Correlation Engine의 **개별 기능**만 제공:

| 시장 제품 | 제공 기능 | 이 시스템 대비 |
|-----------|---------|---------------|
| PagerDuty Intelligent Triage | 인시던트 자동 분류 | Correlation Engine이 이 기능 + Root Cause 분석 + 타임라인 |
| Datadog Watchdog | 메트릭 이상 탐지 + 상관 분석 | 외부 SaaS 의존. 이 시스템은 내장 패키지 |
| Rootly / Blameless | Postmortem 자동화 | 타임라인만. Root Cause 분석 없음 |
| LinkedIn ThirdEye | 이상 탐지 + 차원 드릴다운 | 수십만 메트릭 환경 전용. ML 의존 |

**핵심 차별**: 이것들을 **하나의 `pip install` 패키지 안에서**, **순수 Python으로**, **ML 없이도 동작**하면서 **ML로 교체 가능한 구조**로 제공.

### 5.2 Self-Healing 전체 라이프사이클에서의 위치

```
감지                    차단              격리              복구              학습              보고
PredictiveForecaster → CircuitBreaker → Emergency/     → DLQ Replay /  → Learning      → Postmortem
                                        Bulkhead         Saga
     ↑                                                                       ↑               ↑
     │                                                                       │               │
     └──────────────── Correlation Engine ──────────────────────────────────┘               │
                       (인과관계 분석)                                                        │
                       (미지의 패턴 발견)                                                     │
                       (Root Cause 순위)  ──────────────────────────────────────────────────┘
                       (자동 타임라인)
```

Correlation Engine은 Self-Healing 루프의 **"두뇌" 역할** — 개별 서비스들이 독립 실행하는 것을 **전체적으로 관찰하고 의미를 부여**한다.

---

## 6. 파일 목록 체크리스트

### 6.1 신규 생성 파일

| 파일 경로 | 문서 | 상태 |
|-----------|------|------|
| `services/correlation_engine/__init__.py` | — | 구현 필요 |
| `services/correlation_engine/interfaces.py` | [256](256_CORRELATION_ML_STRATEGY_INTERFACE.md) | 구현 필요 |
| `services/correlation_engine/event_graph.py` | [251](251_CORRELATION_EVENT_GRAPH.md) | 구현 필요 |
| `services/correlation_engine/co_occurrence_tracker.py` | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md) | 구현 필요 |
| `services/correlation_engine/root_cause_ranker.py` | [253](253_CORRELATION_ROOT_CAUSE_RANKER.md) | 구현 필요 |
| `services/correlation_engine/wildcard_observer.py` | [254](254_CORRELATION_WILDCARD_OBSERVER.md) | 구현 필요 |
| `services/correlation_engine/incident_timeline.py` | [255](255_CORRELATION_INCIDENT_TIMELINE.md) | 구현 필요 |
| `services/correlation_engine/service.py` | [257](257_CORRELATION_SERVICE_INTEGRATION.md) | 구현 필요 |
| `settings/correlation_engine.py` | [257](257_CORRELATION_SERVICE_INTEGRATION.md) | 구현 필요 |
| `interfaces/ml_strategy.py` | [256](256_CORRELATION_ML_STRATEGY_INTERFACE.md) | 구현 필요 |

### 6.2 수정 파일 (선택)

| 파일 경로 | 변경 내용 | 필수 여부 |
|-----------|---------|----------|
| `factory.py` | `register_correlation_strategy()` 추가 | 선택 |
| `pyproject.toml` | `[ml]` extras 추가 | 선택 |
| `services/event_bus/bus/__init__.py` | `register_default_handlers()`에 Correlation 핸들러 추가 | 선택 (service.py에서 직접 등록 가능) |

### 6.3 테스트 파일

| 파일 경로 | 테스트 대상 |
|-----------|-----------|
| `tests/unit/correlation_engine/test_event_graph.py` | EventGraphBuilder |
| `tests/unit/correlation_engine/test_co_occurrence_tracker.py` | CoOccurrenceTracker |
| `tests/unit/correlation_engine/test_root_cause_ranker.py` | RootCauseRanker |
| `tests/unit/correlation_engine/test_wildcard_observer.py` | WildcardObserver |
| `tests/unit/correlation_engine/test_incident_timeline.py` | IncidentTimelineBuilder |
| `tests/unit/correlation_engine/test_service.py` | CorrelationEngineService |
| `tests/integration/test_correlation_engine_integration.py` | 전체 연동 |

---

## 7. 문서 시리즈 완전성 검증

| # | 주제 | 핵심 질문 | 답변 위치 |
|---|------|---------|----------|
| **아키텍처** | 전체 구조와 데이터 흐름은? | [250](250_CORRELATION_ENGINE_OVERVIEW.md) §3, §4 |
| **DAG 구축** | 인과관계 방향을 어떻게 추론하는가? | [251](251_CORRELATION_EVENT_GRAPH.md) §3 (4단계 Evidence) |
| **통계 분석** | Granger Causality 대신 무엇을? | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md) §1 (Co-occurrence + ZScore) |
| **Root Cause** | "87% 확률"을 어떻게 산출하는가? | [253](253_CORRELATION_ROOT_CAUSE_RANKER.md) §3 (4-factor 가중합) |
| **데이터 수집** | 미지의 패턴을 어떻게 발견하는가? | [254](254_CORRELATION_WILDCARD_OBSERVER.md) §0, §3 |
| **타임라인** | SRE 메트릭(MTTD/MTTR)을 어떻게 산출? | [255](255_CORRELATION_INCIDENT_TIMELINE.md) §5 |
| **확장성** | 구매자가 ML을 어떻게 연결하는가? | [256](256_CORRELATION_ML_STRATEGY_INTERFACE.md) §4, §5 |
| **연동** | 기존 4개 서비스와 어떻게 연결? | [257](257_CORRELATION_SERVICE_INTEGRATION.md) §3 |
| **비즈니스** | 왜 필요하고, 시장에서 차별점은? | [258](258_CORRELATION_GAP_ANALYSIS.md) §1, §5 |
| **설정** | 환경변수로 무엇을 튜닝할 수 있는가? | [257](257_CORRELATION_SERVICE_INTEGRATION.md) §1 |
| **테스트** | 무엇을 어떻게 테스트하는가? | 각 문서 마지막 섹션 |
| **성능** | 오버헤드와 메모리 사용량은? | [252](252_CORRELATION_CO_OCCURRENCE_TRACKER.md) §7, [254](254_CORRELATION_WILDCARD_OBSERVER.md) §6 |

→ **빠뜨린 영역 없음**. 모든 논의 주제가 문서화됨.
