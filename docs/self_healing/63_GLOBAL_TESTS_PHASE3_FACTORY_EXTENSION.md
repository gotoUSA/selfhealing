# 63. Phase 3: Factory 확장

> **선행 문서**: 62_GLOBAL_TESTS_PHASE2_STRUCTURE.md  
> **목표**: 테스트 코드에서 발견된 패턴을 기반으로 추가 Builder/Factory 구현  
> **예상 소요**: 2-3시간

---

## 1. 목표

1. 기존 테스트 코드에서 반복되는 패턴 분석
2. 추가 Builder 클래스 구현
3. 통합 테스트 전용 Factory 확장

---

## 2. 기존 구현 현황

### 2.1 현재 tests/factories/ 구성

| 파일 | 주요 클래스/함수 |
|------|-----------------|
| `constants.py` | Domains, Services, FailureTypes, Status, CircuitState, RedisTestConfig, DatabaseTestConfig |
| `builders.py` | CircuitBreakerStateBuilder, FailedOperationBuilder, CanaryRolloutBuilder, MockServiceBuilder |
| `data_factory.py` | TestDataFactory, MockCircuitBreakerStateData, MockFailedOperationData |
| `integration.py` | RealRedisClientFactory, RealDatabaseFactory, CeleryTaskRunner, IntegrationTestContext |

---

## 3. 추가 필요 항목 (코드 기반 분석)

### 3.1 CanaryStageBuilder

**근거 위치**: `tests/integration/selfhealing/test_canary_integration.py`

**현재 패턴**:
- 테스트 내에서 `sample_stages` fixture로 CanaryStage 리스트 직접 생성
- 여러 테스트에서 동일한 스테이지 구조 반복

**추가할 Builder**:

| 클래스 | 메서드 |
|--------|--------|
| `CanaryStageBuilder` | `canary_stage()`, `regional_stage()`, `global_stage()` |
| | `with_clusters(list)`, `with_percentage(float)` |
| | `with_duration(minutes)`, `auto_promote(bool)` |
| | `build()` |

**사용 예시**:
```python
stages = [
    CanaryStageBuilder().canary_stage().with_percentage(10).build(),
    CanaryStageBuilder().regional_stage().with_percentage(50).build(),
    CanaryStageBuilder().global_stage().with_percentage(100).build(),
]
```

---

### 3.2 WatchdogConfigBuilder

**근거 위치**: `tests/integration/selfhealing/test_canary_integration.py`

**현재 패턴**:
- `WatchdogConfig` 직접 생성
- 여러 설정값 하드코딩

**추가할 Builder**:

| 클래스 | 메서드 |
|--------|--------|
| `WatchdogConfigBuilder` | `default()`, `aggressive()`, `conservative()` |
| | `with_check_interval(seconds)` |
| | `with_error_threshold(count)` |
| | `build()` |

---

### 3.3 RequestBuilder (API 테스트용)

**근거 위치**: `tests/self_healing/api/test_rbac_permissions.py`, `tests/self_healing/api/test_config_api_history_integration.py`

**현재 패턴**:
- Django Request 객체 Mock 반복 생성
- META 딕셔너리 수동 설정 (REMOTE_ADDR 등)
- user 속성 수동 설정

**추가할 Builder**:

| 클래스 | 메서드 |
|--------|--------|
| `MockRequestBuilder` | `with_user(user)`, `with_ip(ip_address)` |
| | `with_method(GET/POST/etc)` |
| | `with_body(dict)`, `with_headers(dict)` |
| | `admin_user()`, `operator_user()`, `anonymous()` |
| | `build()` |

---

### 3.4 ChaosExperimentBuilder

**근거 위치**: `tests/self_healing/chaos/` 폴더 전체

**현재 패턴**:
- ChaosExperiment 설정 반복 생성
- 실험 타입별 설정 하드코딩

**추가할 Builder**:

| 클래스 | 메서드 |
|--------|--------|
| `ChaosExperimentBuilder` | `circuit_breaker_test()`, `latency_injection()` |
| | `failure_injection()`, `resource_exhaustion()` |
| | `with_target_service(name)` |
| | `with_duration(seconds)` |
| | `with_intensity(level)` |
| | `build()` |

---

### 3.5 NotificationBuilder

**근거 위치**: `tests/hybrid/test_notification_sla.py`

**현재 패턴**:
- notification 관련 테스트 데이터 반복 생성
- domain, failure_type 조합 하드코딩

**추가할 Builder**:

| 클래스 | 메서드 |
|--------|--------|
| `NotificationBuilder` | `email()`, `slack()`, `webhook()` |
| | `with_recipient(email/channel)` |
| | `with_priority(level)` |
| | `failure_alert()`, `recovery_alert()` |
| | `build()` |

---

## 4. constants.py 확장

### 4.1 추가할 상수 그룹

**Canary 관련**:

| 상수 클래스 | 값들 |
|------------|------|
| `CanaryCluster` | SEOUL_CANARY, SEOUL_MAIN, TOKYO_MAIN, SINGAPORE_MAIN |
| `CanaryPercentage` | INITIAL (10), HALF (50), FULL (100) |

**Chaos 관련**:

| 상수 클래스 | 값들 |
|------------|------|
| `ChaosType` | LATENCY_INJECTION, FAILURE_INJECTION, CIRCUIT_BREAKER |
| `ChaosIntensity` | LOW, MEDIUM, HIGH, EXTREME |

**Notification 관련**:

| 상수 클래스 | 값들 |
|------------|------|
| `NotificationType` | EMAIL, SLACK, WEBHOOK |
| `NotificationPriority` | LOW, MEDIUM, HIGH, CRITICAL |

---

## 5. integration.py 확장

### 5.1 추가할 Factory

**CeleryWorkerFactory**:
- Docker Compose celery-worker 상태 확인
- 워커 ready 대기

**RedisClusterFactory** (Multi-Cluster 테스트용):
- LOCAL, GLOBAL 스코프 Redis 연결
- 멀티 클러스터 시뮬레이션

---

## 6. 구현 순서

| 순서 | 항목 | 우선순위 | 근거 |
|------|------|---------|------|
| 1 | MockRequestBuilder | 높음 | API 테스트 10개+ 파일에서 사용 |
| 2 | CanaryStageBuilder | 중간 | Canary 테스트에서 반복 사용 |
| 3 | ChaosExperimentBuilder | 중간 | Chaos 테스트 20개+ 파일 |
| 4 | WatchdogConfigBuilder | 낮음 | 사용 빈도 낮음 |
| 5 | NotificationBuilder | 낮음 | 특정 테스트에서만 사용 |

---

## 7. 완료 기준

- [ ] MockRequestBuilder 구현 및 테스트
- [ ] CanaryStageBuilder 구현 및 테스트
- [ ] ChaosExperimentBuilder 구현 및 테스트
- [ ] constants.py 확장 (Canary, Chaos, Notification)
- [ ] 기존 테스트 1개 이상 새 Builder로 리팩토링하여 동작 확인

---

## 8. 파일 위치

모든 새 Builder는 기존 파일에 추가:

| Builder | 파일 |
|---------|------|
| MockRequestBuilder | `tests/factories/builders.py` |
| CanaryStageBuilder | `tests/factories/builders.py` |
| ChaosExperimentBuilder | `tests/factories/builders.py` |
| WatchdogConfigBuilder | `tests/factories/builders.py` |
| NotificationBuilder | `tests/factories/builders.py` |

상수는:
| 상수 | 파일 |
|------|------|
| CanaryCluster, CanaryPercentage | `tests/factories/constants.py` |
| ChaosType, ChaosIntensity | `tests/factories/constants.py` |
| NotificationType, NotificationPriority | `tests/factories/constants.py` |
