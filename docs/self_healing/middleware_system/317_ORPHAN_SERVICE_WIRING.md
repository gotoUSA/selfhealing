# 317. Orphan Service Wiring — 미연결 서비스 식별 및 연결 계획

> **Status**: Planned
> **Severity**: P1 (HIGH) — 빌드된 기능이 런타임에 활성화되지 않음
> **Target**: `services/`, `adapters/django/apps.py`, `celery_tasks/`, `myproject/celery.py`
> **References**:
> - 318 — Wiring Verification CI (자동 검증)
> - 75 — Crisis Budget Multiplier (domain_tag 데코레이터)
> - 299 — Config Shadow Evaluator (event_journal 의존)

---

## 1. 현황 및 문제

### 1.1 서비스 와이어링 현황

51개 서비스 중 **12개가 엔트리포인트에 미연결 또는 불완전 연결** 상태다.

엔트리포인트란: Django Middleware, Celery Beat, Django AppConfig.ready(), Celery Signal Hook, Django Signal, API View 중 하나 이상에서 import/호출되는 것을 의미한다.

| 구분 | 서비스 수 | 비율 |
|------|----------|------|
| 완전 연결 | 39 | 76% |
| 간접/부분 연결 | 7 | 14% |
| **완전 미연결 (고아)** | **5** | **10%** |

### 1.2 엔트리포인트 유형별 와이어링 수

| 엔트리포인트 | 활성 수 | 연결 서비스 카테고리 |
|-------------|---------|---------------------|
| Django Middleware | 14개 | CB, DLQ, Security, Rate Limit, Scaling, Audit |
| Celery Beat Tasks | 15+ | CB Recovery, Chaos, Metrics, DLQ Cleanup, Recovery |
| AppConfig.ready() | 11개 | Audit, Meta-Watchdog, Precomputed Cache, Hash Chain |
| Celery Signal Hooks | 6개 | CB, DLQ, Forensics, Metrics |
| Django Signals | 3개 | RBAC, Session Management |
| API Views | 63+ | 대부분의 서비스 |
| Management Commands | 4개 | Config, Alerts, Security |

---

## 2. 완전 미연결 서비스 (고아) — 5개

### 2.1 correlation_engine

**기능**: 이벤트 인과관계 분석 엔진. DAG 기반 근본 원인 분석, 인시던트 타임라인 생성.

**코드 위치**: `services/correlation_engine/`

**공개 API**:
```python
CorrelationEngineService.get_instance()
    .initialize()                     # 서브모듈 초기화 + EventBus 구독
    .start_analysis_loop()            # LeaderScheduler 주기적 분석 시작
    .analyze_incident(incident_id)    # 온디맨드 분석
    .shutdown()
```

**내부 의존성** (이미 와이어링된 서비스):
- EventBus (CIRCUIT_BREAKER_CLOSED, EMERGENCY_RECOVERY_COMPLETED 구독)
- Postmortem (타임라인 + 근본 원인 분석 결과 주입)
- Learning (발견된 상관관계 패턴 학습)
- BlastRadius (서비스 의존성 그래프)
- IdempotencyService (중복 분석 방지)
- LeaderScheduler (멀티노드 환경 리더만 분석)

**연결 계획**:

| 엔트리포인트 | 위치 | 내용 |
|-------------|------|------|
| AppConfig.ready() | `adapters/django/apps.py` | `CorrelationEngineService.get_instance().initialize()` |
| AppConfig.ready() | `adapters/django/apps.py` | `CorrelationEngineService.get_instance().start_analysis_loop()` |

**Feature Flag**: `SELFHEALING_CORRELATION_ENGINE_ENABLED` (default: False)

**우선순위**: P2 — Postmortem 품질 향상에 직결

---

### 2.2 execution_services (Chaos + ConfigApply)

**기능**: 예약된 Chaos 실험 실행 + 지연 설정 변경 적용. GovernanceCheckMixin으로 Kill Switch, ErrorBudget, EmergencyMode 사전 검증.

**코드 위치**: `services/execution_services/`

