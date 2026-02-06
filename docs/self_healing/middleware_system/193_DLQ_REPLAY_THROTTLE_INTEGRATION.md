# 193. DLQ + Replay - AdaptiveThrottle 거부된 요청 재처리 연동 구현

> **문서 버전**: 1.1.0
> **최종 수정일**: 2026-02-07
> **작성 근거**:
> - `selfhealing/services/dlq/store_operations.py` (Line 1-290)
> - `selfhealing/services/dlq/replay_operations.py` (Line 1-240)
> - `selfhealing/services/dlq/query_operations.py` (Line 1-130)
> - `selfhealing/services/dlq/base.py` (Line 1-115)
> - `selfhealing/services/dlq_models.py` (DLQConfig: max_replay_attempts, expiry_hours)
> - `selfhealing/services/throttle/adaptive.py` (Line 1548-1577 Recovery)
> - `selfhealing/services/throttle/config.py` (ThrottleConfig, ThrottleResult.remaining)
> - `selfhealing/interfaces/repositories.py` (FailedOperationData: expires_at, can_retry)
> - `selfhealing/services/hedging/result.py` (HedgingResult.hedged)
> - `selfhealing/audit/persistence/disk_buffer.py` (DiskPersistentBuffer)
> **우선순위**: 🟡 P2
> **보완 버전**: v1.1.0 — 리뷰 항목 #3~#10 반영 (Death Spiral 방지, Idempotency 파이프라인, TTL 검증, 샘플링, DiskPersistentBuffer, trace_id, Hedging 필터, Adaptive Pacing)

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

| 문제 | 설명 | 보완 (v1.1.0) |
|------|------|------|
| Throttle 거부 미저장 | Throttle에 의해 거부된 요청은 DLQ에 저장 안 됨 | v1.0.0 해결 |
| 수동 Replay 필요 | Recovery 후에도 수동으로 Replay 트리거 필요 | v1.0.0 해결 |
| 무차별 Replay | Replay 시 Throttle 상태 미확인 | v1.0.0 해결 |
| 대량 Replay 위험 | 축적된 요청 일괄 Replay 시 Re-throttle 위험 | v1.0.0 해결 |
| **Death Spiral** | `query()` 사용으로 max_replay_attempts 필터 누락 → 무한 재시도 | **[Review 6]** `get_replayable_entries()` 사용 |
| **can_replay() bypass** | `executor(entry)` 직접 호출로 안전 검증 우회 | **[Review 7]** `_execute_replay()` 파이프라인 사용 |
| **TTL 미검증** | `expires_at` 확인 없이 만료 엔트리 Replay 시도 | **[Review 10]** `expires_at` 검증 추가 |
| **Hedging 중복 저장** | 보조 요청도 DLQ에 저장되어 중복 발생 | **[Review 9]** `hedged` 필터 추가 |
| **샘플링 부재** | 모든 거부 요청 무조건 저장 → DLQ 폭주 | **[Review 3]** tier_id 기반 샘플링 |
| **Fallback 내구성 부족** | JSONL 단순 append, CRC32/무결성 검증 없음 | **[Review 4]** DiskPersistentBuffer 적용 |
| **trace_id 누락** | metadata에 원본 trace_id 미보존 | **[Review 8]** `original_trace_id` 추가 |
| **고정 Pacing** | capacity 무관 동일 간격/배치 크기 | **[Review 5]** Adaptive Pacing 추가 |

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
│                    (v1.1.0 보완: Review 3, 9 반영)                       │
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
│                             │   │[Review 9] hedged?  │                  │
│                             │   └────────┬───────────┘                  │
│                             │     No     │    Yes                       │
│                             │     ▼      │     ▼                        │
│                             │   ┌─────┐  │   ┌──────┐                   │
│                             │   │계속 │  │   │SKIP  │                   │
│                             │   └──┬──┘  │   └──────┘                   │
│                             │      ▼     │                              │
│                             │   ┌────────────────────┐                  │
│                             │   │[Review 3] tier_id? │                  │
│                             │   └────────┬───────────┘                  │
│                             │      │     │      │                       │
│                             │  critical standard non_essential           │
│                             │      │     │      │                       │
│                             │      ▼     ▼      ▼                       │
│                             │  ┌─────┐┌──────┐┌──────┐                  │
│                             │  │100% ││sample││SKIP  │                  │
│                             │  │저장 ││_rate ││      │                  │
│                             │  └──┬──┘└──┬───┘└──────┘                  │
│                             │     ▼      ▼                              │
│                             │   ┌────────────────────┐                  │
│                             │   │DLQ 저장             │                 │
│                             │   │- reason: throttle_  │                 │
│                             │   │  rejected           │                 │
│                             │   │- metadata:          │                 │
│                             │   │  throttle_state +   │                 │
│                             │   │  [R8] trace_id +    │                 │
│                             │   │  tier_id            │                 │
│                             │   └────────────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.3 Replay 안전 검증 흐름 (v1.1.0)

> **보완 사항**: [Review 6, 7, 10] 반영. 기존에 없던 Replay 진입 검증 흐름 추가.

