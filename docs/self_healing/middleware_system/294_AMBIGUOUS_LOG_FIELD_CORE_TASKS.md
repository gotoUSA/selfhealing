# 294. 모호한 로그 필드 리네이밍 — core/, coordination/, tasks/, celery_tasks/

> **문서 번호**: 294
> **작성일**: 2026-02-25
> **상태**: 구현 대기
> **대상**: `packages/selfhealing-python/src/selfhealing/core/`, `coordination/`, `tasks/`, `celery_tasks/`
> **선행 문서**: 290_AMBIGUOUS_LOG_FIELD_OVERVIEW.md
> **구현 우선순위**: ★★★★☆ (2순위 — 핵심 로직 계층, `_self` 밀집 + `parameter` 패턴 반복)

---

## 1. 범위 요약

| 지표 | 값 |
|---|---|
| 대상 파일 수 | **38개** |
| 모호한 kwargs 인스턴스 | **150건** |
| 불투명 필드(`_self`, `_event` 등) | ~65건 |
| 제네릭 필드(`parameter`, `key`, `count`, `result` 등) | ~85건 |
| 자동 변환 제안 가능 | ~70건 (47%) |
| 수동 컨텍스트 검토 필요(`→수동`) | ~80건 (53%) |

### 1.1 이 문서가 2순위인 이유

1. **핵심 로직 계층** — `core/` 디렉토리는 시스템의 decision engine, safety bounds, hedging 등 핵심 알고리즘 포함
2. **`_self=self._resource_name` 패턴 반복** — `coordination/` 내 elector 클래스들에서 일괄 변환 가능
3. **`parameter=parameter` 패턴 밀집** — `core/safety_bounds.py`, `core/auto_rollback_guard.py` 등에서 반복

### 1.2 하위 디렉토리별 분포

| 하위 디렉토리 | 건수 | 주요 패턴 |
|---|---|---|
| `coordination/` | ~35건 | `_self=self._resource_name` → `resource_name` |
| `core/` | ~55건 | `parameter=parameter`, `key=key`, `_self=self._config.*` |
| `tasks/` | ~45건 | `domain=domain`, `count=len(...)`, `_self=self.name` |
| `celery_tasks/` | ~15건 | `result=result.*`, `domain=domain` |

---

## 2. 명명 규칙 (290 문서 §3 참조)

| 규칙 | 예시 |
|---|---|
| `_self=self.xxx` → `xxx` | `_self=self._resource_name` → `resource_name=self._resource_name` |
| `parameter=parameter` → `→수동` | 컨텍스트별 결정: `safety_parameter`, `tuning_parameter` 등 |
| `key=key` → 컨텍스트 접두사 | `config_key`, `state_key` 등 |
| `domain=domain` → `healing_domain` | selfhealing 도메인 구분자 |
| `count=len(items)` → `items_count` | 카운트 접두사 패턴 |
| `message=message` → `detail_message` | structlog event와 구분 |
| `_event=event.id` → `→수동` | structlog 내부 충돌 확인 필요 |

---

## 3. 전수 변환 테이블

