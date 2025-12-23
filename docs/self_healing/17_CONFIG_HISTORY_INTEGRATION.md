# ConfigHistoryService Integration Plan

> 설정 변경 시 자동 버전 저장을 위한 ConfigHistoryService 연동 계획

---

## 개요

현재 `ConfigHistoryService`가 구현되어 있지만, 실제 설정 변경 지점들과 연결되어 있지 않아 **수동으로 Rollback API를 호출할 때만** 이력이 저장됩니다.

이 문서는 모든 설정 변경 지점에서 자동으로 버전을 저장하도록 연동하는 계획을 정의합니다.

---

## 현재 상태 분석

### ConfigHistoryService 사용 현황

| 위치 | 사용 여부 | 비고 |
|------|----------|------|
| `config_history.py` (서비스) | ✅ 정의됨 | 핵심 서비스 |
| `views/config_history.py` (API) | ✅ 사용됨 | Rollback API에서만 호출 |
| `runtime_config.py` | ❌ 미연동 | 모든 설정 변경 발생 지점 |
| `reconciliation.py` | ❌ 미연동 | Shadow Budget 반영 시 |
| `emergency_mode.py` | ❌ 미연동 | 비상 모드 활성화/해제 시 |
| `views/config.py` | ❌ 미연동 | REST API 설정 변경 시 |

### 문제점

```
[현재 흐름]
API Request → Config View → RuntimeConfigManager → 설정 변경 + 로깅
                                                    ↓
                                            ConfigHistory 저장 ❌

[목표 흐름]
API Request → Config View → RuntimeConfigManager → 설정 변경 + 로깅
                                                    ↓
                                            ConfigHistory 저장 ✅
```

---

## 연동 대상 (우선순위순)

### P1. RuntimeConfigManager._update_config() - Critical

**현재 코드** (`runtime_config.py:219`):
```python
def _update_config(self, config_type: str, **kwargs) -> Dict[str, Any]:
    """Update config fields."""
    with self._lock:
        current = self._get_config(config_type)
        # ... 필드 업데이트 ...
        self._save_config(config_type, current)
        return current.copy()
```

**변경 후**:
```python
def _update_config(
    self, 
    config_type: str, 
    changed_by: str = "system",
    reason: str = "",
    **kwargs
) -> Dict[str, Any]:
    """Update config fields with history tracking."""
    with self._lock:
        current = self._get_config(config_type)
        previous = current.copy()  # 변경 전 스냅샷
        
        # ... 필드 업데이트 ...
        
        self._save_config(config_type, current)
        
        # ✅ ConfigHistory에 자동 저장
        self._save_to_history(
            config_type=config_type,
            values=current,
            changed_by=changed_by,
            reason=reason or f"Updated: {list(kwargs.keys())}",
        )
        
        return current.copy()

def _save_to_history(
    self,
    config_type: str,
    values: Dict[str, Any],
    changed_by: str,
    reason: str,
) -> None:
    """Save config version to history (best-effort)."""
    try:
        from selfhealing.services.config_history import get_config_history_service
        history_service = get_config_history_service()
        history_service.save_version(
            config_type=config_type,
            values=values,
            changed_by=changed_by,
            reason=reason,
        )
    except Exception as e:
        # Graceful degradation - 히스토리 저장 실패해도 설정 변경은 성공
        logger.warning(f"[RuntimeConfig] Failed to save history: {e}")
```

**영향 범위**:
- `update_circuit_breaker_config()`
- `update_dlq_config()`
- `update_retry_config()`
- `update_sla_config()`
- `update_rate_limit_config()`
- `update_security_config()`
- `update_idempotency_config()`
- `update_notification_config()`
- `update_forensic_config()`
- `update_logging_config()`
- `update_metrics_config()`
- `update_error_budget_config()`

---

### P2. ReconciliationService._apply_to_primary() - Critical

