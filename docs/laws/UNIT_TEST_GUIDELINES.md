# 단위 테스트 작성 가이드라인

> **적용 범위**: `packages/selfhealing-python/tests/` 및 전역 `tests/` 폴더
> **최종 수정일**: 2026-02-24

---

## 0. 계약 검증 vs 동작 검증

단위 테스트는 **검증 목적**에 따라 두 가지로 분류하며, 각각 다른 하드코딩 정책을 적용한다.

### 0.1 계약 검증 (Contract Test)

**설계 문서에 명시된 특정 값/구조가 구현에 반영되었는지** 확인하는 테스트.
기대값 자체가 설계 사양이므로 **하드코딩이 필수**이다.

```python
# ✅ 계약 검증: 하드코딩 필수
class TestSafetyBoundsSettingsSlaContract:
    """SLA 필드 설계 계약값 검증."""

    def test_sla_warning_contract_values(self):
        """SLA Warning 설계 계약값: min=50, max=2000, max_change=0.3."""
        settings = SafetyBoundsSettings()
        assert settings.throttle_sla_warning_ms_min == 50       # 설계 계약
        assert settings.throttle_sla_warning_ms_max == 2000     # 설계 계약
        assert settings.throttle_sla_warning_ms_max_change == 0.3  # 설계 계약
```

| 항목 | 설명 |
|------|------|
| **목적** | 설계 사양의 구체적 값이 코드에 올바르게 반영되었는지 확인 |
| **하드코딩** | **필수** — 기대값이 곧 설계 사양 |
| **실패 의미** | 설계 계약 위반 (의도적 변경인지 확인 필요) |
| **클래스 명명** | `Test*Contract` |
| **대상 예시** | 기본값, 상수, 매핑 테이블, 규칙 개수, 조정 계수, 상한/하한, 신뢰도 |

### 0.2 동작 검증 (Behavior Test)

**함수/메서드가 올바른 동작을 수행하는지** 확인하는 테스트.
기본값 변경에 불필요하게 깨지지 않도록 **소스 참조(§1.2 패턴)를 사용**한다.

```python
# ✅ 동작 검증: 소스 참조
class TestSafetyBoundsSlaParamBehavior:
    """SLA 파라미터 동작 검증."""

    def test_is_within_bounds_sla_warning_valid(self, default_settings):
        """유효한 SLA Warning 값은 범위 내로 판정되어야 한다."""
        safety = SafetyBounds()
        mid = (default_settings.throttle_sla_warning_ms_min
               + default_settings.throttle_sla_warning_ms_max) / 2
        assert safety.is_within_bounds("throttle_sla_warning_ms", mid) is True
```

| 항목 | 설명 |
|------|------|
| **목적** | 함수의 입출력/상태 변화가 올바른지 확인 |
| **하드코딩** | **방지** — §1.2 패턴(소스 상수, 설정 참조) 사용 |
| **실패 의미** | 동작 로직 버그 |
| **클래스 명명** | `Test*Behavior` |
| **대상 예시** | 함수 반환값, 라우팅, 경계값 판정, Atomic Swap, 상태 전환 |

### 0.3 구분 판단 기준

```
테스트가 검증하는 것이…
├─ "이 값이 정확히 X이다" → 계약 검증 (하드코딩)
│   예: assert settings.min == 50
│   예: assert len(RULES) == 3
│   예: assert coefficient == pytest.approx(1.15)
│
└─ "이 함수가 올바르게 동작한다" → 동작 검증 (소스 참조)
    예: assert result > current  (상향 조정)
    예: assert bound.min_value == settings.min  (전파 검증)
    예: assert safety.is_within_bounds(param, mid) is True
```

### 0.4 동어반복 방지

```python
# ❌ 나쁜 예: model_fields.default와 인스턴스 비교 (Pydantic 자체 검증 = 동어반복)
field = Settings.model_fields["min"]
assert settings.min == field.default   # Pydantic이 보장하는 것을 재검증

# ✅ 좋은 예: 설계 계약값을 하드코딩
assert settings.min == 50              # 설계 문서의 값을 직접 검증
```

## 1. 하드코딩 방지 원칙

### 1.1 문제점

```python
# ❌ 나쁜 예: 하드코딩
assert settings.service_name == "default"  # 기본값 바뀌면 테스트도 수정
assert len(result) == 128  # 상수가 변경되면 실패
```

소스 코드의 기본값/상수가 변경되면 테스트도 함께 수정해야 하는 **유지보수 비용** 발생.

### 1.2 해결 패턴

#### 패턴 1: 소스 상수 직접 참조

```python
# ✅ 좋은 예: 상수 import
from selfhealing.services.metrics.registry import (
    sanitize_label_value,
    DEFAULT_LABEL_MAX_LENGTH,
    UNKNOWN_LABEL_VALUE,
)

def test_truncates_at_max_length(self):
    long_value = "a" * (DEFAULT_LABEL_MAX_LENGTH + 100)
    result = sanitize_label_value(long_value)
    assert len(result) == DEFAULT_LABEL_MAX_LENGTH
```