> **범례**
> - `↑` = 바로 위 행과 같은 파일
> - `→수동` = 자동 변환 불가, 코드 컨텍스트를 확인하여 수동 결정 필요
> - 모든 파일 경로는 `packages/selfhealing-python/src/selfhealing/` 기준 상대 경로

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 분류 |
|---|---|---|---|---|---|
| `celery_tasks/circuit_breaker_tasks.py` | 44 | `result` | `result["count"]` | `→수동` | 제네릭 |
| `celery_tasks/dlq_tasks.py` | 56 | `result` | `result.total` | `→수동` | 제네릭 |
| `↑` | 129 | `result` | `result.error` | `result_error` | 제네릭 |
| `↑` | 193 | `result` | `result.total` | `→수동` | 제네릭 |
| `↑` | 247 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 260 | `result` | `result.total` | `→수동` | 제네릭 |
| `↑` | 323 | `result` | `result.get("expired_count", 0)` | `expired_count` | 제네릭 |
| `celery_tasks/metrics_tasks.py` | 45 | `value` | `sum(metrics.get('dlq_pending_by_domain', {}).values())` | `→수동_value_dict_get확인` | 제네릭 |
| `coordination/dlq_consumer.py` | 93 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 102 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 114 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 218 | `entry` | `entry.id` | `→수동` | 제네릭 |
| `↑` | 219 | `result` | `result.error` | `result_error` | 제네릭 |
| `↑` | 225 | `entry` | `entry.id` | `→수동` | 제네릭 |
| `coordination/etcd_elector.py` | 211 | `_self` | `self._lease_id` | `lease_id` | 불투명 |
| `↑` | 246 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 297 | `_self` | `self._lease_id` | `lease_id` | 불투명 |
| `↑` | 328 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 346 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 363 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 449 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 469 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 488 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 518 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `coordination/redis_elector.py` | 287 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 344 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 372 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 398 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 527 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 547 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 566 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 597 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `coordination/scheduler.py` | 222 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 241 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 267 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 279 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `↑` | 292 | `_self` | `self._resource_name` | `resource_name` | 불투명 |
| `core/action_executor.py` | 207 | `action` | `action.name` | `→수동` | 제네릭 |
| `↑` | 244 | `action` | `action.name` | `→수동` | 제네릭 |
| `↑` | 245 | `target` | `action.target` | `→수동` | 제네릭 |
| `↑` | 263 | `action` | `action.name` | `→수동` | 제네릭 |
| `↑` | 264 | `target` | `action.target` | `→수동` | 제네릭 |
| `↑` | 291 | `action` | `action.name` | `→수동` | 제네릭 |
| `↑` | 292 | `target` | `action.target` | `→수동` | 제네릭 |
| `core/auto_rollback_guard.py` | 463 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 470 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 481 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 488 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 497 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 504 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 568 | `message` | `message` | `detail_message` | 제네릭 |
| `↑` | 612 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `core/cluster_identity.py` | 167 | `region` | `%s` | `→수동` | 제네릭 |
| `core/connection_health.py` | 198 | `status` | `status.value` | `→수동` | 제네릭 |
| `core/decision_engine.py` | 152 | `count` | `len(self.rules)` | `rules_count` | 제네릭 |
| `↑` | 363 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `core/hedging/async_executor.py` | 176 | `_self` | `self._config.delay` | `delay` | 불투명 |
| `core/hedging/async_strategy.py` | 166 | `_self` | `self._current_load_level` | `current_load_level` | 불투명 |
| `↑` | 248 | `_self` | `self._current_load_level` | `current_load_level` | 불투명 |
| `core/hedging/executor.py` | 227 | `_self` | `self._config.delay` | `delay` | 불투명 |
| `core/hedging/result_validator.py` | 310 | `record` | `record.operation_id` | `→수동` | 제네릭 |
| `↑` | 365 | `record` | `record.operation_id` | `→수동` | 제네릭 |
| `↑` | 395 | `record` | `record.operation_id` | `→수동` | 제네릭 |
| `core/hedging/strategy.py` | 198 | `_self` | `self._current_load_level` | `current_load_level` | 불투명 |
| `↑` | 288 | `_self` | `self._current_load_level` | `current_load_level` | 불투명 |
| `core/hooks.py` | 185 | `count` | `len(cls._hooks)` | `hooks_count` | 제네릭 |
| `↑` | 204 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 270 | `result` | `result.hook_name` | `→수동` | 제네릭 |
| `↑` | 320 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `core/pool_monitor.py` | 205 | `status` | `health_status.value` | `→수동` | 제네릭 |
| `↑` | 274 | `status` | `self._simulation_override.value` | `→수동` | 제네릭 |
| `core/resource_monitor.py` | 139 | `value` | `max_bytes / 1024 / 1024` | `→수동` | 제네릭 |
| `↑` | 208 | `value` | `requested_bytes / 1024 / 1024` | `→수동` | 제네릭 |
| `↑` | 285 | `value` | `current_level / 1024 / 1024` | `→수동` | 제네릭 |
| `core/runtime_feedback.py` | 421 | `result` | `result.parameter` | `→수동` | 제네릭 |
| `↑` | 539 | `message` | `message` | `detail_message` | 제네릭 |
| `↑` | 579 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 590 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `core/safe_defaults.py` | 529 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 530 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 540 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 541 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 722 | `key` | `key` | `config_key` | 제네릭 |
| `↑` | 743 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 756 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 802 | `result` | `result.changes_count` | `→수동` | 제네릭 |
| `↑` | 809 | `count` | `len(result.fatal_violations)` | `fatal_violations_count` | 제네릭 |
| `↑` | 810 | `value` | `list(result.fatal_violations.keys())` | `→수동` | 제네릭 |
| `↑` | 939 | `result` | `result['max_blast_radius']` | `→수동` | 제네릭 |
| `↑` | 950 | `result` | `result['failure_rate']` | `→수동` | 제네릭 |
| `core/safety_bounds.py` | 150 | `count` | `len(self.bounds)` | `bounds_count` | 제네릭 |
| `↑` | 177 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 183 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 191 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 200 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 212 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 284 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 291 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 311 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `core/state_backend.py` | 94 | `_self` | `self._directory` | `directory` | 불투명 |
| `↑` | 156 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 162 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 179 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 213 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 258 | `_self` | `self._redis_url` | `redis_url` | 불투명 |
| `↑` | 281 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 296 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 307 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 318 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `core/tiered_redis.py` | 71 | `_self` | `self._local_url` | `local_url` | 불투명 |
| `↑` | 98 | `_self` | `self._local_url` | `local_url` | 불투명 |
| `↑` | 115 | `_self` | `self._global_url` | `global_url` | 불투명 |
| `core/tls_handler.py` | 306 | `value` | `attempt + 1` | `→수동` | 제네릭 |
| `↑` | 324 | `value` | `attempt + 1` | `→수동` | 제네릭 |
| `tasks/backpressure_mixin.py` | 138 | `_self` | `self._backpressure_retry_count` | `backpressure_retry_count` | 불투명 |
| `↑` | 144 | `_self` | `self.backpressure_retry_countdown` | `backpressure_retry_countdown` | 불투명 |
| `tasks/base.py` | 116 | `_self` | `self.name` | `name` | 불투명 |
| `↑` | 205 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 306 | `_self` | `self.name` | `name` | 불투명 |
| `↑` | 352 | `_self` | `self.name` | `name` | 불투명 |
| `↑` | 396 | `_self` | `self.name` | `name` | 불투명 |
| `↑` | 397 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `tasks/canary_watchdog.py` | 253 | `result` | `result.scanned_count` | `→수동` | 제네릭 |
| `↑` | 310 | `rollout` | `rollout.id` | `→수동` | 제네릭 |
| `↑` | 366 | `rollout` | `rollout.id` | `→수동` | 제네릭 |
| `↑` | 524 | `rollout` | `rollout.id` | `→수동` | 제네릭 |
| `tasks/cascade_cleanup_tasks.py` | 82 | `count` | `len(to_archive)` | `to_archive_count` | 제네릭 |
| `↑` | 106 | `_event` | `event.id` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 229 | `count` | `len(to_purge)` | `to_purge_count` | 제네릭 |
| `↑` | 252 | `_event` | `event.id` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 392 | `result` | `result['checked']` | `→수동` | 제네릭 |
| `↑` | 398 | `count` | `len(result['errors'])` | `result['errors']_count` | 제네릭 |
| `↑` | 469 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `tasks/cleanup_tasks.py` | 194 | `msg` | `msg` | `detail_msg` | 제네릭 |
| `↑` | 205 | `msg` | `msg` | `detail_msg` | 제네릭 |
| `tasks/compliance_tasks.py` | 78 | `value` | `stage_name or "all"` | `→수동` | 제네릭 |
| `↑` | 109 | `report` | `report.total_checks` | `→수동` | 제네릭 |
| `↑` | 229 | `value` | `f" for {stage_name}" if stage_name else ""` | `→수동` | 제네릭 |
| `↑` | 248 | `report` | `report.total_cost` | `→수동` | 제네릭 |
| `tasks/config_apply.py` | 62 | `result` | `result.get('reason')` | `reason` | 제네릭 |
| `↑` | 164 | `_self` | `self.request.retries + 1` | `retry_attempt` | 불투명 |
| `tasks/drift_detection.py` | 145 | `count` | `len(results["warnings"])` | `results["warnings"]_count` | 제네릭 |
| `↑` | 327 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `tasks/integrity_tasks.py` | 241 | `result` | `result.get('errors', [])` | `errors` | 제네릭 |
| `tasks/intelligence_tasks.py` | 114 | `count` | `len(warnings)` | `warnings_count` | 제네릭 |
| `↑` | 371 | `count` | `len(insights)` | `insights_count` | 제네릭 |
| `↑` | 524 | `count` | `len(recovered)` | `recovered_count` | 제네릭 |
| `tasks/traffic_aware_replay.py` | 237 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 290 | `result` | `result["total"]` | `→수동` | 제네릭 |
| `tasks/xtest_cleanup_tasks.py` | 65 | `result` | `result.sessions_cleaned` | `→수동` | 제네릭 |

