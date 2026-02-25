# 295. 모호한 로그 필드 리네이밍 — audit/, settings/, meta/, metrics/, context/, 기타

> **문서 번호**: 295
> **작성일**: 2026-02-25
> **상태**: 구현 대기
> **대상**: `packages/selfhealing-python/src/selfhealing/` 중 `audit/`, `settings/`, `meta/`, `metrics/`, `context/`, `decorators/`, `factory.py`, `config_tracker.py`, `multiregion/`, `resilience/`, `scaling/`, `utils/`
> **선행 문서**: 290_AMBIGUOUS_LOG_FIELD_OVERVIEW.md
> **구현 우선순위**: ★★☆☆☆ (4순위 — `v=v` 설정 검증 패턴 밀집, 다수 수동 확인 필요)

---

## 1. 범위 요약

| 지표 | 값 |
|---|---|
| 대상 파일 수 | **115개** |
| 모호한 kwargs 인스턴스 | **274건** |
| 불투명 필드(`_self`, `v`, `_event`, `n`, `i` 등) | ~120건 |
| 제네릭 필드(`domain`, `key`, `count`, `name`, `region` 등) | ~154건 |
| 자동 변환 제안 가능 | ~115건 (42%) |
| 수동 컨텍스트 검토 필요(`→수동`) | ~159건 (58%) |

### 1.1 이 문서가 4순위인 이유

1. **`v=v` 패턴 밀집(~45건)** — `settings/` 디렉토리 전체에서 설정값 검증 로그에 `v=v` 반복
2. **`_event` 패턴(~10건)** — `meta/escalation.py`, `multiregion/replicator.py` 등에서 structlog 내부 확인 필요
3. **다양한 기능 모듈** — audit, metrics, multiregion 등 기능이 분산되어 있어 일괄 변환이 어려움

### 1.2 하위 디렉토리별 분포

| 하위 디렉토리 | 건수 | 주요 패턴 |
|---|---|---|
| `settings/` | ~55건 | `v=v` → `→수동_v_의미확인필요` |
| `audit/` | ~75건 | `_self`, `entry=entry.*`, `count=len(...)` |
| `metrics/` | ~35건 | `domain=domain` → `healing_domain`, `_self` |
| `multiregion/` | ~30건 | `_self=self._region`, `region=region` → `target_region` |
| `meta/` | ~25건 | `_event`, `component`, `name` |
| `resilience/` | ~10건 | `name=name`, `_self`, `i=i` |
| 기타 | ~44건 | 다양한 패턴 |

---

## 2. 명명 규칙 (290 문서 §3 참조)

| 규칙 | 예시 |
|---|---|
| `_self=self.xxx` → `xxx` | `_self=self._region` → `region=self._region` |
| `v=v` → `→수동` | 설정 검증 컨텍스트에서 코드 확인 필요 (ex: `validated_value`) |
| `domain=domain` → `healing_domain` | selfhealing 도메인 구분자 |
| `region=region` → `target_region` | 지역 식별 |
| `name=name` → 컨텍스트 접두사 | `factory_name`, `bulkhead_name`, `recovery_name` 등 |
| `component=component` → 컨텍스트 접두사 | `watchdog_component`, `health_component` 등 |
| `count=len(items)` → `items_count` | 카운트 접두사 패턴 |
| `_event=event.xxx` → `→수동` | structlog 내부 확인 필수 |
| `key=key` → 컨텍스트 접두사 | `config_key`, `conflict_key`, `rate_limit_key` 등 |

---

## 3. 전수 변환 테이블

