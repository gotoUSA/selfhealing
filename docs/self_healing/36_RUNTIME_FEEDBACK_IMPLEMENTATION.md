# Runtime Feedback Loop 구현 계획

📅 **작성일**: 2025-12-29  
🎯 **목적**: 실시간 메트릭 기반 자율 튜닝 시스템 구현  
📋 **버전**: v1.0.0

---

## 📌 개요

### 배경

현재 Self-Healing 시스템은 **정적 설정**에 의존합니다:
- `retry_count = 3` → 고정값
- `timeout_ms = 5000` → 고정값
- `circuit_breaker_threshold = 0.5` → 고정값

리뷰어 피드백:
> "Netflix나 Google 수준의 인프라를 운영하는 회사들은 사람이 설정값을 일일이 고치는 것을 신뢰하지 않습니다."

### 목표

**정적 도구 → 자율 주행 시스템으로 진화**

```
현재: 사람이 설정 → 시스템 실행 → 문제 발생 → 사람이 수정
목표: 시스템 실행 → 메트릭 수집 → 자동 분석 → 자동 조정 → 감사 로그
```

---

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                   Runtime Feedback Loop                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌─────────────────┐                                           │
│   │ Metrics Source  │ Prometheus / Datadog / Internal          │
│   └────────┬────────┘                                           │
│            │                                                     │
│            ▼                                                     │
│   ┌─────────────────┐                                           │
│   │ Metrics Ingester│ 실시간 메트릭 수집 (1분 간격)             │
│   └────────┬────────┘                                           │
│            │                                                     │
│            ▼                                                     │
│   ┌─────────────────┐                                           │
│   │ Decision Engine │ 조정 필요 여부 판단                       │
│   │ (dna_learning)  │                                           │
│   └────────┬────────┘                                           │
│            │                                                     │
│            ▼                                                     │
│   ┌─────────────────┐    ┌─────────────────┐                   │
│   │ Safety Guard    │───▶│ Audit Trail     │                   │
│   │ (bounds check)  │    │ (로그 + 알림)   │                   │
│   └────────┬────────┘    └─────────────────┘                   │
│            │                                                     │
│            ▼                                                     │
│   ┌─────────────────┐                                           │
│   │ Config Applier  │ 실제 설정값 변경                          │
│   └─────────────────┘                                           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📁 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── core/
│   ├── runtime_feedback.py      # 🆕 메인 피드백 루프
│   ├── decision_engine.py       # 🆕 조정 결정 엔진
│   └── safety_bounds.py         # 🆕 안전 한계 검증
│
├── services/
│   └── auto_tuning/             # 🆕 자율 튜닝 서비스
│       ├── __init__.py
│       ├── service.py           # 자율 튜닝 비즈니스 로직
│       ├── models.py            # 조정 이력, 설정 모델
│       └── adjustment_recorder.py  # 조정 기록기
│
├── api/django/views/
│   └── auto_tuning.py           # 🆕 자율 튜닝 API
│
└── adapters/
    └── metrics/                  # 🆕 메트릭 수집 어댑터
        ├── __init__.py
        ├── prometheus_adapter.py
        ├── datadog_adapter.py
        └── internal_adapter.py   # 내부 메트릭 (DB 기반)
```

---

## 🔧 핵심 구현

### 1. RuntimeFeedbackLoop

```python
# packages/selfhealing-python/src/selfhealing/core/runtime_feedback.py