#### 패턴 2: 소스 함수로 기대값 계산

```python
# ✅ 좋은 예: 동일 함수로 기대값 계산
from selfhealing.services.metrics.registry import sanitize_label_value

def test_service_name_sanitized(self):
    test_input = "test-api-service"
    config = ThrottleConfig(service_name=test_input)
    throttle = AdaptiveThrottle(config)

    expected = sanitize_label_value(test_input)  # 기대값도 함수로 계산
    assert throttle._service_name == expected
```

#### 패턴 3: 설정 기본값 참조

```python
# ✅ 좋은 예: 설정 객체에서 기본값 추출
from selfhealing.settings.throttle import ThrottleSettings

def test_service_name_default(self):
    settings = ThrottleSettings()
    throttle = AdaptiveThrottle(ThrottleConfig())

    assert throttle._service_name == settings.service_name  # 기본값 참조
```

#### 패턴 4: Pydantic Field 기본값 조회

```python
# ✅ 좋은 예: Pydantic model_fields에서 default 추출
def test_field_default(self):
    field_info = ThrottleSettings.model_fields.get("service_name")
    expected_default = field_info.default

    settings = ThrottleSettings()
    assert settings.service_name == expected_default
```

---

## 2. 하드코딩 허용 케이스

### 2.1 함수 동작 검증 (입력→출력 매핑)

```python
# ✅ 허용: 함수의 변환 규칙 자체를 테스트
def test_replaces_special_characters(self):
    assert sanitize_label_value("my-service.v2") == "my_service_v2"
    assert sanitize_label_value("payment/gateway") == "payment_gateway"
```

**이유**: 변환 규칙이 바뀌면 테스트도 바뀌어야 함 (의도된 동작)

### 2.2 타입/범위 검증

```python
# ✅ 허용: 타입과 범위만 검증
def test_get_snapshot_values(self):
    smoothed_rtt, gradient = calc.get_snapshot()
    assert smoothed_rtt > 0
    assert -1.0 < gradient < 1.0
```

### 2.3 메트릭/필드 존재 여부

```python
# ✅ 허용: 이름 자체가 계약
def test_throttle_requests_total_exists(self):
    assert hasattr(definitions, "throttle_requests_total")
    assert "service" in metric._labelnames
```

---

## 3. 상수 추출 규칙

### 3.1 소스 코드에 상수가 없을 경우

1. **소스 코드에 상수 추가** (권장)
2. 테스트에서 상수 import

```python
# 소스 코드 (registry.py)
UNKNOWN_LABEL_VALUE = "unknown"
DEFAULT_LABEL_MAX_LENGTH = 128

def sanitize_label_value(value: str, max_length: int = DEFAULT_LABEL_MAX_LENGTH) -> str:
    if not value:
        return UNKNOWN_LABEL_VALUE
    ...
```

### 3.2 네이밍 컨벤션

| 유형 | 네이밍 | 예시 |
|------|--------|------|
| 기본값 | `DEFAULT_*` | `DEFAULT_LABEL_MAX_LENGTH` |
| 폴백값 | `*_FALLBACK`, `UNKNOWN_*` | `UNKNOWN_LABEL_VALUE` |
| 경계값 | `MIN_*`, `MAX_*` | `MAX_CONCURRENT_LIMIT` |

### 3.3 테스트 함수 네이밍 컨벤션

테스트 함수명은 **무엇을 테스트하는지 읽기만 해도 알 수 있어야** 한다.

**포맷**: `test_<대상>_<상황/조건>_<기대결과>` — 자연어 서술형으로 작성.

```python
# ✅ 좋은 예: 읽기만 해도 목적이 명확
def test_resolve_cell_id_with_missing_keys_returns_default_cell(self): ...
def test_approval_timeout_below_minimum_raises_validation_error(self): ...
def test_concurrent_assign_does_not_corrupt_data(self): ...
def test_frozen_cell_info_prevents_attribute_mutation(self): ...

# ❌ 나쁜 예: 무엇을 테스트하는지 불명확
def test_resolve_cell_id_2(self): ...
def test_evaluate_error_case(self): ...
def test_it_works(self): ...
```

**규칙**:
- 번호 접미사 금지 (`test_foo_1`, `test_foo_2` 금지)
- `test_error_case`, `test_success` 같은 모호한 이름 금지
- docstring에도 **한 줄 요약**을 반드시 작성

---

## 4. 점진적 적용 전략

### 4.1 신규 테스트

- **즉시 적용**: 위 가이드라인 준수

### 4.2 기존 테스트

- **On-Failure Fix**: 소스 변경으로 테스트 실패 시 개선 패턴 적용
- **리팩토링 시**: 관련 테스트도 함께 개선

### 4.3 우선순위

1. 설정 기본값 (`*Settings`, `*Config`)
2. 상수 (`MAX_*`, `DEFAULT_*`)
3. 변환 함수 결과 (sanitize, normalize 등)

---

## 5. conftest.py 활용

### 5.1 배치 규칙 — "2파일 이상, 같은 디렉토리" 원칙