> **범례**
> - `↑` = 바로 위 행과 같은 파일
> - `→수동` = 자동 변환 불가, 코드 컨텍스트를 확인하여 수동 결정 필요
> - 모든 파일 경로는 `packages/selfhealing-python/src/selfhealing/` 기준 상대 경로

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 분류 |
|---|---|---|---|---|---|
| `audit/async_audit_lifecycle.py` | 114 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `audit/audit_integration.py` | 415 | `count` | `len(events)` | `events_count` | 제네릭 |
| `↑` | 599 | `value` | `type(observer).__name__` | `adapter_type` | 제네릭 |
| `↑` | 609 | `value` | `type(observer).__name__` | `adapter_type` | 제네릭 |
| `↑` | 815 | `response` | `response.status` | `→수동` | 제네릭 |
| `audit/audit_watchdog.py` | 258 | `_self` | `self._config.heartbeat_interval_seconds` | `heartbeat_interval_seconds` | 불투명 |
| `↑` | 341 | `target` | `target.name` | `→수동` | 제네릭 |
| `audit/backends/local.py` | 271 | `_self` | `self._last_anchor_date` | `last_anchor_date` | 불투명 |
| `audit/cascade_auditor/_recording.py` | 108 | `count` | `len(cascade_effects)` | `cascade_effects_count` | 제네릭 |
| `audit/cascade_auditor/_wal_recovery.py` | 141 | `_event` | `event.id` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 227 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `audit/cascade_chain.py` | 70 | `config` | `config.warn_at_depth` | `→수동` | 제네릭 |
| `↑` | 81 | `config` | `config.max_chain_depth` | `→수동` | 제네릭 |
| `↑` | 98 | `config` | `config.max_chain_depth` | `→수동` | 제네릭 |
| `audit/checkpoint_manager.py` | 159 | `_self` | `self._path` | `path` | 불투명 |
| `audit/checkpoint_strategy.py` | 1125 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `audit/continuous_audit.py` | 815 | `_self` | `self._failed_write_count` | `failed_write_count` | 불투명 |
| `↑` | 824 | `entry` | `entry.action` | `→수동` | 제네릭 |
| `audit/env_snapshot.py` | 285 | `status` | `status` | `→수동_status_컨텍스트확인` | 제네릭 |
| `audit/export.py` | 237 | `entry` | `entry.get("audit_id")` | `→수동` | 제네릭 |
| `↑` | 345 | `_self` | `self._options.s3_bucket` | `options.s3_bucket` | 불투명 |
| `↑` | 346 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 378 | `response` | `response.status` | `→수동` | 제네릭 |
| `↑` | 426 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `audit/graceful_degradation/circuit_breaker.py` | 162 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 171 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 184 | `_self` | `self._name` | `name` | 불투명 |
| `audit/graceful_degradation/degradation_manager.py` | 132 | `level` | `level.value` | `→수동` | 제네릭 |
| `↑` | 133 | `value` | `f' ({reason})' if reason else ''` | `→수동` | 제네릭 |
| `audit/graceful_degradation/manager.py` | 167 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `audit/graceful_degradation/wal_recovery.py` | 187 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `audit/hash_chain_safety.py` | 170 | `_self` | `self._monotonic_offset` | `monotonic_offset` | 불투명 |
| `↑` | 718 | `_self` | `self._date` | `date` | 불투명 |
| `↑` | 731 | `_self` | `self._date` | `date` | 불투명 |
| `↑` | 755 | `_self` | `self._date` | `date` | 불투명 |
| `↑` | 761 | `_self` | `self._date` | `date` | 불투명 |
| `audit/integrity/cold_storage.py` | 158 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 181 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 362 | `count` | `len(expiring)` | `expiring_count` | 제네릭 |
| `↑` | 474 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `audit/integrity/local_manager.py` | 59 | `_self` | `self._sequence` | `sequence` | 불투명 |
| `audit/integrity/merkle_spot_checker.py` | 184 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `audit/integrity/reconciler.py` | 117 | `result` | `result["new_sequence_start"]` | `→수동` | 제네릭 |
| `↑` | 288 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `audit/integrity/sync.py` | 133 | `result` | `result['action']` | `→수동` | 제네릭 |
| `audit/kafka_checkpoint.py` | 354 | `entry` | `entry.sequence` | `→수동` | 제네릭 |
| `↑` | 391 | `entry` | `entry.sequence` | `→수동` | 제네릭 |
| `audit/logger.py` | 325 | `actor` | `actor.get("user", "system")` | `→수동` | 제네릭 |
| `audit/performance/async_writer.py` | 95 | `_self` | `self._entries_queued` | `entries_queued` | 불투명 |
| `↑` | 122 | `_self` | `self._entries_dropped` | `entries_dropped` | 불투명 |
| `audit/performance/batch_writer.py` | 140 | `_self` | `self._entries_written` | `entries_written` | 불투명 |
| `audit/performance/sampling.py` | 132 | `n` | `n` | `→수동_n_의미확인필요` | 불투명 |
| `↑` | 133 | `value` | `sample_size/n*100` | `→수동` | 제네릭 |
| `audit/performance/watchdog.py` | 108 | `_self` | `self._cleaned_count` | `cleaned_count` | 불투명 |
| `audit/persistence/disk_buffer.py` | 236 | `_self` | `self._sequence` | `sequence` | 불투명 |
| `↑` | 538 | `key` | `key.decode()` | `→수동` | 제네릭 |
| `↑` | 583 | `key` | `key.decode()` | `→수동` | 제네릭 |
| `↑` | 786 | `entry` | `entry.key` | `→수동` | 제네릭 |
| `↑` | 819 | `entry` | `entry.key.decode()` | `→수동` | 제네릭 |
| `↑` | 922 | `key` | `key.decode()` | `→수동` | 제네릭 |
| `audit/persistence/migration.py` | 154 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `↑` | 166 | `result` | `result.drained` | `→수동` | 제네릭 |
| `↑` | 251 | `result` | `result.drained` | `→수동` | 제네릭 |
| `audit/persistence/mmap_buffer.py` | 118 | `_self` | `self._file_path` | `file_path` | 불투명 |
| `↑` | 133 | `_self` | `self._file_path` | `file_path` | 불투명 |
| `audit/reconciler.py` | 214 | `_self` | `self._config.check_interval_seconds` | `check_interval_seconds` | 불투명 |
| `↑` | 344 | `result` | `result.missing_count` | `→수동` | 제네릭 |
| `audit/resilience/circuit_breaker.py` | 162 | `_self` | `self.name` | `name` | 불투명 |
| `↑` | 173 | `_self` | `self.name` | `name` | 불투명 |
| `↑` | 182 | `_self` | `self.name` | `name` | 불투명 |
| `audit/resilient_recorder.py` | 324 | `entry` | `entry.action` | `→수동` | 제네릭 |
| `↑` | 374 | `count` | `len(batch)` | `batch_count` | 제네릭 |
| `↑` | 391 | `total` | `total` | `→수동_total_컨텍스트확인` | 제네릭 |
| `audit/retention_cleaner.py` | 87 | `_self` | `self._wal_dir` | `wal_dir` | 불투명 |
| `audit/signed_manifest.py` | 255 | `response` | `response.status` | `→수동` | 제네릭 |
| `↑` | 265 | `_self` | `self._tsa_url` | `tsa_url` | 불투명 |
| `↑` | 614 | `_self` | `self._merkle_root` | `merkle_root` | 불투명 |
| `audit/sync_worker.py` | 214 | `_self` | `self._config.sync_interval_seconds` | `sync_interval_seconds` | 불투명 |
| `↑` | 370 | `entry` | `entry.sequence` | `→수동` | 제네릭 |
| `↑` | 478 | `entry` | `entry.sequence` | `→수동` | 제네릭 |
| `↑` | 505 | `entry` | `entry.data` | `→수동` | 제네릭 |
| `↑` | 602 | `_self` | `self._last_processed_seq` | `last_processed_seq` | 불투명 |
| `↑` | 622 | `_self` | `self._last_processed_seq` | `last_processed_seq` | 불투명 |
| `config_tracker.py` | 154 | `actor` | `actor.actor_id` | `→수동` | 제네릭 |
| `context/actor_context.py` | 171 | `actor` | `actor.roles` | `→수동` | 제네릭 |
| `context/causation_context.py` | 367 | `info` | `info.cascade_id` | `→수동` | 제네릭 |
| `context/celery_context_utils.py` | 480 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 548 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `decorators/domain_tag.py` | 101 | `_self` | `self.domain` | `domain` | 불투명 |
| `↑` | 115 | `_self` | `self.domain` | `domain` | 불투명 |
| `factory.py` | 88 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 97 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 106 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 115 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 124 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 133 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 142 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 151 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 160 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 169 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 200 | `value` | `type(adapter).__name__` | `adapter_type` | 제네릭 |
| `meta/escalation.py` | 188 | `_event` | `event.component` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 201 | `_event` | `event.component` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 214 | `_event` | `event.component` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 251 | `_event` | `event.component` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 307 | `_event` | `event.title` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 397 | `_event` | `event.title` | `→수동_structlog내부확인` | 불투명 |
| `meta/fallback_escalation.py` | 163 | `entry` | `entry["component"]` | `→수동` | 제네릭 |
| `↑` | 192 | `entry` | `entry["component"]` | `→수동` | 제네릭 |
| `↑` | 193 | `count` | `len(self._memory_buffer)` | `memory_buffer_count` | 제네릭 |
| `meta/rate_limit_escalation.py` | 90 | `_self` | `self._threshold` | `threshold` | 불투명 |
| `↑` | 117 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 119 | `_self` | `self._threshold` | `threshold` | 불투명 |
| `↑` | 127 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 160 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 162 | `result` | `result.channels_sent` | `→수동` | 제네릭 |
| `↑` | 167 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 168 | `result` | `result.error_message` | `result_error` | 제네릭 |
| `↑` | 182 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 191 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `meta/recovery_adapter.py` | 242 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 255 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 369 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 594 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `meta/watchdog.py` | 172 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 178 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 290 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 318 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 333 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 352 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 661 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 702 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 707 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `metrics/prometheus.py` | 319 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 369 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 428 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 446 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 561 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `metrics/reconciler.py` | 165 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 187 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `↑` | 204 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 212 | `count` | `len(result.dlq_pending)` | `dlq_pending_count` | 제네릭 |
| `metrics/reliability_manager.py` | 237 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 295 | `state` | `state.domain` | `→수동` | 제네릭 |
| `↑` | 296 | `_self` | `self._thresholds.stabilization_duration` | `thresholds.stabilization_duration` | 불투명 |
| `↑` | 310 | `state` | `state.domain` | `→수동` | 제네릭 |
| `↑` | 465 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `metrics/safe_gauge/clamping.py` | 32 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 55 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 62 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `metrics/safe_gauge/core.py` | 161 | `_self` | `self._label_values` | `label_values` | 불투명 |
| `↑` | 177 | `value` | `old_value - amount` | `→수동` | 제네릭 |
| `↑` | 178 | `_self` | `self._label_values` | `label_values` | 불투명 |
| `↑` | 195 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 196 | `_self` | `self._label_values` | `label_values` | 불투명 |
| `↑` | 238 | `_self` | `self._label_values` | `label_values` | 불투명 |
| `↑` | 383 | `_self` | `self._eviction_count` | `eviction_count` | 불투명 |
| `metrics/safe_gauge/sync.py` | 83 | `_self` | `self.stabilization_duration` | `stabilization_duration` | 불투명 |
| `metrics/snapshot_storage.py` | 209 | `_self` | `self._storage_dir` | `storage_dir` | 불투명 |
| `↑` | 227 | `_self` | `self._snapshot.age_seconds` | `snapshot.age_seconds` | 불투명 |
| `↑` | 228 | `count` | `len(self._snapshot.values)` | `values_count` | 제네릭 |
| `↑` | 276 | `_self` | `self.file_path` | `file_path` | 불투명 |
| `↑` | 380 | `_self` | `self._snapshot.age_seconds` | `snapshot.age_seconds` | 불투명 |
| `multiregion/conflict.py` | 285 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 296 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `multiregion/failover.py` | 227 | `_self` | `self._settings.failover_cooldown_seconds` | `failover_cooldown_seconds` | 불투명 |
| `↑` | 272 | `_self` | `self._current_primary` | `current_primary` | 불투명 |
| `↑` | 366 | `_self` | `self._current_primary` | `current_primary` | 불투명 |
| `↑` | 368 | `result` | `result.details` | `→수동` | 제네릭 |
| `↑` | 462 | `value` | `'; '.join(issues)` | `→수동` | 제네릭 |
| `↑` | 550 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 551 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 603 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 646 | `_self` | `self._settings.current_region` | `current_region` | 불투명 |
| `multiregion/health_monitor.py` | 503 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 545 | `region` | `region` | `target_region` | 제네릭 |
| `multiregion/heartbeat.py` | 109 | `_self` | `self._settings.current_region` | `current_region` | 불투명 |
| `↑` | 130 | `_self` | `self._settings.current_region` | `current_region` | 불투명 |
| `↑` | 190 | `_self` | `self._settings.current_region` | `current_region` | 불투명 |
| `multiregion/quorum.py` | 202 | `_self` | `self._region` | `region` | 불투명 |
| `↑` | 213 | `_self` | `self._region` | `region` | 불투명 |
| `↑` | 288 | `_self` | `self._region` | `region` | 불투명 |
| `↑` | 413 | `_self` | `self._region` | `region` | 불투명 |
| `↑` | 419 | `_self` | `self._region` | `region` | 불투명 |
| `↑` | 444 | `_self` | `self._region` | `region` | 불투명 |
| `multiregion/replicator.py` | 303 | `_self` | `self.endpoint.region` | `region` | 불투명 |
| `↑` | 394 | `_event` | `event.key` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 480 | `target` | `target.endpoint.region` | `→수동` | 제네릭 |
| `↑` | 511 | `_event` | `event.key` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 574 | `_self` | `self._settings.replication_mode` | `replication_mode` | 불투명 |
| `↑` | 575 | `count` | `len(self._workers)` | `workers_count` | 제네릭 |
| `↑` | 621 | `region` | `region` | `target_region` | 제네릭 |
| `resilience/bulkhead/metrics.py` | 210 | `_self` | `self._interval` | `interval` | 불투명 |
| `resilience/bulkhead/registry.py` | 99 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 180 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 239 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `resilience/policies/fallback.py` | 212 | `i` | `i` | `→수동_i_의미확인필요` | 불투명 |
| `↑` | 450 | `i` | `i` | `→수동_i_의미확인필요` | 불투명 |
| `resilience/policies/hedging.py` | 195 | `_self` | `self._current_load_level` | `current_load_level` | 불투명 |
| `↑` | 593 | `_self` | `self._current_load_level` | `current_load_level` | 불투명 |
| `scaling/graceful_degradation.py` | 210 | `level` | `level.value` | `→수동` | 제네릭 |
| `scaling/hpa_exporter.py` | 116 | `state` | `state.current_rate` | `→수동` | 제네릭 |
| `↑` | 117 | `level` | `state.level.value` | `→수동` | 제네릭 |
| `scaling/rate_controller.py` | 466 | `_self` | `self._settings.get_rate_multiplier(new_level)` | `get_rate_multiplier(new_level)` | 불투명 |
| `settings/anti_flapping.py` | 136 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 141 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/api_rate_limit.py` | 133 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/audit_reconciler.py` | 100 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/audit_sync.py` | 118 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/audit_watchdog.py` | 100 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/backoff.py` | 163 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/canary.py` | 115 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/cascade_retention.py` | 147 | `_self` | `self.hot_retention_days` | `hot_retention_days` | 불투명 |
| `↑` | 153 | `_self` | `self.warm_retention_days` | `warm_retention_days` | 불투명 |
| `↑` | 167 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/chaos.py` | 207 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/chaos_blast_radius.py` | 165 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/chaos_experiment.py` | 128 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/chaos_safety_caps.py` | 135 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/circuit_breaker.py` | 140 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/cleanup.py` | 184 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/corruption_shield.py` | 154 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 165 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/critical_worker.py` | 312 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/distributed_lock.py` | 104 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 115 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/dlq.py` | 96 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/error_budget_propagation.py` | 180 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/gate_fault.py` | 68 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/graceful_degradation.py` | 110 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/hash_chain.py` | 95 | `_self` | `self.merge_swap_timeout_seconds` | `merge_swap_timeout_seconds` | 불투명 |
| `↑` | 101 | `_self` | `self.date_lock_timeout_seconds` | `date_lock_timeout_seconds` | 불투명 |
| `settings/layered_provider.py` | 166 | `value` | `list(request_overrides.keys())` | `→수동` | 제네릭 |
| `settings/namespace_emergency.py` | 90 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/notification_channel.py` | 151 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/precomputed_cache.py` | 84 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/rate_limit.py` | 129 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/recovery_circuit_breaker.py` | 113 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 125 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/recovery_coordinator.py` | 257 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/recovery_shutdown.py` | 103 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/recovery_tasks.py` | 162 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 178 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `settings/resilient_recorder.py` | 140 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/retry.py` | 113 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/ring_buffer.py` | 76 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/runbook.py` | 162 | `msg` | `"승인 대기 시간이 짧으면 검토 없이 자동 승인될 수 있음"` | `→수동` | 제네릭 |
| `↑` | 168 | `msg` | `"승인 대기 시간이 길면 장애 복구가 지연될 수 있음"` | `→수동` | 제네릭 |
| `↑` | 180 | `msg` | `"락 TTL이 짧으면 실행 중 락이 만료될 수 있음"` | `→수동` | 제네릭 |
| `settings/safe_gauge.py` | 78 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/sampling.py` | 82 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 93 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 98 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/secrets.py` | 241 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 250 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 259 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `settings/security.py` | 127 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 142 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 157 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/sla.py` | 69 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/slo.py` | 87 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/steady_state.py` | 90 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 101 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/stress_test.py` | 166 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/throttle.py` | 373 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 385 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `settings/xtest_cleanup.py` | 121 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `↑` | 133 | `v` | `v` | `→수동_v_의미확인필요` | 불투명 |
| `utils/async_logger.py` | 314 | `config` | `config.threshold_count` | `→수동` | 제네릭 |
| `↑` | 630 | `count` | `len(events)` | `events_count` | 제네릭 |
| `↑` | 656 | `value` | `attempt + 1` | `→수동` | 제네릭 |
| `↑` | 715 | `count` | `len(events)` | `events_count` | 제네릭 |
| `↑` | 806 | `count` | `len(events)` | `events_count` | 제네릭 |
| `utils/template.py` | 36 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |

---

## 4. `→수동` 항목 처리 가이드

`→수동` 표기 항목(~159건)은 코드 컨텍스트를 직접 확인하여 다음 기준으로 필드명을 결정한다:

### 4.1 `v=v` 계열 (~45건, settings/ 디렉토리)
- 거의 모든 `settings/*.py`에서 설정값 검증 로그에 `v=v`가 사용됨
- **잠정 규칙**: `v=v` → `validated_value=v` 또는 해당 설정의 필드명 사용
- 예: `settings/circuit_breaker.py`에서 `v=v` → `cb_setting_value=v`
- 예: `settings/dlq.py`에서 `v=v` → `dlq_setting_value=v`
- **대안**: 모든 settings 검증에서 통일하여 `setting_value=v`로 변환

### 4.2 `_event` 계열 (~10건)
- `meta/escalation.py` — `_event=event.component` → `escalation_component`
- `meta/escalation.py` — `_event=event.title` → `escalation_title`
- `multiregion/replicator.py` — `_event=event.key` → `replication_event_key`
- structlog가 `event` 키를 내부적으로 사용하므로 반드시 충돌 확인

### 4.3 `name` 계열 (~20건)
- `factory.py` — `name=name` → `factory_name` (팩토리 등록/해제 컨텍스트)
- `meta/recovery_adapter.py` — `name=name` → `recovery_adapter_name`
- `meta/watchdog.py` — `name=name` → `watchdog_name`
- `resilience/bulkhead/registry.py` — `name=name` → `bulkhead_name`
- `settings/secrets.py` — `name=name` → `secret_name`

