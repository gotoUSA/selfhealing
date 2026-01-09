# 31. Chaos Experiment 확장 계획

> **작성일**: 2026-01-09  
> **최종 수정**: 2026-01-10  
> **상태**: Phase 0-7 구현 완료  
> **관련 문서**: [13_CHAOS_ENGINEERING.md](../13_CHAOS_ENGINEERING.md), [24_CHAOS_INTEGRATION_PLAN.md](24_CHAOS_INTEGRATION_PLAN.md)

---

## 📋 통합 구현 로드맵

> 본 문서의 모든 구현 항목을 단계별로 정리한 통합 구현 가이드입니다.

### 구현 Phase 요약

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         CHAOS EXPERIMENT 확장 구현 로드맵                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Phase 0: 기반 인프라 구축 (1주)                                                  │
│    └─ 공통 모듈 생성, Enum 확장, Factory 함수 업데이트                             │
│                                                                                 │
│  Phase 1: 핵심 실험 타입 구현 (P1) (2주)                                          │
│    └─ CircuitBreakerOpenExperiment, RateLimitExperiment                         │
│                                                                                 │
│  Phase 2: 보조 실험 타입 구현 (P2) (2주)                                          │
│    └─ Error4xxExperiment, PartialFailureExperiment                              │
│                                                                                 │
│  Phase 3: 고급 실험 타입 구현 (P3) (2주)                                          │
│    └─ ConnectionResetExperiment, CascadingFailureExperiment                     │
│                                                                                 │
│  Phase 4: 5대 리스크 처방전 구현 (2주)                                            │
│    └─ Q1-Q5: 데이터 태깅, 메트릭 필터, Cgroup, 이중 락, Blacklist                 │
│                                                                                 │
│  Phase 5: 분산 환경 지원 (1주)                                                    │
│    └─ Redis Pub/Sub, SelfHealingHttpClient                                      │
│                                                                                 │
│  Phase 6: Resilience 검증 시스템 (2주)                                           │
│    └─ ResilienceExpectation, 자동 채점 시스템                                     │
│                                                                                 │
│  Phase 7: 테스트 및 문서화 (1주)                                                  │
│    └─ 단위/통합 테스트, 온보딩 문서                                               │
└─────────────────────────────────────────────────────────────────────────────────┘
                                   총 예상 기간: 13주
```

---

### Phase 0: 기반 인프라 구축 (1주)

| 순서 | 작업 | 파일 | 섹션 참조 | 선행 조건 |
|------|------|------|-----------|-----------|
| 0-1 | `ExperimentType` Enum 확장 (6개 타입 추가) | `base.py` | [3.1](#31-basepy-수정) | 없음 |
| 0-2 | Factory 함수 `create_experiment()` 업데이트 | `experiment_impl.py` | [3.2](#32-factory-함수-확장) | 0-1 |
| 0-3 | `CgroupResourceMonitor` 공통 모듈 생성 | `core/resource_monitor.py` (신규) | [7.3](#73-q3-cgroup-aware-limit-resource-안전선) | 없음 |
| 0-4 | `BlastRadiusPolicy.from_env()` 환경변수 로드 추가 | `blast_radius.py` | [7.5](#75-q5-default-payment-blacklist) | 없음 |

**완료 기준:**
- [x] `ExperimentType` Enum에 6개 타입 추가됨 ✅ (2026-01-09)
- [x] Factory 함수가 새 타입을 인식함 (placeholder 클래스 포함) ✅ (2026-01-09)
- [x] `CgroupResourceMonitor` 단위 테스트 통과 ✅ (2026-01-10)
- [x] 환경변수로 `CHAOS_EXCLUDED_SERVICES` 설정 가능 ✅ (2026-01-10)

---

### Phase 1: 핵심 실험 타입 구현 - P1 (2주)

> **우선순위 P1**: CB + Canary 검증 핵심, Self-DDoS 방지 검증

| 순서 | 작업 | 파일 | 섹션 참조 | 선행 조건 |
|------|------|------|-----------|-----------|
| 1-1 | `CircuitBreakerOpenExperiment` 구현 | `experiment_impl.py` | [2.4](#24-circuitbreakeropenexperiment) | Phase 0 |
| 1-2 | CB 강제 OPEN/CLOSE API 연동 테스트 | - | [2.4 연동 포인트](#24-circuitbreakeropenexperiment) | 1-1 |
| 1-3 | Canary Recovery 검증 통합 테스트 | `tests/` | [2.4 연동 포인트](#24-circuitbreakeropenexperiment) | 1-2 |
| 1-4 | `RateLimitExperiment` 구현 | `experiment_impl.py` | [2.3](#23-ratelimitexperiment) | Phase 0 |
| 1-5 | `RateLimitTracker` 연동 테스트 | - | [2.3 연동 포인트](#23-ratelimitexperiment) | 1-4 |
| 1-6 | Self-DDoS 방지 로직 검증 | `tests/` | [2.3 연동 포인트](#23-ratelimitexperiment) | 1-5 |

**완료 기준:**
- [x] `CircuitBreakerOpenExperiment` inject/rollback 정상 동작 ✅ (2026-01-09)
- [ ] CB OPEN → HALF_OPEN → Canary 전이 검증됨
- [x] `RateLimitExperiment` 주입 시 CB 자동 OPEN 확인 ✅ (2026-01-09)
- [ ] Self-DDoS 보호 로직 동작 확인

---

### Phase 2: 보조 실험 타입 구현 - P2 (2주)

> **우선순위 P2**: 프론트엔드 에러 핸들링, Load Shedding 검증

| 순서 | 작업 | 파일 | 섹션 참조 | 선행 조건 |
|------|------|------|-----------|-----------|
| 2-1 | `Error4xxExperiment` 구현 | `experiment_impl.py` | [2.1](#21-error4xxexperiment) | Phase 0 |
| 2-2 | 다중 에러 코드 (400/401/403/404/429) 테스트 | `tests/` | [2.1](#21-error4xxexperiment) | 2-1 |
| 2-3 | Throttle 연동 검증 (429 폭풍) | - | [2.1 연동 포인트](#21-error4xxexperiment) | 2-2 |
| 2-4 | `PartialFailureExperiment` 구현 | `experiment_impl.py` | [2.5](#25-partialfailureexperiment) | Phase 0 |
| 2-5 | Load Shedding 트리거 테스트 | - | [2.5 연동 포인트](#25-partialfailureexperiment) | 2-4 |
| 2-6 | Emergency Mode 에스컬레이션 검증 | - | [2.5 연동 포인트](#25-partialfailureexperiment) | 2-5 |

**완료 기준:**
- [x] `Error4xxExperiment` 모든 4xx 코드 주입 가능 ✅ (2026-01-09)
- [ ] 429 에러 시 `AdaptiveThrottle` 반응 확인
- [x] `PartialFailureExperiment` 부분 장애율 조절 가능 ✅ (2026-01-09)
- [ ] Load Shedding 자동 트리거 확인

---

### Phase 3: 고급 실험 타입 구현 - P3 (2주)

> **우선순위 P3**: 네트워크 복원력, Panic Threshold (고위험)

| 순서 | 작업 | 파일 | 섹션 참조 | 선행 조건 |
|------|------|------|-----------|-----------|
| 3-1 | `ConnectionResetExperiment` 구현 | `experiment_impl.py` | [2.2](#22-connectionresetexperiment) | Phase 0 |
| 3-2 | TCP RST 시뮬레이션 테스트 | `tests/` | [2.2](#22-connectionresetexperiment) | 3-1 |
| 3-3 | Retry/Backoff 연동 검증 | - | [2.2 연동 포인트](#22-connectionresetexperiment) | 3-2 |
| 3-4 | `CascadingFailureExperiment` 구현 ⚠️ | `experiment_impl.py` | [2.6](#26-cascadingfailureexperiment) | Phase 1 완료 |
| 3-5 | 다중 서비스 순차 장애 테스트 | `tests/` | [2.6](#26-cascadingfailureexperiment) | 3-4 |
| 3-6 | Panic Threshold (70% CB OPEN) 검증 | - | [2.6 연동 포인트](#26-cascadingfailureexperiment) | 3-5 |
| 3-7 | Emergency Level 3 자동 선포 검증 | - | [2.6 연동 포인트](#26-cascadingfailureexperiment) | 3-6 |

**완료 기준:**
- [x] `ConnectionResetExperiment` 연결 리셋 시뮬레이션 가능 ✅ (2026-01-09)
- [ ] `BackoffCalculator` 재시도 로직 정상 동작
- [x] `CascadingFailureExperiment` 다중 서비스 장애 주입 가능 ✅ (2026-01-09)
- [ ] 70% CB OPEN 시 Panic 감지 및 Emergency Level 3 선포

⚠️ **주의**: `CascadingFailureExperiment`는 **고위험 실험**으로 운영 환경 실행 전 철저한 검증 필요

---

### Phase 4: 5대 리스크 처방전 구현 (2주)

> 아키텍트 리뷰 반영 - 운영 안전성 강화

| 순서 | 작업 | 파일 | 섹션 참조 | 선행 조건 |
|------|------|------|-----------|-----------|
| 4-1 | **Q1**: `is_chaos_experiment` 자동 태깅 | `error_budget/calculator.py`, `base.py` | [7.1](#71-q1-실험-데이터-자동-태깅-is_chaos_experiment) | Phase 0 |
| 4-2 | Error Budget에서 Chaos 데이터 제외 로직 | `error_budget/stats_provider.py` | [7.1](#71-q1-실험-데이터-자동-태깅-is_chaos_experiment) | 4-1 |
| 4-3 | **Q2**: `ChaosAwareMetricsAdapter` 구현 | `auto_tuning/chaos_aware_metrics.py` (신규) | [7.2](#72-q2-auto-tuning-metric-silence-chaosawaremetricsadapter) | Phase 0 |
| 4-4 | AutoTuning 메트릭 수집 스킵 테스트 | `tests/` | [7.2](#72-q2-auto-tuning-metric-silence-chaosawaremetricsadapter) | 4-3 |
| 4-5 | **Q3**: Cgroup 안전 마진 적용 (15%) | `experiment_impl.py` (ResourceExhaustion) | [7.3](#73-q3-cgroup-aware-limit-resource-안전선) | Phase 0 완료 |
| 4-6 | OOM 방지 테스트 (컨테이너 환경) | `tests/` | [7.3](#73-q3-cgroup-aware-limit-resource-안전선) | 4-5 |
| 4-7 | **Q4**: 이중 락 패턴 구현 | `idempotency_service.py`, `scheduler.py` | [7.4](#74-q4-domain-level-lock-실험-간섭-차단) | Phase 0 |
| 4-8 | 서비스 락 + 스케줄 락 테스트 | `tests/` | [7.4](#74-q4-domain-level-lock-실험-간섭-차단) | 4-7 |
| 4-9 | **Q5**: 결제 서비스 Blacklist 검증 강화 | `blast_radius.py` | [7.5](#75-q5-default-payment-blacklist) | Phase 0 완료 |
| 4-10 | 온보딩 체크리스트 문서화 | `docs/` | [7.5](#75-q5-default-payment-blacklist) | 4-9 |

**완료 기준:**
- [x] Chaos 실험 데이터가 Error Budget에서 자동 제외됨 ✅ (2026-01-10)
- [x] AutoTuning이 실험 중 메트릭 수집 스킵 ✅ (2026-01-10)
- [x] 컨테이너 메모리 제한의 85%까지만 사용 ✅ (2026-01-10)
- [x] 동일 서비스 동시 실험 차단됨 ✅ (2026-01-10)
- [x] 결제 서비스 Blacklist 환경변수 설정 가이드 완료 ✅ (2026-01-10)

---

### Phase 5: 분산 환경 지원 (1주)

> 멀티-인스턴스 환경에서의 실시간 동기화

| 순서 | 작업 | 파일 | 섹션 참조 | 선행 조건 |
|------|------|------|-----------|-----------|
| 5-1 | `RedisEventBus` 구현 | `services/event_bus_redis.py` (신규) | [6.1](#61-redis-pubsub-실시간-동기화) | Phase 0 |
| 5-2 | Redis Pub/Sub 통합 테스트 | `tests/` | [6.1](#61-redis-pubsub-실시간-동기화) | 5-1 |
| 5-3 | `SelfHealingHttpClient` 래퍼 구현 | `services/http_client.py` (신규) | [6.2](#62-헤더-전파-방식-분석-otel-vs-수동) | Phase 0 |
| 5-4 | Chaos 컨텍스트 자동 헤더 전파 테스트 | `tests/` | [6.2](#62-헤더-전파-방식-분석-otel-vs-수동) | 5-3 |
| 5-5 | `get_event_bus()` 팩토리 함수 (distributed 플래그) | `event_bus_redis.py` | [6.1](#61-redis-pubsub-실시간-동기화) | 5-2 |

**완료 기준:**
- [x] 멀티-인스턴스 환경에서 Chaos 이벤트 실시간 전파됨 ✅ (2026-01-10)
- [x] `SelfHealingHttpClient` 사용 시 헤더 자동 전파됨 ✅ (2026-01-10)
- [x] OTel 의존 없이 수동 전파 가능 ✅ (2026-01-10)

---

### Phase 6: Resilience 검증 시스템 (2주)

> 가설 기반 자동 채점 시스템

| 순서 | 작업 | 파일 | 섹션 참조 | 선행 조건 |
|------|------|------|-----------|-----------|
| 6-1 | `ResilienceExpectation` 데이터 클래스 구현 | `chaos/resilience_expectation.py` (신규) | [8.3](#83-구현-계획) | Phase 0 |
| 6-2 | `ResilienceAssertion` 구조체 구현 | `chaos/resilience_expectation.py` | [8.3](#83-구현-계획) | 6-1 |
| 6-3 | `ExperimentConfig`에 `resilience_expectation` 필드 추가 | `base.py` | [8.4](#84-experimentconfig-확장) | 6-2 |
| 6-4 | `ExperimentResult`에 `resilience_validation` 필드 추가 | `base.py` | [8.5](#85-experimentresult-확장) | 6-3 |
| 6-5 | CB 상태 조회 연동 (Validator) | `chaos/resilience_validator.py` (신규) | [8.3](#83-구현-계획) | Phase 1 |
| 6-6 | Fallback/Retry 이벤트 수집 연동 | `chaos/resilience_validator.py` | [8.3](#83-구현-계획) | 6-5 |
| 6-7 | Resilience Score 자동 계산 테스트 | `tests/` | [8.6](#86-사용-예시) | 6-6 |

**완료 기준:**
- [x] `ResilienceExpectation` 으로 기대 행동 정의 가능 ✅ (2026-01-10)
- [x] 실험 후 자동으로 Resilience Score 계산됨 ✅ (2026-01-10)
- [x] "CB가 10초 내 OPEN되어야 함" 같은 기대 자동 검증됨 ✅ (2026-01-10)

---

### Phase 7: 테스트 및 문서화 (1주)

| 순서 | 작업 | 파일 | 섹션 참조 | 선행 조건 |
|------|------|------|-----------|-----------|
| 7-1 | 단위 테스트 작성 (6개 신규 실험 타입) | `tests/self_healing/chaos/test_phase0_phase1_experiments.py`, `test_phase2_phase3_experiments.py` | [4.1](#41-단위-테스트) | Phase 1-3 |
| 7-2 | 통합 테스트 시나리오 작성 | `tests/` | [4.2](#42-통합-테스트) | 7-1 |
| 7-3 | Rate Limit Storm 시나리오 검증 | `tests/` | [4.2](#42-통합-테스트) | 7-2 |
| 7-4 | Emergency Escalation 시나리오 검증 | `tests/` | [4.2](#42-통합-테스트) | 7-2 |
| 7-5 | Panic Recovery 시나리오 검증 | `tests/` | [4.2](#42-통합-테스트) | 7-2 |
| 7-6 | 온보딩 문서 업데이트 (결제 Blacklist 설정) | `docs/` | [7.5](#75-q5-default-payment-blacklist) | Phase 4 |
| 7-7 | API 문서 업데이트 | `docs/` | - | 모든 Phase |

**완료 기준:**
- [x] Phase 0/1 단위 테스트 작성됨 ✅ (2026-01-09)
- [x] Phase 2/3 단위 테스트 작성됨 ✅ (2026-01-09)
- [x] Phase 4/5 단위 테스트 작성됨 ✅ (2026-01-10)
- [x] Phase 6/7 단위 테스트 작성됨 ✅ (2026-01-10)
- [x] 3개 통합 테스트 시나리오 통과 ✅ (2026-01-10)
- [ ] 온보딩 문서에 결제 서비스 보호 설정 가이드 포함

---

### 구현 의존성 다이어그램

```
Phase 0 (기반 인프라)
    │
    ├──────────────────┬──────────────────┬──────────────────┐
    ▼                  ▼                  ▼                  ▼