**현재 코드** (`reconciliation.py:779`):
```python
def _apply_to_primary(self, shadow: ShadowBudget) -> None:
    """Primary Budget에 조정 적용."""
    adjustment = shadow.adjustment_percent
    
    # Capped 모드 적용
    if self._config.apply_mode == ApplyMode.CAPPED:
        max_adj = self._config.max_adjustment_percent_per_cycle
        if adjustment > max_adj:
            adjustment = max_adj
    
    # 적용 콜백 호출
    if self._apply_adjustment:
        self._apply_adjustment(adjustment)
        shadow.status = ReconciliationStatus.APPLIED
```

**변경 후**:
```python
def _apply_to_primary(self, shadow: ShadowBudget) -> None:
    """Primary Budget에 조정 적용 + 이력 저장."""
    adjustment = shadow.adjustment_percent
    
    # Capped 모드 적용
    if self._config.apply_mode == ApplyMode.CAPPED:
        max_adj = self._config.max_adjustment_percent_per_cycle
        if adjustment > max_adj:
            adjustment = max_adj
    
    # 적용 콜백 호출
    if self._apply_adjustment:
        self._apply_adjustment(adjustment)
        shadow.status = ReconciliationStatus.APPLIED
    
    # ✅ ConfigHistory에 기록
    self._save_reconciliation_to_history(shadow, adjustment)

def _save_reconciliation_to_history(
    self, 
    shadow: ShadowBudget, 
    adjustment: float
) -> None:
    """Reconciliation 결과를 ConfigHistory에 저장."""
    try:
        from selfhealing.services.config_history import get_config_history_service
        history_service = get_config_history_service()
        history_service.save_version(
            config_type="error_budget",
            values={
                "reconciliation_id": shadow.calculation_id,
                "failsafe_period_id": shadow.failsafe_period_id,
                "adjustment_percent": adjustment,
                "previous_remaining": shadow.primary_remaining_percent,
                "new_remaining": shadow.primary_remaining_percent - adjustment,
                "apply_mode": self._config.apply_mode.value,
            },
            changed_by=shadow.reviewed_by or "system",
            reason=f"Shadow Budget Reconciliation: {shadow.review_justification or 'approved'}",
        )
    except Exception as e:
        logger.warning(f"[Reconciliation] Failed to save history: {e}")
```

---

### P3. GracefulDegradationManager - High

**현재 코드** (`emergency_mode.py`):
- `activate_manual()`: 내부 `_history` 리스트에만 저장
- `deactivate()`: 내부 `_history` 리스트에만 저장

**변경 후**:
```python
# emergency_mode.py에 추가

def _save_state_to_config_history(
    self, 
    action: str, 
    state: EmergencyState
) -> None:
    """Emergency 상태를 ConfigHistory에 저장."""
    try:
        from selfhealing.services.config_history import get_config_history_service
        history_service = get_config_history_service()
        
        # emergency config_type 추가 필요 (ConfigHistoryService.SUPPORTED_CONFIG_TYPES)
        history_service.save_version(
            config_type="emergency",
            values=state.to_dict(),
            changed_by=state.activated_by or state.deactivated_by or "system",
            reason=f"Emergency {action}: {state.activation_reason or 'manual'}",
        )
    except Exception as e:
        logger.warning(f"[EmergencyMode] Failed to save history: {e}")
```

**ConfigHistoryService 수정 필요**:
```python
# config_history.py
SUPPORTED_CONFIG_TYPES = [
    "circuit_breaker",
    "dlq",
    "retry",
    "sla",
    "slo",
    "rate_limit",
    "security",
    "idempotency",
    "notification",
    "forensic",
    "metrics",
    "error_budget",
    "emergency",    # ✅ 추가
    "logging",      # ✅ 추가
]
```

---

### P4. Config API Views - High

**현재 코드** (`views/config.py:261`):
```python
def put(self, request: Request) -> Response:
    # ...
    result = manager.update_with_strategy(
        config_type=self.config_name,
        changes=config_changes,
        **apply_options,
    )
```

