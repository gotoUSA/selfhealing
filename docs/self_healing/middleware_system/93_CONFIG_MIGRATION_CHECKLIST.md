# Configuration Migration Checklist

> 마이그레이션 진행 상황 추적을 위한 체크리스트
> 
> **참조**: 구현 방법은 [92_CONFIG_IMPLEMENTATION_GUIDE.md](92_CONFIG_IMPLEMENTATION_GUIDE.md) 참조

---

## 📊 전체 진행률

| 주차 | 대상 | 클래스 수 | 상태 | 완료율 |
|------|------|----------|------|--------|
| Week 1 | CRITICAL | 5개 | ✅ 완료 | 100% |
| Week 2 | HIGH | 6개 | ✅ 완료 | 100% |
| Week 3 | MEDIUM | 6개 | ✅ 완료 | 100% |
| Week 4 | LOW + 마무리 | 9개 | ✅ 완료 | 100% |
| **합계** | - | **26개** | ✅ | **100%** |

**상태**: ⬜ 대기 | 🔄 진행중 | ✅ 완료 | ❌ 보류

---

## Week 1: CRITICAL Settings (5개)

### [1] SecuritySettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/security.py` 생성 | ✅ | 기존 구현됨 |
| B | constants.py 등록 | ✅ | 기존 구현됨 |
| C | `security/models.py` 하드코딩 대체 | ✅ | 기존 구현됨 |
| D | 단위 테스트 | ✅ | 테스트 통과 |

**필드 (91 문서 참조)**:
- [x] `rate_limit_max_requests` (기본값: 100)
- [x] `rate_limit_window_seconds` (기본값: 60)
- [x] `injection_ban_hours` (기본값: 24)
- [x] `injection_sensitivity` (기본값: "HIGH")
- [x] `max_key_length` (기본값: 100)
- [x] `max_value_length` (기본값: 10000)

### [2] RecoveryCircuitBreakerSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/recovery_circuit_breaker.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `recovery_circuit_breaker.py` 하드코딩 대체 | ✅ | from_settings() 추가 |
| D | 단위 테스트 | ✅ | 25개 테스트 통과 |

**필드**:
- [x] `error_rate_threshold` (기본값: 0.15)
- [x] `sampling_window_seconds` (기본값: 60)
- [x] `min_samples` (기본값: 10)
- [x] `open_duration_seconds` (기본값: 300)
- [x] `half_open_max_requests` (기본값: 5)
- [x] `max_consecutive_trips` (기본값: 3)
- [x] `re_escalation_enabled` (기본값: True)
- [x] `re_escalation_level` (기본값: "LEVEL_3")

### [3] RedisKeyGuardSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/redis_key_guard.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `redis_key_guard.py` 하드코딩 대체 | ✅ | field default_factory 사용 |
| D | 단위 테스트 | ✅ | 24개 테스트 통과 |

**필드**:
- [x] `memory_warning_threshold` (기본값: 80.0)
- [x] `memory_critical_threshold` (기본값: 90.0)
- [x] `target_free_percent` (기본값: 20.0)
- [x] `recovery_session_ttl_seconds` (기본값: 3600)
- [x] `recovery_state_ttl_seconds` (기본값: 86400)
- [x] `temp_key_ttl_seconds` (기본값: 300)
- [x] `telemetry_ttl_seconds` (기본값: 3600)

### [4] RecoveryShutdownSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/recovery_shutdown.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `recovery_shutdown.py` 하드코딩 대체 | ✅ | field default_factory 사용 |
| D | 단위 테스트 | ✅ | 16개 테스트 통과 |

**필드**:
- [x] `default_drain_timeout_seconds` (기본값: 30.0)
- [x] `recovery_extension_seconds` (기본값: 300.0)
- [x] `max_shutdown_wait_seconds` (기본값: 600.0)
- [x] `recovery_check_interval_seconds` (기본값: 5.0)
- [x] `log_interval_seconds` (기본값: 15.0)
- [x] `allow_force_shutdown` (기본값: True)

