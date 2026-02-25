# 288. 넘버링 로그 필드 리네이밍 — `audit/`, `core/`, `coordination/`, `tasks/` (56건)

> **문서 번호**: 288
> **작성일**: 2026-02-25
> **상태**: 구현 대기
> **대상**: `packages/selfhealing-python/src/selfhealing/{audit/,core/,coordination/,tasks/}`
> **관련 문서**: 284_LOG_FIELD_NAMING_OVERVIEW.md

---

## 1. 대상 범위

내부 코어 계층: 감사 로깅, 핵심 엔진, 분산 조정, Celery 태스크.
`result_N`, `value_N`, `record_N` 등이 주로 사용된다.

**총 56건, 18개 파일.**

---

## 2. 변환 테이블

### 2.1 audit/checkpoint_strategy.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 679 | `data_2` | `data.kafka_offset` | `kafka_offset` | gap: `data_1` 없음 |

### 2.2 audit/env_snapshot.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 173 | `snapshot_1` | `snapshot['hash']` | `snapshot_hash` | 같은 접두사 다른 의미 |
| 289 | `snapshot_2` | `snapshot['count']` | `snapshot_count` | 다른 로그 호출 |

### 2.3 audit/graceful_degradation/circuit_breaker.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 172 | `self_1` | `self._failure_count` | `failure_count` | |

### 2.4 audit/integrity/cross_cluster_linker.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 368 | `anchor_1` | `anchor.anchor_date` | `anchor_date` | |

### 2.5 audit/integrity/reconciler.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 118 | `result_2` | `result['new_sequence_end']` | `new_sequence_end` | gap |

### 2.6 audit/logger.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 323 | `change_1` | `change.get('config_type', '')` | `config_type` | |
| 324 | `change_2` | `change.get('config_key', '')` | `config_key` | |
| 326 | `actor_4` | `actor.get('ip_address', 'unknown')` | `ip_address` | gap: `actor_1/2/3` 없음 |

### 2.7 audit/performance/async_writer.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 96 | `self_1` | `self._entries_written` | `entries_written` | |

### 2.8 audit/persistence/disk_buffer.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 237 | `self_2` | `self._db_name` | `db_name` | gap: `self_1` 없음 |

### 2.9 audit/persistence/migration.py (5건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 167 | `result_1` | `result.failed` | `failed` | |
| 168 | `result_2` | `result.skipped` | `skipped` | |
| 169 | `result_3` | `result.duration_seconds` | `duration_seconds` | |
| 252 | `result_1` | `result.failed` | `failed` | L167과 동일 패턴 |
| 253 | `result_2` | `result.skipped` | `skipped` | L168과 동일 패턴 |

### 2.10 audit/reconciler.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 351 | `result_1` | `result.resent_count` | `resent_count` | |

### 2.11 audit/sync_worker.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 215 | `self_1` | `self._config.batch_size` | `batch_size` | |

### 2.12 core/action_executor.py (4건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 249 | `action_1` | `action.target` | `target` | 기존 `action=action.name` 존재 → 충돌 없음 |
| 268 | `action_1` | `action.target` | `target` | |
| 296 | `action_1` | `action.target` | `target` | |
| 298 | `action_3` | `action.params` | `params` | gap: `action_2` 없음 |

### 2.13 core/hedging/result_validator.py (6건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 311 | `record_1` | `record.winner_source` | `winner_source` | 기존 `record=record.operation_id` 존재 |
| 312 | `record_2` | `record.winner_region or 'local'` | `winner_region` | |
| 313 | `record_3` | `record.other_source` | `other_source` | |
| 314 | `record_4` | `record.other_region or 'local'` | `other_region` | |
| 315 | `record_5` | `record.mismatch_type` | `mismatch_type` | |
| 316 | `record_6` | `record.estimated_replication_lag_ms` | `replication_lag_ms` | 최대 `_6` |

### 2.14 core/hooks.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 271 | `result_1` | `result.reason` | `reason` | |
| 272 | `result_2` | `result.request_path` | `request_path` | |

### 2.15 core/resource_monitor.py (7건)

기존 kwargs 확인:
- L137-144: `value=max_bytes / 1024 / 1024` 존재 (max_bytes의 MB 변환)
- L206-209: `value=requested_bytes / 1024 / 1024` 존재
- L283-289: `value=current_level / 1024 / 1024`, `minutes_to_oom=minutes_to_oom` 존재

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 140 | `value_1` | `current_bytes / 1024 / 1024` | `current_mb` | 단위 접미사 |
| 141 | `value_2` | `available / 1024 / 1024` | `available_mb` | |
| 142 | `value_3` | `safety_margin * 100` | `safety_margin_pct` | |
| 143 | `value_4` | `safe_available / 1024 / 1024` | `safe_available_mb` | |
| 209 | `value_1` | `available / 1024 / 1024` | `available_mb` | |
| 286 | `value_1` | `safe_limit / 1024 / 1024` | `safe_limit_mb` | |
| 287 | `value_2` | `trend_slope / 1024 / 1024` | `trend_slope_mb` | |

### 2.16 core/runtime_feedback.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 299 | `decision_1` | `decision.suggested_value` | `suggested_value` | |
| 434 | `result_1` | `result.new_value` | `new_value` | |
| 435 | `result_2` | `result.old_value` | `old_value` | |