```
┌─────────────────────────────────────────────────────────────────────────┐
│              replay_throttle_aware() 안전 검증 파이프라인                  │
│              코드 근거: replay_operations.py, query_operations.py          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│                    replay_throttle_aware(entry_id)                       │
│                               │                                         │
│                               ▼                                         │
│                 ┌──────────────────────┐                                │
│                 │[R10] expires_at 확인 │                                │
│                 │FailedOperationData   │                                │
│                 │.expires_at           │                                │
│                 └──────────┬───────────┘                                │
│                  Valid     │    Expired                                  │
│                    ▼       │      ▼                                      │
│                  ┌───┐     │  ┌────────────┐                            │
│                  │계속│     │  │status=     │                            │
│                  └─┬─┘     │  │"expired"   │                            │
│                    ▼       │  └────────────┘                            │
│           ┌────────────────┐                                            │
│           │[R6] can_retry? │                                            │
│           │retry_count <   │                                            │
│           │max_retries     │                                            │
│           └──────┬─────────┘                                            │
│             Yes  │    No                                                 │
│              ▼   │     ▼                                                 │
│            ┌───┐ │  ┌──────────────────┐                                │
│            │계속│ │  │permanently_failed│                                │
│            └─┬─┘ │  └──────────────────┘                                │
│              ▼   │                                                       │
│     ┌──────────────────────┐                                            │
│     │Throttle.acquire()    │                                            │
│     │store_rejection=False │                                            │
│     └──────────┬───────────┘                                            │
│       Allowed  │   Rejected                                             │
│          ▼     │      ▼                                                  │
│        ┌───┐   │  ┌────────────────┐                                    │
│        │계속│   │  │retry_after=    │                                    │
│        └─┬─┘   │  │wait_time       │                                    │
│          ▼     │  └────────────────┘                                    │
│   ┌──────────────────────┐                                              │
│   │[R7] _execute_replay()│                                              │
│   │ ┌─ can_replay(entry) │                                              │
│   │ │  (도메인별 안전검증)│                                              │
│   │ └─ replay(entry)     │                                              │
│   │   (실제 실행)         │                                              │
│   └──────────────────────┘                                              │
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
        """
        거부된 요청을 DLQ에 저장.

        보완 사항 (v1.1.0):
        - [Review 9] Hedging 요청 필터링: hedged=True인 보조 요청 저장 제외
        - [Review 3] tier_id 기반 샘플링: critical=100%, standard=sampling_rate, non_essential=skip
        - [Review 8] trace_id 보존: 원본 요청의 trace_id를 metadata에 포함

        코드 근거:
        - hedging/result.py: HedgingResult.hedged (bool) - 보조 요청 구분 필드
        - adaptive.py Line 459+: tier_id in {"critical", "standard", "non_essential"}
        - FailedOperationData.metadata: dict[str, Any] (repositories.py)
        """
        # ===== [Review 9] Hedging 요청 필터링 =====
        # 근거: hedging/result.py - HedgingResult.hedged 필드
        # Hedging의 보조(secondary) 요청은 원본과 중복이므로 DLQ 저장 불필요
        if context.get("hedged", False):
            logger.debug(
                "[AdaptiveThrottle] Skipping DLQ store for hedged (secondary) request"
            )
            return

        # ===== [Review 3] tier_id 기반 샘플링 =====
        # 근거: adaptive.py - tier_id in {"critical", "standard", "non_essential"}
        # critical: 항상 저장 (100%) - 비즈니스 영향 큼
        # standard: dlq_store_sampling_rate 확률로 저장 (기본 1.0)
        # non_essential: 저장하지 않음 - 재처리 가치 없음
        tier_id = context.get("tier_id", "standard")

        if tier_id == "non_essential":
            logger.debug(
                "[AdaptiveThrottle] Skipping DLQ store for non_essential tier"
            )
            return

        if tier_id == "standard":
            import random
            if random.random() > self.config.dlq_store_sampling_rate:
                logger.debug(
                    f"[AdaptiveThrottle] Sampled out DLQ store "
                    f"(rate={self.config.dlq_store_sampling_rate})"
                )
                return

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
                # [Review 8] trace_id 보존
                # 근거: Replay 시 새 trace_id 생성 + span link로 원본 연결
                "original_trace_id": context.get("trace_id"),
                "tier_id": tier_id,
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
                    "tier_id": tier_id,
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
        """
        Throttle 거부 요청 Replay 실행.

        보완 사항 (v1.1.0):
        - [Review 6] get_replayable_entries() 사용: query() 대신 max_replay_attempts 필터 적용
          → Death Spiral 방지 (무한 재시도 루프 차단)
        - [Review 6] permanently_failed 상태 처리: 재시도 소진 시 상태 전환
        - [Review 5] Adaptive Pacing: ThrottleResult.remaining 기반 동적 배치 크기 조정

        코드 근거:
        - query_operations.py: get_replayable_entries() → repository.find_replayable(max_retries)
          → retry_count < max_retries 인 엔트리만 반환 (can_retry 프로퍼티와 동일 로직)
        - dlq_models.py: DLQConfig.max_replay_attempts = 2 (기본값)
        - config.py: ThrottleResult.remaining (int) - 남은 permit 수
        - repositories.py: FailedOperationData.can_retry → retry_count < max_retries
        """
        # ===== [Review 6] get_replayable_entries() 사용 =====
        # 기존: self._dlq_service.query(domain=..., status=PENDING, ...)
        # 문제: query()는 retry_count 무관하게 조회 → 소진된 엔트리 무한 재시도
        # 수정: get_replayable_entries()는 find_replayable(max_retries) 호출
        #        → retry_count < max_replay_attempts 조건 자동 필터
        pending_entries = self._dlq_service.get_replayable_entries(
            domain="throttle_rejection",
            limit=self._replay_batch_size,
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

            # ===== [Review 6] max_replay_attempts 소진 확인 =====
            # 근거: FailedOperationData.can_retry = retry_count < max_retries
            if not entry.can_retry:
                logger.warning(
                    f"[AdaptiveThrottle] Entry {entry.id} exhausted retries "
                    f"({entry.retry_count}/{entry.max_retries}), marking permanently_failed"
                )
                self._dlq_service.resolve_entry(
                    entry.id, resolution_type="permanently_failed"
                )
                failed += 1
                continue

            # Replay 실행
            result = self._dlq_service.replay_throttle_aware(
                entry_id=entry.id,
                throttle=self,
            )

            if result.success:
                replayed += 1
            else:
                failed += 1

            # ===== [Review 5] Adaptive Pacing =====
            # 근거: ThrottleResult.remaining (config.py) - 현재 남은 permit 수
            # capacity_ratio가 낮을수록 배치 간격을 늘려 부하 조절
            if replayed % self._replay_batch_size == 0:
                adaptive_interval = self._calculate_adaptive_replay_interval()
                time.sleep(adaptive_interval / 1000)

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

    # ===== [Review 5] Adaptive Pacing 메서드 =====

    def _calculate_adaptive_replay_interval(self) -> float:
        """
        Throttle 상태 기반 동적 Replay 간격 계산.

        코드 근거:
        - config.py: ThrottleResult.remaining (int) - 남은 permit 수
        - adaptive.py: self._current_limit, self.config.initial_limit
        - capacity_ratio가 높을수록 간격 단축 (빠른 소화)
        - capacity_ratio가 낮을수록 간격 확대 (부하 보호)

        Returns:
            밀리초 단위 간격 (float)
        """
        capacity_ratio = self._current_limit / max(self.config.initial_limit, 1)

        if capacity_ratio >= 0.9:
            # 90%+ 회복 → 기본 간격
            return self._replay_interval_ms
        elif capacity_ratio >= 0.7:
            # 70~90% → 2배 간격
            return self._replay_interval_ms * 2
        elif capacity_ratio >= 0.5:
            # 50~70% → 4배 간격
            return self._replay_interval_ms * 4
        else:
            # 50% 미만 → Replay 중단 (is_healthy에서 이미 차단되지만 안전장치)
            return self._replay_interval_ms * 10

    def _calculate_adaptive_batch_size(self) -> int:
        """
        Throttle remaining 기반 동적 배치 크기 계산.

        코드 근거:
        - config.py: ThrottleResult.remaining (int) - 현재 사용 가능 permit
        - 남은 허용량의 최대 50%까지만 Replay에 할당

        Returns:
            동적 배치 크기 (int, 최소 1)
        """
        check_result = self._sliding_window.check(
            key=f"{self.config.key_prefix}:replay",
            tier_id="standard",
        )
        # remaining 기반: 남은 용량의 50% 이내에서 배치 크기 결정
        adaptive_size = max(1, check_result.remaining // 2)
        return min(adaptive_size, self._replay_batch_size)
```

