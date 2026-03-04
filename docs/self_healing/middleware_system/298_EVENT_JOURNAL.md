# 298. Event Journal — Self-Healing 이벤트 저널링 저장소

> **Status**: Proposed
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/interfaces/event_journal.py` — EventJournalRepository ABC
> - `packages/selfhealing-python/src/selfhealing/services/event_journal/` — 서비스 디렉터리
> - `packages/selfhealing-python/src/selfhealing/adapters/memory/event_journal.py` — InMemory 어댑터
> - `packages/selfhealing-python/src/selfhealing/adapters/redis/event_journal.py` — Redis 어댑터
> - `packages/selfhealing-python/src/selfhealing/settings/event_journal.py` — Settings
> **References**:
> - [299_CONFIG_SHADOW_EVALUATOR.md](299_CONFIG_SHADOW_EVALUATOR.md) — 본 저장소의 소비자
> - `services/healing_events_store.py` — 기존 Redis 이벤트 저장 (참고용, 대체 대상 아님)
> - `services/event_bus/bus/__init__.py` — EventType enum, EventBus 구독 패턴
> - `audit/wal/` — append-only 저장 패턴 참고

---

## 1. 목적

Config Shadow Evaluator(299)가 "설정 A vs 설정 B"를 시뮬레이션하려면,
과거에 발생한 Self-Healing 이벤트를 **순서가 보장된 형태로** 조회할 수 있어야 한다.

현재 이벤트 데이터가 분산되어 있어 시뮬레이션에 사용할 수 없다:

| 기존 저장소 | 위치 | 시뮬레이션 적합성 |
|-------------|------|-------------------|
| healing_events_store | `services/healing_events_store.py:34-36` | ❌ TTL 7일, LPUSH 역순, 필드 비정규화 |
| Audit WAL | `audit/wal/` | ❌ 감사 보장용 임시 버퍼, 시뮬레이션 쿼리 불가 |
| EventBus | `services/event_bus/bus/__init__.py` | ❌ In-memory pub/sub, 영속성 없음 |

EventJournal은 Self-Healing **결정 이벤트만** append-only로 기록하여
시간 범위 쿼리와 이벤트 타입 필터링을 제공한다.

---

## 2. 기존 코드 근거

### 2.1 healing_events_store.py — 기존 이벤트 저장 패턴

```python
# services/healing_events_store.py:34-36
EVENTS_KEY_PREFIX = "selfhealing:events"
EVENTS_TTL_DAYS = 7
EVENTS_TTL_SECONDS = 604800
```

```python
# services/healing_events_store.py:103-130
def add_healing_event_redis(event: dict[str, Any]) -> bool:
    if "recorded_at" not in event:
        event["recorded_at"] = datetime.now(dt_timezone.utc).isoformat()
    redis_client.lpush(key, event_json)
    if redis_client.ttl(key) < 0:
        redis_client.expire(key, EVENTS_TTL_SECONDS)
```

**한계**: `dict[str, Any]` 비정규화 데이터, 시퀀스 번호 없음, 이벤트 타입 필터링 불가.

### 2.2 EventBus — 구독 패턴

```python
# services/event_bus/bus/__init__.py:59-77
class EventType(str, Enum):
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"
    CIRCUIT_BREAKER_HALF_OPENED = "circuit_breaker_half_opened"
    ERROR_BUDGET_CRITICAL = "error_budget_critical"
    ERROR_BUDGET_WARNING = "error_budget_warning"
    ERROR_BUDGET_RECOVERED = "error_budget_recovered"
    EMERGENCY_LEVEL_CHANGED = "emergency_level_changed"
    ...
```

EventBus는 `bus.subscribe(EventType, handler)` 패턴으로 이벤트를 수신한다.
EventJournal은 이 구독 메커니즘을 통해 이벤트를 캡처한다.

### 2.3 WAL — append-only 저장 패턴

```python
# audit/wal/__init__.py:76-82
MAGIC = b"AWAL"
VERSION = 1
HEADER_SIZE = 8
RECORD_HEADER_SIZE = 12
RECORD_MAGIC = b"\xAB\xCD"
```

WAL은 CRC32 체크섬과 시퀀스 카운터를 사용한 append-only 저장을 구현한다.
EventJournal은 이 **순서 보장 원칙**을 차용하되, WAL 자체는 사용하지 않는다
(WAL은 감사 로그 유실 방지용 임시 버퍼이고, EventJournal은 시뮬레이션용 영구 저장소).

### 2.4 FailedOperationRepository — Repository 인터페이스 패턴

```python
# interfaces/repositories.py:284-292
class FailedOperationRepository(ABC):
    """
    Abstract repository for FailedOperation (DLQ) data access.

    Implementations:
    - DjangoFailedOperationRepository: Uses Django ORM
    - (Future) SQLAlchemyFailedOperationRepository: Uses SQLAlchemy
    """
