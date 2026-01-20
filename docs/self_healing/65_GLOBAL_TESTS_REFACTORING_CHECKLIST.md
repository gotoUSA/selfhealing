# 65. 전역 Tests 리팩토링 체크리스트

> **용도**: 새로운 세션에서 리팩토링 진행 시 체크리스트로 활용  
> **관련 문서**: 60-64번 문서
> **최종 업데이트**: 2026-01-21

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

## Phase 4: 통합 테스트 리팩토링 (64번 문서)

### 📊 테스트 실행 결과 (2026-01-21)

| 테스트 서비스 | Passed | Failed | Skipped | 시간 |
|--------------|--------|--------|---------|------|
| test-hybrid | 98 | 0 | 0 | ~15s |
| test-chaos | 682 | 0 | 35 | ~45s |
| test-global | 1,372 | 39 | 81 | 71.5s |

#### 🔴 실패 원인 분석 (39개 실패)

**1. RBAC Permission 로직 버그 (37개)**
- `IsViewer`, `IsOperator`, `IsSelfHealingAdmin` 권한 클래스가 인증되지 않은 사용자에게도 `True` 반환
- `ThresholdBasedPermission`, `EmergencyEscalationPermission` 동일 문제
- 근본 원인: `selfhealing/api/django/permissions.py` 권한 검증 로직 문제
- ⚠️ **Phase 4 리팩토링과 무관 - 별도 버그 수정 필요**

**2. API 인증 누락 (2개)**
- `test_sync_requires_admin`: 403 예상 → 200 반환
- `test_get_status_unauthenticated`: 401/403 예상 → 200 반환

**3. 메서드 누락 (1개)**
- `RedisDLQRepository.list_pending()` 없음 (→ `get_pending` 사용 필요)

### 4.1 tests/hybrid/ (14개 파일)

- [ ] test_celery_async_mode.py
- [ ] test_celery_crash_recovery.py
- [ ] test_celery_idempotency.py
- [ ] test_celery_worker_crash_recovery.py
- [ ] test_idempotency_enforcement.py
- [ ] test_idempotency_key.py
- [ ] test_notification_sla.py
- [ ] test_payment_tasks.py
- [ ] test_queue_buildup.py
- [ ] test_redis_ttl_idempotency.py
- [ ] test_sla_under_load.py
- [ ] test_toss_timeout_retry.py
- [ ] test_transaction_savepoint_crash.py
- [ ] test_transaction_task_timing.py

### 4.2 tests/integration/ (6개 파일)

- [ ] selfhealing/test_canary_integration.py
- [ ] selfhealing/test_config_history_integration.py
- [ ] selfhealing/test_drift_threshold_api.py
- [ ] selfhealing/test_rbac_permissions.py
- [ ] test_autonomous_tasks.py
- [ ] test_hybrid_storage_integration.py

### 4.3 tests/self_healing/chaos/ (20+개 파일)

- [ ] 전체 파일 목록 확인 후 순차 진행

### 4.4 tests/self_healing/api/ (10개 파일)

- [ ] test_api_views.py
- [ ] test_config_api.py
- [ ] test_config_api_history_integration.py
- [ ] test_config_descriptions.py
- [ ] test_emergency_escalation.py
- [ ] test_metric_sync_api.py
- [ ] test_rbac_permissions.py
- [ ] test_slo_config_api.py
- [ ] test_threshold_permission.py

### 4.5 tests/self_healing/integration/

- [ ] test_multi_cluster_namespace.py
- [ ] test_resilient_storage_integration.py
- [ ] self_healing/test_rbac_audit_flow.py

### 4.6 tests/api/ (2개 파일)

- [ ] canary/test_views.py
- [ ] test_governance_api.py

---

## 최종 검증

- [x] `docker-compose up -d` 전체 서비스 실행
- [x] `docker-compose -f docker-compose.test.yml run --rm test-hybrid` 실행 (98 passed)
- [x] `docker-compose -f docker-compose.test.yml run --rm test-chaos` 실행 (682 passed, 35 skipped)
- [x] `docker-compose -f docker-compose.test.yml run --rm test-global` 실행 (1372 passed, 39 failed, 81 skipped)
- [ ] RBAC Permission 버그 수정 후 전체 테스트 통과 확인 (별도 이슈)
- [ ] Phase 4 Factory 패턴 적용 완료

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

---

## 🚨 발견된 이슈 (2026-01-21)

### 이슈 #1: RBAC Permission 권한 검증 버그

**영향 받는 파일:**
- `selfhealing/api/django/permissions.py`

**증상:**
- `IsViewer.has_permission()`, `IsOperator.has_permission()`, `IsSelfHealingAdmin.has_permission()` 메서드가 인증되지 않은 사용자에게도 `True` 반환
- Mock 테스트에서 `request.user.groups.filter()` 호출되지 않음

**영향 받는 테스트:**
- `tests/self_healing/api/test_rbac_permissions.py` (15개)
- `tests/integration/selfhealing/test_rbac_permissions.py` (15개)
- `tests/self_healing/api/test_emergency_escalation.py` (6개)
- `tests/self_healing/api/test_threshold_permission.py` (3개)

**우선순위:** 🔴 높음 (보안 관련)

---

### 이슈 #2: RedisDLQRepository 메서드 누락

**영향 받는 파일:**
- `selfhealing/core/dlq/redis_dlq.py` (또는 관련 모듈)

**증상:**
- `list_pending()` 메서드 없음
- 힌트: `get_pending` 메서드 사용 필요

**영향 받는 테스트:**
- `tests/self_healing/integration/test_multi_cluster_namespace.py::TestNamespaceIsolation::test_dlq_namespace_isolation`

**우선순위:** 🟡 중간
