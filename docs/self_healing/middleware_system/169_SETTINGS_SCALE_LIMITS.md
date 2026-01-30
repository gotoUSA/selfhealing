# 169. 설정 한계값 증가 (Scale Limits)

> **버전**: 1.0.0
> **작성일**: 2026-01-31
> **의존성**: [168_REDIS_BATCH_OPTIMIZATION.md](168_REDIS_BATCH_OPTIMIZATION.md)
> **예상 소요**: 1일

---

## 1. 현재 문제점 (코드 근거)

### 1.1 CascadeRetentionSettings: max_events_per_second 제한

**파일**: `packages/selfhealing-python/src/selfhealing/settings/cascade_retention.py`
**라인**: 120-130

```python
class CascadeRetentionSettings(BaseSettings):
    max_events_per_second: int = Field(
        default=1000,
        ge=1,
        le=10000,  # ❌ 최대 10,000 제한!
        description="Threshold: max audit events/sec",
    )
```

**문제점**:
- 대기업 환경: **초당 100,000+** 이벤트 예상
- 현재 제한: **10,000** → 90% 손실 가능

---

### 1.2 EventBufferSettings: max_events 제한

**파일**: `packages/selfhealing-python/src/selfhealing/settings/event_buffer.py`
**라인**: 35-45

```python
class EventBufferSettings(BaseSettings):
    max_events_per_request: int = Field(
        default=100,
        ge=10,
        le=1000,  # ❌ 최대 1,000 제한!
        description="Per-request buffer limit",
    )
```

**문제점**:
- 복잡한 요청: **10,000+ 이벤트** 발생 가능 (대규모 벌크 작업)
- 현재 제한: **1,000** → 9,000+ 이벤트 손실

---

### 1.3 BatchSettings: async_logger_max_queue_size 제한

**파일**: `packages/selfhealing-python/src/selfhealing/settings/batch.py`
**라인**: 60-80

```python
class BatchSettings(BaseSettings):
    async_logger_max_queue_size: int = Field(
        default=5000,
        ge=100,
        le=100000,  # ✅ 적정 (10만)
        description="AsyncLogger 최대 큐 크기",
    )

    logger_batch_size: int = Field(
        default=10,
        ge=1,
        le=100,  # ❌ 최대 100 제한!
        description="배치 크기",
    )
```

**문제점**:
- `logger_batch_size`: 100으로 제한
- 대량 처리 시: **1,000+ 배치**가 효율적

---

### 1.4 RingBufferSettings: capacity 확인 필요

**파일**: `packages/selfhealing-python/src/selfhealing/audit/ring_buffer.py`
**라인**: 15-30

```python
class RingBufferSettings(BaseSettings):
    capacity: int = Field(
        default=10000,
        ge=100,
        le=1000000,  # ✅ 적정 (100만)
        description="RingBuffer 용량",
    )
```

**현재 상태**: 적정 (100만까지 가능)

---

## 2. 수정 계획

### 2.1 한계값 증가 요약

| 설정 | 현재 최대값 | 권장 최대값 | 이유 |
|-----|-----------|-----------|-----|
| `max_events_per_second` | 10,000 | 1,000,000 | 대기업 요구사항 |
| `max_events_per_request` | 1,000 | 100,000 | 벌크 작업 지원 |
| `logger_batch_size` | 100 | 10,000 | 배치 효율 |
| `async_logger_max_queue_size` | 100,000 | 유지 | 이미 적정 |
| `RingBuffer.capacity` | 1,000,000 | 유지 | 이미 적정 |

---

## 3. 구현 상세

### 3.1 CascadeRetentionSettings 수정

**파일**: `packages/selfhealing-python/src/selfhealing/settings/cascade_retention.py`

**Before**:
```python
max_events_per_second: int = Field(
    default=1000,
    ge=1,
    le=10000,
    description="Threshold: max audit events/sec",
)
```

