# 313. Settings & Configuration Consistency — 하드코딩 값 설정화 및 설정 사용 표준화

> **Status**: Refactor
> **Severity**: P2 (MEDIUM)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/event_bus/redis_bus.py` — 하드코딩 timeout
> - `packages/selfhealing-python/src/selfhealing/services/capacity_reservation/service.py` — 하드코딩 timeout
> - `packages/selfhealing-python/src/selfhealing/services/canary/state_refresher.py` — 하드코딩 timeout
> - `packages/selfhealing-python/src/selfhealing/services/chaos/synthetic_load.py` — 하드코딩 timeout
> - `packages/selfhealing-python/src/selfhealing/services/correlation_engine/wildcard_observer.py` — 하드코딩 window
> - `packages/selfhealing-python/src/selfhealing/services/correlation_engine/co_occurrence_tracker.py` — 하드코딩 window
> - `packages/selfhealing-python/src/selfhealing/services/predictive_forecaster/anomaly_detector.py` — 하드코딩 window
> - `packages/selfhealing-python/src/selfhealing/services/postmortem/notifier.py` — 하드코딩 HTTP timeout
> **References**:
> - 309 — 아키텍처 패턴 통일
> - 90-102 — 기존 Config Migration 문서 시리즈

---

## 1. 현황 및 문제

### 1.1 설정 인프라 평가

Settings 인프라 자체는 **우수한 상태**:
- 114개 Pydantic BaseSettings 파일
- `SELFHEALING_*` env var 네이밍 일관성 O
- `SelfHealingSettings` root 클래스에 모든 sub-settings 조합
- Range validator, cross-field validator 적용

그러나 **일부 서비스가 settings를 사용하지 않고 값을 하드코딩**하고 있다.

### 1.2 하드코딩 현황 — Thread Join Timeout

16개 이상의 파일에서 thread join/HTTP timeout이 하드코딩되어 있다.

| 파일 | 하드코딩 값 | 코드 위치 |
|------|-----------|-----------|
| `services/event_bus/redis_bus.py` | `timeout=10`, `timeout=5`, `timeout=5` | :375-377 |
| `services/capacity_reservation/service.py` | `timeout=10`, `timeout=5` | :186, :257 |
| `services/canary/state_refresher.py` | `timeout=5.0` | :142 |
| `services/chaos/synthetic_load.py` | `timeout=5.0` | :419 |
| `services/correlation_engine/wildcard_observer.py` | `timeout=5.0` | :210 |
| `services/namespace_emergency/partition_reconciliation.py` | `timeout=5.0` | :519 |
| `services/postmortem/notifier.py` | `timeout=10` | :419, :514 |

**문제**:
- 운영 환경에서 timeout 조정 시 코드 변경 + 재배포 필요
- 동일한 개념(thread join timeout)이 5초, 10초로 일관성 없음
- env var로 제어 불가

### 1.3 하드코딩 현황 — Detection Window Size

| 파일 | 하드코딩 값 | 코드 위치 |
|------|-----------|-----------|
| `services/correlation_engine/wildcard_observer.py` | `window=100` | :137 |
| `services/correlation_engine/co_occurrence_tracker.py` | `window=100` | :323 |
| `services/predictive_forecaster/anomaly_detector.py` | `threshold=3.0, window=100` | :21 |

**문제**:
- window size가 3곳에서 동일한 매직넘버 100으로 하드코딩
- 한 곳만 변경하면 모듈 간 동작 불일치

### 1.4 좋은 패턴 (참조용)

#### HttpClientSettings — 모범 사례

```python
# services/http_client.py:124-126
_settings = get_http_client_settings()
self.default_timeout = timeout if timeout is not None else _settings.default_timeout
```

- 생성자 파라미터로 override 가능
- 기본값은 settings에서 가져옴
- 테스트 시 직접 주입 가능

#### PoolMonitorSettings.from_settings() — 모범 사례

```python
# core/pool_monitor.py:146-172
@classmethod
def from_settings(cls, stats_provider=None, settings=None, **overrides):
    from selfhealing.settings.pool_monitor import get_pool_monitor_settings
    s = settings or get_pool_monitor_settings()
    return cls(
        stats_provider=stats_provider,
        warning_threshold=overrides.get("warning_threshold", s.warning_threshold),
        ...
    )
