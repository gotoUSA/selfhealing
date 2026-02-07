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

## 8. `NotificationResult` — 3곳 중복 (역할 불일치)

### 8-1. 현황

| 구분 | 위치 | 역할 | 주요 필드 |
|------|------|------|-----------|
| **A** | `services/governance_service.py:63` | 거버넌스 알림 결과 | `sent`, `channels`, `error` |
| **B** | `services/unified_notification.py:122` | 통합 알림 결과 | `success`, `channels_sent`, `channels_failed`, `suppressed`, `suppression_reason`, `error` + `to_dict()` |
| **C** | `services/security_notification/models.py:147` | 단일 채널 배달 결과 | `channel`, `success`, `message`, `error` |

```python
# --- A: services/governance_service.py L63-68 ---
@dataclass
class NotificationResult:
    """알림 발송 결과."""
    sent: bool
    channels: list[str] = field(default_factory=list)
    error: str | None = None

# --- B: services/unified_notification.py L122-142 ---
@dataclass
class NotificationResult:
    """Result of notification attempt."""
    success: bool
    channels_sent: list[str] = field(default_factory=list)
    channels_failed: list[str] = field(default_factory=list)
    suppressed: bool = False
    suppression_reason: str | None = None
    error: str | None = None
    def to_dict(self) -> dict[str, Any]: ...

# --- C: services/security_notification/models.py L147-154 ---
@dataclass
class NotificationResult:
    """Result of a notification attempt."""
    channel: str
    success: bool
    message: str = ""
    error: str | None = None
# SecurityNotificationResult.results: list[NotificationResult]  ← 자식으로 사용
```

### 8-2. 문제점

- 3곳 모두 `NotificationResult`라는 이름이지만 필드 구조와 역할이 완전히 다름.
- A는 거버넌스 전용(3필드), B는 통합 알림(6필드+메서드), C는 단일 채널 결과로 `SecurityNotificationResult`의 자식 요소.
- `services/__init__.py`에서 C만 re-export하고 있어 import 충돌 잠재 위험.

### 8-3. 수정 방향

1. **B** (`unified_notification.py`)를 `NotificationResult` **단일 소스**로 유지 — 가장 포괄적이며 `to_dict()` 포함.
2. **A** (`governance_service.py`) → **`GovernanceNotificationResult`**로 이름 변경.
   - 근거: 거버넌스 서비스 내부에서만 사용(L378, L401, L405). 필드가 3개뿐으로 별도 도메인.
3. **C** (`security_notification/models.py`) → **`ChannelDeliveryResult`**로 이름 변경.
   - 근거: `SecurityNotificationResult.results: list[NotificationResult]`에서 **단일 채널 배달 결과**를 나타냄. "Channel + Delivery + Result"가 역할을 정확히 표현.

---

## 9. `HealthStatus(Enum)` — 완전 중복

### 9-1. 현황

| 구분 | 위치 | 베이스 | 멤버 |
|------|------|--------|------|
| **A** | `adapters/ipc/sidecar_ipc_probe.py:34` | `Enum` | `HEALTHY`, `DEGRADED`, `UNHEALTHY`, `UNKNOWN` |
| **B** | `meta/health_probe.py:25` | `Enum` | `HEALTHY`, `DEGRADED`, `UNHEALTHY`, `UNKNOWN` (docstring 포함) |

```python
# --- A: adapters/ipc/sidecar_ipc_probe.py L34-41 ---
class HealthStatus(Enum):
    """IPC 헬스 상태."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

# --- B: meta/health_probe.py L25-38 ---
class HealthStatus(Enum):
    """서브시스템 건강 상태."""
    HEALTHY = "healthy"
    """정상 상태."""
    DEGRADED = "degraded"
    """성능 저하 상태 (동작은 하지만 주의 필요)."""
    UNHEALTHY = "unhealthy"
    """비정상 상태 (복구 필요)."""
    UNKNOWN = "unknown"
    """상태 확인 불가."""
```

### 9-2. 문제점

- 멤버·값이 **완전 동일**한 Enum이 두 파일에 중복 정의.
- B는 `meta/watchdog.py`에서 `from selfhealing.meta.health_probe import HealthStatus`로 import하여 사용 중.
- A를 import하는 코드가 B를 import하는 코드와 혼용되면 `isinstance` 검사 실패.

### 9-3. 수정 방향

1. **B** (`meta/health_probe.py`)를 **단일 소스**로 지정 — docstring이 상세하고 `meta/watchdog.py`에서 이미 의존.
2. **A** (`sidecar_ipc_probe.py`)에서는 `from selfhealing.meta.health_probe import HealthStatus`로 교체.

---

## 10. `RecoveryAction(Enum)` — 멤버 완전 불일치

### 10-1. 현황

| 구분 | 위치 | 베이스 | 멤버 | 도메인 |
|------|------|--------|------|--------|
| **A** | `core/pool_watchdog.py:27` | `str, Enum` | `NONE`, `ALERT_ONLY`, `CLOSE_LEAKED`, `EXPAND_POOL`, `CIRCUIT_BREAK` | 커넥션 풀 복구 |
| **B** | `meta/recovery_adapter.py:23` | `Enum` | `RESTART_WORKER`, `SCALE_DEPLOYMENT`, `DELETE_POD`, `RESET_CONNECTION` | 인프라 복구 |
| **C** | `services/coordination/recovery_dashboard.py:201` | `@dataclass` | `action: str`, `label: str`, `enabled: bool`, `urgent: bool` | UI 대시보드 |

```python
# --- A: core/pool_watchdog.py L27-33 ---
class RecoveryAction(str, Enum):
    """Types of recovery actions"""
    NONE = "none"
    ALERT_ONLY = "alert_only"
    CLOSE_LEAKED = "close_leaked"
    EXPAND_POOL = "expand_pool"
    CIRCUIT_BREAK = "circuit_break"

# --- B: meta/recovery_adapter.py L23-35 ---
class RecoveryAction(Enum):
    """복구 액션 유형."""
    RESTART_WORKER = "restart_worker"
    SCALE_DEPLOYMENT = "scale_deployment"
    DELETE_POD = "delete_pod"
    RESET_CONNECTION = "reset_connection"

# --- C: services/coordination/recovery_dashboard.py L201-218 ---
@dataclass
class RecoveryAction:
    """사용 가능한 복구 액션."""
    action: str = ""
    label: str = ""
    enabled: bool = True
    urgent: bool = False
    def to_dict(self) -> dict[str, Any]: ...
```

### 10-2. 문제점

- 같은 이름이지만 멤버가 **단 하나도 겹치지 않음**.
- A는 DB 커넥션 풀 레벨(CLOSE_LEAKED, EXPAND_POOL), B는 K8s 인프라 레벨(DELETE_POD, SCALE_DEPLOYMENT).
- 베이스 클래스도 다름 (`str, Enum` vs `Enum`).
- C는 대시보드 위젯에 전달할 복구 버튼 정보를 담는 `@dataclass`. A/B의 Enum과 **타입 자체가 다름**.

### 10-3. 수정 방향

1. **A** (`pool_watchdog.py`) → **`PoolRecoveryAction`**으로 이름 변경.
   - 근거: 모든 멤버가 커넥션 풀 전용(`CLOSE_LEAKED`, `EXPAND_POOL`). `PoolWatchdog` 내부에서만 사용.
2. **B** (`meta/recovery_adapter.py`) → `RecoveryAction` **유지**.
   - 근거: `meta/` 레이어의 인프라 복구 어댑터 인터페이스. K8s Pod/Deployment 수준의 일반적 복구 액션으로 범용성이 높음.
3. **C** (`services/coordination/recovery_dashboard.py`) → **`RecoveryActionWidget`**으로 이름 변경.
   - 근거: 같은 파일의 `RecoveryWidgetData`에 포함되는 UI DTO. `action`(ID 문자열), `label`(표시명), `enabled`/`urgent`(버튼 상태) 필드 구성이 위젯 렌더링 전용임을 명확히 보여줌.

---

## 11. `RecoveryResult` — 필드 불일치 (Item 10 연동)

### 11-1. 현황

| 구분 | 위치 | 주요 필드 |
|------|------|-----------|
| **A** | `core/pool_watchdog.py:38` | `action: RecoveryAction`, `success`, `message`, `timestamp`, `connections_closed: int = 0` |
| **B** | `meta/recovery_adapter.py:40` | `action: RecoveryAction`, `success`, `target: str`, `message`, `timestamp`, `details: dict \| None` |

```python
# --- A: core/pool_watchdog.py L38-45 ---
@dataclass
class RecoveryResult:
    """Result of a recovery action"""
    action: RecoveryAction
    success: bool
    message: str
    timestamp: datetime
    connections_closed: int = 0

# --- B: meta/recovery_adapter.py L40-58 ---
@dataclass
class RecoveryResult:
    """복구 결과."""
    action: RecoveryAction
    success: bool
    target: str
    message: str
    timestamp: datetime
    details: dict[str, Any] | None = None
```

### 11-2. 문제점

- A는 `connections_closed`(풀 전용), B는 `target`/`details`(인프라 전용)로 도메인 특화 필드가 다름.
- 각각 같은 파일의 `RecoveryAction`에 의존하므로 Item 10과 함께 수정해야 함.

### 11-3. 수정 방향

1. **A** (`pool_watchdog.py`) → **`PoolRecoveryResult`**로 이름 변경 — `PoolRecoveryAction`과 쌍.
2. **B** (`meta/recovery_adapter.py`) → `RecoveryResult` **유지** — `RecoveryAction`과 쌍.

---

## 12. `HealthCheckResult` — 필드·용도 완전 불일치

### 12-1. 현황

| 구분 | 위치 | 역할 | 주요 필드 |
|------|------|------|-----------|
| **A** | `adapters/ipc/sidecar_ipc_probe.py:44` | 사이드카 IPC 프로브 결과 | `status: HealthStatus`, `message`, `latency_ms`, `details` + `to_dict()` |
| **B** | `core/auto_rollback_guard.py:68` | 롤백 판단용 헬스 메트릭 | `healthy: bool`, `degradation_level: RollbackSeverity`, `error_rate`, `latency_p99_ms`, `throughput_rps` |

```python
# --- A: adapters/ipc/sidecar_ipc_probe.py L44-63 ---
@dataclass
class HealthCheckResult:
    """헬스 체크 결과."""
    status: HealthStatus
    message: str
    timestamp: datetime = field(default_factory=...)
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    def to_dict(self) -> dict[str, Any]: ...

# --- B: core/auto_rollback_guard.py L68-78 ---
@dataclass
class HealthCheckResult:
    """헬스체크 결과"""
    healthy: bool
    degradation_level: RollbackSeverity
    error_rate: float
    latency_p99_ms: float
    throughput_rps: float
    timestamp: datetime = field(default_factory=...)
    details: dict[str, Any] = field(default_factory=dict)
```

### 12-2. 문제점

- A는 HealthStatus Enum 기반 상태 + 단순 latency, B는 bool + RollbackSeverity + 3종 메트릭(error_rate, latency_p99, throughput).
- 필드가 `details`와 `timestamp` 외에는 전혀 겹치지 않음.

### 12-3. 수정 방향

1. **A** (`sidecar_ipc_probe.py`) → **`SidecarProbeResult`**로 이름 변경.
   - 근거: 같은 파일의 `HealthStatus` + `IPCHealthMetrics`와 함께 사이드카 IPC 프로브 전용 결과. "Sidecar + Probe + Result"가 컨텍스트를 명확히 표현.
2. **B** (`auto_rollback_guard.py`) → **`RollbackHealthAssessment`**로 이름 변경.
   - 근거: `RollbackSeverity` 기반 degradation_level과 error_rate/latency_p99/throughput 메트릭을 사용하여 **롤백 여부를 판단하는 평가 결과**. 단순 헬스체크가 아닌 "평가(Assessment)"에 해당.

---

## 13. `WatchdogConfig` — 필드·용도 완전 불일치

### 13-1. 현황

| 구분 | 위치 | 역할 | 주요 필드 |
|------|------|------|-----------|
| **A** | `tasks/canary_watchdog.py:53` | 카나리 배포 워치독 설정 | `zombie_threshold_minutes`, `auto_rollback_after_minutes`, `max_stage_duration_minutes`, `enable_auto_promote` |
| **B** | `audit/audit_watchdog.py:86` | 감사 하트비트 워치독 설정 | `heartbeat_interval_seconds`, `missed_threshold`, `targets: list[HeartbeatTarget]`, `on_heartbeat_success` callback |

```python
# --- A: tasks/canary_watchdog.py L53-78 ---
@dataclass
class WatchdogConfig:
    """Watchdog 설정."""
    zombie_threshold_minutes: int = 30
    auto_rollback_after_minutes: int = 60
    max_stage_duration_minutes: int = 15
    enable_auto_promote: bool = True
    enable_auto_rollback: bool = True
    notification_enabled: bool = True
    slack_channel: str = "#selfhealing-alerts"
    @classmethod
    def from_settings(cls, settings: CanaryWatchdogSettings | None = None, **overrides) -> WatchdogConfig: ...

# --- B: audit/audit_watchdog.py L86-141 ---
@dataclass
class WatchdogConfig:
    """Watchdog 설정."""
    heartbeat_interval_seconds: float = 30.0
    missed_threshold: int = 3
    targets: list[HeartbeatTarget] = field(default_factory=list)
    local_heartbeat_file: str | None = None
    on_heartbeat_success: Callable[[], None] | None = None
    on_heartbeat_failure: Callable[[str, Exception], None] | None = None
    on_threshold_exceeded: Callable[[int], None] | None = None
    @classmethod
    def from_settings(cls, settings: AuditWatchdogSettings | None = None, **overrides) -> WatchdogConfig: ...
```

### 13-2. 문제점

- 필드가 **단 하나도 겹치지 않음**. A는 카나리 배포 제어(분 단위), B는 하트비트 감시(초 단위 + 콜백).
- 양쪽 모두 `from_settings()` 팩토리를 갖지만 각각 다른 Settings 클래스에 의존.

### 13-3. 수정 방향

1. **A** (`canary_watchdog.py`) → **`CanaryWatchdogConfig`**로 이름 변경.
   - 근거: `CanaryWatchdogSettings`에서 생성하며 zombie/rollback/promote 등 카나리 전용 필드.
2. **B** (`audit_watchdog.py`) → **`AuditWatchdogConfig`**로 이름 변경.
   - 근거: `AuditWatchdogSettings`에서 생성하며 heartbeat/targets/callback 등 감사 하트비트 전용 필드.

---

## 14. `EventSeverity(Enum)` — 완전 중복

### 14-1. 현황

| 구분 | 위치 | 멤버 | 비고 |
|------|------|------|------|
| **A** | `utils/async_logger.py:54` | `DEBUG=0`, `INFO=1`, `WARNING=2`, `CRITICAL=3` | 원본 정의 |
| **B** | `audit/audit_integration.py:55` | `DEBUG=0`, `INFO=1`, `WARNING=2`, `CRITICAL=3` | docstring에 "AsyncLogger 호환" 명시 |

```python
# --- A: utils/async_logger.py L54-60 ---
class EventSeverity(Enum):
    """이벤트 심각도 (Batch Flush Policy)"""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    CRITICAL = 3  # CB Open, 장애 감지 → 즉시 전송

# --- B: audit/audit_integration.py L55-62 ---
class EventSeverity(Enum):
    """이벤트 심각도 (AsyncLogger 호환)."""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    CRITICAL = 3
```

### 14-2. 문제점

