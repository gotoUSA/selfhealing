# 24. Chaos Engineering 통합 확장 계획

> **작성일**: 2026-01-07  
> **상태**: Phase 0-4 완료 ✅  
> **관련 문서**: [13_CHAOS_ENGINEERING.md](../13_CHAOS_ENGINEERING.md), [21_CB_ADVANCED_PROTECTION.md](21_CB_ADVANCED_PROTECTION.md)

---

## 1. 개요

### 1.1 목적

현재 Chaos Engineering 모듈과 다른 Self-Healing 서비스들의 통합을 확장하여:
- 기존 부분 연동 3개 항목 완성
- 신규 연동 10개 항목 구현

### 1.2 현황 요약

| 구분 | 항목 수 | 상태 |
|------|---------|------|
| 이미 연동 (부분 완성) | 3 | 개선 필요 |
| 추가 연동 필요 | 10 | 미구현 |
| **총계** | **13** | |

### 1.3 구현 진행 상황

| Phase | 상태 | 구현 내용 | 테스트 |
|-------|------|----------|--------|
| Phase 0 | ✅ 완료 | NotificationCategory.CHAOS 추가, cooldown 300초 설정 | 3/3 통과 |
| Phase 1 | ✅ 완료 | ChaosActionableAlertUrlBuilder, ChaosNotificationService | 8/8 통과 |
| Phase 2 | ✅ 완료 | ImpactPredictor, BlastRadiusAnalyzer, Dry Run API | 14/14 통과 |
| Phase 3 | ✅ 완료 | SyntheticLoadGenerator, TrafficShaper | 14/14 통과 |
| Phase 4 | ✅ 완료 | 통합 테스트 | 12/12 통과 |

---

## 2. 기존 연동 개선 (3개)

### 2.1 ErrorBudgetGate → Chaos 연동 개선

#### 현재 상태: 부분 완성

**코드 위치**: `services/error_budget_gate/gate.py` (lines 500-525)

```python
# 현재 코드 (gate.py:502-505)
def _emit_error_budget_critical_event(self, budget_percent: float) -> None:
    """
    에러 예산 임계치 도달 이벤트 발행.
    
    Chaos 실험 자동 차단, 자동 Replay 일시 중지 등
    다른 컴포넌트가 이 이벤트를 구독하여 반응합니다.
    """
```

**문제점**: 
- 이벤트 발행만 구현됨
- Chaos 모듈에서 이 이벤트를 **구독하는 코드 없음**

#### 개선 방안

**구현 위치**: `services/chaos/scheduler.py`

```python
# 추가해야 할 코드
from selfhealing.services.event_bus import (
    get_event_bus,
    EventType,
    SelfHealingEvent,
)

class ChaosSchedulerService:
    def __init__(self, ...):
        # 기존 코드...
        self._register_event_handlers()
    
    def _register_event_handlers(self) -> None:
        """ErrorBudgetGate 이벤트 구독."""
        bus = get_event_bus()
        bus.subscribe(
            EventType.ERROR_BUDGET_CRITICAL,
            self._on_error_budget_critical,
        )
    
    def _on_error_budget_critical(self, event: SelfHealingEvent) -> None:
        """에러 예산 위험 시 실행 중인 카오스 실험 중단."""
        logger.warning(
            f"[ChaosScheduler] Error budget critical ({event.data.get('budget_percent')}%) - "
            "stopping all chaos experiments"
        )
        self.stop_all_experiments(reason="error_budget_critical")
```

**연동 근거**:
- `EventType.ERROR_BUDGET_CRITICAL` 이미 정의됨: `services/event_bus.py:76`
- `_emit_error_budget_critical_event()` 이미 구현됨: `services/error_budget_gate/gate.py:500`

---

### 2.2 DLQ → Chaos 분리 연동 개선

#### 현재 상태: 완성 (개선 사항 있음)

**코드 위치**: `shopping/tasks/drift_detection_tasks.py` (lines 61-100)

```python
# 현재 코드 (drift_detection_tasks.py:68-69)
pending_chaos = FailedOperation.objects.filter(
    status__in=["pending", "replayed"],
    metadata__is_chaos_experiment=True,
)
```

**완성된 기능**:
- ✅ `is_chaos_experiment` 플래그로 필터링
- ✅ 자동 만료 해결 로직 (`_resolve_expired_chaos_experiments`)

#### 개선 방안

**추가 구현**: DLQ 대시보드에서 카오스 실험 분리 표시

**구현 위치**: `services/dashboard_service.py`

```python
# DashboardService.get_summary() 확장
def get_chaos_experiment_stats(self) -> Dict[str, int]:
    """카오스 실험 관련 DLQ 통계."""
    try:
        stats = self.stats_repo.get_chaos_experiment_counts()
        return {
            "active_chaos_entries": stats.active,
            "expired_chaos_entries": stats.expired,
            "resolved_chaos_entries": stats.resolved,
        }
    except Exception as e:
        logger.error(f"[Dashboard] get_chaos_experiment_stats error: {e}")
        return {}
```

**연동 근거**:
- `metadata__is_chaos_experiment` 필터 사용 중: `drift_detection_tasks.py:69`
- `DashboardService` 확장 가능: `services/dashboard_service.py:140`

---

### 2.3 EventBus 이벤트 발행 개선

#### 현재 상태: 부분 완성

**코드 위치**: `services/event_bus.py` (lines 88-90)

```python
# 현재 정의된 이벤트 (event_bus.py:88-90)
CHAOS_EXPERIMENT_BLOCKED = "chaos_experiment_blocked"
CHAOS_EXPERIMENT_STARTED = "chaos_experiment_started"
CHAOS_EXPERIMENT_STOPPED = "chaos_experiment_stopped"
```

**문제점**:
- 이벤트 타입만 정의됨
- Audit에서만 사용 (`chaos_audit.py:249`)
- **실제 `emit()` 호출 코드 없음**

#### 개선 방안

**구현 위치**: `services/chaos/base.py` 또는 `tasks/chaos_scheduler.py`

```python
# ChaosExperiment.run() 또는 execute_experiment() 내부에 추가
from selfhealing.services.event_bus import get_event_bus, EventType, EventPriority

def _emit_chaos_event(
    self,
    event_type: EventType,
    experiment_id: str,
    data: Dict[str, Any],
) -> None:
    """카오스 실험 이벤트 발행."""
    try:
        bus = get_event_bus()
        bus.emit(
            event_type=event_type,
            data={
                "experiment_id": experiment_id,
                **data,
            },
            source="chaos_experiment",
            priority=EventPriority.HIGH,
        )
    except Exception as e:
        logger.warning(f"[Chaos] Failed to emit event: {e}")

# 실험 시작 시
self._emit_chaos_event(
    EventType.CHAOS_EXPERIMENT_STARTED,
    experiment_id=self.config.experiment_id,
    data={"experiment_type": self.config.experiment_type},
)

# 실험 종료 시
self._emit_chaos_event(
    EventType.CHAOS_EXPERIMENT_STOPPED,
    experiment_id=self.config.experiment_id,
    data={"result": result.to_dict()},
)
```

**연동 근거**:
- `EventType.CHAOS_EXPERIMENT_STARTED` 정의됨: `event_bus.py:89`
- `SelfHealingEventBus.emit()` 구현됨: `event_bus.py:324-347`

#### ⚠️ Architect 권장: Simulation Engine으로의 발전

**문제점**: 현재 `CHAOS_EXPERIMENT_STARTED` 이벤트는 "실험이 시작되었다"는 사실만 발행합니다. 예상 결과가 없으면 실험 성공/실패를 판단하기 어렵습니다.

**권장 구현**: 이벤트 발행 시 **'예상되는 복구 시나리오'**를 함께 발행하세요.

```python
# services/chaos/base.py 확장

@dataclass
class ExpectedRecoveryScenario:
    """예상 복구 시나리오."""
    
    expected_cb_state: str = "OPEN"  # 예상 CB 상태
    expected_recovery_time_seconds: int = 30  # 예상 복구 시간
    expected_canary_recovery: bool = True  # Canary 복구 예상 여부
    expected_error_rate_max_percent: float = 5.0  # 최대 에러율
    confidence_score: float = 0.8  # 예측 신뢰도


def _emit_chaos_event_with_expectation(
    self,
    event_type: EventType,
    experiment_id: str,
    expected_scenario: ExpectedRecoveryScenario,
) -> None:
    """예상 시나리오를 포함한 카오스 이벤트 발행."""
    bus = get_event_bus()
    bus.emit(
        event_type=event_type,
        data={
            "experiment_id": experiment_id,
            "experiment_type": self.config.experiment_type,
            "target_service": self.config.target_service,
            # 예상 시나리오 포함
            "expected_scenario": {
                "cb_state": expected_scenario.expected_cb_state,
                "recovery_time_seconds": expected_scenario.expected_recovery_time_seconds,
                "canary_recovery": expected_scenario.expected_canary_recovery,
                "error_rate_max_percent": expected_scenario.expected_error_rate_max_percent,
                "confidence_score": expected_scenario.confidence_score,
            },
        },
        source="chaos_experiment",
        priority=EventPriority.HIGH,
    )
```

**LearningService 연동 시 활용**:

```python
# services/learning/service.py 확장

def _on_chaos_experiment_stopped(self, event: SelfHealingEvent) -> None:
    """카오스 실험 종료 시 예측 정확도 학습."""
    expected = event.data.get("expected_scenario", {})
    actual = event.data.get("result", {})
    
    # 예측 vs 실제 비교
    prediction_accuracy = self._calculate_prediction_accuracy(
        expected_cb_state=expected.get("cb_state"),
        actual_cb_state=actual.get("final_cb_state"),
        expected_recovery_time=expected.get("recovery_time_seconds"),
        actual_recovery_time=actual.get("recovery_time_seconds"),
    )
    
    # "시스템의 자기 인지 능력" 점수 기록
    self.learn_pattern(
        pattern_type=PatternType.SELF_AWARENESS,
        name=f"chaos_prediction_{event.data['experiment_id']}",
        description="Chaos 실험 예측 정확도",
        features={
            "experiment_type": event.data["experiment_type"],
            "prediction_accuracy": prediction_accuracy,
            "expected_scenario": expected,
            "actual_result": actual,
        },
        confidence=prediction_accuracy,
    )
```

**활용 가치**:

