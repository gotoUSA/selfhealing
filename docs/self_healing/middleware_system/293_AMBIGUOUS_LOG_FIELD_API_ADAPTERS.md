# 293. 모호한 로그 필드 리네이밍 — api/, adapters/

> **문서 번호**: 293
> **작성일**: 2026-02-25
> **상태**: 구현 대기
> **대상**: `packages/selfhealing-python/src/selfhealing/api/`, `packages/selfhealing-python/src/selfhealing/adapters/`
> **선행 문서**: 290_AMBIGUOUS_LOG_FIELD_OVERVIEW.md
> **구현 우선순위**: ★★★☆☆ (3순위 — 외부 인터페이스 계층, request/key 패턴 반복)

---

## 1. 범위 요약

| 지표 | 값 |
|---|---|
| 대상 파일 수 | **87개** |
| 모호한 kwargs 인스턴스 | **302건** |
| 불투명 필드(`_self`, `_event` 등) | ~60건 |
| 제네릭 필드(`key`, `request`, `count`, `value` 등) | ~242건 |
| 자동 변환 제안 가능 | ~140건 (46%) |
| 수동 컨텍스트 검토 필요(`→수동`) | ~162건 (54%) |

### 1.1 이 문서가 3순위인 이유

1. **API/어댑터 계층** — 외부 인터페이스이므로 로그 가독성이 운영에 직결
2. **`key=key` 패턴 밀집** — cache/redis 어댑터에서 `cache_key`, `redis_key` 일괄 변환 가능
3. **`request=request.user/path` 패턴** — Django 미들웨어/뷰에서 반복적으로 사용

### 1.2 하위 디렉토리별 분포

| 하위 디렉토리 | 건수 | 주요 패턴 |
|---|---|---|
| `adapters/cache/` | ~35건 | `key=key` → `cache_key`, `_self=self._name` → `name` |
| `api/django/views/` | ~80건 | `request=request.user`, `domain=domain`, `actor=actor` |
| `api/django/middleware/` | ~15건 | `request=request.path` → `request_path` |
| `adapters/celery/` | ~20건 | `domain=domain`, `_self=self.request.retries` |
| `adapters/rate_limit/` | ~20건 | `key=key` → `redis_key` |
| `adapters/audit/` | ~20건 | `_self`, `count=len(...)` |
| 기타 | ~112건 | 다양한 패턴 |

---

## 2. 명명 규칙 (290 문서 §3 참조)

| 규칙 | 예시 |
|---|---|
| `_self=self.xxx` → `xxx` | `_self=self._name` → `name=self._name` |
| `key=key` (cache 컨텍스트) → `cache_key` | 캐시 키 식별 |
| `key=key` (redis 컨텍스트) → `redis_key` | Redis 키 식별 |
| `request=request.path` → `request_path` | HTTP 요청 경로 |
| `request=request.user` → `→수동` | 사용자 정보 (민감도 확인 필요) |
| `domain=domain` → `healing_domain` | selfhealing 도메인 구분자 |
| `actor=actor` → `actor_id` | 행위자 식별 |
| `pk=pk` → `record_pk` | 레코드 PK 식별 |
| `count=len(items)` → `items_count` | 카운트 접두사 패턴 |
| `value=type(x).__name__` → `adapter_type` | 타입 이름 반환 시 통일 |

---

## 3. 전수 변환 테이블