```

이 패턴을 따라 `EventJournalRepository` ABC를 정의한다.

### 2.5 CB 서비스 — 이벤트 발행 시 캡처할 데이터

CB가 OPEN될 때 수집하는 스냅샷 (`services/circuit_breaker/service.py:633-713`):

```python
snapshot = {
    "service_name": service_name,
    "failure_count": state.failure_count,
    "failure_rate_percent": rate,
    "threshold_config": {
        "failure_threshold": config.failure_threshold,
        "minimum_calls": config.minimum_calls,
    },
}
```

이 스냅샷 데이터가 JournalEntry의 `context` 필드에 저장된다.

---

## 3. 저널 대상 이벤트 선택

**선택 기준**: Self-Healing **결정이 발생한** 이벤트만 저널링한다. 모든 트래픽이 아니라
시스템 상태가 변경되는 시점의 이벤트만 기록하여 저장량을 최소화한다.

| EventType | 저널 대상 | 이유 |
|-----------|-----------|------|
| `CIRCUIT_BREAKER_OPENED` | ✅ | CB threshold 도달 → 시뮬레이션 핵심 입력 |
| `CIRCUIT_BREAKER_CLOSED` | ✅ | CB 복구 → 설정 변경 효과 측정 |
| `CIRCUIT_BREAKER_HALF_OPENED` | ✅ | recovery_timeout 경과 → 타이밍 시뮬레이션 |
| `ERROR_BUDGET_CRITICAL` | ✅ | 예산 임계치 도달 → 예산 소모율 시뮬레이션 |
| `ERROR_BUDGET_WARNING` | ✅ | 예산 경고 → 소모 추세 분석 |
| `ERROR_BUDGET_RECOVERED` | ✅ | 예산 회복 → 윈도우 경계 효과 |
| `EMERGENCY_LEVEL_CHANGED` | ✅ | 비상 모드 전환 → 거버넌스 영향 분석 |
| `KILL_SWITCH_ACTIVATED` | ❌ | 수동 조작 — 설정 시뮬레이션 무관 |
| `CHAOS_EXPERIMENT_*` | ❌ | 인위적 주입 — 시뮬레이션 오염 방지 |
| `DLQ_REPLAY_*` | ❌ | 복구 작업 — 원인 이벤트만 필요 |
| `SAGA_*` | ❌ | 비즈니스 로직 — Self-Healing 설정 무관 |

총 7개 이벤트 타입을 저널링 대상으로 한다.

---

## 4. 데이터 모델

### 4.1 JournalEntry

```python
@dataclass(frozen=True)
class JournalEntry:
    """이벤트 저널 엔트리. 불변(frozen) 데이터."""

    sequence: int                    # 단조 증가 시퀀스 (순서 보장)
    event_type: str                  # EventType.value (예: "circuit_breaker_opened")
    source: str                      # 발행 서비스 (예: "circuit_breaker_service")
    timestamp: datetime              # UTC 이벤트 발생 시각
    service_name: str                # 대상 서비스명 (예: "payment_gateway")
    context: dict[str, Any]          # 이벤트별 상세 데이터 (스냅샷)

    # 선택 필드
    region: str = ""                 # 멀티리전 식별자
    tier_id: str = ""                # 서비스 티어 (critical/standard/non_essential)
