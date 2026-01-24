# Configuration Inventory

> 하드코딩된 설정값의 전체 목록 및 분류

## 1. 분류 기준

| 분류 코드 | 설명 | 관리 방식 |
|-----------|------|----------|
| **IMMUTABLE** | 변경 시 호환성 문제 발생, 재배포 필요 | Django Settings / ENV |
| **RUNTIME** | 운영 중 동적 변경 필요 | RuntimeConfigManager |

---

## 2. 불변 설정 (IMMUTABLE)

총 2개 카테고리

### 2.1 시스템 식별자

| 설정명 | 현재 값 | 위치 | 설명 |
|--------|---------|------|------|
| `REDIS_KEY_PREFIX` | `"selfhealing:"` | 다수 파일 | Redis 키 네이밍 규칙 |
| `METRICS_PREFIX` | `"selfhealing_"` | metrics 관련 파일 | 메트릭 이름 접두사 |

### 2.2 스키마/프로토콜 버전

| 설정명 | 현재 값 | 위치 | 설명 |
|--------|---------|------|------|
| `CONFIG_VERSION` | `"1.0"` 등 | 설정 관련 파일 | 설정 스키마 버전 |
| `API_VERSION` | `"v1"` | API 뷰 파일 | API 버전 |

---

## 3. 가변 설정 (RUNTIME) - 카테고리별 분류

총 9개 카테고리, 약 200개 이상 설정값

### 3.1 TTL (Time-To-Live) 관련

**용도**: 캐시, 토큰, 세션 만료 시간

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `CACHE_TTL_SECONDS` | `30` | dashboard_service.py | HIGH |
| `CACHE_TTL_STATUS` | `15` | dashboard_service.py | HIGH |
| `CACHE_TTL_ACTIVITY` | `60` | dashboard_service.py | MEDIUM |
| `DEFAULT_CACHE_TTL` | 다양 | idempotency_service.py | HIGH |
| `EXTENDED_CACHE_TTL` | 다양 | idempotency_service.py | MEDIUM |
| `_cache_ttl_seconds` | `5.0` | health_penalty.py | MEDIUM |
| `CACHE_TTL_SECONDS` | `30.0` | tracker.py | MEDIUM |
| `token_ttl` | `3600` | 인증 관련 | MEDIUM |
| `key_ttl` | `86400` | 암호화 관련 | LOW |

### 3.2 Timeout 관련

**용도**: 작업 시간 제한, 연결 타임아웃

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `recovery_timeout` | `30-60` | circuit_breaker.py | HIGH |
| `connection_timeout` | `5` | 네트워크 어댑터 | HIGH |
| `request_timeout` | `30` | API 호출 | HIGH |
| `lock_timeout` | `10` | 분산 락 | MEDIUM |
| `operation_timeout` | `300` | 장기 작업 | MEDIUM |

### 3.3 Interval 관련

**용도**: 주기적 작업 간격

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `health_check_interval` | `5-30` | 헬스체크 | HIGH |
| `sync_interval` | `60` | 동기화 작업 | MEDIUM |
| `cleanup_interval` | `300-3600` | 정리 작업 | LOW |
| `metrics_flush_interval` | `10` | 메트릭 전송 | MEDIUM |
| `retry_interval` | `1-5` | 재시도 간격 | HIGH |

### 3.4 Threshold/Limit 관련

**용도**: 임계값, 제한값

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `failure_threshold` | `5` | circuit_breaker.py | CRITICAL |
| `success_threshold` | `3` | circuit_breaker.py | CRITICAL |
| `error_rate_threshold` | `0.5` | error_budget 관련 | CRITICAL |
| `rate_limit` | `100-1000` | throttle 관련 | HIGH |
| `max_pending_count` | `1000` | pending 관련 | MEDIUM |
| `max_queue_size` | `10000` | 큐 관련 | MEDIUM |
| `memory_limit_mb` | `512` | 메모리 제한 | MEDIUM |

### 3.5 Count/Size 관련

**용도**: 배치 크기, 최대 개수

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `batch_size` | `100` | 다수 파일 | HIGH |
| `page_size` | `20` | API 뷰 | MEDIUM |
| `max_retries` | `3-5` | 재시도 로직 | HIGH |
| `buffer_size` | `1000` | 버퍼 관련 | MEDIUM |
| `MAX_HISTORY` | `100` | pending_config.py | LOW |
| `BATCH_SIZE` | `10` | async_logger.py | MEDIUM |

### 3.6 API View 파라미터

**용도**: 기본 페이징, 필터링

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `default_limit` | `100` | API 뷰 | MEDIUM |
| `default_offset` | `0` | API 뷰 | LOW |
| `max_limit` | `1000` | API 뷰 | MEDIUM |
| `default_order` | `"-created_at"` | API 뷰 | LOW |

### 3.7 Celery Task 설정

**용도**: 백그라운드 작업 파라미터

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `max_retries` | `3` | task 데코레이터 | HIGH |
| `default_retry_delay` | `60` | task 데코레이터 | HIGH |
| `time_limit` | `300` | task 데코레이터 | MEDIUM |
| `soft_time_limit` | `240` | task 데코레이터 | MEDIUM |
| `rate_limit` | `"10/s"` | task 데코레이터 | MEDIUM |

### 3.8 Recovery/Healing 설정

**용도**: 자동 복구 관련 파라미터

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `max_recovery_attempts` | `3` | recovery 관련 | CRITICAL |
| `recovery_cooldown` | `300` | recovery 관련 | CRITICAL |
| `anti_flapping_window` | `60` | anti_flapping.py | CRITICAL |
| `min_stability_period` | `120` | stability 관련 | HIGH |
| `escalation_threshold` | `5` | escalation 관련 | HIGH |

### 3.9 Audit/WAL 설정

**용도**: 감사 로그, WAL 관련

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `wal_flush_interval` | `1` | WAL 관련 | HIGH |
| `wal_max_size` | `10000` | WAL 관련 | MEDIUM |
| `audit_retention_days` | `90` | audit 관련 | LOW |
| `max_audit_batch` | `100` | audit 관련 | MEDIUM |

---

## 4. 우선순위 기준

| 우선순위 | 기준 | 마이그레이션 순서 |
|----------|------|------------------|
| **CRITICAL** | 장애 발생 시 즉시 조정 필요 | Phase 1 |
| **HIGH** | 성능/안정성에 직접 영향 | Phase 2 |
| **MEDIUM** | 운영 효율성에 영향 | Phase 3 |
| **LOW** | 변경 빈도 낮음 | Phase 4 |

---

## 5. 파일별 하드코딩 분포

### 5.1 가장 많은 하드코딩이 있는 파일 (Top 10)

| 순위 | 파일 | 하드코딩 수 | 주요 설정 |
|------|------|-------------|----------|
| 1 | `circuit_breaker.py` | 15+ | threshold, timeout, interval |
| 2 | `dashboard_service.py` | 10+ | TTL, cache 관련 |
| 3 | `idempotency_service.py` | 10+ | TTL, cache 관련 |
| 4 | `anti_flapping.py` | 8+ | window, threshold |
| 5 | `recovery_coordinator.py` | 8+ | timeout, threshold |
| 6 | `async_logger.py` | 6+ | batch_size, interval |
| 7 | `tracker.py` | 6+ | TTL, count |
| 8 | `health_penalty.py` | 5+ | TTL, threshold |
| 9 | `pending_config.py` | 5+ | MAX_HISTORY, TTL |
| 10 | `propagator.py` | 5+ | timeout, interval |

### 5.2 디렉토리별 분포

| 디렉토리 | 하드코딩 수 | 주요 카테고리 |
|----------|-------------|--------------|
| `services/` | 80+ | TTL, threshold, batch_size |
| `api/django/views/` | 30+ | limit, offset, page_size |
| `tasks/` | 20+ | max_retries, time_limit |
| `adapters/` | 15+ | timeout, connection |
| `audit/` | 10+ | retention, batch |
| `utils/` | 10+ | buffer, interval |

---

---

## 6. 추가 발견된 카테고리 (2차 분석)

### 6.1 Precomputed Cache 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `L1_TTL_SECONDS` | `2.0` | precomputed_cache.py#L66 | HIGH |
| `L2_TTL_SECONDS` | `15.0` | precomputed_cache.py#L67 | HIGH |
| `REFRESH_INTERVAL` | `10.0` | precomputed_cache.py#L68 | MEDIUM |

### 6.2 Recovery Circuit Breaker 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `error_rate_threshold` | `0.15` | recovery_circuit_breaker.py#L62 | CRITICAL |
| `sampling_window_seconds` | `60` | recovery_circuit_breaker.py#L66 | HIGH |
| `min_samples` | `10` | recovery_circuit_breaker.py#L70 | MEDIUM |
| `open_duration_seconds` | `300` | recovery_circuit_breaker.py#L74 | HIGH |
| `half_open_max_requests` | `5` | recovery_circuit_breaker.py#L78 | MEDIUM |
| `max_consecutive_trips` | `3` | recovery_circuit_breaker.py#L82 | HIGH |

### 6.3 Anti-Flapping 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `min_stable_duration_before_recovery_seconds` | `600` | anti_flapping.py#L64 | CRITICAL |
| `max_level_transitions_per_hour` | `3` | anti_flapping.py#L68 | CRITICAL |

### 6.4 Regional Recovery Policy 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `error_rate_threshold` | `0.10` | regional_recovery_policy.py#L57 | CRITICAL |
| `success_rate_threshold` | `0.95` | regional_recovery_policy.py#L60 | CRITICAL |
| `stability_check_duration_minutes` | `10` | regional_recovery_policy.py#L54 | HIGH |
| `notification_intervals` | `[15, 30, 60]` | regional_recovery_policy.py#L92 | MEDIUM |
| `DEFAULT_REGIONAL_CONFIGS` | 4개 리전 설정 | regional_recovery_policy.py#L139 | HIGH |

### 6.5 Recovery Shutdown 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `default_drain_timeout_seconds` | `30.0` | recovery_shutdown.py#L46 | HIGH |
| `recovery_extension_seconds` | `300.0` | recovery_shutdown.py#L51 | HIGH |
| `max_shutdown_wait_seconds` | `600.0` | recovery_shutdown.py#L56 | HIGH |
| `recovery_check_interval_seconds` | `5.0` | recovery_shutdown.py#L61 | MEDIUM |
| `log_interval_seconds` | `15.0` | recovery_shutdown.py#L64 | LOW |

### 6.6 Cascade Auditor/Config 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `MAX_INDEX_SIZE` | `10000` | cascade_auditor.py#L98 | MEDIUM |
| `max_chain_depth` | `10` | cascade_config.py#L43 | HIGH |
| `hot_max_count` | `10000` | cascade_config.py#L167 | MEDIUM |
| `max_events_per_second` | `1000` | cascade_config.py#L288 | HIGH |
| `buffer_warning_threshold` | `0.7` | cascade_config.py#L274 | HIGH |
| `buffer_critical_threshold` | `0.9` | cascade_config.py#L281 | HIGH |
| `_rate_window_seconds` | `1.0` | cascade_load_shedding.py#L135 | MEDIUM |