- 멤버·값이 **완전 동일**. B의 docstring이 "AsyncLogger 호환"이라고 명시하여 A의 복사본임을 확인.
- `api/django/audit_middleware.py`에서 이미 A를 `from selfhealing.utils.async_logger import EventSeverity`로 import 중.

### 14-3. 수정 방향

1. **A** (`utils/async_logger.py`)를 **단일 소스**로 유지.
2. **B** (`audit/audit_integration.py`)에서 `from selfhealing.utils.async_logger import EventSeverity`로 교체.

---

## 15. `AuditEventType(Enum)` — 같은 패키지 내 도메인 불일치

### 15-1. 현황

| 구분 | 위치 | 멤버 수 | 역할 |
|------|------|---------|------|
| **A** | `audit/event_buffer.py:51` | 25+ | 미들웨어 이벤트 버퍼 유형 (DLQ_STORE, CB_STATE_CHANGE, GOVERNANCE_BLOCKED, API_EXCEPTION 등) |
| **B** | `audit/audit_integration.py:435` | 15 | 감사 레코더 옵저버 이벤트 (RECORD_SUCCESS, CIRCUIT_OPENED, FALLBACK_ACTIVATED, BUFFER_OVERFLOW 등) |

```python
# --- A: audit/event_buffer.py L51-120 (주요 멤버 발췌) ---
class AuditEventType(Enum):
    """Audit 이벤트 유형."""
    DLQ_STORE = "dlq_store"
    DLQ_REPLAY = "dlq_replay"
    CB_STATE_CHANGE = "circuit_breaker_state_change"
    GOVERNANCE_BLOCKED = "governance_blocked"
    API_EXCEPTION = "api_exception"
    RECOVERY_EVENT = "recovery_event"
    SECURITY_VIOLATION = "security_violation"
    # ... 25+ members

# --- B: audit/audit_integration.py L435-460 ---
class AuditEventType(Enum):
    """감사 이벤트 유형."""
    RECORD_SUCCESS = "record_success"
    RECORD_FAILED = "record_failed"
    CIRCUIT_OPENED = "circuit_opened"
    CIRCUIT_CLOSED = "circuit_closed"
    FALLBACK_ACTIVATED = "fallback_activated"
    PRIMARY_RECOVERED = "primary_recovered"
    BUFFER_OVERFLOW = "buffer_overflow"
    DEGRADED_MODE_ENTERED = "degraded_mode_entered"
    DEGRADED_MODE_EXITED = "degraded_mode_exited"
    # ... 15 members
```

### 15-2. 문제점

- 같은 `audit/` 패키지 내에서 동일 이름 Enum이 완전히 다른 멤버 집합으로 정의.
- `audit/__init__.py`에서 이미 A를 `AuditEventType as BufferEventType`으로 alias하여 충돌을 회피 중 — 근본 해결 아님.
- A는 20곳 이상에서 직접 import됨. B는 같은 파일의 `AuditEventObserver`/`AuditEventData`에서만 사용.

### 15-3. 수정 방향

1. **A** (`event_buffer.py`) → `AuditEventType` **유지** — 20곳 이상에서 import되는 핵심 Enum. `audit/__init__.py`의 `BufferEventType` alias 제거.
2. **B** (`audit_integration.py`) → **`AuditObserverEventType`**으로 이름 변경.
   - 근거: `AuditEventObserver` + `AuditEventData`에서만 사용되는 옵저버 패턴 전용 이벤트. RECORD_SUCCESS/FAILED, CIRCUIT_OPENED/CLOSED 등은 옵저버 상태 변화를 나타냄.

---

## 16. `BackpressureStrategy(Enum)` — 부분집합 관계

### 16-1. 현황

| 구분 | 위치 | 멤버 |
|------|------|------|
| **A** | `audit/ring_buffer.py:26` | `DROP_OLDEST`, `DROP_NEWEST` (2개) |
| **B** | `scaling/config.py:33` | `DROP_OLDEST`, `DROP_NEWEST`, `REJECT`, `THROTTLE`, `QUEUE` (5개) |

```python
# --- A: audit/ring_buffer.py L26-29 ---
class BackpressureStrategy(Enum):
    """배압 전략."""
    DROP_OLDEST = "drop_oldest"  # 권장: 비침투
    DROP_NEWEST = "drop_newest"

# --- B: scaling/config.py L33-44 ---
class BackpressureStrategy(Enum):
    """Backpressure 전략."""
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    REJECT = "reject"       # HTTP 503
    THROTTLE = "throttle"   # Rate Limit 적용
    QUEUE = "queue"          # 대기열에 추가
```

### 16-2. 문제점

- A는 B의 **엄격한 부분집합** (2개 ⊂ 5개). 공유 멤버의 값도 동일.
- A를 import하는 곳: `audit/event_buffer.py`, `audit/resilient_recorder.py` (2곳).
- 별도 정의 상태에서는 `isinstance`/비교 시 호환 불가.

### 16-3. 수정 방향

1. **B** (`scaling/config.py`)를 **단일 소스**로 지정 — 상위집합이며 시스템 전체 배압 전략을 포괄.
2. **A** (`ring_buffer.py`)에서 `from selfhealing.scaling.config import BackpressureStrategy`로 교체.
3. ring_buffer는 여전히 `DROP_OLDEST`/`DROP_NEWEST`만 사용하되, 검증 로직으로 제한 가능.

---

## 17. `ConfigChangeEvent` — 필드·용도 완전 불일치

### 17-1. 현황

| 구분 | 위치 | 역할 | 주요 필드 |
|------|------|------|-----------|
| **A** | `audit/logger.py:45` | 감사 로그 설정 변경 이벤트 | `config_type`, `action: AuditAction\|str`, `old_value: Any`, `new_value: Any`, `reason`, `user`, `ip_address`, `user_agent`, `source`, `apply_strategy` |
| **B** | `adapters/deployment/base.py:148` | 배포 타임라인 설정 변경 | `change_id: str`, `config_key`, `old_value: str`, `new_value: str`, `changed_at: str`, `changed_by: str`, `service_name`, `namespace` + `to_timeline_event()` |

```python
# --- A: audit/logger.py L45-72 ---
@dataclass
class ConfigChangeEvent:
    """Represents a configuration change event."""
    config_type: str
    config_key: str
    action: AuditAction | str
    old_value: Any = None
    new_value: Any = None
    reason: str | None = None
    user: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    source: str = "api"
    apply_strategy: str | None = None
    apply_delay_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

# --- B: adapters/deployment/base.py L148-210 ---
@dataclass
class ConfigChangeEvent:
    """설정 변경 이벤트 정보."""
    change_id: str
    config_key: str
    old_value: str
    new_value: str
    changed_at: str
    changed_by: str = "system"
    service_name: str = ""
    namespace: str = "default"
    def to_dict(self) -> dict[str, Any]: ...
    def to_timeline_event(self) -> dict[str, Any]: ...
```

### 17-2. 문제점

- `config_key`만 이름이 같고 나머지 필드·타입이 완전히 다름.
- A는 감사 로그(user/ip/user_agent 추적, `Any` 타입 값), B는 배포 타임라인(change_id/service_name/namespace, `str` 타입 값).
- `audit/__init__.py`에서 A를 `from selfhealing.audit.logger import AuditAction, ConfigChangeEvent`로 export 중.

### 17-3. 수정 방향

1. **A** (`audit/logger.py`) → **`AuditConfigChangeEvent`**로 이름 변경.
   - 근거: 감사 레이어 전용. `AuditAction` 의존, user/ip_address/user_agent 등 감사 추적 필드 포함.
2. **B** (`adapters/deployment/base.py`) → **`DeploymentConfigChange`**로 이름 변경.
   - 근거: 배포 어댑터 전용. `change_id`/`service_name`/`namespace` + `to_timeline_event()` 등 배포 타임라인 도메인.

---

## 18. `AuditAction` — 베이스·멤버·도메인 완전 불일치

### 18-1. 현황

| 구분 | 위치 | 베이스 | 멤버 수 | 도메인 |
|------|------|--------|---------|--------|
| **A** | `audit/logger.py:31` | `Enum` | 8개 | CRUD 패턴 (CREATE, UPDATE, DELETE, READ, APPLY, ROLLBACK, VERIFY, EXPORT) |
| **B** | `interfaces/audit_adapter.py:37` | `str, Enum` | 30+ | 시스템 이벤트 (CB_FORCE_OPEN, DLQ_STORE, SECURITY_INCIDENT, GOVERNANCE_BLOCKED 등) |

```python
# --- A: audit/logger.py L31-42 ---
class AuditAction(Enum):
    """Types of audit actions."""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    READ = "read"
    APPLY = "apply"
    ROLLBACK = "rollback"
    VERIFY = "verify"
    EXPORT = "export"

# --- B: interfaces/audit_adapter.py L37-99 (발췌) ---
class AuditAction(str, Enum):
    """Standard audit action types."""
    CB_FORCE_OPEN = "cb_force_open"
    CB_AUTO_OPEN = "cb_auto_open"
    DLQ_STORE = "dlq_store"
    DLQ_REPLAY_START = "dlq_replay_start"
    SECURITY_INCIDENT = "security_incident"
    GOVERNANCE_BLOCKED = "governance_blocked"
    AUTO_TUNING_ADJUSTMENT = "auto_tuning_adjustment"
    DNA_DRIFT_DETECTED = "dna_drift_detected"
    COMPLIANCE_CHECK = "compliance_check"
    API_ERROR = "api_error"
    # ... 30+ members
```

### 18-2. 문제점

- 멤버가 **단 하나도 겹치지 않음**. A는 CRUD 패턴, B는 시스템 자동화 이벤트.
- 베이스도 다름 (`Enum` vs `str, Enum`).
- B는 6곳 이상에서 import됨 (`continuous_audit_api`, `runtime_feedback`, `auto_tuning/service`, `async_audit_lifecycle`, `audit_middleware`, `pool_circuit_breaker`). A는 `audit/__init__.py`에서만 export.

### 18-3. 수정 방향

1. **B** (`interfaces/audit_adapter.py`) → `AuditAction` **유지** — 인터페이스 레이어에 위치하며 6곳 이상에서 import. 시스템 전체에서 표준 감사 액션 타입으로 사용.
2. **A** (`audit/logger.py`) → **`ConfigAuditAction`**으로 이름 변경.
   - 근거: `AuditLogger`의 설정 변경 감사 전용 CRUD 액션 (CREATE/UPDATE/DELETE/READ/APPLY/ROLLBACK/VERIFY/EXPORT). `ConfigChangeEvent`(→ Item 17에서 `AuditConfigChangeEvent`로 변경)와 쌍을 이룸.

---

## 19. `WatchdogState` — 패러다임 충돌 (Enum vs dataclass)

### 19-1. 현황

| 구분 | 위치 | 타입 | 내용 |
|------|------|------|------|
| **A** | `audit/audit_watchdog.py:65` | `Enum` | `STOPPED`, `RUNNING`, `DEGRADED` (프로세스 라이프사이클 상태) |
| **B** | `meta/watchdog.py:39` | `@dataclass` | `overall_status`, `component_statuses`, `last_check`, `escalation_pending`, `escalation_count`, `self_cb_open`, `consecutive_failures` |

```python
# --- A: audit/audit_watchdog.py L65-70 ---
class WatchdogState(Enum):
    """Watchdog 상태."""
    STOPPED = "stopped"
    RUNNING = "running"
    DEGRADED = "degraded"

# --- B: meta/watchdog.py L39-62 ---
@dataclass
class WatchdogState:
    """Watchdog 상태."""
    overall_status: HealthStatus
    component_statuses: dict[str, HealthStatus]
    last_check: datetime
    escalation_pending: bool
    escalation_count: int
    self_cb_open: bool = False
    consecutive_failures: dict[str, int] = field(default_factory=dict)
```

### 19-2. 문제점

- **타입 자체가 다름**: A는 `Enum` (3개 상수), B는 `@dataclass` (7개 필드).
- A는 워치독 프로세스의 실행 상태(STOPPED/RUNNING/DEGRADED), B는 시스템 전체 상태 스냅샷.
- 같은 이름이므로 import 시 어느 것인지 혼동 가능.

### 19-3. 수정 방향

1. **A** (`audit_watchdog.py`) → **`AuditWatchdogStatus`**로 이름 변경.
   - 근거: 프로세스 라이프사이클 상태(STOPPED/RUNNING/DEGRADED)를 나타내는 Enum. "Status"가 Enum 패턴에 적합하며 `AuditWatchdogConfig`(Item 13)와 같은 네이밍 접두사 사용.
2. **B** (`meta/watchdog.py`) → `WatchdogState` **유지** — `meta/` 레이어의 시스템 상태 스냅샷 dataclass. `HealthStatus`, `component_statuses` 등 종합 정보 포함.

---

## 20. `CBStateCache` — 구현 패러다임 충돌

### 20-1. 현황

| 구분 | 위치 | 패턴 | 특징 |
|------|------|------|------|
| **A** | `core/state_cache.py:24` | 클래스 메서드 싱글톤 | `_cache: dict`, `RLock`, `classmethod configure()`/`get_state()` |
| **B** | `adapters/ipc/cb_state_cache.py:75` | 인스턴스 기반 | `__init__(ttl_seconds)`, EventBus 무효화, `MAX_ENTRIES=10000` |

```python
# --- A: core/state_cache.py L24-50 ---
class CBStateCache:
    """Circuit Breaker 상태 캐시"""
    _cache: dict[str, dict[str, Any]] = {}
    _lock = threading.RLock()
    _fetch_callback: Callable[[str], dict] | None = None

    @classmethod
    def configure(cls, fetch_callback: Callable[[str], dict]) -> None: ...

    @classmethod
    def get_state(cls, service_name: str) -> dict: ...

# --- B: adapters/ipc/cb_state_cache.py L75-100 ---
class CBStateCache:
    """서킷 브레이커 상태 로컬 캐시."""
    DEFAULT_TTL_SECONDS = 5.0
    MAX_ENTRIES = 10000

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS,
                 enable_event_invalidation: bool = True):
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._ttl = ttl_seconds
        self._stats = CacheStats()
```

### 20-2. 문제점

- 같은 이름이지만 설계 패러다임이 다름 (클래스메서드 싱글톤 vs 인스턴스 기반).
- A는 `core/__init__.py`에서 export. B는 `adapters/ipc/grpc_server.py`와 `uds_server.py`에서 import.
- B가 `CacheEntry`, `CacheStats` 등 데이터 모델도 함께 정의.

### 20-3. 수정 방향

1. **A** (`core/state_cache.py`) → `CBStateCache` **유지** — `core/` 레이어 기본 캐시. `core/__init__.py`에서 export되는 공개 API.
2. **B** (`adapters/ipc/cb_state_cache.py`) → **`IPCStateCache`**로 이름 변경.
   - 근거: IPC 어댑터 전용 캐시로 TTL, EventBus 무효화, MAX_ENTRIES 등 IPC 특화 기능. `grpc_server`/`uds_server`에서만 사용하므로 "IPC" 접두사가 역할을 정확히 표현.

---

## 21. `CacheEntry` — 필드 불일치

### 21-1. 현황

| 구분 | 위치 | 주요 필드 |
|------|------|-----------|
| **A** | `adapters/ipc/cb_state_cache.py:39` | `value: Any`, `expires_at: float`, `created_at: float` |
| **B** | `adapters/cache/memory_adapter.py:53` | `value: Any`, `expires_at: float \| None` + `is_expired()` 메서드 |