**공개 API**:
```python
# Chaos
get_chaos_execution_service()
    .run_scheduled_experiments()      # 예정된 실험 실행
    .generate_daily_report()          # 리질리언스 리포트

# ConfigApply
get_config_apply_service()
    .apply_pending_changes()          # 예정된 설정 변경 적용
```

**현재 상태**: Celery Task 래퍼가 `tasks/chaos_scheduler.py`, `tasks/config_apply.py`에 존재하지만 Beat 스케줄에 미등록.

**연결 계획**:

| 엔트리포인트 | 위치 | 내용 |
|-------------|------|------|
| Celery Beat | `myproject/celery.py` | `run_scheduled_experiments` (5분 간격) |
| Celery Beat | `myproject/celery.py` | `apply_pending_config_changes` (30초 간격) |

**우선순위**: P1 — Chaos 엔진과 설정 관리의 핵심 실행 경로

---

### 2.3 isolation (Regional Isolation Gate)

**기능**: 리전 단위 트래픽 차단. Redis TTL 기반 격리 상태 관리.

**코드 위치**: `services/isolation/`

**공개 API**:
```python
gate = get_regional_isolation_gate()
    .isolate_region(region, reason, duration_seconds)
    .restore_region(region)
    .is_region_isolated(region) -> (bool, str)
```

**연결 계획**:

| 엔트리포인트 | 위치 | 내용 |
|-------------|------|------|
| CellTaggingMiddleware 확장 | `api/django/cell/middleware.py` | `is_region_isolated()` 체크 추가 |
| EventBus 구독 | `adapters/django/apps.py` | CB Cascade 감지 시 자동 격리 |

**Feature Flag**: `SELFHEALING_REGIONAL_ISOLATION_ENABLED` (default: False)

**우선순위**: P3 — Multi-Region 배포 시에만 필요

---

### 2.4 saga (Distributed Saga Orchestrator)

**기능**: 다단계 분산 트랜잭션 오케스트레이션. Redis Lua CAS 기반 상태 전이, 보상 트랜잭션, 고아 사가 탐지.

**코드 위치**: `services/saga/`

**공개 API**:
```python
orchestrator = SagaOrchestrator()
    .execute_saga(definition_name, context_data)
    .resume_saga(instance_id)

# Task (이미 존재)
scan_orphan_sagas()  # 주기적 고아 사가 탐지
```

**현재 상태**: 인프라 완성. Celery Task 래퍼 `saga/tasks.py` 존재. Beat 미등록, Saga Definition 미등록.

**연결 계획**:

| 엔트리포인트 | 위치 | 내용 |
|-------------|------|------|
| Celery Beat | `myproject/celery.py` | `scan_orphan_sagas` (2분 간격) |
| AppConfig.ready() | `adapters/django/apps.py` | Saga 정의 자동 등록 (autodiscover) |

**참고**: Saga Definition은 호스트 앱에서 정의해야 한다. selfhealing은 오케스트레이션 인프라만 제공.

**우선순위**: P2 — Recovery 흐름에서 보상 트랜잭션 필요

---

### 2.5 predictive_forecaster

**기능**: 시계열 예측 + 이상 탐지. Holt-Linear/Holt-Winters 예측, Z-Score/IQR 이상 탐지, 스파이크 분류(Flash Sale vs DDoS).

**코드 위치**: `services/predictive_forecaster/`

**공개 API**:
```python
service = PredictiveForecasterService()
    .ingest_metric(metric_name, value)
    .forecast_and_detect(metric_name) -> ForecastResult
```

**연결 계획**:

| 엔트리포인트 | 위치 | 내용 |
|-------------|------|------|
| Celery Beat | `myproject/celery.py` | 메트릭 수집 + 예측 루프 (60초) |
| Metrics 수집 훅 | `celery_tasks/metrics_tasks.py` | `collect_self_healing_metrics` 내에서 ingest 호출 |

**Feature Flag**: `SELFHEALING_FORECASTER_ENABLED` (default: False)

**우선순위**: P2 — 선제적 대응 (Proactive Action)의 핵심

---

## 3. 부분 연결 서비스 — 7개

### 3.1 event_journal — 시작 시 초기화 누락