### 6.7 Recovery Dashboard 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_STALE_THRESHOLD_MINUTES` | `30` | recovery_dashboard.py#L310 | MEDIUM |
| `DEFAULT_MAX_REGIONAL_STATUS` | `5` | recovery_dashboard.py#L313 | LOW |

### 6.8 Override TTL / Accountability 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `default_override_ttl_minutes` | `120` | models.py#L175 | MEDIUM |
| `max_override_ttl_minutes` | `480` | models.py#L182 | MEDIUM |
| `auto_restore_after_hours` | `8.0` | models.py#L347 | MEDIUM |
| `acknowledgement_roles` | `["super_admin"]` | models.py#L194 | LOW |
| `escalation_channels` | `["slack", "pagerduty"]` | models.py#L351 | LOW |

### 6.9 Distributed Recovery Lock 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_LOCK_TIMEOUT` | `30분` | distributed_recovery_lock.py#L92 | HIGH |

### 6.10 Redis Key Guard 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `is_warning threshold` | `80.0` | redis_key_guard.py#L94 | HIGH |
| `is_critical threshold` | `90.0` | redis_key_guard.py#L98 | HIGH |
| `target_free_percent` | `20.0` | redis_key_guard.py#L415 | MEDIUM |
| `volatile_key_ttl` | `3600, 7200, 604800` | redis_key_guard.py#L156 | MEDIUM |

### 6.11 Recovery Tasks 설정 (추가)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_TRIGGER_CHECK_INTERVAL` | `60` | recovery_tasks.py#L54 | HIGH |
| `DEFAULT_HEALTH_MONITOR_INTERVAL` | `30` | recovery_tasks.py#L57 | HIGH |
| `DEFAULT_STALE_CHECK_INTERVAL` | `10` | recovery_tasks.py#L60 | MEDIUM |
| `default_retry_delay` | `60, 30, 15` | recovery_tasks.py#L71 | HIGH |
| `max_age_hours` (cleanup) | `168` | recovery_tasks.py#L581 | LOW |

### 6.12 Chaos Blast Radius 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `instance_max_concurrent` | `5` | chaos/blast_radius.py#L65 | HIGH |
| `service_max_concurrent` | `2` | chaos/blast_radius.py#L68 | HIGH |
| `region_max_concurrent` | `1` | chaos/blast_radius.py#L71 | CRITICAL |
| `max_traffic_percent_instance` | `100.0` | chaos/blast_radius.py#L95 | MEDIUM |
| `max_traffic_percent_service` | `50.0` | chaos/blast_radius.py#L98 | HIGH |
| `max_traffic_percent_region` | `10.0` | chaos/blast_radius.py#L101 | CRITICAL |

### 6.13 Security Notification 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DESCRIPTION_MAX_LENGTH` | `500` | security_notification/models.py#L40 | LOW |
| `ACTION_TAKEN_MAX_LENGTH` | `200` | security_notification/models.py#L41 | LOW |
| `TITLE_MAX_LENGTH` | `150` | security_notification/models.py#L42 | LOW |

### 6.14 Config History 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `MAX_HISTORY_ENTRIES` | `50` | config_history.py#L87 | LOW |

### 6.15 Snapshot Storage 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_FILENAME` | `"last_known_metrics.json"` | snapshot_storage.py#L130 | LOW |
| `DEFAULT_MAX_AGE` | `3600` | snapshot_storage.py#L131 | MEDIUM |

### 6.16 Layered Repository 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `sync_interval_seconds` | `5.0` | layered_repository.py#L79 | MEDIUM |
| `max_workers` | `4` | layered_repository.py#L73 | LOW |

### 6.17 Task Queue Interface 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `retry_backoff_max` | `600` | task_queue.py#L130 | MEDIUM |

### 6.18 XTest Views 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `_max_events` | `500` | xtest/base.py#L135 | LOW |
| `_max_incidents` | `100` | xtest/base.py#L136 | LOW |
| `max_injection` | `100` | xtest/error_budget.py#L47 | LOW |

### 6.19 Jitter 유틸리티 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `max_delay_seconds` | `60.0` | jitter.py#L27 | MEDIUM |
| `min_delay_seconds` | `0.0` | jitter.py#L28 | LOW |

### 6.20 Recovery Coordinator 설정 (추가)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_RECOVERY_STEPS` | 레벨별 스텝 | recovery_coordinator.py#L99 | HIGH |
| `duration_minutes` | `5, 3, 2` | recovery_coordinator.py#L112 | HIGH |

### 6.21 Regional Gate 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `duration_seconds` (기본) | `300` | regional_gate.py#L139 | HIGH |

### 6.22 Idempotent Step Handler 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `IDEMPOTENCY_KEY_TTL_HOURS` | `24` | idempotent_step_handlers.py#L48 | MEDIUM |
| `EXECUTION_TIMEOUT_MINUTES` | `30` | idempotent_step_handlers.py#L50 | HIGH |

### 6.23 Recovery Tasks 스케줄 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_TRIGGER_CHECK_INTERVAL` | `60` (초) | recovery_tasks.py#L54 | HIGH |
| `DEFAULT_HEALTH_MONITOR_INTERVAL` | `30` (초) | recovery_tasks.py#L57 | HIGH |
| `DEFAULT_STALE_CHECK_INTERVAL` | `10` (분) | recovery_tasks.py#L60 | MEDIUM |
| `default_retry_delay` | `60`, `30`, `15` | recovery_tasks.py#L71, #L234, #L388 | MEDIUM |
| `max_retries` | `3` | recovery_tasks.py#L69 | MEDIUM |

### 6.24 Notification Policy 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `cooldown_seconds` | `300` (5분) | notification_policy.py#L100 | MEDIUM |
| `default_channels` | `["slack"]` | notification_policy.py#L102 | LOW |

### 6.25 Task Cooldown 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `cooldown_seconds` (intelligence) | `3600` (1시간) | intelligence_tasks.py#L57, #L154 | MEDIUM |
| `cooldown_seconds` (analysis) | `120` (2분) | intelligence_tasks.py#L440 | MEDIUM |
| `cooldown_seconds` (replay) | `300` (5분) | traffic_aware_replay.py#L186 | MEDIUM |

### 6.26 RingBuffer 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `capacity` (기본) | `10000` | ring_buffer.py#L8 | MEDIUM |
| `max_size` (batch) | `100` | ring_buffer.py#L10 | LOW |
| `strategy` (기본) | `DROP_OLDEST` | ring_buffer.py#L26 | HIGH |

### 6.27 AsyncLogger 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `BATCH_SIZE` | `10` | async_logger.py#L57 | MEDIUM |
| `FLUSH_INTERVAL` | `5.0` (초) | async_logger.py#L58 | MEDIUM |
| `IMMEDIATE_SEVERITIES` | `{CRITICAL}` | async_logger.py#L59 | LOW |

### 6.28 Error Budget Multiplier 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_CACHE_TTL_SECONDS` | `30.0` | multiplier.py#L55 | MEDIUM |
| `target_multiplier` | `1.0` | recovery_coordinator.py#L105, #L135, #L159 | HIGH |

### 6.29 Celery Signal Hooks 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `SELFHEALING_CB_FAILURE_THRESHOLD` | `5` | signal_hooks.py#L26 | HIGH |
| `SELFHEALING_CB_RECOVERY_TIMEOUT` | `60` (초) | signal_hooks.py#L27 | HIGH |
| `max_items` (conditional_replay) | `50` | signal_hooks.py#L647 | MEDIUM |

### 6.30 Pending Recovery Approval 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `timeout_minutes` | `60` | pending_recovery_approval.py#L89 | HIGH |
| `reminder_intervals_minutes` | `[15, 30, 60]` | pending_recovery_approval.py#L230 | MEDIUM |

### 6.31 Distributed Lock 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_LOCK_TIMEOUT` | `30` (분) | distributed_recovery_lock.py#L92 | CRITICAL |

### 6.32 Recovery Dashboard 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_STALE_THRESHOLD_MINUTES` | `30` | recovery_dashboard.py#L310 | MEDIUM |

### 6.33 Unified Notification 채널 매핑

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `CRITICAL 채널` | `["slack", "email", "sms", "pagerduty"]` | unified_notification.py#L150 | HIGH |
| `HIGH 채널` | `["slack", "email"]` | unified_notification.py#L151 | MEDIUM |
| `MEDIUM 채널` | `["slack"]` | unified_notification.py#L152 | LOW |
| `escalation_channels` (기본) | `["slack", "pagerduty"]` | models.py#L351 | HIGH |

### 6.34 Retry Task Delay 계산

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `min_delay` (exponential backoff) | `30` (초) | recovery_tasks.py#L325 | MEDIUM |
| `max_delay` | `300` (5분) | recovery_tasks.py#L325 | MEDIUM |
| `backoff_multiplier` | `2` | recovery_tasks.py#L325 | LOW |

### 6.35 Governance 시간 임계값

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `warning_hours` | `4` (시간) | governance.py#L47 | HIGH |
| `final_warning_hours` | `6` (시간) | governance.py#L48 | HIGH |
| `auto_restore_hours` | `8` (시간) | governance.py#L49 | CRITICAL |

### 6.36 LayeredRepository Timeout 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `sync_interval_seconds` | `5.0` | layered_repository.py#L79 | MEDIUM |
| 기본 timeout (circuit_breaker) | `0.1` (초) | layered_repository.py#L136 | HIGH |
| 기본 timeout (dlq) | `0.2` (초) | layered_repository.py#L137 | HIGH |
| 기본 timeout (retry) | `0.15` (초) | layered_repository.py#L138 | HIGH |
| 초기 로드 timeout multiplier | `2x` | layered_repository.py#L151 | MEDIUM |

---

## 7. 추가 발견된 카테고리 (4차 분석)

### 7.1 Throttle Config 설정 (Netflix Gradient)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `initial_limit` | `100` | throttle/config.py#L18 | HIGH |
| `window_seconds` | `60` | throttle/config.py#L19 | MEDIUM |
| `min_limit` | `10` | throttle/config.py#L22 | HIGH |
| `max_limit` | `500` | throttle/config.py#L23 | HIGH |
| `sample_interval_ms` | `500` | throttle/config.py#L26 | MEDIUM |
| `smoothing_factor` | `0.5` | throttle/config.py#L27 | LOW |
| `decrease_ratio` | `0.9` | throttle/config.py#L30 | MEDIUM |
| `increase_step` | `1` | throttle/config.py#L31 | LOW |
| `sla_warning_ms` | `200` | throttle/config.py#L34 | HIGH |
| `sla_critical_ms` | `500` | throttle/config.py#L35 | HIGH |
| `emergency_limit` | `10` | throttle/config.py#L38 | CRITICAL |
| `key_prefix` | `"selfhealing:throttle"` | throttle/config.py#L41 | LOW |

