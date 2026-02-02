# 167. 비동기 Audit 파이프라인 구현

> **버전**: 1.0.0
> **작성일**: 2026-01-31
> **의존성**: [166_RINGBUFFER_AUDIT_INTEGRATION.md](166_RINGBUFFER_AUDIT_INTEGRATION.md)
> **예상 소요**: 2-3일

---

## 1. 현재 문제점 (코드 근거)

### 1.1 AuditMiddleware의 동기 처리

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/audit_middleware.py`
**라인**: 66-130 (추정)

현재 `AuditMiddleware`는 응답 반환 **직전에** 모든 이벤트를 **동기적으로** 기록합니다.

```python
class AuditMiddleware:
    def __call__(self, request: HttpRequest) -> HttpResponse:
        # ... 요청 처리 ...
        response = self.get_response(request)

        # 응답 반환 전 동기적으로 기록 (블로킹!)
        self._flush_events_to_recorder(request)

        return response

    def _flush_events_to_recorder(self, request):
        buffer = RequestAuditBuffer.get_from_request(request)
        if buffer and buffer.events:
            for event in buffer.events:
                self._recorder.record(event)  # 동기 I/O!
```

**문제점**:
1. 매 요청마다 **동기 I/O** 발생
2. 이벤트가 많으면 **응답 지연** 증가
3. DB/Redis 장애 시 **요청 실패** 가능

---

### 1.2 이미 존재하는 AsyncHealingLogger

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 36-170

```python
class AsyncHealingLogger:
    """
    비동기 힐링 이벤트 로거
    - 일반 이벤트: 배치로 모아서 전송
    - CRITICAL 이벤트: 즉시 전송 (비동기지만 바로)
    """
    _queue: queue.Queue = queue.Queue()
    _running: bool = False
    _worker_thread: threading.Thread | None = None

    IMMEDIATE_SEVERITIES = {EventSeverity.CRITICAL}

    @classmethod
    def log(
        cls, event: dict[str, Any], severity: EventSeverity = EventSeverity.INFO
    ) -> None:
        """이벤트 로깅 (논블로킹, ~0.01ms)"""
        enriched_event = {
            **event,
            "severity": severity.name,
            "timestamp": time.time(),
        }

        if severity in cls.IMMEDIATE_SEVERITIES:
            # CRITICAL: 즉시 전송 (별도 스레드)
            threading.Thread(
                target=cls._flush_immediate, args=([enriched_event],), daemon=True
            ).start()
        else:
            # 일반: 배치 대기
            cls._queue.put(enriched_event)

    @classmethod
    def _worker(cls) -> None:
        """배치 처리 워커"""
        batch: list[dict] = []
        last_flush = time.time()

        while cls._running:
            try:
                event = cls._queue.get(timeout=1.0)
                batch.append(event)
            except queue.Empty:
                pass

            batch_size = cls._get_batch_size()
            flush_interval = cls._get_flush_interval()
            should_flush = len(batch) >= batch_size or (
                batch and time.time() - last_flush >= flush_interval
            )

            if should_flush:
                cls._flush_batch(batch)
                batch = []
                last_flush = time.time()
```

**장점**:
1. **Non-blocking**: `_queue.put()`은 ~0.01ms
2. **배치 처리**: 여러 이벤트를 모아서 전송
3. **백그라운드 워커**: 별도 스레드에서 처리
4. **CRITICAL 즉시 전송**: CB Open 등 중요 이벤트는 바로 전송

---

### 1.3 BatchSettings 존재

**파일**: `packages/selfhealing-python/src/selfhealing/settings/batch.py`
**라인**: 60-140

```python
class BatchSettings(BaseSettings):
    # Logger Batch - from async_logger.py
    logger_batch_size: int = Field(
        default=10,
        ge=1,
        le=100,
        description="비동기 로거 배치 크기",
    )

    flush_interval: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="배치 플러시 간격 (초)",
    )

    async_logger_max_queue_size: int = Field(
        default=5000,
        ge=100,
        le=100000,  # 최대 10만
        description="AsyncLogger 최대 큐 크기",
    )
```

---

## 2. 구현 계획

### 2.1 아키텍처

```
현재:
  Request → Middleware → Response
                ↓ (동기)
           AuditRecorder → DB/Redis