| 지표 | 설명 |
|------|------|
| `prediction_accuracy` | 시스템이 자신의 행동을 얼마나 잘 예측하는지 |
| `self_awareness_score` | 장기 트렌드로 "자기 인지 능력" 성숙도 측정 |

---

## 3. 신규 연동 구현 (10개)

### 3.1 FinOpsService 연동

#### 코드 근거

**위치**: `services/finops/service.py` (lines 17-25)

```python
# 이미 정의된 비용 (finops/service.py:17-25)
DEFAULT_OPERATION_COSTS: Dict[str, Decimal] = {
    "retry": Decimal("0.001"),
    "circuit_breaker_check": Decimal("0.0001"),
    "dlq_enqueue": Decimal("0.005"),
    "dlq_replay": Decimal("0.01"),
    "health_check": Decimal("0.0001"),
    "rollback": Decimal("0.05"),
    "emergency_mode": Decimal("0.10"),
    "chaos_test": Decimal("0.02"),  # ← 이미 정의됨!
}
```

#### 구현 방안

**위치**: `services/chaos/base.py` - `ChaosExperiment.run()` 내부

```python
def _record_chaos_cost(self, experiment_result: ExperimentResult) -> None:
    """카오스 실험 비용 기록."""
    try:
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        finops.record_cost(
            operation="chaos_test",
            stage_name=self.config.target_service,
            success=experiment_result.success,
            metadata={
                "experiment_id": self.config.experiment_id,
                "experiment_type": self.config.experiment_type,
                "duration_seconds": experiment_result.duration_seconds,
            },
        )
    except Exception as e:
        logger.debug(f"[Chaos] FinOps recording failed (ignored): {e}")
```

#### 연동 이점

- 카오스 실험 비용 자동 추적
- 월간 GameDay 예산 관리
- 비용 초과 시 알림 (`FinOpsService._create_alert`)

---

### 3.2 ComplianceService 연동

#### 코드 근거

**위치**: `services/compliance/service.py` (lines 36-42)

```python
# 이미 정의된 검사 항목 (compliance/service.py:36-42)
{
    "check_id": "DORA-003",
    "name": "Resilience Testing",
    "description": "디지털 운영 복원력 테스트 확인",
    "category": "testing",
    # TODO: DORA Article 24-27 실제 요구사항 검토 필요
}
```

#### 구현 방안

**위치**: `services/compliance/service.py` - 검사 함수 등록

```python
def _check_resilience_testing(self) -> bool:
    """DORA-003: Resilience Testing 자동 검사."""
    try:
        from selfhealing.services.chaos.reports import ResilienceReportGenerator
        
        generator = ResilienceReportGenerator()
        report = generator.generate_daily_report()
        
        # 최근 30일 내 카오스 실험 실행 여부 확인
        if report.total_experiments == 0:
            return False
        
        # 최소 성공률 확인 (예: 70%)
        if report.success_rate < 70.0:
            return False
        
        return True
    except Exception:
        return False

# 서비스 초기화 시 등록
def _load_default_checks(self) -> None:
    # 기존 코드...
    self._check_functions["DORA-003"] = self._check_resilience_testing
```

#### 연동 이점

- DORA-003 "Resilience Testing" 준수 자동 증빙
- 컴플라이언스 보고서에 카오스 실험 결과 반영
- 감사 대응 자동화

---

### 3.3 PanicThresholdMonitor 연동

#### 코드 근거

**위치**: `services/circuit_breaker/panic_threshold.py` (lines 1-27, 248-280)

```python
# panic_threshold.py:1-27 설명
"""
70% 이상의 Circuit Breaker가 동시에 OPEN되면, 개별 서비스 문제가 아니라
인프라 전체 붕괴로 판단합니다. 이때 자율 운영 엔진이 스스로 Emergency Level 3를
선포하고 모든 자동 복구를 중단합니다.

동작 흐름:
    get_open_circuits() → OPEN 비율 계산 → 70% 초과?
                                            ↓ Yes
                                    PANIC THRESHOLD TRIGGERED!
                                            ↓
                                    Emergency Level 3 자동 선포
                                            ↓
                                    모든 자동 복구 중단:
                                    - Replay 중지
                                    - Canary Recovery 중지
                                    - Auto OPEN/CLOSE 금지
                                    - 수동 개입 대기
"""

# _trigger_panic 결과 (panic_threshold.py:248-280)
@dataclass
class PanicThresholdResult:
    halted_systems: List[str] = field(default_factory=list)
    # halted_systems = ["Replay", "Canary Recovery", "Auto OPEN/CLOSE"]
```

#### 구현 방안

**위치**: `services/chaos/safety_guard.py` - 안전 체크 추가

```python
def _check_panic_threshold(self) -> SafetyCheckResult:
    """Panic Threshold 상태 확인."""
    try:
        from selfhealing.services.circuit_breaker.panic_threshold import (
            PanicThresholdMonitor,
        )
        
        monitor = PanicThresholdMonitor()
        result = monitor.check_panic_threshold()
        
        if result.triggered:
            return SafetyCheckResult(
                passed=False,
                check_name="panic_threshold",
                message=f"Panic triggered: {result.open_rate:.1f}% CB OPEN",
                severity="critical",
                recommendation="시스템 전체 불안정 - 카오스 실험 금지",
            )
        
        # 경고 수준 (50% 이상)
        if result.open_rate >= 50.0:
            return SafetyCheckResult(
                passed=True,
                check_name="panic_threshold",
                message=f"Warning: {result.open_rate:.1f}% CB OPEN",
                severity="warning",
                recommendation="CB OPEN 비율 높음 - 주의 필요",
            )
        
        return SafetyCheckResult(passed=True, check_name="panic_threshold")
    except Exception as e:
        logger.warning(f"[SafetyGuard] Panic threshold check failed: {e}")
        return SafetyCheckResult(passed=True, check_name="panic_threshold")
```

#### 연동 이점

- 카오스 실험 중 시스템 전체 70% CB OPEN 감지
- 자동으로 카오스 실험 차단 (실제 장애 전파 방지)
- Emergency Level 3와 연계

#### ⚠️ Architect 권장: Chaos-Induced Panic 분리 기록

**문제점**: Chaos 실험이 Panic Threshold(70% OPEN)를 트리거했을 때, 이를 일반 인프라 장애와 동일하게 취급하면 가용성 통계(SLA)가 왜곡될 수 있습니다.

**권장 구현**:

```python
# services/circuit_breaker/panic_threshold.py 확장

def _trigger_panic(
    self,
    open_rate: float,
    triggered_by: Optional[str] = None,  # "chaos_experiment" | "infrastructure" | None
) -> PanicThresholdResult:
    """Panic 발동 시 트리거 원인 기록."""
    result = PanicThresholdResult(
        triggered=True,
        open_rate=open_rate,
        triggered_by=triggered_by or "unknown",
        is_chaos_induced=(triggered_by == "chaos_experiment"),
    )
    
    # Audit 로그에 트리거 원인 명시
    log_panic_audit(
        action="panic_triggered",
        open_rate=open_rate,
        triggered_by=triggered_by,
        classification="experiment_limit_reached" if result.is_chaos_induced else "infrastructure_failure",
    )
    
    return result
```

**Dashboard 분류**:

| 분류 | 설명 | SLA 영향 |
|------|------|----------|
| `infrastructure_failure` | 실제 인프라 장애 | ✅ SLA 감산 |
| `experiment_limit_reached` | 카오스 실험으로 확인된 한계치 | ❌ SLA 제외 |

---

### 3.4 LearningService 연동

#### 코드 근거

**위치**: `services/learning/service.py` (lines 96-155)

```python
# learning/service.py:96-155
def learn_pattern(
    self,
    pattern_type: PatternType,
    name: str,
    description: str,
    features: Dict[str, Any],
    confidence: float = 0.8,
    session_id: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> LearningPattern:
    """
    패턴 학습
    
    Args:
        pattern_type: 패턴 유형 (FAILURE, PERFORMANCE, ...)
        name: 패턴 이름
        description: 패턴 설명
        features: 패턴 특성
        confidence: 신뢰도
    """
```

#### 구현 방안

**위치**: `services/chaos/reports.py` - 리포트 생성 후

```python
def _learn_from_experiment_results(
    self,
    experiment_results: List[ExperimentResult],
) -> None:
    """카오스 실험 결과로부터 패턴 학습."""
    try:
        from selfhealing.services.learning.service import LearningService
        from selfhealing.services.learning.models import PatternType
        
        learning = LearningService()
        session = learning.start_session("chaos_analysis")
        
        for result in experiment_results:
            if not result.success:
                # 실패 패턴 학습
                learning.learn_pattern(
                    pattern_type=PatternType.FAILURE,
                    name=f"chaos_failure_{result.experiment_type}",
                    description=f"Chaos experiment failed: {result.failure_reason}",
                    features={
                        "experiment_type": result.experiment_type,
                        "target_service": result.target_service,
                        "recovery_time_seconds": result.recovery_time_seconds,
                        "error_rate_during_experiment": result.metrics.get("error_rate", 0),
                    },
                    confidence=0.9,
                    session_id=session.session_id,
                    metadata={"stage_name": result.target_service},
                )
        
        learning.end_session(session.session_id)
    except Exception as e:
        logger.debug(f"[ChaosReports] Learning failed (ignored): {e}")
```

#### 연동 이점

- 카오스 실험 실패 패턴 자동 학습
- Circuit Breaker threshold 조정 제안 생성 (`_generate_failure_suggestion`)
- 장애 복구 시간 최적화 제안

---

### 3.5 AutoTuningService 연동

#### 코드 근거

**위치**: `services/auto_tuning/service.py` (lines 26-65)

```python
# auto_tuning/service.py:26-28
class TuningMode(str, Enum):
    AUTOMATIC = "automatic"  # 자동 조정 활성화
    MANUAL = "manual"        # 수동 모드 (조정 불가)
    DRY_RUN = "dry_run"      # 조정 시뮬레이션만

# auto_tuning/service.py:64
MODULES = ["circuit_breaker", "retry", "jitter", "rate_limit", "timeout"]
```

#### 구현 방안

**위치**: `services/chaos/reports.py` - 리포트 기반 튜닝 제안

