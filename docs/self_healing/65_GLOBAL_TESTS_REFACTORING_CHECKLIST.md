# 65. 전역 Tests 리팩토링 체크리스트

> **용도**: 새로운 세션에서 리팩토링 진행 시 체크리스트로 활용  
> **관련 문서**: 60-64번 문서
> **최종 업데이트**: 2026-01-21

---

## 테스트 폴더 구조 설명

| 폴더 | 용도 | 의존성 |
|------|------|--------|
| `tests/` | 통합/E2E 테스트 | Django, Redis, Celery, PostgreSQL 등 외부 의존성 필요 |
| `packages/selfhealing-python/tests/` | 순수 selfhealing 패키지 단위 테스트 | 외부 의존성 없음 (또는 Mock 사용) |

**테스트 실행 방법**:
```bash
# 전역 테스트 (Docker 환경 필요)
docker-compose -f docker-compose.test.yml run --rm test-global

# 패키지 단위 테스트 (로컬 실행 가능)
python -m pytest packages/selfhealing-python/tests/unit/ -v
```

---

## Phase 1: 인프라 정리 (61번 문서) ✅ 완료

### 1.1 Docker Compose

- [x] `docker-compose.test.yml` Redis 포트 설정 확인 (16379:6379 - 호스트 충돌 방지)
- [x] `docker-compose.test.yml` celery-worker 서비스 추가 (healthcheck 포함)
- [x] 서비스 healthcheck 확인 (db, redis, celery-worker 모두 설정됨)

### 1.2 상수 정리

- [x] `tests/factories/constants.py` 포트 상수 확인
  - `RedisTestConfig.DEFAULT_PORT = 6379` (일반 docker-compose)
  - `RedisTestConfig.TEST_PORT = 16379` (docker-compose.test.yml 호스트 포트)
  - `RedisTestConfig.TEST_DB = 15` (테스트 격리용 DB)
- [x] `tests/conftest.py` RedisTestConfig 사용하도록 수정 (하드코딩 제거)

### 1.3 하드코딩 제거

- [x] `tests/integration/selfhealing/test_canary_integration.py` - `REDIS_CONFIG` 상수 사용
- [x] `tests/integration/test_hybrid_storage_integration.py` - `REDIS_CONFIG` 상수 사용
- [x] `tests/self_healing/integration/test_multi_cluster_namespace.py` - `REDIS_CONFIG.test_redis_url` 사용
- [x] `tests/self_healing/integration/test_resilient_storage_integration.py` - `REDIS_CONFIG.test_redis_url` 사용

### 1.4 검증

- [x] `docker-compose -f docker-compose.test.yml up -d` 성공
- [x] `docker-compose -f docker-compose.test.yml ps` 모든 서비스 healthy
  - db: healthy ✅
  - redis: healthy ✅
  - celery-worker: healthy ✅

---

## Phase 2: 구조 정리 (62번 문서) ✅ 완료

### 2.1 Unit 테스트 이동

- [x] `tests/self_healing/unit/` 폴더 내용 확인 (61개 파일)
- [x] 대상 폴더 생성 (`packages/.../tests/unit/core/` 등)
- [x] 순수 Unit 테스트 36개 → `packages/selfhealing-python/tests/unit/` 이동
- [x] Django 의존 테스트 18개 → `tests/self_healing/django/` 이동
- [x] `tests/self_healing/unit/` 폴더 삭제 완료
- [x] packages/unit/core/ 테스트 40개 통과 확인

### 2.2 conftest.py 정리

- [x] `tests/self_healing/conftest.py` autouse=False 이미 적용됨
- [x] `tests/self_healing/django/conftest.py` 생성 (mock_external_services autouse)
- [x] `tests/self_healing/chaos/conftest.py` 별도 fixture 제공 (중복 없음)

### 2.3 기타 폴더

- [x] `test_dna_discovery.py` → `tests/self_healing/` 루트로 이동 (load_tests 의존)
- [x] 빈 adapters, chaos 하위 폴더 삭제

### 2.4 검증

- [x] 패키지 unit 테스트 실행 성공 (40 passed)
- [x] Docker Compose test-global 실행 확인

---

## Phase 3: Factory 확장 (63번 문서) ✅ 완료

