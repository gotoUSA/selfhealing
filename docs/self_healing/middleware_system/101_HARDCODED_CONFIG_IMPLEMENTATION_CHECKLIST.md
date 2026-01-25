# 101. 하드코딩 설정 외부화 - 구현 체크리스트

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 진행 중
- **목적**: 다른 세션에서 리팩토링 작업을 이어갈 수 있도록 구현 순서 정리
- **관련 문서**: 94-100

---

## 1. 전체 요약

| 지표 | 값 |
|------|-----|
| 총 대상 파일 | 94개 |
| 총 하드코딩 항목 | ~246건 |
| 신규 Settings 파일 | ~15개 |
| 기존 Settings 확장 | ~10개 |
| 예상 총 소요시간 | ~46시간 |

---

## 2. 구현 순서 (체크리스트)

### Phase 1: 신규 Settings 모듈 생성 [예상: 4시간]

#### 1.1 settings/stress_test.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_stress_test_settings.py`

```python
# 필요한 설정 필드 (stress_views.py 기반)
# 파일: api/django/stress_views.py
SELFHEALING_STRESS_TEST_DEFAULT_LOCK_TIMEOUT_MS = 1  # Line 282
SELFHEALING_STRESS_TEST_DEFAULT_SLEEP_SECONDS = 1   # Line 330
```

#### 1.2 settings/cleanup.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_cleanup_settings.py`

```python
# 필요한 설정 필드 (cleanup_service.py, cleanup_tasks.py 기반)
# 파일: services/cleanup_service.py, tasks/cleanup_tasks.py
SELFHEALING_CLEANUP_ARCHIVE_OLDER_THAN_DAYS = 30      # Line 68
SELFHEALING_CLEANUP_EXPIRED_CONFIG_HOURS = 24         # Line 118
SELFHEALING_CLEANUP_APPROVAL_EXPIRY_HOURS = 72        # Line 168
SELFHEALING_CLEANUP_PURGE_OLDER_THAN_DAYS = 90        # Line 212
```

#### 1.3 settings/precomputed_cache.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_precomputed_cache_settings.py`

```python
# 필요한 설정 필드 (precomputed_cache.py 기반)
# 파일: services/precomputed_cache.py
SELFHEALING_PRECOMPUTED_CACHE_L1_TTL_SECONDS = 5.0   # Line 23
SELFHEALING_PRECOMPUTED_CACHE_L2_TTL_SECONDS = 30.0  # Line 24
SELFHEALING_PRECOMPUTED_CACHE_MAXSIZE = 100          # Line 111
```

#### 1.4 settings/backoff.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_backoff_settings.py`

```python
# 필요한 설정 필드 (core/backoff.py 기반)
# 파일: core/backoff.py
SELFHEALING_BACKOFF_EXPONENTIAL_BASE_DELAY = 1.0     # Line 44
SELFHEALING_BACKOFF_EXPONENTIAL_MAX_DELAY = 300.0    # Line 45
SELFHEALING_BACKOFF_EXPONENTIAL_MULTIPLIER = 2.0     # Line 46
SELFHEALING_BACKOFF_EXPONENTIAL_JITTER = 0.2         # Line 49
SELFHEALING_BACKOFF_LINEAR_BASE_DELAY = 1.0          # Line 74
SELFHEALING_BACKOFF_LINEAR_INCREMENT = 1.0           # Line 75
SELFHEALING_BACKOFF_LINEAR_MAX_DELAY = 60.0          # Line 76
SELFHEALING_BACKOFF_LINEAR_JITTER = 0.1              # Line 80
SELFHEALING_BACKOFF_CONSTANT_DELAY = 5.0             # Line 106
SELFHEALING_BACKOFF_CONSTANT_JITTER = 0.1            # Line 109
```

#### 1.5 settings/pool_monitor.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_pool_monitor_settings.py`

