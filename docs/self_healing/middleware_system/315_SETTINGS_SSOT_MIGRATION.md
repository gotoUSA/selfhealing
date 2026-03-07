# 315. Settings SSOT Migration — 108 Singleton 통합 및 .env I/O 최적화

> **Status**: Planned
> **Severity**: P2 (MEDIUM)
> **Target**: `packages/selfhealing-python/src/selfhealing/settings/` 전체 (108개 파일)
> **References**:
> - 313 — Settings & Configuration Consistency (선행 작업, Q2에서 분리)
> - 90-102 — Config Migration 시리즈

---

## 1. 현황 및 문제

### 1.1 이원화된 Settings 싱글톤

현재 settings 아키텍처에 **두 종류의 인스턴스 생성 경로**가 공존한다:

| 경로 | 구현 | 인스턴스 수 |
|------|------|-----------|
| Root 경유 | `get_config().circuit_breaker` | Root 내부에 1개 |
| 독립 싱글톤 | `get_circuit_breaker_settings()` → 자체 `_settings` 전역변수 | 별도 1개 |

**결과**: 동일한 settings 클래스의 인스턴스가 애플리케이션 내에 **2개** 존재할 수 있다.
런타임 설정 갱신이나 테스트 환경에서 한쪽만 reset하면 **상태 불일치(Drift)**가 발생한다.

### 1.2 수치 현황

| 항목 | 수치 |
|------|------|
| Root에 조합된 sub-settings | 18개 (`settings/root.py:77-148`) |
| 독립 `get_*_settings()` 싱글톤 | 108개 |
| Root에 convenience getter가 있는 것 | 8개 (`root.py:318-356`) |
| Root로 delegation하는 sub-settings | **0개** |
| `get_config()` 직접 import하는 서비스 | 16개 |
| 개별 `get_*_settings()` import하는 서비스 | 100+개 |

### 1.3 .env 중복 파싱 문제

108개 settings 파일 전부 `env_file=".env"`를 선언하고 있다.
각 `get_*_settings()`가 독립적으로 인스턴스를 생성하므로,
최악의 경우 `.env` 파일이 **108번** 파싱된다.

| 항목 | 비용 |
|------|------|
| .env 파싱 1회 (SSD) | ~0.5-2ms |
| 18개 (현재 Root) | ~10-36ms |
| 108개 (전체 SSOT) | ~54-216ms |

이 비용은 **프로세스당 1회** 발생하므로 비즈니스 영향은 미미하나,
SSOT 통합 시 Root 초기화가 모든 sub-settings를 한꺼번에 생성하므로 최적화가 필요하다.

---

## 2. 개선 계획

### 2.1 Phase 1: SharedEnvSource 도입 (패턴 B)

`.env` 파일을 1회만 파싱하고, 파싱 결과를 메모리에 캐시하여 모든 settings 클래스가 공유한다.

**업계 사례**: Netflix Archaius, Hashicorp Consul Template

#### 2.1.1 SharedEnvSource 구현

```python
# settings/shared_env_source.py (신규)
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic_settings import PydanticBaseSettingsSource


class SharedEnvSource:
    """싱글톤 .env 파싱 캐시.

    모든 BaseSettings 클래스가 .env 파일을 중복 파싱하지 않도록
    파싱 결과를 메모리에 캐시한다.
    """

    _cache: dict[str, str | None] | None = None
    _lock = threading.Lock()

    @classmethod
    def load(cls, env_file: str = ".env") -> dict[str, str | None]:
        if cls._cache is None:
            with cls._lock:
                if cls._cache is None:
                    path = Path(env_file)
                    cls._cache = dotenv_values(path) if path.exists() else {}
        return cls._cache

    @classmethod
    def reset(cls) -> None:
        """캐시 초기화 (테스트용)."""
        with cls._lock:
            cls._cache = None


class SharedDotEnvSettingsSource(PydanticBaseSettingsSource):
    """Pydantic v2 custom settings source — SharedEnvSource 기반."""

    def get_field_value(
        self, field: Any, field_name: str
    ) -> tuple[Any, str, bool]:
        env_vars = SharedEnvSource.load()
        env_name = f"{self.settings_cls.model_config.get('env_prefix', '')}{field_name}".upper()
        val = env_vars.get(env_name)
        return val, field_name, val is not None

    def __call__(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for field_name, field_info in self.settings_cls.model_fields.items():
            val, _, is_set = self.get_field_value(field_info, field_name)
            if is_set:
                d[field_name] = val
        return d
```