fixture는 **가장 가까운 소비자 곁**에 둔다. conftest.py에 올리는 기준은 **같은 디렉토리 안에서 2개 이상 테스트 파일이 사용하는지** 여부이다.

| 사용 범위 | 위치 | 예시 |
|-----------|------|------|
| 1개 파일 전용 | **테스트 파일 내부** | `test_swap_config.py` 안의 `default_config` |
| 같은 디렉토리 2+ 파일 | **해당 디렉토리 conftest.py** | `tests/unit/throttle/conftest.py` |
| 하위 디렉토리 전체 | **상위 conftest.py** | `tests/unit/conftest.py` |
| 전체 테스트 스위트 | **루트 conftest.py** | `tests/conftest.py` |

```
# ❌ 나쁜 예: 1개 파일에서만 쓰는 fixture를 conftest에 배치
# tests/unit/throttle/conftest.py
@pytest.fixture
def only_used_in_test_swap_config():   # → test_swap_config.py 안으로 이동
    ...

# ✅ 좋은 예: 해당 디렉토리 2+ 파일에서 공유
# tests/unit/throttle/conftest.py
@pytest.fixture
def mock_redis():   # test_cooldown.py, test_notification.py 모두 사용
    return MockRedisClient()
```

### 5.2 scope 선택 기준

| scope | 사용 조건 | 예시 |
|-------|----------|------|
| `function` (기본) | 가변 상태, 테스트 격리 필요 | `SafetyBoundsSettings()`, Mock 객체 |
| `class` | 같은 클래스 내 읽기 전용 공유 | `ThrottleConfig()` (frozen model) |
| `module` | 생성 비용 높고 불변인 객체 | DB 스키마, 대형 설정 파싱 |
| `session` | **극히 드물게** — 전체 세션에서 불변 | 환경변수 기반 설정, 외부 연결 정보 |

```python
# scope 선택 예시
@pytest.fixture                           # function (기본) — Mock은 테스트마다 격리
def mock_redis():
    return MockRedisClient()

@pytest.fixture(scope="class")            # class — frozen Pydantic model
def default_config():
    return ThrottleConfig()
```

> **주의**: `autouse=True`는 **싱글톤 리셋**, **환경변수 정리** 등 격리(isolation) 목적에만 사용한다. 데이터 주입 용도로 autouse를 쓰면 테스트 가독성이 떨어진다.

### 5.3 크기 제한 — 200줄 경고, 300줄 분리

conftest.py가 비대해지면 fixture 탐색이 어려워진다.

| 줄 수 | 조치 |
|-------|------|
| ≤ 200줄 | 정상 |
| 200~300줄 | 리뷰 시 분리 가능 여부 검토 |
| > 300줄 | **반드시 분리** — 하위 디렉토리 conftest.py 또는 `tests/factories.py` 모듈로 추출 |

분리 대상 우선순위:
1. **테스트 데이터 상수** → `tests/constants.py` 또는 하위 디렉토리 `constants.py`
2. **Mock/Stub 클래스** → `tests/factories.py`
3. **도메인별 fixture** → 하위 디렉토리 `conftest.py`

### 5.4 공통 fixture 예시

```python
# tests/conftest.py — 전체 공유 fixture (격리용)
import pytest
from selfhealing.settings.throttle import ThrottleSettings

@pytest.fixture
def default_throttle_settings():
    """기본 ThrottleSettings 인스턴스."""
    return ThrottleSettings()

@pytest.fixture
def default_service_name(default_throttle_settings):
    """기본 service_name 값."""
    return default_throttle_settings.service_name
```

### 5.5 사용 예시

```python
def test_service_name_default(self, default_service_name):
    throttle = AdaptiveThrottle(ThrottleConfig())
    assert throttle._service_name == default_service_name
```

---

## 6. 테스트 코드 작성 규칙

### 6.1 테스트 함수 구조화 (Arrange-Act-Assert)

복잡한 테스트(4줄 이상)는 **데이터 준비 → 실행 → 검증**의 3단계로 구조화하여 가독성을 높인다.
각 단계 사이에 **빈 줄**을 두어 시각적으로 분리한다.

```python
# ✅ 좋은 예: 3단계 구조
def test_cell_evacuation_triggers_on_low_health(self):
    """건강도 임계값 이하 시 대피가 트리거된다."""
    # Given — 데이터 및 상태 준비
    registry = CellRegistry(settings)
    registry.register_cell("cell-0", state=CellState.ACTIVE)
    aggregator = CellHealthAggregator(registry)

    # When — 검증하고자 하는 단일 동작 실행
    aggregator.update_health("cell-0", score=0.2)

    # Then — 결과 및 부수효과 검증
    cell = registry.get_cell("cell-0")
    assert cell.state == CellState.EVACUATING
```

```python
# ✅ 간단한 테스트는 구조화 주석 없이도 OK
def test_enabled_default_is_false(self):
    """마스터 토글 기본값: False."""
    settings = CellTopologySettings()
    assert settings.enabled is False
```