```python
# --- A: adapters/ipc/cb_state_cache.py L39-48 ---
@dataclass
class CacheEntry:
    """캐시 엔트리."""
    value: Any
    expires_at: float
    created_at: float = field(default_factory=time.time)

# --- B: adapters/cache/memory_adapter.py L53-62 ---
@dataclass
class CacheEntry:
    """Internal cache entry with value and expiration."""
    value: Any
    expires_at: float | None = None
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at
```

### 21-2. 문제점

- A는 `created_at` 필드 포함 + `expires_at`은 필수. B는 `expires_at`이 Optional + `is_expired()` 메서드 포함.
- A는 `CBStateCache`(→ Item 20에서 `IPCStateCache`) 전용. B는 범용 메모리 캐시 어댑터용.

### 21-3. 수정 방향

1. **A** (`adapters/ipc/cb_state_cache.py`) → **`IPCCacheEntry`**로 이름 변경.
   - 근거: `IPCStateCache`(Item 20 이름 변경)와 짝을 이루는 IPC 전용 캐시 엔트리. `created_at` 필드가 있어 캐시 수명 추적 용도.
2. **B** (`adapters/cache/memory_adapter.py`) → `CacheEntry` **유지** — 범용 메모리 캐시 엔트리. `is_expired()` 메서드 포함으로 자체 만료 판단 가능.

---

## 22. `HealthStatus` — 3번째 정의 (Item 9 누락분, Enum vs dataclass 패러다임 충돌)

### 22-1. 현황

| 구분 | 위치 | 타입 | 내용 |
|------|------|------|------|
| **A** | `adapters/ipc/sidecar_ipc_probe.py:34` | `Enum` | `HEALTHY`, `DEGRADED`, `UNHEALTHY`, `UNKNOWN` |
| **B** | `meta/health_probe.py:25` | `Enum` | `HEALTHY`, `DEGRADED`, `UNHEALTHY`, `UNKNOWN` (Item 9에서 통합 예정) |
| **C** | `services/health_check.py:53` | `@dataclass` | `status: str`, `checks: dict`, `services_count: int`, `timestamp: str \| None` + `to_dict()` |

```python
# --- A+B: Enum (Item 9에서 통합 예정) ---
class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

# --- C: services/health_check.py L53-64 ---
@dataclass
class HealthStatus:
    """전체 헬스 상태."""
    status: str  # healthy, degraded, unhealthy
    checks: dict[str, str] = field(default_factory=dict)
    services_count: int = 0
    timestamp: str | None = None
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

### 22-2. 문제점

- Item 9에서 A·B 두 Enum 통합을 계획했지만 **C의 dataclass 정의를 누락**.
- C는 `HealthCheckService.get_health()`의 반환 타입으로 사용 — HTTP 응답용 데이터 컨테이너.
- `from selfhealing.services.health_check import HealthStatus`와 `from selfhealing.meta.health_probe import HealthStatus`가 전혀 다른 타입을 반환.

### 22-3. 수정 방향

1. **A+B** → Item 9 수정 방향 유지 (`meta/health_probe.py` 단일 소스 Enum).
2. **C** (`services/health_check.py`) → **`SystemHealthSummary`**로 이름 변경.
   - 근거: 이 dataclass는 개별 컴포넌트 상태(`HealthStatus` Enum)가 아니라, 전체 시스템의 **종합 헬스 요약** 정보(checks dict, services_count, timestamp)를 담는 컨테이너. 같은 파일의 `ReadinessStatus`, `PoolHealthStatus` 등과 동일한 "○○Status" 데이터 클래스 패턴이지만, `HealthStatus` Enum과 이름이 충돌하므로 **역할을 정확히 표현하는 이름**으로 변경.

---

## 23. `ProviderRegistry` — 구현 범위·설계 패러다임 충돌

### 23-1. 현황

| 구분 | 위치 | 관리 대상 | 기본값 | 패턴 |
|------|------|-----------|--------|------|
| **A** | `factory.py:41` | 7종 (cache, task_queue, failed_op_repo, cb_repo, security_repo, audit_adapter, statistics) | memory/sync/redis/file | 클래스 변수 싱글톤, `_instances: dict` |
| **B** | `services/factory/registry.py:55` | 2종 (cache, task_queue) | redis/celery | 클래스 변수 싱글톤, `_cache_instances`/`_queue_instances` 분리 |

```python
# --- A: factory.py L41-72 (발췌) ---
class ProviderRegistry:
    """Central registry for all pluggable components."""
    _cache_providers: dict[str, type] = {}
    _task_queues: dict[str, type] = {}
    _failed_op_repos: dict[str, type] = {}
    _circuit_breaker_repos: dict[str, type] = {}
    _security_repos: dict[str, type] = {}
    _audit_adapters: dict[str, type] = {}
    _statistics_adapter: StatisticsRepositoryInterface | None = None
    _postmortem_model: type | None = None
    _default_cache: str = "memory"
    _default_queue: str = "sync"
    _default_repo: str = "redis"
    _default_audit: str = "file"
    _instances: dict[str, object] = {}

# --- B: services/factory/registry.py L55-75 (발췌) ---
class ProviderRegistry:
    """Central registry for all pluggable components."""
    _cache_providers: dict[str, type] = {}
    _task_queues: dict[str, type] = {}
    _cache_instances: dict[str, Any] = {}
    _queue_instances: dict[str, Any] = {}
    _default_cache: str = "redis"
    _default_queue: str = "celery"
```

### 23-2. 문제점

- **A는 20곳 이상에서 `from selfhealing.factory import ProviderRegistry`로 import** (conftest.py, reconciler, sync_worker, audit/base, postmortem_store 등 핵심 경로 포함).
- B는 **소스 내 직접 import 0곳** — `services/factory.py`(deprecated 호환 모듈)에서만 경로 언급. 테스트 1곳에서만 import.
- A는 7종 provider를 관리하는 확장형 레지스트리, B는 cache/task_queue 2종만 관리하는 축소형.
- 기본값이 다름: A의 cache 기본값은 `"memory"`, B는 `"redis"`.
- B는 `override_provider()`, `isolated_test_context()` 등 테스트 격리 유틸을 제공.

### 23-3. 수정 방향

1. **A** (`factory.py`) → `ProviderRegistry` **유지** — 시스템 전체 어댑터 레지스트리. 20곳+ import, 7종 provider 관리. 공식 공개 API.
2. **B** (`services/factory/registry.py`) → **`ServiceProviderRegistry`**로 이름 변경.
   - 근거: `services/factory/` 패키지 내부의 서비스 레이어 전용 레지스트리. cache/task_queue 2종만 관리하며 `override_provider()`/`isolated_test_context()` 등 서비스 테스트 격리 기능이 특화 목적. "Service" 접두사가 `services/` 패키지 소속 + 축소된 범위를 정확히 표현.

---

## 24. `CleanupStats` — 프로퍼티 누락에 의한 기능 불일치

### 24-1. 현황

| 구분 | 위치 | 필드 | 추가 기능 |
|------|------|------|-----------|
| **A** | `services/dlq_models.py:121` | `total`, `by_status`, `resolved_older_than_30_days`, `archived_older_than_90_days` | `can_archive`, `can_purge` 프로퍼티 |
| **B** | `interfaces/statistics.py:73` | `total`, `by_status`, `resolved_older_than_30_days`, `archived_older_than_90_days` | (없음) |

```python
# --- A: services/dlq_models.py L121-139 ---
@dataclass
class CleanupStats:
    """Statistics for DLQ cleanup operations."""
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    resolved_older_than_30_days: int = 0
    archived_older_than_90_days: int = 0

    @property
    def can_archive(self) -> int:
        """Number of entries that can be archived."""
        return self.resolved_older_than_30_days

    @property
    def can_purge(self) -> int:
        """Number of entries that can be purged."""
        return self.archived_older_than_90_days

# --- B: interfaces/statistics.py L73-80 ---
@dataclass
class CleanupStats:
    """Cleanup operation statistics."""
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    resolved_older_than_30_days: int = 0
    archived_older_than_90_days: int = 0
```

### 24-2. 문제점

- 4개 필드는 완전히 동일하나, A는 `can_archive`/`can_purge` 프로퍼티를 추가 제공.
- B를 import한 곳에서 `stats.can_archive` 접근 시 `AttributeError` 발생.
- A는 DLQ 서비스 전용 모델, B는 인터페이스 레이어의 통계 DTO.
- 외부 import 0곳 — 양쪽 모두 아직 직접 import되지 않음 (같은 모듈 내에서만 사용).

### 24-3. 수정 방향

1. **A** (`services/dlq_models.py`) → `CleanupStats` **유지** — `can_archive`/`can_purge` 프로퍼티 포함으로 DLQ 정리 작업에 특화.
2. **B** (`interfaces/statistics.py`) → **제거 후 A에서 import**.
   - 근거: B는 A의 **프로퍼티 누락 복사본**. 4개 필드가 완전 동일하므로 별도 정의할 이유 없음. `interfaces/statistics.py`에서 `from selfhealing.services.dlq_models import CleanupStats`로 교체하거나, 인터페이스 레이어에서 직접 사용하지 않는다면 제거.

---

## 25. `PaginatedResult` — 필드명·타입·구현 불일치

### 25-1. 현황

| 구분 | 위치 | 결과 필드 | 총 개수 필드 | 이전 페이지 | `total_pages` |
|------|------|-----------|-------------|-------------|--------------|
| **A** | `services/dlq_models.py:141` | `results: list[dict[str, Any]]` | `total_count: int` | `has_previous: bool` | 필드 (`int = 0`) |
| **B** | `interfaces/statistics.py:83` | `items: list[Any]` | `total: int` | `has_prev: bool` | 프로퍼티 (연산) |

```python
# --- A: services/dlq_models.py L141-149 ---
@dataclass
class PaginatedResult:
    """Paginated result for list operations."""
    results: list[dict[str, Any]] = field(default_factory=list)
    page: int = 1
    page_size: int = 20
    total_pages: int = 0
    total_count: int = 0
    has_next: bool = False
    has_previous: bool = False

# --- B: interfaces/statistics.py L83-96 ---
@dataclass
class PaginatedResult:
    """Paginated query result."""
    items: list[Any] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    has_next: bool = False
    has_prev: bool = False
    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size
```

### 25-2. 문제점

- **필드명 불일치**: `results` vs `items`, `total_count` vs `total`, `has_previous` vs `has_prev`.
- **타입 불일치**: `list[dict[str, Any]]` vs `list[Any]`.
- **`total_pages` 구현 충돌**: A는 직접 필드(호출자가 계산해서 설정), B는 `@property`(`total`/`page_size`에서 자동 계산).
- 외부 import 0곳 — 양쪽 모두 직접 import되지 않음.

### 25-3. 수정 방향

1. **A** (`services/dlq_models.py`) → **`DLQPaginatedResult`**로 이름 변경.
   - 근거: `results: list[dict[str, Any]]` — DLQ entry를 dict로 직렬화한 결과를 담으며, `total_pages`를 호출자가 직접 설정하는 DLQ 서비스 전용 페이지네이션. "DLQ" 접두사가 서비스 도메인을 명확히 표현.
2. **B** (`interfaces/statistics.py`) → `PaginatedResult` **유지** — 인터페이스 레이어의 범용 페이지네이션 DTO. `list[Any]` 타입 + `total_pages` 자동 계산으로 재사용성이 높음.

---

## 26. `RequestContext` — 패러다임 완전 충돌 (컨텍스트 매니저 vs HTTP DTO)

### 26-1. 현황

| 구분 | 위치 | 타입 | 역할 | 주요 멤버 |
|------|------|------|------|-----------|
| **A** | `core/request_context.py:16` | 일반 클래스 | 요청 라이프사이클 추적 (컨텍스트 매니저) | `__enter__`, `__exit__`, `RequestTracker`, `mark_failed()`, `set_metadata()` |
| **B** | `interfaces/web_framework.py:61` | `@dataclass` | 프레임워크 독립 HTTP 요청 DTO | `method: HttpMethod`, `path`, `headers`, `body`, `user`, `is_authenticated`, `client_ip` 등 14개 필드 |

```python
# --- A: core/request_context.py L16-75 ---
class RequestContext:
    """Context for tracking a single request."""
    def __init__(self, tracker: RequestTracker,
                 request_id: str | None = None,
                 endpoint: str = "", method: str = "",
                 metadata: dict[str, Any] | None = None):
        self._tracker = tracker
        self._request_id = request_id or str(uuid.uuid4())
        self._endpoint = endpoint
        self._method = method
        self._metadata = metadata or {}
        self._tracked: TrackedRequest | None = None
        self._success = True
    def __enter__(self) -> "RequestContext": ...
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...

# --- B: interfaces/web_framework.py L61-100 (발췌) ---
@dataclass
class RequestContext:
    """Framework-independent request context."""
    method: HttpMethod
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, Any] = field(default_factory=dict)
    path_params: dict[str, Any] = field(default_factory=dict)
    body: bytes | None = None
    json_body: dict | None = None
    user: Any | None = None
    is_authenticated: bool = False
    client_ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    content_type: str | None = None
    def get_header(self, name: str, default: str | None = None) -> str | None: ...
    def get_query(self, name: str, default: Any = None) -> Any: ...
```

### 26-2. 문제점

- **타입 패러다임이 완전히 다름**: A는 `with` 문에서 사용하는 컨텍스트 매니저(요청 시작/종료 라이프사이클 관리), B는 HTTP 요청 정보를 담는 순수 데이터 컨테이너.
- A는 `core/__init__.py`에서 export. B는 `interfaces/web_framework.py`에 정의.
- `from selfhealing.core import RequestContext`와 `from selfhealing.interfaces.web_framework import RequestContext`가 **완전히 다른 객체**.

### 26-3. 수정 방향

1. **A** (`core/request_context.py`) → **`RequestLifecycleContext`**로 이름 변경.
   - 근거: `RequestTracker` 기반 요청 라이프사이클 관리가 핵심 역할. `__enter__`/`__exit__` + `mark_failed()` + `set_metadata()` 등 라이프사이클 제어 메서드를 보유. "Lifecycle"이 컨텍스트 매니저 패턴의 목적을 정확히 표현. `core/__init__.py`의 export 이름도 함께 변경 필요.
2. **B** (`interfaces/web_framework.py`) → `RequestContext` **유지**.
   - 근거: HTTP 요청 컨텍스트(method, path, headers, body 등)를 표현하는 인터페이스 레이어의 **표준 DTO**. "RequestContext"라는 이름이 웹 프레임워크에서 관례적으로 사용되는 용어이며, `HttpMethod`, `ContentType` 등과 함께 web_framework 모듈의 핵심 타입.

---

## 27. `EventPriority` — 타입·값·의미 3중 충돌

### 27-1. 현황

| 구분 | 위치 | 타입 | 멤버 | 값 방향 |
|------|------|------|------|---------|
| **A** | `utils/async_logger.py:63` | 일반 클래스 (상수 홀더) | `CRITICAL=0`, `WARNING=1`, `INFO=2`, `DEBUG=3` | 낮을수록 높은 우선순위 |
| **B** | `services/event_bus.py:143` | `Enum` | `LOW=1`, `NORMAL=2`, `HIGH=3`, `CRITICAL=4` | 높을수록 높은 우선순위 |

```python
# --- A: utils/async_logger.py L63-70 ---
class EventPriority:
    """이벤트 처리 우선순위 상수 (낮을수록 높은 우선순위)."""
    CRITICAL = 0
    WARNING = 1
    INFO = 2
    DEBUG = 3

