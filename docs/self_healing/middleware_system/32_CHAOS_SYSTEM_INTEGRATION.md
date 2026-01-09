# 32. Chaos Engineering 힐링 시스템 연동 계획

> **작성일**: 2026-01-09  
> **상태**: 계획 수립  
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
| Circuit Breaker | ❌ 미연동 | - |
| Emergency Mode | ❌ 미연동 | - |
| Canary Recovery | ❌ 미연동 | - |
| Panic Threshold | ❌ 미연동 | - |
| Load Shedding | ❌ 미연동 | - |
| Freeze Mode | ❌ 미연동 | - |
| Corruption Shield | ❌ 미연동 | - |
| DLQ Service | ❌ 미연동 | - |
| Throttle (Adaptive) | ❌ 미연동 | - |
| FinOps Service | ❌ 미연동 | - |
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

| 우선순위 | 연동 대상 | 이유 |
|----------|----------|------|
| 🔴 P1 | Circuit Breaker | 핵심 연동 - CB 상태 기반 실험 |
| 🔴 P1 | Emergency Mode | Safety Guard 필수 체크 |
| 🔴 P1 | Panic Threshold | 안전장치 - 시스템 보호 |
| 🟠 P2 | Canary Recovery | CB OPEN 실험 검증 |
| 🟠 P2 | Load Shedding | Partial Failure 실험 검증 |
| 🟠 P2 | FinOps | 비용 추적 |
| � P2 | ConnectionPoolMonitor | Pool 고갈 실험 검증 |
| 🟠 P2 | CertificateExpiryMonitor | 인증서 만료 실험 검증 |
| 🟢 P3 | Corruption Shield | 데이터 무결성 실험 |
| 🟢 P3 | DLQ | 통계 분리 (이미 구현됨) |
| 🟢 P3 | Throttle | 지연 실험 검증 |
| 🟢 P3 | Compliance | DORA 준수 |
| 🟢 P3 | ConnectionHealthMonitor | 연결 상태/파티션 실험 검증 |

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

## 14. 테스트 계획

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

---

## 관련 문서

| 문서 | 설명 |
|------|------|
| [31_CHAOS_EXPERIMENT_EXPANSION.md](31_CHAOS_EXPERIMENT_EXPANSION.md) | 미구현 실험 타입 |
| [33_CHAOS_INDUSTRY_EXPERIMENTS.md](33_CHAOS_INDUSTRY_EXPERIMENTS.md) | 업계 표준 실험 |
| [24_CHAOS_INTEGRATION_PLAN.md](24_CHAOS_INTEGRATION_PLAN.md) | 기존 통합 계획 |

---

## 버전 정보

- **현재 버전**: 1.0.0
- **마지막 업데이트**: 2026-01-09
- **담당자**: SelfHealing Team
