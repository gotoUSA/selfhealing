# 287. 넘버링 로그 필드 리네이밍 — `api/django/`, `adapters/`, `celery_tasks/` (51건)

> **문서 번호**: 287
> **작성일**: 2026-02-25
> **상태**: 구현 대기
> **대상**: `packages/selfhealing-python/src/selfhealing/{api/django/,adapters/,celery_tasks/}`
> **관련 문서**: 284_LOG_FIELD_NAMING_OVERVIEW.md

---

## 1. 대상 범위

외부 인터페이스 계층: Django REST API, Kafka/Celery 어댑터, Celery 태스크.
`result_N`, `pool_status_N`, `request_N` 등이 주로 사용된다.

**총 51건, 20개 파일.**

---

## 2. 변환 테이블

### 2.1 adapters/audit/kafka_adapter.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 295 | `self_1` | `self._error_count` | `error_count` | |

### 2.2 adapters/audit/kafka_consumer.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 251 | `self_1` | `self._error_count` | `error_count` | |
| 252 | `self_2` | `self._skipped_count` | `skipped_count` | |
| 438 | `p_1` | `p.offset` | `offset` | |

### 2.3 adapters/celery/signal_hooks.py (4건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 1110 | `_config_1` | `_config.cb_enabled` | `cb_enabled` | |
| 1111 | `_config_2` | `_config.dlq_enabled` | `dlq_enabled` | |
| 1112 | `_config_3` | `_config.metrics_enabled` | `metrics_enabled` | |
| 1113 | `_config_4` | `_config.forensics_enabled` | `forensics_enabled` | |

### 2.4 adapters/celery/tasks/circuit_breaker.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 64 | `gate_result_1` | `gate_result.threshold_percent` | `threshold_percent` | |

### 2.5 adapters/django/apps.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 527 | `count_1` | `len(result.circuit_breaker_states)` | `cb_states_count` | |
| 632 | `settings_1` | `settings.sample_interval` | `sample_interval` | |

### 2.6 adapters/kafka/consumer.py (4건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 234 | `event_1` | `event.partition` | `partition` | |
| 235 | `event_2` | `event.offset` | `offset` | |
| 265 | `event_1` | `event.partition` | `partition` | L234와 동일 패턴 |
| 266 | `event_2` | `event.offset` | `offset` | L235와 동일 패턴 |

### 2.7 adapters/kafka/producer.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 123 | `self_1` | `self._settings.producer_idempotent` | `producer_idempotent` | |
| 188 | `report_1` | `report.partition` | `partition` | |
| 189 | `report_2` | `report.offset` | `offset` | |

### 2.8 adapters/queues/sync_adapter.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 274 | `record_1` | `record.retries` | `retries` | |

### 2.9 api/django/middleware/permissions.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 46 | `request_1` | `request.META.get('REMOTE_ADDR')` | `remote_addr` | |
| 95 | `request_1` | `request.path` | `path` | 같은 `request_1` 다른 의미 |

### 2.10 api/django/middleware/self_healing.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 164 | `count_1` | `len(cls.INFRASTRUCTURE_FAILURE_PATHS)` | `infra_paths_count` | |
| 165 | `count_2` | `len(cls.DOMAIN_MAPPING)` | `domain_mapping_count` | |
| 425 | `pool_circuit_breaker_1` | `pool_circuit_breaker._failure_count` | `failure_count` | |

### 2.11 api/django/permissions.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 547 | `approval_request_2` | `approval_request['approved_by']` | `approved_by` | gap: `_1` 없음 |

### 2.12 api/django/pool_circuit_breaker.py (8건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 537 | `pool_status_1` | `pool_status.get('total_capacity')` | `total_capacity` | |
| 552 | `self_2` | `self._failure_threshold` | `failure_threshold` | gap: `self_1` 없음 |
| 622 | `self_1` | `self._success_threshold` | `success_threshold` | |
| 864 | `pool_status_2` | `pool_status.get('total_capacity', '?')` | `total_capacity` | |
| 865 | `pool_status_3` | `pool_status.get('usage_percent', 0)` | `usage_percent` | |
| 866 | `pool_status_4` | `pool_status.get('is_exhausted', False)` | `is_exhausted` | |
| 965 | `pool_status_1` | `pool_status.get('total_capacity', '?')` | `total_capacity` | |
| 966 | `pool_status_2` | `pool_status.get('overflow', '?')` | `overflow` | |