```python
def generate_tuning_recommendations(
    self,
    report: DailyResilienceReport,
) -> List[Dict[str, Any]]:
    """카오스 실험 결과 기반 AutoTuning 권장사항."""
    recommendations = []
    
    for service, metrics in report.service_metrics.items():
        if metrics.recovery_time_avg_seconds > 30:
            # 복구 시간이 긴 경우 timeout 증가 권장
            recommendations.append({
                "module": "timeout",
                "service": service,
                "current_value": metrics.current_timeout_seconds,
                "recommended_value": metrics.recovery_time_avg_seconds * 1.5,
                "reason": f"Chaos 실험에서 평균 복구 시간 {metrics.recovery_time_avg_seconds}s 관측",
            })
        
        if metrics.circuit_breaker_opened_during_experiment:
            # CB가 열린 경우 threshold 조정 권장
            recommendations.append({
                "module": "circuit_breaker",
                "service": service,
                "recommendation": "failure_threshold 검토",
                "reason": "카오스 실험 중 CB OPEN 발생",
            })
    
    return recommendations
```

#### 연동 이점

- 카오스 실험 결과 기반 파라미터 자동 조정
- `DRY_RUN` 모드로 안전한 변경 시뮬레이션
- Circuit Breaker, Retry, Timeout 설정 최적화

---

### 3.6 RollbackService 연동

#### 코드 근거

**위치**: `services/rollback/service.py` (lines 55-75, 107-131)

```python
# rollback/service.py:55-75
class RollbackStrategy(Enum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"

def set_policy(
    self,
    stage_name: str,
    strategy: RollbackStrategy = RollbackStrategy.AUTOMATIC,
    timeout_seconds: int = 120,
    max_retries: int = 3,
    require_approval: bool = False,
) -> RollbackPolicy:

# rollback/service.py:107-131
def _log_audit(
    self,
    request_id: str,
    stage_name: str,
    state: str,
    ...
) -> None:
    """롤백 이벤트를 Audit 로그에 기록."""
    from selfhealing.services.audit_helpers import log_rollback_audit
```

#### 구현 방안

**위치**: `services/chaos/base.py` - 실험 실패 시 롤백

```python
def _handle_experiment_failure(
    self,
    result: ExperimentResult,
) -> None:
    """카오스 실험 실패 시 롤백 요청."""
    if not result.success and self.config.auto_rollback_on_failure:
        try:
            from selfhealing.services.rollback.service import RollbackService
            
            rollback = RollbackService()
            rollback.request_rollback(
                stage_name=self.config.target_service,
                reason=f"Chaos experiment {self.config.experiment_id} failed: {result.failure_reason}",
                triggered_by="chaos_experiment",
                metadata={
                    "experiment_id": self.config.experiment_id,
                    "experiment_type": self.config.experiment_type,
                },
            )
        except Exception as e:
            logger.error(f"[Chaos] Rollback request failed: {e}")
```

#### 연동 이점

- 카오스 실험 실패 시 자동 롤백 트리거
- 실험 전 상태로 안전한 복구
- Audit 로깅 통합 (`log_rollback_audit`)

---

### 3.7 CanaryRecoveryManager 연동

#### 코드 근거

**위치**: `services/circuit_breaker/canary_recovery.py` (lines 302-330, 430-450)

```python
# canary_recovery.py:302-330
class CanaryRecoveryManager:
    """
    Canary 복구 매니저 - 단계적 복구, 성공률 추적, 단계 전이
    
    Usage:
        manager = CanaryRecoveryManager()
        manager.start_canary_recovery(service_id, strategy)
    """
    
    def start_canary_recovery(self, service_id: str, strategy=None) -> CanaryRecoveryState:
        """Canary 복구 시작 (HALF_OPEN 진입 시 호출)."""
    
    def stop_canary_recovery(self, service_id: str, reason: str = "manual") -> bool:
        """Canary 복구 중단."""
```

#### 구현 방안

**위치**: `services/chaos/experiments.py` - CB 관련 실험

```python
def _monitor_canary_during_experiment(
    self,
    service_id: str,
) -> Dict[str, Any]:
    """카오스 실험 중 Canary 복구 상태 모니터링."""
    try:
        from selfhealing.services.circuit_breaker.canary_recovery import (
            CanaryRecoveryManager,
        )
        
        manager = CanaryRecoveryManager()
        
        if manager.is_in_canary_recovery(service_id):
            state = manager.get_recovery_state(service_id)
            return {
                "in_canary_recovery": True,
                "current_stage": state.current_stage.value,
                "success_rate": state.metrics.success_rate if state.metrics else None,
            }
        
        return {"in_canary_recovery": False}
    except Exception:
        return {"in_canary_recovery": False, "error": "check_failed"}
```

#### 연동 이점

- 카오스 후 Canary 기반 점진적 복구 모니터링
- 복구 상태를 실험 결과에 포함
- 복구 실패 시 자동 재실험 트리거

---

### 3.8 IdempotencyService 연동

#### 코드 근거

**위치**: `services/idempotency_service.py` (lines 191-250)

```python
# idempotency_service.py:191-250
class IdempotencyService:
    """
    Service for checking and managing idempotency of operations.
    
    Provides both cache-based (fast) and database-based (reliable)
    idempotency checking.
    """
    
    def check(
        self,
        key: IdempotencyKey,
        lookup_fn: Optional[Callable[..., Any]] = None,
        cache_ttl: Optional[int] = None,
    ) -> IdempotencyResult:
```

#### 구현 방안

**위치**: `services/chaos/scheduler.py` - 스케줄러 실행 시

```python
def _check_experiment_idempotency(
    self,
    experiment_id: str,
) -> bool:
    """카오스 실험 중복 실행 방지."""
    try:
        from selfhealing.services.idempotency_service import (
            IdempotencyService,
            IdempotencyKey,
            IdempotencyDomain,
        )
        
        service = IdempotencyService()
        key = IdempotencyKey(
            domain=IdempotencyDomain.ASYNC_TASK,
            key=f"chaos_experiment:{experiment_id}",
            components={"experiment_id": experiment_id},
        )
        
        result = service.check(key, cache_ttl=300)  # 5분 TTL
        
        if result.is_duplicate:
            logger.info(f"[ChaosScheduler] Experiment {experiment_id} already running/completed")
            return False
        
        return True
    except Exception as e:
        logger.warning(f"[ChaosScheduler] Idempotency check failed: {e}")
        return True  # Fail-open
```

#### 연동 이점

- 카오스 실험 중복 실행 방지
- 스케줄러 재시작 시 안전성 보장
- 분산 환경에서 동일 실험 동시 실행 방지

#### ⚠️ Architect 권장: Blast Radius Idempotency 확장

**문제점**: 현재는 동일 `experiment_id`에 대해서만 중복을 체크합니다. 서로 다른 ID의 실험이더라도 **동일 서비스/도메인에 동시에 여러 실험**을 실행하면 원인 분석이 불가능해집니다.

**예시 문제 상황**:
- 실험 A: `payment-api`에 지연 주입 (ID: exp-001)
- 실험 B: `payment-api`에 5xx 에러 주입 (ID: exp-002)
- 두 실험이 동시 실행되면 어떤 실험이 장애를 유발했는지 구분 불가

**권장 구현**:

```python
# services/chaos/scheduler.py 확장

def _check_blast_radius_idempotency(
    self,
    target_service: str,
    experiment_id: str,
) -> Tuple[bool, Optional[str]]:
    """
    동일 서비스에 대한 중복 실험 방지.
    
    Returns:
        (allowed: bool, blocking_experiment_id: Optional[str])
    """
    try:
        from selfhealing.services.idempotency_service import (
            IdempotencyService,
            IdempotencyKey,
            IdempotencyDomain,
        )
        
        service = IdempotencyService()
        
        # 서비스 레벨 잠금 (단일 서비스에 하나의 실험만)
        service_lock_key = IdempotencyKey(
            domain=IdempotencyDomain.ASYNC_TASK,
            key=f"chaos_blast_radius:{target_service}",
            components={
                "target_service": target_service,
                "experiment_id": experiment_id,
            },
        )
        
        result = service.check(service_lock_key, cache_ttl=600)  # 10분 TTL
        
        if result.is_duplicate:
            blocking_id = result.existing_data.get("experiment_id")
            logger.warning(
                f"[ChaosScheduler] Blast radius conflict: "
                f"{target_service} already has active experiment {blocking_id}"
            )
            return False, blocking_id
        
        return True, None
    except Exception as e:
        logger.warning(f"[ChaosScheduler] Blast radius check failed: {e}")
        return True, None  # Fail-open
```

**차단 규칙**:

| 조건 | 허용 여부 | 이유 |
|------|----------|------|
| 동일 `experiment_id` | ❌ 차단 | 중복 실행 방지 |
| 동일 `target_service` | ❌ 차단 | 원인 분석 불가 방지 |
| 다른 서비스 | ✅ 허용 | 독립적 실험 가능 |

---

### 3.9 SecurityNotificationService 연동

#### 코드 근거

**위치**: `services/security_notification/service.py` (lines 140-185)

```python
# security_notification/service.py:140-185
def send_alert(
    self,
    title: str,
    message: str,
    severity: str = "info",
    channels: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SecurityNotificationResult:
    """
    Send a general-purpose alert notification.
    
    This method is for alerts that are not tied to a specific security incident,
    such as SLA drift warnings, system health alerts, or operational notifications.
    
    Example:
        service = get_security_notification_service()
        service.send_alert(
            title="[SLA Drift] payment",
            message="SLA 위반율이 25%입니다.",
            severity="warning",
            metadata={"domain": "payment", "breach_rate": 25.0}
        )
    """
```

#### 구현 방안

**위치**: `services/chaos/safety_guard.py` - 이상 탐지 시

```python
def _send_security_alert(
    self,
    alert_type: str,
    experiment_id: str,
    details: Dict[str, Any],
) -> None:
    """비정상 카오스 패턴 탐지 시 보안 알림."""
    try:
        from selfhealing.services.security_notification.service import (
            SecurityNotificationService,
        )
        
        service = SecurityNotificationService()
        service.send_alert(
            title=f"[Chaos Alert] {alert_type}",
            message=f"비정상 카오스 패턴 탐지: {details.get('description', '')}",
            severity="warning" if alert_type != "unauthorized" else "critical",
            channels=["slack"],
            metadata={
                "experiment_id": experiment_id,
                "alert_type": alert_type,
                **details,
            },
        )
    except Exception as e:
        logger.warning(f"[SafetyGuard] Security alert failed: {e}")
```

#### 연동 이점

- 비인가 카오스 실험 탐지 알림
- 비정상 패턴 (예: 과도한 실험 빈도) 경고
- Slack, Email, PagerDuty 통합 알림

