# 289. 넘버링 로그 필드 리네이밍 — 기타 모듈 + 구현 체크리스트 (17건)

> **문서 번호**: 289
> **작성일**: 2026-02-25
> **상태**: 구현 대기
> **대상**: `packages/selfhealing-python/src/selfhealing/{settings/,meta/,decorators/,factory.py,metrics/,multiregion/}`
> **관련 문서**: 284_LOG_FIELD_NAMING_OVERVIEW.md

---

## 1. 대상 범위

기타 모듈: 설정 검증 로그, 메타 워치독, 데코레이터, 팩토리, 메트릭, 멀티리전.

**총 17건, 10개 파일.**

---

## 2. 변환 테이블

### 2.1 settings/cascade_retention.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 148 | `self_1` | `self.warm_retention_days` | `warm_retention_days` | |
| 154 | `self_1` | `self.cold_retention_days` | `cold_retention_days` | 같은 `self_1` 다른 의미 |

### 2.2 settings/hash_chain.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 96 | `self_1` | `self.merge_swap_blocking_timeout_seconds` | `merge_swap_blocking_timeout` | |
| 102 | `self_1` | `self.date_lock_blocking_timeout_seconds` | `date_lock_blocking_timeout` | 같은 `self_1` 다른 의미 |

### 2.3 settings/xtest_cleanup.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 159 | `_xtest_cleanup_settings_1` | `_xtest_cleanup_settings.cleanup_interval_minutes` | `cleanup_interval_minutes` | 가장 긴 접두사 |

### 2.4 meta/escalation.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 189 | `event_1` | `event.title` | `title` | |
| 252 | `event_1` | `event.title` | `title` | L189와 동일 패턴 |

### 2.5 meta/fallback_escalation.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 164 | `entry_1` | `entry['title']` | `title` | |

### 2.6 decorators/domain_tag.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 102 | `self_1` | `self._previous_domain` | `previous_domain` | |

### 2.7 factory.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 632 | `cls_1` | `cls._default_queue` | `default_queue` | |
| 633 | `cls_2` | `cls._default_repo` | `default_repo` | |

### 2.8 metrics/safe_gauge/core.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 386 | `self_3` | `self._max_label_combinations` | `max_label_combinations` | gap: `self_1/2` 없음 |

### 2.9 multiregion/conflict.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 287 | `incoming_conflict_key_2` | `incoming_conflict_key.region_priority` | `region_priority` | gap |

### 2.10 multiregion/heartbeat.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 110 | `self_1` | `self.HEARTBEAT_INTERVAL` | `heartbeat_interval` | |
| 111 | `self_2` | `self.HEARTBEAT_TTL` | `heartbeat_ttl` | |

---

## 3. 변환 예시 (Before/After)

### 3.1 settings/hash_chain.py

```python
# Before (L96)
logger.info("hash_chain.merge_swap_timeout_configured",
    self_1=self.merge_swap_blocking_timeout_seconds,
)

# After
logger.info("hash_chain.merge_swap_timeout_configured",
    merge_swap_blocking_timeout=self.merge_swap_blocking_timeout_seconds,
)
```

### 3.2 factory.py

```python
# Before (L632-633)
logger.info("selfhealing.factory_initialized",
    cls_1=cls._default_queue,
    cls_2=cls._default_repo,
)

# After
logger.info("selfhealing.factory_initialized",
    default_queue=cls._default_queue,
    default_repo=cls._default_repo,
)
```

---

## 4. 구현 우선순위 및 실행 가이드

### 4.1 구현 순서

전체 6개 문서(284~289) 중 **구현 문서는 285~289**이다. 아래 순서로 구현한다.

| 순서 | 문서 | 대상 | 건수 | 우선 이유 |
|---|---|---|---|---|
| **1** | **289** | settings, meta, decorators 등 기타 | 17건 | **건수 최소, 위험 최소**. 파일 간 의존성 없음. 변환 패턴이 단순하여 전체 프로세스를 검증하는 파일럿으로 적합. |
| **2** | **285** | services/chaos/ | 66건 | **반복 패턴이 일관적**. 대부분의 chaos experiment가 `target_service`, `effective_ttl`, `injection_rate_pct` 동일 구조를 사용하므로 일괄 치환이 용이. 이 66건을 처리하면 전체의 22%가 완료. |
| **3** | **288** | audit, core, coordination, tasks | 56건 | **핵심 계층이지만 독립적**. `core/resource_monitor.py`(7건)의 단위 접미사 패턴, `core/hedging/result_validator.py`(6건)의 최다 넘버링 등 명명 규칙의 모든 케이스를 포함. |
| **4** | **287** | api/django, adapters, celery_tasks | 51건 | **외부 인터페이스 → 테스트 커버리지 높음**. 기존 integration test에서 로그 필드를 assert할 가능성이 가장 높으므로 테스트 수정 범위 파악 후 진행. |
| **5** | **286** | services/ (chaos 제외) | 72건 | **건수 최다, 충돌 케이스 가장 복잡**. `control_api_service/service.py`(8건, 교차 접두사 넘버링), `replay_service/service.py`(5건, `batch_result_2` 의미 반전), `emergency_mode/manager.py`(3건, `level`/`level_1` 충돌) 등 까다로운 충돌 해결이 모여있으므로 마지막에 진행. |