**After**:
```python
max_events_per_second: int = Field(
    default=10000,  # 기본값 증가
    ge=1,
    le=1000000,  # 100만까지 허용
    description=(
        "초당 최대 감사 이벤트 수 임계값. "
        "대기업 환경에서는 100,000+ 권장. "
        "이 값 초과 시 샘플링 또는 경고 발생."
    ),
)
```

---

### 3.2 EventBufferSettings 수정

**파일**: `packages/selfhealing-python/src/selfhealing/settings/event_buffer.py`

**Before**:
```python
max_events_per_request: int = Field(
    default=100,
    ge=10,
    le=1000,
    description="Per-request buffer limit",
)
```

**After**:
```python
max_events_per_request: int = Field(
    default=1000,  # 기본값 증가
    ge=10,
    le=100000,  # 10만까지 허용
    description=(
        "요청당 최대 감사 이벤트 버퍼 크기. "
        "벌크 작업 시 10,000+ 권장. "
        "RingBuffer 사용 시 이 값은 RingBuffer capacity로 대체됨."
    ),
)
```

---

### 3.3 BatchSettings 수정

**파일**: `packages/selfhealing-python/src/selfhealing/settings/batch.py`

**Before**:
```python
logger_batch_size: int = Field(
    default=10,
    ge=1,
    le=100,
    description="배치 크기",
)
```

**After**:
```python
logger_batch_size: int = Field(
    default=100,  # 기본값 증가
    ge=1,
    le=10000,  # 1만까지 허용
    description=(
        "AsyncHealingLogger 배치 크기. "
        "대량 처리 시 1,000+ 권장. "
        "너무 크면 메모리 사용 증가, 너무 작으면 I/O 증가."
    ),
)

flush_interval: float = Field(
    default=5.0,
    ge=0.1,  # 최소값 감소 (고속 플러시 허용)
    le=60.0,
    description=(
        "배치 플러시 간격 (초). "
        "고속 처리 시 1.0-2.0 권장. "
        "실시간 요구 시 0.5 가능."
    ),
)
```

---

### 3.4 새로운 ScaleSettings 클래스 추가

**파일**: `packages/selfhealing-python/src/selfhealing/settings/scale.py` (신규)