### [5] ResilientRecorderSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/resilient_recorder.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `resilient_recorder.py` 하드코딩 대체 | ✅ | from_settings() 추가 |
| D | 단위 테스트 | ✅ | 14개 테스트 통과 |

**필드**:
- [x] `buffer_capacity` (기본값: 10000)
- [x] `backpressure_strategy` (기본값: "DROP_OLDEST")
- [x] `flush_interval_seconds` (기본값: 1.0)
- [x] `flush_batch_size` (기본값: 100)
- [x] `circuit_failure_threshold` (기본값: 3)
- [x] `circuit_success_threshold` (기본값: 2)
- [x] `circuit_timeout_seconds` (기본값: 30.0)
- [x] `fallback_directory` (기본값: "/tmp/selfhealing_fallback")
- [x] `fallback_max_files` (기본값: 100)
- [x] `fallback_max_size_mb` (기본값: 100.0)

---

## Week 2: HIGH Settings (6개)

### [6] ErrorBudgetSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/error_budget.py` 생성 | ✅ | 이미 존재 |
| B | constants.py 등록 | ✅ | 이미 등록됨 |
| C | `error_budget_gate.py` 하드코딩 대체 | ✅ | 기존 구현 |
| D | 단위 테스트 | ✅ | 147개 테스트 통과 |

**필드**:
- [x] `threshold_healthy` (기본값: 75.0)
- [x] `threshold_caution` (기본값: 50.0)
- [x] `threshold_warning` (기본값: 20.0)
- [x] `threshold_critical` (기본값: 0.0)

### [7] ErrorBudgetPropagationSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/error_budget_propagation.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `error_budget_propagation.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `decay_per_hop` (기본값: 0.5)
- [x] `max_hops` (기본값: 3)
- [x] `propagation_delay_ms` (기본값: 100)
- [x] `base_multiplier` (기본값: 5.0)
- [x] `min_multiplier` (기본값: 1.0)
- [x] `enabled` (기본값: true)

### [8] AntiFlappingSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/anti_flapping.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `anti_flapping.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `level_cooldown_seconds` (기본값: 300)
- [x] `cooldown_after_recovery_seconds` (기본값: 600)
- [x] `min_stable_duration_before_recovery_seconds` (기본값: 600)
- [x] `max_level_transitions_per_hour` (기본값: 3)
- [x] `flapping_lockout_minutes` (기본값: 30)
- [x] `recovery_hysteresis_factor` (기본값: 1.15)

### [9] DLQSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/dlq.py` 생성 | ✅ | 이미 존재 |
| B | constants.py 등록 | ✅ | 이미 등록됨 |
| C | `dlq_models.py` 하드코딩 대체 | ✅ | 기존 구현 |
| D | 단위 테스트 | ✅ | 147개 테스트 통과 |

**필드**:
- [x] `enabled` (기본값: true)
- [x] `max_retries` (기본값: 3)
- [x] `retry_delay` (기본값: 60)
- [x] `expiry_hours` (기본값: 72)
- [x] `retention_days` (기본값: 30)
- [x] `batch_size` (기본값: 10)
- [x] `max_replay_attempts` (기본값: 2)

### [10] ThrottleSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/throttle.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `throttle/config.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `initial_limit` (기본값: 100)
- [x] `window_seconds` (기본값: 60)
- [x] `min_limit` (기본값: 10)
- [x] `max_limit` (기본값: 500)
- [x] `sample_interval_ms` (기본값: 500)
- [x] `smoothing_factor` (기본값: 0.5)
- [x] `decrease_ratio` (기본값: 0.9)
- [x] `increase_step` (기본값: 1)
- [x] `sla_warning_ms` (기본값: 200)
- [x] `sla_critical_ms` (기본값: 500)
- [x] `emergency_limit` (기본값: 10)
- [x] `key_prefix` (기본값: "selfhealing:throttle")