Phase 1 (P1 실험)   Phase 2 (P2 실험)   Phase 4 (리스크)   Phase 5 (분산)
    │                  │                  │                  │
    ├──────────────────┘                  │                  │
    ▼                                     │                  │
Phase 3 (P3 실험) ◄────────────────────────┘                  │
    │                                                        │
    ├────────────────────────────────────────────────────────┘
    ▼
Phase 6 (Resilience)
    │
    ▼
Phase 7 (테스트/문서)
```

---

### 마일스톤 체크포인트

| 마일스톤 | 완료 기준 | 예상 완료 |
|---------|----------|----------|
| **M1: 기반 완료** | Phase 0 완료, Enum/Factory 동작 | 1주차 |
| **M2: 핵심 실험 완료** | Phase 1 완료, CB/Rate Limit 실험 가능 | 3주차 |
| **M3: 전체 실험 완료** | Phase 1-3 완료, 6개 신규 실험 모두 동작 | 7주차 |
| **M4: 운영 안전성** | Phase 4-5 완료, 5대 리스크 처방전 적용 | 10주차 |
| **M5: 자동 채점** | Phase 6 완료, Resilience 자동 검증 | 12주차 |
| **M6: 릴리즈 준비** | Phase 7 완료, 문서화 및 테스트 완료 | 13주차 |

---

## 1. 개요

### 1.1 현황 분석

#### 구현 완료 (5개 - `experiment_impl.py`)

| 실험 타입 | 클래스 | 위험도 | 코드 위치 |
|-----------|--------|--------|-----------|
| `latency_injection` | `LatencyInjectionExperiment` | Low | `experiment_impl.py:37-115` |
| `error_5xx` | `Error5xxExperiment` | Medium | `experiment_impl.py:122-184` |
| `packet_loss` | `PacketLossExperiment` | High | `experiment_impl.py:191-258` |
| `timeout` | `TimeoutExperiment` | Low | `experiment_impl.py:265-327` |
| `resource_exhaustion` | `ResourceExhaustionExperiment` | High | `experiment_impl.py:334-400` |

#### 정의되었으나 미구현 (6개 - `chaos_context.py:59-71`)

```python
# chaos_context.py:59-71
class ChaosExperimentType(str, Enum):
    LATENCY_INJECTION = "latency_injection"     # ✅ 구현됨
    ERROR_5XX = "error_5xx"                     # ✅ 구현됨
    ERROR_4XX = "error_4xx"                     # ❌ 미구현
    TIMEOUT = "timeout"                         # ✅ 구현됨
    CONNECTION_RESET = "connection_reset"       # ❌ 미구현
    RATE_LIMIT = "rate_limit"                   # ❌ 미구현
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"  # ❌ 미구현
    RESOURCE_EXHAUSTION = "resource_exhaustion" # ✅ 구현됨
    PARTIAL_FAILURE = "partial_failure"         # ❌ 미구현
    CASCADING_FAILURE = "cascading_failure"     # ❌ 미구현