"""
Runtime Feedback Loop - 실시간 메트릭 기반 자동 튜닝

Netflix Hystrix, Google Autopilot 스타일의 자율 조정 시스템
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import logging
import threading

from selfhealing.interfaces.audit_adapter import AuditEntry, AuditAction
from selfhealing.services.auto_tuning.adjustment_recorder import AdjustmentRecorder

logger = logging.getLogger(__name__)


class RuntimeFeedbackLoop:
    """
    실시간 피드백 루프
    
    1. 메트릭 수집 (Prometheus/Datadog/Internal)
    2. 조정 필요 여부 판단 (Decision Engine)
    3. 안전 한계 검증 (Safety Bounds)
    4. 설정 적용 + 감사 로그 + 알림
    """
    
    def __init__(
        self,
        metrics_adapter,           # 메트릭 소스
        decision_engine,           # 결정 엔진
        safety_bounds,             # 안전 한계
        audit_adapter,             # 감사 로그
        alert_manager,             # 알림 매니저
        config_applier,            # 설정 적용기
        enabled: bool = True,
        interval_seconds: int = 60,
    ):
        self.metrics_adapter = metrics_adapter
        self.decision_engine = decision_engine
        self.safety_bounds = safety_bounds
        self.audit_adapter = audit_adapter
        self.alert_manager = alert_manager
        self.config_applier = config_applier
        
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self.adjustment_recorder = AdjustmentRecorder()
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def start(self):
        """피드백 루프 시작"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("[RuntimeFeedback] Started")
    
    def stop(self):
        """피드백 루프 중지"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[RuntimeFeedback] Stopped")
    
    def _run_loop(self):
        """메인 루프"""
        import time
        while self._running:
            try:
                if self.enabled:
                    self.observe_and_adjust()
            except Exception as e:
                logger.error(f"[RuntimeFeedback] Error: {e}")
            time.sleep(self.interval_seconds)
    
    def observe_and_adjust(self) -> Dict[str, Any]:
        """
        관찰 및 조정 수행
        
        Returns:
            조정 결과 딕셔너리
        """
        # 1. 메트릭 수집
        metrics = self.metrics_adapter.fetch_current_metrics()
        
        # 2. 조정 결정
        decisions = self.decision_engine.analyze(metrics)
        
        if not decisions:
            return {"adjusted": False, "reason": "no_adjustment_needed"}
        
        adjustments_made = []
        
        for decision in decisions:
            # 3. 안전 한계 검증
            if not self.safety_bounds.is_within_bounds(
                decision.parameter,
                decision.suggested_value
            ):
                logger.warning(
                    f"[RuntimeFeedback] Adjustment rejected by safety bounds: "
                    f"{decision.parameter}={decision.suggested_value}"
                )
                continue
            
            # 4. 설정 적용
            old_value = self.config_applier.get_current(decision.parameter)
            self.config_applier.apply(decision.parameter, decision.suggested_value)
            
            adjustment = {
                "parameter": decision.parameter,
                "old_value": old_value,
                "new_value": decision.suggested_value,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "timestamp": datetime.now().isoformat(),
            }
            adjustments_made.append(adjustment)
            
            # 5. 감사 로그 기록
            self._record_audit(adjustment)
            
            # 6. 알림 발송
            self._send_alert(adjustment)
        
        # 7. 조정 이력 저장
        for adj in adjustments_made:
            self.adjustment_recorder.record(adj)
        
        return {
            "adjusted": len(adjustments_made) > 0,
            "adjustments": adjustments_made,
        }
    
    def _record_audit(self, adjustment: Dict[str, Any]):
        """감사 로그 기록"""
        entry = AuditEntry(
            action=AuditAction.CONFIG_CHANGE,
            resource_type="auto_tuning",
            resource_id=adjustment["parameter"],
            details={
                "type": "automatic_adjustment",
                "old_value": adjustment["old_value"],
                "new_value": adjustment["new_value"],
                "reason": adjustment["reason"],
                "confidence": adjustment["confidence"],
            },
            actor_type="system",
            actor_id="runtime_feedback_loop",
        )
        self.audit_adapter.log(entry)
    
    def _send_alert(self, adjustment: Dict[str, Any]):
        """알림 발송"""
        self.alert_manager.send_auto_tuning_alert(
            parameter=adjustment["parameter"],
            old_value=adjustment["old_value"],
            new_value=adjustment["new_value"],
            reason=adjustment["reason"],
        )
```

### 2. Decision Engine

```python
# packages/selfhealing-python/src/selfhealing/core/decision_engine.py

"""
Decision Engine - 메트릭 기반 조정 결정