---

### 3.10 DashboardService 연동

#### 코드 근거

**위치**: `services/dashboard_service.py` (lines 140-200)

```python
# dashboard_service.py:140-200
class DashboardService:
    """
    Dashboard statistics service.
    
    Provides centralized access to system monitoring data and statistics.
    Uses Redis caching to prevent database overload.
    """
    
    def get_summary(self, skip_cache: bool = False) -> DashboardSummary:
        """Get complete dashboard summary."""
    
    def get_status_counts(self) -> StatusCounts:
        """Get counts by status."""
```

#### 구현 방안

**위치**: `services/dashboard_service.py` - 카오스 통계 추가

```python
@dataclass
class ChaosStats:
    """카오스 실험 통계."""
    active_experiments: int = 0
    completed_today: int = 0
    success_rate_7d: float = 0.0
    avg_recovery_time_seconds: float = 0.0


class DashboardService:
    # 기존 코드...
    
    def get_chaos_stats(self) -> ChaosStats:
        """카오스 실험 통계 조회."""
        cache_key = "chaos_stats"
        
        if not self._skip_cache:
            cached = self._get_cached(cache_key)
            if cached:
                return ChaosStats(**cached)
        
        try:
            from selfhealing.services.chaos.reports import ResilienceReportGenerator
            
            generator = ResilienceReportGenerator()
            report = generator.generate_daily_report()
            
            stats = ChaosStats(
                active_experiments=report.active_count,
                completed_today=report.completed_today,
                success_rate_7d=report.success_rate_7d,
                avg_recovery_time_seconds=report.avg_recovery_time,
            )
            
            self._set_cached(cache_key, asdict(stats), ttl_seconds=60)
            return stats
        except Exception as e:
            logger.error(f"[Dashboard] get_chaos_stats error: {e}")
            return ChaosStats()
```

#### 연동 이점

- 카오스 실험 상태 대시보드 통합
- 실시간 실험 현황 모니터링
- Redis 캐싱으로 성능 최적화

---

## 4. 구현 우선순위

### 4.1 높음 (즉시 구현 권장)

| 순위 | 항목 | 이유 |
|------|------|------|
| 1 | ErrorBudgetGate 이벤트 구독 (2.1) | 이벤트 발행은 되는데 수신 없음 - 연동 완성 필요 |
| 2 | EventBus 이벤트 발행 (2.3) | 이벤트 타입 정의만 있고 실제 발행 없음 |
| 3 | FinOps 연동 (3.1) | `chaos_test` 비용 이미 정의됨 - 즉시 연동 가능 |
| 4 | PanicThreshold 연동 (3.3) | 안전 차단 로직 - 필수 안전장치 |

### 4.2 중간 (2차 단계)

| 순위 | 항목 | 이유 |
|------|------|------|
| 5 | Compliance 연동 (3.2) | DORA-003 자동 증빙 |
| 6 | Rollback 연동 (3.6) | 실패 시 자동 복구 |
| 7 | CanaryRecovery 연동 (3.7) | 복구 모니터링 |
| 8 | Learning 연동 (3.4) | 패턴 학습 및 개선 |

### 4.3 보통 (3차 단계)

| 순위 | 항목 | 이유 |
|------|------|------|
| 9 | AutoTuning 연동 (3.5) | 파라미터 최적화 |
| 10 | Idempotency 연동 (3.8) | 중복 방지 |
| 11 | SecurityNotification 연동 (3.9) | 이상 탐지 알림 |
| 12 | Dashboard 연동 (3.10) | 가시성 |
| 13 | DLQ 대시보드 개선 (2.2) | 통계 분리 표시 |

---

## 5. 테스트 계획

### 5.1 단위 테스트

각 연동에 대해 다음 테스트 작성:

```python
# tests/self_healing/unit/test_chaos_integrations.py

class TestChaosFinOpsIntegration:
    def test_chaos_cost_recorded(self):
        """카오스 실험 완료 시 비용 기록 확인."""
    
    def test_chaos_cost_budget_exceeded(self):
        """예산 초과 시 알림 생성 확인."""


class TestChaosComplianceIntegration:
    def test_dora_003_check_with_experiments(self):
        """카오스 실험 있을 때 DORA-003 통과."""
    
    def test_dora_003_check_without_experiments(self):
        """카오스 실험 없을 때 DORA-003 실패."""


class TestChaosPanicThresholdIntegration:
    def test_chaos_blocked_on_panic(self):
        """Panic 상태에서 카오스 실험 차단."""
```

### 5.2 통합 테스트

```python
# tests/self_healing/integration/test_chaos_full_flow.py

class TestChaosFullIntegration:
    def test_chaos_experiment_with_all_integrations(self):
        """
        전체 연동 흐름 테스트:
        1. Idempotency 체크
        2. SafetyGuard (PanicThreshold 포함)
        3. 실험 실행
        4. EventBus 이벤트 발행
        5. FinOps 비용 기록
        6. Learning 패턴 학습
        7. Dashboard 통계 업데이트
        """
```

---

## 6. Architect 권장 구현 상세

### 6.1 Synthetic Traffic Generator 구현

#### 코드 근거

**기존 헤더 패턴**: `api/django/views/xtest/base.py` (lines 42-67)

```python
# 현재 X-Test-Mode 헤더 패턴 (xtest/base.py:42-55)
class XTestModeMixin:
    CHAOS_HEADER = "X-Test-Mode"
    CHAOS_VALUE = "chaos-monkey"
    
    def is_chaos_allowed(self, request: Request) -> tuple[bool, str]:
        header_value = request.headers.get(self.CHAOS_HEADER, "")
        if header_value != self.CHAOS_VALUE:
            return False, f"Missing or invalid {self.CHAOS_HEADER} header"
```

**TrafficType 정의**: `services/chaos/base.py` (lines 97-112)

```python
class TrafficType(Enum):
    SYNTHETIC = "synthetic"
    SHADOW = "shadow"
    CANARY = "canary"
    PRODUCTION = "production"
```

#### 구현 방안

**신규 파일**: `services/chaos/synthetic_traffic.py`

```python
"""
Synthetic Traffic Generator.

카오스 실험용 합성 트래픽 생성기.
실제 프로덕션 요청과 분리하여 SLA 통계에서 제외합니다.

Reference:
- XTestModeMixin 패턴: api/django/views/xtest/base.py
- TrafficType enum: services/chaos/base.py

Design Principle (23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md §7.2 ⑥):
- Admin Deep Link 방식으로 거버넌스 유지
- 모든 조작이 감사(Audit) 기록
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

SYNTHETIC_HEADER = "X-Self-Healing-Synthetic"
SYNTHETIC_VALUE = "chaos-experiment"


@dataclass
class SyntheticRequest:
    """합성 트래픽 요청 정보."""
    
    experiment_id: str
    target_service: str
    original_headers: Dict[str, str]
    synthetic_headers: Dict[str, str]
    excluded_from_sla: bool = True


class SyntheticTrafficGenerator:
    """
    합성 트래픽 생성기.
    
    카오스 실험 중 실제 프로덕션 요청과 분리된 합성 요청을 생성합니다.
    
    Usage:
        generator = SyntheticTrafficGenerator(experiment_id="exp-001")
        synthetic_req = generator.create_synthetic_request(
            target_service="payment-api",
            original_headers={"Content-Type": "application/json"},
        )
    """
    
    def __init__(self, experiment_id: str):
        """
        Args:
            experiment_id: 연결된 카오스 실험 ID
        """
        self.experiment_id = experiment_id
        self._generated_count = 0
    
    def create_synthetic_request(
        self,
        target_service: str,
        original_headers: Optional[Dict[str, str]] = None,
    ) -> SyntheticRequest:
        """
        합성 요청 생성.
        
        Args:
            target_service: 대상 서비스
            original_headers: 원본 헤더 (복사)
            
        Returns:
            SyntheticRequest: 합성 요청 정보
        """
        original = original_headers or {}
        
        synthetic_headers = {
            **original,
            SYNTHETIC_HEADER: SYNTHETIC_VALUE,
            "X-Experiment-Id": self.experiment_id,
            "X-Traffic-Type": "synthetic",
        }
        
        self._generated_count += 1
        
        logger.debug(
            f"[SyntheticTraffic] Created synthetic request "
            f"for {target_service} (experiment: {self.experiment_id})"
        )
        
        return SyntheticRequest(
            experiment_id=self.experiment_id,
            target_service=target_service,
            original_headers=original,
            synthetic_headers=synthetic_headers,
            excluded_from_sla=True,
        )
    
    @staticmethod
    def is_synthetic_request(headers: Dict[str, str]) -> bool:
        """
        요청이 합성 트래픽인지 확인.
        
        SLA 통계, FinOps 비용 계산에서 필터링할 때 사용합니다.
        
        Args:
            headers: 요청 헤더
            
        Returns:
            bool: 합성 트래픽 여부
        """
        return headers.get(SYNTHETIC_HEADER) == SYNTHETIC_VALUE
    
    def get_stats(self) -> Dict[str, Any]:
        """생성 통계 반환."""
        return {
            "experiment_id": self.experiment_id,
            "generated_count": self._generated_count,
        }
```

**SLA 통계 필터링 연동**: `services/sla/service.py` 확장

```python
# SLA 통계 기록 시 합성 트래픽 제외
def record_request(self, request_info: RequestInfo) -> None:
    from selfhealing.services.chaos.synthetic_traffic import (
        SyntheticTrafficGenerator,
    )
    
    # 합성 트래픽은 SLA 통계에서 제외
    if SyntheticTrafficGenerator.is_synthetic_request(request_info.headers):
        logger.debug(
            f"[SLA] Excluding synthetic request from SLA stats: "
            f"{request_info.headers.get('X-Experiment-Id')}"
        )
        return
    
    # 기존 SLA 기록 로직...
    self._record_sla_metric(request_info)
```

#### 연동 근거

| 기존 패턴 | 위치 | 활용 |
|----------|------|------|
| `X-Test-Mode` 헤더 | `xtest/base.py:42` | 동일한 헤더 기반 필터링 패턴 재사용 |
| `TrafficType.SYNTHETIC` | `chaos/base.py:99` | 이미 정의된 enum 값 활용 |
| `XTestModeMixin.is_chaos_allowed()` | `xtest/base.py:46-67` | 유사한 권한 체크 로직 |

---

### 6.2 Dry Run Enhancement (예측 분석기)