### 2.13 api/django/throttle_adapter.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 99 | `result_1` | `result.current_count` | `current_count` | |

### 2.14 api/django/tiering/registry.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 157 | `snapshot_1` | `snapshot['action']` | `action` | |

### 2.15 api/django/views/dlq.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 63 | `result_3` | `result.success` | `success` | gap: `result_1/2` 없음 |
| 64 | `result_4` | `result.failed` | `failed` | |

### 2.16 api/django/views/l2_storage_drift.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 138 | `result_2` | `result.get('l1_wins', 0)` | `l1_wins` | gap |
| 139 | `result_3` | `result.get('l2_wins', 0)` | `l2_wins` | |
| 194 | `result_3` | `result.get('winner', 'n/a')` | `winner` | 같은 `result_3` 다른 의미 |

### 2.17 api/django/views/l2_storage_shadow_log.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 260 | `result_2` | `result.get('failed', 0)` | `failed` | gap |

### 2.18 api/django/views/xtest/ (4건)

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|---|
| base.py | 365 | `result_1` | `result.memory_percent` | `memory_percent` | |
| rate_limit.py | 215 | `client_status_2` | `client_status['blocked']` | `blocked` | gap |
| replay.py | 396 | `result_2` | `result['success_count']` | `success_count` | gap |
| replay.py | 397 | `result_3` | `result['failed_count']` | `failed_count` | |
| retry.py | 508 | `state_2` | `state.consecutive_429s` | `consecutive_429s` | gap |

### 2.19 celery_tasks/circuit_breaker_tasks.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 45 | `result_1` | `result.get('transitioned', [])` | `transitioned` | |

### 2.20 celery_tasks/dlq_tasks.py (7건)

기존 kwargs 확인:
- L55-60: `service_name=`, `result=result.total` 존재
- L193-198: `result=result.total` 존재
- L260-265: `result=result.total` 존재
- L323-327: `result=result.get('expired_count', 0)` 존재

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 59 | `result_2` | `result.success_count` | `success_count` | gap: `result_1` 없음 |
| 60 | `result_3` | `result.failed_count` | `failed_count` | |
| 196 | `result_1` | `result.success_count` | `success_count` | |
| 197 | `result_2` | `result.failed_count` | `failed_count` | |
| 263 | `result_1` | `result.success_count` | `success_count` | |
| 264 | `result_2` | `result.failed_count` | `failed_count` | |
| 326 | `result_1` | `result.get('archived_count', 0)` | `archived_count` | |

---

## 3. 변환 예시 (Before/After)

### 3.1 pool_circuit_breaker.py L864-866

```python
# Before
logger.warning("pool_circuit_breaker.pool_exhaustion_detected",
    pool_status=pool_name,
    pool_status_2=pool_status.get('total_capacity', '?'),
    pool_status_3=pool_status.get('usage_percent', 0),
    pool_status_4=pool_status.get('is_exhausted', False),
)

# After
logger.warning("pool_circuit_breaker.pool_exhaustion_detected",
    pool_status=pool_name,
    total_capacity=pool_status.get('total_capacity', '?'),
    usage_percent=pool_status.get('usage_percent', 0),
    is_exhausted=pool_status.get('is_exhausted', False),
)
```

### 3.2 kafka/consumer.py L234-235

```python
# Before
logger.info("kafka_consumer.event_committed",
    event=event.topic,
    event_1=event.partition,
    event_2=event.offset,
)

# After
logger.info("kafka_consumer.event_committed",
    event=event.topic,
    partition=event.partition,
    offset=event.offset,
)
```

### 3.3 celery_tasks/dlq_tasks.py L55-60

```python
# Before
logger.info("circuit_recovery_completed",
    service_name=service_name,
    result=result.total,
    result_2=result.success_count,
    result_3=result.failed_count,
)

# After
logger.info("circuit_recovery_completed",
    service_name=service_name,
    result=result.total,
    success_count=result.success_count,
    failed_count=result.failed_count,
)
```

---

## 4. 테스트 영향

```bash
# api/adapter/celery 관련 테스트에서 넘버링 필드 참조 확인
grep -rn "result_[0-9]\|pool_status_[0-9]\|event_[0-9]\|self_[0-9]\|_config_[0-9]\|count_[0-9]" \
    tests/ --include="*.py" | grep -iE "adapter|api|celery|dlq"
```