```python
# 필요한 설정 필드 (core/pool_monitor.py 기반)
# 파일: core/pool_monitor.py
SELFHEALING_POOL_WARNING_THRESHOLD = 70.0            # Line 114
SELFHEALING_POOL_CRITICAL_THRESHOLD = 90.0           # Line 115
SELFHEALING_POOL_LEAK_THRESHOLD_SECONDS = 300.0      # Line 116
```

#### 1.6 settings/namespace_emergency.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_namespace_emergency_settings.py`

```python
# 필요한 설정 필드 (services/namespace_emergency/*.py 기반)
# 파일: services/namespace_emergency/cascade_detector.py
SELFHEALING_NAMESPACE_EMERGENCY_ESCALATION_THRESHOLD = 2     # Line 44
SELFHEALING_NAMESPACE_EMERGENCY_CASCADE_WINDOW_MINUTES = 30  # Line 47
# 파일: services/namespace_emergency/tracker.py
SELFHEALING_NAMESPACE_EMERGENCY_EXPIRY_HOURS = 8             # Line 48
SELFHEALING_NAMESPACE_EMERGENCY_CACHE_TTL_SECONDS = 30.0     # Line 51
# 파일: services/namespace_emergency/escalation_audit.py
SELFHEALING_NAMESPACE_EMERGENCY_MAX_BUFFER_SIZE = 1000       # Line 203
```

#### 1.7 settings/canary.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_canary_settings.py`

```python
# 필요한 설정 필드 (services/canary/*.py 기반)
# 파일: services/canary/service.py
SELFHEALING_CANARY_ROLLOUT_TTL_DAYS = 7              # Line 103
# 파일: services/canary/cross_cluster.py
SELFHEALING_CANARY_CROSS_CLUSTER_TIMEOUT = 10        # Line 388
SELFHEALING_CANARY_DEFAULT_EXPIRY_HOURS = 24         # Line 599
# 파일: services/canary/locking.py
SELFHEALING_CANARY_LOCK_TIMEOUT_MINUTES = 30         # Line 81
```

#### 1.8 settings/canary_watchdog.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_canary_watchdog_settings.py`

```python
# 필요한 설정 필드 (tasks/canary_watchdog.py 기반)
# 파일: tasks/canary_watchdog.py
SELFHEALING_CANARY_WATCHDOG_ZOMBIE_THRESHOLD_MINUTES = 30    # Line 64
SELFHEALING_CANARY_WATCHDOG_AUTO_ROLLBACK_MINUTES = 60       # Line 65
SELFHEALING_CANARY_WATCHDOG_MAX_STAGE_DURATION_MINUTES = 15  # Line 66
SELFHEALING_CANARY_WATCHDOG_SLACK_CHANNEL = "#selfhealing-alerts"  # Line 70
```

#### 1.9 settings/chaos_safety_caps.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_chaos_safety_caps_settings.py`

```python
# 필요한 설정 필드 (services/chaos/constants.py 기반)
# 파일: services/chaos/constants.py
SELFHEALING_CHAOS_DISK_IO_MAX_LATENCY_MS = 2000              # Line 31
SELFHEALING_CHAOS_DISK_IO_MAX_FAILURE_RATE = 0.30            # Line 34
SELFHEALING_CHAOS_REPLAY_FLOOD_MAX_ENTRIES = 5000            # Line 37
SELFHEALING_CHAOS_REPLAY_FLOOD_MAX_RATE = 500                # Line 40
SELFHEALING_CHAOS_CLOCK_SKEW_MAX_SECONDS = 86400             # Line 43
SELFHEALING_CHAOS_BLACKHOLE_MAX_DURATION_SECONDS = 300       # Line 46
SELFHEALING_CHAOS_POOL_EXHAUSTION_MAX_SECONDS = 120          # Line 52
SELFHEALING_CHAOS_POOL_EXHAUSTION_MAX_PERCENTAGE = 0.50      # Line 55
SELFHEALING_CHAOS_TLS_FAILURE_MAX_SECONDS = 180              # Line 58
SELFHEALING_CHAOS_TLS_FAILURE_MAX_RATE = 0.25                # Line 61
```