### 7.2 DLQ Config 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `enabled` | `True` | dlq_models.py#L24 | HIGH |
| `retention_days` | `30` | dlq_models.py#L25 | MEDIUM |
| `max_replay_attempts` | `2` | dlq_models.py#L26 | HIGH |
| `max_retries` | `3` | dlq_models.py#L27 | HIGH |
| `retry_delay` | `60` | dlq_models.py#L28 | MEDIUM |
| `expiry_hours` | `72` | dlq_models.py#L29 | MEDIUM |
| `batch_size` | `10` | dlq_models.py#L30 | HIGH |

### 7.3 Notification Limits 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `slack_block_text_limit` | `3000` | config.py#L54 | MEDIUM |
| `description_max_length` | `500` | config.py#L57 | LOW |
| `action_taken_max_length` | `200` | config.py#L58 | LOW |
| `title_max_length` | `150` | config.py#L59 | LOW |
| `notification_timeout_seconds` | `10` | config.py#L62 | HIGH |

### 7.4 Forensic Settings 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `max_stack_frames` | `50` | config.py#L71 | MEDIUM |
| `max_stacktrace_length` | `10000` | config.py#L72 | MEDIUM |
| `max_context_size_bytes` | `65536` (64KB) | config.py#L75 | MEDIUM |
| `user_agent_max_length` | `500` | safe_defaults.py#L101 | LOW |

### 7.5 Safe Defaults 마스터 설정 (core/safe_defaults.py)

#### 7.5.1 Security Defaults

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `rate_limit_max_requests` | `100` | safe_defaults.py#L90 | HIGH |
| `temporary_ban_hours` | `1` | safe_defaults.py#L91 | MEDIUM |
| `permanent_ban_threshold` | `5` | safe_defaults.py#L92 | MEDIUM |
| `suspicious_ip_cache_timeout` | `86400` | safe_defaults.py#L93 | LOW |
| `injection_ban_hours` | `24` | safe_defaults.py#L94 | MEDIUM |
| `failed_login_threshold` | `5` | safe_defaults.py#L95 | MEDIUM |

#### 7.5.2 Logging Defaults

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `dlq_log_level` | `"INFO"` | safe_defaults.py#L108 | LOW |
| `circuit_breaker_log_level` | `"INFO"` | safe_defaults.py#L109 | LOW |
| `emergency_log_level` | `"WARNING"` | safe_defaults.py#L112 | LOW |
| `structured_json` | `True` | safe_defaults.py#L120 | LOW |

#### 7.5.3 Metrics Defaults

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `collection_interval` | `60` | safe_defaults.py#L139 | MEDIUM |
| `jitter_max_delay_seconds` | `60.0` | safe_defaults.py#L142 | MEDIUM |

#### 7.5.4 Error Budget Defaults (Google SRE)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `threshold_healthy` | `75.0` | safe_defaults.py#L146 | CRITICAL |
| `threshold_caution` | `50.0` | safe_defaults.py#L147 | CRITICAL |
| `threshold_warning` | `20.0` | safe_defaults.py#L148 | CRITICAL |
| `threshold_critical` | `0.0` | safe_defaults.py#L149 | CRITICAL |
| `burn_rate_fast_critical` | `14.4` | safe_defaults.py#L150 | HIGH |
| `burn_rate_fast_warning` | `6.0` | safe_defaults.py#L151 | HIGH |
| `burn_rate_slow_warning` | `3.0` | safe_defaults.py#L152 | MEDIUM |
| `burn_rate_slow_info` | `1.0` | safe_defaults.py#L153 | LOW |
| `failsafe_cooldown_seconds` | `300` | safe_defaults.py#L155 | MEDIUM |
| `heartbeat_interval_seconds` | `60` | safe_defaults.py#L157 | MEDIUM |
| `heartbeat_timeout_seconds` | `120` | safe_defaults.py#L158 | MEDIUM |

#### 7.5.5 Idempotency Defaults

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `default_cache_ttl` | `60` | safe_defaults.py#L164 | HIGH |
| `extended_cache_ttl` | `300` | safe_defaults.py#L165 | MEDIUM |
| `short_cache_ttl` | `60` | safe_defaults.py#L166 | MEDIUM |
| `clock_skew_tolerance_seconds` | `5.0` | safe_defaults.py#L167 | LOW |

#### 7.5.6 Chaos Engineering Defaults

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `max_blast_radius` | `0.05` (5%) | safe_defaults.py#L173 | CRITICAL |
| `failure_rate` | `0.01` (1%) | safe_defaults.py#L175 | HIGH |
| `latency_max_ms` | `1000` | safe_defaults.py#L176 | MEDIUM |

#### 7.5.7 Emergency Defaults

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `gradual_recovery_steps` | `5` | safe_defaults.py#L182 | HIGH |
| `recovery_step_duration_seconds` | `60` | safe_defaults.py#L183 | HIGH |

#### 7.5.8 Governance Defaults

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `approval_timeout_hours` | `24` | safe_defaults.py#L188 | HIGH |
| `max_approval_retries` | `3` | safe_defaults.py#L189 | MEDIUM |
| `threshold_operator` | `0.15` (15%) | safe_defaults.py#L190 | HIGH |
| `threshold_admin` | `0.30` (30%) | safe_defaults.py#L191 | HIGH |
| `emergency_expiry_hours` | `4` | safe_defaults.py#L192 | MEDIUM |
| `audit_log_retention_days` | `90` | safe_defaults.py#L193 | LOW |

#### 7.5.9 L2 Storage Defaults

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `redis_timeout_ms` | `1000` | safe_defaults.py#L199 | HIGH |
| `reconciliation_interval_seconds` | `300` | safe_defaults.py#L203 | MEDIUM |
| `reconciliation_jitter_percent` | `20` | safe_defaults.py#L204 | LOW |
| `max_retry_on_failure` | `3` | safe_defaults.py#L205 | MEDIUM |
| `connection_pool_size` | `10` | safe_defaults.py#L206 | MEDIUM |

#### 7.5.10 Drift Threshold Defaults

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `warning_percent` | `5.0` | safe_defaults.py#L212 | HIGH |
| `critical_percent` | `20.0` | safe_defaults.py#L213 | HIGH |
| `check_interval_seconds` | `60` | safe_defaults.py#L214 | MEDIUM |
| `window_size_seconds` | `300` | safe_defaults.py#L215 | MEDIUM |
| `min_samples_required` | `10` | safe_defaults.py#L216 | MEDIUM |
| `suppress_duplicate_alerts_seconds` | `300` | safe_defaults.py#L218 | LOW |

### 7.6 Cascade Event Priority Mapping

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `EMERGENCY_LEVEL_CHANGED` | `CRITICAL` | cascade_event.py#L91 | LOW |
| `MANUAL_INTERVENTION` | `CRITICAL` | cascade_event.py#L92 | LOW |
| `CIRCUIT_BREAKER_OPENED` | `CRITICAL` | cascade_event.py#L94 | LOW |
| `CANARY_ROLLBACK` | `HIGH` | cascade_event.py#L97 | LOW |
| `METRICS_UPDATED` | `LOW` | cascade_event.py#L107 | LOW |
| `HEALTH_CHECK` | `LOW` | cascade_event.py#L108 | LOW |

### 7.7 Cascade Event Version

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `version` (default) | `"1.0"` | cascade_event.py#L475 | LOW |
| `checkpoint version` | `"1.0"` | cascade_auditor.py#L552 | LOW |

---

## 8. 추가 발견된 카테고리 (5차 분석)

### 8.1 SLO (Service Level Objective) 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `window_days` | `30` | slo.py#L84 | HIGH |
| `fast_burn_rate` | `14.4` | slo.py#L95 | CRITICAL |
| `slow_burn_rate` | `3.0` | slo.py#L96 | CRITICAL |
| `warning_threshold` | `(1 + target) / 2` | slo.py#L100 | HIGH |
| `critical_threshold` | `target + 0.001` | slo.py#L104 | HIGH |

### 8.2 Rate Limit Coordinator 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `base_delay` | `1.0` (초) | rate_limit_coordinator.py#L46 | HIGH |
| `max_delay` | `60.0` (초) | rate_limit_coordinator.py#L49 | HIGH |
| `jitter_percent` | `30.0` | rate_limit_coordinator.py#L52 | MEDIUM |
| `default_retry_after` | `5.0` (초) | rate_limit_coordinator.py#L55 | HIGH |
| `backoff_multiplier` | `2.0` | rate_limit_coordinator.py#L54 | MEDIUM |

### 8.3 Recovery Circuit Breaker 설정 (확장)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `re_escalation_enabled` | `True` | recovery_circuit_breaker.py#L86 | HIGH |
| `re_escalation_level` | `"LEVEL_3"` | recovery_circuit_breaker.py#L90 | CRITICAL |
| `half_open_max_requests` | `5` | recovery_circuit_breaker.py#L78 | MEDIUM |

### 8.4 Anti-Flapping 설정 (확장)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `EMERGENCY_LEVEL_COOLDOWN_SECONDS` | `300` (5분) | anti_flapping.py#L31 | CRITICAL |
| `cooldown_after_recovery_seconds` | `600` (10분) | anti_flapping.py#L60 | CRITICAL |
| `min_stable_duration_before_recovery_seconds` | `600` | anti_flapping.py#L64 | CRITICAL |
| `max_level_transitions_per_hour` | `3` | anti_flapping.py#L68 | CRITICAL |
| `flapping_lockout_minutes` | `30` | anti_flapping.py#L72 | HIGH |
| `recovery_hysteresis_factor` | `1.15` (15%) | anti_flapping.py#L76 | HIGH |

### 8.5 Recovery Accountability 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `ready_to_restore_timeout_hours` | `4.0` | models.py#L338 | HIGH |
| `acknowledgement_required_roles` | `["admin", "sre_lead"]` | models.py#L343 | MEDIUM |
| `auto_restore_after_hours` | `8.0` | models.py#L347 | HIGH |
| `escalation_channels` | `["slack", "pagerduty"]` | models.py#L351 | MEDIUM |

### 8.6 Regional Recovery Policy 설정 (확장)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `approval_timeout_minutes` | `60` | regional_recovery_policy.py#L89 | HIGH |
| `approval_escalation_intervals` | `[15, 30, 60]` (분) | regional_recovery_policy.py#L92 | MEDIUM |
| `priority` (기본) | `0` | regional_recovery_policy.py#L97 | LOW |

### 8.7 Resilient Recorder 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `buffer_capacity` | `10000` | resilient_recorder.py#L63 | MEDIUM |
| `flush_interval_seconds` | `1.0` | resilient_recorder.py#L67 | MEDIUM |
| `flush_batch_size` | `100` | resilient_recorder.py#L68 | MEDIUM |
| `circuit_failure_threshold` | `3` | resilient_recorder.py#L71 | HIGH |
| `circuit_success_threshold` | `2` | resilient_recorder.py#L72 | HIGH |
| `circuit_timeout_seconds` | `30.0` | resilient_recorder.py#L73 | HIGH |