### 3.3 DLQService - Throttle-aware Replay

**수정 위치**: [dlq/replay_operations.py](../../packages/selfhealing-python/src/selfhealing/services/dlq/replay_operations.py)

```python
class ReplayOperationsMixin:

    def replay_throttle_aware(
        self,
        entry_id: str,
        throttle: "AdaptiveThrottle",
    ) -> DLQReplayResult:
        """
        Throttle 상태를 고려한 안전한 Replay.

        보완 사항 (v1.1.0):
        - [Review 7] _execute_replay() 파이프라인 사용: handler.can_replay() → handler.replay()
          → 기존 executor(entry) 직접 호출 제거 (안전 검증 bypass 방지)
        - [Review 10] TTL(expires_at) 만료 검증: Replay 전 expires_at 확인
        - [Review 6] max_replay_attempts 가드: can_retry 검증

        코드 근거:
        - replay_operations.py: _execute_replay() → handler.can_replay(entry) → handler.replay(entry)
        - replay_service.py: ReplayHandler.can_replay() (abstract) - 도메인별 안전 검증
        - repositories.py: FailedOperationData.expires_at (datetime | None)
        - repositories.py: FailedOperationData.can_retry → retry_count < max_retries
        - dlq_models.py: DLQConfig.expiry_hours = 72

        Args:
            entry_id: 재처리할 entry ID
            throttle: AdaptiveThrottle 인스턴스

        Returns:
            DLQReplayResult
        """
        entry = self._repository.get(entry_id)
        if entry is None:
            return DLQReplayResult(success=False, error="Entry not found")

        # ===== [Review 10] TTL 만료 검증 =====
        # 근거: FailedOperationData.expires_at (repositories.py)
        # DLQConfig.expiry_hours = 72 (dlq_models.py)
        # 만료된 엔트리는 Replay 가치가 없으므로 즉시 스킵
        if entry.expires_at and entry.expires_at < datetime.utcnow():
            logger.info(
                f"[DLQService] Entry {entry_id} expired at {entry.expires_at}, "
                f"skipping replay"
            )
            self._repository.update_status(
                entry.id, status="expired",
                resolution_type="ttl_expired",
                resolution_note=f"Expired at {entry.expires_at.isoformat()}"
            )
            return DLQReplayResult(
                success=False,
                error=f"Entry expired at {entry.expires_at.isoformat()}",
            )

        # ===== [Review 6] max_replay_attempts 가드 =====
        # 근거: FailedOperationData.can_retry = retry_count < max_retries
        if not entry.can_retry:
            logger.warning(
                f"[DLQService] Entry {entry_id} exhausted retries "
                f"({entry.retry_count}/{entry.max_retries})"
            )
            self._repository.update_status(
                entry.id, status="permanently_failed",
                resolution_type="max_retries_exhausted",
            )
            return DLQReplayResult(
                success=False,
                error=f"Max retries exhausted ({entry.retry_count}/{entry.max_retries})",
            )

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

        # ===== [Review 7] _execute_replay() 파이프라인 사용 =====
        # 기존 문제: executor(entry) / self._default_executor(entry) 직접 호출
        #   → handler.can_replay() 안전 검증 완전 우회
        # 수정: _execute_replay() 통해 can_replay() → replay() 파이프라인 보장
        # 근거: replay_operations.py Line 22-44:
        #   _execute_replay():
        #     handler = get_replay_handler(entry.domain)
        #     can_replay, reason = handler.can_replay(entry)  ← 이 검증이 핵심
        #     if not can_replay: return False
        #     result = handler.replay(entry)
        try:
            success = self._execute_replay(entry)

            if success:
                entry.status = DLQStatus.RESOLVED
                entry.resolved_at = datetime.utcnow()
                entry.metadata["replay_method"] = "throttle_aware"
            else:
                entry.replay_attempts += 1
                entry.last_replay_error = "Replay handler returned failure"

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
    ) -> DLQBatchReplayResult:
        """
        Throttle 상태를 고려한 배치 Replay.

        보완 사항 (v1.1.0):
        - [Review 6] get_replayable_entries() 사용: query() 대신 max_replay_attempts 필터 적용
        - [Review 7] executor 파라미터 제거: _execute_replay() 파이프라인으로 통일

        Recovery와 함께 사용되며 Throttle이 건강할 때만 진행합니다.
        """
        # [Review 6] get_replayable_entries() → find_replayable(max_retries) 자동 필터
        entries = self.get_replayable_entries(
            domain=domain,
            limit=max_entries,
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

            # [Review 7] _execute_replay() 파이프라인 사용 (replay_throttle_aware 내부)
            result = self.replay_throttle_aware(
                entry_id=entry.id,
                throttle=throttle,
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

### 3.6 [Review 4] DiskPersistentBuffer 기반 Fallback 체인

> **보완 사항 (v1.1.0)**: 현재 `store_operations.py`의 `_write_to_local_fallback()`은
> 단순 JSONL append 방식으로, CRC32 무결성 검증/Group Commit/Dead Letter DB 미지원.
> 기존 시스템에 이미 구현된 `DiskPersistentBuffer` (LMDB 기반, 1244 lines)를 활용하여
> Fallback 체인을 강화함.

**코드 근거**:
- `audit/persistence/disk_buffer.py`: `DiskPersistentBuffer` - LMDB 기반, CRC32 체크섬, Group Commit
- `services/audit/base.py` Line 127-145: 기존 Fallback 체인 패턴
  ```python
  # 1차: DiskPersistentBuffer (영속) - LMDB 기반, Pod 재시작에도 데이터 보존
  # 2차: JSONL fallback (기존 방식)
  # 3차: stderr 출력 (최후 수단)
  ```
- `store_operations.py`: `_write_to_local_fallback()` - 현재 단순 JSONL append

**수정 위치**: [dlq/store_operations.py](../../packages/selfhealing-python/src/selfhealing/services/dlq/store_operations.py)

```python
class StoreOperationsMixin:

    def _write_to_local_fallback(
        self,
        entry_data: dict[str, Any],
        original_error: str,
    ) -> str | None:
        """
        Local fallback with DiskPersistentBuffer (LMDB) upgrade.

        Fallback 체인 (audit/base.py 패턴 준수):
        1차: DiskPersistentBuffer (LMDB) - CRC32 무결성, Group Commit, Pod 재시작 내구성
        2차: JSONL 파일 (기존 방식) - DiskPersistentBuffer 불가 시
        3차: stderr 출력 - 모든 것이 실패 시 최소한의 기록

        코드 근거:
        - audit/persistence/disk_buffer.py: DiskPersistentBuffer.get_instance()
        - services/audit/base.py Line 133-145: 동일 패턴 사용 중
        """
        # 1차: DiskPersistentBuffer (LMDB)
        try:
            from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

            buffer = DiskPersistentBuffer.get_instance()
            # 근거: audit/base.py Line 137 - buffer.put(entry) API
            buffer.put({
                "category": "dlq_fallback",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "original_error": original_error,
                "entry_data": entry_data,
                "pending_reconciliation": True,
            })
            logger.info("[DLQService] Fallback saved to DiskPersistentBuffer (LMDB)")
            return "disk_persistent_buffer://dlq_fallback"
        except ImportError:
            logger.debug("[DLQService] DiskPersistentBuffer not available")
        except Exception as e:
            logger.warning(f"[DLQService] DiskPersistentBuffer failed: {e}")

        # 2차: JSONL 파일 (기존 방식)
        try:
            with self._fallback_lock:
                DLQ_FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)

                fallback_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "original_error": original_error,
                    "entry_data": entry_data,
                    "pending_reconciliation": True,
                }

                with open(DLQ_FALLBACK_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(fallback_entry, default=str) + "\n")

                logger.info(f"[DLQService] Fallback saved to JSONL: {DLQ_FALLBACK_PATH}")
                return str(DLQ_FALLBACK_PATH)

        except Exception as fallback_error:
            # 3차: stderr 출력 (최후 수단)
            import sys
            print(
                f"[DLQ CRITICAL] All fallbacks failed. "
                f"DB: {original_error}, LMDB: N/A, JSONL: {fallback_error}. "
                f"Data: {json.dumps(entry_data, default=str)[:500]}",
                file=sys.stderr,
            )
            logger.critical(
                f"[DLQService] CRITICAL: All fallback methods failed! "
                f"DB: {original_error}, JSONL: {fallback_error}"
            )
            return None
