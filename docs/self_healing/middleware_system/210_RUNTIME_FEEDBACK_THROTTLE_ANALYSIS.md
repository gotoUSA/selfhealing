# 210. Runtime Feedback ↔ AdaptiveThrottle 분석

> **상태**: ⚠️ 선택적 연동 (정적 설정만)
> **목적**: RuntimeFeedbackLoop → AutoTuningService의 `rate_limit` 모듈이 AdaptiveThrottle과 실제 연결되지 않은 문제를 분석하고, 안전한 연동 범위를 정의한다.
> **근거 문서**: [207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md](207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md)

---

## 1. 현재 코드 구조

### 1-1. AutoTuningService의 rate_limit 모듈

**파일**: `services/auto_tuning/service.py`

```python
# service.py L71
MODULES = ["circuit_breaker", "retry", "jitter", "rate_limit", "timeout"]
```

```python
# service.py L879-884 — 모듈-파라미터 매핑
module_params = {
    "circuit_breaker": ["circuit_breaker_threshold"],
    "retry": ["retry_count"],
    "jitter": ["jitter_range"],
    "rate_limit": ["rate_limit_rps"],      # ← rate_limit 모듈의 유일한 파라미터
    "timeout": ["timeout_ms"],
}
```

```python
# service.py L967-976 — 파라미터-모듈 역매핑
param_module = {
    "circuit_breaker_threshold": "circuit_breaker",
    "retry_count": "retry",
    "jitter_range": "jitter",
    "rate_limit_rps": "rate_limit",        # ← AdaptiveThrottle 연결 없음
    "timeout_ms": "timeout",
}
```

**문제**: `rate_limit_rps` 파라미터가 정의되어 있으나, `ConfigApplier.apply("rate_limit_rps", value)` 호출 시 실제 AdaptiveThrottle의 어떤 속성에도 매핑되지 않음.

### 1-2. ConfigApplier 프로토콜

**파일**: `core/runtime_feedback.py` L64-76

```python
class ConfigApplier(Protocol):
    """설정 적용기 프로토콜"""

    def get_current(self, parameter: str) -> float:
        """현재 값 조회"""
        ...

    def apply(self, parameter: str, value: float) -> bool:
        """설정 적용"""
        ...

    def rollback(self, parameter: str, value: float) -> bool:
        """롤백 적용"""
        ...
```

- `AutoTuningService.__init__`에서 `config_applier`를 외부에서 주입 (service.py L84)
- **현재 ConfigApplier 구현체가 AdaptiveThrottle에 연결되어 있는지**: 코드에서 확인 불가 — `config_applier`는 의존성 주입이므로 실제 구현체에 의존

### 1-3. RuntimeFeedbackLoop 주기

```python
# runtime_feedback.py L121
def __init__(self, ..., interval_seconds: int = 60, ...):
    self.interval_seconds = interval_seconds
```

### 1-4. AdaptiveThrottle의 자체 조정 주기

```python
# adaptive.py L1189-1190
# Only adjust every sample_interval_ms
interval_seconds = self.config.sample_interval_ms / 1000.0
```

`ThrottleConfig`:
```python
# adaptive.py L17-19
initial_limit=100,
sla_warning_ms=200,
sla_critical_ms=500,
```

`sample_interval_ms`는 `ThrottleConfig` 기본값으로 설정 (config.py 참조).

---

## 2. 이중 조정 위험 분석

### 2-1. 동적 파라미터 vs 정적 파라미터

AdaptiveThrottle의 파라미터를 두 범주로 분류:

| 범주 | 파라미터 | 현재 조정 주체 | 자동 조정 가능? |
|------|----------|---------------|----------------|
| **동적** (실시간 자동조정) | `current_limit` | Gradient 알고리즘 (매 `sample_interval_ms`) | ❌ 위험 — Gradient과 충돌 |
| **동적** | `_rtt_suggested_limit` | RTT EMA 기반 자동 | ❌ |
| **동적** | `_limit_before_429` | 429 감지 자동 감소 | ❌ |
| **동적** | `_error_budget_limit` | Error Budget 이벤트 | ❌ |
| **정적** (초기화 시 설정) | `initial_limit` | ThrottleConfig | ✅ 안전 |
| **정적** | `sla_warning_ms` | ThrottleConfig | ✅ 안전 |
| **정적** | `sla_critical_ms` | ThrottleConfig | ✅ 안전 |
| **정적** | `sample_interval_ms` | ThrottleConfig | ⚠️ 조건부 |