### 8.8 Recovery Shutdown 설정 (확장)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `recovery_check_interval_seconds` | `5.0` | recovery_shutdown.py#L61 | MEDIUM |
| `log_interval_seconds` | `15.0` | recovery_shutdown.py#L64 | LOW |
| `allow_force_shutdown` | `True` | recovery_shutdown.py#L67 | HIGH |

### 8.9 Metrics Histogram Bucket 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `retry_attempts_buckets` | `(1,2,3,4,5,6,7,8,9,10)` | metrics/definitions.py | LOW |
| `recovery_time_seconds_buckets` | `(60,300,900,1800,3600,7200,14400,28800,86400)` | metrics/definitions.py | LOW |
| `human_review_queue_time_buckets` | `(300,900,1800,3600,7200,14400,28800)` | metrics/definitions.py | LOW |
| `circuit_breaker_open_duration_buckets` | `(60,300,600,1800,3600,7200)` | metrics/definitions.py | LOW |
| `RECOVERY_DURATION_SECONDS_buckets` | `[30,60,120,300,600,1200,1800,3600]` | recovery_metrics.py#L89 | LOW |
| `RECOVERY_STEP_DURATION_buckets` | `[1,5,10,30,60,120,300,600]` | recovery_metrics.py#L97 | LOW |

### 8.10 Notification Policy 설정 (확장)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `cooldown_seconds` (기본) | `300` (5분) | notification_policy.py#L100 | MEDIUM |
| `default_severity` | `"info"` | notification_policy.py#L101 | LOW |
| `escalate_on_emergency` | `True` | notification_policy.py#L104 | MEDIUM |

---

## 9. 6차 분석 결과 (Error Budget, Corruption Shield, Chaos SLA)

### 9.1 Error Budget Propagation 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_PROPAGATION_DECAY` | `0.5` | error_budget/constants.py#L80 | CRITICAL |
| `DEFAULT_PROPAGATION_MAX_HOPS` | `3` | error_budget/constants.py#L81 | CRITICAL |
| `base_multiplier` | `5.0` | error_budget/propagation.py#L70 | CRITICAL |
| `decay_per_hop` | `0.5` | error_budget/propagation.py#L71 | CRITICAL |
| `min_multiplier` | `1.0` | error_budget/propagation.py#L72 | HIGH |
| `max_hops` | `3` | error_budget/propagation.py#L73 | CRITICAL |
| `dampening` | `0.5` | chaos/impact_predictor.py#L45 | HIGH |

### 9.2 Domain Sensitivity Weights (도메인별 민감도 가중치)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_DOMAIN_SENSITIVITY["payment"]` | `10.0` | error_budget/constants.py#L109 | CRITICAL |
| `DEFAULT_DOMAIN_SENSITIVITY["order"]` | `5.0` | error_budget/constants.py#L110 | CRITICAL |
| `DEFAULT_DOMAIN_SENSITIVITY["inventory"]` | `3.0` | error_budget/constants.py#L111 | HIGH |
| `DEFAULT_DOMAIN_SENSITIVITY["notification"]` | `1.5` | error_budget/constants.py#L112 | MEDIUM |
| `DEFAULT_DOMAIN_SENSITIVITY["analytics"]` | `1.0` | error_budget/constants.py#L113 | LOW |

### 9.3 Emergency Level Multipliers (비상 레벨 승수)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_LEVEL_MULTIPLIERS["NORMAL"]` | `1.0` | error_budget/constants.py#L100 | HIGH |
| `DEFAULT_LEVEL_MULTIPLIERS["LEVEL_1"]` | `1.5` | error_budget/constants.py#L101 | HIGH |
| `DEFAULT_LEVEL_MULTIPLIERS["LEVEL_2"]` | `3.0` | error_budget/constants.py#L102 | HIGH |
| `DEFAULT_LEVEL_MULTIPLIERS["LEVEL_3"]` | `5.0` | error_budget/constants.py#L103 | CRITICAL |

### 9.4 Error Budget Refund 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_REFUND_RATIO` | `0.5` (50%) | error_budget/constants.py#L83 | HIGH |
| `REFUND_PROPOSAL_EXPIRY_HOURS` | `24` | error_budget/constants.py#L84 | HIGH |
| `DEFAULT_COMBINE_STRATEGY` | `"max"` | error_budget/constants.py#L122 | MEDIUM |

### 9.5 Corruption Shield 이상치 탐지 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `z_score_threshold` | `3.0` | corruption_shield/config.py#L33 | CRITICAL |
| `iqr_multiplier` | `1.5` | corruption_shield/config.py#L34 | CRITICAL |
| `min_samples_for_anomaly` | `10` | corruption_shield/config.py#L35 | HIGH |
| `max_string_length` | `1000` | corruption_shield/config.py#L40 | MEDIUM |
| `min_amount` | `100` (원) | corruption_shield/config.py#L45 | HIGH |
| `max_amount` | `100_000_000` (1억 원) | corruption_shield/config.py#L46 | CRITICAL |
| `anomaly_limit` | `5` | forensic_audit_bridge.py#L78 | MEDIUM |

### 9.6 Chaos Experiment SLA 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `grace_period_seconds` | `300` (5분) | chaos/base/models.py#L48 | CRITICAL |
| `sla_breach_threshold_percent` | `1.0` (1%) | chaos/base/models.py#L52 | CRITICAL |
| `ttl_seconds` (default) | `600` (10분) | chaos/base/models.py#L55 | HIGH |
| `max_traffic_percent_instance` | `100.0` | chaos/base/models.py#L30 | MEDIUM |
| `max_traffic_percent_service` | `50.0` | chaos/base/models.py#L31 | HIGH |
| `max_traffic_percent_region` | `10.0` | chaos/base/models.py#L32 | CRITICAL |
| `MAX_CHAOS_WEIGHT_MULTIPLIER` | `10.0` | finops/service.py#L35 | HIGH |

### 9.7 Audit Integrity Sequence TTL 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_PENDING_TTL_SECONDS` | `30` | audit/integrity/sequence.py#L42 | HIGH |
| `DEFAULT_ORPHAN_TTL_SECONDS` | `86400` (24시간) | audit/integrity/sequence.py#L43 | CRITICAL |

### 9.8 Audit Cold Storage 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `ARCHIVE_THRESHOLD_DAYS` | `7` | audit/integrity/cold_storage.py#L234 | HIGH |
| `DEFAULT_COLD_RETENTION_YEARS` | `7` | audit/integrity/cold_storage.py#L235 | CRITICAL |

### 9.9 Audit General Configuration 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `retention_days` | `365` (1년) | audit/config.py#L45 | CRITICAL |
| `integrity_check_interval` | `3600` (1시간) | audit/config.py#L50 | HIGH |
| `batch_size` | `100` | audit/config.py#L55 | MEDIUM |
| `batch_flush_interval` | `10` (초) | audit/config.py#L56 | MEDIUM |
| `hash_chain_lock_timeout` | `5.0` (초) | audit/config.py#L60 | HIGH |

### 9.10 Min Samples 설정 (다중 위치)

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `min_samples` | `10` | recovery_circuit_breaker.py#L65 | HIGH |
| `min_samples` | `3` | throttle/adaptive.py#L78 | MEDIUM |
| `min_samples` | `5` | circuit_breaker/canary_recovery.py#L42 | HIGH |
| `min_samples` | `10` | audit/performance/sampling.py#L35 | MEDIUM |

### 9.11 Health Check 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `health_check_interval_seconds` | `30.0` | chaos/base/models.py#L60 | MEDIUM |
| `health_check_timeout_ms` | `100` | chaos/base/models.py#L61 | MEDIUM |
| `DEFAULT_STALE_THRESHOLD_MINUTES` | `30` | recovery_dashboard.py#L45 | MEDIUM |
| `stabilization_period_seconds` | `300` | emergency_mode/models.py#L88 | HIGH |

---

## 10. 전체 통계 요약

| 구분 | 카테고리 수 | 설정값 수 |
|------|------------|----------|
| 1차 분석 (기존) | 9 | ~60 |
| 2차 분석 (추가) | 21 | ~100 |
| 3차 분석 (추가) | 15 | ~55 |
| 4차 분석 (추가) | 12 | ~75 |
| 5차 분석 (추가) | 10 | ~50 |
| 6차 분석 (추가) | 11 | ~55 |
| **총계** | **78** | **~395** |

---

## 12. 7차 분석 결과 (Blast Radius, Safe Defaults, Reconciler)

### 12.1 Chaos Blast Radius Policy 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `instance_max_concurrent` | `5` | chaos/blast_radius.py#L65 | HIGH |
| `service_max_concurrent` | `2` | chaos/blast_radius.py#L68 | CRITICAL |
| `region_max_concurrent` | `1` | chaos/blast_radius.py#L71 | CRITICAL |
| `instance_auto_approve` | `True` | chaos/blast_radius.py#L75 | MEDIUM |
| `service_auto_approve` | `False` | chaos/blast_radius.py#L78 | HIGH |
| `region_auto_approve` | `False` | chaos/blast_radius.py#L81 | CRITICAL |
| `allowed_hours_start` | `2` (UTC 2 AM) | chaos/blast_radius.py#L85 | HIGH |
| `allowed_hours_end` | `6` (UTC 6 AM) | chaos/blast_radius.py#L88 | HIGH |
| `allow_outside_window` | `False` | chaos/blast_radius.py#L91 | CRITICAL |

### 12.2 Audit Reconciler 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `check_interval_seconds` | `300.0` (5분) | audit/reconciler.py#L41 | MEDIUM |
| `check_window_seconds` | `3600.0` (1시간) | audit/reconciler.py#L44 | MEDIUM |
| `resend_batch_size` | `50` | audit/reconciler.py#L47 | MEDIUM |
| `max_resend_attempts` | `3` | audit/reconciler.py#L50 | HIGH |
| `alert_threshold` | `10` | audit/reconciler.py#L53 | HIGH |
| `_confirmed_ids_max_size` | `10000` | audit/reconciler.py#L170 | MEDIUM |

### 12.3 Safe Defaults - Governance 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `four_eyes_enabled` | `True` | core/safe_defaults.py#L188 | CRITICAL |
| `approval_timeout_hours` | `24` | core/safe_defaults.py#L189 | HIGH |
| `max_approval_retries` | `3` | core/safe_defaults.py#L190 | MEDIUM |
| `threshold_operator` | `0.15` (15%) | core/safe_defaults.py#L191 | HIGH |
| `threshold_admin` | `0.30` (30%) | core/safe_defaults.py#L192 | HIGH |
| `emergency_expiry_hours` | `4` | core/safe_defaults.py#L193 | CRITICAL |
| `audit_log_retention_days` | `90` | core/safe_defaults.py#L194 | HIGH |
| `require_reason_for_changes` | `True` | core/safe_defaults.py#L195 | HIGH |

