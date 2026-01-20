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
| `tests/factories/redis.py` | ✅ 완료 | - | MockRedisClient, MockPipeline, MockDistributedLock (eval, evalsha, script_load 포함) |
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
388 passed in 3.76s
```

---

## 3. Phase 3: DLQ Migration ✅ 완료

### 3.1 대상 파일 목록

```
packages/selfhealing-python/tests/services/dlq/
```

| 파일 | Mock 사용 | 상태 | 비고 |
|------|----------|------|------|
| `test_entry_operations.py` | TestDataFactory.mock_failed_operation | ✅ 완료 | Factory 패턴 이미 적용됨 |
| `test_list_operations.py` | TestDataFactory.mock_failed_operation | ✅ 완료 | Factory 패턴 이미 적용됨 |

### 3.2 테스트 결과

```
19 passed in 0.60s
```

---

## 4. Phase 4: Audit Migration ✅ 완료

### 4.1 MockRedisClient 통합

| 위치 | 상태 | 비고 |
|------|------|------|
| `unit/audit/hash_chain_core/conftest.py` | ✅ 완료 | `from tests.factories import MockRedisClient` |
| `unit/audit/graceful_degradation/conftest.py` | ✅ 완료 | `from tests.factories import MockRedisClient, MockDistributedLock` |
| `unit/audit/hash_chain_performance/conftest.py` | ✅ 완료 | `from tests.factories import MockRedisClient` (203줄 → 26줄) |
| `unit/audit/forensic_bridge/conftest.py` | ✅ 완료 | MockAuditAdapter만 사용 (MockRedisClient 미사용) |

### 4.2 Audit 테스트 파일 마이그레이션

```
packages/selfhealing-python/tests/unit/audit/
```

| 파일 | 상태 | 비고 |
|------|------|------|
| `test_redis_hash_chain.py` | ✅ 완료 | 로컬 MockRedisClient 삭제, factories 사용 |
| `test_hash_chain_safety.py` | ✅ 완료 | 로컬 MockRedisClient 삭제, factories 사용 |
| `test_audit_helpers.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_audit_helpers_celery_tasks.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_audit_helpers_compliance_finops.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_audit_helpers_retry_rollback.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_audit_system_fixes.py` | ✅ 마이그레이션 불필요 | Mock/MagicMock만 사용 |
| `test_audit_wal_zero_loss.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_audit_watchdog.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_continuous_audit.py` | ✅ 마이그레이션 불필요 | Mock만 사용 |
| `test_rbac_audit_actor_roles.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_rbac_audit_wal_service_integration.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_self_audit.py` | ✅ 마이그레이션 불필요 | 로컬 Mock 없음 |

```
packages/selfhealing-python/tests/audit/
```

| 파일 | 상태 | 비고 |
|------|------|------|
| `test_backends.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_dlq_hybrid_buffer_pattern.py` | ⚠️ 선택적 | MockRequest 인라인 정의 (간단한 구조) |
| `test_env_snapshot.py` | ✅ 마이그레이션 불필요 | mock 패치만 사용 |
| `test_event_buffer.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_integrity.py` | ✅ 마이그레이션 불필요 | 로컬 Mock 없음 |
| `test_logger.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_masking.py` | ⚠️ 선택적 | MockRequest 인라인 정의 5곳 (간단한 구조) |
| `test_resilience.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |
| `test_trace.py` | ✅ 마이그레이션 불필요 | MagicMock만 사용 |

### 4.3 테스트 결과

```
# hash_chain 관련 테스트
62 passed (test_redis_hash_chain.py + test_hash_chain_safety.py)

# hash_chain_performance 테스트
38 passed in 11.69s
```

---

## 5. Phase 5: Optimization ✅ 분석 완료

### 5.1 FreezeTime 도입

| 작업 | 상태 | 비고 |
|------|------|------|
| `freezegun` 패키지 추가 | ✅ 완료 | requirements-dev.txt에 이미 포함 (`freezegun>=1.2.0`) |
| `tests/factories/time_helpers.py` | ✅ 완료 | `freeze_time` 래퍼 구현됨 |