실시간 메트릭을 분석하여 파라미터 조정 제안
"""

from dataclasses import dataclass
from typing import List, Optional
import statistics


@dataclass
class AdjustmentDecision:
    """조정 결정"""
    parameter: str
    current_value: float
    suggested_value: float
    reason: str
    confidence: float  # 0.0 ~ 1.0
    priority: str  # high, medium, low


class DecisionEngine:
    """
    조정 결정 엔진
    
    메트릭 패턴을 분석하여 파라미터 조정 제안
    """
    
    # 조정 규칙
    RULES = {
        "timeout_ms": {
            "metric": "p99_latency_ms",
            "condition": lambda current, metric: metric > current * 0.8,
            "adjustment": lambda current, metric: min(current * 1.2, 10000),
            "reason": "P99 레이턴시가 타임아웃의 80% 이상 → 타임아웃 상향",
        },
        "retry_count": {
            "metric": "retry_exhausted_rate",
            "condition": lambda current, metric: metric > 0.1,  # 10% 이상 재시도 소진
            "adjustment": lambda current, metric: min(current + 1, 5),
            "reason": "재시도 소진율 10% 이상 → 재시도 횟수 증가",
        },
        "circuit_breaker_threshold": {
            "metric": "error_rate",
            "condition": lambda current, metric: metric > current * 0.9,
            "adjustment": lambda current, metric: min(current * 1.1, 0.8),
            "reason": "에러율이 CB 임계값에 근접 → 임계값 상향 조정",
        },
        "jitter_range": {
            "metric": "retry_collision_rate",
            "condition": lambda current, metric: metric > 0.05,  # 5% 이상 충돌
            "adjustment": lambda current, metric: current * 1.5,
            "reason": "재시도 충돌율 높음 → 지터 범위 확대",
        },
    }
    
    def __init__(self, config_provider):
        self.config_provider = config_provider
        self.history: List[dict] = []
    
    def analyze(self, metrics: dict) -> List[AdjustmentDecision]:
        """
        메트릭 분석 및 조정 결정
        
        Args:
            metrics: 수집된 메트릭
            
        Returns:
            조정 결정 목록
        """
        decisions = []
        
        for param, rule in self.RULES.items():
            metric_name = rule["metric"]
            metric_value = metrics.get(metric_name)
            
            if metric_value is None:
                continue
            
            current_value = self.config_provider.get(param)
            
            if rule["condition"](current_value, metric_value):
                suggested = rule["adjustment"](current_value, metric_value)
                
                # 변경이 의미있는 경우만 제안
                if abs(suggested - current_value) / current_value > 0.05:
                    decisions.append(AdjustmentDecision(
                        parameter=param,
                        current_value=current_value,
                        suggested_value=suggested,
                        reason=rule["reason"],
                        confidence=self._calculate_confidence(metrics, param),
                        priority="medium",
                    ))
        
        return decisions
    
    def _calculate_confidence(self, metrics: dict, param: str) -> float:
        """신뢰도 계산 (샘플 수, 변동성 기반)"""
        # 간단한 구현 - 실제로는 더 정교한 통계 분석 필요
        sample_count = metrics.get("sample_count", 10)
        if sample_count < 5:
            return 0.3
        elif sample_count < 20:
            return 0.6
        else:
            return 0.85
```

### 3. Safety Bounds

```python
# packages/selfhealing-python/src/selfhealing/core/safety_bounds.py

"""
Safety Bounds - 자율 조정 안전 한계

자율 조정이 위험한 범위로 벗어나지 않도록 보호
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ParameterBound:
    """파라미터 한계"""
    min_value: float
    max_value: float
    max_change_per_cycle: float  # 한 번에 변경 가능한 최대 비율


