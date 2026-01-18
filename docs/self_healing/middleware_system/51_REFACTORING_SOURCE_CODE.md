# 51. 소스 코드 분리 전략 상세

> 작성일: 2026-01-18  
> 대상: `packages/selfhealing-python/src/selfhealing`
> 최종 업데이트: 2025-06-16

## 진행 상태

| Phase | 대상 파일 | 상태 | 테스트 | 완료일 |
|-------|----------|------|--------|--------|
| Phase 1-1 | experiment_impl.py → experiments/ | ✅ 완료 | 통과 | 2025-06-16 |
| Phase 1-2 | integrity.py → integrity/ | ✅ 완료 | 12개 통과 | 2025-06-16 |
| Phase 2-1 | hash_chain_graceful_degradation.py → graceful_degradation/ | ✅ 완료 | 50개 통과 | 2025-06-16 |
| Phase 2-2 | resilience.py → resilience/ | ✅ 완료 | 38개 통과 | 2025-06-16 |
| Phase 3 | middleware.py, security_violation_service.py | 미시작 | - | - |
| Phase 4 | chaos/base.py, load_shedding.py, safety_guard.py | 미시작 | - | - |
| Phase 5 | tasks.py, scheduler.py | 미시작 | - | - |

---

## 1. experiment_impl.py (2,812줄) → 수평 분리

### 1.1 현재 구조 분석

| 라인 범위 | 클래스명 | 실험 유형 |
|----------|---------|----------|
| 39-210 | FailureHypothesis | 공통 데이터 클래스 |
| 212-308 | LatencyInjectionExperiment | 지연 주입 |
| 309-398 | Error5xxExperiment | 5xx 에러 |
| 399-466 | PacketLossExperiment | 패킷 손실 |
| 467-536 | TimeoutExperiment | 타임아웃 |
| 537-666 | ResourceExhaustionExperiment | 리소스 고갈 |
| 667-894 | CircuitBreakerOpenExperiment | CB 강제 Open |
| 895-981 | RateLimitExperiment | Rate Limit |
| 982-1076 | Error4xxExperiment | 4xx 에러 |
| 1077-1251 | PartialFailureExperiment | 부분 장애 |
| 1252-1327 | ConnectionResetExperiment | 연결 리셋 |
| 1328-1478 | CascadingFailureExperiment | 연쇄 장애 |
| 1479-1621 | PoolExhaustionExperiment | 풀 고갈 |
| 1622-1770 | ConnectionPartitionExperiment | 네트워크 파티션 |
| 1771-1917 | CertificateExpiryExperiment | 인증서 만료 |
| 1918-2068 | ClockSkewExperiment | 시간 불일치 |
| 2069-2169 | DNSFailureExperiment | DNS 장애 |
| 2170-2257 | NetworkBlackholeExperiment | 네트워크 블랙홀 |
| 2258-2354 | SimulatedDiskIOExperiment | 디스크 I/O |
| 2355-2452 | SimulatedTLSFailureExperiment | TLS 실패 |
| 2453-2813 | AuditStorageFailureExperiment | Audit 저장소 장애 |

### 1.2 분리 전략: 도메인별 그룹화

```
services/chaos/experiments/
├── __init__.py           # 공개 API 및 모든 Experiment re-export
├── hypothesis.py         # FailureHypothesis, 공통 hypothesis 상수들
├── latency.py            # LatencyInjectionExperiment
├── http_errors.py        # Error5xxExperiment, Error4xxExperiment
├── network.py            # PacketLossExperiment, ConnectionResetExperiment,
│                         # NetworkBlackholeExperiment, ConnectionPartitionExperiment
├── timeout.py            # TimeoutExperiment
├── resource.py           # ResourceExhaustionExperiment, PoolExhaustionExperiment
├── circuit_breaker.py    # CircuitBreakerOpenExperiment
├── rate_limit.py         # RateLimitExperiment
├── cascade.py            # CascadingFailureExperiment, PartialFailureExperiment
├── infrastructure.py     # CertificateExpiryExperiment, ClockSkewExperiment,
│                         # DNSFailureExperiment, SimulatedDiskIOExperiment,
│                         # SimulatedTLSFailureExperiment
└── audit.py              # AuditStorageFailureExperiment
```

### 1.3 분리 근거

**도메인별 그룹화 이유**:

1. **HTTP 에러 그룹 (http_errors.py)**
   - Error5xxExperiment와 Error4xxExperiment는 동일한 HTTP 응답 조작 메커니즘 사용
   - 공통 상수: HTTP 상태 코드 맵핑

2. **네트워크 그룹 (network.py)**
   - PacketLossExperiment, ConnectionResetExperiment, NetworkBlackholeExperiment, ConnectionPartitionExperiment
   - 모두 네트워크 계층 장애 시뮬레이션
   - 공통 패턴: 소켓/연결 수준 조작

3. **리소스 그룹 (resource.py)**
   - ResourceExhaustionExperiment, PoolExhaustionExperiment
   - 공통 패턴: 리소스 풀 점유 및 해제

4. **연쇄 장애 그룹 (cascade.py)**
   - CascadingFailureExperiment, PartialFailureExperiment
   - 공통 패턴: 다중 서비스 장애 전파 시뮬레이션

5. **인프라 그룹 (infrastructure.py)**
   - CertificateExpiryExperiment, ClockSkewExperiment, DNSFailureExperiment
   - SimulatedDiskIOExperiment, SimulatedTLSFailureExperiment
   - 공통 패턴: 시스템 인프라 수준 장애

### 1.4 의존성 및 import 구조

```
experiments/__init__.py:
    from .hypothesis import FailureHypothesis, LATENCY_INJECTION_HYPOTHESIS, ...
    from .latency import LatencyInjectionExperiment
    from .http_errors import Error5xxExperiment, Error4xxExperiment
    from .network import PacketLossExperiment, ConnectionResetExperiment, ...
    from .timeout import TimeoutExperiment
    from .resource import ResourceExhaustionExperiment, PoolExhaustionExperiment
    from .circuit_breaker import CircuitBreakerOpenExperiment
    from .rate_limit import RateLimitExperiment
    from .cascade import CascadingFailureExperiment, PartialFailureExperiment
    from .infrastructure import CertificateExpiryExperiment, ClockSkewExperiment, ...
    from .audit import AuditStorageFailureExperiment
    
    __all__ = [...]  # 모든 Experiment 클래스
```

### 1.5 호환성 유지

기존 import 경로 호환을 위해 `experiment_impl.py`를 facade로 유지:

```
# experiment_impl.py (기존 파일, facade로 변경)
from selfhealing.services.chaos.experiments import *  # 모든 re-export
```

---

## 2. integrity.py (1,815줄) → 수직 분리

### 2.1 현재 구조 분석

| 라인 범위 | 클래스/함수명 | 책임 |
|----------|-------------|------|
| 24-31 | IntegrityInfo | 데이터 클래스 |
| 33-46 | compute_hash() | 유틸리티 함수 |
| 48-170 | HashChainVerifier | 체인 검증 |
| 171-273 | HashChainManager | 로컬 체인 관리 |
| 274-313 | verify_audit_log_integrity() | 유틸리티 함수 |
| 314-334 | HashChainManagerProtocol | 프로토콜 인터페이스 |
| 335-607 | RedisHashChainManager | 분산 체인 관리 |
| 608-647 | create_hash_chain_manager() | 팩토리 함수 |
| 649-949 | PendingSequenceManager | 시퀀스 추적 |
| 950-1253 | DailyHashAnchor | 일일 앵커 |
| 1254-1545 | StartupHashChainSync | 시작 동기화 |
| 1546-1816 | HashChainReconciler | 조정/복구 |

### 2.2 분리 전략: 책임별 분리

```
audit/integrity/
├── __init__.py           # 공개 API re-export
├── models.py             # IntegrityInfo, compute_hash()
├── protocol.py           # HashChainManagerProtocol
├── verifier.py           # HashChainVerifier
├── local_manager.py      # HashChainManager
├── redis_manager.py      # RedisHashChainManager
├── factory.py            # create_hash_chain_manager(), verify_audit_log_integrity()
├── sequence.py           # PendingSequenceManager
├── anchor.py             # DailyHashAnchor
├── sync.py               # StartupHashChainSync
└── reconciler.py         # HashChainReconciler
```

### 2.3 분리 근거

**책임별 분리 이유**:

1. **models.py**
   - IntegrityInfo: 순수 데이터 클래스, 외부 의존성 없음
   - compute_hash(): 순수 함수, 외부 의존성 없음
   - 다른 모든 모듈이 이 모듈에 의존

