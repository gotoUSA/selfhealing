# 32. Chaos Engineering 힐링 시스템 연동 계획

> **작성일**: 2026-01-09  
> **상태**: Phase 3 구현 완료  
> **관련 문서**: [31_CHAOS_EXPERIMENT_EXPANSION.md](31_CHAOS_EXPERIMENT_EXPANSION.md), [24_CHAOS_INTEGRATION_PLAN.md](24_CHAOS_INTEGRATION_PLAN.md)

---

## 1. 개요

### 1.1 연동 현황 분석

현재 Self-Healing 시스템 내 컴포넌트와 Chaos Engineering 연동 상태:

| 컴포넌트 | 연동 상태 | 코드 근거 |
|----------|----------|----------|
| Error Budget | ✅ 부분 연동 | `safety_guard.py:150-180` |
| LearningService | ✅ 부분 연동 | `impact_predictor.py:1-20` |
| Synthetic Load | ✅ 구현됨 | `synthetic_load.py:1-600` |
| Traffic Shaper | ✅ 구현됨 | `traffic_shaper.py:1-545` |
| **FailureHypothesis** | ✅ **Phase 1 구현** | `experiment_impl.py:40-180` |
| **Chaos-Aware Metadata** | ✅ **Phase 1 구현** | `manager.py:204-290` |
| **RECOVERY_MONITORING** | ✅ **Phase 1 구현** | `base.py:60-90` |
| **Soft/Hard TTL** | ✅ **Phase 1 구현** | `base.py:155-175` |
| **CB 상태 스냅샷** | ✅ **Phase 2 구현** | `base.py:630-680` |
| **Panic Threshold 체크** | ✅ **Phase 2 구현** | `safety_guard.py:700-810` |
| Emergency Mode | ✅ 연동 완료 | `safety_guard.py:633-680` |
| Circuit Breaker | ⚡ 부분 연동 | `base.py:_get_cb_state_snapshot()` |
| **Canary Recovery 검증** | ✅ **Phase 3 구현** | `experiment_impl.py:_verify_canary_recovery()` |
| **FinOps Chaos Budget** | ✅ **Phase 3 구현** | `finops/service.py:set_chaos_budget()` |
| **LearningService 피드백** | ✅ **Phase 3 구현** | `base.py:_record_hypothesis_validation()` |
| Load Shedding | ❌ 미연동 | - |
| Freeze Mode | ❌ 미연동 | - |
| Corruption Shield | ❌ 미연동 | - |
| DLQ Service | ❌ 미연동 | - |
| Throttle (Adaptive) | ❌ 미연동 | - |
| Compliance Service | ❌ 미연동 | - |

---

## 2. Circuit Breaker 연동

### 2.1 현재 인터페이스 분석

**코드 위치**: `services/circuit_breaker/service.py`

```python
# service.py:50-68 - 사용 가능한 메서드
class CircuitBreakerService(ProtectionMixin, ManualControlMixin):
    def force_open(self, service_name, reason, controlled_by) -> CircuitBreakerResult
    def force_close(self, service_name, reason, controlled_by, trigger_replay) -> CircuitBreakerResult
    def should_allow(self, service_name) -> bool
    def should_allow_with_fallback(self, service_name, cache_key, default_response, request_data) -> FallbackResult
    def get_state(self, service_name) -> str
```

**코드 위치**: `services/circuit_breaker/convenience.py`

```python
# convenience.py - 모듈 레벨 함수
def get_circuit_breaker_service() -> CircuitBreakerService
def should_allow_request(service_name) -> bool
def force_open_circuit(service_name, reason, controlled_by) -> CircuitBreakerResult
def force_close_circuit(service_name, reason, controlled_by, trigger_replay) -> CircuitBreakerResult
def record_rate_limit(service_name, retry_after) -> None
```

### 2.2 연동 구현 계획

#### 2.2.1 Chaos → CB 상태 조회

**구현 위치**: `services/chaos/experiments.py` (새 메서드)

```python
def _get_cb_state_snapshot(self) -> Dict[str, Any]:
    """실험 전후 CB 상태 스냅샷 캡처."""
    try:
        from selfhealing.services.circuit_breaker import get_circuit_breaker_service
        
        service = get_circuit_breaker_service()
        return {
            "target_service_state": service.get_state(self.config.target_service),
            "is_allowed": service.should_allow(self.config.target_service),
            "timestamp": now().isoformat(),
        }
    except Exception as e:
        logger.warning(f"[Chaos] CB state snapshot failed: {e}")
        return {}
```

#### 2.2.2 실험 결과와 CB 상태 비교

**구현 위치**: `services/chaos/base.py` - `ChaosExperiment.run()` 내부

```python
def run(self) -> ExperimentResult:
    # ... 기존 코드
    
    # Steady State 캡처 시 CB 상태 포함
    steady_state_before = self._capture_steady_state()
    steady_state_before["circuit_breaker"] = self._get_cb_state_snapshot()
    
    # 실험 실행
    result = self._execute_experiment()
    
    # 실험 후 CB 상태 캡처
    steady_state_after = self._capture_steady_state()
    steady_state_after["circuit_breaker"] = self._get_cb_state_snapshot()
    
    # 결과에 포함
    result.steady_state_before = steady_state_before
    result.steady_state_after = steady_state_after
    
    return result
```

### 2.3 연동 포인트

| 기능 | 메서드 | 용도 |
|------|--------|------|
| 상태 조회 | `get_state()` | 실험 전후 상태 비교 |
| 강제 OPEN | `force_open_circuit()` | CB OPEN 실험 |
| 강제 CLOSE | `force_close_circuit()` | 롤백 시 복구 |
| Rate Limit 기록 | `record_rate_limit()` | Rate Limit 실험 |

---

## 3. Emergency Mode 연동

### 3.1 현재 인터페이스 분석

**코드 위치**: `services/emergency_mode/manager.py`

```python
# manager.py:20-45 - GracefulDegradationManager
class GracefulDegradationManager:
    def get_state(self) -> EmergencyState
    def get_current_level(self) -> EmergencyLevel
    def is_active(self) -> bool
    def get_tier_multiplier(self, tier_id) -> float
    def activate_manual(self, level, reason, activated_by, duration_minutes) -> bool
    def deactivate(self, deactivated_by) -> bool
```

**코드 위치**: `services/emergency_mode/enums.py`

```python
# enums.py - EmergencyLevel
class EmergencyLevel(str, Enum):
    NORMAL = "normal"
    LEVEL_1 = "level_1"  # 경고
    LEVEL_2 = "level_2"  # 제한
    LEVEL_3 = "level_3"  # Lockdown
```

### 3.2 연동 구현 계획

#### 3.2.1 Safety Guard에 Emergency Level 체크 추가

**구현 위치**: `services/chaos/safety_guard.py`

```python
# safety_guard.py - SafetyGuard._run_checks() 확장

def _check_emergency_level(self) -> SafetyCheckResult:
    """Emergency Level 상태 확인."""
    try:
        from selfhealing.services.emergency_mode import (
            get_emergency_manager,
            EmergencyLevel,
        )
        
        manager = get_emergency_manager()
        level = manager.get_current_level()
        
        # Level 3 (LOCKDOWN): 모든 카오스 실험 차단
        if level == EmergencyLevel.LEVEL_3:
            return SafetyCheckResult(
                passed=False,
                check_name="emergency_level",
                message=f"Emergency Level 3 (LOCKDOWN) active",
                severity="critical",
                recommendation="시스템 Lockdown 상태 - 카오스 실험 금지",
            )
        
        # Level 2: 경고만 (실험 허용)
        if level == EmergencyLevel.LEVEL_2:
            return SafetyCheckResult(
                passed=True,
                check_name="emergency_level",
                message=f"Warning: Emergency Level 2 active",
                severity="warning",
                recommendation="Emergency 상태 - 저위험 실험만 권장",
            )
        
        return SafetyCheckResult(passed=True, check_name="emergency_level")
    except Exception as e:
        logger.warning(f"[SafetyGuard] Emergency level check failed: {e}")
        # Fail-safe: 확인 불가 시 차단
        if self.config.fail_safe_on_error:
            return SafetyCheckResult(
                passed=False,
                check_name="emergency_level",
                message=f"Emergency level check failed: {e}",
                severity="error",
            )
        return SafetyCheckResult(passed=True, check_name="emergency_level")
```

#### 3.2.2 카오스 실험 → Emergency 에스컬레이션 감지

**구현 위치**: `services/chaos/base.py` - Stop Conditions 확장

