# Configuration Implementation Guide

> 하드코딩된 설정값을 계층형 설정 시스템으로 마이그레이션하기 위한 구현 가이드

## 1. 구현 순서 개요

```
Phase 1: 기존 인프라 확인 및 확장 준비
    ↓
Phase 2: 새 설정 타입 정의 (Pydantic Settings)
    ↓
Phase 3: RuntimeConfigManager 등록
    ↓
Phase 4: 하드코딩 값 대체
    ↓
Phase 5: API 엔드포인트 추가
    ↓
Phase 6: 테스트 및 검증
```

---

## 2. Phase 1: 기존 인프라 확인 및 확장 준비

### 2.1 확인 사항

기존 코드에서 이미 구현된 인프라:

| 컴포넌트 | 파일 위치 | 역할 |
|----------|----------|------|
| `LayeredProvider` | `selfhealing/settings/layered_provider.py` | 4계층 설정 병합 |
| `BaseConfigManager` | `selfhealing/services/runtime_config/base.py` | 런타임 설정 CRUD |
| `STORAGE_KEYS` | `selfhealing/services/runtime_config/constants.py` | Redis 저장소 키 |
| `CONFIG_CLASSES` | `selfhealing/services/runtime_config/constants.py` | 설정 클래스 매핑 |
| `StateBackend` | `selfhealing/core/state_backend.py` | 영속화 인터페이스 |

### 2.2 확장 포인트

새 설정 타입 추가 시 수정해야 할 파일:

1. **설정 클래스 정의**: `selfhealing/settings/` 디렉토리
2. **STORAGE_KEYS 등록**: `selfhealing/services/runtime_config/constants.py`
3. **CONFIG_CLASSES 등록**: `selfhealing/services/runtime_config/constants.py`
4. **\_\_init\_\_.py 내보내기**: `selfhealing/settings/__init__.py`

### 2.3 작업 체크리스트

- [ ] 기존 `STORAGE_KEYS` 목록 확인 (현재 17개 등록)
- [ ] 기존 `CONFIG_CLASSES` 목록 확인
- [ ] 새로 추가할 설정 카테고리 결정
- [ ] 네이밍 규칙 확인 (`{category}Settings` 클래스명, `runtime_config:{category}` 키)

---

## 3. Phase 2: 새 설정 타입 정의

### 3.1 Pydantic Settings 클래스 작성 패턴

기존 `CircuitBreakerSettings` 패턴을 따름:

```
파일: selfhealing/settings/{category}.py

필수 요소:
- pydantic_settings.BaseSettings 상속
- model_config에 env_prefix 설정
- Field()로 기본값, 설명, 검증 규칙 정의
```

### 3.2 새로 정의할 설정 클래스 목록 (1차-9차 분석 기반)

#### 3.2.1 기본 설정 클래스 (6개)

| 클래스명 | 용도 | env_prefix |
|----------|------|------------|
| `DashboardSettings` | 대시보드 캐시 TTL | `SELFHEALING_DASHBOARD_` |
| `RecoverySettings` | 복구 관련 파라미터 | `SELFHEALING_RECOVERY_` |
| `BatchSettings` | 배치 처리 크기 | `SELFHEALING_BATCH_` |
| `AuditSettings` | 감사 로그 설정 | `SELFHEALING_AUDIT_` |
| `CeleryTaskSettings` | 태스크 기본 설정 | `SELFHEALING_TASK_` |
| `ApiViewSettings` | API 페이징 설정 | `SELFHEALING_API_` |

#### 3.2.2 Error Budget 관련 (4개)

| 클래스명 | 용도 | env_prefix |
|----------|------|------------|
| `ErrorBudgetSettings` | 버짓 임계치/소진율 | `SELFHEALING_ERROR_BUDGET_` |
| `ErrorBudgetPropagationSettings` | 전파 설정 (decay, max_hops) | `SELFHEALING_PROPAGATION_` |
| `DomainSensitivitySettings` | 도메인별 민감도 가중치 | `SELFHEALING_DOMAIN_` |
| `EmergencyLevelMultiplierSettings` | 비상 레벨 승수 | `SELFHEALING_LEVEL_` |

#### 3.2.3 Chaos Engineering 관련 (3개)

| 클래스명 | 용도 | env_prefix |
|----------|------|------------|
| `ChaosExperimentSettings` | SLA, grace period, TTL | `SELFHEALING_CHAOS_` |
| `ChaosBlastRadiusSettings` | 폭발 반경 제한 | `SELFHEALING_BLAST_RADIUS_` |
| `ChaosHardCapsSettings` | 하드 캡 제한 | `SELFHEALING_CHAOS_CAPS_` |

