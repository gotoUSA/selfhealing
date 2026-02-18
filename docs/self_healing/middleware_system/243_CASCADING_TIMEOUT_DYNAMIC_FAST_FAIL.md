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
| `scaling/traffic_gate.py` | Fast-Fail 체크 추가 | ~15줄 |
| `api/django/admission_control.py` | RTT 기반 Fast-Fail 연동 | ~10줄 |

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

```python
def get_estimated_processing_ms(
    calculator_name: str = "default",
    safety_margin: float = 1.5,
) -> float | None:
    """
    GradientCalculator 기반 예상 처리시간 반환.

    현재 smoothed RTT × 안전 계수로 산출합니다.
    gradient가 양수(RTT 증가 추세)이면 안전 계수를 더 높입니다.

    Args:
        calculator_name: GradientCalculator 이름
        safety_margin: 안전 계수 (기본 1.5 = 50% 여유)

    Returns:
        예상 처리시간 (ms) 또는 데이터 부족 시 None
    """
    try:
        from selfhealing.services.throttle.gradient import get_gradient_calculator

        calc = get_gradient_calculator(calculator_name)
        rtt, gradient = calc.get_snapshot()

        if rtt is None:
            return None

        # RTT 증가 추세이면 안전 계수 상향
        if gradient > 0.1:  # 10% 이상 증가
            safety_margin *= 1.0 + gradient  # gradient 비례 증가

        return rtt * safety_margin
    except ImportError:
        return None
```

### 3.4 TrafficGate에 Dynamic Fast-Fail 통합

**파일**: `scaling/traffic_gate.py`

`should_allow()` 메서드의 0단계 (Bulkhead 이전)에 추가:

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
        estimated = get_estimated_processing_ms()
        if estimated is not None and should_fast_fail(estimated):
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

### 3.5 AdmissionControlMiddleware에서 RTT 샘플 수집

**파일**: `api/django/admission_control.py`

미들웨어의 `__call__()`에서 응답 시간을 GradientCalculator에 피드백합니다:

```python
def __call__(self, request):
    if not self._enabled:
        return self.get_response(request)

    start_time = time.perf_counter()
    try:
        response = self._process_request(request)
        return response
    except Exception as e:
        logger.error(...)
        return self.get_response(request)
    finally:
        # RTT 샘플 수집 (신규)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        try:
            from selfhealing.services.throttle.gradient import (
                get_gradient_calculator,
            )
            get_gradient_calculator("admission_control").add_sample(elapsed_ms)
        except ImportError:
            pass
```

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
[요청 처리 완료]
    ↓
AdmissionControlMiddleware.finally:
    elapsed_ms = (perf_counter - start) × 1000
    ↓
GradientCalculator("admission_control").add_sample(elapsed_ms)
    ↓
smoothed_rtt = α × elapsed + (1-α) × smoothed_rtt   (EMA)
    ↓
[다음 요청의 Fast-Fail 판정에 사용]
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
│   └── test_reset_clears_all              # reset_gradient_calculators()

tests/unit/scaling/test_deadline_fast_fail.py
├── TestDynamicFastFail
│   ├── test_estimated_with_rtt_data       # smoothed_rtt=200, margin=1.5 → 300ms
│   ├── test_estimated_no_data             # 데이터 없음 → None
│   ├── test_gradient_increases_margin     # gradient=0.5 → margin=1.5×1.5=2.25
│   └── test_should_fast_fail_integration  # remaining=400, estimated=500 → True

tests/unit/scaling/test_traffic_gate_deadline.py
├── TestTrafficGateDeadline
│   ├── test_expired_deadline_rejected     # deadline 만료 → allowed=False
│   ├── test_fast_fail_rejected            # estimated > remaining → allowed=False
│   ├── test_no_deadline_passthrough       # deadline 없음 → 기존 동작
│   └── test_import_error_passthrough      # ImportError → 기존 동작
```

### 6.2 통합 테스트

```
tests/integration/test_cascading_timeout.py
├── test_full_chain_fast_fail
│   # A → B → C 시뮬레이션
│   # C에서 Fast-Fail → 전체 체인 조기 종료 확인
├── test_deadline_propagation_accuracy
│   # 전파된 deadline이 실제 경과 시간만큼 감소했는지 확인
└── test_rtt_feedback_loop
    # 응답 시간 증가 → GradientCalculator → Fast-Fail 임계치 자동 조정
```

---

## 7. 설정

### 7.1 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SELFHEALING_DEADLINE_FAST_FAIL_ENABLED` | `true` | Dynamic Fast-Fail 활성화 |
| `SELFHEALING_DEADLINE_SAFETY_MARGIN` | `1.5` | 예상 처리시간 안전 계수 |
| `SELFHEALING_DEADLINE_RTT_SMOOTHING_FACTOR` | `0.5` | GradientCalculator EMA 가중치 |

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
| GradientCalculator 데이터 없음 | `get_estimated_processing_ms()` → `None` → Fast-Fail 건너뜀 |
| `ImportError` (모듈 미설치) | `try/except ImportError: pass` → 기존 동작 |
| Deadline 헤더 없음 | `is_expired()` → `False`, `should_fast_fail()` → `False` → 기존 동작 |
| RTT 수집 실패 | `finally` 블록 내 `try/except` → 요청 처리에 영향 없음 |
| Safety margin 과대 | 더 많이 거절 → 자원 낭비 방지 (안전 방향) |

모든 신규 코드는 `try/except` 또는 `None` 체크로 보호되어 기존 시스템을 전혀 깨뜨리지 않습니다.
