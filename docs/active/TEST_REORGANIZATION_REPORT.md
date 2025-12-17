# 테스트 파일 물리적 재구성 결과 보고서

**작업 일시**: 2025년 1월
**기준 문서**: [TEST_CLASSIFICATION_FRAMEWORK.md](TEST_CLASSIFICATION_FRAMEWORK.md), [TEST_CLASSIFICATION_RESULTS.md](TEST_CLASSIFICATION_RESULTS.md)

---

## 1. 실행 요약

| 구분 | 파일 수 |
|------|---------|
| **이동된 파일** | 60 files |
| **이동되지 않은 파일 (의존성 위험)** | 52+ files (load_tests/) |
| **원위치 유지 (OUT_OF_SCOPE)** | 145+ files (shopping/) |

---

## 2. 새 디렉토리 구조

```
tests/
├── self_healing/               # Self-Healing 전용 테스트
│   ├── unit/                   # G1-UNIT: 19 files
│   ├── integration/            # G2-INTEGRATION: 14 files
│   ├── e2e/                    # G3-E2E: 3 files
│   ├── load/                   # G4-LOAD: (비어있음 - load_tests/에 유지)
│   └── chaos/                  # G5-CHAOS: 6 files
├── hybrid/                     # HYBRID: 14 files
├── _pending_review/            # AMBIGUOUS: 4 files
├── _out_of_scope/              # (비어있음 - shopping 테스트 원위치 유지)
└── _unclassified/              # conftest.py만 남음 (공유 fixture)
```

---

## 3. 이동된 파일 상세

### 3.1 PURE_HEALING / UNIT_HEALING → `tests/self_healing/unit/` (19 files)

| 파일명 | 이전 위치 |
|--------|----------|
| test_audit_record_schema.py | tests/_unclassified/ |
| test_backoff_jitter_distribution.py | tests/_unclassified/ |
| test_backoff_policy.py | tests/_unclassified/ |
| test_circuit_breaker_service.py | tests/_unclassified/ |
| test_circuit_breaker_ttl.py | tests/_unclassified/ |
| test_cost_aware_recovery.py | tests/_unclassified/ |
| test_dlq_retention.py | tests/_unclassified/ |
| test_failure_classification.py | tests/_unclassified/ |
| test_manual_override_policy.py | tests/_unclassified/ |
| test_metrics_dlq_pending.py | tests/_unclassified/ |
| test_observability_tasks.py | tests/_unclassified/ |
| test_retry_configuration.py | tests/_unclassified/ |
| test_retry_decision_table.py | tests/_unclassified/ |
| test_retry_persistence.py | tests/_unclassified/ |
| test_security_notification_service.py | tests/_unclassified/ |
| test_security_violation_service.py | tests/_unclassified/ |
| test_self_healing_metrics.py | tests/_unclassified/ |
| test_self_healing_policy.py | tests/_unclassified/ |
| test_sla_timer_policy.py | tests/_unclassified/ |

### 3.2 PURE_HEALING → `tests/self_healing/integration/` (14 files)

| 파일명 | 이전 위치 |
|--------|----------|
| test_audit_accountability.py | tests/_unclassified/ |
| test_circuit_breaker.py | tests/_unclassified/ |
| test_circuit_breaker_distributed.py | tests/_unclassified/ |
| test_cold_start_recovery.py | tests/_unclassified/ |
| test_control_api.py | tests/_unclassified/ |
| test_db_connection_recovery.py | tests/_unclassified/ |
| test_dlq_storage_and_replay.py | tests/_unclassified/ |
| test_forensic_replay.py | tests/_unclassified/ |
| test_l3_self_healing.py | tests/_unclassified/ |
| test_multi_tenancy_isolation.py | tests/_unclassified/ |
| test_observability_metrics.py | tests/_unclassified/ |
| test_redis_failure_scenarios.py | tests/_unclassified/ |
| test_redis_fallback.py | tests/_unclassified/ |
| test_security_dlq_separation.py | tests/_unclassified/ |

### 3.3 PURE_HEALING → `tests/self_healing/e2e/` (3 files)

| 파일명 | 이전 위치 |
|--------|----------|
| test_failure_recovery_cycle.py | tests/_unclassified/ |
| test_pool_recovery.py | tests/_unclassified/ |
| test_stage26_circuit_breaker.py | tests/_unclassified/ |

### 3.4 CHAOS_NOT_HEALING → `tests/self_healing/chaos/` (6 files)

| 파일명 | 이전 위치 |
|--------|----------|
| test_cascading_failures.py | tests/_unclassified/ |
| test_chaos_engineering.py | tests/_unclassified/ |
| test_concurrent_failures.py | tests/_unclassified/ |
| test_external_api_failures.py | tests/_unclassified/ |
| test_recovery_during_chaos.py | tests/_unclassified/ |
| test_resource_exhaustion.py | tests/_unclassified/ |

