# 207. env_nested_delimiter 사용 기준 수립 계획

> **상태**: 📋 계획
> **목적**: `SettingsConfigDict`의 `env_nested_delimiter` 설정 사용 여부에 대한 일관된 기준을 수립한다.

---

## 1. 현황

### 1-1. 전체 settings/ 파일 현황

`settings/` 디렉토리에는 **90+ BaseSettings** 파일이 존재하며, `env_nested_delimiter` 사용은 **단 1건**:

```python
# settings/safety_bounds.py L46-58
class SafetyBoundsSettings(BaseSettings):
    """SafetyBounds 전체 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_BOUNDS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
        env_nested_delimiter="__",    # ← 유일한 사용처
    )
```

### 1-2. 왜 safety_bounds.py만 사용하는가?

`SafetyBoundsSettings`는 중첩 모델(`ParameterBoundConfig`)의 필드를 환경변수로 노출하기 위해 사용:

```python
# settings/safety_bounds.py L22-33
class ParameterBoundConfig(BaseModel):
    """개별 파라미터 한계 설정."""
    min_value: float = Field(description="최소 허용 값")
    max_value: float = Field(description="최대 허용 값")
    max_change_per_cycle: float = Field(
        ge=0.01, le=1.0,
        description="한 사이클당 최대 변경 비율 (0.3 = 30%)",
    )
```

`env_nested_delimiter="__"` 설정으로 환경변수를 아래처럼 매핑:

```bash
SELFHEALING_BOUNDS_TIMEOUT_MS__MIN_VALUE=100
SELFHEALING_BOUNDS_TIMEOUT_MS__MAX_VALUE=30000
SELFHEALING_BOUNDS_TIMEOUT_MS__MAX_CHANGE_PER_CYCLE=0.3
```

### 1-3. 나머지 90+ settings 파일의 패턴

```python
# 일반적 패턴 (중첩 모델 없음)
model_config = SettingsConfigDict(
    env_prefix="SELFHEALING_CIRCUIT_BREAKER_",
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    validate_default=True,
    # env_nested_delimiter 없음
)
```

나머지 파일들은 모두 **플랫 필드** 구조로 중첩이 없어 `env_nested_delimiter` 불필요.

---

## 2. 문제점

| 항목 | 설명 |
|------|------|
| **암묵적 규칙** | 중첩 모델이 있을 때 `env_nested_delimiter`를 써야 한다는 규칙이 문서화되어 있지 않음 |
| **향후 중첩 모델 추가 시** | 새 설정 파일에 중첩 `BaseModel`을 추가할 때 `env_nested_delimiter` 누락 가능성 |
| **운영자 혼란** | 환경변수 설정 시 `__` 구분자가 필요한 설정과 아닌 설정의 구분이 불명확 |
| **단일 사용처** | 90+개 중 1개만 사용하므로 운영 문서에서 누락되기 쉬움 |

---

## 3. 수정 계획

### 3-1. 명시적 규칙 문서화

`settings/` 모듈의 README 또는 기본 설정 가이드에 다음 규칙 추가:

```markdown
## env_nested_delimiter 사용 규칙

| 조건 | env_nested_delimiter | 예시 |
|------|---------------------|------|
| 플랫 필드만 있는 Settings | **사용하지 않음** | 기본 패턴 |
| 중첩 BaseModel 필드가 있는 Settings | `"__"` **필수** | safety_bounds.py |

### 중첩 모델 사용 시 체크리스트:
1. ✅ `env_nested_delimiter="__"` 추가
2. ✅ 환경변수 매핑 문서화 (docstring 내)
3. ✅ 테스트에서 `__` 구분자 환경변수 검증
```

### 3-2. 코드 내 인라인 주석 표준화

현재 `safety_bounds.py`에 `env_nested_delimiter` 존재 이유에 대한 주석이 없음.

```python
# 수정 제안
model_config = SettingsConfigDict(
    env_prefix="SELFHEALING_BOUNDS_",
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    validate_default=True,
    # ParameterBoundConfig 중첩 모델 → 환경변수 매핑에 "__" 구분자 필요
    # 예: SELFHEALING_BOUNDS_TIMEOUT_MS__MIN_VALUE=100
    env_nested_delimiter="__",
)
```

### 3-3. 향후 중첩 모델 추가 시 자동 검증

`pyproject.toml`의 커스텀 린트 규칙 또는 CI 체크 추가 검토:
- `BaseModel` 필드를 가진 `BaseSettings`에 `env_nested_delimiter` 미설정 시 경고

### 3-4. 검증 항목

- [ ] `safety_bounds.py`에 `env_nested_delimiter` 목적 주석 추가
- [ ] settings/ 하위 모든 파일에 중첩 `BaseModel` 필드 존재 여부 스캔 (누락 방지)
- [ ] 운영 환경변수 문서에 `__` 구분자 사용 사례 명시
- [ ] 신규 settings 파일 생성 시 체크리스트 템플릿에 반영