2. **protocol.py**
   - HashChainManagerProtocol: 인터페이스 정의
   - local_manager.py와 redis_manager.py가 이 프로토콜 구현

3. **verifier.py**
   - HashChainVerifier: 검증 전용 (읽기 전용 작업)
   - models.py에만 의존

4. **local_manager.py vs redis_manager.py**
   - 동일 프로토콜의 다른 구현체
   - 로컬 파일 vs Redis 저장소
   - 의존성 격리: Redis 의존성은 redis_manager.py에만 존재

5. **sequence.py, anchor.py, sync.py, reconciler.py**
   - 각각 독립적인 보조 기능
   - 모두 Redis 의존성 있지만 서로 독립적
   - 개별적으로 테스트 가능

### 2.4 의존성 그래프

```
models.py ← protocol.py ← local_manager.py
                        ← redis_manager.py ← factory.py
         ← verifier.py
         
redis_manager.py ← sequence.py
                 ← anchor.py
                 ← sync.py
                 ← reconciler.py
```

---

## 3. chaos/base.py (1,652줄) → 수직 분리

### 3.1 현재 구조 분석

| 라인 범위 | 클래스/Enum명 | 책임 |
|----------|-------------|------|
| 33-40 | AuditRecorderProtocol | 프로토콜 |
| 41-57 | KillSwitchProtocol | 프로토콜 |
| 58-87 | ExperimentStatus | Enum |
| 90-142 | ExperimentType | Enum |
| 143-164 | TrafficType | Enum |
| 165-224 | ExperimentConfig | 데이터 클래스 |
| 225-320 | ExperimentResult | 데이터 클래스 |
| 321-380 | SteadyStateHypothesis | 데이터 클래스 |
| 382-489 | MonotonicTTLHelper | 헬퍼 클래스 |
| 491-1634 | ChaosExperiment | 추상 기반 클래스 (1,143줄) |
| 1635-1653 | _apply_chaos_config(), _get_current_chaos_config() | 유틸리티 |

### 3.2 분리 전략

```
services/chaos/
├── __init__.py           # 공개 API re-export
├── base/
│   ├── __init__.py       # base 패키지 공개 API
│   ├── protocols.py      # AuditRecorderProtocol, KillSwitchProtocol
│   ├── enums.py          # ExperimentStatus, ExperimentType, TrafficType
│   ├── models.py         # ExperimentConfig, ExperimentResult, SteadyStateHypothesis
│   ├── ttl_helper.py     # MonotonicTTLHelper
│   ├── experiment.py     # ChaosExperiment 추상 클래스
│   └── utils.py          # _apply_chaos_config(), _get_current_chaos_config()
```

### 3.3 분리 근거

**ChaosExperiment (1,143줄) 분리 검토**:

ChaosExperiment 클래스 자체가 1,143줄이지만, 이는 Template Method 패턴을 구현한 추상 기반 클래스로서 다음 메서드들을 포함:
- 생명주기 메서드: execute(), run(), abort(), rollback()
- 상태 검증 메서드: pre_flight_check(), capture_steady_state(), validate_recovery()
- 모니터링 메서드: monitor_impact(), check_stop_conditions()
- 보고 메서드: generate_report(), get_audit_records()
- TTL 관리 메서드: _setup_ttl(), is_expired(), _hard_ttl_exceeded()

**분리하지 않는 이유**:
- 이 메서드들은 모두 단일 실험 생명주기의 일부
- 메서드 간 강한 결합 (상태 공유)
- 분리 시 오히려 응집도 저하

**대신 보조 요소 분리**:
- Enum, 데이터 클래스, 프로토콜은 독립적이므로 분리
- ChaosExperiment는 단일 파일로 유지 (experiment.py)

---

## 4. hash_chain_graceful_degradation.py (1,625줄) → 수직 분리

### 4.1 현재 구조 분석