```

- classmethod factory로 settings 바인딩
- 테스트 시 settings mock 가능
- override 우선순위 명확: overrides > settings > default

---

## 2. 개선 계획

### 2.1 Phase 1: Thread Timeout 설정화 (P2)

**목표**: 하드코딩된 thread join/HTTP timeout을 settings로 이동

#### 2.1.1 설정 클래스 정의

```python
# settings/thread_management.py (신규)
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ThreadManagementSettings(BaseSettings):
    """Thread join 및 background worker timeout 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_THREAD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    join_timeout: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="Default timeout for thread.join() calls",
    )

    join_timeout_long: float = Field(
        default=10.0,
        ge=5.0,
        le=120.0,
        description="Timeout for long-running thread joins (event bus, capacity reservation)",
    )


def get_thread_management_settings() -> "ThreadManagementSettings":
    """Root settings 경유 단일 진입점 (SSOT)."""
    from selfhealing.settings.root import get_config

    return get_config().thread


def reset_thread_management_settings() -> None:
    """Root reset으로 위임 (테스트용)."""
    from selfhealing.settings.root import reset_config

    reset_config()
```

> **Q1 결정**: `webhook_timeout`은 HTTP 도메인에 속하므로 `HttpClientSettings`로 이동.
> `ThreadManagementSettings`는 `thread.join()` 전용으로 순수하게 유지한다.

#### 2.1.4 HttpClientSettings에 webhook_timeout 추가

```python
# settings/http_client.py — 기존 클래스에 필드 추가
class HttpClientSettings(BaseSettings):
    # ... 기존 default_timeout 필드 ...

    webhook_timeout: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Timeout for outbound webhook HTTP calls (notifier, etc.)",
    )
```

#### 2.1.2 서비스 코드 수정 예시

```python
# services/event_bus/redis_bus.py — 수정 전
self._thread.join(timeout=10)

# services/event_bus/redis_bus.py — 수정 후
from selfhealing.settings.thread_management import get_thread_management_settings

_settings = get_thread_management_settings()
self._thread.join(timeout=_settings.join_timeout_long)
```

#### 2.1.3 수정 대상 매핑

| 파일 | 현재 값 | 적용 설정 필드 |
|------|--------|---------------|
| `event_bus/redis_bus.py:375` | `timeout=10` | `join_timeout_long` |
| `event_bus/redis_bus.py:376-377` | `timeout=5` | `join_timeout` |
| `capacity_reservation/service.py:186` | `timeout=10` | `join_timeout_long` |
| `capacity_reservation/service.py:257` | `timeout=5` | `join_timeout` |
| `canary/state_refresher.py:142` | `timeout=5.0` | `join_timeout` |
| `chaos/synthetic_load.py:419` | `timeout=5.0` | `join_timeout` |
| `correlation_engine/wildcard_observer.py:210` | `timeout=5.0` | `join_timeout` |
| `namespace_emergency/partition_reconciliation.py:519` | `timeout=5.0` | `join_timeout` |
| `postmortem/notifier.py:419` | `timeout=10` | `HttpClientSettings.webhook_timeout` |
| `postmortem/notifier.py:514` | `timeout=10` | `HttpClientSettings.webhook_timeout` |

---

### 2.2 Phase 2: Detection Window 설정화 (P2)

**목표**: anomaly detection window/threshold를 settings로 이동

#### 2.2.1 설정 클래스 정의

```python
# settings/detection.py (신규)
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DetectionSettings(BaseSettings):
    """Anomaly detection 및 correlation engine 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_DETECTION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    anomaly_window_size: int = Field(
        default=100,
        ge=10,
        le=10000,
        description="Number of data points for anomaly detection sliding window",
    )

    anomaly_zscore_threshold: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="Z-Score threshold for anomaly detection",
    )

    # Q6 결정: Hybrid Window (Count + Time)
    # 트래픽 저하 시 오래된 데이터가 Z-Score 계산에 포함되는 Data Stale 방지
    anomaly_window_max_age_seconds: float = Field(
        default=300.0,
        ge=10.0,
        le=86400.0,
        description="Maximum age (seconds) of data points in anomaly window. "
        "Points older than this are discarded even if window_size not reached.",
    )

    correlation_window_size: int = Field(
        default=100,
        ge=10,
        le=10000,
        description="Number of events for co-occurrence correlation window",
    )