```

---

## 4. ThrottleConfig 확장

```python
@dataclass
class ThrottleConfig:
    # ... 기존 필드들 (config.py 참고) ...
    # initial_limit, window_seconds, min_limit, max_limit,
    # sample_interval_ms, smoothing_factor, decrease_ratio, increase_step,
    # sla_warning_ms, sla_critical_ms, emergency_limit, key_prefix, service_name

    # DLQ 연동 설정
    dlq_on_rejection: bool = True  # 거부 시 DLQ 저장
    auto_replay_on_recovery: bool = True  # Recovery 시 자동 Replay
    replay_batch_size: int = 10  # 배치 크기
    replay_interval_ms: int = 100  # 배치 간 간격 (기본값, Adaptive Pacing에 의해 조정됨)
    replay_min_recovery_percent: float = 50.0  # Replay 시작 최소 Recovery %

    # [Review 3] 샘플링/필터링 설정
    # 근거: tier_id in {"critical", "standard", "non_essential"} (adaptive.py)
    dlq_store_sampling_rate: float = 1.0  # standard tier 저장 확률 (0.0~1.0)
    dlq_store_non_essential: bool = False  # non_essential tier 저장 여부
```

> **참고**: 기존 `ThrottleConfig` (config.py)에는 DLQ 관련 필드가 없으므로
> 위 필드들을 모두 신규 추가해야 합니다. `from_settings()`, `from_dict()` 메서드도
> 함께 확장 필요.

---

## 5. 테스트 케이스

### 5.1 단위 테스트 — 기존 (v1.0.0)

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
        # [Review 6] get_replayable_entries() 사용으로 수정
        throttle._dlq_service.get_replayable_entries.return_value = []

        event = Mock()
        event.data = {"recovery_percent": 60}

        throttle._on_throttle_recovery_trigger_replay(event)

        # get_replayable_entries 호출 확인 (query가 아닌)
        throttle._dlq_service.get_replayable_entries.assert_called()

    def test_low_recovery_skips_replay(self):
        """50% 미만 Recovery 시 Replay 스킵."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=100))
        throttle._dlq_service = Mock()

        event = Mock()
        event.data = {"recovery_percent": 30}

        throttle._on_throttle_recovery_trigger_replay(event)

        throttle._dlq_service.get_replayable_entries.assert_not_called()


class TestThrottleAwareReplay:
    """Throttle-aware Replay 테스트."""

    def test_replay_acquires_permit(self):
        """Replay 시 Throttle permit 획득."""
        throttle = Mock()
        throttle.acquire.return_value = ThrottleDecision(allowed=True)

        dlq = DLQService(Mock())
        dlq._repository.get = Mock(return_value=make_pending_entry())

        result = dlq.replay_throttle_aware("1", throttle)

        throttle.acquire.assert_called_once()
        assert "store_rejection" in str(throttle.acquire.call_args)
```

