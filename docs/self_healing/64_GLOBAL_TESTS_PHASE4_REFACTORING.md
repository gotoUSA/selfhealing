# 64. Phase 4: 통합 테스트 리팩토링

> **선행 문서**: 63_GLOBAL_TESTS_PHASE3_FACTORY_EXTENSION.md  
> **목표**: 폴더별 통합 테스트를 Factory/Builder 패턴으로 리팩토링  
> **예상 소요**: 1-2일 (폴더별 진행)

---

## 1. 목표

1. 하드코딩된 값을 constants.py 상수로 교체
2. 직접 객체 생성을 Builder 패턴으로 교체
3. 직접 연결을 Factory 패턴으로 교체
4. try/except 우회 패턴 제거 (Docker 연결 보장)

---

## 2. 폴더별 작업 계획

### 2.1 우선순위 매트릭스

| 폴더 | 파일 수 | 복잡도 | 의존성 | 우선순위 |
|------|--------|--------|--------|---------|
| `tests/hybrid/` | 14개 | 높음 | Celery | 1 |
| `tests/integration/` | 6개 | 중간 | Redis, DB | 2 |
| `tests/self_healing/chaos/` | 20+개 | 높음 | 전체 | 3 |
| `tests/self_healing/api/` | 10개 | 중간 | Django | 4 |
| `tests/self_healing/integration/` | 5+개 | 중간 | Redis | 5 |
| `tests/api/` | 2개 | 낮음 | Django | 6 |

---

## 3. 폴더별 상세 계획

### 3.1 tests/hybrid/ (우선순위 1)

**파일 목록**:
- test_celery_async_mode.py
- test_celery_crash_recovery.py
- test_celery_idempotency.py
- test_celery_worker_crash_recovery.py
- test_idempotency_enforcement.py
- test_idempotency_key.py
- test_notification_sla.py
- test_payment_tasks.py
- test_queue_buildup.py
- test_redis_ttl_idempotency.py
- test_sla_under_load.py
- test_toss_timeout_retry.py
- test_transaction_savepoint_crash.py
- test_transaction_task_timing.py

**주요 리팩토링 포인트**:

| 현재 패턴 | 교체 대상 |
|----------|----------|
| `settings.CELERY_TASK_ALWAYS_EAGER = True` | `celery_eager_mode` fixture |
| `settings.CELERY_TASK_ALWAYS_EAGER = False` | `celery_async_mode` fixture |
| `domain="payment"` | `Domains.PAYMENT` |
| `failure_type="PG_TIMEOUT"` | `FailureTypes.PG_TIMEOUT` |

**예상 변경 파일**: 14개 전체

---

### 3.2 tests/integration/ (우선순위 2)

**파일 목록**:
- selfhealing/test_canary_integration.py
- selfhealing/test_config_history_integration.py
- selfhealing/test_drift_threshold_api.py
- selfhealing/test_rbac_permissions.py
- test_autonomous_tasks.py
- test_hybrid_storage_integration.py

**주요 리팩토링 포인트**:

| 현재 패턴 | 교체 대상 |
|----------|----------|
| `redis.Redis(host="localhost", port=6379)` | `RealRedisClientFactory.create()` |
| `redis_port = int(os.environ.get(...))` | `RedisTestConfig.DEFAULT_PORT` |
| `sample_stages` fixture (직접 정의) | `CanaryStageBuilder` |
| `pytest.skip("Redis not available")` | `@pytest.mark.requires_redis` |

**특별 주의**: test_canary_integration.py
- 8곳의 `pytest.skip("Redis not available")` 제거
- Docker 연결 fixture로 대체

---

### 3.3 tests/self_healing/chaos/ (우선순위 3)

**파일 목록**: 20개+

**주요 리팩토링 포인트**:

| 현재 패턴 | 교체 대상 |
|----------|----------|
| Chaos 실험 설정 직접 생성 | `ChaosExperimentBuilder` |
| 서비스 이름 하드코딩 | `Services.PAYMENT_API` 등 |
| Mock 서비스 직접 생성 | `MockServiceBuilder` (필요시) |

**conftest.py 확인 필요**:
- `tests/self_healing/chaos/conftest.py` 내용 확인
- 상위 conftest.py와 중복 제거

---

### 3.4 tests/self_healing/api/ (우선순위 4)

**파일 목록**:
- test_api_views.py
- test_config_api.py
- test_config_api_history_integration.py
- test_config_descriptions.py
- test_emergency_escalation.py
- test_metric_sync_api.py
- test_rbac_permissions.py
- test_slo_config_api.py
- test_threshold_permission.py

**주요 리팩토링 포인트**:

| 현재 패턴 | 교체 대상 |
|----------|----------|
| Request Mock 직접 생성 | `MockRequestBuilder` |
| `META = {"REMOTE_ADDR": "127.0.0.1"}` | `MockRequestBuilder.with_ip()` |
| User Mock 직접 생성 | `MockRequestBuilder.admin_user()` |

---

### 3.5 tests/self_healing/integration/ (우선순위 5)

**파일 목록**:
- test_multi_cluster_namespace.py
- test_resilient_storage_integration.py
- self_healing/test_rbac_audit_flow.py

**주요 리팩토링 포인트**:

| 현재 패턴 | 교체 대상 |
|----------|----------|
| `REDIS_URL = os.getenv(...)` | `RedisTestConfig` |
| 하드코딩된 URL | 환경변수 또는 상수 |

---

### 3.6 tests/api/ (우선순위 6)

**파일 목록**:
- canary/test_views.py
- test_governance_api.py

**주요 리팩토링 포인트**:

| 현재 패턴 | 교체 대상 |
|----------|----------|
| `domain="payment"` | `Domains.PAYMENT` |
| `domains=["payment"]` | `[Domains.PAYMENT]` |

---

## 4. 공통 리팩토링 패턴

### 4.1 Redis 연결 교체

**Before**:
```python
import redis
client = redis.Redis(host="localhost", port=6379)
```

**After**: `RealRedisClientFactory.create()` 사용

---

### 4.2 Celery 설정 교체

**Before**:
```python
settings.CELERY_TASK_ALWAYS_EAGER = True
# ... test code
settings.CELERY_TASK_ALWAYS_EAGER = False
```

**After**: `celery_eager_mode` fixture 사용

---

### 4.3 도메인 상수 교체

**Before**: 문자열 직접 사용
**After**: `Domains.PAYMENT`, `Services.PAYMENT_API` 등

---

### 4.4 pytest.skip 제거

**Before**:
```python
try:
    client.ping()
except:
    pytest.skip("Redis not available")
```

**After**: `@pytest.mark.requires_redis` 마커 사용

---

## 5. 단계별 실행 절차

### 5.1 각 폴더 작업 순서

1. 현재 테스트 실행하여 baseline 확인
2. 하드코딩된 상수 → constants.py 상수로 교체
3. 직접 연결 → Factory 메서드로 교체
4. 객체 직접 생성 → Builder로 교체
5. try/except 우회 → marker로 교체
6. 테스트 실행하여 통과 확인
7. 커밋

### 5.2 진행 상황 추적

| 폴더 | 파일 | 상태 |
|------|------|------|
| tests/hybrid/ | test_payment_tasks.py | ⬜ TODO |
| tests/hybrid/ | test_celery_async_mode.py | ⬜ TODO |
| ... | ... | ... |

---

## 6. 검증 방법

### 6.1 개별 폴더 테스트

```bash
# Docker 서비스 시작
docker-compose up -d

# 폴더별 테스트
python -m pytest tests/hybrid/ -v --override-ini=addopts=
python -m pytest tests/integration/ -v --override-ini=addopts=
```

### 6.2 전체 테스트

```bash
python -m pytest tests/ -v --override-ini=addopts= --ignore=tests/load/
```

---

## 7. 완료 기준

- [ ] tests/hybrid/ 14개 파일 리팩토링 완료
- [ ] tests/integration/ 6개 파일 리팩토링 완료
- [ ] tests/self_healing/chaos/ 리팩토링 완료
- [ ] tests/self_healing/api/ 리팩토링 완료
- [ ] tests/self_healing/integration/ 리팩토링 완료
- [ ] tests/api/ 리팩토링 완료
- [ ] 전체 통합 테스트 통과 (Docker 환경)
- [ ] 하드코딩된 localhost/port 제거
- [ ] pytest.skip 우회 패턴 제거

---

## 8. 롤백 계획

문제 발생 시:
1. Git으로 해당 파일 원복
2. 원인 분석 후 재시도
3. 필요시 Phase 3 (Factory 확장)으로 돌아가 Builder 수정

---

## 9. 참고: 파일별 예상 변경량

| 폴더 | 예상 변경 라인 | 비고 |
|------|--------------|------|
| tests/hybrid/ | 200-300줄 | Celery 설정 변경 많음 |
| tests/integration/ | 100-150줄 | Redis 연결 변경 |
| tests/self_healing/chaos/ | 300-400줄 | 가장 많은 파일 수 |
| tests/self_healing/api/ | 150-200줄 | Request Mock 변경 |
| tests/self_healing/integration/ | 50-100줄 | 적은 파일 수 |
| tests/api/ | 20-30줄 | 적은 변경량 |
