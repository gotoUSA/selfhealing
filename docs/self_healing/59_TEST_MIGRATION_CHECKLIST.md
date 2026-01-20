# 59. Test Migration Checklist

> **의존**: [57_TEST_REFACTORING_PLAN.md](57_TEST_REFACTORING_PLAN.md), [58_TEST_FACTORY_IMPLEMENTATION.md](58_TEST_FACTORY_IMPLEMENTATION.md)  
> **목적**: 파일별 마이그레이션 체크리스트 및 진행 상황 추적  
> **최종 업데이트**: 2026-01-20

---

## 1. Phase 1: Factory Foundation ✅ 완료

### 1.1 신규 파일 생성

| 파일 | 상태 | 담당 | 비고 |
|------|------|------|------|
| `tests/factories/__init__.py` | ✅ 완료 | - | TestDataFactory, 모든 export |
| `tests/factories/data_factory.py` | ✅ 완료 | - | TestDataFactory, MockCircuitBreakerStateData |
| `tests/factories/repositories.py` | ✅ 완료 | - | InMemoryCircuitBreakerRepository, InMemoryDLQRepository, InMemoryRateLimitTracker |
| `tests/factories/redis.py` | ✅ 완료 | - | MockRedisClient, MockPipeline, MockDistributedLock |
| `tests/factories/constants.py` | ✅ 완료 | - | Domains, Services, FailureTypes, Status, CircuitState, DefaultValues |
| `tests/factories/time_helpers.py` | ✅ 완료 | - | freeze_time, mock_sleep, get_fixed_datetime |

### 1.2 예제 테스트 작성

| 테스트 | 상태 | 비고 |
|--------|------|------|
| Factory 기본 동작 테스트 | ✅ 완료 | test_factories.py (45개 테스트) |
| Repository 테스트 | ✅ 완료 | InMemoryCircuitBreakerRepository, InMemoryDLQRepository |
| MockRedisClient 테스트 | ✅ 완료 | String, Hash, List, Pipeline, 실패 모드 |
| 상수 테스트 | ✅ 완료 | 모든 상수 클래스 값 검증 |
| 시간 헬퍼 테스트 | ✅ 완료 | mock_sleep, get_fixed_datetime, make_datetime_range |

---

## 2. Phase 2: Circuit Breaker Migration ✅ 완료

### 2.1 대상 파일 목록

```
packages/selfhealing-python/tests/services/circuit_breaker/
```

| 파일 | Mock 사용 | 상태 | 비고 |
|------|----------|------|------|
| `test_service.py` | factories.InMemoryCircuitBreakerRepository | ✅ 완료 | Factory 패턴 적용됨 |
| `test_config.py` | MagicMock (외부 의존성) | ✅ 완료 | 마이그레이션 불필요 |
| `test_protection.py` | factories.InMemoryCircuitBreakerRepository | ✅ 완료 | Factory 패턴 적용됨 |
| `test_manual_control.py` | factories.InMemoryCircuitBreakerRepository | ✅ 완료 | Factory 패턴 적용됨 |
| `test_convenience.py` | factories.InMemoryCircuitBreakerRepository | ✅ 완료 | Factory 패턴 적용됨 |
| `test_advanced_protection.py` | 없음 (순수 모델 테스트) | ✅ 완료 | 마이그레이션 불필요 |
| `test_blast_radius_cascade.py` | 없음 (싱글톤 패턴) | ✅ 완료 | 마이그레이션 불필요 |
| `test_canary_recovery_manager.py` | Mock (외부 의존성) | ✅ 완료 | 마이그레이션 불필요 |
| `test_cb_e2e_integration.py` | 없음 (통합 테스트) | ✅ 완료 | 마이그레이션 불필요 |
| `test_cb_kill_switch_adaptive_freeze.py` | Mock (외부 의존성) | ✅ 완료 | 마이그레이션 불필요 |
| `test_cb_load_shedding.py` | ServiceConfig 직접 생성 | ✅ 완료 | 마이그레이션 불필요 |
| `test_rate_limit_tracker.py` | patch (시간 제어) | ✅ 완료 | 마이그레이션 불필요 |
| `test_recovery_strategy_selector.py` | 싱글톤 reset 패턴 | ✅ 완료 | 마이그레이션 불필요 |
| `test_service_config_manager.py` | 없음 (순수 단위 테스트) | ✅ 완료 | 마이그레이션 불필요 |
| `test_stale_cache_integration.py` | 싱글톤 reset 패턴 | ✅ 완료 | 마이그레이션 불필요 |

### 2.2 마이그레이션 결과

**Factory 패턴 적용 파일 (4개):**
- [x] `test_service.py` - `from tests.factories import` 사용
- [x] `test_protection.py` - InMemoryCircuitBreakerRepository, InMemoryRateLimitTracker 사용
- [x] `test_manual_control.py` - MockCircuitBreakerStateData 사용
- [x] `test_convenience.py` - InMemoryCircuitBreakerRepository 사용

**마이그레이션 불필요 파일 (11개):**
- 로컬 Mock 클래스 정의 없음
- 외부 의존성 모킹에만 Mock/MagicMock 사용
- 싱글톤 reset 패턴으로 실제 객체 테스트

### 2.3 테스트 결과

```
388 passed in 1.66s
```

---

## 3. Phase 3: DLQ Migration

### 3.1 대상 파일 목록

```
packages/selfhealing-python/tests/services/dlq/
```

