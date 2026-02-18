# 243. Cascading Timeout 방지 — Deadline 전파 + GradientCalculator 추출

| 항목 | 내용 |
|------|------|
| **문서번호** | 243 |
| **작성일** | 2026-02-18 |
| **상태** | 설계 완료 |
| **선행 문서** | 236, 239, 240, 241, 242 |
| **작업 규모** | 239번(Deadline Context) 활용 + GradientCalculator 리팩토링 (~25줄 + 파일 이동) |

---

## 1. 배경 및 문제 정의

### 1.1 Cascading Timeout 문제

MSA 호출 체인 A → B → C에서:

```
A (timeout=3s) → B (소요 2.5s) → C (남은 0.5s, 예상 처리 2s)
                                    ↓
                              C가 2초 작업 수행
                                    ↓
                              A는 이미 3s timeout
                                    ↓
                    C의 작업 결과 전부 폐기 (자원 낭비)
```

### 1.2 현재 시스템의 부분적 해결

#### 이미 존재하는 것

**SelfHealingHttpClient** (`services/http_client.py`)에 ContextVar 기반 헤더 전파 패턴이 존재합니다:

```python
# L25
_is_chaos_request: ContextVar[bool] = ContextVar("is_chaos_request", default=False)

# L250-261 (_get_headers)
if _is_chaos_request.get():
    headers[SYNTHETIC_HEADER] = "chaos-experiment"
    if self._experiment_id:
        headers[CHAOS_EXPERIMENT_ID_HEADER] = self._experiment_id
```

**GradientCalculator** (`services/throttle/adaptive.py` L463-580)가 RTT 추적을 이미 수행합니다:

```python
class GradientCalculator:
    def add_sample(self, rtt_ms: float) -> None:
        # EMA 기반 smoothed RTT 갱신
        self._smoothed_rtt = (
            self.smoothing_factor * rtt_ms
            + (1 - self.smoothing_factor) * self._smoothed_rtt
        )

    def get_current_rtt(self) -> float | None:
        return self._smoothed_rtt

    def get_snapshot(self) -> tuple[float | None, float]:
        # (current_rtt_ms, gradient) 단일 lock 내 반환
```

#### 존재하지 않는 것

1. **Deadline 전파 메커니즘**: 상위 서비스의 남은 시간을 하위 서비스에 전달하는 수단 → **239번 문서에서 해결**
2. **Fast-Fail 판정**: 남은 시간 < 예상 처리시간이면 즉시 거절 → **239번 문서에서 기본 구현**
3. **동적 예상 처리시간**: GradientCalculator의 RTT 데이터를 활용한 예상 처리시간 산출 → **이 문서에서 설계**

### 1.3 GradientCalculator의 현재 한계

GradientCalculator는 `AdaptiveThrottle` 클래스 내부에 종속되어 있습니다:

```python
# services/throttle/adaptive.py L590+
class AdaptiveThrottle(GovernanceCheckMixin, ThrottleDLQReplayMixin, SlidingWindowThrottle):
    def __init__(self, ...):
        self._gradient_calculator = GradientCalculator(
            smoothing_factor=self._config.gradient_smoothing_factor,
            ...
        )
```

외부 모듈 (예: AdmissionControlMiddleware, TrafficGate)에서 RTT 데이터에 접근할 방법이 없습니다.

---

## 2. 설계

### 2.1 해결 전략

```
┌─────────────────────────────────────────────────────┐
│  Cascading Timeout 완전 방지 = 3개 계층 조합         │
│                                                       │
│  [1] Deadline Context (239번)                         │
│      → 상위 서비스 deadline 수신 + ContextVar 전파     │
│                                                       │
│  [2] SelfHealingHttpClient Deadline 전파 (239번)      │
│      → 하위 서비스 호출 시 deadline 헤더 자동 주입      │
│                                                       │
│  [3] Dynamic Fast-Fail (이 문서)                      │
│      → GradientCalculator의 smoothed RTT 기반         │
│      → estimated_processing_time 동적 산출            │
│      → should_fast_fail(estimated_ms) 판정            │
└─────────────────────────────────────────────────────┘
```

### 2.2 변경 범위

| 파일 | 변경 유형 | 규모 |
|------|-----------|------|
| `services/throttle/gradient.py` | **신규 생성** — GradientCalculator 추출 | ~130줄 (이동) |
| `services/throttle/adaptive.py` | import 경로 변경 | ~3줄 |
| `scaling/traffic_gate.py` | Fast-Fail 체크 추가 (Tier 전달 포함) | ~20줄 |
| `scaling/deadline_context.py` | `get_estimated_processing_ms()` 추가 (Cold Start fallback 포함) | ~35줄 |
| `api/django/admission_control.py` | RTT 수집 (필터링 + Tier 분리 + 확률 샘플링) | ~20줄 |

---

## 3. 구현 상세

### 3.1 GradientCalculator 추출

**신규 파일**: `services/throttle/gradient.py`

현재 `adaptive.py` L463-580에 있는 `GradientCalculator`와 `RTTSample`을 별도 파일로 추출합니다.