def get_detection_settings() -> "DetectionSettings":
    """Root settings 경유 단일 진입점 (SSOT)."""
    from selfhealing.settings.root import get_config

    return get_config().detection


def reset_detection_settings() -> None:
    """Root reset으로 위임 (테스트용)."""
    from selfhealing.settings.root import reset_config

    reset_config()
```

#### 2.2.2 수정 대상 매핑

| 파일 | 현재 값 | 적용 설정 필드 |
|------|--------|---------------|
| `wildcard_observer.py:137` | `window=100` | `correlation_window_size` |
| `co_occurrence_tracker.py:323` | `window=100` | `correlation_window_size` |
| `anomaly_detector.py:21` | `threshold=3.0, window=100` (docstring 예시) | `anomaly_zscore_threshold`, `anomaly_window_size`, `anomaly_window_max_age_seconds` |

> **참고**: `anomaly_detector.py:21`은 모듈 docstring의 Usage 예시이며 실행 코드가 아니다.
> `ZScoreDetector` 클래스에 `max_age_seconds` 파라미터가 추가되었고,
> `anomaly_window_size`, `anomaly_zscore_threshold`는 `predictive_forecaster/service.py`에서
> 자체 settings(`PredictiveSettings.zscore_threshold`, `zscore_window`)로 이미 제어되고 있다.
> `anomaly_window_max_age_seconds`는 향후 callers에서 `max_age_seconds` 인자로 전달 예정이다.

---

### 2.3 Phase 3: SelfHealingSettings에 통합 (P2)

신규 settings 클래스를 root settings에 추가한다.
`get_thread_management_settings()`와 `get_detection_settings()`는 Root 경유로 동작하므로
Root에 필드가 추가되어야 정상 작동한다.

```python
# settings/root.py
from selfhealing.settings.thread_management import ThreadManagementSettings
from selfhealing.settings.detection import DetectionSettings

class SelfHealingSettings(BaseSettings):
    # ... 기존 18개 필드 ...

    # 신규 (313)
    thread: ThreadManagementSettings = Field(default_factory=ThreadManagementSettings)
    detection: DetectionSettings = Field(default_factory=DetectionSettings)
```

> **Q2 결정**: 기존 108개 독립 싱글톤의 SSOT 마이그레이션은 315 문서에서 다룬다.
> 313에서는 신규 2개만 처음부터 Root 경유 패턴을 적용하여 315의 마이그레이션 템플릿으로 삼는다.

---

### 2.4 Phase 4: 미사용 Settings 정리 (P3)

에이전트 분석에서 발견된 잠재적 미사용 설정:

| 설정 | 사용 현황 | 판정 |
|------|----------|------|
| `ApiViewSettings` | services에서 0건 사용 | 확인 필요 — Django view에서 사용 가능 |
| `DomainSensitivitySettings` | settings 파일 내부에서만 참조 | 확인 필요 — 다른 settings에서 참조 가능 |
| `SlackChannelSettings` | 통합 포인트용 | 유지 — 외부 통합 설정 |

**방침**: 미사용 settings 정리는 `vulture` 등 dead code 탐지 도구 결과 확인 후 진행.
사전 확인 없이 삭제하면 환경변수 기반 사용(코드에 직접 나타나지 않는 경우)을 놓칠 수 있다.

---

## 3. Settings 사용 표준 패턴

### 3.1 표준 패턴 (모든 신규 코드에 적용)

```python
# Pattern: 생성자 파라미터 + settings fallback

