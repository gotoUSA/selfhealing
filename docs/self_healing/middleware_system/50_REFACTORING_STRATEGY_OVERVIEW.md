# 50. 코드 분리 리팩토링 전략: 개요

> 작성일: 2026-01-18  
> 대상: `packages/selfhealing-python`

---

## 1. 현황 분석 요약

### 1.1 소스 코드 파일 크기 분포

| 줄 수 범위 | 파일 수 | 리팩토링 필요성 |
|-----------|---------|----------------|
| 2,000줄 이상 | 1개 | 🔴 긴급 분리 |
| 1,500~2,000줄 | 3개 | 🔴 긴급 분리 |
| 1,000~1,500줄 | 9개 | 🟠 조속 분리 |
| 500~1,000줄 | 약 30개 | 🟡 권장 분리 |
| 300~500줄 | 다수 | 🟢 허용 범위 |

### 1.2 테스트 코드 파일 크기 분포

| 줄 수 범위 | 파일 수 | 리팩토링 필요성 |
|-----------|---------|----------------|
| 1,500줄 이상 | 1개 | 🔴 긴급 분리 |
| 1,000~1,500줄 | 5개 | 🔴 긴급 분리 |
| 600~1,000줄 | 약 15개 | 🟠 조속 분리 |
| 400~600줄 | 약 20개 | 🟡 권장 분리 |
| 300줄 이하 | 다수 | 🟢 이상적 |

---

## 2. 리팩토링 목표 기준

### 2.1 줄 수 기준

| 파일 유형 | 이상적 | 최대 허용 | 분리 필수 |
|----------|--------|----------|----------|
| 소스 코드 | 200~300줄 | 500줄 | 800줄 초과 |
| 테스트 코드 | 200~300줄 | 400줄 | 600줄 초과 |

### 2.2 구조적 기준

| 지표 | 소스 코드 권장 | 테스트 코드 권장 |
|-----|--------------|----------------|
| 클래스 수 | 1~3개 | 3~5개 |
| 공개 함수 수 | 5~10개 | N/A |
| 테스트 메서드 수 | N/A | 10~15개 |
| 책임 영역 | 단일 책임 | 단일 기능 검증 |

---

## 3. 분리 원칙

### 3.1 단일 책임 원칙 (SRP)

- **소스 코드**: 하나의 파일은 하나의 핵심 도메인 개념만 담당
- **테스트 코드**: 하나의 테스트 파일은 하나의 구체적 동작 범주만 검증

### 3.2 응집도 원칙

분리 시 응집도 유지 기준:
1. **기능적 응집**: 동일 기능을 수행하는 요소는 같은 파일에
2. **통신적 응집**: 동일 데이터를 다루는 요소는 같은 파일에
3. **순차적 응집**: 연속된 처리 단계는 같은 파일에

### 3.3 결합도 최소화 원칙

분리 후 파일 간 결합도 관리:
1. **공유 타입**: 별도의 `types.py` 또는 `models.py`로 분리
2. **공통 유틸**: 별도의 `utils.py` 또는 `helpers.py`로 분리
3. **순환 참조 금지**: 분리된 파일 간 순환 import 불허

---

## 4. 분리 유형별 패턴

### 4.1 수평 분리 (Horizontal Split)

동일 추상화 수준의 병렬적 구현체를 분리.

**적용 대상**: `experiment_impl.py` (21개 Experiment 클래스)

```
분리 전: experiment_impl.py (2,812줄)
         └── 21개 ChaosExperiment 구현체

분리 후: experiments/
         ├── __init__.py
         ├── latency.py          # LatencyInjectionExperiment
         ├── errors.py           # Error5xxExperiment, Error4xxExperiment
         ├── network.py          # PacketLossExperiment, ConnectionResetExperiment, ...
         ├── timeout.py          # TimeoutExperiment
         ├── resource.py         # ResourceExhaustionExperiment, PoolExhaustionExperiment
         ├── circuit_breaker.py  # CircuitBreakerOpenExperiment
         ├── rate_limit.py       # RateLimitExperiment
         ├── cascade.py          # CascadingFailureExperiment, PartialFailureExperiment
         ├── infrastructure.py   # DNSFailureExperiment, ClockSkewExperiment, ...
         └── audit.py            # AuditStorageFailureExperiment
```