**변경 후**:
```python
def put(self, request: Request) -> Response:
    # ...
    result = manager.update_with_strategy(
        config_type=self.config_name,
        changes=config_changes,
        changed_by=str(request.user),  # ✅ 사용자 정보 전달
        reason=request.data.get("reason", "API update"),  # ✅ 변경 사유
        **apply_options,
    )
```

**RuntimeConfigManager.update_with_strategy() 수정**:
```python
def update_with_strategy(
    self,
    config_type: str,
    changes: Dict[str, Any],
    changed_by: str = "system",  # ✅ 추가
    reason: str = "",            # ✅ 추가
    strategy: Optional[str] = None,
    delay_seconds: Optional[int] = None,
    grace_timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    # ...
    if apply_options.strategy == ApplyStrategy.IMMEDIATE:
        new_config = self._update_config(
            config_type, 
            changed_by=changed_by,  # ✅ 전달
            reason=reason,          # ✅ 전달
            **valid_changes
        )
```

---

### P5. Chaos Engineering Config - Medium

**현재 코드** (`runtime_config.py:989`):
```python
def update_chaos_config(self, **kwargs) -> Dict[str, Any]:
    # ...
    self._save_config("chaos", current)
    return current
```

**변경 후**:
```python
def update_chaos_config(
    self, 
    changed_by: str = "system",
    reason: str = "",
    **kwargs
) -> Dict[str, Any]:
    # ...
    self._save_config("chaos", current)
    
    # ✅ ConfigHistory에 저장
    self._save_to_history(
        config_type="chaos",
        values=current,
        changed_by=changed_by,
        reason=reason or "Chaos config update",
    )
    
    return current
```

**SUPPORTED_CONFIG_TYPES에 추가**:
```python
SUPPORTED_CONFIG_TYPES = [
    # ... 기존 ...
    "chaos",  # ✅ 추가
]
```

---

## 구현 계획

### Phase 1: Core Integration (예상 1.5시간)

| 작업 | 파일 | 예상 시간 |
|------|------|----------|
| `_save_to_history()` 헬퍼 추가 | runtime_config.py | 15분 |
| `_update_config()` 시그니처 변경 | runtime_config.py | 15분 |
| `update_with_strategy()` 수정 | runtime_config.py | 15분 |
| SUPPORTED_CONFIG_TYPES 확장 | config_history.py | 5분 |
| 단위 테스트 추가 | test_config_history.py | 30분 |

### Phase 2: Reconciliation Integration (예상 30분)

| 작업 | 파일 | 예상 시간 |
|------|------|----------|
| `_save_reconciliation_to_history()` 추가 | reconciliation.py | 15분 |
| 기존 테스트 업데이트 | test_error_budget_reconciliation.py | 15분 |

### Phase 3: Emergency Mode Integration (예상 30분)

| 작업 | 파일 | 예상 시간 |
|------|------|----------|
| `_save_state_to_config_history()` 추가 | emergency_mode.py | 15분 |
| 테스트 업데이트 | test_emergency_mode.py | 15분 |

### Phase 4: API Views Integration (예상 30분)

| 작업 | 파일 | 예상 시간 |
|------|------|----------|
| `put()` 메서드에 changed_by 전달 | views/config.py | 15분 |
| Serializer에 reason 필드 추가 (optional) | serializers/config.py | 15분 |

---

## 테스트 계획

### 단위 테스트

```python
# test_config_history_integration.py

class TestRuntimeConfigHistoryIntegration:
    """RuntimeConfigManager와 ConfigHistory 연동 테스트."""
    
    def test_update_config_saves_to_history(self):
        """설정 업데이트 시 ConfigHistory에 자동 저장."""
        pass
    
    def test_update_config_with_changed_by(self):
        """changed_by 파라미터가 history에 기록됨."""
        pass
    
    def test_history_save_failure_does_not_break_config_update(self):
        """History 저장 실패해도 설정 변경은 성공."""
        pass


class TestReconciliationHistoryIntegration:
    """Reconciliation과 ConfigHistory 연동 테스트."""
    
    def test_approve_saves_to_history(self):
        """Shadow Budget 승인 시 ConfigHistory에 저장."""
        pass


class TestEmergencyModeHistoryIntegration:
    """EmergencyMode와 ConfigHistory 연동 테스트."""
    
    def test_activate_saves_to_history(self):
        """비상 모드 활성화 시 ConfigHistory에 저장."""
        pass
    
    def test_deactivate_saves_to_history(self):
        """비상 모드 해제 시 ConfigHistory에 저장."""
        pass
```