### 12.4 Safe Defaults - L2 Storage 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `redis_timeout_ms` | `1000` (1초) | core/safe_defaults.py#L200 | HIGH |
| `file_fallback_enabled` | `True` | core/safe_defaults.py#L201 | HIGH |
| `shadow_log_enabled` | `True` | core/safe_defaults.py#L202 | MEDIUM |
| `reconciliation_interval_seconds` | `300` (5분) | core/safe_defaults.py#L204 | MEDIUM |
| `reconciliation_jitter_percent` | `20` | core/safe_defaults.py#L205 | LOW |
| `connection_pool_size` | `10` | core/safe_defaults.py#L207 | HIGH |

### 12.5 Safe Defaults - Drift Threshold 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `warning_percent` | `5.0` (5%) | core/safe_defaults.py#L211 | HIGH |
| `critical_percent` | `20.0` (20%) | core/safe_defaults.py#L212 | CRITICAL |
| `check_interval_seconds` | `60` | core/safe_defaults.py#L213 | MEDIUM |
| `window_size_seconds` | `300` (5분) | core/safe_defaults.py#L214 | MEDIUM |
| `min_samples_required` | `10` | core/safe_defaults.py#L215 | HIGH |
| `suppress_duplicate_alerts_seconds` | `300` (5분) | core/safe_defaults.py#L217 | MEDIUM |

### 12.6 Chaos Synthetic Load - Warmup 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `warmup_seconds` (계산식) | `min(10, duration * 0.1)` | chaos/synthetic_load.py#L525 | MEDIUM |

### 12.7 Emergency Mode - Gradual Recovery 설정

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `gradual_recovery_steps` | `5` | core/safe_defaults.py#L183 | HIGH |
| `recovery_step_duration_seconds` | `60` | core/safe_defaults.py#L184 | HIGH |
| `latency_max_ms` | `1000` | core/safe_defaults.py#L176 | HIGH |

---

## 13. 8차 분석 - Shadow Budget, Anti-Flapping, Chaos Hard Caps

### 13.1 Shadow Budget Calculator - 가중치 설정

**위치**: `error_budget/reconciliation/shadow_calculator.py`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `MAX_WEIGHT_MULTIPLIER` | `50.0` | 480x 폭발 방지용 상한 | CRITICAL |
| `BASE_WEIGHT_MINUTES` | `0.001` | 기본 가중치 (분) | MEDIUM |
| `DEFAULT_SLA_HOURS` | `24` | 기본 SLA 시간 | HIGH |
| `budget_total_minutes` | `43.2` | 99.9% SLO 기반 30일 예산 | HIGH |

**SOURCE_RELIABILITY 딕셔너리** (신뢰도 가중치):

| 소스 | 값 | 설명 |
|------|-----|------|
| `prometheus` | `1.0` | 최고 신뢰도 |
| `dlq` | `0.9` | DLQ 데이터 |
| `application_logs` | `0.8` | 앱 로그 |
| `none_available` | `0.5` | 데이터 없음 |

**SEVERITY_WEIGHT 딕셔너리** (심각도 가중치):

| 심각도 | 값 | 설명 |
|--------|-----|------|
| `critical` | `0.01` | 치명적 |
| `high` | `0.005` | 높음 |
| `medium` | `0.001` | 중간 |
| `low` | `0.0005` | 낮음 |

**PATTERN_OCCURRENCE_WEIGHT 딕셔너리** (패턴 발생 가중치):

| 빈도 | 값 | 설명 |
|------|-----|------|
| `high` | `2.0` | 빈번 |
| `medium` | `1.5` | 보통 |
| `low` | `1.2` | 드문 |
| `none` | `1.0` | 없음 |

### 13.2 Anti-Flapping Guard - 쿨다운 설정

**위치**: `services/coordination/anti_flapping.py`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `EMERGENCY_LEVEL_COOLDOWN_SECONDS` | `300` (5분) | 레벨 전환 간 최소 대기 | CRITICAL |
| `level_cooldown_seconds` | `300` | 레벨 쿨다운 (SSOT 참조) | CRITICAL |
| `cooldown_after_recovery_seconds` | `600` (10분) | 복구 후 재활성화 제한 | HIGH |
| `min_stable_duration_before_recovery_seconds` | `600` (10분) | 복구 전 안정 유지 시간 | HIGH |
| `max_level_transitions_per_hour` | `3` | 시간당 최대 전환 횟수 | HIGH |
| `flapping_lockout_minutes` | `30` | 플래핑 감지 시 잠금 | MEDIUM |
| `recovery_hysteresis_factor` | `1.15` | 복구 히스테리시스 계수 | HIGH |

### 13.3 Chaos Experiment Hard Caps

**위치**: `services/chaos/constants.py`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DISK_IO_MAX_LATENCY_MS` | `2000` (2초) | 디스크 I/O 최대 지연 | HIGH |
| `DISK_IO_MAX_FAILURE_RATE` | `0.30` (30%) | 디스크 I/O 최대 실패율 | HIGH |
| `REPLAY_FLOOD_MAX_ENTRIES` | `5000` | 최대 생성 엔트리 수 | CRITICAL |
| `REPLAY_FLOOD_MAX_RATE` | `500` | 초당 최대 생성 속도 | CRITICAL |
| `CLOCK_SKEW_MAX_SECONDS` | `86400` (1일) | 최대 시간 오차 | MEDIUM |
| `BLACKHOLE_MAX_DURATION_SECONDS` | `300` (5분) | 네트워크 블랙홀 최대 지속 | CRITICAL |
| `POOL_EXHAUSTION_MAX_DURATION_SECONDS` | `120` (2분) | 풀 고갈 최대 지속 | CRITICAL |
| `POOL_EXHAUSTION_MAX_PERCENTAGE` | `0.50` (50%) | 풀 고갈 최대 비율 | CRITICAL |
| `TLS_FAILURE_MAX_DURATION_SECONDS` | `180` (3분) | TLS 실패 최대 지속 | HIGH |
| `TLS_FAILURE_MAX_RATE` | `0.25` (25%) | TLS 실패 최대 발생률 | HIGH |

### 13.4 Distributed Recovery Lock - 락 설정

**위치**: `services/coordination/distributed_recovery_lock.py`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_LOCK_TIMEOUT` | `timedelta(minutes=30)` | 락 자동 만료 시간 | HIGH |
| `LOCK_KEY_TEMPLATE` | `"selfhealing:{namespace}:recovery:lock"` | Redis 락 키 패턴 | IMMUTABLE |

### 13.5 Prometheus Histogram Buckets

**위치**: `services/metrics/definitions.py`, `services/coordination/recovery_metrics.py`

| 메트릭명 | 버킷 | 우선순위 |
|---------|------|----------|
| `retry_attempts_histogram` | `(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)` | LOW |
| `recovery_time_seconds` | `(60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400)` | MEDIUM |
| `human_review_queue_time` | `(300, 900, 1800, 3600, 7200, 14400, 28800)` | LOW |
| `circuit_breaker_open_duration` | `(60, 300, 600, 1800, 3600, 7200)` | LOW |
| `l2_latency_seconds` | `(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)` | MEDIUM |
| `RECOVERY_DURATION_SECONDS` | `[30, 60, 120, 300, 600, 1200, 1800, 3600]` | LOW |
| `RECOVERY_CHECK_LATENCY` | `[1, 5, 10, 30, 60, 120, 300, 600]` | LOW |

### 13.6 Safe Defaults - Notification 설정

**위치**: `core/safe_defaults.py#L124-133`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `critical_threshold` | `10` | Critical 알림 임계치 | HIGH |
| `warning_threshold` | `5` | Warning 알림 임계치 | HIGH |
| `slack_block_text_limit` | `3000` | Slack 블록 텍스트 제한 | LOW |
| `description_max_length` | `500` | 설명 최대 길이 | LOW |
| `action_taken_max_length` | `200` | 조치 내용 최대 길이 | LOW |
| `title_max_length` | `150` | 제목 최대 길이 | LOW |
| `notification_timeout_seconds` | `10` | 알림 전송 타임아웃 | MEDIUM |

### 13.7 Safe Defaults - Error Budget 추가 설정

**위치**: `core/safe_defaults.py#L145-162`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `burn_rate_fast_critical` | `14.4` | 빠른 소진율 Critical | CRITICAL |
| `burn_rate_fast_warning` | `6.0` | 빠른 소진율 Warning | HIGH |
| `burn_rate_slow_warning` | `3.0` | 느린 소진율 Warning | HIGH |
| `burn_rate_slow_info` | `1.0` | 느린 소진율 Info | MEDIUM |
| `failsafe_cooldown_seconds` | `300` | Failsafe 쿨다운 | HIGH |
| `heartbeat_interval_seconds` | `60` | 하트비트 간격 | MEDIUM |
| `heartbeat_timeout_seconds` | `120` | 하트비트 타임아웃 | MEDIUM |

### 13.8 Safe Defaults - Idempotency 설정

**위치**: `core/safe_defaults.py#L164-168`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `default_cache_ttl` | `60` | 기본 캐시 TTL | HIGH |
| `extended_cache_ttl` | `300` | 확장 캐시 TTL | MEDIUM |
| `short_cache_ttl` | `60` | 짧은 캐시 TTL | MEDIUM |
| `clock_skew_tolerance_seconds` | `5.0` | 시간 오차 허용치 | HIGH |

### 13.9 Security Notification Config

**위치**: `services/security_notification/models.py#L91-111`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `slack_critical_channel` | `"#critical-alerts"` | Critical 알림 채널 | HIGH |
| `slack_high_channel` | `"#ops-alerts"` | High 알림 채널 | HIGH |
| `slack_medium_channel` | `"#dev-alerts"` | Medium 알림 채널 | MEDIUM |
| `default_notification_channels` | `["slack", "pagerduty"]` | 기본 알림 채널 목록 | HIGH |

### 13.10 Sampling/Window 설정

**위치**: 다양한 파일

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `sampling_window_seconds` | `60` | recovery_circuit_breaker.py | MEDIUM |
| `sliding_window_size` | `100` | circuit_breaker/config.py | HIGH |
| `sync_interval_seconds` | `5.0` | layered_repository.py | MEDIUM |
| `flush_interval_seconds` | `60` | batch_flush 관련 | MEDIUM |

---

## 14. 9차 분석 추가 설정 (Critical Worker, Retention, RBAC, Weight)

### 14.1 Critical Worker Queue 설정

**위치**: `services/coordination/critical_worker.py#L115-260`