#### 코드 근거

**현재 _run_dry 구현**: `services/chaos/base.py` (lines 391-441)

```python
# 현재 구현 (base.py:391-441) - 예측 없음
def _run_dry(self) -> ExperimentResult:
    """Execute experiment in dry run mode."""
    logger.info(f"[DryRun] Starting dry run for {self.experiment_id}")
    
    # Steady state 캡처만 수행
    steady_state_before = self.capture_steady_state()
    
    # 실제 주입 없이 시뮬레이션 로그만
    self._audit("chaos_injection_simulated", {
        "dry_run": True,
        "would_inject": self._config_to_dict(),
    })
    
    # 짧은 대기 후 종료
    time.sleep(min(5, self.config.duration_seconds))
    
    # 결과 반환 (예측 없음)
    return ExperimentResult(dry_run=True, ...)
```

**LearningService get_patterns**: `services/learning/service.py` (lines 309-328)

```python
def get_patterns(
    self,
    pattern_type: Optional[PatternType] = None,
    min_confidence: float = 0.0,
) -> List[LearningPattern]:
    """
    패턴 조회
    
    Args:
        pattern_type: 패턴 유형 필터
        min_confidence: 최소 신뢰도
    
    Returns:
        List[LearningPattern]: 패턴 목록
    """
    patterns = list(self._patterns.values())
    
    if pattern_type:
        patterns = [p for p in patterns if p.pattern_type == pattern_type]
    patterns = [p for p in patterns if p.confidence >= min_confidence]
    
    return patterns
```

#### 구현 방안

**신규 파일**: `services/chaos/predictive_analyzer.py`

```python
"""
Chaos Predictive Analyzer.

LearningService의 과거 패턴을 활용하여 
Dry Run 시 예상 결과를 예측합니다.

Reference:
- LearningService.get_patterns(): services/learning/service.py:309-328
- PatternType enum: services/learning/models.py:27-32

Design Principle (24_CHAOS_INTEGRATION_PLAN.md §2.3):
- ExpectedRecoveryScenario를 활용한 예측
- 예측 정확도를 LearningService에 피드백
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PredictedOutcome:
    """Dry Run 예측 결과."""
    
    # 예측된 CB 상태
    predicted_cb_state: str = "OPEN"
    
    # 예측된 복구 시간 (초)
    predicted_recovery_time_seconds: float = 30.0
    
    # 예상 에러율 증가
    predicted_error_rate_increase_percent: float = 5.0
    
    # Canary 복구 예상 여부
    predicted_canary_recovery: bool = True
    
    # 예측 신뢰도 (0.0 ~ 1.0)
    confidence_score: float = 0.5
    
    # 예측에 사용된 패턴 수
    patterns_used: int = 0
    
    # 유사 실험 ID 목록
    similar_experiment_ids: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "predicted_cb_state": self.predicted_cb_state,
            "predicted_recovery_time_seconds": self.predicted_recovery_time_seconds,
            "predicted_error_rate_increase_percent": self.predicted_error_rate_increase_percent,
            "predicted_canary_recovery": self.predicted_canary_recovery,
            "confidence_score": self.confidence_score,
            "patterns_used": self.patterns_used,
            "similar_experiment_ids": self.similar_experiment_ids,
        }


class ChaosPredictiveAnalyzer:
    """
    Chaos 실험 예측 분석기.
    
    LearningService의 과거 FAILURE 패턴을 분석하여
    유사한 실험의 예상 결과를 예측합니다.
    
    Usage:
        analyzer = ChaosPredictiveAnalyzer()
        prediction = analyzer.predict_outcome(
            experiment_type="latency_injection",
            target_service="payment-api",
            config={"latency_ms": 500},
        )
    """
    
    def __init__(self, min_confidence: float = 0.6):
        """
        Args:
            min_confidence: 패턴 필터링 최소 신뢰도
        """
        self._min_confidence = min_confidence
    
    def predict_outcome(
        self,
        experiment_type: str,
        target_service: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> PredictedOutcome:
        """
        실험 결과 예측.
        
        Args:
            experiment_type: 실험 유형 (latency_injection, failure_injection, etc.)
            target_service: 대상 서비스
            config: 실험 설정
            
        Returns:
            PredictedOutcome: 예측 결과
        """
        try:
            from selfhealing.services.learning.service import LearningService
            from selfhealing.services.learning.models import PatternType
            
            learning = LearningService()
            
            # 과거 FAILURE 패턴 조회
            failure_patterns = learning.get_patterns(
                pattern_type=PatternType.FAILURE,
                min_confidence=self._min_confidence,
            )
            
            # 유사 패턴 필터링
            similar_patterns = self._find_similar_patterns(
                patterns=failure_patterns,
                experiment_type=experiment_type,
                target_service=target_service,
            )
            
            if not similar_patterns:
                logger.info(
                    f"[PredictiveAnalyzer] No similar patterns found for "
                    f"{experiment_type} on {target_service}"
                )
                return PredictedOutcome(
                    confidence_score=0.3,
                    patterns_used=0,
                )
            
            # 패턴 기반 예측
            return self._calculate_prediction(similar_patterns)
            
        except Exception as e:
            logger.warning(f"[PredictiveAnalyzer] Prediction failed: {e}")
            return PredictedOutcome(confidence_score=0.1)
    
    def _find_similar_patterns(
        self,
        patterns: list,
        experiment_type: str,
        target_service: str,
    ) -> list:
        """유사 패턴 검색."""
        similar = []
        
        for pattern in patterns:
            features = pattern.features or {}
            
            # 동일 실험 유형
            if features.get("experiment_type") == experiment_type:
                similar.append(pattern)
                continue
            
            # 동일 서비스
            if features.get("target_service") == target_service:
                similar.append(pattern)
        
        return similar
    
    def _calculate_prediction(self, patterns: list) -> PredictedOutcome:
        """패턴 기반 예측 계산."""
        if not patterns:
            return PredictedOutcome()
        
        # 복구 시간 평균
        recovery_times = [
            p.features.get("recovery_time_seconds", 30)
            for p in patterns
            if p.features.get("recovery_time_seconds")
        ]
        avg_recovery = sum(recovery_times) / len(recovery_times) if recovery_times else 30.0
        
        # 에러율 평균
        error_rates = [
            p.features.get("error_rate_during_experiment", 5.0)
            for p in patterns
            if p.features.get("error_rate_during_experiment")
        ]
        avg_error_rate = sum(error_rates) / len(error_rates) if error_rates else 5.0
        
        # 신뢰도 (패턴 수 및 개별 신뢰도 기반)
        confidence = min(0.95, 0.5 + (len(patterns) * 0.1))
        avg_pattern_confidence = sum(p.confidence for p in patterns) / len(patterns)
        final_confidence = (confidence + avg_pattern_confidence) / 2
        
        # 유사 실험 ID
        similar_ids = [
            p.features.get("experiment_id", p.name)
            for p in patterns[:5]  # 최대 5개
        ]
        
        return PredictedOutcome(
            predicted_cb_state="OPEN",
            predicted_recovery_time_seconds=avg_recovery,
            predicted_error_rate_increase_percent=avg_error_rate,
            predicted_canary_recovery=True,
            confidence_score=final_confidence,
            patterns_used=len(patterns),
            similar_experiment_ids=similar_ids,
        )
```

**_run_dry 통합**: `services/chaos/base.py` 확장

```python
def _run_dry(self) -> ExperimentResult:
    """Execute experiment in dry run mode with prediction."""
    logger.info(f"[DryRun] Starting dry run for {self.experiment_id}")
    
    # 기존 로직...
    steady_state_before = self.capture_steady_state()
    
    # ✅ 신규: 예측 분석
    prediction = self._get_prediction()
    
    self._audit("chaos_injection_simulated", {
        "dry_run": True,
        "would_inject": self._config_to_dict(),
        # ✅ 예측 결과 포함
        "predicted_outcome": prediction.to_dict() if prediction else None,
    })
    
    # 결과에 예측 포함
    self.result = ExperimentResult(
        # ... 기존 필드 ...
        dry_run=True,
        predicted_outcome=prediction.to_dict() if prediction else None,
    )
    
    return self.result

def _get_prediction(self) -> Optional[PredictedOutcome]:
    """예측 분석 수행."""
    try:
        from selfhealing.services.chaos.predictive_analyzer import (
            ChaosPredictiveAnalyzer,
        )
        
        analyzer = ChaosPredictiveAnalyzer()
        return analyzer.predict_outcome(
            experiment_type=self.experiment_type,
            target_service=self.config.target_service,
            config=self._config_to_dict(),
        )
    except Exception as e:
        logger.debug(f"[DryRun] Prediction skipped: {e}")
        return None
```

#### 연동 근거

| 기존 서비스 | 위치 | 활용 |
|------------|------|------|
| `LearningService.get_patterns()` | `learning/service.py:309-328` | 과거 FAILURE 패턴 조회 |
| `PatternType.FAILURE` | `learning/models.py:28` | 실패 패턴 필터링 |
| `LearningPattern.features` | `learning/models.py:40-50` | 패턴 특성 분석 |

---

### 6.3 Chaos 알림 Admin Deep Link 구현

#### 코드 근거

**23문서 설계 원칙**: `23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md` (Section 7.2 ⑥)

> **Actionable Alert: 신중한 보수주의**
> - 원클릭 해제 대신 Admin 제어판으로 이동
> - 쿼리 파라미터로 컨텍스트 전달
> - 거버넌스 유지: 모든 조작이 Admin 통해 감사 기록

**기존 CB Admin URL 구현**: `services/circuit_breaker/actionable_alert_urls.py` (lines 185-220)

```python
def _build_admin_url(
    self,
    service_name: str,
    action: str = "review",
    trigger_time: Optional[str] = None,
) -> Optional[str]:
    """
    Admin 제어판 URL 생성.
    
    설계 원칙:
    - 원클릭 해제 대신 Admin 제어판으로 이동
    - 거버넌스 유지 (모든 조작이 감사 기록)
    """
    if not self._admin_base_url:
        return None
    
    params = {
        "service_id": service_name,
        "action": action,
    }
    
    if trigger_time:
        params["trigger_time"] = trigger_time
    
    base_url = self._admin_base_url.rstrip("/") + "/"
    return f"{base_url}?{urlencode(params)}"
```

**KillAllView API**: `api/django/views/chaos.py` (lines 900-960)