### 2-2. 충돌 시나리오

**시나리오: `rate_limit_rps`를 `current_limit`에 매핑할 경우**

```
T=0s   RuntimeFeedback: apply("rate_limit_rps", 150)  → limit=150 설정
T=0.5s AdaptiveThrottle: _maybe_adjust_limit(rtt=300)  → gradient > 0 → limit=135 (150*0.9)
T=1s   RuntimeFeedback: 효과 확인 → limit 변화 감지 → "조정 실패" 판단 → 자동 롤백
T=1s   AdaptiveThrottle: 롤백된 값을 다시 gradient로 조정...
```

- **결과**: RuntimeFeedbackLoop(60초 주기)와 AdaptiveThrottle(500ms 주기)이 서로의 조정을 덮어쓰는 무한 루프
- AutoTuningService의 `auto_rollback_enabled`가 활성화되면 연속 실패로 feedback loop 정지 (runtime_feedback.py L96 `MAX_CONSECUTIVE_FAILURES`)

### 2-3. 안전한 연동: 정적 파라미터만

```
T=0    RuntimeFeedback: apply("sla_warning_ms", 250)    → ThrottleConfig 설정 변경
T=0.5s AdaptiveThrottle: _maybe_adjust_limit(rtt=210)   → sla_warning_ms=250 기준 판단
                                                          → 이전 200ms 기준이면 warning이었으나
                                                          → 250ms 기준이면 정상 범위
→ 서로 독립적으로 동작, 충돌 없음
```

---

## 3. 연동 장단점

### 3-1. 연동 시 장점

| 장점 | 근거 |
|------|------|
| SLA 임계값 자동 최적화 | `sla_warning_ms`, `sla_critical_ms`를 트래픽 패턴에 맞게 조정 가능 |
| `initial_limit` 시즌 조정 | 트래픽 피크 시즌에 기본 limit 조정 가능 |
| 통합 감사 로그 | AutoTuningService의 `AdjustmentRecorder`에 throttle 설정 변경 기록 |
| Governance 체크 일관성 | AutoTuningService가 모든 조정 전 `check_all_governance()` 호출 (service.py L149) |
| 자동 롤백 안전장치 | `AutoRollbackGuard` (service.py L136)가 설정 변경 후 메트릭 악화 시 롤백 |

### 3-2. 연동 시 단점

| 단점 | 근거 |
|------|------|
| 이중 조정 위험 | 2-2절 참조 — 동적 파라미터 매핑 시 Gradient과 충돌 |
| 간접 효과 예측 어려움 | `sla_warning_ms` 변경 → Gradient threshold 변화 → limit 자동 조정 패턴 변화 (2차 효과) |
| 구현 복잡도 | `ConfigApplier` 구현체가 `ThrottleConfig`를 라이브로 교체해야 함 — 현재 구조상 `get_adaptive_throttle()` 싱글턴의 config 교체가 필요 |

### 3-3. 분리 유지 시 장점

| 장점 | 근거 |
|------|------|
| Gradient 자율성 보장 | AdaptiveThrottle의 `_maybe_adjust_limit()` (adaptive.py L1173)가 외부 간섭 없이 동작 |
| 단순함 | `rate_limit_rps`가 실제 연결되지 않은 현재 상태가 오히려 안전 |
| Recovery Dampening 안정성 | `RECOVERY_DAMPENING_MULTIPLIERS = (0.8, 0.9, 1.0)` (adaptive.py L1946) 자체 복구 로직이 외부 조정과 독립 |

### 3-4. 분리 유지 시 단점

| 단점 | 근거 |
|------|------|
| `rate_limit` 모듈 사실상 미작동 | MODULES에 정의되어 있으나 실제 효과 없음 — 사용자 혼란 가능 |
| SLA 값 수동 관리 | `sla_warning_ms=200`, `sla_critical_ms=500` 값이 고정되어 트래픽 패턴 변화에 대응 불가 |
| 감사 로그 분리 | Throttle 설정 변경이 AutoTuningService 이력에 포함되지 않음 |