#### 2.1.2 기존 settings에 적용

```python
# 각 settings 파일에 적용 (예: settings/circuit_breaker.py)
class CircuitBreakerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CB_",
        env_file=None,  # 직접 읽기 비활성화
        extra="ignore",
        validate_default=True,
    )

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, **kwargs
    ):
        from selfhealing.settings.shared_env_source import SharedDotEnvSettingsSource

        return (
            init_settings,
            env_settings,
            SharedDotEnvSettingsSource(settings_cls),
        )
```

### 2.2 Phase 2: Facade Pattern — get_*_settings() → Root 경유

기존 108개 `get_*_settings()` 함수의 **외부 API(함수 시그니처)는 유지**하면서,
내부 구현만 Root 경유로 변경한다.

#### 2.2.1 변환 패턴

```python
# BEFORE (독립 싱글톤)
_settings: CircuitBreakerSettings | None = None

def get_circuit_breaker_settings() -> CircuitBreakerSettings:
    global _settings
    if _settings is None:
        _settings = CircuitBreakerSettings()
    return _settings

def reset_circuit_breaker_settings() -> None:
    global _settings
    _settings = None


# AFTER (Root 경유 Facade)
def get_circuit_breaker_settings() -> CircuitBreakerSettings:
    """Root settings 경유 단일 진입점 (SSOT)."""
    from selfhealing.settings.root import get_config
    return get_config().circuit_breaker

def reset_circuit_breaker_settings() -> None:
    """Root reset으로 위임 (테스트용)."""
    from selfhealing.settings.root import reset_config
    reset_config()
```

**핵심**: `from selfhealing.settings.circuit_breaker import get_circuit_breaker_settings`로
import하는 100+개 서비스 코드는 **변경 불필요**.

#### 2.2.2 Root에 누락 필드 추가

현재 Root에 18개만 조합되어 있으므로, 나머지 90개를 추가해야 한다.
Cold Start 방지를 위해 `@cached_property`로 lazy 초기화한다.

```python
# settings/root.py — Phase 2 확장
from functools import cached_property

class SelfHealingSettings(BaseSettings):
    # 기존 18개 + 313 신규 2개 = 20개 (Field 방식 유지)
    circuit_breaker: CircuitBreakerSettings = Field(...)
    thread: ThreadManagementSettings = Field(...)
    detection: DetectionSettings = Field(...)

    # 나머지 88개 (cached_property로 lazy 초기화)
    @cached_property
    def admission_control(self) -> "AdmissionControlSettings":
        from selfhealing.settings.admission_control import AdmissionControlSettings
        return AdmissionControlSettings()

    @cached_property
    def backoff(self) -> "BackoffSettings":
        from selfhealing.settings.backoff import BackoffSettings
        return BackoffSettings()

    # ... 88개 반복
```

**장점**:
- Root 생성 시 기존 20개만 즉시 초기화 (현재와 동일한 비용)
- 나머지 88개는 **처음 접근할 때만** 초기화
- `model_dump()`에는 포함되지 않으므로 serialization 영향 없음

### 2.3 Phase 3: 테스트 인프라 단순화

SSOT 완성 후 `reset_config()` 하나로 전체 settings 초기화가 가능하다.