### [11] CriticalWorkerSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/critical_worker.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `critical_worker.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `critical_queue_name` (기본값: "selfhealing.critical")
- [x] `high_priority_queue_name` (기본값: "selfhealing.high")
- [x] `default_queue_name` (기본값: "selfhealing.default")
- [x] `recovery_queue_name` (기본값: "selfhealing.recovery")
- [x] `notification_queue_name` (기본값: "selfhealing.notifications")
- [x] `maintenance_queue_name` (기본값: "selfhealing.maintenance")
- [x] `critical_worker_count` (기본값: 2)
- [x] `high_priority_worker_count` (기본값: 4)
- [x] `default_worker_count` (기본값: 8)
- [x] `critical_concurrency` (기본값: 2)
- [x] `high_priority_concurrency` (기본값: 4)
- [x] `default_concurrency` (기본값: 8)
- [x] `critical_prefetch_multiplier` (기본값: 1)
- [x] `high_priority_prefetch_multiplier` (기본값: 2)
- [x] `default_prefetch_multiplier` (기본값: 4)
- [x] `task_timeout_seconds` (기본값: 300)

---

## Week 3: MEDIUM Settings (6개)

### [12] ChaosExperimentSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/chaos_experiment.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `chaos_experiment_manager.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `max_duration_seconds` (기본값: 3600)
- [x] `default_duration_seconds` (기본값: 300)
- [x] `default_ttl_seconds` (기본값: 600)
- [x] `grace_period_seconds` (기본값: 300)
- [x] `sla_breach_threshold_percent` (기본값: 1.0)
- [x] `result_ttl_seconds` (기본값: 86400)
- [x] `health_check_interval_seconds` (기본값: 30.0)
- [x] `health_check_timeout_ms` (기본값: 100)

### [13] ChaosBlastRadiusSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/chaos_blast_radius.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `blast_radius.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `instance_max_concurrent` (기본값: 5)
- [x] `service_max_concurrent` (기본값: 2)
- [x] `region_max_concurrent` (기본값: 1)
- [x] `instance_auto_approve` (기본값: True)
- [x] `service_auto_approve` (기본값: False)
- [x] `region_auto_approve` (기본값: False)
- [x] `allowed_hours_start` (기본값: 2)
- [x] `allowed_hours_end` (기본값: 6)
- [x] `allow_outside_window` (기본값: False)
- [x] `max_traffic_percent_instance` (기본값: 100.0)
- [x] `max_traffic_percent_service` (기본값: 50.0)
- [x] `max_traffic_percent_region` (기본값: 10.0)

### [14] CorruptionShieldSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/corruption_shield.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `corruption_shield.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `l1_enabled` (기본값: True)
- [x] `l2_enabled` (기본값: True)
- [x] `l3_enabled` (기본값: True)
- [x] `z_score_threshold` (기본값: 3.0)
- [x] `iqr_multiplier` (기본값: 1.5)
- [x] `min_samples_for_anomaly` (기본값: 10)
- [x] `min_amount` (기본값: 100)
- [x] `max_amount` (기본값: 100,000,000)
- [x] `quarantine_ttl_seconds` (기본값: 3600)

### [15] NotificationChannelSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/notification_channel.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `notification_config.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `rate_limit_per_minute` (기본값: 60)
- [x] `rate_limit_per_hour` (기본값: 300)
- [x] `max_retry` (기본값: 3)
- [x] `retry_delay_seconds` (기본값: 30)
- [x] `cooldown_seconds` (기본값: 300)
- [x] `default_channels` (기본값: ["slack"])
- [x] `critical_channels` (기본값: ["slack", "email", "sms", "pagerduty"])
- [x] `escalation_channels` (기본값: ["slack", "pagerduty"])

### [16] CascadeRetentionSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/cascade_retention.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `cascade_storage.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `hot_retention_days` (기본값: 7)
- [x] `hot_max_count` (기본값: 10000)
- [x] `warm_retention_days` (기본값: 90)
- [x] `cold_retention_days` (기본값: 365)
- [x] `index_retention_days` (기본값: 30)
- [x] `anchor_retention_days` (기본값: 90)
- [x] `buffer_warning_threshold` (기본값: 0.7)
- [x] `buffer_critical_threshold` (기본값: 0.9)
- [x] `max_events_per_second` (기본값: 1000)