### 4.4 `component` 계열 (~10건)
- `meta/watchdog.py` — `component=component` → `watchdog_component`
- `services/metrics/recorders.py` — `component=component` → `metrics_component`

### 4.5 `key` 계열 (~15건)
- `multiregion/conflict.py` — `key=key` → `conflict_key`
- `meta/rate_limit_escalation.py` — `key=key` → `escalation_key`
- `audit/integrity/cold_storage.py` — `key=key` → `storage_key`
- `context/celery_context_utils.py` — `key=key` → `context_key`

### 4.6 `entry` / `result` / `response` 계열
- `entry=entry.sequence` → `entry_sequence`
- `entry=entry.action` → `entry_action`
- `result=result.missing_count` → `missing_count`
- `response=response.status` → `response_status`

### 4.7 `n` / `i` 계열 (불투명)
- `n=n` (audit/performance/sampling.py) → `sample_size` 또는 `population_count`
- `i=i` (resilience/policies/fallback.py) → `fallback_attempt_index` 또는 `fallback_index`

### 4.8 `level` / `state` / `region` 계열
- `level=level.value` → `degradation_level`, `emergency_level` 등
- `state=state.domain` → `reliability_domain`
- `region=region` → `target_region` (일괄 적용)

---

## 5. 구현 절차