**규칙**:
- 주석 스타일은 `# Given` / `# When` / `# Then` 을 표준으로 한다
- 3줄 이하의 간단한 테스트에는 주석을 강제하지 않는다
- 하나의 테스트 함수에서 **Act(When)는 1회**만 수행한다 — 여러 동작을 검증하려면 테스트를 분리한다

### 6.2 Mock 안전성 — autospec 사용 원칙

`unittest.mock`은 존재하지 않는 속성/메서드를 호출해도 에러를 발생시키지 않는다.
**오타나 인터페이스 변경을 감지하지 못하는 거짓 양성(False Positive)** 을 방지하기 위해 `autospec=True`를 사용한다.

```python
# ❌ 위험한 예: spec 없는 Mock — 존재하지 않는 메서드 호출이 조용히 통과
@patch("selfhealing.services.notifier.SlackNotifier")
def test_notify(mock_cls):
    mock_cls.return_value.notiffyyy()  # 오타인데 에러 안 남!

# ✅ 좋은 예: autospec=True — 원본 인터페이스를 강제
@patch("selfhealing.services.notifier.SlackNotifier", autospec=True)
def test_notify(mock_cls):
    mock_cls.return_value.notify()  # 실제 있는 메서드만 허용
    # mock_cls.return_value.notiffyyy()  → AttributeError 발생
```

```python
# ✅ Mock() 직접 생성 시에도 spec 지정
from selfhealing.services.event_bus.bus import SelfHealingEventBus

mock_bus = MagicMock(spec=SelfHealingEventBus)
mock_bus.emit(event)           # OK
# mock_bus.emitt(event)        # → AttributeError 발생
```

**규칙**:
- **신규 테스트**: `@patch()` 사용 시 `autospec=True` 필수. `Mock()`/`MagicMock()` 생성 시 `spec=` 지정 권장
- **기존 테스트**: 수정 시 점진적으로 `autospec=True` 적용 (§4 점진적 적용 전략)
- **예외**: 동적으로 속성을 추가해야 하는 특수한 경우에만 spec 없이 생성 허용 (docstring에 사유 명시)

### 6.3 시간 의존성 제어 — `time.sleep()` 금지

셀프힐링 시스템은 TTL, 슬라이딩 윈도우, 서킷 브레이커 타임아웃 등 **시간에 민감한 로직**이 많다.
테스트에서 `time.sleep()`을 직접 호출하면 **테스트 속도 저하**와 **Flaky Test**를 유발한다.

**반드시 프로젝트 표준 유틸리티 `tests/factories/time_helpers.py`를 사용한다.**

```python
# ❌ 나쁜 예: 실제 sleep — 느리고 Flaky
def test_ttl_expiration(self):
    cache.set("key", "value", ttl=5)
    time.sleep(6)  # 6초 실제 대기!
    assert cache.get("key") is None

# ✅ 좋은 예: freezegun으로 시간 점프 (즉시 실행)
from tests.factories.time_helpers import freeze_time

def test_ttl_expiration(self):
    with freeze_time("2026-02-10 10:00:00"):
        cache.set("key", "value", ttl=5)
    with freeze_time("2026-02-10 10:00:06"):
        assert cache.get("key") is None
```

```python
# ✅ sleep 호출을 검증해야 할 때: mock_sleep 사용
from tests.factories.time_helpers import mock_sleep

def test_retry_sleeps_between_attempts(self):
    with mock_sleep() as sleep_mock:
        retry_with_backoff(action, max_retries=3)
    assert sleep_mock.call_count == 2
    assert sleep_mock.total_slept > 0
```

**프로젝트 시간 유틸리티** (`tests/factories/time_helpers.py`):

| 함수 | 용도 |
|------|------|
| `freeze_time(time_str)` | `datetime.now()`를 특정 시간으로 고정 (컨텍스트 매니저) |
| `mock_sleep()` | `time.sleep()`을 모킹하여 즉시 반환 + 호출 추적 |
| `get_fixed_datetime(y, m, d, h, m, s)` | 테스트용 고정 datetime 생성 (UTC) |
| `make_datetime_range(start, count, delta)` | 시간 순서 있는 datetime 리스트 생성 |

**규칙**:
- **테스트 코드에서 `time.sleep()` 직접 호출 금지** — 예외 없음
- `datetime.now()` 의존 로직은 `freeze_time()`으로 시간 고정 후 테스트
- sleep 동작 자체를 검증해야 할 때만 `mock_sleep()` 사용
- `patch("time.time")` 직접 사용보다 `freeze_time()` 래퍼를 우선한다 (일관성)

---

## 7. 체크리스트

테스트 작성 시 다음을 확인:

### 7.1 분류 정책
- [ ] **계약 vs 동작 구분**: 테스트 목적이 계약 검증인지 동작 검증인지 판단
- [ ] 계약 검증 → `Test*Contract` 클래스, 하드코딩 기대값 사용
- [ ] 동작 검증 → `Test*Behavior` 클래스, 소스 참조 사용