### 4.2 수직 분리 (Vertical Split)

다른 추상화 수준 또는 다른 책임 영역을 분리.

**적용 대상**: `integrity.py` (1,815줄)

```
분리 전: integrity.py (1,815줄)
         ├── IntegrityInfo (데이터 클래스)
         ├── HashChainVerifier (검증)
         ├── HashChainManager (로컬 관리)
         ├── RedisHashChainManager (분산 관리)
         ├── PendingSequenceManager (시퀀스 관리)
         ├── DailyHashAnchor (앵커 관리)
         ├── StartupHashChainSync (동기화)
         └── HashChainReconciler (조정)

분리 후: integrity/
         ├── __init__.py
         ├── models.py           # IntegrityInfo
         ├── verifier.py         # HashChainVerifier
         ├── manager.py          # HashChainManager, HashChainManagerProtocol
         ├── redis_manager.py    # RedisHashChainManager
         ├── sequence.py         # PendingSequenceManager
         ├── anchor.py           # DailyHashAnchor
         ├── sync.py             # StartupHashChainSync
         └── reconciler.py       # HashChainReconciler
```

### 4.3 레이어 분리 (Layer Split)

미들웨어 또는 여러 관심사가 섞인 파일을 레이어별로 분리.

**적용 대상**: `middleware.py` (1,424줄)

```
분리 전: middleware.py (1,424줄)
         ├── HealthBridgeMiddleware (헬스체크)
         ├── AccessLogEntry (데이터)
         ├── SensitiveEndpointAccessLogger (로깅)
         ├── SensitiveAccessLoggingMiddleware (보안 로깅)
         ├── FailSecureIsAuthenticated (인증)
         ├── FailSecureIsAdminUser (권한)
         ├── SelfHealingMiddleware (핵심)
         └── SelfHealingRecoveryLogger (복구 로깅)

분리 후: middleware/
         ├── __init__.py
         ├── health.py           # HealthBridgeMiddleware
         ├── security.py         # FailSecureIsAuthenticated, FailSecureIsAdminUser
         ├── access_log.py       # AccessLogEntry, SensitiveEndpointAccessLogger, SensitiveAccessLoggingMiddleware
         ├── selfhealing.py      # SelfHealingMiddleware
         └── recovery_logger.py  # SelfHealingRecoveryLogger
```

---

## 5. 테스트 분리 패턴

### 5.1 테스트 대상별 분리

하나의 테스트 파일이 여러 클래스를 테스트하는 경우.

**적용 대상**: `test_audit_forensic_bridge.py` (1,787줄, 14개 테스트 클래스)

```
분리 전: test_audit_forensic_bridge.py (1,787줄)
         ├── TestAuditEventTypeAdditions (이벤트 타입)
         ├── TestCorruptionShieldAuditIntegration (Corruption)
         ├── TestShadowLoggerAuditIntegration (Shadow)
         ├── TestWALAuditIntegration (WAL)
         ├── TestForensicAuditBridge (Bridge)
         ├── TestAuditIntegrationEnd2End (E2E)
         ├── TestCorruptionShieldBatching (배치)
         ├── TestAuditContextAutoInjection (컨텍스트)
         ├── TestForensicMasking (마스킹)
         ├── TestInMemoryAuditBuffer (버퍼)
         ├── TestForensicRateLimiter (Rate Limiter)
         ├── TestRedisAuditBuffer (Redis)
         └── TestMTTRCalculator (MTTR)

분리 후: audit/
         ├── test_audit_event_types.py          # TestAuditEventTypeAdditions
         ├── test_corruption_shield.py          # TestCorruptionShieldAuditIntegration, TestCorruptionShieldBatching
         ├── test_shadow_logger.py              # TestShadowLoggerAuditIntegration
         ├── test_wal_integration.py            # TestWALAuditIntegration
         ├── test_forensic_bridge.py            # TestForensicAuditBridge, TestForensicMasking
         ├── test_audit_context.py              # TestAuditContextAutoInjection
         ├── test_audit_buffer.py               # TestInMemoryAuditBuffer, TestRedisAuditBuffer
         ├── test_forensic_rate_limiter.py      # TestForensicRateLimiter
         ├── test_mttr_calculator.py            # TestMTTRCalculator
         └── test_audit_e2e.py                  # TestAuditIntegrationEnd2End
```