```

**설계 결정 — `frozen=True` 선택 이유**:
WAL의 불변 레코드 패턴(`audit/wal/_models.py`)을 따른다.
저널 엔트리는 한번 기록되면 수정되지 않는 append-only 데이터이므로
frozen dataclass가 의미적으로 정확하다.

**설계 결정 — `event_type`을 `str`로 선택한 이유**:
`EventType` enum 직접 참조 대신 `str`을 사용한다.
`FailedOperationData` (`interfaces/repositories.py:103`)의 `failure_type: str` 패턴과 동일하게,
enum 의존성 없이 직렬화/역직렬화가 자유로운 형태를 유지한다.
향후 EventType에 새 이벤트가 추가되어도 저널 스키마 변경이 불필요하다.

### 4.2 JournalQueryFilter

```python
@dataclass
class JournalQueryFilter:
    """저널 조회 필터."""

    event_types: list[str] | None = None     # 이벤트 타입 필터
    service_name: str | None = None          # 대상 서비스 필터
    start_time: datetime | None = None       # 시간 범위 시작 (inclusive)
    end_time: datetime | None = None         # 시간 범위 끝 (exclusive)
    region: str | None = None                # 리전 필터
    limit: int = 1000                        # 최대 반환 건수
```

### 4.3 JournalQueryResult

```python
@dataclass(frozen=True)
class JournalQueryResult:
    """저널 조회 결과. 절삭(truncation) 여부를 포함한다."""

    entries: list[JournalEntry]
    truncated: bool              # limit에 도달하여 절삭되었는가
    total_count: int | None      # 절삭 전 전체 건수 (None이면 미계산)
```

**설계 결정 — `JournalQueryResult` 래퍼 도입 이유**:
`query()`가 `list[JournalEntry]`를 직접 반환하면, `limit`에 의해 데이터가 절삭되었는지
호출자가 알 수 없다. 시뮬레이터(Config Shadow Evaluator)는 절삭된 데이터로 시뮬레이션하면
결과의 정확도가 낮아지므로, `truncated` 플래그를 통해 "이 시뮬레이션은 데이터 절삭으로
인해 정확도가 낮을 수 있다"는 경고를 리포트에 포함할 수 있어야 한다.
예외/경고(`Warning`) 대신 반환값에 플래그를 포함하는 방식을 선택한 이유는,
기존 코드베이스에 Warning을 던지는 패턴이 없고, 에러 격리 원칙과 일관되기 때문이다.

---

## 5. 인터페이스 정의

### 5.1 EventJournalRepository

```python
class EventJournalRepository(ABC):
    """
    Self-Healing 이벤트 저널 저장소 인터페이스.

    append-only 저장소. 기록된 엔트리는 수정/삭제 불가.
    시퀀스 번호는 단조 증가하여 순서를 보장한다.
    Gap이 존재할 수 있으며(예: 1, 2, 4, 5), 소비자는 연속성을 가정하지 않는다.

    Implementations:
    - InMemoryEventJournalRepository: 테스트 및 단일 프로세스
    - RedisEventJournalRepository: 멀티 워커 환경
    """

    @abstractmethod
    def append(self, entry: JournalEntry) -> int:
        """
        이벤트를 저널에 추가한다.

        Args:
            entry: 저널 엔트리 (sequence 필드는 구현체가 할당)

        Returns:
            할당된 시퀀스 번호
        """
        ...

    @abstractmethod
    def query(self, filter: JournalQueryFilter) -> JournalQueryResult:
        """
        필터 조건에 맞는 엔트리를 시퀀스 순서(오름차순)로 반환한다.

        Args:
            filter: 조회 조건

        Returns:
            JournalQueryResult — entries(시퀀스 오름차순), truncated 여부, total_count
        """
        ...

    @abstractmethod
    def get_sequence_range(
        self,
        start_sequence: int,
        end_sequence: int,
    ) -> list[JournalEntry]:
        """
        시퀀스 범위로 엔트리를 조회한다.

        시뮬레이션 시 정확한 범위 재생에 사용.

        Args:
            start_sequence: 시작 시퀀스 (inclusive)
            end_sequence: 끝 시퀀스 (exclusive)

        Returns:
            시퀀스 오름차순 정렬된 엔트리 리스트
        """
        ...

    @abstractmethod
    def get_latest_sequence(self) -> int:
        """현재 최신 시퀀스 번호를 반환한다. 비어있으면 0."""
        ...

    @abstractmethod
    def count(self, filter: JournalQueryFilter) -> int:
        """필터 조건에 맞는 엔트리 수를 반환한다."""
        ...