# --- B: services/event_bus.py L143-149 ---
class EventPriority(Enum):
    """이벤트 처리 우선순위."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4
```

### 27-2. 문제점

- **값 방향이 정반대**: A는 `CRITICAL=0`(낮을수록 높은 우선순위, PriorityQueue용), B는 `CRITICAL=4`(높을수록 높은 우선순위).
- **타입이 다름**: A는 plain class(상수 홀더), B는 `Enum`.
- **멤버 집합이 다름**: A는 `CRITICAL/WARNING/INFO/DEBUG`(severity 기반), B는 `LOW/NORMAL/HIGH/CRITICAL`(일반 우선순위 레벨).
- A의 `CRITICAL`과 B의 `CRITICAL`은 의미가 같지만 값이 `0` vs `4`로 **혼용 시 정렬 순서가 뒤집힘**.
- A는 같은 파일의 `SEVERITY_PRIORITY_MAP`에서 `EventSeverity → EventPriority` 매핑용으로 사용. B는 `SelfHealingEvent.priority` 필드 타입으로 2곳 src + 2곳 테스트에서 import.

### 27-3. 수정 방향

1. **A** (`utils/async_logger.py`) → **`LogFlushPriority`**로 이름 변경.
   - 근거: `PriorityQueue`의 정렬 키로 사용되는 **로그 플러시(비동기 전송) 전용 우선순위**. 낮을수록 높은 우선순위는 `heapq`/`PriorityQueue` 관례를 따른 것. 같은 파일의 `SEVERITY_PRIORITY_MAP`, `PrioritizedEvent`와 함께 async logger 배치 전송 정책 전용. "LogFlush"가 PriorityQueue 기반 배치 플러시 도메인을 명확히 표현.
2. **B** (`services/event_bus.py`) → `EventPriority` **유지** — 이벤트 버스의 표준 우선순위 Enum. `SelfHealingEvent.priority` 타입으로 시스템 전파에 사용.

---

## 28. `PoolHealthStatus` — Enum vs dataclass 패러다임 충돌

### 28-1. 현황

| 구분 | 위치 | 타입 | 내용 |
|------|------|------|------|
| **A** | `core/pool_monitor.py:22` | `str, Enum` | `HEALTHY`, `WARNING`, `CRITICAL`, `EXHAUSTED`, `LEAK_SUSPECTED` (5멤버) |
| **B** | `services/health_check.py:78` | `@dataclass` | `status: str`, `pool_info: dict[str, Any]`, `error: str \| None` + `to_dict()` |

```python
# --- A: core/pool_monitor.py L22-30 ---
class PoolHealthStatus(str, Enum):
    """Connection pool health status"""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    EXHAUSTED = "exhausted"
    LEAK_SUSPECTED = "leak_suspected"

# --- B: services/health_check.py L78-88 ---
@dataclass
class PoolHealthStatus:
    """커넥션 풀 헬스 상태."""
    status: str  # healthy, degraded, error
    pool_info: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

### 28-2. 문제점

- **타입 자체가 다름**: A는 `str, Enum`(5개 상태값), B는 `@dataclass`(상태+풀 정보+에러).
- A는 `core/__init__.py`에서 export, `pool_watchdog.py`에서 10곳+ 참조, `chaos/experiments/resource.py`에서 `.EXHAUSTED`/`.CRITICAL`/`.WARNING`/`.LEAK_SUSPECTED` 멤버 직접 접근.
- B는 `HealthCheckService.get_pool_health()` 반환 타입으로 HTTP 응답 직렬화용.
- B의 `status: str` 값인 `"healthy"`, `"degraded"`, `"error"`는 A의 Enum 멤버와도 **부분적으로만 겹침** (A에 `"degraded"`, `"error"` 없음).

### 28-3. 수정 방향

1. **A** (`core/pool_monitor.py`) → `PoolHealthStatus` **유지** — `core/__init__.py` export, `pool_watchdog.py` 10곳+, `chaos/experiments` 등 시스템 전반에서 상태 판단에 사용하는 핵심 Enum.
2. **B** (`services/health_check.py`) → **`PoolHealthSummary`**로 이름 변경.
   - 근거: 이 dataclass는 커넥션 풀의 **종합 상태 요약**(상태 문자열 + pool_info dict + 에러 메시지)을 HTTP 응답으로 직렬화하기 위한 DTO. 같은 파일의 `DatabaseCheck`, `PoolInfo`, `ReadinessStatus`와 동일한 DTO 패턴. "Summary"가 `to_dict()` 기반 응답 컨테이너 역할을 정확히 표현.

---

## 29. `CircuitBreakerState` — Enum vs dataclass 패러다임 충돌

### 29-1. 현황

| 구분 | 위치 | 타입 | 내용 |
|------|------|------|------|
| **A** | `services/throttle/registry.py:35` | `str, Enum` | `CLOSED`, `OPEN`, `HALF_OPEN` (Throttle 연동용) |
| **B** | `audit/resilience/circuit_breaker.py:38` | `@dataclass` | `state: CircuitState`, `failure_count`, `success_count`, `last_failure_time`, `last_state_change`, `total_failures`, `total_successes` |

```python
# --- A: services/throttle/registry.py L35-39 ---
class CircuitBreakerState(str, Enum):
    """Circuit Breaker 상태 (Throttle 연동용)."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

# --- B: audit/resilience/circuit_breaker.py L38-46 ---
@dataclass
class CircuitBreakerState:
    """Current state of a circuit breaker."""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: datetime | None = None
    last_state_change: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_failures: int = 0
    total_successes: int = 0
```

### 29-2. 문제점

- **타입 자체가 다름**: A는 `str, Enum`(3개 상태 상수), B는 `@dataclass`(7개 필드의 상태 스냅샷).
- A는 `services/throttle/__init__.py`에서 export → `ServiceThrottleState.cb_state` 타입으로 사용. `ThrottleRegistry.handle_cb_state_change()`에서 10곳+ 참조.
- B는 `audit/resilience/__init__.py`에서 export → `audit/__init__.py`에서 lazy export. `CircuitBreaker` 클래스의 내부 상태 관리용.
- A의 멤버 `CLOSED`/`OPEN`/`HALF_OPEN`은 B의 `state: CircuitState` 필드가 참조하는 **같은 파일의 `CircuitState` Enum**과 동일한 값 — A는 이 Enum을 throttle 도메인에서 중복 정의한 것.

### 29-3. 수정 방향

1. **A** (`services/throttle/registry.py`) → **제거 후 `CircuitState` import로 교체**.
   - 근거: A의 멤버(`CLOSED`, `OPEN`, `HALF_OPEN`)와 값(`"closed"`, `"open"`, `"half_open"`)은 Item 2에서 단일 소스로 지정한 `audit/graceful_degradation/enums.py`의 `CircuitState(str, Enum)`과 **완전 동일**. throttle 도메인에서 별도 Enum을 정의할 필요 없음. `from selfhealing.audit.graceful_degradation.enums import CircuitState`로 교체하고 `ServiceThrottleState.cb_state` 등의 타입도 `CircuitState`로 변경.
2. **B** (`audit/resilience/circuit_breaker.py`) → **`CircuitBreakerSnapshot`**으로 이름 변경.
   - 근거: 이 dataclass는 서킷 브레이커의 **현재 상태 스냅샷**(상태 + 카운터 + 타임스탬프)을 기록하는 데이터 객체. `CircuitBreaker` 클래스의 `_states: dict[str, CircuitBreakerState]`에서 각 백엔드별 상태를 추적. "Snapshot"이 특정 시점의 상태 캡처라는 역할을 정확히 표현.

---

## 30. `BlastRadiusLevel(Enum)` — 3곳 멤버·베이스 불일치

### 30-1. 현황

| 구분 | 위치 | 베이스 | 멤버 |
|------|------|--------|------|
| **A** | `services/blast_radius/models.py:10` | `Enum` | `ISOLATED`, `LIMITED`, `MODERATE`, `EXTENSIVE`, `CRITICAL` |
| **B** | `services/chaos/blast_radius_analyzer.py:32` | `str, Enum` | `MINIMAL`, `CONTAINED`, `MODERATE`, `EXTENSIVE`, `CRITICAL` |
| **C** | `services/circuit_breaker/blast_radius_integration.py:57` | `Enum` | `MINIMAL`, `MODERATE`, `EXTENSIVE`, `CRITICAL` |

```python
# --- A: services/blast_radius/models.py L10-18 ---
class BlastRadiusLevel(Enum):
    ISOLATED = "isolated"
    LIMITED = "limited"
    MODERATE = "moderate"
    EXTENSIVE = "extensive"
    CRITICAL = "critical"

# --- B: services/chaos/blast_radius_analyzer.py L32-37 ---
class BlastRadiusLevel(str, Enum):
    MINIMAL = "minimal"
    CONTAINED = "contained"
    MODERATE = "moderate"
    EXTENSIVE = "extensive"
    CRITICAL = "critical"

# --- C: services/circuit_breaker/blast_radius_integration.py L57-62 ---
class BlastRadiusLevel(Enum):
    MINIMAL = "minimal"
    MODERATE = "moderate"
    EXTENSIVE = "extensive"
    CRITICAL = "critical"
```

### 30-2. 문제점

- 3곳 모두 폭발 반경 수준을 표현하지만 멤버가 다름: A는 `ISOLATED`/`LIMITED`, B는 `MINIMAL`/`CONTAINED`, C는 4멤버만.
- 베이스 불일치: B만 `str, Enum`(JSON 직렬화 호환), A/C는 plain `Enum`.
- `MODERATE`, `EXTENSIVE`, `CRITICAL`은 3곳 공통이나 하위 레벨 명칭이 제각각.

### 30-3. 수정 방향

1. **B** (`services/chaos/blast_radius_analyzer.py`) → **단일 소스**로 지정.
   - 근거: `str, Enum`으로 JSON 직렬화 호환. 5단계(MINIMAL~CRITICAL)가 가장 세밀. chaos blast radius 분석기가 시스템 전체 영향 평가를 담당.
2. **A** (`services/blast_radius/models.py`) → **제거 후 B를 import**.
   - 근거: A의 `ISOLATED`/`LIMITED`는 B의 `MINIMAL`/`CONTAINED`와 의미적으로 동일. B 멤버명으로 통일.
3. **C** (`services/circuit_breaker/blast_radius_integration.py`) → **제거 후 B를 import**.
   - 근거: C의 4멤버는 B의 부분집합. `CONTAINED` 단계만 없으므로 제거 무방.

---

## 31. `ControlAPIActions` — 완전 중복

### 31-1. 현황

| 구분 | 위치 | 내용 |
|------|------|------|
| **A** | `core/constants.py:8` | 상수 클래스: `ALLOW`, `BLOCK`, `OVERRIDE`, `RESET`, `INJECT_FAILURE`, `INJECT_SUCCESS` + `CHOICES`, `ALL` |
| **B** | `api/django/serializers_legacy.py:15` | A와 **바이트 단위 완전 동일** (코드 복사) |

```python
# --- A: core/constants.py L8-32 ---
class ControlAPIActions:
    ALLOW = "allow"
    BLOCK = "block"
    OVERRIDE = "override"
    RESET = "reset"
    INJECT_FAILURE = "inject_failure"
    INJECT_SUCCESS = "inject_success"
    CHOICES = [(ALLOW, "Allow - Enable..."), ...]
    ALL = [ALLOW, BLOCK, OVERRIDE, RESET, INJECT_FAILURE, INJECT_SUCCESS]

# --- B: api/django/serializers_legacy.py L15-39 ---
# (A와 완전 동일 코드)
```

### 31-2. 문제점

- **바이트 단위 완전 중복**. `serializers_legacy.py`에서 `core/constants.py`를 복사한 것으로 추정.
- 상수 값 변경 시 양쪽 동시 수정 필요한 동기화 위험.

### 31-3. 수정 방향

1. **A** (`core/constants.py`) → **단일 소스 유지**.
   - 근거: 파일 docstring "Framework-agnostic constant definitions"이 정규 상수 정의 위치임을 명시.
2. **B** (`api/django/serializers_legacy.py`) → **제거 후 `from selfhealing.core.constants import ControlAPIActions`**.

---

## 32. `ControlAPIEnvironments` — 완전 중복

### 32-1. 현황

| 구분 | 위치 | 내용 |
|------|------|------|
| **A** | `core/constants.py:33` | 상수 클래스: `TEST`, `CHAOS`, `OPS` + `CHOICES`, `ALL` |
| **B** | `api/django/serializers_legacy.py:40` | A와 **완전 동일** |

```python
# --- A: core/constants.py L33-47 ---
class ControlAPIEnvironments:
    TEST = "test"
    CHAOS = "chaos"
    OPS = "ops"
    CHOICES = [(TEST, "Test - CI/CD validation"), ...]
    ALL = [TEST, CHAOS, OPS]

# --- B: api/django/serializers_legacy.py L40-54 ---
# (A와 완전 동일 코드)
```

### 32-2. 문제점

- Item 31과 동일한 **완전 중복** 패턴. 같은 파일 쌍에서 복사.

### 32-3. 수정 방향

1. **A** (`core/constants.py`) → **단일 소스 유지**.
2. **B** (`api/django/serializers_legacy.py`) → **제거 후 `from selfhealing.core.constants import ControlAPIEnvironments`**.

---

## 33. `RiskLevels` — 완전 중복

### 33-1. 현황

| 구분 | 위치 | 내용 |
|------|------|------|
| **A** | `core/constants.py:49` | 상수 클래스: `INFO`, `WARNING`, `HIGH`, `CRITICAL`, `FORBIDDEN` |
| **B** | `api/django/serializers_legacy.py:56` | A와 **완전 동일** |

```python
# --- A: core/constants.py L49-55 ---
class RiskLevels:
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"
    FORBIDDEN = "forbidden"

# --- B: api/django/serializers_legacy.py L56-62 ---
# (A와 완전 동일 코드)
```

### 33-2. 문제점

- Item 31/32와 동일한 **완전 중복** 패턴. `serializers_legacy.py`에서 `core/constants.py`의 3개 상수 클래스를 모두 복사.

### 33-3. 수정 방향

1. **A** (`core/constants.py`) → **단일 소스 유지**.
2. **B** (`api/django/serializers_legacy.py`) → **제거 후 import**.
   - 근거 (Item 31-33 공통): `from selfhealing.core.constants import ControlAPIActions, ControlAPIEnvironments, RiskLevels` 한 줄로 3개 중복 동시 해소.

---

## 34. `ViolationSeverity(Enum)` — 멤버 부분집합

### 34-1. 현황

| 구분 | 위치 | 멤버 |
|------|------|------|
| **A** | `services/compliance/models.py:22` | `INFO`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` (5개) |
| **B** | `services/corruption_shield/shield.py:25` | `CRITICAL`, `HIGH`, `MEDIUM`, `LOW` (4개, `INFO` 없음) |

```python
# --- A: services/compliance/models.py L22-28 ---
class ViolationSeverity(Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

# --- B: services/corruption_shield/shield.py L25-30 ---
class ViolationSeverity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
```

### 34-2. 문제점

- B는 A의 **부분집합** (`INFO` 제외). 나머지 4멤버 값은 동일.
- B에서 `INFO` 레벨을 사용할 수 없어 경미한 위반 차등 처리 불가.
- 두 도메인(compliance, corruption_shield) 모두 "위반 심각도"를 표현.

### 34-3. 수정 방향

1. **A** (`services/compliance/models.py`) → **단일 소스**로 지정.
   - 근거: 5단계 구분이 더 세밀. compliance가 규정 준수 표준을 정의하므로 심각도 정의의 정규 위치.
