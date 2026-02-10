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

### 4-1. 연동할 것 (범위 축소 — SLA 파라미터만)

> **결정**: `initial_limit`은 연동 범위에서 **제외**한다.
> **근거**: `initial_limit`은 `BaseThrottle.__init__()` (base.py L131)에서 `_current_limit`에 한 번만 반영되며, 런타임 변경 시 `_current_limit`에 즉시 반영되지 않는다. 유일한 재참조 시점은 Emergency 복구 (adaptive.py L1944)뿐이므로, 피드백 루프(POST_ADJUSTMENT_WAIT=30초)가 효과를 감지할 수 없어 오판/롤백 위험이 있다.

| 파라미터 | AutoTuning 매핑 | 이유 |
|----------|-----------------|------|
| `sla_warning_ms` | `throttle_sla_warning_ms` | `_maybe_adjust_limit()`에서 매 500ms 사이클마다 참조 (adaptive.py L1443) — 즉시 효과 |
| `sla_critical_ms` | `throttle_sla_critical_ms` | `_maybe_adjust_limit()`에서 매 500ms 사이클마다 참조 (adaptive.py L1385) — 즉시 효과 |

### 4-2. 연동하지 않을 것

| 파라미터 | 이유 |
|----------|------|
| `current_limit` / `rate_limit_rps` | Gradient 알고리즘 충돌 — 2-2절 참조 |
| `initial_limit` | 런타임 변경이 `_current_limit`에 반영되지 않음 (base.py L131) — 리뷰 8 결론 |
| `sample_interval_ms` | 변경 시 Gradient 계산 정확도에 직접 영향 |
| `_rtt_suggested_limit` | 내부 상태 — 외부 직접 조정 불가 |

### 4-3. ThrottleConfigApplier 필수 구현 (Atomic Swap 방식)

#### 문제: DummyConfigApplier가 유일한 구현체

현재 `ConfigApplier` Protocol의 **유일한 구현체**는 `DummyConfigApplier` (api/django/views/auto_tuning.py L51-65)이다:

```python
class DummyConfigApplier:
    def __init__(self):
        self._values = {}          # ← 인메모리 딕셔너리

    def apply(self, parameter, value):
        self._values[parameter] = value  # ← 딕셔너리에 저장만 하고 끝
        return True
```

이 구현체는:
1. **AdaptiveThrottle과 전혀 연결되지 않는다** — `self._values` 딕셔너리에 저장만 하고, `get_adaptive_throttle().config`에는 아무 영향이 없다.
2. **`factory.py`에 `get_auto_tuning_service()`가 없다** — `_get_auto_tuning_service()` (auto_tuning.py L20-28)이 항상 `ImportError → _create_default_service()` fallback을 타므로, DummyConfigApplier만 사용된다.
3. **결과**: RuntimeFeedbackLoop → DecisionEngine → ConfigApplier.apply() → **효과 없음** → 피드백 루프 전체가 사실상 미작동.

따라서 `ThrottleConfigApplier`는 RuntimeFeedback ↔ AdaptiveThrottle 연동의 **필수 구현**이다.

#### 설계: Atomic Swap

> 이전 `setattr()` 방식은 폐기. `ThrottleSettings`(Pydantic v2 BaseSettings)는 `frozen=True`가 아니므로(settings/throttle.py L34) Mutable이지만, `_maybe_adjust_limit()` 실행 중 config 값이 중간에 바뀌면 같은 사이클 내에서 `sla_critical_ms`와 `sla_warning_ms` 비교 기준이 불일치할 수 있다. Pydantic v2의 `model_copy(update=...)` 를 사용한 Atomic Swap으로 해결한다.