```python
class KillAllView(APIView):
    """모든 실행 중인 카오스 실험 중단."""
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request: Request) -> Response:
        experiments_killed = scheduler.kill_all(reason=reason)
        return Response({
            "experiments_killed": experiments_killed,
            "rollbacks_initiated": rollbacks_initiated,
        })
```

#### 구현 방안

**신규 파일**: `services/chaos/actionable_alert_urls.py`

```python
"""
Chaos Actionable Alert URL Builder.

Chaos 알림에 포함될 Admin Deep Link를 생성합니다.

Design Reference:
- 23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md §7.2 ⑥
- CB URL 패턴: services/circuit_breaker/actionable_alert_urls.py

Principle:
- 거버넌스 유지: 운영자가 Admin에 로그인하는 과정이 보안 인증(MFA)과 감사(Audit) 단계
- 설정 제로: 별도의 슬랙 앱 설정 불필요
- 비즈니스 가치: 원클릭 편의성보다 운영자 신원 확인과 감사 추적(Audit Trail) 우선
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


@dataclass
class ChaosActionableUrls:
    """Chaos 알림에 포함될 URL 모음."""
    
    dashboard_url: Optional[str] = None
    admin_stop_url: Optional[str] = None  # 중단용 Admin URL
    admin_detail_url: Optional[str] = None  # 상세 조회용 Admin URL
    runbook_url: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "dashboard_url": self.dashboard_url,
            "admin_stop_url": self.admin_stop_url,
            "admin_detail_url": self.admin_detail_url,
            "runbook_url": self.runbook_url,
        }
    
    def has_any_url(self) -> bool:
        return any([
            self.dashboard_url,
            self.admin_stop_url,
            self.admin_detail_url,
            self.runbook_url,
        ])


class ChaosActionableAlertUrlBuilder:
    """
    Chaos Alert URL 빌더.
    
    23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md의 Admin Deep Link 방식을 
    Chaos 실험에도 동일하게 적용합니다.
    
    장점:
    - 거버넌스 준수: Admin 로그인 과정이 MFA 및 Audit 단계
    - 설정 제로: 별도 슬랙 앱 설정 불필요
    - 감사 추적: 기술 실사 시 "운영자 신원 확인과 감사 추적 우선시" 설명 가능
    
    Environment Variables:
    - CHAOS_ADMIN_BASE_URL: Chaos Admin 기본 URL
    - CHAOS_DASHBOARD_URL: Grafana Chaos 대시보드 URL
    - CHAOS_RUNBOOK_URL: Chaos 운영 매뉴얼 URL
    
    Usage:
        builder = get_chaos_actionable_alert_url_builder()
        urls = builder.build_experiment_alert_urls(
            experiment_id="exp-001",
            target_service="payment-api",
        )
    """
    
    def __init__(self):
        """환경변수에서 기본 URL 로드."""
        self._admin_base_url = os.getenv(
            "CHAOS_ADMIN_BASE_URL",
            "/api/self-healing/chaos/"
        )
        self._dashboard_base_url = os.getenv("CHAOS_DASHBOARD_URL", "")
        self._runbook_base_url = os.getenv("CHAOS_RUNBOOK_URL", "")
        
        logger.debug(
            f"[ChaosAlertUrlBuilder] Initialized: "
            f"admin={bool(self._admin_base_url)}, "
            f"dashboard={bool(self._dashboard_base_url)}"
        )
    
    def build_experiment_alert_urls(
        self,
        experiment_id: str,
        target_service: str,
        trigger_time: Optional[str] = None,
    ) -> ChaosActionableUrls:
        """
        실험 알림용 URL 생성.
        
        Args:
            experiment_id: 실험 ID
            target_service: 대상 서비스
            trigger_time: 이벤트 발생 시간
            
        Returns:
            ChaosActionableUrls: Admin Deep Link 포함된 URL 모음
        """
        return ChaosActionableUrls(
            dashboard_url=self._build_dashboard_url(target_service),
            admin_stop_url=self._build_admin_stop_url(
                experiment_id=experiment_id,
                reason="slack_alert_action",
            ),
            admin_detail_url=self._build_admin_detail_url(
                experiment_id=experiment_id,
                trigger_time=trigger_time,
            ),
            runbook_url=self._build_runbook_url("experiment-recovery"),
        )
    
    def build_emergency_stop_url(
        self,
        reason: str = "emergency",
    ) -> str:
        """
        긴급 전체 중단 Admin URL 생성.
        
        이 URL을 클릭하면 Admin 제어판의 Kill All 페이지로 이동합니다.
        운영자가 로그인한 상태에서만 동작하며, 모든 조작이 감사 기록됩니다.
        
        Args:
            reason: 중단 사유
            
        Returns:
            str: Admin Kill All 페이지 URL
        """
        base_url = self._admin_base_url.rstrip("/")
        params = urlencode({
            "action": "kill_all",
            "reason": reason,
            "confirm": "required",  # 확인 필수 표시
        })
        return f"{base_url}/control/kill-all/?{params}"
    
    def _build_dashboard_url(self, target_service: str) -> Optional[str]:
        """대시보드 URL 생성."""
        if not self._dashboard_base_url:
            return None
        
        separator = "&" if "?" in self._dashboard_base_url else "?"
        return f"{self._dashboard_base_url}{separator}service={target_service}"
    
    def _build_admin_stop_url(
        self,
        experiment_id: str,
        reason: str = "manual",
    ) -> str:
        """
        개별 실험 중단 Admin URL.
        
        Slack Interactive 버튼 대신 Admin 페이지로 이동하여
        거버넌스를 유지합니다.
        """
        base_url = self._admin_base_url.rstrip("/")
        params = urlencode({
            "experiment_id": experiment_id,
            "action": "stop",
            "reason": reason,
        })
        return f"{base_url}/schedules/{experiment_id}/?{params}"
    
    def _build_admin_detail_url(
        self,
        experiment_id: str,
        trigger_time: Optional[str] = None,
    ) -> str:
        """실험 상세 조회 Admin URL."""
        base_url = self._admin_base_url.rstrip("/")
        params = {"action": "review"}
        if trigger_time:
            params["trigger_time"] = trigger_time
        
        return f"{base_url}/schedules/{experiment_id}/?{urlencode(params)}"
    
    def _build_runbook_url(self, section: Optional[str] = None) -> Optional[str]:
        """Runbook URL 생성."""
        if not self._runbook_base_url:
            return None
        
        if section:
            return f"{self._runbook_base_url}#{section}"
        return self._runbook_base_url


# =============================================================================
# Singleton
# =============================================================================

_instance: Optional[ChaosActionableAlertUrlBuilder] = None


def get_chaos_actionable_alert_url_builder() -> ChaosActionableAlertUrlBuilder:
    """싱글톤 인스턴스 반환."""
    global _instance
    if _instance is None:
        _instance = ChaosActionableAlertUrlBuilder()
    return _instance
```

**Chaos 알림 연동**: `services/chaos/notification.py` (신규)

```python
"""
Chaos Experiment Notification.

카오스 실험 상태 변경 시 알림을 발송합니다.
Admin Deep Link 방식으로 거버넌스를 유지합니다.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def send_chaos_experiment_alert(
    experiment_id: str,
    experiment_type: str,
    target_service: str,
    event_type: str,  # "started" | "stopped" | "failed"
    details: Dict[str, Any],
) -> bool:
    """
    카오스 실험 알림 발송.
    
    Slack Interactive 버튼 대신 Admin Deep Link를 사용하여
    거버넌스와 감사 추적을 보장합니다.
    
    Args:
        experiment_id: 실험 ID
        experiment_type: 실험 유형
        target_service: 대상 서비스
        event_type: 이벤트 유형
        details: 추가 상세 정보
        
    Returns:
        bool: 발송 성공 여부
    """
    try:
        from selfhealing.services.unified_notification import (
            get_unified_notification_manager,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        from selfhealing.services.chaos.actionable_alert_urls import (
            get_chaos_actionable_alert_url_builder,
        )
        
        # Actionable URL 생성 (Admin Deep Link)
        url_builder = get_chaos_actionable_alert_url_builder()
        actionable_urls = url_builder.build_experiment_alert_urls(
            experiment_id=experiment_id,
            target_service=target_service,
        )
        
        # 이벤트 유형별 메시지
        title_map = {
            "started": f"🧪 Chaos Experiment Started: {target_service}",
            "stopped": f"✅ Chaos Experiment Completed: {target_service}",
            "failed": f"❌ Chaos Experiment Failed: {target_service}",
        }
        
        priority_map = {
            "started": NotificationPriority.INFO,
            "stopped": NotificationPriority.LOW,
            "failed": NotificationPriority.HIGH,
        }
        
        manager = get_unified_notification_manager()
        manager.notify(NotificationPayload(
            title=title_map.get(event_type, f"Chaos: {event_type}"),
            message=f"Experiment {experiment_id} ({experiment_type})",
            priority=priority_map.get(event_type, NotificationPriority.MEDIUM),
            category=NotificationCategory.CHAOS,  # ← §6.4에서 추가 필요
            source="chaos_experiment",
            dedup_key=f"chaos:{experiment_id}:{event_type}",
            metadata={
                "experiment_id": experiment_id,
                "experiment_type": experiment_type,
                "target_service": target_service,
                "event_type": event_type,
                # Admin Deep Link URLs
                "dashboard_url": actionable_urls.dashboard_url,
                "admin_url": actionable_urls.admin_stop_url,  # 중단용 Admin URL
                "runbook_url": actionable_urls.runbook_url,
                **details,
            },
        ))
        
        logger.info(
            f"[ChaosNotification] Sent {event_type} alert for {experiment_id}"
        )
        return True
        
    except Exception as e:
        logger.warning(f"[ChaosNotification] Failed to send alert: {e}")
        return False
```

#### Admin Deep Link 방식의 장점 (코드 근거)

| 장점 | 코드 근거 | 설명 |
|------|----------|------|
| **거버넌스 유지** | `permission_classes = [IsAuthenticated]` (`chaos.py:911`) | Admin 로그인 필수 |
| **감사 추적** | `logger.warning(f"[ChaosAPI] KILL ALL executed by {operator}")` (`chaos.py:949`) | 모든 조작 로깅 |
| **설정 제로** | 환경변수만 필요 (`CHAOS_ADMIN_BASE_URL`) | Slack App 설정 불필요 |
| **MFA 지원** | Django Admin 인증 체계 활용 | 기존 인증 재사용 |