```

**설계 결정 — delete/update 메서드 미포함 이유**:
append-only 원칙. `FailedOperationRepository`는 `update_status()`, `mark_as_resolved()` 등
상태 변경 메서드가 있지만, EventJournal은 불변 이벤트 로그이므로 쓰기는 `append()`만 제공한다.

### 5.2 EventJournalLifecycle (확장용)

데이터 수명주기 관리는 `EventJournalRepository`(읽기/쓰기 경로)와 분리하여
별도 인터페이스로 정의한다. ISP(Interface Segregation Principle) 준수.
기존 `AnchorColdStorage` (`audit/integrity/cold_storage.py:244-538`)가
메인 audit 인터페이스와 별도 클래스로 존재하는 선례를 따른다.

```python
class EventJournalLifecycle(ABC):
    """
    저널 데이터 수명주기 관리. 운영용 별도 인터페이스.

    append-only 원칙의 EventJournalRepository와 분리하여,
    아카이브/퍼지 책임을 독립적으로 관리한다.

    MVP에서는 구현하지 않으며, Tiered Storage 도입 시 활성화한다.
    """

    @abstractmethod
    def archive_older_than(self, cutoff: datetime) -> int:
        """cutoff 이전 엔트리를 Cold Storage로 이동. 이동된 건수 반환."""
        ...

    @abstractmethod
    def purge_archived(self, before: datetime) -> int:
        """아카이브 완료된 엔트리 중 before 이전 데이터를 삭제. 삭제된 건수 반환."""
        ...
```

**설계 결정 — MVP에서 미구현, 스케치만 포함하는 이유**:
현재 Redis 단일 백엔드(TTL 30일)로 충분하다. 다만 향후 Tiered Storage 도입 시
`AnchorColdStorage`의 Hot(Redis) → Cold(GZIP JSONL) 패턴을 그대로 차용하여
`TieredEventJournalRepository`가 `EventJournalRepository`와 `EventJournalLifecycle`을
동시에 구현하는 형태로 확장할 수 있다.

**설계 결정 — `get_sequence_range()` 추가 이유**:
시뮬레이션 시 "이 rollout이 생성된 시점부터 현재까지의 이벤트"를 정확히 재생해야 한다.
시간 기반 쿼리(`start_time`/`end_time`)는 동일 시각에 여러 이벤트가 발생할 수 있어
시퀀스 기반 범위 쿼리가 더 정확하다.

---

## 6. 어댑터 구현

### 6.1 InMemoryEventJournalRepository

기존 `adapters/memory/circuit_breaker.py:48-492` 패턴을 따른다:

```python
class InMemoryEventJournalRepository(EventJournalRepository):
    """스레드 안전 인메모리 구현. 테스트 및 단일 프로세스용."""

    def __init__(self, max_entries: int = 10000):
        self._entries: list[JournalEntry] = []
        self._lock = threading.RLock()       # CB 어댑터와 동일 패턴
        self._next_sequence = 1
        self._max_entries = max_entries

    def append(self, entry: JournalEntry) -> int:
        with self._lock:
            seq = self._next_sequence
            self._next_sequence += 1
            # frozen dataclass이므로 새 인스턴스 생성
            stored = JournalEntry(
                sequence=seq,
                event_type=entry.event_type,
                source=entry.source,
                timestamp=entry.timestamp,
                service_name=entry.service_name,
                context=entry.context,
                region=entry.region,
                tier_id=entry.tier_id,
            )
            self._entries.append(stored)
            # 최대 용량 초과 시 가장 오래된 엔트리 제거
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries:]
            return seq
```

**핵심 패턴**: `threading.RLock()` 사용, frozen dataclass 재생성으로 불변성 보장.
이는 `InMemoryCircuitBreakerStateRepository` (`adapters/memory/circuit_breaker.py:48`)의
`update_state()` 패턴과 동일하다.

### 6.2 RedisEventJournalRepository

기존 `healing_events_store.py`의 Redis LPUSH 패턴을 개선한다:

```python
class RedisEventJournalRepository(EventJournalRepository):
    """Redis Sorted Set 기반 구현. 멀티 워커 환경용."""

    KEY_PREFIX = "selfhealing:journal"
    SEQUENCE_KEY = "selfhealing:journal:sequence"

    def append(self, entry: JournalEntry) -> int:
        # Redis INCR로 원자적 시퀀스 할당
        seq = self._redis.incr(self.SEQUENCE_KEY)

        # Sorted Set: score=sequence, member=JSON
        # INCR 성공 후 ZADD 실패 시 시퀀스 Gap이 발생할 수 있다.
        # Gap은 허용되며, 실패 시 로그를 남겨 운영 모니터링에 활용한다.
        key = self._get_key(entry.timestamp)
        data = self._serialize(entry, seq)
        try:
            self._redis.zadd(key, {data: seq})
        except Exception as e:
            logger.warning("journal_zadd_failed", sequence=seq, error=str(e))
            raise

        # TTL 설정
        if self._redis.ttl(key) < 0:
            self._redis.expire(key, self._ttl_seconds)

        return seq