### 5.2 단위 테스트 — 보완 사항 (v1.1.0)

```python
# ===========================================================================
# [Review 9] Hedging 필터 테스트
# 코드 근거: hedging/result.py - HedgingResult.hedged (bool)
# ===========================================================================

class TestHedgingFilter:
    """[Review 9] Hedging 요청 DLQ 저장 필터링 테스트."""

    def test_hedged_request_skips_dlq_store(self):
        """hedged=True인 보조 요청은 DLQ 저장하지 않음."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=0))
        throttle._dlq_service = Mock()

        context = {
            "domain": "test",
            "hedged": True,  # HedgingResult.hedged = True
        }

        throttle.acquire(context=context)

        # store 호출되지 않아야 함
        throttle._dlq_service.store.assert_not_called()

    def test_primary_request_stores_to_dlq(self):
        """hedged=False인 원본 요청은 정상 DLQ 저장."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=0))
        throttle._dlq_service = Mock()

        context = {
            "domain": "test",
            "hedged": False,
        }

        throttle.acquire(context=context)

        throttle._dlq_service.store.assert_called_once()


# ===========================================================================
# [Review 3] tier_id 기반 샘플링 테스트
# 코드 근거: adaptive.py - tier_id in {"critical", "standard", "non_essential"}
# ===========================================================================

class TestTierSampling:
    """[Review 3] tier_id 기반 DLQ 저장 샘플링 테스트."""

    def test_critical_always_stored(self):
        """critical tier는 항상 100% 저장."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=0))
        throttle._dlq_service = Mock()

        context = {"domain": "payment", "tier_id": "critical"}
        throttle.acquire(context=context)

        throttle._dlq_service.store.assert_called_once()

    def test_non_essential_never_stored(self):
        """non_essential tier는 저장하지 않음."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=0))
        throttle._dlq_service = Mock()

        context = {"domain": "log", "tier_id": "non_essential"}
        throttle.acquire(context=context)

        throttle._dlq_service.store.assert_not_called()

    def test_standard_respects_sampling_rate(self):
        """standard tier는 dlq_store_sampling_rate에 따라 저장."""
        config = ThrottleConfig(initial_limit=0)
        config.dlq_store_sampling_rate = 0.0  # 0% → 저장 안 함

        throttle = AdaptiveThrottle(config)
        throttle._dlq_service = Mock()

        context = {"domain": "test", "tier_id": "standard"}
        throttle.acquire(context=context)

        throttle._dlq_service.store.assert_not_called()


# ===========================================================================
# [Review 8] trace_id 컨텍스트 테스트
# 코드 근거: FailedOperationData.metadata: dict[str, Any] (repositories.py)
# ===========================================================================

class TestTraceIdContext:
    """[Review 8] trace_id metadata 보존 테스트."""

    def test_trace_id_stored_in_metadata(self):
        """DLQ 저장 시 original_trace_id가 metadata에 포함."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=0))
        throttle._dlq_service = Mock()

        context = {
            "domain": "test",
            "trace_id": "abc-123-trace",
        }

        throttle.acquire(context=context)

        stored_entry = throttle._dlq_service.store.call_args[1]["entry"]
        assert stored_entry.metadata["original_trace_id"] == "abc-123-trace"


# ===========================================================================
# [Review 10] TTL 만료 검증 테스트
# 코드 근거: FailedOperationData.expires_at (repositories.py)
#            DLQConfig.expiry_hours = 72 (dlq_models.py)
# ===========================================================================

class TestTTLExpiry:
    """[Review 10] TTL 만료 엔트리 Replay 차단 테스트."""

    def test_expired_entry_skips_replay(self):
        """expires_at이 과거인 엔트리는 Replay하지 않음."""
        dlq = DLQService(Mock())
        expired_entry = make_pending_entry(
            expires_at=datetime(2025, 1, 1),  # 과거 시점
        )
        dlq._repository.get = Mock(return_value=expired_entry)

        throttle = Mock()
        result = dlq.replay_throttle_aware("1", throttle)

        assert result.success is False
        assert "expired" in result.error
        # Throttle acquire는 호출되지 않아야 함
        throttle.acquire.assert_not_called()

    def test_valid_entry_proceeds_to_replay(self):
        """expires_at이 미래인 엔트리는 정상 Replay 진행."""
        dlq = DLQService(Mock())
        valid_entry = make_pending_entry(
            expires_at=datetime(2099, 12, 31),
            retry_count=0, max_retries=2,
        )
        dlq._repository.get = Mock(return_value=valid_entry)

        throttle = Mock()
        throttle.acquire.return_value = ThrottleDecision(allowed=True)

        result = dlq.replay_throttle_aware("1", throttle)

        throttle.acquire.assert_called_once()


# ===========================================================================
# [Review 6] Death Spiral 방지 테스트
# 코드 근거: query_operations.py - get_replayable_entries()
#            → repository.find_replayable(max_retries=config.max_replay_attempts)
#            FailedOperationData.can_retry = retry_count < max_retries
# ===========================================================================

class TestDeathSpiralPrevention:
    """[Review 6] max_replay_attempts 소진 시 영구 실패 처리 테스트."""

    def test_exhausted_retries_marked_permanently_failed(self):
        """재시도 한도 소진 엔트리 → permanently_failed 상태."""
        dlq = DLQService(Mock())
        exhausted_entry = make_pending_entry(
            retry_count=2, max_retries=2,  # can_retry = False
        )
        dlq._repository.get = Mock(return_value=exhausted_entry)

        throttle = Mock()
        result = dlq.replay_throttle_aware("1", throttle)

        assert result.success is False
        assert "Max retries exhausted" in result.error
        # Throttle acquire는 호출되지 않아야 함
        throttle.acquire.assert_not_called()

    def test_trigger_uses_get_replayable_entries(self):
        """_trigger_throttle_rejection_replay()는 get_replayable_entries() 사용."""
        throttle = AdaptiveThrottle(ThrottleConfig(initial_limit=100))
        throttle._dlq_service = Mock()
        throttle._dlq_service.get_replayable_entries.return_value = []

        throttle._trigger_throttle_rejection_replay(recovery_percent=80)

        # query()가 아닌 get_replayable_entries() 호출 확인
        throttle._dlq_service.get_replayable_entries.assert_called_once_with(
            domain="throttle_rejection",
            limit=throttle._replay_batch_size,
        )


# ===========================================================================
# [Review 7] _execute_replay() 파이프라인 테스트
# 코드 근거: replay_operations.py Line 22-44
#   _execute_replay() → handler.can_replay(entry) → handler.replay(entry)
# ===========================================================================

class TestExecuteReplayPipeline:
    """[Review 7] can_replay() → replay() 파이프라인 보장 테스트."""

    def test_replay_throttle_aware_uses_execute_replay(self):
        """replay_throttle_aware()가 _execute_replay() 통해 실행."""
        dlq = DLQService(Mock())
        entry = make_pending_entry(retry_count=0, max_retries=2)
        dlq._repository.get = Mock(return_value=entry)
        dlq._execute_replay = Mock(return_value=True)

        throttle = Mock()
        throttle.acquire.return_value = ThrottleDecision(allowed=True)

        result = dlq.replay_throttle_aware("1", throttle)

        # _execute_replay 호출 확인 (executor 직접 호출이 아닌)
        dlq._execute_replay.assert_called_once_with(entry)
        assert result.success is True

    def test_can_replay_false_blocks_execution(self):
        """handler.can_replay()이 False면 실행 차단."""
        dlq = DLQService(Mock())
        entry = make_pending_entry(retry_count=0, max_retries=2)
        dlq._repository.get = Mock(return_value=entry)
        # _execute_replay가 False 반환 (can_replay 실패)
        dlq._execute_replay = Mock(return_value=False)

        throttle = Mock()
        throttle.acquire.return_value = ThrottleDecision(allowed=True)

        result = dlq.replay_throttle_aware("1", throttle)

        assert result.success is False


# ===========================================================================
# [Review 5] Adaptive Pacing 테스트
# 코드 근거: ThrottleResult.remaining (config.py)
#            self._current_limit / self.config.initial_limit
# ===========================================================================

class TestAdaptivePacing:
    """[Review 5] Throttle 상태 기반 동적 간격/배치 조정 테스트."""

    def test_high_capacity_returns_base_interval(self):
        """90%+ capacity에서 기본 간격 반환."""
        throttle = AdaptiveThrottle(ThrottleConfig(
            initial_limit=100, replay_interval_ms=100
        ))
        throttle._current_limit = 95  # 95% capacity

        interval = throttle._calculate_adaptive_replay_interval()
        assert interval == 100  # 기본 간격

    def test_low_capacity_returns_longer_interval(self):
        """50~70% capacity에서 4배 간격 반환."""
        throttle = AdaptiveThrottle(ThrottleConfig(
            initial_limit=100, replay_interval_ms=100
        ))
        throttle._current_limit = 55  # 55% capacity

        interval = throttle._calculate_adaptive_replay_interval()
        assert interval == 400  # 4배 간격


# ===========================================================================
# [Review 4] DiskPersistentBuffer Fallback 테스트
# 코드 근거: audit/persistence/disk_buffer.py - DiskPersistentBuffer.get_instance()
#            services/audit/base.py Line 133-145 - 동일 패턴
# ===========================================================================

class TestDiskPersistentBufferFallback:
    """[Review 4] DiskPersistentBuffer 기반 Fallback 체인 테스트."""

    @patch("selfhealing.audit.persistence.disk_buffer.DiskPersistentBuffer")
    def test_lmdb_fallback_used_first(self, mock_buffer_cls):
        """1차 Fallback으로 DiskPersistentBuffer 사용."""
        mock_buffer = Mock()
        mock_buffer_cls.get_instance.return_value = mock_buffer

        dlq = DLQService(Mock())
        result = dlq._write_to_local_fallback(
            {"domain": "test"}, "db_error"
        )

        mock_buffer.put.assert_called_once()
        assert "disk_persistent_buffer" in result

    @patch("selfhealing.audit.persistence.disk_buffer.DiskPersistentBuffer")
    def test_jsonl_fallback_when_lmdb_fails(self, mock_buffer_cls):
        """DiskPersistentBuffer 실패 시 JSONL 파일 Fallback."""
        mock_buffer_cls.get_instance.side_effect = RuntimeError("LMDB error")

        dlq = DLQService(Mock())
        with patch("builtins.open", mock_open()):
            result = dlq._write_to_local_fallback(
                {"domain": "test"}, "db_error"
            )

        assert result is not None
        assert "disk_persistent_buffer" not in result
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

# [Review 3] 샘플링 통계
throttle_rejection_sampled_out_total = Counter(
    "selfhealing_throttle_rejection_sampled_out_total",
    "Total throttle rejections filtered by sampling",
    ["tier_id", "reason"],  # reason: non_essential, sampling_rate, hedged
)

# [Review 9] Hedging 필터 통계
throttle_rejection_hedged_skipped_total = Counter(
    "selfhealing_throttle_rejection_hedged_skipped_total",
    "Total hedged requests skipped from DLQ storage",
    ["domain"],
)

# [Review 10] TTL 만료 엔트리 통계
throttle_replay_ttl_expired_total = Counter(
    "selfhealing_throttle_replay_ttl_expired_total",
    "Total entries skipped due to TTL expiry",
    ["domain"],
)

# [Review 6] 재시도 소진 엔트리 통계
throttle_replay_permanently_failed_total = Counter(
    "selfhealing_throttle_replay_permanently_failed_total",
    "Total entries marked permanently_failed (max_retries exhausted)",
    ["domain"],
)

# [Review 5] Adaptive Pacing 현황
throttle_replay_adaptive_interval_ms = Gauge(
    "selfhealing_throttle_replay_adaptive_interval_ms",
    "Current adaptive replay interval based on capacity ratio",
)

# [Review 4] Fallback 채널 통계
throttle_dlq_fallback_total = Counter(
    "selfhealing_throttle_dlq_fallback_total",
    "Total DLQ fallback writes by channel",
    ["channel"],  # channel: disk_persistent_buffer, jsonl, stderr
)
```

