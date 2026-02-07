# 199. 중복 클래스명 수정 계획 (198번 후속)

> **상태**: 📋 계획
> **선행 문서**: 198번 (DegradationLevel, FallbackResult, CircuitBreakerConfig 완료)
> **목적**: 198번에서 다루지 않은 나머지 동명이의 클래스를 해소한다.

---

## 1. `CheckpointError(Exception)` — 완전 중복

### 1-1. 현황

| 구분 | 위치 | 내용 |
|------|------|------|
| **A** | `audit/checkpoint_manager.py:98` | `class CheckpointError(Exception): pass` |
| **B** | `audit/checkpoint_strategy.py:123` | `class CheckpointError(Exception): pass` |

```python
# --- A: audit/checkpoint_manager.py L98-100 ---
class CheckpointError(Exception):
    """체크포인트 관련 에러."""
    pass

# --- B: audit/checkpoint_strategy.py L123-125 ---
class CheckpointError(Exception):
    """체크포인트 관련 에러."""
    pass
```

### 1-2. 문제점

- 동일 패키지(`audit/`) 내 두 파일에 **완전히 같은** 예외 클래스가 중복 정의됨.
- `checkpoint_strategy.py`에는 `CheckpointCorruptedError(CheckpointError)` 서브클래스가 존재(L130).
- `checkpoint_manager.py`에서 정의한 `CheckpointError`와는 별개의 클래스이므로 `except CheckpointError`가 양쪽을 동시에 잡지 못함.

### 1-3. 수정 방향

1. `audit/checkpoint_strategy.py`의 `CheckpointError`를 **단일 소스**로 지정.
2. `audit/checkpoint_manager.py`에서는 `from .checkpoint_strategy import CheckpointError`로 재사용.
3. `CheckpointCorruptedError`도 같은 모듈에 유지.

---

## 2. `CircuitState(Enum)` — 베이스 클래스 불일치

### 2-1. 현황

| 구분 | 위치 | 베이스 | 멤버 |
|------|------|--------|------|
| **A** | `audit/resilience/circuit_breaker.py:19` | `Enum` | `CLOSED`, `OPEN`, `HALF_OPEN` |
| **B** | `audit/graceful_degradation/enums.py:39` | `str, Enum` | `CLOSED`, `OPEN`, `HALF_OPEN` |

```python
# --- A: audit/resilience/circuit_breaker.py L19-22 ---
class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

# --- B: audit/graceful_degradation/enums.py L39-42 ---
class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
```

### 2-2. 문제점

- 멤버와 값은 동일하나 `str` mixin 유무가 다름.
- `str, Enum`은 `json.dumps(state)` 시 자동 직렬화, plain `Enum`은 `.value` 필요.
- 두 모듈 모두 같은 `audit/` 하위에서 서킷 브레이커 상태를 표현.

### 2-3. 수정 방향

1. `audit/graceful_degradation/enums.py`의 `CircuitState(str, Enum)`을 **단일 소스**로 지정.
2. `audit/resilience/circuit_breaker.py`에서 `from ..graceful_degradation.enums import CircuitState`.
3. `str, Enum`으로 통일(JSON 직렬화 호환).

---

## 3. `HashChainWALEntry` — 필드 불일치

### 3-1. 현황

| 구분 | 위치 | 고유 필드 |
|------|------|-----------|
| **A** | `audit/hash_chain_safety.py:194` | `expected_hash`, `status`("PENDING"/"COMMITTED"/"ABORTED") |
| **B** | `audit/graceful_degradation/wal_recovery.py:24` | `pod_id`, `committed`(bool) |

```python
# --- A: audit/hash_chain_safety.py L194-203 ---
@dataclass
class HashChainWALEntry:
    """WAL entry for hash chain operations."""
    sequence: int
    operation: str  # "WRITE", "ANCHOR", "RECONCILE"
    entry_data: dict[str, Any]
    expected_hash: str
    timestamp: str
    status: str = "PENDING"

# --- B: audit/graceful_degradation/wal_recovery.py L24-30 ---
@dataclass
class HashChainWALEntry:
    """WAL entry for hash chain operation."""
    sequence: int
    operation: str  # "add_integrity", "commit", "abort"
    entry_data: dict[str, Any]
    timestamp: str
    pod_id: str
    committed: bool = False
```