### 5.2 시나리오별 분리

동일 클래스에 대한 테스트가 다양한 시나리오로 구성된 경우.

```
분리 기준:
├── *_basic.py          # 기본 동작, happy path
├── *_error.py          # 에러 처리, 예외 상황
├── *_edge_cases.py     # 경계 조건, 극한 상황
├── *_integration.py    # 다른 컴포넌트와 통합
└── *_performance.py    # 성능, 부하 테스트
```

---

## 6. 우선순위 및 로드맵

### Phase 1: 긴급 분리 (1,500줄 이상)

| 순번 | 파일 | 줄 수 | 분리 유형 |
|-----|------|------|----------|
| 1 | experiment_impl.py | 2,812 | 수평 분리 |
| 2 | integrity.py | 1,815 | 수직 분리 |
| 3 | test_audit_forensic_bridge.py | 1,787 | 대상별 분리 |
| 4 | chaos/base.py | 1,652 | 수직 분리 |
| 5 | hash_chain_graceful_degradation.py | 1,625 | 수직 분리 |

### Phase 2: 조속 분리 (1,000~1,500줄)

| 순번 | 파일 | 줄 수 | 분리 유형 |
|-----|------|------|----------|
| 6 | middleware.py | 1,424 | 레이어 분리 |
| 7 | test_error_budget_gate.py | 1,369 | 대상별 분리 |
| 8 | security_violation_service.py | 1,305 | 수직 분리 |
| 9 | hash_chain_performance.py | 1,259 | 수직 분리 |
| 10 | security_notification_service.py | 1,170 | 수직 분리 |
| 11 | load_shedding.py | 1,164 | 수직 분리 |
| 12 | tasks.py | 1,140 | 도메인별 분리 |
| 13 | safety_guard.py | 1,111 | 수직 분리 |
| 14 | test_shadow_budget_weighted.py | 1,128 | 시나리오별 분리 |
| 15 | test_hash_chain_graceful_degradation.py | 1,049 | 대상별 분리 |

### Phase 3: 권장 분리 (600~1,000줄)

약 20개 파일 대상

---

## 7. 분리 작업 체크리스트

각 파일 분리 시 수행할 항목:

### 7.1 분리 전 준비

- [ ] 현재 파일의 모든 클래스/함수 목록 작성
- [ ] 클래스/함수 간 의존성 그래프 작성
- [ ] 공유되는 타입/상수 식별
- [ ] 순환 참조 위험 지점 식별

### 7.2 분리 실행

- [ ] 공유 타입을 별도 파일로 추출
- [ ] 클래스/함수를 새 파일로 이동
- [ ] `__init__.py`에 공개 API 정의
- [ ] 기존 import 경로 호환성 유지

### 7.3 분리 후 검증

- [ ] 모든 기존 테스트 통과 확인
- [ ] 순환 참조 없음 확인
- [ ] import 경로 마이그레이션 가이드 작성
- [ ] 문서 업데이트

---

## 8. 관련 문서

- [51_REFACTORING_SOURCE_CODE.md](51_REFACTORING_SOURCE_CODE.md) - 소스 코드 상세 분리 전략
- [52_REFACTORING_TEST_CODE.md](52_REFACTORING_TEST_CODE.md) - 테스트 코드 상세 분리 전략