| 라인 범위 | 클래스명 | 책임 |
|----------|---------|------|
| 45-65 | DegradationLevel | Enum |
| 66-74 | FallbackConfig | 데이터 클래스 |
| 75-426 | HashChainFallbackChain | 다중 티어 폴백 (351줄) |
| 427-438 | DegradedEntryInfo | 데이터 클래스 |
| 439-618 | DegradedEntryMarker | 저하 엔트리 추적 (179줄) |
| 619-628 | HashChainWALEntry | 데이터 클래스 |
| 629-912 | HashChainWALRecovery | WAL 복구 (283줄) |
| 914-1183 | HashChainDegradationManager | 저하 관리 (269줄, 싱글톤) |
| 1185-1192 | CircuitState | Enum |
| 1193-1200 | CircuitBreakerConfig | 데이터 클래스 |
| 1201-1391 | HashChainCircuitBreaker | CB (190줄) |
| 1392-1626 | HashChainGracefulDegradationManager | 통합 관리자 (234줄) |

### 4.2 분리 전략

```
audit/graceful_degradation/
├── __init__.py               # 공개 API re-export
├── models.py                 # DegradationLevel, FallbackConfig, DegradedEntryInfo,
│                             # HashChainWALEntry, CircuitState, CircuitBreakerConfig
├── fallback_chain.py         # HashChainFallbackChain
├── degraded_marker.py        # DegradedEntryMarker
├── wal_recovery.py           # HashChainWALRecovery
├── degradation_manager.py    # HashChainDegradationManager (싱글톤)
├── circuit_breaker.py        # HashChainCircuitBreaker
└── manager.py                # HashChainGracefulDegradationManager (통합 진입점)
```

### 4.3 분리 근거

1. **models.py**
   - 모든 Enum 및 데이터 클래스 집중
   - 순환 참조 방지의 핵심

2. **fallback_chain.py**
   - HashChainFallbackChain: 다중 티어 폴백 로직
   - 독립적 테스트 가능
   - models.py에만 의존

3. **degraded_marker.py**
   - DegradedEntryMarker: 저하 엔트리 추적
   - Redis 의존성 격리

4. **wal_recovery.py**
   - HashChainWALRecovery: WAL 기반 복구
   - 파일 시스템 의존성 격리

5. **degradation_manager.py**
   - HashChainDegradationManager: 싱글톤 상태 관리자
   - 저하 수준 전환 로직

6. **circuit_breaker.py**
   - HashChainCircuitBreaker: 장애 감지 및 차단
   - 표준 CB 패턴 구현

7. **manager.py**
   - HashChainGracefulDegradationManager: 통합 진입점
   - 모든 컴포넌트 조정 (Facade 패턴)

---

## 5. middleware.py (1,424줄) → 레이어 분리

### 5.1 현재 구조 분석

| 라인 범위 | 클래스명 | 책임 |
|----------|---------|------|
| 34-200 | HealthBridgeMiddleware | 헬스체크 (166줄) |
| 201-282 | AccessLogEntry | 데이터 클래스 |
| 283-455 | SensitiveEndpointAccessLogger | 민감 엔드포인트 로깅 (172줄) |
| 456-503 | SensitiveAccessLoggingMiddleware | 미들웨어 래퍼 |
| 504-535 | FailSecureIsAuthenticated | DRF 권한 클래스 |
| 536-576 | FailSecureIsAdminUser | DRF 권한 클래스 |
| 577-1159 | SelfHealingMiddleware | 핵심 미들웨어 (582줄) |
| 1160-1396 | SelfHealingRecoveryLogger | 복구 로거 (236줄) |
| 1397-1425 | get_recovery_logger() | 팩토리 함수 |

### 5.2 분리 전략

```
api/django/middleware/
├── __init__.py              # 공개 API re-export
├── health.py                # HealthBridgeMiddleware
├── models.py                # AccessLogEntry
├── access_logging.py        # SensitiveEndpointAccessLogger, SensitiveAccessLoggingMiddleware
├── permissions.py           # FailSecureIsAuthenticated, FailSecureIsAdminUser
├── selfhealing.py           # SelfHealingMiddleware
└── recovery_logger.py       # SelfHealingRecoveryLogger, get_recovery_logger()
```

### 5.3 분리 근거

**레이어 분리 이유**:

1. **health.py**
   - HealthBridgeMiddleware: MIDDLEWARE 최상단 배치 필수
   - DB 독립적 헬스 엔드포인트
   - 다른 미들웨어와 의존성 없음

2. **access_logging.py**
   - SensitiveEndpointAccessLogger + SensitiveAccessLoggingMiddleware
   - 동일 기능의 서비스 + 미들웨어 래퍼
   - 보안 감사 로깅 전용