> **범례**
> - `↑` = 바로 위 행과 같은 파일
> - `→수동` = 자동 변환 불가, 코드 컨텍스트를 확인하여 수동 결정 필요
> - 모든 파일 경로는 `packages/selfhealing-python/src/selfhealing/` 기준 상대 경로

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 분류 |
|---|---|---|---|---|---|
| `adapters/airgap/factory.py` | 95 | `value` | `type(adapter).__name__` | `adapter_type` | 제네릭 |
| `adapters/airgap/redis_adapter.py` | 94 | `_self` | `self.prefix` | `prefix` | 불투명 |
| `↑` | 148 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 155 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 177 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `↑` | 184 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 211 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 276 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 325 | `key` | `key` | `redis_key` | 제네릭 |
| `adapters/audit/kafka_adapter.py` | 294 | `_self` | `self._sent_count` | `sent_count` | 불투명 |
| `adapters/audit/kafka_consumer.py` | 193 | `_self` | `self._config.topic` | `topic` | 불투명 |
| `↑` | 206 | `msg` | `msg.error()` | `→수동` | 제네릭 |
| `↑` | 250 | `_self` | `self._processed_count` | `processed_count` | 불투명 |
| `↑` | 356 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 383 | `value` | `value.get("action")` | `→수동_value_dict_get확인` | 제네릭 |
| `↑` | 437 | `p` | `p.partition` | `→수동_p_의미확인필요` | 불투명 |
| `↑` | 449 | `count` | `len(partitions)` | `partitions_count` | 제네릭 |
| `↑` | 469 | `count` | `len(offsets_to_commit)` | `offsets_to_commit_count` | 제네릭 |
| `↑` | 481 | `count` | `len(partitions)` | `partitions_count` | 제네릭 |
| `↑` | 503 | `value` | `value.get("action")` | `→수동_value_dict_get확인` | 제네릭 |
| `↑` | 646 | `count` | `len(self._batch)` | `batch_count` | 제네릭 |
| `adapters/audit/redis_buffer.py` | 303 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `↑` | 797 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `adapters/audit/singleton.py` | 109 | `value` | `type(adapter).__name__` | `adapter_type` | 제네릭 |
| `adapters/cache/memcached_adapter.py` | 284 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 302 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 314 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 326 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 360 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 387 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 408 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 565 | `key` | `key` | `cache_key` | 제네릭 |
| `adapters/cache/memory_adapter.py` | 138 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 148 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 170 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 175 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 512 | `count` | `len(keys_to_delete)` | `keys_to_delete_count` | 제네릭 |
| `adapters/cache/redis_adapter.py` | 112 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 122 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 140 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 160 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 165 | `_self` | `self._name` | `name` | 불투명 |
| `↑` | 324 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 353 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 365 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 377 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 393 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 405 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 422 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 439 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 462 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 569 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 570 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 584 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `↑` | 585 | `key` | `key` | `cache_key` | 제네릭 |
| `↑` | 603 | `name` | `name` | `→수동_name_컨텍스트확인` | 제네릭 |
| `adapters/celery/signal_hooks.py` | 303 | `value` | `type(exception).__name__` | `adapter_type` | 제네릭 |
| `↑` | 622 | `state` | `state` | `→수동_state_컨텍스트확인` | 제네릭 |
| `↑` | 784 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 786 | `result` | `result.dlq_id` | `dlq_id` | 제네릭 |
| `adapters/celery/tasks/circuit_breaker.py` | 109 | `count` | `len(pending)` | `pending_count` | 제네릭 |
| `↑` | 425 | `_self` | `self.request.retries + 1` | `retry_attempt` | 불투명 |
| `adapters/celery/tasks/dlq_replay.py` | 135 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `adapters/celery/tasks/monitoring.py` | 143 | `total` | `total_breaches` | `→수동` | 제네릭 |
| `↑` | 202 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 218 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 288 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `↑` | 317 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `adapters/celery/tasks/persistence.py` | 116 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `↑` | 136 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `adapters/celery/tasks/postmortem.py` | 105 | `status` | `group.status.value` | `→수동` | 제네릭 |
| `↑` | 314 | `count` | `len(affected_services)` | `affected_services_count` | 제네릭 |
| `↑` | 514 | `result` | `result.error` | `result_error` | 제네릭 |
| `↑` | 662 | `_self` | `self.request.retries + 1` | `retry_attempt` | 불투명 |
| `↑` | 1048 | `result` | `result.suppression_reason` | `suppression_reason` | 제네릭 |
| `adapters/celery/tasks/sla_notification.py` | 63 | `_self` | `self.request.retries + 1` | `retry_attempt` | 불투명 |
| `adapters/config_applier/composite.py` | 56 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 57 | `value` | `value` | `config_value` | 제네릭 |
| `adapters/config_applier/throttle.py` | 66 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `↑` | 83 | `parameter` | `parameter` | `→수동_parameter_컨텍스트확인` | 제네릭 |
| `adapters/deployment/kubernetes.py` | 196 | `count` | `len(deployments)` | `deployments_count` | 제네릭 |
| `adapters/deployment/mock.py` | 117 | `count` | `len(result)` | `result_count` | 제네릭 |
| `↑` | 250 | `count` | `len(result)` | `result_count` | 제네릭 |
| `adapters/django/apps.py` | 291 | `result` | `result.get("file_sequence")` | `file_sequence` | 제네릭 |
| `↑` | 298 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 309 | `result` | `result.get("error", "unknown")` | `error` | 제네릭 |
| `↑` | 526 | `count` | `len(result.dlq_pending)` | `dlq_pending_count` | 제네릭 |
| `adapters/django/statistics.py` | 467 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 503 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 672 | `count` | `len(entries)` | `entries_count` | 제네릭 |
| `adapters/health_checker.py` | 111 | `target` | `target` | `target_service` | 제네릭 |
| `↑` | 166 | `target` | `target` | `target_service` | 제네릭 |
| `↑` | 206 | `target` | `target` | `target_service` | 제네릭 |
| `adapters/ipc/cb_state_snapshot.py` | 241 | `value` | `'writer' if self.is_writer else 'reader'` | `→수동` | 제네릭 |
| `adapters/ipc/event_stream_proxy.py` | 209 | `value` | `event_types or 'all'` | `→수동` | 제네릭 |
| `↑` | 357 | `count` | `len(to_remove)` | `to_remove_count` | 제네릭 |
| `adapters/ipc/uds_client.py` | 122 | `_self` | `self._socket_path` | `socket_path` | 불투명 |
| `adapters/ipc/uds_server.py` | 229 | `_self` | `self._socket_path` | `socket_path` | 불투명 |
| `adapters/kafka/consumer.py` | 233 | `_event` | `event.topic` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 264 | `_event` | `event.topic` | `→수동_structlog내부확인` | 불투명 |
| `↑` | 300 | `msg` | `msg.error()` | `→수동` | 제네릭 |
| `adapters/kafka/producer.py` | 122 | `_self` | `self._settings.bootstrap_servers` | `bootstrap_servers` | 불투명 |
| `↑` | 187 | `report` | `report.topic` | `→수동` | 제네릭 |
| `adapters/kafka/retry.py` | 200 | `value` | `retry_count + 1` | `→수동` | 제네릭 |
| `↑` | 255 | `_self` | `self._config.final_dlq_topic` | `final_dlq_topic` | 불투명 |
| `↑` | 263 | `_self` | `self._config.final_dlq_topic` | `final_dlq_topic` | 불투명 |
| `adapters/kafka/schemas.py` | 199 | `_self` | `self._settings.schema_registry_url` | `schema_registry_url` | 불투명 |
| `adapters/memory/layered_repository/base.py` | 137 | `_self` | `self._adapter_type` | `adapter_type` | 불투명 |
| `adapters/memory/layered_repository/error_handling.py` | 111 | `_self` | `self._metrics.get('l2_sync_failure_count', 0)` | `metrics.get('l2_sync_failure_count', 0)` | 불투명 |
| `adapters/memory/layered_repository/l2_load.py` | 55 | `count` | `len(all_states)` | `all_states_count` | 제네릭 |
| `↑` | 63 | `value` | `timeout*1000` | `→수동` | 제네릭 |
| `adapters/memory/layered_repository/l2_sync.py` | 59 | `value` | `timeout*1000` | `→수동` | 제네릭 |
| `adapters/memory/shadow_logger.py` | 199 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 220 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `adapters/metrics/factory.py` | 86 | `value` | `type(adapter).__name__` | `adapter_type` | 제네릭 |
| `adapters/queues/celery_adapter.py` | 251 | `count` | `len(task_ids)` | `task_ids_count` | 제네릭 |
| `adapters/queues/rq_adapter.py` | 574 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `adapters/queues/sync_adapter.py` | 257 | `record` | `record.task_id` | `→수동` | 제네릭 |
| `↑` | 267 | `record` | `record.task_id` | `→수동` | 제네릭 |
| `↑` | 285 | `record` | `record.task_id` | `→수동` | 제네릭 |
| `adapters/rate_limit/database_adapter.py` | 157 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 177 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 198 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 216 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `adapters/rate_limit/memory_adapter.py` | 114 | `count` | `len(expired_keys)` | `expired_keys_count` | 제네릭 |
| `↑` | 149 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 172 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 186 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 197 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `adapters/rate_limit/redis_adapter.py` | 153 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 162 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 277 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 303 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 321 | `key` | `key` | `redis_key` | 제네릭 |
| `↑` | 341 | `key` | `key` | `redis_key` | 제네릭 |
| `adapters/redis/dlq.py` | 209 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `adapters/resilient/backend.py` | 280 | `entry` | `entry.sequence` | `→수동` | 제네릭 |
| `↑` | 849 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `adapters/traffic_routing/k8s_ingress_adapter.py` | 177 | `_self` | `self._ingress_name` | `ingress_name` | 불투명 |
| `api/django/audit_middleware.py` | 207 | `_self` | `self._read_audit_paths` | `read_audit_paths` | 불투명 |
| `↑` | 412 | `_self` | `self._failed_recordings` | `failed_recordings` | 불투명 |
| `api/django/exceptions/handler.py` | 200 | `value` | `type(exc).__name__` | `adapter_type` | 제네릭 |
| `api/django/middleware/actor_context.py` | 61 | `status` | `status` | `→수동_status_컨텍스트확인` | 제네릭 |
| `api/django/middleware/ip_ban.py` | 144 | `request` | `request.path` | `request_path` | 제네릭 |
| `api/django/middleware/permissions.py` | 43 | `request` | `request.path` | `request_path` | 제네릭 |
| `↑` | 54 | `request` | `request.path` | `request_path` | 제네릭 |
| `↑` | 80 | `request` | `request.path` | `request_path` | 제네릭 |
| `↑` | 90 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 101 | `request` | `request.path` | `request_path` | 제네릭 |
| `api/django/middleware/self_healing.py` | 163 | `count` | `len(cls.DLQ_ELIGIBLE_PATHS)` | `DLQ_ELIGIBLE_PATHS_count` | 제네릭 |
| `↑` | 193 | `request` | `request.path` | `request_path` | 제네릭 |
| `↑` | 269 | `request` | `request.path` | `request_path` | 제네릭 |
| `↑` | 270 | `response` | `response.status_code` | `→수동` | 제네릭 |
| `↑` | 354 | `state` | `state.upper()` | `→수동` | 제네릭 |
| `↑` | 355 | `_self` | `self.CB_SERVICE_NAME` | `CB_SERVICE_NAME` | 불투명 |
| `↑` | 397 | `_self` | `self.CB_SERVICE_NAME` | `CB_SERVICE_NAME` | 불투명 |
| `↑` | 470 | `result` | `result.dlq_id` | `dlq_id` | 제네릭 |
| `↑` | 471 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 490 | `result` | `result.error` | `result_error` | 제네릭 |
| `api/django/permissions.py` | 284 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 292 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 294 | `_self` | `self.emergency_expiry_hours` | `emergency_expiry_hours` | 불투명 |
| `↑` | 468 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 512 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 548 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 604 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 667 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 675 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 684 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 747 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 749 | `_self` | `self._get_client_ip(request)` | `get_client_ip(request)` | 불투명 |
| `↑` | 769 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 779 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 787 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/pool_circuit_breaker.py` | 94 | `_self` | `self._cache_interval_ms` | `cache_interval_ms` | 불투명 |
| `↑` | 140 | `_self` | `self._cache_interval_ms` | `cache_interval_ms` | 불투명 |
| `↑` | 286 | `_self` | `self._critical_stale_ms` | `critical_stale_ms` | 불투명 |
| `↑` | 527 | `_self` | `self._failure_count` | `failure_count` | 불투명 |
| `↑` | 595 | `_self` | `self._success_count` | `success_count` | 불투명 |
| `↑` | 691 | `status` | `status` | `→수동_status_컨텍스트확인` | 제네릭 |
| `↑` | 692 | `value` | `"enabled" if self._audit_enabled else "disabled"` | `→수동` | 제네릭 |
| `↑` | 834 | `_self` | `self._log_interval` | `log_interval` | 불투명 |
| `api/django/rate_limit.py` | 433 | `_self` | `self._consecutive_failures` | `consecutive_failures` | 불투명 |
| `↑` | 750 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `api/django/reauthentication.py` | 155 | `request` | `request.path` | `request_path` | 제네릭 |
| `↑` | 243 | `config` | `config.max_idle_minutes` | `→수동` | 제네릭 |
| `↑` | 259 | `config` | `config.max_session_minutes` | `→수동` | 제네릭 |
| `api/django/throttle_adapter.py` | 98 | `result` | `result.limit` | `→수동` | 제네릭 |
| `api/django/tiering/middleware.py` | 208 | `request` | `request.path` | `request_path` | 제네릭 |
| `api/django/tiering/registry.py` | 110 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 157 | `action` | `snapshot["action"]` | `→수동` | 제네릭 |
| `api/django/views/canary.py` | 245 | `rollout` | `rollout.id` | `→수동` | 제네릭 |
| `↑` | 409 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 492 | `count` | `len(results)` | `results_count` | 제네릭 |
| `api/django/views/chaos/config_views.py` | 68 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 125 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 179 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 233 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/chaos/report_views.py` | 114 | `report` | `report.report_id` | `→수동` | 제네릭 |
| `↑` | 115 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 255 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/chaos/safety_views.py` | 105 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 106 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 236 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 292 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 350 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/chaos/schedule_views.py` | 91 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 153 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 179 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 229 | `action` | `action` | `→수동_action_컨텍스트확인` | 제네릭 |
| `↑` | 231 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 263 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 264 | `result` | `result.status` | `→수동` | 제네릭 |
| `api/django/views/config.py` | 108 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 244 | `_self` | `self.config_name` | `config_name` | 불투명 |
| `↑` | 246 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 280 | `_self` | `self.config_name.upper()` | `upper()` | 불투명 |
| `↑` | 281 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 283 | `result` | `result.get("applied_strategy", "immediate")` | `applied_strategy` | 제네릭 |
| `↑` | 293 | `_self` | `self.config_name` | `config_name` | 불투명 |
| `↑` | 294 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 296 | `result` | `result.get("applied_strategy")` | `applied_strategy` | 제네릭 |
| `↑` | 478 | `_self` | `self.config_name` | `config_name` | 불투명 |
| `↑` | 480 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 500 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 526 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/dlq.py` | 60 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 62 | `result` | `result.processed` | `→수동` | 제네릭 |
| `↑` | 133 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 135 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 184 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 185 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 292 | `pk` | `pk` | `record_pk` | 불투명 |
| `↑` | 293 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 332 | `pk` | `pk` | `record_pk` | 불투명 |
| `↑` | 333 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 390 | `result` | `result["dlq_id"]` | `→수동` | 제네릭 |
| `↑` | 391 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 393 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/drift_threshold.py` | 115 | `value` | `list(update_fields.keys())` | `→수동` | 제네릭 |
| `api/django/views/emergency.py` | 179 | `level` | `level.name` | `→수동` | 제네릭 |
| `↑` | 180 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 261 | `actor` | `actor` | `actor_id` | 제네릭 |
| `api/django/views/error_budget/status.py` | 191 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 235 | `target` | `target` | `target_service` | 제네릭 |
| `api/django/views/governance/approval_views.py` | 110 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 150 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 189 | `actor` | `actor` | `actor_id` | 제네릭 |
| `api/django/views/governance/config_views.py` | 106 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 107 | `value` | `list(update_fields.keys())` | `→수동` | 제네릭 |
| `↑` | 181 | `actor` | `actor` | `actor_id` | 제네릭 |
| `↑` | 182 | `value` | `list(update_fields.keys())` | `→수동` | 제네릭 |
| `api/django/views/l2_storage_config.py` | 72 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 105 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/l2_storage_drift.py` | 136 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 137 | `result` | `result.get("reconciled", 0)` | `reconciled` | 제네릭 |
| `↑` | 185 | `result` | `result.get("reason", "unknown")` | `reason` | 제네릭 |
| `↑` | 192 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 193 | `result` | `result.get("action", "none")` | `action` | 제네릭 |
| `api/django/views/l2_storage_shadow_log.py` | 137 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 258 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 259 | `result` | `result.get("synced", 0)` | `synced` | 제네릭 |
| `api/django/views/l2_storage_status.py` | 122 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 160 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 194 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 195 | `result` | `result` | `→수동_result_컨텍스트확인` | 제네릭 |
| `api/django/views/recovery.py` | 220 | `session` | `session.session_id` | `→수동` | 제네릭 |
| `↑` | 222 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 288 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 382 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 458 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/xtest/base.py` | 281 | `request` | `request.path` | `request_path` | 제네릭 |
| `↑` | 301 | `request` | `request.path` | `request_path` | 제네릭 |
| `↑` | 323 | `request` | `request.path` | `request_path` | 제네릭 |
| `↑` | 348 | `result` | `result.block_reason` | `→수동` | 제네릭 |
| `↑` | 349 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 364 | `result` | `result.cpu_percent` | `→수동` | 제네릭 |
| `↑` | 403 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 508 | `component` | `component` | `→수동_component_컨텍스트확인` | 제네릭 |
| `api/django/views/xtest/circuit_breaker.py` | 118 | `request` | `request.user` | `→수동` | 제네릭 |
| `↑` | 188 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/xtest/dlq.py` | 143 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 145 | `count` | `len(created_ids)` | `created_ids_count` | 제네릭 |
| `↑` | 256 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/xtest/error_budget.py` | 90 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |
| `↑` | 93 | `request` | `request.user` | `→수동` | 제네릭 |
| `api/django/views/xtest/integration.py` | 133 | `status` | `result.status.value` | `→수동` | 제네릭 |
| `↑` | 134 | `count` | `len(result.steps)` | `steps_count` | 제네릭 |
| `api/django/views/xtest/observability.py` | 216 | `count` | `len(results['unaffected_services'])` | `results['unaffected_services']_count` | 제네릭 |
| `↑` | 490 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `api/django/views/xtest/rate_limit.py` | 306 | `count` | `len(events)` | `events_count` | 제네릭 |
| `api/django/views/xtest/replay.py` | 146 | `result` | `result["success"]` | `→수동` | 제네릭 |
| `↑` | 394 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 395 | `result` | `result["total"]` | `→수동` | 제네릭 |
| `api/django/views/xtest/retry.py` | 506 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 507 | `state` | `state.is_in_cooldown` | `→수동` | 제네릭 |
| `↑` | 644 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 646 | `config` | `config.max_attempts` | `→수동` | 제네릭 |
| `api/django/views/xtest/scenarios/base.py` | 271 | `_self` | `self.scenario_name` | `scenario_name` | 불투명 |
| `api/django/views/xtest/throttle_simulation.py` | 101 | `level` | `level` | `→수동_level_컨텍스트확인` | 제네릭 |
| `↑` | 218 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `↑` | 344 | `count` | `count` | `→수동_count_대상확인` | 제네릭 |