### 7.2 하드코딩 정책
- [ ] 기본값 하드코딩 대신 소스 참조 사용 (동작 검증의 경우)
- [ ] 상수 하드코딩 대신 상수 import 사용 (동작 검증의 경우)
- [ ] 변환 결과 검증 시 동일 함수로 기대값 계산
- [ ] 필요시 소스에 상수 추출 후 테스트에서 참조
- [ ] model_fields.default 동어반복 방지 (§0.4)

### 7.3 코드 품질
- [ ] 테스트 함수명: `test_<대상>_<상황>_<기대결과>` 서술형 (§3.3)
- [ ] 복잡한 테스트: Given/When/Then 3단계 구조화 (§6.1)
- [ ] Mock 생성: `autospec=True` 또는 `spec=` 지정 (§6.2)
- [ ] 시간 의존: `time.sleep()` 대신 `time_helpers` 사용 (§6.3)

### 7.4 검증 기법 커버리지 (해당 시 — §8 참조)
- [ ] **경계값**: ge/le 제약이 있는 필드 → 경계 직전/직후 값 테스트 (§8.1)
- [ ] **예외/엣지 케이스**: None, 빈 문자열, 범위 초과 입력 처리 (§8.2)
- [ ] **멱등성**: 동일 입력 N회 호출 시 동일 결과 (§8.3)
- [ ] **부수효과**: 로그, 이벤트, 상태 변경이 의도대로 발생 (§8.4)
- [ ] **의존성 상호작용**: Mock 대상이 정확한 횟수/인자로 호출됨 (§8.5)
- [ ] **데이터 불변성**: 입력 파라미터가 함수 내부에서 변경되지 않음 (§8.6)
- [ ] **동시성/스레드 안전**: 멀티스레드 접근 시 데이터 정합성 유지 (§8.7)
- [ ] **상태 전이**: 이벤트 시퀀스에 따른 상태 머신 전환이 올바른지 (§8.8)
- [ ] **직렬화 왕복**: `to_dict()`→`from_dict()` 라운드트립 데이터 보존 (§8.9)
- [ ] **싱글톤/라이프사이클**: `get_*()`/`reset_*()` 캐싱/초기화 동작 (§8.10)
- [ ] **시간 의존성**: TTL, 타임아웃, 주기적 배치의 시간 경과 동작 (§8.11)

### 7.5 conftest.py
- [ ] conftest.py 배치: 2파일 이상 공유 시에만 conftest 이동 (§5.1)
- [ ] conftest.py 크기: 300줄 초과 시 분리 검토 (§5.3)

---

## 8. 검증 기법 상세 가이드

§0의 **Contract/Behavior 분류**는 "기대값을 어떻게 작성할 것인가(하드코딩 vs 소스 참조)"에 대한 정책이다.
본 섹션은 **"무엇을 테스트할 것인가"** — 즉, 하나의 모듈에 대해 어떤 검증 기법을 적용해야 하는지 안내한다.

> **모든 기법이 항상 필요한 것은 아니다.** 구현 코드의 특성에 따라 해당되는 기법만 선택적으로 적용한다.

### 8.0 기법 선택 기준

```
구현 코드 특성 → 적용 기법
│
├─ Pydantic Settings / Field 제약 → §8.1 경계값 + §8.2 예외
├─ 외부 서비스 호출 (Mock 대상) → §8.4 부수효과 + §8.5 상호작용
├─ 캐시/레지스트리/카운터 등 상태 보유 → §8.3 멱등성 + §8.8 상태 전이
├─ 입력 데이터 가공/변환 함수 → §8.6 불변성
├─ threading.Lock, 싱글톤, 공유 자원 → §8.7 동시성
├─ to_dict/from_dict, model_dump → §8.9 직렬화
├─ get_*() / reset_*() 팩토리 → §8.10 싱글톤
└─ TTL, 타임아웃, 슬라이딩 윈도우 → §8.11 시간 의존성
```

### 8.1 경계값 분석 (Boundary Value Analysis)

**적용 시점**: Pydantic `Field(ge=, le=)` 제약, 수치 비교 로직(`<`, `<=`, `>`, `>=`), 범위 판정 함수

**핵심 원칙**: 경계의 **직전(실패)**, **경계(성공/실패)**, **직후(성공)** 3개 값을 테스트한다.

