# 193. DLQ + Replay - AdaptiveThrottle 거부된 요청 재처리 연동 구현

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**:
> - `selfhealing/services/dlq/store_operations.py` (Line 1-290)
> - `selfhealing/services/dlq/replay_operations.py` (Line 1-240)
> - `selfhealing/services/dlq/base.py` (Line 1-115)
> - `selfhealing/services/throttle/adaptive.py` (Line 1548-1577 Recovery)
> **우선순위**: 🟡 P2

---

## 1. 개요

본 문서는 `AdaptiveThrottle`에 의해 거부된 요청을 `DLQService`에 저장하고, Throttle Recovery 시 자동 Replay하는 구현을 정의합니다.

### 1.1 연동 목적

| 목적 | 설명 |
|------|------|
| 거부 요청 보존 | Throttle에 의해 거부된 요청 손실 방지 |
| 자동 복구 | Throttle Recovery 시 자동 Replay |
| 순차 재처리 | DLQ 저장 순서대로 공정한 재처리 |
| Backpressure 해소 | 축적된 요청의 점진적 해소 |

### 1.2 현재 DLQ 구현

**코드 위치**: [dlq/base.py](../../packages/selfhealing-python/src/selfhealing/services/dlq/base.py)

```python
class DLQService(StoreOperationsMixin, ReplayOperationsMixin, QueryOperationsMixin):
    """
    Dead Letter Queue Service.

    Mixins:
    - StoreOperationsMixin: store(), store_batch() - DLQ 저장
    - ReplayOperationsMixin: replay(), replay_all() - 재처리
    - QueryOperationsMixin: query(), count() - 조회
    """

    def __init__(
        self,
        repository: DLQRepository,
        event_bus: EventBus | None = None,
    ):
        self._repository = repository
        self._event_bus = event_bus
```

### 1.3 현재 DLQ 저장

**코드 위치**: [dlq/store_operations.py](../../packages/selfhealing-python/src/selfhealing/services/dlq/store_operations.py)

```python
class StoreOperationsMixin:

    def store(
        self,
        entry: DLQEntry,
        reason: str,
        source: str = "unknown",
    ) -> DLQStoreResult:
        """
        Store a failed operation in the DLQ.

        Args:
            entry: DLQ entry with operation details
            reason: Why the operation failed (e.g., "max_retries_exceeded")
            source: Component that initiated the store

        Returns:
            DLQStoreResult with store status
        """
        entry.reason = reason
        entry.source = source
        entry.stored_at = datetime.utcnow()

        stored = self._repository.save(entry)

        # Emit event
        if self._event_bus:
            self._event_bus.emit(EventType.DLQ_ENTRY_STORED, {
                "entry_id": entry.id,
                "reason": reason,
                "domain": entry.domain,
            })

        return DLQStoreResult(
            success=stored,
            entry_id=entry.id,
        )
```

### 1.4 현재 Replay

**코드 위치**: [dlq/replay_operations.py](../../packages/selfhealing-python/src/selfhealing/services/dlq/replay_operations.py)

```python
class ReplayOperationsMixin:

    def replay(
        self,
        entry_id: str,
        executor: Callable[[DLQEntry], bool] | None = None,
    ) -> DLQReplayResult:
        """
        Replay a single DLQ entry.

        Args:
            entry_id: ID of the entry to replay
            executor: Optional custom executor for replay

        Returns:
            DLQReplayResult with replay status
        """
        entry = self._repository.get(entry_id)
        if entry is None:
            return DLQReplayResult(success=False, error="Entry not found")

        # Check if entry is eligible for replay
        if entry.status == DLQStatus.RESOLVED:
            return DLQReplayResult(success=False, error="Already resolved")

        # Execute replay
        try:
            if executor:
                success = executor(entry)
            else:
                success = self._default_executor(entry)

            if success:
                entry.status = DLQStatus.RESOLVED
                entry.resolved_at = datetime.utcnow()
                self._repository.update(entry)

            return DLQReplayResult(success=success, entry_id=entry_id)
        except Exception as e:
            entry.replay_attempts += 1
            entry.last_replay_error = str(e)
            self._repository.update(entry)
            return DLQReplayResult(success=False, error=str(e))
```