### 5.2 time.sleep 사용 현황

| 분류 | 파일 수 | 호출 수 | 상태 |
|------|---------|---------|------|
| 실제 대기 필요 (비동기/스레드) | ~20 | ~60 | ⚠️ 유지 |
| 시간 기반 로직 테스트 | ~10 | ~30 | 🔄 점진적 개선 가능 |
| 테스트 안정성용 짧은 sleep | ~15 | ~10 | ⚠️ 검토 필요 |

**주요 사용처:**
- `test_audit_watchdog.py`: heartbeat 테스트 (0.1~0.3초)
- `test_graceful_shutdown.py`: 종료 시간 테스트
- `test_platinum_sla_optimization.py`: SLA 타이머 테스트
- `test_cache_provider.py`: TTL 만료 테스트

**권장 사항:**
- TTL/만료 테스트 → `freezegun` 사용으로 대체 가능
- 비동기/스레드 대기 → 현재 유지 (Event 기반 변경은 대규모 리팩토링 필요)

---

## 6. 진행 상황 요약

| Phase | 파일 수 | 완료 | 진행률 |
|-------|---------|------|--------|
| Phase 1: Foundation | 6 | 6 | 100% ✅ |
| Phase 2: CircuitBreaker | 15 | 15 | 100% ✅ |
| Phase 3: DLQ | 2 | 2 | 100% ✅ |
| Phase 4: Audit | 26 | 26 | 100% ✅ |
| Phase 5: Optimization | - | - | 분석 완료 ✅ |
| **Total** | **49** | **49** | **100%** |

---

## 7. 검증 명령어

```bash
# 전체 테스트 실행
cd packages/selfhealing-python
python -m pytest tests/ -v --tb=short

# 특정 Phase 테스트
python -m pytest tests/services/circuit_breaker/ -v  # 388 passed
python -m pytest tests/services/dlq/ -v               # 19 passed
python -m pytest tests/unit/audit/ -v                 # 100+ passed
python -m pytest tests/factories/ -v                  # 45 passed
```

---

## 8. 롤백 계획

만약 마이그레이션 중 문제 발생 시:

1. **Git 브랜치 분리**: `feature/test-factory-migration`
2. **단계별 커밋**: 각 Phase 완료 시 커밋
3. **기존 코드 백업**: Mock 함수 삭제 전 주석 처리
4. **점진적 적용**: 한 번에 전체 적용 X, 파일 단위 적용

---

## 9. 완료 기록

| 일자 | 작업 | 결과 |
|------|------|------|
| 2026-01-20 | Phase 1 (Factory Foundation) | ✅ 6개 파일 생성, 45개 테스트 통과 |
| 2026-01-20 | Phase 2 (Circuit Breaker) | ✅ 4개 파일 마이그레이션, 388개 테스트 통과 |
| 2026-01-20 | Phase 3 (DLQ) | ✅ 이미 Factory 패턴 적용됨, 19개 테스트 통과 |
| 2026-01-20 | Phase 4 (Audit) | ✅ 3개 파일 마이그레이션, MockRedisClient 확장 (eval, evalsha, script_load) |
| 2026-01-20 | Phase 5 (Optimization) | ✅ 분석 완료, freezegun 이미 설치됨, sleep 현황 파악 |

### 주요 코드 변경사항

**factories/redis.py 확장:**
- `eval()`: Lua 스크립트 패턴 3가지 지원 (lock release, atomic sequence, pending commit)
- `evalsha()`: SHA 기반 스크립트 실행
- `script_load()`: 스크립트 로드 및 SHA 반환
- `pexpire()`: 밀리초 TTL 설정
- MockPipeline에 `exists()`, `hgetall()` 추가

**마이그레이션 파일:**
- `test_redis_hash_chain.py`: 로컬 MockRedisClient (100줄) 삭제 → factories 사용
- `test_hash_chain_safety.py`: 로컬 MockRedisClient (60줄) 삭제 → factories 사용
- `hash_chain_performance/conftest.py`: 로컬 MockRedisClient+MockPipeline (180줄) 삭제 → factories 사용