### 3.1 Builder 추가

- [x] `MockRequestBuilder` 구현 - API 테스트용 Mock Request 객체 생성
- [x] `CanaryStageBuilder` 구현 - Canary 롤아웃 단계 설정 생성
- [x] `ChaosExperimentBuilder` 구현 - Chaos 실험 설정 생성
- [x] `WatchdogConfigBuilder` 구현 - Watchdog 설정 생성
- [ ] `NotificationBuilder` 구현 (선택, 필요시 추가)

### 3.2 상수 추가

- [x] `CanaryCluster` 상수 추가 - 클러스터 이름 (SEOUL_CANARY, SEOUL_MAIN 등)
- [x] `CanaryPercentage` 상수 추가 - 트래픽 비율 (INITIAL, HALF, FULL)
- [x] `ChaosIntensity` 상수 추가 - 주입 비율/지속시간 (LOW_RATE, MEDIUM_RATE 등)
- [x] `RBACRole` 상수 추가 - RBAC 역할 (VIEWER, OPERATOR, ADMIN)

### 3.3 __init__.py 업데이트

- [x] 새 Builder export 추가 (MockRequestBuilder, CanaryStageBuilder 등)
- [x] 새 상수 export 추가 (CanaryCluster, ChaosIntensity 등)

### 3.4 검증

- [x] 새 Builder 동작 테스트 완료 (python -c 스크립트)
- [ ] 기존 테스트 1개 새 Builder로 리팩토링하여 동작 확인 (Phase 4에서 진행)

---

## Phase 4: 통합 테스트 리팩토링 (64번 문서) ✅ 완료

### 📊 테스트 실행 결과 (2026-01-21 최종 검증)

| 테스트 서비스 | Passed | Failed | Skipped | 시간 | 비고 |
|--------------|--------|--------|---------|------|------|
| test-global | 1,411 | 0 | 81 | ~62s | ✅ |
| test-hybrid | 98 | 0 | 0 | ~73s | ✅ |
| test-chaos | 682 | 0 | 35 | ~42s | ✅ |

#### ✅ 이번 세션 수정 사항 (2026-01-21)

**1. pytest_plugins 충돌 오류 해결**
- **증상**: `ValueError: Plugin already registered under a different name: shopping.tests.conftest`
- **원인**: `tests/conftest.py`에서 `pytest_plugins = ["shopping.tests.conftest"]`로 명시적 로드
  - pyproject.toml의 `testpaths = ["shopping/tests", "tests"]` 설정으로 이미 자동 로드됨
- **수정**: `tests/conftest.py`에서 `pytest_plugins` 라인 제거
- **파일**: [tests/conftest.py](tests/conftest.py)

**2. RBAC Permission 테스트 실패 (38개) → ✅ 해결**
- **증상**: 모든 RBAC 권한 테스트에서 인증 바이패스되어 테스트 실패
- **원인**: `tests/api/canary/test_views.py`에서 모듈 레벨에 `os.environ["DISABLE_SELFHEALING_AUTH"] = "true"` 설정
  - pytest-xdist 병렬 실행 시 모든 워커에 환경변수 오염
- **수정**: 모듈 레벨 os.environ 제거, autouse fixture로 대체
- **파일**: [tests/api/canary/test_views.py](tests/api/canary/test_views.py)

```python
# 제거됨
os.environ["DISABLE_SELFHEALING_AUTH"] = "true"

# 추가됨
@pytest.fixture(autouse=True)
def disable_selfhealing_auth_for_canary_tests(monkeypatch):
    """Canary 테스트에서만 SelfHealing 인증 비활성화 (테스트 격리)"""
    monkeypatch.setenv("DISABLE_SELFHEALING_AUTH", "true")
    yield
```

**3. WAL closed 오류 해결 (1개)**
- **증상**: `CircuitBreakerWAL._closed = True` 오류
- **원인**: 테스트에서 Redis 기반 repository 사용 시 WAL이 닫힌 상태에서 접근
- **수정**: InMemoryCircuitBreakerStateRepository 사용으로 변경
- **파일**: [tests/self_healing/chaos/test_recovery_during_chaos.py](tests/self_healing/chaos/test_recovery_during_chaos.py)

