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

### 3-1. 우선순위 매핑 테이블 ✅ 구현 완료

> **상태**: 구현 완료 — `services/throttle/tier_mapping.py`
> **208 원안 명칭**: `priority_mapping.py` → **`tier_mapping.py`로 확정** (207 문서 근거)

**파일**: `services/throttle/tier_mapping.py`

```python
# tier_mapping.py (실제 구현 코드)

CRITICALITY_TO_TIER: dict[str, str] = {
    "critical": "critical",
    "high": "critical",     # models.py valid_levels에 포함
    "medium": "standard",
    "low": "non_essential",
}

TIER_TO_CRITICALITY: dict[str, str] = {
    "critical": "critical",
    "standard": "medium",
    "non_essential": "low",
}

VALID_TIER_IDS: set[str] = {"critical", "standard", "non_essential"}

def get_tier_from_criticality(criticality: str) -> str:
    """criticality → tier_id 변환 (대소문자 무시, 미지 입력 시 'standard' 폴백 + 경고 로그)"""

def get_criticality_from_tier(tier_id: str) -> str:
    """tier_id → criticality 변환 (대소문자 무시, 미지 입력 시 'medium' 폴백 + 경고 로그)"""
```

**명칭 변경 근거** (207 문서 L424):
- `priority_mapping.py`는 `TierDefinition.priority`, `TierMapping.priority` 등 기존 priority 속성과 혼동 우려
- `services/throttle/registry.py`가 이미 존재하여 "priority" + "registry" 명칭 충돌 가능
- `tier_mapping`이 "tier ↔ criticality 변환"이라는 역할을 정확히 반영

**현재 호출부 상태**: 테스트 코드(`test_tier_mapping.py`)에서만 import. **프로덕션 코드에서의 호출부 연결은 3-6에서 다룸.**

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

### 3-6. Service ID 식별 — ThrottleRegistry 활용

> **리뷰 지적**: `AdaptiveThrottle.check(context={"service_id": ...})`의 `service_id`를 "누가 넣어주는가?"가 비어 있다.

#### 3-6-1. 현재 코드의 두 가지 사용 경로

| 경로 | 코드 | `service_id` 식별 방식 |
|------|------|------------------------|
| **ThrottleRegistry** (서비스별 인스턴스) | `registry.get_throttle("order-api").check(key)` | **인스턴스 자체가 서비스에 바인딩** — `context` 불필요 |
| **글로벌 싱글톤** | `get_adaptive_throttle().check(key, context={...})` | **caller가 `context={"service_id": ...}` 주입 필수** |

**코드 근거 — ThrottleRegistry 경로**:

```python
# registry.py L121-137
def get_throttle(self, service_name: str) -> AdaptiveThrottle:
    """서비스별 Throttle 인스턴스 가져오기 (없으면 생성)."""
    with self._throttle_lock:
        if service_name not in self._throttles:
            self._create_throttle(service_name)
        return self._throttles[service_name].throttle

# registry.py 사용 예시 (docstring L7-14)
# registry = get_throttle_registry()
# throttle = registry.get_throttle("payment_api")
# result = throttle.check("user_123")
```

각 인스턴스는 생성 시 `self._service_name`이 설정됨 (adaptive.py L526):

```python
self._service_name: str = sanitize_label_value(self.config.service_name)
```

**코드 근거 — 현재 Shedding check 로직** (adaptive.py L1543-1550):

```python
service_id = context.get("service_id") if context else None
if service_id and self._shedding_affected_services and service_id in self._shedding_affected_services:
    original_limit = self._current_limit
    self._current_limit = min(self._current_limit, self._shedding_suggested_limit)
    result = super().check(key)
    self._current_limit = original_limit
```

이 코드는 **글로벌 싱글톤 사용 시** `context`에 의존한다. ThrottleRegistry 경로에서는 `self._service_name`을 사용하도록 확장해야 한다.

#### 3-6-2. 구현 방안 — `self._service_name` 기반 Shedding 매칭 확장

```python
# adaptive.py check()의 shedding 분기 수정안

# ThrottleRegistry 경로: self._service_name으로 식별
effective_service_id = (
    (context.get("service_id") if context else None)
    or self._service_name
)
if (
    effective_service_id
    and self._shedding_affected_services
    and effective_service_id in self._shedding_affected_services
):
    original_limit = self._current_limit
    self._current_limit = min(self._current_limit, self._shedding_suggested_limit)
    result = super().check(key)
    self._current_limit = original_limit
```

**변경 포인트**: `context.get("service_id")` fallback으로 `self._service_name` 사용.

#### 3-6-3. 호출부 가이드

**ThrottleRegistry 경로 (권장)**:

```python
# 서비스별 인스턴스 사용 — context 주입 불필요
from selfhealing.services.throttle.registry import get_throttle_registry

registry = get_throttle_registry()
throttle = registry.get_throttle("order-api")  # 이미 서비스에 바인딩됨
result = throttle.check(key=client_ip, tier_id="standard")
```

**글로벌 싱글톤 경로 (기존 호환)**:

```python
# 글로벌 싱글톤 사용 — context에 service_id 명시
from selfhealing.services.throttle.adaptive import get_adaptive_throttle

throttle = get_adaptive_throttle()
result = throttle.check(
    key=client_ip,
    tier_id="standard",
    context={"service_id": "order-api"},  # [필수] 누락 시 shedding 연동 안 됨
)
```

#### 3-6-4. HybridRateLimitMiddleware와의 관계

`HybridRateLimitMiddleware`는 **Self-Healing Control API 전용** (rate_limit.py L575)이며, `AdaptiveThrottle`을 호출하지 않는다:

```python
# rate_limit.py L575
if not request.path.startswith(control_api_prefix):
    return self.get_response(request)
```

따라서 이 미들웨어에 `service_id` 주입 로직을 추가할 필요는 없다. 서비스별 Throttle 연동은 `ThrottleRegistry` 경로를 통해 이루어진다.

### 3-7. Tier ID 일관성 보장 — Cross-Layer Validation

> **문제**: `VALID_TIER_IDS` (tier_mapping.py)와 `DEFAULT_TIER_DEFINITIONS` (tiering/defaults.py)가 동일한 ID 집합(`"critical"`, `"standard"`, `"non_essential"`)을 사용하지만, 이 일관성을 코드로 보장하는 메커니즘이 없다.

#### 3-7-1. 방안 비교

| 방안 | 구현 방식 | 레이어 의존성 | 기존 코드 변경 | 확장성 |
|------|----------|--------------|--------------|--------|
| **(A) 동적 추출** | `tier_mapping.py`에서 `DEFAULT_TIER_DEFINITIONS` import → ID 추출 | **services → api 역방향 의존 발생** | tier_mapping.py 수정 | tier 추가 시 자동 반영 |
| **(B) 공유 상수 통합** | 중립 위치에 ID 상수 모듈 신설 → 양쪽 import | 중립 모듈 필요 | 양쪽 import 변경 | tier 추가 시 1곳 수정 |
| **(C) Cross-Layer Validation Test** | 테스트에서 양쪽 값 비교 → 불일치 시 실패 | **없음** (테스트만) | **없음** (테스트 추가만) | tier 추가 시 테스트 실패로 감지 |

#### 3-7-2. 선택: **(C) Cross-Layer Validation Test**

**선택 이유**:

1. **레이어 분리 유지**: `tier_mapping.py`(서비스 레이어)가 `api.django.tiering`(Django API 레이어)에 의존하지 않음. 방안 A는 서비스→API 역방향 의존을 생성하여 레이어 아키텍처를 위반.
2. **기존 코드 변경 없음**: 프로덕션 코드(`tier_mapping.py`, `defaults.py`) 수정 불필요. 방안 B는 중립 모듈 신설 + 양쪽 import 경로 변경 필요.
3. **기존 테스트 패턴과 일관**: `test_tier_mapping.py`에 이미 동일 구조의 cross-reference 검증이 존재:
   - `TestCriticalityToTierMapping.test_covers_all_service_config_valid_levels()` — `ServiceConfig.valid_levels`와 `CRITICALITY_TO_TIER` 키 비교
   - `TestValidTierIds.test_matches_tier_to_criticality_keys()` — `VALID_TIER_IDS`와 `TIER_TO_CRITICALITY` 키 비교
4. **확장성**: 새로운 tier가 한쪽에만 추가되면 테스트가 즉시 실패하여 불일치를 CI에서 감지.

#### 3-7-3. 구현 — Cross-Layer Validation Test

**파일**: `tests/unit/throttle/test_tier_mapping.py` (기존 파일에 추가)