---

## 7. 구현 체크리스트

### 7.1 기본 구현 (v1.0.0)

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

### 7.2 보완 구현 (v1.1.0)

**P0 — 안전성 필수**
- [ ] **[Review 6]** `_trigger_throttle_rejection_replay()`: `query()` → `get_replayable_entries()` 변경
- [ ] **[Review 6]** `replay_throttle_aware()`: `entry.can_retry` 가드 + `permanently_failed` 상태 처리
- [ ] **[Review 6]** `replay_all_throttle_aware()`: `query()` → `get_replayable_entries()` 변경
- [ ] **[Review 6]** `replay_all_throttle_aware()`: `executor` 파라미터 제거
- [ ] **[Review 7]** `replay_throttle_aware()`: `executor(entry)` / `_default_executor(entry)` → `_execute_replay(entry)` 변경
- [ ] **[Review 7]** `_execute_replay()` 파이프라인 검증 테스트 작성

**P1 — 안정성 강화**
- [ ] **[Review 10]** `replay_throttle_aware()`: `expires_at` TTL 검증 추가
- [ ] **[Review 10]** TTL 만료 엔트리 `status="expired"` 전환 로직
- [ ] **[Review 10]** `throttle_replay_ttl_expired_total` 메트릭 추가

**P2 — 개선 사항**
- [ ] **[Review 3]** `_store_throttle_rejection_to_dlq()`: Hedging 필터 + tier_id 샘플링 로직 추가
- [ ] **[Review 3]** `ThrottleConfig`: `dlq_store_sampling_rate`, `dlq_store_non_essential` 필드 추가
- [ ] **[Review 4]** `_write_to_local_fallback()`: `DiskPersistentBuffer.put()` 1차 Fallback 추가
- [ ] **[Review 4]** `_write_to_local_fallback()`: stderr 3차 Fallback 추가
- [ ] **[Review 8]** `_store_throttle_rejection_to_dlq()`: `original_trace_id` metadata 추가
- [ ] **[Review 8]** `_store_throttle_rejection_to_dlq()`: `tier_id` metadata 추가
- [ ] **[Review 9]** `_store_throttle_rejection_to_dlq()`: `context.get("hedged")` 필터 추가
- [ ] **[Review 9]** `throttle_rejection_hedged_skipped_total` 메트릭 추가