| 상태 | 문제 | 해결 |
|------|------|------|
| 부분 연결 | `init_event_journal(bus)` 호출 없음 | AppConfig.ready()에 추가 |

Config Shadow 서비스가 EventJournal에 의존하므로 **Config Shadow보다 먼저 초기화**해야 한다.

### 3.2 rate_limit (Kafka 분산 채널) — 호출자 없음

| 상태 | 문제 | 해결 |
|------|------|------|
| 고아에 가까움 | `RateLimitCoordinator`가 Kafka 채널 미사용 | `coordinator.on_rate_limited()`에서 `broadcast_rate_limit_429()` 호출 |

### 3.3 config (Global Config Propagator) — 초기화 + 리스너 없음

| 상태 | 문제 | 해결 |
|------|------|------|
| 고아에 가까움 | `GlobalConfigPropagator` 미초기화 | AppConfig.ready() + ProviderRegistry 등록 |

### 3.4 capacity_reservation — 전혀 초기화 안됨

| 상태 | 문제 | 해결 |
|------|------|------|
| 고아에 가까움 | `CapacityReservationService` 미초기화 | AppConfig.ready()에서 DI로 초기화 |

의존성: RateController, PoolWatchdog, Bulkhead, GracefulDegradation — 모두 ProviderRegistry에서 가져올 수 있다.

### 3.5 config_shadow — 연결 양호

| 상태 | 비고 |
|------|------|
| **양호** | Celery Task 존재, Canary 서비스에서 사용 |

### 3.6 daily_report — 연결 양호

| 상태 | 비고 |
|------|------|
| **양호** | Celery Beat 등록 완료, 09:00 일간 리포트 |

### 3.7 runbook — 초기화 확인 필요

| 상태 | 문제 | 해결 |
|------|------|------|
| 대부분 연결 | `initialize_runbook_system()` 호출 여부 확인 필요 | AppConfig.ready()에 명시적 추가 |

---

## 4. 구현 순서

### Phase 1: 핵심 고아 서비스 (P1)

```
1. execution_services → Celery Beat 등록
2. event_journal → AppConfig.ready() 초기화
3. rate_limit → RateLimitCoordinator 연결
```

### Phase 2: 분석/예측 서비스 (P2)

```
4. correlation_engine → AppConfig.ready() + EventBus
5. predictive_forecaster → Celery Beat + Metrics Hook
6. saga → Celery Beat + Autodiscover
7. runbook → 초기화 확인/추가
```

### Phase 3: 인프라 서비스 (P3)

```
8. config → AppConfig.ready() + ProviderRegistry
9. capacity_reservation → AppConfig.ready() + DI
10. isolation → Middleware 확장
```

---

## 5. 수정 대상 파일

| 파일 | 변경 내용 |
|------|----------|
| `adapters/django/apps.py` | ready()에 6개 서비스 초기화 추가 |
| `myproject/celery.py` | Beat 스케줄에 4개 태스크 추가 |
| `services/rate_limit_coordinator/coordinator.py` | Kafka 분산 채널 연결 |
| `api/django/cell/middleware.py` | Regional Isolation 체크 추가 |
| `settings/` | Feature Flag 6개 추가 |

---

## 6. 검증 기준

각 서비스의 와이어링 완료 기준:

1. **Import Chain 존재**: 엔트리포인트 → 서비스 모듈까지의 import 경로
2. **Feature Flag 제어**: 비활성화 시 초기화 스킵
3. **Graceful Failure**: 의존성(Redis 등) 실패 시 서비스 기동에 영향 없음
4. **테스트 커버리지**: 와이어링 경로의 단위 테스트
5. **CI 검증 통과**: 318번 문서의 CI 스크립트로 자동 검증

---

## 7. 관련 문서

- **318_WIRING_VERIFICATION_CI.md** — 와이어링 상태 자동 검증 CI 스크립트
- **75_CRISIS_BUDGET_MULTIPLIER.md** — domain_tag 데코레이터 기반 도메인 인지
- **299_CONFIG_SHADOW_EVALUATOR.md** — event_journal 의존 서비스