```

**설계 결정 — Sorted Set 선택 이유**:
기존 `healing_events_store.py`는 List (`LPUSH`/`LRANGE`)를 사용하지만,
시뮬레이션에는 **시퀀스 범위 쿼리**가 필요하다.
Redis Sorted Set의 `ZRANGEBYSCORE`로 시퀀스 범위를 O(log N + M)에 조회할 수 있다.

**설계 결정 — 월별 파티셔닝 키 구조**:
단일 Sorted Set에 30일치 데이터를 모두 담는 대신, 월별로 키를 분리한다:

```
selfhealing:journal:2026-01    # 1월 데이터
selfhealing:journal:2026-02    # 2월 데이터
```

`_get_key(timestamp)` 메서드가 `timestamp.strftime("%Y-%m")`을 기반으로 파티션 키를 생성한다.
이렇게 하면 시간 범위 쿼리 시 관련 월의 키만 조회하여 스캔 범위를 자연스럽게 줄인다.

**월 경계(Cross-partition) 쿼리 처리**:
시뮬레이션 시간 범위(`start_time` ~ `end_time`)가 월 경계를 넘어갈 수 있다
(예: 1월 30일 ~ 2월 3일). `query()` 내부에서 다음과 같이 처리한다:

```python
def _resolve_partition_keys(self, start_time: datetime, end_time: datetime) -> list[str]:
    """시간 범위에 걸치는 모든 월별 파티션 키를 반환한다."""
    keys = []
    current = start_time.replace(day=1)
    while current < end_time:
        keys.append(f"{self.KEY_PREFIX}:{current.strftime('%Y-%m')}")
        # 다음 월 1일로 이동
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return keys
```

각 파티션 키에 대해 `ZRANGEBYSCORE`를 호출하고, 결과를 시퀀스 순으로 merge sort하여
전체 결과에 `limit`를 적용한다.

**설계 결정 — TTL 적용**:
기존 `healing_events_store.py:35-36`의 7일 TTL 패턴을 따르되,
Settings에서 설정 가능하게 한다 (기본 30일).
시뮬레이션에는 최소 2주 이상의 이벤트가 필요하므로 7일보다 길게 설정한다.

---

## 7. EventBus 구독을 통한 자동 캡처

### 7.1 JournalSubscriber

EventBus 구독 패턴 (`services/event_bus/bus/__init__.py:20-28`)을 활용한다:

```python
# services/event_journal/subscriber.py

JOURNALED_EVENT_TYPES: frozenset[EventType] = frozenset({
    EventType.CIRCUIT_BREAKER_OPENED,
    EventType.CIRCUIT_BREAKER_CLOSED,
    EventType.CIRCUIT_BREAKER_HALF_OPENED,
    EventType.ERROR_BUDGET_CRITICAL,
    EventType.ERROR_BUDGET_WARNING,
    EventType.ERROR_BUDGET_RECOVERED,
    EventType.EMERGENCY_LEVEL_CHANGED,
})


class _JournalCircuitBreaker:
    """
    저널링 전용 경량 CB. 외부 의존 없음.

    CircuitBreakerService를 직접 사용하면 순환 의존이 발생한다:
    CB → EventBus → JournalSubscriber → CB.
    따라서 자체 완결형 경량 CB로 Redis 장애 시 빠른 fail-fast를 구현한다.
    기존 ring_buffer.py:213-216의 "알림 실패가 메인 로직 방해 금지" 원칙과 동일.
    """

    def __init__(self, failure_threshold: int = 5, recovery_seconds: float = 30):
        self._failures = 0
        self._threshold = failure_threshold
        self._open_until: float = 0
        self._recovery = recovery_seconds

    def is_open(self) -> bool:
        if self._failures < self._threshold:
            return False
        return time.monotonic() < self._open_until

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._open_until = time.monotonic() + self._recovery

    def record_success(self) -> None:
        self._failures = 0