### 1.5 문제점

| 문제 | 설명 |
|------|------|
| Throttle 거부 미저장 | Throttle에 의해 거부된 요청은 DLQ에 저장 안 됨 |
| 수동 Replay 필요 | Recovery 후에도 수동으로 Replay 트리거 필요 |
| 무차별 Replay | Replay 시 Throttle 상태 미확인 |
| 대량 Replay 위험 | 축적된 요청 일괄 Replay 시 Re-throttle 위험 |

---

## 2. 연동 아키텍처

### 2.1 시퀀스 다이어그램

```
┌───────┐   ┌────────────────┐   ┌──────────┐   ┌──────────┐
│Request│   │AdaptiveThrottle│   │DLQService│   │ EventBus │
└───┬───┘   └───────┬────────┘   └─────┬────┘   └─────┬────┘
    │               │                   │              │
    │ ① acquire()   │                   │              │
    │──────────────►│                   │              │
    │               │                   │              │
    │ ② REJECTED    │                   │              │
    │◄──────────────│                   │              │
    │               │ ③ store_throttle_rejection      │
    │               │──────────────────►│              │
    │               │                   │ ④ emit THROTTLE_REJECTION_STORED
    │               │                   │─────────────►│
    │               │                   │              │
    │               │ ⑤ (시간 경과)      │              │
    │               │                   │              │
    │               │ ⑥ emit THROTTLE_LIMIT_RECOVERED │
    │               │─────────────────────────────────►│
    │               │                   │              │
    │               │                   │ ⑦ on_recovery
    │               │                   │◄─────────────│
    │               │                   │              │
    │               │ ⑧ replay_throttle_aware         │
    │◄──────────────────────────────────│              │
```

### 2.2 Throttle 거부 DLQ 저장 흐름

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Throttle 거부 → DLQ 저장 결정 트리                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│                        Throttle.acquire() 호출                          │
│                               │                                         │
│                               ▼                                         │
│                    ┌──────────────────┐                                 │
│                    │  limit > 0 인가?  │                                │
│                    └────────┬─────────┘                                 │
│                      Yes    │      No                                   │
│                      ▼      │      ▼                                    │
│              ┌──────────┐   │   ┌───────────────────┐                   │
│              │허용(ALLOW)│   │   │거부 사유 기록     │                   │
│              └──────────┘   │   │- full_stop       │                   │
│                             │   │- emergency_level │                   │
│                             │   │- limit_exhausted │                   │
│                             │   └────────┬──────────┘                   │
│                             │            ▼                              │
│                             │   ┌────────────────────┐                  │
│                             │   │Throttle 거부 DLQ 저장│                 │
│                             │   │- reason: "throttle_rejected"│         │
│                             │   │- metadata: throttle_state   │         │
│                             │   └────────────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 구현 명세

### 3.1 AdaptiveThrottle 수정 - 거부 시 DLQ 저장

**수정 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)