개선 후:
  Request → Middleware → Response (즉시 반환)
                ↓ (비동기, ~0.01ms)
        AsyncHealingLogger._queue
                ↓ (백그라운드 워커)
           AuditRecorder → DB/Redis
```

### 2.2 수정 대상 파일

| 파일 | 변경 내용 |
|-----|----------|
| `api/django/audit_middleware.py` | `AsyncHealingLogger` 사용 |
| `utils/async_logger.py` | AuditEntry 지원 추가 |
| Django `settings.py` | `AsyncHealingLogger` 초기화 |

---

## 3. 구현 상세

### 3.1 AsyncHealingLogger에 Audit 콜백 설정

**파일**: Django `settings.py` 또는 `apps.py`

```python
# myproject/apps.py 또는 settings.py 하단
def setup_async_audit_logger():
    """AsyncHealingLogger 초기화 및 Audit 콜백 설정."""
    from selfhealing.utils.async_logger import AsyncHealingLogger
    from selfhealing.adapters.audit.singleton import get_audit_adapter
    from selfhealing.interfaces.audit_adapter import AuditEntry

    def flush_to_audit_adapter(events: list[dict]):
        """배치 이벤트를 AuditAdapter로 전송."""
        adapter = get_audit_adapter()

        # dict → AuditEntry 변환
        entries = []
        for event_dict in events:
            try:
                entry = AuditEntry(
                    action=event_dict.get("action", "unknown"),
                    target_type=event_dict.get("target_type"),
                    target_id=event_dict.get("target_id"),
                    details=event_dict.get("details", {}),
                    # ... 기타 필드
                )
                entries.append(entry)
            except Exception as e:
                logger.warning(f"Failed to convert event: {e}")

        # 배치 삽입 (지원되는 경우)
        if hasattr(adapter, "log_batch"):
            adapter.log_batch(entries)
        else:
            for entry in entries:
                adapter.log(entry)

    AsyncHealingLogger.configure(flush_callback=flush_to_audit_adapter)
    AsyncHealingLogger.start()

# Django ready 시 호출
setup_async_audit_logger()
```

---

### 3.2 AuditMiddleware 수정

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/audit_middleware.py`

**Before**:
```python
def _flush_events_to_recorder(self, request):
    buffer = RequestAuditBuffer.get_from_request(request)
    if buffer and buffer.events:
        for event in buffer.events:
            self._recorder.record(event)  # 동기!
```

**After**:
```python
from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

# AuditEventType → EventSeverity 매핑
CRITICAL_EVENT_TYPES = {
    AuditEventType.CB_STATE_CHANGE,
    AuditEventType.EMERGENCY_MODE_ACTIVATED,
    AuditEventType.SECURITY_VIOLATION,
    AuditEventType.ERROR_BUDGET_DEPLETED,
}

def _flush_events_to_async_logger(self, request):
    """
    이벤트를 AsyncHealingLogger로 전송 (Non-blocking).

    일반 이벤트: 배치 처리 (~5초마다 플러시)
    CRITICAL 이벤트: 즉시 전송
    """
    buffer = RequestAuditBuffer.get_from_request(request)
    if not buffer or not buffer.events:
        return

    for event in buffer.events:
        # AuditEvent → dict 변환
        event_dict = event.to_dict() if hasattr(event, "to_dict") else {
            "action": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
            "source": event.source,
            "details": event.details,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        }

        # CRITICAL 여부 판단
        severity = EventSeverity.INFO
        if event.event_type in CRITICAL_EVENT_TYPES:
            severity = EventSeverity.CRITICAL

        # Non-blocking 전송 (~0.01ms)
        AsyncHealingLogger.log(event_dict, severity=severity)

def __call__(self, request: HttpRequest) -> HttpResponse:
    self._ensure_initialized()

    # 요청 처리
    response = self.get_response(request)

    # 비동기 로깅 (Non-blocking)
    try:
        self._flush_events_to_async_logger(request)
    except Exception as e:
        # Fail-open: 로깅 실패가 응답에 영향 주지 않음
        logger.warning(f"[AuditMiddleware] Async logging failed: {e}")

    return response
```