#### 1.10 settings/audit_reconciler.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_audit_reconciler_settings.py`

```python
# 필요한 설정 필드 (audit/reconciler.py 기반)
# 파일: audit/reconciler.py
SELFHEALING_AUDIT_RECONCILER_CHECK_INTERVAL_SECONDS = 300.0  # Line 41
SELFHEALING_AUDIT_RECONCILER_CHECK_WINDOW_SECONDS = 3600.0   # Line 44
SELFHEALING_AUDIT_RECONCILER_RESEND_BATCH_SIZE = 50          # Line 47
SELFHEALING_AUDIT_RECONCILER_MAX_RESEND_ATTEMPTS = 3         # Line 50
SELFHEALING_AUDIT_RECONCILER_ALERT_THRESHOLD = 10            # Line 53
SELFHEALING_AUDIT_RECONCILER_MAX_CONFIRMED_IDS = 10000       # Line 170
```

#### 1.11 settings/jitter.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_jitter_settings.py`

```python
# 필요한 설정 필드 (utils/jitter.py 기반)
# 파일: utils/jitter.py
SELFHEALING_JITTER_MAX_DELAY_SECONDS = 60.0          # Line 27, 75, 99, 122
SELFHEALING_JITTER_MIN_DELAY_SECONDS = 0.0           # Line 28, 76, 100, 123
```

#### 1.12 settings/gate_fault.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_gate_fault_settings.py`

```python
# 필요한 설정 필드 (services/error_budget_gate/fault_detector.py 기반)
# 파일: services/error_budget_gate/fault_detector.py
SELFHEALING_GATE_FAULT_FAILURE_THRESHOLD = 5         # Line 46
SELFHEALING_GATE_FAULT_RECOVERY_TIMEOUT = 30         # Line 46
```

#### 1.13 settings/forensic.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_forensic_settings.py`

```python
# 필요한 설정 필드 (services/forensic_audit_bridge.py 기반)
# 파일: services/forensic_audit_bridge.py
SELFHEALING_FORENSIC_EXCEPTION_LIMIT = 10            # Line 59
SELFHEALING_FORENSIC_SNAPSHOT_LIMIT = 1              # Line 60
SELFHEALING_FORENSIC_ANOMALY_LIMIT = 5               # Line 61
SELFHEALING_FORENSIC_WINDOW_SECONDS = 60.0           # Line 64
```

#### 1.14 settings/graceful_degradation.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_graceful_degradation_settings.py`

```python
# 필요한 설정 필드 (audit/graceful_degradation/enums.py 기반)
# 파일: audit/graceful_degradation/enums.py - FallbackConfig
SELFHEALING_GRACEFUL_DEGRADATION_REDIS_TIMEOUT_SECONDS = 5.0     # Line 45
SELFHEALING_GRACEFUL_DEGRADATION_REPLICA_TIMEOUT_SECONDS = 3.0   # Line 46
SELFHEALING_GRACEFUL_DEGRADATION_MEMORY_MAX_ENTRIES = 10000      # Line 48
# 파일: audit/graceful_degradation/enums.py - CircuitBreakerConfig
SELFHEALING_GRACEFUL_DEGRADATION_CB_FAILURE_THRESHOLD = 5        # Line 55
SELFHEALING_GRACEFUL_DEGRADATION_CB_RECOVERY_TIMEOUT = 30.0      # Line 56
SELFHEALING_GRACEFUL_DEGRADATION_CB_HALF_OPEN_REQUESTS = 3       # Line 57
SELFHEALING_GRACEFUL_DEGRADATION_CB_SUCCESS_THRESHOLD = 2        # Line 58
```

#### 1.15 settings/ring_buffer.py (신규)
- [ ] 파일 생성
- [ ] 테스트 파일: `tests/unit/settings/test_ring_buffer_settings.py`

```python
# 필요한 설정 필드 (audit/ring_buffer.py 기반)
# 파일: audit/ring_buffer.py
SELFHEALING_RING_BUFFER_CAPACITY = 10000             # Line 67
SELFHEALING_RING_BUFFER_BATCH_MAX_SIZE = 100         # Line 60
```

