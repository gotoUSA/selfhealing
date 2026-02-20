# 250. Metric Correlation Engine — 총괄 설계

> **Version**: 1.0.0
> **Created**: 2026-02-20
> **Status**: Approved
> **Parent**: 없음 (신규 서비스)
> **Related**: [238_PREDICTIVE_ANOMALY_FORECASTER.md](238_PREDICTIVE_ANOMALY_FORECASTER.md), [247_SAGA_ORCHESTRATOR_OVERVIEW.md](247_SAGA_ORCHESTRATOR_OVERVIEW.md)
> **Sub-documents**: 251–258
> **Priority**: P1 — "Unknown Unknowns" 대응 능력의 핵심 빈 구간

---

## 0. 요약

현재 Self-Healing 시스템은 **42개 EventType이 독립적으로 발생·처리**되며, 이벤트 간 인과관계를 자동으로 파악하지 못한다.
"DB Pool 고갈 → Error Rate 증가 → Circuit Open → Emergency Mode"와 같은 연쇄 패턴이나, **사전 정의되지 않은 미지의 상관관계**를 감지할 수 없다.

이 문서 시리즈는 `services/correlation_engine/` 모듈을 설계하여:
1. 이벤트 발생 시간순 DAG(방향성 비순환 그래프) 자동 구축
2. 동시 발생 패턴의 통계적 이상 탐지
3. 근본 원인 자동 순위 산정
4. 인시던트 타임라인 자동 생성
5. AI/ML 전략 교체를 위한 확장 인터페이스

를 구현한다.

---

## 1. 문제 정의 — 코드 근거

### 1.1 현재 시스템의 6가지 구조적 갭

| # | 갭 | 코드 근거 | 영향 |
|---|---|---------|------|
| **Gap-1** | EventType 폐쇄성 | `class EventType(str, Enum)` — [bus/\_\_init\_\_.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus/bus/__init__.py) L60-195. Enum은 상속으로 멤버 추가 불가 | 구매자가 커스텀 이벤트를 추가할 수 없음 |
| **Gap-2** | 이벤트 간 상관 분석 부재 | `IncidentGroupManager.get_cascading_pattern()` — [incident_group.py](../../packages/selfhealing-python/src/selfhealing/services/postmortem/incident_group.py) L166-203. 시간 임계값(30초/60초)만 사용 | 서비스 의존성 기반 인과관계 추론 불가 |
| **Gap-3** | Emergency Level 자율 결정 부재 | `activate_auto(level=)` — [manager.py](../../packages/selfhealing-python/src/selfhealing/services/emergency_mode/manager.py) L453-520. 호출자가 레벨을 명시 지정 | 메트릭 기반 자율 판단 로직 없음 |
| **Gap-4** | 메트릭 자동 발견 부재 | `ingest_metric()` 명시 호출 — [service.py](../../packages/selfhealing-python/src/selfhealing/services/predictive_forecaster/service.py). 새 메트릭 자동 수집 안 됨 | 미등록 메트릭의 이상 감지 불가 |
| **Gap-5** | L3 이상 탐지 범위 한정 | `L3AnomalyDetector` — [validators.py](../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/validators.py). `amount` 필드만 검사 | 이벤트 빈도/시퀀스 이상 범위 밖 |
| **Gap-6** | Watchdog 행동 이상 미감지 | [watchdog.py](../../packages/selfhealing-python/src/selfhealing/meta/watchdog.py) L262-270. 컴포넌트 생존 여부만 확인 | "조용한 장애" 감지 불가 |

### 1.2 EventBus 현재 아키텍처 확인

코드 분석 결과 확인된 사실:

- **모든 핸들러는 Pub/Sub 패턴** — 핸들러 제거/추가가 다른 핸들러에 영향 없음
- **이벤트 체인 깊이 = 1** — 어떤 핸들러도 추가 이벤트를 `publish()`하지 않음 (리프 노드)
- **유일한 타이트 결합**: IntegrityGate ↔ Replay (`event.data["integrity_failed"]` 변이로 통신)
- **Catch-All / Wildcard 구독 메커니즘 없음** — 구독자 없는 이벤트는 `logger.debug`로 조용히 무시

→ Correlation Engine은 이 아키텍처의 **소비자(Consumer)** 로만 동작하며, 기존 핸들러 체인에 간섭하지 않는다.

---

## 2. 설계 원칙

### 2.1 프로젝트 전체 설계 원칙 준수