#### 3.2.4 Corruption Shield (2개)

| 클래스명 | 용도 | env_prefix |
|----------|------|------------|
| `CorruptionShieldSettings` | 이상치 탐지 z-score, IQR | `SELFHEALING_CORRUPTION_` |
| `AmountValidationSettings` | 금액 유효 범위 | `SELFHEALING_AMOUNT_` |

#### 3.2.5 Coordination 관련 (5개)

| 클래스명 | 용도 | env_prefix |
|----------|------|------------|
| `AntiFlappingSettings` | 쿨다운, 히스테리시스 | `SELFHEALING_FLAPPING_` |
| `DistributedLockSettings` | 분산 락 타임아웃 | `SELFHEALING_LOCK_` |
| `CriticalWorkerSettings` | 큐 설정, 워커 수 | `SELFHEALING_WORKER_` |
| `RecoveryAccountabilitySettings` | 타임아웃, 에스컬레이션 | `SELFHEALING_ACCOUNTABILITY_` |
| `RegionalRecoveryPolicySettings` | 지역별 복구 정책 | `SELFHEALING_REGIONAL_` |

#### 3.2.6 Notification 관련 (2개)

| 클래스명 | 용도 | env_prefix |
|----------|------|------------|
| `NotificationChannelSettings` | 채널 매핑, 임계치 | `SELFHEALING_NOTIFICATION_` |
| `SlackChannelSettings` | Slack 채널별 설정 | `SELFHEALING_SLACK_` |

#### 3.2.7 DLQ/Throttle 관련 (2개)

| 클래스명 | 용도 | env_prefix |
|----------|------|------------|
| `DLQSettings` | DLQ 보관, 재시도 | `SELFHEALING_DLQ_` |
| `ThrottleSettings` | Netflix Gradient 설정 | `SELFHEALING_THROTTLE_` |

#### 3.2.8 Storage/Retention 관련 (2개)

| 클래스명 | 용도 | env_prefix |
|----------|------|------------|
| `CascadeRetentionSettings` | Hot/Warm/Cold 보관 | `SELFHEALING_CASCADE_` |
| `AuditIntegritySettings` | Sequence TTL, Cold Storage | `SELFHEALING_INTEGRITY_` |

### 3.3 필드 정의 시 고려사항

| 항목 | 설명 | 예시 |
|------|------|------|
| 기본값 | 현재 하드코딩된 값 사용 | `Field(default=30)` |
| 설명 | 필드 용도 명시 | `description="캐시 TTL (초)"` |
| 검증 | 범위 제한 | `Field(ge=1, le=3600)` |
| 환경변수 | 자동 매핑 | `env_prefix` 설정으로 자동 |

### 3.4 작업 체크리스트 (총 20개 클래스)

#### 3.4.1 기본 설정 클래스 (6개)

- [ ] `DashboardSettings` 클래스 생성
- [ ] `RecoverySettings` 클래스 생성
- [ ] `BatchSettings` 클래스 생성
- [ ] `AuditSettings` 클래스 생성
- [ ] `CeleryTaskSettings` 클래스 생성
- [ ] `ApiViewSettings` 클래스 생성

#### 3.4.2 Error Budget 관련 (4개)

- [ ] `ErrorBudgetSettings` 클래스 생성
- [ ] `ErrorBudgetPropagationSettings` 클래스 생성
- [ ] `DomainSensitivitySettings` 클래스 생성
- [ ] `EmergencyLevelMultiplierSettings` 클래스 생성

#### 3.4.3 Chaos Engineering 관련 (3개)

- [ ] `ChaosExperimentSettings` 클래스 생성
- [ ] `ChaosBlastRadiusSettings` 클래스 생성
- [ ] `ChaosHardCapsSettings` 클래스 생성

#### 3.4.4 Corruption Shield 관련 (2개)

- [ ] `CorruptionShieldSettings` 클래스 생성
- [ ] `AmountValidationSettings` 클래스 생성

#### 3.4.5 Coordination 관련 (5개)

- [ ] `AntiFlappingSettings` 클래스 생성
- [ ] `DistributedLockSettings` 클래스 생성
- [ ] `CriticalWorkerSettings` 클래스 생성
- [ ] `RecoveryAccountabilitySettings` 클래스 생성
- [ ] `RegionalRecoveryPolicySettings` 클래스 생성

#### 3.4.6 Notification 관련 (2개)