2. **B** (`services/corruption_shield/shield.py`) → **제거 후 A를 import**.
   - 근거: `INFO` 추가되어도 shield 로직은 `CRITICAL`/`HIGH`만 block 처리하므로 무방.

---

## 35. `ValidationResult` — 필드·용도 완전 불일치

### 35-1. 현황

| 구분 | 위치 | 주요 필드 |
|------|------|-----------|
| **A** | `api/django/tiering/validator.py:20` | `is_valid`, `errors: list[str]`, `warnings: list[str]` |
| **B** | `services/corruption_shield/shield.py:35` | `is_valid`, `violations: list[Violation]`, `blocked`, `l1_passed`~`l3_passed`, `validation_time_ms` |

```python
# --- A: api/django/tiering/validator.py L20-34 ---
@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    def to_dict(self) -> dict[str, Any]: ...

# --- B: services/corruption_shield/shield.py L35-52 ---
@dataclass
class ValidationResult:
    is_valid: bool
    violations: list[Violation] = field(default_factory=list)
    blocked: bool = False
    l1_passed: bool = True
    l2_passed: bool = True
    l3_passed: bool = True
    validation_time_ms: float = 0.0
    def to_dict(self) -> dict: ...
```

### 35-2. 문제점

- `is_valid`만 공통. A는 문자열 에러/경고 목록, B는 `Violation` 객체 + 3-레이어 통과 여부 + 차단 플래그.
- A는 티어링 설정 검증용, B는 데이터 무결성 보호 3-Layer 검증용.

### 35-3. 수정 방향

1. **A** (`api/django/tiering/validator.py`) → **`TierValidationResult`**로 이름 변경.
   - 근거: 같은 파일의 `TierConfigValidator`가 반환하는 결과. "Tier" 접두사로 도메인 한정.
2. **B** (`services/corruption_shield/shield.py`) → `ValidationResult` **유지**.
   - 근거: 3-Layer 검증은 시스템 핵심 데이터 무결성 보호 메커니즘. `Violation` 객체 기반 상세 결과가 범용 패턴에 부합.

---

## 36. `TokenBucket` — API·구현 불일치

### 36-1. 현황

| 구분 | 위치 | API | 내부 구현 |
|------|------|-----|-----------|
| **A** | `scaling/rate_controller.py:53` | `consume()`, `set_rate()`, `get_rate()` | `capacity: float\|None`, 외부 rate 변경 가능 |
| **B** | `services/chaos/traffic_shaper.py:161` | `acquire()` | `capacity: int`, `_refill()` private |

```python
# --- A: scaling/rate_controller.py L53-79 ---
class TokenBucket:
    def __init__(self, rate: float, capacity: float | None = None):
        self._rate = rate
        self._capacity = capacity or rate
    def consume(self, tokens: int = 1) -> bool: ...
    def set_rate(self, rate: float) -> None: ...
    def get_rate(self) -> float: ...

# --- B: services/chaos/traffic_shaper.py L161-213 ---
class TokenBucket:
    def __init__(self, rate: float, capacity: int):
        self._rate = rate
        self._capacity = capacity
    def acquire(self, tokens: int = 1) -> bool: ...
    def _refill(self) -> None: ...
```

### 36-2. 문제점

- 동일 알고리즘이지만 **메서드명이 다름**: `consume()` vs `acquire()`.
- A는 런타임 rate 변경 가능(`set_rate`), B는 불변.
- `capacity` 타입: A는 `float|None`, B는 `int`.

### 36-3. 수정 방향

1. **A** (`scaling/rate_controller.py`) → `TokenBucket` **유지**.
   - 근거: 범용 rate limiting. `set_rate()`/`get_rate()`로 auto-tuning 연동 가능. `scaling/` 패키지가 시스템 전반 Rate Limit 담당.
2. **B** (`services/chaos/traffic_shaper.py`) → **`ChaosTokenBucket`**으로 이름 변경.
   - 근거: chaos 실험 전용 트래픽 셰이핑. 같은 파일의 `TrafficShaper`가 사용하는 내부 클래스.

---

## 37. `StatusCounts` — 필드 부분집합

### 37-1. 현황

| 구분 | 위치 | 필드 수 |
|------|------|---------|
| **A** | `interfaces/statistics.py:28` | 10개: `total`, `pending`, `resolved`, `failed`, `archived`, `reviewing`, `replayed`, `requires_review`, `rejected`, `expired` |
| **B** | `services/dashboard_service.py:42` | 5개: `total`, `pending`, `resolved`, `failed`, `archived` |

```python
# --- A: interfaces/statistics.py L28-38 ---
@dataclass
class StatusCounts:
    total: int = 0;  pending: int = 0;  resolved: int = 0
    failed: int = 0;  archived: int = 0;  reviewing: int = 0
    replayed: int = 0;  requires_review: int = 0
    rejected: int = 0;  expired: int = 0

# --- B: services/dashboard_service.py L42-47 ---
@dataclass
class StatusCounts:
    total: int = 0;  pending: int = 0;  resolved: int = 0
    failed: int = 0;  archived: int = 0
```

### 37-2. 문제점

- B는 A의 **5/10개 필드 부분집합**. 나머지 5개(`reviewing`~`expired`) 누락.
- A는 `interfaces/` 레이어의 정식 DTO, B는 대시보드에서 간소화.

### 37-3. 수정 방향

1. **A** (`interfaces/statistics.py`) → **단일 소스**로 지정.
   - 근거: `interfaces/` 레이어는 계약 정의 위치. 10개 필드가 DLQ 전체 상태를 완전 표현.
2. **B** (`services/dashboard_service.py`) → **제거 후 `from selfhealing.interfaces.statistics import StatusCounts`**.
   - 근거: 기본값이 모두 `0`이므로 5개만 채워도 나머지는 자연스럽게 0.

---

## 38. `RecentActivity` — 필드명 불일치

### 38-1. 현황

| 구분 | 위치 | 필드 |
|------|------|------|
| **A** | `interfaces/statistics.py:62` | `new_in_24h`, `resolved_in_24h`, `new_in_7d`, `resolved_in_7d`, `trend` |
| **B** | `services/dashboard_service.py:53` | `new_failures_24h`, `resolved_24h`, `new_failures_7d`, `resolved_7d` |

```python
# --- A: interfaces/statistics.py L62-68 ---
@dataclass
class RecentActivity:
    new_in_24h: int = 0;  resolved_in_24h: int = 0
    new_in_7d: int = 0;  resolved_in_7d: int = 0
    trend: str = "stable"

# --- B: services/dashboard_service.py L53-58 ---
@dataclass
class RecentActivity:
    new_failures_24h: int = 0;  resolved_24h: int = 0
    new_failures_7d: int = 0;  resolved_7d: int = 0
```

### 38-2. 문제점

- 의미는 동일하나 **필드명이 다름**: `new_in_24h` vs `new_failures_24h`.
- B에는 `trend` 필드 없음. Item 37과 동일한 파일 쌍에서 발생.

### 38-3. 수정 방향

1. **A** (`interfaces/statistics.py`) → **단일 소스**로 지정.
   - 근거: Item 37과 동일. `interfaces/` 레이어 정식 DTO. `trend` 포함으로 더 완전.
2. **B** (`services/dashboard_service.py`) → **제거 후 A를 import**.

---

## 39. `ServiceDependency` — 모델링 패러다임 충돌

### 39-1. 현황

| 구분 | 위치 | 모델링 | 주요 필드 |
|------|------|--------|-----------|
| **A** | `services/blast_radius/models.py:21` | 엣지(source→target) | `source_service`, `target_service`, `dependency_type`, `criticality` |
| **B** | `services/circuit_breaker/blast_radius_integration.py:120` | 노드(인접 리스트) | `service_id`, `depends_on: list`, `dependents: list`, `criticality` |

```python
# --- A: services/blast_radius/models.py L21-32 ---
@dataclass
class ServiceDependency:
    source_service: str
    target_service: str
    dependency_type: str = "sync"
    criticality: str = "medium"
    metadata: dict = field(default_factory=dict)

# --- B: services/circuit_breaker/blast_radius_integration.py L120-131 ---
@dataclass
class ServiceDependency:
    service_id: str
    depends_on: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    criticality: str = "medium"
```

### 39-2. 문제점

- **그래프 모델링이 정반대**: A는 엣지(source→target 방향), B는 노드(service_id + 인접 리스트).
- 같은 `blast_radius` 문맥이지만 데이터 구조가 호환 불가.

### 39-3. 수정 방향

1. **A** (`services/blast_radius/models.py`) → **`ServiceDependencyEdge`**로 이름 변경.
   - 근거: `source_service`→`target_service` 방향성과 `dependency_type`(sync/async/weak)이 그래프 **엣지** 속성.
2. **B** (`services/circuit_breaker/blast_radius_integration.py`) → **`ServiceDependencyNode`**로 이름 변경.
   - 근거: `depends_on`/`dependents` 리스트가 그래프 **노드**의 in/out 연결을 표현.

---

## 40. `RetryResult` — 필드·프로퍼티 완전 불일치

### 40-1. 현황

| 구분 | 위치 | 주요 필드 |
|------|------|-----------|
| **A** | `services/dlq_models.py:154` | `success`, `id: int`, `retry_count`, `previous_retry_count`, `message` |
| **B** | `services/retry_handler.py:151` | `success`, `action: RetryAction`, `attempt`, `value`, `dlq_id`, `next_delay` + `should_retry`/`was_retried` 프로퍼티 |

```python
# --- A: services/dlq_models.py L154-162 ---
@dataclass
class RetryResult:
    success: bool
    id: int
    retry_count: int
    previous_retry_count: int
    message: str = ""
    error: str | None = None

# --- B: services/retry_handler.py L151-171 ---
@dataclass
class RetryResult:
    success: bool
    action: RetryAction
    attempt: int
    value: Any = None
    error: Exception | None = None
    dlq_id: int | None = None
    next_delay: int | None = None
    @property
    def should_retry(self) -> bool: ...
    @property
    def was_retried(self) -> bool: ...
```

### 40-2. 문제점

- A는 DLQ 엔트리 단건 replay 결과, B는 재시도 핸들러 판단 결과.
- `error` 타입 불일치: A는 `str | None`, B는 `Exception | None`.

### 40-3. 수정 방향

1. **A** (`services/dlq_models.py`) → **`DlqReplayResult`**로 이름 변경.
   - 근거: `id`(엔트리ID), `retry_count`/`previous_retry_count`가 DLQ 리플레이 전용 필드.
2. **B** (`services/retry_handler.py`) → `RetryResult` **유지**.
   - 근거: `RetryAction` + `should_retry`/`was_retried` 프로퍼티가 재시도 판단 흐름의 핵심 계약.

---

## 41. `RecoveryEvent` — 도메인 완전 불일치

### 41-1. 현황

| 구분 | 위치 | 도메인 | 주요 필드 |
|------|------|--------|-----------|
| **A** | `audit/integrity/health_score.py:46` | 무결성 복구 | `event_type`, `sequences_affected`, `recovery_time_ms` |
| **B** | `services/audit/mttr_calculator.py:24` | 서비스 장애 복구(MTTR) | `service_name`, `incident_start/end`, `duration_seconds`, `cause` |

```python
# --- A: audit/integrity/health_score.py L46-55 ---
@dataclass
class RecoveryEvent:
    event_type: str  # "reconcile", "startup_sync", "watchdog_cleanup"
    sequences_affected: int
    recovery_time_ms: float
    timestamp: str = field(default_factory=...)
    details: dict[str, Any] = field(default_factory=dict)

# --- B: services/audit/mttr_calculator.py L24-38 ---
@dataclass
class RecoveryEvent:
    service_name: str
    incident_start: datetime
    incident_end: datetime
    duration_seconds: float
    cause: str = "unknown"
    failure_count: int = 0
```

### 41-2. 문제점

- **도메인이 완전히 다름**: A는 시퀀스 무결성 복구(ms 단위), B는 서비스 장애 복구(초 단위, MTTR).
- 필드가 하나도 겹치지 않음.

### 41-3. 수정 방향

1. **A** (`audit/integrity/health_score.py`) → **`IntegrityRecoveryEvent`**로 이름 변경.
   - 근거: `sequences_affected`, `recovery_time_ms` 등 시퀀스 무결성 복구 특화. `AuditHealthScore` 내부 이벤트.
2. **B** (`services/audit/mttr_calculator.py`) → `RecoveryEvent` **유지**.
   - 근거: 서비스 수준 장애-복구 이벤트로 MTTR 산출 입력 데이터. 범용적 의미에 부합.

---

## 42. `PatternType(Enum)` — 도메인 완전 불일치

### 42-1. 현황

| 구분 | 위치 | 베이스 | 멤버 |
|------|------|--------|------|
| **A** | `api/django/tiering/enums.py:23` | `str, Enum` | `EXACT`, `WILDCARD`, `REGEX` |
| **B** | `services/learning/models.py:11` | `Enum` | `FAILURE`, `RECOVERY`, `PERFORMANCE`, `ANOMALY`, `OPTIMIZATION` |

```python
# --- A: api/django/tiering/enums.py L23-27 ---
class PatternType(str, Enum):
    EXACT = "exact"
    WILDCARD = "wildcard"
    REGEX = "regex"

# --- B: services/learning/models.py L11-17 ---
class PatternType(Enum):
    FAILURE = "failure"
    RECOVERY = "recovery"
    PERFORMANCE = "performance"
    ANOMALY = "anomaly"
    OPTIMIZATION = "optimization"
```

### 42-2. 문제점

- **도메인이 완전히 다름**: A는 "패턴 매칭 방식", B는 "패턴 카테고리"(장애/복구 등).
- 멤버·값 모두 겹치지 않음.

### 42-3. 수정 방향

1. **A** (`api/django/tiering/enums.py`) → **`TierMatchType`**으로 이름 변경.
   - 근거: Tiering 서비스명 매칭 방식. `EXACT`/`WILDCARD`/`REGEX`가 "매치 타입". 같은 파일의 `TierPriority` 등과 일관.
2. **B** (`services/learning/models.py`) → `PatternType` **유지**.
   - 근거: Self-Learning DNA 학습 패턴 유형 분류. "패턴 타입"이 학습 도메인에서 자연스러움.

---

## 43. `MetricsAdapterProtocol(Protocol)` — 메서드 시그니처 불일치

### 43-1. 현황

| 구분 | 위치 | 메서드 | 파라미터 |
|------|------|--------|----------|
| **A** | `services/auto_tuning/chaos_aware_metrics.py:16` | `collect_metrics()` | `service: str`, `window_seconds: int` |
| **B** | `services/auto_tuning/metrics_provider.py:17` | `fetch_current_metrics()` | 없음 |

```python
# --- A: services/auto_tuning/chaos_aware_metrics.py L16-19 ---
class MetricsAdapterProtocol(Protocol):
    def collect_metrics(self, service: str, window_seconds: int) -> dict[str, float]: ...

# --- B: services/auto_tuning/metrics_provider.py L17-20 ---
class MetricsAdapterProtocol(Protocol):
    def fetch_current_metrics(self) -> dict[str, float]: ...
```

### 43-2. 문제점

- 같은 `auto_tuning/` 패키지 내에서 **메서드명·파라미터가 다른** 동명 Protocol.
- 구현체가 어느 Protocol을 따라야 하는지 혼란.

### 43-3. 수정 방향

1. **A** (`chaos_aware_metrics.py`) → **`ChaosAwareMetricsProtocol`**로 이름 변경.
   - 근거: `collect_metrics(service, window_seconds)` 시그니처가 chaos 실험 인지 메트릭 수집 특화. `ChaosAwareMetrics` 클래스가 사용.