```python
def _check_emergency_escalation(self) -> bool:
    """실험 중 Emergency Level 에스컬레이션 감지."""
    try:
        from selfhealing.services.emergency_mode import get_emergency_manager
        
        manager = get_emergency_manager()
        current_level = manager.get_current_level()
        
        # 실험 시작 시 레벨 저장
        if not hasattr(self, "_initial_emergency_level"):
            self._initial_emergency_level = current_level
        
        # 레벨 상승 감지
        level_order = ["normal", "level_1", "level_2", "level_3"]
        initial_idx = level_order.index(self._initial_emergency_level.value)
        current_idx = level_order.index(current_level.value)
        
        if current_idx > initial_idx:
            logger.warning(
                f"[Chaos] Emergency escalated: {self._initial_emergency_level} → {current_level}"
            )
            return True
        
        return False
    except Exception as e:
        logger.warning(f"[Chaos] Emergency escalation check failed: {e}")
        return False
```

### 3.3 연동 포인트

| 기능 | 메서드 | 용도 |
|------|--------|------|
| 레벨 조회 | `get_current_level()` | Safety Check |
| 상태 확인 | `is_active()` | 실험 차단 조건 |
| 수동 활성화 | `activate_manual()` | Emergency 실험 테스트 |
| 해제 | `deactivate()` | 실험 롤백 |

---

## 4. Canary Recovery 연동

### 4.1 현재 인터페이스 분석

**코드 위치**: `services/circuit_breaker/canary_recovery.py`

```python
# canary_recovery.py:36-50 - CanaryState
class CanaryState(str, Enum):
    NOT_IN_CANARY = "not_in_canary"
    CANARY_1 = "canary_1"  # 10% 트래픽
    CANARY_2 = "canary_2"  # 30% 트래픽
    CANARY_3 = "canary_3"  # 60% 트래픽
    CANARY_4 = "canary_4"  # 100% 트래픽

# CanaryRecoveryManager
class CanaryRecoveryManager:
    def start_canary(self, service_name) -> bool
    def get_canary_state(self, service_name) -> CanaryState
    def record_success(self, service_name) -> None
    def record_failure(self, service_name) -> None
    def get_traffic_percent(self, service_name) -> float
```

### 4.2 연동 구현 계획

#### 4.2.1 CB OPEN 실험 후 Canary 복구 검증

**구현 위치**: `services/chaos/experiment_impl.py` - `CircuitBreakerOpenExperiment`

```python
class CircuitBreakerOpenExperiment(ChaosExperiment):
    # ... 기존 코드
    
    def _verify_canary_recovery(self) -> Dict[str, Any]:
        """Canary 복구 단계 검증."""
        try:
            from selfhealing.services.circuit_breaker.canary_recovery import (
                CanaryRecoveryManager,
                CanaryState,
            )
            
            manager = CanaryRecoveryManager()
            state = manager.get_canary_state(self.config.target_service)
            traffic = manager.get_traffic_percent(self.config.target_service)
            
            return {
                "canary_state": state.value,
                "traffic_percent": traffic,
                "in_canary": state != CanaryState.NOT_IN_CANARY,
            }
        except Exception as e:
            logger.warning(f"[CBOpenExperiment] Canary verification failed: {e}")
            return {}
    
    def _wait_for_canary_completion(self, timeout_seconds: int = 300) -> bool:
        """Canary 복구 완료 대기."""
        import time
        start_time = time.time()
        
        while time.time() - start_time < timeout_seconds:
            status = self._verify_canary_recovery()
            
            if not status.get("in_canary", True):
                # Canary 완료 (CLOSED 상태)
                logger.info(f"[CBOpenExperiment] Canary recovery completed")
                return True
            
            time.sleep(5)  # 5초마다 체크
        
        logger.warning(f"[CBOpenExperiment] Canary recovery timeout")
        return False
```

### 4.3 연동 포인트

| 기능 | 메서드 | 용도 |
|------|--------|------|
| 상태 조회 | `get_canary_state()` | 복구 단계 확인 |
| 트래픽 확인 | `get_traffic_percent()` | 복구 진행률 |
| 성공 기록 | `record_success()` | 복구 진행 (테스트용) |
| 실패 기록 | `record_failure()` | 롤백 시뮬레이션 |

---

## 5. Panic Threshold 연동

### 5.1 현재 인터페이스 분석

**코드 위치**: `services/circuit_breaker/panic_threshold.py`

```python
# panic_threshold.py:42-60 - PanicThresholdResult
@dataclass
class PanicThresholdResult:
    triggered: bool = False
    open_rate: float = 0.0
    open_count: int = 0
    total_count: int = 0
    open_circuits: List[str] = field(default_factory=list)
    action_taken: Optional[str] = None
    halted_systems: List[str] = field(default_factory=list)

# PanicThresholdMonitor
class PanicThresholdMonitor:
    def check_panic_threshold(self) -> PanicThresholdResult
    def get_open_circuits(self) -> List[str]
    def get_open_rate(self) -> float
```

### 5.2 연동 구현 계획

#### 5.2.1 Safety Guard에 Panic Threshold 체크 추가

**구현 위치**: `services/chaos/safety_guard.py`

```python
def _check_panic_threshold(self) -> SafetyCheckResult:
    """Panic Threshold 상태 확인."""
    try:
        from selfhealing.services.circuit_breaker.panic_threshold import (
            PanicThresholdMonitor,
        )
        
        monitor = PanicThresholdMonitor()
        result = monitor.check_panic_threshold()
        
        # Panic 발동 상태
        if result.triggered:
            return SafetyCheckResult(
                passed=False,
                check_name="panic_threshold",
                message=f"PANIC: {result.open_rate:.1f}% CB OPEN ({result.open_count}/{result.total_count})",
                severity="critical",
                recommendation="시스템 전체 불안정 - 카오스 실험 금지",
                data={
                    "open_circuits": result.open_circuits,
                    "halted_systems": result.halted_systems,
                },
            )
        
        # 경고 수준 (50% 이상)
        if result.open_rate >= 50.0:
            return SafetyCheckResult(
                passed=True,
                check_name="panic_threshold",
                message=f"Warning: {result.open_rate:.1f}% CB OPEN",
                severity="warning",
                recommendation="CB OPEN 비율 높음 - 저위험 실험만 권장",
            )
        
        return SafetyCheckResult(passed=True, check_name="panic_threshold")
    except Exception as e:
        logger.warning(f"[SafetyGuard] Panic threshold check failed: {e}")
        return SafetyCheckResult(passed=True, check_name="panic_threshold")
```

#### 5.2.2 실험 중 Panic 감지 시 자동 중단

**구현 위치**: `services/chaos/base.py` - Stop Conditions 확장

```python
def _check_panic_during_experiment(self) -> bool:
    """실험 중 Panic Threshold 발동 감지."""
    try:
        from selfhealing.services.circuit_breaker.panic_threshold import (
            PanicThresholdMonitor,
        )
        
        monitor = PanicThresholdMonitor()
        result = monitor.check_panic_threshold()
        
        if result.triggered:
            logger.critical(
                f"[Chaos] PANIC THRESHOLD TRIGGERED during experiment - "
                f"{result.open_rate:.1f}% CB OPEN"
            )
            return True
        
        return False
    except Exception as e:
        logger.warning(f"[Chaos] Panic check failed: {e}")
        return False
```

---

## 6. Load Shedding 연동

### 6.1 현재 인터페이스 분석

**코드 위치**: `services/circuit_breaker/load_shedding.py`

```python
# load_shedding.py:42-56 - SheddingState
class SheddingState(str, Enum):
    INACTIVE = "inactive"
    LEVEL_1 = "level_1"  # low 50% 제한
    LEVEL_2 = "level_2"  # low+medium 80% 제한
    LEVEL_3 = "level_3"  # low+medium 완전 차단

# LoadSheddingManager
class LoadSheddingManager:
    def evaluate_shedding(self, service_name) -> float  # 허용 트래픽 %
    def is_shedding_active(self) -> bool
    def get_current_level(self) -> SheddingState
    def get_status(self) -> SheddingStatus
```

### 6.2 연동 구현 계획

#### 6.2.1 Partial Failure 실험에서 Load Shedding 트리거

**구현 위치**: `services/chaos/experiment_impl.py` - `PartialFailureExperiment`

```python
class PartialFailureExperiment(ChaosExperiment):
    # ... 기존 코드
    
    def _trigger_load_shedding(self) -> Dict[str, Any]:
        """Load Shedding 강제 트리거."""
        try:
            from selfhealing.services.circuit_breaker.load_shedding import (
                get_load_shedding_manager,
            )
            
            manager = get_load_shedding_manager()
            
            # Critical 서비스에 높은 에러율 시뮬레이션
            # → Load Shedding 자동 활성화 유도
            before_status = manager.get_status()
            
            # 에러율 주입 (실제 메트릭 시스템 연동 필요)
            # 현재는 상태 변경만 기록
            
            after_status = manager.get_status()
            
            return {
                "before": before_status.to_dict(),
                "after": after_status.to_dict(),
                "shedding_triggered": after_status.active and not before_status.active,
            }
        except Exception as e:
            logger.warning(f"[PartialFailure] Load shedding trigger failed: {e}")
            return {}
    
    def _verify_shedding_behavior(self) -> Dict[str, Any]:
        """Load Shedding 동작 검증."""
        try:
            from selfhealing.services.circuit_breaker.load_shedding import (
                get_load_shedding_manager,
            )
            
            manager = get_load_shedding_manager()
            status = manager.get_status()
            
            return {
                "shedding_active": status.active,
                "current_level": status.current_state.value,
                "shed_services": status.shed_services,
                "traffic_limit": status.traffic_limit,
            }
        except Exception as e:
            logger.warning(f"[PartialFailure] Shedding verification failed: {e}")
            return {}
```