- [ ] `NotificationChannelSettings` 클래스 생성
- [ ] `SlackChannelSettings` 클래스 생성

#### 3.4.7 DLQ/Throttle 관련 (2개)

- [ ] `DLQSettings` 클래스 생성
- [ ] `ThrottleSettings` 클래스 생성

#### 3.4.8 Storage/Retention 관련 (2개)

- [ ] `CascadeRetentionSettings` 클래스 생성
- [ ] `AuditIntegritySettings` 클래스 생성

#### 3.4.9 Export 및 마무리

- [ ] `selfhealing/settings/__init__.py`에 26개 클래스 내보내기 추가

---

## 4. Phase 3: RuntimeConfigManager 등록

### 4.1 STORAGE_KEYS 추가 (기본 6개)

`selfhealing/services/runtime_config/constants.py` 파일 수정:

```
기본 추가 키:
- "dashboard": "runtime_config:dashboard"
- "recovery": "runtime_config:recovery"
- "batch": "runtime_config:batch"
- "audit": "runtime_config:audit"
- "task": "runtime_config:task"
- "api_view": "runtime_config:api_view"
```

### 4.2 STORAGE_KEYS 추가 (1차-9차 분석 14개)

```
Error Budget 관련:
- "error_budget": "runtime_config:error_budget"
- "error_budget_propagation": "runtime_config:error_budget_propagation"
- "domain_sensitivity": "runtime_config:domain_sensitivity"
- "emergency_level": "runtime_config:emergency_level"

Chaos Engineering 관련:
- "chaos_experiment": "runtime_config:chaos_experiment"
- "chaos_blast_radius": "runtime_config:chaos_blast_radius"
- "chaos_hard_caps": "runtime_config:chaos_hard_caps"

Corruption Shield 관련:
- "corruption_shield": "runtime_config:corruption_shield"
- "amount_validation": "runtime_config:amount_validation"

Coordination 관련:
- "anti_flapping": "runtime_config:anti_flapping"
- "distributed_lock": "runtime_config:distributed_lock"
- "critical_worker": "runtime_config:critical_worker"
- "recovery_accountability": "runtime_config:recovery_accountability"
- "regional_recovery": "runtime_config:regional_recovery"

Notification 관련:
- "notification_channel": "runtime_config:notification_channel"
- "slack_channel": "runtime_config:slack_channel"

DLQ/Throttle 관련:
- "dlq": "runtime_config:dlq"
- "throttle": "runtime_config:throttle"

Storage/Retention 관련:
- "cascade_retention": "runtime_config:cascade_retention"
- "audit_integrity": "runtime_config:audit_integrity"
```

### 4.3 CONFIG_CLASSES 추가

동일 파일에서 클래스 매핑 추가:

```
기본 매핑 (6개):
- "dashboard": DashboardSettings
- "recovery": RecoverySettings
- "batch": BatchSettings
- "audit": AuditSettings
- "task": CeleryTaskSettings
- "api_view": ApiViewSettings

1차-9차 분석 매핑 (14개):
- "error_budget": ErrorBudgetSettings
- "error_budget_propagation": ErrorBudgetPropagationSettings
- "domain_sensitivity": DomainSensitivitySettings
- "emergency_level": EmergencyLevelMultiplierSettings
- "chaos_experiment": ChaosExperimentSettings
- "chaos_blast_radius": ChaosBlastRadiusSettings
- "chaos_hard_caps": ChaosHardCapsSettings
- "corruption_shield": CorruptionShieldSettings
- "amount_validation": AmountValidationSettings
- "anti_flapping": AntiFlappingSettings
- "distributed_lock": DistributedLockSettings
- "critical_worker": CriticalWorkerSettings
- "recovery_accountability": RecoveryAccountabilitySettings
- "regional_recovery": RegionalRecoveryPolicySettings
- "notification_channel": NotificationChannelSettings
- "slack_channel": SlackChannelSettings
- "dlq": DLQSettings
- "throttle": ThrottleSettings
- "cascade_retention": CascadeRetentionSettings
- "audit_integrity": AuditIntegritySettings
```

### 4.4 import 문 추가

`constants.py` 상단에 새 클래스 import 추가

### 4.5 작업 체크리스트

- [ ] `STORAGE_KEYS` 딕셔너리에 기본 6개 키 추가
- [ ] `STORAGE_KEYS` 딕셔너리에 1차-9차 14개 키 추가
- [ ] `CONFIG_CLASSES` 딕셔너리에 기본 6개 매핑 추가
- [ ] `CONFIG_CLASSES` 딕셔너리에 1차-9차 14개 매핑 추가
- [ ] import 문 추가
- [ ] 기존 테스트 통과 확인