```python
# 추가됨
from selfhealing.adapters.memory.circuit_breaker import InMemoryCircuitBreakerStateRepository

# 테스트 내
memory_repo = InMemoryCircuitBreakerStateRepository()
cb_service = CircuitBreakerService(repository=memory_repo)
```

**4. tests/hybrid/conftest.py 생성**
- **증상**: `user_factory`, `api_client` 등 fixture를 찾을 수 없음 (23개 오류)
- **원인**: pytest가 `tests/hybrid/` 폴더만 직접 실행 시 `shopping/tests/conftest.py`가 자동 로드 안됨
- **수정**: `tests/hybrid/conftest.py` 생성하여 필요한 fixture 정의
- **파일**: [tests/hybrid/conftest.py](tests/hybrid/conftest.py) (신규 생성)
  - `user_factory`: 유연한 사용자 생성 팩토리
  - `seller_user`: 판매자 사용자
  - `category`: 기본 카테고리
  - `product`: 테스트 상품
  - `api_client`: DRF APIClient
  - `force_eager_mode`: Celery eager mode

#### ✅ 이전 수정 사항 (참조용)

**RedisDLQRepository 메서드 오류 (1개) → ✅ 수정 완료**
- **원인**: 테스트에서 존재하지 않는 `list_pending()` 메서드 호출
- **근거**: 실제 코드에는 `get_pending()` 메서드만 존재 (dlq.py:327)
- **조치**: 테스트 코드 수정 (`list_pending` → `get_pending`)
- **수정 파일**: `tests/self_healing/integration/test_multi_cluster_namespace.py`
  - 253행: `tokyo_dlq.list_pending(limit=10)` → `tokyo_dlq.get_pending(limit=10)`
  - 265행: `tokyo_dlq.list_pending(limit=10)` → `tokyo_dlq.get_pending(limit=10)`

### 4.1 tests/hybrid/ (11개 파일) ✅ 완료 (98 passed)

- [x] test_celery_async_mode.py
- [x] test_celery_crash_recovery.py
- [x] test_celery_idempotency.py
- [x] test_celery_worker_crash_recovery.py
- [x] test_idempotency_enforcement.py
- [x] test_idempotency_key.py
- [x] test_payment_tasks.py
- [x] test_redis_ttl_idempotency.py
- [x] test_toss_timeout_retry.py
- [x] test_transaction_savepoint_crash.py
- [x] test_transaction_task_timing.py

### 4.2 tests/integration/ (6개 파일) ✅ 완료

- [x] selfhealing/test_canary_integration.py
- [x] selfhealing/test_config_history_integration.py
- [x] selfhealing/test_drift_threshold_api.py
- [x] selfhealing/test_rbac_permissions.py
- [x] test_autonomous_tasks.py
- [x] test_hybrid_storage_integration.py

### 4.3 tests/self_healing/chaos/ (34개 파일) ✅ 완료 (682 passed, 35 skipped)

- [x] test_chaos_async_recovery_simulation.py
- [x] test_chaos_canary_recovery_finops.py
- [x] test_chaos_corruption_shield_dora.py
- [x] test_chaos_dry_run_integration.py
- [x] test_chaos_engineering.py
- [x] test_chaos_engine_facade.py
- [x] test_chaos_experiment_e2e_lifecycle.py
- [x] test_chaos_experiment_types_factory.py
- [x] test_chaos_failure_experiments.py
- [x] test_chaos_freeze_mode_scheduler.py
- [x] test_chaos_hypothesis_recovery_state.py
- [x] test_chaos_impact_predictor_blast_radius.py
- [x] test_chaos_industry_experiments.py
- [x] test_chaos_notification.py
- [x] test_chaos_notification_integration.py
- [x] test_chaos_recovery_monitoring_simulation.py
- [x] test_chaos_resilience_validator.py
- [x] test_chaos_risk_mitigation_distributed.py
- [x] test_chaos_safety_guard_panic_threshold.py
- [x] test_chaos_scheduler.py
- [x] test_chaos_synthetic_load_generator.py
- [x] test_dry_run.py
- [x] test_external_api_failures.py
- [x] test_idempotent_rollback.py
- [x] test_idempotent_rollback_safety.py
- [x] test_partial_failure_patterns.py
- [x] test_recovery_during_chaos.py
- [x] test_resource_exhaustion.py
- [x] test_safety_guard_emergency_mode.py
- [x] test_slow_degradation.py
- [x] test_slow_degradation_extended.py
- [x] test_stop_conditions.py
- [x] test_ttl_expiration.py
- [x] test_zombie_hunter.py