```python
"""
Enterprise Scale Settings.

대기업 환경에서의 대규모 처리를 위한 통합 설정.
"""
from enum import Enum
from pydantic import Field
from pydantic_settings import BaseSettings


class ScaleProfile(str, Enum):
    """사전 정의된 스케일 프로파일."""

    DEVELOPMENT = "development"      # 개발/테스트
    SMALL_BUSINESS = "small"         # 소규모 (1-10 pods)
    MEDIUM_BUSINESS = "medium"       # 중규모 (10-50 pods)
    ENTERPRISE = "enterprise"        # 대기업 (50+ pods)
    HIGH_THROUGHPUT = "high"         # 초고속 (100,000+ RPS)


class ScaleSettings(BaseSettings):
    """
    Enterprise Scale 통합 설정.

    프로파일 선택으로 관련 설정 일괄 조정 가능.
    개별 설정 오버라이드도 지원.
    """

    model_config = {"env_prefix": "SELFHEALING_SCALE_"}

    profile: ScaleProfile = Field(
        default=ScaleProfile.DEVELOPMENT,
        description="스케일 프로파일. 프로파일에 따라 기본값 자동 조정.",
    )

    # Per-Request Limits
    max_events_per_request: int | None = Field(
        default=None,  # 프로파일 기본값 사용
        ge=10,
        le=1000000,
        description="요청당 최대 이벤트 (None이면 프로파일 기본값)",
    )

    # Throughput Limits
    max_events_per_second: int | None = Field(
        default=None,
        ge=100,
        le=10000000,
        description="초당 최대 이벤트 (None이면 프로파일 기본값)",
    )

    # Buffer Sizes
    ring_buffer_capacity: int | None = Field(
        default=None,
        ge=1000,
        le=10000000,
        description="RingBuffer 용량 (None이면 프로파일 기본값)",
    )

    # Batch Settings
    batch_size: int | None = Field(
        default=None,
        ge=1,
        le=100000,
        description="배치 크기 (None이면 프로파일 기본값)",
    )

    flush_interval_seconds: float | None = Field(
        default=None,
        ge=0.1,
        le=60.0,
        description="플러시 간격 (None이면 프로파일 기본값)",
    )

    @property
    def effective_max_events_per_request(self) -> int:
        """프로파일 기반 유효 max_events_per_request."""
        if self.max_events_per_request is not None:
            return self.max_events_per_request
        return PROFILE_DEFAULTS[self.profile]["max_events_per_request"]

    @property
    def effective_max_events_per_second(self) -> int:
        """프로파일 기반 유효 max_events_per_second."""
        if self.max_events_per_second is not None:
            return self.max_events_per_second
        return PROFILE_DEFAULTS[self.profile]["max_events_per_second"]

    @property
    def effective_ring_buffer_capacity(self) -> int:
        """프로파일 기반 유효 ring_buffer_capacity."""
        if self.ring_buffer_capacity is not None:
            return self.ring_buffer_capacity
        return PROFILE_DEFAULTS[self.profile]["ring_buffer_capacity"]

    @property
    def effective_batch_size(self) -> int:
        """프로파일 기반 유효 batch_size."""
        if self.batch_size is not None:
            return self.batch_size
        return PROFILE_DEFAULTS[self.profile]["batch_size"]

    @property
    def effective_flush_interval(self) -> float:
        """프로파일 기반 유효 flush_interval."""
        if self.flush_interval_seconds is not None:
            return self.flush_interval_seconds
        return PROFILE_DEFAULTS[self.profile]["flush_interval"]


# 프로파일별 기본값
PROFILE_DEFAULTS = {
    ScaleProfile.DEVELOPMENT: {
        "max_events_per_request": 100,
        "max_events_per_second": 1000,
        "ring_buffer_capacity": 10000,
        "batch_size": 10,
        "flush_interval": 5.0,
    },
    ScaleProfile.SMALL_BUSINESS: {
        "max_events_per_request": 1000,
        "max_events_per_second": 10000,
        "ring_buffer_capacity": 100000,
        "batch_size": 100,
        "flush_interval": 3.0,
    },
    ScaleProfile.MEDIUM_BUSINESS: {
        "max_events_per_request": 10000,
        "max_events_per_second": 50000,
        "ring_buffer_capacity": 500000,
        "batch_size": 500,
        "flush_interval": 2.0,
    },
    ScaleProfile.ENTERPRISE: {
        "max_events_per_request": 50000,
        "max_events_per_second": 200000,
        "ring_buffer_capacity": 1000000,
        "batch_size": 1000,
        "flush_interval": 1.0,
    },
    ScaleProfile.HIGH_THROUGHPUT: {
        "max_events_per_request": 100000,
        "max_events_per_second": 1000000,
        "ring_buffer_capacity": 5000000,
        "batch_size": 5000,
        "flush_interval": 0.5,
    },
}


# Singleton
_scale_settings: ScaleSettings | None = None


def get_scale_settings() -> ScaleSettings:
    """ScaleSettings 싱글톤 반환."""
    global _scale_settings
    if _scale_settings is None:
        _scale_settings = ScaleSettings()
    return _scale_settings
```

---

## 4. 사용 예시

### 4.1 환경 변수로 프로파일 선택

```bash
# .env 또는 환경 변수
SELFHEALING_SCALE_PROFILE=enterprise
```

### 4.2 코드에서 사용

```python
from selfhealing.settings.scale import get_scale_settings, ScaleProfile

settings = get_scale_settings()

# 프로파일 기반 값 사용
max_events = settings.effective_max_events_per_request
buffer_size = settings.effective_ring_buffer_capacity

print(f"Profile: {settings.profile}")
print(f"Max events/request: {max_events}")
print(f"Buffer size: {buffer_size}")
```

**출력** (enterprise 프로파일):
```
Profile: enterprise
Max events/request: 50000
Buffer size: 1000000
```

### 4.3 개별 값 오버라이드

