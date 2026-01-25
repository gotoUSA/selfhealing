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

### Phase 1: 신규 Settings 모듈 생성 [완료: 2026-01-25]

#### 1.1 settings/stress_test.py ✅
- [x] 파일 생성: `settings/stress_test.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestStressTestSettings`

설정 필드 (services/stress_test_service.py):
- `default_lock_timeout_ms`: Lock 획득 기본 타임아웃 (기본 1ms)
- `max_burst_duration_seconds`: Burst Failure 최대 지속 시간 (기본 30초)
- `max_concurrent_locks`: 동시 Lock 시도 최대 수 (기본 100)
- `default_leak_hold_seconds`: Connection Leak 시뮬레이션 유지 시간 (기본 30초)
- `inter_request_sleep_ms`: 요청 간 대기 시간 (기본 10ms)

#### 1.2 settings/cleanup.py ✅
- [x] 파일 생성: `settings/cleanup.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestCleanupSettings`

설정 필드 (services/cleanup_service.py, tasks/cleanup_tasks.py):
- `archive_older_than_days`: DLQ 아카이브 기준 일수 (기본 30일)
- `expired_config_hours`: Pending Config 만료 기준 (기본 24시간)
- `approval_expiry_hours`: 승인 요청 만료 기준 (기본 72시간)
- `purge_older_than_days`: 아카이브 영구 삭제 기준 (기본 90일)

#### 1.3 settings/precomputed_cache.py ✅
- [x] 파일 생성: `settings/precomputed_cache.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestPrecomputedCacheSettings`

설정 필드 (services/precomputed_cache.py):
- `l1_ttl_seconds`: L1 In-Process 캐시 TTL (기본 2.0초)
- `l1_maxsize`: L1 캐시 최대 항목 수 (기본 100)
- `l2_ttl_seconds`: L2 Redis 캐시 TTL (기본 15.0초)
- `refresh_interval_seconds`: 백그라운드 갱신 주기 (기본 10.0초)

#### 1.4 settings/backoff.py ✅
- [x] 파일 생성: `settings/backoff.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestBackoffSettings`

설정 필드 (core/backoff.py):
- Exponential: `base_delay=1.0`, `max_delay=300.0`, `multiplier=2.0`, `jitter_factor=0.2`
- Linear: `base_delay=1.0`, `increment=1.0`, `max_delay=60.0`, `jitter_factor=0.1`
- Constant: `delay=5.0`, `jitter_factor=0.1`
- Legacy: `base=4`, `max_delay=180`, `jitter_percent=25`, `min_delay=1`

#### 1.5 settings/pool_monitor.py ✅
- [x] 파일 생성: `settings/pool_monitor.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestPoolMonitorSettings`

설정 필드 (core/pool_monitor.py):
- `warning_threshold`: Pool 사용률 경고 임계값 (기본 70.0%)
- `critical_threshold`: Pool 사용률 위험 임계값 (기본 90.0%)
- `leak_threshold_seconds`: 누수 의심 기준 시간 (기본 300.0초)
- `max_history`: 통계 히스토리 최대 개수 (기본 100)

#### 1.6 settings/namespace_emergency.py ✅
- [x] 파일 생성: `settings/namespace_emergency.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestNamespaceEmergencySettings`

설정 필드 (services/namespace_emergency/*.py):
- `escalation_threshold`: GLOBAL 격상 임계값 (기본 2개 리전)
- `cascade_window_minutes`: Cascade 판단 윈도우 (기본 30분)
- `expiry_hours`: Emergency 상태 만료 시간 (기본 8시간)
- `cache_ttl_seconds`: 로컬 캐시 TTL (기본 30.0초)
- `max_buffer_size`: 감사 이벤트 버퍼 최대 크기 (기본 1000)

#### 1.7 settings/canary.py ✅
- [x] 파일 생성: `settings/canary.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestCanarySettings`

설정 필드 (services/canary/*.py):
- `rollout_ttl_days`: 롤아웃 데이터 보관 기간 (기본 7일)
- `lock_timeout_minutes`: Config Lock 만료 시간 (기본 30분)
- `cross_cluster_timeout_seconds`: 크로스 클러스터 타임아웃 (기본 10초)
- `default_expiry_hours`: 전파 요청 만료 시간 (기본 24시간)

#### 1.8 settings/canary_watchdog.py ✅
- [x] 파일 생성: `settings/canary_watchdog.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestCanaryWatchdogSettings`

설정 필드 (tasks/canary_watchdog.py WatchdogConfig):
- `zombie_threshold_minutes`: 정체 판단 시간 (기본 30분)
- `auto_rollback_after_minutes`: 자동 롤백 대기 시간 (기본 60분)
- `max_stage_duration_minutes`: 단계별 최대 체류 시간 (기본 15분)
- `enable_auto_promote/rollback`: 자동 프로모션/롤백 활성화
- `slack_channel`: 알림 채널 (기본 #selfhealing-alerts)

#### 1.9 settings/chaos_safety_caps.py ✅
- [x] 파일 생성: `settings/chaos_safety_caps.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestChaosSafetyCapsSettings`

설정 필드 (services/chaos/constants.py ExperimentHardCaps):
- DiskIO: `max_latency_ms=2000`, `max_failure_rate=0.30`
- ReplayFlood: `max_entries=5000`, `max_rate=500`
- ClockSkew: `max_seconds=86400`
- Blackhole: `max_duration_seconds=300`
- PoolExhaustion: `max_duration_seconds=120`, `max_percentage=0.50`
- TLSFailure: `max_duration_seconds=180`, `max_rate=0.25`

#### 1.10 settings/audit_reconciler.py ✅
- [x] 파일 생성: `settings/audit_reconciler.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestAuditReconcilerSettings`

설정 필드 (audit/reconciler.py ReconcilerConfig):
- `check_interval_seconds`: 검증 주기 (기본 300.0초)
- `check_window_seconds`: 검증 범위 (기본 3600.0초)
- `resend_batch_size`: 재전송 배치 크기 (기본 50)
- `max_resend_attempts`: 최대 재전송 시도 (기본 3회)
- `alert_threshold`: 알림 임계값 (기본 10)
- `max_confirmed_ids`: 확인된 ID 캐시 최대 크기 (기본 10000)

#### 1.11 settings/jitter.py ✅
- [x] 파일 생성: `settings/jitter.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestJitterSettings`

설정 필드 (utils/jitter.py):
- `max_delay_seconds`: 최대 지연 시간 (기본 60.0초)
- `min_delay_seconds`: 최소 지연 시간 (기본 0.0초)
- `startup_max_delay_seconds`: 시작 시 최대 지연 (기본 30.0초)
- `enabled`: Jitter 활성화 여부 (기본 True)

#### 1.12 settings/gate_fault.py ✅
- [x] 파일 생성: `settings/gate_fault.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestGateFaultSettings`

설정 필드 (services/error_budget_gate/fault_detector.py GateFaultDetector):
- `failure_threshold`: DEGRADED 전환 임계값 (기본 5회)
- `recovery_timeout_seconds`: 복구 대기 시간 (기본 30초)

#### 1.13 settings/forensic.py (기존 파일 존재 - Phase 2로 연기)
- [x] 기존 파일 확인: `settings/forensic.py` 이미 존재
- [ ] Phase 2에서 ForensicRateLimiter 관련 필드 추가 예정

기존 forensic.py에 추가 필요 필드 (services/forensic_audit_bridge.py ForensicRateLimiter):
- `exception_limit`: 분당 예외 캡처 최대 횟수 (기본 10)
- `snapshot_limit`: 분당 스냅샷 최대 횟수 (기본 1)
- `anomaly_limit`: 분당 이상 탐지 최대 횟수 (기본 5)
- `window_seconds`: 윈도우 크기 (기본 60.0초)

#### 1.14 settings/graceful_degradation.py ✅
- [x] 파일 생성: `settings/graceful_degradation.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestGracefulDegradationSettings`

설정 필드 (audit/graceful_degradation/enums.py):
- FallbackConfig: `redis_timeout=5.0`, `replica_timeout=3.0`, `memory_max_entries=10000`
- CircuitBreakerConfig: `failure_threshold=5`, `recovery_timeout=30.0`, `half_open_requests=3`, `success_threshold=2`

#### 1.15 settings/ring_buffer.py ✅
- [x] 파일 생성: `settings/ring_buffer.py`
- [x] 테스트: `tests/unit/settings/test_phase1_settings.py::TestRingBufferSettings`

설정 필드 (audit/ring_buffer.py RingBuffer):
- `capacity`: 버퍼 최대 용량 (기본 10000)
- `batch_max_size`: 배치 처리 최대 항목 수 (기본 100)
- `strategy`: 배압 전략 (기본 drop_oldest)

---

### Phase 2: 기존 Settings 확장 [완료: 2026-01-25]

#### 2.1 settings/error_budget_propagation.py 확장 ✅
- [x] 필드 추가
- [x] 테스트: `tests/unit/settings/test_phase2_settings.py::TestErrorBudgetPropagationSettingsExtension`

설정 필드 (services/error_budget/constants.py 기반):
- `max_crisis_multiplier_cap`: Emergency Level 기반 최대 가중치 Cap (기본 10.0)
- `max_domain_multiplier`: 도메인 기반 최대 가중치 (기본 24.0)
- `max_combined_multiplier`: Level + Domain 결합 후 최대 가중치 (기본 10.0)
- `default_cache_ttl_seconds`: CrisisMultiplierProvider 기본 캐시 TTL (기본 30.0초)
- `refund_ratio`: 오탐 시 기본 환불 비율 (기본 0.5)
- `refund_proposal_expiry_hours`: 환불 제안 만료 시간 (기본 24시간)
- `default_combine_strategy`: Level/Domain 가중치 결합 기본 전략 (기본 "max")

#### 2.2 settings/slo.py 확장 ⏭️ (이미 완료)
- [x] 필드 확인: `default_fast_burn_rate`, `default_slow_burn_rate` 이미 존재
- 추가 작업 불필요

#### 2.3 settings/throttle.py 확장 ✅
- [x] 필드 추가
- [x] 테스트: `tests/unit/settings/test_phase2_settings.py::TestThrottleSettingsExtension`

설정 필드 (services/throttle/adaptive.py GradientCalculator 기반):
- `sample_window_seconds`: RTT 샘플 윈도우 크기 (기본 10.0초)
- `gradient_min_samples`: 그래디언트 계산에 필요한 최소 샘플 수 (기본 3)

#### 2.4 settings/chaos_blast_radius.py 확장 ⏭️ (이미 완료)
- [x] 필드 확인: `allowed_hours_start`, `allowed_hours_end` 등 모든 필드 이미 존재
- 추가 작업 불필요

#### 2.5 settings/critical_worker.py 확장 ⏭️ (이미 완료)
- [x] 필드 확인: 큐별 worker_count, concurrency, prefetch_multiplier 모든 필드 이미 존재
- 추가 작업 불필요

#### 2.6 settings/anti_flapping.py 확장 ✅
- [x] 필드 추가
- [x] 테스트: `tests/unit/settings/test_phase2_settings.py::TestAntiFlappingSettingsExtension`

설정 필드 (services/idempotency_service.py AntiFlappingWindow 기반):
- `window_seconds`: 슬라이딩 윈도우 크기 (기본 60초)
- `similarity_threshold`: 유사 판정 임계값 (기본 0.01 = 1%)
- `max_similar_changes`: 윈도우 내 최대 유사 변경 횟수 (기본 3)

#### 2.7 settings/retry.py 확장 ⏭️ (이미 완료)
- [x] 필드 확인: `max_attempts`, `backoff_base`, `max_delay`, `jitter_percent` 모든 필드 이미 존재
- 추가 작업 불필요

#### 2.8 settings/governance.py 확장 ✅
- [x] 필드 추가
- [x] 테스트: `tests/unit/settings/test_phase2_settings.py::TestGovernanceSettingsExtension`

설정 필드 (services/governance_checks.py 기반):
- `emergency_min_level`: 거버넌스 체크에서 비상 모드 차단 최소 레벨 (기본 2)

#### 2.9 settings/sampling.py ✅ (신규 생성)
- [x] 파일 생성: `settings/sampling.py`
- [x] 테스트: `tests/unit/settings/test_phase2_settings.py::TestSamplingSettings`

설정 필드 (audit/performance/sampling.py SamplingConfig 기반):
- `sample_rate`: 샘플링 비율 (기본 0.1 = 10%)
- `min_samples`: 최소 샘플 수 (기본 10)
- `max_samples`: 최대 샘플 수 (기본 1000)
- `full_verify_on_failure`: 샘플 검증 실패 시 전체 검증 수행 여부 (기본 True)

#### 2.10 settings/steady_state.py ✅ (신규 생성)
- [x] 파일 생성: `settings/steady_state.py`
- [x] 테스트: `tests/unit/settings/test_phase2_settings.py::TestSteadyStateSettings`

설정 필드 (services/chaos/base/models.py SteadyStateHypothesis 기반):
- `p50_latency_max_ms`: P50 레이턴시 최대 허용값 (기본 100.0ms)
- `p99_latency_max_ms`: P99 레이턴시 최대 허용값 (기본 500.0ms)
- `error_rate_max_percent`: 에러율 최대 허용값 (기본 0.1%)
- `throughput_min_rps`: 최소 처리량 (기본 100.0 rps)

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
| Phase 1: Settings 생성 | ✅ 완료 | 100% (14/14 파일) |
| Phase 2: Settings 확장 | ✅ 완료 | 100% (6/6 작업) |
| Phase 3: 소스 리팩토링 | 미시작 | 0% |
| Phase 4: 테스트 작성 | 부분 완료 | 30% (Phase 1, 2 테스트 완료) |
| Phase 5: 최종 검증 | 미시작 | 0% |

### Phase 1 완료 내역 (2026-01-25)

| Settings 파일 | 테스트 | 상태 |
|--------------|--------|------|
| stress_test.py | ✅ 4 tests | 완료 |
| cleanup.py | ✅ 3 tests | 완료 |
| precomputed_cache.py | ✅ 2 tests | 완료 |
| backoff.py | ✅ 2 tests | 완료 |
| pool_monitor.py | ✅ 3 tests | 완료 |
| namespace_emergency.py | ✅ 2 tests | 완료 |
| canary.py | ✅ 2 tests | 완료 |
| canary_watchdog.py | ✅ 3 tests | 완료 |
| chaos_safety_caps.py | ✅ 2 tests | 완료 |
| audit_reconciler.py | ✅ 2 tests | 완료 |
| jitter.py | ✅ 3 tests | 완료 |
| gate_fault.py | ✅ 3 tests | 완료 |
| graceful_degradation.py | ✅ 2 tests | 완료 |
| ring_buffer.py | ✅ 4 tests | 완료 |

**Phase 1 총 테스트**: 37개 통과

### Phase 2 완료 내역 (2026-01-25)

| 작업 | 테스트 | 상태 |
|-----|--------|------|
| error_budget_propagation.py 확장 | ✅ 3 tests | 완료 |
| throttle.py 확장 | ✅ 2 tests | 완료 |
| anti_flapping.py 확장 | ✅ 2 tests | 완료 |
| governance.py 확장 | ✅ 2 tests | 완료 |
| sampling.py 신규 생성 | ✅ 4 tests | 완료 |
| steady_state.py 신규 생성 | ✅ 5 tests | 완료 |

**Phase 2 총 테스트**: 18개 통과

**전체 테스트 합계**: 55개 통과

### 마지막 업데이트
- **날짜**: 2026-01-25
- **작성자**: AI Assistant
- **완료 작업**: Phase 2 완료 (4개 Settings 확장, 2개 Settings 신규 생성, 18개 테스트 통과)
- **다음 작업**: Phase 3 시작 (소스 파일 리팩토링)

---

## 7. 주의사항

1. **순환 참조 방지**: Settings 모듈은 다른 비즈니스 모듈에 의존하지 않아야 함
2. **하위 호환성**: 기존 함수 시그니처 유지, 기본값 동일하게 설정
3. **테스트 우선**: 각 Settings 파일 생성 후 바로 테스트 작성
4. **점진적 리팩토링**: 한 번에 너무 많은 파일 수정하지 않기
5. **커밋 단위**: Phase 또는 모듈 단위로 커밋