class JournalSubscriber:
    """EventBus에서 이벤트를 수신하여 저널에 기록한다."""

    def __init__(self, repository: EventJournalRepository):
        self._repository = repository
        self._cb = _JournalCircuitBreaker()

    def register(self, bus: EventBus) -> None:
        """대상 이벤트 타입에 대해 구독을 등록한다."""
        for event_type in JOURNALED_EVENT_TYPES:
            bus.subscribe(event_type, self._handle_event)

    def _handle_event(self, event: SelfHealingEvent) -> None:
        """
        이벤트를 JournalEntry로 변환하여 저장한다.

        에러 격리 원칙:
        - 저널링 실패가 Self-Healing 메인 로직을 중단시켜서는 안 된다.
        - 기존 패턴: event_buffer.py:447-456 (WAL 실패 시 삼킴),
          ring_buffer.py:213-216 (알림 실패 시 pass)
        - Redis 장애 지속 시 내부 CB가 열려 빠르게 fail-fast 처리.
        """
        # 내부 CB가 열려있으면 즉시 반환 (Redis 타임아웃 누적 방지)
        if self._cb.is_open():
            return

        try:
            entry = self._build_entry(event)
            self._repository.append(entry)
            self._cb.record_success()
        except (TypeError, ValueError) as e:
            # 직렬화 에러 — context에 비직렬화 객체 포함
            logger.warning("journal_serialization_failed",
                           event_type=event.event_type.value, error=str(e))
        except Exception as e:
            # Redis 장애 등 인프라 에러
            self._cb.record_failure()
            logger.warning("journal_append_failed",
                           event_type=event.event_type.value, error=str(e))

    def _build_entry(self, event: SelfHealingEvent) -> JournalEntry:
        """이벤트를 JournalEntry로 변환한다. 방어적 직렬화 적용."""
        # context 내 datetime, 커스텀 객체 등을 안전하게 변환
        safe_context = json.loads(json.dumps(event.data, default=str))
        return JournalEntry(
            sequence=0,  # repository가 할당
            event_type=event.event_type.value,
            source=event.source,
            timestamp=event.timestamp,
            service_name=event.data.get("service_name", ""),
            context=safe_context,
            region=event.data.get("region", ""),
            tier_id=event.data.get("tier_id", ""),
        )
```

**설계 결정 — 에러 격리(Swallow) 선택 이유**:
EventJournal은 사후 분석(post-hoc analysis) 도구이며 Self-Healing 핵심 경로가 아니다.
저널링 실패 때문에 CB 전환이 지연되면 Self-Healing의 존재 이유에 반한다.
기존 코드베이스 전반에서 관측성 레이어의 에러는 삼키는 것이 표준 패턴이다:
`event_buffer.py:447-456`, `ring_buffer.py:213-216`, `wal/_writer.py:68-76`.

**설계 결정 — 내부 경량 CB(`_JournalCircuitBreaker`) 도입 이유**:
에러를 삼키더라도 Redis 타임아웃(기본 2초)이 이벤트마다 발생하면 지연이 누적된다.
내부 CB가 5회 연속 실패 후 30초간 OPEN 상태를 유지하여 즉시 반환함으로써
메인 로직의 레이턴시 스파이크를 원천 차단한다.
`CircuitBreakerService`를 직접 사용하지 않는 이유는 순환 의존 방지이다.

**설계 결정 — 방어적 직렬화(`json.dumps(default=str)`) 선택 이유**:
EventBus에서 넘어오는 `context` (`dict[str, Any]`) 내부에 `datetime` 객체나
사용자 정의 클래스가 포함될 수 있다. `default=str` 폴백으로
`datetime` → ISO string, 커스텀 객체 → `str(obj)` 변환을 보장한다.

### 7.2 초기화

```python
# services/event_journal/__init__.py

_journal_subscriber: JournalSubscriber | None = None

def init_event_journal(bus: EventBus | None = None) -> JournalSubscriber:
    """EventJournal 구독자를 초기화한다. 앱 시작 시 1회 호출."""
    global _journal_subscriber
    if _journal_subscriber is not None:
        return _journal_subscriber

    repository = ProviderRegistry.get_event_journal_repo()
    _journal_subscriber = JournalSubscriber(repository=repository)

    if bus is None:
        bus = get_event_bus()
    _journal_subscriber.register(bus)

    return _journal_subscriber
