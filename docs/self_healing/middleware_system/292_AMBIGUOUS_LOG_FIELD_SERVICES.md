# 292. 모호한 로그 필드 리네이밍 — services/ (chaos 제외)

> **문서 번호**: 292
> **작성일**: 2026-02-25
> **상태**: 구현 대기
> **대상**: `packages/selfhealing-python/src/selfhealing/services/` (chaos/ 하위 디렉토리 제외)
> **선행 문서**: 290_AMBIGUOUS_LOG_FIELD_OVERVIEW.md, 291_AMBIGUOUS_LOG_FIELD_CHAOS.md
> **구현 우선순위**: ★☆☆☆☆ (5순위 — 최대 규모, 수동 검토 비율 높음)

---

## 1. 범위 요약

| 지표 | 값 |
|---|---|
| 대상 파일 수 | **159개** |
| 모호한 kwargs 인스턴스 | **478건** |
| 불투명 필드(`_self`, `_event` 등) | ~95건 |
| 제네릭 필드(`count`, `key`, `value`, `session`, `domain` 등) | ~383건 |
| 자동 변환 제안 가능 | ~195건 (41%) |
| 수동 컨텍스트 검토 필요(`→수동`) | ~283건 (59%) |

### 1.1 이 문서가 5순위인 이유

1. **478건으로 전체의 36%** — 가장 큰 규모이므로 마지막에 진행
2. **수동 검토 비율 59%** — `session=session.id`, `rollout=rollout.state` 등 컨텍스트 의존적 패턴 다수
3. **하위 디렉토리 다양** — `services/canary/`, `services/circuit_breaker/`, `services/coordination/`, `services/throttle/` 등 30+ 하위 모듈

### 1.2 하위 디렉토리별 분포

| 하위 디렉토리 | 건수 | 주요 패턴 |
|---|---|---|
| `services/coordination/` | ~70건 | `session=session.id`, `type=action.type.value` |
| `services/throttle/` | ~55건 | `_self=self._current_limit`, `level=level` |
| `services/circuit_breaker/` | ~50건 | `state=state.service_name`, `_self=self.config.*` |
| `services/canary/` | ~40건 | `rollout=rollout.*`, `config=config.*` |
| `services/error_budget/` | ~35건 | `domain=domain`, `level=level.name` |
| `services/emergency_mode/` | ~25건 | `level=level.name`, `action=action` |
| `services/audit/` | ~25건 | `action=action.upper()`, `domain=domain` |
| 기타 | ~178건 | 다양한 패턴 |

---

## 2. 명명 규칙 (290 문서 §3 참조)

| 규칙 | 예시 |
|---|---|
| `_self=self.xxx` → `xxx` | `_self=self._current_limit` → `current_limit=self._current_limit` |
| `session=session.id` → `session_id` | 세션 식별자 패턴 통일 |
| `domain=domain` → `healing_domain` | selfhealing 도메인 구분자 |
| `actor=actor` → `actor_id` | 행위자 식별 |
| `region=region` → `target_region` | 지역 식별 |
| `count=len(items)` → `items_count` | 카운트 접두사 패턴 |
| `message=message` → `detail_message` | structlog event와 구분 |
| `value=type(x).__name__` → `adapter_type` | 타입 이름 반환 시 통일 |
| `→수동` 표기 항목 | 코드 컨텍스트를 직접 확인하여 결정 |

---

## 3. 전수 변환 테이블