---

## 5. Phase 4: 하드코딩 값 대체

### 5.1 대체 패턴

**Before (하드코딩)**:
```
CACHE_TTL_SECONDS = 30
```

**After (LayeredProvider 사용)**:
```
settings = get_layered_settings(DashboardSettings, "dashboard")
cache_ttl = settings.cache_ttl_seconds
```

### 5.2 우선순위별 마이그레이션

#### Phase 4.1: CRITICAL 설정 (즉시)

| 파일 | 설정 | 대체 방식 |
|------|------|----------|
| `circuit_breaker.py` | `failure_threshold` | 이미 `CircuitBreakerSettings` 사용 중 |
| `anti_flapping.py` | `anti_flapping_window` | `RecoverySettings.anti_flapping_window` |
| `recovery_coordinator.py` | `max_recovery_attempts` | `RecoverySettings.max_attempts` |
| `error_budget_gate.py` | `error_rate_threshold` | `ErrorBudgetSettings.threshold` |

#### Phase 4.2: HIGH 설정

| 파일 | 설정 | 대체 방식 |
|------|------|----------|
| `dashboard_service.py` | `CACHE_TTL_*` | `DashboardSettings.cache_ttl_*` |
| `idempotency_service.py` | `default_cache_ttl` | 이미 config 사용 중 |
| 다수 파일 | `batch_size` | `BatchSettings.default_batch_size` |
| task 파일들 | `max_retries` | `CeleryTaskSettings.max_retries` |

#### Phase 4.3: MEDIUM 설정

| 파일 | 설정 | 대체 방식 |
|------|------|----------|
| `async_logger.py` | `BATCH_SIZE` | `BatchSettings.logger_batch_size` |
| `tracker.py` | `CACHE_TTL_SECONDS` | `DashboardSettings.tracker_cache_ttl` |
| API 뷰들 | `default_limit` | `ApiViewSettings.default_limit` |

#### Phase 4.4: LOW 설정

| 파일 | 설정 | 대체 방식 |
|------|------|----------|
| pending_config.py | `MAX_HISTORY` | `AuditSettings.max_history` |
| audit 관련 | `retention_days` | `AuditSettings.retention_days` |

### 5.3 1차-9차 분석 기반 상세 마이그레이션 목록

#### 5.3.1 Error Budget 관련 마이그레이션

| 파일 | 현재 값 | 대체 방식 |
|------|--------|----------|
| `error_budget_gate.py` | `warning_threshold=0.8` | `ErrorBudgetSettings.warning_threshold` |
| `error_budget_gate.py` | `critical_threshold=0.5` | `ErrorBudgetSettings.critical_threshold` |
| `error_budget_propagation.py` | `decay_factor=0.8` | `ErrorBudgetPropagationSettings.decay_factor` |
| `error_budget_propagation.py` | `max_hops=3` | `ErrorBudgetPropagationSettings.max_hops` |
| `error_budget_propagation.py` | `propagation_delay_ms=100` | `ErrorBudgetPropagationSettings.delay_ms` |
| `domain_weights.py` | `PAYMENT=2.0, ORDER=1.5...` | `DomainSensitivitySettings.weights` |
| `safe_defaults.py` | `emergency_multipliers={}` | `EmergencyLevelMultiplierSettings.multipliers` |

#### 5.3.2 Chaos Engineering 마이그레이션

| 파일 | 현재 값 | 대체 방식 |
|------|--------|----------|
| `chaos_experiment_manager.py` | `max_duration_seconds=3600` | `ChaosExperimentSettings.max_duration` |
| `chaos_experiment_manager.py` | `grace_period_seconds=60` | `ChaosExperimentSettings.grace_period` |
| `chaos_experiment_manager.py` | `result_ttl=86400` | `ChaosExperimentSettings.result_ttl` |
| `blast_radius.py` | `max_affected_services=0.3` | `ChaosBlastRadiusSettings.max_services_ratio` |
| `blast_radius.py` | `max_concurrent=5` | `ChaosBlastRadiusSettings.max_concurrent` |
| `chaos_hard_caps.py` | 여러 하드 캡 | `ChaosHardCapsSettings.*` |

#### 5.3.3 Corruption Shield 마이그레이션