```python
# tests/conftest.py — Phase 3 단순화
@pytest.fixture(autouse=True, scope="function")
def auto_reset_all_settings():
    """모든 settings를 한 번에 리셋."""
    from selfhealing.settings.root import reset_config
    from selfhealing.settings.shared_env_source import SharedEnvSource

    reset_config()
    SharedEnvSource.reset()
    yield
    reset_config()
    SharedEnvSource.reset()
```

---

## 3. 마이그레이션 순서

| Phase | 작업 | 파일 수 | 우선순위 | 의존성 |
|-------|------|---------|----------|--------|
| 1 | `SharedEnvSource` 생성 | 1 신규 | P2 | 없음 |
| 1.1 | 기존 108개 settings에 `env_file=None` + `settings_customise_sources` 적용 | 108 수정 | P2 | Phase 1 |
| 2 | Root에 `@cached_property` 88개 추가 | 1 수정 | P2 | Phase 1 |
| 2.1 | 108개 `get_*_settings()`를 Root Facade로 변환 | 108 수정 | P2 | Phase 2 |
| 2.2 | 독립 `_settings` 전역변수 + `reset_*` 로컬 구현 제거 | 108 수정 | P2 | Phase 2.1 |
| 3 | `tests/conftest.py` 단순화 | 1 수정 | P3 | Phase 2 |

> Phase 1.1과 2.1은 **자동화 스크립트**로 일괄 변환 가능 (AST 변환 또는 codemod).

---

## 4. 순환 import 방지 전략

현재 import 방향: `root.py` → 18개 sub-settings (단방향, 안전)

SSOT 후 추가되는 방향: sub-settings의 `get_*_settings()` → `root.py` (역방향)

**방지책**: sub-settings에서 root import를 **함수 내부 지연 import**로 처리.

```python
def get_circuit_breaker_settings() -> CircuitBreakerSettings:
    from selfhealing.settings.root import get_config  # 함수 호출 시점에만 import
    return get_config().circuit_breaker
```

이 패턴은 313에서 `ThreadManagementSettings`와 `DetectionSettings`에 이미 적용되어 검증되었다.

---

## 5. 위험 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| `@cached_property`가 `model_dump()`에 미포함 | Root serialization 시 88개 누락 | Root를 직접 serialize하는 코드 없음 (확인 완료) |
| 108개 일괄 변환 시 실수 | 개별 settings 동작 변경 | AST codemod + 기존 테스트 스위트로 회귀 검증 |
| `SharedEnvSource`와 `os.environ` 우선순위 충돌 | env var가 .env보다 우선해야 하는데 역전 | Pydantic 기본 `EnvSettingsSource`를 유지하고 `.env` 부분만 교체 |
| 전체 reset 시 무관한 settings까지 초기화 | 테스트 성능 저하 | `@cached_property`이므로 미사용 settings는 재초기화 비용 0 |

---

## 6. 성능 예측

| 시나리오 | Before (현재) | After (SSOT + SharedEnvSource) |
|---------|--------------|-------------------------------|
| .env 파싱 횟수 | 108회 | **1회** |
| Root `get_config()` 초기화 | 18개 sub-settings 즉시 생성 | 20개 즉시 + 88개 lazy |
| 개별 `get_*_settings()` | 독립 인스턴스 생성 | Root 속성 접근 (포인터 반환) |
| `reset_config()` | Root만 초기화, 독립 싱글톤은 잔존 | **전체 초기화 완료** |

---

## 7. 313과의 관계

313에서 신규 생성한 `ThreadManagementSettings`와 `DetectionSettings`는
이미 Root 경유 패턴(Facade)을 적용했다. 이 두 클래스가 315의 **마이그레이션 템플릿**이 된다.

315의 Phase 2에서 기존 108개를 동일한 패턴으로 변환할 때,
313의 구현을 참조 예시(reference implementation)로 사용한다.

---

## 8. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-07 | 1.0.0 | 초안 작성 (313 Q2 리뷰에서 분리) |