### 3-2. 문제점

- 같은 이름이지만 **필드 구성이 다름** (`expected_hash`/`status` vs `pod_id`/`committed`).
- `operation` 값 셋도 완전히 다름 (`"WRITE"/"ANCHOR"/"RECONCILE"` vs `"add_integrity"/"commit"/"abort"`).
- 용도가 다른 두 WAL 엔트리를 혼동할 수 있음.

### 3-3. 수정 방향

| 현재 이름 | 변경 이름 | 사유 |
|-----------|-----------|------|
| A: `HashChainWALEntry` | `HashChainSafetyWALEntry` | hash_chain_safety 모듈 전용 |
| B: `HashChainWALEntry` | `HashChainRecoveryWALEntry` | wal_recovery 모듈 전용 |

---

## 4. `ForensicSettings` — 패러다임 충돌

### 4-1. 현황

| 구분 | 위치 | 타입 | 필드 |
|------|------|------|------|
| **A** | `config.py:66` | `@dataclass(frozen=True)` | `max_stack_frames`, `max_stacktrace_length`, `mask_sensitive_fields` 등 |
| **B** | `settings/forensic.py:27` | `BaseSettings` | `error_message_max_length`, `response_body_max_length` 등 |

```python
# --- A: config.py L65-66 ---
@dataclass(frozen=True)
class ForensicSettings:
    """Forensic context configuration."""
    max_stack_frames: int = 50
    max_stacktrace_length: int = 10000
    max_context_size_bytes: int = 65536
    ...

# --- B: settings/forensic.py L27-42 ---
class ForensicSettings(BaseSettings):
    """Forensic context truncation limits with validation."""
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_FORENSIC_",
        ...
    )
    error_message_max_length: int = Field(default=500, ...)
    response_body_max_length: int = Field(default=5000, ...)
    ...
```

### 4-2. 문제점

- 동일 이름 `ForensicSettings`이 구현체가 완전히 다름.
- `from selfhealing.config import ForensicSettings`와 `from selfhealing.settings.forensic import ForensicSettings`가 서로 다른 클래스.
- 필드도 전혀 다름 — 하나는 스택 프레임/마스킹 설정, 다른 하나는 truncation 한계.

### 4-3. 수정 방향

| 현재 이름 | 변경 이름 | 사유 |
|-----------|-----------|------|
| A: `config.py` `ForensicSettings` | `ForensicContextConfig` | 포렌식 **컨텍스트 수집** 설정 |
| B: `settings/forensic.py` `ForensicSettings` | (유지) | 환경변수 기반 **표준 Settings** |

---

## 5. `StorageMode(Enum)` — 용도 불일치

### 5-1. 현황

| 구분 | 위치 | 베이스 | 멤버 |
|------|------|--------|------|
| **A** | `services/factory/base.py:49` | `str, Enum` | `MEMORY`, `LAYERED`, `DJANGO` |
| **B** | `adapters/resilient/backend.py:27` | `Enum` | `REDIS`, `DEGRADED`, `RECOVERING` |

```python
# --- A: services/factory/base.py L49-52 ---
class StorageMode(str, Enum):
    """저장소 모드."""
    MEMORY = "memory"
    LAYERED = "layered"
    DJANGO = "django"

# --- B: adapters/resilient/backend.py L27-30 ---
class StorageMode(Enum):
    """Storage operation mode."""
    REDIS = "redis"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
```

### 5-2. 문제점

- 이름만 같고 **의미·멤버·베이스 클래스** 전부 다름.
- A는 서비스 팩토리의 영속성 전략 선택, B는 Resilient Storage의 런타임 운영 모드.

### 5-3. 수정 방향

| 현재 이름 | 변경 이름 | 사유 |
|-----------|-----------|------|
| A: `StorageMode` | (유지) | 팩토리 인터페이스로 외부 노출 |
| B: `StorageMode` | `ResilientStorageMode` | Resilient Backend 전용 운영 모드 |

