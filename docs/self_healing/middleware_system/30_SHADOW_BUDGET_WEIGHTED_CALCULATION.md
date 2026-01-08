# Shadow Budget 가중치 기반 계산 개선

> **문서 버전**: 1.4.0  
> **최종 수정일**: 2026-01-08  
> **관련 문서**: [12_ERROR_BUDGET.md](../12_ERROR_BUDGET.md) §13

---

## 📋 목차

1. [개요](#1-개요)
2. [현재 문제점](#2-현재-문제점)
3. [개선 방향](#3-개선-방향)
4. [구현 계획](#4-구현-계획)
   - 4.0 [핵심 설계 원칙](#40-핵심-설계-원칙-architect-review-반영)
   - 4.5 [SimulationBridge](#45-phase-5-simulationbridge---chaos-callback-연동-1시간)
   - 4.6 [Pending Reconciliation Freeze](#46-phase-6-pending-reconciliation-freeze-1시간)
5. [Audit 이벤트 추가](#5-audit-이벤트-추가)
   - 5.2.1 [RECONCILIATION_REJECTED 투명성 강화](#521-reconciliation_rejected-투명성-강화)
   - 5.2.2 [Accuracy Audit](#522-accuracy-audit---추정-정확도-사후-검증)
6. [알림 설정](#6-알림-설정)
7. [로그 설정](#7-로그-설정)
8. [테스트 계획](#8-테스트-계획)
9. [구현 우선순위](#9-구현-우선순위)
10. [변경 이력](#10-변경-이력)

---

## 1. 개요

### 1.1 배경

Shadow Budget Calculator는 Fail-Safe(Fail-Open) 기간 동안 놓친 에러를 추정하여 Error Budget에 반영합니다.
현재 구현은 모든 에러를 **동일한 가중치(0.001분)**로 처리하여, 에러의 심각도나 도메인 중요도를 반영하지 못합니다.

### 1.2 목표

| 목표 | 설명 |
|------|------|
| **정확한 Budget 소진 계산** | 에러 심각도/도메인별 가중치 반영 |
| **도메인 프리 설계** | 하드코딩 없이 설정 기반 동작 |
| **기존 인프라 활용** | 새 시스템 구축 없이 기존 코드 활용 |
| **관측성 강화** | Audit/알림/로그 통합 |

---

## 1.3 업계 관행 참고

### Google SRE 패턴

Google의 Site Reliability Engineering에서 **Error Budget 기반 의사결정** 개념 제시:
- Error Budget이 소진되면 배포 동결
- 심각도별 차등 Budget 소진율 적용

### Netflix 패턴

**Adaptive Error Budget** 패턴:
- 실시간 트래픽 기반 동적 Budget 조정
- 서비스 중요도에 따른 계층화 (Tier 1/2/3)

### Uber 패턴

**Cascading Failure Budget**:
- 의존성 체인을 고려한 연쇄 영향 계산
- 다운스트림 서비스 영향도 반영

### 본 시스템 적용

| 패턴 | 적용 방법 |
|------|----------|
| Google (심각도 차등) | `SEVERITY_BY_VIOLATION_TYPE` 활용 |
| Netflix (서비스 계층화) | `SLAConfig.thresholds_by_domain` 활용 |
| Uber (연쇄 영향) | 현재 범위 외 (향후 확장 가능) |

---

## 2. 현재 문제점

### 2.1 에러 가중치 하드코딩

**문제 코드**: [shadow_calculator.py#L74-L76](../../../packages/selfhealing-python/src/selfhealing/services/error_budget/reconciliation/shadow_calculator.py#L74-L76)

```python
# 단순화: 에러 1개 = 0.001분 소진 (실제로는 에러 심각도 등에 따라 가중치)
error_weight_minutes = 0.001
additional_consumed = estimated_errors * error_weight_minutes
```

**문제점**:
- 모든 에러가 동일한 가중치(0.001분)
- CRITICAL 에러와 LOW 에러의 구분 없음
- 결제 도메인 에러와 알림 도메인 에러의 구분 없음

### 2.2 도메인 하드코딩 (해결됨)

**해결된 코드**: [query_operations.py#L77-L97](../../../packages/selfhealing-python/src/selfhealing/services/dlq/query_operations.py#L77-L97)

```python
# 변경 전 (하드코딩)
sla_thresholds={
    "payment": sla_config.get_threshold("payment"),
    "point": sla_config.get_threshold("point"),
    ...
}

# 변경 후 (도메인 프리)
sla_thresholds=sla_config.get_all_thresholds()
```

### 2.3 Audit 이벤트 부재

**현재 상태**: [event_buffer.py#L46-L165](../../../packages/selfhealing-python/src/selfhealing/audit/event_buffer.py#L46-L165) 확인 결과:

| 이벤트 | 상태 |
|--------|------|
| `ERROR_BUDGET_DEPLETED` | ✅ 존재 |
| `ERROR_BUDGET_BLOCKED` | ✅ 존재 |
| `SHADOW_BUDGET_CALCULATED` | ❌ 없음 |
| `RECONCILIATION_APPROVED` | ❌ 없음 |
| `FAILSAFE_PERIOD_STARTED` | ❌ 없음 |

---

## 3. 개선 방향

### 3.1 권장 개선 조합

| 순위 | 방법 | 이유 | 근거 코드 |
|------|------|------|----------|
| **1순위** | Severity 매핑 | ✅ 이미 구현됨 | `SEVERITY_BY_VIOLATION_TYPE` |
| **2순위** | Domain SLA | ✅ 도메인 프리 완료 | `SLAConfig.get_all_thresholds()` |
| **3순위** | Learning 패턴 | ⚠️ 연계 개발 필요 | `LearningPattern.occurrence_count` |
| **4순위** | DLQ 메트릭 | ✅ domain+failure_type 저장됨 | `store_operations.py` |

### 3.2 제외된 방법

| 방법 | 제외 이유 |
|------|----------|
| **CorruptionShield 연계** | 오버엔지니어링 - Error Budget 시스템이 보안 에러까지 책임질 필요 없음. 보안 에러는 별도 SecurityIncident 시스템에서 처리됨 ([security_incident.py#L202-L214](../../../shopping/models/security_incident.py#L202-L214)) |

### 3.3 왜 이 조합인가?

#### 근거 1: Severity 매핑 - 즉시 사용 가능

[security_violation_service.py#L127-L164](../../../packages/selfhealing-python/src/selfhealing/services/security_violation_service.py#L127-L164)

```python
SEVERITY_BY_VIOLATION_TYPE: dict[str, Severity] = {
    ViolationType.SIGNATURE_INVALID: Severity.CRITICAL,
    ViolationType.DATA_TAMPERED: Severity.CRITICAL,
    ViolationType.UNAUTHORIZED_ACCESS: Severity.HIGH,
    ViolationType.RATE_LIMIT_ABUSE: Severity.MEDIUM,
    ...
}
```

#### 근거 2: Domain SLA - 도메인 프리 지원

[config.py#L80-L93](../../../packages/selfhealing-python/src/selfhealing/core/config.py#L80-L93)

```python
thresholds_by_domain: dict[str, int] = field(default_factory=dict)

def get_all_thresholds(self) -> dict[str, timedelta]:
    result = {domain: timedelta(hours=hours) for domain, hours in self.thresholds_by_domain.items()}
    ...
```

#### 근거 3: Learning 패턴 - 반복 에러 감지

[learning/models.py#L52-L56](../../../packages/selfhealing-python/src/selfhealing/services/learning/models.py#L52-L56)

```python
@dataclass
class LearningPattern:
    pattern_type: PatternType
    occurrence_count: int = 1  # 발생 횟수 추적
    confidence: float  # 신뢰도
```

[learning/service.py#L384-L388](../../../packages/selfhealing-python/src/selfhealing/services/learning/service.py#L384-L388)

```python
if pattern.occurrence_count >= 3:
    if pattern.pattern_type == PatternType.FAILURE:
        self._generate_failure_suggestion(pattern)
```

---

## 4. 구현 계획

### 4.0 핵심 설계 원칙 (Architect Review 반영)

#### 4.0.1 Multiplier Cap - 가중치 폭발 방지

**문제**: Critical(10x) × Payment(24x) × 반복(2x) = **480배** → 에러 1개가 Budget 과도 소진

**해결**: 최종 가중치에 상한선 적용

```python
# 가중치 폭발 방지
MAX_WEIGHT_MULTIPLIER = 50.0

def calculate_weighted_budget(...):
    final_multiplier = severity_mult * domain_mult * pattern_mult
    final_multiplier = min(final_multiplier, MAX_WEIGHT_MULTIPLIER)  # Cap 적용
    ...
```

**근거**: 기존 `ReconciliationConfig.max_adjustment_percent_per_cycle`은 **적용 단계** Cap이며, 이것은 **계산 단계** Cap

[models.py#L182-L183](../../../packages/selfhealing-python/src/selfhealing/services/error_budget/reconciliation/models.py#L182-L183)

#### 4.0.2 Source Reliability Weight - 데이터 소스 신뢰도

**배경**: 데이터 소스별 정확도 차이 반영

```python
# 데이터 소스 신뢰도 가중치 (낮을수록 보수적 차감)
SOURCE_RELIABILITY = {
    "prometheus": 1.0,        # 가장 정확
    "dlq": 0.9,               # 리플레이 대기 데이터
    "application_logs": 0.8,  # 누락 가능성 존재
    "none_available": 0.5,    # 추정치 (매우 보수적)
}

def calculate_weighted_budget(..., log_source: str) -> float:
    source_reliability = SOURCE_RELIABILITY.get(log_source, 1.0)
    ...
    return estimated_errors * final_weight * source_reliability
```

**근거**: 이미 `_estimate_errors()`에서 소스별 분리됨

[shadow_calculator.py#L113-L153](../../../packages/selfhealing-python/src/selfhealing/services/error_budget/reconciliation/shadow_calculator.py#L113-L153)

#### 4.0.3 Budget 소진 공식 명시

```python
# Error Budget 소진량 계산 공식
consumed_minutes = total_minutes × (error_rate / allowed_error_rate)

# 여기서:
# - total_minutes: SLO 윈도우 내 허용 장애 시간 (예: 99.9% SLO, 30일 → 43.2분)
# - error_rate: 실제 에러율 = error_count / total_requests
# - allowed_error_rate: 허용 에러율 = 1 - SLO_target (예: 0.001)
```

**근거**: [calculator.py#L101-L117](../../../packages/selfhealing-python/src/selfhealing/services/error_budget/calculator.py#L101-L117)

### 4.1 Phase 1: Severity 기반 가중치 (1시간)

**수정 대상**: `shadow_calculator.py`

```python
# 현재
error_weight_minutes = 0.001
additional_consumed = estimated_errors * error_weight_minutes

# 개선
SEVERITY_WEIGHT = {
    "critical": 0.01,   # 10배 가중치
    "high": 0.005,      # 5배 가중치
    "medium": 0.001,    # 기본값
    "low": 0.0005,      # 절반 가중치
}

def _calculate_weighted_errors(
    self,
    errors_by_severity: Dict[str, int],
) -> float:
    """Severity 기반 가중치 계산."""
    total = 0.0
    for severity, count in errors_by_severity.items():
        weight = SEVERITY_WEIGHT.get(severity.lower(), 0.001)
        total += count * weight
    return total
```

### 4.2 Phase 2: Domain SLA 기반 가중치 (1시간)

**근거**: SLA가 짧을수록 해당 도메인이 더 중요함

```python
def _get_domain_weight(self, domain: str) -> float:
    """Domain SLA 기반 가중치 계산."""
    from selfhealing.core.config import get_config
    
    sla_config = get_config().sla
    domain_hours = sla_config.thresholds_by_domain.get(domain.lower(), 24)
    
    # SLA가 짧을수록 더 중요 → 역수 가중치
    # payment(1h) → 24배, notification(24h) → 1배
    return 24.0 / domain_hours
```

### 4.3 Phase 3: Learning 패턴 연계 (2시간)

**근거**: 반복 발생 에러는 더 높은 가중치 부여

```python
def _get_pattern_weight(
    self,
    domain: str,
    failure_type: str,
) -> float:
    """Learning 패턴 기반 가중치 계산."""
    from selfhealing.services.learning import get_learning_service
    
    try:
        service = get_learning_service()
        pattern_name = f"{domain}:{failure_type}"
        
        # 패턴 조회
        patterns = service.get_patterns(name=pattern_name)
        if not patterns:
            return 1.0  # 기본값
        
        pattern = patterns[0]
        
        # occurrence_count 기반 가중치
        # 3회 이상 발생 시 추가 가중치
        if pattern.occurrence_count >= 10:
            return 2.0  # 10회 이상 → 2배
        elif pattern.occurrence_count >= 5:
            return 1.5  # 5회 이상 → 1.5배
        elif pattern.occurrence_count >= 3:
            return 1.2  # 3회 이상 → 1.2배
        
        return 1.0
    except Exception:
        return 1.0  # 기본값 (Learning 서비스 장애 시)
```

### 4.4 Phase 4: 통합 가중치 계산 (1시간)

```python
def calculate_weighted_budget(
    self,
    estimated_errors: int,
    domain: str = "unknown",
    failure_type: str = "unknown",
    severity: str = "medium",
) -> float:
    """
    가중치 기반 Budget 소진량 계산.
    
    모든 가중치 방법을 조합하여 최종 소진량 계산.
    """
    base_weight = 0.001  # 기본 가중치
    
    # 1. Severity 가중치
    severity_multiplier = SEVERITY_WEIGHT.get(severity.lower(), 1.0)
    
    # 2. Domain SLA 가중치
    domain_multiplier = self._get_domain_weight(domain)
    
    # 3. Learning 패턴 가중치
    pattern_multiplier = self._get_pattern_weight(domain, failure_type)
    
    # 최종 계산
    final_weight = base_weight * severity_multiplier * domain_multiplier * pattern_multiplier
    
    logger.info(
        f"[ShadowBudget] Weighted calculation: "
        f"domain={domain}, severity={severity}, "
        f"base={base_weight}, severity_mult={severity_multiplier}, "
        f"domain_mult={domain_multiplier:.2f}, pattern_mult={pattern_multiplier}, "
        f"final={final_weight:.6f}"
    )
    
    return estimated_errors * final_weight
```

### 4.5 Phase 5: Simulation Stats Callback - Chaos 연동 (1시간)

**문제**: `ErrorBudgetService._simulated_errors`가 `Calculator`에 반영되지 않음

**근거**: [service.py#L53-L56](../../../packages/selfhealing-python/src/selfhealing/services/error_budget/service.py#L53-L56)

```python
# 현재: _simulated_errors가 Calculator와 분리됨
self._simulated_errors: int = 0  # Service에만 존재
```

**해결**: 시뮬레이션 콜백 주입 (클래스 아닌 함수 클로저)

> **네이밍 결정**: `SimulationBridge` 클래스 대신 `_simulation_stats_callback` 함수 사용
> - 기존 `_get_xxx` 패턴과 일관성 유지
> - 별도 클래스 불필요, 클로저로 충분

```python
# service.py 수정
def __init__(...):
    ...
    # Simulation Stats Callback: Calculator에 시뮬레이션 에러 주입
    def _simulation_stats_callback(start_time, end_time) -> Dict:
        """Chaos Engineering 테스트용 콜백."""
        return {
            "total_errors": self._simulated_errors,
            "source": "simulation",
        }
    
    self.calculator = ErrorBudgetCalculator(
        slo_config=slo_config,
        get_failed_operation_stats=(
            get_failed_operation_stats or _simulation_stats_callback
        ),
        get_request_stats=get_request_stats,
    )
```

**가치**: Locust Stage 16/20 부하 테스트 시 Budget 소진 실시간 반영

### 4.6 Phase 6: Pending Reconciliation Freeze (1시간)

**요구사항**: 대규모 조정(>5%) 승인 대기 중 배포/자동 튜닝 동결

**근거**: 기존 `FreezeModeManager` 인프라 활용

[freeze_mode.py#L56-L96](../../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/freeze_mode.py#L56-L96)

```python
# shadow_calculator.py에 추가
def _notify_pending_freeze(self, shadow: ShadowBudget) -> None:
    """대규모 조정 시 배포 동결 신호."""
    if shadow.adjustment_percent <= 5.0:
        return  # 소규모 조정은 무시
    
    try:
        from selfhealing.services.circuit_breaker.freeze_mode import (
            FreezeModeManager,
            FreezeReason,
        )
        
        manager = FreezeModeManager()
        manager.activate(
            reason=(
                f"Pending Reconciliation: {shadow.adjustment_percent:.2f}% adjustment. "
                f"Awaiting approval for calculation_id={shadow.calculation_id}"
            ),
            activated_by="shadow_budget_calculator",
        )
        
        logger.warning(
            f"[ShadowBudget] Freeze Mode ACTIVATED for large adjustment: "
            f"{shadow.adjustment_percent:.2f}%"
        )
        
    except Exception as e:
        logger.warning(f"[ShadowBudget] Failed to activate freeze: {e}")
```

**연동 위치**:
- `calculate_shadow_budget()` 완료 후 호출
- `approve_shadow_budget()` 또는 `reject_shadow_budget()` 시 해제

---

## 5. Audit 이벤트 추가

### 5.1 추가할 AuditEventType

**수정 대상**: [event_buffer.py#L46](../../../packages/selfhealing-python/src/selfhealing/audit/event_buffer.py#L46)

```python
class AuditEventType(Enum):
    # ... 기존 이벤트들 ...
    
    # ═══════════════════════════════════════════════════════════
    # Reconciliation 관련 (30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md)
    # ═══════════════════════════════════════════════════════════
    FAILSAFE_PERIOD_STARTED = "failsafe_period_started"
    """Fail-Safe 기간 시작."""
    
    FAILSAFE_PERIOD_ENDED = "failsafe_period_ended"
    """Fail-Safe 기간 종료."""
    
    SHADOW_BUDGET_CALCULATED = "shadow_budget_calculated"
    """Shadow Budget 계산 완료."""
    
    RECONCILIATION_APPROVED = "reconciliation_approved"
    """Reconciliation 승인됨."""
    
    RECONCILIATION_REJECTED = "reconciliation_rejected"
    """Reconciliation 거부됨."""
```

### 5.2 Audit 기록 위치

| 이벤트 | 기록 위치 | 근거 |
|--------|----------|------|
| `FAILSAFE_PERIOD_STARTED` | `period_tracker.py:start_period()` | L80 |
| `FAILSAFE_PERIOD_ENDED` | `period_tracker.py:end_period()` | L95 |
| `SHADOW_BUDGET_CALCULATED` | `shadow_calculator.py:calculate_shadow_budget()` | L104 |
| `RECONCILIATION_APPROVED` | `service.py:approve_shadow_budget()` | L243 |
| `RECONCILIATION_REJECTED` | `service.py:reject_shadow_budget()` | L341 |

### 5.2.1 RECONCILIATION_REJECTED 투명성 강화

**요구사항**: 거부 시 에러 은폐 의혹 방지를 위한 필수 정보 기록

**현재 상태**: [service.py#L340-L350](../../../packages/selfhealing-python/src/selfhealing/services/error_budget/reconciliation/service.py#L340-L350)

```python
# 현재: original_error_count 없음
exclusion = ExcludedPeriod(
    reason=reason,
    excluded_by=rejected_by,
    # ⚠️ original_error_count 미저장
)
```

**개선**: 거부 시 원본 에러 수 필수 기록

```python
def reject_shadow_budget(...):
    ...
    exclusion = ExcludedPeriod(
        ...
        reason=reason,
        excluded_by=rejected_by,
        # 투명성 강화: 전용 필드 사용 (notes 대신)
        original_estimated_errors=shadow.estimated_errors,
        original_log_source=shadow.log_source,
        original_adjustment_percent=shadow.adjustment_percent,
    )
    
    # Audit 기록 (필수 정보 포함)
    self._record_audit_event(
        event_type=AuditEventType.RECONCILIATION_REJECTED,
        details={
            "calculation_id": calculation_id,
            "rejected_by": rejected_by,
            "rejection_reason": reason,
            "original_estimated_errors": shadow.estimated_errors,  # 필수
            "original_log_source": shadow.log_source,
            "original_adjustment_percent": shadow.adjustment_percent,
            "failsafe_period_id": shadow.failsafe_period_id,
            "period_start": shadow.failsafe_period_start.isoformat(),
            "period_end": shadow.failsafe_period_end.isoformat(),
        },
    )
```

#### ExcludedPeriod 모델 필드 추가

**수정 대상**: [models.py#L141-L165](../../../packages/selfhealing-python/src/selfhealing/services/error_budget/reconciliation/models.py#L141-L165)

```python
@dataclass
class ExcludedPeriod:
    """Budget 계산에서 제외된 기간."""
    
    exclusion_id: str
    started_at: datetime
    ended_at: datetime
    
    # 제외 사유
    reason: str
    excluded_by: str
    excluded_at: datetime
    
    # 연관 Fail-Safe 기간
    failsafe_period_id: Optional[str] = None
    
    # 투명성 강화: 제외 당시 원본 데이터 (신규)
    original_estimated_errors: Optional[int] = None
    original_log_source: Optional[str] = None
    original_adjustment_percent: Optional[float] = None
    
    # 메모 (deprecated, 전용 필드 사용 권장)
    notes: str = ""
```
```

**가치**: "예산을 깎지는 않았지만 어떤 일이 있었는지는 숨기지 않는다" - 7년 로그 보관과 결합하여 완벽한 거버넌스 증명

### 5.2.2 Accuracy Audit - 추정 정확도 사후 검증

**요구사항**: 승인 시점에 추정값 vs 실제값 오차율 기록

**가치**:
1. **자기 교정 능력 증명**: "시스템이 스스로 알고리즘을 검증한다"
2. **SLA 데이터 정당성**: "예산 차감이 실제 장애와 일치하는가"
3. **LearningService 근거**: 패턴 학습의 Ground Truth 역할

#### 구현 방식: 기존 Celery Beat 활용

**근거**: 별도 스케줄러 불필요 - Intelligence Lane에 태스크 추가

[beat_schedule.py#L122-L126](../../../packages/selfhealing-python/src/selfhealing/adapters/celery/beat_schedule.py#L122-L126)

```python
# intelligence_tasks.py에 추가
class VerifyReconciliationAccuracyTask(BaseNotifyingTask):
    """
    Shadow Budget 추정 정확도 검증.
    
    승인/거부 30분 후 실제 에러 수와 비교.
    
    스케줄: 5분마다 (Beat에 편승)
    큐: analysis
    """
    name = "selfhealing.verify_reconciliation_accuracy"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.BATCHED,
        threshold=0,  # 항상 실행 (로그만, 알림은 선택적)
        cooldown_seconds=0,
    )
    
    def run(self) -> Dict[str, Any]:
        """검증 대기 중인 Shadow Budget들 처리."""
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )
        from selfhealing.core.timezone import now
        from datetime import timedelta
        
        service = get_reconciliation_service()
        verified_count = 0
        
        # 승인/거부 30분 지난 항목 필터링
        cutoff = now() - timedelta(minutes=30)
        
        for shadow in service.get_all_shadow_budgets():
            # 이미 검증된 항목 스킵
            if shadow.verified_at:
                continue
            
            # 승인/거부 후 30분 경과 확인
            if shadow.reviewed_at and shadow.reviewed_at < cutoff:
                self._verify_accuracy(shadow)
                verified_count += 1
        
        return {
            "success": True,
            "verified_count": verified_count,
        }
    
    def _verify_accuracy(self, shadow: "ShadowBudget") -> None:
        """단일 Shadow Budget 정확도 검증."""
        # Prometheus에서 실제 에러 수 조회
        actual_errors = self._get_actual_errors(
            start=shadow.failsafe_period_end,
            end=shadow.failsafe_period_end + timedelta(minutes=30),
        )
        
        # 오차율 계산
        if shadow.estimated_errors > 0:
            variance_percent = abs(
                (shadow.estimated_errors - actual_errors) / shadow.estimated_errors * 100
            )
        else:
            variance_percent = 0.0 if actual_errors == 0 else 100.0
        
        # 모델 업데이트
        shadow.verified_at = now()
        shadow.accuracy_variance_percent = variance_percent
        
        # Audit 기록
        self._record_audit_event(
            event_type=AuditEventType.RECONCILIATION_ACCURACY_VERIFIED,
            details={
                "calculation_id": shadow.calculation_id,
                "estimated_errors": shadow.estimated_errors,
                "actual_errors_30m": actual_errors,
                "variance_percent": round(variance_percent, 2),
                "log_source": shadow.log_source,
            },
        )
```

#### ShadowBudget 모델 필드 추가

**수정 대상**: [models.py#L67-L100](../../../packages/selfhealing-python/src/selfhealing/services/error_budget/reconciliation/models.py#L67-L100)

```python
@dataclass
class ShadowBudget:
    ...
    # 기존 필드들 ...
    
    # 정확도 검증 (신규)
    verified_at: Optional[datetime] = None
    accuracy_variance_percent: Optional[float] = None  # 오차율
```

#### Beat Schedule 추가

```python
# intelligence_tasks.py - get_intelligence_beat_schedule()
"verify-reconciliation-accuracy": {
    "task": "selfhealing.verify_reconciliation_accuracy",
    "schedule": crontab(minute="*/5"),  # 5분마다
    "options": {"queue": "analysis"},
},
```

### 5.3 Audit 기록 예시

```python
# period_tracker.py에 추가
def start_period(self, reason: str, component: str = "", service_name: str = "") -> FailSafePeriod:
    # ... 기존 코드 ...
    
    # Audit 기록
    self._record_audit_event(
        event_type=AuditEventType.FAILSAFE_PERIOD_STARTED,
        details={
            "period_id": period.period_id,
            "reason": reason,
            "component": component,
            "service_name": service_name,
        },
    )
    
    return period
```

---

## 6. 알림 설정

### 6.1 기존 인프라 활용

**근거**: [unified_notification.py#L41-L58](../../../packages/selfhealing-python/src/selfhealing/services/unified_notification.py#L41-L58)

```python
class NotificationCategory(str, Enum):
    SECURITY = "security"
    OPERATIONS = "operations"  # ← Reconciliation에 적합
    SLA = "sla"
    CIRCUIT_BREAKER = "circuit_breaker"
    GOVERNANCE = "governance"
    APPROVAL = "approval"  # ← 승인 대기에 적합
    ...
```

### 6.2 알림 추가 위치

| 상황 | 카테고리 | 우선순위 | 근거 |
|------|----------|----------|------|
| Shadow Budget 계산 완료 | `APPROVAL` | `MEDIUM` | 검토 대기 알림 |
| Reconciliation 승인 | `OPERATIONS` | `LOW` | 정보성 알림 |
| 대규모 조정 (>5%) | `SLA` | `HIGH` | Budget 영향 큼 |
| **Freeze Mode 활성화** | `GOVERNANCE` | `HIGH` | 배포 동결 알림 |
| **정확도 검증 완료** | `OPERATIONS` | `LOW` | 정확도 리포트 |

### 6.3 알림 예시 코드

```python
# shadow_calculator.py에 추가
def _notify_shadow_budget_calculated(self, shadow: ShadowBudget) -> None:
    """Shadow Budget 계산 완료 알림."""
    try:
        from selfhealing.services.unified_notification import (
            get_unified_notification_manager,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        
        # 대규모 조정 시 우선순위 상향
        priority = NotificationPriority.HIGH if shadow.adjustment_percent > 5.0 else NotificationPriority.MEDIUM
        
        payload = NotificationPayload(
            title="Shadow Budget 승인 대기",
            message=(
                f"Fail-Safe 기간 동안 {shadow.estimated_errors}개 에러 추정. "
                f"예상 조정: {shadow.adjustment_percent:.2f}%. 검토가 필요합니다."
            ),
            priority=priority,
            category=NotificationCategory.APPROVAL,
            source="shadow_budget_calculator",
            metadata={
                "calculation_id": shadow.calculation_id,
                "estimated_errors": shadow.estimated_errors,
                "adjustment_percent": shadow.adjustment_percent,
                "log_source": shadow.log_source,
            },
        )
        
        manager = get_unified_notification_manager()
        manager.send(payload)
        
    except Exception as e:
        logger.warning(f"[ShadowBudget] Notification failed (non-critical): {e}")
```

---

## 7. 로그 설정

### 7.1 현재 로그 패턴

**근거**: 기존 코드 분석 결과

| 접두사 | 용도 | 예시 |
|--------|------|------|
| `[ShadowBudget]` | Shadow 계산 관련 | `shadow_calculator.py#L104` |
| `[Reconciliation]` | 승인/거부/적용 | `service.py#L243` |
| `[FailSafeTracker]` | 기간 추적 | `period_tracker.py#L80` |

### 7.2 추가 권장 로그

```python
# 가중치 계산 시 상세 로그
logger.info(
    f"[ShadowBudget] Weighted calculation: "
    f"domain={domain}, severity={severity}, "
    f"base_weight={base_weight}, "
    f"severity_mult={severity_multiplier}, "
    f"domain_mult={domain_multiplier:.2f}, "
    f"pattern_mult={pattern_multiplier}, "
    f"final_weight={final_weight:.6f}"
)

# Learning 패턴 연계 시
logger.debug(
    f"[ShadowBudget] Pattern weight applied: "
    f"pattern={pattern_name}, occurrence_count={pattern.occurrence_count}, "
    f"multiplier={pattern_multiplier}"
)
```

### 7.3 로그 레벨 가이드

| 레벨 | 용도 |
|------|------|
| `INFO` | 주요 작업 완료 (계산, 승인, 거부) |
| `WARNING` | 외부 시스템 장애 (Prometheus, DLQ 조회 실패) |
| `DEBUG` | 상세 가중치 계산 과정 |
| `ERROR` | 핵심 기능 실패 |

---

## 8. 테스트 계획

### 8.1 단위 테스트

**파일**: `tests/self_healing/unit/test_shadow_budget_weighted.py`

```python
class TestSeverityWeighting:
    """Severity 기반 가중치 테스트."""
    
    def test_critical_severity_10x_weight(self):
        """CRITICAL은 기본값 대비 10배 가중치."""
        calculator = ShadowBudgetCalculator()
        result = calculator._calculate_weighted_errors({"critical": 10})
        assert result == 10 * 0.01  # 0.1분
    
    def test_medium_severity_base_weight(self):
        """MEDIUM은 기본 가중치."""
        calculator = ShadowBudgetCalculator()
        result = calculator._calculate_weighted_errors({"medium": 10})
        assert result == 10 * 0.001  # 0.01분


class TestDomainWeighting:
    """Domain SLA 기반 가중치 테스트."""
    
    def test_payment_domain_highest_weight(self):
        """Payment(1h SLA)는 가장 높은 가중치."""
        calculator = ShadowBudgetCalculator()
        weight = calculator._get_domain_weight("payment")
        assert weight == 24.0  # 24/1 = 24배
    
    def test_notification_domain_lowest_weight(self):
        """Notification(24h SLA)는 가장 낮은 가중치."""
        calculator = ShadowBudgetCalculator()
        weight = calculator._get_domain_weight("notification")
        assert weight == 1.0  # 24/24 = 1배


class TestLearningPatternWeighting:
    """Learning 패턴 기반 가중치 테스트."""
    
    def test_high_occurrence_pattern_2x_weight(self):
        """10회 이상 발생 패턴은 2배 가중치."""
        # ... 테스트 구현 ...
    
    def test_learning_service_failure_fallback(self):
        """Learning 서비스 장애 시 기본값 1.0 반환."""
        # ... 테스트 구현 ...
```

### 8.2 통합 테스트

**파일**: `tests/self_healing/integration/test_reconciliation_weighted.py`

```python
class TestWeightedReconciliationFlow:
    """가중치 적용 Reconciliation 플로우 테스트."""
    
    def test_end_to_end_weighted_calculation(self):
        """전체 플로우: Fail-Safe → 계산 → 승인."""
        # 1. Fail-Safe 기간 시작
        # 2. 에러 발생 (다양한 심각도/도메인)
        # 3. Fail-Safe 종료
        # 4. Shadow Budget 계산 (가중치 적용 확인)
        # 5. 승인
        # 6. Audit 이벤트 확인
```

---

## 9. 구현 우선순위

### 9.1 단계별 계획

| Phase | 작업 | 예상 시간 | 우선순위 | 상태 |
|-------|------|----------|----------|------|
| **Phase 0** | 핵심 설계 (Cap, Source Reliability, 공식) | 0.5시간 | 🔴 High | ✅ 완료 |
| **Phase 1** | Severity 기반 가중치 | 1시간 | 🔴 High | ✅ 완료 |
| **Phase 2** | Domain SLA 기반 가중치 | 1시간 | 🔴 High | ✅ 완료 |
| **Phase 3** | Learning 패턴 연계 | 2시간 | 🟡 Medium | ✅ 완료 |
| **Phase 4** | 통합 가중치 계산 | 1시간 | 🔴 High | ✅ 완료 |
| **Phase 5** | SimulationBridge (Chaos Callback) | 1시간 | 🟡 Medium | ✅ 완료 |
| **Phase 6** | Pending Reconciliation Freeze | 1시간 | 🟡 Medium | ✅ 완료 |
| **Phase 7** | Audit 이벤트 추가 (투명성 강화 포함) | 1.5시간 | 🔴 High | ✅ 완료 |
| **Phase 8** | Accuracy Audit (사후 검증) | 1시간 | 🟢 Low | ✅ 완료 |
| **Phase 9** | 알림 연동 | 1시간 | 🟢 Low | ✅ 완료 |
| **Phase 10** | 테스트 작성 | 2시간 | 🔴 High | ✅ 완료 |

**총 예상 시간**: 13시간  
**진행률**: Phase 0~10 모두 완료 (13시간 / 13시간) ✅

### 9.2 의존성

```
Phase 0 (설계) ─────▶ 모든 Phase에 선행

Phase 1 (Severity) ─┐
                    ├──▶ Phase 4 (통합) ──▶ Phase 10 (테스트)
Phase 2 (Domain) ──┘                    
                    
Phase 3 (Learning) ─────────────────────▶ Phase 4 (통합)

Phase 5 (SimulationBridge) ───▶ 독립 구현 가능 (Chaos Engineering)
Phase 6 (Pending Freeze) ─────▶ Phase 4 이후 (대규모 조정 감지 필요)

Phase 7 (Audit 투명성) ───▶ 독립 구현 가능
Phase 8 (Accuracy Audit) ─▶ Phase 7 이후 (Audit 인프라 필요)
Phase 9 (알림) ───────────▶ 독립 구현 가능
```

---

## 10. 변경 이력

| 버전 | 날짜 | 작성자 | 변경 내용 |
|------|------|--------|----------|
| 1.0.0 | 2026-01-08 | AI Assistant | 최초 작성 - Shadow Budget 가중치 기반 계산 개선 계획 |
| 1.1.0 | 2026-01-08 | AI Assistant | Architect Review 반영: Multiplier Cap, Pending Freeze, Accuracy Audit |
| 1.2.0 | 2026-01-08 | AI Assistant | 추가 Review 반영: Source Reliability Weight, SimulationBridge, ExcludedPeriod 투명성 |
| 1.3.0 | 2026-01-08 | AI Assistant | 최종 Review 반영: Accuracy Audit Celery Beat 통합, ExcludedPeriod 전용 필드, 네이밍 보완 |
| 1.4.0 | 2026-01-08 | AI Assistant | Phase 0, 1 구현 완료: MAX_WEIGHT_MULTIPLIER, SOURCE_RELIABILITY, SEVERITY_WEIGHT 상수 및 `_calculate_weighted_errors()` 메서드 추가. 30개 단위 테스트 통과 |
| 1.5.0 | 2026-01-08 | AI Assistant | Phase 2, 3, 4, 10 구현 완료: `_get_domain_weight()`, `_get_pattern_weight()` 메서드 추가. 통합 가중치 계산 및 Multiplier Cap 적용. 47개 단위 테스트 통과 |
| 1.6.0 | 2026-01-08 | AI Assistant | Phase 5~9 구현 완료: SimulationBridge, Pending Freeze, Audit 이벤트, Accuracy Audit, 알림 연동. 57개 단위 테스트 통과 |