```bash
# 프로파일은 enterprise지만 특정 값만 오버라이드
SELFHEALING_SCALE_PROFILE=enterprise
SELFHEALING_SCALE_MAX_EVENTS_PER_SECOND=500000  # 기본 200,000 대신 500,000
```

---

## 5. 테스트 계획

### 5.1 단위 테스트

**파일**: `tests/unit/settings/test_scale_settings.py`

```python
import pytest
import os
from unittest.mock import patch


class TestScaleSettings:
    """ScaleSettings 테스트."""

    def test_default_profile_is_development(self):
        """기본 프로파일은 development."""
        from selfhealing.settings.scale import ScaleSettings, ScaleProfile

        settings = ScaleSettings()
        assert settings.profile == ScaleProfile.DEVELOPMENT

    def test_enterprise_profile_defaults(self):
        """enterprise 프로파일 기본값 확인."""
        from selfhealing.settings.scale import ScaleSettings, ScaleProfile

        with patch.dict(os.environ, {"SELFHEALING_SCALE_PROFILE": "enterprise"}):
            settings = ScaleSettings()

        assert settings.profile == ScaleProfile.ENTERPRISE
        assert settings.effective_max_events_per_request == 50000
        assert settings.effective_max_events_per_second == 200000
        assert settings.effective_ring_buffer_capacity == 1000000

    def test_individual_override(self):
        """개별 값 오버라이드."""
        from selfhealing.settings.scale import ScaleSettings

        env = {
            "SELFHEALING_SCALE_PROFILE": "enterprise",
            "SELFHEALING_SCALE_MAX_EVENTS_PER_SECOND": "500000",
        }
        with patch.dict(os.environ, env):
            settings = ScaleSettings()

        # 오버라이드된 값
        assert settings.effective_max_events_per_second == 500000
        # 프로파일 기본값 유지
        assert settings.effective_max_events_per_request == 50000

    def test_high_throughput_profile(self):
        """high_throughput 프로파일."""
        from selfhealing.settings.scale import ScaleSettings, ScaleProfile

        with patch.dict(os.environ, {"SELFHEALING_SCALE_PROFILE": "high"}):
            settings = ScaleSettings()

        assert settings.profile == ScaleProfile.HIGH_THROUGHPUT
        assert settings.effective_max_events_per_second == 1000000
        assert settings.effective_batch_size == 5000
```

---

## 6. 마이그레이션 가이드

### 6.1 기존 설정과의 호환성

기존 설정은 그대로 작동합니다:

```bash
# 기존 방식 (계속 작동)
SELFHEALING_EVENT_BUFFER_MAX_EVENTS_PER_REQUEST=5000
SELFHEALING_CASCADE_RETENTION_MAX_EVENTS_PER_SECOND=50000
```

### 6.2 신규 ScaleSettings로 마이그레이션

```bash
# 신규 방식 (권장)
SELFHEALING_SCALE_PROFILE=enterprise
# 필요시 개별 오버라이드
```

### 6.3 우선순위

1. 개별 환경 변수 (`SELFHEALING_SCALE_MAX_EVENTS_*`)
2. 프로파일 기본값 (`SELFHEALING_SCALE_PROFILE`)
3. 레거시 설정 (호환성용)

---

## 7. 체크리스트

### 7.1 코드 변경

- [ ] `cascade_retention.py`: `le=1000000` 으로 수정
- [ ] `event_buffer.py`: `le=100000` 으로 수정
- [ ] `batch.py`: `le=10000` 으로 수정
- [ ] `settings/scale.py`: 신규 파일 생성
- [ ] `settings/__init__.py`: `ScaleSettings` 내보내기

### 7.2 문서

- [ ] README.md에 프로파일 설명 추가
- [ ] 환경 변수 표 업데이트

### 7.3 테스트

- [ ] 단위 테스트 추가
- [ ] 통합 테스트: 프로파일별 동작 확인

---

## 8. 다음 단계

→ [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md): Kafka Audit 어댑터 구현