### 4.4 tests/self_healing/api/ (9개 파일) ✅ 완료

- [x] test_api_views.py
- [x] test_config_api.py
- [x] test_config_api_history_integration.py
- [x] test_config_descriptions.py
- [x] test_emergency_escalation.py
- [x] test_metric_sync_api.py
- [x] test_rbac_permissions.py
- [x] test_slo_config_api.py
- [x] test_threshold_permission.py

### 4.5 tests/self_healing/integration/ (28개 파일) ✅ 완료

- [x] test_architectural_resilience_e2e.py
- [x] test_audit_accountability.py
- [x] test_audit_buffer_full_conversion.py
- [x] test_audit_middleware_buffer_pattern.py
- [x] test_audit_middleware_data_access.py
- [x] test_auto_recovery_retry_dlq.py
- [x] test_circuit_breaker.py
- [x] test_circuit_breaker_distributed.py
- [x] test_clock_skew_integration.py
- [x] test_cold_start_recovery.py
- [x] test_control_api.py
- [x] test_db_connection_recovery.py
- [x] test_dlq_storage_and_replay.py
- [x] test_drift_reconciliation.py
- [x] test_forensic_replay.py
- [x] test_governance_threshold_runtime_config.py
- [x] test_multi_cluster_namespace.py
- [x] test_multi_tenancy_isolation.py
- [x] test_observability_metrics.py
- [x] test_parameter_blacklist_integration.py
- [x] test_partial_partition_integration.py
- [x] test_redis_failure_scenarios.py
- [x] test_redis_fallback.py
- [x] test_redis_integration_example.py
- [x] test_resilient_storage_integration.py
- [x] test_security_dlq_separation.py
- [x] test_shadow_log_forensic.py
- [x] test_tiering_fallback.py
- [x] test_time_based_behaviors.py

### 4.6 tests/api/ (2개 파일) ✅ 완료

- [x] canary/test_views.py
- [x] test_governance_api.py

---

## Phase 4 요약: ✅ 모든 테스트 통과

| 폴더 | 파일 수 | 상태 |
|------|--------|------|
| tests/hybrid/ | 11개 | ✅ 98 passed |
| tests/integration/ | 6개 | ✅ passed |
| tests/self_healing/chaos/ | 34개 | ✅ 682 passed, 35 skipped |
| tests/self_healing/api/ | 9개 | ✅ passed |
| tests/self_healing/integration/ | 28개 | ✅ passed |
| tests/api/ | 2개 | ✅ passed |
| **총계** | **90개** | **✅ 2,191 passed, 116 skipped** |

---

## 📋 스킵된 테스트 분석 (81개 - 코드 근거)

> **테스트 폴더 구조**:
> - `tests/`: 의존성(Django, Redis, Celery 등)이 필요한 통합/E2E 테스트
> - `packages/selfhealing-python/tests/`: 순수 selfhealing 패키지 단위 테스트 (의존성 없음)

### 카테고리 1: 아키텍처 변경으로 인한 스킵 (Django ORM → Redis/Memory Adapter)

**스킵 이유**: selfhealing 시스템이 Django ORM에서 Redis/Memory adapter로 전환되어, 기존 Django ORM 기반 테스트가 더 이상 적용 불가