| 파일 | 현재 값 | 대체 방식 |
|------|--------|----------|
| `corruption_shield.py` | `z_score_threshold=3.0` | `CorruptionShieldSettings.z_score_threshold` |
| `corruption_shield.py` | `iqr_multiplier=1.5` | `CorruptionShieldSettings.iqr_multiplier` |
| `corruption_shield.py` | `min_samples=30` | `CorruptionShieldSettings.min_samples` |
| `corruption_shield.py` | `quarantine_ttl=3600` | `CorruptionShieldSettings.quarantine_ttl` |
| `amount_validator.py` | `min_valid_amount=100` | `AmountValidationSettings.min_amount` |
| `amount_validator.py` | `max_valid_amount=10000000` | `AmountValidationSettings.max_amount` |

#### 5.3.4 Coordination 마이그레이션

| 파일 | 현재 값 | 대체 방식 |
|------|--------|----------|
| `anti_flapping.py` | `cooldown_period=300` | `AntiFlappingSettings.cooldown_period` |
| `anti_flapping.py` | `hysteresis_margin=0.1` | `AntiFlappingSettings.hysteresis_margin` |
| `anti_flapping.py` | `max_transitions=5` | `AntiFlappingSettings.max_transitions` |
| `distributed_lock.py` | `lock_timeout=30` | `DistributedLockSettings.lock_timeout` |
| `distributed_lock.py` | `retry_interval=0.1` | `DistributedLockSettings.retry_interval` |
| `critical_worker.py` | `RECOVERY_CRITICAL_QUEUE` | `CriticalWorkerSettings.queue_name` |
| `accountability.py` | `escalation_timeout=1800` | `RecoveryAccountabilitySettings.escalation_timeout` |

#### 5.3.5 Notification 마이그레이션

| 파일 | 현재 값 | 대체 방식 |
|------|--------|----------|
| `notification_config.py` | 채널 매핑 | `NotificationChannelSettings.channel_map` |
| `notification_config.py` | `rate_limit_per_minute=10` | `NotificationChannelSettings.rate_limit` |
| `notification_config.py` | `max_retry=3` | `NotificationChannelSettings.max_retry` |
| `slack_integration.py` | 채널 설정 | `SlackChannelSettings.*` |

#### 5.3.6 DLQ/Throttle 마이그레이션

| 파일 | 현재 값 | 대체 방식 |
|------|--------|----------|
| `dlq_models.py` | `retention_hours=168` | `DLQSettings.retention_hours` |
| `dlq_models.py` | `max_retry_attempts=3` | `DLQSettings.max_retry` |
| `dlq_models.py` | `replay_batch_size=100` | `DLQSettings.replay_batch_size` |
| `throttle/config.py` | `min_rtt_ms=50` | `ThrottleSettings.min_rtt_ms` |
| `throttle/config.py` | `tolerance=2.0` | `ThrottleSettings.tolerance` |
| `throttle/config.py` | `probe_multiplier=2.0` | `ThrottleSettings.probe_multiplier` |
| `throttle/config.py` | `smoothing=0.2` | `ThrottleSettings.smoothing` |

#### 5.3.7 Storage/Retention 마이그레이션

| 파일 | 현재 값 | 대체 방식 |
|------|--------|----------|
| `cascade_storage.py` | `hot_tier_days=7` | `CascadeRetentionSettings.hot_tier_days` |
| `cascade_storage.py` | `warm_tier_days=30` | `CascadeRetentionSettings.warm_tier_days` |
| `cascade_storage.py` | `cold_tier_days=365` | `CascadeRetentionSettings.cold_tier_days` |
| `audit_integrity.py` | `sequence_ttl=86400` | `AuditIntegritySettings.sequence_ttl` |
| `audit_integrity.py` | `verification_interval=3600` | `AuditIntegritySettings.verification_interval` |

### 5.4 작업 체크리스트

- [ ] CRITICAL 설정 마이그레이션 (4개 파일)
- [ ] HIGH 설정 마이그레이션 (10+ 파일)
- [ ] MEDIUM 설정 마이그레이션 (15+ 파일)
- [ ] LOW 설정 마이그레이션 (5+ 파일)
- [ ] Error Budget 관련 마이그레이션 (7개 항목)
- [ ] Chaos Engineering 마이그레이션 (6개 항목)
- [ ] Corruption Shield 마이그레이션 (6개 항목)
- [ ] Coordination 마이그레이션 (7개 항목)
- [ ] Notification 마이그레이션 (4개 항목)
- [ ] DLQ/Throttle 마이그레이션 (7개 항목)
- [ ] Storage/Retention 마이그레이션 (5개 항목)
- [ ] 각 단계 후 단위 테스트 실행

---

## 6. Phase 5: API 엔드포인트 추가