#### 14.1.1 Queue 이름 정의

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `critical_queue_name` | `"selfhealing.critical"` | P0 전용 긴급 큐 | CRITICAL |
| `high_priority_queue_name` | `"selfhealing.high"` | P1-P2 고우선순위 큐 | CRITICAL |
| `default_queue_name` | `"selfhealing.default"` | P3+ 기본 큐 | HIGH |
| `recovery_queue_name` | `"selfhealing.recovery"` | 복구 전용 큐 | HIGH |
| `notification_queue_name` | `"selfhealing.notifications"` | 알림 전용 큐 | MEDIUM |
| `maintenance_queue_name` | `"selfhealing.maintenance"` | 유지보수 큐 | LOW |

#### 14.1.2 Worker 수 및 동시성

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `critical_worker_count` | `2` | Critical 큐 워커 수 (env 기본값) | CRITICAL |
| `high_priority_worker_count` | `4` | High 큐 워커 수 (env 기본값) | HIGH |
| `default_worker_count` | `8` | Default 큐 워커 수 (env 기본값) | MEDIUM |

#### 14.1.3 Queue Concurrency 설정

| 큐 이름 | concurrency | prefetch_multiplier | priority_range | 우선순위 |
|---------|-------------|---------------------|----------------|----------|
| critical | `2` | `1` | `(0, 0)` | CRITICAL |
| high_priority | `4` | `2` | `(1, 2)` | HIGH |
| recovery | `4` | `2` | `(2, 3)` | HIGH |
| notifications | `4` | `4` | `(3, 4)` | MEDIUM |
| default | `8` | `4` | `(5, 10)` | MEDIUM |

#### 14.1.4 Task Priority Enum

**위치**: `services/coordination/critical_worker.py#L50-60`

| Priority | 값 | 용도 |
|----------|-----|------|
| `ABORT` | `0` | 긴급 중단 |
| `ESCALATION` | `1` | 에스컬레이션 |
| `RECOVERY` | `2` | 복구 작업 |
| `NOTIFICATION` | `3` | 알림 전송 |
| `HEALTH_CHECK` | `4` | 헬스체크 |
| `MAINTENANCE` | `5` | 유지보수 |
| `DEFAULT` | `10` | 기본 우선순위 |

### 14.2 Cascade Retention 설정

**위치**: `audit/cascade_config.py#L130-200`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `hot_retention_days` | `7` | Redis Hot Tier 보관 일수 | HIGH |
| `hot_max_count` | `10000` | Redis 최대 항목 수 | HIGH |
| `warm_retention_days` | `90` | PostgreSQL Warm Tier 보관 일수 | MEDIUM |
| `cold_retention_days` | `365` | Archive Cold Tier 보관 일수 (법적) | LOW |
| `index_retention_days` | `30` | 인덱스 보관 일수 | MEDIUM |
| `anchor_retention_days` | `90` | Anchor 보관 일수 | MEDIUM |

### 14.3 Cascade Buffer 임계치 설정

**위치**: `audit/cascade_config.py#L274-310`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `buffer_warning_threshold` | `0.7` | 버퍼 70% 경고 임계치 | HIGH |
| `buffer_critical_threshold` | `0.9` | 버퍼 90% 위험 임계치 | CRITICAL |

### 14.4 Recovery Circuit Breaker Re-Escalation 설정

**위치**: `services/coordination/recovery_circuit_breaker.py#L86-90`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `re_escalation_enabled` | `True` | 재-에스컬레이션 활성화 | HIGH |
| `re_escalation_level` | `"LEVEL_3"` | 재-에스컬레이션 대상 레벨 | HIGH |

### 14.5 Recovery Accountability 설정

**위치**: `services/coordination/models.py#L340-380`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `acknowledgement_required_roles` | `["admin", "sre_lead"]` | 복구 승인 필요 역할 | HIGH |
| `auto_restore_after_hours` | `8.0` | 자동 복구 대기 시간 (시간) | MEDIUM |
| `escalation_channels` | `["slack", "pagerduty"]` | 에스컬레이션 알림 채널 | HIGH |

### 14.6 RBAC 역할 우선순위 설정

**위치**: `context/actor_context.py#L52-57`

| 역할 | 우선순위 값 | 설명 |
|------|-------------|------|
| `selfhealing_admin` | `3` | 관리자 (최고 권한) |
| `selfhealing_operator` | `2` | 운영자 |
| `selfhealing_viewer` | `1` | 조회자 (최소 권한) |

### 14.7 Shadow Budget Calculator 가중치 설정

**위치**: `services/error_budget/reconciliation/shadow_calculator.py#L28-75`

#### 14.7.1 기본 상수

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `MAX_WEIGHT_MULTIPLIER` | `50.0` | 가중치 폭발 방지 상한 | CRITICAL |
| `BASE_WEIGHT_MINUTES` | `0.001` | 기본 가중치 (분) | HIGH |
| `DEFAULT_SLA_HOURS` | `24` | 기본 SLA 시간 | MEDIUM |

#### 14.7.2 소스 신뢰도 가중치

| 소스 | 신뢰도 값 | 설명 |
|------|-----------|------|
| `prometheus` | `1.0` | 가장 정확 |
| `dlq` | `0.9` | 리플레이 대기 |
| `application_logs` | `0.8` | 누락 가능 |
| `none_available` | `0.5` | 추정치 (보수적) |

#### 14.7.3 심각도별 가중치

| 심각도 | 가중치 값 | 배수 |
|--------|-----------|------|
| `critical` | `0.01` | 10배 |
| `high` | `0.005` | 5배 |
| `medium` | `0.001` | 기본 |
| `low` | `0.0005` | 0.5배 |

#### 14.7.4 패턴 발생 횟수별 가중치

| 발생 빈도 | 가중치 배수 | 기준 횟수 |
|-----------|-------------|-----------|
| `high` | `2.0` | 10회 이상 |
| `medium` | `1.5` | 5회 이상 |
| `low` | `1.2` | 3회 이상 |
| `none` | `1.0` | 3회 미만 |

### 14.8 Export/WAL Prefix 설정

**위치**: `audit/export.py`, `audit/wal.py`

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `s3_prefix` | `"audit-export/"` | export.py | MEDIUM |
| `file_prefix` | `"audit_wal"` | wal.py | MEDIUM |

### 14.9 Jitter/Delay 설정

**위치**: `utils/jitter.py`, `services/coordination/optimistic_action.py`

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `max_delay_seconds` | `60.0` | jitter.py | HIGH |
| `min_delay_seconds` | `0.0` | jitter.py | HIGH |
| `sync_delay_seconds` | `0.1` | optimistic_action.py | MEDIUM |

### 14.10 Emergency Mode Recovery 설정

**위치**: `services/emergency_mode/models.py#L39`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `level_step_delay_seconds` | `60` | 레벨 단계 전환 지연 시간 (1분) | HIGH |

### 14.11 Certificate Monitor 설정

**위치**: `core/cert_monitor.py#L221`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `alert_interval_hours` | `24` | 동일 엔드포인트 반복 알림 최소 간격 | MEDIUM |

### 14.12 Escalation Audit Buffer 설정

**위치**: `services/namespace_emergency/escalation_audit.py#L203-212`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `max_buffer_size` | `1000` | 메모리 버퍼 최대 크기 | MEDIUM |

### 14.13 FinOps Chaos Weight 설정

**위치**: `services/finops/service.py#L382`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `MAX_CHAOS_WEIGHT_MULTIPLIER` | `10.0` | Chaos 가중치 최대 배수 | HIGH |

### 14.14 Throttle/Smoothing 설정

**위치**: `services/throttle/config.py`, `services/error_budget/smoother.py`

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `smoothing_factor` | `0.5` | throttle/config.py | MEDIUM |
| `smoothing_factor` | `0.3` | error_budget/smoother.py | MEDIUM |

---

## 15. 전체 통계 요약 (10차 분석 포함)

| 구분 | 카테고리 수 | 설정값 수 |
|------|------------|----------|
| 1차 분석 (기존) | 9 | ~60 |
| 2차 분석 (추가) | 21 | ~100 |
| 3차 분석 (추가) | 15 | ~55 |
| 4차 분석 (추가) | 12 | ~75 |
| 5차 분석 (추가) | 10 | ~50 |
| 6차 분석 (추가) | 11 | ~55 |
| 7차 분석 (추가) | 7 | ~45 |
| 8차 분석 (추가) | 10 | ~65 |
| 9차 분석 (추가) | 14 | ~85 |
| 10차 분석 (추가) | 11 | ~70 |
| **총계** | **120** | **~660** | |

---

## 16. 10차 분석 - Security, Regional Recovery, Chaos Blast Radius 설정

### 16.1 Security Rate Limiting 설정

**위치**: `services/security/models.py#L90-130`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `rate_limit_window_seconds` | `60` | Rate limit 시간 윈도우 (초) | CRITICAL |
| `rate_limit_max_requests` | `100` | 윈도우당 최대 요청 수 | CRITICAL |
| `temporary_ban_hours` | `1` | 임시 차단 시간 (시간) | HIGH |
| `permanent_ban_threshold` | `5` | 영구 차단 임계치 (위반 횟수) | HIGH |

### 16.2 Security IP Banning 설정

**위치**: `services/security/models.py#L90-130`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `suspicious_ip_cache_timeout` | `86400` | 의심 IP 캐시 만료 (24시간, 초) | MEDIUM |
| `suspicious_ip_cache_prefix` | `"security:suspicious_ip:"` | 의심 IP 캐시 키 접두사 | LOW |
| `banned_ip_cache_prefix` | `"security:banned_ip:"` | 차단 IP 캐시 키 접두사 | LOW |

### 16.3 Security Injection Protection 설정

**위치**: `services/security/models.py#L90-130`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `injection_ban_hours` | `24` | Injection 공격 차단 시간 (시간) | CRITICAL |
| `failed_login_threshold` | `5` | 로그인 실패 임계치 | HIGH |

### 16.4 Regional Recovery Base Config

**위치**: `services/coordination/regional_recovery_policy.py#L50-100`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `stability_check_duration_minutes` | `10` | 안정성 체크 기간 (분) | CRITICAL |
| `error_rate_threshold` | `0.10` | 오류율 임계치 (10%) | CRITICAL |
| `success_rate_threshold` | `0.95` | 성공률 임계치 (95%) | CRITICAL |
| `health_check_wait_after_seconds` | `0` | 헬스체크 후 대기 시간 (초) | MEDIUM |
| `canary_resume_wait_after_seconds` | `60` | Canary 재개 후 대기 시간 (초) | HIGH |
| `governance_normal_wait_after_seconds` | `300` | 거버넌스 정상화 후 대기 (5분) | HIGH |
| `approval_timeout_minutes` | `60` | 승인 타임아웃 (분) | HIGH |
| `approval_escalation_intervals` | `[15, 30, 60]` | 승인 에스컬레이션 간격 (분) | HIGH |

### 16.5 Regional Recovery Per-Region Presets

**위치**: `services/coordination/regional_recovery_policy.py#L100-200` (DEFAULT_REGIONAL_CONFIGS)