class MyService:
    def __init__(
        self,
        timeout: float | None = None,
        window_size: int | None = None,
        *,
        settings: MySettings | None = None,
    ):
        _settings = settings or get_my_settings()
        self._timeout = timeout if timeout is not None else _settings.timeout
        self._window_size = window_size if window_size is not None else _settings.window_size
```

**우선순위**: 생성자 파라미터 > settings 객체 > env var > 기본값

### 3.2 Anti-Pattern (금지)

```python
# BAD: 매직넘버 하드코딩
self._thread.join(timeout=5)

# BAD: 상수를 파일 상단에 정의하지만 settings 연결 없음
DEFAULT_TIMEOUT = 5

# BAD: settings를 모듈 레벨에서 즉시 로딩 (import 시점에 env var 필요)
SETTINGS = get_my_settings()  # 모듈 import 시 실행
```

### 3.3 Checker: AST 기반 하드코딩 탐지 린트 규칙

> **Q4 결정**: 정규식은 주석/문자열 오탐, 멀티라인 미탐 위험이 있으므로 AST 기반으로 교체.

```python
# tests/unit/test_settings_compliance.py

import ast
from pathlib import Path


class HardcodedTimeoutVisitor(ast.NodeVisitor):
    """AST 기반 하드코딩 timeout/window 탐지."""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        for kw in node.keywords:
            if kw.arg in ("timeout", "window") and isinstance(
                kw.value, ast.Constant
            ):
                if isinstance(kw.value.value, (int, float)):
                    self.violations.append(
                        f"{self.filepath}:{node.lineno}: "
                        f"hardcoded {kw.arg}={kw.value.value}"
                    )
        self.generic_visit(node)


def test_no_hardcoded_timeouts_in_services():
    """services/ 디렉토리에서 timeout=N, window=N 형태의 하드코딩을 AST로 탐지."""
    services_dir = Path(
        "packages/selfhealing-python/src/selfhealing/services"
    )
    violations = []

    for py_file in services_dir.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        visitor = HardcodedTimeoutVisitor(py_file.relative_to(services_dir))
        visitor.visit(tree)
        violations.extend(visitor.violations)

    assert not violations, (
        f"Found {len(violations)} hardcoded values in services:\n"
        + "\n".join(violations)
    )
```

> **Known Exceptions**: 313 범위 외에 사전 존재하는 하드코딩은 `_KNOWN_EXCEPTIONS` set으로
> 관리하며, 별도 이슈로 순차 제거한다. 현재 10개 파일이 예외 처리되어 있다.

**AST 방식의 장점**:
- 주석/문자열 내부 코드 무시 (AST는 실행 코드만 파싱)
- Black/Ruff 멀티라인 포매팅에 무관
- `timeout=settings.val`은 `ast.Attribute`이므로 자동 제외

---

## 4. 구현 순서

| Phase | 작업 | 파일 수 | 우선순위 |
|-------|------|---------|----------|
| 1 | `ThreadManagementSettings` 생성 | 1 신규 | P2 |
| 1.1 | Thread timeout 하드코딩 → settings 참조 변경 | 6 수정 | P2 |
| 1.2 | `HttpClientSettings`에 `webhook_timeout` 추가 | 1 수정 | P2 |
| 1.3 | `postmortem/notifier.py` → `HttpClientSettings.webhook_timeout` | 1 수정 | P2 |
| 2 | `DetectionSettings` 생성 (hybrid window 포함) | 1 신규 | P2 |
| 2.1 | Detection window 하드코딩 → settings 참조 변경 | 3 수정 | P2 |
| 2.2 | `anomaly_detector.py`에 시간 상한 eviction 로직 추가 | 1 수정 | P2 |
| 3 | `SelfHealingSettings`에 신규 settings 통합 (Root 경유 SSOT) | 1 수정 | P2 |
| 4 | 미사용 settings 정리 (dead code 도구 기반) | TBD | P3 |
| 5 | AST 기반 하드코딩 탐지 lint 테스트 추가 | 1 신규 | P3 |

---

## 5. 테스트 계획

### 5.1 Settings 동작 검증

```python
# tests/unit/test_thread_management_settings.py
import pytest
from pydantic import ValidationError