```python
# ✅ 경계값 테스트 (Contract — 제약 조건이 설계 사양인 경우)
class TestRunbookSettingsBoundaryContract:
    """RunbookSettings 필드 경계값 계약 검증."""

    def test_approval_timeout_minimum_boundary(self):
        """approval_timeout_seconds의 최소 경계: ge=30."""
        # 경계 직전 (29) → 실패
        with pytest.raises(ValidationError):
            RunbookSettings(approval_timeout_seconds=29)
        # 경계 값 (30) → 성공
        settings = RunbookSettings(approval_timeout_seconds=30)
        assert settings.approval_timeout_seconds == 30

    def test_approval_timeout_maximum_boundary(self):
        """approval_timeout_seconds의 최대 경계: le=3600."""
        # 경계 값 (3600) → 성공
        settings = RunbookSettings(approval_timeout_seconds=3600)
        assert settings.approval_timeout_seconds == 3600
        # 경계 직후 (3601) → 실패
        with pytest.raises(ValidationError):
            RunbookSettings(approval_timeout_seconds=3601)

# ✅ 경계값 테스트 (Behavior — 비교 로직 동작 검증)
class TestSafetyBoundsBoundaryBehavior:
    """범위 판정 함수의 경계 동작 검증."""

    def test_at_exact_minimum_is_within_bounds(self, default_settings):
        """최솟값 정확히 일치 시 범위 내 판정."""
        safety = SafetyBounds()
        assert safety.is_within_bounds(
            "throttle_sla_warning_ms",
            default_settings.throttle_sla_warning_ms_min,
        ) is True

    def test_below_minimum_is_out_of_bounds(self, default_settings):
        """최솟값 미만 시 범위 외 판정."""
        safety = SafetyBounds()
        assert safety.is_within_bounds(
            "throttle_sla_warning_ms",
            default_settings.throttle_sla_warning_ms_min - 1,
        ) is False
```

### 8.2 예외 및 엣지 케이스 (Exception & Edge Case)

**적용 시점**: 외부 입력을 받는 함수, None/빈 값 가능성, 시스템 한계치 초과 가능성

**핵심 원칙**: 시스템이 **크래시하지 않고** 정의된 예외를 발생시키는지, 또는 graceful하게 처리하는지 확인한다.

```python
# ✅ 예외 테스트 (Behavior)
class TestPatternMatcherEdgeCaseBehavior:
    """패턴 매처 엣지 케이스 동작 검증."""

    def test_empty_metrics_returns_no_match(self):
        """빈 메트릭 딕셔너리 입력 시 매칭 결과 없음."""
        matcher = PatternMatcher(registry)
        result = matcher.match({})
        assert result is None

    def test_none_input_raises_type_error(self):
        """None 입력 시 TypeError 발생."""
        matcher = PatternMatcher(registry)
        with pytest.raises(TypeError):
            matcher.match(None)

    def test_unknown_metric_key_is_ignored(self):
        """등록되지 않은 메트릭 키는 무시."""
        matcher = PatternMatcher(registry)
        result = matcher.match({"unknown_metric": 99.9})
        assert result is None
```

### 8.3 멱등성 검증 (Idempotency)

**적용 시점**: 같은 요청을 여러 번 처리하는 핸들러, 캐시 갱신, 상태 설정 함수

**핵심 원칙**: 동일 입력을 N회 호출해도 **결과와 부수효과가 1회 호출과 동일**한지 확인한다.
```python
# ✅ 멱등성 테스트 (Behavior)
class TestIdempotentStepHandlerBehavior:
    """멱등 step 핸들러 동작 검증."""

    def test_duplicate_execution_returns_same_result(self):
        """동일 step을 2회 실행해도 결과가 같다."""
        handler = IdempotentStepHandler(store=mock_store)
        result_1 = handler.execute(step_id="s1", action=my_action)
        result_2 = handler.execute(step_id="s1", action=my_action)
        assert result_1 == result_2

    def test_duplicate_execution_does_not_double_apply(self):
        """동일 step을 2회 실행해도 실제 액션은 1회만 수행."""
        handler = IdempotentStepHandler(store=mock_store)
        handler.execute(step_id="s1", action=mock_action)
        handler.execute(step_id="s1", action=mock_action)
        mock_action.assert_called_once()
```

### 8.4 부수효과 검증 (Side Effect)

**적용 시점**: 로그 기록, 이벤트 발행, 메트릭 카운터 증가, 외부 상태 변경

**핵심 원칙**: 함수의 반환값 외에 **외부에 끼치는 영향**이 의도대로인지 확인한다.
```python
# ✅ 부수효과 테스트 (Behavior)
class TestApprovalGateSideEffectBehavior:
    """승인 게이트 부수효과 검증."""

    def test_medium_risk_emits_notification(self, mock_notifier, mock_bus):
        """MEDIUM 위험도 런북은 알림을 발행한다."""
        gate = ApprovalGate(notifier=mock_notifier, bus=mock_bus)
        gate.evaluate(runbook, risk_level=RiskLevel.MEDIUM)
        mock_notifier.notify.assert_called_once()

    def test_low_risk_does_not_emit_notification(self, mock_notifier, mock_bus):
        """LOW 위험도 런북은 알림을 발행하지 않는다."""
        gate = ApprovalGate(notifier=mock_notifier, bus=mock_bus)
        gate.evaluate(runbook, risk_level=RiskLevel.LOW)
        mock_notifier.notify.assert_not_called()

    def test_completion_emits_event(self, mock_bus):
        """완료 시 RUNBOOK_COMPLETED 이벤트 발행."""
        gate.on_complete(runbook_id="rb1")
        emitted = mock_bus.emit.call_args[0][0]
        assert emitted.event_type == EventType.RUNBOOK_COMPLETED
```

### 8.5 의존성 상호작용 검증 (Dependency Interaction)

