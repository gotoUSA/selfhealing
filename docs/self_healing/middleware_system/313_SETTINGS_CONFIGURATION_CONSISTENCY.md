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
from pydantic_settings import BaseSettings


class ThreadManagementSettings(BaseSettings):
    """Thread join 및 background worker timeout 설정."""

    model_config = {"env_prefix": "SELFHEALING_THREAD_"}

    # Thread join timeout (초)
    join_timeout: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="Default timeout for thread.join() calls",
    )

    # 긴 작업용 thread join timeout (초)
    join_timeout_long: float = Field(
        default=10.0,
        ge=5.0,
        le=120.0,
        description="Timeout for long-running thread joins (event bus, postmortem)",
    )

    # HTTP webhook timeout (초)
    webhook_timeout: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Timeout for HTTP webhook calls (notifier, etc.)",
    )


_settings: ThreadManagementSettings | None = None


def get_thread_management_settings() -> ThreadManagementSettings:
    global _settings
    if _settings is None:
        _settings = ThreadManagementSettings()
    return _settings
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
| `postmortem/notifier.py:419` | `timeout=10` | `webhook_timeout` |
| `postmortem/notifier.py:514` | `timeout=10` | `webhook_timeout` |

---

### 2.2 Phase 2: Detection Window 설정화 (P2)

**목표**: anomaly detection window/threshold를 settings로 이동

#### 2.2.1 설정 클래스 정의

```python
# settings/detection.py (신규)
from pydantic import Field
from pydantic_settings import BaseSettings


class DetectionSettings(BaseSettings):
    """Anomaly detection 및 correlation engine 설정."""

    model_config = {"env_prefix": "SELFHEALING_DETECTION_"}

    # 이상 탐지 윈도우 크기 (데이터 포인트 수)
    anomaly_window_size: int = Field(
        default=100,
        ge=10,
        le=10000,
        description="Number of data points for anomaly detection sliding window",
    )

    # Z-Score 임계값
    anomaly_zscore_threshold: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="Z-Score threshold for anomaly detection",
    )

    # Correlation window
    correlation_window_size: int = Field(
        default=100,
        ge=10,
        le=10000,
        description="Number of events for co-occurrence correlation window",
    )


_settings: DetectionSettings | None = None


def get_detection_settings() -> DetectionSettings:
    global _settings
    if _settings is None:
        _settings = DetectionSettings()
    return _settings
```

#### 2.2.2 수정 대상 매핑

| 파일 | 현재 값 | 적용 설정 필드 |
|------|--------|---------------|
| `wildcard_observer.py:137` | `window=100` | `correlation_window_size` |
| `co_occurrence_tracker.py:323` | `window=100` | `correlation_window_size` |
| `anomaly_detector.py:21` | `threshold=3.0, window=100` | `anomaly_zscore_threshold`, `anomaly_window_size` |

---

### 2.3 Phase 3: SelfHealingSettings에 통합 (P3)

새 settings 클래스를 root settings에 추가.

```python
# settings/root.py
class SelfHealingSettings(BaseSettings):
    # ... 기존 필드 ...

    # 신규
    thread: ThreadManagementSettings = Field(default_factory=ThreadManagementSettings)
    detection: DetectionSettings = Field(default_factory=DetectionSettings)
```

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

### 3.3 Checker: 하드코딩 탐지 린트 규칙

```python
# tests/unit/test_settings_compliance.py

import ast
import re
from pathlib import Path


def test_no_hardcoded_timeouts_in_services():
    """services/ 디렉토리에서 timeout=N 형태의 하드코딩을 탐지."""
    services_dir = Path(
        "packages/selfhealing-python/src/selfhealing/services"
    )
    violations = []

    for py_file in services_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        # timeout=숫자 패턴 (settings에서 가져오는 경우 제외)
        matches = re.finditer(
            r'(?:\.join|requests?\.\w+|urllib)\s*\([^)]*timeout\s*=\s*(\d+\.?\d*)',
            content,
        )
        for match in matches:
            violations.append(
                f"{py_file.relative_to(services_dir)}:{match.start()}: "
                f"hardcoded timeout={match.group(1)}"
            )

    assert not violations, (
        f"Found {len(violations)} hardcoded timeouts in services:\n"
        + "\n".join(violations)
    )
```