---

### Phase 2: 기존 Settings 확장 [예상: 3시간]

#### 2.1 settings/error_budget_propagation.py 확장
- [ ] 필드 추가
- [ ] 테스트 업데이트: `tests/unit/settings/test_error_budget_propagation_settings.py`

```python
# 추가할 설정 필드 (services/error_budget/constants.py 기반)
# 파일: services/error_budget/constants.py
SELFHEALING_ERRORBUDGET_MAX_CRISIS_MULTIPLIER_CAP = 10.0     # Line 34
SELFHEALING_ERRORBUDGET_MAX_DOMAIN_MULTIPLIER = 24.0         # Line 42
SELFHEALING_ERRORBUDGET_MAX_COMBINED_MULTIPLIER = 10.0       # Line 50
SELFHEALING_ERRORBUDGET_CACHE_TTL_SECONDS = 30.0             # Line 62
SELFHEALING_ERRORBUDGET_REFUND_RATIO = 0.5                   # Line 146
SELFHEALING_ERRORBUDGET_REFUND_EXPIRY_HOURS = 24             # Line 153
```

#### 2.2 settings/slo.py 확장
- [ ] 필드 추가
- [ ] 테스트 업데이트: `tests/unit/settings/test_slo_settings.py`

```python
# 추가할 설정 필드 (slo.py 기반)
# 파일: slo.py
SELFHEALING_SLO_FAST_BURN_RATE = 14.4                # Line 95
SELFHEALING_SLO_SLOW_BURN_RATE = 3.0                 # Line 96
```

#### 2.3 settings/throttle.py 확장
- [ ] 필드 추가
- [ ] 테스트 업데이트: `tests/unit/settings/test_throttle_settings.py`

```python
# 추가할 설정 필드 (services/throttle/adaptive.py 기반)
# 파일: services/throttle/adaptive.py
SELFHEALING_THROTTLE_SMOOTHING_FACTOR = 0.5          # Line 62
SELFHEALING_THROTTLE_SAMPLE_WINDOW_SECONDS = 10.0    # Line 63
SELFHEALING_THROTTLE_MIN_SAMPLES = 3                 # Line 64
```

#### 2.4 settings/chaos_blast_radius.py 확장
- [ ] 필드 추가
- [ ] 테스트 업데이트: `tests/unit/settings/test_chaos_blast_radius_settings.py`

```python
# 추가할 설정 필드 (services/chaos/blast_radius.py 기반)
# 파일: services/chaos/blast_radius.py
SELFHEALING_CHAOS_ALLOWED_HOURS_START = 2            # Line 86
SELFHEALING_CHAOS_ALLOWED_HOURS_END = 6              # Line 90
SELFHEALING_CHAOS_MAX_FAILURE_PERCENT = 5.0          # Line 180
```

#### 2.5 settings/critical_worker.py 확장
- [ ] 필드 추가
- [ ] 테스트 업데이트: `tests/unit/settings/test_critical_worker_settings.py`

```python
# 추가할 설정 필드 (services/coordination/critical_worker.py 기반)
# 파일: services/coordination/critical_worker.py
# 큐별 설정은 JSON 형태로 저장
SELFHEALING_CRITICAL_WORKER_QUEUE_CONFIGS = {
    "critical": {"worker_count": 2, "concurrency": 2, "prefetch_multiplier": 1},
    "high": {"worker_count": 4, "concurrency": 4, "prefetch_multiplier": 2},
    "recovery": {"worker_count": 4, "concurrency": 4, "prefetch_multiplier": 2},
    "notifications": {"worker_count": 2, "concurrency": 4, "prefetch_multiplier": 4},
    "default": {"worker_count": 8, "concurrency": 8, "prefetch_multiplier": 4},
}
```

#### 2.6 settings/anti_flapping.py 확장
- [ ] 필드 추가
- [ ] 테스트 업데이트: `tests/unit/settings/test_anti_flapping_settings.py`