class SafetyBounds:
    """
    안전 한계 관리
    
    자율 조정의 범위를 제한하여 시스템 안정성 보호
    """
    
    # 기본 한계 설정
    DEFAULT_BOUNDS: Dict[str, ParameterBound] = {
        "timeout_ms": ParameterBound(
            min_value=100,      # 최소 100ms
            max_value=30000,    # 최대 30초
            max_change_per_cycle=0.3,  # 한 번에 30% 이내
        ),
        "retry_count": ParameterBound(
            min_value=0,
            max_value=10,
            max_change_per_cycle=0.5,
        ),
        "circuit_breaker_threshold": ParameterBound(
            min_value=0.1,      # 최소 10%
            max_value=0.9,      # 최대 90%
            max_change_per_cycle=0.2,
        ),
        "jitter_range": ParameterBound(
            min_value=0.01,     # 최소 10ms
            max_value=1.0,      # 최대 1초
            max_change_per_cycle=0.5,
        ),
        "rate_limit_rps": ParameterBound(
            min_value=10,
            max_value=10000,
            max_change_per_cycle=0.2,
        ),
    }
    
    def __init__(self, custom_bounds: Optional[Dict[str, Dict]] = None):
        self.bounds = dict(self.DEFAULT_BOUNDS)
        
        if custom_bounds:
            for param, config in custom_bounds.items():
                self.bounds[param] = ParameterBound(**config)
    
    def is_within_bounds(
        self,
        parameter: str,
        new_value: float,
        current_value: Optional[float] = None
    ) -> bool:
        """
        값이 안전 한계 내인지 확인
        
        Args:
            parameter: 파라미터 이름
            new_value: 새로운 값
            current_value: 현재 값 (변경폭 검증용)
            
        Returns:
            안전 한계 내이면 True
        """
        bound = self.bounds.get(parameter)
        if not bound:
            logger.warning(f"[SafetyBounds] Unknown parameter: {parameter}")
            return False
        
        # 범위 검증
        if new_value < bound.min_value or new_value > bound.max_value:
            logger.warning(
                f"[SafetyBounds] {parameter}={new_value} out of range "
                f"[{bound.min_value}, {bound.max_value}]"
            )
            return False
        
        # 변경폭 검증
        if current_value is not None and current_value > 0:
            change_ratio = abs(new_value - current_value) / current_value
            if change_ratio > bound.max_change_per_cycle:
                logger.warning(
                    f"[SafetyBounds] {parameter} change ratio {change_ratio:.1%} "
                    f"exceeds limit {bound.max_change_per_cycle:.1%}"
                )
                return False
        
        return True
    
    def clamp_to_bounds(self, parameter: str, value: float) -> float:
        """값을 안전 한계 내로 제한"""
        bound = self.bounds.get(parameter)
        if not bound:
            return value
        return max(bound.min_value, min(value, bound.max_value))
    
    def update_bounds(self, parameter: str, config: Dict[str, float]):
        """런타임에 한계 업데이트 (관리자 전용)"""
        self.bounds[parameter] = ParameterBound(**config)
        logger.info(f"[SafetyBounds] Updated bounds for {parameter}: {config}")
```

---

## 📡 알림 통합

### 기존 Alert Manager 확장

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget_gate/alert_manager.py
# (기존 파일에 추가)

def send_auto_tuning_alert(
    self,
    parameter: str,
    old_value: Any,
    new_value: Any,
    reason: str,
) -> bool:
    """
    자율 조정 알림 발송
    
    Returns:
        bool: 알림 발송 여부
    """
    alert_type = f"auto_tuning_{parameter}"
    
    if not self._can_send_alert(alert_type):
        logger.debug(f"[AutoTuning] Alert skipped (cooldown): {parameter}")
        return False
    
    message = (
        f"🔧 [Self-Healing] 자율 조정 발생\n"
        f"• 파라미터: {parameter}\n"
        f"• 변경: {old_value} → {new_value}\n"
        f"• 사유: {reason}\n"
        f"• 시간: {datetime.now().isoformat()}"
    )
    
    try:
        self._send_to_channels(message, severity="info")
        self._record_alert_sent(alert_type)
        logger.info(f"[AutoTuning] Alert sent: {parameter}")
        return True
    except Exception as e:
        logger.error(f"[AutoTuning] Alert failed: {e}")
        return False
```