### 4.2 단계별 실행 절차

각 문서 구현 시 아래 절차를 따른다:

#### Step 1: 테스트 영향 조사

```bash
# 해당 모듈의 넘버링 필드가 테스트에서 참조되는지 확인
grep -rn "<old_field_name>" tests/ --include="*.py"
```

#### Step 2: 소스 코드 변환

- 변환 테이블의 `현재 필드명` → `변환 후 필드명`으로 교체
- **반드시 같은 logger 호출 내의 다른 kwargs와 충돌이 없는지 확인**
- `_self=` 등 기존 비넘버링 kwargs는 변경하지 않음

#### Step 3: 테스트 코드 동기화

테스트에서 로그 출력을 assert하는 경우 필드명도 함께 변경.

#### Step 4: 검증

```bash
# 타입 체크
python -m mypy packages/selfhealing-python/src/selfhealing/<target_module>

# 기존 테스트 통과 확인
python -m pytest tests/unit/<target_module> -x -q

# 넘버링 필드가 남아있지 않은지 확인
grep -rnE '[a-z_]+_[0-9]+=' packages/selfhealing-python/src/selfhealing/<target_module> --include="*.py"
```

#### Step 5: 커밋

```
fix(logging): <module_name> 넘버링 로그 필드 의미 기반 리네이밍

문서 28N 기준으로 <N>건의 넘버링 로그 필드를 의미 기반 이름으로 변환.
- self_1, result_2 등 → target_service, failed_count 등
- 기존 비넘버링 kwargs(_self=, result= 등)는 유지

Refs: docs/self_healing/middleware_system/28N_LOG_FIELD_NAMING_XXX.md
```

### 4.3 구현 체크리스트

