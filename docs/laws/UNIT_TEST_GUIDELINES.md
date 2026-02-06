# 단위 테스트 작성 가이드라인

> **적용 범위**: `packages/selfhealing-python/tests/` 및 전역 `tests/` 폴더
> **최종 수정일**: 2026-02-06

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

### 5.1 공통 fixture 정의

```python
# tests/conftest.py
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

### 5.2 사용 예시

```python
def test_service_name_default(self, default_service_name):
    throttle = AdaptiveThrottle(ThrottleConfig())
    assert throttle._service_name == default_service_name
```

---

## 6. 체크리스트

테스트 작성 시 다음을 확인:

- [ ] 기본값 하드코딩 대신 소스 참조 사용
- [ ] 상수 하드코딩 대신 상수 import 사용
- [ ] 변환 결과 검증 시 동일 함수로 기대값 계산
- [ ] 필요시 소스에 상수 추출 후 테스트에서 참조
- [ ] conftest.py에 재사용 fixture 정의 검토