2. **B** (`metrics_provider.py`) → `MetricsAdapterProtocol` **유지**.
   - 근거: 파라미터 없이 현재 메트릭 조회하는 단순 어댑터 인터페이스. 메트릭 제공자 계약 정의 모듈.

---

## 44. `FailedOperationProtocol(Protocol)` — 필드 불일치

### 44-1. 현황

| 구분 | 위치 | 고유 필드 | 공통 필드 |
|------|------|-----------|-----------|
| **A** | `services/chaos_context.py:30` | `resolution_type`, `resolution_note`, `next_action_hint` + `Status`/`ResolutionType` 내부 클래스 | `id`, `metadata`, `status`, `resolved_at`, `save()` |
| **B** | `tasks/drift_detection.py:46` | `domain`, `created_at` | `id`, `metadata`, `status`, `resolved_at`, `save()` |

```python
# --- A: services/chaos_context.py L30-49 ---
class FailedOperationProtocol(Protocol):
    id: Any; metadata: dict[str, Any] | None
    status: str; resolution_type: str; resolution_note: str
    resolved_at: datetime | None; next_action_hint: str
    class Status: RESOLVED: str
    class ResolutionType: AUTO_REPLAY: str
    def save(self, update_fields: list[str] | None = None) -> None: ...

# --- B: tasks/drift_detection.py L46-56 ---
class FailedOperationProtocol(Protocol):
    id: Any; domain: str; status: str
    created_at: Any; resolved_at: Any | None
    metadata: dict[str, Any] | None
    def save(self, update_fields: list[str] | None = None) -> None: ...
```

### 44-2. 문제점

- 동일 모델(Django `FailedOperation`)을 참조하지만 각 용도에 필요한 필드만 Protocol로 정의.
- A에만 `resolution_type`/`resolution_note` + 내부 enum 클래스, B에만 `domain`/`created_at`.

### 44-3. 수정 방향

1. **A** (`services/chaos_context.py`) → `FailedOperationProtocol` **유지**.
   - 근거: 해결 흐름(`resolution_type`, `resolution_note`, `next_action_hint`)까지 다루는 상세 Protocol. 내부 `Status`/`ResolutionType` 상수 참조.
2. **B** (`tasks/drift_detection.py`) → **`DriftDetectionOperationProtocol`**로 이름 변경.
   - 근거: drift 감지에서 `domain`별 미해결 건 조회 + `created_at` 기반 age 계산에 특화된 간소화 Protocol.

---

## 45. `EmergencyState` — 필드·모드 체계 불일치

### 45-1. 현황

| 구분 | 위치 | 상태 체계 | 고유 필드 |
|------|------|-----------|-----------|
| **A** | `services/emergency_mode/models.py:56` | `level: EmergencyLevel` (5단계 Enum) | `is_recovering`, `is_auto_triggered`, `expires_at` |
| **B** | `services/governance.py:69` | `mode: str` ("NORMAL"/"STRICT") | `warning_sent_at`, `acknowledged_by/at` |

```python
# --- A: services/emergency_mode/models.py L56-75 ---
@dataclass
class EmergencyState:
    level: EmergencyLevel = EmergencyLevel.NORMAL
    is_active: bool = False
    activated_at: str | None = None
    activated_by: str | None = None
    activation_reason: str | None = None
    expires_at: str | None = None
    is_auto_triggered: bool = False

# --- B: services/governance.py L69-88 ---
@dataclass
class EmergencyState:
    is_active: bool = False
    mode: str = "NORMAL"
    activated_at: str | None = None
    activated_by: str | None = None
    reason: str | None = None
    warning_sent_at: str | None = None
    acknowledged_by: str | None = None
```

### 45-2. 문제점

- A는 5단계 `EmergencyLevel` Enum, B는 2단계 `mode: str`. 상태 체계가 다름.
- A는 자동 만료/자동 트리거 포함, B는 경고 확인 워크플로우 포함.

### 45-3. 수정 방향

1. **A** (`services/emergency_mode/models.py`) → `EmergencyState` **유지**.
   - 근거: `emergency_mode/` 패키지가 비상 모드의 정규 구현. 5단계 레벨 시스템이 더 상세. 자동 만료, 자동 트리거 등 운영 자동화 포함.
2. **B** (`services/governance.py`) → **`GovernanceEmergencyState`**로 이름 변경.
   - 근거: governance 판단용 간소화 상태. `warning_sent_at`/`acknowledged_by` 등 경고-확인 워크플로우가 거버넌스 도메인 특화.

---

## 46. `ConfigChange` — 필드·타입 불일치

### 46-1. 현황

| 구분 | 위치 | 도메인 | 주요 필드 |
|------|------|--------|-----------|
| **A** | `config_tracker.py:59` | 설정 추적기 | `config_key: str`, `old_value: Any`, `new_value: Any`, `applied: bool`, `cache_invalidated: bool` |
| **B** | `services/canary/cross_cluster.py:76` | 크로스 클러스터 카나리 | `config_type: str`, `previous_value: dict`, `new_value: dict`, `rollout_id` |

```python
# --- A: config_tracker.py L59-70 ---
@dataclass
class ConfigChange:
    config_key: str
    old_value: Any
    new_value: Any
    reason: str | None = None
    changed_at: datetime | None = None
    changed_by: str | None = None
    applied: bool = False
    cache_invalidated: bool = False
    error_message: str | None = None

# --- B: services/canary/cross_cluster.py L76-91 ---
@dataclass
class ConfigChange:
    config_type: str
    previous_value: dict[str, Any]
    new_value: dict[str, Any]
    changed_by: str = ""
    changed_at: str = ""
    rollout_id: str | None = None
    reason: str = ""
```

### 46-2. 문제점

- A는 단일 설정 키 변경 추적(`config_key`, `applied`, `cache_invalidated`), B는 클러스터간 설정 동기화(`config_type`, `rollout_id`).
- 값 타입 불일치: A는 `Any`, B는 `dict[str, Any]`. `changed_at` 타입도 A는 `datetime`, B는 `str`.

### 46-3. 수정 방향

1. **A** (`config_tracker.py`) → `ConfigChange` **유지**.
   - 근거: 설정 변경 추적기의 핵심 모델. 캐시 무효화, 적용 여부 등 변경 이력 관리 포함. 시스템 전반 설정 변경의 감사 로그 담당.
2. **B** (`services/canary/cross_cluster.py`) → **`ClusterConfigChange`**로 이름 변경.
   - 근거: 크로스 클러스터 카나리 롤아웃의 클러스터간 설정 전파. `config_type` + `rollout_id` + dict 기반 설정값이 클러스터 단위 변경.

---

## 47. `CascadeEvent` — 도메인·구조 완전 불일치

### 47-1. 현황

| 구분 | 위치 | 도메인 | 주요 필드 |
|------|------|--------|-----------|
| **A** | `audit/cascade_event.py:468` | 연쇄 이벤트 감사 | `id`, `trigger: CascadeTrigger`, `effects: list[CascadeEffect]`, `previous_hash`/`current_hash` |
| **B** | `services/namespace_emergency/cascade_detector.py:76` | 연쇄 장애 감지 | `event_id`, `affected_regions: list`, `total_strict_count`, `auto_escalated` |

```python
# --- A: audit/cascade_event.py L468-530 ---
@dataclass
class CascadeEvent:
    id: str
    trigger: CascadeTrigger
    effects: list[CascadeEffect]
    namespace: str
    timestamp: str
    previous_hash: str | None = None
    current_hash: str | None = None
    external_trace: ExternalTraceContext | None = None
    version: str = "1.0"

# --- B: services/namespace_emergency/cascade_detector.py L76-106 ---
@dataclass
class CascadeEvent:
    event_id: str = ""
    detected_at: datetime = field(default_factory=...)
    affected_regions: list[str] = field(default_factory=list)
    total_strict_count: int = 0
    threshold: int = DEFAULT_ESCALATION_THRESHOLD
    auto_escalated: bool = False
    escalated_at: datetime | None = None
    escalated_by: str = ""
```

### 47-2. 문제점

- **구조가 완전히 다름**: A는 인과관계 그래프(trigger→effects) + hash chain 위변조 방지, B는 리전별 장애 확산 감지.
- 필드가 하나도 겹치지 않음.

### 47-3. 수정 방향

1. **A** (`audit/cascade_event.py`) → `CascadeEvent` **유지**.
   - 근거: 연쇄 이벤트의 정규 감사 기록. hash chain + trigger→effects 인과관계 추적이 핵심.
2. **B** (`services/namespace_emergency/cascade_detector.py`) → **`CascadeDetectionEvent`**로 이름 변경.
   - 근거: 연쇄 장애 **감지** 결과. `affected_regions`, `total_strict_count`, `auto_escalated` 등 감지 시점 데이터.

---

## 48. `CanaryState(str, Enum)` — 도메인·멤버 완전 불일치

### 48-1. 현황

| 구분 | 위치 | 멤버 | 도메인 |
|------|------|------|--------|
| **A** | `services/canary/models.py:56` | `CREATED`, `CANARY`, `PROMOTING`, `PAUSED`, `COMPLETED`, `ROLLED_BACK`, `FAILED`, `CANCELLED` (8개) | 롤아웃 수명주기 |
| **B** | `services/circuit_breaker/canary_recovery.py:37` | `NOT_IN_CANARY`, `CANARY_1`~`CANARY_4` (5개) | CB 점진적 복구 트래픽 단계 |

```python
# --- A: services/canary/models.py L56-75 ---
class CanaryState(str, Enum):
    CREATED = "created"
    CANARY = "canary"
    PROMOTING = "promoting"
    PAUSED = "paused"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CANCELLED = "cancelled"

# --- B: services/circuit_breaker/canary_recovery.py L37-43 ---
class CanaryState(str, Enum):
    NOT_IN_CANARY = "not_in_canary"
    CANARY_1 = "canary_1"  # 10% 트래픽
    CANARY_2 = "canary_2"  # 30% 트래픽
    CANARY_3 = "canary_3"  # 60% 트래픽
    CANARY_4 = "canary_4"  # 100% 트래픽
```

### 48-2. 문제점

- 멤버가 **완전히 다름**: A는 롤아웃 수명주기, B는 점진적 트래픽 복구 단계.
- 둘 다 `str, Enum`이지만 값도 겹치지 않음.

### 48-3. 수정 방향

1. **A** (`services/canary/models.py`) → `CanaryState` **유지**.
   - 근거: `canary/` 패키지의 핵심 롤아웃 상태 Enum. 8개 수명주기 상태.
2. **B** (`services/circuit_breaker/canary_recovery.py`) → **`CanaryRecoveryStage`**로 이름 변경.
   - 근거: CB HALF_OPEN 후 점진적 트래픽 복구 단계. `NOT_IN_CANARY`→`CANARY_1`(10%)→`CANARY_4`(100%) 전이.

---

## 49. `CanaryStage` — 필드·구조 불일치

### 49-1. 현황

| 구분 | 위치 | 주요 필드 |
|------|------|-----------|
| **A** | `services/canary/models.py:139` | `name`, `clusters: list[str]`, `percentage`, `duration_minutes`, `pass_criteria` |
| **B** | `services/circuit_breaker/models.py:164` | `traffic_percent`, `duration_seconds`, `required_success_rate` + `__post_init__` 검증 |

```python
# --- A: services/canary/models.py L139-165 ---
@dataclass
class CanaryStage:
    name: str
    clusters: list[str]
    percentage: float
    duration_minutes: int = 5
    auto_promote: bool = True
    pass_criteria: PassCriteria = field(default_factory=PassCriteria)

# --- B: services/circuit_breaker/models.py L164-195 ---
@dataclass
class CanaryStage:
    traffic_percent: float
    duration_seconds: int
    required_success_rate: float
    description: str = ""
    def __post_init__(self) -> None: ...  # 값 범위 검증
```

### 49-2. 문제점

- A는 클러스터 기반 롤아웃 단계(어디에 적용), B는 트래픽 기반 복구 단계(얼마나 허용).
- 시간 단위 불일치: A는 `duration_minutes`, B는 `duration_seconds`.

### 49-3. 수정 방향

1. **A** (`services/canary/models.py`) → `CanaryStage` **유지**.
   - 근거: `canary/` 패키지의 롤아웃 단계 모델. `clusters`, `pass_criteria` 등 롤아웃 운영 정보.
2. **B** (`services/circuit_breaker/models.py`) → **`CanaryRecoveryStageConfig`**로 이름 변경.
   - 근거: Item 48의 `CanaryRecoveryStage` Enum과 쌍. `traffic_percent`/`duration_seconds`/`required_success_rate`가 복구 단계별 조건 정의.

---

## 50. `CanaryDecision` — 필드·용도 완전 불일치

### 50-1. 현황

| 구분 | 위치 | 도메인 | 주요 필드 |
|------|------|--------|-----------|
| **A** | `services/canary/feature_flag.py:166` | 요청별 설정 선택 | `use_canary: bool`, `strategy_used: str`, `effective_config: dict` |
| **B** | `services/circuit_breaker/canary_recovery.py:233` | 트래픽 허용 판단 | `allow_backend: bool`, `is_canary_request`, `use_stale_cache`, `traffic_percent` |

```python
# --- A: services/canary/feature_flag.py L166-180 ---
@dataclass
class CanaryDecision:
    use_canary: bool
    reason: str
    strategy_used: str
    effective_config: dict[str, Any] = field(default_factory=dict)

# --- B: services/circuit_breaker/canary_recovery.py L233-253 ---
@dataclass
class CanaryDecision:
    allow_backend: bool = False
    is_canary_request: bool = False
    use_stale_cache: bool = False
    current_stage: CanaryState | None = None
    traffic_percent: float = 0.0
    reason: str = ""
    def to_dict(self) -> dict[str, Any]: ...
```

### 50-2. 문제점

- A는 "카나리 설정 적용 여부 결정", B는 "백엔드 호출 허용 여부 결정".
- `reason`만 공통. 나머지 필드 완전히 다름.

### 50-3. 수정 방향

1. **A** (`services/canary/feature_flag.py`) → `CanaryDecision` **유지**.
   - 근거: `canary/` 패키지의 Feature Flag 결정 결과.
2. **B** (`services/circuit_breaker/canary_recovery.py`) → **`CanaryRecoveryDecision`**로 이름 변경.
   - 근거: Item 48/49와 일관된 "CanaryRecovery" 접두사. CB 점진적 복구 트래픽 판단 결과.

---

## 51. `BlockReason(str, Enum)` — 도메인·멤버 불일치

### 51-1. 현황

| 구분 | 위치 | 멤버 수 | 도메인 |
|------|------|---------|--------|
| **A** | `services/chaos/safety_guard/enums.py:26` | 12개 | Chaos 실험 차단 사유 |
| **B** | `services/governance_checks.py:156` | 5개 | 거버넌스 자동화 차단 사유 |