> **범례**
> - `↑` = 바로 위 행과 같은 파일
> - `→수동` = 자동 변환 불가, 코드 컨텍스트를 확인하여 수동 결정 필요
> - 모든 파일 경로는 `packages/selfhealing-python/src/selfhealing/` 기준 상대 경로

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 분류 |
|---|---|---|---|---|---|
| `services/adaptive_replay.py` | 112 | `_self` | `self._config.initial_items` | `initial_items` | 불투명 |
| `↑` | 136 | `config` | `config.min_items` | `→수동` | 제네릭 |
| `↑` | 140 | `_self` | `self._current_items` | `current_items` | 불투명 |
| `↑` | 209 | `_self` | `self._current_items` | `current_items` | 불투명 |
| `↑` | 223 | `_self` | `self._config.success_streak_required` | `success_streak_required` | 불투명 |
| `↑` | 230 | `_self` | `self._success_streak` | `success_streak` | 불투명 |
| `↑` | 238 | `_self` | `self._current_items` | `current_items` | 불투명 |
| `↑` | 285 | `_self` | `self._current_items` | `current_items` | 불투명 |
| `services/audit/cb_audit.py` | 88 | `value` | `reason or 'auto'` | `→수동` | 제네릭 |
| `↑` | 149 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 437 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `services/audit/chaos_audit.py` | 80 | `action` | `action.upper()` | `→수동` | 제네릭 |
| `↑` | 83 | `value` | `reason or "N/A"` | `→수동` | 제네릭 |
| `↑` | 204 | `action` | `action.upper()` | `→수동` | 제네릭 |
| `↑` | 206 | `value` | `emergency_level or "N/A"` | `→수동` | 제네릭 |
| `↑` | 375 | `action` | `action.upper()` | `→수동` | 제네릭 |
| `↑` | 376 | `level` | `level` | `→수동_level_컨텍스트확인` | 제네릭 |
| `↑` | 378 | `value` | `reason or "N/A"` | `→수동` | 제네릭 |
| `↑` | 512 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `services/audit/compliance_audit.py` | 233 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 241 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 328 | `message` | `message` | `detail_message` | 제네릭 |
| `↑` | 403 | `action` | `action.upper()` | `→수동` | 제네릭 |
| `services/audit/dlq_audit.py` | 92 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 185 | `status` | `status` | `→수동_status_컨텍스트확인` | 제네릭 |
| `↑` | 187 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 188 | `value` | `error_message or 'none'` | `→수동` | 제네릭 |
| `services/audit/mttr_calculator.py` | 400 | `ts` | `ts` | `→수동_ts_의미확인필요` | 불투명 |
| `services/audit/retry_audit.py` | 111 | `status` | `status` | `→수동_status_컨텍스트확인` | 제네릭 |
| `↑` | 112 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 115 | `value` | `error_type or 'none'` | `→수동` | 제네릭 |
| `↑` | 181 | `action` | `action.upper()` | `→수동` | 제네릭 |
| `↑` | 182 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 183 | `value` | `reason or 'N/A'` | `→수동` | 제네릭 |
| `↑` | 271 | `state` | `state.upper()` | `→수동` | 제네릭 |
| `↑` | 275 | `value` | `duration_seconds or 0` | `→수동` | 제네릭 |
| `services/audit/xtest_audit.py` | 78 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 79 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 81 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `↑` | 149 | `status` | `status` | `→수동_status_컨텍스트확인` | 제네릭 |
| `services/auto_tuning/adjustment_recorder.py` | 115 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 236 | `session` | `session.session_id if session else 'unknown'` | `→수동` | 제네릭 |
| `↑` | 296 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 362 | `count` | `len(self._records)` | `records_count` | 제네릭 |
| `services/auto_tuning/chaos_aware_metrics.py` | 73 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `services/auto_tuning/service.py` | 732 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 733 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 942 | `message` | `message` | `detail_message` | 제네릭 |
| `↑` | 1015 | `message` | `message` | `detail_message` | 제네릭 |
| `↑` | 1029 | `message` | `message` | `detail_message` | 제네릭 |
| `services/backoff_calculator/calculator.py` | 454 | `value` | `base_delay * multiplier` | `→수동` | 제네릭 |
| `services/blast_radius/service.py` | 95 | `level` | `level.value` | `→수동` | 제네릭 |
| `↑` | 227 | `level` | `level.value` | `→수동` | 제네릭 |
| `↑` | 228 | `count` | `len(all_affected)` | `all_affected_count` | 제네릭 |
| `↑` | 395 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `↑` | 414 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `↑` | 436 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `services/canary/audit.py` | 161 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 163 | `value` | `type(error).__name__` | `adapter_type` | 제네릭 |
| `services/canary/bypass_audit.py` | 210 | `entry` | `entry.audit_id` | `→수동` | 제네릭 |
| `services/canary/chaos_guard.py` | 92 | `result` | `result.chaos_clusters` | `→수동` | 제네릭 |
| `↑` | 344 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `services/canary/cross_cluster.py` | 326 | `message` | `message[:100]` | `→수동` | 제네릭 |
| `↑` | 682 | `request` | `request.request_id` | `→수동` | 제네릭 |
| `↑` | 1003 | `count` | `len(self.clusters)` | `clusters_count` | 제네릭 |
| `services/canary/feature_flag.py` | 323 | `config` | `config.config_type` | `→수동` | 제네릭 |
| `services/canary/locking.py` | 352 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `services/canary/mid_apply_checker.py` | 157 | `i` | `i` | `→수동_i_의미확인필요` | 불투명 |
| `services/canary/service.py` | 265 | `rollout` | `rollout.id` | `→수동` | 제네릭 |
| `↑` | 267 | `count` | `len(stages)` | `stages_count` | 제네릭 |
| `↑` | 298 | `rollout` | `rollout.state` | `→수동` | 제네릭 |
| `↑` | 372 | `rollout` | `rollout.state` | `→수동` | 제네릭 |
| `↑` | 475 | `rollout` | `rollout.current_stage_index` | `→수동` | 제네릭 |
| `↑` | 504 | `rollout` | `rollout.state` | `→수동` | 제네릭 |
| `↑` | 673 | `rollout` | `rollout.id` | `→수동` | 제네릭 |
| `↑` | 681 | `rollout` | `rollout.id` | `→수동` | 제네릭 |
| `↑` | 740 | `value` | `i // max_batch_size + 1` | `→수동` | 제네릭 |
| `↑` | 747 | `count` | `len(resumed)` | `resumed_count` | 제네릭 |
| `↑` | 771 | `rollout` | `rollout.state` | `→수동` | 제네릭 |
| `services/canary/state_refresher.py` | 208 | `_self` | `self._consecutive_failures` | `consecutive_failures` | 불투명 |
| `↑` | 240 | `rollout` | `rollout.id` | `→수동` | 제네릭 |
| `↑` | 245 | `rollout` | `rollout.id` | `→수동` | 제네릭 |
| `services/canary/versioning.py` | 28 | `e` | `e.conflicting_operator` | `→수동_e_의미확인필요` | 불투명 |
| `services/cell_topology/registry.py` | 79 | `_self` | `self._settings.cell_count` | `cell_count` | 불투명 |
| `↑` | 298 | `count` | `len(evicted)` | `evicted_count` | 제네릭 |
| `↑` | 333 | `count` | `len(self._cells)` | `cells_count` | 제네릭 |
| `↑` | 334 | `_self` | `self._settings.bulkhead_max_concurrent_per_cell` | `bulkhead_max_concurrent_per_cell` | 불투명 |
| `↑` | 520 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `services/circuit_breaker/actionable_alert_urls.py` | 83 | `value` | `bool(self._dashboard_base_url)` | `→수동` | 제네릭 |
| `services/circuit_breaker/adaptive_threshold.py` | 187 | `level` | `level` | `→수동_level_컨텍스트확인` | 제네릭 |
| `services/circuit_breaker/blast_radius_integration.py` | 439 | `level` | `level.value` | `→수동` | 제네릭 |
| `↑` | 441 | `count` | `len(critical_affected)` | `critical_affected_count` | 제네릭 |
| `services/circuit_breaker/hooks.py` | 63 | `value` | `type(error).__name__` | `adapter_type` | 제네릭 |
| `services/circuit_breaker/load_shedding/manager.py` | 136 | `config` | `config.service_id` | `→수동` | 제네릭 |
| `↑` | 371 | `count` | `len(affected_services)` | `affected_services_count` | 제네릭 |
| `services/circuit_breaker/manual_control.py` | 303 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 314 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 565 | `state` | `state.service_name` | `→수동` | 제네릭 |
| `services/circuit_breaker/panic_threshold.py` | 270 | `count` | `len(open_circuits)` | `open_circuits_count` | 제네릭 |
| `↑` | 363 | `count` | `len(open_circuits)` | `open_circuits_count` | 제네릭 |
| `↑` | 405 | `value` | `"` | `→수동` | 제네릭 |
| `services/circuit_breaker/policy.py` | 132 | `value` | `type(hook).__name__` | `adapter_type` | 제네릭 |
| `services/circuit_breaker/protection.py` | 73 | `_self` | `self.config.rate_limit_cascade_window_seconds` | `rate_limit_cascade_window_seconds` | 불투명 |
| `↑` | 149 | `_self` | `self.config.self_ddos_window_seconds` | `self_ddos_window_seconds` | 불투명 |
| `services/circuit_breaker/service.py` | 121 | `state` | `state` | `→수동_state_컨텍스트확인` | 제네릭 |
| `↑` | 129 | `state` | `state` | `→수동_state_컨텍스트확인` | 제네릭 |
| `↑` | 542 | `_self` | `self.get_total_calls(service_name)` | `get_total_calls(service_name)` | 불투명 |
| `↑` | 596 | `state` | `state.service_name` | `→수동` | 제네릭 |
| `↑` | 608 | `state` | `state.service_name` | `→수동` | 제네릭 |
| `↑` | 610 | `_self` | `self.config.minimum_calls` | `minimum_calls` | 불투명 |
| `↑` | 620 | `state` | `state.service_name` | `→수동` | 제네릭 |
| `↑` | 622 | `_self` | `self.config.failure_rate_threshold` | `failure_rate_threshold` | 불투명 |
| `↑` | 855 | `_self` | `self.config.success_threshold` | `success_threshold` | 불투명 |
| `↑` | 937 | `state` | `state.service_name` | `→수동` | 제네릭 |
| `↑` | 1079 | `state` | `state.service_name` | `→수동` | 제네릭 |
| `services/circuit_breaker/service_config.py` | 128 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 130 | `config` | `config.criticality` | `→수동` | 제네릭 |
| `↑` | 187 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 441 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `services/circuit_breaker/tracing.py` | 517 | `info` | `info.trace_id` | `→수동` | 제네릭 |
| `services/cleanup_service.py` | 98 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 160 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 222 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 284 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 299 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `services/compliance/service.py` | 369 | `value` | `[s.value for s in standards]` | `→수동` | 제네릭 |
| `↑` | 504 | `count` | `len(violations)` | `violations_count` | 제네릭 |
| `services/config/propagation_health.py` | 177 | `_self` | `self.TIER1_SLA_THRESHOLD_MS` | `TIER1_SLA_THRESHOLD_MS` | 불투명 |
| `↑` | 188 | `_self` | `self.TIER2_SLA_THRESHOLD_MS` | `TIER2_SLA_THRESHOLD_MS` | 불투명 |
| `services/control_api_service/service.py` | 287 | `request` | `request.service_name` | `request_service_name` | 제네릭 |
| `↑` | 288 | `state` | `state.state` | `→수동` | 제네릭 |
| `↑` | 320 | `request` | `request.service_name` | `request_service_name` | 제네릭 |
| `↑` | 362 | `request` | `request.service_name` | `request_service_name` | 제네릭 |
| `↑` | 363 | `state` | `state.state` | `→수동` | 제네릭 |
| `↑` | 458 | `request` | `request.action` | `request_action` | 제네릭 |
| `↑` | 461 | `response` | `response.status` | `→수동` | 제네릭 |
| `↑` | 462 | `actor` | `request.actor` | `→수동` | 제네릭 |
| `services/coordination/anti_flapping.py` | 161 | `count` | `len(recent_transitions)` | `recent_transitions_count` | 제네릭 |
| `↑` | 162 | `_self` | `self.flapping_lockout_minutes` | `flapping_lockout_minutes` | 불투명 |
| `↑` | 252 | `count` | `len(self._transition_history)` | `transition_history_count` | 제네릭 |
| `↑` | 265 | `_self` | `self._last_recovery_at.isoformat()` | `last_recovery_at.isoformat()` | 불투명 |
| `↑` | 334 | `_self` | `self.recovery_hysteresis_factor` | `recovery_hysteresis_factor` | 불투명 |
| `services/coordination/atomic_transition.py` | 241 | `message` | `message` | `detail_message` | 제네릭 |
| `↑` | 242 | `level` | `level` | `→수동_level_컨텍스트확인` | 제네릭 |
| `services/coordination/coordinator.py` | 89 | `type` | `action.type.value` | `→수동` | 제네릭 |
| `↑` | 91 | `action` | `action.params` | `→수동` | 제네릭 |
| `↑` | 239 | `type` | `action.type.value` | `→수동` | 제네릭 |
| `↑` | 406 | `count` | `len(effects)` | `effects_count` | 제네릭 |
| `↑` | 487 | `type` | `action.type.value` | `→수동` | 제네릭 |
| `↑` | 503 | `type` | `action.type.value` | `→수동` | 제네릭 |
| `↑` | 530 | `type` | `action.type.value` | `→수동` | 제네릭 |
| `↑` | 532 | `action` | `action.params` | `→수동` | 제네릭 |
| `services/coordination/crisis_multiplier.py` | 186 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `services/coordination/idempotent_step_handlers.py` | 297 | `status` | `record.status.value` | `→수동` | 제네릭 |
| `services/coordination/optimistic_action.py` | 232 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 260 | `result` | `result.action_id` | `→수동` | 제네릭 |
| `services/coordination/pending_recovery_approval.py` | 287 | `request` | `request.request_id` | `→수동` | 제네릭 |
| `↑` | 327 | `status` | `request.status.value` | `→수동` | 제네릭 |
| `↑` | 533 | `request` | `request.request_id` | `→수동` | 제네릭 |
| `↑` | 578 | `count` | `len(to_remove)` | `to_remove_count` | 제네릭 |
| `↑` | 647 | `request` | `request.request_id` | `→수동` | 제네릭 |
| `services/coordination/policy_engine.py` | 288 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 300 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 312 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `services/coordination/recovery_coordinator/__init__.py` | 368 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 394 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 404 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 550 | `count` | `len(steps)` | `steps_count` | 제네릭 |
| `↑` | 633 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 653 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 677 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 692 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 752 | `value` | `resume_count + 1` | `→수동` | 제네릭 |
| `↑` | 825 | `session` | `session.id` | `→수동` | 제네릭 |
| `services/coordination/recovery_coordinator/_approval.py` | 48 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 106 | `session` | `session.status` | `→수동` | 제네릭 |
| `↑` | 141 | `session` | `session.id` | `→수동` | 제네릭 |
| `services/coordination/recovery_coordinator/_audit_recording.py` | 261 | `session` | `session.id` | `→수동` | 제네릭 |
| `services/coordination/recovery_coordinator/_session_persistence.py` | 107 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 148 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 149 | `count` | `len(comp_result.failed_steps)` | `failed_steps_count` | 제네릭 |
| `↑` | 166 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 317 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 318 | `count` | `len(compensation_failures or [])` | `compensation_failures or []_count` | 제네릭 |
| `↑` | 325 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 405 | `session` | `session.id` | `→수동` | 제네릭 |
| `services/coordination/recovery_coordinator/_step_handler.py` | 303 | `session` | `session.id` | `→수동` | 제네릭 |
| `services/coordination/recovery_metrics.py` | 264 | `status` | `status` | `→수동_status_컨텍스트확인` | 제네릭 |
| `services/coordination/recovery_session_archive.py` | 368 | `session` | `session.id` | `→수동` | 제네릭 |
| `↑` | 369 | `status` | `session.status.value` | `→수동` | 제네릭 |
| `services/coordination/recovery_tasks.py` | 233 | `session` | `session.session_id` | `→수동` | 제네릭 |
| `↑` | 506 | `result` | `result['reason']` | `→수동` | 제네릭 |
| `services/coordination/redis_key_guard.py` | 452 | `info` | `info.used_percent` | `→수동` | 제네릭 |
| `↑` | 459 | `info` | `info.used_percent` | `→수동` | 제네릭 |
| `↑` | 478 | `result` | `result["deleted_p4"]` | `→수동` | 제네릭 |
| `↑` | 511 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 528 | `key` | `key` | `redis_key` | 제네릭 |
| `services/coordination/regional_recovery_policy.py` | 401 | `config` | `config.namespace` | `→수동` | 제네릭 |
| `services/correlation_engine/co_occurrence_tracker.py` | 326 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 545 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `services/correlation_engine/incident_timeline.py` | 802 | `status` | `%s"` | `→수동` | 제네릭 |
| `services/correlation_engine/service.py` | 298 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 494 | `result` | `result["incident_id"]` | `→수동` | 제네릭 |
| `↑` | 495 | `value` | `result["root_cause"].primary_cause.event_node.servic...` | `→수동` | 제네릭 |
| `↑` | 740 | `_self` | `self._settings.zscore_threshold` | `zscore_threshold` | 불투명 |
| `↑` | 986 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 993 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `services/corruption_shield/shield.py` | 294 | `count` | `len(result.violations)` | `violations_count` | 제네릭 |
| `↑` | 295 | `result` | `result.blocked` | `→수동` | 제네릭 |
| `services/daily_report/service.py` | 130 | `count` | `len(report.entries)` | `entries_count` | 제네릭 |
| `services/dashboard_service/service.py` | 140 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 160 | `key` | `key` | `cache_key` | 제네릭 |
| `services/dlq/entry_operations.py` | 66 | `pk` | `pk` | `record_pk` | 불투명 |
| `↑` | 67 | `entry` | `entry.domain` | `→수동` | 제네릭 |
| `↑` | 126 | `pk` | `pk` | `record_pk` | 불투명 |
| `services/dlq/replay_operations.py` | 44 | `entry` | `entry.id` | `→수동` | 제네릭 |
| `↑` | 92 | `entry` | `entry.id` | `→수동` | 제네릭 |
| `↑` | 93 | `domain` | `entry.domain` | `→수동` | 제네릭 |
| `↑` | 121 | `entry` | `entry.id` | `→수동` | 제네릭 |
| `↑` | 136 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 137 | `result` | `result.processed` | `→수동` | 제네릭 |
| `↑` | 192 | `entry` | `entry.expires_at` | `→수동` | 제네릭 |
| `↑` | 221 | `entry` | `entry.retry_count` | `→수동` | 제네릭 |
| `services/dlq/store_operations.py` | 104 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `services/emergency_mode/manager.py` | 170 | `level` | `self._state.level.name` | `→수동` | 제네릭 |
| `↑` | 182 | `_self` | `self._state.is_active` | `state.is_active` | 불투명 |
| `↑` | 208 | `level` | `self._state.level.name` | `→수동` | 제네릭 |
| `↑` | 209 | `_self` | `self._state.is_active` | `state.is_active` | 불투명 |
| `↑` | 306 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 356 | `action` | `snapshot["action"]` | `→수동` | 제네릭 |
| `↑` | 466 | `level` | `level.name` | `→수동` | 제네릭 |
| `↑` | 468 | `_self` | `self._state.expires_at or "manual"` | `state.expires_at or "manual"` | 불투명 |
| `↑` | 469 | `value` | `"` | `→수동` | 제네릭 |
| `↑` | 505 | `level` | `self._state.level.name` | `→수동` | 제네릭 |
| `↑` | 537 | `level` | `level.name` | `→수동` | 제네릭 |
| `↑` | 669 | `level` | `self._state.level.name` | `→수동` | 제네릭 |
| `↑` | 698 | `value` | `reason or "Manual stop"` | `→수동` | 제네릭 |
| `↑` | 728 | `config` | `config.stabilization_period_seconds` | `→수동` | 제네릭 |
| `↑` | 750 | `config` | `config.health_check_interval_seconds` | `→수동` | 제네릭 |
| `↑` | 921 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `services/error_budget/atomic_consumer.py` | 224 | `value` | `attempt + 1` | `→수동` | 제네릭 |
| `services/error_budget/enums.py` | 156 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `services/error_budget/escalation_invalidation.py` | 87 | `count` | `len(self._invalidation_targets)` | `invalidation_targets_count` | 제네릭 |
| `↑` | 216 | `count` | `len(self._invalidation_targets)` | `invalidation_targets_count` | 제네릭 |
| `services/error_budget/exception_weights.py` | 368 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `services/error_budget/multiplier.py` | 322 | `level` | `level.name` | `→수동` | 제네릭 |
| `↑` | 360 | `level` | `level.name` | `→수동` | 제네릭 |
| `services/error_budget/precedence.py` | 187 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `services/error_budget/propagation.py` | 449 | `_self` | `self.config.max_hops` | `max_hops` | 불투명 |
| `↑` | 473 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `services/error_budget/provider.py` | 215 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 296 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `services/error_budget/reconciliation/shadow_calculator.py` | 283 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `services/error_budget/smoother.py` | 140 | `_self` | `self._current_value` | `current_value` | 불투명 |
| `↑` | 256 | `_self` | `self._target_value` | `target_value` | 불투명 |
| `services/error_budget/weighted_audit.py` | 254 | `entry` | `entry.audit_id` | `→수동` | 제네릭 |
| `↑` | 274 | `entry` | `entry.audit_id` | `→수동` | 제네릭 |
| `services/error_budget_gate/__init__.py` | 26 | `result` | `result.reason` | `→수동` | 제네릭 |
| `services/error_budget_gate/alert_manager.py` | 162 | `message` | `message` | `detail_message` | 제네릭 |
| `services/error_budget_gate/fault_detector.py` | 108 | `_self` | `self._failure_count` | `failure_count` | 불투명 |
| `services/error_budget_gate/gate.py` | 154 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 206 | `key` | `key` | `config_key` | 제네릭 |
| `↑` | 207 | `value` | `value` | `config_value` | 제네릭 |
| `↑` | 301 | `_self` | `self._config.critical_threshold_percent` | `critical_threshold_percent` | 불투명 |
| `↑` | 307 | `_self` | `self._config.warning_threshold_percent` | `warning_threshold_percent` | 불투명 |
| `↑` | 577 | `value` | `action or 'unknown'` | `→수동` | 제네릭 |
| `↑` | 578 | `result` | `result.error_budget_percent` | `result_error` | 제네릭 |
| `services/error_budget_gate/region_tier_resolver.py` | 60 | `region` | `region` | `target_region` | 제네릭 |
| `services/event_bus/bus/__init__.py` | 448 | `count` | `len(subscriptions)` | `subscriptions_count` | 제네릭 |
| `↑` | 533 | `_event` | `event.source` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 711 | `level` | `level` | `→수동_level_컨텍스트확인` | 제네릭 |
| `services/event_bus/bus/_cb_handlers.py` | 178 | `result` | `result.suppression_reason` | `suppression_reason` | 제네릭 |
| `↑` | 211 | `_event` | `event.data.get('integrity_gate_result', {})` | `→수동_structlog내부확인` | 불투명 |
| `services/event_bus/bus/_throttle_handlers.py` | 48 | `level` | `level` | `→수동_level_컨텍스트확인` | 제네릭 |
| `services/event_bus/integrity_gate.py` | 77 | `result` | `result.get('errors', [])` | `errors` | 제네릭 |
| `↑` | 85 | `result` | `result.get('checked', 0)` | `checked` | 제네릭 |
| `services/event_bus/redis_bus.py` | 238 | `value` | `list(self._subscribed_redis_channels)` | `→수동` | 제네릭 |
| `services/execution_services/chaos_service.py` | 135 | `count` | `len(due_experiments)` | `due_experiments_count` | 제네릭 |
| `↑` | 159 | `result` | `result.executed` | `→수동` | 제네릭 |
| `↑` | 279 | `report` | `report.report_id` | `→수동` | 제네릭 |
| `↑` | 327 | `total` | `total` | `→수동_total_컨텍스트확인` | 제네릭 |
| `services/execution_services/config_apply_service.py` | 123 | `result` | `result.get('error')` | `error` | 제네릭 |
| `services/factory/registry.py` | 92 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 144 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 486 | `value` | `type(mock_instance).__name__` | `adapter_type` | 제네릭 |
| `services/finops/service.py` | 204 | `message` | `message` | `detail_message` | 제네릭 |
| `↑` | 445 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `services/governance/api_service.py` | 239 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 240 | `result` | `result.get('expiry_hours', 8)` | `expiry_hours` | 제네릭 |
| `↑` | 258 | `actor` | `actor` | `actor_id` | 제네릭 |
| `services/governance/checks.py` | 768 | `result` | `result.block_reason.value if result.block_reason els...` | `→수동` | 제네릭 |
| `services/governance/emergency.py` | 471 | `message` | `message[:100]` | `→수동` | 제네릭 |
| `services/governance/service.py` | 174 | `result` | `result.hours_elapsed` | `→수동` | 제네릭 |
| `↑` | 217 | `result` | `result.hours_elapsed` | `→수동` | 제네릭 |
| `↑` | 271 | `level` | `level` | `→수동_level_컨텍스트확인` | 제네릭 |
| `↑` | 273 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 326 | `actor` | `actor` | `actor_id` | 제네릭 |
| `services/healing_events_store.py` | 138 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `services/health_check.py` | 333 | `region` | `region` | `target_region` | 제네릭 |
| `services/idempotency/service.py` | 180 | `key` | `key.key` | `→수동` | 제네릭 |
| `↑` | 206 | `key` | `key.key` | `→수동` | 제네릭 |
| `↑` | 310 | `key` | `key.cache_key` | `→수동` | 제네릭 |
| `↑` | 338 | `key` | `key.cache_key` | `→수동` | 제네릭 |
| `services/isolation/regional_gate.py` | 201 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 222 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 345 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 368 | `region` | `region` | `target_region` | 제네릭 |
| `services/learning/service.py` | 129 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 193 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 221 | `count` | `len(self._blacklist)` | `blacklist_count` | 제네릭 |
| `↑` | 245 | `count` | `len(serialized)` | `serialized_count` | 제네릭 |
| `↑` | 330 | `session` | `session.patterns_learned` | `→수동` | 제네릭 |
| `↑` | 393 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `services/metric_sync_service.py` | 201 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 211 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 379 | `actor` | `actor` | `actor_id` | 제네릭 |
| `services/metrics/recorders.py` | 76 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 98 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 134 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 171 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 218 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `↑` | 242 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `↑` | 321 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 381 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 409 | `status` | `status` | `→수동_status_컨텍스트확인` | 제네릭 |
| `↑` | 479 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 499 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 542 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 583 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 653 | `region` | `region` | `target_region` | 제네릭 |
| `↑` | 654 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `services/namespace_emergency/atomic_query.py` | 291 | `_self` | `self._script_sha[:8]` | `script_sha[:8]` | 불투명 |
| `services/namespace_emergency/health_penalty.py` | 232 | `ns` | `ns` | `→수동_ns_의미확인필요` | 불투명 |
| `↑` | 234 | `state` | `state.governance_mode` | `→수동` | 제네릭 |
| `↑` | 265 | `result` | `result` | `health_result` | 제네릭 |
| `↑` | 343 | `value` | `namespace or 'all'` | `→수동` | 제네릭 |
| `services/namespace_emergency/partition_reconciliation.py` | 482 | `action` | `action.action_type` | `action_action` | 제네릭 |
| `↑` | 483 | `message` | `action.message` | `→수동` | 제네릭 |
| `↑` | 510 | `_self` | `self._heartbeat_interval` | `heartbeat_interval` | 불투명 |
| `↑` | 535 | `status` | `status.partition_duration_seconds` | `→수동` | 제네릭 |
| `services/namespace_emergency/tracker.py` | 399 | `level` | `level.name` | `→수동` | 제네릭 |
| `services/pending_config.py` | 359 | `count` | `len(expired)` | `expired_count` | 제네릭 |
| `services/postmortem/deep_links.py` | 143 | `value` | `bool(self._postmortem_base_url)` | `→수동` | 제네릭 |
| `services/postmortem/deployment_correlator.py` | 239 | `count` | `len(deployments)` | `deployments_count` | 제네릭 |
| `services/postmortem/incident_group.py` | 542 | `value` | `now - created_ts` | `→수동` | 제네릭 |
| `↑` | 556 | `value` | `now - last_ts` | `→수동` | 제네릭 |
| `services/postmortem/notifier.py` | 331 | `_self` | `self._config.enabled` | `enabled` | 불투명 |
| `↑` | 429 | `response` | `response.status` | `→수동` | 제네릭 |
| `services/postmortem/revision.py` | 918 | `result` | `result["total"]` | `→수동` | 제네릭 |
| `services/postmortem/snapshot_builder.py` | 108 | `_self` | `self._service_name` | `service_name` | 불투명 |
| `↑` | 128 | `_self` | `self._service_name` | `service_name` | 불투명 |
| `↑` | 311 | `_self` | `self._service_name` | `service_name` | 불투명 |
| `↑` | 312 | `value` | `bool(self._snapshot.metrics_at_open)` | `→수동` | 제네릭 |
| `↑` | 314 | `count` | `len(self._snapshot.captured_logs)` | `captured_logs_count` | 제네릭 |
| `services/postmortem/store.py` | 124 | `record` | `record.incident_id` | `→수동` | 제네릭 |
| `services/predictive_forecaster/proactive_action.py` | 310 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 311 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 354 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 355 | `_self` | `self._min_confidence` | `min_confidence` | 불투명 |
| `↑` | 390 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 400 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `services/predictive_forecaster/service.py` | 478 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `services/predictive_forecaster/time_series.py` | 272 | `_self` | `self._count` | `count` | 불투명 |
| `↑` | 273 | `level` | `self._level` | `→수동` | 제네릭 |
| `↑` | 331 | `_self` | `self._count` | `count` | 불투명 |
| `↑` | 332 | `level` | `self._level` | `→수동` | 제네릭 |
| `services/rate_limit/distributed_channel.py` | 153 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 159 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 185 | `count` | `len(self._handlers)` | `handlers_count` | 제네릭 |
| `services/rate_limit_coordinator/coordinator.py` | 153 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 154 | `value` | `now - last_time` | `→수동` | 제네릭 |
| `↑` | 190 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 231 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 244 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 272 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 273 | `state` | `state.consecutive_429s` | `→수동` | 제네릭 |
| `↑` | 367 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 397 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 405 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `services/replay_service/service.py` | 217 | `result` | `result.error` | `result_error` | 제네릭 |
| `↑` | 290 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 542 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 544 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `↑` | 570 | `count` | `len(all_entries)` | `all_entries_count` | 제네릭 |
| `↑` | 657 | `result` | `result.error` | `result_error` | 제네릭 |
| `↑` | 741 | `entry` | `entry.id` | `→수동` | 제네릭 |
| `services/retry_handler/handler.py` | 289 | `result` | `result.wait_time` | `→수동` | 제네릭 |
| `↑` | 356 | `_self` | `self.backoff.calculate(attempt)` | `calculate(attempt)` | 불투명 |
| `↑` | 429 | `_self` | `self.config.domain` | `domain` | 불투명 |
| `↑` | 538 | `value` | `attempt + 1` | `→수동` | 제네릭 |
| `↑` | 604 | `_self` | `self._retry_budget.get_stats()` | `retry_budget.get_stats()` | 불투명 |
| `↑` | 621 | `_self` | `self.config.max_attempts` | `max_attempts` | 불투명 |
| `↑` | 746 | `result` | `result.dlq_id` | `dlq_id` | 제네릭 |
| `↑` | 752 | `result` | `result.error` | `result_error` | 제네릭 |
| `services/retry_handler/sinks.py` | 108 | `result` | `result.dlq_id` | `dlq_id` | 제네릭 |
| `↑` | 114 | `result` | `result.error` | `result_error` | 제네릭 |
| `services/rollback/service.py` | 114 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `services/runbook/executor.py` | 469 | `key` | `idempotency_key` | `→수동` | 제네릭 |
| `↑` | 470 | `status` | `existing_record.status.value` | `→수동` | 제네릭 |
| `↑` | 495 | `action` | `step.action` | `step_action` | 제네릭 |
| `↑` | 927 | `key` | `idempotency_key` | `→수동` | 제네릭 |
| `services/runbook/runbook_registry.py` | 603 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `services/runtime_config/approval.py` | 92 | `request` | `request['id']` | `→수동` | 제네릭 |
| `services/runtime_config/base.py` | 160 | `key` | `key` | `config_key` | 제네릭 |
| `↑` | 161 | `value` | `value` | `config_value` | 제네릭 |
| `↑` | 169 | `key` | `key` | `config_key` | 제네릭 |
| `↑` | 170 | `value` | `value` | `config_value` | 제네릭 |
| `↑` | 177 | `key` | `key` | `config_key` | 제네릭 |
| `↑` | 178 | `value` | `value` | `config_value` | 제네릭 |
| `↑` | 295 | `value` | `list(new_values.keys())` | `→수동` | 제네릭 |
| `services/runtime_config/chaos_storage.py` | 297 | `value` | `list(kwargs.keys())` | `→수동` | 제네릭 |
| `services/security/service.py` | 435 | `value` | `'` | `→수동` | 제네릭 |
| `services/security/session_registry.py` | 86 | `count` | `len(existing)` | `existing_count` | 제네릭 |
| `services/security_notification/email_handler.py` | 50 | `message` | `message['title']` | `→수동` | 제네릭 |
| `↑` | 67 | `count` | `len(recipients)` | `recipients_count` | 제네릭 |
| `↑` | 135 | `message` | `message['title']` | `→수동` | 제네릭 |
| `services/security_notification/pagerduty_handler.py` | 146 | `message` | `message['title']` | `→수동` | 제네릭 |
| `services/security_notification/service.py` | 300 | `count` | `len(result.results)` | `results_count` | 제네릭 |
| `services/security_notification/slack_handler.py` | 50 | `message` | `message['title']` | `→수동` | 제네릭 |
| `↑` | 189 | `message` | `message['title']` | `→수동` | 제네릭 |
| `services/security_notification/sms_handler.py` | 47 | `message` | `message['title']` | `→수동` | 제네릭 |
| `↑` | 102 | `message` | `message['title']` | `→수동` | 제네릭 |
| `services/stress_test_service/service.py` | 578 | `value` | `i+1` | `→수동` | 제네릭 |
| `↑` | 585 | `value` | `i+1` | `→수동` | 제네릭 |
| `services/system_control.py` | 129 | `_self` | `self._cached_state.enabled` | `cached_state.enabled` | 불투명 |
| `↑` | 130 | `value` | `type(self._backend).__name__` | `adapter_type` | 제네릭 |
| `↑` | 230 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 231 | `value` | `reason or 'N/A'` | `→수동` | 제네릭 |
| `↑` | 262 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 263 | `value` | `reason or 'N/A'` | `→수동` | 제네릭 |
| `↑` | 298 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 326 | `actor` | `actor` | `actor_id` | 제네릭 |
| `services/system_metrics_cache.py` | 92 | `_self` | `self._refresh_interval` | `refresh_interval` | 불투명 |
| `services/throttle/adaptive/__init__.py` | 829 | `_self` | `self.config.sla_critical_ms` | `sla_critical_ms` | 불투명 |
| `↑` | 898 | `_self` | `self.config.sla_warning_ms` | `sla_warning_ms` | 불투명 |
| `↑` | 963 | `_self` | `self._current_limit` | `current_limit` | 불투명 |
| `↑` | 1001 | `_self` | `self._current_limit` | `current_limit` | 불투명 |
| `↑` | 1046 | `_self` | `self._limit_before_429` | `limit_before_429` | 불투명 |
| `↑` | 1236 | `_self` | `self._base_limit_before_emergency` | `base_limit_before_emergency` | 불투명 |
| `services/throttle/adaptive/_emergency.py` | 113 | `level` | `level` | `→수동_level_컨텍스트확인` | 제네릭 |
| `↑` | 114 | `_self` | `self._current_limit` | `current_limit` | 불투명 |
| `↑` | 163 | `level` | `level` | `→수동_level_컨텍스트확인` | 제네릭 |
| `↑` | 253 | `level` | `level.name` | `→수동` | 제네릭 |
| `services/throttle/adaptive/_error_budget.py` | 85 | `_self` | `self._target_slo_patterns` | `target_slo_patterns` | 불투명 |
| `↑` | 151 | `_self` | `self._target_slo_patterns` | `target_slo_patterns` | 불투명 |
| `↑` | 238 | `_self` | `self._target_slo_patterns` | `target_slo_patterns` | 불투명 |
| `↑` | 344 | `_self` | `self._error_budget_multiplier` | `error_budget_multiplier` | 불투명 |
| `services/throttle/adaptive/_full_stop.py` | 116 | `status` | `status.budget_remaining_percent` | `→수동` | 제네릭 |
| `services/throttle/adaptive/_governance.py` | 148 | `_self` | `self._shedding_suggested_limit` | `shedding_suggested_limit` | 불투명 |
| `↑` | 194 | `_self` | `self._emergency_level` | `emergency_level` | 불투명 |
| `services/throttle/adaptive/_rate_limit.py` | 97 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 173 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `services/throttle/adaptive/_recovery.py` | 118 | `_self` | `self._recovery_dampening_step` | `recovery_dampening_step` | 불투명 |
| `↑` | 143 | `_self` | `self._base_limit_before_emergency` | `base_limit_before_emergency` | 불투명 |
| `services/throttle/adaptive_dlq_replay.py` | 303 | `_self` | `self._replay_min_recovery_percent` | `replay_min_recovery_percent` | 불투명 |
| `↑` | 346 | `count` | `len(pending_entries)` | `pending_entries_count` | 제네릭 |
| `↑` | 366 | `entry` | `entry.id` | `→수동` | 제네릭 |
| `services/throttle/audit.py` | 469 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 578 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 631 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `services/throttle/cb_bridge.py` | 198 | `_self` | `self.sla_critical_ms` | `sla_critical_ms` | 불투명 |
| `services/throttle/dlq_integration.py` | 154 | `result` | `result.entry_id` | `→수동` | 제네릭 |
| `↑` | 160 | `result` | `result.message` | `→수동` | 제네릭 |
| `↑` | 212 | `result` | `result.processed` | `→수동` | 제네릭 |
| `services/throttle/notification_fallback_recorder.py` | 81 | `count` | `len(self._memory_buffer)` | `memory_buffer_count` | 제네릭 |
| `services/throttle/registry.py` | 117 | `config` | `config.service_name` | `→수동` | 제네릭 |
| `services/throttle/safe_open_fallback.py` | 284 | `state` | `state.last_known_safe_limit` | `→수동` | 제네릭 |
| `services/throttle/sla_notification.py` | 189 | `result` | `result.channels_sent` | `→수동` | 제네릭 |
| `↑` | 194 | `result` | `result.suppression_reason` | `suppression_reason` | 제네릭 |
| `↑` | 199 | `result` | `result.error` | `result_error` | 제네릭 |
| `↑` | 258 | `result` | `result.channels_sent` | `→수동` | 제네릭 |
| `↑` | 263 | `result` | `result.error` | `result_error` | 제네릭 |
| `↑` | 310 | `result` | `result.channels_sent` | `→수동` | 제네릭 |
| `services/throttle/throttle_sla_alert_urls.py` | 59 | `value` | `bool(self._dashboard_base_url)` | `→수동` | 제네릭 |
| `services/unified_notification/service.py` | 102 | `message` | `payload.message` | `→수동` | 제네릭 |
| `services/xtest_cleanup_service.py` | 137 | `count` | `len(expired_sessions)` | `expired_sessions_count` | 제네릭 |
| `↑` | 152 | `session` | `session.session_id` | `→수동` | 제네릭 |
| `services/xtest_session_manager.py` | 348 | `count` | `len(expired_sessions)` | `expired_sessions_count` | 제네릭 |