### 2.17 core/safety_bounds.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 293 | `new_bound_2` | `new_bound.max_value` | `max_value` | gap: `new_bound_1` 없음 |
| 294 | `new_bound_3` | `new_bound.max_change_per_cycle` | `max_change_per_cycle` | |

### 2.18 core/tiered_redis.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 72 | `self_1` | `self._global_url` | `global_url` | |

### 2.19 coordination/etcd_elector.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 247 | `self_1` | `self._fencing_token` | `fencing_token` | |

### 2.20 coordination/redis_elector.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 288 | `self_1` | `self._fencing_token` | `fencing_token` | |

### 2.21 coordination/scheduler.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 365 | `job_1` | `job.run_count` | `run_count` | |

### 2.22 tasks/backpressure_mixin.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 145 | `self_1` | `self._backpressure_retry_count` | `retry_count` | |
| 146 | `self_2` | `self.backpressure_max_retries` | `max_retries` | |

### 2.23 tasks/base.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 208 | `policy_2` | `policy.threshold` | `threshold` | gap: `policy_1` 없음 |

### 2.24 tasks/canary_watchdog.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 254 | `result_1` | `result.zombie_count` | `zombie_count` | |
| 255 | `result_2` | `result.rollback_count` | `rollback_count` | |

### 2.25 tasks/compliance_tasks.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 114 | `report_1` | `report.passed_checks` | `passed_checks` | |
| 115 | `report_2` | `report.failed_checks` | `failed_checks` | |
| 255 | `report_1` | `report.record_count` | `record_count` | 같은 `report_1` 다른 의미 |

### 2.26 tasks/drift_detection.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 339 | `warning_4` | `warning.get('recommendation')` | `recommendation` | gap: `_1/2/3` 없음 |

### 2.27 tasks/intelligence_tasks.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 701 | `shadow_1` | `shadow.estimated_errors` | `estimated_errors` | |

### 2.28 tasks/traffic_aware_replay.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 293 | `result_1` | `result['success']` | `success` | |
| 294 | `result_2` | `result['failed']` | `failed` | |

### 2.29 tasks/xtest_cleanup_tasks.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 66 | `result_1` | `result.cb_states_restored` | `cb_states_restored` | |
| 67 | `result_2` | `result.dlq_entries_purged` | `dlq_entries_purged` | |
| 68 | `result_3` | `result.idempotency_keys_cleared` | `idempotency_keys_cleared` | |

---

## 3. 변환 예시 (Before/After)

### 3.1 core/hedging/result_validator.py — 단일 호출 최다 넘버링 (6개)

```python
# Before (L308-316)
logger.warning(
    "hedging_validator.result_mismatch_detected",
    record=record.operation_id,
    record_1=record.winner_source,
    record_2=record.winner_region or 'local',
    record_3=record.other_source,
    record_4=record.other_region or 'local',
    record_5=record.mismatch_type,
    record_6=record.estimated_replication_lag_ms,
)

# After
logger.warning(
    "hedging_validator.result_mismatch_detected",
    record=record.operation_id,
    winner_source=record.winner_source,
    winner_region=record.winner_region or 'local',
    other_source=record.other_source,
    other_region=record.other_region or 'local',
    mismatch_type=record.mismatch_type,
    replication_lag_ms=record.estimated_replication_lag_ms,
)
```

### 3.2 core/resource_monitor.py — 단위 접미사 적용

```python
# Before (L137-143)
logger.info(
    "cgroup_resource_monitor.mb_mb_mb_safe",
    value=max_bytes / 1024 / 1024,
    value_1=current_bytes / 1024 / 1024,
    value_2=available / 1024 / 1024,
    value_3=safety_margin * 100,
    value_4=safe_available / 1024 / 1024,
)

# After
logger.info(
    "cgroup_resource_monitor.mb_mb_mb_safe",
    value=max_bytes / 1024 / 1024,
    current_mb=current_bytes / 1024 / 1024,
    available_mb=available / 1024 / 1024,
    safety_margin_pct=safety_margin * 100,
    safe_available_mb=safe_available / 1024 / 1024,
)
```

### 3.3 audit/logger.py — 번호 갭 + 교차 접두사

```python
# Before (L323-326)
logger.info("audit.config_changed",
    change=change.get('action', ''),
    change_1=change.get('config_type', ''),
    change_2=change.get('config_key', ''),
    actor=actor.get('user_id', 'system'),
    actor_4=actor.get('ip_address', 'unknown'),
)

# After
logger.info("audit.config_changed",
    change=change.get('action', ''),
    config_type=change.get('config_type', ''),
    config_key=change.get('config_key', ''),
    actor=actor.get('user_id', 'system'),
    ip_address=actor.get('ip_address', 'unknown'),
)
```

---

## 4. 테스트 영향

```bash
# core/audit/tasks 관련 테스트에서 넘버링 필드 참조 확인
grep -rn "record_[0-9]\|value_[0-9]\|result_[0-9]\|self_[0-9]\|action_[0-9]\|report_[0-9]" \
    tests/ --include="*.py" | grep -iE "core|audit|task|coordination"
```