```python
"""
Gradient Calculator — RTT Gradient 계산기.

AdaptiveThrottle에서 추출한 독립 모듈.
RTT 추적 및 gradient 계산을 제공하여, Deadline Context의
Dynamic Fast-Fail 판정에도 사용할 수 있습니다.

원본: services/throttle/adaptive.py L463-580
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class RTTSample:
    """RTT 샘플 데이터포인트."""
    timestamp: float
    rtt_ms: float


class GradientCalculator:
    """
    RTT Gradient 계산기 — Exponential Moving Average 기반.

    사용처:
    1. AdaptiveThrottle: 원래 사용처 (limit 동적 조절)
    2. TrafficGate/AdmissionControl: Dynamic Fast-Fail 예상 처리시간 제공 (신규)

    Positive gradient = RTT 증가 (과부하)
    Negative gradient = RTT 감소 (회복)
    """
    # ... (기존 코드 그대로 이동)
```

**`adaptive.py` 변경**:
```python
# 변경 전
# (adaptive.py 내부에 class GradientCalculator 정의)

# 변경 후
from selfhealing.services.throttle.gradient import GradientCalculator, RTTSample
```

### 3.2 GradientCalculator 싱글톤 레지스트리

여러 서비스/엔드포인트별 RTT를 추적하기 위한 레지스트리:

```python
# services/throttle/gradient.py에 추가

import threading

_calculators: dict[str, GradientCalculator] = {}
_calculators_lock = threading.Lock()


def get_gradient_calculator(
    name: str = "default",
    smoothing_factor: float = 0.5,
    sample_window_seconds: float = 10.0,
) -> GradientCalculator:
    """
    Named GradientCalculator 싱글톤 반환.

    Args:
        name: 계산기 이름 (서비스/엔드포인트 식별자)
        smoothing_factor: EMA 가중치
        sample_window_seconds: 샘플 윈도우

    Returns:
        GradientCalculator 인스턴스
    """
    if name not in _calculators:
        with _calculators_lock:
            if name not in _calculators:
                _calculators[name] = GradientCalculator(
                    smoothing_factor=smoothing_factor,
                    sample_window_seconds=sample_window_seconds,
                )
    return _calculators[name]


def reset_gradient_calculators() -> None:
    """테스트용 초기화."""
    with _calculators_lock:
        _calculators.clear()
```

### 3.3 Dynamic Fast-Fail: estimated_processing_time 산출

**위치**: `scaling/deadline_context.py` (239번 문서에서 생성한 파일에 추가)

#### 3.3.1 Tier별 Cold Start 기본값

서비스 재시작 직후 GradientCalculator에 RTT 데이터가 없으면 `estimated=None` → Fast-Fail 비활성이 됩니다.
이 구간에서 `DEFAULT_MINIMUM_USEFUL_TIME_MS=50ms` Static 임계치만으로는 50ms~실제RTT 사이 구간이 무방비입니다.

**Tier별 기본값을 두는 이유**: 기존 시스템에서 `AdmissionControlSettings`가 Tier별 Bulkhead (`tier_critical_max_concurrent`, `tier_standard_max_concurrent`, `tier_non_essential_max_concurrent`)와 Bulkhead Timeout (`tier_critical_bulkhead_timeout_seconds` 등)을 Tier별로 분리하는 패턴이 확립되어 있습니다. Cold Start 기본 예상 처리시간도 동일한 설계 원칙을 따릅니다.

```python
# scaling/deadline_context.py에 추가

# Cold Start 시 Tier별 기본 예상 처리시간 (ms)
# RTT 데이터가 충분히 쌓이기 전까지 사용하는 Conservative Estimate.
# critical: 빠른 경로 (인증, 결제 확인 등)
# standard: 일반 CRUD 작업
# non_essential: 무거운 쿼리 (통계, 리포트 등)
DEFAULT_ESTIMATED_MS_CRITICAL: float = float(
    os.environ.get("SELFHEALING_DEADLINE_DEFAULT_ESTIMATED_MS_CRITICAL", "50")
)
DEFAULT_ESTIMATED_MS_STANDARD: float = float(
    os.environ.get("SELFHEALING_DEADLINE_DEFAULT_ESTIMATED_MS_STANDARD", "200")
)
DEFAULT_ESTIMATED_MS_NON_ESSENTIAL: float = float(
    os.environ.get("SELFHEALING_DEADLINE_DEFAULT_ESTIMATED_MS_NON_ESSENTIAL", "500")
)

_TIER_DEFAULT_ESTIMATED_MS: dict[str, float] = {
    "critical": DEFAULT_ESTIMATED_MS_CRITICAL,
    "standard": DEFAULT_ESTIMATED_MS_STANDARD,
    "non_essential": DEFAULT_ESTIMATED_MS_NON_ESSENTIAL,
}


def get_tier_default_estimated_ms(tier_id: str = "standard") -> float:
    """
    Tier별 Cold Start 기본 예상 처리시간 반환.

    Args:
        tier_id: Tier 식별자 (critical, standard, non_essential)

    Returns:
        기본 예상 처리시간 (ms)
    """
    return _TIER_DEFAULT_ESTIMATED_MS.get(tier_id, DEFAULT_ESTIMATED_MS_STANDARD)
```

**Tier별 기본값 산출 근거**:

| Tier | 기본값 | 근거 |
|------|--------|------|
| `critical` | 50ms | `AdmissionControlSettings.tier_critical_bulkhead_timeout_seconds=0.05`(50ms)와 동일 수준. 빠른 경로이므로 보수적으로 짧게 |
| `standard` | 200ms | `ThrottleSettings.sla_warning_ms=200`과 동일. SLA Warning 임계치 이전이면 정상 처리 가능 |
| `non_essential` | 500ms | `ThrottleSettings.sla_critical_ms=500`과 동일. Heavy Query 특성 반영 |

#### 3.3.2 get_estimated_processing_ms 구현

```python
def get_estimated_processing_ms(
    calculator_name: str = "default",
    safety_margin: float = 1.5,
    tier_id: str = "standard",
) -> float:
    """
    GradientCalculator 기반 예상 처리시간 반환.

    현재 smoothed RTT × 안전 계수로 산출합니다.
    gradient가 양수(RTT 증가 추세)이면 안전 계수를 더 높입니다.
    RTT 데이터가 없으면(Cold Start) Tier별 기본값을 반환합니다.

    Args:
        calculator_name: GradientCalculator 이름
        safety_margin: 안전 계수 (기본 1.5 = 50% 여유)
        tier_id: Tier 식별자 (Cold Start fallback에 사용)

    Returns:
        예상 처리시간 (ms). Cold Start 시에도 기본값을 반환하므로
        None을 반환하지 않습니다.
    """
    try:
        from selfhealing.services.throttle.gradient import get_gradient_calculator

        calc = get_gradient_calculator(calculator_name)
        rtt, gradient = calc.get_snapshot()

        if rtt is None:
            # Cold Start: Tier별 기본값 반환
            return get_tier_default_estimated_ms(tier_id)

        # RTT 증가 추세이면 안전 계수 상향
        if gradient > 0.1:  # 10% 이상 증가
            safety_margin *= 1.0 + gradient  # gradient 비례 증가

        return rtt * safety_margin
    except ImportError:
        return get_tier_default_estimated_ms(tier_id)
```

**기존 설계 대비 변경점**:

| 항목 | 기존 | 변경 후 |
|------|------|--------|
| 반환 타입 | `float \| None` | `float` (항상 값 반환) |
| Cold Start | `None` → Fast-Fail 비활성 | Tier별 기본값 → Fast-Fail 즉시 작동 |
| `tier_id` 파라미터 | 없음 | 추가 (Cold Start fallback 용) |
| `ImportError` | `None` | Tier별 기본값 (Fail-Safe 방향 유지) |

### 3.4 TrafficGate에 Dynamic Fast-Fail 통합

**파일**: `scaling/traffic_gate.py`

`should_allow()` 메서드의 0단계 (Bulkhead 이전)에 추가합니다.
Tier 정보는 `metadata["tier_id"]`로 전달받아 Tier별 GradientCalculator를 조회합니다.
이 패턴은 기존 `should_allow()`의 `metadata` dict 활용 방식과 동일합니다.

```python
def should_allow(self, priority=0, bulkhead_name=None, metadata=None):
    current_level = self._rate_controller.get_state().level
    bulkhead_acquired = False

    # -1단계: Deadline Fast-Fail (신규)
    try:
        from selfhealing.scaling.deadline_context import (
            is_expired,
            should_fast_fail,
            get_estimated_processing_ms,
        )

        if is_expired():
            return TrafficDecision(
                allowed=False,
                reason="Deadline expired",
                level=current_level,
                gate="DeadlineContext",
                metadata=metadata,
            )

        # Dynamic Fast-Fail: RTT 기반 예상 처리시간 vs 남은 시간
        # metadata에서 tier_id를 꺼내 Tier별 GradientCalculator 조회
        tier_id = (metadata or {}).get("tier_id", "standard")
        calculator_name = f"admission_control:{tier_id}"
        estimated = get_estimated_processing_ms(
            calculator_name=calculator_name,
            tier_id=tier_id,
        )
        if should_fast_fail(estimated):
            return TrafficDecision(
                allowed=False,
                reason=(
                    f"Deadline Fast-Fail: estimated={estimated:.0f}ms "
                    f"exceeds remaining time"
                ),
                level=current_level,
                gate="DeadlineContext",
                metadata={
                    **(metadata or {}),
                    "estimated_ms": estimated,
                    "fast_fail": True,
                },
            )
    except ImportError:
        pass

    # 0단계: Bulkhead (기존)
    if bulkhead_name is not None:
        # ... 이하 기존 로직
```

**기존 설계 대비 변경점**:

| 항목 | 기존 | 변경 후 |
|------|------|--------|
| `estimated` None 체크 | `if estimated is not None and should_fast_fail(estimated)` | `if should_fast_fail(estimated)` — 항상 float 반환이므로 None 체크 불필요 |
| Calculator 이름 | `"default"` (전역 단일) | `f"admission_control:{tier_id}"` (Tier별 분리) |
| Tier 전달 | 없음 | `metadata["tier_id"]` 활용 |

**AdmissionControlMiddleware에서 metadata에 tier_id를 주입하는 코드** (기존 `should_allow()` 호출부 수정):

```python
# api/django/admission_control.py — _process_request() 내
decision = self._traffic_gate.should_allow(
    priority=traffic_priority,
    bulkhead_name=bulkhead_name,
    bulkhead_timeout=bulkhead_timeout,
    metadata={"tier_id": tier_id},  # ← tier_id 전달 추가
)
```

### 3.5 AdmissionControlMiddleware에서 RTT 샘플 수집