```

---

## 8. Settings

기존 `settings/replay_automation.py:14-33` 패턴을 따른다:

```python
# settings/event_journal.py

class EventJournalSettings(BaseSettings):
    """
    Event Journal 설정.

    Environment variables:
        SELFHEALING_JOURNAL_ENABLED=true
        SELFHEALING_JOURNAL_TTL_DAYS=30
        SELFHEALING_JOURNAL_MAX_ENTRIES_MEMORY=10000
        ...
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_JOURNAL_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(
        default=True,
        description="EventJournal 활성화 여부",
    )
    ttl_days: int = Field(
        default=30,
        ge=7,
        le=365,
        description="Redis 저장소 TTL (일)",
    )
    max_entries_memory: int = Field(
        default=10000,
        ge=100,
        le=1000000,
        description="InMemory 어댑터 최대 엔트리 수",
    )
    max_query_limit: int = Field(
        default=10000,
        ge=100,
        le=100000,
        description="query() 최대 반환 건수 상한. JournalQueryFilter.limit이 이 값을 초과하면 클램프된다.",
    )
    backend: str = Field(
        default="memory",
        description="저장소 백엔드 (memory, redis)",
    )
```

**설계 결정 — `ttl_days` 기본값 30일 선택 이유**:
기존 `healing_events_store.py`의 7일은 대시보드 표시용으로 충분하지만,
시뮬레이션은 "지난 2주간 이 서비스에서 CB가 몇 번 열렸는가"를 분석해야 한다.
30일은 월간 SLO 윈도우(일반적 30일)와 일치시킨 값이다.

---

## 9. ProviderRegistry 연동

기존 `factory.py:82-107`의 register/get 패턴을 따른다:

```python
# factory.py에 추가

class ProviderRegistry:
    _event_journal_repos: dict[str, type] = {}

    @classmethod
    def register_event_journal_repo(cls, name: str, repo_class: type) -> None:
        cls._event_journal_repos[name] = repo_class

    @classmethod
    def get_event_journal_repo(
        cls,
        name: str | None = None,
        singleton: bool = True,
    ) -> EventJournalRepository:
        name = name or _default_event_journal
        ...
```

auto-registration (`factory.py:738-861` 패턴):

```python
def _auto_register_adapters():
    ...
    # Event Journal
    cls.register_event_journal_repo("memory", InMemoryEventJournalRepository)
    try:
        from selfhealing.adapters.redis.event_journal import RedisEventJournalRepository
        cls.register_event_journal_repo("redis", RedisEventJournalRepository)
    except ImportError:
        pass
```

---

## 10. 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── interfaces/
│   └── event_journal.py              # EventJournalRepository ABC, EventJournalLifecycle ABC, JournalEntry, JournalQueryFilter, JournalQueryResult
├── services/
│   └── event_journal/
│       ├── __init__.py               # init_event_journal(), get_event_journal()
│       └── subscriber.py             # JournalSubscriber, _JournalCircuitBreaker, JOURNALED_EVENT_TYPES
├── adapters/
│   ├── memory/
│   │   └── event_journal.py          # InMemoryEventJournalRepository
│   └── redis/
│       └── event_journal.py          # RedisEventJournalRepository
└── settings/
    └── event_journal.py              # EventJournalSettings
```

---

## 11. healing_events_store.py와의 관계

EventJournal은 기존 `healing_events_store.py`를 **대체하지 않는다**.

| 속성 | healing_events_store | EventJournal |
|------|---------------------|--------------|
| 목적 | 대시보드 표시, 디버깅 | 시뮬레이션 데이터 소스 |
| 대상 | 모든 healing 이벤트 | 결정 이벤트 7종만 |
| TTL | 7일 | 30일 (설정 가능) |
| 순서 보장 | ❌ (LPUSH 역순) | ✅ (시퀀스 번호) |
| 쿼리 | LRANGE (인덱스) | ZRANGEBYSCORE (시퀀스/시간) |
| 스키마 | `dict[str, Any]` 비정규화 | `JournalEntry` 정규화 |

두 저장소는 동일한 EventBus 이벤트를 각각 구독하여 독립적으로 저장한다.