---

## 4. `→수동` 항목 처리 가이드

`→수동` 표기 항목(~80건)은 코드 컨텍스트를 직접 확인하여 다음 기준으로 필드명을 결정한다:

### 4.1 `parameter` 계열 (~25건)
- `core/safety_bounds.py` — `safety_parameter` (안전 경계 파라미터)
- `core/auto_rollback_guard.py` — `rollback_parameter` (자동 롤백 설정)
- `core/decision_engine.py` — `decision_parameter` (의사결정 엔진 파라미터)
- `core/runtime_feedback.py` — `feedback_parameter` (런타임 피드백 파라미터)

### 4.2 `result` 계열 (~20건)
- `result=result.total` → `total` 또는 `dlq_total`
- `result=result.error` → `result_error`
- `result=result.get("expired_count", 0)` → `expired_count`
- `result=result.hook_name` → `hook_name`

### 4.3 `action` / `target` 계열
- `action=action.name` → `action_name`
- `target=action.target` → `action_target`

### 4.4 `key` 계열 (core/state_backend)
- `key=key` (state_backend 컨텍스트) → `state_key`

### 4.5 `record` / `entry` / `rollout` 계열
- `record=record.operation_id` → `operation_id`
- `entry=entry.id` → `entry_id`
- `rollout=rollout.id` → `rollout_id`