```python
# 추가할 설정 필드 (services/idempotency_service.py 기반)
# 파일: services/idempotency_service.py - AntiFlappingWindow
SELFHEALING_ANTI_FLAPPING_WINDOW_SECONDS = 60        # Line 774
SELFHEALING_ANTI_FLAPPING_SIMILARITY_THRESHOLD = 0.01  # Line 775
SELFHEALING_ANTI_FLAPPING_MAX_SIMILAR_CHANGES = 3    # Line 776
```

#### 2.7 settings/retry.py 확장 (또는 신규)
- [ ] 필드 추가
- [ ] 테스트: `tests/unit/settings/test_retry_settings.py`

```python
# 추가할 설정 필드 (services/retry_handler.py 기반)
# 파일: services/retry_handler.py - RetryConfig
SELFHEALING_RETRY_MAX_ATTEMPTS = 3                   # Line 76
SELFHEALING_RETRY_BACKOFF_BASE = 4                   # Line 77
SELFHEALING_RETRY_BACKOFF_MAX = 180                  # Line 78
SELFHEALING_RETRY_JITTER_PERCENT = 25                # Line 79
```

#### 2.8 settings/governance.py (신규 또는 확장)
- [ ] 파일 생성/확장
- [ ] 테스트: `tests/unit/settings/test_governance_settings.py`

```python
# 필요한 설정 필드 (services/governance_checks.py 기반)
# 파일: services/governance_checks.py
SELFHEALING_GOVERNANCE_EMERGENCY_MIN_LEVEL = 2       # Line 340, 403, 535, 621
```

#### 2.9 settings/sampling.py (신규)
- [ ] 파일 생성
- [ ] 테스트: `tests/unit/settings/test_sampling_settings.py`

```python
# 필요한 설정 필드 (audit/performance/sampling.py 기반)
# 파일: audit/performance/sampling.py
SELFHEALING_SAMPLING_SAMPLE_RATE = 0.1               # Line 20
SELFHEALING_SAMPLING_MIN_SAMPLES = 10                # Line 21
SELFHEALING_SAMPLING_MAX_SAMPLES = 1000              # Line 22
```

#### 2.10 settings/steady_state.py (신규)
- [ ] 파일 생성
- [ ] 테스트: `tests/unit/settings/test_steady_state_settings.py`

```python
# 필요한 설정 필드 (services/chaos/base/models.py 기반)
# 파일: services/chaos/base/models.py - SteadyStateHypothesis
SELFHEALING_STEADY_STATE_P50_LATENCY_MAX_MS = 100.0      # Line 186
SELFHEALING_STEADY_STATE_P99_LATENCY_MAX_MS = 500.0      # Line 187
SELFHEALING_STEADY_STATE_ERROR_RATE_MAX_PERCENT = 0.1    # Line 191
SELFHEALING_STEADY_STATE_THROUGHPUT_MIN_RPS = 100.0      # Line 194
SELFHEALING_STEADY_STATE_INJECTION_RATE = 0.001          # Line 35
```

---

### Phase 3: 소스 파일 리팩토링 [예상: 25시간]

#### 3.1 Core 모듈 (5개 파일)
- [ ] `core/backoff.py` - Settings 연동
- [ ] `core/pool_monitor.py` - Settings 연동
- [ ] `core/connection_health.py` - Settings 연동
- [ ] `core/decision_engine.py` - Settings 연동
- [ ] `core/apply_strategy.py` - Settings 연동