| 원칙 | Correlation Engine 적용 |
|------|----------------------|
| **Pure Python — no framework dependencies** | numpy/scipy 의존 없음. 기존 `ZScoreDetector`, `IQRDetector`, `HoltLinearForecaster` 재사용 |
| **Plug & Play** | `CorrelationStrategy` ABC를 통해 알고리즘 교체 가능. 코어 수정 없이 ML 전략 연결 |
| **모듈 독립성** | `correlation_engine/` 폴더 전체를 제거해도 나머지 시스템 정상 동작 |
| **Fail-Safe** | 엔진 오류 시 분석 결과만 누락, 복원력 기능(CB, Throttle 등)에 영향 없음 |
| **Settings 패턴** | `CorrelationEngineSettings(BaseSettings)` + `env_prefix="SELFHEALING_CORRELATION_"` |
| **StateBackend 영속화** | Cold Start 방지 — 학습된 패턴 영속화 |

### 2.2 기존 인프라 최대 재사용

| 기존 모듈 | 재사용 방식 |
|-----------|-----------|
| `BlastRadiusService.get_dependencies()` | 서비스 의존성 그래프 → DAG 구축 시 인과 방향성 추론 |
| `ZScoreDetector` / `IQRDetector` | 동시 발생 빈도의 이상치 판단 |
| `HoltLinearForecaster` | 상관 강도의 시간적 트렌드 추적 |
| `LearningService.learn_pattern()` | 발견된 상관 패턴 축적 (3회+ 반복 시 자동 제안) |
| `SnapshotBuilder` / `build_timeline()` | 타임라인 데이터 수집 기반 |
| `EventBus.subscribe()` / `get_history()` | 이벤트 스트림 수집 |
| `StateBackend` | 학습 상태 영속화 |
| `ProviderRegistry.get_cache()` | 분석 결과 캐싱 |

### 2.3 확장 가능한 프레임워크로서의 설계

이 엔진은 **"닫힌 완성 엔진"이 아닌 "확장 가능한 분석 프레임워크"**:

```
기본 제공 (Day-1):
├── co_occurrence_tracker.py  ← ZScore 기반 동시발생 이상 탐지
├── event_graph.py            ← BlastRadius + 시간순 DAG
└── root_cause_ranker.py      ← 진입차수 + 시간 가중 점수

구매자가 확장 가능:
├── CorrelationStrategy 구현체 추가 (Granger, Bayesian, ML 등)
├── RootCauseStrategy 구현체 추가 (LLM 기반 등)
├── AnomalyDetectionStrategy 구현체 추가 (Isolation Forest 등)
└── ForecastStrategy 구현체 추가 (Prophet, LSTM 등)
```

---

## 3. 모듈 구조

```
services/
  correlation_engine/
    __init__.py
    interfaces.py              # Strategy ABC/Protocol 정의 (251번 이전, 256번에서 상세)
    event_graph.py             # 이벤트 DAG 자동 구축 (251)
    co_occurrence_tracker.py   # 동시발생 빈도 통계 추적 (252)
    root_cause_ranker.py       # 근본 원인 순위 산정 (253)
    wildcard_observer.py       # EventBus 전체 이벤트 구독 관찰자 (254)
    incident_timeline.py       # 인시던트 타임라인 자동 생성 (255)
    service.py                 # Orchestrator (257)
settings/
    correlation_engine.py      # Pydantic BaseSettings (257)
interfaces/
    ml_strategy.py             # AI/ML Strategy Protocol (256)
```

---

## 4. 데이터 흐름

```
┌──────────────────────────────────────────────────────────────────┐
│                         EventBus                                  │
│  CB_OPENED · EMERGENCY_LEVEL_CHANGED · ERROR_BUDGET_CRITICAL · … │
└───────────┬──────────────────────────────────────────────────────┘
            │ (42개 EventType 전부 구독)
            ▼
┌─────────────────────────────┐
│   wildcard_observer.py      │  ← Gap-1 해결: 전체 이벤트 관찰
│   시간 윈도우 내 이벤트 버퍼 │
└───────────┬─────────────────┘
            │
     ┌──────┴──────┐
     ▼             ▼
┌──────────┐  ┌──────────────────┐
│ event    │  │ co_occurrence    │  ← Gap-2,5 해결: 통계적 상관 분석
│ _graph   │  │ _tracker         │
│ .py      │  │ .py              │
│          │  │                  │
│ Blast    │  │ ZScoreDetector로 │
│ Radius   │  │ 이상 빈도 탐지   │
│ × 시간순 │  │                  │
│ → DAG    │  └────────┬─────────┘
└────┬─────┘           │
     │                 │
     └────────┬────────┘
              ▼
┌─────────────────────────────┐
│   root_cause_ranker.py      │  ← Gap-3 해결 보조: 자율적 원인 특정
│   DAG 진입차수 + 시간순     │
│   + 의존성 깊이 가중 점수   │
│   → "Pool 고갈 87% 확률"   │
└───────────┬─────────────────┘
            │
     ┌──────┴──────┐
     ▼             ▼
┌──────────┐  ┌──────────────────┐
│incident  │  │ LearningService  │  ← Gap-4 해결 보조: 패턴 축적
│_timeline │  │ .learn_pattern() │
│.py       │  │ → 3회+ 반복 시   │
│          │  │   자동 제안 생성  │
│→ Postmor │  └──────────────────┘
│  tem 주입│
└──────────┘
```