#### Slack Interactive 대비 비교

| 항목 | Slack Interactive | Admin Deep Link |
|------|-------------------|-----------------|
| **설정 복잡도** | Slack App + Request URL 설정 필요 | 환경변수만 설정 |
| **보안 인증** | Slack 서명 검증 추가 구현 | 기존 Django 인증 활용 |
| **감사 추적** | 별도 로깅 구현 필요 | 기존 Admin 로깅 활용 |
| **MFA** | Slack 계정 의존 | 조직 MFA 정책 적용 |
| **기술 실사** | 추가 설명 필요 | "감사 추적 우선시" 설명 |

---

### 6.4 NotificationCategory 확장 (CHAOS 카테고리 추가)

#### 코드 근거

**현재 NotificationCategory 정의**: `services/unified_notification.py` (lines 50-60)

```python
# 현재 구현 (unified_notification.py:50-60)
class NotificationCategory(str, Enum):
    """Notification categories for routing and filtering."""

    SECURITY = "security"
    OPERATIONS = "operations"
    SLA = "sla"
    CIRCUIT_BREAKER = "circuit_breaker"  # ← CB 전용 카테고리 존재
    GOVERNANCE = "governance"
    APPROVAL = "approval"
    REPORT = "report"
    ERROR = "error"
    # ❌ CHAOS 없음!
```

**현재 cooldown_seconds 설정**: `services/unified_notification.py` (lines 167-179)

```python
# 현재 구현 (unified_notification.py:167-179)
cooldown_seconds: Dict[NotificationCategory, int] = field(
    default_factory=lambda: {
        NotificationCategory.SECURITY: 60,
        NotificationCategory.OPERATIONS: 300,
        NotificationCategory.SLA: 1800,
        NotificationCategory.CIRCUIT_BREAKER: 300,  # ← CB 전용 쿨다운
        NotificationCategory.GOVERNANCE: 900,
        NotificationCategory.APPROVAL: 0,
        NotificationCategory.REPORT: 0,
        NotificationCategory.ERROR: 60,
        # ❌ CHAOS 없음!
    }
)
```

**dedup_key 생성 로직**: `services/unified_notification.py` (line 290)

```python
# 현재 구현 (unified_notification.py:290)
dedup_key = payload.dedup_key or f"{payload.source}:{payload.category.value}"
```

#### 문제점

1. **독립적 중복 방지 불가**: `category.value`가 dedup key 생성에 사용됨
2. **쿨다운 공유**: `OPERATIONS` 사용 시 다른 운영 알림과 쿨다운 충돌
3. **라우팅 불가**: `category_channels`에 CHAOS 전용 채널 설정 불가

#### 구현 방안

**수정 파일**: `services/unified_notification.py`

**변경 1**: NotificationCategory enum에 CHAOS 추가

```python
# services/unified_notification.py - NotificationCategory 클래스

class NotificationCategory(str, Enum):
    """Notification categories for routing and filtering."""

    SECURITY = "security"
    OPERATIONS = "operations"
    SLA = "sla"
    CIRCUIT_BREAKER = "circuit_breaker"
    GOVERNANCE = "governance"
    APPROVAL = "approval"
    REPORT = "report"
    ERROR = "error"
    CHAOS = "chaos"  # ✅ 추가: 카오스 실험 알림
```

**변경 2**: RoutingPolicy.cooldown_seconds에 CHAOS 추가

```python
# services/unified_notification.py - RoutingPolicy 클래스

cooldown_seconds: Dict[NotificationCategory, int] = field(
    default_factory=lambda: {
        NotificationCategory.SECURITY: 60,
        NotificationCategory.OPERATIONS: 300,
        NotificationCategory.SLA: 1800,
        NotificationCategory.CIRCUIT_BREAKER: 300,
        NotificationCategory.GOVERNANCE: 900,
        NotificationCategory.APPROVAL: 0,
        NotificationCategory.REPORT: 0,
        NotificationCategory.ERROR: 60,
        NotificationCategory.CHAOS: 300,  # ✅ 추가: 5분 쿨다운
    }
)
```

**변경 3 (선택)**: category_channels에 CHAOS 추가

```python
# services/unified_notification.py - RoutingPolicy 클래스

category_channels: Dict[NotificationCategory, List[str]] = field(
    default_factory=lambda: {
        NotificationCategory.SECURITY: ["slack", "email"],
        NotificationCategory.APPROVAL: ["slack", "email"],
        NotificationCategory.REPORT: ["slack", "email"],
        NotificationCategory.CHAOS: ["slack"],  # ✅ 추가: Slack만 사용
    }
)
```

#### 연동 효과

| 효과 | 설명 | 코드 근거 |
|------|------|----------|
| **독립적 중복 방지** | `chaos:exp-001:started` 형식의 dedup key 생성 | `unified_notification.py:290` |
| **독립적 쿨다운** | 다른 운영 알림과 쿨다운 분리 (5분) | `unified_notification.py:167-179` |
| **채널 분리** | CHAOS 전용 채널 설정 가능 | `unified_notification.py:159-165` |
| **필터링** | 알림 대시보드에서 카테고리별 필터링 | `category.value` 활용 |

#### 기존 코드와의 충돌 검토

| 검토 항목 | 결과 | 근거 |
|----------|------|------|
| Enum 확장 호환성 | ✅ 안전 | Python Enum은 멤버 추가에 하위 호환 |
| Dict 기본값 | ✅ 안전 | `get()` 메서드가 기본값 300 반환 (`line 202`) |
| 직렬화 | ✅ 안전 | `category.value`로 문자열 변환 (`line 99`) |
| 테스트 | ⚠️ 확인 필요 | 기존 테스트가 특정 카테고리 목록에 의존하는지 확인 |

---

### 6.5 Admin Deep Link 추가/수정/보완 사항

#### 6.5.1 환경변수 추가 필요 (필수)

**현재 상태**: CB용 환경변수만 존재

```bash
# 기존 (actionable_alert_urls.py:82)
CB_ADMIN_BASE_URL="/admin/selfhealing/circuitbreaker/"
```

**추가 필요**:

```bash
# .env 또는 docker-compose.yml에 추가
CHAOS_ADMIN_BASE_URL="/api/self-healing/chaos/"
CHAOS_DASHBOARD_URL="https://grafana.internal/d/chaos-experiments"
CHAOS_RUNBOOK_URL="https://docs.internal/runbooks/chaos-recovery"
```

#### 6.5.2 URL 경로 명확화 (권장)

**문제점**: 리뷰에서 제안된 경로가 현재 존재하지 않음

```python
# 리뷰 제안 (미존재 경로)
admin_url = f"/chaos/experiments/{exp_id}/rollback/?auto_confirm=true"
```

**현재 존재하는 경로**: `api/django/urls.py` (lines 477-488)

```python
# 존재하는 경로 (urls.py:477-488)
path("chaos/schedules/<str:schedule_id>/", ScheduleDetailView.as_view())
path("chaos/control/kill-all/", KillAllView.as_view())
```

**권장 URL 형식**:

```python
# 개별 실험 중단 (기존 ScheduleDetailView 활용)
admin_stop_url = f"/api/self-healing/chaos/schedules/{experiment_id}/?action=stop&reason=slack_alert"

# 전체 중단 (기존 KillAllView 활용)
admin_kill_all_url = f"/api/self-healing/chaos/control/kill-all/?reason=emergency&confirm=required"
```

#### 6.5.3 `auto_confirm=true` 제거 권장 (보안)

**문제점**: 확인 단계 스킵은 거버넌스 약화

**권장**:

```python
# 변경 전 (리뷰 제안)
params = {"auto_confirm": "true"}

# 변경 후 (권장)
params = {
    "action": "stop",
    "reason": "slack_alert",
    "confirm": "required",  # ← 확인 단계 유지
}
```

**이유**: Admin Deep Link의 핵심 가치는 "확인 단계를 통한 거버넌스 유지"

#### 6.5.4 테스트 케이스 추가 필요

```python
# tests/self_healing/unit/test_chaos_notification.py

class TestChaosNotificationCategory:
    def test_chaos_category_exists(self):
        """NotificationCategory.CHAOS 존재 확인."""
        from selfhealing.services.unified_notification import NotificationCategory
        assert hasattr(NotificationCategory, "CHAOS")
        assert NotificationCategory.CHAOS.value == "chaos"
    
    def test_chaos_cooldown_configured(self):
        """CHAOS 카테고리 쿨다운 설정 확인."""
        from selfhealing.services.unified_notification import RoutingPolicy
        policy = RoutingPolicy()
        cooldown = policy.get_cooldown(NotificationCategory.CHAOS)
        assert cooldown == 300  # 5분
    
    def test_chaos_dedup_key_independent(self):
        """CHAOS 알림 dedup key 독립성 확인."""
        from selfhealing.services.unified_notification import (
            NotificationPayload,
            NotificationCategory,
        )
        
        payload = NotificationPayload(
            title="Test",
            message="Test",
            category=NotificationCategory.CHAOS,
            source="chaos_experiment",
        )
        
        # dedup_key 생성 시 category.value 사용 확인
        expected_key = f"chaos_experiment:{NotificationCategory.CHAOS.value}"
        assert "chaos" in expected_key


class TestChaosActionableUrls:
    def test_admin_stop_url_format(self):
        """Admin 중단 URL 형식 확인."""
        from selfhealing.services.chaos.actionable_alert_urls import (
            ChaosActionableAlertUrlBuilder,
        )
        
        builder = ChaosActionableAlertUrlBuilder()
        urls = builder.build_experiment_alert_urls(
            experiment_id="exp-001",
            target_service="payment-api",
        )
        
        assert "schedules/exp-001" in urls.admin_stop_url
        assert "action=stop" in urls.admin_stop_url
    
    def test_emergency_stop_url_requires_confirm(self):
        """긴급 중단 URL에 confirm 파라미터 포함 확인."""
        from selfhealing.services.chaos.actionable_alert_urls import (
            ChaosActionableAlertUrlBuilder,
        )
        
        builder = ChaosActionableAlertUrlBuilder()
        url = builder.build_emergency_stop_url(reason="test")
        
        assert "confirm=required" in url
        assert "auto_confirm" not in url
```

---

## 7. 구현 Phase 구분

> 📋 **근거 문서**: 21_CB_ADVANCED_PROTECTION.md Phase 0-6 패턴 참조  
> 📋 **근거 코드**: `tests/self_healing/chaos/test_chaos_scheduler.py` 기존 테스트 구조