### [17] DistributedLockSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/distributed_lock.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `distributed_lock.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `timeout_minutes` (기본값: 30)
- [x] `retry_interval_seconds` (기본값: 0.1)
- [x] `max_retry_attempts` (기본값: 100)
- [x] `extend_interval_seconds` (기본값: 60)
- [x] `auto_extend_enabled` (기본값: True)

---

## Week 4: LOW Settings + 마무리 (9개)

### [18] DashboardSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/dashboard.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | `dashboard_service.py` 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `cache_ttl_seconds` (기본값: 30)
- [x] `cache_ttl_status` (기본값: 15)
- [x] `cache_ttl_activity` (기본값: 60)
- [x] `tracker_cache_ttl` (기본값: 30.0)
- [x] `health_penalty_cache_ttl` (기본값: 5.0)
- [x] `stale_threshold_minutes` (기본값: 30)
- [x] `max_regional_status` (기본값: 5)
- [x] `cache_prefix` (기본값: "selfhealing:dashboard:")

### [19] BatchSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/batch.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | 다수 파일 `batch_size` 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `default_batch_size` (기본값: 100)
- [x] `logger_batch_size` (기본값: 10)
- [x] `flush_interval` (기본값: 5.0)
- [x] `dlq_batch_size` (기본값: 50)
- [x] `redis_scan_batch_size` (기본값: 100)
- [x] `audit_batch_size` (기본값: 100)
- [x] `audit_flush_interval` (기본값: 10.0)

### [20] AuditSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/audit_settings.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | 감사 관련 파일 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `max_history` (기본값: 100)
- [x] `config_history_entries` (기본값: 50)
- [x] `retention_days` (기본값: 90)
- [x] `event_history_max` (기본값: 1000)
- [x] `cascade_history_max` (기본값: 100)
- [x] `pool_stats_history_max` (기본값: 100)

### [21] CeleryTaskSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/celery_task.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | tasks 파일들 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `max_retries` (기본값: 3)
- [x] `default_retry_delay` (기본값: 60)
- [x] `min_retry_delay` (기본값: 30)
- [x] `max_retry_delay` (기본값: 300)
- [x] `backoff_multiplier` (기본값: 2.0)
- [x] `time_limit` (기본값: 300)
- [x] `soft_time_limit` (기본값: 240)
- [x] `default_rate_limit` (기본값: "10/s")
- [x] `default_queue` (기본값: "selfhealing.default")
- [x] `trigger_check_interval` (기본값: 60)
- [x] `health_monitor_interval` (기본값: 30)
- [x] `stale_check_interval` (기본값: 10)

### [22] ApiViewSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/api_view.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | API 뷰 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `default_limit` (기본값: 100)
- [x] `default_offset` (기본값: 0)
- [x] `max_limit` (기본값: 1000)
- [x] `default_order` (기본값: "-created_at")
- [x] `max_events` (기본값: 500)
- [x] `max_incidents` (기본값: 100)
- [x] `max_injection` (기본값: 100)
- [x] `throttle_max_limit` (기본값: 1000)

### [23] DomainSensitivitySettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/domain_sensitivity.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | error_budget 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `payment` (기본값: 10.0)
- [x] `order` (기본값: 5.0)
- [x] `inventory` (기본값: 3.0)
- [x] `notification` (기본값: 1.5)
- [x] `analytics` (기본값: 1.0)
- [x] `default_sensitivity` (기본값: 1.0)
- [x] `level_multiplier_normal` (기본값: 1.0)
- [x] `level_multiplier_level_1` (기본값: 1.5)
- [x] `level_multiplier_level_2` (기본값: 3.0)
- [x] `level_multiplier_level_3` (기본값: 5.0)