**P3 — 개선 사항**
- [ ] **[Review 5]** `_calculate_adaptive_replay_interval()` 메서드 추가
- [ ] **[Review 5]** `_calculate_adaptive_batch_size()` 메서드 추가
- [ ] **[Review 5]** `throttle_replay_adaptive_interval_ms` Gauge 메트릭 추가

---

## 8. 참고 문서

- [191_ERROR_BUDGET_GATE_THROTTLE_INTEGRATION.md](191_ERROR_BUDGET_GATE_THROTTLE_INTEGRATION.md) - Error Budget 연동
- [192_RETRY_HANDLER_BACKOFF_THROTTLE_INTEGRATION.md](192_RETRY_HANDLER_BACKOFF_THROTTLE_INTEGRATION.md) - Retry Handler 연동
- [189_DLQ_INTEGRATION.md](189_DLQ_INTEGRATION.md) - DLQ 기본 구현

---

## 9. 보완 사항 요약 (v1.1.0)

> 본 섹션은 v1.0.0 설계에 대한 리뷰 항목 #3~#10의 보완 내용을 요약합니다.
> 모든 내용은 기존 코드를 근거로 작성되었습니다.

### 9.1 보완 항목 요약표

| # | 리뷰 | 우선순위 | 문제 | 해결 | 코드 근거 |
|---|------|---------|------|------|----------|
| 3 | 샘플링/필터링 | P2 | 모든 거부 요청 무조건 DLQ 저장 → 폭주 위험 | tier_id 기반 샘플링: critical=100%, standard=sampling_rate, non_essential=skip | `adaptive.py` tier_id, `ThrottleConfig` 신규 필드 |
| 4 | DiskPersistentBuffer | P2 | JSONL 단순 append, 무결성 검증 없음 | 3단계 Fallback 체인: LMDB → JSONL → stderr | `disk_buffer.py` `put()`, `audit/base.py` L127-145 |
| 5 | Adaptive Pacing | P3 | capacity 무관 동일 간격/배치 크기 | capacity_ratio 기반 동적 간격/배치 조정 | `ThrottleResult.remaining` (`config.py`) |
| 6 | Death Spiral 방지 | **P0** | `query()`로 max_retries 필터 누락 → 무한 재시도 | `get_replayable_entries()` + `can_retry` 가드 | `query_operations.py` L48-73, `repositories.py` `can_retry` |
| 7 | Idempotency 파이프라인 | **P0** | `executor(entry)` 직접 호출 → `can_replay()` bypass | `_execute_replay()` 파이프라인으로 통일 | `replay_operations.py` L22-44 |
| 8 | trace_id 컨텍스트 | P2 | metadata에 원본 trace_id 미보존 | `original_trace_id` + `tier_id` metadata 추가 | `FailedOperationData.metadata` (`repositories.py`) |
| 9 | Hedging 필터 | P2 | 보조 요청도 DLQ 저장 → 중복 | `hedged=True` 필터로 보조 요청 제외 | `HedgingResult.hedged` (`hedging/result.py`) |
| 10 | TTL 만료 검증 | **P1** | `expires_at` 확인 없이 만료 엔트리 Replay | `expires_at` 검증 + `expired` 상태 전환 | `FailedOperationData.expires_at`, `DLQConfig.expiry_hours=72` |