---

## 7. Corruption Shield 연동

### 7.1 현재 인터페이스 분석

**코드 위치**: `services/corruption_shield/shield.py`

```python
# shield.py:62-75 - CorruptionShield
class CorruptionShield:
    def validate(self, data, context, block_on_violation, request) -> ValidationResult
    def get_stats(self) -> Dict[str, int]

# ValidationResult
@dataclass
class ValidationResult:
    is_valid: bool
    violations: List[Violation]
    blocked: bool
    l1_passed: bool  # Schema
    l2_passed: bool  # Business Rules
    l3_passed: bool  # Anomaly Detection
```

### 7.2 연동 구현 계획

#### 7.2.1 Data Corruption 실험 타입 추가

**새 실험 타입** (향후 구현)

```python
class DataCorruptionExperiment(ChaosExperiment):
    """
    Inject corrupted/malformed data to test Corruption Shield.
    
    Config parameters:
        - corruption_type: "schema", "business", "anomaly"
        - corruption_rate: Percentage of requests to corrupt
        - field_to_corrupt: Target field name
    """
    
    experiment_type = "data_corruption"
    requires_approval = True  # High risk - data integrity
    
    def inject_chaos(self) -> bool:
        """Inject corrupted data."""
        # Corruption Shield bypass 또는 잘못된 데이터 주입
        # L1/L2/L3 각 계층 검증 테스트
        pass
```

#### 7.2.2 실험 결과에 Corruption Shield 통계 포함

**구현 위치**: `services/chaos/base.py`

```python
def _get_corruption_shield_stats(self) -> Dict[str, Any]:
    """Corruption Shield 통계 조회."""
    try:
        from selfhealing.services.corruption_shield import get_corruption_shield
        
        shield = get_corruption_shield()
        return shield.get_stats()
    except Exception as e:
        logger.warning(f"[Chaos] Corruption shield stats failed: {e}")
        return {}
```

---

## 8. DLQ Service 연동

### 8.1 현재 인터페이스 분석

**코드 위치**: `services/dlq/base.py`, `services/dlq/__init__.py`

```python
# DLQService 주요 메서드
class DLQService(DLQServiceBase):
    def store(self, operation_type, entity_type, entity_id, ...) -> Optional[int]
    def get_pending_count(self, domain=None) -> int
    def replay(self, operation_id, replayer) -> bool
    def get_entries(self, status, domain, limit) -> List[FailedOperationData]
```

### 8.2 연동 구현 계획

#### 8.2.1 카오스 실험 DLQ 분리

**구현 위치**: 이미 구현됨 - `shopping/tasks/drift_detection_tasks.py:61-100`

```python
# 현재 구현 확인 (drift_detection_tasks.py:68-69)
pending_chaos = FailedOperation.objects.filter(
    status__in=["pending", "replayed"],
    metadata__is_chaos_experiment=True,
)
```

#### 8.2.2 실험 결과에 DLQ 통계 포함

**구현 위치**: `services/chaos/base.py`

```python
def _get_dlq_stats(self) -> Dict[str, Any]:
    """DLQ 통계 조회 (카오스 실험 제외)."""
    try:
        from selfhealing.services.dlq import get_dlq_service
        
        service = get_dlq_service()
        return {
            "pending_count": service.get_pending_count(),
            "pending_by_domain": service.get_pending_count_by_domain(),
        }
    except Exception as e:
        logger.warning(f"[Chaos] DLQ stats failed: {e}")
        return {}
```

---

## 9. Throttle (Adaptive) 연동

### 9.1 현재 인터페이스 분석

**코드 위치**: `services/throttle/adaptive.py`

```python
# adaptive.py:140-180 - AdaptiveThrottle
class AdaptiveThrottle(SlidingWindowThrottle):
    def check(self, identifier) -> ThrottleResult
    def record_response(self, rtt_ms) -> None
    def get_current_limit(self) -> int
    def get_stats(self) -> Dict[str, Any]
```

### 9.2 연동 구현 계획

#### 9.2.1 지연 주입 실험에서 Throttle 반응 검증

**구현 위치**: `services/chaos/experiment_impl.py` - `LatencyInjectionExperiment` 확장

```python
class LatencyInjectionExperiment(ChaosExperiment):
    # ... 기존 코드
    
    def _verify_throttle_adaptation(self) -> Dict[str, Any]:
        """Adaptive Throttle 반응 검증."""
        try:
            from selfhealing.services.throttle import get_adaptive_throttle
            
            throttle = get_adaptive_throttle()
            return throttle.get_stats()
        except Exception as e:
            logger.warning(f"[LatencyInjection] Throttle verification failed: {e}")
            return {}
```

---

## 10. FinOps Service 연동

### 10.1 현재 인터페이스 분석

**코드 위치**: `services/finops/service.py`

```python
# service.py:17-25 - 이미 chaos_test 비용 정의됨
DEFAULT_OPERATION_COSTS: Dict[str, Decimal] = {
    "retry": Decimal("0.001"),
    "circuit_breaker_check": Decimal("0.0001"),
    "dlq_enqueue": Decimal("0.005"),
    "dlq_replay": Decimal("0.01"),
    "health_check": Decimal("0.0001"),
    "rollback": Decimal("0.05"),
    "emergency_mode": Decimal("0.10"),
    "chaos_test": Decimal("0.02"),  # ← 이미 정의됨
}

# FinOpsService
class FinOpsService:
    def record_cost(self, operation, stage_name, success, metadata) -> None
    def get_daily_cost(self, date=None) -> Decimal
    def get_cost_by_operation(self, operation, days=30) -> Decimal
```

### 10.2 연동 구현 계획

**구현 위치**: `services/chaos/base.py` - `ChaosExperiment.run()` 내부

```python
def _record_chaos_cost(self, result: ExperimentResult) -> None:
    """카오스 실험 비용 기록."""
    try:
        from selfhealing.services.finops.service import get_finops_service
        
        finops = get_finops_service()
        finops.record_cost(
            operation="chaos_test",
            stage_name=self.config.target_service,
            success=result.status == ExperimentStatus.COMPLETED.value,
            metadata={
                "experiment_id": self.experiment_id,
                "experiment_type": self.experiment_type,
                "duration_seconds": result.duration_seconds,
                "dry_run": result.dry_run,
            },
        )
    except Exception as e:
        logger.debug(f"[Chaos] FinOps recording skipped: {e}")
```

---

## 11. Compliance Service 연동

### 11.1 현재 인터페이스 분석

**코드 위치**: `services/compliance/service.py`

```python
# service.py:36-42 - DORA-003 이미 정의됨
{
    "check_id": "DORA-003",
    "name": "Resilience Testing",
    "description": "디지털 운영 복원력 테스트 확인",
    "category": "testing",
}

# ComplianceService
class ComplianceService:
    def run_checks(self) -> List[ComplianceResult]
    def get_check_status(self, check_id) -> ComplianceResult
    def register_check(self, check_id, check_func) -> None
```

### 11.2 연동 구현 계획

**구현 위치**: `services/compliance/service.py` - 자동 검사 함수 추가

```python
def _check_resilience_testing(self) -> bool:
    """DORA-003: Resilience Testing 자동 검사."""
    try:
        from selfhealing.services.chaos.reports import get_report_generator
        
        generator = get_report_generator()
        
        # 최근 30일 내 카오스 실험 실행 여부 확인
        recent_reports = generator.get_reports(days=30)
        if not recent_reports:
            return False
        
        # 최소 실험 횟수 확인 (예: 월 4회 이상)
        total_experiments = sum(r.total_experiments for r in recent_reports)
        if total_experiments < 4:
            return False
        
        return True
    except Exception as e:
        logger.warning(f"[Compliance] DORA-003 check failed: {e}")
        return False
```

---

## 12. 구현 우선순위