```python
class AdaptiveThrottle:

    def __init__(self, config: ThrottleConfig, ...):
        # ... 기존 코드 ...

        # DLQ 연동 (선택적)
        self._dlq_service: DLQService | None = None
        self._dlq_enabled = getattr(config, "dlq_on_rejection", True)

        if self._dlq_enabled:
            self._init_dlq_service()

    def _init_dlq_service(self) -> None:
        """DLQ 서비스 초기화 (Fail-Open)."""
        try:
            from selfhealing.services.dlq import get_dlq_service
            self._dlq_service = get_dlq_service()
        except Exception:
            self._dlq_service = None

    def acquire(
        self,
        context: dict[str, Any] | None = None,
        store_rejection: bool = True,  # 신규 파라미터
    ) -> ThrottleDecision:
        """
        Attempt to acquire a permit.

        Args:
            context: Request context for DLQ storage
            store_rejection: Whether to store rejection in DLQ
        """
        with self._lock:
            allowed = self._try_acquire()

            if not allowed:
                reason = self._get_rejection_reason()

                # DLQ 저장
                if store_rejection and self._dlq_service and context:
                    self._store_throttle_rejection_to_dlq(context, reason)

                return ThrottleDecision(
                    allowed=False,
                    wait_time=self._calculate_wait_time(),
                    reason=reason,
                    dlq_stored=store_rejection,
                )

            return ThrottleDecision(allowed=True)

    def _get_rejection_reason(self) -> str:
        """거부 사유 결정."""
        if self._full_stop_active:
            return "full_stop"
        if self._emergency_level >= 3:
            return f"emergency_level_{self._emergency_level}"
        if self._current_limit <= 0:
            return "limit_exhausted"
        return "capacity_exceeded"

    def _store_throttle_rejection_to_dlq(
        self,
        context: dict[str, Any],
        reason: str,
    ) -> None:
        """거부된 요청을 DLQ에 저장."""
        from selfhealing.services.dlq.models import DLQEntry

        entry = DLQEntry(
            id=str(uuid.uuid4()),
            domain=context.get("domain", "throttle_rejection"),
            operation=context.get("operation", "unknown"),
            payload=context.get("payload", {}),
            reason=f"throttle_rejected:{reason}",
            source="AdaptiveThrottle",
            metadata={
                "throttle_state": {
                    "current_limit": self._current_limit,
                    "initial_limit": self.config.initial_limit,
                    "emergency_level": self._emergency_level,
                    "full_stop_active": self._full_stop_active,
                    "rejection_reason": reason,
                },
                "request_id": context.get("request_id"),
                "timestamp": datetime.utcnow().isoformat(),
            },
            priority=self._calculate_rejection_priority(context),
        )

        self._dlq_service.store(
            entry=entry,
            reason="throttle_rejected",
            source="AdaptiveThrottle",
        )

        # Emit event
        if self._event_bus:
            self._event_bus.emit(
                EventType.THROTTLE_REJECTION_STORED,
                {
                    "entry_id": entry.id,
                    "reason": reason,
                    "domain": entry.domain,
                },
            )

    def _calculate_rejection_priority(self, context: dict[str, Any]) -> int:
        """거부 요청 우선순위 계산 (낮을수록 높은 우선순위)."""
        # 기본 우선순위
        priority = 50

        # Critical 요청은 높은 우선순위
        if context.get("critical", False):
            priority = 10

        # 이미 재시도된 요청은 높은 우선순위
        retry_count = context.get("retry_count", 0)
        priority -= min(retry_count * 5, 30)  # 최대 30 감소

        return max(1, priority)
```

### 3.2 Recovery 시 자동 Replay

**추가 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)