### 3.5 HYBRID → `tests/hybrid/` (14 files)

| 파일명 | 이전 위치 |
|--------|----------|
| test_celery_async_mode.py | tests/_unclassified/ |
| test_celery_crash_recovery.py | tests/_unclassified/ |
| test_celery_idempotency.py | tests/_unclassified/ |
| test_idempotency_enforcement.py | tests/_unclassified/ |
| test_idempotency_key.py | tests/_unclassified/ |
| test_l2_celery_crash.py | tests/_unclassified/ |
| test_l2_transaction_crash.py | tests/_unclassified/ |
| test_notification_sla.py | tests/_unclassified/ |
| test_payment_tasks.py | tests/_unclassified/ |
| test_queue_buildup.py | tests/_unclassified/ |
| test_redis_ttl_idempotency.py | tests/_unclassified/ |
| test_sla_under_load.py | tests/_unclassified/ |
| test_toss_timeout_retry.py | tests/_unclassified/ |
| test_transaction_task_timing.py | tests/_unclassified/ |

### 3.6 AMBIGUOUS → `tests/_pending_review/` (4 files)

| 파일명 | 이전 위치 | 리뷰 필요 사유 |
|--------|----------|---------------|
| test_architectural_resilience_e2e.py | tests/_unclassified/ | B1/B2 경계 모호 |
| test_partial_failure_patterns.py | tests/_unclassified/ | F-AXIS 불명확 |
| test_slow_degradation.py | tests/_unclassified/ | 힐링/관측 경계 |
| test_time_based_behaviors.py | tests/_unclassified/ | 비즈니스/힐링 혼재 |

---

## 4. 이동하지 않은 파일 (의존성 위험)

### 4.1 `load_tests/scenarios/` (52+ files)

**이유**: 패키지 수준 의존성 존재
```python
from load_tests.utils import ...
from load_tests.metrics import ...
from load_tests.chaos import ...
```

파일을 이동하면 import가 깨지므로 원위치 유지합니다.

**분류 결과**: 대부분 **PURE_HEALING (G4-LOAD)**

**향후 조치 권장**:
1. `load_tests/` 패키지 내부에 하위 분류 적용
2. 또는 symbolic link를 통해 `tests/self_healing/load/`에서 참조

### 4.2 `shopping/tests/` (145+ files)

**이유**: **OUT_OF_SCOPE** (비즈니스 도메인 테스트)

**분류 결과**: Stage 0-9 비즈니스 테스트

**조치**: 원위치 유지 (Self-Healing 범위 외)

---

## 5. 남은 파일

### 5.1 `tests/_unclassified/`

| 파일명 | 목적 | 조치 |
|--------|------|------|
| conftest.py | 공유 pytest fixtures | **유지** (삭제하면 테스트 실패) |
| __init__.py | 패키지 마커 | 유지 |

---

## 6. pytest 실행 검증 명령어

```bash
# Self-Healing 테스트만 실행
pytest tests/self_healing/ -v

# Unit 테스트만 실행
pytest tests/self_healing/unit/ -v

# Integration 테스트만 실행
pytest tests/self_healing/integration/ -v

# Chaos 테스트만 실행
pytest tests/self_healing/chaos/ -v

# Hybrid 테스트만 실행
pytest tests/hybrid/ -v

# 전체 테스트 실행
pytest tests/ -v
```

---

## 7. 후속 작업 권장

1. **Import 검증**: 이동된 모든 파일의 import 문 확인
2. **conftest.py 검토**: 각 디렉토리에 필요한 fixtures가 접근 가능한지 확인
3. **CI/CD 업데이트**: 테스트 경로 변경에 따른 CI 설정 수정
4. **AMBIGUOUS 파일 리뷰**: `tests/_pending_review/` 내 4개 파일 분류 결정

---

## 8. 통계 요약

```
┌─────────────────────────────────────────────────────────────────┐
│                    테스트 파일 재구성 결과                        │
├─────────────────────────────────────────────────────────────────┤
│  📁 tests/self_healing/unit/          │  19 files              │
│  📁 tests/self_healing/integration/   │  14 files              │
│  📁 tests/self_healing/e2e/           │   3 files              │
│  📁 tests/self_healing/chaos/         │   6 files              │
│  📁 tests/hybrid/                     │  14 files              │
│  📁 tests/_pending_review/            │   4 files              │
├─────────────────────────────────────────────────────────────────┤
│  총 이동된 파일                        │  60 files              │
│  load_tests/ (원위치)                  │  52+ files (PURE)     │
│  shopping/tests/ (원위치)              │  145+ files (OUT)     │
└─────────────────────────────────────────────────────────────────┘
```

---

**작성자**: GitHub Copilot
**기준 프레임워크**: TEST_CLASSIFICATION_FRAMEWORK.md