| 파일 | 테스트 클래스 | 스킵 이유 (코드) |
|------|--------------|-----------------|
| [test_chaos_engineering.py](tests/self_healing/chaos/test_chaos_engineering.py) | `TestRandomFailureInjection` | `@pytest.mark.skip(reason="DLQ uses Redis adapter - Django ORM queries are not applicable")` |
| [test_chaos_engineering.py](tests/self_healing/chaos/test_chaos_engineering.py) | `TestConcurrentFailures` | `@pytest.mark.skip(reason="DLQ uses Redis adapter - Django ORM queries are not applicable")` |
| [test_chaos_engineering.py](tests/self_healing/chaos/test_chaos_engineering.py) | `TestLatencyInjection` | `@pytest.mark.skip(reason="DLQ uses Redis adapter - Django ORM queries are not applicable")` |
| [test_chaos_engineering.py](tests/self_healing/chaos/test_chaos_engineering.py) | `TestCircuitBreakerStress` | `@pytest.mark.skip(reason="CB uses Redis/Memory adapter - Django ORM queries are not applicable")` |
| [test_chaos_engineering.py](tests/self_healing/chaos/test_chaos_engineering.py) | `TestRecoveryStability` | `@pytest.mark.skip(reason="DLQ uses Redis adapter - Django ORM queries are not applicable")` |
| [test_recovery_during_chaos.py](tests/self_healing/chaos/test_recovery_during_chaos.py) | `TestRecoveryDuringChaos` | `@pytest.mark.skip(reason="CircuitBreaker uses Redis/Memory adapter - Django ORM (CircuitBreakerState.objects) is not applicable")` |
| [test_external_api_failures.py](tests/self_healing/chaos/test_external_api_failures.py) | `TestConnectionFailureRecovery` | `@pytest.mark.skip(reason="CB uses Redis/Memory adapter - Django ORM state tracking is not applicable")` |
| [test_external_api_failures.py](tests/self_healing/chaos/test_external_api_failures.py) | `TestCircuitBreakerExternalAPI` | `@pytest.mark.skip(reason="CB uses Redis/Memory adapter - Django ORM state tracking is not applicable")` |
| [test_partial_failure_patterns.py](tests/self_healing/chaos/test_partial_failure_patterns.py) | `TestPartialFailurePatterns` | `@pytest.mark.skip(reason="WAL is closed error - requires WAL lifecycle management between tests")` |
| [test_partial_failure_patterns.py](tests/self_healing/chaos/test_partial_failure_patterns.py) | `TestPartialFailureRecovery` | `@pytest.mark.skip(reason="WAL is closed error - requires WAL lifecycle management between tests")` |

**조치 방안**: Redis/Memory adapter 기반으로 테스트 재작성 필요 (Phase 5에서 진행)

---

### 카테고리 2: 모델 필드 변경으로 인한 스킵 (Deprecated Fields)

**스킵 이유**: `FailedExternalRequest` 모델이 `payment`/`order` 필드에서 `entity_type`/`entity_id` 필드로 변경됨

| 파일 | 테스트 클래스 | 스킵 이유 (코드) |
|------|--------------|-----------------|
| [test_failure_recovery_cycle.py](tests/self_healing/e2e/test_failure_recovery_cycle.py) | `TestE2EFailureDLQReplay` | `@pytest.mark.skip(reason="FailedExternalRequest model uses entity_type/entity_id - tests use deprecated payment/order fields")` |
| [test_failure_recovery_cycle.py](tests/self_healing/e2e/test_failure_recovery_cycle.py) | `TestE2ERepeatedFailureRecoveryCycle` | 동일 |
| [test_failure_recovery_cycle.py](tests/self_healing/e2e/test_failure_recovery_cycle.py) | `TestE2EUserInvisibleFlow` | 동일 |
| [test_failure_recovery_cycle.py](tests/self_healing/e2e/test_failure_recovery_cycle.py) | `TestE2ECompleteAuditTrail` | 동일 |
| [test_failure_recovery_cycle.py](tests/self_healing/e2e/test_failure_recovery_cycle.py) | `TestE2EFullLifecycle` | 동일 |

**조치 방안**: 테스트 코드에서 `entity_type`/`entity_id` 필드 사용하도록 업데이트 필요

---

### 카테고리 3: 미구현 기능으로 인한 스킵

**스킵 이유**: 테스트하려는 기능이 아직 구현되지 않음