### 6.1 기존 API 패턴 확인

현재 `selfhealing/api/django/views/` 디렉토리에 설정 관련 API가 있음:

- `config/` 디렉토리 내 ViewSet들
- `RuntimeConfigManager` 연동 패턴 참조

### 6.2 새 엔드포인트 추가

#### 6.2.1 기본 설정 엔드포인트 (6개)

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/api/v1/config/dashboard/` | GET/PUT | 대시보드 설정 |
| `/api/v1/config/recovery/` | GET/PUT | 복구 설정 |
| `/api/v1/config/batch/` | GET/PUT | 배치 설정 |
| `/api/v1/config/audit/` | GET/PUT | 감사 설정 |
| `/api/v1/config/task/` | GET/PUT | 태스크 설정 |
| `/api/v1/config/api-view/` | GET/PUT | API 뷰 설정 |

#### 6.2.2 Error Budget 엔드포인트 (4개)

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/api/v1/config/error-budget/` | GET/PUT | Error Budget 임계치 |
| `/api/v1/config/error-budget-propagation/` | GET/PUT | 전파 설정 |
| `/api/v1/config/domain-sensitivity/` | GET/PUT | 도메인 민감도 |
| `/api/v1/config/emergency-level/` | GET/PUT | 비상 레벨 승수 |

#### 6.2.3 Chaos Engineering 엔드포인트 (3개)

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/api/v1/config/chaos-experiment/` | GET/PUT | 실험 설정 |
| `/api/v1/config/chaos-blast-radius/` | GET/PUT | 폭발 반경 |
| `/api/v1/config/chaos-hard-caps/` | GET/PUT | 하드 캡 |

#### 6.2.4 Corruption Shield 엔드포인트 (2개)

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/api/v1/config/corruption-shield/` | GET/PUT | 이상치 탐지 |
| `/api/v1/config/amount-validation/` | GET/PUT | 금액 검증 |

#### 6.2.5 Coordination 엔드포인트 (5개)

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/api/v1/config/anti-flapping/` | GET/PUT | Anti-Flapping |
| `/api/v1/config/distributed-lock/` | GET/PUT | 분산 락 |
| `/api/v1/config/critical-worker/` | GET/PUT | 크리티컬 워커 |
| `/api/v1/config/recovery-accountability/` | GET/PUT | 복구 책임 |
| `/api/v1/config/regional-recovery/` | GET/PUT | 지역별 복구 |

#### 6.2.6 기타 엔드포인트 (6개)

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/api/v1/config/notification-channel/` | GET/PUT | 알림 채널 |
| `/api/v1/config/slack-channel/` | GET/PUT | Slack 채널 |
| `/api/v1/config/dlq/` | GET/PUT | DLQ 설정 |
| `/api/v1/config/throttle/` | GET/PUT | 스로틀 설정 |
| `/api/v1/config/cascade-retention/` | GET/PUT | Cascade 보관 |
| `/api/v1/config/audit-integrity/` | GET/PUT | 감사 무결성 |

### 6.3 권한 검증

- 기존 RBAC 시스템 연동
- `config:read`, `config:write` 권한 확인

### 6.4 작업 체크리스트

- [ ] 기본 설정 (6개) Serializer 및 ViewSet 생성
- [ ] Error Budget (4개) Serializer 및 ViewSet 생성
- [ ] Chaos Engineering (3개) Serializer 및 ViewSet 생성
- [ ] Corruption Shield (2개) Serializer 및 ViewSet 생성
- [ ] Coordination (5개) Serializer 및 ViewSet 생성
- [ ] 기타 (6개) Serializer 및 ViewSet 생성
- [ ] URL 라우팅 추가 (총 26개 엔드포인트)
- [ ] 권한 데코레이터 적용
- [ ] OpenAPI 스키마 업데이트

---

## 7. Phase 6: 테스트 및 검증

### 7.1 단위 테스트

| 테스트 대상 | 검증 내용 |
|------------|----------|
| Settings 클래스 | 기본값, 검증 규칙, 환경변수 매핑 |
| RuntimeConfigManager | CRUD, 영속화, 이력 추적 |
| LayeredProvider | 계층 우선순위, 병합 로직 |
| API 엔드포인트 | 권한, 요청/응답 형식 |

### 7.2 통합 테스트

| 시나리오 | 검증 내용 |
|----------|----------|
| 설정 변경 후 반영 | 런타임 설정 변경이 즉시 적용되는지 |
| 환경변수 오버라이드 | ENV 값이 기본값을 오버라이드하는지 |
| Request 오버라이드 | 요청별 오버라이드가 작동하는지 |
| 재시작 후 유지 | Redis/DB 저장 설정이 유지되는지 |