---

## 📝 감사 로그 통합

### 기존 Audit 시스템 활용

```python
# 자율 조정 시 기록되는 감사 로그 예시

{
    "timestamp": "2025-12-29T10:30:00Z",
    "action": "config_change",
    "resource_type": "auto_tuning",
    "resource_id": "timeout_ms",
    "actor_type": "system",
    "actor_id": "runtime_feedback_loop",
    "details": {
        "type": "automatic_adjustment",
        "old_value": 5000,
        "new_value": 6000,
        "reason": "P99 레이턴시가 타임아웃의 80% 이상 → 타임아웃 상향",
        "confidence": 0.85,
        "metrics_snapshot": {
            "p99_latency_ms": 4200,
            "error_rate": 0.02,
            "sample_count": 150
        }
    },
    "environment": "production"
}
```

---

## 🔗 API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/self-healing/auto-tuning/status/` | 자율 조정 상태 조회 |
| POST | `/api/self-healing/auto-tuning/enable/` | 자율 조정 활성화 |
| POST | `/api/self-healing/auto-tuning/disable/` | 자율 조정 비활성화 |
| GET | `/api/self-healing/auto-tuning/bounds/` | 안전 한계 조회 |
| PUT | `/api/self-healing/auto-tuning/bounds/` | 안전 한계 수정 |
| GET | `/api/self-healing/auto-tuning/history/` | 조정 이력 조회 |
| GET | `/api/self-healing/auto-tuning/metrics/` | 현재 메트릭 조회 |

---

## � DNA Drift 연동 (Live vs Desired)

### 개념

RuntimeFeedbackLoop이 설정을 조정할 때, **DNA에 선언된 값(Desired)**과 **실제 운영 중인 값(Live)**의 차이를 감지합니다:

```
┌─────────────────────────────────────────────────────────────┐
│                  DNA Drift Detection                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  DNA 선언 (Desired)          실제 설정 (Live)               │
│  ─────────────────          ───────────────                 │
│  timeout_ms = 5000    vs    timeout_ms = 8000 ← Drift!      │
│  retry_count = 3      vs    retry_count = 3   ← 일치        │
│  cb_threshold = 0.5   vs    cb_threshold = 0.7 ← Drift!     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 기존 dna_drift.py와의 관계

| 모듈 | 목적 | 시점 |
|------|------|------|
| `dna_drift.py` (테스트용) | 코드 스캔 → DNA 선언 누락 감지 | CI/CD (테스트 시점) |
| `RuntimeFeedbackLoop` | Live vs DNA 선언값 비교 | 런타임 (운영 중) |

### 구현 위치

이 로직은 **RuntimeFeedbackLoop.apply_adjustment()** 내에서 수행됩니다:

```python
def apply_adjustment(self, adjustment: AdjustmentDecision) -> bool:
    # 1. DNA 선언값 로드 (Stage DNA 또는 기본값)
    desired_value = self._get_dna_declared_value(adjustment.parameter)
    
    # 2. 현재 Live 값과 비교
    if desired_value is not None:
        drift = abs(adjustment.new_value - desired_value) / desired_value
        if drift > 0.2:  # 20% 이상 차이
            self.audit_adapter.record(AuditEntry(
                action=AuditAction.DNA_DRIFT_DETECTED,
                details={
                    "parameter": adjustment.parameter,
                    "dna_value": desired_value,
                    "live_value": adjustment.new_value,
                    "drift_ratio": drift,
                }
            ))
    
    # 3. 조정 적용
    return self.config_applier.apply(adjustment)