| 리전 | stability_check | error_rate | success_rate | priority |
|------|-----------------|------------|--------------|----------|
| Seoul (ap-northeast-2) | `10` (분) | `0.05` | `0.98` | `100` |
| Tokyo (ap-northeast-1) | `7` (분) | `0.10` | `0.95` | `50` |
| Oregon (us-west-2) | `5` (분) | `0.15` | `0.90` | `10` |
| Global (default) | `10` (분) | `0.10` | `0.95` | `0` |

**우선순위**: CRITICAL - 리전별 복구 동작에 직접 영향

### 16.6 Chaos Blast Radius Policy

**위치**: `services/chaos/blast_radius.py#L65-100`

**Concurrent Limits (동시 실험 제한)**:

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `instance_max_concurrent` | `5` | 인스턴스 수준 동시 실험 수 | HIGH |
| `service_max_concurrent` | `2` | 서비스 수준 동시 실험 수 | HIGH |
| `region_max_concurrent` | `1` | 리전 수준 동시 실험 수 | CRITICAL |

**Traffic Limits (트래픽 영향 제한)**:

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `max_traffic_percent_instance` | `100.0` | 인스턴스 수준 최대 영향 (%) | HIGH |
| `max_traffic_percent_service` | `50.0` | 서비스 수준 최대 영향 (%) | CRITICAL |
| `max_traffic_percent_region` | `10.0` | 리전 수준 최대 영향 (%) | CRITICAL |

### 16.7 Anti-Flapping Guard 설정

**위치**: `services/coordination/anti_flapping.py#L64-68`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `min_stable_duration_before_recovery_seconds` | `600` | 복구 전 최소 안정 시간 (10분) | CRITICAL |
| `max_level_transitions_per_hour` | `3` | 시간당 최대 레벨 전환 횟수 | CRITICAL |

### 16.8 Recovery Circuit Breaker 추가 설정

**위치**: `services/coordination/recovery_circuit_breaker.py`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `min_samples` | `10` | 최소 샘플 수 | HIGH |
| `half_open_max_requests` | `5` | Half-Open 상태 최대 요청 | HIGH |
| `max_consecutive_trips` | `3` | 연속 트립 최대 횟수 | CRITICAL |
| `open_duration_seconds` | `300` | Open 상태 지속 시간 (5분) | HIGH |

### 16.9 Cascade Auditor & History Limits

**위치**: 다양한 파일

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `MAX_INDEX_SIZE` | `10000` | audit/cascade_auditor.py | HIGH |
| `MAX_HISTORY_ENTRIES` | `50` | services/config_history.py | MEDIUM |
| `MAX_HISTORY` | `100` | services/pending_config.py | MEDIUM |

### 16.10 Shutdown & Watchdog Limits

**위치**: 다양한 파일

| 설정명 | 현재 값 | 위치 | 우선순위 |
|--------|---------|------|----------|
| `max_shutdown_wait_seconds` | `600.0` | services/recovery_shutdown.py (env) | HIGH |
| `max_stage_duration_minutes` | `15` | services/canary_watchdog.py | HIGH |
| `max_age_hours` | `168` | services/recovery_tasks.py (7일) | MEDIUM |
| `max_override_ttl_minutes` | `480` | models.py (8시간) | MEDIUM |

### 16.11 Recovery Dashboard Constants

**위치**: `services/recovery_dashboard.py`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_MAX_REGIONAL_STATUS` | `5` | 대시보드 최대 리전 상태 표시 | LOW |

---

## 17. 다음 단계

1. [92_CONFIG_IMPLEMENTATION_GUIDE.md](92_CONFIG_IMPLEMENTATION_GUIDE.md) 참조하여 구현 진행
2. CRITICAL 우선순위 설정부터 마이그레이션 시작
3. 각 Phase 완료 후 테스트 및 검증
4. 특히 14.1 Critical Worker Queue 설정은 Celery 배포 구성에 직접 영향

---

## 17. 9차 분석 핵심 발견 사항

### 17.1 운영 영향도 높은 설정

| 설정 그룹 | 파일 | 영향도 |
|-----------|------|--------|
| Critical Worker Queue | critical_worker.py | Celery 작업 라우팅 및 우선순위 |
| Cascade Retention | cascade_config.py | 데이터 보관 정책 및 스토리지 비용 |
| Re-Escalation | recovery_circuit_breaker.py | 장애 재발 시 자동 에스컬레이션 |
| RBAC Priority | actor_context.py | 권한 체계 및 감사 로깅 |
| Shadow Budget Weights | shadow_calculator.py | Error Budget 소비 계산 |

### 17.2 환경 변수 기본값이 있는 설정

다음 설정들은 환경 변수에서 읽되, 기본값이 하드코딩되어 있음:

```python
# critical_worker.py
critical_worker_count = int(os.getenv("CRITICAL_WORKER_COUNT", "2"))
high_priority_worker_count = int(os.getenv("HIGH_PRIORITY_WORKER_COUNT", "4"))
default_worker_count = int(os.getenv("DEFAULT_WORKER_COUNT", "8"))

# cascade_config.py
buffer_warning_threshold = float(os.environ.get("SELFHEALING_AUDIT_BUFFER_WARNING_THRESHOLD", "0.7"))
buffer_critical_threshold = float(os.environ.get("SELFHEALING_AUDIT_BUFFER_CRITICAL_THRESHOLD", "0.9"))
```

---

## 18. 11차 분석 - Recovery Tasks, Hash Chain Integrity, TSA 설정

### 18.1 Recovery Tasks Interval 설정

**위치**: `services/coordination/recovery_tasks.py#L54-60`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_TRIGGER_CHECK_INTERVAL` | `60` | 복구 트리거 체크 간격 (초) | HIGH |
| `DEFAULT_HEALTH_MONITOR_INTERVAL` | `30` | 복구 건강 모니터링 간격 (초) | HIGH |
| `DEFAULT_STALE_CHECK_INTERVAL` | `10` | 방치된 복구 체크 간격 (분) | MEDIUM |

### 18.2 Recovery Tasks Retry Delay 설정

**위치**: `services/coordination/recovery_tasks.py` (Celery task decorators)

| Task | `default_retry_delay` | 설명 | 우선순위 |
|------|----------------------|------|----------|
| `check_recovery_trigger_task` | `60` | 트리거 체크 재시도 간격 | MEDIUM |
| `monitor_recovery_health_task` | `30` | 헬스 모니터 재시도 간격 | MEDIUM |
| `cleanup_stale_recoveries_task` | `15` | Stale 정리 재시도 간격 | LOW |

### 18.3 Pending Sequence Manager 설정

**위치**: `audit/integrity/sequence.py#L47-49`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_PENDING_TTL_SECONDS` | `30` | Pending 키 TTL (crash recovery용) | HIGH |
| `DEFAULT_ORPHAN_TTL_SECONDS` | `86400` | Orphan 키 TTL (24시간) | MEDIUM |
| `PENDING_KEY_PREFIX` | `"audit:hash_chain:pending:"` | Pending 키 접두사 | LOW |
| `ORPHANED_KEY_PREFIX` | `"audit:hash_chain:orphaned:"` | Orphan 키 접두사 | LOW |

### 18.4 Daily Hash Anchor 설정

**위치**: `audit/integrity/anchor.py#L46-48`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_RETENTION_DAYS` | `90` | Anchor 보관 기간 (일) | HIGH |
| `ANCHOR_KEY_PREFIX` | `"audit:hash_chain:anchor:"` | Anchor 키 접두사 | LOW |
| `STATE_KEY` | `"audit:hash_chain:state"` | 상태 키 | LOW |

### 18.5 Cold Storage Archive 설정

**위치**: `audit/integrity/cold_storage.py#L239-240`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `ARCHIVE_THRESHOLD_DAYS` | `7` | Archive 임계 일수 (TTL < 7일) | MEDIUM |
| `DEFAULT_COLD_RETENTION_YEARS` | `7` | Cold archive 보관 연수 | HIGH |

### 18.6 Atomic Merge Swap Lock 설정

**위치**: `audit/hash_chain_safety.py#L485-486`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_TIMEOUT_SECONDS` | `300` | Global lock 타임아웃 (5분) | HIGH |
| `blocking_timeout` | `10.0` | Lock 획득 대기 시간 | MEDIUM |
| `LOCK_KEY` | `"audit:hash_chain:reconcile:global_lock"` | Global lock 키 | LOW |

### 18.7 Sharded Date Lock 설정

**위치**: `audit/hash_chain_safety.py#L605-606`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_TIMEOUT_SECONDS` | `120` | Date lock 타임아웃 (2분) | HIGH |
| `blocking_timeout` | `5.0` | Date lock 대기 시간 | MEDIUM |
| `LOCK_KEY_PREFIX` | `"audit:hash_chain:reconcile:date_lock:"` | Date lock 키 접두사 | LOW |

### 18.8 Security Notification Slack Channel 설정

**위치**: `services/security_notification/models.py#L95-98`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `slack_critical_channel` | `"#critical-alerts"` | Critical 심각도 채널 | HIGH |
| `slack_high_channel` | `"#ops-alerts"` | High 심각도 채널 | HIGH |
| `slack_medium_channel` | `"#dev-alerts"` | Medium 심각도 채널 | MEDIUM |

### 18.9 Recovery Dashboard Stale 설정

**위치**: `services/coordination/recovery_dashboard.py#L310`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_STALE_THRESHOLD_MINUTES` | `30` | Stale 판정 임계 시간 (분) | MEDIUM |

### 18.10 Redis Key Guard TTL 설정

**위치**: `services/coordination/redis_key_guard.py#L213-226`

| 패턴 | `default_ttl_seconds` | 설명 | 우선순위 |
|------|----------------------|------|----------|
| `cache:*` | `3600` | 일반 캐시 (1시간) | MEDIUM |
| `metrics:*` | `7200` | 메트릭 데이터 (2시간) | MEDIUM |
| `audit:event:*` | `604800` | 감사 이벤트 (7일) | HIGH |

### 18.11 RFC3161 TSA URL 설정

**위치**: `audit/signed_manifest.py#L205-209`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `DEFAULT_TSA_URLS[0]` | `"https://freetsa.org/tsr"` | 기본 TSA 서버 | MEDIUM |
| `DEFAULT_TSA_URLS[1]` | `"http://timestamp.digicert.com"` | 대체 TSA 서버 #1 | LOW |
| `DEFAULT_TSA_URLS[2]` | `"http://tsa.safecreative.org"` | 대체 TSA 서버 #2 | LOW |
| `timeout_seconds` | `10.0` | TSA 요청 타임아웃 | MEDIUM |

### 18.12 State Cache Base TTL 설정

**위치**: `core/state_cache.py#L38`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `BASE_TTL` | `5.0` | 상태 캐시 기본 TTL (초) | MEDIUM |

---

## 19. 전체 통계 요약 (11차 분석 포함)