```python
class TestCrossLayerTierConsistency:
    """
    tier_mapping.VALID_TIER_IDS ↔ tiering.DEFAULT_TIER_DEFINITIONS 일관성 검증.

    서비스 레이어(tier_mapping.py)와 Django API 레이어(tiering/defaults.py)가
    동일한 tier ID 집합을 사용하는지 보장한다.
    """

    @classmethod
    def setup_class(cls):
        """Django API 레이어 import를 위한 최소 Django 설정."""
        import os

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings.test")

        import django

        django.setup()

        # pytest-django _dj_autoclear_mailbox fixture가 mail.outbox를 참조
        from django.core import mail

        if not hasattr(mail, "outbox"):
            mail.outbox = []

    def test_valid_tier_ids_matches_default_tier_definitions(self):
        """
        VALID_TIER_IDS == {td.id for td in DEFAULT_TIER_DEFINITIONS}.

        한쪽에 tier가 추가/삭제되면 이 테스트가 실패하여 불일치를 감지.
        """
        from selfhealing.api.django.tiering.defaults import DEFAULT_TIER_DEFINITIONS

        definition_ids = {td.id for td in DEFAULT_TIER_DEFINITIONS}
        assert VALID_TIER_IDS == definition_ids, (
            f"tier_mapping.VALID_TIER_IDS {VALID_TIER_IDS} != "
            f"DEFAULT_TIER_DEFINITIONS IDs {definition_ids}. "
            f"Missing in tier_mapping: {definition_ids - VALID_TIER_IDS}, "
            f"Extra in tier_mapping: {VALID_TIER_IDS - definition_ids}"
        )

    def test_criticality_to_tier_values_match_definitions(self):
        """
        CRITICALITY_TO_TIER의 모든 target tier_id가 DEFAULT_TIER_DEFINITIONS에 정의됨.
        """
        from selfhealing.api.django.tiering.defaults import DEFAULT_TIER_DEFINITIONS
        from selfhealing.services.throttle.tier_mapping import CRITICALITY_TO_TIER

        definition_ids = {td.id for td in DEFAULT_TIER_DEFINITIONS}
        for criticality, tier_id in CRITICALITY_TO_TIER.items():
            assert tier_id in definition_ids, (
                f"CRITICALITY_TO_TIER['{criticality}'] = '{tier_id}' "
                f"is not defined in DEFAULT_TIER_DEFINITIONS"
            )
```

**기존 테스트와의 관계**:

| 기존 테스트 | 검증 대상 | 범위 |
|------------|----------|------|
| `test_covers_all_service_config_valid_levels` | `CRITICALITY_TO_TIER` keys ↔ `ServiceConfig.valid_levels` | models 레이어 |
| `test_matches_tier_to_criticality_keys` | `VALID_TIER_IDS` ↔ `TIER_TO_CRITICALITY` keys | 서비스 레이어 내부 |
| **`test_valid_tier_ids_matches_default_tier_definitions`** (신규) | `VALID_TIER_IDS` ↔ `DEFAULT_TIER_DEFINITIONS` IDs | **서비스 ↔ Django API 레이어 간** |

---

## 4. 영향 범위

| 파일 | 변경 내용 | 상태 |
|------|-----------|------|
| `services/throttle/adaptive.py` | `_subscribe_load_shedding_events()`, `_handle_shedding_changed()` 추가 | ✅ 구현됨 |
| `services/throttle/adaptive.py` | `check()` shedding 분기에 `self._service_name` fallback 추가 (3-6-2) | ✅ 구현됨 |
| `services/circuit_breaker/load_shedding/manager.py` | `update_shedding_state()`에 EventBus 발행 (`_publish_shedding_event`) 추가 | ✅ 구현됨 |
| `services/event_bus/bus.py` | `EventType.LOAD_SHEDDING_LEVEL_CHANGED` 추가 | ✅ 구현됨 |
| ~~(신규) `services/throttle/priority_mapping.py`~~ | ~~tier ↔ criticality 매핑 테이블~~ | ❌ 명칭 변경 |
| `services/throttle/tier_mapping.py` | tier ↔ criticality 매핑 유틸리티 | ✅ 구현됨 |
| `tests/unit/throttle/test_tier_mapping.py` | `TestCrossLayerTierConsistency` 클래스 추가 (3-7-3) | ✅ 구현됨 |

---

## 5. 검증 항목

- [x] Load Shedding LEVEL_2 활성화 시 Throttle limit이 자동 감소하는지
- [x] Shedding 해제 시 Recovery Dampening으로 점진 복구하는지
- [x] `critical` tier 요청이 Shedding 시에도 보호되는지
- [x] EventBus 미가용 시 Fail-Open으로 기존 동작 유지하는지
- [x] Emergency Mode + Shedding 동시 활성 시 Conservative Limit (Min-Winner) 정상 동작
- [x] `ThrottleRegistry.get_throttle(service).check()` 시 `self._service_name` 기반 shedding 매칭 동작
- [x] 글로벌 싱글톤 `get_adaptive_throttle().check(context={"service_id": ...})` 시 기존 context 기반 매칭 유지
- [x] `VALID_TIER_IDS` ↔ `DEFAULT_TIER_DEFINITIONS` ID 집합 일치 (Cross-Layer Validation Test)