3. **permissions.py**
   - FailSecureIsAuthenticated, FailSecureIsAdminUser
   - DRF 권한 클래스 (미들웨어 아님)
   - 독립적 재사용 가능

4. **selfhealing.py**
   - SelfHealingMiddleware: 핵심 Self-Healing 로직
   - 가장 복잡한 컴포넌트 (582줄)
   - 추후 추가 분리 검토 대상

5. **recovery_logger.py**
   - SelfHealingRecoveryLogger: 복구 이벤트 로깅
   - selfhealing.py에서 사용하지만 독립적 테스트 가능

---

## 6. security_violation_service.py (1,305줄) → 수직 분리

### 6.1 현재 구조 분석

| 라인 범위 | 클래스/Enum명 | 책임 |
|----------|-------------|------|
| 44-116 | ViolationType | Enum (72개 항목) |
| 117-165 | Severity | Enum |
| 166-314 | ActionPolicy | Enum + 로직 |
| 315-357 | ProtectionResult | 데이터 클래스 |
| 358-378 | SecurityViolationResult | 데이터 클래스 |
| 379-423 | SecurityConfig | 설정 클래스 |
| 424-983 | SecurityViolationService | 핵심 서비스 (559줄) |
| 984-1028 | get_security_violation_service(), handle_security_violation() | 팩토리/헬퍼 |
| 1029-1305 | ProtectionOrchestrator | 보호 오케스트레이터 (276줄) |

### 6.2 분리 전략

```
services/security/
├── __init__.py              # 공개 API re-export
├── violation/
│   ├── __init__.py
│   ├── types.py             # ViolationType, Severity
│   ├── policies.py          # ActionPolicy
│   ├── models.py            # ProtectionResult, SecurityViolationResult, SecurityConfig
│   ├── service.py           # SecurityViolationService
│   └── helpers.py           # get_security_violation_service(), handle_security_violation()
└── orchestrator.py          # ProtectionOrchestrator
```

### 6.3 분리 근거

1. **types.py**
   - ViolationType: 72개 항목의 대형 Enum
   - Severity: 심각도 수준

2. **policies.py**
   - ActionPolicy: 각 위반 유형에 대한 조치 정책
   - 정책 결정 로직 포함

3. **models.py**
   - ProtectionResult, SecurityViolationResult, SecurityConfig
   - 순수 데이터 구조

4. **service.py**
   - SecurityViolationService: 핵심 위반 감지/처리
   - 559줄로 여전히 크지만 단일 책임

5. **orchestrator.py**
   - ProtectionOrchestrator: 다중 보호 메커니즘 조정
   - service.py와 별개의 조정 계층

---

## 7. load_shedding.py (1,164줄) → 수직 분리

### 7.1 현재 구조 분석

| 라인 범위 | 클래스명 | 책임 |
|----------|---------|------|
| 42-52 | SheddingState | Enum |
| 53-74 | SheddingDecision | 데이터 클래스 |
| 75-115 | SheddingStatus | 데이터 클래스 |
| 116-155 | SheddingAuditEntry | 데이터 클래스 |
| 156-226 | ErrorRateProvider | 에러율 제공자 |
| 227-791 | LoadSheddingManager | 핵심 관리자 (564줄) |
| 792-869 | LoadSheddingMiddleware | 미들웨어 |
| 870-1031 | LoadSheddingDashboard | 대시보드 (161줄) |
| 1032-1164 | 팩토리/헬퍼 함수들 | 유틸리티 |

### 7.2 분리 전략

```
services/circuit_breaker/load_shedding/
├── __init__.py              # 공개 API re-export
├── models.py                # SheddingState, SheddingDecision, SheddingStatus, SheddingAuditEntry
├── error_rate.py            # ErrorRateProvider
├── manager.py               # LoadSheddingManager
├── middleware.py            # LoadSheddingMiddleware
├── dashboard.py             # LoadSheddingDashboard
└── helpers.py               # 모든 팩토리/헬퍼 함수
```

---

## 8. 기타 1,000줄 이상 파일

### 8.1 tasks.py (1,140줄)