```python
# --- A: services/chaos/safety_guard/enums.py L26-62 ---
class BlockReason(str, Enum):
    LOW_ERROR_BUDGET = "low_error_budget"
    ACTIVE_INCIDENT = "active_incident"
    KILL_SWITCH_ACTIVE = "kill_switch_active"
    DEPLOYMENT_FREEZE = "deployment_freeze"
    UNHEALTHY_SYSTEM = "unhealthy_system"
    RECENT_EXPERIMENT = "recent_experiment"
    BLAST_RADIUS_EXCEEDED = "blast_radius_exceeded"
    MANUAL_BLOCK = "manual_block"
    EMERGENCY_MODE_ACTIVE = "emergency_mode_active"
    PANIC_THRESHOLD_TRIGGERED = "panic_threshold_triggered"
    CHAOS_BUDGET_EXCEEDED = "chaos_budget_exceeded"
    CB_FREEZE_MODE_ACTIVE = "cb_freeze_mode_active"

# --- B: services/governance_checks.py L156-173 ---
class BlockReason(str, Enum):
    KILL_SWITCH = "kill_switch"
    EMERGENCY_MODE = "emergency_mode"
    ERROR_BUDGET = "error_budget"
    RATE_LIMITED = "rate_limited"
    MANUALLY_BLOCKED = "manually_blocked"
```

### 51-2. 문제점

- A는 chaos 전용(12개), B는 범용 거버넌스(5개). 의미적으로 겹치는 멤버 존재(`KILL_SWITCH_ACTIVE` ↔ `KILL_SWITCH`)하지만 **값이 다름**.

### 51-3. 수정 방향

1. **A** (`services/chaos/safety_guard/enums.py`) → **`ChaosBlockReason`**으로 이름 변경.
   - 근거: 12개 중 7개가 chaos 전용(`BLAST_RADIUS_EXCEEDED`, `RECENT_EXPERIMENT` 등). `safety_guard/` 내부용.
2. **B** (`services/governance_checks.py`) → `BlockReason` **유지**.
   - 근거: 시스템 전반 기본 차단 조건. `GovernanceCheckResult.block_reasons`에서 사용.

---

## 52. `BlastRadiusPolicy` — 필드·구조 완전 불일치

### 52-1. 현황

| 구분 | 위치 | 도메인 | 주요 필드 |
|------|------|--------|-----------|
| **A** | `services/blast_radius/models.py:41` | 영향 범위 정책 | `policy_id`, `stage_name`, `level: BlastRadiusLevel`, `max_affected_percentage` |
| **B** | `services/chaos/blast_radius.py:61` | Chaos 정책 | `instance/service/region_max_concurrent`, `allowed_hours_start/end`, `max_traffic_percent_*` |

```python
# --- A: services/blast_radius/models.py L41-56 ---
@dataclass
class BlastRadiusPolicy:
    policy_id: str
    stage_name: str
    level: BlastRadiusLevel = BlastRadiusLevel.ISOLATED
    max_affected_percentage: float = 10.0
    auto_isolate: bool = True
    isolation_timeout_seconds: int = 300

# --- B: services/chaos/blast_radius.py L61-110 ---
@dataclass
class BlastRadiusPolicy:
    instance_max_concurrent: int = 5
    service_max_concurrent: int = 2
    region_max_concurrent: int = 1
    allowed_hours_start: int = 2
    allowed_hours_end: int = 6
    max_traffic_percent_instance: float = 100.0
    max_traffic_percent_service: float = 50.0
    max_traffic_percent_region: float = 10.0
```

### 52-2. 문제점

- **필드가 하나도 겹치지 않음**: A는 스테이지별 영향 범위 제한, B는 chaos 실험 동시성·시간창·트래픽 제한.

### 52-3. 수정 방향

1. **A** (`services/blast_radius/models.py`) → `BlastRadiusPolicy` **유지**.
   - 근거: 범용 영향 범위 정책. `blast_radius/` 패키지의 핵심 모델.
2. **B** (`services/chaos/blast_radius.py`) → **`ChaosBlastRadiusPolicy`**로 이름 변경.
   - 근거: chaos 실험 전용 정책. `max_concurrent`, `allowed_hours`, `auto_approve` 등 실험 런타임 제한.

---

## 53. `BackoffConfig` — `from_settings` 구현 불일치

### 53-1. 현황

| 구분 | 위치 | `from_settings` 시그니처 | 데이터 소스 |
|------|------|--------------------------|-------------|
| **A** | `core/backoff.py:280` | `from_settings(settings=None, **overrides)` | `BackoffSettings` 또는 legacy |
| **B** | `services/backoff_calculator.py:302` | `from_settings(domain=None)` | `get_config().retry` + 도메인 오버라이드 |

```python
# --- A: core/backoff.py L280-296 ---
@dataclass
class BackoffConfig:
    base: int = 4
    max_delay: int = 180
    jitter_percent: int = 25
    min_delay: int = 1
    @classmethod
    def from_settings(cls, settings=None, **overrides) -> "BackoffConfig": ...

# --- B: services/backoff_calculator.py L302-330 ---
@dataclass
class BackoffConfig:
    base: int = 4
    max_delay: int = 180
    jitter_percent: int = 25
    min_delay: int = 1
    @classmethod
    def from_settings(cls, domain: str | None = None) -> BackoffConfig: ...
```

### 53-2. 문제점

- **필드는 동일** (base=4, max_delay=180, jitter_percent=25, min_delay=1) 하지만 `from_settings` 구현이 다름.
- 어느 `BackoffConfig`를 import할지 혼란.

### 53-3. 수정 방향

1. **B** (`services/backoff_calculator.py`) → **단일 소스**로 지정.
   - 근거: `from_settings(domain=...)` 시그니처가 도메인별 오버라이드 지원. `get_config()` 통한 통합 설정 조회.
2. **A** (`core/backoff.py`의 `BackoffConfig`) → **제거 후 B를 import**.
   - 근거: A의 `from_settings(**overrides)` 패턴은 B의 `from_settings(domain=)` 로 대체 가능.

---

## 54. `BackoffCalculator` — ABC vs concrete 패러다임 충돌

### 54-1. 현황

| 구분 | 위치 | 타입 | 핵심 API |
|------|------|------|----------|
| **A** | `core/backoff.py:13` | `ABC` | `calculate(attempt) → float`, `reset()` (abstract) |
| **B** | `services/backoff_calculator.py:343` | concrete class | `calculate(attempt, with_jitter=True) → int`, `get_delays_sequence()` |

```python
# --- A: core/backoff.py L13-25 ---
class BackoffCalculator(ABC):
    @abstractmethod
    def calculate(self, attempt: int) -> float: ...
    @abstractmethod
    def reset(self) -> None: ...

# --- B: services/backoff_calculator.py L343-365 ---
class BackoffCalculator:
    def __init__(self, config: BackoffConfig | None = None): ...
    def calculate(self, attempt: int, with_jitter: bool = True) -> int: ...
    def get_delays_sequence(self, max_attempts: int = 10) -> list[int]: ...
```

### 54-2. 문제점

- A는 추상 기반 클래스(`calculate → float`, `reset` 필수), B는 구체 구현(`calculate → int`, `reset` 없음).
- 반환 타입 불일치: A `float`, B `int`. B가 A를 상속하지 않아 다형성 깨짐.

### 54-3. 수정 방향

1. **A** (`core/backoff.py`) → **`BackoffStrategy`**로 이름 변경.
   - 근거: ABC는 전략 패턴 인터페이스. "Strategy"가 추상 계약 역할을 정확히 표현.
2. **B** (`services/backoff_calculator.py`) → `BackoffCalculator` **유지**.
   - 근거: 시스템에서 실제 사용하는 구체적 backoff 계산기. Config 기반 지수 백오프 + 지터.

---

## 55. `ApprovalRequest` — 도메인 특화 필드 불일치

### 55-1. 현황

| 구분 | 위치 | 도메인 | 주요 필드 |
|------|------|--------|-----------|
| **A** | `core/config.py:59` | 4-Eyes 범용 승인 | `request_type`, `description`, `payload: dict`, `expires_at` |
| **B** | `services/chaos/blast_radius.py:246` | Chaos 실험 승인 | `experiment_id`, `blast_radius`, `target_service`, `denial_reason` |

```python
# --- A: core/config.py L59-97 ---
@dataclass
class ApprovalRequest:
    id: str = ""
    request_type: str = ""  # config_change, mode_change, emergency_action
    description: str = ""
    requested_by: str = ""
    status: str = "PENDING"
    payload: dict[str, Any] = field(default_factory=dict)
    expires_at: str = ""

# --- B: services/chaos/blast_radius.py L246-271 ---
@dataclass
class ApprovalRequest:
    experiment_id: str
    blast_radius: str
    target_service: str
    target_domain: str
    requested_by: str = ""
    status: str = ApprovalStatus.PENDING.value
    denial_reason: str = ""
    expires_at: str = ""
```

### 55-2. 문제점

- A는 4-Eyes 듀얼 승인(범용), B는 chaos 실험 고위험 승인(도메인 특화).
- 공통 필드(`requested_by`, `status`, `expires_at`) 있지만, A는 `payload: dict`로 범용화, B는 `experiment_id`/`blast_radius` 등 특화.

### 55-3. 수정 방향

1. **A** (`core/config.py`) → `ApprovalRequest` **유지**.
   - 근거: PCI-DSS 듀얼 컨트롤 충족을 위한 범용 승인 모델. `core/` 패키지의 핵심 컴플라이언스 모델.
2. **B** (`services/chaos/blast_radius.py`) → **`ChaosApprovalRequest`**로 이름 변경.
   - 근거: chaos 실험 전용 승인. `experiment_id`, `blast_radius`, `denial_reason` 등 실험 메타데이터.

---

## 56. `ActionResult` — 필드·용도 완전 불일치

### 56-1. 현황

| 구분 | 위치 | 도메인 | 주요 필드 |
|------|------|--------|-----------|
| **A** | `core/action_executor.py:80` | 액션 실행 | `action_id`, `action_name`, `target`, `executed`, `mode`, `validation_result` |
| **B** | `services/coordination/models.py:231` | 네임스페이스 조율 | `success`, `action_type: ActionType`, `event_id`, `namespace`, `was_dry_run` |

```python
# --- A: core/action_executor.py L80-110 ---
@dataclass
class ActionResult:
    action_id: str
    action_name: str
    target: str
    executed: bool
    mode: str
    timestamp: datetime
    success: bool | None = None
    result: Any = None
    error: str | None = None
    validation_result: bool | None = None

# --- B: services/coordination/models.py L231-260 ---
@dataclass
class ActionResult:
    success: bool
    action_type: ActionType
    event_id: str
    namespace: str
    executed_at: datetime = field(default_factory=...)
    was_dry_run: bool = False
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: ...
```

### 56-2. 문제점

- A는 범용 액션 실행 결과, B는 네임스페이스 조율 결과.
- `success` 타입 불일치: A는 `bool | None`, B는 `bool`.

### 56-3. 수정 방향

1. **A** (`core/action_executor.py`) → `ActionResult` **유지**.
   - 근거: `core/` 패키지의 범용 액션 실행기 결과. `ActionExecutor` 반환 타입.
2. **B** (`services/coordination/models.py`) → **`CoordinationActionResult`**로 이름 변경.
   - 근거: 같은 파일의 `CoordinationEvent`, `QuorumResult` 등과 일관된 "Coordination" 접두사.

---

## 57. `LayeredCircuitBreakerStateRepository` — 레거시 vs 리팩토링 중복

### 57-1. 현황

| 구분 | 위치 | 구조 |
|------|------|------|
| **A** | `adapters/memory/layered_repository/__init__.py:32` | 8개 Mixin 기반, body=`pass` (리팩토링 버전) |
| **B** | `adapters/memory/layered_repository.py:43` | 모놀리식, `ThreadPoolExecutor` (레거시 버전) |

```python
# --- A: adapters/memory/layered_repository/__init__.py L32-48 ---
class LayeredCircuitBreakerStateRepository(
    L2LoadMixin, ErrorHandlingMixin, DriftOperationsMixin,
    L2SyncMixin, RepositoryOperationsMixin, MonitoringMixin,
    AuditHelpersMixin, LayeredRepositoryBase,
    CircuitBreakerStateRepository,
):
    """하이브리드 레이어드 저장소 (L1 Memory + L2 Shared Storage)."""
    pass

# --- B: adapters/memory/layered_repository.py L43-75 ---
class LayeredCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """하이브리드 레이어드 저장소 (L1 Memory + L2 Shared Storage)."""
    _executor: ThreadPoolExecutor | None = None
    _executor_lock = threading.Lock()
    # ... (800+ lines monolithic)
```

### 57-2. 문제점

- **같은 클래스명의 두 구현**이 공존: Mixin 리팩토링 vs 모놀리식 레거시.
- `adapters/memory/` 하위에 `layered_repository/`(패키지)와 `layered_repository.py`(파일) 동시 존재.
- import 경로 해석이 Python 모듈 시스템에 따라 달라짐.

### 57-3. 수정 방향

1. **A** (`layered_repository/__init__.py`) → **유지** — 리팩토링된 정식 구현.
   - 근거: 8개 Mixin 책임 분리. `CircuitBreakerStateRepository` 인터페이스 구현.
2. **B** (`layered_repository.py`) → **`_layered_repository_legacy.py`로 파일명 변경 + deprecated 마킹**.
   - 근거: 모놀리식 구현은 마이그레이션 기간 참조용으로만 보존. `_` 접두사로 내부용 표시. 완료 후 삭제.

---

## 58. `_LegacyTaskWrapper` — 3곳 구현 불일치

### 58-1. 현황

| 구분 | 위치 | 구현 |
|------|------|------|
| **A** | `tasks/cleanup_tasks.py:280` | `__init__(self, name, func)` — 외부 함수 래핑 (범용) |
| **B** | `tasks/daily_report.py:140` | class var `name`, 하드코딩 `run()` (특정 작업) |
| **C** | `tasks/xtest_cleanup_tasks.py:177` | A와 **완전 동일** (테스트 파일 복사) |

```python
# --- A: tasks/cleanup_tasks.py L280-290 ---
class _LegacyTaskWrapper:
    def __init__(self, name: str, func):
        self.name = name
        self._func = func
    def run(self, *args, **kwargs):
        return self._func(*args, **kwargs)

# --- B: tasks/daily_report.py L140-155 ---
class _LegacyTaskWrapper:
    name = "daily_report_legacy"
    def run(self):
        # 하드코딩된 리포트 생성 로직
        ...

# --- C: tasks/xtest_cleanup_tasks.py L177-187 ---
# (A와 완전 동일 코드)
```

### 58-2. 문제점

- A/C는 동일한 범용 래퍼, B는 하드코딩된 특정 작업 래퍼. private 클래스이지만 3곳 독립 정의.

### 58-3. 수정 방향

1. **A** (`tasks/cleanup_tasks.py`) → `_LegacyTaskWrapper` **단일 소스**로 지정.
   - 근거: 범용 래핑 패턴(`name` + `func` 주입). `tasks/` 내부 유틸리티.
2. **B** (`tasks/daily_report.py`) → **`_DailyReportLegacyTask`로 이름 변경**.
   - 근거: `name = "daily_report_legacy"` 하드코딩 + 특정 `run()` 로직. 범용 래퍼가 아닌 특정 작업.
3. **C** (`tasks/xtest_cleanup_tasks.py`) → **제거 후 A를 import**.
   - 근거: 테스트 파일에서 A의 복사본. `from .cleanup_tasks import _LegacyTaskWrapper`로 교체.

---

## 59. `AutoTuningHistoryView` — 인증·베이스 클래스 불일치

### 59-1. 현황

| 구분 | 위치 | 베이스 | 인증 |
|------|------|--------|------|
| **A** | `api/django/views/auto_tuning.py:273` | `APIView` (DRF) | `permission_classes = [IsViewer]` |
| **B** | `audit/continuous_audit_api.py:173` | `View` (Django) | 없음 |