| 파일 | Mock 사용 | Fixture | 상태 |
|------|----------|---------|------|
| `test_entry_operations.py` | make_mock_entry() | 0 | ⬜ TODO |
| `test_list_operations.py` | make_mock_entries() | 0 | ⬜ TODO |

### 3.2 마이그레이션 작업

- [ ] `make_mock_entry` → `F.mock_failed_operation`
- [ ] `make_mock_entries` → `F.failed_operation_list`
- [ ] 도메인 상수화 ("payment" → `Domains.PAYMENT`)

---

## 4. Phase 4: Audit Migration

### 4.1 MockRedisClient 통합

| 위치 | 줄 수 | 상태 |
|------|-------|------|
| `unit/audit/hash_chain_core/conftest.py` | 178줄 | ⬜ TODO |
| `unit/audit/graceful_degradation/conftest.py` | 212줄 | ⬜ TODO |

**작업:**
- [ ] `MockRedisClient` 클래스 삭제
- [ ] `from tests.factories.redis import MockRedisClient` import
- [ ] fixture 수정

### 4.2 Audit 테스트 파일

```
packages/selfhealing-python/tests/unit/audit/
```

| 파일 | 상태 |
|------|------|
| `test_audit_helpers.py` | ⬜ TODO |
| `test_audit_helpers_celery_tasks.py` | ⬜ TODO |
| `test_audit_helpers_compliance_finops.py` | ⬜ TODO |
| `test_audit_helpers_retry_rollback.py` | ⬜ TODO |
| `test_audit_system_fixes.py` | ⬜ TODO |
| `test_audit_wal_zero_loss.py` | ⬜ TODO |
| `test_audit_watchdog.py` | ⬜ TODO |
| `test_continuous_audit.py` | ⬜ TODO |
| `test_hash_chain_safety.py` | ⬜ TODO |
| `test_rbac_audit_actor_roles.py` | ⬜ TODO |
| `test_rbac_audit_wal_service_integration.py` | ⬜ TODO |
| `test_redis_hash_chain.py` | ⬜ TODO |
| `test_self_audit.py` | ⬜ TODO |

```
packages/selfhealing-python/tests/audit/
```

| 파일 | 상태 |
|------|------|
| `test_backends.py` | ⬜ TODO |
| `test_dlq_hybrid_buffer_pattern.py` | ⬜ TODO |
| `test_env_snapshot.py` | ⬜ TODO |
| `test_event_buffer.py` | ⬜ TODO |
| `test_integrity.py` | ⬜ TODO |
| `test_logger.py` | ⬜ TODO |
| `test_masking.py` | ⬜ TODO |
| `test_resilience.py` | ⬜ TODO |
| `test_trace.py` | ⬜ TODO |

---

## 5. Phase 5: Optimization

### 5.1 FreezeTime 도입

| 작업 | 파일 수 | 상태 |
|------|---------|------|
| `freezegun` 패키지 추가 | requirements-dev.txt | ⬜ TODO |
| `datetime.now()` → `freezegun` 사용 | ~156개 호출 | ⬜ TODO |

**예시:**
```python
# Before
def test_timeout():
    state.opened_at = datetime.now()
    # 1시간 후 체크하려면 sleep 필요

# After
from freezegun import freeze_time

@freeze_time("2025-01-01 12:00:00")
def test_timeout():
    state.opened_at = datetime.now()  # 고정된 시간
    
    with freeze_time("2025-01-01 13:00:00"):
        # 1시간 후 로직 테스트
```

### 5.2 sleep 제거

| 작업 | 파일 수 | 상태 |
|------|---------|------|
| `time.sleep()` 호출 분석 | 91개 | ⬜ TODO |
| 시간 관련 → FreezeTime 교체 | TBD | ⬜ TODO |
| 비동기 대기 → Event 기반 교체 | TBD | ⬜ TODO |

---

## 6. 진행 상황 요약

| Phase | 파일 수 | 완료 | 진행률 |
|-------|---------|------|--------|
| Phase 1: Foundation | 6 | 6 | 100% ✅ |
| Phase 2: CircuitBreaker | 15 | 15 | 100% ✅ |
| Phase 3: DLQ | 2 | 0 | 0% |
| Phase 4: Audit | 24 | 0 | 0% |
| Phase 5: Optimization | - | - | 0% |
| **Total** | **47** | **21** | **45%** |

---

## 7. 검증 명령어

```bash
# 전체 테스트 실행
cd packages/selfhealing-python
python -m pytest tests/ -v --tb=short

# 특정 Phase 테스트
python -m pytest tests/services/circuit_breaker/ -v
python -m pytest tests/services/dlq/ -v
python -m pytest tests/unit/audit/ -v

# Factory 테스트만
python -m pytest tests/factories/ -v
```

---

## 8. 롤백 계획

만약 마이그레이션 중 문제 발생 시:

1. **Git 브랜치 분리**: `feature/test-factory-migration`
2. **단계별 커밋**: 각 Phase 완료 시 커밋
3. **기존 코드 백업**: Mock 함수 삭제 전 주석 처리
4. **점진적 적용**: 한 번에 전체 적용 X, 파일 단위 적용

---

## 9. 다음 액션

1. [x] 이 문서 검토 완료
2. [x] Phase 1 시작 결정
3. [x] `tests/factories/` 디렉토리 생성
4. [x] 첫 번째 파일 마이그레이션 시도
5. [x] Phase 1 완료 (2026-01-20)
6. [x] Phase 2 완료 (2026-01-20)
7. [ ] Phase 3 (DLQ) 시작
8. [ ] Phase 4 (Audit) 시작
9. [ ] Phase 5 (Optimization) 시작
