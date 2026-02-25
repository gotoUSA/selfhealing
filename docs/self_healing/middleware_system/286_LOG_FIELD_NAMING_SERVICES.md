# 286. 넘버링 로그 필드 리네이밍 — `services/` (chaos 제외, 72건)

> **문서 번호**: 286
> **작성일**: 2026-02-25
> **상태**: 구현 완료 (2026-02-25)
> **대상**: `packages/selfhealing-python/src/selfhealing/services/` (chaos/ 제외)
> **관련 문서**: 284_LOG_FIELD_NAMING_OVERVIEW.md

---

## 1. 대상 범위

`services/` 하위 디렉토리 중 `chaos/`를 제외한 모든 서비스 모듈.
`result_N`, `entry_N`, `config_N`, `batch_result_N` 등 다양한 접두사가 혼재한다.

**총 72건, 31개 파일.**

---

## 2. 변환 테이블

### 2.1 services/adaptive_replay.py (5건)

기존 kwargs 확인:
- L110-116: `_self=self._config.initial_items` 존재
- L135-142: `config=config.min_items`, `old_config=old_config.min_items`, `_self=self._current_items` 존재
- L221-226: `_self=self._config.success_streak_required`, `old_items=old_items` 존재

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 113 | `self_1` | `self._config.min_items` | `min_items` | |
| 114 | `self_2` | `self._config.max_items` | `max_items` | |
| 138 | `config_2` | `config.max_items` | `new_max_items` | 기존 `config=config.min_items` 충돌 방지 |
| 139 | `old_config_3` | `old_config.max_items` | `old_max_items` | 기존 `old_config=old_config.min_items` 충돌 방지 |
| 225 | `self_2` | `self._current_items` | `current_items` | gap: `self_1` 없음 |

### 2.2 services/audit/chaos_audit.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 209 | `value_3` | `reason or 'N/A'` | `reason` | gap: `value_1/2` 없음 |

### 2.3 services/canary/bypass_audit.py (4건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 211 | `entry_1` | `entry.bypassed_by` | `bypassed_by` | |
| 212 | `entry_2` | `entry.bypass_reason[:50]` | `bypass_reason` | |
| 213 | `entry_3` | `entry.emergency_level_name` | `emergency_level_name` | |
| 214 | `entry_4` | `entry.severity` | `severity` | |

### 2.4 services/canary/feature_flag.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 324 | `config_1` | `config.percentage` | `percentage` | |

### 2.5 services/canary/mid_apply_checker.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 159 | `interlock_result_2` | `interlock_result.reason` | `reason` | gap: `_1` 없음 |

### 2.6 services/canary/pause_tracker.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 173 | `context_2` | `context.causation_chain_id` | `causation_chain_id` | gap: `_1` 없음 |

### 2.7 services/canary/service.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 748 | `count_1` | `len(candidates)` | `candidates_count` | |

### 2.8 services/cell_topology/registry.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 80 | `self_1` | `self._settings.bulkhead_isolation_enabled` | `bulkhead_isolation_enabled` | |
| 522 | `count_2` | `len(self._cells)` | `total_cells` | gap: `count_1` 없음 |

### 2.9 services/circuit_breaker/actionable_alert_urls.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 84 | `value_1` | `bool(self._admin_base_url)` | `admin_configured` | bool → 의미 반영 |
| 85 | `value_2` | `bool(self._runbook_base_url)` | `runbook_configured` | |

### 2.10 services/circuit_breaker/adaptive_threshold.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 287 | `threshold_2` | `threshold.emergency_level` | `emergency_level` | gap: `_1` 없음 |

### 2.11 services/circuit_breaker/load_shedding/manager.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 137 | `config_1` | `config.criticality` | `criticality` | |

### 2.12 services/circuit_breaker/panic_threshold.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 271 | `count_1` | `len(total_circuits)` | `total_circuits_count` | |
| 406 | `value_4` | `', '.join(open_circuits)` | `open_circuits` | gap: `value_1/2/3` 없음 |

### 2.13 services/circuit_breaker/service_config.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 131 | `config_3` | `config.shed_priority` | `shed_priority` | gap: `config_1/2` 없음 |

### 2.14 services/config/propagator.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 212 | `change_1` | `change.config_key` | `config_key` | |
| 349 | `change_1` | `change.config_key` | `config_key` | |
| 350 | `change_2` | `change.source_cluster` | `source_cluster` | |

### 2.15 services/control_api_service/service.py (8건)