---

## 4. `→수동` 항목 처리 가이드

`→수동` 표기 항목(~162건)은 코드 컨텍스트를 직접 확인하여 다음 기준으로 필드명을 결정한다:

### 4.1 `request=request.user` 계열 (~40건)
- Django views에서 매우 빈번 — `request_user`로 통일 가능하나, 민감 정보(PII) 포함 여부 검토 필요
- 대안: `request_user_id` (user.pk만 로깅) 또는 `requesting_user`

### 4.2 `result` 계열 (~25건)
- `result=result.dlq_id` → `dlq_id`
- `result=result.error` → `result_error`
- `result=result.get("applied_strategy")` → `applied_strategy`
- `result=result.get("reconciled", 0)` → `reconciled`

### 4.3 `value` 계열 (~15건)
- `value=type(adapter).__name__` → `adapter_type` (일관 적용)
- `value="enabled" if ... else "disabled"` → `audit_status`
- `value=timeout*1000` → `timeout_ms`

### 4.4 `component` / `action` / `status` 계열
- `component=component` → 모듈 컨텍스트에 따라 `monitoring_component`, `health_component` 등
- `action=action` → `config_action`, `safety_action` 등
- `status=status` → `actor_status`, `middleware_status` 등

### 4.5 `key` 계열 (rate_limit, 비-cache)
- `key=key` (rate_limit 컨텍스트) → `rate_limit_key`
- `key=key` (모호한 컨텍스트) → 코드를 확인하여 `config_key`, `lookup_key` 등