```

### 별도 파일 불필요한 이유

1. **런타임 Drift 감지**는 RuntimeFeedbackLoop의 자연스러운 기능
2. **테스트 시점 Drift 감지**는 기존 `dna_drift.py`가 담당
3. 두 가지 다른 목적이므로 별도 파일로 분리하면 혼란만 가중

---
## ⚠️ 장애 대비 복구 전략 (Fallback Strategy)

### 문제 인식

자율 조정 시스템이 **잘못된 결정**을 내리거나 **시스템 자체가 장애**나면?

```
문제 시나리오:
1. RuntimeFeedbackLoop이 잘못된 조정을 수행
2. 시스템 성능이 오히려 악화
3. 추가 조정 시도 → 악순환
4. 수동 개입 필요
```

### 3단계 복구 우선순위 (Tiered Recovery)

| 우선순위 | 전략 | 설명 | 사용 시점 |
|:---:|---|---|---|
| 1 | **Last Known Good** | 변경 직전 스냅샷으로 롤백 | 스냅샷이 있을 때 |
| 2 | **DNA Declared** | DNA에 선언된 Desired 값으로 복구 | 스냅샷이 없거나 실패했을 때 |
| 3 | **System Defaults** | 하드코딩된 보수적 기본값 | 모든 방법이 실패했을 때 |

### 왜 이 순서인가?

```
✔️ Last Known Good (직전 상태)
   - 최소한 그때까지는 작동했음 → 가장 신뢰할 수 있음

✔️ DNA Declared (관리자 선언값)
   - 관리자가 원하는 상태 → 비즈니스 의도 반영

✔️ System Defaults (시스템 기본값)
   - 보수적으로 안전한 값 → 최후 수단

❌ Pause Only (조정만 중지)
   - 현재 "문제 있는 상태"가 유지됨 → 일반적으로 비추천
```

### AutoRollbackGuard 구현

```python
# packages/selfhealing-python/src/selfhealing/core/auto_rollback_guard.py

class RecoveryStrategy(Enum):
    """복구 전략"""
    LAST_KNOWN_GOOD = "last_known_good"  # 바꾸기 전 상태로 롤백
    DNA_DECLARED = "dna_declared"        # DNA 선언값으로 복구
    SYSTEM_DEFAULTS = "system_defaults"  # 하드코딩 기본값 (최후 수단)
    PAUSE_ONLY = "pause_only"            # 조정만 중지, 현재값 유지


class AutoRollbackGuard:
    """
    RuntimeFeedbackLoop과 독립적으로 작동하는 안전장치
    
    핵심: 피드백 루프 자체가 장애나도 이 가드는 살아있음
    """
    
    # 시스템 기본값 (정말 최후 수단)
    SYSTEM_DEFAULTS = [
        SafeDefault("timeout_ms", 5000, "시스템 기본 타임아웃"),
        SafeDefault("retry_count", 3, "시스템 기본 재시도"),
        SafeDefault("circuit_breaker_threshold", 0.5, "시스템 기본 CB"),
    ]
    
    def _recover_parameter(self, parameter: str, system_default: float) -> str:
        # 1단계: Last Known Good
        if snapshots := self._config_snapshots.get(parameter):
            return self._apply_last_known_good(parameter, snapshots)
        
        # 2단계: DNA Declared
        if dna_value := self._get_dna_declared_value(parameter):
            return self._apply_dna_value(parameter, dna_value)
        
        # 3단계: System Defaults (최후 수단)
        return self._apply_system_default(parameter, system_default)
```

### 하드코딩 문제 해결

| 문제 | 해결 |
|------|------|
| SYSTEM_DEFAULTS가 모든 서비스에 맞을까? | DNA 선언값을 먼저 시도 |
| DNA도 없으면? | 보수적인 SYSTEM_DEFAULTS 사용 |
| 그마저도 실패하면? | PAUSE_ONLY로 fallback + 알림 |

---
## �📚 관련 문서

- [37_CONTINUOUS_AUDIT_IMPLEMENTATION.md](./37_CONTINUOUS_AUDIT_IMPLEMENTATION.md) - 지속적 감사
- [38_AUTO_TUNING_API.md](./38_AUTO_TUNING_API.md) - 자율 튜닝 API
- [35_DNA_ANALYZER_GUIDE.md](./35_DNA_ANALYZER_GUIDE.md) - DNA 분석기