| 구분 | 카테고리 수 | 설정값 수 |
|------|------------|----------|
| 1차 분석 (기존) | 9 | ~60 |
| 2차 분석 (추가) | 21 | ~100 |
| 3차 분석 (추가) | 15 | ~55 |
| 4차 분석 (추가) | 12 | ~75 |
| 5차 분석 (추가) | 10 | ~50 |
| 6차 분석 (추가) | 11 | ~55 |
| 7차 분석 (추가) | 7 | ~45 |
| 8차 분석 (추가) | 10 | ~65 |
| 9차 분석 (추가) | 14 | ~85 |
| 10차 분석 (추가) | 11 | ~70 |
| 11차 분석 (추가) | 12 | ~45 |
| 12차 분석 (추가) | 6 | ~35 |
| 13차 분석 (추가) | 8 | ~40 |
| 14차 분석 (추가) | 9 | ~55 |
| **총계** | **155** | **~835** |

---

## 23. 13차 분석 - Precomputed Cache, Reconciler, Shutdown 설정

### 23.1 Precomputed Cache TTL 설정

**위치**: `services/precomputed_cache.py#L66-68`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `L1_TTL_SECONDS` | `2.0` | In-process 캐시 TTL | HIGH |
| `L2_TTL_SECONDS` | `15.0` | Redis 캐시 TTL | HIGH |
| `REFRESH_INTERVAL` | `10.0` | 백그라운드 갱신 간격 | HIGH |
| `CACHE_KEY_HEALTH` | `"selfhealing:cache:health"` | 헬스 캐시 키 | LOW |
| `CACHE_KEY_ERROR_BUDGET` | `"selfhealing:cache:error_budget"` | 버짓 캐시 키 | LOW |
| `CACHE_KEY_POOL_STATUS` | `"selfhealing:cache:pool_status"` | 풀 상태 캐시 키 | LOW |

### 23.2 Audit Reconciler 설정

**위치**: `audit/reconciler.py#L41-53`

| 설정명 | 현재 값 | 환경변수 | 우선순위 |
|--------|---------|----------|----------|
| `check_interval_seconds` | `300.0` | `AUDIT_RECONCILE_INTERVAL` | HIGH |
| `check_window_seconds` | `3600.0` | `AUDIT_RECONCILE_WINDOW` | HIGH |
| `resend_batch_size` | `50` | `AUDIT_RECONCILE_BATCH_SIZE` | MEDIUM |
| `max_resend_attempts` | `3` | `AUDIT_RECONCILE_MAX_ATTEMPTS` | MEDIUM |
| `alert_threshold` | `10` | `AUDIT_RECONCILE_ALERT_THRESHOLD` | HIGH |

### 23.3 Recovery Shutdown 설정

**위치**: `services/coordination/recovery_shutdown.py#L50-66`

| 설정명 | 현재 값 | 환경변수 | 우선순위 |
|--------|---------|----------|----------|
| `recovery_extension_seconds` | `300.0` | `SELFHEALING_RECOVERY_EXTENSION` | HIGH |
| `max_shutdown_wait_seconds` | `600.0` | `SELFHEALING_MAX_SHUTDOWN_WAIT` | CRITICAL |
| `recovery_check_interval_seconds` | `5.0` | (없음) | MEDIUM |
| `log_interval_seconds` | `15.0` | (없음) | LOW |
| `allow_force_shutdown` | `True` | (없음) | HIGH |

### 23.4 Redis Memory Threshold 설정

**위치**: `services/coordination/redis_key_guard.py#L165-171`

| 설정명 | 현재 값 | 환경변수 | 우선순위 |
|--------|---------|----------|----------|
| `memory_warning_threshold` | `80.0` | `REDIS_MEMORY_WARNING_THRESHOLD` | HIGH |
| `memory_critical_threshold` | `90.0` | `REDIS_MEMORY_CRITICAL_THRESHOLD` | CRITICAL |

### 23.5 Recovery Circuit Breaker Sampling 설정

**위치**: `services/coordination/recovery_circuit_breaker.py#L66`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `sampling_window_seconds` | `60` | 샘플링 윈도우 (초) | HIGH |

### 23.6 Chaos Blast Radius Time Window 설정

**위치**: `services/chaos/blast_radius.py#L85-92`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `maintenance_window_start_hour` | `2` | 유지보수 윈도우 시작 (UTC 2AM) | MEDIUM |
| `maintenance_window_end_hour` | `6` | 유지보수 윈도우 종료 (UTC 6AM) | MEDIUM |
| `allow_outside_window` | `False` | 윈도우 외 실험 허용 | HIGH |

### 23.7 Cascade Load Shedding 설정

**위치**: `audit/cascade_load_shedding.py#L135`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `_rate_window_seconds` | `1.0` | Rate 계산 윈도우 | MEDIUM |

### 23.8 Layered Repository Sync 설정

**위치**: `adapters/memory/layered_repository.py#L59,79`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `sync_interval_seconds` | `5.0` | L2 동기화 주기 | HIGH |

---

## 24. 14차 분석 - Batch Flush, Jitter, Histogram, Partition 설정

### 24.1 BatchFlushConfig 설정

**위치**: `audit/performance/batch_writer.py#L20-24`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `batch_size` | `100` | 배치 크기 | HIGH |
| `flush_interval_seconds` | `10.0` | 플러시 간격 (초) | HIGH |
| `sync_on_flush` | `True` | 플러시 시 fsync 호출 | MEDIUM |

### 24.2 AsyncAuditWriter 설정

**위치**: `audit/performance/async_writer.py#L41-42`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `batch_size` | `50` | 비동기 배치 크기 | HIGH |
| `flush_interval_seconds` | `0.1` | 비동기 플러시 간격 | HIGH |

### 24.3 JitterConfig 설정

**위치**: `utils/jitter.py#L152-167`

| 설정명 | 현재 값 | 환경변수 | 우선순위 |
|--------|---------|----------|----------|
| `enabled` | `True` | `SELFHEALING_METRICS_JITTER_ENABLED` | MEDIUM |
| `max_delay_seconds` | `60.0` | `SELFHEALING_METRICS_JITTER_MAX_DELAY_SECONDS` | MEDIUM |
| `min_delay_seconds` | `0.0` | `SELFHEALING_METRICS_JITTER_MIN_DELAY_SECONDS` | LOW |

### 24.4 다양한 Jitter Percent 설정

| 위치 | 설정명 | 현재 값 | 우선순위 |
|------|--------|---------|----------|
| `services/retry_handler.py#L79` | `jitter_percent` | `25` | MEDIUM |
| `services/rate_limit_coordinator.py#L48` | `jitter_percent` | `30.0` | MEDIUM |
| `services/backoff_calculator.py#L31` | `jitter_percent` | `25` | MEDIUM |
| `core/backoff.py#L49` | `jitter_factor` | `0.2` | MEDIUM |
| `core/backoff.py#L80` | `jitter_factor` | `0.1` | MEDIUM |
| `services/canary/state_refresher.py#L45` | `jitter_max_seconds` | `5` | MEDIUM |
| `api/django/rate_limit.py#L249` | `RECOVERY_JITTER_MAX` | `10` | MEDIUM |
| `adapters/resilient/backend.py#L42` | `recovery_jitter_max` | `5.0` | MEDIUM |

### 24.5 ResilientRecorderConfig 설정

**위치**: `audit/resilient_recorder.py#L62-77`

| 설정명 | 현재 값 | 환경변수 | 우선순위 |
|--------|---------|----------|----------|
| `buffer_capacity` | `10000` | `AUDIT_BUFFER_CAPACITY` | HIGH |
| `flush_interval_seconds` | `1.0` | `AUDIT_FLUSH_INTERVAL` | HIGH |
| `flush_batch_size` | `100` | `AUDIT_FLUSH_BATCH_SIZE` | HIGH |
| `circuit_failure_threshold` | `3` | `AUDIT_CIRCUIT_BREAKER_THRESHOLD` | CRITICAL |
| `circuit_success_threshold` | `2` | (없음) | HIGH |
| `circuit_timeout_seconds` | `30.0` | `AUDIT_CIRCUIT_BREAKER_TIMEOUT` | HIGH |
| `enable_syslog_fallback` | `True` | `AUDIT_SYSLOG_ENABLED` | MEDIUM |

### 24.6 Partition Reconciliation 설정

**위치**: `services/namespace_emergency/partition_reconciliation.py#L42-48`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `HEARTBEAT_INTERVAL_SECONDS` | `10` | Heartbeat 전송 간격 | HIGH |
| `PARTITION_DETECTION_THRESHOLD_SECONDS` | `30` | 파티션 감지 임계값 | HIGH |
| `MAX_RECONCILIATION_ACTIONS` | `100` | 조정 액션 히스토리 최대 크기 | LOW |

### 24.7 Optimistic Action 동기화 설정

**위치**: `services/coordination/optimistic_action.py#L99-100`

| 설정명 | 현재 값 | 설명 | 우선순위 |
|--------|---------|------|----------|
| `sync_delay_seconds` | `0.1` | 중앙 동기화 지연 시간 (초) | HIGH |

### 24.8 Histogram Buckets 정의

**위치**: `services/metrics/definitions.py`

| 메트릭명 | 버킷 정의 | 설명 |
|----------|----------|------|
| `retry_attempts_histogram` | `(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)` | 재시도 횟수 분포 |
| `recovery_time_seconds` | `(60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400)` | 복구 소요 시간 |
| `human_review_queue_time` | `(300, 900, 1800, 3600, 7200, 14400, 28800)` | 휴먼 리뷰 대기 시간 |
| `circuit_breaker_open_duration` | `(60, 300, 600, 1800, 3600, 7200)` | CB 오픈 지속 시간 |
| `l2_latency_seconds` | `(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)` | L2 스토리지 지연 |

**위치**: `services/coordination/recovery_metrics.py#L89-97`

| 메트릭명 | 버킷 정의 | 설명 |
|----------|----------|------|
| `RECOVERY_DURATION_SECONDS` | `[30, 60, 120, 300, 600, 1200, 1800, 3600]` | 복구 세션 소요 시간 |
| `RECOVERY_STEP_DURATION_SECONDS` | `[1, 5, 10, 30, 60, 120, 300, 600]` | 복구 단계 소요 시간 |

### 24.9 Recovery Hysteresis Factor 설정

**위치**: `services/coordination/anti_flapping.py#L75-78`

| 설정명 | 현재 값 | 환경변수 | 우선순위 |
|--------|---------|----------|----------|
| `recovery_hysteresis_factor` | `1.15` | `SELFHEALING_RECOVERY_HYSTERESIS_FACTOR` | HIGH |

---

## 25. 다음 단계

1. [92_CONFIG_IMPLEMENTATION_GUIDE.md](92_CONFIG_IMPLEMENTATION_GUIDE.md) 참조하여 구현 진행
2. CRITICAL 우선순위 설정부터 마이그레이션 시작
3. 각 Phase 완료 후 테스트 및 검증
4. 특히 Hash Chain Integrity 관련 설정은 감사 로그 무결성에 직접 영향