**파일**: `api/django/admission_control.py`

#### 3.5.1 문제: 원래 설계의 Data Pollution

원래 `__call__()`의 `finally` 블록에서 무조건 `add_sample()` 호출 시:

- **Fast-Fail 거절 요청** (~0ms) → smoothed RTT 급락
- **4xx 유효성 검증 실패** (~수ms) → 실제 비즈니스 처리 시간과 괴리
- **TrafficGate 거절** (~0ms) → 마찬가지로 RTT 오염

이러한 노이즈가 RTT 평균을 깎아먹으면 "처리 시간이 매우 짧다"고 착각하게 되어,
**정작 Fast-Fail이 작동해야 할 때 작동하지 않는 (False Negative)** 심각한 결함이 발생합니다.

#### 3.5.2 해결: 3중 필터링 (상태 코드 + 최소 임계치 + 확률 샘플링)

RTT 수집 위치를 `__call__()`의 `finally`에서 `_process_request()` 내 **정상 응답 경로**로 이동합니다.

```python
# api/django/admission_control.py — _process_request() 내
import random
import time

# 환경변수 기반 상수 (모듈 레벨)
_RTT_MIN_SAMPLE_MS: float = float(
    os.environ.get("SELFHEALING_DEADLINE_RTT_MIN_SAMPLE_MS", "5")
)
_RTT_SAMPLE_RATE: float = float(
    os.environ.get("SELFHEALING_DEADLINE_RTT_SAMPLE_RATE", "0.1")
)

def _process_request(self, request):
    # ... (0단계: Deadline, 1단계: Tier 분류, 2단계: tier 주입, 3단계: TrafficGate)
    # ... 기존 로직 동일

    # 4. 허용 시 다음 미들웨어로 전달
    start_time = time.perf_counter()
    try:
        response = self.get_response(request)
    finally:
        # Bulkhead 리소스 반환
        if decision.bulkhead_acquired and decision.bulkhead_name:
            self._traffic_gate.release_bulkhead(decision.bulkhead_name)

    # 5. RTT 샘플 수집 — 3중 필터링
    # 필터 1: HTTP 2xx 성공 응답만 (실제 비즈니스 로직을 수행한 요청)
    # 필터 2: 최소 임계치 이상 (Health Check 등 노이즈 제거)
    # 필터 3: 확률 샘플링 (Lock 경합 1/10 감소)
    try:
        if 200 <= response.status_code < 300:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            if elapsed_ms >= _RTT_MIN_SAMPLE_MS:
                if random.random() < _RTT_SAMPLE_RATE:
                    from selfhealing.services.throttle.gradient import (
                        get_gradient_calculator,
                    )
                    get_gradient_calculator(
                        f"admission_control:{tier_id}"
                    ).add_sample(elapsed_ms)
    except Exception:
        pass  # Fail-Open: RTT 수집 실패가 요청 처리에 영향 없음

    return response
```

#### 3.5.3 3중 필터링 설계 근거

| 필터 | 조건 | 목적 | 코드 근거 |
|------|------|------|----------|
| **상태 코드** | `200 <= status_code < 300` | Fast-Fail/4xx/5xx 노이즈 제거 | `GradientCalculator.add_sample()`에 자체 필터 없음 (adaptive.py L498-512) |
| **최소 임계치** | `elapsed_ms >= 5.0` | Health Check/CORS 등 초단기 요청 제거 | 환경변수 `SELFHEALING_DEADLINE_RTT_MIN_SAMPLE_MS` (기본 5ms) |
| **확률 샘플링** | `random.random() < 0.1` | Lock 경합 1/10 감소, EMA 특성상 10% 샘플로 추세 파악 충분 | 기존 패턴: `adaptive_dlq_replay.py` L158-159 `random.random() > sampling_rate` |

**수집 위치 변경 이유**: `__call__()`의 `finally`가 아닌 `_process_request()` 내 `response = self.get_response(request)` 이후로 이동합니다.
현재 `__call__()` 구조에서 `response` 객체는 `_process_request()` 내부에서 생성되어 즉시 `return`됩니다.
따라서 `__call__()` 레벨의 `finally`에서는 `response.status_code`에 접근할 수 없습니다.

```python
# 현재 __call__() 구조 — response가 스코프 밖
def __call__(self, request):
    try:
        return self._process_request(request)  # response가 즉시 return
    except Exception:
        return self.get_response(request)
    # finally에서 response.status_code 접근 불가
```

#### 3.5.4 Tier별 GradientCalculator 분리

`get_gradient_calculator(f"admission_control:{tier_id}")` 네이밍으로 Tier별 RTT를 분리합니다.

**분리 이유**: `/api/heavy-stat` (5초 소요, non_essential)와 `/api/health` (10ms, critical)가
하나의 계산기에 섞이면 EMA smoothed RTT가 무의미해집니다.
Tier 3개(critical, standard, non_essential)만 사용하므로 카디널리티 이슈가 전혀 없습니다.

**네이밍 패턴**: 기존 Bulkhead가 `f"tier:{tier_id}"` 패턴을 사용하므로 (admission_control.py L230),
GradientCalculator도 동일한 `{prefix}:{tier_id}` 패턴을 따릅니다.