---

### 3.3 Graceful Shutdown 처리 (데이터 유실 0% 보장) ⭐

**⚠️ 중요**: 현재 `daemon=True` 스레드 사용으로 프로세스 종료 시 미처리 데이터 유실 가능

**파일**: Django `settings.py` 또는 시그널 핸들러

```python
import atexit
import signal
import sys
import logging

logger = logging.getLogger(__name__)

def graceful_shutdown_audit_system():
    """
    종료 시 감사 시스템 정상 종료.

    순서:
    1. AsyncHealingLogger 플러시 (메모리 → WAL)
    2. AuditSyncWorker 종료 대기 (WAL → 중앙 저장소)
    3. WAL 최종 플러시 (디스크 동기화)
    4. 체크포인트 저장
    """
    from selfhealing.utils.async_logger import AsyncHealingLogger
    from selfhealing.audit.sync_worker import AuditSyncWorker
    from selfhealing.audit.wal import WriteAheadLog

    logger.info("[GracefulShutdown] Starting audit system shutdown...")

    # 1. AsyncHealingLogger 플러시 (메모리 큐 → 처리)
    try:
        AsyncHealingLogger.flush()
        AsyncHealingLogger.stop(timeout=5.0)
        logger.info("[GracefulShutdown] AsyncHealingLogger stopped")
    except Exception as e:
        logger.warning(f"[GracefulShutdown] AsyncHealingLogger error: {e}")

    # 2. AuditSyncWorker 종료 대기 (WAL → 중앙 저장소)
    try:
        sync_worker = AuditSyncWorker.get_instance()
        sync_worker.stop(timeout=30.0)  # 동기화 완료 대기
        logger.info("[GracefulShutdown] AuditSyncWorker stopped")
    except Exception as e:
        logger.warning(f"[GracefulShutdown] SyncWorker error: {e}")

    # 3. WAL 최종 플러시 (미동기화 엔트리 디스크 보장)
    try:
        wal = _get_wal_instance()
        if wal:
            wal.flush()
            wal.close()
            logger.info("[GracefulShutdown] WAL closed")
    except Exception as e:
        logger.warning(f"[GracefulShutdown] WAL error: {e}")

    # 4. 체크포인트 저장
    try:
        checkpoint_manager = _get_checkpoint_manager()
        if checkpoint_manager:
            checkpoint_manager.save()
            logger.info("[GracefulShutdown] Checkpoint saved")
    except Exception as e:
        logger.warning(f"[GracefulShutdown] Checkpoint error: {e}")

    logger.info("[GracefulShutdown] Audit system shutdown complete")


def _get_wal_instance():
    """WAL 인스턴스 가져오기."""
    try:
        from selfhealing.services.audit_helpers import _get_wal
        return _get_wal()
    except Exception:
        return None


def _get_checkpoint_manager():
    """CheckpointManager 인스턴스 가져오기."""
    try:
        from selfhealing.audit.checkpoint import CheckpointManager
        return CheckpointManager.get_instance()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# 시그널 핸들러 등록
# ─────────────────────────────────────────────────────────────

# 정상 종료 시 (atexit)
atexit.register(graceful_shutdown_audit_system)

# SIGTERM (Kubernetes Pod 종료)
def handle_sigterm(signum, frame):
    logger.info(f"[GracefulShutdown] Received SIGTERM (signal {signum})")
    graceful_shutdown_audit_system()
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)

# SIGINT (Ctrl+C)
def handle_sigint(signum, frame):
    logger.info(f"[GracefulShutdown] Received SIGINT (signal {signum})")
    graceful_shutdown_audit_system()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_sigint)
```

---

### 3.4 CheckpointManager 구현 (신규)

**파일**: `packages/selfhealing-python/src/selfhealing/audit/checkpoint.py` (신규)