```python
# 파일: adapters/config_applier/throttle.py (신규)

import logging
from selfhealing.services.throttle.adaptive import get_adaptive_throttle

logger = logging.getLogger(__name__)


class ThrottleConfigApplier:
    """
    AdaptiveThrottle의 SLA 설정 전용 ConfigApplier.

    ConfigApplier Protocol (core/runtime_feedback.py L60-72) 구현체.
    화이트리스트 기반으로 허용된 파라미터만 조정하며,
    model_copy() Atomic Swap으로 Thread-Safety를 보장한다.

    비허용 파라미터(rate_limit_rps 등)는 No-op + 로그로 하위 호환성 유지.
    """

    # 조정 허용 파라미터 → config 속성명 매핑
    PARAM_TO_CONFIG: dict[str, str] = {
        "throttle_sla_warning_ms": "sla_warning_ms",
        "throttle_sla_critical_ms": "sla_critical_ms",
    }

    # No-op 처리할 레거시 파라미터 (하위 호환)
    LEGACY_NOOP_PARAMS: set[str] = {"rate_limit_rps"}

    def get_current(self, parameter: str) -> float:
        """현재 값 조회."""
        # No-op 레거시 파라미터
        if parameter in self.LEGACY_NOOP_PARAMS:
            # DummyConfigApplier와 동일 동작: 기존 테스트 호환
            return 0.0

        config_attr = self.PARAM_TO_CONFIG.get(parameter)
        if config_attr is None:
            raise ValueError(
                f"Parameter '{parameter}' not supported by ThrottleConfigApplier. "
                f"Allowed: {set(self.PARAM_TO_CONFIG.keys())}"
            )

        throttle = get_adaptive_throttle()
        return float(getattr(throttle.config, config_attr))

    def apply(self, parameter: str, value: float) -> bool:
        """
        설정 적용 — Atomic Swap 방식.

        Pydantic v2 model_copy(update=...)로 새 config 객체를 생성하고,
        throttle.config 참조를 한 번에 교체한다.
        Python GIL에 의해 참조 대입은 atomic이므로 _maybe_adjust_limit()
        실행 중에도 안전하다.
        """
        # No-op 레거시 파라미터 — 경고 없이 성공 반환 (하위 호환)
        if parameter in self.LEGACY_NOOP_PARAMS:
            logger.info(
                f"[ThrottleConfigApplier] '{parameter}' is deprecated (No-op). "
                f"Use throttle_sla_* parameters instead."
            )
            return True

        config_attr = self.PARAM_TO_CONFIG.get(parameter)
        if config_attr is None:
            return False

        throttle = get_adaptive_throttle()
        old_config = throttle.config

        # Atomic Swap: model_copy()로 새 객체 생성 후 참조 교체
        # ThrottleSettings는 BaseSettings(frozen 아님) — model_copy 사용 가능
        new_config = old_config.model_copy(update={config_attr: int(value)})
        throttle.config = new_config  # GIL atomic reference swap

        logger.info(
            f"[ThrottleConfigApplier] Applied {parameter}: "
            f"{getattr(old_config, config_attr)} → {int(value)} "
            f"(config swap)"
        )
        return True

    def rollback(self, parameter: str, value: float) -> bool:
        """롤백 적용 — apply()와 동일 로직."""
        return self.apply(parameter, value)
```

### 4-4. AdaptiveThrottle에 swap_config() 메서드 추가

> **네이밍 선택**: `update_config`는 기존 코드베이스에서 `ChaosScheduler`, `ColdStart`, `ConfigManager` 등 5곳 이상에서 사용 중이므로 혼동 위험. `swap_config`를 선택한다.

```python
# 파일: services/throttle/adaptive.py — AdaptiveThrottle 클래스에 추가

def swap_config(self, new_config: ThrottleConfig) -> ThrottleConfig:
    """
    Config 객체를 Atomic Swap으로 교체.

    GIL에 의해 참조 대입은 atomic이므로,
    _maybe_adjust_limit() 실행 중에도 안전하다.
    _current_limit, _base_limit_before_emergency 등
    파생 상태는 변경하지 않는다 (SLA 값만 교체 용도).

    Args:
        new_config: 새 ThrottleConfig (model_copy 등으로 생성)

    Returns:
        교체 전 이전 config 객체 (롤백용 보관)
    """
    old_config = self.config
    self.config = new_config
    logger.info(
        f"[AdaptiveThrottle] Config swapped: "
        f"sla_warning_ms={old_config.sla_warning_ms}→{new_config.sla_warning_ms}, "
        f"sla_critical_ms={old_config.sla_critical_ms}→{new_config.sla_critical_ms}"
    )
    return old_config
```

### 4-5. MODULES 매핑 변경

> **선택**: 제안 A를 채택 — `rate_limit` 모듈명은 유지하되, 파라미터 매핑을 SLA 전용으로 변경한다.
> **이유**: `MODULES` 리스트에서 `"rate_limit"` 자체를 삭제/변경하면 `_module_states` 딕셔너리(service.py L89), API 응답의 `modules` 필드 등에 영향을 줄 수 있다. 모듈명은 유지하고 내부 매핑만 변경하는 것이 안전하다.