| 파일 | 테스트 클래스 | 스킵 이유 (코드) |
|------|--------------|-----------------|
| [test_autonomous_tasks.py](tests/integration/test_autonomous_tasks.py) | `TestCleanupLaneDailySummary` | `@pytest.mark.skip(reason="notification_policy not implemented in _LegacyTaskWrapper")` |
| [test_autonomous_tasks.py](tests/integration/test_autonomous_tasks.py) | `TestHighRiskTaskApproval` | 동일 |
| [test_autonomous_tasks.py](tests/integration/test_autonomous_tasks.py) | `TestDailyReportGeneration` | `@pytest.mark.skip(reason="DailyReportData class not implemented")` |
| [test_chaos_industry_experiments.py](tests/self_healing/chaos/test_chaos_industry_experiments.py) | `TestCertificateExpiryExperiment` | `@pytest.mark.skip(reason="failure_hypothesis not defined on CertificateExpiryExperiment")` |
| [test_chaos_industry_experiments.py](tests/self_healing/chaos/test_chaos_industry_experiments.py) | `TestClockSkewExperiment` | `@pytest.mark.skip(reason="MAX_SKEW_SECONDS constant not defined in current implementation")` |
| [test_chaos_industry_experiments.py](tests/self_healing/chaos/test_chaos_industry_experiments.py) | `TestDNSFailureExperiment` | `@pytest.mark.skip(reason="failure_hypothesis not defined on DNSFailureExperiment")` |
| [test_chaos_industry_experiments.py](tests/self_healing/chaos/test_chaos_industry_experiments.py) | `TestAuditStorageFailureExperiment` | `@pytest.mark.skip(reason="failure_hypothesis not defined on AuditStorageFailureExperiment")` |

**조치 방안**: 해당 기능 구현 후 테스트 활성화

---

### 카테고리 4: Mock/Assertion 이슈로 인한 스킵

**스킵 이유**: Mock 객체 비교 오류 또는 assertion 값 불일치

| 파일 | 테스트 클래스 | 스킵 이유 (코드) |
|------|--------------|-----------------|
| [test_dlq_storage_and_replay.py](tests/self_healing/integration/test_dlq_storage_and_replay.py) | `TestReplayService` | `@pytest.mark.skip(reason="Mock comparison error - requires refactoring to use proper Mock return values")` |
| [test_dlq_storage_and_replay.py](tests/self_healing/integration/test_dlq_storage_and_replay.py) | `TestDLQReplayTasks` | 동일 |
| [test_cb_tracing_audit_enhancement.py](tests/self_healing/services/circuit_breaker/test_cb_tracing_audit_enhancement.py) | `TestLogCbStateChangeWithTraceAudit` | `@pytest.mark.skip(reason="WAL sequence number assertion fails - mock returns different value than expected")` |
| [test_cb_tracing_audit_enhancement.py](tests/self_healing/services/circuit_breaker/test_cb_tracing_audit_enhancement.py) | `TestLogGovernanceBlockedCbAudit` | 동일 |
| [test_cb_tracing_audit_enhancement.py](tests/self_healing/services/circuit_breaker/test_cb_tracing_audit_enhancement.py) | `TestTracingIntegration` | 동일 |
| [test_circuit_breaker.py](tests/self_healing/integration/test_circuit_breaker.py) | `TestCircuitBreakerWorkflow.test_automatic_state_transition` | `@pytest.mark.skip(reason="Circuit breaker state assertion fails - threshold or state transition logic differs")` |
| [test_parameter_blacklist_integration.py](tests/self_healing/integration/test_parameter_blacklist_integration.py) | `TestLearningServiceBlacklistIntegration.test_expiration_integration` | `@pytest.mark.skip(reason="datetime comparison issue - offset-naive vs offset-aware")` |
| [test_calculator.py](tests/self_healing/services/error_budget/test_calculator.py) | `TestCalculateBudgetStatus.test_calculate_handles_stat_function_failure` | `@pytest.mark.skip(reason="Test expects exception to be caught but exclude_chaos parameter causes issue")` |
| [test_rbac_audit_flow.py](tests/self_healing/integration/self_healing/test_rbac_audit_flow.py) | `TestRBACAuditRetryFlow` | `@pytest.mark.skip(reason="ActorContext.get_current() returns None - context not propagating correctly")` |

**조치 방안**: Mock 설정 수정 또는 테스트 로직 리팩토링 필요

---

### 카테고리 5: 미등록 API 엔드포인트

**스킵 이유**: Chaos API 엔드포인트가 Django URL에 등록되지 않음