| Tier | Calculator 이름 | 예상 RTT 범위 |
|------|----------------|---------------|
| `critical` | `admission_control:critical` | 10~100ms |
| `standard` | `admission_control:standard` | 50~500ms |
| `non_essential` | `admission_control:non_essential` | 100~5000ms |

---

## 4. 데이터 흐름

### 4.1 전체 흐름도

```
[상위 서비스 A]
    │
    │ X-Deadline-Remaining: 3000ms
    ▼
[AdmissionControlMiddleware]
    │
    ├─ Deadline 파싱: 3000ms → ContextVar 설정 (239번)
    ├─ RTT 기반 Fast-Fail 체크:
    │   estimated = GradientCalculator("admission_control").get_snapshot()
    │   if remaining(3000ms) < estimated(200ms × 1.5 = 300ms): FAIL
    │   → 3000 > 300 → 통과
    │
    ├─ TierRegistry tier 분류
    ├─ TrafficGate 판정 (Deadline Fast-Fail 포함)
    │
    ├─ 비즈니스 로직 처리 (소요: 2500ms)
    │
    └─ SelfHealingHttpClient → 서비스 C 호출
        │
        │ X-Deadline-Remaining: 450ms  ← 자동 계산 (3000 - 2500 - 전파 오버헤드)
        ▼
    [서비스 C]
        │
        ├─ Deadline 파싱: 450ms
        ├─ estimated = 2000ms (C의 GradientCalculator)
        ├─ should_fast_fail(2000): 450 < 2000 → True
        ├─ 즉시 408 반환 ← C의 2초 작업 방지!
        │
    [서비스 B]
        └─ 408 수신 → 상위에 빠르게 에러 전파
```

### 4.2 RTT 데이터 수집 경로

```
[요청 처리 완료 — _process_request() 내 response 확보 후]
    ↓
필터 1: response.status_code 200~299?
    ├─ No → 수집 스킵 (4xx/5xx 노이즈 방지)
    └─ Yes ↓
필터 2: elapsed_ms >= 5.0?
    ├─ No → 수집 스킵 (Health Check 등 노이즈 방지)
    └─ Yes ↓
필터 3: random.random() < 0.1?
    ├─ No → 수집 스킵 (Lock 경합 감소)
    └─ Yes ↓
GradientCalculator(f"admission_control:{tier_id}").add_sample(elapsed_ms)
    ↓
smoothed_rtt = α × elapsed + (1-α) × smoothed_rtt   (EMA)
    ↓
[다음 요청의 Fast-Fail 판정에 사용]
    ↓
※ Cold Start (smoothed_rtt=None) 시:
   get_tier_default_estimated_ms(tier_id) → 기본값 반환
   critical=50ms, standard=200ms, non_essential=500ms
```

---

## 5. GradientCalculator 추출 상세

### 5.1 추출 대상

| 원본 위치 | 추출 위치 | 내용 |
|-----------|-----------|------|
| `adaptive.py` L453-461 | `gradient.py` | `RTTSample` dataclass |
| `adaptive.py` L463-580 | `gradient.py` | `GradientCalculator` class |

### 5.2 하위 호환

`adaptive.py`에 re-export를 유지하여 기존 import를 깨뜨리지 않습니다:

```python
# services/throttle/adaptive.py
from selfhealing.services.throttle.gradient import (  # noqa: F401
    GradientCalculator,
    RTTSample,
)
```

### 5.3 AdaptiveThrottle 변경 없음

```python
# AdaptiveThrottle.__init__() — 변경 없음
self._gradient_calculator = GradientCalculator(
    smoothing_factor=self._config.gradient_smoothing_factor,
    sample_window_seconds=self._config.gradient_sample_window,
    min_samples=self._config.gradient_min_samples,
)
```

import 경로만 변경되며, 인스턴스 생성/사용 방식은 동일합니다.

---

## 6. 테스트 전략

### 6.1 단위 테스트

```
tests/unit/services/throttle/test_gradient_extracted.py
├── TestGradientCalculatorExtraction
│   ├── test_import_from_new_module        # gradient.py에서 import 가능
│   ├── test_import_from_adaptive_compat    # adaptive.py에서도 여전히 import 가능
│   ├── test_singleton_registry             # get_gradient_calculator() 동일 인스턴스
│   ├── test_singleton_by_tier              # tier별 인스턴스 분리
│   └── test_reset_clears_all              # reset_gradient_calculators()

tests/unit/scaling/test_deadline_fast_fail.py
├── TestDynamicFastFail
│   ├── test_estimated_with_rtt_data       # smoothed_rtt=200, margin=1.5 → 300ms
│   ├── test_cold_start_returns_tier_default  # 데이터 없음 → critical=50ms
│   ├── test_cold_start_standard_default   # 데이터 없음 → standard=200ms
│   ├── test_cold_start_non_essential_default  # 데이터 없음 → non_essential=500ms
│   ├── test_gradient_increases_margin     # gradient=0.5 → margin=1.5×1.5=2.25
│   ├── test_should_fast_fail_integration  # remaining=400, estimated=500 → True
│   └── test_import_error_returns_tier_default  # ImportError → Tier별 기본값

tests/unit/scaling/test_traffic_gate_deadline.py
├── TestTrafficGateDeadline
│   ├── test_expired_deadline_rejected     # deadline 만료 → allowed=False
│   ├── test_fast_fail_rejected            # estimated > remaining → allowed=False
│   ├── test_fast_fail_with_tier_metadata  # metadata["tier_id"]로 Tier별 계산기 조회
│   ├── test_no_deadline_passthrough       # deadline 없음 → 기존 동작
│   └── test_import_error_passthrough      # ImportError → 기존 동작

tests/unit/api/test_admission_control_rtt.py
├── TestRTTSampling
│   ├── test_2xx_response_sampled          # 200 OK → add_sample 호출 가능
│   ├── test_4xx_response_not_sampled      # 400 Bad Request → add_sample 미호출
│   ├── test_5xx_response_not_sampled      # 500 Error → add_sample 미호출
│   ├── test_below_min_threshold_not_sampled  # 3ms 응답 → add_sample 미호출
│   ├── test_sampling_rate_respected       # 10% 확률로만 호출
│   ├── test_tier_separated_calculator     # critical/standard 분리 확인
│   └── test_rtt_collection_fail_open      # 수집 실패 시 요청 처리 무영향
```