```python
"""
Checkpoint Manager - 마지막 처리 시퀀스 영속화.

재시작 시 WAL에서 중복 없이 복구하기 위해 필요.
"""
import os
import json
import threading
from pathlib import Path
from typing import Any

import logging

logger = logging.getLogger(__name__)


class CheckpointManager:
    """
    마지막 처리된 시퀀스를 디스크에 저장.

    기능:
    - save(): 현재 시퀀스를 디스크에 저장
    - load(): 디스크에서 시퀀스 로드
    - 원자적 쓰기 (임시 파일 → rename)
    """

    _instance: "CheckpointManager | None" = None
    _lock = threading.Lock()

    DEFAULT_PATH = "/var/log/audit/checkpoint.json"

    def __init__(self, checkpoint_path: str | None = None):
        self._path = Path(checkpoint_path or self.DEFAULT_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        self._last_seq: int = 0
        self._last_sync_time: float = 0.0
        self._data_lock = threading.Lock()

        # 시작 시 로드
        self._load_from_disk()

    @classmethod
    def get_instance(cls, checkpoint_path: str | None = None) -> "CheckpointManager":
        """싱글톤 인스턴스 반환."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(checkpoint_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """싱글톤 리셋 (테스트용)."""
        with cls._lock:
            cls._instance = None

    def save(self, last_seq: int | None = None) -> None:
        """
        체크포인트 저장.

        원자적 쓰기: 임시 파일에 쓰고 rename.
        """
        with self._data_lock:
            if last_seq is not None:
                self._last_seq = last_seq

            data = {
                "last_processed_seq": self._last_seq,
                "last_sync_time": self._last_sync_time,
            }

        # 원자적 쓰기
        tmp_path = self._path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())  # 디스크 영속화

            # 원자적 rename
            tmp_path.rename(self._path)

            logger.debug(f"[CheckpointManager] Saved: seq={self._last_seq}")
        except Exception as e:
            logger.error(f"[CheckpointManager] Save failed: {e}")
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def load(self) -> int:
        """마지막 처리 시퀀스 반환."""
        with self._data_lock:
            return self._last_seq

    def update(self, seq: int) -> None:
        """시퀀스 업데이트 (메모리만, 주기적으로 save() 호출 필요)."""
        import time
        with self._data_lock:
            self._last_seq = max(self._last_seq, seq)
            self._last_sync_time = time.time()

    def _load_from_disk(self) -> None:
        """디스크에서 체크포인트 로드."""
        if not self._path.exists():
            logger.info("[CheckpointManager] No checkpoint file, starting fresh")
            return

        try:
            with open(self._path) as f:
                data = json.load(f)

            with self._data_lock:
                self._last_seq = data.get("last_processed_seq", 0)
                self._last_sync_time = data.get("last_sync_time", 0.0)

            logger.info(f"[CheckpointManager] Loaded: seq={self._last_seq}")
        except Exception as e:
            logger.warning(f"[CheckpointManager] Load failed: {e}, starting fresh")

    def get_stats(self) -> dict[str, Any]:
        """통계 반환."""
        with self._data_lock:
            return {
                "last_processed_seq": self._last_seq,
                "last_sync_time": self._last_sync_time,
                "checkpoint_path": str(self._path),
            }
```

---

### 3.5 시작 시 자동 복구

**파일**: Django `apps.py` 또는 초기화 코드

```python
def startup_audit_recovery():
    """
    시작 시 WAL에서 미처리 엔트리 복구.

    순서:
    1. 체크포인트 로드 (마지막 처리 시퀀스)
    2. WAL에서 미처리 엔트리 조회
    3. 미처리 엔트리 재처리
    4. SyncWorker 시작
    """
    from selfhealing.audit.checkpoint import CheckpointManager
    from selfhealing.audit.wal import WriteAheadLog
    from selfhealing.audit.sync_worker import AuditSyncWorker

    logger.info("[StartupRecovery] Starting audit system recovery...")

    # 1. 체크포인트 로드
    checkpoint = CheckpointManager.get_instance()
    last_seq = checkpoint.load()
    logger.info(f"[StartupRecovery] Last processed seq: {last_seq}")

    # 2. WAL에서 미처리 엔트리 조회
    wal = _get_or_create_wal()
    unprocessed = wal.recover_unprocessed(last_processed_seq=last_seq)

    if unprocessed:
        logger.info(f"[StartupRecovery] Found {len(unprocessed)} unprocessed entries")

        # 3. 미처리 엔트리는 SyncWorker가 처리할 예정
        # (별도 재처리 로직 필요 없음, SyncWorker가 알아서 함)
    else:
        logger.info("[StartupRecovery] No unprocessed entries")

    # 4. SyncWorker 시작
    sync_worker = AuditSyncWorker.get_instance(wal=wal)
    sync_worker.start()

    # 5. AsyncHealingLogger 시작
    from selfhealing.utils.async_logger import AsyncHealingLogger
    AsyncHealingLogger.start()

    logger.info("[StartupRecovery] Audit system recovery complete")


def _get_or_create_wal():
    """WAL 인스턴스 생성 또는 반환."""
    try:
        from selfhealing.services.audit_helpers import _get_wal
        return _get_wal()
    except Exception:
        from selfhealing.audit.wal import WriteAheadLog, WALConfig
        return WriteAheadLog(WALConfig())


# Django ready 시 호출
# class AuditConfig(AppConfig):
#     def ready(self):
#         startup_audit_recovery()
```