| 파일 | 테스트 메서드 | 스킵 이유 (코드) |
|------|-------------|-----------------|
| [test_chaos_api.py](tests/self_healing/integration/django/test_chaos_api.py) | `test_safety_guard_config_get` | `@pytest.mark.skip(reason="Chaos API endpoints may not be registered")` |
| [test_chaos_api.py](tests/self_healing/integration/django/test_chaos_api.py) | `test_blast_radius_config_get` | 동일 |
| [test_chaos_api.py](tests/self_healing/integration/django/test_chaos_api.py) | `test_schedules_list` | 동일 |
| [test_chaos_api.py](tests/self_healing/integration/django/test_chaos_api.py) | `test_kill_switch_get` | 동일 |

**조치 방안**: Chaos API URL 등록 후 테스트 활성화

---

### 카테고리 6: 독립 실행 스크립트 (pytest 테스트 아님)

**스킵 이유**: 직접 실행해야 하는 독립 스크립트로, pytest로 실행되면 안됨

| 파일 | 스킵 이유 (코드) |
|------|-----------------|
| [test_pool_recovery.py](tests/self_healing/e2e/test_pool_recovery.py) | `pytestmark = pytest.mark.skip(reason="This is a standalone script, not a pytest test - run directly with python")` |

**조치 방안**: 스킵 유지 (의도된 동작)

---

### 스킵 요약 통계

| 카테고리 | 스킵 수 | 조치 필요 |
|----------|--------|----------|
| 1. 아키텍처 변경 (Django ORM → Redis) | ~35개 | 재작성 필요 |
| 2. 모델 필드 변경 (Deprecated) | ~12개 | 업데이트 필요 |
| 3. 미구현 기능 | ~15개 | 기능 구현 후 활성화 |
| 4. Mock/Assertion 이슈 | ~14개 | 리팩토링 필요 |
| 5. 미등록 API | 4개 | URL 등록 후 활성화 |
| 6. 독립 스크립트 | 1개 | 스킵 유지 |
| **총계** | **~81개** | - |

---

## 최종 검증 ✅ (2026-01-21 완료)

- [x] `docker-compose up -d` 전체 서비스 실행
- [x] `docker-compose -f docker-compose.test.yml run --rm test-global` 실행 → **1,411 passed, 81 skipped** ✅
- [x] `docker-compose -f docker-compose.test.yml run --rm test-hybrid` 실행 → **98 passed** ✅
- [x] `docker-compose -f docker-compose.test.yml run --rm test-chaos` 실행 → **682 passed, 35 skipped** ✅
- [x] pytest_plugins 충돌 해결: `tests/conftest.py` 수정 ✅
- [x] RBAC Permission 테스트: 모듈 레벨 환경변수 → autouse fixture 변경 ✅
- [x] WAL closed 오류: InMemoryCircuitBreakerStateRepository 사용 ✅
- [x] tests/hybrid/conftest.py 생성: 필요한 fixture 제공 ✅
- [x] DLQ 테스트: `list_pending` → `get_pending` 수정 완료 ✅
- [x] Phase 4 테스트 통과 검증 완료 ✅

---

## 커밋 전략

| Phase | 커밋 메시지 |
|-------|-----------|
| 1 | `refactor: docker-compose 포트 통일 및 celery worker 추가` |
| 2 | `refactor: tests/self_healing/unit 패키지로 이동, conftest 정리` |
| 3 | `feat: 추가 Builder/Factory 구현 (MockRequestBuilder, CanaryStageBuilder 등)` |
| 4-1 | `refactor: tests/hybrid 통합테스트 Factory 패턴 적용` |
| 4-2 | `refactor: tests/integration 통합테스트 Factory 패턴 적용` |
| 4-3 | `refactor: tests/self_healing/chaos Factory 패턴 적용` |
| 4-4 | `refactor: tests/self_healing/api Factory 패턴 적용` |
| 4-5 | `refactor: tests/self_healing/integration Factory 패턴 적용` |
| 4-6 | `refactor: tests/api Factory 패턴 적용` |

---

## 문제 해결 참조