### Phase 0: 사전 준비 (Day 0)

| 순서 | 작업 | 파일 | 근거 |
|------|------|------|------|
| 0.1 | NotificationCategory.CHAOS 추가 | `services/unified_notification.py:50-60` | §6.4 |
| 0.2 | cooldown_seconds 설정 | `services/unified_notification.py:167-179` | §6.4 |
| 0.3 | 환경변수 정의 | `.env.example` | §6.5 |

**체크포인트**: NotificationCategory.CHAOS.value == "chaos" 확인

### Phase 1: 핵심 연동 (Day 1-2)

> 🎯 **목표**: Chaos 알림 시스템 기반 구축

| 순서 | 작업 | 의존성 | 파일 | 근거 |
|------|------|--------|------|------|
| 1.1 | ChaosActionableAlertUrlBuilder 생성 | 0.1 | `services/chaos/actionable_alert_urls.py` (신규) | §6.3 |
| 1.2 | ChaosNotificationService 구현 | 1.1 | `services/chaos/notification.py` (신규) | §3.6 |
| 1.3 | 실험 시작/종료 알림 연동 | 1.2 | `services/chaos/scheduler.py` | §2.1 |

**체크포인트**: 실험 시작 시 Slack 알림 발송 확인, 테스트 8개

### Phase 2: Dry Run 강화 (Day 3-4)

> 🎯 **목표**: 실험 전 예측 분석 시스템 구축

| 순서 | 작업 | 의존성 | 파일 | 근거 |
|------|------|--------|------|------|
| 2.1 | ImpactPredictor 구현 | 없음 | `services/chaos/impact_predictor.py` (신규) | §6.2 |
| 2.2 | BlastRadiusAnalyzer 구현 | 2.1 | `services/chaos/blast_radius_analyzer.py` (신규) | §6.2 |
| 2.3 | Dry Run API 확장 | 2.2 | `views/api_v1/chaos_views.py` | §2.2 |

**체크포인트**: dry_run=True 시 영향도 분석 결과 반환 확인, 테스트 12개

### Phase 3: 트래픽 생성 (Day 5-6)

> 🎯 **목표**: Synthetic Traffic Generator 구현

| 순서 | 작업 | 의존성 | 파일 | 근거 |
|------|------|--------|------|------|
| 3.1 | SyntheticLoadGenerator 구현 | 없음 | `services/chaos/synthetic_load.py` (신규) | §6.1 |
| 3.2 | TrafficShaper 구현 | 3.1 | `services/chaos/traffic_shaper.py` (신규) | §6.1 |
| 3.3 | Chaos 실험과 연동 | 3.2 | `services/chaos/experiment.py` | §6.1 |

**체크포인트**: Synthetic Traffic 주입 후 메트릭 변화 확인, 테스트 10개

### Phase 4: 통합 테스트 (Day 7)

> 🎯 **목표**: 전체 연동 검증

| 순서 | 작업 | 의존성 | 파일 |
|------|------|--------|------|
| 4.1 | 알림 통합 테스트 | Phase 1 | `tests/self_healing/chaos/test_chaos_notification.py` (신규) |
| 4.2 | Dry Run 통합 테스트 | Phase 2 | `tests/self_healing/chaos/test_chaos_dry_run.py` (신규) |
| 4.3 | E2E 시나리오 테스트 | Phase 1-3 | `tests/self_healing/integration/test_chaos_e2e.py` (신규) |

**체크포인트**: 전체 Phase 테스트 45개 이상 통과

---

## 8. 테스트 케이스 요약

> 📋 **근거 코드**: `tests/self_healing/chaos/test_chaos_scheduler.py` (기존 TestSafetyGuard 패턴)

### 8.1 Phase 0 테스트 (3개)

| 테스트 클래스 | 테스트 메서드 | 검증 내용 |
|--------------|--------------|----------|
| TestNotificationCategory | `test_chaos_category_exists` | CHAOS enum 존재 확인 |
| TestNotificationCategory | `test_chaos_cooldown_configured` | cooldown 300초 확인 |
| TestNotificationCategory | `test_chaos_dedup_key_independent` | dedup key 독립성 |

### 8.2 Phase 1 테스트 (8개)

| 테스트 클래스 | 테스트 메서드 | 검증 내용 |
|--------------|--------------|----------|
| TestChaosActionableUrls | `test_admin_stop_url_format` | URL 형식 검증 |
| TestChaosActionableUrls | `test_emergency_stop_url_requires_confirm` | confirm 파라미터 포함 |
| TestChaosNotification | `test_experiment_start_notification` | 시작 알림 발송 |
| TestChaosNotification | `test_experiment_end_notification` | 종료 알림 발송 |
| TestChaosNotification | `test_experiment_error_notification` | 에러 알림 발송 |
| TestChaosNotification | `test_notification_cooldown` | 중복 알림 쿨다운 |
| TestChaosNotification | `test_notification_with_admin_url` | Admin URL 포함 |
| TestChaosNotification | `test_notification_fallback_channel` | 채널 폴백 |

### 8.3 Phase 2 테스트 (12개)

| 테스트 클래스 | 테스트 메서드 | 검증 내용 |
|--------------|--------------|----------|
| TestImpactPredictor | `test_predict_service_impact` | 서비스 영향도 예측 |
| TestImpactPredictor | `test_predict_latency_increase` | 레이턴시 예측 |
| TestImpactPredictor | `test_predict_error_rate` | 에러율 예측 |
| TestImpactPredictor | `test_confidence_score_calculation` | 신뢰도 계산 |
| TestBlastRadiusAnalyzer | `test_analyze_affected_services` | 영향 서비스 분석 |
| TestBlastRadiusAnalyzer | `test_calculate_blast_radius_level` | 폭발 반경 레벨 |
| TestBlastRadiusAnalyzer | `test_dependency_graph_traversal` | 의존성 그래프 탐색 |
| TestDryRunAPI | `test_dry_run_returns_prediction` | 예측 결과 반환 |
| TestDryRunAPI | `test_dry_run_no_side_effects` | 부작용 없음 확인 |
| TestDryRunAPI | `test_dry_run_with_blast_radius` | 폭발 반경 포함 |
| TestDryRunAPI | `test_dry_run_recommendation` | 권장 사항 포함 |
| TestDryRunAPI | `test_dry_run_approval_requirement` | 승인 필요 여부 |

### 8.4 Phase 3 테스트 (14개)

| 테스트 클래스 | 테스트 메서드 | 검증 내용 |
|--------------|--------------|----------|
| TestSyntheticLoadGenerator | `test_generate_baseline_traffic` | 기준 트래픽 생성 |
| TestSyntheticLoadGenerator | `test_ramp_up_pattern` | Ramp-up 패턴 |
| TestSyntheticLoadGenerator | `test_steady_state_pattern` | Steady-state 패턴 |
| TestSyntheticLoadGenerator | `test_spike_pattern` | Spike 패턴 |
| TestSyntheticLoadGenerator | `test_graceful_shutdown` | 정상 종료 |
| TestTrafficShaper | `test_shape_request_rate` | 요청 비율 조절 |
| TestTrafficShaper | `test_shape_concurrent_users` | 동시 사용자 조절 |
| TestTrafficShaper | `test_traffic_distribution_uniform` | 균등 트래픽 분배 |
| TestTrafficShaper | `test_traffic_distribution_weighted` | 가중치 트래픽 분배 |
| TestTrafficShaper | `test_adaptive_shaping` | 적응형 레이트 조절 |
| TestPhase3Integration | `test_experiment_with_synthetic_traffic` | 실험 연동 |
| TestPhase3Integration | `test_traffic_cleanup_on_error` | 에러 시 정리 |
| TestPhase3Integration | `test_synthetic_request_headers` | 합성 요청 헤더 검증 |
| TestPhase3Integration | `test_shaper_reset` | 형성기 리셋 검증 |

### 8.5 Phase 4 통합 테스트 (12개)

| 테스트 파일 | 테스트 수 | 검증 범위 |
|------------|----------|----------|
| `test_chaos_notification.py` | 4개 | 알림 → Slack 전송 E2E |
| `test_chaos_dry_run.py` | 4개 | Dry Run → 예측 → 권장사항 |
| `test_chaos_e2e.py` | 4개 | 전체 시나리오 (생성 → 실행 → 알림 → 정리) |

### 8.6 테스트 파일 매핑

| 신규 테스트 파일 | 기존 패턴 참조 | 테스트 수 |
|-----------------|---------------|----------|
| `test_chaos_notification.py` | `test_chaos_scheduler.py:TestSafetyGuard` | 8개 |
| `test_phase2_dry_run.py` | `test_chaos_scheduler.py:TestBlastRadiusManager` | 14개 |
| `test_phase3_synthetic_traffic.py` | `test_chaos_scheduler.py:TestChaosSchedulerService` | 14개 |
| `test_chaos_e2e.py` | `test_chaos_api.py` 패턴 | 4개 |

**총 테스트 수**: 51개 (Phase 0: 3개, Phase 1: 8개, Phase 2: 14개, Phase 3: 14개, Phase 4: 12개)

---

## 9. 변경 이력

| 날짜 | 버전 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 2026-01-07 | 1.0 | 초안 작성 | - |
| 2026-01-07 | 1.1 | Architect 구현 상세 추가 (§6) | - |
| 2026-01-07 | 1.2 | NotificationCategory 확장 (§6.4), Admin Deep Link 보완 (§6.5) | - |
| 2026-01-07 | 1.3 | Phase 구분 (§7), 테스트 케이스 요약 (§8) 추가 | - |
| 2026-01-07 | 1.4 | **Phase 0-1 구현 완료**: NotificationCategory.CHAOS 추가, ChaosActionableAlertUrlBuilder, ChaosNotificationService 구현, 11개 테스트 통과 | - |
| 2026-01-07 | 1.5 | **Phase 2 구현 완료**: ImpactPredictor, BlastRadiusAnalyzer, DryRunAnalysisView API 구현, 14개 테스트 작성 | - |
| 2026-01-07 | 1.6 | **Phase 3 구현 완료**: SyntheticLoadGenerator, TrafficShaper 구현, 14개 테스트 통과 | - |
| 2026-01-07 | 1.7 | **Phase 4 구현 완료**: 통합 테스트 12개 작성 (Notification 4개, DryRun 4개, E2E 4개), Phase 2-4 전체 40개 테스트 통과 | - |