기존 kwargs 확인:
- L283-290: `trigger_cb_failures=`, `request=request.service_name`, `state=state.state` 존재
- L359-365: `success_count=success_count`, `request=request.service_name`, `state=state.state` 존재
- L456-466: `request=request.action`, `response=response.status` 존재

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 289 | `state_3` | `state.failure_count` | `failure_count` | gap: `state_1/2` 없음 |
| 322 | `failure_config_2` | `failure_config['failure_type']` | `failure_type` | gap |
| 364 | `state_3` | `state.success_count` | `state_success_count` | 기존 `success_count=` 충돌 → 접두사 |
| 459 | `request_1` | `request.service_name` | `service_name` | |
| 460 | `request_2` | `request.environment` | `environment` | |
| 462 | `request_4` | `request.actor` | `actor` | gap: `request_3` 없음 |
| 463 | `response_5` | `response.risk_level` | `risk_level` | 교차 접두사 넘버링 |
| 464 | `request_6` | `request.reason` | `reason` | |

### 2.16 services/coordination/pending_recovery_approval.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 648 | `request_2` | `request.namespace` | `namespace` | gap |

### 2.17 services/coordination/recovery_coordinator/_approval.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 49 | `session_1` | `session.namespace` | `namespace` | |

### 2.18 services/coordination/recovery_coordinator/_session_persistence.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 108 | `session_1` | `session.namespace` | `namespace` | |
| 150 | `count_2` | `len(comp_result.skipped_steps)` | `skipped_steps_count` | gap: `count_1` 없음 |

### 2.19 services/coordination/redis_key_guard.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 491 | `result_1` | `result['deleted_p3']` | `deleted_p3` | |

### 2.20 services/correlation_engine/service.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 496 | `value_2` | `result['root_cause'].primary_cause.score` | `primary_cause_score` | gap |
| 741 | `self_1` | `self._settings.window_seconds` | `window_seconds` | |

### 2.21 services/dlq/entry_operations.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 68 | `entry_2` | `entry.failure_type` | `failure_type` | gap |

### 2.22 services/dlq/replay_operations.py (5건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 93 | `entry_1` | `entry.domain` | `domain` | |
| 94 | `entry_2` | `entry.failure_type` | `failure_type` | |
| 138 | `result_2` | `result.success` | `success` | gap |
| 139 | `result_3` | `result.failed` | `failed` | |
| 222 | `entry_2` | `entry.max_retries` | `max_retries` | 같은 `entry_2` 다른 의미 |

### 2.23 services/emergency_mode/manager.py (3건)

기존 kwargs 확인:
- L168-172: `level=self._state.level.name` 존재 → `level_1`과 충돌
- L355-359: `snapshot=snapshot['timestamp']` 존재
- L509-513: `level=self._state.level.name` 존재 → `level_1`과 충돌

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 171 | `level_1` | `backend_state.level.name` | `backend_level` | 기존 `level=` 충돌 → 접두사 |
| 358 | `snapshot_1` | `snapshot['action']` | `action` | |
| 512 | `level_1` | `level.name` | `requested_level` | 기존 `level=` 충돌, 다른 의미 → 의미 구분 접두사 |

### 2.24 services/error_budget/ (4건)

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|---|
| reconciliation/period_tracker.py | 111 | `period_1` | `period.duration_minutes` | `duration_minutes` | |
| weighted_audit.py | 255 | `entry_1` | `entry.raw_consumption_minutes` | `raw_consumption_minutes` | |
| weighted_audit.py | 256 | `entry_2` | `entry.weighted_consumption_minutes` | `weighted_consumption_minutes` | |
| weighted_audit.py | 257 | `entry_3` | `entry.final_multiplier` | `final_multiplier` | |

### 2.25 services/error_budget_gate/fault_detector.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 109 | `self_1` | `self._failure_threshold` | `failure_threshold` | |

### 2.26 services/execution_services/chaos_service.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 160 | `result_1` | `result.skipped` | `skipped` | |
| 161 | `result_2` | `result.blocked` | `blocked` | |
| 280 | `report_1` | `report.grade` | `grade` | |

### 2.27 services/governance/service.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 175 | `result_1` | `result.hours_remaining` | `hours_remaining` | |

### 2.28 services/learning/service.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 331 | `session_2` | `session.suggestions_generated` | `suggestions_generated` | gap |

### 2.29 services/namespace_emergency/partition_reconciliation.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 495 | `action_1` | `action.message` | `message` | |
| 523 | `self_1` | `self._partition_threshold` | `partition_threshold` | |

### 2.30 services/postmortem/ (5건)

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|---|
| deep_links.py | 144 | `value_1` | `bool(self._dashboard_base_url)` | `dashboard_configured` | |
| deep_links.py | 145 | `value_2` | `bool(self._prometheus_base_url)` | `prometheus_configured` | |
| deployment_correlator.py | 240 | `count_2` | `len(config_changes)` | `config_changes_count` | gap |
| incident_group.py | 674 | `group_2` | `group.get_cascading_pattern()` | `cascading_pattern` | gap |
| notifier.py | 332 | `self_1` | `self._config.channels` | `channels` | |
| revision.py | 919 | `result_1` | `result['migrated']` | `migrated` | |
| revision.py | 920 | `result_2` | `result['skipped']` | `skipped` | |
| revision.py | 921 | `result_3` | `result['failed']` | `failed` | |
| snapshot_builder.py | 313 | `value_2` | `bool(self._snapshot.peak_metrics)` | `has_peak_metrics` | gap |