```

### 1.2 업계 도구 비교

| 카테고리 | AWS FIS / Gremlin / LitmusChaos | 현재 구현 | Gap |
|----------|--------------------------------|-----------|-----|
| **네트워크** | Latency, Packet Loss, DNS, Blackhole, Bandwidth | ✅ 2/5 | DNS, Blackhole, Bandwidth |
| **응답** | HTTP 4xx, 5xx, Timeout, Connection Reset | ✅ 2/4 | 4xx, Connection Reset |
| **리소스** | CPU, Memory, Disk I/O, Process Kill | ✅ 1/4 | Disk I/O, Process Kill |
| **상태** | Circuit Breaker, Rate Limit, State Injection | ❌ 0/3 | 전체 미구현 |
| **인프라** | Container Kill, Pod Delete, AZ Failover | ❌ 0/3 | 범위 외 (Kubernetes) |

---

## 2. Phase 1: 미구현 실험 타입 구현

### 2.1 Error4xxExperiment

#### 목적
클라이언트 에러(400, 401, 403, 404, 429) 주입으로 프론트엔드 에러 핸들링 검증

#### 코드 근거
```python
# chaos_context.py:64
ERROR_4XX = "error_4xx"
```

#### 구현 명세

**파일**: `packages/selfhealing-python/src/selfhealing/services/chaos/experiment_impl.py`

```python
class Error4xxExperiment(ChaosExperiment):
    """
    Inject HTTP 4xx errors into service responses.
    
    Simulates client errors, authentication failures, rate limiting.
    
    Config parameters:
        - error_code: HTTP error code to inject (default: 400)
        - error_codes: List of codes for random selection (optional)
        - error_message: Error message (default: "Bad Request (Chaos)")
    """
    
    experiment_type = ExperimentType.ERROR_4XX.value  # 추가 필요
    requires_approval = False  # Low risk
    
    @property
    def error_code(self) -> int:
        codes = self.config.parameters.get("error_codes")
        if codes:
            import random
            return random.choice(codes)
        return self.config.parameters.get("error_code", 400)
    
    @property
    def error_message(self) -> str:
        messages = {
            400: "Bad Request (Chaos Experiment)",
            401: "Unauthorized (Chaos Experiment)",
            403: "Forbidden (Chaos Experiment)",
            404: "Not Found (Chaos Experiment)",
            429: "Too Many Requests (Chaos Experiment)",
        }
        return self.config.parameters.get(
            "error_message", 
            messages.get(self.error_code, "Client Error")
        )
    
    def inject_chaos(self) -> bool:
        """Inject 4xx errors into target service with TTL."""
        logger.info(
            f"[Error4xxInjection] Injecting {self.error_code} errors "
            f"to {self.config.target_service} at {self.config.injection_rate*100}% rate "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "error_4xx_injection": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "error_code": self.error_code,
                    "error_message": self.error_message,
                    "rate": self.config.injection_rate,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[Error4xxInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove 4xx error injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[Error4xxInjection] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "error_4xx_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[Error4xxInjection] Rollback failed: {e}")
```

#### 연동 포인트
- **Rate Limiter 검증**: 429 에러 주입으로 `RateLimitTracker` 동작 확인
  - 코드 위치: `services/circuit_breaker/rate_limit_tracker.py:45-80`
- **Throttle 검증**: 429 폭풍 시 `AdaptiveThrottle` 반응 확인
  - 코드 위치: `services/throttle/adaptive.py:140-170`

---

### 2.2 ConnectionResetExperiment

#### 목적
네트워크 연결 리셋 시뮬레이션으로 재연결 로직 및 Circuit Breaker 검증

#### 코드 근거
```python
# chaos_context.py:66
CONNECTION_RESET = "connection_reset"
```

#### 구현 명세

```python
class ConnectionResetExperiment(ChaosExperiment):
    """
    Simulate network connection reset (TCP RST).
    
    Simulates sudden connection drops, network instability.
    
    Config parameters:
        - reset_after_bytes: Bytes to send before reset (0=immediate)
        - reset_probability: Probability of reset per request (0-1)
    """
    
    experiment_type = ExperimentType.CONNECTION_RESET.value  # 추가 필요
    requires_approval = True  # Medium-High risk
    
    @property
    def reset_after_bytes(self) -> int:
        return self.config.parameters.get("reset_after_bytes", 0)
    
    @property
    def reset_probability(self) -> float:
        return self.config.parameters.get("reset_probability", 0.5)
    
    def inject_chaos(self) -> bool:
        """Inject connection reset behavior with TTL."""
        logger.info(
            f"[ConnectionReset] Injecting connection resets "
            f"to {self.config.target_service} at {self.reset_probability*100}% probability "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "connection_reset": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "reset_after_bytes": self.reset_after_bytes,
                    "reset_probability": self.reset_probability,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[ConnectionReset] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove connection reset injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[ConnectionReset] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "connection_reset": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[ConnectionReset] Rollback failed: {e}")
```

#### 연동 포인트
- **Circuit Breaker 검증**: 연결 실패 시 `CircuitBreakerService.record_failure()` 동작 확인
  - 코드 위치: `services/circuit_breaker/service.py:200-230`
- **Retry Handler 검증**: `BackoffCalculator` 재시도 로직 확인
  - 코드 위치: `services/backoff_calculator.py:50-100`

---

### 2.3 RateLimitExperiment

#### 목적
Rate Limit(429 응답) 폭풍 주입으로 Circuit Breaker의 자동 OPEN 동작 검증

#### 코드 근거
```python
# chaos_context.py:67
RATE_LIMIT = "rate_limit"

# circuit_breaker/__init__.py:51-53
from .rate_limit_tracker import (
    RateLimitTracker,
    get_rate_limit_tracker,
)
```

#### 구현 명세

```python
class RateLimitExperiment(ChaosExperiment):
    """
    Inject rate limit (429) responses to trigger CB auto-open.
    
    Tests rate limit cascade detection and self-DDoS protection.
    
    Config parameters:
        - rate_limit_count: Number of 429s to inject (default: 10)
        - window_seconds: Time window for injection (default: 60)
        - retry_after_seconds: Retry-After header value (default: 30)
    """
    
    experiment_type = ExperimentType.RATE_LIMIT.value  # 추가 필요
    requires_approval = False  # Medium risk
    
    @property
    def rate_limit_count(self) -> int:
        return self.config.parameters.get("rate_limit_count", 10)
    
    @property
    def retry_after_seconds(self) -> int:
        return self.config.parameters.get("retry_after_seconds", 30)
    
    def inject_chaos(self) -> bool:
        """Inject rate limit responses to trigger CB cascade."""
        logger.info(
            f"[RateLimitInjection] Injecting {self.rate_limit_count} rate limits "
            f"to {self.config.target_service} (TTL: {self._effective_ttl}s)"
        )
        
        try:
            # Rate Limit Tracker에 직접 기록 (CB 자동 OPEN 유발)
            from selfhealing.services.circuit_breaker import (
                get_rate_limit_tracker,
                record_rate_limit,
            )
            
            tracker = get_rate_limit_tracker()
            for _ in range(self.rate_limit_count):
                record_rate_limit(
                    service_name=self.config.target_service,
                    retry_after=self.retry_after_seconds,
                )
            
            # 설정 저장 (TTL용)
            _apply_chaos_config({
                "rate_limit_injection": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "count": self.rate_limit_count,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[RateLimitInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Clear rate limit injection state."""
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[RateLimitInjection] Rolling back {self.experiment_id}")
            
            try:
                # Rate Limit Tracker 리셋은 어려우므로 설정만 해제
                _apply_chaos_config({
                    "rate_limit_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[RateLimitInjection] Rollback failed: {e}")
```

#### 연동 포인트
- **Rate Limit Tracker**: `record_rate_limit()` 함수로 429 기록
  - 코드 위치: `services/circuit_breaker/rate_limit_tracker.py:100-130`
- **CB 자동 OPEN**: 임계치 초과 시 자동 CB OPEN
  - 코드 위치: `services/circuit_breaker/protection.py:80-120`
- **Self-DDoS 방지**: `ProtectionMixin._is_self_ddos()` 동작 확인
  - 코드 위치: `services/circuit_breaker/protection.py:150-180`

---

### 2.4 CircuitBreakerOpenExperiment

#### 목적
Circuit Breaker 강제 OPEN으로 Canary Recovery 및 Fallback 전략 검증

#### 코드 근거
```python
# chaos_context.py:68
CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"

# circuit_breaker/service.py:50-55
result = service.force_open(
    service_name="external_api",
    reason="External service maintenance",
    controlled_by=admin_user
)
```

#### 구현 명세

```python
class CircuitBreakerOpenExperiment(ChaosExperiment):
    """
    Force Circuit Breaker to OPEN state.
    
    Tests fast-fail behavior, fallback strategies, and canary recovery.
    
    Config parameters:
        - trigger_canary: Whether to wait for canary recovery (default: True)
        - fallback_type: Expected fallback type (cache, dlq, default)
    """
    
    experiment_type = ExperimentType.CIRCUIT_BREAKER_OPEN.value  # 추가 필요
    requires_approval = True  # High risk - blocks real traffic
    
    @property
    def trigger_canary(self) -> bool:
        return self.config.parameters.get("trigger_canary", True)
    
    @property
    def fallback_type(self) -> str:
        return self.config.parameters.get("fallback_type", "default")
    
    def inject_chaos(self) -> bool:
        """Force CB to OPEN state."""
        logger.info(
            f"[CBOpenInjection] Forcing CB OPEN for {self.config.target_service} "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            from selfhealing.services.circuit_breaker import (
                get_circuit_breaker_service,
                force_open_circuit,
            )
            
            # CB 강제 OPEN
            result = force_open_circuit(
                service_name=self.config.target_service,
                reason=f"Chaos Experiment: {self.experiment_id}",
                controlled_by="chaos_engine",
            )
            
            if not result.success:
                logger.error(f"[CBOpenInjection] Failed to open CB: {result.message}")
                return False
            
            # 설정 저장 (TTL 및 rollback용)
            _apply_chaos_config({
                "circuit_breaker_open": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "trigger_canary": self.trigger_canary,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[CBOpenInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Force CB back to CLOSED state."""
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[CBOpenInjection] Rolling back {self.experiment_id}")
            
            try:
                from selfhealing.services.circuit_breaker import force_close_circuit
                
                force_close_circuit(
                    service_name=self.config.target_service,
                    reason=f"Chaos Experiment Rollback: {self.experiment_id}",
                    controlled_by="chaos_engine",
                    trigger_replay=False,  # 실험이므로 리플레이 안 함
                )
                
                _apply_chaos_config({
                    "circuit_breaker_open": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[CBOpenInjection] Rollback failed: {e}")
```

#### 연동 포인트
- **Canary Recovery 검증**: HALF_OPEN → Canary 1/2/3 단계 전이
  - 코드 위치: `services/circuit_breaker/canary_recovery.py:40-100`
- **Fallback 전략 검증**: `should_allow_with_fallback()` 동작 확인
  - 코드 위치: `services/circuit_breaker/service.py:170-220`
- **Stale Cache 연동**: CB OPEN 시 캐시 반환
  - 코드 위치: `services/circuit_breaker/stale_cache_integration.py`

---

### 2.5 PartialFailureExperiment

#### 목적
서비스 부분 장애로 Graceful Degradation 및 Load Shedding 동작 검증

#### 코드 근거
```python
# chaos_context.py:70
PARTIAL_FAILURE = "partial_failure"

# circuit_breaker/load_shedding.py:1-18
"""
Load Shedding (부분적 차단) - Phase 5
핵심 서비스에 장애 조짐이 보이면, 비핵심 서비스 트래픽을 먼저 제한하여
핵심 서비스에 리소스를 집중시킵니다.
"""
```

#### 구현 명세

```python
class PartialFailureExperiment(ChaosExperiment):
    """
    Inject partial failures to test graceful degradation.
    
    Tests load shedding, emergency mode escalation.
    
    Config parameters:
        - failure_rate: Percentage of requests to fail (default: 30%)
        - affected_endpoints: List of endpoints to affect (optional)
        - trigger_shedding: Whether to trigger load shedding (default: True)
    """
    
    experiment_type = ExperimentType.PARTIAL_FAILURE.value  # 추가 필요
    requires_approval = True  # High risk
    
    @property
    def failure_rate(self) -> float:
        return self.config.parameters.get("failure_rate", 0.30)
    
    @property
    def affected_endpoints(self) -> list:
        return self.config.parameters.get("affected_endpoints", [])
    
    @property
    def trigger_shedding(self) -> bool:
        return self.config.parameters.get("trigger_shedding", True)
    
    def inject_chaos(self) -> bool:
        """Inject partial failures."""
        logger.info(
            f"[PartialFailure] Injecting {self.failure_rate*100}% failures "
            f"to {self.config.target_service} (TTL: {self._effective_ttl}s)"
        )
        
        try:
            # Load Shedding 트리거 (선택적)
            if self.trigger_shedding:
                from selfhealing.services.circuit_breaker.load_shedding import (
                    LoadSheddingManager,
                    get_load_shedding_manager,
                )
                
                manager = get_load_shedding_manager()
                # 서비스에 에러율 주입
                manager._inject_error_rate(
                    service_name=self.config.target_service,
                    error_rate=self.failure_rate * 100,  # percent
                )
            
            _apply_chaos_config({
                "partial_failure": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "failure_rate": self.failure_rate,
                    "affected_endpoints": self.affected_endpoints,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[PartialFailure] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove partial failure injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[PartialFailure] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "partial_failure": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[PartialFailure] Rollback failed: {e}")
```

#### 연동 포인트
- **Load Shedding 검증**: 부하 시 비핵심 서비스 트래픽 제한
  - 코드 위치: `services/circuit_breaker/load_shedding.py:200-300`
- **Emergency Mode 에스컬레이션**: 에러율 급증 시 Emergency Level 상승
  - 코드 위치: `services/emergency_mode/manager.py:200-250`

---

### 2.6 CascadingFailureExperiment

#### 목적
연쇄 장애 시뮬레이션으로 Panic Threshold 및 Emergency Level 3 선포 검증

#### 코드 근거
```python
# chaos_context.py:71
CASCADING_FAILURE = "cascading_failure"

# circuit_breaker/panic_threshold.py:1-27
"""
70% 이상의 Circuit Breaker가 동시에 OPEN되면, 개별 서비스 문제가 아니라
인프라 전체 붕괴로 판단합니다. 이때 자율 운영 엔진이 스스로 Emergency Level 3를
선포하고 모든 자동 복구를 중단합니다.
"""
```

#### 구현 명세

```python
class CascadingFailureExperiment(ChaosExperiment):
    """
    Simulate cascading failures across multiple services.
    
    Tests panic threshold detection and Emergency Level 3 escalation.
    
    Config parameters:
        - affected_services: List of services to fail
        - cascade_delay_seconds: Delay between service failures (default: 5)
        - target_open_percent: Target CB OPEN percentage (default: 75%)
    """
    
    experiment_type = ExperimentType.CASCADING_FAILURE.value  # 추가 필요
    requires_approval = True  # Critical risk - requires manual approval
    
    @property
    def affected_services(self) -> list:
        return self.config.parameters.get("affected_services", [])
    
    @property
    def cascade_delay_seconds(self) -> int:
        return self.config.parameters.get("cascade_delay_seconds", 5)
    
    @property
    def target_open_percent(self) -> float:
        return self.config.parameters.get("target_open_percent", 75.0)
    
    def inject_chaos(self) -> bool:
        """Inject cascading failures across services."""
        logger.warning(
            f"[CascadingFailure] CRITICAL: Injecting cascading failures to "
            f"{len(self.affected_services)} services (TTL: {self._effective_ttl}s)"
        )
        
        try:
            from selfhealing.services.circuit_breaker import force_open_circuit
            import time
            
            opened_services = []
            for service in self.affected_services:
                result = force_open_circuit(
                    service_name=service,
                    reason=f"Cascading Failure Experiment: {self.experiment_id}",
                    controlled_by="chaos_engine",
                )
                if result.success:
                    opened_services.append(service)
                
                # 연쇄 효과 시뮬레이션을 위한 지연
                time.sleep(self.cascade_delay_seconds)
                
                # Kill Switch 확인
                if self._is_killed():
                    logger.warning("[CascadingFailure] Kill switch activated, stopping")
                    break
            
            _apply_chaos_config({
                "cascading_failure": {
                    "enabled": True,
                    "affected_services": opened_services,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[CascadingFailure] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Close all opened circuit breakers."""
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[CascadingFailure] Rolling back {self.experiment_id}")
            
            try:
                from selfhealing.services.circuit_breaker import force_close_circuit
                
                for service in self.affected_services:
                    force_close_circuit(
                        service_name=service,
                        reason=f"Cascading Failure Rollback: {self.experiment_id}",
                        controlled_by="chaos_engine",
                        trigger_replay=False,
                    )
                
                _apply_chaos_config({
                    "cascading_failure": {
                        "enabled": False,
                        "affected_services": [],
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[CascadingFailure] Rollback failed: {e}")
```

#### 연동 포인트
- **Panic Threshold 검증**: 70% CB OPEN 감지
  - 코드 위치: `services/circuit_breaker/panic_threshold.py:100-150`
- **Emergency Level 3 자동 선포**: 시스템 전체 Lockdown
  - 코드 위치: `services/emergency_mode/manager.py:300-350`
- **Freeze Mode 검증**: 모든 자동 복구 중단
  - 코드 위치: `services/circuit_breaker/freeze_mode.py:100-150`

---

## 3. ExperimentType Enum 확장

### 3.1 base.py 수정

**파일**: `packages/selfhealing-python/src/selfhealing/services/chaos/base.py`

```python
# 현재 (base.py:87-93)
class ExperimentType(str, Enum):
    LATENCY_INJECTION = "latency_injection"
    ERROR_5XX = "error_5xx"
    PACKET_LOSS = "packet_loss"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTION = "resource_exhaustion"

# 확장 후
class ExperimentType(str, Enum):
    """Core experiment types."""
    
    # 기존
    LATENCY_INJECTION = "latency_injection"
    ERROR_5XX = "error_5xx"
    PACKET_LOSS = "packet_loss"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    
    # 신규 추가
    ERROR_4XX = "error_4xx"
    CONNECTION_RESET = "connection_reset"
    RATE_LIMIT = "rate_limit"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    PARTIAL_FAILURE = "partial_failure"
    CASCADING_FAILURE = "cascading_failure"
```

### 3.2 Factory 함수 확장

**파일**: `packages/selfhealing-python/src/selfhealing/services/chaos/experiment_impl.py`

```python
def create_experiment(
    experiment_type: str,
    config: Optional[ExperimentConfig] = None,
    hypothesis: Optional[SteadyStateHypothesis] = None,
) -> ChaosExperiment:
    """Factory function to create experiment instances."""
    
    experiment_classes = {
        # 기존
        ExperimentType.LATENCY_INJECTION.value: LatencyInjectionExperiment,
        ExperimentType.ERROR_5XX.value: Error5xxExperiment,
        ExperimentType.PACKET_LOSS.value: PacketLossExperiment,
        ExperimentType.TIMEOUT.value: TimeoutExperiment,
        ExperimentType.RESOURCE_EXHAUSTION.value: ResourceExhaustionExperiment,
        # 신규 추가
        ExperimentType.ERROR_4XX.value: Error4xxExperiment,
        ExperimentType.CONNECTION_RESET.value: ConnectionResetExperiment,
        ExperimentType.RATE_LIMIT.value: RateLimitExperiment,
        ExperimentType.CIRCUIT_BREAKER_OPEN.value: CircuitBreakerOpenExperiment,
        ExperimentType.PARTIAL_FAILURE.value: PartialFailureExperiment,
        ExperimentType.CASCADING_FAILURE.value: CascadingFailureExperiment,
    }
    
    # ... 기존 코드
```

---

## 4. 테스트 계획

### 4.1 단위 테스트

**파일**: `tests/self_healing/chaos/test_new_experiments.py`

| 테스트 | 검증 항목 |
|--------|----------|
| `test_error_4xx_inject_and_rollback` | 4xx 에러 주입/롤백 |
| `test_connection_reset_inject` | 연결 리셋 주입 |
| `test_rate_limit_triggers_cb_open` | Rate Limit → CB OPEN |
| `test_cb_open_triggers_canary` | CB OPEN → Canary Recovery |
| `test_partial_failure_triggers_shedding` | 부분 장애 → Load Shedding |
| `test_cascading_failure_triggers_panic` | 연쇄 장애 → Panic Threshold |

### 4.2 통합 테스트

| 시나리오 | 테스트 흐름 |
|----------|------------|
| Rate Limit Storm | RateLimitExperiment → CB OPEN → Fast Fail 503 |
| Emergency Escalation | PartialFailureExperiment → Emergency Level 2 |
| Panic Recovery | CascadingFailureExperiment → Panic → Emergency Level 3 → Manual Recovery |

---

## 5. 구현 우선순위

| 우선순위 | 실험 타입 | 이유 |
|----------|----------|------|
| 🔴 P1 | `CircuitBreakerOpenExperiment` | CB + Canary 검증 핵심 |
| 🔴 P1 | `RateLimitExperiment` | Self-DDoS 방지 검증 |
| 🟠 P2 | `Error4xxExperiment` | 프론트엔드 에러 핸들링 |
| 🟠 P2 | `PartialFailureExperiment` | Load Shedding 검증 |
| 🟢 P3 | `ConnectionResetExperiment` | 네트워크 복원력 |
| 🟢 P3 | `CascadingFailureExperiment` | Panic Threshold (고위험) |

---

## 6. 아키텍트 리뷰 반영 계획

> **리뷰 일자**: 2026-01-09  
> **리뷰어**: System Architect

### 6.1 Redis Pub/Sub 실시간 동기화

**배경**: 멀티-인스턴스 환경에서 Chaos 설정 변경 실시간 전파 필요

**현재 상태**:
```python
# event_bus.py:88-90
CHAOS_EXPERIMENT_BLOCKED = "chaos_experiment_blocked"
CHAOS_EXPERIMENT_STARTED = "chaos_experiment_started"
CHAOS_EXPERIMENT_STOPPED = "chaos_experiment_stopped"
# 인메모리 구현, 단일 프로세스만 지원
```

**구현 계획**:

```python
# 파일: services/event_bus_redis.py (신규)

import redis
from typing import Callable, Dict, List
from .event_bus import EventType

class RedisEventBus:
    """
    Redis Pub/Sub 기반 분산 이벤트 버스.
    
    멀티-인스턴스 환경에서 Chaos 실험 상태 실시간 동기화.
    """
    
    CHANNEL_PREFIX = "selfhealing:events:"
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._redis = redis.from_url(redis_url)
        self._pubsub = self._redis.pubsub()
        self._subscribers: Dict[EventType, List[Callable]] = {}
    
    def subscribe(self, event_type: EventType, callback: Callable) -> None:
        """이벤트 구독."""
        channel = f"{self.CHANNEL_PREFIX}{event_type.value}"
        
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
            self._pubsub.subscribe(**{channel: self._handle_message})
        
        self._subscribers[event_type].append(callback)
    
    def publish(self, event_type: EventType, data: dict) -> None:
        """이벤트 발행 (모든 인스턴스에 전파)."""
        channel = f"{self.CHANNEL_PREFIX}{event_type.value}"
        self._redis.publish(channel, json.dumps(data))
    
    def _handle_message(self, message: dict) -> None:
        """Redis 메시지 핸들러."""
        channel = message["channel"].decode()
        event_type_str = channel.replace(self.CHANNEL_PREFIX, "")
        event_type = EventType(event_type_str)
        
        data = json.loads(message["data"])
        
        for callback in self._subscribers.get(event_type, []):
            callback(data)


# Factory function
def get_event_bus(distributed: bool = False):
    """이벤트 버스 팩토리."""
    if distributed:
        return RedisEventBus()
    else:
        from .event_bus import EventBus
        return EventBus()
```

---

### 6.2 헤더 전파 방식 분석 (OTel vs 수동)

> **질문**: OTel Baggage 없이 헤더 전파가 가능한가?

**코드 근거 분석:**

| 파일 | 현황 |
|------|------|
| `synthetic_load.py:44-45` | `SYNTHETIC_HEADER = "X-Self-Healing-Synthetic"` 정의됨 |
| `toss_payment.py:77-80` | `requests.post(..., headers=self.headers)` - 수동 헤더 전달 |
| `chaos_middleware.py:8-17` | `X-Chaos-Mode` 등 헤더 직접 읽기 |

**결론: OTel 없이도 가능하지만 수동 전파 필요**

| 방식 | 장점 | 단점 |
|------|------|------|
| **OTel Baggage** | 자동 전파, 표준화 | OTel 의존성 추가 필요 |
| **수동 전파** | 의존성 없음 | 모든 HTTP 호출에 수동 추가 필요 |

**현재 시스템 특성:**
- `requests.post/get` 직접 사용 (OTel 미적용)
- HTTP 클라이언트 래퍼 없음

**권장 구현 - `SelfHealingHttpClient` 래퍼:**

```python
# 파일: services/http_client.py (신규)

import requests
from contextvars import ContextVar
from typing import Dict, Any, Optional

# Context Variable for chaos experiment flag
_is_chaos_request: ContextVar[bool] = ContextVar("is_chaos_request", default=False)

SYNTHETIC_HEADER = "X-Self-Healing-Synthetic"


class SelfHealingHttpClient:
    """
    Self-Healing 시스템용 HTTP 클라이언트.
    
    Chaos 실험 플래그 자동 전파 (OTel 의존 없음).
    """
    
    def __init__(self, base_headers: Optional[Dict[str, str]] = None):
        self.base_headers = base_headers or {}
    
    def _get_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """요청 헤더 생성 (chaos 플래그 자동 포함)."""
        headers = {**self.base_headers}
        
        if extra_headers:
            headers.update(extra_headers)
        
        # Chaos 실험 컨텍스트 전파
        if _is_chaos_request.get():
            headers[SYNTHETIC_HEADER] = "chaos-experiment"
        
        return headers
    
    def get(self, url: str, **kwargs) -> requests.Response:
        """GET 요청."""
        headers = self._get_headers(kwargs.pop("headers", None))
        return requests.get(url, headers=headers, **kwargs)
    
    def post(self, url: str, **kwargs) -> requests.Response:
        """POST 요청."""
        headers = self._get_headers(kwargs.pop("headers", None))
        return requests.post(url, headers=headers, **kwargs)
    
    @staticmethod
    def set_chaos_context(is_chaos: bool = True):
        """Chaos 실험 컨텍스트 설정."""
        _is_chaos_request.set(is_chaos)
    
    @staticmethod
    def is_chaos_request() -> bool:
        """현재 요청이 Chaos 실험인지 확인."""
        return _is_chaos_request.get()
```

**사용 예시:**

```python
# 기존 toss_payment.py 변경 없이 사용 가능
from selfhealing.services.http_client import SelfHealingHttpClient

client = SelfHealingHttpClient(base_headers=self.headers)
response = client.post(url, json=data, timeout=30)
# ✅ Chaos 컨텍스트 시 자동으로 X-Self-Healing-Synthetic 헤더 추가
```

---

## 7. 5대 리스크 처방전 분석

> **리뷰 일자**: 2026-01-09

### 7.1 Q1. 실험 데이터 자동 태깅 (`is_chaos_experiment`)

> **네이밍 통일**: `is_chaos_simulated` → `is_chaos_experiment` (기존 함수명과 일치)

**현재 코드:**
```python
# error_budget/calculator.py:87-96
if self._get_failed_operation_stats:
    stats = self._get_failed_operation_stats(
        start_time=window_start,
        end_time=current_time,
    )
    error_count = stats.get("total_errors", 0)  # ⚠️ 필터링 없음
```

**구현 계획:**

```python
# 파일: services/error_budget/calculator.py 확장

class ErrorBudgetCalculator:
    """
    Error Budget 계산기.
    
    v1.2.0: exclude_chaos 파라미터 추가 - Chaos 실험 데이터 자동 제외
    """
    
    def calculate_budget_status(
        self,
        slo_name: str = "availability",
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
        exclude_chaos: bool = True,  # ← 신규 파라미터 (기본값 True)
    ) -> ErrorBudgetStatus:
        """
        Error Budget 상태 계산.
        
        Args:
            exclude_chaos: Chaos 실험 데이터 제외 여부 (기본: True)
        """
        # 에러 통계 조회 시 chaos 필터링
        if self._get_failed_operation_stats:
            stats = self._get_failed_operation_stats(
                start_time=window_start,
                end_time=current_time,
                exclude_chaos=exclude_chaos,  # ← 필터 전달
            )
            error_count = stats.get("total_errors", 0)


# 파일: services/error_budget/stats_provider.py 확장

def get_failed_operation_stats(
    start_time: datetime,
    end_time: datetime,
    exclude_chaos: bool = True,
) -> Dict[str, int]:
    """
    DLQ/에러 통계 조회.
    
    Args:
        exclude_chaos: is_chaos_experiment=True인 레코드 제외
    """
    query = FailedOperation.objects.filter(
        created_at__gte=start_time,
        created_at__lte=end_time,
    )
    
    if exclude_chaos:
        # metadata 필드에서 chaos 플래그 필터링
        query = query.exclude(metadata__is_chaos_experiment=True)
    
    return {
        "total_errors": query.count(),
    }
```

**Audit 자동 태깅 (주입 시점):**

```python
# 파일: services/chaos/base.py 확장

class ChaosExperiment:
    
    def execute(self) -> ExperimentResult:
        """실험 실행 (자동 태깅 포함)."""
        
        # Chaos 컨텍스트 설정 (모든 하위 Audit에 자동 태깅)
        from selfhealing.services.http_client import SelfHealingHttpClient
        SelfHealingHttpClient.set_chaos_context(is_chaos=True)
        
        try:
            # ... 기존 실행 로직
            result = self._execute_internal()
            
            # Audit 기록 시 is_chaos_experiment 자동 포함
            self._record_audit({
                "experiment_id": self.experiment_id,
                "is_chaos_experiment": True,  # ← 자동 태깅
                # ...
            })
            
            return result
        finally:
            # 컨텍스트 정리
            SelfHealingHttpClient.set_chaos_context(is_chaos=False)
```

---

### 7.2 Q2. Auto-Tuning Metric Silence (`ChaosAwareMetricsAdapter`)

> **네이밍 일관성**: `ChaosAware*` 접두사 패턴 유지 (33번 문서와 일관)

**현재 코드:**
```python
# auto_tuning/service.py:161-171
return check_all_governance(...)  # Governance 체크만, 메트릭 필터 없음
```

**분석:**

| 항목 | 판정 |
|------|------|
| 구현 가능성 | ✅ 가능 |
| 네이밍 | ⚠️ `ChaosAwareCollector` - `33_CHAOS_INDUSTRY_EXPERIMENTS.md`에 `ChaosAwareConnectionHealthMonitor` 존재 |
| 권장 패턴 | `ChaosAware*` 접두사 유지 (일관성) |

**구현 계획:**

```python
# 파일: services/auto_tuning/chaos_aware_metrics.py (신규)

class ChaosAwareMetricsAdapter:
    """
    Chaos 실험 인식 메트릭 어댑터.
    
    실험 트래픽을 메트릭 수집에서 제외하여 AutoTuning 학습 오염 방지.
    """
    
    def __init__(self, delegate: MetricsAdapter):
        self._delegate = delegate
    
    def collect_metrics(self, service: str, window_seconds: int) -> Dict[str, float]:
        """메트릭 수집 (Chaos 실험 중 스킵)."""
        
        # Chaos 실험 실행 중 확인
        if self._is_chaos_experiment_running():
            logger.info(
                f"[ChaosAwareMetrics] Skipping metrics for {service}: "
                f"Chaos experiment active"
            )
            return {}  # 빈 메트릭 반환 → 조정 없음
        
        return self._delegate.collect_metrics(service, window_seconds)
    
    def _is_chaos_experiment_running(self) -> bool:
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        try:
            scheduler = get_chaos_scheduler()
            return len(scheduler.get_running_experiments()) > 0
        except Exception:
            return False
```

**보완점:** 
- 아키텍트 제안의 "가중치 0"보다 **메트릭 수집 스킵**이 더 안전
- 오염된 데이터가 수집 자체가 안 되므로 학습 오염 완전 차단

---

### 7.3 Q3. Cgroup-Aware Limit (Resource 안전선)

**현재 코드:**
```python
# memory_test_views.py:115-250
class CgroupMemoryMonitor:
    CGROUP_V2_MAX = "/sys/fs/cgroup/memory.max"
    
    @classmethod
    def get_max_bytes(cls) -> Optional[int]:  # ✅ 이미 구현됨
```

**분석:**

| 항목 | 판정 |
|------|------|
| 구현 가능성 | ✅ 가능 - `get_max_bytes()` 활용 |
| 위치 문제 | ⚠️ `shopping/views`에 있음 → 공통 모듈로 이동 필요 |
| 마진 권장 | 10% → **15%** (OOM Killer 발동 전 버퍼) |

**구현 계획:**

```python
# 파일: services/chaos/experiment_impl.py 확장

class ResourceExhaustionExperiment(ChaosExperiment):
    
    SAFETY_MARGIN_PERCENT = 0.15  # 15% 안전 마진 (아키텍트 제안 10%+5% 버퍼)
    
    def inject_chaos(self) -> bool:
        """리소스 고갈 주입 (cgroup 안전 마진 적용)."""
        
        if self.resource_type == "memory":
            # 공통 모듈에서 import (이동 후)
            from selfhealing.core.resource_monitor import CgroupMemoryMonitor
            
            max_bytes = CgroupMemoryMonitor.get_max_bytes()
            current_bytes = CgroupMemoryMonitor.get_current_bytes()
            
            if max_bytes and current_bytes:
                available_bytes = max_bytes - current_bytes
                safe_limit = available_bytes * (1.0 - self.SAFETY_MARGIN_PERCENT)
                
                # 실제 사용 가능량 - 15%를 Hard Cap으로 설정
                requested_bytes = max_bytes * self.exhaustion_percent
                if requested_bytes > safe_limit:
                    actual_bytes = safe_limit
                    logger.warning(
                        f"[ResourceExhaustion] Capping to {actual_bytes / 1024 / 1024:.0f}MB "
                        f"(cgroup limit: {max_bytes / 1024 / 1024:.0f}MB, "
                        f"safety margin: {self.SAFETY_MARGIN_PERCENT * 100}%)"
                    )
                    self.config.parameters["exhaustion_bytes"] = int(actual_bytes)
        
        # ... 기존 주입 로직
```

**모듈 이동 계획:**

```python
# 현재: shopping/views/memory_test_views.py (잘못된 위치)
# 이동: selfhealing/core/resource_monitor.py (공통 모듈)

# 파일: selfhealing/core/resource_monitor.py (신규)

"""
Container/VM Resource Monitor.

cgroup v1/v2 지원 리소스 모니터링 유틸리티.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CgroupResourceMonitor:
    """
    Cgroup 기반 리소스 모니터.
    
    Memory, CPU 제한 감지 및 현재 사용량 조회.
    """
    
    # cgroup v2 경로 (Kubernetes 1.25+, Docker 20.10+)
    CGROUP_V2_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
    CGROUP_V2_MEMORY_CURRENT = Path("/sys/fs/cgroup/memory.current")
    
    # cgroup v1 경로 (레거시 호환)
    CGROUP_V1_MEMORY_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    CGROUP_V1_MEMORY_USAGE = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    
    @classmethod
    def get_memory_max_bytes(cls) -> Optional[int]:
        """컨테이너 메모리 제한 (bytes). None = 제한 없음."""
        # cgroup v2 먼저 시도
        if cls.CGROUP_V2_MEMORY_MAX.exists():
            content = cls.CGROUP_V2_MEMORY_MAX.read_text().strip()
            if content != "max":  # "max" = 제한 없음
                return int(content)
        
        # cgroup v1 폴백
        if cls.CGROUP_V1_MEMORY_LIMIT.exists():
            value = int(cls.CGROUP_V1_MEMORY_LIMIT.read_text().strip())
            if value < 2**63:  # 매우 큰 값 = 사실상 무제한
                return value
        
        return None
    
    @classmethod
    def get_memory_current_bytes(cls) -> Optional[int]:
        """현재 메모리 사용량 (bytes)."""
        if cls.CGROUP_V2_MEMORY_CURRENT.exists():
            return int(cls.CGROUP_V2_MEMORY_CURRENT.read_text().strip())
        
        if cls.CGROUP_V1_MEMORY_USAGE.exists():
            return int(cls.CGROUP_V1_MEMORY_USAGE.read_text().strip())
        
        return None
    
    @classmethod
    def get_available_memory_bytes(cls, safety_margin: float = 0.15) -> Optional[int]:
        """
        안전하게 사용 가능한 메모리 (bytes).
        
        Args:
            safety_margin: OOM 방지 여유분 비율 (기본 15%)
        
        Returns:
            (max - current) * (1 - safety_margin)
        """
        max_bytes = cls.get_memory_max_bytes()
        current_bytes = cls.get_memory_current_bytes()
        
        if max_bytes is None or current_bytes is None:
            return None
        
        available = max_bytes - current_bytes
        safe_available = int(available * (1.0 - safety_margin))
        
        logger.debug(
            f"[CgroupResourceMonitor] max={max_bytes}, current={current_bytes}, "
            f"available={available}, safe(margin={safety_margin})={safe_available}"
        )
        
        return max(0, safe_available)
```

---

### 7.4 Q4. Domain-Level Lock (실험 간섭 차단)

**현재 코드:**
```python
# idempotency_service.py:223-244
@classmethod
def for_chaos_experiment(cls, schedule_id, experiment_type, target_service):
    key = f"chaos:{schedule_id}:{experiment_type}:{target_service}"
    # ⚠️ schedule_id 포함 → 동일 서비스 다른 스케줄은 중복 가능
```

**락 방식 비교 분석:**

| 측면 | 스케줄 단위 (현재) | 서비스 단위 (제안) |
|------|-------------------|-------------------|
| **Key 형태** | `chaos:{schedule_id}:{type}:{service}` | `chaos:service_lock:{service}` |
| **차단 범위** | 동일 스케줄 재실행 | 동일 서비스 모든 실험 |
| **장점** | 다른 스케줄은 허용 → 유연성 | 1 서비스 = 1 실험 → 원인 명확 |
| **단점** | 다른 스케줄 동시 실행 → 원인 혼란 | 다른 팀 실험 차단 가능 |
| **용도** | 재시도 중복 방지 | 동시성 제어 |

**권장 접근: 이중 락 (Dual Lock)**

```
┌─────────────────────────────────────────────────────────────┐
│                    실험 실행 플로우                          │
├─────────────────────────────────────────────────────────────┤
│  1. Service Lock 획득 시도                                   │
│     └─ chaos:service_lock:{service}                         │
│     └─ 실패 시: "다른 실험 실행 중" 반환                      │
│                                                             │
│  2. Schedule Lock 획득 시도                                  │
│     └─ chaos:{schedule_id}:{type}:{service}                 │
│     └─ 실패 시: "이 스케줄은 이미 실행됨" 반환                │
│                                                             │
│  3. 실험 실행                                               │
│                                                             │
│  4. 종료 시: Service Lock만 해제                             │
│     └─ Schedule Lock은 TTL까지 유지 (재실행 방지)            │
└─────────────────────────────────────────────────────────────┘
```

**구현 계획:**

```python
# 파일: services/idempotency_service.py 확장

@classmethod
def for_chaos_service_lock(
    cls,
    target_service: str,
) -> "IdempotencyKey":
    """
    서비스 단위 Chaos 실험 락.
    
    동일 서비스에 2개 이상의 실험이 동시 실행되는 것을 방지.
    (원인 분석 명확성 확보)
    
    Schedule 락과 함께 사용:
    - Service Lock: 동시성 제어 (실험 종료 시 해제)
    - Schedule Lock: 재실행 방지 (TTL까지 유지)
    
    Args:
        target_service: 대상 서비스명
    
    Returns:
        IdempotencyKey for service-level chaos lock
    """
    key = f"chaos:service_lock:{target_service}"  # schedule_id 없음
    return cls(
        domain=IdempotencyDomain.CHAOS_EXPERIMENT,
        key=key,
        components={
            "lock_type": "service_level",
            "target_service": target_service,
        },
    )


# 파일: services/chaos/scheduler.py 이중 락 구현

class ChaosScheduler:
    
    def _acquire_locks(self, schedule: ChaosSchedule) -> Tuple[bool, str]:
        """
        이중 락 획득.
        
        Returns:
            (성공여부, 실패사유)
        """
        target_service = schedule.config.target_service
        
        # 1. 서비스 락 먼저 (동시성 제어)
        service_lock = IdempotencyKey.for_chaos_service_lock(target_service)
        if not self._idempotency.acquire_lock(service_lock, ttl_seconds=7200):
            return False, f"Service '{target_service}' has another experiment running"
        
        # 2. 스케줄 락 (재실행 방지)
        schedule_lock = IdempotencyKey.for_chaos_experiment(
            schedule.schedule_id,
            schedule.config.experiment_type,
            target_service,
        )
        if not self._idempotency.acquire_lock(schedule_lock, ttl_seconds=86400):
            # 서비스 락 해제 (롤백)
            self._idempotency.release_lock(service_lock)
            return False, f"Schedule '{schedule.schedule_id}' already executed"
        
        return True, ""
    
    def _release_service_lock(self, target_service: str) -> None:
        """서비스 락만 해제 (스케줄 락은 TTL 유지)."""
        service_lock = IdempotencyKey.for_chaos_service_lock(target_service)
        self._idempotency.release_lock(service_lock)
    
    def execute_now(self, schedule: ChaosSchedule) -> ExperimentResult:
        """실험 즉시 실행 (이중 락 적용)."""
        
        success, reason = self._acquire_locks(schedule)
        if not success:
            return ExperimentResult(
                success=False,
                skipped=True,
                reason=reason,
            )
        
        try:
            # 기존 실행 로직...
            return self._execute_internal(schedule)
        finally:
            # 서비스 락만 해제 (스케줄 락은 재실행 방지용으로 유지)
            self._release_service_lock(schedule.config.target_service)
```

---

### 7.5 Q5. Default Payment Blacklist

**현재 코드:**
```python
# blast_radius.py:107-110
excluded_services: List[str] = field(default_factory=list)  # ⚠️ 빈 리스트
excluded_domains: List[str] = field(default_factory=list)   # ⚠️ 빈 리스트
```

**사용자 의견 동의: ✅ 하드코딩보다 환경변수/사용자 입력이 더 좋음**

**이유:**
1. 각 회사마다 결제 서비스명이 다름 (`toss`, `toss-payment`, `payment-gateway` 등)
2. 하드코딩 시 **오탐(False Positive)** 가능 - 존재하지 않는 서비스 제외는 무의미
3. 온보딩 체크리스트에 결제 서비스 등록 단계 추가가 더 적절

**구현 계획:**

```python
# 파일: services/chaos/blast_radius.py

import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


def _get_excluded_services_from_env() -> List[str]:
    """
    환경변수에서 제외 서비스 목록 로드.
    
    환경변수: CHAOS_EXCLUDED_SERVICES
    형식: 쉼표 구분 (예: "payment,toss,iamport")
    
    Returns:
        제외할 서비스명 목록 (빈 값 필터링됨)
    """
    raw = os.getenv("CHAOS_EXCLUDED_SERVICES", "")
    services = [s.strip() for s in raw.split(",") if s.strip()]
    
    if services:
        logger.info(f"[BlastRadius] Loaded excluded services from env: {services}")
    
    return services


def _get_excluded_domains_from_env() -> List[str]:
    """
    환경변수에서 제외 도메인 목록 로드.
    
    환경변수: CHAOS_EXCLUDED_DOMAINS
    형식: 쉼표 구분 (예: "payment,billing,settlement")
    
    Returns:
        제외할 도메인명 목록 (빈 값 필터링됨)
    """
    raw = os.getenv("CHAOS_EXCLUDED_DOMAINS", "")
    domains = [d.strip() for d in raw.split(",") if d.strip()]
    
    if domains:
        logger.info(f"[BlastRadius] Loaded excluded domains from env: {domains}")
    
    return domains


@dataclass
class BlastRadiusPolicy:
    """
    Blast Radius (폭발 반경) 정책.
    
    Chaos 실험의 영향 범위를 제한하는 정책 설정.
    
    Attributes:
        excluded_services: 실험에서 제외할 서비스 목록
        excluded_domains: 실험에서 제외할 도메인 목록
        max_failure_percent: 최대 허용 실패율 (%)
        max_affected_instances: 최대 영향 인스턴스 수
    """
    
    # 기본값은 빈 리스트 유지 (하드코딩 금지)
    excluded_services: List[str] = field(default_factory=list)
    excluded_domains: List[str] = field(default_factory=list)
    
    max_failure_percent: float = 10.0
    max_affected_instances: int = 1
    
    @classmethod
    def from_env(cls) -> "BlastRadiusPolicy":
        """
        환경변수에서 정책 로드.
        
        환경변수:
            CHAOS_EXCLUDED_SERVICES: 제외 서비스 (쉼표 구분)
            CHAOS_EXCLUDED_DOMAINS: 제외 도메인 (쉼표 구분)
            CHAOS_MAX_FAILURE_PERCENT: 최대 실패율 (기본: 10.0)
            CHAOS_MAX_AFFECTED_INSTANCES: 최대 영향 인스턴스 (기본: 1)
        
        Returns:
            환경변수 기반 BlastRadiusPolicy
        
        Example:
            # .env
            CHAOS_EXCLUDED_SERVICES=payment-core,toss-payment,iamport
            CHAOS_EXCLUDED_DOMAINS=payment,billing,settlement
            CHAOS_MAX_FAILURE_PERCENT=5.0
            CHAOS_MAX_AFFECTED_INSTANCES=2
        """
        return cls(
            excluded_services=_get_excluded_services_from_env(),
            excluded_domains=_get_excluded_domains_from_env(),
            max_failure_percent=float(os.getenv("CHAOS_MAX_FAILURE_PERCENT", "10.0")),
            max_affected_instances=int(os.getenv("CHAOS_MAX_AFFECTED_INSTANCES", "1")),
        )
    
    def is_service_allowed(self, service_name: str) -> bool:
        """서비스가 Chaos 실험 대상으로 허용되는지 확인."""
        if service_name in self.excluded_services:
            logger.warning(
                f"[BlastRadius] Service '{service_name}' is in excluded list"
            )
            return False
        
        # 도메인 패턴 매칭 (서비스명에 도메인이 포함된 경우)
        for domain in self.excluded_domains:
            if domain.lower() in service_name.lower():
                logger.warning(
                    f"[BlastRadius] Service '{service_name}' matches "
                    f"excluded domain '{domain}'"
                )
                return False
        
        return True
```

**온보딩 문서 추가 권장:**

```markdown
## Chaos Engineering 설정 체크리스트

### 필수: 결제/인증 서비스 보호 설정

카오스 실험에서 제외할 **결제/인증 서비스**를 환경변수로 지정하세요:

```bash
# .env 또는 docker-compose.yml
CHAOS_EXCLUDED_SERVICES=payment-core,toss-payment,iamport,auth-service
CHAOS_EXCLUDED_DOMAINS=payment,billing,settlement,authentication

# 선택: 실험 범위 제한
CHAOS_MAX_FAILURE_PERCENT=5.0      # 최대 5% 실패율
CHAOS_MAX_AFFECTED_INSTANCES=1     # 최대 1개 인스턴스만 영향
```

⚠️ **경고**: 이 설정 없이 카오스 실험을 실행하면 결제/인증 서비스에 장애가 주입될 수 있습니다.

### 설정 검증

```bash
# Python에서 설정 확인
python -c "
from selfhealing.services.chaos.blast_radius import BlastRadiusPolicy
policy = BlastRadiusPolicy.from_env()
print(f'Excluded services: {policy.excluded_services}')
print(f'Excluded domains: {policy.excluded_domains}')
"
```
```

---

### 7.6 추가 분석: OpenTelemetry 의존성

> **질문**: OTel을 강제해도 괜찮은가? 의존성 최소화 목표와 충돌하는가?

**코드 근거:**
```python
# test_opentelemetry_adapter.py:35-51
def test_import_without_opentelemetry_does_not_raise():
    """OTel 미설치 시에도 임포트 가능 확인."""
    # NoOpOpenTelemetryAdapter 사용으로 OTel 없이도 동작
    pass
```

**분석:**

| 항목 | 현재 상태 |
|------|----------|
| OTel 필수 여부 | ❌ 선택적 의존성 |
| 미설치 시 동작 | ✅ `NoOpOpenTelemetryAdapter` 사용 |
| 기능 영향 | 트레이싱 없음 (핵심 기능 정상) |

**결론: OTel 강제 ❌ 불필요**

1. `NoOpOpenTelemetryAdapter` 패턴으로 OTel 없이도 동일 동작
2. 의존성 최소화 목표와 충돌 없음
3. Trace 전파가 필요한 경우: `SelfHealingHttpClient`로 수동 헤더 전파

---

#### 7.6.1 OTel 자동 전파 vs 수동 전파

| 방식 | 의존성 | 구현 노력 | 전파 범위 |
|------|--------|----------|----------|
| **수동 전파** | 없음 | 낮음 | 명시적 호출만 |
| **OTel 자동 전파** | 추가 패키지 필요 | 중간 | HTTP/DB/Celery 등 자동 |

**⚠️ 현재 상태: 자동 전파 미구현**

```python
# adapter.py:150-180 (현재 구현)
# WHY WE DON'T SET TRACER PROVIDER:
# The application/platform owns OpenTelemetry configuration.
# We only get a tracer from the existing provider.
```

현재 selfhealing 패키지는:
- ✅ **이벤트 방출(emit)**: 기존 span에 이벤트 붙이기 지원
- ❌ **자동 전파(auto-instrumentation)**: 미구현 - 애플리케이션 책임

---

#### 7.6.2 OTel 자동 전파 활성화 방법

**OTel을 붙이면 자동 전파가 가능한가?** → ❌ **추가 설정 필요**

OTel API만 설치해서는 자동 전파가 되지 않습니다. 다음 작업이 필요합니다:

**1단계: 추가 패키지 설치**
```bash
pip install opentelemetry-api \
            opentelemetry-sdk \
            opentelemetry-instrumentation-django \
            opentelemetry-instrumentation-requests \
            opentelemetry-instrumentation-celery \
            opentelemetry-exporter-otlp
```

**2단계: 애플리케이션 설정 (settings.py 또는 wsgi.py)**
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.celery import CeleryInstrumentor

# TracerProvider 설정 (애플리케이션 책임 - selfhealing이 설정하지 않음)
provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

# Auto-Instrumentation 활성화 → 이후 자동 전파
DjangoInstrumentor().instrument()      # Django HTTP 요청
RequestsInstrumentor().instrument()    # requests 라이브러리 외부 호출
CeleryInstrumentor().instrument()      # Celery 태스크
```

**3단계: 자동 전파 동작 원리**
```
┌─────────────────────────────────────────────────────────────────────┐
│  Django Request (자동 Span 생성)                                     │
│    └─ DjangoInstrumentor가 X-Request-ID, traceparent 자동 추출       │
│                                                                     │
│  → requests.get() 호출 (자동 헤더 주입)                               │
│    └─ RequestsInstrumentor가 traceparent 헤더 자동 추가              │
│                                                                     │
│  → Celery task.delay() (자동 컨텍스트 전파)                           │
│    └─ CeleryInstrumentor가 태스크 헤더에 trace context 주입          │
└─────────────────────────────────────────────────────────────────────┘
```

---

#### 7.6.3 권장 접근법

| 상황 | 권장 |
|------|------|
| 의존성 최소화 우선 | **수동 전파** (`SelfHealingHttpClient`) |
| 풀 옵저버빌리티 필요 | **OTel 자동 전파** (위 설정 적용) |
| Chaos 실험 추적만 필요 | **수동 전파** (충분함) |

**수동 전파 예시 (의존성 없음):**
```python
from selfhealing.services.http_client import SelfHealingHttpClient

client = SelfHealingHttpClient()
response = client.get(url, headers={
    "X-Request-ID": request_id,
    "X-Trace-ID": trace_id,
    "X-Chaos-Experiment": "true",  # Chaos 컨텍스트 전파
})
```

---

## 8. 가설 기반 검증 (Hypothesis Validation) - 제안

> **아키텍트 제안**: `ExperimentConfig`에 `expected_outcome` 필드 추가로 Resilience 자동 채점

### 8.1 현재 상태 분석

**현재 구현된 `SteadyStateHypothesis`:**

```python
# base.py:229-282 (현재)
@dataclass
class SteadyStateHypothesis:
    p50_latency_max_ms: float = 100.0
    p99_latency_max_ms: float = 500.0
    error_rate_max_percent: float = 0.1
    throughput_min_rps: float = 100.0
    # → 시스템 "상태(State)" 검증만 가능
```

| 현재 기능 | 한계 |
|----------|------|
| ✅ 메트릭 임계값 검증 | ❌ 시스템 **행동(Behavior)** 검증 불가 |
| ✅ 실험 전/후 상태 비교 | ❌ "CB가 OPEN 되어야 함" 같은 기대 표현 불가 |
| ✅ `steady_state_hypothesis_passed` | ❌ **왜** 실패했는지 Resilience 관점 분석 없음 |

---

### 8.2 제안 분석: 찬성 + 보완

**✅ 찬성 이유:**

1. **Chaos Engineering 철학 정렬**: Netflix ChAP의 핵심 = "Hypothesis → Experiment → Validation"
2. **자동 채점**: "장애 주입 성공" → "**시스템 방어 성공/실패**" 판정
3. **SLO 연계**: Error Budget 소진 없이 Resilience 검증 가능

**📝 보완 제안:**

| 아키텍트 제안 | 보완점 |
|-------------|--------|
| `expected_outcome: str` | → `ResilienceExpectation` **구조화된 클래스** |
| 단일 텍스트 기대값 | → **다중 Assertion** 지원 |
| 암묵적 검증 | → **프로그래밍적 Validator** 지원 |

---

### 8.3 구현 계획

```python
# 파일: services/chaos/resilience_expectation.py (신규)

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from datetime import timedelta


class ExpectationType(str, Enum):
    """기대 유형."""
    
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    """CB가 OPEN 상태로 전환되어야 함."""
    
    CIRCUIT_BREAKER_HALF_OPEN = "circuit_breaker_half_open"
    """CB가 HALF_OPEN 상태로 전환되어야 함."""
    
    FALLBACK_ACTIVATED = "fallback_activated"
    """Fallback이 활성화되어야 함."""
    
    RETRY_TRIGGERED = "retry_triggered"
    """Retry가 발동되어야 함."""
    
    RATE_LIMIT_ACTIVATED = "rate_limit_activated"
    """Rate Limit이 활성화되어야 함."""
    
    AUTO_SCALE_TRIGGERED = "auto_scale_triggered"
    """Auto-scaling이 발동되어야 함."""
    
    ALERT_FIRED = "alert_fired"
    """Alert이 발생해야 함."""
    
    CUSTOM = "custom"
    """사용자 정의 검증."""


@dataclass
class ResilienceAssertion:
    """
    단일 Resilience 기대 조건.
    
    예: "CB가 10초 내에 OPEN 되어야 함"
    """
    
    expectation_type: ExpectationType
    """기대 유형."""
    
    target_service: str = ""
    """대상 서비스 (선택)."""
    
    expected_within_seconds: float = 30.0
    """기대 시간 내 발생 (초)."""
    
    expected_state: str = ""
    """기대 상태 (CB의 경우 "open", "half_open" 등)."""
    
    custom_validator: Optional[Callable[[Dict[str, Any]], bool]] = None
    """사용자 정의 검증 함수 (CUSTOM 타입용)."""
    
    description: str = ""
    """사람이 읽을 수 있는 설명."""
    
    def __post_init__(self):
        if not self.description:
            self.description = self._generate_description()
    
    def _generate_description(self) -> str:
        """자동 설명 생성."""
        if self.expectation_type == ExpectationType.CIRCUIT_BREAKER_OPEN:
            return f"CB should OPEN within {self.expected_within_seconds}s"
        elif self.expectation_type == ExpectationType.FALLBACK_ACTIVATED:
            return f"Fallback should activate within {self.expected_within_seconds}s"
        elif self.expectation_type == ExpectationType.RETRY_TRIGGERED:
            return f"Retry should trigger within {self.expected_within_seconds}s"
        return f"{self.expectation_type.value} expected"


@dataclass  
class ResilienceExpectation:
    """
    Chaos 실험의 Resilience 기대값 집합.
    
    "시스템이 이 장애에 대해 어떻게 반응해야 하는가?"를 정의.
    """
    
    assertions: List[ResilienceAssertion] = field(default_factory=list)
    """검증할 Assertion 목록."""
    
    require_all: bool = True
    """True: 모든 Assertion 통과 필요, False: 하나라도 통과하면 성공."""
    
    description: str = ""
    """전체 기대값 설명."""
    
    @classmethod
    def expect_cb_open(
        cls,
        target_service: str,
        within_seconds: float = 10.0,
    ) -> "ResilienceExpectation":
        """편의 팩토리: CB OPEN 기대."""
        return cls(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                    target_service=target_service,
                    expected_within_seconds=within_seconds,
                    expected_state="open",
                )
            ],
            description=f"Expect {target_service} CB to OPEN within {within_seconds}s",
        )
    
    @classmethod
    def expect_graceful_degradation(
        cls,
        target_service: str,
        within_seconds: float = 30.0,
    ) -> "ResilienceExpectation":
        """편의 팩토리: Graceful Degradation 기대 (CB + Fallback)."""
        return cls(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                    target_service=target_service,
                    expected_within_seconds=within_seconds,
                ),
                ResilienceAssertion(
                    expectation_type=ExpectationType.FALLBACK_ACTIVATED,
                    target_service=target_service,
                    expected_within_seconds=within_seconds,
                ),
            ],
            require_all=True,
            description=f"Expect graceful degradation for {target_service}",
        )


@dataclass
class ResilienceValidationResult:
    """Resilience 검증 결과."""
    
    passed: bool
    """전체 통과 여부."""
    
    assertion_results: List[Dict[str, Any]] = field(default_factory=list)
    """개별 Assertion 결과."""
    
    total_assertions: int = 0
    passed_assertions: int = 0
    failed_assertions: int = 0
    
    resilience_score: float = 0.0
    """0.0 ~ 1.0 Resilience 점수."""
    
    summary: str = ""
    """결과 요약."""
    
    def __post_init__(self):
        if self.total_assertions > 0:
            self.resilience_score = self.passed_assertions / self.total_assertions
            self.summary = (
                f"Resilience: {self.passed_assertions}/{self.total_assertions} "
                f"({self.resilience_score * 100:.0f}%)"
            )
```

---

### 8.4 ExperimentConfig 확장

```python
# 파일: services/chaos/base.py 확장

@dataclass
class ExperimentConfig:
    """Configuration for a chaos experiment."""
    
    # ... 기존 필드들 ...
    
    # 신규: Resilience 기대값
    resilience_expectation: Optional["ResilienceExpectation"] = None
    """
    시스템이 이 장애에 대해 어떻게 반응해야 하는지 정의.
    
    예시:
        ResilienceExpectation.expect_cb_open("payment-api", within_seconds=10)
        → "지연 500ms 주입 시, CB가 10초 내에 OPEN되어야 함"
    
    None이면 Resilience 검증 스킵 (기존 동작 유지).
    """
```

---

### 8.5 ExperimentResult 확장

```python
# 파일: services/chaos/base.py 확장

@dataclass
class ExperimentResult:
    """Result of a chaos experiment execution."""
    
    # ... 기존 필드들 ...
    
    # 신규: Resilience 검증 결과
    resilience_validation: Optional[Dict[str, Any]] = None
    """
    Resilience 기대값 검증 결과.
    
    {
        "passed": True/False,
        "resilience_score": 0.0 ~ 1.0,
        "assertions": [...],
        "summary": "Resilience: 2/3 (67%)"
    }
    """
    
    resilience_passed: bool = True
    """Resilience 검증 통과 여부. expectation이 없으면 True."""
```

---

### 8.6 사용 예시

```python
from selfhealing.services.chaos import (
    ExperimentConfig,
    LatencyInjectionExperiment,
    ResilienceExpectation,
    ResilienceAssertion,
    ExpectationType,
)

# 1. 간단한 사용: CB OPEN 기대
config = ExperimentConfig(
    target_service="payment-api",
    parameters={"latency_ms": 500},
    resilience_expectation=ResilienceExpectation.expect_cb_open(
        target_service="payment-api",
        within_seconds=10.0,
    ),
)

experiment = LatencyInjectionExperiment(config=config)
result = experiment.execute()

# 결과 확인
if result.resilience_passed:
    print("✅ Resilience Pass: 시스템이 예상대로 방어에 성공")
else:
    print("❌ Resilience Fail: 방어 메커니즘 미작동")
    print(f"   Score: {result.resilience_validation['resilience_score'] * 100:.0f}%")


# 2. 복잡한 사용: 다중 Assertion
config = ExperimentConfig(
    target_service="order-api",
    parameters={"error_rate": 0.5},
    resilience_expectation=ResilienceExpectation(
        assertions=[
            ResilienceAssertion(
                expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                target_service="order-api",
                expected_within_seconds=10,
            ),
            ResilienceAssertion(
                expectation_type=ExpectationType.FALLBACK_ACTIVATED,
                target_service="order-api",
                expected_within_seconds=15,
            ),
            ResilienceAssertion(
                expectation_type=ExpectationType.ALERT_FIRED,
                expected_within_seconds=60,
                description="PagerDuty alert should fire",
            ),
        ],
        require_all=True,
        description="Full graceful degradation with alerting",
    ),
)
```

---

### 8.7 가치 요약

| 항목 | Before | After |
|------|--------|-------|
| **실험 결과** | "장애 주입 성공" | "**Resilience Pass/Fail**" |
| **판정 기준** | 메트릭 임계값 | **행동 기대값** (CB, Fallback 등) |
| **자동화** | 수동 검토 필요 | **자동 채점** |
| **말할 수 있는 것** | "우리는 장애를 주입한다" | "**시스템 방어 기제가 작동하는지 자동 채점한다**" |

---

### 8.8 구현 우선순위

| 단계 | 내용 | 복잡도 |
|------|------|--------|
| **Phase 1** | `ResilienceExpectation` 데이터 클래스 | 낮음 |
| **Phase 2** | CB 상태 조회 연동 | 중간 |
| **Phase 3** | 실시간 이벤트 수집 (Fallback, Retry 등) | 높음 |
| **Phase 4** | Alert 시스템 연동 (PagerDuty, Slack) | 높음 |

**Phase 1은 즉시 구현 가능** - 데이터 구조만 추가하면 됨.

---

## 관련 문서

| 문서 | 설명 |
|------|------|
| [32_CHAOS_SYSTEM_INTEGRATION.md](32_CHAOS_SYSTEM_INTEGRATION.md) | 힐링 시스템 연동 계획 |
| [33_CHAOS_INDUSTRY_EXPERIMENTS.md](33_CHAOS_INDUSTRY_EXPERIMENTS.md) | 업계 표준 실험 추가 |
| [24_CHAOS_INTEGRATION_PLAN.md](24_CHAOS_INTEGRATION_PLAN.md) | 기존 통합 계획 |

---

## 버전 정보

- **현재 버전**: 1.7.0
- **마지막 업데이트**: 2026-01-10
- **변경 사항**: 
  - v1.1.0: 아키텍트 1차 리뷰 반영 (6.1-6.8 섹션)
  - v1.2.0: 아키텍트 2차 리뷰 반영 (6.1-6.2 재구성, 7.1-7.5 섹션 추가)
  - v1.3.0: Q1-Q5 완전 구현 계획 추가, OTel 분석 (7.6), 네이밍 통일, 이중 락 패턴
  - v1.4.0: OTel 자동 전파 vs 수동 전파 상세 분석 (7.6.1-7.6.3)
  - v1.5.0: 가설 기반 검증 (Hypothesis Validation) 설계 추가 (8.1-8.8)
  - v1.6.0: 📋 통합 구현 로드맵 추가 (Phase 0-7 단계별 구현 순서, 의존성 다이어그램, 마일스톤)
  - **v1.7.0: ✅ Phase 6-7 구현 완료 (ResilienceExpectation, ResilienceValidator, 통합 테스트)**
- **담당자**: SelfHealing Team
- **담당자**: SelfHealing Team
