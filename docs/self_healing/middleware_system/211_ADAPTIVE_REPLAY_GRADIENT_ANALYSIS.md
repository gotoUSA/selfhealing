# 211. Adaptive Replay ↔ AdaptiveThrottle Gradient 공유 분석

> **상태**: ❌ 비추천 (현재 DLQ 연동으로 충분)
> **목적**: AdaptiveReplayManager와 AdaptiveThrottle이 "유사한 Gradient 알고리즘"을 사용한다는 근거를 코드에서 검증하고, 공통화 가치를 판단한다.
> **근거 문서**: [207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md](207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md)

---

## 1. 현재 코드 구조

### 1-1. AdaptiveReplayManager — 배치 크기 조정 알고리즘

**파일**: `services/adaptive_replay.py` (320줄)

```python
# adaptive_replay.py 문서화 (L4-12)
"""
Similar to Netflix Gradient algorithm for rate limiting,
but applied to DLQ replay batch sizing.

Algorithm:
- If failure rate >= threshold (20%): reduce batch size by 20%
- If 3 consecutive perfect batches: increase batch size by 5
- Bounded by min_items and max_items
"""
```

**핵심 조정 로직** (L186-222):

```python
def _adjust_max_items(self, failure_rate: float, failures: int) -> None:
    if failure_rate >= self._config.failure_threshold:       # ≥ 0.2
        new_items = int(self._current_items * self._config.decrease_ratio)  # × 0.8
        self._current_items = max(self._config.min_items, new_items)
        self._success_streak = 0
    elif failures == 0:
        self._success_streak += 1
        if self._success_streak >= self._config.success_streak_required:  # ≥ 3
            new_items = self._current_items + self._config.increase_step   # + 5
            self._current_items = min(self._config.max_items, new_items)
            self._success_streak = 0
```

### 1-2. AdaptiveThrottle — RTT Gradient 알고리즘

**파일**: `services/throttle/adaptive.py` (2287줄)

```python
# adaptive.py L6-10 (docstring)
"""
Algorithm:
1. Sample RTT every 500ms (configurable)
2. Calculate RTT gradient (positive = slowing down, negative = speeding up)
3. Adjust limit:
   - If gradient > 0 (RTT increasing): limit = limit × 0.9
   - If gradient <= 0 (RTT stable/decreasing): limit = limit + 1
"""
```

**핵심 조정 로직** (GradientCalculator L396-470):

```python
class GradientCalculator:
    """RTT gradient 계산기 — EMA 기반 연속 gradient."""

    # RTT EMA 기반으로 매 sample_interval_ms마다 gradient 계산
    # gradient > 0: RTT 증가 중 → limit × 0.9 (연속적 감소)
    # gradient ≤ 0: RTT 안정/감소 → limit + 1 (점진적 증가)
```

### 1-3. 이미 존재하는 DLQ 연동 — ThrottleDLQReplayMixin

**파일**: `services/throttle/adaptive_dlq_replay.py` (452줄)

```python
# adaptive.py L502
class AdaptiveThrottle(ThrottleDLQReplayMixin, SlidingWindowThrottle):
    """AdaptiveThrottle은 이미 ThrottleDLQReplayMixin을 상속"""
```

```python
# adaptive_dlq_replay.py L83-87
class ThrottleDLQReplayMixin:
    """
    AdaptiveThrottle에 DLQ 거부 저장 및 Recovery Replay 기능을 추가하는 Mixin.
    """
```

- Throttle 거부 → `store_throttle_rejection_to_dlq()` → DLQ 저장 (tier_id 기반 샘플링)
- Recovery 이벤트 → `_auto_replay_on_recovery` → DLQ 항목 자동 재시도
- Adaptive Pacing: `capacity_ratio` 기반 간격/배치 동적 조정

---

## 2. 알고리즘 비교표

| 속성 | AdaptiveThrottle | AdaptiveReplayManager |
|------|------------------|-----------------------|
| **입력 데이터** | RTT (응답 시간 밀리초) | Batch 성공/실패 카운트 |
| **입력 유형** | 연속값 (continuous) | 이산값 (discrete ratio) |
| **gradient 계산** | EMA 기반 연속 미분 (GradientCalculator) | 단순 임계값 비교 (`failure_rate >= 0.2`) |
| **감소 방식** | `limit × 0.9` (매 interval) | `items × 0.8` (배치마다) |
| **증가 방식** | `limit + 1` (매 interval) | `items + 5` (3연속 성공 후) |
| **증가 조건** | `gradient ≤ 0` (RTT 안정/감소) | `failures == 0` 연속 3회 |
| **조정 주기** | `sample_interval_ms` (500ms 기본) | 배치 완료 단위 (이벤트 기반) |
| **추가 로직** | 429 감지, Emergency Cap, Error Budget, Recovery Dampening, Conservative Limit (Min-Winner) | 없음 (단순 batch sizing) |
| **범위** | Rate Limit (RPS) | Batch Size (items) |

---

## 3. 공통 추상화 가능성 — 코드 분석

### 3-1. 잠재적 공통 인터페이스

```python
class AdaptiveController(Protocol):
    """가상의 공통 adaptive 인터페이스"""

    def get_current_value(self) -> int: ...        # limit or items
    def record_feedback(self, metric: float): ...   # RTT or failure_rate
    def adjust(self) -> None: ...                   # 조정 실행
```

### 3-2. 공통화가 어려운 이유 (코드 근거)

**1) AdaptiveThrottle의 복잡도**

AdaptiveThrottle의 limit 조정에 관여하는 요소:

| 요소 | 코드 위치 | AdaptiveReplay 해당? |
|------|-----------|---------------------|
| RTT EMA Gradient | GradientCalculator L396 | ❌ |
| 429 HTTP 감지 | `_handle_429_reduction()` L1297 | ❌ |
| Emergency Level Cap | `_apply_emergency_cap()` L1724 | ❌ |
| Error Budget 감소 | `_handle_error_budget_event()` L760 | ❌ |
| Recovery Dampening | `RECOVERY_DAMPENING_MULTIPLIERS = (0.8, 0.9, 1.0)` L1946 | ❌ |
| Conservative Limit (Min-Winner) | `conservative_limit` L1095 | ❌ |
| LEVEL_3 Gradient Freeze | `_gradient_frozen` L1176 | ❌ |
| Full Stop | `_full_stop_active` L1856 | ❌ |

→ AdaptiveThrottle의 조정 로직 중 AdaptiveReplay와 공통인 부분은 "감소율 곱셈" 패턴 한 줄뿐.

**2) AdaptiveReplayManager의 단순함**

- 전체 조정 로직이 `_adjust_max_items()` 36줄 (L186-222)
- 추가 로직 없음: Emergency, Error Budget, Recovery 모두 불필요
- 공통 추상화 시 불필요한 인터페이스 강제됨

**3) docstring의 "Similar" 표현 검증**

```python
# adaptive_replay.py L4-5
"Similar to Netflix Gradient algorithm for rate limiting,
but applied to DLQ replay batch sizing."
```

유사점:
- ✅ 양쪽 모두 "지표 악화 → 값 감소, 개선 → 값 증가" 패턴
- ✅ 감소 시 비율 곱셈 (0.9 vs 0.8)

차이점:
- ❌ Gradient 계산 방식 (EMA 미분 vs 단순 임계값)
- ❌ 입력 데이터 도메인 (RTT continuous vs batch discrete)
- ❌ 조정 주기 (시간 기반 vs 이벤트 기반)
- ❌ 부가 안전장치 (8가지 vs 0가지)

---

## 4. 연동 장단점

### 4-1. 공통화 시 장점

| 장점 | 크기 |
|------|------|
| `decrease_ratio` 곱셈 코드 1줄 재사용 | 미미 |
| 알고리즘 네이밍 일관성 ("Gradient" 패턴) | 문서 수준 |

### 4-2. 공통화 시 단점

| 단점 | 코드 근거 |
|------|-----------|
| 불필요한 추상화 레이어 추가 | AdaptiveReplay의 전체 조정 로직은 36줄 — 공통 인터페이스가 더 복잡 |
| AdaptiveThrottle 수정 범위 광범위 | 8가지 부가 로직이 공통 인터페이스에 맞지 않음 |
| 독립 배포 불가 | 공통 모듈 변경 시 양쪽 모두 테스트 필요 |
| 도메인 오염 | "batch_size"와 "rate_limit"의 의미적 차이를 인터페이스가 숨김 |

### 4-3. 현행 유지(DLQ Mixin) 시 장점

| 장점 | 코드 근거 |
|------|-----------|
| 이미 DLQ 연동 완료 | `ThrottleDLQReplayMixin` (adaptive_dlq_replay.py) 452줄 — 완성된 구현체 |
| 역할 분리 명확 | AdaptiveThrottle: RPS 제한, AdaptiveReplay: DLQ 배치 크기 |
| 각자 독립 진화 | AdaptiveThrottle에 새 안전장치 추가해도 Replay에 영향 없음 |
| Adaptive Pacing 이미 존재 | ThrottleDLQReplayMixin이 `capacity_ratio` 기반 replay 간격 조정 수행 |

### 4-4. 현행 유지 시 단점

| 단점 | 크기 |
|------|------|
| `decrease_ratio` 값 (0.8 vs 0.9)이 독립 관리 | 미미 — 도메인별 최적값이 다를 수 있음 |
| "Gradient" docstring이 혼란 유발 가능 | 문서 수준 — docstring 수정으로 해결 |

---

## 5. 결론

### 비추천 근거 요약

1. **DLQ 연동은 이미 완료**: `ThrottleDLQReplayMixin`이 Throttle 거부 → DLQ 저장 → Recovery Replay를 모두 처리 (adaptive_dlq_replay.py 452줄)
2. **알고리즘 유사도 낮음**: docstring에선 "Similar"이나, 실제 코드는 EMA Gradient vs 단순 임계값으로 근본적으로 다름
3. **추상화 비용 > 이익**: 공통화할 코드 1줄(비율 곱셈), 추상화에 필요한 인터페이스 + 리팩토링 범위 광범위
4. **AdaptiveReplayManager의 36줄 조정 로직을 위해 AdaptiveThrottle의 2287줄을 리팩토링하는 것은 비합리적**

### 권장 액션

| 액션 | 유형 | 내용 |
|------|------|------|
| docstring 정확성 개선 | 문서 수정 | `adaptive_replay.py` L4-5의 "Similar to Netflix Gradient"→ "Inspired by adaptive pacing patterns"으로 변경 |
| DLQ Mixin 유지 | 현행 유지 | ThrottleDLQReplayMixin을 통한 Throttle↔DLQ↔Replay 간접 연동으로 충분 |
| 공통 Gradient 추출 | 하지 않음 | ROI 부족 |

---

## 6. 검증 항목

- [x] AdaptiveReplayManager와 AdaptiveThrottle 간 import 관계 없음 확인 (grep_search 결과)
- [x] ThrottleDLQReplayMixin이 AdaptiveThrottle에 상속되어 DLQ 저장/Replay 처리 확인
- [x] AdaptiveReplayManager의 조정 로직 전체 36줄 (L186-222) — 공통화 대상 미미
- [x] GradientCalculator (adaptive.py L396-470)의 EMA 방식이 batch failure_rate 임계값과 구조적으로 상이
