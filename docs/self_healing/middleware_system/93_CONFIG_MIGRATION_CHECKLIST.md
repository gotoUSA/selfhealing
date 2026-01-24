# Configuration Migration Checklist

> 마이그레이션 진행 상황 추적을 위한 체크리스트
> 
> **참조**: 구현 방법은 [92_CONFIG_IMPLEMENTATION_GUIDE.md](92_CONFIG_IMPLEMENTATION_GUIDE.md) 참조

---

## 📊 전체 진행률

| 주차 | 대상 | 클래스 수 | 상태 | 완료율 |
|------|------|----------|------|--------|
| Week 1 | CRITICAL | 5개 | ✅ 완료 | 100% |
| Week 2 | HIGH | 6개 | ⬜ 대기 | 0% |
| Week 3 | MEDIUM | 6개 | ⬜ 대기 | 0% |
| Week 4 | LOW + 마무리 | 9개 | ⬜ 대기 | 0% |
| **합계** | - | **26개** | - | **19%** |

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
| A | `settings/error_budget.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `error_budget_gate.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `warning_threshold` (기본값: 0.8)
- [ ] `critical_threshold` (기본값: 0.5)
- [ ] `exhausted_threshold` (기본값: 0.0)
- [ ] `budget_reset_interval_hours` (기본값: 24)

### [7] ErrorBudgetPropagationSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/error_budget_propagation.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `error_budget_propagation.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `decay_factor` (기본값: 0.8)
- [ ] `max_hops` (기본값: 3)
- [ ] `propagation_delay_ms` (기본값: 100)

### [8] AntiFlappingSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/anti_flapping.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `anti_flapping.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `cooldown_period` (기본값: 300)
- [ ] `hysteresis_margin` (기본값: 0.1)
- [ ] `max_transitions` (기본값: 5)
- [ ] `recovery_hysteresis_factor` (기본값: 1.15)

### [9] DLQSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/dlq.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `dlq_models.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `retention_hours` (기본값: 168)
- [ ] `max_retry_attempts` (기본값: 3)
- [ ] `replay_batch_size` (기본값: 100)

### [10] ThrottleSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/throttle.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `throttle/config.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `min_rtt_ms` (기본값: 50)
- [ ] `tolerance` (기본값: 2.0)
- [ ] `probe_multiplier` (기본값: 2.0)
- [ ] `smoothing` (기본값: 0.2)
- [ ] `initial_limit` (기본값: 20)

### [11] CriticalWorkerSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/critical_worker.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `critical_worker.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `queue_name` (기본값: "recovery_critical")
- [ ] `worker_count` (기본값: 3)
- [ ] `prefetch_count` (기본값: 1)
- [ ] `task_timeout_seconds` (기본값: 300)

---

## Week 3: MEDIUM Settings (6개)

### [12] ChaosExperimentSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/chaos_experiment.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `chaos_experiment_manager.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `max_duration_seconds` (기본값: 3600)
- [ ] `grace_period_seconds` (기본값: 60)
- [ ] `result_ttl` (기본값: 86400)

### [13] ChaosBlastRadiusSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/chaos_blast_radius.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `blast_radius.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `max_affected_services` (기본값: 0.3)
- [ ] `max_concurrent` (기본값: 5)
- [ ] `maintenance_window_start_hour` (기본값: 2)
- [ ] `maintenance_window_end_hour` (기본값: 6)

### [14] CorruptionShieldSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/corruption_shield.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `corruption_shield.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `z_score_threshold` (기본값: 3.0)
- [ ] `iqr_multiplier` (기본값: 1.5)
- [ ] `min_samples` (기본값: 30)
- [ ] `quarantine_ttl` (기본값: 3600)

### [15] NotificationChannelSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/notification_channel.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `notification_config.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `rate_limit_per_minute` (기본값: 10)
- [ ] `max_retry` (기본값: 3)
- [ ] `default_channel` (기본값: "slack")

### [16] CascadeRetentionSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/cascade_retention.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `cascade_storage.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `hot_tier_days` (기본값: 7)
- [ ] `warm_tier_days` (기본값: 30)
- [ ] `cold_tier_days` (기본값: 365)

### [17] DistributedLockSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/distributed_lock.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `distributed_lock.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `lock_timeout` (기본값: 30)
- [ ] `retry_interval` (기본값: 0.1)

---

## Week 4: LOW Settings + 마무리 (9개)

### [18] DashboardSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/dashboard.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | `dashboard_service.py` 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `cache_ttl_seconds` (기본값: 30)
- [ ] `cache_ttl_status` (기본값: 60)
- [ ] `cache_ttl_activity` (기본값: 120)

### [19] BatchSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/batch.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | 다수 파일 `batch_size` 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `default_batch_size` (기본값: 100)
- [ ] `logger_batch_size` (기본값: 50)
- [ ] `flush_interval` (기본값: 10.0)

### [20] AuditSettings

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| A | `settings/audit_settings.py` 생성 | ⬜ | |
| B | constants.py 등록 | ⬜ | |
| C | 감사 관련 파일 하드코딩 대체 | ⬜ | |
| D | 단위 테스트 | ⬜ | |

**필드**:
- [ ] `max_history` (기본값: 50)
- [ ] `retention_days` (기본값: 90)

### [21-26] 추가 Settings

| 번호 | 클래스 | 상태 | 비고 |
|------|--------|------|------|
| 21 | `CeleryTaskSettings` | ⬜ | max_retries, task_timeout |
| 22 | `ApiViewSettings` | ⬜ | default_limit, default_offset |
| 23 | `DomainSensitivitySettings` | ⬜ | 도메인별 가중치 |
| 24 | `SlackChannelSettings` | ⬜ | 채널 설정 |
| 25 | `AuditIntegritySettings` | ⬜ | sequence_ttl 등 |
| 26 | `RegionalRecoveryPolicySettings` | ⬜ | 지역별 복구 정책 |

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
| 단위 테스트 | 26개 Settings 클래스 | ⬜ |
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