### 2.31 services/predictive_forecaster/time_series.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 273 | `self_2` | `self._level` | `level` | gap: `self_1` 없음 |
| 332 | `self_2` | `self._level` | `level` | gap: `self_1` 없음 |

### 2.32 services/replay_service/service.py (5건)

기존 kwargs 확인:
- L365-370: `batch_result=batch_result.total` 존재
- L744-752: `service_name=`, `batch_result=batch_result.total` 존재

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 368 | `batch_result_1` | `batch_result.success_count` | `success_count` | |
| 369 | `batch_result_2` | `batch_result.failed_count` | `failed_count` | |
| 748 | `batch_result_2` | `batch_result.success_count` | `success_count` | 같은 `_2` 다른 의미! |
| 749 | `batch_result_3` | `batch_result.failed_count` | `failed_count` | |
| 750 | `batch_result_4` | `batch_result.failed_count if escalate_failures else 0` | `escalated_failures` | |

### 2.33 services/retry_handler/handler.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 444 | `gate_result_1` | `gate_result.threshold_percent` | `threshold_percent` | |

### 2.34 services/security/hooks.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 59 | `getattr_1` | `getattr(hook, '__qualname__', repr(hook))` | `hook_qualname` | |

### 2.35 services/system_metrics_cache.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 93 | `self_1` | `self._sample_interval` | `sample_interval` | |

### 2.36 services/throttle/ (9건)

기존 kwargs 확인:
- L829-835 (`adaptive/__init__.py`): `rtt_ms=rtt_ms`, `_self=self.config.sla_critical_ms`, `new_limit=new_limit` 존재
- L898-904: `rtt_ms=rtt_ms`, `_self=self.config.sla_warning_ms`, `new_limit=new_limit` 존재
- L1297-1303: `old_config=old_config.sla_warning_ms`, `new_config=new_config.sla_warning_ms` 존재

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|---|
| adaptive/\_\_init\_\_.py | 833 | `self_2` | `self._current_limit` | `current_limit` | gap |
| adaptive/\_\_init\_\_.py | 902 | `self_2` | `self._current_limit` | `current_limit` | gap |
| adaptive/\_\_init\_\_.py | 1301 | `old_config_2` | `old_config.sla_critical_ms` | `old_sla_critical_ms` | 기존 `old_config=` 충돌 방지 |
| adaptive/\_\_init\_\_.py | 1302 | `new_config_3` | `new_config.sla_critical_ms` | `new_sla_critical_ms` | 교차 접두사, 기존 `new_config=` 충돌 방지 |
| adaptive/\_error_budget.py | 307 | `forecast_1` | `forecast.estimated_depletion_hours` | `estimated_depletion_hours` | |
| adaptive/\_error_budget.py | 308 | `forecast_2` | `forecast.burn_rate_1h` | `burn_rate_1h` | |
| adaptive_dlq_replay.py | 367 | `entry_1` | `entry.retry_count` | `retry_count` | |
| adaptive_dlq_replay.py | 368 | `entry_2` | `entry.max_retries` | `max_retries` | |
| dlq_integration.py | 213 | `result_1` | `result.success` | `success` | |
| dlq_integration.py | 214 | `result_2` | `result.failed` | `failed` | |
| registry.py | 118 | `config_1` | `config.initial_limit` | `initial_limit` | |
| registry.py | 119 | `config_2` | `config.min_limit` | `min_limit` | |
| registry.py | 120 | `config_3` | `config.max_limit` | `max_limit` | |
| throttle_sla_alert_urls.py | 60 | `value_1` | `bool(self._admin_base_url)` | `admin_configured` | |
| throttle_sla_alert_urls.py | 61 | `value_2` | `bool(self._runbook_base_url)` | `runbook_configured` | |

### 2.37 services/unified_notification/service.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 102 | `payload_2` | `payload.message` | `message` | gap |

### 2.38 services/xtest_session_manager.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 349 | `count_1` | `len(active_ids)` | `active_count` | |

---

## 3. 주의 사항

### 3.1 `control_api_service/service.py` — 가장 복잡한 충돌 케이스

L456-464의 `request_N`/`response_N` 교차 넘버링은 **기존 `request=request.action`과 `response=response.status`를 유지**하면서 나머지를 속성명으로 변환한다. 이 파일은 3곳의 서로 다른 로거 호출에서 `state_3`가 각각 다른 의미로 사용되므로 특히 주의가 필요하다.

### 3.2 `replay_service/service.py` — `batch_result_2` 의미 반전

L369의 `batch_result_2`는 `failed_count`이고, L748의 `batch_result_2`는 `success_count`이다. 같은 필드명이 같은 파일 내에서 **의미가 반대**이므로, 의미 기반 리네이밍이 특히 중요한 파일이다.