### 4.6 `name` 계열
- `name=name` (scheduler 컨텍스트) → `scheduler_name` 또는 `job_name`
- `name=name` (hooks 컨텍스트) → `hook_name`

### 4.7 `_event` 필드 (structlog 내부)
- `_event=event.id` — structlog가 `event` 키를 내부적으로 사용하므로 `_event`로 우회한 패턴
- 변환 시 structlog 내부 동작에 영향 없는지 반드시 확인 → `cascade_event_id` 등

### 4.8 `value` 계열
- `value=attempt + 1` → `retry_attempt`
- `value=value` → 코드 확인 후 `config_value`, `parameter_value` 등
- `value=requested_bytes / 1024 / 1024` → `requested_mb`

---

## 5. 구현 절차

### 5.1 추천 순서

1. `coordination/etcd_elector.py`, `coordination/redis_elector.py` — `_self=self._resource_name` → `resource_name` 일괄 (20건)
2. `core/safety_bounds.py`, `core/auto_rollback_guard.py` — `parameter=parameter` 일괄 (20건)
3. `tasks/` — `domain=domain` → `healing_domain`, `count=len(...)` → `xxx_count` 일괄
4. `core/hedging/` — `_self=self._current_load_level` → `current_load_level` 일괄
5. 나머지 수동 검토 항목

### 5.2 단계

1. 자동 변환(~70건): 확정 패턴 일괄 변환
2. 수동 검토(~80건): `→수동` 항목을 코드에서 직접 확인하여 필드명 결정
3. 테스트 실행: `pytest tests/ -k "core or task or coordination"` — 통과 확인
4. Loki 쿼리 검증

### 5.3 주의사항

- **이벤트 메시지(첫 번째 positional 인자)는 절대 변경하지 않는다**
- `_event` 필드 변환 시 structlog 내부 동작 확인 필수
- `core/` 디렉토리는 핵심 알고리즘이므로 테스트 커버리지가 충분한지 확인

---

## 6. 완료 기준

- [ ] 150건 전체 변환 완료 (자동 ~70건 + 수동 ~80건)
- [ ] `pytest tests/` 통과
- [ ] 변환 전후 필드명 매핑 기록 (이 문서의 테이블)
- [ ] Loki/Grafana 대시보드 쿼리 업데이트 확인