```python
class AdaptiveThrottle:

    def __init__(self, ...):
        # ... 기존 코드 ...

        # Auto-replay 설정
        self._auto_replay_on_recovery = getattr(config, "auto_replay_on_recovery", True)
        self._replay_batch_size = getattr(config, "replay_batch_size", 10)
        self._replay_interval_ms = getattr(config, "replay_interval_ms", 100)

        # Recovery 이벤트 구독
        self._subscribe_throttle_recovery_for_replay()

    def _subscribe_throttle_recovery_for_replay(self) -> None:
        """Recovery 이벤트 구독 (자동 Replay 트리거)."""
        if not self._event_bus or not self._auto_replay_on_recovery:
            return

        self._event_bus.subscribe(
            EventType.THROTTLE_LIMIT_RECOVERED,
            self._on_throttle_recovery_trigger_replay,
        )

    def _on_throttle_recovery_trigger_replay(self, event) -> None:
        """Recovery 시 DLQ Replay 트리거."""
        if not self._dlq_service:
            return

        data = event.data if hasattr(event, "data") else event
        recovery_percent = data.get("recovery_percent", 0)

        # 50% 이상 Recovery 시에만 Replay
        if recovery_percent < 50:
            logger.debug(
                f"[AdaptiveThrottle] Recovery {recovery_percent}% too low, skipping DLQ replay"
            )
            return

        # 비동기 Replay 시작
        import threading
        thread = threading.Thread(
            target=self._trigger_throttle_rejection_replay,
            kwargs={"recovery_percent": recovery_percent},
            daemon=True,
        )
        thread.start()

    def _trigger_throttle_rejection_replay(self, recovery_percent: float) -> None:
        """Throttle 거부 요청 Replay 실행."""
        # Replay 대상 조회
        pending_entries = self._dlq_service.query(
            domain="throttle_rejection",
            status=DLQStatus.PENDING,
            limit=self._replay_batch_size,
            order_by="priority",  # 우선순위 높은 것 먼저
        )

        if not pending_entries:
            return

        logger.info(
            f"[AdaptiveThrottle] Starting DLQ replay for {len(pending_entries)} entries "
            f"(recovery={recovery_percent}%)"
        )

        replayed = 0
        failed = 0

        for entry in pending_entries:
            # Throttle 건강 상태 재확인
            if not self._is_throttle_healthy_for_replay():
                logger.warning(
                    f"[AdaptiveThrottle] Throttle health degraded, pausing DLQ replay "
                    f"(replayed={replayed}, remaining={len(pending_entries) - replayed})"
                )
                break

            # Replay 실행
            result = self._dlq_service.replay_throttle_aware(
                entry_id=entry.id,
                throttle=self,
            )

            if result.success:
                replayed += 1
            else:
                failed += 1

            # Rate limiting: 배치 간 간격
            if replayed % self._replay_batch_size == 0:
                time.sleep(self._replay_interval_ms / 1000)

        # Emit replay completion event
        if self._event_bus:
            self._event_bus.emit(
                EventType.THROTTLE_REJECTION_REPLAY_COMPLETED,
                {
                    "replayed": replayed,
                    "failed": failed,
                    "remaining": len(pending_entries) - replayed - failed,
                },
            )

    def _is_throttle_healthy_for_replay(self) -> bool:
        """Replay 계속 가능 여부 확인."""
        # Full Stop 시 중단
        if self._full_stop_active:
            return False

        # Emergency 시 중단
        if self._emergency_level > 0:
            return False

        # 50% 미만 capacity 시 중단
        capacity_ratio = self._current_limit / self.config.initial_limit
        if capacity_ratio < 0.5:
            return False

        return True
```

### 3.3 DLQService - Throttle-aware Replay

**수정 위치**: [dlq/replay_operations.py](../../packages/selfhealing-python/src/selfhealing/services/dlq/replay_operations.py)

```python
class ReplayOperationsMixin:

    def replay_throttle_aware(
        self,
        entry_id: str,
        throttle: "AdaptiveThrottle",
        executor: Callable[[DLQEntry], bool] | None = None,
    ) -> DLQReplayResult:
        """
        Throttle 상태를 고려한 안전한 Replay.

        Args:
            entry_id: 재처리할 entry ID
            throttle: AdaptiveThrottle 인스턴스
            executor: 커스텀 실행기

        Returns:
            DLQReplayResult
        """
        entry = self._repository.get(entry_id)
        if entry is None:
            return DLQReplayResult(success=False, error="Entry not found")

        # Throttle permit 획득 시도 (DLQ 재저장 방지)
        decision = throttle.acquire(
            context={"domain": entry.domain, "replay": True},
            store_rejection=False,  # Replay 거부 시 DLQ 재저장 안 함
        )

        if not decision.allowed:
            # Throttle 거부 → 재시도 대기
            entry.replay_attempts += 1
            entry.last_replay_error = f"Throttle rejected: {decision.reason}"
            self._repository.update(entry)

            return DLQReplayResult(
                success=False,
                error=f"Throttle rejected: {decision.reason}",
                retry_after=decision.wait_time,
            )

        # Replay 실행
        try:
            if executor:
                success = executor(entry)
            else:
                success = self._default_executor(entry)

            if success:
                entry.status = DLQStatus.RESOLVED
                entry.resolved_at = datetime.utcnow()
                entry.metadata["replay_method"] = "throttle_aware"
            else:
                entry.replay_attempts += 1
                entry.last_replay_error = "Executor returned False"

            self._repository.update(entry)
            return DLQReplayResult(success=success, entry_id=entry_id)

        except Exception as e:
            entry.replay_attempts += 1
            entry.last_replay_error = str(e)
            self._repository.update(entry)
            return DLQReplayResult(success=False, error=str(e))

    def replay_all_throttle_aware(
        self,
        throttle: "AdaptiveThrottle",
        domain: str | None = None,
        batch_size: int = 10,
        max_entries: int = 100,
        executor: Callable[[DLQEntry], bool] | None = None,
    ) -> DLQBatchReplayResult:
        """
        Throttle 상태를 고려한 배치 Replay.

        Recovery와 함께 사용되며 Throttle이 건강할 때만 진행합니다.
        """
        entries = self._repository.query(
            domain=domain,
            status=DLQStatus.PENDING,
            limit=max_entries,
            order_by="priority",
        )

        results = DLQBatchReplayResult(
            total=len(entries),
            succeeded=0,
            failed=0,
            skipped=0,
        )

        for i, entry in enumerate(entries):
            # 배치 단위 Throttle 건강 확인
            if i % batch_size == 0 and i > 0:
                stats = throttle.get_stats()
                if stats.get("emergency", {}).get("level", 0) > 0:
                    results.skipped = len(entries) - i
                    results.early_stop_reason = "emergency_mode_activated"
                    break

            result = self.replay_throttle_aware(
                entry_id=entry.id,
                throttle=throttle,
                executor=executor,
            )

            if result.success:
                results.succeeded += 1
            else:
                results.failed += 1

        return results
```