---

## 4. 추천 — 선택적 연동

### 4-1. 연동할 것

| 파라미터 | AutoTuning 매핑 | 이유 |
|----------|-----------------|------|
| `sla_warning_ms` | `throttle_sla_warning_ms` | SLA 임계값을 트래픽 패턴에 맞게 자동 조정 |
| `sla_critical_ms` | `throttle_sla_critical_ms` | 동일 |
| `initial_limit` | `throttle_initial_limit` | 시즌/시간대별 기본 limit 조정 |

### 4-2. 연동하지 않을 것

| 파라미터 | 이유 |
|----------|------|
| `current_limit` / `rate_limit_rps` | Gradient 알고리즘 충돌 — 2-2절 참조 |
| `sample_interval_ms` | 변경 시 Gradient 계산 정확도에 직접 영향 |
| `_rtt_suggested_limit` | 내부 상태 — 외부 직접 조정 불가 |

### 4-3. ConfigApplier 구현 예시

```python
class ThrottleConfigApplier:
    """AdaptiveThrottle의 정적 설정 전용 ConfigApplier."""

    # 조정 허용 파라미터 화이트리스트
    ALLOWED_PARAMS = {
        "throttle_sla_warning_ms",
        "throttle_sla_critical_ms",
        "throttle_initial_limit",
    }

    def get_current(self, parameter: str) -> float:
        if parameter not in self.ALLOWED_PARAMS:
            raise ValueError(f"Parameter {parameter} not allowed for throttle tuning")
        throttle = get_adaptive_throttle()
        mapping = {
            "throttle_sla_warning_ms": throttle.config.sla_warning_ms,
            "throttle_sla_critical_ms": throttle.config.sla_critical_ms,
            "throttle_initial_limit": throttle.config.initial_limit,
        }
        return mapping[parameter]

    def apply(self, parameter: str, value: float) -> bool:
        if parameter not in self.ALLOWED_PARAMS:
            return False
        throttle = get_adaptive_throttle()
        # Config hot-reload 패턴 필요
        config_attr = parameter.replace("throttle_", "")
        setattr(throttle.config, config_attr, int(value))
        return True

    def rollback(self, parameter: str, value: float) -> bool:
        return self.apply(parameter, value)
```

### 4-4. MODULES 정리 필요

```python
# 현재 (service.py L71)
MODULES = ["circuit_breaker", "retry", "jitter", "rate_limit", "timeout"]

# 제안 A: rate_limit → 정적 설정 전용으로 명시
# module_params 매핑 변경 (service.py L879)
"rate_limit": ["throttle_sla_warning_ms", "throttle_sla_critical_ms", "throttle_initial_limit"],

# 제안 B: rate_limit_rps 제거 (실제 연결 없으므로)
# param_module 역매핑에서 삭제 (service.py L971)
```

---

## 5. 영향 범위

| 파일 | 변경 내용 |
|------|-----------|
| `services/auto_tuning/service.py` | `module_params["rate_limit"]` 매핑 변경, `param_module` 역매핑 업데이트 |
| `services/throttle/adaptive.py` | `ThrottleConfig` hot-reload 지원 또는 setter 추가 |
| 신규 파일 | `ThrottleConfigApplier` 구현체 (화이트리스트 기반) |

---

## 6. 검증 항목

- [ ] `rate_limit_rps` 파라미터의 현재 ConfigApplier 구현체 확인 (외부 주입이므로 사용처 추적 필요)
- [ ] `ThrottleConfig` 속성이 런타임 변경 시 Gradient 계산에 즉시 반영되는지
- [ ] `sla_warning_ms` 변경 후 `SLA_WARNING` 이벤트 발생 패턴 변화 관찰
- [ ] `initial_limit` 변경이 현재 `current_limit`에 미치는 영향 범위 확인
- [ ] 기존 `rate_limit_rps` → 새 파라미터명으로 변경 시 하위 호환성
- [ ] AutoRollbackGuard가 ThrottleConfig 변경 후 메트릭 악화를 정확히 감지하는지