```python
# 변경 전 (service.py L879-884)
module_params = {
    ...
    "rate_limit": ["rate_limit_rps"],
    ...
}

# 변경 후
module_params = {
    ...
    "rate_limit": ["throttle_sla_warning_ms", "throttle_sla_critical_ms", "rate_limit_rps"],
    ...
}
# rate_limit_rps는 유지 (하위 호환) — ThrottleConfigApplier가 No-op 처리

# 변경 전 (service.py L967-976)
param_module = {
    ...
    "rate_limit_rps": "rate_limit",
    ...
}

# 변경 후
param_module = {
    ...
    "rate_limit_rps": "rate_limit",               # 유지 (No-op)
    "throttle_sla_warning_ms": "rate_limit",       # 추가
    "throttle_sla_critical_ms": "rate_limit",      # 추가
    ...
}
```

---

## 5. 메트릭 관측 지연(Lag) 안전성 분석

### 5-1. 타이밍 체인

```
SLA 값 변경 → Gradient 사이클(500ms) × N회 → RTT 변화 관측
                                                   ↓
POST_ADJUSTMENT_WAIT(30초) 후 _detect_degradation() 실행
```

| 타이밍 | 값 | 소스 |
|--------|-----|------|
| Gradient 사이클 주기 | 500ms | `sample_interval_ms` (settings/throttle.py L80) |
| POST_ADJUSTMENT_WAIT | 30초 | `adjustment_wait` (settings/runtime_feedback.py L59) |
| RuntimeFeedback 주기 | 60초 | `interval_seconds` (runtime_feedback.py L121) |
| POST_ROLLBACK_COOLDOWN | 120초 | `rollback_cooldown` (settings/runtime_feedback.py L51) |

### 5-2. 안전성 확인

SLA 값 변경 후 30초(POST_ADJUSTMENT_WAIT) 안에 약 60회의 Gradient 사이클이 돈다. `_maybe_adjust_limit()`의 SLA 비교 로직(adaptive.py L1385, L1443)이 매 사이클 `self.config.sla_critical_ms`와 `self.config.sla_warning_ms`를 직접 참조하므로, config swap 즉시 새 기준이 적용된다.

### 5-3. 운영 제약

- `SELFHEALING_RUNTIME_ADJUSTMENT_WAIT`를 30초 미만으로 설정하면 SLA 변경 효과가 관측되기 전에 롤백될 위험이 있다.
- 현재 최소값 제약: `ge=5` (settings/runtime_feedback.py L62) — SLA 튜닝 시에는 **최소 30초 이상 유지** 권장.

---

## 6. DummyMetricsAdapter 교체 — 기존 어댑터 활용

### 6-1. 현재 문제

`_create_default_service()` (api/django/views/auto_tuning.py L38-48) 에서 `DummyMetricsAdapter`가 고정 0값만 반환하여 DecisionEngine이 어떤 조정 결정도 내리지 못한다:

```python
# 현재 (api/django/views/auto_tuning.py L38-48) — 모든 값 0
class DummyMetricsAdapter:
    def fetch_current_metrics(self):
        return {
            "p99_latency_ms": 0,
            "error_rate": 0,
            "retry_exhausted_rate": 0,
            "throughput_rps": 0,
        }
```

특히 `throttle_rate` 키가 없으므로 DecisionEngine의 `rate_limit_rps` 규칙(decision_engine.py L124)이 `metric_value is None → return None`으로 무조건 스킵된다.

### 6-2. 기존 구현체 현황

| 클래스 | 파일 | `throttle_rate` 포함 | 프로덕션 사용 가능 |
|--------|------|---------------------|-------------------|
| `InternalMetricsAdapter` | adapters/metrics/auto_tuning_adapter.py L40 | ✅ (L90) | ✅ |
| `PrometheusMetricsAdapter` | adapters/metrics/auto_tuning_adapter.py L132 | ✅ (L196) | ✅ (Prometheus 필요) |
| `ChaosAwareMetricsAdapter` | services/auto_tuning/chaos_aware_metrics.py L24 | Delegate | ✅ (래퍼) |