from selfhealing.settings.thread_management import (
    ThreadManagementSettings,
    get_thread_management_settings,
    reset_thread_management_settings,
)


class TestThreadManagementSettings:
    # Q3 결정: autouse fixture로 테스트 간 격리 보장
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        reset_thread_management_settings()
        yield
        reset_thread_management_settings()

    def test_default_values(self):
        settings = ThreadManagementSettings()
        assert settings.join_timeout == 5.0
        assert settings.join_timeout_long == 10.0

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("SELFHEALING_THREAD_JOIN_TIMEOUT", "15.0")
        # reset 후 재생성하여 env var 반영
        reset_thread_management_settings()
        settings = get_thread_management_settings()
        assert settings.join_timeout == 15.0

    def test_validation_rejects_negative(self):
        with pytest.raises(ValidationError):
            ThreadManagementSettings(join_timeout=-1)

    def test_validation_rejects_too_large(self):
        with pytest.raises(ValidationError):
            ThreadManagementSettings(join_timeout=999)


class TestDetectionSettings:
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        from selfhealing.settings.detection import reset_detection_settings

        reset_detection_settings()
        yield
        reset_detection_settings()

    def test_default_values(self):
        from selfhealing.settings.detection import DetectionSettings

        settings = DetectionSettings()
        assert settings.anomaly_window_size == 100
        assert settings.anomaly_zscore_threshold == 3.0
        assert settings.anomaly_window_max_age_seconds == 300.0

    def test_env_var_override(self, monkeypatch):
        from selfhealing.settings.detection import (
            DetectionSettings,
            get_detection_settings,
            reset_detection_settings,
        )

        monkeypatch.setenv("SELFHEALING_DETECTION_ANOMALY_WINDOW_SIZE", "200")
        reset_detection_settings()
        settings = get_detection_settings()
        assert settings.anomaly_window_size == 200