```
현재: adapters/celery/tasks.py
      15개 Celery Task 정의

분리 전략:
adapters/celery/tasks/
├── __init__.py              # 모든 Task re-export
├── dlq.py                   # async_persist_dlq_entry, async_persist_batch,
│                            # link_audit_to_dlq, replay_single_dlq_entry,
│                            # replay_batch_by_domain, cleanup_resolved_dlq_entries
├── circuit_breaker.py       # conditional_replay_on_circuit_close,
│                            # check_circuit_breaker_recovery, force_open_circuit_breaker,
│                            # force_close_circuit_breaker, expire_manual_overrides
├── metrics.py               # collect_self_healing_metrics, check_and_report_sla_breaches
├── heartbeat.py             # emit_selfhealing_heartbeat
└── notification.py          # notify_failsafe_recovery
```

### 8.2 safety_guard.py (1,111줄)

```
현재: services/chaos/safety_guard.py
      SafetyGuard (858줄) + 보조 클래스들

분리 전략:
services/chaos/safety/
├── __init__.py              # 공개 API re-export
├── models.py                # SafetyStatus, BlockReason, SafetyConfig, SafetyCheckResult
├── guard.py                 # SafetyGuard
└── helpers.py               # get_safety_guard(), reset_safety_guard()
```

### 8.3 scheduler.py (1,062줄)

```
현재: services/chaos/scheduler.py
      ChaosSchedulerService (985줄)

분리 전략:
services/chaos/scheduler/
├── __init__.py              # 공개 API re-export
├── service.py               # ChaosSchedulerService
└── helpers.py               # get_chaos_scheduler(), reset_chaos_scheduler()

참고: ChaosSchedulerService 자체가 985줄이지만 단일 책임(스케줄링)이므로
      클래스 분리보다는 패키지화만 진행
```

### 8.4 resilience.py (1,027줄)

```
현재: audit/resilience.py
      7개 클래스

분리 전략:
audit/resilience/
├── __init__.py              # 공개 API re-export
├── models.py                # CircuitState, CircuitBreakerConfig, CircuitBreakerState
├── circuit_breaker.py       # CircuitBreaker, CircuitBreakerRegistry
├── metrics.py               # AuditMetrics
├── syslog.py                # SyslogFallback
├── degraded_mode.py         # DegradedModeManager
├── buffer.py                # InMemoryAuditBuffer
└── helpers.py               # get_circuit_breaker(), get_audit_metrics(), ...
```

---

## 9. 분리 작업 순서

### Phase 1 (1주차): 가장 큰 파일

1. experiment_impl.py → experiments/ 패키지
2. integrity.py → integrity/ 패키지

### Phase 2 (2주차): Audit 관련

3. hash_chain_graceful_degradation.py → graceful_degradation/ 패키지
4. resilience.py → resilience/ 패키지

### Phase 3 (3주차): Middleware/Security

5. middleware.py → middleware/ 패키지
6. security_violation_service.py → security/ 패키지

### Phase 4 (4주차): Chaos/CircuitBreaker

7. chaos/base.py → base/ 패키지
8. load_shedding.py → load_shedding/ 패키지
9. safety_guard.py → safety/ 패키지

### Phase 5 (5주차): 나머지

10. tasks.py → tasks/ 패키지
11. scheduler.py → scheduler/ 패키지

---

## 10. 공통 패턴: `__init__.py` 구조

모든 분리된 패키지는 동일한 `__init__.py` 구조 사용:

```python
"""
패키지 설명.

Usage:
    from selfhealing.audit.integrity import (
        HashChainManager,
        RedisHashChainManager,
        create_hash_chain_manager,
    )
"""

from .models import IntegrityInfo
from .protocol import HashChainManagerProtocol
from .verifier import HashChainVerifier
from .local_manager import HashChainManager
from .redis_manager import RedisHashChainManager
from .factory import create_hash_chain_manager, verify_audit_log_integrity
from .sequence import PendingSequenceManager
from .anchor import DailyHashAnchor
from .sync import StartupHashChainSync
from .reconciler import HashChainReconciler

__all__ = [
    # Models
    "IntegrityInfo",
    # Protocol
    "HashChainManagerProtocol",
    # Core
    "HashChainVerifier",
    "HashChainManager",
    "RedisHashChainManager",
    # Factory
    "create_hash_chain_manager",
    "verify_audit_log_integrity",
    # Advanced
    "PendingSequenceManager",
    "DailyHashAnchor",
    "StartupHashChainSync",
    "HashChainReconciler",
]
```