### 7.3 부하 테스트

| 시나리오 | 검증 내용 |
|----------|----------|
| 설정 읽기 성능 | 캐시 적용 여부, 응답 시간 |
| 동시 변경 | 동시성 제어, 락 동작 |
| 대량 설정 | 많은 설정 타입 동시 사용 |

### 7.4 작업 체크리스트

- [ ] 각 Settings 클래스 단위 테스트 작성
- [ ] RuntimeConfigManager 통합 테스트 작성
- [ ] API 엔드포인트 테스트 작성
- [ ] 기존 테스트 모두 통과 확인
- [ ] 부하 테스트 시나리오 실행

---

## 8. 롤백 전략

### 8.1 단계별 롤백

각 Phase는 독립적으로 롤백 가능:

| Phase | 롤백 방법 |
|-------|----------|
| Phase 2 | 새 Settings 클래스 파일 삭제 |
| Phase 3 | constants.py 원복 |
| Phase 4 | 개별 파일 git revert |
| Phase 5 | API 엔드포인트 제거 |

### 8.2 비상 롤백

- Feature flag로 런타임 설정 사용 여부 제어
- `SELFHEALING_USE_RUNTIME_CONFIG=false` 환경변수로 비활성화

---

## 9. 10차~14차 분석 신규 설정 구현 순서

### 9.1 CRITICAL 우선순위 (즉시 구현)

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `failure_rate_threshold` | `recovery_circuit_breaker.py` | `0.3` | `RecoveryCircuitBreakerSettings` |
| `failure_count_threshold` | `recovery_circuit_breaker.py` | `10` | `RecoveryCircuitBreakerSettings` |
| `memory_critical_threshold` | `redis_key_guard.py` | `90.0` | `RedisKeyGuardSettings` |
| `max_shutdown_wait_seconds` | `recovery_shutdown.py` | `600.0` | `RecoveryShutdownSettings` |
| `circuit_failure_threshold` | `resilient_recorder.py` | `3` | `ResilientRecorderSettings` |

### 9.2 HIGH 우선순위 (1주 내)

#### 9.2.1 Security 설정

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `rate_limit_per_minute` | `security/models.py` | `60` | `SecuritySettings` |
| `ip_ban_threshold` | `security/models.py` | `10` | `SecuritySettings` |
| `ip_ban_duration_minutes` | `security/models.py` | `30` | `SecuritySettings` |
| `injection_max_depth` | `security/models.py` | `10` | `SecuritySettings` |
| `max_key_length` | `security/models.py` | `100` | `SecuritySettings` |
| `max_value_length` | `security/models.py` | `10000` | `SecuritySettings` |

#### 9.2.2 Recovery Coordination 설정

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `recovery_extension_seconds` | `recovery_shutdown.py` | `300.0` | `RecoveryShutdownSettings` |
| `memory_warning_threshold` | `redis_key_guard.py` | `80.0` | `RedisKeyGuardSettings` |
| `sampling_window_seconds` | `recovery_circuit_breaker.py` | `60` | `RecoveryCircuitBreakerSettings` |
| `recovery_hysteresis_factor` | `anti_flapping.py` | `1.15` | `AntiFlappingSettings` |
| `HEARTBEAT_INTERVAL_SECONDS` | `partition_reconciliation.py` | `10` | `PartitionSettings` |
| `PARTITION_DETECTION_THRESHOLD` | `partition_reconciliation.py` | `30` | `PartitionSettings` |

#### 9.2.3 Cache/TTL 설정

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `L1_TTL_SECONDS` | `precomputed_cache.py` | `2.0` | `CacheSettings` |
| `L2_TTL_SECONDS` | `precomputed_cache.py` | `15.0` | `CacheSettings` |
| `REFRESH_INTERVAL` | `precomputed_cache.py` | `10.0` | `CacheSettings` |
| `check_interval_seconds` | `reconciler.py` | `300.0` | `AuditReconcilerSettings` |
| `sync_interval_seconds` | `layered_repository.py` | `5.0` | `RepositorySettings` |

#### 9.2.4 Batch/Flush 설정

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `batch_size` | `batch_writer.py` | `100` | `BatchFlushSettings` |
| `flush_interval_seconds` | `batch_writer.py` | `10.0` | `BatchFlushSettings` |
| `buffer_capacity` | `resilient_recorder.py` | `10000` | `ResilientRecorderSettings` |
| `flush_batch_size` | `resilient_recorder.py` | `100` | `ResilientRecorderSettings` |