### 6.2 통합 테스트

```
tests/integration/test_cascading_timeout.py
├── test_full_chain_fast_fail
│   # A → B → C 시뮬레이션
│   # C에서 Fast-Fail → 전체 체인 조기 종료 확인
├── test_deadline_propagation_accuracy
│   # 전파된 deadline이 실제 경과 시간만큼 감소했는지 확인
├── test_rtt_feedback_loop
│   # 응답 시간 증가 → GradientCalculator → Fast-Fail 임계치 자동 조정
├── test_cold_start_fast_fail
│   # 서비스 재시작 직후 → Tier별 기본값으로 Fast-Fail 작동 확인
├── test_data_pollution_prevention
│   # Fast-Fail 거절 후 → smoothed_rtt 불변 확인
└── test_tier_rtt_isolation
    # critical 요청 RTT가 non_essential 계산기에 영향 없음 확인
```

---

## 7. 설정

### 7.1 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SELFHEALING_DEADLINE_FAST_FAIL_ENABLED` | `true` | Dynamic Fast-Fail 활성화 |
| `SELFHEALING_DEADLINE_SAFETY_MARGIN` | `1.5` | 예상 처리시간 안전 계수 |
| `SELFHEALING_DEADLINE_RTT_SMOOTHING_FACTOR` | `0.5` | GradientCalculator EMA 가중치 |
| `SELFHEALING_DEADLINE_RTT_MIN_SAMPLE_MS` | `5` | RTT 샘플 최소 임계치 (ms). 이 미만은 노이즈로 간주하여 수집 제외 |
| `SELFHEALING_DEADLINE_RTT_SAMPLE_RATE` | `0.1` | RTT 수집 확률 샘플링 비율 (0.1 = 10%). Lock 경합 감소용 |
| `SELFHEALING_DEADLINE_DEFAULT_ESTIMATED_MS_CRITICAL` | `50` | Cold Start 시 critical tier 기본 예상 처리시간 (ms) |
| `SELFHEALING_DEADLINE_DEFAULT_ESTIMATED_MS_STANDARD` | `200` | Cold Start 시 standard tier 기본 예상 처리시간 (ms) |
| `SELFHEALING_DEADLINE_DEFAULT_ESTIMATED_MS_NON_ESSENTIAL` | `500` | Cold Start 시 non_essential tier 기본 예상 처리시간 (ms) |

### 7.2 Prometheus 메트릭

| 메트릭 | 타입 | 라벨 | 설명 |
|--------|------|------|------|
| `selfhealing_deadline_fast_fail_total` | Counter | `gate`, `tier` | Fast-Fail 거절 횟수 |
| `selfhealing_deadline_estimated_ms` | Histogram | `calculator` | 예상 처리시간 분포 |
| `selfhealing_gradient_rtt_ms` | Gauge | `calculator` | 현재 smoothed RTT |
| `selfhealing_gradient_value` | Gauge | `calculator` | 현재 gradient 값 |

---

## 8. 239번 문서와의 관계

| 기능 | 239번 (Deadline Context) | 243번 (Cascading Timeout) |
|------|--------------------------|--------------------------|
| Deadline 수신 | `parse_deadline_header()` | — (239번 사용) |
| ContextVar 전파 | `set_deadline()`, `get_remaining_ms()` | — (239번 사용) |
| HTTP 헤더 전파 | `SelfHealingHttpClient._get_headers()` | — (239번 사용) |
| Static Fast-Fail | `should_fast_fail(estimated_ms)` | — (239번 사용) |
| **Dynamic estimated_ms** | — | `get_estimated_processing_ms()` |
| **GradientCalculator 추출** | — | `gradient.py` |
| **RTT 피드백 루프** | — | `AdmissionControlMiddleware.finally` |

239번은 **인프라**(ContextVar, 헤더 파싱, 전파)를 제공하고, 243번은 **지능**(RTT 기반 동적 임계치)을 추가합니다.

---

## 9. Fail-Open 안전성