#### 3.2 Services 모듈 (30개+ 파일)
- [ ] `services/cleanup_service.py` - Settings 연동
- [ ] `services/pending_config.py` - Settings 연동
- [ ] `services/precomputed_cache.py` - Settings 연동
- [ ] `services/retry_handler.py` - Settings 연동
- [ ] `services/governance_checks.py` - Settings 연동
- [ ] `services/forensic_audit_bridge.py` - Settings 연동
- [ ] `services/idempotency_service.py` - Settings 연동
- [ ] `services/error_budget/constants.py` - Settings 연동
- [ ] `services/error_budget/backfill.py` - Settings 연동
- [ ] `services/error_budget_gate/fault_detector.py` - Settings 연동
- [ ] `services/namespace_emergency/cascade_detector.py` - Settings 연동
- [ ] `services/namespace_emergency/tracker.py` - Settings 연동
- [ ] `services/namespace_emergency/escalation_audit.py` - Settings 연동
- [ ] `services/canary/service.py` - Settings 연동
- [ ] `services/canary/cross_cluster.py` - Settings 연동
- [ ] `services/canary/locking.py` - Settings 연동
- [ ] `services/throttle/adaptive.py` - Settings 연동
- [ ] `services/chaos/constants.py` - Settings 연동
- [ ] `services/chaos/traffic_shaper.py` - Settings 연동
- [ ] `services/chaos/blast_radius.py` - Settings 연동
- [ ] `services/chaos/base/models.py` - Settings 연동
- [ ] `services/coordination/critical_worker.py` - Settings 연동
- [ ] `services/coordination/recovery_tasks.py` - Settings 연동
- [ ] `services/isolation/regional_gate.py` - Settings 연동
- [ ] `services/control_api_service.py` - Settings 연동
- [ ] `services/finops/service.py` - Settings 연동

#### 3.3 Audit 모듈 (15개+ 파일)
- [ ] `audit/reconciler.py` - Settings 연동 (from_env → from_settings)
- [ ] `audit/sync_worker.py` - Settings 연동 (from_env → from_settings)
- [ ] `audit/audit_integration.py` - Settings 연동
- [ ] `audit/audit_watchdog.py` - Settings 연동 (from_env → from_settings)
- [ ] `audit/ring_buffer.py` - Settings 연동
- [ ] `audit/performance/sampling.py` - Settings 연동
- [ ] `audit/cascade_load_shedding.py` - Settings 연동
- [ ] `audit/self_audit.py` - Settings 연동
- [ ] `audit/graceful_degradation/enums.py` - Settings 연동

#### 3.4 Tasks 모듈 (8개 파일)
- [ ] `tasks/cleanup_tasks.py` - Settings 연동
- [ ] `tasks/canary_watchdog.py` - Settings 연동
- [ ] `tasks/drift_detection.py` - Settings 연동
- [ ] `tasks/intelligence_tasks.py` - Settings 연동
- [ ] `tasks/traffic_aware_replay.py` - Settings 연동

#### 3.5 API 모듈 (6개 파일)
- [ ] `api/django/stress_views.py` - Settings 연동
- [ ] `api/django/permissions.py` - Settings 연동
- [ ] `api/django/views/canary.py` - Settings 연동
- [ ] `api/django/views/auto_tuning.py` - Settings 연동
- [ ] `api/django/views/xtest/observability.py` - Settings 연동

#### 3.6 Utils 모듈 (2개 파일)
- [ ] `utils/jitter.py` - Settings 연동

#### 3.7 SLO 모듈 (1개 파일)
- [ ] `slo.py` - Settings 연동

---

### Phase 4: 테스트 파일 작성/업데이트 [예상: 10시간]

#### 4.1 신규 Settings 테스트 파일 (15개)
- [ ] `tests/unit/settings/test_stress_test_settings.py`
- [ ] `tests/unit/settings/test_cleanup_settings.py`
- [ ] `tests/unit/settings/test_precomputed_cache_settings.py`
- [ ] `tests/unit/settings/test_backoff_settings.py`
- [ ] `tests/unit/settings/test_pool_monitor_settings.py`
- [ ] `tests/unit/settings/test_namespace_emergency_settings.py`
- [ ] `tests/unit/settings/test_canary_settings.py`
- [ ] `tests/unit/settings/test_canary_watchdog_settings.py`
- [ ] `tests/unit/settings/test_chaos_safety_caps_settings.py`
- [ ] `tests/unit/settings/test_audit_reconciler_settings.py`
- [ ] `tests/unit/settings/test_jitter_settings.py`
- [ ] `tests/unit/settings/test_gate_fault_settings.py`
- [ ] `tests/unit/settings/test_forensic_settings.py`
- [ ] `tests/unit/settings/test_graceful_degradation_settings.py`
- [ ] `tests/unit/settings/test_ring_buffer_settings.py`