### 6-3. 선택: `InternalMetricsAdapter`

> **이유**: Prometheus 외부 의존 없이 동작하며, `record_metric()` API로 AdaptiveThrottle 상태를 수동/자동 주입할 수 있다. 기존 `fetch_current_metrics()` 반환 형식이 `MetricsAdapter Protocol` (runtime_feedback.py L52-54)과 정확히 일치한다.

### 6-4. 구현 — `_create_default_service()` 수정

```python
# 변경 전 (api/django/views/auto_tuning.py L92-95)
return AutoTuningService(
    metrics_adapter=DummyMetricsAdapter(),
    config_provider=DummyConfigProvider(),
    config_applier=DummyConfigApplier(),
    audit_adapter=audit_adapter,
)

# 변경 후
from selfhealing.adapters.metrics.auto_tuning_adapter import InternalMetricsAdapter
from selfhealing.adapters.config_applier.throttle import ThrottleConfigApplier
from selfhealing.adapters.config_applier.composite import CompositeConfigApplier

return AutoTuningService(
    metrics_adapter=InternalMetricsAdapter(),
    config_provider=DummyConfigProvider(),
    config_applier=CompositeConfigApplier([
        ThrottleConfigApplier(),   # throttle_sla_*, rate_limit_rps(No-op)
        DummyConfigApplier(),      # 나머지 모듈 (circuit_breaker, retry, jitter, timeout)
    ]),
    audit_adapter=audit_adapter,
)
```

> **중요 — 왜 CompositeConfigApplier가 지금 필수인가**:
> AutoTuningService는 5개 모듈의 파라미터를 **단일** `config_applier`로 라우팅한다 (service.py L107, L128, L136). `ThrottleConfigApplier`만 주입하면 `circuit_breaker_threshold`, `retry_count`, `jitter_range`, `timeout_ms` 파라미터가 `PARAM_TO_CONFIG`에 없어 `return False` → `_consecutive_failures` 증가 → 피드백 루프 정지. `DummyConfigApplier`를 fallback으로 유지하되, `CompositeConfigApplier`로 조합해야 기존 모듈이 깨지지 않는다.

`DummyMetricsAdapter` 클래스 정의(L38-48)와 `DummyConfigApplier` 클래스 정의(L51-65)는 **삭제하지 않고 유지**한다. `CompositeConfigApplier`의 fallback으로 계속 사용되며, 다른 코드에서 직접 import하는 경우도 대비.

---

## 7. ThrottleConfigApplier 팩토리 등록

### 7-1. 현재 팩토리 상태

`factory.py`(709줄)에 `get_auto_tuning_service()`가 존재하지 않는다. `_get_auto_tuning_service()` (api/django/views/auto_tuning.py L20-28)이 `from selfhealing.factory import get_auto_tuning_service`를 시도하지만 항상 `ImportError → _create_default_service()` fallback을 탄다.

### 7-2. 선택: `_create_default_service()` 직접 수정

> **이유**: `factory.py`의 `ProviderRegistry`는 캐시/큐/리포지토리 어댑터 전용 레지스트리로 설계되어 있으며, AutoTuningService 전용 팩토리 메서드를 추가하면 ProviderRegistry의 책임이 과도하게 확장된다. `_create_default_service()` 자체가 이미 AutoTuningService의 팩토리 역할을 하고 있으므로, 여기서 `ThrottleConfigApplier`를 주입하는 것이 최소 변경 원칙에 부합한다.

### 7-3. CompositeConfigApplier 구현 (필수)

> **결정 변경**: 이전에 "향후 확장 시 구현"으로 분류했으나, **지금 필수**로 승격한다.
> **이유**: AutoTuningService는 5개 모듈(circuit_breaker, retry, jitter, rate_limit, timeout)의 파라미터를 **단일** `config_applier`로 전달한다 (service.py L107, L128, L136). `ThrottleConfigApplier`만 주입하면 throttle 외 파라미터(`circuit_breaker_threshold`, `retry_count`, `jitter_range`, `timeout_ms`)가 `PARAM_TO_CONFIG`에 없어 `return False` → `_consecutive_failures` 증가 → 피드백 루프 정지 위험.

Composite 패턴으로 ThrottleConfigApplier + DummyConfigApplier(fallback)를 조합해야 한다.