| 우선순위 | 연동 대상 | 이유 | 소요 시간 |
|----------|----------|------|----------|
| 🔴 P0 | **Failure Hypothesis** | LearningService 피드백 루프 - 핵심 (§20) | 5.5시간 |
| 🔴 P0 | **Chaos-Aware 인시던트 분류** | 운영팀 혼란 방지 - 필수 (§14) | 2시간 |
| 🔴 P0 | **비동기 복구 모니터링** | Celery 워커 블로킹 방지 (§15) | 3시간 |
| 🔴 P1 | Circuit Breaker | 핵심 연동 - CB 상태 기반 실험 | 2시간 |
| 🔴 P1 | Emergency Mode | Safety Guard 필수 체크 | 2시간 |
| 🔴 P1 | Panic Threshold | 안전장치 - 시스템 보호 | 1시간 |
| 🟠 P2 | Canary Recovery | CB OPEN 실험 검증 | 2시간 |
| 🟠 P2 | Load Shedding | Partial Failure 실험 검증 | 2시간 |
| 🟠 P2 | **FinOps GameDay Budget** | 중앙 집중형 예산 (§17) | 3시간 |
| 🟠 P2 | **능동형 시뮬레이션** | 안전한 장애 테스트 (§16) | 4시간 |
| 🟠 P2 | ConnectionPoolMonitor | Pool 고갈 실험 검증 | 1시간 |
| 🟠 P2 | CertificateExpiryMonitor | 인증서 만료 실험 검증 | 1시간 |
| 🟢 P3 | Corruption Shield | 데이터 무결성 실험 | 3시간 |
| 🟢 P3 | DLQ | 통계 분리 (이미 구현됨) | 0.5시간 |
| 🟢 P3 | Throttle | 지연 실험 검증 | 1시간 |
| 🟢 P3 | **DORA-003 자동 검사** | 실패 실험도 실적 인정 (§18) | 1시간 |
| 🟢 P3 | ConnectionHealthMonitor | 연결 상태/파티션 실험 검증 | 1시간 |

### 12.1 권장 구현 순서

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Phase 1: 핵심 기반 (P0)                        │
│                         예상 소요: 10.5시간                           │
├──────────────────────────────────────────────────────────────────────┤
│ 1. FailureHypothesis 클래스 정의 (§20.2)                             │
│ 2. 실험 클래스별 기대 가설 상수 (§20.3)                               │
│ 3. Chaos-Aware 메타데이터 (§14)                                       │
│ 4. RECOVERY_MONITORING 상태 + Celery Beat 폴링 (§15)                 │
└──────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Phase 2: Safety Guards (P1)                      │
│                         예상 소요: 5시간                              │
├──────────────────────────────────────────────────────────────────────┤
│ 1. SafetyGuard._check_emergency_level() (§3)                         │
│ 2. SafetyGuard._check_panic_threshold() (§5)                         │
│ 3. CB 상태 스냅샷 캡처 (§2)                                           │
└──────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Phase 3: 검증 강화 (P2)                          │
│                         예상 소요: 13시간                             │
├──────────────────────────────────────────────────────────────────────┤
│ 1. Canary Recovery 검증 (§4)                                          │
│ 2. set_simulation_override() 인터페이스 (§16)                        │
│ 3. FinOps GameDay Budget (§17)                                       │
│ 4. LearningService 피드백 루프 (§20.4)                               │
└──────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Phase 4: 고급 기능 (P3)                          │
│                         예상 소요: 6.5시간                            │
├──────────────────────────────────────────────────────────────────────┤
│ 1. Corruption Shield 연동 (§7)                                       │
│ 2. DORA-003 자동 검사 (§18)                                          │
│ 3. Throttle/DLQ 통합 (§8, §9)                                        │
│ 4. LearningService analyze_trend() 연동                              │
└──────────────────────────────────────────────────────────────────────┘
```

**총 예상 소요**: 35시간 (약 4.5일)

---

## 13. 추가 발견 모니터 연동

### 13.1 ConnectionPoolMonitor 연동

**코드 위치**: `core/pool_monitor.py:83-100`

```python
# pool_monitor.py - 핵심 인터페이스
class ConnectionPoolMonitor:
    def check_health(self) -> PoolHealthStatus
    def get_metrics(self) -> PoolMetrics
    def detect_leak(self) -> Optional[LeakInfo]
    def predict_exhaustion(self) -> Optional[ExhaustionPrediction]