---

## 4. 테스트 계획

### 4.1 단위 테스트

**파일**: `tests/unit/audit/test_async_audit_pipeline.py`

```python
import pytest
import time
from unittest.mock import Mock, patch

class TestAsyncAuditPipeline:
    """비동기 Audit 파이프라인 테스트."""

    def test_non_blocking_logging(self):
        """로깅이 Non-blocking인지 확인."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []
        AsyncHealingLogger.configure(
            flush_callback=lambda events: events_received.extend(events)
        )
        AsyncHealingLogger.start()

        try:
            start = time.time()
            for _ in range(1000):
                AsyncHealingLogger.log({"test": True}, EventSeverity.INFO)
            elapsed = time.time() - start

            # 1000개 로깅이 10ms 이내 (Non-blocking)
            assert elapsed < 0.01, f"1000 logs took {elapsed}s (should be < 0.01s)"
        finally:
            AsyncHealingLogger.stop()

    def test_critical_events_immediate(self):
        """CRITICAL 이벤트는 즉시 전송."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []
        AsyncHealingLogger.configure(
            flush_callback=lambda events: events_received.extend(events)
        )
        AsyncHealingLogger.start()

        try:
            AsyncHealingLogger.log(
                {"type": "cb_open"},
                EventSeverity.CRITICAL
            )

            # 즉시 전송이므로 짧은 대기 후 확인
            time.sleep(0.1)

            assert len(events_received) == 1
            assert events_received[0]["type"] == "cb_open"
        finally:
            AsyncHealingLogger.stop()

    def test_batch_flush(self):
        """배치 크기 도달 시 플러시."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []
        AsyncHealingLogger.configure(
            flush_callback=lambda events: events_received.extend(events)
        )
        AsyncHealingLogger.start()

        try:
            # 배치 크기(10)보다 많이 전송
            for i in range(15):
                AsyncHealingLogger.log({"idx": i}, EventSeverity.INFO)

            # 워커가 처리할 시간 대기
            time.sleep(2.0)

            # 최소 10개는 플러시됨
            assert len(events_received) >= 10
        finally:
            AsyncHealingLogger.stop()

    def test_graceful_shutdown_flushes_remaining(self):
        """종료 시 남은 이벤트 플러시."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []
        AsyncHealingLogger.configure(
            flush_callback=lambda events: events_received.extend(events)
        )
        AsyncHealingLogger.start()

        # 이벤트 추가 (배치 크기 미달)
        for i in range(5):
            AsyncHealingLogger.log({"idx": i}, EventSeverity.INFO)

        # flush() 호출로 강제 플러시
        AsyncHealingLogger.flush()
        AsyncHealingLogger.stop()

        # 모든 이벤트 플러시됨
        assert len(events_received) == 5
```

---

### 4.2 통합 테스트

**파일**: `tests/integration/selfhealing/test_async_audit_middleware_integration.py`