---

## 5. 구현 절차

### 5.1 추천 순서

1. `adapters/cache/` — `key=key` → `cache_key` 일괄 변환 (35건, 기계적)
2. `adapters/rate_limit/` — `key=key` → `redis_key` 일괄 변환 (20건)
3. `api/django/middleware/` — `request=request.path` → `request_path` (15건)
4. `adapters/audit/`, `adapters/celery/` — `_self`, `count` 패턴
5. `api/django/views/` — 수동 검토 비율 높음, 마지막에 진행

### 5.2 단계

1. 자동 변환(~140건): 확정 패턴 일괄 변환
2. 수동 검토(~162건): `→수동` 항목을 코드에서 직접 확인하여 필드명 결정
3. 테스트 실행: `pytest tests/ -k "api or adapter"` — 통과 확인
4. Loki 쿼리 검증: 기존 대시보드/알림 규칙에서 이전 필드명 사용 여부 확인

### 5.3 주의사항

- **이벤트 메시지(첫 번째 positional 인자)는 절대 변경하지 않는다**
- `request=request.user` 로깅 시 PII(개인식별정보) 포함 여부 검토 — 필요시 `request.user.pk`만 로깅
- `_event` 필드는 structlog 내부 사용 여부 확인 후 변환

---

## 6. 완료 기준

- [ ] 302건 전체 변환 완료 (자동 ~140건 + 수동 ~162건)
- [ ] `pytest tests/` 통과
- [ ] 변환 전후 필드명 매핑 기록 (이 문서의 테이블)
- [ ] Loki/Grafana 대시보드 쿼리 업데이트 확인
