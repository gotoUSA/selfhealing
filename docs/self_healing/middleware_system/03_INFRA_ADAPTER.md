# Self-Healing 인프라 어댑터

> **Version**: 2.2.0
> **Updated**: 2026-01-02
> **Category**: 인프라/저장소/외부 시스템 연동

---

## 📋 목차

1. [개요](#1-개요)
2. [Adapters 모듈](#2-adapters-모듈)
3. [Audit Backends](#3-audit-backends)
4. [Audit 컴포넌트](#4-audit-컴포넌트)
5. [Notification 인터페이스](#5-notification-인터페이스)
6. [SQLAlchemy 어댑터](#6-sqlalchemy-어댑터)
7. [Django 어댑터](#7-django-어댑터)

---

## 1. 개요

이 문서는 Self-Healing 시스템의 **인프라/저장소/외부 시스템 연동**을 다룹니다.

### 1.1 범위

- 캐시 어댑터 (Redis, Memory)
- 태스크 큐 어댑터 (Celery, Sync)
- 설정 프로바이더
- 감사 로그 백엔드
- 알림 인터페이스
- ORM 어댑터 (Django, SQLAlchemy)

### 1.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       02_LOGIC_ENGINE (인터페이스)                        │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐       │
│  │ Repository  │ │ Cache       │ │ TaskQueue   │ │ Audit       │       │
│  │ Interface   │ │ Interface   │ │ Interface   │ │ Interface   │       │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘       │
└─────────┼───────────────┼───────────────┼───────────────┼───────────────┘
          │               │               │               │
          ▼               ▼               ▼               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       03_INFRA_ADAPTER (구현체)                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────┐  ┌─────────────────────┐                       │
│  │ Django Adapters     │  │ SQLAlchemy Adapters │                       │
│  │ - Repository        │  │ - Repository        │                       │
│  │ - ConfigProvider    │  │ - SessionFactory    │                       │
│  └─────────────────────┘  └─────────────────────┘                       │
│                                                                          │
│  ┌─────────────────────┐  ┌─────────────────────┐                       │
│  │ Cache Adapters      │  │ Queue Adapters      │                       │
│  │ - Redis             │  │ - Celery            │                       │
│  │ - Memory            │  │ - Sync              │                       │
│  └─────────────────────┘  └─────────────────────┘                       │
│                                                                          │
│  ┌─────────────────────┐  ┌─────────────────────┐                       │
│  │ Audit Backends      │  │ Notification        │                       │
│  │ - Postgres          │  │ - Slack             │                       │
│  │ - CloudWatch        │  │ - Teams             │                       │
│  │ - File              │  │ - PagerDuty         │                       │
│  └─────────────────────┘  └─────────────────────┘                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Adapters 모듈

**경로**: `selfhealing.adapters/`

### 2.1 Cache Adapters

**경로**: `selfhealing.adapters.cache/`

#### 2.1.1 RedisCacheAdapter

**경로**: `selfhealing.adapters.cache.redis_cache`

| 메서드 | 설명 |
|--------|------|
| `get(key)` | 값 조회 |
| `set(key, value, ttl)` | 값 저장 |
| `delete(key)` | 값 삭제 |
| `exists(key)` | 존재 여부 |
| `incr(key, delta)` | 증가 |
| `decr(key, delta)` | 감소 |
| `expire(key, ttl)` | TTL 설정 |

**설정**:
```python
REDIS_CACHE_CONFIG = {
    'host': 'localhost',
    'port': 6379,
    'db': 0,
    'password': None,
    'socket_timeout': 5,
    'retry_on_timeout': True,
}
```

#### 2.1.2 MemoryCacheAdapter

**경로**: `selfhealing.adapters.cache.memory_cache`

| 특징 | 설명 |
|------|------|
| 용도 | 테스트/개발 환경 |
| 저장소 | Python dict |
| TTL | time 기반 만료 |
| 스레드 안전 | threading.Lock 사용 |

### 2.2 Queue Adapters

**경로**: `selfhealing.adapters.queue/`

#### 2.2.1 CeleryTaskQueue

**경로**: `selfhealing.adapters.queue.celery_queue`

| 메서드 | 설명 |
|--------|------|
| `enqueue(task, *args, **kwargs)` | 태스크 큐잉 |
| `schedule(task, delay, *args, **kwargs)` | 예약 실행 |
| `get_status(task_id)` | 상태 조회 |
| `cancel(task_id)` | 태스크 취소 |
| `retry(task_id)` | 재시도 |

**상태 값**:
- `PENDING`: 대기 중
- `STARTED`: 실행 중
- `SUCCESS`: 성공
- `FAILURE`: 실패
- `REVOKED`: 취소됨

#### 2.2.2 SyncTaskQueue

**경로**: `selfhealing.adapters.queue.sync_queue`

| 특징 | 설명 |
|------|------|
| 용도 | 테스트/동기 실행 필요 시 |
| 실행 | 즉시 동기 실행 |
| 스케줄 | time.sleep 기반 |

### 2.3 Config Adapters

**경로**: `selfhealing.adapters.config/`

#### 2.3.1 DjangoConfigProvider

**경로**: `selfhealing.adapters.config.django_config`

| 메서드 | 설명 |
|--------|------|
| `get(key, default)` | settings에서 조회 |
| `set(key, value)` | 동적 설정 (제한적) |
| `reload()` | 설정 리로드 |

**프리픽스 규칙**:
```python
# settings.py
SELFHEALING_CIRCUIT_BREAKER_TIMEOUT = 30
SELFHEALING_DLQ_MAX_RETRIES = 5

# 조회
config.get("circuit_breaker.timeout")  # → 30
config.get("dlq.max_retries")          # → 5
```

#### 2.3.2 EnvConfigProvider

**경로**: `selfhealing.adapters.config.env_config`

| 특징 | 설명 |
|------|------|
| 소스 | 환경 변수 |
| 프리픽스 | `SELFHEALING_` |
| 타입 변환 | 자동 (int, bool, json) |

---

## 3. Audit Backends

**경로**: `selfhealing.audit.backends/`

### 3.1 PostgresAuditBackend

**경로**: `selfhealing.audit.backends.postgres`

| 특징 | 설명 |
|------|------|
| 저장소 | PostgreSQL 테이블 |
| 인덱스 | timestamp, actor, action |
| 파티션 | 월별 자동 파티션 |

**테이블 스키마**:
```sql
CREATE TABLE audit_log (
    id UUID PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    actor VARCHAR(255),
    action VARCHAR(100),
    resource_type VARCHAR(100),
    resource_id VARCHAR(255),
    details JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.2 CloudWatchAuditBackend

**경로**: `selfhealing.audit.backends.cloudwatch`

| 특징 | 설명 |
|------|------|
| 서비스 | AWS CloudWatch Logs |
| 로그 그룹 | `/selfhealing/audit` |
| 로그 스트림 | 날짜별 자동 생성 |

**설정**:
```python
CLOUDWATCH_AUDIT_CONFIG = {
    'log_group': '/selfhealing/audit',
    'region': 'ap-northeast-2',
    'retention_days': 90,
}
```

### 3.3 FileAuditBackend

**경로**: `selfhealing.audit.backends.file`

| 특징 | 설명 |
|------|------|
| 저장소 | 로컬 파일 시스템 |
| 형식 | JSON Lines |
| 로테이션 | 일별 로테이션 |

**파일 경로**: `logs/audit/audit-{date}.jsonl`

### 3.4 Composite Backend

**경로**: `selfhealing.audit.backends.composite`

| 특징 | 설명 |
|------|------|
| 용도 | 다중 백엔드 동시 기록 |
| 전략 | 팬아웃 (모든 백엔드에 전송) |
| 실패 처리 | 개별 실패 시 계속 진행 |

```python
composite = CompositeAuditBackend([
    PostgresAuditBackend(),
    CloudWatchAuditBackend(),
])
```

---

## 4. Audit 컴포넌트

**경로**: `selfhealing.audit/`

### 4.1 WAL (Write-Ahead Log)

**경로**: `selfhealing.audit.wal`

| 컴포넌트 | 용도 |
|---------|------|
| `WriteAheadLog` | WAL 관리자 |
| `WALEntry` | WAL 엔트리 |
| `WALRecovery` | 복구 핸들러 |

**기능**:
- 백엔드 실패 시 로컬 WAL에 기록
- 백엔드 복구 시 WAL 리플레이
- 체크포인트 기반 진행 추적

### 4.2 Ring Buffer

**경로**: `selfhealing.audit.ring_buffer`

| 컴포넌트 | 용도 |
|---------|------|
| `AuditRingBuffer` | 순환 버퍼 |
| `BufferConfig` | 버퍼 설정 |

**특징**:
- 고정 크기 메모리 버퍼
- 오래된 항목 자동 덮어쓰기
- 최근 N개 항목 빠른 조회

### 4.3 Signed Manifest

**경로**: `selfhealing.audit.manifest`

| 컴포넌트 | 용도 |
|---------|------|
| `SignedManifest` | 서명된 매니페스트 |
| `ManifestSigner` | 서명 생성기 |
| `ManifestVerifier` | 서명 검증기 |

**기능**:
- 감사 로그 무결성 보장
- HMAC-SHA256 서명
- 주기적 매니페스트 생성

### 4.4 Sensitive Masker

**경로**: `selfhealing.audit.masker`

| 컴포넌트 | 용도 |
|---------|------|
| `SensitiveMasker` | 민감 데이터 마스킹 |
| `MaskingRule` | 마스킹 규칙 |

**마스킹 패턴**:
- 신용카드: `****-****-****-1234`
- 이메일: `u***@domain.com`
- 전화번호: `010-****-5678`
- 주민번호: `******-*******`

### 4.5 Real-time Alerter

**경로**: `selfhealing.audit.alerter`

| 컴포넌트 | 용도 |
|---------|------|
| `RealTimeAlerter` | 실시간 알림 |
| `AlertRule` | 알림 규칙 |
| `AlertThrottle` | 알림 쓰로틀링 |

**트리거 조건**:
- 권한 변경
- 대량 데이터 접근
- 비정상 시간대 접근
- 실패한 인증 시도 급증

---

## 5. Notification 인터페이스

**경로**: `selfhealing.interfaces.notification`

### 5.1 인터페이스 정의

```python
class NotificationSeverity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class NotificationChannel(Enum):
    SLACK = "slack"
    TEAMS = "teams"
    PAGERDUTY = "pagerduty"
    EMAIL = "email"
    WEBHOOK = "webhook"
    SMS = "sms"
    STDOUT = "stdout"
    FILE = "file"

@dataclass
class Notification:
    title: str
    message: str
    severity: NotificationSeverity
    channel: NotificationChannel
    metadata: Dict[str, Any] = field(default_factory=dict)

class NotificationAdapter(ABC):
    @abstractmethod
    def send(self, notification: Notification) -> bool: ...
```

### 5.2 기본 어댑터

| 어댑터 | 설명 |
|--------|------|
| `StdoutNotificationAdapter` | 표준 출력 (기본) |
| `LoggingNotificationAdapter` | Python 로깅 |

### 5.3 커스텀 어댑터 등록

```python
from selfhealing.interfaces.notification import (
    NotificationAdapter,
    Notification,
    register_notification_adapter,
)

class SlackNotificationAdapter(NotificationAdapter):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, notification: Notification) -> bool:
        payload = {
            "text": notification.title,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": notification.message}
                }
            ]
        }
        response = requests.post(self.webhook_url, json=payload)
        return response.ok

# 등록
register_notification_adapter(
    SlackNotificationAdapter(webhook_url="https://hooks.slack.com/...")
)
```

### 5.4 Severity 기반 라우팅

```python
NOTIFICATION_ROUTING = {
    NotificationSeverity.CRITICAL: [
        NotificationChannel.PAGERDUTY,
        NotificationChannel.SLACK,
        NotificationChannel.SMS,
    ],
    NotificationSeverity.HIGH: [
        NotificationChannel.SLACK,
        NotificationChannel.EMAIL,
    ],
    NotificationSeverity.MEDIUM: [
        NotificationChannel.SLACK,
    ],
    NotificationSeverity.LOW: [
        NotificationChannel.EMAIL,
    ],
    NotificationSeverity.INFO: [
        NotificationChannel.FILE,
    ],
}
```

---

## 6. SQLAlchemy 어댑터

**경로**: `selfhealing.adapters.sqlalchemy/`

### 6.1 Models

**경로**: `selfhealing.adapters.sqlalchemy.models`

#### 6.1.1 FailedOperationModel

```python
class FailedOperationModel(Base):
    __tablename__ = "selfhealing_failed_operations"

    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    domain = Column(String(50), nullable=False, index=True)
    failure_type = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    entity_type = Column(String(100))
    entity_id = Column(String(255))
    snapshot_data = Column(JSONB)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
```

#### 6.1.2 CircuitBreakerStateModel

```python
class CircuitBreakerStateModel(Base):
    __tablename__ = "selfhealing_circuit_breaker_states"

    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False)
    state = Column(String(20), nullable=False, default="closed")
    failure_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    last_failure_at = Column(DateTime)
    last_success_at = Column(DateTime)
    opened_at = Column(DateTime)
    metadata = Column(JSONB)
```

#### 6.1.3 SecurityIncidentModel

```python
class SecurityIncidentModel(Base):
    __tablename__ = "selfhealing_security_incidents"

    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    incident_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    source_ip = Column(INET)
    user_id = Column(String(255))
    description = Column(Text)
    details = Column(JSONB)
    status = Column(String(20), default="open")
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)
```

### 6.2 Repositories

**경로**: `selfhealing.adapters.sqlalchemy/`

| 파일 | 구현 인터페이스 |
|------|----------------|
| `failed_operation.py` | `RepositoryInterface[FailedOperation]` |
| `circuit_breaker.py` | `RepositoryInterface[CircuitBreakerState]` |
| `security_incident.py` | `RepositoryInterface[SecurityIncident]` |

### 6.3 Base

**경로**: `selfhealing.adapters.sqlalchemy.base`

| 컴포넌트 | 용도 |
|---------|------|
| `BaseRepository` | SQLAlchemy 저장소 기본 클래스 |
| `create_session_factory` | 세션 팩토리 생성 함수 |

```python
from selfhealing.adapters.sqlalchemy.base import create_session_factory

session_factory = create_session_factory(
    connection_string="postgresql://user:pass@localhost/db",
    pool_size=10,
    max_overflow=20,
)

with session_factory() as session:
    ...
```

---

## 7. Django 어댑터

**경로**: `selfhealing.adapters.django/`

### 7.1 Models

**경로**: `selfhealing.adapters.django.models`

#### 7.1.1 FailedOperation

```python
class FailedOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    domain = models.CharField(max_length=50, db_index=True)
    failure_type = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default="pending")

    # Generic Foreign Key
    entity_type = models.CharField(max_length=100, blank=True)
    entity_id = models.CharField(max_length=255, blank=True)

    # Snapshot
    snapshot_data = models.JSONField(default=dict)

    # Retry info
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)

    # Error details
    error_message = models.TextField(blank=True)
    error_traceback = models.TextField(blank=True)

    # Resolution
    resolution_type = models.CharField(max_length=50, blank=True)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["domain", "status"]),
            models.Index(fields=["created_at"]),
        ]