| 시나리오 | 동작 |
|---------|------|
| GradientCalculator 데이터 없음 (Cold Start) | `get_estimated_processing_ms()` → Tier별 기본값 반환 → Fast-Fail **즉시 작동** |
| `ImportError` (모듈 미설치) | `try/except ImportError` → Tier별 기본값 반환 (기존 동작 유지) |
| Deadline 헤더 없음 | `is_expired()` → `False`, `should_fast_fail()` → `False` → 기존 동작 |
| RTT 수집 실패 | `try/except Exception: pass` → 요청 처리에 영향 없음 |
| Safety margin 과대 | 더 많이 거절 → 자원 낭비 방지 (안전 방향) |
| 4xx/5xx 요청의 RTT 오염 | 2xx 성공 응답만 수집 → smoothed_rtt 정확도 보장 |
| Health Check 초단기 요청 | `RTT_MIN_SAMPLE_MS=5` 필터 → 노이즈 제거 |
| 확률 샘플링 누락 | EMA 특성상 10% 샘플로 추세 파악 충분. 누락 시 이전 smoothed_rtt 유지 |
| Tier별 Calculator 미생성 | `get_gradient_calculator()` 내부 Double-Checked Locking → 자동 생성 |
| `random` 모듈 Import 실패 | 최외곽 `try/except Exception: pass` → Fail-Open |

모든 신규 코드는 `try/except` 또는 기본값 반환으로 보호되어 기존 시스템을 전혀 깨뜨리지 않습니다.
Cold Start 시에도 Tier별 기본값이 즉시 작동하므로, 기존 대비 **안전성이 향상**되었습니다.

---

## 10. 리뷰 반영 보완 설계

### 10.1 RTT 샘플링 오염 방지 (Data Pollution Prevention)

| 항목 | 내용 |
|------|------|
| **판정** | 필수 수정 (치명적 결함) |
| **반영 위치** | §3.5 전면 재작성 |

**문제**: 원래 설계의 `__call__()` `finally` 블록에서 무조건 `add_sample()` 호출 시,
Fast-Fail 거절(~0ms), 4xx 유효성 검증 실패, TrafficGate 거절 등이 RTT에 포함되어
smoothed RTT가 급락하고, 실제로 Fast-Fail이 필요한 상황에서 작동하지 않음 (False Negative).

**해결**: 3중 필터링 적용.

1. **HTTP 2xx만 수집** — 실제 비즈니스 로직을 수행한 요청만 의미 있음
2. **최소 임계치** `SELFHEALING_DEADLINE_RTT_MIN_SAMPLE_MS=5` — Health Check, CORS 등 제거
3. **확률 샘플링** `SELFHEALING_DEADLINE_RTT_SAMPLE_RATE=0.1` — Lock 경합 감소

**수집 위치 변경**: `__call__()` `finally` → `_process_request()` 내 `response` 획보 후.
현재 `__call__()` 구조에서 `response` 객체는 `_process_request()` 내부에서 생성되어
즉시 `return`되므로 `__call__()` 레벨 `finally`에서 `response.status_code` 접근이 불가합니다.

**확률 샘플링 패턴 선택 근거**: `random.random() < rate` 패턴을 선택한 이유는
기존 시스템에서 동일 패턴이 확립되어 있기 때문입니다:
- `adaptive_dlq_replay.py` L158-159: `if random.random() > sampling_rate:`
- `hedging/result_validator.py` L180: `return random.random() < self._sample_rate`
- `services/throttle/audit.py` L247: `return random.random() < rate`

**샘플링을 AdmissionControlMiddleware 호출 지점에만 적용하는 이유**:
기존 `AdaptiveThrottle.record_response()`는 Throttle limit 동적 조절에 정확한 RTT가 필요하며,
이 경로의 샘플링을 줄이면 Gradient 계산 정확도가 떨어집니다.
243번에서 추가하는 AdmissionControlMiddleware의 RTT 수집은 **별도 Calculator 인스턴스**
(`admission_control:{tier_id}`)를 사용하므로 기존 경로와 독립적입니다.

---

### 10.2 Tier별 RTT 분리 (Endpoint Granularity)

| 항목 | 내용 |
|------|------|
| **판정** | 필수 수정 (정확도 향상) |
| **반영 위치** | §3.4, §3.5 |

**문제**: `get_gradient_calculator("admission_control")` 단일 계산기로
`/api/heavy-stat`(5초 소요)와 `/api/health`(10ms)가 섞이면 smoothed RTT가 무의미.

**해결**: Tier별 GradientCalculator 분리 — `f"admission_control:{tier_id}"`

**Path별 분리 대신 Tier별 분리를 선택한 이유**:

| 방식 | Calculator 수 | Cardinality 위험 | 정확도 |
|------|-------------|-----------------|--------|
| 전역 단일 | 1 | 없음 | ❌ 무의미 |
| **Tier별** | **3** | **없음** | **✅ 충분** |
| Path별 | 수십~수백 | ⚠️ 높음 | ✅ 높음 |
| Path + Method별 | 수백~수천 | ❌ 폭발 | ✅ 최고 |

Tier 3개는 기존 시스템의 근간이며 (`critical`, `standard`, `non_essential`),
`AdmissionControlSettings`가 이미 이 3개 Tier를 기준으로 모든 설정을 분리합니다:
- `tier_critical_max_concurrent` / `tier_standard_max_concurrent` / `tier_non_essential_max_concurrent`
- `tier_critical_bulkhead_timeout_seconds` / `tier_standard_bulkhead_timeout_seconds` / `tier_non_essential_bulkhead_timeout_seconds`