### 통합 테스트

```python
# test_config_history_e2e.py

class TestConfigHistoryE2E:
    """ConfigHistory 전체 흐름 테스트."""
    
    def test_api_update_creates_history_with_user_info(self):
        """API를 통한 설정 변경 시 사용자 정보 포함하여 저장."""
        pass
    
    def test_rollback_restores_previous_version(self):
        """Rollback API가 이전 버전을 정확히 복원."""
        pass
    
    def test_history_query_returns_all_changes(self):
        """History API가 모든 변경 이력을 반환."""
        pass
```

---

## Graceful Degradation

### 원칙

1. **설정 변경 우선**: History 저장 실패해도 설정 변경은 반드시 성공
2. **Best-effort 저장**: History 저장은 try-except로 감싸서 예외 무시
3. **로깅**: 실패 시 경고 로그 기록

### 구현 패턴

```python
def _save_to_history(self, ...):
    try:
        history_service.save_version(...)
    except Exception as e:
        # 절대 예외를 상위로 전파하지 않음
        logger.warning(f"[ConfigHistory] Failed to save: {e}")
```

---

## 마이그레이션 고려사항

### 기존 데이터

- 기존 설정에 대한 초기 버전이 없음
- 첫 번째 변경 시점부터 이력 시작

### 하위 호환성

- 모든 변경은 추가(additive)
- 기존 API 시그니처는 default 파라미터로 호환 유지

```python
# 기존 코드도 동작
manager._update_config("circuit_breaker", failure_threshold=10)

# 새 코드도 동작
manager._update_config(
    "circuit_breaker", 
    changed_by="admin",
    reason="Increase threshold",
    failure_threshold=10,
)
```

---

## 예상 효과

| 지표 | 현재 | 구현 후 |
|------|------|---------|
| 설정 변경 추적률 | 0% (수동 Rollback만) | **100%** |
| 변경자 정보 | 로그에만 존재 | **History에 영구 저장** |
| 롤백 가능 범위 | 수동 저장분만 | **모든 변경** |
| 감사 추적 | 불완전 | **완전한 Audit Trail** |

---

## 관련 문서

- [16_GOVERNANCE_IMPLEMENTATION_PART2.md](16_GOVERNANCE_IMPLEMENTATION_PART2.md) - Config Versioning 설계
- [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) - Reconciliation 설계
- [09_CONFIGURATION.md](09_CONFIGURATION.md) - 설정 레퍼런스

---

## 체크리스트

### Phase 1: Core Integration
- [ ] `RuntimeConfigManager._save_to_history()` 헬퍼 추가
- [ ] `RuntimeConfigManager._update_config()` 시그니처 변경
- [ ] `RuntimeConfigManager.update_with_strategy()` 수정
- [ ] `ConfigHistoryService.SUPPORTED_CONFIG_TYPES` 확장
- [ ] 단위 테스트 추가

### Phase 2: Reconciliation Integration
- [ ] `ReconciliationService._save_reconciliation_to_history()` 추가
- [ ] `ReconciliationService._apply_to_primary()` 수정
- [ ] 테스트 업데이트

### Phase 3: Emergency Mode Integration
- [ ] `GracefulDegradationManager._save_state_to_config_history()` 추가
- [ ] `activate_manual()` 수정
- [ ] `deactivate()` 수정
- [ ] 테스트 업데이트

### Phase 4: API Views Integration
- [ ] `BaseConfigView.put()` 수정
- [ ] Serializer reason 필드 추가 (optional)
- [ ] 테스트 추가

### 문서화
- [ ] 변경된 API 시그니처 문서 업데이트
- [ ] 사용 예시 추가
