# 65. 전역 Tests 리팩토링 체크리스트

> **용도**: 새로운 세션에서 리팩토링 진행 시 체크리스트로 활용  
> **관련 문서**: 60-64번 문서
> **최종 업데이트**: 2026-01-20

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

- [ ] `docker-compose -f docker-compose.test.yml up -d` 성공
- [ ] `docker-compose -f docker-compose.test.yml ps` 모든 서비스 healthy

---

## Phase 2: 구조 정리 (62번 문서)

### 2.1 Unit 테스트 이동

- [ ] `tests/self_healing/unit/` 폴더 내용 확인 (61개 파일)
- [ ] 대상 폴더 생성 (`packages/.../tests/unit/core/` 등)
- [ ] 파일 이동 완료
- [ ] import 경로 수정 (필요시)
- [ ] 이동된 테스트 실행 성공
- [ ] 빈 폴더 삭제

### 2.2 conftest.py 정리

- [ ] `tests/self_healing/conftest.py` autouse=False 로 변경
- [ ] 영향받는 테스트에 fixture 명시적 추가
- [ ] `tests/self_healing/chaos/conftest.py` 중복 확인/제거

### 2.3 기타 폴더

- [ ] `tests/_unclassified/` 내용 확인 및 정리

### 2.4 검증

- [ ] 패키지 unit 테스트 실행 성공
- [ ] 전역 통합 테스트 실행 성공

---

## Phase 3: Factory 확장 (63번 문서)

### 3.1 Builder 추가

- [ ] `MockRequestBuilder` 구현
- [ ] `CanaryStageBuilder` 구현
- [ ] `ChaosExperimentBuilder` 구현
- [ ] `WatchdogConfigBuilder` 구현 (선택)
- [ ] `NotificationBuilder` 구현 (선택)

### 3.2 상수 추가

- [ ] `CanaryCluster` 상수 추가
- [ ] `ChaosType`, `ChaosIntensity` 상수 추가
- [ ] `NotificationType`, `NotificationPriority` 상수 추가 (선택)

### 3.3 __init__.py 업데이트

- [ ] 새 Builder export 추가
- [ ] 새 상수 export 추가

### 3.4 검증

- [ ] 새 Builder 단위 테스트 작성/실행
- [ ] 기존 테스트 1개 새 Builder로 리팩토링하여 동작 확인

---

## Phase 4: 통합 테스트 리팩토링 (64번 문서)

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

- [ ] `docker-compose up -d` 전체 서비스 실행
- [ ] `python -m pytest tests/ --override-ini=addopts= --ignore=tests/load/ -v` 전체 통과
- [ ] 하드코딩된 localhost/port grep 결과 없음
- [ ] pytest.skip 우회 패턴 최소화

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