**적용 시점**: Mock으로 대체한 외부 의존 컴포넌트가 **정확한 횟수, 정확한 인자**로 호출되었는지

**핵심 원칙**: 불필요한 호출은 없는가? 필수 호출을 빠뜨리지 않았는가?
```python
# ✅ 상호작용 테스트 (Behavior)
class TestExecutorInteractionBehavior:
    """Executor의 의존 컴포넌트 호출 검증."""

    def test_acquires_lock_before_execution(self, mock_lock):
        """실행 전 분산 락을 획득한다."""
        executor = RunbookExecutor(lock=mock_lock)
        executor.run(runbook)
        mock_lock.acquire.assert_called_once_with(
            "runbook", runbook.id,
        )

    def test_releases_lock_after_execution(self, mock_lock):
        """실행 후 분산 락을 해제한다."""
        executor = RunbookExecutor(lock=mock_lock)
        executor.run(runbook)
        mock_lock.release.assert_called_once()

    def test_releases_lock_even_on_failure(self, mock_lock, failing_step):
        """실행 실패 시에도 분산 락을 해제한다."""
        executor = RunbookExecutor(lock=mock_lock)
        with pytest.raises(ExecutionError):
            executor.run(failing_runbook)
        mock_lock.release.assert_called_once()
```

### 8.6 데이터 불변성 검증 (Data Immutability)

**적용 시점**: 입력 리스트/딕셔너리를 가공하는 함수, frozen dataclass/model 사용처

**핵심 원칙**: 함수 호출 전후로 원본 데이터가 **훼손되지 않았는지** 확인한다.
```python
# ✅ 불변성 테스트 (Behavior)
class TestCellRegistryImmutabilityBehavior:
    """CellRegistry 입력 데이터 불변성 검증."""

    def test_assign_does_not_mutate_input_services(self):
        """Cell 할당 시 원본 서비스 리스트가 변경되지 않는다."""
        original_services = ["svc-a", "svc-b", "svc-c"]
        services_copy = original_services.copy()
        registry.assign(services=original_services)
        assert original_services == services_copy

    def test_frozen_cell_info_prevents_mutation(self):
        """CellInfo는 frozen이므로 속성 변경 시 에러."""
        cell = CellInfo(cell_id="cell-0", state=CellState.ACTIVE)
        with pytest.raises(FrozenInstanceError):
            cell.state = CellState.EVACUATING
```

### 8.7 동시성 및 스레드 안전 검증 (Concurrency & Thread Safety)

**적용 시점**: `threading.Lock` 사용, 싱글톤 팩토리, 공유 카운터/레지스트리, `asyncio` 코루틴

**핵심 원칙**: N개 스레드가 동시 접근해도 데이터 정합성이 유지되는지 확인한다.
```python
# ✅ 동시성 테스트 (Behavior)
class TestCellRegistryThreadSafetyBehavior:
    """CellRegistry 멀티스레드 접근 안전성 검증."""

    def test_concurrent_assign_no_data_corruption(self):
        """10개 스레드가 동시에 assign해도 데이터 손상 없음."""
        registry = CellRegistry(settings)
        errors = []

        def worker(thread_id):
            try:
                registry.assign(services=[f"svc-{thread_id}"])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_get_singleton_returns_same_instance(self):
        """멀티스레드에서 get_cell_registry()가 동일 인스턴스 반환."""
        results = []

        def worker():
            results.append(get_cell_registry())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is results[0] for r in results)
```

### 8.8 상태 전이 검증 (State Transition)

**적용 시점**: Circuit Breaker, Saga 상태 머신, Emergency Level, Cell 상태 변경

**핵심 원칙**: 이벤트 시퀀스에 대해 **허용된 전이만 발생**하는지, 비허용 전이가 거부되는지 확인한다.
```python
# ✅ 상태 전이 테스트 (Behavior)
class TestCellStateTransitionBehavior:
    """Cell 상태 전이 규칙 검증."""

    def test_active_to_evacuating_on_health_drop(self):
        """ACTIVE Cell의 건강도가 임계값 이하로 떨어지면 EVACUATING으로 전이."""
        cell = registry.get_cell("cell-0")
        assert cell.state == CellState.ACTIVE
        policy.on_health_update("cell-0", health_score=0.2)
        cell = registry.get_cell("cell-0")
        assert cell.state == CellState.EVACUATING

    def test_evacuating_to_active_on_recovery(self):
        """EVACUATING Cell의 건강도가 회복되면 ACTIVE로 복귀."""
        policy.on_health_update("cell-0", health_score=0.2)  # → EVACUATING
        policy.on_health_update("cell-0", health_score=0.8)  # → ACTIVE
        cell = registry.get_cell("cell-0")
        assert cell.state == CellState.ACTIVE

    def test_invalid_transition_rejected(self):
        """허용되지 않은 상태 전이는 거부."""
        # ISOLATED → ACTIVE 직접 전이는 불가 (EVACUATING을 거쳐야 함)
        with pytest.raises(InvalidStateTransition):
            registry.transition("cell-0", CellState.ISOLATED, CellState.ACTIVE)
```