### 3.4 신규 EventType 정의

**추가 위치**: [event_bus.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus.py)

```python
class EventType(str, Enum):
    # ... 기존 이벤트들 ...

    # Throttle + DLQ 연동 이벤트
    THROTTLE_REJECTION_STORED = "throttle_rejection_stored"
    THROTTLE_REJECTION_REPLAY_STARTED = "throttle_rejection_replay_started"
    THROTTLE_REJECTION_REPLAY_COMPLETED = "throttle_rejection_replay_completed"
    THROTTLE_REJECTION_REPLAY_FAILED = "throttle_rejection_replay_failed"
```

### 3.5 DLQReplayResult 확장

```python
@dataclass
class DLQReplayResult:
    success: bool
    entry_id: str | None = None
    error: str | None = None
    retry_after: int | None = None  # 신규: Throttle 거부 시 재시도 대기 시간


@dataclass
class DLQBatchReplayResult:
    total: int
    succeeded: int
    failed: int
    skipped: int = 0
    early_stop_reason: str | None = None
```

---

## 4. ThrottleConfig 확장

```python
@dataclass
class ThrottleConfig:
    # ... 기존 필드들 ...

    # DLQ 연동 설정
    dlq_on_rejection: bool = True  # 거부 시 DLQ 저장
    auto_replay_on_recovery: bool = True  # Recovery 시 자동 Replay
    replay_batch_size: int = 10  # 배치 크기
    replay_interval_ms: int = 100  # 배치 간 간격
    replay_min_recovery_percent: float = 50.0  # Replay 시작 최소 Recovery %
```

---

## 5. 테스트 케이스

### 5.1 단위 테스트