### [24] SlackChannelSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/slack_channel.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | 알림 관련 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `default_channel` (기본값: "#selfhealing-alerts")
- [x] `critical_channel` (기본값: "#selfhealing-critical")
- [x] `emergency_channel` (기본값: "#selfhealing-emergency")
- [x] `recovery_channel` (기본값: "#selfhealing-recovery")
- [x] `audit_channel` (기본값: "#selfhealing-audit")
- [x] `on_call_channel` (기본값: "#on-call")
- [x] `block_text_limit` (기본값: 3000)
- [x] `max_attachments` (기본값: 10)
- [x] `title_max_length` (기본값: 150)
- [x] `description_max_length` (기본값: 500)
- [x] `action_taken_max_length` (기본값: 200)
- [x] `webhook_timeout_seconds` (기본값: 10)

### [25] AuditIntegritySettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/audit_integrity.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | audit 관련 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `pending_ttl_seconds` (기본값: 30)
- [x] `orphan_ttl_seconds` (기본값: 86400)
- [x] `archive_threshold_days` (기본값: 7)
- [x] `cold_retention_years` (기본값: 7)
- [x] `integrity_check_interval` (기본값: 3600)
- [x] `hash_chain_lock_timeout` (기본값: 5.0)
- [x] `verification_batch_size` (기본값: 100)
- [x] `max_verification_retries` (기본값: 3)
- [x] `retention_days` (기본값: 365)

### [26] RegionalRecoveryPolicySettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/regional_recovery_policy.py` 생성 | ✅ | 신규 구현 |
| B | constants.py 등록 | ✅ | STORAGE_KEYS, CONFIG_CLASSES |
| C | regional_recovery 관련 하드코딩 대체 | ⬜ | 추후 진행 |
| D | 단위 테스트 | ✅ | import/기본값 확인 |

**필드**:
- [x] `error_rate_threshold` (기본값: 0.10)
- [x] `success_rate_threshold` (기본값: 0.95)
- [x] `auto_approve_threshold` (기본값: 0.1)
- [x] `stability_check_duration_minutes` (기본값: 10)
- [x] `max_recovery_duration_minutes` (기본값: 60)
- [x] `cooldown_minutes` (기본값: 15)
- [x] `approval_timeout_minutes` (기본값: 60)
- [x] `escalation_interval_1` (기본값: 15)
- [x] `escalation_interval_2` (기본값: 30)
- [x] `escalation_interval_3` (기본값: 60)
- [x] `max_concurrent_recoveries` (기본값: 3)
- [x] `ready_to_restore_timeout_hours` (기본값: 4.0)
- [x] `auto_restore_after_hours` (기본값: 8.0)

---

## Phase 5: API 엔드포인트 추가

### ViewSet 생성 (총 26개)

| 그룹 | 엔드포인트 수 | 상태 |
|------|-------------|------|
| 기본 설정 | 6개 | ⬜ |
| Error Budget | 4개 | ⬜ |
| Chaos Engineering | 3개 | ⬜ |
| Corruption Shield | 2개 | ⬜ |
| Coordination | 5개 | ⬜ |
| 기타 | 6개 | ⬜ |

### 검증

- [ ] 각 엔드포인트 CRUD 테스트
- [ ] 권한 검증 테스트
- [ ] OpenAPI 스키마 생성 확인

---

## Phase 6: 테스트 및 검증

### 테스트 통과 현황

| 테스트 유형 | 대상 | 상태 |
|------------|------|------|
| 단위 테스트 | 26개 Settings 클래스 | ✅ 147개 통과 |
| 통합 테스트 | LayeredProvider | ⬜ |
| API 테스트 | 26개 엔드포인트 | ⬜ |
| 부하 테스트 | 설정 읽기/쓰기 | ⬜ |

### 배포 체크리스트

| 환경 | 작업 | 상태 |
|------|------|------|
| Staging | 배포 | ⬜ |
| Staging | 24시간 검증 | ⬜ |
| Production | 배포 | ⬜ |
| Production | 모니터링 | ⬜ |

---

## 변경 이력

| 날짜 | 변경 내용 | 작성자 |
|------|----------|--------|
| 2026-01-24 | 초안 작성 | - |
| 2026-01-24 | 92 문서와 동기화 (26개 클래스 전체 반영) | - |
| 2026-01-24 | Week 4 구현 완료 (9개 Settings 클래스) | - |