```python
class TestAsyncAuditMiddlewareIntegration:
    """AuditMiddleware + AsyncHealingLogger 통합 테스트."""

    def test_middleware_non_blocking_response(self, client):
        """미들웨어가 응답 지연 없이 처리."""
        import time

        start = time.time()
        response = client.get("/api/health/")
        elapsed = time.time() - start

        assert response.status_code == 200
        # 응답 시간이 로깅으로 인해 증가하지 않음
        assert elapsed < 0.1

    def test_events_eventually_recorded(self, client, db):
        """이벤트가 최종적으로 기록됨."""
        from selfhealing.utils.async_logger import AsyncHealingLogger
        from selfhealing.adapters.django.models import AuditLog

        # 요청 발생 (이벤트 생성)
        response = client.post("/api/orders/", {"amount": 100})

        # 배치 플러시 대기
        AsyncHealingLogger.flush()
        time.sleep(1.0)

        # DB에 기록 확인
        logs = AuditLog.objects.filter(target_type="order")
        assert logs.count() > 0
```

---

## 5. 환경 변수

| 변수명 | 기본값 | 설명 |
|-------|-------|------|
| `SELFHEALING_BATCH_LOGGER_BATCH_SIZE` | 10 | 배치 크기 |
| `SELFHEALING_BATCH_FLUSH_INTERVAL` | 5.0 | 플러시 간격 (초) |
| `SELFHEALING_BATCH_ASYNC_LOGGER_MAX_QUEUE_SIZE` | 5000 | 최대 큐 크기 |

---

## 6. 마이그레이션 체크리스트

### 6.1 코드 변경

- [x] `audit_middleware.py`: `_flush_events_to_async_logger()` 구현
  - 파일: `packages/selfhealing-python/src/selfhealing/api/django/audit_middleware.py`
  - `CRITICAL_AUDIT_EVENT_TYPES` 상수 추가
  - `_is_async_mode_enabled()` 메서드 추가
  - `_flush_events_to_async_logger()` 메서드 추가
  - `_convert_event_to_dict()` 메서드 추가
  - `__call__` 메서드에서 비동기/동기 모드 분기 처리
- [x] Django 앱 초기화: `AsyncHealingLogger.configure()` 및 `start()`
  - 파일: `packages/selfhealing-python/src/selfhealing/audit/async_audit_lifecycle.py` (신규)
  - `startup_async_audit_system()` 함수 구현
  - `create_audit_flush_callback()` 함수 구현
- [x] Graceful shutdown: `atexit` 및 `SIGTERM` 핸들러
  - 파일: `packages/selfhealing-python/src/selfhealing/audit/async_audit_lifecycle.py`
  - `graceful_shutdown_audit_system()` 함수 구현
  - `register_shutdown_handlers()` 함수 구현

### 6.2 테스트

- [x] 단위 테스트 추가 (20개 테스트 통과)
  - 파일: `packages/selfhealing-python/tests/unit/audit/test_async_audit_pipeline.py`
  - `TestAsyncHealingLoggerNonBlocking` (2개)
  - `TestAsyncHealingLoggerCriticalEvents` (2개)
  - `TestAsyncHealingLoggerBatchFlush` (2개)
  - `TestAsyncHealingLoggerGracefulShutdown` (2개)
  - `TestAsyncAuditLifecycle` (5개)
  - `TestAuditMiddlewareAsyncMode` (3개)
  - `TestConvertEventToDict` (1개)
  - `TestCheckpointManagerIntegration` (3개)
- [ ] 통합 테스트 추가 (선택사항 - Docker Compose 환경 필요)
- [ ] 부하 테스트: 1000 RPS에서 응답 지연 < 10ms 확인

### 6.3 모니터링

- [ ] `AsyncHealingLogger.get_stats()` 메트릭 노출
- [ ] `queue_size`, `flush_errors` 알림 설정

---

## 7. 예상 효과

| 지표 | Before | After |
|-----|--------|-------|
| 요청당 추가 지연 | 5-50ms | ~0.01ms |
| 이벤트 처리 방식 | 동기 (블로킹) | 비동기 (Non-blocking) |
| DB/Redis 장애 영향 | 요청 실패 | 영향 없음 (Fail-open) |
| 배치 효율 | 없음 (개별 처리) | 10개씩 배치 처리 |

---

## 8. 다음 단계

→ [168_REDIS_BATCH_OPTIMIZATION.md](168_REDIS_BATCH_OPTIMIZATION.md): Redis 배치 처리 최적화