---

## 4. `→수동` 항목 처리 가이드

`→수동` 표기 항목(~283건)은 코드 컨텍스트를 직접 확인하여 다음 기준으로 필드명을 결정한다:

### 4.1 `session` 계열 (~25건)
- `session=session.id` → `session_id` (식별자)
- `session=session.status` → `session_status` (상태)
- `session=session.session_id` → `session_id` (이미 ID 포함)
- `session=session.patterns_learned` → `patterns_learned` (속성 직접 사용)

### 4.2 `rollout` 계열 (~15건)
- `rollout=rollout.id` → `rollout_id`
- `rollout=rollout.state` → `rollout_state`
- `rollout=rollout.current_stage_index` → `current_stage_index`

### 4.3 `state` 계열 (~15건)
- `state=state.service_name` → `state_service_name` 또는 `service_name` (충돌 확인 필요)
- `state=state.state` → `cb_state` (circuit breaker 컨텍스트)
- `state=state.consecutive_429s` → `consecutive_429s`

### 4.4 `action` 계열 (~20건)
- `action=action.upper()` → `audit_action` (감사 기록용)
- `action=action` → 컨텍스트에 따라 `cb_action`, `emergency_action`, `throttle_action` 등

### 4.5 `result` 계열 (~25건)
- `result=result.error` → `result_error`
- `result=result.dlq_id` → `dlq_id`
- `result=result.suppression_reason` → `suppression_reason`
- `result=result["total"]` → `total` (dict 접근)