---

## 5. 기존 인과 체인과의 관계

### 5.1 "선형 체인"은 물리 현상이지, 코드 결합이 아님

"DB Pool 고갈 → Error Rate ↑ → CB OPEN → Emergency"는 **운영 환경에서 물리적으로 발생하는 인과 관계**이다.
현재 코드는 각각을 **독립적인 Pub/Sub 핸들러**로 처리하며, 핸들러 간 직접 호출이 없다.

```
CB_OPENED ──┬── _on_circuit_breaker_opened_notify    (제거 가능)
            ├── _on_circuit_breaker_opened_throttle   (제거 가능)
            └── _on_circuit_breaker_opened_snapshot    (제거 가능)
```

Correlation Engine은 이 독립성을 유지하면서, **사후적으로 "이 세 이벤트가 동시에 발생했다"**는 패턴을 분석한다.

### 5.2 알려진 체인 vs 미지의 상관관계

| 유형 | 예시 | 현재 대응 | Correlation Engine 역할 |
|------|------|----------|----------------------|
| **알려진 체인** | CB OPEN → Throttle 강등 | ✅ 핸들러로 대응 중 | 시각화 + 타임라인 자동화 |
| **알려진-미연결** | 동시 다발 CB OPEN → 인프라 문제 추정 | ⚠️ `IncidentGroupManager`이 시간 근접성만 판단 | DAG + 의존성 그래프로 인과 방향성 추론 |
| **미지의 상관관계** | `THROTTLE_LIMIT_CHANGED` + `SAGA_TIMED_OUT` 빈번 동시 발생 | ❌ 감지 불가 | `co_occurrence_tracker`가 통계적 이상 탐지 |

---

## 6. 서브 문서 맵

| 문서 번호 | 제목 | 핵심 내용 |
|----------|------|----------|
| **251** | Event Graph — DAG 자동 구축 | `event_graph.py` 상세 설계. BlastRadius × 시간순 교차 알고리즘 |
| **252** | Co-occurrence Tracker — 동시발생 이상 탐지 | `co_occurrence_tracker.py` 상세 설계. ZScore 기반 이벤트 쌍 빈도 추적 |
| **253** | Root Cause Ranker — 근본 원인 순위 산정 | `root_cause_ranker.py` 상세 설계. DAG 진입차수 + 시간 가중 점수 |
| **254** | Wildcard Observer — 전체 이벤트 관찰자 | `wildcard_observer.py` 상세 설계. EventBus 42개 타입 전체 구독 |
| **255** | Incident Timeline — 통합 타임라인 생성 | `incident_timeline.py` 상세 설계. Postmortem 자동 주입 |
| **256** | ML Strategy Interface — AI/ML 확장 기반 | `interfaces/ml_strategy.py` + `interfaces.py`. 전략 교체 프로토콜 |
| **257** | 기존 시스템 연동 및 서비스 오케스트레이터 | `service.py` + `settings/correlation_engine.py`. 4개 서비스와의 연동 상세 |
| **258** | 갭 분석 · 요구사항 매트릭스 · 시장 포지셔닝 | 비즈니스 컨텍스트. 경쟁 분석. 확장 가능 프레임워크로서의 가치 |

---

## 7. 비기능 요구사항

| 항목 | 요구사항 | 구현 방식 |
|------|---------|----------|
| **성능** | 이벤트 처리 ≤ 1ms 오버헤드 | Observer는 버퍼링만 수행, 분석은 비동기 |
| **메모리** | 이벤트 버퍼 ≤ 10MB | `maxlen` 제한 `deque` + LRU 캐시 |
| **Cold Start** | 재시작 후 학습 상태 유지 | `StateBackend` 영속화 |
| **Fail-Safe** | 엔진 오류 시 복원력 기능 영향 없음 | 전체 `try/except` 래핑, 독립 모듈 |
| **테스트** | 단위 + 통합 + 시나리오 | 기존 테스트 피라미드 준수 |
| **멀티리전** | 리전별 독립 분석 | `ReplicationFilter`에서 제외 (로컬 전용) |
