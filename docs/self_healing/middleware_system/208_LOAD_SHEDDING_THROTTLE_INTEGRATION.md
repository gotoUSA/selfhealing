# 208. Load Shedding ↔ AdaptiveThrottle 연동 계획

> **상태**: ✅ 연동 추천
> **목적**: 독립 동작 중인 Load Shedding과 AdaptiveThrottle의 트래픽 우선순위 체계를 통합한다.
> **근거 문서**: [207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md](207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md)

---

## 1. 현재 코드 구조

### 1-1. Load Shedding 측

**파일**: `services/circuit_breaker/load_shedding/manager.py` (489줄)

```python
# manager.py L210-241
def evaluate_shedding(self, service_id: str) -> float:
    """허용 트래픽 비율 계산 (0.0 ~ 100.0)"""
    if service_config.criticality == "critical":
        return 100.0  # critical은 항상 100%

    critical_error_rate = self.get_critical_services_error_rate()
    applicable_level = self._find_applicable_level(
        critical_error_rate, service_config.criticality,
    )
    return max(applicable_level.traffic_limit, service_config.min_traffic_percentage)
```

- **입력**: `service_id`, `criticality` (critical/low/medium)
- **출력**: 허용 트래픽 비율 (float, 0~100)
- **판단 기준**: critical 서비스 평균 에러율 → `SheddingLevel` 매칭
- **레벨 체계**: `SheddingState.LEVEL_1/2/3` (shedding_models.py L14-18)
  - LEVEL_1: low 50% 제한
  - LEVEL_2: low+medium 80% 제한
  - LEVEL_3: low+medium 완전 차단

**파일**: `services/circuit_breaker/load_shedding/shedding_middleware.py`

```python
# shedding_middleware.py L74-87
def process(self, service_id: str) -> SheddingDecision:
    """요청에 대한 Shedding 결정"""
    decision = self.manager.should_allow_request(service_id)
    return decision
```

- `LoadSheddingMiddleware.process()` → `SheddingDecision.allow_request` (bool)
- 확률적 차단: `random.random() * 100 < allowed_percent` (manager.py L270)

### 1-2. AdaptiveThrottle 측

**파일**: `services/throttle/adaptive.py` (2287줄)

```python
# adaptive.py L1434-1498
def check(
    self,
    key: str,
    tier_id: str = "standard",
    context: dict | None = None,
    store_rejection: bool = True,
) -> ThrottleResult:
    """Check if request is allowed with adaptive info and priority protection."""
    # Error Budget Critical 상태에서 non_essential 티어 거부
    if self._error_budget_limit_reduction_active:
        if tier_id == "non_essential" and self._error_budget_multiplier <= 0.5:
            ...  # 즉시 거부

    # 429 감소 상태에서 CRITICAL 티어 보호
    if self._429_reduction_active and tier_id in PROTECTED_TIERS_ON_429:
        # CRITICAL 요청은 429 감소 전 limit 기준으로 검사
        ...
```

- **입력**: `key`, `tier_id` (critical/standard/non_essential)
- **출력**: `ThrottleResult.allowed` (bool)
- **판단 기준**: 슬라이딩 윈도우 카운트 vs `current_limit`
- **우선순위**: `PROTECTED_TIERS_ON_429 = {"critical"}` (adaptive.py L498)

---

## 2. 문제점 (코드 근거)

### 2-1. 이중 트래픽 제한

```
요청 → LoadSheddingMiddleware.process() → 50% 확률 차단
         ↓ (통과)
     → AdaptiveThrottle.check() → limit 초과 차단
```

- Load Shedding이 50% 차단 + Throttle이 limit 초과로 추가 차단 → 실제 허용률 < 50%
- 두 시스템이 **독립적으로 차단 결정** → 누적 효과 예측 불가

### 2-2. 우선순위 체계 불일치

| 구분 | Load Shedding | AdaptiveThrottle |
|------|--------------|------------------|
| 분류 기준 | `criticality`: critical / low / medium | `tier_id`: critical / standard / non_essential |
| 보호 대상 | `critical` → 항상 100% 허용 (manager.py L218) | `critical` → 429 감소 전 limit 적용 (adaptive.py L1496) |
| 차단 대상 | `low` → 먼저 차단 | `non_essential` → Error Budget Critical 시 즉시 차단 |
| 중간 레벨 | `medium` | `standard` |
| **매핑** | **없음** | **없음** |

- `low`와 `non_essential`이 같은 의미인지, `medium`과 `standard`가 같은 의미인지 정의 없음
- 동일 서비스가 Load Shedding에서는 `criticality=medium`이고 Throttle에서는 `tier_id=standard`인 경우 → 각 시스템이 다른 기준으로 판단

### 2-3. 상태 불공유

- Load Shedding의 현재 shedding 레벨을 Throttle이 모름
- Throttle의 Emergency 상태/Recovery Dampening을 Load Shedding이 모름
- `LoadSheddingManager.is_shedding_active()` (manager.py L398) 결과가 Throttle limit에 미반영

---

## 3. 연동 설계

### 3-1. 우선순위 매핑 테이블

```python
# 제안: services/throttle/priority_mapping.py

CRITICALITY_TO_TIER: dict[str, str] = {
    "critical": "critical",
    "high": "critical",        # 확장 대비
    "medium": "standard",
    "low": "non_essential",
}

TIER_TO_CRITICALITY: dict[str, str] = {
    "critical": "critical",
    "standard": "medium",
    "non_essential": "low",
}
```