```

#### 7.1.2 CircuitBreakerState

```python
class CircuitBreakerState(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=100, unique=True)
    state = models.CharField(max_length=20, default="closed")
    failure_count = models.IntegerField(default=0)
    success_count = models.IntegerField(default=0)
    last_failure_at = models.DateTimeField(null=True)
    last_success_at = models.DateTimeField(null=True)
    opened_at = models.DateTimeField(null=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

#### 7.1.3 SecurityIncident

```python
class SecurityIncident(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    incident_type = models.CharField(max_length=50)
    severity = models.CharField(max_length=20)
    source_ip = models.GenericIPAddressField(null=True)
    user_id = models.CharField(max_length=255, blank=True)
    description = models.TextField()
    details = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="open")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True)
```

### 7.2 Repositories

**경로**: `selfhealing.adapters.django/`

#### 7.2.1 FailedOperationRepository

```python
class DjangoFailedOperationRepository(RepositoryInterface):
    def save(self, entity: FailedOperationDTO) -> FailedOperationDTO:
        obj, created = FailedOperation.objects.update_or_create(
            id=entity.id,
            defaults={
                'domain': entity.domain,
                'failure_type': entity.failure_type,
                'status': entity.status,
                # ...
            }
        )
        return self._to_dto(obj)

    def find_by_id(self, id: str) -> Optional[FailedOperationDTO]:
        try:
            obj = FailedOperation.objects.get(id=id)
            return self._to_dto(obj)
        except FailedOperation.DoesNotExist:
            return None

    def find_pending(self, domain: str = None) -> List[FailedOperationDTO]:
        qs = FailedOperation.objects.filter(status="pending")
        if domain:
            qs = qs.filter(domain=domain)
        return [self._to_dto(obj) for obj in qs]
```

#### 7.2.2 CircuitBreakerRepository

```python
class DjangoCircuitBreakerRepository(RepositoryInterface):
    def get_state(self, name: str) -> Optional[CircuitBreakerStateDTO]:
        try:
            obj = CircuitBreakerState.objects.get(name=name)
            return self._to_dto(obj)
        except CircuitBreakerState.DoesNotExist:
            return None

    def update_state(
        self, name: str, state: str, failure_count: int = None
    ) -> CircuitBreakerStateDTO:
        obj, _ = CircuitBreakerState.objects.get_or_create(name=name)
        obj.state = state
        if failure_count is not None:
            obj.failure_count = failure_count
        if state == "open":
            obj.opened_at = timezone.now()
        obj.save()
        return self._to_dto(obj)
```

### 7.3 Config Provider

**경로**: `selfhealing.adapters.django.config_provider`

```python
class DjangoConfigProvider(ConfigInterface):
    PREFIX = "SELFHEALING_"

    def get(self, key: str, default: Any = None) -> Any:
        # key: "circuit_breaker.timeout"
        # → SELFHEALING_CIRCUIT_BREAKER_TIMEOUT
        setting_name = self.PREFIX + key.upper().replace(".", "_")
        return getattr(settings, setting_name, default)

    def set(self, key: str, value: Any) -> bool:
        # 동적 설정은 제한적
        setting_name = self.PREFIX + key.upper().replace(".", "_")
        setattr(settings, setting_name, value)
        return True

    def reload(self) -> bool:
        # Django 설정은 일반적으로 리로드 불가
        return False
```

---

## 📎 관련 문서

- [00_INDEX.md](00_INDEX.md) - 문서 인덱스
- [01_MIDDLEWARE_GATEWAY.md](01_MIDDLEWARE_GATEWAY.md) - 미들웨어 게이트웨이
- [02_LOGIC_ENGINE.md](02_LOGIC_ENGINE.md) - 비즈니스 로직 엔진
- [04_AUTONOMOUS_OPS.md](04_AUTONOMOUS_OPS.md) - 자율 운영 시스템