---

## 4. 구현 순서

| Phase | 작업 | 파일 수 | 우선순위 |
|-------|------|---------|----------|
| 1 | `ThreadManagementSettings` 생성 | 1 신규 | P2 |
| 1.1 | Thread timeout 하드코딩 → settings 참조 변경 | 8 수정 | P2 |
| 2 | `DetectionSettings` 생성 | 1 신규 | P2 |
| 2.1 | Detection window 하드코딩 → settings 참조 변경 | 3 수정 | P2 |
| 3 | `SelfHealingSettings`에 신규 settings 통합 | 1 수정 | P3 |
| 4 | 미사용 settings 정리 (dead code 도구 기반) | TBD | P3 |
| 5 | 하드코딩 탐지 lint 테스트 추가 | 1 신규 | P3 |

---

## 5. 테스트 계획

### 5.1 Settings 동작 검증

```python
# tests/unit/test_thread_management_settings.py

class TestThreadManagementSettings:
    def test_default_values(self):
        settings = ThreadManagementSettings()
        assert settings.join_timeout == 5.0
        assert settings.join_timeout_long == 10.0
        assert settings.webhook_timeout == 10.0

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("SELFHEALING_THREAD_JOIN_TIMEOUT", "15.0")
        settings = ThreadManagementSettings()
        assert settings.join_timeout == 15.0

    def test_validation_rejects_negative(self):
        with pytest.raises(ValidationError):
            ThreadManagementSettings(join_timeout=-1)

    def test_validation_rejects_too_large(self):
        with pytest.raises(ValidationError):
            ThreadManagementSettings(join_timeout=999)


class TestDetectionSettings:
    def test_default_values(self):
        settings = DetectionSettings()
        assert settings.anomaly_window_size == 100
        assert settings.anomaly_zscore_threshold == 3.0

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("SELFHEALING_DETECTION_ANOMALY_WINDOW_SIZE", "200")
        settings = DetectionSettings()
        assert settings.anomaly_window_size == 200
```

### 5.2 회귀 테스트

기존 서비스 테스트가 settings 기본값으로 동작하므로, 변경 후에도 동일하게 통과해야 한다.
기본값을 현재 하드코딩 값과 동일하게 설정하여 행동 변경 없음을 보장.

---

## 6. 위험 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| Settings 로딩이 import 시점에 발생하면 테스트 격리 깨짐 | 테스트 간 settings 상태 공유 | lazy getter 패턴 사용, 테스트 fixture에서 global reset |
| env var 미설정 시 기본값 변경 | 운영 환경 동작 변경 | 기본값을 현재 하드코딩 값과 동일하게 유지 |
| 미사용 settings 오삭제 | env var로만 참조하는 settings 유실 | dead code 도구 + 수동 확인 병행 |

---

## 7. 기존 Config Migration 문서와의 관계

| 기존 문서 | 범위 | 이 문서와의 관계 |
|----------|------|----------------|
| 90-93 (Config Migration Overview/Inventory/Guide/Checklist) | settings 인프라 구축 | 완료됨 — 이 문서는 인프라 위에서 누락된 적용 처리 |
| 94-107 (Hardcoded Config Refactoring) | 하드코딩 설정값 리팩토링 | 일부 완료 — 이 문서는 thread/detection 영역의 잔여 하드코딩 처리 |

이 문서(313)는 기존 Config Migration 시리즈의 **후속 정리** 성격이며, 새로운 인프라를 도입하지 않고 기존 패턴을 확장 적용한다.

---

## 8. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-06 | 1.0.0 | 초안 작성 |