```python
# 파일: adapters/config_applier/composite.py (신규)

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class ConfigApplier(Protocol):
    def get_current(self, parameter: str) -> float: ...
    def apply(self, parameter: str, value: float) -> bool: ...
    def rollback(self, parameter: str, value: float) -> bool: ...


class CompositeConfigApplier:
    """
    모듈별 ConfigApplier를 조합하는 Composite.

    appliers 리스트를 순서대로 순회하며, 첫 번째로 처리 가능한
    applier가 요청을 수행한다. 어떤 applier도 처리하지 못하면
    False를 반환한다.

    네이밍 일관성: CompositeCheckpointStorage, CompositeBackend 패턴.
    """

    def __init__(self, appliers: list[ConfigApplier]):
        if not appliers:
            raise ValueError("CompositeConfigApplier requires at least one applier")
        self._appliers = appliers

    def get_current(self, parameter: str) -> float:
        """첫 번째로 처리 가능한 applier에서 값 조회."""
        last_error = None
        for applier in self._appliers:
            try:
                return applier.get_current(parameter)
            except (ValueError, KeyError) as e:
                last_error = e
                continue
        # 모든 applier가 실패 → 마지막 에러 전파
        raise ValueError(
            f"No applier can handle parameter '{parameter}'"
        ) from last_error

    def apply(self, parameter: str, value: float) -> bool:
        """첫 번째로 True를 반환하는 applier에 위임."""
        for applier in self._appliers:
            if applier.apply(parameter, value):
                return True
        logger.warning(
            f"[CompositeConfigApplier] No applier handled: {parameter}={value}"
        )
        return False

    def rollback(self, parameter: str, value: float) -> bool:
        """apply()와 동일한 라우팅 로직으로 롤백."""
        for applier in self._appliers:
            if applier.rollback(parameter, value):
                return True
        return False
```

#### 라우팅 흐름

```
CompositeConfigApplier.apply("throttle_sla_warning_ms", 250)
  → ThrottleConfigApplier.apply() → PARAM_TO_CONFIG 매칭 → Atomic Swap → return True ✅
  (DummyConfigApplier까지 가지 않음)

CompositeConfigApplier.apply("rate_limit_rps", 1000)
  → ThrottleConfigApplier.apply() → LEGACY_NOOP_PARAMS → No-op → return True ✅
  (DummyConfigApplier까지 가지 않음)

CompositeConfigApplier.apply("circuit_breaker_threshold", 5)
  → ThrottleConfigApplier.apply() → PARAM_TO_CONFIG 미매칭, LEGACY 미매칭 → return False
  → DummyConfigApplier.apply() → self._values["circuit_breaker_threshold"] = 5 → return True ✅
```

#### 향후 확장

새 모듈 Applier 추가 시 `CompositeConfigApplier`의 appliers 리스트에 추가하면 된다:

```python
# 향후 CircuitBreakerConfigApplier가 생기면:
CompositeConfigApplier([
    ThrottleConfigApplier(),
    CircuitBreakerConfigApplier(),  # 신규
    DummyConfigApplier(),           # 항상 마지막 (fallback)
])
```

기존 `CompositeCheckpointStorage` (tests/unit/audit/checkpoint/test_checkpoint_strategy.py), `CompositeBackend` (tests/unit/audit/pipeline/test_audit_lazy_import.py) 등과 동일한 Composite 패턴.

---

## 8. rate_limit_rps No-op 처리 상세

### 8-1. 영향받는 모듈 (6개)

| 파일 | 위치 | 용도 | No-op 영향 |
|------|------|------|-----------|
| service.py L883 | `module_params` | 모듈-파라미터 매핑 | 유지 (4-5절 참조) |
| service.py L971, L986 | `param_module` | 파라미터-모듈 역매핑 | 유지 |
| decision_engine.py L124 | `DEFAULT_RULES` | `throttle_rate` 기반 조정 규칙 | 트리거 시 Applier가 No-op |
| safety_bounds.py L86-89 | `_default_bounds` | 안전 한계 (min=10, max=10000) | 검증 통과 후 Applier No-op |
| auto_rollback_guard.py L138 | `SYSTEM_DEFAULTS` | 최후 수단 기본값 (1000) | Applier No-op |
| settings/safety_bounds.py L145-155 | `SafetyBoundsSettings` | 환경변수 오버라이드 | 그대로 유지 |