---

## 6. `EventType(Enum)` — 도메인 불일치

### 6-1. 현황

| 구분 | 위치 | 베이스 | 멤버 수 |
|------|------|--------|---------|
| **A** | `services/event_bus.py:57` | `Enum` | 20+ (시스템 이벤트) |
| **B** | `core/decision_logger.py:35` | `str, Enum` | 3 (결정 경계 이벤트) |

```python
# --- A: services/event_bus.py L57-59 ---
class EventType(Enum):
    """Self-Healing 시스템 이벤트 타입."""
    EMERGENCY_LEVEL_CHANGED = "emergency_level_changed"
    ...

# --- B: core/decision_logger.py L35-38 ---
class EventType(str, Enum):
    """Decision boundary event types."""
    ENTER_PRE_DECISION_ZONE = "ENTER_PRE_DECISION_ZONE"
    INTERVENTION_EVALUATED = "INTERVENTION_EVALUATED"
    EXIT_PRE_DECISION_ZONE = "EXIT_PRE_DECISION_ZONE"
```

### 6-2. 수정 방향

| 현재 이름 | 변경 이름 | 사유 |
|-----------|-----------|------|
| A: `EventType` | (유지) | 전역 이벤트 버스의 핵심 타입 |
| B: `EventType` | `DecisionBoundaryEventType` | 결정 로거 전용 |

---

## 7. `NotificationChannel(str, Enum)` — 멤버 불일치

### 7-1. 현황

| 구분 | 위치 | 멤버 |
|------|------|------|
| **A** | `interfaces/notification.py:55` | `SLACK`, `TEAMS`, `PAGERDUTY`, `EMAIL`, `WEBHOOK`, `SMS`, `STDOUT`, `FILE` |
| **B** | `services/security_notification/models.py:75` | `SLACK`, `EMAIL`, `SMS`, `PAGERDUTY` |

```python
# --- A: interfaces/notification.py L55-63 ---
class NotificationChannel(str, Enum):
    """Notification delivery channels."""
    SLACK = "slack"
    TEAMS = "teams"
    PAGERDUTY = "pagerduty"
    EMAIL = "email"
    WEBHOOK = "webhook"
    SMS = "sms"
    STDOUT = "stdout"
    FILE = "file"

# --- B: services/security_notification/models.py L75-79 ---
class NotificationChannel(str, Enum):
    """Available notification channels."""
    SLACK = "slack"
    EMAIL = "email"
    SMS = "sms"
    PAGERDUTY = "pagerduty"
```

### 7-2. 문제점

- A는 전체 알림 인터페이스(8개 채널), B는 보안 알림 전용(4개 채널).
- B가 A의 부분집합이지만 별도 정의되어 있어 추후 채널 추가 시 동기화 누락 위험.

### 7-3. 수정 방향

1. `interfaces/notification.py`의 `NotificationChannel`을 **단일 소스**로 유지.
2. `services/security_notification/models.py`에서 `from selfhealing.interfaces.notification import NotificationChannel`.
3. 보안 알림에서 사용 가능한 채널 제한은 **검증 로직**으로 처리 (Enum 자체를 분리하지 않음).

---

## 수정 순서 (권장)

| 순서 | 대상 | 위험도 | 사유 |
|------|------|--------|------|
| 1 | `CheckpointError` 중복 제거 | 낮음 | 같은 디렉토리 내 단순 중복 |
| 2 | `NotificationChannel` 단일 소스 | 낮음 | 부분집합 통합 |
| 3 | `CircuitState` 통합 | 중간 | `str, Enum`으로 통일 필요 |
| 4 | `HashChainWALEntry` 이름 변경 | 중간 | 필드 차이 큼 |
| 5 | `EventType` 이름 변경 | 중간 | event_bus 의존 범위 파악 필요 |
| 6 | `ForensicSettings` 이름 변경 | 높음 | config.py 전역 사용 |
| 7 | `StorageMode` 이름 변경 | 높음 | adapters 전체 영향 확인 필요 |
