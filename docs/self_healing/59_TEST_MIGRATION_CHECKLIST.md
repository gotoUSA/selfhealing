# 59. Test Migration Checklist

> **의존**: [57_TEST_REFACTORING_PLAN.md](57_TEST_REFACTORING_PLAN.md), [58_TEST_FACTORY_IMPLEMENTATION.md](58_TEST_FACTORY_IMPLEMENTATION.md)  
> **목적**: 파일별 마이그레이션 체크리스트 및 진행 상황 추적

---

## 1. Phase 1: Factory Foundation

### 1.1 신규 파일 생성

| 파일 | 상태 | 담당 | 비고 |
|------|------|------|------|
| `tests/factories/__init__.py` | ⬜ TODO | - | TestDataFactory |
| `tests/factories/repositories.py` | ⬜ TODO | - | InMemory Repos |
| `tests/factories/redis.py` | ⬜ TODO | - | MockRedisClient |
| `tests/factories/constants.py` | ⬜ TODO | - | 도메인/서비스 상수 |
| `tests/factories/builders.py` | ⬜ TODO | - | Complex builders |

### 1.2 예제 테스트 작성

| 테스트 | 상태 | 비고 |
|--------|------|------|
| Factory 기본 동작 테스트 | ⬜ TODO | - |
| Repository 테스트 | ⬜ TODO | - |
| MockRedisClient 테스트 | ⬜ TODO | - |

---

## 2. Phase 2: Circuit Breaker Migration

### 2.1 대상 파일 목록

```
packages/selfhealing-python/tests/services/circuit_breaker/
```

| 파일 | Mock 사용 | Fixture | 상태 |
|------|----------|---------|------|
| `test_service.py` | MockRepository, MockCircuitBreakerStateData | 0 | ⬜ TODO |
| `test_config.py` | - | 1 | ⬜ TODO |
| `test_protection.py` | MagicMock | 2 | ⬜ TODO |
| `test_manual_control.py` | MagicMock | 1 | ⬜ TODO |
| `test_convenience.py` | MagicMock | 0 | ⬜ TODO |
| `test_advanced_protection.py` | MagicMock | 3 | ⬜ TODO |
| `test_blast_radius_cascade.py` | MagicMock | 2 | ⬜ TODO |
| `test_canary_recovery_manager.py` | MagicMock | 4 | ⬜ TODO |
| `test_cb_e2e_integration.py` | MagicMock | 5 | ⬜ TODO |
| `test_cb_kill_switch_adaptive_freeze.py` | MagicMock | 3 | ⬜ TODO |
| `test_cb_load_shedding.py` | MagicMock | 2 | ⬜ TODO |
| `test_rate_limit_tracker.py` | MagicMock | 1 | ⬜ TODO |
| `test_recovery_strategy_selector.py` | MagicMock | 2 | ⬜ TODO |
| `test_service_config_manager.py` | MagicMock | 1 | ⬜ TODO |
| `test_stale_cache_integration.py` | MagicMock | 2 | ⬜ TODO |

### 2.2 마이그레이션 작업

각 파일에 대해:

- [ ] `from tests.factories import TestDataFactory as F` 추가
- [ ] 로컬 Mock 클래스/함수 제거
- [ ] `F.mock_circuit_breaker_state()` 사용으로 교체
- [ ] `F.circuit_breaker_config()` 사용으로 교체
- [ ] 테스트 실행 및 통과 확인

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
| Phase 1: Foundation | 5 | 0 | 0% |
| Phase 2: CircuitBreaker | 15 | 0 | 0% |
| Phase 3: DLQ | 2 | 0 | 0% |
| Phase 4: Audit | 24 | 0 | 0% |
| Phase 5: Optimization | - | - | 0% |
| **Total** | **46+** | **0** | **0%** |

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

1. [ ] 이 문서 검토 완료
2. [ ] Phase 1 시작 결정
3. [ ] `tests/factories/` 디렉토리 생성
4. [ ] 첫 번째 파일 마이그레이션 시도