```

### 5.2 회귀 테스트

기존 서비스 테스트가 settings 기본값으로 동작하므로, 변경 후에도 동일하게 통과해야 한다.
기본값을 현재 하드코딩 값과 동일하게 설정하여 행동 변경 없음을 보장.

---

## 6. 위험 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| Settings 로딩이 import 시점에 발생하면 테스트 격리 깨짐 | 테스트 간 settings 상태 공유 | lazy getter 패턴 + `reset_*_settings()` + autouse fixture (Q3) |
| env var 미설정 시 기본값 변경 | 운영 환경 동작 변경 | 기본값을 현재 하드코딩 값과 동일하게 유지 |
| 미사용 settings 오삭제 | env var로만 참조하는 settings 유실 | dead code 도구 + 수동 확인 병행 |
| 동적 리로드 미지원 | 런타임 설정 변경 불가 | 현재 불필요 (Q5). `reload_config()` 확장점 존재. 필요 시 315/316에서 대응 |

---

## 7. 기존 Config Migration 문서와의 관계

| 기존 문서 | 범위 | 이 문서와의 관계 |
|----------|------|----------------|
| 90-93 (Config Migration Overview/Inventory/Guide/Checklist) | settings 인프라 구축 | 완료됨 — 이 문서는 인프라 위에서 누락된 적용 처리 |
| 94-107 (Hardcoded Config Refactoring) | 하드코딩 설정값 리팩토링 | 일부 완료 — 이 문서는 thread/detection 영역의 잔여 하드코딩 처리 |

이 문서(313)는 기존 Config Migration 시리즈의 **후속 정리** 성격이며, 새로운 인프라를 도입하지 않고 기존 패턴을 확장 적용한다.

---

## 8. 리뷰 결정 사항

313 초안 리뷰에서 채택된 설계 결정을 아래에 정리한다.

### Q1: webhook_timeout → HttpClientSettings 이동

`postmortem/notifier.py:419`, `:514`의 `timeout=10`은 `urllib.request.urlopen()`의 HTTP 소켓 타임아웃이며
`thread.join()`과 무관하다. DDD 응집도 원칙에 따라 HTTP 도메인인 `HttpClientSettings`로 이동한다.

- **변경**: `ThreadManagementSettings`에서 `webhook_timeout` 필드 제거
- **변경**: `HttpClientSettings`에 `webhook_timeout` 필드 추가 (2.1.4절 참조)
- **매핑 변경**: `postmortem/notifier.py` → `HttpClientSettings.webhook_timeout`

### Q3: 테스트 격리 — reset 함수 + autouse fixture 의무화

기존 프로젝트 패턴(`tests/conftest.py:63-150`)에 따라 모든 신규 settings 모듈에
`reset_*_settings()` 함수를 의무적으로 제공하고, 테스트에 autouse fixture를 적용한다.

### Q4: AST 기반 린트 체커로 강화

정규식(Regex) 기반 하드코딩 탐지는 주석/문자열 오탐 및 멀티라인 미탐 위험이 있으므로
Python `ast` 모듈 기반 `NodeVisitor`로 교체한다 (3.3절 반영 완료).

### Q5: 동적 리로드 — 현재 불필요

현재 정적 캐싱 구조를 유지한다. `root.py`에 `reload_config()`가 이미 존재하므로
향후 필요 시 확장 가능하며, 지금은 YAGNI 원칙을 따른다.

### Q6: Hybrid Window — anomaly_window_max_age_seconds 추가

개수(Count) 기반 윈도우만으로는 트래픽 저하 시 오래된 데이터가 탐지에 포함되는
Data Stale 문제가 발생한다. "최근 N개, 단 M초 이내" 하이브리드 구조를 채택한다.

- **변경**: `DetectionSettings`에 `anomaly_window_max_age_seconds` 필드 추가 (2.2.1절 반영 완료)
- **기본값**: `300.0`초 (`co_occurrence_tracker`의 기존 `window_seconds=300.0`과 통일)

### Q2: Settings SSOT 리팩토링 → 별도 문서

108개 독립 싱글톤의 SSOT 통합은 313의 범위(하드코딩 제거)를 넘으므로 별도 문서로 분리한다.
단, 313에서 신규 생성하는 2개 settings는 처음부터 Root 경유 패턴을 적용한다.

- **315**: Settings SSOT Migration — SharedEnvSource + Facade 패턴
- **316**: Gunicorn Preload Optimization — DevOps 배포 최적화

---

## 9. 관련 문서

| 문서 | 관계 |
|------|------|
| 90-107 | Config Migration 시리즈 (완료된 선행 작업) |
| 309 | 아키텍처 패턴 통일 |
| 315 | Settings SSOT Migration (Q2에서 분리) |
| 316 | Gunicorn Preload Optimization (Q2에서 분리) |

---

## 10. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-06 | 1.0.0 | 초안 작성 |
| 2026-03-07 | 1.1.0 | Q1-Q6 리뷰 결정 반영: webhook_timeout 이동, AST 린트, hybrid window, 테스트 격리 패턴, SSOT 분리(315/316) |