### 8.9 직렬화 왕복 검증 (Serialization Round-trip)

**적용 시점**: `to_dict()`/`from_dict()`, `model_dump()`/`model_validate()`, JSON 파일 저장/로드

**핵심 원칙**: **직렬화 → 역직렬화 시 원본 데이터가 손실 없이 복원**되는지 확인한다.
```python
# ✅ 직렬화 왕복 테스트 (Behavior)
class TestEvacuationRecordSerializationBehavior:
    """EvacuationRecord 직렬화 왕복 검증."""

    def test_round_trip_preserves_all_fields(self):
        """to_dict → from_dict 왕복 시 모든 필드가 보존된다."""
        original = EvacuationRecord(
            cell_id="cell-3",
            reason="health_below_threshold",
            timestamp=datetime.now(timezone.utc),
        )
        serialized = original.to_dict()
        restored = EvacuationRecord.from_dict(serialized)
        assert restored.cell_id == original.cell_id
        assert restored.reason == original.reason
        assert restored.timestamp == original.timestamp

    def test_serialized_keys_match_contract(self):
        """직렬화된 딕셔너리의 키가 계약과 일치한다."""
        record = EvacuationRecord(cell_id="cell-0", reason="test")
        data = record.to_dict()
        assert "cell_id" in data
        assert "reason" in data
        assert "timestamp" in data
```
### 8.10 싱글톤 및 라이프사이클 검증 (Singleton & Lifecycle)

**적용 시점**: `get_*()` 팩토리 함수, `reset_*()` 초기화, 컴포넌트 시작/종료 순서

**핵심 원칙**: 캐싱이 정상 동작하고, 리셋 후 새 인스턴스가 생성되며, 라이프사이클 훅이 올바른 순서로 호출되는지 확인한다.
```python
# ✅ 싱글톤 테스트 (Behavior)
class TestCellRegistrySingletonBehavior:
    """CellRegistry 싱글톤 캐싱/리셋 동작 검증."""

    def test_get_returns_same_instance(self):
        """get_cell_registry()는 동일 인스턴스를 반환."""
        first = get_cell_registry()
        second = get_cell_registry()
        assert first is second

    def test_reset_clears_cached_instance(self):
        """reset 후 새 인스턴스가 생성된다."""
        first = get_cell_registry()
        reset_cell_registry()
        second = get_cell_registry()
        assert first is not second
```

### 8.11 시간 의존성 검증 (Time-dependent Behavior)

**적용 시점**: TTL 만료, 타임아웃, 주기적 배치 작업, 슬라이딩 윈도우, 서킷 브레이커 half-open 전이

**핵심 원칙**: `time.sleep()` 절대 사용 금지. `tests/factories/time_helpers.py`의 `freeze_time()`/`mock_sleep()`을 사용한다. (상세: §6.3)

```python
# ✅ 시간 의존성 테스트 (Behavior)
from tests.factories.time_helpers import freeze_time

class TestCircuitBreakerTimeBehavior:
    """서킷 브레이커 시간 경과 동작 검증."""

    def test_transitions_to_half_open_after_ttl(self):
        """TTL 만료 후 OPEN → HALF_OPEN으로 전이한다."""
        with freeze_time("2026-02-10 10:00:00"):
            cb.force_open(ttl_seconds=300)
            assert cb.get_state() == CircuitBreakerState.OPEN

        # 5분 + 1초 후로 시간 점프 (sleep 없이 즉시)
        with freeze_time("2026-02-10 10:05:01"):
            assert cb.get_state() == CircuitBreakerState.HALF_OPEN

    def test_sliding_window_expires_old_entries(self):
        """슬라이딩 윈도우에서 TTL이 지난 항목이 제거된다."""
        with freeze_time("2026-02-10 10:00:00"):
            window.add(error_count=5)

        with freeze_time("2026-02-10 10:01:01"):
            # 60초 윈도우 → 이전 항목 만료
            assert window.get_total() == 0
```

### 8.12 참고사항

#### 회귀 테스트 (Regression)

회귀 테스트는 기법이 아니라 **관행**이다. 버그 수정 시 해당 버그를 재현하는 테스트를 먼저 작성하고, 수정 후 통과를 확인한다. 기존 분류(Contract/Behavior) 안에 포함시키되, docstring에 버그 참조를 남긴다.

```python
def test_negative_cell_count_rejected(self):
    """음수 cell_count 입력 시 ValidationError. (BUG-1234 회귀 방지)"""
    with pytest.raises(ValidationError):
        CellTopologySettings(cell_count=-1)
```

#### 성능 테스트 (Performance)

타이밍 민감 코드(TTL, 타임아웃, 슬라이딩 윈도우)에 한해 단위 테스트 수준에서 수행할 수 있다. 단, CI 환경의 성능 편차를 고려하여 **넉넉한 마진**을 둔다.

```python
def test_sliding_window_operations_per_second(self):
    """슬라이딩 윈도우 10,000회 연산이 1초 이내에 완료."""
    start = time.perf_counter()
    for _ in range(10_000):
        window.add(1.0)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
```