#### 4.2 기존 Settings 테스트 업데이트 (10개)
- [ ] `tests/unit/settings/test_error_budget_propagation_settings.py` - 업데이트
- [ ] `tests/unit/settings/test_slo_settings.py` - 업데이트
- [ ] `tests/unit/settings/test_throttle_settings.py` - 업데이트
- [ ] `tests/unit/settings/test_chaos_blast_radius_settings.py` - 업데이트
- [ ] `tests/unit/settings/test_critical_worker_settings.py` - 업데이트
- [ ] `tests/unit/settings/test_anti_flapping_settings.py` - 업데이트
- [ ] `tests/unit/settings/test_retry_settings.py` - 신규/업데이트
- [ ] `tests/unit/settings/test_governance_settings.py` - 신규
- [ ] `tests/unit/settings/test_sampling_settings.py` - 신규
- [ ] `tests/unit/settings/test_steady_state_settings.py` - 신규

#### 4.3 통합 테스트 (2개)
- [ ] `tests/integration/test_settings_env_override.py` - 환경 변수 오버라이드 테스트
- [ ] `tests/integration/test_settings_from_settings_pattern.py` - from_settings 패턴 테스트

---

### Phase 5: 최종 검증 [예상: 4시간]

- [ ] 전체 테스트 실행: `pytest tests/`
- [ ] 환경 변수 오버라이드 동작 확인
- [ ] 기존 기본값과 동일한지 확인
- [ ] 문서 최종 업데이트

---

## 3. 테스트 파일 템플릿

### 3.1 Settings 테스트 템플릿

```python
"""
Tests for {module_name} settings.

File: tests/unit/settings/test_{module_name}_settings.py
"""
import os
from unittest import mock

import pytest

from selfhealing.settings.{module_name} import (
    {SettingsClass},
    get_{module_name}_settings,
    reset_{module_name}_settings,
)


class Test{SettingsClass}:
    """Test {SettingsClass} Pydantic settings."""

    def setup_method(self):
        """Reset settings before each test."""
        reset_{module_name}_settings()

    def teardown_method(self):
        """Reset settings after each test."""
        reset_{module_name}_settings()

    def test_default_values(self):
        """Test default values are set correctly."""
        settings = get_{module_name}_settings()
        
        # 기본값 검증
        assert settings.field_name == expected_default_value

    def test_env_override(self):
        """Test environment variable override."""
        with mock.patch.dict(os.environ, {
            "SELFHEALING_{MODULE}_FIELD_NAME": "new_value"
        }):
            reset_{module_name}_settings()
            settings = get_{module_name}_settings()
            
            assert settings.field_name == "new_value"

    def test_validation(self):
        """Test validation rules."""
        # 유효성 검증 테스트
        pass

    def test_singleton_pattern(self):
        """Test singleton pattern works correctly."""
        settings1 = get_{module_name}_settings()
        settings2 = get_{module_name}_settings()
        
        assert settings1 is settings2
```

### 3.2 리팩토링된 소스 파일 테스트 템플릿

```python
"""
Tests for {module_name} with Settings integration.

File: tests/unit/{path}/test_{module_name}_settings_integration.py
"""
import os
from unittest import mock

import pytest


class Test{ClassName}SettingsIntegration:
    """Test {ClassName} uses Settings correctly."""

    def test_uses_settings_defaults(self):
        """Test class uses Settings default values."""
        pass

    def test_settings_override_works(self):
        """Test Settings override is reflected in class behavior."""
        pass

    def test_from_settings_factory(self):
        """Test from_settings() factory method."""
        pass
```

---