### 8-2. No-op 동작 흐름

```
DecisionEngine: analyze(metrics) → rate_limit_rps 규칙 트리거
    ↓
SafetyBounds: is_within_bounds("rate_limit_rps", value) → True (기존 한계 통과)
    ↓
ThrottleConfigApplier.apply("rate_limit_rps", value)
    → LEGACY_NOOP_PARAMS에 포함
    → logger.info("deprecated, use throttle_sla_*")
    → return True  (성공으로 간주)
    ↓
AdjustmentResult(success=True) → 이력 기록 → 감사 로그
```

`return True`로 처리하는 이유: `return False`이면 `_apply_single_adjustment()` (runtime_feedback.py L305)가 `None`을 반환하여 "적용 실패"로 기록되고, `_consecutive_failures`가 증가하여 피드백 루프가 정지될 위험이 있다.

### 8-3. 로그 레벨 선택

`logger.info` 사용 (warning 아님). 이유: RuntimeFeedback 주기(60초)마다 반복 호출될 수 있으며, warning 레벨은 AlertManager 파이프라인에 영향을 줄 수 있다.

---

## 9. SafetyBounds SLA 파라미터 한계 추가

### 9-1. 현재 상태

`SafetyBounds._get_default_bounds()` (safety_bounds.py L62-99)에 `throttle_sla_warning_ms`, `throttle_sla_critical_ms`에 대한 한계가 **없다**. `is_within_bounds()` 호출 시 `"Unknown parameter"` 로그와 함께 거부된다.

### 9-2. 구현 — settings/safety_bounds.py 추가

```python
# settings/safety_bounds.py에 추가

# ==========================================================================
# throttle_sla_warning_ms 한계
# ==========================================================================
throttle_sla_warning_ms_min: float = Field(
    default=50,
    ge=10,
    description="SLA Warning 임계값 최소값 (ms)",
)
throttle_sla_warning_ms_max: float = Field(
    default=2000,
    le=5000,
    description="SLA Warning 임계값 최대값 (ms)",
)
throttle_sla_warning_ms_max_change: float = Field(
    default=0.3,
    ge=0.01,
    le=1.0,
    description="SLA Warning 한 사이클당 최대 변경 비율",
)

# ==========================================================================
# throttle_sla_critical_ms 한계
# ==========================================================================
throttle_sla_critical_ms_min: float = Field(
    default=100,
    ge=50,
    description="SLA Critical 임계값 최소값 (ms)",
)
throttle_sla_critical_ms_max: float = Field(
    default=5000,
    le=10000,
    description="SLA Critical 임계값 최대값 (ms)",
)
throttle_sla_critical_ms_max_change: float = Field(
    default=0.3,
    ge=0.01,
    le=1.0,
    description="SLA Critical 한 사이클당 최대 변경 비율",
)
```

한계값 근거:
- `sla_warning_ms` 기본 200ms (settings/throttle.py L132), 범위 50-2000ms — 너무 낮으면 정상 RTT에서도 warning 트리거
- `sla_critical_ms` 기본 500ms (settings/throttle.py L138), 범위 100-5000ms — 너무 낮으면 과도한 limit 감소
- `max_change_per_cycle` 0.3 (30%) — 한 번에 200ms→260ms 또는 200ms→140ms 수준으로 제한

### 9-3. 구현 — core/safety_bounds.py 추가

```python
# core/safety_bounds.py _get_default_bounds()에 추가

"throttle_sla_warning_ms": ParameterBound(
    min_value=settings.throttle_sla_warning_ms_min,
    max_value=settings.throttle_sla_warning_ms_max,
    max_change_per_cycle=settings.throttle_sla_warning_ms_max_change,
),
"throttle_sla_critical_ms": ParameterBound(
    min_value=settings.throttle_sla_critical_ms_min,
    max_value=settings.throttle_sla_critical_ms_max,
    max_change_per_cycle=settings.throttle_sla_critical_ms_max_change,
),
```

---

## 10. DecisionEngine SLA 전용 규칙 추가

### 10-1. 현재 상태

`DEFAULT_RULES` (decision_engine.py L90-131)에 `throttle_sla_warning_ms`나 `throttle_sla_critical_ms`에 대한 **규칙이 없다**. ThrottleConfigApplier를 만들어도 DecisionEngine이 조정 결정을 내리지 않으면 `apply()`가 호출되지 않는다.