### 5.1 추천 순서

1. `metrics/` — `domain=domain` → `healing_domain` 일괄 변환 (10건)
2. `multiregion/` — `_self=self._region` → `region`, `region=region` → `target_region` 일괄 (15건)
3. `audit/` — `_self`, `count=len(...)` 패턴 일괄 (30건)
4. `settings/` — `v=v` 패턴 일괄 수동 결정 (45건)
5. `meta/` — `_event`, `component` 수동 검토
6. 나머지 모듈 순차 진행

### 5.2 단계

1. 자동 변환(~115건): 확정 패턴 일괄 변환
2. 수동 검토(~159건): `→수동` 항목을 코드에서 직접 확인하여 필드명 결정
3. 테스트 실행: `pytest tests/ -k "audit or settings or meta or metrics"` — 통과 확인
4. Loki 쿼리 검증

### 5.3 주의사항

- **이벤트 메시지(첫 번째 positional 인자)는 절대 변경하지 않는다**
- `_event` 필드 변환 시 structlog 내부 동작 확인 필수
- `settings/` 디렉토리의 `v=v`는 모든 파일에서 동일한 패턴이므로 **통일된 명명 규칙** 적용 권장
- `audit/` 디렉토리의 `entry` 관련 필드는 감사 무결성에 영향을 줄 수 있으므로 주의

---

## 6. 완료 기준

- [ ] 274건 전체 변환 완료 (자동 ~115건 + 수동 ~159건)
- [ ] `pytest tests/` 통과
- [ ] 변환 전후 필드명 매핑 기록 (이 문서의 테이블)
- [ ] Loki/Grafana 대시보드 쿼리 업데이트 확인