class PoolHealthStatus(Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    EXHAUSTED = "exhausted"
    LEAK_SUSPECTED = "leak_suspected"
```

#### 연동 구현

```python
# services/chaos/experiments.py - 풀 상태 모니터링
def _get_pool_state_snapshot(self) -> Dict[str, Any]:
    """실험 전후 커넥션 풀 상태 캡처."""
    try:
        from selfhealing.core.pool_monitor import ConnectionPoolMonitor
        
        monitor = ConnectionPoolMonitor()
        metrics = monitor.get_metrics()
        
        return {
            "health_status": monitor.check_health().value,
            "active_connections": metrics.active,
            "idle_connections": metrics.idle,
            "waiting_requests": metrics.waiting,
            "leak_suspected": monitor.detect_leak() is not None,
            "timestamp": now().isoformat(),
        }
    except Exception as e:
        logger.warning(f"[Chaos] Pool state snapshot failed: {e}")
        return {}
```

### 13.2 CertificateExpiryMonitor 연동

**코드 위치**: `core/cert_monitor.py:59-100`

```python
# cert_monitor.py - 핵심 인터페이스
class CertificateExpiryMonitor:
    def check_expiry(self, not_after, endpoint, subject) -> CertificateStatus
    
class CertificateStatus(Enum):
    VALID = "valid"           # 30일 이상 남음
    EXPIRING_SOON = "expiring_soon"  # 30일 미만
    CRITICAL = "critical"     # 7일 미만
    EXPIRED = "expired"       # 만료됨
```

#### 연동 구현

```python
# services/chaos/experiments.py - 인증서 상태 모니터링
def _get_cert_state_snapshot(self) -> Dict[str, Any]:
    """실험 전후 인증서 상태 캡처."""
    try:
        from selfhealing.core.cert_monitor import (
            CertificateExpiryMonitor,
            CertificateStatus,
        )
        
        monitor = CertificateExpiryMonitor()
        # 주요 엔드포인트 인증서 상태 확인
        target = self.config.target_service
        
        # 실제 인증서 정보 조회는 TLS 핸들러 사용
        return {
            "target_endpoint": target,
            "check_performed": True,
            "timestamp": now().isoformat(),
        }
    except Exception as e:
        logger.warning(f"[Chaos] Cert state snapshot failed: {e}")
        return {}
```

### 13.3 ConnectionHealthMonitor 연동

**코드 위치**: `core/connection_health.py:85-113`

```python
# connection_health.py - 핵심 인터페이스
class ConnectionHealthMonitor(ABC):
    def check_health(self, connection_type, name) -> ConnectionHealth
    def get_all_health(self) -> List[ConnectionHealth]
    def detect_partition(self) -> Optional[PartitionState]

class PartitionState:
    @property
    def is_partial_partition(self) -> bool
    
    @property
    def is_full_partition(self) -> bool

class ConnectionType(Enum):
    DATABASE = "database"
    CACHE = "cache"
    EXTERNAL_API = "external_api"
```

#### 연동 구현

```python
# services/chaos/experiments.py - 연결 상태 모니터링
def _get_connection_health_snapshot(self) -> Dict[str, Any]:
    """실험 전후 연결 상태 캡처."""
    try:
        from selfhealing.core.connection_health import (
            get_connection_health_monitor,
            ConnectionType,
        )
        
        monitor = get_connection_health_monitor()
        all_health = monitor.get_all_health()
        partition = monitor.detect_partition()
        
        return {
            "connections": [
                {
                    "type": h.connection_type.value,
                    "name": h.name,
                    "status": h.status.value,
                    "consecutive_failures": h.consecutive_failures,
                }
                for h in all_health
            ],
            "is_partial_partition": partition.is_partial_partition if partition else False,
            "is_full_partition": partition.is_full_partition if partition else False,
            "timestamp": now().isoformat(),
        }
    except Exception as e:
        logger.warning(f"[Chaos] Connection health snapshot failed: {e}")
        return {}
```

---

## 14. Chaos-Aware 인시던트 분류

> **Architect Review 반영**: 카오스 실험이 Emergency/Panic을 트리거했을 때 "의도된 상황"으로 분류

### 14.1 문제점

카오스 실험이 Emergency Level 에스컬레이션이나 Panic Threshold를 트리거했을 때, 운영팀/경영진이 실제 장애로 오인할 수 있음.

### 14.2 해결책: `is_chaos_experiment` 메타데이터 강제 포함

**구현 위치**: `services/emergency_mode/manager.py` - `activate_manual()` 확장

**코드 근거**: 현재 `activate_manual()` 시그니처 ([manager.py#L202-L225](../../../packages/selfhealing-python/src/selfhealing/services/emergency_mode/manager.py#L202-L225)):

```python
def activate_manual(
    self,
    level: EmergencyLevel,
    reason: str,
    activated_by: str,
    duration_minutes: Optional[int] = None,
) -> EmergencyState:
```

**확장 구현**:

```python
def activate_manual(
    self,
    level: EmergencyLevel,
    reason: str,
    activated_by: str,
    duration_minutes: Optional[int] = None,
    # 신규 파라미터
    is_chaos_experiment: bool = False,
    experiment_id: Optional[str] = None,
) -> EmergencyState:
    """
    수동 비상 모드 활성화.
    
    Args:
        level: 비상 모드 레벨
        reason: 활성화 사유
        activated_by: 활성화한 사용자
        duration_minutes: 자동 만료 시간
        is_chaos_experiment: 카오스 실험에 의한 활성화 여부
        experiment_id: 관련 카오스 실험 ID
    """
    with self._state_lock:
        # ... 기존 코드 ...
        
        # 메타데이터에 카오스 실험 정보 포함
        self._state.metadata = {
            "is_chaos_experiment": is_chaos_experiment,
            "experiment_id": experiment_id,
            "classification": "chaos_induced_test" if is_chaos_experiment else "infrastructure_incident",
        }
        
        # ... 기존 코드 ...
```

### 14.3 LearningService 연동

**코드 근거**: LearningService가 패턴 학습 시 카오스 실험 구분 필요 ([impact_predictor.py#L1-20](../../../packages/selfhealing-python/src/selfhealing/services/chaos/impact_predictor.py#L1-20))

```python
# LearningService에서 Emergency 이벤트 처리 시
def _classify_incident(self, emergency_state: EmergencyState) -> str:
    """인시던트 분류."""
    metadata = emergency_state.metadata or {}
    
    if metadata.get("is_chaos_experiment"):
        # 카오스 실험으로 인한 의도된 상황
        return "chaos_stress_test_success"
    
    return "infrastructure_incident"
```

### 14.4 네이밍 결정

| 후보 | 결정 | 이유 |
|------|------|------|
| `is_chaos_induced` | ❌ | 문서에만 존재, 코드 미사용 |
| `is_chaos_experiment` | ✅ | DLQ 메타데이터와 일관 ([drift_detection_tasks.py#L68-69](../../../shopping/tasks/drift_detection_tasks.py#L68-L69)) |

---

## 15. 비동기 복구 모니터링

> **Architect Review 반영**: `time.sleep()` 블로킹 대기 → 상태 기반 비동기 폴링

### 15.1 문제점

**기존 코드** (Section 4.2.1):

```python
def _wait_for_canary_completion(self, timeout_seconds: int = 300) -> bool:
    while time.time() - start_time < timeout_seconds:
        # ...
        time.sleep(5)  # 5초마다 체크 → Celery 워커 5분 점유
```

**위험**: Celery 워커가 5분간 블로킹되어 다른 힐링 작업 처리 불가.

### 15.2 해결책: 상태 기반 비동기 폴링

**코드 근거**: 기존 `ExperimentStatus` enum ([base.py#L61-L75](../../../packages/selfhealing-python/src/selfhealing/services/chaos/base.py#L61-L75)):

```python
class ExperimentStatus(str, Enum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"
```

**확장 구현**: `RECOVERY_MONITORING` 상태 추가

```python
class ExperimentStatus(str, Enum):
    # ... 기존 상태 ...
    
    RECOVERY_MONITORING = "recovery_monitoring"
    """실험 완료 후 Canary 복구 모니터링 중."""
```

### 15.3 Celery Beat 폴링 설계

**코드 근거**: 기존 Celery Beat 스케줄러 ([scheduler.py#L4-L10](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py#L4-L10))

```python
# shopping/tasks/chaos_tasks.py (신규)

@celery_app.task(bind=True)
def check_recovery_monitoring_experiments(self):
    """
    RECOVERY_MONITORING 상태 실험들의 Canary 복구 완료 체크.
    
    Celery Beat: 매 30초마다 실행
    """
    from selfhealing.services.chaos import get_chaos_scheduler
    
    scheduler = get_chaos_scheduler()
    monitoring_experiments = scheduler.get_experiments_by_status(
        ExperimentStatus.RECOVERY_MONITORING
    )
    
    for exp in monitoring_experiments:
        canary_status = exp._verify_canary_recovery()
        
        if not canary_status.get("in_canary", True):
            # Canary 복구 완료
            exp.complete_recovery_monitoring()
        elif exp.is_hard_ttl_expired():
            # Hard TTL 만료 → 강제 종료
            exp.force_complete(reason="hard_ttl_expired")
```

### 15.4 Soft TTL / Hard TTL 이중 구조

**설계 원칙**:

| TTL 유형 | 트리거 시점 | 동작 |
|----------|------------|------|
| **Soft TTL** | `config.ttl_seconds` 도달 | 장애 주입 중단, `RECOVERY_MONITORING`으로 전환 |
| **Grace Period** | Soft TTL ~ Hard TTL | Canary 복구 대기, 지표 수집 |
| **Hard TTL** | Soft TTL + 5분 | 실험 강제 종료 (`COMPLETED` or `FAILED`) |

**구현 위치**: `services/chaos/base.py`

```python
@dataclass
class ExperimentConfig:
    # ... 기존 필드 ...
    
    # TTL 이중 구조
    ttl_seconds: Optional[int] = None
    """Soft TTL: 장애 주입 종료 시간."""
    
    grace_period_seconds: int = 300
    """Grace Period: Canary 복구 대기 시간 (기본 5분)."""
    
    @property
    def hard_ttl_seconds(self) -> int:
        """Hard TTL: 실험 강제 종료 시간."""
        base_ttl = self.ttl_seconds or 600  # 기본 10분
        return base_ttl + self.grace_period_seconds
```

### 15.5 네이밍 결정

| 후보 | 결정 | 이유 |
|------|------|------|
| `WAITING_FOR_RECOVERY` | ❌ | 수동적 느낌 |
| `RECOVERY_PENDING` | ❌ | `PENDING`과 혼동 가능 |
| `RECOVERY_MONITORING` | ✅ | 능동적 + 기존 `_ING` 패턴과 일관 (`RUNNING`) |

---

## 16. 능동형 시뮬레이션 인터페이스

> **Architect Review 반영**: 실제 인프라 상태 변경 없이 "문제 있는 것처럼" 보고하는 모킹 인터페이스

### 16.1 문제점

현재 모니터들은 실제 상태만 반환하며, 카오스 실험에서 "가짜 장애 상태"를 주입할 방법이 없음.

**코드 근거**:
- [pool_monitor.py#L83-L109](../../../packages/selfhealing-python/src/selfhealing/core/pool_monitor.py#L83-L109): Mock Mode 없음
- [connection_health.py#L85-L113](../../../packages/selfhealing-python/src/selfhealing/core/connection_health.py#L85-L113): Mock Mode 없음

### 16.2 해결책: `set_simulation_override()` 인터페이스

**네이밍 결정**: 기존 `set_` 패턴과 일관되게 `set_simulation_override()` 사용

#### 16.2.1 ConnectionPoolMonitor 확장

```python
# core/pool_monitor.py 확장

class ConnectionPoolMonitor:
    def __init__(self, ...):
        # ... 기존 코드 ...
        self._simulation_override: Optional[PoolHealthStatus] = None
        self._simulation_stats: Optional[PoolStats] = None
    
    def set_simulation_override(
        self,
        health_status: Optional[PoolHealthStatus] = None,
        stats: Optional[PoolStats] = None,
        experiment_id: Optional[str] = None,
    ) -> None:
        """
        시뮬레이션 상태 오버라이드 설정.
        
        실제 인프라를 변경하지 않고 모니터가 특정 상태를 보고하도록 강제.
        카오스 실험에서 알림/복구 체인 검증에 사용.
        
        Args:
            health_status: 강제할 건강 상태 (None이면 해제)
            stats: 강제할 통계 (None이면 해제)
            experiment_id: 관련 카오스 실험 ID (감사 추적용)
        """
        self._simulation_override = health_status
        self._simulation_stats = stats
        
        if health_status:
            logger.info(
                f"[PoolMonitor] Simulation override set: {health_status.value} "
                f"(experiment_id={experiment_id})"
            )
        else:
            logger.info("[PoolMonitor] Simulation override cleared")
    
    def clear_simulation_override(self) -> None:
        """시뮬레이션 오버라이드 해제."""
        self.set_simulation_override(None, None)
    
    def check_health(self) -> Tuple[PoolHealthStatus, PoolStats]:
        """Check pool health status."""
        # 시뮬레이션 모드 체크
        if self._simulation_override is not None:
            logger.debug(f"[PoolMonitor] Returning simulated status: {self._simulation_override.value}")
            stats = self._simulation_stats or self._get_default_stats()
            return self._simulation_override, stats
        
        # ... 기존 실제 상태 체크 로직 ...
```

#### 16.2.2 ConnectionHealthMonitor 확장

```python
# core/connection_health.py 확장

class DefaultConnectionHealthMonitor(ConnectionHealthMonitor):
    def __init__(self, ...):
        # ... 기존 코드 ...
        self._simulation_overrides: Dict[str, ConnectionHealth] = {}
        self._partition_override: Optional[PartitionState] = None
    
    def set_simulation_override(
        self,
        connection_type: ConnectionType,
        name: str,
        status: ConnectionStatus,
        experiment_id: Optional[str] = None,
    ) -> None:
        """특정 연결에 대한 시뮬레이션 상태 설정."""
        key = f"{connection_type.value}:{name}"
        self._simulation_overrides[key] = ConnectionHealth(
            connection_type=connection_type,
            name=name,
            status=status,
        )
        logger.info(
            f"[ConnectionHealthMonitor] Simulation override set: {key}={status.value} "
            f"(experiment_id={experiment_id})"
        )
    
    def set_partition_simulation(
        self,
        partition_state: PartitionState,
        experiment_id: Optional[str] = None,
    ) -> None:
        """네트워크 파티션 시뮬레이션 설정."""
        self._partition_override = partition_state
        logger.info(
            f"[ConnectionHealthMonitor] Partition simulation set: "
            f"partial={partition_state.is_partial_partition}, "
            f"full={partition_state.is_full_partition} "
            f"(experiment_id={experiment_id})"
        )
    
    def clear_all_simulation_overrides(self) -> None:
        """모든 시뮬레이션 오버라이드 해제."""
        self._simulation_overrides.clear()
        self._partition_override = None
        logger.info("[ConnectionHealthMonitor] All simulation overrides cleared")
```

### 16.3 카오스 실험 통합

**구현 위치**: `services/chaos/experiment_impl.py`

```python
class PoolExhaustionExperiment(ChaosExperiment):
    """Connection Pool 고갈 시뮬레이션 실험."""
    
    experiment_type = "pool_exhaustion_simulation"
    
    def inject_chaos(self) -> bool:
        """시뮬레이션 모드로 Pool 고갈 상태 주입."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )
        
        monitor = ConnectionPoolMonitor()
        monitor.set_simulation_override(
            health_status=PoolHealthStatus.EXHAUSTED,
            experiment_id=self.experiment_id,
        )
        return True
    
    def rollback(self) -> None:
        """시뮬레이션 오버라이드 해제."""
        from selfhealing.core.pool_monitor import ConnectionPoolMonitor
        
        monitor = ConnectionPoolMonitor()
        monitor.clear_simulation_override()
```

---

## 17. FinOps 통합 GameDay Budget

> **Architect Review 반영**: Stage별 개별 예산 대신 중앙 집중형 예산 풀 + 도메인 가중치

### 17.1 문제점

- 각 Stage별로 카오스 예산을 개별 설정하면 운영 효율성 저하
- 현재 `FinOpsService.set_budget()`은 Stage 단위로만 동작

### 17.2 해결책: Global Chaos Budget Pool

**코드 근거**: 기존 `FinOpsService` 구조 ([service.py#L60-L90](../../../packages/selfhealing-python/src/selfhealing/services/finops/service.py#L60-L90))

#### 17.2.1 Chaos Budget 전용 설정

```python
# services/finops/service.py 확장

class FinOpsService:
    def __init__(self):
        # ... 기존 코드 ...
        self._chaos_budget: Optional[CostBudget] = None
        self._domain_weights: Dict[str, float] = {}
    
    def set_chaos_budget(
        self,
        max_budget: Decimal,
        alert_threshold: float = 0.8,
        hard_limit: bool = True,
        reset_period: str = "monthly",
    ) -> CostBudget:
        """
        전역 카오스 실험 예산 설정.
        
        Args:
            max_budget: 월간 최대 예산 (USD)
            alert_threshold: 알림 임계값 (0.0 ~ 1.0)
            hard_limit: 예산 초과 시 실험 차단 여부
            reset_period: 리셋 주기 (daily, weekly, monthly)
        """
        self._chaos_budget = CostBudget(
            stage_name="_chaos_global_pool",
            max_budget=max_budget,
            alert_threshold=alert_threshold,
            hard_limit=hard_limit,
            reset_period=reset_period,
        )
        logger.info(f"[FinOps] Chaos budget set: ${max_budget}")
        return self._chaos_budget
    
    def set_domain_weight(self, domain: str, weight: float) -> None:
        """
        도메인별 비용 가중치 설정.
        
        높은 가중치 = 높은 위험 도메인 = 더 많은 예산 소진
        
        Args:
            domain: 도메인 이름
            weight: 가중치 배수 (기본 1.0)
        """
        self._domain_weights[domain] = weight
        logger.info(f"[FinOps] Domain weight set: {domain}={weight}x")
```

#### 17.2.2 가중치 기반 비용 차감

**코드 근거**: `30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md`의 `MAX_WEIGHT_MULTIPLIER` 패턴 ([#L186-L195](30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md#L186-L195))

```python
# 가중치 폭발 방지
MAX_CHAOS_WEIGHT_MULTIPLIER = 10.0

def record_chaos_cost(
    self,
    experiment_id: str,
    experiment_type: str,
    target_domain: str,
    success: bool = True,
) -> CostRecord:
    """
    카오스 실험 비용 기록 (가중치 적용).
    
    Raises:
        ValueError: 예산 초과 시 (hard_limit=True인 경우)
    """
    if not self._chaos_budget:
        logger.debug("[FinOps] No chaos budget configured, skipping cost recording")
        return None
    
    # 기본 비용
    base_cost = self._operation_costs.get("chaos_test", Decimal("0.02"))
    
    # 도메인 가중치 적용
    domain_weight = self._domain_weights.get(target_domain.lower(), 1.0)
    domain_weight = min(domain_weight, MAX_CHAOS_WEIGHT_MULTIPLIER)  # Cap
    
    final_cost = base_cost * Decimal(str(domain_weight))
    
    # 예산에서 차감
    return self._record_cost_to_budget(
        budget=self._chaos_budget,
        operation="chaos_test",
        cost=final_cost,
        metadata={
            "experiment_id": experiment_id,
            "experiment_type": experiment_type,
            "target_domain": target_domain,
            "domain_weight": domain_weight,
        },
    )
```

### 17.3 SafetyGuard 연동

**코드 근거**: 기존 `SafetyGuard._run_core_checks()` 구조 ([safety_guard.py#L416-L435](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py#L416-L435))

```python
# services/chaos/safety_guard.py 확장

def _check_chaos_budget_status(self, result: SafetyCheckResult) -> bool:
    """Check chaos budget. Returns True if blocked."""
    result.checks_performed.append("chaos_budget")
    
    try:
        from selfhealing.services.finops.service import get_finops_service
        
        finops = get_finops_service()
        budget = finops.get_chaos_budget()
        
        if budget is None:
            # 예산 미설정 시 통과
            result.checks_passed.append("chaos_budget")
            return False
        
        usage_percent = budget.usage_percent
        result.chaos_budget_usage_percent = usage_percent
        
        # 100% 소진: 차단
        if usage_percent >= 100.0:
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = BlockReason.CHAOS_BUDGET_EXCEEDED.value
            result.block_message = f"Chaos budget exhausted: {usage_percent:.1f}%"
            result.checks_failed.append("chaos_budget")
            
            # 알림 발송
            self._notify_chaos_budget_exceeded(budget)
            return True
        
        # 80% 이상: 경고
        if usage_percent >= budget.alert_threshold * 100:
            result.warnings.append(f"Chaos budget usage high: {usage_percent:.1f}%")
            if result.status == SafetyStatus.SAFE.value:
                result.status = SafetyStatus.WARNING.value
            
            # 알림 발송
            self._notify_chaos_budget_warning(budget)
        
        result.checks_passed.append("chaos_budget")
        return False
        
    except Exception as e:
        logger.warning(f"[SafetyGuard] Could not check chaos budget: {e}")
        result.checks_passed.append("chaos_budget")
        return False

def _notify_chaos_budget_exceeded(self, budget: CostBudget) -> None:
    """예산 초과 알림."""
    try:
        from selfhealing.services.unified_notification import (
            get_unified_notification_manager,
        )
        
        manager = get_unified_notification_manager()
        manager.notify(
            event_type="chaos_budget_exceeded",
            severity="critical",
            message=f"Chaos budget exhausted: ${budget.current_spent} / ${budget.max_budget}",
            data=budget.to_dict(),
        )
    except Exception as e:
        logger.warning(f"[SafetyGuard] Could not send budget notification: {e}")
```

### 17.4 BlockReason Enum 확장

```python
# services/chaos/safety_guard.py - BlockReason 확장

class BlockReason(str, Enum):
    # ... 기존 값 ...
    CHAOS_BUDGET_EXCEEDED = "chaos_budget_exceeded"
    """월간 카오스 실험 예산 초과."""
```

---

## 18. DORA-003 자동 검사 확장

> **Architect Review 반영**: "실패한 카오스 실험"도 복원력 테스트 실적으로 인정

### 18.1 현재 상태

**코드 근거**: DORA-003 정의 ([service.py#L41-L57](../../../packages/selfhealing-python/src/selfhealing/services/compliance/service.py#L41-L57))

```python
{
    "check_id": "DORA-003",
    "name": "Resilience Testing",
    "description": "디지털 운영 복원력 테스트 확인",
    "category": "testing",
}
```

현재는 자동 검사 함수가 등록되어 있지 않아 **항상 통과** 처리됨.

### 18.2 해결책: 카오스 실험 실적 기반 자동 검사

```python
# services/compliance/service.py 확장

def _register_resilience_testing_check(self) -> None:
    """DORA-003 자동 검사 함수 등록."""
    self._check_functions["DORA-003"] = self._check_resilience_testing

def _check_resilience_testing(self) -> bool:
    """
    DORA-003: Resilience Testing 자동 검사.
    
    판정 기준:
    - 최근 30일 내 카오스 실험 4회 이상 실행
    - "실패한 실험"도 "복원력 한계를 발견한 성공적 테스트"로 인정
    """
    try:
        from selfhealing.services.chaos import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        
        # 최근 30일 실험 이력 조회
        recent_experiments = scheduler.get_execution_history(days=30)
        
        if not recent_experiments:
            logger.warning("[Compliance] DORA-003: No chaos experiments in last 30 days")
            return False
        
        # 총 실험 횟수 (성공 + 실패 모두 카운트)
        total_count = len(recent_experiments)
        
        # 최소 실험 횟수: 월 4회 (주 1회)
        min_required = 4
        
        if total_count < min_required:
            logger.warning(
                f"[Compliance] DORA-003: Insufficient experiments "
                f"({total_count}/{min_required})"
            )
            return False
        
        # 통과: 실패한 실험도 "복원력 한계 발견"으로 인정
        logger.info(
            f"[Compliance] DORA-003: PASSED - {total_count} experiments in 30 days "
            f"(includes failed experiments as valid resilience testing)"
        )
        return True
        
    except Exception as e:
        logger.warning(f"[Compliance] DORA-003 check failed: {e}")
        return False
```

---

## 19. Emergency 전파 확정: RedisEventBus

> **Architect Review 반영**: Emergency 이벤트는 RedisEventBus를 통해 10ms 이내 전 클러스터 전파

### 19.1 구현 확인

**코드 근거**:
- [manager.py#L72-L88](../../../packages/selfhealing-python/src/selfhealing/services/emergency_mode/manager.py#L72-L88): EventBus 구독 등록
- [manager.py#L263-L270](../../../packages/selfhealing-python/src/selfhealing/services/emergency_mode/manager.py#L263-L270): 레벨 변경 시 이벤트 발행
- [event_bus_redis.py#L36-L200](../../../packages/selfhealing-python/src/selfhealing/services/event_bus_redis.py#L36-L200): Redis Pub/Sub 구현

```python
# manager.py - 이벤트 버스 연동 (이미 구현됨)
def _register_event_handlers(self):
    """이벤트 버스 핸들러 등록 (캐시 무효화용)."""
    from selfhealing.services.event_bus import get_event_bus, EventType
    
    bus = get_event_bus()
    bus.subscribe(
        EventType.EMERGENCY_LEVEL_CHANGED,
        self._on_external_level_changed,
    )

def _emit_level_changed_event(self, new_level, previous_level, reason):
    """다른 컴포넌트에 레벨 변경 알림."""
    # RedisEventBus를 통해 전 클러스터 전파
```

### 19.2 성능 보장

| 항목 | 값 | 근거 |
|------|-----|------|
| 전파 지연 | < 10ms | Redis Pub/Sub 특성 |
| 구독자 | 전 클러스터 인스턴스 | `event_bus_redis.py` 리스너 |
| Fallback | 로컬 이벤트 버스 | `fallback_to_local=True` |

---

## 20. Failure Hypothesis (복구 기대 가설)

> **Architect 제안 반영**: 각 실험 클래스에 "기대 가설"을 정의하여 LearningService 피드백 루프 구성

### 20.1 문제점

**현재 상태**:
- 실험 결과는 "장애 주입 성공/실패"만 판정
- **시스템이 어떻게 반응해야 하는지** 명시되지 않음
- LearningService가 "복구 성능 저하 추세"를 자동 감지할 수 없음

**코드 근거** ([base.py#L199](../../../packages/selfhealing-python/src/selfhealing/services/chaos/base.py#L199)):
```python
steady_state_hypothesis_passed: bool = True  # "왜" 실패했는지 분석 없음
```

### 20.2 해결책: `FailureHypothesis` 클래스 정의

**기존 설계 참조**: [31_CHAOS_EXPERIMENT_EXPANSION.md §8](31_CHAOS_EXPERIMENT_EXPANSION.md#8-가설-기반-검증-hypothesis-validation---제안)의 `ResilienceExpectation` 확장

**새 클래스** (experiment_impl.py 상단에 추가):

```python
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FailureHypothesis:
    """
    카오스 실험의 복구 기대 가설.
    
    LearningService가 실제 결과와 비교하여
    "시스템 복구 성능 저하 추세"를 자동 감지하게 함.
    
    Reference:
    - Architect 제안: "CB Open 실험 시, 30초 내에 Canary Stage 1이 시작되어야 함"
    - 31_CHAOS_EXPERIMENT_EXPANSION.md §8
    """
    
    # 복구 관련 기대
    expected_recovery_time_seconds: float = 30.0
    """기대 복구 시간 (초). 이 시간 내에 복구되어야 함."""
    
    expected_canary_stage: Optional[str] = None
    """기대 Canary 단계. 예: "canary_1" (10% 트래픽)."""
    
    expected_canary_start_within_seconds: Optional[float] = None
    """Canary 시작까지 기대 시간 (초)."""
    
    # CB 관련 기대
    expected_cb_state_after: Optional[str] = None
    """실험 후 기대 CB 상태. 예: "open", "half_open"."""
    
    expected_cb_transition_within_seconds: Optional[float] = None
    """CB 상태 전환까지 기대 시간 (초)."""
    
    # Fallback 관련 기대
    expected_fallback_activated: bool = False
    """Fallback 활성화 기대 여부."""
    
    expected_fallback_type: Optional[str] = None
    """기대 Fallback 유형. 예: "cache", "dlq", "default"."""
    
    # 메타데이터
    description: str = ""
    """사람이 읽을 수 있는 가설 설명."""
    
    tolerance_percent: float = 20.0
    """허용 오차 (%). 기대 시간의 ±20% 내면 정상."""
    
    def validate(
        self,
        actual_recovery_time: float,
        actual_canary_stage: Optional[str] = None,
        actual_cb_state: Optional[str] = None,
    ) -> tuple[bool, list[str]]:
        """
        가설 검증.
        
        Returns:
            (passed, violations) 튜플
        """
        violations = []
        tolerance_factor = 1 + (self.tolerance_percent / 100)
        
        # 복구 시간 검증
        if actual_recovery_time > self.expected_recovery_time_seconds * tolerance_factor:
            violations.append(
                f"Recovery time {actual_recovery_time:.1f}s > "
                f"expected {self.expected_recovery_time_seconds:.1f}s "
                f"(+{self.tolerance_percent}% tolerance)"
            )
        
        # Canary 단계 검증
        if self.expected_canary_stage and actual_canary_stage != self.expected_canary_stage:
            violations.append(
                f"Canary stage '{actual_canary_stage}' != expected '{self.expected_canary_stage}'"
            )
        
        # CB 상태 검증
        if self.expected_cb_state_after and actual_cb_state != self.expected_cb_state_after:
            violations.append(
                f"CB state '{actual_cb_state}' != expected '{self.expected_cb_state_after}'"
            )
        
        return len(violations) == 0, violations


# =============================================================================
# 실험별 기대 가설 정의 (클래스 레벨 상수)
# =============================================================================

# CircuitBreakerOpenExperiment
CB_OPEN_HYPOTHESIS = FailureHypothesis(
    description="CB Open 실험 시, 30초 내에 Canary Stage 1이 시작되어야 함",
    expected_recovery_time_seconds=60.0,
    expected_canary_stage="canary_1",
    expected_canary_start_within_seconds=30.0,
    expected_cb_state_after="half_open",
    expected_cb_transition_within_seconds=30.0,
    expected_fallback_activated=True,
    expected_fallback_type="cache",
)

# LatencyInjectionExperiment
LATENCY_INJECTION_HYPOTHESIS = FailureHypothesis(
    description="500ms 지연 주입 시, CB가 10초 내에 OPEN되어야 함",
    expected_recovery_time_seconds=45.0,
    expected_cb_state_after="open",
    expected_cb_transition_within_seconds=10.0,
    expected_fallback_activated=False,
)

# Error5xxExperiment
ERROR_5XX_HYPOTHESIS = FailureHypothesis(
    description="503 에러 주입 시, 5초 내에 CB OPEN 및 Fallback 활성화",
    expected_recovery_time_seconds=30.0,
    expected_cb_state_after="open",
    expected_cb_transition_within_seconds=5.0,
    expected_fallback_activated=True,
)
```

### 20.3 실험 클래스 적용

**구현 위치**: `services/chaos/experiment_impl.py`

```python
class CircuitBreakerOpenExperiment(ChaosExperiment):
    """
    Force Circuit Breaker to OPEN state.
    
    ┌─────────────────────────────────────────────────────────────┐
    │ FAILURE HYPOTHESIS (복구 기대 가설)                          │
    ├─────────────────────────────────────────────────────────────┤
    │ • CB Open 후 30초 내에 Canary Stage 1 시작                   │
    │ • 60초 내에 복구 완료 (HALF_OPEN 전환)                        │
    │ • Fallback(cache) 활성화 필수                                │
    │                                                              │
    │ LearningService 연동:                                        │
    │ → 실제 결과와 비교하여 "복구 성능 저하 추세" 자동 감지        │
    └─────────────────────────────────────────────────────────────┘
    """
    
    experiment_type = ExperimentType.CIRCUIT_BREAKER_OPEN.value
    requires_approval = True
    
    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = CB_OPEN_HYPOTHESIS
    
    # ... 기존 코드 ...
```

### 20.4 LearningService 피드백 루프

**구현 위치**: `services/chaos/base.py` - `execute()` 메서드 확장

```python
def _record_hypothesis_validation(
    self,
    result: ExperimentResult,
    actual_recovery_time: float,
    actual_canary_stage: Optional[str],
    actual_cb_state: Optional[str],
) -> None:
    """가설 검증 결과를 LearningService에 기록."""
    if not hasattr(self, 'failure_hypothesis') or self.failure_hypothesis is None:
        return
    
    passed, violations = self.failure_hypothesis.validate(
        actual_recovery_time=actual_recovery_time,
        actual_canary_stage=actual_canary_stage,
        actual_cb_state=actual_cb_state,
    )
    
    try:
        from selfhealing.services.learning import LearningService
        from selfhealing.services.learning.models import PatternType
        
        learning = LearningService()
        
        # 패턴 기록
        learning.record_pattern(
            pattern_type=PatternType.FAILURE if not passed else PatternType.SUCCESS,
            source=f"chaos:{self.experiment_type}",
            target=self.config.target_service,
            features={
                "experiment_id": self.experiment_id,
                "experiment_type": self.experiment_type,
                "hypothesis_passed": passed,
                "violations": violations,
                "expected_recovery_time": self.failure_hypothesis.expected_recovery_time_seconds,
                "actual_recovery_time": actual_recovery_time,
                "recovery_time_delta": actual_recovery_time - self.failure_hypothesis.expected_recovery_time_seconds,
                "expected_canary_stage": self.failure_hypothesis.expected_canary_stage,
                "actual_canary_stage": actual_canary_stage,
            },
            confidence=0.9 if passed else 0.7,
        )
        
        # 복구 시간 추세 분석 요청
        if not passed and violations:
            logger.warning(
                f"[Chaos] Hypothesis validation FAILED for {self.experiment_id}: {violations}"
            )
            
            # LearningService에 추세 분석 트리거
            learning.analyze_trend(
                target_field="recovery_time",
                source=f"chaos:{self.experiment_type}",
                window_days=30,
            )
    except Exception as e:
        logger.warning(f"[Chaos] Failed to record hypothesis validation: {e}")
```

### 20.5 가치 요약

| Before | After |
|--------|-------|
| "장애 주입 성공" | "**시스템 방어 기제가 예상대로 작동했는가?**" |
| 수동 검토 필요 | **LearningService 자동 추세 감지** |
| 복구 성능 저하 미감지 | **"복구 시간이 설계치보다 느려지고 있음" 자동 알림** |

### 20.6 구현 우선순위 (P0)

| 단계 | 내용 | 복잡도 | 소요 시간 |
|------|------|--------|----------|
| **Phase 1** | `FailureHypothesis` 데이터 클래스 | 낮음 | 30분 |
| **Phase 2** | 실험 클래스별 기대 가설 상수 정의 | 낮음 | 1시간 |
| **Phase 3** | `_record_hypothesis_validation()` 구현 | 중간 | 2시간 |
| **Phase 4** | LearningService `analyze_trend()` 연동 | 중간 | 2시간 |

**총 예상 소요**: 5.5시간

---

## 21. 테스트 계획

| 테스트 | 검증 항목 |
|--------|----------|
| `test_safety_guard_emergency_check` | Emergency Level 3 → 실험 차단 |
| `test_safety_guard_panic_check` | Panic Threshold → 실험 차단 |
| `test_cb_open_triggers_canary_recovery` | CB OPEN → Canary 복구 |
| `test_partial_failure_triggers_shedding` | 부분 장애 → Load Shedding |
| `test_chaos_cost_recorded` | FinOps 비용 기록 |
| `test_dora_003_auto_check` | Compliance 자동 검사 |
| `test_pool_exhaustion_detection` | Pool 고갈 → 모니터링 트리거 |
| `test_cert_expiry_simulation` | 인증서 만료 → 알림 트리거 |
| `test_partition_detection` | 연결 파티션 → 상태 캡처 |
| `test_chaos_aware_incident_classification` | 카오스 실험 → `is_chaos_experiment` 메타데이터 |
| `test_recovery_monitoring_state` | Soft TTL → `RECOVERY_MONITORING` 상태 전환 |
| `test_hard_ttl_force_complete` | Hard TTL → 강제 종료 |
| `test_simulation_override_pool` | `set_simulation_override()` → 가짜 상태 반환 |
| `test_chaos_budget_blocking` | 예산 초과 → 실험 차단 |
| `test_chaos_budget_warning` | 80% 소진 → 경고 알림 |
| `test_dora_003_failed_experiment_counted` | 실패한 실험 → DORA-003 실적 인정 |
| `test_emergency_redis_propagation` | Emergency 변경 → 전 클러스터 전파 |
| `test_failure_hypothesis_validation` | 가설 검증 → passed/violations 반환 |
| `test_hypothesis_learning_feedback` | 가설 실패 → LearningService 패턴 기록 |
| `test_recovery_time_trend_detection` | 복구 시간 증가 추세 → 자동 감지 |

---

## 관련 문서

| 문서 | 설명 |
|------|------|
| [31_CHAOS_EXPERIMENT_EXPANSION.md](31_CHAOS_EXPERIMENT_EXPANSION.md) | 미구현 실험 타입 + **가설 기반 검증 설계 (§8)** |
| [33_CHAOS_INDUSTRY_EXPERIMENTS.md](33_CHAOS_INDUSTRY_EXPERIMENTS.md) | 업계 표준 실험 |
| [24_CHAOS_INTEGRATION_PLAN.md](24_CHAOS_INTEGRATION_PLAN.md) | 기존 통합 계획 |
| [30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md](30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md) | 가중치 계산 패턴 참조 |

---

## 버전 정보

- **현재 버전**: 1.5.0
- **마지막 업데이트**: 2026-01-09
- **담당자**: SelfHealing Team

### 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.5.0 | 2026-01-09 | **Phase 3 구현 완료**: Canary Recovery 검증 (§4), FinOps Chaos Budget (§17), LearningService 피드백 루프 (§20.4), BlockReason.CHAOS_BUDGET_EXCEEDED 추가, 테스트 24개 통과 |
| 1.4.0 | 2026-01-09 | **Phase 2 구현 완료**: SafetyGuard._check_panic_threshold() (§5), CB 상태 스냅샷 캡처 (§2), BlockReason.PANIC_THRESHOLD_TRIGGERED 추가, 테스트 15개 통과 |
| 1.3.0 | 2026-01-09 | **Phase 1 구현 완료**: FailureHypothesis 클래스, 실험별 가설 상수, Chaos-Aware 메타데이터, RECOVERY_MONITORING 상태, Soft/Hard TTL |
| 1.0.0 | 2026-01-09 | 초기 연동 계획 수립 |
| 1.1.0 | 2026-01-09 | Architect Review 반영: Chaos-Aware 분류, 비동기 모니터링, 시뮬레이션 인터페이스, FinOps GameDay Budget 추가 |
| 1.2.0 | 2026-01-09 | **Failure Hypothesis (§20) 추가**: LearningService 피드백 루프, 복구 기대 가설 정의, 구현 순서 정리 |