| # | 파일 | 건수 | 문서 | 상태 |
|---|---|---|---|---|
| 1 | settings/cascade_retention.py | 2 | 289 | ☐ |
| 2 | settings/hash_chain.py | 2 | 289 | ☐ |
| 3 | settings/xtest_cleanup.py | 1 | 289 | ☐ |
| 4 | meta/escalation.py | 2 | 289 | ☐ |
| 5 | meta/fallback_escalation.py | 1 | 289 | ☐ |
| 6 | decorators/domain_tag.py | 1 | 289 | ☐ |
| 7 | factory.py | 2 | 289 | ☐ |
| 8 | metrics/safe_gauge/core.py | 1 | 289 | ☐ |
| 9 | multiregion/conflict.py | 1 | 289 | ☐ |
| 10 | multiregion/heartbeat.py | 2 | 289 | ☐ |
| 11 | services/chaos/base/experiment.py | 6 | 285 | ☐ |
| 12 | services/chaos/base/ttl_helper.py | 1 | 285 | ☐ |
| 13 | services/chaos/experiments/audit.py | 3 | 285 | ☐ |
| 14 | services/chaos/experiments/cascade.py | 3 | 285 | ☐ |
| 15 | services/chaos/experiments/circuit_breaker.py | 2 | 285 | ☐ |
| 16 | services/chaos/experiments/http_errors.py | 6 | 285 | ☐ |
| 17 | services/chaos/experiments/infrastructure.py | 8 | 285 | ☐ |
| 18 | services/chaos/experiments/latency.py | 5 | 285 | ☐ |
| 19 | services/chaos/experiments/network.py | 9 | 285 | ☐ |
| 20 | services/chaos/experiments/rate_limit.py | 2 | 285 | ☐ |
| 21 | services/chaos/experiments/resource.py | 6 | 285 | ☐ |
| 22 | services/chaos/experiments/timeout.py | 3 | 285 | ☐ |
| 23 | services/chaos/reports.py | 6 | 285 | ☐ |
| 24 | services/chaos/blast_radius.py | 2 | 285 | ☐ |
| 25 | services/chaos/safety_guard/resource_guard.py | 1 | 285 | ☐ |
| 26 | services/chaos/scheduler/service.py | 1 | 285 | ☐ |
| 27 | services/chaos/synthetic_load.py | 2 | 285 | ☐ |
| 28 | services/chaos/actionable_alert_urls.py | 1 | 285 | ☐ |
| 29 | audit/checkpoint_strategy.py | 1 | 288 | ☐ |
| 30 | audit/env_snapshot.py | 2 | 288 | ☐ |
| 31 | audit/graceful_degradation/circuit_breaker.py | 1 | 288 | ☐ |
| 32 | audit/integrity/cross_cluster_linker.py | 1 | 288 | ☐ |
| 33 | audit/integrity/reconciler.py | 1 | 288 | ☐ |
| 34 | audit/logger.py | 3 | 288 | ☐ |
| 35 | audit/performance/async_writer.py | 1 | 288 | ☐ |
| 36 | audit/persistence/disk_buffer.py | 1 | 288 | ☐ |
| 37 | audit/persistence/migration.py | 5 | 288 | ☐ |
| 38 | audit/reconciler.py | 1 | 288 | ☐ |
| 39 | audit/sync_worker.py | 1 | 288 | ☐ |
| 40 | core/action_executor.py | 4 | 288 | ☐ |
| 41 | core/hedging/result_validator.py | 6 | 288 | ☐ |
| 42 | core/hooks.py | 2 | 288 | ☐ |
| 43 | core/resource_monitor.py | 7 | 288 | ☐ |
| 44 | core/runtime_feedback.py | 3 | 288 | ☐ |
| 45 | core/safety_bounds.py | 2 | 288 | ☐ |
| 46 | core/tiered_redis.py | 1 | 288 | ☐ |
| 47 | coordination/etcd_elector.py | 1 | 288 | ☐ |
| 48 | coordination/redis_elector.py | 1 | 288 | ☐ |
| 49 | coordination/scheduler.py | 1 | 288 | ☐ |
| 50 | tasks/backpressure_mixin.py | 2 | 288 | ☐ |
| 51 | tasks/base.py | 1 | 288 | ☐ |
| 52 | tasks/canary_watchdog.py | 2 | 288 | ☐ |
| 53 | tasks/compliance_tasks.py | 3 | 288 | ☐ |
| 54 | tasks/drift_detection.py | 1 | 288 | ☐ |
| 55 | tasks/intelligence_tasks.py | 1 | 288 | ☐ |
| 56 | tasks/traffic_aware_replay.py | 2 | 288 | ☐ |
| 57 | tasks/xtest_cleanup_tasks.py | 3 | 288 | ☐ |
| 58 | api/django/middleware/permissions.py | 2 | 287 | ☐ |
| 59 | api/django/middleware/self_healing.py | 3 | 287 | ☐ |
| 60 | api/django/permissions.py | 1 | 287 | ☐ |
| 61 | api/django/pool_circuit_breaker.py | 8 | 287 | ☐ |
| 62 | api/django/throttle_adapter.py | 1 | 287 | ☐ |
| 63 | api/django/tiering/registry.py | 1 | 287 | ☐ |
| 64 | api/django/views/dlq.py | 2 | 287 | ☐ |
| 65 | api/django/views/l2_storage_drift.py | 3 | 287 | ☐ |
| 66 | api/django/views/l2_storage_shadow_log.py | 1 | 287 | ☐ |
| 67 | api/django/views/xtest/base.py | 1 | 287 | ☐ |
| 68 | api/django/views/xtest/rate_limit.py | 1 | 287 | ☐ |
| 69 | api/django/views/xtest/replay.py | 2 | 287 | ☐ |
| 70 | api/django/views/xtest/retry.py | 1 | 287 | ☐ |
| 71 | adapters/audit/kafka_adapter.py | 1 | 287 | ☐ |
| 72 | adapters/audit/kafka_consumer.py | 3 | 287 | ☐ |
| 73 | adapters/celery/signal_hooks.py | 4 | 287 | ☐ |
| 74 | adapters/celery/tasks/circuit_breaker.py | 1 | 287 | ☐ |
| 75 | adapters/django/apps.py | 2 | 287 | ☐ |
| 76 | adapters/kafka/consumer.py | 4 | 287 | ☐ |
| 77 | adapters/kafka/producer.py | 3 | 287 | ☐ |
| 78 | adapters/queues/sync_adapter.py | 1 | 287 | ☐ |
| 79 | celery_tasks/circuit_breaker_tasks.py | 1 | 287 | ☐ |
| 80 | celery_tasks/dlq_tasks.py | 7 | 287 | ☐ |
| 81 | services/adaptive_replay.py | 5 | 286 | ☐ |
| 82 | services/audit/chaos_audit.py | 1 | 286 | ☐ |
| 83 | services/canary/bypass_audit.py | 4 | 286 | ☐ |
| 84 | services/canary/feature_flag.py | 1 | 286 | ☐ |
| 85 | services/canary/mid_apply_checker.py | 1 | 286 | ☐ |
| 86 | services/canary/pause_tracker.py | 1 | 286 | ☐ |
| 87 | services/canary/service.py | 1 | 286 | ☐ |
| 88 | services/cell_topology/registry.py | 2 | 286 | ☐ |
| 89 | services/circuit_breaker/actionable_alert_urls.py | 2 | 286 | ☐ |
| 90 | services/circuit_breaker/adaptive_threshold.py | 1 | 286 | ☐ |
| 91 | services/circuit_breaker/load_shedding/manager.py | 1 | 286 | ☐ |
| 92 | services/circuit_breaker/panic_threshold.py | 2 | 286 | ☐ |
| 93 | services/circuit_breaker/service_config.py | 1 | 286 | ☐ |
| 94 | services/config/propagator.py | 3 | 286 | ☐ |
| 95 | services/control_api_service/service.py | 8 | 286 | ☐ |
| 96 | services/coordination/pending_recovery_approval.py | 1 | 286 | ☐ |
| 97 | services/coordination/recovery_coordinator/_approval.py | 1 | 286 | ☐ |
| 98 | services/coordination/recovery_coordinator/_session_persistence.py | 2 | 286 | ☐ |
| 99 | services/coordination/redis_key_guard.py | 1 | 286 | ☐ |
| 100 | services/correlation_engine/service.py | 2 | 286 | ☐ |
| 101 | services/dlq/entry_operations.py | 1 | 286 | ☐ |
| 102 | services/dlq/replay_operations.py | 5 | 286 | ☐ |
| 103 | services/emergency_mode/manager.py | 3 | 286 | ☐ |
| 104 | services/error_budget/reconciliation/period_tracker.py | 1 | 286 | ☐ |
| 105 | services/error_budget/weighted_audit.py | 3 | 286 | ☐ |
| 106 | services/error_budget_gate/fault_detector.py | 1 | 286 | ☐ |
| 107 | services/execution_services/chaos_service.py | 3 | 286 | ☐ |
| 108 | services/governance/service.py | 1 | 286 | ☐ |
| 109 | services/learning/service.py | 1 | 286 | ☐ |
| 110 | services/namespace_emergency/partition_reconciliation.py | 2 | 286 | ☐ |
| 111 | services/postmortem/deep_links.py | 2 | 286 | ☐ |
| 112 | services/postmortem/deployment_correlator.py | 1 | 286 | ☐ |
| 113 | services/postmortem/incident_group.py | 1 | 286 | ☐ |
| 114 | services/postmortem/notifier.py | 1 | 286 | ☐ |
| 115 | services/postmortem/revision.py | 3 | 286 | ☐ |
| 116 | services/postmortem/snapshot_builder.py | 1 | 286 | ☐ |
| 117 | services/predictive_forecaster/time_series.py | 2 | 286 | ☐ |
| 118 | services/replay_service/service.py | 5 | 286 | ☐ |
| 119 | services/retry_handler/handler.py | 1 | 286 | ☐ |
| 120 | services/security/hooks.py | 1 | 286 | ☐ |
| 121 | services/system_metrics_cache.py | 1 | 286 | ☐ |
| 122 | services/throttle/adaptive/__init__.py | 4 | 286 | ☐ |
| 123 | services/throttle/adaptive/_error_budget.py | 2 | 286 | ☐ |
| 124 | services/throttle/adaptive_dlq_replay.py | 2 | 286 | ☐ |
| 125 | services/throttle/dlq_integration.py | 2 | 286 | ☐ |
| 126 | services/throttle/registry.py | 3 | 286 | ☐ |
| 127 | services/throttle/throttle_sla_alert_urls.py | 2 | 286 | ☐ |
| 128 | services/unified_notification/service.py | 1 | 286 | ☐ |
| 129 | services/xtest_session_manager.py | 1 | 286 | ☐ |

**합계: 295건 / 79개 파일**
