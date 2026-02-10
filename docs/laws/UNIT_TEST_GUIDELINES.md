# 단위 테스트 작성 가이드라인

> **적용 범위**: `packages/selfhealing-python/tests/` 및 전역 `tests/` 폴더
> **최종 수정일**: 2026-02-10

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

## 6. 체크리스트

테스트 작성 시 다음을 확인:

- [ ] **계약 vs 동작 구분**: 테스트 목적이 계약 검증인지 동작 검증인지 판단
- [ ] 계약 검증 → `Test*Contract` 클래스, 하드코딩 기대값 사용
- [ ] 동작 검증 → `Test*Behavior` 클래스, 소스 참조 사용
- [ ] 기본값 하드코딩 대신 소스 참조 사용 (동작 검증의 경우)
- [ ] 상수 하드코딩 대신 상수 import 사용 (동작 검증의 경우)
- [ ] 변환 결과 검증 시 동일 함수로 기대값 계산
- [ ] 필요시 소스에 상수 추출 후 테스트에서 참조
- [ ] conftest.py 배치: 2파일 이상 공유 시에만 conftest 이동 (§5.1)
- [ ] conftest.py 크기: 300줄 초과 시 분리 검토 (§5.3)
- [ ] model_fields.default 동어반복 방지 (§0.4)