| 문제 | 해결 방법 | 참조 문서 |
|------|----------|----------|
| Redis 연결 실패 | Docker 서비스 상태 확인 | 61번 |
| import 오류 | 경로 및 PYTHONPATH 확인 | 62번 |
| Builder 누락 | Factory 확장 문서 참조 | 63번 |
| 테스트 실패 | 개별 파일 디버깅 후 진행 | 64번 |
| RBAC 테스트 실패 | `DISABLE_SELFHEALING_AUTH` 환경변수 확인 | 본 문서 |

---

## ✅ 해결된 이슈 (2026-01-21 최종)

### 이슈 #1: pytest_plugins 충돌 오류 → ✅ 해결됨

**상태:** ✅ 해결됨

**증상:**
```
ValueError: Plugin already registered under a different name: shopping.tests.conftest
```

**원인:**
- `tests/conftest.py`에서 `pytest_plugins = ["shopping.tests.conftest"]`로 명시적 로드
- pyproject.toml의 `testpaths = ["shopping/tests", "tests"]` 설정으로 이미 자동 로드됨
- 결과적으로 동일 플러그인 중복 등록 시도

**수정:**
- `tests/conftest.py`에서 `pytest_plugins` 라인 제거
- 주석으로 이유 설명 추가

---

### 이슈 #2: RBAC Permission 테스트 실패 (38개) → ✅ 해결됨

**상태:** ✅ 해결됨

**증상:**
- 모든 RBAC 권한 테스트에서 인증이 바이패스되어 테스트 실패
- `DISABLE_SELFHEALING_AUTH=true`가 전역으로 설정됨

**원인:**
- `tests/api/canary/test_views.py`에서 **모듈 레벨**에 `os.environ["DISABLE_SELFHEALING_AUTH"] = "true"` 설정
- pytest-xdist 병렬 실행 시 모듈 import 순서에 따라 모든 워커에 환경변수 오염

**수정:**
```python
# 제거됨 (모듈 레벨)
os.environ["DISABLE_SELFHEALING_AUTH"] = "true"

# 추가됨 (fixture - 테스트 격리)
@pytest.fixture(autouse=True)
def disable_selfhealing_auth_for_canary_tests(monkeypatch):
    """Canary 테스트에서만 SelfHealing 인증 비활성화 (테스트 격리)"""
    monkeypatch.setenv("DISABLE_SELFHEALING_AUTH", "true")
    yield
```

---

### 이슈 #3: WAL closed 오류 → ✅ 해결됨

**상태:** ✅ 해결됨

**증상:**
```
CircuitBreakerWAL._closed = True (WAL is closed)
```

**원인:**
- 테스트에서 기본 Redis 기반 `CircuitBreakerStateRepository` 사용
- Redis WAL이 닫힌 상태에서 접근 시도

**수정:**
- `InMemoryCircuitBreakerStateRepository` 사용으로 변경
- 파일: `tests/self_healing/chaos/test_recovery_during_chaos.py`

```python
from selfhealing.adapters.memory.circuit_breaker import InMemoryCircuitBreakerStateRepository

memory_repo = InMemoryCircuitBreakerStateRepository()
cb_service = CircuitBreakerService(repository=memory_repo)
```

---

### 이슈 #4: tests/hybrid/ fixture 누락 (23개) → ✅ 해결됨

**상태:** ✅ 해결됨

**증상:**
```
fixture 'user_factory' not found
fixture 'api_client' not found
```

**원인:**
- pytest가 `tests/hybrid/` 폴더만 직접 실행 시 `shopping/tests/conftest.py`가 자동 로드되지 않음
- docker-compose.test.yml의 test-hybrid 서비스가 `pytest tests/hybrid/`로 직접 실행

**수정:**
- `tests/hybrid/conftest.py` 신규 생성
- 필요한 fixture 정의: `user_factory`, `seller_user`, `category`, `product`, `api_client`, `force_eager_mode`

---

### 이슈 #5: RedisDLQRepository 메서드 오류 → ✅ 수정 완료

**상태:** ✅ 수정됨

**원인:**
- 테스트에서 존재하지 않는 `list_pending()` 메서드 호출
- 실제 코드에는 `get_pending()` 메서드만 존재

**수정:**
- 파일: `tests/self_healing/integration/test_multi_cluster_namespace.py`
- `list_pending(limit=10)` → `get_pending(limit=10)` (2곳)