### 4.6 `request` 계열 (~10건)
- `request=request.service_name` → `request_service_name`
- `request=request.request_id` → `request_id`
- `request=request.action` → `request_action`

### 4.7 `config` 계열 (~10건)
- `config=config.service_id` → `service_id`
- `config=config.criticality` → `criticality`
- `config=config.namespace` → `config_namespace`

### 4.8 `level` / `value` / `message` 계열
- `level=level.value` → 컨텍스트에 따라 `blast_radius_level`, `emergency_level`, `error_budget_level` 등
- `value=reason or 'N/A'` → `reason`
- `message=message` → `detail_message`

---

## 5. 구현 절차

### 5.1 추천 순서 (하위 디렉토리별)

1. `services/audit/` — 독립적, 감사 로그 특화
2. `services/coordination/` — `session` 패턴 일괄 처리
3. `services/circuit_breaker/` — `state` 패턴 집중
4. `services/canary/` — `rollout` 패턴 집중
5. `services/throttle/` — `_self` 밀집
6. `services/error_budget/` — `domain` 패턴 반복
7. `services/emergency_mode/` — `level` 패턴 집중
8. 나머지 모듈 순차 진행

### 5.2 단계

1. 자동 변환(~195건): 스크립트 또는 일괄 sed로 확정 패턴 변환
2. 수동 검토(~283건): `→수동` 항목을 코드에서 직접 확인하여 필드명 결정
3. 테스트 실행: `pytest tests/ -k "not chaos"` — 서비스 계층 테스트 통과 확인
4. Loki 쿼리 검증: 기존 대시보드/알림 규칙에서 이전 필드명 사용 여부 확인

### 5.3 주의사항

- **이벤트 메시지(첫 번째 positional 인자)는 절대 변경하지 않는다**
- structlog의 예약어(`exc_info`, `stack_info`)는 변경 대상이 아님
- 같은 logger 호출에서 새 필드명이 기존 다른 필드와 충돌하지 않는지 반드시 확인
- `type=action.type.value` 같은 경우 Python 내장 `type` 함수와 혼동 주의 → `action_type`으로 변환

---

## 6. 완료 기준

- [ ] 478건 전체 변환 완료 (자동 ~195건 + 수동 ~283건)
- [ ] `pytest tests/` 통과
- [ ] 변환 전후 필드명 매핑 기록 (이 문서의 테이블)
- [ ] Loki/Grafana 대시보드 쿼리 업데이트 확인