GradientCalculator도 동일한 Tier 기준으로 분리하면
**시스템 전체의 일관성이 유지**됩니다.

---

### 10.3 Cold Start 보호 — Tier별 기본 예상 처리시간

| 항목 | 내용 |
|------|------|
| **판정** | 필수 추가 (초기 안정성 확보) |
| **반영 위치** | §3.3 |

**문제**: Cold Start 시 GradientCalculator에 RTT 데이터가 없으면
`get_estimated_processing_ms()` → `None` → Fast-Fail 비활성.
`DEFAULT_MINIMUM_USEFUL_TIME_MS=50ms` Static 임계치만 작동하여
50ms~실제RTT 사이 구간이 무방비.

**해결**: `get_estimated_processing_ms()`의 반환 타입을 `float | None` → `float`로 변경.
RTT 데이터 없을 때 Tier별 기본값을 Fallback으로 반환합니다.

**Tier별 기본값을 설정 가능하게 만든 이유**:
기존 시스템이 Tier별로 명확하게 구분하는 설정이 다수 존재합니다:
- Bulkhead max_concurrent: critical=100, standard=50, non_essential=20
- Bulkhead timeout: critical=0.05s, standard=0.03s, non_essential=0.0s
- DLQ 샘플링: critical=100%, standard=sampling_rate, non_essential=스킵

Cold Start 기본 예상 처리시간도 Tier별로 구분하지 않으면
시스템 전체의 **Tier 기반 설계 일관성이 깨집니다**.
또한 엔터프라이즈급 환경에서는 배포 환경마다 Tier별 워크로드 특성이 다르므로
환경변수로 조정 가능하게 만들어야 합니다.

기본값 산출 근거:

| Tier | 기본값 | 참조 설정 | 근거 |
|------|--------|----------|------|
| `critical` | 50ms | `tier_critical_bulkhead_timeout_seconds=0.05` | 빠른 경로 — Bulkhead 대기 시간과 동일 수준 |
| `standard` | 200ms | `ThrottleSettings.sla_warning_ms=200` | 일반 경로 — SLA Warning 임계치 |
| `non_essential` | 500ms | `ThrottleSettings.sla_critical_ms=500` | 무거운 경로 — SLA Critical 임계치 |

---

### 10.4 Clock Skew 및 전파 지연 확인

| 항목 | 내용 |
|------|------|
| **판정** | 충분함 — 추가 조치 없음 |
| **기구현 코드** | `scaling/deadline_context.py` |

이미 239번 문서에서 구현 완료:

- `set_deadline()`에서 `DEFAULT_NETWORK_LATENCY_BUFFER_MS=50ms` 차감
- `time.monotonic()` 기반으로 시스템 클럭 변경에 안전
- `SELFHEALING_DEADLINE_NETWORK_BUFFER_MS` 환경변수로 Cross-region 배포 시 조정 가능
- `get_propagation_header_value()`에서 `get_remaining_ms()` 호출 시점의 실제 경과 시간 자동 반영

`get_estimated_processing_ms()` 내부에서 추가 네트워크 차감은 불필요합니다.
Inbound 네트워크 지연은 `set_deadline()` 시점에 이미 차감되었고,
`estimated_processing_ms`는 순수 서버 사이드 처리시간 예측이기 때문입니다.

---

### 10.5 GradientCalculator 성능 — 확률 샘플링

| 항목 | 내용 |
|------|------|
| **판정** | 보완 권장 (High Traffic 대비) |
| **반영 위치** | §3.5 |

**기존 Lock 구조 분석**:

`GradientCalculator`의 Lock 사용 지점 (adaptive.py L463-580):
- `add_sample()`: `with self._lock` — EMA 산술연산 + deque append (O(1))
- `get_snapshot()`: `with self._lock` — 단일 Lock으로 RTT+gradient 동시 반환 (최적화 완료)

Python GIL 환경에서 `threading.Lock`의 추가 오버헤드는 미미하고,
`deque(maxlen=100)` bounded collection이므로 메모리 무한 증가 없습니다.

**그럼에도 샘플링을 적용하는 이유**:

1. 엔터프라이즈급 수만 RPS 환경에서 **매 요청마다** Lock 획득은 불필요
2. EMA 알고리즘 특성상 **10% 샘플로도 추세 파악에 충분** (모든 샘플을 쓸 필요 없음)
3. `SELFHEALING_DEADLINE_RTT_SAMPLE_RATE` 환경변수로 조정 가능 — 100% 수집도 가능

**싱글톤 레지스트리 Lock 분석**: `_calculators_lock`은 Double-Checked Locking 패턴으로
초기 생성 시에만 Lock 경합이 발생하고, 이후 읽기는 Lock-free입니다.
Tier 3개 Calculator는 서비스 시작 직후 수 요청 내에 모두 생성되므로 문제 없습니다.

**기존 AdaptiveThrottle 경로에 샘플링을 적용하지 않는 이유**:
`AdaptiveThrottle.record_response()`는 Throttle limit 동적 조절에 정확한 RTT가 필요합니다.
이 경로의 샘플링을 줄이면 Gradient 계산 정확도가 떨어지므로,
샘플링은 **243번에서 추가하는 AdmissionControlMiddleware 경로에만** 적용합니다.
두 경로는 별도 Calculator 인스턴스를 사용하므로 서로 독립적입니다.