### 10-2. 선택: custom_rules 주입

> **이유**: `DEFAULT_RULES`를 직접 수정하면 SLA 튜닝을 사용하지 않는 환경에서도 규칙이 평가된다. `DecisionEngine.__init__()` (decision_engine.py L147)의 `custom_rules` 파라미터를 통해 주입하면, AutoTuningService 생성 시점에 선택적으로 추가할 수 있다.

### 10-3. 구현 — SLA 튜닝 규칙 정의

```python
# 파일: services/auto_tuning/throttle_sla_rules.py (신규)

from selfhealing.core.decision_engine import AdjustmentPriority, AdjustmentRule

# SLA Warning 임계값 자동 조정 규칙
# 메트릭: p99_latency_ms vs 현재 sla_warning_ms 값
# 조건: p99 레이턴시가 sla_warning 임계값에 지속적으로 근접(90% 이상)하면 상향
#        → warning 빈도를 줄여 불필요한 limit 감소 방지
THROTTLE_SLA_RULES: list[AdjustmentRule] = [
    AdjustmentRule(
        parameter="throttle_sla_warning_ms",
        metric="p99_latency_ms",
        condition=lambda current, metric: metric > current * 0.9 and metric < current,
        adjustment=lambda current, metric: min(current * 1.15, 2000),
        reason="P99 레이턴시가 SLA Warning 임계값의 90% 이상 → 임계값 상향 (과도한 warning 방지)",
        priority=AdjustmentPriority.LOW,
        min_confidence=0.6,
    ),
    AdjustmentRule(
        parameter="throttle_sla_warning_ms",
        metric="p99_latency_ms",
        condition=lambda current, metric: metric < current * 0.5 and current > 100,
        adjustment=lambda current, metric: max(current * 0.85, 50),
        reason="P99 레이턴시가 SLA Warning 임계값의 50% 미만 → 임계값 하향 (감도 향상)",
        priority=AdjustmentPriority.LOW,
        min_confidence=0.6,
    ),
    AdjustmentRule(
        parameter="throttle_sla_critical_ms",
        metric="p99_latency_ms",
        condition=lambda current, metric: metric > current * 0.85 and metric < current,
        adjustment=lambda current, metric: min(current * 1.15, 5000),
        reason="P99 레이턴시가 SLA Critical 임계값의 85% 이상 → 임계값 상향",
        priority=AdjustmentPriority.MEDIUM,
        min_confidence=0.7,
    ),
]
```

### 10-4. 주입 위치

```python
# _create_default_service()에서 DecisionEngine 생성 시 custom_rules 전달
# AutoTuningService.__init__() (service.py L119)에서:
#   self.decision_engine = DecisionEngine(config_provider)
# → custom_rules를 전달하려면 AutoTuningService 생성자에 파라미터 추가 필요
#
# 최소 변경 접근: _create_default_service()에서 service 생성 후
# decision_engine에 직접 rules 추가

from selfhealing.services.auto_tuning.throttle_sla_rules import THROTTLE_SLA_RULES
from selfhealing.adapters.config_applier.composite import CompositeConfigApplier
from selfhealing.adapters.config_applier.throttle import ThrottleConfigApplier

service = AutoTuningService(
    metrics_adapter=InternalMetricsAdapter(),
    config_provider=DummyConfigProvider(),
    config_applier=CompositeConfigApplier([
        ThrottleConfigApplier(),
        DummyConfigApplier(),   # fallback
    ]),
    audit_adapter=audit_adapter,
)
# 생성 후 규칙 추가 (DecisionEngine.rules는 list이므로 extend 가능)
service.decision_engine.rules.extend(THROTTLE_SLA_RULES)
```

---

## 11. 영향 범위 (최종)