## 4. 리팩토링 패턴 가이드

### 4.1 함수 파라미터 기본값 패턴

**Before:**
```python
def cleanup_expired(self, max_age_hours: int = 24) -> int:
    ...
```

**After:**
```python
from selfhealing.settings.cleanup import get_cleanup_settings

def cleanup_expired(self, max_age_hours: int = None) -> int:
    if max_age_hours is None:
        max_age_hours = get_cleanup_settings().expired_config_hours
    ...
```

### 4.2 dataclass 기본값 패턴

**Before:**
```python
@dataclass
class Config:
    timeout: int = 30
```

**After:**
```python
from selfhealing.settings.module import get_module_settings

@dataclass
class Config:
    timeout: int = field(default_factory=lambda: get_module_settings().timeout)
    
    @classmethod
    def from_settings(cls, settings=None, **overrides):
        s = settings or get_module_settings()
        return cls(
            timeout=overrides.get("timeout", s.timeout),
        )
```

### 4.3 모듈 레벨 상수 패턴

**Before:**
```python
DEFAULT_TIMEOUT = 30
```

**After:**
```python
from selfhealing.settings.module import get_module_settings

def _get_default_timeout() -> int:
    return get_module_settings().timeout

# 또는 lazy initialization
_DEFAULT_TIMEOUT = None

def get_default_timeout() -> int:
    global _DEFAULT_TIMEOUT
    if _DEFAULT_TIMEOUT is None:
        _DEFAULT_TIMEOUT = get_module_settings().timeout
    return _DEFAULT_TIMEOUT
```

---

## 5. 파일 위치 참조

| 문서 | 내용 |
|------|------|
| [94_HARDCODED_CONFIG_REFACTORING_OVERVIEW.md](94_HARDCODED_CONFIG_REFACTORING_OVERVIEW.md) | 전체 개요 및 Phase 정의 |
| [95_HARDCODED_CONFIG_API_VIEWS.md](95_HARDCODED_CONFIG_API_VIEWS.md) | API 뷰 상세 |
| [96_HARDCODED_CONFIG_SERVICES.md](96_HARDCODED_CONFIG_SERVICES.md) | 서비스 계층 상세 |
| [97_HARDCODED_CONFIG_TASKS.md](97_HARDCODED_CONFIG_TASKS.md) | 태스크 상세 |
| [98_HARDCODED_CONFIG_AUDIT.md](98_HARDCODED_CONFIG_AUDIT.md) | Audit 모듈 상세 |
| [99_HARDCODED_CONFIG_FUNCTIONS.md](99_HARDCODED_CONFIG_FUNCTIONS.md) | 함수 파라미터 상세 |
| [100_HARDCODED_CONFIG_ADDITIONAL.md](100_HARDCODED_CONFIG_ADDITIONAL.md) | 추가 발견 항목 상세 |

---

## 6. 진행 상태 추적

### 현재 진행률

| Phase | 상태 | 완료율 |
|-------|------|--------|
| Phase 1: Settings 생성 | 미시작 | 0% |
| Phase 2: Settings 확장 | 미시작 | 0% |
| Phase 3: 소스 리팩토링 | 미시작 | 0% |
| Phase 4: 테스트 작성 | 미시작 | 0% |
| Phase 5: 최종 검증 | 미시작 | 0% |

### 마지막 업데이트
- **날짜**: 2026-01-25
- **작성자**: AI Assistant
- **다음 작업**: Phase 1.1 시작 (settings/stress_test.py 생성)

---

## 7. 주의사항

1. **순환 참조 방지**: Settings 모듈은 다른 비즈니스 모듈에 의존하지 않아야 함
2. **하위 호환성**: 기존 함수 시그니처 유지, 기본값 동일하게 설정
3. **테스트 우선**: 각 Settings 파일 생성 후 바로 테스트 작성
4. **점진적 리팩토링**: 한 번에 너무 많은 파일 수정하지 않기
5. **커밋 단위**: Phase 또는 모듈 단위로 커밋