**근거**: Load Shedding은 `critical`을 항상 100% 허용 (manager.py L218), Throttle은 `critical`을 429 보호 (adaptive.py L1496) → 양쪽에서 `critical`은 최우선 보호 대상으로 동일.

### 3-2. Shedding 상태 → Throttle limit 반영

```python
# 제안: AdaptiveThrottle에 추가

def apply_shedding_factor(self, shedding_percent: float) -> None:
    """
    Load Shedding 허용 비율을 limit에 반영.

    Args:
        shedding_percent: 허용 트래픽 비율 (0~100)
    """
    # 기존 Emergency cap과 동일 패턴 활용
    # adaptive.py L1724 _apply_emergency_cap() 참조
    factor = shedding_percent / 100.0
    new_limit = int(self._base_limit_before_emergency * factor)
    self.current_limit = max(new_limit, self.config.min_limit)
```

**반영 지점**: `check_and_sync_emergency_state()` (adaptive.py L1877)에서 Emergency sync와 함께 Shedding 상태도 sync

### 3-3. 이중 차단 방지

```
요청 → LoadSheddingMiddleware.process() → Shedding 결정
         ↓
     → AdaptiveThrottle.check(tier_id=...) → Shedding 상태 반영된 limit으로 판단
         ↓
     이중 차단 아닌 통합 차단
```

**방안 A — Shedding을 Throttle limit에 통합**:

- Load Shedding 활성화 시 → Throttle limit 자동 감소
- Load Shedding Middleware는 `allow_request=True` 고정 (Throttle에 위임)
- 장점: 단일 차단 지점
- 단점: Load Shedding Middleware 역할 축소

**방안 B — Shedding 전달만 (EventBus 활용)**:

- Load Shedding 레벨 변경 시 EventBus로 이벤트 발행
- AdaptiveThrottle이 구독하여 limit에 반영
- Load Shedding Middleware는 기존 역할 유지
- 장점: 기존 코드 변경 최소
- 단점: 여전히 이중 차단 가능 (줄어들지만 완전 제거 아님)

**추천**: **방안 B** — EventBus 기반 Shedding 상태 전파. 기존 Emergency EventBus 패턴 (`_subscribe_rate_limit_events()` L603)과 동일.

### 3-4. EventBus 이벤트 정의

```python
# 제안: EventType에 추가
LOAD_SHEDDING_LEVEL_CHANGED = "load_shedding_level_changed"

# 이벤트 데이터
{
    "previous_level": -1,
    "new_level": 0,
    "critical_error_rate": 45.0,
    "affected_services": ["review-api", "search-api"],
    "traffic_limit": 50.0,  # 허용 비율
}
```

**근거**: `LoadSheddingManager.update_shedding_state()` (manager.py L338)가 이미 레벨 변경 감지 + `SheddingAuditEntry` 생성. EventBus 발행을 추가하면 됨.

### 3-5. AdaptiveThrottle 구독 핸들러

```python
# 제안: AdaptiveThrottle에 추가

def _subscribe_load_shedding_events(self) -> None:
    """Load Shedding 이벤트 구독 (Fail-Open)."""
    try:
        from selfhealing.services.event_bus import EventType, get_event_bus
        bus = get_event_bus()
        bus.subscribe(EventType.LOAD_SHEDDING_LEVEL_CHANGED, self._handle_shedding_changed)
    except ImportError:
        logger.debug("[AdaptiveThrottle] EventBus not available for shedding")

def _handle_shedding_changed(self, event) -> None:
    """Shedding 레벨 변경 시 limit 조정."""
    event_data = event.data if hasattr(event, "data") else event
    traffic_limit = event_data.get("traffic_limit", 100.0)

    # Conservative Limit에 shedding factor 반영
    # 기존 Min-Winner 정책 (adaptive.py L1095-1115) 확장
    self._shedding_suggested_limit = int(
        self.config.max_limit * traffic_limit / 100.0
    )
```

**근거**: 기존 `_subscribe_rate_limit_events()` (adaptive.py L603)와 동일 패턴.

---

## 4. 영향 범위

| 파일 | 변경 내용 |
|------|-----------|
| `services/throttle/adaptive.py` | `_subscribe_load_shedding_events()`, `_handle_shedding_changed()` 추가 |
| `services/circuit_breaker/load_shedding/manager.py` | `update_shedding_state()`에 EventBus 발행 추가 |
| `services/event_bus.py` | `LOAD_SHEDDING_LEVEL_CHANGED` EventType 추가 |
| (신규) `services/throttle/priority_mapping.py` | tier ↔ criticality 매핑 테이블 |

---

## 5. 검증 항목

- [ ] Load Shedding LEVEL_2 활성화 시 Throttle limit이 자동 감소하는지
- [ ] Shedding 해제 시 Recovery Dampening으로 점진 복구하는지
- [ ] `critical` tier 요청이 Shedding 시에도 보호되는지
- [ ] EventBus 미가용 시 Fail-Open으로 기존 동작 유지하는지
- [ ] Emergency Mode + Shedding 동시 활성 시 Conservative Limit (Min-Winner) 정상 동작