```python
class TestThrottleRejectionDLQStorage:
    """Throttle 거부 DLQ 저장 테스트."""

    def test_rejection_stores_to_dlq(self):
        """Throttle 거부 시 DLQ에 저장."""
        mock_dlq = Mock(spec=DLQService)
        throttle = AdaptiveThrottle(
            ThrottleConfig(initial_limit=0),  # 즉시 거부
        )
        throttle._dlq_service = mock_dlq

        context = {
            "domain": "test",
            "operation": "create_order",
            "payload": {"order_id": "123"},
        }

        decision = throttle.acquire(context=context)

        assert decision.allowed is False
        mock_dlq.store.assert_called_once()
        stored_entry = mock_dlq.store.call_args[1]["entry"]
        assert stored_entry.reason.startswith("throttle_rejected:")

    def test_full_stop_stores_with_reason(self):
        """Full Stop 거부 시 사유 기록."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=100))
        throttle._full_stop_active = True
        throttle._dlq_service = Mock()

        decision = throttle.acquire(context={"domain": "test"})

        stored_entry = throttle._dlq_service.store.call_args[1]["entry"]
        assert "full_stop" in stored_entry.reason


class TestAutoReplayOnRecovery:
    """Recovery 자동 Replay 테스트."""

    def test_recovery_triggers_replay(self):
        """50%+ Recovery 시 DLQ Replay 트리거."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=100))
        throttle._dlq_service = Mock()
        throttle._dlq_service.query.return_value = []

        # Recovery 이벤트 시뮬레이션
        event = Mock()
        event.data = {"recovery_percent": 60}

        throttle._on_throttle_recovery_trigger_replay(event)

        # DLQ 조회 시도 확인
        throttle._dlq_service.query.assert_called()

    def test_low_recovery_skips_replay(self):
        """50% 미만 Recovery 시 Replay 스킵."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=100))
        throttle._dlq_service = Mock()

        event = Mock()
        event.data = {"recovery_percent": 30}

        throttle._on_throttle_recovery_trigger_replay(event)

        throttle._dlq_service.query.assert_not_called()


class TestThrottleAwareReplay:
    """Throttle-aware Replay 테스트."""

    def test_replay_acquires_permit(self):
        """Replay 시 Throttle permit 획득."""
        throttle = Mock()
        throttle.acquire.return_value = ThrottleDecision(allowed=True)

        dlq = DLQService(Mock())
        dlq._repository.get = Mock(return_value=DLQEntry(
            id="1", domain="test", operation="op", payload={}
        ))

        result = dlq.replay_throttle_aware("1", throttle)

        throttle.acquire.assert_called_once()
        assert "store_rejection" in str(throttle.acquire.call_args)
```

---

## 6. 메트릭 정의

### 6.1 Prometheus 메트릭

```python
# Throttle 거부 DLQ 저장
throttle_rejection_dlq_stored_total = Counter(
    "selfhealing_throttle_rejection_dlq_stored_total",
    "Total throttle rejections stored to DLQ",
    ["reason", "domain"],
)

# Recovery Replay 통계
throttle_recovery_replay_total = Counter(
    "selfhealing_throttle_recovery_replay_total",
    "Total entries replayed on throttle recovery",
    ["domain", "result"],  # result: succeeded, failed, skipped
)

# Replay 지연 시간
throttle_replay_delay_seconds = Histogram(
    "selfhealing_throttle_replay_delay_seconds",
    "Time between rejection and successful replay",
    ["domain"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)
```

---

## 7. 구현 체크리스트

- [ ] `AdaptiveThrottle._init_dlq_service()` 메서드 추가
- [ ] `AdaptiveThrottle.acquire()` `store_rejection` 파라미터 추가
- [ ] `AdaptiveThrottle._store_throttle_rejection_to_dlq()` 메서드 추가
- [ ] `AdaptiveThrottle._subscribe_throttle_recovery_for_replay()` 메서드 추가
- [ ] `AdaptiveThrottle._on_throttle_recovery_trigger_replay()` 메서드 추가
- [ ] `AdaptiveThrottle._trigger_throttle_rejection_replay()` 메서드 추가
- [ ] `AdaptiveThrottle._is_throttle_healthy_for_replay()` 메서드 추가
- [ ] `ReplayOperationsMixin.replay_throttle_aware()` 메서드 추가
- [ ] `ReplayOperationsMixin.replay_all_throttle_aware()` 메서드 추가
- [ ] `DLQReplayResult.retry_after` 필드 추가
- [ ] `DLQBatchReplayResult` dataclass 추가
- [ ] `ThrottleConfig` 신규 필드 추가
- [ ] 신규 `EventType` 정의
- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성
- [ ] Prometheus 메트릭 추가

---

## 8. 참고 문서

- [191_ERROR_BUDGET_GATE_THROTTLE_INTEGRATION.md](191_ERROR_BUDGET_GATE_THROTTLE_INTEGRATION.md) - Error Budget 연동
- [192_RETRY_HANDLER_BACKOFF_THROTTLE_INTEGRATION.md](192_RETRY_HANDLER_BACKOFF_THROTTLE_INTEGRATION.md) - Retry Handler 연동
- [189_DLQ_INTEGRATION.md](189_DLQ_INTEGRATION.md) - DLQ 기본 구현