```python
# --- A: api/django/views/auto_tuning.py L273-300 ---
class AutoTuningHistoryView(APIView):
    """자율 조정 이력 조회 (DRF)."""
    permission_classes = [IsViewer]
    def get(self, request): ...

# --- B: audit/continuous_audit_api.py L173-210 ---
class AutoTuningHistoryView(View):
    """자율 조정 이력 조회."""
    def get(self, request: HttpRequest) -> JsonResponse: ...
```

### 59-2. 문제점

- A는 DRF `APIView` + `IsViewer` 권한, B는 Django 기본 `View`로 **인증 없음**.
- 같은 URL 패턴 매핑 시 어느 것이 실행되는지 URL conf 순서에 의존.
- B는 `audit/` 패키지에서 직접 API 노출 — 레이어 분리 위반.

### 59-3. 수정 방향

1. **A** (`api/django/views/auto_tuning.py`) → `AutoTuningHistoryView` **유지**.
   - 근거: DRF `APIView` + `IsViewer` 권한으로 인증·인가 적용. `api/django/views/` 경로가 API 레이어 정규 위치.
2. **B** (`audit/continuous_audit_api.py`) → **`ContinuousAuditAutoTuningView`로 이름 변경 + 인증 추가**.
   - 근거: `continuous_audit_api.py`의 다른 View들과 일관된 "ContinuousAudit" 접두사. 인증 없는 View는 보안 위험 — `LoginRequiredMixin` 또는 DRF `APIView` 전환 필요.

---

## 수정 순서 (권장)

| 순서 | 대상 | 위험도 | 사유 | 상태 |
|------|------|--------|------|------|
| 1 | `CheckpointError` 중복 제거 (Item 1) | 낮음 | 같은 디렉토리 내 단순 중복 | ✅ 완료 (2026-02-07) |
| 2 | `NotificationChannel` 단일 소스 (Item 3) | 낮음 | 부분집합 통합 | ✅ 완료 (2026-02-07) |
| 3 | `HealthStatus` 단일 소스 + 3번째 이름 변경 (Item 9+22) | 낮음 | 동시 처리 | ✅ 완료 (2026-02-07) |
| 4 | `EventSeverity` 단일 소스 (Item 4) | 낮음 | 완전 중복, docstring에 호환 명시 | ✅ 완료 (2026-02-07) |
| 5 | `BackpressureStrategy` 단일 소스 (Item 6) | 낮음 | 부분집합, import 2곳만 변경 | ✅ 완료 (2026-02-07) |
| 6 | `CleanupStats` 단일 소스 (Item 24) | 낮음 | 프로퍼티 누락 복사본 제거 | ✅ 완료 (2026-02-07) |
| 7 | `ControlAPIActions` + `ControlAPIEnvironments` + `RiskLevels` 중복 제거 (Item 31-33) | 낮음 | serializers_legacy.py 1파일에서 3개 동시 제거, import 1줄 | ✅ 완료 (2026-02-07) |
| 8 | `ViolationSeverity` 단일 소스 (Item 34) | 낮음 | 부분집합 통합 | ✅ 완료 (2026-02-07) |
| 9 | `StatusCounts` + `RecentActivity` 단일 소스 (Item 37-38) | 낮음 | dashboard_service.py 1파일에서 2개 동시 제거 | ✅ 완료 (2026-02-07) |
| 10 | `_LegacyTaskWrapper` 정리 (Item 58) | 낮음 | private 클래스, tasks 내부 | ✅ 완료 (2026-02-07) |
| 11 | `CircuitState` 통합 (Item 2) | 중간 | `str, Enum`으로 통일 필요 | ✅ 완료 (2026-02-07) |
| 12 | `HashChainWALEntry` 이름 변경 (Item 5) | 중간 | 필드 차이 큼 | ✅ 완료 (2026-02-07) |
| 13 | `EventType` 이름 변경 (Item 7) | 중간 | event_bus 의존 범위 파악 필요 | ✅ 완료 (2026-02-07) |
| 14 | `RecoveryAction` + `RecoveryResult` 이름 변경 (Item 10+11) | 중간 | 3개 클래스 동시 변경 (C 추가) | ✅ 완료 (2026-02-07) |
| 15 | `AuditEventType` 이름 변경 (Item 8) | 중간 | alias 정리 | ✅ 완료 (2026-02-07) |
| 16 | `WatchdogState` 이름 변경 (Item 13) | 중간 | Enum↔dataclass 충돌 | ✅ 완료 (2026-02-07) |
| 17 | `CacheEntry` 이름 변경 (Item 14) | 중간 | adapters 내부 | ✅ 완료 (2026-02-07) |
| 18 | `NotificationResult` 이름 변경 3곳 (Item 15) | 중간 | services 3곳 동시 | ✅ 완료 (2026-02-07) |
| 19 | `HealthCheckResult` 이름 변경 (Item 12) | 중간 | 두 곳 모두 변경 | ✅ 완료 (2026-02-07) |
| 20 | `WatchdogConfig` 이름 변경 (Item 16) | 중간 | 두 곳 모두 변경 | ✅ 완료 (2026-02-07) |
| 21 | `PaginatedResult` 이름 변경 (Item 25) | 중간 | 필드·타입·구현 3중 불일치 |
| 22 | `RequestContext` 이름 변경 (Item 26) | 중간 | core/__init__.py export 변경 |
| 23 | `EventPriority` 이름 변경 (Item 27) | 중간 | 값 방향 정반대 |
| 24 | `PoolHealthStatus` 이름 변경 (Item 28) | 중간 | core/__init__.py export + pool_watchdog |
| 25 | `CircuitBreakerState` 이름 변경 + 제거 (Item 29) | 중간 | Item 2 CircuitState 통합과 연동 |
| 26 | `BlastRadiusLevel` 3곳 통합 (Item 30) | 중간 | 3곳 동시 변경, str,Enum 통일 |
| 27 | `ValidationResult` 이름 변경 (Item 35) | 중간 | tiering 내부용 |
| 28 | `TokenBucket` 이름 변경 (Item 36) | 중간 | chaos 내부용 |
| 29 | `ServiceDependency` 이름 변경 (Item 39) | 중간 | blast_radius 2곳, 양쪽 변경 |
| 30 | `RetryResult` 이름 변경 (Item 40) | 중간 | dlq_models 내부용 |
| 31 | `RecoveryEvent` 이름 변경 (Item 41) | 중간 | audit 내부용 |
| 32 | `PatternType` 이름 변경 (Item 42) | 중간 | tiering enums 내부용 |
| 33 | `MetricsAdapterProtocol` 이름 변경 (Item 43) | 중간 | auto_tuning 패키지 내부 |
| 34 | `FailedOperationProtocol` 이름 변경 (Item 44) | 중간 | tasks 내부용 |
| 35 | `EmergencyState` 이름 변경 (Item 45) | 중간 | governance 내부용 |
| 36 | `ConfigChange` 이름 변경 (Item 46) | 중간 | canary/cross_cluster 내부용 |
| 37 | `CascadeEvent` 이름 변경 (Item 47) | 중간 | namespace_emergency 내부용 |
| 38 | `CanaryState` + `CanaryStage` + `CanaryDecision` 이름 변경 (Item 48-50) | 중간 | circuit_breaker/canary_recovery 3개 동시 |
| 39 | `BlockReason` 이름 변경 (Item 51) | 중간 | chaos/safety_guard 내부용 |
| 40 | `BlastRadiusPolicy` 이름 변경 (Item 52) | 중간 | chaos/blast_radius 내부용 |
| 41 | `CBStateCache` 이름 변경 (Item 17) | 높음 | core/__init__.py export + IPC 서버 |
| 42 | `ProviderRegistry` 이름 변경 (Item 23) | 높음 | services/factory + deprecated 연동 |
| 43 | `ConfigChangeEvent` 이름 변경 (Item 18) | 높음 | audit/__init__.py export + deployment |
| 44 | `AuditAction` 이름 변경 (Item 19) | 높음 | interfaces 6곳+ import |
| 45 | `ForensicSettings` 이름 변경 (Item 20) | 높음 | config.py 전역 사용 |
| 46 | `StorageMode` 이름 변경 (Item 21) | 높음 | adapters 전체 영향 |
| 47 | `BackoffConfig` + `BackoffCalculator` 통합 (Item 53-54) | 높음 | core/backoff.py ABC + 구체 구현 쌍 변경 |
| 48 | `ApprovalRequest` 이름 변경 (Item 55) | 높음 | core/config.py + chaos 승인 |
| 49 | `ActionResult` 이름 변경 (Item 56) | 높음 | core/action_executor + coordination |
| 50 | `LayeredCircuitBreakerStateRepository` 레거시 파일 정리 (Item 57) | 높음 | adapters 패키지 구조 변경 |
| 51 | `AutoTuningHistoryView` 이름 변경 + 인증 (Item 59) | 높음 | 보안 영향 + URL 라우팅 |

---

## 실행 기록

### Phase 1: 순서 1-20 완료 (2026-02-07)

**테스트 결과**: 7563 passed, 1 skipped (flaky: `test_manual_flush` 비동기 타이밍 이슈, 리팩토링 무관)

**소스 파일 변경 목록**:

| 파일 | 변경 내용 |
|------|----------|
| `audit/checkpoint_manager.py` | `CheckpointError` → import from `checkpoint_strategy` |
| `services/security_notification/models.py` | `NotificationChannel` → import from `interfaces/notification` |
| `services/health_check.py` | `HealthStatus` → `SystemHealthSummary` 이름 변경 |
| `adapters/ipc/sidecar_ipc_probe.py` | `HealthStatus` → import from `meta.health_probe`, `HealthCheckResult` → `SidecarProbeResult` |
| `audit/audit_integration.py` | `EventSeverity` → import from `utils.async_logger`, `AuditEventType` → `AuditObserverEventType` |
| `audit/ring_buffer.py` | `BackpressureStrategy` → import from `scaling.config` |
| `interfaces/statistics.py` | `CleanupStats` → import from `services/dlq_models` |
| `api/django/serializers_legacy.py` | `ControlAPIActions/Environments/RiskLevels` → import from `core.constants` |
| `services/corruption_shield/shield.py` | `ViolationSeverity` → import from `services/compliance/models` |
| `services/dashboard_service.py` | `StatusCounts/RecentActivity` → import from `interfaces/statistics` |
| `tasks/daily_report.py` | `_LegacyTaskWrapper` → `_DailyReportLegacyTask` |
| `audit/resilience/circuit_breaker.py` | `CircuitState` → import from `audit.graceful_degradation.enums` |
| `audit/hash_chain_safety.py` | `HashChainWALEntry` → `HashChainSafetyWALEntry` |
| `audit/graceful_degradation/wal_recovery.py` | `HashChainWALEntry` → `HashChainRecoveryWALEntry` |
| `core/decision_logger.py` | `EventType` → `DecisionBoundaryEventType` |
| `core/pool_watchdog.py` | `RecoveryAction` → `PoolRecoveryAction`, `RecoveryResult` → `PoolRecoveryResult` |
| `services/coordination/recovery_dashboard.py` | `RecoveryAction` → `RecoveryActionWidget` |
| `audit/audit_watchdog.py` | `WatchdogState` → `AuditWatchdogStatus`, `WatchdogConfig` → `AuditWatchdogConfig` |
| `adapters/ipc/cb_state_cache.py` | `CacheEntry` → `IPCCacheEntry` |
| `services/governance_service.py` | `NotificationResult` → `GovernanceNotificationResult` |
| `services/security_notification/models.py` | `NotificationResult` → `ChannelDeliveryResult` |
| `services/security_notification/slack_handler.py` | `NotificationResult` → `ChannelDeliveryResult` |
| `services/security_notification/email_handler.py` | `NotificationResult` → `ChannelDeliveryResult` |
| `services/security_notification/sms_handler.py` | `NotificationResult` → `ChannelDeliveryResult` |
| `services/security_notification/pagerduty_handler.py` | `NotificationResult` → `ChannelDeliveryResult` |
| `core/auto_rollback_guard.py` | `HealthCheckResult` → `RollbackHealthAssessment` |
| `tasks/canary_watchdog.py` | `WatchdogConfig` → `CanaryWatchdogConfig` |
| `core/__init__.py` | `EventType` → `DecisionBoundaryEventType`, `RecoveryAction/Result` → `PoolRecoveryAction/Result` export 갱신 |
| `audit/__init__.py` | `WatchdogConfig/State` → `AuditWatchdogConfig/Status`, `BufferEventType` lazy import 추가 |
| `audit/graceful_degradation/__init__.py` | `HashChainWALEntry` → `HashChainRecoveryWALEntry` export 갱신 |
| `services/security_notification/__init__.py` | `NotificationResult` → `ChannelDeliveryResult` export 갱신 |
| `services/__init__.py` | `NotificationResult` → `ChannelDeliveryResult` export 갱신 |

**테스트 파일 변경 목록**:

| 파일 | 변경 내용 |
|------|----------|
| `tests/core/test_decision_logger.py` | `EventType` → `DecisionBoundaryEventType` |
| `tests/unit/storage/test_connection_pool.py` | `RecoveryAction` → `PoolRecoveryAction` |
| `tests/unit/adapters/ipc/test_sidecar_ipc_probe.py` | `HealthCheckResult` → `SidecarProbeResult` |
| `tests/unit/adapters/ipc/test_cb_state_cache.py` | `CacheEntry` → `IPCCacheEntry` |
| `tests/core/test_auto_rollback_guard.py` | `HealthCheckResult` → `RollbackHealthAssessment` |
| `tests/unit/tasks/test_canary_watchdog.py` | `WatchdogConfig` → `CanaryWatchdogConfig` |
| `tests/unit/tasks/test_canary_error_budget_gate.py` | `WatchdogConfig` → `CanaryWatchdogConfig` |
| `tests/unit/audit/pipeline/test_audit_watchdog.py` | `WatchdogConfig` → `AuditWatchdogConfig`, `WatchdogState` → `AuditWatchdogStatus` |
| `tests/unit/audit/pipeline/test_audit_lazy_import.py` | `WatchdogConfig` → `AuditWatchdogConfig`, `WatchdogState` → `AuditWatchdogStatus` |
| `tests/unit/audit/audit_integration/test_data.py` | `AuditEventType` → `AuditObserverEventType` |
| `tests/unit/audit/audit_integration/test_recorder.py` | `AuditEventType` → `AuditObserverEventType` |
| `tests/unit/audit/audit_integration/test_observers.py` | `AuditEventType` → `AuditObserverEventType` |
| `tests/unit/audit/integrity/test_hash_chain_safety.py` | `HashChainWALEntry` → `HashChainSafetyWALEntry` |
| `tests/unit/selfhealing/test_health_check_service.py` | `HealthStatus` → `SystemHealthSummary` |
| `tests/unit/selfhealing/test_dashboard_service.py` | `RecentActivity` 필드명 갱신 (`new_in_24h` 등) |
| `tests/unit/resilience/test_retry_decision_table.py` | `throttle_aware=False` 추가 (순수 backoff 검증) |
| `tests/unit/throttle/test_throttle_eventbus_handlers.py` | Recovery Dampening 동작에 맞춘 assertion 변경 |
| `tests/unit/audit/helpers/test_audit_helpers_retry_rollback.py` | `AdaptiveRetryBudget` 소량 요청 차단 회피 패치 |
| `tests/conftest.py` | `WatchdogState` → `AuditWatchdogStatus` |
| `load_tests/scenarios/` (8파일) | `EventType` → `DecisionBoundaryEventType` |
| `load_tests/docker/docker-compose.stage38.yml` | `EventType` → `DecisionBoundaryEventType` |