### 9.3 MEDIUM 우선순위 (2주 내)

#### 9.3.1 Regional Recovery Policy 설정

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `max_recovery_duration_minutes` | `regional_recovery_policy.py` | `60` | `RegionalRecoverySettings` |
| `cooldown_minutes` | `regional_recovery_policy.py` | `15` | `RegionalRecoverySettings` |
| `max_concurrent_recoveries` | `regional_recovery_policy.py` | `3` | `RegionalRecoverySettings` |
| `auto_approve_threshold` | `regional_recovery_policy.py` | `0.1` | `RegionalRecoverySettings` |

#### 9.3.2 Chaos Blast Radius 설정

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `max_traffic_percent` | `blast_radius.py` | `1.0~10.0` | `ChaosBlastRadiusSettings` |
| `max_concurrent_experiments` | `blast_radius.py` | `1~5` | `ChaosBlastRadiusSettings` |
| `maintenance_window_start_hour` | `blast_radius.py` | `2` | `ChaosBlastRadiusSettings` |
| `maintenance_window_end_hour` | `blast_radius.py` | `6` | `ChaosBlastRadiusSettings` |

#### 9.3.3 Jitter 설정

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `max_delay_seconds` | `jitter.py` | `60.0` | `JitterSettings` |
| `jitter_percent` | `retry_handler.py` | `25` | `RetrySettings` |
| `jitter_percent` | `rate_limit_coordinator.py` | `30.0` | `RateLimitSettings` |
| `jitter_factor` | `backoff.py` | `0.2` | `BackoffSettings` |

#### 9.3.4 Throttle 설정

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `smoothing_factor` | `config.py` | `0.5` | `ThrottleSettings` |
| `tolerance` | `config.py` | `1.5` | `ThrottleSettings` |
| `initial_limit` | `config.py` | `20` | `ThrottleSettings` |
| `min_limit` | `config.py` | `1` | `ThrottleSettings` |
| `max_limit` | `config.py` | `1000` | `ThrottleSettings` |

### 9.4 LOW 우선순위 (1개월 내)

| 설정 | 파일 | 현재 값 | 구현 클래스 |
|------|------|---------|------------|
| `DEFAULT_TSA_URLS` | `signed_manifest.py` | `[...]` | `TSASettings` |
| `DEFAULT_STALE_THRESHOLD_MINUTES` | `recovery_dashboard.py` | `30` | `DashboardSettings` |
| `MAX_RECONCILIATION_ACTIONS` | `partition_reconciliation.py` | `100` | `PartitionSettings` |
| Histogram buckets | `definitions.py` | 다양 | 코드 상수 유지 권장 |

### 9.5 구현 체크리스트

#### Phase A: Settings 클래스 생성 (9개 신규)

- [ ] `SecuritySettings` - 보안 관련 6개 설정
- [ ] `RecoveryCircuitBreakerSettings` - CB 3개 설정
- [ ] `RedisKeyGuardSettings` - Redis 메모리 2개 설정
- [ ] `RecoveryShutdownSettings` - 셧다운 3개 설정
- [ ] `ResilientRecorderSettings` - 감사 기록기 7개 설정
- [ ] `PartitionSettings` - 파티션 복구 3개 설정
- [ ] `ChaosBlastRadiusSettings` - Chaos 4개 설정
- [ ] `BatchFlushSettings` - 배치 플러시 3개 설정
- [ ] `JitterSettings` - Jitter 4개 설정

#### Phase B: constants.py 등록

- [ ] 9개 STORAGE_KEYS 추가
- [ ] 9개 CONFIG_CLASSES 매핑 추가

#### Phase C: 하드코딩 값 대체

- [ ] CRITICAL 5개 파일 마이그레이션
- [ ] HIGH 12개 파일 마이그레이션
- [ ] MEDIUM 8개 파일 마이그레이션
- [ ] LOW 4개 파일 마이그레이션

#### Phase D: 테스트

- [ ] 각 Settings 클래스 단위 테스트
- [ ] 환경변수 오버라이드 테스트
- [ ] 기존 기능 회귀 테스트

---

## 10. 다음 단계

1. Phase 1부터 순차적으로 진행
2. 각 Phase 완료 후 PR 생성 및 리뷰
3. staging 환경에서 검증 후 production 배포
4. [93_CONFIG_MIGRATION_CHECKLIST.md](93_CONFIG_MIGRATION_CHECKLIST.md)로 진행 상황 추적