### 9.2 수정 영향 범위

| 파일 | 수정 유형 | 관련 리뷰 |
|------|----------|----------|
| `throttle/adaptive.py` | 메서드 수정 | #3, #5, #6, #8, #9 |
| `throttle/config.py` | 필드 추가 | #3, #5 |
| `dlq/replay_operations.py` | 메서드 수정 | #6, #7, #10 |
| `dlq/store_operations.py` | 메서드 수정 | #4 |
| `dlq/query_operations.py` | 기존 메서드 활용 | #6 (get_replayable_entries) |
| `services/event_bus.py` | 기존 EventType 활용 | — |

### 9.3 코드 근거 참조 목록

```
# P0 핵심 근거
replay_operations.py  L22-44   _execute_replay() → handler.can_replay() → handler.replay()
query_operations.py   L48-73   get_replayable_entries() → find_replayable(max_retries)
repositories.py       L170     can_retry = retry_count < max_retries
repositories.py       L157     expires_at: datetime | None

# P1 핵심 근거
dlq_models.py         L23-25   DLQConfig: max_replay_attempts=2, expiry_hours=72

# P2 핵심 근거
hedging/result.py              HedgingResult.hedged: bool
audit/base.py         L127-145 DiskPersistentBuffer Fallback 체인 패턴
disk_buffer.py        L315     DiskPersistentBuffer.put(entry: dict) → bytes | None
config.py             L106     ThrottleResult.remaining: int
```
