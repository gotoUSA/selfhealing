# Self-Healing System Phase A/B/C Changelog

> 📅 작성일: 2024-12-24  
> 🎯 목적: Phase A, B, C 구현 이력 기록

---

## Phase A: Critical 수정 (완료 - 2024-12-24)

### 1. TieringMiddleware 구현 ✅

**파일**: `api/django/tiering.py` (신규 생성)

- EmergencyManager와 연동하여 Emergency Level 기반 트래픽 제어
- TierRegistry를 통한 엔드포인트 Tier 분류 (critical/standard/non_essential)
- 확률 기반 Load Shedding (503 응답 + Retry-After 헤더)
- Emergency Level별 Tier 허용 비율 적용

### 2. RuntimeConfigManager 연동 ✅

**파일들**:
- `circuit_breaker/config.py`
- `dlq_models.py` 
- `retry_handler.py`

**변경 내용**:
- `CircuitBreakerConfig.from_settings()`: RuntimeConfigManager 우선 조회, fallback to static config
- `DLQConfig.from_settings()`: RuntimeConfigManager 우선 조회, fallback to static config
- `RetryConfig.from_settings()`: RuntimeConfigManager 우선 조회, fallback to static config

### 3. Kill Switch 체크 추가 ✅

**파일들**:
- `circuit_breaker/manual_control.py`
- `replay_service.py`
- `retry_handler.py`

**변경 내용**:
- `force_open()`, `force_close()`: Kill Switch 체크 후 차단 시 실패 반환
- `replay_single()`, `replay_batch()`: Kill Switch 체크 후 차단
- `RetryHandler.execute()`: Kill Switch 체크 후 즉시 ABORT 반환

---

## Phase B: High 수정 (완료 - 2024-12-24)

### 4. SelfHealingEventBus 구현 ✅

**파일**: `services/event_bus.py` (신규 생성)

**이벤트 타입**:
- EmergencyLevelChanged
- ErrorBudgetCritical
- ErrorBudgetWarning
- CircuitBreakerStateChanged

**기능**:
- Thread-safe 싱글톤 구현
- 우선순위 기반 핸들러 실행
- 이벤트 히스토리 저장
- 간편 함수: `emit_emergency_level_changed()`, `emit_error_budget_critical()` 등

### 5. EmergencyManager 이벤트 발행 ✅

**파일**: `services/emergency_mode.py`

- `activate_manual()`: EmergencyLevelChanged 이벤트 발행
- `activate_auto()`: EmergencyLevelChanged 이벤트 발행
- `_do_deactivate()`: EmergencyLevelChanged 이벤트 발행

### 6. ErrorBudgetGate 이벤트 발행 ✅

**파일**: `services/error_budget_gate.py`

- `_evaluate()`: 예산 < critical → ERROR_BUDGET_CRITICAL 이벤트
- `_evaluate()`: critical < 예산 < warning → ERROR_BUDGET_WARNING 이벤트

### 7. RetryHandler ErrorBudgetGate 체크 ✅

**파일**: `services/retry_handler.py`

- `execute()`: 시작 시 ErrorBudgetGate 체크
- 예산 부족 시 즉시 ABORT 반환 (재시도 차단)

### 8. Conditional Replay ErrorBudgetGate 체크 ✅

**파일**: `adapters/celery/tasks.py`

- CB 복구 시 자동 Replay 전에 ErrorBudgetGate 체크
- 예산 부족 시 Replay 차단 (automation_blocked)

### 9. TTL 캐시 (Check on Use 패턴) ✅

**파일**: `services/emergency_mode.py`

**목적**: Celery 의존 없이 상태 동기화 보장

**동작 방식**:
- `get_current_level()`, `get_state()` 호출 시 TTL 확인
- 캐시 만료 시 (기본 30초) StateBackend 재조회
- 이벤트 버스는 "즉시 캐시 무효화" 역할만 담당

**장점**: Celery Beat 태스크 없이도 최대 30초 내 상태 동기화

---

## Phase C: Medium 개선 (완료 - 2024-12-24)

### 10. Safe Defaults 추가 ✅

**파일**: `core/safe_defaults.py`

**추가된 설정 유형**:

#### governance
```python
{
    "four_eyes_enabled": True,
    "approval_timeout_hours": 24,
    "max_approval_retries": 3,
    "threshold_operator": 2,
    "threshold_admin": 3,
    "emergency_expiry_hours": 4,
    "audit_log_retention_days": 90,
    "require_reason_for_changes": True,
}
```

#### l2_storage
```python
{
    "enabled": False,  # 기본 비활성화
    "redis_timeout_ms": 1000,
    "file_fallback_enabled": True,
    "shadow_log_enabled": True,
    "reconciliation_enabled": True,
    "reconciliation_interval_seconds": 300,
    "reconciliation_jitter_percent": 20,
    "max_retry_on_failure": 3,
    "connection_pool_size": 10,
}
```

#### drift_threshold
```python
{
    "enabled": True,  # 기본 활성화
    "warning_percent": 5.0,
    "critical_percent": 20.0,
    "check_interval_seconds": 60,
    "window_size_seconds": 300,
    "min_samples_required": 10,
    "auto_alert_enabled": True,
    "suppress_duplicate_alerts_seconds": 300,
}
```

**Validation Rules 추가**:
- governance: approval_timeout_hours, emergency_expiry_hours 등
- l2_storage: redis_timeout_ms, connection_pool_size 등
- drift_threshold: warning_percent, critical_percent 등

### 11. config_apply Task 안전 체크 ✅

**파일**: `tasks/config_apply.py`

**추가된 기능**:
- `_is_emergency_blocking()`: LEVEL_2 이상에서 설정 적용 차단
- `apply_pending_config_changes()`: Emergency Mode 체크 추가
- `apply_graceful_config_change()`: Emergency Mode 체크 추가, 재시도 로직

**동작**:
- LEVEL_2 이상에서는 설정 변경 차단 (시스템 안정성 보호)
- 비상 모드 중 재시도하여 비상 모드 해제 후 적용
- EmergencyManager 로드 실패 시 안전하게 허용 (fail-safe)

---

## 테스트 추가

### test_safe_defaults.py
- `TestPhaseCSafeDefaults`: governance, l2_storage, drift_threshold Safe Default 테스트
- `TestPhaseCValidationRules`: 검증 규칙 테스트
- `TestPhaseCGetSafeDefaults`: get_safe_default 함수 테스트

### test_config_apply.py (신규 생성)
- `TestIsEmergencyBlocking`: Emergency Mode 차단 로직 테스트
- `TestApplyPendingConfigChanges`: Celery Task 테스트
- `TestApplyGracefulConfigChange`: Graceful 적용 테스트
- `TestConfigApplyIntegration`: 통합 테스트

---

## 요약

| Phase | 항목 | 상태 |
|-------|------|------|
| A | TieringMiddleware 구현 | ✅ |
| A | RuntimeConfigManager 연동 (CB, DLQ, Retry) | ✅ |
| A | Kill Switch 체크 추가 | ✅ |
| B | SelfHealingEventBus 구현 | ✅ |
| B | EmergencyManager 이벤트 발행 | ✅ |
| B | ErrorBudgetGate 이벤트 발행 | ✅ |
| B | RetryHandler ErrorBudgetGate 체크 | ✅ |
| B | Conditional Replay ErrorBudgetGate 체크 | ✅ |
| B | TTL 캐시 (Check on Use 패턴) | ✅ |
| C | Safe Defaults 추가 (governance, l2_storage, drift_threshold) | ✅ |
| C | config_apply Task 안전 체크 | ✅ |