| 파일 | 변경 유형 | 변경 내용 |
|------|-----------|-----------|
| `adapters/config_applier/throttle.py` | **신규** | `ThrottleConfigApplier` — Atomic Swap + No-op |
| `adapters/config_applier/composite.py` | **신규** | `CompositeConfigApplier` — 모듈별 Applier 라우팅 Composite |
| `services/auto_tuning/throttle_sla_rules.py` | **신규** | `THROTTLE_SLA_RULES` — SLA 전용 DecisionEngine 규칙 |
| `services/throttle/adaptive.py` | **수정** | `swap_config()` 메서드 추가 |
| `services/auto_tuning/service.py` L879-884 | **수정** | `module_params["rate_limit"]` 매핑에 SLA 파라미터 추가 |
| `services/auto_tuning/service.py` L967-986 | **수정** | `param_module` 역매핑에 SLA 파라미터 추가 |
| `settings/safety_bounds.py` | **수정** | SLA 파라미터 한계값 필드 추가 |
| `core/safety_bounds.py` L62-99 | **수정** | `_get_default_bounds()`에 SLA 파라미터 ParameterBound 추가 |
| `api/django/views/auto_tuning.py` L92-95 | **수정** | `DummyMetricsAdapter` → `InternalMetricsAdapter`, `DummyConfigApplier` → `CompositeConfigApplier([ThrottleConfigApplier(), DummyConfigApplier()])` |

---

## 12. 구현 우선순위

| 순서 | 작업 | 이유 |
|------|------|------|
| 1 | `AdaptiveThrottle.swap_config()` | 다른 작업의 기반 (config 교체 메커니즘) |
| 2 | `ThrottleConfigApplier` 구현 | Dummy 대체, No-op 포함 |
| 3 | `CompositeConfigApplier` 구현 | 단일 applier 교체 시 비-throttle 모듈 파손 방지 (7-3절) |
| 4 | `SafetyBounds` SLA 한계 추가 | Applier `apply()` 호출 전 `is_within_bounds()` 통과 전제 |
| 5 | `THROTTLE_SLA_RULES` 규칙 정의 | DecisionEngine이 SLA 조정 결정을 내리기 위한 전제 |
| 6 | `_create_default_service()` 수정 | 주입 연결 (InternalMetricsAdapter + CompositeConfigApplier + SLA Rules) |
| 7 | `service.py` 매핑 업데이트 | module_params / param_module에 SLA 파라미터 추가 |

---

## 13. 검증 항목

### 13-1. 기존 검증 (1-4절에서 확인 완료)

- [x] `rate_limit_rps` 파라미터의 현재 ConfigApplier 구현체 → `DummyConfigApplier` (api/django/views/auto_tuning.py L51), 인메모리 딕셔너리만 사용, AdaptiveThrottle 미연결
- [x] `ThrottleConfig` 속성 런타임 변경 시 Gradient 반영 → `sla_warning_ms`, `sla_critical_ms`는 매 사이클 참조 (adaptive.py L1385, L1443), Atomic Swap 즉시 반영
- [x] `initial_limit` 변경이 `current_limit`에 미치는 영향 → 즉시 반영 안 됨 (base.py L131), 연동 범위에서 제외 결정

### 13-2. 신규 구현 검증

- [ ] `ThrottleConfigApplier.apply("throttle_sla_warning_ms", 250)` → `throttle.config.sla_warning_ms`가 250으로 변경되는지
- [ ] `ThrottleConfigApplier.apply("rate_limit_rps", 1000)` → No-op, `return True`, info 로그 출력
- [ ] `model_copy(update={"sla_warning_ms": 250})` 후 원본 config 불변인지 (Pydantic v2 copy semantics)
- [ ] `sla_warning_ms` 변경 후 30초(POST_ADJUSTMENT_WAIT) 내 `_detect_degradation()` 정상 판단
- [ ] `SafetyBounds.is_within_bounds("throttle_sla_warning_ms", 250)` → True (50-2000 범위 내)
- [ ] `DecisionEngine.analyze()` → `THROTTLE_SLA_RULES` 조건 충족 시 `AdjustmentDecision` 반환
- [ ] `InternalMetricsAdapter.fetch_current_metrics()` → `throttle_rate` 포함 메트릭 반환
- [ ] `CompositeConfigApplier.apply("circuit_breaker_threshold", 5)` → ThrottleConfigApplier skip → DummyConfigApplier 처리 → `return True`
- [ ] `CompositeConfigApplier.get_current("throttle_sla_warning_ms")` → ThrottleConfigApplier에서 반환 (Dummy까지 가지 않음)
- [ ] `sla_warning_ms` > `sla_critical_ms`가 되는 역전 상황 방지 (규칙의 `adjustment` lambda에서 상한/하한 제약)
